#!/usr/bin/env python3
"""
Signal Genesis Time-Series — Per-Symbol Adaptive Momentum/Reversion (h24)
==========================================================================

Hypothesis: Time-series momentum and mean-reversion are mathematically
independent of cross-sectional momentum (Moskowitz, Ooi, Pedersen 2012).
A symbol's own autocorrelation structure determines whether to trend-follow
or mean-revert for THAT SPECIFIC SYMBOL at THAT SPECIFIC TIME.

Previous experiments using cross-sectional features all failed — the dominant
finding was negative cross-sectional IC (momentum/volatility features predict
negative forward returns), translating to a short-vol/mean-reversion bet that
loses money after transaction costs + stop-loss drag.

This script tests a fundamentally different approach:
- Rolling autocorrelation of 1-bar returns at lags 1, 4, 8, 12, 24 (55-bar window)
- Own-momentum: rolling Sharpe of returns over 8, 13, 21 bars
- Own mean-reversion: distance from own 21-bar rolling mean, normalized by own std
- Trend quality: efficiency ratio (net move / sum |moves|) over 13 bars
- Adaptive signal: weighted sum driven by autocorrelation regime

Key difference: NO cross-sectional comparison. Each symbol uses only its own
history to determine signal direction and strength.

Usage:
    tmux new -s timeseries
    docker compose run --rm -T worker python3 /scripts/signal_genesis_timeseries.py --days 110
"""

# --- Standard library --------------------------------------------------------

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

# --- Env + path setup --------------------------------------------------------

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

# --- BLAS thread control ------------------------------------------------------
os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')
os.environ.setdefault('MKL_NUM_THREADS', '4')

# --- Third-party --------------------------------------------------------------

import numpy as np
import psycopg
from exit_checker import ExitConfig, check_exit_simple as _check_exit_simple_orig
import inspect

_exit_simple_params = inspect.signature(_check_exit_simple_orig).parameters

def check_exit_simple(**kwargs):
    if 'config' in kwargs and 'config' not in _exit_simple_params:
        kwargs.pop('config')
    return _check_exit_simple_orig(**kwargs)

# --- Logging ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# --- Constants ----------------------------------------------------------------

WARMUP_BARS = 100
POSITION_SIZE_USD = 1000.0
FEE_BPS = 4.0
MIN_SHORT_PRICE = 1.00
BTC_SYMBOL = "BTCUSDT"
BASE_SLIPPAGE_1H = 5.0
MIN_BARS_PER_SYMBOL = 100

HORIZON = 24

# Exit config A: Standard (ATR stop + trail + time stop)
EXIT_CONFIG_A = {
    "name": "standard",
    "stop_atr_mult": 2.0,
    "time_stop_bars": 36,
    "trail_activation_r": 1.5,
    "trail_distance_r": 1.0,
    "trail_breakeven_r": 0.0,
    "use_atr_stop": True,
}

# Exit config B: Time-stop only (no ATR stop, no trail)
EXIT_CONFIG_B = {
    "name": "time_stop_only",
    "stop_atr_mult": 0.0,       # Not used
    "time_stop_bars": 36,
    "trail_activation_r": 999.0, # Effectively disabled
    "trail_distance_r": 999.0,   # Effectively disabled
    "trail_breakeven_r": 0.0,
    "use_atr_stop": False,
}

# Autocorrelation config
AC_LAGS = [1, 4, 8, 12, 24]
AC_WINDOW = 55

# Own-momentum Sharpe windows
SHARPE_WINDOWS = [8, 13, 21]

# Mean-reversion config
MR_WINDOW = 21

# Efficiency ratio window
ER_WINDOW = 13

# Threshold sweep values (in sigma units)
THRESHOLD_SWEEP = [0.5, 1.0, 1.5, 2.0]

# Walk-forward config
N_FOLDS = 3
EMBARGO_DAYS = 1
HOLDOUT_DAYS = 18
MIN_TRADES_TOTAL = 25
MIN_FOLD_CONSISTENCY = 2 / 3  # 2 out of 3

# Robust PF config
MIN_TRADES_PER_DAY_ROBUST = 3


# ==============================================================================
# DATA STRUCTURES
# ==============================================================================

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
    exit_config_name: str
    threshold: float
    n_trades: int
    avg_daily_pf: float
    avg_daily_pf_robust: float
    median_daily_pf: float
    aggregate_pf: float
    win_rate: float
    total_pnl: float
    avg_r_mult: float
    exit_reasons: dict


# ==============================================================================
# ROLLING HELPERS (reused from sparse, unchanged)
# ==============================================================================

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


# ==============================================================================
# NEW: TIME-SERIES FEATURE COMPUTATION
# ==============================================================================

def compute_autocorrelation_2d(returns, lag, window):
    """
    Rolling autocorrelation of returns at a given lag over a rolling window.

    For each bar t and symbol s, computes the Pearson correlation between
    returns[t-window+1:t+1] and returns[t-window+1-lag:t+1-lag].

    Shape: (n_bars, n_symbols), NaN where insufficient data.
    """
    n_bars, n_symbols = returns.shape
    out = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)

    if n_bars < window + lag:
        return out

    # x = returns shifted by lag (lagged returns)
    # y = current returns
    # We need both x and y to be aligned, so we compute correlation between
    # returns[t] and returns[t-lag] over a rolling window.

    # Build lagged returns array
    lagged = np.full_like(returns, np.nan)
    lagged[lag:] = returns[:-lag]

    # Now compute rolling correlation between returns and lagged returns
    # Using the same rolling correlation formula as _rolling_corr_2d
    # but applied to (returns, lagged)
    x = returns
    y = lagged

    # Both x and y need to be valid (non-NaN) for meaningful correlation
    # Replace NaN with 0 for cumsum, then mask the output
    x_clean = np.where(np.isnan(x) | np.isnan(y), 0.0, x)
    y_clean = np.where(np.isnan(x) | np.isnan(y), 0.0, y)
    valid = (~np.isnan(x) & ~np.isnan(y)).astype(np.float64)

    sx = _rolling_sum_2d(x_clean, window)
    sy = _rolling_sum_2d(y_clean, window)
    sx2 = _rolling_sum_2d(x_clean**2, window)
    sy2 = _rolling_sum_2d(y_clean**2, window)
    sxy = _rolling_sum_2d(x_clean * y_clean, window)
    n_valid = _rolling_sum_2d(valid, window)

    # Only compute where we have enough valid points
    enough = n_valid >= (window * 0.7)
    numer = n_valid * sxy - sx * sy
    dx = n_valid * sx2 - sx**2
    dy = n_valid * sy2 - sy**2
    denom = np.sqrt(np.maximum(dx, 0) * np.maximum(dy, 0))

    mask = enough & (denom > 0)
    out[mask] = numer[mask] / denom[mask]

    # NaN out initial warmup
    min_start = window + lag - 1
    out[:min_start] = np.nan

    return out


