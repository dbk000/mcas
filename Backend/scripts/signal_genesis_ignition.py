#!/usr/bin/env python3
"""
U1 Ignition Discovery — Event-Based Pre-Ignition Detector
============================================================

Scans 1h OHLCV for symbols achieving large upward moves within 24 hours.
Uses event-vs-control classification (NOT cross-sectional ranking) to find
raw statistical fingerprints that distinguish pre-ignition bars from normal bars.

Methodology:
  1. Build ignition event catalog (max forward high within 24 bars >= threshold)
  2. Systematic feature generation: 5 data streams × 4 windows × 9 statistics
     = 180 raw features (no named TA indicators)
  3. Event-vs-control screening: Mann-Whitney U, rank-biserial, lift by quantile, MI
  4. Iterative discovery loop: generate new features from what data reveals,
     converge when marginal MI < 50% of median Round 1 MI
  5. Conditional probability scoring: binned lift model with interaction terms
  6. Absolute signal generation: fire when log-score > threshold (not ranking)
  7. Trade simulation: long-only, dynamic slippage, position dedup
  8. Walk-forward: 3 folds, expanding window, 2-day embargo
  9. Holdout at 1.5× slippage, DSR correction
  10. Report

Usage:
    tmux new -s ignition
    docker compose run --rm -T worker python3 /scripts/signal_genesis_ignition.py --days 110
    # Quick smoke test:
    docker compose run --rm -T worker python3 /scripts/signal_genesis_ignition.py --days 30 --quick
"""

# ─── Standard library ────────────────────────────────────────────────────────

import os
import sys
import time
import json
import math
import argparse
import logging
import multiprocessing as mp
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

# ─── BLAS thread control (before numpy import) ───────────────────────────────
# Single-process phases: 4 BLAS threads. Pool phases: reset to 1 per worker.
_BLAS_KEYS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')
for _k in _BLAS_KEYS:
    os.environ.setdefault(_k, '4')

# ─── Third-party ──────────────────────────────────────────────────────────────

import numpy as np
import psycopg
from exit_checker import ExitConfig, check_exit_simple as _check_exit_simple_orig
import inspect

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

WARMUP_BARS = 55               # longest lookback (21) + feature window + buffer
HORIZON = 24                   # bars forward for ignition detection
IGNITION_THRESHOLD = 0.20      # 20% max forward high return = ignition event
POSITION_SIZE_USD = 1000.0
FEE_BPS = 4.0
BASE_SLIPPAGE_BPS = 5.0
MIN_BARS_PER_SYMBOL = 100
BTC_SYMBOL = "BTCUSDT"
MIN_SHORT_PRICE = 1.00

# Walk-forward config
N_FOLDS = 3
EMBARGO_DAYS = 2
HOLDOUT_DAYS = 18
MIN_TRADES_TOTAL = 25
MIN_FOLD_CONSISTENCY = 0.67    # 2/3 folds

# Screening thresholds
EFFECT_SIZE_THRESHOLD = 0.15   # rank-biserial correlation
LIFT_THRESHOLD = 2.5           # top/bottom quintile lift
MI_THRESHOLD = 0.005           # binned MI

# Robust daily PF
ROBUST_MIN_TRADES_PER_DAY = 3
ROBUST_PF_CAP = 5.0

# Feature generation
STREAMS = ['close_ret', 'volume', 'range_norm', 'body_frac', 'buy_pressure']
WINDOWS = [4, 8, 13, 21]
STATISTICS = ['mean', 'std', 'skew', 'kurtosis', 'norm_slope',
              'autocorr_1', 'acceleration', 'spikiness', 'percentile']

# Control group
CONTROLS_PER_EVENT = 8         # non-event bars sampled per event
CONTROL_MIN_SEP = 48           # minimum bar separation from any event

# Convergence
CONVERGENCE_MI_RATIO = 0.50    # stop when best new MI < this × median Round 1 MI

# ─── Module-level shared data (populated before fork, read-only in workers) ──

_shared_ohlcv_data = {}
_shared_symbols = []
_shared_close = None
_shared_high = None
_shared_low = None
_shared_open = None
_shared_volume = None
_shared_ts_grid = None


# ═══════════════════════════════════════════════════════════════════════════════
# BLAS THREAD MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _set_blas_threads(n):
    for k in _BLAS_KEYS:
        os.environ[k] = str(n)


def _pool_init():
    """Initializer for Pool workers: set BLAS to 1 thread."""
    for k in _BLAS_KEYS:
        os.environ[k] = '1'


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: DATA LOADING & MATRIX CONSTRUCTION (reused from signal_genesis)
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

    for s_idx in range(n_symbols):
        for arr in [close, open_, high, low, volume]:
            col = arr[:, s_idx]
            mask = np.isnan(col)
            if mask.any() and not mask.all():
                idx = np.where(~mask, np.arange(n_bars), 0)
                np.maximum.accumulate(idx, out=idx)
                arr[:, s_idx] = col[idx]

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


# ═══════════════════════════════════════════════════════════════════════════════
# ROLLING UTILITIES (reused from signal_genesis)
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


def _rolling_slope_2d(arr, w):
    from numpy.lib.stride_tricks import sliding_window_view
    n_bars = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n_bars < w:
        return out
    n = w
    i_vals = np.arange(n, dtype=np.float64)
    sum_i = i_vals.sum()
    sum_i2 = (i_vals**2).sum()
    denom = n * sum_i2 - sum_i**2
    if denom == 0:
        return out
    weights = np.arange(n, dtype=np.float64)
    windows = sliding_window_view(arr, w, axis=0)
    iy_sum = np.sum(windows * weights[np.newaxis, np.newaxis, :], axis=-1)
    y_sum = np.sum(windows, axis=-1)
    slopes = (n * iy_sum - sum_i * y_sum) / denom
    out[w-1:] = slopes
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
# EVENT CATALOG
# ═══════════════════════════════════════════════════════════════════════════════

def compute_max_forward_high(close, high, horizon=HORIZON):
    """Compute max(high[t+1:t+horizon+1]) / close[t] - 1 for each bar."""
    n_bars, n_symbols = close.shape
    mfh = np.full((n_bars, n_symbols), np.nan, dtype=np.float32)
    for h_offset in range(1, horizon + 1):
        if h_offset >= n_bars:
            break
        ratio = high[h_offset:] / close[:-h_offset]
        end = min(n_bars - h_offset, n_bars)
        current = mfh[:end]
        candidate = ratio[:end].astype(np.float32) - 1.0
        mfh[:end] = np.where(np.isnan(current), candidate,
                             np.where(np.isnan(candidate), current,
                                      np.maximum(current, candidate)))
    return mfh


def compute_mae_mfe(close, high, low, events, horizon=HORIZON):
    """Compute MAE (max adverse excursion) and MFE (max favorable excursion)
    for ignition events. Returns arrays of MAE and MFE values."""
    maes, mfes = [], []
    for t, s in events:
        c = close[t, s]
        if np.isnan(c) or c <= 0:
            continue
        end = min(t + horizon + 1, close.shape[0])
        if end <= t + 1:
            continue
        # MFE = max upside
        h_slice = high[t+1:end, s]
        mfe = (np.nanmax(h_slice) / c - 1.0) if len(h_slice) > 0 else 0.0
        # MAE = max downside
        l_slice = low[t+1:end, s]
        mae = (c - np.nanmin(l_slice)) / c if len(l_slice) > 0 else 0.0
        maes.append(mae)
        mfes.append(mfe)
    return np.array(maes, dtype=np.float64), np.array(mfes, dtype=np.float64)


def build_event_catalog(mfh, threshold=IGNITION_THRESHOLD):
    """Identify ignition events: bars where max forward high >= threshold.

    Returns: list of (bar_idx, symbol_idx) tuples, event_mask (n_bars, n_symbols) bool
    """
    event_mask = (~np.isnan(mfh)) & (mfh >= threshold)
    events = list(zip(*np.where(event_mask)))
    return events, event_mask


def build_control_group(event_mask, n_bars, n_symbols, events,
                        controls_per_event=CONTROLS_PER_EVENT,
                        min_sep=CONTROL_MIN_SEP, rng=None):
    """Build control group: non-event bars from same symbols and same times.

    For each event (t, s):
      - Sample from same symbol at different times (time-separated by >=min_sep from ANY event)
      - Sample from different symbols at same time
    """
    if rng is None:
        rng = np.random.RandomState(42)

    # Build per-symbol event bar sets for fast lookup
    sym_event_bars = defaultdict(set)
    for t, s in events:
        sym_event_bars[s].add(t)

    # All event bars per symbol (for exclusion zones)
    all_event_bars = set()
    for t, s in events:
        for dt in range(-min_sep, min_sep + 1):
            all_event_bars.add((t + dt, s))

    control_indices = []  # (bar_idx, symbol_idx)
    n_same_sym = controls_per_event // 2
    n_same_time = controls_per_event - n_same_sym

    for t, s in events:
        # Same symbol, different time
        candidates_same_sym = []
        for tt in range(WARMUP_BARS, n_bars - HORIZON):
            if (tt, s) not in all_event_bars and not event_mask[tt, s]:
                candidates_same_sym.append(tt)
        if candidates_same_sym:
            n_pick = min(n_same_sym, len(candidates_same_sym))
            picked = rng.choice(candidates_same_sym, size=n_pick, replace=False)
            for tt in picked:
                control_indices.append((tt, s))

        # Different symbol, same time
        candidates_same_time = []
        for ss in range(n_symbols):
            if ss != s and not event_mask[t, ss] and not np.isnan(_shared_close[t, ss] if _shared_close is not None else 0):
                candidates_same_time.append(ss)
        if candidates_same_time:
            n_pick = min(n_same_time, len(candidates_same_time))
            picked = rng.choice(candidates_same_time, size=n_pick, replace=False)
            for ss in picked:
                control_indices.append((t, ss))

    # Deduplicate
    control_set = set(control_indices)
    # Remove any that are actually events
    control_set -= set(events)
    return list(control_set)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: SYSTEMATIC FEATURE GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_stream(close, open_, high, low, volume, stream_name):
    """Compute one data stream from OHLCV. Returns (n_bars, n_symbols) array."""
    if stream_name == 'close_ret':
        ret = np.full_like(close, np.nan)
        ret[1:] = (close[1:] - close[:-1]) / np.where(close[:-1] != 0, close[:-1], np.nan)
        return ret
    elif stream_name == 'volume':
        return volume.copy()
    elif stream_name == 'range_norm':
        denom = np.where(close != 0, close, np.nan)
        return (high - low) / denom
    elif stream_name == 'body_frac':
        hl = high - low
        hl = np.where(hl > 0, hl, np.nan)
        return np.abs(close - open_) / hl
    elif stream_name == 'buy_pressure':
        hl = high - low
        hl = np.where(hl > 0, hl, np.nan)
        return (close - low) / hl
    else:
        raise ValueError(f"Unknown stream: {stream_name}")


