# CLAUDE.md — Guidance for Claude Code

This file is the macro-level “north star + guardrails” for all MCAS iterations.
If anything conflicts with this canon, the code is wrong (or the canon must be explicitly amended).

---

# 0) Source of Truth (Read Order)
# 0.1) Claude Code Session Rules
- Maximum 2 concurrent sub-agents/tasks. No more.
- Work through all tasks sequentially (or 2 at a time) without waiting for user confirmation between tasks.
- Keep changes per task to <5 files where possible.
1) **Canon logs**: `chats combined text.md` (same directory). Chronologically dedupe: later refinements override earlier.
2) **This file — MCAS Canon** (Sections 1–12 below).
3) `docs/` module specs (contracts; must be updated with behavior changes).
4) Code (implementation).

### Self-Correction Protocol (Meta-Rule)                                                                                                                                                    
  Whenever the user corrects a course of action, pushes back on an approach, or expresses frustration with a mistake — even if minor — Claude MUST immediately draft a Corrections Log entry  
  (following the format in Section 3) and print it to chat for the user to insert into CLAUDE.md. Do not wait to be asked. The goal is to capture every correctable pattern the first time it 
  occurs, preventing repeated time loss. If unsure whether something qualifies, err on the side of logging it.

**MCAS Functionality Rule:** If you change behavior/semantics, you must update the relevant canon/spec in the same PR.

---

# 1) MCAS Canon (Non-Negotiables + Architecture Invariants)

## 1.1 North Star
MCAS = **Market Cognition At Scale**: scan many markets, compute a large indicator universe, generate **ranked, explainable signals**, and guide **human-in-the-loop** execution (MVP default).  
Edge: **do the popular retail-crowded stuff, faster** — better ranking, guardrails, auditability.

## 1.2 Non-Negotiables (Do Not Violate)

### A) Human-in-the-loop (MVP)
- MCAS produces *actions + timing + rationale*.
- User is the final actuator unless a future phase explicitly enables auto-execution.

### B) API-first, UI-swappable architecture
- Backend is system of record for computation and ranking.
- Frontend is a thin client: render + request + workflow actions only.

### C) Determinism & audit
- Every computed value maps to canonical ID (indicator_id / signal_id) + version.
- Every trade decision reproducible via stored snapshots (inputs, params, versions, timestamps).

### D) Efficiency by design
- Assume scale: **~1024 markets** with frequent updates.
- Prefer cache/ring-buffer compute; store only what’s needed for durability + audit.
- No accidental data warehouses.

## 1.3 Core Definitions (Shared Language)
- Market, Asset, Timeframe, Candle (`bar_start_ts` UTC), Feature, Signal, Setup, Run, Snapshot — (keep your definitions as-is)
- **Known Universe** (~1024): All USDT pairs worth tracking. Derived from top Binance symbols by 24h quote volume. OHLCV (1h candles) ingested hourly via REST.
- **Monitored Universe** (dynamic, ~150-300): Subset of Known filtered by `MIN_24H_QUOTE_VOLUME_USD` with hysteresis. Gets WS 1m streams + full compute pipeline.

### Canonical glossary location (required)
- Core definitions MUST live in exactly one glossary file and be referenced from here.
  - Recommended path: `docs/glossary.md` (create if missing).
- Do **not** redefine these terms in other docs/specs; link to the glossary instead.
- If semantics change, update glossary + any dependent `docs/` specs in the same PR.

### Candle timestamp semantics (avoid drift)
- `bar_start_ts` (UTC) is the canonical candle identifier: **start of the interval**.
- `as_of_ts` (UTC) is the data freshness timestamp (when the system last updated/validated the value).
- `is_final` indicates whether the candle is closed/final.
- `candle_ts` may be used as an API alias for `bar_start_ts` for UI readability, but if both are present they MUST be identical.

## 1.4 Architecture Invariants
### A) Data plane (truth ledger)
- OHLCV stored as attribute of a market.
- Persist candles with timeframe, `bar_start_ts`, `as_of_ts`, `is_final`.

### B) Compute plane (Feature Factory / Signal Engine)
- Compute indicators from canonical candles.
- Use tiering to control cost (Tier 1–2 cheap; Tier 3+ persisted only if needed).

### C) Control plane (universe + workflow)
- **Known Universe** (~1024): All USDT pairs worth tracking. Derived from Binance top symbols by 24h quote volume. OHLCV (1h candles) ingested hourly via REST and persisted to Postgres. Stored in Redis `universe:known` SET.
- **Monitored Universe** (dynamic, ~150-300): Subset of Known filtered by `MIN_24H_QUOTE_VOLUME_USD` threshold with hysteresis (demote at 80% of threshold). Gets WebSocket 1m streams, indicator computation, signal evaluation. Stored in Redis `universe:monitored` SET. Refreshed hourly.
- Invariant: Known Universe is strictly USDT-quoted pairs (so quoteVolume ≈ USD).
- Universe inclusion ≠ compute capability. Known symbols get cheap hourly candles; only Monitored symbols get the full compute pipeline.
- `universe:promoted` is deprecated (replaced by two-tier known/monitored model).

