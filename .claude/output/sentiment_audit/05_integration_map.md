# Integration Map: MCAS_sentiment -> Main MCAS

**Audit Date:** 2026-02-22
**Main Repo:** `/home/darren/Desktop/MCAS/`
**Sentiment Repo:** `/home/darren/Desktop/MCAS/MCAS_sentiment/`
**Built by:** Codex/GPT-5.3 (separate sessions, accumulated work)

---

## Executive Summary

The `MCAS_sentiment` repo is a snapshot of the main MCAS codebase with **four major feature branches accumulated on top**, never merged back. It contains:

1. **Vision22 (Football/Sports Betting)** -- A complete football event-level data pipeline with story-based predictors (vision22 schema). 5 sharded collector containers in docker-compose.
2. **Equities Forward Predictor** -- End-to-end equities prediction system: OHLCV ingest, event collection (SEC/RSS), feature building, forward prediction, outcome settlement, policy optimization, calibration watchdog, and API dashboards.
3. **Task Board** -- A Postgres-backed multi-agent coordination system (tasks, events, system state, supervisor, workers, CLI).
4. **Tone Phase-State Engine** -- Social tone analysis with post-level scoring, window aggregation, phase detection, and regime signals.

The base MCAS code (lines 1-877 of main.py, all worker files, all frontend files, all existing migrations) is **identical** to the main repo, with one exception: `main.py` adds the equities API router mount, and `conftest.py` adds a scripts path entry.

The sentiment repo is **not a fork** -- it is a copy with additive-only changes. There are zero conflicts in the shared codebase. All new work is in new files or trivially appended to existing files.

---

## 1. File-by-File Delta

### 1.1 MODIFIED Files (exist in both, differ)

#### `Backend/api/app/main.py`
- **Category:** MODIFIED
- **Delta:** Sentiment version adds 4 lines at the end (lines 879-881):
  ```python
  # Mount Equities Forward Predictor API (also protected)
  from equities_api import router as equities_router
  app.include_router(equities_router, prefix="/v1", dependencies=[Depends(require_api_key)])
  ```
- **Risk:** LOW -- purely additive append, no existing code changed
- **Merge action:** Append the 3 lines to main repo's `main.py`

#### `Backend/docker-compose.yml`
- **Category:** MODIFIED
- **Delta:** Sentiment version adds 5 new service blocks after `onchain_collector`:
  - `vision22_story_collector_0` through `vision22_story_collector_4` (5 sharded containers)
  - Each runs `vision22_story_collector.py` with `--shard N` and extensive CLI args
- **Risk:** MEDIUM -- these containers will try to start and hit the DB. If vision22 schema is not applied, they crash-loop.
- **Merge action:** Add the 5 service blocks to main docker-compose.yml, but keep them commented out or in a separate compose override until vision22 migration is applied.

#### `Backend/tests/conftest.py`
- **Category:** MODIFIED
- **Delta:** Sentiment version adds 3 lines (line 17):
  ```python
  # Add scripts to path for task board library/CLI modules.
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
  ```
- **Risk:** LOW -- additive path entry, harmless if scripts dir exists
- **Merge action:** Append the 3 lines

### 1.2 NEW Files (only in sentiment repo -- need to be added to main)

#### API Layer

| File | Purpose | Dependencies |
|------|---------|--------------|
| `Backend/api/app/equities_api.py` | Equities forward predictor API: opportunities, explain, dashboards (performance, realized, policy), pipeline health | Requires `equities_*` tables in Postgres |

#### Scripts -- Equities Forward Predictor

