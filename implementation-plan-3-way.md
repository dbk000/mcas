# MCAS Frontend 3-Instance Implementation Plan

This document turns [deep-research-report(2).md](/home/darren/Documents/MCAS/deep-research-report(2).md) into an execution plan that can be split across 3 Codex instances without file conflicts.

Everything below is written as an implementation checklist, not a brainstorming note. If an item is not checked off later, it should be treated as unfinished.

## Objective

- [ ] Deliver the front-end-only MCAS subscription experience described in the report.
- [ ] Keep backend behavior unchanged.
- [ ] Split the existing authenticated SPA into public routes and paid app routes.
- [ ] Preserve the current API base URL + API key workflow for the paid app.
- [ ] Use [frontend_placeholders](/home/darren/Documents/MCAS/frontend_placeholders) as the canonical placeholder/demo data source for any desired feature that lacks a live backend source.
- [ ] Leave a concrete completion checklist so there is a clear definition of done.

## Non-Goals

- [ ] Do not add backend Stripe session creation, entitlement verification, or secure server-side subscription enforcement.
- [ ] Do not change API endpoints or backend schemas.
- [ ] Do not refactor unrelated UI systems.
- [ ] Do not centralize all routing into a new shared helper in this pass. That would create unnecessary shared ownership and merge risk.
- [ ] Do not ship weak lorem-ipsum placeholders. Placeholder data must be rich enough to support the final intended UI.

## Fixed Contracts

These are not optional. All 3 Codex instances should code against this exact contract.

### Route Contract

- [ ] Public routes are exactly: `/`, `/winners`, `/pricing`, `/upgrade`, `/checkout/success`, `/checkout/cancel`, `/settings`.
- [ ] Paid app routes are exactly: `/app`, `/app/strategies`, `/app/opportunities`, `/app/opportunities/survey`, `/app/opportunities/:id`, `/app/markets`, `/app/markets/:symbol`, `/app/runs`, `/app/runs/compare`, `/app/runs/:id`, `/app/runs/:id/simulation`, `/app/control`, `/app/paper`, `/app/analytics/correlations`, `/app/pipeline`.
- [ ] Unknown paths redirect to `/`, not `/app`.
- [ ] `/settings` remains public.
- [ ] Public routes must not render the existing app sidebar shell.
- [ ] App routes must render the existing app shell.

### Gating Contract

- [ ] The paid gate runs before the API-config gate.
- [ ] The paid gate is UX-only and must not claim secure enforcement.
- [ ] A paid user without API config still gets redirected to `/settings`.
- [ ] An unpaid user trying to visit `/app/*` sees a paywall with links to `/upgrade` and `/winners`.

### Storage and Env Contract

- [ ] Existing `mcas_api_config` localStorage behavior remains unchanged.
- [ ] New subscription key is `mcas_tier` with values `free` or `paid`.
- [ ] New activation timestamp key is `mcas_tier_activated_at` storing an ISO timestamp.
- [ ] Hosted checkout URL comes from `import.meta.env.VITE_STRIPE_PAYMENT_URL`.
- [ ] `CheckoutSuccess` sets `mcas_tier=paid` and `mcas_tier_activated_at=<ISO timestamp>` locally, but does not treat query params as proof of payment.

### Frontend Analytics Event Contract

- [ ] Frontend analytics events must be named and emitted even if no real sink is wired yet.
- [ ] Event names are pure front-end names only.
- [ ] Until a real analytics sink exists, the frontend must still provide a no-op or buffer-backed emitter so instrumented surfaces are real rather than aspirational.
- [ ] Minimum event names required by this plan are:
- [ ] `landing_cta_click`
- [ ] `winners_cta_click`
- [ ] `pricing_cta_click`
- [ ] `paywall_cta_click`
- [ ] `checkout_success_landed`
- [ ] `checkout_cancel_landed`
- [ ] `settings_configured`
- [ ] `strategy_card_clicked`
- [ ] Minimum shared frontend payload fields are:
- [ ] `route`
- [ ] `surface`
- [ ] `placement`
- [ ] `source_status`
- [ ] `viewport_bucket`
- [ ] `ts`
- [ ] CTA click events must also include:
- [ ] `cta_id`
- [ ] `cta_label`
- [ ] `href`
- [ ] `return_to_present`
- [ ] `tier`
- [ ] `strategy_card_clicked` must also include:
- [ ] `setup_id`
- [ ] `strategy_label`
- [ ] `rank`
- [ ] `live_count`
- [ ] `urgency_band`
- [ ] `settings_configured` must also include:
- [ ] `has_base_url`
- [ ] `has_api_key`
- [ ] `origin`
- [ ] If sink wiring is absent, the frontend must still preserve emitted events in an inspectable browser-side buffer for testing and debugging.

### Data and Metrics Contract

- [ ] `StrategyDashboard` uses existing `useStrategies()` and `useOpportunities({ status: 'active', min_score: 0, limit: 200 })`.
- [ ] `Glass Box` badge reuses `opp.sim_trace?.kline_lineage?.causality_verified`; no new causality logic is invented.
- [ ] Aggregate Profit Factor stays visible.
- [ ] Avg Daily PF is added alongside aggregate PF.
- [ ] Avg Daily PF is computed client-side from realized per-trade or per-result PnL grouped by day.
- [ ] Daily PF days with zero gross loss and positive gross profit are treated as infinite days.
- [ ] `averageDailyPF` averages only finite daily PF values. If there are no finite daily PF values but there is at least one infinite profitable day, the displayed value is `∞`. If there are no valid trading days, the displayed value is `—`.
- [ ] The UI tooltip/footnote explicitly states that days with no realized losses are excluded from the finite daily average to avoid meaningless infinite arithmetic means.

### Placeholder and Demo Data Contract

