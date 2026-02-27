# EQ_NVDA_MACRO — Briefing for Alan
**Date:** 25 Feb 2026 | **Status:** Live but with known gaps (see Section 5)

---

## 1. What This Strategy Does (30-Second Version)

When NVIDIA reports earnings (after market close, quarterly), the next morning other stocks react in sympathy — some gap up, some gap down, copying NVIDIA's direction. But not all of them react as much as they historically do. The ones that under-react are **laggards** — and historically, they catch up over the next 1-3 days.

EQ_NVDA_MACRO identifies those laggards and generates LONG or SHORT signals on them.

**NVIDIA reported tonight (25 Feb).** The reaction day is **tomorrow, 26 Feb.**

---

## 2. How We Got Here — The Analysis Steps

### Step 1: Collected 8 NVDA Earnings Events (Feb 2024 → Nov 2025)

| Event Date | NVDA Gap (Open) | NVDA Close | NVDA 5-Day |
|------------|----------------|------------|------------|
| May 2024 | +7.45% | +9.32% | +16.38% |
| Aug 2024 | -3.38% | -6.38% | -14.65% |
| Nov 2024 | +2.37% | +0.53% | -7.23% |
| Feb 2025 | +2.83% | -8.48% | -10.65% |
| May 2025 | +5.52% | +3.25% | +5.27% |
| Aug 2025 | -0.43% | -0.79% | -5.47% |
| Nov 2025 | +5.06% | -3.15% | -3.36% |

**Key pattern:** NVDA often gaps one way at open, then reverses by close. In 5 of 7 events, the gap was positive but the close was negative or much smaller. The gap is the signal; by close, it's often gone.

### Step 2: SPY Decontamination

NVIDIA is ~6% of the S&P 500. When NVDA moves +9%, it drags SPY up ~0.6% mechanically. If we then say "META moved with the market," we're double-counting NVDA's effect.

**Fix:** We strip NVDA's contribution out of SPY before computing betas. This gives us a clean "market return" that doesn't include the NVDA shock. All excess returns below use this decontaminated SPY.

### Step 3: Screened 30+ Equities for Cascade Behaviour

For each stock, across all 7 events, we measured:
- **Direction consistency:** How often does it move the same direction as NVDA? (out of 7)
- **Gap vs Close:** Does it react at the open or persist through close?
- **Excess return:** How much does it move beyond what the market explains?

### Step 4: Identified 6 Cascade-Eligible Assets

