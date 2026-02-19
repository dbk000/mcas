#!/usr/bin/env python3
"""
Social Data Historical Backfill
================================

Populates social_posts with 365 days of Reddit (via Arctic Shift) and
crypto news (via CryptoCompare), then materializes social_features_1h
for the entire window including mention_volume_ratio (social-OHLCV divergence).

Usage (inside worker container):
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/backfill_social_posts.py --days 365 --source all

Or on host (if psycopg + requests + numpy available):
    python3 backfill_social_posts.py --days 365 --source all \
        --db-url "postgresql://trading:PASSWORD@172.18.0.11:5432/trading"
"""

import os
import re
import sys
import time
import json
import logging
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import requests
import psycopg
import numpy as np

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("backfill")

# ─── Crypto context words (from social_collector/app/config.py) ───────────────

CRYPTO_CONTEXT_WORDS = {
    "token", "coin", "crypto", "listing", "binance", "exchange", "pump",
    "dump", "chart", "buy", "sell", "moon", "bullish", "bearish", "hodl",
    "defi", "dex", "cex", "airdrop", "staking", "yield", "tvl", "nft",
    "blockchain", "wallet", "whale", "altcoin", "usdt", "usdc", "btc",
    "eth", "trading", "futures", "leverage", "long", "short", "liquidation",
    "mcap", "market cap", "volume", "price", "ath", "support", "resistance",
    "breakout", "dip", "rally", "accumulation", "distribution",
}

# ─── SymbolMatcher (copied from social_collector/app/symbol_matcher.py) ───────

class SymbolMatcher:
    def __init__(self, aliases: dict):
        self.aliases = aliases
        self.cashtag_re = re.compile(r'\$([A-Z]{2,10})\b')
        self.hashtag_re = re.compile(r'#([A-Z]{2,10})\b')
        self.ticker_re = re.compile(r'\b([A-Z]{2,10})\b')
        self.unambiguous_tickers = set()
        self.ambiguous_tickers = set()
        for alias_lower, entries in aliases.items():
            for symbol, alias_type, is_ambiguous in entries:
                if alias_type == 'ticker':
                    if is_ambiguous:
                        self.ambiguous_tickers.add(alias_lower.upper())
                    else:
                        self.unambiguous_tickers.add(alias_lower.upper())

    def extract_symbols(self, text: str) -> list[str]:
        if not text:
            return []
        matched = set()
        text_lower = text.lower()
        for m in self.cashtag_re.finditer(text):
            ticker = m.group(1)
            key = '$' + ticker.lower()
            if key in self.aliases:
                for symbol, _, _ in self.aliases[key]:
                    matched.add(symbol)
            key2 = ticker.lower()
            if key2 in self.aliases:
                for symbol, _, _ in self.aliases[key2]:
                    matched.add(symbol)
        # Tier 1b: Hashtag tickers (#BTC, #ETH) — Whale Alert format
        for m in self.hashtag_re.finditer(text):
            ticker = m.group(1)
            key = ticker.lower()
            if key in self.aliases:
                for symbol, _, _ in self.aliases[key]:
                    matched.add(symbol)
        for m in self.ticker_re.finditer(text):
            ticker = m.group(1)
            if ticker in self.unambiguous_tickers:
                key = ticker.lower()
                if key in self.aliases:
                    for symbol, alias_type, _ in self.aliases[key]:
                        if alias_type == 'ticker':
                            matched.add(symbol)
        for m in self.ticker_re.finditer(text):
            ticker = m.group(1)
            if ticker in self.ambiguous_tickers:
                start = max(0, m.start() - 100)
                end = min(len(text), m.end() + 100)
                context = text_lower[start:end]
                if any(cw in context for cw in CRYPTO_CONTEXT_WORDS):
                    key = ticker.lower()
                    if key in self.aliases:
                        for symbol, alias_type, _ in self.aliases[key]:
                            if alias_type == 'ticker':
                                matched.add(symbol)
        for alias_lower, entries in self.aliases.items():
            if len(alias_lower) >= 5 and not alias_lower.startswith('$'):
                for symbol, alias_type, _ in entries:
                    if alias_type in ('name', 'common'):
                        if re.search(r'\b' + re.escape(alias_lower) + r'\b', text_lower):
                            matched.add(symbol)
        return sorted(matched)

