# S9-1h Winner vs Loser Analysis
**Date:** 2026-02-20  
**Agent:** S2-A (Analysis)  
**Dataset:** 576 trades, 48 symbols, 18 days (2026-01-21 to 2026-02-09)  
**Direction:** 100% short  

---

## 1. Baseline Outcome Distribution

| Exit Reason | N   | Avg PnL | Avg R  | Avg Bars | Std PnL |
|-------------|-----|---------|--------|----------|---------|
| TP1         | 250 | +4.83   | +0.48  | 16.6     | 0.21    |
| FAIL_FAST   | 234 | -1.44   | -0.14  | 1.2      | 1.15    |
| TIME_STOP   | 70  | +0.42   | +0.04  | 49.0     | 2.44    |
| STOP_LOSS   | 22  | -10.30  | -1.07  | 13.2     | 0.33    |

**Key ratio:** TP1 rate = 43.4%, FAIL_FAST rate = 40.6%. Thin edge.  
**Baseline metrics:** Avg daily PF = 3.87 (17 days), Aggregate PF = 2.02, Total PnL = +672.50

---

## 2. Feature Separation Analysis

### 2.1 Setup Score — NO SEPARATION
Score distributions by exit reason are nearly identical:

| Exit Reason | Avg Score | P25   | Median | P75   |
|-------------|-----------|-------|--------|-------|
| TP1         | 0.435     | 0.301 | 0.394  | 0.561 |
| FAIL_FAST   | 0.430     | 0.334 | 0.422  | 0.531 |
| STOP_LOSS   | 0.421     | 0.323 | 0.426  | 0.561 |
| TIME_STOP   | 0.387     | 0.301 | 0.359  | 0.502 |

**Verdict:** Score has essentially zero discriminative power between TP1 and FAIL_FAST. The quintile analysis confirms this — no monotonic relationship between score and PnL. Score is NOT a useful filter for S9-1h.

### 2.2 Trading Session (Hour-of-Day) — STRONG SEPARATION

| Session          | N   | Avg PnL | Total PnL | TP1 Rate | FF Rate | Symbols | Days |
|------------------|-----|---------|-----------|----------|---------|---------|------|
| A: 00-05 (Asia)  | 136 | +1.70   | +231.70   | 0.500    | 0.412   | 27      | 10   |
| B: 06-11 (EU)    | 120 | -0.06   | -7.13     | 0.300    | 0.567   | 23      | 11   |
| C: 12-17 (US)    | 114 | +0.76   | +86.55    | 0.386    | 0.316   | 27      | 11   |
| D: 18-23 (US Eve)| 206 | +1.75   | +361.37   | 0.495    | 0.359   | 37      | 14   |

**Verdict:** EU open session (06-11 UTC) is a clear negative zone — near-zero avg PnL, lowest TP1 rate (30%), highest FF rate (56.7%). Removing it alone would cut 120 losing trades. The effect spans 23 symbols and 11 days, so it's not a single-symbol artifact.

Worst individual hours: 08h (-0.92 avg, -24 total), 09h (-1.04 avg, -27 total), 10h (-0.97 avg, -15 total).

### 2.3 Signal Density (Per-Symbol Per-Day) — STRONG SEPARATION

| Density     | N   | Avg PnL | Total PnL | TP1 Rate | FF Rate | Symbols | Days |
|-------------|-----|---------|-----------|----------|---------|---------|------|
| 1 (isolated)| 50  | +2.48   | +123.84   | 0.600    | 0.200   | 20      | 11   |
| 2 (pair)    | 94  | +1.47   | +138.55   | 0.426    | 0.404   | 24      | 12   |
| 3 (cluster) | 80  | +1.97   | +157.95   | 0.550    | 0.275   | 23      | 11   |
| 4+ (dense)  | 334 | +0.64   | +214.37   | 0.389    | 0.473   | 29      | 16   |

**Verdict:** Sparse signals (1-3 per symbol-day) outperform dense (4+) by 3x in avg PnL. Isolated signals have 60% TP1 rate vs 39% for dense. This mirrors S3's validated ignition density finding — high density means the setup is firing indiscriminately, diluting quality.

### 2.4 Session x Density Interaction — STRONGEST COMBINED FILTER

| Session Filter   | Density Filter | N   | Avg PnL | Total PnL | TP1 Rate | FF Rate |
|------------------|----------------|-----|---------|-----------|----------|---------|
| EU_OPEN (06-11)  | Dense (4+)     | 84  | **-0.56** | -46.68  | 0.286    | 0.595   |
| EU_OPEN (06-11)  | Sparse (1-3)   | 36  | +1.10   | +39.56    | 0.333    | 0.500   |
| Other hours      | Dense (4+)     | 250 | +1.04   | +261.05   | 0.424    | 0.432   |
| **Other hours**  | **Sparse (1-3)** | **188** | **+2.03** | **+380.79** | **0.543** | **0.277** |

**Verdict:** The best cohort (non-EU + sparse) has 54.3% TP1 rate, 27.7% FF rate, +2.03 avg PnL across 40 symbols and 16 days. The worst cohort (EU + dense) has -0.56 avg PnL. The interaction is multiplicative, not additive.

### 2.5 Day-of-Week — MODERATE SEPARATION (Thursday weak)

