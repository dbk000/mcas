"""
EXTRACTED PURE LOGIC FROM: SCR_IND_FAM_ma.py
V5 Updates: Full Quarantine enforced.
have been physically replaced to satisfy the zero-contamination mandate.
Dataclass imports are explicitly injected to guarantee semantics.
"""

from dataclasses import dataclass, field, asdict

import numpy as np

from ..rolling_utils import (
    rolling_mean,
    rolling_ema,
    rolling_sum,
    rolling_std,
)

from . import IndicatorFamily, IndicatorVariant, register_family

SMA_PERIODS = [5, 8, 10, 13, 20, 21, 34, 50, 55, 89, 100, 200]

def _compute_sma(open_, high, low, close, volume, timestamps, context, variant):
    p = variant.params["period"]
    return rolling_mean(close, p)

_sma_family = register_family(IndicatorFamily(
    name="MA_SMA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(
            name=f"SMA_{p}",
            family="MA_SMA",
            params={"period": p},
            retain_in_context=p in (20, 50, 200),
        )
        for p in SMA_PERIODS
    ],
    compute_fn=_compute_sma,
))

EMA_PERIODS = [5, 8, 9, 10, 12, 13, 20, 21, 26, 34, 50, 200]

def _compute_ema(open_, high, low, close, volume, timestamps, context, variant):
    p = variant.params["period"]
    return rolling_ema(close, p)

_ema_family = register_family(IndicatorFamily(
    name="MA_EMA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(
            name=f"EMA_{p}",
            family="MA_EMA",
            params={"period": p},
            retain_in_context=p in (9, 12, 20, 26, 50),
        )
        for p in EMA_PERIODS
    ],
    compute_fn=_compute_ema,
))

WMA_PERIODS = [9, 14, 20, 30, 50, 100]

def _compute_wma(open_, high, low, close, volume, timestamps, context, variant):
    """WMA via FIR filter (lfilter). Memory-safe at any scale."""
    from scipy.signal import lfilter

    p = variant.params["period"]
    n_bars, n_sym = close.shape
    out = np.full_like(close, np.nan)
    if n_bars < p:
        return out

    weights = np.arange(1, p + 1, dtype=np.float64)
    weights = weights / weights.sum()
    filled = close.copy()
    nan_mask = np.isnan(filled)
    filled[nan_mask] = 0.0
    result = lfilter(weights, [1.0], filled, axis=0)
    result[nan_mask] = np.nan
    result[:p - 1] = np.nan
    return result

_wma_family = register_family(IndicatorFamily(
    name="MA_WMA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"WMA_{p}", family="MA_WMA", params={"period": p})
        for p in WMA_PERIODS
    ],
    compute_fn=_compute_wma,
))

DEMA_PERIODS = [9, 14, 20, 30, 50, 100]

def _compute_dema(open_, high, low, close, volume, timestamps, context, variant):
    """Double EMA: DEMA = 2*EMA - EMA(EMA)."""
    p = variant.params["period"]
    ema1 = rolling_ema(close, p)
    ema2 = rolling_ema(ema1, p)
    return 2.0 * ema1 - ema2

_dema_family = register_family(IndicatorFamily(
    name="MA_DEMA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"DEMA_{p}", family="MA_DEMA", params={"period": p})
        for p in DEMA_PERIODS
    ],
    compute_fn=_compute_dema,
))

TEMA_PERIODS = [9, 14, 20, 30, 50, 100]

def _compute_tema(open_, high, low, close, volume, timestamps, context, variant):
    """Triple EMA: TEMA = 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))."""
    p = variant.params["period"]
    ema1 = rolling_ema(close, p)
    ema2 = rolling_ema(ema1, p)
    ema3 = rolling_ema(ema2, p)
    return 3.0 * ema1 - 3.0 * ema2 + ema3

_tema_family = register_family(IndicatorFamily(
    name="MA_TEMA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"TEMA_{p}", family="MA_TEMA", params={"period": p})
        for p in TEMA_PERIODS
    ],
    compute_fn=_compute_tema,
))

HMA_PERIODS = [9, 14, 20, 30, 50, 100]

