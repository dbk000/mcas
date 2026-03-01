"""
Social Feature Library — Pure computation functions for social + OHLCV features.

Used by:
  - WS2 sentiment-price correlation study (Backend/scripts/ws2_sentiment_price_study.py)
  - Future live signal evaluation

All functions are pure (no DB calls). They operate on pre-loaded numpy arrays
and post lists. Designed for both batch (study) and single-point (live) use.

Channel categorization uses source + channel together to separate organic
community discussion from automated whale bot alerts that REACT to price
(Brain 1 contamination — see WS2 study design notes).
"""

import bisect
import numpy as np
from typing import Optional


# ============================================================================
# Channel categorization
# ============================================================================

# Known whale bot channels that produce automated transfer notifications.
# These posts REACT to price moves and contaminate frequency features.
WHALE_CHANNELS = frozenset({
    "WhaleBotAlerts",
    "WhaleBotPumps",
    "whale_alert_io",
    "WhaleAlert",
    "whalebotalerts",
    "whale_alert",
})

# Source-level categorization (when channel isn't informative)
SOURCE_CATEGORIES = {
    "coingecko": "news",
    "rss": "news",
}

# Known news telegram channels
NEWS_CHANNELS = frozenset({
    "CoinDesk",
    "Cointelegraph",
    "TheBlock",
    "decrypt_co",
    "crypto_news",
})


def is_whale_channel(channel: str) -> bool:
    """Check if a channel is a known whale bot alert channel."""
    return channel in WHALE_CHANNELS


def categorize_channel(source: str, channel: str) -> str:
    """Categorize a post's source+channel into content type.

    Categories: 'whale', 'news', 'community', 'reddit', 'other'

    Uses source AND channel together — telegram channels can be whale bots,
    news outlets, or community groups. Reddit is always 'reddit'.
    """
    if is_whale_channel(channel):
        return "whale"
    if source == "reddit":
        return "reddit"
    if source in SOURCE_CATEGORIES:
        return SOURCE_CATEGORIES[source]
    if source == "telegram":
        if channel in NEWS_CHANNELS:
            return "news"
        return "community"
    return "other"


# ============================================================================
# Sentiment frequency features (organic posts only by default)
# ============================================================================

def _filter_posts(posts: list[dict], timestamp_ms: int,
                  window_hours: float, organic_only: bool) -> list[dict]:
    """Filter posts within window before timestamp, optionally organic only."""
    cutoff_ms = timestamp_ms - int(window_hours * 3600 * 1000)
    result = []
    for p in posts:
        pts = p["post_ts"]
        if cutoff_ms <= pts < timestamp_ms:
            if organic_only and is_whale_channel(p.get("channel", "")):
                continue
            result.append(p)
    return result


def compute_mention_rolling(posts: list[dict], timestamp_ms: int,
                            windows: list[int] = None,
                            organic_only: bool = True) -> dict:
    """Count mentions in rolling windows (hours) before timestamp.

    Returns: {"mention_count_1h": N, "mention_count_4h": N, ...}
    """
    if windows is None:
        windows = [1, 4, 12, 24]
    result = {}
    for w in windows:
        count = len(_filter_posts(posts, timestamp_ms, w, organic_only))
        result[f"mention_count_{w}h"] = count
    return result


def compute_mention_velocity(posts: list[dict], timestamp_ms: int,
                             organic_only: bool = True) -> Optional[float]:
    """Ratio of current hour mentions to previous hour mentions.

    Returns None if previous hour has zero mentions.
    """
    current = len(_filter_posts(posts, timestamp_ms, 1, organic_only))
    prev_start = timestamp_ms - 2 * 3600 * 1000
    prev_end = timestamp_ms - 1 * 3600 * 1000
    prev_count = 0
    for p in posts:
        pts = p["post_ts"]
        if prev_start <= pts < prev_end:
            if organic_only and is_whale_channel(p.get("channel", "")):
                continue
            prev_count += 1
    if prev_count == 0:
        return None
    return current / prev_count


def compute_mention_acceleration(posts: list[dict], timestamp_ms: int,
                                 organic_only: bool = True) -> Optional[float]:
    """Ratio of current velocity to previous velocity.

    Returns None if either velocity is unavailable.
    """
    vel_now = compute_mention_velocity(posts, timestamp_ms, organic_only)
    vel_prev = compute_mention_velocity(
        posts, timestamp_ms - 3600 * 1000, organic_only
    )
    if vel_now is None or vel_prev is None or vel_prev == 0:
        return None
    return vel_now / vel_prev