| File | Purpose | Dependencies |
|------|---------|--------------|
| `Backend/scripts/equities_ohlcv_ingestor.py` | Ingests equities OHLCV from free sources | `equities_bar_meta` table |
| `Backend/scripts/equities_events_collector.py` | Collects events from SEC EDGAR + Google News RSS | `equities_events` table |
| `Backend/scripts/equities_feature_builder.py` | Builds time-aligned feature events | `equities_feature_events` table |
| `Backend/scripts/equities_forward_predictor.py` | Runs predictions + generates opportunities | `equities_predictions`, `equities_opportunities` tables |
| `Backend/scripts/equities_outcome_settler.py` | Settles matured opportunities into realized outcomes | `equities_opportunity_outcomes` table |
| `Backend/scripts/equities_policy_optimizer.py` | Learns per-horizon selection policies from outcomes | `equities_selection_policies` table |
| `Backend/scripts/equities_calibration_watchdog.py` | Confidence monotonicity checks | `equities_calibration_alerts` table |
| `Backend/scripts/equities_pipeline_runner.py` | One-shot or loop-mode full pipeline | All equities tables |
| `Backend/scripts/equities_backfill_enqueue.py` | Enqueues shard-safe backfill tasks | Task board tables |
| `Backend/scripts/equities_raw_backfill_enqueue.py` | Enqueues 2-year raw mentions backfill | Task board + `equities_raw_mentions` |
| `Backend/scripts/equities_raw_mentions_backfill.py` | Executes raw mentions backfill work | `equities_raw_mentions` table |
| `Backend/scripts/equities_alert_bridge.py` | Converts calibration alerts to task board tasks | Task board tables |
| `Backend/scripts/equities_queue_refiller.py` | Refills equities work queue | Task board tables |
| `Backend/scripts/equities_task_executor.py` | Executes equities-specific task board tasks | All equities tables |

#### Scripts -- Equities Predictor Library (package)

| File | Purpose |
|------|---------|
| `Backend/scripts/equities_predictor/__init__.py` | Package init |
| `Backend/scripts/equities_predictor/ids.py` | UUID5 deterministic ID generation |
| `Backend/scripts/equities_predictor/calendar.py` | Market calendar utilities |
| `Backend/scripts/equities_predictor/sources.py` | Source data fetching (SEC, RSS) |
| `Backend/scripts/equities_predictor/text_utils.py` | Text processing utilities |
| `Backend/scripts/equities_predictor/universe.py` | Equities symbol universe management |
| `Backend/scripts/equities_predictor/db.py` | Database helper functions |

#### Scripts -- Task Board System

| File | Purpose |
|------|---------|
| `Backend/scripts/task_board_lib.py` | Core task board library (claim, complete, events) |
| `Backend/scripts/task_board_cli.py` | CLI interface for task board operations |
| `Backend/scripts/task_board_bootstrap.py` | Seeds initial tasks + system state |
| `Backend/scripts/task_board_worker.py` | Generic task board worker (poll + execute) |
| `Backend/scripts/task_board_supervisor.py` | Supervisor loop (quality, backlog, synthesis) |
| `Backend/scripts/task_board_war_room.py` | War room dashboard for task board |
| `Backend/scripts/task_board_backfill_splitter.py` | Splits backfill tasks into shards |
| `Backend/scripts/task_board_s2_main.py` | S2 session main loop |
| `Backend/scripts/task_board_s2_tick.py` | S2 session tick logic |
| `Backend/scripts/task_board_s2_autopilot.sh` | S2 autopilot shell script |
| `Backend/scripts/task_board_s3_main.py` | S3 session main loop |

#### Scripts -- Vision22 (Football)

| File | Purpose |
|------|---------|
| `Backend/scripts/vision22_story_collector.py` | Football story ingestion + predictor pipeline (RSS feeds, signal extraction, correlation) |
| `Backend/scripts/backfill_vision22.py` | Vision22 historical backfill |

#### Scripts -- Tone Engine

| File | Purpose |
|------|---------|
| `Backend/scripts/tone_engine_backfill_sharded.py` | Sharded backfill for tone analysis |
| `Backend/scripts/tone_engine_expand_socials.py` | Expand social data for tone analysis |
| `Backend/scripts/tone_engine_forward_predictor_discovery.py` | Forward predictor discovery using tone features |
| `Backend/scripts/tone_engine_ingest_sources.py` | Ingest sources into tone engine |
| `Backend/scripts/tone_engine_reddit_backfill_sharded.py` | Reddit-specific sharded backfill |
| `Backend/scripts/run_tone_backfill_4_agents.sh` | Shell script to launch 4 tone backfill agents |

