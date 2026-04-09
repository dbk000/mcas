#!/usr/bin/env python3
"""
Unified Opportunity Regeneration Under Frozen Configs
=====================================================

Regenerates all opportunities for D1/D2/U1/S9/S10 from Jan 1 2026 to present
using the strategy_config_freeze_20260407.csv config snapshot.

Three execution modes:
  Mode 1 (per-symbol parallel): D1, U1, S10
  Mode 2 (cross-sectional serial): S9
  Mode 3 (event-driven): D2

Usage (run inside Docker worker container via tmux):
    tmux new -s regen

    # D1 + U1 + S10 (per-symbol parallel, 12 workers)
    python3 /scripts/regenerate_opportunities.py \\
        --setup-ids D1,U1,S10 \\
        --start-date 2026-01-01 --end-date 2026-04-07 \\
        --workers 12 --batch-id regen_frozen_20260407

    # S9 (cross-sectional, single-threaded)
    python3 /scripts/regenerate_opportunities.py \\
        --setup-ids S9 \\
        --start-date 2026-01-01 --end-date 2026-04-07 \\
        --batch-id regen_frozen_20260407

    # D2 (event-driven, single-threaded)
    python3 /scripts/regenerate_opportunities.py \\
        --setup-ids D2 \\
        --start-date 2026-01-01 --end-date 2026-04-07 \\
        --batch-id regen_frozen_20260407

Preflight checklist compliance:
  1. Progress logging: per-symbol + periodic summaries
  2. Incremental writes: batch of 500 via executemany, per-symbol commit
  3. Warmup: OHLCV loaded with extra days (see WARMUP_DAYS)
  4. Execution context: detects Docker vs host
  5. Detached: run in tmux (instructions above)
  6. Plan approval: user reviews plan before execution
  7. Persistent output: writes summary to scripts/output/
  8. Parallelism: Mode 1 uses Pool(N), Modes 2/3 are serial (justified below)

Parallelism answers (Canon §0.3):
  a) D1/U1/S10: per-symbol FeatureFactory + evaluator is CPU-bound; 12-worker Pool.
     S9: cross-sectional (all symbols per bar) — cannot parallelise by symbol.
     D2: ~100 clusters — trivial workload, parallelism overhead > benefit.
  b) D1/U1/S10: >95% of wall time is in the Pool. S9/D2 are serial but small.
  c) D1/U1/S10 sorted heaviest-first; ratio logged at startup.
  d) imap_unordered feeds new work to idle workers continuously.
  e) D1: ~80% flat for main phase, tapering in last 5%. S9: ~20% (serial). D2: <1%.
"""

import argparse
import bisect
import json
import logging
import multiprocessing
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import psycopg

# ---------------------------------------------------------------------------
# Execution context detection (Docker vs host)
# ---------------------------------------------------------------------------
if os.path.isdir("/app"):
    sys.path.insert(0, "/backend")
    OUTPUT_DIR = Path("/scripts/output")
else:
    _SCRIPT_DIR = Path(__file__).resolve().parent
    _BACKEND_DIR = _SCRIPT_DIR.parent
    sys.path.insert(0, str(_BACKEND_DIR))
    OUTPUT_DIR = _SCRIPT_DIR / "output"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Monkey-patch _should_compute_dark BEFORE importing FeatureFactory.
# The real function calls governance.bootstrap.get_config() which does
# sys.exit(78) if ConfigRegistry isn't bootstrapped. We don't need dark
# indicators for the regen — just disable them.
import indicators.indicators as _ind_mod
_ind_mod._should_compute_dark = lambda: False

from indicators.indicators import FeatureFactory, OHLCV, IndicatorSnapshot
from core_logic.d1_dormancy_ignition import D1Config, D1Evaluator, D1State, is_d1_excluded
from core_logic.u1_unicorn_ignition import U1Config, U1Evaluator, U1State
from core_logic.s9_short_reversion import S9Evaluator, S9State
from core_logic.s10_macd_scalp import S10Evaluator, S10State
from core_logic.s11_macd_long_reversion import S11Evaluator, S11State
from core_logic.d2_fade_the_casino import (
    D2Config, cluster_from_alerts, check_gates, evaluate_cluster, get_pre_pump_price,
)
from core_logic.d2_signal_parser import parse_whalebotpumps

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("regen")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BATCH_SIZE = 500
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://trading:trading@postgres:5432/trading",
)

# Warmup days per strategy (extra days before start_date for indicator computation)
WARMUP_DAYS = {
    "D1": 12,    # FeatureFactory needs 201 bars of 1h = ~8.4 days, round up to 12
    "U1": 10,    # BASELINE_WINDOWS max 7d + gate windows
    "S9": 2,     # 24h breadth + 4h return lookback
    "S10": 2,    # FeatureFactory needs 201 bars of 5m = ~17h, round up to 2 days
    "S11": 12,   # SMA200 on 1h = ~8.4 days, round up to 12 (same as D1)
    "D2": 3,     # Pre-pump price needs 1h bar before cluster
}

STRATEGY_TF = {
    "D1": "1h",
    "U1": "5m",
    "S9": "1h",
    "S10": "5m",
    "S11": "1h",
    "D2": "1h",
}

SETUP_VERSIONS = {
    "D1": "D1_v1.0_frozen_20260407",
    "U1": "U1_v4.0_frozen_20260407",
    "S9": "S9_v1.0_frozen_20260407",
    "S10": "S10_v1.0_frozen_20260407",
    "S11": "S11_v1.0_locked_20260409",
    "D2": "D2_v1.0_frozen_20260407",
}

