"""
EXTRACTED PURE LOGIC FROM: SCR_IND_FAM_composite.py
V5 Updates: Full Quarantine enforced.
have been physically replaced to satisfy the zero-contamination mandate.
Dataclass imports are explicitly injected to guarantee semantics.
"""

from dataclasses import dataclass, field, asdict

import numpy as np

from ..rolling_utils import (
    rolling_mean,
    rolling_std,
    rolling_ema,
    rolling_slope,
    rolling_max,
    rolling_min,
    rolling_sum,
    rolling_corr,
    rolling_percentile_rank,
    true_range,
    rolling_wilder,
)

from . import IndicatorFamily, IndicatorVariant, register_family

MA_CROSS_PAIRS = [
    ("EMA_9", "EMA_20"),
    ("EMA_9", "EMA_50"),
    ("EMA_12", "EMA_26"),
    ("EMA_20", "EMA_50"),
    ("SMA_20", "SMA_50"),
    ("SMA_50", "SMA_200"),
]

def _compute_ma_cross(open_, high, low, close, volume, timestamps, context, variant):
    ctype = variant.params["type"]
    fast_key = variant.params.get("fast")
    slow_key = variant.params.get("slow")

    if ctype == "CROSS":
        fast = context.get(fast_key)
        slow = context.get(slow_key)
        if fast is None or slow is None:
            return None
        # Cross: +1 when fast crosses above slow, -1 below, 0 otherwise
        above = fast > slow
        cross_up = above & ~np.roll(above, 1, axis=0)
        cross_down = ~above & np.roll(above, 1, axis=0)
        cross_up[0] = False
        cross_down[0] = False
        return np.where(cross_up, 1.0, np.where(cross_down, -1.0, 0.0))

    elif ctype == "SPREAD":
        fast = context.get(fast_key)
        slow = context.get(slow_key)
        if fast is None or slow is None:
            return None
        return np.where(slow > 0, (fast - slow) / slow, 0.0)

    elif ctype == "STACK_BULL":
        # EMA_9 > EMA_20 > EMA_50 (all must be in context)
        e9 = context.get("EMA_9")
        e20 = context.get("EMA_20")
        e50 = context.get("EMA_50")
        if e9 is None or e20 is None or e50 is None:
            return None
        return np.where((e9 > e20) & (e20 > e50), 1.0, 0.0)

    elif ctype == "STACK_BEAR":
        e9 = context.get("EMA_9")
        e20 = context.get("EMA_20")
        e50 = context.get("EMA_50")
        if e9 is None or e20 is None or e50 is None:
            return None
        return np.where((e9 < e20) & (e20 < e50), -1.0, 0.0)

    elif ctype == "MA_RIBBON_WIDTH":
        # Spread between fastest and slowest MA as % of price
        e9 = context.get("EMA_9")
        e50 = context.get("EMA_50")
        if e9 is None or e50 is None:
            return None
        return np.where(close > 0, np.abs(e9 - e50) / close, 0.0)

    elif ctype == "PRICE_VS_SMA":
        sma_key = variant.params["sma_key"]
        sma = context.get(sma_key)
        if sma is None:
            return None
        return np.where(sma > 0, (close - sma) / sma, 0.0)

    return None

_mac_variants = []

def _compute_slope_div(open_, high, low, close, volume, timestamps, context, variant):
    stype = variant.params["type"]

    if stype.startswith("SLOPE_"):
        target = variant.params["target"]
        period = variant.params["period"]
        arr = context.get(target)
        if arr is None:
            return None
        return rolling_slope(arr, period)

    elif stype == "RSI_DIVERGENCE":
        # Bullish divergence: price makes lower low but RSI makes higher low
        rsi = context.get("RSI_14")
        if rsi is None:
            return None
        period = variant.params["period"]
        price_ll = rolling_min(close, period)
        rsi_ll = rolling_min(rsi, period)
        prev_price_ll = np.empty_like(price_ll)
        prev_price_ll[:period] = np.nan
        prev_price_ll[period:] = price_ll[:-period]
        prev_rsi_ll = np.empty_like(rsi_ll)
        prev_rsi_ll[:period] = np.nan
        prev_rsi_ll[period:] = rsi_ll[:-period]

        bull_div = (close <= price_ll) & (rsi > prev_rsi_ll) & (~np.isnan(prev_rsi_ll))
        # Bearish divergence: price at rolling high but RSI below prior rolling high
        rsi_hh = rolling_max(rsi, period)
        prev_rsi_hh = np.empty_like(rsi_hh)
        prev_rsi_hh[:period] = np.nan
        prev_rsi_hh[period:] = rsi_hh[:-period]
        bear_div = (close >= rolling_max(close, period)) & (rsi < prev_rsi_hh) & (~np.isnan(prev_rsi_hh))
        return np.where(bull_div, 1.0, np.where(bear_div, -1.0, 0.0))

    elif stype == "OBV_DIVERGENCE":
        # Price up but OBV down = bearish divergence
        period = variant.params["period"]
        direction = np.sign(np.diff(close, axis=0, prepend=close[:1]))
        obv = np.cumsum(direction * volume, axis=0)
        price_slope = rolling_slope(close, period)
        obv_slope = rolling_slope(obv, period)
        bull = (price_slope < 0) & (obv_slope > 0)
        bear = (price_slope > 0) & (obv_slope < 0)
        return np.where(bull, 1.0, np.where(bear, -1.0, 0.0))

    elif stype == "VOLUME_PRICE_TREND":
        # VPT = cumsum(volume * (close / prev_close - 1))
        prev_c = np.empty_like(close)
        prev_c[0] = close[0]
        prev_c[1:] = close[:-1]
        vpt = np.cumsum(volume * (close / np.where(prev_c > 0, prev_c, 1.0) - 1.0), axis=0)
        return vpt

    return None

