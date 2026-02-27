
# U1 Ground Truth Audit — WS1 Missed Trades Autopsy

Generated: 2026-02-27 18:41 UTC

Period: 2025-12-28 to 2026-02-27 (61 days)

Total opportunities: 454 (430 non-excluded, 24 excluded)


## 1. Executive Summary

**Precision** (at p95=10.1% threshold): **3.5%** (15 TP / 430 signaled)

**Recall** (CAUGHT_EARLY + CAUGHT_CONCURRENT only): **92.3%** (446 caught / 483 total pumps)

**Dominant failure mode**: NO_MOVE (171 opps, 40%)

**Social lead** (median, whale-excluded): 132.0h before signal


**Verdict**: U1 v3.0 achieves 3% precision at the data-derived p95 pump threshold of 10.1%. 171 of 430 non-excluded signals showed no meaningful move. Recall is 92% — 37 pumps had no U1 signal within ±24h.


## 2. Independence Diagnostics

**Top 10 symbols by opp count:**

| Symbol | Count | % |

|--------|-------|---|

| WUSDT | 2 | 0.5% |

| BNTUSDT | 2 | 0.5% |

| PENGUUSDT | 1 | 0.2% |

| HUMAUSDT | 1 | 0.2% |

| HIVEUSDT | 1 | 0.2% |

| RIFUSDT | 1 | 0.2% |

| XPLUSDT | 1 | 0.2% |

| GPSUSDT | 1 | 0.2% |

| APTUSDT | 1 | 0.2% |

| UNIUSDT | 1 | 0.2% |



**Cluster days** (>15 signals in 24h): 3

  - 2025-12-28: 337 signals

  - 2025-12-29: 48 signals

  - 2025-12-30: 27 signals


**Effective sample size**: 430 unique (symbol, week) combinations

**Cluster-adjusted precision**: 16.7% (18 non-cluster opps)


## 3. Calibration Results

24h close-to-close max return percentiles (U1 volume band 75K-3M only, 353 symbols):

  - **p50**: 1.70%

  - **p75**: 3.80%

  - **p90**: 6.97%

  - **p95**: 10.12%

  - **p99**: 21.86%


Wick-based diagnostic: p50=6.46%, p95=22.84%

Headline threshold (p95): **10.12%**


Per-2-week p95 stability:

  - 2025-W00: 7.73%

  - 2025-W52: 9.86%

  - 2026-W00: 11.14%

  - 2026-W02: 9.34%

  - 2026-W04: 9.27%

  - 2026-W06: 11.58%

  - 2026-W08: 9.76%


## 4. Contamination Audit

Total excluded: 24 opps across 24 symbols

| Reason | Symbols | Opp Count |

|--------|---------|-----------|

| fiat_pegged | AEURUSDT, EURIUSDT, EURUSDT | 3 |

| gold_backed | PAXGUSDT | 1 |

| non_crypto | AVGO, META, NVDA, QQQ, XLK | 5 |

| stablecoin | BFUSDUSDT, FDUSDUSDT, FRAXUSDT, RLUSDUSDT, TUSDUSDT... | 12 |

| stablecoin_price | 0GUSDT, LAZIOUSDT | 2 |

| wrapped | WBTCUSDT | 1 |


## 5. Multi-threshold Confusion Matrix

TP/FP = U1 signals whose MFE_24h did/didn't reach threshold. FN = Phase 3 pumps at that threshold with no timely U1 signal (MISSED). Recall uses Phase 3 CAUGHT_EARLY + CAUGHT_CONCURRENT as TP (consistent with headline).


| Threshold | Signaled TP | Signaled FP | Caught (E+C) | Missed (FN) | Precision | Recall |

|-----------|-------------|-------------|--------------|-------------|-----------|--------|

| 3% | 84 | 346 | 7674 | 508 | 19.5% | 93.8% |

| 5% | 40 | 390 | 1965 | 103 | 9.3% | 95.0% |

| 10% | 15 | 415 | 446 | 37 | 3.5% | 92.3% |

| 20% | 5 | 425 | 120 | 16 | 1.2% | 88.2% |

| 50% | 1 | 429 | 22 | 1 | 0.2% | 95.7% |


## 6. Outcome Distribution

| Outcome | Count | % |

|---------|-------|---|

| TP_STRONG | 57 | 13.3% |

| TP_MODERATE | 111 | 25.8% |

| TP_WEAK | 177 | 41.2% |

| MARGINAL | 44 | 10.2% |

| FALSE_POSITIVE | 13 | 3.0% |

| ADVERSE | 28 | 6.5% |


## 7. Failure Mode Distribution

| Failure Mode | Count | % | Example Symbols |

|-------------|-------|---|-----------------|

| CORRECT_SIGNAL | 15 | 3.5% | AGLDUSDT, ATUSDT, BIGTIMEUSDT |

| BAD_EXIT_PARAMS | 0 | 0.0% |  |

| LATE_ENTRY | 142 | 33.0% | 1000CATUSDT, 2ZUSDT, A2ZUSDT |

| ALREADY_PUMPED | 13 | 3.0% | ACTUSDT, BANKUSDT, BREVUSDT |

| DEAD_CAT_BOUNCE | 3 | 0.7% | DCRUSDT, FIDAUSDT, FUSDT |

| SLOW_BLEED | 67 | 15.6% | 1000CHEEMSUSDT, 1INCHUSDT, ACXUSDT |

| NO_MOVE | 171 | 39.8% | 1000SATSUSDT, 1MBABYDOGEUSDT, AAVEUSDT |

| WRONG_DIRECTION | 19 | 4.4% | ACAUSDT, BIFIUSDT, CITYUSDT |


## 8. Root Cause Distribution

| Root Cause | Count | % |

|-----------|-------|---|

| CORRECT_SIGNAL | 168 | 39.1% |

| WEAK_SIGNAL | 177 | 41.2% |

| NOISE_TRIGGER | 56 | 13.0% |

| COMPRESSION_ARTIFACT | 0 | 0.0% |

| LOW_VOL_Z | 0 | 0.0% |

| REGIME_MISMATCH | 0 | 0.0% |

| GENUINE_FP | 3 | 0.7% |


## 9. Trigger Path × Outcome

| Trigger Path | TP_STRONG | TP_MODERATE | TP_WEAK | MARGINAL | FALSE_POSITIVE | ADVERSE | Total |

|---|---|---|---|---|---|---|---|

| score_based | 57 | 111 | 177 | 44 | 13 | 28 | 430 |


## 10. Signal State Statistics

**Ignition Score**: N=430, mean=0.439, median=0.432, std=0.032, p25=0.414, p75=0.457

**Vol Z-Score**: N=430, mean=3.732, median=3.800, std=0.460, p25=3.500, p75=4.100

**Per-Coin Score**: N=430, mean=0.517, median=0.508, std=0.037, p25=0.487, p75=0.537

**Universe Temp**: N=430, mean=0.000, median=0.000, std=0.000, p25=0.000, p75=0.000

**R-Value %**: N=430, mean=0.762, median=0.500, std=0.605, p25=0.500, p75=0.726

**Setup Score (confidence)**: N=430, mean=0.435, median=0.439, std=0.052, p25=0.405, p75=0.470


**Trigger path distribution:**

  - score_based: 430 (100.0%)


**Confirmations met:**

  - 0/3: 428 opps

  - 1/3: 2 opps


## 11. Stop Calibration Heatmap

Hit rate = TP reached before stop, on TP_MODERATE+ opps.



| Stop Floor | Range Mult | N | Hit Rate | Avg PnL % |

|-----------|-----------|---|----------|-----------|

| 0.5% | 12× | 345 | 68.7% | 4.65% |

| 0.5% | 3× | 345 | 98.6% | 1.98% |

| 0.5% | 5× | 345 | 95.4% | 3.05% |

| 0.5% | 8× | 345 | 88.7% | 4.34% |

| 1.0% | 12× | 345 | 38.0% | 4.32% |

| 1.0% | 3× | 345 | 98.6% | 3.11% |

| 1.0% | 5× | 345 | 86.1% | 4.40% |

| 1.0% | 8× | 345 | 63.2% | 4.99% |

| 2.0% | 12× | 345 | 11.3% | 2.06% |

| 2.0% | 3× | 345 | 80.3% | 4.53% |

| 2.0% | 5× | 345 | 49.3% | 4.34% |

| 2.0% | 8× | 345 | 27.0% | 3.66% |

| 3.0% | 12× | 345 | 3.5% | 0.48% |

| 3.0% | 3× | 345 | 56.2% | 4.20% |

| 3.0% | 5× | 345 | 30.4% | 3.57% |

| 3.0% | 8× | 345 | 11.9% | 1.95% |

| 5.0% | 12× | 345 | 1.4% | 1.15% |

| 5.0% | 3× | 345 | 30.4% | 3.63% |

| 5.0% | 5× | 345 | 9.9% | 1.95% |

| 5.0% | 8× | 345 | 3.8% | 1.56% |


## 12. Social Correlation Matrix

**⚠️ BACKFILL ARTIFACT WARNING**: Social data was backfilled historically. `ingested_ts` for backfilled posts is weeks/months after `post_ts`. Any 'social precedes price' finding proves temporal correlation in historical data, NOT that a live system would have seen the signal in time. Findings are provisional pending live validation.**


| Social Timing | TP_STRONG | TP_MODERATE | TP_WEAK | MARGINAL | FALSE_POSITIVE | ADVERSE | Total | Precision |

|---|---|---|---|---|---|---|---|---|

| social_precedes_6h+ | 24 | 43 | 64 | 10 | 6 | 12 | 159 | 4.4% |

| social_precedes_1_6h | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0% |

| social_concurrent | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0% |

| social_follows | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0% |

| no_social | 33 | 68 | 113 | 34 | 7 | 16 | 271 | 3.0% |


## 13. Social Lead Cross-correlation

Top 40 events by MFE, lag -24h to +24h (1h steps).

Events with social data: 35/40

Social lead distribution for top performers: median=130.1h, mean=119.9h, min=34.8h, max=167.1h


## 14. Mention Runway Distributions

**social_lead_hours**: N=159, median=132.0h, mean=119.9h

Bimodality coefficient: BC=0.658 (suggests bimodality)

  skewness=-0.748, kurtosis=2.369