def compute_mention_z_score(posts: list[dict], timestamp_ms: int,
                            lookback_hours: int = 168,
                            organic_only: bool = True) -> Optional[float]:
    """Z-score of current hour mentions vs rolling lookback mean/std.

    Returns None if fewer than 6 hourly data points in lookback.
    """
    current = len(_filter_posts(posts, timestamp_ms, 1, organic_only))

    # Build hourly counts over lookback
    hourly_counts = []
    for h in range(1, lookback_hours + 1):
        t_end = timestamp_ms - (h - 1) * 3600 * 1000
        t_start = timestamp_ms - h * 3600 * 1000
        count = 0
        for p in posts:
            pts = p["post_ts"]
            if t_start <= pts < t_end:
                if organic_only and is_whale_channel(p.get("channel", "")):
                    continue
                count += 1
        hourly_counts.append(count)

    if len(hourly_counts) < 6:
        return None
    arr = np.array(hourly_counts, dtype=float)
    mean = arr.mean()
    std = arr.std()
    if std == 0:
        return 0.0
    return float((current - mean) / std)


def compute_time_since_first_mention(posts: list[dict], timestamp_ms: int,
                                     organic_only: bool = True) -> Optional[float]:
    """Hours since first organic mention for this symbol.

    Returns None if no mentions exist before timestamp.
    """
    earliest = None
    for p in posts:
        pts = p["post_ts"]
        if pts >= timestamp_ms:
            continue
        if organic_only and is_whale_channel(p.get("channel", "")):
            continue
        if earliest is None or pts < earliest:
            earliest = pts
    if earliest is None:
        return None
    return (timestamp_ms - earliest) / (3600 * 1000)


# ============================================================================
# Sentiment content features (all posts, categorized)
# ============================================================================

def compute_content_proportions(posts: list[dict], timestamp_ms: int,
                                window_hours: int = 24) -> dict:
    """Proportion of posts by content category in window.

    Returns: {"prop_whale_alert": 0.3, "prop_news": 0.2, ...}
    Uses ALL posts (including whale) — this IS the categorization feature.
    """
    cats = {"whale_alert": 0, "news": 0, "community": 0, "reddit": 0, "other": 0}
    # Map internal category names to output names
    cat_map = {"whale": "whale_alert", "news": "news", "community": "community",
               "reddit": "reddit", "other": "other"}
    cutoff_ms = timestamp_ms - window_hours * 3600 * 1000
    total = 0
    for p in posts:
        if cutoff_ms <= p["post_ts"] < timestamp_ms:
            cat = categorize_channel(p.get("source", ""), p.get("channel", ""))
            output_cat = cat_map.get(cat, "other")
            cats[output_cat] = cats.get(output_cat, 0) + 1
            total += 1
    if total == 0:
        return {f"prop_{k}": 0.0 for k in cats}
    return {f"prop_{k}": v / total for k, v in cats.items()}


def compute_content_diversity(posts: list[dict], timestamp_ms: int,
                              window_hours: int = 24) -> int:
    """Count of distinct content categories present (0-5) in window."""
    cats = set()
    cutoff_ms = timestamp_ms - window_hours * 3600 * 1000
    for p in posts:
        if cutoff_ms <= p["post_ts"] < timestamp_ms:
            cat = categorize_channel(p.get("source", ""), p.get("channel", ""))
            cats.add(cat)
    return len(cats)


def compute_whale_polarity(posts: list[dict], timestamp_ms: int,
                           window_hours: int = 24) -> tuple[int, int]:
    """Count bullish vs bearish whale alert posts via polarity regex.

    Returns (bullish_count, bearish_count).
    Only processes posts from whale channels that have content.
    """
    import re
    bullish_re = re.compile(
        r"(?:bought|buy|accumul|transfer.*to.*exchange|deposit)",
        re.IGNORECASE,
    )
    bearish_re = re.compile(
        r"(?:sold|sell|dump|transfer.*from.*exchange|withdraw)",
        re.IGNORECASE,
    )
    cutoff_ms = timestamp_ms - window_hours * 3600 * 1000
    bullish = 0
    bearish = 0
    for p in posts:
        if cutoff_ms <= p["post_ts"] < timestamp_ms:
            if not is_whale_channel(p.get("channel", "")):
                continue
            content = p.get("content", "") or p.get("title", "") or ""
            if bullish_re.search(content):
                bullish += 1
            if bearish_re.search(content):
                bearish += 1
    return bullish, bearish


