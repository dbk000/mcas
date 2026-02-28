# EQ-WS1+WS2 Implementation Plan — SME Review

**Reviewer:** Opus 4.6 (contracted SME)  
**Date:** 2026-02-28  
**Document reviewed:** CC's `stateful-humming-kahan.md` — equities research program implementation plan  
**Context:** CC produced this after reviewing the full Backend codebase, all equities tables, and the crypto WS2 architecture. I reviewed it against the earlier prompt I wrote, the data reality from CC's initial coverage audit, and the crypto WS2 lessons learned.

---

## Overall Verdict: Strong plan, three structural gaps

CC has clearly learned from the crypto WS2 execution. The Bonferroni-binomial fix is embedded, Phase 0.5 feature viability audit is a smart addition that didn't exist in crypto, the embargo sensitivity analysis at 5/10/15 days is the right call, and the "written from scratch" constraint prevents crypto assumptions leaking in.

But there are three gaps that will either produce wrong answers or waste effort if not corrected before CC starts coding.

---

## GAP 1: Market-Adjusted Labelling (CRITICAL — will produce spurious findings)

### The Problem

The plan labels MOVED_UP if `z-score = forward_return / realized_vol_20 > 1.0`. This normalizes for volatility (good — NVDA's 3% daily move ≠ DUK's 0.5%) but does NOT subtract the market return.

Consider: On a day SPY rallies 2.5%, AAPL gains 2.8%, NVDA gains 3.2%, DUK gains 1.5%, JPM gains 2.3%. With vol-normalization alone, most of these get labeled MOVED_UP. But none of them outperformed the market. Any feature correlated with "the market is going up" — which includes most technical features during a trend — will appear predictive.

In a 7-month study window (Aug 2025 – Feb 2026), any persistent bull or bear trend will create clusters of correlated MOVED_UP/MOVED_DOWN labels across ALL 70 symbols simultaneously. Phase 5 walk-forward may not catch this because each fold can contain a multi-week trend where "everything goes up" produces correlated labels.

### Why This Didn't Matter for Crypto

Crypto WS2 studied pump events (>10.16% moves in 4-24h) on illiquid altcoins. These are idiosyncratic events — PEPEUSDT pumping 15% has near-zero correlation with ARBUSDT pumping 12%. Market beta doesn't dominate because the "crypto market" factor explains much less cross-sectional variance than SPY does for US large caps.

### The Fix

Add to Phase 1 labelling, one of:

**Option A (preferred):** Label on excess returns.
```
excess_return = forward_return - beta_60d * spy_forward_return
z_excess = excess_return / residual_vol_20
MOVED_UP if z_excess > 1.0
```
This requires pre-computing rolling 60-day beta and residual vol per symbol. ~20 extra lines of code. SPY OHLCV already in the universe.

**Option B (simpler):** Keep raw-return labels but include SPY_return as a mandatory control variable in Phase 3 correlations. Report partial correlations (feature-vs-label conditional on SPY_return). This is less clean but requires no label change.

**Option C (minimum):** Run the full study twice — once with raw labels, once with excess-return labels. Compare which features survive each. If the same features survive both, they're genuinely predictive. If features only survive raw labels, they're market-factor proxies.

Recommendation: **Option A as primary, with raw-return labels as a diagnostic cross-check.** The extra code is trivial, and it prevents the single most common false-positive source in equity factor research.

### What Happens If Not Fixed

The study will "discover" that realized_vol_20, roc_5, roc_10, overnight_gap, and possibly event_count_deviation predict MOVED_UP. These are all correlated with market momentum. The walk-forward validation may pass because the trend persists within folds. The holdout may pass because the holdout period shares the same trend regime. Only when you deploy the rule and the market reverses will precision collapse. This is the textbook regime-dependent false positive.

---

## GAP 2: Effective Sample Size vs Nominal Sample Size (IMPORTANT — will produce overconfident results)

