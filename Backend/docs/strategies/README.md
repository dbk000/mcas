# MCAS Strategy Research Index
> Research sweep conducted 2026-03. Phase 0 documentation for D1 Dormancy-to-Ignition and related strategies.

## Strategy Catalogue

| # | Setup ID | Name | Status | Data Requirements | MCAS Fit | Paper/Source |
|---|----------|------|--------|-------------------|----------|--------------|
| 01 | — | [BOCD Changepoint](01_bocd_changepoint.md) | Not started | OHLCV | 3/5 | Adams & MacKay 2007 |
| 02 | — | [Pump Prediction (SIGMOD)](02_pump_prediction_sigmod.md) | Not started | Tick-level order book | 1/5 | SIGMOD 2023 |
| 03 | — | [DL Pump Detection](03_dl_pump_detection.md) | Not started | Labeled data + OHLCV | 2/5 | Various 2021-2023 |
| 04 | — | [Thresholding Pump Detection](04_thresholding_pump_detection.md) | Partial (D1 overlap) | OHLCV | 5/5 | arxiv 2503.08692 |
| 05 | — | [Real-time ML Detection](05_realtime_ml_detection.md) | Not started | OHLCV + market cap | 3/5 | Various ML P&D papers |
| 06 | — | [Microstructure Manipulation](06_microstructure_manipulation.md) | Research only | OHLCV | 5/5 | arxiv 2504.15790 |
| 07 | — | [Unusual Volume Detector](07_unusual_volume_detector.md) | Not started | OHLCV | 4/5 | Statistical anomaly detection |
| 08 | — | [Whale Accumulation](08_whale_accumulation.md) | Not started | Tick/1s klines | 3/5 | Microstructure literature |
| 09 | — | [Pure Momentum 24h Rolloff](09_pure_momentum_24h_rolloff.md) | Not started | OHLCV | 3/5 | Market microstructure |
| 10 | — | [Cross-Sectional Reversal](10_cross_sectional_reversal.md) | Not started | OHLCV | 4/5 | Crypto reversal literature |
| 11 | — | [Factor Momentum](11_factor_momentum.md) | Not started | OHLCV | 3/5 | Crypto factor papers |
| 12 | — | [Illiquidity Premium](12_illiquidity_premium.md) | Not started | OHLCV + volume | 4/5 | Anomaly amplification research |
| 13 | — | [BTC Beta Decomposition](13_btc_beta_decomposition.md) | Not started | OHLCV + BTCUSDT | 5/5 | Factor decomposition |
| 14 | D1 | [Dormancy to Ignition](14_dormancy_to_ignition.md) | **In progress** | OHLCV | 5/5 | arxiv 2504.15790, 2503.08692 |

## Rating Scale

| Rating | Meaning |
|--------|---------|
| 5/5 | Directly implementable with current MCAS infrastructure and data |
| 4/5 | Implementable with minor extensions or as a feature/filter |
| 3/5 | Requires moderate new infrastructure or data sources |
| 2/5 | Requires significant new infrastructure or labeled data |
| 1/5 | Data requirements exceed what MCAS can reasonably provide |

## Usage

Each strategy doc follows a standard template: Edge, Data Requirements, Parameter Space, MCAS Mapping, References, Implementation Notes, Status. Strategies rated 4/5 or 5/5 are candidates for near-term implementation. Lower-rated strategies are documented for completeness and may become viable as MCAS infrastructure expands.
