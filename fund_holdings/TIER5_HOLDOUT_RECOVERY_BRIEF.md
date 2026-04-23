# Brief: Tier 5 Holdout Recovery Planner

You are **Claude Opus 4.7** working on the MCAS project's fund-holdings bible. Your job is to **produce a written plan** for recovering holdings endpoints for four ETF sponsors that have so far resisted three rounds of Phase 0 recovery work: **VanEck, JPMorgan, Fidelity, and Franklin Templeton.**

Do NOT write code or execute anything. Do NOT modify the bible or any other artifact. **Your deliverable is one markdown file:** `fund_holdings/TIER5_HOLDOUT_RECOVERY_PLAN.md`.

The current Phase 0 bible has 1,244 funds across 16 sheets ($10.16T AUM). These four holdouts represent roughly **$304B of remaining unrecovered AUM** (Fidelity $80B + VanEck $84B + JPMorgan $123B + Franklin $17B). The project's core mission is fund-flow research; every sponsor we can't reach is a blind spot in the rebalancing-flow model. These four have to be solved, not deferred.

Before you start, read `fund_holdings/PHASE_0_SPONSOR_RECOVERY_PLAN.md` (the original plan) and `fund_holdings/PHASE_0_SPONSOR_RECOVERY_STATE.md` (the state after three recovery passes). Also read the root `CLAUDE.md` for project-wide conventions.

## What we know — per-sponsor failure mode

### VanEck (53 funds, ~$84B AUM)
- Playwright harvest captured 13–17 responses per ticker after full click-through (cookie accept → audience select → Holdings tab click). **None** of them carried position-level data.
- Responses we did find:
  - `GET /Main/FundDetailsBlock/GetContent/?blockid={bid}&pageid={pid}&ticker={T}` → 2.9KB JSON with fund stats (CUSIP, ISIN, benchmark, shares outstanding, SEC yield) — not holdings.
  - `POST /Main/FundListingUs/GetFundData` with `filterJson=...` URL-encoded body → 12.6KB JSON of UI table headers, not holdings.
  - `GET /Main/ContentAreaUsBlock/GetContent/?blockid=364471&pageid={pid}&ticker={T}` → 12KB JSON of marketing / trending-insights HTML fragments, not holdings.
  - `GET /Main/HtmlUsBlock/GetContent/...&blockid=11414&...` → 5KB of disclaimer text.
- Static HTML at `/us/en/investments/{ticker}/holdings/` contains **zero `<table>` elements** — the holdings are rendered entirely client-side from JS.
- No `.csv` or `.xlsx` links are exposed in the rendered page.
- Full capture artifact: `fund_holdings/source_authoring/phase_0/logs/session_captures/vaneck_harvest.json`. Response bodies are base64-encoded under `response_body_b64`.

### JPMorgan Asset Management (40 funds, ~$123B AUM)
- Playwright harvest captured **0 holdings-signature XHRs** for JEPI and JPST fund pages. The 12 captures (per ticker) were all: consent banners, audience-config JSON, analytics beacons, chase.com infrastructure — nothing with position data.
- Page URL was `am.jpmorgan.com/us/en/asset-management/adv/products/{ticker_lower}-etf-shares` — loaded OK.
- **Suspected root cause:** the audience-selector click ("Financial Professional") didn't complete in our harvester, so the session never unlocked the advisor-tier holdings view. None of the `button:has-text('Financial Professional')` / `button:has-text('Advisor')` / `button:has-text('Continue')` selectors matched.
- Known-probe endpoint `am.jpmorgan.com/services/etf-holdings?ticker={T}` returns 404 without the right JSESSIONID.
- Full capture artifact: `fund_holdings/source_authoring/phase_0/logs/session_captures/jpmorgan_harvest.json`.

