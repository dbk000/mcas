# Phase 0 Remediation — Fund Bible (NX1 Rebalancing Reverse-Engineering)

**Owner:** Darren
**Executor:** Claude Code, one-shot session
**Parent strategy:** NX1 — Institutional Rebalancing Reverse-Engineering (concept stage; this phase produces the substrate, not the strategy itself)
**Governance posture:** Research-grade. Structure must match production kernel patterns so this artifact can be retro-hardened before live capital. No three-laws audit required per-step in this session — structure first, rigor later.

---

## 0. WHY THIS IS A REMEDIATION — READ THIS BEFORE ANYTHING ELSE

Phase 0 ran and produced `fund_bible_v1.xlsx` with 2,563 funds. This taskboard remediates that bible **in place** — same file, same row IDs where they survive, no parallel `v2` artifact. The work CC already did to identify, classify, size, and group the universe is preserved. The work that failed gets replaced.

**What v1 got right and stays:** Universe enumeration from N-CEN (2,563 funds correctly identified). Sponsor classification (one sheet per sponsor family). N-CEN-derived structured fields: `Vehicle_Type`, `Vehicle_Subtype`, `Replication_Method`, `Rebalance_Frequency_Prospectus`, `Creation_Unit_Size`, `AUM_USD`, `AUM_AsOf_Date`, `Has_Derivatives_Overlay`, `Currency_Hedged`, `Fund_Of_Funds`, `Sponsor`, `Exchange`, `Benchmark_Provider`, `Primary_Market_Cap_Tier`, `Geographic_Focus`. These are correct (verified by spot-check: HEFA/HEZU correctly currency-hedged, Direxion correctly daily-rebalanced, leveraged ETFs correctly flagged). Do not re-fetch them.

**What v1 got wrong and gets replaced:** 1,715 of 2,563 funds (66.9%) have `Holdings_Feed_URL` pointing to a SEC EDGAR search results page rather than a real holdings feed, with `Holdings_Feed_Format = edgar_fallback`. Entire major sponsors landed at 0% real coverage: iShares (349), State Street (152), Schwab (33), VanEck (53), xtrackers (34). The companion fields populated alongside this failure are also bad: `Sponsor_Page_URL` and `Prospectus_URL` for these funds are generic EDGAR CIK searches, not fund-specific pages. Six fields are 100% null across all 2,563 rows: `Inception_Date`, `Shares_Outstanding`, `Expense_Ratio_Bps`, `Benchmark_Name`, `Rebalance_Trigger_Rules`, `Holdings_Feed_Lag_Hours` — none of these were ever attempted at the sponsor-page level. `Internal_Crossing_Disclosed` is 100% False, suspect-defaulted.

