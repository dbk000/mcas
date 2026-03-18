# MCAS Frontend Go-Live and Governance Audit

Date: 2026-03-16

## Purpose

This document closes out the earlier frontend/backend governance questions and expands them into an exhaustive go-live audit for everything in the frontend that is intended to ship on `https://mcasui.com`.

It answers two separate questions:

1. Are the original governance concerns actually real in the current codebase?
2. What still has to be true before the frontend can honestly be called "live" on `mcasui.com`?

## Bottom Line

The current repo is not yet in a "fully live" state for the entire frontend surface.

What is already true:

- The public static funnel can be hosted today.
- The frontend no longer persists the backend API key and now uses a server-backed `HttpOnly` session model.
- Backend governance bootstrap, frozen registries, import guard, and doctrine gates are real.
- The backend already exposes the major API surfaces the current app uses: session, runs, opportunities, control plane, trading, analytics, and meta/pipeline routes.

What still blocks calling the full frontend live:

- The frontend production build still fails its release gate because the `ui-*.js` chunk is over budget.
- The Playwright smoke suite still fails 2 tests in the expired-opportunity simulation modal flow.
- The frontend has no baked-in production API origin. `/app/*` depends on a browser-stored base URL or a manual Settings step.
- Paid access is still browser-local UX state, not server-verified entitlement.
- Several operator routes still fall back to authored preview/demo data.
- Backend lineage enforcement/stamping is still not fully wired through the live queue and persistence paths.
- Production cookie security behind Caddy needs explicit verification because `secure=` is derived from the request scheme inside FastAPI while `uvicorn` is started without proxy-header flags.
- Hosted checkout remains disabled unless `VITE_STRIPE_PAYMENT_URL` is configured.

## Verification Performed

Code paths inspected:

- Frontend route/app shell/session/paywall/config flows
- Frontend public curation/demo fallbacks
- Frontend build, test, and deployment config
- Backend session, CORS, router surface, governance bootstrap, frozen registries, runtime graph, import guard, CI doctrine gates
- Backend queue/write paths relevant to lineage enforcement

Frontend commands run on 2026-03-16:

- `npm run test:unit` -> passed
- `npm run lint:css` -> passed
- `npm run lint:knip` -> passed
- `npm run lint:js` -> passed
- `npm run build` -> failed release gate
- `npx playwright test tests/full-ui.spec.js` -> 22 passed, 2 failed

Exact current command failures:

- Build gate:
  - `Chunk ui-CK2R-eMk.js matched ui-*.js at 12.44 KB, over its limit of 12.00 KB.`
- Playwright smoke failures:
  - `tests/full-ui.spec.js:319` failed on both `desktop-chromium` and `mobile-operator`
  - failing assertion: `getByRole('button', { name: /Filter setups/i })`
  - affected UI: expired-opportunity simulation modal multi-select interaction

## Release Gate Status

| Gate | Status | Notes |
| --- | --- | --- |
| Unit tests | Pass | `tests/pf.unit.test.js`, `src/hooks/__tests__/useNodeVisualState.test.js` |
| CSS lint | Pass | No current Stylelint issues |
| JS lint | Pass | No current ESLint failures in this tree |
| Unused-code scan | Pass | `knip` exits clean |
| Production build | Fail | Bundle budget hard-fails on `ui-*.js` |
| Playwright smoke | Fail | 22/24 pass, 2/24 fail in simulation modal flow |

## Route Readiness

### Public Funnel

| Route | Current backing model | Current status | What must be true for live |
| --- | --- | --- | --- |
| `/` | Curated JSON in `Frontend/public/curation/proof-pack.v1.json` and `winners.v1.json` | Ready if curated marketing content is intentional | Keep curation files current |
| `/winners` | Curated JSON in `Frontend/public/curation/winners.v1.json` | Ready if curated free surface is intentional | Keep curation current |
| `/pricing` | Curated JSON in `pricing-matrix.v1.json` and `proof-pack.v1.json` | Ready if pricing copy is current | Confirm pricing copy and plan data are current |
| `/upgrade` | Curated onboarding/pricing data plus `VITE_STRIPE_PAYMENT_URL` | Not fully live | Configure checkout URL and decide whether browser-local activation is acceptable |
| `/checkout/success` | Local browser activation only | Not true billing proof | Replace with server-verified entitlement if this is part of a commercial launch |
| `/checkout/cancel` | Static UX surface | Ready | None beyond copy review |
| `/settings` | Manual backend URL + one-time API key exchange | Functional, but not zero-config | Decide how production users discover or inherit `https://api.mcasui.com` |

