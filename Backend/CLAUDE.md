# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCAS (Market Cognition At Scale) v7 — a crypto trading analytics and paper trading system. Python 3.12, FastAPI, PostgreSQL 16, Redis 7. 1024-symbol Known Universe with dynamic Monitored Universe (~200 symbols filtered by 24h volume), generates ranked signals via three setups (S1: BB Squeeze, S2: Golden Cross, U1: Unicorn Ignition), and executes paper trades with full position management.

## Build & Run Commands

```bash
# Start infrastructure only
docker-compose up -d postgres redis

# Initialize database schema
psql -U trading -d trading -f docs/db/schema/mcas_schema.sql

# Start all services (Caddy, API, Worker, Postgres, Redis)
docker-compose up -d

# Rebuild after code changes
docker-compose up -d --build

# View logs
docker-compose logs -f api
docker-compose logs -f worker
```

No test framework or linter is configured yet.

## Architecture

Two main services share a Postgres database and Redis instance:

- **API** (`api/app/`): FastAPI HTTP server. Entry point is `main.py`. Three routers split by concern:
  - `control_api.py` — run management (start/stop scans, paper trading)
  - `runs_api.py` — run history and analytics queries
  - `trading_api.py` — account, positions, performance endpoints

- **Worker** (`worker/app/`): Queue-driven background processor. Entry point is `worker.py`. Main loop claims queued runs via `FOR UPDATE SKIP LOCKED`, executes them, and monitors open positions in real-time.

- **Caddy** (`caddy/Caddyfile`): Reverse proxy routing `/health` and `/v1/*` to the API on ports 80/8000.

### Worker internals

- `signal_engine.py` — fetches OHLCV from Binance, computes indicators, runs setup evaluators, returns ranked signals
- `indicators.py` — Feature Factory: all indicator math (ATR, RSI, BBands, Keltner, EMAs, SMAs, slopes, percentiles). Uses numpy arrays. Requires 201 bars warmup.
- `setups/s1_bb_squeeze.py` — BB Squeeze v2.1 setup evaluator
- `setups/s2_golden_cross.py` — Golden Cross v1.4 setup evaluator (includes BTC shock filter)
- `setup_base.py` — abstract `SetupBase` class and shared enums (`Direction`, `ExitType`)
- `run_executor.py` — unified executor for SCAN/BACKTEST/PAPER run types
- `paper_trading.py` — paper trading engine with persistent state, slippage/fee models, risk controls
- `price_feed.py` — live price polling (Binance REST, 2s interval) + position exit monitoring (1s)
- `execution.py` — slippage/fee model defaults (5bps slippage, 2/4bps maker/taker fees)
- `data_layer.py` — OHLCV persistence to Postgres
- `runs/models.py` — data models (Run, RunTrade, TradeTrace); `runs/repository.py` — DB access layer

### Data flow

API queues a run → Redis pubsub notifies worker → Worker claims run atomically from Postgres queue → Signal engine fetches OHLCV + computes indicators → Setup evaluators generate signals → Paper trading engine manages positions → Price feed monitors exits in real-time → Results written to Postgres.

## Key Patterns

- **Auth**: API key via `X-API-Key` header, validated with `hmac.compare_digest()`. All `/v1/*` routes protected; `/health` is public.
- **Response envelope**: `{data, meta: {timestamp, version, compute_ms}}`
- **Run fingerprinting**: SHA256 of universe+strategy+execution+data specs, prevents duplicate runs.
- **Queue atomicity**: `FOR UPDATE SKIP LOCKED` prevents double-claims across workers.
- **Risk controls**: daily loss circuit breaker (3%), daily trade cap (20), per-asset cooldown (6h after stop-out), max 3 concurrent positions.
- **Exit priority order**: STOP_LOSS → FAIL_FAST → TIME_STOP → TRAILING_STOP → TP1 → TP2.

## Environment Setup

Copy `.env.example` to `.env` and set `POSTGRES_PASSWORD`, `API_KEY`, and `DATABASE_URL`. Key variables:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres connection string |
| `REDIS_URL` | Redis connection (default `redis://redis:6379/0`) |
| `API_KEY` | 64-char hex for API auth |
| `SETUP_IDS` | Active setups, comma-separated (default `S1,S2`) |
| `SCAN_INTERVAL_SECONDS` | Periodic scan interval (default 300) |
| `ENABLE_PERIODIC_SCANS` | Toggle auto-scans (default true) |
| `ENABLE_POSITION_MONITORING` | Toggle position monitoring (default true) |
| `KNOWN_UNIVERSE_SIZE` | Size of Known Universe — top USDT pairs by volume (default 1024) |
| `MIN_24H_QUOTE_VOLUME_USD` | Volume threshold for Monitored Universe; 0 = monitor all (default 5000000) |
| `SYMBOL_COUNT` | **DEPRECATED** — fallback only (default 200) |

## Database

Schema lives in `docs/db/schema/mcas_schema.sql`. Core tables: `runs`, `run_queue`, `run_events`, `run_trades`, `paper_accounts`, `paper_positions`, `ohlcv`, `data_snapshots`. No Alembic migrations in use yet — schema is applied directly via SQL.

## Adding a New Setup

1. Create `worker/app/setups/s<N>_name.py` extending `SetupBase` from `setup_base.py`
2. Implement `evaluate(features) -> Optional[Signal]` with tier 3 state + tier 4 trigger logic
3. Register in `signal_engine.py` setup loader
4. Add setup ID to `SETUP_IDS` env var
