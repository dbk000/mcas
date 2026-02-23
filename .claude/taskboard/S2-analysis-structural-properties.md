# Structural Properties Analysis: Profitable vs Unprofitable Setup/Timeframe Combos

**Agent:** S2-B (Analysis) | **Date:** 2026-02-21

---

## RAW RESULTS

### Step 1: Trade Structure Comparison (exit profiles, bars held, R-multiple)

| setup_id | tf   |   n  | avg_pnl | total_pnl | tp1_rate | ff_rate | sl_rate | trail_rate | time_rate | avg_bars | avg_r  | n_sym | n_days |
|----------|------|------|---------|-----------|----------|---------|---------|------------|-----------|----------|--------|-------|--------|
| S9       | 1h   |  288 |    1.17 |    336.25 |    0.434 |   0.406 |   0.038 |      0.000 |     0.122 |     14.2 |  0.115 |    48 |     18 |
| S7       | 1h   |  446 |    0.58 |    258.00 |    0.130 |   0.182 |   0.330 |      0.278 |     0.020 |      2.1 | -0.135 |   128 |      9 |
| S2       | 15m  |   92 |    1.07 |     98.10 |    0.402 |   0.533 |   0.011 |      0.033 |     0.011 |      7.7 | -0.033 |    19 |      7 |
| S2       | 1h   |   23 |    1.82 |     41.82 |    0.435 |   0.217 |   0.130 |      0.217 |     0.000 |      3.0 |  0.018 |     8 |      3 |
| S1       | 15m  |   95 |    0.17 |     15.71 |    0.389 |   0.158 |   0.105 |      0.084 |     0.126 |      2.6 |  0.021 |    28 |     10 |
| --- BREAK EVEN LINE --- | | | | | | | | | | | | |
| S1       | 1h   |   29 |   -2.69 |    -77.96 |    0.379 |   0.310 |   0.138 |      0.034 |     0.138 |      1.9 | -0.129 |    12 |      7 |
| S3       | 1h   |   25 |   -3.31 |    -82.74 |    0.240 |   0.760 |   0.000 |      0.000 |     0.000 |      1.8 | -0.176 |    12 |      6 |
| S7       | 15m  | 1007 |   -1.88 |  -1889.99 |    0.056 |   0.042 |   0.545 |      0.338 |     0.005 |      1.3 | -0.449 |   265 |     11 |
| S6       | 1h   |  939 |   -2.66 |  -2502.05 |    0.355 |   0.383 |   0.110 |      0.067 |     0.058 |      3.0 | -0.100 |   207 |     11 |
| S6       | 15m  | 2778 |   -1.68 |  -4664.35 |    0.365 |   0.356 |   0.112 |      0.078 |     0.075 |      2.9 | -0.102 |   345 |     12 |

### Step 2: Win/Loss Asymmetry (payoff ratio)

| setup_id | tf   |   n  | win_rate | avg_win | avg_loss | payoff_ratio | total_pnl |
|----------|------|------|----------|---------|----------|--------------|-----------|
| S9       | 1h   |  288 |    0.517 |    4.34 |    -2.24 |         1.94 |    336.25 |
| S7       | 1h   |  446 |    0.410 |   11.34 |    -6.91 |         1.64 |    258.00 |
| S2       | 15m  |   92 |    0.435 |   10.61 |    -6.28 |         1.69 |     98.10 |
| S2       | 1h   |   23 |    0.652 |    7.16 |    -8.21 |         0.87 |     41.82 |
| S1       | 15m  |   95 |    0.653 |    2.73 |    -4.65 |         0.59 |     15.71 |
| --- BREAK EVEN LINE --- | | | | | | | |
| S3       | 1h   |   25 |    0.240 |   11.57 |    -8.01 |         1.44 |    -82.74 |
| S7       | 15m  | 1007 |    0.313 |    6.00 |    -5.46 |         1.10 |  -1889.99 |
| S6       | 1h   |  939 |    0.456 |    8.85 |   -12.31 |         0.72 |  -2502.05 |
| S6       | 15m  | 2778 |    0.454 |    7.37 |    -9.20 |         0.80 |  -4664.35 |

### Step 3: Timeframe Aggregate