TP vs FP social lead comparison:

  KS test: stat=0.251, p=0.2764

  Mann-Whitney U: stat=782, p=0.0549

  TP median lead: 141.8h (N=67)

  FP median lead: 117.0h (N=18)


**mention_runway_hours**: N=153, median=276.0h, mean=253.7h


Histogram (social_lead_hours):

  [10h, 26h): 5

  [26h, 42h): 7

  [42h, 57h): 9

  [57h, 73h): 10

  [73h, 89h): 9

  [89h, 105h): 13

  [105h, 121h): 13

  [121h, 136h): 17

  [136h, 152h): 22

  [152h, 168h): 54


## 15. Content Category Analysis

| Content Category | TP_STRONG | TP_MODERATE | TP_WEAK | MARGINAL | FALSE_POSITIVE | ADVERSE | Total |

|---|---|---|---|---|---|---|---|

| community_hype | 4 | 3 | 7 | 0 | 0 | 1 | 15 |

| news | 6 | 6 | 15 | 3 | 4 | 1 | 35 |

| other | 6 | 25 | 27 | 5 | 0 | 6 | 69 |


**Whale alert polarity** (32 opps with whale data): 116 bullish (from-exchange), 225 bearish (to-exchange)


## 16. BTC Regime Context

BTC 7d return at signal time:

  TP: median=0.08%, mean=0.21% (N=168)

  FP: median=0.09%, mean=0.49% (N=41)


**Regime filter assessment**: 2 opps during BTC 7d < -5%, 1 TP (50% precision)

  vs overall precision 3.5% — marginal benefit


## 17. Top 20 Best Catches

| # | Symbol | MFE_24h | MFE_7d | Trigger | Ign Score | Social Lead | Content |

|---|--------|---------|--------|---------|-----------|-------------|---------|

| 1 | BROCCOLI714USDT | -0.8% | 430.0% | score_based | 0.407 | — | None |

| 2 | A2ZUSDT | -0.6% | 155.1% | score_based | 0.492 | — | None |

| 3 | 币安人生USDT | 15.1% | 108.4% | score_based | 0.406 | — | None |

| 4 | RENDERUSDT | 0.8% | 97.8% | score_based | 0.451 | — | None |

| 5 | ZBTUSDT | 82.8% | 82.8% | score_based | 0.469 | — | news |

| 6 | PEPEUSDT | 3.4% | 54.6% | score_based | 0.416 | 167h | news |

| 7 | ESPUSDT | 2.0% | 52.4% | score_based | 0.404 | 104h | other |

| 8 | TLMUSDT | 0.6% | 50.6% | score_based | 0.412 | — | None |

| 9 | SENTUSDT | 12.7% | 50.2% | score_based | 0.411 | 35h | other |

| 10 | STXUSDT | 0.2% | 49.9% | score_based | 0.481 | — | None |

| 11 | AIXBTUSDT | 8.0% | 49.2% | score_based | 0.418 | — | None |

| 12 | SAPIENUSDT | 2.0% | 42.8% | score_based | 0.444 | — | None |

| 13 | TUTUSDT | -0.7% | 40.8% | score_based | 0.492 | — | None |

| 14 | MUBARAKUSDT | 3.8% | 35.2% | score_based | 0.406 | — | None |

| 15 | FTTUSDT | 3.0% | 35.2% | score_based | 0.425 | — | None |

| 16 | AMPUSDT | 0.3% | 34.4% | score_based | 0.51 | — | None |

| 17 | BONKUSDT | 2.2% | 34.1% | score_based | 0.491 | 166h | community_hype |

| 18 | XPLUSDT | 8.0% | 33.0% | score_based | 0.428 | 145h | news |

| 19 | MEMEUSDT | 0.0% | 33.0% | score_based | 0.452 | 130h | None |

| 20 | CVXUSDT | 1.3% | 32.6% | score_based | 0.401 | — | None |


## 18. Top 20 Worst False Positives

| # | Symbol | MAE_7d | MFE_24h | Root Cause | Ign Score | Vol Z |

|---|--------|--------|---------|------------|-----------|-------|

| 1 | OGUSDT | -63.7% | 0.0% | NOISE_TRIGGER | 0.417 | 3.9 |

| 2 | ACTUSDT | -37.9% | -2.0% | NOISE_TRIGGER | 0.404 | 3.5 |

| 3 | BIFIUSDT | -37.0% | 0.7% | CORRELATION_CLUSTER | 0.46 | 3.1 |

| 4 | FLOWUSDT | -35.1% | 0.9% | CORRELATION_CLUSTER | 0.501 | 3.8 |

| 5 | FOGOUSDT | -30.0% | 0.1% | TIME_OF_DAY | 0.563 | 3.9 |

| 6 | ZKPUSDT | -22.2% | -1.5% | GENUINE_FP | 0.467 | 3.5 |

| 7 | STOUSDT | -21.0% | -1.2% | CORRELATION_CLUSTER | 0.461 | 3.9 |

| 8 | METISUSDT | -17.9% | 0.0% | CORRELATION_CLUSTER | 0.467 | 3.5 |

| 9 | ACAUSDT | -17.8% | 0.0% | GENUINE_FP | 0.576 | 4.3 |

| 10 | VANAUSDT | -16.1% | 1.2% | NOISE_TRIGGER | 0.428 | 3.0 |

| 11 | PORTALUSDT | -16.1% | 0.9% | NOISE_TRIGGER | 0.412 | 3.3 |

| 12 | RSRUSDT | -14.7% | 0.7% | NOISE_TRIGGER | 0.413 | 2.9 |

| 13 | CELOUSDT | -14.3% | -2.1% | CORRELATION_CLUSTER | 0.463 | 4.3 |

| 14 | SOLVUSDT | -14.0% | -0.1% | NOISE_TRIGGER | 0.408 | 4.3 |

| 15 | STRAXUSDT | -13.7% | -1.3% | CORRELATION_CLUSTER | 0.454 | 3.2 |

| 16 | IDEXUSDT | -13.7% | 1.6% | NOISE_TRIGGER | 0.419 | 3.3 |

| 17 | UNIUSDT | -13.3% | -2.1% | NOISE_TRIGGER | 0.402 | 4.1 |

| 18 | ZRXUSDT | -13.3% | -1.8% | NOISE_TRIGGER | 0.441 | 3.9 |

| 19 | GASUSDT | -12.9% | -3.5% | CORRELATION_CLUSTER | 0.461 | 4.2 |

| 20 | ROSEUSDT | -12.7% | -0.4% | NOISE_TRIGGER | 0.405 | 3.3 |


## 19. Top 20 Missed Pumps

| # | Symbol | Peak Return | Miss Reason | Social Pre-Pump | Social Lead |

|---|--------|-------------|-------------|-----------------|-------------|

| 1 | ATUSDT | 69.1% | not_in_volume_band_either | No | — |

| 2 | BANANAUSDT | 48.9% | in_universe_no_signal | No | — |

| 3 | BIFIUSDT | 44.5% | not_in_volume_band_either | No | — |

| 4 | DCRUSDT | 40.8% | in_universe_no_signal | No | — |

| 5 | ZBTUSDT | 34.7% | in_universe_no_signal | No | — |

| 6 | ACTUSDT | 29.1% | not_in_volume_band_either | No | — |

| 7 | TRUUSDT | 26.7% | reachable_during_pump | No | — |

| 8 | BIFIUSDT | 25.4% | in_universe_no_signal | No | — |

| 9 | DOLOUSDT | 25.1% | not_in_volume_band_either | No | — |

| 10 | ZKCUSDT | 24.8% | in_universe_no_signal | No | — |

| 11 | KAITOUSDT | 24.4% | in_universe_no_signal | No | — |

| 12 | NILUSDT | 24.1% | in_universe_no_signal | No | — |

| 13 | AVNTUSDT | 23.1% | not_in_volume_band_either | No | — |

| 14 | METISUSDT | 21.8% | in_universe_no_signal | No | — |

| 15 | YBUSDT | 21.7% | in_universe_no_signal | Yes | — |

| 16 | BANKUSDT | 21.6% | in_universe_no_signal | Yes | — |

| 17 | HMSTRUSDT | 19.4% | in_universe_no_signal | No | — |

| 18 | DOLOUSDT | 19.2% | not_in_volume_band_either | No | — |

| 19 | LAYERUSDT | 18.9% | not_in_volume_band_either | No | — |

| 20 | RADUSDT | 18.8% | in_universe_no_signal | No | — |


## 20. Per-Opportunity Classification Table

| ID | Symbol | Excl | Outcome | Failure Mode | Root Cause | MFE_24h | MAE_24h | Social Lead | Content | Ign Score | Trigger | BTC_7d | Bars |

|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

| 59446 | PENGUUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 1.61% | -1.82% | 164h | community_hype | 0.506 | score_based | 0.17% | 168 |

| 59447 | HUMAUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 0.00% | -2.96% | — | None | 0.442 | score_based | 0.17% | 168 |

| 59448 | HIVEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.21% | -1.62% | — | None | 0.404 | score_based | 2.96% | 168 |

| 59449 | RIFUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.42% | -0.85% | 120h | None | 0.429 | score_based | 0.33% | 168 |

| 59450 | XPLUSDT |  | TP_STRONG | NO_MOVE | CORRECT_SIGNAL | 7.96% | -1.20% | 145h | news | 0.428 | score_based | 0.14% | 168 |

| 59451 | GPSUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.00% | -2.81% | 111h | news | 0.496 | score_based | 0.08% | 168 |

| 59452 | APTUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.53% | -2.18% | 137h | other | 0.412 | score_based | 0.08% | 168 |

| 59453 | UNIUSDT |  | ADVERSE | WRONG_DIRECTION | NOISE_TRIGGER | -2.08% | -7.56% | 164h | other | 0.402 | score_based | 0.20% | 168 |

| 59454 | AVNTUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.83% | -9.10% | 79h | other | 0.466 | score_based | 0.07% | 168 |

| 59455 | DYMUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 0.42% | -4.16% | — | None | 0.438 | score_based | 0.17% | 168 |

| 59456 | STRKUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.08% | -2.88% | 114h | news | 0.422 | score_based | -0.00% | 168 |

| 59457 | DYDXUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.79% | -2.02% | — | None | 0.406 | score_based | 0.09% | 168 |

| 59458 | IOSTUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.19% | -5.30% | — | None | 0.46 | score_based | 0.17% | 168 |

