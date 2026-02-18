#!/usr/bin/env python3
"""
Signal Genesis — Volatility-Gated Momentum Strategy
=====================================================

Hypothesis: Volatility features predict MAGNITUDE of moves, not direction.
Architecture separates "when to trade" (vol gate) from "which direction" (momentum).

Stage 1 — Volatility Gate: Cross-sectional z-score of 5 vol features, average.
    Top V% symbols pass the gate.
Stage 2 — Direction Filter: IC-weighted momentum signal among gated symbols.
    Positive direction → LONG, negative → SHORT (or top/bottom D%).

Only h24 horizon (best cost-to-edge ratio).

Usage:
    tmux new -s volgated
    docker compose run --rm -T worker python3 /scripts/signal_genesis_volgated.py --days 110
    # Quick smoke test:
    docker compose run --rm -T worker python3 /scripts/signal_genesis_volgated.py --days 30 --quick
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
from typing import Optional

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

# ─── BLAS thread control (must be before numpy import) ────────────────────────
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '2')
os.environ.setdefault('MKL_NUM_THREADS', '2')

# ─── Third-party ──────────────────────────────────────────────────────────────

import numpy as np
import psycopg
from exit_checker import ExitConfig, check_exit_simple as _check_exit_simple_orig
import inspect

# Compatibility wrapper: old exit_checker.py may not have 'config' param
_exit_simple_params = inspect.signature(_check_exit_simple_orig).parameters

def check_exit_simple(**kwargs):
    if 'config' in kwargs and 'config' not in _exit_simple_params:
        kwargs.pop('config')
    return _check_exit_simple_orig(**kwargs)

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

WARMUP_BARS = 100          # longest lookback (55) + buffer
POSITION_SIZE_USD = 1000.0
FEE_BPS = 4.0
MIN_SHORT_PRICE = 1.00
BTC_SYMBOL = "BTCUSDT"
BASE_SLIPPAGE_1H = 5.0     # bps for 1h timeframe
MIN_BARS_PER_SYMBOL = 100

# h24 only
HORIZON = 24
HORIZON_CONFIG = {
    "stop_atr_mult": 2.0,
    "time_stop_bars": 36,
    "trail_activation_r": 1.5,
    "trail_distance_r": 1.0,
    "trail_breakeven_r": 0.0,
}

# Volatility gate features (5 features, equal weight)
VOL_FEATURES = [
    "parkinson_vol_21",
    "garman_klass_21",
    "atr_14_norm",
    "range_pct",
    "vol_ratio_8_21",
]

# Direction features (2 features, IC-weighted)
DIR_FEATURES = [
    "ret_norm_5",
    "relative_strength_21",
]

# Parameter grid
V_GRID = [10, 20, 30, 40, 50]   # volatility gate percentile (top V%)
D_GRID = [20, 30, 40, 50]       # direction threshold within gated set

# Walk-forward config
N_FOLDS = 3
EMBARGO_DAYS = 1
HOLDOUT_DAYS = 18
MIN_TRADES_TOTAL = 25
MIN_FOLD_CONSISTENCY = 2 / 3  # 2/3 folds

# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol: str
    direction: str
    ts_idx: int
    score: float


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


@dataclass
class FoldResult:
    fold_idx: int
    horizon: int
    n_trades: int
    avg_daily_pf: float
    aggregate_pf: float
    win_rate: float
    total_pnl: float
    best_v: int
    best_d: int
    vol_features: list
    dir_features: list
    dir_ic_weights: list


@dataclass
class HorizonResult:
    horizon: int
    fold_results: list
    avg_daily_pf: float
    consistency: float  # fraction of folds with avg_daily_pf > 1.0
    total_trades: int
    holdout_avg_daily_pf: float = 0.0
    holdout_aggregate_pf: float = 0.0
    holdout_dsr: float = -99.0
    holdout_n_trades: int = 0
    holdout_best_v: int = 0
    holdout_best_d: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: DATA LOADING & MATRIX CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

def load_ohlcv_matrices(database_url: str, days: int):
    """Load 1h OHLCV and build aligned (n_bars, n_symbols) matrices.

    Returns: close, open_, high, low, volume matrices (float64),
             ts_grid (1d), symbols list, btc_idx (int or None), ohlcv_data dict
    """
    start_ts = datetime.now(timezone.utc) - timedelta(days=days + 15)  # extra for warmup
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

    # Build global timestamp grid from union of all timestamps
    all_ts = set()
    for rows in sym_rows.values():
        for r in rows:
            ts = r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None else r[1].timestamp()
            all_ts.add(ts)
    ts_grid = np.array(sorted(all_ts))
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)

    # Filter symbols with enough bars
    valid_symbols = []
    for sym, rows in sorted(sym_rows.items()):
        if len(rows) >= MIN_BARS_PER_SYMBOL:
            valid_symbols.append(sym)

    n_symbols = len(valid_symbols)
    logger.info("  %d symbols with >= %d bars, %d total bars in grid",
                n_symbols, MIN_BARS_PER_SYMBOL, n_bars)

    # Build matrices (float64 for price accuracy)
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

    # Forward-fill NaN gaps (symbols may have missing bars)
    for s_idx in range(n_symbols):
        for arr in [close, open_, high, low, volume]:
            col = arr[:, s_idx]
            mask = np.isnan(col)
            if mask.any() and not mask.all():
                idx = np.where(~mask, np.arange(n_bars), 0)
                np.maximum.accumulate(idx, out=idx)
                arr[:, s_idx] = col[idx]

    # Build per-symbol OHLCV dict for trade simulation (keyed by (symbol, '1h'))
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


def compute_forward_returns(close, horizon=HORIZON):
    """Compute forward returns for h24. Returns (n_bars, n_symbols) array."""
    n_bars, n_symbols = close.shape
    ret = np.full((n_bars, n_symbols), np.nan, dtype=np.float32)
    valid = (close[:-horizon] != 0) & ~np.isnan(close[:-horizon]) & ~np.isnan(close[horizon:])
    ret_vals = np.where(valid, (close[horizon:] - close[:-horizon]) / close[:-horizon], np.nan)
    ret[:n_bars - horizon] = ret_vals.astype(np.float32)
    logger.info("  Forward returns h=%d: %d valid bars", horizon, np.sum(~np.isnan(ret)))
    return ret


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: FEATURE ENGINEERING (only the 7 features we need)
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_sum_2d(arr, w):
    """Rolling sum over axis=0 for 2D array. Returns same shape, first w-1 rows are partial."""
    cs = np.cumsum(arr, axis=0)
    out = np.empty_like(arr)
    out[:w] = cs[:w]
    out[w:] = cs[w:] - cs[:-w]
    return out


def _rolling_mean_2d(arr, w):
    """Rolling mean over axis=0."""
    rs = _rolling_sum_2d(arr, w)
    out = rs / w
    out[:w-1] = np.nan
    return out


def _rolling_std_2d(arr, w):
    """Rolling std over axis=0."""
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


def _xs_zscore_2d(arr):
    """Cross-sectional z-score at each row."""
    mean = np.nanmean(arr, axis=1, keepdims=True)
    std = np.nanstd(arr, axis=1, keepdims=True)
    std = np.where(std > 0, std, 1.0)
    return (arr - mean) / std


def _atr_2d(high, low, close, n=14):
    """ATR for (n_bars, n_symbols) matrices."""
    prev_close = np.full_like(close, np.nan)
    prev_close[1:] = close[:-1]
    hl = high - low
    hc = np.abs(high - prev_close)
    lc = np.abs(low - prev_close)
    tr = np.maximum(hl, np.maximum(hc, lc))
    tr[0] = hl[0]
    return _rolling_mean_2d(tr, n)


def _eng_store(result, name, arr):
    """Store feature into result dict, converting to float32 and clamping infinities."""
    a = np.array(arr, dtype=np.float32, copy=True)
    a[~np.isfinite(a)] = np.nan
    result[name] = a


def engineer_features(close, open_, high, low, volume, ts_grid):
    """Engineer the 7 features needed for vol-gated momentum.

    Volatility features (5):
        parkinson_vol_21, garman_klass_21, atr_14_norm, range_pct, vol_ratio_8_21

    Direction features (2):
        ret_norm_5, relative_strength_21
    """
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    t0 = time.time()
    n_bars, n_symbols = close.shape
    features = {}

    safe_close = np.where(close > 0, close, 1e-10)
    bar_range = high - low

    # ── Returns (needed for direction features) ──
    ret_1 = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    ret_1[1:] = (close[1:] - close[:-1]) / safe_close[:-1]

    ret_5 = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    ret_5[5:] = (close[5:] - close[:-5]) / safe_close[:-5]

    ret_21 = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    ret_21[21:] = (close[21:] - close[:-21]) / safe_close[:-21]

    # ── ATR (needed for atr_14_norm and vol measures) ──
    atr_14 = _atr_2d(high, low, close, 14)

    # ── Volatility Feature 1: parkinson_vol_21 ──
    # Parkinson estimator: (1/(4*ln2)) * (ln(H/L))^2, then rolling mean over 21 bars
    log_hl = np.log(np.where(low > 0, high / low, 1.0))
    parkinson_var = log_hl**2 / (4 * np.log(2))
    parkinson_vol_21 = np.sqrt(_rolling_mean_2d(parkinson_var, 21))
    _eng_store(features, "parkinson_vol_21", parkinson_vol_21)
    logger.info("  parkinson_vol_21 computed")

    # ── Volatility Feature 2: garman_klass_21 ──
    # GK estimator: 0.5*(ln(H/L))^2 - (2*ln2-1)*(ln(C/O))^2
    log_co = np.log(np.where((open_ > 0) & (close > 0), close / open_, 1.0))
    gk_var = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    gk_var = np.maximum(gk_var, 0)  # clamp negative values
    garman_klass_21 = np.sqrt(_rolling_mean_2d(gk_var, 21))
    _eng_store(features, "garman_klass_21", garman_klass_21)
    logger.info("  garman_klass_21 computed")

    # ── Volatility Feature 3: atr_14_norm ──
    atr_14_norm = atr_14 / safe_close
    _eng_store(features, "atr_14_norm", atr_14_norm)
    logger.info("  atr_14_norm computed")

    # ── Volatility Feature 4: range_pct ──
    range_pct = bar_range / safe_close
    _eng_store(features, "range_pct", range_pct)
    logger.info("  range_pct computed")

    # ── Volatility Feature 5: vol_ratio_8_21 ──
    # Short-term vol / long-term vol (>1 = expanding)
    vol_8 = _rolling_std_2d(ret_1, 8)
    vol_21 = _rolling_std_2d(ret_1, 21)
    safe_vol_21 = np.where(vol_21 > 0, vol_21, 1e-10)
    vol_ratio_8_21 = vol_8 / safe_vol_21
    _eng_store(features, "vol_ratio_8_21", vol_ratio_8_21)
    logger.info("  vol_ratio_8_21 computed")

    # ── Direction Feature 1: ret_norm_5 ──
    # Vol-normalized 5-bar return
    safe_vol_21_dir = np.where(vol_21 > 0, vol_21, 1e-10)
    ret_norm_5 = ret_5 / safe_vol_21_dir
    _eng_store(features, "ret_norm_5", ret_norm_5)
    logger.info("  ret_norm_5 computed")

    # ── Direction Feature 2: relative_strength_21 ──
    # Return vs universe mean (cross-sectional momentum)
    universe_mean_ret_21 = np.nanmean(ret_21, axis=1, keepdims=True)
    relative_strength_21 = ret_21 - universe_mean_ret_21
    _eng_store(features, "relative_strength_21", relative_strength_21)
    logger.info("  relative_strength_21 computed")

    elapsed = time.time() - t0
    logger.info("  Feature engineering complete: %d features in %.1fs", len(features), elapsed)

    return features


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: VOLATILITY GATE & DIRECTION SIGNAL
# ═══════════════════════════════════════════════════════════════════════════════

def compute_vol_composite(features):
    """Build volatility composite: cross-sectional z-score of 5 vol features, equal weight average.

    Returns: (n_bars, n_symbols) float32 array — higher = more volatile (expected bigger move).
    """
    z_scores = []
    for fname in VOL_FEATURES:
        arr = features[fname]
        z = _xs_zscore_2d(arr.astype(np.float64))
        z_scores.append(z.astype(np.float32))

    # Equal weight average
    stacked = np.stack(z_scores, axis=0)  # (5, n_bars, n_symbols)
    # nanmean across features — a symbol needs at least 1 valid feature
    vol_composite = np.nanmean(stacked, axis=0)

    return vol_composite


def compute_direction_signal(features, fwd_ret, bar_mask):
    """Build IC-weighted direction signal from the 2 direction features.

    IC is computed on training data (bar_mask). Returns (n_bars, n_symbols) float32 array,
    plus the IC weights used.
    """
    # Compute IC (mean cross-sectional Spearman rank correlation with forward returns)
    # on training bars
    bar_indices = np.where(bar_mask)[0]
    ics = {}

    for fname in DIR_FEATURES:
        arr = features[fname]
        correlations = []
        for t in bar_indices:
            f_row = arr[t]
            r_row = fwd_ret[t]
            valid = ~np.isnan(f_row) & ~np.isnan(r_row)
            n_valid = valid.sum()
            if n_valid < 20:
                continue
            # Rank-based (Spearman) IC
            f_vals = f_row[valid]
            r_vals = r_row[valid]
            f_ranks = np.argsort(np.argsort(f_vals)).astype(np.float64)
            r_ranks = np.argsort(np.argsort(r_vals)).astype(np.float64)
            f_ranks -= f_ranks.mean()
            r_ranks -= r_ranks.mean()
            denom = np.sqrt(np.sum(f_ranks**2) * np.sum(r_ranks**2))
            if denom > 0:
                correlations.append(np.sum(f_ranks * r_ranks) / denom)

        if correlations:
            ics[fname] = float(np.mean(correlations))
        else:
            ics[fname] = 0.0

    logger.info("    Direction feature ICs: %s",
                {k: f"{v:.4f}" for k, v in ics.items()})

    # IC-weight: use absolute IC as weights, preserve sign
    total_abs_ic = sum(abs(v) for v in ics.values())
    if total_abs_ic == 0:
        # Fallback to equal weight
        weights = {fname: 1.0 / len(DIR_FEATURES) for fname in DIR_FEATURES}
    else:
        weights = {fname: ics[fname] / total_abs_ic for fname in DIR_FEATURES}

    logger.info("    Direction weights: %s",
                {k: f"{v:.4f}" for k, v in weights.items()})

    # Build weighted composite of cross-sectional z-scores
    n_bars, n_symbols = features[DIR_FEATURES[0]].shape
    direction_signal = np.zeros((n_bars, n_symbols), dtype=np.float64)

    for fname in DIR_FEATURES:
        z = _xs_zscore_2d(features[fname].astype(np.float64))
        direction_signal += weights[fname] * z

    ic_weights_list = [weights[fname] for fname in DIR_FEATURES]
    return direction_signal.astype(np.float32), ic_weights_list, ics


def generate_signals(vol_composite, direction_signal, bar_mask, symbols,
                     v_pct, d_pct):
    """Generate trading signals using vol gate + direction filter.

    Args:
        vol_composite: (n_bars, n_symbols) volatility composite scores
        direction_signal: (n_bars, n_symbols) direction signal scores
        bar_mask: (n_bars,) bool — which bars to generate signals on
        symbols: list of symbol names
        v_pct: top V% of symbols pass the vol gate
        d_pct: top/bottom D% of gated symbols get signals

    Returns: list of Signal objects
    """
    bar_indices = np.where(bar_mask)[0]
    signals = []
    n_symbols = len(symbols)

    for t in bar_indices:
        vol_row = vol_composite[t]
        dir_row = direction_signal[t]

        # Valid symbols at this bar
        valid = ~np.isnan(vol_row) & ~np.isnan(dir_row)
        n_valid = valid.sum()
        if n_valid < 10:
            continue

        # Stage 1: Volatility gate — top V% of valid symbols
        vol_vals = vol_row.copy()
        vol_vals[~valid] = -np.inf  # push invalids to bottom
        n_gated = max(1, int(n_valid * v_pct / 100))
        # Get indices of top n_gated symbols by volatility
        gated_idx = np.argpartition(vol_vals, -n_gated)[-n_gated:]
        # Filter out any that were invalid
        gated_idx = gated_idx[valid[gated_idx]]
        if len(gated_idx) == 0:
            continue

        # Stage 2: Direction filter among gated symbols
        dir_vals = dir_row[gated_idx]
        n_dir = max(1, int(len(gated_idx) * d_pct / 100))

        # Top D% → LONG (highest direction scores)
        if n_dir > 0 and len(gated_idx) >= 2:
            long_local = np.argpartition(dir_vals, -n_dir)[-n_dir:]
            for li in long_local:
                s_idx = gated_idx[li]
                if dir_vals[li] > 0:  # only LONG if direction is positive
                    signals.append(Signal(
                        symbol=symbols[s_idx],
                        direction="long",
                        ts_idx=t,
                        score=float(dir_vals[li]),
                    ))

            # Bottom D% → SHORT (lowest direction scores)
            short_local = np.argpartition(dir_vals, n_dir)[:n_dir]
            for si in short_local:
                s_idx = gated_idx[si]
                if dir_vals[si] < 0:  # only SHORT if direction is negative
                    signals.append(Signal(
                        symbol=symbols[s_idx],
                        direction="short",
                        ts_idx=t,
                        score=float(dir_vals[si]),
                    ))

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATION (adapted from signal_genesis.py, with stop-loss slippage fix)
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_atr_1d(highs, lows, closes, n=14):
    """ATR for 1D arrays."""
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


def _make_exit_config(cfg):
    """Build ExitConfig from horizon config dict."""
    ec = ExitConfig()
    for key in ("trail_breakeven_r", "trail_activation_r", "trail_distance_r"):
        if hasattr(ec, key):
            setattr(ec, key, cfg[key])
    return ec


def simulate_trades(signals, ohlcv_data, stop_atr_mult, time_stop_bars,
                    slippage_bps, exit_config=None):
    """Simulate signals to trades. Applies slippage to ALL exits including STOP_LOSS."""
    if exit_config is None:
        exit_config = ExitConfig()
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

        # ATR at signal bar
        atr = _rolling_atr_1d(highs[:i+1], lows[:i+1], closes[:i+1], 14)[-1]
        if atr <= 0:
            continue

        # Entry at next bar open + slippage
        entry_raw = opens[i + 1]
        if sig.direction == "long":
            entry = entry_raw * (1 + slippage_bps / 10000)
            stop = entry - stop_atr_mult * atr
        else:
            entry = entry_raw * (1 - slippage_bps / 10000)
            stop = entry + stop_atr_mult * atr
            if entry < MIN_SHORT_PRICE:
                continue

        if stop <= 0 or entry <= 0:
            continue
        r_value = abs(entry - stop)
        if r_value <= 0 or r_value / entry > 0.10:
            continue
        if 2 * entry * FEE_BPS / 10000 > 0.3 * r_value:
            continue

        max_idx = min(i + 2 + time_stop_bars + 20, n)  # extra buffer beyond time stop
        hwm = None
        half_r_touched = False
        bars_held = 0
        exit_reason = "TIME_STOP"
        exit_price = closes[min(max_idx - 1, n - 1)]

        for j in range(i + 2, max_idx):
            bars_held += 1
            bar = {"open": opens[j], "high": highs[j], "low": lows[j], "close": closes[j]}
            result, new_hwm, new_half_r = check_exit_simple(
                side=sig.direction, bar=bar,
                entry_price=entry, stop_price=stop,
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

        # PnL with exit slippage — APPLIED TO ALL EXITS INCLUDING STOP_LOSS
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
            pnl=pnl_net, r_mult=r_mult, exit_reason=exit_reason, bars_held=bars_held,
        ))

    return results


def compute_avg_daily_pf(trades, ohlcv_data, symbols):
    """Compute average daily profit factor. PF per day, then average."""
    if not trades:
        return 0.0

    # Get timestamps for each trade
    ts_grid = ohlcv_data[(symbols[0], '1h')]["ts"]

    # Group trades by day
    daily = defaultdict(lambda: {"wins": 0.0, "losses": 0.0})
    for t in trades:
        ts = ts_grid[t.ts_idx]
        day = int(ts // 86400)
        if t.pnl > 0:
            daily[day]["wins"] += t.pnl
        elif t.pnl < 0:
            daily[day]["losses"] += abs(t.pnl)

    if not daily:
        return 0.0

    pfs = []
    for day_data in daily.values():
        w = day_data["wins"]
        l = day_data["losses"]
        if l > 0:
            pfs.append(w / l)
        elif w > 0:
            pfs.append(10.0)  # cap at 10 for days with only wins
        # Skip days with no trades

    if not pfs:
        return 0.0

    return float(np.mean(pfs))


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
# PHASE 4: FOLD CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

def build_folds(ts_grid, n_folds=N_FOLDS, holdout_days=HOLDOUT_DAYS, embargo_days=EMBARGO_DAYS):
    """Build expanding window folds from timestamp grid.

    Returns: list of (train_mask, test_mask) bool arrays, holdout_mask
    """
    # Convert to days since start
    day_start = ts_grid[0] // 86400
    day_end = ts_grid[-1] // 86400
    total_days = int(day_end - day_start) + 1

    logger.info("  Total span: %d days (%s to %s)",
                total_days,
                datetime.fromtimestamp(ts_grid[0], tz=timezone.utc).strftime("%Y-%m-%d"),
                datetime.fromtimestamp(ts_grid[-1], tz=timezone.utc).strftime("%Y-%m-%d"))

    # Guard: need enough days for at least 1 fold + holdout
    min_required = holdout_days + 2 * 5 + embargo_days
    if total_days < min_required:
        logger.error("  Insufficient data: %d days available, need >= %d (holdout=%d + folds). "
                     "Increase --days or check DB has 1h OHLCV data.", total_days, min_required, holdout_days)
        return [], np.zeros(len(ts_grid), dtype=bool)

    # Reserve holdout at the end
    holdout_start_day = total_days - holdout_days
    train_test_days = holdout_start_day

    # Divide train_test into folds (expanding window)
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

        # Convert days to bar masks
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

    # Holdout mask
    holdout_mask = np.zeros(len(ts_grid), dtype=bool)
    for i, ts in enumerate(ts_grid):
        day = int(ts // 86400 - day_start)
        if day >= holdout_start_day:
            holdout_mask[i] = True

    logger.info("    Holdout: days [%d, %d) = %d bars",
                holdout_start_day, total_days, holdout_mask.sum())

    return folds, holdout_mask


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def optimize_params_on_train(vol_composite, direction_features, fwd_ret,
                             train_mask, symbols, ohlcv_data, exit_config):
    """Optimize V (vol gate) and D (direction threshold) on training data.

    Uses a mini-validation split: last 20% of training data for validation.
    Returns: (best_v, best_d, best_pf, ic_weights, ics)
    """
    # Split training into train-proper and mini-val
    train_indices = np.where(train_mask)[0]
    split_idx = int(len(train_indices) * 0.8)
    train_proper_bars = train_indices[:split_idx]
    val_bars = train_indices[split_idx:]

    # Build train-proper mask for IC computation
    train_proper_mask = np.zeros(len(train_mask), dtype=bool)
    train_proper_mask[train_proper_bars] = True

    # Build val mask
    val_mask = np.zeros(len(train_mask), dtype=bool)
    val_mask[val_bars] = True

    # Compute direction signal (IC-weighted) on train-proper
    direction_signal, ic_weights, ics = compute_direction_signal(
        direction_features, fwd_ret, train_proper_mask
    )

    best_v = V_GRID[len(V_GRID) // 2]  # default middle
    best_d = D_GRID[len(D_GRID) // 2]
    best_pf = 0.0

    for v in V_GRID:
        for d in D_GRID:
            signals = generate_signals(
                vol_composite, direction_signal, val_mask, symbols, v, d
            )
            if len(signals) < 5:
                continue

            trades = simulate_trades(
                signals, ohlcv_data,
                HORIZON_CONFIG["stop_atr_mult"],
                HORIZON_CONFIG["time_stop_bars"],
                BASE_SLIPPAGE_1H, exit_config,
            )
            if len(trades) < 3:
                continue

            avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
            if avg_pf > best_pf:
                best_pf = avg_pf
                best_v = v
                best_d = d

    logger.info("      Param optimization: best V=%d%%, D=%d%% (avg daily PF=%.3f)",
                best_v, best_d, best_pf)

    return best_v, best_d, best_pf, ic_weights, ics


def process_fold(fold_idx, folds, vol_composite, features, fwd_ret,
                 symbols, ohlcv_data, exit_config):
    """Process a single walk-forward fold.

    1. Optimize V, D on training data (with mini-val split)
    2. Compute direction IC on full training data
    3. Generate signals on test data
    4. Simulate trades and compute metrics

    Returns: FoldResult
    """
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    t0 = time.time()
    train_mask, test_mask = folds[fold_idx]

    logger.info("  Fold %d: optimizing params on training data...", fold_idx)

    # Optimize V and D on training data
    best_v, best_d, train_pf, ic_weights_train, ics_train = optimize_params_on_train(
        vol_composite, features, fwd_ret, train_mask, symbols, ohlcv_data, exit_config
    )

    # Recompute direction signal on FULL training data (not mini-val split)
    direction_signal, ic_weights, ics = compute_direction_signal(
        features, fwd_ret, train_mask
    )

    # Generate signals on TEST data using optimized params
    test_signals = generate_signals(
        vol_composite, direction_signal, test_mask, symbols, best_v, best_d
    )
    logger.info("    Fold %d: %d signals on test period (V=%d%%, D=%d%%)",
                fold_idx, len(test_signals), best_v, best_d)

    # Simulate trades
    trades = simulate_trades(
        test_signals, ohlcv_data,
        HORIZON_CONFIG["stop_atr_mult"],
        HORIZON_CONFIG["time_stop_bars"],
        BASE_SLIPPAGE_1H, exit_config,
    )

    # Compute metrics
    if trades:
        avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
        total_pnl = sum(t.pnl for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        win_rate = wins / len(trades)
        agg_pf = compute_aggregate_pf(trades)

        # Trade breakdown by exit reason
        exit_counts = defaultdict(int)
        for t in trades:
            exit_counts[t.exit_reason] += 1
        exit_breakdown = dict(exit_counts)

        # Direction breakdown
        long_trades = [t for t in trades if t.direction == "long"]
        short_trades = [t for t in trades if t.direction == "short"]
        long_pnl = sum(t.pnl for t in long_trades)
        short_pnl = sum(t.pnl for t in short_trades)
    else:
        avg_pf = 0.0
        total_pnl = 0.0
        win_rate = 0.0
        agg_pf = 0.0
        exit_breakdown = {}
        long_pnl = 0.0
        short_pnl = 0.0

    elapsed = time.time() - t0
    logger.info("    Fold %d: %d trades, avg daily PF=%.3f, agg PF=%.3f, WR=%.1f%%, "
                "PnL=$%.2f (L=$%.2f S=$%.2f), exits=%s (%.1fs)",
                fold_idx, len(trades), avg_pf, agg_pf, win_rate * 100,
                total_pnl, long_pnl, short_pnl, exit_breakdown, elapsed)

    fold_result = FoldResult(
        fold_idx=fold_idx, horizon=HORIZON,
        n_trades=len(trades), avg_daily_pf=avg_pf, aggregate_pf=agg_pf,
        win_rate=win_rate, total_pnl=total_pnl,
        best_v=best_v, best_d=best_d,
        vol_features=VOL_FEATURES,
        dir_features=DIR_FEATURES,
        dir_ic_weights=ic_weights,
    )

    return fold_result


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: HOLDOUT & DSR
# ═══════════════════════════════════════════════════════════════════════════════

def deflated_sharpe_ratio(observed_sr, n_trials, n_observations, skew=0, kurtosis=3):
    """Harvey, Liu, Zhu (2016) — adjusts for multiple testing."""
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


def run_holdout(vol_composite, features, fwd_ret, folds, holdout_mask,
                symbols, ohlcv_data, exit_config, n_trials):
    """Run holdout evaluation.

    Uses all pre-holdout data for IC computation and param optimization.
    Tests on holdout at 1.5x slippage.
    """
    # Full training mask = everything before holdout
    full_train_mask = np.zeros(len(holdout_mask), dtype=bool)
    holdout_start = np.where(holdout_mask)[0]
    if len(holdout_start) > 0:
        full_train_mask[:holdout_start[0]] = True
    else:
        return None

    logger.info("  Holdout: param optimization on %d training bars...", full_train_mask.sum())

    # Optimize V, D on full training data
    best_v, best_d, train_pf, ic_weights_opt, ics_opt = optimize_params_on_train(
        vol_composite, features, fwd_ret, full_train_mask, symbols, ohlcv_data, exit_config
    )

    # Recompute direction signal on full training data
    direction_signal, ic_weights, ics = compute_direction_signal(
        features, fwd_ret, full_train_mask
    )

    # Generate signals on holdout
    holdout_signals = generate_signals(
        vol_composite, direction_signal, holdout_mask, symbols, best_v, best_d
    )
    logger.info("  Holdout: %d signals generated (V=%d%%, D=%d%%)",
                len(holdout_signals), best_v, best_d)

    # Test at 1.5x slippage
    slippage_15x = BASE_SLIPPAGE_1H * 1.5
    trades = simulate_trades(
        holdout_signals, ohlcv_data,
        HORIZON_CONFIG["stop_atr_mult"],
        HORIZON_CONFIG["time_stop_bars"],
        slippage_15x, exit_config,
    )

    if not trades:
        logger.info("  Holdout: 0 trades")
        return None

    avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades)
    agg_pf = compute_aggregate_pf(trades)

    # Sharpe ratio for DSR
    pnl_arr = np.array([t.pnl for t in trades])
    n_obs = len(pnl_arr)
    mean_pnl = np.mean(pnl_arr)
    std_pnl = np.std(pnl_arr, ddof=1)
    sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

    # Skew and kurtosis for DSR
    if n_obs > 2 and std_pnl > 0:
        z_pnl = (pnl_arr - mean_pnl) / std_pnl
        m3 = np.mean(z_pnl**3)
        m4 = np.mean(z_pnl**4)
        skew = float(m3 * (n_obs * (n_obs - 1))**0.5 / (n_obs - 2)) if n_obs > 2 else 0
        kurt = float(m4)
    else:
        skew, kurt = 0.0, 3.0

    dsr = deflated_sharpe_ratio(sharpe, n_trials, n_obs, skew, kurt)

    # Exit breakdown
    exit_counts = defaultdict(int)
    for t in trades:
        exit_counts[t.exit_reason] += 1

    # Direction breakdown
    long_trades = [t for t in trades if t.direction == "long"]
    short_trades = [t for t in trades if t.direction == "short"]
    long_pnl = sum(t.pnl for t in long_trades)
    short_pnl = sum(t.pnl for t in short_trades)

    logger.info("  Holdout (1.5x slip): %d trades, avg daily PF=%.3f, agg PF=%.3f, "
                "WR=%.1f%%, Sharpe=%.3f, DSR=%.3f, PnL=$%.2f (L=$%.2f S=$%.2f)",
                len(trades), avg_pf, agg_pf, win_rate * 100, sharpe, dsr,
                total_pnl, long_pnl, short_pnl)
    logger.info("    Exit breakdown: %s", dict(exit_counts))

    return {
        "avg_daily_pf": avg_pf,
        "aggregate_pf": agg_pf,
        "dsr": dsr,
        "n_trades": len(trades),
        "sharpe": sharpe,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "best_v": best_v,
        "best_d": best_d,
        "ic_weights": ic_weights,
        "ics": ics,
        "exit_breakdown": dict(exit_counts),
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_pnl": long_pnl,
        "short_pnl": short_pnl,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 7: 2x SLIPPAGE SENSITIVITY TEST
# ═══════════════════════════════════════════════════════════════════════════════

def run_slippage_sensitivity(vol_composite, features, fwd_ret, holdout_mask,
                             symbols, ohlcv_data, exit_config, best_v, best_d):
    """Run 2x slippage sensitivity test on holdout."""
    full_train_mask = np.zeros(len(holdout_mask), dtype=bool)
    holdout_start = np.where(holdout_mask)[0]
    if len(holdout_start) > 0:
        full_train_mask[:holdout_start[0]] = True
    else:
        return None

    direction_signal, _, _ = compute_direction_signal(features, fwd_ret, full_train_mask)

    holdout_signals = generate_signals(
        vol_composite, direction_signal, holdout_mask, symbols, best_v, best_d
    )

    slippage_2x = BASE_SLIPPAGE_1H * 2.0
    trades = simulate_trades(
        holdout_signals, ohlcv_data,
        HORIZON_CONFIG["stop_atr_mult"],
        HORIZON_CONFIG["time_stop_bars"],
        slippage_2x, exit_config,
    )

    if not trades:
        return {"avg_daily_pf": 0.0, "n_trades": 0}

    avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
    agg_pf = compute_aggregate_pf(trades)
    logger.info("  2.0x slippage test: %d trades, avg daily PF=%.3f, agg PF=%.3f",
                len(trades), avg_pf, agg_pf)

    return {"avg_daily_pf": avg_pf, "aggregate_pf": agg_pf, "n_trades": len(trades)}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Signal Genesis — Volatility-Gated Momentum Strategy"
    )
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: 2 folds, fewer param combos")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS,
                        help="Days reserved for holdout")
    parser.add_argument("--n-folds", type=int, default=N_FOLDS,
                        help="Number of walk-forward folds")
    args = parser.parse_args()

    t_start = time.time()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    n_folds = 2 if args.quick else args.n_folds
    v_grid = V_GRID if not args.quick else [20, 40]
    d_grid = D_GRID if not args.quick else [30, 50]

    logger.info("=" * 70)
    logger.info("SIGNAL GENESIS — Volatility-Gated Momentum Strategy")
    logger.info("=" * 70)
    logger.info("Config: days=%d, horizon=h%d, folds=%d, holdout=%d days, quick=%s",
                args.days, HORIZON, n_folds, args.holdout_days, args.quick)
    logger.info("Vol features: %s", VOL_FEATURES)
    logger.info("Dir features: %s", DIR_FEATURES)
    logger.info("V grid: %s, D grid: %s", v_grid, d_grid)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    jsonl_path = os.path.join(script_dir, "signal_genesis_volgated_incremental.jsonl")
    results_path = os.path.join(script_dir, "signal_genesis_volgated_results.json")

    # ── Phase 1: Data Loading ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 1: Data Loading & Matrix Construction")
    logger.info("-" * 50)

    close, open_, high, low, volume, ts_grid, symbols, btc_idx, ohlcv_data = \
        load_ohlcv_matrices(database_url, args.days)

    logger.info("Computing forward returns h=%d...", HORIZON)
    fwd_ret = compute_forward_returns(close, HORIZON)

    # ── Phase 2: Feature Engineering ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 2: Feature Engineering (7 features)")
    logger.info("-" * 50)

    features = engineer_features(close, open_, high, low, volume, ts_grid)

    # ── Phase 3: Volatility Composite ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 3: Building Volatility Composite")
    logger.info("-" * 50)

    vol_composite = compute_vol_composite(features)

    # Diagnostic: correlation of abs(forward return) with vol composite
    abs_fwd = np.abs(fwd_ret)
    # Flatten valid bars for correlation check
    valid_both = ~np.isnan(vol_composite) & ~np.isnan(abs_fwd)
    if valid_both.sum() > 100:
        vc_flat = vol_composite[valid_both].astype(np.float64)
        af_flat = abs_fwd[valid_both].astype(np.float64)
        corr = np.corrcoef(vc_flat, af_flat)[0, 1]
        logger.info("  Vol composite vs |fwd_ret_h24| correlation: %.4f", corr)
    else:
        logger.info("  Not enough valid data for correlation check")

    # ── Phase 4: Build Folds ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 4: Building Walk-Forward Folds")
    logger.info("-" * 50)

    folds, holdout_mask = build_folds(ts_grid, n_folds=n_folds, holdout_days=args.holdout_days)

    if not folds:
        logger.error("No folds could be constructed. Need more data (try --days 110).")
        sys.exit(1)

    exit_config = _make_exit_config(HORIZON_CONFIG)

    # ── Phase 5: Walk-Forward Validation ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 5: Walk-Forward Validation (%d folds)", len(folds))
    logger.info("-" * 50)

    fold_results = []
    for fold_idx in range(len(folds)):
        fold_t0 = time.time()
        elapsed_so_far = time.time() - t_start
        if fold_idx > 0:
            avg_fold_time = elapsed_so_far / fold_idx
            remaining = avg_fold_time * (len(folds) - fold_idx)
            logger.info("\n  [ETA: %.0fs remaining for %d more folds]",
                        remaining, len(folds) - fold_idx)

        fr = process_fold(
            fold_idx, folds, vol_composite, features, fwd_ret,
            symbols, ohlcv_data, exit_config
        )
        fold_results.append(fr)

        # Write incremental JSONL
        fold_data = {
            "type": "walk_forward_fold",
            "fold_idx": fold_idx,
            "horizon": HORIZON,
            "n_trades": fr.n_trades,
            "avg_daily_pf": fr.avg_daily_pf,
            "aggregate_pf": fr.aggregate_pf,
            "win_rate": fr.win_rate,
            "total_pnl": fr.total_pnl,
            "best_v": fr.best_v,
            "best_d": fr.best_d,
            "dir_ic_weights": fr.dir_ic_weights,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(fold_data, default=str) + "\n")

        fold_elapsed = time.time() - fold_t0
        logger.info("    Fold %d complete in %.1fs", fold_idx, fold_elapsed)

    # Aggregate fold results
    valid_folds = [fr for fr in fold_results if fr.n_trades > 0]
    if valid_folds:
        avg_daily_pf = float(np.mean([fr.avg_daily_pf for fr in valid_folds]))
        consistency = sum(1 for fr in valid_folds if fr.avg_daily_pf > 1.0) / len(valid_folds)
        total_trades = sum(fr.n_trades for fr in valid_folds)
    else:
        avg_daily_pf = 0.0
        consistency = 0.0
        total_trades = 0

    hr = HorizonResult(
        horizon=HORIZON,
        fold_results=fold_results,
        avg_daily_pf=avg_daily_pf,
        consistency=consistency,
        total_trades=total_trades,
    )

    logger.info("\n  Walk-forward summary h=%d: avg daily PF=%.3f, consistency=%.1f%% (%d/%d folds), trades=%d",
                HORIZON, avg_daily_pf, consistency * 100,
                sum(1 for fr in valid_folds if fr.avg_daily_pf > 1.0),
                len(valid_folds), total_trades)

    # Write WF summary incremental
    wf_summary = {
        "type": "walk_forward_summary",
        "horizon": HORIZON,
        "avg_daily_pf": avg_daily_pf,
        "consistency": consistency,
        "total_trades": total_trades,
        "n_folds": len(valid_folds),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(wf_summary, default=str) + "\n")

    # ── Phase 6: Holdout ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 6: Holdout Evaluation")
    logger.info("-" * 50)

    wf_pass = (avg_daily_pf > 1.0
               and consistency >= MIN_FOLD_CONSISTENCY
               and total_trades >= MIN_TRADES_TOTAL)

    holdout_result = None
    slippage_2x_result = None

    if not wf_pass:
        logger.info("  SKIPPING holdout (walk-forward failed: PF=%.3f, cons=%.1f%%, trades=%d)",
                    avg_daily_pf, consistency * 100, total_trades)
        reasons = []
        if avg_daily_pf <= 1.0:
            reasons.append(f"avg_daily_pf={avg_daily_pf:.3f} <= 1.0")
        if consistency < MIN_FOLD_CONSISTENCY:
            reasons.append(f"consistency={consistency:.2f} < {MIN_FOLD_CONSISTENCY:.2f}")
        if total_trades < MIN_TRADES_TOTAL:
            reasons.append(f"trades={total_trades} < {MIN_TRADES_TOTAL}")
        logger.info("  Fail reasons: %s", "; ".join(reasons))
    else:
        # Number of trials = V_GRID * D_GRID combinations
        n_trials = len(V_GRID) * len(D_GRID)

        holdout_result = run_holdout(
            vol_composite, features, fwd_ret, folds, holdout_mask,
            symbols, ohlcv_data, exit_config, n_trials
        )

        if holdout_result:
            hr.holdout_avg_daily_pf = holdout_result["avg_daily_pf"]
            hr.holdout_aggregate_pf = holdout_result["aggregate_pf"]
            hr.holdout_dsr = holdout_result["dsr"]
            hr.holdout_n_trades = holdout_result["n_trades"]
            hr.holdout_best_v = holdout_result["best_v"]
            hr.holdout_best_d = holdout_result["best_d"]

            # 2x slippage sensitivity
            slippage_2x_result = run_slippage_sensitivity(
                vol_composite, features, fwd_ret, holdout_mask,
                symbols, ohlcv_data, exit_config,
                holdout_result["best_v"], holdout_result["best_d"]
            )

            # Write holdout incremental
            ho_data = {
                "type": "holdout",
                "horizon": HORIZON,
                **{k: v for k, v in holdout_result.items()},
                "slippage_2x": slippage_2x_result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(ho_data, default=str) + "\n")

    # ── Phase 7: Final Report ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 7: Final Report")
    logger.info("-" * 50)

    elapsed_total = time.time() - t_start

    # Determine verdict
    if not wf_pass:
        verdict = "FAIL_WALK_FORWARD"
        fail_reasons = []
        if avg_daily_pf <= 1.0:
            fail_reasons.append(f"avg_daily_pf={avg_daily_pf:.3f} <= 1.0")
        if consistency < MIN_FOLD_CONSISTENCY:
            fail_reasons.append(f"consistency={consistency:.2f} < {MIN_FOLD_CONSISTENCY:.2f}")
        if total_trades < MIN_TRADES_TOTAL:
            fail_reasons.append(f"trades={total_trades} < {MIN_TRADES_TOTAL}")
    elif holdout_result is None or holdout_result["avg_daily_pf"] <= 1.0:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = ["holdout avg_daily_pf <= 1.0"]
    elif holdout_result["dsr"] <= 0:
        verdict = "FAIL_DSR"
        fail_reasons = [f"DSR={holdout_result['dsr']:.3f} <= 0"]
    else:
        verdict = "CONFIRMED"
        fail_reasons = []

    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed_total,
        "strategy": "volatility_gated_momentum",
        "hypothesis": ("Volatility features predict magnitude of moves, not direction. "
                       "Stage 1 (vol gate) selects high-expected-move symbols. "
                       "Stage 2 (direction filter) determines long/short via momentum."),
        "vol_features": VOL_FEATURES,
        "dir_features": DIR_FEATURES,
        "param_grid": {"V": V_GRID, "D": D_GRID},
        "horizon": HORIZON,
        "exit_config": {
            "stop_atr_mult": HORIZON_CONFIG["stop_atr_mult"],
            "time_stop_bars": HORIZON_CONFIG["time_stop_bars"],
            "trail_activation_r": HORIZON_CONFIG["trail_activation_r"],
            "trail_distance_r": HORIZON_CONFIG["trail_distance_r"],
            "trail_breakeven_r": HORIZON_CONFIG["trail_breakeven_r"],
            "slippage_bps": BASE_SLIPPAGE_1H,
            "fee_bps": FEE_BPS,
            "slippage_on_all_exits": True,
        },
        "walk_forward": {
            "n_folds": len(valid_folds),
            "avg_daily_pf": avg_daily_pf,
            "consistency": consistency,
            "total_trades": total_trades,
            "folds": [asdict(fr) for fr in fold_results],
        },
        "holdout": {
            "avg_daily_pf": holdout_result["avg_daily_pf"] if holdout_result else 0.0,
            "aggregate_pf": holdout_result["aggregate_pf"] if holdout_result else 0.0,
            "dsr": holdout_result["dsr"] if holdout_result else -99.0,
            "n_trades": holdout_result["n_trades"] if holdout_result else 0,
            "sharpe": holdout_result["sharpe"] if holdout_result else 0.0,
            "win_rate": holdout_result["win_rate"] if holdout_result else 0.0,
            "total_pnl": holdout_result["total_pnl"] if holdout_result else 0.0,
            "best_v": holdout_result["best_v"] if holdout_result else 0,
            "best_d": holdout_result["best_d"] if holdout_result else 0,
            "ic_weights": holdout_result["ic_weights"] if holdout_result else [],
            "ics": holdout_result["ics"] if holdout_result else {},
            "exit_breakdown": holdout_result["exit_breakdown"] if holdout_result else {},
            "long_trades": holdout_result["long_trades"] if holdout_result else 0,
            "short_trades": holdout_result["short_trades"] if holdout_result else 0,
            "long_pnl": holdout_result["long_pnl"] if holdout_result else 0.0,
            "short_pnl": holdout_result["short_pnl"] if holdout_result else 0.0,
        },
        "slippage_2x": slippage_2x_result if slippage_2x_result else {
            "avg_daily_pf": 0.0, "n_trades": 0
        },
        "verdict": verdict,
        "fail_reasons": fail_reasons,
    }

    with open(results_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Results written to %s", results_path)

    # Print summary
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info("Elapsed: %.1f min", elapsed_total / 60)
    logger.info("Strategy: Volatility-Gated Momentum (h%d)", HORIZON)
    logger.info("Verdict: %s", verdict)
    logger.info("")
    logger.info("Walk-forward (h%d):", HORIZON)
    logger.info("  Avg daily PF: %.3f", avg_daily_pf)
    logger.info("  Consistency: %.1f%% (%d/%d folds)", consistency * 100,
                sum(1 for fr in valid_folds if fr.avg_daily_pf > 1.0),
                len(valid_folds))
    logger.info("  Total trades: %d", total_trades)

    for fr in fold_results:
        logger.info("  Fold %d: avg PF=%.3f, agg PF=%.3f, trades=%d, V=%d%%, D=%d%%",
                    fr.fold_idx, fr.avg_daily_pf, fr.aggregate_pf,
                    fr.n_trades, fr.best_v, fr.best_d)

    if holdout_result:
        logger.info("")
        logger.info("Holdout (1.5x slippage):")
        logger.info("  Avg daily PF: %.3f", holdout_result["avg_daily_pf"])
        logger.info("  Aggregate PF: %.3f", holdout_result["aggregate_pf"])
        logger.info("  DSR: %.3f", holdout_result["dsr"])
        logger.info("  Sharpe: %.3f", holdout_result["sharpe"])
        logger.info("  Trades: %d (L=%d, S=%d)",
                    holdout_result["n_trades"],
                    holdout_result["long_trades"],
                    holdout_result["short_trades"])
        logger.info("  PnL: $%.2f (L=$%.2f, S=$%.2f)",
                    holdout_result["total_pnl"],
                    holdout_result["long_pnl"],
                    holdout_result["short_pnl"])
        logger.info("  Best params: V=%d%%, D=%d%%",
                    holdout_result["best_v"], holdout_result["best_d"])
        logger.info("  ICs: %s", {k: f"{v:.4f}" for k, v in holdout_result["ics"].items()})

    if slippage_2x_result and slippage_2x_result.get("n_trades", 0) > 0:
        logger.info("  2x slippage: avg daily PF=%.3f, trades=%d",
                    slippage_2x_result["avg_daily_pf"],
                    slippage_2x_result["n_trades"])

    if fail_reasons:
        logger.info("")
        logger.info("Fail reasons: %s", "; ".join(fail_reasons))

    # Diagnostic: per-fold parameter stability
    if len(fold_results) > 1:
        vs = [fr.best_v for fr in fold_results if fr.n_trades > 0]
        ds = [fr.best_d for fr in fold_results if fr.n_trades > 0]
        if vs:
            logger.info("")
            logger.info("Parameter stability:")
            logger.info("  V values: %s (spread: %d)", vs, max(vs) - min(vs))
            logger.info("  D values: %s (spread: %d)", ds, max(ds) - min(ds))

    logger.info("\n" + "=" * 70)
    logger.info("Done.")


if __name__ == "__main__":
    main()
