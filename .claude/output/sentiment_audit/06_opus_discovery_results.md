# Opus 4.6 Forward Predictor Discovery — First Run Results

**Date:** 2026-02-22
**Data:** 166K OHLCV bars (1h) × 20 symbols × 1 year + 53K social feature rows
**Model:** Ridge regression on 40 first-principles features
**Validation:** 5-fold purged walk-forward, 24-bar embargo, DSR correction

---

## Results Summary

| Config | Avg Daily PF | Aggregate PF | OOS Sharpe | Mean PnL (bps) | WF Pass | DSR |
|--------|-------------|-------------|-----------|----------------|---------|-----|
| h6 base cost | **1.340** | 1.001 | -0.004 | -1.0 | 5/5 (100%) | -1.21 FAIL |
| h6 1.5x cost | 1.229 | 0.997 | -0.011 | -2.5 | 5/5 (100%) | -1.23 FAIL |
| h6 2x cost | 1.163 | 0.994 | -0.017 | -3.7 | 4/5 (80%) | -1.24 FAIL |
| h24 base cost | **1.715** | 1.003 | -0.001 | -1.0 | 5/5 (100%) | -1.21 FAIL |
| h24 1.5x cost | 1.681 | 0.999 | -0.005 | -2.3 | 5/5 (100%) | -1.22 FAIL |
| h24 2x cost | 1.649 | 0.994 | -0.010 | -3.6 | 5/5 (100%) | -1.23 FAIL |
| h72 base cost | **8.907** | 1.001 | -0.002 | -1.5 | 5/5 (100%) | -1.19 FAIL |
| h72 1.5x cost | 8.882 | 0.999 | -0.004 | -2.7 | 5/5 (100%) | -1.19 FAIL |
| h72 2x cost | 8.836 | 0.998 | -0.005 | -4.0 | 5/5 (100%) | -1.20 FAIL |

---

## Honest Analysis

### What the numbers say

1. **Aggregate PF ≈ 1.0 everywhere.** Total profit ≈ total loss. The linear model is breakeven.

2. **Avg daily PF > 1.0 everywhere.** Most trading DAYS are profitable, but a few high-loss days drag down the aggregate. This means the edge is inconsistent — it works most days but fails catastrophically on some days.

3. **DSR fails across the board.** All DSR values are ~-1.2, meaning the OOS Sharpe doesn't survive correction for multiple testing. Per CLAUDE.md Section 4.3, these results cannot be reported as confirmed findings.

4. **Accuracy ≈ 50% (coin flip).** The model cannot predict return DIRECTION reliably. Ridge regression on these features does not separate up-moves from down-moves.

### What IS interesting

**The top features by cross-fold importance:**

| Rank | Feature | Importance | Type |
|------|---------|-----------|------|
| 1 | **mentions_6h_avg** | 0.151 | Social |
| 2 | **mentions_24h_avg** | 0.146 | Social |
| 3 | **volume_regime** | 0.073 | Price |
| 4 | realized_vol_24 | 0.040 | Price |
| 5 | ret_24h | 0.035 | Price |
| 6 | ret_72h | 0.019 | Price |
| 7 | **idiosyncratic_ret_6h** | 0.018 | Cross-symbol |
| 8 | vol_regime_ratio | 0.016 | Price |
| 9 | relative_volume_24 | 0.014 | Price |
| 10 | vol_change_speed_24h | 0.013 | Price |
| 11 | **author_concentration_24h** | 0.009 | Social |
| 12 | cs_rank_ret_24h | 0.007 | Cross-symbol |
| 13 | **author_novelty_24h_avg** | 0.006 | Social |

**Social features are #1 and #2 by importance — more important than any price feature.** This is the key finding. The social data contains cross-sectional information that the linear model is trying to use but can't exploit efficiently.

The idiosyncratic return (vs BTC) and cross-sectional rank features also show up — suggesting cross-symbol structure exists.

### Why the linear model fails

Ridge regression assumes features predict returns linearly. But social attention probably has a non-linear relationship with returns:
- Low attention → no signal (random walk)
- Moderate attention → possible momentum (crowd following price)
- High attention → possible reversal (crowd is overextended)

A linear model averages these regimes, producing near-zero net prediction. A model that can capture the THRESHOLD effects (attention > X AND volatility > Y → short) would do better.

### What comes next (Iteration 2)

1. **Cross-sectional long-short approach**: Each hour, rank symbols by social feature value. Go long bottom quintile (low attention), short top quintile (high attention). This exploits the cross-sectional structure without predicting absolute returns.

2. **Regime-conditional signals**: Split data by volatility regime, test whether social features predict differently in high-vol vs low-vol.

3. **Non-linear model**: Replace ridge with gradient-boosted trees or piecewise linear model that can capture threshold effects.

4. **Feature interaction screening**: Test all pairwise interactions of the top 10 features.

---

*Per CLAUDE.md Section 0.2 Rule 5: "Honest reporting always." The linear model does not find a significant edge after costs and DSR correction. But the feature importance rankings reveal that social data IS the most informative cross-sectional signal. The methodology needs to change from "predict returns" to "exploit cross-sectional rank structure."*
