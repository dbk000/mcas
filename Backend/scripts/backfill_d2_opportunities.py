#!/usr/bin/env python3
"""
D2 Expired Opportunity Backfill
================================

Reads historical WhaleBotPumps cluster data from social_bot_signals,
detects pump clusters, computes entry/stop/TP levels using winning
D2 config (HWM trailing drop, entry=25%, stop=15%), and writes
expired opportunities to the opportunities table.

Uses same cluster detection + state machine as d2_honest_backtest.py
and d2_paper_trader.py for consistency.

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/backfill_d2_opportunities.py [--dry-run] [--batch-id BATCH]

Output: scripts/output/d2_backfill_{timestamp}.txt
"""

import bisect
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading"
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
if os.path.isdir("/scripts/output"):
    OUTPUT_DIR = "/scripts/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
OUTPUT_TXT = os.path.join(OUTPUT_DIR, f"d2_backfill_{TIMESTAMP}.txt")
logger.info("Output: %s", OUTPUT_TXT)

# ---------------------------------------------------------------------------
# D2 Winning Config (from d2_paper_trader.py / stress test)
# ---------------------------------------------------------------------------
CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3
ENTRY_THRESHOLD = 0.25        # 25% drop from HWM
STOP_PCT = 0.15               # 15% above HWM at entry
TIME_STOP_HOURS = 72
MAX_WATCH_HOURS = 48
MANIA_GATE_PCT = 40.0         # Skip clusters >40% pump (mania filter)
LIQUIDITY_GATE_USD = 1_000_000  # Min 24h volume
SETUP_VERSION = "D2v1.0"

# Trading costs
TAKER_FEE_PCT = 0.0004
SLIPPAGE_PCT = 0.0010
ROUND_TRIP_FRICTION = 2 * (TAKER_FEE_PCT + SLIPPAGE_PCT)


# ---------------------------------------------------------------------------
# Load signals
# ---------------------------------------------------------------------------
def load_pump_signals(conn):
    """Load all WhaleBotPumps signals from social_bot_signals."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, source_post_id, source, channel, symbol, exchange,
                   signal_type, price, pct_change, vol_24h, last_vol,
                   vol_change_pct, oi, oi_change_pct, funding_rate,
                   bid_vol, ask_vol, spread_pct, post_ts
            FROM social_bot_signals
            ORDER BY post_ts
        """)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Cluster detection (same as d2_honest_backtest.py)
# ---------------------------------------------------------------------------
def detect_pump_clusters(signals):
    """Group pump signals into clusters (>=3 alerts within 4h window)."""
    pumps = [s for s in signals if s.get("signal_type") == "pump"]
    by_symbol = defaultdict(list)
    for s in pumps:
        by_symbol[s["symbol"]].append(s)

    clusters = []
    for symbol, sigs in by_symbol.items():
        sigs.sort(key=lambda x: x["post_ts"])
        current = [sigs[0]]
        for s in sigs[1:]:
            gap_h = (s["post_ts"] - current[-1]["post_ts"]).total_seconds() / 3600
            if gap_h <= CLUSTER_WINDOW_HOURS:
                current.append(s)
            else:
                if len(current) >= MIN_ALERTS_PER_CLUSTER:
                    clusters.append(_build_cluster(symbol, current))
                current = [s]
        if len(current) >= MIN_ALERTS_PER_CLUSTER:
            clusters.append(_build_cluster(symbol, current))

    clusters.sort(key=lambda c: c["cluster_start_ts"])
    return clusters


def _build_cluster(symbol, signals):
    """Build cluster dict from grouped signals."""
    exchanges = set(s.get("exchange") for s in signals if s.get("exchange"))
    pct_changes = [float(s["pct_change"]) for s in signals if s.get("pct_change") is not None]
    funding_rates = [float(s["funding_rate"]) for s in signals if s.get("funding_rate") is not None]
    vol_24h_values = [float(s["vol_24h"]) for s in signals if s.get("vol_24h") is not None]

    return {
        "symbol": symbol,
        "cluster_start_ts": signals[0]["post_ts"],
        "cluster_end_ts": signals[-1]["post_ts"],
        "alert_count": len(signals),
        "exchanges_seen": sorted(exchanges),
        "exchange_count": len(exchanges),
        "max_pct_change": max(pct_changes) if pct_changes else None,
        "funding_rates_reported": funding_rates,
        "max_vol_24h": max(vol_24h_values) if vol_24h_values else None,
    }


