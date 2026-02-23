# CODEX_MANIFEST.md -- Infrastructure Build Plan for Equities Side

> Generated: 2026-02-23 by Opus 4.6
> Purpose: Well-defined infrastructure tasks for OpenAI Codex to build, enabling Opus 4.6 to focus on research/strategy.
> Source: Full codebase audit of crypto vs equities infrastructure gaps.

---

## Current State Summary

### Crypto Side (Mature)
- **Ingestor**: WebSocket real-time + REST seeding for 1024 symbols, 6 timeframes (1m-1d), ring buffers in Redis
- **Indicator Worker** (x2): Stream-driven, computes ~40 features per symbol/timeframe via FeatureFactory
- **Signal Worker** (x2): Stream-driven, runs 9 setup evaluators (S1-S9) + U1, stores signals in Redis ZSETs + Postgres
- **Paper Trading**: Full position management with risk controls, exit monitoring at 1s interval
- **Opportunity Simulator**: Walk-forward simulation of expired opportunities with exit logic
- **OHLCV Persister**: Continuous Redis-to-Postgres persistence
- **Social Collector**: Telegram channel monitoring
- **Derivatives Collector**: Funding rates, OI from Binance Futures
- **On-chain Collector**: Dune API integration
- **API**: 40+ endpoints covering signals, opportunities, runs, paper trading, analytics, champion/challenger, unicorns, telemetry
- **Frontend**: React 18 + Vite + Tailwind SPA
- **Docker Compose**: 15 services with proper isolation

### Equities Side (Partially Built)
- **OHLCV Ingestor**: Script-based (not a service), Yahoo + Stooq, 1h + 1d
- **Events Collector**: SEC EDGAR Atom + Google News RSS
- **Feature Builder**: Price returns, vol, event windows, regime labels, session state
- **Forward Predictor**: Ridge regression + logistic, walk-forward validation, DSR, daily PF
- **Outcome Settler**: Computes realized returns, Brier loss, log loss
- **Policy Optimizer**: Grid search on confidence/edge thresholds
- **Calibration Watchdog**: Confidence-bucket monotonicity checks
- **Alert Bridge**: Bridges calibration alerts to task board
- **Pipeline Runner**: Orchestrates full cycle (OHLCV -> events -> features -> predict -> settle -> optimize -> calibrate)
- **Raw Mentions Backfill**: SEC submissions API + GDELT + Reddit PullPush
- **API**: 7 endpoints under /v1/equities/
- **DB**: 12 equities-specific tables with append-only guards
- **Tests**: 5 test files with ~25 test functions

**What's NOT built**: No continuous Docker service, no real-time streaming, no Redis caching layer, no indicator worker equivalent, no paper trading equivalent, no frontend pages, incomplete API surface, no cron/scheduler, sparse monitoring.

---

## Task Categories

### A. Docker Compose Service for Equities Pipeline

**A1. Equities Pipeline Service** [Size: M]
- **What**: Add a new Docker Compose service `equities_pipeline` that runs `equities_pipeline_runner.py --loop --interval-seconds 600` as a continuous daemon
- **Files to create/modify**:
  - `/home/darren/Desktop/MCAS/Backend/docker-compose.yml` -- add service definition
- **Details**:
  - Build context: `./worker` (same Dockerfile as other worker services)
  - Command: `["python", "-u", "/scripts/equities_pipeline_runner.py", "--loop", "--interval-seconds", "600"]`
  - Volumes: `./scripts:/scripts`
  - Environment: `SERVICE_NAME: equities_pipeline`
  - env_file: `.env`
  - network: `backend`
  - restart: `unless-stopped`
  - Resource limits: `cpus: '2.0'`, `memory: 2G` (it runs sequentially so 2 cores is fine)
- **Dependencies**: None (pipeline_runner.py already exists)

