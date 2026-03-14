"""
EXTRACTED PURE LOGIC FROM: SCR_IND_FAM_trend.py
V5 Updates: Full Quarantine enforced.
have been physically replaced to satisfy the zero-contamination mandate.
Dataclass imports are explicitly injected to guarantee semantics.
"""

from dataclasses import dataclass, field, asdict

import numpy as np

from ..rolling_utils import (
    rolling_ema,
    rolling_wilder,
    rolling_mean,
    rolling_max,
    rolling_min,
    true_range,
)

from . import IndicatorFamily, IndicatorVariant, register_family

MACD_CONFIGS = [
    (12, 26, 9),    # standard
    (5, 35, 5),     # alternative
]

def _compute_macd(open_, high, low, close, volume, timestamps, context, variant):
    """MACD = EMA(fast) - EMA(slow). Signal = EMA(MACD, signal). Hist = MACD - Signal."""
    fast = variant.params["fast"]
    slow = variant.params["slow"]
    signal = variant.params["signal"]
    output = variant.params["output"]

    ema_fast = context.get(f"EMA_{fast}", rolling_ema(close, fast))
    ema_slow = context.get(f"EMA_{slow}", rolling_ema(close, slow))

    macd_line = ema_fast - ema_slow

    if output == "MACD":
        return macd_line
    elif output == "SIGNAL":
        return rolling_ema(macd_line, signal)
    else:  # HIST
        return macd_line - rolling_ema(macd_line, signal)

_macd_variants = []

_macd_family = register_family(IndicatorFamily(
    name="TREND_MACD",
    tier=2,
    dependencies=["MA_EMA"],
    warmup=200,
    variants=_macd_variants,
    compute_fn=_compute_macd,
))

ADX_PERIODS = [14, 20]

def _compute_adx(open_, high, low, close, volume, timestamps, context, variant):
    """ADX: smoothed directional movement."""
    p = variant.params["period"]
    output = variant.params["output"]

    # Directional movement
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Pad first row
    plus_dm = np.concatenate([np.zeros((1, close.shape[1])), plus_dm], axis=0)
    minus_dm = np.concatenate([np.zeros((1, close.shape[1])), minus_dm], axis=0)

    tr = true_range(high, low, close)
    atr = rolling_wilder(tr, p)

    smooth_plus = rolling_wilder(plus_dm, p)
    smooth_minus = rolling_wilder(minus_dm, p)

    plus_di = np.where(atr > 0, 100.0 * smooth_plus / atr, 0.0)
    minus_di = np.where(atr > 0, 100.0 * smooth_minus / atr, 0.0)

    if output == "PLUS_DI":
        return plus_di
    elif output == "MINUS_DI":
        return minus_di
    else:  # ADX
        di_sum = plus_di + minus_di
        dx = np.where(di_sum > 0, 100.0 * np.abs(plus_di - minus_di) / di_sum, 0.0)
        adx = rolling_wilder(dx, p)
        return adx

_adx_variants = []

_adx_family = register_family(IndicatorFamily(
    name="TREND_ADX",
    tier=2,
    dependencies=["VOL_ATR"],
    warmup=200,
    variants=_adx_variants,
    compute_fn=_compute_adx,
))

AROON_PERIODS = [14, 25]

def _compute_aroon(open_, high, low, close, volume, timestamps, context, variant):
    """Aroon Up/Down: bars since highest high / lowest low."""
    p = variant.params["period"]
    output = variant.params["output"]

    from ..rolling_utils import rolling_argmax, rolling_argmin

    if output == "UP":
        bars_since = rolling_argmax(high, p + 1)
        return (p - bars_since) / p * 100.0
    else:  # DOWN
        bars_since = rolling_argmin(low, p + 1)
        return (p - bars_since) / p * 100.0

_aroon_variants = []

_aroon_family = register_family(IndicatorFamily(
    name="TREND_AROON",
    tier=1,
    warmup=200,
    variants=_aroon_variants,
    compute_fn=_compute_aroon,
))

ST_CONFIGS = [
    (10, 1.5),
    (10, 3.0),   # standard
    (20, 2.0),
]

def _compute_supertrend(open_, high, low, close, volume, timestamps, context, variant):
    """Supertrend: ATR-based trailing stop that flips direction."""
    p = variant.params["period"]
    mult = variant.params["mult"]
    output = variant.params["output"]

    atr = context.get(f"ATR_{p}")
    if atr is None:
        atr = rolling_wilder(true_range(high, low, close), p)

    hl2 = (high + low) / 2.0
    upper_band = hl2 + mult * atr
    lower_band = hl2 - mult * atr

    n_bars, n_sym = close.shape
    st = np.full_like(close, np.nan)
    direction = np.ones((n_bars, n_sym))  # 1 = uptrend

    # Initialize
    st[p - 1] = upper_band[p - 1]

    for t in range(p, n_bars):
        prev_dir = direction[t - 1]
        prev_st = st[t - 1]

        # In uptrend (direction=1): st = lower_band, price crossing below → flip
        # In downtrend (direction=-1): st = upper_band, price crossing above → flip
        new_lower = np.where(
            lower_band[t] > lower_band[t - 1],
            lower_band[t],
            np.where(close[t - 1] > lower_band[t - 1], lower_band[t - 1], lower_band[t]),
        )
        new_upper = np.where(
            upper_band[t] < upper_band[t - 1],
            upper_band[t],
            np.where(close[t - 1] < upper_band[t - 1], upper_band[t - 1], upper_band[t]),
        )

        # Direction logic
        was_up = prev_dir > 0
        flip_down = was_up & (close[t] < new_lower)
        flip_up = (~was_up) & (close[t] > new_upper)

        direction[t] = np.where(flip_down, -1, np.where(flip_up, 1, prev_dir))
        st[t] = np.where(direction[t] > 0, new_lower, new_upper)

    if output == "VALUE":
        return st
    else:  # DIRECTION
        return direction

