#!/usr/bin/env python3
"""
S11 Trade Desk — live monitoring + manual position tracking.

Two modes:
  - Display (default): auto-refreshes every 5s showing active S11 signals,
    open positions with live P&L, and exit alerts.
  - Input (press Enter): log opens/closes, view summary.

Run inside a container with DB + Redis access:
  docker exec -it backend-worker-1 python3 /scripts/s11_trade_desk.py
  # or
  cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -it worker python3 /scripts/s11_trade_desk.py

Output: Backend/scripts/output/s11_trade_log.json
"""

import json
import math
import os
import select
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import redis

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Detect Docker vs host
if os.path.isdir("/app") or os.path.isdir("/backend"):
    _SCRIPT_DIR = Path("/scripts") if os.path.isdir("/scripts") else Path("/backend/scripts")
else:
    _SCRIPT_DIR = Path(__file__).resolve().parent

_OUTPUT_DIR = _SCRIPT_DIR / "output"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_TRADE_LOG = _OUTPUT_DIR / "s11_trade_log.json"

# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
_DB_URL = os.getenv("DATABASE_URL", "")
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

DEFAULT_SIZE_USD = 1000.0

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------
RESET   = "\033[0m"
BOLD    = "\033[1m"
RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
WHITE   = "\033[97m"
DIM     = "\033[2m"
BLINK   = "\033[5m"
BG_RED  = "\033[41m"


def _color_pnl(pnl_pct: float) -> str:
    if pnl_pct > 0:
        return f"{GREEN}{pnl_pct:+.2f}%{RESET}"
    elif pnl_pct < 0:
        return f"{RED}{pnl_pct:+.2f}%{RESET}"
    return f"{WHITE}{pnl_pct:+.2f}%{RESET}"


# ---------------------------------------------------------------------------
# Trade log (JSON on disk)
# ---------------------------------------------------------------------------
def _load_log() -> dict:
    if _TRADE_LOG.exists():
        try:
            return json.loads(_TRADE_LOG.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"open": [], "closed": []}