| 59459 | LTCUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.17% | -1.31% | 166h | other | 0.41 | score_based | -0.00% | 168 |

| 59460 | 2ZUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.89% | -3.42% | — | None | 0.435 | score_based | 0.17% | 168 |

| 59461 | MANAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.36% | -1.06% | 61h | None | 0.406 | score_based | -0.07% | 168 |

| 59462 | BELUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.08% | -3.22% | — | None | 0.48 | score_based | 0.17% | 168 |

| 59463 | USDEUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.02% | -0.02% | — | None | 0.422 | score_based | 0.04% | 168 |

| 59464 | SXTUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.00% | -3.16% | — | None | 0.439 | score_based | 0.17% | 168 |

| 59465 | THETAUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 2.94% | -1.84% | — | None | 0.436 | score_based | -0.07% | 168 |

| 59466 | HYPERUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.26% | -2.26% | 147h | news | 0.4 | score_based | 0.28% | 168 |

| 59467 | SANTOSUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.64% | -1.70% | — | None | 0.452 | score_based | 0.16% | 168 |

| 59468 | SYRUPUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.03% | -3.14% | — | None | 0.433 | score_based | 0.17% | 168 |

| 59469 | RENDERUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.77% | -0.54% | — | None | 0.451 | score_based | 0.33% | 168 |

| 59470 | SLPUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.13% | -2.95% | — | None | 0.512 | score_based | 0.16% | 168 |

| 59471 | RSRUSDT |  | ADVERSE | WRONG_DIRECTION | NOISE_TRIGGER | 0.68% | -7.44% | — | None | 0.413 | score_based | -0.00% | 168 |

| 59472 | ZBTUSDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 82.83% | -5.09% | — | news | 0.469 | score_based | 0.08% | 168 |

| 59473 | ORDIUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 0.82% | -1.48% | — | None | 0.436 | score_based | 0.17% | 168 |

| 59474 | TRUMPUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.42% | -0.79% | 165h | other | 0.4 | score_based | -0.30% | 168 |

| 59475 | SAGAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.72% | -4.41% | 15h | other | 0.428 | score_based | 0.33% | 168 |

| 59476 | WCTUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.26% | -5.42% | — | None | 0.421 | score_based | 2.65% | 168 |

| 59477 | ZENUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.91% | -3.74% | 88h | None | 0.432 | score_based | -0.00% | 168 |

| 59478 | OGNUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.34% | -1.67% | — | None | 0.424 | score_based | 0.16% | 168 |

| 59479 | NEWTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 6.15% | -4.41% | — | None | 0.472 | score_based | 0.16% | 168 |

| 59480 | MITOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 4.58% | 0.14% | — | news | 0.43 | score_based | -0.00% | 168 |

| 59481 | PORTALUSDT |  | ADVERSE | SLOW_BLEED | NOISE_TRIGGER | 0.87% | -3.04% | — | None | 0.412 | score_based | -0.00% | 168 |

| 59482 | ONTUSDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 28.22% | 1.67% | — | None | 0.571 | score_based | 2.83% | 168 |

| 59483 | SAHARAUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.11% | -1.76% | — | None | 0.444 | score_based | 0.08% | 168 |

| 59484 | PEOPLEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.63% | -0.32% | — | other | 0.429 | score_based | 0.17% | 168 |

| 59485 | SHIBUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.30% | -1.36% | 168h | news | 0.414 | score_based | 0.08% | 168 |

| 59486 | STORJUSDT |  | TP_WEAK | ALREADY_PUMPED | WEAK_SIGNAL | 5.24% | -0.86% | — | None | 0.409 | score_based | 0.72% | 168 |

| 59487 | WINUSDT |  | ADVERSE | NO_MOVE | CORRELATION_CLUSTER | 0.67% | -1.74% | — | None | 0.514 | score_based | 0.14% | 168 |

| 59488 | RVNUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 1.64% | -2.05% | — | None | 0.469 | score_based | 0.17% | 168 |

| 59489 | VELODROMEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.49% | -4.85% | — | None | 0.422 | score_based | 0.08% | 168 |

| 59490 | NEIROUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 3.21% | -1.99% | — | None | 0.424 | score_based | 0.09% | 168 |

| 59491 | NEARUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 1.13% | -4.41% | 153h | other | 0.421 | score_based | 0.20% | 168 |

| 59492 | XVSUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.00% | -0.23% | — | None | 0.416 | score_based | 0.08% | 168 |

| 59493 | NEOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.18% | -1.97% | 102h | None | 0.471 | score_based | 0.11% | 168 |

| 59494 | NEXOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.66% | -1.55% | 129h | None | 0.508 | score_based | -0.07% | 168 |

| 59495 | PYRUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.85% | -1.07% | — | None | 0.437 | score_based | -0.00% | 168 |

| 59496 | QNTUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 0.18% | -2.24% | 167h | None | 0.461 | score_based | 0.22% | 168 |

| 59497 | PYTHUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.48% | -2.64% | 131h | None | 0.406 | score_based | -0.07% | 168 |

| 59498 | USTCUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.30% | -1.95% | — | None | 0.407 | score_based | 0.08% | 168 |

| 59499 | RAYUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | -0.76% | -2.61% | 163h | None | 0.423 | score_based | -0.48% | 168 |

| 59500 | SSVUSDT |  | TP_STRONG | NO_MOVE | CORRECT_SIGNAL | 3.86% | -2.06% | — | None | 0.452 | score_based | 0.07% | 168 |

| 59501 | STEEMUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 2.68% | -2.96% | — | None | 0.426 | score_based | 0.08% | 168 |

| 59502 | TONUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 4.60% | -0.44% | 161h | other | 0.506 | score_based | 0.09% | 168 |

| 59504 | METISUSDT |  | ADVERSE | WRONG_DIRECTION | CORRELATION_CLUSTER | 0.00% | -12.50% | — | None | 0.467 | score_based | 0.08% | 168 |

| 59505 | MINAUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 1.10% | -2.92% | — | None | 0.411 | score_based | 0.07% | 168 |

| 59506 | MMTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 7.96% | 1.04% | — | None | 0.401 | score_based | 0.17% | 168 |

| 59508 | MOVEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.91% | -0.84% | 127h | community_hype | 0.443 | score_based | 0.07% | 168 |

| 59509 | MORPHOUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 2.21% | -1.19% | — | None | 0.402 | score_based | 0.09% | 168 |

| 59510 | NOMUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.69% | -5.45% | — | None | 0.433 | score_based | 1.89% | 168 |

| 59511 | OGUSDT |  | ADVERSE | NO_MOVE | NOISE_TRIGGER | 0.02% | -2.40% | 164h | other | 0.417 | score_based | -0.18% | 168 |

| 59512 | MOVRUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | -0.27% | -2.88% | — | None | 0.407 | score_based | 0.16% | 168 |

| 59513 | NILUSDT |  | TP_WEAK | ALREADY_PUMPED | WEAK_SIGNAL | 4.02% | -2.81% | — | None | 0.482 | score_based | 0.17% | 168 |

| 59514 | MUBARAKUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 3.76% | 0.25% | — | None | 0.406 | score_based | 0.27% | 168 |

| 59515 | OPUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.44% | -2.05% | 167h | news | 0.405 | score_based | 0.33% | 168 |

| 59516 | PEPEUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 3.37% | -0.72% | 167h | news | 0.416 | score_based | -0.00% | 168 |

| 59517 | ONEUSDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 17.53% | 1.15% | 155h | community_hype | 0.444 | score_based | -0.18% | 168 |

| 59518 | PLUMEUSDT |  | ADVERSE | SLOW_BLEED | CORRELATION_CLUSTER | -0.82% | -6.10% | 10h | community_hype | 0.46 | score_based | 0.16% | 168 |

| 59521 | PNUTUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.64% | -1.53% | 87h | None | 0.447 | score_based | 0.16% | 168 |

| 59522 | POLUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 1.67% | -1.02% | 120h | None | 0.42 | score_based | 0.09% | 168 |

| 59523 | PROMUSDT |  | TP_WEAK | ALREADY_PUMPED | WEAK_SIGNAL | 1.98% | -0.41% | — | None | 0.426 | score_based | 0.08% | 168 |

| 59524 | PUMPUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.58% | -1.07% | 149h | news | 0.551 | score_based | 0.17% | 168 |

| 59525 | PROVEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.21% | -0.95% | — | None | 0.409 | score_based | 0.17% | 168 |

| 59526 | SANDUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.54% | -1.71% | 47h | news | 0.402 | score_based | 0.09% | 168 |

| 59527 | PUNDIXUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | -0.25% | -3.52% | — | None | 0.414 | score_based | 0.08% | 168 |

| 59528 | RESOLVUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.40% | -7.33% | — | other | 0.417 | score_based | 0.28% | 168 |

| 59529 | REZUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.63% | -3.81% | — | None | 0.453 | score_based | 0.09% | 168 |

| 59530 | ROSEUSDT |  | ADVERSE | SLOW_BLEED | NOISE_TRIGGER | -0.43% | -4.21% | — | None | 0.405 | score_based | 0.17% | 168 |

| 59531 | RUNEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.74% | 0.18% | 157h | None | 0.449 | score_based | -0.46% | 168 |

| 59532 | SAPIENUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 1.98% | -2.93% | — | None | 0.444 | score_based | 0.17% | 168 |

| 59533 | SEIUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.43% | -2.57% | 141h | other | 0.415 | score_based | 0.08% | 168 |

| 59534 | SKLUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.58% | -3.17% | — | None | 0.409 | score_based | 0.17% | 168 |

| 59535 | SKYUSDT |  | MARGINAL | NO_MOVE | CORRELATION_CLUSTER | 2.73% | -0.74% | 133h | news | 0.488 | score_based | 0.09% | 168 |

| 59536 | SOLUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.66% | -0.85% | 168h | other | 0.474 | score_based | 0.08% | 168 |

| 59537 | SOLVUSDT |  | ADVERSE | NO_MOVE | NOISE_TRIGGER | -0.14% | -1.57% | 145h | None | 0.408 | score_based | 0.28% | 168 |

| 59538 | SNXUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.45% | -3.58% | 122h | None | 0.415 | score_based | 0.16% | 168 |

| 59539 | SOMIUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 5.40% | -0.87% | — | None | 0.412 | score_based | 0.17% | 168 |

| 59540 | THEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.71% | -0.88% | 165h | other | 0.456 | score_based | 0.09% | 168 |

