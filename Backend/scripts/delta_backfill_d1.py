#!/usr/bin/env python3
"""
Delta Backfill D1 — compute only NEW opportunities from loosened thresholds.

When loosening D1 entry thresholds (e.g. lowering dormancy from 0.80 to 0.70),
the strict batch is a proper subset of the loose batch. This script re-evaluates
the full D1 state machine with loose thresholds, then deduplicates against a
reference batch to insert only the genuinely new (delta) opportunities.

Key insight: This is NOT a pure SQL filter. The D1 state machine is non-linear —
changing the dormancy threshold changes phase transitions (SCANNING ->
DORMANT_CONFIRMED -> ACCUMULATING -> TRIGGERED), cascading to different trigger
bars. We must re-evaluate the full state machine with loose thresholds, then
dedup against the reference batch's signal keys.

Architecture: Forks from backfill_d1_opportunities.py (Pool(12), COW globals,
executemany batching). Self-contained — no imports from the reference script.

Usage:
    # Docker (production):
    docker compose -f docker-compose.yml run --rm -T worker \\
        python3 /scripts/delta_backfill_d1.py \\
        --reference-batch-id 'backfill_20260307_D1v1_%' \\
        --dormancy-threshold 0.70 \\
        --start-date 2025-09-01 --end-date 2026-03-07 \\
        --workers 12

    # Host:
    python scripts/delta_backfill_d1.py \\
        --reference-batch-id 'backfill_20260307_D1v1_abc12345' \\
        --dormancy-threshold 0.70 \\
        --start-date 2025-09-01 --end-date 2026-03-07

Requires DATABASE_URL env var (reads from .env if available).
"""

