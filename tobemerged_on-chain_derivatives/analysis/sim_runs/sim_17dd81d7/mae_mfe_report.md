# MAE/MFE Calibration Report

**Trades analyzed:** 1087
**Winners:** 481 (44.3%)
**Losers:** 606 (55.7%)
**Total PnL:** $-1971.73

## Q1: Are Stops Too Tight?

**Sample:** 1087 trades (481 winners, 606 losers)

### Winner MAE Distribution (how far winners dip against you)

| Metric | Value |
|:-------|------:|
| P10 | -0.6528 R |
| P25 | -0.3881 R |
| P50 | -0.2040 R |
| P75 | -0.0848 R |
| P90 | -0.0403 R |
| Mean | -0.3001 R |
| Min (worst) | -3.0788 R |

- Winners with MAE worse than -0.5R: 85 (17.7%)
- Winners with MAE worse than -0.75R: 33 (6.9%)
- Winners with MAE worse than -0.9R: 23 (4.8%)

### Near-Miss Analysis (STOP_LOSS trades that had profit potential)

- STOP_LOSS trades: 156
- STOP_LOSS with MFE >= 1.0R (would have hit TP1): **7** (4.5%)
- STOP_LOSS with MFE >= 0.5R (had meaningful favorable move): **42** (26.9%)

### STOP_LOSS MFE Distribution

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.0000 R |
| P50 | 0.2283 R |
| P75 | 0.5294 R |
| P90 | 0.8266 R |
| Mean | 0.4217 R |
| Max | 6.3372 R |

### Winner MAE by Setup x Timeframe

| Setup | Timeframe | N | Median MAE | P25 MAE | P75 MAE |
|:------|:----------|--:|-----------:|--------:|--------:|
| S1 | 1h | 6 | -0.1270 | -0.3220 | -0.0582 |
| S2 | 15m | 8 | -0.2288 | -0.2482 | -0.1851 |
| S2 | 1h | 5 | -0.4839 | -0.4950 | -0.0928 |
| S3 | 15m | 12 | -0.1014 | -0.2107 | -0.0960 |
| S6 | 15m | 287 | -0.1860 | -0.3501 | -0.0757 |
| S6 | 1h | 110 | -0.1887 | -0.4274 | -0.0840 |
| S7 | 1h | 42 | -0.3856 | -0.9233 | -0.1847 |
| S7 | 4h | 6 | -0.5019 | -1.9543 | -0.1545 |

## Q2: Is FAIL_FAST Too Aggressive?

**FAIL_FAST trades:** 361 (33.2% of all trades)

### FAIL_FAST MFE Distribution

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.0000 R |
| P50 | 0.0652 R |
| P75 | 0.2144 R |
| P90 | 0.3965 R |
| Mean | 0.1393 R |
| Max | 1.1809 R |

### FAIL_FAST Opportunity Cost

- MFE >= 1.0R (would have hit ~TP1): **1** (0.3%)
- MFE >= 0.5R (meaningful favorable move): **15** (4.2%)
- MFE < 0.1R (correct to exit — no favorable move): **200** (55.4%)

### FAIL_FAST Threshold vs Winner MAE

- Median winner MAE: -0.2040R
- Median FAIL_FAST MFE: 0.0652R
- If FAIL_FAST threshold were widened to median winner MAE (-0.2040R), some FAIL_FAST exits might have become winners.

### FAIL_FAST by Setup

| Setup | N | Median MFE | % with MFE >= 0.5R |
|:------|--:|-----------:|-------------------:|
| S1 | 1 | 0.0000 | 0.0% |
| S2 | 2 | 0.1438 | 0.0% |
| S3 | 11 | 0.1787 | 9.1% |
| S6 | 322 | 0.0636 | 3.4% |
| S7 | 25 | 0.1692 | 12.0% |

## Q3: Are Targets Too Conservative?

### MFE Distribution (all trades)

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.1062 R |
| P50 | 0.4825 R |
| P75 | 0.8943 R |
| P90 | 1.6081 R |
| P95 | 2.4469 R |
| Mean | 0.7399 R |
| Max | 14.4215 R |

### MFE Buckets

