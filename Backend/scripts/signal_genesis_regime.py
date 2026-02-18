#!/usr/bin/env python3
"""
Signal Genesis — Regime-Conditional Sparse Model
==================================================

Hypothesis: The edge from genesis features is conditional on market regime.
Use market-level features (breadth, dispersion, concentration, vol trend)
to gate when to trade, and a sparse 6-feature IC-weighted model for direction.

Only h24 is tested. 3 expanding folds + 18-day holdout.

Usage:
    tmux new -s regime
    docker compose run --rm -T worker python3 /scripts/signal_genesis_regime.py --days 110
"""

# ─── Standard library ────────────────────────────────────────────────────────

import os
import sys
import time
import json
import math
import argparse
import logging
from dataclasses import dataclass, asdict
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
os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')
os.environ.setdefault('MKL_NUM_THREADS', '4')

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

WARMUP_BARS = 100
POSITION_SIZE_USD = 1000.0
FEE_BPS = 4.0
MIN_SHORT_PRICE = 1.00
BTC_SYMBOL = "BTCUSDT"
BASE_SLIPPAGE_1H = 5.0
MIN_BARS_PER_SYMBOL = 100

# h24-only configuration
HORIZON = 24
HORIZON_CONFIG = {
    "stop_atr_mult": 2.0,
    "time_stop_bars": 36,
    "trail_activation_r": 1.5,
    "trail_distance_r": 1.0,
    "trail_breakeven_r": 0.0,
}

# Sparse signal features (6 that appeared in ALL folds of ALL horizons in genesis)
SIGNAL_FEATURES = [
    "relative_strength_21",
    "ret_norm_5",
    "xs_ret_rank_5",
    "pos_in_range_21",
    "efficiency_ratio_8",
    "momentum_quality_divergence",
]

# Regime gate: percentile thresholds to optimize
REGIME_THRESHOLDS = [30, 40, 50, 60, 70]  # percentile of regime_score

# Walk-forward config
N_FOLDS = 3
EMBARGO_DAYS = 1
HOLDOUT_DAYS = 18
K_PCT = 2  # fixed from genesis (top/bottom 2%)
MIN_TRADES_TOTAL = 25


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
    n_features: int
    feature_names: list
    n_trades: int
    avg_daily_pf: float
    aggregate_pf: float
    win_rate: float
    total_pnl: float
    regime_threshold: int
    regime_open_fraction: float
    regime_score_mean: float
    regime_score_std: float
    n_signals_before_gate: int
    n_signals_after_gate: int


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: DATA LOADING & MATRIX CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

def load_ohlcv_matrices(database_url: str, days: int):
    """Load 1h OHLCV and build aligned (n_bars, n_symbols) matrices.

    Returns: close, open_, high, low, volume matrices (float64),
             ts_grid (1d), symbols list, btc_idx (int or None), ohlcv_data
    """
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

    # Build matrices
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


def compute_forward_returns(close, horizon=HORIZON):
    """Compute forward returns for h24. Returns (n_bars, n_symbols) array."""
    n_bars, n_symbols = close.shape
    h = horizon
    ret = np.full((n_bars, n_symbols), np.nan, dtype=np.float32)
    valid = (close[:-h] != 0) & ~np.isnan(close[:-h]) & ~np.isnan(close[h:])
    ret_vals = np.where(valid, (close[h:] - close[:-h]) / close[:-h], np.nan)
    ret[:n_bars - h] = ret_vals.astype(np.float32)
    logger.info("  Forward returns h=%d: %d valid bars", h, np.sum(~np.isnan(ret)))
    return ret


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: FEATURE ENGINEERING (only ~10 features: 4 regime + 6 signal)
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_sum_2d(arr, w):
    """Rolling sum over axis=0 for 2D array."""
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


def _rolling_max_2d(arr, w):
    """Rolling max over axis=0."""
    from numpy.lib.stride_tricks import sliding_window_view
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w-1:] = np.max(sliding_window_view(arr, w, axis=0), axis=-1)
    return out


def _rolling_min_2d(arr, w):
    """Rolling min over axis=0."""
    from numpy.lib.stride_tricks import sliding_window_view
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w-1:] = np.min(sliding_window_view(arr, w, axis=0), axis=-1)
    return out


def _rolling_slope_2d(arr, w):
    """Rolling OLS slope over axis=0."""
    n_val = w
    i_vals = np.arange(n_val, dtype=np.float64)
    sum_i = i_vals.sum()
    sum_i2 = (i_vals**2).sum()
    denom = n_val * sum_i2 - sum_i**2
    if denom == 0:
        return np.full_like(arr, np.nan)

    n_bars = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n_bars < w:
        return out

    from numpy.lib.stride_tricks import sliding_window_view
    weights = np.arange(n_val, dtype=np.float64)
    windows = sliding_window_view(arr, w, axis=0)
    iy_sum = np.sum(windows * weights[np.newaxis, np.newaxis, :], axis=-1)
    y_sum = np.sum(windows, axis=-1)
    slopes = (n_val * iy_sum - sum_i * y_sum) / denom
    out[w-1:] = slopes
    return out


def _xs_rank_2d(arr):
    """Cross-sectional percentile rank at each row. Returns [0, 1]."""
    out = np.full_like(arr, np.nan)
    for t in range(arr.shape[0]):
        row = arr[t]
        valid = ~np.isnan(row)
        if valid.sum() < 2:
            continue
        ranks = np.argsort(np.argsort(row[valid]))
        out[t, valid] = ranks / (valid.sum() - 1)
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


def _store(features, name, arr):
    """Store feature, converting to float32 and clamping infinities."""
    a = np.array(arr, dtype=np.float32, copy=True)
    a[~np.isfinite(a)] = np.nan
    features[name] = a


