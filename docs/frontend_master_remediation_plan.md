# 1. Executive diagnosis
The frontend is not production-ready for a trading application.

It is functional, it builds, and parts of it are thoughtfully instrumented, but its current trust boundaries, state architecture, and quality-gate reliability are below production grade. The main problems are not evenly distributed. Risk is concentrated in a handful of files and cross-cutting patterns:

- Security posture is below baseline.
  - `src/context/ApiContext.jsx` persists backend API configuration, including the API key, to `localStorage` (`lines 23-39`, `73-90`).
  - `src/components/pipeline/ChatPanel.jsx` persists an Anthropic key in `localStorage` and calls Anthropic directly from the browser with `anthropic-dangerous-direct-browser-access: true` (`lines 369-380`, `458-465`, `576-586`).
  - `src/pages/Pipeline.jsx` contains a hardcoded client-side password gate and stores the "authenticated" flag in `sessionStorage` (`lines 116-129`), which is security theater, not access control.
  - `public/_headers` sets a few response headers but does not define a Content Security Policy.

- Architecture is concentrated and brittle.
  - `src/context/ApiContext.jsx` mixes config persistence, request transport, auth-style gating, connection tests, chaos mode, and fixture state.
  - Several route files are effectively mini-applications:
    - `src/pages/Pipeline.jsx` - 1153 LOC
    - `src/pages/Opportunities.jsx` - 1094 LOC
    - `src/pages/RunDetail.jsx` - 835 LOC
    - `src/pages/ControlPlane.jsx` - 826 LOC
    - `src/pages/OpportunitySurvey.jsx` - 797 LOC
    - `src/pages/PaperTrading.jsx` - 732 LOC

- Performance posture is structurally weak.
  - `src/App.jsx` statically imports every route component.
  - `vite.config.js` does not define route-level chunking or `manualChunks`.
  - Production build output is one main JS bundle at `993.92 kB` minified / `282.26 kB` gzip.
  - The bundle budget script passes this near-1 MB main chunk because the baseline in `bundle-budget.json` has already been ratcheted upward to `950 kB`.

- Tooling is not yet trustworthy.
  - `npm run lint:js` failed with `126 errors` and `115 warnings`.
  - `npm run lint:css` failed with `20 errors`, some real and some config-generated because Tailwind directives are treated as unknown at-rules.
  - `npm run lint:knip` found:
    - 3 unused files
    - 1 unused devDependency (`msw-auto-mock`)
    - 1 unlisted dependency (`vitest`)
    - 37 unused exports
  - `npm run test:unit` passed, but it only runs `tests/pf.unit.test.js`.
  - `npm run test:e2e` did not execute tests; Playwright timed out after `120000ms` waiting for its web server. Manual `npm run dev -- --port 3000` showed Vite auto-switching to port `3001` because `3000` was in use, while `playwright.config.js` hardcodes `3000` and does not use `strictPort`.

- Likely failure modes under continued feature growth:
  - further browser trust-boundary regressions
  - stale or inconsistent request behavior from oversized context state
  - page-level regressions inside god components
  - initial-load and rerender degradation as heavy surfaces grow
  - false confidence from gates that are noisy, partial, or pointed at the wrong runtime

Bluntly: this codebase is salvageable without a rewrite, but it needs a staged remediation program before it can be treated as production-grade trading software.

# 2. Current-state architecture map
## Routing model
- React Router v6 with `BrowserRouter` in `src/main.jsx`.
- All routes are declared in one `<Routes>` tree in `src/App.jsx`.
- Public routes:
  - `/`
  - `/winners`
  - `/pricing`
  - `/upgrade`
  - `/checkout/success`
  - `/checkout/cancel`
  - `/settings`
- App routes under `/app/*`:
  - dashboard, strategies, opportunities, markets, runs, control, paper, correlations, pipeline
- All route components are statically imported in `src/App.jsx`.
- One app-wide React error boundary exists in `src/App.jsx`.
- No route-level lazy loading, no data-router boundaries, no `Suspense` route shells.

## Data-fetching model
- Centralized through `src/context/ApiContext.jsx`:
  - `testConnection`
  - `apiRequest`
  - `get`
  - `post`
  - `connectionStatus`
- Consumer hooks in `src/hooks/useData.js`:
  - `useFetch`
  - `usePolling`
  - many route-specific wrappers
- Direct `fetch` still exists outside the API layer:
  - `src/components/pipeline/ChatPanel.jsx`
  - `src/pages/Opportunities.jsx`
  - `src/pages/SimulationResults.jsx`
  - `src/utils/logger.js`
  - `src/utils/publicJson.js`
- No `AbortController` usage was found in `src/`.

## Context/provider model
- Provider stack in `src/main.jsx`:
  - `ToastProvider`
  - `ApiProvider`
  - `SubscriptionProvider`
- `ApiContext` is oversized and cross-cutting.
- `SubscriptionContext` controls paid/free tier in browser storage.
- `ToastContext` is lightweight and isolated.
- No domain-scoped providers for route families like pipeline, runs, paper trading, or opportunities.

## API/config model
- `ApiContext` stores:
  - `config` with `baseUrl` and `apiKey`
  - `chaosMode`
  - `fixtureState`
  - `connectionStatus`
- `ProtectedRoute` in `src/App.jsx` treats presence of `config.baseUrl` and `config.apiKey` as a route prerequisite.
- `PaidRoute` uses client-side subscription tier from `SubscriptionContext`.
- Dev-only mock bootstrapping happens in `src/main.jsx` via `enableMocking()`.
- `VITE_SKIP_AUTH=true` is passed by Playwright's webServer command, but there is no matching frontend usage of that variable in `src/`.

## Styling stack
- Vite + PostCSS + Tailwind.
- `src/index.css` contains:
  - Tailwind directives
  - global styles
  - component-ish utility classes
  - animations
  - form/button/toggle styling
- `src/styles/tokens.css` defines operator density tokens.
- Tailwind classes are used heavily inline throughout page/component JSX.
- Stylelint config is in `.stylelintrc.json`.

## Testing stack
- Playwright E2E and screenshot tests in `tests/`.
- Node built-in test for PF utility:
  - `tests/pf.unit.test.js`
- Orphaned Vitest-style hook test:
  - `src/hooks/__tests__/useNodeVisualState.test.js`
- No React Testing Library or equivalent component/integration runner is wired.
- `hardening:check` runs:
  - `lint:js`
  - `lint:css`
  - `lint:knip`
  - `test:density`
  - `test:e2e`
- `hardening:check` does not include `build` or `test:unit`.

## Build/bundling stack
- Vite 5 in `vite.config.js`
- optional visualizer in `stats` mode
- bundle budget script in `scripts/check-bundle-budget.js`
- bundle baseline in `bundle-budget.json`
- no manual chunk strategy
- no route-level split by default

## Major page surfaces
- Marketing:
  - `Landing.jsx`
  - `Winners.jsx`
  - `Pricing.jsx`
  - `Upgrade.jsx`
  - `Settings.jsx`
- Operator / trading:
  - `Dashboard.jsx`
  - `StrategyDashboard.jsx`
  - `Opportunities.jsx`
  - `OpportunityDetail.jsx`
  - `OpportunitySurvey.jsx`
  - `Markets.jsx`
  - `MarketDetail.jsx`
  - `Runs.jsx`
  - `RunCompare.jsx`
  - `RunDetail.jsx`
  - `SimulationResults.jsx`
  - `ControlPlane.jsx`
  - `PaperTrading.jsx`
  - `CorrelationAnalysis.jsx`
  - `Pipeline.jsx`

## Shared component system
- `src/components/ui.jsx` is a broad kitchen-sink primitives file.
- Layout system:
  - `Layout.jsx`
  - `MarketingLayout.jsx`
  - `PaidRoute.jsx`
