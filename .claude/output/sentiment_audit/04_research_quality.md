# Tone Engine Forward Predictor Discovery: Research Quality Audit

**Auditor:** Claude Opus 4.6 (S-audit session)
**Date:** 2026-02-22
**Scope:** Research outputs, data quality, and statistical validity of the Tone Engine forward predictor discovery pipeline.

**Key Files Examined:**
- `/home/darren/Desktop/MCAS/MCAS_sentiment/tone_forward_predictors_relationships_aggregated.csv` (343 rows, 49 features x 7 horizons)
- `/home/darren/Desktop/MCAS/MCAS_sentiment/tone_forward_predictors_discovery.csv` (60 rows)
- `/home/darren/Desktop/MCAS/MCAS_sentiment/tone_forward_predictors_relationships_concat.csv` (1372 rows, 4 shards)
- `/home/darren/Desktop/MCAS/MCAS_sentiment/Backend/scripts/tone_engine_forward_predictor_discovery.py` (1096 lines)
- `/home/darren/Desktop/MCAS/MCAS_sentiment/synthetic_data_audit.md`
- `/home/darren/Desktop/MCAS/MCAS_sentiment/activity_report.md`
- `/home/darren/Desktop/MCAS/MCAS_sentiment/Backend/logs/forward_discovery_20260222_221416/` (per-shard CSVs)
- `/home/darren/Desktop/MCAS/MCAS_sentiment/analysis/sim_runs/` (7 simulation run directories)

---

## 1. FEATURE UNIVERSE ASSESSMENT

### 1.1 Feature Inventory

The discovery script (`tone_engine_forward_predictor_discovery.py`) defines 49 features split into "basic" (20) and "extended" (29 additional) sets.

**Basic features (20):**
- Price-derived: `ret_1h`, `ret_6h`, `ret_24h`, `range_pct`, `vol_ratio_24`, `realized_vol_24`, `trend_24`, `trend_72`, `vol_regime_24_72`
- Social raw counts: `mentions_1h`, `mentions_6h`, `mentions_24h`, `authors_6h`, `authors_24h`
- Social ratios: `attention_ratio_6h`, `mention_accel_6h`, `posts_per_author_6h`
- Social content: `text_len_6h`, `exclaim_6h`, `cashtag_6h`

**Extended features (29 additional):**
- Z-scores: `z_ret_6h_72`, `z_ret_24h_168`, `z_mentions_6h_72`, `z_mentions_24h_168`, `z_attention_6h_168`, `z_vol_ratio_24_168`, `z_realized_vol_24_168`
- Author structure: `author_diversity_24h`, `author_concentration_24h`, `mention_author_gap_24h`
- Surprise metrics: `surprise_mentions_1h`, `surprise_mentions_6h`, `surprise_mentions_24h`
- Divergences: `social_price_divergence_6h`, `social_price_divergence_24h`, `attention_vs_volume_gap`, `attention_vs_volatility_gap`
- Interaction terms: `crowding_pressure`, `panic_chatter`, `ticker_frenzy`, `verbosity_pressure`
- Cross-domain couplings: `social_reversion_pressure`, `social_trend_pressure`, `accel_reversion_pressure`, `accel_trend_pressure`, `vol_attention_coupling`, `range_attention_coupling`, `volume_attention_coupling`, `momentum_attention_coupling`

### 1.2 Novelty Assessment

**VERDICT: Mixed. The features are a blend of renamed TA indicators and genuinely novel social-price interaction terms.**

The price-only features (9 of 49) are standard technical analysis under different names:
- `realized_vol_24` = 24-bar rolling standard deviation of returns (textbook realized volatility)
- `range_pct` = (high - low) / close (standard range indicator, variant of ATR)
- `trend_24`, `trend_72` = rolling mean of 1h returns (simple momentum)
- `vol_ratio_24` = volume / 24-bar moving average of volume (relative volume, classic)
- `vol_regime_24_72` = volatility regime ratio (standard regime detection)
- `ret_1h/6h/24h` = simple returns (trivially standard)

The social count features (mentions, authors, attention_ratio) are standard social media analytics -- not novel, extensively studied in academic literature on social media sentiment and crypto markets.

**The genuinely interesting features are the interaction terms** (15 of 49):
- `vol_attention_coupling` = realized_vol * z_mentions_6h (volatility-social coupling)
- `range_attention_coupling` = range_pct * z_mentions_6h
- `social_price_divergence_6h/24h` = z_mentions - z_abs_return (social-price divergence)
- `crowding_pressure` = posts_per_author * attention_ratio (behavioral crowding)
- `panic_chatter` = exclaim_rate * surprise_mentions (content-driven panic metric)

