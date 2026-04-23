# Phase 0 — Sponsor Recovery Plan

**Owner:** Darren
**Author:** phase_0_sponsor_recovery_planner_cc (Claude Code, Opus 4.7)
**Planned at:** 2026-04-23 UTC
**Target artifact:** `fund_holdings/ground_truth/fund_bible_v1.xlsx` (additive in-place enrichment)
**Status:** Revision 2 — 2026-04-23. Darren approved Revision 1 with three changes (single-session execution, resolve §13 open questions in-plan, Overview-sheet rebuild into DoD) plus a Bucket G scoping clarification. Applied below. Awaiting Darren's final review of this revision before commit + execute.

---

## 0. Starting state

- `fund_bible_v1.xlsx` currently has **806 funds across 9 sponsor sheets**, all with content-verified `Holdings_Feed_URL`. That work is settled and stays untouched.
- `excluded_sponsors.yaml` lists **59 sponsors with 1,757 funds and ~$1.68T aggregate AUM** that the previous session couldn't discover endpoints for.
- `excluded_funds.yaml` additionally contains **~95 rows removed from surviving sheets** for content-verify failures (iBonds ≤9-position bond ladders, Direxion single-stock leveraged ETFs with ≤9 positions). These are separately recoverable by relaxing the ≥10-row gate for narrow-universe vehicle subtypes; they are included in this plan as a second-order recovery target.
- Six bible columns are 100% null: `Inception_Date`, `Shares_Outstanding`, `Expense_Ratio_Bps`, `Benchmark_Name`, `Rebalance_Trigger_Rules`, `Holdings_Feed_Lag_Hours`. Most sponsor product-index endpoints carry 3–5 of these; recovery should catch them in the same pass.

## 1. Why the previous session excluded 59 sponsors — diagnosis

I read `sponsor_probes.py` and `sponsor_probes_result.jsonl`. The failure is not "these sponsors have no API." The failure is that the previous session's probe was structurally underpowered:

1. **One to three hand-guessed URL patterns per sponsor, attempted once.** Most failing sponsors got a single probe URL. If that URL returned 404/403, the sponsor was tabled. No TLS-fingerprint retry. No sitemap enumeration. No inline-JSON extraction. No browser network-tab inspection. No alternate hostname probing.
2. **No workaround for WAF/TLS-fingerprint blocking.** Every 403/406 from Invesco, Schwab, WisdomTree, Pacer, PIMCO, T. Rowe Price, John Hancock is a Python `requests`/`httpx` default-TLS-fingerprint being rejected by Akamai/Cloudflare/F5. These sponsors all serve real browsers. The `curl_cffi` package (or equivalent TLS-impersonation library) unblocks most of them in ~15 LOC. The previous session never tried it.
3. **No workaround for client-side-rendered pages.** Every `no_html_table_with_id_column_and_10_rows` (Fidelity, JPMorgan, Dimensional, Franklin Templeton, Innovator, Pacer, VanEck, AllianceBernstein, PGIM, etc.) is a React/Next.js/Angular page where the raw HTML is an empty app shell. The holdings data arrives via XHR after page load. The previous session didn't load any page in a browser to capture those XHR URLs. Headless Chromium + CDP (Chrome DevTools Protocol) solves this deterministically.
4. **"One ticker probed" was the exit gate.** Alpha Architect was excluded because 35 of 42 tickers returned 404 under `etfsite.alphaarchitect.com/{ticker}/` — but `qmom` did work. That means the pattern is real but only covers a subset of the 42 funds. The correct response is to enumerate the subadvisers under EA Series Trust and author per-subadviser endpoints, not to discard all 42. The previous session made the same category error on Tidal (128 funds across 50+ subadvisers) and "other_sponsors" (236 funds across 138 micro-sponsors).
5. **URL pattern variations were not systematically tried.** A 404 on `/product/{ticker}` was accepted without trying `/products/{ticker}`, `/fund/{ticker}`, `/funds/{ticker}`, `/etf/{ticker}`, `/etfs/{ticker}`. FlexShares is a concrete example: previous session tried `flexshares.com/en-us/fund/{ticker_lower}` (singular) and got 404. The real pattern is almost certainly `/funds/` (plural) or `/fund-center/{TICKER}`. One extra probe would have resolved it.

The previous session wasn't wrong to exclude when its probes failed — the mistake was running one probe, not five, and running one tool, not four. **This recovery plan fixes the toolchain, not the taxonomy.**

## 2. Discovery technique catalogue

Eight techniques, ordered cheapest-to-most-expensive. A sponsor is declared API-less only after every applicable technique has been tried and each has a recorded failure artifact.

### T1 — URL-pattern enumeration with curl_cffi TLS impersonation
For each sponsor, try 8–15 plausible URL patterns with `curl_cffi.requests.Session(impersonate="chrome124")`. Patterns come from a shared catalogue (below). One GET per pattern, content-verify the response per §7.2 of the remediation taskboard. This alone is expected to unblock every WAF-403/406 sponsor and ~half the 404 sponsors.
- **Pattern catalogue (apply to every sponsor, not just the one the previous session guessed):**
  - `{base}/etf/{ticker}` / `/etfs/{ticker}` / `/etf/{ticker_upper}` / `/etfs/{ticker_upper}`
  - `{base}/fund/{ticker}` / `/funds/{ticker}` / `/fund-center/{ticker}` / `/product/{ticker}` / `/products/{ticker}`
  - `{base}/etf-products/{ticker}` / `/investment-products/etfs/profile/{ticker}`
  - `{base}/etfs/{ticker}/holdings` / `/etfs/{ticker}/holdings.csv` / `/etfs/{ticker}/holdings.xlsx`
  - `{base}/api/etf/holdings?ticker={TICKER}` / `/services/etf-holdings?ticker={TICKER}`
  - `{base}/globalassets/{sponsor}/resources/holdings/{ticker}-holdings.csv` (AEM pattern)
  - `{base}/_api/etfs/{TICKER}/holdings` / `/api/funds/etfs/{TICKER}` (Next.js API route pattern)
  - CDN origin swap: if `www.sponsor.com` is WAF-blocked, probe `cdn.sponsor.com`, `static.sponsor.com`, `assets.sponsor.com`, `api.sponsor.com` with the same paths.
- **Stack:** `curl_cffi` (already pip-installable, no system deps) + the existing `content_verify.py`.
- **Effort per sponsor:** ~60 seconds automated.

### T2 — Sitemap.xml and robots.txt enumeration
Fetch `/sitemap.xml`, `/sitemap_index.xml`, `/robots.txt`. Most content-managed sponsor sites expose a sitemap listing every fund URL; the pattern can then be inferred by diffing two fund URLs. For an AEM or WordPress site, `/sitemap.xml` is usually populated by the CMS and is authoritative.
- **Applies especially to:** mid-tier sponsors on Adobe Experience Manager (iShares pattern), WordPress (Global X pattern), or Next.js-SSR sites where per-fund pages are indexed.
- **Effort per sponsor:** ~60 seconds.

