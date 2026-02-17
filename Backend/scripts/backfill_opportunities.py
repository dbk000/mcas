#!/usr/bin/env python3
"""
Backfill opportunities from stored OHLCV data using current setup code.

Reads OHLCV from Postgres ohlcv table (populated by backfill_ohlcv.py),
walks forward bar-by-bar computing indicators and evaluating
S1/S2/S3/S5/S6/S7/U1 setups (S4 disabled), and inserts triggered signals
as expired opportunities with setup_version and batch_id for version tracking.

Writes to DB incrementally per-symbol (crash at hour 7 doesn't lose hours 1-6).
Logs per-symbol progress with elapsed time and ETA.

Uses the same evaluator code as production — guaranteed consistency.

Usage:
    python Backend/scripts/backfill_opportunities.py --days 7 --setup-version "S1_v2.2,S2_v1.5"

    # Clear stale expired backfill opps before regenerating:
    python Backend/scripts/backfill_opportunities.py --days 7 --clear-expired

    # After code changes, re-run with new version tag:
    python Backend/scripts/backfill_opportunities.py --days 7 --setup-version "S1_v2.3,S2_v1.6,S3_v1.0"

Requires DATABASE_URL env var (reads from .env if available).
"""

import os
import sys
import json
import time
import hashlib
import logging
import argparse
import dataclasses
import multiprocessing
from enum import Enum
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

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
import numpy as np

from indicators import OHLCV, FeatureFactory, IndicatorSnapshot
from setups.s1_bb_squeeze import S1Evaluator, S1Signal
from setups.s2_golden_cross import S2Evaluator, S2Signal
from setups.s3_rsi_divergence import S3Evaluator, S3Signal
from setups.s5_sr_breakout import S5Evaluator, S5Signal
from setups.s6_trend_pullback import S6Evaluator, S6Signal
from setups.s7_momentum_breakout import S7Evaluator, S7Signal
from setups.s8_mtf_momentum import S8Evaluator, S8Signal
from setups.u1_unicorn_ignition import U1Evaluator, U1Signal

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

# Day-of-week gate: skip bar evaluation on these weekdays.
# Python weekday(): 0=Monday, 6=Sunday. Default "6" = block Sunday.
# Set BLOCKED_WEEKDAYS="" to disable.
# TODO(Phase 3 follow-up): Before live deployment, also add this gate to
# signal_engine.py → evaluate_setups() so live signals match backtest behavior.
BLOCKED_WEEKDAYS = {int(d) for d in os.environ.get("BLOCKED_WEEKDAYS", "6").split(",") if d.strip()}

# ---------------------------------------------------------------------------
# Module-level shared data — populated BEFORE pool creation, inherited via fork COW.
# Children read these dicts but never write to them, so Linux COW keeps memory shared.
# ---------------------------------------------------------------------------
_shared_all_data = {}
_shared_btc_data = {}
_shared_setup_versions = {}
_shared_batch_id = ""
_shared_start_days = 0


def _build_evaluators() -> dict:
    """Create a fresh evaluator dict for all active setups (S4 disabled)."""
    return {
        "S1": S1Evaluator(),
        "S2": S2Evaluator(),
        "S3": S3Evaluator(),
        "S5": S5Evaluator(),
        "S6": S6Evaluator(),
        "S7": S7Evaluator(),
        "S8": S8Evaluator(),
        "U1": U1Evaluator(),
    }


def _process_symbol_worker(symbol_tfs):
    """Worker: process all TFs for one symbol. Fresh evaluators per call."""
    symbol, tfs = symbol_tfs
    factory = FeatureFactory()
    evaluators = _build_evaluators()
    results = []
    for tf in tfs:
        candles = _shared_all_data.get((symbol, tf), [])
        htf_tf = HTF_MAP.get(tf)
        htf_candles = _shared_all_data.get((symbol, htf_tf), []) if htf_tf else []
        btc_candles = _shared_btc_data.get(tf, [])
        opps = process_symbol_timeframe(
            symbol, tf, candles, htf_candles, btc_candles,
            _shared_setup_versions, _shared_batch_id,
            factory, evaluators, _shared_start_days,
        )
        results.extend(opps)
    return symbol, results


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