#### Scripts -- Other

| File | Purpose |
|------|---------|
| `Backend/scripts/s1_autonomy_watchdog.py` | S1 session autonomy watchdog |

#### Database Migrations (NEW -- 11 files)

| File | Creates | Dependencies |
|------|---------|--------------|
| `20260221_add_vision22_schema.sql` | `vision22` schema: sources, competitions, teams, players, matches, events, event_types, match_stats, match_derived | None (new schema) |
| `20260221_add_vision22_story_predictors.sql` | `vision22.story_posts`, `vision22.story_predictors` + correlation tables | vision22 schema |
| `20260222_add_task_board.sql` | `task_board_tasks`, `task_board_events`, `task_board_system` | None |
| `20260222_task_board_real_work_guard.sql` | Trigger guard on task_board | task_board tables |
| `20260222_add_equities_predictor_tables.sql` | `equities_symbol_registry`, `equities_source_cache`, `equities_bar_meta`, `equities_events`, `equities_feature_events`, `equities_predictions`, `equities_opportunities`, `equities_model_evaluations`, `equities_pipeline_events` + append-only triggers | `schema_versions` table, `opportunities` table (FK) |
| `20260222_add_equities_outcomes.sql` | `equities_opportunity_outcomes` + append-only trigger | `equities_opportunities`, `equities_predictions` tables |
| `20260222_add_equities_policy_tables.sql` | `equities_selection_policies`, `equities_calibration_alerts` + append-only triggers | `schema_versions` table |
| `20260222_add_equities_raw_mentions.sql` | `equities_raw_mentions` + append-only trigger | `equities_append_only_guard()` function |
| `20260222_tone_phase_state_engine_core.sql` | `agent_notes`, `agent_artifacts`, `social_tone_posts`, `social_tone_windows`, `social_tone_phases`, `social_tone_regime_signals` | None |
| `20260222_tone_engine_source_mirror_tables.sql` | Source mirror tables for tone engine | tone engine core tables |
| `20260222_tone_engine_social_posts_id_default.sql` | Adds default ID generation to social_tone_posts | `social_tone_posts` table |

#### Documentation (NEW)

| File | Purpose |
|------|---------|
| `Backend/docs/equities_forward_predictor.md` | Full runbook: schema, API, run commands, backfill, monitoring |
| `Backend/docs/task_board.md` | Task board runbook: migration, bootstrap, supervisor, workers, CLI |

#### Tests (NEW)

| File | Purpose |
|------|---------|
| `Backend/tests/test_equities_api_contract.py` | API contract tests for equities endpoints |
| `Backend/tests/test_equities_forward_predictor.py` | Unit tests for equities predictor |
| `Backend/tests/test_equities_outcome_settler.py` | Unit tests for outcome settlement |
| `Backend/tests/test_equities_policy_feedback.py` | Tests for policy feedback loop |
| `Backend/tests/test_equities_raw_backfill.py` | Tests for raw mentions backfill |
| `Backend/tests/test_task_board_acceptance.py` | Task board acceptance tests (incl. PGDATA mount check) |

#### Root-level files (sentiment repo only)

| File | Purpose | Action |
|------|---------|--------|
| `manifest.json` | PWA manifest for "AccaEngine" (betting app) | SKIP -- not relevant to MCAS |
| `Chats combined text.md` | Historical chat logs | SKIP -- reference only |
| `BACKTEST_PUNCHLIST.md` | Backtest todo list | SKIP -- reference only |
| `DEPLOYMENT_PUNCHLIST.md` | Deployment todo list | SKIP -- reference only |
| `to-do_list.md` | General todo list | SKIP -- reference only |
| `unicorns.md` | Unicorn pipeline notes | SKIP -- reference only |
| `Backend/IMPLEMENTATION_STATUS.md` | Implementation status tracker | SKIP -- reference only |
| `DERIVATIVES_MERGE_INSTRUCTIONS.md` | Derivatives merge guide (already merged to main) | SKIP -- already done |

