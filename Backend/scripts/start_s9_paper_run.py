#!/usr/bin/env python3
"""
Start S9 Paper Trading Run (Phase 3)
====================================

Creates (and optionally drives) a dedicated S9-only paper trading run with the
canonical configuration from Phase 0 / Phase 4:
  - MAX_CONCURRENT = 15 (via ExecutionConfig.max_positions)
  - Only S9 signals accepted (filter_setup_ids = ['S9'])
  - Perp-only universe whitelist (baked into signal_worker via Phase 4)
  - Canonical exit engine (check_exit_simple + split-exit, via Phase 2)
  - Position size $1000, initial capital $20k (1.5× headroom over 15x$1000)

Usage
-----
    # One-shot run creation (no loop) — creates run + paper_account, returns run_id
    docker exec backend-computers-1 python3 /backend/scripts/start_s9_paper_run.py --create

    # Continuous loop (foreground) — polls Redis for new S9 signals every 60s
    # and calls update_positions on every 1h bar close.
    docker exec -d backend-computers-1 python3 /backend/scripts/start_s9_paper_run.py --loop

    # Stop the currently-running paper run
    docker exec backend-computers-1 python3 /backend/scripts/start_s9_paper_run.py --stop

Notes
-----
- The loop is intentionally simple: it polls the opportunities table for
  freshly-generated S9 signals (created_ts within the last 2 hours that are
  not yet in paper_positions for this run), and feeds each through
  engine.process_signal(). No Redis pub/sub dependency.
- update_positions is called once per hour, 60 seconds after the top of
  the hour — this matches the signal_worker's S9 cadence and ensures the
  hourly 1h bar has finalized.
- Graceful shutdown on SIGINT/SIGTERM: final update, mark run SUCCESS.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal as sigmod
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

# Make governance/ and core_logic/ importable from host or container
if os.path.isdir("/backend"):
    sys.path.insert(0, "/backend")
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_logic.execution import ExecutionConfig, realistic_config  # noqa: E402
from governance.paper_trading import PaperTradingEngine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trading:trading@127.0.0.1:5432/trading")

# --- S9 canonical execution config -------------------------------------------
# Phase 0 (perp rerank): daily PF 3.738 at cap=15 on perp-only universe.
S9_MAX_CONCURRENT = 15
S9_INITIAL_CAPITAL = 20_000.0      # $20k working capital
S9_POSITION_SIZE_USD = 100.0       # $100 notional per trade (2026-04-09 user set)
# S9 canonical stop distance is 10% of entry (hardcoded in
# core_logic.s9_short_reversion.S9Evaluator.build_signal). To produce a
# $100 notional with the risk-based sizing in paper_trading.process_signal
# (position_size = risk_amount / stop_distance), we need
#   risk_amount = S9_POSITION_SIZE_USD * 0.10 = $10
# which is 0.05% of $20k initial capital:
S9_MAX_RISK_PER_TRADE_PCT = (S9_POSITION_SIZE_USD * 0.10) / S9_INITIAL_CAPITAL * 100
# → 0.05  (yields $10 risk → $100 notional at S9's 10% stop)


def build_s9_execution_config() -> ExecutionConfig:
    """Build the canonical S9 ExecutionConfig.

    Starts from `realistic_config(timeframe='1h')` and overrides:
      - max_positions = 15 (canon)
      - max_risk_per_trade_pct derived from $100 notional target

    NOTE: we use dataclasses.replace() NOT asdict() round-trip — asdict
    would flatten the nested SlippageModel/FeeModel instances to dicts,
    and ExecutionConfig(**dict) would then store dicts in those slots,
    causing `self.execution_config.slippage.calculate(...)` to crash in
    PaperTradingEngine. Same bug pattern as s9_paper_reconcile.py.
    """
    from dataclasses import replace
    return replace(
        realistic_config(timeframe="1h"),
        max_positions=S9_MAX_CONCURRENT,
        max_risk_per_trade_pct=S9_MAX_RISK_PER_TRADE_PCT,
    )


# -----------------------------------------------------------------------------
# Cross-layer validator — Phase 4 item
# -----------------------------------------------------------------------------

def validate_s9_canon(exec_cfg: ExecutionConfig) -> None:
    """Sanity-check the canonical relationship between S9TypedConfig and
    ExecutionConfig for a paper run that will trade S9.

    Raises if the two layers disagree.
    """
    # Only load governance stack if we're inside a container that has it.
    try:
        from governance.bootstrap import bootstrap_genesis, get_config
        bootstrap_genesis()
        s9 = get_config().S9
        if exec_cfg.max_positions != s9.MAX_CONCURRENT:
            raise RuntimeError(
                f"S9 canon violation: ExecutionConfig.max_positions={exec_cfg.max_positions} "
                f"!= S9TypedConfig.MAX_CONCURRENT={s9.MAX_CONCURRENT}. "
                "Either update S9TypedConfig.MAX_CONCURRENT or the launcher override."
            )
        logger.info(
            "S9 canon OK: MAX_CONCURRENT=%d, whitelist_path=%s",
            s9.MAX_CONCURRENT, s9.SYMBOL_WHITELIST_PATH,
        )
    except SystemExit:
        # bootstrap may call sys.exit on registry violation — re-raise
        raise
    except ImportError:
        logger.warning("governance.bootstrap unavailable — skipping canon validator (host mode)")


# -----------------------------------------------------------------------------
# Run lifecycle helpers
# -----------------------------------------------------------------------------

def create_paper_run(
    conn,
    initial_capital: float,
    exec_cfg: ExecutionConfig,
    name: str,
) -> str:
    """Create a PAPER run and its paper_account atomically. Returns run_id."""
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    execution_json = json.dumps({
        **asdict(exec_cfg),
        "setup_filter": ["S9"],
        "symbol_whitelist": "s9_symbol_whitelist (from frozen registry)",
        "position_size_usd": S9_POSITION_SIZE_USD,
    }, default=str, sort_keys=True)

    strategy_json = json.dumps({
        "setup_ids": ["S9"],
        "canon_reference_batch": "regen_perp_only_20260408",
        "canon_reference_run": "47f3d418-5153-4bba-a17d-f6d0d4b91143",
    }, sort_keys=True)

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO runs (
                id, run_type, status, name,
                engine_version, strategy_json, execution_json, data_json,
                started_at, summary_json, created_at
            ) VALUES (
                %s, 'PAPER', 'RUNNING', %s,
                '1.0.0', %s, %s, %s,
                %s, %s, %s
            );
        """, (
            run_id, name,
            strategy_json, execution_json, json.dumps({}),
            now, json.dumps({"total_trades": 0, "signals_generated": 0}), now,
        ))

        cur.execute("""
            INSERT INTO paper_accounts (
                run_id, initial_capital, current_capital, peak_capital,
                daily_pnl_reset_date, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s);
        """, (
            run_id, initial_capital, initial_capital, initial_capital,
            now.date(), now, now,
        ))

    conn.commit()
    return run_id


