# Backend Changes Audit: MCAS_sentiment vs Main MCAS

**Auditor:** Claude Opus 4.6
**Date:** 2026-02-22
**Repos compared:**
- Main: `/home/darren/Desktop/MCAS/Backend/`
- Sentiment: `/home/darren/Desktop/MCAS/MCAS_sentiment/Backend/`

---

## Executive Summary

The sentiment repo (built by Codex/GPT-5.3) is a **superset** of the main MCAS repo. All existing MCAS backend code is byte-identical -- no modifications were made to signal_engine.py, signal_worker.py, indicators.py, setups, worker internals, or API routes. The changes are strictly additive: 5 new docker-compose services, 1 new API router, 11 new DB migrations, 36 new scripts, 6 new test files, and 1 minor conftest.py change.

Three distinct feature pillars were added:
1. **Vision22** -- Football (soccer) story predictor pipeline
2. **Equities Forward Predictor** -- Stock-market event-driven prediction system
3. **Tone Engine** -- Social media tone/phase-state analysis for crypto

None of these touch the existing crypto signal pipeline.

---

## 1. Docker Compose Diff

### Identical services (no changes)
All 16 existing services are byte-identical between repos:
- caddy, api, worker, postgres, redis
- ingestor, indicator_worker_0, indicator_worker_1
- signal_worker_0, signal_worker_1
- unicorn_pipeline, ohlcv_persister
- social_collector, derivatives_collector, onchain_collector
- cpu_collector

### New services (sentiment repo only)

| Service | Image | Entry Point | Purpose |
|---------|-------|-------------|---------|
| `vision22_story_collector_0` | worker | `/scripts/vision22_story_collector.py --shard 0 --shards 5` | Football story ingestion + predictor shard 0 |
| `vision22_story_collector_1` | worker | `/scripts/vision22_story_collector.py --shard 1 --shards 5` | Shard 1 |
| `vision22_story_collector_2` | worker | `/scripts/vision22_story_collector.py --shard 2 --shards 5` | Shard 2 |
| `vision22_story_collector_3` | worker | `/scripts/vision22_story_collector.py --shard 3 --shards 5` | Shard 3 |
| `vision22_story_collector_4` | worker | `/scripts/vision22_story_collector.py --shard 4 --shards 5` | Shard 4 |

All 5 vision22 collectors share identical CLI arguments:
```
--loop --interval-seconds 30 --lookback-days 1095 --prediction-lookback-days 1095
--window-days 10 --future-days 14 --relevance-threshold 0.35
--google-match-history-days 1095 --google-match-window-days 14
--google-match-max-items 20 --google-match-max-windows 15
--google-match-max-errors 20 --google-match-only-missing-windows
--min-corr-samples 20 --strong-r-threshold 0.08 --min-feature-nonzero 25
```

Key observations:
- 30-second cycle interval is aggressive for RSS scraping
- 1095-day lookback (3 years) via Google News RSS only -- GDELT disabled (0 months)
- All 5 reuse the `worker` Docker image (no separate Dockerfile)
- Scripts mounted via `./scripts:/scripts` volume (same as existing worker)

### Volumes and networks
Identical between repos. No new volumes or networks added.

---

## 2. Worker Changes

### Core worker pipeline: ZERO changes

| File | Status |
|------|--------|
| `worker/app/signal_engine.py` | **IDENTICAL** (1284 lines) |
| `worker/app/signal_worker.py` | **IDENTICAL** (1236 lines) |
| `worker/app/worker.py` | **IDENTICAL** |
| `worker/app/ingestor.py` | **IDENTICAL** |
| `worker/app/indicator_worker.py` | **IDENTICAL** |
| `worker/app/indicators.py` | **IDENTICAL** |
| `worker/app/unicorn_pipeline.py` | **IDENTICAL** |
| `worker/app/setups/*` | **IDENTICAL** |
| `worker/app/setup_base.py` | **IDENTICAL** |
| `worker/app/paper_trading.py` | **IDENTICAL** |
| `worker/app/price_feed.py` | **IDENTICAL** |
| `worker/app/execution.py` | **IDENTICAL** |
| `worker/app/data_layer.py` | **IDENTICAL** |
| `worker/app/runs/*` | **IDENTICAL** |
| `worker/requirements.txt` | **IDENTICAL** |
| `worker/Dockerfile` | **IDENTICAL** |