def _save_log(log: dict):
    _TRADE_LOG.write_text(json.dumps(log, indent=2))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _fetch_active_s11(conn) -> list[dict]:
    """Fetch active S11 opps ordered by raw_score (depth) desc."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, symbol, created_ts, score, payload_json
            FROM opportunities
            WHERE setup_id = 'S11'
              AND status = 'active'
              AND batch_id IS NULL
            ORDER BY score DESC
        """)
        rows = cur.fetchall()

    opps = []
    for row in rows:
        opp_id, symbol, created_ts, score, payload_json = row
        try:
            pj = json.loads(payload_json or "{}")
        except (json.JSONDecodeError, TypeError):
            pj = {}

        raw_score = float(pj.get("raw_score", score or 0))
        entry_price = float(pj.get("entry_price", 0))
        stop_price = float(pj.get("stop_price", 0))
        time_stop_bars = int(pj.get("time_stop_bars", 24))

        # Bars elapsed since signal
        bars_elapsed = 0
        if created_ts:
            if hasattr(created_ts, 'timestamp'):
                sig_epoch = created_ts.timestamp()
            else:
                sig_epoch = float(created_ts)
            bars_elapsed = int((time.time() - sig_epoch) / 3600)

        opps.append({
            "id": opp_id,
            "symbol": symbol,
            "created_ts": created_ts,
            "score": score,
            "raw_score": raw_score,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "time_stop_bars": time_stop_bars,
            "bars_elapsed": bars_elapsed,
            "bars_remaining": max(0, time_stop_bars - bars_elapsed),
        })

    # Re-sort by raw_score descending (depth)
    opps.sort(key=lambda o: o["raw_score"], reverse=True)
    return opps


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------
def _get_price(r: redis.Redis, symbol: str) -> float | None:
    """Get latest price from Redis price:{symbol} hash."""
    try:
        raw = r.hget(f"price:{symbol}", "price")
        if raw:
            return float(raw)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _render(conn, r: redis.Redis, log: dict):
    """Render the full trade desk display."""
    os.system("clear")
    now = time.time()
    now_utc = datetime.fromtimestamp(now, tz=timezone.utc)

    print(f"{BOLD}{'=' * 72}{RESET}")
    print(f"{BOLD}  S11 MACD Long Reversion — Trade Desk{RESET}")
    print(f"{BOLD}  {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}{RESET}")
    print(f"{BOLD}{'=' * 72}{RESET}")

    # --- Active S11 signals ---
    opps = _fetch_active_s11(conn)
    active_symbols = {o["symbol"] for o in opps}

    print(f"\n{BOLD}  ACTIVE S11 SIGNALS ({len(opps)}/15){RESET}")
    print(f"  {'Symbol':<14} {'Entry':>10} {'Stop':>10} {'Depth':>7} {'Bars':>5} {'Remaining':>10}")
    print(f"  {'-' * 62}")

    if not opps:
        print(f"  {DIM}(none){RESET}")
    else:
        for o in opps:
            bars_color = RESET
            if o["bars_remaining"] <= 0:
                bars_color = RED
            elif o["bars_remaining"] <= 4:
                bars_color = YELLOW

            print(
                f"  {o['symbol']:<14} "
                f"{o['entry_price']:>10.4f} "
                f"{o['stop_price']:>10.4f} "
                f"{o['raw_score']:>7.2f} "
                f"{o['bars_elapsed']:>5} "
                f"{bars_color}{o['bars_remaining']:>10}{RESET}"
            )

    # --- Open positions ---
    open_positions = log.get("open", [])
    print(f"\n{BOLD}  MY OPEN POSITIONS ({len(open_positions)}){RESET}")
    print(f"  {'Symbol':<14} {'MyEntry':>10} {'Current':>10} {'P&L':>10} {'Bars':>5} {'Status':>14}")
    print(f"  {'-' * 69}")

    if not open_positions:
        print(f"  {DIM}(none — press Enter then 'o SYMBOL PRICE' to open){RESET}")
    else:
        for pos in open_positions:
            symbol = pos["symbol"]
            entry_px = pos["entry_price"]
            current_px = _get_price(r, symbol)

            # P&L
            if current_px and entry_px > 0:
                pnl_pct = ((current_px - entry_px) / entry_px) * 100
                pnl_str = _color_pnl(pnl_pct)
                current_str = f"{current_px:>10.4f}"
            else:
                pnl_str = f"{DIM}  N/A{RESET}"
                current_str = f"{DIM}       N/A{RESET}"

            # Bars held
            entry_ts = pos.get("entry_ts", "")
            if entry_ts:
                try:
                    entry_epoch = datetime.fromisoformat(entry_ts).timestamp()
                    bars_held = int((now - entry_epoch) / 3600)
                except (ValueError, OSError):
                    bars_held = 0
            else:
                bars_held = 0

            # Status / alerts
            alerts = []
            edge_gone = symbol not in active_symbols
            if edge_gone:
                alerts.append("EDGE GONE")
            if bars_held >= 24:
                alerts.append("TIME STOP")
            elif bars_held >= 20:
                alerts.append("APPROACHING")

            if "TIME STOP" in alerts or "EDGE GONE" in alerts:
                status_str = f"{BLINK}{BG_RED}{WHITE} CLOSE AT MARKET {RESET}"
            elif "APPROACHING" in alerts:
                status_str = f"{YELLOW}~{24 - bars_held}h left{RESET}"
            else:
                status_str = f"{GREEN}OK{RESET}"

            print(
                f"  {symbol:<14} "
                f"{entry_px:>10.4f} "
                f"{current_str} "
                f"{pnl_str:>20} "
                f"{bars_held:>5} "
                f"{status_str:>24}"
            )

    # --- Closed trades summary ---
    closed = log.get("closed", [])
    if closed:
        wins = [t for t in closed if t.get("pnl_pct", 0) > 0]
        losses = [t for t in closed if t.get("pnl_pct", 0) <= 0]
        total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
        wr = (len(wins) / len(closed) * 100) if closed else 0

        gross_profit = sum(t.get("pnl_usd", 0) for t in wins) if wins else 0
        gross_loss = abs(sum(t.get("pnl_usd", 0) for t in losses)) if losses else 0
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        pnl_color = GREEN if total_pnl > 0 else RED if total_pnl < 0 else WHITE
        print(f"\n{BOLD}  CLOSED TRADES{RESET}  "
              f"{len(closed)} trades | "
              f"WR {wr:.0f}% | "
              f"PF {pf:.2f} | "
              f"P&L {pnl_color}${total_pnl:+.2f}{RESET}")

    # --- Footer ---
    # Next 1h bar close
    next_bar = math.ceil(now / 3600) * 3600
    countdown = int(next_bar - now)
    mm, ss = divmod(countdown, 60)

    # Last signal
    if opps:
        last = max(opps, key=lambda o: str(o.get("created_ts", "")))
        last_str = f"{last['symbol']} depth={last['raw_score']:.2f} @ {last['created_ts']}"
    else:
        last_str = "—"

    print(f"\n{DIM}  Next 1h close: {mm:02d}:{ss:02d} | "
          f"Last signal: {last_str} | "
          f"Open: {len(open_positions)} | Closed: {len(closed)}{RESET}")

    print(f"\n  {DIM}Press Enter for input mode (o/c/s/q){RESET}")


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------
def _handle_input(log: dict) -> bool:
    """Handle user input. Returns False if user wants to quit."""
    line = input(f"\n{BOLD}> {RESET}").strip()

    if not line:
        return True  # empty = back to display

    parts = line.split()
    cmd = parts[0].lower()

    if cmd == "q":
        return False

    elif cmd == "o" and len(parts) >= 3:
        symbol = parts[1].upper()
        try:
            entry_price = float(parts[2])
        except ValueError:
            print(f"{RED}  Invalid price: {parts[2]}{RESET}")
            time.sleep(1)
            return True

        size_usd = float(parts[3]) if len(parts) >= 4 else DEFAULT_SIZE_USD
        now_iso = datetime.now(timezone.utc).isoformat()

        log["open"].append({
            "symbol": symbol,
            "entry_price": entry_price,
            "entry_ts": now_iso,
            "size_usd": size_usd,
        })
        _save_log(log)
        print(f"{GREEN}  Opened {symbol} @ {entry_price} (${size_usd}){RESET}")
        time.sleep(1)

    elif cmd == "c" and len(parts) >= 3:
        symbol = parts[1].upper()
        try:
            exit_price = float(parts[2])
        except ValueError:
            print(f"{RED}  Invalid price: {parts[2]}{RESET}")
            time.sleep(1)
            return True

        # Find the open position
        found = None
        for i, pos in enumerate(log["open"]):
            if pos["symbol"] == symbol:
                found = i
                break

        if found is None:
            print(f"{RED}  No open position for {symbol}{RESET}")
            time.sleep(1)
            return True

        pos = log["open"].pop(found)
        entry_price = pos["entry_price"]
        size_usd = pos.get("size_usd", DEFAULT_SIZE_USD)
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        pnl_usd = size_usd * (pnl_pct / 100)

        log["closed"].append({
            "symbol": symbol,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_ts": pos.get("entry_ts", ""),
            "exit_ts": datetime.now(timezone.utc).isoformat(),
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(pnl_usd, 2),
            "size_usd": size_usd,
        })
        _save_log(log)

        color = GREEN if pnl_pct > 0 else RED
        print(f"{color}  Closed {symbol}: {entry_price} → {exit_price} "
              f"({pnl_pct:+.2f}%, ${pnl_usd:+.2f}){RESET}")
        time.sleep(1.5)

    elif cmd == "s":
        closed = log.get("closed", [])
        if not closed:
            print(f"  {DIM}No closed trades yet.{RESET}")
            time.sleep(1)
            return True

        print(f"\n{BOLD}  TRADE HISTORY{RESET}")
        print(f"  {'Symbol':<12} {'Entry':>10} {'Exit':>10} {'P&L%':>8} {'P&L$':>8} {'Date'}")
        print(f"  {'-' * 65}")
        for t in closed:
            color = GREEN if t.get("pnl_pct", 0) > 0 else RED
            exit_dt = t.get("exit_ts", "")[:16] if t.get("exit_ts") else "—"
            print(
                f"  {t['symbol']:<12} "
                f"{t['entry_price']:>10.4f} "
                f"{t['exit_price']:>10.4f} "
                f"{color}{t.get('pnl_pct', 0):>+7.2f}%{RESET} "
                f"{color}${t.get('pnl_usd', 0):>+7.2f}{RESET} "
                f"{exit_dt}"
            )

        wins = [t for t in closed if t.get("pnl_pct", 0) > 0]
        losses = [t for t in closed if t.get("pnl_pct", 0) <= 0]
        total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
        wr = (len(wins) / len(closed) * 100) if closed else 0
        gp = sum(t.get("pnl_usd", 0) for t in wins) if wins else 0
        gl = abs(sum(t.get("pnl_usd", 0) for t in losses)) if losses else 0
        pf = (gp / gl) if gl > 0 else float("inf")

        pnl_color = GREEN if total_pnl > 0 else RED
        print(f"\n  {BOLD}Totals:{RESET} {len(closed)} trades | "
              f"WR {wr:.0f}% ({len(wins)}W/{len(losses)}L) | "
              f"PF {pf:.2f} | "
              f"P&L {pnl_color}${total_pnl:+.2f}{RESET}")

        input(f"\n  {DIM}Press Enter to continue...{RESET}")

    else:
        print(f"  {DIM}Commands: o SYMBOL PRICE [SIZE] | c SYMBOL PRICE | s | q{RESET}")
        time.sleep(1)

    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    print(f"S11 Trade Desk starting...")
    print(f"  DB: {_DB_URL[:40]}..." if _DB_URL else "  DB: (not set)")
    print(f"  Redis: {_REDIS_URL}")
    print(f"  Trade log: {_TRADE_LOG}")

    if not _DB_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg.connect(_DB_URL, autocommit=True)
    r = redis.Redis.from_url(_REDIS_URL, decode_responses=True)

    log = _load_log()
    print(f"  Loaded {len(log.get('open', []))} open, {len(log.get('closed', []))} closed positions")
    time.sleep(1)

    # Check if stdin is a TTY (interactive mode available)
    interactive = sys.stdin.isatty()
    if not interactive:
        print(f"  {DIM}Non-interactive mode — display only (no input){RESET}")

    try:
        while True:
            log = _load_log()  # re-read in case edited externally
            _render(conn, r, log)

            if interactive:
                # Multiplex: wait up to 5s for stdin, else auto-refresh
                rlist, _, _ = select.select([sys.stdin], [], [], 5.0)
                if rlist:
                    # stdin has data — enter input mode
                    # Consume the Enter that triggered select
                    sys.stdin.readline()
                    if not _handle_input(log):
                        break
            else:
                time.sleep(5)

    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
        print(f"\n{DIM}Trade desk stopped. Log: {_TRADE_LOG}{RESET}")


if __name__ == "__main__":
    main()