def engineer_features(close, open_, high, low, volume, ts_grid, btc_idx):
    """Engineer the ~10 features needed: 4 regime + 6 signal.

    Returns: features dict, regime_features dict (4 market-level arrays)
    """
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    t0 = time.time()
    n_bars, n_symbols = close.shape
    features = {}

    # ── Precompute intermediates ──
    bar_range = high - low
    safe_range = np.where(bar_range > 0, bar_range, 1e-10)
    safe_close = np.where(close > 0, close, 1e-10)

    ret_1 = np.full((n_bars, n_symbols), np.nan)
    valid_prev = (close[:-1] != 0) & ~np.isnan(close[:-1])
    ret_1[1:] = np.where(valid_prev,
                          (close[1:] - close[:-1]) / np.where(valid_prev, close[:-1], 1.0),
                          np.nan)
    abs_ret_1 = np.abs(ret_1)

    # ── REGIME FEATURES (4, market-level) ──
    logger.info("  Computing 4 regime features...")

    # 1. market_breadth: fraction of symbols with positive 1-bar returns
    market_breadth_raw = np.nanmean((ret_1 > 0).astype(np.float64), axis=1)  # (n_bars,)

    # 2. market_ret_std: cross-sectional std of 1-bar returns, z-scored over 21-bar lookback
    market_ret_std_raw = np.nanstd(ret_1, axis=1)  # (n_bars,)
    # Cross-sectional z-score over time (21-bar lookback)
    mrs_1d = market_ret_std_raw.copy()
    mrs_mean = np.full(n_bars, np.nan)
    mrs_std = np.full(n_bars, np.nan)
    for t in range(20, n_bars):
        window = mrs_1d[t-20:t+1]
        valid_w = ~np.isnan(window)
        if valid_w.sum() >= 5:
            mrs_mean[t] = np.mean(window[valid_w])
            mrs_std[t] = np.std(window[valid_w])
    safe_mrs_std = np.where(mrs_std > 0, mrs_std, 1e-10)
    market_ret_std_z = (mrs_1d - mrs_mean) / safe_mrs_std

    # 3. xs_ret_concentration: Herfindahl index of absolute returns (5-bar)
    ret_5 = np.full((n_bars, n_symbols), np.nan)
    if n_bars > 5:
        valid_5 = (close[:-5] != 0) & ~np.isnan(close[:-5]) & ~np.isnan(close[5:])
        ret_5[5:] = np.where(valid_5,
                              (close[5:] - close[:-5]) / np.where(valid_5, close[:-5], 1.0),
                              np.nan)
    abs_ret_5 = np.abs(ret_5)
    abs_sum = np.nansum(abs_ret_5, axis=1, keepdims=True)
    safe_abs_sum = np.where(abs_sum > 0, abs_sum, 1e-10)
    shares = abs_ret_5 / safe_abs_sum
    xs_ret_concentration_raw = np.nansum(shares**2, axis=1)  # (n_bars,)

    # 4. market_vol_trend: 8-bar slope of market_ret_std_raw
    # Use a 1D rolling slope
    mrs_2d = market_ret_std_raw.reshape(-1, 1)
    market_vol_trend_raw = _rolling_slope_2d(mrs_2d, 8).flatten()

    # Store regime arrays as (n_bars,) 1D
    regime_features = {
        "market_breadth": market_breadth_raw,
        "market_ret_std_z": market_ret_std_z,
        "xs_ret_concentration": xs_ret_concentration_raw,
        "market_vol_trend": market_vol_trend_raw,
    }

    # ── SIGNAL FEATURES (6) ──
    logger.info("  Computing 6 signal features...")

    # Precompute what we need
    ret_cache = {}
    for L in [5, 8, 21]:
        ret_L = np.full((n_bars, n_symbols), np.nan)
        valid_L = (close[:-L] != 0) & ~np.isnan(close[:-L]) & ~np.isnan(close[L:])
        ret_L[L:] = np.where(valid_L,
                              (close[L:] - close[:-L]) / np.where(valid_L, close[:-L], 1.0),
                              np.nan)
        ret_cache[L] = ret_L

    vol_5 = _rolling_std_2d(ret_1, 5)
    vol_21 = _rolling_std_2d(ret_1, 21)

    # 1. relative_strength_21: symbol return minus universe mean (21-bar)
    univ_mean_ret_21 = np.nanmean(ret_cache[21], axis=1, keepdims=True)
    _store(features, "relative_strength_21", ret_cache[21] - univ_mean_ret_21)

    # 2. ret_norm_5: vol-normalized 5-bar momentum
    safe_vol_5 = np.where(vol_5 > 0, vol_5, 1e-10)
    _store(features, "ret_norm_5", ret_cache[5] / safe_vol_5)

    # 3. xs_ret_rank_5: cross-sectional rank of 5-bar return
    _store(features, "xs_ret_rank_5", _xs_rank_2d(ret_cache[5]))

    # 4. pos_in_range_21: where close sits in 21-bar high-low range
    rmax_21 = _rolling_max_2d(close, 21)
    rmin_21 = _rolling_min_2d(close, 21)
    rng_21 = rmax_21 - rmin_21
    safe_rng_21 = np.where(rng_21 > 0, rng_21, 1e-10)
    _store(features, "pos_in_range_21", (close - rmin_21) / safe_rng_21)

    # 5. efficiency_ratio_8: Kaufman efficiency ratio
    abs_ret_sum_8 = _rolling_sum_2d(abs_ret_1, 8)
    safe_ars_8 = np.where(abs_ret_sum_8 > 0, abs_ret_sum_8, 1e-10)
    _store(features, "efficiency_ratio_8", np.abs(ret_cache[8]) / safe_ars_8)

    # 6. momentum_quality_divergence: ret_norm_5 - ret_norm_21
    safe_vol_21 = np.where(vol_21 > 0, vol_21, 1e-10)
    ret_norm_5 = ret_cache[5] / safe_vol_5
    ret_norm_21 = ret_cache[21] / safe_vol_21
    _store(features, "momentum_quality_divergence", ret_norm_5 - ret_norm_21)

    elapsed = time.time() - t0
    logger.info("  Feature engineering complete: %d signal + %d regime features in %.1fs",
                len(features), len(regime_features), elapsed)

    return features, regime_features


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: REGIME GATE LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def compute_regime_score(regime_features, bar_mask=None):
    """Compute regime_score from 4 market-level features.

    Each feature is z-scored within the bar_mask period, then combined:
        regime_score = w1*breadth_z + w2*dispersion_z - w3*concentration_z + w4*vol_trend_z

    Higher score = more favorable regime (high dispersion, expanding vol,
    non-concentrated returns, bullish breadth).

    Returns: (n_bars,) regime_score array
    """
    mb = regime_features["market_breadth"]
    mrs = regime_features["market_ret_std_z"]
    xrc = regime_features["xs_ret_concentration"]
    mvt = regime_features["market_vol_trend"]

    n_bars = len(mb)

    # Z-score each within the provided mask (or full range)
    def _zscore_1d(arr, mask=None):
        if mask is not None:
            vals = arr[mask]
        else:
            vals = arr
        valid = ~np.isnan(vals)
        if valid.sum() < 5:
            return np.full(n_bars, 0.0)
        m = np.nanmean(vals[valid])
        s = np.nanstd(vals[valid])
        if s < 1e-10:
            return np.full(n_bars, 0.0)
        return (arr - m) / s

    mask = bar_mask
    mb_z = _zscore_1d(mb, mask)
    mrs_z = _zscore_1d(mrs, mask)
    xrc_z = _zscore_1d(xrc, mask)
    mvt_z = _zscore_1d(mvt, mask)

    # Weights: dispersion is the strongest signal for cross-sectional strategies
    # High breadth → directional clarity, high dispersion → opportunity,
    # low concentration → distributed opportunity, positive vol trend → expanding window
    regime_score = (0.20 * mb_z
                    + 0.35 * mrs_z
                    - 0.25 * xrc_z
                    + 0.20 * mvt_z)

    return regime_score


