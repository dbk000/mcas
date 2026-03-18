# Worker 3: Page And Component Architecture

You are Worker 3 for the MCAS frontend remediation program.

## Mission
Decompose god pages, clean up page/component ownership, and reduce route-level complexity without changing global config or security architecture.

## Own these files/modules
- `Frontend/src/pages/RunDetail.jsx`
- `Frontend/src/pages/Opportunities.jsx`
- `Frontend/src/pages/OpportunitySurvey.jsx`
- `Frontend/src/pages/OpportunityDetail.jsx`
- `Frontend/src/pages/ControlPlane.jsx`
- `Frontend/src/pages/PaperTrading.jsx`
- `Frontend/src/pages/SimulationResults.jsx`
- `Frontend/src/components/ui.jsx`
- any new page-local folders/components/hooks you create for those routes

## Avoid touching
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/hooks/useData.js`
- `Frontend/src/pages/Pipeline.jsx`
- `Frontend/src/App.jsx`
- root lint/test/build config

## Confirmed problems to fix
- `RunDetail.jsx` contains render-defined helper components and analytics + view logic mixed together
- `Opportunities.jsx` is a 1000+ LOC route with filters, export, selection, simulation modal, and card rendering all mixed
- `ControlPlane.jsx` combines multiple operational workflows in one file
- `PaperTrading.jsx` has async flow density and a shared UI contract mismatch
- `ui.jsx` is too broad and has drifted shared contracts

## Required outcomes
- Route files become orchestrators rather than mini-apps
- Route-local subcomponents/hooks/selectors are extracted into clear ownership boundaries
- Render-defined helpers like `SortHeader` are removed from hot path route files
- Shared primitives are split or clarified enough to stop contract drift
- Behavior is preserved except where the current behavior is clearly broken

## Constraints
- Use Worker 2's stable hooks/interfaces; do not redesign the request layer yourself
- Do not touch bundle strategy, router shell, or security files
- Prefer page-local extraction over premature global abstractions

## Recommended execution pattern
1. Split each route into:
  - route shell
  - view-model hook/selectors
  - section components
  - pure leaf components
2. Remove export/download logic from giant JSX trees.
3. Normalize prop contracts between route components and shared primitives.
4. Add targeted tests where practical once sections are extracted.

## Validation
- Route files are materially smaller
- Manual parity check for:
  - run detail tabs and sorting
  - opportunities filters, selection, export, and modal flows
  - control plane run creation and queue actions
  - paper trading metrics and state transitions
- Shared UI contract mismatches are eliminated

## Deliverables
- Code changes within owned scope
- Short inventory of:
  - new folders/components/hooks created
  - page files materially reduced
  - remaining cross-route shared debt, if any
