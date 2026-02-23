# Social Data Historical Backfill — Build Specification

## Your Role

You are building a historical backfill script that populates the `social_posts` table with 365 days of Reddit and crypto news posts, then materializes `social_features_1h` for the entire window. This converts the social pipeline from "accumulate and wait" to "testable today" — same as derivatives.

The live social_collector service already exists and will continue collecting going forward. This script fills in the past.

## Step 0 — Orientation

Read these files first:

```
Backend/social_collector/app/config.py          — subreddit list, RSS feed names, constants
Backend/social_collector/app/db.py              — batch_insert_posts(), upsert_social_features()
Backend/social_collector/app/symbol_matcher.py  — SymbolMatcher class (you'll reuse this)
Backend/social_collector/app/brain1.py          — aggregate_hour() logic (you'll adapt this for bulk)
Backend/social_collector/app/collectors/rss.py  — post dict format for batch_insert_posts
Backend/docs/db/migrations/20260219_add_social_tables.sql — schema
```

Also check current data state:
```bash
docker exec backend-postgres-1 psql -U trading -d trading -c "
    SELECT count(*) as posts FROM social_posts;
    SELECT count(*) as features FROM social_features_1h;
    SELECT count(*) as aliases FROM symbol_aliases;
"
```

And check OHLCV coverage (defines alignment window):
```bash
docker exec backend-postgres-1 psql -U trading -d trading -c "
    SELECT min(bar_start_ts), max(bar_start_ts), count(DISTINCT symbol)
    FROM ohlcv WHERE timeframe = '1h' AND is_final = TRUE;
"
```

Report these numbers — they define the backfill window.

## Step 1 — Test Data Source APIs

Before building the full pipeline, verify each API works and understand rate limits.

### 1a. Arctic Shift (Reddit Historical)

Arctic Shift is the Pushshift successor — free, no auth, full Reddit submission history.

```bash
# Test: fetch 5 recent posts from r/CryptoCurrency
curl -s "https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=CryptoCurrency&limit=5&sort=created_utc&order=desc" | python3 -m json.tool | head -50
```

Expected response: JSON with `data` array containing submission objects with `id`, `title`, `selftext`, `author`, `created_utc`, `score`, `subreddit` fields.

If Arctic Shift is down, test PullPush.io as fallback:
```bash
curl -s "https://api.pullpush.io/reddit/search/submission/?subreddit=CryptoCurrency&size=5&sort=created_utc&order=desc" | python3 -m json.tool | head -50
```

### 1b. CryptoCompare News API

```bash
# Test: fetch recent crypto news
curl -s "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest" | python3 -m json.tool | head -80
```

Expected: JSON with `Data` array containing articles with `id`, `title`, `body`, `source`, `published_on` (epoch), `url`, `categories`.

CryptoCompare free tier: 100,000 calls/month, 50 articles per call. For 365 days at ~200 articles/day = ~73K articles = ~1,460 API calls. Well within limits.

### 1c. Verify Response Formats

For each working API, note:
- Exact field names for: post ID, title, body/content, author, timestamp, score/engagement
- Pagination mechanism (cursor, timestamp offset, page number)
- Rate limits (headers or documentation)
- Maximum results per request
- Maximum historical depth

**Report findings before proceeding.** If Arctic Shift is down, we'll adjust the plan.

## Step 2 — Build the Backfill Script

Create `Backend/scripts/backfill_social_posts.py`.

### Architecture