**A2. Equities Queue Worker Service** [Size: M]
- **What**: Add a Docker Compose service `equities_task_worker` that continuously claims and executes task board EXEC tasks
- **Files to create/modify**:
  - `/home/darren/Desktop/MCAS/Backend/docker-compose.yml` -- add service definition
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_task_worker_loop.py` -- new file: loop wrapper around equities_task_executor.py
- **Details**:
  - The loop script should: (1) claim an OPEN EXEC task with `FOR UPDATE SKIP LOCKED`, (2) set status to IN_PROGRESS, (3) invoke the handler from equities_task_executor, (4) set status to DONE, (5) sleep if no tasks, (6) handle SIGTERM gracefully
  - Claim query filters: `task_type='EXEC' AND tags->>'program' = 'equities_forward_predictor' AND status='OPEN' AND needs_scoping=FALSE`
  - Sleep interval: 15 seconds when queue is empty
  - Use the existing `equities_task_executor._select_handler` for dispatch
- **Dependencies**: A1

---

### B. Missing Data Pipelines

**B1. Hourly OHLCV Backfill for All 62 Symbols** [Size: S]
- **What**: Create a one-shot script to backfill 2 years of hourly OHLCV for all 62 symbols (currently only 7 have hourly data)
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_hourly_backfill.py`
- **Details**:
  - Wraps `equities_ohlcv_ingestor.ingest()` with `timeframes=["1h"]`, `lookback_days=730`, parallelized across 10 shards
  - Uses multiprocessing.Pool(10) with one shard per worker
  - Progress logging: every 5 symbols, print elapsed + ETA
  - Writes to DB in batches (already handled by upsert_ohlcv)
  - Must include `polite_sleep(0.3)` between symbols to respect Yahoo rate limits
  - Run instructions: `tmux new -s hourly_backfill && docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/equities_hourly_backfill.py`
- **Dependencies**: None

**B2. Indicator Computation Layer for Equities** [Size: L]
- **What**: Create an equities-specific indicator computation module that computes technical indicators on equities OHLCV data, analogous to `Backend/worker/app/indicators.py` but for equities bar structure (session-aware, gaps, overnight returns)
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_predictor/indicators.py`
- **Details**:
  - Compute from OHLCV bars loaded via `equities_predictor.db.load_ohlcv()`
  - Features to compute (per symbol, per timeframe):
    - SMA(20), SMA(50), SMA(200)
    - EMA(9), EMA(21)
    - RSI(14)
    - ATR(14)
    - Bollinger Bands (20, 2.0)
    - VWAP (intraday only, for 1h timeframe)
    - Rate of change (ROC) 5, 10, 20 periods
    - Volume ratio (current / SMA(20))
    - Dollar volume
    - Realized volatility (20-day)
    - Overnight gap (already in bar_meta)
    - Sector-relative return (requires SPY as benchmark)
  - Output: dict[str, float] keyed by indicator name
  - All computations must use numpy vectorized operations (no Python loops over bars)
  - Must handle NaN gracefully for warmup periods
  - The existing `Backend/worker/app/indicators.py` FeatureFactory is the reference implementation -- follow its numpy-only pattern but DO NOT import from it (different execution context)
- **Dependencies**: None
- **DO NOT**: Invent trading signals or strategies. Just compute raw indicator values.

**B3. Equities Feature Event Builder V2 (with technical indicators)** [Size: M]
- **What**: Extend `equities_feature_builder.py` to include the technical indicators from B2 in the feature event rows
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_feature_builder.py` -- integrate indicators from B2
- **Details**:
  - Import from `equities_predictor.indicators`
  - Add indicator values to the `features` JSONB column alongside the existing event-window features
  - Update `FEATURE_COLUMNS` in `equities_forward_predictor.py` to include new indicators
  - Maintain backward compatibility: existing feature columns must not change names or computation
  - Warmup: require 200 bars minimum before computing indicators (skip earlier bars)
- **Dependencies**: B2

---

### C. Missing API Endpoints

