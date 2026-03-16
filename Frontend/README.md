# MCAS Frontend (Subscription Edition)

Market Cognition At Scale — Crypto trading analytics with Glass Box causality proofs.

## Architecture: Public/Paid Split

This frontend is architecturally split into two distinct zones:

1. **Public Funnel (`/`, `/winners`, `/pricing`, `/upgrade`, `/settings`)**
   - No authentication or app sidebar.
   - Mobile-first conversion pages with dense proof strips and authored curation.
   - Uses `/curation/*.json` as the canonical source for placeholder/demo data.
   - `Settings` stays public to allow pre-activation configuration.

2. **Paid Operator App (`/app/*`)**
   - Protected by the **Paid Gate** (`mcas_tier` in localStorage).
   - Uses the existing authenticated App Shell and Sidebar.
   - Requires both local Pro activation AND valid API configuration.

## Key Concepts

- **Local Activation:** Subscription state is managed in the browser (`mcas_tier=paid`). Use `/checkout/success` to activate.
- **Glass Box Proof:** Opportunities show a causality badge (Verified/Violation/Unknown) based on the `sim_trace.kline_lineage` payload.
- **Honest Metrics:** Surfaces display both aggregate Profit Factor and **Avg Daily PF** to prevent outlier-driven distortion.
- **Authored Curation:** When live backend data is missing or the user is unpaid, the UI degrades gracefully to high-density authored demo sets in `public/curation/`.

## Development

```bash
npm install
npm run dev      # Local dev server
npm run build    # Production build
npm run test:unit # Logic verification (Node)
npm run test:e2e  # UI verification (Playwright)
```

## Testing Requirements

- **Mobile First:** Public conversion pages must have no horizontal scroll and keep primary CTAs above the fold on common phone sizes (390x844 etc.).
- **Self-Contained:** All demo data must live in `public/curation/`.
- **Instrumentation:** Named frontend analytics events are emitted for all conversion surfaces even when no sink is wired.