def stop_active_paper_run(conn) -> str | None:
    """Mark the most recent RUNNING PAPER run as SUCCESS. Returns the run_id."""
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, started_at FROM runs
            WHERE run_type = 'PAPER' AND status = 'RUNNING'
            ORDER BY started_at DESC LIMIT 1;
        """)
        row = cur.fetchone()
        if not row:
            return None
        run_id, started = row
        duration = (now - started).total_seconds() if started else 0
        cur.execute("""
            UPDATE runs
            SET status = 'SUCCESS', completed_at = %s, duration_seconds = %s
            WHERE id = %s;
        """, (now, duration, run_id))
    conn.commit()
    return str(run_id)


# -----------------------------------------------------------------------------
# Signal polling (DB-based, no Redis dependency)
# -----------------------------------------------------------------------------

def fetch_new_s9_signals(conn, run_id: str, since: datetime) -> list[dict]:
    """Poll the opportunities table for S9 signals created since `since` that
    aren't yet processed by this paper run."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.id, o.symbol, o.timeframe, o.created_ts, o.payload_json
            FROM opportunities o
            LEFT JOIN paper_positions pp
              ON pp.run_id = %s
             AND pp.symbol = o.symbol
             AND pp.signal_ts = o.created_ts
            WHERE o.setup_id = 'S9'
              AND o.created_ts >= %s
              AND pp.id IS NULL
            ORDER BY o.created_ts ASC;
        """, (run_id, since))
        rows = cur.fetchall()

    signals: list[dict] = []
    for row in rows:
        opp_id, symbol, tf, created_ts, payload_json = row
        payload = payload_json if isinstance(payload_json, dict) else json.loads(payload_json or "{}")
        signals.append({
            "opportunity_id": opp_id,
            "symbol": symbol,
            "timeframe": tf,
            "created_ts": created_ts,
            "direction": payload.get("direction", "short"),
            "setup_id": "S9",
            "entry_price": payload.get("entry_price") or payload.get("naive_entry_price"),
            "stop_price": payload.get("stop_price"),
            "tp1_price": payload.get("tp1_price"),
            "tp2_price": payload.get("tp2_price"),
            "r_value": payload.get("r_value") or abs(
                (payload.get("entry_price") or 0) - (payload.get("stop_price") or 0)
            ),
            "time_stop_bars": payload.get("time_stop_bars", 48),
            "setup_score": payload.get("setup_score", 0.0),
            "payload": payload,
        })
    return signals


def fetch_latest_final_bar_ms(conn) -> int:
    """Return the most recently-finalized 1h bar (in ms epoch) using BTCUSDT
    as the reference symbol. BTCUSDT is always tracked by the ingestor and
    is in the S9 whitelist — its latest final bar is the canonical "live
    signal bar". Returns 0 if no bar is available."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(bar_start_ts)
            FROM ohlcv
            WHERE symbol = 'BTCUSDT'
              AND timeframe = '1h'
              AND is_final = TRUE
              AND bar_start_ts >= NOW() - INTERVAL '3 hours';
        """)
        row = cur.fetchone()
    if not row or row[0] is None:
        return 0
    return int(row[0].timestamp() * 1000)


def fetch_bars_at_ts(conn, symbols: list[str], bar_ts_ms: int) -> dict[str, dict]:
    """Fetch each symbol's 1h bar AT the specific reference timestamp.

    Returns a dict keyed by symbol. Symbols missing the reference bar are
    dropped from the result — their positions simply skip this update tick
    and will be re-checked on the next bar-advance.
    """
    if not symbols or bar_ts_ms <= 0:
        return {}
    from datetime import datetime, timezone
    bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (symbol)
                   symbol, open, high, low, close, bar_start_ts
            FROM ohlcv
            WHERE symbol = ANY(%s)
              AND timeframe = '1h'
              AND bar_start_ts = %s
              AND is_final = TRUE
            ORDER BY symbol,
                     CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END;
        """, (symbols, bar_dt))
        return {
            row[0]: {
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "bar_start_ts": row[5],
            }
            for row in cur.fetchall()
        }