## 1.5 UI ↔ Backend Contract (Hard Rules)
- Every response includes `as_of_ts` + per-value `candle_ts` where applicable.
- Frontend does not compute indicators.
- Deterministic IDs only; no ad-hoc UI IDs.
- Ranking is server-side (`rank`, `score_total`, `score_components`).
- Signals include timing metadata: `since_ts`, `action_by_ts`.

### Timestamp field naming (hard rule)
- Prefer `bar_start_ts` internally (storage + compute).
- API responses MAY expose `candle_ts` for readability, but it must always mean the candle/bar start time (i.e., `candle_ts == bar_start_ts`).
- If an endpoint returns both fields, document the aliasing in the corresponding `docs/` contract and add a contract test.

## 1.6 Indicator Library Governance
- Codify identity + provenance, not hand-written arithmetic steps for thousands of indicators.
- Each indicator definition includes ID, inputs, params, expression type, impl pointer, version.
- Ambiguity hotspots must be explicit.

### Indicator ID grammar & generation (hard rule)
- Indicator / signal IDs MUST be generated by a single canonical generator (library function). No hand-authored IDs in feature code.
- Grammar: `<FAMILY>_<PARAMS>` using **uppercase A–Z**, digits, and underscores only.
  - Examples: `EMA_200`, `RSI_14`, `ATR_14`, `MACD_12_26_9`, `BBANDS_20_2`, `DONCHIAN_20`.
- Timeframe is **not** encoded into `indicator_id`; it is part of the series key (e.g., `(market_id, timeframe, bar_start_ts, indicator_id, version)`).
- Parameter encoding must be canonical and positional. Do not introduce synonyms (`MA` vs `SMA`) or formatting variants (`MASlope_20` vs `MA_SLOPE_20`).
- CI MUST fail if:
  - two definitions resolve to the same `(indicator_id, version)` but differ semantically,
  - any ID is duplicated,
  - an indicator definition changes without bumping its version (or adding an explicit Canon Patch entry explaining why).

## 1.7 Setups as Plug-in Modules
- Setup interface contract: preconditions, trigger, entry fill model, stop/invalidation, time stop, targets/trailing state machine, concurrency, liquidity/sizing caps, explainability payload.
- S1 is the template.
- Champion/challenger everywhere (setup/modules/params/scoring components).
- Unicorns is first-class.

## 1.8 Anti-lookahead & Backtest Safety
- No circular definitions.
- Backtest-safe fills (e.g., next bar open + slippage model).
- `is_final` explicit.

## 1.9 Storage Policy (Cost + Audit)
Persist: OHLCV truth (bounded/pruned), minimal durable Tier 3+ state, decision snapshots.
Do not persist: whole indicator universe history by default, redundant recomputable series.

### Boundary: evented audit logs vs. “indicator warehouse”
To satisfy auditability **without** exploding storage:
- Persist **evented** feature/signal logs only at key decision events (not every candle for every indicator):
  - setup armed / fired / invalidated,
  - threshold cross,
  - enters/leaves a ranked top-N (or score bucket),
  - entry/exit decisions and any post-entry state flips that change guidance.
- Each event log row stores the **minimal payload needed to reproduce** the decision later:
  - canonical IDs + versions,
  - referenced candle keys (`market_id`, `timeframe`, `bar_start_ts`),
  - key feature values / score components used for that decision,
  - reason codes / explainability payload.
- Logs MUST be **bounded**:
  - choose and document a retention window and/or rollups (e.g., roll up to daily aggregates after N days),
  - use partitioning and/or compression for large append-only tables.
- Do **not** persist per-candle values for the entire indicator universe by default.
  - Full-series persistence is allowed only when explicitly whitelisted (too expensive to recompute, or required for a specific setup/backtest), and must be called out in `docs/` + a Canon Patch if it changes storage footprint materially.

## 1.10 DB / Schema Governance
- No schema drift: schema changes are versioned and repeatable.
- Avoid duplicating DDL in multiple runtime entrypoints.
- Schema is governed (migration + CI checks).

## 1.11 PR Checklist (Fail PR if any are “No”)
- Preserve UI-thin/backend-compute?
- IDs deterministic/canonical?
- `as_of_ts` and `candle_ts` present where applicable?
- Ranking server-side & stable?
- Time semantics explicit (UTC, bar_start_ts vs as_of_ts, is_final)?
- Scales to 1024 markets without exploding storage/compute?
- If behavior changed, docs/specs updated?
- Audit snapshots sufficient to reproduce decisions?

- Are indicator/signal IDs produced via the canonical generator and does the ID-collision CI check pass?
- Are feature/signal logs **evented + bounded** (retention/rollups), avoiding a full indicator warehouse by default?
- If glossary/terminology changed, was the canonical glossary updated and referenced from Section 1.3?