### Operator App

| Route | Current backing model | Current status | Remaining issue |
| --- | --- | --- | --- |
| `/app` | Live-only dashboard data | Conditionally live | Requires configured API origin, backend session, and local paid flag |
| `/app/strategies` | Live data with authored dashboard fallback | Not fully live | Decide whether preview fallback is acceptable in production |
| `/app/opportunities` | Live-only data | Not fully live | Simulation modal smoke test currently fails |
| `/app/opportunities/survey` | Live-only data | Conditionally live | Depends on live opportunities and price-window APIs |
| `/app/opportunities/:id` | Live data with authored walkthrough fallback | Not fully live | Decide whether fallback walkthrough is acceptable |
| `/app/markets` | Live-only data | Conditionally live | Needs healthy backend/session |
| `/app/markets/:symbol` | Live-only data | Conditionally live | Needs OHLCV/snapshot/squeeze endpoints populated |
| `/app/runs` | Live-only data | Conditionally live | Needs healthy backend/session |
| `/app/runs/compare` | Live-only data | Conditionally live | Needs completed backtests |
| `/app/runs/:id` | Live data with demo trade-derived metric fallback | Not fully live | Live run payloads still do not always provide realized rows used by the UI |
| `/app/runs/:id/simulation` | Live data with demo fallback | Not fully live | Remove demo fallback or explicitly keep it as preview behavior |
| `/app/control` | Live control-plane APIs | Conditionally live | Needs working control queue/paper routes |
| `/app/paper` | Live trading APIs | Conditionally live | Needs paper engine and trading routes populated |
| `/app/analytics/correlations` | Live analytics APIs | Conditionally live | Needs correlation backend job flow working |
| `/app/pipeline` | Live meta APIs | Conditionally live | Meta endpoints exist; browser-side AI chat remains intentionally disabled |

## Cross-Cutting Frontend Findings

### 1. Static Hosting and SPA Routing

Status: Ready

What is already in place:

- `Frontend/public/_redirects` rewrites all paths to `/index.html`, which is required for `BrowserRouter` deep links.
- `Frontend/public/_headers` defines security headers and CSP for a static host.
- `Frontend/index.html`, `Frontend/public/site.webmanifest`, and `Frontend/public/social/*` provide baseline metadata and app assets.
- `Frontend/src/main.jsx` enables MSW mocking only in `import.meta.env.DEV`, so production builds do not intentionally start mock handlers.

Closeout:

- Static hosting itself is not the blocker.

### 2. Production API Origin Strategy

Status: Open

What is true now:

- The frontend stores only `mcas_api_base_url` in browser storage and reads that value on startup.
- There is no baked-in production API origin in frontend code.
- `/app/*` redirects to `/settings` when no backend base URL exists.
- Playwright test helpers explicitly seed `mcas_api_base_url` in storage to make app routes work in tests.

Why this matters:

- On a fresh browser visiting `https://mcasui.com`, the operator app is not zero-config.
- A user must either:
  - manually type `https://api.mcasui.com` into Settings, or
  - receive that value from some deployment-specific initialization path that does not exist in this repo today.

Required action:

- Decide one production approach:
  - preconfigure `https://api.mcasui.com` in the frontend build/runtime,
  - inject it via a supported runtime config object,
  - or accept a manual Settings step as part of the operator onboarding flow.

### 3. Session and Browser Secret Handling

Status: Closed

What is true now:

- The frontend performs a one-time `{ api_key }` exchange to `/v1/session`.
- After that, it relies on cookie-backed auth with `credentials: 'include'`.
- The frontend sanitizes config so only the base URL is persisted. The API key is cleared and not stored.
- The app now blocks `/app/*` until the backend session is present and authenticated.

Closeout:

- The earlier concern that the browser was persisting long-lived backend secrets is no longer accurate.

### 4. Paid Entitlement Model

Status: Open by design

What is true now:

- Paid access still depends on browser-local state in `SubscriptionContext`.
- `PaidRoute` explicitly says the gate is UX-only and that secure entitlement is out of scope.
- `Upgrade` and `CheckoutSuccess` explicitly say checkout success only marks this browser as paid locally.
- Backend session bootstrap still returns `entitlement_mode: preview_only`.

