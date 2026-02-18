#!/usr/bin/env python3
"""
Signal Genesis Sparse — Top-Feature Minimal Model (h24)
========================================================

Hypothesis: The genesis IC-weighted composite with 100+ features diluted
signal into noise. This script takes only the top 8 features by FM IC
(from the ~20 most robust candidates that appeared in ALL folds at ALL
horizons), builds a minimal composite, and tests h24 only.

Key differences from signal_genesis.py:
- Hard cap at 8 features (no interaction features)
- Simple selection: FM IC -> top 8 by |IC| (no MI gate, no stability check)
- Slippage applied to ALL exits including stop-loss
- h24 only (best edge-to-cost ratio)
- Feature ablation test to identify which features actually contribute

Usage:
    tmux new -s sparse
    docker compose run --rm -T worker python3 /scripts/signal_genesis_sparse.py --days 110
    # Quick smoke test:
    docker compose run --rm -T worker python3 /scripts/signal_genesis_sparse.py --days 30 --quick
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
HORIZON_CONFIG = {
    "stop_atr_mult": 2.0,
    "time_stop_bars": 36,
    "trail_activation_r": 1.5,
    "trail_distance_r": 1.0,
    "trail_breakeven_r": 0.0,
}

# Sparse model config
MAX_FEATURES = 8            # Hard cap
IC_TSTAT_MIN = 1.5          # Minimum IC t-stat to even be considered
TOP_K_PCT = 2               # Fixed K=2% (genesis showed this was best for h24)

# Walk-forward config
N_FOLDS = 4
EMBARGO_DAYS = 1
HOLDOUT_DAYS = 18
MIN_TRADES_TOTAL = 25
MIN_FOLD_CONSISTENCY = 0.75


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

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
    n_features: int
    feature_names: list
    feature_ics: list
    n_trades: int
    avg_daily_pf: float
    aggregate_pf: float
    win_rate: float
    total_pnl: float


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

def _rolling_max_2d(arr, w):
    from numpy.lib.stride_tricks import sliding_window_view
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w-1:] = np.max(sliding_window_view(arr, w, axis=0), axis=-1)
    return out

def _rolling_min_2d(arr, w):
    from numpy.lib.stride_tricks import sliding_window_view
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w-1:] = np.min(sliding_window_view(arr, w, axis=0), axis=-1)
    return out

def _rolling_corr_2d(x, y, w):
    sx = _rolling_sum_2d(x, w)
    sy = _rolling_sum_2d(y, w)
    sx2 = _rolling_sum_2d(x**2, w)
    sy2 = _rolling_sum_2d(y**2, w)
    sxy = _rolling_sum_2d(x * y, w)
    numer = w * sxy - sx * sy
    dx = w * sx2 - sx**2
    dy = w * sy2 - sy**2
    denom = np.sqrt(np.maximum(dx, 0) * np.maximum(dy, 0))
    out = np.full_like(x, np.nan)
    valid = denom > 0
    out[valid] = numer[valid] / denom[valid]
    out[:w-1] = np.nan
    return out

def _rolling_slope_2d(arr, w):
    n = w
    i_vals = np.arange(n, dtype=np.float64)
    sum_i = i_vals.sum()
    sum_i2 = (i_vals**2).sum()
    denom = n * sum_i2 - sum_i**2
    if denom == 0:
        return np.full_like(arr, np.nan)
    n_bars = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n_bars < w:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    windows = sliding_window_view(arr, w, axis=0)
    weights = np.arange(n, dtype=np.float64)
    iy_sum = np.sum(windows * weights[np.newaxis, np.newaxis, :], axis=-1)
    y_sum = np.sum(windows, axis=-1)
    slopes = (n * iy_sum - sum_i * y_sum) / denom
    out[w-1:] = slopes
    return out

def _xs_rank_2d(arr):
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
    mean = np.nanmean(arr, axis=1, keepdims=True)
    std = np.nanstd(arr, axis=1, keepdims=True)
    std = np.where(std > 0, std, 1.0)
    return (arr - mean) / std

def _atr_2d(high, low, close, n=14):
    prev_close = np.full_like(close, np.nan)
    prev_close[1:] = close[:-1]
    hl = high - low
    hc = np.abs(high - prev_close)
    lc = np.abs(low - prev_close)
    tr = np.maximum(hl, np.maximum(hc, lc))
    tr[0] = hl[0]
    return _rolling_mean_2d(tr, n)


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
# PHASE 2: FEATURE ENGINEERING (only the ~20 candidates we need)
# ═══════════════════════════════════════════════════════════════════════════════

def _store(features, name, arr):
    a = np.array(arr, dtype=np.float32, copy=True)
    a[~np.isfinite(a)] = np.nan
    features[name] = a


def engineer_features(close, open_, high, low, volume, ts_grid, btc_idx):
    """Engineer ~20 candidate features — only the universal survivors from genesis."""
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    t0 = time.time()
    n_bars, n_symbols = close.shape
    features = {}

    # Intermediates
    bar_range = high - low
    safe_range = np.where(bar_range > 0, bar_range, 1e-10)
    body = np.abs(close - open_)
    safe_close = np.where(close > 0, close, 1e-10)
    atr_14 = _atr_2d(high, low, close, 14)
    safe_atr = np.where(atr_14 > 0, atr_14, 1e-10)

    ret_1 = np.full((n_bars, n_symbols), np.nan)
    valid_prev = (close[:-1] != 0) & ~np.isnan(close[:-1])
    ret_1[1:] = np.where(valid_prev, (close[1:] - close[:-1]) / np.where(valid_prev, close[:-1], 1.0), np.nan)
    abs_ret_1 = np.abs(ret_1)
    volume_z = _xs_zscore_2d(volume)

    # Returns at multiple scales
    ret_cache = {}
    for L in [5, 8, 13, 21]:
        ret_L = np.full((n_bars, n_symbols), np.nan)
        valid_L = (close[:-L] != 0) & ~np.isnan(close[:-L]) & ~np.isnan(close[L:])
        ret_L[L:] = np.where(valid_L, (close[L:] - close[:-L]) / np.where(valid_L, close[:-L], 1.0), np.nan)
        ret_cache[L] = ret_L

    logger.info("  Engineering candidate features...")

    # ── Momentum features ──
    vol_5 = _rolling_std_2d(ret_1, 5)
    safe_v5 = np.where(vol_5 > 0, vol_5, 1e-10)
    _store(features, "ret_norm_5", ret_cache[5] / safe_v5)

    vol_21 = _rolling_std_2d(ret_1, 21)
    safe_v21 = np.where(vol_21 > 0, vol_21, 1e-10)
    _store(features, "ret_norm_21", ret_cache[21] / safe_v21)

    vol_13 = _rolling_std_2d(ret_1, 13)
    safe_v13 = np.where(vol_13 > 0, vol_13, 1e-10)
    _store(features, "ret_norm_13", ret_cache[13] / safe_v13)

    # Relative strength
    univ_mean_ret = np.nanmean(ret_cache[21], axis=1, keepdims=True)
    _store(features, "relative_strength_21", ret_cache[21] - univ_mean_ret)

    # Cross-sectional ranks
    _store(features, "xs_ret_rank_5", _xs_rank_2d(ret_cache[5]))
    _store(features, "xs_ret_rank_21", _xs_rank_2d(ret_cache[21]))

    # Rank velocity
    xr5 = features["xs_ret_rank_5"]
    rv5 = np.full((n_bars, n_symbols), np.nan)
    rv5[5:] = xr5[5:] - xr5[:-5]
    _store(features, "xs_rank_velocity_5", rv5)

    # ── Position features ──
    rmax_21 = _rolling_max_2d(close, 21)
    rmin_21 = _rolling_min_2d(close, 21)
    rng_21 = rmax_21 - rmin_21
    safe_rng_21 = np.where(rng_21 > 0, rng_21, 1e-10)
    _store(features, "pos_in_range_21", (close - rmin_21) / safe_rng_21)

    high_21 = _rolling_max_2d(high, 21)
    low_21 = _rolling_min_2d(low, 21)
    _store(features, "dist_from_high_21", (high_21 - close) / safe_atr)
    _store(features, "dist_from_low_21", (close - low_21) / safe_atr)

    # ── Volatility features ──
    log_hl = np.log(np.where(high > 0, high, 1e-10)) - np.log(np.where(low > 0, low, 1e-10))
    _store(features, "parkinson_vol_21", np.sqrt(_rolling_mean_2d(log_hl**2, 21) / (4 * np.log(2))))

    log_co = np.log(np.where(close > 0, close, 1e-10)) - np.log(np.where(open_ > 0, open_, 1e-10))
    gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    _store(features, "garman_klass_21", np.sqrt(np.abs(_rolling_mean_2d(gk, 21))))

    _store(features, "atr_14_norm", atr_14 / safe_close)
    _store(features, "range_pct", bar_range / safe_close)

    # ── Volume features ──
    _store(features, "volume_z_20", _rolling_mean_2d(volume_z, 20))
    _store(features, "vol_z_max_6", _rolling_max_2d(volume_z, 6))

    # VWAP distance
    vwap_num = _rolling_sum_2d(close * volume, 21)
    vwap_den = _rolling_sum_2d(volume, 21)
    safe_vd = np.where(vwap_den > 0, vwap_den, 1e-10)
    vwap = vwap_num / safe_vd
    _store(features, "vwap_distance_21", (close - vwap) / safe_atr)

    up_vol = np.where(ret_1 > 0, volume, 0)
    down_vol = np.where(ret_1 < 0, volume, 0)
    up_sum = _rolling_sum_2d(up_vol, 21)
    down_sum = _rolling_sum_2d(down_vol, 21)
    safe_ds = np.where(down_sum > 0, down_sum, 1e-10)
    _store(features, "volume_asymmetry_21", up_sum / safe_ds)

    # ── Efficiency / quality features ──
    abs_ret_sum_8 = _rolling_sum_2d(abs_ret_1, 8)
    safe_ars = np.where(abs_ret_sum_8 > 0, abs_ret_sum_8, 1e-10)
    _store(features, "efficiency_ratio_8", np.abs(ret_cache[8]) / safe_ars)

    rn5 = features["ret_norm_5"]
    rn21 = features["ret_norm_21"]
    _store(features, "momentum_quality_divergence", rn5 - rn21)

    # ── Tail / distribution features ──
    if n_bars >= 21:
        from numpy.lib.stride_tricks import sliding_window_view
        c_win = sliding_window_view(close, 21, axis=0)
        cum_max = np.maximum.accumulate(c_win, axis=-1)
        dd = (c_win - cum_max) / np.where(cum_max > 0, cum_max, 1e-10)
        mdd = np.min(dd, axis=-1)
        md = np.full((n_bars, n_symbols), np.nan)
        md[20:] = mdd
        _store(features, "max_drawdown_21", md)

    elapsed = time.time() - t0
    logger.info("  Engineered %d candidate features in %.1fs", len(features), elapsed)
    return features


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: FEATURE SELECTION (simple FM IC → top 8)
# ═══════════════════════════════════════════════════════════════════════════════

def batch_fama_macbeth_ic(features_dict, fwd_ret, bar_mask):
    """Compute Fama-MacBeth IC for ALL features. Vectorized across features."""
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


def select_top_features(features, fwd_ret, bar_mask, fold_idx):
    """Select top MAX_FEATURES by |IC t-stat|. Simple and brutal."""
    t0 = time.time()
    logger.info("    [Fold %d] Feature selection: %d candidates → top %d",
                fold_idx, len(features), MAX_FEATURES)

    all_ic = batch_fama_macbeth_ic(features, fwd_ret, bar_mask)

    # Filter by minimum t-stat
    candidates = [(name, ic, t, n) for name, (ic, t, n) in all_ic.items()
                  if abs(t) > IC_TSTAT_MIN]

    if not candidates:
        logger.info("      No features pass |t| > %.1f", IC_TSTAT_MIN)
        return []

    # Sort by |t-stat| descending, take top MAX_FEATURES
    candidates.sort(key=lambda x: -abs(x[2]))
    selected = candidates[:MAX_FEATURES]

    elapsed = time.time() - t0
    for name, ic, t, n in selected:
        logger.info("      %s: IC=%.4f, t=%.2f, n=%d", name, ic, t, n)
    logger.info("    Selected %d features in %.1fs", len(selected), elapsed)

    return [(name, ic, t) for name, ic, t, n in selected]


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: MODEL & SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_sparse_composite(features, selected):
    """Build IC-weighted composite from selected features. No interactions."""
    n_bars, n_symbols = next(iter(features.values())).shape
    composite = np.zeros((n_bars, n_symbols), dtype=np.float64)
    total_weight = 0.0

    for name, ic, t_stat in selected:
        f = features[name].astype(np.float64)
        mean_xs = np.nanmean(f, axis=1, keepdims=True)
        std_xs = np.nanstd(f, axis=1, keepdims=True)
        safe_std = np.where(std_xs > 0, std_xs, 1e-10)
        z = (f - mean_xs) / safe_std
        w = ic  # sign already in ic
        composite += w * z
        total_weight += abs(w)

    if total_weight > 0:
        composite /= total_weight

    return composite


def generate_signals(composite, bar_mask, k_pct, symbols):
    """Generate LONG/SHORT signals from composite. Top/bottom k_pct%."""
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
                signals.append(Signal(symbols[s_idx], "long", t, score))
            elif ranks[s_idx] <= threshold_low:
                score = (threshold_low - ranks[s_idx]) / (threshold_low + 1e-10)
                signals.append(Signal(symbols[s_idx], "short", t, score))

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATION (fixed: slippage on ALL exits)
# ═══════════════════════════════════════════════════════════════════════════════

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


def _make_exit_config(cfg):
    ec = ExitConfig()
    for key in ("trail_breakeven_r", "trail_activation_r", "trail_distance_r"):
        if hasattr(ec, key):
            setattr(ec, key, cfg[key])
    return ec


def simulate_trades(signals, ohlcv_data, slippage_bps, exit_config=None):
    """Simulate signals to trades. Slippage applied to ALL exits (including stop-loss)."""
    cfg = HORIZON_CONFIG
    stop_atr_mult = cfg["stop_atr_mult"]
    time_stop_bars = cfg["time_stop_bars"]

    if exit_config is None:
        exit_config = _make_exit_config(cfg)
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

        # Apply slippage to ALL exits (including stop-loss — fixed from genesis)
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

    return float(np.mean(pfs)) if pfs else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_folds(ts_grid, n_folds=N_FOLDS, holdout_days=HOLDOUT_DAYS, embargo_days=EMBARGO_DAYS):
    """Build expanding window folds."""
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


def run_walk_forward(features, fwd_ret, folds, symbols, ohlcv_data, jsonl_path):
    """Run walk-forward for h24. Sequential (few folds, simple model)."""
    exit_cfg = _make_exit_config(HORIZON_CONFIG)
    fold_results = []
    fold_features_sets = []

    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        t0 = time.time()
        logger.info("\n  ── Fold %d ──", fold_idx)

        # Feature selection on training data
        selected = select_top_features(features, fwd_ret, train_mask, fold_idx)
        if not selected:
            logger.info("    No features selected, skipping fold")
            fold_results.append(FoldResult(
                fold_idx=fold_idx, horizon=HORIZON, n_features=0,
                feature_names=[], feature_ics=[], n_trades=0,
                avg_daily_pf=0.0, aggregate_pf=0.0, win_rate=0.0, total_pnl=0.0,
            ))
            fold_features_sets.append(set())
            continue

        # Build composite
        composite = build_sparse_composite(features, selected)

        # Generate signals on test data
        test_signals = generate_signals(composite, test_mask, TOP_K_PCT, symbols)
        logger.info("    %d signals on test period", len(test_signals))

        # Simulate trades
        trades = simulate_trades(test_signals, ohlcv_data, BASE_SLIPPAGE_1H, exit_cfg)

        # Metrics
        if trades:
            avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
            total_pnl = sum(t.pnl for t in trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            win_rate = wins / len(trades)
            gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
            gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
            agg_pf = gross_wins / gross_losses if gross_losses > 0 else 10.0
        else:
            avg_pf = total_pnl = win_rate = agg_pf = 0.0

        elapsed = time.time() - t0
        logger.info("    %d trades, avg daily PF=%.3f, agg PF=%.3f, WR=%.1f%%, PnL=$%.2f (%.1fs)",
                    len(trades), avg_pf, agg_pf, win_rate * 100, total_pnl, elapsed)

        # Exit reason breakdown
        if trades:
            reason_counts = defaultdict(int)
            for t in trades:
                reason_counts[t.exit_reason] += 1
            logger.info("    Exit reasons: %s", dict(reason_counts))

        feature_names = [s[0] for s in selected]
        feature_ics = [s[1] for s in selected]
        fr = FoldResult(
            fold_idx=fold_idx, horizon=HORIZON, n_features=len(selected),
            feature_names=feature_names, feature_ics=feature_ics,
            n_trades=len(trades), avg_daily_pf=avg_pf, aggregate_pf=agg_pf,
            win_rate=win_rate, total_pnl=total_pnl,
        )
        fold_results.append(fr)
        fold_features_sets.append(set(feature_names))

        # Incremental write
        with open(jsonl_path, "a") as f:
            f.write(json.dumps({"type": "fold", "fold_idx": fold_idx, **asdict(fr),
                                "timestamp": datetime.now(timezone.utc).isoformat()},
                               default=str) + "\n")

    # Aggregate
    valid_folds = [fr for fr in fold_results if fr.n_trades > 0]
    if valid_folds:
        avg_pf = float(np.mean([fr.avg_daily_pf for fr in valid_folds]))
        consistency = sum(1 for fr in valid_folds if fr.avg_daily_pf > 1.0) / len(valid_folds)
        total_trades = sum(fr.n_trades for fr in valid_folds)
    else:
        avg_pf = consistency = total_trades = 0.0

    logger.info("\n  Walk-forward summary: avg daily PF=%.3f, consistency=%.1f%%, trades=%d",
                avg_pf, consistency * 100, total_trades)

    # Feature overlap diagnostic
    if len(fold_features_sets) >= 2:
        for i in range(len(fold_features_sets)):
            for j in range(i + 1, len(fold_features_sets)):
                a, b = fold_features_sets[i], fold_features_sets[j]
                if a and b:
                    overlap = len(a & b)
                    union = len(a | b)
                    logger.info("    Fold %d vs %d: %d/%d features shared (Jaccard=%.2f)",
                                i, j, overlap, union, overlap / union if union > 0 else 0)

    return fold_results, avg_pf, consistency, total_trades, fold_features_sets


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


def run_holdout(features, fwd_ret, holdout_mask, symbols, ohlcv_data, n_features_tested):
    """Run holdout at 1.5x slippage."""
    exit_cfg = _make_exit_config(HORIZON_CONFIG)

    # Full training = everything before holdout
    full_train_mask = np.zeros(len(holdout_mask), dtype=bool)
    holdout_start = np.where(holdout_mask)[0]
    if len(holdout_start) == 0:
        return None
    full_train_mask[:holdout_start[0]] = True

    logger.info("  Holdout: feature selection on %d training bars...", full_train_mask.sum())

    selected = select_top_features(features, fwd_ret, full_train_mask, -1)
    if not selected:
        logger.info("  Holdout: no features selected")
        return None

    composite = build_sparse_composite(features, selected)

    holdout_signals = generate_signals(composite, holdout_mask, TOP_K_PCT, symbols)
    logger.info("  Holdout: %d signals", len(holdout_signals))

    # 1.5x slippage
    slippage_15x = BASE_SLIPPAGE_1H * 1.5
    trades = simulate_trades(holdout_signals, ohlcv_data, slippage_15x, exit_cfg)

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

    dsr = deflated_sharpe_ratio(sharpe, n_features_tested, n_obs, skew_val, kurt_val)

    logger.info("  Holdout (1.5x slip): %d trades, avg daily PF=%.3f, agg PF=%.3f, "
                "WR=%.1f%%, Sharpe=%.3f, DSR=%.3f, PnL=$%.2f",
                len(trades), avg_pf, agg_pf, win_rate * 100, sharpe, dsr, total_pnl)

    # 2.0x slippage sensitivity
    trades_2x = simulate_trades(holdout_signals, ohlcv_data, BASE_SLIPPAGE_1H * 2.0, exit_cfg)
    if trades_2x:
        pf_2x = compute_avg_daily_pf(trades_2x, ohlcv_data, symbols)
        logger.info("  Holdout (2.0x slip): %d trades, avg daily PF=%.3f", len(trades_2x), pf_2x)
    else:
        pf_2x = 0.0

    return {
        "avg_daily_pf": avg_pf,
        "aggregate_pf": agg_pf,
        "dsr": dsr,
        "n_trades": len(trades),
        "sharpe": sharpe,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "features": [s[0] for s in selected],
        "feature_ics": [s[1] for s in selected],
        "pf_2x_slippage": pf_2x,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE ABLATION TEST
# ═══════════════════════════════════════════════════════════════════════════════

def run_feature_ablation(features, fwd_ret, folds, symbols, ohlcv_data, base_selected):
    """Remove each feature one-at-a-time and measure impact on PF.

    This tells us which features actually contribute vs add noise.
    """
    if len(base_selected) < 3:
        return {}

    exit_cfg = _make_exit_config(HORIZON_CONFIG)
    logger.info("\n  Feature ablation test (%d features)...", len(base_selected))

    ablation_results = {}
    for drop_idx in range(len(base_selected)):
        drop_name = base_selected[drop_idx][0]
        remaining = [s for i, s in enumerate(base_selected) if i != drop_idx]

        composite = build_sparse_composite(features, remaining)

        # Test on all folds
        fold_pfs = []
        total_trades = 0
        for fold_idx, (train_mask, test_mask) in enumerate(folds):
            signals = generate_signals(composite, test_mask, TOP_K_PCT, symbols)
            trades = simulate_trades(signals, ohlcv_data, BASE_SLIPPAGE_1H, exit_cfg)
            if trades:
                fold_pfs.append(compute_avg_daily_pf(trades, ohlcv_data, symbols))
                total_trades += len(trades)

        avg_pf_without = float(np.mean(fold_pfs)) if fold_pfs else 0.0
        ablation_results[drop_name] = {
            "avg_pf_without": avg_pf_without,
            "n_trades": total_trades,
        }
        logger.info("    Drop %s: avg PF=%.3f (%d trades)", drop_name, avg_pf_without, total_trades)

    return ablation_results


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global MAX_FEATURES

    parser = argparse.ArgumentParser(description="Signal Genesis Sparse — Top-Feature Minimal Model")
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--quick", action="store_true", help="Quick mode: 2 folds")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    parser.add_argument("--max-features", type=int, default=MAX_FEATURES, help="Max features to select")
    args = parser.parse_args()

    if args.max_features:
        MAX_FEATURES = args.max_features

    t_start = time.time()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    n_folds = 2 if args.quick else N_FOLDS

    logger.info("=" * 70)
    logger.info("SIGNAL GENESIS SPARSE — Top-Feature Minimal Model (h24)")
    logger.info("=" * 70)
    logger.info("Config: days=%d, horizon=%d, folds=%d, holdout=%d days, max_features=%d, K=%d%%",
                args.days, HORIZON, n_folds, args.holdout_days, MAX_FEATURES, TOP_K_PCT)

    # ── Phase 1: Data Loading ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 1: Data Loading")
    logger.info("-" * 50)

    close, open_, high, low, volume, ts_grid, symbols, btc_idx, ohlcv_data = \
        load_ohlcv_matrices(database_url, args.days)

    logger.info("Computing forward returns (h=%d)...", HORIZON)
    fwd_ret = compute_forward_returns(close)

    # ── Phase 2: Feature Engineering ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 2: Feature Engineering (~20 candidates)")
    logger.info("-" * 50)

    features = engineer_features(close, open_, high, low, volume, ts_grid, btc_idx)

    # ── Phase 3-4: Walk-Forward ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 3-4: Walk-Forward Validation")
    logger.info("-" * 50)

    folds, holdout_mask = build_folds(ts_grid, n_folds=n_folds, holdout_days=args.holdout_days)
    if not folds:
        logger.error("No folds could be constructed. Need more data.")
        sys.exit(1)

    script_dir = os.path.dirname(__file__)
    jsonl_path = os.path.join(script_dir, "signal_genesis_sparse_incremental.jsonl")

    fold_results, avg_pf, consistency, total_trades, fold_features = \
        run_walk_forward(features, fwd_ret, folds, symbols, ohlcv_data, jsonl_path)

    # ── Phase 5: Holdout ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 5: Holdout Evaluation")
    logger.info("-" * 50)

    wf_pass = (avg_pf > 1.0 and consistency >= MIN_FOLD_CONSISTENCY
               and total_trades >= MIN_TRADES_TOTAL)

    holdout_result = None
    if wf_pass:
        holdout_result = run_holdout(
            features, fwd_ret, holdout_mask, symbols, ohlcv_data,
            n_features_tested=len(features)
        )
    else:
        logger.info("  SKIPPING holdout (walk-forward failed: PF=%.3f, cons=%.1f%%, trades=%d)",
                    avg_pf, consistency * 100, total_trades)

    # ── Phase 6: Feature Ablation ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 6: Feature Ablation")
    logger.info("-" * 50)

    # Use features from the last fold for ablation (most training data)
    last_fold_selected = None
    for fr in reversed(fold_results):
        if fr.n_features > 0:
            last_fold_selected = list(zip(fr.feature_names, fr.feature_ics,
                                          [0.0] * len(fr.feature_names)))
            break

    ablation_results = {}
    if last_fold_selected:
        ablation_results = run_feature_ablation(
            features, fwd_ret, folds, symbols, ohlcv_data, last_fold_selected
        )

    # ── Phase 7: Report ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 7: Final Report")
    logger.info("-" * 50)

    elapsed_total = time.time() - t_start

    # Determine verdict
    if not wf_pass:
        verdict = "FAIL_WALK_FORWARD"
        fail_reasons = []
        if avg_pf <= 1.0:
            fail_reasons.append(f"avg_daily_pf={avg_pf:.3f} <= 1.0")
        if consistency < MIN_FOLD_CONSISTENCY:
            fail_reasons.append(f"consistency={consistency:.2f} < {MIN_FOLD_CONSISTENCY}")
        if total_trades < MIN_TRADES_TOTAL:
            fail_reasons.append(f"trades={total_trades} < {MIN_TRADES_TOTAL}")
    elif holdout_result is None:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = ["no holdout result"]
    elif holdout_result["avg_daily_pf"] <= 1.0:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = [f"holdout_avg_daily_pf={holdout_result['avg_daily_pf']:.3f} <= 1.0"]
    elif holdout_result["dsr"] <= 0:
        verdict = "FAIL_DSR"
        fail_reasons = [f"DSR={holdout_result['dsr']:.3f} <= 0"]
    else:
        verdict = "CONFIRMED"
        fail_reasons = []

    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed_total,
        "model": "sparse_top_features",
        "horizon": HORIZON,
        "max_features": MAX_FEATURES,
        "k_pct": TOP_K_PCT,
        "n_candidate_features": len(features),
        "walk_forward": {
            "avg_daily_pf": avg_pf,
            "consistency": consistency,
            "total_trades": int(total_trades),
            "folds": [asdict(fr) for fr in fold_results],
        },
        "holdout": holdout_result if holdout_result else {
            "avg_daily_pf": 0.0, "aggregate_pf": 0.0, "dsr": -99.0,
            "n_trades": 0, "features": [],
        },
        "feature_ablation": ablation_results,
        "verdict": verdict,
        "fail_reasons": fail_reasons,
    }

    output_path = os.path.join(script_dir, "signal_genesis_sparse_results.json")
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Results written to %s", output_path)

    # Print summary
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info("Elapsed: %.1f min", elapsed_total / 60)
    logger.info("Verdict: %s", verdict)
    logger.info("Walk-forward: avg daily PF=%.3f, consistency=%.1f%%, trades=%d",
                avg_pf, consistency * 100, total_trades)
    if holdout_result:
        logger.info("Holdout: avg daily PF=%.3f, DSR=%.3f, trades=%d",
                    holdout_result["avg_daily_pf"], holdout_result["dsr"],
                    holdout_result["n_trades"])
    if fail_reasons:
        for r in fail_reasons:
            logger.info("  Fail: %s", r)

    # Feature overlap across folds
    all_feat_sets = [s for s in fold_features if s]
    if all_feat_sets:
        universal = set.intersection(*all_feat_sets)
        logger.info("Features in ALL folds: %s", sorted(universal))

    if ablation_results:
        logger.info("\nAblation impact (PF without each feature):")
        base_pf = avg_pf
        for name, info in sorted(ablation_results.items(), key=lambda x: x[1]["avg_pf_without"]):
            delta = info["avg_pf_without"] - base_pf
            logger.info("  %s: PF=%.3f (delta=%.3f)", name, info["avg_pf_without"], delta)


if __name__ == "__main__":
    main()
