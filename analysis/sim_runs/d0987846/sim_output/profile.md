# Simulation Profile: d0987846

Generated: 2026-02-14 19:27:01 UTC

## A. Run Metadata

- **Run ID:** `d0987846-dfbd-437a-8cdd-6e7550c7c3d6`
- **Type:** SIMULATION
- **Status:** SUCCESS
- **Name:** simulation_20260214_154646
- **Engine:** 1.0.0
- **Created:** 2026-02-14 15:46:46.717373+00:00
- **Started:** 2026-02-14 15:46:46.795032+00:00
- **Completed:** None
- **Duration:** —s
- **Summary:** `{
  "total_trades": 1220,
  "winning_trades": 503,
  "losing_trades": 717,
  "win_rate": 0.4123,
  "total_r": -214.83,
  "avg_r": -0.1761,
  "max_r": 0.0,
  "min_r": 0.0,
  "gross_pnl": 0.0,
  "total_fees": 0.0,
  "total_slippage": 0.0,
  "net_pnl": -362.63,
  "max_drawdown_pct": 0.0,
  "profit_factor": 0.58,
  "expectancy": -0.1761,
  "bars_processed": 0,
  "assets_processed": 0,
  "assets_skipped": 0,
  "signals_generated": 2753,
  "execution_time_seconds": 233.81427145004272,
  "starting_capi`

## B. Data Quality

- **Total rows:** 2753
- **Data available (valid):** 1220
- **Data unavailable:** 1533
- **With error messages:** 1533

### Data availability by setup

| Setup | Available | Unavailable | % Available |
|:------|----------:|------------:|------------:|
| S1    |        23 |          22 |       51.1% |
| S2    |        12 |          21 |       36.4% |
| S3    |        34 |          42 |       44.7% |
| S6    |       745 |         113 |       86.8% |
| S7    |       406 |        1335 |       23.3% |

### Error taxonomy

| Error | Count |
|:------|------:|
| R-value invalid after slippage |    99 |
| Uneconomic: fees/R=0.32 > max 0.3 |    12 |
| Microcap short blocked: price=0.999900 < min=1.0 |    11 |
| Uneconomic: fees/R=0.31 > max 0.3 |    11 |
| Uneconomic: fees/R=0.33 > max 0.3 |    10 |
| Microcap short blocked: price=0.998600 < min=1.0 |     9 |
| Uneconomic: fees/R=0.34 > max 0.3 |     8 |
| Uneconomic: fees/R=1.60 > max 0.3 |     8 |
| Uneconomic: fees/R=0.38 > max 0.3 |     8 |
| Uneconomic: fees/R=0.45 > max 0.3 |     7 |
| Uneconomic: fees/R=0.37 > max 0.3 |     7 |
| Microcap short blocked: price=0.000004 < min=1.0 |     6 |
| Uneconomic: fees/R=0.30 > max 0.3 |     6 |
| Uneconomic: fees/R=0.41 > max 0.3 |     6 |
| Uneconomic: fees/R=0.42 > max 0.3 |     6 |
| Microcap short blocked: price=0.999500 < min=1.0 |     6 |
| Microcap short blocked: price=0.999800 < min=1.0 |     5 |
| Microcap short blocked: price=0.000022 < min=1.0 |     5 |
| Microcap short blocked: price=0.000006 < min=1.0 |     5 |
| Microcap short blocked: price=0.025887 < min=1.0 |     5 |

## C. Gate Impact

No rejection/gate data found in opportunity payloads.

## D. Segmentation

### Overall

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| ALL                      |   1220 |   41.2% |      -362.63 |      -0.30 |    0.58 |   -0.18 |     -0.24 |        -8.98 |       2.6 |

### D1. By Setup

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1                       |     23 |   52.2% |        -5.57 |      -0.24 |    0.60 |   -0.12 |      0.10 |        -3.57 |       3.0 |
| S2                       |     12 |   58.3% |        -2.89 |      -0.24 |    0.77 |    0.02 |      0.28 |        -5.18 |       2.7 |
| S3                       |     34 |   44.1% |        -5.26 |      -0.15 |    0.68 |   -0.15 |     -0.17 |        -1.92 |       2.7 |
| S6                       |    745 |   44.0% |      -328.55 |      -0.44 |    0.48 |   -0.12 |     -0.18 |        -8.98 |       3.0 |
| S7                       |    406 |   34.7% |       -20.35 |      -0.05 |    0.89 |   -0.30 |     -0.77 |        -3.78 |       1.8 |

