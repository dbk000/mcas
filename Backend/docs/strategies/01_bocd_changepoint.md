# Bayesian Online Changepoint Detection
> Strategy #01 from MCAS Research Sweep (2026-03)

## Edge

Markets exhibit distinct regimes (trending, mean-reverting, volatile, dormant). BOCD maintains a posterior distribution over the current "run length" — how long since the last regime change. When the posterior suddenly shifts to short run lengths, a changepoint has occurred. Detecting regime transitions in real time allows positioning before the new regime is fully priced in. The edge is not in the changepoint itself but in being early to recognise a structural shift in volatility or trend character.

## Data Requirements

- OHLCV candles (any timeframe, 1h or 4h preferred for regime detection)
- Minimum 200 bars of history for prior calibration
- No external data needed

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| hazard_rate | 1/50 to 1/500 | 1/200 | Prior probability of changepoint per timestep |
| observation_model | gaussian, t-student | gaussian | Likelihood function for price returns |
| run_length_threshold | 0.3 to 0.8 | 0.5 | Posterior mass on short runs to trigger detection |
| lookback_window | 100 to 500 | 200 | Bars used for prior parameter estimation |
| min_run_length | 5 to 20 | 10 | Minimum run length before allowing new changepoint |

## MCAS Mapping

- **Closest existing setup:** None (novel detection method)
- **Required indicators:** Returns series, volatility estimate
- **Timeframe affinity:** 1h, 4h (regime changes are multi-hour phenomena)
- **Universe filter:** All monitored universe; most useful on assets transitioning from dormancy

## References

- Adams, R.P. & MacKay, D.J.C. (2007). "Bayesian Online Changepoint Detection." arXiv:0710.3742. Core algorithm: O(t) per update, exact posterior over run lengths.
- Fearnhead, P. & Liu, Z. (2007). "On-line inference for multiple changepoint problems." JRSS-B. Extended version with particle filtering for scalability.

## Implementation Notes

BOCD is most valuable as a **feature generator**, not a standalone strategy. The run-length posterior provides a continuous "regime freshness" signal that can feed into D1 or other setups. Key consideration: the hazard rate is the most sensitive parameter — too high produces false changepoints, too low misses real ones. A hierarchical prior on the hazard rate (learning it from data) is preferred over a fixed value. Computational cost is O(t) per update but can be truncated to O(1) by pruning negligible run lengths.

## Status

- **MCAS Fit Rating:** 3/5
- **Implementation:** Not started
- **Validated:** No