### Automated checks mapping (make the canon enforceable)
Maintain a small and growing set of automated checks that correspond to the PR checklist above. Start with these (and expand as failures are discovered):

- **API contract tests**: responses include `as_of_ts` and candle identity timestamps (`bar_start_ts` / `candle_ts` aliasing) where applicable.
- **Indicator registry lint**: uniqueness + canonical formatting + deterministic generator enforcement; fail on collisions or drift.
- **Snapshot replay test**: given stored decision snapshot(s), reproduce the same ranking output deterministically.
- **Candle finality test**: signal triggers use only `is_final == true` data unless a module explicitly supports forming-candle behavior.
- **Schema governance check**: migrations (or schema versioning) are present and repeatable; no DDL duplication across entrypoints.
- **Queue/concurrency test** (if applicable): jobs are claimed atomically; no double-processing.

> Note: these checks can start as lightweight linters/contract tests; the goal is to make the canon “real”, not to build a massive test suite on day 1.

## 1.12 Canon Patch Process (Append-only)
When vision evolves:
1) Add dated Canon Patch entry at bottom.
2) Update affected `docs/` specs.
3) Implement code + tests + contract checks.

### 2026-02-08 — Known Universe → Monitored Universe Architecture
- **§1.3**: Added "Known Universe" and "Monitored Universe" to Core Definitions.
- **§1.4C**: Replaced single-tier universe with two-tier architecture:
  - Known Universe (~1024): hourly REST OHLCV, persisted to Postgres.
  - Monitored Universe (dynamic ~150-300): WS streams + full compute pipeline, filtered by `MIN_24H_QUOTE_VOLUME_USD` with 0.8× hysteresis.
  - Atomic universe swap via Redis RENAME.
  - `universe:promoted` deprecated.
- **§2.1, §2.3, §2.4**: Updated to reflect two-tier model, new env vars, Redis key changes.
- **Code**: `ingestor.py` (two-tier build, hourly refresh, WS restart, OHLCV persistence), `cache_contract.py` (get_known_universe), `backfill_ohlcv.py` (default 1024).
- **Migration path**: Phase 1 (KNOWN_UNIVERSE_SIZE=200, MIN_24H_QUOTE_VOLUME_USD=0) → Phase 2 (1024, 5M). Rollback: revert env vars.

---

# 2) Repository Working Notes (Implementation Facts — MUST NOT OVERRIDE CANON)

This section is descriptive of the current repo state. If it conflicts with Canon, Canon wins.

**Important:** The placeholder lines in Section 2 are *not* sufficient long-term. Replace/augment them with real, current repo facts as soon as possible (commands, file paths, component boundaries, and the true hot path). Keep this section concise, but complete.

## 2.1 Project Overview

MCAS (Market Cognition At Scale) v7 — a crypto trading analytics and paper trading system. 1024-symbol Known Universe with dynamic Monitored Universe (~200 symbols filtered by 24h volume) on Binance, computes 47 indicators across 5 tiers, generates ranked explainable signals via three setups (S1: BB Squeeze, S2: Golden Cross, U1: Unicorn Ignition), and executes paper trades with full position management and risk controls.

**Stack:** Python 3.12, FastAPI, PostgreSQL 16, Redis 7, React 18 + Vite + Tailwind, Caddy reverse proxy. All containerised via Docker Compose.

**Runtime components in this repo:**
- **API** (`Backend/api/app/`) — FastAPI HTTP server (port 8000, exposed at 8002)
- **Worker** (`Backend/worker/app/worker.py`) — queue-driven run executor
- **Ingestor** (`Backend/worker/app/ingestor.py`) — background OHLCV data ingestion
- **Indicator workers** (x2) (`Backend/worker/app/indicator_worker.py`) — parallel indicator computation
- **Signal workers** (x2) (`Backend/worker/app/signal_worker.py`) — parallel signal evaluation
- **Unicorn pipeline** (`Backend/worker/app/unicorn_pipeline.py`) — U1 setup pipeline
- **Frontend** (`Frontend/`) — React SPA (thin client)
- **Caddy** (`Backend/caddy/Caddyfile`) — reverse proxy routing `/v1/*` to API
- **Postgres 16** — persistent storage (schema, trades, positions, audit logs)
- **Redis 7** — caching, pub/sub, live signal ZSETs, price feed

**Happy path:** Ingestor fetches OHLCV from Binance → Indicator workers compute features → Signal workers evaluate setups → Ranked signals stored in Redis ZSETs + Postgres → API serves to frontend → User reviews and acts.

**Not yet implemented:** Live (real-money) execution, multi-exchange support, Alembic migrations, full CI test suite (in progress), signal_events retention automation (in progress).

## 2.2 Build & Run Commands