### T3 — Inline JSON / hydration-state extraction
For SSR'd React/Next.js/Angular pages, the initial JSON payload is hydrated inline in the HTML. Extract:
- `<script id="__NEXT_DATA__" type="application/json">` (Next.js, very common: Franklin Templeton, Dimensional, JPMorgan, Janus Henderson)
- `<script type="application/ld+json">` (JSON-LD)
- `window.__INITIAL_STATE__`, `window.__APOLLO_STATE__`, `window.__PRELOADED_STATE__` (Redux/Apollo hydration)
- AEM's `<div data-sly-resource="…">` + backing `.model.json`
- **Yield:** typically includes the canonical per-fund URL template and the holdings-feed URL in the same JSON blob, plus Inception_Date / AUM / Expense_Ratio — free metadata extraction.
- **Effort per sponsor:** ~90 seconds.

### T4 — Headless Chromium network-tab capture (the escalation)
For sponsors where T1–T3 fail, launch `playwright` (headless chromium) against 2 known tickers, wait for network idle, dump every XHR URL, score by keywords (`holdings`, `portfolio`, `composition`, `position`, `constituent`), probe the top-3 scored candidates against `content_verify`. Save the full HAR-equivalent XHR dump to `source_authoring/phase_0/logs/network_captures/{sponsor}.json` so future sessions can skip this step.
- **Stack:** Playwright Python (install once; `playwright install chromium` pulls a headless browser). Runs under Xvfb if display needed, but headless is fine for XHR capture.
- **Memory budget:** ~400MB per chromium process. Cap at 4 concurrent processes = ~1.6GB RSS. Run serially or with a 4-way pool — do NOT fork 16 at once.
- **Effort per sponsor:** ~10 minutes (page load + network idle + XHR filtering + verify).
- **This is the technique the previous session should have used on Fidelity, JPMorgan, Dimensional, Franklin Templeton, Innovator, Calamos, Vanguard-profile, VanEck, and ~half the other JS-rendered sponsors.**

### T5 — SEC EDGAR 497 prospectus / sponsor-page scrape for URL hints
Pull the most recent 497 filing for a fund's CIK from EDGAR. Grep the prospectus for `http[s]://.*holdings`, `www.sponsor.com/.*{ticker}`, etc. Prospectuses often cite the exact sponsor URL where daily holdings are published. This is the cheapest "last resort" before declaring a sponsor API-less.
- **Effort per sponsor:** ~2 minutes (fetch, text-extract, grep).

### T6 — Morningstar / Bloomberg widget reverse-lookup
Some small sponsors embed a Morningstar or Bloomberg iframe on their fund pages. The iframe src contains a Morningstar Secondary Identifier (`MSID`) or Bloomberg ticker. From that ID we can derive the sponsor's "vendor holdings feed" which is the same data the public widget renders. Usable only when Morningstar's widget URL is publicly-embeddable without auth — verify per sponsor.
- **Effort per sponsor:** ~5 minutes. Low yield; escalation only.

### T7 — Sponsor subadviser decomposition (for white-label hosts)
For `tidal`, `exchange_traded_concepts`, `alpha_architect` (EA Series Trust), and the `other_sponsors` aggregate: decompose the fund list by **actual sub-adviser** (from N-CEN `SUBADVISER_NAME` or fund name prefix) and treat each sub-adviser as its own sponsor. Run T1–T5 per sub-adviser.
- Top tidal sub-advisers by fund count (spot estimates — verify during execution): YieldMax, Roundhill, Simplify, Themes, Cambria, Bitwise, Defiance, GraniteShares (subset), TBLT (Trust for Professional Managers), Matthew Cambria family.
- **Effort per sub-adviser:** same as T1–T5 for a regular sponsor (10–30 minutes total).
- **Applies to ~50 sub-advisers across the 4 white-label hosts.** Not all worth recovering (see §3 and §5).

### T8 — Manual browser inspection (top-5 sponsors only)
If T1–T4 all fail for a sponsor in the top-AUM tier, Darren or the execution session opens the sponsor's ETF page in a full Chrome browser with DevTools Network panel and manually identifies the XHR that serves holdings. Record the finding as an endpoint entry. No automation for this step — it's the escape hatch for sponsors whose JS is smart enough to detect headless and refuse to load data. Budget: 30 minutes per sponsor, capped at 5 sponsors total for the whole recovery.

### What I am NOT proposing
- **Paid data sources.** Excluded per brief.
- **Scraping authenticated institutional portals.** If a sponsor requires login to reach holdings, the fund fails Phase 0 structural test §4.1(3) (published "publicly, for free"). Exclude legitimately.
- **Using N-PORT as the Holdings_Feed_URL.** N-PORT is monthly and lagged by ~60 days. It is not a daily holdings feed. Using it would re-import the `edgar_fallback` failure pattern in a new disguise. Its legitimate uses are: (a) cross-verification that a discovered Holdings_Feed_URL serves the same positions as the filed N-PORT, and (b) Phase 2 historical backfill. Not Phase 0 Holdings_Feed_URL.
- **Per-fund URL template guessing without a content verify.** Any pattern that earns a row in `sponsor_endpoints.yaml` must have passed §7.2 content verification on at least 2 tickers (not one) from that sponsor.

## 3. Sponsor triage — 59 excluded sponsors binned by expected recovery mechanism