| 59541 | SYNUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.77% | -3.09% | 129h | None | 0.443 | score_based | 0.18% | 168 |

| 59542 | TSTUSDT |  | TP_STRONG | NO_MOVE | CORRECT_SIGNAL | 5.63% | -3.15% | — | None | 0.452 | score_based | 0.22% | 168 |

| 59543 | WALUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.57% | -5.30% | 84h | None | 0.426 | score_based | 2.65% | 168 |

| 59544 | XRPUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.05% | -1.04% | 166h | news | 0.41 | score_based | 0.07% | 168 |

| 59545 | XTZUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 3.63% | 0.70% | — | None | 0.413 | score_based | 0.17% | 168 |

| 59546 | XUSDUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.03% | 0.00% | 143h | other | 0.447 | score_based | 0.08% | 168 |

| 59547 | BABYUSDT |  | FALSE_POSITIVE | NO_MOVE | NOISE_TRIGGER | 0.12% | -2.37% | 42h | news | 0.411 | score_based | 0.09% | 168 |

| 59548 | AIUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.04% | -2.33% | 168h | other | 0.407 | score_based | -0.22% | 168 |

| 59549 | AUDIOUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.00% | -1.73% | — | None | 0.437 | score_based | 0.81% | 168 |

| 59550 | AVAUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.18% | -2.47% | — | None | 0.449 | score_based | 0.28% | 168 |

| 59551 | BIFIUSDT |  | ADVERSE | WRONG_DIRECTION | CORRELATION_CLUSTER | 0.69% | -9.57% | 66h | None | 0.46 | score_based | -0.00% | 168 |

| 59552 | DENTUSDT |  | MARGINAL | NO_MOVE | CORRELATION_CLUSTER | 1.44% | -2.40% | — | None | 0.511 | score_based | 0.09% | 168 |

| 59553 | EDENUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 3.17% | -2.42% | — | None | 0.447 | score_based | 0.28% | 168 |

| 59554 | CUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 3.73% | -1.92% | — | None | 0.419 | score_based | -0.07% | 168 |

| 59555 | EDUUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.05% | -1.81% | — | None | 0.446 | score_based | 0.28% | 168 |

| 59556 | EGLDUSDT |  | TP_WEAK | WRONG_DIRECTION | WEAK_SIGNAL | 0.17% | -3.93% | — | other | 0.403 | score_based | -0.22% | 168 |

| 59557 | ENJUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.94% | -1.09% | — | None | 0.414 | score_based | -0.22% | 168 |

| 59558 | ERAUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.84% | -1.54% | 28h | other | 0.457 | score_based | -0.48% | 168 |

| 59559 | FUSDT |  | TP_WEAK | DEAD_CAT_BOUNCE | WEAK_SIGNAL | 4.55% | -2.14% | — | None | 0.429 | score_based | 0.08% | 168 |

| 59560 | GLMRUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.00% | -2.90% | — | None | 0.456 | score_based | 0.09% | 168 |

| 59561 | GASUSDT |  | ADVERSE | WRONG_DIRECTION | CORRELATION_CLUSTER | -3.54% | -11.13% | — | None | 0.461 | score_based | 0.08% | 168 |

| 59562 | HOTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.01% | -2.22% | 116h | None | 0.446 | score_based | 0.17% | 168 |

| 59563 | HOLOUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.00% | -7.10% | — | None | 0.458 | score_based | 0.09% | 168 |

| 59564 | HIGHUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.91% | -2.28% | 95h | community_hype | 0.447 | score_based | 0.22% | 168 |

| 59565 | KMNOUSDT |  | TP_MODERATE | CORRECT_SIGNAL | CORRECT_SIGNAL | 10.63% | -0.70% | 28h | other | 0.414 | score_based | -0.46% | 168 |

| 59566 | LAYERUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 1.58% | -3.33% | — | None | 0.411 | score_based | 0.16% | 168 |

| 59567 | METUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 4.44% | -0.83% | — | None | 0.436 | score_based | -0.00% | 168 |

| 59568 | OMUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.48% | -1.08% | — | None | 0.475 | score_based | 0.16% | 168 |

| 59569 | ONDOUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.71% | -1.06% | 132h | news | 0.409 | score_based | 0.09% | 168 |

| 59570 | OPENUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 4.12% | -3.38% | 146h | None | 0.408 | score_based | -0.00% | 168 |

| 59571 | ORCAUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 2.05% | -0.62% | — | None | 0.41 | score_based | 0.07% | 168 |

| 59572 | PAXGUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.18% | -1.50% | 88h | news | 0.494 | score_based | 0.08% | 168 |

| 59573 | PARTIUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.36% | -2.55% | — | None | 0.471 | score_based | 0.17% | 168 |

| 59574 | SPKUSDT |  | TP_MODERATE | CORRECT_SIGNAL | CORRECT_SIGNAL | 11.67% | -2.97% | — | None | 0.481 | score_based | 0.17% | 168 |

| 59575 | PENDLEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.36% | -1.89% | 164h | other | 0.474 | score_based | 0.08% | 168 |

| 59576 | RADUSDT |  | TP_MODERATE | ALREADY_PUMPED | CORRECT_SIGNAL | 1.64% | -0.33% | — | None | 0.442 | score_based | 2.81% | 168 |

| 59577 | STOUSDT |  | ADVERSE | WRONG_DIRECTION | CORRELATION_CLUSTER | -1.20% | -14.24% | — | None | 0.461 | score_based | 0.08% | 168 |

| 59578 | SUIUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 3.43% | -0.94% | 159h | other | 0.409 | score_based | 0.16% | 168 |

| 59579 | STXUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.19% | -2.29% | — | None | 0.481 | score_based | 0.41% | 168 |

| 59580 | SUNUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 0.36% | -1.85% | — | None | 0.425 | score_based | 0.08% | 168 |

| 59582 | SUPERUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.05% | -0.89% | — | community_hype | 0.422 | score_based | -0.00% | 168 |

| 59583 | SUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 4.45% | -1.53% | — | None | 0.414 | score_based | -0.00% | 168 |

| 59584 | SXPUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.98% | -1.96% | — | None | 0.425 | score_based | 0.17% | 168 |

| 59585 | TAOUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.93% | -2.17% | 121h | other | 0.44 | score_based | 0.08% | 168 |

| 59586 | TIAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.21% | -2.93% | 108h | other | 0.463 | score_based | -0.00% | 168 |

| 59587 | TNSRUSDT |  | FALSE_POSITIVE | SLOW_BLEED | NOISE_TRIGGER | 0.73% | -3.53% | — | None | 0.413 | score_based | 0.25% | 168 |

| 59588 | TREEUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.37% | -5.94% | 93h | None | 0.445 | score_based | 0.07% | 168 |

| 59589 | TRBUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 2.87% | -1.09% | — | None | 0.425 | score_based | -0.08% | 168 |

| 59590 | TRXUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.81% | 0.00% | 162h | other | 0.467 | score_based | 0.07% | 168 |

| 59591 | TWTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.69% | -1.27% | 61h | news | 0.448 | score_based | 0.18% | 168 |

| 59592 | TURTLEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.69% | -3.21% | — | None | 0.437 | score_based | 0.22% | 168 |

| 59593 | UMAUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | -0.14% | -2.86% | — | None | 0.402 | score_based | 0.08% | 168 |

| 59612 | USD1USDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.07% | 0.00% | — | None | 0.421 | score_based | 1.23% | 168 |

| 59616 | USDCUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.02% | 0.00% | 168h | news | 0.417 | score_based | 7.80% | 168 |

| 59617 | VTHOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.70% | -0.51% | 92h | None | 0.489 | score_based | 0.17% | 168 |

| 59618 | USUALUSDT |  | TP_WEAK | WRONG_DIRECTION | WEAK_SIGNAL | 0.78% | -3.11% | — | None | 0.428 | score_based | 0.08% | 168 |

| 59619 | VETUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.06% | -2.21% | 75h | news | 0.53 | score_based | 0.18% | 168 |

| 59620 | VIRTUALUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.29% | -2.44% | 64h | None | 0.46 | score_based | 0.07% | 168 |

| 59621 | WLDUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.26% | -1.49% | 141h | news | 0.469 | score_based | 0.07% | 168 |

| 59622 | WBETHUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 1.50% | 0.28% | — | None | 0.478 | score_based | -0.18% | 168 |

| 59623 | WBTCUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 2.59% | -0.34% | 146h | other | 0.435 | score_based | 0.09% | 168 |

| 59624 | WLFIUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 3.33% | 0.64% | 165h | news | 0.46 | score_based | -0.46% | 168 |

| 59625 | WIFUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.58% | -2.52% | 137h | community_hype | 0.441 | score_based | 0.25% | 168 |

| 59626 | WOOUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 8.53% | -0.78% | 142h | other | 0.42 | score_based | 2.23% | 168 |

| 59627 | WUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 3.20% | -1.45% | — | None | 0.417 | score_based | -0.08% | 168 |

| 59628 | XLMUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 0.90% | -1.66% | 165h | other | 0.408 | score_based | 0.17% | 168 |

| 59629 | XVGUSDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 11.34% | -2.14% | — | community_hype | 0.456 | score_based | -0.00% | 168 |

| 59630 | YFIUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.03% | -1.08% | — | other | 0.441 | score_based | -0.35% | 168 |

| 59631 | YGGUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.58% | -2.88% | 26h | community_hype | 0.448 | score_based | 0.07% | 168 |

| 59632 | YBUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 9.55% | -0.62% | 62h | None | 0.484 | score_based | -0.07% | 168 |

| 59633 | ZRXUSDT |  | ADVERSE | ALREADY_PUMPED | NOISE_TRIGGER | -1.78% | -13.19% | — | None | 0.441 | score_based | 1.57% | 168 |

| 59634 | ZROUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.86% | -2.67% | 50h | None | 0.454 | score_based | 2.23% | 168 |

| 59635 | ZKUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.41% | -6.69% | 145h | other | 0.451 | score_based | -0.00% | 168 |

| 59636 | GIGGLEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 6.40% | -0.04% | — | None | 0.449 | score_based | 0.08% | 168 |

| 59637 | ALLOUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.09% | -3.86% | — | None | 0.423 | score_based | 0.08% | 168 |