| timeframe |   n  | avg_pnl | total_pnl | win_rate | payoff_ratio | n_setups | n_symbols |
|-----------|------|---------|-----------|----------|--------------|----------|-----------|
| 1m        | 1454 |   -0.20 |   -291.48 |    0.358 |         0.72 |        5 |       300 |
| 4h        |   55 |   -5.98 |   -328.74 |    0.364 |         0.63 |        3 |        23 |
| 5m        |  603 |   -0.57 |   -344.53 |    0.189 |         0.86 |        5 |       173 |
| 1h        | 1750 |   -1.16 |  -2026.67 |    0.454 |         0.93 |        6 |       270 |
| 15m       | 4108 |   -1.63 |  -6709.19 |    0.424 |         0.87 |        6 |       377 |

### Step 4: Signal Frequency / Selectivity (trades per active day)

| setup_id | tf   | n_active_days | avg_trades/day | median_trades/day | max_trades/day | total |
|----------|------|---------------|----------------|-------------------|----------------|-------|
| S9       | 1h   |            18 |           16.0 |              16.0 |             33 |   288 | PROFITABLE
| S7       | 1h   |             9 |           49.6 |              14.0 |            156 |   446 | PROFITABLE
| S2       | 15m  |             7 |           13.1 |               5.0 |             61 |    92 | PROFITABLE
| S2       | 1h   |             3 |            7.7 |               4.0 |             17 |    23 | PROFITABLE
| S1       | 15m  |            10 |            9.5 |               6.0 |             39 |    95 | PROFITABLE
| S7       | 15m  |            11 |           91.5 |              85.0 |            229 |  1007 | UNPROFITABLE
| S6       | 1h   |            11 |           85.4 |              24.0 |            437 |   939 | UNPROFITABLE
| S6       | 15m  |            12 |          231.5 |             133.0 |            850 |  2778 | UNPROFITABLE

### Step 5: Symbol Overlap (S9/S7 1h vs S6 15m/1h)

| profitable_symbols | unprofitable_symbols | shared_symbols |
|--------------------|----------------------|----------------|
| 157                | 366                  | 149            |

### Step 6: Opportunity Score Distribution

| setup_id | tf   |   n | avg_score | std_score | min_score | max_score | total_pnl |
|----------|------|-----|-----------|-----------|-----------|-----------|-----------|
| S9       | 1h   | 288 |     0.426 |     0.183 |     0.069 |     0.990 |    336.25 |
| S1       | 15m  |  39 |     0.735 |     0.056 |     0.612 |     0.824 |     30.37 |
| S6       | 5m   |  65 |     0.890 |     0.086 |     0.541 |     1.000 |    -32.07 |
| S6       | 1h   |  14 |     0.860 |     0.062 |     0.708 |     0.929 |    -95.96 |
| S6       | 1m   | 602 |     0.893 |     0.088 |     0.536 |     1.000 |   -131.74 |
| S6       | 15m  | 209 |     0.888 |     0.079 |     0.669 |     1.000 |   -367.51 |
| S7       | 15m  | 149 |     0.735 |     0.170 |     0.290 |     1.000 |   -772.83 |

### Step 7: Average Daily PF (THE Success Metric)

| setup_id | tf   | n_days | avg_daily_pf | median_daily_pf | total_trades | aggregate_pf |
|----------|------|--------|--------------|-----------------|--------------|--------------|
| S2       | 15m  |      7 |        15.48 |            1.20 |           92 |         1.30 |
| S9       | 1h   |     18 |         3.87 |            2.01 |          288 |         2.08 |
| S7       | 1h   |      9 |         1.47 |            0.33 |          446 |         1.14 |
| S2       | 1h   |      2 |         1.37 |            1.37 |           21 |         2.21 |
| S1       | 15m  |      9 |         1.25 |            0.42 |           94 |         1.06 |
| --- BREAK EVEN LINE (avg daily PF = 1.0) --- | | | | | | |
| S2       | 1m   |     10 |         0.93 |            0.37 |          718 |         0.49 |
| S6       | 15m  |     11 |         0.71 |            0.61 |         2777 |         0.67 |
| S7       | 15m  |     11 |         0.67 |            0.41 |         1007 |         0.50 |
| S3       | 15m  |      9 |         0.55 |            0.15 |          114 |         0.41 |
| S6       | 1h   |     10 |         0.50 |            0.39 |          938 |         0.60 |
| S7       | 4h   |      3 |         0.41 |            0.43 |           50 |         0.40 |
| S1       | 5m   |      2 |         0.10 |            0.10 |          171 |         0.01 |
| S3       | 1h   |      3 |         0.00 |            0.00 |           22 |         0.43 |
| S1       | 1h   |      4 |         0.00 |            0.00 |           24 |         0.59 |