**The two structural failures that produced this:**
1. The source strategy was per-fund template-guessing rather than per-sponsor authoritative discovery. (E.g. CC made up `https://www.ishares.com/us/products/search?filter=ticker:{TICKER}` — real iShares URLs need a numeric product_id obtainable only from iShares' product-screener JSON endpoint.)
2. The verification gate measured "URL returns HTTP 200" rather than "URL serves actual holdings data." A SEC EDGAR search URL returns 200, so 1,715 placeholder URLs passed the gate.

**This remediation fixes both at the source.** Per-sponsor product-index endpoints are discovered once per sponsor and called once per sponsor, returning canonical URLs and most missing metadata for every fund that sponsor manages. Every URL that lands in the bible is content-verified — the response is fetched and parsed to confirm it actually serves position-level holdings data, not a search results page or fund-list landing page.

**There is no fallback URL state in this remediation.** If §6.2's discovery doesn't yield a verifiable holdings URL for a given sponsor, every fund from that sponsor is **removed from the bible** and logged in `excluded_funds.yaml`. `edgar_fallback` is not a permitted Holdings_Feed_Format value after this session.

If at any point during execution you find yourself writing `edgar_fallback`, `template_guess`, `pattern_guess`, or any other "I couldn't get the real one so here's a placeholder" mechanism — **stop and re-read this section.** That is exactly the v1 failure. This remediation has no graceful degradation. Either the data is real or the row doesn't exist.

---

## 1. Objective

Produce `fund_holdings/ground_truth/fund_bible_v1.xlsx` — an authoritative, governed catalogue of every US-listed ETF that meets the NX1 Phase 0 inclusion criteria, with sufficient metadata to support downstream rule-inference research on (symbol, fund) rebalancing cells.

The fund bible is the analogue of `equities_news/ground_truth/bible_v1.xlsx` but keyed on funds, not symbols. Same discipline, smaller universe, richer per-entry metadata.

## 2. Non-goals (explicit — do NOT do these in this session)

- Do NOT author a source registry yet. That is Phase 1. Phase 0 produces the list of funds and their attributes; Phase 1 authors the holdings-feed source registry with full Lineage.
- Do NOT collect any actual holdings data. No CSV downloads, no scraping of positions. Only catalogue the *existence and location* of the holdings feed.
- Do NOT include non-US-listed ETFs. Explicit deferral to Phase 0-B.
- Do NOT include mutual funds, 13F filers, SMAs, CITs, closed-end funds, UITs. Explicit deferral to Phase 0-C/D.
- Do NOT include ETFs using proxy-basket disclosure (ANTs — active non-transparent). See §4.1 for the mechanical test.
- Do NOT build any ingestion code. Phase 4 problem.
- Do NOT attempt to infer rebalance rules or perform any research analysis. Phase 5 problem.
- Do NOT modify anything under `equities_news/`. Different governance tree.

## 3. Directory layout

The existing `fund_holdings/` tree is preserved. Remediation scripts and outputs are additive — new files live under `source_authoring/phase_0/` alongside the existing v1 artifacts. The bible itself is written in place; v1 cells are overwritten where remediation produces better data.

```
/home/darren/Desktop/MCAS/fund_holdings/
├── PHASE_0_REMEDIATION_TASKBOARD.md            (this file, copied on arrival)
├── PHASE_0_REMEDIATION_STATE.md                (CC writes progress here)
├── PHASE_0_TASKBOARD.md                        (v1, kept for historical reference; do not modify)
├── PHASE_0_SESSION_STATE.md                    (v1, kept for historical reference; do not modify)
├── ground_truth/
│   └── fund_bible_v1.xlsx                      (DELIVERABLE — same file, enriched in place)
├── registry/
│   ├── namespace_rules.yaml                    (v1; no changes)
│   └── sponsor_endpoints.yaml                  (NEW — discovered sponsor product-index endpoints)
├── source_authoring/
│   ├── phase_0/
│   │   ├── candidate_list.py                   (v1; do not modify — source of universe still trusted)
│   │   ├── metadata_enrich.py                  (v1; do not modify — N-CEN-derived fields still trusted)
│   │   ├── sponsor_discovery.py                (NEW — per-sponsor product-index endpoint discovery)
│   │   ├── batch_enrich.py                     (NEW — per-sponsor batch metadata fetch via discovered endpoints)
│   │   ├── content_verify.py                   (NEW — content-based holdings URL verification)
│   │   ├── remediation_merge.py                (NEW — load v1 bible, merge enrichment, write back atomically)
│   │   ├── logs/
│   │   │   ├── phase_0.jsonl                   (v1 author log; preserved untouched)
│   │   │   └── phase_0_remediation.jsonl       (NEW — one line per fund touched by remediation)
│   │   ├── excluded_funds.yaml                 (NEW — funds removed from bible during remediation, with reason)
│   │   ├── excluded_sponsors.yaml              (NEW — sponsors with no discoverable endpoint)
│   │   └── notes.md                            (extend existing v1 notes; do not delete prior content)
│   └── verification/
│       └── schema_check.py                     (UPDATE per §7 — content-based, not status-based)
└── tests/
    └── test_fund_bible_schema.py               (UPDATE per §7)
```

`fund_holdings/` is a sibling of `equities_news/` under the MCAS repo root. Commit from MCAS root per the existing convention.

**v1 artifacts are preserved as historical record, not deleted.** This is deliberate — the v1 taskboard, session state, and notes document the failure mode that motivated this remediation. Future readers (including future-Darren and future-CC sessions) need to be able to see why the architecture is what it is. Don't tidy them away.

## 4. Inclusion rules (mechanical — apply these exactly, no interpretation)

A candidate fund is IN the bible if and only if ALL of these are true:

### 4.1 Structural tests

1. **Listed on a US exchange** (NYSE Arca, Nasdaq, Cboe BZX). Primary listing only. ADR-referenced or non-US primary listings are OUT.
2. **Registered as an open-end ETF under the Investment Company Act of 1940.** This excludes ETNs (debt instruments), commodity pools (e.g. GLD is a grantor trust — SPECIAL CASE: include if it publishes daily full constituents, else exclude), and closed-end funds.
3. **Publishes full holdings daily, publicly, for free, on the sponsor's website.** Mechanical test (in order — stop at first positive):
   - **Primary test:** The sponsor's fund page (or the linked holdings file) contains text explicitly stating "daily" update cadence (e.g. "holdings updated daily", "Holdings as of [today's date]", "updated each business day"). A date-stamp within the last 2 business days is sufficient evidence of daily cadence.
   - **Secondary test:** The downloadable file (CSV/XLSX/JSON) lists every position with ticker or CUSIP, shares, weight. If the file is present with a recent (≤2 business days) date-stamp AND the sponsor is in the "known daily disclosers" set (iShares, Vanguard, SSGA, Invesco, Schwab, First Trust, WisdomTree, ProShares, Direxion, Global X, ARK, VanEck, Pacer, JPMorgan, etc.), treat as daily-compliant without further checks.
   - **PROXY-BASKET EXCLUSION:** If the sponsor uses "Tracking Basket", "Proxy Portfolio", "Indicative Basket", or "ActiveShares" terminology (ANTs — Fidelity active ETFs on Blue Tractor model, T. Rowe Price active ETFs on Precidian ActiveShares model, Natixis, Gabelli, etc.), the fund is OUT. Proxy baskets are not real holdings.
   - **Mechanical pre-filter via N-CEN:** Rule 6c-11's "custom basket" adoption is reported on Form N-CEN. Fidelity/Precidian-model ANTs file under Rule 6c-11 but flag themselves as non-transparent via specific disclosure items. CC should use the N-CEN ETF dataset as the pre-filter source for this flag where possible, rather than reading prospectuses one-by-one.