| Bucket | Failure mode | Sponsors (count, aggregate AUM) | Expected resolving technique |
|---|---|---|---|
| **A** | WAF/TLS fingerprint block (3xx/4xx to Python, 200 to browser) | invesco (217, $314B), schwab (33, $412B), wisdomtree (20, $22B), pimco (23, $33B), pacer (21, $4.6B), t_rowe_price (25, $14B), john_hancock (15, $7B) | **T1 with `curl_cffi`** |
| **B** | Wrong URL pattern (404 on plausible-but-incorrect path) | blackrock non-iShares (32, $30B), northern_trust/FlexShares (29, $19B), bondbloxx (27, $5B), victory_capital (33, $11B), harbor (21, $3B), janus_henderson (13, $31B), columbia (13, $5B), alliancebernstein (21, $11B), allspring (7), hartford (9), pgim (13, $14B), principal (12), alps (17, $15B), capital_group (13, $21B), touchstone (9), alger (5), goldman_sachs (5, $2B), mfs (5), russell_investments (5), neuberger_berman (13, $2B), bny_mellon (10, $8B), macquarie (6) | **T1 (pattern-expansion) + T2 (sitemap)** |
| **C** | Client-side rendered (empty HTML shell) | fidelity (61, $80B — plus ANT filter), jpmorgan (40, $123B), dimensional (37, $174B), franklin_templeton (49, $17B), innovator (22, $3B), calamos (32, $1B), vaneck (53, $84B), dimensional (covered), franklin (covered), abrdn (6), doubleline (8), putnam (6), virtus (11), tcw (12), tema (5), themes (17), inspire (9), strategy_shares (7), frontier_asset (6), gmo (6), lazard (7, connection reset), tortoise (5), sprott (12, $3B) | **T3 (inline JSON) → T4 (headless browser)** |
| **D** | DNS / timeout / flaky | nuveen (25, $11B), xtrackers (34, $24B), lazard (7), tortoise (5), tidal (128 — see bucket E) | **T1 with retry + alt hostname + T4** |
| **E** | White-label / multi-subadviser host | tidal (128, $25B aggregate), exchange_traded_concepts (15, $3B), alpha_architect (42, $6B), other_sponsors (236, $35B aggregate — 138 sub-sponsors) | **T7 — decompose to sub-adviser, apply T1–T5 per sub-adviser** |
| **F** | Too small to justify T4+ effort | All sponsors with < $2B aggregate AUM on their excluded funds (roughly: touchstone, doubleline, putnam, tcw, gmo, alger, russell_investments, mfs, strategy_shares, frontier_asset, macquarie, tema, themes, lazard, tortoise, unaffiliated_oneascent, unaffiliated_tradr, inspire, virtus, bondbloxx_alt, abrdn, sprott) | **T1 + T2 only. No T3/T4 effort.** If those fail, exclude legitimately. |
| **G** | Intra-sheet content-verify fails (bond ladders + Direxion single-stock) | iShares iBonds (19 funds, $8B), Direxion leveraged/inverse (38 funds, $2.8B), state_street 3, krane 7, neos 5, global_x 5 | **Relax `≥10 rows` gate to `≥3 rows` for specific vehicle_subtypes** (leveraged_Nx, inverse_-1x, target_maturity_bond) — policy change, not a discovery task |

One clarification on Bucket F: the ~$15–25B of combined AUM across these small sponsors is real, but the discovery cost per $1B added is an order of magnitude higher than the top tier. Bounded effort is correct here.

## 4. Priority list — 59 sponsors ordered by recovery value

Ranking combines three factors, each scored 1–3, sum is priority score (9 = highest, 3 = lowest):
- **AUM** (3 = >$50B excluded, 2 = $10–50B, 1 = <$10B)
- **Discovery expected yield** (3 = very likely solvable in T1–T3, 2 = needs T4 but precedent exists, 1 = may remain API-less)
- **NX1 relevance** (3 = mechanical rebalance at daily/monthly cadence — direct rebalance-flow research fuel, 2 = scheduled quarterly rebalance, 1 = active/opaque)