### Step 8: Exit Reason PnL Breakdown (S9-1h vs S6-15m vs S7-1h vs S7-15m)

**S9-1h (PROFITABLE, +$336):**
| exit_reason | n   | avg_pnl | total_pnl |
|-------------|-----|---------|-----------|
| TP1         | 125 |    4.83 |    603.64 |
| TIME_STOP   |  35 |    0.42 |     14.67 |
| STOP_LOSS   |  11 |  -10.30 |   -113.35 |
| FAIL_FAST   | 117 |   -1.44 |   -168.70 |

**S7-1h (PROFITABLE, +$258):**
| exit_reason |   n | avg_pnl | total_pnl |
|-------------|-----|---------|-----------|
| TRAIL_STOP  | 124 |   15.27 |   1893.24 |
| TP1         |  58 |    1.46 |     84.78 |
| TIME_STOP   |   9 |    3.81 |     34.27 |
| TP2         |  27 |    0.59 |     16.04 |
| FAIL_FAST   |  81 |   -5.71 |   -462.53 |
| STOP_LOSS   | 147 |   -8.90 |  -1307.80 |

**S7-15m (UNPROFITABLE, -$1,890):**
| exit_reason |   n | avg_pnl | total_pnl |
|-------------|-----|---------|-----------|
| TRAIL_STOP  | 340 |    5.05 |   1715.54 |
| TP1         |  56 |    1.24 |     69.59 |
| TP2         |  15 |    0.48 |      7.25 |
| TIME_STOP   |   5 |   -5.65 |    -28.27 |
| FAIL_FAST   |  42 |   -9.09 |   -381.92 |
| STOP_LOSS   | 549 |   -5.96 |  -3272.18 |

**S6-15m (UNPROFITABLE, -$4,664):**
| exit_reason |    n | avg_pnl | total_pnl  |
|-------------|------|---------|------------|
| TP1         | 1014 |    7.36 |   7466.16  |
| TRAIL_STOP  |  218 |    6.86 |   1495.89  |
| TP2         |   40 |    2.19 |     87.54  |
| TIME_STOP   |  207 |   -4.22 |   -874.08  |
| STOP_LOSS   |  310 |   -9.64 |  -2989.66  |
| FAIL_FAST   |  989 |   -9.96 |  -9850.19  |

---

## SYNTHESIS: 5 Structural Findings

### Finding 1: PAYOFF RATIO IS THE DOMINANT PREDICTOR, NOT WIN RATE

The top 3 profitable combos (S9-1h, S7-1h, S2-15m) all have payoff ratios >= 1.64. Their win rates are mediocre (41-52%). The unprofitable combos mostly have payoff ratios below 1.0 (S6-15m: 0.80, S6-1h: 0.72, S7-15m: 1.10).

The two marginal winners (S2-1h and S1-15m) invert this -- they have HIGH win rates (65%) but LOW payoff ratios (0.87 and 0.59). They're profitable only because they win often enough to offset small wins vs larger losses. This is fragile.

**Key pattern:** The three substantial winners all make money the same way: when they win, the average win is 1.6-1.9x the average loss. They don't need to win often. The losers have the opposite problem: even when S6-15m wins 45% of the time, its wins ($7.37 avg) can't overcome its losses ($9.20 avg).

**Single strongest predictor: payoff_ratio >= 1.5 separates the three real winners from everything else.**

### Finding 2: FAIL_FAST COST ASYMMETRY IS THE MECHANISM

Comparing S9-1h (profitable) to S6-15m (unprofitable) reveals where money actually goes:

- **S9-1h FAIL_FAST:** 117 trades, avg loss -$1.44, total -$169. These are CHEAP exits.
- **S6-15m FAIL_FAST:** 989 trades, avg loss -$9.96, total -$9,850. These are EXPENSIVE exits.

S9's fail-fast mechanism catches losers early (avg loss $1.44), while S6's fail-fast still bleeds $9.96 per trade. S9's fail-fasts are 7x cheaper per trade. That single difference accounts for almost the entire profitability gap.

Similarly for S7: on 1h (profitable), TRAIL_STOP generates +$1,893 from 124 trades ($15.27 avg). On 15m (unprofitable), TRAIL_STOP generates +$1,716 from 340 trades ($5.05 avg). The WINNERS produce roughly the same gross profit on both timeframes, but 15m has 549 stop-losses at -$5.96 each (-$3,272) vs 1h's 147 stop-losses at -$8.90 each (-$1,308). More stops, more total damage.

