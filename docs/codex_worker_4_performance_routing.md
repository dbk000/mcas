# Worker 4: Performance, Routing, And Bundle Strategy

You are Worker 4 for the MCAS frontend remediation program.

## Mission
Improve performance posture, route loading, bundle strategy, and heavy-surface rendering with measured changes only.

## Own these files/modules
- `Frontend/src/App.jsx`
- `Frontend/src/main.jsx` only if needed for suspense/bootstrap composition
- `Frontend/vite.config.js`
- `Frontend/scripts/check-bundle-budget.js`
- `Frontend/bundle-budget.json`
- `Frontend/src/hooks/usePipelineGraph.js`
- `Frontend/src/components/opportunities/OpportunityCurveChart.jsx`
- `Frontend/src/pages/MarketDetail.jsx`
- `Frontend/src/workers/layoutWorker.js`

## Avoid touching
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/pages/Settings.jsx`
- `Frontend/src/components/pipeline/ChatPanel.jsx`
- Worker 3-owned page files unless a narrow shared hotspot is explicitly agreed

## Confirmed problems to fix
- All routes are eagerly imported in `App.jsx`
- Production build emits a single near-1 MB main chunk
- Bundle budget policy normalizes current oversized output
- Heavy chart/graph logic remains in startup path and/or expensive runtime paths

## Required outcomes
- Route-level lazy loading for heavy pages
- `Suspense` fallbacks for route families
- Lower and credible bundle budgets
- Measured render improvements for owned hotspots
- Build output shows real route chunks rather than one giant startup bundle

## Constraints
- Do not redesign API/session/security semantics
- Do not turn this into a speculative memoization sweep
- Every performance change should be backed by measurable impact:
  - bundle size
  - render count/profile
  - route load timing

## Recommended execution pattern
1. Introduce lazy imports in `App.jsx`.
2. Add Vite chunk strategy.
3. Rework bundle-budget policy so it trends downward.
4. Profile and optimize owned heavy surfaces.
5. Document before/after stats.

## Validation
- `npm run build`
- bundle size diff
- route chunk map
- profiler evidence or concrete render-metric before/after notes for:
  - `OpportunityCurveChart`
  - `usePipelineGraph`
  - any owned heavy path you change

## Deliverables
- Code changes within owned scope
- Before/after build stats summary
- Short note on:
  - new route split points
  - new chunking behavior
  - any shared file freeze needed around `App.jsx`
