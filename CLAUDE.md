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

6. **Before any --clear-expired backfill run, snapshot existing results to /scripts/output/snapshots/{setup_version}_{date}/.

7. **Record nodal atomic influence on every experiment.** Every simulation, backfill, ablation, or sweep must decompose its results into per-node contributions and write them to `meta.node_impact`. Each pipeline node involved in an experiment gets a record of its quantified marginal impact — filter rates, metric slices by threshold, sensitivity analysis. Before running a new experiment, check existing `node_impact` records first: if the question can be answered by scanning past nodal influence (e.g. "what happens if I tighten dormancy from 0.75 to 0.90?"), use the existing data instead of re-running the full pipeline. Never compute 10,000 factorial to get 20 numbers when those 20 numbers are already recorded atomically at the node level.

## 0.3 How to Work

- **Think longer, implement less, prove before scaling.** Reason about the solution before writing code. Design sweeps and matrix tests that explore the space in parallel rather than iterating sequentially. Extract strategic implications from data — model theoretical bounds, identify the highest-leverage intervention, and say what you'd do and why.
- **Prefer building measurement infrastructure over single-point guesses.** Parameter sweeps, walk-forward validation, diagnostic scripts — these are more valuable than any individual signal.
- **When you see a broken subsystem, propose a ground-up redesign.** You have full architectural authority unless explicitly constrained.
- **If you see the user doing your thinking for you** (prescriptive prompts, specifying thresholds, line numbers, exact code), flag it and suggest how they can get out of your way.
- **Saturate the hardware.** The server has 16 vCPUs, 32 GB RAM, and SSD storage. Scripts that leave cores idle are wasting the most expensive resource in the pipeline: wall-clock time. Design parallel work distribution so all cores stay busy until the job is done — not just at the start. This means: balance work units by estimated compute cost (not just parameter count), avoid sequential bottlenecks where one slow unit blocks completion, and use the full RAM budget for in-memory computation rather than repeated disk/DB reads. If a script finishes with peak CPU below 80% of available cores, the parallelism design needs improvement.

---

# 1) Session Rules & Protocols

## 1.1 Claude Code Session Rules
- Maximum 6 concurrent sub-agents/tasks. No more. Agents must write findings to disk and share filepath to avoid busting chat context.
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

## 1.5 Production Database & Deploy Access
Dev and production run on the same machine (65.109.7.49). No SSH needed — all commands run locally. The dev workspace is at `/home/darren/Desktop/MCAS`, production is at `/srv/mcas`. Docker provides permission isolation between the two.

**Ad-hoc SQL queries:**
```bash
docker exec backend-postgres-1 psql -U trading -d trading -c "SQL HERE"
```

**Running scripts against production DB:**
```bash
docker exec backend-worker-1 python3 /scripts/my_script.py --args
```
Scripts write output to `/scripts/` inside the container, which is bind-mounted to `/srv/mcas/Backend/scripts/` on the host — read results directly from there.

For heavy/long-running scripts that shouldn't compete with the live worker, spin up a fresh container instead:
```bash
cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/my_script.py
```

**Deploy workflow (no SSH):**
```bash
# 1. Pull in production path
cd /srv/mcas && git pull origin main
# 2. Build & restart only changed services (never bare `up`)
cd /srv/mcas/Backend && docker compose -f docker-compose.yml build <service> && docker compose -f docker-compose.yml up -d --no-deps <service>
# 3. Frontend rebuild
cd /srv/mcas/Frontend && npm install && npm run build
```

**Container names:**
- `backend-postgres-1` / `backend-worker-1` = production
- `mcas-dev-postgres-1` / `mcas-dev-worker-1` = dev
- Do not confuse them.

**DB Migrations — ALWAYS dev first, then production:**
Every migration runs on dev first (catch bugs cheap against the 102GB prod DB), verify, then production. If you catch yourself manually patching dev to match prod, the process broke.
```bash
# Step 1: Dev (catch bugs cheap)
cat docs/db/migrations/XXXX.sql | docker exec -i mcas-dev-postgres-1 psql -U trading -d trading
# Step 2: Verify — run a query that exercises the migration
# Step 3: Production (deploy verified schema)
cat docs/db/migrations/XXXX.sql | docker exec -i backend-postgres-1 psql -U trading -d trading
```
For changes the frontend depends on: run prod migration BEFORE pushing the code that queries new schema. Deploy sequence: dev migration → verify → prod migration → push code.