```bash
# Local dev up (all services)
cd Backend && docker compose up -d

# Local dev down
cd Backend && docker compose down

# Rebuild after code changes
cd Backend && docker compose up -d --build

# Run API only (for dev)
cd Backend && docker compose up -d api postgres redis

# Run frontend dev server
cd Frontend && npm install && npm run dev

# Run tests (unit + canon contract)
cd Backend && pytest tests/ -v

# DB reset (WARNING: drops all data)
cd Backend && docker compose exec postgres psql -U trading -d trading -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
cd Backend && docker compose exec postgres psql -U trading -d trading -f /docker-entrypoint-initdb.d/mcas_schema.sql

# Apply schema from scratch
psql -U trading -d trading -f docs/db/schema/mcas_schema.sql
for f in docs/db/migrations/*.sql; do psql -U trading -d trading -f "$f"; done

# Smoke check (confirm API responds)
curl http://localhost:80/health
curl -H "X-API-Key: <your-key>" http://localhost:80/v1/markets

# View logs
cd Backend && docker compose logs -f worker
cd Backend && docker compose logs -f api
```

## 2.3 Architecture (Repo Current State)

**Component list + ports:**

| Component | Container | Port | Entry Point |
|-----------|-----------|------|-------------|
| Caddy (reverse proxy) | caddy | 80, 443 | `caddy/Caddyfile` |
| API (FastAPI) | api | 8000 (internal), 8002 (host) | `api/app/main.py` |
| Worker (queue consumer) | worker | — | `worker/app/worker.py` |
| Ingestor | ingestor | — | `worker/app/ingestor.py` |
| Indicator workers (x2) | indicator_worker_0/1 | — | `worker/app/indicator_worker.py` |
| Signal workers (x2) | signal_worker_0/1 | — | `worker/app/signal_worker.py` |
| Unicorn pipeline | unicorn_pipeline | — | `worker/app/unicorn_pipeline.py` |
| PostgreSQL 16 | postgres | 5432 (internal) | — |
| Redis 7 | redis | 6379 (internal) | — |
| Frontend (React SPA) | — (separate) | 5173 (dev) | `Frontend/src/main.jsx` |

**Data flow:** API queues a run → Redis pub/sub notifies worker → Worker claims run atomically (`FOR UPDATE SKIP LOCKED`) from Postgres `run_queue` → Signal engine fetches OHLCV + computes indicators → Setup evaluators generate signals → Paper trading engine manages positions → Price feed monitors exits in real-time → Results written to Postgres + Redis.

**Canonical schemas:** `Backend/docs/db/schema/mcas_schema.sql` (base) + `Backend/docs/db/migrations/*.sql` (incremental).

**Compute hot path (must stay in-memory/cached):** Feature Factory in `indicators.py` computes all 47 indicators from ring buffers (500 bars). IndicatorCache and OHLCVCache in `signal_engine.py` hold recent state in memory. Redis ZSETs hold live ranked signals.

**Queue semantics:** Postgres `run_queue` table with `FOR UPDATE SKIP LOCKED` for atomic claiming. Priority-ordered. Max attempts with retry. Worker heartbeats track liveness.

**Universe Redis keys:**
- `universe:known` SET — Known Universe (~1024 USDT pairs). Refreshed hourly.
- `universe:monitored` SET — Monitored Universe (dynamic ~150-300). WS streams + full pipeline.
- `universe:promoted` — **DEPRECATED**, replaced by two-tier known/monitored model.

## 2.4 Environment Setup

Copy `Backend/.env.example` to `Backend/.env` and configure:

| Variable | Required | Default | Example | Secret | Breaks if missing |
|----------|----------|---------|---------|--------|-------------------|
| `POSTGRES_DB` | Y | — | `trading` | N | DB container won't start |
| `POSTGRES_USER` | Y | — | `trading` | N | DB auth fails |
| `POSTGRES_PASSWORD` | Y | — | `change_me` | **Y** (`.env` only) | DB auth fails |
| `DATABASE_URL` | Y | — | `postgresql://trading:pw@postgres:5432/trading` | **Y** | API + worker can't connect to DB |
| `REDIS_URL` | Y | `redis://redis:6379/0` | same | N | Caching, pub/sub, live signals all fail |
| `API_KEY` | Y | — | 64-char hex | **Y** (`.env` only) | All `/v1/*` endpoints return 401 |
| `DEFAULT_TIMEFRAMES` | N | `1m,5m,15m,1h,4h,1d` | same | N | Defaults to all 6 |
| `KNOWN_UNIVERSE_SIZE` | N | `1024` | `1024` | N | Size of Known Universe (top USDT pairs by volume) |
| `MIN_24H_QUOTE_VOLUME_USD` | N | `5000000` | `5000000` | N | Volume threshold for Monitored Universe (0 = monitor all) |
| `SYMBOL_COUNT` | N | `200` | `200` | N | **DEPRECATED** — fallback only if volume map fails |
| `USE_DYNAMIC_SYMBOLS` | N | `false` | `false` | N | Dynamic symbol loading (legacy fallback) |
| `SIGNAL_TOP_N_DAILY` | N | `10` | `10` | N | Rank cutoff for daily signals |
| `SEED_WORKERS` | N | `20` | `20` | N | Ingestor parallelism |
| `LOG_LEVEL` | N | `INFO` | `INFO` | N | Verbosity of logs |