- [ ] Canonical placeholder authoring root is [frontend_placeholders](/home/darren/Documents/MCAS/frontend_placeholders).
- [ ] Any ambitious feature without a current live backend data source must have a placeholder pack in [frontend_placeholders](/home/darren/Documents/MCAS/frontend_placeholders) before UI implementation begins.
- [ ] Frontend components must not hide large ad-hoc placeholder objects inline. They must import from or sync from [frontend_placeholders](/home/darren/Documents/MCAS/frontend_placeholders).
- [ ] Placeholder data must be shaped to support the final intended UI, not just unblock rendering.
- [ ] Placeholder data must be rich enough for ranked tables, tapes, ladders, timing strips, state badges, methodology panels, and empty/error/fallback states.
- [ ] Placeholder packs currently required by this plan are:
- [ ] [frontend_placeholders/marketing/winners.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/winners.v1.json)
- [ ] [frontend_placeholders/marketing/proof-pack.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/proof-pack.v1.json)
- [ ] [frontend_placeholders/marketing/pricing-matrix.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/pricing-matrix.v1.json)
- [ ] [frontend_placeholders/marketing/onboarding-flow.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/onboarding-flow.v1.json)
- [ ] [frontend_placeholders/app/strategy-dashboard-demo.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/strategy-dashboard-demo.v1.json)
- [ ] [frontend_placeholders/app/glass-box-cases.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/glass-box-cases.v1.json)
- [ ] [frontend_placeholders/app/metrics-demo.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/metrics-demo.v1.json)
- [ ] [frontend_placeholders/manifests/placeholder-index.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/manifests/placeholder-index.v1.json)

### Dormant Surface Rule

- [ ] If a feature is conceptually part of the final subscription experience, it ships now in full UI form even when live backend data does not yet exist.
- [ ] Placeholder-backed surfaces must not ship as stubs, TODO blocks, or temporary throwaway layouts.
- [ ] A dormant surface must still include the final intended hierarchy, table density, CTA placement, proof strips, empty states, and interaction model.
- [ ] Each dormant surface must clearly declare its data source status as `live`, `placeholder`, or `degraded` without visually downgrading the surrounding product experience.
- [ ] When live data arrives later, the surface should swap sources without redesigning the page contract.

### Frontend Demand Contract

The frontend is not a passive consumer here. For every placeholder-backed surface, the plan must state the live payload shape the backend will eventually need to satisfy. These are demand specifications, not backend implementation instructions.

#### Winners Demand Spec

- [ ] Eventual live winners payload must satisfy:

```json
{
  "as_of_ts": "ISO-8601",
  "source_status": "live | placeholder | degraded",
  "strategies": [
    {
      "setup_id": "string",
      "label": "string",
      "description": "string",
      "rank": 1,
      "market_fit": ["string"],
      "items": [
        {
          "id": "string",
          "symbol": "string",
          "timeframe": "string",
          "direction": "LONG | SHORT",
          "date": "YYYY-MM-DD",
          "outcome_pct": 0,
          "holding_bars": 0,
          "quality_score": 0,
          "regime": "string | null",
          "why_it_mattered": "string",
          "notes": ["string"],
          "cta_tease": "string"
        }
      ]
    }
  ]
}
```

- [ ] Winners payload must include enough ranking, regime, and explanatory text to support dense ranked strategy sections rather than anonymous cards.
- [ ] Winners failure modes must be explicit:
- [ ] missing strategy list -> render curated-unavailable state with upgrade CTA and fallback placeholder pack
- [ ] malformed strategy item -> drop only the bad item and keep the section alive
- [ ] stale data -> show last-updated timestamp and source-state badge

#### Public Proof Demand Spec

- [ ] Eventual proof/trust payload must satisfy:

```json
{
  "as_of_ts": "ISO-8601",
  "source_status": "live | placeholder | degraded",
  "hero_metrics": [
    { "id": "string", "label": "string", "value": "string", "subtext": "string" }
  ],
  "glass_box_walkthrough": {
    "setup_id": "string",
    "symbol": "string",
    "timeframe": "string",
    "direction": "LONG | SHORT",
    "decision_ts": "ISO-8601",
    "causality_status": "VERIFIED | VIOLATION | UNKNOWN | N_A",
    "timeline": [
      { "phase": "string", "state": "verified | warning | error | neutral", "ts": "ISO-8601", "detail": "string" }
    ],
    "decision_inputs": ["string"]
  },
  "honest_pf_demo": {
    "aggregate_pf": 0,
    "avg_daily_pf": 0,
    "days_counted": 0,
    "days_excluded_infinite_profitable": 0,
    "method_note": "string"
  },
  "evidence_blocks": [
    { "id": "string", "eyebrow": "string", "headline": "string", "body": "string" }
  ]
}
```

- [ ] Proof payload must include both badge inputs and proof narrative inputs.
- [ ] Public proof failure modes must be explicit:
- [ ] missing walkthrough -> keep trust surface alive with methodology and evidence blocks
- [ ] missing hero metrics -> render labels with `N/A` values, not empty holes
- [ ] missing PF proof block -> show methodology fallback from placeholder pack

#### Pricing and Value-Stack Demand Spec

- [ ] Eventual pricing/value-stack payload must satisfy:

```json
{
  "plans": [
    {
      "id": "free | pro",
      "label": "string",
      "price_label": "string",
      "tagline": "string",
      "cta_label": "string",
      "cta_href": "string"
    }
  ],
  "value_stack": [
    {
      "id": "string",
      "headline": "string",
      "body": "string",
      "priority_rank": 1
    }
  ],
  "comparison_rows": [
    {
      "feature": "string",
      "free": "string",
      "pro": "string",
      "priority_rank": 1
    }
  ],
  "faq": [
    { "q": "string", "a": "string" }
  ]
}
```

- [ ] Pricing payload must be strong enough to support value-stack framing, comparison matrices, objections handling, and repeated CTAs.
- [ ] Pricing failure modes must be explicit:
- [ ] missing FAQ -> keep plans and matrix visible
- [ ] missing value stack -> fallback to authored placeholder pack and log degradation

#### Onboarding and Checkout Demand Spec

- [ ] Eventual onboarding/checkout payload must satisfy:

```json
{
  "steps": [
    { "id": "string", "label": "string", "headline": "string", "body": "string" }
  ],
  "success_routing": {
    "if_paid_and_configured": "returnTo or /app/strategies",
    "if_paid_and_unconfigured": "/settings?from=checkout-success",
    "if_return_to_missing": "/app/strategies"
  },
  "trust_copy": {
    "activation_scope": "string",
    "payment_proof": "string",
    "backend_step": "string"
  }
}
```