```
backfill_social_posts.py
├── ARGS
│   ├── --days 365              (how far back to go)
│   ├── --source reddit|news|all
│   ├── --resume                (skip date windows with existing data)
│   ├── --materialize           (run Brain 1 aggregation after backfill)
│   └── --dry-run               (fetch but don't insert)
├── SYMBOL MATCHER
│   ├── Load symbol_aliases from DB
│   ├── Instantiate SymbolMatcher (same class as live collector)
│   └── Used for every post
├── REDDIT BACKFILL (Arctic Shift / PullPush)
│   ├── For each subreddit in config.REDDIT_SUBREDDITS:
│   │   ├── For each day-chunk (7 days at a time to manage pagination):
│   │   │   ├── Fetch submissions in [start, end) window
│   │   │   ├── Paginate until exhausted or 0 results
│   │   │   ├── Run symbol_matcher on title + selftext
│   │   │   ├── Skip posts with no symbol matches
│   │   │   └── Batch insert to social_posts
│   │   └── Log progress: subreddit, date range, posts fetched, matches
│   └── Resume: check max(post_ts) for source='reddit_historical' per channel
├── NEWS BACKFILL (CryptoCompare)
│   ├── Paginate backwards from now using lTs parameter
│   ├── For each article:
│   │   ├── Run symbol_matcher on title + body
│   │   ├── Map source_info.name to channel
│   │   └── Insert with source='news_historical'
│   └── Resume: check max(post_ts) for source='news_historical'
├── BRAIN 1 BULK MATERIALIZATION
│   ├── For each hour in backfill window:
│   │   ├── Aggregate social_posts for that hour
│   │   ├── Compute all Brain 1 features
│   │   └── Upsert to social_features_1h
│   └── Vectorized where possible (batch full days, not hour-by-hour)
└── REPORT
    ├── Total posts by source
    ├── Symbol coverage
    ├── Feature rows materialized
    └── Date range covered
```

### Reddit Backfill Implementation

```python
import requests
import time
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("backfill")

ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
PULLPUSH_URL = "https://api.pullpush.io/reddit/search/submission/"

class RedditBackfiller:
    def __init__(self, matcher, db_url, source_label="reddit_historical"):
        self.matcher = matcher
        self.db_url = db_url
        self.source_label = source_label
        self.api_url = None  # Set after testing
        self.api_type = None  # 'arctic_shift' or 'pullpush'
        self._detect_api()
    
    def _detect_api(self):
        """Test which Reddit archive API is available."""
        for url, name in [(ARCTIC_SHIFT_URL, "arctic_shift"), (PULLPUSH_URL, "pullpush")]:
            try:
                params = {"subreddit": "CryptoCurrency", "limit": 1, "sort": "created_utc", "order": "desc"}
                if name == "pullpush":
                    params = {"subreddit": "CryptoCurrency", "size": 1, "sort": "created_utc", "order": "desc"}
                resp = requests.get(url, params=params, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    # Arctic Shift wraps in 'data', PullPush returns array directly
                    posts = data.get("data", data) if isinstance(data, dict) else data
                    if isinstance(posts, list) and len(posts) > 0:
                        self.api_url = url
                        self.api_type = name
                        logger.info("Using %s API: %s", name, url)
                        return
            except Exception as e:
                logger.warning("%s unavailable: %s", name, e)
        
        raise RuntimeError("No Reddit archive API available (tried Arctic Shift + PullPush)")
    
    def fetch_window(self, subreddit: str, after_ts: int, before_ts: int, limit: int = 100) -> list[dict]:
        """Fetch submissions for a subreddit in a time window. Returns raw API records."""
        all_posts = []
        cursor_ts = after_ts
        
        while cursor_ts < before_ts:
            if self.api_type == "arctic_shift":
                params = {
                    "subreddit": subreddit,
                    "after": cursor_ts,
                    "before": before_ts,
                    "limit": limit,
                    "sort": "created_utc",
                    "order": "asc",
                }
                resp = requests.get(self.api_url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                posts = data.get("data", [])
            else:  # pullpush
                params = {
                    "subreddit": subreddit,
                    "after": cursor_ts,
                    "before": before_ts,
                    "size": limit,
                    "sort": "created_utc",
                    "order": "asc",
                }
                resp = requests.get(self.api_url, params=params, timeout=30)
                resp.raise_for_status()
                posts = resp.json()
                if isinstance(posts, dict):
                    posts = posts.get("data", [])
            
            if not posts:
                break
            
            all_posts.extend(posts)
            
            # Advance cursor past the last post
            last_ts = max(p.get("created_utc", 0) for p in posts)
            if last_ts <= cursor_ts:
                break  # No progress, avoid infinite loop
            cursor_ts = last_ts + 1
            
            # Rate limit: be polite to free APIs
            time.sleep(1.0)
        
        return all_posts
    
    def backfill_subreddit(self, subreddit: str, start_date: datetime, end_date: datetime,
                           batch_id_prefix: str) -> dict:
        """Backfill one subreddit for a date range. Returns stats dict."""
        stats = {"fetched": 0, "matched": 0, "inserted": 0}
        
        # Process in 7-day chunks
        chunk_start = start_date
        chunk_days = 7
        
        while chunk_start < end_date:
            chunk_end = min(chunk_start + timedelta(days=chunk_days), end_date)
            after_ts = int(chunk_start.timestamp())
            before_ts = int(chunk_end.timestamp())
            
            batch_id = f"{batch_id_prefix}_{subreddit}_{chunk_start.strftime('%Y%m%d')}"
            
            try:
                raw_posts = self.fetch_window(subreddit, after_ts, before_ts, limit=100)
            except Exception as e:
                logger.error("Reddit fetch error r/%s %s-%s: %s", 
                           subreddit, chunk_start.date(), chunk_end.date(), e)
                chunk_start = chunk_end
                continue
            
            stats["fetched"] += len(raw_posts)
            
            # Process and match symbols
            db_posts = []
            for p in raw_posts:
                text = (p.get("title", "") or "") + " " + (p.get("selftext", "") or "")
                symbols = self.matcher.extract_symbols(text)
                
                if not symbols:
                    continue
                
                db_posts.append({
                    "source": self.source_label,
                    "source_id": p.get("id", ""),
                    "channel": subreddit,
                    "author": p.get("author", None),
                    "title": (p.get("title", "") or "")[:500],
                    "content": (p.get("selftext", "") or "")[:2000],
                    "post_ts": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc),
                    "engagement_score": p.get("score", 0),
                    "symbols": symbols,
                    "batch_id": batch_id,
                })
            
            stats["matched"] += len(db_posts)
            
            if db_posts:
                n = batch_insert_posts(db_posts)
                stats["inserted"] += n
            
            logger.info("  r/%s %s→%s: fetched=%d matched=%d inserted=%d",
                       subreddit, chunk_start.strftime("%Y-%m-%d"), 
                       chunk_end.strftime("%Y-%m-%d"),
                       len(raw_posts), len(db_posts), len(db_posts))
            
            chunk_start = chunk_end
        
        return stats
```