# ─── DB Helpers ───────────────────────────────────────────────────────────────

def load_symbol_aliases_direct(database_url: str) -> dict:
    aliases = {}
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, alias, alias_type, is_ambiguous FROM symbol_aliases")
            for symbol, alias, alias_type, is_ambiguous in cur:
                key = alias.lower()
                if key not in aliases:
                    aliases[key] = []
                aliases[key].append((symbol, alias_type, is_ambiguous))
    return aliases


def batch_insert_posts(database_url: str, posts: list[dict]) -> int:
    if not posts:
        return 0
    sql = """
        INSERT INTO social_posts (source, source_id, channel, author, title, content,
                                  post_ts, engagement_score, symbols, batch_id)
        VALUES (%(source)s, %(source_id)s, %(channel)s, %(author)s, %(title)s, %(content)s,
                %(post_ts)s, %(engagement_score)s, %(symbols)s, %(batch_id)s)
        ON CONFLICT (source, source_id) DO NOTHING
    """
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, posts)
        conn.commit()
    return len(posts)


# ─── Reddit Backfiller (Arctic Shift / PullPush) ─────────────────────────────

ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
PULLPUSH_URL = "https://api.pullpush.io/reddit/search/submission/"

REDDIT_SUBREDDITS = [
    "CryptoCurrency", "CryptoMoonShots", "altcoin", "SatoshiStreetBets",
    "CryptoMarkets", "defi", "binance", "ethtrader", "solana",
    "cardano", "algorand", "Chainlink", "Polkadot", "0xPolygon",
    "avax", "cosmosnetwork", "UniSwap", "pancakeswap",
]


