# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

<overarching instructions to take precedence over everything that follows>
# MCAS Canon (Repository Root Guidance)

This file is the *macro-level* “north star + guardrails” for all MCAS code iterations.
If anything conflicts with this canon, the code is wrong (or the canon must be explicitly amended).

---
the file named "chats combined text.md" located in the same directory as this CLAUDE.md contains all the chats that gave birth to the current canon scope. You read it before implementing new features or changing existing features, as the "chats combined text.md" is a meta-North Star i.e. what the Claude-generated North Star oritents itself with.
## 0) The North Star

MCAS = **Market Cognition At Scale**: scan many markets, compute a large indicator universe, generate **ranked, explainable signals**, and guide **human-in-the-loop** execution (MVP default).  
The edge is: **do the popular retail-crowded stuff, faster** — with better ranking, guardrails, and auditability.

---

## 1) Source-of-Truth Hierarchy

1. **Canon logs (“Chats combined_text”)**: chronologically deduped; later refinements override earlier.
2. `docs/` specs (module-level contracts).
3. Code.

**Rule:** If you change behavior/semantics, you must update the relevant canon/spec in the same PR.

---

## 2) Non-Negotiables (Do Not Violate)

### A) Human-in-the-loop (MVP)
- MCAS produces *actions + timing + rationale*.
- The user is the final actuator (manual execution) unless a future phase explicitly enables auto-execution.

### B) API-first, UI-swappable architecture
- The backend is the system of record for computation and ranking.
- The frontend is a thin client: render + request + workflow actions only.

### C) Determinism & audit
- Every computed value must map to a **canonical ID** (indicator_id / signal_id) and version.
- Every trade decision must be reproducible via stored snapshots (inputs, params, versions, timestamps).

### D) Efficiency by design
- Assume scale: **~1024 markets** with frequent updates.
- Prefer cache/ring-buffer compute; store only what’s needed for durability + audit.
- No “accidental data warehouses”.

---

## 3) Core Definitions (Shared Language)

- **Market**: (exchange + pair). OHLCV truth lives here.
- **Asset**: a symbol abstraction that can map to one-or-many markets.
- **Timeframe**: explicit string (`"1m"`, `"5m"`, `"1h"`, …).
- **Candle**: identified by `bar_start_ts` (UTC) + timeframe.
- **Feature**: numeric indicator output (e.g., `RSI_14@1m`).
- **Signal**: setup-derived state (e.g., `S1_TRIGGERED`), with score + timing.
- **Setup**: plug-in strategy module (S1, S2, Unicorns, etc).
- **Run**: a bounded execution context (backtest, paper, live scan cycle).
- **Snapshot**: the frozen record at decision time (for audit & replay).

---

## 4) Architecture Invariants

### A) Data plane (truth ledger)
- Store OHLCV as *an attribute of a market*, not “one price per coin”.
- Persist candles with:
  - `timeframe`
  - `bar_start_ts` (UTC; the candle identity)
  - `as_of_ts` (when data was last refreshed / valid-through)
  - `is_final` (bar closed)

### B) Compute plane (Feature Factory / Signal Engine)
- Compute indicators from canonical input candles.
- Use **tiering** to control cost:
  - Tier 1–2: cheap primitives / derived basics (often compute on-the-fly)
  - Tier 3+: store only higher-order state that’s expensive or needed for monitoring

### C) Control plane (universe + workflow)
- Universe inclusion ≠ compute capability.
- Maintain flags/tables to control:
  - what is screened,
  - what is monitored,
  - what is tradable in MVP.

---

## 5) UI ↔ Backend Contract (Hard Rules)

### A) “As-of” consistency
Every relevant response must include:
- `as_of_ts` (backend freshness)
- per-value `candle_ts` (what candle the number belongs to)

### B) Frontend does **not** compute indicators
UI only:
- requests latest features/signals,
- renders,
- writes workflow actions (notes, universe pin/unpin, etc).

### C) Deterministic identifiers
- `indicator_id` and `signal_id` must match canonical library IDs.
- Timeframe is always explicit.
- No ad-hoc IDs in the UI.

### D) Ranking is server-side
- Backend returns `rank`, `score_total`, and `score_components`.
- UI displays; UI does not implement bespoke sorting logic.

### E) Signal timing metadata (required)
Each signal should expose:
- `since_ts` = first time the signal entered its current state
- `action_by_ts` = latest “still valid” time to act (derived from setup rules, e.g., time-stop windows)

---

## 6) Indicator Library Governance (What to Codify)

You **do not** hand-write arithmetic steps for thousands of indicators as the primary artifact.
You codify **identity + provenance**:

Each indicator definition must include:
- `indicator_id` (stable)
- inputs (OHLCV / derived dependencies)
- params (defaults)
- expression type (value/slope/cross/percentile/etc)
- implementation pointer (function/template)
- version (formula/params versioning hook)

