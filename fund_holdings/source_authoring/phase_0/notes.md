# Phase 0 — Fund Bible Authoring Notes

Author: phase_0_fund_bible_cc (Claude Code, Opus 4.7, 1M context)
Session date: 2026-04-22 (UTC)

This document captures the non-obvious decisions and gotchas from the
Phase 0 authoring session. Structure-first commentary for downstream
phases and human reviewers.

---

## 1. Source-of-truth choice

The candidate list is enumerated from the SEC's **Form N-CEN structured
data sets** (`https://www.sec.gov/files/dera/data/form-n-cen-data-sets/`).
We combine the trailing FOUR quarterly bundles (2025q2 + 2025q3 + 2025q4
+ 2026q1) and dedupe by `SERIES_ID` keeping the most recent filing. This
gives the rolling twelve-month view of US registered open-end ETFs. N-CEN
is filed annually by every registered investment company, so four quarters
covers essentially every operational fund.

Alternatives considered and rejected:
- **etf.com / etfdb.com screeners** — third-party, lag the SEC and
  occasionally mis-categorise vehicle type. Used as sanity checks only.
- **NYSE / Nasdaq / Cboe ETF listing pages** — fragmented across three
  exchanges, no aggregated feed. Useful for spot-checks but not primary.
- **N-PORT** — higher resolution on portfolio composition but too large
  for Phase 0 scope. Phase 2+ will consume N-PORT.

## 2. ETF identification in N-CEN

The authoritative flag is `FUND_REPORTED_INFO.IS_ETF = 'Y'`. Secondary
flags that refine classification:

- `IS_ETMF` — Exchange-Traded Managed Fund (Eaton Vance NextShares, now
  defunct). Excluded from the bible; none expected in scope.
- `IS_MASTER_FEEDER` — feeder funds don't publish independent holdings
  (they hold a single interest in the master fund). Excluded.
- `IS_MULTI_INVERSE_INDEX` — leveraged / inverse ETFs. KEPT, but tagged
  with `vehicle_subtype = leveraged_Nx` or `inverse`. Their rebalancing
  mechanics differ from unlevered index funds and are a separate research
  cell for NX1.
- `IS_MONEY_MARKET` — money market fund. Excluded (no equity-like
  rebalance cadence).
- `IS_RELYON_RULE_6C_11` — reliance on the 2019 ETF Rule (transparent
  basket). Not a strict filter — older ETFs pre-dating 6c-11 still
  operate under legacy exemptive orders and file N=Y here. Used as a
  hint for proxy-basket detection.

## 3. Proxy-basket (ANT) exclusion