_slope_targets = [
    ("RSI_14", 5), ("RSI_14", 10), ("RSI_14", 20),
    ("EMA_9", 5), ("EMA_9", 10),
    ("EMA_20", 5), ("EMA_20", 10), ("EMA_20", 20),
    ("EMA_50", 10), ("EMA_50", 20),
    ("SMA_20", 10), ("SMA_20", 20),
    ("SMA_50", 10), ("SMA_50", 20),
    ("SMA_200", 20), ("SMA_200", 50),
    ("ATR_14", 10), ("ATR_14", 20),
    ("ATR_10", 10), ("ATR_10", 20),
]

_sd_variants = []

def _compute_composite(open_, high, low, close, volume, timestamps, context, variant):
    with np.errstate(divide="ignore", invalid="ignore"):
        return _compute_composite_inner(open_, high, low, close, volume, timestamps, context, variant)

def _compute_composite_inner(open_, high, low, close, volume, timestamps, context, variant):
    ctype = variant.params["type"]

    if ctype == "BB_SQUEEZE":
        # BB inside KC = squeeze (low volatility)
        p = variant.params.get("period", 20)
        sma = context.get(f"SMA_{p}", rolling_mean(close, p))
        std = rolling_std(close, p)
        bb_upper = sma + 2.0 * std
        bb_lower = sma - 2.0 * std

        ema = context.get(f"EMA_{p}", rolling_ema(close, p))
        atr = context.get("ATR_14")
        if atr is None:
            atr = rolling_wilder(true_range(high, low, close), 14)
        kc_upper = ema + 1.5 * atr
        kc_lower = ema - 1.5 * atr

        return np.where((bb_upper < kc_upper) & (bb_lower > kc_lower), 1.0, 0.0)

    elif ctype == "SQUEEZE_MOMENTUM":
        # When in squeeze: momentum direction (close vs midline)
        p = variant.params.get("period", 20)
        sma = context.get(f"SMA_{p}", rolling_mean(close, p))
        hl2 = (rolling_max(high, p) + rolling_min(low, p)) / 2.0
        midline = (sma + hl2) / 2.0
        return close - midline

    elif ctype == "VOLATILITY_RATIO":
        # Short-term vol / long-term vol
        short_p = variant.params["short"]
        long_p = variant.params["long"]
        log_ret = np.log(close / np.roll(close, 1, axis=0))
        log_ret[0] = 0.0
        short_vol = rolling_std(log_ret, short_p)
        long_vol = rolling_std(log_ret, long_p)
        return np.where(long_vol > 0, short_vol / long_vol, 1.0)

    elif ctype == "ATR_RATIO":
        a10 = context.get("ATR_10")
        a14 = context.get("ATR_14")
        if a10 is None or a14 is None:
            return None
        return np.where(a14 > 0, a10 / a14, 1.0)

    elif ctype == "RSI_MA_COMBO":
        rsi = context.get("RSI_14")
        ema = context.get("EMA_20")
        if rsi is None or ema is None:
            return None
        # RSI normalized to [-1, 1] * price position relative to EMA
        rsi_norm = (rsi - 50.0) / 50.0
        price_pos = np.where(ema > 0, (close - ema) / ema, 0.0)
        return rsi_norm * np.sign(price_pos)

    elif ctype == "TREND_STRENGTH":
        # ADX * sign of DI difference (if ADX available in context)
        adx = context.get("ADX_14")
        if adx is not None:
            return adx / 100.0
        # Fallback: slope of EMA_50 normalized
        ema50 = context.get("EMA_50")
        if ema50 is None:
            return None
        slope = rolling_slope(ema50, 20)
        return np.where(close > 0, slope / close * 1000, 0.0)

    elif ctype == "MEAN_REVERSION_SCORE":
        # How far from mean (z-score of close vs SMA)
        sma = context.get("SMA_20", rolling_mean(close, 20))
        std = rolling_std(close, 20)
        return np.where(std > 0, (close - sma) / std, 0.0)

    elif ctype == "MEAN_REVERSION_SCORE_N":
        p = variant.params["period"]
        sma = context.get(f"SMA_{p}", rolling_mean(close, p))
        std = rolling_std(close, p)
        return np.where(std > 0, (close - sma) / std, 0.0)

    elif ctype == "ZSCORE":
        p = variant.params["period"]
        mean = rolling_mean(close, p)
        std = rolling_std(close, p)
        return np.where(std > 0, (close - mean) / std, 0.0)

    elif ctype == "VOLUME_ZSCORE":
        p = variant.params["period"]
        mean = rolling_mean(volume, p)
        std = rolling_std(volume, p)
        return np.where(std > 0, (volume - mean) / std, 0.0)

    elif ctype == "PRICE_ACCELERATION":
        # Second derivative of price (ROC of ROC)
        p = variant.params["period"]
        n_bars = close.shape[0]
        out = np.full_like(close, np.nan)
        if n_bars > 2 * p:
            roc1 = close[p:] / close[:-p] - 1.0
            roc2_start = np.full((p,) + close.shape[1:], np.nan)
            roc2 = roc1[p:] - roc1[:-p]
            out[2 * p:] = roc2
        return out

    return None