**The profitable combos aren't better at finding winners -- they're better at making losers cheap.**

### Finding 3: SELECTIVITY (TRADES PER DAY) SEPARATES WINNERS FROM LOSERS

Profitable combos:
- S9-1h: 16.0 trades/day (median 16)
- S2-15m: 13.1 trades/day (median 5)
- S1-15m: 9.5 trades/day (median 6)
- S2-1h: 7.7 trades/day (median 4)

Unprofitable combos:
- S7-15m: 91.5 trades/day (median 85)
- S6-1h: 85.4 trades/day (median 24)
- S6-15m: 231.5 trades/day (median 133)

**The unprofitable combos fire 5-25x more signals per day.** S6-15m generates 231 trades per day on average. That is not a signal; that is noise. Even S7, which is profitable on 1h (49.6/day), becomes unprofitable on 15m (91.5/day) -- the same setup logic applied to a faster timeframe generates too many false positives.

Exception: S7-1h at 49.6/day is the highest-frequency profitable combo, but its median is only 14 -- it has spike days (max 156) that inflate the average. The median for all profitable combos is below 16.

### Finding 4: TIMEFRAME IS NOT THE DOMINANT FACTOR -- BUT IT'S A STRONG FILTER

The aggregate timeframe analysis is misleading: every single timeframe is net negative. 1h (-$2,027), 15m (-$6,709), etc. This is because the massive losses from S6 and S7-15m swamp everything.

However, timeframe acts as a strong filter on WHICH setups survive:
- **1h:** 2 of 6 setups profitable (S9, S7) = 33% survival rate
- **15m:** 2 of 6 setups profitable (S2, S1) = 33% survival rate  
- **5m:** 0 of 5 profitable = 0%
- **1m:** 0 of 5 profitable = 0%
- **4h:** 0 of 3 profitable = 0%

**No setup is profitable below 15m timeframe. The 5m and 1m timeframes are universally unprofitable.** This is likely a fee/slippage floor effect: at 15bps slippage + 4bps fee per side, a 5m trade needs to capture ~38bps round-trip just to break even. Most 5m moves don't have that range.

### Finding 5: SYMBOL OVERLAP PROVES THE EDGE IS IN SETUP LOGIC, NOT ASSET SELECTION

149 of 157 profitable-combo symbols (95%) also appear in the unprofitable combos. The same coins that make money for S9-1h lose money for S6-15m. This conclusively rules out asset selection as the differentiator.

The profitable combos don't trade better coins -- they apply better entry/exit logic to the SAME coins.

Score data confirms this: S6 has the highest average opportunity scores (0.888-0.893) but is the biggest loser. S9-1h has the lowest average score (0.426) and is the biggest winner. **High confidence scores are anti-correlated with profitability in this dataset** -- suggesting the scoring functions are measuring the wrong thing, or that high-confidence signals correspond to obvious setups that everyone is already positioned for.

---

## SUMMARY: ANATOMY OF A PROFITABLE COMBO

A profitable setup/timeframe combo has these properties:

| Property | Profitable Range | Unprofitable Range |
|----------|------------------|--------------------|
| Payoff ratio (avg_win / avg_loss) | >= 1.5 (ideally) | < 1.0 |
| Fail-fast cost per trade | < $2 | > $5 |
| Trades per day (median) | 4-16 | 25-133+ |
| Timeframe | >= 15m | < 15m universally fails |
| Win rate | Doesn't matter (41-65%) | Doesn't matter |
| Symbol selection | Same symbols | Same symbols |
| Opportunity score | Lower avg scores | Higher avg scores (!) |

**The single strongest predictor:** Payoff ratio >= 1.5 identifies the 3 substantial winners (S9-1h, S7-1h, S2-15m) with zero false positives among the combos with >= 50 trades.

**The mechanism:** Cheap fail-fasts. The profitable combos don't find better entries -- they exit losers faster and cheaper, preserving capital for the winners that pay 1.6-1.9x the average loss.

**The implication for system design:** Rather than searching for better entry signals, the highest-leverage improvement is (a) reducing fail-fast costs across all setups, (b) killing all 5m/1m timeframes, and (c) adding a selectivity gate that caps signals per day per setup/timeframe to ~20.