**C1. Equities Universe Endpoint** [Size: S]
- **What**: Add endpoint to list all equities symbols with metadata
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py`
- **Details**:
  - `GET /equities/universe` -- returns all symbols from `equities_symbol_registry` with `active=TRUE`
  - Response fields: symbol, name, sector, is_etf, cik, active, created_at
  - Filter params: `sector` (optional), `is_etf` (optional bool)
  - Use envelope() wrapper
  - Sort by symbol ASC

**C2. Equities Events Endpoint** [Size: S]
- **What**: Add endpoint to list recent equities events
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py`
- **Details**:
  - `GET /equities/events` -- returns recent events from `equities_events`
  - Filter params: `symbol` (optional), `source` (optional), `event_type` (optional), `lookback_days` (default 7), `limit` (default 100, max 1000)
  - Response fields: id, symbol, source, publisher, title, url, published_ts, available_at_ts, event_type, session_relation, sentiment_score, anti_bot_score
  - Sort by published_ts DESC
  - Use envelope() wrapper

**C3. Equities Predictions Endpoint** [Size: S]
- **What**: Add endpoint to list recent predictions
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py`
- **Details**:
  - `GET /equities/predictions` -- returns recent predictions from `equities_predictions`
  - Filter params: `symbol` (optional), `horizon` (optional), `timeframe` (optional), `min_confidence` (default 0.0), `limit` (default 100, max 1000)
  - Response fields: id, model_name, model_version, symbol, timeframe, horizon, bar_start_ts, available_at_ts, predicted_return, predicted_prob_up, confidence, gap_risk_score, regime_label, created_at
  - Sort by created_at DESC
  - Use envelope() wrapper

**C4. Equities Model Evaluations Endpoint** [Size: S]
- **What**: Add endpoint to view model evaluation metrics
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py`
- **Details**:
  - `GET /equities/model-evaluations` -- returns from `equities_model_evaluations`
  - Filter params: `model_name` (optional), `timeframe` (optional), `split_type` (optional), `limit` (default 50, max 500)
  - Response fields: id, model_name, model_version, timeframe, horizon, split_type, metrics (JSONB), notes, created_at
  - Sort by created_at DESC
  - Use envelope() wrapper

**C5. Equities Outcomes Endpoint** [Size: S]
- **What**: Add endpoint to view settled opportunity outcomes
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py`
- **Details**:
  - `GET /equities/outcomes` -- returns from `equities_opportunity_outcomes`
  - Filter params: `symbol` (optional), `horizon` (optional), `timeframe` (optional), `limit` (default 200, max 2000)
  - Response fields: id, opportunity_id, symbol, timeframe, horizon, direction, regime_label, available_at_ts, settle_bar_ts, expected_return, predicted_prob_up, confidence, edge_score, realized_return, directional_realized_return, hit, brier_loss, log_loss, created_at
  - Sort by created_at DESC
  - Use envelope() wrapper

**C6. Equities OHLCV Data Endpoint** [Size: S]
- **What**: Add endpoint to retrieve OHLCV bars for equities symbols
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py`
- **Details**:
  - `GET /equities/ohlcv` -- returns OHLCV bars from `ohlcv` table filtered to equities sources
  - Required params: `symbol`, `timeframe` (1h or 1d)
  - Optional params: `lookback_days` (default 90), `limit` (default 500, max 2000)
  - Filter: `source IN ('equities_yahoo', 'equities_stooq')`, `is_final = TRUE`
  - Response fields: symbol, timeframe, bar_start_ts, open, high, low, close, volume
  - Sort by bar_start_ts ASC (chronological)
  - Use the same dedup pattern as `equities_predictor.db.load_ohlcv()` (ROW_NUMBER over as_of_ts DESC)
  - Use envelope() wrapper

