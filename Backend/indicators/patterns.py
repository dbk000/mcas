"""
EXTRACTED PURE LOGIC FROM: SCR_IND_FAM_patterns.py
V5 Updates: Full Quarantine enforced.
have been physically replaced to satisfy the zero-contamination mandate.
Dataclass imports are explicitly injected to guarantee semantics.
"""

from dataclasses import dataclass, field, asdict

import numpy as np

from ..rolling_utils import (
    rolling_max,
    rolling_min,
    rolling_mean,
    rolling_std,
)

from . import IndicatorFamily, IndicatorVariant, register_family

def _body(open_, close):
    return close - open_

def _body_abs(open_, close):
    return np.abs(close - open_)

def _upper_shadow(high, open_, close):
    return high - np.maximum(open_, close)

def _lower_shadow(low, open_, close):
    return np.minimum(open_, close) - low

def _range_(high, low):
    return high - low

def _doji(O, H, L, C, V):
    """Doji: body < 10% of range."""
    r = _range_(H, L)
    b = _body_abs(O, C)
    return np.where((r > 0) & (b < 0.1 * r), np.sign(_body(O, C)), 0.0)

def _hammer(O, H, L, C, V):
    """Hammer/Hanging Man: lower shadow >= 2x body, small upper shadow."""
    b = _body_abs(O, C)
    ls = _lower_shadow(L, O, C)
    us = _upper_shadow(H, O, C)
    r = _range_(H, L)
    cond = (ls >= 2.0 * b) & (us < 0.3 * r) & (r > 0)
    return np.where(cond, 1.0, 0.0)

def _inverted_hammer(O, H, L, C, V):
    """Inverted Hammer: upper shadow >= 2x body, small lower shadow."""
    b = _body_abs(O, C)
    us = _upper_shadow(H, O, C)
    ls = _lower_shadow(L, O, C)
    r = _range_(H, L)
    cond = (us >= 2.0 * b) & (ls < 0.3 * r) & (r > 0)
    return np.where(cond, 1.0, 0.0)

def _engulfing(O, H, L, C, V):
    """Bullish/Bearish Engulfing."""
    body_cur = _body(O, C)
    body_prev = np.empty_like(body_cur)
    body_prev[0] = 0.0
    body_prev[1:] = (C[:-1] - O[:-1])

    bull = (body_cur > 0) & (body_prev < 0) & (C > O) & (np.abs(body_cur) > np.abs(body_prev))
    bear = (body_cur < 0) & (body_prev > 0) & (C < O) & (np.abs(body_cur) > np.abs(body_prev))
    result = np.where(bull, 1.0, np.where(bear, -1.0, 0.0))
    result[0] = 0.0
    return result

def _morning_star(O, H, L, C, V):
    """Morning Star (3-bar bullish reversal)."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 3:
        return result
    # Bar 0: large bearish, Bar 1: small body (star), Bar 2: large bullish
    body0 = C[:-2] - O[:-2]
    body1 = _body_abs(O[1:-1], C[1:-1])
    body2 = C[2:] - O[2:]
    range0 = _range_(H[:-2], L[:-2])

    cond = (
        (body0 < 0) &                          # day 0 bearish
        (body1 < 0.3 * np.abs(body0)) &         # day 1 small body
        (body2 > 0) &                           # day 2 bullish
        (C[2:] > (O[:-2] + C[:-2]) / 2.0)      # day 2 closes above midpoint of day 0
    )
    result[2:] = np.where(cond, 1.0, 0.0)
    return result

def _evening_star(O, H, L, C, V):
    """Evening Star (3-bar bearish reversal)."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 3:
        return result
    body0 = C[:-2] - O[:-2]
    body1 = _body_abs(O[1:-1], C[1:-1])
    body2 = C[2:] - O[2:]

    cond = (
        (body0 > 0) &
        (body1 < 0.3 * np.abs(body0)) &
        (body2 < 0) &
        (C[2:] < (O[:-2] + C[:-2]) / 2.0)
    )
    result[2:] = np.where(cond, -1.0, 0.0)
    return result