### D2. By Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| long                     |    917 |   43.1% |      -135.72 |      -0.15 |    0.76 |   -0.15 |     -0.20 |        -8.98 |       2.8 |
| short                    |    303 |   35.6% |      -226.91 |      -0.75 |    0.25 |   -0.25 |     -0.39 |        -7.18 |       2.0 |

### D3. By Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 15m                      |    993 |   40.8% |      -332.41 |      -0.33 |    0.51 |   -0.21 |     -0.24 |        -8.98 |       2.5 |
| 1h                       |    223 |   43.5% |       -30.23 |      -0.14 |    0.83 |   -0.04 |     -0.20 |        -7.18 |       3.0 |
| 4h                       |      4 |   25.0% |         0.01 |       0.00 |    1.00 |   -0.65 |     -0.82 |        -2.86 |       1.2 |

### D4. By Exit Reason

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| FAIL_FAST                |    345 |    0.0% |      -543.78 |      -1.58 |    0.00 |   -0.47 |     -0.43 |        -8.98 |       1.2 |
| STOP_LOSS                |    276 |    0.0% |      -279.31 |      -1.01 |    0.00 |   -1.45 |     -1.21 |        -7.18 |       1.8 |
| TIME_STOP                |     61 |   27.9% |       -22.72 |      -0.37 |    0.29 |   -0.14 |     -0.16 |        -4.29 |      11.1 |
| TP1                      |    300 |  100.0% |       275.71 |       0.92 |    0.00 |    0.44 |      0.43 |         0.00 |       3.0 |
| TP2                      |     35 |  100.0% |         8.12 |       0.23 |    0.00 |    0.17 |      0.16 |         0.01 |       1.7 |
| TRAIL_STOP               |    203 |   74.4% |       199.35 |       0.98 |   24.22 |    1.09 |      0.42 |        -1.19 |       3.0 |

### D5. By Setup × Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / long                |     13 |   46.2% |        -5.89 |      -0.45 |    0.40 |   -0.28 |     -0.27 |        -3.57 |       2.2 |
| S1 / short               |     10 |   60.0% |         0.32 |       0.03 |    1.08 |    0.08 |      0.13 |        -1.78 |       4.0 |
| S2 / long                |     12 |   58.3% |        -2.89 |      -0.24 |    0.77 |    0.02 |      0.28 |        -5.18 |       2.7 |
| S3 / long                |     31 |   45.2% |        -5.37 |      -0.17 |    0.66 |   -0.14 |     -0.15 |        -1.92 |       2.5 |
| S3 / short               |      3 |   33.3% |         0.10 |       0.03 |    1.12 |   -0.27 |     -0.19 |        -0.69 |       5.0 |
| S6 / long                |    577 |   47.1% |      -117.28 |      -0.20 |    0.70 |   -0.07 |     -0.10 |        -8.98 |       3.2 |
| S6 / short               |    168 |   33.3% |      -211.27 |      -1.26 |    0.16 |   -0.27 |     -0.33 |        -7.18 |       2.4 |
| S7 / long                |    284 |   33.8% |        -4.29 |      -0.02 |    0.97 |   -0.32 |     -0.69 |        -3.78 |       2.0 |
| S7 / short               |    122 |   36.9% |       -16.06 |      -0.13 |    0.65 |   -0.26 |     -1.14 |        -1.38 |       1.3 |