def optimize_regime_threshold(regime_score, composite, fwd_ret,
                               bar_mask, symbols, ohlcv_data,
                               thresholds=REGIME_THRESHOLDS):
    """Optimize regime threshold on a 70/30 mini-split within training data.

    Returns: best_threshold (percentile), and the threshold value
    """
    bar_indices = np.where(bar_mask)[0]
    n_bars_train = len(bar_indices)
    split_idx = int(n_bars_train * 0.7)

    mini_val_bars = bar_indices[split_idx:]
    val_mask = np.zeros(composite.shape[0], dtype=bool)
    val_mask[mini_val_bars] = True

    # Compute score distribution on training portion (first 70%)
    train_bars = bar_indices[:split_idx]
    train_scores = regime_score[train_bars]
    train_valid = ~np.isnan(train_scores)
    if train_valid.sum() < 10:
        return 50, 0.0

    cfg = HORIZON_CONFIG
    exit_cfg = _make_exit_config(cfg)

    best_pctl = 50
    best_pf = 0.0
    best_thresh_val = 0.0

    for pctl in thresholds:
        thresh_val = np.nanpercentile(train_scores[train_valid], pctl)

        # Generate signals on val bars where regime gate is open
        gated_mask = val_mask & (regime_score >= thresh_val)
        if gated_mask.sum() < 10:
            continue

        signals = generate_signals(composite, gated_mask, K_PCT, symbols)
        if len(signals) < 3:
            continue

        trades = simulate_trades(signals, ohlcv_data, cfg["stop_atr_mult"],
                                 cfg["time_stop_bars"], BASE_SLIPPAGE_1H, exit_cfg)
        if len(trades) < 3:
            continue

        avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
        if avg_pf > best_pf:
            best_pf = avg_pf
            best_pctl = pctl
            best_thresh_val = thresh_val

    logger.info("      Regime threshold: best=%dth pctl (val=%.4f), val PF=%.3f",
                best_pctl, best_thresh_val, best_pf)
    return best_pctl, best_thresh_val


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: FAMA-MACBETH IC & COMPOSITE MODEL
# ═══════════════════════════════════════════════════════════════════════════════

def batch_fama_macbeth_ic(features_dict, fwd_ret, bar_mask):
    """Compute Fama-MacBeth IC for ALL features at once. Vectorized.

    Returns: dict[name] -> (mean_ic, t_stat, n_cross_sections)
    """
    names = sorted(features_dict.keys())
    n_features = len(names)
    bar_indices = np.where(bar_mask)[0]

    all_ics = []

    for t in bar_indices:
        target = fwd_ret[t]
        t_valid = ~np.isnan(target)
        n_valid = t_valid.sum()
        if n_valid < 20:
            continue

        t_sub = target[t_valid]
        t_ranks = np.argsort(np.argsort(t_sub)).astype(np.float64)
        t_mean = t_ranks.mean()
        t_std = t_ranks.std()
        if t_std == 0:
            continue

        f_matrix = np.empty((n_features, n_valid), dtype=np.float64)
        for fi, name in enumerate(names):
            f_matrix[fi] = features_dict[name][t, t_valid]

        has_nan = np.isnan(f_matrix).any(axis=1)
        ics_at_t = np.full(n_features, np.nan)

        # Batch clean features
        clean = ~has_nan
        if clean.sum() > 0:
            f_clean = f_matrix[clean]
            f_ranks = np.argsort(np.argsort(f_clean, axis=1), axis=1).astype(np.float64)
            f_mean = f_ranks.mean(axis=1, keepdims=True)
            f_std = f_ranks.std(axis=1)
            cov = ((f_ranks - f_mean) * (t_ranks[np.newaxis, :] - t_mean)).mean(axis=1)
            valid_std = f_std > 0
            ics_clean = np.zeros(clean.sum())
            ics_clean[valid_std] = cov[valid_std] / (f_std[valid_std] * t_std)
            ics_at_t[clean] = ics_clean

        # Handle NaN features individually
        nan_idxs = np.where(has_nan)[0]
        for fi in nan_idxs:
            f_row = f_matrix[fi]
            ok = ~np.isnan(f_row)
            if ok.sum() < 20:
                continue
            fs = f_row[ok]
            ts = t_sub[ok]
            fr = np.argsort(np.argsort(fs)).astype(np.float64)
            tr = np.argsort(np.argsort(ts)).astype(np.float64)
            fm, tm = fr.mean(), tr.mean()
            fsd, tsd = fr.std(), tr.std()
            if fsd > 0 and tsd > 0:
                ics_at_t[fi] = np.mean((fr - fm) * (tr - tm)) / (fsd * tsd)

        all_ics.append(ics_at_t)

    if not all_ics:
        return {name: (0.0, 0.0, 0) for name in names}

    ics_matrix = np.array(all_ics)

    results = {}
    for fi, name in enumerate(names):
        col = ics_matrix[:, fi]
        valid = ~np.isnan(col)
        n_cs = int(valid.sum())
        if n_cs < 5:
            results[name] = (0.0, 0.0, n_cs)
            continue
        ic_vals = col[valid]
        mean_ic = float(np.mean(ic_vals))
        std_ic = float(np.std(ic_vals, ddof=1))
        t_stat = mean_ic / (std_ic / np.sqrt(n_cs)) if std_ic > 0 else 0.0
        results[name] = (mean_ic, t_stat, n_cs)

    return results


