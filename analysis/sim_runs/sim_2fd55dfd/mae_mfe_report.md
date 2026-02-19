# MAE/MFE Calibration Report

**Trades analyzed:** 1087
**Winners:** 480 (44.2%)
**Losers:** 607 (55.8%)
**Total PnL:** $-2480.83

## Q1: Are Stops Too Tight?

**Sample:** 1087 trades (480 winners, 607 losers)

### Winner MAE Distribution (how far winners dip against you)

| Metric | Value |
|:-------|------:|
| P10 | -0.6805 R |
| P25 | -0.3966 R |
| P50 | -0.2052 R |
| P75 | -0.0853 R |
| P90 | -0.0387 R |
| Mean | -0.3118 R |
| Min (worst) | -3.2952 R |

- Winners with MAE worse than -0.5R: 87 (18.1%)
- Winners with MAE worse than -0.75R: 38 (7.9%)
- Winners with MAE worse than -0.9R: 26 (5.4%)

### Near-Miss Analysis (STOP_LOSS trades that had profit potential)

- STOP_LOSS trades: 144
- STOP_LOSS with MFE >= 1.0R (would have hit TP1): **7** (4.9%)
- STOP_LOSS with MFE >= 0.5R (had meaningful favorable move): **30** (20.8%)

### STOP_LOSS MFE Distribution

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.0000 R |
| P50 | 0.1717 R |
| P75 | 0.4585 R |
| P90 | 0.7673 R |
| Mean | 0.3981 R |
| Max | 6.3372 R |

### Winner MAE by Setup x Timeframe

| Setup | Timeframe | N | Median MAE | P25 MAE | P75 MAE |
|:------|:----------|--:|-----------:|--------:|--------:|
| S1 | 1h | 6 | -0.1270 | -0.3220 | -0.0582 |
| S2 | 15m | 8 | -0.2288 | -0.2482 | -0.1851 |
| S2 | 1h | 5 | -0.4839 | -0.4950 | -0.0928 |
| S3 | 15m | 12 | -0.1336 | -0.3264 | -0.0960 |
| S6 | 15m | 284 | -0.1854 | -0.3517 | -0.0761 |
| S6 | 1h | 108 | -0.1935 | -0.4072 | -0.0849 |
| S7 | 1h | 46 | -0.4572 | -0.9535 | -0.1595 |
| S7 | 4h | 6 | -0.5019 | -1.9543 | -0.1545 |

## Q2: Is FAIL_FAST Too Aggressive?

**FAIL_FAST trades:** 361 (33.2% of all trades)

### FAIL_FAST MFE Distribution

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.0000 R |
| P50 | 0.0651 R |
| P75 | 0.2080 R |
| P90 | 0.3870 R |
| Mean | 0.1361 R |
| Max | 0.9479 R |

### FAIL_FAST Opportunity Cost

- MFE >= 1.0R (would have hit ~TP1): **0** (0.0%)
- MFE >= 0.5R (meaningful favorable move): **14** (3.9%)
- MFE < 0.1R (correct to exit — no favorable move): **201** (55.7%)

### FAIL_FAST Threshold vs Winner MAE

- Median winner MAE: -0.2052R
- Median FAIL_FAST MFE: 0.0651R
- If FAIL_FAST threshold were widened to median winner MAE (-0.2052R), some FAIL_FAST exits might have become winners.

### FAIL_FAST by Setup

| Setup | N | Median MFE | % with MFE >= 0.5R |
|:------|--:|-----------:|-------------------:|
| S1 | 1 | 0.0000 | 0.0% |
| S2 | 2 | 0.1438 | 0.0% |
| S3 | 11 | 0.1787 | 9.1% |
| S6 | 322 | 0.0634 | 3.1% |
| S7 | 25 | 0.1692 | 12.0% |

## Q3: Are Targets Too Conservative?

