# MCAS Frontend Hardening 2.0 (Refined): The Operator Standard Plan

This plan implements a **high-fidelity, operator-grade hardening stack**. It prioritizes **presentation integrity**, **contract-bound stability**, and **mechanical density budgets** over speculative re-verification or novelty engineering.

The implementation is split across **3 Gemini instances** using a **Linear-Infra, Parallel-Domain** strategy.

---

## Objective
- [ ] **Presentation Integrity:** Audit payload shape, timestamp order, and trace completeness (validate the *view*, not the *math*).
- [ ] **Hybrid Mocking:** OpenAPI-generated mocks for conformance; authored fixtures for curated/premium/degraded states.
- [ ] **Mechanical Density:** Enforce strict operator-grade padding, row heights, and fold-visibility via CSS tokens and linting.
- [ ] **Feedback Onset:** Ensure <50ms visual acknowledgement on critical paths with tolerance for CI latency.
- [ ] **Failure Harness:** Local failure-injection via Caddy Chaos Headers (Infrastructure, not UI).

---

## Workstream 1: Core Hardening & Density (The Guardrails)
**Owner: Gemini 1**
*Focus: Build-time integrity, mechanical density budgets, and CLI-first DX.*

### Owned Files
- [ ] `Frontend/package.json` (Hardening scripts & CI gates)
- [ ] `Frontend/src/styles/tokens.css` (New: Density constraints)
- [ ] `Frontend/.eslintrc.cjs` (Mechanical Style rules)
- [ ] `Frontend/.stylelintrc.json` (New: Mechanical Density enforcement)
- [ ] `Frontend/bundle-budget.json` (New: Declared size exceptions)
- [ ] `Frontend/src/components/DevHUD.jsx` (New: Near-zero effort overlay)

### Required Changes
- **Mechanical Density Budgets:** 
    - Define strict tokens in `tokens.css` (e.g., `--op-card-padding: 1rem`, `--op-row-height: 2.5rem`).
    - **Operator Surface Scope:** Enforce rules only on components identified by `[data-operator-surface]` or `.op-surface` (Dashboard, Tables, Control, Run views).
    - Use **Stylelint** to reject hardcoded spacing or padding overrides that deviate from tokens within the identified scope.
- **Min Visible Rows Assertion:** 
    - Implement a Playwright test to assert that at least 8 full data rows (matching `.op-row`) are within the `390x844` viewport on the Strategy Dashboard and Run Detail surfaces, excluding headers and footers.
- **Near-Zero Effort DevHUD:** Implement a disposable, minimal `DevHUD.jsx` (max 100 lines) that surfaces Knip/A11y/Bundle warnings. If implementation exceeds "minimal effort," discard it; CLI/CI remains the sole authority.
- **Mechanical Bundle Governance:** 
    - Integrate `rollup-plugin-visualizer`. 
    - Fail PRs that increase main bundle size by >5% unless the exception is explicitly recorded in `bundle-budget.json` (storing the new baseline + rationale).
- **Dead Code:** Configure Knip as a mandatory CI gate.

---

## Workstream 2: Contract Stability & Integrity (The Fortress)
**Owner: Gemini 2**
*Focus: Presentation integrity, hybrid mocking, and failure-injection harness.*

### Owned Files
- [ ] `Frontend/src/utils/integrity.js` (New: Shape & Order Audit)
- [ ] `Frontend/src/mocks/` (Generated Handlers + Authored Fixtures)
- [ ] `Frontend/Caddyfile` (Chaos Infrastructure)
- [ ] `Frontend/src/context/ApiContext.jsx` (Failure-Injection Wiring)

### Required Changes
- **Presentation Integrity Audit:** Implement `integrity.js` returning a standardized status enum: `ok | warn_shape | warn_order | warn_incomplete`.
    - **Shape:** Schema conformance.
    - **Order:** E.g., `decision_ts >= signal_bar_close_ts`.
    - **Incomplete:** Missing required Glass Box fields.
- **Integrity Non-Goals (Strict):**
    - NO frontend inference of strategy correctness.
    - NO re-calculation of Profit Factor (PF).
    - NO re-computation of Glass Box causality logic.
- **Data Consistency Warnings:** Trigger "Inconsistent Data" UI states based on the integrity enum, rather than attempting re-computation.
- **Hybrid Mocking:** 
    - Automate MSW handler generation from OpenAPI.
    - Retain and prioritize **authored fixtures** for curated winners, premium empty states, and complex degradation demos.
- **Local-Only Failure Harness:** Configure Caddy Chaos Headers (500/403/latency). Wire `ApiContext` to allow toggling these headers **strictly in local development**. Ensure this toggle is stripped or disabled in production builds.

---

## Workstream 3: Operator Journey & Feedback Onset (The Stress Runner)
**Owner: Gemini 3**
*Focus: Interaction onset latency, visual diffs, and mobile stress.*

### Owned Files
- [ ] `Frontend/playwright.config.js` (Visual & Latency Config)
- [ ] `Frontend/tests/visual-regressions.spec.js` (New)
- [ ] `Frontend/src/components/FeedbackOnset.jsx` (New: Latency tracking shim)
- [ ] `Frontend/tests/operator-stress.spec.js` (New: Latency & Fold tests)

### Required Changes
- **Instant Feedback Onset:** 
    - Implement visual acknowledgement (button state, micro-spinner) that starts within a threshold on critical flows (Upgrade, Run Start, Filter).
    - Measure onset in `operator-stress.spec.js` with tolerance bands (e.g., 50ms base + 100ms jitter) for CI variance.
- **Visual Regression (Playwright-only):** Implement screenshot assertions for Landing, Pricing, and Dashboard.
    - **CRITICAL DEPENDENCY:** Visual baselines must NOT be captured until Workstream 1 has stabilized the density tokens and spacing rules.
    - Use **authored fixtures** for stable, predictable baselines.
- **Informed Doubt Patterns:** Implement ghosting/dimming for stale data states with a precision stale-timer instead of generic spinners.
- **Mobile Fold Hardening:** 
    - Assert zero horizontal scroll and thumb-reachability on 390x844.
    - Assert primary CTA stability (no layout shift) on mobile.

---

## Implementation Order
1.  **Gemini 1 (Infra):** Establish Stylelint density rules and CI hardening scripts.
2.  **Gemini 2 (Fortress):** Implement the integrity audit and hybrid mocking.
3.  **Gemini 3 (Stress):** Implement visual diffs and feedback onset tests once stable fixtures and tokens are available.

## Final Definition of Done
- [ ] `integrity.js` catches malformed timestamps or missing Glass Box fields.
- [ ] Primary surfaces meet **Mechanical Density** (row count, Stylelint tokens).
- [ ] Critical CTAs show visual feedback onset within the threshold.
- [ ] Caddy Chaos Headers enable instant failure-injection testing in local dev.
- [ ] CI fails on dead code, bundle bloat (without exception), or broken contract conformance.