- Pipeline subsystem components:
  - `src/components/pipeline/*`
- Opportunities/chart subsystem:
  - `src/components/opportunities/*`

## Key complex pages/components
- `src/pages/Pipeline.jsx`
- `src/pages/Opportunities.jsx`
- `src/pages/RunDetail.jsx`
- `src/pages/ControlPlane.jsx`
- `src/pages/OpportunitySurvey.jsx`
- `src/pages/PaperTrading.jsx`
- `src/components/pipeline/ChatPanel.jsx`
- `src/components/opportunities/OpportunityCurveChart.jsx`
- `src/hooks/usePipelineGraph.js`

## Key cross-cutting utilities
- `src/utils/logger.js`
- `src/utils/analytics.js`
- `src/utils/integrity.js`
- `src/utils/hardening.js`
- `src/utils/publicJson.js`
- `src/utils/pageMeta.js`
- `src/utils/returnTo.js`
- `src/utils/formatters.js`
- `src/utils/pf.js`

# 3. Gap analysis against target state
## Security / secrets / trust boundaries
- Current problem:
  - backend API key is persisted in `localStorage` in `src/context/ApiContext.jsx`
  - Anthropic key is persisted in `localStorage` in `src/components/pipeline/ChatPanel.jsx`
  - direct browser Anthropic calls are made with `anthropic-dangerous-direct-browser-access`
  - pipeline route uses a hardcoded browser password and `sessionStorage`
  - entitlement state is client-controlled in `src/context/SubscriptionContext.jsx`
  - `public/_headers` does not include a CSP
- Why it matters:
  - browser storage is not a safe location for privileged secrets
  - client-side passwords and paid gates are not authorization
  - direct vendor calls from the browser expand attack surface and make compliance/control harder
- Severity: Critical
- Recommended end-state:
  - no privileged secrets in browser storage
  - no browser-direct LLM vendor calls
  - server-mediated session/bootstrap contract for protected routes
  - paid gating explicitly treated as UX only unless backed by server entitlement
  - CSP added and tuned after trust boundary cleanup

## State management / context architecture
- Current problem:
  - `ApiContext` mixes concerns that should not live in one provider
  - `apiRequest` captures `chaosMode` and `fixtureState` but only depends on `[config]`
  - `useData.js` serializes params but still closes over raw `params`
  - dev fixtures and chaos toggles are intertwined with normal runtime
- Why it matters:
  - stale-closure correctness bugs become likely
  - rerender surface is too broad
  - test seams are weak
  - production and dev-only behavior are not cleanly separated
- Severity: High
- Recommended end-state:
  - split context into narrower modules:
    - runtime/session config
    - request client
    - dev fixture/chaos controls
    - subscription/entitlement
  - consumers depend on stable domain hooks, not a broad god-context

## Component architecture / page decomposition
- Current problem:
  - route files own fetching, state, analytics, exports, modal logic, and UI
  - helper components are defined inside render-heavy pages
  - `ui.jsx` is too broad and mixes unrelated primitives
- Why it matters:
  - pages are hard to test and hard to change safely
  - ownership is unclear
  - parallel remediation creates merge conflicts
- Severity: High
- Recommended end-state:
  - page shell + container + section + leaf component pattern
  - route files reduced to orchestration
  - shared primitives split into focused modules
  - heavy route families moved into page-local folders

## Data access / API client design
- Current problem:
  - direct fetches bypass `ApiContext`
  - no abort/cancellation layer
  - error shaping is inconsistent
  - transport policy is embedded in context and page code
- Why it matters:
  - trading screens need deterministic request lifecycle behavior
  - stale data and race conditions become user-visible
  - API-first modularity is weak
- Severity: High
- Recommended end-state:
  - explicit API client layer
  - normalized domain adapters
  - abort support for route changes and rapid filter changes
  - consistent error objects and retry/backoff policy

## Performance / bundling / rendering
- Current problem:
  - all routes load eagerly
  - one oversized main JS bundle
  - heavy libs are loaded into startup path
  - large pages do substantial derived work inline
- Why it matters:
  - initial load is heavier than it needs to be
  - expensive surfaces are not isolated
  - growth pressure will keep increasing the main bundle
- Severity: High
- Recommended end-state:
  - route-level lazy loading
  - `Suspense` fallbacks per heavy route family
  - manual chunk strategy for heavy graph/chart surfaces
  - measured render budgets for pipeline/chart pages

## Tooling / lint / test / dependency hygiene
- Current problem:
  - JS lint fails broadly
  - CSS lint mixes true issues with Tailwind config noise
  - E2E harness points at `vite dev`
  - hook tests are unwired
  - generated mock output causes lint parse errors
  - dead code/dependency drift is real
- Why it matters:
  - current gates are not trustworthy proxies for release readiness
  - signal-to-noise ratio is poor
- Severity: High
- Recommended end-state:
  - coherent test runner topology
  - Playwright smoke on built/preview assets
  - Tailwind-aware Stylelint
  - generated code excluded or generated correctly
  - dead code/deps materially reduced

## Styling / CSS architecture
- Current problem:
  - `src/index.css` is overgrown
  - `.stylelintrc.json` enforces token rules globally without Tailwind awareness
  - raw CSS values and app-specific utility classes live alongside Tailwind directives
- Why it matters:
  - lint noise hides real defects
  - styling ownership drifts
- Severity: Medium
- Recommended end-state:
  - Tailwind/PostCSS-aware Stylelint config
  - operator-token rules scoped to intended surfaces
  - global CSS narrowed to primitives
  - app-specific component classes moved closer to their owning feature

## Observability / error handling
- Current problem:
  - only one app-wide boundary in `src/App.jsx`
  - extra boundaries exist mostly for pipeline only
  - logger reads runtime config from `localStorage`
  - telemetry is best-effort and not clearly governed
- Why it matters:
  - route failures are not consistently contained
  - debugging and support workflows are tied to insecure config assumptions
- Severity: High
- Recommended end-state:
  - route/feature-level error boundaries
  - standardized loading/refreshing/error/empty patterns
  - structured telemetry with redaction and server-side ownership

## UX robustness for trading workflows
- Current problem:
  - no centralized async state model
  - no request cancellation
  - direct fetch exports have ad hoc error handling
  - pages mix live and demo data fallbacks differently
- Why it matters:
  - trading users need confidence about freshness, failure, and fallback mode
  - stale or mixed state can mislead
- Severity: High
- Recommended end-state:
  - explicit async-state contracts for critical routes
  - stale-data indication
  - consistent fallback behavior
  - manual regression suite for run/opportunity/market/paper flows

## Accessibility
- Current problem:
  - custom `Modal` has no focus trap, no `aria-modal`, no dialog semantics
  - `Tabs` has no tablist semantics
  - `MultiSelect` has limited keyboard/accessibility affordances
  - complex chart/pipeline surfaces have no evident a11y verification
- Why it matters:
  - dense operator UIs regress quickly for keyboard and assistive-technology users
- Severity: Medium
- Recommended end-state:
  - route/component accessibility checks
  - semantic primitives
  - focus management for overlays/panels
  - chart/pipeline fallback patterns

## Developer experience / maintainability
- Current problem:
  - code ownership boundaries are weak
  - generated and human-owned code are mixed into the same gate surfaces
  - multiple large cross-cutting files attract repeated edits
- Why it matters:
  - future features and remediation work will keep colliding
  - merge risk remains high unless ownership is partitioned explicitly
- Severity: High
- Recommended end-state:
  - domain-oriented module boundaries
  - smaller files
  - documented ownership and execution sequencing

# 4. High-risk findings register
## 1. Browser-stored backend API configuration
- Severity: Critical
- Evidence:
  - `src/context/ApiContext.jsx:23-39`
  - `src/context/ApiContext.jsx:73-90`
