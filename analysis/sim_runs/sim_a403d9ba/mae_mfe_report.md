# MAE/MFE Calibration Report

**Trades analyzed:** 638
**Winners:** 221 (34.6%)
**Losers:** 417 (65.4%)
**Total PnL:** $-2021.26

## Q1: Are Stops Too Tight?

**Sample:** 638 trades (221 winners, 417 losers)

### Winner MAE Distribution (how far winners dip against you)

| Metric | Value |
|:-------|------:|
| P10 | -1.0273 R |
| P25 | -0.5937 R |
| P50 | -0.3072 R |
| P75 | -0.1210 R |
| P90 | -0.0552 R |
| Mean | -0.4923 R |
| Min (worst) | -4.3935 R |

- Winners with MAE worse than -0.5R: 67 (30.3%)
- Winners with MAE worse than -0.75R: 42 (19.0%)
- Winners with MAE worse than -0.9R: 29 (13.1%)

### Near-Miss Analysis (STOP_LOSS trades that had profit potential)

- STOP_LOSS trades: 160
- STOP_LOSS with MFE >= 1.0R (would have hit TP1): **18** (11.2%)
- STOP_LOSS with MFE >= 0.5R (had meaningful favorable move): **52** (32.5%)

### STOP_LOSS MFE Distribution

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.0000 R |
| P50 | 0.2150 R |
| P75 | 0.6838 R |
| P90 | 1.0408 R |
| Mean | 0.5374 R |
| Max | 11.0221 R |

### Winner MAE by Setup x Timeframe

| Setup | Timeframe | N | Median MAE | P25 MAE | P75 MAE |
|:------|:----------|--:|-----------:|--------:|--------:|
| S6 | 15m | 106 | -0.2384 | -0.4593 | -0.1107 |
| S6 | 1h | 59 | -0.3180 | -0.7786 | -0.1190 |
| S7 | 1h | 39 | -0.3906 | -1.0694 | -0.1580 |
| S7 | 4h | 5 | -0.3711 | -3.1680 | -0.1405 |

## Q2: Is FAIL_FAST Too Aggressive?

**FAIL_FAST trades:** 170 (26.6% of all trades)

### FAIL_FAST MFE Distribution

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.0000 R |
| P50 | 0.1544 R |
| P75 | 0.3315 R |
| P90 | 0.5689 R |
| Mean | 0.2347 R |
| Max | 1.8017 R |

### FAIL_FAST Opportunity Cost

- MFE >= 1.0R (would have hit ~TP1): **5** (2.9%)
- MFE >= 0.5R (meaningful favorable move): **22** (12.9%)
- MFE < 0.1R (correct to exit — no favorable move): **77** (45.3%)

### FAIL_FAST Threshold vs Winner MAE

- Median winner MAE: -0.3072R
- Median FAIL_FAST MFE: 0.1544R
- If FAIL_FAST threshold were widened to median winner MAE (-0.3072R), some FAIL_FAST exits might have become winners.

### FAIL_FAST by Setup

| Setup | N | Median MFE | % with MFE >= 0.5R |
|:------|--:|-----------:|-------------------:|
| S1 | 1 | 0.0000 | 0.0% |
| S2 | 4 | 0.7905 | 75.0% |
| S3 | 9 | 0.2739 | 33.3% |
| S6 | 132 | 0.1321 | 9.8% |
| S7 | 24 | 0.1765 | 12.5% |

## Q3: Are Targets Too Conservative?

### MFE Distribution (all trades)

| Metric | Value |
|:-------|------:|
| P10 | 0.0000 R |
| P25 | 0.1540 R |
| P50 | 0.6695 R |
| P75 | 1.3199 R |
| P90 | 2.5678 R |
| P95 | 3.8076 R |
| Mean | 1.0743 R |
| Max | 18.3311 R |

### MFE Buckets

| MFE Range | Count | % |
|:----------|------:|--:|
| < 0.25R | 188 | 29.5% |
| 0.25R - 0.5R | 81 | 12.7% |
| 0.5R - 1.0R | 149 | 23.4% |
| 1.0R - 1.5R | 82 | 12.9% |
| 1.5R - 2.0R | 55 | 8.6% |
| 2.0R - 3.0R | 37 | 5.8% |
| >= 3.0R | 46 | 7.2% |

### TP1 Exits: Profit Left on Table

- TP1 trades: 184
- Median additional MFE beyond TP1 exit: 0.9951R
- Mean additional MFE beyond TP1 exit: 1.5304R
- P90 additional MFE beyond TP1 exit: 3.4454R

### Losers That Were Once Winners

- Losers total: 417
- Losers with MFE >= 1.0R before reversing: **34** (8.2%)
- Losers with MFE >= 0.5R before reversing: **157** (37.6%)

### Losers with MFE >= 1R — Detail