These cross-domain interaction features do represent a step beyond standard sentiment analysis. However, they are still relatively simple multiplicative interactions -- not the kind of high-dimensional pattern discovery that CLAUDE.md Section 0.1 calls for. There is no non-linear feature engineering, no learned representations, no multi-symbol cross-sectional structure discovery.

**Missing from the feature universe:**
- Time-of-day / day-of-week effects
- Cross-symbol contagion (social spillover from BTC/ETH to altcoins)
- Social network structure features (reply chains, retweet cascades)
- NLP-derived features (sentiment polarity, topic clustering)
- Order-book-derived features
- On-chain metrics

### 1.3 Redundancy Problem

Several feature pairs are algebraically identical or near-identical:
- `author_diversity_24h` = 1 / `author_concentration_24h` (they appear as separate features but are monotonic transforms of each other -- choosing opposite sides yields identical signals)
- `social_reversion_pressure` = -1 * `social_trend_pressure` (literal negation)
- `accel_reversion_pressure` = -1 * `accel_trend_pressure` (literal negation)
- `mentions_6h` and `authors_6h` are highly correlated (both are 6h rolling sums of correlated inputs)
- `mentions_24h` and `authors_24h` (same issue at 24h window)

This means the effective unique feature count is closer to 40, not 49. The negation pairs are particularly egregious: they appear as separate "discoveries" in the results (e.g., `accel_reversion_pressure` h=120 and `accel_trend_pressure` h=120 both show ~219 bps edge), artificially inflating the appearance of breadth.

---

## 2. STATISTICAL RIGOR ASSESSMENT

### 2.1 Compliance with MCAS Validation Standards (CLAUDE.md Section 4)

| Requirement | Status | Details |
|---|---|---|
| Walk-Forward Validation (4.1) | **ABSENT** | No walk-forward folds. The script processes all data in a single pass. No train/test split. No temporal purging. |
| Holdout Set (4.2) | **ABSENT** | No holdout reservation. All data used for both feature computation and evaluation. |
| DSR Correction (4.3) | **ABSENT** | 49 features x 7 horizons = 343 tests performed. No Deflated Sharpe Ratio correction applied. No `n_trials` reporting. |
| Transaction Cost Model (4.4) | **ABSENT** | Raw return edges reported. No fee/slippage deduction in the discovery pipeline. |
| Average Daily PF (4.5) | **ABSENT** | No daily profit factor computation. The metric used is `predictor_score` (a composite of edge, IC, and event count). |

**This research fails every single validation standard required by CLAUDE.md Section 4.**

The discovery script performs what is essentially an in-sample screen: it computes features and targets over the same data period, evaluates cross-sectional Spearman IC and decile returns, and ranks by a composite score. There is no out-of-sample evaluation of any kind.

### 2.2 Multiple Testing Problem

With 49 features x 7 horizons = 343 hypothesis tests, the probability of finding spurious results is very high. At a conventional 5% significance level, you'd expect ~17 false positives by chance alone. The script applies no correction:
- No Bonferroni correction
- No Benjamini-Hochberg FDR control
- No DSR (as required by CLAUDE.md Section 4.3)
- No bootstrap confidence intervals

The `predictor_score` formula (`max(0, edge) * |mean_ic| * log1p(events) * 10000`) is ad hoc and does not account for the number of tests conducted.

### 2.3 Cross-Sectional IC Methodology

The script computes cross-sectional Spearman IC at each timestamp, then averages across timestamps. This is a reasonable methodological choice for cross-sectional factors, but with critical caveats:

**Sampling bias in overall IC:** When n_obs > 100,000, the script subsamples 100,000 observations for the overall IC computation (line 709-711). This is fine for computational efficiency but means the overall IC is a noisy estimate with uncontrolled sampling variance.

**No temporal structure in IC evaluation:** The cross-sectional ICs are averaged without weighting by time, without testing for temporal stability, and without checking whether the IC is decaying, regime-dependent, or concentrated in specific periods. A feature could have IC of +0.10 in the first half and -0.05 in the second half, and the script would report the average as "positive signal" without flagging the instability.

### 2.4 Decile Construction