- [ ] Checkout payload must explicitly support `returnTo`, onboarding state, and trust-preserving entitlement copy.
- [ ] Onboarding failure modes must be explicit:
- [ ] missing `returnTo` -> route to `/app/strategies`
- [ ] missing payment URL -> disabled checkout CTA plus support fallback
- [ ] malformed tier state -> reset to `free` and show explicit reactivation path

#### Strategy Dashboard Demand Spec

- [ ] Eventual strategy dashboard payload must satisfy:

```json
{
  "as_of_ts": "ISO-8601",
  "source_status": "live | placeholder | degraded",
  "market_state": {
    "regime": "string",
    "breadth_score": 0,
    "volatility_state": "string",
    "cross_asset_note": "string"
  },
  "alerts_tape": ["string"],
  "leaderboard": [
    {
      "setup_id": "string",
      "label": "string",
      "status": "active | paused | degraded",
      "rank": 1,
      "live_count": 0,
      "verified_count": 0,
      "avg_score": 0,
      "freshest_signal_age_minutes": 0,
      "urgency_band": "very_high | high | medium | low",
      "dominant_regime": "string",
      "top_opportunities": [
        {
          "id": "string",
          "symbol": "string",
          "timeframe": "string",
          "direction": "LONG | SHORT",
          "score": 0,
          "rank": 1,
          "age_minutes": 0,
          "action_window_minutes": 0,
          "decision_ts": "ISO-8601",
          "causality_status": "VERIFIED | VIOLATION | UNKNOWN | N_A",
          "thesis": "string"
        }
      ]
    }
  ]
}
```

- [ ] Strategy dashboard payload must support ranking, freshness, urgency, tape strips, and compact opportunity ladders.
- [ ] Strategy dashboard failure modes must be explicit:
- [ ] strategies available but no opportunities -> render strategy cards with premium empty states and upgrade continuity
- [ ] opportunities available but labels missing -> bucket under `Unknown strategy` and log schema degradation
- [ ] partial strategy failure -> keep remaining strategy cards alive and mark source-state visibly

#### Glass Box Demand Spec

- [ ] Eventual Glass Box opportunity payload must satisfy:

```json
{
  "sim_trace": {
    "kline_lineage": {
      "causality_verified": true,
      "decision_ts": "ISO-8601",
      "bar_start_ts": "ISO-8601",
      "phases": [
        { "phase": "string", "state": "verified | warning | error | neutral", "ts": "ISO-8601", "detail": "string" }
      ]
    }
  }
}
```

- [ ] Badge inputs are mandatory: `causality_verified`, `decision_ts`, and `bar_start_ts`.
- [ ] Proof-walkthrough inputs are mandatory for the public trust surface: lineage phases and descriptive details.
- [ ] Glass Box failure modes must be explicit:
- [ ] lineage absent -> show `Glass Box: N/A`
- [ ] lineage present but state unknown -> show `Glass Box: Unknown`
- [ ] malformed timestamps -> keep badge but log degraded proof payload

#### Metrics Demand Spec

- [ ] Eventual metrics payload for run detail and simulation results must satisfy:

```json
{
  "summary": {
    "profit_factor": 0,
    "total_trades": 0,
    "net_pnl": 0
  },
  "items": [
    {
      "id": "string",
      "exit_ts": "ISO-8601",
      "created_at": "ISO-8601",
      "pnl_net": 0
    }
  ]
}
```

- [ ] Frontend demand is that backend eventually supply first-class `exit_ts` and `pnl_net` on realized items.
- [ ] `created_at` is a degraded fallback for simulation results, not the preferred long-term field.
- [ ] Metrics failure modes must be explicit:
- [ ] summary available but items absent -> render aggregate PF only with explicit note that Avg Daily PF is unavailable
- [ ] items present but timestamps missing -> show degraded note and suppress Avg Daily PF
- [ ] items present but `pnl_net` missing -> treat Avg Daily PF as unavailable and log schema failure

### Visual and Information-Density Contract

- [ ] The target visual standard is Bloomberg-terminal-grade scanability without copying Bloomberg's harshness.
- [ ] Marketing pages must still feel like operator software: dense proof strips, ranked surfaces, precise labels, visible state changes, and minimal generic SaaS fluff.
- [ ] Paid app surfaces must prioritize information density, freshness, urgency, and cross-panel coherence over oversized empty hero blocks.
- [ ] Placeholder data must enable a dense UI with real-looking ladders, tapes, compact cards, microcopy, and methodology callouts.

### Performance and Mobile Contract

- [ ] Public conversion pages are mobile-first surfaces, not desktop pages awkwardly shrunk down.
- [ ] On common phone sizes, no critical primary CTA may sit below the first meaningful fold on `/`, `/pricing`, or the unpaid paywall.
- [ ] Common phone sizes for this contract are at minimum `375x812`, `390x844`, and `393x852`.
- [ ] Public pages must have no horizontal scroll at those sizes.
- [ ] Landing, pricing, and paywall must not visibly shift layout after first render in a way that moves the primary CTA, hero headline, or top proof strip.
- [ ] Public pages must remain visually sharp for QR traffic: readable typography, intact spacing, and no overflow or clipped proof/CTA blocks on a cold mobile load.
- [ ] Mobile nav and CTA surfaces must remain thumb-reachable and visually clear without requiring the user to fight browser chrome.

### Trust Copy Contract

- [ ] Marketing and funnel copy must preserve trust without sounding apologetic or cheap.
- [ ] Public-facing surfaces must include `not financial advice` wording where appropriate, especially landing and winners.
- [ ] Copy must clearly distinguish `curated/free historical examples` from `paid/live app functionality`.
- [ ] Honest metrics copy must explicitly explain the relationship between aggregate PF and Avg Daily PF.
- [ ] Placeholder, live, and degraded source labels must be phrased in premium language:
- [ ] `Live feed`
- [ ] `Authored preview set`
- [ ] `Live feed temporarily unavailable, showing authored preview set`
- [ ] Avoid low-status language like `fake data`, `dummy data`, `just a mock`, or `placeholder only`.
- [ ] Paywall, pricing, winners, and upgrade copy must feel like access-limited operator software, not generic consumer subscription copy.

### Sales Pressure Contract

