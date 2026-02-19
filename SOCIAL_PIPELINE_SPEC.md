# MCAS Crypto-Chatter Pipeline — Claude Code Build Prompt

## Your Role

You are building a social media data collection and feature engineering pipeline ("crypto-chatter") for MCAS, a cryptocurrency trading research platform. This is a new Docker service with its own dependencies, separate from the existing worker service.

The full design rationale and architecture is in `crypto-chatter-build-spec.md` — that's the master spec. **Read it first, in full.** This document is your execution plan: what to build, in what order, with specific implementation decisions pre-made so you don't have to guess.

## Step 0 — Orientation

Read these files to understand existing codebase patterns:

```
Backend/docker-compose.yml          — service architecture, network names, volume patterns
Backend/.env.example                — env var conventions
Backend/docs/db/schema/mcas_schema.sql  — table naming/indexing conventions
Backend/scripts/backfill_ohlcv_multi.py — reference for DB write patterns (batched inserts, ON CONFLICT DO NOTHING)
Backend/worker/Dockerfile           — reference for how Docker images are built
Backend/worker/app/ohlcv_persister.py   — reference for a continuous service with DB writes
Backend/worker/app/ingestor.py      — reference for how the codebase discovers Binance symbols
```

Then read `crypto-chatter-build-spec.md` completely. That's the architecture doc with schema, service layout, Brain 1/Brain 2 design, symbol matching tiers, and phase plan.

Then read `ambiguous_tickers.json` — a pre-built list of tickers that collide with common English words.

## Step 1 — Create the social_collector Directory Structure

Create the entire service scaffold:

```
Backend/social_collector/
├── Dockerfile
├── requirements.txt
└── app/
    ├── collector.py          # Main entrypoint
    ├── config.py             # Env vars, constants, symbol alias loader
    ├── db.py                 # DB connection helpers + batch insert
    ├── symbol_matcher.py     # Symbol extraction from text
    ├── collectors/
    │   ├── __init__.py
    │   ├── reddit.py         # PRAW collector
    │   ├── telegram.py       # Telethon listener
    │   ├── rss.py            # feedparser poll
    │   └── coingecko.py      # Trending endpoint poll
    ├── brain1.py             # Hourly aggregation
    └── brain2.py             # Anomaly detection + Haiku extraction
```

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app

CMD ["python", "-u", "collector.py"]
```

### requirements.txt

```
psycopg[binary]
redis
requests
numpy
praw
telethon
feedparser
anthropic
```

## Step 2 — Database Migration

Create `Backend/docs/db/migrations/YYYYMMDD_add_social_tables.sql` (use today's date).

Use the exact schema from `crypto-chatter-build-spec.md` (the `social_posts`, `social_features_1h`, `social_signals`, and `symbol_aliases` tables). Wrap in advisory lock for idempotency:

```sql
DO $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('add_social_tables'));
END $$;
```

Add all CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS statements from the spec. At the end, insert into schema_versions:

```sql
INSERT INTO schema_versions (version, description)
VALUES ('add_social_tables', 'Social posts, Brain 1 features, Brain 2 signals, symbol aliases')
ON CONFLICT DO NOTHING;
```

Apply the migration:
```bash
docker compose exec postgres psql -U trading -d trading
```
Paste the SQL. Verify all 4 tables exist.

## Step 3 — Populate symbol_aliases

Create a one-time script `Backend/scripts/populate_symbol_aliases.py` that:

1. Queries the Binance spot exchange info API: `GET https://api.binance.com/api/v3/exchangeInfo`
2. Filters for USDT pairs that are TRADING status
3. For each pair, inserts aliases:
   - Ticker: `DOGE` from `DOGEUSDT` (alias_type='ticker')
   - Cashtag: `$DOGE` (alias_type='cashtag')
   - Full name: from the Binance `baseAsset` field if useful, but more importantly from a CoinGecko lookup (optional — skip if too complex, the ticker + cashtag cover 90% of matching)
4. Marks ambiguous tickers using the pre-built `ambiguous_tickers.json` list (sets `is_ambiguous=TRUE`)

The ambiguous tickers list (provided as `ambiguous_tickers.json`) contains tickers that are common English words. These MUST be marked ambiguous so symbol_matcher.py only matches them with cashtag form ($WIN) or with crypto context words nearby.

