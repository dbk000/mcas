"""
Volatility families: ATR/NATR, Bollinger Bands, Keltner Channels, Donchian,
standard deviation, and advanced estimators (Parkinson, Garman-Klass, Yang-Zhang).

~52 variants. ATR_14 and ATR_10 retained in context for downstream deps.
"""

import numpy as np

from ..rolling_utils import (
    true_range,
    rolling_wilder,
    rolling_mean,
    rolling_std,
    rolling_max,
    rolling_min,
    rolling_ema,
    rolling_sum,
)
from . import IndicatorFamily, IndicatorVariant, register_family


# ---------------------------------------------------------------------------
# ATR / NATR (10 variants: 5 periods x 2 outputs)
# ---------------------------------------------------------------------------

ATR_PERIODS = [5, 7, 10, 14, 20]


def _compute_atr(open_, high, low, close, volume, timestamps, context, variant):
    """ATR via Wilder smoothing of True Range. NATR = ATR/close * 100."""
    p = variant.params["period"]
    output = variant.params["output"]

    tr = true_range(high, low, close)
    atr = rolling_wilder(tr, p)

    if output == "ATR":
        return atr
    else:
        return np.where(close > 0, atr / close * 100.0, np.nan)


_atr_variants = []
for p in ATR_PERIODS:
    _atr_variants.append(IndicatorVariant(
        name=f"ATR_{p}", family="VOL_ATR",
        params={"period": p, "output": "ATR"},
        retain_in_context=(p in (10, 14)),
    ))
    _atr_variants.append(IndicatorVariant(
        name=f"NATR_{p}", family="VOL_ATR",
        params={"period": p, "output": "NATR"},
    ))

_atr_family = register_family(IndicatorFamily(
    name="VOL_ATR",
    tier=1,
    warmup=200,
    variants=_atr_variants,
    compute_fn=_compute_atr,
))


# ---------------------------------------------------------------------------
# Bollinger Bands (12 variants: 4 configs x 3 outputs)
# ---------------------------------------------------------------------------

BB_CONFIGS = [
    (10, 1.5),
    (20, 2.0),    # standard
    (20, 2.5),
    (50, 2.0),
]


def _compute_bb(open_, high, low, close, volume, timestamps, context, variant):
    """Bollinger Bands. Outputs: upper, lower, width, %B."""
    p = variant.params["period"]
    k = variant.params["k"]
    output = variant.params["output"]

    # Use context SMA if available
    ctx_key = f"SMA_{p}"
    if ctx_key in context:
        sma = context[ctx_key]
    else:
        sma = rolling_mean(close, p)

    std = rolling_std(close, p)

    if output == "UPPER":
        return sma + k * std
    elif output == "LOWER":
        return sma - k * std
    elif output == "WIDTH":
        return np.where(sma > 0, (2 * k * std) / sma, np.nan)
    else:  # %B
        band_range = 2 * k * std
        return np.where(band_range > 0, (close - (sma - k * std)) / band_range, 0.5)


_bb_variants = []
for p, k in BB_CONFIGS:
    k_str = str(k).replace(".", "p")
    for output in ["UPPER", "LOWER", "WIDTH"]:
        _bb_variants.append(IndicatorVariant(
            name=f"BB_{output}_{p}_{k_str}",
            family="VOL_BB",
            params={"period": p, "k": k, "output": output},
        ))

_bb_family = register_family(IndicatorFamily(
    name="VOL_BB",
    tier=2,
    dependencies=["MA_SMA"],
    warmup=200,
    variants=_bb_variants,
    compute_fn=_compute_bb,
))


# ---------------------------------------------------------------------------
# Keltner Channels (6 variants: 2 configs x 3 outputs)
# ---------------------------------------------------------------------------

KC_CONFIGS = [
    (20, 1.5),
    (20, 2.0),
]


def _compute_kc(open_, high, low, close, volume, timestamps, context, variant):
    """Keltner Channels: EMA ± mult * ATR."""
    p = variant.params["period"]
    mult = variant.params["mult"]
    output = variant.params["output"]

    ema_key = f"EMA_{p}"
    ema = context.get(ema_key, rolling_ema(close, p))

    atr_key = "ATR_14"  # KC traditionally uses ATR(14)
    atr = context.get(atr_key)
    if atr is None:
        atr = rolling_wilder(true_range(high, low, close), 14)

    if output == "UPPER":
        return ema + mult * atr
    elif output == "LOWER":
        return ema - mult * atr
    else:  # WIDTH
        return np.where(ema > 0, (2 * mult * atr) / ema, np.nan)


_kc_variants = []
for p, mult in KC_CONFIGS:
    m_str = str(mult).replace(".", "p")
    for output in ["UPPER", "LOWER", "WIDTH"]:
        _kc_variants.append(IndicatorVariant(
            name=f"KC_{output}_{p}_{m_str}",
            family="VOL_KC",
            params={"period": p, "mult": mult, "output": output},
        ))

_kc_family = register_family(IndicatorFamily(
    name="VOL_KC",
    tier=2,
    dependencies=["MA_EMA", "VOL_ATR"],
    warmup=200,
    variants=_kc_variants,
    compute_fn=_compute_kc,
))


# ---------------------------------------------------------------------------
# Donchian Channels (6 variants: 2 periods x 3 outputs)
# ---------------------------------------------------------------------------

DONCHIAN_PERIODS = [20, 50, 100]