### D6. By Setup × Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / 15m                 |     19 |   57.9% |         0.08 |       0.00 |    1.01 |   -0.06 |      0.16 |        -1.70 |       3.3 |
| S1 / 1h                  |      4 |   25.0% |        -5.65 |      -1.41 |    0.18 |   -0.44 |     -0.55 |        -3.57 |       1.5 |
| S2 / 15m                 |     10 |   60.0% |        -2.66 |      -0.27 |    0.76 |    0.03 |      0.28 |        -5.18 |       2.6 |
| S2 / 1h                  |      2 |   50.0% |        -0.23 |      -0.11 |    0.85 |    0.00 |      0.00 |        -1.54 |       3.0 |
| S3 / 15m                 |     29 |   48.3% |        -2.23 |      -0.08 |    0.81 |   -0.14 |     -0.11 |        -1.92 |       2.9 |
| S3 / 1h                  |      5 |   20.0% |        -3.03 |      -0.61 |    0.40 |   -0.18 |     -0.29 |        -1.76 |       1.8 |
| S6 / 15m                 |    616 |   43.8% |      -278.43 |      -0.45 |    0.46 |   -0.13 |     -0.19 |        -8.98 |       2.9 |
| S6 / 1h                  |    129 |   45.0% |       -50.12 |      -0.39 |    0.58 |   -0.05 |     -0.14 |        -7.18 |       3.6 |
| S7 / 15m                 |    319 |   32.6% |       -49.17 |      -0.15 |    0.63 |   -0.37 |     -1.09 |        -3.17 |       1.6 |
| S7 / 1h                  |     83 |   43.4% |        28.80 |       0.35 |    1.66 |    0.01 |     -0.40 |        -3.78 |       2.4 |
| S7 / 4h                  |      4 |   25.0% |         0.01 |       0.00 |    1.00 |   -0.65 |     -0.82 |        -2.86 |       1.2 |

### D7. By R-Multiple Bucket

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| -1 < R < 0               |    432 |    0.0% |      -577.99 |      -1.34 |    0.00 |   -0.42 |     -0.40 |        -8.98 |       2.2 |
| 0 < R ≤ 1                |    427 |  100.0% |       334.08 |       0.78 |    0.00 |    0.38 |      0.40 |         0.00 |       3.4 |
| 1 < R ≤ 3                |     57 |  100.0% |        65.87 |       1.16 |    0.00 |    1.83 |      1.71 |         0.34 |       3.4 |
| 3 < R ≤ 5                |     15 |  100.0% |        31.22 |       2.08 |    0.00 |    3.79 |      3.87 |         1.09 |       2.2 |
| R > 5                    |      4 |  100.0% |        69.84 |      17.46 |    0.00 |   13.50 |     10.71 |         3.11 |       3.5 |
| R ≤ -1                   |    285 |    0.0% |      -285.66 |      -1.00 |    0.00 |   -1.44 |     -1.20 |        -7.18 |       1.8 |

## E. Exit Mechanics

### E1. Exit Reason Distribution

| Exit Reason  | Count | % of Total |
|:-------------|------:|-----------:|
| FAIL_FAST    |   345 |      28.3% |
| TP1          |   300 |      24.6% |
| STOP_LOSS    |   276 |      22.6% |
| TRAIL_STOP   |   203 |      16.6% |
| TIME_STOP    |    61 |       5.0% |
| TP2          |    35 |       2.9% |

### E2. Bars Held Distribution

| Percentile | Bars |
|-----------:|-----:|
| 5th |    1 |
| 10th |    1 |
| 25th |    1 |
| 50th |    1 |
| 75th |    2 |
| 90th |    7 |
| 95th |    9 |
| Mean |  2.6 |

### E3. Bars Held by Exit Reason

| Exit Reason  | Count | Mean  | Median | Min | Max |
|:-------------|------:|------:|-------:|----:|----:|
| FAIL_FAST    |   345 |   1.2 |    1.0 |   1 |   2 |
| STOP_LOSS    |   276 |   1.8 |    1.0 |   1 |  24 |
| TIME_STOP    |    61 |  11.1 |    9.0 |   5 | 100 |
| TP1          |   300 |   3.0 |    1.0 |   1 |  65 |
| TP2          |    35 |   1.7 |    1.0 |   1 |   9 |
| TRAIL_STOP   |   203 |   3.0 |    1.0 |   1 |  54 |

### E4. TP Invalidation

- **TP invalidated:** 209 / 1220 (17.1%)

| Reason | Count |
|:-------|------:|
| WRONG_SIDE |   157 |
| BELOW_COST |    52 |

