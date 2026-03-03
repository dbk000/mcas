# Pump Prediction (SIGMOD 2023)
> Strategy #02 from MCAS Research Sweep (2026-03)

## Edge

Sequence-based modelling of order flow can predict pump-and-dump events before the price spike. The SIGMOD approach uses LSTM networks trained on sequences of order book snapshots (bid/ask depth, spread dynamics, order arrival rates) to learn temporal patterns that precede coordinated pump activity. The key insight is that manipulators leave footprints in the order book — pre-positioning limit orders, testing liquidity — before the pump signal is broadcast.

## Data Requirements

- **Tick-level order book snapshots** (L2 depth, top 5-20 levels) at sub-second frequency
- Historical labeled pump events for training
- Order flow imbalance features (buy/sell volume ratio per tick)
- Minimum months of continuous order book data per asset
- Storage: ~50-100 GB per asset per month at tick level

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| sequence_length | 50 to 500 ticks | 200 | LSTM input window |
| hidden_dim | 64 to 256 | 128 | LSTM hidden state size |
| num_layers | 1 to 3 | 2 | LSTM depth |
| dropout | 0.1 to 0.5 | 0.3 | Regularisation |
| prediction_horizon | 1 to 60 min | 5 min | How far ahead to predict |
| ob_depth_levels | 5 to 20 | 10 | Order book levels to include |

## MCAS Mapping

- **Closest existing setup:** None
- **Required indicators:** Not applicable — requires entirely different data pipeline
- **Timeframe affinity:** Sub-minute (tick level)
- **Universe filter:** Only assets with sufficient order book depth

## References

- SIGMOD 2023 proceedings. Sequence-based pump prediction using order flow features. Demonstrated high AUC on labeled pump events using exchange-specific order book data.

## Implementation Notes

This strategy is **not feasible for MCAS** in its current form. MCAS ingests OHLCV candles at 1-minute minimum granularity. The order book data pipeline required (WebSocket L2 streams, tick storage, sequence preprocessing) would be a separate infrastructure project. The labeled training data requirement adds another barrier — constructing a reliable pump event dataset requires manual labelling or heuristic labelling (which introduces circularity). Documented here for completeness; the dual-threshold approach (Strategy #04) achieves comparable detection with OHLCV-only data.

## Status

- **MCAS Fit Rating:** 1/5
- **Implementation:** Not started
- **Validated:** No