_comp_variants = [
    IndicatorVariant(name="BB_SQUEEZE_20", family="COMPOSITE", params={"type": "BB_SQUEEZE", "period": 20}),
    IndicatorVariant(name="BB_SQUEEZE_50", family="COMPOSITE", params={"type": "BB_SQUEEZE", "period": 50}),
    IndicatorVariant(name="SQUEEZE_MOM_20", family="COMPOSITE", params={"type": "SQUEEZE_MOMENTUM", "period": 20}),
    IndicatorVariant(name="SQUEEZE_MOM_50", family="COMPOSITE", params={"type": "SQUEEZE_MOMENTUM", "period": 50}),
    IndicatorVariant(name="VRATIO_5_20", family="COMPOSITE", params={"type": "VOLATILITY_RATIO", "short": 5, "long": 20}),
    IndicatorVariant(name="VRATIO_10_50", family="COMPOSITE", params={"type": "VOLATILITY_RATIO", "short": 10, "long": 50}),
    IndicatorVariant(name="VRATIO_20_100", family="COMPOSITE", params={"type": "VOLATILITY_RATIO", "short": 20, "long": 100}),
    IndicatorVariant(name="ATR_RATIO", family="COMPOSITE", params={"type": "ATR_RATIO"}),
    IndicatorVariant(name="RSI_MA_COMBO", family="COMPOSITE", params={"type": "RSI_MA_COMBO"}),
    IndicatorVariant(name="TREND_STRENGTH", family="COMPOSITE", params={"type": "TREND_STRENGTH"}),
    IndicatorVariant(name="MREV_SCORE_20", family="COMPOSITE", params={"type": "MEAN_REVERSION_SCORE"}),
    IndicatorVariant(name="ZSCORE_20", family="COMPOSITE", params={"type": "ZSCORE", "period": 20}),
    IndicatorVariant(name="ZSCORE_50", family="COMPOSITE", params={"type": "ZSCORE", "period": 50}),
    IndicatorVariant(name="ZSCORE_100", family="COMPOSITE", params={"type": "ZSCORE", "period": 100}),
    IndicatorVariant(name="VOL_ZSCORE_20", family="COMPOSITE", params={"type": "VOLUME_ZSCORE", "period": 20}),
    IndicatorVariant(name="VOL_ZSCORE_50", family="COMPOSITE", params={"type": "VOLUME_ZSCORE", "period": 50}),
    IndicatorVariant(name="PRICE_ACCEL_5", family="COMPOSITE", params={"type": "PRICE_ACCELERATION", "period": 5}),
    IndicatorVariant(name="PRICE_ACCEL_10", family="COMPOSITE", params={"type": "PRICE_ACCELERATION", "period": 10}),
    IndicatorVariant(name="PRICE_ACCEL_20", family="COMPOSITE", params={"type": "PRICE_ACCELERATION", "period": 20}),
    IndicatorVariant(name="BB_SQUEEZE_10", family="COMPOSITE", params={"type": "BB_SQUEEZE", "period": 10}),
    IndicatorVariant(name="SQUEEZE_MOM_10", family="COMPOSITE", params={"type": "SQUEEZE_MOMENTUM", "period": 10}),
    IndicatorVariant(name="VRATIO_5_50", family="COMPOSITE", params={"type": "VOLATILITY_RATIO", "short": 5, "long": 50}),
    IndicatorVariant(name="VRATIO_10_100", family="COMPOSITE", params={"type": "VOLATILITY_RATIO", "short": 10, "long": 100}),
    IndicatorVariant(name="ZSCORE_10", family="COMPOSITE", params={"type": "ZSCORE", "period": 10}),
    IndicatorVariant(name="ZSCORE_200", family="COMPOSITE", params={"type": "ZSCORE", "period": 200}),
    IndicatorVariant(name="VOL_ZSCORE_10", family="COMPOSITE", params={"type": "VOLUME_ZSCORE", "period": 10}),
    IndicatorVariant(name="VOL_ZSCORE_100", family="COMPOSITE", params={"type": "VOLUME_ZSCORE", "period": 100}),
    IndicatorVariant(name="MREV_SCORE_50", family="COMPOSITE", params={"type": "MEAN_REVERSION_SCORE_N", "period": 50}),
]

def _compute_regime(open_, high, low, close, volume, timestamps, context, variant):
    with np.errstate(divide="ignore", invalid="ignore"):
        return _compute_regime_inner(open_, high, low, close, volume, timestamps, context, variant)

