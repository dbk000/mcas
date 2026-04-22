# Phase 0 — Fund Bible (NX1 Rebalancing Reverse-Engineering)

**Owner:** Darren
**Executor:** Claude Code, one-shot session
**Parent strategy:** NX1 — Institutional Rebalancing Reverse-Engineering (concept stage; this phase produces the substrate, not the strategy itself)
**Governance posture:** Research-grade. Structure must match production kernel patterns so this artifact can be retro-hardened before live capital. No three-laws audit required per-step in this session — structure first, rigor later.

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

```
/home/darren/Desktop/MCAS/fund_holdings/
├── PHASE_0_TASKBOARD.md           (this file, copied on arrival)
├── PHASE_0_SESSION_STATE.md       (CC writes progress here; resume point if needed)
├── ground_truth/
│   └── fund_bible_v1.xlsx         (DELIVERABLE)
├── registry/
│   └── namespace_rules.yaml       (copy of equities_news version; adapted for fund namespaces)
├── source_authoring/
│   ├── phase_0/
│   │   ├── candidate_list.py      (scripts used to enumerate candidates)
│   │   ├── metadata_enrich.py     (scripts used to fill metadata columns)
│   │   ├── logs/
│   │   │   └── phase_0.jsonl      (one line per fund, append-only audit trail)
│   │   └── notes.md               (free-form CC notes / decisions / gotchas)
│   └── verification/
│       └── schema_check.py        (validates fund_bible_v1.xlsx schema + coverage)
└── tests/
    └── test_fund_bible_schema.py  (pytest; runs schema_check + basic sanity)
```

`fund_holdings/` is a sibling of `equities_news/` under the MCAS repo root. Commit from MCAS root per the existing convention.

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

### 6.1 Candidate enumeration (§5 population)

The first problem is building the candidate list of funds before filtering. Preferred sources, in order:

1. **SEC Form N-CEN structured data set** — the SEC publishes quarterly .tsv bundles of N-CEN filings (all registered investment companies), including an ETF-specific table with fields for creation unit size, AP relationships, in-kind-ETF flag, custom basket adoption, and sponsor identifiers. This is the authoritative US-ETF starting set. URL base: `https://www.sec.gov/data-research/sec-markets-data/form-n-cen-data-sets`. Use the most recent quarterly release.
2. **SEC EDGAR company search** — filter to Form N-1A + Investment Company Type = ETF for any recent ETF launches not yet in the most recent N-CEN bundle.
3. **NYSE Arca, Nasdaq, Cboe BZX listing pages** — each exchange publishes its ETF listings. Cross-check listed tickers against SEC set to catch gaps.
4. **etf.com screener and etfdb.com** — free web screeners with AUM filter; useful for sanity-checking the candidate count but NOT as primary source (third-party sites may lag or err).
5. **ICI (Investment Company Institute) statistics** — publishes aggregate ETF counts and AUM; sanity check only.

CC should produce the candidate list from source 1, enrich with 2-3, spot-check with 4-5. Target: ~3,000-3,800 gross candidates before the structural tests in §4. Final bible likely ~2,500-3,200 funds after daily-holdings and proxy-basket filters (most exclusions are proxy-basket ANTs and sub-$5M shells, not AUM tier). Respect SEC's 10 requests/second rate limit; include a `User-Agent: Darren Kenny <dkcrypto520@gmail.com>` header on all SEC requests per SEC's fair-access policy.

### 6.2 Metadata enrichment

For each candidate passing §4 structural tests, enrich metadata from these sources in order:

1. **Sponsor website fund page** — canonical for Holdings_Feed_URL, Sponsor_Page_URL, AUM, Shares_Outstanding, Inception_Date. This is the source of truth.
2. **Prospectus / summary prospectus** (linked from sponsor page, SEC EDGAR as backup) — canonical for Rebalance_Frequency_Prospectus, Rebalance_Trigger_Rules, Replication_Method, Creation_Unit_Size, Has_Derivatives_Overlay, Internal_Crossing_Disclosed.
3. **Fund fact sheet (PDF on sponsor page)** — backup for any field missing from 1-2.
4. **Morningstar fund page** — last-resort sanity check only. Do NOT use as primary source.

### 6.3 Fallback rules

- If any required field cannot be resolved from sources 1-3 after reasonable effort, enter the literal string `"null"` in the cell and add an entry to the Notes column explaining the gap. Do NOT fabricate values. Fabrication fails the schema_check which does a spot-URL-resolve on `Holdings_Feed_URL`.
- If Holdings_Feed_URL cannot be resolved, the fund is **EXCLUDED** regardless of all other criteria — the holdings feed is load-bearing for the whole thesis.

## 7. Verification gates

Before declaring the session complete, CC runs:

### 7.1 Schema check (`source_authoring/verification/schema_check.py`)

- Every row has every column.
- Every enum value is in the permitted enum set.
- **`Holdings_Feed_URL` (column 19) is load-bearing — 100% of rows must be verified.** HEAD request to each URL; 200/302 acceptable; 403 acceptable ONLY if the request succeeds with a browser-like User-Agent header (many sponsor CDNs bot-block default Python headers but serve real browsers); 404 or connection-refused = fail, and the row is either re-researched or EXCLUDED. Do not commit a bible with any broken Holdings_Feed_URL.
- For all other URL columns (22, 23), 5% random sample HEAD-check. If sample pass rate <95%, re-check the full population of the failing sponsor group.
- Every numeric column is a valid number (or "null").
- Every date column is ISO8601 or Excel-date-parseable.