- [ ] The public side is not just informational. It must actively convert.
- [ ] Recurring upgrade CTAs must appear across landing, winners, pricing, paywall, checkout-cancel, and premium empty states.
- [ ] Value-stack framing on `/pricing` must clearly sell actionability, causality visibility, timing fidelity, and honest metrics rather than only listing features.
- [ ] Proof continuity must persist from landing -> winners -> paywall -> upgrade so each page compounds the same trust story rather than resetting it.
- [ ] Free/unpaid empty states must feel premium and intentional, like access-limited operator software rather than missing content.
- [ ] Mobile-first conversion behavior must be explicit for QR and link-scan traffic: primary CTA visible early, thumb-friendly controls, no horizontal scroll, and no critical proof block buried below dead space.

## Current-Code Facts This Plan Depends On

- [ ] [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx) currently treats `/` as the protected dashboard and wraps all routes in the app shell.
- [ ] [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx) currently owns sidebar navigation, top header, desktop collapse behavior, and quick-scan navigation.
- [ ] [Frontend/src/pages/OpportunityDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/OpportunityDetail.jsx) already renders `TraceViewer`, so only a header-level `Glass Box` badge needs to be surfaced.
- [ ] [Frontend/src/pages/RunDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunDetail.jsx) and [Frontend/src/pages/SimulationResults.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/SimulationResults.jsx) already show aggregate Profit Factor.
- [ ] [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js) currently assumes `/` is the dashboard and must be rewritten for the new route map.
- [ ] [Frontend/package.json](/home/darren/Documents/MCAS/Frontend/package.json) has no unit-test script today.
- [ ] [frontend_placeholders](/home/darren/Documents/MCAS/frontend_placeholders) now contains the canonical authored placeholder packs required by this plan.

## Ownership Rules

- [ ] Each file listed under a workstream below has exactly one owner.
- [ ] No instance edits another instance's owned file, even for a one-line route fix.
- [ ] Shared hotspot files are single-owner only: [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx), [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), [Frontend/package.json](/home/darren/Documents/MCAS/Frontend/package.json).
- [ ] Avoid touching [Frontend/src/hooks/useData.js](/home/darren/Documents/MCAS/Frontend/src/hooks/useData.js), [Frontend/src/context/ApiContext.jsx](/home/darren/Documents/MCAS/Frontend/src/context/ApiContext.jsx), and [Frontend/src/components/ui.jsx](/home/darren/Documents/MCAS/Frontend/src/components/ui.jsx) unless something is truly blocked.

## Workstream 1: Public Routing, Subscription UX, Public Pages

### Owner

- [ ] Codex 1

### Owned Files

- [ ] [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx)
- [ ] [Frontend/src/main.jsx](/home/darren/Documents/MCAS/Frontend/src/main.jsx)
- [ ] [Frontend/index.html](/home/darren/Documents/MCAS/Frontend/index.html)
- [ ] [Frontend/src/pages/Settings.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Settings.jsx)
- [ ] [Frontend/src/context/SubscriptionContext.jsx](/home/darren/Documents/MCAS/Frontend/src/context/SubscriptionContext.jsx)
- [ ] [Frontend/src/components/PaidRoute.jsx](/home/darren/Documents/MCAS/Frontend/src/components/PaidRoute.jsx)
- [ ] [Frontend/src/components/MarketingLayout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/MarketingLayout.jsx)
- [ ] [Frontend/src/utils/analytics.js](/home/darren/Documents/MCAS/Frontend/src/utils/analytics.js)
- [ ] [Frontend/src/utils/pageMeta.js](/home/darren/Documents/MCAS/Frontend/src/utils/pageMeta.js)
- [ ] [Frontend/src/pages/Landing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Landing.jsx)
- [ ] [Frontend/src/pages/Winners.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Winners.jsx)
- [ ] [Frontend/src/pages/Pricing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Pricing.jsx)
- [ ] [Frontend/src/pages/Upgrade.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Upgrade.jsx)
- [ ] [Frontend/src/pages/CheckoutSuccess.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutSuccess.jsx)
- [ ] [Frontend/src/pages/CheckoutCancel.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutCancel.jsx)
- [ ] [Frontend/src/pages/StrategyDashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/StrategyDashboard.jsx)
- [ ] [Frontend/public/curation/winners.v1.json](/home/darren/Documents/MCAS/Frontend/public/curation/winners.v1.json)
- [ ] [Frontend/public/favicon.svg](/home/darren/Documents/MCAS/Frontend/public/favicon.svg)
- [ ] [Frontend/public/site.webmanifest](/home/darren/Documents/MCAS/Frontend/public/site.webmanifest)
- [ ] [Frontend/public/social/og-default.png](/home/darren/Documents/MCAS/Frontend/public/social/og-default.png)
- [ ] [Frontend/public/social/og-winners.png](/home/darren/Documents/MCAS/Frontend/public/social/og-winners.png)
- [ ] [Frontend/public/social/og-pricing.png](/home/darren/Documents/MCAS/Frontend/public/social/og-pricing.png)
- [ ] [frontend_placeholders/README.md](/home/darren/Documents/MCAS/frontend_placeholders/README.md)
- [ ] [frontend_placeholders/manifests/placeholder-index.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/manifests/placeholder-index.v1.json)
- [ ] [frontend_placeholders/marketing/winners.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/winners.v1.json)
- [ ] [frontend_placeholders/marketing/proof-pack.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/proof-pack.v1.json)
- [ ] [frontend_placeholders/marketing/pricing-matrix.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/pricing-matrix.v1.json)
- [ ] [frontend_placeholders/marketing/onboarding-flow.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/onboarding-flow.v1.json)
- [ ] [frontend_placeholders/app/strategy-dashboard-demo.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/strategy-dashboard-demo.v1.json)

### Required Changes

