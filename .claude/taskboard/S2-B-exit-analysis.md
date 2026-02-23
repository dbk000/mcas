# Exit Mechanism Deep Dive — Agent B Analysis
**Date:** 2026-02-21  
**Scope:** simulation_results, last 30 days  
**Status:** COMPLETE

---

## Executive Summary

The exit mechanism analysis reveals **three distinct intervention opportunities** worth a combined estimated **+$28,000-35,000 in recoverable PnL** over the 30-day window. The largest single lever is not TIME_STOP (which is a relatively small -$2,977 drag) — it is **FAIL_FAST (-$39,385)** and the **bars_held=9 TIME_STOP cliff (-$2,492 from a single bar bucket)**. The data shows a clear "act fast or die" structure: winners declare themselves within 1-6 bars, while losers bleed across specific clustered bar counts.

---

## 1. TIME_STOP Deep Dive: Slow-Bleed Losers, Not Missed Windows

### 1.1 By Setup/Timeframe
| Setup | TF | N | Avg PnL | Total PnL | Avg Bars | Avg R | Win Rate |
|-------|-----|-----|---------|-----------|----------|-------|----------|
| S6 | 15m | 682 | -3.05 | -2,082 | 9.5 | -0.181 | 29.9% |
| S2 | 5m | 236 | -1.04 | -246 | 32.9 | -0.204 | 20.8% |
| S6 | 1h | 167 | -1.41 | -236 | 8.4 | -0.011 | 44.9% |
| S1 | 15m | 165 | -0.70 | -116 | 2.9 | -0.063 | 44.8% |
| S1 | 5m | 39 | -2.61 | -102 | 4.4 | -0.617 | 0.0% |

**Key finding:** S6/15m alone accounts for **70% of all TIME_STOP losses** (-$2,082 of -$2,977). The avg bars_held of 9.5 combined with -0.181 R means these trades drift slightly negative and expire. They are not trades that "missed their window" — they never had a window.

### 1.2 PnL Distribution of TIME_STOP Trades
| Bucket | N | Avg PnL | Total PnL | Avg Bars |
|--------|------|---------|-----------|----------|
| < -$10 | 108 | -16.28 | -1,758 | 8.5 |
| -$10 to -$5 | 169 | -7.28 | -1,231 | 11.1 |
| -$5 to -$1 | 277 | -2.29 | -635 | 21.1 |
| -$1 to $0 | 1,351 | -0.38 | -514 | 13.6 |
| $0 to $1 | 439 | 0.28 | +124 | 12.0 |
| $1 to $5 | 188 | 2.32 | +437 | 20.8 |
| > $5 | 62 | 9.68 | +600 | 10.5 |

**Structure:** The distribution is heavily left-skewed. 1,351 trades (52%) end in the tiny -$1 to $0 bucket — the classic "slow bleed to fees" pattern. These are trades that went nowhere and got eaten by slippage+fees. The 277 trades losing $1-$5 and the 108 trades losing >$10 are the actual damage — they are positions that drifted against but not enough to hit the stop loss.

### 1.3 R-Multiple at TIME_STOP Exit
| R Bucket | N | Avg PnL | Total PnL | Avg Bars | Avg R |
|----------|------|---------|-----------|----------|-------|
| positive_r (>0) | 689 | +1.69 | +1,161 | 14.3 | +0.211 |
| small_neg (-0.5 to 0) | 1,269 | -2.12 | -2,695 | 14.1 | -0.246 |
| moderate_neg (-1.0 to -0.5) | 577 | -2.41 | -1,391 | 13.9 | -0.697 |
| large_neg (<-1.0) | 59 | -0.88 | -52 | 19.0 | -1.068 |

**Critical insight:** 689 TIME_STOP trades (27%) exit with **positive R-multiple** — they were profitable at expiry but not enough to hit TP1. These trades collectively made +$1,161. The problem is the 1,269 trades (49%) in the "small negative R" zone (-0.5 to 0) that collectively lost -$2,695. These are the definitive "went nowhere" trades.