# ---------------------------------------------------------------------------
# COW globals (set before Pool, read-only in workers via fork)
# ---------------------------------------------------------------------------
_OHLCV_DATA = {}          # (symbol, tf) -> list of (ts_ms, o, h, l, c, v, qv)
_BTC_TS_SORTED = []        # sorted list of BTC bar timestamps (ms)
_BTC_RETURNS_BY_TS = {}    # {ts_ms: float} — BTC bar-over-bar return at that bar
_SOCIAL_DAILY = {}         # {symbol: {iso_date_str: mention_count}} for D1 social silence
_VOL_Z_1D = {}             # {symbol: {iso_date_str: float}} — daily volume z-scores for U1
_ARGS = {}
_START_TS = None
_END_TS = None
_DRY_RUN = False


# ═══════════════════════════════════════════════════════════════════════════
# DB Utilities
# ═══════════════════════════════════════════════════════════════════════════

def get_conn():
    return psycopg.connect(DATABASE_URL)


def load_ohlcv_bulk(timeframe: str, start_date: datetime, symbols: list[str] = None) -> dict:
    """Load OHLCV from Postgres. Returns {symbol: [(ts_ms, o, h, l, c, v, qv), ...]}."""
    logger.info("Loading %s OHLCV from %s%s...",
                timeframe, start_date.date(),
                f" for {len(symbols)} symbols" if symbols else " (all)")

    data = defaultdict(list)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if symbols:
                cur.execute("""
                    SELECT symbol, bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE timeframe = %s AND bar_start_ts >= %s
                      AND is_final = TRUE AND symbol = ANY(%s)
                    ORDER BY symbol, bar_start_ts
                """, (timeframe, start_date, symbols))
            else:
                cur.execute("""
                    SELECT symbol, bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE timeframe = %s AND bar_start_ts >= %s AND is_final = TRUE
                    ORDER BY symbol, bar_start_ts
                """, (timeframe, start_date))

            for row in cur:
                sym = row[0]
                ts = row[1]
                ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, 'timestamp') else int(ts)
                data[sym].append((ts_ms, float(row[2]), float(row[3]),
                                  float(row[4]), float(row[5]),
                                  float(row[6]), 0.0))

    total_bars = sum(len(v) for v in data.values())
    logger.info("Loaded %s OHLCV: %d symbols, %s total bars",
                timeframe, len(data), f"{total_bars:,}")
    return dict(data)


def load_btc_returns_indexed(start_date: datetime) -> tuple[list, dict]:
    """Load BTC 1h returns indexed by timestamp.

    Returns (sorted_ts_list, {ts_ms: return_value}).
    The return at ts_ms is (close[ts] - close[ts-1]) / close[ts-1].
    """
    data = load_ohlcv_bulk("1h", start_date, symbols=["BTCUSDT"])
    btc_bars = data.get("BTCUSDT", [])
    if len(btc_bars) < 2:
        logger.warning("No BTC data — btc_adj_ret scoring will be neutral")
        return [], {}

    by_ts = {}
    sorted_ts = []
    for i in range(1, len(btc_bars)):
        prev_close = btc_bars[i - 1][4]
        cur_close = btc_bars[i][4]
        ts_ms = btc_bars[i][0]
        if prev_close > 0:
            by_ts[ts_ms] = (cur_close - prev_close) / prev_close
            sorted_ts.append(ts_ms)

    logger.info("BTC returns: %d bars indexed by timestamp", len(sorted_ts))
    return sorted_ts, by_ts


def get_btc_returns_for_bar(bar_ts_ms: int, n: int = 20) -> np.ndarray:
    """Get the N most recent BTC returns ending at or before bar_ts_ms.

    Uses _BTC_TS_SORTED and _BTC_RETURNS_BY_TS COW globals.
    Returns numpy array of length <= n.
    """
    if not _BTC_TS_SORTED:
        return np.array([])
    # bisect_right: index of first element > bar_ts_ms
    pos = bisect.bisect_right(_BTC_TS_SORTED, bar_ts_ms)
    start = max(0, pos - n)
    ts_slice = _BTC_TS_SORTED[start:pos]
    return np.array([_BTC_RETURNS_BY_TS[t] for t in ts_slice])


