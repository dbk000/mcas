# Frontend Remaining Closeout Plan

Validated against the current repo on 2026-03-16.

## Purpose

This document turns the remaining audit findings into a concrete closeout plan for the MCAS frontend. It is meant to be execution-ready: each workstream has a suggested owner, file scope, dependency order, acceptance criteria, and validation commands.

This plan covers only what is still open after the currently landed remediation work.

## Current Verified State

The following items are already in place:

- Browser-direct Anthropic usage is removed and chat is disabled until a server proxy exists.
- The old client-only pipeline password gate is removed.
- `returnTo` sanitization is centralized.
- The paid gate is explicitly labeled UX-only, not secure entitlement.
- Route-level lazy loading and suspense boundaries are in place.
- Vite chunking and bundle-budget enforcement are in place.
- Stylelint is aligned with Tailwind/PostCSS.
- Playwright now runs against preview instead of `vite dev`.
- `useData` and public JSON loading now support request cancellation.

The following current validations were run on 2026-03-16:

- `npm run build`: passed
- `npm run test:unit`: passed, 25/25
- `npx playwright test tests/full-ui.spec.js`: passed, 20/20
- `npm run lint:css`: passed
- `npm run lint:js:full`: passed
- `npm run lint:js`: failed on an unused import in `tests/full-ui.spec.js`
- `npm run lint:knip`: still reports 25 unused exports

## Remaining Gaps

The remaining work falls into six streams:

1. Release-gate truth restoration
2. Server-backed auth/bootstrap replacement
3. Final `ApiContext` split and transport cleanup
4. Telemetry trust-boundary cleanup
5. Remaining route decomposition
6. Accessibility closure

## Suggested Owners

Replace these labels with actual assignees if needed.

| Stream | Suggested Owner | Responsibility |
| --- | --- | --- |
| A | Frontend Tooling | Lint/test/build truth and repo hygiene |
| B | Frontend Runtime + Backend API | Session/bootstrap contract and route protection |
| C | Frontend Runtime | Provider split, request lifecycle, transport ownership |
| D | Frontend Runtime | Telemetry shipping and logging trust boundaries |
| E | Frontend Surfaces | Remaining page decomposition and component ownership |
| F | Design System / Accessibility | Shared primitive semantics and keyboard behavior |
| G | QA / Release | Final hardening pass and signoff |

## Execution Order

Follow this sequence. Some later steps can overlap, but the session/bootstrap contract must freeze before the runtime split is finalized.

1. Stream A: make the gates truthful
2. Stream B: replace browser-held auth/bootstrap
3. Stream C: split `ApiContext` around the new contract
4. Stream D: remove telemetry coupling to browser config
5. Stream E and Stream F in parallel once runtime interfaces are stable
6. Stream G: final hardening pass and signoff

## Stream A: Release-Gate Truth Restoration

### Objective

Make the default scripts reflect actual product readiness instead of partial health.

### File Scope

- `Frontend/package.json`
- `Frontend/tests/full-ui.spec.js`
- `Frontend/knip.json`
- any files touched to remove confirmed unused exports

### Tasks

- Remove the unused `API_CONFIG` import from `Frontend/tests/full-ui.spec.js`.
- Decide whether `lint:js` should remain a config/tests lint or become the real app-wide lint command.
- Recommended: make `lint:js` run the current `lint:js:full` scope and remove the split unless there is a strong reason to keep it.
- Clean the current Knip output, especially the 25 unused exports.
- After cleanup, change Knip `exports` from `warn` to `error`.
- Keep `hardening:check` pointed at the real lint command, not the partial one.

### Acceptance Criteria

- `npm run lint:js` passes and covers the application source.
- `npm run lint:knip` reports zero file/dependency/unlisted failures and no accepted export drift.
- `npm run hardening:check` can be used as a real release gate.

### Validation

```bash
npm run lint:js
npm run lint:css
npm run lint:knip
npm run test:unit
npm run build
npx playwright test tests/full-ui.spec.js
```

## Stream B: Server-Backed Auth And Bootstrap

### Objective

Replace browser-owned API auth/bootstrap with a server-backed session/bootstrap model.

### Why This Is Still Open

The frontend still protects app routes by checking `config.baseUrl` and `config.apiKey`, and the request client still sends `X-API-Key` from browser-held config. That does not satisfy the target state from the remediation docs.

### File Scope