| Symbol | TF | Setup | Dir | MFE R | Final R | Exit Reason | Bars |
|:-------|:---|:------|:----|------:|--------:|:------------|-----:|
| NEARUSDT | 15m | S6 | short | 1.11 | -0.20 | TRAIL_STOP | 1 |
| SOLUSDT | 15m | S6 | short | 1.90 | -0.24 | TRAIL_STOP | 1 |
| TAOUSDT | 15m | S6 | short | 2.57 | -0.36 | TRAIL_STOP | 1 |
| RAREUSDT | 15m | S6 | long | 1.28 | -1.05 | STOP_LOSS | 1 |
| UNIUSDT | 15m | S6 | short | 1.34 | -0.16 | TRAIL_STOP | 6 |
| DUSKUSDT | 15m | S6 | long | 1.28 | -0.16 | FAIL_FAST | 1 |
| ZAMAUSDT | 15m | S3 | long | 1.12 | -1.79 | STOP_LOSS | 1 |
| ILVUSDT | 1h | S3 | long | 1.32 | -1.04 | STOP_LOSS | 1 |
| ACEUSDT | 15m | S2 | long | 1.80 | -0.46 | FAIL_FAST | 1 |
| AXSUSDT | 1h | S1 | long | 1.50 | -1.19 | STOP_LOSS | 1 |
| IMXUSDT | 1h | S7 | long | 5.23 | -1.22 | STOP_LOSS | 1 |
| WLFIUSDT | 15m | S6 | long | 1.71 | -1.12 | STOP_LOSS | 1 |
| BANDUSDT | 1h | S7 | long | 1.08 | -0.83 | FAIL_FAST | 1 |
| XAIUSDT | 1h | S7 | long | 1.19 | -0.36 | TRAIL_STOP | 1 |
| ANIMEUSDT | 15m | S6 | long | 1.38 | -0.49 | TIME_STOP | 9 |

## Q4: Is Entry Timing the Problem?

### Bars to MAE Distribution (when does the worst drawdown occur?)

| Metric | Value |
|:-------|------:|
| P10 | 0.0 bars |
| P25 | 0.0 bars |
| P50 | 1.0 bars |
| P75 | 1.0 bars |
| P90 | 2.0 bars |
| Mean | 0.8 bars |

### Instant-Death vs Survivors MAE Comparison

- Instant-death trades (bars_held <= 2): 548
- Survivor trades (bars_held > 2): 90
- Median MAE (instant-death): -0.6637R
- Median MAE (survivors): -0.4454R

### Bar-0 Adverse Movement (entry bar is the worst point)

- Trades where MAE occurs on bar 0: 310 (48.6%)
- Median bar-0 MAE: -0.4463R
- P90 bar-0 MAE: -1.3674R

### Bars to MFE Distribution (when does the best point occur?)

| Metric | Value |
|:-------|------:|
| P10 | 0.0 bars |
| P25 | 0.0 bars |
| P50 | 0.0 bars |
| P75 | 1.0 bars |
| P90 | 2.0 bars |
| Mean | 0.9 bars |

## Q5: What Would Optimal Stops Look Like?

### Winner MAE Percentiles by Setup

| Setup | N | P10 | P25 | P50 | P75 | P90 |
|:------|--:|----:|----:|----:|----:|----:|
| S2 | 6 | -0.804 | -0.599 | -0.412 | -0.333 | -0.257 |
| S6 | 165 | -0.859 | -0.543 | -0.259 | -0.112 | -0.054 |
| S7 | 44 | -2.869 | -1.139 | -0.389 | -0.145 | -0.077 |
| **ALL** | 221 | -1.027 | -0.594 | -0.307 | -0.121 | -0.055 |

### Stop Calibration Trade-Off Table

If stop were set at the Nth percentile of winner MAE, what % of winners and losers would survive?

| Stop Level (R) | Winner MAE Pctile | % Winners Survive | % Losers Survive | Net Trades Kept |
|---------------:|------------------:|------------------:|-----------------:|----------------:|
| -1.444 | P5 | 210/221 (95.0%) | 327/417 (78.4%) | 537 |
| -1.027 | P10 | 199/221 (90.0%) | 257/417 (61.6%) | 456 |
| -0.852 | P15 | 188/221 (85.1%) | 214/417 (51.3%) | 402 |
| -0.711 | P20 | 177/221 (80.1%) | 176/417 (42.2%) | 353 |
| -0.594 | P25 | 166/221 (75.1%) | 129/417 (30.9%) | 295 |
| -0.507 | P30 | 155/221 (70.1%) | 100/417 (24.0%) | 255 |
| -0.400 | P40 | 133/221 (60.2%) | 72/417 (17.3%) | 205 |
| -0.307 | P50 | 111/221 (50.2%) | 47/417 (11.3%) | 158 |

### Per-Setup Recommendations

**S6** (N=483, WR=34.2%, PnL=$-1566.60)

- Winner P25 MAE: -0.543R — setting stop here would keep 75% of winners
- Winner P10 MAE: -0.859R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-0.543R): 223 of 318 (70.1%)
- Losers stopped earlier at P10 (-0.859R): 144 of 318 (45.3%)

**S7** (N=118, WR=37.3%, PnL=$-306.81)

- Winner P25 MAE: -1.139R — setting stop here would keep 75% of winners
- Winner P10 MAE: -2.869R — setting stop here would keep 90% of winners
- Losers stopped earlier at P25 (-1.139R): 41 of 74 (55.4%)
- Losers stopped earlier at P10 (-2.869R): 18 of 74 (24.3%)

## Summary: Findings → Parameters → Expected Impact

| # | Finding | Parameter | Suggested Action | Expected Impact |
|--:|:--------|:----------|:-----------------|:----------------|
| 1 | Winners have deep MAE | stop_price distance | Stops may need widening — winner P25 MAE is -0.594R | More winners survive, but losers run longer |
| 2 | Only 2.9% of FAIL_FAST had MFE >= 1R | FAIL_FAST threshold | FAIL_FAST is well-calibrated | No change needed |
| 3 | 8.2% of losers had MFE >= 1R | TP1/TP2 levels | Some losers visited profitable territory — targets may be missing the move | Earlier TP1 could capture these |
| 4 | 48.6% of trades hit MAE on entry bar | Entry fill model / timing | High bar-0 MAE suggests poor entry timing | Limit orders or delayed entry could help |
