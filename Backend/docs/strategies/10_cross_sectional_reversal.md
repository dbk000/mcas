# Cross-Sectional Reversal in Illiquid Coins
> Strategy #10 from MCAS Research Sweep (2026-03)

## Edge

Illiquid cryptocurrency assets exhibit stronger short-term reversal than liquid ones. Coins in the bottom decile of recent returns (1-7 day lookback) tend to bounce, while top-decile coins tend to pull back. This reversal effect is 2-5x stronger in illiquid assets compared to large-caps, because: (1) illiquid coins overshoot more due to thin order books, (2) mean-reversion arbitrageurs avoid illiquid assets (leaving the opportunity), and (3) retail overreaction is more pronounced in low-attention assets. A cross-sectional approach — ranking all assets by recent return and going long the losers — captures this systematically.

## Data Requirements

- OHLCV candles (1h to 1d)
- Cross-sectional universe of 100+ assets for ranking
- 24h quote volume for liquidity classification
- No external data needed

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| lookback_period | 1d to 7d | 3d | Return measurement window |
| holding_period | 1d to 7d | 3d | Position holding time |
| n_long | 5 to 20 | 10 | Number of bottom-ranked assets to buy |
| liquidity_filter | bottom 50% to bottom 25% | bottom 33% | Restrict to illiquid segment |
| rebalance_frequency | 1d to 7d | 1d | How often to re-rank and rebalance |
| min_volume_usd | 50k to 500k | 100k | Minimum daily volume for tradability |

## MCAS Mapping

- **Closest existing setup:** S9 (cross-sectional momentum — opposite direction but similar infrastructure)
- **Required indicators:** N-day returns, 24h volume ranking, cross-sectional percentile ranks
- **Timeframe affinity:** 4h, 1d (reversal is a multi-day phenomenon)
- **Universe filter:** Bottom third of monitored universe by 24h volume (illiquidity amplifies the effect)

## References

- Academic crypto factor research (2020-2024) documenting short-term reversal in cryptocurrency markets. Key finding: the reversal factor is strongest in the lowest liquidity quintile, with weekly returns of 1-3% for long-only bottom-decile portfolios. The effect weakens but persists after transaction costs when restricted to assets with >$100k daily volume.

## Implementation Notes

MCAS already maintains a ranked monitored universe sorted by volume — the infrastructure for cross-sectional ranking exists. The main implementation gap is the rebalancing mechanism: current MCAS setups generate point-in-time signals, not portfolio-level rebalance instructions. A reversal strategy requires periodic re-ranking of the entire universe and position adjustment, which is architecturally different from the current signal-per-asset model. Could be implemented as a "portfolio setup" that emits a ranked list rather than individual signals.

## Status

- **MCAS Fit Rating:** 4/5
- **Implementation:** Not started
- **Validated:** No