def compute_own_sharpe_2d(returns, window):
    """
    Rolling Sharpe ratio per symbol: mean_ret / std_ret over window bars.

    Shape: (n_bars, n_symbols), NaN where insufficient data.
    """
    mean_ret = _rolling_mean_2d(returns, window)
    std_ret = _rolling_std_2d(returns, window)
    safe_std = np.where(std_ret > 0, std_ret, 1e-10)
    sharpe = mean_ret / safe_std
    sharpe[:window-1] = np.nan
    return sharpe


def compute_mean_reversion_score_2d(close, window):
    """
    Distance from own rolling mean, normalized by own rolling std.

    For each bar t, symbol s:
        mr_score = (close[t] - rolling_mean[t]) / rolling_std[t]

    Positive = above mean (overbought), Negative = below mean (oversold).
    The mean-reversion signal is -mr_score (fade the deviation).

    Shape: (n_bars, n_symbols), NaN where insufficient data.
    """
    rolling_mean = _rolling_mean_2d(close, window)
    rolling_std = _rolling_std_2d(close, window)
    safe_std = np.where(rolling_std > 0, rolling_std, 1e-10)
    mr_score = (close - rolling_mean) / safe_std
    mr_score[:window-1] = np.nan
    return mr_score


def compute_efficiency_ratio_2d(close, window):
    """
    Efficiency ratio: |net move| / sum(|bar moves|) over window bars.

    ER near 1.0 = strong trend (price moved mostly in one direction)
    ER near 0.0 = choppy/noisy (lots of back-and-forth)

    Shape: (n_bars, n_symbols), NaN where insufficient data.
    """
    n_bars, n_symbols = close.shape
    out = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)

    if n_bars < window + 1:
        return out

    # Net move over window
    net_move = np.abs(close[window:] - close[:-window])

    # Sum of absolute 1-bar moves over window
    abs_bar_moves = np.abs(np.diff(close, axis=0))  # shape: (n_bars-1, n_symbols)
    # Rolling sum of abs_bar_moves over window bars
    sum_abs_moves = _rolling_sum_2d(abs_bar_moves, window)
    # sum_abs_moves has valid values starting at index window-1 in abs_bar_moves space
    # which is index window in close space
    sum_abs_valid = sum_abs_moves[window-1:]  # shape: (n_bars - window, n_symbols)

    safe_sum = np.where(sum_abs_valid > 0, sum_abs_valid, 1e-10)
    er = net_move / safe_sum

    out[window:] = er
    return out


def compute_adaptive_signal(close, returns, ac_lags, ac_window, sharpe_windows,
                            mr_window, er_window):
    """
    Compute the per-symbol adaptive signal.

    For each symbol at each bar:
    1. Compute autocorrelation at multiple lags
    2. If ac_lag1 > 0: weight toward own-momentum (trend-follow)
    3. If ac_lag1 < 0: weight toward mean-reversion (fade)
    4. Trend quality (efficiency ratio) as confidence multiplier

    Returns: (n_bars, n_symbols) adaptive signal array
    """
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    n_bars, n_symbols = close.shape
    t0 = time.time()

    logger.info("  Computing time-series features...")

    # --- Autocorrelations ---
    ac_dict = {}
    for lag in ac_lags:
        ac = compute_autocorrelation_2d(returns, lag, ac_window)
        ac_dict[lag] = ac
        valid_count = np.sum(np.isfinite(ac))
        logger.info("    AC lag=%d: %d valid values, mean=%.4f",
                     lag, valid_count, np.nanmean(ac))

    # --- Own-momentum (rolling Sharpe) ---
    sharpe_dict = {}
    for w in sharpe_windows:
        sh = compute_own_sharpe_2d(returns, w)
        sharpe_dict[w] = sh
        logger.info("    Own-Sharpe w=%d: mean=%.4f, std=%.4f",
                     w, np.nanmean(sh), np.nanstd(sh))

    # --- Mean-reversion score ---
    mr_score = compute_mean_reversion_score_2d(close, mr_window)
    logger.info("    Mean-reversion score: mean=%.4f, std=%.4f",
                 np.nanmean(mr_score), np.nanstd(mr_score))

    # --- Efficiency ratio (trend quality) ---
    er = compute_efficiency_ratio_2d(close, er_window)
    logger.info("    Efficiency ratio: mean=%.4f, std=%.4f",
                 np.nanmean(er), np.nanstd(er))

    # --- Build adaptive signal ---
    # The adaptive signal blends momentum and mean-reversion based on
    # the autocorrelation regime of each symbol at each point in time.
    #
    # Core logic:
    #   ac1 = autocorrelation at lag 1
    #   If ac1 > 0: returns are positively autocorrelated -> trend-follow
    #     signal += weight * own_momentum_composite
    #   If ac1 < 0: returns are negatively autocorrelated -> mean-revert
    #     signal += weight * (-mean_reversion_score)  [fade the deviation]
    #
    #   Confidence multiplier: efficiency ratio (higher = stronger trend)
    #   Additional regime info from higher lags

    ac1 = ac_dict[1]

    # Composite own-momentum: average of Sharpe ratios across windows
    # (different windows capture different momentum time-scales)
    momentum_composite = np.zeros((n_bars, n_symbols), dtype=np.float64)
    n_sharpe = 0
    for w in sharpe_windows:
        sh = sharpe_dict[w]
        valid = np.isfinite(sh)
        momentum_composite = np.where(valid, momentum_composite + sh, momentum_composite)
        n_sharpe += valid.astype(np.float64)
    safe_n = np.where(n_sharpe > 0, n_sharpe, 1.0)
    momentum_composite /= safe_n

    # Mean-reversion signal: fade the deviation (negative of mr_score)
    # If price is above mean (mr_score > 0), expect reversion down -> short
    # If price is below mean (mr_score < 0), expect reversion up -> long
    mr_signal = -mr_score

    # Regime-adaptive blending
    # ac1 > 0: momentum regime -> use momentum_composite
    # ac1 < 0: mean-reversion regime -> use mr_signal
    # ac1 near 0: mixed -> small signal (low confidence)

    # Smooth blending using ac1 as the mixing weight
    # signal = ac1 * momentum_composite + (1 - |ac1|) * 0 + (-ac1) * mr_signal
    # Simplified: signal = ac1 * momentum_composite - ac1 * mr_signal ... no
    #
    # Correct logic:
    # When ac1 > 0 (trending): want momentum_composite direction
    # When ac1 < 0 (reverting): want mean-reversion direction
    # These are NOT opposites — momentum says "continuation", MR says "fade"
    #
    # Blending:
    #   momentum_weight = max(ac1, 0)
    #   mr_weight = max(-ac1, 0)
    #   raw_signal = momentum_weight * momentum_composite + mr_weight * mr_signal

    momentum_weight = np.maximum(ac1, 0)  # positive when ac1 > 0
    mr_weight = np.maximum(-ac1, 0)       # positive when ac1 < 0

    raw_signal = momentum_weight * momentum_composite + mr_weight * mr_signal

    # Use higher-lag autocorrelations as additional confidence
    # If ac at lag 4 and lag 8 agree with lag 1 in sign, boost confidence
    ac4 = ac_dict[4]
    ac8 = ac_dict[8]

    # Agreement factor: how many of lag1, lag4, lag8 agree in sign
    sign1 = np.sign(ac1)
    sign4 = np.sign(ac4)
    sign8 = np.sign(ac8)
    # If all agree, factor = 1.0; if only lag1, factor = 0.33
    agreement = (np.abs(sign1 + sign4 + sign8)) / 3.0  # range [0.33, 1.0]
    agreement = np.where(np.isfinite(agreement), agreement, 0.5)

    # Efficiency ratio as trend quality multiplier
    # Higher ER = price is moving directionally = signal more trustworthy
    er_safe = np.where(np.isfinite(er), er, 0.0)

    # Final adaptive signal
    # = raw_signal * agreement * (0.5 + 0.5 * er)
    # The (0.5 + 0.5*er) term ensures even in choppy markets we don't zero out completely
    confidence = agreement * (0.5 + 0.5 * er_safe)
    adaptive_signal = raw_signal * confidence

    # Clean up
    adaptive_signal[~np.isfinite(adaptive_signal)] = np.nan

    elapsed = time.time() - t0
    valid_count = np.sum(np.isfinite(adaptive_signal))
    logger.info("  Adaptive signal computed in %.1fs: %d valid values", elapsed, valid_count)
    logger.info("    Signal stats: mean=%.4f, std=%.4f, min=%.4f, max=%.4f",
                 np.nanmean(adaptive_signal), np.nanstd(adaptive_signal),
                 np.nanmin(adaptive_signal), np.nanmax(adaptive_signal))

    # Diagnostic: what fraction of bars are in momentum vs MR regime
    n_total = np.sum(np.isfinite(ac1))
    n_momentum = np.sum(ac1 > 0)
    n_mr = np.sum(ac1 < 0)
    if n_total > 0:
        logger.info("    Regime split: %.1f%% momentum, %.1f%% mean-reversion",
                     100.0 * n_momentum / n_total, 100.0 * n_mr / n_total)

    return adaptive_signal