### The Problem

The plan correctly flags this in "Known Limitations" point 4: "70 symbols provides fewer independent cross-sectional observations than crypto's 200 symbols." But it doesn't quantify how bad this is or adjust the analysis.

The numbers:
- 70 symbols × ~130 trading days (Aug–Feb) × 4 horizons = nominal ~36K label observations
- But on any given day, all 70 symbols share the same macro environment
- Cross-sectional correlation for large-cap US stocks is typically 0.3–0.6 (driven by market factor + sector factors)
- With average pairwise correlation ρ ≈ 0.4 and N = 70 symbols, the effective independent sample size per day is approximately N / (1 + (N-1)ρ) ≈ 70 / 28.6 ≈ 2.4 independent observations per day
- Over 130 trading days: ~312 effective independent observations per horizon
- Compare: crypto WS2 had ~33K events per window with much lower cross-sectional correlation among altcoins

312 effective observations per horizon is enough for Phase 3 correlations to detect large effects, but it's borderline for Phase 5/6 walk-forward + Bonferroni. With 6 walk-forward folds on 312 effective observations, each fold has ~52 effective observations. That's thin.

### The Fix (three levels)

**Level 1 (minimum — add to Phase 3 output):** Compute and report the effective sample size using the Moulton correction factor. Print it prominently: "Nominal N = 36,000. Effective N (Moulton-corrected for cross-sectional correlation) = ~1,250." This sets expectations for what the study can and cannot detect.

**Level 2 (recommended — add to Phase 6):** When computing Bonferroni p-values, use the effective N rather than nominal N for the binomial test. This is conservative but honest. A rule that survives Bonferroni at effective-N is genuinely robust.

**Level 3 (ideal but complex — note for future):** Cluster-robust standard errors or block bootstrap (blocks = trading days, with all symbols on the same day in the same block). This would be the statistically proper approach. The plan already acknowledges this would add complexity and defers it. That's reasonable for a first pass, but document clearly that any validated rule with precision just above threshold should be treated with extra skepticism.

### Interaction with Gap 1

If you fix Gap 1 (excess-return labels), the cross-sectional correlation of labels drops significantly because you've removed the shared market factor. The effective sample size improvement from excess-return labelling is substantial — excess returns for 70 stocks have pairwise correlation ~0.1–0.2 rather than 0.3–0.6, which increases effective N by roughly 3×.

This is another reason Gap 1 is the highest-leverage fix. It simultaneously reduces spurious findings AND increases statistical power.

---

## GAP 3: The Content Void — Correctly Identified, But Strategic Implications Under-Explored

### What the Plan Gets Right

Phase 0.5 will identify dead features (keyword_density, content_length for SEC, source_diversity). The plan expects ~8% content coverage (NEWS only). The LLM opportunity assessment in Phase 7 is correctly scoped.

### What's Missing: A Pre-Study Power Calculation for Content Features

The plan proposes to test content features alongside technical features, let Phase 3 rank them, and determine if content matters. But with 8% coverage, the content features are fighting with one hand tied behind their back.

Consider: `tone_score` has 8% non-zero coverage. For Phase 3 point-biserial correlation, the effective sample size for tone_score is 8% × 36K ≈ 2,900 observations (nominal). With the Moulton correction from Gap 2, it's more like ~250. At n=250, you can detect correlations of |r| > 0.12 at α=0.05. That's actually still useful — if tone_score has r=0.15 or higher, you'll see it. But if the true effect is r=0.05–0.10 (plausible for keyword sentiment on news headlines in efficient markets), you won't have power to detect it.

The plan should set this expectation explicitly: **Phase 3 can detect strong content effects but will miss moderate ones due to coverage.** A null result for content features means "insufficient data" not "content doesn't matter." The LLM opportunity assessment should factor this in — the question isn't just "did keyword scoring work?" but "is the coverage sufficient to test ANY content hypothesis?"

