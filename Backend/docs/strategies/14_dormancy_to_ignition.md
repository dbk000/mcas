# Dormancy to Ignition (D1)
> Strategy #14 from MCAS Research Sweep (2026-03)

## Edge

Coins transitioning from prolonged dormancy (flatline price, near-zero relative volume) to active accumulation (volume spikes while price stays compressed) exhibit a predictable breakout pattern. The dormancy phase establishes a low-volatility, low-volume baseline. The accumulation phase — visible as rising volume without proportional price movement — signals that informed participants are building positions while the market is inattentive. Entry on breakout from the compressed range captures the ignition move. Research basis: 69.3% of pump events show pre-accumulation averaging 36 hours (arxiv:2504.15790), and dual EWMA + volume thresholding achieves 0.84 precision on detecting the ignition (arxiv:2503.08692). D1 combines the dormancy detection (identifying candidates), accumulation confirmation (filtering for genuine buying), and threshold-based entry timing into a single integrated setup.

## Data Requirements

- OHLCV candles (15m and 1h primary timeframes)
- Bollinger Bandwidth for price compression measurement
- Rolling volume statistics (mean, std, z-score)
- Relative Volume (RVOL) vs N-day baseline
- ATR for stop/target calibration
- BTCUSDT for beta decomposition (optional but recommended, see Strategy #13)

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| dormancy_threshold | 0.60 to 0.95 | 0.80 | Flatness percentile (price range / ATR) |
| dormancy_min_bars | 48 to 336 | 168 | Minimum bars in dormancy (168 = 1 week at 1h) |
| bb_width_compress | 0.01 to 0.05 | 0.02 | Bollinger bandwidth below this = compressed |
| volume_z_spike | 1.5 to 4.0 | 2.0 | Volume z-score to trigger accumulation flag |
| rvol_min | 2.0 to 10.0 | 5.0 | Relative volume vs dormancy baseline |
| breakout_confirm | close_above_bb, volume_expand | close_above_bb | Entry confirmation method |
| stop_pct | 1.5% to 5.0% | 2.5% | Stop loss below entry |
| tp1_pct | 5.0% to 15.0% | 10.0% | First take-profit target |
| tp2_pct | 10.0% to 30.0% | 20.0% | Second take-profit target |
| max_hold_bars | 24 to 168 | 72 | Maximum holding period (bars) before forced exit |
| idio_return_filter | true/false | true | Require positive idiosyncratic return (#13) |

## MCAS Mapping

- **Closest existing setup:** None (new setup, combines elements of S1/S2 volume detection with novel dormancy concept)
- **Required indicators:** Bollinger Bands (width), ATR, Volume rolling mean/std, Volume z-score, RVOL, optionally BTC beta
- **Timeframe affinity:** 1h (primary — matches 36h average accumulation period), 15m (confirmation)
- **Universe filter:** Monitored universe, optionally restricted to bottom tercile by liquidity (Strategy #12 amplification)

## References

- arXiv:2504.15790 — Microstructure analysis of pump-and-dump events. 69.3% show pre-accumulation phase averaging 2160 minutes. Pre-accumulated pumps achieve higher peak prices. Foundation for the dormancy-to-accumulation detection logic.
- arXiv:2503.08692 — EWMA + volume dual-threshold detection achieving 0.84 precision. Foundation for the ignition/entry timing logic. Dual-condition requirement (price deviation AND volume spike) is key to high precision.
- Strategy #04 (Thresholding) — Entry timing component.
- Strategy #06 (Microstructure) — Pre-accumulation theoretical basis.
- Strategy #07 (Unusual Volume) — Volume anomaly detection component.
- Strategy #13 (BTC Beta) — Idiosyncratic return filter to separate genuine buying from market-wide flow.

## Implementation Notes

D1 is a three-phase detection pipeline:

**Phase 1 — Dormancy Detection.** Continuously scan the monitored universe for assets meeting dormancy criteria: Bollinger bandwidth below `bb_width_compress`, price range within `dormancy_threshold` percentile of ATR, sustained for `dormancy_min_bars`. This phase runs on every candle close and maintains a "dormancy watchlist" in Redis.

**Phase 2 — Accumulation Confirmation.** For assets on the dormancy watchlist, monitor for volume anomalies: volume z-score exceeding `volume_z_spike` AND relative volume exceeding `rvol_min` vs the dormancy baseline. Optionally require positive idiosyncratic return (BTC beta decomposition). Assets meeting accumulation criteria move to an "accumulating watchlist."

**Phase 3 — Ignition Entry.** For accumulating assets, trigger entry on breakout confirmation: close above upper Bollinger Band with expanding volume. Set stop at `stop_pct` below entry, TP1 at `tp1_pct`, TP2 at `tp2_pct`. Maximum hold of `max_hold_bars` before forced exit.

Key risks: (1) Dormancy can persist indefinitely — the watchlist could grow large. Implement TTL-based expiry. (2) Not all accumulation leads to breakout — some positions will stop out during false starts. The 2.5% stop limits damage. (3) The strategy naturally has low frequency (dormancy periods are long) — expect 5-15 signals per week across the full monitored universe.

## Status

- **MCAS Fit Rating:** 5/5
- **Implementation:** In progress (Phase 0 — documentation and design)
- **Validated:** No