### News Backfill Implementation

```python
CRYPTOCOMPARE_NEWS_URL = "https://min-api.cryptocompare.com/data/v2/news/"

class NewsBackfiller:
    def __init__(self, matcher, db_url, source_label="news_historical"):
        self.matcher = matcher
        self.db_url = db_url
        self.source_label = source_label
    
    def backfill(self, start_date: datetime, end_date: datetime, batch_id_prefix: str) -> dict:
        """Backfill crypto news by paginating backwards from end_date."""
        stats = {"fetched": 0, "matched": 0, "inserted": 0}
        
        # CryptoCompare paginates backwards via lTs (less than timestamp)
        cursor_ts = int(end_date.timestamp())
        start_ts = int(start_date.timestamp())
        
        while cursor_ts > start_ts:
            try:
                resp = requests.get(CRYPTOCOMPARE_NEWS_URL, params={
                    "lang": "EN",
                    "sortOrder": "latest",
                    "lTs": cursor_ts,
                }, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                articles = data.get("Data", [])
            except Exception as e:
                logger.error("CryptoCompare fetch error at ts=%d: %s", cursor_ts, e)
                cursor_ts -= 86400  # Skip a day
                continue
            
            if not articles:
                break
            
            batch_id = f"{batch_id_prefix}_news_{cursor_ts}"
            db_posts = []
            
            for a in articles:
                pub_ts = a.get("published_on", 0)
                if pub_ts < start_ts:
                    continue
                
                title = a.get("title", "") or ""
                body = a.get("body", "") or ""
                text = title + " " + body
                symbols = self.matcher.extract_symbols(text)
                
                if not symbols:
                    stats["fetched"] += 1
                    continue
                
                source_name = a.get("source_info", {}).get("name", "unknown")
                
                db_posts.append({
                    "source": self.source_label,
                    "source_id": str(a.get("id", "")),
                    "channel": source_name,
                    "author": a.get("source", None),
                    "title": title[:500],
                    "content": body[:2000],
                    "post_ts": datetime.fromtimestamp(pub_ts, tz=timezone.utc),
                    "engagement_score": 0,  # CryptoCompare doesn't provide engagement
                    "symbols": symbols,
                    "batch_id": batch_id,
                })
            
            stats["fetched"] += len(articles)
            stats["matched"] += len(db_posts)
            
            if db_posts:
                n = batch_insert_posts(db_posts)
                stats["inserted"] += n
            
            # Advance cursor backwards
            oldest = min(a.get("published_on", cursor_ts) for a in articles)
            if oldest >= cursor_ts:
                break  # No progress
            cursor_ts = oldest - 1
            
            # Rate limit
            time.sleep(0.5)
            
            if stats["fetched"] % 1000 == 0:
                logger.info("  News: fetched=%d matched=%d so far, cursor at %s",
                           stats["fetched"], stats["matched"],
                           datetime.fromtimestamp(cursor_ts, tz=timezone.utc).strftime("%Y-%m-%d"))
        
        return stats
```