4. **AUM > $5M as of most recent month-end.** This is a structural floor to exclude shell funds that are registered but never operationally launched, NOT a filter on fund size for research purposes. Real fund sizes below $5M are effectively zero-flow and carry no rebalancing signal to reverse-engineer. The floor is deliberately permissive — it admits every genuinely operational ETF regardless of size. AUM is recorded as a first-class column (see §5.1 col 7) for downstream research partitioning. Record the AUM value and as-of-date in the bible. Do not re-check or re-filter within this session.

### 4.2 Tie-breakers and edge cases

- **Funds with AUM between $5M and $25M:** IN. Size alone is not an exclusion criterion. These are the small-but-operational ETFs most likely to have mechanically simple rebalance behavior and least likely to be already arbed by competing quants. Flag `operational_small_cap = true` if AUM < $25M so research can partition these out if desired.
- **Shell / never-launched funds (AUM < $5M):** OUT. Excluded because they carry no flow signal, not because they're small. Document rationale in the per-fund log entry.
- **Leveraged / inverse ETFs (2x, 3x, -1x):** IN if they publish daily full holdings of underlying positions (most do — ProShares, Direxion). Flag with `vehicle_subtype = "leveraged"` or `"inverse"`. Their rebalancing behaviour is mechanically different and may be a separate research cell later.
- **Currency-hedged ETFs:** IN. Flag `currency_hedged = true`. Hedging trades are a distinct flow category.
- **Smart-beta / factor ETFs:** IN. Flag `vehicle_subtype = "factor"` or `"smart_beta"`.
- **Fund-of-funds:** IN only if they publish the constituent *funds* daily and those constituent funds are themselves in-scope. If FoF holds underlying securities directly, IN normally. Flag `fund_of_funds = true`.
- **Sponsor-affiliated internal-crossing ETFs (e.g., same-sponsor sibling funds cross trades internally):** IN, but flag `sponsor_internal_crossing = true` if the prospectus discloses this. Material for the tradeability discussion later.

## 5. Column schema

The fund bible is a single Excel file, one sheet per sponsor family (e.g., "iShares", "Vanguard", "State Street", etc.), plus an "Overview" sheet.

Header at row 4, data starts row 5, mirroring `bible_v1.xlsx` convention.

### 5.1 Columns (exhaustive — every row must have every column filled or explicit "null" string)

| # | Column | Type | Rationale (why NX1 needs this) |
|---|---|---|---|
| 1 | `#` | int | Sequence number within sheet |
| 2 | `Ticker` | str | Primary identifier |
| 3 | `Fund_Name` | str | Human-readable |
| 4 | `Sponsor` | str | Grouping axis; sponsors have common rebalance policies |
| 5 | `Exchange` | str | NYSE Arca / Nasdaq / Cboe BZX |
| 6 | `Inception_Date` | date | Bounds the historical data available |
| 7 | `AUM_USD` | float | Point-in-time snapshot, as of `AUM_AsOf_Date` |
| 8 | `AUM_AsOf_Date` | date | Snapshot date for AUM |
| 9 | `Shares_Outstanding` | int | Point-in-time; used for creation/redemption flow inference |
| 10 | `Expense_Ratio_Bps` | float | Cost structure; may correlate with rebalance frequency discipline |
| 11 | `Vehicle_Type` | enum | `equity_index`, `equity_active`, `equity_factor`, `fixed_income`, `commodity`, `multi_asset`, `currency`, `other` |
| 12 | `Vehicle_Subtype` | str | `leveraged_2x`, `inverse_-1x`, `smart_beta`, `sector`, `thematic`, `esg`, `dividend`, `null` etc. |
| 13 | `Benchmark_Name` | str | E.g. "S&P 500", "MSCI EAFE", "Russell 2000", or "null" if active |
| 14 | `Benchmark_Provider` | str | `S&P`, `MSCI`, `FTSE_Russell`, `Bloomberg`, `Solactive`, `custom`, `null` |
| 15 | `Replication_Method` | enum | `full_replication`, `sampled_replication`, `synthetic`, `active` |
| 16 | `Rebalance_Frequency_Prospectus` | enum | From prospectus: `daily`, `monthly`, `quarterly`, `semi_annual`, `annual`, `as_needed`, `continuous` |
| 17 | `Rebalance_Trigger_Rules` | str | Free-text summary of prospectus rebalance language. Keep under 200 chars. |
| 18 | `Creation_Unit_Size` | int | Shares per creation unit; determines flow granularity |
| 19 | `Holdings_Feed_URL` | str | Direct URL to the daily holdings file (the thing Phase 4 will ingest) |
| 20 | `Holdings_Feed_Format` | enum | `csv`, `xlsx`, `json`, `html_table`, `pdf`, `other` |
| 21 | `Holdings_Feed_Lag_Hours` | float | Hours between market close and feed availability (e.g. 14 if posted 6am ET next day) |
| 22 | `Sponsor_Page_URL` | str | Canonical fund page on sponsor site |
| 23 | `Prospectus_URL` | str | Current prospectus (most recent filing) |
| 24 | `Has_Derivatives_Overlay` | bool | Does the fund use futures/swaps for cash equitisation or exposure management? (From prospectus.) |
| 25 | `Internal_Crossing_Disclosed` | bool | See §4.2 |
| 26 | `Currency_Hedged` | bool | See §4.2 |
| 27 | `Fund_Of_Funds` | bool | See §4.2 |
| 28 | `Primary_Market_Cap_Tier` | enum | `mega`, `large`, `mid`, `small`, `micro`, `mixed`, `n_a` (for non-equity) |
| 29 | `Geographic_Focus` | str | `us`, `developed_ex_us`, `emerging`, `global`, `regional_asia`, etc. |
| 30 | `Notes` | str | Free-text gotchas, exceptions, ambiguities. `null` if none. |
| 31 | `Lineage_Authored_By` | str | `phase_0_fund_bible_cc` |
| 32 | `Lineage_Authored_At` | ISO8601 | UTC timestamp |

