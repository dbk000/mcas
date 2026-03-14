"""
EXTRACTED PURE LOGIC FROM: SCR_IND_FAM_oscillators.py
V5 Updates: Full Quarantine enforced.
have been physically replaced to satisfy the zero-contamination mandate.
Dataclass imports are explicitly injected to guarantee semantics.
"""

from dataclasses import dataclass, field, asdict

import numpy as np

from ..rolling_utils import (
    rolling_wilder,
    rolling_ema,
    rolling_mean,
    rolling_sum,
    rolling_max,
    rolling_min,
    typical_price,
)

from . import IndicatorFamily, IndicatorVariant, register_family

RSI_PERIODS = [6, 9, 14, 21, 25, 50]

def _compute_rsi(open_, high, low, close, volume, timestamps, context, variant):
    """RSI via Wilder smoothing of gains/losses."""
    p = variant.params["period"]
    diff = np.empty_like(close)
    diff[0] = 0.0
    diff[1:] = close[1:] - close[:-1]

    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)

    avg_gain = rolling_wilder(gains, p)
    avg_loss = rolling_wilder(losses, p)

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
        rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi[:p] = np.nan
    return rsi

_rsi_family = register_family(IndicatorFamily(
    name="OSC_RSI",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(
            name=f"RSI_{p}",
            family="OSC_RSI",
            params={"period": p},
            retain_in_context=(p == 14),
        )
        for p in RSI_PERIODS
    ],
    compute_fn=_compute_rsi,
))

STOCH_PARAMS = [
    (5, 3),    # fast
    (14, 3),   # standard
    (21, 5),   # slow
]

def _compute_stoch(open_, high, low, close, volume, timestamps, context, variant):
    """Stochastic: %K = (C - LL) / (HH - LL) * 100. Returns %K or %D."""
    k_period = variant.params["k_period"]
    d_period = variant.params["d_period"]
    output = variant.params["output"]

    hh = rolling_max(high, k_period)
    ll = rolling_min(low, k_period)

    denom = hh - ll
    pct_k = np.where(denom > 0, (close - ll) / denom * 100.0, 50.0)
    pct_k[:k_period - 1] = np.nan

    if output == "K":
        return pct_k
    else:
        return rolling_mean(pct_k, d_period)

_stoch_variants = []

_stoch_family = register_family(IndicatorFamily(
    name="OSC_STOCH",
    tier=1,
    warmup=200,
    variants=_stoch_variants,
    compute_fn=_compute_stoch,
))

CCI_PERIODS = [9, 14, 20, 40, 50, 100]

def _compute_cci(open_, high, low, close, volume, timestamps, context, variant):
    """CCI = (TP - SMA(TP)) / (0.015 * MAD(TP))."""
    p = variant.params["period"]
    tp = typical_price(high, low, close)
    sma_tp = rolling_mean(tp, p)
    # Mean Absolute Deviation via rolling
    deviation = tp - sma_tp
    mad = rolling_mean(np.abs(deviation), p)

    with np.errstate(divide="ignore", invalid="ignore"):
        cci = np.where(mad > 0, deviation / (0.015 * mad), 0.0)
    cci[:p - 1] = np.nan
    return cci

_cci_family = register_family(IndicatorFamily(
    name="OSC_CCI",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"CCI_{p}", family="OSC_CCI", params={"period": p})
        for p in CCI_PERIODS
    ],
    compute_fn=_compute_cci,
))

WILLIAMS_PERIODS = [5, 10, 14, 20, 28, 50]

def _compute_williams(open_, high, low, close, volume, timestamps, context, variant):
    """Williams %R = (HH - C) / (HH - LL) * -100."""
    p = variant.params["period"]
    hh = rolling_max(high, p)
    ll = rolling_min(low, p)
    denom = hh - ll
    wr = np.where(denom > 0, (hh - close) / denom * -100.0, -50.0)
    wr[:p - 1] = np.nan
    return wr

_williams_family = register_family(IndicatorFamily(
    name="OSC_WILLIAMS",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"WILLR_{p}", family="OSC_WILLIAMS", params={"period": p})
        for p in WILLIAMS_PERIODS
    ],
    compute_fn=_compute_williams,
))

MFI_PERIODS = [7, 10, 14, 20, 28, 50]

def _compute_mfi(open_, high, low, close, volume, timestamps, context, variant):
    """MFI: volume-weighted RSI using typical price."""
    p = variant.params["period"]
    tp = typical_price(high, low, close)
    raw_mf = tp * volume

    tp_diff = np.empty_like(tp)
    tp_diff[0] = 0.0
    tp_diff[1:] = tp[1:] - tp[:-1]

    pos_mf = np.where(tp_diff > 0, raw_mf, 0.0)
    neg_mf = np.where(tp_diff < 0, raw_mf, 0.0)

    sum_pos = rolling_sum(pos_mf, p)
    sum_neg = rolling_sum(neg_mf, p)

    with np.errstate(divide="ignore", invalid="ignore"):
        mr = np.where(sum_neg > 0, sum_pos / sum_neg, 100.0)
        mfi = 100.0 - 100.0 / (1.0 + mr)
    mfi[:p] = np.nan
    return mfi