def load_social_daily(start_date: datetime) -> dict:
    """Load social_features_1h aggregated per symbol per day.

    Returns {symbol: {iso_date: total_mention_count}}.
    Used for D1 social silence scoring.
    """
    logger.info("Loading social_features_1h for D1 social silence...")
    result = defaultdict(lambda: defaultdict(int))
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT symbol, bar_start_ts::date AS day, SUM(mention_count)
                    FROM social_features_1h
                    WHERE bar_start_ts >= %s
                    GROUP BY symbol, day
                """, (start_date - timedelta(days=D1Config.SOCIAL_SILENCE_LOOKBACK_DAYS),))
                count = 0
                for row in cur:
                    sym, day, mentions = row[0], row[1], int(row[2])
                    result[sym][day.isoformat()] = mentions
                    count += 1
        logger.info("Social data: %d symbol-day entries for %d symbols",
                    count, len(result))
    except Exception as e:
        logger.warning("social_features_1h query failed: %s — social silence will be neutral", e)
    return dict(result)


def get_social_mention_count(symbol: str, bar_ts: datetime,
                             lookback_days: int = None) -> int:
    """Compute social mention count for D1 from _SOCIAL_DAILY COW global.

    Returns -1 if no social data exists for this symbol (neutral score).
    """
    if not _SOCIAL_DAILY:
        return -1
    if lookback_days is None:
        lookback_days = D1Config.SOCIAL_SILENCE_LOOKBACK_DAYS

    sym_data = _SOCIAL_DAILY.get(symbol)
    if not sym_data:
        return -1  # No social data for this symbol — neutral

    total = 0
    has_any_data = False
    for d in range(lookback_days):
        day = (bar_ts - timedelta(days=d)).strftime("%Y-%m-%d")
        if day in sym_data:
            total += sym_data[day]
            has_any_data = True

    return total if has_any_data else -1


def load_volume_z_1d(start_date: datetime) -> dict:
    """Load 1d OHLCV and compute rolling volume z-score per symbol per day.

    Returns {symbol: {iso_date: volume_z_1d}}.
    Used for U1 v4.0 daily volume regime hard gate.
    """
    logger.info("Loading 1d OHLCV for U1 volume_z_1d computation...")
    # Need extra lookback for the rolling z-score window (20 days)
    data = load_ohlcv_bulk("1d", start_date - timedelta(days=30))

    result = {}
    n_window = 20
    for sym, bars in data.items():
        if len(bars) < n_window + 1:
            continue
        vols = np.array([b[5] for b in bars])
        sym_z = {}
        for i in range(n_window, len(bars)):
            window = vols[i - n_window:i]
            mean_v = window.mean()
            std_v = window.std()
            if std_v > 0:
                z = (vols[i] - mean_v) / std_v
            else:
                z = 0.0
            bar_date = datetime.fromtimestamp(bars[i][0] / 1000, tz=timezone.utc)
            sym_z[bar_date.strftime("%Y-%m-%d")] = float(z)
        result[sym] = sym_z

    logger.info("volume_z_1d: computed for %d symbols", len(result))
    return result


def get_volume_z_1d(symbol: str, bar_ts: datetime) -> float:
    """Look up volume_z_1d for a symbol at a given bar timestamp.

    Returns the z-score for the most recent complete day, or None if unavailable.
    """
    sym_data = _VOL_Z_1D.get(symbol)
    if not sym_data:
        return None
    # Use the date of the bar (for 5m bars, this is the calendar day)
    date_key = bar_ts.strftime("%Y-%m-%d")
    z = sym_data.get(date_key)
    if z is not None:
        return z
    # Try previous day (bar might be early morning, daily not yet complete)
    prev_key = (bar_ts - timedelta(days=1)).strftime("%Y-%m-%d")
    return sym_data.get(prev_key)


def write_opportunities(opps: list[dict], conn=None):
    """Write opportunities to DB in batches of 500 via executemany."""
    if not opps or _DRY_RUN:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    values = [
        (
            o["created_ts"], o["symbol"], o["timeframe"], o["setup_id"],
            o["decision"], o["score"], o.get("reason", ""),
            json.dumps(o["payload"]), "expired",
            o["setup_version"], o["batch_id"],
        )
        for o in opps
    ]

    try:
        with conn.cursor() as cur:
            for i in range(0, len(values), BATCH_SIZE):
                batch = values[i:i + BATCH_SIZE]
                cur.executemany("""
                    INSERT INTO opportunities
                        (created_ts, symbol, timeframe, setup_id, decision,
                         score, reason, payload_json, status,
                         setup_version, batch_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, batch)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Mode 1: Per-Symbol Parallel (D1, U1, S10)
# ═══════════════════════════════════════════════════════════════════════════

def _make_ohlcv_objects(raw_bars: list[tuple]) -> list[OHLCV]:
    """Convert raw (ts_ms, o, h, l, c, v, qv) tuples to OHLCV objects."""
    return [
        OHLCV(timestamp=b[0], open=b[1], high=b[2], low=b[3],
              close=b[4], volume=b[5], quote_volume=b[6])
        for b in raw_bars
    ]


# --- D1 Worker ---

def _worker_d1(symbol: str) -> list[dict]:
    """Walk D1 evaluator over 1h bars for one symbol. Runs in Pool worker."""
    raw_bars = _OHLCV_DATA.get((symbol, "1h"))
    if not raw_bars or len(raw_bars) < 201:
        return []

    candles = _make_ohlcv_objects(raw_bars)
    evaluator = D1Evaluator()
    ff = FeatureFactory()
    state = D1State()
    opps = []

    start_ms = int(_START_TS.timestamp() * 1000)
    end_ms = int(_END_TS.timestamp() * 1000)

    for i in range(201, len(candles)):
        bar_ts_ms = candles[i].timestamp
        if bar_ts_ms < start_ms:
            # Still in warmup — advance state machine but don't emit opps
            window = candles[max(0, i - 250):i + 1]
            if len(window) >= 201:
                try:
                    snapshot = ff.compute(window)
                    btc_rets = get_btc_returns_for_bar(bar_ts_ms)
                    state = evaluator.compute_state(
                        snapshot, prev_state=state, btc_returns=btc_rets,
                    )
                except Exception:
                    pass
            continue
        if bar_ts_ms > end_ms:
            break

        window = candles[max(0, i - 250):i + 1]
        if len(window) < 201:
            continue

        try:
            snapshot = ff.compute(window)
        except Exception:
            continue

        # BTC returns sliced to current bar position
        btc_rets = get_btc_returns_for_bar(bar_ts_ms)

        state = evaluator.compute_state(
            snapshot, prev_state=state, btc_returns=btc_rets,
        )

        # Set social silence before evaluate_trigger (affects score, not gating)
        bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)
        state.social_mention_count = get_social_mention_count(symbol, bar_dt)

        signal = evaluator.evaluate_trigger(snapshot, state, symbol, "1h")

        if signal:
            opps.append({
                "created_ts": bar_dt,
                "symbol": symbol,
                "timeframe": "1h",
                "setup_id": "D1",
                "decision": "SIGNAL",
                "score": signal.setup_score,
                "reason": "D1 dormancy-to-ignition breakout",
                "payload": {
                    "entry_price": signal.entry_price,
                    "stop_price": signal.stop_price,
                    "tp1_price": signal.tp1_price,
                    "tp2_price": signal.tp2_price,
                    "r_value": signal.r_value,
                    "direction": "long",
                    "time_stop_bars": signal.time_stop_bars,
                    "score_components": signal.score_components,
                    "dormancy_score": signal.dormancy_score,
                    "bb_width": signal.bb_width_at_trigger,
                    "volume_z": signal.volume_z_at_trigger,
                    "rvol": signal.rvol_at_trigger,
                    "social_mention_count": state.social_mention_count,
                },
                "setup_version": SETUP_VERSIONS["D1"],
                "batch_id": _ARGS["batch_id"],
            })
            # State machine handles cooldown automatically:
            # evaluate_trigger sets phase=TRIGGERED, next compute_state
            # transitions TRIGGERED→COOLDOWN, then COOLDOWN→SCANNING
            # after COOLDOWN_BARS_1H bars.

    return opps


