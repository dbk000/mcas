"""
Ridge model for the simultaneous solver strategy.

Encapsulates: feature computation, model weights, scoring, regime gate.
Trained offline — this module loads frozen weights and applies them.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger("ridge_model")


@dataclass
class ModelConfig:
    """Frozen model parameters from the solver."""
    weights: np.ndarray          # (n_features,) ridge weights
    feature_means: np.ndarray    # (n_features,) standardization means
    feature_stds: np.ndarray     # (n_features,) standardization stds
    score_threshold: float       # percentile threshold (in score units, not pct)
    regime_threshold: float      # mean_return_24h smoothed threshold
    stop_mult: float = 1.5
    time_stop_bars: int = 8
    alpha: float = 100.0
    feature_names: list[str] = field(default_factory=list)


FEATURE_NAMES = [
    "rsi_14", "rsi_slope_3",
    "macd_line_atr", "macd_histogram_atr", "bars_above_zero",
    "close_vs_sma20_atr", "close_vs_sma50_atr", "close_vs_sma200_atr",
    "volume_z_20", "rvol_20", "atr_14_pct",
    "return_1h", "return_4h", "return_24h",
    "hour_sin", "hour_cos",
]


# ── Indicator functions (frozen, matching solver exactly) ──

def _ema(v: np.ndarray, p: int) -> np.ndarray:
    a = 2.0 / (p + 1)
    o = np.empty_like(v, dtype=np.float64)
    o[0] = v[0]
    for i in range(1, len(v)):
        o[i] = a * v[i] + (1 - a) * o[i - 1]
    return o


def compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    d = np.diff(close)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100.0 - 100.0 / (1.0 + _ema(g, period) / _ema(l, period))
    return np.concatenate([[np.nan], rsi])


def compute_macd(close, fast=12, slow=26, signal=9):
    line = _ema(close, fast) - _ema(close, slow)
    hist = line - _ema(line, signal)
    return line, hist


def compute_sma(close, period):
    n = len(close)
    out = np.full(n, np.nan)
    cs = np.cumsum(close)
    out[period - 1:] = (cs[period - 1:] - np.concatenate([[0], cs[:n - period]])) / period
    return out


def compute_atr(high, low, close, period=14):
    tr = np.empty(len(close))
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    return _ema(tr, period)


def compute_features_for_bar(close: np.ndarray, high: np.ndarray,
                              low: np.ndarray, volume: np.ndarray,
                              hours: np.ndarray) -> dict[str, np.ndarray]:
    """Compute all 16 features for a bar series. Returns dict of arrays."""
    n = len(close)
    rsi = compute_rsi(close)
    ml, mh = compute_macd(close)
    sma20, sma50, sma200 = compute_sma(close, 20), compute_sma(close, 50), compute_sma(close, 200)
    atr = compute_atr(high, low, close)

    # Volume stats
    vm = np.full(n, np.nan)
    vs = np.full(n, np.nan)
    cs_v = np.cumsum(volume)
    cs_v2 = np.cumsum(volume * volume)
    pv = np.concatenate([[0], cs_v])
    pv2 = np.concatenate([[0], cs_v2])
    ie = np.arange(20, n + 1)
    ist = ie - 20
    wm = (pv[ie] - pv[ist]) / 20
    wvar = (pv2[ie] - pv2[ist]) / 20 - wm ** 2
    vm[19:] = wm
    vs[19:] = np.sqrt(np.maximum(wvar, 0))

    with np.errstate(divide="ignore", invalid="ignore"):
        vz = np.where(vs > 0, (volume - vm) / vs, 0.0)
        rv = np.where(vm > 0, volume / vm, np.nan)
        c20 = (close - sma20) / atr
        c50 = (close - sma50) / atr
        c200 = (close - sma200) / atr
        ml_a = ml / atr
        mh_a = mh / atr
        atr_p = atr / close * 100

    # Bars since MACD above zero
    baz = np.zeros(n)
    count = 0.0
    for i in range(n):
        if np.isnan(ml[i]):
            baz[i] = np.nan
        elif ml[i] >= 0:
            count += 1
            baz[i] = count
        else:
            count = 0

    rs3 = np.full(n, np.nan)
    rs3[3:] = rsi[3:] - rsi[:-3]
    r1 = np.full(n, np.nan)
    r1[1:] = close[1:] / close[:-1] - 1
    r4 = np.full(n, np.nan)
    r4[4:] = close[4:] / close[:-4] - 1
    r24 = np.full(n, np.nan)
    r24[24:] = close[24:] / close[:-24] - 1

    hs = np.sin(2 * np.pi * hours / 24)
    hc = np.cos(2 * np.pi * hours / 24)

    return {
        "rsi_14": rsi, "rsi_slope_3": rs3,
        "macd_line_atr": ml_a, "macd_histogram_atr": mh_a,
        "bars_above_zero": baz,
        "close_vs_sma20_atr": c20, "close_vs_sma50_atr": c50,
        "close_vs_sma200_atr": c200,
        "volume_z_20": vz, "rvol_20": rv, "atr_14_pct": atr_p,
        "return_1h": r1, "return_4h": r4, "return_24h": r24,
        "hour_sin": hs, "hour_cos": hc,
        "atr_raw": atr, "close": close,
    }


def score_bar(features: dict, bar_idx: int, config: ModelConfig) -> float | None:
    """Score a single bar. Returns score or None if features invalid."""
    raw = np.array([features[f][bar_idx] for f in FEATURE_NAMES])
    if np.any(np.isnan(raw)):
        return None
    normed = (raw - config.feature_means) / config.feature_stds
    return float(normed @ config.weights)


def compute_regime(ret24h_by_symbol: dict[str, float]) -> float:
    """Compute cross-sectional mean 24h return (the regime indicator)."""
    vals = [v for v in ret24h_by_symbol.values() if v is not None and not np.isnan(v)]
    if not vals:
        return 0.0
    return float(np.mean(vals))


def save_config(config: ModelConfig, path: str):
    """Serialize model config to JSON."""
    d = {
        "weights": config.weights.tolist(),
        "feature_means": config.feature_means.tolist(),
        "feature_stds": config.feature_stds.tolist(),
        "score_threshold": config.score_threshold,
        "regime_threshold": config.regime_threshold,
        "stop_mult": config.stop_mult,
        "time_stop_bars": config.time_stop_bars,
        "alpha": config.alpha,
        "feature_names": config.feature_names or FEATURE_NAMES,
    }
    with open(path, "w") as f:
        json.dump(d, f, indent=2)


def load_config(path: str) -> ModelConfig:
    """Load model config from JSON."""
    with open(path) as f:
        d = json.load(f)
    return ModelConfig(
        weights=np.array(d["weights"]),
        feature_means=np.array(d["feature_means"]),
        feature_stds=np.array(d["feature_stds"]),
        score_threshold=d["score_threshold"],
        regime_threshold=d["regime_threshold"],
        stop_mult=d.get("stop_mult", 1.5),
        time_stop_bars=d.get("time_stop_bars", 8),
        alpha=d.get("alpha", 100.0),
        feature_names=d.get("feature_names", FEATURE_NAMES),
    )