- `Frontend/src/App.jsx`
- `Frontend/src/pages/Settings.jsx`
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/api/requestClient.js`
- new files such as:
  - `Frontend/src/context/SessionContext.jsx`
  - `Frontend/src/api/sessionClient.js`
  - `Frontend/src/hooks/useSession.js`

### Backend Contract To Freeze First

The frontend and backend need one agreed bootstrap shape before implementation starts. The exact route names can vary, but the frontend needs all of the following:

- whether a valid session exists
- whether the user has app access
- the backend base URL or resolved API origin if the frontend still needs it
- connection health/auth state as server-owned facts where possible
- whether entitlement is real or still preview-only

Recommended frontend assumption:

- browser stores only non-sensitive UX state
- backend stores secrets and authenticates requests
- frontend gets a bootstrap/session response and uses normal authenticated requests after that

### Tasks

- Introduce a server-backed bootstrap/session hook and provider.
- Replace `ProtectedRoute` logic in `Frontend/src/App.jsx` so it depends on session/bootstrap state instead of `config.apiKey`.
- Rework `Frontend/src/pages/Settings.jsx` so it configures or tests the backend through a server-mediated flow.
- Remove client attachment of `X-API-Key` from `Frontend/src/api/requestClient.js`.
- Keep any temporary compatibility layer behind a narrow adapter, not in route code.

### Acceptance Criteria

- Protected routes do not depend on browser-held privileged API credentials.
- The browser no longer acts as the source of truth for backend auth/bootstrap.
- No privileged API key is entered, stored, or forwarded from frontend route logic.

### Validation

- Manual verification of:
  - unpaid user flow
  - paid-but-unconfigured flow
  - configured session flow
  - reload behavior after session creation
- Existing Playwright smoke continues to pass after the route-protection change.

## Stream C: Final `ApiContext` Split And Transport Cleanup

### Objective

Finish the provider/runtime split so `ApiContext` no longer acts as a god object.

### Why This Is Still Open

`ApiContext` is much better than before, but it still owns config, connection testing, request verbs, dev override state, and connection status in one provider.

### File Scope

- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/hooks/useData.js`
- `Frontend/src/api/requestClient.js`
- `Frontend/src/api/errors.js`
- `Frontend/src/api/devOverrides.js`
- `Frontend/src/utils/publicJson.js`
- new files such as:
  - `Frontend/src/context/ConnectionContext.jsx`
  - `Frontend/src/api/apiClient.js`
  - `Frontend/src/hooks/useConnectionStatus.js`

### Tasks

- Split runtime concerns into at least:
  - session/bootstrap state
  - transport client
  - connection diagnostics
  - dev overrides
- Keep `useApi()` only as a temporary facade if it reduces churn during migration.
- Move connection-test logic out of the main transport provider.
- Keep normalized errors and abort behavior as non-negotiable requirements during refactor.
- Keep public JSON loading on the shared fetch/error primitives.

### Acceptance Criteria

- `ApiContext` no longer owns transport plus config plus diagnostics plus dev-only controls together.
- `useData` still supports abort/cancel behavior on route churn and polling.
- Settings and route consumers use stable domain-facing hooks instead of broad provider state.

### Validation

```bash
npm run lint:js
npm run test:unit
npm run build
```

Manual verification:

- rapid route changes do not leave stale data on screen
- polling routes cancel correctly
- error objects stay consistent in user-facing toasts and logs

## Stream D: Telemetry Trust-Boundary Cleanup

### Objective

Remove the last logging and telemetry dependencies on browser-held backend config.

### Why This Is Still Open

`logger.js` still reads the API base URL from `localStorage` and still ships logs with raw `fetch`.

### File Scope

- `Frontend/src/utils/logger.js`
- new files such as:
  - `Frontend/src/api/telemetryClient.js`

### Tasks

- Stop reading backend URL from browser storage for telemetry shipping.
- Move log shipping onto a dedicated telemetry client or the shared transport layer.
- Add explicit payload redaction before logs leave the browser.
- Confirm telemetry shipping still works across reloads and route changes without leaking config assumptions.

### Acceptance Criteria

- Telemetry no longer depends on `localStorage` API config.
- Telemetry uses the active runtime contract, not an out-of-band fetch path.
- No sensitive runtime config is logged or shipped.

### Validation

- manual verification with successful telemetry delivery
- manual verification with missing session/bootstrap state
- regression check that `window.__mcas.dumpLogs()` still works locally

## Stream E: Remaining Route Decomposition

### Objective

Finish the architecture cleanup so the remaining oversized routes become orchestrators instead of mini-apps.

### Remaining Priority Targets

1. `Frontend/src/pages/Pipeline.jsx`
2. `Frontend/src/pages/OpportunitySurvey.jsx`
3. `Frontend/src/pages/OpportunityDetail.jsx`
4. `Frontend/src/pages/SimulationResults.jsx`
5. `Frontend/src/pages/CorrelationAnalysis.jsx`