def compute_whale_alert_present(posts: list[dict], timestamp_ms: int,
                                window_hours: int = 24) -> bool:
    """Whether any whale alert post exists in the window."""
    cutoff_ms = timestamp_ms - window_hours * 3600 * 1000
    for p in posts:
        if cutoff_ms <= p["post_ts"] < timestamp_ms:
            if is_whale_channel(p.get("channel", "")):
                return True
    return False


def compute_dominant_category(posts: list[dict], timestamp_ms: int,
                              window_hours: int = 24,
                              exclude_whale: bool = True) -> Optional[str]:
    """Most frequent content category in window (whale-excluded by default)."""
    cats = {}
    cutoff_ms = timestamp_ms - window_hours * 3600 * 1000
    for p in posts:
        if cutoff_ms <= p["post_ts"] < timestamp_ms:
            cat = categorize_channel(p.get("source", ""), p.get("channel", ""))
            if exclude_whale and cat == "whale":
                continue
            cats[cat] = cats.get(cat, 0) + 1
    if not cats:
        return None
    return max(cats, key=cats.get)


def compute_content_type_shift(posts: list[dict], timestamp_ms: int) -> bool:
    """Whether dominant category changed in last 4h vs previous 4h."""
    recent = compute_dominant_category(posts, timestamp_ms, window_hours=4)
    prev = compute_dominant_category(
        posts, timestamp_ms - 4 * 3600 * 1000, window_hours=4
    )
    if recent is None or prev is None:
        return False
    return recent != prev


# ============================================================================
# Cross-domain features
# ============================================================================

def compute_sentiment_volume_divergence(mention_z: Optional[float],
                                        volume_z: Optional[float]) -> Optional[float]:
    """Organic mention_z minus volume_z. Positive = social leads price."""
    if mention_z is None or volume_z is None:
        return None
    return mention_z - volume_z


def compute_sentiment_lead_lag(posts: list[dict], timestamp_ms: int,
                               price_changes_1h: np.ndarray,
                               price_ts_ms: np.ndarray,
                               lookback_hours: int = 48,
                               organic_only: bool = True) -> Optional[float]:
    """Cross-correlation peak lag between mention_velocity and price_change_1h.

    NOTE: Only ~48 points per computation — noisy. Use for exploration only.
    Returns lag in hours at max cross-correlation, or None if insufficient data.
    """
    n_hours = lookback_hours
    mention_series = []
    price_series = []
    start_ms = timestamp_ms - n_hours * 3600 * 1000

    for h in range(n_hours):
        t = start_ms + (h + 1) * 3600 * 1000
        vel = compute_mention_velocity(posts, t, organic_only)
        mention_series.append(vel if vel is not None else 0.0)

        # Find closest price change
        idx = bisect.bisect_right(price_ts_ms, t) - 1
        if 0 <= idx < len(price_changes_1h):
            price_series.append(price_changes_1h[idx])
        else:
            price_series.append(0.0)

    m_arr = np.array(mention_series)
    p_arr = np.array(price_series)

    if np.std(m_arr) == 0 or np.std(p_arr) == 0:
        return None

    # Normalized cross-correlation
    m_norm = (m_arr - m_arr.mean()) / m_arr.std()
    p_norm = (p_arr - p_arr.mean()) / p_arr.std()
    corr = np.correlate(m_norm, p_norm, mode="full") / n_hours
    lags = np.arange(-(n_hours - 1), n_hours)
    peak_idx = np.argmax(np.abs(corr))
    return float(lags[peak_idx])


# ============================================================================
# OHLCV indicator features (per timeframe)
# ============================================================================