# ---------------------------------------------------------------------------
# OHLCV loading
# ---------------------------------------------------------------------------
def load_ohlcv_for_clusters(conn, clusters):
    """Load 1h OHLCV for all cluster symbols. Returns dict: symbol -> [(ts, o, h, l, c)]."""
    logger.info("Loading 1h OHLCV for %d clusters...", len(clusters))
    symbols = sorted(set(c["symbol"] for c in clusters))
    earliest = min(c["cluster_start_ts"] for c in clusters) - timedelta(hours=2)
    latest = max(c["cluster_start_ts"] for c in clusters) + timedelta(hours=130)

    ohlcv = {}
    loaded = 0
    with conn.cursor() as cur:
        for sym in symbols:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close
                FROM ohlcv
                WHERE symbol = %s AND timeframe = '1h'
                  AND bar_start_ts >= %s AND bar_start_ts <= %s
                ORDER BY bar_start_ts
            """, (sym, earliest, latest))
            rows = cur.fetchall()
            if rows:
                ohlcv[sym] = rows
                loaded += 1

    logger.info("  OHLCV loaded for %d/%d symbols", loaded, len(symbols))
    return ohlcv


def get_pre_pump_price(bars, bar_times, cluster_start_ts):
    """Get close of bar immediately before cluster start."""
    pre_idx = bisect.bisect_left(bar_times, cluster_start_ts) - 1
    if pre_idx < 0:
        return None
    return float(bars[pre_idx][4])


# ---------------------------------------------------------------------------
# HWM Trailing Drop state machine (winning entry mechanism)
# ---------------------------------------------------------------------------
def simulate_hwm_trailing(cluster, ohlcv_bars, bar_times):
    """
    Walk-forward simulation with HWM trailing drop entry.
    Returns opportunity dict or None if no valid entry.
    """
    pre_pump_price = get_pre_pump_price(ohlcv_bars, bar_times, cluster["cluster_start_ts"])
    if pre_pump_price is None:
        return None

    cluster_start = cluster["cluster_start_ts"]
    max_watch_ts = cluster_start + timedelta(hours=MAX_WATCH_HOURS)

    start_idx = bisect.bisect_left(bar_times, cluster_start)
    if start_idx >= len(ohlcv_bars):
        return None

    live_hwm = 0.0
    hwm_ts = None
    state = "WATCHING"

    for i in range(start_idx, len(ohlcv_bars)):
        bar_ts = bar_times[i]
        bar_high = float(ohlcv_bars[i][2])
        bar_low = float(ohlcv_bars[i][3])
        bar_close = float(ohlcv_bars[i][4])

        if state == "WATCHING":
            if bar_high > live_hwm:
                live_hwm = bar_high
                hwm_ts = bar_ts

            if bar_ts >= max_watch_ts:
                return None

            if live_hwm > 0:
                drop_from_hwm = (live_hwm - bar_close) / live_hwm
                if drop_from_hwm >= ENTRY_THRESHOLD:
                    entry_price = bar_close
                    entry_ts = bar_ts
                    entry_hwm = live_hwm
                    stop_price = live_hwm * (1 + STOP_PCT)
                    tp_price = pre_pump_price

                    if entry_price <= tp_price:
                        return None

                    # Now simulate the trade to get exit
                    exit_result = _simulate_exit(
                        ohlcv_bars, bar_times, i + 1,
                        entry_price, entry_ts, stop_price, tp_price
                    )

                    return {
                        "cluster": cluster,
                        "pre_pump_price": pre_pump_price,
                        "entry_price": entry_price,
                        "entry_ts": entry_ts,
                        "entry_hwm": entry_hwm,
                        "stop_price": stop_price,
                        "tp_price": tp_price,
                        "hwm_ts": hwm_ts,
                        "drop_from_hwm_pct": round(drop_from_hwm * 100, 2),
                        **exit_result,
                    }

    return None


def _simulate_exit(ohlcv_bars, bar_times, start_idx, entry_price, entry_ts,
                   stop_price, tp_price):
    """Simulate exit from entry point. Returns exit dict."""
    time_stop_ts = entry_ts + timedelta(hours=TIME_STOP_HOURS)

    for i in range(start_idx, len(ohlcv_bars)):
        bar_ts = bar_times[i]
        bar_high = float(ohlcv_bars[i][2])
        bar_low = float(ohlcv_bars[i][3])
        bar_close = float(ohlcv_bars[i][4])

        # Stop loss (short position — stop is above entry)
        if bar_high >= stop_price:
            return _exit_dict("STOP_LOSS", stop_price, bar_ts, entry_price, entry_ts)

        # Take profit
        if bar_low <= tp_price:
            return _exit_dict("TP_HIT", tp_price, bar_ts, entry_price, entry_ts)

        # Time stop
        if bar_ts >= time_stop_ts:
            return _exit_dict("TIME_STOP", bar_close, bar_ts, entry_price, entry_ts)

    # Ran out of bars
    if len(ohlcv_bars) > start_idx:
        last_close = float(ohlcv_bars[-1][4])
        last_ts = bar_times[-1]
        return _exit_dict("TIME_STOP", last_close, last_ts, entry_price, entry_ts)

    return _exit_dict("NO_DATA", entry_price, entry_ts, entry_price, entry_ts)


def _exit_dict(exit_reason, exit_price, exit_ts, entry_price, entry_ts):
    """Build exit result dict."""
    risk = abs(exit_price - entry_price) if exit_reason == "STOP_LOSS" else abs(entry_price * STOP_PCT)
    if risk <= 0:
        risk = 0.001

    # Short PnL: entry - exit
    pnl_pct = (entry_price - exit_price) / entry_price
    r_multiple = (entry_price - exit_price) / (entry_price * STOP_PCT) if entry_price > 0 else 0
    hours_held = (exit_ts - entry_ts).total_seconds() / 3600 if exit_ts and entry_ts else 0

    return {
        "exit_reason": exit_reason,
        "exit_price": exit_price,
        "exit_ts": exit_ts,
        "pnl_pct": round(pnl_pct * 100, 4),
        "r_multiple": round(r_multiple, 4),
        "hours_held": round(hours_held, 1),
        "pnl_after_costs_pct": round((pnl_pct - ROUND_TRIP_FRICTION) * 100, 4),
    }


# ---------------------------------------------------------------------------
# Binance perps check
# ---------------------------------------------------------------------------
def get_binance_perp_symbols(conn):
    """Get perp symbols from ohlcv table (symbols with data = tradeable)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT symbol FROM ohlcv
            WHERE timeframe = '1h'
              AND bar_start_ts > NOW() - INTERVAL '30 days'
        """)
        return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Gate checks
# ---------------------------------------------------------------------------
def apply_gates(cluster, available_symbols):
    """Apply D2 gates. Returns (passed, reason)."""
    sym = cluster["symbol"]

    # Must have OHLCV data
    if sym not in available_symbols:
        return False, "no_ohlcv"

    # Mania gate: skip extreme pumps
    max_pct = cluster.get("max_pct_change") or 0
    if max_pct > MANIA_GATE_PCT:
        return False, f"mania_gate ({max_pct:.1f}% > {MANIA_GATE_PCT}%)"

    # Liquidity gate
    vol = cluster.get("max_vol_24h") or 0
    if vol < LIQUIDITY_GATE_USD:
        return False, f"liquidity_gate (${vol:,.0f} < ${LIQUIDITY_GATE_USD:,.0f})"

    return True, "passed"


# ---------------------------------------------------------------------------
# Write opportunities
# ---------------------------------------------------------------------------
def write_opportunities(conn, opps, batch_id, dry_run=False):
    """Write opportunity records to DB in batches."""
    if dry_run:
        logger.info("DRY RUN: would write %d opportunities", len(opps))
        return 0

    written = 0
    batch_size = 500
    with conn.cursor() as cur:
        for i in range(0, len(opps), batch_size):
            batch = opps[i:i + batch_size]
            values = []
            for opp in batch:
                cluster = opp["cluster"]
                payload = {
                    "setup_id": "D2",
                    "symbol": cluster["symbol"],
                    "timeframe": "1h",
                    "direction": "short",
                    "entry_price": opp["entry_price"],
                    "stop_price": opp["stop_price"],
                    "tp_price": opp["tp_price"],
                    "pre_pump_price": opp["pre_pump_price"],
                    "entry_hwm": opp["entry_hwm"],
                    "drop_from_hwm_pct": opp["drop_from_hwm_pct"],
                    "cluster_start_ts": cluster["cluster_start_ts"].isoformat(),
                    "cluster_end_ts": cluster["cluster_end_ts"].isoformat(),
                    "alert_count": cluster["alert_count"],
                    "max_pct_change": cluster.get("max_pct_change"),
                    "exchanges_seen": cluster.get("exchanges_seen", []),
                    "entry_threshold_pct": ENTRY_THRESHOLD * 100,
                    "stop_pct": STOP_PCT * 100,
                    "time_stop_hours": TIME_STOP_HOURS,
                    "max_watch_hours": MAX_WATCH_HOURS,
                    "entry_ts": opp["entry_ts"].isoformat(),
                    "exit_ts": opp["exit_ts"].isoformat() if opp.get("exit_ts") else None,
                    "exit_price": opp["exit_price"],
                    "exit_reason": opp["exit_reason"],
                    "pnl_pct": opp["pnl_pct"],
                    "pnl_after_costs_pct": opp["pnl_after_costs_pct"],
                    "r_multiple": opp["r_multiple"],
                    "hours_held": opp["hours_held"],
                    "exit_settled_ts": datetime.now(timezone.utc).isoformat(),
                    "exit_settler": "backfill_d2_opportunities_v1",
                }

                values.append((
                    opp["entry_ts"],                 # created_ts (use entry time)
                    cluster["symbol"],               # symbol
                    "1h",                             # timeframe
                    "D2",                             # setup_id
                    "short",                          # decision
                    opp.get("cluster", {}).get("alert_count", 3) / 10.0,  # score (normalized)
                    f"D2 cluster: {cluster['alert_count']} alerts, "
                    f"{cluster.get('max_pct_change', 0):.1f}% pump",  # reason
                    json.dumps(payload, default=str),  # payload_json
                    "expired",                        # status
                    SETUP_VERSION,                    # setup_version
                    batch_id,                         # batch_id
                ))

            cur.executemany("""
                INSERT INTO opportunities
                    (created_ts, symbol, timeframe, setup_id, decision, score,
                     reason, payload_json, status, setup_version, batch_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, values)
            written += len(batch)
            logger.info("  Written %d/%d opportunities", written, len(opps))

        conn.commit()

    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backfill D2 expired opportunities")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--batch-id", default=f"d2_backfill_{TIMESTAMP}",
                        help="Batch ID for this backfill run")
    args = parser.parse_args()

    t0 = time.time()
    lines = []

    def log(msg):
        logger.info(msg)
        lines.append(msg)

    log(f"=== D2 Expired Opportunity Backfill ===")
    log(f"Batch ID: {args.batch_id}")
    log(f"Dry run: {args.dry_run}")
    log(f"Config: entry={ENTRY_THRESHOLD*100}%, stop={STOP_PCT*100}%, "
        f"time_stop={TIME_STOP_HOURS}h, max_watch={MAX_WATCH_HOURS}h")
    log(f"Gates: mania>{MANIA_GATE_PCT}%, liquidity>${LIQUIDITY_GATE_USD:,.0f}")
    log("")

    with psycopg.connect(DATABASE_URL) as conn:
        # 1. Load signals
        log("Step 1: Loading pump signals from social_bot_signals...")
        signals = load_pump_signals(conn)
        log(f"  Loaded {len(signals)} signals")

        # 2. Detect clusters
        log("Step 2: Detecting pump clusters...")
        clusters = detect_pump_clusters(signals)
        log(f"  Found {len(clusters)} clusters (>= {MIN_ALERTS_PER_CLUSTER} alerts within {CLUSTER_WINDOW_HOURS}h)")

        if not clusters:
            log("No clusters found. Exiting.")
            with open(OUTPUT_TXT, "w") as f:
                f.write("\n".join(lines))
            return

        # 3. Get available symbols
        available_symbols = get_binance_perp_symbols(conn)
        log(f"  Available symbols in OHLCV: {len(available_symbols)}")

        # 4. Apply gates
        log("Step 3: Applying gates...")
        gated_clusters = []
        gate_reasons = defaultdict(int)
        for c in clusters:
            passed, reason = apply_gates(c, available_symbols)
            if passed:
                gated_clusters.append(c)
            else:
                gate_reasons[reason] += 1

        log(f"  Passed gates: {len(gated_clusters)}/{len(clusters)}")
        for reason, count in sorted(gate_reasons.items(), key=lambda x: -x[1]):
            log(f"    Rejected: {reason}: {count}")
        log("")

        if not gated_clusters:
            log("No clusters passed gates. Exiting.")
            with open(OUTPUT_TXT, "w") as f:
                f.write("\n".join(lines))
            return

        # 5. Load OHLCV
        log("Step 4: Loading OHLCV data...")
        ohlcv = load_ohlcv_for_clusters(conn, gated_clusters)

        # 6. Simulate entries
        log("Step 5: Simulating HWM trailing drop entries...")
        opportunities = []
        no_entry = 0
        no_ohlcv = 0

        for ci, cluster in enumerate(gated_clusters):
            sym = cluster["symbol"]
            if sym not in ohlcv:
                no_ohlcv += 1
                continue

            bars = ohlcv[sym]
            bar_times = [b[0] for b in bars]

            result = simulate_hwm_trailing(cluster, bars, bar_times)
            if result is not None:
                opportunities.append(result)
            else:
                no_entry += 1

            if (ci + 1) % 50 == 0:
                logger.info("  Progress: %d/%d clusters processed, %d opps so far",
                            ci + 1, len(gated_clusters), len(opportunities))

        log(f"  Valid entries: {len(opportunities)}")
        log(f"  No valid entry: {no_entry}")
        log(f"  No OHLCV: {no_ohlcv}")
        log("")

        # 7. Stats
        if opportunities:
            exits = defaultdict(int)
            pnls = []
            for opp in opportunities:
                exits[opp["exit_reason"]] += 1
                pnls.append(opp["pnl_after_costs_pct"])

            winners = [p for p in pnls if p > 0]
            losers = [p for p in pnls if p <= 0]

            log("=== Backfill Stats ===")
            log(f"Total opportunities: {len(opportunities)}")
            log(f"Exit breakdown:")
            for reason, count in sorted(exits.items(), key=lambda x: -x[1]):
                log(f"  {reason}: {count} ({count/len(opportunities)*100:.1f}%)")
            log(f"Win rate: {len(winners)/len(opportunities)*100:.1f}%")
            if winners:
                log(f"Avg winner: {sum(winners)/len(winners):.2f}%")
            if losers:
                log(f"Avg loser: {sum(losers)/len(losers):.2f}%")
            log(f"Mean PnL (after costs): {sum(pnls)/len(pnls):.2f}%")

            total_wins = sum(p for p in pnls if p > 0)
            total_losses = abs(sum(p for p in pnls if p <= 0))
            pf = total_wins / total_losses if total_losses > 0 else float("inf")
            log(f"Aggregate PF: {pf:.2f}")
            log("")

            # Date range
            earliest = min(o["entry_ts"] for o in opportunities)
            latest = max(o["entry_ts"] for o in opportunities)
            log(f"Date range: {earliest.date()} to {latest.date()}")
            log("")

        # 8. Write to DB
        log("Step 6: Writing opportunities to DB...")
        written = write_opportunities(conn, opportunities, args.batch_id, args.dry_run)
        log(f"  Written: {written} opportunities")

    elapsed = time.time() - t0
    log(f"\nCompleted in {elapsed:.1f}s")

    # Write output file
    with open(OUTPUT_TXT, "w") as f:
        f.write("\n".join(lines))
    logger.info("Output written to %s", OUTPUT_TXT)


if __name__ == "__main__":
    main()