## 2.5 Database

- **Engine:** PostgreSQL 16 (containerised via `postgres:16` image)
- **Schema source of truth:** `Backend/docs/db/schema/mcas_schema.sql` (v7)
- **Migrations:** `Backend/docs/db/migrations/*.sql` — versioned SQL files with `pg_advisory_lock()` for concurrency safety and `IF NOT EXISTS` for idempotency. Applied in filename order after base schema. Tracked via `schema_versions` table.
- **Application order:** Base schema first, then migrations in filename-sorted order. See header comment in `mcas_schema.sql`.
- **Note:** `ohlcv` column renamed from `open_time` to `bar_start_ts` (Canon §1.3) via migration `20260207_rename_open_time_bar_start_ts.sql`.

**Retention policies:**
- `signal_events`: 30-day detail, auto-pruned by worker daily task. See Canon §1.9.
- `ohlcv`: Bounded by ingestion scope (Known Universe ~1024 symbols x 6 timeframes for monitored, 1h only for known-but-unmonitored). Pruning policy: retain 90 days of 1m candles, unlimited for 1h+ timeframes. Not yet automated — document in schema comments.
- `run_events`: Cascade-deleted with parent `runs` record.

## 2.6 Adding a New Setup

1. Create `Backend/worker/app/setups/s<N>_name.py` extending `SetupBase` from `setup_base.py`.
2. Implement the required interface:
   - `metadata: SetupMetadata` — ID, name, version, timeframe affinity, concurrency limits
   - `required_indicators: set[IndicatorRequirement]` — canonical indicator IDs needed
   - `parameters: list[SetupParameter]` — tunable params with defaults
   - `compute_state(snapshot, **kwargs) -> TState` — Tier 3 state from indicator snapshot
   - `evaluate_trigger(snapshot, state, symbol, timeframe) -> Optional[TSignal]` — Tier 4 trigger
3. Gate `evaluate_trigger()` on `is_final == True` (Canon §1.8 anti-lookahead).
4. Include `since_ts` and `action_by_ts` in signal output (Canon §1.5).
5. Include `ExplainPayload` with 3–8 "why" items (Canon §1.7).
6. Register in `setups/__init__.py` and `signal_engine.py` setup loader.
7. Add signal_events logging calls for SETUP_FIRED and SETUP_INVALIDATED (Canon §1.9).
8. Use `generate_indicator_id()` for any new indicator IDs (Canon §1.6).
9. Add setup ID to `SETUP_IDS` env var.

---

# 3) Corrections Log (Append-only — THIS IS WHY THIS FILE EXISTS)

Purpose: capture recurring Claude Code mistakes so they never repeat.

## Format (copy/paste for each new entry)
### YYYY-MM-DD — <short title>
**Symptom:** what CC did wrong (1–3 bullets)  
**Why it’s wrong:** runtime risk / canon violation  
**Rule:** do/don’t statement that is unambiguous  
**Checklist:** add a yes/no check to PR checklist or a code review step  
**Example:** (optional) bad vs good snippet / pattern

## Entries
<!-- Append new corrections below this line -->

2026-02-06 — Persist Audit Trail vs. Ephemeral Data [CANON]

Symptom:

The system only persisted trade execution snapshots, dropping all intermediate indicator and signal data.

No record existed of signals that fired or disappeared if they didn’t lead to a trade, making debugging and analysis impossible.
Why it’s wrong: This violates determinism and auditability – every decision’s context is lost. Without a feature/signal log, it’s impossible to reproduce or explain why a recommendation was made (or not made), creating a runtime blind spot and canon breach.
Rule: Always persist a minimal feature/signal log at key events (e.g. threshold cross, entry/exit) beyond just trades. No “signals-only-in-memory” designs – ensure any signal that influences decisions is saved for audit.
Checklist: Are minimal snapshots or logs persisted to reproduce all decisions (not just final trades)?
Example: Bad: Only storing final trade outcomes, with no data on why a signal triggered. Good: Logging signal states (scores, trigger values) whenever a setup fires or flips, so every recommendation can be later traced and justified.

2026-02-06 — Indicator ID Duplication and Drift [CANON]

Symptom:

Multiple indicator definitions ended up sharing the same indicator_id (e.g. at one point 7 IDs were duplicated in the library).

Inconsistencies in naming (slight variations for similar indicators) appeared across modules.
Why it’s wrong: Duplicate or drifting IDs break the canonical lookup – computations and data mappings will collide or become ambiguous. This causes bugs in the compute registry and violates the single-source-of-truth for indicator identities.
Rule: Every indicator must have a unique, stable ID aligned with the canonical naming scheme. No two indicators should use the same ID, and naming conventions must be followed exactly to avoid subtle mismatches.
Checklist: Are all indicator IDs unique and deterministically generated according to the canonical library?
Example: Bad: Defining two different indicators both as ATR_14, or one as MA_Slope20 and another as MASlope_20. Good: Each indicator ID is one-of-a-kind (e.g., ATR_14, MA_SLOPE_20) with consistent formatting, ensuring one ID maps to exactly one definition.