Why this matters:

- If `/app/*` is meant to be a real paid production product, current gating is not sufficient.
- Anyone with browser storage access can promote themselves locally.

Required action:

- Replace local paid gating with a server-verified entitlement model before claiming a full paid commercial launch.

### 5. Checkout / Upgrade Wiring

Status: Open

What is true now:

- `Upgrade` reads `import.meta.env.VITE_STRIPE_PAYMENT_URL`.
- If the env var is missing, the checkout CTA is intentionally disabled.
- The flow still performs browser-local activation after `/checkout/success`; it does not verify purchase server-side.

Required action:

- If upgrade/checkout is meant to work in production:
  - set `VITE_STRIPE_PAYMENT_URL`,
  - define the authoritative post-payment server flow,
  - and stop treating `/checkout/success` as payment proof.

### 6. Preview / Demo Fallback Policy

Status: Open

What is true now:

- Public routes intentionally use curated JSON by design.
- Operator routes still contain authored preview/demo fallbacks in at least these places:
  - `Frontend/src/pages/StrategyDashboard.jsx`
  - `Frontend/src/pages/OpportunityDetail.jsx`
  - `Frontend/src/pages/RunDetail.jsx`
  - `Frontend/src/pages/run-detail/RunOverviewTab.jsx`
  - `Frontend/src/pages/SimulationResults.jsx`

Why this matters:

- If "fully live" means no authored preview data inside paid operator surfaces, the current app is not there yet.
- If the product intentionally wants graceful demo fallback in operator views, that needs explicit signoff because it affects trust framing.

Required action:

- Decide route by route whether preview fallback stays, is replaced with empty/live-only states, or is hidden behind a non-production flag.

### 7. Pipeline AI / Chat

Status: Intentionally disabled

What is true now:

- Browser-side chat remains disabled pending a server-backed proxy.
- This is a good security choice because it prevents frontend-held model/vendor secrets.

Closeout:

- Not a blocker unless pipeline chat is part of the promised launch scope.

## Production Domain, CORS, and Cookie Findings

### Frontend host

Status: Ready

- The frontend repo contains static-host rewrites and security headers suitable for a site like `https://mcasui.com`.

### API domain

Status: Partially ready

- `Backend/caddy/Caddyfile` is clearly written for `api.mcasui.com`.
- Caddy explicitly allows origin `https://mcasui.com` and proxies `/health` and `/v1/*` to the API container.

### FastAPI CORS

Status: Needs alignment check

- `Backend/api/app/main.py` still hardcodes `https://mcas.pages.dev` plus localhost dev origins in the app-level CORS middleware.
- If all production traffic reaches FastAPI only through Caddy, the external Caddy headers may be sufficient.
- If the app-level FastAPI CORS middleware is ever the direct production path, `https://mcasui.com` is missing there.

Required action:

- Decide which layer is the production source of truth for CORS.
- Either:
  - keep Caddy as the only public entry point and document that assumption, or
  - add `https://mcasui.com` to the FastAPI CORS allowlist too.

### Session cookie security behind proxy

Status: Open and important

What is true now:

- `Backend/api/app/routers/session.py` sets `secure=request.url.scheme == "https"`.
- `Backend/Dockerfile.api` starts `uvicorn` without `--proxy-headers` or similar forwarded-proto handling.
- `Backend/docker-compose.yml` places FastAPI behind Caddy.

Why this matters:

- If FastAPI sees the proxied request as plain `http`, the cookie may be minted without the `Secure` flag even though the public site is HTTPS.
- That is exactly the kind of subtle production issue that breaks a supposedly hardened session flow.

Required action:

- Verify the cookie on real `https://api.mcasui.com` responses.
- If needed, fix this by either:
  - enabling trusted proxy headers for `uvicorn`, or
  - setting `secure=True` in production session-cookie handling.

## Backend Coverage for Current Frontend

Status: Broadly present

The current backend router surface covers the major frontend dependencies:

- Session: `/v1/session`, `/v1/session/bootstrap`
- Operator data: `/v1/strategies`, `/v1/opportunities`, `/v1/runs`
- Control plane: `/v1/control/*`
- Trading/paper: `/v1/trading/*`
- Analytics: `/v1/analytics/correlation`
- Pipeline/meta: `/v1/meta/topology`, `/v1/meta/experiments`, `/v1/meta/compositions`, `/v1/meta/composition/draft`, `/v1/meta/node/version`, `/v1/meta/events/recent`, and related endpoints

