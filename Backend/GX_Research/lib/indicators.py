"""
Vectorized indicators. Pure numpy + pandas.ewm for exponential smoothing.
All functions take 1-D arrays and return 1-D arrays of the same length.
Leading positions before warmup return NaN.

Wilder-smoothed indicators (ATR/RSI/ADX) use `ewm(alpha=1/n, adjust=False)` with
a forced SMA seed at index n-1 so the series matches the classical formulation
bit-for-bit after warmup.

Also exposes `IndicatorCache` — per-(symbol, timeframe) memoizer so signal
functions don't recompute identical indicators for each combo.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _as_series(a: np.ndarray) -> pd.Series:
    return pd.Series(a, dtype=np.float64)


def sma(close: np.ndarray, n: int) -> np.ndarray:
    if close.size < n:
        return np.full(close.shape, np.nan, dtype=np.float64)
    out = pd.Series(close).rolling(window=n, min_periods=n).mean().to_numpy(dtype=np.float64)
    return out


def ema(close: np.ndarray, n: int) -> np.ndarray:
    """Classical EMA with SMA seed. alpha = 2/(n+1)."""
    if close.size < n:
        return np.full(close.shape, np.nan, dtype=np.float64)
    s = pd.Series(close, dtype=np.float64)
    seed = s.iloc[:n].mean()
    # Build a series whose first n-1 values are NaN, then seed at n-1, then EMA onward.
    seeded = s.copy()
    seeded.iloc[: n - 1] = np.nan
    seeded.iloc[n - 1] = seed
    out = np.asarray(seeded.ewm(span=n, adjust=False, ignore_na=False).mean().to_numpy(dtype=np.float64)).copy()
    out[: n - 1] = np.nan
    return out


def _wilder(x: np.ndarray, n: int, seed_mean: bool = True) -> np.ndarray:
    """Wilder smoothing: alpha = 1/n, SMA-seeded at index n-1 (or first non-NaN + n-1)."""
    if x.size < n:
        return np.full(x.shape, np.nan, dtype=np.float64)
    s = pd.Series(x, dtype=np.float64)
    seeded = s.copy()
    if seed_mean:
        seeded.iloc[: n - 1] = np.nan
        seeded.iloc[n - 1] = s.iloc[:n].mean()
    out = np.asarray(seeded.ewm(alpha=1.0 / n, adjust=False, ignore_na=False).mean().to_numpy(dtype=np.float64)).copy()
    out[: n - 1] = np.nan
    return out


def macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ef = ema(close, fast)
    es = ema(close, slow)
    line = ef - es
    # Signal line = EMA of the line, seeded after slow warmup + (signal-1) bars.
    first_valid = int(np.argmax(~np.isnan(line))) if np.any(~np.isnan(line)) else 0
    sig = np.full(close.shape, np.nan, dtype=np.float64)
    start = first_valid + signal - 1
    if close.size > start:
        seed = np.nanmean(line[first_valid : first_valid + signal])
        s = pd.Series(line, dtype=np.float64).copy()
        s.iloc[:start] = np.nan
        s.iloc[start] = seed
        sig_full = np.asarray(s.ewm(span=signal, adjust=False, ignore_na=False).mean().to_numpy(dtype=np.float64))
        sig[start:] = sig_full[start:]
    hist = line - sig
    return line, sig, hist


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    if close.size <= n:
        return np.full(close.shape, np.nan, dtype=np.float64)
    delta = np.diff(close, prepend=close[0])
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    # Wilder smooth both, seeded at index n using the mean of gains[1..n] (classical RSI).
    g = pd.Series(gains).copy()
    l = pd.Series(losses).copy()
    g.iloc[:n] = np.nan
    l.iloc[:n] = np.nan
    g.iloc[n] = np.mean(gains[1 : n + 1])
    l.iloc[n] = np.mean(losses[1 : n + 1])
    avg_gain = np.asarray(g.ewm(alpha=1.0 / n, adjust=False, ignore_na=False).mean().to_numpy()).copy()
    avg_loss = np.asarray(l.ewm(alpha=1.0 / n, adjust=False, ignore_na=False).mean().to_numpy()).copy()
    avg_gain[:n] = np.nan
    avg_loss[:n] = np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss == 0, np.inf, avg_gain / np.where(avg_loss == 0, 1.0, avg_loss))
        out = 100.0 - (100.0 / (1.0 + rs))
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.concatenate([[close[0]], close[:-1]])
    a = high - low
    b = np.abs(high - prev_close)
    c = np.abs(low - prev_close)
    return np.maximum.reduce([a, b, c])


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    tr = true_range(high, low, close)
    return _wilder(tr, n, seed_mean=True)


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    if close.size < n * 2:
        return np.full(close.shape, np.nan, dtype=np.float64)
    up = high[1:] - high[:-1]
    dn = low[:-1] - low[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus_dm = np.concatenate([[0.0], plus_dm])
    minus_dm = np.concatenate([[0.0], minus_dm])
    tr = true_range(high, low, close)

    # Classical ADX uses the Wilder *sum* (not average) for TR / +DM / -DM over n bars.
    # Equivalent to Wilder-average × n. The DI ratios cancel the factor of n.
    atr_s = _wilder_sum(tr, n)
    plus_s = _wilder_sum(plus_dm, n)
    minus_s = _wilder_sum(minus_dm, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * plus_s / atr_s
        minus_di = 100.0 * minus_s / atr_s
        denom = plus_di + minus_di
        dx = np.where(denom == 0, 0.0, 100.0 * np.abs(plus_di - minus_di) / np.where(denom == 0, 1.0, denom))
    # ADX = Wilder smooth of DX starting at index 2n-1 (needs n DX values to seed).
    out = np.full(close.shape, np.nan, dtype=np.float64)
    start = 2 * n - 1
    if close.size > start:
        s = pd.Series(dx).copy()
        s.iloc[:start] = np.nan
        s.iloc[start] = np.mean(dx[n : start + 1])
        adx_full = np.asarray(s.ewm(alpha=1.0 / n, adjust=False, ignore_na=False).mean().to_numpy())
        out[start:] = adx_full[start:]
    return out


def _wilder_sum(x: np.ndarray, n: int) -> np.ndarray:
    """
    Wilder moving sum: S_t = S_{t-1} * (n-1)/n + x_t. Seeded at index n with sum(x[1..n]).
    Equivalent to n × ewm(alpha=1/n, adjust=False).mean() with the seed set to the mean of
    the seed window — pandas implements the AR(1) in C, so this is fully vectorized.
    """
    if x.size <= n:
        return np.full(x.shape, np.nan, dtype=np.float64)
    seed_sum = float(np.sum(x[1 : n + 1]))
    # Feed a series whose first n entries are NaN, with the seed (as a mean) at index n,
    # so pandas ewm uses it as the initial state.
    s = pd.Series(x, dtype=np.float64).copy()
    s.iloc[:n] = np.nan
    s.iloc[n] = seed_sum / n  # ewm mean form of the seed
    mean = np.asarray(s.ewm(alpha=1.0 / n, adjust=False, ignore_na=False).mean().to_numpy()).copy()
    mean[:n] = np.nan
    return mean * n


def bollinger(close: np.ndarray, n: int = 20, k: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    s = pd.Series(close, dtype=np.float64)
    mid = s.rolling(n, min_periods=n).mean().to_numpy()
    std = s.rolling(n, min_periods=n).std(ddof=0).to_numpy()
    upper = mid + k * std
    lower = mid - k * std
    with np.errstate(divide="ignore", invalid="ignore"):
        width = (upper - lower) / mid
    return mid, upper, lower, width


def stochastic(high: np.ndarray, low: np.ndarray, close: np.ndarray, k: int = 14, d: int = 3) -> tuple[np.ndarray, np.ndarray]:
    hh = pd.Series(high).rolling(k, min_periods=k).max().to_numpy()
    ll = pd.Series(low).rolling(k, min_periods=k).min().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = hh - ll
        out_k = np.where(denom == 0, 100.0, 100.0 * (close - ll) / np.where(denom == 0, 1.0, denom))
    out_k = np.where(np.isnan(hh), np.nan, out_k)
    out_d = sma(out_k, d)
    return out_k, out_d


def realised_vol(close: np.ndarray, n: int = 20) -> np.ndarray:
    if close.size < n + 1:
        return np.full(close.shape, np.nan, dtype=np.float64)
    r = np.diff(np.log(close), prepend=np.log(close[0]))
    out = pd.Series(r).rolling(n, min_periods=n).std(ddof=0).to_numpy()
    return out


def volume_z(volume: np.ndarray, n: int = 20) -> np.ndarray:
    if volume.size < n:
        return np.full(volume.shape, np.nan, dtype=np.float64)
    s = pd.Series(volume, dtype=np.float64)
    mu = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = ((volume - mu) / sd).to_numpy().copy()
    mu_arr = mu.to_numpy()
    z[np.isnan(mu_arr)] = np.nan
    return z


# ---------- IndicatorCache ----------

class IndicatorCache:
    """
    Per-symbol (at a fixed timeframe) memoizer. Populated lazily. Keyed by
    (indicator_name, params tuple). Trigger signal functions fetch through this
    so the same EMA(50) isn't recomputed 214 times per cell sweep.
    """

    __slots__ = ("open", "high", "low", "close", "volume", "_cache")

    def __init__(self, open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray | None = None):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self._cache: dict[tuple, Any] = {}

    def sma(self, n: int) -> np.ndarray:
        key = ("sma", n)
        if key not in self._cache:
            self._cache[key] = sma(self.close, n)
        return self._cache[key]

    def ema(self, n: int) -> np.ndarray:
        key = ("ema", n)
        if key not in self._cache:
            self._cache[key] = ema(self.close, n)
        return self._cache[key]

    def rsi(self, n: int = 14) -> np.ndarray:
        key = ("rsi", n)
        if key not in self._cache:
            self._cache[key] = rsi(self.close, n)
        return self._cache[key]

    def atr(self, n: int = 14) -> np.ndarray:
        key = ("atr", n)
        if key not in self._cache:
            self._cache[key] = atr(self.high, self.low, self.close, n)
        return self._cache[key]

    def adx(self, n: int = 14) -> np.ndarray:
        key = ("adx", n)
        if key not in self._cache:
            self._cache[key] = adx(self.high, self.low, self.close, n)
        return self._cache[key]

    def macd(self, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        key = ("macd", fast, slow, signal)
        if key not in self._cache:
            self._cache[key] = macd(self.close, fast, slow, signal)
        return self._cache[key]

    def bollinger(self, n: int = 20, k: float = 2.0):
        key = ("bb", n, k)
        if key not in self._cache:
            self._cache[key] = bollinger(self.close, n, k)
        return self._cache[key]