### 5.2 Column rationale grouping (why these and not others)

- **Identification (1-8):** Minimum viable to reference the fund.
- **Flow inputs (9, 18):** Needed to compute creation/redemption flow and interpret it at the right granularity.
- **Cost / discipline proxies (10):** Low-ER index funds are the most mechanical rebalancers; high-ER active funds are the least. Expected to be a strong feature in rule inference.
- **Classification (11-15):** The rebalancing rule is a function of vehicle type. Cannot reverse-engineer without knowing what you're engineering against.
- **Rebalance policy (16-17):** Prospectus-stated rules are the prior. Inferred rules are the posterior. Need the prior explicitly.
- **Feed logistics (19-22):** Phase 4 ingestion needs these. Collecting them now avoids recollection cost later.
- **Exception flags (24-27):** Each flag identifies a class of fund where the "trivial rebalance rule" assumption breaks.
- **Scoping attributes (28-29):** Research partition keys.
- **Operational (23, 30):** Auditability and human-readable exception log.
- **Lineage (31-32):** Governance primitive. Non-negotiable.

### 5.3 Deliberately excluded columns (for clarity)

- **Benchmark constituents list:** Too large for bible, belongs in a separate downstream artifact.
- **Historical tracking error:** Derived, not source-of-truth.
- **AP list (authorised participants):** Not publicly disclosed reliably per-fund.
- **Bid-ask spread / liquidity metrics:** Market data, not fund metadata. Lives in Phase 3.

## 6. Source strategy (where CC should look, in what order)

## 6. Source strategy (REMEDIATION MODE — read §0 first)

### 6.1 Starting state — what's already in the bible

The starting state is `fund_holdings/ground_truth/fund_bible_v1.xlsx` as currently committed. CC's first action is to load this bible and partition every (row, column) cell into one of three states:

- **TRUST:** Cell was correctly populated by v1 from N-CEN structured data. **Do not re-fetch and do not re-verify.** Columns: `Sponsor`, `Exchange`, `Vehicle_Type`, `Vehicle_Subtype`, `Replication_Method`, `Rebalance_Frequency_Prospectus`, `Creation_Unit_Size`, `AUM_USD`, `AUM_AsOf_Date`, `Has_Derivatives_Overlay`, `Currency_Hedged`, `Fund_Of_Funds`, `Benchmark_Provider`, `Primary_Market_Cap_Tier`, `Geographic_Focus`, `#`, `Ticker`, `Fund_Name`. (Spot-checked in conversation: HEFA/HEZU correctly currency-hedged, Direxion correctly daily-rebalanced, leveraged ETFs correctly flagged. The N-CEN extraction is reliable.) If you encounter what looks like an incorrect TRUST cell during enrichment, log it in `notes.md` and move on — do not re-verify, do not re-fetch from N-CEN. Re-verification of TRUST cells is out of scope and will burn session budget without improving the deliverable.
- **REPLACE:** Cell was populated by v1 with `edgar_fallback` URLs or other placeholder values. Must be replaced via §6.2 sponsor discovery. Columns: `Holdings_Feed_URL`, `Holdings_Feed_Format`, `Sponsor_Page_URL`, `Prospectus_URL` for any row whose `Notes` column contains the substring `edgar_fallback` OR whose `Holdings_Feed_URL` contains `sec.gov/cgi-bin/browse-edgar`.
- **FILL:** Cell is currently `null` (literal string) and must be populated via §6.2 sponsor discovery or §6.3 sponsor prospectus reading. Columns: `Inception_Date`, `Shares_Outstanding`, `Expense_Ratio_Bps`, `Benchmark_Name`, `Holdings_Feed_Lag_Hours`, `Rebalance_Trigger_Rules`, `Internal_Crossing_Disclosed` (currently 100% False — verify via prospectus reading).

CC writes a partition report to `PHASE_0_REMEDIATION_STATE.md` as the first deliverable: per-column counts of TRUST / REPLACE / FILL across the 2,563 rows, plus a per-sponsor breakdown of how many funds fall into each bucket. This is the work-queue for the rest of the session.

### 6.2 Sponsor-first discovery (the critical path)

For each sponsor with at least one row in REPLACE or FILL state, CC executes the following sequence:

**Step 1 — Discover the sponsor's product-index endpoint.**

Most US ETF sponsors expose a public product-index JSON endpoint that returns a list of all their funds with canonical metadata. These endpoints are not always advertised but are discoverable via:
- Open the sponsor's "ETF list" or "Funds" page in a browser-equivalent fetch; observe what XHR/fetch request populates the fund grid; that request URL is the product-index endpoint.
- For sponsors using server-side rendering, inspect the page source for an inline JSON blob or a `<script type="application/json">` tag.
- Some sponsors expose endpoints under conventional paths (`/api/products`, `/etfs/products.json`, `/data/funds.json`) but **do not assume**; verify the endpoint exists by calling it and inspecting the response before using it.