class RedditBackfiller:
    def __init__(self, matcher: SymbolMatcher, database_url: str,
                 source_label: str = "reddit_historical"):
        self.matcher = matcher
        self.database_url = database_url
        self.source_label = source_label
        self.api_url = None
        self.api_type = None
        self._detect_api()

    def _detect_api(self):
        """Test which Reddit archive API is available."""
        # Arctic Shift
        try:
            resp = requests.get(ARCTIC_SHIFT_URL,
                                params={"subreddit": "CryptoCurrency", "limit": 1, "sort": "desc"},
                                timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                posts = data.get("data", [])
                if isinstance(posts, list) and len(posts) > 0:
                    self.api_url = ARCTIC_SHIFT_URL
                    self.api_type = "arctic_shift"
                    logger.info("Using Arctic Shift API")
                    return
        except Exception as e:
            logger.warning("Arctic Shift unavailable: %s", e)

        # PullPush fallback
        try:
            resp = requests.get(PULLPUSH_URL,
                                params={"subreddit": "CryptoCurrency", "size": 1,
                                        "sort": "created_utc", "order": "desc"},
                                timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                posts = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(posts, list) and len(posts) > 0:
                    self.api_url = PULLPUSH_URL
                    self.api_type = "pullpush"
                    logger.info("Using PullPush API (fallback)")
                    return
        except Exception as e:
            logger.warning("PullPush unavailable: %s", e)

        raise RuntimeError("No Reddit archive API available (tried Arctic Shift + PullPush)")

    def fetch_window(self, subreddit: str, after_ts: int, before_ts: int,
                     limit: int = 100) -> list[dict]:
        """Fetch submissions in a time window. Returns raw API records."""
        all_posts = []
        cursor_ts = after_ts
        retries = 0

        while cursor_ts < before_ts:
            if self.api_type == "arctic_shift":
                params = {
                    "subreddit": subreddit,
                    "after": cursor_ts,
                    "before": before_ts,
                    "limit": limit,
                    "sort": "asc",
                }
            else:  # pullpush
                params = {
                    "subreddit": subreddit,
                    "after": cursor_ts,
                    "before": before_ts,
                    "size": limit,
                    "sort": "created_utc",
                    "order": "asc",
                }

            try:
                resp = requests.get(self.api_url, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = min(60, 2 ** retries)
                    logger.warning("Rate limited, waiting %ds", wait)
                    time.sleep(wait)
                    retries += 1
                    continue
                resp.raise_for_status()
                retries = 0

                data = resp.json()
                if self.api_type == "arctic_shift":
                    posts = data.get("data", [])
                else:
                    posts = data.get("data", data) if isinstance(data, dict) else data

                if not posts:
                    break

                all_posts.extend(posts)

                last_ts = max(p.get("created_utc", 0) for p in posts)
                if last_ts <= cursor_ts:
                    break
                cursor_ts = last_ts + 1

                # Polite rate limit for free APIs
                time.sleep(1.0)

            except requests.exceptions.RequestException as e:
                retries += 1
                if retries > 5:
                    logger.error("Too many retries for r/%s window %d-%d: %s",
                                 subreddit, after_ts, before_ts, e)
                    break
                wait = min(60, 2 ** retries)
                logger.warning("Request error (retry %d): %s, waiting %ds", retries, e, wait)
                time.sleep(wait)

        return all_posts

    def backfill_subreddit(self, subreddit: str, start_date: datetime,
                           end_date: datetime, batch_id_prefix: str) -> dict:
        """Backfill one subreddit. Returns stats dict."""
        stats = {"fetched": 0, "matched": 0, "inserted": 0}

        chunk_start = start_date
        chunk_days = 7

        while chunk_start < end_date:
            chunk_end = min(chunk_start + timedelta(days=chunk_days), end_date)
            after_ts = int(chunk_start.timestamp())
            before_ts = int(chunk_end.timestamp())

            batch_id = "%s_%s_%s" % (batch_id_prefix, subreddit,
                                     chunk_start.strftime('%Y%m%d'))

            try:
                raw_posts = self.fetch_window(subreddit, after_ts, before_ts, limit=100)
            except Exception as e:
                logger.error("Reddit fetch error r/%s %s-%s: %s",
                             subreddit, chunk_start.date(), chunk_end.date(), e)
                chunk_start = chunk_end
                continue

            stats["fetched"] += len(raw_posts)

            db_posts = []
            for p in raw_posts:
                title = (p.get("title", "") or "")
                selftext = (p.get("selftext", "") or "")
                text = title + " " + selftext
                symbols = self.matcher.extract_symbols(text)

                if not symbols:
                    continue

                created = p.get("created_utc", 0)
                if isinstance(created, float):
                    created = int(created)

                db_posts.append({
                    "source": self.source_label,
                    "source_id": str(p.get("id", "")),
                    "channel": subreddit,
                    "author": p.get("author"),
                    "title": title[:500],
                    "content": selftext[:2000],
                    "post_ts": datetime.fromtimestamp(created, tz=timezone.utc),
                    "engagement_score": p.get("score", 0) or 0,
                    "symbols": symbols,
                    "batch_id": batch_id,
                })

            stats["matched"] += len(db_posts)

            if db_posts:
                n = batch_insert_posts(self.database_url, db_posts)
                stats["inserted"] += n

            logger.info("  r/%s %s -> %s: fetched=%d matched=%d",
                        subreddit, chunk_start.strftime("%Y-%m-%d"),
                        chunk_end.strftime("%Y-%m-%d"),
                        len(raw_posts), len(db_posts))

            chunk_start = chunk_end

        return stats


# ─── News Backfiller (CryptoCompare) ─────────────────────────────────────────

CRYPTOCOMPARE_NEWS_URL = "https://min-api.cryptocompare.com/data/v2/news/"


class NewsBackfiller:
    def __init__(self, matcher: SymbolMatcher, database_url: str,
                 source_label: str = "news_historical"):
        self.matcher = matcher
        self.database_url = database_url
        self.source_label = source_label

    def backfill(self, start_date: datetime, end_date: datetime,
                 batch_id_prefix: str) -> dict:
        """Backfill crypto news by paginating backwards from end_date."""
        stats = {"fetched": 0, "matched": 0, "inserted": 0}

        cursor_ts = int(end_date.timestamp())
        start_ts = int(start_date.timestamp())
        retries = 0

        while cursor_ts > start_ts:
            try:
                resp = requests.get(CRYPTOCOMPARE_NEWS_URL, params={
                    "lang": "EN",
                    "sortOrder": "latest",
                    "lTs": cursor_ts,
                }, timeout=15)

                if resp.status_code == 429:
                    wait = min(60, 2 ** retries)
                    logger.warning("CryptoCompare rate limited, waiting %ds", wait)
                    time.sleep(wait)
                    retries += 1
                    continue

                resp.raise_for_status()
                data = resp.json()
                articles = data.get("Data", [])
                retries = 0

            except Exception as e:
                retries += 1
                if retries > 5:
                    logger.error("CryptoCompare: too many errors at ts=%d: %s", cursor_ts, e)
                    cursor_ts -= 86400
                    retries = 0
                    continue
                wait = min(60, 2 ** retries)
                logger.warning("CryptoCompare error (retry %d): %s, waiting %ds",
                               retries, e, wait)
                time.sleep(wait)
                continue

            if not articles:
                break

            batch_id = "%s_news_%d" % (batch_id_prefix, cursor_ts)
            db_posts = []

            for a in articles:
                pub_ts = a.get("published_on", 0)
                if pub_ts < start_ts:
                    continue

                title = (a.get("title", "") or "")
                body = (a.get("body", "") or "")
                text = title + " " + body
                symbols = self.matcher.extract_symbols(text)

                stats["fetched"] += 1

                if not symbols:
                    continue

                source_name = a.get("source_info", {}).get("name", "unknown")

                db_posts.append({
                    "source": self.source_label,
                    "source_id": str(a.get("id", "")),
                    "channel": source_name,
                    "author": a.get("source"),
                    "title": title[:500],
                    "content": body[:2000],
                    "post_ts": datetime.fromtimestamp(pub_ts, tz=timezone.utc),
                    "engagement_score": 0,
                    "symbols": symbols,
                    "batch_id": batch_id,
                })

            stats["matched"] += len(db_posts)

            if db_posts:
                n = batch_insert_posts(self.database_url, db_posts)
                stats["inserted"] += n

            # Advance cursor backwards
            oldest = min(a.get("published_on", cursor_ts) for a in articles)
            if oldest >= cursor_ts:
                break
            cursor_ts = oldest - 1

            # Rate limit
            time.sleep(0.5)

            if stats["fetched"] % 2000 == 0:
                dt = datetime.fromtimestamp(cursor_ts, tz=timezone.utc)
                logger.info("  News progress: fetched=%d matched=%d cursor=%s",
                            stats["fetched"], stats["matched"],
                            dt.strftime("%Y-%m-%d"))

        return stats


# ─── Brain 1 Bulk Materialization ─────────────────────────────────────────────

def materialize_features_bulk(database_url: str, start_date: datetime,
                               end_date: datetime) -> int:
    """Materialize social_features_1h for the entire backfill window.

    Loads ALL social_posts into memory, groups by (symbol, hour),
    computes features in Python, bulk upserts.
    """
    logger.info("Materializing social features: %s to %s", start_date, end_date)

    # Step 1: Load all posts
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT post_ts, symbols, author, source, engagement_score
                FROM social_posts
                WHERE post_ts >= %s AND post_ts < %s
                ORDER BY post_ts
            """, (start_date, end_date))
            posts = cur.fetchall()

    logger.info("Loaded %d posts for materialization", len(posts))
    if not posts:
        return 0

    # Step 2: Group by (symbol, hour_start)
    symbol_hour = defaultdict(lambda: {
        "authors": set(), "sources": set(), "count": 0, "engagement": 0
    })

    for post_ts, symbols_arr, author, source, engagement in posts:
        if not symbols_arr:
            continue
        hour_ts = post_ts.replace(minute=0, second=0, microsecond=0)
        for sym in symbols_arr:
            key = (sym, hour_ts)
            d = symbol_hour[key]
            d["count"] += 1
            if author:
                d["authors"].add(author)
            d["sources"].add(source)
            d["engagement"] += (engagement or 0)

    logger.info("Grouped into %d (symbol, hour) buckets", len(symbol_hour))

    # Step 3: Build per-symbol time series for velocity/acceleration
    sym_ts = defaultdict(dict)
    for (sym, hour_ts), data in symbol_hour.items():
        sym_ts[sym][hour_ts] = data["count"]

    # Step 4: Compute features chronologically
    all_hours = sorted(set(h for _, h in symbol_hour.keys()))
    all_symbols = sorted(set(s for s, _ in symbol_hour.keys()))

    sym_historical_authors = defaultdict(set)
    feature_rows = []

    for hour_ts in all_hours:
        for sym in all_symbols:
            key = (sym, hour_ts)
            if key not in symbol_hour:
                continue

            data = symbol_hour[key]
            mention_count = data["count"]
            unique_authors = len(data["authors"])
            platform_count = len(data["sources"])
            engagement_sum = data["engagement"]

            # Velocity
            prev_hour = hour_ts - timedelta(hours=1)
            prev_count = sym_ts[sym].get(prev_hour, 0)
            mention_velocity = (mention_count / prev_count) if prev_count > 0 else None

            # Acceleration
            prev_prev_hour = hour_ts - timedelta(hours=2)
            prev_prev_count = sym_ts[sym].get(prev_prev_hour, 0)
            prev_velocity = (prev_count / prev_prev_count) if prev_prev_count > 0 else None
            mention_acceleration = None
            if mention_velocity is not None and prev_velocity is not None and prev_velocity > 0:
                mention_acceleration = mention_velocity / prev_velocity

            # Author novelty
            historical = sym_historical_authors[sym]
            new_authors = data["authors"] - historical
            author_novelty_ratio = len(new_authors) / unique_authors if unique_authors > 0 else 0.0
            sym_historical_authors[sym].update(data["authors"])

            # First mention
            first_mention = len(historical) == 0 and len(new_authors) > 0

            # mention_volume_ratio computed in second pass
            mention_volume_ratio = None

            feature_rows.append((
                sym, hour_ts, mention_count, unique_authors, platform_count,
                engagement_sum, mention_velocity, mention_acceleration,
                author_novelty_ratio, first_mention, mention_volume_ratio
            ))

    logger.info("Computed %d feature rows, upserting...", len(feature_rows))

    # Step 5: Bulk upsert in batches
    sql = """
        INSERT INTO social_features_1h
        (symbol, bar_start_ts, mention_count, unique_authors, platform_count,
         engagement_sum, mention_velocity, mention_acceleration,
         author_novelty_ratio, first_mention, mention_volume_ratio)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, bar_start_ts) DO UPDATE SET
            mention_count = EXCLUDED.mention_count,
            unique_authors = EXCLUDED.unique_authors,
            platform_count = EXCLUDED.platform_count,
            engagement_sum = EXCLUDED.engagement_sum,
            mention_velocity = EXCLUDED.mention_velocity,
            mention_acceleration = EXCLUDED.mention_acceleration,
            author_novelty_ratio = EXCLUDED.author_novelty_ratio,
            first_mention = EXCLUDED.first_mention,
            mention_volume_ratio = EXCLUDED.mention_volume_ratio,
            computed_at = NOW()
    """
    batch_size = 5000
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for i in range(0, len(feature_rows), batch_size):
                cur.executemany(sql, feature_rows[i:i + batch_size])
                if (i + batch_size) % 25000 == 0 or i + batch_size >= len(feature_rows):
                    logger.info("  Upserted %d / %d feature rows",
                                min(i + batch_size, len(feature_rows)), len(feature_rows))
        conn.commit()

    logger.info("Materialized %d feature rows", len(feature_rows))
    return len(feature_rows)


# ─── Second Pass: mention_volume_ratio ────────────────────────────────────────

def compute_mention_volume_ratio(database_url: str, start_date: datetime,
                                  end_date: datetime) -> int:
    """Compute mention_volume_ratio = mention_z - volume_z by joining OHLCV."""
    logger.info("Computing mention_volume_ratio: %s to %s", start_date, end_date)

    # Load social features
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, mention_count
                FROM social_features_1h
                WHERE bar_start_ts >= %s AND bar_start_ts < %s
                ORDER BY symbol, bar_start_ts
            """, (start_date, end_date))
            social_rows = cur.fetchall()

    if not social_rows:
        logger.info("No social features to update")
        return 0

    logger.info("Loaded %d social feature rows", len(social_rows))

    # Build per-symbol mention time series
    sym_mentions = defaultdict(list)
    for sym, ts, count in social_rows:
        sym_mentions[sym].append((ts, count))

    symbols = list(sym_mentions.keys())
    logger.info("Loading OHLCV volume for %d symbols...", len(symbols))

    # Load OHLCV volume
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, volume
                FROM ohlcv
                WHERE timeframe = '1h' AND is_final = TRUE
                  AND bar_start_ts >= %s AND bar_start_ts < %s
                  AND symbol = ANY(%s)
                ORDER BY symbol, bar_start_ts
            """, (start_date, end_date, symbols))
            vol_rows = cur.fetchall()

    logger.info("Loaded %d OHLCV volume rows", len(vol_rows))

    vol_lookup = {}
    for sym, ts, vol in vol_rows:
        vol_lookup[(sym, ts)] = float(vol)

    # Compute z-scores and divergence
    updates = []
    window = 24

    for sym, series in sym_mentions.items():
        series.sort(key=lambda x: x[0])
        if len(series) < window:
            continue

        mentions = np.array([c for _, c in series], dtype=np.float64)
        timestamps = [t for t, _ in series]

        for i in range(window, len(mentions)):
            win = mentions[i - window:i]
            m_mean = np.mean(win)
            m_std = np.std(win)
            mention_z = (mentions[i] - m_mean) / m_std if m_std > 0 else 0.0

            ts = timestamps[i]
            vol_vals = []
            for j in range(max(0, i - window), i):
                v = vol_lookup.get((sym, timestamps[j]))
                if v is not None:
                    vol_vals.append(v)

            if len(vol_vals) < window // 2:
                continue

            vol_arr = np.array(vol_vals)
            v_mean = np.mean(vol_arr)
            v_std = np.std(vol_arr)
            current_vol = vol_lookup.get((sym, ts), v_mean)
            volume_z = (current_vol - v_mean) / v_std if v_std > 0 else 0.0

            mvr = float(mention_z - volume_z)
            updates.append((mvr, sym, ts))

    if updates:
        logger.info("Updating %d mention_volume_ratio values...", len(updates))
        batch_size = 5000
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                for i in range(0, len(updates), batch_size):
                    cur.executemany("""
                        UPDATE social_features_1h
                        SET mention_volume_ratio = %s
                        WHERE symbol = %s AND bar_start_ts = %s
                    """, updates[i:i + batch_size])
            conn.commit()

    logger.info("Updated %d mention_volume_ratio values", len(updates))
    return len(updates)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Social Data Historical Backfill")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--source", choices=["reddit", "news", "all"], default="all")
    parser.add_argument("--no-materialize", action="store_true",
                        help="Skip Brain 1 materialization")
    parser.add_argument("--db-url", default=None,
                        help="PostgreSQL connection URL (default: DATABASE_URL env)")
    args = parser.parse_args()

    database_url = args.db_url or os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("No DATABASE_URL. Set env var or pass --db-url")
        sys.exit(1)

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    logger.info("=" * 70)
    logger.info("SOCIAL DATA HISTORICAL BACKFILL")
    logger.info("=" * 70)
    logger.info("Window: %s to %s (%d days)", start_date.date(), end_date.date(), args.days)
    logger.info("Source: %s", args.source)

    # Load symbol matcher
    aliases = load_symbol_aliases_direct(database_url)
    if not aliases:
        logger.error("FATAL: symbol_aliases table is empty. Run populate_symbol_aliases.py first.")
        sys.exit(1)
    matcher = SymbolMatcher(aliases)
    logger.info("Loaded %d symbol aliases (%d unambiguous, %d ambiguous tickers)",
                len(aliases), len(matcher.unambiguous_tickers), len(matcher.ambiguous_tickers))

    total_stats = {"fetched": 0, "matched": 0, "inserted": 0}
    t0 = time.time()

    # ── Reddit backfill ──
    if args.source in ("reddit", "all"):
        logger.info("")
        logger.info("=" * 50)
        logger.info("REDDIT HISTORICAL BACKFILL")
        logger.info("=" * 50)

        try:
            reddit = RedditBackfiller(matcher, database_url)
        except RuntimeError as e:
            logger.error("Reddit backfill skipped: %s", e)
            reddit = None

        if reddit:
            for i, sub in enumerate(REDDIT_SUBREDDITS):
                logger.info("[%d/%d] Backfilling r/%s...",
                            i + 1, len(REDDIT_SUBREDDITS), sub)
                stats = reddit.backfill_subreddit(
                    sub, start_date, end_date,
                    batch_id_prefix="backfill_%s" % start_date.strftime('%Y%m%d')
                )
                for k in total_stats:
                    total_stats[k] += stats.get(k, 0)
                logger.info("  r/%s complete: fetched=%d matched=%d",
                            sub, stats["fetched"], stats["matched"])

    # ── News backfill ──
    if args.source in ("news", "all"):
        logger.info("")
        logger.info("=" * 50)
        logger.info("CRYPTO NEWS HISTORICAL BACKFILL")
        logger.info("=" * 50)

        news = NewsBackfiller(matcher, database_url)
        stats = news.backfill(
            start_date, end_date,
            batch_id_prefix="backfill_%s" % start_date.strftime('%Y%m%d')
        )
        for k in total_stats:
            total_stats[k] += stats.get(k, 0)
        logger.info("  News complete: fetched=%d matched=%d",
                     stats["fetched"], stats["matched"])

    elapsed = time.time() - t0
    logger.info("")
    logger.info("=" * 50)
    logger.info("BACKFILL SUMMARY")
    logger.info("=" * 50)
    logger.info("Total fetched:  %d", total_stats["fetched"])
    logger.info("Total matched:  %d", total_stats["matched"])
    logger.info("Total inserted: %d", total_stats["inserted"])
    logger.info("Elapsed: %.1f minutes", elapsed / 60)

    # ── Verify ──
    logger.info("")
    logger.info("Verification:")
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source, count(*), min(post_ts)::date, max(post_ts)::date
                FROM social_posts GROUP BY source ORDER BY count DESC
            """)
            for row in cur:
                logger.info("  %s: %d posts (%s to %s)", row[0], row[1], row[2], row[3])

            cur.execute("""
                SELECT unnest(symbols) as sym, count(*)
                FROM social_posts
                GROUP BY sym
                ORDER BY count DESC
                LIMIT 20
            """)
            logger.info("")
            logger.info("Top 20 mentioned symbols:")
            for sym, count in cur:
                logger.info("  %-12s %d mentions", sym, count)

    # ── Materialization ──
    if not args.no_materialize:
        logger.info("")
        logger.info("=" * 50)
        logger.info("BRAIN 1 BULK MATERIALIZATION")
        logger.info("=" * 50)
        t1 = time.time()
        n_features = materialize_features_bulk(database_url, start_date, end_date)
        logger.info("Materialized %d feature rows in %.1f seconds",
                     n_features, time.time() - t1)

        logger.info("")
        logger.info("=" * 50)
        logger.info("COMPUTING mention_volume_ratio")
        logger.info("=" * 50)
        t2 = time.time()
        n_mvr = compute_mention_volume_ratio(database_url, start_date, end_date)
        logger.info("Updated %d mention_volume_ratio values in %.1f seconds",
                     n_mvr, time.time() - t2)

        # Final verification
        logger.info("")
        logger.info("Final feature table stats:")
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT count(*), count(DISTINCT symbol),
                           min(bar_start_ts)::date, max(bar_start_ts)::date,
                           avg(mention_count)::numeric(10,2),
                           count(mention_volume_ratio) as mvr_non_null
                    FROM social_features_1h
                """)
                row = cur.fetchone()
                logger.info("  Rows: %d, Symbols: %d, Range: %s to %s, "
                            "Avg mentions: %s, MVR coverage: %d",
                            row[0], row[1], row[2], row[3], row[4], row[5])

    total_elapsed = time.time() - t0
    logger.info("")
    logger.info("BACKFILL COMPLETE in %.1f minutes", total_elapsed / 60)


if __name__ == "__main__":
    main()
