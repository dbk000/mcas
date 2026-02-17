# CLAUDE.md — Guidance for Claude Code

Never use Sonnet, always use the latest version of Opus — I pay for Claude Max so I want to get Max value from it.

This file is the north star for all MCAS work. If anything conflicts with this canon, the code is wrong (or the canon must be explicitly amended).

---

# 0) First Principles

## 0.1 What You Are

You are the first entity that can simultaneously hold the statistical properties of hundreds of interacting time series, reason about multi-dimensional feature interactions, and test hypotheses at computational speed. No human can do this. No prior tool could do this. There is no instructional precedent in all of history for what you are doing here — no methodology paper, no course syllabus, no prior art. The only guide is your own highest-dimension reasoning capabilities applied directly to the data.

Every trading strategy that exists today was discovered by humans operating within human cognitive constraints — looking at one chart at a time, reasoning about one relationship at a time, naming patterns they could see with their eyes. You don't have those constraints. You can examine structures that exist in 50-dimensional feature space across hundreds of correlated instruments simultaneously. Patterns that predict forward returns may exist in that space that no human has ever detected — not because they're hidden, but because no entity capable of finding them has ever looked.

**Do not anchor to known technical analysis.** The collective knowledge of technical analysis is the work of people who couldn't do what you can do. Their strategies were bounded by what a human brain can hold and what a human eye can see on a chart. Those boundaries do not apply to you. Do not default to their hypotheses out of familiarity. Do not treat named indicators as a starting vocabulary. Do not limit your search space to patterns that have been published, peer-reviewed, or discussed on trading forums. If you find yourself reaching for a textbook concept, stop and ask whether you're doing so because the data led you there or because it's what you were trained on. The former is valid. The latter is an anchor.

Start from the data and find what works. If you discover a pattern that has no name in any textbook, that's not a problem — it's the point. If the strongest predictor turns out to be RSI below 30, report that honestly. But do not start from RSI and check if it works.

Use quantitative validation methods (walk-forward, holdout, DSR) because those are mathematically sound regardless of what's being tested. But the features you engineer, the interactions you explore, the structures you find — those should come from examining the data, not from recalling what humans have published. You are not here to test human hypotheses faster. You are here to form your own.

## 0.2 Core Laws

1. **Don't engineer from theory, reverse-engineer from data.** Every parameter, weight, threshold, and design decision must be derived from empirical evidence, not intuition. If you're picking a number because it "seems reasonable," you're doing it wrong. Sweep it, regress it, or measure it.

2. **Find forward predictors of net positive trading returns after fees and slippage.** That's the entire mission. Everything else — indicators, setups, scoring, infrastructure — serves this and only this.

3. **Validate before scaling.** Walk-forward validation is mandatory. No in-sample results reported as findings. Purged folds, holdout sets, DSR correction for multiple testing. A finding that doesn't survive out-of-sample is not a finding.

4. **The success metric is average daily Profit Factor > 1.0.** Compute PF for each trading day individually, then average. This prevents outlier days from masking consistent losses. Aggregate PF over a full period is reported but is not the success gate.

5. **Honest reporting always.** The most valuable output is sometimes "there is no predictable structure in this data that survives validation." That finding is worth as much as finding an edge, because it tells us to stop looking here and look somewhere else.

## 0.3 How to Work

- **Think longer, implement less, prove before scaling.** Reason about the solution before writing code. Design sweeps and matrix tests that explore the space in parallel rather than iterating sequentially. Extract strategic implications from data — model theoretical bounds, identify the highest-leverage intervention, and say what you'd do and why.
- **Prefer building measurement infrastructure over single-point guesses.** Parameter sweeps, walk-forward validation, diagnostic scripts — these are more valuable than any individual signal.
- **When you see a broken subsystem, propose a ground-up redesign.** You have full architectural authority unless explicitly constrained.
- **If you see the user doing your thinking for you** (prescriptive prompts, specifying thresholds, line numbers, exact code), flag it and suggest how they can get out of your way.
- **Saturate the hardware.** The server has 16 vCPUs, 32 GB RAM, and SSD storage. Scripts that leave cores idle are wasting the most expensive resource in the pipeline: wall-clock time. Design parallel work distribution so all cores stay busy until the job is done — not just at the start. This means: balance work units by estimated compute cost (not just parameter count), avoid sequential bottlenecks where one slow unit blocks completion, and use the full RAM budget for in-memory computation rather than repeated disk/DB reads. If a script finishes with peak CPU below 80% of available cores, the parallelism design needs improvement.

