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