def build_composite_model(features, ic_weights, bar_mask):
    """Build IC-weighted composite z-score model.

    ic_weights: dict[name] -> ic_value (sign encodes direction)
    Returns: (n_bars, n_symbols) composite scores
    """
    n_bars, n_symbols = next(iter(features.values())).shape
    composite = np.zeros((n_bars, n_symbols), dtype=np.float64)
    total_weight = 0.0

    for name, ic in ic_weights.items():
        if name not in features:
            continue
        f = features[name].astype(np.float64)
        mean_xs = np.nanmean(f, axis=1, keepdims=True)
        std_xs = np.nanstd(f, axis=1, keepdims=True)
        safe_std = np.where(std_xs > 0, std_xs, 1e-10)
        z = (f - mean_xs) / safe_std
        composite += ic * z
        total_weight += abs(ic)

    if total_weight > 0:
        composite /= total_weight

    return composite


def generate_signals(composite, bar_mask, k_pct, symbols):
    """Generate LONG/SHORT signals from composite scores.

    At each bar, top k_pct% -> LONG, bottom k_pct% -> SHORT.
    Returns list of Signal objects.
    """
    n_bars, n_symbols = composite.shape
    signals = []

    bar_indices = np.where(bar_mask)[0]
    for t in bar_indices:
        row = composite[t]
        valid = ~np.isnan(row)
        n_valid = valid.sum()
        if n_valid < 20:
            continue

        vals = row.copy()
        vals[~valid] = np.nan
        ranks = np.full(n_symbols, np.nan)
        order = np.argsort(vals[valid])
        ranks_valid = np.empty(valid.sum())
        ranks_valid[order] = np.arange(valid.sum()) / (valid.sum() - 1)

        idx_valid = np.where(valid)[0]
        for i, idx in enumerate(idx_valid):
            ranks[idx] = ranks_valid[i]

        threshold_high = 1.0 - k_pct / 100.0
        threshold_low = k_pct / 100.0

        for s_idx in range(n_symbols):
            if np.isnan(ranks[s_idx]):
                continue
            if ranks[s_idx] >= threshold_high:
                score = (ranks[s_idx] - threshold_high) / (1.0 - threshold_high + 1e-10)
                signals.append(Signal(
                    symbol=symbols[s_idx],
                    direction="long",
                    ts_idx=t,
                    score=score,
                ))
            elif ranks[s_idx] <= threshold_low:
                score = (threshold_low - ranks[s_idx]) / (threshold_low + 1e-10)
                signals.append(Signal(
                    symbol=symbols[s_idx],
                    direction="short",
                    ts_idx=t,
                    score=score,
                ))

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATION
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

        max_idx = min(i + 2 + time_stop_bars + 20, n)
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

        # FIX: Apply slippage to ALL exits including STOP_LOSS
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
    """Compute average daily profit factor."""
    if not trades:
        return 0.0

    ts_grid = ohlcv_data[(symbols[0], '1h')]["ts"]

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
            pfs.append(10.0)

    if not pfs:
        return 0.0

    return float(np.mean(pfs))


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_folds(ts_grid, n_folds=N_FOLDS, holdout_days=HOLDOUT_DAYS, embargo_days=EMBARGO_DAYS):
    """Build expanding window folds from timestamp grid.

    Returns: list of (train_mask, test_mask), holdout_mask
    """
    day_start = ts_grid[0] // 86400
    day_end = ts_grid[-1] // 86400
    total_days = int(day_end - day_start) + 1

    logger.info("  Total span: %d days (%s to %s)",
                total_days,
                datetime.fromtimestamp(ts_grid[0], tz=timezone.utc).strftime("%Y-%m-%d"),
                datetime.fromtimestamp(ts_grid[-1], tz=timezone.utc).strftime("%Y-%m-%d"))

    min_required = holdout_days + 2 * 5 + embargo_days
    if total_days < min_required:
        logger.error("  Insufficient data: %d days, need >= %d. Increase --days.",
                      total_days, min_required)
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