def _serialize_value(val):
    """Recursively serialize dataclass/enum/datetime values to JSON-safe types."""
    if val is None:
        return None
    if isinstance(val, bool):       # bool before int (bool is subclass of int)
        return val
    if isinstance(val, (int, float)):
        if val != val:  # NaN check
            return None
        return val
    if isinstance(val, str):
        return val
    if isinstance(val, Enum):       # Direction.LONG → "long"
        return val.value
    if isinstance(val, datetime):
        return val.isoformat()
    if dataclasses.is_dataclass(val):
        return {f.name: _serialize_value(getattr(val, f.name))
                for f in dataclasses.fields(val)}
    if isinstance(val, list):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _serialize_value(v) for k, v in val.items()}
    return str(val)


# Fields that are part of the base Signal contract (not score components)
_BASE_SIGNAL_FIELDS = {
    "symbol", "timeframe", "direction", "setup_score",
    "entry_price", "stop_price", "tp1_price", "tp2_price",
    "r_value", "state", "explain", "time_stop_bars",
    "since_ts", "action_by_ts",
}


def build_signal_payload(signal, setup_id: str, symbol: str, tf: str,
                         bar_ts_ms: int, snapshot=None) -> dict:
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

    # --- Score components (setup-specific fields beyond the base Signal contract) ---
    if dataclasses.is_dataclass(signal):
        score_components = {}
        for field in dataclasses.fields(signal):
            if field.name in _BASE_SIGNAL_FIELDS:
                continue
            val = getattr(signal, field.name)
            serialized = _serialize_value(val)
            if serialized is not None:
                score_components[field.name] = serialized
        if score_components:
            payload["score_components"] = score_components

    # --- State snapshot (Tier 3 state at signal time) ---
    if hasattr(signal, "state") and signal.state is not None:
        state_dict = _serialize_value(signal.state)
        if isinstance(state_dict, dict):
            payload["state_snapshot"] = state_dict

    # --- Indicator values from IndicatorSnapshot ---
    if snapshot is not None and dataclasses.is_dataclass(snapshot):
        indicator_values = {}
        for field in dataclasses.fields(snapshot):
            if field.name == "ohlcv":  # Skip raw candle list
                continue
            val = getattr(snapshot, field.name)
            serialized = _serialize_value(val)
            if serialized is not None:
                indicator_values[field.name] = serialized
        if indicator_values:
            payload["indicator_values"] = indicator_values

    return payload


def _make_opportunity(signal, setup_id: str, symbol: str, tf: str,
                      bar_ts_ms: int, setup_versions: dict, batch_id: str,
                      snapshot=None) -> dict:
    """Build an opportunity dict from a signal."""
    bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)  # Binance ms epoch
    reason = "; ".join(signal.explain.why_items) if signal.explain else None
    payload = build_signal_payload(signal, setup_id, symbol, tf, bar_ts_ms, snapshot)

    # Default version per setup
    default_versions = {
        "S1": "S1_v2.1", "S2": "S2_v1.4", "S3": "S3_v1.0",
        "S5": "S5_v1.0", "S6": "S6_v1.0", "S7": "S7_v1.0",
        "S8": "S8_v1.0",
        "U1": "U1_v1.0",
    }

    return {
        "created_ts": bar_dt,
        "symbol": symbol,
        "timeframe": tf,
        "setup_id": setup_id,
        "decision": "SIGNAL",
        "score": signal.setup_score,
        "reason": reason,
        "payload": payload,
        "setup_version": setup_versions.get(setup_id, default_versions.get(setup_id, f"{setup_id}_v1.0")),
        "batch_id": batch_id,
    }


