# Microstructure Manipulation Archetypes
> Strategy #06 from MCAS Research Sweep (2026-03)

## Edge

Research reveals two distinct pump-and-dump archetypes with fundamentally different microstructures. **Pre-accumulated pumps** (69.3% of events) show gradual volume accumulation averaging 2160 minutes (36 hours) before the pump signal, with manipulators building positions in advance. **On-the-spot pumps** (30.7%) show no pre-accumulation — buying starts only after the signal. This distinction is the core insight behind D1: the pre-accumulation phase is detectable because it creates a volume/price divergence (rising volume, compressed price) that precedes the pump by hours to days. Entering during late-stage accumulation captures the pump move from a lower cost basis.

## Data Requirements

- OHLCV candles (15m to 1h for accumulation detection)
- Volume profile over 24-72 hour windows
- Price range compression metrics (Bollinger bandwidth, ATR ratio)
- No external data needed

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| accumulation_window | 12h to 96h | 36h | Window to detect pre-accumulation |
| volume_ramp_threshold | 1.5x to 5.0x | 2.0x | Volume increase vs dormant baseline |
| price_compression_max | 1% to 5% | 2% | Max price range during accumulation |
| dormancy_baseline_days | 3 to 14 | 7 | Days to establish dormant volume baseline |
| ramp_slope_min | 0.01 to 0.10 | 0.03 | Minimum volume ramp slope (normalised) |
| archetype_classifier | rule, ml | rule | Method to distinguish pre-acc vs on-the-spot |

## MCAS Mapping

- **Closest existing setup:** D1 (Dormancy-to-Ignition) — this research is D1's theoretical foundation
- **Required indicators:** Volume rolling mean, Bollinger bandwidth, ATR, volume slope
- **Timeframe affinity:** 1h (best balance of accumulation visibility and noise reduction)
- **Universe filter:** Low-liquidity assets in monitored universe (accumulation most visible where baseline volume is low)

## References

- arXiv:2504.15790 — Comprehensive analysis of pump-and-dump microstructure. Key findings: 69.3% of pump events show pre-accumulation phase averaging 2160 minutes. Pre-accumulated pumps achieve higher peak prices (+18% vs +12% for on-the-spot). The accumulation phase creates detectable volume/price divergence patterns.

## Implementation Notes

This is **not a standalone strategy** but the research foundation for D1. The 69.3% pre-accumulation finding means that a detector tuned for the accumulation-then-ignition pattern will capture the majority of pump events. The 2160-minute average accumulation period (with high variance) informs D1's window sizing. Key implementation consideration: the accumulation phase is gradual and noisy — simple threshold detection (Strategy #04) catches the ignition but misses the accumulation. D1 combines both: detect the dormancy-to-accumulation transition (this research) and confirm with volume spike detection (#04) for entry timing.

## Status

- **MCAS Fit Rating:** 5/5
- **Implementation:** Research only (informs D1 design)
- **Validated:** No (validated indirectly through D1)