2026-02-06 — Implicit Time Period vs Explicit Timestamp [CANON]

Symptom:

A design was proposed using a numeric “time period” code (e.g. 7 to represent 6am–7am) in data tables instead of storing explicit timeframe strings and timestamps.

This approach attempted to label candle intervals with an abstract period number rather than a standard timestamp.
Why it’s wrong: Using ambiguous time period codes introduces confusion and bugs (e.g. timezone ambiguity, DST issues, off-by-one interpretations of what “7” means). It duplicates information already in timestamps. This violates the canon of explicit time semantics and would be error-prone across different locales.
Rule: Use explicit UTC timestamps and timeframe identifiers for candles. Never rely on custom period indices for time — each bar must be identified by an absolute bar_start_ts (in UTC) and a timeframe (e.g., "1h"). Derive any hour-of-day or period info on the fly if needed, rather than storing it.
Checklist: Are time semantics explicit, with clear UTC timestamps (e.g. bar_start_ts) and timeframe labels (and no ad-hoc period codes)?
Example: Bad: period = 7 (ambiguous) in a candle record. Good: timeframe = "1h", bar_start_ts = 2026-01-08T06:00:00Z to mark a 6–7am UTC hourly candle.

2026-02-06 — Ignoring Final vs. Forming Candle State [RUNTIME]

Symptom:

Initial logic treated the latest candle as if it were final/closed without any marker, processing signals on partially formed data.

No use of an is_final flag or equivalent to distinguish a completed bar from one that was still updating.
Why it’s wrong: Acting on incomplete candle data can trigger false signals or flip-flopping outcomes as prices update. Without marking final bars, the system risks using transient information for decisions, leading to unstable and incorrect trading behavior – a serious runtime risk.
Rule: Incorporate an is_final indicator for market data and enforce that certain computations (especially signal triggers) only execute on closed/final candles. Always separate the handling of a fully closed bar from a still-forming bar in logic.
Checklist: Does the code explicitly handle candle finality (e.g., only acting on data where is_final == true and deferring or updating signals until bars close)?
Example: Bad: Immediately treating the 1pm candle data at 1:30pm as a confirmed signal without waiting for the 1–2pm candle to close. Good: Marking a candle is_final=false until its period ends, and only then evaluating trigger conditions, preventing premature signal generation.

2026-02-06 — Frontend Computing Indicators Locally [UI-CONTRACT]

Symptom:

The frontend (e.g. a Base44 sheet or React UI) attempted to calculate indicator values (like RSI or moving averages) on its own, rather than requesting them from the backend.
Why it’s wrong: This violates the “thin client” principle – duplicating complex logic on the UI side leads to inconsistencies (the UI could diverge from backend results) and heavier client load. It breaks the UI-backend contract by moving computation out of the source-of-truth backend, increasing maintenance and error surface.
Rule: The UI must never compute or derive indicators itself. All indicator and signal calculations happen on the backend; the frontend’s role is to display data and handle user interaction. Don’t implement indicator formulas in frontend code – do fetch precomputed values via API.
Checklist: Does the UI avoid performing any indicator calculations (relying solely on backend-provided data)?
Example: Bad: JavaScript in the browser recomputing RSI from price data. Good: The UI calls an endpoint (e.g., /v1/indicators) and simply renders the RSI value returned by the backend.

2026-02-06 — Custom Signal Sorting in UI [UI-CONTRACT]

Symptom:

The frontend applied its own sorting or filtering to the list of signals/opportunities (for example, re-ordering signals by a custom priority in the UI code).
Why it’s wrong: Ranking logic is supposed to reside in the backend to ensure consistency. If the UI re-sorts or filters signals differently, it can mislead users by showing a different order of importance than the server intended. This undermines trust in the scores and violates the contract that the backend provides the definitive ranking.
Rule: The UI should display signals in the order and with the scores exactly as provided by the backend. No bespoke sorting or client-side ranking adjustments are allowed – the server-side rank and score_total are the source of truth.
Checklist: Is ranking of signals entirely done on the server side and used as-is by the frontend (no custom re-ordering in the UI)?
Example: Bad: The web app re-sorting the signal list by its own calculation of “importance.” Good: The web app simply presents the signals as returned by the API (already ranked, e.g., with a rank field), ensuring the user sees the backend’s intended ordering.

2026-02-06 — Missing Timestamps in API Responses [UI-CONTRACT]

Symptom:

Some API endpoints returned data without including an as_of_ts (overall data timestamp) or omitted the per-value candle_ts for time-based values.