def _compute_rolling_stats_for_stream(args):
    """Worker: compute all rolling statistics for one data stream.

    Args: (stream_name, stream_data, windows, n_bars, n_symbols)
    Returns: dict[feature_name] -> (n_bars, n_symbols) float32 array
    """
    stream_name, stream_data, windows = args
    features = {}

    for w in windows:
        prefix = f"{stream_name}_w{w}"

        # Mean
        features[f"{prefix}_mean"] = _rolling_mean_2d(stream_data, w).astype(np.float32)

        # Std
        features[f"{prefix}_std"] = _rolling_std_2d(stream_data, w).astype(np.float32)

        # Skewness: E[(x-mu)^3] / sigma^3
        mu = _rolling_mean_2d(stream_data, w)
        sigma = _rolling_std_2d(stream_data, w)
        sigma_safe = np.where(sigma > 1e-12, sigma, np.nan)
        centered = stream_data - mu
        m3 = _rolling_mean_2d(centered**3, w)
        skew = m3 / (sigma_safe**3)
        skew = np.clip(skew, -10, 10)
        features[f"{prefix}_skew"] = skew.astype(np.float32)

        # Kurtosis: E[(x-mu)^4] / sigma^4
        m4 = _rolling_mean_2d(centered**4, w)
        kurt = m4 / (sigma_safe**4)
        kurt = np.clip(kurt, 0, 100)
        features[f"{prefix}_kurtosis"] = kurt.astype(np.float32)

        # Normalized slope: OLS slope / mean
        slope = _rolling_slope_2d(stream_data, w)
        mu_safe = np.where(np.abs(mu) > 1e-12, mu, np.nan)
        norm_slope = slope / mu_safe
        norm_slope = np.clip(norm_slope, -10, 10)
        features[f"{prefix}_norm_slope"] = norm_slope.astype(np.float32)

        # Lag-1 autocorrelation
        if w >= 4:
            x_curr = stream_data[1:]
            x_prev = stream_data[:-1]
            ac = _rolling_corr_2d(x_curr, x_prev, w - 1)
            ac_full = np.full_like(stream_data, np.nan)
            ac_full[1:] = ac
            features[f"{prefix}_autocorr_1"] = ac_full.astype(np.float32)
        else:
            features[f"{prefix}_autocorr_1"] = np.full_like(stream_data, np.nan, dtype=np.float32)

        # Acceleration: last-half mean / first-half mean
        half = max(w // 2, 1)
        from numpy.lib.stride_tricks import sliding_window_view
        if stream_data.shape[0] >= w:
            wins = sliding_window_view(stream_data, w, axis=0)  # (n-w+1, n_sym, w)
            first_half = np.mean(wins[:, :, :half], axis=-1)
            second_half = np.mean(wins[:, :, half:], axis=-1)
            first_safe = np.where(np.abs(first_half) > 1e-12, first_half, np.nan)
            accel = second_half / first_safe
            accel = np.clip(accel, -10, 10)
            accel_full = np.full_like(stream_data, np.nan, dtype=np.float32)
            accel_full[w-1:] = accel.astype(np.float32)
            features[f"{prefix}_acceleration"] = accel_full
        else:
            features[f"{prefix}_acceleration"] = np.full_like(stream_data, np.nan, dtype=np.float32)

        # Spikiness: max / mean
        rmax = _rolling_max_2d(np.abs(stream_data), w)
        rmean = _rolling_mean_2d(np.abs(stream_data), w)
        rmean_safe = np.where(rmean > 1e-12, rmean, np.nan)
        spiky = rmax / rmean_safe
        spiky = np.clip(spiky, 0, 50)
        features[f"{prefix}_spikiness"] = spiky.astype(np.float32)

        # Percentile: current value's rank within window
        if stream_data.shape[0] >= w:
            wins = sliding_window_view(stream_data, w, axis=0)
            current_val = wins[:, :, -1:]  # (n-w+1, n_sym, 1)
            pctile = np.sum(wins <= current_val, axis=-1).astype(np.float32) / w
            pctile_full = np.full_like(stream_data, np.nan, dtype=np.float32)
            pctile_full[w-1:] = pctile
            features[f"{prefix}_percentile"] = pctile_full
        else:
            features[f"{prefix}_percentile"] = np.full_like(stream_data, np.nan, dtype=np.float32)

    return features


def _compute_cross_dimensional(streams_dict, windows=(8, 21)):
    """Compute cross-dimensional features between streams."""
    features = {}
    vol = streams_dict.get('volume')
    rng = streams_dict.get('range_norm')
    ret = streams_dict.get('close_ret')
    body = streams_dict.get('body_frac')

    if vol is None or rng is None or ret is None:
        return features

    for w in windows:
        prefix = f"cross_w{w}"
        features[f"{prefix}_vol_range_corr"] = _rolling_corr_2d(vol, rng, w).astype(np.float32)
        features[f"{prefix}_vol_ret_corr"] = _rolling_corr_2d(vol, ret, w).astype(np.float32)
        features[f"{prefix}_range_ret_corr"] = _rolling_corr_2d(rng, ret, w).astype(np.float32)

        if body is not None:
            absorption = vol * (1.0 - body)
            features[f"{prefix}_absorption"] = _rolling_mean_2d(absorption, w).astype(np.float32)

        vol_sign = np.sign(ret) * vol
        features[f"{prefix}_vol_sign_align"] = _rolling_mean_2d(vol_sign, w).astype(np.float32)

    return features


def _compute_absolute_state(close, open_, high, low, volume):
    """Compute absolute state features (not windowed)."""
    features = {}
    hl = high - low
    hl_safe = np.where(hl > 0, hl, np.nan)

    features["abs_body_ratio"] = (np.abs(close - open_) / hl_safe).astype(np.float32)
    features["abs_upper_wick"] = ((high - np.maximum(close, open_)) / hl_safe).astype(np.float32)
    features["abs_lower_wick"] = ((np.minimum(close, open_) - low) / hl_safe).astype(np.float32)

    # Price position in 21-bar channel
    ch_high = _rolling_max_2d(high, 21)
    ch_low = _rolling_min_2d(low, 21)
    ch_range = ch_high - ch_low
    ch_range_safe = np.where(ch_range > 0, ch_range, np.nan)
    features["abs_channel_position"] = ((close - ch_low) / ch_range_safe).astype(np.float32)

    # Volume z-score vs 21-bar history
    vol_mean = _rolling_mean_2d(volume, 21)
    vol_std = _rolling_std_2d(volume, 21)
    vol_std_safe = np.where(vol_std > 1e-12, vol_std, np.nan)
    features["abs_volume_zscore"] = ((volume - vol_mean) / vol_std_safe).astype(np.float32)

    return features


def generate_features_round1(close, open_, high, low, volume, parallel=True):
    """Generate Round 1 features: 5 streams × 4 windows × 9 stats + cross-dim + absolute.

    Uses Pool(5) to parallelize across data streams (unless parallel=False,
    for use inside Pool workers where nested Pools are forbidden).
    Returns: dict[feature_name] -> (n_bars, n_symbols) float32 array
    """
    t0 = time.time()
    logger.info("Phase 2: Generating Round 1 features...")

    # Compute raw streams
    streams_dict = {}
    for sname in STREAMS:
        streams_dict[sname] = _compute_stream(close, open_, high, low, volume, sname)

    pool_args = [(sname, streams_dict[sname], WINDOWS) for sname in STREAMS]

    all_features = {}
    if parallel:
        _set_blas_threads(1)
        with mp.Pool(5, initializer=_pool_init) as pool:
            results = pool.map(_compute_rolling_stats_for_stream, pool_args)
        _set_blas_threads(4)
    else:
        results = [_compute_rolling_stats_for_stream(a) for a in pool_args]

    for feat_dict in results:
        all_features.update(feat_dict)

    logger.info("  Stream features: %d", len(all_features))

    # Cross-dimensional features
    cross = _compute_cross_dimensional(streams_dict)
    all_features.update(cross)
    logger.info("  Cross-dimensional features: %d", len(cross))

    # Absolute state features
    absolute = _compute_absolute_state(close, open_, high, low, volume)
    all_features.update(absolute)
    logger.info("  Absolute state features: %d", len(absolute))

    # Clean: replace inf with nan
    for name, arr in all_features.items():
        mask = ~np.isfinite(arr)
        if mask.any():
            arr[mask] = np.nan

    elapsed = time.time() - t0
    logger.info("  Total Round 1 features: %d in %.1fs", len(all_features), elapsed)
    return all_features


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: FEATURE SCREENING — EVENT VS CONTROL
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_event_control_values(feature_arr, events, controls):
    """Extract feature values at event and control locations.
    Returns: event_vals (1d), control_vals (1d) — both filtered for non-NaN.
    """
    ev = np.array([feature_arr[t, s] for t, s in events], dtype=np.float64)
    cv = np.array([feature_arr[t, s] for t, s in controls], dtype=np.float64)
    ev_valid = ev[~np.isnan(ev)]
    cv_valid = cv[~np.isnan(cv)]
    return ev_valid, cv_valid


def _mann_whitney_u(x, y):
    """Mann-Whitney U test (numpy-only). Returns U statistic and rank-biserial r.

    Uses normal approximation for p-value.
    r = 1 - 2U/(n1*n2)  where r > 0 means x tends to be larger.
    """
    n1, n2 = len(x), len(y)
    if n1 < 5 or n2 < 5:
        return 0.0, 0.0, 1.0  # U, r, p

    combined = np.concatenate([x, y])
    ranks = np.argsort(np.argsort(combined)).astype(np.float64) + 1.0

    # Handle ties: average ranks for tied values
    sorted_vals = np.sort(combined)
    sorted_ranks = np.sort(ranks)
    # Simple tie-handling: use midrank for groups of equal values
    unique_vals, inverse, counts = np.unique(sorted_vals, return_inverse=True, return_counts=True)
    cumcounts = np.cumsum(counts)
    midranks = (cumcounts - counts + 1 + cumcounts) / 2.0
    tied_ranks = midranks[inverse]
    # Map back to original order
    order = np.argsort(combined)
    final_ranks = np.empty_like(tied_ranks)
    final_ranks[order] = tied_ranks

    R1 = np.sum(final_ranks[:n1])
    U1 = R1 - n1 * (n1 + 1) / 2

    # Rank-biserial correlation
    r = 1.0 - 2.0 * U1 / (n1 * n2)

    # Normal approximation for p-value
    mu_U = n1 * n2 / 2.0
    # Tie correction for variance
    N = n1 + n2
    tie_term = 0.0
    for c in counts:
        if c > 1:
            tie_term += c**3 - c
    sigma_U = math.sqrt(n1 * n2 / 12.0 * (N + 1 - tie_term / (N * (N - 1))))
    if sigma_U < 1e-12:
        return U1, r, 1.0

    z = (U1 - mu_U) / sigma_U
    # Two-tailed p from standard normal (numpy approximation)
    p = 2.0 * _norm_cdf(-abs(z))

    return U1, r, p


def _norm_cdf(x):
    """Standard normal CDF approximation (Abramowitz & Stegun 26.2.17)."""
    if x < -8:
        return 0.0
    if x > 8:
        return 1.0
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    t = 1.0 / (1.0 + p * abs(x))
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2.0)
    return 0.5 * (1.0 + sign * y)