### Fidelity (61 funds, ~$80B AUM)
- Playwright `page.goto()` timed out at 30s on both `https://fundresearch.fidelity.com/etfs/composition/{TICKER}` and `https://fundresearch.fidelity.com/etfs/summary/{TICKER}`. Repeated twice — consistent timeout.
- `curl_cffi` GET of `fundresearch.fidelity.com/mutual-funds/download-csv/composition/{TICKER}` returns 200 OK with 8,134 bytes of HTML (error page / redirect target, not CSV).
- **Suspected root cause:** Fidelity is detecting headless Chromium via webdriver-detection fingerprints. `--disable-blink-features=AutomationControlled` + `navigator.webdriver = undefined` init-script were not enough.
- Capture artifact: `fund_holdings/source_authoring/phase_0/logs/session_captures/fidelity_harvest.json` (empty / timeout records).

### Franklin Templeton (49 funds, ~$17B AUM)
- Playwright harvest did capture several POST XHRs against a **GraphQL-over-REST** endpoint at `www.franklintempleton.com/api/pds/price-and-performance?op={OP}&id={N}`:
  - `op=Labels` → 663 label entries (PDS service field names + display labels). Not holdings.
  - `op=Ppss` → 98 product records (ticker/fundid/name/asset-class) — this is the **ticker → PDS fundid mapping**.
  - `op=ProductCommon`, `op=ShareClassDetail` → product metadata.
- POST bodies are GraphQL queries wrapped in a `query` field: e.g. `{"query":"query Ppss($countrycode: String!, $languagecode: String!, $productType: String!) { PPSS(countrycode: ..., productType: ...) { fundid ... } }", "variables": {...}}`.
- **No `op=Holdings` identified in our capture**, but the PDS infrastructure clearly supports arbitrary ops. The `pds.*` field-name prefix appeared in the Labels response (e.g., `pds.SECURITY_EXPIRATION_DATE`), suggesting the backend has a fully-typed Holdings schema.
- The resourceapi endpoint at `/us-retail/resourceapi/investments/options/exchange-traded-funds/products/{ticker_lower}-etf` returns 200KB of page metadata but no holdings; it references `products.portfolio-top-ten-holdings-tab` as a component slug (so holdings are served separately).
- Capture artifact: `fund_holdings/source_authoring/phase_0/logs/session_captures/franklin_templeton_harvest.json`.

## What infrastructure exists (do NOT rebuild)

All under `fund_holdings/source_authoring/phase_0/`:
- **`session_replay_pipeline.py`** — Playwright harvester (with `page.route` interception to capture full request+response including POST bodies) + `curl_cffi` replayer. Has a `HARVEST_TARGETS` dict keyed by sponsor slug with per-sponsor page URL, cookie-accept selectors, audience-selector selectors, holdings-click texts, and host hints. Already configured for the 4 holdouts — you can just iterate on the click flow per sponsor.
- **`content_verify.py`** — format-specific verifiers (`_csv_ok`, `_xlsx_ok`, `_json_ok`, `_html_ok`). Known issue: `_json_ok` passes arrays-of-dicts with id-like fields even when they're cookie consents or disclosure text. The plan should specify a harder gate that requires instrument-specific fields (`units`/`weight`/`market_value`/`cusip`/`isin`).
- **`sponsor_discovery_v2.py`** — base fetch utils (curl_cffi with chrome124 impersonation), URL expand, format detection, content verify.
- **`recover_dimensional.py`** — template for sponsor-specific recovery: find pattern, enumerate all tickers, verify, write to `recovered_{sponsor}_urls.json`. Your per-sponsor recovery scripts should follow this shape.
- **`rebuild_bible_v2.py`** — assembles final bible from settled sheets + invesco/dimensional URL maps + template-based confirmed sponsors + bucket_g. Extensible: add another `load_url_map` call and a `build_sheet_from_url_map` block for each new sponsor.
- **`verify_bible_v2.py`** — runs §7 verification suite: schema, url distinctness, no-fallback-formats, metadata coverage, sample content-verify.

## Constraints (Phase 0 rules)

From `CLAUDE.md` and `fund_holdings/PHASE_0_SPONSOR_RECOVERY_PLAN.md`:

