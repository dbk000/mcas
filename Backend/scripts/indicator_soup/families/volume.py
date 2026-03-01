"""
Volume families: OBV, AD, CMF, Force Index, EMV, VWAP (rolling 24h).

~22 variants. No external dependencies (Tier 1).
"""

import numpy as np

from ..rolling_utils import (
    rolling_sum,
    rolling_mean,
    rolling_ema,
    typical_price,
)
from . import IndicatorFamily, IndicatorVariant, register_family


# ---------------------------------------------------------------------------
# OBV — On Balance Volume (1 raw + 3 smoothed = 4 variants)
# ---------------------------------------------------------------------------


def _compute_obv(open_, high, low, close, volume, timestamps, context, variant):
    """OBV: cumulative volume flow based on close direction."""
    output = variant.params["output"]

    direction = np.sign(np.diff(close, axis=0, prepend=close[:1]))
    signed_vol = direction * volume
    obv = np.cumsum(signed_vol, axis=0)

    if output == "RAW":
        return obv
    else:
        p = variant.params["period"]
        return rolling_ema(obv, p)


_obv_variants = [
    IndicatorVariant(name="OBV_RAW", family="VOL_OBV", params={"output": "RAW"}),
    IndicatorVariant(name="OBV_EMA_10", family="VOL_OBV", params={"output": "EMA", "period": 10}),
    IndicatorVariant(name="OBV_EMA_20", family="VOL_OBV", params={"output": "EMA", "period": 20}),
    IndicatorVariant(name="OBV_EMA_50", family="VOL_OBV", params={"output": "EMA", "period": 50}),
]

_obv_family = register_family(IndicatorFamily(
    name="VOLUME_OBV",
    tier=1,
    warmup=200,
    variants=_obv_variants,
    compute_fn=_compute_obv,
))


# ---------------------------------------------------------------------------
# AD — Accumulation/Distribution Line (1 raw + 2 smoothed = 3 variants)
# ---------------------------------------------------------------------------


def _compute_ad(open_, high, low, close, volume, timestamps, context, variant):
    """A/D Line: CLV * volume, cumulative."""
    output = variant.params["output"]

    hl_range = high - low
    clv = np.where(hl_range > 0, ((close - low) - (high - close)) / hl_range, 0.0)
    mf_volume = clv * volume
    ad = np.cumsum(mf_volume, axis=0)

    if output == "RAW":
        return ad
    else:
        p = variant.params["period"]
        return rolling_ema(ad, p)


_ad_variants = [
    IndicatorVariant(name="AD_RAW", family="VOLUME_AD", params={"output": "RAW"}),
    IndicatorVariant(name="AD_EMA_10", family="VOLUME_AD", params={"output": "EMA", "period": 10}),
    IndicatorVariant(name="AD_EMA_20", family="VOLUME_AD", params={"output": "EMA", "period": 20}),
    IndicatorVariant(name="AD_EMA_50", family="VOLUME_AD", params={"output": "EMA", "period": 50}),
]

_ad_family = register_family(IndicatorFamily(
    name="VOLUME_AD",
    tier=1,
    warmup=200,
    variants=_ad_variants,
    compute_fn=_compute_ad,
))


# ---------------------------------------------------------------------------
# CMF — Chaikin Money Flow (3 variants)
# ---------------------------------------------------------------------------

CMF_PERIODS = [10, 14, 20, 50]


def _compute_cmf(open_, high, low, close, volume, timestamps, context, variant):
    """CMF = sum(CLV * volume, n) / sum(volume, n)."""
    p = variant.params["period"]

    hl_range = high - low
    clv = np.where(hl_range > 0, ((close - low) - (high - close)) / hl_range, 0.0)
    mf_volume = clv * volume

    sum_mfv = rolling_sum(mf_volume, p)
    sum_vol = rolling_sum(volume, p)

    return np.where(sum_vol > 0, sum_mfv / sum_vol, 0.0)


_cmf_family = register_family(IndicatorFamily(
    name="VOLUME_CMF",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"CMF_{p}", family="VOLUME_CMF", params={"period": p})
        for p in CMF_PERIODS
    ],
    compute_fn=_compute_cmf,
))


# ---------------------------------------------------------------------------
# Force Index (3 variants)
# ---------------------------------------------------------------------------

FORCE_PERIODS = [2, 13, 26, 50]


def _compute_force(open_, high, low, close, volume, timestamps, context, variant):
    """Force Index = EMA(close_change * volume, n)."""
    p = variant.params["period"]

    change = np.diff(close, axis=0, prepend=close[:1])
    raw_force = change * volume

    return rolling_ema(raw_force, p)


_force_family = register_family(IndicatorFamily(
    name="VOLUME_FORCE",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"FORCE_{p}", family="VOLUME_FORCE", params={"period": p})
        for p in FORCE_PERIODS
    ],
    compute_fn=_compute_force,
))


# ---------------------------------------------------------------------------
# EMV — Ease of Movement (3 variants)
# ---------------------------------------------------------------------------

EMV_PERIODS = [10, 14, 20, 50]


def _compute_emv(open_, high, low, close, volume, timestamps, context, variant):
    """EMV = (midpoint_move / box_ratio) smoothed."""
    p = variant.params["period"]

    mid = (high + low) / 2.0
    mid_move = np.diff(mid, axis=0, prepend=mid[:1])

    hl_range = high - low
    # Box ratio: volume / range (scaled down)
    box_ratio = np.where(hl_range > 0, volume / hl_range, 0.0)

    raw_emv = np.where(box_ratio > 0, mid_move / box_ratio, 0.0)

    return rolling_mean(raw_emv, p)


_emv_family = register_family(IndicatorFamily(
    name="VOLUME_EMV",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"EMV_{p}", family="VOLUME_EMV", params={"period": p})
        for p in EMV_PERIODS
    ],
    compute_fn=_compute_emv,
))


# ---------------------------------------------------------------------------
# VWAP — Rolling 24h Volume Weighted Average Price (4 variants)
# No session reset — crypto is 24/7. Period = 24h in bar count.
# ---------------------------------------------------------------------------

# Periods are specified in bars — the screening script sets these
# based on timeframe (5m→288, 15m→96, 1h→24, 4h→6).
VWAP_PERIODS = [24, 96, 288, 480]  # Default for 1h; overridden per TF


def _compute_vwap(open_, high, low, close, volume, timestamps, context, variant):
    """Rolling VWAP = rolling_sum(TP * volume, n) / rolling_sum(volume, n)."""
    p = variant.params["period"]

    tp = typical_price(high, low, close)
    tp_vol = tp * volume

    sum_tpv = rolling_sum(tp_vol, p)
    sum_vol = rolling_sum(volume, p)

    vwap = np.where(sum_vol > 0, sum_tpv / sum_vol, np.nan)
    return vwap


_vwap_family = register_family(IndicatorFamily(
    name="VOLUME_VWAP",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"VWAP_{p}", family="VOLUME_VWAP", params={"period": p})
        for p in VWAP_PERIODS
    ],
    compute_fn=_compute_vwap,
))
