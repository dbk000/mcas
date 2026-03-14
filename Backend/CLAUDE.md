# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository. This document is superseded by the main CLAUDE.md in repo root.

## Project Overview

MCAS (Market Cognition At Scale) — crypto trading analytics platform with Glass Box causality proofs. Python 3.12, FastAPI, PostgreSQL 16, Redis 7. 1024-symbol Known Universe, dynamic Monitored Universe (~200 symbols filtered by 24h volume). Strategies: D1 (Dormancy Ignition), U1 (Unicorn Ignition), S9 (Short Reversion), S10 (MACD Scalp), D2 (Fade the Casino — paper trading).

## Build & Run

```bash
docker compose up -d                    # Start all 20 services
docker compose up -d --build            # Rebuild after code changes
docker compose logs -f api              # View logs
docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/<script>.py  # Research scripts
```

Images: `Dockerfile.worker` (WORKDIR /backend, PYTHONPATH=/backend), `Dockerfile.api` (uvicorn api.app.main:app).

## Architecture

Three-layer governed structure. Imports flow downward only: `core_logic/` → `governance/` → `api/`.

### core_logic/ — Pure math. No I/O. No config reads. No governance imports.
- `d1_dormancy_ignition.py` — D1 state machine, gates, 8-component scoring
- `u1_unicorn_ignition.py` — U1 ignition features, 9-component scoring, envelope stop
- `s9_short_reversion.py` — S9 cross-sectional ranking, breadth gate
- `s10_macd_scalp.py` — S10 MACD crossover, flat-top detection
- `opportunity_simulator.py` — exit walk with tiered granularity (1m/5m), kline_lineage builder
- `exit_checker.py` — Canon exit priority: STOP → FAIL_FAST → TIME_STOP → TRAIL → TP1 → TP2
- `exit_checker_vectorized.py` — numpy-optimized path for stateless configs
- `microstructure.py` — 1s tape analysis, directional (long/short), MicrostructureContext
- `dxy_math.py` — DXY regime classifier (STRONG_UP/UP/NEUTRAL/DOWN/STRONG_DOWN)
- `execution.py` — slippage/fee models

### governance/ — Control room. Orchestration, registries, dispatch, persistence, services.
- `config_registry.py` — TypedConfigRegistry (sole config authority)
- `strategy_registry.py` — StrategyRegistry (sole dispatch authority, timeframe affinity)
- `signal_worker.py` — signal evaluation via StrategyRegistry, TraceContext recording, DXY gating, capacity scoring
- `run_executor.py` — simulation orchestration
- `persistence.py` — psycopg_pool.ConnectionPool + Redis clients
- `cache_contract.py` — Redis key patterns, ring buffers, atomic ops (canonical copy)
- `trace_context.py` — Glass Box sealed audit trail, SHA-256 integrity hash
- `ingestor.py` — Binance WS 1s klines → CandleAggregator → 1m/5m/15m/1h/4h/1d
- `indicator_worker.py` — indicator compute pipeline
- `setup_base.py` — Direction ("long"/"short"/"none"), ExitType, SetupBase ABC, ExplainPayload, IndicatorRequirement

### api/app/ — HTTP endpoints only.
- `main.py` — app creation, auth (HMAC X-API-Key), inline endpoints, router mounting
- `routers/` — control, runs, trading, emergency, analytics, meta, cc, equities, unicorns, telemetry

### Data flow
1s WS klines → CandleAggregator → indicators → StrategyRegistry dispatch → signals (Redis + Postgres) → simulator (tiered exit walk) → kline_lineage in trace_json → API (sim_trace LEFT JOIN LATERAL) → TraceViewer causality badge.

## Key Patterns

- **Auth**: `X-API-Key` header, `hmac.compare_digest()`. All `/v1/*` protected; `/health` public.
- **Response envelope**: `{data, meta: {timestamp, version, compute_ms, as_of_ts}}`
- **Config**: TypedConfigRegistry is the sole authority. No `os.getenv` outside bootstrap.
- **Dispatch**: StrategyRegistry with timeframe_affinity. No `if setup_id ==` chains.
- **Trace**: Every signal gets a sealed TraceContext. Every simulation gets kline_lineage.
- **Exit priority**: STOP_LOSS → FAIL_FAST → TIME_STOP → TRAILING_STOP → TP1 → TP2.
- **Simulation**: `elapsed_strategy_bars` from timestamps, not raw bar count. Tiered granularity.

## Environment

`.env` file with: `DATABASE_URL`, `REDIS_URL`, `API_KEY`, `KNOWN_UNIVERSE_SIZE` (1024), `MIN_24H_QUOTE_VOLUME_USD` (5M), `DEFAULT_TIMEFRAMES`, `SETUP_IDS`. Loaded into TypedConfigRegistry at bootstrap.

## Database

PostgreSQL 16. Schema: `docs/db/schema/mcas_schema.sql` + `docs/db/migrations/*.sql`. Core tables: `opportunities`, `simulation_results`, `ohlcv`, `paper_positions`, `d2_cluster_events`. Migrations use `pg_advisory_lock()` + `IF NOT EXISTS`.

## Adding a New Setup

1. Pure math in `core_logic/<name>.py` — config injected via constructor
2. Register in `governance/strategy_registry.py` with `timeframe_affinity`
3. Add TypedConfig in `governance/config_registry.py`
4. Wire TraceContext in `governance/signal_worker.py`