- Impacted files/modules:
  - `src/context/ApiContext.jsx`
  - `src/pages/Settings.jsx`
  - `src/App.jsx`
  - `src/utils/logger.js`
- Likely root cause:
  - frontend-owned bootstrap/auth model built for convenience
- Blast radius:
  - all protected routes and request flows
- Blocks:
  - Release

## 2. Browser-side Anthropic key persistence and direct vendor invocation
- Severity: Critical
- Evidence:
  - `src/components/pipeline/ChatPanel.jsx:369-380`
  - `src/components/pipeline/ChatPanel.jsx:458-465`
  - `src/components/pipeline/ChatPanel.jsx:576-586`
- Impacted files/modules:
  - `src/components/pipeline/ChatPanel.jsx`
  - `src/pages/Pipeline.jsx`
- Likely root cause:
  - feature shipped before backend proxy/service existed
- Blast radius:
  - any pipeline user using chat
- Blocks:
  - Release

## 3. Client-side pipeline password gate is false security
- Severity: High
- Evidence:
  - `src/pages/Pipeline.jsx:116-129`
- Impacted files/modules:
  - `src/pages/Pipeline.jsx`
- Likely root cause:
  - placeholder protection pattern left in a sensitive operator surface
- Blast radius:
  - pipeline route access model
- Blocks:
  - Release hardening

## 4. `ApiContext` has a real stale-closure / correctness defect
- Severity: High
- Evidence:
  - `src/context/ApiContext.jsx:236-322`
  - `react-hooks/exhaustive-deps` failure:
    - missing `chaosMode.latency`
    - missing `chaosMode.status`
    - missing `fixtureState`
- Impacted files/modules:
  - `src/context/ApiContext.jsx`
  - all request consumers
- Likely root cause:
  - broad context callback accumulating new responsibilities without dependency rework
- Blast radius:
  - request headers, latency injection, fixture behavior
- Blocks:
  - Release hardening

## 5. No request cancellation across the app data layer
- Severity: High
- Evidence:
  - no `AbortController` usage found in `src/`
  - `src/hooks/useData.js:21-47`, `76-100`, `114-132`
- Impacted files/modules:
  - `src/hooks/useData.js`
  - all polling/fetching routes
- Likely root cause:
  - homegrown fetch hooks without lifecycle hardening
- Blast radius:
  - opportunities, runs, paper, markets, correlation analysis, pipeline consumers
- Blocks:
  - Hardening

## 6. Route tree eagerly loads all pages into one main bundle
- Severity: High
- Evidence:
  - `src/App.jsx:9-30`
  - production build output:
    - `dist/assets/index-Dr9xPwmI.js` `993.92 kB` minified
    - `282.26 kB` gzip
- Impacted files/modules:
  - `src/App.jsx`
  - `vite.config.js`
  - all route modules
- Likely root cause:
  - route composition never moved to lazy imports
- Blast radius:
  - all users on initial load
- Blocks:
  - Hardening, likely release blocker for performance-sensitive deployment

## 7. Bundle budget policy currently normalizes an oversized bundle
- Severity: High
- Evidence:
  - `bundle-budget.json` baseline `950 kB`
  - `scripts/check-bundle-budget.js` passed current near-1 MB bundle
- Impacted files/modules:
  - `bundle-budget.json`
  - `scripts/check-bundle-budget.js`
- Likely root cause:
  - governance ratcheted upward instead of enforcing reduction
- Blast radius:
  - long-term bundle growth
- Blocks:
  - Hardening

## 8. God-component concentration in core trading routes
- Severity: High
- Evidence:
  - `src/pages/Pipeline.jsx` - 1153 LOC
  - `src/pages/Opportunities.jsx` - 1094 LOC
  - `src/pages/RunDetail.jsx` - 835 LOC
  - `src/pages/ControlPlane.jsx` - 826 LOC
  - `src/pages/PaperTrading.jsx` - 732 LOC
- Impacted files/modules:
  - core route surfaces
- Likely root cause:
  - feature growth without page-local extraction boundaries
- Blast radius:
  - maintainability, regressions, merge conflicts
- Blocks:
  - Hardening

## 9. `RunDetail` contains render-defined component logic in a hot route
- Severity: High
- Evidence:
  - `src/pages/RunDetail.jsx:569-583` defines `SortHeader` inside `TradesTab`
- Impacted files/modules:
  - `src/pages/RunDetail.jsx`
- Likely root cause:
  - page-level accumulation of view logic and controls
- Blast radius:
  - route readability, testability, rerender overhead
- Blocks:
  - Hardening

## 10. Direct fetches bypass the shared API boundary
- Severity: High
- Evidence:
  - `src/pages/Opportunities.jsx:319-321`
  - `src/pages/SimulationResults.jsx:137-148`
  - `src/components/pipeline/ChatPanel.jsx:458-473`
  - `src/utils/logger.js:123-128`
  - `src/utils/publicJson.js`
- Impacted files/modules:
  - opportunities export
  - simulation export
  - chat
  - logging
  - public JSON loader
- Likely root cause:
  - no formalized API-client boundary for download/public/vendor flows
- Blast radius:
  - auth headers, retries, error handling, observability
- Blocks:
  - Hardening

## 11. Test topology is fragmented and partially unwired
- Severity: High
- Evidence:
  - `package.json` wires `node --test tests/pf.unit.test.js`
  - `src/hooks/__tests__/useNodeVisualState.test.js` imports `vitest`
  - `npm ls vitest` returns empty
- Impacted files/modules:
  - `package.json`
  - `src/hooks/__tests__/useNodeVisualState.test.js`
- Likely root cause:
  - tooling drift between experimental and active runners
- Blast radius:
  - shared hook and component regression coverage
- Blocks:
  - Hardening

## 12. Playwright harness is misaligned with the app runtime
- Severity: High
- Evidence:
  - `playwright.config.js:3` declares unused `baseURL`
  - `playwright.config.js:22-33` hardcodes `http://127.0.0.1:3000` and launches `npm run dev -- --port 3000`
  - observed E2E failure: `Timed out waiting 120000ms from config.webServer`
  - manual `npm run dev -- --port 3000` switched to `3001` because `3000` was occupied
  - `VITE_SKIP_AUTH=true` is passed but not consumed anywhere in `src/`
- Impacted files/modules:
  - `playwright.config.js`
  - test scripts
- Likely root cause:
  - convenience-first dev-server testing plus drifted assumptions
- Blast radius:
  - E2E credibility
- Blocks:
  - Hardening

## 13. Generated mock output breaks lint
- Severity: Medium
- Evidence:
  - `src/mocks/generated/native.js:3` contains TypeScript syntax inside `.js`
  - ESLint parse error on that file
- Impacted files/modules:
  - generated mocks
  - lint pipeline
- Likely root cause:
  - generation output not aligned with repo JS tooling
- Blast radius:
  - lint reliability
- Blocks:
  - Hardening

## 14. Stylelint is misconfigured for the actual Tailwind stack
- Severity: Medium
- Evidence:
  - `.stylelintrc.json` has no Tailwind-aware configuration
  - `src/index.css` begins with `@tailwind base/components/utilities`
  - `npm run lint:css` reports `@tailwind` as unknown
- Impacted files/modules:
  - `.stylelintrc.json`
  - `src/index.css`
- Likely root cause:
  - generic Stylelint standard config applied to a Tailwind/PostCSS project
- Blast radius:
  - CSS gate signal-to-noise ratio
- Blocks:
  - Hardening

## 15. `useData` has real dependency and lifecycle defects
- Severity: Medium
- Evidence:
  - `src/hooks/useData.js:21-47`
  - `src/hooks/useData.js:76-100`
  - `src/hooks/useData.js:114-132`
  - lint errors for missing `params` and `endpoint` dependencies
- Impacted files/modules:
  - `src/hooks/useData.js`
  - all consumers