def _compute_lift_by_quantile(event_vals, control_vals, n_bins=5):
    """Compute lift by quantile. Returns max lift across bins."""
    all_vals = np.concatenate([event_vals, control_vals])
    if len(all_vals) < n_bins * 2:
        return 0.0

    bin_edges = np.percentile(all_vals, np.linspace(0, 100, n_bins + 1))
    bin_edges[-1] += 1e-10  # ensure rightmost edge captures max

    n_events = len(event_vals)
    n_controls = len(control_vals)
    base_rate = n_events / (n_events + n_controls)
    if base_rate <= 0:
        return 0.0

    max_lift = 0.0
    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i == 0:
            in_bin_ev = np.sum(event_vals <= hi)
            in_bin_ct = np.sum(control_vals <= hi)
        else:
            in_bin_ev = np.sum((event_vals > lo) & (event_vals <= hi))
            in_bin_ct = np.sum((control_vals > lo) & (control_vals <= hi))

        total_in_bin = in_bin_ev + in_bin_ct
        if total_in_bin < 3:
            continue
        bin_rate = in_bin_ev / total_in_bin
        lift = bin_rate / base_rate
        max_lift = max(max_lift, lift)

    return max_lift


def _compute_binned_mi(event_vals, control_vals, n_bins=10):
    """Compute binned mutual information between feature and event/control label."""
    all_vals = np.concatenate([event_vals, control_vals])
    labels = np.concatenate([np.ones(len(event_vals)), np.zeros(len(control_vals))])
    N = len(all_vals)
    if N < 20:
        return 0.0

    bin_edges = np.percentile(all_vals, np.linspace(0, 100, n_bins + 1))
    bin_edges[-1] += 1e-10
    bin_indices = np.digitize(all_vals, bin_edges) - 1
    bin_indices = np.clip(bin_indices, 0, n_bins - 1)

    # Contingency table
    ct = np.zeros((n_bins, 2), dtype=np.float64)
    for i in range(N):
        ct[bin_indices[i], int(labels[i])] += 1

    # MI = sum p(x,y) * log(p(x,y) / (p(x)*p(y)))
    px = ct.sum(axis=1) / N
    py = ct.sum(axis=0) / N
    mi = 0.0
    for i in range(n_bins):
        for j in range(2):
            pxy = ct[i, j] / N
            if pxy > 0 and px[i] > 0 and py[j] > 0:
                mi += pxy * math.log(pxy / (px[i] * py[j]))
    return mi


def _screen_one_feature(args):
    """Worker: screen one feature against event/control groups.
    Returns: (feature_name, passed, effect_size, lift, mi, p_value) or None.
    """
    fname, event_vals, control_vals = args
    if len(event_vals) < 10 or len(control_vals) < 10:
        return (fname, False, 0.0, 0.0, 0.0, 1.0)

    _, r, p = _mann_whitney_u(event_vals, control_vals)
    lift = _compute_lift_by_quantile(event_vals, control_vals)
    mi = _compute_binned_mi(event_vals, control_vals)

    passed = (abs(r) > EFFECT_SIZE_THRESHOLD and
              (lift > LIFT_THRESHOLD or mi > MI_THRESHOLD))

    return (fname, passed, float(r), float(lift), float(mi), float(p))