The top/bottom decile analysis (line 737-749) uses percentile cutoffs computed at each cross-section. This is methodologically sound for cross-sectional factor analysis. However:
- The "short edge" metric is simply the negation of the mean return of the extreme decile. Shorting the top decile means the "edge" is the average decline of the highest-feature-value stocks. This conflates mean-reversion with genuine predictive power.
- For features like `realized_vol_24`, the "top decile" consists of the highest-volatility assets. Reporting that shorting these yields ~200 bps over 120 bars (5 days) is largely reporting that high-volatility assets have negative forward returns on average -- a well-documented risk premium that is not a new discovery and is extremely difficult to capture after transaction costs.

---

## 3. EDGE MAGNITUDE ANALYSIS

### 3.1 Reported Edges vs. Cost Model

The top features report edges in the range of 100-220 bps at the 120-bar horizon (5 days at 1h timeframe).

Applying MCAS cost model (Section 4.4) for 1h timeframe:
- Slippage: 5 bps per leg = 10 bps round-trip
- Fee: 4 bps taker per leg = 8 bps round-trip
- Multi-symbol penalty: +5 bps (cross-sectional strategy)
- **Total base cost: 23 bps per trade**
- **At 1.5x slippage: 30.5 bps per trade**
- **At 2x slippage: 38 bps per trade**

| Feature | Horizon | Gross Edge (bps) | Net Edge (base costs) | Net Edge (1.5x slip) | Survives 1.5x? |
|---|---|---|---|---|---|
| vol_attention_coupling | 120 (5d) | 220.7 | 197.7 | 190.2 | YES |
| realized_vol_24 | 120 (5d) | 199.3 | 176.3 | 168.8 | YES |
| range_pct | 120 (5d) | 192.0 | 169.0 | 161.5 | YES |
| realized_vol_24 | 72 (3d) | 114.4 | 91.4 | 83.9 | YES |
| range_pct | 72 (3d) | 117.1 | 94.1 | 86.6 | YES |
| range_pct | 48 (2d) | 81.8 | 58.8 | 51.3 | YES |
| realized_vol_24 | 48 (2d) | 76.0 | 53.0 | 45.5 | YES |
| realized_vol_24 | 24 (1d) | 43.5 | 20.5 | 13.0 | Marginal |
| range_pct | 24 (1d) | 45.6 | 22.6 | 15.1 | Marginal |
| range_pct | 6 (6h) | 15.5 | -7.5 | -15.0 | NO |
| realized_vol_24 | 6 (6h) | 14.1 | -8.9 | -16.4 | NO |

**Critical observation:** The edges at short horizons (6-12 bars) are consumed by transaction costs. The large edges (150-220 bps) exist only at the 120-bar (5-day) horizon, which raises a fundamental question: **are these trading signals or are they slow-moving risk premia?**

### 3.2 The Volatility Premium Confound

The highest-ranked features are `realized_vol_24` and `range_pct` -- both volatility measures. At 120-bar horizon, shorting the most volatile assets yields ~200 bps. This is almost certainly the **well-documented volatility risk premium**: high-volatility assets tend to underperform on a risk-adjusted basis. This has been extensively studied:
- Ang et al. (2006): "The Cross-Section of Volatility and Expected Returns" -- high-volatility stocks underperform
- Baker et al. (2011): "Benchmarks as Limits to Arbitrage" -- low-volatility anomaly in equities
- The same pattern exists in crypto (multiple papers, 2018-2024)

This is not a novel discovery from social data. It is a standard risk factor dressed up in the results of a social-tone pipeline. The social data contributes virtually nothing to the top-ranked features -- `realized_vol_24` and `range_pct` are computed entirely from OHLCV data with no social inputs whatsoever.

### 3.3 Social Feature Edges Are Much Smaller