# --- U1 Worker ---

def _worker_u1(symbol: str) -> list[dict]:
    """Walk U1 evaluator over 5m bars for one symbol. Runs in Pool worker."""
    tf = U1Config.GATE_TIMEFRAME  # "5m"
    raw_bars = _OHLCV_DATA.get((symbol, tf))
    if not raw_bars or len(raw_bars) < U1Config.GATE_WINDOW_BARS * 2:
        return []

    candles = _make_ohlcv_objects(raw_bars)
    evaluator = U1Evaluator()
    state = U1State()
    opps = []

    start_ms = int(_START_TS.timestamp() * 1000)
    end_ms = int(_END_TS.timestamp() * 1000)

    for i in range(U1Config.GATE_WINDOW_BARS * 2, len(candles)):
        bar_ts_ms = candles[i].timestamp
        if bar_ts_ms < start_ms:
            # Warmup: advance state machine but don't emit
            window = candles[max(0, i - 2100):i + 1]
            state = evaluator.evaluate_gates(window, prev_state=state, is_final=True)
            continue
        if bar_ts_ms > end_ms:
            break

        window = candles[max(0, i - 2100):i + 1]

        state = evaluator.evaluate_gates(window, prev_state=state, is_final=True)

        # Compute volume_z_1d for U1 v4.0 hard gate
        bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)
        vol_z_1d = get_volume_z_1d(symbol, bar_dt)

        signal = evaluator.evaluate_trigger(
            window, state, symbol, tf,
            is_final=True, volume_z_1d=vol_z_1d,
        )

        if signal:
            time_stop = getattr(signal, "time_stop_bars",
                                U1Config.TIME_STOP_BARS_5M)
            opps.append({
                "created_ts": bar_dt,
                "symbol": symbol,
                "timeframe": tf,
                "setup_id": "U1",
                "decision": "SIGNAL",
                "score": signal.setup_score,
                "reason": "U1 unicorn ignition",
                "payload": {
                    "entry_price": signal.entry_price,
                    "stop_price": signal.stop_price,
                    "tp1_price": signal.tp1_price,
                    "tp2_price": signal.tp2_price,
                    "r_value": signal.r_value,
                    "direction": "long",
                    "time_stop_bars": time_stop,
                    "ignition_score": getattr(signal, "ignition_score", state.ignition_score),
                    "trigger_path": getattr(signal, "trigger_path", "score"),
                    "volume_z_1d": vol_z_1d,
                },
                "setup_version": SETUP_VERSIONS["U1"],
                "batch_id": _ARGS["batch_id"],
            })

    return opps


# --- S10 Worker ---
# S10 compute_state is stateless: each bar fully determined from snapshot.
# MACD crossover uses snapshot.macd_prev_histogram (computed by FeatureFactory),
# not a carried-forward state. No prev_state needed.

def _worker_s10(symbol: str) -> list[dict]:
    """Walk S10 evaluator over 5m bars for one symbol. Runs in Pool worker."""
    raw_bars = _OHLCV_DATA.get((symbol, "5m"))
    if not raw_bars or len(raw_bars) < 201:
        return []

    candles = _make_ohlcv_objects(raw_bars)
    evaluator = S10Evaluator()
    ff = FeatureFactory()
    opps = []

    start_ms = int(_START_TS.timestamp() * 1000)
    end_ms = int(_END_TS.timestamp() * 1000)

    for i in range(201, len(candles)):
        bar_ts_ms = candles[i].timestamp
        if bar_ts_ms < start_ms:
            continue
        if bar_ts_ms > end_ms:
            break

        window = candles[max(0, i - 250):i + 1]
        if len(window) < 201:
            continue

        try:
            snapshot = ff.compute(window)
        except Exception:
            continue

        state = evaluator.compute_state(snapshot)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, "5m")

        if signal:
            bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)
            opps.append({
                "created_ts": bar_dt,
                "symbol": symbol,
                "timeframe": "5m",
                "setup_id": "S10",
                "decision": "SIGNAL",
                "score": signal.setup_score,
                "reason": "S10 MACD scalp breakout",
                "payload": {
                    "entry_price": signal.entry_price,
                    "stop_price": signal.stop_price,
                    "tp1_price": signal.tp1_price,
                    "tp2_price": signal.tp2_price,
                    "r_value": signal.r_value,
                    "direction": "long",
                    "time_stop_bars": signal.time_stop_bars,
                    "score_components": signal.score_components,
                    "macd_histogram": signal.macd_histogram,
                    "rvol": signal.rvol,
                    "flat_top_level": signal.flat_top_level,
                },
                "setup_version": SETUP_VERSIONS["S10"],
                "batch_id": _ARGS["batch_id"],
            })

    return opps