def process_symbol_timeframe(
    symbol: str,
    tf: str,
    candles: list[OHLCV],
    htf_candles: list[OHLCV],
    btc_candles: list[OHLCV],
    setup_versions: dict[str, str],
    batch_id: str,
    factory: FeatureFactory,
    evaluators: dict,
    start_days: int,
) -> list[dict]:
    """Walk forward through candles, evaluating all active setups at each bar.

    Returns list of opportunity dicts for triggered signals.
    """
    if len(candles) < MIN_BARS:
        return []

    opportunities = []

    # State tracking
    s1_prev_state = None
    s2_prev_state = None
    s6_prev_state = None   # Tracked per signal_engine.py line 544-546 pattern
    prev_snapshot = None    # S7: handles None gracefully — falls back to candles[-21:-1]
    s8_prev_state = None   # S8: same pattern as S6
    u1_prev_state = None   # U1: gate state machine (EMBRYO → EMBRYO_ACTIVE)
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

    # Calculate the cutoff timestamp for the --days window
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - (start_days * 86400 * 1000)

    # Get evaluator refs (may be None if setup not in dict)
    s1_eval = evaluators.get("S1")
    s2_eval = evaluators.get("S2")
    s3_eval = evaluators.get("S3")
    s5_eval = evaluators.get("S5")
    s6_eval = evaluators.get("S6")
    s7_eval = evaluators.get("S7")
    s8_eval = evaluators.get("S8")
    u1_eval = evaluators.get("U1")

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

        # --- Warmup phase: compute stateful setup state but don't evaluate triggers ---
        if current_bar.timestamp < cutoff_ms:
            if s1_eval:
                s1_state = s1_eval.compute_state(
                    snapshot, bb_width_history, s1_prev_state,
                )
                s1_prev_state = s1_state

            if s2_eval:
                s2_state = s2_eval.compute_state(
                    snapshot, ma_gap_history, s2_prev_state,
                )
                s2_prev_state = s2_state

            if s6_eval:
                s6_prev_state = s6_eval.compute_state(snapshot, s6_prev_state)

            if s8_eval:
                s8_prev_state = s8_eval.compute_state(snapshot, s8_prev_state)

            # S7: stash snapshot for next bar's prev_snapshot
            prev_snapshot = snapshot

            # U1: evaluate gates during warmup to build state machine
            if u1_eval:
                u1_prev_state = u1_eval.evaluate_gates(window, u1_prev_state)

            # S3, S5: stateless — no warmup needed
            continue

        # --- Evaluation phase ---

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

        bar_ts_ms = current_bar.timestamp

        # Day-of-week gate: skip entire bar on blocked days (e.g., Sunday)
        if BLOCKED_WEEKDAYS:
            bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)  # Binance ms epoch
            if bar_dt.weekday() in BLOCKED_WEEKDAYS:
                prev_snapshot = snapshot  # preserve S7 state continuity
                continue

        # --- S1 Evaluation ---
        if s1_eval:
            s1_state = s1_eval.compute_state(
                snapshot, bb_width_history, s1_prev_state,
                btc_snapshot, btc_bb_hist,
            )
            s1_prev_state = s1_state

            s1_signal = s1_eval.evaluate_trigger(snapshot, s1_state, symbol, tf)
            if s1_signal:
                opportunities.append(_make_opportunity(
                    s1_signal, "S1", symbol, tf, bar_ts_ms, setup_versions, batch_id, snapshot,
                ))

        # --- S2 Evaluation ---
        if s2_eval:
            s2_state = s2_eval.compute_state(
                snapshot, ma_gap_history, s2_prev_state, btc_snapshot,
            )
            s2_prev_state = s2_state

            s2_signal = s2_eval.evaluate_trigger(snapshot, s2_state, symbol, tf)
            if s2_signal:
                opportunities.append(_make_opportunity(
                    s2_signal, "S2", symbol, tf, bar_ts_ms, setup_versions, batch_id, snapshot,
                ))

        # --- S3 Evaluation (RSI Divergence) ---
        if s3_eval:
            # S3 needs RSI series — same pattern as signal_engine.py line 451-452
            rsi_series = factory._rsi_series(
                np.array([c.close for c in window]), 14
            )
            s3_state = s3_eval.compute_state(snapshot, rsi_series)
            s3_signal = s3_eval.evaluate_trigger(snapshot, s3_state, symbol, tf)
            if s3_signal:
                opportunities.append(_make_opportunity(
                    s3_signal, "S3", symbol, tf, bar_ts_ms, setup_versions, batch_id, snapshot,
                ))

        # --- S5 Evaluation (S/R Breakout) ---
        if s5_eval:
            s5_state = s5_eval.compute_state(snapshot)
            s5_signal = s5_eval.evaluate_trigger(snapshot, s5_state, symbol, tf)
            if s5_signal:
                opportunities.append(_make_opportunity(
                    s5_signal, "S5", symbol, tf, bar_ts_ms, setup_versions, batch_id, snapshot,
                ))

        # --- S6 Evaluation (Trend Continuation Pullback) ---
        if s6_eval:
            s6_state = s6_eval.compute_state(snapshot, s6_prev_state)
            s6_prev_state = s6_state
            s6_signal = s6_eval.evaluate_trigger(snapshot, s6_state, symbol, tf)
            if s6_signal:
                opportunities.append(_make_opportunity(
                    s6_signal, "S6", symbol, tf, bar_ts_ms, setup_versions, batch_id, snapshot,
                ))

        # --- S7 Evaluation (Momentum Breakout / Donchian) ---
        if s7_eval:
            s7_state = s7_eval.compute_state(snapshot, prev_snapshot)
            s7_signal = s7_eval.evaluate_trigger(snapshot, s7_state, symbol, tf)
            if s7_signal:
                opportunities.append(_make_opportunity(
                    s7_signal, "S7", symbol, tf, bar_ts_ms, setup_versions, batch_id, snapshot,
                ))

        # --- S8 Evaluation (MTF Momentum Pullback) ---
        if s8_eval:
            s8_state = s8_eval.compute_state(snapshot, s8_prev_state)
            s8_prev_state = s8_state
            s8_signal = s8_eval.evaluate_trigger(snapshot, s8_state, symbol, tf)
            if s8_signal:
                opportunities.append(_make_opportunity(
                    s8_signal, "S8", symbol, tf, bar_ts_ms, setup_versions, batch_id, snapshot,
                ))

        # --- U1 Evaluation (Unicorn Ignition) ---
        # U1 uses raw candles, not IndicatorSnapshot. Gate eval builds state,
        # trigger eval only fires for EMBRYO_ACTIVE phase.
        if u1_eval:
            u1_prev_state = u1_eval.evaluate_gates(window, u1_prev_state)
            u1_signal = u1_eval.evaluate_trigger(window, u1_prev_state, symbol, tf)
            if u1_signal:
                opportunities.append(_make_opportunity(
                    u1_signal, "U1", symbol, tf, bar_ts_ms, setup_versions, batch_id, snapshot,
                ))

        # Stash current snapshot for S7's next-bar prev_snapshot
        prev_snapshot = snapshot

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
        help="Comma-separated setup versions (e.g. 'S1_v2.2,S2_v1.5,S3_v1.0'). "
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
    parser.add_argument(
        "--workers", type=int, default=6,
        help="Parallel workers for ProcessPool (default: 6, use 1 for sequential)"
    )
    parser.add_argument(
        "--clear-expired", action="store_true",
        help="Delete stale backfill-generated expired opportunities before inserting new ones. "
             "Scoped to matching batch_id prefix, setup_ids, timeframes, and --days window."
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set. Set it in .env or environment.")
        sys.exit(1)

    # Valid setup IDs (those with evaluators — S4 disabled)
    valid_setup_ids = {"S1", "S2", "S3", "S5", "S6", "S7", "S8", "U1"}

    # Parse setup versions
    setup_versions = {
        "S1": "S1_v2.1", "S2": "S2_v1.4", "S3": "S3_v1.0",
        "S5": "S5_v1.0", "S6": "S6_v1.0", "S7": "S7_v1.0",
        "S8": "S8_v1.0",
        "U1": "U1_v1.0",
    }
    if args.setup_version:
        for sv in args.setup_version.split(","):
            sv = sv.strip()
            prefix = sv.split("_")[0]
            if prefix not in valid_setup_ids:
                logger.error(
                    "Unknown setup version prefix '%s' from '%s' — valid prefixes: %s",
                    prefix, sv, sorted(valid_setup_ids),
                )
                sys.exit(1)
            setup_versions[prefix] = sv

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
    logger.info(f"  Clear expired:  {args.clear_expired}")
    logger.info(f"  Active setups:  {sorted(valid_setup_ids)}")
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

    # --- Clear stale expired backfill opportunities if requested ---
    if args.clear_expired:
        setup_ids_list = sorted(valid_setup_ids)
        tf_list = sorted(tfs_seen) if not tf_filter else tf_filter
        logger.info("=" * 60)
        logger.info("--clear-expired: Deleting stale backfill opportunities")
        logger.info(f"  Scope: status='expired' AND batch_id LIKE 'backfill_%%'")
        logger.info(f"  Setup IDs:  {setup_ids_list}")
        logger.info(f"  Timeframes: {tf_list}")
        logger.info(f"  Window:     last {args.days} days")

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM opportunities
                    WHERE status = 'expired'
                      AND batch_id LIKE 'backfill_%%'
                      AND setup_id = ANY(%(setup_ids)s)
                      AND timeframe = ANY(%(timeframes)s)
                      AND created_ts >= NOW() - %(days)s * INTERVAL '1 day'
                """, {
                    "setup_ids": setup_ids_list,
                    "timeframes": tf_list,
                    "days": args.days,
                })
                deleted_count = cur.rowcount
            conn.commit()
        logger.info(f"  Deleted {deleted_count:,} stale expired opportunities")
        logger.info("=" * 60)

    # Preload BTC data for each timeframe (needed for correlation filter)
    btc_data = {}
    for tf in tfs_seen:
        key = ("BTCUSDT", tf)
        if key in all_data:
            btc_data[tf] = all_data[key]
        else:
            btc_data[tf] = []

    # Group combos by symbol — each worker handles all TFs for its symbols
    symbol_tfs_map = defaultdict(list)
    for (sym, tf) in sorted(all_data.keys()):
        symbol_tfs_map[sym].append(tf)
    work_items = list(symbol_tfs_map.items())  # [(symbol, [tfs]), ...]

    n_workers = args.workers

    t0 = time.time()

    # Running counters for incremental tracking
    total_inserted = 0
    total_found = 0
    by_setup = defaultdict(int)
    by_tf = defaultdict(int)

    if n_workers <= 1:
        # Sequential path (for debugging / correctness verification)
        logger.info(f"Running sequentially (--workers {n_workers})")
        factory = FeatureFactory()
        evaluators = _build_evaluators()
        processed = 0

        for symbol, tfs in work_items:
            processed += 1
            symbol_opps = []
            for tf in tfs:
                candles = all_data.get((symbol, tf), [])
                htf_tf = HTF_MAP.get(tf)
                htf_candles = all_data.get((symbol, htf_tf), []) if htf_tf else []
                btc_candles = btc_data.get(tf, [])

                opps = process_symbol_timeframe(
                    symbol=symbol, tf=tf, candles=candles,
                    htf_candles=htf_candles, btc_candles=btc_candles,
                    setup_versions=setup_versions, batch_id=batch_id,
                    factory=factory, evaluators=evaluators,
                    start_days=args.days,
                )
                symbol_opps.extend(opps)

            # Incremental write: insert this symbol's opps immediately
            if symbol_opps:
                symbol_opps.sort(key=lambda o: (o["timeframe"], o["created_ts"]))
                n_ins = insert_opportunities(symbol_opps)
                total_inserted += n_ins
                total_found += len(symbol_opps)
                for opp in symbol_opps:
                    by_setup[opp["setup_id"]] += 1
                    by_tf[opp["timeframe"]] += 1

            # Per-symbol progress log
            elapsed = time.time() - t0
            eta = (elapsed / processed) * (len(work_items) - processed) if processed > 0 else 0
            sym_setup_counts = defaultdict(int)
            for opp in symbol_opps:
                sym_setup_counts[opp["setup_id"]] += 1
            setup_breakdown = " ".join(
                f"{count} {sid}" for sid, count in sorted(sym_setup_counts.items())
            ) if sym_setup_counts else "0"
            logger.info(
                "[%d/%d] %s: %d signals (%s) | %s inserted | %s elapsed | ~%s remaining",
                processed, len(work_items), symbol, len(symbol_opps),
                setup_breakdown, f"{total_inserted:,}",
                _format_duration(elapsed), _format_duration(eta),
            )
    else:
        # Parallel path — ProcessPoolExecutor with fork COW
        logger.info(f"Running with {n_workers} parallel workers (fork COW)")

        # Populate module-level globals BEFORE pool creation — fork COW inherits them
        global _shared_all_data, _shared_btc_data, _shared_setup_versions
        global _shared_batch_id, _shared_start_days
        _shared_all_data = all_data
        _shared_btc_data = btc_data
        _shared_setup_versions = setup_versions
        _shared_batch_id = batch_id
        _shared_start_days = args.days

        # Enforce fork start method (Linux) — abort if unavailable
        try:
            mp_ctx = multiprocessing.get_context("fork")
        except ValueError:
            logger.error("ProcessPool requires fork start method (Linux). "
                         "Falling back to sequential with --workers 1.")
            sys.exit(1)

        failed_count = 0
        fail_threshold = max(1, int(len(work_items) * 0.10))  # 10% failure = abort
        processed = 0

        # No initializer/initargs — zero pickling overhead
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=mp_ctx) as pool:
            futures = {
                pool.submit(_process_symbol_worker, item): item[0]
                for item in work_items
            }
            for future in as_completed(futures):
                symbol = futures[future]
                processed += 1
                try:
                    sym, opps = future.result()

                    # Incremental write: insert immediately on completion
                    if opps:
                        opps.sort(key=lambda o: (o["timeframe"], o["created_ts"]))
                        n_ins = insert_opportunities(opps)
                        total_inserted += n_ins
                        total_found += len(opps)
                        for opp in opps:
                            by_setup[opp["setup_id"]] += 1
                            by_tf[opp["timeframe"]] += 1

                    # Per-symbol progress log
                    elapsed = time.time() - t0
                    eta = (elapsed / processed) * (len(work_items) - processed) if processed > 0 else 0
                    sym_setup_counts = defaultdict(int)
                    for opp in opps:
                        sym_setup_counts[opp["setup_id"]] += 1
                    setup_breakdown = " ".join(
                        f"{count} {sid}" for sid, count in sorted(sym_setup_counts.items())
                    ) if sym_setup_counts else "0"
                    logger.info(
                        "[%d/%d] %s: %d signals (%s) | %s inserted | %s elapsed | ~%s remaining",
                        processed, len(work_items), sym, len(opps),
                        setup_breakdown, f"{total_inserted:,}",
                        _format_duration(elapsed), _format_duration(eta),
                    )
                except Exception as e:
                    failed_count += 1
                    logger.warning(f"Failed {symbol}: {e}")
                    if failed_count >= fail_threshold:
                        logger.error(
                            f"Aborting: {failed_count} failures "
                            f"(>10% of {len(work_items)} symbols)"
                        )
                        pool.shutdown(wait=False, cancel_futures=True)
                        sys.exit(1)

    # Summary
    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info(f"Backfill complete in {_format_duration(elapsed)}")
    logger.info(f"  Signals found:        {total_found:,}")
    logger.info(f"  Opportunities stored: {total_inserted:,}")
    logger.info(f"  Batch ID:             {batch_id}")
    logger.info(f"  Setup versions:       {setup_versions}")
    logger.info(f"\n  By setup:")
    for setup_id, count in sorted(by_setup.items()):
        logger.info(f"    {setup_id}: {count:,}")
    logger.info(f"  By timeframe:")
    for tf, count in sorted(by_tf.items()):
        logger.info(f"    {tf}: {count:,}")
    logger.info("=" * 60)
    logger.info(f"\nTo simulate these opportunities:")
    logger.info(f'  POST /v1/control/runs/simulation')
    logger.info(f'  {{"batch_id": "{batch_id}"}}')


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration (e.g. '2h13m45s')."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


if __name__ == "__main__":
    main()