def _compute_regime_inner(open_, high, low, close, volume, timestamps, context, variant):
    rtype = variant.params["type"]

    if rtype == "TREND_REGIME":
        # Binary: trending (ADX>25 proxy via EMA slope) vs ranging
        ema50 = context.get("EMA_50")
        if ema50 is None:
            return None
        slope = rolling_slope(ema50, 20)
        std = rolling_std(close, 20)
        normalized_slope = np.where(std > 0, np.abs(slope) / std, 0.0)
        return normalized_slope

    elif rtype == "VOLATILITY_REGIME":
        # Percentile rank of current ATR within trailing window
        atr = context.get("ATR_14")
        if atr is None:
            return None
        return rolling_percentile_rank(atr, variant.params["period"])

    elif rtype == "VOLUME_REGIME":
        return rolling_percentile_rank(volume, variant.params["period"])

    elif rtype == "HURST":
        # Hurst exponent proxy via autocorrelation decay (O(n*s) memory).
        # Full R/S method requires (n, s, w) tensor — OOM at scale.
        # Proxy: H = 0.5 + 0.5 * log2(1 + autocorr(1)) approximates Hurst.
        # More robust: use variance ratio V(2p)/V(p) approach.
        p = variant.params["period"]
        n_bars, n_sym = close.shape
        out = np.full_like(close, np.nan)
        if n_bars < 2 * p:
            return out

        log_ret = np.log(close / np.roll(close, 1, axis=0))
        log_ret[0] = 0.0

        # Variance ratio: Var(k-period returns) / (k * Var(1-period returns))
        # H > 0.5 = trending, H < 0.5 = mean-reverting, H = 0.5 = random walk
        var_1 = rolling_std(log_ret, p) ** 2  # variance of 1-bar returns

        # k-bar returns
        k = min(p, 10)  # use 10-bar aggregation
        k_ret = np.full_like(close, np.nan)
        if n_bars > k:
            k_ret[k:] = np.log(close[k:] / close[:-k])
        var_k = rolling_std(k_ret, p) ** 2

        # Variance ratio
        vr = np.where(var_1 * k > 0, var_k / (var_1 * k), 1.0)
        # Convert to Hurst proxy: H = log(VR) / (2 * log(k)) + 0.5
        out = np.where(vr > 0, np.log(np.maximum(vr, 1e-10)) / (2.0 * np.log(k)) + 0.5, 0.5)
        out[:2 * p] = np.nan
        return out

    elif rtype == "AUTOCORRELATION":
        # Lag-1 autocorrelation of returns
        p = variant.params["period"]
        log_ret = np.log(close / np.roll(close, 1, axis=0))
        log_ret[0] = 0.0
        lagged = np.roll(log_ret, 1, axis=0)
        lagged[0] = 0.0
        return rolling_corr(log_ret, lagged, p)

    elif rtype == "STREAK_LENGTH":
        # Consecutive up/down closes
        direction = np.sign(np.diff(close, axis=0, prepend=close[:1]))
        streak = np.zeros_like(close)
        for t in range(1, close.shape[0]):
            same = direction[t] == direction[t - 1]
            streak[t] = np.where(same & (direction[t] != 0), streak[t - 1] + direction[t], direction[t])
        return streak

    elif rtype == "UP_DAY_RATIO":
        # Fraction of up days in trailing window
        p = variant.params["period"]
        up = np.where(close > np.roll(close, 1, axis=0), 1.0, 0.0)
        up[0] = 0.0
        return rolling_mean(up, p)

    elif rtype == "EFFICIENCY_RATIO":
        # Kaufman ER: net price change / sum of per-bar changes
        p = variant.params["period"]
        n_bars = close.shape[0]
        out = np.full_like(close, np.nan)
        if n_bars <= p:
            return out
        direction = np.abs(close[p:] - close[:-p])
        abs_change = np.abs(np.diff(close, axis=0))
        volatility = rolling_sum(abs_change, p)
        volatility = volatility[p - 1:]
        out[p:] = np.where(volatility > 0, direction / volatility, 0.0)
        return out

    elif rtype == "RANGE_PERSISTENCE":
        # How often price stays within prior bar's range
        p = variant.params["period"]
        inside = np.zeros_like(close)
        if close.shape[0] >= 2:
            inside[1:] = np.where(
                (high[1:] <= high[:-1]) & (low[1:] >= low[:-1]), 1.0, 0.0
            )
        return rolling_mean(inside, p)

    elif rtype == "MOMENTUM_REGIME":
        # Percentile rank of ROC within trailing window
        p = variant.params["period"]
        n_bars = close.shape[0]
        roc = np.full_like(close, np.nan)
        roc_p = variant.params.get("roc_period", 10)
        if n_bars > roc_p:
            roc[roc_p:] = close[roc_p:] / close[:-roc_p] - 1.0
        return rolling_percentile_rank(roc, p)

    elif rtype == "VOLATILITY_EXPANSION":
        # Current ATR / long-term ATR — expansion when > 1
        short_p = variant.params["short"]
        long_p = variant.params["long"]
        atr = context.get("ATR_14")
        if atr is None:
            return None
        short_vol = rolling_mean(atr, short_p)
        long_vol = rolling_mean(atr, long_p)
        return np.where(long_vol > 0, short_vol / long_vol, 1.0)

    elif rtype == "REGIME_CHANGE":
        # Absolute change in volatility regime percentile — detects transitions
        p = variant.params["period"]
        atr = context.get("ATR_14")
        if atr is None:
            return None
        prank = rolling_percentile_rank(atr, p)
        change = np.full_like(close, np.nan)
        lookback = variant.params.get("lookback", 5)
        if close.shape[0] > lookback:
            change[lookback:] = prank[lookback:] - prank[:-lookback]
        return change

    return None

_regime_variants = []

def _compute_risk(open_, high, low, close, volume, timestamps, context, variant):
    with np.errstate(divide="ignore", invalid="ignore"):
        return _compute_risk_inner(open_, high, low, close, volume, timestamps, context, variant)