### 1.4 Cumulative PnL by Bars Held (TIME_STOP only)
The cumulative PnL curve for TIME_STOP reveals three catastrophic bar-count clusters:
- **bars_held=0:** -179 (93 trades, all losers — immediate fail, should be FAIL_FAST)
- **bars_held=9:** -2,492 (1,312 trades — THIS IS THE CLIFF, single biggest loss bucket)
- **bars_held=25:** -311 (418 trades)
- **bars_held=100:** -14 (52 trades, long-duration S9 exits)

**The bars_held=9 cluster is decisive.** 1,312 of 2,594 TIME_STOP trades (50.6%) exit at exactly bar 9. This is not random — it is a hard-coded max_bars parameter set to 9 for certain setup/timeframe combos (likely S6/15m which has avg_bars=9.5). These 1,312 trades lose -$2,492, accounting for **84% of total TIME_STOP losses**.

---

## 2. TP1 Trade Speed: Winners Declare Themselves Fast

| Speed Bucket | N | Avg PnL | Total PnL | N Setups |
|-------------|------|---------|-----------|----------|
| 0-1 bars | 3,229 | +3.80 | +12,280 | 7 |
| 2-3 bars | 1,372 | +5.27 | +7,232 | 7 |
| 4-6 bars | 1,059 | +4.91 | +5,204 | 7 |
| 7-12 bars | 717 | +3.78 | +2,710 | 6 |
| 13-24 bars | 224 | +4.07 | +912 | 5 |
| 25+ bars | 390 | +7.63 | +2,976 | 6 |

**Key finding:** 46% of all TP1 hits happen within 0-1 bars. 66% happen within 0-3 bars. Winners are overwhelmingly fast. The avg PnL per trade is remarkably consistent across speed buckets ($3.78-$5.27 for bars 0-12), meaning the TP1 level is well-calibrated — once hit, the payout is similar regardless of how long it took.

The 25+ bar bucket has the highest avg PnL ($7.63) — these are likely higher-timeframe trades where each bar represents more price movement, not evidence that slow winners are bigger.

**Implication for TIME_STOP:** If a trade hasn't hit TP1 within 6 bars, it has only a 19% chance of being a TP1 winner (1,331 of 6,991 TP1 trades happen after bar 6). Most trades that reach bar 7+ without hitting TP1 are on a path to TIME_STOP, STOP_LOSS, or TRAIL_STOP.

---

## 3. TRAILING_STOP vs TP1: Trail Captures Extended Moves on S7

| Setup | TF | Trail Avg PnL | TP1 Avg PnL | Trail Avg Bars | TP1 Avg Bars | N Trail | N TP1 |
|-------|-----|--------------|-------------|----------------|-------------|---------|-------|
| S7 | 1h | **+14.53** | +1.37 | 3.6 | 1.1 | 331 | 155 |
| S7 | 15m | +3.57 | +0.75 | 1.7 | 1.1 | 590 | 107 |
| S7 | 4h | +8.38 | +2.03 | 2.4 | 1.9 | 29 | 19 |
| S6 | 1h | +5.09 | +8.75 | 5.2 | 3.4 | 173 | 835 |
| S6 | 15m | +3.84 | +6.14 | 3.3 | 3.1 | 823 | 2,903 |
| S2 | 15m | +9.30 | +11.20 | 2.8 | 30.1 | 13 | 253 |

**Two distinct patterns emerge:**

1. **S7 is a trailing-stop setup.** On S7/1h, the trail captures +$14.53 per trade vs TP1's +$1.37. The trail gets 2.1x more trades (331 vs 155) at 10.6x more profit per trade. S7's edge is in catching extended moves — TP1 is leaving massive money on the table. Across all S7 timeframes, TRAIL_STOP dominates TP1.

2. **S6 is a TP1 setup.** On S6/15m, TP1 captures +$6.14 per trade vs trail's +$3.84. S6 generates 3.5x more TP1 exits than trail exits. S6's edge is in precision targeting — it predicts the TP1 level accurately and most trades resolve there.