| 59638 | GMTUSDT |  | TP_MODERATE | CORRECT_SIGNAL | CORRECT_SIGNAL | 17.81% | -2.84% | 150h | other | 0.402 | score_based | 0.08% | 168 |

| 59639 | LRCUSDT |  | FALSE_POSITIVE | SLOW_BLEED | CORRELATION_CLUSTER | -0.18% | -3.31% | 56h | None | 0.469 | score_based | 0.07% | 168 |

| 59640 | MBLUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.60% | -0.86% | — | None | 0.427 | score_based | 0.07% | 168 |

| 59641 | MEMEUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.00% | -4.70% | 130h | None | 0.452 | score_based | 2.23% | 168 |

| 59642 | TURBOUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.80% | -1.52% | 92h | None | 0.478 | score_based | 0.08% | 168 |

| 59643 | MEUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 5.30% | 0.15% | 86h | None | 0.417 | score_based | -0.18% | 168 |

| 59644 | MIRAUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 5.55% | -0.65% | — | None | 0.492 | score_based | 0.09% | 168 |

| 59645 | BIGTIMEUSDT |  | TP_MODERATE | CORRECT_SIGNAL | CORRECT_SIGNAL | 10.30% | -1.79% | — | None | 0.428 | score_based | 0.08% | 168 |

| 59646 | ZKCUSDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 29.33% | -4.03% | 78h | None | 0.43 | score_based | -0.00% | 168 |

| 59647 | ZECUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 5.58% | -0.48% | 163h | other | 0.426 | score_based | -0.00% | 168 |

| 59648 | ZILUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.61% | -3.24% | 72h | None | 0.446 | score_based | 0.09% | 168 |

| 59649 | COMPUSDT |  | FALSE_POSITIVE | SLOW_BLEED | NOISE_TRIGGER | 0.30% | -3.87% | 88h | news | 0.41 | score_based | 0.09% | 168 |

| 59650 | LPTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.43% | -2.31% | — | None | 0.47 | score_based | 0.09% | 168 |

| 59651 | LUNAUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | -0.10% | -4.44% | 142h | news | 0.435 | score_based | 0.07% | 168 |

| 59652 | LUNCUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | -0.68% | -2.43% | 142h | None | 0.489 | score_based | -0.00% | 168 |

| 59653 | MAGICUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | 0.00% | -3.27% | — | None | 0.43 | score_based | 0.27% | 168 |

| 59654 | PHAUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.80% | -2.68% | — | None | 0.408 | score_based | 0.22% | 168 |

| 59655 | MANTAUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | -0.77% | -4.25% | — | None | 0.431 | score_based | 0.08% | 168 |

| 59656 | PHBUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.94% | -2.33% | — | None | 0.404 | score_based | 0.11% | 168 |

| 59657 | VANRYUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.30% | -2.60% | — | None | 0.475 | score_based | -0.00% | 168 |

| 59658 | RONINUSDT |  | TP_WEAK | WRONG_DIRECTION | WEAK_SIGNAL | 0.52% | -4.72% | — | None | 0.483 | score_based | 0.09% | 168 |

| 59659 | SUSHIUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.60% | -0.27% | — | None | 0.408 | score_based | 0.07% | 168 |

| 59660 | VANAUSDT |  | ADVERSE | NO_MOVE | NOISE_TRIGGER | 1.17% | -1.78% | 150h | None | 0.428 | score_based | -0.07% | 168 |

| 59661 | 0GUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 14.14% | -4.20% | — | None | 0.46 | score_based | 0.25% | 168 |

| 59662 | 1000CHEEMSUSDT |  | ADVERSE | SLOW_BLEED | CORRELATION_CLUSTER | -0.20% | -3.75% | — | None | 0.505 | score_based | 0.07% | 168 |

| 59663 | 1000SATSUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.70% | -2.53% | — | None | 0.493 | score_based | 0.09% | 168 |

| 59664 | 1INCHUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.00% | -4.17% | — | None | 0.434 | score_based | 0.07% | 168 |

| 59665 | 1MBABYDOGEUSDT |  | TP_STRONG | NO_MOVE | CORRECT_SIGNAL | 9.20% | -0.26% | — | None | 0.523 | score_based | 0.08% | 168 |

| 59666 | AAVEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.95% | -2.75% | 161h | other | 0.426 | score_based | 0.11% | 168 |

| 59667 | ACEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -1.04% | -4.50% | — | None | 0.429 | score_based | -0.00% | 168 |

| 59668 | ACHUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.49% | -2.98% | 148h | None | 0.438 | score_based | 0.17% | 168 |

| 59669 | ADXUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | -0.46% | -4.47% | — | None | 0.438 | score_based | 2.41% | 168 |

| 59670 | ACTUSDT |  | ADVERSE | ALREADY_PUMPED | NOISE_TRIGGER | -2.00% | -7.35% | 36h | other | 0.404 | score_based | 0.07% | 168 |

| 59671 | ADAUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.94% | -0.96% | 166h | other | 0.466 | score_based | -0.47% | 168 |

| 59672 | AGLDUSDT |  | TP_MODERATE | CORRECT_SIGNAL | CORRECT_SIGNAL | 10.47% | -3.10% | — | None | 0.403 | score_based | -0.47% | 168 |

| 59673 | AIXBTUSDT |  | TP_STRONG | NO_MOVE | CORRECT_SIGNAL | 8.03% | 0.00% | — | None | 0.418 | score_based | -0.46% | 168 |

| 59674 | ALGOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.91% | -1.74% | 167h | other | 0.459 | score_based | 0.09% | 168 |

| 59675 | ALTUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.58% | -2.76% | — | None | 0.422 | score_based | 0.18% | 168 |

| 59676 | APEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.09% | -2.22% | 94h | news | 0.419 | score_based | 0.16% | 168 |

| 59677 | API3USDT |  | MARGINAL | SLOW_BLEED | CORRELATION_CLUSTER | -0.17% | -3.58% | — | None | 0.489 | score_based | 0.07% | 168 |

| 59678 | ARBUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.02% | -1.93% | 163h | other | 0.476 | score_based | 0.09% | 168 |

| 59679 | ARKMUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 1.04% | -3.65% | — | None | 0.426 | score_based | 0.07% | 168 |

| 59680 | ARPAUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.24% | -4.57% | — | None | 0.454 | score_based | 2.81% | 168 |

| 59681 | ARUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 0.00% | -4.47% | 52h | None | 0.426 | score_based | 0.16% | 168 |

| 59682 | ASTERUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.24% | -0.98% | 147h | other | 0.418 | score_based | 0.07% | 168 |

| 59683 | ASTRUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.50% | -1.21% | — | None | 0.494 | score_based | -0.35% | 168 |

| 59684 | ATMUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | 0.32% | -3.38% | 148h | other | 0.414 | score_based | 0.11% | 168 |

| 59685 | ATOMUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.62% | -1.72% | 167h | other | 0.419 | score_based | -0.00% | 168 |

| 59686 | ATUSDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 20.78% | -3.62% | 150h | None | 0.413 | score_based | 0.09% | 168 |

| 59687 | AUCTIONUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.39% | -1.38% | — | None | 0.425 | score_based | 0.08% | 168 |

| 59688 | AUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.19% | -2.08% | — | None | 0.458 | score_based | 0.17% | 168 |

| 59689 | AWEUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 0.66% | -1.94% | — | None | 0.435 | score_based | 0.07% | 168 |

| 59690 | AVAXUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.67% | -1.57% | 158h | other | 0.45 | score_based | 0.25% | 168 |

| 59691 | AXLUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.55% | -1.10% | 164h | None | 0.447 | score_based | 0.17% | 168 |

| 59692 | AXSUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 3.06% | -2.82% | — | None | 0.444 | score_based | -0.08% | 168 |

| 59693 | BANANAS31USDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.39% | -1.25% | — | None | 0.421 | score_based | 0.17% | 168 |

| 59694 | BANANAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.74% | -0.89% | — | None | 0.413 | score_based | -0.37% | 168 |

| 59695 | BARDUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.15% | -0.88% | — | other | 0.415 | score_based | 0.09% | 168 |

| 59696 | BATUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 0.90% | -1.94% | — | None | 0.406 | score_based | 0.08% | 168 |

| 59697 | BARUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.79% | 0.00% | — | None | 0.419 | score_based | -0.00% | 168 |

| 59698 | BBUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | -0.54% | -5.95% | — | None | 0.411 | score_based | 0.50% | 168 |

| 59699 | BCHUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.58% | -0.39% | 160h | other | 0.444 | score_based | -0.35% | 168 |

| 59700 | BEAMXUSDT |  | MARGINAL | NO_MOVE | CORRELATION_CLUSTER | 0.14% | -2.37% | — | None | 0.455 | score_based | 0.17% | 168 |

| 59701 | BFUSDUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.01% | -0.02% | — | None | 0.474 | score_based | 0.08% | 168 |

| 59702 | BERAUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.46% | -6.31% | 158h | None | 0.409 | score_based | 0.50% | 168 |

| 59703 | BIOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.07% | -3.29% | 98h | news | 0.474 | score_based | 0.16% | 168 |

| 59704 | BNBUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.22% | -0.31% | 166h | other | 0.421 | score_based | -0.48% | 168 |

| 59705 | BONKUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.23% | -2.35% | 166h | community_hype | 0.491 | score_based | 0.07% | 168 |

| 59706 | BOMEUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 1.86% | -2.37% | — | None | 0.477 | score_based | 0.09% | 168 |

| 59707 | BNSOLUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.69% | -0.81% | — | None | 0.472 | score_based | 0.07% | 168 |

| 59708 | BROCCOLI714USDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | -0.79% | -4.50% | — | None | 0.407 | score_based | 0.07% | 168 |

| 59709 | BTCUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.87% | -0.47% | 167h | other | 0.432 | score_based | 0.27% | 168 |

| 59710 | C98USDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.87% | -2.16% | — | None | 0.521 | score_based | 0.07% | 168 |

| 59711 | CAKEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.59% | -0.32% | — | community_hype | 0.407 | score_based | 0.07% | 168 |

| 59713 | CELOUSDT |  | ADVERSE | SLOW_BLEED | CORRELATION_CLUSTER | -2.12% | -6.82% | — | None | 0.463 | score_based | 0.16% | 168 |

| 59714 | CFXUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.23% | -1.78% | 132h | community_hype | 0.435 | score_based | 0.09% | 168 |

