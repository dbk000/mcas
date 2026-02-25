"""
Equities Feature Engineering
=============================
Shared feature computation for all equities strategies.
Extracted from opus_discovery_iter4_equities.py.
"""

import logging
import numpy as np
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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

    Aggregates raw rows from equities_events (which has individual event rows,
    not pre-aggregated columns) into per-day, per-symbol counts.

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
        # Map OHLCV bar dates to indices — handle both date and datetime timestamps
        date_idx = {}
        for i, ts in enumerate(timestamps):
            if hasattr(ts, 'date'):
                date_idx[ts.date()] = i
            else:
                date_idx[ts] = i

        cur.execute("""
            SELECT symbol,
                   DATE(available_at_ts AT TIME ZONE 'America/New_York') AS event_date,
                   COUNT(*) AS event_count,
                   COUNT(DISTINCT publisher) AS unique_publishers,
                   AVG(COALESCE(sentiment_score, 0)) AS avg_sentiment,
                   COUNT(DISTINCT source) AS pub_novelty
            FROM equities_events
            WHERE symbol = ANY(%s)
              AND available_at_ts >= %s
            GROUP BY symbol, DATE(available_at_ts AT TIME ZONE 'America/New_York')
        """, (symbols, timestamps[0]))

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
        filled = int((news_mat[:, :, 0] > 0).sum())
        logger.info("News matrix: %d/%d (date,symbol) slots filled", filled, T * N)
    except Exception as e:
        logger.warning("News matrix load failed (table may not exist): %s", e)

    return news_mat


def load_atc_matrix(conn, symbols: list[str], timestamps):
    """Load after-trading chatter matrix from equities_events.

    Single raw-row query + Python-side aggregation. Fixes v1 bugs:
      - Friday postclose bug (uses OHLCV trading-day calendar, not +1 calendar day)
      - Publisher double-counting (set-based dedup across postclose+preopen)
      - Adds keyword tone scoring for OPEN_NEWS titles (v2)

    Temporal model:
      - 'postclose'/'weekend' events → attributed to NEXT trading day
      - 'published_preopen' events → attributed to SAME trading day

    Returns:
        atc_mat: (T, N, 8) array:
          [0] event_count         — all overnight events (including SEC)
          [1] unique_publishers   — set-deduped across sessions
          [2] mean_sentiment      — avg of original sentiment_score
          [3] sentiment_std       — std of original sentiment_score
          [4] max_abs_sentiment   — max |sentiment_score|
          [5] avg_tone            — mean of nonzero keyword tone scores (OPEN_NEWS only)
          [6] tone_eligible_count — count of OPEN_NEWS events
          [7] tone_nonzero_count  — count of OPEN_NEWS events with directional keywords
        Returns zeros if table doesn't exist or has no data.
    """
    T = len(timestamps)
    N = len(symbols)
    atc_mat = np.zeros((T, N, 8))

    try:
        # Import tone scorer (may not be available)
        try:
            from equities_strategies.tone_scorer import score_title_tone as _score_fn
        except Exception:
            _score_fn = None

        # Timezone for ET conversion
        try:
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo('America/New_York')
        except Exception:
            _ET = timezone(timedelta(hours=-5))  # Approximate fallback

        cur = conn.cursor()
        sym_idx = {s: i for i, s in enumerate(symbols)}

        # Build date-to-index mapping from OHLCV timestamps
        date_to_idx = {}
        for i, ts in enumerate(timestamps):
            d = ts.date() if hasattr(ts, 'date') else ts
            date_to_idx[d] = i

        trading_dates = sorted(date_to_idx.keys())

        # Build next-trading-day-after lookup (for postclose/weekend → next trading day)
        # Fixes Friday bug: Friday postclose → Monday (not Saturday)
        next_td_after = {}
        for i in range(len(trading_dates) - 1):
            target_idx = date_to_idx[trading_dates[i + 1]]
            d = trading_dates[i]
            while d < trading_dates[i + 1]:
                next_td_after[d] = target_idx
                d += timedelta(days=1)

        # Single raw-row query (replaces two GROUP BY queries)
        cur.execute("""
            SELECT symbol, title, source, publisher, available_at_ts,
                   session_relation, sentiment_score, anti_bot_score
            FROM equities_events
            WHERE session_relation IN ('postclose', 'published_preopen', 'weekend')
              AND symbol = ANY(%s)
              AND available_at_ts >= %s - INTERVAL '5 days'
            ORDER BY available_at_ts
        """, (symbols, timestamps[0]))

        # Per-slot accumulators (set-based dedup for publishers)
        slot_pubs = defaultdict(set)
        slot_sents = defaultdict(list)
        slot_counts = defaultdict(int)
        slot_tones = defaultdict(list)         # nonzero tone scores only
        slot_tone_eligible = defaultdict(int)  # all OPEN_NEWS count

        rows_fetched = 0
        for row in cur.fetchall():
            sym, title, source, publisher, available_at_ts, session_rel, sentiment, anti_bot = row
            rows_fetched += 1

            if sym not in sym_idx:
                continue
            n_col = sym_idx[sym]

            # Convert to ET date (DST-safe)
            if hasattr(available_at_ts, 'astimezone'):
                et_date = available_at_ts.astimezone(_ET).date()
            elif hasattr(available_at_ts, 'date'):
                et_date = available_at_ts.date()
            else:
                continue

            # Determine target bar index using trading-day calendar
            if session_rel in ('postclose', 'weekend'):
                target_idx = next_td_after.get(et_date)
            else:  # published_preopen
                target_idx = date_to_idx.get(et_date)

            if target_idx is None:
                continue

            key = (target_idx, n_col)
            slot_counts[key] += 1
            if publisher:
                slot_pubs[key].add(publisher)
            if sentiment is not None:
                slot_sents[key].append(float(sentiment))

            # Tone scoring (OPEN_NEWS only — scorer handles source/anti_bot/min_tokens)
            if source == 'OPEN_NEWS':
                slot_tone_eligible[key] += 1
                if _score_fn is not None:
                    tone = _score_fn(
                        title or "",
                        source=source or "",
                        anti_bot_score=float(anti_bot or 0.0),
                    )
                    if abs(tone) > 0:
                        slot_tones[key].append(tone)

        # Fill matrix from accumulators
        for key, count in slot_counts.items():
            t, n_col = key
            if t < 0 or t >= T:
                continue
            atc_mat[t, n_col, 0] = count
            atc_mat[t, n_col, 1] = len(slot_pubs.get(key, set()))

            sents = slot_sents.get(key, [])
            if sents:
                atc_mat[t, n_col, 2] = np.mean(sents)
                atc_mat[t, n_col, 3] = np.std(sents) if len(sents) > 1 else 0.0
                atc_mat[t, n_col, 4] = max(abs(s) for s in sents)

            tones = slot_tones.get(key, [])
            if tones:
                atc_mat[t, n_col, 5] = np.mean(tones)
            atc_mat[t, n_col, 6] = slot_tone_eligible.get(key, 0)
            atc_mat[t, n_col, 7] = len(tones)

        cur.close()

        filled = int((atc_mat[:, :, 0] > 0).sum())
        tone_slots = int((atc_mat[:, :, 7] > 0).sum())
        open_news_total = int(atc_mat[:, :, 6].sum())
        tone_nonzero_total = int(atc_mat[:, :, 7].sum())
        logger.info(
            "ATC matrix: %d/%d slots filled, %d with tone data (%d OPEN_NEWS events scored)",
            filled, T * N, tone_slots, open_news_total,
        )
        if open_news_total > 0:
            logger.info(
                "ATC tone: %d/%d OPEN_NEWS had directional keywords (%.1f%%)",
                tone_nonzero_total, open_news_total,
                100.0 * tone_nonzero_total / max(open_news_total, 1),
            )

    except Exception as e:
        logger.warning("ATC matrix load failed (table may not exist): %s", e)

    return atc_mat