- **No paid data sources.**
- **No scraping authenticated institutional portals.** A sponsor that requires login to reach holdings fails Phase 0 structural test §4.1(3) ("publicly, for free").
- **No N-PORT fallback** as `Holdings_Feed_URL`. N-PORT is monthly and lagged.
- **No `edgar_fallback` / `template_guess` / `pattern_guess` / `n_port_fallback` placeholder formats.** Content-verified or excluded.
- **Every admitted row must pass §7.2 content verification** on the URL we record (or at minimum on the pattern we derive — per-ticker verify during rebuild is the standard).
- Content-verify means the URL serves structured data with ≥10 rows (relaxed for specific vehicle_subtypes via Bucket G) AND an identifier column AND (per the lesson from earlier passes) at least one instrument-specific field.

## What the plan must contain

Write `fund_holdings/TIER5_HOLDOUT_RECOVERY_PLAN.md` with sections:

1. **Per-sponsor strategy.** For each of VanEck, JPMorgan, Fidelity, Franklin Templeton: exactly what technique(s) you propose, in order, with a wall-clock budget per step and a clear give-up criterion. Anchor each strategy in the specific evidence from the harvest captures (cite which XHR URLs you'll investigate further, which click flows to fix, which GraphQL ops to enumerate).

2. **Harvest improvements needed.** The current harvester has known weaknesses:
   - Audience-selector clicks fail silently for JPMorgan (no confirmed working selector).
   - Headless detection blocks Fidelity entirely.
   - Some POSTs (Dimensional-style) have `post_data=None` without `page.route` interception — the pipeline fixed this but you should double-check per sponsor.
   - Content-verify admits false positives; harden it before re-harvesting or you'll land on garbage URLs again.

3. **Per-sponsor probe-list.** For each sponsor, list 5–10 specific URL patterns / query variations you'd try, ranked by likelihood. For Franklin, list specific GraphQL op names to enumerate. For JPMorgan, list specific audience-selector text variants + alternate page URLs (non-advisor flows). For VanEck, list specific blockid values to sweep OR a DOM-extraction approach if no XHR exists. For Fidelity, list stealth-browser options to try.

4. **Effort budget with escape hatches.** Plan for 1–3 hours per sponsor wall-clock. Define escalation triggers — if X doesn't work within Y minutes, switch to strategy Z. Cap total session time so the planner agent knows when to stop and write a state report instead of pushing through.

5. **Realistic recovery estimate.** Call your shot per sponsor. "X of Y tickers likely recover" with reasoning. Remember: Dimensional recovered 36/37 by finding a `fullHoldingsCsvUrl` field hidden inside a larger XHR response — the equivalent insight for each of these 4 might already exist in the captured bodies. You should read the capture JSONs (especially the `response_body_preview` and decode the `response_body_b64`) before declaring what's possible.

6. **Fallback strategy.** For sponsors that still don't yield after best-effort: what's the honest-exclusion reason code? When is it OK to say "this sponsor genuinely doesn't publish daily holdings publicly" vs. "we couldn't crack it with current tools"?

7. **Definition of done.** Scope-specific DoD — e.g., "4 of 4 confirmed with ≥20/ticker rows" vs. "2 of 4 confirmed, other 2 documented with specific Phase 2 next steps".

8. **Open questions you'd ask Darren.** Before executing, what decisions need his input? (e.g., is a residential proxy OK for Fidelity if stealth-mode fails? How much of the bible can stall while you chase Franklin's GraphQL?)

## What NOT to propose

- Don't propose paid tools (Apify, ScrapingBee, Bright Data, etc.).
- Don't propose rebuilding infrastructure that already exists. Read `session_replay_pipeline.py` first.
- Don't propose N-PORT / EDGAR fallbacks.
- Don't propose scraping authenticated portals.
- Don't propose manual browser inspection as a first resort — it's the Tier 8 escape hatch, not the plan.
- Don't gloss over the content-verify false positive issue — it MUST be hardened before re-harvest or the plan will re-land on cookielaw-style responses.

## Deliverable shape

The output file should be structured like `PHASE_0_SPONSOR_RECOVERY_PLAN.md` Revision 2 — clearly numbered sections, per-sponsor tables where applicable, explicit decisions with reasoning, a self-honest recovery estimate, and escalation triggers. Target 1,500–3,000 words. No code blocks longer than 10 lines unless essential.

When you're done, print a one-paragraph summary of the plan's key decisions and the realistic expected outcome, then stop. I'll review before execution is authorized.