def compute_ohlcv_features(closes: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray, volumes: np.ndarray,
                           bar_idx: int, tf: str) -> dict:
    """Compute OHLCV-derived features at bar_idx for a given timeframe.

    Features: bb_width_{tf}, volume_z_{tf}, bb_position_{tf}, rsi_14_{tf}

    Args:
        closes, highs, lows, volumes: full numpy arrays for this symbol+tf
        bar_idx: index into arrays at which to compute (uses bars up to and including bar_idx)
        tf: timeframe string for namespacing (e.g., "1h", "5m")

    Returns dict of {feature_name: value}. Values are None if insufficient data.
    """
    result = {}
    n_bb = 20
    k_bb = 2.0
    n_vol = 24  # 24-bar rolling for volume z
    n_rsi = 14

    # Need at least n_bb bars
    start = max(0, bar_idx - 200)
    c = closes[start:bar_idx + 1]
    v = volumes[start:bar_idx + 1]

    # BB width
    if len(c) >= n_bb:
        sma = np.mean(c[-n_bb:])
        std = np.std(c[-n_bb:])
        bb_ub = sma + k_bb * std
        bb_lb = sma - k_bb * std
        result[f"bb_width_{tf}"] = float((bb_ub - bb_lb) / sma) if sma != 0 else None
        # BB position
        if bb_ub != bb_lb:
            result[f"bb_position_{tf}"] = float((c[-1] - bb_lb) / (bb_ub - bb_lb))
        else:
            result[f"bb_position_{tf}"] = 0.5
    else:
        result[f"bb_width_{tf}"] = None
        result[f"bb_position_{tf}"] = None

    # Volume Z
    if len(v) >= n_vol:
        window = v[-n_vol:]
        vmean = np.mean(window)
        vstd = np.std(window)
        result[f"volume_z_{tf}"] = float((v[-1] - vmean) / vstd) if vstd > 0 else 0.0
    else:
        result[f"volume_z_{tf}"] = None

    # RSI 14
    if len(c) >= n_rsi + 1:
        deltas = np.diff(c[-(n_rsi + 1):])
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            result[f"rsi_14_{tf}"] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[f"rsi_14_{tf}"] = float(100.0 - (100.0 / (1.0 + rs)))
    else:
        result[f"rsi_14_{tf}"] = None

    return result


# ============================================================================
# Price change features (timeframe-independent, computed from 1h data)
# ============================================================================

def compute_price_changes(closes_1h: np.ndarray, ts_1h: np.ndarray,
                          timestamp_ms: int) -> dict:
    """Close-to-close returns over fixed time horizons from 1h data.

    Returns: {"price_change_1h": pct, "price_change_4h": pct, ...}
    """
    result = {}
    idx = bisect.bisect_right(ts_1h, timestamp_ms) - 1
    if idx < 0 or idx >= len(closes_1h):
        return {f"price_change_{w}h": None for w in [1, 4, 12, 24]}

    current = closes_1h[idx]
    for hours in [1, 4, 12, 24]:
        past_idx = idx - hours
        if past_idx >= 0 and current > 0:
            result[f"price_change_{hours}h"] = float(
                (current - closes_1h[past_idx]) / closes_1h[past_idx]
            )
        else:
            result[f"price_change_{hours}h"] = None
    return result


# ============================================================================
# Funding rate feature
# ============================================================================

def compute_funding_rate(funding_rates: list[tuple], timestamp_ms: int) -> Optional[float]:
    """Most recent funding rate before timestamp via bisect lookup.

    Args:
        funding_rates: sorted list of (ts_ms, rate) tuples
        timestamp_ms: event timestamp in ms

    Returns funding rate or None if no data.
    """
    if not funding_rates:
        return None
    ts_list = [fr[0] for fr in funding_rates]
    idx = bisect.bisect_right(ts_list, timestamp_ms) - 1
    if idx < 0:
        return None
    # Only use if within 24h
    if timestamp_ms - funding_rates[idx][0] > 24 * 3600 * 1000:
        return None
    return float(funding_rates[idx][1])


# ============================================================================
# Liveability annotations
# ============================================================================

LIVEABLE_FEATURES = {
    # OHLCV-derived — available in real-time from Binance WS
    "bb_width", "volume_z", "bb_position", "rsi_14",
    "price_change", "funding_rate",
}

NON_LIVEABLE_FEATURES = {
    # Sentiment features — require real-time ingestion not currently available
    "mention_count", "mention_velocity", "mention_acceleration",
    "mention_z_score", "time_since_first_mention",
    "prop_whale_alert", "prop_news", "prop_community", "prop_reddit", "prop_other",
    "content_diversity", "whale_alert_present", "whale_bullish_count",
    "whale_bearish_count", "dominant_content_category",
    "sentiment_volume_divergence", "sentiment_lead_lag", "content_type_shift",
}


def is_feature_liveable(feature_name: str) -> bool:
    """Check if a feature can be computed in real-time with current infrastructure.

    OHLCV + funding features are liveable (Binance WS + REST).
    Sentiment features require real-time social ingestion (97.8% of current
    social data was backfilled months after publication).
    """
    base = feature_name.split("_")[0]
    # Check prefixes
    for prefix in LIVEABLE_FEATURES:
        if feature_name.startswith(prefix):
            return True
    for prefix in NON_LIVEABLE_FEATURES:
        if feature_name.startswith(prefix):
            return False
    return True  # Default to liveable for unknown features