### Brain 1 Bulk Materialization

The live Brain 1 (brain1.py) processes one hour at a time with individual DB queries per symbol. For backfill materialization over 8,760 hours (365 days), this would be too slow. Build a bulk version:

```python
def materialize_features_bulk(database_url: str, start_date: datetime, end_date: datetime):
    """Materialize social_features_1h for the entire backfill window.
    
    Strategy: Load ALL social_posts for the window into memory, group by 
    (symbol, hour), compute features in Python, bulk upsert.
    
    This is feasible because social_posts for crypto subreddits is manageable
    (~100K-500K rows for a year).
    """
    import psycopg
    from collections import defaultdict
    
    logger.info("Materializing social features: %s to %s", start_date, end_date)
    
    # Step 1: Load all posts in window
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
    # symbol_hour_data[(symbol, hour_ts)] = {"authors": set(), "sources": set(), "count": 0, "engagement": 0}
    symbol_hour = defaultdict(lambda: {"authors": set(), "sources": set(), "count": 0, "engagement": 0})
    
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
    
    # Step 3: Build sorted list of all unique hours and symbols
    all_hours = sorted(set(h for _, h in symbol_hour.keys()))
    all_symbols = sorted(set(s for s, _ in symbol_hour.keys()))
    
    # Step 4: Compute time-series features per symbol
    # Build per-symbol time series of mention_count for velocity/acceleration
    sym_ts = defaultdict(dict)  # sym -> {hour: mention_count}
    for (sym, hour_ts), data in symbol_hour.items():
        sym_ts[sym][hour_ts] = data["count"]
    
    # Step 5: Compute historical author sets for novelty ratio
    # For efficiency: process chronologically, maintain running author set per symbol
    sym_historical_authors = defaultdict(set)
    
    # Step 6: Compute and collect feature rows
    feature_rows = []
    
    # Process chronologically
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
            
            # Velocity: compare to previous hour
            prev_hour = hour_ts - timedelta(hours=1)
            prev_count = sym_ts[sym].get(prev_hour, 0)
            mention_velocity = (mention_count / prev_count) if prev_count > 0 else None
            
            # Acceleration: velocity change
            prev_prev_hour = hour_ts - timedelta(hours=2)
            prev_prev_count = sym_ts[sym].get(prev_prev_hour, 0)
            prev_velocity = (prev_count / prev_prev_count) if prev_prev_count > 0 else None
            mention_acceleration = None
            if mention_velocity is not None and prev_velocity is not None and prev_velocity > 0:
                mention_acceleration = mention_velocity / prev_velocity
            
            # Author novelty
            historical = sym_historical_authors[sym]
            new_authors = data["authors"] - historical
            author_novelty_ratio = len(new_authors) / unique_authors if unique_authors > 0 else 0
            # Update historical set
            sym_historical_authors[sym].update(data["authors"])
            
            # First mention: no previous data for this symbol
            first_mention = len(historical) == 0 and len(new_authors) > 0
            
            # mention_volume_ratio: skip for now (requires OHLCV join — compute in a second pass)
            mention_volume_ratio = None
            
            feature_rows.append((
                sym, hour_ts, mention_count, unique_authors, platform_count,
                engagement_sum, mention_velocity, mention_acceleration,
                author_novelty_ratio, first_mention, mention_volume_ratio
            ))
    
    logger.info("Computed %d feature rows", len(feature_rows))
    
    # Step 7: Bulk upsert
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            # Use executemany with ON CONFLICT
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
            # Batch in chunks of 5000
            for i in range(0, len(feature_rows), 5000):
                cur.executemany(sql, feature_rows[i:i+5000])
            conn.commit()
    
    logger.info("Materialized %d feature rows", len(feature_rows))
    return len(feature_rows)
```

### Second Pass: mention_volume_ratio

After Brain 1 materialization, compute the mention-volume divergence feature by joining OHLCV:

```python
def compute_mention_volume_ratio(database_url: str, start_date: datetime, end_date: datetime):
    """Second pass: compute mention_volume_ratio by joining social_features_1h with OHLCV.
    
    mention_volume_ratio = mention_z - volume_z
    Where mention_z = (mention_count - 24h_rolling_mean) / 24h_rolling_std
    And volume_z = (ohlcv_volume - 24h_rolling_mean) / 24h_rolling_std
    
    Positive = social hot, market quiet = pre-ignition divergence
    """
    import psycopg
    import numpy as np
    
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
        return 0
    
    # Build per-symbol mention time series
    from collections import defaultdict
    sym_mentions = defaultdict(list)  # sym -> [(ts, count)]
    for sym, ts, count in social_rows:
        sym_mentions[sym].append((ts, count))
    
    # Load OHLCV volume for matching symbols and time range
    symbols = list(sym_mentions.keys())
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
    
    # Build volume lookup: (sym, ts) -> volume
    vol_lookup = {}
    for sym, ts, vol in vol_rows:
        vol_lookup[(sym, ts)] = float(vol)
    
    # Compute z-scores and divergence per symbol
    updates = []
    for sym, series in sym_mentions.items():
        series.sort(key=lambda x: x[0])
        mentions = np.array([c for _, c in series], dtype=np.float64)
        timestamps = [t for t, _ in series]
        
        # Rolling 24h z-score for mentions
        window = 24
        if len(mentions) < window:
            continue
        
        for i in range(window, len(mentions)):
            win = mentions[i-window:i]
            m_mean = np.mean(win)
            m_std = np.std(win)
            mention_z = (mentions[i] - m_mean) / m_std if m_std > 0 else 0
            
            # Volume z-score
            ts = timestamps[i]
            vol_vals = []
            for j in range(max(0, i-window), i):
                v = vol_lookup.get((sym, timestamps[j]))
                if v is not None:
                    vol_vals.append(v)
            
            if len(vol_vals) < window // 2:
                continue
            
            vol_arr = np.array(vol_vals)
            v_mean = np.mean(vol_arr)
            v_std = np.std(vol_arr)
            volume_z = (vol_lookup.get((sym, ts), v_mean) - v_mean) / v_std if v_std > 0 else 0
            
            mvr = mention_z - volume_z  # Positive = social hot, market quiet
            updates.append((float(mvr), sym, ts))
    
    # Bulk update
    if updates:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    UPDATE social_features_1h 
                    SET mention_volume_ratio = %s
                    WHERE symbol = %s AND bar_start_ts = %s
                """, updates)
            conn.commit()
    
    logger.info("Updated mention_volume_ratio for %d rows", len(updates))
    return len(updates)
```

### Main Function