- Likely root cause:
  - generic fetch hook built without robust request-lifecycle semantics
- Blast radius:
  - stale data, polling correctness
- Blocks:
  - Hardening

## 16. Shared UI contract drift has already produced a real page bug
- Severity: Medium
- Evidence:
  - `src/pages/PaperTrading.jsx:57-80` passes `subValue` and `valueColor` props to `StatCard`
  - `src/components/ui.jsx:417-438` `StatCard` only supports `{ label, value, change, trend, icon }`
- Impacted files/modules:
  - `src/pages/PaperTrading.jsx`
  - `src/components/ui.jsx`
- Likely root cause:
  - kitchen-sink primitive evolved without typed contract or shared tests
- Blast radius:
  - at least paper-trading metrics display
- Blocks:
  - Hardening

# 5. Master remediation program
## Epic A: Secret handling and trust boundary hardening
- Objective:
  - remove privileged secrets and direct vendor trust from the browser
- Rationale:
  - current secret handling is a release blocker
- Exact areas/files likely involved:
  - `src/context/ApiContext.jsx`
  - `src/components/pipeline/ChatPanel.jsx`
  - `src/pages/Settings.jsx`
  - `src/context/SubscriptionContext.jsx`
  - `src/components/PaidRoute.jsx`
  - `src/pages/Pipeline.jsx`
  - `src/pages/Upgrade.jsx`
  - `src/pages/CheckoutSuccess.jsx`
  - `src/utils/returnTo.js`
  - `src/utils/logger.js`
  - `public/_headers`
- Concrete tasks:
  - remove API-key persistence from browser storage
  - remove Anthropic key persistence and browser-direct vendor invocation
  - replace pipeline client password gate with either:
    - server-backed capability gating, or
    - an explicit disabled/hidden preview mode
  - clarify that paid route is UX gating only unless server entitlement exists
  - sanitize and centralize `returnTo` handling
  - scrub logs/debug helpers of config coupling and secret leakage
  - add CSP once unsafe direct-call paths are removed
- Prerequisite tasks:
  - define backend bootstrap/session contract
- Risks:
  - temporary loss of local-dev convenience
  - possible UX changes in settings/chat flows
- Validation criteria:
  - no secrets in `localStorage` or `sessionStorage`
  - no browser-direct LLM vendor calls
  - no hardcoded client password gates
  - no logging path that depends on browser-stored API config
- Estimated implementation complexity:
  - High
- Merge risk:
  - High around config/session files

## Epic B: API/context/data-flow remediation
- Objective:
  - split the monolithic runtime provider model into explicit session/config, request-client, and dev-environment layers
- Rationale:
  - correctness risk and long-term modularity both hinge on this cleanup
- Exact areas/files likely involved:
  - `src/context/ApiContext.jsx`
  - `src/hooks/useData.js`
  - `src/utils/publicJson.js`
  - `src/utils/logger.js`
  - new `src/api/*` or `src/services/*` modules
- Concrete tasks:
  - extract transport into a dedicated request client
  - normalize success/error envelopes
  - add abort/cancellation support
  - define retry/backoff policy for safe read paths
  - isolate dev-only fixture/chaos flags outside the production request path
  - narrow provider value shapes to reduce rerender churn
  - update route consumers to use domain hooks rather than raw context verbs
- Prerequisite tasks:
  - Epic A trust-boundary decisions on auth/session/bootstrap
- Risks:
  - hidden consumer dependencies
  - widespread integration surface
- Validation criteria:
  - `ApiContext` no longer owns transport + persistence + dev flags in one place
  - request cancellation exists
  - error shaping is consistent across critical routes
  - fixture/chaos behavior is unavailable or isolated in production
- Estimated implementation complexity:
  - High
- Merge risk:
  - High

## Epic C: Page/component decomposition and ownership cleanup
- Objective:
  - reduce god pages into route shells plus owned submodules
- Rationale:
  - most maintainability risk is concentrated in a few oversized routes
- Exact areas/files likely involved:
  - `src/pages/RunDetail.jsx`
  - `src/pages/Opportunities.jsx`
  - `src/pages/OpportunitySurvey.jsx`
  - `src/pages/OpportunityDetail.jsx`
  - `src/pages/ControlPlane.jsx`
  - `src/pages/PaperTrading.jsx`
  - `src/pages/SimulationResults.jsx`
  - `src/components/ui.jsx`
- Concrete tasks:
  - extract route-specific components, selectors, and view-model hooks
  - remove render-defined helpers like `SortHeader`
  - move export/download behavior into dedicated action modules
  - move dense filters/forms into local subcomponents
  - align heavy pages around one data orchestration entry point each
  - split `ui.jsx` into focused primitives
- Prerequisite tasks:
  - API interfaces from Epic B need to be stable or shimmed
- Risks:
  - behavior drift on dense trading surfaces
- Validation criteria:
  - page files materially smaller
  - route ownership is obvious by folder structure
  - extracted sections can be tested independently
- Estimated implementation complexity:
  - High
- Merge risk:
  - Medium if split by route family

## Epic D: Performance, routing, and rendering overhaul
- Objective:
  - reduce startup bundle size, isolate heavy routes, and make render cost measurable
- Rationale:
  - current bundle and eager route model will not scale
- Exact areas/files likely involved:
  - `src/App.jsx`
  - `src/main.jsx`
  - `vite.config.js`
  - `scripts/check-bundle-budget.js`
  - `bundle-budget.json`
  - `src/pages/Pipeline.jsx`
  - `src/hooks/usePipelineGraph.js`
  - `src/components/opportunities/OpportunityCurveChart.jsx`
  - `src/pages/MarketDetail.jsx`
  - `src/workers/layoutWorker.js`
- Concrete tasks:
  - lazy-load route modules
  - introduce route suspense fallbacks
  - manual chunk heavy libraries/pages
  - separate pipeline/chart-heavy surfaces from startup shell
  - profile rerenders in pipeline and chart surfaces
  - reduce unnecessary memo churn and view-model recomputation
  - replace permissive bundle budget with downward-trending budgets
- Prerequisite tasks:
  - route/auth/session contract stable enough for lazy shell changes
- Risks:
  - route-loading UX regressions
  - hidden side effects exposed by lazy imports
- Validation criteria:
  - main startup bundle materially reduced
  - heavy routes loaded on demand
  - profiling shows reduced render churn in targeted hotspots
- Estimated implementation complexity:
  - High
- Merge risk:
  - Medium-High around `App.jsx`

## Epic E: Tooling, test, and lint truth restoration
- Objective:
  - make lint/test/build output reflect actual product risk
- Rationale:
  - current tooling is partial, noisy, and drifted
- Exact areas/files likely involved:
  - `package.json`
  - `eslint.config.js`
  - `.stylelintrc.json`
  - `playwright.config.js`
  - `knip.json`
  - `src/hooks/__tests__/useNodeVisualState.test.js`
  - `src/mocks/generated/native.js`
  - `tests/**`
  - `scripts/generate-mocks.js`
- Concrete tasks:
  - choose one component/unit runner and wire it
  - move or delete orphaned Vitest tests
  - repair generated mock output or exclude it from human-owned lint
  - run Playwright against build/preview for at least smoke coverage
  - split smoke, density, stress, and full suites clearly
  - include `build` and real unit coverage in `hardening:check`
  - clean up dead files/exports/deps from knip output
- Prerequisite tasks:
  - none for the baseline pass
- Risks:
  - initial increase in failing gates
- Validation criteria:
  - coherent script topology
  - E2E starts reliably
  - lint output is mostly real defects
  - dead-code/dependency drift materially reduced
- Estimated implementation complexity:
  - Medium
- Merge risk:
  - Low-Medium

## Epic F: Styling and CSS/tooling alignment
- Objective:
  - align Stylelint with the actual Tailwind/PostCSS stack and narrow global CSS scope