def screen_features(features, events, controls, parallel=True):
    """Screen all features using event-vs-control tests.

    Uses Pool(8) for parallelism (unless parallel=False,
    for use inside Pool workers where nested Pools are forbidden).
    Returns: list of (name, effect_size, lift, mi, p) for features that pass.
    """
    t0 = time.time()
    logger.info("Phase 3: Screening %d features...", len(features))

    # Pre-extract values for all features (serial, I/O bound)
    screen_args = []
    for fname, farr in features.items():
        ev, cv = _extract_event_control_values(farr, events, controls)
        if len(ev) >= 10 and len(cv) >= 10:
            screen_args.append((fname, ev, cv))

    logger.info("  %d features have sufficient event/control values", len(screen_args))

    if parallel:
        _set_blas_threads(1)
        with mp.Pool(8, initializer=_pool_init) as pool:
            results = pool.map(_screen_one_feature, screen_args)
        _set_blas_threads(4)
    else:
        results = [_screen_one_feature(a) for a in screen_args]

    survivors = []
    for fname, passed, r, lift, mi, p in results:
        if passed:
            survivors.append((fname, r, lift, mi, p))

    survivors.sort(key=lambda x: -abs(x[3]))  # sort by MI descending

    elapsed = time.time() - t0
    logger.info("  Screening complete: %d/%d features passed in %.1fs",
                len(survivors), len(features), elapsed)

    if survivors:
        logger.info("  Top 10 by MI:")
        for fname, r, lift, mi, p in survivors[:10]:
            logger.info("    %-40s  r=%.3f  lift=%.2f  MI=%.4f  p=%.4f", fname, r, lift, mi, p)

    return survivors


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: ITERATIVE DISCOVERY LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def generate_round_n_features(surviving_features, features, close, open_, high, low, volume, round_num):
    """Generate new features based on what Round N-1 screening revealed.

    Analyzes which streams, windows, and statistics carried the most signal,
    then generates targeted features: finer windows, cross-stream interactions,
    lag sequences, etc.

    Returns: dict[feature_name] -> (n_bars, n_symbols) float32 array (new features only)
    """
    t0 = time.time()
    logger.info("  Round %d: Analyzing surviving feature structure...", round_num)

    # Parse surviving feature names to find dominant patterns
    stream_counts = defaultdict(int)
    window_counts = defaultdict(int)
    stat_counts = defaultdict(int)
    stream_mis = defaultdict(list)

    for fname, r, lift, mi, p in surviving_features:
        parts = fname.split('_')
        # Try to parse stream_wN_stat pattern
        for sname in STREAMS:
            if fname.startswith(sname + '_w'):
                stream_counts[sname] += 1
                stream_mis[sname].append(mi)
                # Extract window
                rest = fname[len(sname) + 2:]  # skip "stream_w"
                w_str = rest.split('_')[0]
                try:
                    w = int(w_str)
                    window_counts[w] += 1
                except ValueError:
                    pass
                # Extract stat (everything after wN_)
                stat_start = rest.find('_')
                if stat_start >= 0:
                    stat = rest[stat_start + 1:]
                    stat_counts[stat] += 1
                break

    logger.info("    Stream signal: %s", dict(stream_counts))
    logger.info("    Window signal: %s", dict(window_counts))
    logger.info("    Stat signal: %s", dict(stat_counts))

    new_features = {}

    # Strategy 1: Finer windows around dominant windows
    dominant_windows = sorted(window_counts.keys(), key=lambda w: -window_counts[w])[:2]
    new_windows = set()
    for w in dominant_windows:
        for offset in [-2, -1, 1, 2]:
            nw = w + offset
            if nw >= 3 and nw not in WINDOWS and nw not in new_windows:
                new_windows.add(nw)

    if new_windows:
        logger.info("    Adding finer windows: %s", sorted(new_windows))
        # Compute dominant streams at new windows
        dominant_streams = sorted(stream_counts.keys(), key=lambda s: -stream_counts[s])[:3]
        streams_dict = {}
        for sname in dominant_streams:
            streams_dict[sname] = _compute_stream(close, open_, high, low, volume, sname)

        for sname in dominant_streams:
            sdata = streams_dict[sname]
            for w in sorted(new_windows):
                args = (sname, sdata, [w])
                feat_dict = _compute_rolling_stats_for_stream(args)
                new_features.update(feat_dict)

    # Strategy 2: Interaction features between top surviving features
    top_pairs = []
    surv_names = [s[0] for s in surviving_features[:15]]
    for i in range(min(len(surv_names), 8)):
        for j in range(i + 1, min(len(surv_names), 8)):
            top_pairs.append((surv_names[i], surv_names[j]))

    for f1name, f2name in top_pairs[:10]:
        if f1name in features and f2name in features:
            f1 = features[f1name]
            f2 = features[f2name]
            # Product interaction
            prod = f1 * f2
            prod = np.clip(prod, -1e6, 1e6)
            new_features[f"interact_{f1name}_x_{f2name}"] = prod.astype(np.float32)

    # Strategy 3: Lagged versions of top features (1, 2, 4 bars back)
    for fname, r, lift, mi, p in surviving_features[:10]:
        if fname in features:
            farr = features[fname]
            for lag in [1, 2, 4]:
                lagged = np.full_like(farr, np.nan)
                lagged[lag:] = farr[:-lag]
                new_features[f"{fname}_lag{lag}"] = lagged.astype(np.float32)

    # Strategy 4: Diff of top features (current - lagged)
    for fname, r, lift, mi, p in surviving_features[:8]:
        if fname in features:
            farr = features[fname]
            for lag in [1, 4]:
                diff = np.full_like(farr, np.nan)
                diff[lag:] = farr[lag:] - farr[:-lag]
                new_features[f"{fname}_diff{lag}"] = diff.astype(np.float32)

    # Clean
    for name, arr in new_features.items():
        mask = ~np.isfinite(arr)
        if mask.any():
            arr[mask] = np.nan

    elapsed = time.time() - t0
    logger.info("    Round %d generated %d new features in %.1fs", round_num, len(new_features), elapsed)
    return new_features


def run_discovery_loop(features, events, controls, close, open_, high, low, volume, max_rounds=5):
    """Iterative discovery loop.

    Round 1: screen initial features.
    Round N (N>=2): generate new features from Round N-1 insights, screen, merge survivors.
    Stop when best new feature MI < 50% of median Round 1 MI, or max_rounds reached.

    Returns: all surviving features with screening results, all feature arrays
    """
    logger.info("Phase 4: Starting discovery loop (max %d rounds)...", max_rounds)

    # Round 1
    round1_survivors = screen_features(features, events, controls)
    if not round1_survivors:
        logger.info("  No features survived Round 1 screening. Stopping.")
        return [], features

    median_round1_mi = float(np.median([s[3] for s in round1_survivors]))
    logger.info("  Round 1: %d survivors, median MI = %.5f", len(round1_survivors), median_round1_mi)
    mi_threshold = median_round1_mi * CONVERGENCE_MI_RATIO

    all_survivors = list(round1_survivors)
    all_features = dict(features)

    for round_num in range(2, max_rounds + 1):
        logger.info("\n  ── Discovery Round %d ──", round_num)

        # Generate new features based on what survived
        new_feats = generate_round_n_features(
            all_survivors, all_features, close, open_, high, low, volume, round_num
        )

        if not new_feats:
            logger.info("  Round %d: no new features generated. Converged.", round_num)
            break

        # Screen new features
        new_survivors = screen_features(new_feats, events, controls)

        if not new_survivors:
            logger.info("  Round %d: no new features passed screening. Converged.", round_num)
            break

        best_new_mi = max(s[3] for s in new_survivors)
        logger.info("  Round %d: best new MI = %.5f (threshold = %.5f)",
                    round_num, best_new_mi, mi_threshold)

        if best_new_mi < mi_threshold:
            logger.info("  Round %d: best MI below threshold. Converged.", round_num)
            # Still add any that passed screening
            all_survivors.extend(new_survivors)
            all_features.update(new_feats)
            break

        all_survivors.extend(new_survivors)
        all_features.update(new_feats)
        logger.info("  Round %d: %d total survivors after merge", round_num, len(all_survivors))

    # Deduplicate by feature name (keep highest MI)
    best_by_name = {}
    for s in all_survivors:
        fname = s[0]
        if fname not in best_by_name or s[3] > best_by_name[fname][3]:
            best_by_name[fname] = s
    final_survivors = sorted(best_by_name.values(), key=lambda x: -abs(x[3]))

    logger.info("  Discovery complete: %d unique surviving features", len(final_survivors))
    return final_survivors, all_features


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: CONDITIONAL PROBABILITY MODEL
# ═══════════════════════════════════════════════════════════════════════════════

def build_conditional_model(surviving_features, all_features, events, controls, n_bins=5):
    """Build binned conditional probability model.

    For each surviving feature:
      1. Bin into n_bins quantiles (edges from training data)
      2. Compute lift[feature][bin] = P(ignition | feature in bin) / P(ignition)

    Also discover interaction terms (joint lift exceeds product of marginals).

    Returns: model dict with bin_edges, lifts, interactions, threshold
    """
    t0 = time.time()
    logger.info("Phase 5: Building conditional probability model...")

    n_events = len(events)
    n_controls = len(controls)
    base_rate = n_events / (n_events + n_controls)

    model = {
        "features": [],
        "bin_edges": {},
        "log_lifts": {},
        "interactions": [],
        "threshold": 0.0,
        "base_rate": base_rate,
    }

    # Use top features (capped at 30 to avoid overfitting)
    top_features = surviving_features[:30]

    for fname, r, lift_val, mi, p in top_features:
        if fname not in all_features:
            continue
        farr = all_features[fname]

        ev, cv = _extract_event_control_values(farr, events, controls)
        all_vals = np.concatenate([ev, cv])
        labels = np.concatenate([np.ones(len(ev)), np.zeros(len(cv))])

        if len(all_vals) < n_bins * 5:
            continue

        # Compute bin edges from all training data
        edges = np.percentile(all_vals, np.linspace(0, 100, n_bins + 1))
        edges[0] -= 1e-10
        edges[-1] += 1e-10

        bin_idx = np.digitize(all_vals, edges) - 1
        bin_idx = np.clip(bin_idx, 0, n_bins - 1)

        log_lifts = np.zeros(n_bins, dtype=np.float64)
        for b in range(n_bins):
            in_bin = bin_idx == b
            if in_bin.sum() < 3:
                log_lifts[b] = 0.0
                continue
            bin_rate = labels[in_bin].mean()
            lift_b = bin_rate / base_rate if base_rate > 0 else 0.0
            lift_b = max(lift_b, 0.01)  # floor to avoid log(0)
            lift_b = min(lift_b, 20.0)  # cap extreme lifts
            log_lifts[b] = math.log(lift_b)

        model["features"].append(fname)
        model["bin_edges"][fname] = edges.tolist()
        model["log_lifts"][fname] = log_lifts.tolist()

    logger.info("  Model has %d features", len(model["features"]))

    # Interaction discovery: top feature pairs
    feature_names = model["features"][:15]
    for i in range(len(feature_names)):
        for j in range(i + 1, len(feature_names)):
            f1, f2 = feature_names[i], feature_names[j]
            if f1 not in all_features or f2 not in all_features:
                continue

            ev1, cv1 = _extract_event_control_values(all_features[f1], events, controls)
            ev2, cv2 = _extract_event_control_values(all_features[f2], events, controls)

            all1 = np.concatenate([ev1, cv1])
            all2 = np.concatenate([ev2, cv2])
            labels = np.concatenate([np.ones(len(ev1)), np.zeros(len(cv1))])

            if len(all1) != len(all2) or len(all1) < 50:
                continue

            # Joint 3×3 bins for efficiency
            jn = 3
            edges1 = np.percentile(all1, np.linspace(0, 100, jn + 1))
            edges2 = np.percentile(all2, np.linspace(0, 100, jn + 1))
            edges1[0] -= 1e-10; edges1[-1] += 1e-10
            edges2[0] -= 1e-10; edges2[-1] += 1e-10

            b1 = np.clip(np.digitize(all1, edges1) - 1, 0, jn - 1)
            b2 = np.clip(np.digitize(all2, edges2) - 1, 0, jn - 1)

            # Marginal lifts
            marginal1 = np.zeros(jn)
            marginal2 = np.zeros(jn)
            for b in range(jn):
                m = b1 == b
                if m.sum() > 0:
                    marginal1[b] = labels[m].mean() / base_rate if base_rate > 0 else 0
                m = b2 == b
                if m.sum() > 0:
                    marginal2[b] = labels[m].mean() / base_rate if base_rate > 0 else 0

            # Joint lifts and synergy check
            max_synergy = 0.0
            best_joint = None
            for b_i in range(jn):
                for b_j in range(jn):
                    mask = (b1 == b_i) & (b2 == b_j)
                    if mask.sum() < 5:
                        continue
                    joint_rate = labels[mask].mean()
                    joint_lift = joint_rate / base_rate if base_rate > 0 else 0
                    expected_lift = marginal1[b_i] * marginal2[b_j]
                    if expected_lift > 0:
                        synergy = joint_lift / expected_lift
                        if synergy > max_synergy:
                            max_synergy = synergy
                            best_joint = (b_i, b_j, joint_lift, expected_lift, synergy)

            if max_synergy > 2.0 and best_joint is not None:
                model["interactions"].append({
                    "f1": f1, "f2": f2,
                    "edges1": edges1.tolist(), "edges2": edges2.tolist(),
                    "best_bins": (best_joint[0], best_joint[1]),
                    "joint_lift": best_joint[2],
                    "expected_lift": best_joint[3],
                    "synergy": best_joint[4],
                })

    logger.info("  Interactions found: %d", len(model["interactions"]))

    # Threshold tuning: sweep on training data, maximize expected value
    # Score every event and control bar, find optimal threshold
    all_scores = []
    all_labels = []
    all_points = list(events) + list(controls)
    point_labels = [1] * len(events) + [0] * len(controls)

    for idx, (t, s) in enumerate(all_points):
        score = 0.0
        valid = True
        for fname in model["features"]:
            if fname not in all_features:
                valid = False
                break
            val = all_features[fname][t, s]
            if np.isnan(val):
                valid = False
                break
            edges = model["bin_edges"][fname]
            b = np.searchsorted(edges, val) - 1
            b = max(0, min(b, n_bins - 1))
            score += model["log_lifts"][fname][b]
        if valid:
            all_scores.append(score)
            all_labels.append(point_labels[idx])

    all_scores = np.array(all_scores)
    all_labels = np.array(all_labels)

    if len(all_scores) < 20:
        logger.warning("  Too few scored points for threshold tuning")
        model["threshold"] = 0.0
        elapsed = time.time() - t0
        logger.info("  Model built in %.1fs", elapsed)
        return model

    # Sweep thresholds
    percentiles = np.percentile(all_scores, np.arange(50, 99, 2))
    best_ev = -1e9
    best_thresh = float(np.median(all_scores))

    for thresh in percentiles:
        fired = all_scores >= thresh
        if fired.sum() < 5:
            continue
        precision = all_labels[fired].mean()
        n_signals = fired.sum()

        # Expected value: precision * avg_gain - (1-precision) * avg_loss
        # Approximate: precision > 0.5 is good for ignition events
        ev = precision * n_signals  # simple: maximize precision × volume
        if ev > best_ev:
            best_ev = ev
            best_thresh = float(thresh)

    model["threshold"] = best_thresh
    fired = all_scores >= best_thresh
    if fired.sum() > 0:
        precision = all_labels[fired].mean()
        logger.info("  Threshold=%.3f: %d signals, precision=%.3f (training)",
                    best_thresh, fired.sum(), precision)

    elapsed = time.time() - t0
    logger.info("  Model built in %.1fs", elapsed)
    return model


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: SIGNAL GENERATION (Absolute, Not Ranked)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Signal:
    symbol: str
    direction: str  # always "long"
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