- [ ] In [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx), split public routes from app routes so public pages do not render the app shell.
- [ ] In [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx), move the existing dashboard route from `/` to `/app`.
- [ ] In [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx), move every existing protected route under `/app/*`.
- [ ] In [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx), add the new public routes for landing, winners, pricing, upgrade, checkout success, and checkout cancel.
- [ ] In [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx), add `/app/strategies` and mount [Frontend/src/pages/StrategyDashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/StrategyDashboard.jsx).
- [ ] In [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx), wrap `/app/*` pages in `PaidRoute` and then the existing `ProtectedRoute`.
- [ ] In [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx), leave `/settings` public.
- [ ] In [Frontend/src/main.jsx](/home/darren/Documents/MCAS/Frontend/src/main.jsx), add `SubscriptionProvider` around `<App />` without changing existing provider order beyond that addition.
- [ ] In [Frontend/src/utils/analytics.js](/home/darren/Documents/MCAS/Frontend/src/utils/analytics.js), define the canonical frontend event names from this plan and a browser-side emitter that works even when no analytics sink exists yet.
- [ ] In [Frontend/src/utils/analytics.js](/home/darren/Documents/MCAS/Frontend/src/utils/analytics.js), preserve emitted events in an inspectable buffer for test and debug use when no real sink is wired.
- [ ] In [Frontend/src/utils/pageMeta.js](/home/darren/Documents/MCAS/Frontend/src/utils/pageMeta.js), implement per-page title, meta description, OG, and Twitter metadata helpers for public routes.
- [ ] In [Frontend/index.html](/home/darren/Documents/MCAS/Frontend/index.html), support default metadata, OG/Twitter tags, favicon references, and app-level polish needed for route-level overrides.
- [ ] In [Frontend/src/context/SubscriptionContext.jsx](/home/darren/Documents/MCAS/Frontend/src/context/SubscriptionContext.jsx), expose `tier`, `isPaid`, `setPaid`, `setFree`, and `clearTier`.
- [ ] In [Frontend/src/components/PaidRoute.jsx](/home/darren/Documents/MCAS/Frontend/src/components/PaidRoute.jsx), render a full-page paywall when unpaid, with benefits copy and buttons to `/upgrade` and `/winners`.
- [ ] In [Frontend/src/components/PaidRoute.jsx](/home/darren/Documents/MCAS/Frontend/src/components/PaidRoute.jsx), make the unpaid state feel premium and access-limited rather than empty, with proof continuity and repeated upgrade CTAs.
- [ ] In [Frontend/src/components/PaidRoute.jsx](/home/darren/Documents/MCAS/Frontend/src/components/PaidRoute.jsx), emit `paywall_cta_click` for upgrade and winners CTAs.
- [ ] In [Frontend/src/components/MarketingLayout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/MarketingLayout.jsx), build the public top nav and a mobile-friendly public-page container.
- [ ] In [Frontend/src/pages/Landing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Landing.jsx), implement the hero, value proposition, credibility strip, honest-metrics strip, CTA buttons, and disclaimer.
- [ ] In [Frontend/src/pages/Landing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Landing.jsx), consume [frontend_placeholders/marketing/proof-pack.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/proof-pack.v1.json) for public trust surfaces until a live source exists.
- [ ] In [Frontend/src/pages/Landing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Landing.jsx), make proof continuity explicit so the trust narrative carries cleanly into `/winners` and `/upgrade`.
- [ ] In [Frontend/src/pages/Landing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Landing.jsx), emit `landing_cta_click` for all primary and secondary conversion CTAs.
- [ ] In [Frontend/src/pages/Landing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Landing.jsx), keep the primary CTA above the first meaningful fold on common phone sizes.
- [ ] In [Frontend/src/pages/Landing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Landing.jsx), include `not financial advice` wording and premium `Authored preview set` / `Live feed` labeling when applicable.
- [ ] In [Frontend/src/pages/Winners.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Winners.jsx), fetch `/curation/winners.v1.json`, show strategy sections, render cards, handle load failure, and avoid exposing exact trade-plan details.
- [ ] In [Frontend/public/curation/winners.v1.json](/home/darren/Documents/MCAS/Frontend/public/curation/winners.v1.json), derive the public placeholder dataset from [frontend_placeholders/marketing/winners.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/winners.v1.json) instead of inventing a second source of truth.
- [ ] In [Frontend/src/pages/Winners.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Winners.jsx), place recurring upgrade CTAs at banner, section, and card levels so the free tier pushes naturally into paid value.
- [ ] In [Frontend/src/pages/Winners.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Winners.jsx), emit `winners_cta_click` for upgrade and unlock-live CTAs.
- [ ] In [Frontend/src/pages/Winners.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Winners.jsx), clearly distinguish curated historical winners from paid live opportunity surfaces.
- [ ] In [Frontend/src/pages/Pricing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Pricing.jsx), show Free vs Pro cards and route CTA to `/upgrade`.
- [ ] In [Frontend/src/pages/Pricing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Pricing.jsx), use [frontend_placeholders/marketing/pricing-matrix.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/pricing-matrix.v1.json) so the comparison surface is dense, explicit, and data-backed from the start.
- [ ] In [Frontend/src/pages/Pricing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Pricing.jsx), include strong value-stack framing, objection handling, and repeated upgrade CTAs rather than only a two-card plan list.
- [ ] In [Frontend/src/pages/Pricing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Pricing.jsx), emit `pricing_cta_click` for every upgrade CTA placement.
- [ ] In [Frontend/src/pages/Pricing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Pricing.jsx), ensure the primary upgrade CTA remains above the first meaningful fold on common phone sizes.
- [ ] In [Frontend/src/pages/Pricing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Pricing.jsx), explain honest metric methodology in premium language rather than burying it in tiny footnotes.
- [ ] In [Frontend/src/pages/Upgrade.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Upgrade.jsx), redirect to `VITE_STRIPE_PAYMENT_URL` if present and show a disabled/support fallback if absent.
- [ ] In [Frontend/src/pages/Upgrade.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Upgrade.jsx), use [frontend_placeholders/marketing/onboarding-flow.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/onboarding-flow.v1.json) for upgrade-step copy and return-to behavior when live backend support does not exist.
- [ ] In [Frontend/src/pages/Upgrade.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Upgrade.jsx), preserve proof continuity from landing and pricing so the user is never asked to buy on trust-reset copy.
- [ ] In [Frontend/src/pages/CheckoutSuccess.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutSuccess.jsx), set local paid state on mount and provide a clear button to `/app`.
- [ ] In [Frontend/src/pages/CheckoutSuccess.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutSuccess.jsx), use [frontend_placeholders/marketing/onboarding-flow.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/marketing/onboarding-flow.v1.json) to drive explicit post-checkout onboarding copy.
- [ ] In [Frontend/src/pages/CheckoutSuccess.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutSuccess.jsx), emit `checkout_success_landed` on entry.
- [ ] In [Frontend/src/pages/CheckoutCancel.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutCancel.jsx), show a canceled state with links back to `/pricing` and `/winners`.
- [ ] In [Frontend/src/pages/CheckoutCancel.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutCancel.jsx), emit `checkout_cancel_landed` on entry.
- [ ] In [Frontend/src/pages/Settings.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Settings.jsx), change the post-success navigation target from `/` to `/app`.
- [ ] In [Frontend/src/pages/Settings.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Settings.jsx), keep the existing API key UX intact.
- [ ] In [Frontend/src/pages/Settings.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Settings.jsx), emit `settings_configured` after a successful save/test flow that leaves the user in a valid configured state.
- [ ] In [Frontend/src/pages/StrategyDashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/StrategyDashboard.jsx), group active opportunities by `setup_id`, show counts per strategy, and show top 3 to 5 opportunities per strategy.
- [ ] In [Frontend/src/pages/StrategyDashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/StrategyDashboard.jsx), link each strategy section back into `/app/opportunities`, preferably with `setup_id` in the URL query string.
- [ ] In [Frontend/src/pages/StrategyDashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/StrategyDashboard.jsx), support a dense placeholder/demo mode using [frontend_placeholders/app/strategy-dashboard-demo.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/strategy-dashboard-demo.v1.json) whenever no live source is available.
- [ ] In [Frontend/src/pages/StrategyDashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/StrategyDashboard.jsx), if the user is unpaid or the live feed is absent, render a premium-feel restricted/demo state with repeated upgrade CTAs and clear continuity back to pricing.
- [ ] In [Frontend/src/pages/StrategyDashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/StrategyDashboard.jsx), emit `strategy_card_clicked` when a strategy card or its primary CTA is activated.
- [ ] In [Frontend/src/pages/Landing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Landing.jsx), [Frontend/src/pages/Winners.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Winners.jsx), [Frontend/src/pages/Pricing.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Pricing.jsx), [Frontend/src/pages/Upgrade.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Upgrade.jsx), [Frontend/src/pages/CheckoutSuccess.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutSuccess.jsx), and [Frontend/src/pages/CheckoutCancel.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/CheckoutCancel.jsx), set route-specific page titles and meta descriptions.
- [ ] In [Frontend/index.html](/home/darren/Documents/MCAS/Frontend/index.html) and public assets, wire OG/Twitter card support and social preview assets for winners and pricing.
- [ ] In [Frontend/public/favicon.svg](/home/darren/Documents/MCAS/Frontend/public/favicon.svg) and [Frontend/public/site.webmanifest](/home/darren/Documents/MCAS/Frontend/public/site.webmanifest), add favicon/app polish appropriate for a premium operator product.

