# Worker 5: Tooling, Tests, Styling, And Repo Hygiene

You are Worker 5 for the MCAS frontend remediation program.

## Mission
Restore truthful lint/test/build hygiene, align styling tooling with Tailwind/PostCSS, and reduce dead-code/dependency drift.

## Own these files/modules
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

## Avoid touching
- feature route logic except minimal selectors/test hooks explicitly coordinated with the owning worker
- `Frontend/src/App.jsx`
- `Frontend/src/context/ApiContext.jsx`

## Confirmed problems to fix
- JS lint currently fails broadly
- CSS lint treats Tailwind directives as unknown
- Playwright times out waiting for its web server
- Playwright is wired to `vite dev`, not a production-like target
- `vitest` tests exist without `vitest` installed
- generated mock output contains invalid JS for the repo lint toolchain
- dead-code/dependency drift exists per knip

## Required outcomes
- Coherent script topology
- Tailwind-aware Stylelint configuration
- One clear component/unit test runner strategy
- Playwright harness that starts reliably and targets a production-like runtime for smoke coverage
- Generated mocks either produce valid JS or are isolated from human-owned lint
- Knip output materially reduced and documented

## Constraints
- Do not refactor business logic as a substitute for tooling cleanup
- If a feature file needs a small test selector/hook, coordinate with its owner instead of taking ownership of the file

## Recommended execution pattern
1. Fix the test/lint runner topology first.
2. Align Stylelint with Tailwind/PostCSS.
3. Repair Playwright startup/runtime assumptions.
4. Clean up generated/mock ownership boundaries.
5. Re-run and document remaining real failures.

## Validation
- `npm run lint:js`
- `npm run lint:css`
- `npm run lint:knip`
- active unit/component test runner command
- `npm run build`
- Playwright smoke against a reliable target

## Deliverables
- Code changes within owned scope
- Short report covering:
  - what was config noise vs real defect
  - which dead files/deps were removed
  - final script/test matrix