def _three_white_soldiers(O, H, L, C, V):
    """Three White Soldiers: 3 consecutive large bullish bodies."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 3:
        return result
    b0 = C[:-2] - O[:-2]
    b1 = C[1:-1] - O[1:-1]
    b2 = C[2:] - O[2:]
    r0 = _range_(H[:-2], L[:-2])

    cond = (
        (b0 > 0.5 * r0) &
        (b1 > 0) & (b2 > 0) &
        (C[1:-1] > C[:-2]) &
        (C[2:] > C[1:-1])
    )
    result[2:] = np.where(cond, 1.0, 0.0)
    return result

def _three_black_crows(O, H, L, C, V):
    """Three Black Crows: 3 consecutive large bearish bodies."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 3:
        return result
    b0 = C[:-2] - O[:-2]
    b1 = C[1:-1] - O[1:-1]
    b2 = C[2:] - O[2:]
    r0 = _range_(H[:-2], L[:-2])

    cond = (
        (b0 < -0.5 * r0) &
        (b1 < 0) & (b2 < 0) &
        (C[1:-1] < C[:-2]) &
        (C[2:] < C[1:-1])
    )
    result[2:] = np.where(cond, -1.0, 0.0)
    return result

def _marubozu(O, H, L, C, V):
    """Marubozu: very small shadows relative to body."""
    b = _body_abs(O, C)
    us = _upper_shadow(H, O, C)
    ls = _lower_shadow(L, O, C)
    r = _range_(H, L)
    cond = (us < 0.05 * r) & (ls < 0.05 * r) & (r > 0)
    return np.where(cond, np.sign(C - O), 0.0)

def _spinning_top(O, H, L, C, V):
    """Spinning Top: small body, long shadows on both sides."""
    b = _body_abs(O, C)
    us = _upper_shadow(H, O, C)
    ls = _lower_shadow(L, O, C)
    r = _range_(H, L)
    cond = (b < 0.3 * r) & (us > 0.25 * r) & (ls > 0.25 * r) & (r > 0)
    return np.where(cond, 1.0, 0.0)

def _harami(O, H, L, C, V):
    """Harami: current body inside previous body."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    prev_top = np.maximum(O[:-1], C[:-1])
    prev_bot = np.minimum(O[:-1], C[:-1])
    cur_top = np.maximum(O[1:], C[1:])
    cur_bot = np.minimum(O[1:], C[1:])
    prev_body = _body_abs(O[:-1], C[:-1])

    inside = (cur_top < prev_top) & (cur_bot > prev_bot) & (prev_body > 0)
    prev_bearish = C[:-1] < O[:-1]
    bull = inside & prev_bearish
    bear = inside & ~prev_bearish

    result[1:] = np.where(bull, 1.0, np.where(bear, -1.0, 0.0))
    return result

def _piercing_line(O, H, L, C, V):
    """Piercing Line: opens below prior low, closes above prior midpoint."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    prev_bearish = C[:-1] < O[:-1]
    mid_prev = (O[:-1] + C[:-1]) / 2.0
    cond = prev_bearish & (O[1:] < L[:-1]) & (C[1:] > mid_prev) & (C[1:] > O[1:])
    result[1:] = np.where(cond, 1.0, 0.0)
    return result