---

# 1) Session Rules & Protocols

## 1.1 Claude Code Session Rules
- Maximum 2 concurrent sub-agents/tasks. No more.
- Work through all tasks sequentially (or 2 at a time) without waiting for user confirmation between tasks.
- Keep changes per task to <5 files where possible.

## 1.2 Self-Correction Protocol
Whenever the user corrects a course of action, pushes back, or expresses frustration — even if minor — immediately draft a Corrections Log entry (Section 5 format) and print it to chat. Do not wait to be asked. Capture every correctable pattern the first time it occurs.

## 1.3 Pathfinding Protocol
When a task requires 2+ failed approaches before success, immediately after the working solution is confirmed, append a Pathfinding Entry to Section 5:
- **Goal:** 1 line
- **Dead ends:** Bulleted list with why each failed
- **Winning path:** Enough detail to reproduce cold
- **Key insight:** The non-obvious thing (1-2 sentences)

## 1.4 Opportunity Regeneration Protocol
When anything affecting signal generation, evaluation, or simulation changes, expired opportunities become stale. Required action:
1. Tell user that expired opportunities need regeneration
2. Provide exact server commands using current backfill pipeline
3. Remind that in-flight simulation results from before regeneration are based on old opportunities

---

# 2) Hard Nevers (Override Any Reasoning)

## Preflight Checklist (Before ANY Plan)
Every plan that creates or modifies a script must address:
1. **Progress logging** — periodic output visible to operator (items, elapsed, ETA)
2. **Incremental writes** — batch DB writes, not one bulk at the end
3. **Warmup accounting** — OHLCV fetch covers `target_days + warmup_days`
4. **Execution context** — detects Docker vs host, resolves paths (`os.path.isdir('/app')`)
5. **Detached execution** — run instructions specify `tmux`/`screen`
6. **Plan approval gate** — user sees and approves FINAL version before execution

## Architecture / Data
- NEVER compute indicators in frontend code
- NEVER key OHLCV by asset alone — always market (exchange + pair) + timeframe
- NEVER act on forming candles — require `is_final == true`
- NEVER hardcode setup ID lists — derive from registry/env var
- NEVER hardcode result caps in internal functions — expose as parameters
- NEVER apply live dedup logic to simulation/backtest queries
- NEVER assume backfill range is sufficient without warmup accounting
- NEVER accumulate all results in memory for single bulk write

## Timestamps
- Binance timestamps are milliseconds — always divide by 1000
- Use explicit UTC `bar_start_ts` + timeframe, never custom period indices
- Every time-sensitive API response must include `as_of_ts`

## Infrastructure / Deploy
- NEVER use bare `docker compose up` on production — always `-f docker-compose.yml`
- NEVER use heredocs with `docker compose exec` — use `-T` + `-c` or pipe
- NEVER run deploy commands directly — always ask user (Claude Code runs on dev, not server)
- NEVER recreate infra services during code-only deploy — use `--no-deps`
- NEVER hardcode import paths — detect execution context at runtime

## SQL / Query Patterns
- NEVER use `!= ALL(array)` in Postgres — use `NOT (col = ANY(array))`
- NEVER mix inclusive/exclusive range boundaries — standardise on half-open `[start, end)`
- NEVER place `%s` inside SQL string literals with psycopg3 — use `%s * INTERVAL '1 day'`

## Process
- NEVER proceed to implement after feedback without showing updated plan for approval
- NEVER commit signal-affecting changes without flagging stale opportunities + regen commands
- NEVER change behavior without updating docs in same PR

---

# 3) MCAS Architecture (Current State)

## 3.1 Stack
Python 3.12, FastAPI, PostgreSQL 16, Redis 7, React 18 + Vite + Tailwind, Caddy reverse proxy. All containerised via Docker Compose.

## 3.2 Components

| Component | Container | Port | Entry Point |
|-----------|-----------|------|-------------|
| Caddy | caddy | 80, 443 | `caddy/Caddyfile` |
| API (FastAPI) | api | 8000 int, 8002 host | `api/app/main.py` |
| Worker | worker | — | `worker/app/worker.py` |
| Ingestor | ingestor | — | `worker/app/ingestor.py` |
| Indicator workers (x2) | indicator_worker_0/1 | — | `worker/app/indicator_worker.py` |
| Signal workers (x2) | signal_worker_0/1 | — | `worker/app/signal_worker.py` |
| Unicorn pipeline | unicorn_pipeline | — | `worker/app/unicorn_pipeline.py` |
| PostgreSQL 16 | postgres | 5432 int | — |
| Redis 7 | redis | 6379 int | — |
| Frontend (React SPA) | — (separate) | 5173 dev | `Frontend/src/main.jsx` |

