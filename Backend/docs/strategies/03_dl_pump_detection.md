# Deep Learning Pump Detection
> Strategy #03 from MCAS Research Sweep (2026-03)

## Edge

CNN and LSTM architectures can learn complex temporal patterns in price and volume data that precede pump-and-dump events. Unlike rule-based thresholding, deep learning models can capture nonlinear interactions between features — for example, a specific sequence of volume decay followed by a particular price compression pattern that precedes accumulation. The models generalise across different pump morphologies (fast vs slow, pre-accumulated vs spontaneous).

## Data Requirements

- OHLCV candles (1m to 1h)
- **Labeled training dataset** of pump events (hundreds to thousands of examples)
- Negative examples (non-pump periods) for balanced training
- Feature engineering: returns, volume ratios, volatility measures, technical indicators
- Validation set with temporal separation from training

## Parameter Space

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| model_type | CNN, LSTM, CNN-LSTM | CNN-LSTM | Architecture choice |
| window_size | 30 to 200 bars | 60 | Input window in bars |
| num_features | 5 to 30 | 12 | Engineered feature count |
| learning_rate | 1e-4 to 1e-2 | 1e-3 | Optimiser LR |
| batch_size | 32 to 256 | 64 | Training batch size |
| class_weight_ratio | 1:5 to 1:50 | 1:20 | Positive:negative sampling ratio |
| detection_threshold | 0.3 to 0.8 | 0.5 | Classification threshold |

## MCAS Mapping

- **Closest existing setup:** D1 (Dormancy-to-Ignition) addresses similar detection goal with rules
- **Required indicators:** Standard OHLCV-derived features (returns, volume z-scores, volatility)
- **Timeframe affinity:** 15m, 1h (balances signal quality vs detection speed)
- **Universe filter:** All monitored universe

## References

- Various 2021-2023 papers on deep learning for P&D detection in cryptocurrency markets. Common finding: CNN-LSTM hybrids outperform pure CNN or pure LSTM on this task. Precision typically 0.70-0.85 depending on labelling methodology and asset universe.

## Implementation Notes

The primary barrier is **labeled data construction**. Pump events are rare, and labelling requires either: (a) known Telegram pump group signals (external data), (b) heuristic labelling using price/volume thresholds (circular with the detection goal), or (c) manual labelling (expensive, subjective). Strategy #04 (dual thresholding) achieves 0.84 precision without any labeled data, which makes the ML overhead hard to justify unless precision >0.90 is needed. If pursued, start with heuristic labels from Strategy #04 outputs as bootstrap training data.

## Status

- **MCAS Fit Rating:** 2/5
- **Implementation:** Not started
- **Validated:** No
