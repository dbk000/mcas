"""
Regime labellers (plan §7.2). Each scheme returns per-bar integer bucket labels.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def volatility_regime(close: np.ndarray, atr_arr: np.ndarray) -> np.ndarray:
    """ATR/close quintiles (5 buckets: VL=0, L=1, M=2, H=3, VH=4). Cross-sectional within the symbol's own history."""
    out = np.full(close.shape, -1, dtype=np.int8)
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_pct = atr_arr / close
    valid = ~np.isnan(atr_pct)
    if valid.sum() < 5:
        return out
    qs = np.nanquantile(atr_pct[valid], [0.2, 0.4, 0.6, 0.8])
    for i, v in enumerate(atr_pct):
        if np.isnan(v):
            continue
        if v < qs[0]:
            out[i] = 0
        elif v < qs[1]:
            out[i] = 1
        elif v < qs[2]:
            out[i] = 2
        elif v < qs[3]:
            out[i] = 3
        else:
            out[i] = 4
    return out


def trend_strength_regime(adx_arr: np.ndarray) -> np.ndarray:
    """ADX buckets: 0-15 → 0, 15-25 → 1, 25-40 → 2, 40+ → 3."""
    out = np.full(adx_arr.shape, -1, dtype=np.int8)
    mask = ~np.isnan(adx_arr)
    out[mask & (adx_arr < 15)] = 0
    out[mask & (adx_arr >= 15) & (adx_arr < 25)] = 1
    out[mask & (adx_arr >= 25) & (adx_arr < 40)] = 2
    out[mask & (adx_arr >= 40)] = 3
    return out


def breadth_regime(breadth_pct: np.ndarray) -> np.ndarray:
    """Breadth buckets: <30% → 0, 30-50 → 1, 50-70 → 2, >70 → 3. Input is % of universe in uptrend."""
    out = np.full(breadth_pct.shape, -1, dtype=np.int8)
    mask = ~np.isnan(breadth_pct)
    out[mask & (breadth_pct < 0.30)] = 0
    out[mask & (breadth_pct >= 0.30) & (breadth_pct < 0.50)] = 1
    out[mask & (breadth_pct >= 0.50) & (breadth_pct < 0.70)] = 2
    out[mask & (breadth_pct >= 0.70)] = 3
    return out


def btc_reference_regime(btc_sma50_slope_in_atr: np.ndarray) -> np.ndarray:
    """BTC SMA50 slope in ATR units: <-1 → 0 strong_down, [-1,-0.3) → 1 down, [-0.3,0.3] → 2 neutral, (0.3,1] → 3 up, >1 → 4 strong_up."""
    out = np.full(btc_sma50_slope_in_atr.shape, -1, dtype=np.int8)
    mask = ~np.isnan(btc_sma50_slope_in_atr)
    v = btc_sma50_slope_in_atr
    out[mask & (v < -1.0)] = 0
    out[mask & (v >= -1.0) & (v < -0.3)] = 1
    out[mask & (v >= -0.3) & (v <= 0.3)] = 2
    out[mask & (v > 0.3) & (v <= 1.0)] = 3
    out[mask & (v > 1.0)] = 4
    return out


def compute_breadth_pct(all_frames: dict[str, pd.DataFrame], timeframe: str, ema_fast_col: str = "ema20", ema_slow_col: str = "ema50") -> pd.Series:
    """
    Cross-sectional breadth: share of universe where ema20 > ema50 at each bar_start_ts.
    Expects each frame to already have indicator columns attached.
    Returns a Series indexed by bar_start_ts.
    """
    per_sym: list[pd.Series] = []
    for sym, df in all_frames.items():
        if ema_fast_col in df.columns and ema_slow_col in df.columns:
            s = (df[ema_fast_col] > df[ema_slow_col]).astype(np.float32)
            s.index = df["bar_start_ts"]
            s.name = sym
            per_sym.append(s)
    if not per_sym:
        return pd.Series(dtype=np.float32)
    stacked = pd.concat(per_sym, axis=1)
    return stacked.mean(axis=1, skipna=True)
