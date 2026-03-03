# Factor Momentum (Cross-Sectional)
> Strategy #11 from MCAS Research Sweep (2026-03)

## Edge

Academic research documents a cross-sectional momentum factor in cryptocurrency markets yielding approximately 1.65% per week in backtests. The strategy ranks all assets by past-week returns and goes long the top quintile while shorting the bottom quintile. The momentum effect in crypto is stronger and more persistent than in equities, likely because: (1) crypto markets have fewer institutional arbitrageurs, (2) information diffusion is slower across the fragmented crypto ecosystem, and (3) retail herding amplifies trends. The effect survives transaction costs at weekly rebalancing frequency but decays at daily frequency due to higher turnover costs.

## Data Requirements

- OHLCV candles (1d or 4h for weekly return calculation)
- Cross-sectional universe of 50+ assets
- 24h volume for tradability screening
- **Short-selling capability** for the hedge leg (or long-only variant at reduced Sharpe)
- No external data needed

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| ranking_lookback | 3d to 14d | 7d | Return measurement period |
| holding_period | 3d to 14d | 7d | Position holding time |
| n_quintile | top/bottom 10% to 20% | 20% | Fraction of universe in each leg |
| rebalance_freq | 1d to 7d | 7d | Rebalancing frequency |
| volume_floor | 100k to 1M USD | 250k | Min daily volume for inclusion |
| max_position_pct | 5% to 20% | 10% | Max portfolio weight per asset |

## MCAS Mapping

- **Closest existing setup:** S9 (cross-sectional momentum, similar concept at shorter timeframe)
- **Required indicators:** N-day returns, cross-sectional percentile ranks
- **Timeframe affinity:** 1d (weekly rebalancing is the sweet spot)
- **Universe filter:** Top 200 by volume (need sufficient liquidity for weekly rebalancing)

## References

- Academic crypto momentum papers (2020-2024). Key result: 1.65%/week long-short return in backtests across 2017-2023. The effect is robust to different lookback periods (5-14 days) and universe definitions. Sharpe ratio approximately 2.0 annualised before costs, 1.2-1.5 after costs depending on exchange fee tier.

## Implementation Notes

Two significant barriers for MCAS: (1) **Short capability.** The full long-short strategy requires Binance margin or futures, which MCAS does not currently integrate. A long-only variant (buy top quintile only) captures roughly 60% of the long-short returns but has higher drawdowns and beta exposure. (2) **Rebalancing infrastructure.** Like Strategy #10, this requires portfolio-level management rather than individual signals. The weekly rebalancing frequency is actually easier to implement than daily — a scheduled job could re-rank and emit portfolio targets once per week. If MCAS adds futures/short capability, this becomes a natural portfolio-level strategy.

## Status

- **MCAS Fit Rating:** 3/5
- **Implementation:** Not started
- **Validated:** No