| MFE Range | Count | % |
|:----------|------:|--:|
| < 0.25R | 395 | 36.3% |
| 0.25R - 0.5R | 159 | 14.6% |
| 0.5R - 1.0R | 312 | 28.7% |
| 1.0R - 1.5R | 102 | 9.4% |
| 1.5R - 2.0R | 44 | 4.0% |
| 2.0R - 3.0R | 35 | 3.2% |
| >= 3.0R | 40 | 3.7% |

### TP1 Exits: Profit Left on Table

- TP1 trades: 345
- Median additional MFE beyond TP1 exit: 0.2998R
- Mean additional MFE beyond TP1 exit: 0.4251R
- P90 additional MFE beyond TP1 exit: 0.8399R

### Losers That Were Once Winners

- Losers total: 606
- Losers with MFE >= 1.0R before reversing: **43** (7.1%)
- Losers with MFE >= 0.5R before reversing: **96** (15.8%)

### Losers with MFE >= 1R — Detail

| Symbol | TF | Setup | Dir | MFE R | Final R | Exit Reason | Bars |
|:-------|:---|:------|:----|------:|--------:|:------------|-----:|
| COMPUSDT | 15m | S6 | short | 1.41 | -0.16 | TRAIL_STOP | 2 |
| ICPUSDT | 15m | S6 | short | 1.16 | -0.32 | TRAIL_STOP | 1 |
| XRPUSDT | 1h | S7 | short | 1.15 | -0.44 | TRAIL_STOP | 6 |
| GNOUSDT | 15m | S3 | long | 1.31 | -0.15 | TRAIL_STOP | 1 |
| SCUSDT | 15m | S3 | long | 1.55 | -1.22 | STOP_LOSS | 3 |
| WINUSDT | 15m | S6 | long | 1.23 | -0.50 | TRAIL_STOP | 4 |
| LAYERUSDT | 15m | S6 | long | 1.65 | -0.05 | TRAIL_STOP | 2 |
| MINAUSDT | 15m | S6 | long | 1.46 | -0.24 | TRAIL_STOP | 1 |
| IMXUSDT | 1h | S7 | long | 3.76 | -1.16 | STOP_LOSS | 1 |
| ARKMUSDT | 15m | S3 | long | 1.39 | -0.14 | TRAIL_STOP | 5 |
| IOUSDT | 1h | S7 | long | 3.13 | -0.29 | TRAIL_STOP | 1 |
| SANDUSDT | 15m | S6 | long | 1.19 | -0.05 | TRAIL_STOP | 1 |
| MOVEUSDT | 1h | S7 | long | 1.37 | -0.12 | TRAIL_STOP | 3 |
| XAIUSDT | 1h | S7 | long | 1.39 | -0.19 | TRAIL_STOP | 4 |
| DEXEUSDT | 15m | S6 | long | 1.23 | -0.00 | TRAIL_STOP | 5 |

## Q4: Is Entry Timing the Problem?

### Bars to MAE Distribution (when does the worst drawdown occur?)

| Metric | Value |
|:-------|------:|
| P10 | 0.0 bars |
| P25 | 0.0 bars |
| P50 | 1.0 bars |
| P75 | 1.0 bars |
| P90 | 3.0 bars |
| Mean | 1.3 bars |

### Instant-Death vs Survivors MAE Comparison

- Instant-death trades (bars_held <= 2): 807
- Survivor trades (bars_held > 2): 280
- Median MAE (instant-death): -0.4087R
- Median MAE (survivors): -0.3745R

### Bar-0 Adverse Movement (entry bar is the worst point)

- Trades where MAE occurs on bar 0: 497 (45.7%)
- Median bar-0 MAE: -0.2422R
- P90 bar-0 MAE: -0.9211R

### Bars to MFE Distribution (when does the best point occur?)

| Metric | Value |
|:-------|------:|
| P10 | 0.0 bars |
| P25 | 0.0 bars |
| P50 | 1.0 bars |
| P75 | 2.0 bars |
| P90 | 5.0 bars |
| Mean | 1.5 bars |

## Q5: What Would Optimal Stops Look Like?