**C7. Equities Calibration Alerts Endpoint** [Size: S]
- **What**: Add endpoint to view calibration alerts separately (the existing policy dashboard includes them but a standalone endpoint is useful for monitoring)
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py`
- **Details**:
  - `GET /equities/calibration-alerts` -- returns from `equities_calibration_alerts`
  - Filter params: `severity` (optional), `timeframe` (optional), `horizon` (optional), `lookback_days` (default 30), `limit` (default 100, max 500)
  - Response fields: id, created_at, actor, timeframe, horizon, severity, alert_type, message, data
  - Sort by created_at DESC
  - Use envelope() wrapper

- **Dependencies for all C tasks**: None (all use existing DB tables)

---

### D. Test Coverage

**D1. Test equities_predictor.calendar edge cases** [Size: S]
- **What**: Expand calendar test coverage for holidays, DST transitions, half-days
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/tests/test_equities_calendar.py`
- **Details**:
  - Test `nyse_holidays()` returns correct dates for 2024, 2025, 2026
  - Test `is_trading_day()` returns False for all NYSE holidays + weekends
  - Test `session_state()` at DST boundaries (March/November) for correct RTH/ETH classification
  - Test `session_relation()` for events published at exactly RTH open (09:30 ET) and close (16:00 ET)
  - Test `next_open_ts()` across a holiday weekend (e.g., Thanksgiving Thu -> Mon)
  - Test `last_close_ts()` from pre-open on Monday returns previous Friday's close
  - Test `open_gap_pct()` with zero prev_close returns None

**D2. Test equities_predictor.text_utils** [Size: S]
- **What**: Add unit tests for text utilities
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/tests/test_equities_text_utils.py`
- **Details**:
  - Test `normalize_text()` strips whitespace, lowercases
  - Test `content_hash()` is deterministic and order-sensitive
  - Test `tokenize()` extracts words >= 2 chars, ignores punctuation
  - Test `simple_sentiment_score()` positive for "beat earnings", negative for "losses drop", zero for neutral
  - Test `simhash_64()` produces same hash for same input, similar hash for similar text
  - Test `hamming_distance()` is 0 for identical, >0 for different
  - Test `anti_bot_score()` penalizes ALL CAPS, multiple $TICKER mentions, "!!!" in text, Telegram publisher

**D3. Test equities_predictor.sources** [Size: S]
- **What**: Add unit tests for data source parsing (mock HTTP responses)
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/tests/test_equities_sources.py`
- **Details**:
  - Mock `requests.get` responses
  - Test `fetch_yahoo_chart()` parses valid Yahoo JSON into correct bar dicts
  - Test `fetch_yahoo_chart()` returns empty list for malformed JSON
  - Test `fetch_stooq_daily()` parses CSV with correct column headers
  - Test `fetch_stooq_daily()` returns empty for bad CSV
  - Test `fetch_sec_filings_atom()` parses Atom XML into filing dicts with form_type
  - Test `fetch_google_news_rss()` parses RSS XML into event dicts with published_ts
  - Test `_request_text()` uses cache on second call (mock get_cache/set_cache)
  - Test `polite_sleep()` sleeps >= 0 seconds

**D4. Test equities_predictor.db integration** [Size: M]
- **What**: Add integration-style tests for DB functions using a mock or test database
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/tests/test_equities_db.py`
- **Details**:
  - Use `pytest.importorskip("psycopg")` for CI safety
  - Test `upsert_ohlcv()` handles ON CONFLICT correctly (update, not duplicate)
  - Test `insert_events()` deduplicates on (source, source_event_id)
  - Test `insert_feature_events()` idempotent (ON CONFLICT DO NOTHING)
  - Test `insert_predictions()` idempotent
  - Test `load_ohlcv()` deduplicates by ROW_NUMBER with correct priority
  - Test `list_settleable_opportunities()` excludes already-settled opportunities
  - Test `mark_opportunities_settled()` updates status correctly
  - Use a conftest fixture that creates test tables and rolls back after each test
  - Skip if DATABASE_URL not set (for CI without a running Postgres)

**D5. Test equities_predictor.ids** [Size: S]
- **What**: Add tests for deterministic ID generation
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/tests/test_equities_ids.py`
- **Details**:
  - Test `deterministic_uuid()` is deterministic: same inputs -> same UUID
  - Test `deterministic_uuid()` is different for different inputs
  - Test `stable_hash()` is deterministic and respects length parameter
  - Test `stable_key()` joins parts with "|"