```python
def main():
    parser = argparse.ArgumentParser(description="Social Data Historical Backfill")
    parser.add_argument("--days", type=int, default=365, help="Days to backfill (default: 365)")
    parser.add_argument("--source", choices=["reddit", "news", "all"], default="all")
    parser.add_argument("--materialize", action="store_true", default=True,
                       help="Run Brain 1 materialization after backfill (default: True)")
    parser.add_argument("--no-materialize", action="store_true", help="Skip materialization")
    parser.add_argument("--resume", action="store_true", help="Skip date windows with existing data")
    parser.add_argument("--workers", type=int, default=4, help="Parallel subreddit fetchers")
    args = parser.parse_args()
    
    database_url = os.environ["DATABASE_URL"]
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)
    
    logger.info("=" * 70)
    logger.info("SOCIAL DATA HISTORICAL BACKFILL")
    logger.info("=" * 70)
    logger.info("Window: %s to %s (%d days)", start_date.date(), end_date.date(), args.days)
    
    # Load symbol matcher (same as live collector)
    aliases = load_symbol_aliases_direct(database_url)
    matcher = SymbolMatcher(aliases)
    logger.info("Loaded %d symbol aliases", len(aliases))
    
    total_stats = {"fetched": 0, "matched": 0, "inserted": 0}
    
    # ── Reddit backfill ──
    if args.source in ("reddit", "all"):
        logger.info("\n── Reddit Historical Backfill ──")
        reddit = RedditBackfiller(matcher, database_url)
        
        subreddits = [
            "CryptoCurrency", "CryptoMoonShots", "altcoin", "SatoshiStreetBets",
            "CryptoMarkets", "defi", "binance", "ethtrader", "solana",
            "cardano", "algorand", "Chainlink", "Polkadot", "0xPolygon",
            "avax", "cosmosnetwork", "UniSwap", "pancakeswap",
        ]
        
        # Process subreddits sequentially (API rate limits)
        # Could parallelise with workers but be polite to free APIs
        for sub in subreddits:
            logger.info("  Backfilling r/%s...", sub)
            stats = reddit.backfill_subreddit(
                sub, start_date, end_date, 
                batch_id_prefix=f"backfill_{start_date.strftime('%Y%m%d')}"
            )
            for k in total_stats:
                total_stats[k] += stats.get(k, 0)
            logger.info("  r/%s done: fetched=%d matched=%d inserted=%d",
                       sub, stats["fetched"], stats["matched"], stats["inserted"])
    
    # ── News backfill ──
    if args.source in ("news", "all"):
        logger.info("\n── Crypto News Historical Backfill ──")
        news = NewsBackfiller(matcher, database_url)
        stats = news.backfill(start_date, end_date, 
                             batch_id_prefix=f"backfill_{start_date.strftime('%Y%m%d')}")
        for k in total_stats:
            total_stats[k] += stats.get(k, 0)
        logger.info("  News done: fetched=%d matched=%d inserted=%d",
                    stats["fetched"], stats["matched"], stats["inserted"])
    
    logger.info("\n── Backfill Summary ──")
    logger.info("Total fetched: %d", total_stats["fetched"])
    logger.info("Total matched (with symbols): %d", total_stats["matched"])
    logger.info("Total inserted: %d", total_stats["inserted"])
    
    # ── Verify ──
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source, count(*), min(post_ts), max(post_ts) FROM social_posts GROUP BY source")
            for row in cur:
                logger.info("  %s: %d posts, %s to %s", row[0], row[1], row[2], row[3])
            
            cur.execute("""
                SELECT unnest(symbols) as sym, count(*) 
                FROM social_posts 
                GROUP BY sym 
                ORDER BY count DESC 
                LIMIT 20
            """)
            logger.info("\nTop 20 mentioned symbols:")
            for sym, count in cur:
                logger.info("  %s: %d mentions", sym, count)
    
    # ── Materialization ──
    if args.materialize and not args.no_materialize:
        logger.info("\n── Brain 1 Bulk Materialization ──")
        n_features = materialize_features_bulk(database_url, start_date, end_date)
        logger.info("Materialized %d feature rows", n_features)
        
        logger.info("\n── Computing mention_volume_ratio ──")
        n_mvr = compute_mention_volume_ratio(database_url, start_date, end_date)
        logger.info("Updated %d mention_volume_ratio values", n_mvr)
        
        # Final verification
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT count(*), count(DISTINCT symbol), 
                           min(bar_start_ts), max(bar_start_ts),
                           avg(mention_count)::numeric(10,2),
                           count(mention_volume_ratio) as mvr_non_null
                    FROM social_features_1h
                """)
                row = cur.fetchone()
                logger.info("\nFeature table: %d rows, %d symbols, %s to %s, avg_mentions=%.2f, mvr_coverage=%d",
                           *row)
    
    logger.info("\n✓ Backfill complete")
```

### Database Connection Helper

Since this script runs outside the social_collector container, it needs its own DB connection. Add a direct symbol_aliases loader that doesn't depend on config.py:

```python
def load_symbol_aliases_direct(database_url: str) -> dict:
    """Load symbol_aliases. Returns {lowercase_alias: [(symbol, alias_type, is_ambiguous), ...]}"""
    import psycopg
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
```

### SymbolMatcher Import

The script needs SymbolMatcher but runs from Backend/scripts/, not inside the social_collector container. Either:
1. Copy the SymbolMatcher class into the script (simplest)
2. Add social_collector/app to sys.path

**Option 1 is cleaner for a standalone script.** Copy the SymbolMatcher class and CRYPTO_CONTEXT_WORDS from the social_collector code. The spec already has the full implementation in SOCIAL_PIPELINE_SPEC.md, Steps 4 and 6.

### Script Dependencies

This script runs via `docker compose run --rm -T worker python3 /scripts/backfill_social_posts.py` (using the existing worker container which has psycopg, requests, numpy). Check that `requests` and `psycopg` are in the worker — they are (see worker/requirements.txt). No new dependencies needed.

## Step 3 — Run the Backfill

```bash
# Reddit + News, 365 days, with materialization
docker compose run --rm -T worker python3 /scripts/backfill_social_posts.py --days 365 --source all
```