# ==============================================================================
# SIGNAL GENERATION
# ==============================================================================

def generate_ts_signals(adaptive_signal, threshold, symbols, bar_mask):
    """
    Generate LONG/SHORT signals from the adaptive signal.

    Per-symbol, per-bar:
    - If adaptive_signal > threshold -> LONG
    - If adaptive_signal < -threshold -> SHORT
    - threshold is in units of the signal's own cross-sectional std at that bar

    Using per-bar z-scoring so that "threshold=1.0" means ">1 sigma above
    the cross-bar mean of that symbol's signal".

    Actually, since each symbol's signal is already its own, we use the
    signal's own rolling std to normalize. This avoids cross-sectional
    comparisons entirely.
    """
    n_bars, n_symbols = adaptive_signal.shape
    signals = []

    bar_indices = np.where(bar_mask)[0]

    # Pre-compute per-symbol rolling mean and std of adaptive signal for z-scoring
    # Use a 55-bar rolling window to match the autocorrelation computation window
    zscore_window = AC_WINDOW
    sig_mean = _rolling_mean_2d(adaptive_signal, zscore_window)
    sig_std = _rolling_std_2d(adaptive_signal, zscore_window)
    safe_std = np.where(sig_std > 0, sig_std, 1e-10)
    sig_z = (adaptive_signal - sig_mean) / safe_std

    for t in bar_indices:
        for s_idx in range(n_symbols):
            z = sig_z[t, s_idx]
            if not np.isfinite(z):
                continue

            if z > threshold:
                score = z - threshold
                signals.append(Signal(symbols[s_idx], "long", t, float(score)))
            elif z < -threshold:
                score = abs(z) - threshold
                signals.append(Signal(symbols[s_idx], "short", t, float(score)))

    return signals


# ==============================================================================
# THRESHOLD OPTIMIZATION
# ==============================================================================

def optimize_threshold(adaptive_signal, fwd_ret, train_mask, symbols, ohlcv_data,
                       exit_cfg_dict, slippage_bps):
    """
    Sweep threshold values on an 80/20 mini-split within training data.
    Returns the best threshold.
    """
    # Split training bars into 80% mini-train and 20% mini-val
    train_indices = np.where(train_mask)[0]
    n_train = len(train_indices)
    split_idx = int(n_train * 0.8)

    mini_train_mask = np.zeros(len(train_mask), dtype=bool)
    mini_val_mask = np.zeros(len(train_mask), dtype=bool)
    mini_train_mask[train_indices[:split_idx]] = True
    mini_val_mask[train_indices[split_idx:]] = True

    # Add embargo between mini-train and mini-val
    if split_idx < n_train:
        embargo_bars = 24  # 1 day of hourly bars
        embargo_end = min(split_idx + embargo_bars, n_train)
        mini_val_mask[train_indices[split_idx:embargo_end]] = False

    exit_cfg = _make_exit_config(exit_cfg_dict)
    use_atr_stop = exit_cfg_dict.get("use_atr_stop", True)

    best_threshold = 1.0
    best_metric = -999.0

    logger.info("      Threshold sweep on mini-val (%d bars):", mini_val_mask.sum())

    for threshold in THRESHOLD_SWEEP:
        sigs = generate_ts_signals(adaptive_signal, threshold, symbols, mini_val_mask)
        if not sigs:
            logger.info("        threshold=%.1f: 0 signals", threshold)
            continue

        trades = simulate_trades(sigs, ohlcv_data, slippage_bps, exit_cfg,
                                 use_atr_stop=use_atr_stop,
                                 time_stop_bars=exit_cfg_dict["time_stop_bars"],
                                 stop_atr_mult=exit_cfg_dict.get("stop_atr_mult", 2.0))
        if not trades:
            logger.info("        threshold=%.1f: 0 trades", threshold)
            continue

        robust_pf = compute_avg_daily_pf_robust(trades, ohlcv_data, symbols)
        agg_pf = compute_aggregate_pf(trades)
        n_trades = len(trades)

        # Use robust avg daily PF as optimization target
        # But require minimum trades to avoid overfitting to tiny sample
        if n_trades < 10:
            metric = -1.0
        else:
            metric = robust_pf

        logger.info("        threshold=%.1f: %d trades, robust_pf=%.3f, agg_pf=%.3f",
                     threshold, n_trades, robust_pf, agg_pf)

        if metric > best_metric:
            best_metric = metric
            best_threshold = threshold

    logger.info("      Best threshold: %.1f (metric=%.3f)", best_threshold, best_metric)
    return best_threshold