def _worker_s11(symbol: str) -> list[dict]:
    """Walk S11 MACD Long Reversion evaluator over 1h bars for one symbol.

    Identical semantics to the live signal_worker._evaluate_s11 path:
    local MACD(3,10,16) from close series, SMA50/SMA200/ATR from the
    FeatureFactory snapshot, bullish histogram crossover + filter gates,
    stop=5.0xATR, time_stop=24 bars. Runs in Pool worker.
    """
    raw_bars = _OHLCV_DATA.get((symbol, "1h"))
    if not raw_bars or len(raw_bars) < 201:
        return []

    candles = _make_ohlcv_objects(raw_bars)
    evaluator = S11Evaluator()
    ff = FeatureFactory()
    opps = []

    start_ms = int(_START_TS.timestamp() * 1000)
    end_ms = int(_END_TS.timestamp() * 1000)

    for i in range(201, len(candles)):
        bar_ts_ms = candles[i].timestamp
        if bar_ts_ms < start_ms:
            continue
        if bar_ts_ms > end_ms:
            break

        window = candles[max(0, i - 250):i + 1]
        if len(window) < 201:
            continue

        try:
            snapshot = ff.compute(window)
        except Exception:
            continue

        state = evaluator.compute_state(snapshot)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, "1h")

        if signal:
            bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)
            opps.append({
                "created_ts": bar_dt,
                "symbol": symbol,
                "timeframe": "1h",
                "setup_id": "S11",
                "decision": "SIGNAL",
                "score": signal.setup_score,
                "reason": "S11 MACD long reversion (deep-oversold pullback)",
                "payload": {
                    "entry_price": signal.entry_price,
                    "stop_price": signal.stop_price,
                    # S11 carries None for TPs — preserved through JSON as null
                    "tp1_price": signal.tp1_price,
                    "tp2_price": signal.tp2_price,
                    "r_value": signal.r_value,
                    "direction": "long",
                    "time_stop_bars": signal.time_stop_bars,
                    "score_components": signal.score_components,
                    "macd_fast_n": evaluator.MACD_FAST,
                    "macd_slow_n": evaluator.MACD_SLOW,
                    "macd_signal_n": evaluator.MACD_SIGNAL,
                    "macd_line": signal.macd_line,
                    "macd_signal": signal.macd_signal,
                    "macd_histogram": signal.macd_histogram,
                    "macd_prev_histogram": signal.macd_prev_histogram,
                    "sma_50": signal.sma_50,
                    "sma_200": signal.sma_200,
                    "close_vs_sma50_atr": signal.close_vs_sma50_atr,
                    "close_vs_sma200_atr": signal.close_vs_sma200_atr,
                    "atr_at_signal": signal.atr_at_signal,
                },
                "setup_version": SETUP_VERSIONS["S11"],
                "batch_id": _ARGS["batch_id"],
            })

    return opps


def run_per_symbol(setup_id: str, args):
    """Run per-symbol parallel mode for D1, U1, S10, or S11."""
    global _OHLCV_DATA, _BTC_TS_SORTED, _BTC_RETURNS_BY_TS
    global _SOCIAL_DAILY, _VOL_Z_1D, _ARGS, _START_TS, _END_TS

    tf = STRATEGY_TF[setup_id]
    warmup = timedelta(days=WARMUP_DAYS[setup_id])
    ohlcv_start = args.start_date - warmup

    _ARGS = {"batch_id": args.batch_id}
    _START_TS = args.start_date
    _END_TS = args.end_date

    # Load OHLCV
    raw_data = load_ohlcv_bulk(tf, ohlcv_start)

    # Filter D1 exclusions
    if setup_id == "D1":
        excluded = {sym for sym in raw_data if is_d1_excluded(sym)}
        for sym in excluded:
            del raw_data[sym]
        logger.info("D1: excluded %d symbols, %d remain", len(excluded), len(raw_data))

    # Set COW globals
    _OHLCV_DATA = {(sym, tf): bars for sym, bars in raw_data.items()}

    # D1-specific: BTC returns + social silence
    if setup_id == "D1":
        _BTC_TS_SORTED, _BTC_RETURNS_BY_TS = load_btc_returns_indexed(ohlcv_start)
        _SOCIAL_DAILY = load_social_daily(args.start_date)

    # U1-specific: daily volume z-scores
    if setup_id == "U1":
        _VOL_Z_1D = load_volume_z_1d(args.start_date)

    # Select worker function
    worker_fn = {
        "D1": _worker_d1,
        "U1": _worker_u1,
        "S10": _worker_s10,
        "S11": _worker_s11,
    }[setup_id]

    # Sort symbols heaviest-first (most bars → longest compute)
    symbols = sorted(raw_data.keys(), key=lambda s: len(raw_data[s]), reverse=True)
    if symbols:
        heaviest = len(raw_data[symbols[0]])
        lightest = len(raw_data[symbols[-1]])
        logger.info("%s: %d symbols, heaviest=%d bars, lightest=%d bars, ratio=%.1fx",
                    setup_id, len(symbols), heaviest, lightest,
                    heaviest / max(lightest, 1))

    # Dispatch workers
    total_opps = 0
    t0 = time.time()
    conn = get_conn()

    try:
        with multiprocessing.Pool(args.workers) as pool:
            for idx, result in enumerate(pool.imap_unordered(worker_fn, symbols)):
                if result:
                    write_opportunities(result, conn=conn)
                    total_opps += len(result)

                if (idx + 1) % 50 == 0 or idx == len(symbols) - 1:
                    elapsed = time.time() - t0
                    rate = (idx + 1) / elapsed if elapsed > 0 else 0
                    eta = (len(symbols) - idx - 1) / rate if rate > 0 else 0
                    logger.info(
                        "[%d/%d] %s: %d opps so far | %.0fs elapsed | ETA %.0fs",
                        idx + 1, len(symbols), setup_id, total_opps, elapsed, eta,
                    )
    finally:
        conn.close()

    elapsed = time.time() - t0
    logger.info("%s COMPLETE: %d opps from %d symbols in %.0fs",
                setup_id, total_opps, len(symbols), elapsed)
    return total_opps