Run the script:
```bash
docker compose run --rm -T worker python3 /scripts/populate_symbol_aliases.py
```

(It can use the worker container since it only needs requests + psycopg.)

Verify:
```sql
SELECT count(*) FROM symbol_aliases;
SELECT count(*) FROM symbol_aliases WHERE is_ambiguous = TRUE;
SELECT * FROM symbol_aliases WHERE symbol = 'DOGEUSDT';
```

Expected: ~1500-2000 alias rows, ~30-40 ambiguous, DOGEUSDT should have at least ticker 'DOGE' and cashtag '$DOGE'.

## Step 4 — Build config.py

```python
import os

# Database
DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Reddit
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "mcas-research/1.0")

# Telegram
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION_PATH = os.getenv("TELEGRAM_SESSION_PATH", "/app/sessions/mcas")

# Anthropic (Brain 2)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
BRAIN2_MODEL = "claude-haiku-4-5-20251001"
BRAIN2_MAX_CALLS_PER_HOUR = 100
BRAIN2_DEDUP_HOURS = 4
BRAIN2_DAILY_COST_ALERT_USD = 10.0
BRAIN2_CIRCUIT_BREAKER_FAILURES = 5
BRAIN2_CIRCUIT_BREAKER_BACKOFF_S = 1800

# Collection intervals
REDDIT_POLL_INTERVAL_S = 900       # 15 min
RSS_POLL_INTERVAL_S = 300          # 5 min
COINGECKO_POLL_INTERVAL_S = 1800   # 30 min
BRAIN1_DELAY_AFTER_HOUR_S = 300    # 5 min past the hour
BRAIN2_DELAY_AFTER_HOUR_S = 600    # 10 min past the hour

# Reddit subreddits to monitor
REDDIT_SUBREDDITS = [
    "CryptoCurrency", "CryptoMoonShots", "altcoin", "SatoshiStreetBets",
    "CryptoMarkets", "defi", "binance", "ethtrader", "solana",
    "cardano", "algorand", "Chainlink", "Polkadot", "0xPolygon",
    "avax", "cosmosnetwork", "UniSwap", "pancakeswap",
]

# RSS feeds (crypto news)
RSS_FEEDS = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "TheBlock", "url": "https://www.theblock.co/rss/all"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/"},
    {"name": "NewsBTC", "url": "https://www.newsbtc.com/feed/"},
    {"name": "Bitcoinist", "url": "https://bitcoinist.com/feed/"},
    {"name": "DailyHodl", "url": "https://dailyhodl.com/feed/"},
]

# Content truncation
MAX_CONTENT_LENGTH = 2000

# Crypto context words for ambiguous ticker matching
CRYPTO_CONTEXT_WORDS = {
    "token", "coin", "crypto", "listing", "binance", "exchange", "pump",
    "dump", "chart", "buy", "sell", "moon", "bullish", "bearish", "hodl",
    "defi", "dex", "cex", "airdrop", "staking", "yield", "tvl", "nft",
    "blockchain", "wallet", "whale", "altcoin", "usdt", "usdc", "btc",
    "eth", "trading", "futures", "leverage", "long", "short", "liquidation",
    "mcap", "market cap", "volume", "price", "ath", "support", "resistance",
    "breakout", "dip", "rally", "accumulation", "distribution",
}
```

## Step 5 — Build db.py

Shared database helpers. Pattern from the codebase: direct psycopg connections (no ORM), batched inserts with `ON CONFLICT DO NOTHING`, `executemany` for efficiency.

```python
import psycopg
import logging
from config import DATABASE_URL

logger = logging.getLogger("db")

def get_connection():
    return psycopg.connect(DATABASE_URL)

def batch_insert_posts(posts: list[dict]):
    """Insert social_posts batch. posts is list of dicts with keys matching columns."""
    if not posts:
        return 0
    sql = """
        INSERT INTO social_posts (source, source_id, channel, author, title, content,
                                  post_ts, engagement_score, symbols, batch_id)
        VALUES (%(source)s, %(source_id)s, %(channel)s, %(author)s, %(title)s, %(content)s,
                %(post_ts)s, %(engagement_score)s, %(symbols)s, %(batch_id)s)
        ON CONFLICT (source, source_id) DO NOTHING
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, posts)
        conn.commit()
    return len(posts)

def load_symbol_aliases() -> dict:
    """Load symbol_aliases into memory. Returns {lowercase_alias: [(symbol, alias_type, is_ambiguous), ...]}"""
    aliases = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, alias, alias_type, is_ambiguous FROM symbol_aliases")
            for symbol, alias, alias_type, is_ambiguous in cur:
                key = alias.lower()
                if key not in aliases:
                    aliases[key] = []
                aliases[key].append((symbol, alias_type, is_ambiguous))
    return aliases
```