| 59715 | CHRUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | -0.48% | -4.52% | — | None | 0.401 | score_based | 0.08% | 168 |

| 59716 | CHZUSDT |  | TP_STRONG | NO_MOVE | CORRECT_SIGNAL | 5.12% | -0.85% | — | None | 0.432 | score_based | -0.07% | 168 |

| 59717 | COTIUSDT |  | ADVERSE | SLOW_BLEED | NOISE_TRIGGER | 0.27% | -3.81% | 134h | other | 0.429 | score_based | 0.09% | 168 |

| 59718 | COWUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.64% | -1.47% | — | None | 0.487 | score_based | 0.07% | 168 |

| 59719 | CVXUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 1.30% | -2.59% | — | None | 0.401 | score_based | 0.08% | 168 |

| 59720 | CTKUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.08% | -2.01% | — | None | 0.427 | score_based | 0.22% | 168 |

| 59721 | CRVUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.77% | -3.24% | 134h | news | 0.402 | score_based | -0.00% | 168 |

| 59722 | CYBERUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 3.36% | -1.26% | — | None | 0.402 | score_based | -0.08% | 168 |

| 59723 | DASHUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 1.95% | -2.13% | 73h | other | 0.407 | score_based | -0.00% | 168 |

| 59724 | DCRUSDT |  | TP_WEAK | DEAD_CAT_BOUNCE | WEAK_SIGNAL | 4.01% | -3.38% | 67h | other | 0.443 | score_based | 0.13% | 168 |

| 59725 | DEXEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.68% | -2.93% | — | None | 0.4 | score_based | 0.22% | 168 |

| 59726 | DOGEUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.76% | -0.69% | 165h | other | 0.435 | score_based | 0.09% | 168 |

| 59727 | DGBUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | -0.50% | -2.48% | — | None | 0.44 | score_based | 0.04% | 168 |

| 59728 | DOLOUSDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 25.11% | 1.05% | 56h | other | 0.452 | score_based | -0.00% | 168 |

| 59729 | DOTUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.01% | -2.08% | 162h | other | 0.474 | score_based | 0.07% | 168 |

| 59730 | DUSKUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.44% | -3.29% | 13h | other | 0.423 | score_based | -0.00% | 168 |

| 59731 | DUSDT |  | ADVERSE | SLOW_BLEED | NOISE_TRIGGER | -0.13% | -5.83% | — | None | 0.406 | score_based | 2.65% | 168 |

| 59733 | ENAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.87% | -2.78% | 165h | other | 0.421 | score_based | 0.08% | 168 |

| 59734 | EIGENUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.53% | -4.26% | 159h | news | 0.408 | score_based | -0.34% | 168 |

| 59735 | ENSOUSDT |  | TP_MODERATE | CORRECT_SIGNAL | CORRECT_SIGNAL | 15.40% | 0.44% | — | None | 0.409 | score_based | -0.00% | 168 |

| 59736 | ENSUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.21% | -1.24% | 158h | None | 0.426 | score_based | -0.34% | 168 |

| 59737 | ETCUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.47% | -1.63% | 119h | news | 0.427 | score_based | 0.16% | 168 |

| 59738 | ETHFIUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.66% | -2.10% | 161h | None | 0.479 | score_based | 0.07% | 168 |

| 59739 | ETHUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 1.93% | -0.39% | 167h | other | 0.404 | score_based | -0.22% | 168 |

| 59740 | EULUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.10% | -3.06% | — | None | 0.47 | score_based | 2.65% | 168 |

| 59741 | EURIUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | -0.04% | -0.36% | — | None | 0.419 | score_based | 2.16% | 168 |

| 59742 | FDUSDUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.02% | -0.05% | 146h | news | 0.422 | score_based | 2.41% | 168 |

| 59743 | EURUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.04% | -0.21% | 166h | other | 0.421 | score_based | -0.35% | 168 |

| 59744 | FETUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 3.87% | -0.51% | — | community_hype | 0.435 | score_based | 0.09% | 168 |

| 59745 | FFUSDT |  | ADVERSE | SLOW_BLEED | NOISE_TRIGGER | 0.97% | -3.36% | 90h | None | 0.417 | score_based | 0.07% | 168 |

| 59746 | FIDAUSDT |  | TP_WEAK | DEAD_CAT_BOUNCE | WEAK_SIGNAL | 6.13% | -1.42% | — | news | 0.451 | score_based | 0.16% | 168 |

| 59747 | FILUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.94% | -1.72% | 88h | other | 0.403 | score_based | 0.08% | 168 |

| 59748 | FLOKIUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.45% | -1.42% | 129h | None | 0.452 | score_based | 0.07% | 168 |

| 59749 | FLOWUSDT |  | ADVERSE | WRONG_DIRECTION | CORRELATION_CLUSTER | 0.88% | -15.79% | 23h | news | 0.501 | score_based | 0.25% | 168 |

| 59750 | FLUXUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.95% | -1.71% | — | None | 0.4 | score_based | 0.16% | 168 |

| 59751 | FORMUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.33% | -5.66% | — | None | 0.437 | score_based | -0.11% | 168 |

| 59752 | FTTUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 3.04% | -0.65% | — | None | 0.425 | score_based | 0.25% | 168 |

| 59753 | FUNUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.23% | -2.46% | 152h | other | 0.408 | score_based | 0.22% | 168 |

| 59754 | GALAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.96% | -2.08% | 119h | None | 0.427 | score_based | 0.17% | 168 |

| 59755 | GNSUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.36% | -1.43% | — | None | 0.44 | score_based | 0.08% | 168 |

| 59756 | GLMUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.24% | -2.58% | — | None | 0.481 | score_based | 0.18% | 168 |

| 59757 | GUNUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | -0.09% | -3.87% | — | None | 0.408 | score_based | 0.17% | 168 |

| 59758 | GRTUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.21% | -3.74% | 54h | None | 0.412 | score_based | 0.22% | 168 |

| 59759 | HBARUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.13% | -1.84% | 164h | other | 0.406 | score_based | 0.08% | 168 |

| 59760 | HEMIUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.64% | -3.82% | — | None | 0.407 | score_based | -0.00% | 168 |

| 59761 | HMSTRUSDT |  | MARGINAL | SLOW_BLEED | CORRELATION_CLUSTER | 0.00% | -5.23% | — | None | 0.459 | score_based | 0.09% | 168 |

| 59762 | HOMEUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 1.13% | -8.43% | 155h | other | 0.493 | score_based | 0.16% | 168 |

| 59763 | ICPUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 0.58% | -2.04% | 163h | news | 0.405 | score_based | -0.00% | 168 |

| 59764 | IDEXUSDT |  | ADVERSE | SLOW_BLEED | NOISE_TRIGGER | 1.60% | -3.30% | — | None | 0.419 | score_based | 0.28% | 168 |

| 59765 | IMXUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.24% | -2.89% | — | None | 0.487 | score_based | 0.16% | 168 |

| 59766 | INITUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 4.53% | -1.16% | — | None | 0.454 | score_based | 0.38% | 168 |

| 59767 | IOTAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.34% | -2.40% | 44h | other | 0.44 | score_based | -0.00% | 168 |

| 59768 | INJUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.97% | -3.88% | 112h | None | 0.436 | score_based | 0.27% | 168 |

| 59769 | IOTXUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 0.67% | -2.14% | — | None | 0.445 | score_based | 0.09% | 168 |

| 59771 | IOUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -1.27% | -5.06% | 160h | None | 0.42 | score_based | 2.16% | 168 |

| 59772 | JASMYUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.82% | -3.43% | — | None | 0.416 | score_based | 0.07% | 168 |

| 59773 | JSTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.85% | -0.34% | — | None | 0.4 | score_based | -0.07% | 168 |

| 59774 | JTOUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 1.52% | -1.52% | 64h | None | 0.488 | score_based | -0.00% | 168 |

| 59776 | JUPUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.62% | -0.75% | 109h | None | 0.41 | score_based | -0.63% | 168 |

| 59777 | JUVUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.81% | -1.08% | — | None | 0.435 | score_based | 0.07% | 168 |

| 59778 | KAIAUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.52% | -2.43% | — | None | 0.419 | score_based | 0.22% | 168 |

| 59779 | KAITOUSDT |  | TP_WEAK | ALREADY_PUMPED | WEAK_SIGNAL | 1.82% | -2.04% | 32h | other | 0.466 | score_based | 0.20% | 168 |

| 59780 | KERNELUSDT |  | ADVERSE | SLOW_BLEED | NOISE_TRIGGER | -0.66% | -4.09% | — | None | 0.415 | score_based | -0.47% | 168 |

| 59781 | KITEUSDT |  | FALSE_POSITIVE | SLOW_BLEED | NOISE_TRIGGER | 0.42% | -3.92% | — | None | 0.418 | score_based | 0.07% | 168 |

| 59782 | LAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.35% | -1.57% | — | None | 0.418 | score_based | 0.33% | 168 |

| 59783 | KSMUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.28% | -2.35% | — | None | 0.477 | score_based | -0.11% | 168 |

| 59784 | LDOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 6.01% | -0.41% | 123h | other | 0.418 | score_based | 0.08% | 168 |

| 59785 | LINEAUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.32% | -2.78% | — | None | 0.568 | score_based | 0.08% | 168 |

| 59786 | NOTUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.29% | -2.57% | 164h | other | 0.472 | score_based | 0.07% | 168 |

| 59787 | LINKUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.46% | -0.57% | 163h | other | 0.402 | score_based | -0.34% | 168 |

| 59788 | NTRNUSDT |  | TP_WEAK | ALREADY_PUMPED | WEAK_SIGNAL | 4.39% | -7.43% | — | None | 0.418 | score_based | 0.09% | 168 |

| 59789 | TUTUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | -0.71% | -5.65% | — | None | 0.492 | score_based | 2.65% | 168 |

| 59790 | RPLUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.49% | -2.99% | — | None | 0.444 | score_based | 0.07% | 168 |

| 59791 | RAREUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.46% | -2.29% | — | None | 0.4 | score_based | 0.16% | 168 |

| 59792 | CKBUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.94% | -2.53% | — | None | 0.51 | score_based | -0.47% | 168 |

| 59793 | TUSDT |  | MARGINAL | ALREADY_PUMPED | NOISE_TRIGGER | 0.55% | -2.65% | — | None | 0.428 | score_based | -0.30% | 168 |