**D6. Test equities API endpoints** [Size: M]
- **What**: Expand API contract tests for new endpoints (from C1-C7) and add response structure validation
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/tests/test_equities_api_contract.py`
- **Details**:
  - Add source-code checks for each new endpoint route
  - Verify each new endpoint function has `envelope()` call
  - Verify each new endpoint has proper Query parameter annotations with defaults and bounds
  - Check that SQL queries use parameterized queries (no f-strings for user input)
- **Dependencies**: C1-C7

**D7. Test equities_pipeline_runner orchestration** [Size: S]
- **What**: Test that pipeline_runner calls all stages in correct order
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/tests/test_equities_pipeline_runner.py`
- **Details**:
  - Mock all stage functions (ingest_ohlcv, collect_events, build_features, run_predictor, run_settlement, run_optimizer, run_watchdog, run_bridge)
  - Verify `run_cycle()` calls them in correct order
  - Verify each stage receives correct arguments
  - Verify summary dict has all expected keys

---

### E. Monitoring & Observability

**E1. Equities Pipeline Health Check Script** [Size: S]
- **What**: Create a health check script that reports on equities pipeline freshness, similar to how `cpu_query.py` reports CPU stats
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_health_check.py`
- **Details**:
  - Query and report:
    - Latest OHLCV bar_start_ts per timeframe (freshness)
    - Latest event published_ts (freshness)
    - Latest feature_event created_at (freshness)
    - Latest prediction created_at (freshness)
    - Latest pipeline_event ts (freshness)
    - Count of active vs settled opportunities
    - Count of HIGH/MEDIUM calibration alerts in last 7 days
    - Count of pipeline errors in last 24h
  - Output: JSON to stdout with `status: OK | STALE | ERROR` top-level field
  - STALE if any component is >2 hours old (1h pipeline) or >26 hours (1d pipeline)
  - ERROR if any pipeline_event with "ERROR" in event_type in last 1 hour
  - Can be run: `docker exec backend-worker-1 python3 /scripts/equities_health_check.py`

**E2. Equities Pipeline Metrics Endpoint** [Size: S]
- **What**: Add a lightweight metrics endpoint for Prometheus-style scraping
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py`
- **Details**:
  - `GET /equities/metrics` -- returns key counters/gauges
  - Metrics:
    - `equities_ohlcv_latest_bar_age_seconds{timeframe="1h"}` / `{timeframe="1d"}`
    - `equities_events_total{source="SEC_EDGAR"}` / `{source="OPEN_NEWS"}`
    - `equities_predictions_total{horizon="1d"}` etc
    - `equities_opportunities_active_count`
    - `equities_opportunities_settled_count`
    - `equities_calibration_alerts_high_7d`
    - `equities_pipeline_errors_24h`
  - Return as JSON (not Prometheus text format -- Codex should not design scraping infra)

---

### F. Configuration & Environment

**F1. Add Equities Environment Variables** [Size: S]
- **What**: Add equities-specific env vars to `.env.example` and document them
- **Files to modify**:
  - `/home/darren/Desktop/MCAS/Backend/.env.example` -- add new vars
- **Details**:
  - `EQUITIES_PIPELINE_ENABLED=true` -- toggle equities pipeline service
  - `EQUITIES_PIPELINE_INTERVAL_S=600` -- cycle interval (default 10 min)
  - `EQUITIES_OHLCV_LOOKBACK_DAYS=180` -- default OHLCV lookback
  - `EQUITIES_MODEL_VERSION=v1_freeopen` -- default model version string
  - `EQUITIES_MIN_CONFIDENCE=0.10` -- default minimum confidence for opportunity generation
  - `EQUITIES_UNIVERSE_SIZE=62` -- universe size
  - `EQUITIES_SYMBOLS=` -- override symbol list (comma-separated, empty = use default universe)