def _dark_cloud(O, H, L, C, V):
    """Dark Cloud Cover: opens above prior high, closes below prior midpoint."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    prev_bullish = C[:-1] > O[:-1]
    mid_prev = (O[:-1] + C[:-1]) / 2.0
    cond = prev_bullish & (O[1:] > H[:-1]) & (C[1:] < mid_prev) & (C[1:] < O[1:])
    result[1:] = np.where(cond, -1.0, 0.0)
    return result

def _dragonfly_doji(O, H, L, C, V):
    """Dragonfly Doji: doji with long lower shadow."""
    r = _range_(H, L)
    b = _body_abs(O, C)
    ls = _lower_shadow(L, O, C)
    us = _upper_shadow(H, O, C)
    cond = (b < 0.05 * r) & (ls > 0.6 * r) & (us < 0.1 * r) & (r > 0)
    return np.where(cond, 1.0, 0.0)

def _gravestone_doji(O, H, L, C, V):
    """Gravestone Doji: doji with long upper shadow."""
    r = _range_(H, L)
    b = _body_abs(O, C)
    us = _upper_shadow(H, O, C)
    ls = _lower_shadow(L, O, C)
    cond = (b < 0.05 * r) & (us > 0.6 * r) & (ls < 0.1 * r) & (r > 0)
    return np.where(cond, -1.0, 0.0)

def _tweezer_top(O, H, L, C, V):
    """Tweezer Top: two bars with matching highs."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    r = _range_(H, L)
    tol = 0.001 * np.where(r > 0, r, 1.0)
    matching_high = np.abs(H[1:] - H[:-1]) < tol[1:]
    first_bull = C[:-1] > O[:-1]
    second_bear = C[1:] < O[1:]
    cond = matching_high & first_bull & second_bear
    result[1:] = np.where(cond, -1.0, 0.0)
    return result

def _tweezer_bottom(O, H, L, C, V):
    """Tweezer Bottom: two bars with matching lows."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    r = _range_(H, L)
    tol = 0.001 * np.where(r > 0, r, 1.0)
    matching_low = np.abs(L[1:] - L[:-1]) < tol[1:]
    first_bear = C[:-1] < O[:-1]
    second_bull = C[1:] > O[1:]
    cond = matching_low & first_bear & second_bull
    result[1:] = np.where(cond, 1.0, 0.0)
    return result

def _body_size_ratio(O, H, L, C, V):
    """Body as ratio of range (continuous 0-1)."""
    r = _range_(H, L)
    return np.where(r > 0, _body_abs(O, C) / r, 0.0)

def _upper_shadow_ratio(O, H, L, C, V):
    """Upper shadow as ratio of range (continuous 0-1)."""
    r = _range_(H, L)
    return np.where(r > 0, _upper_shadow(H, O, C) / r, 0.0)

def _lower_shadow_ratio(O, H, L, C, V):
    """Lower shadow as ratio of range (continuous 0-1)."""
    r = _range_(H, L)
    return np.where(r > 0, _lower_shadow(L, O, C) / r, 0.0)

def _body_direction(O, H, L, C, V):
    """Body direction: +1 bullish, -1 bearish, 0 doji."""
    return np.sign(C - O)

def _gap_up(O, H, L, C, V):
    """Gap up: open > previous high."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    result[1:] = np.where(O[1:] > H[:-1], 1.0, 0.0)
    return result

def _gap_down(O, H, L, C, V):
    """Gap down: open < previous low."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    result[1:] = np.where(O[1:] < L[:-1], -1.0, 0.0)
    return result

def _inside_bar(O, H, L, C, V):
    """Inside bar: current range entirely within previous range."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    cond = (H[1:] <= H[:-1]) & (L[1:] >= L[:-1])
    result[1:] = np.where(cond, 1.0, 0.0)
    return result

