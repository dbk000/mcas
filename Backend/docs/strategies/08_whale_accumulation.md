# Whale Accumulation Detection
> Strategy #08 from MCAS Research Sweep (2026-03)

## Edge

Large buyers ("whales") attempting to accumulate positions in illiquid assets leave statistical footprints in the trade size distribution. During whale accumulation, the distribution of individual trade sizes shifts: the mean trade size increases, the right tail fattens, and the ratio of large-to-small trades rises — even if the whale uses TWAP or iceberg strategies to disguise their activity. Detecting this shift in real-time signals that informed money is building a position before the price moves. The edge is in identifying the accumulation before it becomes visible on standard OHLCV charts.

## Data Requirements

- **Tick-level trade data** or **1-second klines** (Binance provides 1s klines via API)
- Individual trade sizes and directions (buy/sell taker classification)
- Minimum 1 hour of continuous tick data for baseline distribution
- Historical tick data for backtesting (storage-intensive)

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| trade_size_window | 100 to 1000 trades | 500 | Window for distribution estimation |
| large_trade_percentile | 90th to 99th | 95th | Definition of "large" trade |
| large_trade_ratio_threshold | 1.5x to 5.0x | 2.0x | Ratio vs baseline to trigger |
| baseline_period | 1h to 24h | 6h | Period for establishing normal distribution |
| min_trades_per_window | 50 to 200 | 100 | Minimum trades for statistical validity |
| direction_filter | buy_only, any | buy_only | Only flag buy-side accumulation |

## MCAS Mapping

- **Closest existing setup:** S9 (uses volume-based features); D1 (accumulation detection goal)
- **Required indicators:** Trade size distribution metrics (requires new data pipeline)
- **Timeframe affinity:** Sub-minute (tick level ideal), 1m minimum
- **Universe filter:** Monitored universe assets with sufficient trade frequency (>100 trades/hour)

## References

- Market microstructure literature on informed trading detection (Kyle 1985, Easley et al. 1996 — PIN model). Modern crypto adaptations focus on trade size clustering and order flow toxicity (VPIN). Binance 1s kline API provides partial access to sub-minute data without full tick-level infrastructure.

## Implementation Notes

Binance's 1-second kline API (used by S9) provides a partial path to implementation without full tick infrastructure. From 1s klines, you can compute: volume per second (proxy for trade intensity), high-low range per second (proxy for trade size variation), and number of trades per second. These proxy features capture some whale accumulation signal but miss the trade size distribution detail. Full implementation requires the WebSocket trade stream, which MCAS does not currently store. A staged approach: (1) proxy features from 1s klines as D1 enhancement, (2) full tick storage if proxy features show promise.

## Status

- **MCAS Fit Rating:** 3/5
- **Implementation:** Not started
- **Validated:** No