### Winner MAE Percentiles by Setup

| Setup | N | P10 | P25 | P50 | P75 | P90 |
|:------|--:|----:|----:|----:|----:|----:|
| S1 | 10 | -0.401 | -0.357 | -0.241 | -0.112 | -0.045 |
| S2 | 13 | -0.493 | -0.320 | -0.231 | -0.181 | -0.082 |
| S3 | 13 | -0.457 | -0.280 | -0.104 | -0.097 | -0.072 |
| S6 | 397 | -0.569 | -0.363 | -0.188 | -0.079 | -0.037 |
| S7 | 48 | -1.479 | -0.930 | -0.386 | -0.170 | -0.073 |
| **ALL** | 481 | -0.653 | -0.388 | -0.204 | -0.085 | -0.040 |

### Stop Calibration Trade-Off Table

If stop were set at the Nth percentile of winner MAE, what % of winners and losers would survive?

| Stop Level (R) | Winner MAE Pctile | % Winners Survive | % Losers Survive | Net Trades Kept |
|---------------:|------------------:|------------------:|-----------------:|----------------:|
| -0.866 | P5 | 457/481 (95.0%) | 422/606 (69.6%) | 879 |
| -0.653 | P10 | 433/481 (90.0%) | 353/606 (58.3%) | 786 |
| -0.521 | P15 | 409/481 (85.0%) | 274/606 (45.2%) | 683 |
| -0.470 | P20 | 385/481 (80.0%) | 239/606 (39.4%) | 624 |
| -0.388 | P25 | 361/481 (75.1%) | 167/606 (27.6%) | 528 |
| -0.339 | P30 | 337/481 (70.1%) | 124/606 (20.5%) | 461 |
| -0.268 | P40 | 289/481 (60.1%) | 72/606 (11.9%) | 361 |
| -0.204 | P50 | 241/481 (50.1%) | 42/606 (6.9%) | 283 |

### Per-Setup Recommendations

**S1** (N=15, WR=66.7%, PnL=$7.16)

- Winner P25 MAE: -0.357R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.401R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.357R): 3 of 5 (60.0%)
- Losers stopped earlier at P10 (-0.401R): 3 of 5 (60.0%)

**S2** (N=17, WR=76.5%, PnL=$72.15)

- Winner P25 MAE: -0.320R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.493R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.320R): 4 of 4 (100.0%)
- Losers stopped earlier at P10 (-0.493R): 2 of 4 (50.0%)

**S3** (N=31, WR=41.9%, PnL=$-124.70)

- Winner P25 MAE: -0.280R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.457R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.280R): 16 of 18 (88.9%)
- Losers stopped earlier at P10 (-0.457R): 13 of 18 (72.2%)

**S6** (N=892, WR=44.5%, PnL=$-1947.59)

- Winner P25 MAE: -0.363R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.569R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.363R): 361 of 495 (72.9%)
- Losers stopped earlier at P10 (-0.569R): 220 of 495 (44.4%)

**S7** (N=132, WR=36.4%, PnL=$21.25)

- Winner P25 MAE: -0.930R — setting stop here would keep 75% of winners
- Winner P10 MAE: -1.479R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.930R): 55 of 84 (65.5%)
- Losers stopped earlier at P10 (-1.479R): 43 of 84 (51.2%)

## Summary: Findings → Parameters → Expected Impact

| # | Finding | Parameter | Suggested Action | Expected Impact |
|--:|:--------|:----------|:-----------------|:----------------|
| 1 | Winners have shallow MAE | stop_price distance | Consider tightening stops — winner P25 MAE is only -0.388R | Reduced loser damage, minor winner loss |
| 2 | Only 0.3% of FAIL_FAST had MFE >= 1R | FAIL_FAST threshold | FAIL_FAST is well-calibrated | No change needed |
| 3 | 7.1% of losers had MFE >= 1R | TP1/TP2 levels | Some losers visited profitable territory — targets may be missing the move | Earlier TP1 could capture these |
| 4 | 45.7% of trades hit MAE on entry bar | Entry fill model / timing | High bar-0 MAE suggests poor entry timing | Limit orders or delayed entry could help |
