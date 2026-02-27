# WS1: Missed Trades Autopsy — Penny Coin Ignition Strategy

## Objective

Systematically classify why the ignition strategy missed or mishandled a set of expired opportunities. Every coin in the input CSV spiked at some point. The system either never entered, or entered and exited badly. We need to know which, why, and what SOP changes follow.

## Inputs Required

Before starting, confirm you have access to:

1. **Expired opportunities CSV** — list of coins that spiked but the system didn't capture the move. Darren will provide this.
2. **System signal logs** — for each coin in the CSV, the raw filter/signal state at the time the opportunity was live. What did each filter evaluate to? Which filter(s) rejected entry, or what triggered exit?
3. **Backfill sentiment data** — raw social/mention data for each coin covering the period from first mention through to peak price. This includes mention count, timestamps, and content/text of mentions.
4. **OHLCV data** — price and volume data for each coin across all available timeframes for the relevant period.

If any of these are missing or incomplete, stop and flag it. Do not proceed with partial data — the classification will be wrong.

## Procedure

### Step 1: Classify Each Opportunity Into a Failure Mode

For every coin in the CSV, assign exactly one classification:

- **MISSED_ENTRY** — the system never entered. No position was opened.
- **BAD_EXIT** — the system entered but exited before capturing the spike (premature exit, stop-loss hit, bizarre exit trigger).
- **LATE_ENTRY** — the system entered but after the optimal entry point, reducing or eliminating upside.

Record the classification in a new column. Do not proceed to Step 2 until every row is classified.

### Step 2: Three-Timeline Alignment

For each coin, build a synchronised timeline with three tracks:

1. **Price action** — OHLCV from first detectable activity through spike and subsequent price action. Mark: first unusual volume, optimal entry point (in hindsight), peak, and where the system exited (if applicable).
2. **System signal state** — at each key timestamp, what were the filter values? BB squeeze state, funding rate, volume z-score, whatever filters were active. Which filter(s) said no (for MISSED_ENTRY) or triggered exit (for BAD_EXIT)?
3. **Sentiment data** — mention count over time, content summary at each phase. Was sentiment bullish, neutral, or absent at each key timestamp?

Output this as a structured table per coin — not paragraphs.

### Step 3: Mention Runway Analysis

For each coin:

1. Count total mentions between **first mention** and **optimal entry point**.
2. Measure **time elapsed** between first mention and optimal entry point.
3. Calculate **mention velocity** — mentions per hour in the acceleration phase.

Then across the full dataset:

4. Distribution of mention count at optimal entry (histogram — are we looking at 5 mentions or 50?).
5. Distribution of time runway (minutes/hours between first signal and optimal entry).
6. Identify whether the distribution is unimodal or bimodal. If bimodal, these are two different opportunity types and need separate treatment.

### Step 4: Sentiment Content Analysis

For each coin, examine the **content** of the sentiment data (not just volume):

1. What was being said? Categorise mentions into: hype/speculation, technical analysis, news/announcement, whale/wallet alerts, exchange listing, other.
2. Is there a dominant content pattern that precedes successful spikes?
3. For MISSED_ENTRY coins: was there a detectable content signal that the system could have acted on but didn't?

### Step 5: Root Cause Classification

Using Steps 2–4, classify each miss into exactly one root cause:

- **FILTER_TOO_TIGHT** — sentiment and price data showed a valid opportunity, but a specific filter threshold rejected it. Name the filter and the threshold that killed it.
- **EXIT_LOGIC_FAULT** — entry was correct, but exit triggered on noise/retracement before the real move. Name the exit condition that fired.
- **DETECTION_GAP** — sentiment data existed and was bullish, but the system's sentiment processing didn't flag it. The data was there; the interpretation layer failed.
- **DATA_GAP** — no meaningful sentiment signal existed before the spike. The move was driven by something outside our data coverage (on-chain, private groups, exchange announcements, etc.).
- **LATE_DETECTION** — sentiment signal appeared but with insufficient runway (< X minutes or < Y mentions) for the system to act in time.

### Step 6: Confusion Matrix

Build a confusion matrix for the ignition strategy over the analysis period. This requires not just the missed trades but also:

- Trades the system DID take that were profitable (True Positives)
- Trades the system DID take that were not profitable (False Positives)
- Coins the system correctly ignored (True Negatives)
- The missed trades from this CSV (False Negatives)

If TP/FP/TN data isn't readily available, flag it and estimate from system logs. The matrix must exist even if approximate — we need to see all four quadrants to evaluate tradeoffs.

### Step 7: Output

Produce exactly three deliverables:

1. **Classification table** — one row per coin, columns: symbol, failure_mode, root_cause, blocking_filter_or_exit_trigger, mention_count_at_optimal_entry, time_runway_minutes, sentiment_content_category, brief_notes.

2. **Findings summary** — no more than one page. What is the dominant failure mode? What is the dominant root cause? What is the mention runway distribution shape? Is there a single filter or exit condition responsible for the majority of misses?

3. **Proposed SOP changes** — specific, numbered parameter or logic changes to the ignition strategy entry/exit procedures. Each change must reference which root cause it addresses and which coins from the analysis it would have caught. Do NOT propose changes that aren't directly supported by the data.

## Rules

- Do not improvise. Follow these steps in order.
- Do not write narrative explanations of individual coins. Tables and structured data only.
- If data is missing for a step, stop and ask. Do not fill gaps with assumptions.
- Proposed changes must be falsifiable — they should be testable against the dataset to confirm they would have improved outcomes without introducing new false positives.