def _outside_bar(O, H, L, C, V):
    """Outside bar: current range engulfs previous range."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    cond = (H[1:] > H[:-1]) & (L[1:] < L[:-1])
    result[1:] = np.where(cond, 1.0, 0.0)
    return result

def _consecutive_bodies(O, H, L, C, V):
    """Count of consecutive same-direction bodies (positive=bullish streak)."""
    direction = np.sign(C - O)
    result = np.zeros_like(C)
    for t in range(1, C.shape[0]):
        same = direction[t] == direction[t - 1]
        result[t] = np.where(same & (direction[t] != 0), result[t - 1] + direction[t], direction[t])
    return result

def _range_expansion(O, H, L, C, V):
    """Range expansion: current range / rolling mean range."""
    r = _range_(H, L)
    mean_r = rolling_mean(r, 20)
    return np.where(mean_r > 0, r / mean_r, 1.0)

def _close_position_in_range(O, H, L, C, V):
    """Where close sits in the bar range (0=low, 1=high)."""
    r = _range_(H, L)
    return np.where(r > 0, (C - L) / r, 0.5)

def _volume_bar_ratio(O, H, L, C, V):
    """Volume relative to rolling 20-bar average."""
    mean_vol = rolling_mean(V, 20)
    return np.where(mean_vol > 0, V / mean_vol, 1.0)

def _three_inside_up(O, H, L, C, V):
    """Three Inside Up: harami + confirmation bar."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 3:
        return result
    # Bar 0 bearish, bar 1 inside & bullish, bar 2 closes above bar 0 open
    b0_bear = C[:-2] < O[:-2]
    b1_inside = (np.maximum(O[1:-1], C[1:-1]) < np.maximum(O[:-2], C[:-2])) & \
                (np.minimum(O[1:-1], C[1:-1]) > np.minimum(O[:-2], C[:-2]))
    b1_bull = C[1:-1] > O[1:-1]
    b2_confirm = C[2:] > O[:-2]
    cond = b0_bear & b1_inside & b1_bull & b2_confirm
    result[2:] = np.where(cond, 1.0, 0.0)
    return result

def _three_inside_down(O, H, L, C, V):
    """Three Inside Down: bearish harami + confirmation."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 3:
        return result
    b0_bull = C[:-2] > O[:-2]
    b1_inside = (np.maximum(O[1:-1], C[1:-1]) < np.maximum(O[:-2], C[:-2])) & \
                (np.minimum(O[1:-1], C[1:-1]) > np.minimum(O[:-2], C[:-2]))
    b1_bear = C[1:-1] < O[1:-1]
    b2_confirm = C[2:] < O[:-2]
    cond = b0_bull & b1_inside & b1_bear & b2_confirm
    result[2:] = np.where(cond, -1.0, 0.0)
    return result

def _kicker_bull(O, H, L, C, V):
    """Bullish Kicker: gap up with body entirely above prior body."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    prev_bear = C[:-1] < O[:-1]
    gap_up = O[1:] > O[:-1]
    bull_body = C[1:] > O[1:]
    cond = prev_bear & gap_up & bull_body
    result[1:] = np.where(cond, 1.0, 0.0)
    return result

def _kicker_bear(O, H, L, C, V):
    """Bearish Kicker: gap down with body entirely below prior body."""
    result = np.zeros_like(C)
    n = C.shape[0]
    if n < 2:
        return result
    prev_bull = C[:-1] > O[:-1]
    gap_down = O[1:] < O[:-1]
    bear_body = C[1:] < O[1:]
    cond = prev_bull & gap_down & bear_body
    result[1:] = np.where(cond, -1.0, 0.0)
    return result

def _belt_hold_bull(O, H, L, C, V):
    """Bullish Belt Hold: opens at low, closes near high."""
    r = _range_(H, L)
    ls = _lower_shadow(L, O, C)
    body = C - O
    cond = (body > 0.6 * r) & (ls < 0.05 * r) & (r > 0)
    return np.where(cond, 1.0, 0.0)

def _belt_hold_bear(O, H, L, C, V):
    """Bearish Belt Hold: opens at high, closes near low."""
    r = _range_(H, L)
    us = _upper_shadow(H, O, C)
    body = O - C
    cond = (body > 0.6 * r) & (us < 0.05 * r) & (r > 0)
    return np.where(cond, -1.0, 0.0)