The discovery is a one-time cost per sponsor (~70 sponsors at most, not ~3,000 funds). Document each discovered endpoint in `fund_holdings/registry/sponsor_endpoints.yaml`:

```yaml
- sponsor_slug: ishares
  product_index_url: <discovered URL>
  discovery_method: <how CC found it — "browser network tab", "inline JSON in fund-list page", etc.>
  fund_url_pattern: <how to construct the per-fund holdings URL from the index data>
  holdings_url_template: <as above for the holdings file specifically>
  discovered_at: <UTC ISO>
  verified_serves_holdings: true|false  # set by §7.2 content check
```

**Step 2 — Call the discovered endpoint and extract per-fund metadata.**

Single request per sponsor. Parse the response (typically JSON) into a per-ticker mapping. Most sponsor product-indexes carry: `Holdings_Feed_URL`, `Sponsor_Page_URL`, `Inception_Date`, `AUM_USD` (use this to *update* the v1 N-CEN value if newer), `Shares_Outstanding`, `Expense_Ratio_Bps`, `Benchmark_Name`. Map sponsor-specific field names to bible column names via a per-sponsor parser.

**Step 3 — If a sponsor has no discoverable product-index endpoint:**

Some small sponsors genuinely have no API. Two permitted paths and only two:

- **Manual reconnaissance:** CC fetches one fund's page, identifies the actual sponsor URL pattern (e.g. `https://example-sponsor.com/etfs/{TICKER}/holdings.csv`), verifies it returns real holdings content via §7.2, and uses that pattern for all that sponsor's funds. Recorded in `sponsor_endpoints.yaml` with `discovery_method: manual_pattern`.
- **Exclusion:** If neither a product-index endpoint nor a verifiable URL pattern can be established for a sponsor after a bounded effort (~10 minutes per sponsor), every fund from that sponsor is **removed from the bible** (deleted as rows from the relevant sheet) and logged in `fund_holdings/source_authoring/phase_0/excluded_sponsors.yaml` with the reason. No fallback URLs. No placeholders.

**Step 4 — There is no Step 4.** No EDGAR fallback. No SEC search URL substitution. If §6.2 doesn't yield a verifiable holdings URL for a sponsor's funds, those funds leave the bible.

### 6.3 Fields not covered by the sponsor product-index

For fields the sponsor's product-index doesn't carry — typically `Rebalance_Trigger_Rules`, `Internal_Crossing_Disclosed`, sometimes `Holdings_Feed_Lag_Hours`:

- **Sponsor-level prospectus reading:** Most sponsors use a single prospectus template across their fund family. One careful read of the sponsor's master prospectus extracts these fields for the entire family. ~70 prospectus reads, not ~3,000.
- **N-CEN structured data:** Already extracted in v1 — these cells are TRUST. Do not re-fetch.
- **If a field cannot be resolved** from sponsor product-index, sponsor prospectus, or N-CEN: the cell stays as the literal string `"null"` and the Notes column is appended to document the gap (do not overwrite existing v1 Notes content; append). This is acceptable for non-load-bearing fields. **It is not acceptable for `Holdings_Feed_URL`** — that field has no null state in this remediation; either it's a verified working URL or the row is removed from the bible.

### 6.4 Lineage handling for partially-touched rows

The bible has a `Lineage_Authored_By` and `Lineage_Authored_At` per row. v1 rows are stamped `phase_0_fund_bible_cc` / `2026-04-22T...`. The remediation must preserve the audit trail without erasing it.

Approach:
- Add **two new columns** to the bible after the existing Lineage columns: `Lineage_Remediated_By` (string, `phase_0_remediation_cc` for any row touched by remediation; `null` for rows untouched), `Lineage_Remediated_At` (ISO8601 UTC for any row touched; `null` otherwise).
- Original `Lineage_Authored_By` and `Lineage_Authored_At` are **never overwritten**, even on rows where every REPLACE/FILL field was changed. This preserves the v1 author trail.
- The `phase_0_remediation.jsonl` log records per-row, per-cell what changed: `{ticker, sponsor, columns_replaced: [...], columns_filled: [...], removed: bool, removal_reason: <str|null>, remediated_at: <iso>}`.
- For **rows removed from the bible** during remediation (sponsor exclusion, content-verification failure), the row is logged in `excluded_funds.yaml` with full v1 row content preserved verbatim plus removal reason and removal timestamp. The row is then deleted from the bible (not retained as a "tombstone"). Removal is auditable via the excluded_funds.yaml.


## 7. Verification gates (REMEDIATION MODE — read §0 first)

v1's verification was a HEAD request expecting HTTP 200, satisfied by any reachable page including SEC EDGAR search URLs. The remediation gate is **content-based** and applies uniformly to the entire bible after enrichment, not just to remediated rows. (Reason: a TRUST cell from v1 is only TRUST under the assumption that v1 populated it correctly; the gate must confirm that assumption holds for the final delivered file.)

Before declaring the session complete, CC runs:

### 7.1 Schema check (`source_authoring/verification/schema_check.py`)

