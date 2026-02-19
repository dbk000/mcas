# Simulation Analysis Report

**Run ID:** `25b54fff-9d02-479c-8439-f163517b8a77`  
**Trades analysed:** 1405 (with trace data, of 1405 valid)  
**Overall:** WR=41.4%, PnL=-2,395, PF=0.71  
**Generated:** 2026-02-16 15:46 UTC

## 1. Executive Summary

- **47% of trades die within 2 bars** (score=100) — 656/1405 trades exit via STOP_LOSS or FAIL_FAST within 2 bars. These are entries that immediately move against the posit...
- **Underperformer: S7/15m (29% WR)** (score=100) — S7/15m: WR=28.9% vs overall 41.4% (N=277, Wilson 95% CI: (0.2386, 0.3448)). Avg R=-0.6, total PnL=-725.29. Decomposition...
- **Underperformer: Sunday (26% WR)** (score=100) — Sunday: WR=26.4% vs overall 41.4% (N=140, Wilson 95% CI: (0.1982, 0.3429)). Avg R=-0.551, total PnL=-862.97. Decompositi...
- **MAE/MFE data not available — bar-by-bar tracking needed** (score=80) — trace_json stores entry-time features only, not intra-trade bar tracking. Adding MAE/MFE would enable stop optimization ...
- **Best winner rule: exit.bars_held <= 2.5 AND ind.rsi <= 80.1003** (score=48) — Decision tree path [exit.bars_held <= 2.5 AND ind.rsi <= 80.1003 AND ind.rsi_sma <= 54.4845 AND ind.sma200_slope_atr > -...

## 2. Ranked Insight Register

| Rank | ID | Title | I | C | A | Score | Section |
|------|----|-------|---|---|---|-------|---------|
| 1 | I09 | 47% of trades die within 2 bars | 5 | 5 | 4 | 100 | 4b |
| 2 | I18 | Underperformer: S7/15m (29% WR) | 5 | 4 | 5 | 100 | 5 |
| 3 | I19 | Underperformer: Sunday (26% WR) | 5 | 4 | 5 | 100 | 5 |
| 4 | I20 | MAE/MFE data not available — bar-by-bar tracking needed | 4 | 5 | 4 | 80 | 6 |
| 5 | I05 | Best winner rule: exit.bars_held <= 2.5 AND ind.rsi <= 80.10... | 4 | 3 | 4 | 48 | 3b |
| 6 | I06 | High stop-loss rule: score.break_strength > 0.9283 AND ind.r... | 4 | 3 | 4 | 48 | 3b |
| 7 | I08 | Worst archetype: 'Mixed Quick-Death (S6)' (38% WR) | 4 | 3 | 4 | 48 | 3c |
| 8 | I07 | Best archetype: 'Winners Target-Hit (S6)' (75% WR) | 4 | 3 | 3 | 36 | 3c |
| 9 | I10 | TP2 hit rate: 6% of profitable exits reach TP2 | 3 | 4 | 3 | 36 | 4c |
| 10 | I17 | Outperformer: S2 (71% WR) | 4 | 3 | 3 | 36 | 5 |
| 11 | I11 | Top predictor of STOP_LOSS: score.clean_close | 3 | 3 | 3 | 27 | 4a |
| 12 | I12 | Top predictor of FAIL_FAST: exit.bars_held | 3 | 3 | 3 | 27 | 4a |
| 13 | I13 | Top predictor of TP1: state.long_allowed | 3 | 3 | 3 | 27 | 4a |
| 14 | I14 | Top predictor of TP2: score.break_strength | 3 | 3 | 3 | 27 | 4a |
| 15 | I15 | Top predictor of TRAIL_STOP: score.severity | 3 | 3 | 3 | 27 | 4a |
| 16 | I16 | Top predictor of TIME_STOP: state.squeeze_age | 3 | 3 | 3 | 27 | 4a |
| 17 | I01 | Top positive correlate with pnl_net: state.bias_score | 4 | 2 | 3 | 24 | 3a |
| 18 | I02 | Top negative correlate with pnl_net: score.conv_tight | 4 | 2 | 3 | 24 | 3a |
| 19 | I03 | Top positive correlate with is_winner: state.long_allowed | 4 | 2 | 3 | 24 | 3a |
| 20 | I04 | Top negative correlate with is_winner: state.bb_width_pctile | 4 | 2 | 3 | 24 | 3a |

## 3a. Univariate Correlations (Spearman)

### Top correlations with `pnl_net`

**Top 20 positive:**

| Feature | rho | p-value | N | Low-conf? |
|---------|-----|---------|---|-----------|
| state.bias_score | +0.4938 | 0.022909 | 21 |  :warning: |
| state.long_allowed | +0.4357 | 0.008892 | 35 |  :warning: |
| exit.bars_held | +0.2192 | 0.000000 | 1405 |  |
| score.span_quality | +0.1952 | 0.246881 | 37 |  :warning: |
| state.ma_gap_atr | +0.1912 | 0.512584 | 14 |  :warning: |
| score.severity | +0.1711 | 0.458415 | 21 |  :warning: |
| score.break_quality | +0.1661 | 0.340333 | 35 |  :warning: |
| state.short_breakout | +0.0843 | 0.090458 | 404 |  |
| score.htf_confluence | +0.0824 | 0.097979 | 404 |  |
| score.conv_speed | +0.0676 | 0.818422 | 14 |  :warning: |
| state.dec_count | +0.0676 | 0.818422 | 14 |  :warning: |
| score.div_magnitude | +0.0647 | 0.703482 | 37 |  :warning: |
| score.volume_quality | +0.0631 | 0.205735 | 404 |  |
| state.rsi_extreme_low | +0.0615 | 0.717701 | 37 |  :warning: |
| state.donchian_low | +0.0219 | 0.661383 | 404 |  |
| state.prev_donchian_low | +0.0219 | 0.660530 | 404 |  |
| state.donchian_mid | +0.0213 | 0.668786 | 404 |  |
| score.bias_strength | +0.0212 | 0.663544 | 425 |  |
| state.prev_donchian_high | +0.0210 | 0.674421 | 404 |  |
| state.donchian_high | +0.0205 | 0.681762 | 404 |  |

**Top 20 negative:**

| Feature | rho | p-value | N | Low-conf? |
|---------|-----|---------|---|-----------|
| ind.atr | -0.0133 | 0.619650 | 1405 |  |
| ind.clv | -0.0136 | 0.610182 | 1405 |  |
| ind.ema20_slope_atr | -0.0181 | 0.496652 | 1405 |  |
| score.break_strength | -0.0223 | 0.655411 | 404 |  |
| score.participation | -0.0262 | 0.407091 | 1001 |  |
| ind.vwap_distance_atr | -0.0281 | 0.295944 | 1384 |  |
| ind.rsi_slope | -0.0340 | 0.202609 | 1405 |  |
| ind.rsi_sma | -0.0466 | 0.081043 | 1405 |  |
| score.trend_strength | -0.0471 | 0.148662 | 943 |  |
| ind.ma_gap_atr | -0.0512 | 0.054842 | 1405 |  |
| state.rsi_extreme_high | -0.0615 | 0.717701 | 37 |  :warning: |
| ind.rsi | -0.0655 | 0.014123 | 1405 |  |
| ind.sma200_slope_atr | -0.0662 | 0.013124 | 1405 |  |
| state.long_breakout | -0.0843 | 0.090458 | 404 |  |
| ind.bb_width | -0.0879 | 0.000974 | 1405 |  |
| score.rsi_extremity | -0.0910 | 0.592002 | 37 |  :warning: |
| state.bb_width_pctile | -0.1711 | 0.458415 | 21 |  :warning: |
| state.box_high | -0.1909 | 0.407133 | 21 |  :warning: |
| state.box_low | -0.1909 | 0.407133 | 21 |  :warning: |
| score.conv_tight | -0.1912 | 0.512584 | 14 |  :warning: |

### Top correlations with `is_winner`

**Top 20 positive:**

| Feature | rho | p-value | N | Low-conf? |
|---------|-----|---------|---|-----------|
| state.long_allowed | +0.4399 | 0.008179 | 35 |  :warning: |
| state.bias_score | +0.3934 | 0.077663 | 21 |  :warning: |
| score.span_quality | +0.3882 | 0.017571 | 37 |  :warning: |
| score.severity | +0.3404 | 0.131043 | 21 |  :warning: |
| score.conv_speed | +0.2412 | 0.406046 | 14 |  :warning: |
| state.dec_count | +0.2412 | 0.406046 | 14 |  :warning: |
| score.break_quality | +0.1963 | 0.258275 | 35 |  :warning: |
| exit.bars_held | +0.1681 | 0.000000 | 1405 |  |
| state.ma_gap_atr | +0.1177 | 0.688694 | 14 |  :warning: |
| state.box_high | +0.0630 | 0.786220 | 21 |  :warning: |
| state.box_low | +0.0630 | 0.786220 | 21 |  :warning: |
| state.short_breakout | +0.0461 | 0.355022 | 404 |  |
| score.pullback_quality | +0.0399 | 0.224717 | 929 |  |
| score.retrace_depth | +0.0333 | 0.310445 | 929 |  |
| score.volume_quality | +0.0286 | 0.566049 | 404 |  |
| state.rsi_extreme_high | +0.0260 | 0.878512 | 37 |  :warning: |
| score.div_magnitude | +0.0133 | 0.937925 | 37 |  :warning: |
| score.rsi_extremity | +0.0133 | 0.937925 | 37 |  :warning: |
| ind.sma50_slope_atr | +0.0111 | 0.676336 | 1405 |  |
| ind.htf_sma50 | +0.0098 | 0.716633 | 1388 |  |

**Top 20 negative:**

| Feature | rho | p-value | N | Low-conf? |
|---------|-----|---------|---|-----------|
| ind.ema20_slope_atr | -0.0065 | 0.807216 | 1405 |  |
| score.break_strength | -0.0143 | 0.775094 | 404 |  |
| ind.rsi_sma | -0.0165 | 0.537002 | 1405 |  |
| ind.rsi_slope | -0.0210 | 0.430477 | 1405 |  |
| ind.bb_width | -0.0255 | 0.338833 | 1405 |  |
| state.rsi_extreme_low | -0.0260 | 0.878512 | 37 |  :warning: |
| ind.vwap_distance_atr | -0.0280 | 0.298254 | 1384 |  |
| state.retrace_depth_atr | -0.0322 | 0.326672 | 929 |  |
| ind.clv | -0.0338 | 0.206005 | 1405 |  |
| score.participation | -0.0398 | 0.208814 | 1001 |  |
| score.clean_close | -0.0450 | 0.091566 | 1405 |  |
| ind.rsi | -0.0454 | 0.088583 | 1405 |  |
| state.long_breakout | -0.0461 | 0.355022 | 404 |  |
| ind.ma_gap_atr | -0.0465 | 0.081671 | 1405 |  |
| score.bias_strength | -0.0682 | 0.160361 | 425 |  |
| ind.sma200_slope_atr | -0.0730 | 0.006217 | 1405 |  |
| score.trend_strength | -0.0750 | 0.021330 | 943 |  |
| state.squeeze_age | -0.0875 | 0.706011 | 21 |  :warning: |
| score.conv_tight | -0.1177 | 0.688694 | 14 |  :warning: |
| state.bb_width_pctile | -0.3404 | 0.131043 | 21 |  :warning: |

## 3b. Interaction Mining (Decision Trees)

### Winner prediction tree (max_depth=4)

**Top feature importances:**

- `exit.bars_held`: 0.3910
- `ind.sma200_slope_atr`: 0.1312
- `ind.ma_gap_atr`: 0.1142
- `ind.rsi_sma`: 0.0857
- `ind.rsi`: 0.0841
- `state.donchian_low`: 0.0714
- `ind.rsi_slope`: 0.0533
- `score.clean_close`: 0.0360
- `score.participation`: 0.0169
- `ind.vwap_distance_atr`: 0.0163

**Top leaf rules:**

1. **IF** exit.bars_held <= 2.5 AND ind.rsi <= 80.1003 AND ind.rsi_sma <= 54.4845 AND ind.sma200_slope_atr > -0.0752
   **THEN** win_rate=27% (N=243)

2. **IF** exit.bars_held <= 2.5 AND ind.rsi <= 80.1003 AND ind.rsi_sma > 54.4845 AND state.donchian_low <= 0.1548
   **THEN** win_rate=44% (N=642)

3. **IF** exit.bars_held > 2.5 AND exit.bars_held <= 8.5 AND ind.ma_gap_atr <= 6.0209 AND score.clean_close <= 0.631
   **THEN** win_rate=91% (N=67)

4. **IF** exit.bars_held <= 2.5 AND ind.rsi <= 80.1003 AND ind.rsi_sma > 54.4845 AND state.donchian_low > 0.1548
   **THEN** win_rate=23% (N=87)

5. **IF** exit.bars_held > 2.5 AND exit.bars_held <= 8.5 AND ind.ma_gap_atr <= 6.0209 AND score.clean_close > 0.631
   **THEN** win_rate=69% (N=75)

### Stop-loss prediction tree (max_depth=4)

**Top feature importances:**

- `score.break_strength`: 0.6479
- `ind.bb_width`: 0.1323
- `ind.rsi`: 0.1120
- `ind.sma200_slope_atr`: 0.0578
- `ind.ema50_slope_atr`: 0.0270
- `ind.ma_gap_atr`: 0.0229
- `cond.why_0`: 0.0000
- `cond.why_1`: 0.0000
- `cond.why_2`: 0.0000
- `cond.why_3`: 0.0000

**Top leaf rules:**

1. **IF** score.break_strength <= 0.9283 AND score.break_strength > 0.9181 AND ind.bb_width > 0.0233 AND ind.sma200_slope_atr > -0.0876
   **THEN** stop_loss_rate=6% (N=709)

2. **IF** score.break_strength <= 0.9283 AND score.break_strength > 0.9181 AND ind.bb_width <= 0.0233 AND ind.rsi > 65.9377
   **THEN** stop_loss_rate=9% (N=122)

3. **IF** score.break_strength <= 0.9283 AND score.break_strength > 0.9181 AND ind.bb_width <= 0.0233 AND ind.rsi <= 65.9377
   **THEN** stop_loss_rate=33% (N=147)

4. **IF** score.break_strength > 0.9283 AND ind.rsi > 73.4134 AND ind.ma_gap_atr <= 2.9508
   **THEN** stop_loss_rate=85% (N=39)

5. **IF** score.break_strength <= 0.9283 AND score.break_strength <= 0.9181 AND ind.sma200_slope_atr <= -0.0323
   **THEN** stop_loss_rate=18% (N=33)

## 3c. Trade Archetypes (K-Means Clustering)

**Best k=4** (silhouette=0.364)

| Cluster | Name | Count | WR | Avg PnL | Avg R | Dom. Exit | Dom. Setup | Top Features |
|---------|------|-------|----|---------|-------|-----------|------------|--------------|
| 0 | Mixed Target-Hit (S6) | 1038 | 42.0% | -1.4 | -0.20 | TP1 | S6 | ind.rsi(z=0.38), state.htf_bias_score(z=0.38), ind.clv(z=0.38) |
| 1 | Winners Target-Hit (S6) | 12 | 75.0% | 2.7 | 0.19 | TP1 | S6 | ind.sma_200(z=10.75), ind.ema_50(z=10.75), ind.bb_ub(z=10.75) |
| 2 | Mixed Quick-Death (S6) | 213 | 37.6% | -2.9 | -0.28 | FAIL_FAST | S6 | ind.rsi(z=2.07), state.htf_bias_score(z=2.06), ind.ema50_slope_atr(z=1.94) |
| 3 | Mixed Quick-Death (S6) | 142 | 40.1% | -2.6 | -0.13 | FAIL_FAST | S6 | score.pullback_quality(z=2.81), score.retrace_depth(z=2.45), state.retrace_depth_atr(z=2.2) |

## 4a. Exit Predictability

### STOP_LOSS — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| score.clean_close | +0.2770 | 0.000000 | 1405 |
| score.severity | -0.2559 | 0.262824 | 21 |
| state.bb_width_pctile | +0.2559 | 0.262824 | 21 |
| state.box_high | -0.2411 | 0.292395 | 21 |
| state.box_low | -0.2411 | 0.292395 | 21 |

### FAIL_FAST — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| exit.bars_held | -0.2462 | 0.000000 | 1405 |
| state.long_allowed | -0.2322 | 0.179530 | 35 |
| score.clean_close | -0.2129 | 0.000000 | 1405 |
| state.bias_score | -0.2106 | 0.359487 | 21 |
| score.bias_strength | -0.1943 | 0.000055 | 425 |

### TP1 — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| state.long_allowed | +0.3111 | 0.068911 | 35 |
| score.span_quality | +0.2910 | 0.080606 | 37 |
| exit.bars_held | +0.1867 | 0.000000 | 1405 |
| score.clean_close | -0.1637 | 0.000000 | 1405 |
| score.bias_strength | -0.1488 | 0.002106 | 425 |

### TP2 — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| score.break_strength | -0.1183 | 0.017407 | 404 |
| score.participation | +0.0627 | 0.047312 | 1001 |
| ind.rsi | -0.0602 | 0.024073 | 1405 |
| score.clean_close | +0.0599 | 0.024817 | 1405 |
| ind.sma200_slope_atr | -0.0576 | 0.030823 | 1405 |

### TRAIL_STOP — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| score.severity | +0.5118 | 0.017696 | 21 |
| state.bb_width_pctile | -0.5118 | 0.017696 | 21 |
| state.bias_score | +0.2910 | 0.200583 | 21 |
| score.div_magnitude | -0.2592 | 0.121371 | 37 |
| state.squeeze_age | -0.2166 | 0.345687 | 21 |

### TIME_STOP — top feature predictors at entry

| Feature | rho | p-value | N |
|---------|-----|---------|---|
| state.squeeze_age | +0.5565 | 0.008785 | 21 |
| exit.bars_held | +0.4600 | 0.000000 | 1405 |
| state.long_allowed | -0.4355 | 0.008929 | 35 |
| score.severity | -0.3021 | 0.183238 | 21 |
| state.bb_width_pctile | +0.3021 | 0.183238 | 21 |

## 4b. Time-in-Trade Analysis

### Bars held by exit reason

| Exit Reason | Count | Min | P25 | Median | P75 | Max | Mean |
|-------------|-------|-----|-----|--------|-----|-----|------|
| TP1 | 373 | 1 | 1.0 | 1.0 | 4.0 | 26 | 2.8 |
| STOP_LOSS | 314 | 1 | 1.0 | 1.0 | 1.0 | 58 | 2.0 |
| FAIL_FAST | 386 | 1 | 1.0 | 1.0 | 1.0 | 2 | 1.2 |
| TIME_STOP | 80 | 5 | 9.0 | 9.0 | 9.0 | 100 | 9.9 |
| TRAIL_STOP | 228 | 1 | 1.0 | 1.0 | 2.0 | 49 | 2.5 |
| TP2 | 24 | 1 | 1.0 | 1.0 | 1.2 | 6 | 1.5 |

### Instant death trades (bars_held <= 2 AND STOP_LOSS/FAIL_FAST)

**656 trades (46.7%)** exit within 2 bars via stop or fail-fast.

**Statistically significant feature differences (instant death vs rest):**

| Feature | Instant Median | Rest Median | p-value |
|---------|---------------|-------------|---------|
| exit.bars_held | 1.0 | 1.0 | 0.000000 |
| ind.bb_width | 0.0362 | 0.0329 | 0.000083 |
| ind.rsi | 69.4523 | 68.4454 | 0.007343 |
| score.break_strength | 0.9602 | 0.8353 | 0.021109 |
| score.bias_strength | 1.0 | 1.0 | 0.049665 |

## 4c. TP1 vs TP2 Analysis

- TP1 exits: 373
- TP2 exits: 24
- **TP2 hit rate:** 6.0% of profitable exits reached TP2

**Features differentiating TP1 vs TP2 exits:**

| Feature | rho (with TP2) | p-value | N |
|---------|---------------|---------|---|
| score.break_strength | -0.2666 | 0.121642 | 35 |
| score.volume_quality | -0.1816 | 0.296591 | 35 |
| score.clean_close | +0.1760 | 0.000425 | 397 |
| state.long_breakout | +0.1368 | 0.433280 | 35 |
| state.short_breakout | -0.1368 | 0.433280 | 35 |
| ind.clv | +0.1312 | 0.008856 | 397 |
| exit.bars_held | -0.1210 | 0.015845 | 397 |
| score.bias_strength | +0.1186 | 0.448951 | 43 |
| score.participation | +0.1159 | 0.027440 | 362 |
| ind.sma200_slope_atr | -0.1032 | 0.039769 | 397 |

## 5. Segment Deep Dives

### By Setup

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| S7 | 404 | 32.4% | [28.1%, 37.1%] | -1.2 | -0.44 | 11.6 | -7.4 | -493 |
| S3 | 37 | 35.1% | [21.8%, 51.2%] | -5.4 | -0.26 | 5.7 | -11.4 | -199 |
| S6 | 929 | 44.9% | [41.7%, 48.1%] | -1.9 | -0.10 | 9.8 | -11.4 | -1740 |
| S1 | 21 | 52.4% | [32.4%, 71.7%] | -1.0 | -0.06 | 6.3 | -9.1 | -21 |
| S2 | 14 | 71.4% | [45.4%, 88.3%] | 4.1 | 0.10 | 9.6 | -9.5 | 58 |

### By Setup × Timeframe

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| S3/1h | 5 | 20.0% | [3.6%, 62.5%] | -4.9 | -0.13 | 20.2 | -11.2 | -25 |
| S7/15m | 277 | 28.9% | [23.9%, 34.5%] | -2.6 | -0.60 | 8.1 | -7.0 | -725 |
| S3/15m | 31 | 38.7% | [23.7%, 56.2%] | -4.3 | -0.27 | 4.5 | -9.9 | -134 |
| S7/1h | 113 | 39.8% | [31.3%, 49.0%] | 3.0 | -0.09 | 18.2 | -7.1 | 334 |
| S7/4h | 14 | 42.9% | [21.4%, 67.4%] | -7.3 | -0.15 | 8.6 | -19.2 | -102 |
| S6/15m | 662 | 44.4% | [40.7%, 48.2%] | -1.5 | -0.10 | 9.3 | -10.2 | -1019 |
| S6/1h | 267 | 46.1% | [40.2%, 52.1%] | -2.7 | -0.10 | 11.0 | -14.4 | -721 |
| S1/15m | 14 | 50.0% | [26.8%, 73.2%] | -0.9 | -0.14 | 4.9 | -6.7 | -12 |
| S1/1h | 7 | 57.1% | [25.1%, 84.2%] | -1.3 | 0.09 | 8.7 | -14.6 | -9 |
| S2/1h | 7 | 71.4% | [35.9%, 91.8%] | 2.6 | 0.01 | 6.8 | -8.1 | 18 |
| S2/15m | 5 | 100.0% | [56.5%, 100.0%] | 12.4 | 0.37 | 12.4 | 0.0 | 62 |

### By Direction

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| short | 205 | 40.0% | [33.5%, 46.8%] | -2.7 | -0.25 | 6.6 | -8.9 | -551 |
| long | 1200 | 41.7% | [38.9%, 44.5%] | -1.5 | -0.19 | 10.6 | -10.2 | -1844 |

### By Day of Week

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| Sunday | 140 | 26.4% | [19.8%, 34.3%] | -6.2 | -0.55 | 11.6 | -12.6 | -863 |
| Tuesday | 93 | 37.6% | [28.5%, 47.8%] | -2.3 | -0.17 | 7.6 | -8.2 | -210 |
| Thursday | 198 | 39.4% | [32.9%, 46.3%] | -3.1 | -0.38 | 7.3 | -9.8 | -607 |
| Saturday | 524 | 40.1% | [36.0%, 44.3%] | -2.5 | -0.24 | 7.7 | -9.3 | -1307 |
| Monday | 77 | 46.8% | [36.0%, 57.8%] | -0.1 | -0.09 | 10.6 | -9.4 | -5 |
| Friday | 240 | 48.3% | [42.1%, 54.6%] | 0.5 | -0.12 | 10.8 | -9.1 | 120 |
| Wednesday | 133 | 52.6% | [44.2%, 60.9%] | 3.6 | 0.34 | 18.8 | -13.3 | 476 |

### By Hour Bucket (UTC)

| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |
|---------|-------|----|---------------|---------|-------|---------|----------|-----------|
| 20-24 | 168 | 35.1% | [28.3%, 42.6%] | 1.0 | -0.12 | 20.6 | -9.7 | 161 |
| 08-12 | 356 | 39.0% | [34.1%, 44.2%] | -3.5 | -0.27 | 8.0 | -10.8 | -1233 |
| 00-04 | 198 | 39.4% | [32.9%, 46.3%] | -2.7 | -0.25 | 8.6 | -10.0 | -534 |
| 04-08 | 233 | 42.9% | [36.7%, 49.3%] | -1.9 | -0.25 | 9.1 | -10.2 | -449 |
| 12-16 | 218 | 43.1% | [36.7%, 49.8%] | -1.3 | -0.15 | 10.9 | -10.6 | -286 |
| 16-20 | 232 | 48.3% | [41.9%, 54.7%] | -0.2 | -0.11 | 8.1 | -8.0 | -55 |

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

### I09: 47% of trades die within 2 bars

- **Explicit:** `exit_checker.py:101-107 (FAIL_FAST logic)`
- `exit_checker.py:101-107` — Exits if close moves 0.3% against entry within first 2 bars
  - Params: fail_fast_bars=2, fail_fast_threshold=0.003
- `exit_checker.py:~70-99` — Hard stop at setup-computed stop_price (checked first in priority)

### I18: Underperformer: S7/15m (29% WR)

- `setups/s7_momentum_breakout.py` — Momentum Breakout (Donchian): break_strength, volume_quality, bias_strength, clean_close, htf_confluence

### I19: Underperformer: Sunday (26% WR)

- *(no specific code path identified)*

### I20: MAE/MFE data not available — bar-by-bar tracking needed

- **Explicit:** `opportunity_simulator.py:611-676 (trace construction)`
- `exit_checker.py:155-164` — Target 1 hit (setup-specific distance from entry)
- `exit_checker.py:167-175` — Target 2 hit (extended target, setup-specific)

### I05: Best winner rule: exit.bars_held <= 2.5 AND ind.rsi <= 80.1003

- *(no specific code path identified)*

### I06: High stop-loss rule: score.break_strength > 0.9283 AND ind.rsi > 73.4134

- **Explicit:** `exit_checker.py:101-107 (FAIL_FAST), exit_checker.py:131-153 (TRAIL_STOP)`
- `signal_engine.py:620-794` — Weighted score aggregation → rank; serves ranked list to API

### I08: Worst archetype: 'Mixed Quick-Death (S6)' (38% WR)

- `exit_checker.py:101-107` — Exits if close moves 0.3% against entry within first 2 bars
  - Params: fail_fast_bars=2, fail_fast_threshold=0.003
- `setups/s6_trend_pullback.py` — Trend Pullback setup: trend_strength, pullback_quality, retrace_depth, volume_quality, htf_confluence
- `signal_engine.py:620-794` — Weighted score aggregation → rank; serves ranked list to API

### I07: Best archetype: 'Winners Target-Hit (S6)' (75% WR)

- `exit_checker.py:155-164` — Target 1 hit (setup-specific distance from entry)
- `exit_checker.py:167-175` — Target 2 hit (extended target, setup-specific)
- `setups/s6_trend_pullback.py` — Trend Pullback setup: trend_strength, pullback_quality, retrace_depth, volume_quality, htf_confluence

### I10: TP2 hit rate: 6% of profitable exits reach TP2

- **Explicit:** `exit_checker.py:155-175 (TP1/TP2 logic)`
- `exit_checker.py:155-164` — Target 1 hit (setup-specific distance from entry)
- `exit_checker.py:167-175` — Target 2 hit (extended target, setup-specific)

### I17: Outperformer: S2 (71% WR)

- `setups/s2_golden_cross.py` — Golden Cross setup: conv_speed, conv_tight, trend_strength, participation, clean_close

## 8. Next Experiments

### E1: Relax FAIL_FAST threshold for setups with high FAIL_FAST rate

- **Module:** `exit_checker.py`
- **Change:** Increase fail_fast_threshold from 0.003 (0.3%) to 0.005 (0.5%) for S6 and S7
- **Rationale:** FAIL_FAST exits 386 trades (27.5% of all). Some may be noise-triggered — widening the threshold should reduce false exits.
- **Baseline:** FAIL_FAST rate: 27.5%, overall WR: 41.4%
- **Success metric:** FAIL_FAST rate, overall win rate, profit factor

### E2: Add RSI filter for S7/15m

- **Module:** `setups/s7_*.py (evaluate_trigger)`
- **Change:** Gate S7/15m entries on RSI not being extreme against direction (e.g., RSI > 70 blocks long, RSI < 30 blocks short)
- **Rationale:** S7/15m has 28.9% WR on 277 trades (vs 41.4% overall). Many may be late-momentum entries.
- **Baseline:** S7/15m: WR=28.9%, N=277, PnL=-725.29
- **Success metric:** S7/15m win rate and total PnL

### E3: Reduce time_stop_bars for S7 from 6 to 4

- **Module:** `setups/s7_momentum_breakout.py (metadata.time_stop_bars)`
- **Change:** Reduce S7 time_stop_bars from 6 to 4. Momentum breakouts should resolve quickly.
- **Rationale:** Momentum breakouts that don't move within 4 bars are likely failed breakouts. Holding longer just delays the inevitable stop-loss.
- **Baseline:** TIME_STOP stats: {'count': 80, 'min': 5, 'p25': 9.0, 'median': 9.0, 'p75': 9.0, 'max': 100, 'mean': 9.9}
- **Success metric:** S7 profit factor, TIME_STOP count, avg holding period

### E4: Raise score threshold for short entries

- **Module:** `signal_engine.py (ranking/filtering)`
- **Change:** Require opportunity_score >= 0.75 for short entries (vs current universal threshold)
- **Rationale:** Shorts: WR=40.0% (N=205) vs Longs: WR=41.7% (N=1200). Higher bar for shorts should filter out weak setups.
- **Baseline:** Short PnL=-551.39, Long PnL=-1844.09
- **Success metric:** Short win rate, short profit factor, overall PnL

### E5: Require volume_z >= 1.5 for all entries

- **Module:** `opportunity_simulator.py (entry gates) or setup evaluate_trigger`
- **Change:** Add global gate: reject opportunities where ind.volume_z < 1.5 at entry
- **Rationale:** Volume confirmation is a score component but not a hard gate. Low-volume entries may lack the participation needed for follow-through.
- **Baseline:** Overall WR: 41.4%, PF: 0.71
- **Success metric:** Win rate, profit factor, trade count (ensuring we don't filter too aggressively)

## 9. What We Can't See Yet

1. **MAE/MFE (bar-by-bar excursion tracking)** — Cannot assess whether stops are too tight or targets too conservative without knowing the intra-trade path. See Section 6 for implementation proposal.

2. **Market regime at entry time** — BTC trend, overall market volatility regime, funding rates. Trades entered during high-correlation risk-off events may have structurally different outcomes. Would require adding BTC price/regime data to trace_json.

3. **Slippage model accuracy** — The simulator applies a fixed slippage model. Without live fill data to compare against, we cannot validate whether the model is optimistic or pessimistic. Requires live trading data.

4. **Multi-trade correlation** — When the same symbol fires multiple setups in close succession, are the outcomes correlated? Current analysis treats each trade independently. Would need time-proximity grouping of trades on the same symbol.

5. **Order book depth / liquidity at entry** — Position sizing assumes sufficient liquidity. For microcaps (already filtered by the microcap gate), the actual fill quality depends on book depth not captured in OHLCV data.