Add more helpers as needed (upsert for social_features_1h, insert for social_signals, etc.).

## Step 6 — Build symbol_matcher.py

This is the hardest engineering problem. Implement the tiered matching from the spec:

```python
import re
import logging
from config import CRYPTO_CONTEXT_WORDS

logger = logging.getLogger("symbol_matcher")

class SymbolMatcher:
    def __init__(self, aliases: dict):
        """
        aliases: {lowercase_alias: [(symbol, alias_type, is_ambiguous), ...]}
        Loaded from db.load_symbol_aliases()
        """
        self.aliases = aliases
        # Pre-compile cashtag regex: $DOGE, $BTC etc
        self.cashtag_re = re.compile(r'\$([A-Z]{2,10})\b')
        # Pre-compile word boundary ticker regex
        self.ticker_re = re.compile(r'\b([A-Z]{2,10})\b')
        
        # Build sets for fast lookup
        self.unambiguous_tickers = set()
        self.ambiguous_tickers = set()
        self.all_aliases_lower = set(aliases.keys())
        
        for alias_lower, entries in aliases.items():
            for symbol, alias_type, is_ambiguous in entries:
                if alias_type == 'ticker':
                    if is_ambiguous:
                        self.ambiguous_tickers.add(alias_lower.upper())
                    else:
                        self.unambiguous_tickers.add(alias_lower.upper())
    
    def extract_symbols(self, text: str) -> list[str]:
        """Extract MCAS symbol names from text. Returns deduplicated list."""
        if not text:
            return []
        
        matched = set()
        text_upper = text.upper()
        text_lower = text.lower()
        
        # Tier 1: Cashtags ($DOGE) — always match
        for m in self.cashtag_re.finditer(text):
            ticker = m.group(1)
            key = '$' + ticker.lower()
            if key in self.aliases:
                for symbol, _, _ in self.aliases[key]:
                    matched.add(symbol)
            # Also try without $ prefix
            key2 = ticker.lower()
            if key2 in self.aliases:
                for symbol, _, _ in self.aliases[key2]:
                    matched.add(symbol)
        
        # Tier 2: Unambiguous tickers (DOGE, SOL, PEPE)
        for m in self.ticker_re.finditer(text):
            ticker = m.group(1)
            if ticker in self.unambiguous_tickers:
                key = ticker.lower()
                if key in self.aliases:
                    for symbol, alias_type, _ in self.aliases[key]:
                        if alias_type == 'ticker':
                            matched.add(symbol)
        
        # Tier 3: Ambiguous tickers — only with crypto context nearby
        for m in self.ticker_re.finditer(text):
            ticker = m.group(1)
            if ticker in self.ambiguous_tickers:
                # Check for crypto context within 100 chars
                start = max(0, m.start() - 100)
                end = min(len(text), m.end() + 100)
                context = text_lower[start:end]
                if any(cw in context for cw in CRYPTO_CONTEXT_WORDS):
                    key = ticker.lower()
                    if key in self.aliases:
                        for symbol, alias_type, _ in self.aliases[key]:
                            if alias_type == 'ticker':
                                matched.add(symbol)
        
        # Tier 4: Full names (case-insensitive word boundary)
        # Only do this for names >= 5 chars to avoid false positives
        for alias_lower, entries in self.aliases.items():
            if len(alias_lower) >= 5 and not alias_lower.startswith('$'):
                for symbol, alias_type, _ in entries:
                    if alias_type in ('name', 'common'):
                        # Word boundary match
                        if re.search(r'\b' + re.escape(alias_lower) + r'\b', text_lower):
                            matched.add(symbol)
        
        return sorted(matched)
```

