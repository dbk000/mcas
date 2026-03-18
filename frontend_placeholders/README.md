# Frontend Placeholder Data

This directory is the canonical authoring source for placeholder/demo data used by ambitious frontend features that do not yet have a reliable backend data source.

The goal is not filler. The goal is operator-grade sample data that can support:

- ranked tables
- live-looking tapes
- dense strategy cards
- causality walkthroughs
- PF methodology explainers
- pricing/proof surfaces

Design target: Bloomberg-terminal-grade information density, but with a less punitive visual tone. Data-first, scan-fast, high signal, minimal fluff.

## Rules

1. Do not put ad-hoc placeholder objects directly inside React components if the feature lacks a backend source. Author them here first.
2. Keep strategy IDs, route semantics, and object shapes close to the real app where possible.
3. Placeholders should be rich enough to drive the intended UI, not just unblock rendering.
4. Frontend runtime consumption should happen by either:
   - importing these files at build time, or
   - syncing selected files into `Frontend/public/placeholders/`
5. If a live backend source arrives later, keep these files for demo mode, visual testing, onboarding, and regression fixtures unless there is a strong reason to retire them.

## Current Packs

- `manifests/placeholder-index.v1.json`
- `marketing/winners.v1.json`
- `marketing/proof-pack.v1.json`
- `marketing/pricing-matrix.v1.json`
- `marketing/onboarding-flow.v1.json`
- `app/strategy-dashboard-demo.v1.json`
- `app/glass-box-cases.v1.json`
- `app/metrics-demo.v1.json`

## Intended Consumers

- Landing page
- Winners page
- Pricing page
- Upgrade and checkout-success onboarding
- Strategy dashboard demo/fallback
- Glass Box public walkthrough and badge-state mocks
- Avg Daily PF methodology and metrics mocks