For example, the API would return indicator values or prices without any timestamp context indicating when they were last updated.
Why it’s wrong: This breaches the UI-backend contract for “as-of” consistency. Without as_of_ts, the frontend (and user) cannot tell how fresh the data is, and without per-value candle_ts, time-series values can’t be aligned to their originating candle. It risks the user acting on stale or context-less data.
Rule: Every response that delivers time-sensitive data must include a top-level as_of_ts (UTC timestamp of data freshness) and each time-indexed value should carry its candle_ts (the timestamp of that value’s source candle). Never omit these, as they are critical for UI context.
Checklist: Does every relevant API response include an as_of_ts and per-value candle_ts (to convey data freshness and timing)?
Example: Bad: { "price": 123.45 } with no timing info. Good: { "price": 123.45, "candle_ts": "2026-02-07T13:00:00Z", "meta": { "as_of_ts": "2026-02-07T13:05:00Z" } }, so the UI knows exactly the data’s time context.

2026-02-06 — Code vs. Spec Divergence [CANON]

Symptom:

Code changes (new features or behavior tweaks) were implemented without updating the corresponding specification in docs/ or the canon notes.

For instance, a parameter default or an algorithm was changed in code, but the documentation still reflected the old design.
Why it’s wrong: This creates a dangerous inconsistency where the documented truth no longer matches the actual system. Future developers or even the AI itself will be misled by outdated specs, causing compounding errors and violating the source-of-truth hierarchy (canon logs and docs should always reflect the code).
Rule: Whenever making a behavior or contract change in code, update the relevant documentation or canon in the same commit/PR. Treat the spec and code as atomically linked – if they diverge, the canon must be amended or the code is wrong.
Checklist: If this change alters documented behavior or interfaces, have the relevant docs/specs been updated accordingly?
Example: Bad: The system’s risk limit was raised from 3% to 5% in the code, but the README and CLAUDE.md still say “3%”. Good: Along with the code change to 5%, the developer updates the documentation to state the new 5% limit and notes the change in the canon patch section.

2026-02-06 — Non-Atomic Queue Consumption [RUNTIME]

Symptom:

The initial job queue processing did not properly lock or atomically claim tasks, allowing multiple worker instances to potentially pick the same run.

In testing, a queued run could be executed twice in parallel because two workers pulled it from the queue simultaneously.
Why it’s wrong: Lack of atomic claim on queue entries leads to double execution, race conditions, or data corruption (e.g., two workers both executing the same trade logic). This is a serious runtime flaw that can cause duplicate trades or inconsistent state, violating safe concurrency practices.
Rule: Use atomic operations (e.g., SQL FOR UPDATE SKIP LOCKED or dedicated claim flags) to ensure each queued job is taken by only one worker. In multi-worker setups, implement partitioning or locks so no two workers ever process the same item.
Checklist: Are queue jobs consumed under a safe lock or partition scheme (preventing any double-processing or race conditions in multi-worker scenarios)?
Example: Bad: Two workers polling a run_queue table without locks, both grabbing run ID 42 to process. Good: An UPDATE ... FOR UPDATE SKIP LOCKED claim query so that once one worker picks run 42, it’s marked and excluded from others.

2026-02-06 — OHLCV Data Attributed to Assets Instead of Markets [DATA]

Symptom:

The data model treated OHLCV price data as an attribute of an asset (coin) globally, rather than distinguishing by exchange or market pair.

For example, using a single asset_id to key candlestick data, implying “one candle series per asset,” regardless of exchange origin.
Why it’s wrong: An asset like BTC trades on many markets (BTC/USD, BTC/USDT, across different exchanges) – their OHLCV can differ. Blending or not separating these violates the “truth ledger” principle: market data must be tied to the specific market source. Failing to separate by market leads to inaccurate analytics (or confusion when an asset is listed on multiple venues) and breaks canonical data modeling.
Rule: Always key and store OHLCV data by its exact market (exchange + trading pair) and timeframe. Treat “asset-level” candle series as derived or aggregated data, not the default. In practice, a candle’s unique key should include a market identifier and timeframe, not just the asset.
Checklist: Is OHLCV time-series data stored per market (exchange/pair) with timeframe, rather than conflated under a single asset entry?
Example: Bad: A single table of daily candles indexed only by asset_id (so BTC’s data intermixes all exchanges). Good: A schema where, say, BTC on Binance USDT market vs BTC on Coinbase USD market are separate series (e.g., identified by market_id or exchange+symbol), ensuring clarity of data provenance.