**Important:** The Tier 4 full-name loop is O(n_aliases × n_posts) and could be slow. For Phase 0 this is fine (only Reddit, ~100 posts/cycle). If it becomes a bottleneck later, pre-compile a single alternation regex or use Aho-Corasick. Don't optimise prematurely.

## Step 7 — Build Reddit Collector

`Backend/social_collector/app/collectors/reddit.py`

Use PRAW (Python Reddit API Wrapper). Key design:

```python
import praw
import time
import logging
from datetime import datetime, timezone
import config
from db import batch_insert_posts
from symbol_matcher import SymbolMatcher

logger = logging.getLogger("reddit")

class RedditCollector:
    def __init__(self, matcher: SymbolMatcher):
        self.matcher = matcher
        self.reddit = None
        self.seen_ids = set()  # In-memory dedup for current session
        self._init_client()
    
    def _init_client(self):
        if not config.REDDIT_CLIENT_ID:
            logger.warning("Reddit credentials not configured, collector disabled")
            return
        self.reddit = praw.Reddit(
            client_id=config.REDDIT_CLIENT_ID,
            client_secret=config.REDDIT_CLIENT_SECRET,
            username=config.REDDIT_USERNAME,
            password=config.REDDIT_PASSWORD,
            user_agent=config.REDDIT_USER_AGENT,
        )
        logger.info("Reddit client initialised")
    
    def collect_cycle(self) -> int:
        """One collection cycle. Returns number of posts ingested."""
        if not self.reddit:
            return 0
        
        batch_id = f"reddit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        posts = []
        
        for sub_name in config.REDDIT_SUBREDDITS:
            try:
                subreddit = self.reddit.subreddit(sub_name)
                for submission in subreddit.new(limit=50):
                    if submission.id in self.seen_ids:
                        continue
                    self.seen_ids.add(submission.id)
                    
                    text = (submission.title or "") + " " + (submission.selftext or "")
                    symbols = self.matcher.extract_symbols(text)
                    
                    if not symbols:
                        continue  # Skip posts with no symbol matches
                    
                    posts.append({
                        "source": "reddit",
                        "source_id": submission.id,
                        "channel": sub_name,
                        "author": str(submission.author) if submission.author else None,
                        "title": (submission.title or "")[:500],
                        "content": (submission.selftext or "")[:config.MAX_CONTENT_LENGTH],
                        "post_ts": datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
                        "engagement_score": submission.score,
                        "symbols": symbols,
                        "batch_id": batch_id,
                    })
            except Exception as e:
                logger.error(f"Reddit error on r/{sub_name}: {e}")
                continue
        
        if posts:
            n = batch_insert_posts(posts)
            logger.info(f"Reddit: ingested {n} posts from {len(config.REDDIT_SUBREDDITS)} subs, {len(posts)} with symbol matches")
        
        # Trim seen_ids to prevent unbounded growth (keep last 50K)
        if len(self.seen_ids) > 50000:
            self.seen_ids = set(list(self.seen_ids)[-25000:])
        
        return len(posts)
```

## Step 8 — Build RSS Collector

`Backend/social_collector/app/collectors/rss.py`

Simple feedparser poll. Same structure as Reddit but simpler — no auth, just HTTP GET + parse.

For each RSS feed in `config.RSS_FEEDS`:
1. `feedparser.parse(url)` 
2. For each entry: extract title + summary/description
3. Run symbol_matcher
4. Insert matched posts to social_posts with source='rss', channel=feed name

Dedup by source_id = RSS entry `id` or `link` field.

## Step 9 — Build CoinGecko Trending Collector

`Backend/social_collector/app/collectors/coingecko.py`

CoinGecko's trending endpoint is free, no auth: `GET https://api.coingecko.com/api/v3/search/trending`

This returns trending coins. For each trending coin:
1. Try to match its symbol/name to an MCAS symbol
2. If matched, insert a synthetic post with source='coingecko', content = the trending data
3. This gives a "this coin is trending on CoinGecko" signal even if nobody mentions it on Reddit/Telegram

Engagement score = CoinGecko's `score` field or market_cap_rank.

## Step 10 — Build Telegram Collector (Stub OK for Phase 0)