| # | Sponsor | Funds | AUM $B | AUM | Yield | NX1 | Score | Primary technique | Why |
|---|---|---:|---:|:-:|:-:|:-:|:-:|---|---|
| 1 | invesco | 217 | 313.7 | 3 | 3 | 3 | **9** | T1 curl_cffi | RSP (S&P 500 Equal Weight, quarterly mechanical rebalance, $70B) alone makes this the highest-value sponsor. WAF block is deterministic. |
| 2 | proshares | 89 | 55.3 | 3 | 2 | 3 | **8** | T1 + T4 | Daily-rebalanced leveraged/inverse ETFs — purest rebalance-flow research cell. Multiple candidate patterns failed in probes; T4 will resolve. |
| 3 | dimensional | 37 | 173.7 | 3 | 2 | 3 | **8** | T3 → T4 | DFAC/DFUS factor rebalance is NX1-native. Next.js hydration is tractable. |
| 4 | jpmorgan | 40 | 123.5 | 3 | 2 | 3 | **8** | T3 → T4 | JEPI covered-call monthly roll is the canonical mechanical flow. Large AUM. |
| 5 | schwab | 33 | 412.1 | 3 | 3 | 2 | **8** | T1 curl_cffi | Highest AUM on the list; index funds with scheduled rebalances. Akamai 403 is a TLS fingerprint issue. |
| 6 | vaneck | 53 | 83.9 | 3 | 2 | 3 | **8** | T3 → T4 | MOAT / wide-moat factor index is a pure quarterly rebalance cell. |
| 7 | fidelity | 61 | 80.5 | 3 | 1 | 3 | **7** | T3 → T4 + ANT filter | Large AUM but need to exclude Blue-Tractor ANT funds. Transparent index ETFs (FTEC, FNCL, FENY etc.) are NX1-useful. |
| 8 | wisdomtree | 20 | 21.6 | 2 | 3 | 3 | **8** | T1 curl_cffi | Dividend-weighted index quarterly rebalance, Cloudflare-only block. High yield per effort. |
| 9 | northern_trust (FlexShares) | 29 | 18.9 | 2 | 3 | 3 | **8** | T1 pattern-expansion | Multifactor quarterly rebalance. Probably `flexshares.com/funds/{ticker}` (plural); previous session tried singular. |
| 10 | alpha_architect subadvisers | 42 | 5.8 | 1 | 2 | 3 | **6** | T7 decomposition | Each Alpha Architect fund is its own sub-adviser. Factor quant rebalancing is NX1-clean. QMOM, QVAL etc. have mechanical quarterly rebalance. |
| 11 | nuveen | 25 | 10.8 | 2 | 2 | 3 | **7** | T1 with timeout + T2 | Factor / multifactor ETFs. DNS timeout was transient — retry. |
| 12 | pimco | 23 | 33.0 | 2 | 2 | 1 | **5** | T1 curl_cffi | Cloudflare 403. Active bond funds, less mechanical but large AUM. |
| 13 | franklin_templeton | 49 | 17.2 | 2 | 2 | 2 | **6** | T3 → T4 | Next.js site; JSON hydration extractable. |
| 14 | innovator | 22 | 2.8 | 1 | 2 | 3 | **6** | T3 → T4 | Buffered / defined-outcome ETFs — monthly roll is mechanical and NX1-canonical despite low AUM. |
| 15 | calamos | 32 | 0.9 | 1 | 2 | 3 | **6** | T3 → T4 | Same as Innovator — buffered ETFs. |
| 16 | janus_henderson | 13 | 31.0 | 2 | 2 | 2 | **6** | T1 pattern-expansion | AAA (CLO ETF) has scheduled rebalance; large AUM. |
| 17 | capital_group | 13 | 20.9 | 2 | 2 | 1 | **5** | T1 pattern-expansion | Active but rules-based; published holdings daily. Pattern 404 — likely wrong path. |
| 18 | t_rowe_price | 25 | 14.3 | 2 | 2 | 1 | **5** | T1 curl_cffi | Most T. Rowe ETFs are ANT (Precidian). Must filter ANTs; transparent T. Rowe TCAF is NX1-useful. |
| 19 | blackrock (non-iShares) | 32 | 29.9 | 2 | 3 | 2 | **7** | T1 pattern-expansion | BlackRock runs several non-iShares ETF lines (BKCH, BMEZ, etc.). 404 suggests pattern is `/us/individual/products/etfs/{ticker}` not `/us/individual/products/{ticker}`. |
| 20 | alliancebernstein | 21 | 10.7 | 2 | 2 | 2 | **6** | T3 | AB has a React fund-finder. Active + quant ETFs. |
| 21 | pacer | 21 | 4.6 | 1 | 3 | 3 | **7** | T1 curl_cffi | Cash-cows COWZ is factor quarterly rebalance. High NX1 value despite low AUM. 403 blocked. |
| 22 | alps | 17 | 15.3 | 2 | 2 | 2 | **6** | T1 pattern-expansion | AMLP and partners MLP index — quarterly rebalance. |
| 23 | victory_capital | 33 | 11.4 | 2 | 2 | 2 | **6** | T1 pattern-expansion | VictoryShares mixed factor. 404 on `/etfs/`. |
| 24 | john_hancock | 15 | 7.0 | 1 | 2 | 2 | **5** | T1 curl_cffi | Multifactor ETFs, Cloudflare 403. |
| 25 | xtrackers | 34 | 23.9 | 2 | 1 | 2 | **5** | T1 alt hostname + T4 | DNS on `etfus.dws.com` was unresolvable in probe environment — could be environment-specific. Retry from MCAS VPS. |
| 26 | pgim | 13 | 13.5 | 2 | 2 | 2 | **6** | T3 → T4 | Active fixed income. |
| 27 | bondbloxx | 27 | 4.7 | 1 | 3 | 2 | **6** | T1 pattern-expansion | Specialized bond ETFs, pattern almost certainly `/etfs/` not `/product/`. |
| 28 | harbor | 21 | 3.3 | 1 | 2 | 1 | **4** | T1 pattern-expansion | Active ETFs. |
| 29 | columbia | 13 | 5.4 | 1 | 2 | 1 | **4** | T1 pattern-expansion | Active / factor. |
| 30 | tidal (YieldMax sub-adviser) | ~30 | ~12 | 2 | 2 | 3 | **7** | T7 → T1 on `yieldmaxetfs.com` | YieldMax single-stock covered-call ETFs have monthly option roll — mechanical rebalance. High NX1 value. |
| 31 | tidal (Roundhill sub-adviser) | ~20 | ~3 | 1 | 2 | 3 | **6** | T7 → T1 on `roundhillinvestments.com` | Roundhill single-stock leveraged ETFs — daily rebalance. |
| 32 | tidal (Simplify sub-adviser) | ~15 | ~4 | 1 | 2 | 2 | **5** | T7 → T1 on `simplify.us/etfs` | |
| 33 | tidal (Themes/Cambria/Defiance/Bitwise) | ~50 | ~6 | 1 | 2 | 2 | **5** | T7 | Scan per sub-adviser. |
| 34 | principal | 12 | 5.5 | 1 | 2 | 2 | **5** | T1 pattern-expansion | Factor ETFs. |
| 35 | hartford | 9 | 4.7 | 1 | 2 | 2 | **5** | T1 pattern-expansion | |
| 36 | other_sponsors (top-10 sub-sponsors by fund count) | ~60 | ~20 | 2 | 2 | 2 | **6** | T7 → T1 per sub-sponsor | Triage only; do not attempt all 138. |
| 37 | neuberger_berman | 13 | 1.7 | 1 | 1 | 2 | **4** | T1 rate-limited | 429 throttled. Retry with longer backoff. |
| 38 | exchange_traded_concepts (top 3 sub-advisers) | ~15 | ~3 | 1 | 2 | 2 | **5** | T7 | Tri-fund issuer; do not attempt all 15. |
| 39–59 | All remaining Bucket F sponsors (< $2B excluded AUM) | 150+ | ~15 | 1 | 1 | 1 | **3** | T1 + T2 only | Exclude legitimately if those fail. |

**Ordering summary:** Top 12 sponsors alone cover ~$1.37T of excluded AUM (82% of the recovery opportunity). If those land, everything else is optimization.

## 5. Effort budget per sponsor

Five tiers. Each tier has a fixed cost; a sponsor escalates to the next tier only if the previous one fails to produce a content-verified endpoint. **A sponsor is declared genuinely API-less only after tiers 1–4 have all failed and their failure artifacts exist on disk.**

| Tier | Technique | Max wall-clock per sponsor | Ceiling budget | What evidence must exist on disk before declaring failure |
|---|---|---|---|---|
| **Tier 1** | T1 (curl_cffi pattern enumeration) + T2 (sitemap) | **90 seconds** | Automated for all 59 | `sponsor_probes_v2.jsonl`: one line per (sponsor, pattern) with status, bytes, content-verify result |
| **Tier 2** | T5 (EDGAR 497 grep) + T3 (inline-JSON hydration) | **3 minutes** | Automated for all that fail Tier 1 | Per-sponsor `source_authoring/phase_0/logs/sponsor_discovery/{sponsor}/tier2.md` — fetched page snippets, extracted candidate URLs, verify results |
| **Tier 3** | T4 (headless Chromium network-tab capture, 2 tickers) | **10 minutes** | Top-30 priority sponsors only (score ≥ 5) | Per-sponsor `source_authoring/phase_0/logs/sponsor_discovery/{sponsor}/network_capture.json` — full XHR dump with scoring, at least 3 probed candidates |
| **Tier 4** | T7 (sub-adviser decomposition) | **30 minutes aggregate** for each of the 4 white-label hosts | tidal, exchange_traded_concepts, alpha_architect, other_sponsors | Per-host `source_authoring/phase_0/logs/sponsor_discovery/{host}/subadviser_decomposition.md` listing all sub-advisers and the Tier 1–3 result for each |
| **Tier 5** | T8 (Darren or execution-session manual browser inspection) | **30 minutes per sponsor**, capped at 5 sponsors across the whole recovery | Only top-tier sponsors (score ≥ 7) that failed Tiers 1–3 | `source_authoring/phase_0/logs/sponsor_discovery/{sponsor}/manual_inspection.md` — URL, response sample, chosen pattern |