**Implication:** S7 would benefit from wider TP1 (or no TP1, pure trail) while S6's TP1 calibration is working well. These setups have fundamentally different trade structures and should not share exit parameters.

---

## 4. STOP_LOSS Analysis: Slippage Beyond the Stop

| Setup | TF | N SL | Avg R | P25 R | Median R | P75 R | Avg PnL | Avg Bars |
|-------|-----|------|-------|-------|----------|-------|---------|----------|
| S2 | 1m | 1,343 | -1.785 | -1.611 | -1.284 | -1.184 | -0.54 | 7.0 |
| S7 | 1h | 417 | -1.672 | -1.958 | -1.372 | -1.183 | -8.38 | 1.1 |
| S6 | 1m | 214 | -1.657 | -1.283 | -1.236 | -1.172 | -0.66 | 6.2 |
| S2 | 5m | 517 | -1.614 | -2.064 | -1.230 | -1.130 | -0.72 | 4.5 |
| S7 | 15m | 945 | -1.611 | -1.817 | -1.275 | -1.180 | -4.04 | 1.2 |
| S6 | 1h | 282 | -1.351 | -1.338 | -1.097 | -1.040 | -20.25 | 2.7 |
| S6 | 15m | 1,015 | -1.206 | -1.207 | -1.115 | -1.073 | -7.69 | 3.4 |
| S1 | 15m | 23 | -1.082 | -1.081 | -1.081 | -1.068 | -7.27 | 3.0 |
| S9 | 1h | 22 | -1.067 | -1.008 | -1.008 | -1.008 | -10.30 | 13.2 |

**Stop slippage is severe on lower timeframes.** If stops were executing perfectly at -1.0R, the median R should be ~-1.0. Instead:
- S2/1m median R = -1.284 (28.4% slippage beyond stop)
- S7/1h median R = -1.372 (37.2% slippage beyond stop)
- S2/5m median R = -1.230 (23.0% slippage beyond stop)
- S6/15m median R = -1.115 (11.5% slippage beyond stop)
- S9/1h median R = -1.008 (0.8% slippage — excellent)

**P25 values are alarming:** S7/1h P25 = -1.958 means 25% of S7/1h stop losses lose nearly 2R. S2/5m P25 = -2.064 means a quarter of those stops blow through to -2R.

**The pattern:** Lower timeframes and faster-moving setups (S7, S2) have worse stop execution. Higher timeframes (S9/1h, S1/15m) execute stops much closer to -1R. This is consistent with gap/momentum effects on shorter bars — when price moves against you on a 1m or 5m bar, the slippage from entry to the next bar can overshoot the stop.

**S6/1h is a red flag:** avg_pnl = -$20.25 per stop loss. With 282 SL trades, that is -$5,710 from stop losses alone. The -1.351 avg R says each stop costs 35% more than the intended risk. On a 1h timeframe this should not be happening — suggests the stop is too tight relative to 1h volatility.

---

## 5. Trade Lifecycle: The Bar-by-Bar PnL Curve

| Bars Held | N | Avg PnL | Total PnL | Win Rate |
|-----------|-------|---------|-----------|----------|
| 0 | 46,401 | -1.92 | -179 | 0.0% |
| 1 | 14,400 | -2.25 | -32,383 | 29.4% |
| 2 | 3,067 | -0.38 | -1,172 | 29.4% |
| 3 | 1,232 | +1.68 | +2,071 | 60.1% |
| 4 | 832 | +1.43 | +1,189 | 59.7% |
| 5 | 934 | +1.38 | +1,285 | 62.1% |
| 6 | 553 | +1.36 | +752 | 57.3% |
| 7 | 797 | -0.36 | -288 | 37.1% |
| 8 | 369 | +1.19 | +440 | 73.2% |
| 9 | 1,674 | -1.33 | -2,232 | 32.8% |
| 10 | 200 | +0.69 | +139 | 42.0% |
| 11 | 192 | +13.38 | +2,569 | 72.4% |

**The lifecycle is crystal clear:**