def _long_legged_doji(O, H, L, C, V):
    """Long-Legged Doji: tiny body with both shadows > 30% of range."""
    r = _range_(H, L)
    b = _body_abs(O, C)
    us = _upper_shadow(H, O, C)
    ls = _lower_shadow(L, O, C)
    cond = (b < 0.1 * r) & (us > 0.3 * r) & (ls > 0.3 * r) & (r > 0)
    return np.where(cond, 1.0, 0.0)

def _high_wave(O, H, L, C, V):
    """High Wave: small body, very long shadows (both > 40% of range)."""
    r = _range_(H, L)
    b = _body_abs(O, C)
    us = _upper_shadow(H, O, C)
    ls = _lower_shadow(L, O, C)
    cond = (b < 0.2 * r) & (us > 0.35 * r) & (ls > 0.35 * r) & (r > 0)
    return np.where(cond, 1.0, 0.0)

def _closing_marubozu(O, H, L, C, V):
    """Closing Marubozu: no shadow on the closing side."""
    r = _range_(H, L)
    bullish = (C > O) & (np.abs(H - C) < 0.01 * r) & (r > 0)
    bearish = (C < O) & (np.abs(L - C) < 0.01 * r) & (r > 0)
    return np.where(bullish, 1.0, np.where(bearish, -1.0, 0.0))

def _opening_marubozu(O, H, L, C, V):
    """Opening Marubozu: no shadow on the opening side."""
    r = _range_(H, L)
    bullish = (C > O) & (np.abs(O - L) < 0.01 * r) & (r > 0)
    bearish = (C < O) & (np.abs(O - H) < 0.01 * r) & (r > 0)
    return np.where(bullish, 1.0, np.where(bearish, -1.0, 0.0))

def _shooting_star(O, H, L, C, V):
    """Shooting Star: long upper shadow, small body near low (bearish)."""
    b = _body_abs(O, C)
    us = _upper_shadow(H, O, C)
    ls = _lower_shadow(L, O, C)
    r = _range_(H, L)
    cond = (us >= 2.0 * b) & (ls < 0.3 * r) & (r > 0) & (C < O)
    return np.where(cond, -1.0, 0.0)

_CANDLE_PATTERNS = {
    "CDOJI": _doji,
    "CHAMMER": _hammer,
    "CINV_HAMMER": _inverted_hammer,
    "CENGULFING": _engulfing,
    "CMORNING_STAR": _morning_star,
    "CEVENING_STAR": _evening_star,
    "C3WHITE": _three_white_soldiers,
    "C3BLACK": _three_black_crows,
    "CMARUBOZU": _marubozu,
    "CSPINNING": _spinning_top,
    "CHARAMI": _harami,
    "CPIERCING": _piercing_line,
    "CDARKCLOUD": _dark_cloud,
    "CDRAGONFLY": _dragonfly_doji,
    "CGRAVESTONE": _gravestone_doji,
    "CTWEEZER_TOP": _tweezer_top,
    "CTWEEZER_BOT": _tweezer_bottom,
    "CBODY_RATIO": _body_size_ratio,
    "CUPPER_SHADOW": _upper_shadow_ratio,
    "CLOWER_SHADOW": _lower_shadow_ratio,
    "CBODY_DIR": _body_direction,
    "CGAP_UP": _gap_up,
    "CGAP_DOWN": _gap_down,
    "CINSIDE": _inside_bar,
    "COUTSIDE": _outside_bar,
    "CSTREAK": _consecutive_bodies,
    "CRANGE_EXP": _range_expansion,
    "CCLOSE_POS": _close_position_in_range,
    "CVOL_BAR": _volume_bar_ratio,
    "C3INSIDE_UP": _three_inside_up,
    "C3INSIDE_DN": _three_inside_down,
    "CKICKER_BULL": _kicker_bull,
    "CKICKER_BEAR": _kicker_bear,
    "CBELT_BULL": _belt_hold_bull,
    "CBELT_BEAR": _belt_hold_bear,
    "CLONGLEG_DOJI": _long_legged_doji,
    "CHIGH_WAVE": _high_wave,
    "CCLOSING_MARU": _closing_marubozu,
    "COPENING_MARU": _opening_marubozu,
    "CSHOOT_STAR": _shooting_star,
}