import hashlib
import json
import logging
import multiprocessing
import os
import sys
import time
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

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
if os.path.isdir('/app') and os.path.exists('/app/indicators.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))
setups_dir = os.path.join(worker_app_dir, 'setups')
sys.path.insert(0, os.path.abspath(setups_dir))

import psycopg
from indicators import FeatureFactory, OHLCV
from setups.d1_dormancy_ignition import (
    D1Evaluator, D1Signal, D1State, D1Phase, D1Config, is_d1_excluded,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

# Execution context
IN_DOCKER = os.path.isdir("/app")

# D1 needs FeatureFactory minimum of 201 candles
D1_MIN_BARS = 201

# Volume band for D1 universe
MIN_VOL_USD = D1Config.MIN_VOLUME_USD   # 75K
MAX_VOL_USD = D1Config.MAX_VOLUME_USD   # 3M

# Module-level shared data — set before Pool fork, inherited via copy-on-write
_ALL_DATA = None          # {(symbol, tf): [OHLCV, ...]}
_VOL_24H = None           # {(symbol, tf): np.array of rolling 24h quote vol}
_BTC_DATA = None          # {tf: [OHLCV, ...]} for BTC beta computation
_WORKER_ARGS = None       # dict of shared args
_EXCLUSION_SET = None     # set of excluded symbols
_REFERENCE_KEYS = None    # set of (symbol, tf, bar_ts_ms) from reference batch


# =========================================================================
# Exclusion List
# =========================================================================

EXTRA_EXCLUSIONS = {
    # Stablecoins / USD-pegged (dormancy=1.0 by design, not real signals)
    "USDCUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "RLUSDUSDT",
    "BFUSDUSDT", "USD1USDT", "USDEUSDT", "USTCUSDT", "FRAXUSDT",
    "XUSDUSDT", "UUSDT",
    # EUR/fiat-pegged (same issue — dormant by design)
    "EURUSDT", "EURIUSDT", "AEURUSDT",
    # Gold-pegged
    "PAXGUSDT",
    # Equities
    "AVGOUSDT", "METAUSDT", "NVDAUSDT", "QQQUSDT", "XLKUSDT",
}


def build_exclusion_set(symbols: list[str]) -> set[str]:
    """Build symbol exclusion set."""
    excluded = set()
    for sym in symbols:
        if sym in EXTRA_EXCLUSIONS:
            excluded.add(sym)
        elif is_d1_excluded(sym):
            excluded.add(sym)
    return excluded


# =========================================================================
# Reference Batch Loading
# =========================================================================

def load_reference_keys(batch_id_pattern: str) -> set[tuple[str, str, int]]:
    """Load signal keys from reference batch.

    Returns set of (symbol, timeframe, created_ts_epoch_ms) for dedup.
    """
    t0 = time.time()
    keys = set()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, timeframe, created_ts
                FROM opportunities
                WHERE batch_id LIKE %s
                  AND status = 'expired'
                  AND setup_id = 'D1'
            """, (batch_id_pattern,))

            for row in cur:
                symbol, tf, created_ts = row
                # Convert to epoch milliseconds for matching against bar timestamps
                ts_ms = int(created_ts.timestamp() * 1000)
                keys.add((symbol, tf, ts_ms))

    elapsed = time.time() - t0
    logger.info(f"Loaded {len(keys):,} reference keys from batch pattern "
                f"'{batch_id_pattern}' in {elapsed:.1f}s")
    return keys


# =========================================================================
# Data Loading
# =========================================================================

def load_ohlcv_from_db(
    start_date: datetime,
    end_date: datetime,
    extra_warmup_days: int,
    timeframes: list[str],
) -> dict[tuple[str, str], list[OHLCV]]:
    """Load OHLCV data from Postgres, grouped by (symbol, timeframe)."""
    warmup_start = start_date - timedelta(days=extra_warmup_days)

    logger.info(f"Loading OHLCV: {warmup_start.date()} to {end_date.date()} "
                f"for timeframes {timeframes}")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv
                WHERE timeframe = ANY(%s)
                  AND bar_start_ts >= %s AND bar_start_ts < %s
                  AND is_final = TRUE
            """, (timeframes, warmup_start, end_date))
            all_symbols = sorted([r[0] for r in cur.fetchall()])

    logger.info(f"Found {len(all_symbols)} symbols with OHLCV data")

    data = {}
    t0 = time.time()
    batch_size = 50

    for batch_start in range(0, len(all_symbols), batch_size):
        batch_syms = all_symbols[batch_start:batch_start + batch_size]

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT symbol, timeframe, bar_start_ts,
                           open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = ANY(%s)
                      AND timeframe = ANY(%s)
                      AND bar_start_ts >= %s AND bar_start_ts < %s
                      AND is_final = TRUE
                    ORDER BY symbol, timeframe, bar_start_ts ASC
                """, (batch_syms, timeframes, warmup_start, end_date))

                for row in cur:
                    symbol, tf, bar_start_ts, o, h, l, c, v = row
                    ts_ms = int(bar_start_ts.timestamp() * 1000)
                    key = (symbol, tf)
                    if key not in data:
                        data[key] = []
                    data[key].append(OHLCV(
                        timestamp=ts_ms,
                        open=o, high=h, low=l, close=c, volume=v,
                    ))

        loaded = min(batch_start + batch_size, len(all_symbols))
        if loaded % 200 == 0 or loaded == len(all_symbols):
            elapsed = time.time() - t0
            total_bars = sum(len(v) for v in data.values())
            logger.info(f"  OHLCV load: {loaded}/{len(all_symbols)} symbols, "
                        f"{total_bars:,} bars | {elapsed:.0f}s")

    total_bars = sum(len(v) for v in data.values())
    logger.info(f"Loaded {total_bars:,} bars across {len(data)} symbol/timeframe combos "
                f"in {time.time()-t0:.1f}s")
    return data


def load_btc_ohlcv(
    start_date: datetime, end_date: datetime, extra_warmup_days: int,
    timeframes: list[str],
) -> dict[str, list[OHLCV]]:
    """Load BTCUSDT OHLCV for beta adjustment."""
    warmup_start = start_date - timedelta(days=extra_warmup_days)
    btc_data = {}

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for tf in timeframes:
                cur.execute("""
                    SELECT bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = 'BTCUSDT' AND timeframe = %s
                      AND bar_start_ts >= %s AND bar_start_ts < %s
                      AND is_final = TRUE
                    ORDER BY bar_start_ts ASC
                """, (tf, warmup_start, end_date))
                candles = [
                    OHLCV(
                        timestamp=int(r[0].timestamp() * 1000),
                        open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5],
                    )
                    for r in cur.fetchall()
                ]
                btc_data[tf] = candles
                logger.info(f"  BTC {tf}: {len(candles):,} bars loaded")

    return btc_data


def precompute_rolling_volume(
    data: dict[tuple[str, str], list[OHLCV]],
) -> dict[tuple[str, str], np.ndarray]:
    """Precompute rolling 24h quote volume."""
    t0 = time.time()
    vol_24h = {}
    bars_24h = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}

    for (sym, tf), candles in data.items():
        n = len(candles)
        window = bars_24h.get(tf, 24)

        if n < window:
            vol_24h[(sym, tf)] = np.zeros(n)
            continue

        volumes = np.array([c.volume for c in candles])
        closes = np.array([c.close for c in candles])
        qvol = volumes * closes

        cumsum = np.cumsum(qvol)
        rolling = np.zeros(n)
        rolling[window - 1:] = cumsum[window - 1:] - np.concatenate([[0], cumsum[:n - window]])
        for i in range(min(window - 1, n)):
            rolling[i] = cumsum[i]

        vol_24h[(sym, tf)] = rolling

    logger.info(f"Precomputed rolling 24h volume for {len(vol_24h)} series "
                f"in {time.time()-t0:.1f}s")
    return vol_24h


# =========================================================================
# Per-Symbol Worker
# =========================================================================

def _worker_process_symbol(symbol: str) -> tuple[str, list[dict], dict]:
    """Multiprocessing worker: process one symbol across all timeframes.

    Uses fork-inherited globals (COW). Zero DB calls.
    Returns (symbol, opportunities, stats).

    Delta-specific: after finding a signal, checks if the signal key
    (symbol, tf, bar_ts_ms) exists in _REFERENCE_KEYS. If so, skips it
    as it's already in the reference batch.
    """
    all_data = _ALL_DATA
    vol_24h_data = _VOL_24H
    btc_data = _BTC_DATA
    args = _WORKER_ARGS
    exclusion_set = _EXCLUSION_SET
    reference_keys = _REFERENCE_KEYS

    output_start_ms = args["output_start_ms"]
    cooldown_hours = args["cooldown_hours"]
    setup_version = args["setup_version"]
    batch_id = args["batch_id"]
    tfs = args["symbol_tfs"].get(symbol, [])

    opportunities = []
    stats = {
        "signals": 0,
        "skipped_existing": 0,
        "delta_signals": 0,
        "bars_processed": 0,
    }

    if symbol in exclusion_set:
        return symbol, [], stats

    evaluator = D1Evaluator()
    ff = FeatureFactory()

    for tf in tfs:
        candles = all_data.get((symbol, tf), [])
        vol_arr = vol_24h_data.get((symbol, tf))

        if len(candles) < D1_MIN_BARS or vol_arr is None:
            continue

        cooldown_bars = int(cooldown_hours * (1 if tf == "1h" else 12))

        d1_state = None
        cooldown_until_idx = -1

        # Precompute BTC returns for this timeframe
        btc_rets_tf = None
        if btc_data:
            btc_candles = btc_data.get(tf, [])
            if len(btc_candles) >= 2:
                btc_closes = np.array([c.close for c in btc_candles])
                btc_rets_tf = np.diff(btc_closes) / btc_closes[:-1]

        for i in range(D1_MIN_BARS, len(candles)):
            stats["bars_processed"] += 1

            # Cooldown check
            if i < cooldown_until_idx:
                continue

            # Volume band check
            current_vol = vol_arr[i]
            if not (MIN_VOL_USD <= current_vol <= MAX_VOL_USD):
                if d1_state and d1_state.phase != D1Phase.COOLDOWN:
                    d1_state = None
                continue

            # Build candle window for FeatureFactory (needs 201 bars)
            window = candles[max(0, i - D1_MIN_BARS + 1):i + 1]
            if len(window) < D1_MIN_BARS:
                continue

            # Compute indicators
            try:
                snapshot = ff.compute(window)
            except Exception:
                continue

            # BTC returns for beta computation (align by bar index)
            btc_rets_window = None
            if btc_rets_tf is not None and i < len(btc_rets_tf):
                start_idx = max(0, i - D1Config.BTC_BETA_WINDOW)
                btc_rets_window = btc_rets_tf[start_idx:i]

            # Compute D1 state
            try:
                d1_state = evaluator.compute_state(
                    snapshot, prev_state=d1_state,
                    btc_returns=btc_rets_window,
                )
            except Exception:
                continue

            # Evaluate trigger (from DORMANT_CONFIRMED or ACCUMULATING)
            signal = None
            if d1_state.phase in (D1Phase.DORMANT_CONFIRMED, D1Phase.ACCUMULATING):
                try:
                    signal = evaluator.evaluate_trigger(
                        snapshot, d1_state, symbol, tf,
                    )
                except Exception:
                    pass

            if signal:
                current_bar = candles[i]
                bar_ts_ms = current_bar.timestamp
                stats["signals"] += 1

                # Delta check: skip if this signal exists in reference batch
                if (symbol, tf, bar_ts_ms) in reference_keys:
                    stats["skipped_existing"] += 1
                    d1_state = None
                    cooldown_until_idx = i + cooldown_bars
                    continue

                # Only record if after output start date
                if bar_ts_ms >= output_start_ms:
                    opportunities.append(
                        _build_opportunity(signal, current_bar, symbol, tf,
                                           setup_version, batch_id))
                    stats["delta_signals"] += 1

                # Reset state + enter cooldown
                d1_state = None
                cooldown_until_idx = i + cooldown_bars

    return symbol, opportunities, stats


def _build_opportunity(
    signal: D1Signal,
    current_bar: OHLCV,
    symbol: str,
    tf: str,
    setup_version: str,
    batch_id: str,
) -> dict:
    """Build opportunity dict from D1Signal."""
    bar_ts_ms = current_bar.timestamp
    bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)
    reason = "; ".join(signal.explain.why_items) if signal.explain else None

    payload = {
        "setup_id": "D1",
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
        "dormancy_score": signal.dormancy_score,
        "accum_divergence": signal.accum_divergence,
        "compression_box_high": signal.compression_box_high,
        "compression_box_low": signal.compression_box_low,
        "volume_z_at_trigger": signal.volume_z_at_trigger,
        "rvol_at_trigger": signal.rvol_at_trigger,
        "bb_width_at_trigger": signal.bb_width_at_trigger,
        "btc_beta": signal.btc_beta,
        "score_components": signal.score_components,
    }
    if signal.explain:
        payload["why_items"] = signal.explain.why_items

    return {
        "created_ts": bar_dt,
        "symbol": symbol,
        "timeframe": tf,
        "setup_id": "D1",
        "decision": "SIGNAL",
        "score": signal.setup_score,
        "reason": reason,
        "payload": payload,
        "setup_version": setup_version,
        "batch_id": batch_id,
    }


# =========================================================================
# DB Operations
# =========================================================================

def insert_opportunities(opportunities: list[dict], batch_size: int = 500) -> int:
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, values)
                inserted += len(batch)
        conn.commit()

    return inserted


# =========================================================================
# Helpers
# =========================================================================

def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


# =========================================================================
# Main
# =========================================================================

def main():
    global _ALL_DATA, _VOL_24H, _BTC_DATA, _WORKER_ARGS, _EXCLUSION_SET, _REFERENCE_KEYS

    parser = argparse.ArgumentParser(
        description="Delta Backfill D1 — compute only NEW opportunities from loosened thresholds"
    )
    parser.add_argument("--reference-batch-id", type=str, required=True,
                        help="Reference batch_id (exact match or SQL LIKE pattern, e.g. 'backfill_20260307_D1v1_%%')")
    parser.add_argument("--new-batch-id", type=str, default=None,
                        help="Batch ID for delta opportunities (auto-generated if not provided)")
    parser.add_argument("--start-date", type=str, required=True,
                        help="Output start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Output end date (YYYY-MM-DD, default: today)")
    parser.add_argument("--timeframes", type=str, default="1h",
                        help="D1 evaluation timeframes (default: 1h)")
    parser.add_argument("--cooldown-hours", type=int, default=24,
                        help="Cooldown between signals per symbol/tf (default: 24)")
    parser.add_argument("--workers", type=int, default=12,
                        help="Pool workers (default: 12)")
    parser.add_argument("--setup-version", type=str, default="D1_v1.0",
                        help="Version tag (default: D1_v1.0)")
    parser.add_argument("--extra-warmup-days", type=int, default=90,
                        help="OHLCV loaded before start-date for warmup (default: 90)")
    # Entry config overrides (these are the "loose" thresholds)
    parser.add_argument("--dormancy-threshold", type=float, default=None,
                        help="Override D1Config.DORMANCY_THRESHOLD (e.g. 0.70 to loosen from 0.75)")
    parser.add_argument("--rvol-min", type=float, default=None,
                        help="Override D1Config.RVOL_MIN (e.g. 2.0 to loosen from 3.0)")
    parser.add_argument("--volume-z-min", type=float, default=None,
                        help="Override D1Config.VOLUME_Z_SPIKE (e.g. 1.5 to loosen from 2.0)")
    parser.add_argument("--bb-width-max", type=float, default=None,
                        help="Override D1Config.BB_WIDTH_COMPRESS (e.g. 0.06 to loosen from 0.05)")
    parser.add_argument("--dormancy-range-threshold", type=float, default=None,
                        help="Override D1Config.DORMANCY_RANGE_THRESHOLD")
    args = parser.parse_args()

    # Apply entry config overrides to D1Config (affects D1Evaluator instances)
    if args.dormancy_threshold is not None:
        D1Config.DORMANCY_THRESHOLD = args.dormancy_threshold
    if args.rvol_min is not None:
        D1Config.RVOL_MIN = args.rvol_min
    if args.volume_z_min is not None:
        D1Config.VOLUME_Z_SPIKE = args.volume_z_min
    if args.bb_width_max is not None:
        D1Config.BB_WIDTH_COMPRESS = args.bb_width_max
    if args.dormancy_range_threshold is not None:
        D1Config.DORMANCY_RANGE_THRESHOLD = args.dormancy_range_threshold

    # Persistent output file
    if os.path.isdir("/scripts/output"):
        output_dir = "/scripts/output"
    elif os.path.isdir(os.path.join(os.path.dirname(__file__), "output")):
        output_dir = os.path.join(os.path.dirname(__file__), "output")
    else:
        output_dir = "/scripts/output"
    os.makedirs(output_dir, exist_ok=True)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"delta_backfill_d1_{ts_tag}.log")
    file_handler = logging.FileHandler(output_path, mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(file_handler)
    logger.info("Output file: %s", output_path)

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set. Set it in .env or environment.")
        sys.exit(1)

    # Parse dates
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc)
    else:
        end_date = datetime.now(timezone.utc)

    timeframes = [t.strip() for t in args.timeframes.split(",")]
    n_workers = min(args.workers, 12)

    # Generate batch_id for delta
    if args.new_batch_id:
        batch_id = args.new_batch_id
    else:
        batch_hash = hashlib.sha256(
            f"{datetime.now(timezone.utc).isoformat()}D1_delta_{args.reference_batch_id}".encode()
        ).hexdigest()[:8]
        batch_id = f"delta_{datetime.now(timezone.utc).strftime('%Y%m%d')}_D1v1_{batch_hash}"

    output_start_ms = int(start_date.timestamp() * 1000)

    logger.info("=" * 60)
    logger.info("MCAS D1 Delta Backfill — New Opportunities Only")
    logger.info("=" * 60)
    logger.info(f"  Reference batch: {args.reference_batch_id}")
    logger.info(f"  Delta batch ID:  {batch_id}")
    logger.info(f"  Output window:   {start_date.date()} to {end_date.date()}")
    logger.info(f"  Warmup:          {args.extra_warmup_days} days before start")
    logger.info(f"  Timeframes:      {timeframes}")
    logger.info(f"  Cooldown:        {args.cooldown_hours}h")
    logger.info(f"  Workers:         {n_workers}")
    logger.info(f"  Setup version:   {args.setup_version}")
    logger.info(f"  D1 LOOSE entry config:")
    logger.info(f"    Dormancy threshold:      {D1Config.DORMANCY_THRESHOLD}")
    logger.info(f"    Dormancy range threshold: {D1Config.DORMANCY_RANGE_THRESHOLD}")
    logger.info(f"    BB width compress:        {D1Config.BB_WIDTH_COMPRESS}")
    logger.info(f"    Volume Z spike:           {D1Config.VOLUME_Z_SPIKE}")
    logger.info(f"    RVOL min:                 {D1Config.RVOL_MIN}")
    logger.info("=" * 60)

    # ---- Phase 0: Load reference batch keys ----
    t0 = time.time()
    reference_keys = load_reference_keys(args.reference_batch_id)

    if not reference_keys:
        logger.warning("WARNING: Reference batch returned 0 keys. "
                       "This means ALL signals will be treated as delta (new). "
                       "Verify --reference-batch-id is correct.")
        # Show available batches for debugging
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT batch_id, COUNT(*) as cnt
                    FROM opportunities
                    WHERE setup_id = 'D1' AND status = 'expired'
                      AND batch_id LIKE 'backfill_%%'
                    GROUP BY batch_id
                    ORDER BY batch_id DESC
                    LIMIT 10
                """)
                rows = cur.fetchall()
                if rows:
                    logger.info("Available D1 backfill batches:")
                    for bid, cnt in rows:
                        logger.info(f"  {bid}: {cnt:,} opps")
                else:
                    logger.info("No D1 backfill batches found in DB.")

    logger.info(f"Reference batch size: {len(reference_keys):,} signal keys")

    # ---- Phase 1: Load data ----
    all_data = load_ohlcv_from_db(
        start_date=start_date,
        end_date=end_date,
        extra_warmup_days=args.extra_warmup_days,
        timeframes=timeframes,
    )

    if not all_data:
        logger.error("No OHLCV data found.")
        sys.exit(1)

    # ---- Phase 2: Precompute rolling volume ----
    vol_24h = precompute_rolling_volume(all_data)

    # ---- Phase 3: Load BTC data for beta adjustment ----
    btc_data = load_btc_ohlcv(start_date, end_date, args.extra_warmup_days, timeframes)

    # ---- Phase 4: Build exclusion set ----
    all_symbols = sorted(set(sym for (sym, tf) in all_data.keys()))
    exclusion_set = build_exclusion_set(all_symbols)
    logger.info(f"Exclusion set: {len(exclusion_set)} symbols excluded, "
                f"{len(all_symbols) - len(exclusion_set)} eligible")

    # ---- Phase 5: Process symbols in parallel ----
    symbol_tfs_map = defaultdict(list)
    for (sym, tf) in sorted(all_data.keys()):
        if sym not in exclusion_set:
            symbol_tfs_map[sym].append(tf)
    work_symbols = sorted(symbol_tfs_map.keys())

    # Sort heaviest-first (most bars) so the tail is short units
    bar_counts = {
        sym: sum(len(all_data.get((sym, tf), []))
                 for tf in symbol_tfs_map[sym])
        for sym in work_symbols
    }
    work_symbols_sorted = sorted(work_symbols, key=lambda s: bar_counts.get(s, 0), reverse=True)

    heaviest = max(bar_counts.values()) if bar_counts else 0
    lightest = min(bar_counts.values()) if bar_counts else 0
    logger.info(f"Processing {len(work_symbols_sorted)} symbols x {len(timeframes)} timeframes")
    logger.info(f"  Heaviest: {heaviest:,} bars, Lightest: {lightest:,} bars, "
                f"Ratio: {heaviest/max(lightest, 1):.1f}x")

    # Set module-level globals BEFORE fork
    _ALL_DATA = all_data
    _VOL_24H = vol_24h
    _BTC_DATA = btc_data
    _EXCLUSION_SET = exclusion_set
    _REFERENCE_KEYS = reference_keys
    _WORKER_ARGS = {
        "symbol_tfs": dict(symbol_tfs_map),
        "output_start_ms": output_start_ms,
        "cooldown_hours": args.cooldown_hours,
        "setup_version": args.setup_version,
        "batch_id": batch_id,
    }

    t_process = time.time()
    total_inserted = 0
    total_found = 0
    total_skipped = 0
    total_delta = 0
    total_bars = 0
    completed = 0

    with multiprocessing.Pool(n_workers) as pool:
        for symbol, opps, sym_stats in pool.imap_unordered(
                _worker_process_symbol, work_symbols_sorted):
            completed += 1
            total_bars += sym_stats["bars_processed"]
            total_found += sym_stats["signals"]
            total_skipped += sym_stats["skipped_existing"]
            total_delta += sym_stats["delta_signals"]

            if opps:
                opps.sort(key=lambda o: o["created_ts"])
                n_ins = insert_opportunities(opps)
                total_inserted += n_ins

            elapsed = time.time() - t_process
            eta = (elapsed / completed) * (len(work_symbols_sorted) - completed) if completed > 0 else 0
            if opps or completed % 50 == 0 or completed == len(work_symbols_sorted):
                logger.info(
                    "[%d/%d] %s: %d signals (%d delta, %d skipped) | "
                    "total: %s found, %s delta, %s skipped | %s elapsed | ~%s remaining",
                    completed, len(work_symbols_sorted), symbol,
                    sym_stats["signals"], sym_stats["delta_signals"],
                    sym_stats["skipped_existing"],
                    f"{total_found:,}", f"{total_delta:,}", f"{total_skipped:,}",
                    _format_duration(elapsed), _format_duration(eta),
                )

    # ---- Summary ----
    elapsed_total = time.time() - t0
    logger.info("=" * 60)
    logger.info(f"D1 Delta Backfill complete in {_format_duration(elapsed_total)}")
    logger.info(f"  Reference batch size:     {len(reference_keys):,}")
    logger.info(f"  Total signals (loose):    {total_found:,}")
    logger.info(f"  Signals skipped (in ref): {total_skipped:,}")
    logger.info(f"  Delta signals (new):      {total_delta:,}")
    logger.info(f"  Delta inserted:           {total_inserted:,}")
    logger.info(f"  Bars processed:           {total_bars:,}")
    logger.info(f"  Excluded symbols:         {len(exclusion_set)}")
    logger.info(f"  Delta batch ID:           {batch_id}")
    logger.info(f"  Setup version:            {args.setup_version}")
    logger.info("=" * 60)

    # ---- DB-level dedup + zero-overlap enforcement ----
    # The Python set dedup in workers is the fast path. This is the paranoid
    # DB-level safety net: DELETE any delta rows that also exist in the
    # reference batch (should be zero, but guarantees correctness at scale).
    if total_inserted > 0 and reference_keys:
        logger.info("Running DB-level dedup (paranoid safety net)...")
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # Delete any delta rows whose (symbol, timeframe, created_ts)
                # exists in the reference batch
                cur.execute("""
                    DELETE FROM opportunities d
                    USING opportunities r
                    WHERE d.batch_id = %s
                      AND d.setup_id = 'D1'
                      AND r.batch_id LIKE %s
                      AND r.setup_id = 'D1'
                      AND r.status = 'expired'
                      AND d.symbol = r.symbol
                      AND d.timeframe = r.timeframe
                      AND d.created_ts = r.created_ts
                """, (batch_id, args.reference_batch_id))
                purged = cur.rowcount
            conn.commit()

            if purged == 0:
                logger.info("  PASS: Zero overlap — DB dedup removed 0 rows")
            else:
                logger.warning(f"  PURGED: DB dedup removed {purged:,} duplicate rows "
                               f"that slipped past Python set check")
                total_inserted -= purged

            # Verify: final count in delta batch
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM opportunities WHERE batch_id = %s",
                    (batch_id,),
                )
                final_count = cur.fetchone()[0]
                logger.info(f"  Final delta batch size: {final_count:,}")

    # ---- Day/symbol breakdown ----
    if total_inserted > 0:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DATE(created_ts) as d, COUNT(*) as c
                    FROM opportunities
                    WHERE batch_id = %s
                    GROUP BY d ORDER BY d
                """, (batch_id,))
                day_counts = {str(row[0]): row[1] for row in cur.fetchall()}

                cur.execute("""
                    SELECT symbol, COUNT(*) FROM opportunities
                    WHERE batch_id = %s GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 10
                """, (batch_id,))
                top_syms = cur.fetchall()

        logger.info(f"\n  Delta signals by day:")
        for d, c in sorted(day_counts.items()):
            logger.info(f"    {d}: {c}")
        logger.info(f"\n  Top delta symbols:")
        for sym, cnt in top_syms:
            logger.info(f"    {sym}: {cnt}")
    elif total_found > 0 and total_delta == 0:
        logger.info("All signals from loose thresholds already exist in reference batch. "
                     "The thresholds may not be meaningfully different.")
    else:
        logger.info("No signals found with loose thresholds.")

    logger.info(f"\nOutput written to: {output_path}")


if __name__ == "__main__":
    main()
