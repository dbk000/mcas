# Pure Momentum 24h Rolloff
> Strategy #09 from MCAS Research Sweep (2026-03)

## Edge

Most crypto dashboards, screeners, and automated systems display "24h change" as a core metric. This creates a mechanical artefact: when a coin pumped exactly 24 hours ago, the large positive return "rolls off" the 24h window, causing the displayed 24h change to drop sharply — even if the current price is stable. This sudden apparent weakness triggers sell pressure from momentum-following algorithms and retail traders watching screeners. The rolloff creates a predictable short-term dip that can be exploited as a mean-reversion entry. The edge is purely mechanical — it exploits the fixed window boundary of a widely-watched metric.

## Data Requirements

- OHLCV candles (1h or finer for precise timing)
- Ability to compute exact 24h rolling returns with bar-level precision
- Historical pump event timestamps (to identify candidates)
- No external data needed

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| pump_magnitude_min | 10% to 50% | 20% | Minimum 24h-ago pump size to create meaningful rolloff |
| entry_window | 0 to 4 hours | 1h | How long before/after the 24h mark to enter |
| holding_period | 1h to 12h | 4h | Time to hold the mean-reversion position |
| stop_loss | 2% to 8% | 5% | Stop below rolloff entry |
| volume_confirmation | true/false | true | Require volume decline during rolloff |
| min_post_pump_stability | 50% to 90% | 70% | Min retention of pump gains before rolloff window |

## MCAS Mapping

- **Closest existing setup:** None (novel timing-based strategy)
- **Required indicators:** 24h rolling return, pump event log, bar-level timestamps
- **Timeframe affinity:** 1h (aligns with 24h window granularity)
- **Universe filter:** Assets that experienced a >20% pump exactly 22-26 hours ago

## References

- Market microstructure research on rolling window artefacts. The 24h rolloff effect is well-known among crypto market makers but sparsely documented in academic literature. Related concept: "stale price" effects in ETF NAV calculations.

## Implementation Notes

Timing precision is critical: the rolloff effect is concentrated in a 1-2 hour window around the 24h mark. MCAS would need to track historical pump events and schedule evaluations at T+23h to T+25h. This is architecturally different from MCAS's current event-driven signal pipeline (which evaluates on each new candle). Implementation requires either: (1) a scheduled evaluation system (cron-like), or (2) maintaining a "pump event queue" that triggers evaluation at the appropriate future time. The strategy is interesting but the timing dependency makes it fragile — execution delays of even 30 minutes could miss the window.

## Status

- **MCAS Fit Rating:** 3/5
- **Implementation:** Not started
- **Validated:** No
