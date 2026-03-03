# Thresholding Pump Detection (EWMA + Volume)
> Strategy #04 from MCAS Research Sweep (2026-03)

## Edge

A dual-condition threshold detector achieves 0.84 precision on pump event detection using only OHLCV data — outperforming most ML approaches. The first condition flags abnormal price movement via EWMA deviation; the second requires a simultaneous volume z-score spike. The conjunction of both conditions eliminates most false positives (news-driven moves without volume anomaly, or volume spikes without price follow-through). The simplicity is the edge: no model training, no labeled data, no overfitting risk, sub-millisecond evaluation.

## Data Requirements

- OHLCV candles (1m to 1h)
- Minimum 20 bars for EWMA and volume z-score calculation
- No external data needed

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| ewma_span | 10 to 50 | 20 | EWMA smoothing window (bars) |
| price_dev_threshold | 1.5 to 4.0 std | 2.5 std | EWMA price deviation trigger |
| volume_zscore_window | 10 to 30 | 20 | Rolling window for volume z-score |
| volume_zscore_threshold | 2.0 to 5.0 | 3.0 | Volume z-score trigger |
| min_volume_usd | 10k to 500k | 50k | Minimum absolute volume filter |
| cooldown_bars | 1 to 10 | 3 | Bars before re-triggering |

## MCAS Mapping

- **Closest existing setup:** D1 (Dormancy-to-Ignition) — D1 incorporates this as its volume spike detection component
- **Required indicators:** EWMA of close, rolling volume mean/std
- **Timeframe affinity:** 5m, 15m (fast detection), 1h (fewer false positives)
- **Universe filter:** All monitored universe; higher precision on lower-liquidity assets

## References

- arXiv:2503.08692 — EWMA + volume thresholding methodology. Key result: dual-condition threshold achieves 0.84 precision, exceeding most ML baselines. The paper demonstrates that the conjunction requirement (both price AND volume anomaly) is what drives precision — either condition alone achieves only 0.55-0.65 precision.

## Implementation Notes

This is the **highest-value detection primitive** in the research sweep. Already partially implemented in D1's volume spike detection. Key considerations: (1) the EWMA span and volume window should be calibrated per-timeframe since volatility regimes differ; (2) the cooldown parameter prevents repeated triggering during a single pump event; (3) this detector is best used as a **trigger condition** within a larger strategy (like D1) rather than as a standalone entry signal — detection alone doesn't specify entry timing, position sizing, or exits.

## Status

- **MCAS Fit Rating:** 5/5
- **Implementation:** Partial (integrated into D1 design)
- **Validated:** No (pending D1 validation)