# ==============================================================================
# TRADE SIMULATION (fixed: slippage on ALL exits)
# ==============================================================================

def _make_exit_config(cfg):
    ec = ExitConfig()
    for key in ("trail_breakeven_r", "trail_activation_r", "trail_distance_r"):
        if hasattr(ec, key):
            setattr(ec, key, cfg[key])
    return ec


def simulate_trades(signals, ohlcv_data, slippage_bps, exit_config=None,
                    use_atr_stop=True, time_stop_bars=36, stop_atr_mult=2.0):
    """
    Simulate signals to trades. Slippage applied to ALL exits (including stop-loss).

    If use_atr_stop=False, the stop is placed extremely wide (effectively disabled),
    relying on time_stop and trail to exit.
    """
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

        atr = _rolling_atr_1d(highs[:i+1], lows[:i+1], closes[:i+1], 14)[-1]
        if atr <= 0:
            continue

        entry_raw = opens[i + 1]
        if sig.direction == "long":
            entry = entry_raw * (1 + slippage_bps / 10000)
            if use_atr_stop:
                stop = entry - stop_atr_mult * atr
            else:
                # Place stop extremely wide (effectively no stop)
                stop = entry * 0.50  # 50% below entry — will almost never hit
        else:
            entry = entry_raw * (1 - slippage_bps / 10000)
            if use_atr_stop:
                stop = entry + stop_atr_mult * atr
            else:
                stop = entry * 1.50  # 50% above entry
            if entry < MIN_SHORT_PRICE:
                continue

        if stop <= 0 or entry <= 0:
            continue
        r_value = abs(entry - stop)
        if r_value <= 0:
            continue

        # For ATR-based stops, filter out unreasonable risk
        if use_atr_stop:
            if r_value / entry > 0.10:
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
            pnl=pnl_net, r_mult=r_mult, exit_reason=exit_reason, bars_held=bars_held,
        ))

    return results


# ==============================================================================
# METRICS (original + robust + median)
# ==============================================================================

def compute_avg_daily_pf(trades, ohlcv_data, symbols):
    """Compute average daily profit factor (original, potentially inflated)."""
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