def _compute_risk_inner(open_, high, low, close, volume, timestamps, context, variant):
    rtype = variant.params["type"]

    if rtype == "MAX_DRAWDOWN":
        p = variant.params["period"]
        hh = rolling_max(close, p)
        dd = (close - hh) / np.where(hh > 0, hh, 1.0)
        return dd

    elif rtype == "DRAWDOWN_DURATION":
        # Bars since last ATH within window
        p = variant.params["period"]
        hh = rolling_max(close, p)
        at_high = close >= hh * 0.999  # within 0.1% of high
        duration = np.zeros_like(close)
        for t in range(1, close.shape[0]):
            duration[t] = np.where(at_high[t], 0, duration[t - 1] + 1)
        return duration

    elif rtype == "UPSIDE_VOLATILITY":
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        up_ret = np.where(ret > 0, ret, 0.0)
        return rolling_std(up_ret, p)

    elif rtype == "DOWNSIDE_VOLATILITY":
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        dn_ret = np.where(ret < 0, ret, 0.0)
        return rolling_std(dn_ret, p)

    elif rtype == "UPDOWN_VOL_RATIO":
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        up_vol = rolling_std(np.where(ret > 0, ret, 0.0), p)
        dn_vol = rolling_std(np.where(ret < 0, ret, 0.0), p)
        return np.where(dn_vol > 0, up_vol / dn_vol, 1.0)

    elif rtype == "SORTINO_PROXY":
        # Return / downside vol
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        mean_ret = rolling_mean(ret, p)
        dn_ret = np.where(ret < 0, ret, 0.0)
        dn_vol = rolling_std(dn_ret, p)
        return np.where(dn_vol > 0, mean_ret / dn_vol, 0.0)

    elif rtype == "CALMAR_PROXY":
        # Return / max drawdown
        p = variant.params["period"]
        n_bars = close.shape[0]
        out = np.full_like(close, np.nan)
        if n_bars <= p:
            return out
        ret = close[p:] / close[:-p] - 1.0
        hh = rolling_max(close, p)
        dd = (close - hh) / np.where(hh > 0, hh, 1.0)
        max_dd = rolling_min(dd, p)
        max_dd_aligned = max_dd[p:]  # same length as ret
        out[p:] = np.where(max_dd_aligned < -0.001, ret / np.abs(max_dd_aligned), 0.0)
        return out

    elif rtype == "TAIL_RATIO":
        # Proxy for tail ratio using upside_vol / downside_vol.
        # True percentile calculation requires (n, s, w) tensor — OOM at scale.
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        up_vol = rolling_std(np.where(ret > 0, ret, 0.0), p)
        dn_vol = rolling_std(np.where(ret < 0, ret, 0.0), p)
        return np.where(dn_vol > 0, up_vol / dn_vol, 1.0)

    elif rtype == "GAIN_LOSS_RATIO":
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        mean_gain = rolling_mean(np.where(ret > 0, ret, 0.0), p)
        mean_loss = rolling_mean(np.where(ret < 0, -ret, 0.0), p)
        return np.where(mean_loss > 0, mean_gain / mean_loss, 1.0)

    elif rtype == "OMEGA_PROXY":
        # sum(gains) / sum(|losses|) over window
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        sum_gain = rolling_sum(np.where(ret > 0, ret, 0.0), p)
        sum_loss = rolling_sum(np.where(ret < 0, -ret, 0.0), p)
        return np.where(sum_loss > 0, sum_gain / sum_loss, 1.0)

    elif rtype == "ULCER_INDEX":
        # sqrt(mean(drawdown^2)) over window — penalizes depth and duration
        p = variant.params["period"]
        hh = rolling_max(close, p)
        dd_pct = (close - hh) / np.where(hh > 0, hh, 1.0) * 100.0
        return np.sqrt(np.maximum(rolling_mean(dd_pct ** 2, p), 0.0))

    elif rtype == "PAIN_RATIO":
        # Return / Ulcer Index — risk-adjusted return
        p = variant.params["period"]
        n_bars = close.shape[0]
        out = np.full_like(close, np.nan)
        if n_bars <= p:
            return out
        ret = close[p:] / close[:-p] - 1.0
        hh = rolling_max(close, p)
        dd_pct = (close - hh) / np.where(hh > 0, hh, 1.0) * 100.0
        ulcer = np.sqrt(np.maximum(rolling_mean(dd_pct ** 2, p), 0.0))
        ulcer_aligned = ulcer[p:]
        out[p:] = np.where(ulcer_aligned > 0.01, ret / ulcer_aligned, 0.0)
        return out

    return None

_risk_variants = []

def _compute_meta(open_, high, low, close, volume, timestamps, context, variant):
    with np.errstate(divide="ignore", invalid="ignore"):
        return _compute_meta_inner(open_, high, low, close, volume, timestamps, context, variant)

