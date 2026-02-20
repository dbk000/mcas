#!/usr/bin/env python3
"""
Backfill U1 (Unicorn Ignition) opportunities from stored OHLCV data.

Companion to backfill_opportunities.py — runs U1 only, so it can execute
independently while the main backfill covers S1-S7.

U1 uses raw candles (not IndicatorSnapshot/FeatureFactory), so this script
is simpler: load OHLCV → walk forward → evaluate_gates → evaluate_trigger.

Writes to DB incrementally per-symbol. Logs per-symbol progress with ETA.

Usage:
    python scripts/backfill_u1_opportunities.py --days 8 --timeframes 15m,1h,4h,1d

    # Clear stale U1 backfill opps before regenerating:
    python scripts/backfill_u1_opportunities.py --days 8 --timeframes 15m,1h,4h,1d --clear-expired

Requires DATABASE_URL env var (reads from .env if available).
"""

import os
import sys
import json
import time
import hashlib
import logging
import argparse
from datetime import datetime, timezone
from collections import defaultdict

# Load .env if available
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

# Add worker/app to path for imports
# In Docker: script at /scripts/, worker code at /app/
# On host: script at Backend/scripts/, worker code at Backend/worker/app/
worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/indicators.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))

# Also add setups dir
setups_dir = os.path.join(worker_app_dir, 'setups')
sys.path.insert(0, os.path.abspath(setups_dir))

import psycopg

from indicators import OHLCV
from setups.u1_unicorn_ignition import U1Evaluator, U1Signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

# U1 needs GATE_WINDOW_BARS * 2 = 96 candles minimum
U1_MIN_BARS = 96


def load_ohlcv_from_db(
    days: int,
    symbols: list[str] = None,
    timeframes: list[str] = None,
) -> dict[tuple[str, str], list[OHLCV]]:
    """Load OHLCV data from Postgres, grouped by (symbol, timeframe)."""
    logger.info("Loading OHLCV data from database...")

    conditions = ["is_final = TRUE"]
    params = []

    if days:
        conditions.append("bar_start_ts >= NOW() - %s * INTERVAL '1 day'")
        params.append(days)

    if symbols:
        conditions.append("symbol = ANY(%s)")
        params.append(symbols)

    if timeframes:
        conditions.append("timeframe = ANY(%s)")
        params.append(timeframes)

    where_clause = " AND ".join(conditions)
    logger.info(f"OHLCV query: WHERE {where_clause} | params={params}")

    data = defaultdict(list)

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT symbol, timeframe, bar_start_ts,
                       open, high, low, close, volume
                FROM ohlcv
                WHERE {where_clause}
                ORDER BY symbol, timeframe, bar_start_ts ASC;
            """, params)

            for row in cur:
                symbol, tf, bar_start_ts, o, h, l, c, v = row
                ts_ms = int(bar_start_ts.timestamp() * 1000)
                data[(symbol, tf)].append(OHLCV(
                    timestamp=ts_ms,
                    open=o, high=h, low=l, close=c, volume=v,
                ))

    total_bars = sum(len(v) for v in data.values())
    logger.info(f"Loaded {total_bars:,} bars across {len(data)} symbol/timeframe combos")
    return dict(data)


def insert_opportunities(
    opportunities: list[dict],
    batch_size: int = 500,
) -> int:
    """Bulk insert opportunities into Postgres. Returns count inserted."""
    if not opportunities:
        return 0

    inserted = 0
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for i in range(0, len(opportunities), batch_size):
                batch = opportunities[i:i + batch_size]
                values = []
                for opp in batch:
                    values.append((
                        opp["created_ts"],
                        opp["symbol"],
                        opp["timeframe"],
                        opp["setup_id"],
                        opp["decision"],
                        opp["score"],
                        opp["reason"],
                        json.dumps(opp["payload"]),
                        "expired",
                        opp["setup_version"],
                        opp["batch_id"],
                    ))

                cur.executemany("""
                    INSERT INTO opportunities
                        (created_ts, symbol, timeframe, setup_id, decision,
                         score, reason, payload_json, status,
                         setup_version, batch_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, values)
                inserted += len(batch)
        conn.commit()

    return inserted