Closeout:

- I did not find a major frontend surface that points at a completely nonexistent backend family.
- The remaining issues are operational and product-policy gaps, not a total absence of backend routes.

## Governance Closeout

### 1. Frozen Registries and Bootstrap Governance

Status: Closed

- Backend bootstrap creates registries, freezes them, seals the runtime graph, and installs import-boundary enforcement.
- `FrozenRegistry` immutability is real.
- The frontend consumes registry-derived strategy/config data through read-only API surfaces.

### 2. Frontend Lineage Propagation

Status: Closed with scope correction

- The current browser HTTP contract does not require `runtime_graph_seal`, `process_id`, or serialized `Lineage`.
- The frontend consumes provenance-like response data such as `sim_trace.kline_lineage`, but it is not responsible for request-side lineage stamping under the current API contract.

### 3. Peer Shunning / `LineageEnforcer`

Status: Open, backend-owned

- The lineage enforcement mechanism exists in the kernel.
- The live runtime paths inspected here still do not appear to consistently call `enforce_lineage()` or stamp lineage columns on governed writes.
- Queue payloads and inspected writes remain business-only payloads in several live paths.

Required action:

- Wire lineage stamping and enforcement into live queue ingress and governed persistence paths.

### 4. Import Guard and CI Doctrine

Status: Closed or largely closed

- Runtime import-boundary enforcement is implemented.
- Backend AST doctrine gates are implemented and exercised via pre-commit.
- The frontend still uses a small number of public build-time env vars, but that does not violate the backend-only doctrine as implemented.

### 5. Browser Secret Handling

Status: Closed

- The frontend does not persist the backend API key in config.
- Browser-side AI chat remains off until a server proxy exists.

## Launch Scope Recommendations

### If the goal is "public marketing site live"

This is close.

Minimum remaining work:

1. Make the build pass.
2. Make the Playwright smoke suite pass.
3. Configure `VITE_STRIPE_PAYMENT_URL` if upgrade should be clickable.
4. Keep curated proof/pricing/onboarding JSON current.

Notes:

- The browser-local paid gate and backend lineage gap do not block a pure marketing launch if `/app/*` is not being sold as a fully live product surface.

### If the goal is "operator app beta live"

This is not finished.

Minimum remaining work:

1. Make the build pass.
2. Make the Playwright smoke suite pass.
3. Decide how production users get `https://api.mcasui.com` without manual guesswork.
4. Verify Caddy/CORS/cookie behavior on the real production domains.
5. Decide whether preview/demo fallbacks are acceptable in beta operator routes.

### If the goal is "full paid product live"

This is clearly not closed yet.

Minimum remaining work:

1. Everything in the beta list.
2. Replace browser-local paid gating with server-verified entitlement.
3. Stop treating checkout success as local browser proof only.
4. Decide whether operator surfaces may ever show authored preview/demo data.
5. Wire backend lineage enforcement/stamping through live runtime paths.

## Must-Do Checklist Before Calling The Full Frontend Live

1. Fix or explicitly re-baseline the bundle budget failure in `npm run build`.
2. Fix the expired-opportunity simulation modal interaction so `npx playwright test tests/full-ui.spec.js` passes 24/24.
3. Implement a production API-origin strategy for `https://api.mcasui.com` instead of relying on manual Settings entry alone.
4. Verify that production session cookies are actually set with `Secure` on the real `api.mcasui.com` deployment, and patch proxy-header handling if they are not.
5. Decide whether app-route preview/demo fallbacks are acceptable in production; remove them if the answer is no.
6. Replace browser-local paid gating with server-backed entitlement if `/app/*` is a real paid product.
7. Configure `VITE_STRIPE_PAYMENT_URL` if Upgrade is part of launch scope.
8. Align production CORS handling between Caddy and FastAPI so `https://mcasui.com` is unquestionably supported.
9. Wire backend lineage enforcement/stamping through the live queue and persistence paths.

## Final Disposition

The earlier governance audit was directionally right that lineage and entitlement still matter, but it overstated the frontend's responsibility for them.

As of 2026-03-16:

- frontend auth and browser-secret handling are materially hardened,
- the public funnel is close to shippable,
- the operator app still has release, production-config, entitlement, and governance gaps,
- and the biggest real governance issue still open is backend-side lineage enforcement, not browser-side lineage stamping.