Ambiguity hotspots (must be explicit):
- pivot/swing definitions,
- percentile/ranking windows,
- slope definitions,
- any composite scoring stacks.

---

## 7) Setups as Plug-in Modules (Strategy Factory)

### A) Setup interface contract
Every setup module must define:
- preconditions (regime/filters)
- trigger definition (no lookahead)
- entry definition (explicit fill model)
- initial stop + invalidation
- time stop (progress deadline)
- targets/trailing state machine
- concurrency rules (human bandwidth)
- liquidity tiers / sizing caps
- explainability payload (3–8 “why” items)

### B) S1 is the template
S1 (BB Squeeze On) is the reference blueprint for:
- tiered indicator build order,
- strict trigger quality,
- time-stop semantics,
- ranking + explain arrays,
- liquidity + BTC-filter patterns.

### C) Champion / Challenger everywhere
Champion/challenger is not only “which setup wins”, but also:
- setup modules,
- parameters,
- scoring components.
Keep this pluggable and versioned.

### D) Unicorns is first-class (not a side quest)
Unicorn discovery is a dedicated strategy family: low-volume pre-ignition detection.
It must fit the same setup plug-in contract and ranking/guardrails framework.

---

## 8) Anti-Lookahead & Backtest Safety

- No circular definitions (e.g., “ripe because breakout happened”).
- Entry fills must be defined in a backtest-safe way (e.g., next bar open + slippage model).
- Keep “forming candle” vs “final candle” explicit (`is_final`).

---

## 9) Storage Policy (Cost + Audit)

Persist:
- OHLCV truth (bounded history per market/TF, pruned)
- minimal durable state for Tier 3+ (where required)
- trade snapshots (full provenance)

Do **not** persist:
- the entire indicator universe history by default
- redundant derived series that can be recomputed cheaply

---

## 10) DB / Schema Governance

- No schema drift: schema changes are versioned and repeatable.
- Avoid duplicating DDL in multiple runtime entrypoints.
- Treat schema as a governed artifact (migration + CI checks).

---

## 11) PR Checklist (Fail the PR if any are “No”)

- [ ] Does this preserve UI-thin / backend-compute?
- [ ] Are IDs deterministic and aligned to the canonical library?
- [ ] Does every endpoint include `as_of_ts` and per-value `candle_ts` where applicable?
- [ ] Is ranking server-side and stable?
- [ ] Are time semantics explicit (UTC, bar_start_ts vs as_of_ts, is_final)?
- [ ] Does this scale (1024 markets) without exploding storage/compute?
- [ ] If behavior changed, did you update docs specs?
- [ ] Are audit snapshots sufficient to reproduce decisions?

---

## 12) “Canon Patch” Process (How to Evolve This Safely)

When the vision evolves:
1. Add a dated “Canon Patch” section at the bottom describing the change.
2. Update affected module specs in `docs/`.
3. Implement code + tests + contract checks.

---

## Canon Patches (append-only)
<!-- YYYY-MM-DD: describe changes + why -->
<overarching instructions to take precedence over everything that follows>

## Project Overview

MCAS (Market Cognition At Scale) v7 — a crypto trading analytics and paper trading system. Scans 200+ crypto markets, generates ranked signals via two setups (S1: BB Squeeze, S2: Golden Cross), and executes paper trades with full position management.

**Stack**: Python 3.12 (FastAPI), React 18 (Vite + Tailwind), PostgreSQL 16, Redis 7, Caddy 2, Docker Compose.

## Build & Run Commands

### Backend (Docker Compose)

```bash
# Start infrastructure only
docker-compose -f Backend/docker-compose.yml up -d postgres redis

# Initialize database schema (first run)
psql -U trading -d trading -f Backend/docs/db/schema/mcas_schema.sql

# Start all services (Caddy, API, Worker, Postgres, Redis)
docker-compose -f Backend/docker-compose.yml up -d

# Rebuild after code changes
docker-compose -f Backend/docker-compose.yml up -d --build

# View logs
docker-compose -f Backend/docker-compose.yml logs -f api
docker-compose -f Backend/docker-compose.yml logs -f worker
```

### Frontend

```bash
cd Frontend
npm install
npm run dev       # Dev server on port 3000, proxies /api → localhost:8000
npm run build     # Production build
npm run preview   # Preview production build
```

No test framework or linter is configured for either backend or frontend.

## Architecture

Two backend services share a Postgres database and Redis instance. A React SPA provides the UI.

### Backend API (`Backend/api/app/`)

FastAPI HTTP server on port 8000. Entry point: `main.py`. Three routers:
- `control_api.py` — run management (start/stop scans, backtests, paper trading)
- `runs_api.py` — run history and analytics queries
- `trading_api.py` — account, positions, performance endpoints

### Worker (`Backend/worker/app/`)