### E5. Performance by Hour (UTC)

| Hour | Trades | Win Rate | Avg R   | Total PnL   |
|-----:|-------:|---------:|--------:|------------:|
|    0 |     80 |    27.5% |   -0.41 |     -197.80 |
|    1 |     46 |    26.1% |   -0.25 |      -23.81 |
|    2 |     23 |    47.8% |   -0.35 |        4.47 |
|    3 |     53 |    43.4% |   -0.28 |        2.37 |
|    4 |     47 |    44.7% |   -0.28 |       -5.92 |
|    5 |     34 |    35.3% |   -0.27 |      -20.13 |
|    6 |    112 |    52.7% |    0.24 |        3.76 |
|    7 |     76 |    21.1% |   -0.75 |      -30.94 |
|    8 |     85 |    52.9% |   -0.15 |       -0.52 |
|    9 |     56 |    51.8% |    0.09 |        0.77 |
|   10 |    101 |    26.7% |   -0.57 |      -68.62 |
|   11 |     45 |    37.8% |   -0.37 |      -17.01 |
|   12 |     27 |    40.7% |   -0.37 |      -10.15 |
|   13 |     35 |    45.7% |    0.09 |       -0.62 |
|   14 |     45 |    46.7% |   -0.11 |      -14.62 |
|   15 |     78 |    39.7% |   -0.14 |       -5.47 |
|   16 |     91 |    44.0% |   -0.17 |      -19.77 |
|   17 |     27 |    55.6% |    0.23 |       -0.15 |
|   18 |     17 |    35.3% |   -0.17 |       -6.93 |
|   19 |     18 |    50.0% |    0.01 |        1.35 |
|   20 |     22 |    50.0% |   -0.38 |       -1.43 |
|   21 |     46 |    56.5% |    0.71 |       57.39 |
|   22 |     30 |    40.0% |    0.21 |        3.43 |
|   23 |     26 |    42.3% |   -0.34 |      -12.27 |

## F. Outlier Flags

### F1. Top 10 Winners

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | TNSRUSDT   | 15m | S7    | long  |     24.00 |     12.14 | TRAIL_STOP   |    1 |
| 2 | MEUSDT     | 1h  | S7    | long  |     12.80 |     44.14 | TRAIL_STOP   |   11 |
| 3 | DYMUSDT    | 15m | S7    | long  |      8.62 |     10.46 | TRAIL_STOP   |    1 |
| 4 | COWUSDT    | 15m | S7    | long  |      8.58 |      3.11 | TRAIL_STOP   |    1 |
| 5 | LAUSDT     | 15m | S7    | long  |      4.97 |      2.87 | TRAIL_STOP   |    1 |
| 6 | SKLUSDT    | 15m | S7    | long  |      4.40 |      1.35 | TRAIL_STOP   |    1 |
| 7 | AXSUSDT    | 15m | S7    | short |      4.33 |      1.88 | TRAIL_STOP   |    1 |
| 8 | ACMUSDT    | 15m | S7    | long  |      4.07 |      2.81 | TRAIL_STOP   |    1 |
| 9 | ZENUSDT    | 1h  | S7    | long  |      4.05 |      6.74 | TRAIL_STOP   |   19 |
| 10 | EGLDUSDT   | 15m | S6    | long  |      4.04 |      1.09 | TRAIL_STOP   |    1 |

### F2. Top 10 Losers

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | KAIAUSDT   | 15m | S7    | long  |     -3.30 |     -0.88 | STOP_LOSS    |    1 |
| 2 | AGLDUSDT   | 15m | S7    | long  |     -3.30 |     -0.88 | STOP_LOSS    |    1 |
| 3 | YFIUSDT    | 15m | S7    | long  |     -3.29 |     -0.90 | STOP_LOSS    |    1 |
| 4 | MOVRUSDT   | 15m | S7    | short |     -3.29 |     -0.92 | STOP_LOSS    |    1 |
| 5 | PAXGUSDT   | 15m | S7    | short |     -3.25 |     -0.96 | STOP_LOSS    |    1 |
| 6 | API3USDT   | 15m | S7    | long  |     -3.23 |     -1.13 | STOP_LOSS    |    1 |
| 7 | AUCTIONUSDT | 15m | S7    | short |     -3.22 |     -1.17 | STOP_LOSS    |    1 |
| 8 | TUTUSDT    | 15m | S7    | long  |     -3.21 |     -1.21 | STOP_LOSS    |    1 |
| 9 | BIFIUSDT   | 15m | S7    | short |     -3.20 |     -1.30 | STOP_LOSS    |    1 |
| 10 | JASMYUSDT  | 15m | S7    | long  |     -3.18 |     -1.40 | STOP_LOSS    |    1 |