**Deploy checklist — EVERYTHING targets production:**
There is no staging environment. After every commit+push that touches backend code:
1. DB migrations: dev first, verify, then production (see above)
2. Frontend: automatic via CF Pages (`git push origin main`)
3. Backend API: `cd /srv/mcas && git pull origin main` then `docker compose -f docker-compose.yml build api && docker compose -f docker-compose.yml up -d --no-deps api`
4. NEVER deploy only to dev containers (`mcas-dev-*`) and call it done — the live site won't see the changes

## 1.6 Resource Awareness & Agent Teams Protocol

### CPU Monitoring
The VPS runs a CPU metrics collector (`cpu_collector`) writing to Redis every 5 seconds. Query via:
```bash
docker exec mcas-cpu-collector python cpu_query.py <command>
```
Commands: `brief` (one-liner), `now` (full snapshot), `summary` (rolling stats), `containers` (per-container), `hotspots` (imbalance), `history <secs>` (time series), `health` (liveness), `alerts` (threshold state).

**Mandatory**: Check `brief` before any script >60s. Check every 2-3 min during long runs. Log `history` after completion.

**Interpretation**: usage <40% on multi-core = single-threaded, refactor. One core pinned = GIL-bound. steal >5% = hypervisor throttle. iowait >20% = I/O bound. load > 2x cores = oversubscribed.