_mfi_family = register_family(IndicatorFamily(
    name="OSC_MFI",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"MFI_{p}", family="OSC_MFI", params={"period": p})
        for p in MFI_PERIODS
    ],
    compute_fn=_compute_mfi,
))

CMO_PERIODS = [5, 9, 14, 20, 25, 50]

def _compute_cmo(open_, high, low, close, volume, timestamps, context, variant):
    """CMO = (sum_gains - sum_losses) / (sum_gains + sum_losses) * 100."""
    p = variant.params["period"]
    diff = np.empty_like(close)
    diff[0] = 0.0
    diff[1:] = close[1:] - close[:-1]

    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)

    sg = rolling_sum(gains, p)
    sl = rolling_sum(losses, p)
    total = sg + sl

    cmo = np.where(total > 0, (sg - sl) / total * 100.0, 0.0)
    cmo[:p] = np.nan
    return cmo

_cmo_family = register_family(IndicatorFamily(
    name="OSC_CMO",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"CMO_{p}", family="OSC_CMO", params={"period": p})
        for p in CMO_PERIODS
    ],
    compute_fn=_compute_cmo,
))

UO_PARAMS = [
    (7, 14, 28),     # standard
    (5, 10, 20),     # fast
    (10, 20, 40),    # slow
    (14, 28, 56),    # extended
    (3, 7, 14),      # ultra-fast
    (21, 42, 84),    # very slow
]

def _compute_uo(open_, high, low, close, volume, timestamps, context, variant):
    """Ultimate Oscillator: weighted average of 3 BP/TR ratios."""
    p1 = variant.params["p1"]
    p2 = variant.params["p2"]
    p3 = variant.params["p3"]

    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]

    # Buying Pressure = Close - min(Low, PrevClose)
    bp = close - np.minimum(low, prev_close)

    # True Range
    tr = np.maximum(high, prev_close) - np.minimum(low, prev_close)

    sum_bp1 = rolling_sum(bp, p1)
    sum_tr1 = rolling_sum(tr, p1)
    sum_bp2 = rolling_sum(bp, p2)
    sum_tr2 = rolling_sum(tr, p2)
    sum_bp3 = rolling_sum(bp, p3)
    sum_tr3 = rolling_sum(tr, p3)

    avg1 = np.where(sum_tr1 > 0, sum_bp1 / sum_tr1, 0.5)
    avg2 = np.where(sum_tr2 > 0, sum_bp2 / sum_tr2, 0.5)
    avg3 = np.where(sum_tr3 > 0, sum_bp3 / sum_tr3, 0.5)

    uo = 100.0 * (4 * avg1 + 2 * avg2 + avg3) / 7.0
    uo[:p3] = np.nan
    return uo

_uo_variants = [
    IndicatorVariant(
        name=f"UO_{p1}_{p2}_{p3}", family="OSC_UO",
        params={"p1": p1, "p2": p2, "p3": p3},
    )
    for p1, p2, p3 in UO_PARAMS
]

_uo_family = register_family(IndicatorFamily(
    name="OSC_UO",
    tier=1,
    warmup=200,
    variants=_uo_variants,
    compute_fn=_compute_uo,
))

ROC_PERIODS = [1, 3, 5, 9, 12, 14, 20, 50]

def _compute_roc(open_, high, low, close, volume, timestamps, context, variant):
    """ROC = (close / close[t-n] - 1) * 100."""
    p = variant.params["period"]
    n_bars = close.shape[0]
    out = np.full_like(close, np.nan)
    if n_bars <= p:
        return out
    out[p:] = (close[p:] / close[:-p] - 1.0) * 100.0
    return out

_roc_family = register_family(IndicatorFamily(
    name="OSC_ROC",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"ROC_{p}", family="OSC_ROC", params={"period": p})
        for p in ROC_PERIODS
    ],
    compute_fn=_compute_roc,
))

MOM_PERIODS = [1, 3, 5, 10, 14, 20, 30, 50]

def _compute_momentum(open_, high, low, close, volume, timestamps, context, variant):
    """Momentum = close - close[t-n]."""
    p = variant.params["period"]
    n_bars = close.shape[0]
    out = np.full_like(close, np.nan)
    if n_bars <= p:
        return out
    out[p:] = close[p:] - close[:-p]
    return out

_mom_family = register_family(IndicatorFamily(
    name="OSC_MOMENTUM",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"MOM_{p}", family="OSC_MOMENTUM", params={"period": p})
        for p in MOM_PERIODS
    ],
    compute_fn=_compute_momentum,
))