### Supporting Files Likely To Be Added

- `Frontend/src/pages/pipeline/*`
- `Frontend/src/pages/opportunity-survey/*`
- `Frontend/src/pages/opportunity-detail/*`
- `Frontend/src/pages/simulation-results/*`
- `Frontend/src/pages/correlation-analysis/*`

### Tasks

- Split each remaining route into:
  - route shell
  - view-model hook or selector layer
  - section components
  - pure leaf components
- Keep route files focused on orchestration and composition.
- Avoid creating new global abstractions unless the same contract is already shared across multiple routes.
- For `Pipeline`, isolate graph orchestration, panels, saved layout behavior, and route messaging into smaller modules without undoing the current lazy-loading and chunking wins.
- For `SimulationResults`, move the local stat-card and export flow out of the page file.
- For `OpportunityDetail` and `OpportunitySurvey`, split the long walkthrough and chart-related rendering logic into page-local modules.

### Acceptance Criteria

- Each target route is materially smaller.
- Render-defined helper logic is no longer concentrated in the top-level route body.
- Route-local ownership is obvious from folder structure.
- Existing behavior is preserved except where the prior behavior was already intentionally changed for safety.

### Validation

- manual parity check for:
  - pipeline node focus, side panels, and saved layout behavior
  - survey filters and reveal workflow
  - opportunity detail walkthrough and Glass Box rendering
  - simulation results pagination and CSV export
  - correlation analysis interactions
- bundle output does not regress beyond current budgets

## Stream F: Accessibility Closure

### Objective

Close the remaining shared-primitive accessibility gaps called out in the remediation docs.

### File Scope

- `Frontend/src/components/ui.jsx`
- route files or extracted feature components that consume `Modal`, `Tabs`, or `MultiSelect`
- tests added under `Frontend/tests/**` or `Frontend/src/hooks/__tests__/**`

### Tasks

- Upgrade `Modal` with dialog semantics:
  - `role="dialog"`
  - `aria-modal="true"`
  - labelled title association
  - focus trap
  - escape handling
  - focus return to trigger
- Upgrade `Tabs` with semantic tab behavior:
  - `role="tablist"`
  - `role="tab"`
  - `role="tabpanel"`
  - arrow-key navigation
  - `aria-selected`
  - `aria-controls`
- Rework `MultiSelect` into a keyboard-usable control with clearer semantics.
- Add focused tests around shared keyboard behavior and modal dismissal.

### Acceptance Criteria

- Shared overlay and tab primitives support keyboard-only interaction.
- Modal and tab patterns expose correct dialog/tab semantics.
- The current remediation doc accessibility findings are closed, not deferred.

### Validation

- manual keyboard-only walkthrough
- Playwright coverage for at least one modal and one tab surface

## Stream G: Final Hardening And Signoff

### Objective

Confirm the repo is genuinely over the line and tighten the remaining governance once the code changes are in.

### File Scope

- `Frontend/package.json`
- `Frontend/knip.json`
- any scripts or test harness files changed during closeout

### Tasks

- Re-run all hardening commands after Streams A-F land.
- Tighten any temporary compatibility shims if they are no longer needed.
- Keep the current bundle budget as the minimum bar. Only lower budgets after confirming the new baseline from the final merged state.
- Update documentation if bootstrap/session semantics changed materially.

### Acceptance Criteria

- `npm run hardening:check` passes without manual exceptions.
- Default scripts reflect real release readiness.
- No remaining audit item is still open in the critical or high-severity buckets.

### Final Validation Matrix

```bash
npm run lint:js
npm run lint:css
npm run lint:knip
npm run test:unit
npm run build
npx playwright test tests/full-ui.spec.js
npm run test:e2e:all
```

## Definition Of Done

The remaining remediation is complete when all of the following are true:

- app access is derived from a server-backed bootstrap/session model
- no privileged API credential is owned by frontend route protection
- `ApiContext` is no longer the runtime god object
- telemetry no longer depends on browser-stored backend config
- the remaining oversized routes are decomposed into local modules
- shared primitives close the current modal/tab/multi-select accessibility gaps
- default lint/test/build/e2e scripts are green and trustworthy

## Notes

- The highest-risk dependency is Stream B. Freeze the backend bootstrap/session contract before doing the final runtime split.
- Streams E and F should not block Stream B, but they should avoid taking new dependencies on the current `ApiContext` shape once Stream C starts.
- If time pressure forces triage, the minimum release path is:
  1. Stream A
  2. Stream B
  3. Stream C
  4. Stream D
  5. Stream G

