# Unusual Volume Detector (10-sigma)
> Strategy #07 from MCAS Research Sweep (2026-03)

## Edge

Flagging volume bars that exceed 10 standard deviations above the 20-period rolling mean is an extremely high-precision anomaly detector. At 10-sigma, the probability of a false positive under a normal distribution is effectively zero — any trigger represents genuine unusual activity (whale entry, coordinated buying, exchange listing pump, or market-moving news). The trade-off is low recall: many tradeable volume events occur at 3-5 sigma. The 10-sigma threshold is best used as a **confirmation filter** — when other conditions suggest accumulation, a 10-sigma volume bar confirms that something material is happening.

## Data Requirements

- OHLCV candles (any timeframe)
- Minimum 20 bars for rolling statistics
- No external data needed

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| zscore_threshold | 5.0 to 15.0 | 10.0 | Volume z-score trigger |
| rolling_window | 10 to 50 | 20 | Bars for mean/std calculation |
| min_abs_volume | 5k to 100k USD | 10k | Minimum absolute volume to avoid micro-cap noise |
| use_log_volume | true/false | true | Log-transform volume before z-score (handles skew) |
| cooldown_bars | 1 to 5 | 2 | Minimum bars between triggers |

## MCAS Mapping

- **Closest existing setup:** D1 volume spike component; S9 (cross-sectional momentum) uses volume filters
- **Required indicators:** Rolling volume mean, rolling volume std (or log-volume equivalents)
- **Timeframe affinity:** 5m, 15m (catching the spike bar), 1h (smoothed, fewer false positives)
- **Universe filter:** All monitored universe; most meaningful on assets with established low-volume baselines

## References

- Statistical anomaly detection applied to financial volume data. The 10-sigma threshold is a practitioner convention rather than an academic finding — it originates from the observation that financial volume distributions are heavy-tailed (log-normal or Pareto), so even 5-sigma events occur more frequently than Gaussian assumptions predict. Using 10-sigma on raw volume (or 5-sigma on log-volume) compensates for this.

## Implementation Notes

Two implementation choices matter significantly: (1) **Log-transform vs raw volume.** Raw volume z-scores are dominated by the heavy tail — a single extreme bar inflates the std, suppressing future detections. Log-transforming volume before computing z-scores produces more stable statistics and better-calibrated thresholds. If using log-volume, reduce the threshold to ~5 sigma. (2) **Rolling vs expanding window.** Rolling windows adapt to recent volume regimes; expanding windows provide a more stable baseline. For dormancy detection (D1), an expanding window anchored to the dormancy period is preferred — it keeps the baseline low so that even moderate accumulation volume triggers detection.

## Status

- **MCAS Fit Rating:** 4/5
- **Implementation:** Not started
- **Validated:** No