def engineer_features(ohlcv: dict, news_mat: np.ndarray, atc_mat: np.ndarray = None) -> dict[str, np.ndarray]:
    """Compute all features from OHLCV, news, and ATC matrices.

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

    # Overnight gap: today's open vs yesterday's close
    overnight_gap = O / _lag(C, 1) - 1.0
    features["overnight_gap"] = overnight_gap

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

    # ATC (After-Trading Chatter) features
    if atc_mat is not None:
        atc_event_count = atc_mat[:, :, 0]
        atc_unique_pubs = atc_mat[:, :, 1]
        features["atc_event_count"] = atc_event_count
        features["atc_unique_pubs"] = atc_unique_pubs
        features["atc_source_diversity"] = atc_unique_pubs / np.maximum(atc_event_count, 1.0)
        features["atc_has_chatter"] = (atc_event_count > 0).astype(float)

        # Velocity: event count relative to 20d rolling mean
        atc_mean_20 = _rolling_mean(atc_event_count, 20)
        atc_velocity = atc_event_count / np.maximum(atc_mean_20, 0.1)
        features["atc_velocity"] = atc_velocity

        # Velocity z-score for confidence scaling
        vel_mean_20 = _rolling_mean(atc_velocity, 20)
        vel_std_20 = _rolling_std(atc_velocity, 20)
        features["atc_velocity_z"] = (atc_velocity - vel_mean_20) / np.maximum(vel_std_20, 0.01)

        # Tone features (v2 — keyword scoring of OPEN_NEWS titles)
        if atc_mat.shape[2] >= 8:
            tone_avg = atc_mat[:, :, 5]       # mean of nonzero tone scores
            tone_eligible = atc_mat[:, :, 6]  # total OPEN_NEWS count
            tone_nonzero = atc_mat[:, :, 7]   # directional OPEN_NEWS count

            features["atc_tone"] = tone_avg
            features["atc_tone_eligible_count"] = tone_eligible
            features["atc_tone_nonzero_count"] = tone_nonzero

            # Breadth: fraction of OPEN_NEWS events with directional keywords
            breadth = tone_nonzero / np.maximum(tone_eligible, 1.0)
            features["atc_tone_breadth"] = breadth

            # Agreement with momentum direction (NaN when no directional tone data)
            has_directional = tone_nonzero > 0
            agrees = np.where(
                has_directional,
                (np.sign(tone_avg) == np.sign(ret_1)).astype(float),
                np.nan,
            )
            features["atc_tone_agrees_momentum"] = agrees

            # Combined conviction: direction strength × breadth
            features["atc_tone_strength"] = np.abs(tone_avg) * breadth

    # Clip return-based and ratio-based features to [-100, 100].
    # Skip price-level features (high_5d, low_5d), cumulative features (obv),
    # and count features that can legitimately exceed 100.
    _NO_CLIP = {"high_5d", "low_5d", "obv", "atc_event_count", "atc_unique_pubs",
                "atc_tone_eligible_count", "atc_tone_nonzero_count"}
    for k in features:
        if k.startswith("_") or k in _NO_CLIP:
            continue
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
