# Lessons Learned

Hard-won operational lessons from building and deploying MCAS. Append-only.

---

## 2026-03-02 — Frontend deploys via Cloudflare Pages, NOT the VPS

**What happened:** Spent time running `npm run build` at `/srv/mcas/Frontend/` assuming the VPS serves the frontend. It doesn't. The frontend is deployed via Cloudflare Pages (auto-deploy from GitHub).

**Root cause:** The Caddyfile only proxies API routes (`/v1/*`, `/health`) to the backend. There is no static file serving config for the frontend. The frontend SPA is hosted on Cloudflare Pages at `mcasui.com` / `mcas.pages.dev`.

**Key facts:**
- Cloudflare Pages project: `mcas`
- Domains: `mcasui.com`, `mcas.pages.dev`
- GitHub repo: `dbk000/mcas`
- Build command: `npm ci && npm run build`
- Build output: `dist`
- Root directory: `Frontend`
- Branch: `main`, automatic deployments enabled
- Account ID: `0f25318d292c7b6fcd1eb5502ff85b8a`

**Rule:** Frontend changes deploy by pushing to `main` on GitHub. Cloudflare Pages auto-builds. Never run `npm run build` on the VPS for production frontend — it does nothing useful.

---

## 2026-03-02 — Cloudflare Pages can miss webhook triggers

**What happened:** Commit `d394338` was pushed to GitHub but Cloudflare Pages didn't trigger a build. The previous commit `cdf9360` (pushed seconds earlier) did build successfully.

**Root cause:** Likely GitHub webhook race condition or CF rate-limiting when commits are pushed in rapid succession.

**Fix:** Push an empty commit (`git commit --allow-empty`) to re-trigger the webhook. Alternatively, use the CF API or dashboard "Retry deployment" to manually trigger.

**Rule:** After pushing frontend changes, check the CF Pages deployments list to confirm a build was triggered. If not, push an empty commit or use the API to trigger manually.

---

## 2026-03-02 — Browser cache serves stale Vite bundles despite new deployment

**What happened:** After deploying a new CF Pages build, the browser continued loading the old JS bundle (`index-DCQz4J4c.js`) instead of the new one (`index-BPso_6Lv.js`). Vite uses content-hashed filenames so the new file should have been fetched automatically.

**Root cause:** Cloudflare CDN cache and/or browser cache of `index.html` which contains the `<script>` tag pointing to the old bundle filename.

**Fix:** Hard refresh (Ctrl+Shift+R), or append `?v=N` cache buster to the URL. For persistent issues, purge the CF cache via API or dashboard.

**Rule:** After deploying, verify the new bundle is being served by checking the network tab for the JS filename hash. If stale, hard refresh or purge CF cache.

---

## 2026-03-02 — Cloudflare dashboard settings page is unreliable

**What happened:** Navigating directly to CF Pages settings URLs (`/settings/builds-deployments`) repeatedly shows "Refresh the page to try again" error.

**Workaround:** Navigate to the project page first (`Workers & Pages > mcas`) then click through to settings from there. Direct URL navigation to settings sub-pages is unreliable.

---

## 2026-03-02 — Fast layout iteration via SVG render sweep (not browser)

**What happened:** Iterating on pipeline node sizing through the browser (edit → deploy to CF Pages → refresh → check) was taking minutes per iteration. Tried using Vite dev server + Chrome but hit CORS issues, password gates, and slow feedback loops.

**Solution:** Built `Frontend/layout-tuner.cjs` — a Node.js script that uses dagre directly (same library as the React Flow pipeline) to compute layout with different parameter combinations, then renders to SVG files. No React, no browser, no Vite — pure dagre → SVG in milliseconds.

**How it works:**
1. Fetches topology data from the API once (`/tmp/topology.json`)
2. Runs dagre layout with N parameter combos (nodeW, nodeH, nodesep, ranksep)
3. For each combo, computes graph bounding box and aspect ratio
4. Calculates viewport fill % against target display (e.g., 1920×1080)
5. Renders SVG files to `/tmp/pipeline-layouts/` + an HTML gallery for comparison
6. Reports best match (we hit 99% viewport fill for 16:9)

**Usage:**
```bash
cd Frontend && node layout-tuner.cjs --width 1920 --height 1080
# Then serve: cd /tmp/pipeline-layouts && python3 -m http.server 8888
# Open: http://localhost:8888/gallery.html
```