- Rationale:
  - styling gate credibility is currently compromised
- Exact areas/files likely involved:
  - `.stylelintrc.json`
  - `src/index.css`
  - `src/styles/tokens.css`
  - `tailwind.config.js`
  - `postcss.config.js`
- Concrete tasks:
  - configure Tailwind-aware Stylelint behavior
  - scope density-token enforcement to operator surfaces only
  - reduce `index.css` to primitives and truly global concerns
  - move feature-owned classes closer to their surfaces
- Prerequisite tasks:
  - none
- Risks:
  - low
- Validation criteria:
  - `lint:css` failures are real rather than config noise
- Estimated implementation complexity:
  - Medium
- Merge risk:
  - Low

## Epic G: Reliability, observability, and UX safeguards
- Objective:
  - standardize failure, loading, stale-data, and telemetry behavior on critical routes
- Rationale:
  - trading software must fail clearly and predictably
- Exact areas/files likely involved:
  - `src/App.jsx`
  - `src/main.jsx`
  - `src/utils/logger.js`
  - `src/components/StaleDataGhost.jsx`
  - `src/components/TraceViewer.jsx`
  - critical page routes
- Concrete tasks:
  - route/feature-level error boundaries
  - standard async-state components and conventions
  - stale-data indicators for core trading routes
  - telemetry redaction policy
  - critical-flow QA checklist
- Prerequisite tasks:
  - Epic B normalized request/error behavior
- Risks:
  - moderate because behavior becomes more explicit
- Validation criteria:
  - each critical route has loading, refreshing, empty, stale, and error states
  - telemetry is structured and non-secret
- Estimated implementation complexity:
  - Medium-High
- Merge risk:
  - Medium

## Epic H: Optional strategic improvements
- Objective:
  - prepare the frontend for a future API-first, swappable-UI architecture
- Rationale:
  - not required for immediate hardening, but enabled by earlier cleanup
- Exact areas/files likely involved:
  - new domain service/adaptor modules
  - route/domain folder structure
  - API schema validation entry points
- Concrete tasks:
  - formalize domain adapters over raw transport payloads
  - document route and domain ownership
  - optionally introduce transport schema validation
- Prerequisite tasks:
  - Epics A-D substantially landed
- Risks:
  - scope creep
- Validation criteria:
  - UI modules consume domain models rather than raw backend payloads
- Estimated implementation complexity:
  - Medium-High
- Merge risk:
  - Medium

# 6. Parallelization strategy for 5 Codex instances
This split is optimized to reduce overlap on shared files. Where overlap cannot be avoided, ownership is explicit and sequencing is defined.

## Worker 1 scope
- Mission:
  - Security, secrets, browser trust boundary, unsafe persistence, and direct third-party browser-call removal
- Exact boundaries:
  - Own browser secret-removal and browser trust-boundary cleanup only
  - Do not re-architect general data fetching beyond what is necessary to remove unsafe secret handling
- Files/modules to own:
  - `Frontend/src/components/pipeline/ChatPanel.jsx`
  - `Frontend/src/pages/Pipeline.jsx` only for access-gating/security-boundary changes
  - `Frontend/src/context/SubscriptionContext.jsx`
  - `Frontend/src/components/PaidRoute.jsx`
  - `Frontend/src/pages/Upgrade.jsx`
  - `Frontend/src/pages/CheckoutSuccess.jsx`
  - `Frontend/src/utils/returnTo.js`
  - `Frontend/public/_headers`
- Files/modules to avoid touching:
  - `Frontend/src/context/ApiContext.jsx`
  - `Frontend/src/hooks/useData.js`
  - `Frontend/src/App.jsx`
  - `Frontend/vite.config.js`
  - `Frontend/package.json`
  - route decomposition files owned by Worker 3
- Prerequisites:
  - agree on whether the immediate remediation is:
    - disable unsafe chat features, or
    - replace them with a backend proxy contract stub
- Expected outputs:
  - no browser-stored Anthropic key
  - no browser-direct Anthropic call path
  - no hardcoded client password gate treated as security
  - sanitized and centralized `returnTo` handling
  - tightened browser headers
- Risk of merge conflicts:
  - Medium
- Recommended branch name:
  - `remediate/w1-security-trust-boundary`
- Recommended sequencing:
  - first
- Dependency edges to other workers:
  - informs Worker 2 about final bootstrap/session assumptions
  - Worker 4 must not touch `Pipeline.jsx` until Worker 1's security gate change is settled

## Worker 2 scope
- Mission:
  - API layer, config flow, context/provider correctness, request lifecycle, stale closure/state correctness
- Exact boundaries:
  - Own runtime config, transport, and request-lifecycle architecture
  - Provide stable consumer hooks/adapters for the rest of the app
- Files/modules to own:
  - `Frontend/src/context/ApiContext.jsx`
  - `Frontend/src/hooks/useData.js`
  - `Frontend/src/pages/Settings.jsx`
  - `Frontend/src/utils/publicJson.js`
  - `Frontend/src/utils/logger.js` only if needed for transport decoupling, otherwise coordinate with Worker 1
  - new `Frontend/src/api/*` or `Frontend/src/services/*` modules
- Files/modules they must avoid touching:
  - `Frontend/src/components/pipeline/ChatPanel.jsx`
  - `Frontend/src/pages/Pipeline.jsx`
  - `Frontend/src/App.jsx`
  - `Frontend/package.json`
  - route decomposition files except narrow consumer rewires after interfaces are stable
- Prerequisites:
  - Worker 1 establishes trust-boundary direction for stored config/session semantics
- Expected outputs:
  - narrower provider architecture
  - request client with abort/error normalization
  - fixture/chaos isolation
  - removal of the `apiRequest` stale-closure defect
- Risk of merge conflicts:
  - High around `ApiContext.jsx` and `Settings.jsx`
- Recommended branch name:
  - `remediate/w2-api-context-dataflow`
- Recommended sequencing:
  - start after Worker 1's contract note, merge second
- Dependency edges to other workers:
  - Worker 3 should consume Worker 2's stable interfaces
  - Worker 4 should avoid route shell changes that depend on old config semantics

## Worker 3 scope
- Mission:
  - Page/component decomposition, god components, ownership boundaries, shared UI architecture
- Exact boundaries:
  - Own route-level decomposition for page families
  - Avoid security, provider, and build-tooling rewrites
- Files/modules they should own:
  - `Frontend/src/pages/RunDetail.jsx`
  - `Frontend/src/pages/Opportunities.jsx`
  - `Frontend/src/pages/OpportunitySurvey.jsx`
  - `Frontend/src/pages/OpportunityDetail.jsx`
  - `Frontend/src/pages/ControlPlane.jsx`
  - `Frontend/src/pages/PaperTrading.jsx`
  - `Frontend/src/pages/SimulationResults.jsx`
  - `Frontend/src/components/ui.jsx`
  - extracted page-local folders/components/hooks created from those surfaces
- Files/modules they must avoid touching:
  - `Frontend/src/context/ApiContext.jsx`
  - `Frontend/src/hooks/useData.js`
  - `Frontend/src/pages/Pipeline.jsx`
  - `Frontend/src/App.jsx`
  - root config/tooling files
- Prerequisites:
  - Worker 2 must expose stable data/client interfaces or compatibility shims
- Expected outputs:
  - materially smaller route files
  - extracted sections/selectors/hooks
  - resolved UI-contract drift like the `PaperTrading` `StatCard` mismatch
- Risk of merge conflicts:
  - Medium-Low if kept route-local
- Recommended branch name:
  - `remediate/w3-page-component-ownership`
- Recommended sequencing:
  - parallel after Worker 2 interface freeze
- Dependency edges to other workers:
  - Worker 4 benefits from route splits but should not block on them unless a shared hotspot is involved

