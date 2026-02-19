# MCAS On-Chain Data Pipeline — Build Specification

## Your Role

You are building a derivatives data collection and feature engineering pipeline for MCAS (Multi-Currency Algorithmic Strategies), a cryptocurrency trading research platform. This is a greenfield addition to an existing, well-structured codebase. Follow the patterns already established in the codebase — don't reinvent.

## Context You Need

MCAS runs signal discovery against 588 Binance USDT spot pairs using 1h OHLCV data stored in PostgreSQL. The key finding: an "ignition model" detects volatility compression preceding 20%+ price expansions with 5× lift over base rate. OHLCV features alone are necessary but may not be sufficient — derivatives positioning data (funding rates, open interest, long/short ratios) captures information invisible to price/volume: where leveraged money is placed before the move happens.

The hypothesis: derivatives features provide leading information about upcoming moves that is invisible to OHLCV. If true, adding them to the ignition model improves precision. If false, the same walk-forward validation that rejected 528 strategies will reject these features too.

## Step 0 — Orientation
Read CLAUDE.md then:

Read these files to understand existing patterns:
- `Backend/docker-compose.yml` — service architecture
- `Backend/.env` (or `.env.example`) — config variables, especially DATABASE_URL
- `Backend/docs/db/schema/mcas_schema.sql` — current schema
- `Backend/scripts/backfill_ohlcv_multi.py` — THE reference implementation for data collection. Match its patterns: ThreadPoolExecutor(20), separate DB writer thread, adaptive rate limiting from X-MBX-USED-WEIGHT headers, batched inserts, resume-from-existing, ON CONFLICT DO NOTHING.
- `Backend/worker/requirements.txt` — available packages (psycopg, redis, requests, numpy, websocket-client, python-json-logger). Do NOT add new packages unless absolutely necessary.
- `Backend/scripts/signal_genesis_ignition.py` lines 1-280 — how OHLCV data is loaded into (n_bars, n_symbols) numpy matrices. Your derivatives features must integrate with this pattern.

## Step 1 — Run Symbol Mapping

The file `map_futures_symbols.py` is in `Backend/scripts/`  run it:

```bash
docker compose run --rm -T worker python3 /scripts/map_futures_symbols.py
```

This will:
1. Query your OHLCV DB for distinct spot symbols
2. Query Binance Futures API for active USDT-M perpetuals
3. Compute the overlap
4. Test all API endpoints we'll use
5. Estimate collection load
6. Write `futures_symbol_mapping.json`

**Read the output carefully.** The overlap count determines the scope of everything downstream. Expected: 350-450 symbols with both spot and futures data (60-70% coverage).

If any API endpoint test fails, investigate and fix before proceeding.

## Step 2 — Create Database Migration