**Key insight:** When optimising spatial layout, the bottleneck is visual feedback speed. Removing the browser from the loop (dagre → SVG → static file serve) made iteration ~100× faster. The dagre library is identical in Node.js and the browser, so parameters that work in the SVG sweep transfer directly to React Flow. This approach applies to any React Flow / dagre layout optimisation.

**Rule:** For any future layout tuning (new nodes, different display ratios), use `layout-tuner.cjs` to sweep parameters first, then apply the winning params to Pipeline.jsx. Don't iterate through the browser.

---

## 2026-03-02 — Winning UI scaling process: plan → parallel build → visual verify

**What happened:** Implemented a 7-phase pipeline operating surface (visual states, biography system, toolbar, control surface, chat integration, multi-pipeline) in a single session. 9 new files, 7 modified. Zero regressions on existing cards/metrics.

**Process that worked:**
1. **Deep exploration first** — read every file that would be touched, understand the full data model
2. **Write the plan** — detailed spec with file changes, schema, endpoints, component contracts
3. **Build in parallel** — schema + API + frontend hook simultaneously, not sequentially
4. **Frontend-first for interactions** — the hook (`usePipelineGraph`) was designed before any component was modified, so all components shared the same state model
5. **Backend scripts async** — auto_capture_commit.py and genesis_import.py are independent of the frontend work

**Key insight:** The planning phase paid for itself 10x. By speccing every file change, endpoint, and component contract upfront, the implementation was mechanical — no design decisions at code time, just translating spec to code. The plan also caught dependency ordering issues (e.g., biography tab needs version events API, which needs version_event table) before they became blockers.

**Rule:** For any multi-file feature (>5 files), write a full spec first. The plan should name every file, every endpoint, and every component prop. Implementation should be boring — if you're making design decisions while coding, the plan wasn't detailed enough.

---

## 2026-03-02 — Always run DB migrations on production, not dev

**What happened:** Implemented composition guardrails (new migration, new API endpoints, frontend changes) and ran migrations only on the dev DB (`mcas-dev-postgres-1`). The production site (`mcasui.com`) points at the production API (`backend-api-1`) which uses `backend-postgres-1`. New endpoints returned 500s in production because the schema changes (pareto_status column, composition_constraint table, updated validation gate trigger) didn't exist there.

**Root cause:** Defaulted to running migrations on dev DB because it's "safer." But the frontend deploys to Cloudflare Pages which hits the production API. Dev DB/API is only useful for local Vite dev server testing. Every migration that supports a feature being deployed to production MUST run on production.

**Rule:** When implementing a plan that includes DB migrations, always run migrations on the production database (`backend-postgres-1`) — not the dev DB. The frontend auto-deploys to CF Pages which hits production. Dev-only migrations are useless for verifying deployed features. Run on production first, dev second (or skip dev entirely unless actively developing against the dev API).

---

## 2026-03-02 — Deploy means production: backend API, DB, and frontend all target prod

**What happened:** After committing and pushing Wave 0+1 pipeline changes, nearly forgot to deploy the backend API to production. The `meta_api.py` changes (recursive CTE rewrite for `get_causal_chain`) would have been dead code until the production API container was rebuilt.

**The full deploy chain for any change touching both frontend and backend:**
1. `git push origin main` — triggers CF Pages auto-deploy for frontend
2. `cd /srv/mcas && git pull origin main` — pull to production path
3. `cd /srv/mcas/Backend && docker compose -f docker-compose.yml build <service> && docker compose -f docker-compose.yml up -d --no-deps <service>` — rebuild + restart the changed backend service(s)
4. DB migrations: `cat migration.sql | docker exec -i backend-postgres-1 psql -U trading -d trading`
5. Verify: check CF deploy status, hit a production API endpoint, confirm no errors

**Rule:** "Deploy" always means production. There is no staging environment. The dev containers (`mcas-dev-*`) are for local development only and are never part of the deploy chain. After every commit+push, mentally walk through: "Did I deploy everything that changed?" — frontend (automatic via CF), backend API (manual docker rebuild), DB migrations (manual psql).

---

## 2026-03-03 — Data first: run distribution analysis before hardcoding thresholds