The entire `worker/app/` directory is byte-for-byte identical. No modifications whatsoever.

---

## 3. API Changes

### Changed files

**`api/app/main.py`** -- 3 lines added at the end:
```python
# Mount Equities Forward Predictor API (also protected)
from equities_api import router as equities_router
app.include_router(equities_router, prefix="/v1", dependencies=[Depends(require_api_key)])
```

All existing routes (control_api, runs_api, trading_api, /health) are unchanged.

### New files

**`api/app/equities_api.py`** (621 lines) -- Complete REST API for equities forward predictor:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/equities/opportunities` | GET | List equities opportunities with filters (symbol, horizon, status, confidence, edge_score) |
| `/v1/equities/opportunities/{opp_id}` | GET | Get single opportunity detail |
| `/v1/equities/opportunities/{opp_id}/explain` | GET | Get feature contributions and diagnostics |
| `/v1/equities/dashboard/performance` | GET | Aggregated prediction performance (by symbol, horizon, regime, source coverage) |
| `/v1/equities/dashboard/realized` | GET | Realized returns dashboard (by horizon, symbol, confidence bucket) |
| `/v1/equities/dashboard/policy` | GET | Selection policies and calibration alerts |
| `/v1/equities/pipeline/health` | GET | Pipeline event log |

Architecture observations:
- Uses psycopg (not psycopg2) for direct DB connections -- matches main repo pattern
- Follows MCAS response envelope pattern: `{data, meta: {timestamp, version, compute_ms, as_of_ts}}`
- All endpoints are behind API key auth (mounted with `Depends(require_api_key)`)
- Creates new DB connection per request (no connection pool) -- same pattern as existing API
- Queries reference tables that would be created by the equities migration set

### Dependency changes
- `api/requirements.txt`: **IDENTICAL** -- no new dependencies needed

---

## 4. Social Collector

### Status: IDENTICAL between repos

The entire `social_collector/` directory is byte-for-byte identical. The social_collector service already exists in the main MCAS repo's docker-compose.yml (lines 171-184) and has been running in production.

Files confirmed identical:
- `Dockerfile`
- `requirements.txt`
- `app/config.py`
- `app/db.py`
- `app/collector.py`
- `app/symbol_matcher.py`
- `app/brain1.py`
- `app/brain2.py`
- `app/collectors/__init__.py`
- `app/collectors/reddit.py`
- `app/collectors/rss.py`
- `app/collectors/coingecko.py`
- `app/collectors/telegram.py`
- `app/ambiguous_tickers.json`
- `app/telegram_channels.json`
- `app/backfill_telegram.py`

The sentiment repo did not modify any social_collector code. It is the same code already deployed in production.

---

## 5. New Scripts

36 new scripts exist only in the sentiment repo (none modify existing scripts):

### Vision22 (Football)
| Script | Purpose |
|--------|---------|
| `vision22_story_collector.py` | 1814-line sharded RSS/GDELT/Google News collector for football stories. Runs 5-stage pipeline per cycle: ingest stories, extract signals, materialize predictors, compute correlations, build forward predictions. |
| `backfill_vision22.py` | Historical data backfill for vision22 schema |

### Equities Forward Predictor (16 scripts)
| Script | Purpose |
|--------|---------|
| `equities_forward_predictor.py` | Core prediction engine |
| `equities_feature_builder.py` | Feature engineering from events |
| `equities_events_collector.py` | Event/news ingestion |
| `equities_ohlcv_ingestor.py` | Stock OHLCV data ingestion |
| `equities_outcome_settler.py` | Settle predictions against realized prices |
| `equities_policy_optimizer.py` | Auto-tune selection policy thresholds |
| `equities_calibration_watchdog.py` | Monitor prediction calibration drift |
| `equities_alert_bridge.py` | Bridge calibration alerts |
| `equities_pipeline_runner.py` | Orchestrate full pipeline |
| `equities_backfill_enqueue.py` | Queue historical backfill tasks |
| `equities_raw_backfill_enqueue.py` | Queue raw mention backfill |
| `equities_raw_mentions_backfill.py` | Backfill raw mention data |
| `equities_queue_refiller.py` | Keep task queue populated |
| `equities_task_executor.py` | Execute task board tasks |
| `equities_predictor/` | Directory (sub-package) |

### Task Board (8 scripts)
| Script | Purpose |
|--------|---------|
| `task_board_lib.py` | Shared task board library |
| `task_board_cli.py` | CLI for task board operations |
| `task_board_bootstrap.py` | Initialize task board schema/state |
| `task_board_supervisor.py` | Supervisor process for task board |
| `task_board_worker.py` | Worker process for task execution |
| `task_board_s2_main.py` | S2-specific task board main |
| `task_board_s2_tick.py` | S2 periodic tick handler |
| `task_board_s3_main.py` | S3-specific task board main |
| `task_board_s2_autopilot.sh` | Shell script for S2 autopilot |
| `task_board_backfill_splitter.py` | Split backfill into chunks |
| `task_board_war_room.py` | Dashboard/monitoring |

### Tone Engine (5 scripts)
| Script | Purpose |
|--------|---------|
| `tone_engine_ingest_sources.py` | Ingest social + OHLCV data for tone analysis |
| `tone_engine_backfill_sharded.py` | Sharded historical backfill |
| `tone_engine_reddit_backfill_sharded.py` | Reddit-specific backfill |
| `tone_engine_expand_socials.py` | Expand social data coverage |
| `tone_engine_forward_predictor_discovery.py` | Discover forward-predictive tone features |

### Other
| Script | Purpose |
|--------|---------|
| `s1_autonomy_watchdog.py` | S1 session autonomy watchdog |
| `run_tone_backfill_4_agents.sh` | Shell script to run 4-agent tone backfill |
| `.runtime/` | Runtime directory |

---

## 6. Database Migrations

### Existing migrations: IDENTICAL
All 19 migrations present in the main repo exist unchanged in the sentiment repo:
- `20260203_000000_add_cc_tables.sql` through `20260219_add_dexscreener_dune_tables.sql`

### New migrations (sentiment repo only): 11 files

#### Vision22 Schema (2 migrations)

**`20260221_add_vision22_schema.sql`** -- Creates `vision22` schema with tables:
- `vision22.sources` -- data source registry
- `vision22.competitions` -- leagues/tournaments
- `vision22.teams` -- football teams
- `vision22.players` -- player registry
- `vision22.matches` -- match fixtures + results
- `vision22.event_types` -- event type definitions
- `vision22.events` -- granular match events (shots, passes, cards, etc.)
- `vision22.match_stats` -- aggregated team stats per match
- `vision22.match_derived` -- derived market indicators (BTTS, over/under, etc.)

**`20260221_add_vision22_story_predictors.sql`** -- Story pipeline tables:
- `vision22.story_posts` -- ingested news articles with signal_counts JSONB
- `vision22.story_predictors` -- materialized per-team/per-match predictor vectors (20 columns)
- `vision22.story_predictor_correlations` -- Pearson correlations between features and targets
- `vision22.story_outcome_predictions` -- forward outcome predictions

#### Equities Forward Predictor (4 migrations)

**`20260222_add_equities_predictor_tables.sql`** -- Core equities tables:
- `equities_symbol_registry` -- stock symbol registry
- `equities_source_cache` -- source data cache
- `equities_bar_meta` -- bar session metadata (RTH/ETH/CLOSED)
- `equities_events` -- news events with dedupe_hash + simhash
- `equities_feature_events` -- engineered features per bar
- `equities_predictions` -- model predictions
- `equities_opportunities` -- actionable opportunities
- `equities_model_evaluations` -- model evaluation history
- `equities_pipeline_events` -- pipeline audit log
- Append-only triggers on 5 tables

**`20260222_add_equities_outcomes.sql`** -- Realized outcome tracking:
- `equities_opportunity_outcomes` -- realized returns, hit rate, Brier/log loss
- Append-only trigger

**`20260222_add_equities_policy_tables.sql`** -- Selection policy tables:
- `equities_selection_policies` -- confidence/edge thresholds per timeframe/horizon
- `equities_calibration_alerts` -- drift alerts
- Append-only triggers on both

**`20260222_add_equities_raw_mentions.sql`** -- Raw mention ingestion:
- `equities_raw_mentions` -- source articles with session_relation, dedupe_hash, simhash
- Append-only trigger

#### Task Board (2 migrations)

**`20260222_add_task_board.sql`** -- Coordination layer:
- `task_board_tasks` -- tasks with status lifecycle (OPEN -> CLAIMED -> IN_PROGRESS -> DONE/FAILED/CANCELLED/STALE/BLOCKED)
- `task_board_events` -- append-only event log
- `task_board_system` -- singleton config row (bootstrap_state, agent_allocation, current_focus)

**`20260222_task_board_real_work_guard.sql`** -- Execution guard trigger:
- Auto-injects `command` payload for EXEC/BACKFILL tasks in the `equities_forward_predictor` program
- Falls back to blocking tasks with `needs_scoping = TRUE` if no command payload provided

#### Tone Engine (3 migrations)

**`20260222_tone_phase_state_engine_core.sql`** -- Tone analysis core:
- `agent_notes` -- agent observation log
- `agent_artifacts` -- agent output artifacts
- `social_tone_posts` -- posts with tone_vec and stance_vec arrays
- `social_tone_windows` -- windowed tone aggregates (mean, cov_diag, novelty_score)
- `social_tone_cluster_models` -- K-means cluster models
- `social_tone_cluster_assignments` -- cluster assignments per window
- `social_tone_states` -- HMM-style state transitions
- `ohlcv_regimes` -- price regime labels
- `tone_signal_metrics` -- signal backtesting metrics
- `tone_short_alerts` -- actionable alerts

**`20260222_tone_engine_source_mirror_tables.sql`** -- Source data mirrors:
- `social_posts` -- mirror of social collector posts
- `ohlcv` -- mirror of OHLCV data
- `tone_ingestion_state` -- cursor tracking for incremental ingestion

**`20260222_tone_engine_social_posts_id_default.sql`** -- Adds auto-increment default to `social_posts.id`

---

## 7. Configuration Changes

### .env.example
**IDENTICAL** between repos. No new environment variables added to the template.

This is notable because the vision22_story_collector CLI arguments are hardcoded in docker-compose.yml rather than being env-var-driven. The equities pipeline similarly relies on script-level defaults rather than .env configuration.

### .env
The sentiment repo contains a `.env` file (the main repo's `.env` is gitignored). Cannot audit secrets but its presence suggests the sentiment repo was a working deployment.

---

## 8. Test Changes

### Changed files
**`tests/conftest.py`** -- 3 lines added:
```python
# Add scripts to path for task board library/CLI modules.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
```

### New test files (6)
| File | Purpose |
|------|---------|
| `test_equities_forward_predictor.py` | Core predictor tests |
| `test_equities_api_contract.py` | API endpoint contract tests |
| `test_equities_outcome_settler.py` | Outcome settlement logic tests |
| `test_equities_policy_feedback.py` | Policy optimization feedback loop tests |
| `test_equities_raw_backfill.py` | Raw mention backfill tests |
| `test_task_board_acceptance.py` | Task board acceptance tests |

---

## 9. Dependency Changes

### All requirements.txt files: IDENTICAL
- `worker/requirements.txt` -- no changes
- `api/requirements.txt` -- no changes
- `social_collector/requirements.txt` -- no changes

The new scripts (equities, tone, vision22, task board) all run inside the `worker` container and rely on dependencies already present in the worker image (psycopg, numpy, requests).

---

## 10. Integration Architecture

### Vision22 Story Predictor Pipeline
```
RSS feeds (BBC, Guardian, Sky, Google News)
    |
    v