### 7.2 Coverage check

- Overview sheet `Total` row matches the sum of per-sheet row counts.
- Per-sponsor row count is in a sensible band (iShares ~400, Vanguard ~85, SSGA ~140, etc. — CC can refine the bands from the candidate enumeration step).
- At least 95% of rows have non-null values for columns 4, 5, 11, 15, 16, 19, 20. These are the load-bearing fields for downstream research.
- Every row has Lineage columns populated.

### 7.3 Sanity check

- Sum of AUM_USD is within 20% of the public ICI total US ETF AUM figure. (Current public total ~$11T. With the $5M floor, the fund bible should capture essentially all ETF AUM — the long tail of sub-$5M shells is negligible in total dollar terms.) Substantial deviation flags either a scope error or a data-quality problem and must be resolved before commit.
- No duplicate tickers.
- Every Holdings_Feed_URL is distinct at the fund level (but may share a domain with siblings).

### 7.4 Self-audit checklist (CC must complete before commit)

- [ ] Every ticker in the bible passes all §4 structural tests (re-confirm by sampling 20 random entries end-to-end).
- [ ] No non-US-listed, non-1940-Act, or proxy-basket funds slipped through.
- [ ] `phase_0.jsonl` has exactly one line per fund in the bible.
- [ ] Schema check passes clean.
- [ ] Coverage check passes clean.
- [ ] Sanity check passes clean.
- [ ] `PHASE_0_SESSION_STATE.md` is updated with final counts and any deferred items.

## 8. Session discipline

- **One session, one commit.** If the session hits its context limit before completion, CC writes full state to `PHASE_0_SESSION_STATE.md` and commits whatever is complete with message `phase_0: partial — N of ~3000 funds catalogued, resume via session state file`. Subsequent session resumes per the state file. Do not force one-shot completion if quality would suffer.
- **Append-only author log.** `phase_0.jsonl` is written one line at a time, flushed immediately, never rewritten. Crash-safe.
- **Do not delete or overwrite the Excel file during the session.** Write to `fund_bible_v1.xlsx.tmp`, then atomic rename to `fund_bible_v1.xlsx` only after schema_check passes.
- **Commit scope:** `fund_holdings/` only. Never `-A`. Branch `main`, direct commit per MCAS convention.
- **Every function in the scripts CC writes does real work or raises `NotImplementedError`.** No stub functions, no silent pass-through. This is the one production-grade discipline we keep even at research-grade governance — stubs metastasise.
- **No hardcoded sponsor lists in code.** If a "known daily disclosers" set is needed for §4.1.3 secondary test, it lives in `fund_holdings/registry/known_daily_disclosers.yaml`, not in a Python literal. Same for any other enumerable set.

## 9. Deliverable definition — "done" means:

1. `fund_holdings/ground_truth/fund_bible_v1.xlsx` exists, schema-conformant, ~2,500-3,200 rows across sponsor-family sheets plus Overview.
2. `fund_holdings/source_authoring/phase_0/logs/phase_0.jsonl` has one line per fund.
3. `fund_holdings/source_authoring/verification/schema_check.py` exits 0.
4. `fund_holdings/tests/test_fund_bible_schema.py` passes under pytest.
5. `PHASE_0_SESSION_STATE.md` documents final state + any deferred items (flagged non-strict-inclusions, partial metadata, etc.).
6. `notes.md` documents non-obvious decisions made during authoring.
7. Single commit, pushed to `origin/main`, message: `phase_0_fund_bible: N funds catalogued (iShares X, Vanguard Y, ...)`

## 10. Explicit non-scope reminders (end-of-session discipline)

Do NOT, at any point:

- Attempt to infer or propose rebalancing rules from the metadata.
- Download any holdings CSV.
- Build any ingestion adapter.
- Extend scope to non-US ETFs, mutual funds, or other vehicle types.
- Touch `equities_news/` or any other MCAS subsystem.
- Author the source registry (Phase 1 work).
- Make any claims about edge, expectancy, or tradeability.

The Phase 0 deliverable is a *catalogue*. Nothing more. Its value is that everything downstream (Phase 1 source registry, Phase 2 backfill, Phase 3 market data, Phase 4 ingestion, Phase 5 research) can proceed cleanly from a governed starting substrate.

## 11. Session start checklist (first actions on session open)

1. Read this taskboard end-to-end.
2. `mkdir -p /home/darren/Desktop/MCAS/fund_holdings/{ground_truth,registry,source_authoring/phase_0/logs,source_authoring/verification,tests}`
3. Copy `equities_news/registry/namespace_rules.yaml` → `fund_holdings/registry/namespace_rules.yaml`. Adapt namespaces for fund-keyed identifiers (`fund.{sponsor_slug}.{ticker_slug}` etc. — use judgement, keep pattern consistent with equities_news).
4. Write the `candidate_list.py` script to enumerate candidates from SEC + exchange sources.
5. Write the `metadata_enrich.py` script to walk sponsor pages and prospectuses.
6. Iterate; write `phase_0.jsonl` as you go; write `fund_bible_v1.xlsx.tmp` as you go.
7. Run `schema_check.py` and `test_fund_bible_schema.py`.
8. Atomic-rename `.tmp` → `fund_bible_v1.xlsx`.
9. Update `PHASE_0_SESSION_STATE.md`.
10. Commit + push from MCAS root.

---

**End of taskboard.** If anything in this document is ambiguous when you sit down to execute, STOP and flag — do not guess. Ambiguity in a foundational catalogue compounds downstream. Better to ask Darren than to produce a polluted bible.