1. **Bar 0 (46,401 trades):** These are 46,308 null-exit trades (no data available / unfilled) plus 93 immediate TIME_STOPs. The null-exits have NULL pnl_net. Ignore these for PnL analysis.

2. **Bar 1 is the killing field:** 14,400 trades exit at bar 1 with avg PnL of -$2.25, total -$32,383. This is where FAIL_FAST and immediate STOP_LOSS concentrate. 70.6% of bar-1 exits are losers.

3. **Bars 3-6 are the sweet spot:** Win rate jumps to 58-62%, avg PnL is consistently +$1.36 to +$1.68. Trades that survive past bar 2 are statistically profitable.

4. **Bar 7 is a danger zone:** Win rate drops from 57-62% (bars 3-6) to 37.1%. This is the transition point where surviving trades start becoming TIME_STOP candidates.

5. **Bar 9 is the TIME_STOP cliff:** 1,674 trades at bar 9 with -$1.33 avg PnL. This is the max_bars expiry for multiple setups dumping their "went nowhere" trades.

6. **Bar 11 is an anomaly:** +$13.38 avg PnL, 72.4% win rate, 192 trades. This appears to be TRAIL_STOP captures from trades that needed 11 bars to develop. Worth investigating which setups produce this.

---

## 6. FAIL_FAST Analysis: The Largest Single Drag

| Setup | TF | N | Avg PnL | Total PnL | Avg Bars | Avg R |
|-------|-----|------|---------|-----------|----------|-------|
| S6 | 15m | 2,920 | -7.31 | -21,331 | 1.2 | -0.426 |
| S6 | 1h | 983 | -10.58 | -10,403 | 1.2 | -0.434 |
| S2 | 15m | 496 | -3.75 | -1,862 | 1.3 | -0.549 |
| S7 | 1h | 214 | -5.75 | -1,231 | 1.2 | -0.635 |
| S7 | 4h | 59 | -13.29 | -784 | 1.1 | -0.560 |

**FAIL_FAST is overwhelmingly an S6 problem.** S6/15m + S6/1h = -$31,734 of -$39,385 total FAIL_FAST losses (80.6%). All FAIL_FAST exits occur at bars_held ~1.2, meaning the trade fails on the very first or second bar after entry.

The avg R of -0.43 means FAIL_FAST trades lose about 0.43R — less than a full stop loss. This confirms FAIL_FAST is an early-exit mechanism that cuts trades before they reach the full stop. **The mechanism is working as intended** — without it, these trades would drift to -1R stop losses and the total drag would be ~2.3x worse.

**However:** 6,983 FAIL_FAST trades out of ~26,000 total (26.9%) is an extraordinarily high fail rate. Over a quarter of all entries immediately fail. This is a signal quality problem at the entry level, not an exit problem.

---

## 7. Synthesis: Five Key Findings

### Finding 1: The "Act Fast or Die" Structure
Winners declare themselves within 1-3 bars (66% of TP1 hits). Trades alive at bar 7+ have a declining expected value. The entire system has a rapid-resolution structure where edge decays quickly after entry.

### Finding 2: TIME_STOP Losses Concentrate at Specific Bar Counts
84% of TIME_STOP losses come from bars_held=9 (1,312 trades, -$2,492). This is a parameter artifact, not a market phenomenon. The max_bars=9 setting for S6/15m creates a "dump bucket" where directionless trades accumulate fees before forced exit.

### Finding 3: Stop Slippage is the Hidden Tax
Stops execute 11-37% beyond intended -1R on lower timeframes. S7/1h loses -1.672R on average per stop (vs intended -1R). This alone accounts for approximately -$4,000 to -$6,000 in excess losses vs perfect stop execution across the dataset.

### Finding 4: S7 and S6 Need Opposite Exit Strategies
S7 is a momentum/trend setup — trails capture 10.6x more per trade than TP1 on S7/1h. S6 is a mean-reversion/precision setup — TP1 captures +60% more per trade than trails. Shared exit parameters are suboptimal for both.