vision22_story_collector.py (5 shards, 30s cycle)
    |
    +-> Stage 1: Fetch RSS/Google News entries
    +-> Stage 2: Extract team IDs, filter soccer context, compute signal_counts
    +-> Stage 3: Upsert to vision22.story_posts
    +-> Stage 4: Materialize story_predictors per match/team (20 features)
    +-> Stage 5: Pearson correlation recompute (shard 0 only)
    +-> Stage 6: Forward outcome predictions via weighted feature scores
```
- Targets: team_win, team_scored, team_conceded, team_cards_high, team_corners_high, match_btts, match_over_2_5, match_corners_over_9_5, match_cards_over_3_5
- Signal extraction: regex-based mention counting for 12 signal types
- Prediction: weighted sum of correlated features, tanh normalization, confidence scaling
- Has fallback prior weights when no strong correlations found

### Equities Forward Predictor Pipeline
```
equities_events_collector.py --> equities_events table
equities_ohlcv_ingestor.py   --> equities_bar_meta table
equities_feature_builder.py  --> equities_feature_events table
equities_forward_predictor.py -> equities_predictions + equities_opportunities
equities_outcome_settler.py  --> equities_opportunity_outcomes
equities_policy_optimizer.py --> equities_selection_policies
equities_calibration_watchdog.py -> equities_calibration_alerts
    |
    v
