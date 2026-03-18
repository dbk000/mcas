# Worker 1: Security And Trust Boundary

You are Worker 1 for the MCAS frontend remediation program.

## Mission
Remediate security and browser trust-boundary issues only.

## Own these files/modules
- `Frontend/src/components/pipeline/ChatPanel.jsx`
- `Frontend/src/pages/Pipeline.jsx` only for access-gating/security cleanup
- `Frontend/src/context/SubscriptionContext.jsx`
- `Frontend/src/components/PaidRoute.jsx`
- `Frontend/src/pages/Upgrade.jsx`
- `Frontend/src/pages/CheckoutSuccess.jsx`
- `Frontend/src/utils/returnTo.js`
- `Frontend/public/_headers`

## Avoid touching
- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/hooks/useData.js`
- `Frontend/src/App.jsx`
- `Frontend/vite.config.js`
- `Frontend/package.json`
- `Frontend/playwright.config.js`
- Worker 3-owned page decomposition files

## Confirmed problems to fix
- Browser-stored Anthropic key and direct browser Anthropic calls in `ChatPanel.jsx`
- Hardcoded client password gate in `Pipeline.jsx`
- Client-side entitlement semantics in `SubscriptionContext.jsx` / `PaidRoute.jsx`
- `returnTo` handling needs central sanitization
- Browser headers are incomplete and missing CSP

## Required outcomes
- No browser-stored Anthropic key
- No direct browser LLM vendor calls
- No client-side password gate treated as security
- Paid gating clearly documented/implemented as UX-only unless server-backed
- `returnTo` sanitization is centralized and consistently used
- `public/_headers` includes a credible CSP after unsafe browser vendor flows are removed

## Constraints
- Do not redesign the general request/data layer
- Do not change route lazy loading or bundle strategy
- Do not refactor unrelated page structure
- If a backend proxy/service does not exist yet, disable the unsafe browser-direct path rather than inventing a fake secure version

## Validation
- Search for:
  - `mcas_anthropic_key`
  - `anthropic-dangerous-direct-browser-access`
  - direct Anthropic URLs
  - client-only password gates
- Manually verify:
  - pipeline route behavior is safe
  - upgrade/checkout `returnTo` flow is sanitized
  - no sensitive values are persisted in browser storage by your owned files

## Deliverables
- Code changes only within your owned scope
- Short implementation note:
  - what unsafe browser trust paths were removed
  - what behavior changed intentionally for safety
  - what Worker 2 must assume about session/bootstrap semantics