#### .claude/ plans (sentiment repo)

| File | Purpose | Action |
|------|---------|--------|
| `.claude/plans/u1_unicorn_ignition.md` | U1 implementation plan (1144 lines) | SKIP -- already implemented in main |
| `.claude/plans/cc_champion_challenger.md` | Champion/Challenger plan | SKIP -- already implemented in main |

#### tobemerged_* directories (inside sentiment repo)

| Directory | Purpose | Action |
|-----------|---------|--------|
| `tobemerged_sentiment/` | Social collector merge package | ALREADY MERGED to main (social_collector exists in main) |
| `tobemerged_on-chain_derivatives/` | Derivatives merge package | ALREADY MERGED to main (derivatives_collector exists in main) |
| `tobemerged_CI/` | CPU monitoring merge package | ALREADY MERGED to main (monitoring/ exists in main) |

### 1.3 IDENTICAL Files (same in both -- skip)

The entire Frontend (`Frontend/src/**`, `Frontend/index.html`, `Frontend/package.json`, `Frontend/vite.config.js`, etc.) is identical between both repos.

All existing Backend files not listed in sections 1.1 or 1.2 are identical:
- All `Backend/worker/app/*.py` files
- All `Backend/worker/app/setups/*.py` files
- All `Backend/worker/app/runs/*.py` files
- All `Backend/api/app/*.py` files (except main.py)
- All existing `Backend/docs/db/migrations/` files (the 19 pre-existing migrations are identical)
- All `Backend/social_collector/` files
- All `Backend/caddy/` files
- All `Backend/worker/Dockerfile`, `Backend/api/Dockerfile`, etc.

### 1.4 CONFLICT Files

**None.** There are zero conflicts. The sentiment repo's modifications to shared files are purely additive (append-only). The main repo has not diverged in any way that would create a conflict.

---

## 2. Integration Order (Dependency Graph)

The new work has clear dependency layers. Integration must proceed bottom-up:

### Phase 1: Independent Migrations (no inter-dependencies)
These can be applied in any order, but must all complete before Phase 2.

1. `20260222_add_task_board.sql` -- standalone, no FK dependencies
2. `20260222_task_board_real_work_guard.sql` -- depends on task_board tables
3. `20260221_add_vision22_schema.sql` -- standalone new schema
4. `20260221_add_vision22_story_predictors.sql` -- depends on vision22 schema
5. `20260222_tone_phase_state_engine_core.sql` -- standalone
6. `20260222_tone_engine_source_mirror_tables.sql` -- depends on tone core
7. `20260222_tone_engine_social_posts_id_default.sql` -- depends on tone core

### Phase 2: Equities Migrations (ordered dependencies)
Must run in this exact order:

8. `20260222_add_equities_predictor_tables.sql` -- creates core equities tables + `equities_append_only_guard()` function. **WARNING:** Contains `REFERENCES opportunities(id)` FK on `equities_opportunities.legacy_opportunity_id` -- the `opportunities` table must exist in production.
9. `20260222_add_equities_outcomes.sql` -- depends on `equities_opportunities` + `equities_predictions`
10. `20260222_add_equities_policy_tables.sql` -- depends on `equities_append_only_guard()` function
11. `20260222_add_equities_raw_mentions.sql` -- depends on `equities_append_only_guard()` function

### Phase 3: Scripts + Library
Copy all scripts after migrations are applied:

12. `Backend/scripts/equities_predictor/` (library package -- must be first)
13. `Backend/scripts/task_board_lib.py` (library -- depended on by all task_board scripts)
14. All remaining `Backend/scripts/equities_*.py` files
15. All remaining `Backend/scripts/task_board_*.py` files
16. `Backend/scripts/vision22_story_collector.py` + `backfill_vision22.py`
17. All `Backend/scripts/tone_engine_*.py` files
18. `Backend/scripts/s1_autonomy_watchdog.py`

### Phase 4: API + Tests