| 59794 | WANUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | 0.27% | -4.13% | — | None | 0.422 | score_based | 2.41% | 168 |

| 59795 | NXPCUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 0.23% | -2.08% | — | None | 0.421 | score_based | 0.25% | 168 |

| 59796 | POLYXUSDT |  | TP_STRONG | NO_MOVE | CORRECT_SIGNAL | 3.94% | -1.38% | — | None | 0.458 | score_based | -0.07% | 168 |

| 59797 | 1000CATUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.86% | -2.86% | — | None | 0.426 | score_based | 0.09% | 168 |

| 59798 | A2ZUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | -0.56% | -4.87% | — | None | 0.492 | score_based | 0.08% | 168 |

| 59800 | ACMUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.17% | 0.00% | — | None | 0.421 | score_based | 0.88% | 168 |

| 59801 | ACXUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.38% | -4.61% | — | None | 0.426 | score_based | 0.25% | 168 |

| 59802 | AEURUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.41% | -0.16% | — | None | 0.403 | score_based | 0.16% | 168 |

| 59803 | ALCXUSDT |  | MARGINAL | NO_MOVE | CORRELATION_CLUSTER | 1.03% | -2.06% | — | None | 0.451 | score_based | 0.08% | 168 |

| 59804 | ALICEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.40% | -1.35% | — | None | 0.445 | score_based | -0.00% | 168 |

| 59805 | ANKRUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.46% | -3.65% | — | None | 0.484 | score_based | 2.65% | 168 |

| 59806 | ALPINEUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 2.50% | -1.17% | — | None | 0.405 | score_based | -0.00% | 168 |

| 59807 | ANIMEUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 4.23% | -5.25% | — | None | 0.472 | score_based | 0.09% | 168 |

| 59808 | ARDRUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.48% | -0.96% | — | None | 0.46 | score_based | -0.22% | 168 |

| 59809 | ARKUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.63% | -1.55% | 149h | news | 0.438 | score_based | -0.07% | 168 |

| 59810 | ATAUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | -0.56% | -3.33% | 130h | None | 0.4 | score_based | 0.08% | 168 |

| 59811 | BICOUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.69% | -3.67% | — | None | 0.412 | score_based | 0.11% | 168 |

| 59813 | BLURUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.07% | -4.86% | — | None | 0.482 | score_based | 2.23% | 168 |

| 59814 | BMTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 4.13% | -2.07% | — | None | 0.454 | score_based | -0.00% | 168 |

| 59815 | BNTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.20% | -0.65% | 47h | news | 0.408 | score_based | 0.22% | 168 |

| 59816 | CATIUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 0.99% | -0.17% | — | None | 0.439 | score_based | 0.72% | 168 |

| 59817 | CELRUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.14% | -1.92% | — | None | 0.447 | score_based | 0.08% | 168 |

| 59818 | CGPTUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.26% | -3.24% | — | None | 0.405 | score_based | 0.16% | 168 |

| 59819 | CETUSUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.41% | -4.49% | 98h | None | 0.437 | score_based | 0.07% | 168 |

| 59820 | COOKIEUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | -0.24% | -8.39% | — | None | 0.423 | score_based | 2.41% | 168 |

| 59821 | CTSIUSDT |  | MARGINAL | WRONG_DIRECTION | NOISE_TRIGGER | -0.29% | -5.83% | — | None | 0.407 | score_based | 0.16% | 168 |

| 59822 | COSUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 1.95% | -1.38% | — | None | 0.403 | score_based | -0.07% | 168 |

| 59823 | CVCUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.67% | -1.34% | — | None | 0.463 | score_based | 0.08% | 168 |

| 59824 | DEGOUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.23% | -1.64% | — | None | 0.409 | score_based | 0.07% | 168 |

| 59825 | DIAUSDT |  | MARGINAL | WRONG_DIRECTION | CORRELATION_CLUSTER | 0.42% | -3.99% | — | other | 0.488 | score_based | -0.00% | 168 |

| 59826 | DODOUSDT |  | TP_WEAK | WRONG_DIRECTION | WEAK_SIGNAL | -0.52% | -4.15% | — | None | 0.447 | score_based | 0.08% | 168 |

| 59827 | DOGSUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.24% | -3.57% | — | None | 0.454 | score_based | 0.16% | 168 |

| 59828 | EPICUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 3.61% | -0.28% | — | None | 0.493 | score_based | 0.16% | 168 |

| 59829 | FIOUSDT |  | MARGINAL | NO_MOVE | CORRELATION_CLUSTER | 0.08% | -2.27% | — | None | 0.456 | score_based | 0.07% | 168 |

| 59830 | FARMUSDT |  | MARGINAL | NO_MOVE | CORRELATION_CLUSTER | -0.05% | -1.63% | — | None | 0.45 | score_based | -0.00% | 168 |

| 59831 | FORTHUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.06% | -2.67% | — | None | 0.485 | score_based | 0.16% | 168 |

| 59832 | GMXUSDT |  | FALSE_POSITIVE | SLOW_BLEED | NOISE_TRIGGER | 0.12% | -3.30% | 151h | news | 0.414 | score_based | 0.25% | 168 |

| 59833 | GTCUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.48% | -2.22% | — | None | 0.432 | score_based | -0.00% | 168 |

| 59834 | GUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 6.11% | -0.90% | — | None | 0.419 | score_based | -0.00% | 168 |

| 59835 | GNOUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 4.43% | -0.18% | — | None | 0.417 | score_based | 0.07% | 168 |

| 59836 | HAEDALUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.00% | -4.74% | — | None | 0.453 | score_based | 0.28% | 168 |

| 59839 | HEIUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.67% | -1.58% | — | None | 0.446 | score_based | 0.25% | 168 |

| 59840 | HFTUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | 0.00% | -5.12% | 90h | None | 0.416 | score_based | 0.08% | 168 |

| 59841 | HOOKUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.77% | -3.61% | — | None | 0.42 | score_based | 0.28% | 168 |

| 59842 | ICXUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 3.62% | -2.17% | — | None | 0.445 | score_based | 0.72% | 168 |

| 59843 | IDUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 3.59% | -1.09% | 163h | other | 0.403 | score_based | 0.27% | 168 |

| 59844 | IQUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.19% | -1.67% | 143h | news | 0.418 | score_based | 0.09% | 168 |

| 59845 | ILVUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.71% | -0.85% | — | None | 0.453 | score_based | 0.16% | 168 |

| 59865 | JOEUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 3.49% | -1.58% | — | None | 0.407 | score_based | 0.27% | 168 |

| 59866 | KNCUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.17% | -1.53% | — | None | 0.429 | score_based | 0.25% | 168 |

| 59867 | LQTYUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 1.63% | -5.18% | — | None | 0.444 | score_based | -0.08% | 168 |

| 59868 | LISTAUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | 0.46% | -3.69% | — | None | 0.41 | score_based | 0.07% | 168 |

| 59869 | LSKUSDT |  | FALSE_POSITIVE | WRONG_DIRECTION | NOISE_TRIGGER | 0.49% | -7.32% | — | None | 0.407 | score_based | 0.09% | 168 |

| 59870 | LUMIAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.89% | -4.46% | — | None | 0.471 | score_based | -0.00% | 168 |

| 59871 | MAVUSDT |  | MARGINAL | NO_MOVE | CORRELATION_CLUSTER | 0.29% | -2.85% | — | None | 0.467 | score_based | 0.16% | 168 |

| 59872 | MBOXUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.65% | -1.63% | — | None | 0.439 | score_based | 0.22% | 168 |

| 59873 | MTLUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.82% | -3.57% | — | None | 0.401 | score_based | 0.08% | 168 |

| 59874 | MLNUSDT |  | FALSE_POSITIVE | SLOW_BLEED | NOISE_TRIGGER | -0.41% | -3.04% | — | None | 0.415 | score_based | 0.16% | 168 |

| 59875 | NFPUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.41% | -4.10% | — | None | 0.436 | score_based | 0.08% | 168 |

| 59876 | NMRUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.46% | -1.87% | — | None | 0.455 | score_based | 0.07% | 168 |

| 59877 | ONGUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 1.02% | -5.77% | — | None | 0.428 | score_based | -0.00% | 168 |

| 59878 | OSMOUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.78% | -1.17% | — | None | 0.403 | score_based | 0.24% | 168 |

| 59879 | OXTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 6.75% | -0.84% | — | None | 0.411 | score_based | 0.25% | 168 |

| 59880 | PIVXUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.00% | -1.46% | — | None | 0.468 | score_based | 1.39% | 168 |

| 59881 | PIXELUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.36% | -4.09% | — | None | 0.462 | score_based | 0.07% | 168 |

| 59882 | PONDUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 1.98% | -0.50% | — | None | 0.416 | score_based | 0.08% | 168 |

| 59883 | POWRUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 6.14% | 0.36% | — | None | 0.424 | score_based | -0.47% | 168 |

| 59884 | PORTOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.19% | -2.17% | — | None | 0.411 | score_based | 0.07% | 168 |

| 59885 | QKCUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 0.80% | -2.66% | — | None | 0.42 | score_based | 0.90% | 168 |

| 59886 | QIUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.33% | -2.61% | — | None | 0.405 | score_based | -0.22% | 168 |

| 59887 | PSGUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.09% | -1.21% | 91h | None | 0.41 | score_based | 0.11% | 168 |

| 59888 | QUICKUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.61% | -2.27% | — | None | 0.469 | score_based | 0.28% | 168 |

| 59889 | QTUMUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 1.24% | -1.86% | 89h | news | 0.423 | score_based | 0.07% | 168 |

| 59890 | RDNTUSDT |  | TP_STRONG | NO_MOVE | CORRECT_SIGNAL | 6.79% | 0.11% | — | None | 0.441 | score_based | 0.08% | 168 |

| 59891 | REQUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.48% | -1.55% | — | None | 0.458 | score_based | -0.46% | 168 |

| 59892 | RLCUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.36% | -0.76% | — | None | 0.437 | score_based | 0.16% | 168 |

| 59893 | SCRTUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 2.52% | -1.40% | — | None | 0.418 | score_based | -0.07% | 168 |

| 59894 | SCRUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.63% | -3.95% | 105h | None | 0.406 | score_based | 0.09% | 168 |

| 59895 | SCUSDT |  | MARGINAL | SLOW_BLEED | CORRELATION_CLUSTER | -1.43% | -4.80% | — | None | 0.516 | score_based | 0.08% | 168 |