def _compute_candle(open_, high, low, close, volume, timestamps, context, variant):
    fn = _CANDLE_PATTERNS[variant.name]
    return fn(open_, high, low, close, volume)

_candle_variants = [
    IndicatorVariant(name=name, family="PATTERN_CANDLE", params={})
    for name in _CANDLE_PATTERNS
]

_candle_family = register_family(IndicatorFamily(
    name="PATTERN_CANDLE",
    tier=1,
    warmup=5,
    variants=_candle_variants,
    compute_fn=_compute_candle,
))

PIVOT_TYPES = ["STANDARD", "FIBONACCI", "CAMARILLA"]

def _compute_pivot(open_, high, low, close, volume, timestamps, context, variant):
    """Pivot points computed from prior bar's HLC."""
    ptype = variant.params["type"]
    output = variant.params["output"]

    # Use previous bar's HLC
    prev_h = np.empty_like(high)
    prev_l = np.empty_like(low)
    prev_c = np.empty_like(close)
    prev_h[0] = high[0]
    prev_l[0] = low[0]
    prev_c[0] = close[0]
    prev_h[1:] = high[:-1]
    prev_l[1:] = low[:-1]
    prev_c[1:] = close[:-1]

    pp = (prev_h + prev_l + prev_c) / 3.0
    r = prev_h - prev_l

    if ptype == "STANDARD":
        r1 = 2 * pp - prev_l
        s1 = 2 * pp - prev_h
        r2 = pp + r
        s2 = pp - r
    elif ptype == "FIBONACCI":
        r1 = pp + 0.382 * r
        s1 = pp - 0.382 * r
        r2 = pp + 0.618 * r
        s2 = pp - 0.618 * r
    else:  # CAMARILLA
        r1 = close + 1.1 * r / 12.0
        s1 = close - 1.1 * r / 12.0
        r2 = close + 1.1 * r / 6.0
        s2 = close - 1.1 * r / 6.0

    _PIVOT_OUTPUTS = {
        "PP": pp,
        "R1": r1,
        "S1": s1,
        "R2": r2,
    }
    return _PIVOT_OUTPUTS.get(output, s2)

_pivot_variants = []

_pivot_family = register_family(IndicatorFamily(
    name="PATTERN_PIVOT",
    tier=1,
    warmup=5,
    variants=_pivot_variants,
    compute_fn=_compute_pivot,
))