When we isolate features that actually use social data:
- `vol_attention_coupling` h=120: 220 bps (but this is realized_vol * z_mentions -- dominated by the volatility component)
- `mentions_24h` h=72: 56.3 bps (marginal after costs)
- `attention_ratio_6h` h=72: 84.3 bps (includes a volatility confound via the denominator's sensitivity to market conditions)
- `surprise_mentions_6h` h=120: 175.4 bps (but at 5-day horizon, this is effectively a slow-moving contrarian indicator)

The pure social features (`mentions_Xh`, `authors_Xh`) show edges of 15-60 bps at the horizons where they might be tradeable (24-72 bars), which are marginal to negative after costs.

---

## 4. CROSS-SECTIONAL IC INTERPRETATION

### 4.1 Reported ICs: -0.05 to -0.13

The mean cross-sectional ICs for the top features are:
- `realized_vol_24` h=120: IC = -0.125
- `range_pct` h=120: IC = -0.112
- `vol_attention_coupling` h=120: IC = +0.070
- `mentions_24h` h=72: IC = -0.002 (effectively zero)
- `surprise_mentions_6h` h=120: IC = +0.025

**Interpretation:** In quantitative equity research, cross-sectional ICs of 0.02-0.05 are considered meaningful, and ICs above 0.10 are considered strong. The reported ICs for volatility features (0.10-0.13) are strong for cross-sectional factor research. However:

1. **These are in-sample ICs with no out-of-sample validation.** In-sample ICs routinely degrade 50-80% out of sample.
2. **The high ICs are on volatility measures, not social features.** Social-only features have ICs near zero (0.01-0.03).
3. **IC magnitude does not translate linearly to trading profits.** An IC of -0.10 for a long-short decile portfolio means roughly 10% of the cross-sectional return variance is explained -- useful for factor investing, but the implementation cost (holding 10+ positions simultaneously, rebalancing daily or hourly) eats into the theoretical edge.
4. **The std of cross-sectional IC is very high** (0.14-0.28), meaning the IC sign flips frequently across timestamps. A feature with mean IC of -0.10 and std of 0.25 has negative IC roughly 35% of the time -- not a reliable signal.

### 4.2 The std_cs_ic Problem

Every feature in the results shows `std_cs_ic` that is 1.5-4x the magnitude of `mean_cs_ic`. This means the cross-sectional relationship between feature and forward return is extremely noisy on a per-timestamp basis. The t-statistics look large (20-40+) only because of the enormous number of cross-sectional observations (5000-8000 timestamps). This is a classic case of statistical significance without practical significance.

---

## 5. P(DUMP) ANALYSIS

### 5.1 Reported Values: 0.58-0.63

The `p_dump_chosen` metric represents the probability that a trade in the chosen extreme decile produces a negative forward return. Values of 0.58-0.63 mean:
- **58-63% of the time, the trade loses money**
- Only 37-42% of trades are winners
- The strategy relies entirely on the winners being larger than the losers (positive skew)

This is consistent with a contrarian/mean-reversion strategy applied to volatile assets: most of the time the high-volatility asset continues moving against you (hence the high loss rate), but occasionally it reverts sharply enough to offset the frequent small losses.

### 5.2 Practical Implications

A 37-42% win rate with a 5-day holding period means:
- Frequent drawdowns
- High psychological cost
- Risk of extended losing streaks (5+ consecutive losses have ~8-10% probability per any starting point)
- Capital efficiency is poor: you need large position counts to smooth the variance

For the social features specifically, p_dump is 0.52-0.56 -- barely above coin-flip probability. The improvement over random is 2-6 percentage points, which is real in expectation but essentially undetectable in any reasonable sample of live trades (you'd need 1000+ trades to confirm with statistical confidence).

---

## 6. SHARD COVERAGE AND ROBUSTNESS

### 6.1 All Features Show shard_cov = 4

Every row in the aggregated file shows `shard_coverage = 4`, meaning all 4 shards produced results for every feature-horizon combination. This is expected given the design: the script shards by symbol (round-robin modulo), not by time period. Each shard processes a different subset of ~30 symbols but the same time range.

### 6.2 Cross-Shard Variation

Examining the top features across shards:

**realized_vol_24, h=120:**
| Shard | Edge (bps) | IC | n_obs |
|---|---|---|---|
| 0 | 205.0 | -0.117 | 695K |
| 1 | 190.5 | -0.129 | 709K |
| 2 | 241.7 | -0.128 | 861K |
| 3 | 159.9 | -0.127 | 687K |

Range: 159.9 - 241.7 bps (60% variation). Shard 2 has notably higher n_obs, suggesting it received more data-rich symbols. The cross-shard variation of ~50% is high enough to question the precision of the edge estimate.

**vol_attention_coupling, h=120:**
| Shard | Edge (bps) | IC | n_obs |
|---|---|---|---|
| 0 | 224.6 | +0.068 | 254K |
| 1 | 240.6 | +0.085 | 259K |
| 2 | 222.2 | +0.068 | 328K |
| 3 | 195.3 | +0.059 | 240K |

Range: 195.3 - 240.6 bps (23% variation). More consistent, but IC varies from 0.059 to 0.085.

### 6.3 Adequacy of 4-Shard Coverage

**VERDICT: 4 shards is insufficient for robustness testing.** The shards are split by symbol, not by time. This means:
- All shards see the same market regimes simultaneously
- Cross-shard correlation is high (same bull/bear periods)
- Shard agreement does NOT demonstrate temporal stability
- What would actually test robustness is temporal sharding (e.g., rolling walk-forward windows)

The shard design tests whether the signal works across different symbol subsets (good), but does not test whether it works across different time periods (essential). A signal that works in a bull market across all symbols is not robust -- it's regime-dependent across all symbols.

---

## 7. DATA QUALITY ASSESSMENT

### 7.1 Social Data

The `synthetic_data_audit.md` confirms that all synthetic data has been cleaned from the databases. Sources are:
- `telegram_historical`
- `news_historical`
- `reddit_historical`
- `equities_raw_mentions`
- `coingecko`
- `rss`

**Red flags:**
1. **No source quality breakdown.** We don't know the distribution of posts across sources. If 90% comes from one source (e.g., Telegram historical), the social features are dominated by one platform's dynamics.
2. **443K social posts** (as reported by the user) across 120 symbols over ~2 years. That is approximately 5 posts per symbol per day on average. For many symbols, this is extremely sparse -- likely concentrated on BTC, ETH, and a handful of large-caps, with most symbols having negligible social coverage.
3. **The `min_social_posts` threshold is only 30** (line 110 of the discovery script). A symbol with 30 posts over 2 years has 1 post per 24 days -- the social features for such symbols are essentially zero-filled, which means the social component adds noise, not signal.

### 7.2 OHLCV Data

Sources are `binance`, `ws`, `equities_yahoo`. The 128M bars figure (if accurate) covers multiple timeframes. For 1h timeframe over 2 years, 120 symbols would produce ~120 * 365 * 2 * 24 = ~2.1M bars, which is consistent.

**Survivorship bias concern:** The script selects the top 120 symbols by social post count (line 276: `ORDER BY COALESCE(s.post_count, 0) DESC`). This creates survivorship bias: symbols that attracted social attention over the period are preferentially selected. Symbols that crashed and lost social attention would have fewer posts and might be excluded. This biases the sample toward successful or at least surviving tokens, potentially inflating the apparent predictability of social features.

### 7.3 Data Period

The default `--start-date` is 2024-01-01 (line 103). This covers approximately 2 years of data. The crypto market during this period included:
- 2024 Q1: Strong rally (BTC from ~42K to ~73K)
- 2024 Q2-Q3: Range-bound/correction (~55K-70K)
- 2024 Q4: Post-election rally (~70K to ~100K)
- 2025 Q1-Q2: Continued bull market dynamics

This is predominantly a bull market sample. The absence of a bear market period (like 2022 Q2-Q4) means any discovered signals may not survive regime change.

---

## 8. GPT-5.3 PF ESTIMATE EVALUATION

### 8.1 The Claim: PF 0.95 - 1.1

The user reports that GPT estimated a Profit Factor of 0.95-1.1 based on this research.

### 8.2 Assessment

This estimate appears reasonable but is likely optimistic, for the following reasons:

**Arguments supporting PF near 1.0:**
- The raw edges of 100-200 bps at 5-day horizons are real after costs (~170-190 bps net of 23 bps costs)
- Cross-sectional factor strategies in crypto can achieve gross Sharpe ratios of 0.5-1.0
- p(dump) of 0.58-0.63 implies the average winner is larger than the average loser (positive skew)

**Arguments for PF < 1.0:**
1. **No out-of-sample validation.** In-sample edges degrade 40-70% out of sample. A 200 bps in-sample edge might be 60-120 bps out of sample.
2. **The best features are volatility, not social.** A PF estimate based on "social tone engine" is misleading if the edge comes from shorting high-vol assets.
3. **Implementation costs not modeled.** Cross-sectional strategies require simultaneous positions, rebalancing costs, margin costs for shorting, and funding rate exposure. None of these are included.
4. **5-day holding period capital efficiency.** With p(dump) = 0.60 and a 5-day hold, you need significant capital reserves. The PF on invested capital (not notional) is lower.
5. **Regime sensitivity.** The 2024-2025 period has specific volatility characteristics. PF in a different regime could be well below 1.0.

**Realistic PF estimate after accounting for validation degradation and implementation friction: 0.85 - 1.0.** This is unprofitable to breakeven.

---

## 9. CRITICAL FINDINGS SUMMARY

### 9.1 Fatal Issues

1. **Zero out-of-sample validation.** The entire output is in-sample results. Per CLAUDE.md Section 4, this research cannot be reported as findings. Walk-forward validation is "mandatory" and "no in-sample results reported as findings."

2. **No multiple testing correction.** 343 hypothesis tests with no DSR, Bonferroni, or FDR correction. The required DSR > 0 threshold (Section 4.3) has not been computed.

3. **The strongest signals are not social features.** The top two features (`realized_vol_24`, `range_pct`) are pure price features with zero social data input. They would rank identically in a study with no social data at all.

4. **Feature redundancy inflation.** Algebraically redundant pairs (reversion/trend pressure, diversity/concentration) double-count discoveries.

### 9.2 Significant Issues

5. **No temporal robustness testing.** Symbol sharding demonstrates cross-symbol consistency but says nothing about temporal stability. The strategy may be regime-dependent.

6. **Survivorship bias in symbol selection.** Selecting by social post count biases toward surviving/popular tokens.

7. **Predominantly bull-market data.** 2024-2025 is not representative of full market cycles.

8. **Sparse social data for most symbols.** With min_social_posts = 30 over 2 years, many symbols have near-zero social feature values.

9. **No daily PF computation.** CLAUDE.md requires average daily PF as the success metric. This was not computed.

### 9.3 Methodological Strengths

10. The cross-sectional IC framework is methodologically appropriate for factor discovery.

11. The delay-aware target construction (30-minute execution delay, line 112) is good practice.

12. The sharded execution design enables parallelism and provides a partial robustness check.

13. The synthetic data cleanup was thorough and well-documented.

14. The feature engineering includes some genuinely novel social-price interaction terms.

---

## 10. VERDICT

### Is this research worth continuing?

**NO -- not in its current form. YES -- if the methodology is rebuilt from scratch to comply with MCAS validation standards.**

The pipeline produced in-sample results that look superficially interesting (100-220 bps edges, ICs of 0.05-0.13) but that fail every validation standard in CLAUDE.md Section 4. The strongest signals come from standard volatility factors, not from social data. The research as it stands proves that:

1. High-volatility crypto assets tend to underperform over 5-day horizons (known)
2. Social mention volume is weakly correlated with subsequent volatility (known)
3. Some social-price interaction features have weak but nonzero cross-sectional IC (interesting but unvalidated)

### What would be needed to make this worth continuing:

1. **Implement walk-forward validation** with minimum 5 purged rolling folds and 1-day embargo per MCAS Section 4.1. This is the single most important missing piece.

2. **Reserve a holdout set** (final 15-20% of data) that is never seen during feature screening or parameter tuning.

3. **Apply DSR correction** for the 343 (or ~200 unique, excluding redundant pairs) tests conducted.

4. **Remove redundant feature pairs** (reversion/trend pressure, diversity/concentration) before counting as independent discoveries.

5. **Separate the OHLCV-only features from the social features.** Run the discovery pipeline once with only OHLCV features and once with only social features (or social + the OHLCV features as controls). Determine how much incremental IC the social data provides above the OHLCV baseline.

6. **Include a proper cost model** in the evaluation pipeline, not as a post-hoc adjustment. Filter out feature-horizon combinations that are cost-negative before scoring.

7. **Add temporal regime analysis.** Split the data into regimes (bull/bear/range) and report feature stability across regimes.

8. **Extend the data period** to include at least one bear market if available (2022 data).

### Bottom Line

The research infrastructure (data pipeline, feature engineering framework, sharded execution) has value and can be reused. The research output (reported edges, feature rankings) has no evidential value because it is entirely in-sample. If someone told you "I tested 343 strategies on historical data and 341 of them made money," the correct response is not excitement -- it is suspicion. The absence of out-of-sample validation means the signal-to-noise ratio of the findings is unknown and potentially zero.

The GPT-5.3 PF estimate of 0.95-1.1 is plausible but should be interpreted as "might break even, might lose money" rather than "has a tradeable edge." A PF of 1.0 is the boundary of profitability, not a target.

---

*Report generated by Claude Opus 4.6 audit session. Per CLAUDE.md Section 0.2 Rule 5: "Honest reporting always. The most valuable output is sometimes 'there is no predictable structure in this data that survives validation.' That finding is worth as much as finding an edge, because it tells us to stop looking here and look somewhere else."*
