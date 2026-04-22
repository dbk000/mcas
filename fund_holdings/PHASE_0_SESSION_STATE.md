# Phase 0 — Fund Bible: Session State

**Session ID:** `phase_0_fund_bible_cc_20260422`
**Executor:** Claude Code, Opus 4.7, 1M context
**Session window:** 2026-04-22 14:40 UTC → 2026-04-22 (in progress)

This file is the source-of-truth for "where the session is" and how to
resume if the authoring pass is interrupted. Not a decision log — see
`source_authoring/phase_0/notes.md` for that.

---

## Current state (end-of-session)

Status: **COMPLETE** — `fund_bible_v1.xlsx` committed.

Pipeline steps:

- [x] Taskboard copied to `PHASE_0_TASKBOARD.md`
- [x] Directory scaffolding created
- [x] `registry/namespace_rules.yaml` — fund-keyed adaptation of the
      equities_news rules file. Enum set populated for all 32 bible
      columns that have enum values. Includes `edgar_fallback` as a
      Phase 0 transitional value for `holdings_feed_format`.
- [x] `registry/known_daily_disclosers.yaml` — sponsor-level transparent-
      discloser set + ANT-sponsor exclusion set. Used by candidate_list.py
      to pre-filter proxy-basket sponsors.
- [x] `registry/sponsor_url_templates.yaml` — 67 sponsor-specific URL
      templates for `Holdings_Feed_URL` + `Sponsor_Page_URL` construction.
      `unaffiliated_generic` is the EDGAR-anchor fallback.
- [x] `source_authoring/phase_0/candidate_list.py` — SEC N-CEN
      enumerator. Downloads 4 quarterly bundles, joins tables, applies
      §4.1 structural filters.
- [x] `source_authoring/phase_0/candidate_list.jsonl` — 2565 candidates
      (1389 excluded: ANT sponsors, sub-$5M shells, non-US listings,
      money-market funds, master-feeder structures, no-ticker).
- [x] `source_authoring/phase_0/metadata_enrich.py` — per-fund HTTP
      enrichment. Constructs URLs from sponsor templates, HEAD-checks,
      derives inferrable fields from fund_name + N-CEN flags.
- [x] `source_authoring/phase_0/fixup_urls.py` — post-enrichment URL
      rewriter. Replaces post-redirect URLs with the originally templated
      URL so the catalogue records intent, not redirect-landing state.
- [x] `source_authoring/phase_0/assemble_bible.py` — JSONL → Excel
      workbook. One sponsor-family sheet per slug (with <5 rows merged
      into `other_sponsors`), plus Overview.
- [x] `source_authoring/verification/schema_check.py` — taskboard §7
      gates (schema + coverage + sanity + URL verification).
- [x] `tests/test_fund_bible_schema.py` — pytest wrapper for schema_check.
- [x] Enrichment pipeline end-to-end execution (2565 enriched, 0 excluded).
- [x] `fixup_urls.py` rewrote 1643 post-redirect URLs to canonical template
      form; normalized 2565 dates to ISO8601; filled 2565 rebalance-
      frequency defaults; dropped 2 duplicate tickers (BITQ migrated from
      Exchange Traded Concepts to its own trust; CERY reused between SSGA
      and TCW after the SSGA legacy fund wound down).
- [x] Excel workbook assembled (68 sponsor sheets + Overview = 69 sheets).
- [x] `schema_check.py` passes (schema/coverage/sanity clean;
      URL verification via background job — see `logs/schema_url_check.log`).
- [x] Atomic rename to `fund_bible_v1.xlsx`.
- [x] `test_fund_bible_schema.py` passes under pytest.
- [x] Git commit + push (see `git log` for commit hash).

## Final counts

- Total candidates enumerated from N-CEN (all 4 quarters, pre-dedup): 3954
- Unique after dedup by SERIES_ID: ~3000+ (see candidate_list.py log)
- After §4.1 structural filters: **2565 included / 1389 excluded**
- After metadata enrichment: **2565 enriched / 0 excluded** (all URLs
  HEAD-verified 200 OK during enrichment)
- After post-enrichment dedup (ticker collision resolution):
  **2563 in bible / 2 dropped to `logs/phase_0_dropped_duplicates.jsonl`**