def _compute_structure(open_, high, low, close, volume, timestamps, context, variant):
    """Structure indicators: fractals, volume profile proxies, etc."""
    stype = variant.params["type"]
    n_bars, n_sym = close.shape

    if stype == "FRACTAL_UP":
        # Williams Fractal Up: high[t] > high[t-2:t] and high[t] > high[t+1:t+3]
        # Since we can't look forward in streaming, use lagged (confirmed after 2 bars)
        result = np.zeros_like(close)
        if n_bars >= 5:
            for t in range(2, n_bars - 2):
                cond = (
                    (high[t] > high[t - 1]) & (high[t] > high[t - 2]) &
                    (high[t] > high[t + 1]) & (high[t] > high[t + 2])
                )
                result[t + 2] = np.where(cond, 1.0, 0.0)  # confirmed 2 bars later
        return result

    elif stype == "FRACTAL_DOWN":
        result = np.zeros_like(close)
        if n_bars >= 5:
            for t in range(2, n_bars - 2):
                cond = (
                    (low[t] < low[t - 1]) & (low[t] < low[t - 2]) &
                    (low[t] < low[t + 1]) & (low[t] < low[t + 2])
                )
                result[t + 2] = np.where(cond, -1.0, 0.0)
        return result

    elif stype == "HH":
        # Higher High: current rolling max > previous rolling max
        hh20 = rolling_max(high, 20)
        prev_hh = np.empty_like(hh20)
        prev_hh[0] = hh20[0]
        prev_hh[1:] = hh20[:-1]
        return np.where(hh20 > prev_hh, 1.0, 0.0)

    elif stype == "LL":
        ll20 = rolling_min(low, 20)
        prev_ll = np.empty_like(ll20)
        prev_ll[0] = ll20[0]
        prev_ll[1:] = ll20[:-1]
        return np.where(ll20 < prev_ll, -1.0, 0.0)

    elif stype == "DIST_FROM_HIGH_20":
        hh = rolling_max(high, 20)
        return np.where(hh > 0, (close - hh) / hh, 0.0)

    elif stype == "DIST_FROM_LOW_20":
        ll = rolling_min(low, 20)
        return np.where(ll > 0, (close - ll) / ll, 0.0)

    elif stype == "DIST_FROM_HIGH_50":
        hh = rolling_max(high, 50)
        return np.where(hh > 0, (close - hh) / hh, 0.0)

    elif stype == "DIST_FROM_LOW_50":
        ll = rolling_min(low, 50)
        return np.where(ll > 0, (close - ll) / ll, 0.0)

    elif stype == "RANGE_POSITION_20":
        hh = rolling_max(high, 20)
        ll = rolling_min(low, 20)
        r = hh - ll
        return np.where(r > 0, (close - ll) / r, 0.5)

    elif stype == "RANGE_POSITION_50":
        hh = rolling_max(high, 50)
        ll = rolling_min(low, 50)
        r = hh - ll
        return np.where(r > 0, (close - ll) / r, 0.5)

    elif stype == "CONSOLIDATION_20":
        # Range as % of price — narrow = consolidation
        hh = rolling_max(high, 20)
        ll = rolling_min(low, 20)
        mid = (hh + ll) / 2.0
        return np.where(mid > 0, (hh - ll) / mid, 0.0)

    elif stype == "CONSOLIDATION_50":
        hh = rolling_max(high, 50)
        ll = rolling_min(low, 50)
        mid = (hh + ll) / 2.0
        return np.where(mid > 0, (hh - ll) / mid, 0.0)

    elif stype == "VOL_PROFILE_SKEW":
        # Volume concentration: high-vol bars near top vs bottom of range
        hh = rolling_max(high, 20)
        ll = rolling_min(low, 20)
        r = hh - ll
        pos = np.where(r > 0, (close - ll) / r, 0.5)
        # Weighted by volume — high-volume bars at top (>0.5) = bullish
        vol_weight = volume / np.maximum(rolling_mean(volume, 20), 1e-10)
        return rolling_mean(pos * vol_weight, 20) - 0.5

    elif stype == "NEW_HIGH_20":
        # Binary: is this a new 20-bar high?
        hh = rolling_max(high, 20)
        return np.where(high >= hh, 1.0, 0.0)

    return np.full_like(close, np.nan)

_struct_types = [
    "FRACTAL_UP", "FRACTAL_DOWN", "HH", "LL",
    "DIST_FROM_HIGH_20", "DIST_FROM_LOW_20",
    "DIST_FROM_HIGH_50", "DIST_FROM_LOW_50",
    "RANGE_POSITION_20", "RANGE_POSITION_50",
    "CONSOLIDATION_20", "CONSOLIDATION_50",
    "VOL_PROFILE_SKEW", "NEW_HIGH_20",
]

_struct_variants = [
    IndicatorVariant(name=f"STRUCT_{st}", family="STRUCTURE", params={"type": st})
    for st in _struct_types
]

_struct_family = register_family(IndicatorFamily(
    name="STRUCTURE",
    tier=1,
    warmup=200,
    variants=_struct_variants,
    compute_fn=_compute_structure,
))