equities_api.py (FastAPI endpoints)
```
- Orchestrated via task_board system (DB-backed task queue with lease-based claiming)
- Append-only tables with DB triggers prevent mutation
- Session-aware (RTH/ETH/CLOSED) bar metadata

### Tone Phase-State Engine
```
social_posts (from social_collector) + ohlcv
    |
    v
tone_engine_ingest_sources.py --> social_tone_posts (with tone_vec, stance_vec)
    |
    v
Windowed aggregation --> social_tone_windows
    |
    v
K-means clustering --> social_tone_cluster_models + assignments
    |
    v
HMM state inference --> social_tone_states
    |
    v
Signal metrics --> tone_signal_metrics + tone_short_alerts
```
- Consumes data from existing social_collector (no modifications needed)
- Adds regime detection layer on OHLCV data
- Forward predictor discovery via `tone_engine_forward_predictor_discovery.py`

### Cross-system integration
- **No coupling between Vision22, Equities, and Tone Engine** -- they are independent
- **No coupling with existing MCAS signal pipeline** -- signal_engine.py, signal_worker.py are untouched
- **Task Board** is the only shared coordination layer, currently only used by equities pipeline
- **social_collector data** is consumed by Tone Engine via mirror tables (not by direct reference)

---

## 11. KEEP / FIX / SKIP

### KEEP (merge into main repo)

1. **`social_collector/`** -- Already identical. Nothing to merge; it is already deployed in production.

2. **Tone Engine migrations + scripts** -- The tone phase-state engine builds directly on existing social_collector data. Schema design is clean (windowed aggregates, cluster models, state transitions). Forward predictor discovery is exactly aligned with MCAS core mission (finding forward predictors of returns).
   - `20260222_tone_phase_state_engine_core.sql`
   - `20260222_tone_engine_source_mirror_tables.sql`
   - `20260222_tone_engine_social_posts_id_default.sql`
   - `tone_engine_*.py` scripts (5 scripts)

3. **Vision22 schema migration** (`20260221_add_vision22_schema.sql`) -- Clean, well-indexed schema for football event data. Separate `vision22` schema avoids pollution of public schema. Worth keeping as foundation even if the story collector needs work.

### FIX (merge with modifications)

1. **Vision22 story collector** -- The collector itself (`vision22_story_collector.py`, 1814 lines) has several issues:
   - **30-second cycle interval** is too aggressive for free RSS/Google News endpoints. Will trigger rate limiting. Should be 300-600s minimum.
   - **Regex-based signal extraction** is crude -- pattern matching for "injury", "suspension", etc. yields false positives on non-football context despite the context filter.
   - **Prior weights are hardcoded** with no empirical basis documented. Weights like `injury_mentions: -0.30` for team_win are theory-engineered, violating CLAUDE.md Section 0.2 Law 1 ("Don't engineer from theory, reverse-engineer from data").
   - **tanh(raw_score / 3.0) normalization** is arbitrary. The divisor 3.0 has no empirical justification.
   - **Correlation recompute only on shard 0** -- creates single point of failure. If shard 0 is down, correlations and predictions never update.
   - **executemany for bulk upserts** -- should use psycopg's `execute_batch` or `COPY` for performance.
   - Fix: Keep schema + story_predictors tables, rewrite collector with proper rate limiting, replace prior weights with empirical sweep, add walk-forward validation before deploying predictions.

2. **Docker-compose vision22 services** -- 5 shards of the same RSS scraper is overkill for 6 RSS feeds. Reduce to 1-2 shards. The sharding logic distributes by `feed_index % shards` so 5 shards with 6 feeds means uneven distribution (shards 0,1 get 2 feeds each; shards 2,3,4 get 1 each).

3. **Equities API** (`equities_api.py`) -- The code follows MCAS patterns correctly but:
   - Creates a new DB connection per request (`psycopg.connect()` in every endpoint). Should use a connection pool.
   - Uses `%s IS NULL OR column = %s` pattern extensively, passing the same parameter twice. Works but wasteful.
   - The `equities_opportunities` table references `opportunities(id)` as a foreign key (`legacy_opportunity_id`), which assumes the main MCAS `opportunities` table exists. Verify before deploying.
   - Fix: Add connection pooling, verify FK reference, then merge.

4. **conftest.py path addition** -- The `sys.path.insert(0, ...)` for scripts is acceptable but should use a more robust path resolution.

### SKIP (do not merge)

1. **Task Board system** -- This is a general-purpose DB-backed task queue that duplicates Redis-based coordination already in MCAS. The existing MCAS architecture uses Redis pubsub + Postgres `FOR UPDATE SKIP LOCKED` for task claiming. Adding a parallel task queue creates two competing coordination systems.
   - `20260222_add_task_board.sql`
   - `20260222_task_board_real_work_guard.sql`
   - `task_board_*.py` scripts (11 scripts)
   - The real_work_guard trigger hardcodes equities-specific script paths, making it non-general.

2. **Equities predictor tables + pipeline** -- This is a stock market prediction system. MCAS is a crypto trading system. The equities pipeline:
   - Adds 12 new public-schema tables (`equities_*`) that have nothing to do with crypto
   - References stock-market concepts (RTH/ETH sessions, open gaps, overnight returns) irrelevant to 24/7 crypto markets
   - Has its own OHLCV ingestion, feature building, and prediction pipeline that duplicates MCAS architecture
   - Brings no reusable components back to crypto signal generation
   - Skip entirely unless the project scope explicitly expands to equities.
   - Affected files: 4 equities migrations, 16 equities scripts, equities_api.py, 5 equities test files.

3. **`.env` file** -- Contains production secrets. Must never be committed. The sentiment repo should have this gitignored.

4. **`.runtime/` directory** in scripts -- Runtime artifacts, not source code.

---

## Appendix: File-Level Diff Summary

### Files modified in sentiment repo (vs main)
| File | Change |
|------|--------|
| `api/app/main.py` | +3 lines: import and mount equities_api router |
| `tests/conftest.py` | +3 lines: sys.path.insert for scripts directory |
| `docker-compose.yml` | +75 lines: 5 vision22_story_collector services |

### Files added in sentiment repo (not in main)
| Category | Count | Key files |
|----------|-------|-----------|
| API | 1 | `equities_api.py` |
| Scripts | 36 | See Section 5 |
| Migrations | 11 | See Section 6 |
| Tests | 6 | See Section 8 |
| Config | 1 | `.env` (should be gitignored) |

### Files identical between repos
Everything not listed above -- all worker code, all social_collector code, all existing migrations, all existing API routes, all requirements.txt files, all Dockerfiles.
