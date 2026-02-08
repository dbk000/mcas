#!/usr/bin/env python3
"""
Backfill opportunities from stored OHLCV data using current setup code.

Reads OHLCV from Postgres ohlcv table (populated by backfill_ohlcv.py),
walks forward bar-by-bar computing indicators and evaluating S1/S2 setups,
and inserts triggered signals as expired opportunities with setup_version
and batch_id for version tracking.

Uses the same evaluator code as production — guaranteed consistency.

Usage:
    python Backend/scripts/backfill_opportunities.py --days 7 --setup-version "S1_v2.2,S2_v1.5"

    # After code changes, re-run with new version tag:
    python Backend/scripts/backfill_opportunities.py --days 7 --setup-version "S1_v2.3,S2_v1.6"

Requires DATABASE_URL env var (reads from .env if available).
"""

import os
import sys
import json
import time
import hashlib
import logging
import argparse
from datetime import datetime, timezone, timedelta
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
worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
sys.path.insert(0, os.path.abspath(worker_app_dir))

# Also add setups dir
setups_dir = os.path.join(worker_app_dir, 'setups')
sys.path.insert(0, os.path.abspath(setups_dir))

import psycopg
import numpy as np

from indicators import OHLCV, FeatureFactory, IndicatorSnapshot
from setups.s1_bb_squeeze import S1Evaluator, S1Signal
from setups.s2_golden_cross import S2Evaluator, S2Signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

# HTF mapping for S1 bias scoring
HTF_MAP = {
    "1m": "5m",
    "5m": "1h",
    "15m": "4h",
    "1h": "4h",
    "4h": "1d",
    "1d": None,
}

# Minimum bars needed for indicator computation
MIN_BARS = 201
# Warmup bars before we start evaluating triggers
WARMUP_BARS = 500


def load_ohlcv_from_db(
    days: int,
    symbols: list[str] = None,
    timeframes: list[str] = None,
) -> dict[tuple[str, str], list[OHLCV]]:
    """Load OHLCV data from Postgres, grouped by (symbol, timeframe).

    Returns dict of (symbol, tf) -> sorted list of OHLCV objects (oldest first).
    """
    logger.info("Loading OHLCV data from database...")

    conditions = ["is_final = TRUE"]
    params = []

    if days:
        conditions.append("bar_start_ts >= NOW() - INTERVAL '%s days'")
        params.append(days)

    if symbols:
        conditions.append("symbol = ANY(%s)")
        params.append(symbols)

    if timeframes:
        conditions.append("timeframe = ANY(%s)")
        params.append(timeframes)

    where_clause = " AND ".join(conditions)

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


