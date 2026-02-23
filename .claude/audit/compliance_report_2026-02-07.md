# Compliance Report: Codebase vs CLAUDE.md Canon
**Generated:** 2026-02-07
**Scope:** Full codebase audit against CLAUDE.md (Sections 0–3, including all Corrections Log rules)
**Methodology:** All Python, JS/JSX, SQL, config, and doc files read in full by specialized agents

---

## Table of Contents
1. [Section-by-Section Findings](#findings)
2. [The 6 Automated CI Checks (§1.11)](#ci-checks)
3. [Corrections Log Compliance (Top 10 Recurring Checks)](#corrections-log)
4. [Section 2 Placeholder Status](#section-2)
5. [Prioritised Remediation Plan](#remediation)

---

## <a name="findings"></a>1. Section-by-Section Findings

---

### §1.1 North Star
**Status:** COMPLIANT
**What exists:** System scans ~200 markets (configurable via SYMBOL_COUNT), computes 47 indicators across tiers, generates ranked explainable signals, and presents them for human-in-the-loop execution.
**Canon requires:** Scan many markets, compute large indicator universe, generate ranked explainable signals, guide human execution.
**Remediation:** None.

---

### §1.2.A Human-in-the-loop (MVP)
**Status:** COMPLIANT
**What exists:** Paper trading has optional `auto_trade` flag that is opt-in. Default is human-controlled. All SCAN/BACKTEST runs produce recommendations, not auto-executions.
**Canon requires:** User is final actuator unless future phase enables auto-execution.
**Remediation:** None.

---

### §1.2.B API-first, UI-swappable
**Status:** COMPLIANT
**What exists:** Backend (FastAPI + worker) is system of record. Frontend is React SPA with zero indicator computation — confirmed across all 22 JSX files.
**Canon requires:** Backend is system of record for computation/ranking; frontend is thin client.
**Remediation:** None.

---

### §1.2.C Determinism & audit
**Status:** GAP
**What exists:** Indicator IDs are canonical (47 unique, no duplicates). `signal_events` table schema exists (migration created). TradeTrace captures conditions/indicators/state/scores. Data checksums computed for backtests. However, worker code does **not yet call** the signal_events INSERT — the table exists but is **not being populated**.
**Canon requires:** Every computed value maps to canonical ID + version. Every decision reproducible via stored snapshots.
**Remediation:** Wire signal_events INSERT calls into signal_engine.py and setup evaluators at key events (SETUP_ARMED, SETUP_FIRED, SETUP_INVALIDATED, RANK_CHANGE, ENTRY_DECISION, EXIT_DECISION).
**Effort:** M
**Risk if deferred:** HIGH — audit trail is the table structure without data; decisions cannot be replayed.

---

### §1.2.D Efficiency by design
**Status:** COMPLIANT
**What exists:** Ring buffers (500 bars), tiered indicator computation (Tier 0–4), in-memory caching (Redis + IndicatorCache), evented logs (not full warehouse).
**Canon requires:** Scale to ~1024 markets; prefer cache/ring-buffer compute; store only what's needed.
**Remediation:** None currently. At 1024 markets, OHLCV table and signal_events may need partitioning.

---

### §1.3 Core Definitions / Glossary
**Status:** COMPLIANT
**What exists:** `Backend/docs/glossary.md` exists with canonical definitions for Market, Asset, Timeframe, Candle, Feature, Signal, Setup, Run, Snapshot. Terms include `bar_start_ts`, `as_of_ts`, `is_final`, `candle_ts` semantics.
**Canon requires:** Glossary in docs/glossary.md, referenced from §1.3, no redefinition elsewhere.
**Remediation:** None — glossary is complete and no term redefinitions found elsewhere.

---

### §1.3 Candle timestamp semantics
**Status:** GAP
**What exists:** Schema uses `open_time` (not `bar_start_ts`) for the OHLCV primary timestamp column. Signal_events table correctly uses `bar_start_ts`. Run_trades uses `signal_bar_ts`. Worker code uses `open_time` throughout data_layer.py.
**Canon requires:** `bar_start_ts` (UTC) is the canonical candle identifier.
**Remediation:** Either rename `ohlcv.open_time` to `bar_start_ts` (breaking change) or document the aliasing explicitly. The internal Python OHLCV dataclass uses `open_time` — should use `bar_start_ts` or document mapping.
**Effort:** M (schema rename is a migration; code rename touches many files)
**Risk if deferred:** LOW — semantically correct, just naming inconsistency. Add alias documentation.

---

### §1.4.A Data plane (OHLCV truth ledger)
**Status:** COMPLIANT
**What exists:** `ohlcv` table stores candles with `symbol`, `timeframe`, `open_time` (bar_start_ts semantically), `as_of_ts`, `is_final`, `source`, `batch_id`. Indexed by `(symbol, timeframe, open_time DESC)`.
**Canon requires:** OHLCV stored as attribute of a market; persist with timeframe, bar_start_ts, as_of_ts, is_final.
**Note:** OHLCV is keyed by `symbol` (e.g., "BTCUSDT") not explicit `market_id`. Since all data is currently from Binance only, this works. When multi-exchange support is added, a `market_id` or `exchange` column will be needed.
**Remediation:** Add `exchange/source` to OHLCV unique key when multi-exchange is implemented.
**Effort:** S (future)
**Risk if deferred:** LOW — single exchange today.

---

### §1.4.B Compute plane (Feature Factory / Signal Engine)
**Status:** COMPLIANT
**What exists:** `indicators.py` implements Feature Factory with 47 indicators across 5 tiers. Tier 0 (raw OHLCV), Tier 1 (basic: ATR, RSI, volume Z), Tier 2 (derived: EMAs, BBands, Keltner, slopes), Tier 3+ (setup-specific state in S1/S2/U1 evaluators). Tiering controls cost.
**Canon requires:** Compute indicators from canonical candles; use tiering to control cost.
**Remediation:** None.

---

### §1.4.C Control plane (universe + workflow)
**Status:** COMPLIANT
**What exists:** Universe defined via `universe_json` in runs (symbols list). Setup registration via SetupRegistry. Flags for paper trading active/inactive.
**Canon requires:** Universe inclusion ≠ compute capability; maintain flags.
**Remediation:** None.

---

### §1.5 UI ↔ Backend Contract
**Status:** VIOLATION
**What exists today:**
- **`as_of_ts` missing from ~18 endpoints** including: `/v1/markets`, `/v1/timeframes`, `/v1/squeeze/state`, `/v1/btc/squeeze-status`, `/v1/signals/*` (3 endpoints), `/v1/opportunities/{id}`, all `/v1/trading/*` (6 endpoints), all `/v1/control/*` endpoints, `/v1/telemetry/*`.
- **`candle_ts` missing** from: `/v1/opportunities` (Postgres fallback path), `/v1/opportunities/{id}`, `/v1/trading/positions` (has `signal_ts` but no `candle_ts`).
- **Response envelope inconsistent**: `trading_api.py` and `control_api.py` return bare dicts without `{data, meta}` envelope. Other modules (analytics, cc, unicorns, runs) use envelope correctly.
- **`since_ts` / `action_by_ts`** present in `run_trades` schema and API for run trades, but **not displayed anywhere in frontend** (0 JSX matches).

**Canon requires:** Every response includes `as_of_ts` + per-value `candle_ts`; ranking server-side; signals include timing metadata (since_ts, action_by_ts).
**Remediation:**
1. Add `as_of_ts` to all endpoint responses (use consistent envelope pattern)
2. Add `candle_ts` to opportunity and position responses (Postgres path)
3. Wrap `trading_api.py` and `control_api.py` in standard envelope
4. Display `since_ts` / `action_by_ts` in Opportunities.jsx and OpportunityDetail.jsx

**Effort:** M
**Risk if deferred:** HIGH — hard contract violation; frontend cannot determine data freshness.

---

### §1.5 Ranking server-side
**Status:** COMPLIANT
**What exists:** Signals sorted by `score DESC` in Postgres queries. Redis ZSETs ordered by score. Opportunities.jsx `sortOpportunities()` explicitly preserves server order as default (with Canon §1.5D comment in code). No client-side reranking.
**Canon requires:** Ranking is server-side (rank, score_total, score_components).
**Remediation:** None.

---

### §1.6 Indicator Library Governance
**Status:** VIOLATION
**What exists:** 47 indicators registered via `_register(IndicatorDef(...))` in `indicators.py`. IDs are **hand-authored strings** passed to `_register()`. No canonical generator function exists. IDs follow UPPERCASE_PARAMS grammar correctly. `get_indicator_library_fingerprint()` returns SHA256 hash for versioning. No duplicates currently.
**Canon requires:** IDs MUST be generated by a single canonical generator function. No hand-authored IDs in feature code. CI MUST fail on collision, duplication, or semantic change without version bump.
**Remediation:**
1. Create `generate_indicator_id(family: str, *params) -> str` function
2. Refactor all `_register()` calls to use the generator
3. Add CI lint test for uniqueness, format validation, collision detection

**Effort:** M
**Risk if deferred:** MEDIUM — currently no duplicates, but manual authoring is fragile at scale. The Corrections Log explicitly flags this as a historical problem.

---

### §1.7 Setups as Plug-in Modules
**Status:** COMPLIANT
**What exists:** `SetupBase` abstract class with generic types, required interface (metadata, required_indicators, parameters, compute_state, evaluate_trigger). `SetupRegistry` for dynamic loading. S1, S2, U1 all follow the contract. Metadata includes concurrency limits, liquidity tiers. Explainability payload in all signals.
**Canon requires:** Setup interface contract with preconditions, trigger, fill model, stops, targets, concurrency, explainability. Champion/challenger support.
**Remediation:** None — well implemented.

---

### §1.8 Anti-lookahead & Backtest Safety
**Status:** COMPLIANT
**What exists:** S1 and S2 both explicitly gate on `is_final == True` with early return (line 351 and 328 respectively). Backtest loader filters `WHERE is_final = TRUE`. S1 uses `ripe_prev_bar` (t-1) check to avoid circular definitions. Backtest fills use next-bar open + slippage model (5bps default).
**Canon requires:** No circular definitions; backtest-safe fills; is_final explicit.
**Remediation:** None.

---

### §1.9 Storage Policy (Cost + Audit)
**Status:** GAP
**What exists:**
- `signal_events` table schema exists with correct event types and minimal payload design
- Retention documented as "30 days detail, roll up to daily aggregates"
- **NOT YET POPULATED** — worker code doesn't INSERT into signal_events
- **Retention NOT AUTOMATED** — no cron job, pg_cron, or worker task for cleanup
- **Partitioning NOT IMPLEMENTED** — single flat table
- OHLCV retention/pruning not specified

**Canon requires:** Evented feature/signal logs at key decision events. Bounded retention with documented rollups. No full indicator warehouse.
**Remediation:**
1. Wire signal_events INSERT into worker (signal_engine.py, setup evaluators)
2. Implement automated retention (pg_cron or worker scheduled task)
3. Add partition-by-month strategy for signal_events when volume justifies it
4. Document OHLCV retention policy

**Effort:** M (wiring) + S (retention cron)
**Risk if deferred:** HIGH — empty audit table means canon §1.9 is structurally present but operationally void.

---

### §1.10 DB / Schema Governance
**Status:** GAP
**What exists:**
- Schema source of truth: `Backend/docs/db/schema/mcas_schema.sql` (v7)
- 5 migration files with advisory locks and verification queries
- Schema snapshots (pg_dump outputs) for reference
- `IF NOT EXISTS` used throughout (can hide drift)
- No migration version tracking table
- No Alembic or equivalent tool
- DDL duplicated across mcas_schema.sql and migration files

**Canon requires:** No schema drift; versioned repeatable migrations; avoid duplicating DDL.
**Remediation:**
1. Add `schema_versions` table to track applied migrations
2. Document explicit application order (mcas_schema.sql first, then migrations in filename order)
3. Consider consolidating base schema + migrations into a single ordered set

**Effort:** S
**Risk if deferred:** MEDIUM — works in practice today but fragile at scale. IF NOT EXISTS silently masks drift.

---

### §1.11 PR Checklist & Automated Checks
**Status:** VIOLATION
**What exists:** Only 1 test file: `Backend/tests/test_backtest_pnl.py` (694 lines, backtest PnL calculations). No CI pipeline. No `.github/workflows/` directory.

**The 6 required automated checks:**

| Check | Status | Notes |
|-------|--------|-------|
| API contract tests (as_of_ts, candle_ts) | **MISSING** | No tests verify response envelope compliance |
| Indicator registry lint (uniqueness, format, generator) | **MISSING** | No automated collision/format validation |
| Snapshot replay test (reproducibility) | **MISSING** | TradeTrace exists but no test replays it |
| Candle finality test (is_final gating) | **MISSING** | Code gates correctly but no test enforces it |
| Schema governance check (migrations, no DDL duplication) | **MISSING** | No automated schema drift detection |
| Queue/concurrency test (atomic claiming) | **MISSING** | FOR UPDATE SKIP LOCKED used but untested |

**Canon requires:** Maintain growing set of automated checks; CI fails on indicator collisions, schema drift, etc.
**Remediation:** Create test suite covering all 6 check categories. Set up CI pipeline (GitHub Actions or equivalent).
**Effort:** L
**Risk if deferred:** HIGH — canon is unenforceable without automated checks.

---

### §1.12 Canon Patch Process
**Status:** COMPLIANT
**What exists:** Corrections Log (Section 3) with 10 dated entries. Append-only format followed.
**Canon requires:** Dated patch entries, update affected docs, implement code + tests.
**Remediation:** None for the process itself. Individual correction items checked below.

---

## <a name="ci-checks"></a>2. The 6 Automated CI Checks (§1.11)

| # | Check | Exists? | Coverage |
|---|-------|---------|----------|
| 1 | API contract tests (as_of_ts, candle_ts) | NO | 0% |
| 2 | Indicator registry lint (uniqueness, canonical generator) | NO | 0% |
| 3 | Snapshot replay test (deterministic ranking) | NO | 0% |
| 4 | Candle finality test (is_final gating) | NO | 0% |
| 5 | Schema governance check (migrations, no DDL duplication) | NO | 0% |
| 6 | Queue/concurrency test (atomic claiming) | NO | 0% |

**Summary:** Zero of six required automated checks exist. The only test file (`test_backtest_pnl.py`) covers backtest math but none of the canon-required contract checks.

---

## <a name="corrections-log"></a>3. Corrections Log Compliance (Top 10 Recurring Checks)

| # | Check | Status | Evidence |
|---|-------|--------|----------|
| 1 | Minimal snapshots/logs persisted to reproduce decisions? | **GAP** | `signal_events` table exists but not populated. TradeTrace captures per-trade data. Gap: non-trade signals not logged. |
| 2 | All indicator IDs unique and deterministic? | **GAP** | Currently unique (47, no dupes). But IDs are hand-authored, not generated by canonical function. |
| 3 | Time semantics explicit (UTC bar_start_ts, no period codes)? | **COMPLIANT** | All timestamps UTC. `bar_start_ts` semantics correct (called `open_time` in code). No period codes. |
| 4 | Logic only acts on final/closed data (is_final)? | **COMPLIANT** | S1, S2 both gate on `is_final == True`. Backtest loader filters `is_final = TRUE`. |
| 5 | UI refrains from indicator computation? | **COMPLIANT** | Zero indicator math in any of 22 JSX files. Confirmed exhaustively. |
| 6 | Signal ranking done entirely server-side? | **COMPLIANT** | Opportunities.jsx preserves server order by default (Canon §1.5D comment in code). |
| 7 | Every API response includes as_of_ts and candle_ts? | **VIOLATION** | ~18 endpoints missing `as_of_ts`. Multiple endpoints missing `candle_ts`. |
| 8 | Docs updated when behavior changed? | **GAP** | Section 2 of CLAUDE.md still has placeholder text ("Keep your existing..."). Some docs current (glossary, architecture). |
| 9 | Queue consumption atomic (no double-processing)? | **COMPLIANT** | `FOR UPDATE SKIP LOCKED` in `worker.py` claim_next_run(). |
| 10 | OHLCV stored per market (exchange+pair) with timeframe? | **COMPLIANT** | `ohlcv` table keyed by `(symbol, timeframe, open_time)`. Symbol includes pair (e.g., BTCUSDT). Single exchange (Binance) currently. |

---

## <a name="section-2"></a>4. Section 2 Placeholder Status

| Subsection | Status | What's There |
|------------|--------|-------------|
| §2.1 Project Overview | **PLACEHOLDER** | Template text "Keep your existing..." with fill-in instructions |
| §2.2 Build & Run Commands | **PLACEHOLDER** | Template text "Keep your docker-compose + frontend commands" |
| §2.3 Architecture | **PLACEHOLDER** | Template text "Keep your current API/Worker/Frontend/Caddy/Data Flow" |
| §2.4 Environment Setup | **PLACEHOLDER** | Template text "Keep your env var table" |
| §2.5 Database | **PLACEHOLDER** | Template text "Keep schema path note" |
| §2.6 Adding a New Setup | **PLACEHOLDER** | Template text "Keep your steps" |

**All 6 subsections are unfilled templates.** The information exists in `Backend/README.md`, `Backend/docs/architecture.md`, and `.env.example`, but is not consolidated into CLAUDE.md §2 as required.

---

## <a name="remediation"></a>5. Prioritised Remediation Plan

---

### P0: Violations — Fix Now

These actively break canon rules.

| ID | Finding | Canon Section | Files to Change | Effort |
|----|---------|--------------|-----------------|--------|
| **P0-1** | `signal_events` table not populated by worker | §1.2.C, §1.9 | `Backend/worker/app/signal_engine.py`, setup evaluators, `data_layer.py` | M |
| **P0-2** | `as_of_ts` missing from ~18 API endpoints | §1.5 | `Backend/api/app/main.py`, `trading_api.py`, `control_api.py` | M |
| **P0-3** | `candle_ts` missing from opportunities (Postgres path), positions | §1.5 | `Backend/api/app/main.py` (opportunities endpoint), `trading_api.py` | S |
| **P0-4** | Response envelope missing from `trading_api.py` and `control_api.py` | §1.5 | `Backend/api/app/trading_api.py`, `control_api.py` | M |
| **P0-5** | Indicator IDs hand-authored, no canonical generator function | §1.6 | `Backend/worker/app/indicators.py` | S |

### P1: Gaps — Fix This Sprint

Missing but not currently causing harm.

| ID | Finding | Canon Section | Files to Change | Effort |
|----|---------|--------------|-----------------|--------|
| **P1-1** | Zero of 6 required CI automated checks exist | §1.11 | New: `Backend/tests/test_api_contract.py`, `test_indicator_registry.py`, `test_snapshot_replay.py`, `test_candle_finality.py`, `test_schema_governance.py`, `test_queue_atomicity.py` | L |
| **P1-2** | No CI pipeline (no `.github/workflows/`) | §1.11 | New: `.github/workflows/ci.yml` | S |
| **P1-3** | `signal_events` retention not automated (30-day policy documented, not enforced) | §1.9 | Worker scheduled task or pg_cron | S |
| **P1-4** | Schema governance: no migration version tracking table | §1.10 | New migration: `add_schema_versions.sql` | S |
| **P1-5** | `since_ts` / `action_by_ts` not displayed in frontend Opportunities page | §1.5 | `Frontend/src/pages/Opportunities.jsx`, `OpportunityDetail.jsx` | S |
| **P1-6** | S1/S2 setups don't populate `since_ts` / `action_by_ts` (only U1 does) | §1.5 | `Backend/worker/app/setups/s1_bb_squeeze.py`, `s2_golden_cross.py` | S |
| **P1-7** | DDL duplicated between `mcas_schema.sql` and migration files | §1.10 | Document application order; consider consolidation | S |

### P2: Hygiene — Scheduled

Nice-to-haves, documentation debt, future-proofing.

| ID | Finding | Canon Section | Files to Change | Effort |
|----|---------|--------------|-----------------|--------|
| **P2-1** | Section 2 of CLAUDE.md all placeholder text (6 subsections) | §2.1–2.6 | `CLAUDE.md` | M |
| **P2-2** | `ohlcv.open_time` column not named `bar_start_ts` (naming inconsistency) | §1.3 | Schema migration + code rename (data_layer.py + many references) | L |
| **P2-3** | OHLCV keyed by `symbol` not `market_id` (single exchange assumption) | §1.4.A | Future: add `exchange` column when multi-exchange needed | M |
| **P2-4** | OHLCV retention/pruning policy not documented | §1.9 | Document in schema comments or docs/ | S |
| **P2-5** | `signal_events` partitioning not implemented (flat table) | §1.9 | Implement partition-by-month when volume justifies | M |
| **P2-6** | No rollback strategy for migrations | §1.10 | Add DOWN scripts to each migration | M |
| **P2-7** | `test_backtest_pnl.py` is the only test file (694 lines) | General | Expand test coverage beyond backtest math | L |
| **P2-8** | U1 (Unicorn) setup anti-lookahead not fully verified | §1.8 | Deep audit of u1_unicorn_ignition.py gate logic | S |

---

## Summary Statistics

| Category | Count |
|----------|-------|
| COMPLIANT | 14 |
| GAP | 7 |
| VIOLATION | 4 |
| **P0 items** | **5** |
| **P1 items** | **7** |
| **P2 items** | **8** |

### Highest Risk Items (fix first)
1. **P0-1**: signal_events not populated — audit trail is empty
2. **P0-2/3/4**: API timestamp/envelope violations — ~18 endpoints non-compliant
3. **P0-5**: Indicator ID generator missing — historical problem per Corrections Log
4. **P1-1/2**: No CI pipeline or automated checks — canon is unenforceable

---

*Report persisted to: `.claude/audit/compliance_report_2026-02-07.md`*