### Agent Teams (Mesh Architecture)
Up to 3 Claude Code sessions run simultaneously, each spawning 2 sub-agents (9 Claudes total). All follow `.claude/agent-teams-governance.md`. Key rules:
- **All substance on disk.** Chat carries pointers and summaries only.
- **Cross-session coordination via `.claude/taskboard/`.** Leads post, claim, and complete task files. Sub-agents only talk to their own lead.
- **Check-ins under 50 tokens.** STATUS, RESULT (one line), OUTPUT (path), READY.
- **Lead never runs heavy scripts.** Lead assigns, reads files, synthesizes, manages taskboard.
- **File ownership via SCOPE.** Only write where assigned. Shared files are lead-only. Output paths are session-namespaced.
- **Max 2 heavy scripts concurrently across all sessions.** Leads check each other's team-state files to stagger compute.
- **Recovery via `.claude/team-state-S{N}.md`.** Each lead maintains its own session state file.

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
7. **Persistent output** — every script must write its results to a file (e.g. `scripts/output/{script_name}_{timestamp}.{ext}`). No script should ever run without a built-in "output records to location X" pathway. If a script produces findings, metrics, or confirmations, those must survive the process — console-only output that vanishes when the terminal closes or Docker container is removed is not acceptable. The output path must be logged at script start.
8. **Parallelism design** — before any script is approved for execution, answer these five questions in the plan:

   **a) What is the compute profile?** List every phase of the script with estimated wall-clock time and whether it's single-threaded, multi-threaded (BLAS), or multi-process. Example: "Phase 2: 8 min, categories A-G single-threaded, H-M 6-worker Pool."

   **b) What percentage of total compute is parallelised?** If <50% of estimated wall time runs on multiple cores, redesign before running. Amdahl's law: 16 cores can't help if 60% of the work is serial.

   **c) Are work units balanced by cost?** If the heaviest work unit takes >3× the lightest, it will create a tail. Either split heavy units into smaller chunks, or sort heaviest-first so the tail is composed of cheap units. Report: "heaviest unit: ~X seconds, lightest: ~Y seconds, ratio: Z×."

   **d) Does utilisation drop below 50% before the script is 90% complete?** If workers finish and go idle with significant work remaining on other workers, the work distribution needs rebalancing. Design for flat CPU utilisation, not a triangle that peaks early and tapers.

   **e) What is the expected CPU utilisation curve?** Describe in one sentence: "~80% for first 15 min, tapering to ~50% for final 5 min" is acceptable. "~90% for 5 min then ~20% for 25 min" is not — that's a serial script with a parallel preamble.

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
- NEVER deploy without user approval — dev and prod share the same machine but are separate git worktrees
- NEVER recreate infra services during code-only deploy — use `--no-deps`
- NEVER hardcode import paths — detect execution context at runtime
- NEVER run DB migrations on prod only — always run on dev (`mcas-dev-postgres-1`) first, verify, then production. Schema drift from skipping dev causes manual patching later

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
| CPU collector | mcas-cpu-collector | — | `monitoring/cpu_collector.py` |
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
7. **"Finished" means:** the end user can run an expired opportunities simulation in the UI on all expired opps for that strategy and the output matches the configs for the winning version of the strat. This means: D1Config defaults = winning config (e.g. Config D's 1.5% stop), backfill opportunities have correct price levels baked in, strategy appears in STRATEGY_REGISTRY, and `SETUP_IDS` includes it in the worker.

## 3.6 Build & Run
```bash
# Local dev
cd Backend && docker compose up -d
cd Backend && docker compose up -d --build  # after code changes
cd Frontend && npm install && npm run dev

# Backend production deploy (same machine, no SSH — get user approval first)
cd /srv/mcas && git pull origin main
cd /srv/mcas/Backend && docker compose -f docker-compose.yml build <service> && docker compose -f docker-compose.yml up -d --no-deps <service>

# Frontend production deploy — via Cloudflare Pages (auto-deploys from GitHub)
# Just push to main — CF builds automatically. Do NOT run npm build on the VPS.
git push origin main
# Check deploy status:
./scripts/cf-deploy.sh --status
# Or via API directly:
curl -s 'https://api.cloudflare.com/client/v4/accounts/0f25318d292c7b6fcd1eb5502ff85b8a/pages/projects/mcas/deployments' \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" | python3 -m json.tool

# View logs
cd Backend && docker compose logs -f worker

# Research scripts (always in tmux)
tmux new -s <name>
docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/<script>.py
```

**Frontend hosting:** Cloudflare Pages project `mcas`. Domains: `mcasui.com`, `mcas.pages.dev`. Auto-deploys from `main` branch of `dbk000/mcas`. Build: `npm ci && npm run build` in `Frontend/` dir. CF API token stored at `/home/darren/.env.cloudflare` and in `~/.bashrc`. Deploy helper: `scripts/cf-deploy.sh`.

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
                                                                                                                                                                   
### 2026-02-18 — Anchored to existing template instead of reasoning from first principles
  Rule: When the plan says "unlimited latitude in methodology," do not copy-paste the existing pipeline with a different target variable. The methodology itself must be derived from the     
  problem structure. Opus-level work means the architecture is novel, not just the features.

### 2026-02-18 — Nested multiprocessing.Pool Crashes with Daemonic Process Assertion                                                                                                            
What happened: _process_fold ran inside Pool(12) workers and called generate_features_round1 (Pool(5)) and screen_features (Pool(8)). Crashed with AssertionError: daemonic processes are   
not allowed to have children.                           
Mechanics: multiprocessing.Pool sets all worker processes to daemon=True so they die if the parent dies (prevents orphan trees). Python enforces that daemon processes cannot call          
Process.start(), which means they cannot spawn child Pools. This is an assertion in multiprocessing/process.py:118, not a catchable exception — it's a hard architectural constraint.
Rule: Any function that spawns a multiprocessing.Pool internally must accept a parallel=False flag (or equivalent) that falls back to serial execution ([f(a) for a in args] instead of
pool.map(f, args)). Any code that runs inside a Pool worker must call such functions with parallel=False. Audit the full call tree of every Pool worker function for nested Pool usage
before running.
Key insight: The danger is indirect nesting. _process_fold didn't directly create a Pool — it called generate_features_round1 which called mp.Pool(5). The nesting was two function calls
deep, invisible from reading _process_fold alone. The correct audit question is: "does anything in this worker's entire call graph spawn a Pool?"

2026-02-18 — Pure Python Loops Over (bars × symbols) Matrices
                                                                                                                                                                                              
  What happened: generate_signals looped for t in bars: for s in symbols: score_bar(model, features, t, s) — 400 bars × 500 symbols = 200,000 Python function calls, each doing 16 dict       
  lookups and numpy scalar indexing. Single-threaded, ~3% CPU for 30+ minutes. Same pattern in build_control_group (O(events × all_bars) per symbol) and threshold tuning (O(points ×
  features) scoring loop).

  Mechanics: Python for loops execute at ~10M bytecode ops/sec. Numpy vectorized ops on the same data execute at ~1B elements/sec (100× faster) because the loop runs in C and data stays in
  contiguous memory. A function like np.digitize(feature_matrix, edges) bins the entire (2600, 500) matrix in one call — replacing 1.3M individual np.searchsorted + Python dict lookups with
  a single C-level pass. The cost of the Python loop isn't the arithmetic, it's the per-iteration overhead: function call frames, dict hash lookups, type checking, and — critically —
  breaking numpy's ability to use SIMD/BLAS on contiguous memory.

  Rule: Any operation that touches every (bar, symbol) slot must be a numpy matrix operation, never a Python loop. The pattern is: precompute bin indices / masks / lookups for ALL slots as
  (n_bars, n_symbols) arrays, then combine via numpy arithmetic. If you find yourself writing for t in range(n_bars): for s in range(n_symbols): or for idx, (t, s) in enumerate(points): over
   more than ~1000 iterations, stop and redesign as a matrix op.

  Key insight: The symptom is not "slow code" — it's near-zero CPU utilization on a 16-core machine. A single Python thread doing dict lookups uses ~3% of a 16-vCPU machine. Numpy vectorized
   code on the same data saturates BLAS threads and shows 400-800% CPU. If a compute-heavy phase shows <100% CPU, the bottleneck is Python interpretation overhead, not the math.

2026-02-19 — Tighter exits with low-precision rare-event model
  Rule: For rare-event models with <30% precision, wider exits outperform tighter ones. The rare true positives need room to develop their full MFE. Trails and TPs cap the upside on the few
  winners, which are the only trades that can offset the many losers. Tighter stops also increase turnover (more entries, each with negative EV). The monotonic pattern (wider → better across
   all three exit dimensions) proves this is structural, not a calibration issue.

2026-02-21 — simulation_results contains massive duplication (2-11x per setup)
  Rule: ALL queries against simulation_results must use `DISTINCT ON (opportunity_id) ORDER BY opportunity_id, created_ts DESC` to get the latest run's result per opportunity, or filter by a specific `run_id`. Raw `COUNT(*)` or `SUM(pnl_net)` without deduplication is NEVER valid. The table is a run-log (multiple sim configs × same opportunities), not a trade-log.
  What happened: Multiple simulation runs (auto-runs Feb 13-16 + Phase3/4/5 config variants) each re-simulated the same opportunities. 72,713 rows = 25,219 unique opportunities = 2.88x avg inflation. S1/5m was inflated 11.4x. S2-15m "809 trades" was actually 97 unique (67% from one day). All S2 hunting analyses used raw counts — directional findings survived but magnitudes were ~2.5x overstated.
  Key insight: The duplication was roughly uniform across exit types, so ratios (FF rate, TP1 rate, win rate) and directional comparisons (A > B) were preserved. But absolute PnL, trade counts, and sample size confidence were all inflated. Any finding that relied on "large sample" confidence (like the S2-15m hour filter at "809 trades") collapsed when deduped.

2026-03-01 — Theory-first feature engineering despite data-first instruction
  Rule: When a prompt says "look at the data before designing features," the feature set must be derived from observed patterns, not pre-engineered from theory. Hardcoded dictionaries (MEME_TOKENS, TECH_TOKENS, etc.) and theory-driven categories are anchors. Mechanical string features are acceptable (they make no semantic claim). External metadata (CoinGecko categories) is acceptable because it's empirical data, not theory. Also: don't reimplement simulation logic that already exists — import it.
  What happened: First version of u1_ticker_name_analysis.py pre-engineered 22 features from theory with hardcoded token dictionaries, reimplemented 120 lines of Config A simulator instead of importing from sim_u1v4_matrix, and called a "numerology proxy" (letter_sum) a feature.
  Key insight: The difference between "data first" and "theory first" is whether the feature set was designed before or after looking at the data. Mechanical features (string length, vowel ratio) are data-first because they make no prediction about what matters. Hardcoded MEME_TOKENS = ["DOGE", "SHIB", ...] is theory-first because it assumes meme coins behave differently before checking.