### MFE Distribution (all trades)

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.1062 R |
| P50 | 0.4825 R |
| P75 | 0.8440 R |
| P90 | 1.5133 R |
| P95 | 2.3649 R |
| Mean | 0.7047 R |
| Max | 14.4215 R |

### MFE Buckets

| MFE Range | Count | % |
|:----------|------:|--:|
| < 0.25R | 395 | 36.3% |
| 0.25R - 0.5R | 159 | 14.6% |
| 0.5R - 1.0R | 330 | 30.4% |
| 1.0R - 1.5R | 93 | 8.6% |
| 1.5R - 2.0R | 40 | 3.7% |
| 2.0R - 3.0R | 33 | 3.0% |
| >= 3.0R | 37 | 3.4% |

### TP1 Exits: Profit Left on Table

- TP1 trades: 421
- Median additional MFE beyond TP1 exit: 0.4191R
- Mean additional MFE beyond TP1 exit: 0.8072R
- P90 additional MFE beyond TP1 exit: 1.8287R

### Losers That Were Once Winners

- Losers total: 607
- Losers with MFE >= 1.0R before reversing: **10** (1.6%)
- Losers with MFE >= 0.5R before reversing: **97** (16.0%)

### Losers with MFE >= 1R — Detail

| Symbol | TF | Setup | Dir | MFE R | Final R | Exit Reason | Bars |
|:-------|:---|:------|:----|------:|--------:|:------------|-----:|
| SCUSDT | 15m | S3 | long | 1.55 | -1.22 | STOP_LOSS | 3 |
| IMXUSDT | 1h | S7 | long | 3.76 | -1.16 | STOP_LOSS | 1 |
| EIGENUSDT | 1h | S7 | long | 1.24 | -0.09 | TRAIL_STOP | 1 |
| AUCTIONUSDT | 1h | S7 | long | 6.34 | -2.05 | STOP_LOSS | 1 |
| IMXUSDT | 1h | S7 | long | 1.87 | -1.16 | STOP_LOSS | 1 |
| BANDUSDT | 15m | S6 | long | 1.35 | -0.21 | TRAIL_STOP | 1 |
| VTHOUSDT | 1h | S7 | long | 2.37 | -0.05 | TRAIL_STOP | 1 |
| 1000CHEEMSUSDT | 1h | S7 | long | 2.61 | -1.19 | STOP_LOSS | 1 |
| NOTUSDT | 15m | S6 | long | 3.11 | -1.28 | STOP_LOSS | 1 |
| CUSDT | 15m | S6 | long | 5.51 | -1.08 | STOP_LOSS | 1 |

## Q4: Is Entry Timing the Problem?

### Bars to MAE Distribution (when does the worst drawdown occur?)

| Metric | Value |
|:-------|------:|
| P10 | 0.0 bars |
| P25 | 0.0 bars |
| P50 | 1.0 bars |
| P75 | 1.0 bars |
| P90 | 3.0 bars |
| Mean | 1.1 bars |

### Instant-Death vs Survivors MAE Comparison

- Instant-death trades (bars_held <= 2): 830
- Survivor trades (bars_held > 2): 257
- Median MAE (instant-death): -0.4005R
- Median MAE (survivors): -0.3464R

### Bar-0 Adverse Movement (entry bar is the worst point)

- Trades where MAE occurs on bar 0: 504 (46.4%)
- Median bar-0 MAE: -0.2389R
- P90 bar-0 MAE: -0.9126R

### Bars to MFE Distribution (when does the best point occur?)

| Metric | Value |
|:-------|------:|
| P10 | 0.0 bars |
| P25 | 0.0 bars |
| P50 | 1.0 bars |
| P75 | 1.0 bars |
| P90 | 4.0 bars |
| Mean | 1.3 bars |

## Q5: What Would Optimal Stops Look Like?

### Winner MAE Percentiles by Setup