# ═══════════════════════════════════════════════════════════════════════════
# Mode 2: Cross-Sectional Serial (S9)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_fng_history(start_date: datetime, end_date: datetime) -> dict:
    """Fetch Fear & Greed Index history. Returns {date_str: int_value}."""
    import urllib.request
    days = (end_date - start_date).days + 10
    url = f"https://api.alternative.me/fng/?limit={days}&format=json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        result = {}
        for entry in data.get("data", []):
            ts = int(entry["timestamp"])
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            result[dt.strftime("%Y-%m-%d")] = int(entry["value"])
        logger.info("FnG history: %d days loaded", len(result))
        return result
    except Exception as e:
        logger.warning("FnG API failed: %s — S9 FnG gate will be SKIPPED", e)
        return {}


def run_s9(args):
    """Run S9 cross-sectional mode."""
    warmup = timedelta(days=WARMUP_DAYS["S9"])
    ohlcv_start = args.start_date - warmup

    raw_data = load_ohlcv_bulk("1h", ohlcv_start)
    fng_data = fetch_fng_history(args.start_date, args.end_date)

    # Symbol whitelist: restrict the entire cross-sectional universe (ranking +
    # breadth) to the specified symbols. Canonical S9 universe is the
    # Binance USDT-M perp-eligible set (Phase 0, perp rerank). If no
    # --symbol-whitelist is supplied, fall back to the canonical file shipped
    # in the image. Pass --no-symbol-whitelist to disable the filter entirely
    # (non-canonical; only for research/full-universe reruns).
    whitelist_path = getattr(args, "symbol_whitelist", None)
    no_whitelist = getattr(args, "no_symbol_whitelist", False)
    if not no_whitelist and not whitelist_path:
        # Default to the canonical file baked into the image (Phase 4 canon).
        default_paths = [
            "/backend/config/s9_symbol_whitelist.txt",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config", "s9_symbol_whitelist.txt",
            ),
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "binance_perp_symbols.txt",
            ),
        ]
        for candidate in default_paths:
            if os.path.isfile(candidate):
                whitelist_path = candidate
                logger.info("S9: using canonical whitelist at %s", whitelist_path)
                break
    if whitelist_path:
        with open(whitelist_path) as f:
            whitelist = {line.strip().upper() for line in f if line.strip()}
        pre_count = len(raw_data)
        raw_data = {s: v for s, v in raw_data.items() if s.upper() in whitelist}
        logger.info("S9: symbol whitelist applied — %d / %d symbols kept (%d in whitelist file)",
                    len(raw_data), pre_count, len(whitelist))
    else:
        logger.warning("S9: NO symbol whitelist applied — using full universe (non-canonical)")

    # Collect all unique 1h timestamps
    all_timestamps = set()
    for bars in raw_data.values():
        for b in bars:
            all_timestamps.add(b[0])
    sorted_ts = sorted(all_timestamps)
    logger.info("S9: %d unique 1h timestamps, %d symbols", len(sorted_ts), len(raw_data))

    # Build lookup: {symbol: {ts_ms: (o, h, l, c, v)}}
    sym_data = {}
    for sym, bars in raw_data.items():
        sym_data[sym] = {b[0]: (b[1], b[2], b[3], b[4], b[5]) for b in bars}

    start_ms = int(args.start_date.timestamp() * 1000)
    end_ms = int(args.end_date.timestamp() * 1000)
    # S9 frozen config (hardcoded from strategy_config_freeze_20260407.csv)
    class _s9_cfg:
        FNG_GATE_ENABLED = True
        FNG_MAX_VALUE = 50
        MIN_RET_4H = 0.05
        BREADTH_THRESHOLD = 0.3
        PERCENTILE = 10
    ret_lookback = S9Evaluator.RET_LOOKBACK
    ms_per_hour = 3_600_000
    lookback_ms = ret_lookback * ms_per_hour
    breadth_lookback_ms = 24 * ms_per_hour

    total_opps = 0
    gap_drops_total = 0
    conn = get_conn()
    batch = []
    t0 = time.time()

    for ts_idx, ts_ms in enumerate(sorted_ts):
        if ts_ms < start_ms or ts_ms > end_ms:
            continue

        bar_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        # FnG gate (frozen config: FNG_GATE_ENABLED=True, FNG_MAX_VALUE=50)
        if _s9_cfg.FNG_GATE_ENABLED and fng_data:
            date_key = bar_dt.strftime("%Y-%m-%d")
            fng_val = fng_data.get(date_key)
            if fng_val is not None and fng_val > _s9_cfg.FNG_MAX_VALUE:
                continue

        # Compute 4h and 24h returns for all symbols at this timestamp
        returns_4h = {}
        returns_24h = {}
        closes = {}
        prev_ts = ts_ms - lookback_ms
        breadth_prev_ts = ts_ms - breadth_lookback_ms
        gap_drops_this_bar = 0

        for sym, lookup in sym_data.items():
            current = lookup.get(ts_ms)
            if current is None:
                continue
            prev = lookup.get(prev_ts)
            if prev is None:
                gap_drops_this_bar += 1
                continue
            close_now = current[3]
            close_prev = prev[3]
            if close_prev <= 0:
                continue
            returns_4h[sym] = (close_now - close_prev) / close_prev
            closes[sym] = close_now

            breadth_prev = lookup.get(breadth_prev_ts)
            if breadth_prev:
                close_24h_ago = breadth_prev[3]
                if close_24h_ago > 0:
                    returns_24h[sym] = (close_now - close_24h_ago) / close_24h_ago

        gap_drops_total += gap_drops_this_bar

        if len(returns_4h) < 20:
            continue

        # Market breadth
        n_positive_24h = sum(1 for r in returns_24h.values() if r > 0)
        breadth = n_positive_24h / len(returns_24h) if returns_24h else 0

        if breadth < _s9_cfg.BREADTH_THRESHOLD:
            continue

        # Rank by 4h return (descending), take top decile
        ranked = sorted(returns_4h.items(), key=lambda x: x[1], reverse=True)
        decile_size = max(1, len(ranked) * _s9_cfg.PERCENTILE // 100)
        top_decile = ranked[:decile_size]

        threshold = ranked[decile_size - 1][1] if decile_size <= len(ranked) else 0
        state = S9State(
            breadth=breadth,
            breadth_passes=True,
            universe_size=len(returns_4h),
            top_decile_threshold=threshold,
            top_decile_count=decile_size,
        )

        for rank, (sym, ret_4h) in enumerate(top_decile):
            if ret_4h < _s9_cfg.MIN_RET_4H:
                continue

            close = closes[sym]
            ret_24h = returns_24h.get(sym, 0)

            signal = S9Evaluator.build_signal(
                symbol=sym, close=close, ret_4h=ret_4h, ret_24h=ret_24h,
                breadth=breadth, rank=rank, universe_size=len(returns_4h),
                decile_size=decile_size, state=state,
                bar_start_ts=str(ts_ms),
            )

            batch.append({
                "created_ts": bar_dt,
                "symbol": sym,
                "timeframe": "1h",
                "setup_id": "S9",
                "decision": "SIGNAL",
                "score": signal.setup_score,
                "reason": f"S9 top-decile 4h gainer (rank #{rank+1}/{len(returns_4h)})",
                "payload": {
                    "entry_price": signal.entry_price,
                    "stop_price": signal.stop_price,
                    "tp1_price": signal.tp1_price,
                    "tp2_price": signal.tp2_price,
                    "r_value": signal.r_value,
                    "direction": "short",
                    "time_stop_bars": signal.time_stop_bars,
                    "ret_4h": ret_4h,
                    "ret_24h": ret_24h,
                    "rank": rank + 1,
                    "universe_size": len(returns_4h),
                    "breadth": breadth,
                },
                "setup_version": SETUP_VERSIONS["S9"],
                "batch_id": args.batch_id,
            })

        if len(batch) >= 1000:
            write_opportunities(batch, conn=conn)
            total_opps += len(batch)
            batch = []

        if ts_idx > 0 and ts_idx % (24 * 7) == 0:
            elapsed = time.time() - t0
            logger.info("S9 progress: %s | %d opps | %.0fs | gap-drops: %d",
                        bar_dt.strftime("%Y-%m-%d %H:%M"),
                        total_opps + len(batch), elapsed, gap_drops_total)

    if batch:
        write_opportunities(batch, conn=conn)
        total_opps += len(batch)

    conn.close()
    elapsed = time.time() - t0
    logger.info("S9 COMPLETE: %d opps in %.0fs (gap-drops: %d symbols×bars skipped)",
                total_opps, elapsed, gap_drops_total)
    return total_opps


# ═══════════════════════════════════════════════════════════════════════════
# Mode 3: Event-Driven (D2)
# ═══════════════════════════════════════════════════════════════════════════

def run_d2(args):
    """Run D2 event-driven mode."""
    t0 = time.time()

    logger.info("D2: loading WhaleBot pump alerts from social_posts...")
    alerts = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, content, post_ts
                FROM social_posts
                WHERE post_ts >= %s AND post_ts <= %s
                  AND (
                    (source = 'telegram_historical' AND channel = 'WhaleBotPumps')
                    OR (source = 'telegram_historical' AND channel = 'WhaleBotAlerts')
                    OR (source = 'telegram' AND channel = 'WhaleBotPumps')
                    OR (source = 'telegram' AND channel = 'WhaleBotAlerts')
                  )
                ORDER BY post_ts
            """, (
                args.start_date - timedelta(days=WARMUP_DAYS["D2"]),
                args.end_date,
            ))
            for row in cur:
                post_id, content, post_ts = row[0], row[1], row[2]
                parsed = parse_whalebotpumps(post_id, content, post_ts)
                if parsed and parsed.get("signal_type") == "pump":
                    alerts.append(parsed)

    logger.info("D2: %d pump alerts parsed", len(alerts))
    if not alerts:
        logger.info("D2: no pump alerts — 0 opps")
        return 0

    clusters = cluster_from_alerts(alerts)
    logger.info("D2: %d clusters detected from %d alerts", len(clusters), len(alerts))

    cluster_symbols = list(set(c["symbol"] for c in clusters))
    ohlcv_start = args.start_date - timedelta(days=WARMUP_DAYS["D2"])
    ohlcv_data = load_ohlcv_bulk("1h", ohlcv_start, symbols=cluster_symbols)

    total_opps = 0
    opps_batch = []
    conn = get_conn()

    for idx, cluster in enumerate(clusters):
        sym = cluster["symbol"]

        passed, reason = check_gates(cluster)
        if not passed:
            logger.debug("D2 GATED: %s — %s", sym, reason)
            continue

        raw_bars = ohlcv_data.get(sym)
        if not raw_bars:
            logger.debug("D2: no OHLCV for %s", sym)
            continue

        bars = [
            (datetime.fromtimestamp(b[0] / 1000, tz=timezone.utc),
             b[1], b[2], b[3], b[4])
            for b in raw_bars
        ]

        pre_pump = get_pre_pump_price(bars, cluster["cluster_start_ts"])
        if pre_pump is None:
            logger.debug("D2: no pre-pump price for %s", sym)
            continue

        signal = evaluate_cluster(cluster, bars, pre_pump)
        if signal is None:
            continue

        opps_batch.append({
            "created_ts": signal.signal_ts,
            "symbol": sym,
            "timeframe": "1h",
            "setup_id": "D2",
            "decision": "SIGNAL",
            "score": signal.setup_score,
            "reason": f"D2 fade-the-casino: {signal.alert_count} alerts, "
                      f"{signal.drop_from_hwm_pct:.1f}% drop from HWM",
            "payload": {
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "tp1_price": signal.tp1_price,
                "tp2_price": signal.tp2_price,
                "r_value": signal.r_value,
                "direction": "short",
                "time_stop_bars": signal.time_stop_bars,
                "pre_pump_price": signal.pre_pump_price,
                "hwm_price": signal.hwm_price,
                "drop_from_hwm_pct": signal.drop_from_hwm_pct,
                "max_pct_change": signal.max_pct_change,
                "alert_count": signal.alert_count,
                "cluster_start_ts": signal.cluster_start_ts.isoformat()
                                    if signal.cluster_start_ts else None,
                "score_components": signal.score_components,
            },
            "setup_version": SETUP_VERSIONS["D2"],
            "batch_id": args.batch_id,
        })

        if (idx + 1) % 50 == 0:
            logger.info("D2 progress: %d/%d clusters, %d opps",
                        idx + 1, len(clusters), len(opps_batch))

    if opps_batch:
        write_opportunities(opps_batch, conn=conn)
        total_opps = len(opps_batch)

    conn.close()
    elapsed = time.time() - t0
    logger.info("D2 COMPLETE: %d opps from %d clusters in %.0fs",
                total_opps, len(clusters), elapsed)
    return total_opps


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    global _DRY_RUN

    parser = argparse.ArgumentParser(description="Regenerate opportunities under frozen configs")
    parser.add_argument("--setup-ids", required=True,
                        help="Comma-separated: D1,D2,U1,S9,S10")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=12,
                        help="Parallel workers (default: 12)")
    parser.add_argument("--batch-id", required=True, help="Batch ID tag")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count opps without writing to DB")
    parser.add_argument("--symbol-whitelist", default=None,
                        help="Path to file with newline-separated symbols. "
                             "If set, S9 restricts ranking to these symbols only. "
                             "Defaults to the canonical perp-only file baked into the image.")
    parser.add_argument("--no-symbol-whitelist", action="store_true",
                        help="Disable the default canonical S9 whitelist (use full universe). "
                             "Non-canonical — only for research/full-universe reruns.")

    args = parser.parse_args()
    _DRY_RUN = args.dry_run

    args.start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(
        tzinfo=timezone.utc,
    )
    args.end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc,
    )

    setup_ids = [s.strip().upper() for s in args.setup_ids.split(",")]
    valid_ids = {"D1", "D2", "U1", "S9", "S10", "S11"}
    invalid = set(setup_ids) - valid_ids
    if invalid:
        logger.error("Invalid setup IDs: %s (valid: %s)", invalid, valid_ids)
        sys.exit(1)

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = OUTPUT_DIR / f"regen_{args.batch_id}_{ts_str}.txt"
    logger.info("=" * 60)
    logger.info("OPPORTUNITY REGENERATION%s", " (DRY RUN)" if _DRY_RUN else "")
    logger.info("  Setup IDs:  %s", ", ".join(setup_ids))
    logger.info("  Date range: %s to %s", args.start_date.date(), args.end_date.date())
    logger.info("  Workers:    %d", args.workers)
    logger.info("  Batch ID:   %s", args.batch_id)
    logger.info("  Output:     %s", out_file)
    logger.info("  DB:         %s", DATABASE_URL[:50] + "...")
    logger.info("=" * 60)

    results = {}
    t_total = time.time()

    for setup_id in setup_ids:
        logger.info("\n>>> Starting %s <<<", setup_id)

        if setup_id in ("D1", "U1", "S10", "S11"):
            count = run_per_symbol(setup_id, args)
        elif setup_id == "S9":
            count = run_s9(args)
        elif setup_id == "D2":
            count = run_d2(args)
        else:
            logger.error("Unknown setup ID: %s", setup_id)
            continue

        results[setup_id] = count

    total_elapsed = time.time() - t_total

    summary_lines = [
        f"Opportunity Regeneration Summary",
        f"================================",
        f"Batch ID:   {args.batch_id}",
        f"Date range: {args.start_date.date()} to {args.end_date.date()}",
        f"Workers:    {args.workers}",
        f"Dry run:    {_DRY_RUN}",
        f"Elapsed:    {total_elapsed:.0f}s",
        f"",
    ]
    total = 0
    for sid, count in results.items():
        summary_lines.append(f"  {sid}: {count:,} opportunities")
        total += count
    summary_lines.append(f"")
    summary_lines.append(f"  TOTAL: {total:,} opportunities")

    summary_text = "\n".join(summary_lines)
    out_file.write_text(summary_text)

    logger.info("\n%s", summary_text)
    logger.info("\nOutput written to: %s", out_file)


if __name__ == "__main__":
    main()