## Worker 4 scope
- Mission:
  - Performance, routing, code splitting, render efficiency, bundle strategy, heavy-surface optimization
- Exact boundaries:
  - Own route shell and performance work
  - Measure changes rather than speculative memoization
- Files/modules they should own:
  - `Frontend/src/App.jsx`
  - `Frontend/src/main.jsx` only if needed for route suspense/bootstrap composition
  - `Frontend/vite.config.js`
  - `Frontend/scripts/check-bundle-budget.js`
  - `Frontend/bundle-budget.json`
  - `Frontend/src/hooks/usePipelineGraph.js`
  - `Frontend/src/components/opportunities/OpportunityCurveChart.jsx`
  - `Frontend/src/pages/MarketDetail.jsx`
  - `Frontend/src/workers/layoutWorker.js`
- Files/modules they must avoid touching:
  - `Frontend/src/context/ApiContext.jsx`
  - `Frontend/src/pages/Settings.jsx`
  - `Frontend/src/components/pipeline/ChatPanel.jsx`
  - Worker 3-owned page files unless there is a pre-agreed hotspot extraction
- Prerequisites:
  - route/bootstrap assumptions from Workers 1 and 2 need to be settled before merge
- Expected outputs:
  - lazy-loaded routes
  - reduced startup JS
  - lowered and credible bundle budget
  - profiled optimization of chart/graph hotspots
- Risk of merge conflicts:
  - Medium-High around `App.jsx`
- Recommended branch name:
  - `remediate/w4-performance-routing-bundles`
- Recommended sequencing:
  - can start early on `App.jsx` + build config, merge after Worker 2 checkpoint
- Dependency edges to other workers:
  - final route shell must rebase on Worker 2 if protected-route semantics change

## Worker 5 scope
- Mission:
  - Tooling, lint/test pipeline, dependency hygiene, styling/tooling alignment, repo cleanup
- Exact boundaries:
  - Own build/test/lint config and test harnesses
  - Avoid business-logic refactors except for minimal testability hooks
- Files/modules they should own:
  - `Frontend/package.json`
  - `Frontend/eslint.config.js`
  - `Frontend/.stylelintrc.json`
  - `Frontend/tailwind.config.js`
  - `Frontend/postcss.config.js`
  - `Frontend/playwright.config.js`
  - `Frontend/knip.json`
  - `Frontend/src/index.css`
  - `Frontend/src/mocks/generated/native.js`
  - `Frontend/scripts/generate-mocks.js`
  - `Frontend/tests/**`
  - `Frontend/src/hooks/__tests__/**`
- Files/modules they must avoid touching:
  - feature route logic except minimal selector/test-hook additions coordinated with file owner
  - `Frontend/src/App.jsx`
  - `Frontend/src/context/ApiContext.jsx`
- Prerequisites:
  - none
- Expected outputs:
  - truthful lint/test scripts
  - Tailwind-aware CSS lint
  - coherent unit/component runner
  - E2E harness that actually starts and targets a production-like runtime
  - dead code/dependency issues materially reduced
- Risk of merge conflicts:
  - Low-Medium
- Recommended branch name:
  - `remediate/w5-tooling-test-css-hygiene`
- Recommended sequencing:
  - start immediately, merge baseline fixes early, re-run final ratchet after Workers 1-4
- Dependency edges to other workers:
  - final CI matrix should land after structural work

## Shared conflict zones and tactics
- Highest-risk shared files:
  - `Frontend/src/context/ApiContext.jsx`
  - `Frontend/src/pages/Settings.jsx`
  - `Frontend/src/App.jsx`
  - `Frontend/package.json`
  - `Frontend/playwright.config.js`
  - `Frontend/src/index.css`
- Tactics to minimize collision:
  - freeze `ApiContext.jsx` ownership to Worker 2 after Worker 1 documents trust-boundary assumptions
  - freeze `App.jsx` to Worker 4
  - freeze root config files to Worker 5
  - avoid touching huge page files outside their owner unless explicitly coordinated
  - use temporary adapter layers rather than simultaneous consumer rewrites

# 7. Dependency graph and sequencing
## What must happen first
1. Define the browser trust-boundary end-state.
2. Remove browser-direct vendor secret paths and false security mechanisms.
3. Decide what protected-route bootstrap means after secret removal.

## What can happen in parallel
- Worker 5 can begin immediately.
- Worker 4 can start lazy-route and build-config groundwork immediately.
- Worker 3 can begin page decomposition on isolated route families once Worker 2 publishes stable interfaces or adapters.

## What must be merged before other work begins
- Worker 1's trust-boundary decisions should merge before Worker 2 finalizes config/session flow.
- Worker 2's stable client/provider interfaces should merge before Worker 3 completes consumer rewires.
- Worker 4's `App.jsx` changes should merge after Worker 2's protected-route semantics settle.

## What can be validated independently
- Worker 5:
  - lint/test harness reliability
  - Tailwind/stylelint alignment
  - test runner topology
- Worker 4:
  - bundle stats
  - route chunk map
  - render profiling
- Worker 3:
  - page-level manual parity checks

## Critical path
1. Security/trust boundary (Worker 1)
2. API/context/data-flow stabilization (Worker 2)
3. Route shell and bundle strategy (Worker 4)
4. Page decomposition on stable interfaces (Worker 3)
5. Final tooling ratchet and CI truth pass (Worker 5)

## Parallel paths
- Worker 5 runs throughout
- Worker 4 can front-load build/route work
- Worker 3 can work on route-local decomposition that does not depend on provider internals

## Merge checkpoints
- Checkpoint A:
  - trust-boundary contract approved
- Checkpoint B:
  - API client/provider split approved
- Checkpoint C:
  - route lazy-loading and bundle budgets approved
- Checkpoint D:
  - core god pages decomposed
- Checkpoint E:
  - tooling/test/lint final ratchet approved

## Rollback points
- Roll back browser-direct chat removal separately from broader provider refactor if backend proxy is not yet available.
- Roll back lazy route imports separately from page decomposition if route loading regresses.
- Keep tooling config changes isolated from business logic so they can be reverted independently.