- Every row has every column.
- Every enum value is in the permitted enum set.
- Every numeric column is a valid number (or `"null"` where §6.3 permits).
- Every date column is ISO8601 or Excel-date-parseable.
- **No row contains the literal strings** `edgar_fallback`, `template_guess`, `pattern_guess`, `pending Phase 1`, or any other graceful-degradation marker. The presence of any such string in any cell — including `Notes` — is a hard schema_check failure. (Notes that reference these terms historically should have been removed or rewritten during remediation; their presence in the final bible means the remediation didn't complete its work.)

### 7.2 Holdings_Feed_URL content verification (the critical gate)

For **every** row in the bible (TRUST rows included — see preamble above), CC fetches `Holdings_Feed_URL` via GET (not HEAD) with a browser-like User-Agent and verifies the response **content** matches the declared `Holdings_Feed_Format`:

- **Format = csv:** Response is parseable as CSV, contains ≥10 rows of data, and at least one column header matches a member of `{ticker, symbol, isin, cusip, security_id, identifier}` (case-insensitive partial match acceptable). Total response body ≥1KB.
- **Format = xlsx:** Response is parseable as a workbook, has ≥1 sheet with ≥10 data rows, and at least one column header matches the identifier set above. Total response body ≥5KB.
- **Format = json:** Response is parseable as JSON, contains an array (or a key whose value is an array) with ≥10 elements, and the array elements contain at least one identifier-like key.
- **Format = html_table:** Response contains at least one HTML `<table>` element with ≥10 `<tr>` rows and at least one identifier-like header cell. Pure JavaScript-rendered tables that don't appear in the raw HTML are NOT acceptable for this format — those URLs must be either (a) replaced with the underlying API endpoint the JS fetches from, or (b) the row is removed from the bible.
- **Format = pdf or other:** NOT permitted. PDFs and other unstructured formats are not real holdings feeds for the purposes of this bible.
- **Format = edgar_fallback:** NOT permitted. This format value must not exist in the final bible.

If the content check fails for any row, that row is **removed from the bible** (deleted from the relevant sponsor sheet) and logged in `fund_holdings/source_authoring/phase_0/excluded_funds.yaml` with: full v1 row content (so it can be reinstated later if a real URL is found), failure reason, and removal timestamp. Do NOT reduce the rigor of the content check to make rows pass. Do NOT substitute a different URL "to make it pass" — if §6.2's discovery didn't yield a verifiable URL for a fund's sponsor, the fund leaves the bible.

Practical execution: content-verify in batches as enrichment proceeds (don't wait for end-of-session). This catches sponsor-endpoint discoveries that are technically reachable but don't actually serve holdings data, and lets CC re-attempt §6.2 Step 1 for that sponsor before too much work has accumulated on a bad endpoint.

### 7.3 Holdings_Feed_URL distinctness

- Every Holdings_Feed_URL must be distinct **at the fund level**. Two funds from the same sponsor pointing at the same URL is a hard failure — it means the URL points to the sponsor's fund-list page, not per-fund holdings files. This was a v1 failure mode the URL-distinctness check would have caught.
- Sister funds may share a domain (`ishares.com/...IWF` and `ishares.com/...IVV`) but the full URL must differ.

### 7.4 Coverage check

- Overview sheet `Total` row matches the sum of per-sheet row counts (and reflects post-remediation row counts, accounting for removals).
- At least 95% of rows have non-null values for: `Sponsor`, `Exchange`, `Vehicle_Type`, `Replication_Method`, `Rebalance_Frequency_Prospectus`, `Holdings_Feed_URL`, `Holdings_Feed_Format`.
- Every row has both v1 Lineage columns AND remediation Lineage columns populated (per §6.4 — remediation columns are `null` for rows untouched by remediation, populated for rows that were touched).
- **No more than 30% of rows have `"null"` in any single column other than** `Inception_Date`, `Rebalance_Trigger_Rules`, `Holdings_Feed_Lag_Hours`, and `Vehicle_Subtype` (the four fields where structural sparsity is expected).

### 7.5 Sanity check

- Sum of AUM_USD on the post-remediation bible is within 25% of the public ICI total US ETF AUM figure (~$11T). The remediation's stricter exclusion may legitimately produce a smaller AUM total than v1 — a post-remediation total of $9-10T is acceptable; below $7T suggests over-exclusion of major sponsors and should be investigated before commit.
- No duplicate tickers.
- Row count: `v1_count - removed_count == final_count`. The arithmetic must reconcile against `excluded_funds.yaml`.

### 7.6 Self-audit checklist (CC must complete before commit)

- [ ] Every ticker in the final bible passes all §4 structural tests (re-confirm by sampling 20 random entries end-to-end).
- [ ] No non-US-listed, non-1940-Act, or proxy-basket funds slipped through.
- [ ] `phase_0_remediation.jsonl` records every row touched by remediation, with column-level change detail.
- [ ] Schema check (§7.1) passes clean.
- [ ] **Holdings_Feed_URL content check (§7.2) passes for 100% of rows in the bible.** Any failed row was removed (not retained with a placeholder URL).
- [ ] URL distinctness check (§7.3) passes — no two funds share a Holdings_Feed_URL.
- [ ] Coverage check (§7.4) passes clean.
- [ ] Sanity check (§7.5) passes clean. Row arithmetic reconciles.
- [ ] `sponsor_endpoints.yaml` documents every sponsor's discovered endpoint with `verified_serves_holdings: true`.
- [ ] `excluded_funds.yaml` and `excluded_sponsors.yaml` document every removal with reason.
- [ ] v1 Lineage columns are intact on every row that survived remediation. Remediation Lineage columns are populated on every row that was touched.
- [ ] **`grep -rE 'edgar_fallback|template_guess|pattern_guess|pending Phase' fund_holdings/ground_truth/`** returns zero results. (The string may legitimately appear in `notes.md`, the v1 historical taskboard, or this remediation taskboard as historical reference; it must not appear in any data row of the bible itself.)
- [ ] `PHASE_0_REMEDIATION_STATE.md` documents the partition report (TRUST/REPLACE/FILL counts), final state counts, exclusion counts per sponsor, and any deferred items.

## 8. Session discipline

- **One session, one commit.** This is a one-shot. Per-sponsor batch enrichment via discovered product-index endpoints is dramatically cheaper than per-fund scraping (~70 sponsor calls vs ~3,000 fund calls). If CC genuinely cannot complete the session, write full state to `PHASE_0_REMEDIATION_STATE.md` and commit partial work — but report it honestly as a failure mode, not as a planned checkpoint.
- **Append-only author log.** `phase_0_remediation.jsonl` is written one line at a time, flushed immediately, never rewritten. Crash-safe.
- **In-place enrichment with atomic write.** CC loads `fund_bible_v1.xlsx`, mutates the in-memory representation as enrichment proceeds, writes to `fund_bible_v1.xlsx.tmp` only after schema_check + content verification passes on the mutated state, then atomic-renames `.tmp` → `fund_bible_v1.xlsx`. Never edit the bible file in place mid-session — that risks leaving it corrupt on crash.
- **v1 Lineage columns are immutable.** Existing `Lineage_Authored_By` and `Lineage_Authored_At` values are never overwritten on rows that survive remediation. Remediation work is recorded in the new `Lineage_Remediated_By` / `Lineage_Remediated_At` columns (per §6.4).
- **Row removal is a real operation.** When a row fails §7.2 content verification or its sponsor is excluded, it is **deleted from the bible** (not marked or hidden) and its full v1 content is preserved verbatim in `excluded_funds.yaml`. The bible's row count after remediation will be smaller than 2,563.
- **Commit scope:** `fund_holdings/` only. Never `-A`. Branch `main`, direct commit per MCAS convention.
- **Every function in the scripts CC writes does real work or raises `NotImplementedError`.** No stub functions, no silent pass-through.
- **No hardcoded sponsor lists in code.** All enumerable sets live in `fund_holdings/registry/*.yaml`. Discovered sponsor endpoints live in `sponsor_endpoints.yaml`.
- **No fallback URLs of any kind.** If §6.2 doesn't yield a verifiable holdings URL for a sponsor, every fund from that sponsor is removed and logged.
- **Content-verify in batches as enrichment proceeds.** Don't accumulate ~2,500 unverified rows then verify at the end — content-verify per-sponsor immediately after enrichment, so a bad sponsor endpoint is caught early enough to re-attempt §6.2 Step 1.
- **HTTP requests use a browser-like User-Agent header.** Many sponsor CDNs bot-block default Python UA strings (`python-urllib/3.x` etc.) but serve real browsers. Use a recent Chrome or Firefox UA string. This applies to both discovery (§6.2) and content verification (§7.2). The default UA is the single most common cause of false-negative URL failures.

## 9. Deliverable definition — "done" means:

1. `fund_holdings/ground_truth/fund_bible_v1.xlsx` exists, schema-conformant. Row count is `2563 - removed_count` (expected 1,800–2,500 depending on how many sponsors get excluded for un-discoverable endpoints). Same file as v1, enriched in place.
2. Every row has a real, content-verified `Holdings_Feed_URL`. Zero `edgar_fallback` strings anywhere in the bible.
3. `fund_holdings/source_authoring/phase_0/logs/phase_0_remediation.jsonl` records every row touched by remediation with column-level change detail.
4. `fund_holdings/source_authoring/phase_0/logs/phase_0.jsonl` (the v1 author log) is preserved untouched.
5. `fund_holdings/registry/sponsor_endpoints.yaml` documents every sponsor's discovered product-index endpoint.
6. `fund_holdings/source_authoring/phase_0/excluded_funds.yaml` documents every row removed during remediation, with full v1 content preserved verbatim.
7. `fund_holdings/source_authoring/phase_0/excluded_sponsors.yaml` documents every sponsor with no discoverable endpoint and a non-zero candidate count.
8. `fund_holdings/source_authoring/verification/schema_check.py` exits 0 on the post-remediation bible.
9. `fund_holdings/source_authoring/phase_0/content_verify.py` exits 0 on the post-remediation bible (100% of `Holdings_Feed_URL` content-verified).
10. `fund_holdings/tests/test_fund_bible_schema.py` passes under pytest.
11. `PHASE_0_REMEDIATION_STATE.md` documents the partition report (TRUST/REPLACE/FILL counts at start), final state counts, exclusion counts per sponsor, and any deferred items.
12. `notes.md` is extended (not overwritten) with non-obvious decisions made during remediation, especially each sponsor's discovery method.
13. `grep -rE 'edgar_fallback|template_guess|pattern_guess|pending Phase' fund_holdings/ground_truth/` returns zero results.
14. Single commit, pushed to `origin/main`, message: `phase_0_remediation: M sponsors discovered, K funds removed, N funds remain with verified holdings URLs`

## 10. Explicit non-scope reminders (end-of-session discipline)

Do NOT, at any point:

- Attempt to infer or propose rebalancing rules from the metadata.
- Download any holdings CSV.
- Build any ingestion adapter.
- Extend scope to non-US ETFs, mutual funds, or other vehicle types.
- Touch `equities_news/` or any other MCAS subsystem.
- Author the source registry (Phase 1 work).
- Make any claims about edge, expectancy, or tradeability.
- **Substitute a SEC EDGAR URL, search URL, or any other "second-best" URL** when the real Holdings_Feed_URL cannot be obtained. The only valid responses to "I cannot get a real URL for this fund" are (a) re-attempt §6.2 discovery for the sponsor, or (b) remove the fund from the bible and log it. Inventing a fallback URL state is the v1 failure that this remediation exists to eliminate.
- **Reduce the rigor of §7.2 content verification** to make rows pass. If the content check fails, the row is removed.
- **Guess sponsor URL patterns by templating on ticker.** §6.2 discovery is per-sponsor, not per-fund. If you find yourself writing `f"https://{sponsor}.com/etfs/{ticker}/holdings.csv"` or similar, stop — that is exactly the v1 failure mode.
- **Re-fetch TRUST cells from §6.1.** The N-CEN-derived fields are correct; re-fetching them wastes session budget and risks introducing inconsistency.
- **Overwrite v1 Lineage columns.** v1 author trail is preserved unconditionally; remediation work is recorded in the new remediation Lineage columns per §6.4.
- **Tidy away v1 historical artifacts.** The v1 taskboard, session state, and v1 author log stay in the tree as historical record.

The Phase 0 deliverable remains a *catalogue* — nothing more. Its value is that everything downstream (Phase 1 source registry, Phase 2 backfill, Phase 3 market data, Phase 4 ingestion, Phase 5 research) can proceed cleanly from a governed starting substrate. This remediation trades coverage for trustworthiness — a smaller bible with verified data is the desired outcome, not a larger bible with placeholder URLs.

## 11. Session start checklist (first actions on session open)

**Repo invocation:** Working directory is `/home/darren/Desktop/MCAS/`. The fund_holdings tree is at `fund_holdings/` relative to that root. All commits land on branch `main` of `origin` (github.com:dbk000/mcas), no PR workflow. Commit scope is the `fund_holdings/` path only — never `git add -A`.

1. Read this taskboard end-to-end. Pay particular attention to §0 (v1 failure post-mortem) and §6 (partition / sponsor-discovery / lineage handling).
2. Load `fund_holdings/ground_truth/fund_bible_v1.xlsx`. Before building the partition logic, **sample 10 random rows from each sheet** and inspect them to confirm the actual data shape matches §6.1's assumptions: `edgar_fallback` appears literally in the `Notes` column and `Holdings_Feed_Format` column for the broken rows; `Holdings_Feed_URL` for those rows starts with `https://www.sec.gov/cgi-bin/browse-edgar`; the 100%-null fields are literal `null` strings (not Python None or empty cells). Adjust the partition logic to match what's actually there if anything diverges. Then run the partition analysis: per column, classify every cell as TRUST / REPLACE / FILL per §6.1. Write the partition report to `PHASE_0_REMEDIATION_STATE.md` as the first commit-worthy artifact.
3. Add the two new Lineage columns (`Lineage_Remediated_By`, `Lineage_Remediated_At`) to every sponsor-family sheet. Initialise to `null` for all existing rows.
4. Write `sponsor_discovery.py` to discover each sponsor's product-index endpoint (per §6.2 Step 1). Populate `registry/sponsor_endpoints.yaml` as discoveries land. Process sponsors in descending order of REPLACE+FILL row count (largest impact first — iShares 349, then SSGA 152, then proshares 89, then first_trust 86, then global_x 83, then vaneck 53, etc.).
5. Write `batch_enrich.py` to call each discovered endpoint and populate REPLACE+FILL fields per fund. As each sponsor completes:
   a. Stamp `Lineage_Remediated_By` / `Lineage_Remediated_At` on touched rows.
   b. Append entries to `phase_0_remediation.jsonl` recording column-level changes.
   c. Save to `fund_bible_v1.xlsx.tmp` (in-memory mutation, not partial-file write).
6. Write `content_verify.py` per §7.2. Run it per-sponsor immediately after enrichment for that sponsor. Move failed rows to `excluded_funds.yaml` and delete them from the in-memory bible.
7. For sponsors where discovery in step 4 fails entirely: remove all that sponsor's rows from the in-memory bible, write to `excluded_sponsors.yaml`, log to `phase_0_remediation.jsonl`.
8. After all sponsors processed: run the full schema_check (§7.1), URL distinctness check (§7.3), coverage check (§7.4), sanity check (§7.5).
9. If all checks pass: atomic-rename `fund_bible_v1.xlsx.tmp` → `fund_bible_v1.xlsx`.
10. Update `PHASE_0_REMEDIATION_STATE.md` with final state.
11. Run `grep -rE 'edgar_fallback|template_guess|pattern_guess|pending Phase' fund_holdings/ground_truth/` — must return zero results.
12. Commit + push from MCAS root.

---

**End of taskboard.** If anything in this document is ambiguous when you sit down to execute, STOP and flag — do not guess. Ambiguity in a foundational catalogue compounds downstream. Better to ask Darren than to produce a polluted bible.