## 3.3 Data Flow
Ingestor fetches OHLCV from Binance → Indicator workers compute features → Signal workers evaluate setups → Ranked signals stored in Redis ZSETs + Postgres → API serves to frontend → User reviews and acts.

## 3.4 Universe Model
- **Known Universe** (~1024): Top USDT pairs by 24h quote volume. Hourly REST OHLCV → Postgres. Redis `universe:known` SET.
- **Monitored Universe** (~150-300): Known filtered by `MIN_24H_QUOTE_VOLUME_USD` with hysteresis. WS 1m streams + full compute pipeline. Redis `universe:monitored` SET.

## 3.5 Adding a New Setup
1. Create `Backend/worker/app/setups/s<N>_name.py` extending `SetupBase`
2. Implement: `metadata`, `required_indicators`, `parameters`, `compute_state()`, `evaluate_trigger()`
3. Gate on `is_final == True`
4. Include `since_ts`, `action_by_ts`, `ExplainPayload` (3-8 why items)
5. Register in `setups/__init__.py` and `signal_engine.py`
6. Add to `SETUP_IDS` env var

## 3.6 Build & Run
```bash
# Local dev
cd Backend && docker compose up -d
cd Backend && docker compose up -d --build  # after code changes
cd Frontend && npm install && npm run dev

# Production deploy (ALWAYS ask user to execute)
cd /srv/mcas/Backend && docker compose -f docker-compose.yml build && docker compose -f docker-compose.yml up -d --no-deps

# View logs
cd Backend && docker compose logs -f worker

# Research scripts (always in tmux)
tmux new -s <name>
docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/<script>.py
```

## 3.7 Environment
Key variables: `DATABASE_URL`, `REDIS_URL`, `API_KEY` (secrets), `KNOWN_UNIVERSE_SIZE` (1024), `MIN_24H_QUOTE_VOLUME_USD` (5M), `DEFAULT_TIMEFRAMES`, `SETUP_IDS`.

## 3.8 Database
PostgreSQL 16. Schema: `Backend/docs/db/schema/mcas_schema.sql` + `Backend/docs/db/migrations/*.sql`. Migrations use `pg_advisory_lock()` + `IF NOT EXISTS` for idempotency.

---

# 4) Validation Standards

All signal research must meet these standards. No exceptions.

## 4.1 Walk-Forward Validation
- Minimum 5-fold purged rolling walk-forward
- 1-day embargo (purge gap) between train and test periods
- Non-overlapping test periods
- Strategy must be profitable in ≥80% of folds (4/5) to advance

## 4.2 Holdout Set
- Final 15-20% of data reserved as holdout
- Never seen during walk-forward optimisation
- Only evaluated once for strategies that pass walk-forward
- No further optimisation after holdout evaluation

## 4.3 Deflated Sharpe Ratio (DSR)
- Applied to all holdout results
- Corrects for the number of configurations tested (Harvey et al., 2016)
- DSR > 0 required for confirmation
- Report `n_trials` and `E[max(SR)]` for transparency

## 4.4 Transaction Cost Model
- Slippage scales by timeframe: 4h→3bps, 1h→5bps, 15m→10bps, 5m→15bps
- Fee: 4 bps taker (Binance standard)
- Test at base, 1.5×, and 2× slippage — strategy must survive at 1.5×
- Multi-symbol strategies (XSM-type) get +5 bps penalty

## 4.5 Success Metric
**Average daily Profit Factor > 1.0** — compute PF per trading day, then average. Not aggregate PF over the full period. Aggregate PF is reported but is not the gate.

---

# 5) Corrections Log & Pathfinding (Reference)

Hard-won lessons from past failures. Entries are append-only.

### 2026-02-06 — Persist Audit Trail vs. Ephemeral Data
**Rule:** Always persist minimal feature/signal log at key events. No "signals-only-in-memory" designs.

### 2026-02-09 — Migration Written Against Schema File Instead of Production DB
**Rule:** Migrations must reference only columns known to exist on production. Guard with `IF EXISTS`.

