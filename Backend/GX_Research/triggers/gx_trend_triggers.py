"""
Trigger families GX1-GX5. Each signal function accepts an IndicatorCache so
indicators are computed once per (symbol, timeframe) and shared across all
trigger combos.

Plan §4. Parameter grids here match the plan:
  GX1 SMA cross       → 12 combos
  GX2 MACD zero-cross → up to 54 combos
  GX3 ADX breakout    → 36 combos
  GX4 Golden cross prox → 16 combos
  GX5 Geometry stack  → 60 combos
"""
from __future__ import annotations

from typing import Iterator

import numpy as np

from ..lib import indicators as ind
from ..lib.indicators import IndicatorCache


# ---------- GX1 SMA cross ----------
def gx1_combos() -> Iterator[dict]:
    for x, y in [(10, 50), (10, 100), (10, 200), (20, 50), (20, 100), (50, 200)]:
        for trend_align in (True, False):
            yield {"family": "GX1", "x": x, "y": y, "require_trend_aligned": trend_align}


def gx1_signals(combo: dict, ic: IndicatorCache) -> tuple[np.ndarray, np.ndarray]:
    close = ic.close
    fast = ic.sma(combo["x"])
    slow = ic.sma(combo["y"])
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    prev_fast = np.roll(fast, 1); prev_fast[0] = fast[0]
    cross_up = (close > fast) & (prev_close <= prev_fast)
    cross_dn = (close < fast) & (prev_close >= prev_fast)
    if combo["require_trend_aligned"]:
        long_e = cross_up & (fast > slow)
        short_e = cross_dn & (fast < slow)
    else:
        long_e = cross_up
        short_e = cross_dn
    long_e = np.where(np.isnan(fast) | np.isnan(slow), False, long_e)
    short_e = np.where(np.isnan(fast) | np.isnan(slow), False, short_e)
    return long_e.astype(bool), short_e.astype(bool)


# ---------- GX2 MACD histogram zero-cross ----------
def gx2_combos() -> Iterator[dict]:
    for fast in (8, 12, 16):
        for slow in (21, 26, 34):
            if fast >= slow:
                continue
            for signal in (7, 9, 12):
                for filt in (True, False):
                    yield {"family": "GX2", "fast": fast, "slow": slow, "signal": signal, "require_close_above_ema50": filt}


def gx2_signals(combo: dict, ic: IndicatorCache) -> tuple[np.ndarray, np.ndarray]:
    _line, _sig, hist = ic.macd(combo["fast"], combo["slow"], combo["signal"])
    prev_hist = np.roll(hist, 1); prev_hist[0] = np.nan
    zero_up = (hist > 0) & (prev_hist <= 0)
    zero_dn = (hist < 0) & (prev_hist >= 0)
    if combo["require_close_above_ema50"]:
        e50 = ic.ema(50)
        long_e = zero_up & (ic.close > e50)
        short_e = zero_dn & (ic.close < e50)
    else:
        long_e = zero_up
        short_e = zero_dn
    long_e = np.where(np.isnan(hist), False, long_e).astype(bool)
    short_e = np.where(np.isnan(hist), False, short_e).astype(bool)
    return long_e, short_e


# ---------- GX3 ADX breakout ----------
def gx3_combos() -> Iterator[dict]:
    for adx_th in (15, 20, 25, 30):
        for ef, es in ((10, 20), (20, 50), (50, 100)):
            for lb in (10, 20, 50):
                yield {"family": "GX3", "adx_th": adx_th, "ef": ef, "es": es, "breakout_bars": lb}


def gx3_signals(combo: dict, ic: IndicatorCache) -> tuple[np.ndarray, np.ndarray]:
    close = ic.close
    high = ic.high
    low = ic.low
    adx_arr = ic.adx(14)
    ef = ic.ema(combo["ef"])
    es = ic.ema(combo["es"])
    lb = combo["breakout_bars"]
    # Vectorised rolling hh/ll via pandas (fast, in C).
    import pandas as pd
    hh = pd.Series(high).rolling(lb, min_periods=lb).max().shift(1).to_numpy()
    ll = pd.Series(low).rolling(lb, min_periods=lb).min().shift(1).to_numpy()
    adx_ok = ~np.isnan(adx_arr) & (adx_arr >= combo["adx_th"])
    long_e = adx_ok & (close > hh) & (ef > es) & ~np.isnan(hh)
    short_e = adx_ok & (close < ll) & (ef < es) & ~np.isnan(ll)
    return long_e.astype(bool), short_e.astype(bool)