Active non-transparent ETFs (ANTs) — Fidelity's Blue-Tractor-model funds,
Precidian ActiveShares licensees (T. Rowe Price active line, Natixis,
Gabelli, etc.) — disclose a "Tracking Basket" or "Proxy Portfolio"
rather than real holdings. The bible excludes these at the **sponsor
level** when the sponsor's entire ETF line is ANT (see
`registry/known_daily_disclosers.yaml:ant_sponsors_excluded`). For
ambiguous sponsors (Fidelity, American Century, Nationwide — who have
both transparent and ANT lines), per-fund proxy-basket disclosure must
be verified in Phase 1 against the prospectus. Phase 0 admits these and
relies on the fact that `holdings_feed_url` verification (HEAD check
against the sponsor's "composition" URL) is downstream of any Tracking
Basket language.

**Known gap:** a handful of Fidelity active ETFs and T. Rowe Price
active ETFs will pass Phase 0 structural filters and appear in the
bible. Their `Holdings_Feed_URL` resolves to the sponsor composition
page which does expose a Tracking Basket. Phase 1 must re-check and
drop these; they are flagged by sponsor_slug + active-term fund_name
conventions (e.g. "Blue Chip", "Capital Appreciation", "Growth") but
not yet systematically excluded.

## 4. AUM proxy

N-CEN carries `DAILY_AVG_NET_ASSETS` and `MONTHLY_AVG_NET_ASSETS` on
`FUND_REPORTED_INFO`. These are averages over the fiscal year covered by
the filing (typically 12 months ending ≤75 days before the filing date).
This is NOT a point-in-time AUM — it's a backward-looking average and
can differ from the current AUM by 3-12 months. For the Phase 0 $5M
floor (§4 structural tests), this average is sufficient: operational
ETFs with $5M+ in trailing AUM stay above the floor; genuine shells
that crossed $5M briefly then collapsed are rare and acceptable noise.
Phase 1 will re-populate AUM from sponsor pages for current point-in-
time figures.

`AUM_USD` column in the bible = whichever of daily / monthly is non-null
(daily preferred). `AUM_AsOf_Date` = `REPORT_ENDING_PERIOD` from the
N-CEN submission.

## 5. Sponsor normalisation

The SEC N-CEN has no canonical sponsor slug field; `FAMILY_INVESTMENT_
COMPANY_NAME` is populated only for family-of-funds registrants and
`REGISTRANT_NAME` is the trust name (e.g. "iShares Trust", "iShares,
Inc.", "iShares U.S. ETF Trust" — all the same sponsor). Normalisation
is done via a regex-pattern registry in `candidate_list.py`
(`SPONSOR_PATTERNS`). Coverage on the 2565 included candidates:

- 100% of candidates end up with a non-empty `sponsor_slug`.
- ~185 candidates have no pattern match on either registrant or family
  name; for these, a `unaffiliated_{first-two-words-of-fund-name}`
  slug is generated from the fund name. These are mostly small issuers
  hosted in multi-sponsor series trusts (EA Series Trust, ETF
  Opportunities Trust, Northern Lights Trusts, Unified Series Trust,
  Investment Managers Series Trust, etc.).

The `unaffiliated_*` slugs are a Phase 0 compromise — Phase 1 will
introduce a per-subadviser registry and replace these with proper slugs.

## 6. Holdings_Feed_URL resolution

`Holdings_Feed_URL` (column 19) is the load-bearing column per the
taskboard. Strategy:

1. **Sponsor-specific templates** in `registry/sponsor_url_templates.yaml`
   for the ~65 highest-volume sponsors. For each sponsor, up to N
   candidate URL patterns are tried (first 200/302 wins).
2. **EDGAR fallback** (`sponsor_url_templates.yaml:unaffiliated_generic`)
   for sponsors without a template, or when the sponsor-specific URL
   fails. The fallback URL is the fund's SEC EDGAR company page filtered
   to N-1A filings — a verified-accessible anchor that links to the most
   recent prospectus and 497 supplements. When this is used, the row's
   `Holdings_Feed_Format` is set to `edgar_fallback` so downstream
   consumers know Phase 1 still needs to author the real sponsor adapter.
3. **HEAD check** on every constructed URL. Polite `User-Agent` first;
   fall back to browser `User-Agent` on 400/403/406 (many sponsor CDNs
   bot-block the default Python header); fall back to GET on HEAD
   rejection (405). Final verdict = first method to return a 200/302.

**Known quality issue #1:** some sponsor templates produce URLs that
redirect to unrelated domains under certain regional IPs (e.g.
`delawarefunds.com` → Nomura Asset Management homepage for funds acquired
by Macquarie years ago). The HEAD check reports 200 (the final Nomura
page exists) but the URL is semantically wrong. `fixup_urls.py` rewrites
the stored URL to the canonical pre-redirect template form, so the
catalogue at least documents the intent. Phase 1 must re-author these
templates against current sponsor URLs.

**Known quality issue #2:** several sponsors have templates that 404 for
every per-ticker URL — the per-fund URL requires a sponsor-side ID
lookup (e.g. iShares encodes product as `/us/products/{product_id}/`
where `product_id` comes from their JSON search API, NOT from the
ticker). For these sponsors ALL funds fall back to `edgar_fallback`
format. Affected sponsors in Phase 0 (0% sponsor-native feed):
  - ishares (349 funds) — needs iShares JSON API to resolve product_id
  - tidal (128 funds) — multi-subadviser trust, needs per-subadviser
    lookup
  - alpha_architect (42 funds) — EA Series Trust, subadviser-hosted
    fund sites
  - xtrackers (34 funds) — DWS template URL pattern stale
Phase 1 will reauthor these four.

## 7. Inferrable fields (vehicle_type / geographic_focus etc.)

Several bible columns are derived from `fund_name` keywords + N-CEN flags
via heuristics in `metadata_enrich.py:infer_*()`. These are best-effort,
not authoritative. Error rate expected in 5-10% range on edge cases:

- `Vehicle_Type` — keyword matching on fund name (bond/treasury/etc. →
  fixed_income; gold/silver/oil → commodity; currency → currency; factor
  names → equity_factor). Falls back to `equity_index` for IS_INDEX=Y,
  `equity_active` for IS_INDEX=N.
- `Vehicle_Subtype` — captures leverage / inverse / smart_beta / thematic
  / sector / ESG / dividend / buffered_outcome / options_strategy.
- `Replication_Method` — `active` if IS_INDEX=N; `synthetic` if
  IS_MULTI_INVERSE_INDEX=Y; `full_replication` as default for passive
  (N-CEN does not distinguish full vs sampled).
- `Benchmark_Provider` — regex on S&P, MSCI, FTSE/Russell, Bloomberg,
  Solactive, Nasdaq, CRSP, ICE.
- `Primary_Market_Cap_Tier` — regex on "large-cap", "mid-cap", etc.
  `n_a` for non-equity vehicles.
- `Geographic_Focus` — regex on "emerging", "developed ex-US", region
  names; default `us`.
- `Currency_Hedged` — regex on "currency-hedged", "hedged equity" etc.
- `Has_Derivatives_Overlay` — regex on "covered call", "option", "buffer",
  "defined outcome", "put write", "overlay". Not from the prospectus.

## 8. Explicit gaps (deferred to Phase 1+)

The following bible columns are populated as literal `"null"` in Phase 0
because they require prospectus or sponsor-page parsing not yet built:

- `Inception_Date` (col 6) — not in N-CEN; in sponsor fact sheets.
- `Shares_Outstanding` (col 9) — requires N-PORT or sponsor page.
- `Expense_Ratio_Bps` (col 10) — requires sponsor prospectus.
- `Benchmark_Name` (col 13) — sponsor fact sheet or prospectus.
- `Rebalance_Frequency_Prospectus` (col 16) — prospectus parse.
- `Rebalance_Trigger_Rules` (col 17) — prospectus parse.
- `Holdings_Feed_Lag_Hours` (col 21) — requires empirical observation.
- `Internal_Crossing_Disclosed` (col 25) — prospectus parse.

## 9. Verification gates

- **`schema_check.py`** runs schema + coverage + sanity per taskboard §7.
  URL verification is optional in the committed run (the authoring-time
  HEAD check is the load-bearing verification); `--skip-url-check` makes
  the pytest wrapper hermetic.
- **`test_fund_bible_schema.py`** wraps schema_check + row-count gate +
  ticker uniqueness + lineage populated + Overview total matches.

## 10. Governance artefacts

Every bible row carries:
- `Lineage_Authored_By = "phase_0_fund_bible_cc"`
- `Lineage_Authored_At = <UTC ISO8601>`

Any Phase 1+ re-write of a row must stamp new lineage with the Phase 1
authoring ID. Never mutate Phase 0 lineage in place.

## 11. Rerun instructions

```
cd /home/darren/Desktop/MCAS/fund_holdings/source_authoring/phase_0
python3 candidate_list.py \
    --output candidate_list.jsonl \
    --raw-dir raw_ncen \
    --excluded-log candidate_excluded.jsonl \
    --quarters 2025q2 2025q3 2025q4 2026q1

python3 metadata_enrich.py \
    --input candidate_list.jsonl \
    --output logs/phase_0.jsonl \
    --excluded logs/phase_0_excluded.jsonl \
    --templates ../../registry/sponsor_url_templates.yaml \
    --workers 32

python3 fixup_urls.py \
    --input logs/phase_0.jsonl \
    --output logs/phase_0_fixed.jsonl \
    --templates ../../registry/sponsor_url_templates.yaml
mv logs/phase_0_fixed.jsonl logs/phase_0.jsonl

python3 assemble_bible.py \
    --input logs/phase_0.jsonl \
    --output-tmp ../../ground_truth/fund_bible_v1.xlsx.tmp \
    --min-sponsor-rows 5

python3 ../verification/schema_check.py \
    --workbook ../../ground_truth/fund_bible_v1.xlsx.tmp \
    --namespace-rules ../../registry/namespace_rules.yaml \
    --skip-url-check
mv ../../ground_truth/fund_bible_v1.xlsx.tmp ../../ground_truth/fund_bible_v1.xlsx

pytest -v ../../tests/test_fund_bible_schema.py
```

Total end-to-end runtime: ~20-25 min dominated by metadata_enrich.py
HTTP HEAD checks.