Queue-driven background processor. Entry point: `worker.py`. Main loop claims queued runs via `FOR UPDATE SKIP LOCKED`, executes them, and monitors open positions in real-time.

Key modules:
- `signal_engine.py` — orchestrates OHLCV fetch, indicator computation, setup evaluation
- `indicators.py` — Feature Factory: all indicator math (ATR, RSI, BBands, Keltner, EMAs, SMAs, slopes). Numpy arrays. Requires 201 bars warmup.
- `setups/s1_bb_squeeze.py` — BB Squeeze v2.1 (front-runs volatility expansion from BB squeeze inside KC)
- `setups/s2_golden_cross.py` — Golden Cross v1.4 (EMA 9/21 cross with BTC shock filter and t-1 check)
- `setup_base.py` — abstract `SetupBase` class, `Direction`/`ExitType` enums, global registry
- `paper_trading.py` — paper trading engine with persistent state, slippage/fee models, risk controls
- `price_feed.py` — live price polling (Binance REST, 2s interval) + position exit monitoring (1s)
- `execution.py` — slippage/fee model (5bps slippage, 2/4bps maker/taker fees)
- `run_executor.py` — unified executor for SCAN/BACKTEST/PAPER run types
- `runs/repository.py` — database access layer; `runs/models.py` — dataclasses

### Frontend (`Frontend/src/`)

React 18 SPA with Vite. Terminal aesthetic (dark grays, amber accents, JetBrains Mono).

- `context/ApiContext.jsx` — API client, connection state, auth (baseUrl + apiKey in localStorage)
- `context/ToastContext.jsx` — toast notifications
- `hooks/useData.js` — `useFetch`, `usePolling` hooks for data fetching with polling intervals
- `utils/formatters.js` — number/date formatting
- `components/Layout.jsx` — sidebar + header navigation
- `components/ui.jsx` — reusable UI components (tables, cards)
- `pages/` — Dashboard, Opportunities, Markets, Runs, ControlPlane, PaperTrading, Settings
- Routes protected via `ProtectedRoute` wrapper requiring baseUrl + apiKey

### Caddy (`Backend/caddy/Caddyfile`)

Reverse proxy: `/health` and `/v1/*` route to API on port 8000.

### Data Flow

API queues a run → Redis pubsub notifies worker → Worker claims run atomically from Postgres → Signal engine fetches OHLCV + computes indicators → Setup evaluators generate signals → Paper trading engine manages positions → Price feed monitors exits → Results written to Postgres.

## Key Patterns

- **Auth**: API key via `X-API-Key` header, `hmac.compare_digest()`. All `/v1/*` protected; `/health` public.
- **Response envelope**: `{data, meta: {timestamp, version, compute_ms}}`
- **Run fingerprinting**: SHA256 of universe+strategy+execution+data specs prevents duplicate runs.
- **Queue atomicity**: `FOR UPDATE SKIP LOCKED` prevents double-claims across workers.
- **Risk controls**: daily loss breaker (3%), trade cap (20/day), per-asset cooldown (6h after stop-out), max 3 concurrent positions, 1% risk per trade.
- **Exit priority**: STOP_LOSS → FAIL_FAST → TIME_STOP → TRAILING_STOP → TP1 → TP2.
- **Indicator tiers**: Tier 0 (raw OHLCV) → Tier 1 (ATR, RSI, volume) → Tier 2 (MAs, BB, KC, slopes) → Tier 3 (setup state) → Tier 4 (entry signals).
- **Frontend state**: Context API for globals, polling-based updates (no WebSocket), localStorage for config.

## Environment Setup

Copy `Backend/.env.example` to `Backend/.env`. Key variables:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres connection string |
| `REDIS_URL` | Redis connection (default `redis://redis:6379/0`) |
| `API_KEY` | 64-char hex for API auth |
| `SETUP_IDS` | Active setups, comma-separated (default `S1,S2`) |
| `SCAN_INTERVAL_SECONDS` | Periodic scan interval (default 300) |
| `ENABLE_PERIODIC_SCANS` | Toggle auto-scans (default true) |
| `ENABLE_POSITION_MONITORING` | Toggle position monitoring (default true) |
| `SYMBOL_COUNT` | Number of symbols to scan (default 200) |

## Database

Schema: `Backend/docs/db/schema/mcas_schema.sql`. Core tables: `runs`, `run_queue`, `run_events`, `run_trades`, `paper_accounts`, `paper_positions`, `ohlcv`, `data_snapshots`. No migrations — schema applied directly via SQL.

## Adding a New Setup

1. Create `Backend/worker/app/setups/s<N>_name.py` extending `SetupBase`
2. Implement `evaluate(features) -> Optional[Signal]` with tier 3 state + tier 4 trigger logic
3. Register in `signal_engine.py` setup loader
4. Add setup ID to `SETUP_IDS` env var