# ---------- GX4 Golden cross proximity ----------
def gx4_combos() -> Iterator[dict]:
    for fast, slow in ((20, 50), (50, 100), (50, 200), (20, 100)):
        for n_bars in (3, 8, 24, 48):
            yield {"family": "GX4", "fast": fast, "slow": slow, "within_bars": n_bars}


def gx4_signals(combo: dict, ic: IndicatorCache) -> tuple[np.ndarray, np.ndarray]:
    close = ic.close
    f = ic.sma(combo["fast"])
    s = ic.sma(combo["slow"])
    n = close.size
    prev_f = np.roll(f, 1); prev_f[0] = np.nan
    prev_s = np.roll(s, 1); prev_s[0] = np.nan
    cross_up = (f > s) & (prev_f <= prev_s)
    cross_dn = (f < s) & (prev_f >= prev_s)
    long_e = np.zeros(n, dtype=bool)
    short_e = np.zeros(n, dtype=bool)
    nb = combo["within_bars"]
    up_idx = np.where(cross_up)[0]
    dn_idx = np.where(cross_dn)[0]
    # For each cross, mark bars [x, x+nb] as eligible. Use ufunc for speed: build a mask.
    for x in up_idx:
        end = min(n, x + nb + 1)
        long_e[x:end] = True
    for x in dn_idx:
        end = min(n, x + nb + 1)
        short_e[x:end] = True
    long_e &= (close > s) & ~np.isnan(s)
    short_e &= (close < s) & ~np.isnan(s)
    return long_e, short_e


# ---------- GX5 Geometry stack ----------
def gx5_combos() -> Iterator[dict]:
    stacks = ((9, 20, 50), (12, 26, 50), (10, 30, 100))
    rsi_bands = []
    for lo in (40, 45, 50, 55):
        for hi in (65, 70, 75, 80):
            if lo < hi:
                rsi_bands.append((lo, hi))
    for stack in stacks:
        for lo, hi in rsi_bands:
            for macd_pos in (True, False):
                yield {"family": "GX5", "stack": stack, "rsi_lo": lo, "rsi_hi": hi, "require_macd_hist_positive": macd_pos}


def gx5_signals(combo: dict, ic: IndicatorCache) -> tuple[np.ndarray, np.ndarray]:
    a, b, c = combo["stack"]
    ea = ic.ema(a)
    eb = ic.ema(b)
    ec = ic.ema(c)
    rsi_arr = ic.rsi(14)
    _line, _sig, hist = ic.macd(12, 26, 9)
    close = ic.close
    stack_up = (ea > eb) & (eb > ec)
    stack_dn = (ea < eb) & (eb < ec)
    rsi_in_band = (rsi_arr >= combo["rsi_lo"]) & (rsi_arr <= combo["rsi_hi"])
    rsi_in_band_short = (rsi_arr >= (100 - combo["rsi_hi"])) & (rsi_arr <= (100 - combo["rsi_lo"]))
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    price_up = close > prev_close
    price_dn = close < prev_close
    long_e = stack_up & rsi_in_band & price_up
    short_e = stack_dn & rsi_in_band_short & price_dn
    if combo["require_macd_hist_positive"]:
        long_e &= hist > 0
        short_e &= hist < 0
    long_e = np.where(np.isnan(ea) | np.isnan(eb) | np.isnan(ec) | np.isnan(rsi_arr), False, long_e)
    short_e = np.where(np.isnan(ea) | np.isnan(eb) | np.isnan(ec) | np.isnan(rsi_arr), False, short_e)
    return long_e.astype(bool), short_e.astype(bool)


# ---------- Registry ----------
SIGNAL_FNS = {
    "GX1": gx1_signals,
    "GX2": gx2_signals,
    "GX3": gx3_signals,
    "GX4": gx4_signals,
    "GX5": gx5_signals,
}

COMBO_FNS = {
    "GX1": gx1_combos,
    "GX2": gx2_combos,
    "GX3": gx3_combos,
    "GX4": gx4_combos,
    "GX5": gx5_combos,
}


def all_combos():
    for fam, fn in COMBO_FNS.items():
        for combo in fn():
            yield fam, combo