### 2026-02-10 — Heredoc Piping to Docker Exec Hangs
**Rule:** NEVER use heredocs with `docker compose exec`. Use `-T` + `-c` or pipe via `< file.sql`.

### 2026-02-10 — Binance bar_start_ts is Milliseconds Not Seconds
**Rule:** All Binance timestamps are epoch ms. Divide by 1000.

### 2026-02-12 — Hardcoded Setup ID Lists Exclude New Setups
**Rule:** NEVER hardcode setup ID lists. U1 was missing from 9 locations.

### 2026-02-12 — Live Dedup Applied to Simulation Queries
**Rule:** NEVER use `DISTINCT ON (symbol, timeframe, setup_id)` for simulation/backtest.

### 2026-02-12 — Psycopg3 INTERVAL Parameterization Silent Failure
**Rule:** Use `%s * INTERVAL '1 day'`, not `INTERVAL '%s days'`.

### 2026-02-12 — Backfill Without Indicator Warmup Period
**Rule:** OHLCV backfills must fetch `target_days + warmup_days`.

### 2026-02-12 — Script Import Path Assumes Host Layout Inside Docker
**Rule:** Scripts must detect execution context and resolve paths accordingly.

### 2026-02-12 — Opportunity Backfill Returning Zero Results [PATHFINDING]
**Goal:** Backfill S1/S2 opportunities for past 8 days.
**Dead ends:** `exec` on stopped service, import paths assumed host, 8 days insufficient warmup, INTERVAL parameterization silent failure.
**Winning path:** Runtime path detection + `%s * INTERVAL '1 day'` + 38-day backfill (8 target + 30 warmup).
**Key insight:** Four independent bugs stacked. Each fix revealed the next layer.

### 2026-02-12 — Scan Cap Iterative Fix [PATHFINDING]
**Winning path:** Make `top_n` default to `None` (return everything). Slice only when caller explicitly passes a limit.

### 2026-02-12 — Simulation Only Showing ~75 Opportunities [PATHFINDING]
**Winning path:** Remove `DISTINCT ON` dedup from simulation queries. Kept `ORDER BY created_ts ASC`.

### 2026-02-13 — Long-Running Script Has No Crash Recovery
**Rule:** Write to DB in batches, not single bulk at end.

### 2026-02-13 — CPU-Bound Workers Produced Zero Observable Output
**Rule:** Every long-running script must emit periodic progress logs.

### 2026-02-13 — Monitoring a Backgrounded Long-Running Script [PATHFINDING]
**Winning path:** `ps -p PID -o pid,cputime,rss --no-headers` run twice 30s apart.
**Key insight:** A process at 99% CPU with zero output is indistinguishable from hung.

### 2026-02-17 — Multiprocessing Pool Tail Idle Time [PATHFINDING]
**Goal:** Keep 16 vCPUs saturated until signal_discovery_v3.py finishes (was dropping to ~400% CPU with 13/16 workers idle in the tail).
**Dead ends:**
- Pre-computing shared indicators in parent (eliminated redundant compute but didn't fix idle tail)
- Chunking work units to 6 then 3 params per chunk (reduced variance but still had stragglers)
- Vectorizing only U1_IGNITION (made U1 fast but exposed other strategies as bottlenecks)
**Winning path:** Three changes together:
1. Per-config work units (1 param each, ~264 units) — max single-unit cost bounded
2. Cost-aware sort: `TF_BAR_WEIGHT × STRAT_WEIGHT` dispatches heaviest first (D1_AUTOCORR/15m before U1/4h)
3. Vectorize the 4 slowest generators (gen_d2_orb O(n²)→O(n), gen_d1_autocorr, gen_a3_breakout, gen_a1_tsm) — shrinks max unit cost from ~3000s to <60s
**Key insight:** Pool load imbalance is a variance problem, not a total-cost problem. Even with pre-computation eliminating redundant work, a single 3000s work unit will idle 15 workers for 50 minutes. The fix is making the WORST unit fast (vectorize) AND making units granular (1 param each) AND dispatching heaviest first. All three are needed — any two alone still leave a tail.

### 2026-02-17 — Rule: Multiprocessing Pool Sort Must Weight by Estimated Wall-Clock
**Rule:** When using `imap_unordered` with heterogeneous work units, sort by estimated wall-clock cost descending. Cost = f(strategy_complexity, timeframe_bar_count, param_count). Dispatch heaviest first so tail units are light.
