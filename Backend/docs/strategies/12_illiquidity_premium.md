# Illiquidity Premium / Anomaly Amplification
> Strategy #12 from MCAS Research Sweep (2026-03)

## Edge

Standard market anomalies (momentum, reversal, volatility, value) are 2-5x stronger in illiquid cryptocurrency assets compared to liquid large-caps. This is not a separate strategy but a **meta-finding** that amplifies all other strategies when applied to the right universe segment. The mechanism: illiquid assets have wider bid-ask spreads, thinner order books, and fewer sophisticated participants — creating larger mispricings that persist longer. The illiquidity premium itself (holding illiquid assets earns a return premium over liquid ones) adds to the base anomaly return. MCAS already operates primarily in the illiquid segment of the crypto universe, which means it is naturally positioned to benefit from this amplification effect.

## Data Requirements

- OHLCV candles (any timeframe)
- 24h quote volume (already available in MCAS)
- Bid-ask spread estimates (can be approximated from OHLCV high-low range)
- Amihud illiquidity ratio: |return| / dollar volume
- No external data needed

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| illiquidity_measure | volume, amihud, spread_proxy | amihud | Method to classify liquidity |
| liquidity_quintile | bottom 20% to bottom 50% | bottom 33% | Universe segment to focus on |
| amihud_window | 5d to 30d | 20d | Window for Amihud ratio calculation |
| spread_proxy_method | hl_range, close_to_close | hl_range | High-low range as spread proxy |
| min_volume_floor | 10k to 100k USD | 50k | Absolute minimum for tradability |
| rebalance_freq | 1d to 7d | 7d | How often to re-classify liquidity tiers |

## MCAS Mapping

- **Closest existing setup:** All setups benefit; universe filtering already favours illiquid assets
- **Required indicators:** Amihud illiquidity ratio, 24h volume percentile rank
- **Timeframe affinity:** Any (meta-property, not timeframe-specific)
- **Universe filter:** This IS a universe filter — restrict to bottom third by liquidity

## References

- Academic research on illiquidity premium in cryptocurrency markets (2021-2024). Key finding: anomaly returns in the bottom liquidity quintile are 2-5x those in the top quintile, after controlling for transaction costs. The Amihud (2002) illiquidity ratio, originally developed for equities, translates directly to crypto with even stronger effects due to the wider range of liquidity conditions.

## Implementation Notes

This is a **universal enhancement**, not a standalone strategy. Implementation path: (1) compute Amihud illiquidity ratio as a standard indicator for all monitored assets, (2) add liquidity tier classification (quintiles or terciles) to the asset metadata, (3) use liquidity tier as a filter or feature in existing setups — for example, D1 signals on illiquid assets get higher confidence scores. The most important insight is that MCAS's existing universe (MIN_24H_QUOTE_VOLUME_USD = $5M) already selects for moderate illiquidity. Tightening this further for specific strategies (e.g., D1 focusing on bottom tercile within the monitored universe) could amplify signal quality.

## Status

- **MCAS Fit Rating:** 4/5
- **Implementation:** Not started
- **Validated:** No