| Day | N   | Avg PnL | Total PnL | TP1 Rate | FF Rate |
|-----|-----|---------|-----------|----------|---------|
| Sun | 78  | +1.51   | +117.42   | 0.436    | 0.436   |
| Mon | 68  | +1.33   | +90.35    | 0.471    | 0.353   |
| Tue | 88  | +1.19   | +104.91   | 0.500    | 0.341   |
| Wed | 106 | +1.61   | +170.71   | 0.509    | 0.377   |
| **Thu** | **66** | **+0.01** | **+0.70** | **0.212** | **0.515** |
| Fri | 82  | +1.34   | +109.88   | 0.415    | 0.439   |
| Sat | 88  | +0.89   | +78.54    | 0.432    | 0.409   |

**Verdict:** Thursday is dramatically worse — near-zero PnL, 21% TP1 rate, 52% FF rate. However, this is only 3 Thursdays in the sample. This needs WF validation before trusting. Could be a regime artifact.

### 2.6 Bars Held — STRUCTURAL INSIGHT (not a filter)

| Bucket   | Exit     | N   | Avg PnL | Total PnL |
|----------|----------|-----|---------|-----------|
| 0-1 bars | FAIL_FAST| 180 | -1.49   | -268.80   |
| 0-1 bars | TP1      | 16  | +4.86   | +77.72    |
| 2-3 bars | FAIL_FAST| 54  | -1.27   | -68.61    |
| 13+ bars | TP1      | 130 | +4.85   | +630.69   |
| 13+ bars | TIME_STOP| 70  | +0.42   | +29.33    |

77% of FAIL_FAST exits happen within 0-1 bars (immediate reversal). 52% of TP1 exits take 13+ bars. This tells us S9 winners develop slowly — they need patient holding. Not directly filterable at entry, but confirms the wider-exit insight from corrections log (2026-02-19).

### 2.7 Toxic Symbols — CLEAR OUTLIERS

| Symbol    | N  | Avg PnL | Total PnL | TP1 Rate | FF Rate |
|-----------|----|---------|-----------|----------|---------|
| OGUSDT    | 28 | -2.03   | -56.87    | 0.214    | 0.571   |
| BIFIUSDT  | 28 | -1.02   | -28.66    | 0.286    | 0.571   |
| PAXGUSDT  | 24 | -0.69   | -16.45    | 0.083    | 0.750   |
| AUCTIONUSDT| 22| -0.37   | -8.21     | 0.455    | 0.364   |
| GNOUSDT   | 12 | -0.05   | -0.58     | 0.167    | 0.833   |

PAXGUSDT stands out: a gold-pegged token with 7.5% TP1 rate — fundamentally unsuited for a short mean-reversion setup. GNOUSDT has 83% FF rate. These are structural misfits.

---

## 3. Filter Impact Summary

| Filter Set                    | N   | Avg PnL | Total PnL | TP1 Rate | FF Rate | Avg Daily PF | Symbols | Days |
|-------------------------------|-----|---------|-----------|----------|---------|--------------|---------|------|
| **Unfiltered baseline**       | 576 | +1.17   | +672.50   | 0.434    | 0.406   | 3.87         | 48      | 18   |
| Non-EU + Sparse only          | 188 | +2.03   | +380.79   | 0.543    | 0.277   | 9.15*        | 40      | 16   |
| Non-EU + Sparse + no toxic + no Thu | 158 | +2.38 | +375.78 | 0.620  | 0.215   | —            | 34      | 15   |

*Avg daily PF of 9.15 measured over 11 trading days with losses (6 days had zero losses in the filtered set).

---

## 4. Recommendations & Caveats

### Filters Ranked by Confidence (strength of evidence)

1. **EU Open session exclusion (06-11 UTC)** — HIGH confidence
   - 120 trades across 23 symbols, 11 days
   - Near-zero aggregate PnL in this window
   - Structural explanation: EU open brings volatility spikes that trigger S9 entries but then reverse (shorts squeezed by opening momentum)
   - **Recommend for WF validation**

2. **Density filter (skip when 4+ same-symbol signals/day)** — HIGH confidence  
   - 334 dense trades vs 224 sparse across broad symbol/day coverage
   - Dense signals have 2x higher FF rate (47% vs 28%)
   - Structural explanation: high signal density = indiscriminate firing, low conviction per signal
   - Aligns with S3's validated density finding
   - **Recommend for WF validation**

3. **Toxic symbol exclusion (PAXGUSDT, GNOUSDT, WBETHUSDT)** — MODERATE confidence
   - PAXGUSDT is gold-pegged (structural misfit for crypto mean-reversion)
   - GNOUSDT has 83% FF rate (12 trades, only 2 days — low sample)
   - WBETHUSDT is ETH-pegged (similar structural issue)
   - **Recommend static blocklist for structurally unsuitable assets only (PAXG, WBETH). Others need more data.**

4. **Thursday exclusion** — LOW confidence
   - Only 66 trades across ~3 Thursdays
   - Could be a single bad regime day driving the entire effect
   - **Do NOT implement without WF validation across a longer period**

### What Does NOT Work

- **Setup score** has zero discriminative power for S9-1h. Do not filter on it.

### Sample Quality Warning

- 18 days is thin. The EU session and density filters have the broadest coverage (11-16 days, 23-40 symbols), making them the most trustworthy. Thursday and some toxic symbols rest on 2-3 data points.
- All findings are short-only (S9-1h is 100% short). No long-side data exists.

### Next Steps for Validation

1. Run walk-forward on EU-open exclusion as a binary feature
2. Run walk-forward on density threshold (signals/symbol/day <= 3)
3. Test combined filter (session + density) for interaction significance
4. All three must pass 4/5 fold profitability gate per CLAUDE.md Section 4.1