| Setup | N | P10 | P25 | P50 | P75 | P90 |
|:------|--:|----:|----:|----:|----:|----:|
| S1 | 10 | -0.401 | -0.357 | -0.241 | -0.112 | -0.045 |
| S2 | 13 | -0.493 | -0.320 | -0.231 | -0.181 | -0.082 |
| S3 | 13 | -0.689 | -0.415 | -0.168 | -0.097 | -0.072 |
| S6 | 392 | -0.562 | -0.363 | -0.187 | -0.079 | -0.036 |
| S7 | 52 | -1.622 | -0.980 | -0.457 | -0.152 | -0.065 |
| **ALL** | 480 | -0.681 | -0.397 | -0.205 | -0.085 | -0.039 |

### Stop Calibration Trade-Off Table

If stop were set at the Nth percentile of winner MAE, what % of winners and losers would survive?

| Stop Level (R) | Winner MAE Pctile | % Winners Survive | % Losers Survive | Net Trades Kept |
|---------------:|------------------:|------------------:|-----------------:|----------------:|
| -0.918 | P5 | 456/480 (95.0%) | 449/607 (74.0%) | 905 |
| -0.681 | P10 | 432/480 (90.0%) | 386/607 (63.6%) | 818 |
| -0.524 | P15 | 408/480 (85.0%) | 294/607 (48.4%) | 702 |
| -0.482 | P20 | 384/480 (80.0%) | 270/607 (44.5%) | 654 |
| -0.397 | P25 | 360/480 (75.0%) | 195/607 (32.1%) | 555 |
| -0.348 | P30 | 336/480 (70.0%) | 153/607 (25.2%) | 489 |
| -0.270 | P40 | 288/480 (60.0%) | 86/607 (14.2%) | 374 |
| -0.205 | P50 | 240/480 (50.0%) | 54/607 (8.9%) | 294 |

### Per-Setup Recommendations

**S1** (N=15, WR=66.7%, PnL=$13.94)

- Winner P25 MAE: -0.357R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.401R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.357R): 3 of 5 (60.0%)
- Losers stopped earlier at P10 (-0.401R): 3 of 5 (60.0%)

**S2** (N=17, WR=76.5%, PnL=$72.62)

- Winner P25 MAE: -0.320R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.493R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.320R): 4 of 4 (100.0%)
- Losers stopped earlier at P10 (-0.493R): 2 of 4 (50.0%)

**S3** (N=31, WR=41.9%, PnL=$-125.70)

- Winner P25 MAE: -0.415R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.689R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.415R): 12 of 18 (66.7%)
- Losers stopped earlier at P10 (-0.689R): 7 of 18 (38.9%)

**S6** (N=892, WR=43.9%, PnL=$-2082.76)

- Winner P25 MAE: -0.363R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.562R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.363R): 349 of 500 (69.8%)
- Losers stopped earlier at P10 (-0.562R): 213 of 500 (42.6%)

**S7** (N=132, WR=39.4%, PnL=$-358.92)

- Winner P25 MAE: -0.980R — setting stop here would keep 75% of winners
- Winner P10 MAE: -1.622R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.980R): 46 of 80 (57.5%)
- Losers stopped earlier at P10 (-1.622R): 35 of 80 (43.8%)

## Summary: Findings → Parameters → Expected Impact

| # | Finding | Parameter | Suggested Action | Expected Impact |
|--:|:--------|:----------|:-----------------|:----------------|
| 1 | Winners have shallow MAE | stop_price distance | Consider tightening stops — winner P25 MAE is only -0.397R | Reduced loser damage, minor winner loss |
| 2 | Only 0.0% of FAIL_FAST had MFE >= 1R | FAIL_FAST threshold | FAIL_FAST is well-calibrated | No change needed |
| 3 | 46.4% of trades hit MAE on entry bar | Entry fill model / timing | High bar-0 MAE suggests poor entry timing | Limit orders or delayed entry could help |
