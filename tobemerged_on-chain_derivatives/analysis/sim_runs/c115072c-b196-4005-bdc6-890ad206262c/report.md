# Simulation Analysis Report

**Run ID:** `c115072c`  
**Trades analysed:** 1087 (with trace data, of 1087 valid)  
**Overall:** WR=44.3%, PnL=-1,962, PF=0.71  
**Generated:** 2026-02-16 18:15 UTC

## 1. Executive Summary

- **44% of trades die within 2 bars** (score=100) — 476/1087 trades exit via STOP_LOSS or FAIL_FAST within 2 bars. These are entries that immediately move against the posit...
- **MAE/MFE data not available — bar-by-bar tracking needed** (score=80) — trace_json stores entry-time features only, not intra-trade bar tracking. Adding MAE/MFE would enable stop optimization ...
- **Top positive correlate with pnl_net: score.htf_confluence** (score=48) — score.htf_confluence has rho=0.235 (p=0.006678, N=132) with pnl_net. Higher values predict better outcomes....
- **Best winner rule: ind.ema50_slope_atr <= 0.2183 AND ind.vwap_distance_atr > 5.1432** (score=48) — Decision tree path [ind.ema50_slope_atr <= 0.2183 AND ind.vwap_distance_atr > 5.1432 AND ind.ma_gap_atr > 1.7467 AND ind...
- **High stop-loss rule: ind.clv <= 0.978 AND ind.rsi <= 66.13** (score=48) — Path [ind.clv <= 0.978 AND ind.rsi <= 66.13 AND ind.vwap_distance_atr <= 2.9999 AND ind.donchian_high_20 <= 1.3365] → 22...

## 2. Ranked Insight Register

| Rank | ID | Title | I | C | A | Score | Section |
|------|----|-------|---|---|---|-------|---------|
| 1 | I09 | 44% of trades die within 2 bars | 5 | 5 | 4 | 100 | 4b |
| 2 | I19 | MAE/MFE data not available — bar-by-bar tracking needed | 4 | 5 | 4 | 80 | 6 |
| 3 | I01 | Top positive correlate with pnl_net: score.htf_confluence | 4 | 4 | 3 | 48 | 3a |
| 4 | I05 | Best winner rule: ind.ema50_slope_atr <= 0.2183 AND ind.vwap... | 4 | 3 | 4 | 48 | 3b |
| 5 | I06 | High stop-loss rule: ind.clv <= 0.978 AND ind.rsi <= 66.13 | 4 | 3 | 4 | 48 | 3b |
| 6 | I08 | Worst archetype: 'Mixed Quick-Death (S6)' (41% WR) | 4 | 3 | 4 | 48 | 3c |
| 7 | I07 | Best archetype: 'Winners Target-Hit (S6)' (73% WR) | 4 | 3 | 3 | 36 | 3c |
| 8 | I10 | TP2 hit rate: 5% of profitable exits reach TP2 | 3 | 4 | 3 | 36 | 4c |
| 9 | I17 | Outperformer: S1 (67% WR) | 4 | 3 | 3 | 36 | 5 |
| 10 | I18 | Outperformer: S2 (76% WR) | 4 | 3 | 3 | 36 | 5 |
| 11 | I11 | Top predictor of STOP_LOSS: score.div_magnitude | 3 | 3 | 3 | 27 | 4a |
| 12 | I12 | Top predictor of FAIL_FAST: score.span_quality | 3 | 3 | 3 | 27 | 4a |
| 13 | I13 | Top predictor of TP1: score.span_quality | 3 | 3 | 3 | 27 | 4a |
| 14 | I14 | Top predictor of TP2: state.long_breakout | 3 | 3 | 3 | 27 | 4a |
| 15 | I15 | Top predictor of TRAIL_STOP: score.span_quality | 3 | 3 | 3 | 27 | 4a |
| 16 | I16 | Top predictor of TIME_STOP: state.long_allowed | 3 | 3 | 3 | 27 | 4a |
| 17 | I02 | Top negative correlate with pnl_net: state.box_low | 4 | 2 | 3 | 24 | 3a |
| 18 | I03 | Top positive correlate with is_winner: score.severity | 4 | 2 | 3 | 24 | 3a |
| 19 | I04 | Top negative correlate with is_winner: state.bb_width_pctile | 4 | 2 | 3 | 24 | 3a |

## 3a. Univariate Correlations (Spearman)

### Top correlations with `pnl_net`

**Top 20 positive:**

| Feature | rho | p-value | N | Low-conf? |
|---------|-----|---------|---|-----------|
| score.htf_confluence | +0.2350 | 0.006678 | 132 |  |
| score.severity | +0.2095 | 0.453652 | 15 |  :warning: |
| state.rsi_extreme_high | +0.1380 | 0.459057 | 31 |  :warning: |
| state.short_breakout | +0.1298 | 0.138113 | 132 |  |
| score.span_quality | +0.1214 | 0.515155 | 31 |  :warning: |
| state.long_allowed | +0.1126 | 0.539591 | 32 |  :warning: |
| score.break_quality | +0.1088 | 0.553328 | 32 |  :warning: |
| state.donchian_low | +0.0858 | 0.327955 | 132 |  |
| state.prev_donchian_high | +0.0853 | 0.330902 | 132 |  |
| state.prev_donchian_low | +0.0853 | 0.330648 | 132 |  |
| state.donchian_mid | +0.0851 | 0.332107 | 132 |  |
| state.donchian_high | +0.0846 | 0.335030 | 132 |  |
| score.volume_quality | +0.0604 | 0.491763 | 132 |  |
| score.div_magnitude | +0.0599 | 0.748935 | 31 |  :warning: |
| score.break_strength | +0.0405 | 0.644679 | 132 |  |
| score.bias_strength | +0.0308 | 0.711465 | 147 |  |
| score.conv_tight | +0.0172 | 0.947891 | 17 |  :warning: |
| score.clean_close | +0.0135 | 0.656707 | 1087 |  |
| state.htf_bias_score | +0.0126 | 0.686675 | 1024 |  |
| ind.htf_sma50 | +0.0093 | 0.760076 | 1072 |  |

**Top 20 negative:**

| Feature | rho | p-value | N | Low-conf? |
|---------|-----|---------|---|-----------|
| state.dec_count | -0.0102 | 0.969113 | 17 |  :warning: |
| ind.ema50_slope_atr | -0.0121 | 0.691160 | 1087 |  |
| score.participation | -0.0141 | 0.662310 | 955 |  |
| state.ma_gap_atr | -0.0172 | 0.947891 | 17 |  :warning: |
| ind.atr | -0.0208 | 0.494225 | 1087 |  |
| ind.rsi_slope | -0.0307 | 0.311854 | 1087 |  |
| score.rsi_extremity | -0.0373 | 0.842072 | 31 |  :warning: |
| state.bias_score | -0.0490 | 0.862349 | 15 |  :warning: |
| ind.sma200_slope_atr | -0.0573 | 0.059006 | 1087 |  |
| ind.rsi | -0.0609 | 0.044598 | 1087 |  |
| score.trend_strength | -0.0664 | 0.045361 | 909 |  |
| ind.rsi_sma | -0.0721 | 0.017494 | 1087 |  |
| ind.ma_gap_atr | -0.0825 | 0.006521 | 1087 |  |
| ind.bb_width | -0.1010 | 0.000858 | 1087 |  |
| state.squeeze_age | -0.1115 | 0.692356 | 15 |  :warning: |
| state.long_breakout | -0.1298 | 0.138113 | 132 |  |
| state.rsi_extreme_low | -0.1380 | 0.459057 | 31 |  :warning: |
| state.bb_width_pctile | -0.2095 | 0.453652 | 15 |  :warning: |
| state.box_high | -0.2929 | 0.289472 | 15 |  :warning: |
| state.box_low | -0.2929 | 0.289472 | 15 |  :warning: |

### Top correlations with `is_winner`

**Top 20 positive:**

| Feature | rho | p-value | N | Low-conf? |
|---------|-----|---------|---|-----------|
| score.severity | +0.3446 | 0.208436 | 15 |  :warning: |
| score.span_quality | +0.3372 | 0.063575 | 31 |  :warning: |
| score.conv_tight | +0.2548 | 0.323734 | 17 |  :warning: |
| score.conv_speed | +0.2495 | 0.334210 | 17 |  :warning: |
| state.dec_count | +0.2495 | 0.334210 | 17 |  :warning: |
| state.long_allowed | +0.1839 | 0.313699 | 32 |  :warning: |
| state.rsi_extreme_high | +0.1664 | 0.370858 | 31 |  :warning: |
| state.short_breakout | +0.1535 | 0.078814 | 132 |  |
| score.break_quality | +0.1481 | 0.418508 | 32 |  :warning: |
| state.donchian_high | +0.1387 | 0.112821 | 132 |  |
| state.prev_donchian_high | +0.1385 | 0.113363 | 132 |  |
| state.donchian_mid | +0.1380 | 0.114456 | 132 |  |
| state.donchian_low | +0.1378 | 0.115003 | 132 |  |
| state.prev_donchian_low | +0.1372 | 0.116661 | 132 |  |
| score.rsi_extremity | +0.1133 | 0.543907 | 31 |  :warning: |
| score.htf_confluence | +0.0700 | 0.425337 | 132 |  |
| score.volume_quality | +0.0651 | 0.458333 | 132 |  |
| score.break_strength | +0.0605 | 0.490770 | 132 |  |
| score.div_magnitude | +0.0402 | 0.829964 | 31 |  :warning: |
| ind.vwap_lower_2sd | +0.0321 | 0.294228 | 1070 |  |

**Top 20 negative:**

| Feature | rho | p-value | N | Low-conf? |
|---------|-----|---------|---|-----------|
| ind.clv | -0.0212 | 0.485258 | 1087 |  |
| ind.ema20_slope_atr | -0.0236 | 0.436926 | 1087 |  |
| ind.sma50_slope_atr | -0.0259 | 0.393050 | 1087 |  |
| state.box_high | -0.0327 | 0.907807 | 15 |  :warning: |
| state.box_low | -0.0327 | 0.907807 | 15 |  :warning: |
| score.participation | -0.0385 | 0.234417 | 955 |  |
| ind.ema50_slope_atr | -0.0397 | 0.190947 | 1087 |  |
| ind.vwap_distance_atr | -0.0421 | 0.168427 | 1070 |  |
| ind.bb_width | -0.0604 | 0.046583 | 1087 |  |
| ind.rsi_sma | -0.0667 | 0.027792 | 1087 |  |
| ind.ma_gap_atr | -0.0776 | 0.010509 | 1087 |  |
| ind.rsi | -0.0779 | 0.010151 | 1087 |  |
| score.bias_strength | -0.0779 | 0.348357 | 147 |  |
| ind.sma200_slope_atr | -0.0833 | 0.005991 | 1087 |  |
| score.trend_strength | -0.0969 | 0.003467 | 909 |  |
| state.long_breakout | -0.1535 | 0.078814 | 132 |  |
| state.rsi_extreme_low | -0.1664 | 0.370858 | 31 |  :warning: |
| state.squeeze_age | -0.1978 | 0.479754 | 15 |  :warning: |
| state.ma_gap_atr | -0.2548 | 0.323734 | 17 |  :warning: |
| state.bb_width_pctile | -0.3446 | 0.208436 | 15 |  :warning: |

## 3b. Interaction Mining (Decision Trees)

### Winner prediction tree (max_depth=4)

**Top feature importances:**

- `ind.ema50_slope_atr`: 0.4425
- `ind.vwap_distance_atr`: 0.1937
- `ind.bb_width`: 0.1547
- `ind.ma_gap_atr`: 0.0812
- `ind.ema20_slope_atr`: 0.0773
- `ind.rsi_sma`: 0.0506
- `cond.why_0`: 0.0000
- `cond.why_1`: 0.0000
- `cond.why_2`: 0.0000
- `cond.why_3`: 0.0000

**Top leaf rules:**

1. **IF** ind.ema50_slope_atr <= 0.2183 AND ind.vwap_distance_atr > 5.1432 AND ind.ma_gap_atr > 1.7467 AND ind.rsi_sma > 63.1764
   **THEN** win_rate=13% (N=39)

2. **IF** ind.ema50_slope_atr <= 0.2183 AND ind.vwap_distance_atr <= 5.1432 AND ind.bb_width > 0.0772 AND ind.ema20_slope_atr <= 0.1777
   **THEN** win_rate=20% (N=41)

3. **IF** ind.ema50_slope_atr <= 0.2183 AND ind.vwap_distance_atr <= 5.1432 AND ind.bb_width <= 0.0772 AND ind.ema50_slope_atr <= -0.1391
   **THEN** win_rate=22% (N=36)

4. **IF** ind.ema50_slope_atr > 0.2183
   **THEN** win_rate=0% (N=20)

5. **IF** ind.ema50_slope_atr <= 0.2183 AND ind.vwap_distance_atr > 5.1432 AND ind.ma_gap_atr > 1.7467 AND ind.rsi_sma <= 63.1764
   **THEN** win_rate=33% (N=33)

### Stop-loss prediction tree (max_depth=4)

**Top feature importances:**

- `ind.clv`: 0.2382
- `ind.rsi`: 0.1896
- `ind.vwap_distance_atr`: 0.1493
- `ind.ema20_slope_atr`: 0.1350
- `ind.volume_z`: 0.1277
- `ind.donchian_high_20`: 0.0671
- `ind.rsi_sma`: 0.0367
- `ind.vwap_lower_2sd`: 0.0359
- `ind.kc_lb`: 0.0204
- `cond.why_0`: 0.0000

**Top leaf rules:**

1. **IF** ind.clv <= 0.978 AND ind.rsi > 66.13 AND ind.rsi_sma <= 66.1937 AND ind.kc_lb > 0.0009
   **THEN** stop_loss_rate=3% (N=362)

2. **IF** ind.clv > 0.978 AND ind.ema20_slope_atr > 0.0609 AND ind.volume_z > 1.9858 AND ind.rsi > 63.8535
   **THEN** stop_loss_rate=15% (N=203)

3. **IF** ind.clv <= 0.978 AND ind.rsi <= 66.13 AND ind.vwap_distance_atr <= 2.9999 AND ind.donchian_high_20 > 1.3365
   **THEN** stop_loss_rate=8% (N=152)

4. **IF** ind.clv <= 0.978 AND ind.rsi > 66.13 AND ind.rsi_sma > 66.1937 AND ind.vwap_lower_2sd > 0.047
   **THEN** stop_loss_rate=8% (N=74)

5. **IF** ind.clv <= 0.978 AND ind.rsi <= 66.13 AND ind.vwap_distance_atr <= 2.9999 AND ind.donchian_high_20 <= 1.3365
   **THEN** stop_loss_rate=22% (N=111)

## 3c. Trade Archetypes (K-Means Clustering)

**Best k=4** (silhouette=0.335)

| Cluster | Name | Count | WR | Avg PnL | Avg R | Dom. Exit | Dom. Setup | Top Features |
|---------|------|-------|----|---------|-------|-----------|------------|--------------|
| 0 | Mixed Quick-Death (S6) | 146 | 43.2% | -3.5 | -0.16 | FAIL_FAST | S6 | ind.rsi(z=2.18), ind.ema50_slope_atr(z=2.16), state.htf_bias_score(z=2.15) |
| 1 | Mixed Quick-Death (S6) | 785 | 44.7% | -1.5 | -0.11 | FAIL_FAST | S6 | score.pullback_quality(z=0.41), score.retrace_depth(z=0.36), state.htf_bias_score(z=0.35) |
| 2 | Winners Target-Hit (S6) | 11 | 72.7% | 2.1 | 0.11 | TP1 | S6 | ind.bb_ub(z=9.87), ind.kc_ub(z=9.87), ind.ema_50(z=9.87) |
| 3 | Mixed Quick-Death (S6) | 145 | 41.4% | -2.3 | -0.11 | FAIL_FAST | S6 | score.pullback_quality(z=2.42), score.retrace_depth(z=2.11), state.retrace_depth_atr(z=1.9) |

## 4a. Exit Predictability

### STOP_LOSS — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| score.div_magnitude | +0.2060 | 0.266310 | 31 |
| state.long_allowed | +0.1429 | 0.435401 | 32 |
| state.long_breakout | +0.1412 | 0.106267 | 132 |
| state.short_breakout | -0.1412 | 0.106267 | 132 |
| score.bias_strength | +0.1404 | 0.089829 | 147 |

### FAIL_FAST — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| score.span_quality | -0.2266 | 0.220171 | 31 |
| state.long_allowed | -0.2026 | 0.266087 | 32 |
| ind.bb_width | +0.1789 | 0.000000 | 1087 |
| score.bias_strength | -0.1552 | 0.060497 | 147 |
| ind.rsi | +0.1546 | 0.000000 | 1087 |

### TP1 — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| score.span_quality | +0.2360 | 0.201246 | 31 |
| score.bias_strength | -0.1712 | 0.038145 | 147 |
| state.long_allowed | +0.1429 | 0.435401 | 32 |
| score.volume_quality | +0.1248 | 0.153806 | 132 |
| state.rsi_extreme_high | +0.1225 | 0.511670 | 31 |

### TP2 — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| state.long_breakout | -0.1002 | 0.252842 | 132 |
| state.short_breakout | +0.1002 | 0.252842 | 132 |
| ind.sma200_slope_atr | -0.0877 | 0.003820 | 1087 |
| score.break_strength | -0.0724 | 0.409264 | 132 |
| ind.rsi | -0.0686 | 0.023804 | 1087 |

### TRAIL_STOP — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| score.span_quality | -0.1840 | 0.321627 | 31 |
| state.long_allowed | +0.1816 | 0.319960 | 32 |
| score.div_magnitude | -0.1722 | 0.354375 | 31 |
| score.clean_close | +0.1083 | 0.000349 | 1087 |
| ind.clv | +0.1049 | 0.000535 | 1087 |

### TIME_STOP — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| state.long_allowed | -0.3578 | 0.044347 | 32 |
| state.long_breakout | -0.2353 | 0.006621 | 132 |
| state.short_breakout | +0.2353 | 0.006621 | 132 |
| score.span_quality | +0.2011 | 0.278116 | 31 |
| score.rsi_extremity | +0.1940 | 0.295793 | 31 |

## 4b. Time-in-Trade Analysis

### Bars held by exit reason

| Exit Reason | Count | Min | P25 | Median | P75 | Max | Mean |
|-------------|-------|-----|-----|--------|-----|-----|------|
| FAIL_FAST | 361 | 1 | 1.0 | 1.0 | 1.0 | 2 | 1.2 |
| TP1 | 345 | 1 | 1.0 | 2.0 | 4.0 | 26 | 2.9 |
| TRAIL_STOP | 130 | 1 | 1.0 | 1.0 | 3.0 | 49 | 3.3 |
| TIME_STOP | 77 | 1 | 9.0 | 9.0 | 9.0 | 9 | 8.6 |
| TP2 | 18 | 1 | 1.0 | 1.0 | 1.8 | 9 | 1.9 |
| STOP_LOSS | 156 | 1 | 1.0 | 1.0 | 3.0 | 58 | 2.9 |

### Instant death trades (bars_held <= 2 AND STOP_LOSS/FAIL_FAST)

**476 trades (43.8%)** exit within 2 bars via stop or fail-fast.

**Statistically significant feature differences (instant death vs rest):**

| Feature | Instant Median | Rest Median | p-value |
|---------|---------------|-------------|---------|
| ind.bb_width | 0.0386 | 0.033 | 0.000011 |
| ind.rsi | 69.8222 | 68.3515 | 0.003355 |
| ind.rsi_sma | 60.2332 | 59.4681 | 0.045852 |
| score.trend_strength | 1.0 | 1.0 | 0.048334 |

## 4c. TP1 vs TP2 Analysis

- TP1 exits: 345
- TP2 exits: 18
- **TP2 hit rate:** 5.0% of profitable exits reached TP2

**Features differentiating TP1 vs TP2 exits:**

| Feature | rho (with TP2) | p-value | N |
|---------|---------------|---------|---|
| score.volume_quality | -0.3894 | 0.089689 | 20 |
| score.bias_strength | +0.1582 | 0.440079 | 26 |
| ind.sma200_slope_atr | -0.1476 | 0.004828 | 363 |
| score.break_strength | -0.1155 | 0.627796 | 20 |
| ind.rsi | -0.1051 | 0.045357 | 363 |
| state.long_breakout | -0.1021 | 0.668530 | 20 |
| state.short_breakout | +0.1021 | 0.668530 | 20 |
| score.clean_close | +0.1014 | 0.053590 | 363 |
| score.participation | +0.1009 | 0.061874 | 343 |
| ind.rsi_slope | -0.0826 | 0.116221 | 363 |

## 5. Segment Deep Dives

### By Setup

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| S7 | 132 | 36.4% | [28.6%, 44.9%] | 0.2 | -0.25 | 17.0 | -9.5 | 21 |
| S3 | 31 | 41.9% | [26.4%, 59.2%] | -4.0 | -0.13 | 6.5 | -11.6 | -125 |
| S6 | 892 | 44.6% | [41.4%, 47.9%] | -2.2 | -0.10 | 9.2 | -11.3 | -1941 |
| S1 | 15 | 66.7% | [41.7%, 84.8%] | 0.5 | 0.06 | 6.2 | -11.0 | 7 |
| S2 | 17 | 76.5% | [52.7%, 90.5%] | 4.5 | 0.09 | 9.6 | -12.1 | 76 |

### By Setup × Timeframe

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| S3/1h | 7 | 14.3% | [2.6%, 51.3%] | -6.6 | -0.21 | 20.2 | -11.1 | -46 |
| S7/1h | 117 | 35.9% | [27.8%, 44.9%] | 1.6 | -0.26 | 18.7 | -8.0 | 183 |
| S7/4h | 15 | 40.0% | [19.8%, 64.2%] | -10.8 | -0.19 | 5.1 | -21.4 | -162 |
| S6/1h | 263 | 42.2% | [36.4%, 48.2%] | -4.3 | -0.14 | 9.8 | -14.6 | -1130 |
| S6/15m | 629 | 45.6% | [41.8%, 49.5%] | -1.3 | -0.09 | 9.0 | -9.9 | -812 |
| S3/15m | 24 | 50.0% | [31.4%, 68.6%] | -3.3 | -0.10 | 5.4 | -11.9 | -79 |
| S1/15m | 7 | 57.1% | [25.1%, 84.2%] | 0.3 | -0.06 | 4.1 | -4.8 | 2 |
| S2/1h | 8 | 62.5% | [30.6%, 86.3%] | 0.3 | -0.04 | 6.8 | -10.6 | 3 |
| S1/1h | 8 | 75.0% | [40.9%, 92.8%] | 0.6 | 0.17 | 7.6 | -20.3 | 5 |
| S2/15m | 9 | 88.9% | [56.5%, 98.0%] | 8.2 | 0.20 | 11.3 | -16.6 | 74 |

### By Direction

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| long | 949 | 43.9% | [40.8%, 47.1%] | -1.7 | -0.12 | 10.3 | -11.1 | -1608 |
| short | 138 | 47.1% | [39.0%, 55.4%] | -2.6 | -0.12 | 6.8 | -10.9 | -354 |

### By Day of Week

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| Saturday | 461 | 39.7% | [35.3%, 44.2%] | -3.1 | -0.21 | 7.3 | -9.9 | -1431 |
| Tuesday | 70 | 40.0% | [29.3%, 51.7%] | -3.4 | -0.13 | 8.2 | -11.1 | -237 |
| Thursday | 134 | 45.5% | [37.3%, 54.0%] | -3.0 | -0.09 | 8.2 | -12.4 | -408 |
| Monday | 100 | 49.0% | [39.4%, 58.7%] | -2.1 | -0.18 | 9.8 | -13.5 | -213 |
| Wednesday | 112 | 49.1% | [40.0%, 58.2%] | 0.8 | 0.05 | 18.3 | -16.1 | 88 |
| Friday | 210 | 50.5% | [43.8%, 57.2%] | 1.1 | 0.02 | 11.4 | -9.3 | 240 |

### By Hour Bucket (UTC)

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| 20-24 | 134 | 35.8% | [28.2%, 44.2%] | 0.1 | -0.18 | 19.5 | -10.7 | 19 |
| 08-12 | 285 | 40.4% | [34.8%, 46.1%] | -3.7 | -0.14 | 8.0 | -11.6 | -1046 |
| 00-04 | 149 | 43.0% | [35.3%, 51.0%] | -3.1 | -0.15 | 8.5 | -11.8 | -462 |
| 12-16 | 179 | 46.4% | [39.2%, 53.7%] | -1.3 | -0.11 | 10.1 | -11.2 | -238 |
| 04-08 | 164 | 48.2% | [40.6%, 55.8%] | -1.4 | -0.07 | 9.8 | -11.9 | -232 |
| 16-20 | 176 | 52.8% | [45.5%, 60.1%] | -0.0 | -0.04 | 7.9 | -8.9 | -3 |

## 6. MAE/MFE Assessment

**Status:** NOT AVAILABLE

The current `trace_json` stores entry-time indicator values and score components — 
it does **not** track bar-by-bar price excursions during the trade.

### Proposed Schema Addition

```sql
CREATE TABLE trade_bar_tracking (
    trade_id UUID REFERENCES trades(id),
    bar_index INT (0-based from entry),
    bar_start_ts TIMESTAMPTZ,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    unrealized_pnl NUMERIC,
    mae_so_far NUMERIC (worst drawdown from entry to this bar),
    mfe_so_far NUMERIC (best excursion from entry to this bar),
);
```

**Implementation file:** `opportunity_simulator.py` (lines lines 611-676 (trace construction))

**Pseudocode:**
```python
In simulate_trade(), after entry fill:
  mae, mfe = 0.0, 0.0
  for bar in bars_after_entry:
    unrealized = (bar.close - entry_price) * direction_mult
    mae = min(mae, unrealized)
    mfe = max(mfe, unrealized)
    record(trade_id, bar_index, bar.ts, bar.high, bar.low, bar.close, unrealized, mae, mfe)
  # Also store final mae/mfe on the trade row itself for quick access
```

## 7. Code Attribution

Top insights mapped to code paths:

### I09: 44% of trades die within 2 bars

- **Explicit:** `exit_checker.py:101-107 (FAIL_FAST logic)`
- `exit_checker.py:101-107` — Exits if close moves 0.3% against entry within first 2 bars
  - Params: fail_fast_bars=2, fail_fast_threshold=0.003
- `exit_checker.py:~70-99` — Hard stop at setup-computed stop_price (checked first in priority)

### I19: MAE/MFE data not available — bar-by-bar tracking needed

- **Explicit:** `opportunity_simulator.py:611-676 (trace construction)`
- `exit_checker.py:155-164` — Target 1 hit (setup-specific distance from entry)
- `exit_checker.py:167-175` — Target 2 hit (extended target, setup-specific)

### I01: Top positive correlate with pnl_net: score.htf_confluence

- `signal_engine.py:620-794` — Weighted score aggregation → rank; serves ranked list to API

### I05: Best winner rule: ind.ema50_slope_atr <= 0.2183 AND ind.vwap_distance_atr > 5.1432

- *(no specific code path identified)*

### I06: High stop-loss rule: ind.clv <= 0.978 AND ind.rsi <= 66.13

- **Explicit:** `exit_checker.py:101-107 (FAIL_FAST), exit_checker.py:131-153 (TRAIL_STOP)`

### I08: Worst archetype: 'Mixed Quick-Death (S6)' (41% WR)

- `exit_checker.py:101-107` — Exits if close moves 0.3% against entry within first 2 bars
  - Params: fail_fast_bars=2, fail_fast_threshold=0.003
- `setups/s6_trend_pullback.py` — Trend Pullback setup: trend_strength, pullback_quality, retrace_depth, volume_quality, htf_confluence
- `signal_engine.py:620-794` — Weighted score aggregation → rank; serves ranked list to API

### I07: Best archetype: 'Winners Target-Hit (S6)' (73% WR)

- `exit_checker.py:155-164` — Target 1 hit (setup-specific distance from entry)
- `exit_checker.py:167-175` — Target 2 hit (extended target, setup-specific)
- `setups/s6_trend_pullback.py` — Trend Pullback setup: trend_strength, pullback_quality, retrace_depth, volume_quality, htf_confluence

### I10: TP2 hit rate: 5% of profitable exits reach TP2

- **Explicit:** `exit_checker.py:155-175 (TP1/TP2 logic)`
- `exit_checker.py:155-164` — Target 1 hit (setup-specific distance from entry)
- `exit_checker.py:167-175` — Target 2 hit (extended target, setup-specific)

### I17: Outperformer: S1 (67% WR)

- `setups/s1_bb_squeeze.py` — BB Squeeze setup: severity, bias_strength, participation, break_quality, clean_close

### I18: Outperformer: S2 (76% WR)

- `setups/s2_golden_cross.py` — Golden Cross setup: conv_speed, conv_tight, trend_strength, participation, clean_close

## 8. Next Experiments

### E1: Relax FAIL_FAST threshold for setups with high FAIL_FAST rate

- **Module:** `exit_checker.py`
- **Change:** Increase fail_fast_threshold from 0.003 (0.3%) to 0.005 (0.5%) for S6 and S7
- **Rationale:** FAIL_FAST exits 361 trades (33.2% of all). Some may be noise-triggered — widening the threshold should reduce false exits.
- **Baseline:** FAIL_FAST rate: 33.2%, overall WR: 44.3%
- **Success metric:** FAIL_FAST rate, overall win rate, profit factor

### E2: Add RSI filter for S7/1h

- **Module:** `setups/s7_*.py (evaluate_trigger)`
- **Change:** Gate S7/1h entries on RSI not being extreme against direction (e.g., RSI > 70 blocks long, RSI < 30 blocks short)
- **Rationale:** S7/1h has 35.9% WR on 117 trades (vs 44.3% overall). Many may be late-momentum entries.
- **Baseline:** S7/1h: WR=35.9%, N=117, PnL=183.22
- **Success metric:** S7/1h win rate and total PnL

### E3: Reduce time_stop_bars for S7 from 6 to 4

- **Module:** `setups/s7_momentum_breakout.py (metadata.time_stop_bars)`
- **Change:** Reduce S7 time_stop_bars from 6 to 4. Momentum breakouts should resolve quickly.
- **Rationale:** Momentum breakouts that don't move within 4 bars are likely failed breakouts. Holding longer just delays the inevitable stop-loss.
- **Baseline:** TIME_STOP stats: {'count': 77, 'min': 1, 'p25': 9.0, 'median': 9.0, 'p75': 9.0, 'max': 9, 'mean': 8.6}
- **Success metric:** S7 profit factor, TIME_STOP count, avg holding period

### E4: Raise score threshold for short entries

- **Module:** `signal_engine.py (ranking/filtering)`
- **Change:** Require opportunity_score >= 0.75 for short entries (vs current universal threshold)
- **Rationale:** Shorts: WR=47.1% (N=138) vs Longs: WR=43.9% (N=949). Higher bar for shorts should filter out weak setups.
- **Baseline:** Short PnL=-354.1, Long PnL=-1607.51
- **Success metric:** Short win rate, short profit factor, overall PnL

### E5: Require volume_z >= 1.5 for all entries

- **Module:** `opportunity_simulator.py (entry gates) or setup evaluate_trigger`
- **Change:** Add global gate: reject opportunities where ind.volume_z < 1.5 at entry
- **Rationale:** Volume confirmation is a score component but not a hard gate. Low-volume entries may lack the participation needed for follow-through.
- **Baseline:** Overall WR: 44.3%, PF: 0.71
- **Success metric:** Win rate, profit factor, trade count (ensuring we don't filter too aggressively)

## 9. What We Can't See Yet

1. **MAE/MFE (bar-by-bar excursion tracking)** — Cannot assess whether stops are too tight or targets too conservative without knowing the intra-trade path. See Section 6 for implementation proposal.

2. **Market regime at entry time** — BTC trend, overall market volatility regime, funding rates. Trades entered during high-correlation risk-off events may have structurally different outcomes. Would require adding BTC price/regime data to trace_json.

3. **Slippage model accuracy** — The simulator applies a fixed slippage model. Without live fill data to compare against, we cannot validate whether the model is optimistic or pessimistic. Requires live trading data.

4. **Multi-trade correlation** — When the same symbol fires multiple setups in close succession, are the outcomes correlated? Current analysis treats each trade independently. Would need time-proximity grouping of trades on the same symbol.

5. **Order book depth / liquidity at entry** — Position sizing assumes sufficient liquidity. For microcaps (already filtered by the microcap gate), the actual fill quality depends on book depth not captured in OHLCV data.
