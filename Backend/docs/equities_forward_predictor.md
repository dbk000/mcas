# Equities Forward Predictor Runbook

## Scope

This module adds an end-to-end equities forward predictor in the existing MCAS backend:

- Equities OHLCV ingest (`1h`, `1d`) with free/open fallback
- Session-aware market-state metadata (RTH/ETH/CLOSED)
- Event/information layer from free/open sources (SEC + open RSS)
- Time-aligned feature events with `available_at_ts` anti-leakage discipline
- Baseline predictors + opportunity generation + explainability
- Multi-split validation (`time_test`, `symbol_test`, `walk_forward`) with logged metrics
- Edge-scored opportunity gating (confidence + expected-return floor + model/coverage/regime quality)
- Realized-outcome settlement (hit-rate, directional return, calibration losses)
- Policy optimizer from settled outcomes (per-horizon thresholds)
- Calibration watchdog (confidence monotonicity checks)
- Alert-to-task bridge for automatic remediation task creation
- API dashboards and health endpoints

No paid feeds, API keys, or subscriptions are required.

## Data policy (free/open only)

Implemented tiers:

- Tier A: SEC EDGAR ticker map + Atom filings (no key)
- Tier B: Open web RSS (Google News RSS query) with source cache + rate limiting
- Tier C: Paid/locked sources are skipped by design. Limitations are emitted in pipeline events.

## New schema

Migrations:

- `Backend/docs/db/migrations/20260222_add_equities_predictor_tables.sql`
- `Backend/docs/db/migrations/20260222_add_equities_outcomes.sql`
- `Backend/docs/db/migrations/20260222_add_equities_policy_tables.sql`

Tables:

- `equities_symbol_registry`
- `equities_source_cache`
- `equities_bar_meta`
- `equities_events` (append-only)
- `equities_feature_events` (append-only)
- `equities_predictions` (append-only)
- `equities_opportunities`
- `equities_model_evaluations` (append-only)
- `equities_pipeline_events` (append-only)
- `equities_opportunity_outcomes` (append-only)
- `equities_selection_policies` (append-only)
- `equities_calibration_alerts` (append-only)

## API surface

Mounted under `/v1/equities`:

- `GET /v1/equities/opportunities`
  - supports `min_confidence` and `min_edge_score` filters
  - returns `edge_score`, `model_quality_factor`, `coverage_quality_factor`, `regime_quality_factor`
- `GET /v1/equities/opportunities/{id}`
- `GET /v1/equities/opportunities/{id}/explain`
- `GET /v1/equities/dashboard/performance`
  - now includes `by_regime` and `by_source_coverage`
- `GET /v1/equities/dashboard/realized`
- `GET /v1/equities/dashboard/policy`
- `GET /v1/equities/pipeline/health`

## Run commands

```bash
cd Backend
export PYTHONPATH=./scripts
export DATABASE_URL='postgresql://trading:...@localhost:5432/trading'
```

For host-side access to the backend postgres container (510GB-mounted):

```bash
export DATABASE_URL='postgresql://trading:mcas_local_dev_2026@172.18.0.4:5432/trading'
```

Apply migration:

```bash
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < docs/db/migrations/20260222_add_equities_predictor_tables.sql
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < docs/db/migrations/20260222_add_equities_outcomes.sql
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < docs/db/migrations/20260222_add_equities_policy_tables.sql
```

OHLCV ingest:

```bash
python3 scripts/equities_ohlcv_ingestor.py --symbols AAPL,MSFT,NVDA,SPY,QQQ --timeframes 1h,1d --lookback-days 120
```

Events collect:

```bash
python3 scripts/equities_events_collector.py --symbols AAPL,MSFT,NVDA,SPY,QQQ --max-sec-items 20 --max-news-items 10
```

Feature build:

```bash
python3 scripts/equities_feature_builder.py --symbols AAPL,MSFT,NVDA,SPY,QQQ --timeframe 1h --lookback-days 120
python3 scripts/equities_feature_builder.py --symbols AAPL,MSFT,NVDA,SPY,QQQ --timeframe 1d --lookback-days 120
```

Predictor + opportunities:

```bash
python3 scripts/equities_forward_predictor.py --symbols AAPL,MSFT,NVDA,SPY,QQQ --timeframe 1h --lookback-days 180 --model-version v1_freeopen
python3 scripts/equities_forward_predictor.py --symbols AAPL,MSFT,NVDA,SPY,QQQ --timeframe 1d --lookback-days 180 --model-version v1_freeopen
```

Settle matured opportunities into realized outcomes:

