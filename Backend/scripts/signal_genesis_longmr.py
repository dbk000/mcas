#!/usr/bin/env python3
"""
Signal Genesis Long-Only MR — Long-Only Quality / Mean-Reversion (h24)
=======================================================================

Hypothesis: The universal negative IC across momentum/volatility features
implies an anti-momentum, short-vol composite. Previous L/S tests lost money.
In crypto, the SHORT side (high-vol, momentum assets) gets crushed by spikes.
This script tests LONG-ONLY on the quality/low-drawdown side: go long on
low-vol, shallow-drawdown, recently-underperforming assets.

Key differences from signal_genesis_sparse.py:
- LONG-ONLY: no short signals, only top K% of IC-weighted composite
- Multiple exit configs swept per fold: time-stop-only (24/36), wide 3ATR, standard 2ATR
- K% swept: [1, 2, 3, 5]
- Fixed avg_daily_pf: min-trades-per-day filter (≥3) + median daily PF reported
- L/S diagnostic: split analysis to prove asymmetry hypothesis
- Same slippage-on-all-exits treatment

Usage:
    tmux new -s longmr
    docker compose run --rm -T worker python3 /scripts/signal_genesis_longmr.py --days 110
    # Quick smoke test:
    docker compose run --rm -T worker python3 /scripts/signal_genesis_longmr.py --days 30 --quick
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
BTC_SYMBOL = "BTCUSDT"
BASE_SLIPPAGE_1H = 5.0
MIN_BARS_PER_SYMBOL = 100
HORIZON = 24

# Exit configs to sweep per fold:
#   time24: pure time-stop 24 bars (no ATR stop, no trail)
#   time36: pure time-stop 36 bars
#   wide36: 3.0 ATR stop + time 36 (no trail)
#   std36:  2.0 ATR stop + time 36 + trail (control, same as genesis)
EXIT_CONFIGS = {
    "time24": {"stop_atr_mult": 0.0, "time_stop_bars": 24,
               "trail_activation_r": 999.0, "trail_distance_r": 999.0, "trail_breakeven_r": 0.0},
    "time36": {"stop_atr_mult": 0.0, "time_stop_bars": 36,
               "trail_activation_r": 999.0, "trail_distance_r": 999.0, "trail_breakeven_r": 0.0},
    "wide36": {"stop_atr_mult": 3.0, "time_stop_bars": 36,
               "trail_activation_r": 999.0, "trail_distance_r": 999.0, "trail_breakeven_r": 0.0},
    "std36":  {"stop_atr_mult": 2.0, "time_stop_bars": 36,
               "trail_activation_r": 1.5, "trail_distance_r": 1.0, "trail_breakeven_r": 0.0},
}

K_VALUES = [1, 2, 3, 5]

# Feature selection
MAX_FEATURES = 8
IC_TSTAT_MIN = 1.5

# Walk-forward config
N_FOLDS = 4
EMBARGO_DAYS = 1
HOLDOUT_DAYS = 18
MIN_TRADES_TOTAL = 25
MIN_FOLD_CONSISTENCY = 0.75
MIN_TRADES_PER_DAY = 3   # For robust daily PF calculation


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
class ComboResult:
    k_pct: int
    exit_name: str
    n_trades: int
    avg_daily_pf: float
    median_daily_pf: float
    robust_daily_pf: float
    aggregate_pf: float
    win_rate: float
    total_pnl: float
    exit_breakdown: dict


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
# PHASE 2: FEATURE ENGINEERING (~20 candidates, same as sparse)
# ═══════════════════════════════════════════════════════════════════════════════

def _store(features, name, arr):
    a = np.array(arr, dtype=np.float32, copy=True)
    a[~np.isfinite(a)] = np.nan
    features[name] = a


def engineer_features(close, open_, high, low, volume, ts_grid, btc_idx):
    """Engineer ~20 candidate features."""
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    t0 = time.time()
    n_bars, n_symbols = close.shape
    features = {}

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

    univ_mean_ret = np.nanmean(ret_cache[21], axis=1, keepdims=True)
    _store(features, "relative_strength_21", ret_cache[21] - univ_mean_ret)

    _store(features, "xs_ret_rank_5", _xs_rank_2d(ret_cache[5]))
    _store(features, "xs_ret_rank_21", _xs_rank_2d(ret_cache[21]))

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

    # ── Vol ratio feature ──
    vol_8 = _rolling_std_2d(ret_1, 8)
    safe_vol_21 = np.where(vol_21 > 0, vol_21, 1e-10)
    _store(features, "vol_ratio_8_21", vol_8 / safe_vol_21)

    elapsed = time.time() - t0
    logger.info("  Engineered %d candidate features in %.1fs", len(features), elapsed)
    return features


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: FEATURE SELECTION (FM IC → top 8)
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
    """Select top MAX_FEATURES by |IC t-stat|."""
    t0 = time.time()
    logger.info("    [Fold %d] Feature selection: %d candidates → top %d",
                fold_idx, len(features), MAX_FEATURES)

    all_ic = batch_fama_macbeth_ic(features, fwd_ret, bar_mask)

    candidates = [(name, ic, t, n) for name, (ic, t, n) in all_ic.items()
                  if abs(t) > IC_TSTAT_MIN]

    if not candidates:
        logger.info("      No features pass |t| > %.1f", IC_TSTAT_MIN)
        return []

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

def build_composite(features, selected):
    """Build IC-weighted composite from selected features."""
    n_bars, n_symbols = next(iter(features.values())).shape
    composite = np.zeros((n_bars, n_symbols), dtype=np.float64)
    total_weight = 0.0

    for name, ic, t_stat in selected:
        f = features[name].astype(np.float64)
        mean_xs = np.nanmean(f, axis=1, keepdims=True)
        std_xs = np.nanstd(f, axis=1, keepdims=True)
        safe_std = np.where(std_xs > 0, std_xs, 1e-10)
        z = (f - mean_xs) / safe_std
        w = ic
        composite += w * z
        total_weight += abs(w)

    if total_weight > 0:
        composite /= total_weight

    return composite


def generate_long_signals(composite, bar_mask, k_pct, symbols):
    """Generate LONG-ONLY signals. Top k_pct% of composite (highest scores)."""
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

        for s_idx in range(n_symbols):
            if np.isnan(ranks[s_idx]):
                continue
            if ranks[s_idx] >= threshold_high:
                score = (ranks[s_idx] - threshold_high) / (1.0 - threshold_high + 1e-10)
                signals.append(Signal(symbols[s_idx], "long", t, score))

    return signals


def generate_ls_signals(composite, bar_mask, k_pct, symbols):
    """Generate both LONG and SHORT signals for L/S diagnostic."""
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
# TRADE SIMULATION (parameterized exit config)
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


def simulate_trades(signals, ohlcv_data, slippage_bps, exit_cfg_dict):
    """Simulate signals to trades with given exit config dict.
    Slippage applied to ALL exits including stop-loss."""
    stop_atr_mult = exit_cfg_dict["stop_atr_mult"]
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
            if stop_atr_mult > 0:
                stop = entry - stop_atr_mult * atr
            else:
                stop = 0.0  # No stop — never triggers for longs
        else:
            entry = entry_raw * (1 - slippage_bps / 10000)
            if stop_atr_mult > 0:
                stop = entry + stop_atr_mult * atr
            else:
                stop = entry * 100  # No stop — never triggers for shorts
            if entry < 1.00:
                continue

        if entry <= 0:
            continue

        # r_value: when no stop, use 2% of entry as synthetic R
        if stop_atr_mult > 0:
            r_value = abs(entry - stop)
        else:
            r_value = entry * 0.02

        if r_value <= 0 or (stop_atr_mult > 0 and r_value / entry > 0.10):
            continue
        if stop_atr_mult > 0 and 2 * entry * FEE_BPS / 10000 > 0.3 * r_value:
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

        # Apply slippage to ALL exits
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


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS (FIXED: robust daily PF + median daily PF)
# ═══════════════════════════════════════════════════════════════════════════════

def _daily_pnl_buckets(trades, ohlcv_data, symbols):
    """Group trades by day, return dict of day → {wins, losses, n_trades}."""
    if not trades:
        return {}

    ts_grid = ohlcv_data[(symbols[0], '1h')]["ts"]
    daily = defaultdict(lambda: {"wins": 0.0, "losses": 0.0, "n_trades": 0})
    for t in trades:
        ts = ts_grid[t.ts_idx]
        day = int(ts // 86400)
        daily[day]["n_trades"] += 1
        if t.pnl > 0:
            daily[day]["wins"] += t.pnl
        elif t.pnl < 0:
            daily[day]["losses"] += abs(t.pnl)

    return daily


def compute_avg_daily_pf(trades, ohlcv_data, symbols):
    """Original avg daily PF (for comparison with prior scripts). Caps at 10.0."""
    daily = _daily_pnl_buckets(trades, ohlcv_data, symbols)
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


def compute_robust_daily_pf(trades, ohlcv_data, symbols, min_trades=MIN_TRADES_PER_DAY):
    """Robust avg daily PF: only counts days with >= min_trades.
    Caps PF at 5.0 to reduce outlier influence. This is the primary metric."""
    daily = _daily_pnl_buckets(trades, ohlcv_data, symbols)
    if not daily:
        return 0.0

    pfs = []
    for day_data in daily.values():
        if day_data["n_trades"] < min_trades:
            continue
        w = day_data["wins"]
        l = day_data["losses"]
        if l > 0:
            pfs.append(min(w / l, 5.0))
        elif w > 0:
            pfs.append(5.0)

    return float(np.mean(pfs)) if pfs else 0.0


def compute_median_daily_pf(trades, ohlcv_data, symbols, min_trades=MIN_TRADES_PER_DAY):
    """Median daily PF: fully robust to outlier days."""
    daily = _daily_pnl_buckets(trades, ohlcv_data, symbols)
    if not daily:
        return 0.0

    pfs = []
    for day_data in daily.values():
        if day_data["n_trades"] < min_trades:
            continue
        w = day_data["wins"]
        l = day_data["losses"]
        if l > 0:
            pfs.append(w / l)
        elif w > 0:
            pfs.append(5.0)

    return float(np.median(pfs)) if pfs else 0.0


def compute_trade_metrics(trades, ohlcv_data, symbols):
    """Compute all metrics for a set of trades. Returns dict."""
    if not trades:
        return {
            "n_trades": 0, "avg_daily_pf": 0.0, "median_daily_pf": 0.0,
            "robust_daily_pf": 0.0, "aggregate_pf": 0.0, "win_rate": 0.0,
            "total_pnl": 0.0, "exit_breakdown": {},
        }

    avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
    robust_pf = compute_robust_daily_pf(trades, ohlcv_data, symbols)
    median_pf = compute_median_daily_pf(trades, ohlcv_data, symbols)

    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades)
    gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
    agg_pf = gross_wins / gross_losses if gross_losses > 0 else 10.0

    reason_counts = defaultdict(int)
    for t in trades:
        reason_counts[t.exit_reason] += 1

    return {
        "n_trades": len(trades),
        "avg_daily_pf": avg_pf,
        "median_daily_pf": median_pf,
        "robust_daily_pf": robust_pf,
        "aggregate_pf": agg_pf,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "exit_breakdown": dict(reason_counts),
    }


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
    """Walk-forward with K × exit_config sweep. Returns combo results for all combos."""
    # For each fold: select features, build composite, then sweep all combos
    all_combo_results = defaultdict(list)  # (k, exit_name) → [fold metrics]
    fold_feature_info = []  # per-fold feature info for reporting

    n_combos = len(K_VALUES) * len(EXIT_CONFIGS)
    logger.info("  Sweeping %d combos (K=%s × exits=%s) across %d folds",
                n_combos, K_VALUES, list(EXIT_CONFIGS.keys()), len(folds))

    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        t0 = time.time()
        logger.info("\n  ── Fold %d ──", fold_idx)

        # Feature selection on training data
        selected = select_top_features(features, fwd_ret, train_mask, fold_idx)
        if not selected:
            logger.info("    No features selected, skipping fold")
            fold_feature_info.append({"fold_idx": fold_idx, "features": [], "ics": []})
            for k in K_VALUES:
                for exit_name in EXIT_CONFIGS:
                    all_combo_results[(k, exit_name)].append(None)
            continue

        fold_feature_info.append({
            "fold_idx": fold_idx,
            "features": [s[0] for s in selected],
            "ics": [s[1] for s in selected],
        })

        # Build composite (same for all combos)
        composite = build_composite(features, selected)

        # Sweep K × exit_config
        for k in K_VALUES:
            signals = generate_long_signals(composite, test_mask, k, symbols)

            for exit_name, exit_cfg in EXIT_CONFIGS.items():
                trades = simulate_trades(signals, ohlcv_data, BASE_SLIPPAGE_1H, exit_cfg)
                metrics = compute_trade_metrics(trades, ohlcv_data, symbols)
                all_combo_results[(k, exit_name)].append(metrics)

                if metrics["n_trades"] > 0:
                    logger.info("    K=%d%% %s: %d trades, robust_PF=%.3f, med_PF=%.3f, "
                                "agg_PF=%.3f, PnL=$%.0f",
                                k, exit_name, metrics["n_trades"],
                                metrics["robust_daily_pf"], metrics["median_daily_pf"],
                                metrics["aggregate_pf"], metrics["total_pnl"])

        elapsed = time.time() - t0
        logger.info("    Fold %d complete in %.1fs", fold_idx, elapsed)

        # Incremental write
        with open(jsonl_path, "a") as f:
            f.write(json.dumps({
                "type": "fold", "fold_idx": fold_idx,
                "features": fold_feature_info[-1]["features"],
                "ics": fold_feature_info[-1]["ics"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, default=str) + "\n")

    # ── Aggregate across folds for each combo ──
    logger.info("\n  ── Combo Summary ──")
    combo_summaries = {}
    for (k, exit_name), fold_metrics_list in sorted(all_combo_results.items()):
        valid = [m for m in fold_metrics_list if m is not None and m["n_trades"] > 0]
        if not valid:
            combo_summaries[(k, exit_name)] = {
                "k_pct": k, "exit_name": exit_name,
                "avg_robust_pf": 0.0, "avg_median_pf": 0.0, "avg_agg_pf": 0.0,
                "consistency_robust": 0.0, "consistency_median": 0.0,
                "total_trades": 0, "total_pnl": 0.0,
                "avg_win_rate": 0.0, "fold_metrics": fold_metrics_list,
            }
            continue

        avg_robust = float(np.mean([m["robust_daily_pf"] for m in valid]))
        avg_median = float(np.mean([m["median_daily_pf"] for m in valid]))
        avg_agg = float(np.mean([m["aggregate_pf"] for m in valid]))
        cons_robust = sum(1 for m in valid if m["robust_daily_pf"] > 1.0) / len(valid)
        cons_median = sum(1 for m in valid if m["median_daily_pf"] > 1.0) / len(valid)
        total_trades = sum(m["n_trades"] for m in valid)
        total_pnl = sum(m["total_pnl"] for m in valid)
        avg_wr = float(np.mean([m["win_rate"] for m in valid]))

        combo_summaries[(k, exit_name)] = {
            "k_pct": k, "exit_name": exit_name,
            "avg_robust_pf": avg_robust, "avg_median_pf": avg_median,
            "avg_agg_pf": avg_agg,
            "consistency_robust": cons_robust, "consistency_median": cons_median,
            "total_trades": total_trades, "total_pnl": total_pnl,
            "avg_win_rate": avg_wr, "fold_metrics": fold_metrics_list,
        }

        logger.info("    K=%d%% %-7s: robust=%.3f, median=%.3f, agg=%.3f, "
                    "cons=%.0f%%, trades=%d, PnL=$%.0f",
                    k, exit_name, avg_robust, avg_median, avg_agg,
                    cons_robust * 100, total_trades, total_pnl)

    # Find best combo by robust daily PF (primary) then median (tiebreak)
    best_key = max(combo_summaries.keys(),
                   key=lambda k: (combo_summaries[k]["avg_robust_pf"],
                                  combo_summaries[k]["avg_median_pf"]))
    best = combo_summaries[best_key]
    logger.info("\n  Best combo: K=%d%% %s (robust=%.3f, median=%.3f, agg=%.3f)",
                best["k_pct"], best["exit_name"],
                best["avg_robust_pf"], best["avg_median_pf"], best["avg_agg_pf"])

    # Write summary to JSONL
    with open(jsonl_path, "a") as f:
        f.write(json.dumps({
            "type": "walk_forward_summary",
            "best_k": best["k_pct"], "best_exit": best["exit_name"],
            "avg_robust_pf": best["avg_robust_pf"],
            "avg_median_pf": best["avg_median_pf"],
            "avg_agg_pf": best["avg_agg_pf"],
            "consistency_robust": best["consistency_robust"],
            "total_trades": best["total_trades"],
            "n_combos": n_combos,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, default=str) + "\n")

    return combo_summaries, best_key, fold_feature_info


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


def run_holdout(features, fwd_ret, holdout_mask, symbols, ohlcv_data,
                best_k, best_exit_name, n_combos_tested):
    """Run holdout using best combo from walk-forward."""
    exit_cfg = EXIT_CONFIGS[best_exit_name]

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

    composite = build_composite(features, selected)

    # LONG-ONLY signals on holdout
    holdout_signals = generate_long_signals(composite, holdout_mask, best_k, symbols)
    logger.info("  Holdout: %d LONG signals (K=%d%%)", len(holdout_signals), best_k)

    # Base slippage
    trades_base = simulate_trades(holdout_signals, ohlcv_data, BASE_SLIPPAGE_1H, exit_cfg)
    metrics_base = compute_trade_metrics(trades_base, ohlcv_data, symbols)

    if not trades_base:
        logger.info("  Holdout: 0 trades")
        return None

    # Sharpe + DSR
    pnl_arr = np.array([t.pnl for t in trades_base])
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

    dsr = deflated_sharpe_ratio(sharpe, n_combos_tested, n_obs, skew_val, kurt_val)

    logger.info("  Holdout (base slip): %d trades, robust_PF=%.3f, median_PF=%.3f, "
                "agg_PF=%.3f, WR=%.1f%%, Sharpe=%.3f, DSR=%.3f, PnL=$%.2f",
                len(trades_base), metrics_base["robust_daily_pf"],
                metrics_base["median_daily_pf"], metrics_base["aggregate_pf"],
                metrics_base["win_rate"] * 100, sharpe, dsr, metrics_base["total_pnl"])

    # 1.5x slippage sensitivity
    slippage_15x = BASE_SLIPPAGE_1H * 1.5
    trades_15x = simulate_trades(holdout_signals, ohlcv_data, slippage_15x, exit_cfg)
    metrics_15x = compute_trade_metrics(trades_15x, ohlcv_data, symbols)
    logger.info("  Holdout (1.5x slip): %d trades, robust_PF=%.3f, PnL=$%.2f",
                metrics_15x["n_trades"], metrics_15x["robust_daily_pf"], metrics_15x["total_pnl"])

    # 2.0x slippage sensitivity
    slippage_2x = BASE_SLIPPAGE_1H * 2.0
    trades_2x = simulate_trades(holdout_signals, ohlcv_data, slippage_2x, exit_cfg)
    metrics_2x = compute_trade_metrics(trades_2x, ohlcv_data, symbols)
    logger.info("  Holdout (2.0x slip): %d trades, robust_PF=%.3f, PnL=$%.2f",
                metrics_2x["n_trades"], metrics_2x["robust_daily_pf"], metrics_2x["total_pnl"])

    return {
        "best_k": best_k,
        "best_exit": best_exit_name,
        "exit_config": exit_cfg,
        **metrics_base,
        "sharpe": sharpe,
        "dsr": dsr,
        "features": [s[0] for s in selected],
        "feature_ics": [s[1] for s in selected],
        "slippage_15x": {
            "n_trades": metrics_15x["n_trades"],
            "robust_daily_pf": metrics_15x["robust_daily_pf"],
            "total_pnl": metrics_15x["total_pnl"],
        },
        "slippage_2x": {
            "n_trades": metrics_2x["n_trades"],
            "robust_daily_pf": metrics_2x["robust_daily_pf"],
            "total_pnl": metrics_2x["total_pnl"],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# L/S DIAGNOSTIC: prove asymmetry hypothesis
# ═══════════════════════════════════════════════════════════════════════════════

def run_ls_diagnostic(features, fwd_ret, folds, symbols, ohlcv_data):
    """Run standard L/S (like sparse.py) and split results by long/short side.
    Uses std36 exit config and K=2% (control conditions from prior experiments)."""
    logger.info("\n  ── L/S Diagnostic (proving asymmetry) ──")
    exit_cfg = EXIT_CONFIGS["std36"]
    k_pct = 2

    long_metrics_all = []
    short_metrics_all = []
    combined_metrics_all = []

    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        selected = select_top_features(features, fwd_ret, train_mask, fold_idx)
        if not selected:
            continue

        composite = build_composite(features, selected)
        ls_signals = generate_ls_signals(composite, test_mask, k_pct, symbols)

        long_signals = [s for s in ls_signals if s.direction == "long"]
        short_signals = [s for s in ls_signals if s.direction == "short"]

        long_trades = simulate_trades(long_signals, ohlcv_data, BASE_SLIPPAGE_1H, exit_cfg)
        short_trades = simulate_trades(short_signals, ohlcv_data, BASE_SLIPPAGE_1H, exit_cfg)
        all_trades = long_trades + short_trades

        long_m = compute_trade_metrics(long_trades, ohlcv_data, symbols)
        short_m = compute_trade_metrics(short_trades, ohlcv_data, symbols)
        all_m = compute_trade_metrics(all_trades, ohlcv_data, symbols)

        long_metrics_all.append(long_m)
        short_metrics_all.append(short_m)
        combined_metrics_all.append(all_m)

        logger.info("    Fold %d: LONG  %d trades, agg_PF=%.3f, PnL=$%.0f",
                    fold_idx, long_m["n_trades"], long_m["aggregate_pf"], long_m["total_pnl"])
        logger.info("    Fold %d: SHORT %d trades, agg_PF=%.3f, PnL=$%.0f",
                    fold_idx, short_m["n_trades"], short_m["aggregate_pf"], short_m["total_pnl"])
        logger.info("    Fold %d: L+S   %d trades, agg_PF=%.3f, PnL=$%.0f",
                    fold_idx, all_m["n_trades"], all_m["aggregate_pf"], all_m["total_pnl"])

    # Aggregate
    def _avg(metrics_list, key):
        valid = [m[key] for m in metrics_list if m["n_trades"] > 0]
        return float(np.mean(valid)) if valid else 0.0

    result = {
        "k_pct": k_pct,
        "exit_config": "std36",
        "long_side": {
            "avg_robust_pf": _avg(long_metrics_all, "robust_daily_pf"),
            "avg_agg_pf": _avg(long_metrics_all, "aggregate_pf"),
            "total_trades": sum(m["n_trades"] for m in long_metrics_all),
            "total_pnl": sum(m["total_pnl"] for m in long_metrics_all),
            "avg_win_rate": _avg(long_metrics_all, "win_rate"),
        },
        "short_side": {
            "avg_robust_pf": _avg(short_metrics_all, "robust_daily_pf"),
            "avg_agg_pf": _avg(short_metrics_all, "aggregate_pf"),
            "total_trades": sum(m["n_trades"] for m in short_metrics_all),
            "total_pnl": sum(m["total_pnl"] for m in short_metrics_all),
            "avg_win_rate": _avg(short_metrics_all, "win_rate"),
        },
        "combined": {
            "avg_robust_pf": _avg(combined_metrics_all, "robust_daily_pf"),
            "avg_agg_pf": _avg(combined_metrics_all, "aggregate_pf"),
            "total_trades": sum(m["n_trades"] for m in combined_metrics_all),
            "total_pnl": sum(m["total_pnl"] for m in combined_metrics_all),
            "avg_win_rate": _avg(combined_metrics_all, "win_rate"),
        },
    }

    logger.info("\n    L/S SUMMARY:")
    logger.info("      LONG:  robust=%.3f, agg=%.3f, WR=%.1f%%, PnL=$%.0f (%d trades)",
                result["long_side"]["avg_robust_pf"], result["long_side"]["avg_agg_pf"],
                result["long_side"]["avg_win_rate"] * 100,
                result["long_side"]["total_pnl"], result["long_side"]["total_trades"])
    logger.info("      SHORT: robust=%.3f, agg=%.3f, WR=%.1f%%, PnL=$%.0f (%d trades)",
                result["short_side"]["avg_robust_pf"], result["short_side"]["avg_agg_pf"],
                result["short_side"]["avg_win_rate"] * 100,
                result["short_side"]["total_pnl"], result["short_side"]["total_trades"])
    logger.info("      L+S:   robust=%.3f, agg=%.3f, WR=%.1f%%, PnL=$%.0f (%d trades)",
                result["combined"]["avg_robust_pf"], result["combined"]["avg_agg_pf"],
                result["combined"]["avg_win_rate"] * 100,
                result["combined"]["total_pnl"], result["combined"]["total_trades"])

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global MAX_FEATURES

    parser = argparse.ArgumentParser(description="Signal Genesis Long-Only MR")
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--quick", action="store_true", help="Quick mode: 2 folds")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    parser.add_argument("--max-features", type=int, default=MAX_FEATURES)
    parser.add_argument("--skip-diagnostic", action="store_true",
                        help="Skip L/S diagnostic to save time")
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
    logger.info("SIGNAL GENESIS LONG-ONLY MR — Quality/Mean-Reversion (h24)")
    logger.info("=" * 70)
    logger.info("Hypothesis: LONG-ONLY on top of IC-weighted composite.")
    logger.info("  The SHORT side in crypto gets crushed by spikes.")
    logger.info("  Testing if removing shorts reveals a hidden long-only edge.")
    logger.info("Config: days=%d, h=%d, folds=%d, holdout=%d, max_feat=%d",
                args.days, HORIZON, n_folds, args.holdout_days, MAX_FEATURES)
    logger.info("K sweep: %s", K_VALUES)
    logger.info("Exit configs: %s", list(EXIT_CONFIGS.keys()))
    logger.info("Metric fix: robust daily PF (min %d trades/day, cap 5.0) + median daily PF",
                MIN_TRADES_PER_DAY)

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
    logger.info("PHASE 2: Feature Engineering")
    logger.info("-" * 50)

    features = engineer_features(close, open_, high, low, volume, ts_grid, btc_idx)

    # ── Phase 3-4: Walk-Forward ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 3-4: Walk-Forward (K × exit sweep)")
    logger.info("-" * 50)

    folds, holdout_mask = build_folds(ts_grid, n_folds=n_folds, holdout_days=args.holdout_days)
    if not folds:
        logger.error("No folds could be constructed. Need more data.")
        sys.exit(1)

    script_dir = os.path.dirname(__file__)
    output_dir = os.path.join(script_dir, "signal_genesis_alt4")
    os.makedirs(output_dir, exist_ok=True)
    jsonl_path = os.path.join(output_dir, "signal_genesis_longmr_incremental.jsonl")

    combo_summaries, best_key, fold_feature_info = \
        run_walk_forward(features, fwd_ret, folds, symbols, ohlcv_data, jsonl_path)

    best_k, best_exit_name = best_key
    best = combo_summaries[best_key]
    n_combos = len(K_VALUES) * len(EXIT_CONFIGS)

    # ── Phase 5: L/S Diagnostic ──
    ls_result = None
    if not args.skip_diagnostic:
        logger.info("\n" + "-" * 50)
        logger.info("PHASE 5: L/S Diagnostic")
        logger.info("-" * 50)
        ls_result = run_ls_diagnostic(features, fwd_ret, folds, symbols, ohlcv_data)

    # ── Phase 6: Holdout ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 6: Holdout Evaluation")
    logger.info("-" * 50)

    # Gate: robust PF > 1.0 from walk-forward
    wf_pass = (best["avg_robust_pf"] > 1.0
               and best["consistency_robust"] >= MIN_FOLD_CONSISTENCY
               and best["total_trades"] >= MIN_TRADES_TOTAL)

    holdout_result = None
    if wf_pass:
        holdout_result = run_holdout(
            features, fwd_ret, holdout_mask, symbols, ohlcv_data,
            best_k, best_exit_name, n_combos,
        )
        # Write holdout to JSONL
        if holdout_result:
            with open(jsonl_path, "a") as f:
                f.write(json.dumps({
                    "type": "holdout", **{k: v for k, v in holdout_result.items()
                                          if k != "exit_config"},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, default=str) + "\n")
    else:
        logger.info("  SKIPPING holdout (walk-forward failed: robust_PF=%.3f, cons=%.0f%%, trades=%d)",
                    best["avg_robust_pf"], best["consistency_robust"] * 100, best["total_trades"])

    # ── Phase 7: Final Report ──
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 7: Final Report")
    logger.info("-" * 50)

    elapsed_total = time.time() - t_start

    # Determine verdict
    if not wf_pass:
        verdict = "FAIL_WALK_FORWARD"
        fail_reasons = []
        if best["avg_robust_pf"] <= 1.0:
            fail_reasons.append(f"robust_daily_pf={best['avg_robust_pf']:.3f} <= 1.0")
        if best["consistency_robust"] < MIN_FOLD_CONSISTENCY:
            fail_reasons.append(f"consistency={best['consistency_robust']:.2f} < {MIN_FOLD_CONSISTENCY}")
        if best["total_trades"] < MIN_TRADES_TOTAL:
            fail_reasons.append(f"trades={best['total_trades']} < {MIN_TRADES_TOTAL}")
    elif holdout_result is None:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = ["no holdout result"]
    elif holdout_result["robust_daily_pf"] <= 1.0:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = [f"holdout_robust_pf={holdout_result['robust_daily_pf']:.3f} <= 1.0"]
    elif holdout_result["dsr"] <= 0:
        verdict = "FAIL_DSR"
        fail_reasons = [f"DSR={holdout_result['dsr']:.3f} <= 0"]
    else:
        verdict = "CONFIRMED"
        fail_reasons = []

    # Build serializable combo summaries (strip non-serializable fold_metrics)
    combo_report = {}
    for (k, en), summary in sorted(combo_summaries.items()):
        combo_key = f"K{k}_{en}"
        combo_report[combo_key] = {
            "k_pct": k, "exit_name": en,
            "avg_robust_pf": summary["avg_robust_pf"],
            "avg_median_pf": summary["avg_median_pf"],
            "avg_agg_pf": summary["avg_agg_pf"],
            "consistency_robust": summary["consistency_robust"],
            "consistency_median": summary["consistency_median"],
            "total_trades": summary["total_trades"],
            "total_pnl": summary["total_pnl"],
            "avg_win_rate": summary["avg_win_rate"],
        }

    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed_total,
        "model": "long_only_mr",
        "hypothesis": "LONG-ONLY on IC-weighted composite. Remove shorts to test "
                      "if hidden long-only edge exists (short side destroyed by crypto spikes).",
        "horizon": HORIZON,
        "max_features": MAX_FEATURES,
        "k_values_tested": K_VALUES,
        "exit_configs_tested": list(EXIT_CONFIGS.keys()),
        "exit_config_details": EXIT_CONFIGS,
        "n_combos": n_combos,
        "metric_fix": {
            "min_trades_per_day": MIN_TRADES_PER_DAY,
            "pf_cap": 5.0,
            "description": "robust_daily_pf = mean of daily PF (capped at 5.0) for days with >= 3 trades",
        },
        "walk_forward": {
            "best_k": best_k,
            "best_exit": best_exit_name,
            "avg_robust_pf": best["avg_robust_pf"],
            "avg_median_pf": best["avg_median_pf"],
            "avg_agg_pf": best["avg_agg_pf"],
            "consistency_robust": best["consistency_robust"],
            "total_trades": best["total_trades"],
            "total_pnl": best["total_pnl"],
            "all_combos": combo_report,
        },
        "fold_features": fold_feature_info,
        "ls_diagnostic": ls_result,
        "holdout": holdout_result if holdout_result else {
            "robust_daily_pf": 0.0, "aggregate_pf": 0.0, "dsr": -99.0,
            "n_trades": 0, "features": [],
        },
        "verdict": verdict,
        "n_trials_for_dsr": n_combos,
        "fail_reasons": fail_reasons,
    }

    output_path = os.path.join(output_dir, "signal_genesis_longmr_results.json")
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Results written to %s", output_path)

    # ── Print Summary ──
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info("Elapsed: %.1f min", elapsed_total / 60)
    logger.info("Verdict: %s", verdict)
    logger.info("Best combo: K=%d%% %s", best_k, best_exit_name)
    logger.info("Walk-forward: robust_PF=%.3f, median_PF=%.3f, agg_PF=%.3f, "
                "consistency=%.0f%%, trades=%d, PnL=$%.0f",
                best["avg_robust_pf"], best["avg_median_pf"], best["avg_agg_pf"],
                best["consistency_robust"] * 100, best["total_trades"], best["total_pnl"])

    if holdout_result:
        logger.info("Holdout: robust_PF=%.3f, median_PF=%.3f, Sharpe=%.3f, DSR=%.3f, "
                    "trades=%d, PnL=$%.0f",
                    holdout_result["robust_daily_pf"], holdout_result["median_daily_pf"],
                    holdout_result["sharpe"], holdout_result["dsr"],
                    holdout_result["n_trades"], holdout_result["total_pnl"])

    if ls_result:
        logger.info("\nL/S Asymmetry Test:")
        logger.info("  LONG  side: agg_PF=%.3f, PnL=$%.0f (%d trades)",
                    ls_result["long_side"]["avg_agg_pf"],
                    ls_result["long_side"]["total_pnl"],
                    ls_result["long_side"]["total_trades"])
        logger.info("  SHORT side: agg_PF=%.3f, PnL=$%.0f (%d trades)",
                    ls_result["short_side"]["avg_agg_pf"],
                    ls_result["short_side"]["total_pnl"],
                    ls_result["short_side"]["total_trades"])

    if fail_reasons:
        logger.info("\nFail reasons:")
        for r in fail_reasons:
            logger.info("  - %s", r)

    # Feature overlap across folds
    all_feat_sets = [set(fi["features"]) for fi in fold_feature_info if fi["features"]]
    if all_feat_sets:
        universal = set.intersection(*all_feat_sets)
        logger.info("\nFeatures in ALL folds: %s", sorted(universal))


if __name__ == "__main__":
    main()