_st_variants = []

_st_family = register_family(IndicatorFamily(
    name="TREND_SUPERTREND",
    tier=2,
    dependencies=["VOL_ATR"],
    warmup=200,
    variants=_st_variants,
    compute_fn=_compute_supertrend,
))

SAR_CONFIGS = [
    (0.01, 0.2),
    (0.02, 0.2),   # standard
    (0.02, 0.3),
]

def _compute_sar(open_, high, low, close, volume, timestamps, context, variant):
    """Parabolic SAR: trailing stop with acceleration factor."""
    af_start = variant.params["af_start"]
    af_max = variant.params["af_max"]
    af_step = af_start

    n_bars, n_sym = close.shape
    sar = np.full_like(close, np.nan)
    direction = np.ones((n_bars, n_sym))  # 1=long, -1=short

    # Seed
    start = 1
    sar[start] = low[0]
    ep = high[0].copy()  # extreme point
    af = np.full(n_sym, af_start)

    for t in range(start + 1, n_bars):
        prev_sar = sar[t - 1]
        prev_dir = direction[t - 1]
        is_long = prev_dir > 0

        # Compute new SAR
        new_sar = prev_sar + af * (ep - prev_sar)

        # Clamp SAR within prior 2 bars' range
        new_sar = np.where(
            is_long,
            np.minimum(new_sar, np.minimum(low[t - 1], low[t - 2] if t >= 2 else low[t - 1])),
            np.maximum(new_sar, np.maximum(high[t - 1], high[t - 2] if t >= 2 else high[t - 1])),
        )

        # Check for reversal
        long_reversal = is_long & (low[t] < new_sar)
        short_reversal = (~is_long) & (high[t] > new_sar)

        # Long → Short reversal
        sar[t] = np.where(long_reversal, ep, np.where(short_reversal, ep, new_sar))
        direction[t] = np.where(long_reversal, -1, np.where(short_reversal, 1, prev_dir))

        # Update EP and AF
        new_dir = direction[t]
        new_is_long = new_dir > 0

        new_ep_long = np.maximum(ep, high[t])
        new_ep_short = np.minimum(ep, low[t])
        new_ep = np.where(new_is_long, new_ep_long, new_ep_short)

        ep_changed = new_ep != ep
        af = np.where(
            long_reversal | short_reversal,
            af_start,
            np.where(ep_changed, np.minimum(af + af_step, af_max), af),
        )
        ep = new_ep

    return sar

_sar_variants = [
    IndicatorVariant(
        name=f"SAR_{str(afs).replace('.','p')}_{str(afm).replace('.','p')}",
        family="TREND_SAR",
        params={"af_start": afs, "af_max": afm},
    )
    for afs, afm in SAR_CONFIGS
]

_sar_family = register_family(IndicatorFamily(
    name="TREND_SAR",
    tier=2,
    dependencies=["VOL_ATR"],
    warmup=200,
    variants=_sar_variants,
    compute_fn=_compute_sar,
))

def _compute_ichimoku(open_, high, low, close, volume, timestamps, context, variant):
    """Ichimoku Cloud components. Uses crypto-standard 9/26/52 periods."""
    output = variant.params["output"]
    n_bars = close.shape[0]

    # Tenkan-sen (conversion) = (HH9 + LL9) / 2
    tenkan = (rolling_max(high, 9) + rolling_min(low, 9)) / 2.0

    # Kijun-sen (base) = (HH26 + LL26) / 2
    kijun = (rolling_max(high, 26) + rolling_min(low, 26)) / 2.0

    if output == "TENKAN":
        return tenkan
    elif output == "KIJUN":
        return kijun
    elif output == "SENKOU_A":
        # Senkou Span A = (tenkan + kijun) / 2, shifted forward 26 bars
        sa = (tenkan + kijun) / 2.0
        shifted = np.full_like(close, np.nan)
        if n_bars > 26:
            shifted[26:] = sa[:-26]
        return shifted
    elif output == "SENKOU_B":
        # Senkou Span B = (HH52 + LL52) / 2, shifted forward 26 bars
        sb = (rolling_max(high, 52) + rolling_min(low, 52)) / 2.0
        shifted = np.full_like(close, np.nan)
        if n_bars > 26:
            shifted[26:] = sb[:-26]
        return shifted
    else:  # CHIKOU
        # Chikou = close shifted back 26 bars
        shifted = np.full_like(close, np.nan)
        if n_bars > 26:
            shifted[:-26] = close[26:]
        return shifted

_ichi_variants = [
    IndicatorVariant(name=f"ICHI_{out}", family="TREND_ICHIMOKU", params={"output": out})
    for out in ["TENKAN", "KIJUN", "SENKOU_A", "SENKOU_B", "CHIKOU"]
]

_ichi_family = register_family(IndicatorFamily(
    name="TREND_ICHIMOKU",
    tier=1,
    warmup=200,
    variants=_ichi_variants,
    compute_fn=_compute_ichimoku,
))