**Hard exclusion criterion.** A sponsor is added to `excluded_sponsors_v2.yaml` (with reason `api_genuinely_absent_verified`) only if ALL of these are true:
- Tier 1 produced at least 8 failed pattern attempts across ≥3 URL shapes and ≥2 hostnames.
- Tier 2 produced a fetched sitemap.xml (or confirmed 404) and at least one 497 prospectus text grep result.
- For score ≥ 5 sponsors: Tier 3 produced a network capture with ≥20 XHRs recorded and the top-5 XHR URLs probed against content_verify.
- The sponsor has < $2B aggregate AUM OR Tier 4/5 has been applied and still failed.

If any of those conditions are not met, the sponsor goes to a different file: `needs_review.yaml`. Darren triages manually before the recovery session commits. **This prevents the loose "10 min, moved on" gate the previous session used.**

### Why these thresholds are defensible (not arbitrary)
- **Tier 1 (90s) isn't arbitrary — it's the pattern catalogue × retry time.** 12 patterns × 2 hostnames × 3s each = 72s. The budget is the work, not a guess at "long enough to try hard."
- **Tier 3 (10 min) is the headless Chromium load + network-idle budget.** A Chromium page load with network idle timeout is ~8 seconds; multiplied by 2 tickers and 3 verified candidates, plus Playwright startup overhead, that's ~10 min.
- **Tier 5 (30 min, capped at 5 sponsors)** is a hard cap against "I'll just spend an hour on this one" creep. If 5 sponsors need manual effort, something structural is wrong with the toolchain and Darren should see the evidence before the session commits.

### Parallelism budget (so this completes in one session, not five)
- Tier 1: 59 sponsors × 90s = ~90 min serial. Run with 8-way concurrency (Tier 1 is I/O-bound) → **~15 min wall clock**. Well under 16-core headroom. No memory concern.
- Tier 2: ~40 sponsors × 3 min = ~120 min serial. 8-way concurrency → **~15 min wall clock**.
- Tier 3: ~20 sponsors × 10 min = ~200 min serial. 4-way concurrency (memory-limited for Chromium) → **~50 min wall clock**. ~1.6GB RSS peak.
- Tier 4: Fixed 30 min per host × 4 hosts = **~60–120 min** depending on sub-adviser count.
- Tier 5: 5 × 30 min = 150 min wall clock (mostly idle waiting for Darren).
- **Total estimated wall-clock:** ~2.5 hours automated + ~2.5 hours Darren's manual inspection time if all 5 Tier 5 slots are used.

This is well under one session. If the execution session blows past 4 hours without committing, something failed — escalate rather than pushing through.

## 6. Handling sponsors that genuinely have no API

Two paths, picked per sponsor based on what the evidence tells us:

**Path A — Exclude for Phase 0, reconsider in Phase 1.** Used when all 5 tiers exhaust and there is positive evidence the sponsor does not publish daily holdings publicly (e.g., sponsor page explicitly says "holdings available to registered advisors only", OR the prospectus cites N-PORT as the sole public position disclosure). Reason code: `api_genuinely_absent_verified`. Full Tier 1–5 artifacts preserved for Phase 1 re-attempt.

**Path B — Exclude for Phase 0, flag for Phase 2 N-PORT integration.** Used when the sponsor publishes monthly/quarterly position data via SEC N-PORT only. These are structurally outside the Phase 0 daily-holdings scope per taskboard §4.1(3). Reason code: `quarterly_only_via_n_port`. The funds remain in `excluded_funds.yaml` with the full v1 row preserved so Phase 2 can reinstate them as a quarterly-cadence research cell.

**What I will NOT propose:**
- Per-fund URL-template guessing as a fallback — that's exactly the v1 failure mode.
- Using the Prospectus_URL as Holdings_Feed_URL — same failure class.
- Any `edgar_fallback` / `pattern_guess` placeholder format — explicitly banned by the remediation taskboard.

**Chosen default:** Path A for sponsors with no public daily feed. Path B for the subset whose only public position data is N-PORT. Both are real exclusions, not placeholders. Both are reversible in later phases by re-running this plan's T1–T5 under different environments (e.g., residential proxy) or by loosening Phase 0's daily-cadence gate. Neither admits a fund to the bible without a content-verified `Holdings_Feed_URL`.

## 7. Metadata extraction beyond Holdings_Feed_URL

Every sponsor product-index endpoint discovered in Tiers 1–4 will carry most of the currently-null columns. The recovery script must extract them in the same pass. Target per-column extraction rates, by sponsor tier:

| Column | Source | Expected coverage after recovery | Notes |
|---|---|---:|---|
| `Inception_Date` | Sponsor product-index JSON (all major sponsors carry this) | 90%+ | N-CEN does NOT carry this; sponsor pages do. |
| `Expense_Ratio_Bps` | Sponsor product-index JSON | 95%+ | Usually listed as `expense_ratio` or `gross_expense_ratio` in the same payload as AUM. Parse in bps (multiply by 100 if given as percentage). |
| `Shares_Outstanding` | Sponsor product-index JSON | 70%+ | Some sponsors carry this, others don't. For those that don't, derive from `AUM_USD / NAV` if both are in the payload. |
| `Benchmark_Name` | Sponsor product-index JSON | 85%+ | For passive ETFs this is almost always present. Active ETFs: populate as `"null"` (already permitted by the remediation taskboard). |
| `Holdings_Feed_Lag_Hours` | Parsed from sponsor fund page ("holdings as of [date]") | 60%+ | For CSV/XLSX feeds with a date-stamp header row, compute `now() - date_header` in hours. For html_table feeds, parse the "as of" text. For JSON feeds, use the `as_of` / `dataDate` field if present. For feeds without timestamps, keep `"null"`. |
| `Rebalance_Trigger_Rules` | Sponsor prospectus (one per sponsor family) — T5 batch prospectus read | 50%+ | Deferred from the per-fund path to a **sponsor-master-prospectus read pass** (~30–40 prospectus reads for the recovered sponsors). Keep under 200 chars. |
| `Vehicle_Subtype` | Sponsor product-index JSON + fund-name regex (fallback) | 95%+ | Most sponsors tag their funds (e.g., "Factor", "Leveraged", "Currency-Hedged"). Fill gaps from fund_name regex like v1 did. |
| `Benchmark_Provider` | Derived from `Benchmark_Name` via existing v1 regex | 90%+ | Free once Benchmark_Name populated. |

**Prospectus_URL upgrade (secondary, optional):** upgrade from EDGAR filings-listing URL to direct 497 PDF URL using the EDGAR JSON-companyfacts endpoint. This is Phase 1 work in the previous remediation's deferral list; adding it here is ~1 hour of work and avoids leaving Phase 0 with a known-gappy column. **Recommend: do it in this recovery session.**

