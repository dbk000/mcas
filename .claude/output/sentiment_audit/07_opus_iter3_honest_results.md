# Opus 4.6 Forward Predictor Discovery — Iteration 3 Results (Fixed Costs)

**Date:** 2026-02-22
**Data:** 166K OHLCV bars (1h) × 20 symbols × 1 year + 53K social feature rows
**Models:** GBStumps (200 stumps), Cross-sectional rank L/S (9 features), Interactions (6 pairs), Regime-conditional
**Validation:** 5-fold purged walk-forward, 24-bar embargo, DSR correction
**Cost model:** 23 bps per leg (slippage 5 + fee 4 + XSM 2.5, both sides) — CORRECTLY APPLIED

---

## Critical Bug Fix From Iteration 2

Iter2 had two cost model bugs that made results look better than reality:

1. **L/S cost cancellation:** Embedding costs in the target return caused them to cancel in dollar-neutral portfolios. `long_ret = gross - cost` and `short_ret = -(gross - cost)` → costs vanish in the L/S spread. Iter2 XS L/S results were IDENTICAL across all cost multipliers.

2. **Short cost bonus:** For directional GBStumps, `pnl = sign(pred) × (gross - cost)` means shorts get `pnl = -gross + cost` — the cost is ADDED to short PnL. In a net-short market, higher costs = higher PF. Iter2 GBStumps PF went UP with higher costs (1.36 → 1.53 → 1.76).

Iter3 fixes both: `pnl = direction × gross_ret - cost_per_leg` (always subtract).

---

## Iteration 3 Grand Summary (ALL FAIL)

| Config | Best Strategy | Avg Daily PF | Aggregate PF | Sharpe | Mean PnL (bps) | DSR |
|--------|-------------|-------------|-------------|--------|----------------|-----|
| h24 base | gbstumps | 3.675 | 0.839 | -0.061 | -26.6 | -1.253 |
| h24 1.5x | gbstumps | 3.267 | 0.778 | -0.087 | -38.1 | -1.279 |
| h24 2x | gbstumps | 2.905 | 0.721 | -0.113 | -49.6 | -1.306 |
| h6 base | gbstumps | 0.999 | 0.746 | -0.097 | -21.1 | -1.290 |
| h6 1.5x | gbstumps | 0.825 | 0.636 | -0.150 | -32.6 | -1.342 |
| h6 2x | gbstumps | 0.687 | 0.544 | -0.203 | -44.1 | -1.395 |
| h72 base | contrarian_social | 9.602 | 0.913 | -0.030 | -7.4 | -1.223 |
| h72 1.5x | gbstumps | 17.213 | 0.808 | -0.076 | -56.7 | -1.269 |
| h72 2x | gbstumps | 12.631 | 0.774 | -0.092 | -68.2 | -1.284 |

**Every configuration has aggregate PF < 1.0 and negative mean PnL after costs.**

---

## What The Data Says

### 1. Costs dominate the signal
At 23 bps per leg (base), the best strategy generates -7.4 bps/trade. The signal exists but it's too small (~15 bps gross) to overcome friction. At 6h rebalancing with 20 symbols × 2 legs, the cost drag is massive.

### 2. Longer horizons are closer to breakeven
- h6: Aggregate PF 0.75 (deep underwater)
- h24: Aggregate PF 0.84 (significant drag)
- h72: Aggregate PF 0.91 (nearly breakeven)

The pattern is clear: less frequent trading = less cost drag = closer to profitability. The 72h contrarian social composite at base cost is only 7.4 bps underwater.

### 3. Social features ARE informative — but insufficient alone
GBStumps feature importance (consistent across all horizons):
1. ret_24h (0.25) — past return momentum
2. ret_72h (0.18) — multi-day momentum
3. ret_6h (0.18) — short-term momentum
4. idiosyncratic_ret_24h (0.10) — altcoin-specific return vs BTC
5. cs_rank_ret_72h (0.06) — cross-sectional rank
6. **cs_z_mentions_6h (0.04)** — social mentions z-score
7. **attention_price_divergence (0.07 at 24h)** — social-price coupling

Social features rank #6-7 by importance. They add information but don't provide enough edge to cover costs.

### 4. The "average daily PF" illusion persists
Many strategies show avg daily PF > 1.0 (even 10-60+!) while aggregate PF < 1.0. This means: on most days, the strategy has a small edge. But on the few catastrophic days, the losses overwhelm all gains. The edge is real but inconsistent — the tail risk destroys it.

---

## Honest Assessment for MCAS Equities

**Current state: No tradeable edge has been found that passes CLAUDE.md Section 4 validation.**

What we've tested:
- 3 model types: Ridge regression (iter1), GBStumps non-linear (iter3), cross-sectional rank L/S (iter3)
- 41 features from first principles (not named indicators)
- 9 ranking strategies + 6 interaction pairs + regime-conditional
- 3 horizons × 3 cost multipliers = 9 configurations × 17+ strategies = 765 total jobs × 5 folds = 3,825 evaluations
- All with purged walk-forward, DSR correction, correct transaction costs

**The data is NOT useless — the infrastructure works, the validation is rigorous, and we now know EXACTLY where the edge boundary is:**

| Path Forward | What It Means |
|-------------|---------------|
| Reduce trading frequency | 72h is nearly breakeven; weekly rebalancing might work |
| Increase conviction threshold | Only trade when social signal is extreme (>2σ), reducing turnover |
| Expand universe | More symbols = better L/S diversification, tighter spreads |
| Wait for market regime | Social signals may be stronger in trending/volatile markets |
| Use as filter, not signal | Use social features to filter existing MCAS crypto setups (S1/S2) |

---

## Performance Comparison: Iter2 (Buggy) vs Iter3 (Correct)

| Metric | Iter2 (buggy costs) | Iter3 (fixed costs) |
|--------|-------------------|-------------------|
| h6 GBStumps AggPF | 1.36 (fake) | 0.75 (real) |
| h24 GBStumps AggPF | 1.11 (fake) | 0.84 (real) |
| h72 GBStumps AggPF | N/A (still running) | 0.84 (real) |
| XS L/S best AggPF | 1.16 (costs cancelled) | 0.91 (h72 contrarian, real) |
| Wall clock | ~17 min | 155 seconds |
| CPU utilization | ~200% (2 cores) | ~950% (10 cores) |

*Per CLAUDE.md Section 0.2 Rule 5: "Honest reporting always." No tradeable edge found after costs. The methodology is sound. The signal exists but is too small for hourly crypto L/S at current fee levels.*