def _compute_meta_inner(open_, high, low, close, volume, timestamps, context, variant):
    mtype = variant.params["type"]

    if mtype == "IC_PROXY_PRICE_VOLUME":
        # Rolling correlation between price change and volume
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1])
        return rolling_corr(np.abs(ret), volume, p)

    elif mtype == "PRICE_VOL_CORR":
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1])
        return rolling_corr(ret, volume, p)

    elif mtype == "RETURN_SKEW":
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        mean = rolling_mean(ret, p)
        std = rolling_std(ret, p)
        dev = ret - mean
        m3 = rolling_mean(dev ** 3, p)
        return np.where(std > 0, m3 / (std ** 3), 0.0)

    elif mtype == "RETURN_KURTOSIS":
        p = variant.params["period"]
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        mean = rolling_mean(ret, p)
        std = rolling_std(ret, p)
        dev = ret - mean
        m4 = rolling_mean(dev ** 4, p)
        return np.where(std > 0, m4 / (std ** 4) - 3.0, 0.0)

    elif mtype == "VOLUME_PRICE_CONFIRMATION":
        # Price up + volume up = +1, price up + volume down = -1
        p = variant.params["period"]
        price_slope = rolling_slope(close, p)
        vol_slope = rolling_slope(volume, p)
        price_dir = np.sign(price_slope)
        vol_dir = np.sign(vol_slope)
        return price_dir * vol_dir  # +1 = confirmed, -1 = divergent

    elif mtype == "INTRABAR_VOLATILITY":
        # (H-L)/C normalized by rolling average
        p = variant.params["period"]
        intra = (high - low) / np.where(close > 0, close, 1.0)
        mean_intra = rolling_mean(intra, p)
        return np.where(mean_intra > 0, intra / mean_intra, 1.0)

    elif mtype == "AMIHUD_ILLIQUIDITY":
        # |return| / volume — higher = more illiquid
        p = variant.params["period"]
        ret = np.abs(np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0))
        dollar_vol = volume * close
        ratio = np.where(dollar_vol > 0, ret / dollar_vol, 0.0)
        return rolling_mean(ratio, p)

    elif mtype == "VOLUME_ENTROPY":
        # Proxy for volume entropy using coefficient of variation.
        # True rolling entropy requires (n, s, w) tensor — OOM at scale.
        # CV-based proxy: low CV = uniform distribution = high entropy.
        p = variant.params["period"]
        vol_mean = rolling_mean(volume, p)
        vol_std = rolling_std(volume, p)
        cv = np.where(vol_mean > 0, vol_std / vol_mean, 0.0)
        # Map to [0,1]: entropy proxy = 1 - normalized CV
        # CV=0 → max entropy (uniform), high CV → low entropy (concentrated)
        return 1.0 / (1.0 + cv)

    elif mtype == "CLOSE_TO_OPEN_GAP":
        # Rolling average gap (close-to-open) as fraction of price
        result = np.zeros_like(close)
        if close.shape[0] >= 2:
            gap = (open_[1:] - close[:-1]) / np.where(close[:-1] > 0, close[:-1], 1.0)
            result[1:] = gap
        p = variant.params.get("period", 20)
        return rolling_mean(result, p)

    elif mtype == "HIGH_LOW_CORR":
        # Correlation between high and low changes
        p = variant.params["period"]
        h_change = np.diff(high, axis=0, prepend=high[:1])
        l_change = np.diff(low, axis=0, prepend=low[:1])
        return rolling_corr(h_change, l_change, p)

    elif mtype == "RETURN_AUTOCORR_5":
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        lagged = np.roll(ret, 5, axis=0)
        lagged[:5] = 0.0
        p = variant.params["period"]
        return rolling_corr(ret, lagged, p)

    elif mtype == "VOLUME_CONCENTRATION":
        # Proxy for volume concentration using max/mean ratio.
        # True top-20% calculation requires (n, s, w) tensor — OOM at scale.
        # max(volume, w) / mean(volume, w) → high = concentrated, low = spread
        p = variant.params["period"]
        vol_max = rolling_max(volume, p)
        vol_mean = rolling_mean(volume, p)
        return np.where(vol_mean > 0, vol_max / (p * vol_mean), 1.0 / p)

    elif mtype == "VOLUME_IMPULSE":
        # Volume spike: rolling max volume / rolling mean volume
        p = variant.params["period"]
        vol_max = rolling_max(volume, p)
        vol_mean = rolling_mean(volume, p)
        return np.where(vol_mean > 0, vol_max / vol_mean, 1.0)

    elif mtype == "PRICE_RANGE_NORM":
        # Normalized intrabar range: (H-L)/C averaged over window
        p = variant.params["period"]
        bar_range = (high - low) / np.where(close > 0, close, 1.0)
        return rolling_mean(bar_range, p)

    elif mtype == "RETURN_AUTOCORR_10":
        # Lag-10 autocorrelation of returns
        ret = np.diff(close, axis=0, prepend=close[:1]) / np.where(close > 0, close, 1.0)
        lagged = np.empty_like(ret)
        lagged[:10] = 0.0
        lagged[10:] = ret[:-10]
        p = variant.params["period"]
        return rolling_corr(ret, lagged, p)

    elif mtype == "OVERNIGHT_REVERSAL":
        # Tendency for gaps to reverse: corr(gap, intraday_return)
        p = variant.params["period"]
        gap = np.zeros_like(close)
        intra = np.zeros_like(close)
        if close.shape[0] >= 2:
            gap[1:] = (open_[1:] - close[:-1]) / np.where(close[:-1] > 0, close[:-1], 1.0)
            intra[1:] = (close[1:] - open_[1:]) / np.where(open_[1:] > 0, open_[1:], 1.0)
        return rolling_corr(gap, intra, p)

    elif mtype == "VOLUME_TREND":
        # Slope of volume normalized by mean volume
        p = variant.params["period"]
        vol_slope = rolling_slope(volume, p)
        vol_mean = rolling_mean(volume, p)
        return np.where(vol_mean > 0, vol_slope / vol_mean, 0.0)

    return None

_meta_variants = []

