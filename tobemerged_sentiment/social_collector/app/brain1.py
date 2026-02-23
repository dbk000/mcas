"""
Brain 1 — Hourly social feature aggregation.

Runs at :05 past each hour. Aggregates social_posts for the hour that just
ended into social_features_1h rows (one per symbol). Computes velocity,
acceleration, author novelty, first-mention flags, and mention-volume divergence.
"""

import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from db import get_connection, upsert_social_features

logger = logging.getLogger("brain1")


def aggregate_hour(hour_start: datetime) -> int:
    """Aggregate social_posts for one hour into social_features_1h.
    Returns number of symbols aggregated."""
    hour_end = hour_start + timedelta(hours=1)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Get all posts for this hour with their symbols
            cur.execute("""
                SELECT symbols, author, source, engagement_score
                FROM social_posts
                WHERE post_ts >= %s AND post_ts < %s
            """, (hour_start, hour_end))

            # 2. Explode symbols array — one post mentioning [A, B] counts for both
            symbol_data = defaultdict(lambda: {
                "authors": set(), "platforms": set(),
                "count": 0, "engagement": 0
            })
            for symbols, author, source, engagement in cur:
                if symbols:
                    for sym in symbols:
                        d = symbol_data[sym]
                        d["count"] += 1
                        d["authors"].add(author)
                        d["platforms"].add(source)
                        d["engagement"] += (engagement or 0)

            if not symbol_data:
                logger.info("Brain 1: no posts found for hour %s", hour_start)
                return 0

            # 3. For each symbol, compute features
            rows = []
            for symbol, data in symbol_data.items():
                mention_count = data["count"]
                unique_authors = len(data["authors"] - {None})
                platform_count = len(data["platforms"])
                engagement_sum = data["engagement"]

                # Get previous hour's data for velocity
                cur.execute("""
                    SELECT mention_count, mention_velocity
                    FROM social_features_1h
                    WHERE symbol = %s AND bar_start_ts = %s
                """, (symbol, hour_start - timedelta(hours=1)))
                prev = cur.fetchone()

                prev_count = prev[0] if prev else 0
                prev_velocity = prev[1] if prev else None

                mention_velocity = (mention_count / prev_count) if prev_count > 0 else None
                mention_acceleration = None
                if mention_velocity is not None and prev_velocity is not None and prev_velocity > 0:
                    mention_acceleration = mention_velocity / prev_velocity

                # Author novelty: how many authors are new for this symbol?
                cur.execute("""
                    SELECT DISTINCT author FROM social_posts
                    WHERE %s = ANY(symbols) AND post_ts < %s AND author IS NOT NULL
                    LIMIT 5000
                """, (symbol, hour_start))
                historical_authors = {row[0] for row in cur}
                new_authors = data["authors"] - historical_authors - {None}
                author_novelty_ratio = len(new_authors) / unique_authors if unique_authors > 0 else 0.0

                # First mention ever?
                cur.execute("""
                    SELECT 1 FROM social_features_1h
                    WHERE symbol = %s AND bar_start_ts < %s LIMIT 1
                """, (symbol, hour_start))
                first_mention = cur.fetchone() is None

                # mention_volume_ratio: social_z - volume_z
                mention_volume_ratio = _compute_mention_volume_ratio(
                    cur, symbol, hour_start, mention_count
                )

                rows.append((
                    symbol, hour_start, mention_count, unique_authors, platform_count,
                    engagement_sum, mention_velocity, mention_acceleration,
                    author_novelty_ratio, first_mention, mention_volume_ratio
                ))

    # Batch upsert outside the read connection
    if rows:
        upsert_social_features(rows)

    logger.info("Brain 1: aggregated %d symbols for %s", len(rows), hour_start)
    return len(rows)


def _compute_mention_volume_ratio(cur, symbol: str, hour_start: datetime,
                                   mention_count: int):
    """Compute mention_z - volume_z for social-volume divergence detection.
    Returns None if insufficient history."""
    # Need 24h of social history
    cur.execute("""
        SELECT mention_count
        FROM social_features_1h
        WHERE symbol = %s
          AND bar_start_ts >= %s
          AND bar_start_ts < %s
        ORDER BY bar_start_ts
    """, (symbol, hour_start - timedelta(hours=24), hour_start))
    mention_history = [row[0] for row in cur]

    if len(mention_history) < 6:
        return None

    import numpy as np
    mention_arr = np.array(mention_history, dtype=float)
    m_mean = mention_arr.mean()
    m_std = mention_arr.std()
    if m_std == 0:
        mention_z = 0.0
    else:
        mention_z = (mention_count - m_mean) / m_std

    # Get OHLCV volume for the same hour
    cur.execute("""
        SELECT volume FROM ohlcv
        WHERE symbol = %s AND timeframe = '1h' AND bar_start_ts = %s
        LIMIT 1
    """, (symbol, hour_start))
    vol_row = cur.fetchone()
    if not vol_row:
        return None
    current_volume = vol_row[0]

    # 24h volume history
    cur.execute("""
        SELECT volume FROM ohlcv
        WHERE symbol = %s AND timeframe = '1h'
          AND bar_start_ts >= %s
          AND bar_start_ts < %s
        ORDER BY bar_start_ts
    """, (symbol, hour_start - timedelta(hours=24), hour_start))
    vol_history = [row[0] for row in cur]

    if len(vol_history) < 6:
        return None

    vol_arr = np.array(vol_history, dtype=float)
    v_mean = vol_arr.mean()
    v_std = vol_arr.std()
    if v_std == 0:
        volume_z = 0.0
    else:
        volume_z = (current_volume - v_mean) / v_std

    return mention_z - volume_z