Create `Backend/docs/db/migrations/YYYYMMDD_add_derivatives_tables.sql` (use today's date).

Schema — use this exactly:

```sql
-- Migration: Add derivatives data tables for on-chain pipeline
-- Follows MCAS migration conventions (IF NOT EXISTS, advisory locks)

DO $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('add_derivatives_tables'));
END $$;

-- ═══════════════════════════════════════════════════════════════
-- RAW DATA TABLES (append-only, full provenance)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS funding_rates (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    funding_rate DOUBLE PRECISION NOT NULL,
    funding_time TIMESTAMPTZ NOT NULL,
    mark_price DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'binance_futures',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, funding_time, source)
);
CREATE INDEX IF NOT EXISTS idx_funding_rates_lookup
    ON funding_rates(symbol, funding_time DESC);

CREATE TABLE IF NOT EXISTS open_interest (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    sum_open_interest DOUBLE PRECISION NOT NULL,
    sum_open_interest_value DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL DEFAULT 'binance_futures',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_open_interest_lookup
    ON open_interest(symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS long_short_ratio (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    long_short_ratio DOUBLE PRECISION NOT NULL,
    long_account DOUBLE PRECISION,
    short_account DOUBLE PRECISION,
    long_position DOUBLE PRECISION,
    short_position DOUBLE PRECISION,
    ratio_type TEXT NOT NULL DEFAULT 'global',
    source TEXT NOT NULL DEFAULT 'binance_futures',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, ratio_type, source)
);
CREATE INDEX IF NOT EXISTS idx_long_short_ratio_lookup
    ON long_short_ratio(symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS taker_buy_sell (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    buy_vol DOUBLE PRECISION NOT NULL,
    sell_vol DOUBLE PRECISION NOT NULL,
    buy_sell_ratio DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'binance_futures',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_taker_buy_sell_lookup
    ON taker_buy_sell(symbol, timestamp DESC);

-- ═══════════════════════════════════════════════════════════════
-- MATERIALIZED FEATURE TABLE (rebuilt hourly by feature pipeline)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS derivatives_features_1h (
    symbol TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,

    -- Funding rate features
    funding_rate DOUBLE PRECISION,
    funding_rate_ma8 DOUBLE PRECISION,
    funding_rate_acceleration DOUBLE PRECISION,
    funding_rate_zscore DOUBLE PRECISION,

    -- Open interest features
    oi_value DOUBLE PRECISION,
    oi_change_pct_1h DOUBLE PRECISION,
    oi_change_pct_4h DOUBLE PRECISION,
    oi_change_pct_24h DOUBLE PRECISION,
    oi_volume_ratio DOUBLE PRECISION,

    -- Long/short features
    ls_ratio DOUBLE PRECISION,
    ls_ratio_change_4h DOUBLE PRECISION,
    ls_ratio_extreme DOUBLE PRECISION,

    -- Taker flow features
    taker_buy_ratio DOUBLE PRECISION,
    taker_imbalance DOUBLE PRECISION,
    taker_imbalance_ma DOUBLE PRECISION,

    -- Metadata
    data_completeness DOUBLE PRECISION,
    materialized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (symbol, bar_start_ts)
);
CREATE INDEX IF NOT EXISTS idx_deriv_features_ts
    ON derivatives_features_1h(bar_start_ts DESC);

-- Track migration
INSERT INTO schema_versions (version, description)
VALUES ('add_derivatives_tables', 'Raw derivatives data tables + materialized 1h feature table')
ON CONFLICT DO NOTHING;
```

Apply the migration:
```bash
docker compose exec postgres psql -U trading -d trading -f /path/to/migration.sql
```

Or if the migration file isn't mounted, paste the SQL into:
```bash
docker compose exec postgres psql -U trading -d trading
```

Verify tables exist before proceeding.

## Step 3 — Build derivatives_collector.py

Create `Backend/scripts/derivatives_collector.py`. This is the main deliverable.

### Architecture

```
derivatives_collector.py
├── STARTUP
│   ├── Load futures_symbol_mapping.json (or re-discover from API)
│   ├── Resume: query max(timestamp) per table per symbol
│   └── Backfill: fetch historical data for all symbols up to max available depth
├── CONTINUOUS LOOP
│   ├── Every ~60 min: collect OI stats, L/S ratio (3 types), taker buy/sell
│   ├── Every ~4 hrs: collect funding rates
│   ├── After each collection: materialize features
│   └── Sleep until next cycle
├── COLLECTION ENGINE (match backfill_ohlcv_multi.py patterns)
│   ├── ThreadPoolExecutor(20) for parallel HTTP fetches
│   ├── Separate DB writer thread consuming from queue.Queue
│   ├── Batched INSERT ... ON CONFLICT DO NOTHING
│   ├── Adaptive rate limiting: read X-MBX-USED-WEIGHT-1M header
│   ├── Exponential backoff on 429/418 responses
│   └── Per-request retry with backoff on transient errors
└── FEATURE MATERIALIZATION
    ├── Read raw tables for last N hours
    ├── Align to 1h grid (floor timestamps)
    ├── Compute derived features (MAs, z-scores, ratios, changes)
    └── UPSERT into derivatives_features_1h
```

### Binance Futures API Endpoints

All endpoints are under `https://fapi.binance.com`. No API key needed for market data.

**Funding Rate History** — `GET /fapi/v1/fundingRate`
- Params: `symbol`, `startTime` (ms), `endTime` (ms), `limit` (max 1000)
- Weight: shared 500/5min rate limit with fundingInfo
- Returns: `[{symbol, fundingRate, fundingTime, markPrice}]`
- History: full (back to pair listing)
- Collect: every 4 hours, backfill in 1000-record pages

**Open Interest Statistics** — `GET /futures/data/openInterestHist`
- Params: `symbol`, `period` (5m/15m/30m/1h/2h/4h/6h/12h/1d), `startTime`, `endTime`, `limit` (max 500)
- Weight: 5 per request
- Returns: `[{symbol, sumOpenInterest, sumOpenInterestValue, timestamp}]`
- History: ~30 days max
- Collect: every hour with period=1h, backfill from 30d ago

**Global Long/Short Account Ratio** — `GET /futures/data/globalLongShortAccountRatio`
- Params: `symbol`, `period`, `startTime`, `endTime`, `limit` (max 500)
- Weight: 1
- Returns: `[{symbol, longShortRatio, longAccount, shortAccount, timestamp}]`
- History: ~30 days
- Store with ratio_type='global'

**Top Trader Long/Short Position Ratio** — `GET /futures/data/topLongShortPositionRatio`
- Same params/limits. Store with ratio_type='top_trader_position'

**Top Trader Long/Short Account Ratio** — `GET /futures/data/topLongShortAccountRatio`
- Same params/limits. Store with ratio_type='top_trader_account'

**Taker Buy/Sell Volume** — `GET /futures/data/takerlongshortRatio`
- Params: same pattern
- Returns: `[{buySellRatio, sellVol, buyVol, timestamp}]`
- History: ~30 days

### Rate Limit Strategy

Binance Futures rate limits are SEPARATE from spot (2400 weight/min for futures market data). At ~400 symbols:
- OI stats: 400 × 5w = 2000w (spread over ~2 minutes)
- Other 4 endpoints: 400 × 1w × 4 = 1600w
- Total per cycle: ~3600w → needs ~2 minutes at max throughput

Use the same adaptive approach as backfill_ohlcv_multi.py: read `X-MBX-USED-WEIGHT-1M` from response headers, pause when approaching 2000w, resume when weight resets.

### Feature Materialization Logic

The materialization step runs after each collection cycle. Key computations:

```python
# For each symbol with futures data:

# Funding rate features (funding_rates table → hourly grid)
# funding rates are every 4h or 8h, forward-fill to hourly
funding_rate            = forward_filled latest funding rate
funding_rate_ma8        = 8-period (8h for 8h funding, or 8 intervals) moving average
funding_rate_acceleration = funding_rate[t] - funding_rate[t-3]  (3-interval diff)
funding_rate_zscore     = (funding_rate - rolling_30d_mean) / rolling_30d_std

# OI features (open_interest table, already hourly)
oi_value                = sum_open_interest_value
oi_change_pct_1h        = (oi[t] - oi[t-1]) / oi[t-1]
oi_change_pct_4h        = (oi[t] - oi[t-4]) / oi[t-4]
oi_change_pct_24h       = (oi[t] - oi[t-24]) / oi[t-24]
oi_volume_ratio         = oi_value / spot_volume_1h  (needs OHLCV join)
    # Note: for initial build, skip this one — it requires OHLCV join.
    # Set to NULL. We'll add it when integrating with signal_genesis.

# Long/short features (long_short_ratio table, ratio_type='global')
ls_ratio                = long_short_ratio
ls_ratio_change_4h      = ls_ratio[t] - ls_ratio[t-4]
ls_ratio_extreme        = percentile_rank(ls_ratio, window=720)  # 30 days of hours

# Taker features (taker_buy_sell table)
taker_buy_ratio         = buy_vol / (buy_vol + sell_vol)
taker_imbalance         = (buy_vol - sell_vol) / (buy_vol + sell_vol)
taker_imbalance_ma      = 4-period moving average of taker_imbalance

# Metadata
data_completeness       = count_non_null_features / total_features
```

Use numpy for vectorized computation where possible. Process all symbols in one pass if memory allows (same as signal_genesis pattern).

### Backfill Mode

On first run (or with `--backfill` flag):
1. For each overlapping symbol, query max(timestamp) from each raw table
2. Fetch history from that point back to 30 days ago (or pair listing date, whichever is later)
3. For funding rates, fetch full history (no 30d limit)
4. Use ThreadPoolExecutor(20) with DB writer queue, same as backfill_ohlcv_multi.py
5. After backfill completes, materialize features for the full backfilled period
6. Then enter continuous collection mode

### Docker Service

Add to `Backend/docker-compose.yml`:

```yaml
  derivatives_collector:
    build:
      context: ./worker
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file:
      - .env
    environment:
      SERVICE_NAME: derivatives_collector
      COLLECTION_INTERVAL_S: "3600"
      FUNDING_INTERVAL_S: "14400"
    command: ["python", "-u", "/scripts/derivatives_collector.py"]
    volumes:
      - ./scripts:/scripts
    networks:
      - backend
```

### Logging

Match existing pattern: `logging.basicConfig(level=INFO, format="%(asctime)s [%(levelname)s] %(message)s")`. Log collection cycle start/end, symbols processed, rows inserted, errors, and rate limit state. Be verbose — this is a data pipeline, observability matters.

### Error Handling

- Individual symbol failures should not stop the cycle. Log the error, continue to next symbol.
- API 429/418 → exponential backoff (same as backfill_ohlcv_multi.py).
- DB connection failures → retry with backoff, max 3 attempts per cycle.
- If >50% of symbols fail in a cycle, log a CRITICAL and continue (don't crash — the service should self-heal on the next cycle).

## Step 4 — Verify

After building and running:

1. Run backfill mode, let it complete
2. Check row counts:
```sql
SELECT 'funding_rates' as tbl, count(*) FROM funding_rates
UNION ALL SELECT 'open_interest', count(*) FROM open_interest
UNION ALL SELECT 'long_short_ratio', count(*) FROM long_short_ratio
UNION ALL SELECT 'taker_buy_sell', count(*) FROM taker_buy_sell
UNION ALL SELECT 'derivatives_features_1h', count(*) FROM derivatives_features_1h;
```
3. Check symbol coverage:
```sql
SELECT count(DISTINCT symbol) FROM open_interest;
SELECT count(DISTINCT symbol) FROM derivatives_features_1h;
```
4. Check feature completeness:
```sql
SELECT avg(data_completeness), min(data_completeness), max(data_completeness)
FROM derivatives_features_1h
WHERE bar_start_ts > NOW() - INTERVAL '24 hours';
```
5. Spot-check a single symbol:
```sql
SELECT * FROM derivatives_features_1h
WHERE symbol = 'BTCUSDT'
ORDER BY bar_start_ts DESC
LIMIT 5;
```

Report all results.

## Step 5 — Start Continuous Collection

Start the service:
```bash
docker compose up -d derivatives_collector
docker compose logs -f derivatives_collector
```

Watch the first collection cycle complete. Verify new rows are appearing in the raw tables and features are being materialized.

## Non-Goals (Don't Do These)

- Don't modify signal_genesis_ignition.py yet. That's a separate task.
- Don't add Coinglass, DefiLlama, or any paid data sources. This is Phase 1 (Binance Futures only).
- Don't add new Python packages to requirements.txt unless something is genuinely impossible without them. Everything needed (requests, psycopg, numpy) is already there.
- Don't build a REST API for this data. It's consumed by research scripts via direct DB queries.
- Don't over-engineer scheduling. A simple sleep loop with time tracking is fine — no need for APScheduler or celery.

## Quality Bar

The reference implementation is `backfill_ohlcv_multi.py`. Your code should be at the same quality level: clear structure, good logging, proper error handling, resume support, batched DB operations. Read it carefully and match its patterns.