def _compute_multi(open_, high, low, close, volume, timestamps, context, variant):
    mtype = variant.params["type"]

    if mtype == "MA_CONVERGENCE":
        # Number of MAs (out of available) above close / total
        ma_keys = ["SMA_20", "SMA_50", "SMA_200", "EMA_9", "EMA_20", "EMA_50"]
        count_above = np.zeros_like(close)
        total = 0
        for k in ma_keys:
            ma = context.get(k)
            if ma is not None:
                count_above += np.where(close > ma, 1.0, 0.0)
                total += 1
        return count_above / max(total, 1)

    elif mtype == "RSI_MULTI_TF_PROXY":
        # RSI at different periods — spread between fast and slow RSI
        fast = variant.params.get("fast", 6)
        slow = variant.params.get("slow", 25)

        def _rsi(c, p):
            diff = np.empty_like(c)
            diff[0] = 0.0
            diff[1:] = c[1:] - c[:-1]
            ag = rolling_wilder(np.where(diff > 0, diff, 0.0), p)
            al = rolling_wilder(np.where(diff < 0, -diff, 0.0), p)
            rs = np.where(al > 0, ag / al, 100.0)
            rsi = 100.0 - 100.0 / (1.0 + rs)
            rsi[:p] = np.nan
            return rsi

        rsi_f = _rsi(close, fast)
        rsi_s = _rsi(close, slow)
        return rsi_f - rsi_s

    elif mtype == "STOCH_RSI":
        # Stochastic of RSI
        rsi = context.get("RSI_14")
        if rsi is None:
            return None
        p = variant.params["period"]
        rsi_max = rolling_max(rsi, p)
        rsi_min = rolling_min(rsi, p)
        denom = rsi_max - rsi_min
        return np.where(denom > 0, (rsi - rsi_min) / denom, 0.5)

    elif mtype == "BB_PERCENTILE":
        # BB %B percentile rank over trailing window
        sma20 = context.get("SMA_20", rolling_mean(close, 20))
        std20 = rolling_std(close, 20)
        bb_range = 4.0 * std20
        pct_b = np.where(bb_range > 0, (close - (sma20 - 2.0 * std20)) / bb_range, 0.5)
        return rolling_percentile_rank(pct_b, variant.params["period"])

    elif mtype == "ATR_PCT_RANK":
        atr = context.get("ATR_14")
        if atr is None:
            return None
        return rolling_percentile_rank(atr, variant.params["period"])

    elif mtype == "MOMENTUM_MULTI":
        # ROC at multiple horizons averaged
        periods = variant.params["periods"]
        combined = np.zeros_like(close)
        count = 0
        for p in periods:
            n = close.shape[0]
            if n > p:
                roc = np.full_like(close, np.nan)
                roc[p:] = close[p:] / close[:-p] - 1.0
                valid = ~np.isnan(roc)
                combined = np.where(valid, combined + roc, combined)
                count += valid.astype(float)
        return np.where(count > 0, combined / count, np.nan)

    elif mtype == "VOLUME_MULTI":
        # Volume relative to mean at multiple lookbacks, averaged
        periods = variant.params["periods"]
        combined = np.zeros_like(close)
        count = 0
        for p in periods:
            mean_v = rolling_mean(volume, p)
            ratio = np.where(mean_v > 0, volume / mean_v, 1.0)
            combined += ratio
            count += 1
        return combined / count

    return None

_multi_variants = [
    IndicatorVariant(name="MA_CONVERGENCE", family="MULTI_PERIOD", params={"type": "MA_CONVERGENCE"}),
    IndicatorVariant(name="RSI_SPREAD_6_14", family="MULTI_PERIOD", params={"type": "RSI_MULTI_TF_PROXY", "fast": 6, "slow": 14}),
    IndicatorVariant(name="RSI_SPREAD_6_25", family="MULTI_PERIOD", params={"type": "RSI_MULTI_TF_PROXY", "fast": 6, "slow": 25}),
    IndicatorVariant(name="RSI_SPREAD_9_25", family="MULTI_PERIOD", params={"type": "RSI_MULTI_TF_PROXY", "fast": 9, "slow": 25}),
    IndicatorVariant(name="RSI_SPREAD_9_50", family="MULTI_PERIOD", params={"type": "RSI_MULTI_TF_PROXY", "fast": 9, "slow": 50}),
    IndicatorVariant(name="RSI_SPREAD_14_50", family="MULTI_PERIOD", params={"type": "RSI_MULTI_TF_PROXY", "fast": 14, "slow": 50}),
    IndicatorVariant(name="STOCH_RSI_9", family="MULTI_PERIOD", params={"type": "STOCH_RSI", "period": 9}),
    IndicatorVariant(name="STOCH_RSI_14", family="MULTI_PERIOD", params={"type": "STOCH_RSI", "period": 14}),
    IndicatorVariant(name="STOCH_RSI_20", family="MULTI_PERIOD", params={"type": "STOCH_RSI", "period": 20}),
    IndicatorVariant(name="STOCH_RSI_50", family="MULTI_PERIOD", params={"type": "STOCH_RSI", "period": 50}),
    IndicatorVariant(name="BB_PCTRANK_20", family="MULTI_PERIOD", params={"type": "BB_PERCENTILE", "period": 20}),
    IndicatorVariant(name="BB_PCTRANK_50", family="MULTI_PERIOD", params={"type": "BB_PERCENTILE", "period": 50}),
    IndicatorVariant(name="BB_PCTRANK_100", family="MULTI_PERIOD", params={"type": "BB_PERCENTILE", "period": 100}),
    IndicatorVariant(name="BB_PCTRANK_200", family="MULTI_PERIOD", params={"type": "BB_PERCENTILE", "period": 200}),
    IndicatorVariant(name="ATR_PCTRANK_20", family="MULTI_PERIOD", params={"type": "ATR_PCT_RANK", "period": 20}),
    IndicatorVariant(name="ATR_PCTRANK_50", family="MULTI_PERIOD", params={"type": "ATR_PCT_RANK", "period": 50}),
    IndicatorVariant(name="ATR_PCTRANK_100", family="MULTI_PERIOD", params={"type": "ATR_PCT_RANK", "period": 100}),
    IndicatorVariant(name="ATR_PCTRANK_200", family="MULTI_PERIOD", params={"type": "ATR_PCT_RANK", "period": 200}),
    IndicatorVariant(name="MOM_MULTI_FAST", family="MULTI_PERIOD", params={"type": "MOMENTUM_MULTI", "periods": [1, 3, 5]}),
    IndicatorVariant(name="MOM_MULTI_SHORT", family="MULTI_PERIOD", params={"type": "MOMENTUM_MULTI", "periods": [3, 5, 10]}),
    IndicatorVariant(name="MOM_MULTI_MED", family="MULTI_PERIOD", params={"type": "MOMENTUM_MULTI", "periods": [10, 20, 50]}),
    IndicatorVariant(name="MOM_MULTI_LONG", family="MULTI_PERIOD", params={"type": "MOMENTUM_MULTI", "periods": [20, 50, 100]}),
    IndicatorVariant(name="MOM_MULTI_XLONG", family="MULTI_PERIOD", params={"type": "MOMENTUM_MULTI", "periods": [50, 100, 200]}),
    IndicatorVariant(name="VOL_MULTI_FAST", family="MULTI_PERIOD", params={"type": "VOLUME_MULTI", "periods": [3, 5, 10]}),
    IndicatorVariant(name="VOL_MULTI_SHORT", family="MULTI_PERIOD", params={"type": "VOLUME_MULTI", "periods": [5, 10, 20]}),
    IndicatorVariant(name="VOL_MULTI_MED", family="MULTI_PERIOD", params={"type": "VOLUME_MULTI", "periods": [10, 20, 50]}),
    IndicatorVariant(name="VOL_MULTI_LONG", family="MULTI_PERIOD", params={"type": "VOLUME_MULTI", "periods": [20, 50, 100]}),
    IndicatorVariant(name="VOL_MULTI_XLONG", family="MULTI_PERIOD", params={"type": "VOLUME_MULTI", "periods": [50, 100, 200]}),
    IndicatorVariant(name="ATR_PCTRANK_10", family="MULTI_PERIOD", params={"type": "ATR_PCT_RANK", "period": 10}),
    IndicatorVariant(name="STOCH_RSI_100", family="MULTI_PERIOD", params={"type": "STOCH_RSI", "period": 100}),
]