### The Strategic Frame

For equities, the honest answer might be: "With 8% NEWS coverage, dead GDELT, dead Reddit, and 16-char SEC filing titles, there is currently no content signal to find — not because content doesn't predict, but because we don't have content." The study might produce a null result for content that is entirely explained by data availability, not by the information-theoretic question of whether content matters.

The plan's LLM assessment should distinguish:
1. "Keyword scoring doesn't predict" (scorer quality problem)
2. "No content of any kind predicts" (data availability problem)  
3. "Content of specific types predicts" (points to where LLM processing would help)

If the filing TYPE classification (8-K vs 10-Q) shows signal but tone_score doesn't, that's a strong indicator that structured filing metadata predicts but keyword NLP on titles doesn't add anything — and that the opportunity is in LLM-based reading of filing BODIES, not better headline processing.

---

## Smaller Issues (worth correcting, not structural)

### A. Earnings Calendar Yield May Be Low

The plan estimates 70 symbols × 4-8 quarters = 280-560 rows from NEWS_EARNINGS + 8-K Item 2.02. But NEWS events are only 8% of total events, and not all of those are earnings announcements. The 8-K 2.02 approach is clever but depends on the item number being in the title text — if the SEC filing title is just "8-K" without the item detail, the filter won't match.

**Recommendation:** Add a diagnostic to Step 0A that reports: (a) count of NEWS_EARNINGS events, (b) count of 8-K events with "2.02" in title, (c) count of 8-K events WITHOUT item numbers in title. If (c) >> (b), the fallback to 10-Q/10-K filing dates as `confirmed=false` will be needed for most symbols.

### B. The 10-Day Embargo Is Probably Fine in Practice

The plan worries about SMA_200 sharing 95% of input data at 10-day embargo. This is mathematically true but practically irrelevant. SMA_200 at day T and day T+10 differ by ~0.5%. The feature doesn't carry novel information across the boundary — it's effectively a constant at this timescale. The embargo sensitivity analysis at 5/10/15 days will confirm this. If results are indistinguishable across all three embargo lengths, it validates that slow-moving features don't create meaningful leakage. If results differ, that's a genuine finding about feature contamination.

No change needed — the plan's sensitivity analysis is the right approach.

### C. ETF Handling Needs Clarification

The plan says "ETFs (SPY, QQQ, sector SPDRs) used as benchmarks only, not as cross-sectional study targets." But Phase 1 loads OHLCV for all 70 symbols and Phase 2 computes features for all. The question: are ETFs included in the label dataset (MOVED_UP/DOWN) or not?

If included: ETFs and their constituent stocks create duplicate exposure. SPY MOVED_UP and AAPL MOVED_UP on the same day are not independent observations — they're the same market move measured twice.

If excluded: 56 stocks only. But then ETF OHLCV is still loaded for benchmark computations (sector_relative_return, SPY vol proxy).

**Recommendation:** Exclude ETFs from the label dataset. Use them as benchmark inputs only. Reduces nominal N from 70 to 56 but removes systematic duplication. State this explicitly in Phase 1.

### D. Phase 0.5 Should Also Check Feature VARIANCE, Not Just Zero-Rate