def _compute_hma(open_, high, low, close, volume, timestamps, context, variant):
    """HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    from scipy.signal import lfilter

    p = variant.params["period"]
    half_p = max(2, p // 2)
    sqrt_p = max(2, int(np.sqrt(p)))

    def _wma_arr(arr, w):
        weights = np.arange(1, w + 1, dtype=np.float64)
        weights = weights / weights.sum()
        filled = arr.copy()
        nm = np.isnan(filled)
        filled[nm] = 0.0
        result = lfilter(weights, [1.0], filled, axis=0)
        result[nm] = np.nan
        result[:w - 1] = np.nan
        return result

    wma_half = _wma_arr(close, half_p)
    wma_full = _wma_arr(close, p)
    diff = 2.0 * wma_half - wma_full
    return _wma_arr(diff, sqrt_p)

_hma_family = register_family(IndicatorFamily(
    name="MA_HMA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"HMA_{p}", family="MA_HMA", params={"period": p})
        for p in HMA_PERIODS
    ],
    compute_fn=_compute_hma,
))

KAMA_PERIODS = [10, 14, 20, 30, 50]

KAMA_FAST = 2     # fast EMA constant

KAMA_SLOW = 30    # slow EMA constant

def _compute_kama(open_, high, low, close, volume, timestamps, context, variant):
    """KAMA: adapts smoothing based on efficiency ratio."""
    p = variant.params["period"]
    n_bars, n_sym = close.shape
    out = np.full_like(close, np.nan)
    if n_bars < p + 1:
        return out

    fast_sc = 2.0 / (KAMA_FAST + 1)
    slow_sc = 2.0 / (KAMA_SLOW + 1)

    # Direction = |close[t] - close[t-p]|
    direction = np.abs(close[p:] - close[:-p])

    # Volatility = sum of |close[t] - close[t-1]| over p bars
    abs_diff = np.abs(np.diff(close, axis=0))  # (n_bars-1, n_sym)
    volatility = rolling_sum(abs_diff, p)
    volatility = volatility[p - 1:]  # align with direction: both start at index p

    # Efficiency Ratio
    er = np.where(volatility > 0, direction / volatility, 0.0)

    # Smoothing constant: SC = (ER * (fast - slow) + slow)^2
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2

    # KAMA: iterative — can't avoid the loop along axis=0
    kama = np.full((n_bars - p, n_sym), np.nan)
    kama[0] = close[p]  # seed with close at bar p

    for t in range(1, kama.shape[0]):
        kama[t] = kama[t - 1] + sc[t] * (close[p + t] - kama[t - 1])

    out[p:] = kama
    return out

_kama_family = register_family(IndicatorFamily(
    name="MA_KAMA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"KAMA_{p}", family="MA_KAMA", params={"period": p})
        for p in KAMA_PERIODS
    ],
    compute_fn=_compute_kama,
))

FRAMA_PERIODS = [10, 16, 20, 30, 50]

def _compute_frama(open_, high, low, close, volume, timestamps, context, variant):
    """FRAMA: adapts alpha based on fractal dimension of price."""
    p = variant.params["period"]
    n_bars, n_sym = close.shape
    half = p // 2
    out = np.full_like(close, np.nan)
    if n_bars < p + 1:
        return out

    # Fractal dimension via Higuchi-inspired approach
    # N1 = (max - min) over first half
    # N2 = (max - min) over second half
    # N3 = (max - min) over full period
    from ..rolling_utils import rolling_max, rolling_min

    hh_full = rolling_max(high, p)
    ll_full = rolling_min(low, p)
    hh_half = rolling_max(high, half)
    ll_half = rolling_min(low, half)

    n3 = hh_full - ll_full
    n1 = hh_half - ll_half

    # N2: second-half range — shift the half-period range
    n2 = np.full_like(close, np.nan)
    if n_bars >= p:
        # Range of (high, low) from [t-p+1 to t-half]
        high_shifted = np.full_like(high, np.nan)
        low_shifted = np.full_like(low, np.nan)
        high_shifted[half:] = high[:-half] if half > 0 else high
        low_shifted[half:] = low[:-half] if half > 0 else low
        hh_half2 = rolling_max(high_shifted, half)
        ll_half2 = rolling_min(low_shifted, half)
        n2 = hh_half2 - ll_half2

    # Fractal dimension
    sum_n12 = n1 + n2
    d = np.where(
        (sum_n12 > 0) & (n3 > 0),
        np.log(sum_n12 / n3) / np.log(2.0),
        1.0,
    )

    # Alpha from dimension
    alpha = np.exp(-4.6 * (d - 1.0))
    alpha = np.clip(alpha, 0.01, 1.0)

    # Iterative EMA with varying alpha
    frama = np.full_like(close, np.nan)
    frama[p - 1] = close[p - 1]

    for t in range(p, n_bars):
        a = alpha[t]
        prev = frama[t - 1]
        nan_mask = np.isnan(prev)
        frama[t] = np.where(nan_mask, close[t], prev + a * (close[t] - prev))

    out = frama
    return out

_frama_family = register_family(IndicatorFamily(
    name="MA_FRAMA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"FRAMA_{p}", family="MA_FRAMA", params={"period": p})
        for p in FRAMA_PERIODS
    ],
    compute_fn=_compute_frama,
))

VIDYA_PERIODS = [10, 14, 20, 30, 50]

def _compute_vidya(open_, high, low, close, volume, timestamps, context, variant):
    """VIDYA: EMA weighted by Chande Momentum Oscillator ratio."""
    p = variant.params["period"]
    n_bars, n_sym = close.shape
    out = np.full_like(close, np.nan)
    if n_bars < p + 1:
        return out

    # CMO for volatility index
    diff = np.diff(close, axis=0)  # (n_bars-1, n_sym)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)

    sum_gains = rolling_sum(gains, p)
    sum_losses = rolling_sum(losses, p)

    # Align: rolling_sum output[p-1] is first valid → corresponds to close[p]
    sg = sum_gains[p - 1:]  # (n_bars-p, n_sym)
    sl = sum_losses[p - 1:]

    total = sg + sl
    cmo_abs = np.where(total > 0, np.abs(sg - sl) / total, 0.0)

    # Smoothing constant
    sc = 2.0 / (p + 1)

    # VIDYA: iterative
    vidya = np.full((n_bars - p, n_sym), np.nan)
    vidya[0] = close[p]

    for t in range(1, vidya.shape[0]):
        alpha_t = sc * cmo_abs[t]
        vidya[t] = alpha_t * close[p + t] + (1.0 - alpha_t) * vidya[t - 1]

    out[p:] = vidya
    return out

_vidya_family = register_family(IndicatorFamily(
    name="MA_VIDYA",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"VIDYA_{p}", family="MA_VIDYA", params={"period": p})
        for p in VIDYA_PERIODS
    ],
    compute_fn=_compute_vidya,
))