def _compute_exhaustion(open_, high, low, close, volume, timestamps, context, variant):
    with np.errstate(divide="ignore", invalid="ignore"):
        return _compute_exhaustion_inner(open_, high, low, close, volume, timestamps, context, variant)

def _compute_exhaustion_inner(open_, high, low, close, volume, timestamps, context, variant):
    etype = variant.params["type"]

    if etype == "BUYING_CLIMAX":
        # Wide range + high volume + close near high + reversal next bar
        p = variant.params.get("period", 20)
        r = high - low
        mean_r = rolling_mean(r, p)
        mean_v = rolling_mean(volume, p)
        close_pos = np.where(r > 0, (close - low) / r, 0.5)
        range_expansion = np.where(mean_r > 0, r / mean_r, 1.0)
        vol_expansion = np.where(mean_v > 0, volume / mean_v, 1.0)
        return range_expansion * vol_expansion * close_pos

    elif etype == "SELLING_CLIMAX":
        p = variant.params.get("period", 20)
        r = high - low
        mean_r = rolling_mean(r, p)
        mean_v = rolling_mean(volume, p)
        close_pos = np.where(r > 0, (high - close) / r, 0.5)  # close near low = selling
        range_expansion = np.where(mean_r > 0, r / mean_r, 1.0)
        vol_expansion = np.where(mean_v > 0, volume / mean_v, 1.0)
        return range_expansion * vol_expansion * close_pos

    elif etype == "VOLUME_SPIKE":
        p = variant.params["period"]
        mean_v = rolling_mean(volume, p)
        std_v = rolling_std(volume, p)
        return np.where(std_v > 0, (volume - mean_v) / std_v, 0.0)

    elif etype == "MOMENTUM_EXHAUSTION":
        # High RSI + declining momentum
        rsi = context.get("RSI_14")
        if rsi is None:
            return None
        rsi_slope = rolling_slope(rsi, 5)
        # Overbought + declining RSI = exhaustion
        return np.where(rsi > 70, -rsi_slope, np.where(rsi < 30, rsi_slope, 0.0))

    elif etype == "VELOCITY_DECELERATION":
        # Price acceleration turning negative after positive run
        p = variant.params["period"]
        n_bars = close.shape[0]
        if n_bars < 2 * p:
            return np.full_like(close, np.nan)
        roc = np.full_like(close, np.nan)
        roc[p:] = close[p:] / close[:-p] - 1.0
        accel = np.full_like(close, np.nan)
        accel[2 * p:] = roc[2 * p:] - roc[p:-p]
        return accel

    elif etype == "DIVERGENCE_STRENGTH":
        # Combined price-up / volume-down divergence score
        p = variant.params["period"]
        price_slope = rolling_slope(close, p)
        vol_slope = rolling_slope(volume, p)
        ps = np.where(close > 0, price_slope / close, 0.0)
        vs = np.where(volume > 0, vol_slope / volume, 0.0)
        # Divergence = price going one way, volume going opposite
        return ps * np.where(vs * ps < 0, -np.abs(vs), 0.0)

    return None

_exh_variants = []