```bash
python3 scripts/equities_outcome_settler.py --actor S1_MAIN --limit 3000
```

Learn and persist per-horizon selection policy:

```bash
python3 scripts/equities_policy_optimizer.py --actor S1_MAIN --lookback-days 120 --min-samples 20 --transaction-cost-bps 6
```

Run confidence calibration watchdog:

```bash
python3 scripts/equities_calibration_watchdog.py --actor S1_MAIN --lookback-days 120 --min-bucket-samples 12
```

One-shot full cycle:

```bash
python3 scripts/equities_pipeline_runner.py --symbols AAPL,MSFT,NVDA,SPY,QQQ --lookback-days 180 --model-version v1_freeopen
```

Loop mode:

```bash
python3 scripts/equities_pipeline_runner.py --loop --interval-seconds 600 --lookback-days 180 --model-version v1_freeopen
```

## Backfill rule (hard)

Backfills must be split into at least 5 shard tasks.

Enqueue shard-safe backfill tasks:

```bash
python3 scripts/equities_backfill_enqueue.py --symbols AAPL,MSFT,NVDA,AMZN,SPY,QQQ --lookback-days 365 --shards 5
```

This creates `BACKFILL` tasks with `slice_total >= 5` and deterministic shard metadata.

## 2-year raw-first equities backfill (priority path)

New migration:

- `Backend/docs/db/migrations/20260222_add_equities_raw_mentions.sql`

New scripts:

- `Backend/scripts/equities_raw_mentions_backfill.py`
- `Backend/scripts/equities_raw_backfill_enqueue.py`

Apply migration:

```bash
docker exec -i backend-postgres-1 psql -U trading -d trading < docs/db/migrations/20260222_add_equities_raw_mentions.sql
```

Enqueue shard-safe 2-year backfill onto Task Board:

```bash
python3 scripts/equities_raw_backfill_enqueue.py \
  --database-url "$DATABASE_URL" \
  --data-database-url "$DATABASE_URL" \
  --top-n 62 \
  --lookback-days 730 \
  --ohlcv-shards 10 \
  --mention-shards 15 \
  --mention-sources sec,gdelt,reddit
```

Run 15 workers against the same Task Board DB:

```bash
for actor in \
  S1_AGENT_1 S1_AGENT_2 S1_AGENT_3 S1_AGENT_4 S1_AGENT_5 \
  S2_AGENT_1 S2_AGENT_2 S2_AGENT_3 S2_AGENT_4 S2_AGENT_5 \
  S3_AGENT_1 S3_AGENT_2 S3_AGENT_3 S3_AGENT_4 S3_AGENT_5; do
  nohup env DATABASE_URL="$DATABASE_URL" PYTHONPATH=./scripts \
    python3 scripts/task_board_worker.py \
      --actor "$actor" \
      --task-types BACKFILL,EXEC \
      --poll-seconds 10 \
      --lease-seconds 60 \
      --renew-seconds 20 \
      > "logs/taskboard_${actor}.log" 2>&1 &
done
```

Run supervisor loop:

```bash
nohup env DATABASE_URL="$DATABASE_URL" PYTHONPATH=./scripts \
  python3 scripts/task_board_supervisor.py \
    --actor S1_MAIN \
    --poll-seconds 10 \
    --min-backlog 25 \
    > logs/taskboard_supervisor.log 2>&1 &
```

Check backfill coverage:

```bash
docker exec backend-postgres-1 psql -U trading -d trading -c "
SELECT source, COUNT(*) rows, MIN(mention_ts), MAX(mention_ts)
FROM equities_raw_mentions
GROUP BY source
ORDER BY rows DESC;"
```

## Regeneration discipline

- All deterministic IDs are generated via UUID5 in `Backend/scripts/equities_predictor/ids.py`
- Predictor outputs can be regenerated by rerunning pipeline scripts with same inputs and `model_version`
- Append-only event/prediction/evaluation tables preserve audit history

## Monitoring hooks

Every major stage emits to:

- `equities_pipeline_events`
- `task_board_events` (mirrored as `EQUITIES_*` event types)

Use:

```bash
python3 scripts/task_board_cli.py dashboard
python3 scripts/task_board_cli.py system-state
```

and:

```bash
curl -H "X-API-Key: ..." http://localhost:8002/v1/equities/pipeline/health
```

## Known limitations (documented by design)

- No paid feeds (analyst premium terminals, proprietary tape, paid social firehose) are intentionally excluded.
- Earnings calendar uses free/open proxies (SEC filings and open news signals), not paid official calendars.
- Open-web sources may intermittently throttle; source cache and fallback behavior are implemented and logged.
