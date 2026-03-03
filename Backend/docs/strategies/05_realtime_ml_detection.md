# Real-time ML Detection Pipeline
> Strategy #05 from MCAS Research Sweep (2026-03)

## Edge

An ensemble ML pipeline using market cap rank, volume z-scores, and price change features can detect pump events in real time with moderate accuracy. The key differentiator from pure threshold approaches is the inclusion of market cap as a feature: smaller-cap coins are disproportionately targeted by pump groups, so market cap rank acts as a strong prior. The random forest ensemble captures nonlinear feature interactions that simple thresholds miss — for example, a moderate volume spike combined with very low market cap is more suspicious than a large volume spike on a top-50 coin.

## Data Requirements

- OHLCV candles (1m to 1h)
- **Market capitalisation data** (real-time or daily snapshots)
- Circulating supply data (for market cap calculation)
- CoinGecko or CoinMarketCap API access for market cap rankings
- Historical market cap rankings for backtesting

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| n_estimators | 50 to 500 | 200 | Random forest trees |
| max_depth | 3 to 15 | 8 | Tree depth limit |
| volume_z_window | 10 to 50 | 20 | Volume z-score lookback |
| price_change_window | 5 to 30 | 10 | Price change lookback (bars) |
| mcap_rank_bins | 5 to 20 | 10 | Market cap rank discretisation |
| retrain_interval | 1d to 7d | 3d | Model retraining frequency |
| detection_threshold | 0.3 to 0.7 | 0.5 | Classification probability cutoff |

## MCAS Mapping

- **Closest existing setup:** D1 (shares volume z-score features)
- **Required indicators:** Volume z-score, price change, volatility — plus external market cap data
- **Timeframe affinity:** 15m, 1h
- **Universe filter:** Bottom 500 by market cap rank (where pump activity concentrates)

## References

- Various ML-based pump-and-dump detection papers (2020-2024). Common approach: random forest or gradient boosting on engineered features. Market cap is consistently the strongest single feature for pump prediction across multiple studies.

## Implementation Notes

The market cap data requirement is the main barrier. MCAS does not currently ingest market cap or circulating supply data. CoinGecko API provides this but has rate limits (30 calls/min on free tier). Options: (1) daily snapshot of market cap rankings stored in Postgres, refreshed via cron; (2) use 24h quote volume as a proxy for market cap rank (already available in MCAS). If using volume-as-proxy, this strategy collapses to a variant of the threshold approach (#04) with an ML wrapper — diminishing the justification for ML complexity.

## Status

- **MCAS Fit Rating:** 3/5
- **Implementation:** Not started
- **Validated:** No
