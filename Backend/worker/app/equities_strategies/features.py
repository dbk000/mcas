"""
Equities Feature Engineering
=============================
Shared feature computation for all equities strategies.
Extracted from opus_discovery_iter4_equities.py.
"""

import logging
import numpy as np
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rolling helpers (vectorized, no Python loops)
# ---------------------------------------------------------------------------

def _lag(arr: np.ndarray, n: int) -> np.ndarray:
    """Shift array forward by n rows, filling with NaN."""
    out = np.full_like(arr, np.nan)
    if n < arr.shape[0]:
        out[n:] = arr[:-n]
    return out


def _rolling_mean(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling mean over axis=0, NaN-aware."""
    T = arr.shape[0]
    out = np.full_like(arr, np.nan, dtype=float)
    cumsum = np.nancumsum(arr, axis=0)
    count = np.nancumsum(np.isfinite(arr).astype(float), axis=0)
    out[w - 1:] = (cumsum[w - 1:] - np.concatenate([np.zeros_like(cumsum[:1]), cumsum], axis=0)[:T - w + 1]) / \
                   np.maximum(count[w - 1:] - np.concatenate([np.zeros_like(count[:1]), count], axis=0)[:T - w + 1], 1)
    return out


def _rolling_std(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling standard deviation, NaN-aware. Uses E[X^2] - E[X]^2."""
    mean = _rolling_mean(arr, w)
    mean_sq = _rolling_mean(arr ** 2, w)
    var = mean_sq - mean ** 2
    var = np.maximum(var, 0)  # numerical safety
    return np.sqrt(var)


def _rolling_linear_slope(arr: np.ndarray, w: int) -> np.ndarray:
    """Rolling linear regression slope over window w."""
    T = arr.shape[0]
    out = np.full_like(arr, np.nan, dtype=float)
    x = np.arange(w, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    for t in range(w - 1, T):
        window = arr[t - w + 1:t + 1]
        valid = np.isfinite(window)
        if valid.all():
            y_mean = window.mean(axis=0)
            cov = ((x[:, np.newaxis] - x_mean) * (window - y_mean)).sum(axis=0)
            out[t] = cov / max(x_var, 1e-10)
    return out


def _cross_sectional_rank(arr_1d: np.ndarray) -> np.ndarray:
    """Rank values cross-sectionally (0-1 scale). NaN stays NaN."""
    valid = np.isfinite(arr_1d)
    out = np.full_like(arr_1d, np.nan)
    if valid.sum() < 2:
        return out
    vals = arr_1d[valid]
    ranks = np.argsort(np.argsort(vals)).astype(float) / (len(vals) - 1)
    out[valid] = ranks
    return out


# ---------------------------------------------------------------------------
# Main feature engineering
# ---------------------------------------------------------------------------

def load_ohlcv_matrix(conn, symbols: list[str], lookback_days: int = 200):
    """Load OHLCV matrix from Postgres.

    Returns:
        ohlcv: dict with 'open', 'high', 'low', 'close', 'volume' as (T, N) arrays
        timestamps: (T,) array of bar_start_ts
        actual_symbols: list of symbols that had data
    """
    cur = conn.cursor()

    # Get available dates
    cur.execute("""
        SELECT DISTINCT bar_start_ts FROM ohlcv
        WHERE source = 'equities_yahoo' AND timeframe = '1d' AND symbol = 'SPY'
        ORDER BY bar_start_ts DESC LIMIT %s
    """, (lookback_days,))
    dates = sorted([r[0] for r in cur.fetchall()])

    if len(dates) < 60:
        raise ValueError(f"Insufficient OHLCV data: {len(dates)} bars, need >= 60")

    T = len(dates)
    N = len(symbols)
    sym_idx = {s: i for i, s in enumerate(symbols)}

    O = np.full((T, N), np.nan)
    H = np.full((T, N), np.nan)
    L = np.full((T, N), np.nan)
    C = np.full((T, N), np.nan)
    V = np.full((T, N), np.nan)

    date_idx = {d: i for i, d in enumerate(dates)}

    placeholders = ",".join(["%s"] * len(symbols))
    cur.execute(f"""
        SELECT symbol, bar_start_ts, open, high, low, close, volume
        FROM ohlcv
        WHERE source = 'equities_yahoo' AND timeframe = '1d'
          AND symbol IN ({placeholders})
          AND bar_start_ts >= %s
        ORDER BY bar_start_ts
    """, (*symbols, dates[0]))

    for row in cur.fetchall():
        sym, ts, o, h, l, c, v = row
        if sym in sym_idx and ts in date_idx:
            t = date_idx[ts]
            n = sym_idx[sym]
            O[t, n] = float(o)
            H[t, n] = float(h)
            L[t, n] = float(l)
            C[t, n] = float(c)
            V[t, n] = float(v)

    cur.close()

    return {
        "open": O, "high": H, "low": L, "close": C, "volume": V,
        "timestamps": np.array(dates),
    }


def load_news_matrix(conn, symbols: list[str], timestamps):
    """Load news event matrix from Postgres equities_events table.

    Returns:
        news_mat: (T, N, 4) array — [event_count, unique_pubs, avg_sentiment, pub_novelty]
        Returns zeros if table doesn't exist or has no data.
    """
    T = len(timestamps)
    N = len(symbols)
    news_mat = np.zeros((T, N, 4))

    try:
        cur = conn.cursor()
        sym_idx = {s: i for i, s in enumerate(symbols)}
        date_idx = {d: i for i, d in enumerate(timestamps)}

        placeholders = ",".join(["%s"] * len(symbols))
        cur.execute(f"""
            SELECT symbol, event_date, event_count, unique_publishers,
                   avg_sentiment, publisher_novelty
            FROM equities_events
            WHERE symbol IN ({placeholders})
              AND event_date >= %s
            ORDER BY event_date
        """, (*symbols, timestamps[0]))

        for row in cur.fetchall():
            sym, dt, count, pubs, sent, novelty = row
            if sym in sym_idx and dt in date_idx:
                t = date_idx[dt]
                n = sym_idx[sym]
                news_mat[t, n, 0] = float(count or 0)
                news_mat[t, n, 1] = float(pubs or 0)
                news_mat[t, n, 2] = float(sent or 0)
                news_mat[t, n, 3] = float(novelty or 0)

        cur.close()
    except Exception as e:
        logger.warning("News matrix load failed (table may not exist): %s", e)

    return news_mat


def engineer_features(ohlcv: dict, news_mat: np.ndarray) -> dict[str, np.ndarray]:
    """Compute all features from OHLCV and news matrices.

    Returns dict of feature_name -> (T, N) arrays.
    """
    O, H, L, C, V = ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"]
    T, N = C.shape
    features = {}

    # Returns
    ret_1 = C / _lag(C, 1) - 1.0
    ret_5 = C / _lag(C, 5) - 1.0
    ret_20 = C / _lag(C, 20) - 1.0
    ret_60 = C / _lag(C, 60) - 1.0
    features["ret_1d"] = ret_1
    features["ret_5d"] = ret_5
    features["ret_20d"] = ret_20
    features["ret_60d"] = ret_60

    # Volatility
    realized_vol_20 = _rolling_std(ret_1, 20)
    features["realized_vol_20"] = realized_vol_20

    # Volume
    vol_ma_5 = _rolling_mean(V, 5)
    vol_ma_20 = _rolling_mean(V, 20)
    features["relative_volume_5"] = V / np.where(vol_ma_5 > 0, vol_ma_5, np.nan)

    # Cross-sectional ranks
    cs_rank_ret_20 = np.full_like(ret_20, np.nan)
    cs_rank_ret_60 = np.full_like(ret_60, np.nan)
    for t in range(T):
        cs_rank_ret_20[t] = _cross_sectional_rank(ret_20[t])
        cs_rank_ret_60[t] = _cross_sectional_rank(ret_60[t])
    features["cs_rank_ret_20d"] = cs_rank_ret_20
    features["cs_rank_ret_60d"] = cs_rank_ret_60

    # OBV and slope (for VPD strategy)
    sign_ret = np.sign(ret_1)
    signed_vol = sign_ret * V
    obv = np.nancumsum(np.nan_to_num(signed_vol, nan=0.0), axis=0)
    features["obv"] = obv
    features["obv_slope_5"] = _rolling_linear_slope(obv, 5)

    # Rolling high/low for VPD
    high_5 = np.full_like(C, np.nan)
    low_5 = np.full_like(C, np.nan)
    for t in range(4, T):
        high_5[t] = np.nanmax(H[t-4:t+1], axis=0)
        low_5[t] = np.nanmin(L[t-4:t+1], axis=0)
    features["high_5d"] = high_5
    features["low_5d"] = low_5

    # News features
    event_count = news_mat[:, :, 0]
    events_20d = _rolling_mean(event_count, 20)
    features["events_20d_avg"] = events_20d

    # Aggregate news velocity z-score (for REV1A)
    event_vel = event_count - _lag(event_count, 1)
    agg_event_vel = np.nanmean(event_vel, axis=1, keepdims=True)  # (T, 1)
    agg_ev_broadcast = np.broadcast_to(agg_event_vel, (T, N)).copy()
    agg_ev_mean = _rolling_mean(agg_ev_broadcast[:, 0:1], 60)
    agg_ev_std = _rolling_std(agg_ev_broadcast[:, 0:1], 60)
    agg_event_vel_z = (agg_event_vel - agg_ev_mean) / np.where(agg_ev_std > 0, agg_ev_std, np.nan)
    features["agg_news_vel_zscore"] = np.broadcast_to(agg_event_vel_z, (T, N)).copy()

    # SPY beta and residuals (index 0 = SPY)
    spy_ret = ret_1[:, 0:1]
    # Simple rolling beta: cov(stock, spy) / var(spy) over 60 bars
    spy_beta = np.full_like(C, np.nan)
    for t in range(59, T):
        stock_window = ret_1[t-59:t+1]  # (60, N)
        spy_window = spy_ret[t-59:t+1]  # (60, 1)
        valid = np.isfinite(stock_window) & np.isfinite(spy_window)
        for n in range(N):
            v = valid[:, n]
            if v.sum() >= 30:
                s_w = stock_window[v, n]
                spy_w = spy_window[v, 0]
                cov = np.cov(s_w, spy_w)[0, 1]
                var_spy = np.var(spy_w)
                if var_spy > 1e-10:
                    spy_beta[t, n] = cov / var_spy
    features["spy_beta_60"] = spy_beta

    # Clip all features
    for k in features:
        features[k] = np.clip(features[k], -100, 100)

    return features


def check_data_freshness(conn, max_stale_days: int = 3) -> tuple[bool, str]:
    """Check if equities OHLCV data is fresh enough.

    Returns (is_fresh, message).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(bar_start_ts) FROM ohlcv
        WHERE symbol = 'SPY' AND timeframe = '1d' AND source = 'equities_yahoo'
    """)
    row = cur.fetchone()
    cur.close()

    if not row or not row[0]:
        return False, "No SPY OHLCV data found"

    latest = row[0]
    now = datetime.now(timezone.utc)

    if hasattr(latest, 'timestamp'):
        age_days = (now - latest).days
    else:
        age_days = (now.timestamp() - float(latest)) / 86400

    if age_days > max_stale_days:
        return False, f"SPY data is {age_days:.1f} days old (max {max_stale_days})"

    return True, f"Data fresh: {age_days:.1f} days old"


def check_split_guard(close: np.ndarray) -> np.ndarray:
    """Flag bars with |daily return| > 30% as potential splits.

    Returns boolean mask (T, N) where True = suspected split (should be NaN'd).
    """
    ret = close[1:] / close[:-1] - 1.0
    extreme = np.abs(ret) > 0.30
    # Prepend False for first bar
    return np.concatenate([np.zeros((1, close.shape[1]), dtype=bool), extreme], axis=0)