**F2. Equities Migration Runner Script** [Size: S]
- **What**: Create a script that applies all equities migration SQL files in order
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_migrate.py`
- **Details**:
  - Reads all files matching `Backend/docs/db/migrations/20260222_add_equities_*.sql`
  - Sorts by filename (alphabetical order ensures correct dependency)
  - For each file, check `schema_versions` table for the filename
  - If not found, execute the SQL
  - Uses advisory locks (already in migration files) to prevent concurrent runs
  - Log each migration applied
  - Run: `docker exec backend-postgres-1 psql -U trading -d trading < /path/to/migration.sql` or via psycopg

---

### G. Data Backfill Infrastructure

**G1. Sector Relative Data Pipeline** [Size: S]
- **What**: Create a script that computes and stores sector-relative returns for each symbol
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_sector_relative.py`
- **Details**:
  - For each symbol, compute: `symbol_return_N - sector_etf_return_N` for N in [1, 5, 20] days
  - Sector ETF mapping from `equities_predictor.universe` (XLK for Technology, XLF for Financials, etc.)
  - Index-relative: `symbol_return_N - SPY_return_N`
  - Store results in `equities_feature_events.features` JSONB alongside existing features
  - Fields to add: `sector_relative_1d`, `sector_relative_5d`, `sector_relative_20d`, `spy_relative_1d`, `spy_relative_5d`, `spy_relative_20d`
  - Sector mapping must handle all 62 symbols -- map sector names to ETF symbols:
    - Technology -> XLK, Financials -> XLF, Health Care -> XLV, Energy -> XLE
    - Consumer Discretionary -> XLY, Consumer Staples -> XLP, Industrials -> XLI
    - Utilities -> XLU, Index ETF -> SPY, Sector ETF -> SPY (self-relative to SPY)
- **Dependencies**: B1 (needs hourly OHLCV data for all symbols including sector ETFs)

**G2. Earnings Calendar Integration** [Size: M]
- **What**: Create a script that fetches and stores earnings dates for the equities universe
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_earnings_calendar.py`
  - `/home/darren/Desktop/MCAS/Backend/docs/db/migrations/20260223_add_equities_earnings_calendar.sql`
- **Details**:
  - New table `equities_earnings_calendar`:
    - `symbol TEXT NOT NULL`, `fiscal_quarter TEXT NOT NULL`, `report_date DATE`, `report_time TEXT` (BMO/AMC/TBD), `confirmed BOOLEAN DEFAULT FALSE`
    - PRIMARY KEY (symbol, fiscal_quarter)
  - Fetch from SEC EDGAR (10-Q/10-K filing dates as proxy) -- no paid API needed
  - Compute `days_to_earnings` feature: signed integer, negative = days since last, positive = days until next
  - Compute `earnings_week_flag`: 1 if within 5 trading days of earnings date
  - These become new features in `equities_feature_events.features` JSONB
- **Dependencies**: None

---

### H. Cron / Scheduler

**H1. Crontab Configuration for Equities Pipeline** [Size: S]
- **What**: Create crontab entries for periodic equities tasks that should run on schedule (not in the continuous pipeline loop)
- **Files to create**:
  - `/home/darren/Desktop/MCAS/Backend/scripts/equities_cron.sh`
- **Details**:
  - Daily at 03:00 UTC (before market open): Run OHLCV ingestor for daily bars
    ```
    0 3 * * 1-5 docker compose -f /srv/mcas/Backend/docker-compose.yml run --rm -T worker python3 /scripts/equities_ohlcv_ingestor.py --timeframes 1d --lookback-days 5 --actor CRON
    ```
  - Every 15 min during RTH (13:30-21:00 UTC, Mon-Fri): Run events collector
    ```
    */15 13-20 * * 1-5 docker compose -f /srv/mcas/Backend/docker-compose.yml run --rm -T worker python3 /scripts/equities_events_collector.py --max-sec-items 10 --max-news-items 5 --actor CRON
    ```
  - Daily at 22:00 UTC (after market close): Run outcome settlement + policy optimizer
    ```
    0 22 * * 1-5 docker compose -f /srv/mcas/Backend/docker-compose.yml run --rm -T worker python3 /scripts/equities_outcome_settler.py --actor CRON && docker compose -f /srv/mcas/Backend/docker-compose.yml run --rm -T worker python3 /scripts/equities_policy_optimizer.py --actor CRON
    ```
  - Weekly Sunday 06:00 UTC: Run calibration watchdog
    ```
    0 6 * * 0 docker compose -f /srv/mcas/Backend/docker-compose.yml run --rm -T worker python3 /scripts/equities_calibration_watchdog.py --actor CRON
    ```
  - The shell script should be `chmod +x` and contain comments explaining each entry
  - Include instructions for installing: `crontab /srv/mcas/Backend/scripts/equities_cron.sh`

---

## Dependency Graph

```
Phase 1 (No dependencies - can run in parallel):
  A1, B1, B2, C1, C2, C3, C4, C5, C6, C7, D1, D2, D3, D5, D7, E1, F1, F2, H1