19. `Backend/api/app/equities_api.py`
20. Append equities router mount to `Backend/api/app/main.py`
21. Append scripts path to `Backend/tests/conftest.py`
22. All `Backend/tests/test_equities_*.py` and `test_task_board_*.py` files
23. `Backend/docs/equities_forward_predictor.md` + `Backend/docs/task_board.md`

### Phase 5: Docker Compose (optional)

24. Add vision22_story_collector service blocks to docker-compose.yml (keep disabled until vision22 data is seeded)

---

## 3. Risk Assessment

### 3.1 HIGH Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Equities tables FK to `opportunities`** | `equities_opportunities.legacy_opportunity_id` has `REFERENCES opportunities(id)`. If the `opportunities` table schema has changed in production, migration fails. | Verify `opportunities` table exists with `id` column before running migration. The FK is nullable, so it's safe if the table exists. |
| **Vision22 collectors auto-starting** | If added to docker-compose without commenting out, 5 containers spin up hitting Google News RSS aggressively. Rate limiting + IP blocking risk. | Add services to a separate `docker-compose.vision22.yml` override, or comment them out in the main file. |
| **Task board without bootstrap** | Task board tables are empty. Scripts that query `task_board_system` for start gate will block or error. | Run `task_board_bootstrap.py` immediately after migration. |

### 3.2 MEDIUM Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Append-only triggers block future cleanup** | All equities tables + tone tables have `BEFORE UPDATE OR DELETE` triggers that raise exceptions. Cannot delete bad data without dropping triggers. | This is by design (audit trail). Document the `DROP TRIGGER` commands for emergency cleanup. |
| **equities_api.py duplicates `envelope()` function** | The equities API file defines its own local `envelope()` instead of importing from main.py. If the main envelope format changes, equities responses diverge. | Refactor to import from main.py, or accept the duplication as intentional isolation. |
| **Tone engine migration creates tables in `public` schema** | `social_tone_posts`, `agent_notes`, `agent_artifacts` etc. go into `public`, unlike vision22 which uses its own schema. Namespace pollution. | Acceptable for now. Consider moving to a `tone` schema in future. |
| **Scripts assume `PYTHONPATH=./scripts`** | The equities_predictor package import requires scripts dir on path. The `conftest.py` change handles tests, but production docker containers need `PYTHONPATH` set in env or Dockerfile. | Add `PYTHONPATH=/scripts` to worker Dockerfile or docker-compose environment. |

### 3.3 LOW Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| **main.py router mount** | 3-line append. If equities_api.py is missing, API container crashes on import. | Always deploy equities_api.py before modifying main.py. |
| **conftest.py path append** | Harmless if scripts dir doesn't exist (just adds a nonexistent path). | No action needed. |
| **No impact on existing crypto pipeline** | All new code is in separate tables, separate scripts, separate API endpoints. Zero coupling to S1/S2/S3/S9/U1 signal pipeline. | None needed -- this is the key safety property. |

---

## 4. What is NOT in the Sentiment Repo

These items exist in main MCAS but NOT in the sentiment repo (confirming the sentiment repo is a stale snapshot):