### Done Criteria

- [ ] Hitting `/` shows the marketing experience, not the dashboard.
- [ ] Hitting `/app` when unpaid shows the paywall.
- [ ] Hitting `/app` when paid but without API config routes to `/settings`.
- [ ] Hitting `/app` when paid and configured loads the dashboard shell.
- [ ] Hitting `/checkout/success` sets `mcas_tier=paid`.
- [ ] Hitting `/winners` renders curated content even without API config.
- [ ] Hitting `/pricing` and `/upgrade` works with or without a configured backend.
- [ ] Public trust surfaces and strategy-dashboard demo states still render coherently when live backend data is absent.
- [ ] The public funnel behaves like a continuous conversion path rather than disconnected marketing pages.
- [ ] QR/mobile traffic can scan and convert without fighting layout, dead space, or hidden CTAs.
- [ ] Every named frontend analytics event in this plan is emitted by a real front-end surface, even if the sink is still a buffer/no-op implementation.
- [ ] Public pages have route-specific titles, descriptions, OG/Twitter metadata, and social preview assets for winners and pricing.
- [ ] Landing, pricing, and paywall honor the no-horizontal-scroll, low-layout-shift, above-the-fold CTA contract on common phone sizes.
- [ ] Trust copy stays premium while clearly labeling `not financial advice`, `curated historical examples`, and `live` vs `authored preview set` vs `degraded`.

### Explicitly Out of Scope for Codex 1

- [ ] Do not edit [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx).
- [ ] Do not edit [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js).
- [ ] Do not edit run/control pages owned by Codex 3.
- [ ] Do not edit opportunity/market pages owned by Codex 2 except for route registration in [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx).

## Workstream 2: App Shell, Mobile Nav, Opportunity and Market Surfaces

### Owner

- [ ] Codex 2

### Owned Files

- [ ] [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx)
- [ ] [Frontend/src/pages/Dashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Dashboard.jsx)
- [ ] [Frontend/src/pages/Opportunities.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Opportunities.jsx)
- [ ] [Frontend/src/pages/OpportunitySurvey.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/OpportunitySurvey.jsx)
- [ ] [Frontend/src/pages/OpportunityDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/OpportunityDetail.jsx)
- [ ] [Frontend/src/pages/Markets.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Markets.jsx)
- [ ] [Frontend/src/pages/MarketDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/MarketDetail.jsx)
- [ ] [Frontend/src/components/opportunities/GlassBoxBadge.jsx](/home/darren/Documents/MCAS/Frontend/src/components/opportunities/GlassBoxBadge.jsx)
- [ ] [Frontend/src/index.css](/home/darren/Documents/MCAS/Frontend/src/index.css) only if a tiny amount of shared CSS is strictly necessary for the mobile drawer or marketing-safe spacing. Prefer Tailwind classes first.
- [ ] [frontend_placeholders/app/glass-box-cases.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/glass-box-cases.v1.json)

### Required Changes

- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), update every app navigation entry to `/app/...`.
- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), add a `Strategies` nav item pointing to `/app/strategies`.
- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), update pipeline detection to `/app/pipeline`.
- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), update quick-scan success navigation to `/app/runs/:id`.
- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), add mobile drawer behavior so the sidebar is hidden by default on small screens.
- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), add a visible mobile toggle button in the top header.
- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), make the drawer close on backdrop click and `Escape`.
- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), keep desktop collapse behavior unchanged on `md+`.
- [ ] In [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx), keep app-content padding smaller on mobile and current roomy spacing on desktop.
- [ ] In [Frontend/src/pages/Dashboard.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Dashboard.jsx), change links to use `/app/runs`, `/app/opportunities`, `/app/paper`, and `/app/control`.
- [ ] In [Frontend/src/pages/Opportunities.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Opportunities.jsx), change simulation-run navigation to `/app/runs/:id`.
- [ ] In [Frontend/src/pages/Opportunities.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Opportunities.jsx), change survey navigation to `/app/opportunities/survey`.
- [ ] In [Frontend/src/pages/Opportunities.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Opportunities.jsx), change detail links to `/app/opportunities/:id`.
- [ ] In [Frontend/src/pages/OpportunitySurvey.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/OpportunitySurvey.jsx), change back/navigation links to `/app/opportunities`.
- [ ] In [Frontend/src/pages/OpportunityDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/OpportunityDetail.jsx), change back links to `/app/opportunities`.
- [ ] In [Frontend/src/pages/OpportunityDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/OpportunityDetail.jsx), change market links to `/app/markets/:symbol`.
- [ ] In [Frontend/src/pages/OpportunityDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/OpportunityDetail.jsx), add a compact header-level `Glass Box` badge next to existing badges.
- [ ] In [Frontend/src/components/opportunities/GlassBoxBadge.jsx](/home/darren/Documents/MCAS/Frontend/src/components/opportunities/GlassBoxBadge.jsx), implement badge variants for `Verified`, `Violation`, `Unknown`, and `N/A`.
- [ ] In [Frontend/src/pages/OpportunityDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/OpportunityDetail.jsx), use [frontend_placeholders/app/glass-box-cases.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/glass-box-cases.v1.json) for mocked badge states and public proof-surface walkthroughs when a live payload is unavailable.
- [ ] In [Frontend/src/pages/Markets.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Markets.jsx), change detail links to `/app/markets/:symbol`.
- [ ] In [Frontend/src/pages/MarketDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/MarketDetail.jsx), change back links to `/app/markets`.

### Done Criteria

- [ ] All app-shell sidebar links point to `/app/...` and work.
- [ ] The quick-scan CTA lands on `/app/runs/:id`.
- [ ] Opportunity links and market links never route back to the old non-`/app` paths.
- [ ] The `Glass Box` badge appears without duplicating causality logic.
- [ ] At `375x812`, the app shell is usable and the sidebar is not permanently consuming the viewport.
- [ ] The pipeline page still renders full-bleed content under the new `/app/pipeline` path.
- [ ] Glass Box proof states can still be demonstrated in UI and tests even if live lineage payloads are unavailable.

### Explicitly Out of Scope for Codex 2

- [ ] Do not edit [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx).
- [ ] Do not edit [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js).
- [ ] Do not edit run/control pages owned by Codex 3.

## Workstream 3: Runs, Control, PF Honesty, and Test Coverage

### Owner

- [ ] Codex 3

### Owned Files

- [ ] [Frontend/src/pages/Runs.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Runs.jsx)
- [ ] [Frontend/src/pages/RunDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunDetail.jsx)
- [ ] [Frontend/src/pages/RunCompare.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunCompare.jsx)
- [ ] [Frontend/src/pages/SimulationResults.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/SimulationResults.jsx)
- [ ] [Frontend/src/pages/ControlPlane.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/ControlPlane.jsx)
- [ ] [Frontend/src/pages/PaperTrading.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/PaperTrading.jsx)
- [ ] [Frontend/src/utils/pf.js](/home/darren/Documents/MCAS/Frontend/src/utils/pf.js)
- [ ] [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js)
- [ ] [Frontend/tests/pf.unit.test.js](/home/darren/Documents/MCAS/Frontend/tests/pf.unit.test.js)
- [ ] [Frontend/package.json](/home/darren/Documents/MCAS/Frontend/package.json)
- [ ] [frontend_placeholders/app/metrics-demo.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/metrics-demo.v1.json)

### Required Changes

