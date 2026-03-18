# Worker 4 Performance / Rendering Update

## Summary
- Tightened `SimulationResults` render discipline by stabilizing demo defaults, collapsing repeated result derivations into memoized passes, and making CSV export safer and less ad hoc.
- Tightened `CorrelationAnalysis` render discipline by memoizing heavy derived collections, converting poll cadence from fixed overlap-prone intervals to sequential polling, and isolating repeated run/feature rows behind memoized leaf components.
- Eliminated the owned-file JS lint failures in both owned pages without changing routing or shared build configuration.

## Files Touched
- `Frontend/src/pages/SimulationResults.jsx`
- `Frontend/src/pages/CorrelationAnalysis.jsx`
- `docs/worker4_performance_rendering_update.md`

## Performance / Lint Issues Fixed

### `SimulationResults.jsx`
- Removed the unstable `rawResults` -> `useMemo` pattern that triggered `react-hooks/exhaustive-deps`.
- Hoisted the authored demo fallback object to module scope so the page no longer recreates that structure every render.
- Replaced multiple repeated `displayResults` scans with a single memoized derivation for:
  - winners / losers counts
  - exit-reason counts
  - avg daily PF inputs
- Memoized per-setup entry expansion instead of rebuilding `Object.entries(...)` on every render.
- Replaced the inline CSV export flow with a stable callback and explicit export state.
- Added guarded export error handling and reused the page-local toast flow for failures.

### `CorrelationAnalysis.jsx`
- Removed unused imports that were causing owned-file lint failures.
- Memoized:
  - merged/sorted available runs
  - selected-run lookup set
  - setup select options
  - top predictor list
  - exit reason breakdown rows
- Replaced `setInterval` polling with sequential `setTimeout` polling to avoid overlapping requests and stale poll races.
- Stabilized selection/filter/analyze handlers with `useCallback`.
- Introduced memoized leaf components for the run selector rows and feature bars so selection/expand changes do not force unnecessary rerender work across the full list.

## Behavior-Sensitive Areas
- `SimulationResults` CSV export still uses the same endpoint and auth header contract; the change is limited to better failure handling and stable UI state.
- `CorrelationAnalysis` polling still uses the same backend endpoints and state transitions, but requests are now serialized so a slow response does not overlap with the next poll tick.
- No route definitions, lazy-loading boundaries, Vite chunking rules, or shared API context behavior were changed in this pass.

## Shared / Conflict Files Intentionally Avoided
- `Frontend/src/App.jsx`
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/package.json`
- `Frontend/vite.config.js`
- Worker 3-owned pipeline/layout files

## Remaining Issues Outside This Boundary
- `npm run lint:js:full` still fails outside these owned pages. Remaining failures are in shared or other-worker-owned files including:
  - `src/components/FeedbackOnset.jsx`
  - `src/components/Layout.jsx`
  - `src/components/StaleDataGhost.jsx`
  - multiple `src/components/pipeline/*` files
  - `src/pages/Pipeline.jsx`
  - `src/pages/RunCompare.jsx`
  - `src/utils/logger.js`
- After this pass, `SimulationResults.jsx` and `CorrelationAnalysis.jsx` are clean under targeted ESLint.

## Validation Results

### Targeted owned-file lint
- `npx eslint src/pages/SimulationResults.jsx src/pages/CorrelationAnalysis.jsx`
- Result: passed

### Full JS lint
- `npm run lint:js:full`
- Result: still failing, but the remaining failures are outside this ownership boundary

### Unit tests
- `npm run test:unit`
- Result: passed

### Build
- `npm run build`
- Result: passed

## Build / Bundle Notes Relevant To Owned Files
- Route splitting remained intact.
- Final emitted page chunks for owned pages were:
  - `SimulationResults`: about `11.11 kB`
  - `CorrelationAnalysis`: about `13.24 kB`
- Startup and shared bundle policy remained within the enforced budget gate after these page-local changes.
