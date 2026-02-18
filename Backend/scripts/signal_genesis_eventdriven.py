#!/usr/bin/env python3
"""
Signal Genesis Event-Driven — Microstructure Event Trading (h24)
=================================================================

Hypothesis: Continuous IC-weighted composites produce noise. The edge only
exists at specific, rare microstructure events where information asymmetry
creates predictable short-term price dynamics.

Four event types tested:
  1. Absorption:  High volume + small body → large orders absorbed
  2. Vacuum Move: Large price move + low volume → liquidity vacuum reversal
  3. Volume Climax: Extreme volume + directional candle → exhaustion reversal
  4. Compression Release: Contracting range/volume then breakout → continuation

Three exit configurations tested per fold:
  A) Standard: stop=2.0 ATR, time_stop=36, trail 1.5R/1.0R
  B) Tight:    stop=1.5 ATR, time_stop=24 (events work fast or not at all)
  C) Time-only: NO ATR stop, 36-bar hold (pure event conviction)

Usage:
    tmux new -s eventdriven
    docker compose run --rm -T worker python3 /scripts/signal_genesis_eventdriven.py --days 110
    # Quick smoke test:
    docker compose run --rm -T worker python3 /scripts/signal_genesis_eventdriven.py --days 30 --quick
"""

# ─── Standard library ────────────────────────────────────────────────────────

import os
import sys
import time
import json
import math
import argparse
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional, Dict, List, Tuple

# ─── Env + path setup ────────────────────────────────────────────────────────

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/exit_checker.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))

# ─── BLAS thread control ─────────────────────────────────────────────────────
os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')
os.environ.setdefault('MKL_NUM_THREADS', '4')

# ─── Third-party ─────────────────────────────────────────────────────────────

import numpy as np
import psycopg
from exit_checker import ExitConfig, check_exit_simple as _check_exit_simple_orig
import inspect

_exit_simple_params = inspect.signature(_check_exit_simple_orig).parameters

def check_exit_simple(**kwargs):
    if 'config' in kwargs and 'config' not in _exit_simple_params:
        kwargs.pop('config')
    return _check_exit_simple_orig(**kwargs)

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

WARMUP_BARS = 100
POSITION_SIZE_USD = 1000.0
FEE_BPS = 4.0
MIN_SHORT_PRICE = 1.00
BTC_SYMBOL = "BTCUSDT"
BASE_SLIPPAGE_1H = 5.0
MIN_BARS_PER_SYMBOL = 100

HORIZON = 24

# Exit configs: A=Standard, B=Tight, C=Time-only
EXIT_CONFIGS = {
    "A_standard": {
        "stop_atr_mult": 2.0,
        "time_stop_bars": 36,
        "trail_activation_r": 1.5,
        "trail_distance_r": 1.0,
        "trail_breakeven_r": 0.0,
    },
    "B_tight": {
        "stop_atr_mult": 1.5,
        "time_stop_bars": 24,
        "trail_activation_r": 1.5,
        "trail_distance_r": 1.0,
        "trail_breakeven_r": 0.0,
    },
    "C_timeonly": {
        "stop_atr_mult": None,  # No ATR stop
        "time_stop_bars": 36,
        "trail_activation_r": 1.5,
        "trail_distance_r": 1.0,
        "trail_breakeven_r": 0.0,
    },
}

# Event detection default thresholds
DEFAULT_THRESHOLDS = {
    "absorption_volume_z": 2.0,
    "absorption_body_ratio": 0.25,
    "absorption_wick_mult": 2.0,
    "vacuum_volume_z": -0.5,
    "vacuum_price_z": 2.0,
    "climax_volume_z": 3.0,
    "climax_body_ratio": 0.6,
    "compression_range_ratio": 0.5,
    "compression_volume_ratio": 0.7,
    "compression_breakout_ratio": 1.5,
}

# Threshold sweep ranges for optimisation
THRESHOLD_SWEEPS = {
    "absorption_volume_z": [1.5, 2.0, 2.5],
    "vacuum_price_z": [1.5, 2.0, 2.5],
    "climax_volume_z": [2.5, 3.0, 3.5],
}

# Walk-forward config
N_FOLDS = 3
EMBARGO_DAYS = 1
HOLDOUT_DAYS = 18
MIN_TRADES_TOTAL = 25
MIN_FOLD_CONSISTENCY = 2 / 3  # 2 out of 3

EVENT_TYPES = ["absorption", "vacuum", "climax", "compression"]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EventSignal:
    symbol: str
    direction: str      # "long" or "short"
    ts_idx: int
    score: float
    event_type: str     # absorption, vacuum, climax, compression
    event_details: dict  # threshold values that triggered


@dataclass
class TradeResult:
    symbol: str
    direction: str
    ts_idx: int
    entry_price: float
    exit_price: float
    pnl: float
    r_mult: float
    exit_reason: str
    bars_held: int
    event_type: str
    exit_config_name: str


@dataclass
class FoldResult:
    fold_idx: int
    exit_config_name: str
    n_trades: int
    avg_daily_pf: float
    avg_daily_pf_robust: float
    median_daily_pf: float
    aggregate_pf: float
    win_rate: float
    total_pnl: float
    event_counts: dict       # per-event-type trade counts
    event_pfs: dict          # per-event-type aggregate PF
    direction_breakdown: dict  # per-event per-direction PF
    best_thresholds: dict    # thresholds used
    n_signals: int


# ═══════════════════════════════════════════════════════════════════════════════
# ROLLING HELPERS (from genesis, unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_sum_2d(arr, w):
    cs = np.cumsum(arr, axis=0)
    out = np.empty_like(arr)
    out[:w] = cs[:w]
    out[w:] = cs[w:] - cs[:-w]
    return out


def _rolling_mean_2d(arr, w):
    rs = _rolling_sum_2d(arr, w)
    out = rs / w
    out[:w-1] = np.nan
    return out


def _rolling_std_2d(arr, w):
    cs1 = np.cumsum(arr, axis=0)
    cs2 = np.cumsum(arr**2, axis=0)
    s1 = np.empty_like(arr)
    s2 = np.empty_like(arr)
    s1[:w] = cs1[:w]
    s1[w:] = cs1[w:] - cs1[:-w]
    s2[:w] = cs2[:w]
    s2[w:] = cs2[w:] - cs2[:-w]
    mean = s1 / w
    var = s2 / w - mean**2
    var = np.maximum(var, 0)
    out = np.sqrt(var)
    out[:w-1] = np.nan
    return out


def _atr_2d(high, low, close, n=14):
    prev_close = np.full_like(close, np.nan)
    prev_close[1:] = close[:-1]
    hl = high - low
    hc = np.abs(high - prev_close)
    lc = np.abs(low - prev_close)
    tr = np.maximum(hl, np.maximum(hc, lc))
    tr[0] = hl[0]
    return _rolling_mean_2d(tr, n)