def score_bar(model, all_features, t, s, n_bins=5):
    """Score a single (bar, symbol) slot. Returns log-score or NaN."""
    score = 0.0
    for fname in model["features"]:
        if fname not in all_features:
            return np.nan
        val = all_features[fname][t, s]
        if np.isnan(val):
            return np.nan
        edges = model["bin_edges"][fname]
        b = np.searchsorted(edges, val) - 1
        b = max(0, min(b, n_bins - 1))
        score += model["log_lifts"][fname][b]

    # Add interaction bonuses
    for inter in model["interactions"]:
        f1, f2 = inter["f1"], inter["f2"]
        if f1 not in all_features or f2 not in all_features:
            continue
        v1 = all_features[f1][t, s]
        v2 = all_features[f2][t, s]
        if np.isnan(v1) or np.isnan(v2):
            continue
        b1 = np.clip(np.searchsorted(inter["edges1"], v1) - 1, 0, 2)
        b2 = np.clip(np.searchsorted(inter["edges2"], v2) - 1, 0, 2)
        if b1 == inter["best_bins"][0] and b2 == inter["best_bins"][1]:
            score += math.log(max(inter["synergy"], 1.01))

    return score


def generate_signals(model, all_features, bar_mask, symbols):
    """Generate signals: for each bar in mask, for each symbol independently,
    fire if score > threshold. At some bars 0 signals, at others 1-3.
    """
    threshold = model["threshold"]
    bar_indices = np.where(bar_mask)[0]
    signals = []

    for t in bar_indices:
        for s_idx, sym in enumerate(symbols):
            sc = score_bar(model, all_features, t, s_idx)
            if not np.isnan(sc) and sc >= threshold:
                signals.append(Signal(symbol=sym, direction="long", ts_idx=t, score=sc))

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 7: TRADE SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

def _dynamic_slippage(volume_24h_usd):
    """Volume-tiered slippage: thin books get higher slippage."""
    if volume_24h_usd < 100_000:
        return 50.0   # 50 bps
    elif volume_24h_usd < 500_000:
        return 30.0
    elif volume_24h_usd < 2_000_000:
        return 15.0
    elif volume_24h_usd < 10_000_000:
        return 8.0
    else:
        return 5.0


def simulate_trades(signals, ohlcv_data, symbols, exit_config_dict, slippage_mult=1.0):
    """Simulate signals to trades. Long-only, dynamic slippage, position dedup.

    exit_config_dict: {stop_atr_mult, time_stop_bars, trail_activation_r, trail_distance_r, trail_breakeven_r}
    """
    stop_atr_mult = exit_config_dict["stop_atr_mult"]
    time_stop_bars = exit_config_dict["time_stop_bars"]
    ec = ExitConfig()
    for key in ("trail_breakeven_r", "trail_activation_r", "trail_distance_r"):
        if hasattr(ec, key) and key in exit_config_dict:
            setattr(ec, key, exit_config_dict[key])

    # Dedup: only one position per symbol at a time
    open_positions = {}  # symbol -> exit_bar_idx
    results = []

    # Sort signals by bar index for chronological processing
    sorted_signals = sorted(signals, key=lambda s: (s.ts_idx, -s.score))

    for sig in sorted_signals:
        key = (sig.symbol, '1h')
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        highs = data["high"]
        lows = data["low"]
        opens = data["open"]
        volumes = data["volume"]
        n = len(closes)

        i = sig.ts_idx
        if i + 1 >= n or i < 14:
            continue

        # Dedup check
        if sig.symbol in open_positions and open_positions[sig.symbol] > i:
            continue

        # ATR at signal bar
        atr = _rolling_atr_1d(highs[:i+1], lows[:i+1], closes[:i+1], 14)[-1]
        if atr <= 0:
            continue

        # Dynamic slippage based on recent volume
        vol_window = min(24, i)
        avg_vol = np.nanmean(volumes[max(0, i - vol_window):i+1])
        avg_price = closes[i]
        vol_usd = avg_vol * avg_price if not np.isnan(avg_vol) else 0
        slip_bps = _dynamic_slippage(vol_usd) * slippage_mult

        # Entry at next bar open + slippage (long only)
        entry_raw = opens[i + 1]
        entry = entry_raw * (1 + slip_bps / 10000)
        stop = entry - stop_atr_mult * atr

        if stop <= 0 or entry <= 0:
            continue
        r_value = abs(entry - stop)
        if r_value <= 0 or r_value / entry > 0.15:
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
                side="long", bar=bar,
                entry_price=entry, stop_price=stop,
                tp1_price=None, tp2_price=None,
                r_value=r_value, bars_held=bars_held,
                max_bars=time_stop_bars, high_water_mark=hwm,
                half_r_touched=half_r_touched, config=ec,
            )
            hwm = new_hwm
            half_r_touched = new_half_r
            if result:
                exit_reason, exit_price = result
                break

        # Track position end for dedup
        exit_bar = i + 1 + bars_held
        open_positions[sig.symbol] = exit_bar

        # PnL with exit slippage
        if exit_reason != "STOP_LOSS":
            ep = exit_price * (1 - slip_bps / 10000)
        else:
            ep = exit_price

        pos_size = POSITION_SIZE_USD / entry
        pnl_gross = (ep - entry) * pos_size
        fees = (entry + ep) * pos_size * FEE_BPS / 10000
        pnl_net = pnl_gross - fees
        r_mult = pnl_net / (r_value * pos_size) if r_value * pos_size > 0 else 0

        results.append(TradeResult(
            symbol=sig.symbol, direction="long",
            ts_idx=sig.ts_idx, entry_price=entry, exit_price=ep,
            pnl=pnl_net, r_mult=r_mult, exit_reason=exit_reason, bars_held=bars_held,
        ))

    return results