**What happened:** D1 Dormancy-to-Ignition implementation hardcoded `DORMANCY_THRESHOLD=0.80`, `BB_WIDTH_COMPRESS=0.02`, `VOLUME_Z_SPIKE=2.0`, `RVOL_MIN=5.0` as config defaults before examining the empirical distribution of these indicators across historical data. The threshold 0.80 means "80% of the last 48 bars had range/close < 0.001" — but without knowing what percentage of symbol-hours score above 0.80 in reality, the value could produce a universe of 3 symbols or 300.

**Root cause:** Theory-first parameter selection. Thresholds were chosen because they "seemed reasonable" rather than derived from data. This violates Core Law #1: "Every parameter, weight, threshold, and design decision must be derived from empirical evidence, not intuition."

**Fix:** Write a distribution analysis script first. Sample 200 symbols × 6 months of 1h bars, compute the indicator at every 6th bar, report percentiles + threshold pass rates + conjunctive gate estimates. Use results to set thresholds that produce a target universe size.

**Rule:** Before hardcoding any threshold in a new setup, run the indicator across historical data and report its distribution. The script is cheap (~10 minutes). The cost of a wrong threshold is a strategy that fires 0 or 10,000 times when you expected 50. Always know the distribution shape before setting the cut point.

---

## 2026-03-03 — Conjunctive gate count can silently kill signal volume

**What happened:** D1 trigger requires 6 simultaneous conditions: dormancy confirmed, volume_z >= 2.0, rvol >= 5.0, accum_divergence > 0, price above compression box, BB width expanding. Each gate individually seems reasonable, but the conjunction of all 6 can produce near-zero signals — and the sim matrix only sweeps exit configs, not entry gate combinations.

**Root cause:** Additive gating without measuring the marginal filter rate of each gate. If gate 1 passes 5% and gate 2 passes 10% and they're independent, the conjunction passes 0.5%. With 6 gates, you're multiplying tiny probabilities.

**Fix:** Run a gate ablation pass — backfill with gates dropped one at a time to measure each gate's marginal filter contribution. Identify which gates are doing real filtering vs. which are redundant (gate that eliminates 0.1% of already-filtered candidates adds complexity without value).

**Rule:** For any multi-gate trigger, measure each gate's marginal contribution before running the full backfill. The ablation script is fast and prevents the situation where a 6-gate conjunction fires 0 times over 6 months and you don't know which gate to relax.

---

## 2026-03-03 — DB migrations: ALWAYS dev first, then production

**What happened:** Earlier rule said "always run on production first" to prevent CF Pages 500s when the frontend queries new schema. This caused schema drift — dev fell behind production, and CC spent time manually patching dev to match prod (adding `setup_version`/`batch_id` columns that had been applied to prod months earlier). The moment you're manually patching dev to match prod, the process is broken.

**Corrected process:** ALL migrations run on dev first, verify, then production. No exceptions.

```bash
# Step 1: Dev (catch bugs cheap)
cat docs/db/migrations/XXXX.sql | docker exec -i mcas-dev-postgres-1 psql -U trading -d trading
# Step 2: Verify — run a query that exercises the migration
# Step 3: Production (deploy verified schema)
cat docs/db/migrations/XXXX.sql | docker exec -i backend-postgres-1 psql -U trading -d trading
```

**What about the CF Pages 500 problem?** If the frontend auto-deploys before the prod migration runs, the new endpoints will 500. The fix is: run the prod migration BEFORE pushing the frontend code that depends on it. The deploy sequence is: dev migration → verify → prod migration → push code (triggers CF build + backend deploy). This is a deploy ordering discipline, not a "skip dev" justification.

**Rule:** Every migration runs on dev first, verifies, then runs on prod. If you catch yourself manually patching dev to match prod, the process broke.

---

## 2026-03-03 — Python output buffering hides progress in Docker containers

**What happened:** Ran `d1_dormancy_distribution.py` via `docker compose run --rm -T worker python3 /scripts/script.py`. The container was running (State=R, 60MB RSS, 16 threads active) but produced zero visible output for 7+ minutes. The script had `print()` progress statements every 50 symbols, but none appeared.

**Root cause:** Python buffers stdout by default when it detects output is not a TTY (which is the case with `-T` flag and pipe-based Docker log capture). Print statements accumulate in a buffer and only flush when the buffer fills (~8KB) or the process exits.