### F3. Top 10 Longest Held

| # | Symbol     | TF  | Setup | Dir   | Bars | R Multiple | Exit Reason  |
|--:|:-----------|:----|:------|:------|-----:|----------:|:-------------|
| 1 | SKYUSDT    | 15m | S6    | long  |  100 |      0.10 | TIME_STOP    |
| 2 | WBETHUSDT  | 15m | S6    | long  |   65 |      0.50 | TP1          |
| 3 | NXPCUSDT   | 15m | S6    | long  |   54 |      0.55 | TRAIL_STOP   |
| 4 | PUNDIXUSDT | 1h  | S7    | long  |   32 |      0.81 | TIME_STOP    |
| 5 | PUNDIXUSDT | 15m | S7    | long  |   25 |     -0.10 | TRAIL_STOP   |
| 6 | REDUSDT    | 15m | S6    | long  |   24 |     -1.06 | STOP_LOSS    |
| 7 | BCHUSDT    | 1h  | S6    | long  |   23 |      0.77 | TIME_STOP    |
| 8 | SUNUSDT    | 1h  | S6    | long  |   21 |      2.02 | TIME_STOP    |
| 9 | XRPUSDT    | 15m | S6    | long  |   21 |      0.95 | TRAIL_STOP   |
| 10 | ALCXUSDT   | 1h  | S6    | long  |   20 |      0.65 | TP1          |

### F4. Score Anomalies

Score Q25=0.777, Q75=0.908

**High-score losers (score ≥ Q75, R < 0): 178**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| KAIAUSDT   | S7    |  0.948 |     -3.30 | STOP_LOSS    |
| API3USDT   | S7    |  0.972 |     -3.23 | STOP_LOSS    |
| ENSOUSDT   | S6    |  0.915 |     -2.77 | STOP_LOSS    |
| EGLDUSDT   | S7    |  0.919 |     -2.76 | STOP_LOSS    |
| RENDERUSDT | S7    |  0.952 |     -2.51 | STOP_LOSS    |
| JTOUSDT    | S7    |  0.983 |     -2.32 | STOP_LOSS    |
| LTCUSDT    | S7    |  0.950 |     -2.13 | STOP_LOSS    |
| CHZUSDT    | S7    |  0.910 |     -2.10 | STOP_LOSS    |
| GMXUSDT    | S7    |  0.990 |     -2.08 | STOP_LOSS    |
| ATAUSDT    | S7    |  0.915 |     -2.07 | STOP_LOSS    |

**Low-score winners (score ≤ Q25, R > 0): 124**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| LAUSDT     | S7    |  0.741 |      4.97 | TRAIL_STOP   |
| ZENUSDT    | S7    |  0.696 |      4.05 | TRAIL_STOP   |
| YFIUSDT    | S7    |  0.659 |      3.97 | TRAIL_STOP   |
| KSMUSDT    | S7    |  0.686 |      3.87 | TRAIL_STOP   |
| PROMUSDT   | S7    |  0.690 |      3.57 | TRAIL_STOP   |
| 1MBABYDOGEUSDT | S7    |  0.578 |      3.27 | TRAIL_STOP   |
| METISUSDT  | S7    |  0.668 |      3.13 | TRAIL_STOP   |
| RESOLVUSDT | S7    |  0.656 |      2.89 | TRAIL_STOP   |
| SSVUSDT    | S7    |  0.758 |      2.48 | TRAIL_STOP   |
| BERAUSDT   | S7    |  0.511 |      2.46 | TRAIL_STOP   |