2026-02-09 — Dev Override Contaminating Production Deploys [RUNTIME]                                                                                                                    
  **Symptom:**                                                                                     
  - Claude suggested `docker compose up -d --build` without `-f docker compose.yml`, which auto-loaded `docker compose.override.yml` (project name `mcas-dev`, port conflicts).               
  - This spun up a second set of dev-named containers alongside the running production stack, causing port 80 conflicts and confusion.

  **Why it's wrong:** The production stack runs as project `backend` on the server at `/srv/mcas/Backend`. The override file (`docker compose.override.yml`) sets `name: mcas-dev` and is for
  local dev only. Auto-loading it on the server creates a parallel dev stack that conflicts with production.

  **Rule:** When suggesting container rebuild/restart commands for the production server, ALWAYS use:
  ```bash
  cd /srv/mcas/Backend && docker compose -f docker compose.yml build && docker compose -f docker compose.yml up -d --no-deps
  - -f docker compose.yml — explicitly skips the dev override.
  - --no-deps — avoids recreating infra services (postgres, redis, caddy).
  - NEVER use bare docker compose up on the server.
  - ALWAYS ask the user to execute deploy commands (Claude Code runs on the dev machine, not the server).

  Checklist: Do deploy commands explicitly specify -f docker compose.yml and --no-deps?

 2026-02-09 — Migration Written Against Schema File Instead of Production DB [RUNTIME]                                                                                                   
  **Symptom:**                                                                                                                                                                                
  - Migration referenced columns (`status` on `run_trades`, `description` on `schema_versions`) that exist in `mcas_schema.sql` but not on the production database.
  - Migration failed twice, requiring two fix-and-redeploy cycles.

  **Why it's wrong:** The schema file represents the *intended* state, not necessarily what's actually deployed. Writing migrations that assume the schema file matches production causes
  failures and wastes deploy cycles.

  **Rule:** When writing migrations, reference only columns known to exist on production. If unsure, query `information_schema.columns` in the migration itself (via `DO $$ ... IF EXISTS`
  blocks) rather than assuming the schema file is accurate. For index creation, use only columns confirmed to exist.

  **Checklist:** Does the migration avoid assuming schema file matches production? Are column references verified or guarded with IF EXISTS?

Top Recurring Checks:

1. Are minimal snapshots/logs persisted to reproduce decisions? (Persist Audit Trail vs. Ephemeral Data)

2. Are all indicator IDs unique and deterministic (aligned to the canonical library)? (Indicator ID Duplication and Drift)

3. Are time semantics explicit (correct timeframe, UTC bar_start_ts, and no ambiguous “period” codes)? (Implicit Time Period vs Explicit Timestamp)

4. Does logic only act on final/closed data (uses is_final or equivalent to avoid partial-bar signals)? (Ignoring Final vs. Forming Candle State)

5. Does the UI refrain from any indicator computation (using only backend-provided values)? (Frontend Computing Indicators Locally)

6. Is signal ranking done entirely server-side with no client-side reordering? (Custom Signal Sorting in UI)

7. Does every API response include as_of_ts and per-value candle_ts where applicable? (Missing Timestamps in API Responses)

8. If behavior or data output changed, were the docs and canon updated accordingly? (Code vs. Spec Divergence)

9. Are queue consumption and concurrency controls in place to prevent double-processing? (Non-Atomic Queue Consumption)

10. Is OHLCV data stored by market (exchange+pair) and timeframe (not just by asset)? (OHLCV Data Attributed to Assets Instead of Markets)

2026-02-10 — Heredoc Piping to Docker Exec Hangs [RUNTIME]
  **Symptom:**
  - Commands using `<<'EOF'` heredocs piped into `docker compose exec` (for psql, redis-cli, etc.) hang indefinitely, showing a `>` prompt and never executing.
  - Happened multiple times: once with psql for migrations, once with redis-cli for XGROUP commands.

  **Why it's wrong:** `docker compose exec` does not reliably consume stdin from shell heredocs. The heredoc content is never delivered to the container process, causing it to block waiting for input. This wastes deploy cycles and requires Ctrl+C recovery.

  **Rule:** NEVER use heredocs (`<<EOF`) with `docker compose exec`. Instead:
  - For **single SQL/Redis commands**: use `docker compose exec -T <service> <cli> -c "COMMAND"` (one command per invocation).
  - For **multi-line SQL**: pipe via `< file.sql` using `-T` flag, e.g., `docker compose exec -T postgres psql -U trading -d trading < file.sql`.
  - For **multiple commands**: run each as a separate `docker compose exec -T ... -c "..."` invocation.

  **Checklist:** Do all `docker compose exec` commands avoid heredocs and use `-c` flags or file piping with `-T` instead?

2026-02-10 — Binance bar_start_ts is Milliseconds Not Seconds [DATA]
  **Symptom:**
  - `datetime.fromtimestamp(bar_start_ts)` threw `ValueError: year 57303 is out of range` because Binance timestamps are epoch milliseconds (e.g., `1739125800000`), not seconds.
  - The error was uncaught in `_process_event()`, bubbling to the main loop, so affected messages were never ACKed — causing infinite retry loops.

  **Why it's wrong:** Treating millisecond timestamps as seconds produces dates ~50,000 years in the future. The uncaught exception also prevented ACK, turning a data bug into a liveness issue.

  **Rule:** All Binance `bar_start_ts` values are in **epoch milliseconds**. When converting to Python `datetime`, always divide by 1000: `datetime.fromtimestamp(ts / 1000, tz=timezone.utc)`. When comparing against Redis ring buffer candle dicts, compare raw values directly (both are ms). Add a guard comment `# Binance ms epoch` at conversion points.

  **Checklist:** Are all Binance timestamp conversions to datetime dividing by 1000? Are comparison paths using consistent units (ms-to-ms)?