**Fix:** Add `-e PYTHONUNBUFFERED=1` to the docker compose run command:
```bash
docker compose -f docker-compose.yml run --rm -T -e PYTHONUNBUFFERED=1 worker python3 /scripts/script.py
```

**Rule:** Every `docker compose run` command for scripts with progress output must include `-e PYTHONUNBUFFERED=1`. Without it, a script that runs for 20 minutes appears identical to one that hanged on startup — there's no way to tell them apart from outside the container.

---

## 2026-03-03 — Host-side scripts can't resolve Docker internal hostnames

**What happened:** Ran a DB analysis script directly on the host (`python3 /tmp/d1_dormancy_distribution.py`). The script loaded `DATABASE_URL` from `.env`, which contains `postgresql://trading:...@postgres:5432/trading`. Failed with `psycopg.OperationalError: failed to resolve host 'postgres'`.

**Root cause:** `postgres` is a Docker Compose internal hostname that only resolves inside the Docker network. On the host, the hostname doesn't exist. The production DB is accessible on `localhost:5432` from the host, but that's not what `DATABASE_URL` contains.

**Fix:** Always run DB-accessing scripts inside Docker:
```bash
docker compose -f docker-compose.yml run --rm -T -e PYTHONUNBUFFERED=1 worker python3 /scripts/script.py
```
Never run them on the host unless `DATABASE_URL` is overridden to use `localhost`.

**Rule:** Scripts in `Backend/scripts/` are designed to run inside Docker containers where `DATABASE_URL` resolves correctly. Running them on the host will fail at the DB connection step. Always use `docker compose run` or `docker exec`.

---

## 2026-03-03 — Self-invalidating indicators: the breakout kills the regime it's breaking out of

**What happened:** D1 Dormancy-to-Ignition had two logical contradictions where the trigger event itself invalidated the conditions being checked:

**Bug 1: Dormancy score includes the breakout bar.** `dormancy_score` = fraction of last 48 bars where `(high-low)/close < 0.001`. A coin at dormancy 0.8125 (39/48 bars dormant) receives a breakout bar — by definition non-dormant. The score drops to 38/48 = 0.7917, failing the 0.80 threshold on the exact bar the trigger should fire. The act of breaking out invalidates the dormancy condition.

**Bug 2: Compression box includes current bar's high.** `donchian_high_20 = max(highs[-20:])` includes the current bar. Gate 3 checks `close > donchian_high_20`. But `close <= high <= donchian_high_20` by definition — this gate is mathematically impossible to pass.

**Root cause:** Both bugs come from treating a regime description (dormancy, compression) and a trigger event (breakout) as simultaneous when they are sequential. The dormancy phase and the breakout event happen in sequence: the coin WAS dormant, then it breaks out. Past tense on the regime, present tense on the trigger.