# -----------------------------------------------------------------------------
# Continuous loop
# -----------------------------------------------------------------------------

_SHUTDOWN = False


def _install_signal_handlers():
    def _handler(_signum, _frame):
        global _SHUTDOWN
        logger.info("Shutdown signal received — finishing current iteration")
        _SHUTDOWN = True
    sigmod.signal(sigmod.SIGINT, _handler)
    sigmod.signal(sigmod.SIGTERM, _handler)


def run_loop(run_id: str, engine: PaperTradingEngine):
    """S9 paper trading loop.

    Two data-driven cadences, both polled every 15s:

      1) Signal pickup — any time a new S9 opportunity appears in the DB
         with created_ts > last-seen, hand it to engine.process_signal.
      2) Position update — any time a new 1h bar becomes final in Postgres
         (tracked via a bar-advance gate on BTCUSDT), call update_positions
         with THAT bar's OHLC for every open symbol.

    The bar-advance gate replaces the old wall-clock hour gate so we:
      - never scan the same bar twice (short-circuits cheaply)
      - always act on a newly-available bar the instant it's finalized
      - automatically recover from restarts/lag — first tick after
        startup will see a new bar and run update_positions immediately

    S9 positions are exclusively handled by this path. governance.price_feed's
    PositionMonitor excludes setup_id='S9' to prevent racing.
    """
    _install_signal_handlers()

    last_processed_bar_ms = 0
    since = datetime.now(timezone.utc) - timedelta(hours=2)

    logger.info("S9 paper loop started (run_id=%s)", run_id)

    while not _SHUTDOWN:
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                # 1) Process new signals
                new_sigs = fetch_new_s9_signals(conn, run_id, since)
                if new_sigs:
                    logger.info("Fetched %d new S9 signals since %s", len(new_sigs), since)
                    for sig in new_sigs:
                        try:
                            pos_id = engine.process_signal(run_id, sig)
                            if pos_id:
                                logger.info(
                                    "Opened paper position %s for %s",
                                    pos_id, sig["symbol"],
                                )
                        except Exception as e:
                            logger.error("process_signal failed for %s: %s", sig["symbol"], e)
                    since = max((s["created_ts"] for s in new_sigs), default=since)

                # 2) Update positions on bar-advance (data-driven gate).
                latest_bar_ms = fetch_latest_final_bar_ms(conn)
                if latest_bar_ms > last_processed_bar_ms:
                    open_syms = [
                        p["symbol"] for p in engine.get_open_positions(run_id)
                    ]
                    if open_syms:
                        prices = fetch_bars_at_ts(conn, open_syms, latest_bar_ms)
                        if prices:
                            closed = engine.update_positions(run_id, prices)
                            logger.info(
                                "S9 update_positions at bar %s: %d open, %d closed",
                                datetime.fromtimestamp(latest_bar_ms / 1000, tz=timezone.utc).isoformat(),
                                len(open_syms), len(closed),
                            )
                    last_processed_bar_ms = latest_bar_ms

        except Exception as e:
            logger.error("Loop iteration failed: %s", e, exc_info=True)

        time.sleep(15)

    logger.info("S9 paper loop exiting")


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="S9 paper run launcher")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true",
                      help="Create a new S9 paper run (no loop).")
    mode.add_argument("--loop", action="store_true",
                      help="Create a run (if none active) then run the polling loop.")
    mode.add_argument("--stop", action="store_true",
                      help="Stop the currently-active S9 paper run.")
    parser.add_argument("--name", default="paper_s9_canon",
                        help="Human-readable run name")
    parser.add_argument("--initial-capital", type=float, default=S9_INITIAL_CAPITAL)
    args = parser.parse_args()

    exec_cfg = build_s9_execution_config()
    validate_s9_canon(exec_cfg)

    with psycopg.connect(DATABASE_URL) as conn:
        if args.stop:
            run_id = stop_active_paper_run(conn)
            if run_id:
                logger.info("Stopped paper run %s", run_id)
                return 0
            logger.warning("No RUNNING paper run found")
            return 1

        # Reuse active run if one exists, else create a new one
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM runs
                WHERE run_type = 'PAPER' AND status = 'RUNNING'
                  AND strategy_json LIKE '%%S9%%'
                ORDER BY started_at DESC LIMIT 1;
            """)
            row = cur.fetchone()

        if row:
            run_id = str(row[0])
            logger.info("Reusing existing active S9 paper run: %s", run_id)
        else:
            run_id = create_paper_run(conn, args.initial_capital, exec_cfg, args.name)
            logger.info(
                "Created new S9 paper run: %s (name=%s, initial_capital=%.2f)",
                run_id, args.name, args.initial_capital,
            )

    print(f"RUN_ID={run_id}")

    if args.create:
        return 0

    # --loop: start the continuous loop
    engine = PaperTradingEngine(database_url=DATABASE_URL, execution_config=exec_cfg)
    run_loop(run_id, engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
