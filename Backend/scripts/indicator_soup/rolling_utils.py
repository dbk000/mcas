"""
2D rolling operations for (n_bars, n_symbols) matrices.

All functions accept and return float64 arrays with shape (n_bars, n_symbols).
Warmup bars are NaN. Operations are O(n) via cumsum where possible.
"""

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import lfilter


# ---------------------------------------------------------------------------
# Forward-fill
# ---------------------------------------------------------------------------

def ffill_2d(arr: np.ndarray) -> np.ndarray:
    """Forward-fill NaN along axis=0 (time)."""
    out = arr.copy()
    for j in range(1, out.shape[0]):
        mask = np.isnan(out[j])
        out[j, mask] = out[j - 1, mask]
    return out


# ---------------------------------------------------------------------------
# Cumsum-based O(n) operations
# ---------------------------------------------------------------------------

def rolling_sum(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling sum with cumsum trick. First w-1 bars = NaN.

    NaN policy: uses nancumsum, which treats NaN as 0.  A window with k
    valid values and (w-k) NaN returns the sum of the k values, NOT NaN.
    This means indicators derived from rolling_sum (SMA, CMF, CMO, etc.)
    produce biased values when symbols have interior NaN gaps — the
    denominator is w but the actual valid count is less.  For symbols with
    continuous data (most of the universe) this is correct.  Documented
    here as a conscious design choice: prefer producing a value over
    propagating NaN.
    """
    n_bars = arr.shape[0]
    if w > n_bars:
        return np.full_like(arr, np.nan)
    cs = np.nancumsum(arr, axis=0)
    out = np.full_like(arr, np.nan)
    out[w - 1] = cs[w - 1]
    if n_bars > w:
        out[w:] = cs[w:] - cs[:-w]
    return out


def rolling_mean(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling mean via cumsum."""
    return rolling_sum(arr, w) / w


def rolling_std(arr: np.ndarray, w: int, ddof: int = 0) -> np.ndarray:
    """Rolling std via cumsum of x and x^2. Population std by default."""
    cs1 = np.nancumsum(arr, axis=0)
    cs2 = np.nancumsum(arr ** 2, axis=0)

    s1 = np.full_like(arr, np.nan)
    s2 = np.full_like(arr, np.nan)
    s1[w - 1] = cs1[w - 1]
    s2[w - 1] = cs2[w - 1]
    if arr.shape[0] > w:
        s1[w:] = cs1[w:] - cs1[:-w]
        s2[w:] = cs2[w:] - cs2[:-w]

    mean = s1 / w
    var = s2 / w - mean ** 2
    var = np.maximum(var, 0)
    if ddof > 0:
        var = var * w / (w - ddof)
    return np.sqrt(var)


def rolling_corr(x: np.ndarray, y: np.ndarray, w: int) -> np.ndarray:
    """Rolling Pearson correlation via cumsum."""
    sx = rolling_sum(x, w)
    sy = rolling_sum(y, w)
    sx2 = rolling_sum(x ** 2, w)
    sy2 = rolling_sum(y ** 2, w)
    sxy = rolling_sum(x * y, w)

    numer = w * sxy - sx * sy
    dx = w * sx2 - sx ** 2
    dy = w * sy2 - sy ** 2
    denom = np.sqrt(np.maximum(dx, 0) * np.maximum(dy, 0))

    out = np.full_like(x, np.nan)
    valid = denom > 0
    out[valid] = numer[valid] / denom[valid]
    out[: w - 1] = np.nan
    return out


# ---------------------------------------------------------------------------
# Sliding-window operations (O(n*w) but required for min/max)
# ---------------------------------------------------------------------------

def rolling_max(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling max using sliding_window_view."""
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w - 1 :] = np.nanmax(sliding_window_view(arr, w, axis=0), axis=-1)
    return out


def rolling_min(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling min using sliding_window_view."""
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w - 1 :] = np.nanmin(sliding_window_view(arr, w, axis=0), axis=-1)
    return out


def rolling_argmax(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling argmax (bars since max) using sliding_window_view."""
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w - 1 :] = (w - 1) - np.nanargmax(
            sliding_window_view(arr, w, axis=0), axis=-1
        )
    return out


def rolling_argmin(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling argmin (bars since min) using sliding_window_view."""
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w - 1 :] = (w - 1) - np.nanargmin(
            sliding_window_view(arr, w, axis=0), axis=-1
        )
    return out


# ---------------------------------------------------------------------------
# Cumsum-based slope (O(n) memory — no (n, s, w) tensor)
# ---------------------------------------------------------------------------

def rolling_slope(arr: np.ndarray, w: int) -> np.ndarray:
    """OLS slope via cumsum. O(n*s) memory, safe at any scale."""
    n_bars, n_sym = arr.shape
    out = np.full_like(arr, np.nan)
    if n_bars < w:
        return out

    n = w
    sum_i = n * (n - 1) / 2
    sum_i2 = n * (n - 1) * (2 * n - 1) / 6
    denom = n * sum_i2 - sum_i ** 2
    if denom == 0:
        return out

    idx = np.arange(n_bars, dtype=np.float64)[:, None]
    cs_iy = np.nancumsum(idx * arr, axis=0)
    cs_y = np.nancumsum(arr, axis=0)

    pad = np.zeros((1, n_sym))
    iy_sum = cs_iy[w - 1 :] - np.concatenate([pad, cs_iy[: -w]], axis=0)
    y_sum = cs_y[w - 1 :] - np.concatenate([pad, cs_y[: -w]], axis=0)

    k = np.arange(n_bars - w + 1, dtype=np.float64)[:, None]
    local_iy = iy_sum - k * y_sum

    slopes = (n * local_iy - sum_i * y_sum) / denom
    out[w - 1 :] = slopes
    return out


# ---------------------------------------------------------------------------
# EMA / Wilder smooth via scipy.signal.lfilter (IIR, vectorized 2D)
# ---------------------------------------------------------------------------

def rolling_ema(arr: np.ndarray, span: int) -> np.ndarray:
    """Vectorized 2D EMA via lfilter. Handles NaN via forward-fill.

    Leading NaN per column are backfilled with the first valid value before
    the IIR filter runs.  Without this, chained EMA (e.g. DEMA = EMA(EMA))
    feeds NaN into lfilter whose infinite memory propagates NaN forever.
    """
    alpha = 2.0 / (span + 1)
    mask = np.isnan(arr)
    filled = arr.copy()
    # Backfill leading NaN with first valid value per column
    for col in range(filled.shape[1]):
        col_mask = mask[:, col]
        first_valid = np.argmax(~col_mask)
        if col_mask[first_valid]:  # entire column is NaN
            continue
        filled[:first_valid, col] = filled[first_valid, col]
    # Forward-fill interior NaN gaps
    filled = ffill_2d(filled)
    # lfilter along axis=0 for each column simultaneously
    result = lfilter([alpha], [1, -(1 - alpha)], filled, axis=0)
    result[mask] = np.nan
    # Mark warmup (first span-1 bars) as NaN for clean downstream
    result[: span - 1] = np.nan
    return result


def rolling_wilder(arr: np.ndarray, span: int) -> np.ndarray:
    """Wilder smoothing (alpha = 1/span instead of 2/(span+1)).

    Same leading-NaN backfill as rolling_ema — see docstring there.
    """
    alpha = 1.0 / span
    mask = np.isnan(arr)
    filled = arr.copy()
    # Backfill leading NaN with first valid value per column
    for col in range(filled.shape[1]):
        col_mask = mask[:, col]
        first_valid = np.argmax(~col_mask)
        if col_mask[first_valid]:  # entire column is NaN
            continue
        filled[:first_valid, col] = filled[first_valid, col]
    # Forward-fill interior NaN gaps
    filled = ffill_2d(filled)
    result = lfilter([alpha], [1, -(1 - alpha)], filled, axis=0)
    result[mask] = np.nan
    result[: span - 1] = np.nan
    return result


# ---------------------------------------------------------------------------
# Cross-sectional rank (per-bar)
# ---------------------------------------------------------------------------

def cross_sectional_rank(arr: np.ndarray) -> np.ndarray:
    """Rank each row (timestamp) across columns (symbols). NaN-aware.
    Returns fractional ranks in [0, 1]."""
    out = np.full_like(arr, np.nan)
    for t in range(arr.shape[0]):
        row = arr[t]
        valid = ~np.isnan(row)
        n_valid = valid.sum()
        if n_valid < 2:
            continue
        ranks = np.argsort(np.argsort(row[valid])).astype(np.float64)
        out[t, valid] = ranks / (n_valid - 1)
    return out


# ---------------------------------------------------------------------------
# Percentage rank within rolling window
# ---------------------------------------------------------------------------

def rolling_percentile_rank(arr: np.ndarray, w: int) -> np.ndarray:
    """For each bar, what percentile is current value within trailing w bars?
    Returns values in [0, 1]. Memory-safe: processes in time chunks."""
    n_bars, n_sym = arr.shape
    out = np.full_like(arr, np.nan)
    if n_bars < w:
        return out

    # Process in chunks to avoid (n_bars, n_sym, w) tensor OOM
    chunk_size = max(1, min(500, 500_000_000 // (n_sym * w * 8)))

    for start in range(0, n_bars - w + 1, chunk_size):
        end = min(start + chunk_size, n_bars - w + 1)
        windows = sliding_window_view(arr[start:start + end - start + w - 1], w, axis=0)
        current = arr[start + w - 1: start + w - 1 + (end - start)]
        count_le = np.nansum(windows <= current[:, :, np.newaxis], axis=-1)
        out[start + w - 1: start + w - 1 + (end - start)] = (count_le - 1) / (w - 1)

    return out


# ---------------------------------------------------------------------------
# True Range (building block for ATR)
# ---------------------------------------------------------------------------

def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True Range: max(H-L, |H-prevC|, |L-prevC|). First bar = H-L."""
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]

    hl = high - low
    hpc = np.abs(high - prev_close)
    lpc = np.abs(low - prev_close)

    tr = np.maximum(hl, np.maximum(hpc, lpc))
    return tr


# ---------------------------------------------------------------------------
# Typical / median / weighted close helpers
# ---------------------------------------------------------------------------

def typical_price(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    return (high + low + close) / 3.0


def median_price(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    return (high + low) / 2.0


def weighted_close(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    return (high + low + 2 * close) / 4.0