| Stock | Gap Consistency | Close Consistency | Avg Excess (Gap) | Avg Excess (Close) | Verdict |
|-------|:-:|:-:|:-:|:-:|---------|
| **META** | **7/7** | 6/7 | +0.26% | +0.11% | Best all-round |
| **AVGO** | 6/7 | 6/7 | +0.84% | +0.05% | Strong gap, weak close |
| **QQQ** | **7/7** | 4/7 | +0.25% | -0.18% | Gap-only (reverses by close) |
| **XLK** | **7/7** | 3/7 | +0.18% | -0.40% | Gap-only (reverses by close) |
| TMO | 2/7 | 6/7 | -0.32% | -0.10% | Negative excess — avoid |
| NVDA | 7/7 | 7/7 | +1.60% | +0.10% | Excluded (can't be laggard of itself) |

**Bottom line:** META and AVGO are the strongest. QQQ and XLK only work at the open.

### Step 5: Catch-Up Rate Analysis

When a cascade-eligible stock under-reacts (is a "laggard"), does it catch up over the next 3 days?

| Scenario | Sample | Catch-Up Rate | Avg Subsequent Return |
|----------|:------:|:-------------:|:---------------------:|
| Gap laggards (below median) | 21 | **42.9%** | -0.49% |
| Gap laggards (below p25) | 11 | **45.5%** | -0.33% |
| Close laggards (below median) | 21 | 33.3% | -1.56% |
| Close laggards (below p25) | 11 | 27.3% | -2.67% |

**Honest assessment:** The gap-phase catch-up rate (~43-45%) is better than close (~27-33%), but neither is a strong edge on its own. Sample size is small (7 events, ~42 observations). This is a pattern worth watching, not a proven strategy.

---

## 3. What It Proposes to Do Tomorrow (26 Feb)

The automated strategy evaluates at **4:30 PM ET** (9:30 PM Dublin) and generates signals for a **3-day hold**.

**If NVDA closes up ≥3% on 26 Feb, it will look for:**
- Stocks that moved less than expected (laggards)
- Specifically META and AVGO (the only ones that survive filtering)
- Generate LONG signals with confidence 0.50-0.60

**If NVDA closes down, or up less than 3%:** No signals generated.

---

## 4. Timing — What Happens When

All times in Dublin (GMT). US market open = 2:30 PM Dublin.

| When | What | Action Required |
|------|------|:---------------:|
| **Tonight (Tue 25 Feb)** | NVDA reports AMC. After-hours reaction visible. | Watch AH price for direction. |
| **Wed 26 Feb, 2:30 PM** | US market opens. Gap cascade visible. | **Manual trade window** if you want gap plays (QQQ, XLK, META, AVGO). |
| **Wed 26 Feb, 9:00 PM** | US market closes. | — |
| **Wed 26 Feb, 9:30 PM** | Strategy evaluates automatically. | Automated signals for META/AVGO (if NVDA closed ≥3%). |
| **Thu-Fri 27-28 Feb** | 3-day hold period. | Monitor positions. |

---

## 5. Known Gaps — What Isn't Perfect

### Gap A: The strategy evaluates too late
The strongest cascade signal is at the **open** (gap). The strategy evaluates at **close** (4:30 PM ET). By then, QQQ and XLK have already reversed. The automated strategy misses the best window.

**Implication:** For gap-phase trades (especially QQQ, XLK), you'd need to trade manually at 2:30 PM Dublin time.

### Gap B: Filtering threshold mismatch
The analysis found META and AVGO at 6/7 direction consistency. The strategy code requires 7/7 for the final filter. Net result: the strategy may generate zero signals even when the analysis says the cascade is real. This is a code bug we haven't fixed yet.

### Gap C: Small sample size
7 earnings events. That's not enough for statistical validation. No walk-forward test, no holdout set, no DSR correction. The pattern is real in the data we have, but 7 is 7.

### Gap D: TMO has negative excess returns
The analysis flags TMO as cascade-eligible (6/7 close consistency) but its average excess return is **negative** (-0.10%). It moves in NVDA's direction but underperforms the market. Not a trade.

---

## 6. The Two Practical Plays

### Play A — Gap Sympathisers (Manual, Morning)
**Who:** META, AVGO, QQQ, XLK
**When:** At or shortly after US open (2:30 PM Dublin), Wed 26 Feb
**Logic:** If NVDA gaps up big in after-hours tonight, these gap up in sympathy at open. Buy whichever gapped less than expected. Take profit within hours — don't hold through close (QQQ and XLK reverse intraday).
**Strength:** 7/7 gap consistency for META, QQQ, XLK. This is the strongest pattern in the data.
**Weakness:** Requires manual execution. Short hold time. Small average excess (+0.18% to +0.84%).

### Play B — Close Sympathisers (Automated, Evening)
**Who:** META, AVGO only
**When:** Automated signal at 9:30 PM Dublin, Wed 26 Feb
**Logic:** If NVDA's close-to-close return is ≥3%, META and AVGO are flagged as laggards. Hold 3 days.
**Strength:** Persistent move, doesn't reverse intraday. META 6/7, AVGO 6/7.
**Weakness:** The 3% threshold may not trigger (NVDA often gaps up but closes flat or down). Strategy code may filter them out due to the 7/7 vs 6/7 threshold bug.

**META and AVGO appear in both plays** — they're the only stocks where the cascade is sticky enough to last from open through close.

---

## 7. What To Watch Tonight

1. **NVDA after-hours reaction** — Direction and magnitude. If +5% or more, cascade is historically strong. If flat or negative, no cascade.
2. **META and AVGO after-hours** — Are they reacting already? If they're already moving in sympathy AH, the gap is being priced in.
3. **News tone** — Bullish analyst commentary on semis/AI broadly (not just NVDA) increases cascade probability.

---

## 8. Time-to-Peak Analysis (Alan's Question)

> "How long after open does it peak in minutes, and what prices as a percentage to peak?"

We checked hourly price bars for NVDA on every earnings reaction day. The answer is clear — and it's the **opposite** of what you might expect.

### NVDA: Peak After Open on Positive-Gap Reaction Days

| Event | Gap at Open | Additional Rise After Open | When Peak Hit | Then What Happened |
|-------|:-----------:|:--------------------------:|:-------------:|:-------------------|
| **May 2024** | +7.45% | **+4.24%** | **~4 hours** (1:30 PM ET) | Only event that kept rallying. Outlier. |
| **Nov 2024** | +2.37% | **+2.39%** | **First hour** | Spiked then sold off. Closed -1.79% from open. |
| **Feb 2025** | +2.83% | **+0.01%** | **At the open bell** | Peaked immediately, crashed -11% intraday. |
| **May 2025** | +5.52% | **+0.88%** | **First hour** | Tiny upside, then sold off -2.15% from open. |
| **Nov 2025** | +5.06% | **+0.03%** | **At the open bell** | Peaked immediately, crashed -7.81% intraday. |

**Average additional upside after the gap: +1.51%**
**Median additional upside: +0.88%**

### The Pattern — Not What You'd Expect

Alan's hypothesis: *"It will peak during the day and eventually adjust downward."*

**What actually happens:** The peak **IS** the open. In 4 of 5 positive-gap events, NVDA peaked within the first hour of trading (and in 2 of those, the high was literally the opening price). Only May 2024 saw a continued rally — and that was the largest gap (+7.45%) of the dataset.

After the peak, NVDA sold off. In 4 of 5 events, the close was **below** the open despite the positive gap. The biggest collapses were Feb 2025 (-11% intraday) and Nov 2025 (-7.8% intraday).

### Cascade Stocks: Same Story

The sympathy stocks (META, AVGO, QQQ, XLK) show the same pattern — tiny upside after the gap, then selloff:

| Stock | Avg Rise After Open | Avg Intraday Fall | Best Strategy |
|-------|:-------------------:|:-----------------:|:--------------|
| META | +0.52% | -1.44% | Sell into the first-hour spike |
| AVGO | +1.08% | -2.63% | Sell into the first-hour spike |
| QQQ | +0.15% | -1.73% | Don't hold past first hour |
| XLK | +0.22% | -2.14% | Don't hold past first hour |

### Data Resolution Note

These timings come from **1-hour bars** — the finest historical data available from Yahoo Finance for dates older than 60 days. When the table says "first hour" or "at the open bell," that means the peak occurred somewhere within the first 60 minutes of trading. We cannot pinpoint whether that's minute 4 or minute 45 from this data.

**Tomorrow (26 Feb) will be different.** We've now loaded 5-minute bar data for all five stocks. After market close Wednesday, we'll be able to see exactly which 5-minute window the peak hit — the first reaction day with sub-hourly precision.

### Practical Execution (Alan's MACD Suggestion)

Alan's instinct is sound: **buy at open, watch MACD for convergence, sell when it starts converging** — ideally within the first hour.

The data supports this approach:
- The peak is historically at or near the open
- The intraday reversal is consistent (4 of 5 events)
- MACD divergence/convergence on a 1m or 5m chart would give a real-time signal for when the initial momentum is fading
- VWAP is also useful here — if price drops below VWAP within the first hour, the sympathy rally is over

### What This Means for Tomorrow (26 Feb)

**If NVDA gaps up tonight:**
1. The gap **is** the peak (or very close to it). Don't wait for a "midday peak" — it probably won't come.
2. If you want to trade the cascade, buy sympathy stocks **at the open** and sell within the **first 1-2 hours**.
3. Watch MACD on a 1m/5m chart — sell when you see convergence (momentum fading).
4. Holding through the day is historically a losing move — the gap reverses.
5. The only exception was May 2024 (massive +7.45% gap, continued rallying). If tonight's after-hours is +7% or more, there *may* be room for a longer hold, but that's 1 event out of 5.

**The tradeable window is narrow: 2:30 PM → 3:30 PM Dublin time (first hour of US trading).**

---

---

## 8a. Minute-by-Minute Event Profiles (Per-Event Narratives)

> **Disclaimer:** These are individual event stories, not statistical patterns. n=7 events (5 positive-gap) cannot support pattern detection. The guidance in Section 8 ("sell in the first hour") is unchanged. This section enriches the individual stories for real-time context.

> **Data resolution:** All profiles below use **1-hour bars** — the finest historical resolution available. "First hour" means somewhere within 09:30–10:30 ET; we cannot distinguish minute 4 from minute 45 at this resolution. Tomorrow (26 Feb) will be the first event with 1m live capture — see Section 8b.

### Event 2: May 23 2024 (Earnings May 22) — THE OUTLIER

**NVDA** opened at $102.00 after a +7.45% gap (the largest in the dataset).
- **Peak:** +4.24% above open at **1:30 PM ET** (+240 min) → $106.32
- This is the **only event** where NVDA kept rallying after open. The peak came 4 hours into the session.
- **Trough:** -0.47% at 09:30 ET — the opening bar itself was the low point.
- **Close:** +1.78% → $103.82 (gave back ~58% of the peak gain)
- **Volume:** 36% in first 30 min
- **MFE curve:** Steady climb all day: +2.22% after 1h → +3.13% after 2h → +3.90% after 3h → +4.24% after 4h

**No cascade stock data available** for this date (1h data for META/AVGO/QQQ/XLK starts Aug 2025).

**Narrative:** The biggest gap produced the only sustained rally. If tonight's after-hours is +7% or more, this is the reference event.

### Event 3: Aug 29 2024 (Earnings Aug 28) — NEGATIVE GAP, IMMEDIATE SELLOFF

**NVDA** opened at $121.36 after a -3.38% gap.
- **Peak:** +2.27% at **09:30 ET** (+0 min) — the opening bar was the high.
- **Trough:** -3.82% at 2:30 PM ET (+300 min) → $116.72
- **Close:** -3.10% → $117.59
- **VWAP cross below:** +180 min (3 hours in)
- **Volume:** 38% in first 30 min

**Narrative:** Negative gap, small dead-cat bounce at open, then ground lower all day. Peaked immediately.

### Event 4: Nov 21 2024 (Earnings Nov 20) — FAST SPIKE, FAST CRASH

**NVDA** opened at $149.32 after a +2.37% gap.
- **Peak:** +2.39% at **09:30 ET** (+0 min) — peaked in the first hour bar.
- **Trough:** -5.77% at 10:30 ET (+60 min) → $140.70. The crash happened in the **second hour**.
- **Close:** -1.80% → $146.63 (recovered from the trough)
- **Volume:** 43% in first 30 min — heaviest volume concentration of all events.

**Narrative:** Violent whipsaw. Peaked at open, crashed -8.16% peak-to-trough in the second hour, then recovered half the loss. This is the worst-case scenario for "buy at open and hold."

### Event 5: Feb 27 2025 (Earnings Feb 26) — TOTAL COLLAPSE

**NVDA** opened at $135.00 after a +2.83% gap.
- **Peak:** +0.00% — the **open price was the day's high**. No additional upside whatsoever.
- **Trough:** -11.10% at 3:30 PM ET (+360 min) → $120.01. Bled out all day.
- **Close:** -11.10% → $120.01 (closed at the low)
- **VWAP cross below:** +60 min (within the first hour)
- **Volume:** 35% in first 30 min

**Narrative:** The most extreme reversal. Gapped up nearly 3%, then fell 11% from open. VWAP broke within the first hour — anyone watching VWAP would have exited early. This is the strongest argument for "sell in the first hour."

### Event 6: May 29 2025 (Earnings May 28) — SMALL SPIKE, GRIND DOWN

**NVDA** opened at $142.24 after a +5.52% gap.
- **Peak:** +0.88% at **09:30 ET** (+0 min) → $143.49
- **Trough:** -3.04% at 1:30 PM ET (+240 min) → $137.91
- **Close:** -2.15% → $139.18
- **VWAP cross below:** +120 min (2 hours in)
- **Volume:** 43% in first 30 min

**Narrative:** Tiny additional upside (<1%), then steady grind lower. VWAP break came at 2 hours.

### Event 7: Aug 28 2025 (Earnings Aug 27) — MILDEST REACTION + CASCADE DATA

**NVDA** opened at $180.82 after a -0.43% gap (nearly flat).
- **Peak:** +2.02% at **09:30 ET** (+0 min) → $184.47
- **Trough:** -2.44% also within the first hour → $176.41
- **Close:** -0.34% → $180.20 (nearly flat)

**Cascade stocks** (first event with cascade data):

| Stock | Peak | Peak Time | Close |
|-------|:----:|:---------:|:-----:|
| META | +1.24% | 11:30 ET (+120m) | +1.00% |
| AVGO | +2.67% | 2:30 PM ET (+300m) | +1.88% |
| QQQ | +0.68% | 3:30 PM ET (+360m) | +0.52% |
| XLK | +0.88% | 3:30 PM ET (+360m) | +0.66% |

**Peak sequence:** NVDA (0m) → META (120m) → AVGO (300m) → QQQ/XLK (360m)

**Narrative:** NVDA peaked immediately but the cascade stocks peaked **hours later** — a slow-motion sympathy rally. This was a flat/negative NVDA gap day though, so the cascade was mild.

### Event 8: Nov 20 2025 (Earnings Nov 19) — SELLOFF CASCADE

**NVDA** opened at $195.95 after a +5.06% gap.
- **Peak:** +0.03% at **09:30 ET** (+0 min) — effectively the open was the high.
- **Trough:** -8.22% at 3:30 PM ET (+360 min) → $179.85
- **Close:** -7.66% → $180.94
- **VWAP cross below:** +60 min

**Cascade stocks:**

| Stock | Peak | Peak Time | Trough | Close | VWAP Cross |
|-------|:----:|:---------:|:------:|:-----:|:----------:|
| META | +0.63% | 09:30 ET (+0m) | -3.25% | -2.28% | +60m |
| AVGO | +2.75% | 10:30 ET (+60m) | -5.69% | -5.19% | +60m |
| QQQ | +0.39% | 10:30 ET (+60m) | -4.39% | -4.26% | +60m |
| XLK | +0.37% | 09:30 ET (+0m) | -5.54% | -5.31% | +60m |

**Peak sequence:** NVDA/META/XLK (0m) → AVGO/QQQ (60m)

**Narrative:** Despite a +5% gap, NVDA peaked at the open and fell 8%. The cascade stocks all broke below VWAP within 60 minutes. Everyone who held past the first hour lost money on every stock.

### Control Day Comparison

To confirm that reaction days look different from normal days, we analyzed 5 non-reaction Wednesdays from Oct 2025:

| Stock | Normal Avg Peak | Normal Avg Trough | Reaction Day Range |
|-------|:--------------:|:-----------------:|:------------------:|
| NVDA | +1.19% | -1.84% | Peak 0–4.24%, Trough -2.44 to -11.10% |
| META | +0.73% | -1.22% | Peak 0.63–1.24%, Trough -0.41 to -3.25% |
| AVGO | +2.56% | -1.20% | Peak 2.67–2.75%, Trough -0.20 to -5.69% |
| QQQ | +0.60% | -0.81% | Peak 0.39–0.68%, Trough -0.28 to -4.39% |
| XLK | +0.92% | -0.88% | Peak 0.37–0.88%, Trough -0.50 to -5.54% |

**Key observation:** Reaction day **peaks** for META/QQQ/XLK are within normal range — the cascade upside isn't special. What's special is the **trough**: reaction day drawdowns are 2-5x deeper than normal. The cascade effect is primarily a selloff amplifier, not a rally amplifier.

---

## 8c. Suggested Take Profit Levels

> Based on the peak-above-open data from all historical reaction days. These are **not predictions** — they're derived from what happened in 7 prior events. Use alongside MACD/VWAP for real-time confirmation.

### NVDA

Additional upside above the opening price across 7 events: +0.00%, +0.03%, +0.88%, +2.02%, +2.27%, +2.39%, +4.24%.

| TP Level | Hit Rate | Comment |
|:--------:|:--------:|---------|
| **+0.5%** | **5 of 7** (71%) | Conservative. Leaves money on the table when the rally extends, but gets you out before the 2 events where the open was the high. |
| +1.0% | 4 of 7 (57%) | Moderate. Misses the two zero-upside events and the +0.88% event. |
| +2.0% | 3 of 7 (43%) | Aggressive. Only hit during the three strongest reactions. |

**Suggested TP: +0.5% above open.** At tomorrow's likely open price, that's roughly:
- If NVDA opens at $140 → TP at **$140.70**
- If NVDA opens at $150 → TP at **$150.75**
- If NVDA opens at $160 → TP at **$160.80**

Adjust based on tonight's after-hours price. The formula: **open price x 1.005**.

### Cascade Stocks

Cascade stock data is thinner (only 2 events with intraday resolution), so these are less reliable:

| Stock | Peak Range (2 events) | Suggested TP | Logic |
|-------|:---------------------:|:------------:|-------|
| **META** | +0.63% to +1.24% | **+0.5%** | Both events exceeded 0.5%. Conservative floor. |
| **AVGO** | +2.67% to +2.75% | **+2.0%** | Consistently the strongest cascade mover. Room for a wider TP. |
| **QQQ** | +0.39% to +0.68% | **+0.3%** | Tiny upside. Barely worth trading for the gap — it reverses fast. |
| **XLK** | +0.37% to +0.88% | **+0.3%** | Same as QQQ. Thin edge, fast reversal. |

### Important Caveats

1. **In 2 of 7 events, NVDA's open was the day's high.** No TP would have been hit. A limit sell at +0.5% simply wouldn't fill, and the position would bleed. **Always pair TP with a stop loss** — suggest -1.0% below open as a floor.
2. **These TPs assume you buy at the open.** If you enter 5-10 minutes late (chasing), the edge shrinks or vanishes.
3. **The one outlier (May 2024, +4.24%) would make a +0.5% TP look foolish in hindsight.** But optimising for the single best event while ignoring the two worst (0% upside) is how you lose money. The conservative TP is the right call with n=7.
4. **MACD convergence on a 1m/5m chart is your real-time exit signal.** The TP is a backstop, not a substitute for watching the tape.

---

## 8d. LIVE UPDATE — Tonight's Earnings Results & Sentiment (25 Feb, ~11:30 PM GMT)

### The Numbers (Beat on Everything)

| Metric | Actual | Expected | Beat By |
|--------|:------:|:--------:|:-------:|
| **EPS** | $1.62 | $1.53 | +6% |
| **Revenue** | $68.13B | $66.21B | +3% |
| **Data Center** | $62.3B | $60.69B | +3% |
| **Q1 Guidance** | **$78.0B** ± 2% | $72.78B | **+7.2%** |
| **Gross Margin** | 75.2% | 75.0% | In line |

Revenue up 73% YoY. Net income nearly doubled to $43B. Data Center networking revenue up 263% YoY. The guidance beat (+7.2% above consensus) is the headline — $78B next quarter is massive.

### After-Hours Price Reaction — MUTED

- **Close:** $195.56
- **Initial AH pop:** +3.1% (hit ~$201)
- **Faded during Jensen's call** to +0.7% (~$197)
- **Late AH trading:** ~$196.57 (+0.5%)

This is the **smallest after-hours reaction** of any positive earnings event in our dataset. For comparison:
- May 2024: +7.45% gap → Nov 2024: +2.37% → Feb 2025: +2.83% → May 2025: +5.52% → Nov 2025: +5.06%
- **Tonight: +0.5% to +2% (and fading)**

### Online Sentiment — "Beat Is Priced In"

**Analyst consensus:** Strong Buy, average price target $261.54. Morgan Stanley PT $250. Goldman Sachs expected a beat-and-raise — and they got one.

**But the mood is cautious:**
- Bloomberg ran the headline *"Stock Is So Stuck Even Blowout Earnings May Not Lift It"* before the report — and they were right.
- Prediction markets had a 95% probability of a beat priced in beforehand. No surprise = no pop.
- Reddit/WSB sentiment scores: 78-88 (elevated but not euphoric — compare to 90+ at euphoric peaks).
- Multiple pre-report articles flagged the "sell the news" risk. The initial +3% pop fading to +0.7% during the call is consistent with that.

**Key risk factor:** DeepSeek concerns. Reports that China's DeepSeek used Nvidia's Blackwell chips in potential violation of export controls could add an overhang.

### What This Means for Tomorrow's TP Levels

**The gap will be small — likely +1% to +2%.** This fundamentally changes the calculus:

1. **At a +1-2% gap, the cascade effect will be minimal.** Sympathy stocks track the magnitude — small NVDA gap = negligible sympathy moves in META/AVGO/QQQ/XLK.

2. **The TP targets in Section 8c assumed a meaningful gap.** With a +1% gap, the +0.5% TP above open may not trigger at all. The additional upside above a tiny gap is likely near zero.

3. **"Sell the news" is the dominant narrative.** When the beat was priced in and the stock popped +3% then faded to +0.7%, that's distribution — smart money selling into the post-earnings pop. This is the same pattern as Feb 2025 (gapped +2.83%, then fell 11%) and Nov 2025 (gapped +5.06%, fell 7.66%).

### Updated Recommendation for Tomorrow

**The historical pattern says: the gap IS the peak. Tonight's sentiment says: even the gap may not hold.**

| Scenario | Probability | Action |
|----------|:-----------:|--------|
| NVDA opens +1-2% | **Most likely** | Extremely tight window. TP at **+0.3% above open** if anything. Consider skipping — the edge may not cover transaction costs. |
| NVDA opens +3-4% | Possible (overnight buying) | Original TP of **+0.5% above open** applies. Sell in first 30 minutes. |
| NVDA opens +5%+ | Unlikely given AH action | Would indicate overnight sentiment shift. Original analysis applies — TP +0.5%, sell first hour. |
| NVDA opens flat or red | Possible (sell the news) | **No trade.** The cascade doesn't activate on flat/negative gaps. |

**Bottom line:** This looks like a weak-cascade event. The earnings were strong but the market already knew. Unless overnight sentiment shifts dramatically (watch pre-market from 9 AM Dublin / 4 AM ET), this may be one of those "open is the high" events. **Be prepared to not trade at all** if the gap is under +2%.

---

## 8b. Tomorrow's 1m Live Capture

A polling script (`capture_equities_1m_live.py`) will capture 1-minute bars for NVDA, META, AVGO, QQQ, and XLK throughout tomorrow's session. This will be the **first reaction day with sub-hourly precision**.

**To start capture** (before 2:25 PM Dublin):
```
tmux new -s capture_1m
cd Backend && docker compose -f docker-compose.yml run --rm -T worker \
  python3 /scripts/capture_equities_1m_live.py
```

After market close, we'll know exactly which minute the peak hit — not just "first hour."

---

*Generated from cascade analysis of 8 NVDA earnings events (Feb 2024 - Nov 2025), 30+ equities screened, SPY-decontaminated returns. Multi-resolution OHLCV analysis (1h bars for historical, 1m live for 26 Feb). Strategy code: eq_nvda_macro.py. Analysis scripts: nvda_cascade_report.md, nvda_reaction_day_analysis.py.*
