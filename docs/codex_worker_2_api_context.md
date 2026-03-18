# Worker 2: API Layer And Context Correctness

You are Worker 2 for the MCAS frontend remediation program.

## Mission
Remediate API layer design, config flow, provider/context correctness, request lifecycle, and stale state/closure issues.

## Own these files/modules
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/hooks/useData.js`
- `Frontend/src/pages/Settings.jsx`
- `Frontend/src/utils/publicJson.js`
- new `Frontend/src/api/*` or `Frontend/src/services/*` modules you create

## Avoid touching
- `Frontend/src/components/pipeline/ChatPanel.jsx`
- `Frontend/src/pages/Pipeline.jsx`
- `Frontend/src/App.jsx`
- `Frontend/package.json`
- `Frontend/playwright.config.js`
- Worker 3-owned route decompositions except for minimal consumer rewires after interface freeze

## Confirmed problems to fix
- `ApiContext` mixes config persistence, transport, connectivity, fixture state, chaos mode, and API verbs
- `apiRequest` has missing dependencies and stale-closure risk around `chaosMode` / `fixtureState`
- `useData.js` has lifecycle and dependency issues
- no centralized abort/cancellation support
- direct public JSON loading bypasses a coherent request policy

## Required outcomes
- Narrower provider/module boundaries
- Stable request client abstraction with:
  - abort/cancellation
  - normalized errors
  - explicit transport ownership
- `ApiContext` no longer acts as a god object
- Dev-only fixture/chaos behavior is isolated from normal production request paths
- `Settings.jsx` consumes the new config/session contract cleanly

## Constraints
- Do not re-open Worker 1's browser trust-boundary decisions
- Do not own route decomposition
- Do not own bundle strategy or route lazy loading
- Preserve consumer behavior with adapters/shims if a direct cutover would create excessive merge risk

## Recommended execution pattern
1. Freeze and map the current public interface of `useApi()` and `useData.js`.
2. Extract a request client module.
3. Move dev-only flags out of the transport hot path.
4. Repair the dependency/lifecycle defects.
5. Leave compatibility wrappers if downstream pages are not yet migrated.

## Validation
- Lint for hook dependency issues in your owned files
- Verify request cancellation on route/filter churn
- Verify error objects are consistent across read and action flows
- Verify `Settings` still supports the intended bootstrap flow under the new model

## Deliverables
- Code changes within owned scope
- Brief migration note for Worker 3 and Worker 4:
  - new client/hook interfaces
  - removed interfaces
  - any temporary adapters still in place