**Content-verify gate relaxation for bond ladders and single-stock leveraged (Bucket G recovery):**
- Add `Vehicle_Subtype ∈ {target_maturity_bond, leveraged_Nx, inverse_-Nx}` exception: minimum row count = 3 instead of 10.
- Add `Vehicle_Subtype ∈ {single_stock_leveraged, single_stock_inverse}` exception: minimum row count = 1 (these hold 1 swap + money-market + a few cash positions).
- Both apply only to the Bucket G recovery (re-verifying already-excluded iShares iBonds and Direxion rows); they do NOT loosen the gate for buckets A–F new recoveries.
- **Two separable concerns — scoped explicitly:**
  - **(a) Execution scope for this recovery:** apply the relaxation to reinstate the ~80 already-excluded intra-sheet rows (iShares iBonds, Direxion single-stock leveraged, etc.). This happens as part of this session's work.
  - **(b) Forward-looking precedent:** record in `source_authoring/phase_0/notes.md` as a precedent set during this recovery. Do NOT codify in `PHASE_0_REMEDIATION_TASKBOARD.md` — whether future Phase 0 work admits these vehicle subtypes at a relaxed threshold is a Phase 1+ governance decision, not one this plan makes unilaterally.

## 8. Recovery estimate — self-honest

Forecasting per bucket:

| Bucket | Sponsors | Funds | AUM $B | Expected recovery | Reason |
|---|---:|---:|---:|:---:|---|
| A (WAF/TLS) | 7 | 354 | 807 | **6 of 7** | curl_cffi is a known solution. Only risk is sponsor-specific anti-bot (e.g., challenge-response) on Schwab or one other. Even if 1 fails, 6 of 7 covers ~98% of the bucket's AUM. |
| B (Wrong URL) | 23 | 350 | 251 | **17 of 23** | Pattern expansion resolves most. Some sponsors legitimately have no public feed (e.g., small active managers whose only "holdings" page requires advisor login). Will find those via Tier 2. |
| C (JS-rendered) | 15 | 300 | 350 | **10 of 15** | Headless Chromium resolves most, but 3–5 sponsors use anti-headless detection or client-side-only data (holdings computed in JS from other sources). |
| D (DNS/timeout) | 4 | 60 | 37 | **3 of 4** | Mostly flaky transient. xtrackers may remain problematic if DNS issue is persistent. |
| E (white-label) | 4 hosts | 421 | 69 | **5–8 sub-advisers of ~200** | Will cover YieldMax, Roundhill, Simplify, Themes, maybe Bitwise and Defiance. Long tail of 100+ tiny sub-sponsors gets tier-1-only treatment and most will stay excluded. |
| F (tiny sponsors) | ~20 | ~100 | 12 | **5–8 of 20** | Best-effort. Many small shops publish holdings PDFs only (excluded) or require advisor login (excluded). |
| G (intra-sheet content-verify) | — | 95 | 12 | **80 of 95** | Policy change — relaxing the ≥10-row gate for specific vehicle_subtypes reinstates these mechanically. |

**Aggregate honest estimate:**
- **Sponsors recovered: 36–41 of 59** (61–69%)
- **Plus 80+ intra-sheet rows reinstated from Bucket G policy change.**
- **Funds added back to bible: 900–1,150** (51–65% of the 1,757 excluded)
- **AUM added back: $1.35–1.55T** (80–92% of the $1.68T excluded). AUM recovery is higher than fund-count recovery because the top-tier sponsors (Invesco, Schwab, Dimensional, JPMorgan, VanEck, ProShares, Fidelity) account for most of the AUM and are all in the high-confidence buckets A and C.
- **Final bible size: 1,700–2,000 funds** (up from 806; ICI-ratio moves from 83.7% to ~95–98%).

### Where the gap lives (the 800–900 funds we still won't recover)
- ~200 tidal/exchange-traded-concepts sub-adviser funds whose issuers have no public daily feed (advisor-portal-only or PDF-only).
- ~150 "other_sponsors" micro-issuers that don't justify Tier 3+ effort.
- ~100 ANT (active non-transparent) Fidelity/Precidian funds — these are structurally excluded per taskboard §4.1 and stay excluded.
- ~50 funds in small sponsors that publish quarterly only via N-PORT (Path B from §6).
- ~100 funds whose sponsor sites use anti-headless-browser detection that Tier 5 doesn't have budget to bypass.
- Remainder: long tail of launches <6 months ago, shell funds, and edge cases.

**If the recovery lands at 36 sponsors / 900 funds, I'll count that as the realistic ceiling and recommend not chasing the remainder in Phase 0.** The marginal $1B of added AUM per extra hour of work past that point isn't worth the session budget. Phase 2+ re-evaluation is the right home for them.

### What would make me revise this estimate downward mid-session
- **curl_cffi getting blocked by Cloudflare Enterprise or PerimeterX.** If it happens on 2+ Bucket A sponsors, the whole bucket's estimate drops from 6/7 to 2–3/7.
- **Playwright's chromium being detected as headless by major sponsors' WAFs.** If Dimensional, JPMorgan, or Fidelity all reject headless in Tier 3, drop Bucket C estimate from 10/15 to 4–6/15.
- **ProShares requiring JavaScript-executed challenge for holdings download.** If so, exclude ProShares from Phase 0 recovery and defer to Phase 2 with a clear reason. ProShares is the single highest-value NX1 cell, so I'd flag this escalation to Darren immediately rather than silently fail.

## 9. Execution sequencing — single session, four phases (+ optional Phase 5)

Single session end-to-end. No mid-session approval gate. The §12 escalation triggers remain the only exit path — if one fires, STOP, write the state report, do NOT commit. Otherwise the session runs straight through to the atomic commit.

### Phase 1 — Automated Tier 1–3 recovery + Bucket G reinstatement (~30 min wall-clock)
1. Write `source_authoring/phase_0/sponsor_discovery_v2.py` — Tier 1 probe with `curl_cffi` (Chrome TLS impersonation) across all 59 sponsors × the expanded pattern catalogue from §2 T1. 8-way concurrent. Writes `sponsor_probes_v2.jsonl` and appends verified patterns to `registry/sponsor_endpoints_v2.yaml`.
2. Write `source_authoring/phase_0/tier2_edgar_and_sitemap.py` — for every sponsor that failed Tier 1, fetch `/sitemap.xml`, `/sitemap_index.xml`, `/robots.txt`, and the latest 497 prospectus text from EDGAR; grep for holdings URLs; verify candidates. 8-way concurrent. Appends verified patterns to `sponsor_endpoints_v2.yaml`.
3. Write `source_authoring/phase_0/tier3_hydration_extract.py` — for every sponsor whose landing page is SSR React / Next.js / Angular (detect via HTML meta / script fingerprints), extract `__NEXT_DATA__`, Redux/Apollo hydration state, JSON-LD; pull candidate holdings URLs out of the hydration blob; verify. Appends.
4. Apply Bucket G (a) from §7: update `content_verify.py` with vehicle_subtype-specific row-count minimums. Re-verify the ~95 intra-sheet content-verify failures; reinstate those that pass the relaxed gate. Append the Bucket G (b) forward-looking precedent note to `source_authoring/phase_0/notes.md`.

