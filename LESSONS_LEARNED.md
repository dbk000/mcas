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