`Backend/social_collector/app/collectors/telegram.py`

Telegram is the most complex collector because Telethon is async and requires first-auth. **For Phase 0, create a functional stub** that:

1. Checks if `TELEGRAM_API_ID` is configured
2. If not, logs "Telegram collector disabled — no credentials" and returns
3. If configured, attempts to connect using the session file
4. If session doesn't exist, logs "Telegram requires first-auth. Run interactively: docker compose run -it social_collector python -c '...'" and returns
5. If session exists, connects and listens to configured channels

The Telegram channel list should be loaded from a JSON config file `Backend/social_collector/app/telegram_channels.json`. Create it with a reasonable seed list of 20-30 public crypto channels. Research these — they should be real, active, public channels focused on: altcoin calls, whale alerts, listing announcements, or alpha signals. Here's a starting structure:

```json
{
  "channels": [
    {"username": "whale_alert_io", "category": "whale_alerts"},
    {"username": "crypto_trading_signals", "category": "signals"}
  ]
}
```

**Important Telethon design:** Telethon is async (asyncio). The main collector.py orchestrates threading, so Telegram needs to run in its own asyncio event loop. Use `asyncio.run_coroutine_threadsafe` or run it in a dedicated thread with its own loop. Match the pattern of how ingestor.py uses threads for its WebSocket connections.

For each message received from a monitored channel:
1. Extract text
2. Run symbol_matcher
3. If symbols matched, insert to social_posts with source='telegram'
4. Engagement score = message.views or 0

## Step 11 — Build Brain 1 (Hourly Aggregation)

`Backend/social_collector/app/brain1.py`

Runs at :05 past each hour. For the hour that just ended:

```python
def aggregate_hour(hour_start: datetime):
    """Aggregate social_posts for one hour into social_features_1h."""
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
            symbol_data = defaultdict(lambda: {"authors": set(), "platforms": set(), 
                                                "count": 0, "engagement": 0})
            for symbols, author, source, engagement in cur:
                if symbols:
                    for sym in symbols:
                        d = symbol_data[sym]
                        d["count"] += 1
                        d["authors"].add(author)
                        d["platforms"].add(source)
                        d["engagement"] += (engagement or 0)
            
            # 3. For each symbol, compute features and upsert
            for symbol, data in symbol_data.items():
                mention_count = data["count"]
                unique_authors = len(data["authors"])
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
                """, (symbol, hour_start))
                historical_authors = {row[0] for row in cur}
                new_authors = data["authors"] - historical_authors - {None}
                author_novelty_ratio = len(new_authors) / unique_authors if unique_authors > 0 else 0
                
                # First mention ever?
                cur.execute("""
                    SELECT 1 FROM social_features_1h 
                    WHERE symbol = %s AND bar_start_ts < %s LIMIT 1
                """, (symbol, hour_start))
                first_mention = cur.fetchone() is None
                
                # mention_volume_ratio (needs OHLCV join)
                # Compute z-score divergence: social_z - volume_z
                mention_volume_ratio = None
                # ... compute if sufficient history exists (skip for first 24h)
                
                # Upsert
                cur.execute("""
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
                """, (symbol, hour_start, mention_count, unique_authors, platform_count,
                      engagement_sum, mention_velocity, mention_acceleration,
                      author_novelty_ratio, first_mention, mention_volume_ratio))
            
            conn.commit()
    
    return len(symbol_data)
```

**Note on the historical authors query:** This will get slow as social_posts grows. For Phase 0 it's fine. In Phase 3, materialise a `symbol_author_history` table or use an in-memory set that's loaded at startup.

## Step 12 — Build Brain 2 (Anomaly Detection + Haiku Extraction)

`Backend/social_collector/app/brain2.py`

Runs at :10 past each hour. Checks the freshly-written `social_features_1h` rows.

Key design constraints (from the spec):
- Max 100 Haiku calls per hour (hard cap)
- Don't re-trigger for same symbol within 4 hours unless NEW trigger type
- Track token counts, log daily totals, warn if daily spend > $10
- Circuit breaker: 5 consecutive API errors → 30 min backoff