### Phase 2 — Tier 4 headless browser + Tier 7 sub-adviser decomposition (~90 min wall-clock)
5. Write `source_authoring/phase_0/tier4_headless_capture.py` — Playwright + headless Chromium; for each top-30 sponsor (score ≥ 5 per §4) that failed Phase 1, load 2 known tickers, wait for network idle, dump every XHR URL, score by keyword (`holdings`, `portfolio`, `composition`, `position`, `constituent`), probe top-3 candidates against `content_verify`. 4-way concurrent, memory-bounded at ~1.6 GB RSS peak. Full XHR dumps persisted under `source_authoring/phase_0/logs/network_captures/{sponsor}.json` so future sessions can skip re-capturing.
6. Write `source_authoring/phase_0/tier7_subadviser_decomp.py` — for `tidal`, `exchange_traded_concepts`, `alpha_architect`, and the top-10 `other_sponsors` sub-sponsors by fund count: partition by `SUBADVISER_NAME` from N-CEN (or fund_name prefix if absent). Treat each sub-adviser as its own sponsor and run Phase 1's T1–T3 against each. Cap at top-10 sub-advisers per host by fund count; long-tail stays excluded with reason `subadviser_long_tail`.

### Phase 3 — Metadata second pass + Prospectus_URL upgrade (~30 min wall-clock)
7. Batch sponsor-master-prospectus read for `Rebalance_Trigger_Rules`: for each sponsor family with ≥5 recovered funds, fetch the master prospectus once and extract the rebalance-rules paragraph (≤200 chars, free text).
8. Metadata fill: populate `Inception_Date`, `Expense_Ratio_Bps`, `Shares_Outstanding`, `Benchmark_Name`, `Holdings_Feed_Lag_Hours` using the JSON already captured during Tiers 1–4 (no re-fetching).
9. `Prospectus_URL` upgrade: for every row in the bible (including the 806 previously-remediated + newly-recovered), swap the EDGAR filings-listing URL for the direct 497 PDF URL via EDGAR's company-facts JSON endpoint. Rate-limited to SEC's 10 rps per `reference_sec_ncen.md`.

### Phase 4 — Verification + atomic commit (~20 min wall-clock)
10. Rebuild the Overview sheet from scratch per §10 item 3. No v1 fragments retained.
11. Run the full §7 verification suite on the atomic `.tmp` bible: schema (§7.1), content-verify (§7.2), URL distinctness (§7.3), coverage (§7.4), sanity (§7.5).
12. Run `pytest -v tests/test_fund_bible_schema.py`.
13. `grep -rE 'edgar_fallback|template_guess|pattern_guess|pending Phase' fund_holdings/ground_truth/` — must return zero results.
14. Atomic-rename `fund_bible_v1.xlsx.tmp` → `fund_bible_v1.xlsx`.
15. Write `PHASE_0_SPONSOR_RECOVERY_STATE.md` with before/after counts, per-bucket recovery rate vs. §8 forecast, per-sponsor outcomes, and deferred items for Phase 1/2.
16. Commit + push. Single commit from MCAS root, scope `fund_holdings/` only. Message template: `phase_0_recovery: +N sponsors, +M funds (-R removed), bible total K funds, AUM $X.XT`.

### Phase 5 — Optional Tier 5 manual inspection (only if triggered)
Triggers only if Phase 2 leaves ≥ 3 top-priority sponsors (score ≥ 7) unrecovered AND fewer than 5 Tier 5 slots have been consumed. If triggered: STOP after Phase 4 writes the `.tmp` (do NOT rename to live yet), write an abridged state file listing the unrecovered top-priority sponsors, and surface to Darren. Darren performs manual Chrome DevTools inspection (30 min per sponsor, cap 5). Resume from Phase 4 step 14 once new endpoints land in `sponsor_endpoints_v2.yaml`.

### Total wall-clock estimate
Phases 1–4: **~2h 40m**. Phase 5 adds up to 2.5 hours of Darren's time if triggered.

### Escalation triggers (unchanged from §12)
Any one of these fires → STOP, write state report, do NOT commit:
- `curl_cffi` blocked on 2+ Bucket A sponsors.
- Playwright detected as headless on any 3 of the top-5 C-bucket sponsors (Dimensional, JPMorgan, Fidelity, VanEck, Franklin Templeton).
- ProShares requires JavaScript-executed challenge for holdings download.
- Memory pressure exceeds 4 GB RSS during Phase 2 even at 2-way concurrency.
- Sub-adviser decomposition in Tier 7 uncovers > 80 distinct sub-advisers (scope explosion).

Otherwise: straight through to Phase 4 commit.

## 10. Definition of done

For this recovery plan's execution:
1. `registry/sponsor_endpoints_v2.yaml` exists with one entry per newly-recovered sponsor, `verified_serves_holdings: true` on each after content-verify on ≥2 tickers per sponsor.
2. `fund_bible_v1.xlsx` has grown from 806 rows to 1,700–2,000 rows (per §8 estimate).
3. **The Overview sheet is rebuilt from scratch to reflect post-recovery state only.** No v1 fragments retained. Single Total row. Per-sponsor row counts and AUM match the actual data on each sponsor sheet. Any v1-era or mid-remediation artefacts (the "138 small sponsors merged" note and similar) are updated to current state or removed.
4. `Inception_Date`, `Expense_Ratio_Bps`, `Benchmark_Name`, `Shares_Outstanding` coverage ≥ 80% across the bible (currently 0%).
5. `Holdings_Feed_Lag_Hours` coverage ≥ 50%.
6. Every recovered sponsor's funds pass §7.2 content-verify at commit time.
7. The 9 previously-remediated sponsors' `Holdings_Feed_URL`, `Holdings_Feed_Format`, and identification fields are unchanged. `Prospectus_URL` upgrade (item 11) counts as additive metadata enrichment on those rows, not a re-touch of the settled holdings data.
8. `excluded_sponsors_v2.yaml` documents sponsors that stayed excluded after the recovery, with per-sponsor Tier 1–5 evidence artifacts on disk (enabling future phases to pick up where this left off).
9. `PHASE_0_SPONSOR_RECOVERY_STATE.md` documents before/after counts, per-bucket recovery rate vs. §8 forecast, and deferred items.
10. Pytest suite `tests/test_fund_bible_schema.py` passes.
11. `Prospectus_URL` upgraded from EDGAR filings-listing URL to direct 497 PDF URL for every row in the bible (recovered + previously-remediated).
12. Single commit for the recovery execution. (The plan revision is a separate commit; two commits total for the whole effort.)
13. No scripts or artifacts touch anything outside `fund_holdings/`.

