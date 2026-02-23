# Crypto-Chatter Pipeline: Implementation Spec

## Read First

The strategic rationale is in `crypto_chatter_pipeline.md` (the original design doc). Read it for context on WHY this exists. This document is the BUILD spec — what to implement, in what order, with what schema.

## Strategic Context (Brief)

MCAS has tested 528 OHLCV-based strategies. Zero survived walk-forward validation. However, the **ignition model** found a real signal: volatility compression precedes 20%+ price expansions with 5× lift, 11.8% precision, 43% mean MFE. The signal is real but execution was broken (now being fixed separately).

The hypothesis: social data contains **leading** information about upcoming catalysts that is invisible to OHLCV. Specifically, social mention volume diverging from trading volume (people talking but price hasn't moved yet) should predict which compressed coins will ignite and which won't. This pipeline extracts that information and feeds it into the existing signal discovery framework for walk-forward evaluation. Data decides whether it works.

**Two-brain architecture:**
- **Brain 1 (counting):** Continuous, zero LLM cost. Collects raw social posts, computes hourly structural features (velocity, novelty, cross-platform breadth). These become columns in the Signal Genesis feature matrix alongside OHLCV.
- **Brain 2 (reading):** Event-triggered, ~€60-160/month LLM cost. When Brain 1 detects an anomaly, Claude Haiku reads the triggering posts and extracts a 13-dimensional signal vector (catalyst type, specificity, cascade stage, coordination risk, etc.). Sparse features that overlay the ignition model.

---

## Infrastructure Requirements

### New Environment Variables (add to `.env`)

```bash
# Social Collector
REDDIT_CLIENT_ID=           # From https://www.reddit.com/prefs/apps (create "script" app)
REDDIT_CLIENT_SECRET=
REDDIT_USERNAME=            # Reddit account username
REDDIT_PASSWORD=            # Reddit account password
REDDIT_USER_AGENT=mcas-research/1.0

TELEGRAM_API_ID=            # From https://my.telegram.org
TELEGRAM_API_HASH=
TELEGRAM_PHONE=             # Phone number for first-time auth (interactive)

ANTHROPIC_API_KEY=          # For Brain 2 Haiku calls
```

### New Python Packages (add to a NEW `social_collector/requirements.txt`)

```
psycopg[binary]
redis
requests
numpy
praw                    # Reddit API
telethon                # Telegram client
feedparser              # RSS parsing
anthropic               # Brain 2 LLM calls
```

### New Docker Service

Add to `docker-compose.yml`:

```yaml
  social_collector:
    build:
      context: ./social_collector
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file:
      - .env
    environment:
      SERVICE_NAME: social_collector
    volumes:
      - ./social_collector/app:/app
      - social_telegram_sessions:/app/sessions  # Persist Telegram auth
    networks:
      - backend

volumes:
  # ... existing volumes ...
  social_telegram_sessions:
```

**Why a separate service, not the existing worker:** Different dependencies (praw, telethon, anthropic, feedparser), different lifecycle (continuous collection vs queue-driven jobs), different failure modes (API rate limits vs compute-bound).

---

## Database Schema

```sql
-- ═══════════════════════════════════════════════════════════
-- RAW SOCIAL POSTS
-- ═══════════════════════════════════════════════════════════

CREATE TABLE social_posts (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,            -- 'reddit', 'telegram', 'rss', 'coingecko'
    source_id TEXT,                  -- Platform-specific post ID (dedup key)
    channel TEXT,                    -- Subreddit name, Telegram channel, RSS feed
    author TEXT,
    title TEXT,                      -- Reddit title / Telegram N/A / RSS headline
    content TEXT,                    -- Body text (truncate to 2000 chars at ingestion)
    post_ts TIMESTAMPTZ NOT NULL,    -- When the post was created on-platform
    ingested_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    engagement_score INT DEFAULT 0,  -- Upvotes, views, reactions (platform-dependent)
    symbols TEXT[],                  -- Matched MCAS symbols (e.g., {'BTCUSDT','ETHUSDT'})
    batch_id TEXT,                   -- Ingestion batch provenance
    UNIQUE(source, source_id)
);

CREATE INDEX idx_social_posts_ts ON social_posts(post_ts DESC);
CREATE INDEX idx_social_posts_symbols ON social_posts USING GIN(symbols);
CREATE INDEX idx_social_posts_source ON social_posts(source, channel, post_ts DESC);

-- ═══════════════════════════════════════════════════════════
-- BRAIN 1: HOURLY AGGREGATED FEATURES (aligned to OHLCV grid)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE social_features_1h (
    symbol TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,   -- Aligned to hour boundary (matches ohlcv)
    
    -- Raw counts
    mention_count INT NOT NULL DEFAULT 0,
    unique_authors INT NOT NULL DEFAULT 0,
    platform_count INT NOT NULL DEFAULT 0,
    engagement_sum INT NOT NULL DEFAULT 0,
    
    -- Derived (computed by aggregation job)
    mention_velocity DOUBLE PRECISION,       -- hour-over-hour change ratio
    mention_acceleration DOUBLE PRECISION,   -- velocity change
    author_novelty_ratio DOUBLE PRECISION,   -- fraction first-time-for-this-symbol authors
    first_mention BOOLEAN NOT NULL DEFAULT FALSE,  -- Symbol's first-ever social appearance
    mention_volume_ratio DOUBLE PRECISION,   -- mention_count normalised by OHLCV volume z-score
    
    -- Provenance
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    PRIMARY KEY (symbol, bar_start_ts)
);

-- ═══════════════════════════════════════════════════════════
-- BRAIN 2: LLM SIGNAL VECTORS (sparse, event-triggered)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE social_signals (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    trigger_ts TIMESTAMPTZ NOT NULL,         -- When Brain 1 anomaly fired
    trigger_type TEXT NOT NULL,              -- 'volume_z', 'first_mention', 'author_velocity', 'cross_platform'
    
    -- Event signals
    catalyst_detected BOOLEAN,
    catalyst_type TEXT,                      -- 'exchange_listing_rumor','partnership','upgrade','airdrop','influencer','whale','none'
    catalyst_timeline TEXT,                  -- 'imminent_24h','near_term_7d','speculative','none'
    catalyst_specificity DOUBLE PRECISION,   -- 0.0-1.0
    catalyst_verifiability DOUBLE PRECISION, -- 0.0-1.0
    
    -- Narrative signals
    narrative_category TEXT,                 -- 'AI','RWA','meme','DeFi','gaming','L2','privacy','none'
    narrative_novelty DOUBLE PRECISION,
    cascade_stage TEXT,                      -- 'emergence','acceleration','amplification','saturation','decay'
    
    -- Source quality
    discussion_quality DOUBLE PRECISION,
    author_credibility DOUBLE PRECISION,
    source_diversity DOUBLE PRECISION,
    
    -- Risk signals
    coordination_probability DOUBLE PRECISION,
    controversy_level DOUBLE PRECISION,
    
    -- Meta
    confidence DOUBLE PRECISION,
    raw_summary TEXT,
    model_used TEXT NOT NULL DEFAULT 'claude-haiku-4-5-20251001',
    input_token_count INT,
    output_token_count INT,
    input_posts JSONB,                       -- The posts that were analysed
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_social_signals_symbol_ts ON social_signals(symbol, trigger_ts DESC);
CREATE INDEX idx_social_signals_cascade ON social_signals(cascade_stage, trigger_ts DESC);

-- ═══════════════════════════════════════════════════════════
-- SYMBOL ALIASES (for fuzzy matching)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE symbol_aliases (
    symbol TEXT NOT NULL,          -- MCAS symbol e.g. 'DOGEUSDT'
    alias TEXT NOT NULL,           -- Matchable string e.g. 'DOGE', 'dogecoin', '$DOGE'
    alias_type TEXT NOT NULL,      -- 'ticker', 'name', 'cashtag', 'common'
    is_ambiguous BOOLEAN DEFAULT FALSE,  -- TRUE for generic words like 'SUN', 'WIN', 'ALICE'
    PRIMARY KEY (symbol, alias)
);

CREATE INDEX idx_symbol_aliases_alias ON symbol_aliases(LOWER(alias));
```

---

## Service Architecture

The `social_collector` service runs four concurrent subsystems in one process:

```
social_collector/
├── Dockerfile
├── requirements.txt
└── app/
    ├── collector.py          # Main entrypoint, starts all subsystems
    ├── config.py             # Env vars, constants, symbol alias loader
    ├── db.py                 # Shared DB connection pool + batch insert helpers
    ├── symbol_matcher.py     # Symbol extraction from text (see below)
    ├── collectors/
    │   ├── reddit.py         # PRAW collector (15-min cycle)
    │   ├── telegram.py       # Telethon continuous listener
    │   ├── rss.py            # feedparser poll (5-min cycle)
    │   └── coingecko.py      # Trending endpoint (30-min cycle)
    ├── brain1.py             # Hourly aggregation job
    └── brain2.py             # Anomaly detection + Haiku extraction
```

### Main Loop (`collector.py`)

```python
# Pseudocode — all subsystems run as threads/asyncio tasks in one process
async def main():
    # 1. Load symbol aliases from DB into memory
    # 2. Start collectors (each is its own async task / thread)
    #    - Reddit: poll every 15 min
    #    - Telegram: continuous async listener
    #    - RSS: poll every 5 min
    #    - CoinGecko: poll every 30 min
    # 3. Start Brain 1 hourly aggregation (runs at :05 past each hour)
    # 4. Start Brain 2 anomaly checker (runs at :10 past each hour, after Brain 1)
    # 5. Health check loop: log stats every 5 min
```

---

## Critical Implementation Details

### 1. Symbol Matching (the hardest part of Brain 1)

**Problem:** 588 USDT symbols. Many have generic tickers: SUN, WIN, ALICE, PEOPLE, SPELL, RARE, LEVER, HIGH, LOOM. Naive regex will match normal English text.

**Solution:** Tiered matching.

```
Tier 1 — Cashtag: $DOGE, $SOL, $PEPE
         Always match. Zero ambiguity. This is deliberate financial reference.

Tier 2 — Unambiguous ticker in crypto context:
         Match DOGE, SOL, PEPE, SHIB etc. when surrounded by word boundaries
         AND post is from a crypto-specific source (all our sources are).
         Use a whitelist of unambiguous tickers (~400 of the 588).

Tier 3 — Ambiguous tickers: SUN, WIN, ALICE, PEOPLE, HIGH, etc.
         Only match when: (a) cashtag form ($SUN), OR (b) accompanied by
         crypto context words within 50 chars ("token", "listing", "pump",
         "chart", "buy", "sell", "moon", "binance", etc.)

Tier 4 — Token full names: "dogecoin", "solana", "chainlink"
         Match with word boundaries. Low ambiguity for most.
```

**Populate `symbol_aliases` at startup:** Query the Binance exchange info endpoint for all USDT pairs, extract base asset as ticker, populate aliases. Mark known-ambiguous tickers manually (maintain a short blacklist of ~30 generic English words that happen to be tickers).

### 2. Telegram First-Auth

Telethon requires interactive phone-code verification on first run. Handle this by:
1. On first startup, if no session file exists, log a clear message and prompt for code via stdin
2. After auth, session is persisted in the Docker volume `social_telegram_sessions`
3. Subsequent restarts use the saved session (no interaction needed)

**Darren will need to do the first auth manually** by running the container interactively once:
```bash
docker compose run -it social_collector python -c "from telethon.sync import TelegramClient; c = TelegramClient('/app/sessions/mcas', API_ID, API_HASH); c.start(phone=PHONE); print('Auth complete')"
```

### 3. Telegram Channel List

Start with a curated seed list stored in a config file (not hardcoded). JSON or simple text file mounted into the container:

```json
{
  "channels": [
    {"name": "WhaleTrades", "id": null, "category": "whale_alerts"},
    {"name": "crypto_alpha_group", "id": null, "category": "alpha_calls"},
    // ... 20-50 channels
  ]
}
```

**The initial channel list should be researched by Claude Code at build time.** Search for: public Telegram crypto channels with >10K members that focus on altcoin calls, whale alerts, listing announcements, or alpha signals. Populate 30-50 high-signal channels. This list is a living document — Brain 2's Opus review cycle updates it monthly based on which channels produced pre-ignition signals.

### 4. Brain 1 Hourly Aggregation

Runs at :05 past each hour (allows 5 minutes for late-arriving posts):

```
For each hour H:
  1. Query social_posts WHERE post_ts >= H AND post_ts < H+1h
  2. GROUP BY symbol (from the symbols[] array — one post can map to multiple symbols)
  3. Compute:
     - mention_count, unique_authors, platform_count, engagement_sum (direct from query)
     - mention_velocity = mention_count / prev_hour_mention_count (query previous row)
     - mention_acceleration = velocity / prev_velocity
     - author_novelty_ratio = (authors NOT IN historical author set for this symbol) / unique_authors
     - first_mention = TRUE if symbol has zero rows in social_features_1h before this hour
     - mention_volume_ratio = needs OHLCV volume from the same hour (JOIN against ohlcv table)
  4. UPSERT into social_features_1h
```

**mention_volume_ratio computation:** This requires joining OHLCV data. Compute it as:
```
mention_z = (mention_count - rolling_24h_mean) / rolling_24h_std
volume_z = (ohlcv_volume - rolling_24h_mean_volume) / rolling_24h_std_volume
mention_volume_ratio = mention_z - volume_z
```
Positive values = social hot, market quiet = pre-ignition divergence signal.

For symbols with zero mentions, don't insert a row (sparse storage). The research scripts treat missing rows as zero.

### 5. Brain 2 Anomaly Detection + Extraction

Runs at :10 past each hour (after Brain 1 completes):

**Trigger conditions** (check the freshly-written `social_features_1h` row for each symbol):

```python
triggers = []

# 1. Volume spike: mentions jumped vs recent baseline
if mention_count > 0:
    rolling_mean = avg(last 24h mention_counts for this symbol)
    rolling_std = std(last 24h)
    if rolling_std > 0:
        z = (mention_count - rolling_mean) / rolling_std
        if z > 3.0:
            triggers.append('volume_z')

# 2. First mention
if first_mention:
    triggers.append('first_mention')

# 3. Author velocity (new people talking about this)
if author_novelty_ratio > 0.7 and unique_authors >= 3:
    triggers.append('author_velocity')

# 4. Cross-platform (2+ platforms in same hour)
if platform_count >= 2 and mention_count >= 5:
    triggers.append('cross_platform')
```

**For each triggered symbol:**
1. Fetch the 15-20 most recent `social_posts` for that symbol from the past 4 hours, ordered by engagement_score DESC
2. Format as a batch for the Haiku extraction prompt
3. Call Anthropic API with structured output prompt (see below)
4. Parse response into `social_signals` row
5. Insert

**Haiku Extraction Prompt:**

```
You are analysing social media posts about a cryptocurrency token to extract structured signals for a trading research system.

Token: {symbol}
Trigger: {trigger_type} at {trigger_ts}

Posts (ordered by engagement, most recent first):
---
{formatted_posts}
---

Extract the following as JSON. Use null for any field you cannot determine with reasonable confidence.

{
  "catalyst_detected": bool,
  "catalyst_type": "exchange_listing_rumor|partnership|upgrade|airdrop|influencer|whale|none",
  "catalyst_timeline": "imminent_24h|near_term_7d|speculative|none",
  "catalyst_specificity": float 0.0-1.0 (does it name specific entities, dates, transactions?),
  "catalyst_verifiability": float 0.0-1.0 (could this be confirmed on-chain or via official sources?),
  "narrative_category": "AI|RWA|meme|DeFi|gaming|L2|privacy|none",
  "narrative_novelty": float 0.0-1.0 (fresh claim vs recycled content?),
  "cascade_stage": "emergence|acceleration|amplification|saturation|decay",
  "discussion_quality": float 0.0-1.0 (substantive analysis vs hype/spam ratio),
  "author_credibility": float 0.0-1.0 (specific claims, technical detail, track record mentions),
  "source_diversity": float 0.0-1.0 (independent voices or echo chamber?),
  "coordination_probability": float 0.0-1.0 (does this look like organised shilling?),
  "controversy_level": float 0.0-1.0 (active debate/FUD alongside hype?),
  "confidence": float 0.0-1.0 (your overall confidence in this extraction),
  "raw_summary": "One sentence: what is the internet saying about this token right now?"
}

Respond with ONLY the JSON object. No preamble, no markdown fences.
```

### 6. Brain 2 Cost Controls

- **Rate limit:** Max 100 Haiku calls per hour (hard cap). If more than 100 symbols trigger, prioritise by z-score magnitude.
- **Dedup:** Don't re-trigger Brain 2 for the same symbol within 4 hours unless a NEW trigger type fires.
- **Token budget:** Track `input_token_count` and `output_token_count` per call. Log daily totals. Alert if daily spend exceeds $10 (via log warning).
- **Circuit breaker:** If Anthropic API returns errors on 5 consecutive calls, back off for 30 minutes.

---

## Integration with Signal Genesis

Research scripts load social features alongside OHLCV. The join is straightforward:

```python
# In signal_genesis_ignition.py or similar:
# After loading OHLCV matrix (n_bars, n_symbols):

social_query = """
    SELECT symbol, bar_start_ts, mention_count, unique_authors, platform_count,
           mention_velocity, mention_acceleration, author_novelty_ratio,
           first_mention, mention_volume_ratio
    FROM social_features_1h
    WHERE bar_start_ts >= %s AND bar_start_ts <= %s
    ORDER BY bar_start_ts, symbol
"""
# Pivot into (n_bars, n_symbols) matrices matching OHLCV alignment
# Missing values = 0 (no social activity for that symbol-hour)
```

**Derived features for the feature matrix** (computed in the research script, not stored):
```python
social_mention_z_8      = zscore(mention_count, window=8)
social_mention_z_24     = zscore(mention_count, window=24)
social_velocity_8       = rolling_mean(mention_velocity, window=8)
social_acceleration_4   = rolling_mean(mention_acceleration, window=4)
social_author_novelty   = author_novelty_ratio  # raw
social_platform_count   = platform_count         # raw
social_first_mention_24 = any(first_mention, window=24)  # binary, within 24h
social_engagement_z_24  = zscore(engagement_sum, window=24)
social_mention_volume_divergence = mention_volume_ratio  # pre-computed in Brain 1
```

These go through the same IC screening, walk-forward, and DSR pipeline as OHLCV features. No special treatment.

---

## Implementation Phases

### Phase 0: Schema + Reddit Collector (Day 1)

1. Create all tables (run schema SQL against production DB)
2. Populate `symbol_aliases` from Binance exchange info endpoint
3. Build and deploy `social_collector` service with Reddit collector only
4. Verify posts are flowing into `social_posts` with correct symbol matching
5. Check: how many posts/hour? How many symbols getting matched? False positive rate on ambiguous tickers?

**Success criteria:** 100+ posts/hour ingested, 50+ unique symbols matched per day, <5% obvious false positives on manual spot-check.

### Phase 1: Full Collection + Brain 1 (Days 2-4)

1. Add RSS collector
2. Add CoinGecko trending collector
3. Add Telegram collector (Darren does first-auth interactively, then it runs unattended)
4. Build Brain 1 hourly aggregation job
5. Verify `social_features_1h` is populating correctly
6. Check: mention_velocity and mention_acceleration producing sensible values? mention_volume_ratio correctly joining OHLCV?

**Success criteria:** All 4 sources collecting. Brain 1 producing hourly rows for 100+ symbols. Features have non-degenerate distributions.

### Phase 2: Brain 2 (Days 5-7)

1. Implement anomaly detection triggers
2. Implement Haiku extraction pipeline with cost controls
3. Deploy and monitor for 48 hours
4. Check: trigger rate (expect 20-80/hour initially), extraction quality (manual review of 20 random signals), cost tracking

**Success criteria:** Brain 2 triggering appropriately (not too noisy, not too quiet), extractions are sensible on manual review, daily cost <$10.

### Phase 3: Accumulation (Weeks 2-4)

1. Let it collect for 2-3 weeks undisturbed
2. Daily monitoring: post volume by source, Brain 2 trigger rate, cost
3. Tune thresholds if needed (z-score trigger too sensitive/insensitive, ambiguous symbol matching)
4. After 2 weeks: run exploratory analysis on accumulated data — what's the actual mention_volume_divergence distribution look like? Do Brain 2 signals cluster before known price moves?

### Phase 4: Signal Genesis Integration (Week 4+)

1. Add social features to ignition discovery script
2. Run walk-forward: OHLCV-only vs OHLCV+Brain1 vs OHLCV+Brain1+Brain2
3. Data decides whether social features survive IC screening

---

## Known Issues and Honest Limitations

### 1. No Historical Social Data for Backtesting

This is the biggest limitation. Walk-forward validation needs months of aligned data. We'll have 2-3 weeks when Phase 4 starts. Options:
- **LunarCrush ($24/month):** Provides historical aggregated social volume back to 2017. Can bootstrap a proxy for `mention_count` and `mention_velocity`. Cannot proxy Brain 2 features (those are LLM-extracted and have no historical equivalent).
- **Accept forward-only validation initially.** Run the pipeline, accumulate 3+ months, then do proper walk-forward. This is slower but honest.
- **Use daily data as bridge.** We have 585 days of 1d OHLCV for 367 symbols. If LunarCrush daily social volume aligns, we can test the divergence hypothesis on daily resolution first, then refine on hourly once we have the data.

**Recommendation:** Subscribe to LunarCrush ($24/month), backfill daily social volume, test the mention_volume_divergence hypothesis on daily data immediately. This gives us signal/no-signal evidence in days, not months.

### 2. Symbol Matching Will Have Edge Cases

No amount of regex engineering will perfectly disambiguate "ALICE" the token from "Alice" the person. The tiered approach handles 95% of cases. The remaining 5% will create noise in low-mention symbols. Accept this and filter by minimum mention_count ≥ 3 in the feature matrix.

### 3. Telegram Channel Quality is Everything

The pipeline is only as good as the channels it monitors. Bad channels (spam, paid promotions, scam calls) will produce high-volume garbage that triggers Brain 2 constantly. Good channels (genuine alpha, whale tracking, insider circles) produce the Stage 2 signals we need. The initial channel list must be researched carefully. Expect to prune 30-50% of seed channels within the first month.

### 4. Brain 2 Extraction Quality is Unvalidated

Haiku's ability to reliably score `catalyst_specificity` or `coordination_probability` on crypto social posts is assumed, not proven. Build in a weekly calibration check: run 20 random Brain 2 inputs through Sonnet and compare outputs. If Haiku and Sonnet materially disagree, the Haiku prompt needs tuning.

### 5. Reddit API Terms

Reddit's API terms prohibit "commercial use" on free tier. MCAS is a personal research project, not a commercial product. If this changes, Reddit API Basic is $100/month.

---

## Cost Summary

| Component | Monthly Cost |
|---|---|
| Reddit API | Free (research use) |
| Telegram API | Free |
| RSS / CoinGecko | Free |
| LunarCrush (historical backfill) | €24 |
| Brain 2 LLM (Haiku, ~€3-5/day) | €90-150 |
| Anthropic API overhead | ~€10 |
| **Total** | **€125-185/month** |

---

## Files to Examine in MCAS Codebase

Before building, read these for patterns and conventions:
- `Backend/docker-compose.yml` — service definitions, network, volumes
- `Backend/.env.example` — env var patterns
- `Backend/docs/db/schema/mcas_schema.sql` — existing table conventions
- `Backend/scripts/backfill_ohlcv_multi.py` — best example of a well-built data pipeline (ThreadPoolExecutor, adaptive rate limiting, batched DB writes, resume support)
- `Backend/worker/app/indicators.py` — how features are computed from OHLCV
- `Backend/worker/app/ohlcv_persister.py` — how real-time data flows to DB
- `Backend/scripts/signal_genesis_ignition.py` — the primary consumer of social features