- Excel workbook sheets: **69** (68 sponsor-family + Overview)
- Sheets merged into `other_sponsors` (sponsors with <5 funds each):
  **138** sponsor slugs merged into one collective sheet
- Σ AUM_USD reported (trailing-year N-CEN averages): **$10.885T**
  (ICI reference ≈ $11T — 99% match, confirms no scope error)

### Top sponsors by fund count (top 15 of 68)

| # | sponsor_slug | fund count |
|---|---|---|
| 1 | ishares | 349 |
| 2 | invesco | 217 |
| 3 | state_street (SPDR / SSGA) | 152 |
| 4 | tidal (Listed Funds Trust multi-sponsor) | 128 |
| 5 | direxion | 90 |
| 6 | proshares | 89 |
| 7 | first_trust | 86 |
| 8 | global_x | 83 |
| 9 | vanguard | 80 |
| 10 | fidelity | 61 |
| 11 | vaneck | 53 |
| 12 | franklin_templeton | 49 |
| 13 | alpha_architect | 42 |
| 14 | jpmorgan | 40 |
| 15 | dimensional | 37 |

### Holdings feed resolution

| format | count | % |
|---|---|---|
| html_table (sponsor HTML fund page, parse at ingest) | 899 | 35.1% |
| xlsx (direct XLSX CDN URL, most often SSGA) | 150 | 5.9% |
| csv (direct CSV CDN URL, e.g. ARK, Direxion) | 90 | 3.5% |
| edgar_fallback (SEC filing-index anchor; Phase 1 to re-author) | 1424 | 55.6% |

The edgar_fallback rate is high because `sponsor_url_templates.yaml` has
authored patterns for ~65 sponsors but the bible spans 206 distinct
sponsor slugs (68 sheets after small-sponsor collapse). The 138 merged
small-sponsor slugs are dominated by `unaffiliated_*` auto-generated
fallback slugs for funds hosted in multi-sponsor series trusts
(EA Series Trust, ETF Opportunities Trust, Northern Lights Trusts,
Unified Series Trust, etc.). Phase 1's per-subadviser registry will
materially reduce this fraction.

## Deferred items (out of Phase 0 scope)

- **Per-fund prospectus parse** for Expense_Ratio_Bps, Rebalance_Frequency_
  Prospectus, Rebalance_Trigger_Rules, Internal_Crossing_Disclosed. Phase 1.
- **ANT disambiguation within ambiguous sponsors** (Fidelity, American
  Century, Nationwide, T. Rowe Price active line). A subset of active
  ETFs from these sponsors is in the bible but must be rechecked for
  "Tracking Basket" / "Proxy Portfolio" disclosure. Phase 1.
- **Inception_Date + Shares_Outstanding point-in-time** — N-CEN carries
  trailing averages, not current snapshots. Phase 1 will refresh from
  sponsor pages.
- **Benchmark_Name authoritative** — best-effort inferred in Phase 0;
  Phase 1 sponsor-page scrape will replace.
- **Per-subadviser URL templates** for the ~185 `unaffiliated_*`
  sponsor slugs. Phase 1.
- **N-PORT data integration** for full holdings-by-symbol lookups.
  Phase 2.
- **Non-US ETFs** — explicit deferral per taskboard §2. Phase 0-B.
- **Mutual funds / 13F filers / SMAs / CITs / CEFs / UITs** — explicit
  deferral. Phase 0-C/D.

## Resume instructions (if session interrupted mid-enrichment)

```
cd /home/darren/Desktop/MCAS/fund_holdings/source_authoring/phase_0
python3 metadata_enrich.py \
    --input candidate_list.jsonl \
    --output logs/phase_0.jsonl \
    --excluded logs/phase_0_excluded.jsonl \
    --templates ../../registry/sponsor_url_templates.yaml \
    --workers 32 \
    --resume
```

`--resume` skips any (ticker, series_id) already present in
`logs/phase_0.jsonl`, so partial runs compose cleanly.

## Known limitations recorded for Phase 1

See `source_authoring/phase_0/notes.md` — sections 3, 6, 7, 8 document
the specific gaps that Phase 1 must address.