| 59896 | SFPUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.32% | -1.78% | — | None | 0.418 | score_based | 0.09% | 168 |

| 59897 | SOPHUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 0.00% | -2.73% | — | None | 0.418 | score_based | 0.08% | 168 |

| 59898 | SPELLUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.82% | -1.80% | — | None | 0.466 | score_based | 0.20% | 168 |

| 59899 | SHELLUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 7.77% | -0.61% | — | None | 0.43 | score_based | 0.07% | 168 |

| 59900 | STRAXUSDT |  | ADVERSE | ALREADY_PUMPED | CORRELATION_CLUSTER | -1.30% | -9.45% | — | None | 0.454 | score_based | 0.51% | 168 |

| 59901 | SYSUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.05% | -3.63% | — | None | 0.439 | score_based | 0.09% | 168 |

| 59902 | TFUELUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.11% | -3.38% | — | None | 0.428 | score_based | 2.81% | 168 |

| 59903 | TOWNSUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.16% | -5.18% | — | None | 0.415 | score_based | 0.07% | 168 |

| 59904 | TKOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 2.99% | -1.56% | — | None | 0.45 | score_based | 0.08% | 168 |

| 59905 | TRUUSDT |  | MARGINAL | ALREADY_PUMPED | CORRELATION_CLUSTER | 0.00% | -4.72% | — | None | 0.454 | score_based | 0.08% | 168 |

| 59907 | TUSDUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | -0.01% | -0.09% | — | None | 0.427 | score_based | 0.20% | 168 |

| 59908 | USDPUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.11% | -0.01% | — | None | 0.412 | score_based | 0.72% | 168 |

| 59909 | UTKUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 0.94% | -3.53% | — | None | 0.421 | score_based | 0.08% | 168 |

| 59910 | WAXPUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.77% | -2.43% | — | None | 0.442 | score_based | 0.22% | 168 |

| 59911 | VICUSDT |  | TP_WEAK | LATE_ENTRY | WEAK_SIGNAL | 0.00% | -3.83% | — | None | 0.459 | score_based | 0.09% | 168 |

| 59912 | XAIUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | -0.62% | -4.06% | — | None | 0.425 | score_based | 0.22% | 168 |

| 59913 | XECUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | -0.09% | -2.22% | — | None | 0.495 | score_based | 0.08% | 168 |

| 59914 | XNOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | -0.28% | -2.54% | 34h | community_hype | 0.444 | score_based | -0.51% | 168 |

| 59915 | BANKUSDT |  | TP_WEAK | ALREADY_PUMPED | WEAK_SIGNAL | 5.09% | -3.02% | 40h | community_hype | 0.482 | score_based | 0.08% | 168 |

| 59916 | KAVAUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 3.28% | -0.26% | — | None | 0.434 | score_based | 0.51% | 168 |

| 59917 | AEVOUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.26% | -2.83% | — | None | 0.495 | score_based | 0.08% | 168 |

| 59918 | AMPUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.34% | -0.92% | — | None | 0.51 | score_based | 0.16% | 168 |

| 59919 | ASRUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | 1.05% | -3.86% | — | None | 0.413 | score_based | -0.00% | 168 |

| 59920 | BANDUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.50% | -2.40% | 72h | other | 0.453 | score_based | 0.07% | 168 |

| 59921 | CITYUSDT |  | FALSE_POSITIVE | WRONG_DIRECTION | NOISE_TRIGGER | -0.14% | -4.14% | 122h | news | 0.437 | score_based | 1.23% | 168 |

| 59922 | LAZIOUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 1.23% | -1.12% | — | None | 0.417 | score_based | -0.34% | 168 |

| 59923 | MDTUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 1.21% | -1.58% | — | None | 0.41 | score_based | 1.82% | 168 |

| 59924 | MASKUSDT |  | TP_MODERATE | LATE_ENTRY | CORRECT_SIGNAL | 2.50% | -0.50% | — | None | 0.452 | score_based | -0.46% | 168 |

| 59925 | REDUSDT |  | MARGINAL | SLOW_BLEED | NOISE_TRIGGER | 0.66% | -3.34% | 165h | None | 0.407 | score_based | 0.07% | 168 |

| 59926 | ACAUSDT |  | ADVERSE | WRONG_DIRECTION | GENUINE_FP | 0.00% | -3.33% | — | None | 0.576 | score_based | 2.90% | 168 |

| 59927 | SIGNUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.18% | -3.15% | — | None | 0.434 | score_based | 0.22% | 168 |

| 59928 | STGUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 1.95% | -1.30% | — | None | 0.48 | score_based | -0.00% | 168 |

| 59929 | TLMUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 0.57% | -2.32% | — | None | 0.412 | score_based | 0.16% | 168 |

| 59930 | CHESSUSDT |  | MARGINAL | NO_MOVE | NOISE_TRIGGER | 2.29% | -0.67% | — | None | 0.408 | score_based | -0.00% | 168 |

| 59932 | 币安人生USDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 15.08% | -12.53% | — | None | 0.406 | score_based | 3.62% | 168 |

| 59933 | FXSUSDT |  | FALSE_POSITIVE | SLOW_BLEED | NOISE_TRIGGER | 0.75% | -4.94% | — | None | 0.414 | score_based | 0.22% | 168 |

| 59934 | BREVUSDT |  | MARGINAL | ALREADY_PUMPED | GENUINE_FP | 2.64% | -4.05% | 110h | other | 0.465 | score_based | 0.71% | 168 |

| 59935 | ZKPUSDT |  | ADVERSE | WRONG_DIRECTION | GENUINE_FP | -1.49% | -10.49% | 158h | other | 0.467 | score_based | 1.12% | 168 |

| 59936 | FRAXUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 33.51% | -5.86% | 74h | news | 0.416 | score_based | 4.84% | 168 |

| 59937 | UUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.01% | -0.03% | — | None | 0.509 | score_based | 1.31% | 168 |

| 59938 | FOGOUSDT |  | ADVERSE | WRONG_DIRECTION | TIME_OF_DAY | 0.13% | -12.09% | 113h | other | 0.563 | score_based | 5.32% | 168 |

| 59939 | KGSTUSDT |  | FALSE_POSITIVE | NO_MOVE | NOISE_TRIGGER | 0.44% | -0.26% | 125h | None | 0.417 | score_based | -0.63% | 168 |

| 59940 | DATAUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 0.67% | -1.68% | — | None | 0.405 | score_based | 0.16% | 168 |

| 59941 | DFUSDT |  | TP_MODERATE | NO_MOVE | CORRECT_SIGNAL | 6.75% | 0.00% | — | None | 0.427 | score_based | -0.04% | 168 |

| 59942 | GHSTUSDT |  | ADVERSE | SLOW_BLEED | CORRELATION_CLUSTER | 0.55% | -3.85% | — | None | 0.452 | score_based | 0.07% | 168 |

| 59943 | NKNUSDT |  | TP_WEAK | SLOW_BLEED | WEAK_SIGNAL | 0.00% | -4.07% | — | None | 0.431 | score_based | 0.09% | 168 |

| 59944 | ZAMAUSDT |  | TP_WEAK | NO_MOVE | WEAK_SIGNAL | 5.87% | -9.20% | 168h | other | 0.409 | score_based | -12.32% | 168 |

| 59945 | NVDA | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | -0.92% | -1.26% | — | None | 0.498 | score_based | 0.13% | 64 |

| 59946 | QQQ | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | -0.12% | -0.89% | — | None | 0.54 | score_based | 0.65% | 64 |

| 59947 | AVGO | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 6.62% | -1.09% | — | None | 0.421 | score_based | 0.33% | 64 |

| 59948 | META | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | -0.12% | -0.15% | — | None | 0.422 | score_based | 2.59% | 80 |

| 59949 | RLUSDUSDT | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.04% | -0.02% | 167h | news | 0.421 | score_based | -1.67% | 168 |

| 59950 | SENTUSDT |  | TP_STRONG | CORRECT_SIGNAL | CORRECT_SIGNAL | 12.70% | -3.65% | 35h | other | 0.411 | score_based | -5.89% | 168 |

| 59951 | XLK | Y | EXCLUDED | EXCLUDED_SYMBOL | EXCLUDED_SYMBOL | 0.01% | -1.06% | — | None | 0.406 | score_based | 0.65% | 64 |

| 59952 | ESPUSDT |  | TP_STRONG | LATE_ENTRY | CORRECT_SIGNAL | 2.04% | -7.04% | 104h | other | 0.404 | score_based | 0.82% | 168 |

| 59985 | WUSDT |  | FALSE_POSITIVE | WRONG_DIRECTION | TIME_OF_DAY | 0.51% | -5.56% | — | None | 0.506 | score_based | 2.54% | 38 |

| 60596 | BNTUSDT |  | FALSE_POSITIVE | SLOW_BLEED | NOISE_TRIGGER | 0.51% | -5.71% | — | None | 0.405 | score_based | -0.15% | 16 |


## 21. SOP Change Proposals

1. **Increase MIN_STOP_PCT** from 0.5% to 1.0% with 8× range multiplier. (Stop heatmap shows 63% hit rate, 4.99% avg PnL on TP opps)

2. **Tighten entry filters**: 190 opps (44%) were NO_MOVE or WRONG_DIRECTION. Root causes: 56 noise trigger, 0 low vol_z, 0 compression artifact.

3. **Add pre-entry momentum filter**: 13 opps triggered on coins that had already moved >20% in 72h. Filter: reject if pre_entry_72h_return > 2× p95.

5. **Social-adjusted confidence**: Precision when social precedes = 4.4% (N=159) vs no social = 3.0% (N=271). Social presence is a confirming signal.

6. **Social → universe integration**: 465 missed pumps were in the volume band with no U1 signal. Of these, 5 (1%) had social mentions before the pump. This quantifies the gap a social promotion gate would fill.


## WS2 Handoff Notes

For WS2 (Sentiment-Price Correlation Study):

- Feature importance: ignition_score, vol_z, social_lead_hours, btc_7d_return are the primary candidates for multivariate analysis

- Recommended SPIKED threshold: 10.12% (p95 from this audit)

- Pattern clusters: Check content category × outcome cross-tab (Section 15) for category-specific prediction power

- Sample size caveat: 430 unique (symbol, week) — sufficient for ML