**Expected runtime:** 
- Reddit: 18 subreddits × 52 weekly chunks × ~1s API delay = ~15-20 minutes
- News: ~1,460 API calls × 0.5s = ~12 minutes  
- Materialization: a few minutes (in-memory processing)
- **Total: ~30-40 minutes**

**Expected volumes:**
- Reddit: 200K-1M raw posts (r/CryptoCurrency alone has ~500 posts/day)
- News: 50K-100K articles
- Symbol-matched: 30-60% of Reddit posts, 60-80% of news articles
- Feature rows: 5K-50K (symbol, hour) buckets with at least one mention

## Step 4 — Verify and Report

After backfill completes, run:

```sql
-- Volume by source
SELECT source, count(*), min(post_ts)::date, max(post_ts)::date
FROM social_posts GROUP BY source ORDER BY count DESC;

-- Monthly distribution (check for gaps)
SELECT date_trunc('month', post_ts)::date as month, source, count(*)
FROM social_posts 
GROUP BY month, source 
ORDER BY month, source;

-- Feature coverage
SELECT count(*) as total_features,
       count(DISTINCT symbol) as symbols,
       min(bar_start_ts)::date as first_date,
       max(bar_start_ts)::date as last_date,
       count(mention_volume_ratio) as mvr_non_null,
       avg(mention_count)::numeric(10,2) as avg_mentions
FROM social_features_1h;

-- Top symbols by total mentions (sanity check)
SELECT symbol, sum(mention_count) as total_mentions, count(*) as hours_active
FROM social_features_1h
GROUP BY symbol
ORDER BY total_mentions DESC
LIMIT 30;

-- mention_volume_ratio distribution (the thesis feature)
SELECT 
    percentile_cont(0.10) WITHIN GROUP (ORDER BY mention_volume_ratio) as p10,
    percentile_cont(0.25) WITHIN GROUP (ORDER BY mention_volume_ratio) as p25,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY mention_volume_ratio) as p50,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY mention_volume_ratio) as p75,
    percentile_cont(0.90) WITHIN GROUP (ORDER BY mention_volume_ratio) as p90
FROM social_features_1h
WHERE mention_volume_ratio IS NOT NULL;
```

Report all results. The critical number is: **how many (symbol, hour) feature rows have non-null mention_volume_ratio?** This is the usable sample size for signal testing.

## Step 5 — Sanity Checks

After reporting volumes, run these quality checks:

1. **Ambiguous ticker false positives:** Spot-check the most common ambiguous symbols
```sql
SELECT title FROM social_posts 
WHERE 'SUNUSDT' = ANY(symbols) 
ORDER BY random() LIMIT 5;
```

2. **Temporal distribution:** Make sure posts aren't clustered in one period
```sql
SELECT date_trunc('week', post_ts)::date as week, count(*)
FROM social_posts 
GROUP BY week ORDER BY week;
```

3. **Feature sensibility:** mention_velocity should be centered around 1.0
```sql
SELECT avg(mention_velocity), stddev(mention_velocity),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY mention_velocity)
FROM social_features_1h 
WHERE mention_velocity IS NOT NULL;
```

## Non-Goals

- Don't modify the live social_collector service
- Don't backfill Telegram (requires interactive auth)
- Don't backfill CoinGecko trending (no historical API)
- Don't run signal testing — that's a separate script (same pattern as derivatives_signal_test.py)
- Don't tune the symbol matcher — use it as-is, note false positive rate for later tuning

## Error Handling

- If Arctic Shift AND PullPush are both down: log clearly and proceed with news-only backfill. Reddit can be retried later.
- If CryptoCompare returns errors: exponential backoff, skip gaps, continue.
- If a subreddit returns zero posts (deleted/private): log and skip, don't crash.
- If symbol_aliases is empty: FATAL — run populate_symbol_aliases.py first.
- All API calls must have timeouts (15-30s) and retry with backoff.

## Quality Bar

This is a data pipeline, not a research script. It must be:
- **Resumable**: ON CONFLICT DO NOTHING means re-running is safe and skips duplicates
- **Observable**: Log progress per subreddit, per date chunk, with running totals
- **Honest**: Don't inflate match counts. If a post has no symbol match, skip it silently.
- **Complete**: The materialization pass must produce feature rows aligned to the OHLCV hourly grid
