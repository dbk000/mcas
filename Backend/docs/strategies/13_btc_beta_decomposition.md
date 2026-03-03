# BTC Beta Decomposition
> Strategy #13 from MCAS Research Sweep (2026-03)

## Edge

Nearly all altcoin returns are partially explained by Bitcoin's movement. Decomposing any altcoin's return into a BTC-correlated component and an idiosyncratic (asset-specific) component separates "riding the market" from "genuine buying interest." A positive idiosyncratic return means the asset is outperforming what BTC correlation alone would predict — someone is specifically buying this coin, not just the market. This decomposition is a **universal feature** that improves every strategy in MCAS: D1 accumulation is more meaningful when it shows positive idiosyncratic return (genuine buying, not just BTC uplift); volume spikes during BTC rallies are less significant than volume spikes during BTC-flat periods.

## Data Requirements

- OHLCV candles for target asset (any timeframe)
- OHLCV candles for BTCUSDT at matching timeframe
- Minimum 30 bars for rolling beta estimation
- No external data needed (BTCUSDT already in monitored universe)

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| beta_window | 20 to 100 bars | 50 | Rolling window for OLS beta estimation |
| beta_method | ols, ewma, robust | ols | Beta estimation method |
| ewma_halflife | 10 to 30 | 20 | Half-life if using EWMA beta |
| min_r_squared | 0.05 to 0.30 | 0.10 | Minimum R-squared for beta to be meaningful |
| idio_smoothing | 1 to 5 bars | 1 | Smoothing on idiosyncratic return series |

## MCAS Mapping

- **Closest existing setup:** None (universal feature, applies to all setups)
- **Required indicators:** Rolling OLS beta vs BTCUSDT, idiosyncratic return = asset_return - beta * btc_return
- **Timeframe affinity:** All timeframes; 1h is most practical for rolling estimation
- **Universe filter:** All monitored universe (every asset has some BTC correlation)

## References

- Standard factor model decomposition (Sharpe 1964, adapted to crypto). In crypto markets, BTC acts as the single dominant factor, explaining 30-70% of altcoin return variance depending on the asset and period. The idiosyncratic component is what remains after removing this common factor.

## Implementation Notes

Implementation is straightforward and high-value. Steps: (1) Ensure BTCUSDT OHLCV is always available at all timeframes used by setups. (2) Compute rolling 50-bar OLS beta for each asset: `beta = cov(asset_ret, btc_ret) / var(btc_ret)`. (3) Compute idiosyncratic return: `idio_ret = asset_ret - beta * btc_ret`. (4) Expose `btc_beta`, `idio_return`, and `idio_cumulative` as standard indicators. (5) Setups can then use `idio_return > 0` as a quality filter. For D1 specifically: during the accumulation detection phase, positive idiosyncratic return confirms that the volume increase represents genuine asset-specific buying, not market-wide flow. This single feature could significantly reduce D1 false positives during BTC rally periods.

## Status

- **MCAS Fit Rating:** 5/5
- **Implementation:** Not started
- **Validated:** No