def _rolling_atr_1d(highs, lows, closes, n=14):
    length = len(closes)
    prev_close = np.empty(length)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]
    hl = highs - lows
    hc = np.abs(highs - prev_close)
    lc = np.abs(lows - prev_close)
    tr = np.maximum(hl, np.maximum(hc, lc))
    tr[0] = highs[0] - lows[0]
    atr = np.zeros(length)
    cumtr = np.cumsum(tr)
    atr[n-1:] = (cumtr[n-1:] - np.concatenate([[0], cumtr[:-n]])) / n
    return atr


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_ohlcv_matrices(database_url: str, days: int):
    """Load 1h OHLCV and build aligned (n_bars, n_symbols) matrices."""
    start_ts = datetime.now(timezone.utc) - timedelta(days=days + 15)
    end_ts = datetime.now(timezone.utc)

    logger.info("Loading 1h OHLCV: %s to %s (%d+15 days)", start_ts.date(), end_ts.date(), days)
    t0 = time.time()

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM (
                    SELECT symbol, bar_start_ts, open, high, low, close, volume,
                           ROW_NUMBER() OVER (
                               PARTITION BY symbol, bar_start_ts
                               ORDER BY CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END
                           ) AS rn
                    FROM ohlcv
                    WHERE timeframe = '1h'
                      AND bar_start_ts >= %s AND bar_start_ts < %s
                      AND is_final = TRUE
                ) sub
                WHERE rn = 1
                ORDER BY symbol, bar_start_ts
            """, (start_ts, end_ts))

            sym_rows = defaultdict(list)
            row_count = 0
            for row in cur:
                sym_rows[row[0]].append(row)
                row_count += 1

    logger.info("  Fetched %d rows for %d symbols in %.1fs",
                row_count, len(sym_rows), time.time() - t0)

    all_ts = set()
    for rows in sym_rows.values():
        for r in rows:
            ts = r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None else r[1].timestamp()
            all_ts.add(ts)
    ts_grid = np.array(sorted(all_ts))
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)

    valid_symbols = []
    for sym, rows in sorted(sym_rows.items()):
        if len(rows) >= MIN_BARS_PER_SYMBOL:
            valid_symbols.append(sym)

    n_symbols = len(valid_symbols)
    logger.info("  %d symbols with >= %d bars, %d total bars in grid",
                n_symbols, MIN_BARS_PER_SYMBOL, n_bars)

    close = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    open_ = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    high = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    low = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    volume = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)

    btc_idx = None
    for s_idx, sym in enumerate(valid_symbols):
        if sym == BTC_SYMBOL:
            btc_idx = s_idx
        for r in sym_rows[sym]:
            ts = r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None else r[1].timestamp()
            t_idx = ts_to_idx[ts]
            open_[t_idx, s_idx] = float(r[2])
            high[t_idx, s_idx] = float(r[3])
            low[t_idx, s_idx] = float(r[4])
            close[t_idx, s_idx] = float(r[5])
            volume[t_idx, s_idx] = float(r[6])

    # Forward-fill NaN gaps
    for s_idx in range(n_symbols):
        for arr in [close, open_, high, low, volume]:
            col = arr[:, s_idx]
            mask = np.isnan(col)
            if mask.any() and not mask.all():
                idx = np.where(~mask, np.arange(n_bars), 0)
                np.maximum.accumulate(idx, out=idx)
                arr[:, s_idx] = col[idx]

    # Build per-symbol OHLCV dict for trade simulation
    ohlcv_data = {}
    for s_idx, sym in enumerate(valid_symbols):
        ohlcv_data[(sym, '1h')] = {
            "ts": ts_grid,
            "open": open_[:, s_idx],
            "high": high[:, s_idx],
            "low": low[:, s_idx],
            "close": close[:, s_idx],
            "volume": volume[:, s_idx],
        }

    mem_mb = (close.nbytes + open_.nbytes + high.nbytes + low.nbytes + volume.nbytes) / 1024**2
    logger.info("  Matrices: (%d, %d), %.1f MB, BTC idx=%s",
                n_bars, n_symbols, mem_mb, btc_idx)

    return close, open_, high, low, volume, ts_grid, valid_symbols, btc_idx, ohlcv_data


def compute_forward_returns(close):
    """Compute forward returns for h=24 only."""
    n_bars, n_symbols = close.shape
    h = HORIZON
    ret = np.full((n_bars, n_symbols), np.nan, dtype=np.float32)
    valid = (close[:-h] != 0) & ~np.isnan(close[:-h]) & ~np.isnan(close[h:])
    ret_vals = np.where(valid, (close[h:] - close[:-h]) / close[:-h], np.nan)
    ret[:n_bars - h] = ret_vals.astype(np.float32)
    logger.info("  Forward returns h=%d: %d valid bars", h, np.sum(~np.isnan(ret)))
    return ret


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: PRECOMPUTE INTERMEDIATES
# ═══════════════════════════════════════════════════════════════════════════════

def precompute_intermediates(close, open_, high, low, volume):
    """Precompute all rolling statistics needed by event detectors.

    Returns a dict of (n_bars, n_symbols) arrays.
    """
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    t0 = time.time()
    n_bars, n_symbols = close.shape
    inter = {}

    # Bar structure
    bar_range = high - low
    safe_range = np.where(bar_range > 0, bar_range, 1e-10)
    body = close - open_                       # signed body (positive = green)
    abs_body = np.abs(body)
    body_ratio = abs_body / safe_range         # |body| / range

    inter["bar_range"] = bar_range
    inter["safe_range"] = safe_range
    inter["body"] = body
    inter["abs_body"] = abs_body
    inter["body_ratio"] = body_ratio

    # Wicks
    upper_wick = high - np.maximum(close, open_)
    lower_wick = np.minimum(close, open_) - low
    inter["upper_wick"] = upper_wick
    inter["lower_wick"] = lower_wick

    # Returns
    safe_close = np.where(close > 0, close, 1e-10)
    ret_1 = np.full((n_bars, n_symbols), np.nan)
    valid_prev = (close[:-1] != 0) & ~np.isnan(close[:-1])
    ret_1[1:] = np.where(valid_prev,
                         (close[1:] - close[:-1]) / np.where(valid_prev, close[:-1], 1.0),
                         np.nan)
    inter["ret_1"] = ret_1

    # Volume z-score (rolling, per-symbol)
    vol_mean_21 = _rolling_mean_2d(volume, 21)
    vol_std_21 = _rolling_std_2d(volume, 21)
    safe_vol_std = np.where(vol_std_21 > 0, vol_std_21, 1e-10)
    volume_z = (volume - vol_mean_21) / safe_vol_std
    volume_z[:20] = np.nan  # warmup
    inter["volume_z"] = volume_z
    inter["vol_mean_21"] = vol_mean_21

    # Rolling return std (for price z-score in vacuum detection)
    ret_std_21 = _rolling_std_2d(ret_1, 21)
    inter["ret_std_21"] = ret_std_21

    # Rolling mean range and volume (for compression detection)
    range_mean_21 = _rolling_mean_2d(bar_range, 21)
    inter["range_mean_21"] = range_mean_21

    # ATR 14
    atr_14 = _atr_2d(high, low, close, 14)
    inter["atr_14"] = atr_14

    elapsed = time.time() - t0
    logger.info("  Precomputed intermediates in %.1fs", elapsed)
    return inter


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: EVENT DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_absorption_events(open_, high, low, close, inter, thresholds):
    """Detect absorption events.

    Absorption: High volume + low price movement. Large orders are being
    absorbed by the market. Small body relative to range with large wicks.

    Returns:
        event_mask: (n_bars, n_symbols) bool — True where event detected
        direction:  (n_bars, n_symbols) int — +1 for LONG, -1 for SHORT, 0 for none
        strength:   (n_bars, n_symbols) float — conviction score
    """
    n_bars, n_symbols = close.shape
    vol_z_thresh = thresholds.get("absorption_volume_z", 2.0)
    body_thresh = thresholds.get("absorption_body_ratio", 0.25)
    wick_mult = thresholds.get("absorption_wick_mult", 2.0)

    volume_z = inter["volume_z"]
    body_ratio = inter["body_ratio"]
    body = inter["body"]
    abs_body = inter["abs_body"]
    upper_wick = inter["upper_wick"]
    lower_wick = inter["lower_wick"]

    # Core conditions: high volume + small body
    cond_volume = volume_z > vol_z_thresh
    cond_body = body_ratio < body_thresh

    safe_abs_body = np.where(abs_body > 0, abs_body, 1e-10)

    # Direction determination:
    # If close > open slightly AND upper wick > wick_mult * body → longs being absorbed → SHORT
    # If close < open slightly AND lower wick > wick_mult * body → shorts being absorbed → LONG
    is_green = body > 0
    is_red = body < 0

    cond_absorb_longs = is_green & (upper_wick > wick_mult * safe_abs_body)
    cond_absorb_shorts = is_red & (lower_wick > wick_mult * safe_abs_body)

    event_mask = np.zeros((n_bars, n_symbols), dtype=bool)
    direction = np.zeros((n_bars, n_symbols), dtype=np.int8)
    strength = np.zeros((n_bars, n_symbols), dtype=np.float64)

    # SHORT signals: absorption of longs
    short_mask = cond_volume & cond_body & cond_absorb_longs
    event_mask |= short_mask
    direction[short_mask] = -1

    # LONG signals: absorption of shorts
    long_mask = cond_volume & cond_body & cond_absorb_shorts
    event_mask |= long_mask
    direction[long_mask] = 1

    # Strength: based on how extreme the volume z-score is
    strength[event_mask] = np.clip(volume_z[event_mask] / vol_z_thresh, 1.0, 3.0)

    # NaN guard: no events in warmup
    event_mask[:WARMUP_BARS] = False
    direction[:WARMUP_BARS] = 0
    strength[:WARMUP_BARS] = 0.0

    return event_mask, direction, strength


def detect_vacuum_events(close, inter, thresholds):
    """Detect vacuum move events.

    Vacuum: Large price movement on low volume — price moved through a
    liquidity vacuum. Predicts mean-reversion.

    Returns:
        event_mask, direction, strength — same shapes as input
    """
    n_bars, n_symbols = close.shape
    vol_z_thresh = thresholds.get("vacuum_volume_z", -0.5)
    price_z_thresh = thresholds.get("vacuum_price_z", 2.0)

    volume_z = inter["volume_z"]
    ret_1 = inter["ret_1"]
    ret_std_21 = inter["ret_std_21"]

    # Low volume
    cond_low_vol = volume_z < vol_z_thresh

    # Large price move: |ret_1| > price_z_thresh * rolling_std_21
    safe_std = np.where(ret_std_21 > 0, ret_std_21, 1e-10)
    price_z = np.abs(ret_1) / safe_std
    cond_large_move = price_z > price_z_thresh

    event_mask = cond_low_vol & cond_large_move

    # Direction: OPPOSITE of the move (mean-reversion)
    direction = np.zeros((n_bars, n_symbols), dtype=np.int8)
    direction[event_mask & (ret_1 > 0)] = -1   # price went up → SHORT
    direction[event_mask & (ret_1 < 0)] = 1    # price went down → LONG
    # If ret_1 == 0 exactly, no direction → remove from mask
    event_mask[event_mask & (ret_1 == 0)] = False

    # Strength: how extreme the price move was
    strength = np.zeros((n_bars, n_symbols), dtype=np.float64)
    strength[event_mask] = np.clip(price_z[event_mask] / price_z_thresh, 1.0, 3.0)

    # NaN guard
    event_mask[:WARMUP_BARS] = False
    direction[:WARMUP_BARS] = 0
    strength[:WARMUP_BARS] = 0.0

    return event_mask, direction, strength


def detect_climax_events(open_, high, low, close, inter, thresholds):
    """Detect volume climax events.

    Climax: Extreme volume spike with strong directional candle.
    Predicts exhaustion and reversal.

    Returns:
        event_mask, direction, strength
    """
    n_bars, n_symbols = close.shape
    vol_z_thresh = thresholds.get("climax_volume_z", 3.0)
    body_thresh = thresholds.get("climax_body_ratio", 0.6)

    volume_z = inter["volume_z"]
    body_ratio = inter["body_ratio"]
    body = inter["body"]

    # Extreme volume + strong directional body
    cond_volume = volume_z > vol_z_thresh
    cond_body = body_ratio > body_thresh

    event_mask = cond_volume & cond_body

    # Direction: OPPOSITE of the candle (exhaustion reversal)
    direction = np.zeros((n_bars, n_symbols), dtype=np.int8)
    direction[event_mask & (body > 0)] = -1   # green candle → SHORT (exhaustion)
    direction[event_mask & (body < 0)] = 1    # red candle → LONG (exhaustion)
    # Remove zero-body events
    event_mask[event_mask & (body == 0)] = False

    # Strength: how extreme the volume z-score is
    strength = np.zeros((n_bars, n_symbols), dtype=np.float64)
    strength[event_mask] = np.clip(volume_z[event_mask] / vol_z_thresh, 1.0, 3.0)

    # NaN guard
    event_mask[:WARMUP_BARS] = False
    direction[:WARMUP_BARS] = 0
    strength[:WARMUP_BARS] = 0.0

    return event_mask, direction, strength


def detect_compression_release(open_, high, low, close, volume, inter, thresholds):
    """Detect compression release events.

    Compression: Period of contracting range + declining volume, followed by
    a sudden range expansion. Predicts continuation of the breakout.

    The compression condition is checked on bar t-1, and the breakout on bar t.

    Returns:
        event_mask, direction, strength
    """
    n_bars, n_symbols = close.shape
    range_ratio = thresholds.get("compression_range_ratio", 0.5)
    vol_ratio = thresholds.get("compression_volume_ratio", 0.7)
    breakout_ratio = thresholds.get("compression_breakout_ratio", 1.5)

    bar_range = inter["bar_range"]
    range_mean_21 = inter["range_mean_21"]
    vol_mean_21 = inter["vol_mean_21"]
    body = inter["body"]

    safe_range_mean = np.where(range_mean_21 > 0, range_mean_21, 1e-10)
    safe_vol_mean = np.where(vol_mean_21 > 0, vol_mean_21, 1e-10)

    # Compression on previous bar: range < ratio * mean AND volume < ratio * mean
    range_contracted = bar_range / safe_range_mean < range_ratio
    vol_low = volume / safe_vol_mean < vol_ratio

    compression_prev = np.zeros((n_bars, n_symbols), dtype=bool)
    compression_prev[1:] = range_contracted[:-1] & vol_low[:-1]

    # Breakout on current bar: range > breakout_ratio * mean range
    breakout_current = bar_range / safe_range_mean > breakout_ratio

    event_mask = compression_prev & breakout_current

    # Direction: SAME as breakout candle direction
    direction = np.zeros((n_bars, n_symbols), dtype=np.int8)
    direction[event_mask & (body > 0)] = 1    # green breakout → LONG
    direction[event_mask & (body < 0)] = -1   # red breakout → SHORT
    # Remove neutral candles
    event_mask[event_mask & (body == 0)] = False

    # Strength: how large the breakout range is relative to mean
    strength = np.zeros((n_bars, n_symbols), dtype=np.float64)
    ratio_vals = bar_range / safe_range_mean
    strength[event_mask] = np.clip(ratio_vals[event_mask] / breakout_ratio, 1.0, 3.0)

    # NaN guard
    event_mask[:WARMUP_BARS] = False
    direction[:WARMUP_BARS] = 0
    strength[:WARMUP_BARS] = 0.0

    return event_mask, direction, strength


def detect_all_events(open_, high, low, close, volume, inter, thresholds):
    """Run all four event detectors. Returns dict of event data per type."""
    events = {}

    mask, direction, strength = detect_absorption_events(open_, high, low, close, inter, thresholds)
    events["absorption"] = {"mask": mask, "direction": direction, "strength": strength}

    mask, direction, strength = detect_vacuum_events(close, inter, thresholds)
    events["vacuum"] = {"mask": mask, "direction": direction, "strength": strength}

    mask, direction, strength = detect_climax_events(open_, high, low, close, inter, thresholds)
    events["climax"] = {"mask": mask, "direction": direction, "strength": strength}

    mask, direction, strength = detect_compression_release(open_, high, low, close, volume, inter, thresholds)
    events["compression"] = {"mask": mask, "direction": direction, "strength": strength}

    return events


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: SIGNAL GENERATION FROM EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_event_signals(events, bar_mask, symbols, active_events=None):
    """Convert detected events into tradeable signals.

    Args:
        events: dict from detect_all_events
        bar_mask: (n_bars,) bool — which bars to consider
        symbols: list of symbol names
        active_events: list of event types to include (default: all)

    Returns:
        list of EventSignal
    """
    if active_events is None:
        active_events = EVENT_TYPES

    n_bars = len(bar_mask)
    n_symbols = len(symbols)
    bar_indices = np.where(bar_mask)[0]
    signals = []

    # For compound events: aggregate strength across multiple event types
    # firing on the same symbol at the same bar
    for t in bar_indices:
        for s_idx in range(n_symbols):
            # Collect all events for this symbol at this bar
            firing_events = []
            total_strength = 0.0
            consensus_direction = 0

            for etype in active_events:
                ev = events[etype]
                if ev["mask"][t, s_idx]:
                    d = int(ev["direction"][t, s_idx])
                    s = float(ev["strength"][t, s_idx])
                    firing_events.append((etype, d, s))
                    total_strength += s
                    consensus_direction += d

            if not firing_events:
                continue

            # Determine direction: use consensus if multiple events
            if len(firing_events) == 1:
                etype, direction, strength = firing_events[0]
                dir_str = "long" if direction > 0 else "short"
                # Compound bonus: none for single event
                score = strength
                details = {"event": etype, "strength": strength}
            else:
                # Multiple events: only trade if they agree on direction
                if consensus_direction == 0:
                    continue  # conflicting signals cancel out
                dir_str = "long" if consensus_direction > 0 else "short"
                # Compound bonus: +50% per additional agreeing event
                agreeing = sum(1 for _, d, _ in firing_events
                               if (d > 0 and consensus_direction > 0)
                               or (d < 0 and consensus_direction < 0))
                score = total_strength * (1.0 + 0.5 * (agreeing - 1))
                etype = "+".join(sorted(set(e for e, _, _ in firing_events)))
                details = {
                    "events": [e for e, _, _ in firing_events],
                    "strengths": [s for _, _, s in firing_events],
                    "compound_bonus": agreeing - 1,
                }

            signals.append(EventSignal(
                symbol=symbols[s_idx],
                direction=dir_str,
                ts_idx=t,
                score=score,
                event_type=etype,
                event_details=details,
            ))

    return signals


def compute_event_frequency(events, bar_mask):
    """Compute event frequency diagnostics: events per bar for each type."""
    n_active_bars = bar_mask.sum()
    if n_active_bars == 0:
        return {}

    freq = {}
    for etype in EVENT_TYPES:
        ev = events[etype]
        n_events = ev["mask"][bar_mask].sum()
        # Per bar across all symbols
        n_symbols = ev["mask"].shape[1]
        total_slots = n_active_bars * n_symbols
        freq[etype] = {
            "total_events": int(n_events),
            "events_per_bar": float(n_events / n_active_bars),
            "pct_of_bar_symbol_slots": float(n_events / total_slots * 100) if total_slots > 0 else 0.0,
        }
    return freq


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATION (from sparse, with exit config support + event tracking)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_exit_config(cfg):
    ec = ExitConfig()
    for key in ("trail_breakeven_r", "trail_activation_r", "trail_distance_r"):
        if hasattr(ec, key):
            setattr(ec, key, cfg[key])
    return ec


def simulate_trades(signals, ohlcv_data, slippage_bps, exit_cfg_name, exit_cfg_dict):
    """Simulate signals to trades. Slippage applied to ALL exits.

    Args:
        signals: list of EventSignal
        ohlcv_data: per-symbol OHLCV
        slippage_bps: slippage in basis points
        exit_cfg_name: name of exit config (A/B/C)
        exit_cfg_dict: dict with stop_atr_mult, time_stop_bars, trail params
    """
    stop_atr_mult = exit_cfg_dict["stop_atr_mult"]  # None for time-only
    time_stop_bars = exit_cfg_dict["time_stop_bars"]
    exit_config = _make_exit_config(exit_cfg_dict)

    results = []

    for sig in signals:
        key = (sig.symbol, '1h')
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        highs = data["high"]
        lows = data["low"]
        opens = data["open"]
        n = len(closes)

        i = sig.ts_idx
        if i + 1 >= n or i < 14:
            continue

        atr = _rolling_atr_1d(highs[:i+1], lows[:i+1], closes[:i+1], 14)[-1]
        if atr <= 0:
            continue

        entry_raw = opens[i + 1]
        if sig.direction == "long":
            entry = entry_raw * (1 + slippage_bps / 10000)
            if stop_atr_mult is not None:
                stop = entry - stop_atr_mult * atr
            else:
                stop = 0.0  # No stop for time-only config
        else:
            entry = entry_raw * (1 - slippage_bps / 10000)
            if stop_atr_mult is not None:
                stop = entry + stop_atr_mult * atr
            else:
                stop = entry * 10.0  # Effectively no stop for short
            if entry < MIN_SHORT_PRICE:
                continue

        if entry <= 0:
            continue

        # For time-only config, r_value is based on ATR anyway (for position sizing)
        if stop_atr_mult is not None:
            r_value = abs(entry - stop)
        else:
            # Use 2.0 ATR as notional risk for R-mult calculation
            r_value = 2.0 * atr

        if r_value <= 0 or r_value / entry > 0.10:
            continue
        if 2 * entry * FEE_BPS / 10000 > 0.3 * r_value:
            continue

        max_idx = min(i + 2 + time_stop_bars + 20, n)
        hwm = None
        half_r_touched = False
        bars_held = 0
        exit_reason = "TIME_STOP"
        exit_price = closes[min(max_idx - 1, n - 1)]

        for j in range(i + 2, max_idx):
            bars_held += 1
            bar = {"open": opens[j], "high": highs[j], "low": lows[j], "close": closes[j]}

            # For time-only config, pass an extreme stop that won't trigger
            if stop_atr_mult is None:
                if sig.direction == "long":
                    sim_stop = 0.0
                else:
                    sim_stop = entry * 10.0
            else:
                sim_stop = stop

            result, new_hwm, new_half_r = check_exit_simple(
                side=sig.direction, bar=bar,
                entry_price=entry, stop_price=sim_stop,
                tp1_price=None, tp2_price=None,
                r_value=r_value, bars_held=bars_held,
                max_bars=time_stop_bars, high_water_mark=hwm,
                half_r_touched=half_r_touched, config=exit_config,
            )
            hwm = new_hwm
            half_r_touched = new_half_r
            if result:
                exit_reason, exit_price = result
                break

        # Apply slippage to ALL exits (including stop-loss)
        if sig.direction == "long":
            ep = exit_price * (1 - slippage_bps / 10000)
        else:
            ep = exit_price * (1 + slippage_bps / 10000)

        pos_size = POSITION_SIZE_USD / entry
        if sig.direction == "long":
            pnl_gross = (ep - entry) * pos_size
        else:
            pnl_gross = (entry - ep) * pos_size
        fees = (entry + ep) * pos_size * FEE_BPS / 10000
        pnl_net = pnl_gross - fees
        r_mult = pnl_net / (r_value * pos_size) if r_value * pos_size > 0 else 0

        results.append(TradeResult(
            symbol=sig.symbol, direction=sig.direction,
            ts_idx=sig.ts_idx, entry_price=entry, exit_price=ep,
            pnl=pnl_net, r_mult=r_mult, exit_reason=exit_reason,
            bars_held=bars_held, event_type=sig.event_type,
            exit_config_name=exit_cfg_name,
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS (fixed avg daily PF + new robust + median variants)
# ═══════════════════════════════════════════════════════════════════════════════

def _trades_to_daily_pnl(trades, ohlcv_data, symbols):
    """Group trades into daily win/loss buckets. Returns dict of day → {wins, losses, count}."""
    if not trades:
        return {}

    # Get ts_grid from first available symbol
    ts_grid = None
    for sym in symbols:
        key = (sym, '1h')
        if key in ohlcv_data:
            ts_grid = ohlcv_data[key]["ts"]
            break
    if ts_grid is None:
        return {}

    daily = defaultdict(lambda: {"wins": 0.0, "losses": 0.0, "count": 0})
    for t in trades:
        if t.ts_idx < len(ts_grid):
            ts = ts_grid[t.ts_idx]
        else:
            continue
        day = int(ts // 86400)
        daily[day]["count"] += 1
        if t.pnl > 0:
            daily[day]["wins"] += t.pnl
        elif t.pnl < 0:
            daily[day]["losses"] += abs(t.pnl)

    return daily


def compute_avg_daily_pf(trades, ohlcv_data, symbols):
    """Compute average daily profit factor (original metric)."""
    daily = _trades_to_daily_pnl(trades, ohlcv_data, symbols)
    if not daily:
        return 0.0

    pfs = []
    for day_data in daily.values():
        w = day_data["wins"]
        l = day_data["losses"]
        if l > 0:
            pfs.append(w / l)
        elif w > 0:
            pfs.append(10.0)

    return float(np.mean(pfs)) if pfs else 0.0


def compute_avg_daily_pf_robust(trades, ohlcv_data, symbols, min_trades_per_day=3):
    """Compute average daily profit factor, only including days with >= min_trades_per_day.

    This prevents false positives from low-trade-count days where a single
    winning trade produces PF=10.0 and inflates the average.
    """
    daily = _trades_to_daily_pnl(trades, ohlcv_data, symbols)
    if not daily:
        return 0.0

    pfs = []
    for day_data in daily.values():
        if day_data["count"] < min_trades_per_day:
            continue
        w = day_data["wins"]
        l = day_data["losses"]
        if l > 0:
            pfs.append(w / l)
        elif w > 0:
            pfs.append(10.0)

    return float(np.mean(pfs)) if pfs else 0.0


def compute_median_daily_pf(trades, ohlcv_data, symbols):
    """Compute median daily profit factor — robust to outlier days."""
    daily = _trades_to_daily_pnl(trades, ohlcv_data, symbols)
    if not daily:
        return 0.0

    pfs = []
    for day_data in daily.values():
        w = day_data["wins"]
        l = day_data["losses"]
        if l > 0:
            pfs.append(w / l)
        elif w > 0:
            pfs.append(10.0)

    return float(np.median(pfs)) if pfs else 0.0


def compute_aggregate_pf(trades):
    """Compute aggregate profit factor over all trades."""
    if not trades:
        return 0.0
    gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
    if gross_losses > 0:
        return gross_wins / gross_losses
    elif gross_wins > 0:
        return 10.0
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# EVENT DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_event_diagnostics(trades):
    """Compute per-event-type and per-direction P&L breakdown.

    Returns:
        event_counts: {event_type: count}
        event_pfs: {event_type: aggregate_pf}
        direction_breakdown: {event_type: {long: {pf, n, pnl}, short: {pf, n, pnl}}}
    """
    if not trades:
        return {}, {}, {}

    # Group trades by event type
    by_event = defaultdict(list)
    for t in trades:
        by_event[t.event_type].append(t)

    event_counts = {}
    event_pfs = {}
    direction_breakdown = {}

    for etype, etrades in sorted(by_event.items()):
        event_counts[etype] = len(etrades)
        event_pfs[etype] = compute_aggregate_pf(etrades)

        dir_data = {}
        for d in ["long", "short"]:
            dtrades = [t for t in etrades if t.direction == d]
            if dtrades:
                pf = compute_aggregate_pf(dtrades)
                pnl = sum(t.pnl for t in dtrades)
                wr = sum(1 for t in dtrades if t.pnl > 0) / len(dtrades)
                dir_data[d] = {"pf": round(pf, 3), "n": len(dtrades),
                               "pnl": round(pnl, 2), "win_rate": round(wr, 3)}
            else:
                dir_data[d] = {"pf": 0.0, "n": 0, "pnl": 0.0, "win_rate": 0.0}
        direction_breakdown[etype] = dir_data

    return event_counts, event_pfs, direction_breakdown


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_folds(ts_grid, n_folds=N_FOLDS, holdout_days=HOLDOUT_DAYS, embargo_days=EMBARGO_DAYS):
    """Build expanding window folds. 3 folds + holdout."""
    day_start = ts_grid[0] // 86400
    day_end = ts_grid[-1] // 86400
    total_days = int(day_end - day_start) + 1

    logger.info("  Total span: %d days (%s to %s)",
                total_days,
                datetime.fromtimestamp(ts_grid[0], tz=timezone.utc).strftime("%Y-%m-%d"),
                datetime.fromtimestamp(ts_grid[-1], tz=timezone.utc).strftime("%Y-%m-%d"))

    min_required = holdout_days + 2 * 5 + embargo_days
    if total_days < min_required:
        logger.error("  Insufficient data: %d days, need >= %d", total_days, min_required)
        return [], np.zeros(len(ts_grid), dtype=bool)

    holdout_start_day = total_days - holdout_days
    train_test_days = holdout_start_day

    test_size = train_test_days // (n_folds + 1)
    if test_size < 5:
        test_size = 5

    folds = []
    for fold_idx in range(n_folds):
        train_end_day = (fold_idx + 1) * test_size + test_size
        test_start_day = train_end_day + embargo_days
        test_end_day = test_start_day + test_size

        if test_end_day > holdout_start_day:
            test_end_day = holdout_start_day
        if test_start_day >= holdout_start_day:
            break

        train_mask = np.zeros(len(ts_grid), dtype=bool)
        test_mask = np.zeros(len(ts_grid), dtype=bool)

        for i, ts in enumerate(ts_grid):
            day = int(ts // 86400 - day_start)
            if day < train_end_day:
                train_mask[i] = True
            elif test_start_day <= day < test_end_day:
                test_mask[i] = True

        train_bars = train_mask.sum()
        test_bars = test_mask.sum()
        if train_bars < 100 or test_bars < 50:
            continue

        folds.append((train_mask, test_mask))
        logger.info("    Fold %d: train days [0, %d) = %d bars, test days [%d, %d) = %d bars",
                     fold_idx, train_end_day, train_bars,
                     test_start_day, test_end_day, test_bars)

    holdout_mask = np.zeros(len(ts_grid), dtype=bool)
    for i, ts in enumerate(ts_grid):
        day = int(ts // 86400 - day_start)
        if day >= holdout_start_day:
            holdout_mask[i] = True

    logger.info("    Holdout: days [%d, %d) = %d bars",
                holdout_start_day, total_days, holdout_mask.sum())

    return folds, holdout_mask


def optimize_thresholds(open_, high, low, close, volume, inter, ohlcv_data,
                        symbols, train_mask, exit_cfg_name, exit_cfg_dict):
    """Sweep event thresholds on training data using 80/20 mini-val split.

    Returns the best threshold combination (highest robust avg daily PF).
    """
    # Split training mask into 80% train, 20% mini-val
    train_indices = np.where(train_mask)[0]
    n_train = len(train_indices)
    split_idx = int(n_train * 0.8)

    mini_train_mask = np.zeros(len(train_mask), dtype=bool)
    mini_val_mask = np.zeros(len(train_mask), dtype=bool)

    mini_train_mask[train_indices[:split_idx]] = True
    mini_val_mask[train_indices[split_idx:]] = True

    # Build sweep grid from THRESHOLD_SWEEPS
    sweep_keys = sorted(THRESHOLD_SWEEPS.keys())
    sweep_values = [THRESHOLD_SWEEPS[k] for k in sweep_keys]

    # Generate all combinations
    from itertools import product
    combos = list(product(*sweep_values))

    best_pf = -999.0
    best_thresholds = dict(DEFAULT_THRESHOLDS)
    n_combos = len(combos)

    logger.info("      Sweeping %d threshold combos on mini-val (%d bars)...",
                n_combos, mini_val_mask.sum())

    for combo_idx, combo in enumerate(combos):
        trial_thresholds = dict(DEFAULT_THRESHOLDS)
        for k, v in zip(sweep_keys, combo):
            trial_thresholds[k] = v

        # Detect events with trial thresholds
        events = detect_all_events(open_, high, low, close, volume, inter, trial_thresholds)

        # Generate signals on mini-val
        signals = generate_event_signals(events, mini_val_mask, symbols)

        if len(signals) < 5:
            continue

        # Simulate trades
        trades = simulate_trades(signals, ohlcv_data, BASE_SLIPPAGE_1H,
                                 exit_cfg_name, exit_cfg_dict)

        if len(trades) < 5:
            continue

        # Evaluate: use robust PF as the objective
        pf = compute_avg_daily_pf_robust(trades, ohlcv_data, symbols, min_trades_per_day=2)
        if pf > best_pf:
            best_pf = pf
            best_thresholds = dict(trial_thresholds)

    logger.info("      Best sweep PF=%.3f, thresholds: abs_vol_z=%.1f, vac_price_z=%.1f, clim_vol_z=%.1f",
                best_pf,
                best_thresholds.get("absorption_volume_z", 0),
                best_thresholds.get("vacuum_price_z", 0),
                best_thresholds.get("climax_volume_z", 0))

    return best_thresholds, best_pf


def run_walk_forward(open_, high, low, close, volume, inter, ohlcv_data,
                     symbols, folds, jsonl_path):
    """Run walk-forward for all 3 exit configs across all folds."""
    all_fold_results = []  # list of FoldResult
    t_wf_start = time.time()

    total_work = len(folds) * len(EXIT_CONFIGS)
    work_done = 0

    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        fold_t0 = time.time()
        logger.info("\n  ══ Fold %d ══", fold_idx)

        for ecfg_name, ecfg_dict in EXIT_CONFIGS.items():
            work_done += 1
            step_t0 = time.time()
            logger.info("    ── Exit config: %s ──", ecfg_name)

            # Optimize thresholds on training data
            best_thresholds, mini_val_pf = optimize_thresholds(
                open_, high, low, close, volume, inter, ohlcv_data,
                symbols, train_mask, ecfg_name, ecfg_dict)

            # Detect events with best thresholds on FULL data
            events = detect_all_events(open_, high, low, close, volume, inter, best_thresholds)

            # Event frequency diagnostic on test period
            freq = compute_event_frequency(events, test_mask)
            for etype, fdata in freq.items():
                logger.info("      Event '%s': %d events (%.2f%% of bar-symbol slots)",
                            etype, fdata["total_events"], fdata["pct_of_bar_symbol_slots"])

            # Generate signals on test period
            test_signals = generate_event_signals(events, test_mask, symbols)
            logger.info("      %d signals on test period", len(test_signals))

            # Simulate trades
            trades = simulate_trades(test_signals, ohlcv_data, BASE_SLIPPAGE_1H,
                                     ecfg_name, ecfg_dict)

            # Metrics
            if trades:
                avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
                avg_pf_robust = compute_avg_daily_pf_robust(trades, ohlcv_data, symbols,
                                                            min_trades_per_day=3)
                med_pf = compute_median_daily_pf(trades, ohlcv_data, symbols)
                agg_pf = compute_aggregate_pf(trades)
                total_pnl = sum(t.pnl for t in trades)
                wins = sum(1 for t in trades if t.pnl > 0)
                win_rate = wins / len(trades) if trades else 0.0
            else:
                avg_pf = avg_pf_robust = med_pf = agg_pf = total_pnl = win_rate = 0.0

            # Event diagnostics
            event_counts, event_pfs, direction_breakdown = compute_event_diagnostics(trades)

            step_elapsed = time.time() - step_t0
            elapsed_total = time.time() - t_wf_start
            eta = (elapsed_total / work_done) * (total_work - work_done) if work_done > 0 else 0

            logger.info("      %d trades, avg PF=%.3f, robust PF=%.3f, median PF=%.3f, "
                        "agg PF=%.3f, WR=%.1f%%, PnL=$%.2f (%.1fs, ETA %.0fs)",
                        len(trades), avg_pf, avg_pf_robust, med_pf, agg_pf,
                        win_rate * 100, total_pnl, step_elapsed, eta)

            # Log event-specific PFs
            for etype, epf in sorted(event_pfs.items()):
                n_e = event_counts.get(etype, 0)
                logger.info("        %s: PF=%.3f (%d trades)", etype, epf, n_e)

            # Log direction breakdown
            for etype, ddata in sorted(direction_breakdown.items()):
                for d in ["long", "short"]:
                    dd = ddata[d]
                    if dd["n"] > 0:
                        logger.info("          %s %s: PF=%.3f, WR=%.1f%%, n=%d, PnL=$%.2f",
                                    etype, d, dd["pf"], dd["win_rate"] * 100, dd["n"], dd["pnl"])

            # Exit reason breakdown
            if trades:
                reason_counts = defaultdict(int)
                for t in trades:
                    reason_counts[t.exit_reason] += 1
                logger.info("      Exit reasons: %s", dict(reason_counts))

            fr = FoldResult(
                fold_idx=fold_idx,
                exit_config_name=ecfg_name,
                n_trades=len(trades),
                avg_daily_pf=avg_pf,
                avg_daily_pf_robust=avg_pf_robust,
                median_daily_pf=med_pf,
                aggregate_pf=agg_pf,
                win_rate=win_rate,
                total_pnl=total_pnl,
                event_counts=event_counts,
                event_pfs={k: round(v, 4) for k, v in event_pfs.items()},
                direction_breakdown=direction_breakdown,
                best_thresholds={k: v for k, v in best_thresholds.items()
                                 if k in THRESHOLD_SWEEPS},
                n_signals=len(test_signals),
            )
            all_fold_results.append(fr)

            # Incremental JSONL write
            with open(jsonl_path, "a") as f:
                f.write(json.dumps({
                    "type": "fold",
                    "fold_idx": fold_idx,
                    "exit_config": ecfg_name,
                    **asdict(fr),
                    "event_frequency": freq,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, default=str) + "\n")

        fold_elapsed = time.time() - fold_t0
        logger.info("    Fold %d total: %.1fs", fold_idx, fold_elapsed)

    # Aggregate per exit config
    logger.info("\n  ── Walk-Forward Summary ──")
    summary = {}
    for ecfg_name in EXIT_CONFIGS:
        cfg_results = [fr for fr in all_fold_results if fr.exit_config_name == ecfg_name]
        valid = [fr for fr in cfg_results if fr.n_trades > 0]
        if valid:
            avg_pf = float(np.mean([fr.avg_daily_pf for fr in valid]))
            avg_pf_robust = float(np.mean([fr.avg_daily_pf_robust for fr in valid]))
            med_pf = float(np.mean([fr.median_daily_pf for fr in valid]))
            consistency = sum(1 for fr in valid if fr.avg_daily_pf_robust > 1.0) / len(valid)
            total_trades = sum(fr.n_trades for fr in valid)
        else:
            avg_pf = avg_pf_robust = med_pf = consistency = 0.0
            total_trades = 0

        summary[ecfg_name] = {
            "avg_daily_pf": avg_pf,
            "avg_daily_pf_robust": avg_pf_robust,
            "median_daily_pf": med_pf,
            "consistency": consistency,
            "total_trades": total_trades,
        }
        logger.info("    %s: avg PF=%.3f, robust PF=%.3f, median PF=%.3f, "
                     "consistency=%.1f%%, trades=%d",
                     ecfg_name, avg_pf, avg_pf_robust, med_pf,
                     consistency * 100, total_trades)

    return all_fold_results, summary


# ═══════════════════════════════════════════════════════════════════════════════
# HOLDOUT & DSR
# ═══════════════════════════════════════════════════════════════════════════════

def deflated_sharpe_ratio(observed_sr, n_trials, n_observations, skew=0, kurtosis=3):
    if n_trials <= 1 or n_observations <= 1:
        return 0.0
    euler_gamma = 0.5772156649
    log_n = math.log(n_trials)
    if log_n <= 0:
        return 0.0
    e_max_sr = (math.sqrt(2 * log_n) * (1 - euler_gamma / log_n)
                + euler_gamma / (2 * math.sqrt(2 * log_n)))
    se_sr = math.sqrt(
        (1 + 0.5 * observed_sr**2 - skew * observed_sr
         + (kurtosis - 3) / 4 * observed_sr**2)
        / n_observations
    )
    if se_sr <= 0:
        return 0.0
    return float((observed_sr - e_max_sr) / se_sr)


def run_holdout(open_, high, low, close, volume, inter, ohlcv_data, symbols,
                holdout_mask, folds, best_exit_cfg_name, best_exit_cfg_dict,
                n_configs_tested):
    """Run holdout on the best exit config at 1.5x and 2.0x slippage."""
    logger.info("  Holdout: using exit config '%s'", best_exit_cfg_name)

    # Full training = everything before holdout
    full_train_mask = np.zeros(len(holdout_mask), dtype=bool)
    holdout_start = np.where(holdout_mask)[0]
    if len(holdout_start) == 0:
        return None
    full_train_mask[:holdout_start[0]] = True

    logger.info("  Holdout: optimizing thresholds on %d training bars...", full_train_mask.sum())

    # Optimize thresholds on full training data
    best_thresholds, _ = optimize_thresholds(
        open_, high, low, close, volume, inter, ohlcv_data,
        symbols, full_train_mask, best_exit_cfg_name, best_exit_cfg_dict)

    # Detect events
    events = detect_all_events(open_, high, low, close, volume, inter, best_thresholds)

    # Event frequency on holdout
    freq = compute_event_frequency(events, holdout_mask)
    for etype, fdata in freq.items():
        logger.info("    Event '%s': %d events (%.2f%%)",
                     etype, fdata["total_events"], fdata["pct_of_bar_symbol_slots"])

    # Generate signals on holdout
    holdout_signals = generate_event_signals(events, holdout_mask, symbols)
    logger.info("  Holdout: %d signals", len(holdout_signals))

    # 1.5x slippage
    slippage_15x = BASE_SLIPPAGE_1H * 1.5
    trades = simulate_trades(holdout_signals, ohlcv_data, slippage_15x,
                             best_exit_cfg_name, best_exit_cfg_dict)

    if not trades:
        logger.info("  Holdout: 0 trades")
        return None

    avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
    avg_pf_robust = compute_avg_daily_pf_robust(trades, ohlcv_data, symbols, min_trades_per_day=3)
    med_pf = compute_median_daily_pf(trades, ohlcv_data, symbols)
    agg_pf = compute_aggregate_pf(trades)
    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades) if trades else 0.0

    # Event diagnostics
    event_counts, event_pfs, direction_breakdown = compute_event_diagnostics(trades)

    # DSR
    pnl_arr = np.array([t.pnl for t in trades])
    n_obs = len(pnl_arr)
    mean_pnl = np.mean(pnl_arr)
    std_pnl = np.std(pnl_arr, ddof=1)
    sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

    if n_obs > 2 and std_pnl > 0:
        z_pnl = (pnl_arr - mean_pnl) / std_pnl
        skew_val = float(np.mean(z_pnl**3))
        kurt_val = float(np.mean(z_pnl**4))
    else:
        skew_val, kurt_val = 0.0, 3.0

    dsr = deflated_sharpe_ratio(sharpe, n_configs_tested, n_obs, skew_val, kurt_val)

    logger.info("  Holdout (1.5x slip): %d trades, avg PF=%.3f, robust PF=%.3f, "
                "median PF=%.3f, agg PF=%.3f, WR=%.1f%%, Sharpe=%.3f, DSR=%.3f, PnL=$%.2f",
                len(trades), avg_pf, avg_pf_robust, med_pf, agg_pf,
                win_rate * 100, sharpe, dsr, total_pnl)

    # Event-specific holdout results
    for etype, epf in sorted(event_pfs.items()):
        n_e = event_counts.get(etype, 0)
        logger.info("    %s: PF=%.3f (%d trades)", etype, epf, n_e)

    for etype, ddata in sorted(direction_breakdown.items()):
        for d in ["long", "short"]:
            dd = ddata[d]
            if dd["n"] > 0:
                logger.info("      %s %s: PF=%.3f, WR=%.1f%%, n=%d",
                             etype, d, dd["pf"], dd["win_rate"] * 100, dd["n"])

    # 2.0x slippage sensitivity
    trades_2x = simulate_trades(holdout_signals, ohlcv_data, BASE_SLIPPAGE_1H * 2.0,
                                best_exit_cfg_name, best_exit_cfg_dict)
    if trades_2x:
        pf_2x = compute_avg_daily_pf(trades_2x, ohlcv_data, symbols)
        pf_2x_robust = compute_avg_daily_pf_robust(trades_2x, ohlcv_data, symbols, min_trades_per_day=3)
        logger.info("  Holdout (2.0x slip): %d trades, avg PF=%.3f, robust PF=%.3f",
                     len(trades_2x), pf_2x, pf_2x_robust)
    else:
        pf_2x = pf_2x_robust = 0.0

    return {
        "exit_config": best_exit_cfg_name,
        "thresholds": best_thresholds,
        "avg_daily_pf": avg_pf,
        "avg_daily_pf_robust": avg_pf_robust,
        "median_daily_pf": med_pf,
        "aggregate_pf": agg_pf,
        "dsr": dsr,
        "sharpe": sharpe,
        "n_trades": len(trades),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "event_counts": event_counts,
        "event_pfs": {k: round(v, 4) for k, v in event_pfs.items()},
        "direction_breakdown": direction_breakdown,
        "event_frequency": freq,
        "pf_2x_slippage": pf_2x,
        "pf_2x_slippage_robust": pf_2x_robust,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INDIVIDUAL EVENT ABLATION
# ═══════════════════════════════════════════════════════════════════════════════

def run_event_ablation(open_, high, low, close, volume, inter, ohlcv_data,
                       symbols, folds, best_exit_cfg_name, best_exit_cfg_dict):
    """Test each event type in isolation AND each type removed.

    This tells us which events actually contribute signal.
    """
    logger.info("\n  ── Event Ablation ──")

    # Use default thresholds for consistency across ablation
    events = detect_all_events(open_, high, low, close, volume, inter, DEFAULT_THRESHOLDS)
    ablation_results = {}

    # Test each event type in ISOLATION
    for etype in EVENT_TYPES:
        logger.info("    Testing %s in isolation...", etype)
        fold_pfs = []
        total_trades = 0

        for fold_idx, (train_mask, test_mask) in enumerate(folds):
            signals = generate_event_signals(events, test_mask, symbols, active_events=[etype])
            trades = simulate_trades(signals, ohlcv_data, BASE_SLIPPAGE_1H,
                                     best_exit_cfg_name, best_exit_cfg_dict)
            if trades:
                pf = compute_avg_daily_pf_robust(trades, ohlcv_data, symbols, min_trades_per_day=2)
                fold_pfs.append(pf)
                total_trades += len(trades)

        avg_pf = float(np.mean(fold_pfs)) if fold_pfs else 0.0
        ablation_results[f"only_{etype}"] = {
            "avg_pf_robust": round(avg_pf, 4),
            "n_trades": total_trades,
        }
        logger.info("      only_%s: robust PF=%.3f (%d trades)", etype, avg_pf, total_trades)

    # Test each event type REMOVED
    for etype in EVENT_TYPES:
        logger.info("    Testing without %s...", etype)
        remaining = [e for e in EVENT_TYPES if e != etype]
        fold_pfs = []
        total_trades = 0

        for fold_idx, (train_mask, test_mask) in enumerate(folds):
            signals = generate_event_signals(events, test_mask, symbols, active_events=remaining)
            trades = simulate_trades(signals, ohlcv_data, BASE_SLIPPAGE_1H,
                                     best_exit_cfg_name, best_exit_cfg_dict)
            if trades:
                pf = compute_avg_daily_pf_robust(trades, ohlcv_data, symbols, min_trades_per_day=2)
                fold_pfs.append(pf)
                total_trades += len(trades)

        avg_pf = float(np.mean(fold_pfs)) if fold_pfs else 0.0
        ablation_results[f"without_{etype}"] = {
            "avg_pf_robust": round(avg_pf, 4),
            "n_trades": total_trades,
        }
        logger.info("      without_%s: robust PF=%.3f (%d trades)", etype, avg_pf, total_trades)

    return ablation_results


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Signal Genesis Event-Driven — Microstructure Event Trading (h24)")
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--quick", action="store_true", help="Quick mode: 2 folds, no ablation")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    args = parser.parse_args()

    t_start = time.time()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    n_folds = 2 if args.quick else N_FOLDS

    logger.info("=" * 72)
    logger.info("SIGNAL GENESIS EVENT-DRIVEN — Microstructure Event Trading (h24)")
    logger.info("=" * 72)
    logger.info("Config: days=%d, horizon=%d, folds=%d, holdout=%d days",
                args.days, HORIZON, n_folds, args.holdout_days)
    logger.info("Events: %s", EVENT_TYPES)
    logger.info("Exit configs: %s", list(EXIT_CONFIGS.keys()))
    logger.info("Threshold sweeps: %s", THRESHOLD_SWEEPS)

    # ── Phase 1: Data Loading ──
    logger.info("\n" + "-" * 54)
    logger.info("PHASE 1: Data Loading")
    logger.info("-" * 54)

    close, open_, high, low, volume, ts_grid, symbols, btc_idx, ohlcv_data = \
        load_ohlcv_matrices(database_url, args.days)

    logger.info("Computing forward returns (h=%d)...", HORIZON)
    fwd_ret = compute_forward_returns(close)

    # ── Phase 2: Precompute Intermediates ──
    logger.info("\n" + "-" * 54)
    logger.info("PHASE 2: Precompute Intermediates")
    logger.info("-" * 54)

    inter = precompute_intermediates(close, open_, high, low, volume)

    # Quick event frequency check on full data
    events_full = detect_all_events(open_, high, low, close, volume, inter, DEFAULT_THRESHOLDS)
    full_mask = np.ones(len(ts_grid), dtype=bool)
    full_mask[:WARMUP_BARS] = False
    freq_full = compute_event_frequency(events_full, full_mask)
    logger.info("  Full-data event frequencies (default thresholds):")
    for etype, fdata in freq_full.items():
        logger.info("    %s: %d events, %.3f per bar, %.2f%% of slots",
                     etype, fdata["total_events"], fdata["events_per_bar"],
                     fdata["pct_of_bar_symbol_slots"])

    # ── Phase 3: Walk-Forward ──
    logger.info("\n" + "-" * 54)
    logger.info("PHASE 3: Walk-Forward Validation")
    logger.info("-" * 54)

    folds, holdout_mask = build_folds(ts_grid, n_folds=n_folds,
                                      holdout_days=args.holdout_days)
    if not folds:
        logger.error("No folds could be constructed. Need more data.")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    jsonl_path = os.path.join(script_dir, "signal_genesis_eventdriven_incremental.jsonl")

    # Clear previous incremental file
    if os.path.exists(jsonl_path):
        os.remove(jsonl_path)

    all_fold_results, wf_summary = run_walk_forward(
        open_, high, low, close, volume, inter, ohlcv_data,
        symbols, folds, jsonl_path)

    # ── Phase 4: Determine Best Exit Config ──
    logger.info("\n" + "-" * 54)
    logger.info("PHASE 4: Select Best Exit Config")
    logger.info("-" * 54)

    # Pick exit config with best robust avg daily PF
    best_exit_cfg_name = None
    best_robust_pf = -999.0
    for ecfg_name, sdata in wf_summary.items():
        rpf = sdata["avg_daily_pf_robust"]
        logger.info("  %s: robust PF=%.3f, consistency=%.1f%%, trades=%d",
                     ecfg_name, rpf, sdata["consistency"] * 100, sdata["total_trades"])
        if rpf > best_robust_pf:
            best_robust_pf = rpf
            best_exit_cfg_name = ecfg_name

    best_exit_cfg_dict = EXIT_CONFIGS[best_exit_cfg_name]
    best_summary = wf_summary[best_exit_cfg_name]
    logger.info("  Best exit config: %s (robust PF=%.3f)", best_exit_cfg_name, best_robust_pf)

    # ── Phase 5: Holdout ──
    logger.info("\n" + "-" * 54)
    logger.info("PHASE 5: Holdout Evaluation")
    logger.info("-" * 54)

    # Walk-forward pass check using robust PF
    wf_pass = (best_summary["avg_daily_pf_robust"] > 1.0
               and best_summary["consistency"] >= MIN_FOLD_CONSISTENCY
               and best_summary["total_trades"] >= MIN_TRADES_TOTAL)

    # Count total configurations tested for DSR
    n_sweep_combos = 1
    for vals in THRESHOLD_SWEEPS.values():
        n_sweep_combos *= len(vals)
    n_configs_tested = n_sweep_combos * len(EXIT_CONFIGS)  # threshold combos x exit configs

    holdout_result = None
    if wf_pass:
        holdout_result = run_holdout(
            open_, high, low, close, volume, inter, ohlcv_data, symbols,
            holdout_mask, folds, best_exit_cfg_name, best_exit_cfg_dict,
            n_configs_tested)
    else:
        logger.info("  SKIPPING holdout (walk-forward failed)")
        logger.info("    robust PF=%.3f (need >1.0), consistency=%.1f%% (need >=%.0f%%), trades=%d (need >=%d)",
                     best_summary["avg_daily_pf_robust"],
                     best_summary["consistency"] * 100,
                     MIN_FOLD_CONSISTENCY * 100,
                     best_summary["total_trades"],
                     MIN_TRADES_TOTAL)

    # ── Phase 6: Event Ablation ──
    logger.info("\n" + "-" * 54)
    logger.info("PHASE 6: Event Ablation")
    logger.info("-" * 54)

    ablation_results = {}
    if not args.quick:
        ablation_results = run_event_ablation(
            open_, high, low, close, volume, inter, ohlcv_data,
            symbols, folds, best_exit_cfg_name, best_exit_cfg_dict)
    else:
        logger.info("  Skipped in quick mode")

    # ── Phase 7: Report ──
    logger.info("\n" + "-" * 54)
    logger.info("PHASE 7: Final Report")
    logger.info("-" * 54)

    elapsed_total = time.time() - t_start

    # Determine verdict
    if not wf_pass:
        verdict = "FAIL_WALK_FORWARD"
        fail_reasons = []
        if best_summary["avg_daily_pf_robust"] <= 1.0:
            fail_reasons.append(f"robust_avg_daily_pf={best_summary['avg_daily_pf_robust']:.3f} <= 1.0")
        if best_summary["consistency"] < MIN_FOLD_CONSISTENCY:
            fail_reasons.append(f"consistency={best_summary['consistency']:.2f} < {MIN_FOLD_CONSISTENCY:.2f}")
        if best_summary["total_trades"] < MIN_TRADES_TOTAL:
            fail_reasons.append(f"trades={best_summary['total_trades']} < {MIN_TRADES_TOTAL}")
    elif holdout_result is None:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = ["no holdout result (0 trades)"]
    elif holdout_result["avg_daily_pf_robust"] <= 1.0:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = [f"holdout_robust_pf={holdout_result['avg_daily_pf_robust']:.3f} <= 1.0"]
    elif holdout_result["dsr"] <= 0:
        verdict = "FAIL_DSR"
        fail_reasons = [f"DSR={holdout_result['dsr']:.3f} <= 0"]
    else:
        verdict = "CONFIRMED"
        fail_reasons = []

    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_total, 1),
        "model": "event_driven_microstructure",
        "horizon": HORIZON,
        "events": EVENT_TYPES,
        "exit_configs_tested": list(EXIT_CONFIGS.keys()),
        "best_exit_config": best_exit_cfg_name,
        "n_configs_tested": n_configs_tested,
        "threshold_sweeps": THRESHOLD_SWEEPS,
        "walk_forward": {
            "per_exit_config": wf_summary,
            "folds": [asdict(fr) for fr in all_fold_results],
        },
        "holdout": holdout_result if holdout_result else {
            "avg_daily_pf": 0.0, "avg_daily_pf_robust": 0.0, "median_daily_pf": 0.0,
            "aggregate_pf": 0.0, "dsr": -99.0, "n_trades": 0,
        },
        "event_ablation": ablation_results,
        "event_frequency_full_data": freq_full,
        "verdict": verdict,
        "fail_reasons": fail_reasons,
    }

    output_path = os.path.join(script_dir, "signal_genesis_eventdriven_results.json")
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Results written to %s", output_path)

    # Incremental write: final summary
    with open(jsonl_path, "a") as f:
        f.write(json.dumps({
            "type": "summary",
            "verdict": verdict,
            "fail_reasons": fail_reasons,
            "best_exit_config": best_exit_cfg_name,
            "walk_forward_summary": wf_summary,
            "holdout_summary": {
                "avg_daily_pf": holdout_result["avg_daily_pf"] if holdout_result else 0.0,
                "robust_pf": holdout_result["avg_daily_pf_robust"] if holdout_result else 0.0,
                "dsr": holdout_result["dsr"] if holdout_result else -99.0,
                "n_trades": holdout_result["n_trades"] if holdout_result else 0,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, default=str) + "\n")

    # ── Print Summary ──
    logger.info("\n" + "=" * 72)
    logger.info("SUMMARY")
    logger.info("=" * 72)
    logger.info("Elapsed: %.1f min", elapsed_total / 60)
    logger.info("Verdict: %s", verdict)
    logger.info("Best exit config: %s", best_exit_cfg_name)
    logger.info("")

    logger.info("Walk-forward (best config: %s):", best_exit_cfg_name)
    logger.info("  avg daily PF=%.3f, robust PF=%.3f, median PF=%.3f",
                best_summary.get("avg_daily_pf", 0),
                best_summary.get("avg_daily_pf_robust", 0),
                best_summary.get("median_daily_pf", 0))
    logger.info("  consistency=%.1f%%, trades=%d",
                best_summary["consistency"] * 100,
                best_summary["total_trades"])

    logger.info("")
    logger.info("All exit configs:")
    for ecfg_name, sdata in wf_summary.items():
        logger.info("  %s: robust PF=%.3f, trades=%d",
                     ecfg_name, sdata["avg_daily_pf_robust"], sdata["total_trades"])

    if holdout_result:
        logger.info("")
        logger.info("Holdout (1.5x slippage):")
        logger.info("  avg PF=%.3f, robust PF=%.3f, median PF=%.3f, DSR=%.3f",
                     holdout_result["avg_daily_pf"],
                     holdout_result["avg_daily_pf_robust"],
                     holdout_result["median_daily_pf"],
                     holdout_result["dsr"])
        logger.info("  trades=%d, WR=%.1f%%, PnL=$%.2f",
                     holdout_result["n_trades"],
                     holdout_result["win_rate"] * 100,
                     holdout_result["total_pnl"])

        if holdout_result.get("event_pfs"):
            logger.info("  Per-event holdout PFs:")
            for etype, epf in sorted(holdout_result["event_pfs"].items()):
                n_e = holdout_result["event_counts"].get(etype, 0)
                logger.info("    %s: PF=%.3f (%d trades)", etype, epf, n_e)

    if ablation_results:
        logger.info("")
        logger.info("Event ablation:")
        for key, info in sorted(ablation_results.items()):
            logger.info("  %s: robust PF=%.3f (%d trades)",
                         key, info["avg_pf_robust"], info["n_trades"])

    if fail_reasons:
        logger.info("")
        logger.info("Fail reasons:")
        for r in fail_reasons:
            logger.info("  - %s", r)

    logger.info("")
    logger.info("Event frequency on full data:")
    for etype, fdata in freq_full.items():
        logger.info("  %s: %d events (%.2f%% of bar-symbol slots)",
                     etype, fdata["total_events"], fdata["pct_of_bar_symbol_slots"])


if __name__ == "__main__":
    main()