- [ ] In [Frontend/src/pages/Runs.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/Runs.jsx), change compare/detail links to `/app/runs/...`.
- [ ] In [Frontend/src/pages/RunDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunDetail.jsx), change back links to `/app/runs`.
- [ ] In [Frontend/src/pages/RunDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunDetail.jsx), change simulation links to `/app/runs/:id/simulation`.
- [ ] In [Frontend/src/pages/RunCompare.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunCompare.jsx), change back links to `/app/runs`.
- [ ] In [Frontend/src/pages/SimulationResults.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/SimulationResults.jsx), change back links to `/app/runs/:id`.
- [ ] In [Frontend/src/pages/ControlPlane.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/ControlPlane.jsx), change all post-action navigation to `/app/runs/:id` or `/app/paper` as appropriate.
- [ ] In [Frontend/src/pages/PaperTrading.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/PaperTrading.jsx), change the control-plane CTA to `/app/control`.
- [ ] In [Frontend/src/utils/pf.js](/home/darren/Documents/MCAS/Frontend/src/utils/pf.js), implement `groupPnLByDay(items, tsField, pnlField)`.
- [ ] In [Frontend/src/utils/pf.js](/home/darren/Documents/MCAS/Frontend/src/utils/pf.js), implement `profitFactor(grossProfit, grossLossAbs)`.
- [ ] In [Frontend/src/utils/pf.js](/home/darren/Documents/MCAS/Frontend/src/utils/pf.js), implement `averageDailyPF(items, tsField, pnlField)`.
- [ ] In [Frontend/src/pages/RunDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunDetail.jsx), add a new `Avg Daily PF` stat near the existing `Profit Factor` stat.
- [ ] In [Frontend/src/pages/RunDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunDetail.jsx), fetch whatever trade data is needed for Avg Daily PF inside the overview flow without changing backend APIs.
- [ ] In [Frontend/src/pages/RunDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunDetail.jsx), support a deterministic placeholder/demo state using [frontend_placeholders/app/metrics-demo.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/metrics-demo.v1.json).
- [ ] In [Frontend/src/pages/RunDetail.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/RunDetail.jsx), add tooltip/footnote text explaining the daily-PF methodology.
- [ ] In [Frontend/src/pages/SimulationResults.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/SimulationResults.jsx), add a new `Avg Daily PF` stat near the existing `Profit Factor` stat.
- [ ] In [Frontend/src/pages/SimulationResults.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/SimulationResults.jsx), compute Avg Daily PF from the loaded results using `exit_ts` when available and `created_at` as fallback.
- [ ] In [Frontend/src/pages/SimulationResults.jsx](/home/darren/Documents/MCAS/Frontend/src/pages/SimulationResults.jsx), support a deterministic placeholder/demo state using [frontend_placeholders/app/metrics-demo.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/metrics-demo.v1.json).
- [ ] In [Frontend/tests/pf.unit.test.js](/home/darren/Documents/MCAS/Frontend/tests/pf.unit.test.js), add deterministic tests for finite PF, infinite PF, empty/no-trade input, and multi-day averages using Node's built-in test runner.
- [ ] In [Frontend/tests/pf.unit.test.js](/home/darren/Documents/MCAS/Frontend/tests/pf.unit.test.js), anchor fixture expectations to [frontend_placeholders/app/metrics-demo.v1.json](/home/darren/Documents/MCAS/frontend_placeholders/app/metrics-demo.v1.json).
- [ ] In [Frontend/package.json](/home/darren/Documents/MCAS/Frontend/package.json), add `test:unit` as `node --test tests/pf.unit.test.js`.
- [ ] In [Frontend/package.json](/home/darren/Documents/MCAS/Frontend/package.json), add `test:e2e` as `playwright test`.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), rewrite setup helpers so paid-tier and API-config setup are independent.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), stop assuming `/` is the dashboard.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add coverage for the landing page.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add coverage for the winners page.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add coverage for the unpaid `/app` paywall.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add coverage for paid-but-unconfigured routing to `/settings`.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add coverage for `/checkout/success` setting local paid state.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add coverage for `/checkout/cancel`.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), change all existing route sweeps to `/app/...`.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add a mobile viewport smoke test for `/`, `/winners`, and `/app/strategies`.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add public-route assertions for page title and meta description on landing, winners, and pricing.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), assert no horizontal scroll on landing, pricing, winners, and paywall at common phone sizes.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), assert the primary CTA is in the initial viewport on landing, pricing, and paywall at common phone sizes.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add a low-layout-shift assertion for landing, pricing, and paywall so the primary CTA does not jump after initial render.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add a mocked opportunity-detail test that intercepts the opportunity payload and asserts the `Glass Box` badge label.
- [ ] In [Frontend/tests/full-ui.spec.js](/home/darren/Documents/MCAS/Frontend/tests/full-ui.spec.js), add a mocked run/simulation test that asserts `Avg Daily PF` appears when the relevant API payload is stubbed.

### Done Criteria

- [ ] No run/control page points to the old non-`/app` routes.
- [ ] `Avg Daily PF` appears next to `Profit Factor` on both run-detail and simulation-results surfaces.
- [ ] `test:unit` exists and passes locally.
- [ ] `test:e2e` exists and the suite no longer assumes `/` is the dashboard.
- [ ] Playwright covers the new public route map, paywall behavior, and at least one mocked `Glass Box` and `Avg Daily PF` assertion.
- [ ] Metrics surfaces remain demonstrable and testable even without a live backend source.
- [ ] Playwright verifies public metadata, CTA visibility, and no-horizontal-scroll behavior for the conversion pages.

### Explicitly Out of Scope for Codex 3

- [ ] Do not edit [Frontend/src/App.jsx](/home/darren/Documents/MCAS/Frontend/src/App.jsx).
- [ ] Do not edit [Frontend/src/components/Layout.jsx](/home/darren/Documents/MCAS/Frontend/src/components/Layout.jsx).
- [ ] Do not edit public pages owned by Codex 1.
- [ ] Do not edit opportunity/market pages owned by Codex 2.

## Merge Order

- [ ] Merge Codex 1 first. The route topology and subscription provider must exist before the rest of the app is coherent.
- [ ] Merge Codex 2 second. It rewires the shell and opportunity/market surfaces to the new route contract.
- [ ] Merge Codex 3 last. Its tests and PF assertions depend on the route split and page changes from the first two workstreams.

## Post-Merge Global Verification

- [ ] Run `npm run build` in [Frontend](/home/darren/Documents/MCAS/Frontend).
- [ ] Run `npm run test:unit` in [Frontend](/home/darren/Documents/MCAS/Frontend).
- [ ] Run `npm run test:e2e` in [Frontend](/home/darren/Documents/MCAS/Frontend).
- [ ] Manually verify `/`, `/winners`, `/pricing`, `/upgrade`, `/checkout/success`, `/checkout/cancel`, and `/settings`.
- [ ] Manually verify `/app` as unpaid, paid-without-config, and paid-with-config.
- [ ] Manually verify `/app/strategies`, `/app/opportunities/:id`, `/app/runs/:id`, and `/app/runs/:id/simulation`.
- [ ] Manually verify the mobile shell at roughly `375x812`.
- [ ] Verify every feature without a live backend data source still renders from [frontend_placeholders](/home/darren/Documents/MCAS/frontend_placeholders) with dense, operator-grade content rather than thin placeholders.
- [ ] Verify analytics events are emitted into the frontend buffer for the named conversion surfaces when interacted with manually.
- [ ] Verify winners and pricing produce polished social previews and route metadata.

## Final Definition of Done

- [ ] All checklist items under Codex 1, 2, and 3 are complete.
- [ ] No file ownership boundary was broken.
- [ ] The app has no remaining links to legacy non-`/app` app routes.
- [ ] Public pages render without the app sidebar.
- [ ] Unpaid users cannot enter `/app/*` without seeing the paywall.
- [ ] Paid users can activate locally and then connect their backend via `/settings`.
- [ ] `Glass Box` badge is present on opportunity detail.
- [ ] `Avg Daily PF` is present anywhere aggregate PF is surfaced by this scope.
- [ ] Features without a current backend source still ship with authored placeholder/demo data from [frontend_placeholders](/home/darren/Documents/MCAS/frontend_placeholders).
- [ ] Conversion surfaces are instrumentable, shareable, mobile-hard, and trust-consistent before backend analytics matures.
- [ ] Unit and Playwright verification both pass.