def compute_robust_daily_pf(trades, ts_grid, min_trades=ROBUST_MIN_TRADES_PER_DAY, pf_cap=ROBUST_PF_CAP):
    """Robust daily PF: mean of daily PF (capped) for days with >= min_trades trades."""
    if not trades:
        return 0.0

    daily = defaultdict(lambda: {"wins": 0.0, "losses": 0.0, "count": 0})
    for t in trades:
        ts = ts_grid[t.ts_idx]
        day = int(ts // 86400)
        daily[day]["count"] += 1
        if t.pnl > 0:
            daily[day]["wins"] += t.pnl
        elif t.pnl < 0:
            daily[day]["losses"] += abs(t.pnl)

    pfs = []
    for day_data in daily.values():
        if day_data["count"] < min_trades:
            continue
        w = day_data["wins"]
        l = day_data["losses"]
        if l > 0:
            pf = min(w / l, pf_cap)
        elif w > 0:
            pf = pf_cap
        else:
            continue
        pfs.append(pf)

    if not pfs:
        return 0.0
    return float(np.mean(pfs))


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 8: WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_folds(ts_grid, n_folds=N_FOLDS, holdout_days=HOLDOUT_DAYS, embargo_days=EMBARGO_DAYS):
    """Build expanding window folds. Returns: list of (train_mask, test_mask), holdout_mask"""
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

        if train_mask.sum() < 100 or test_mask.sum() < 50:
            continue

        folds.append((train_mask, test_mask))
        logger.info("    Fold %d: train [0, %d) = %d bars, test [%d, %d) = %d bars",
                    fold_idx, train_end_day, train_mask.sum(),
                    test_start_day, test_end_day, test_mask.sum())

    holdout_mask = np.zeros(len(ts_grid), dtype=bool)
    for i, ts in enumerate(ts_grid):
        day = int(ts // 86400 - day_start)
        if day >= holdout_start_day:
            holdout_mask[i] = True

    logger.info("    Holdout: days [%d, %d) = %d bars",
                holdout_start_day, total_days, holdout_mask.sum())

    return folds, holdout_mask


def _derive_exit_configs_from_mae_mfe(maes, mfes):
    """Derive data-driven exit configs from MAE/MFE distributions of ignition events."""
    configs = {}

    # Preset configs (starting vocabulary)
    configs["ultra_wide"] = {
        "stop_atr_mult": 4.0, "time_stop_bars": 36,
        "trail_activation_r": 999.0, "trail_distance_r": 999.0, "trail_breakeven_r": 0.0,
    }
    configs["wide_trail"] = {
        "stop_atr_mult": 3.0, "time_stop_bars": 36,
        "trail_activation_r": 2.0, "trail_distance_r": 1.0, "trail_breakeven_r": 0.0,
    }
    configs["no_atr_stop"] = {
        "stop_atr_mult": 0.0, "time_stop_bars": 36,
        "trail_activation_r": 999.0, "trail_distance_r": 999.0, "trail_breakeven_r": 0.0,
    }
    configs["standard"] = {
        "stop_atr_mult": 2.0, "time_stop_bars": 36,
        "trail_activation_r": 1.5, "trail_distance_r": 1.0, "trail_breakeven_r": 0.0,
    }

    if len(maes) < 10 or len(mfes) < 10:
        return configs

    # Data-driven configs from MAE/MFE distributions
    mae_p50 = np.percentile(maes, 50)
    mae_p75 = np.percentile(maes, 75)
    mfe_p25 = np.percentile(mfes, 25)
    mfe_p50 = np.percentile(mfes, 50)

    logger.info("  MAE distribution: p25=%.3f p50=%.3f p75=%.3f",
                np.percentile(maes, 25), mae_p50, mae_p75)
    logger.info("  MFE distribution: p25=%.3f p50=%.3f p75=%.3f",
                mfe_p25, mfe_p50, np.percentile(mfes, 75))

    # Config based on median MAE as stop distance
    # Trail activation at MFE p25 (many trades reach this)
    configs["mae_fitted"] = {
        "stop_atr_mult": 2.5,  # will be overridden by r_value logic in sim
        "time_stop_bars": 36,
        "trail_activation_r": max(1.0, mfe_p25 / max(mae_p50, 0.01)),
        "trail_distance_r": max(0.5, mae_p50 / max(mae_p75, 0.01)),
        "trail_breakeven_r": 0.0,
    }

    # Tighter config: stop at MAE p75, trail early
    configs["tight_fitted"] = {
        "stop_atr_mult": 1.5,
        "time_stop_bars": 24,
        "trail_activation_r": max(0.75, mfe_p25 / max(mae_p75, 0.01) * 0.5),
        "trail_distance_r": 0.5,
        "trail_breakeven_r": max(0.3, mfe_p25 / max(mae_p75, 0.01) * 0.3),
    }

    return configs


def _process_fold(args):
    """Worker for parallel walk-forward. One fold × one exit config.

    Reads from module-level shared data.
    Returns dict with fold results.
    """
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    fold_idx, exit_name, exit_config, train_mask, test_mask = args

    close = _shared_close
    high_ = _shared_high
    low_ = _shared_low
    open_ = _shared_open
    volume_ = _shared_volume
    ts_grid = _shared_ts_grid
    symbols = _shared_symbols
    ohlcv_data = _shared_ohlcv_data

    t0 = time.time()

    # === Training phase ===
    # 1. Compute max forward high on training data
    mfh_train = compute_max_forward_high(close, high_, HORIZON)

    # 2. Build event catalog on training bars
    train_indices = np.where(train_mask)[0]
    train_mfh = np.full_like(mfh_train, np.nan)
    train_mfh[train_mask] = mfh_train[train_mask]
    events_train, event_mask_train = build_event_catalog(train_mfh, IGNITION_THRESHOLD)

    if len(events_train) < 10:
        return {
            "fold_idx": fold_idx, "exit_name": exit_name,
            "n_events": len(events_train), "n_trades": 0,
            "robust_daily_pf": 0.0, "aggregate_pf": 0.0,
            "win_rate": 0.0, "total_pnl": 0.0, "precision": 0.0,
            "elapsed": time.time() - t0,
        }

    # 3. Build control group on training bars
    controls_train = build_control_group(
        event_mask_train, close.shape[0], close.shape[1], events_train,
        controls_per_event=CONTROLS_PER_EVENT, min_sep=CONTROL_MIN_SEP,
    )

    # 4. Generate features (Round 1 only in fold — full discovery already done)
    # parallel=False: we're inside a Pool worker, can't nest Pools
    features = generate_features_round1(close, open_, high_, low_, volume_, parallel=False)

    # 5. Screen features (serial for same reason)
    survivors = screen_features(features, events_train, controls_train, parallel=False)

    if not survivors:
        return {
            "fold_idx": fold_idx, "exit_name": exit_name,
            "n_events": len(events_train), "n_trades": 0,
            "robust_daily_pf": 0.0, "aggregate_pf": 0.0,
            "win_rate": 0.0, "total_pnl": 0.0, "precision": 0.0,
            "elapsed": time.time() - t0,
        }

    # 6. Build model on training data
    model = build_conditional_model(survivors, features, events_train, controls_train)

    # === Test phase ===
    # 7. Generate signals on test bars
    test_signals = generate_signals(model, features, test_mask, symbols)

    if not test_signals:
        return {
            "fold_idx": fold_idx, "exit_name": exit_name,
            "n_events": len(events_train), "n_trades": 0,
            "n_signals": 0, "robust_daily_pf": 0.0, "aggregate_pf": 0.0,
            "win_rate": 0.0, "total_pnl": 0.0, "precision": 0.0,
            "n_features": len(model["features"]),
            "elapsed": time.time() - t0,
        }

    # 8. Simulate trades
    trades = simulate_trades(test_signals, ohlcv_data, symbols, exit_config)

    # 9. Compute metrics
    robust_pf = compute_robust_daily_pf(trades, ts_grid)
    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades) if trades else 0.0
    gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
    agg_pf = gross_wins / gross_losses if gross_losses > 0 else (ROBUST_PF_CAP if gross_wins > 0 else 0.0)

    # Check how many signals actually hit ignition (precision proxy)
    mfh_test = mfh_train  # already computed for full range
    ignition_hits = 0
    for sig in test_signals:
        s_idx = symbols.index(sig.symbol) if sig.symbol in symbols else -1
        if s_idx >= 0 and sig.ts_idx < mfh_test.shape[0]:
            mfh_val = mfh_test[sig.ts_idx, s_idx]
            if not np.isnan(mfh_val) and mfh_val >= IGNITION_THRESHOLD:
                ignition_hits += 1
    precision = ignition_hits / len(test_signals) if test_signals else 0.0

    elapsed = time.time() - t0
    logger.info("  Fold %d [%s]: %d signals → %d trades, robust PF=%.3f, agg PF=%.3f, "
                "WR=%.1f%%, precision=%.3f, PnL=$%.2f (%.1fs)",
                fold_idx, exit_name, len(test_signals), len(trades),
                robust_pf, agg_pf, win_rate * 100, precision, total_pnl, elapsed)

    return {
        "fold_idx": fold_idx, "exit_name": exit_name,
        "n_events": len(events_train), "n_trades": len(trades),
        "n_signals": len(test_signals), "robust_daily_pf": robust_pf,
        "aggregate_pf": agg_pf, "win_rate": win_rate, "total_pnl": total_pnl,
        "precision": precision, "n_features": len(model["features"]),
        "exit_reasons": dict(defaultdict(int, {t.exit_reason: 1 for t in trades})),
        "elapsed": elapsed,
    }


def run_walk_forward(folds, exit_configs, ts_grid, jsonl_path):
    """Run walk-forward validation across folds × exit configs.

    Uses Pool(12) for parallelism.
    Returns: dict with results per exit config.
    """
    t0 = time.time()
    logger.info("Phase 8: Walk-forward validation (%d folds × %d exit configs = %d combos)...",
                len(folds), len(exit_configs), len(folds) * len(exit_configs))

    # Build work units, sorted heaviest-first (later folds have more training data)
    work_units = []
    for fold_idx in range(len(folds) - 1, -1, -1):
        train_mask, test_mask = folds[fold_idx]
        for exit_name, exit_cfg in exit_configs.items():
            work_units.append((fold_idx, exit_name, exit_cfg, train_mask, test_mask))

    logger.info("  Dispatching %d work units to Pool(12)...", len(work_units))

    _set_blas_threads(1)
    with mp.Pool(min(12, len(work_units)), initializer=_pool_init) as pool:
        results = pool.map(_process_fold, work_units)
    _set_blas_threads(4)

    # Aggregate by exit config
    by_exit = defaultdict(list)
    for r in results:
        by_exit[r["exit_name"]].append(r)

    summary = {}
    best_exit = None
    best_robust_pf = -1

    for exit_name, fold_results in by_exit.items():
        robust_pfs = [r["robust_daily_pf"] for r in fold_results]
        avg_robust = float(np.mean(robust_pfs)) if robust_pfs else 0.0
        consistency = sum(1 for pf in robust_pfs if pf > 1.0) / len(robust_pfs) if robust_pfs else 0.0
        total_trades = sum(r["n_trades"] for r in fold_results)
        total_pnl = sum(r["total_pnl"] for r in fold_results)
        avg_precision = float(np.mean([r.get("precision", 0) for r in fold_results]))

        summary[exit_name] = {
            "avg_robust_pf": avg_robust,
            "consistency": consistency,
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "avg_precision": avg_precision,
            "folds": fold_results,
        }

        logger.info("  Exit '%s': avg robust PF=%.3f, consistency=%.2f, trades=%d, precision=%.3f",
                    exit_name, avg_robust, consistency, total_trades, avg_precision)

        if avg_robust > best_robust_pf:
            best_robust_pf = avg_robust
            best_exit = exit_name

        # Write incremental
        with open(jsonl_path, "a") as f:
            f.write(json.dumps({
                "type": "walk_forward_exit",
                "exit_name": exit_name,
                "avg_robust_pf": avg_robust,
                "consistency": consistency,
                "total_trades": total_trades,
                "total_pnl": total_pnl,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }) + "\n")

    elapsed = time.time() - t0
    logger.info("  Walk-forward complete in %.1fs. Best exit: '%s' (robust PF=%.3f)",
                elapsed, best_exit, best_robust_pf)

    return summary, best_exit


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 9: HOLDOUT + DSR
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


def run_holdout(close, open_, high_, low_, volume_, ts_grid, symbols, ohlcv_data,
                holdout_mask, folds, best_exit_config, n_trials, slippage_mult=1.5):
    """Run holdout evaluation with the best exit config at 1.5× slippage."""
    t0 = time.time()
    logger.info("Phase 9: Holdout evaluation (%.1f× slippage)...", slippage_mult)

    # Full training = everything before holdout
    full_train_mask = np.zeros(len(ts_grid), dtype=bool)
    holdout_start = np.where(holdout_mask)[0]
    if len(holdout_start) == 0:
        logger.warning("  No holdout bars")
        return None
    full_train_mask[:holdout_start[0]] = True

    # 1. Event catalog on full training
    mfh = compute_max_forward_high(close, high_, HORIZON)
    train_mfh = np.full_like(mfh, np.nan)
    train_mfh[full_train_mask] = mfh[full_train_mask]
    events, event_mask = build_event_catalog(train_mfh, IGNITION_THRESHOLD)
    logger.info("  Full training: %d ignition events", len(events))

    if len(events) < 10:
        logger.warning("  Too few events for holdout")
        return None

    # 2. Control group
    controls = build_control_group(event_mask, close.shape[0], close.shape[1], events)

    # 3. Features
    features = generate_features_round1(close, open_, high_, low_, volume_)

    # 4. Full discovery loop on training
    survivors, all_features = run_discovery_loop(
        features, events, controls, close, open_, high_, low_, volume_
    )
    if not survivors:
        logger.warning("  No features survived in holdout training")
        return None

    # 5. Build model
    model = build_conditional_model(survivors, all_features, events, controls)

    # 6. Generate signals on holdout
    holdout_signals = generate_signals(model, all_features, holdout_mask, symbols)
    logger.info("  Holdout signals: %d", len(holdout_signals))

    if not holdout_signals:
        return {"n_trades": 0, "robust_daily_pf": 0.0, "aggregate_pf": 0.0,
                "dsr": -99.0, "features": model["features"]}

    # 7. Simulate at 1.5× slippage
    trades = simulate_trades(holdout_signals, ohlcv_data, symbols,
                             best_exit_config, slippage_mult=slippage_mult)

    if not trades:
        return {"n_trades": 0, "robust_daily_pf": 0.0, "aggregate_pf": 0.0,
                "dsr": -99.0, "features": model["features"]}

    robust_pf = compute_robust_daily_pf(trades, ts_grid)
    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades)
    gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
    agg_pf = gross_wins / gross_losses if gross_losses > 0 else (ROBUST_PF_CAP if gross_wins > 0 else 0.0)

    # DSR
    pnl_arr = np.array([t.pnl for t in trades])
    n_obs = len(pnl_arr)
    mean_pnl = np.mean(pnl_arr)
    std_pnl = np.std(pnl_arr, ddof=1) if n_obs > 1 else 1e-10
    sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

    if n_obs > 2 and std_pnl > 0:
        z_pnl = (pnl_arr - mean_pnl) / std_pnl
        skew = float(np.mean(z_pnl**3) * (n_obs * (n_obs - 1))**0.5 / (n_obs - 2))
        kurt = float(np.mean(z_pnl**4))
    else:
        skew, kurt = 0.0, 3.0

    dsr = deflated_sharpe_ratio(sharpe, n_trials, n_obs, skew, kurt)

    logger.info("  Holdout: %d trades, robust PF=%.3f, agg PF=%.3f, WR=%.1f%%, "
                "Sharpe=%.3f, DSR=%.3f, PnL=$%.2f",
                len(trades), robust_pf, agg_pf, win_rate * 100, sharpe, dsr, total_pnl)

    return {
        "n_trades": len(trades),
        "robust_daily_pf": robust_pf,
        "aggregate_pf": agg_pf,
        "dsr": dsr,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "features": model["features"],
        "n_interactions": len(model["interactions"]),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 10: REPORT + MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global IGNITION_THRESHOLD

    parser = argparse.ArgumentParser(description="U1 Ignition Discovery")
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--quick", action="store_true", help="Quick mode: 2 folds, fewer features")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    parser.add_argument("--threshold", type=float, default=IGNITION_THRESHOLD,
                        help="Ignition threshold (default 0.20 = 20%%)")
    args = parser.parse_args()

    t_start = time.time()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    IGNITION_THRESHOLD = args.threshold
    n_folds = 2 if args.quick else N_FOLDS

    # Output directory
    output_dir = os.path.join(os.path.dirname(__file__), "signal_genesis_ignition")
    os.makedirs(output_dir, exist_ok=True)
    jsonl_path = os.path.join(output_dir, "signal_genesis_ignition_incremental.jsonl")
    results_path = os.path.join(output_dir, "signal_genesis_ignition_results.json")

    logger.info("=" * 70)
    logger.info("U1 IGNITION DISCOVERY — Event-Based Pre-Ignition Detector")
    logger.info("=" * 70)
    logger.info("Config: days=%d, threshold=%.0f%%, folds=%d, holdout=%d days, quick=%s",
                args.days, IGNITION_THRESHOLD * 100, n_folds, args.holdout_days, args.quick)

    # ── Phase 1: Data Loading ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 1: Data Loading")
    logger.info("─" * 50)

    close, open_, high, low, volume, ts_grid, symbols, btc_idx, ohlcv_data = \
        load_ohlcv_matrices(database_url, args.days)

    # Populate shared globals for Pool workers
    global _shared_close, _shared_high, _shared_low, _shared_open, _shared_volume
    global _shared_ts_grid, _shared_symbols, _shared_ohlcv_data
    _shared_close = close
    _shared_high = high
    _shared_low = low
    _shared_open = open_
    _shared_volume = volume
    _shared_ts_grid = ts_grid
    _shared_symbols = symbols
    _shared_ohlcv_data = ohlcv_data

    # ── Event Catalog ──
    logger.info("\n" + "─" * 50)
    logger.info("EVENT CATALOG")
    logger.info("─" * 50)

    mfh = compute_max_forward_high(close, high, HORIZON)
    events_all, event_mask_all = build_event_catalog(mfh, IGNITION_THRESHOLD)

    n_bars, n_symbols = close.shape
    total_slots = np.sum(~np.isnan(mfh))
    event_rate = len(events_all) / total_slots if total_slots > 0 else 0

    logger.info("  Ignition events (>= %.0f%% in %d bars): %d / %d slots (%.4f%%)",
                IGNITION_THRESHOLD * 100, HORIZON, len(events_all), total_slots, event_rate * 100)

    # Catalog at multiple thresholds
    for thresh in [0.10, 0.20, 0.30, 0.50]:
        _, em = build_event_catalog(mfh, thresh)
        cnt = em.sum()
        logger.info("    >= %3.0f%%: %d events (%.4f%%)", thresh * 100, cnt, cnt / total_slots * 100 if total_slots > 0 else 0)

    # Symbol distribution
    events_per_sym = defaultdict(int)
    for t, s in events_all:
        events_per_sym[symbols[s]] += 1
    top_event_syms = sorted(events_per_sym.items(), key=lambda x: -x[1])[:10]
    logger.info("  Top event symbols: %s", [(s, c) for s, c in top_event_syms])

    # MAE/MFE
    maes, mfes = compute_mae_mfe(close, high, low, events_all)
    logger.info("  MAE: mean=%.3f, p50=%.3f, p75=%.3f", np.mean(maes), np.median(maes), np.percentile(maes, 75))
    logger.info("  MFE: mean=%.3f, p50=%.3f, p75=%.3f", np.mean(mfes), np.median(mfes), np.percentile(mfes, 75))

    # Write catalog to JSONL
    with open(jsonl_path, "w") as f:
        f.write(json.dumps({
            "type": "event_catalog",
            "threshold": IGNITION_THRESHOLD,
            "n_events": len(events_all),
            "event_rate": event_rate,
            "mae_mean": float(np.mean(maes)),
            "mae_p50": float(np.median(maes)),
            "mfe_mean": float(np.mean(mfes)),
            "mfe_p50": float(np.median(mfes)),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }) + "\n")

    if len(events_all) < 20:
        logger.error("  Too few ignition events (%d). Cannot proceed.", len(events_all))
        sys.exit(1)

    # ── Phase 2-4: Feature Generation & Discovery ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASES 2-4: Feature Generation & Discovery Loop")
    logger.info("─" * 50)

    # Build control group using all events (for initial discovery)
    controls = build_control_group(event_mask_all, n_bars, n_symbols, events_all)
    logger.info("  Control group: %d bars (%d events × ~%d controls/event)",
                len(controls), len(events_all), CONTROLS_PER_EVENT)

    # Generate Round 1 features
    features = generate_features_round1(close, open_, high, low, volume)

    # Run discovery loop
    survivors, all_features = run_discovery_loop(
        features, events_all, controls, close, open_, high, low, volume,
        max_rounds=2 if args.quick else 5
    )

    if not survivors:
        logger.error("  No features survived discovery. Writing empty results.")
        report = {
            "run_timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.time() - t_start,
            "verdict": "FAIL_NO_FEATURES",
            "n_events": len(events_all),
        }
        with open(results_path, "w") as f:
            json.dump(report, f, indent=2)
        return

    with open(jsonl_path, "a") as f:
        f.write(json.dumps({
            "type": "discovery_complete",
            "n_survivors": len(survivors),
            "top_features": [(s[0], s[3]) for s in survivors[:20]],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, default=str) + "\n")

    # ── Phase 5: Build model ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 5: Conditional Probability Model")
    logger.info("─" * 50)

    model = build_conditional_model(survivors, all_features, events_all, controls)

    # ── Phases 7-8: Walk-Forward ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASES 7-8: Walk-Forward Validation")
    logger.info("─" * 50)

    folds, holdout_mask = build_folds(ts_grid, n_folds=n_folds, holdout_days=args.holdout_days)
    if not folds:
        logger.error("No folds could be constructed. Need more data.")
        sys.exit(1)

    # Derive exit configs from MAE/MFE
    exit_configs = _derive_exit_configs_from_mae_mfe(maes, mfes)
    logger.info("  Exit configs: %s", list(exit_configs.keys()))

    wf_summary, best_exit = run_walk_forward(folds, exit_configs, ts_grid, jsonl_path)

    # ── Phase 9: Holdout ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 9: Holdout + DSR")
    logger.info("─" * 50)

    # Check walk-forward pass
    wf_pass = False
    if best_exit and best_exit in wf_summary:
        ws = wf_summary[best_exit]
        wf_pass = (ws["avg_robust_pf"] > 1.0 and
                   ws["consistency"] >= MIN_FOLD_CONSISTENCY and
                   ws["total_trades"] >= MIN_TRADES_TOTAL)

    holdout_result = None
    n_trials = len(survivors) * len(exit_configs)

    if wf_pass:
        holdout_result = run_holdout(
            close, open_, high, low, volume, ts_grid, symbols, ohlcv_data,
            holdout_mask, folds, exit_configs[best_exit], n_trials,
        )
    else:
        if best_exit and best_exit in wf_summary:
            ws = wf_summary[best_exit]
            logger.info("  Walk-forward FAILED (robust PF=%.3f, consistency=%.2f, trades=%d). Skipping holdout.",
                        ws["avg_robust_pf"], ws["consistency"], ws["total_trades"])
        else:
            logger.info("  Walk-forward produced no results. Skipping holdout.")

    # ── Phase 10: Report ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 10: Final Report")
    logger.info("─" * 50)

    elapsed_total = time.time() - t_start

    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed_total,
        "model": "ignition_event_discovery",
        "hypothesis": "Event-based pre-ignition detection using raw statistical fingerprints",
        "threshold": IGNITION_THRESHOLD,
        "horizon": HORIZON,
        "event_catalog": {
            "n_events": len(events_all),
            "event_rate": event_rate,
            "mae": {"mean": float(np.mean(maes)), "p50": float(np.median(maes)), "p75": float(np.percentile(maes, 75))},
            "mfe": {"mean": float(np.mean(mfes)), "p50": float(np.median(mfes)), "p75": float(np.percentile(mfes, 75))},
            "top_symbols": top_event_syms[:10],
        },
        "discovery": {
            "n_round1_features": len(features),
            "n_total_features": len(all_features),
            "n_survivors": len(survivors),
            "top_features": [(s[0], {"r": s[1], "lift": s[2], "mi": s[3]}) for s in survivors[:30]],
        },
        "model_summary": {
            "n_features": len(model["features"]),
            "n_interactions": len(model["interactions"]),
            "threshold": model["threshold"],
        },
        "exit_configs_tested": list(exit_configs.keys()),
        "walk_forward": {},
        "holdout": {},
        "verdict": "PENDING",
        "n_trials_for_dsr": n_trials,
    }

    # Walk-forward results
    for exit_name, ws in wf_summary.items():
        report["walk_forward"][exit_name] = {
            "avg_robust_pf": ws["avg_robust_pf"],
            "consistency": ws["consistency"],
            "total_trades": ws["total_trades"],
            "total_pnl": ws["total_pnl"],
            "avg_precision": ws["avg_precision"],
        }

    report["walk_forward"]["best_exit"] = best_exit

    # Verdict
    fail_reasons = []
    if not wf_pass:
        report["verdict"] = "FAIL_WALK_FORWARD"
        if best_exit and best_exit in wf_summary:
            ws = wf_summary[best_exit]
            if ws["avg_robust_pf"] <= 1.0:
                fail_reasons.append(f"robust_pf={ws['avg_robust_pf']:.3f} <= 1.0")
            if ws["consistency"] < MIN_FOLD_CONSISTENCY:
                fail_reasons.append(f"consistency={ws['consistency']:.2f} < {MIN_FOLD_CONSISTENCY}")
            if ws["total_trades"] < MIN_TRADES_TOTAL:
                fail_reasons.append(f"trades={ws['total_trades']} < {MIN_TRADES_TOTAL}")
        else:
            fail_reasons.append("no walk-forward results")
    elif holdout_result is None or holdout_result["n_trades"] == 0:
        report["verdict"] = "FAIL_HOLDOUT"
        fail_reasons.append("holdout produced 0 trades")
    elif holdout_result["robust_daily_pf"] <= 1.0:
        report["verdict"] = "FAIL_HOLDOUT"
        fail_reasons.append(f"holdout robust PF={holdout_result['robust_daily_pf']:.3f} <= 1.0")
    elif holdout_result["dsr"] <= 0:
        report["verdict"] = "FAIL_DSR"
        fail_reasons.append(f"DSR={holdout_result['dsr']:.3f} <= 0")
    else:
        report["verdict"] = "CONFIRMED"

    report["fail_reasons"] = fail_reasons

    if holdout_result:
        report["holdout"] = {k: v for k, v in holdout_result.items() if k != "trades"}

    # Write results
    with open(results_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Results written to %s", results_path)

    # Summary
    logger.info("\n" + "═" * 70)
    logger.info("SUMMARY")
    logger.info("═" * 70)
    logger.info("Elapsed: %.1f min", elapsed_total / 60)
    logger.info("Events: %d (%.4f%% of bars)", len(events_all), event_rate * 100)
    logger.info("Features discovered: %d → %d survivors", len(all_features), len(survivors))
    logger.info("Verdict: %s", report["verdict"])
    if fail_reasons:
        logger.info("Fail reasons: %s", "; ".join(fail_reasons))
    if best_exit and best_exit in wf_summary:
        ws = wf_summary[best_exit]
        logger.info("Best exit config: '%s' (robust PF=%.3f, consistency=%.2f)",
                    best_exit, ws["avg_robust_pf"], ws["consistency"])
    if holdout_result and holdout_result["n_trades"] > 0:
        logger.info("Holdout: robust PF=%.3f, DSR=%.3f, %d trades, PnL=$%.2f",
                    holdout_result["robust_daily_pf"], holdout_result["dsr"],
                    holdout_result["n_trades"], holdout_result["total_pnl"])

    logger.info("═" * 70)
    logger.info("Done.")


if __name__ == "__main__":
    main()
