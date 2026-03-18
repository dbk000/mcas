# Worker 2 API / Context Update

## Summary

Applied targeted correctness fixes in the API/context layer and the two page-level export flows that were still bypassing the shared request client.

Main changes:

- Removed render-time ref mutation in `ApiContext` and moved config/dev-override ref synchronization into effects.
- Hardened raw-response handling in the shared request client so CSV/download endpoints still produce normalized backend error details.
- Replaced page-local `fetch(...)` CSV exports in `Opportunities` and `SimulationResults` with the shared `useApi().request(...)` client.
- Added abort-safe download lifecycle handling for those exports.
- Centralized filename parsing / browser download behavior in a small shared helper.

## Files Touched

- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/api/requestClient.js`
- `Frontend/src/api/downloads.js`
- `Frontend/src/pages/Opportunities.jsx`
- `Frontend/src/pages/SimulationResults.jsx`

## Correctness / API Issues Fixed

### 1. `ApiContext` ref correctness

`configRef.current` and `devOverridesRef.current` were being reassigned during render. That pattern is easy to miss and can trip lint correctness rules around ref mutation during render.

Fix:

- moved both ref updates into effects
- preserved the stable client memoization pattern
- kept the existing memory-only API key behavior intact

### 2. Shared client raw-response error normalization

The request client supported `responseType: 'raw'`, but error responses in that mode were throwing without parsing the error body first. That meant CSV/export endpoints could lose backend-provided error detail and fall back to weak `HTTP XXX` messages.

Fix:

- raw responses now parse error bodies before constructing `RequestError`
- JSON or text error detail from CSV endpoints now flows through normalized request errors

### 3. `Opportunities` CSV export bypass

`Opportunities.jsx` was manually building the CSV URL, attaching `X-API-Key`, and running a raw `fetch`.

Fix:

- moved export to `useApi().request('/v1/opportunities/csv', { params, responseType: 'raw' })`
- removed duplicated auth/header/config handling
- added abort cleanup for in-flight export requests
- preserved success/failure toast behavior and filename handling

### 4. `SimulationResults` CSV export bypass

`SimulationResults.jsx` was also issuing a raw `fetch` with duplicated auth/header logic and no abort cleanup.

Fix:

- moved export to `useApi().request(...)`
- added abort cleanup for in-flight export requests
- normalized error handling through `RequestError`
- added `runId`-aware polling enable/skip guards so polling does not run against an invalid route param

### 5. Shared browser download behavior

Filename parsing and anchor download code was duplicated in page-local flows.

Fix:

- added `src/api/downloads.js`
- centralized `Content-Disposition` filename extraction
- centralized object URL / anchor download handling

## Behavior-Sensitive Areas

- The API key remains memory-only for the current tab. This remediation did not reintroduce browser persistence.
- CSV exports now rely on the shared request client, so auth/config failures use the same normalized error path as the rest of the app.
- Export requests are aborted on unmount or when a newer export replaces an older one.
- `SimulationResults` polling is now explicitly gated by `runId`.

## Shared / Conflict Files Intentionally Avoided

No changes were made to:

- `src/App.jsx`
- `package.json`
- Vite / ESLint / Stylelint config
- broader pipeline/layout architecture files
- security UX copy / persistence wording outside the already-established memory-only API-key model

## Remaining Issues Outside My Boundary

`npm run lint:js:full` still fails in unrelated files outside this ownership boundary. Current failures include:

- `src/components/Layout.jsx`
- `src/components/FeedbackOnset.jsx`
- several `src/components/pipeline/*` files
- `src/hooks/useNodeVisualState.js`
- `src/pages/Pipeline.jsx`
- fast-refresh warnings in `src/context/SubscriptionContext.jsx` and `src/context/ToastContext.jsx`

These were not modified here.

## Validation Results

Required validation:

- `npm run lint:js:full`
  - failed outside this boundary with existing errors in layout/pipeline/shared files listed above
- `npm run test:unit`
  - passed
- `npm run build`
  - passed

Owned-surface validation:

- targeted ESLint on touched Worker 2 files passed:
  - `src/context/ApiContext.jsx`
  - `src/api/requestClient.js`
  - `src/api/downloads.js`
  - `src/pages/Opportunities.jsx`
  - `src/pages/SimulationResults.jsx`