def compute_avg_daily_pf_robust(trades, ohlcv_data, symbols, min_trades_per_day=MIN_TRADES_PER_DAY_ROBUST):
    """
    Compute average daily profit factor with robustness fix.

    Only includes days with >= min_trades_per_day trades in the average.
    This prevents the false positive from winning-only days capped at PF=10.
    """
    if not trades:
        return 0.0

    ts_grid = ohlcv_data[(symbols[0], '1h')]["ts"]
    daily = defaultdict(lambda: {"wins": 0.0, "losses": 0.0, "count": 0})
    for t in trades:
        ts = ts_grid[t.ts_idx]
        day = int(ts // 86400)
        daily[day]["count"] += 1
        if t.pnl > 0:
            daily[day]["wins"] += t.pnl
        elif t.pnl < 0:
            daily[day]["losses"] += abs(t.pnl)

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
            pfs.append(10.0)  # still cap, but rare with >= 3 trades/day

    return float(np.mean(pfs)) if pfs else 0.0


def compute_median_daily_pf(trades, ohlcv_data, symbols):
    """
    Compute median daily profit factor (resistant to outlier days).
    """
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


# ==============================================================================
# PHASE 1: DATA LOADING
# ==============================================================================

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


def compute_1bar_returns(close):
    """Compute 1-bar log returns."""
    n_bars, n_symbols = close.shape
    ret = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    valid_prev = (close[:-1] != 0) & ~np.isnan(close[:-1])
    ret[1:] = np.where(valid_prev, (close[1:] - close[:-1]) / np.where(valid_prev, close[:-1], 1.0), np.nan)
    return ret


# ==============================================================================
# WALK-FORWARD VALIDATION
# ==============================================================================

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


def run_walk_forward(adaptive_signal, fwd_ret, folds, symbols, ohlcv_data, jsonl_path):
    """
    Run walk-forward for h24 with both exit configs.
    Per fold:
    1. Optimize threshold on 80/20 mini-split within training data
    2. Generate signals on test data using optimized threshold
    3. Simulate trades with both exit configs A and B
    4. Report all metrics
    """
    fold_results_all = []
    n_total_folds = len(folds) * 2  # 2 exit configs per fold
    fold_counter = 0

    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        t0_fold = time.time()
        logger.info("\n  === Fold %d/%d ===", fold_idx + 1, len(folds))

        for exit_cfg_dict in [EXIT_CONFIG_A, EXIT_CONFIG_B]:
            fold_counter += 1
            cfg_name = exit_cfg_dict["name"]
            t0 = time.time()
            logger.info("    -- Exit config: %s --", cfg_name)

            # Step 1: Optimize threshold
            exit_cfg = _make_exit_config(exit_cfg_dict)
            use_atr_stop = exit_cfg_dict.get("use_atr_stop", True)

            best_threshold = optimize_threshold(
                adaptive_signal, fwd_ret, train_mask, symbols, ohlcv_data,
                exit_cfg_dict, BASE_SLIPPAGE_1H,
            )

            # Step 2: Generate signals on test data
            test_signals = generate_ts_signals(adaptive_signal, best_threshold, symbols, test_mask)
            logger.info("    %d signals on test period (threshold=%.1f)", len(test_signals), best_threshold)

            # Step 3: Simulate trades
            trades = simulate_trades(
                test_signals, ohlcv_data, BASE_SLIPPAGE_1H, exit_cfg,
                use_atr_stop=use_atr_stop,
                time_stop_bars=exit_cfg_dict["time_stop_bars"],
                stop_atr_mult=exit_cfg_dict.get("stop_atr_mult", 2.0),
            )

            # Step 4: Compute all metrics
            if trades:
                avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
                robust_pf = compute_avg_daily_pf_robust(trades, ohlcv_data, symbols)
                median_pf = compute_median_daily_pf(trades, ohlcv_data, symbols)
                agg_pf = compute_aggregate_pf(trades)
                total_pnl = sum(t.pnl for t in trades)
                wins = sum(1 for t in trades if t.pnl > 0)
                win_rate = wins / len(trades)
                avg_r = float(np.mean([t.r_mult for t in trades]))

                reason_counts = defaultdict(int)
                for t in trades:
                    reason_counts[t.exit_reason] += 1
                exit_reasons = dict(reason_counts)
            else:
                avg_pf = robust_pf = median_pf = agg_pf = total_pnl = win_rate = avg_r = 0.0
                exit_reasons = {}

            elapsed = time.time() - t0
            logger.info("    %d trades | avg_pf=%.3f | robust_pf=%.3f | median_pf=%.3f | "
                        "agg_pf=%.3f | WR=%.1f%% | PnL=$%.2f | %.1fs",
                        len(trades), avg_pf, robust_pf, median_pf,
                        agg_pf, win_rate * 100, total_pnl, elapsed)
            if exit_reasons:
                logger.info("    Exit reasons: %s", exit_reasons)

            fr = FoldResult(
                fold_idx=fold_idx, horizon=HORIZON,
                exit_config_name=cfg_name, threshold=best_threshold,
                n_trades=len(trades), avg_daily_pf=avg_pf,
                avg_daily_pf_robust=robust_pf, median_daily_pf=median_pf,
                aggregate_pf=agg_pf, win_rate=win_rate,
                total_pnl=total_pnl, avg_r_mult=avg_r,
                exit_reasons=exit_reasons,
            )
            fold_results_all.append(fr)

            # Incremental write
            with open(jsonl_path, "a") as f:
                f.write(json.dumps({
                    "type": "fold", **asdict(fr),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, default=str) + "\n")

            # ETA estimation
            elapsed_total = time.time() - t0_fold
            if fold_counter < n_total_folds:
                avg_per_fold = elapsed_total / (fold_counter if fold_idx == 0 else fold_counter)
                remaining = n_total_folds - fold_counter
                eta_seconds = remaining * avg_per_fold / 2  # /2 because 2 configs per fold measured together
                logger.info("    Progress: %d/%d | ETA: %.0fs",
                            fold_counter, n_total_folds, eta_seconds)

    # Summary per exit config
    summaries = {}
    for cfg_name in ["standard", "time_stop_only"]:
        cfg_folds = [fr for fr in fold_results_all if fr.exit_config_name == cfg_name and fr.n_trades > 0]
        if cfg_folds:
            avg_pf = float(np.mean([fr.avg_daily_pf for fr in cfg_folds]))
            robust_pf = float(np.mean([fr.avg_daily_pf_robust for fr in cfg_folds]))
            median_pf = float(np.mean([fr.median_daily_pf for fr in cfg_folds]))
            consistency = sum(1 for fr in cfg_folds if fr.avg_daily_pf_robust > 1.0) / len(cfg_folds)
            total_trades = sum(fr.n_trades for fr in cfg_folds)
        else:
            avg_pf = robust_pf = median_pf = consistency = 0.0
            total_trades = 0

        summaries[cfg_name] = {
            "avg_daily_pf": avg_pf,
            "avg_daily_pf_robust": robust_pf,
            "median_daily_pf": median_pf,
            "consistency": consistency,
            "total_trades": total_trades,
        }

        logger.info("\n  Walk-forward summary [%s]: avg_pf=%.3f, robust_pf=%.3f, "
                    "median_pf=%.3f, consistency=%.1f%%, trades=%d",
                    cfg_name, avg_pf, robust_pf, median_pf,
                    consistency * 100, total_trades)

    return fold_results_all, summaries


# ==============================================================================
# HOLDOUT & DSR
# ==============================================================================

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


def run_holdout(adaptive_signal, fwd_ret, holdout_mask, folds, symbols, ohlcv_data,
                n_configs_tested, best_exit_cfg_dict):
    """
    Run holdout at 1.5x slippage with the best exit config.
    Threshold is re-optimized on all pre-holdout data.
    """
    # Full training = everything before holdout
    full_train_mask = np.zeros(len(holdout_mask), dtype=bool)
    holdout_start = np.where(holdout_mask)[0]
    if len(holdout_start) == 0:
        return None
    full_train_mask[:holdout_start[0]] = True

    logger.info("  Holdout: optimizing threshold on %d training bars...", full_train_mask.sum())

    exit_cfg = _make_exit_config(best_exit_cfg_dict)
    use_atr_stop = best_exit_cfg_dict.get("use_atr_stop", True)

    # Optimize threshold on full training data
    best_threshold = optimize_threshold(
        adaptive_signal, fwd_ret, full_train_mask, symbols, ohlcv_data,
        best_exit_cfg_dict, BASE_SLIPPAGE_1H * 1.5,  # optimize at 1.5x slippage
    )

    # Generate signals on holdout
    holdout_signals = generate_ts_signals(adaptive_signal, best_threshold, symbols, holdout_mask)
    logger.info("  Holdout: %d signals (threshold=%.1f)", len(holdout_signals), best_threshold)

    # 1.5x slippage
    slippage_15x = BASE_SLIPPAGE_1H * 1.5
    trades = simulate_trades(
        holdout_signals, ohlcv_data, slippage_15x, exit_cfg,
        use_atr_stop=use_atr_stop,
        time_stop_bars=best_exit_cfg_dict["time_stop_bars"],
        stop_atr_mult=best_exit_cfg_dict.get("stop_atr_mult", 2.0),
    )

    if not trades:
        logger.info("  Holdout: 0 trades")
        return None

    avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
    robust_pf = compute_avg_daily_pf_robust(trades, ohlcv_data, symbols)
    median_pf = compute_median_daily_pf(trades, ohlcv_data, symbols)
    agg_pf = compute_aggregate_pf(trades)
    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades)
    avg_r = float(np.mean([t.r_mult for t in trades]))

    reason_counts = defaultdict(int)
    for t in trades:
        reason_counts[t.exit_reason] += 1

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

    logger.info("  Holdout (1.5x slip): %d trades, avg_pf=%.3f, robust_pf=%.3f, median_pf=%.3f, "
                "agg_pf=%.3f, WR=%.1f%%, Sharpe=%.3f, DSR=%.3f, PnL=$%.2f",
                len(trades), avg_pf, robust_pf, median_pf,
                agg_pf, win_rate * 100, sharpe, dsr, total_pnl)
    logger.info("  Exit reasons: %s", dict(reason_counts))

    # 2.0x slippage sensitivity
    trades_2x = simulate_trades(
        holdout_signals, ohlcv_data, BASE_SLIPPAGE_1H * 2.0, exit_cfg,
        use_atr_stop=use_atr_stop,
        time_stop_bars=best_exit_cfg_dict["time_stop_bars"],
        stop_atr_mult=best_exit_cfg_dict.get("stop_atr_mult", 2.0),
    )
    if trades_2x:
        robust_pf_2x = compute_avg_daily_pf_robust(trades_2x, ohlcv_data, symbols)
        logger.info("  Holdout (2.0x slip): %d trades, robust_pf=%.3f", len(trades_2x), robust_pf_2x)
    else:
        robust_pf_2x = 0.0

    # Per-direction breakdown
    long_trades = [t for t in trades if t.direction == "long"]
    short_trades = [t for t in trades if t.direction == "short"]
    long_pnl = sum(t.pnl for t in long_trades)
    short_pnl = sum(t.pnl for t in short_trades)
    logger.info("  Direction breakdown: %d longs ($%.2f), %d shorts ($%.2f)",
                len(long_trades), long_pnl, len(short_trades), short_pnl)

    return {
        "exit_config": best_exit_cfg_dict["name"],
        "threshold": best_threshold,
        "avg_daily_pf": avg_pf,
        "avg_daily_pf_robust": robust_pf,
        "median_daily_pf": median_pf,
        "aggregate_pf": agg_pf,
        "dsr": dsr,
        "sharpe": sharpe,
        "n_trades": len(trades),
        "win_rate": win_rate,
        "avg_r_mult": avg_r,
        "total_pnl": total_pnl,
        "exit_reasons": dict(reason_counts),
        "robust_pf_2x_slippage": robust_pf_2x,
        "long_trades": len(long_trades),
        "long_pnl": long_pnl,
        "short_trades": len(short_trades),
        "short_pnl": short_pnl,
    }


# ==============================================================================
# REGIME DIAGNOSTICS
# ==============================================================================

def compute_regime_diagnostics(adaptive_signal, returns, close, symbols, ts_grid, fwd_ret):
    """
    Diagnostic: analyze the autocorrelation regime structure.
    - What fraction of symbol-bars are in momentum vs MR regime?
    - Does regime persist or flip frequently?
    - Are forward returns different in momentum vs MR regimes?
    """
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    n_bars, n_symbols = close.shape
    logger.info("\n  Regime diagnostics:")

    # Autocorrelation at lag 1
    ac1 = compute_autocorrelation_2d(returns, 1, AC_WINDOW)

    valid = np.isfinite(ac1) & np.isfinite(fwd_ret)
    n_total = valid.sum()
    if n_total == 0:
        logger.info("    No valid data for regime analysis")
        return {}

    # Forward returns by regime
    momentum_mask = valid & (ac1 > 0)
    mr_mask = valid & (ac1 < 0)

    n_mom = momentum_mask.sum()
    n_mr = mr_mask.sum()

    fwd_mom = fwd_ret[momentum_mask]
    fwd_mr = fwd_ret[mr_mask]

    logger.info("    Momentum regime: %d bars (%.1f%%), mean fwd ret = %.4f%%",
                n_mom, 100.0 * n_mom / n_total, np.nanmean(fwd_mom) * 100 if len(fwd_mom) > 0 else 0)
    logger.info("    MR regime: %d bars (%.1f%%), mean fwd ret = %.4f%%",
                n_mr, 100.0 * n_mr / n_total, np.nanmean(fwd_mr) * 100 if len(fwd_mr) > 0 else 0)

    # Regime persistence: what fraction of bars have the same regime as previous bar?
    regime = np.sign(ac1)
    regime_change = np.diff(regime, axis=0)
    regime_persists = (regime_change == 0) & np.isfinite(regime_change)
    regime_flips = (regime_change != 0) & np.isfinite(regime_change)
    n_persist = regime_persists.sum()
    n_flip = regime_flips.sum()
    n_regime_total = n_persist + n_flip
    if n_regime_total > 0:
        persistence_rate = n_persist / n_regime_total
        logger.info("    Regime persistence: %.1f%% (flip rate: %.1f%%)",
                    persistence_rate * 100, (1 - persistence_rate) * 100)
    else:
        persistence_rate = 0.0

    # Signal strength by regime
    sig_in_mom = adaptive_signal[momentum_mask]
    sig_in_mr = adaptive_signal[mr_mask]
    logger.info("    Signal in momentum regime: mean=%.4f, std=%.4f",
                np.nanmean(sig_in_mom), np.nanstd(sig_in_mom))
    logger.info("    Signal in MR regime: mean=%.4f, std=%.4f",
                np.nanmean(sig_in_mr), np.nanstd(sig_in_mr))

    # Per-symbol regime heterogeneity
    # How many symbols are predominantly momentum vs MR?
    sym_momentum_frac = []
    for s_idx in range(n_symbols):
        col = ac1[:, s_idx]
        v = np.isfinite(col)
        if v.sum() < 50:
            continue
        mom_frac = (col[v] > 0).sum() / v.sum()
        sym_momentum_frac.append(mom_frac)

    if sym_momentum_frac:
        frac_arr = np.array(sym_momentum_frac)
        logger.info("    Per-symbol momentum fraction: mean=%.2f, std=%.2f, "
                    "min=%.2f, max=%.2f",
                    np.mean(frac_arr), np.std(frac_arr),
                    np.min(frac_arr), np.max(frac_arr))

    return {
        "n_momentum_bars": int(n_mom),
        "n_mr_bars": int(n_mr),
        "momentum_fwd_ret_mean": float(np.nanmean(fwd_mom)) if len(fwd_mom) > 0 else 0.0,
        "mr_fwd_ret_mean": float(np.nanmean(fwd_mr)) if len(fwd_mr) > 0 else 0.0,
        "regime_persistence_rate": float(persistence_rate),
    }


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Signal Genesis Time-Series — Per-Symbol Adaptive (h24)")
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    parser.add_argument("--n-folds", type=int, default=N_FOLDS)
    args = parser.parse_args()

    t_start = time.time()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    logger.info("=" * 72)
    logger.info("SIGNAL GENESIS TIME-SERIES — Per-Symbol Adaptive Momentum/Reversion")
    logger.info("=" * 72)
    logger.info("Config: days=%d, horizon=%d, folds=%d, holdout=%d days",
                args.days, HORIZON, args.n_folds, args.holdout_days)
    logger.info("AC lags=%s, AC window=%d", AC_LAGS, AC_WINDOW)
    logger.info("Sharpe windows=%s, MR window=%d, ER window=%d",
                SHARPE_WINDOWS, MR_WINDOW, ER_WINDOW)
    logger.info("Threshold sweep=%s", THRESHOLD_SWEEP)
    logger.info("Exit configs: A (standard ATR stop), B (time-stop only)")

    # ── Phase 1: Data Loading ─────────────────────────────────────────────────
    logger.info("\n" + "-" * 60)
    logger.info("PHASE 1: Data Loading")
    logger.info("-" * 60)

    close, open_, high, low, volume, ts_grid, symbols, btc_idx, ohlcv_data = \
        load_ohlcv_matrices(database_url, args.days)

    logger.info("Computing forward returns (h=%d)...", HORIZON)
    fwd_ret = compute_forward_returns(close)

    logger.info("Computing 1-bar returns...")
    returns = compute_1bar_returns(close)

    # ── Phase 2: Time-Series Feature Computation ─────────────────────────────
    logger.info("\n" + "-" * 60)
    logger.info("PHASE 2: Time-Series Feature Computation")
    logger.info("-" * 60)

    adaptive_signal = compute_adaptive_signal(
        close, returns,
        ac_lags=AC_LAGS, ac_window=AC_WINDOW,
        sharpe_windows=SHARPE_WINDOWS,
        mr_window=MR_WINDOW, er_window=ER_WINDOW,
    )

    # ── Phase 3: Regime Diagnostics ───────────────────────────────────────────
    logger.info("\n" + "-" * 60)
    logger.info("PHASE 3: Regime Diagnostics")
    logger.info("-" * 60)

    regime_diag = compute_regime_diagnostics(
        adaptive_signal, returns, close, symbols, ts_grid, fwd_ret,
    )

    # ── Phase 4: Walk-Forward Validation ──────────────────────────────────────
    logger.info("\n" + "-" * 60)
    logger.info("PHASE 4: Walk-Forward Validation")
    logger.info("-" * 60)

    folds, holdout_mask = build_folds(ts_grid, n_folds=args.n_folds, holdout_days=args.holdout_days)
    if not folds:
        logger.error("No folds could be constructed. Need more data.")
        sys.exit(1)

    script_dir = os.path.dirname(__file__)
    jsonl_path = os.path.join(script_dir, "signal_genesis_timeseries_incremental.jsonl")

    # Clear previous incremental file
    if os.path.exists(jsonl_path):
        os.remove(jsonl_path)

    fold_results, wf_summaries = run_walk_forward(
        adaptive_signal, fwd_ret, folds, symbols, ohlcv_data, jsonl_path,
    )

    # ── Phase 5: Determine Best Exit Config ──────────────────────────────────
    logger.info("\n" + "-" * 60)
    logger.info("PHASE 5: Holdout Evaluation")
    logger.info("-" * 60)

    # Decide which exit config performed better in walk-forward
    best_cfg_name = None
    best_cfg_score = -999.0
    for cfg_name, summary in wf_summaries.items():
        score = summary["avg_daily_pf_robust"]
        if score > best_cfg_score and summary["total_trades"] >= MIN_TRADES_TOTAL:
            best_cfg_score = score
            best_cfg_name = cfg_name

    if best_cfg_name is None:
        # Fallback: pick whichever has more trades
        for cfg_name, summary in wf_summaries.items():
            if summary["total_trades"] > 0:
                if best_cfg_name is None or summary["total_trades"] > wf_summaries[best_cfg_name]["total_trades"]:
                    best_cfg_name = cfg_name
                    best_cfg_score = summary["avg_daily_pf_robust"]

    if best_cfg_name is None:
        best_cfg_name = "standard"  # default fallback

    best_exit_cfg_dict = EXIT_CONFIG_A if best_cfg_name == "standard" else EXIT_CONFIG_B
    logger.info("  Best exit config from walk-forward: %s (robust_pf=%.3f)", best_cfg_name, best_cfg_score)

    # Check walk-forward gate using the ROBUST metric
    wf_summary = wf_summaries.get(best_cfg_name, {})
    wf_robust_pf = wf_summary.get("avg_daily_pf_robust", 0.0)
    wf_consistency = wf_summary.get("consistency", 0.0)
    wf_total_trades = wf_summary.get("total_trades", 0)

    wf_pass = (wf_robust_pf > 1.0
               and wf_consistency >= MIN_FOLD_CONSISTENCY
               and wf_total_trades >= MIN_TRADES_TOTAL)

    # Total configs tested: len(THRESHOLD_SWEEP) * 2 exit configs * n_folds
    n_configs_tested = len(THRESHOLD_SWEEP) * 2 * len(folds)

    holdout_result = None
    if wf_pass:
        holdout_result = run_holdout(
            adaptive_signal, fwd_ret, holdout_mask, folds, symbols, ohlcv_data,
            n_configs_tested=n_configs_tested,
            best_exit_cfg_dict=best_exit_cfg_dict,
        )
    else:
        logger.info("  SKIPPING holdout (walk-forward failed: robust_pf=%.3f, consistency=%.1f%%, trades=%d)",
                    wf_robust_pf, wf_consistency * 100, wf_total_trades)

        # Still run holdout for diagnostic purposes (but don't gate on it)
        logger.info("  Running holdout anyway for diagnostic purposes...")
        holdout_result = run_holdout(
            adaptive_signal, fwd_ret, holdout_mask, folds, symbols, ohlcv_data,
            n_configs_tested=n_configs_tested,
            best_exit_cfg_dict=best_exit_cfg_dict,
        )

    # ── Phase 6: Final Report ────────────────────────────────────────────────
    logger.info("\n" + "-" * 60)
    logger.info("PHASE 6: Final Report")
    logger.info("-" * 60)

    elapsed_total = time.time() - t_start

    # Determine verdict using ROBUST metric
    if not wf_pass:
        verdict = "FAIL_WALK_FORWARD"
        fail_reasons = []
        if wf_robust_pf <= 1.0:
            fail_reasons.append(f"robust_avg_daily_pf={wf_robust_pf:.3f} <= 1.0")
        if wf_consistency < MIN_FOLD_CONSISTENCY:
            fail_reasons.append(f"consistency={wf_consistency:.2f} < {MIN_FOLD_CONSISTENCY:.2f}")
        if wf_total_trades < MIN_TRADES_TOTAL:
            fail_reasons.append(f"trades={wf_total_trades} < {MIN_TRADES_TOTAL}")
    elif holdout_result is None:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = ["no holdout result"]
    elif holdout_result["avg_daily_pf_robust"] <= 1.0:
        verdict = "FAIL_HOLDOUT"
        fail_reasons = [f"holdout_robust_avg_daily_pf={holdout_result['avg_daily_pf_robust']:.3f} <= 1.0"]
    elif holdout_result["dsr"] <= 0:
        verdict = "FAIL_DSR"
        fail_reasons = [f"DSR={holdout_result['dsr']:.3f} <= 0"]
    else:
        verdict = "CONFIRMED"
        fail_reasons = []

    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed_total,
        "model": "timeseries_adaptive",
        "hypothesis": "Per-symbol adaptive momentum/reversion based on own autocorrelation structure",
        "horizon": HORIZON,
        "config": {
            "ac_lags": AC_LAGS,
            "ac_window": AC_WINDOW,
            "sharpe_windows": SHARPE_WINDOWS,
            "mr_window": MR_WINDOW,
            "er_window": ER_WINDOW,
            "threshold_sweep": THRESHOLD_SWEEP,
            "exit_configs": ["standard", "time_stop_only"],
            "n_folds": len(folds),
            "holdout_days": args.holdout_days,
            "slippage_bps": BASE_SLIPPAGE_1H,
            "fee_bps": FEE_BPS,
        },
        "data": {
            "n_symbols": len(symbols),
            "n_bars": len(ts_grid),
            "days_loaded": args.days,
        },
        "regime_diagnostics": regime_diag,
        "walk_forward": {
            "summaries": wf_summaries,
            "best_exit_config": best_cfg_name,
            "pass": wf_pass,
            "folds": [asdict(fr) for fr in fold_results],
        },
        "holdout": holdout_result if holdout_result else {
            "avg_daily_pf": 0.0, "avg_daily_pf_robust": 0.0, "median_daily_pf": 0.0,
            "aggregate_pf": 0.0, "dsr": -99.0, "n_trades": 0,
        },
        "n_configs_tested": n_configs_tested,
        "verdict": verdict,
        "fail_reasons": fail_reasons,
    }

    output_path = os.path.join(script_dir, "signal_genesis_timeseries_results.json")
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Results written to %s", output_path)

    # Incremental write: final summary
    with open(jsonl_path, "a") as f:
        f.write(json.dumps({
            "type": "final",
            "verdict": verdict,
            "fail_reasons": fail_reasons,
            "walk_forward_summaries": wf_summaries,
            "holdout": holdout_result,
            "elapsed_seconds": elapsed_total,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, default=str) + "\n")

    # Print summary
    logger.info("\n" + "=" * 72)
    logger.info("SUMMARY")
    logger.info("=" * 72)
    logger.info("Elapsed: %.1f min", elapsed_total / 60)
    logger.info("Verdict: %s", verdict)
    logger.info("")

    for cfg_name, summary in wf_summaries.items():
        logger.info("Walk-forward [%s]:", cfg_name)
        logger.info("  avg_daily_pf=%.3f, robust_pf=%.3f, median_pf=%.3f",
                    summary["avg_daily_pf"], summary["avg_daily_pf_robust"],
                    summary["median_daily_pf"])
        logger.info("  consistency=%.1f%%, trades=%d",
                    summary["consistency"] * 100, summary["total_trades"])

    if holdout_result:
        logger.info("")
        logger.info("Holdout [%s, 1.5x slip]:", holdout_result.get("exit_config", "?"))
        logger.info("  avg_pf=%.3f, robust_pf=%.3f, median_pf=%.3f",
                    holdout_result["avg_daily_pf"],
                    holdout_result["avg_daily_pf_robust"],
                    holdout_result["median_daily_pf"])
        logger.info("  agg_pf=%.3f, Sharpe=%.3f, DSR=%.3f",
                    holdout_result["aggregate_pf"],
                    holdout_result["sharpe"],
                    holdout_result["dsr"])
        logger.info("  %d trades, WR=%.1f%%, PnL=$%.2f",
                    holdout_result["n_trades"],
                    holdout_result["win_rate"] * 100,
                    holdout_result["total_pnl"])
        logger.info("  robust_pf at 2x slip: %.3f", holdout_result.get("robust_pf_2x_slippage", 0.0))

    if fail_reasons:
        logger.info("")
        for r in fail_reasons:
            logger.info("  FAIL: %s", r)

    # Metric comparison table
    logger.info("")
    logger.info("Metric comparison (original vs robust vs median):")
    logger.info("  %-20s  %-10s  %-10s  %-10s", "Phase", "avg_pf", "robust_pf", "median_pf")
    logger.info("  %-20s  %-10s  %-10s  %-10s", "-" * 20, "-" * 10, "-" * 10, "-" * 10)
    for cfg_name, summary in wf_summaries.items():
        logger.info("  %-20s  %-10.3f  %-10.3f  %-10.3f",
                    f"WF [{cfg_name[:8]}]",
                    summary["avg_daily_pf"],
                    summary["avg_daily_pf_robust"],
                    summary["median_daily_pf"])
    if holdout_result:
        logger.info("  %-20s  %-10.3f  %-10.3f  %-10.3f",
                    "Holdout",
                    holdout_result["avg_daily_pf"],
                    holdout_result["avg_daily_pf_robust"],
                    holdout_result["median_daily_pf"])


if __name__ == "__main__":
    main()