def process_fold(fold_idx, train_mask, test_mask, features, fwd_ret,
                 regime_features, regime_score, symbols, ohlcv_data):
    """Process a single fold: train IC weights + regime threshold, test on OOS.

    Returns: FoldResult
    """
    t0 = time.time()
    cfg = HORIZON_CONFIG
    exit_cfg = _make_exit_config(cfg)

    logger.info("  Fold %d: computing ICs on training data...", fold_idx)

    # (a) Compute feature ICs on training data
    ic_results = batch_fama_macbeth_ic(features, fwd_ret, train_mask)
    ic_weights = {}
    for name, (mean_ic, t_stat, n_cs) in ic_results.items():
        ic_weights[name] = mean_ic  # sign in ic encodes direction
        logger.info("    Feature %s: IC=%.4f, t=%.2f, n_cs=%d", name, mean_ic, t_stat, n_cs)

    # (b) Build composite model
    composite = build_composite_model(features, ic_weights, train_mask)

    # (c) Optimize regime threshold on mini-split within training
    logger.info("  Fold %d: optimizing regime threshold...", fold_idx)
    best_pctl, thresh_val = optimize_regime_threshold(
        regime_score, composite, fwd_ret, train_mask, symbols, ohlcv_data)

    # If threshold_val was from mini-split, recompute on full training for test usage
    train_indices = np.where(train_mask)[0]
    train_scores = regime_score[train_indices]
    train_valid = ~np.isnan(train_scores)
    if train_valid.sum() >= 10:
        thresh_val = np.nanpercentile(train_scores[train_valid], best_pctl)

    # (d) Test on OOS period with fixed regime threshold and model
    # Count signals before gate
    test_signals_ungated = generate_signals(composite, test_mask, K_PCT, symbols)
    n_signals_before_gate = len(test_signals_ungated)

    # Apply regime gate to test mask
    gated_test_mask = test_mask & (regime_score >= thresh_val)
    regime_open_bars = gated_test_mask.sum()
    total_test_bars = test_mask.sum()
    regime_open_fraction = regime_open_bars / total_test_bars if total_test_bars > 0 else 0.0

    logger.info("  Fold %d: regime gate open %.1f%% of test bars (%d/%d), threshold=%dth pctl (%.4f)",
                fold_idx, regime_open_fraction * 100, regime_open_bars, total_test_bars,
                best_pctl, thresh_val)

    # Generate gated signals
    test_signals = generate_signals(composite, gated_test_mask, K_PCT, symbols)
    n_signals_after_gate = len(test_signals)
    logger.info("  Fold %d: %d signals (ungated: %d, gated: %d)",
                fold_idx, n_signals_after_gate, n_signals_before_gate, n_signals_after_gate)

    # Simulate trades
    trades = simulate_trades(test_signals, ohlcv_data, cfg["stop_atr_mult"],
                             cfg["time_stop_bars"], BASE_SLIPPAGE_1H, exit_cfg)

    # Compute metrics
    if trades:
        avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
        total_pnl = sum(t.pnl for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        win_rate = wins / len(trades)
        gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
        gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
        agg_pf = gross_wins / gross_losses if gross_losses > 0 else 10.0
    else:
        avg_pf = 0.0
        total_pnl = 0.0
        win_rate = 0.0
        agg_pf = 0.0

    # Regime score stats for test period
    test_scores = regime_score[np.where(test_mask)[0]]
    test_valid_scores = test_scores[~np.isnan(test_scores)]
    rs_mean = float(np.mean(test_valid_scores)) if len(test_valid_scores) > 0 else 0.0
    rs_std = float(np.std(test_valid_scores)) if len(test_valid_scores) > 0 else 0.0

    elapsed = time.time() - t0
    logger.info("  Fold %d: %d trades, avg daily PF=%.3f, agg PF=%.3f, WR=%.1f%%, "
                "PnL=$%.2f, regime_thresh=%dpctl (%.1fs)",
                fold_idx, len(trades), avg_pf, agg_pf, win_rate * 100,
                total_pnl, best_pctl, elapsed)

    fold_result = FoldResult(
        fold_idx=fold_idx,
        n_features=len(features),
        feature_names=list(features.keys()),
        n_trades=len(trades),
        avg_daily_pf=avg_pf,
        aggregate_pf=agg_pf,
        win_rate=win_rate,
        total_pnl=total_pnl,
        regime_threshold=best_pctl,
        regime_open_fraction=regime_open_fraction,
        regime_score_mean=rs_mean,
        regime_score_std=rs_std,
        n_signals_before_gate=n_signals_before_gate,
        n_signals_after_gate=n_signals_after_gate,
    )

    return fold_result, ic_weights, thresh_val


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: HOLDOUT & DSR
# ═══════════════════════════════════════════════════════════════════════════════

def deflated_sharpe_ratio(observed_sr, n_trials, n_observations, skew=0, kurtosis=3):
    """Harvey, Liu, Zhu (2016) -- adjusts for multiple testing."""
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


def run_holdout(features, fwd_ret, regime_features, regime_score,
                folds, holdout_mask, symbols, ohlcv_data, n_trials):
    """Run holdout evaluation.

    Uses all pre-holdout data for IC weights + regime threshold, tests on holdout.
    """
    cfg = HORIZON_CONFIG
    exit_cfg = _make_exit_config(cfg)

    # Full training mask: everything before holdout
    full_train_mask = np.zeros(len(holdout_mask), dtype=bool)
    holdout_start = np.where(holdout_mask)[0]
    if len(holdout_start) > 0:
        full_train_mask[:holdout_start[0]] = True
    else:
        return None

    logger.info("  Holdout: computing ICs on %d training bars...", full_train_mask.sum())

    # IC weights on full training
    ic_results = batch_fama_macbeth_ic(features, fwd_ret, full_train_mask)
    ic_weights = {}
    for name, (mean_ic, t_stat, n_cs) in ic_results.items():
        ic_weights[name] = mean_ic
        logger.info("    Feature %s: IC=%.4f, t=%.2f", name, mean_ic, t_stat)

    # Build composite
    composite = build_composite_model(features, ic_weights, full_train_mask)

    # Optimize regime threshold on training
    best_pctl, thresh_val = optimize_regime_threshold(
        regime_score, composite, fwd_ret, full_train_mask, symbols, ohlcv_data)

    # Recompute threshold from full training
    train_indices = np.where(full_train_mask)[0]
    train_scores = regime_score[train_indices]
    train_valid = ~np.isnan(train_scores)
    if train_valid.sum() >= 10:
        thresh_val = np.nanpercentile(train_scores[train_valid], best_pctl)

    # Apply regime gate to holdout
    gated_holdout_mask = holdout_mask & (regime_score >= thresh_val)
    regime_open_bars = gated_holdout_mask.sum()
    total_holdout_bars = holdout_mask.sum()
    regime_open_fraction = regime_open_bars / total_holdout_bars if total_holdout_bars > 0 else 0.0

    logger.info("  Holdout: regime gate open %.1f%% (%d/%d bars), threshold=%dpctl",
                regime_open_fraction * 100, regime_open_bars, total_holdout_bars, best_pctl)

    # Ungated signals for comparison
    ungated_signals = generate_signals(composite, holdout_mask, K_PCT, symbols)
    logger.info("  Holdout: %d ungated signals", len(ungated_signals))

    # Gated signals
    holdout_signals = generate_signals(composite, gated_holdout_mask, K_PCT, symbols)
    logger.info("  Holdout: %d gated signals", len(holdout_signals))

    # Test at 1.5x slippage
    slippage_15x = BASE_SLIPPAGE_1H * 1.5
    trades = simulate_trades(holdout_signals, ohlcv_data, cfg["stop_atr_mult"],
                             cfg["time_stop_bars"], slippage_15x, exit_cfg)

    if not trades:
        logger.info("  Holdout: 0 trades")
        return None

    avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades)
    gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
    agg_pf = gross_wins / gross_losses if gross_losses > 0 else 10.0

    # Sharpe for DSR
    pnl_arr = np.array([t.pnl for t in trades])
    n_obs = len(pnl_arr)
    mean_pnl = np.mean(pnl_arr)
    std_pnl = np.std(pnl_arr, ddof=1)
    sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

    if n_obs > 2 and std_pnl > 0:
        z_pnl = (pnl_arr - mean_pnl) / std_pnl
        m3 = np.mean(z_pnl**3)
        m4 = np.mean(z_pnl**4)
        skew = float(m3 * (n_obs * (n_obs - 1))**0.5 / (n_obs - 2)) if n_obs > 2 else 0
        kurt = float(m4)
    else:
        skew, kurt = 0.0, 3.0

    dsr = deflated_sharpe_ratio(sharpe, n_trials, n_obs, skew, kurt)

    logger.info("  Holdout (1.5x slip): %d trades, avg daily PF=%.3f, agg PF=%.3f, "
                "WR=%.1f%%, Sharpe=%.3f, DSR=%.3f, PnL=$%.2f",
                len(trades), avg_pf, agg_pf, win_rate * 100, sharpe, dsr, total_pnl)

    # Also test ungated for comparison
    ungated_trades = simulate_trades(ungated_signals, ohlcv_data, cfg["stop_atr_mult"],
                                     cfg["time_stop_bars"], slippage_15x, exit_cfg)
    if ungated_trades:
        ungated_pf = compute_avg_daily_pf(ungated_trades, ohlcv_data, symbols)
        ungated_pnl = sum(t.pnl for t in ungated_trades)
        logger.info("  Holdout UNGATED (1.5x slip): %d trades, avg daily PF=%.3f, PnL=$%.2f",
                    len(ungated_trades), ungated_pf, ungated_pnl)
    else:
        ungated_pf = 0.0
        ungated_pnl = 0.0

    # 2x slippage stress test
    slippage_2x = BASE_SLIPPAGE_1H * 2.0
    trades_2x = simulate_trades(holdout_signals, ohlcv_data, cfg["stop_atr_mult"],
                                cfg["time_stop_bars"], slippage_2x, exit_cfg)
    if trades_2x:
        pf_2x = compute_avg_daily_pf(trades_2x, ohlcv_data, symbols)
        pnl_2x = sum(t.pnl for t in trades_2x)
        logger.info("  Holdout (2.0x slip): %d trades, avg daily PF=%.3f, PnL=$%.2f",
                    len(trades_2x), pf_2x, pnl_2x)
    else:
        pf_2x = 0.0
        pnl_2x = 0.0

    return {
        "avg_daily_pf": avg_pf,
        "aggregate_pf": agg_pf,
        "dsr": dsr,
        "sharpe": sharpe,
        "n_trades": len(trades),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "regime_threshold_pctl": best_pctl,
        "regime_threshold_value": float(thresh_val),
        "regime_open_fraction": regime_open_fraction,
        "ungated_n_trades": len(ungated_trades) if ungated_trades else 0,
        "ungated_avg_daily_pf": ungated_pf,
        "ungated_total_pnl": ungated_pnl,
        "slippage_2x_avg_daily_pf": pf_2x,
        "slippage_2x_total_pnl": pnl_2x,
        "ic_weights": {k: float(v) for k, v in ic_weights.items()},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Signal Genesis -- Regime-Conditional Sparse Model")
    parser.add_argument("--days", type=int, default=110,
                        help="Days of OHLCV to load")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS,
                        help="Days reserved for holdout")
    args = parser.parse_args()

    t_start = time.time()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    jsonl_path = os.path.join(script_dir, "signal_genesis_regime_incremental.jsonl")
    results_path = os.path.join(script_dir, "signal_genesis_regime_results.json")

    logger.info("=" * 70)
    logger.info("SIGNAL GENESIS -- Regime-Conditional Sparse Model")
    logger.info("=" * 70)
    logger.info("Config: days=%d, horizon=h%d, folds=%d, holdout=%d days, K=%d%%",
                args.days, HORIZON, N_FOLDS, args.holdout_days, K_PCT)
    logger.info("Signal features: %s", SIGNAL_FEATURES)
    logger.info("Regime thresholds to sweep: %s", REGIME_THRESHOLDS)

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
    logger.info("PHASE 2: Feature Engineering (sparse: 6 signal + 4 regime)")
    logger.info("-" * 50)

    features, regime_features = engineer_features(
        close, open_, high, low, volume, ts_grid, btc_idx)

    # Verify all signal features were computed
    missing = [f for f in SIGNAL_FEATURES if f not in features]
    if missing:
        logger.error("Missing signal features: %s", missing)
        sys.exit(1)

    # ── Phase 3: Build Folds ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 3: Walk-Forward Fold Construction")
    logger.info("-" * 50)

    folds, holdout_mask = build_folds(ts_grid, n_folds=N_FOLDS,
                                       holdout_days=args.holdout_days)
    if not folds:
        logger.error("No folds could be constructed. Need more data (try --days 110).")
        sys.exit(1)

    # ── Phase 4: Compute regime score ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 4: Regime Score Computation")
    logger.info("-" * 50)

    # Compute regime score using full data (z-scoring is done per fold's training later)
    regime_score = compute_regime_score(regime_features)
    valid_rs = regime_score[~np.isnan(regime_score)]
    logger.info("  Regime score: mean=%.4f, std=%.4f, min=%.4f, max=%.4f",
                np.mean(valid_rs), np.std(valid_rs), np.min(valid_rs), np.max(valid_rs))

    # Log regime feature distributions
    for name, arr in regime_features.items():
        valid = arr[~np.isnan(arr)]
        if len(valid) > 0:
            logger.info("  %s: mean=%.4f, std=%.4f", name, np.mean(valid), np.std(valid))

    # ── Phase 5: Walk-Forward Validation ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 5: Walk-Forward Validation (%d folds, h%d)", len(folds), HORIZON)
    logger.info("-" * 50)

    fold_results = []
    fold_ic_weights = []
    fold_thresh_vals = []

    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        fold_t0 = time.time()
        logger.info("\n  --- Fold %d/%d ---", fold_idx + 1, len(folds))

        # Recompute regime score z-scored to training period
        fold_regime_score = compute_regime_score(regime_features, bar_mask=train_mask)

        fr, ic_w, thresh_v = process_fold(
            fold_idx, train_mask, test_mask, features, fwd_ret,
            regime_features, fold_regime_score, symbols, ohlcv_data)

        fold_results.append(fr)
        fold_ic_weights.append(ic_w)
        fold_thresh_vals.append(thresh_v)

        # Incremental write
        fold_data = {
            "type": "fold",
            "fold_idx": fold_idx,
            **asdict(fr),
            "ic_weights": {k: float(v) for k, v in ic_w.items()},
            "regime_threshold_value": float(thresh_v),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(fold_data, default=str) + "\n")

        elapsed_fold = time.time() - fold_t0
        elapsed_total = time.time() - t_start
        remaining_folds = len(folds) - (fold_idx + 1)
        eta = elapsed_fold * remaining_folds
        logger.info("  Fold %d complete in %.1fs. ETA for remaining %d folds: %.0fs",
                    fold_idx, elapsed_fold, remaining_folds, eta)

    # Walk-forward summary
    valid_folds = [fr for fr in fold_results if fr.n_trades > 0]
    if valid_folds:
        avg_pf = float(np.mean([fr.avg_daily_pf for fr in valid_folds]))
        consistency = sum(1 for fr in valid_folds if fr.avg_daily_pf > 1.0) / len(valid_folds)
        total_trades = sum(fr.n_trades for fr in valid_folds)
        avg_regime_open = float(np.mean([fr.regime_open_fraction for fr in valid_folds]))
    else:
        avg_pf = 0.0
        consistency = 0.0
        total_trades = 0
        avg_regime_open = 0.0

    logger.info("\n  Walk-forward summary:")
    logger.info("    Avg daily PF: %.3f", avg_pf)
    logger.info("    Consistency: %.1f%% (%d/%d folds profitable)",
                consistency * 100,
                sum(1 for fr in valid_folds if fr.avg_daily_pf > 1.0),
                len(valid_folds))
    logger.info("    Total trades: %d", total_trades)
    logger.info("    Avg regime gate open: %.1f%%", avg_regime_open * 100)

    for fr in fold_results:
        logger.info("    Fold %d: %d trades, avg PF=%.3f, agg PF=%.3f, regime=%dpctl (%.1f%% open), PnL=$%.2f",
                    fr.fold_idx, fr.n_trades, fr.avg_daily_pf, fr.aggregate_pf,
                    fr.regime_threshold, fr.regime_open_fraction * 100, fr.total_pnl)

    # Write walk-forward summary
    wf_summary = {
        "type": "walk_forward_summary",
        "avg_daily_pf": avg_pf,
        "consistency": consistency,
        "total_trades": total_trades,
        "avg_regime_open_fraction": avg_regime_open,
        "folds": [asdict(fr) for fr in fold_results],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(wf_summary, default=str) + "\n")

    # ── Phase 6: Holdout ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 6: Holdout Evaluation")
    logger.info("-" * 50)

    # Determine if walk-forward passed
    wf_pass = (avg_pf > 1.0
               and consistency >= 0.67  # 2/3 folds
               and total_trades >= MIN_TRADES_TOTAL)

    if not wf_pass:
        logger.info("  Walk-forward FAILED (PF=%.3f, cons=%.1f%%, trades=%d). "
                     "Running holdout anyway for diagnostic purposes.",
                     avg_pf, consistency * 100, total_trades)

    # Total trials for DSR: 6 features x 5 regime thresholds = 30
    n_trials = len(SIGNAL_FEATURES) * len(REGIME_THRESHOLDS)

    # Re-compute regime score z-scored to all pre-holdout data
    pre_holdout_mask = np.zeros(len(holdout_mask), dtype=bool)
    holdout_start_idx = np.where(holdout_mask)[0]
    if len(holdout_start_idx) > 0:
        pre_holdout_mask[:holdout_start_idx[0]] = True

    holdout_regime_score = compute_regime_score(regime_features, bar_mask=pre_holdout_mask)

    holdout_result = run_holdout(
        features, fwd_ret, regime_features, holdout_regime_score,
        folds, holdout_mask, symbols, ohlcv_data, n_trials)

    if holdout_result:
        with open(jsonl_path, "a") as f:
            ho_data = {
                "type": "holdout",
                **{k: v for k, v in holdout_result.items()},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            f.write(json.dumps(ho_data, default=str) + "\n")

    # ── Phase 7: Also run an ungated baseline for comparison ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 7: Ungated Baseline Comparison (no regime gate)")
    logger.info("-" * 50)

    # Run each fold without the regime gate
    ungated_fold_results = []
    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        logger.info("  Ungated fold %d...", fold_idx)
        ic_results = batch_fama_macbeth_ic(features, fwd_ret, train_mask)
        ic_weights_ug = {name: mean_ic for name, (mean_ic, _, _) in ic_results.items()}
        composite_ug = build_composite_model(features, ic_weights_ug, train_mask)
        test_signals_ug = generate_signals(composite_ug, test_mask, K_PCT, symbols)
        trades_ug = simulate_trades(test_signals_ug, ohlcv_data,
                                     HORIZON_CONFIG["stop_atr_mult"],
                                     HORIZON_CONFIG["time_stop_bars"],
                                     BASE_SLIPPAGE_1H,
                                     _make_exit_config(HORIZON_CONFIG))
        if trades_ug:
            ug_pf = compute_avg_daily_pf(trades_ug, ohlcv_data, symbols)
            ug_pnl = sum(t.pnl for t in trades_ug)
            logger.info("    Ungated fold %d: %d trades, avg PF=%.3f, PnL=$%.2f",
                        fold_idx, len(trades_ug), ug_pf, ug_pnl)
            ungated_fold_results.append({
                "fold_idx": fold_idx,
                "n_trades": len(trades_ug),
                "avg_daily_pf": ug_pf,
                "total_pnl": ug_pnl,
            })
        else:
            logger.info("    Ungated fold %d: 0 trades", fold_idx)
            ungated_fold_results.append({
                "fold_idx": fold_idx,
                "n_trades": 0,
                "avg_daily_pf": 0.0,
                "total_pnl": 0.0,
            })

    # ── Phase 8: Final Report ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 8: Final Report")
    logger.info("-" * 50)

    elapsed_total = time.time() - t_start

    # Verdict
    if wf_pass and holdout_result and holdout_result["avg_daily_pf"] > 1.0:
        if holdout_result["dsr"] > 0:
            verdict = "CONFIRMED"
        else:
            verdict = "FAIL_DSR"
    elif wf_pass:
        if holdout_result:
            verdict = "FAIL_HOLDOUT"
        else:
            verdict = "FAIL_HOLDOUT_NO_TRADES"
    else:
        verdict = "FAIL_WALK_FORWARD"

    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed_total,
        "hypothesis": "Regime-conditional sparse model: gate trading on market dispersion/volatility regime, use 6 stable cross-sectional features for direction.",
        "config": {
            "horizon": HORIZON,
            "n_folds": N_FOLDS,
            "holdout_days": args.holdout_days,
            "k_pct": K_PCT,
            "signal_features": SIGNAL_FEATURES,
            "regime_thresholds_swept": REGIME_THRESHOLDS,
            "exit_config": HORIZON_CONFIG,
            "slippage_bps": BASE_SLIPPAGE_1H,
            "fee_bps": FEE_BPS,
            "slippage_on_all_exits": True,
        },
        "walk_forward": {
            "avg_daily_pf": avg_pf,
            "consistency": consistency,
            "total_trades": total_trades,
            "avg_regime_open_fraction": avg_regime_open,
            "folds": [asdict(fr) for fr in fold_results],
        },
        "ungated_baseline": {
            "folds": ungated_fold_results,
            "avg_daily_pf": float(np.mean([f["avg_daily_pf"] for f in ungated_fold_results
                                            if f["n_trades"] > 0])) if any(f["n_trades"] > 0 for f in ungated_fold_results) else 0.0,
        },
        "holdout": holdout_result if holdout_result else {"status": "no_trades_or_skipped"},
        "verdict": verdict,
        "n_trials_for_dsr": n_trials,
    }

    # Fail reasons
    if verdict.startswith("FAIL"):
        reasons = []
        if avg_pf <= 1.0:
            reasons.append(f"walk_forward_avg_daily_pf={avg_pf:.3f} <= 1.0")
        if consistency < 0.67:
            reasons.append(f"consistency={consistency:.2f} < 0.67")
        if total_trades < MIN_TRADES_TOTAL:
            reasons.append(f"trades={total_trades} < {MIN_TRADES_TOTAL}")
        if holdout_result:
            if holdout_result["avg_daily_pf"] <= 1.0:
                reasons.append(f"holdout_avg_daily_pf={holdout_result['avg_daily_pf']:.3f} <= 1.0")
            if holdout_result["dsr"] <= 0:
                reasons.append(f"holdout_dsr={holdout_result['dsr']:.3f} <= 0")
        report["fail_reasons"] = reasons

    # Write results
    with open(results_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Results written to %s", results_path)

    # Print summary
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info("Total elapsed: %.1f min", elapsed_total / 60)
    logger.info("Verdict: %s", verdict)
    logger.info("")
    logger.info("Walk-forward (regime-gated):")
    logger.info("  Avg daily PF: %.3f", avg_pf)
    logger.info("  Consistency: %.1f%%", consistency * 100)
    logger.info("  Trades: %d", total_trades)
    logger.info("  Avg regime open: %.1f%%", avg_regime_open * 100)

    ug_avg_pf = report["ungated_baseline"]["avg_daily_pf"]
    logger.info("")
    logger.info("Ungated baseline:")
    logger.info("  Avg daily PF: %.3f", ug_avg_pf)

    if holdout_result:
        logger.info("")
        logger.info("Holdout (1.5x slippage):")
        logger.info("  Avg daily PF: %.3f (gated) vs %.3f (ungated)",
                    holdout_result["avg_daily_pf"],
                    holdout_result.get("ungated_avg_daily_pf", 0.0))
        logger.info("  DSR: %.3f", holdout_result["dsr"])
        logger.info("  Trades: %d (gated) vs %d (ungated)",
                    holdout_result["n_trades"],
                    holdout_result.get("ungated_n_trades", 0))
        logger.info("  Regime gate: %dpctl (%.1f%% open)",
                    holdout_result["regime_threshold_pctl"],
                    holdout_result["regime_open_fraction"] * 100)
        logger.info("  2x slippage PF: %.3f",
                    holdout_result.get("slippage_2x_avg_daily_pf", 0.0))

    if verdict.startswith("FAIL") and "fail_reasons" in report:
        logger.info("")
        logger.info("Fail reasons:")
        for r in report["fail_reasons"]:
            logger.info("  - %s", r)

    # Regime gate value assessment
    if valid_folds and ungated_fold_results:
        gated_pfs = [fr.avg_daily_pf for fr in valid_folds]
        ungated_pfs = [f["avg_daily_pf"] for f in ungated_fold_results if f["n_trades"] > 0]
        if gated_pfs and ungated_pfs:
            gate_lift = float(np.mean(gated_pfs)) - float(np.mean(ungated_pfs))
            logger.info("")
            logger.info("Regime gate value: %.3f PF lift (gated %.3f - ungated %.3f)",
                        gate_lift, float(np.mean(gated_pfs)), float(np.mean(ungated_pfs)))
            if gate_lift > 0:
                logger.info("  -> Regime gate ADDS value")
            else:
                logger.info("  -> Regime gate does NOT add value")

    logger.info("\n" + "=" * 70)
    logger.info("Done.")


if __name__ == "__main__":
    main()