A feature with 20% non-zero rate but near-zero variance when non-zero (e.g., `source_diversity` = 1 for 80% of non-zero values because it's effectively binary SEC-or-NEWS) is dead in spirit even if it passes the >80% zero kill rule. Add a variance floor (var < 1e-8) as the plan already mentions, but also flag features with effective cardinality < 3 (fewer than 3 distinct non-zero values).

### E. The `sector_relative_return` Double-Count

`sector_relative_return` is listed as both a technical indicator in INDICATOR_REGISTRY (#19) AND a market structure feature. Make sure it's included once, not twice, to avoid doubling its Bonferroni cost.

### F. FDR Correction Scope Is Correct and Smart

The plan applies BH-FDR "across ALL horizons simultaneously" rather than per-horizon. This is the right choice — testing 80 features × 4 horizons = 320 tests, and applying FDR at 320 prevents cherry-picking the horizon where noise happened to align. The crypto WS2 plan didn't specify this as clearly. Good improvement.

---

## What I'd Expect to Find (Setting Expectations Before Results)

Given that this is 70 liquid US large-cap stocks with free data sources:

### Likely Findings
1. **Technical features dominate.** With full OHLCV coverage and proven indicators, features like RSI, bollinger_width, volume_ratio, and realized_vol will show the strongest correlations. This is not a crypto-style market where OHLCV features failed.

2. **Content features are mostly dead.** Not because content doesn't matter — because coverage is 8% and titles are 16 characters. The study correctly frames this as a data gap, not a conceptual failure.

3. **Filing TYPE classification might show something.** Whether the event is an 8-K, 10-Q, or NEWS_EARNINGS is a categorical feature with 100% coverage. If 8-K filings (material events) correlate with forward moves differently than routine 10-Q filings, that's a genuine finding about information type even without content analysis.

4. **Earnings proximity will be significant.** This is one of the strongest known factors in equity markets. Returns around earnings dates are structurally different from non-earnings periods. The plan includes this feature.

5. **gap_open horizon may be the best.** For event-driven equities, the overnight gap is where information gets priced. Post-close events predicting next-day open should show the highest signal-to-noise ratio of any horizon.

6. **Few or no validated rules may survive Bonferroni.** This would be the CORRECT answer for large-cap US equities with free data sources. These are some of the most efficiently priced securities in the world. If systematic edges existed that could be found with 35-regex keyword scoring and standard technical indicators, they'd have been arbed away. A null result is not a failure — it's a finding that either (a) better data sources are needed, or (b) the edge is too small to detect at this sample size.

### What Would Be Surprising (and Worth Extra Scrutiny)
- Any single feature with holdout lift > 3× on excess-return labels (not raw labels)
- Content features surviving Bonferroni despite 8% coverage
- Any finding that contradicts efficient market pricing for large-cap US equities

---

## Execution Sequence — Amended Recommendation

1. **Step 0A: Earnings calendar** — proceed as planned, add diagnostic counts per recommendation A
2. **Step 0B: load_raw_mentions()** — proceed as planned
3. **Step 0C: Add excess-return labelling** — NEW. Compute rolling 60-day beta per symbol, excess returns, residual vol. Add to `content_features.py` or as a separate utility
4. **Step 1: content_features.py** — add ETF exclusion flag, deduplicate sector_relative_return
5. **Step 2: EQ-WS1** — proceed as planned. Phase 2 feature attribution should use BOTH raw and excess return labels
6. **Step 3: Run EQ-WS1** — proceed as planned
7. **Step 4: EQ-WS2** — implement with excess-return labels as primary, raw-return labels as diagnostic. Report Moulton-corrected effective N. ETFs excluded from label dataset.
8. **Step 5: Run EQ-WS2** — proceed as planned

---

## Summary Table

| Issue | Severity | Fix Complexity | Impact If Not Fixed |
|-------|----------|---------------|-------------------|
| Market-adjusted labelling | CRITICAL | Low (~30 lines) | Spurious findings from market factor |
| Effective sample size | IMPORTANT | Low (reporting) | Overconfident p-values, false positives |
| Content void framing | MODERATE | None (wording) | Misinterpretation of null results |
| Earnings calendar yield | LOW | Low (diagnostic) | Missing earnings dates for some symbols |
| ETF duplication | LOW | Low (exclusion flag) | Inflated cross-sectional N |
| sector_relative_return double-count | TRIVIAL | Trivial | Wastes 1 Bonferroni test |

The plan is ready to execute with the Gap 1 fix. Gaps 2 and 3 are reporting/framing changes that improve interpretation but don't require code changes to the study pipeline.