def process_symbol_timeframe(
    symbol: str,
    tf: str,
    candles: list[OHLCV],
    setup_version: str,
    batch_id: str,
    evaluator: U1Evaluator,
    start_days: int,
) -> list[dict]:
    """Walk forward through candles, evaluating U1 gates + trigger at each bar.

    Returns list of opportunity dicts for triggered signals.
    """
    if len(candles) < U1_MIN_BARS:
        return []

    opportunities = []
    u1_state = None

    # Calculate the cutoff timestamp for the --days window
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - (start_days * 86400 * 1000)

    for i in range(U1_MIN_BARS, len(candles)):
        window = candles[max(0, i - 500 + 1):i + 1]
        current_bar = candles[i]

        # Evaluate gates every bar (builds state machine: EMBRYO → EMBRYO_ACTIVE)
        try:
            u1_state = evaluator.evaluate_gates(window, u1_state)
        except Exception:
            continue

        # Only evaluate triggers within the requested date range
        if current_bar.timestamp < cutoff_ms:
            continue

        # Evaluate trigger (only fires for EMBRYO_ACTIVE phase)
        try:
            signal = evaluator.evaluate_trigger(window, u1_state, symbol, tf, database_url=DATABASE_URL)
        except Exception:
            continue

        if signal:
            bar_ts_ms = current_bar.timestamp
            bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)  # Binance ms epoch
            reason = "; ".join(signal.explain.why_items) if signal.explain else None

            payload = {
                "setup_id": "U1",
                "symbol": symbol,
                "timeframe": tf,
                "direction": signal.direction.value,
                "setup_score": signal.setup_score,
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "tp1_price": signal.tp1_price,
                "tp2_price": signal.tp2_price,
                "r_value": signal.r_value,
                "time_stop_bars": signal.time_stop_bars,
                "bar_start_ts": bar_ts_ms,
                "as_of_ts": bar_ts_ms / 1000,
            }
            if signal.explain:
                payload["why_items"] = signal.explain.why_items

            opportunities.append({
                "created_ts": bar_dt,
                "symbol": symbol,
                "timeframe": tf,
                "setup_id": "U1",
                "decision": "SIGNAL",
                "score": signal.setup_score,
                "reason": reason,
                "payload": payload,
                "setup_version": setup_version,
                "batch_id": batch_id,
            })

    return opportunities


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def main():
    parser = argparse.ArgumentParser(
        description="Backfill U1 (Unicorn Ignition) opportunities from stored OHLCV data"
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Number of days to evaluate (default: 7)"
    )
    parser.add_argument(
        "--setup-version", type=str, default="U1_v1.0",
        help="U1 setup version tag (default: U1_v1.0)"
    )
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated symbols to process (default: all in ohlcv table)"
    )
    parser.add_argument(
        "--timeframes", type=str, default=None,
        help="Comma-separated timeframes (default: all in ohlcv table)"
    )
    parser.add_argument(
        "--extra-warmup-days", type=int, default=30,
        help="Extra days of OHLCV to load before --days window for warmup (default: 30)"
    )
    parser.add_argument(
        "--clear-expired", action="store_true",
        help="Delete stale backfill-generated expired U1 opportunities before inserting."
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set. Set it in .env or environment.")
        sys.exit(1)

    # Generate batch ID
    batch_hash = hashlib.sha256(
        f"{datetime.now(timezone.utc).isoformat()}U1{args.setup_version}".encode()
    ).hexdigest()[:8]
    batch_id = f"backfill_{datetime.now(timezone.utc).strftime('%Y%m%d')}_U1_{batch_hash}"

    # Parse filters
    symbol_filter = None
    if args.symbols:
        symbol_filter = [s.strip().upper() for s in args.symbols.split(",")]

    tf_filter = None
    if args.timeframes:
        tf_filter = [t.strip() for t in args.timeframes.split(",")]

    logger.info("=" * 60)
    logger.info("MCAS U1 Opportunity Backfill")
    logger.info("=" * 60)
    logger.info(f"  Days:           {args.days}")
    logger.info(f"  Setup version:  {args.setup_version}")
    logger.info(f"  Batch ID:       {batch_id}")
    logger.info(f"  Symbol filter:  {symbol_filter or 'all'}")
    logger.info(f"  TF filter:      {tf_filter or 'all'}")
    logger.info(f"  Clear expired:  {args.clear_expired}")
    logger.info("=" * 60)

    # Load OHLCV data (include warmup period)
    total_days = args.days + args.extra_warmup_days
    all_data = load_ohlcv_from_db(
        days=total_days,
        symbols=symbol_filter,
        timeframes=tf_filter,
    )

    if not all_data:
        logger.error("No OHLCV data found. Run backfill_ohlcv.py first.")
        sys.exit(1)

    symbols_seen = set()
    tfs_seen = set()
    for (sym, tf) in all_data:
        symbols_seen.add(sym)
        tfs_seen.add(tf)

    logger.info(f"Processing {len(symbols_seen)} symbols x {len(tfs_seen)} timeframes")

    # Clear stale U1 expired backfill opps if requested
    if args.clear_expired:
        tf_list = sorted(tfs_seen) if not tf_filter else tf_filter
        logger.info("=" * 60)
        logger.info("--clear-expired: Deleting stale U1 backfill opportunities")
        logger.info(f"  Scope: status='expired' AND batch_id LIKE 'backfill_%%' AND setup_id='U1'")
        logger.info(f"  Timeframes: {tf_list}")
        logger.info(f"  Window:     last {args.days} days")

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM opportunities
                    WHERE status = 'expired'
                      AND batch_id LIKE 'backfill_%%'
                      AND setup_id = 'U1'
                      AND timeframe = ANY(%(timeframes)s)
                      AND created_ts >= NOW() - %(days)s * INTERVAL '1 day'
                """, {
                    "timeframes": tf_list,
                    "days": args.days,
                })
                deleted_count = cur.rowcount
            conn.commit()
        logger.info(f"  Deleted {deleted_count:,} stale U1 opportunities")
        logger.info("=" * 60)

    # Group by symbol
    symbol_tfs_map = defaultdict(list)
    for (sym, tf) in sorted(all_data.keys()):
        symbol_tfs_map[sym].append(tf)
    work_items = list(symbol_tfs_map.items())

    t0 = time.time()
    total_inserted = 0
    total_found = 0
    evaluator = U1Evaluator()

    for idx, (symbol, tfs) in enumerate(work_items, 1):
        symbol_opps = []
        for tf in tfs:
            candles = all_data.get((symbol, tf), [])
            opps = process_symbol_timeframe(
                symbol=symbol, tf=tf, candles=candles,
                setup_version=args.setup_version, batch_id=batch_id,
                evaluator=evaluator, start_days=args.days,
            )
            symbol_opps.extend(opps)

        # Incremental write per-symbol
        if symbol_opps:
            symbol_opps.sort(key=lambda o: (o["timeframe"], o["created_ts"]))
            n_ins = insert_opportunities(symbol_opps)
            total_inserted += n_ins
            total_found += len(symbol_opps)

        # Per-symbol progress log
        elapsed = time.time() - t0
        eta = (elapsed / idx) * (len(work_items) - idx) if idx > 0 else 0
        logger.info(
            "[%d/%d] %s: %d U1 signals | %s inserted | %s elapsed | ~%s remaining",
            idx, len(work_items), symbol, len(symbol_opps),
            f"{total_inserted:,}",
            _format_duration(elapsed), _format_duration(eta),
        )

    # Summary
    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info(f"U1 backfill complete in {_format_duration(elapsed)}")
    logger.info(f"  Signals found:        {total_found:,}")
    logger.info(f"  Opportunities stored: {total_inserted:,}")
    logger.info(f"  Batch ID:             {batch_id}")
    logger.info(f"  Setup version:        {args.setup_version}")
    logger.info("=" * 60)
    logger.info(f"\nTo simulate these opportunities:")
    logger.info(f'  POST /v1/control/runs/simulation')
    logger.info(f'  {{"batch_id": "{batch_id}"}}')


if __name__ == "__main__":
    main()