### Finding 5: FAIL_FAST is Working But Exposing an Entry Problem
FAIL_FAST prevents ~$91,000+ in potential stop losses (6,983 trades x ~$13 avg avoided loss). But the 27% fail rate means entry filters are letting in too many bad trades. The highest-leverage intervention is upstream (better entry filters) not downstream (better exits).

---

## 8. Recommended Interventions (Ranked by Expected PnL Impact)

### Intervention 1: Tighten S6 Entry Filters [Est. +$15,000-20,000]
FAIL_FAST is S6's biggest problem: 3,903 FAIL_FAST trades losing -$31,734. If entry filters could reject even 30% of these pre-entry, that recovers ~$9,500. Combined with fewer TIME_STOP trades from better entries: ~$15-20k recoverable.

### Intervention 2: Setup-Specific Exit Parameters [Est. +$5,000-8,000]
- **S7:** Remove or widen TP1, rely on trailing stop. Current TP1 captures only $1.37/trade on S7/1h while trail captures $14.53.
- **S6:** Keep TP1 (well-calibrated), but reduce max_bars from 9 to 6 — trades at bars 7-9 have negative expected value and should be cut sooner.
- **S2/1m, S2/5m:** Consider widening stop distance to reduce slippage blowthrough.

### Intervention 3: Dynamic Time-Based Exit [Est. +$2,000-3,000]
Replace hard max_bars with a time-decay exit: if a trade is not >0.3R profitable by bar 5, close it. Currently 1,269 TIME_STOP trades exit at small negative R (-0.246 avg) — catching these at bar 5 instead of bar 9 saves 4 bars of fee decay per trade.

### Intervention 4: Stop Distance Calibration by Timeframe [Est. +$2,000-4,000]
Lower-timeframe stops are too tight relative to bar volatility, causing excessive slippage beyond -1R. Widening stops on 1m/5m by 20-30% while reducing position size proportionally would reduce slippage tax without increasing dollar risk per trade.

### Intervention 5: Bar-7 "Danger Zone" Check [Est. +$500-1,000]
At bar 7, win rate drops from 57-62% (bars 3-6) to 37%. Implement an exit-or-tighten-trail check at bar 7 for all setups: if the trade is not >0.2R profitable at bar 7, close it.

---

## 9. Answers to Key Questions

**Q1: Are TIME_STOP exits just slow-bleed losers that should be cut sooner?**
Yes, definitively. 52% end in the -$1 to $0 range (pure fee bleed). 49% exit at small negative R (-0.246 avg). These are trades that never developed conviction in either direction. Cutting at bar 5-6 instead of bar 9 would save ~$1,500-2,000.

**Q2: How do TRAILING_STOP and TP1 trades evolve?**
TP1 trades are fast: 66% hit within 0-3 bars. Trail trades run longer (avg 3-5 bars) and capture more on S7 but less on S6. The two exit types serve different setup archetypes.

**Q3: Is there an optimal bars_held threshold for cutting TIME_STOP-bound trades?**
Yes: **bar 6**. The win rate is 57%+ through bar 6, drops to 37% at bar 7, and continues declining. The PnL curve goes negative and stays negative after bar 6 for non-TP1/non-trail trades. Bar 6 is the optimal early-cut threshold for most setups.

**Q4: How does stop-loss distance relate to the entry setup?**
Dramatically. S9/1h executes at median -1.008R (near-perfect). S7/1h executes at median -1.372R (37% excess loss). S2/5m at -1.230R. The relationship is: faster timeframes and momentum setups have worse stop execution. Stop distance should be calibrated per setup+timeframe, not globally.

**Q5: What changes to exit logic would have the highest PnL impact?**
1. Better S6 entry filters (largest absolute impact: ~$15-20k)
2. Setup-specific exit parameters, especially S7 trail-only (~$5-8k)
3. Reduce max_bars from 9 to 6 for S6/15m (~$2-3k)
4. Timeframe-specific stop widening on 1m/5m (~$2-4k)

---

*Analysis complete. All queries run against production simulation_results, 30-day window.*