def _compute_donchian(open_, high, low, close, volume, timestamps, context, variant):
    """Donchian: highest high / lowest low over n bars."""
    p = variant.params["period"]
    output = variant.params["output"]

    hh = rolling_max(high, p)
    ll = rolling_min(low, p)

    if output == "UPPER":
        return hh
    elif output == "LOWER":
        return ll
    else:  # WIDTH (normalized)
        mid = (hh + ll) / 2.0
        return np.where(mid > 0, (hh - ll) / mid, np.nan)


_donch_variants = []
for p in DONCHIAN_PERIODS:
    for output in ["UPPER", "LOWER"]:
        _donch_variants.append(IndicatorVariant(
            name=f"DONCH_{output}_{p}",
            family="VOL_DONCHIAN",
            params={"period": p, "output": output},
        ))

_donch_family = register_family(IndicatorFamily(
    name="VOL_DONCHIAN",
    tier=1,
    warmup=200,
    variants=_donch_variants,
    compute_fn=_compute_donchian,
))


# ---------------------------------------------------------------------------
# Rolling StdDev of returns (4 variants)
# ---------------------------------------------------------------------------

STDDEV_PERIODS = [5, 10, 20, 30, 50, 100]


def _compute_stddev(open_, high, low, close, volume, timestamps, context, variant):
    """Rolling standard deviation of log returns."""
    p = variant.params["period"]
    log_ret = np.log(close / np.roll(close, 1, axis=0))
    log_ret[0] = 0.0
    return rolling_std(log_ret, p)


_stddev_family = register_family(IndicatorFamily(
    name="VOL_STDDEV",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"VSTD_{p}", family="VOL_STDDEV", params={"period": p})
        for p in STDDEV_PERIODS
    ],
    compute_fn=_compute_stddev,
))


# ---------------------------------------------------------------------------
# Parkinson Volatility Estimator (4 variants)
# ---------------------------------------------------------------------------

PARKINSON_PERIODS = [10, 20, 50, 100]


def _compute_parkinson(open_, high, low, close, volume, timestamps, context, variant):
    """Parkinson: sqrt(1/(4n*ln2) * sum(ln(H/L)^2))."""
    p = variant.params["period"]
    hl_log = np.log(np.where(low > 0, high / low, 1.0))
    hl_log_sq = hl_log ** 2
    mean_sq = rolling_mean(hl_log_sq, p)
    return np.sqrt(mean_sq / (4.0 * np.log(2.0)))


_park_family = register_family(IndicatorFamily(
    name="VOL_PARKINSON",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"VPARK_{p}", family="VOL_PARKINSON", params={"period": p})
        for p in PARKINSON_PERIODS
    ],
    compute_fn=_compute_parkinson,
))


# ---------------------------------------------------------------------------
# Garman-Klass Volatility (3 variants)
# ---------------------------------------------------------------------------

GK_PERIODS = [10, 20, 50, 100]


def _compute_gk(open_, high, low, close, volume, timestamps, context, variant):
    """Garman-Klass: uses OHLC for more efficient vol estimation."""
    p = variant.params["period"]

    hl = np.log(np.where(low > 0, high / low, 1.0))
    co = np.log(np.where(open_ > 0, close / open_, 1.0))

    gk_per_bar = 0.5 * hl ** 2 - (2.0 * np.log(2.0) - 1.0) * co ** 2
    return np.sqrt(np.maximum(rolling_mean(gk_per_bar, p), 0.0))


_gk_family = register_family(IndicatorFamily(
    name="VOL_GK",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"VGK_{p}", family="VOL_GK", params={"period": p})
        for p in GK_PERIODS
    ],
    compute_fn=_compute_gk,
))


# ---------------------------------------------------------------------------
# Yang-Zhang Volatility (3 variants)
# ---------------------------------------------------------------------------

YZ_PERIODS = [10, 20, 50, 100]


def _compute_yz(open_, high, low, close, volume, timestamps, context, variant):
    """Yang-Zhang: combines overnight, open-close, and Rogers-Satchell components."""
    p = variant.params["period"]
    n_bars = close.shape[0]

    # Overnight return: log(open / prev_close)
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    overnight = np.log(np.where(prev_close > 0, open_ / prev_close, 1.0))

    # Open-to-close return
    oc = np.log(np.where(open_ > 0, close / open_, 1.0))

    # Rogers-Satchell per bar
    ho = np.log(np.where(open_ > 0, high / open_, 1.0))
    lo = np.log(np.where(open_ > 0, low / open_, 1.0))
    hc = np.log(np.where(close > 0, high / close, 1.0))
    lc = np.log(np.where(close > 0, low / close, 1.0))
    rs = ho * hc + lo * lc

    # Variance components
    k = 0.34 / (1.34 + (p + 1) / (p - 1))

    var_overnight = rolling_mean(overnight ** 2, p)
    var_oc = rolling_mean(oc ** 2, p)
    var_rs = rolling_mean(rs, p)

    yz_var = var_overnight + k * var_oc + (1.0 - k) * var_rs
    return np.sqrt(np.maximum(yz_var, 0.0))


_yz_family = register_family(IndicatorFamily(
    name="VOL_YZ",
    tier=1,
    warmup=200,
    variants=[
        IndicatorVariant(name=f"VYZ_{p}", family="VOL_YZ", params={"period": p})
        for p in YZ_PERIODS
    ],
    compute_fn=_compute_yz,
))