## 11. Explicit non-scope (match remediation taskboard §10)

Do NOT:
- Re-touch the 9 successfully-remediated sponsors (iShares, SSGA, Vanguard, Direxion, First Trust, Global X, KraneShares, GraniteShares, NEOS). They are settled.
- Re-derive N-CEN-sourced columns (`Sponsor`, `Exchange`, `Vehicle_Type`, etc.). They're TRUST per §6.1 of the remediation taskboard.
- Introduce any fallback URL formats (`edgar_fallback`, `template_guess`, `pattern_guess`, `n_port_fallback`, etc.). Content-verified or excluded. No middle ground.
- Use N-PORT filings as `Holdings_Feed_URL`. They're lagged and non-daily.
- Scrape authenticated institutional portals — those fail structural test §4.1(3).
- Download actual holdings data. Phase 0 is still a catalogue, not an ingestion pipeline.
- Modify equities_news/ or other MCAS subsystems.
- Propose paid data sources.
- Commit half-verified entries to `sponsor_endpoints_v2.yaml`. Every entry in that file must have `verified_serves_holdings: true` with evidence on at least 2 tickers from that sponsor.

## 12. Risks and escalation gates

| Risk | Trigger | Response |
|---|---|---|
| curl_cffi insufficient for Schwab/Invesco/WisdomTree | Tier 1 fails on 2+ Bucket A sponsors | STOP session, write state report, do NOT commit. Surface to Darren with the failure artifacts. Options: (a) browser-TLS-replay, (b) accept the exclusion for Phase 0. Residential proxies excluded by D5. |
| Playwright detected as headless | Tier 3 fails on Dimensional + Fidelity + JPMorgan (3 largest C-bucket sponsors) | Same — escalate with evidence. Options: (a) stealth plugin, (b) full browser mode with Xvfb, (c) defer to Phase 1. |
| Memory pressure | Headless Chromium × 4 workers + bible in-memory exceeds 4GB RSS | Drop Playwright concurrency to 2. Swap does not exist on the VPS; OOM will kill the process (see `feedback_oom_prevention.md`). Estimate peak RSS explicitly before launching Tier 3. |
| Sub-adviser scope explosion | Tidal decomposition uncovers 80+ distinct sub-advisers | Cap Tier 1 effort at top-10 by fund count. Long tail gets excluded with reason `subadviser_long_tail`. Re-evaluate in Phase 1. |
| Darren's manual-inspection time budget exceeded | Phase 5 triggered for ≥10 sponsors, or Tier 5 takes ≥45 min per sponsor once started | Reduce Tier 5 cap to 3 sponsors. The remaining stay excluded with full Tier 1–4 artifacts so Phase 1 has everything needed to pick up. |
| Content-verify gate false negatives on recovered funds | A sponsor's endpoint serves holdings but format parsing fails | **Do NOT relax the gate to pass.** Instead, fix the format parser in `content_verify.py` to handle the new format (a new AEM dispatcher-compressed JSON, a new CSV header variant, etc.). Record the format quirk in `notes.md`. |
| "Dead" sponsor turns out to have an edge | During execution, a sponsor flagged for exclusion is found to publish via an unexpected channel (e.g., a GitHub repository of holdings snapshots) | Record but do not act without Darren's approval — exotic channels may be unreliable. The feedback memory `feedback_dont_declare_dead.md` applies: a failure to find the obvious endpoint doesn't mean the sponsor is dead, but a non-standard channel warrants a sanity check before committing. |

## 13. Decisions locked in (resolved from Revision 1's open questions)

Revision 1 ended with six open questions. Revision 2 resolves them with positions and reasoning. These are session defaults; §12 escalation triggers remain the only in-flight override.

**D1 — Single session, no mid-session approval gate.** Resolved per Darren's approval-brief Change 1. The §12 triggers are sufficient post-hoc escape hatches; a pre-hoc gate added no information. §9 is rewritten as four phases (+ optional Phase 5) executing end-to-end.

**D2 — Tier 3 / Tier 4 admission threshold: priority score ≥ 5 (~top 30 of 59 sponsors).** The excluded-sponsor AUM distribution has a clean shoulder at ~$2B. Below that, Tier 3's 10-minute headless-Chromium wall-clock per sponsor buys less than $200M of bible AUM per minute — below the unit economics of Phase 0 research labour. Score ≥ 5 (from §4) admits every $2B+ sponsor plus the low-AUM-but-NX1-critical cells (Innovator at $3B, Alpha Architect at $6B, Pacer at $5B). Using **score** as the gate rather than **AUM** is deliberate: it lets NX1 rebalance-flow relevance override pure size. Bucket F sponsors below the line still receive Tier 1 + Tier 2 (nearly free per §5) but consume no Tier 3+ budget.

**D3 — `Prospectus_URL` upgrade is in scope for this recovery.** The bible has a known-gappy column (every row currently points to an EDGAR filings-listing URL, not a direct 497 PDF). Upgrading costs ~30 min of Phase 3 work against EDGAR's company-facts endpoint, amortises the EDGAR fetch budget already needed for Tier 2, and removes a known Phase 1 item. Deferring leaves a column Phase 1 would have to re-author from scratch. Do it now. (DoD item 11 in §10.)

**D4 — Tier 5 manual-inspection cap: 5 sponsors × 30 min.** Triggers only if Phase 2 leaves ≥ 3 top-priority (score ≥ 7) sponsors unrecovered. 5 × 30 = 150 min is the defensible middle: a higher cap invites "just one more" drift against Darren's own time; a lower cap surrenders Phase 0 ground on sponsors a 10-min manual browser inspection would have resolved. Cap stays at 5.

**D5 — No residential proxies by default.** If `curl_cffi` is blocked by a sponsor's WAF, escalate per §12 — don't route around with a residential proxy. Proxies blur Phase 0 structural test §4.1(3) ("published publicly, for free, on the sponsor's website"): if the endpoint is only reachable from a residential IP, it isn't truly public, and the row commits under a broken promise. Remains an escalation, not a default.

**D6 — Tier 1 runs on all 59 sponsors. Tier 3+ only on top-30 by score.** Tier 1 at 90s × 59 sponsors / 8-way concurrency ≈ 11 min — dropping any sponsor saves no meaningful budget, and surprise hits in the long tail are where unknown-unknowns live (the iShares iBonds reinstatement is an example of a "not worth bothering with" category that turned out to carry real AUM). The cost discipline is at Tier 3, not Tier 1. No sponsor is dropped from Tier 1 scope.

---

**End of plan.** Revision 2 is ready for Darren's final review. On approval: commit this revision, then execute Phases 1–4 per §9. Escalate per §12 if any trigger fires.