**Fix:**
- Dormancy score: compute on bars `[-(n+1):-1]` — the 48 bars BEFORE the current bar, excluding it. One-line window offset.
- Compression box: `evaluate_trigger` compares `close > prev_compression_box_high` (saved before current bar's donchian update), not the current bar's donchian.

**Rule:** Any indicator that describes a regime (dormancy, compression, consolidation) must exclude the current bar when used as a trigger gate. The trigger bar is evidence of the regime ENDING, not of the regime continuing. Always ask: "does computing this indicator on the current bar create a logical contradiction with the trigger condition?"

---

## 2026-03-03 — `docker compose -f docker-compose.yml` skips override, resolves wrong image

**What happened:** Ran `docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/backfill_d1_opportunities.py`. Container failed with `ModuleNotFoundError: No module named 'setups.d1_dormancy_ignition'`. The file existed in the build context (`worker/app/setups/d1_dormancy_ignition.py`), `docker compose build --no-cache worker` completed successfully, and `docker run --rm mcas-dev-worker:latest ls /app/setups/` showed the file. But the compose-created container used image `backend-worker:latest` (old, Feb 28) instead of `mcas-dev-worker:latest` (freshly built).

**Root cause:** The `-f docker-compose.yml` flag tells compose to use ONLY that file, skipping `docker-compose.override.yml`. The override contains `name: mcas-dev` which sets the project name (and thus the image name prefix). Without the override, compose derives the project name from the directory name (`Backend` → `backend`), resolving to image `backend-worker:latest` — a completely different (stale) image.

Two separate images coexisted:
- `backend-worker:latest` (854MB, built Feb 28, no D1 code) — resolved by `-f docker-compose.yml`
- `mcas-dev-worker:latest` (515MB, freshly built) — resolved by bare `docker compose` (auto-includes override)

**Fix:** In the dev workspace, use `docker compose run` (no `-f` flag) for running scripts. This auto-includes the override file, resolving to the correct `mcas-dev-worker` image. Reserve `-f docker-compose.yml` for production (`/srv/mcas/Backend`) only.

**Rule:** In the dev workspace (`/home/darren/Desktop/MCAS/Backend`), NEVER use `-f docker-compose.yml` for `run`/`build` commands — it strips the override that sets the project name, causing compose to resolve to stale `backend-*` images. Use bare `docker compose run/build` which auto-includes `docker-compose.override.yml`. The `-f` flag is for production only where there is no override file.

---

## 2026-03-03 — Stablecoin/pegged asset contamination in dormancy-based filters

**What happened:** D1 dormancy backfill found signals exclusively in stablecoins (BFUSDUSDT, FDUSDUSDT, TUSDUSDT, USDCUSDT, EURUSDT, EURIUSDT, PAXGUSDT, etc.) because pegged assets are dormant by design — their range/close is permanently near zero. The dormancy range threshold (0.001 = 0.1%) was calibrated for altcoins but only catches pegged assets, since real altcoin consolidation has ranges of 1-2%. Wasted 30 minutes of Opus compute on pegged asset noise.

**Root cause:** Exclusion sets are maintained as hardcoded lists that go stale as new pegged assets are listed. Any filter based on low volatility, low range, high dormancy, or compression will preferentially select pegged assets over the intended microcap targets.

**Fix:** Two-layer defense:
1. **Structural filter (primary):** Require minimum 90-day price variance (e.g., ATR as % of price > 0.5%, or max drawdown from ATH > 5%). Assets pegged by design will never pass this regardless of their name or listing date.
2. **Exclusion list (secondary):** Hardcoded list for known pegged/wrapped/leveraged assets. Goes stale but catches edge cases the structural filter might miss.

**Rule — applies to ALL strategies, scans, backfills, and universe filters:**
1. Never rely solely on a hardcoded exclusion list for stablecoins/pegged assets. Lists go stale on every new Binance listing.
2. Both structural filter AND exclusion list must be present.
3. When debugging unexpectedly high or low signal counts, ALWAYS check which symbols are passing. Sort by frequency. Stablecoins cluster at the top of any low-volatility filter.
4. Any time a filter selects for "unusual quietness followed by activity," pegged assets dominate. The quietness isn't unusual for them — it's their entire purpose.

**Also:** The dormancy range threshold itself was wrong. At 0.001 (0.1%), 0% of altcoins qualify. At 0.010 (1.0%), 83% of altcoins show dormancy periods. The threshold was off by 10x. Calibrated via 150-altcoin sweep (excluding stablecoins), sampling 1h bars over 6 months. Sweet spot at 0.010: selective enough to mean something, loose enough to actually capture altcoin consolidation.

---

## 2026-03-04 — Docker `run` containers without mem_limit trigger kernel OOM killer

**What happened:** `docker compose run --rm worker python3 /scripts/sim_d1_matrix.py` caused kernel OOM killer to fire repeatedly, killing random Python and Docker processes across the server — including production containers. The Hetzner console showed dozens of `Memory cgroup out of memory: Killed process` entries.

**Root cause:** The `worker` service in `docker-compose.yml` had no `mem_limit`. When `docker compose run worker` creates a one-off container, it inherits unlimited memory. On a 32GB server already running 30+ containers, one runaway script can exhaust remaining RAM. The kernel OOM killer then kills processes indiscriminately — it picks the highest-RSS process, which could be production Postgres, the API, or any other container.

**Fix:** Add `mem_limit: 8g` to the `worker` service in `docker-compose.yml`. When a `run` container hits 8GB, Docker kills only that container's process (clean SIGKILL to the cgroup), not random production processes. The script crashes, but production stays up.

**Rule:** Every service definition used for `docker compose run` scripts must have `mem_limit` set. Without it, one bad script can take down the entire server. The limit should be generous enough for legitimate workloads (8GB for worker) but constrained enough to prevent a single container from triggering the kernel OOM killer.