## Areas where temporary freeze on shared files may be needed
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/pages/Settings.jsx`
- `Frontend/src/App.jsx`
- `Frontend/package.json`
- `Frontend/playwright.config.js`
- `Frontend/src/index.css`

# 8. File-level intervention map
Top priority heatmap.

| File/module | Why problematic | Intervention type |
| --- | --- | --- |
| `Frontend/src/context/ApiContext.jsx` | secret persistence, stale-closure defect, oversized provider, transport/config/dev-state coupling | harden, split, rewire, test |
| `Frontend/src/components/pipeline/ChatPanel.jsx` | browser vendor key, direct vendor call, oversized panel logic | harden, replace, isolate |
| `Frontend/src/pages/Pipeline.jsx` | 1153 LOC, false security gate, graph/panel/orchestration all in one file | split, isolate, optimize, harden |
| `Frontend/src/App.jsx` | static imports of all routes, one broad boundary, protected-route coupling | lazy-load, rewire, harden |
| `Frontend/src/hooks/useData.js` | no abort, missing deps, broad polling/fetch semantics | rewire, harden, test |
| `Frontend/src/pages/Opportunities.jsx` | 1094 LOC, direct export fetch, selection/filter/modal/export logic mixed | split, rewire, isolate |
| `Frontend/src/pages/RunDetail.jsx` | 835 LOC, render-defined helper, analytics + UI mixed | split, isolate, test |
| `Frontend/src/pages/ControlPlane.jsx` | 826 LOC, multiple admin forms and queue logic in one file | split, isolate |
| `Frontend/src/pages/PaperTrading.jsx` | 732 LOC, async flow density, UI primitive mismatch | split, fix, test |
| `Frontend/src/components/opportunities/OpportunityCurveChart.jsx` | heavy chart surface, hook dependency issues, bundle weight | optimize, lazy-load, isolate |
| `Frontend/src/hooks/usePipelineGraph.js` | 545 LOC graph state machine with caching and derived sets | isolate, test, optimize |
| `Frontend/src/components/ui.jsx` | kitchen-sink primitive file, shared contract drift | split, replace, document |
| `Frontend/src/pages/OpportunitySurvey.jsx` | 797 LOC, URL/fetch/reveal/chart logic all mixed | split, isolate |
| `Frontend/src/pages/SimulationResults.jsx` | direct CSV fetch, derived state duplication, shareable logic not extracted | rewire, align, split |
| `Frontend/src/pages/OpportunityDetail.jsx` | dense detail + trace rendering | split, isolate |
| `Frontend/src/components/pipeline/NodeDetailPanel.jsx` | effect dependency issues, panel data orchestration | split, rewire |
| `Frontend/src/components/pipeline/PipelineToolbar.jsx` | 579 LOC, mixed modes, unused state, inline fetches | split, isolate |
| `Frontend/src/components/pipeline/RecentActivity.jsx` | effect-driven loading state and grouping in one panel | rewire, test |
| `Frontend/src/components/pipeline/VersionEventTimeline.jsx` | effect/state issues, derived tree work | harden, test, optimize |
| `Frontend/src/pages/Settings.jsx` | config UX tied to insecure storage model | harden, rewire |
| `Frontend/src/context/SubscriptionContext.jsx` | client-side entitlement persistence | document, harden, rewire |
| `Frontend/src/utils/logger.js` | config coupling via local storage, best-effort shipping without clear governance | harden, isolate |
| `Frontend/src/utils/publicJson.js` | direct fetch path outside common request lifecycle | isolate, rewire |
| `Frontend/public/_headers` | missing CSP and incomplete hardening headers | harden |
| `Frontend/playwright.config.js` | unused baseURL var, dev-server targeting, startup brittleness | reconfigure, harden |
| `Frontend/.stylelintrc.json` | Tailwind misalignment, over-broad token enforcement | reconfigure |
| `Frontend/src/index.css` | global CSS sprawl + lint noise | narrow, relocate, align |
| `Frontend/src/mocks/generated/native.js` | invalid generated JS for repo lint toolchain | replace, reconfigure, isolate |
| `Frontend/package.json` | fragmented script topology and incomplete hardening pipeline | rewire |
| `Frontend/scripts/check-bundle-budget.js` | budget policy is too permissive | replace, tighten |
| `Frontend/bundle-budget.json` | baseline normalized to near-1 MB main chunk | ratchet downward |
| `Frontend/knip.json` | drift hints and ignore noise | reconfigure |
| `Frontend/scripts/generate-mocks.js` | lint failures and generated-code drift | harden, reconfigure |
| `Frontend/src/components/Layout.jsx` | effect/setState issue and layout-state coupling | harden, isolate |
| `Frontend/src/components/MarketingLayout.jsx` | effect dependency issue | harden |

# 9. Definition of done
The frontend is "brought into line" when all of the following are true:

- No secrets or privileged API keys are persisted in `localStorage` or `sessionStorage`.
- No direct browser LLM vendor calls remain, and no privileged vendor headers are sent from the browser.
- Protected-route bootstrap is based on an approved session/capability model, not presence of a browser-stored API key.
- `ApiContext` is materially reduced or replaced by narrower providers/modules.
- `useData` or its replacement supports abort/cancellation and standardized error shaping.
- Direct fetch bypasses for authenticated or privileged flows are removed or explicitly isolated behind approved adapters.
- `lint:js` passes cleanly, or only with documented, narrow exceptions.
- `lint:css` is aligned to Tailwind/PostCSS and reports mostly real defects.
- The test runner topology is coherent:
  - one active component/unit runner
  - Playwright reliably starts
  - orphaned runner drift removed
- `hardening:check` includes:
  - JS lint
  - CSS lint
  - dependency/dead-code hygiene
  - unit/component tests
  - build
  - E2E smoke
- Route-level lazy loading is in place for heavy pages.
- Bundle budget is explicitly lower than the current effective baseline and trending down.
- Major god components are decomposed:
  - `Pipeline`
  - `Opportunities`
  - `RunDetail`
  - `ControlPlane`
  - `PaperTrading`
- The stale-closure and dependency issues in `ApiContext` and `useData` are eliminated.
- Critical routes have defined loading, refreshing, empty, stale, and error states.
- Critical trading flows are manually regression-tested.

# 10. Validation plan
## Static validation
- Run:
  - `npm run lint:js`
  - `npm run lint:css`
  - `npm run lint:knip`
  - `npm run test:unit` or its replacement
  - `npm run build`
- Add route/chunk reporting to build validation.
- Fail the build if secret-storage or direct-vendor patterns are found.

## Unit / integration / E2E validation
- Unit/component:
  - request client behavior
  - `usePipelineGraph`
  - extracted selectors and route subcomponents
  - shared primitive contracts like `StatCard`
- Integration:
  - settings/bootstrap flow
  - protected-route resolution
  - cancellation on rapid filter changes
  - route fallback modes for live vs preview/demo data
- E2E:
  - run smoke tests against built/preview assets
  - keep optional dev-fast suites separate if needed

## Security validation
- Search the codebase for:
  - `localStorage`
  - `sessionStorage`
  - `x-api-key`
  - `anthropic-dangerous-direct-browser-access`
  - direct third-party vendor URLs
- Validate browser storage after key flows:
  - settings save/connect
  - pipeline open
  - upgrade/checkout success
- Confirm CSP and header changes are active in deployment config.

## Performance validation
- Track:
  - main startup JS size
  - per-route chunk sizes
  - render counts / profiling on:
    - `Pipeline`
    - `OpportunityCurveChart`
    - `RunDetail`
    - `Opportunities`
- Compare before/after build stats.

## Manual QA focus areas
- Settings connection flow
- Paid gate / protected-route flow
- Opportunities filter, selection, CSV export
- Run detail tabs, sorting, cancel action
- Simulation results export
- Control plane run creation and queue cancel
- Paper trading open/close flows
- Pipeline side panels, overlays, recent activity, chat fallback
- Market detail charts

## Regression focus areas specific to trading workflows
- stale data after fast filter changes
- polling refresh correctness
- route changes while requests are in flight
- chart redraw correctness
- consistency between run detail, simulation results, and exported data
- failure modes during partial backend outage
- separation between preview/demo data and live data

# 11. Fast wins vs foundational work
## Immediate high-value quick wins
- Disable or replace the browser-direct Anthropic path.
- Stop persisting privileged keys/config in browser storage.
- Remove false client password security for pipeline.
- Add lazy route imports in `src/App.jsx`.
- Fix Tailwind/Stylelint alignment.
- Fix Playwright startup and target `preview`/built assets.
- Add `build` and unit/component coverage to `hardening:check`.
- Fix generated mock parse issue or exclude it from human-owned lint.

## Medium-effort structural fixes
- Split `ApiContext`.
- Rebuild `useData` around a real request client.
- Decompose `RunDetail`, `Opportunities`, `ControlPlane`, and `PaperTrading`.
- Split `ui.jsx`.
- Standardize route-level loading/error/stale patterns.

## Deeper architectural moves
- server-mediated bootstrap/session contract
- domain adapter layer over raw transport payloads
- route-family folders and API-first module boundaries
- measured render-budget discipline for graph/chart-heavy surfaces

# 12. Risks, tradeoffs, and non-goals
## Where refactor risk is high
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/App.jsx`
- `Frontend/src/pages/Pipeline.jsx`
- `Frontend/src/pages/Opportunities.jsx`

## Where preserving behavior may conflict with cleanup
- Settings/protected-route flow:
  - current behavior depends on browser-stored config
- Pipeline chat:
  - secure remediation may temporarily remove a feature until backend support exists