**Trigger conditions** (check the current hour's social_features_1h):

```python
def find_triggers(hour_start: datetime) -> list[dict]:
    triggers = []
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Get this hour's features
            cur.execute("""
                SELECT symbol, mention_count, unique_authors, platform_count,
                       mention_velocity, author_novelty_ratio, first_mention
                FROM social_features_1h
                WHERE bar_start_ts = %s
            """, (hour_start,))
            
            for row in cur:
                symbol, count, authors, platforms, velocity, novelty, first = row
                symbol_triggers = []
                
                # 1. Volume z-score spike
                if count > 0:
                    cur.execute("""
                        SELECT avg(mention_count), stddev(mention_count)
                        FROM social_features_1h
                        WHERE symbol = %s 
                          AND bar_start_ts >= %s 
                          AND bar_start_ts < %s
                    """, (symbol, hour_start - timedelta(hours=24), hour_start))
                    stats = cur.fetchone()
                    if stats and stats[0] and stats[1] and stats[1] > 0:
                        z = (count - stats[0]) / stats[1]
                        if z > 3.0:
                            symbol_triggers.append("volume_z")
                
                # 2. First mention
                if first:
                    symbol_triggers.append("first_mention")
                
                # 3. Author velocity
                if novelty and novelty > 0.7 and authors and authors >= 3:
                    symbol_triggers.append("author_velocity")
                
                # 4. Cross-platform
                if platforms and platforms >= 2 and count and count >= 5:
                    symbol_triggers.append("cross_platform")
                
                if symbol_triggers:
                    triggers.append({
                        "symbol": symbol,
                        "trigger_types": symbol_triggers,
                        "mention_count": count,
                    })
    
    return triggers
```

**Haiku extraction:** Use the exact prompt from `crypto-chatter-build-spec.md` (Section 5). Parse the JSON response, handle malformed responses gracefully (log and skip, don't crash).

**Dedup check:** Before calling Haiku, check:
```sql
SELECT trigger_type FROM social_signals 
WHERE symbol = %s AND trigger_ts > %s
```
If the same trigger_type already fired within 4 hours, skip.

## Step 13 — Build collector.py (Main Entrypoint)

The orchestrator. Uses threads for each collector + scheduled tasks.

```python
import time
import threading
import logging
import sys
from datetime import datetime, timezone, timedelta

import config
from db import load_symbol_aliases
from symbol_matcher import SymbolMatcher
from collectors.reddit import RedditCollector
from collectors.rss import RssCollector
from collectors.coingecko import CoinGeckoCollector
# from collectors.telegram import TelegramCollector  # Enable when configured
from brain1 import aggregate_hour
from brain2 import run_brain2_cycle

logger = logging.getLogger("collector")

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

def collector_loop(collector, interval_s, name):
    """Generic poll loop for a collector."""
    while True:
        try:
            n = collector.collect_cycle()
            logger.info(f"{name}: collected {n} posts")
        except Exception as e:
            logger.error(f"{name} error: {e}", exc_info=True)
        time.sleep(interval_s)

def brain_scheduler():
    """Runs Brain 1 at :05 and Brain 2 at :10 past each hour."""
    while True:
        now = datetime.now(timezone.utc)
        # Wait until :05 past the next hour
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        brain1_time = next_hour + timedelta(minutes=5)
        wait_s = (brain1_time - now).total_seconds()
        if wait_s > 0:
            time.sleep(wait_s)
        
        # Brain 1
        hour_start = next_hour - timedelta(hours=1)  # The hour that just ended
        try:
            n = aggregate_hour(hour_start)
            logger.info(f"Brain 1: aggregated {n} symbols for {hour_start}")
        except Exception as e:
            logger.error(f"Brain 1 error: {e}", exc_info=True)
        
        # Wait 5 more minutes for Brain 2
        time.sleep(300)
        
        # Brain 2
        try:
            n = run_brain2_cycle(hour_start)
            logger.info(f"Brain 2: processed {n} triggers for {hour_start}")
        except Exception as e:
            logger.error(f"Brain 2 error: {e}", exc_info=True)

def main():
    setup_logging()
    logger.info("=== Social Collector starting ===")
    
    # Load symbol aliases
    logger.info("Loading symbol aliases...")
    aliases = load_symbol_aliases()
    logger.info(f"Loaded {len(aliases)} aliases")
    matcher = SymbolMatcher(aliases)
    
    threads = []
    
    # Reddit collector
    if config.REDDIT_CLIENT_ID:
        reddit = RedditCollector(matcher)
        t = threading.Thread(target=collector_loop, 
                           args=(reddit, config.REDDIT_POLL_INTERVAL_S, "Reddit"),
                           daemon=True)
        threads.append(t)
    
    # RSS collector
    rss = RssCollector(matcher)
    t = threading.Thread(target=collector_loop,
                       args=(rss, config.RSS_POLL_INTERVAL_S, "RSS"),
                       daemon=True)
    threads.append(t)
    
    # CoinGecko collector
    cg = CoinGeckoCollector(matcher)
    t = threading.Thread(target=collector_loop,
                       args=(cg, config.COINGECKO_POLL_INTERVAL_S, "CoinGecko"),
                       daemon=True)
    threads.append(t)
    
    # Brain scheduler (Brain 1 + Brain 2)
    t = threading.Thread(target=brain_scheduler, daemon=True)
    threads.append(t)
    
    # Start all
    for t in threads:
        t.start()
    logger.info(f"Started {len(threads)} threads")
    
    # Health check loop
    while True:
        time.sleep(300)
        logger.info(f"Health: {len(threads)} threads alive, "
                    f"{sum(1 for t in threads if t.is_alive())} running")

if __name__ == "__main__":
    main()
```

## Step 14 — Update docker-compose.yml

Add the social_collector service. Also add the `social_telegram_sessions` volume. See `crypto-chatter-build-spec.md` for the exact YAML.

## Step 15 — Deploy and Verify Phase 0

Build and start:
```bash
docker compose build social_collector
docker compose up -d social_collector
docker compose logs -f social_collector
```

Watch for:
1. "Loading symbol aliases... Loaded N aliases" — confirms DB connection + aliases populated
2. "Reddit: collected N posts" — confirms Reddit API working
3. "RSS: collected N posts" — confirms RSS feeds parsing
4. No crash loops

After 30 minutes, verify data:
```sql
-- Post counts by source
SELECT source, count(*), count(DISTINCT channel), 
       min(post_ts), max(post_ts)
FROM social_posts 
GROUP BY source;

-- Symbol match distribution
SELECT unnest(symbols) as sym, count(*) 
FROM social_posts 
GROUP BY sym 
ORDER BY count DESC 
LIMIT 20;

-- Check for false positives on ambiguous tickers
SELECT source_id, title, symbols 
FROM social_posts 
WHERE 'SUNUSDT' = ANY(symbols) OR 'WINUSDT' = ANY(symbols) OR 'ALICEUSDT' = ANY(symbols)
LIMIT 10;
```

After 1+ hours, check Brain 1:
```sql
SELECT count(*), count(DISTINCT symbol), avg(mention_count)
FROM social_features_1h;
```

Report all results.

## Step 16 — Verify Brain 2 (after a few hours)

Check if any triggers have fired:
```sql
SELECT count(*), array_agg(DISTINCT trigger_type) 
FROM social_signals;

-- If any exist, spot-check quality
SELECT symbol, trigger_type, catalyst_type, catalyst_timeline, 
       cascade_stage, confidence, raw_summary
FROM social_signals 
ORDER BY created_at DESC 
LIMIT 5;
```

If Brain 2 hasn't triggered yet (possible if no anomalies), that's fine — it means the z-score thresholds are working. Lower z > 3.0 to z > 2.0 temporarily for testing, then raise back.

## Non-Goals (Don't Do These)

- Don't modify any existing worker code or signal_genesis scripts
- Don't add Telegram first-auth automation — Darren does this manually
- Don't implement LunarCrush historical backfill (Phase 3)
- Don't optimise symbol_matcher for performance yet (Phase 3)
- Don't implement the monthly Opus review cycle for channel quality (Phase 3)
- Don't add any monitoring dashboards or alerts beyond logging
- Don't try to run all 4 collectors in Phase 0 — Reddit + RSS + CoinGecko are the minimum. Telegram is a bonus if credentials are available.

## Quality Bar

Match the quality of `backfill_ohlcv_multi.py` and `ohlcv_persister.py`: clear structure, good logging at each step, proper error handling that doesn't crash the service, batched DB operations, and clean separation of concerns.