Phase 2 (Depends on Phase 1):
  A2 (depends on A1)
  B3 (depends on B2)
  D4 (depends on F2 for test DB)
  D6 (depends on C1-C7)
  E2 (depends on C1-C7 for pattern)
  G1 (depends on B1)

Phase 3 (Depends on Phase 2):
  G2 (no strict dependency but benefits from B3 patterns)
```

---

## DO NOT List (Leave for Opus 4.6)

These tasks require research judgment, strategy design, or high-dimensional analysis that Codex should NOT attempt:

1. **DO NOT** design or modify trading signals, setups, or strategies
2. **DO NOT** choose model architectures, feature selection, or hyperparameters for the forward predictor
3. **DO NOT** modify the walk-forward validation logic, DSR computation, or daily PF computation
4. **DO NOT** design new regime labels, risk modes, or selection policies
5. **DO NOT** create any frontend components or pages (the equities frontend design requires UX decisions)
6. **DO NOT** modify the opportunity edge score formula or quality factor computations
7. **DO NOT** add new data sources beyond what's already defined (SEC, GDELT, Reddit, Yahoo, Stooq)
8. **DO NOT** create research scripts or discovery notebooks
9. **DO NOT** modify the anti-bot scoring heuristics or sentiment scoring logic
10. **DO NOT** design the equities-equivalent of the crypto paper trading system (requires careful position management design specific to equities market microstructure)
11. **DO NOT** implement WebSocket real-time streaming for equities (equities operates on much slower timeframes; the batch pipeline is appropriate)
12. **DO NOT** modify any existing crypto-side code -- equities infrastructure should be additive only
13. **DO NOT** make parameter choices that affect signal quality (thresholds, weights, confidence floors) -- expose them as configuration, do not hardcode values

---

## File Index

### Existing equities files (read-only context for Codex):

| File | Purpose |
|------|---------|
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_ohlcv_ingestor.py` | OHLCV ingestion from Yahoo/Stooq |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_events_collector.py` | SEC + Google News RSS events |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_feature_builder.py` | Feature event construction |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_forward_predictor.py` | Model training + prediction + opportunity generation |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_outcome_settler.py` | Realized outcome computation |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_policy_optimizer.py` | Selection policy optimization |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_calibration_watchdog.py` | Confidence monotonicity checks |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_alert_bridge.py` | Alert-to-task-board bridge |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_pipeline_runner.py` | Full pipeline orchestrator |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_backfill_enqueue.py` | Shard backfill task creation |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_queue_refiller.py` | Task queue maintenance |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_task_executor.py` | Task board work execution |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_raw_backfill_enqueue.py` | Raw mentions backfill enqueueing |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_raw_mentions_backfill.py` | SEC/GDELT/Reddit raw backfill |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_predictor/__init__.py` | Package init |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_predictor/db.py` | Database access layer |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_predictor/universe.py` | 62-symbol universe definition |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_predictor/calendar.py` | NYSE session calendar |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_predictor/sources.py` | HTTP data source clients |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_predictor/text_utils.py` | Text processing utilities |
| `/home/darren/Desktop/MCAS/Backend/scripts/equities_predictor/ids.py` | Deterministic UUID/hash helpers |
| `/home/darren/Desktop/MCAS/Backend/api/app/equities_api.py` | Equities API endpoints |
| `/home/darren/Desktop/MCAS/Backend/api/app/main.py` | Main FastAPI app (mounts equities router) |
| `/home/darren/Desktop/MCAS/Backend/docs/db/migrations/20260222_add_equities_predictor_tables.sql` | Core equities tables |
| `/home/darren/Desktop/MCAS/Backend/docs/db/migrations/20260222_add_equities_outcomes.sql` | Outcomes table |
| `/home/darren/Desktop/MCAS/Backend/docs/db/migrations/20260222_add_equities_policy_tables.sql` | Policy + alerts tables |
| `/home/darren/Desktop/MCAS/Backend/docs/db/migrations/20260222_add_equities_raw_mentions.sql` | Raw mentions table |
| `/home/darren/Desktop/MCAS/Backend/tests/test_equities_api_contract.py` | API contract tests |
| `/home/darren/Desktop/MCAS/Backend/tests/test_equities_forward_predictor.py` | Predictor logic tests |
| `/home/darren/Desktop/MCAS/Backend/tests/test_equities_outcome_settler.py` | Outcome settler tests |
| `/home/darren/Desktop/MCAS/Backend/tests/test_equities_policy_feedback.py` | Policy optimizer tests |
| `/home/darren/Desktop/MCAS/Backend/tests/test_equities_raw_backfill.py` | Raw backfill tests |

### Crypto-side reference files (pattern reference for Codex):

| File | Purpose |
|------|---------|
| `/home/darren/Desktop/MCAS/Backend/worker/app/ingestor.py` | Crypto WS+REST ingestor (reference for service patterns) |
| `/home/darren/Desktop/MCAS/Backend/worker/app/indicator_worker.py` | Crypto indicator computation (reference for B2) |
| `/home/darren/Desktop/MCAS/Backend/worker/app/signal_worker.py` | Crypto signal evaluation (reference for service patterns) |
| `/home/darren/Desktop/MCAS/Backend/worker/app/indicators.py` | Crypto FeatureFactory (reference for B2 numpy patterns) |
| `/home/darren/Desktop/MCAS/Backend/worker/app/paper_trading.py` | Crypto paper trading (DO NOT replicate for equities) |
| `/home/darren/Desktop/MCAS/Backend/docker-compose.yml` | Service definitions (modify for A1/A2) |

---

## Task Summary

| Task | Category | Size | New Files | Modified Files |
|------|----------|------|-----------|----------------|
| A1 | Docker Service | M | 0 | 1 (docker-compose.yml) |
| A2 | Docker Service | M | 1 | 1 (docker-compose.yml) |
| B1 | Data Pipeline | S | 1 | 0 |
| B2 | Data Pipeline | L | 1 | 0 |
| B3 | Data Pipeline | M | 0 | 2 |
| C1 | API Endpoint | S | 0 | 1 |
| C2 | API Endpoint | S | 0 | 1 |
| C3 | API Endpoint | S | 0 | 1 |
| C4 | API Endpoint | S | 0 | 1 |
| C5 | API Endpoint | S | 0 | 1 |
| C6 | API Endpoint | S | 0 | 1 |
| C7 | API Endpoint | S | 0 | 1 |
| D1 | Tests | S | 1 | 0 |
| D2 | Tests | S | 1 | 0 |
| D3 | Tests | S | 1 | 0 |
| D4 | Tests | M | 1 | 0 |
| D5 | Tests | S | 1 | 0 |
| D6 | Tests | M | 0 | 1 |
| D7 | Tests | S | 1 | 0 |
| E1 | Monitoring | S | 1 | 0 |
| E2 | Monitoring | S | 0 | 1 |
| F1 | Configuration | S | 0 | 1 |
| F2 | Migration | S | 1 | 0 |
| G1 | Data Pipeline | S | 1 | 0 |
| G2 | Data Pipeline | M | 2 | 0 |
| H1 | Scheduling | S | 1 | 0 |

**Totals**: 26 tasks (16 Small, 8 Medium, 2 Large), 15 new files, 14 modified files