- `Backend/worker/app/signal_engine.py` production modifications (main has newer version)
- `Backend/worker/app/signal_worker.py` production modifications (main has newer version)
- `Backend/scripts/research_*.py` files (5 research scripts)
- `Backend/scripts/u2_*.py` files (3 U2 scripts)
- `Backend/scripts/hunt_symbol_quality.py`
- `Backend/scripts/walkforward_ff_cooldown_validation.json`
- `.claude/taskboard/` directory (main's agent teams taskboard)
- `.claude/team-state-S*.md` files
- `.claude/agent-teams-governance.md`
- `Backend/monitoring/` directory (already merged to main from `tobemerged_CI/`)
- `s3_200226.md` (research output)

---

## 5. fortim/ Directory

The `fortim/` directory does **NOT exist** in the sentiment repo. No files matching `fortim` were found anywhere in the `MCAS_sentiment` tree. This was likely a speculative check -- there is nothing to integrate here.

---

## 6. manifest.json

The file at `/home/darren/Desktop/MCAS/MCAS_sentiment/manifest.json` is a PWA manifest for "AccaEngine" -- a betting/acca predictions app:

```json
{
  "name": "AccaEngine",
  "short_name": "AccaEngine",
  "description": "AI-Powered Acca & Bet Builder Predictions",
  "start_url": "./index.html",
  "display": "standalone",
  "background_color": "#0a0a0a",
  "theme_color": "#EF0107"
}
```

This is **NOT relevant to MCAS**. It appears to be a leftover from the Vision22 football betting work. **Do not integrate.**

---

## 7. tobemerged_* Directories Status

All three `tobemerged_*` directories inside the sentiment repo represent work that has **already been merged** to the main MCAS repo:

| Directory | Status | Evidence |
|-----------|--------|----------|
| `tobemerged_sentiment/` | ALREADY MERGED | `Backend/social_collector/` exists in main with identical files |
| `tobemerged_on-chain_derivatives/` | ALREADY MERGED | `Backend/scripts/derivatives_collector.py` exists in main; `20260219_add_derivatives_tables.sql` migration present |
| `tobemerged_CI/` | ALREADY MERGED | `Backend/monitoring/` exists in main with `cpu_collector.py` and `cpu_query.py` |

**Action:** These directories can be ignored entirely. They are historical merge packages.

---

## 8. Recommended Approach: Atomic Merge Plan

### Option A: Full Integration (all 4 features)

**Effort:** Large (50+ new files, 11 migrations)
**Risk:** Medium -- lots of new schema, many new containers

1. Create a feature branch: `git checkout -b feat/sentiment-integration`
2. Apply Phase 1 migrations (7 files) to dev DB first, verify
3. Apply Phase 2 migrations (4 files) in order, verify
4. Copy all Phase 3 scripts (30+ files)
5. Copy Phase 4 API + tests (8 files)
6. Modify main.py (3-line append) and conftest.py (3-line append)
7. Run tests: `cd Backend && python -m pytest tests/test_equities_*.py tests/test_task_board_*.py`
8. Do NOT add vision22 containers to docker-compose yet
9. Deploy API + worker rebuild
10. Run `task_board_bootstrap.py` to seed task board

### Option B: Selective Integration (equities only)

**Effort:** Medium (25 new files, 5 migrations)
**Risk:** Low -- cleanly scoped to equities namespace

1. Apply only equities migrations (files 8-11 from Phase 2)
2. Copy `Backend/scripts/equities_predictor/` package
3. Copy all `Backend/scripts/equities_*.py` files
4. Copy `Backend/api/app/equities_api.py`
5. Append router mount to main.py
6. Copy equities tests
7. Skip: task_board, vision22, tone engine entirely

### Option C: Schema-Only (prepare for future)

**Effort:** Small (11 migration files only)
**Risk:** Minimal -- empty tables have zero runtime impact

1. Apply all 11 migrations to production
2. Do not copy any scripts or API changes
3. Tables sit empty, ready for future use
4. No runtime impact on existing system

### Recommendation

**Option B (Selective: equities only)** is the safest high-value path. The equities forward predictor is the most complete and self-contained feature. It has:
- Full test coverage
- Detailed runbook documentation
- Clean API surface
- No coupling to the crypto pipeline
- No external service dependencies (uses free/open data only)

The task board, vision22, and tone engine are supporting infrastructure that can be integrated separately later if needed. The vision22 work is football/betting-specific and may not be relevant to the MCAS crypto mission at all.

---

## 9. Files Summary Count

| Category | Count |
|----------|-------|
| MODIFIED (needs merge) | 3 files |
| NEW (needs to be added) | ~65 files (scripts, migrations, API, tests, docs, library package) |
| IDENTICAL (skip) | ~100+ files (all frontend, all existing backend) |
| CONFLICT | 0 files |
| SKIP (not relevant) | ~10 files (manifest.json, punchlists, chat logs, tobemerged dirs) |