- Paid gating:
  - clarifying UX-only semantics may change expectations around local activation

## Where a full rewrite is not justified
- The route structure is salvageable.
- Build tooling exists and can be repaired.
- Shared component islands can be extracted incrementally.
- Debt is concentrated, not uniformly catastrophic.

## What should be postponed to phase 2
- large-scale state-library migration
- broad design-system repackaging
- speculative framework rewrite
- fully typed transport/domain layer if it delays trust-boundary remediation

# 13. Deliverables for execution handoff
## A. One-page master execution summary
Bring the frontend into production shape in four waves. First, close the browser trust boundary: remove browser-stored secrets, remove browser-direct vendor calls, replace false client-side security, and clarify entitlement semantics. Second, split the oversized API/provider model into explicit config/session, request client, and dev-environment layers with abort/error handling. Third, reduce the highest-risk page files into route shells and owned submodules so future changes stop colliding in the same 800-1100 line files. Fourth, land route-level lazy loading, lower the bundle budget, and restore truthful lint/test/build gates. Keep `ApiContext.jsx`, `App.jsx`, `package.json`, `playwright.config.js`, and `index.css` under explicit owner freezes during the program.

## B. Backlog-style task list
1. Remove backend API key persistence from browser storage.
2. Remove Anthropic key persistence and direct browser Anthropic calls.
3. Remove or replace the client-side pipeline password gate.
4. Add CSP and finalize browser hardening headers.
5. Redefine protected-route bootstrap away from browser-stored API-key presence.
6. Split `ApiContext` into narrower modules/providers.
7. Add abort/cancellation and normalized error shaping to the request layer.
8. Isolate fixture/chaos controls from production runtime.
9. Remove direct authenticated fetch bypasses from route components and utilities.
10. Add lazy-loading for heavy routes in `App.jsx`.
11. Add chunk strategy and lower the bundle budget.
12. Decompose `RunDetail`.
13. Decompose `Opportunities`.
14. Decompose `ControlPlane`.
15. Decompose `PaperTrading` and align shared UI primitive contracts.
16. Optimize `OpportunityCurveChart` and `usePipelineGraph`.
17. Repair generated mock output and align lint ownership.
18. Choose and wire one component/unit test runner.
19. Make Playwright run reliably against a production-like target.
20. Expand manual QA coverage across critical trading workflows.

## C. Five copy-paste worker prompts
### Worker 1 prompt
Remediate frontend security and browser trust-boundary issues only.

Own these files/modules:
- `Frontend/src/components/pipeline/ChatPanel.jsx`
- `Frontend/src/pages/Pipeline.jsx` only for access-gating/security cleanup
- `Frontend/src/context/SubscriptionContext.jsx`
- `Frontend/src/components/PaidRoute.jsx`
- `Frontend/src/pages/Upgrade.jsx`
- `Frontend/src/pages/CheckoutSuccess.jsx`
- `Frontend/src/utils/returnTo.js`
- `Frontend/public/_headers`

Do not touch:
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/hooks/useData.js`
- `Frontend/src/App.jsx`
- tooling/build/test config
- route decomposition files owned by Worker 3

Mission:
- remove browser-stored Anthropic key handling
- remove browser-direct Anthropic invocation
- eliminate false client-side pipeline password security
- sanitize and centralize `returnTo`
- clarify client-side entitlement semantics
- tighten browser response headers

Validation:
- no `mcas_anthropic_key`
- no direct vendor URL calls from the browser
- no hardcoded client password gate used as access control
- route behavior preserved unless the behavior is unsafe

### Worker 2 prompt
Remediate API layer, config flow, provider correctness, and request lifecycle only.

Own these files/modules:
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/hooks/useData.js`
- `Frontend/src/pages/Settings.jsx`
- `Frontend/src/utils/publicJson.js`
- new `Frontend/src/api/*` or `Frontend/src/services/*` modules you create

Avoid:
- `Frontend/src/components/pipeline/ChatPanel.jsx`
- `Frontend/src/pages/Pipeline.jsx`
- `Frontend/src/App.jsx`
- root tooling config
- Worker 3 route decompositions except narrow consumer rewires after interface freeze

Mission:
- split broad provider responsibilities
- remove stale-closure defects
- add abort/cancellation, timeout, and normalized error handling
- isolate fixture/chaos behavior from the production path
- expose stable interfaces for route consumers

Validation:
- `ApiContext` no longer mixes transport, persistence, and dev controls in one broad module
- request lifecycle is deterministic
- consumer hooks remain stable or are shimmed

### Worker 3 prompt
Decompose god pages and clean up page/component ownership only.

Own these files/modules:
- `Frontend/src/pages/RunDetail.jsx`
- `Frontend/src/pages/Opportunities.jsx`
- `Frontend/src/pages/OpportunitySurvey.jsx`
- `Frontend/src/pages/OpportunityDetail.jsx`
- `Frontend/src/pages/ControlPlane.jsx`
- `Frontend/src/pages/PaperTrading.jsx`
- `Frontend/src/pages/SimulationResults.jsx`
- `Frontend/src/components/ui.jsx`
- new page-local components/hooks folders you create

Avoid:
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/hooks/useData.js`
- `Frontend/src/pages/Pipeline.jsx`
- `Frontend/src/App.jsx`
- build/test/lint config

Mission:
- turn page files into orchestrators
- extract sections, selectors, table config, modal content, and page-local hooks
- remove render-defined helper components
- fix shared UI contract drift without turning this into a design-system rewrite

Validation:
- route files are materially smaller
- behavior is preserved
- ownership boundaries are obvious from the file structure

### Worker 4 prompt
Handle performance, routing, code splitting, render efficiency, and bundle strategy only.

Own these files/modules:
- `Frontend/src/App.jsx`
- `Frontend/src/main.jsx` only if needed for suspense/bootstrap composition
- `Frontend/vite.config.js`
- `Frontend/scripts/check-bundle-budget.js`
- `Frontend/bundle-budget.json`
- `Frontend/src/hooks/usePipelineGraph.js`
- `Frontend/src/components/opportunities/OpportunityCurveChart.jsx`
- `Frontend/src/pages/MarketDetail.jsx`
- `Frontend/src/workers/layoutWorker.js`

Avoid:
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/pages/Settings.jsx`
- `Frontend/src/components/pipeline/ChatPanel.jsx`
- Worker 3-owned page files

Mission:
- add route-level lazy loading
- add suspense boundaries
- reduce startup bundle size
- lower and enforce bundle budgets
- profile and optimize graph/chart-heavy render hotspots

Validation:
- build shows route chunks
- startup JS is lower than the current baseline
- optimization claims are backed by measurements

### Worker 5 prompt
Restore tooling, lint/test pipeline truth, dependency hygiene, and styling/tooling alignment only.

Own these files/modules:
- `Frontend/package.json`
- `Frontend/eslint.config.js`
- `Frontend/.stylelintrc.json`
- `Frontend/tailwind.config.js`
- `Frontend/postcss.config.js`
- `Frontend/playwright.config.js`
- `Frontend/knip.json`
- `Frontend/src/index.css`
- `Frontend/src/mocks/generated/native.js`
- `Frontend/scripts/generate-mocks.js`
- `Frontend/tests/**`
- `Frontend/src/hooks/__tests__/**`

Avoid:
- feature-route logic except minimal testability/selectors agreed with file owners
- `Frontend/src/App.jsx`
- `Frontend/src/context/ApiContext.jsx`

Mission:
- make lint/test/build gates truthful
- align Stylelint to Tailwind/PostCSS
- choose and wire a coherent component/unit runner
- make Playwright start reliably and target a production-like runtime
- reduce dead-code/dependency drift

Validation:
- scripts are coherent
- CSS lint signal is real
- E2E harness starts and runs
- unwired test-runner drift is eliminated