def build_signal_payload(signal, setup_id: str, symbol: str, tf: str, bar_ts_ms: int) -> dict:
    """Convert evaluator signal into payload dict matching live signal format."""
    payload = {
        "setup_id": setup_id,
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

    return payload


def process_symbol_timeframe(
    symbol: str,
    tf: str,
    candles: list[OHLCV],
    htf_candles: list[OHLCV],
    btc_candles: list[OHLCV],
    setup_versions: dict[str, str],
    batch_id: str,
    factory: FeatureFactory,
    s1_eval: S1Evaluator,
    s2_eval: S2Evaluator,
    start_days: int,
) -> list[dict]:
    """Walk forward through candles, evaluating S1/S2 at each bar.

    Returns list of opportunity dicts for triggered signals.
    """
    if len(candles) < MIN_BARS:
        return []

    opportunities = []

    # State tracking
    s1_prev_state = None
    s2_prev_state = None
    bb_width_history = []
    ma_gap_history = []

    # BTC data for correlation filter
    btc_factory = FeatureFactory()
    btc_bb_width_history = []

    # Precompute HTF index for fast lookup
    htf_by_ts = {}
    if htf_candles:
        for c in htf_candles:
            htf_by_ts[c.timestamp] = c

    # Determine evaluation start index (skip warmup)
    # We need WARMUP_BARS of history, but start evaluating only within --days window
    eval_start_idx = max(WARMUP_BARS, MIN_BARS)

    # Calculate the cutoff timestamp for the --days window
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - (start_days * 86400 * 1000)

    for i in range(MIN_BARS, len(candles)):
        # Build candle window (up to 500 bars of history)
        window_start = max(0, i - WARMUP_BARS + 1)
        window = candles[window_start:i + 1]

        if len(window) < MIN_BARS:
            continue

        current_bar = candles[i]

        # Compute indicators
        try:
            # Get HTF candles up to current timestamp
            htf_window = None
            if htf_candles:
                htf_window = [c for c in htf_candles if c.timestamp <= current_bar.timestamp]
                if htf_window and len(htf_window) >= 50:
                    htf_window = htf_window[-200:]  # Keep last 200 HTF bars
                else:
                    htf_window = None

            snapshot = factory.compute(window, htf_window)
        except Exception:
            continue

        # Track indicator histories
        bb_width_history.append(snapshot.bb_width)
        ma_gap_history.append(snapshot.ma_gap_atr)

        # Only evaluate triggers within the requested date range
        if current_bar.timestamp < cutoff_ms:
            # Still compute state for warmup tracking
            s1_state = s1_eval.compute_state(
                snapshot, bb_width_history, s1_prev_state,
            )
            s1_prev_state = s1_state

            s2_state = s2_eval.compute_state(
                snapshot, ma_gap_history, s2_prev_state,
            )
            s2_prev_state = s2_state
            continue

        # Compute BTC snapshot for correlation filter (altcoins only)
        btc_snapshot = None
        btc_bb_hist = None
        if symbol != "BTCUSDT" and btc_candles:
            btc_window = [c for c in btc_candles if c.timestamp <= current_bar.timestamp]
            if btc_window and len(btc_window) >= MIN_BARS:
                btc_window = btc_window[-WARMUP_BARS:]
                try:
                    btc_snapshot = btc_factory.compute(btc_window)
                    btc_bb_width_history.append(btc_snapshot.bb_width)
                    btc_bb_hist = btc_bb_width_history
                except Exception:
                    pass

        # --- S1 Evaluation ---
        s1_state = s1_eval.compute_state(
            snapshot, bb_width_history, s1_prev_state,
            btc_snapshot, btc_bb_hist,
        )
        s1_prev_state = s1_state

        s1_signal = s1_eval.evaluate_trigger(snapshot, s1_state, symbol, tf)
        if s1_signal:
            bar_dt = datetime.fromtimestamp(current_bar.timestamp / 1000, tz=timezone.utc)
            reason = "; ".join(s1_signal.explain.why_items) if s1_signal.explain else None
            payload = build_signal_payload(s1_signal, "S1", symbol, tf, current_bar.timestamp)

            opportunities.append({
                "created_ts": bar_dt,
                "symbol": symbol,
                "timeframe": tf,
                "setup_id": "S1",
                "decision": "SIGNAL",
                "score": s1_signal.setup_score,
                "reason": reason,
                "payload": payload,
                "setup_version": setup_versions.get("S1", "S1_v2.1"),
                "batch_id": batch_id,
            })

        # --- S2 Evaluation ---
        s2_state = s2_eval.compute_state(
            snapshot, ma_gap_history, s2_prev_state, btc_snapshot,
        )
        s2_prev_state = s2_state

        s2_signal = s2_eval.evaluate_trigger(snapshot, s2_state, symbol, tf)
        if s2_signal:
            bar_dt = datetime.fromtimestamp(current_bar.timestamp / 1000, tz=timezone.utc)
            reason = "; ".join(s2_signal.explain.why_items) if s2_signal.explain else None
            payload = build_signal_payload(s2_signal, "S2", symbol, tf, current_bar.timestamp)

            opportunities.append({
                "created_ts": bar_dt,
                "symbol": symbol,
                "timeframe": tf,
                "setup_id": "S2",
                "decision": "SIGNAL",
                "score": s2_signal.setup_score,
                "reason": reason,
                "payload": payload,
                "setup_version": setup_versions.get("S2", "S2_v1.4"),
                "batch_id": batch_id,
            })

    return opportunities


def main():
    parser = argparse.ArgumentParser(
        description="Backfill opportunities from stored OHLCV data"
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Number of days to evaluate (default: 7)"
    )
    parser.add_argument(
        "--setup-version", type=str, default=None,
        help="Comma-separated setup versions (e.g. 'S1_v2.2,S2_v1.5'). "
             "Defaults to current code versions."
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
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set. Set it in .env or environment.")
        sys.exit(1)

    # Parse setup versions
    setup_versions = {"S1": "S1_v2.1", "S2": "S2_v1.4"}
    if args.setup_version:
        for sv in args.setup_version.split(","):
            sv = sv.strip()
            if sv.startswith("S1"):
                setup_versions["S1"] = sv
            elif sv.startswith("S2"):
                setup_versions["S2"] = sv

    # Generate batch ID
    batch_hash = hashlib.sha256(
        f"{datetime.now(timezone.utc).isoformat()}{args.setup_version}".encode()
    ).hexdigest()[:8]
    batch_id = f"backfill_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{batch_hash}"

    # Parse symbol/timeframe filters
    symbol_filter = None
    if args.symbols:
        symbol_filter = [s.strip().upper() for s in args.symbols.split(",")]

    tf_filter = None
    if args.timeframes:
        tf_filter = [t.strip() for t in args.timeframes.split(",")]

    logger.info("=" * 60)
    logger.info("MCAS Opportunity Backfill")
    logger.info("=" * 60)
    logger.info(f"  Days:           {args.days}")
    logger.info(f"  Setup versions: {setup_versions}")
    logger.info(f"  Batch ID:       {batch_id}")
    logger.info(f"  Symbol filter:  {symbol_filter or 'all'}")
    logger.info(f"  TF filter:      {tf_filter or 'all'}")
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

    # Group by symbol to find unique symbols
    symbols_seen = set()
    tfs_seen = set()
    for (sym, tf) in all_data:
        symbols_seen.add(sym)
        tfs_seen.add(tf)

    logger.info(f"Processing {len(symbols_seen)} symbols x {len(tfs_seen)} timeframes")

    # Initialize evaluators (shared across all symbols)
    factory = FeatureFactory()
    s1_eval = S1Evaluator()
    s2_eval = S2Evaluator()

    # Preload BTC data for each timeframe (needed for correlation filter)
    btc_data = {}
    for tf in tfs_seen:
        key = ("BTCUSDT", tf)
        if key in all_data:
            btc_data[tf] = all_data[key]
        else:
            btc_data[tf] = []

    t0 = time.time()
    total_opps = 0
    total_bars_evaluated = 0
    all_opportunities = []
    processed = 0
    total_combos = len(all_data)

    for (symbol, tf), candles in sorted(all_data.items()):
        processed += 1

        # Get HTF candles
        htf_tf = HTF_MAP.get(tf)
        htf_candles = []
        if htf_tf:
            htf_key = (symbol, htf_tf)
            htf_candles = all_data.get(htf_key, [])

        # Get BTC candles for this timeframe
        btc_candles = btc_data.get(tf, [])

        opps = process_symbol_timeframe(
            symbol=symbol,
            tf=tf,
            candles=candles,
            htf_candles=htf_candles,
            btc_candles=btc_candles,
            setup_versions=setup_versions,
            batch_id=batch_id,
            factory=factory,
            s1_eval=s1_eval,
            s2_eval=s2_eval,
            start_days=args.days,
        )

        all_opportunities.extend(opps)
        total_bars_evaluated += max(0, len(candles) - MIN_BARS)

        if processed % 50 == 0 or processed == total_combos:
            elapsed = time.time() - t0
            logger.info(
                f"  [{processed}/{total_combos}] "
                f"{total_bars_evaluated:,} bars evaluated, "
                f"{len(all_opportunities)} signals found "
                f"({elapsed:.0f}s)"
            )

    # Insert all opportunities into database
    logger.info(f"\nInserting {len(all_opportunities)} opportunities into database...")
    inserted = insert_opportunities(all_opportunities)

    # Summary by setup
    by_setup = defaultdict(int)
    by_tf = defaultdict(int)
    for opp in all_opportunities:
        by_setup[opp["setup_id"]] += 1
        by_tf[opp["timeframe"]] += 1

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info(f"Backfill complete in {elapsed:.1f}s")
    logger.info(f"  Bars evaluated:       {total_bars_evaluated:,}")
    logger.info(f"  Signals found:        {len(all_opportunities)}")
    logger.info(f"  Opportunities stored: {inserted}")
    logger.info(f"  Batch ID:             {batch_id}")
    logger.info(f"  Setup versions:       {setup_versions}")
    logger.info(f"\n  By setup:")
    for setup_id, count in sorted(by_setup.items()):
        logger.info(f"    {setup_id}: {count}")
    logger.info(f"  By timeframe:")
    for tf, count in sorted(by_tf.items()):
        logger.info(f"    {tf}: {count}")
    logger.info("=" * 60)
    logger.info(f"\nTo simulate these opportunities:")
    logger.info(f'  POST /v1/control/runs/simulation')
    logger.info(f'  {{"batch_id": "{batch_id}"}}')


if __name__ == "__main__":
    main()
