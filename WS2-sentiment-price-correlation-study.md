# WS2: Sentiment–Price Correlation Study — Full Research Program

## Objective

Map the complete relationship between social sentiment patterns (frequency, velocity, content type) and price behaviour (OHLCV across all timeframes) for penny coins. The output is calibrated, walk-forward-validated thresholds for the ignition strategy — not exploratory charts.

## Prerequisites

- **WS1 must be completed first.** The autopsy findings tell us where to focus. Do not start WS2 without WS1's classification table and findings summary.
- All data sources used in WS1 must be available for the full historical dataset, not just expired opportunities.

## Procedure

### Phase 1: Dataset Construction

1. Build a master dataset of ALL penny coins the system has tracked — not just misses. Include coins that spiked, coins that didn't, coins the system traded, and coins it ignored.
2. For each coin, align:
   - OHLCV across all available timeframes (1m, 5m, 15m, 1h, 4h, 1d)
   - Sentiment time series: mention count, mention velocity (mentions/hour), rolling mention acceleration
   - Sentiment content: categorise each mention using the same taxonomy from WS1 (hype/speculation, technical analysis, news/announcement, whale/wallet, exchange listing, other)
   - System state: was a position opened? If so, entry time, exit time, PnL.
3. Label each coin-period with an outcome: **SPIKED** (>X% move within Y hours of first sentiment signal) or **NO_SPIKE**. Define X and Y thresholds explicitly before proceeding — use WS1 findings to inform sensible values.

### Phase 2: Feature Engineering

Build the following features for each coin at each timestamp. Do not add features beyond this list without asking.

**Sentiment frequency features:**
- Mention count (rolling 1h, 4h, 12h, 24h windows)
- Mention velocity (mentions/hour, rolling)
- Mention acceleration (change in velocity)
- Time since first mention
- Mention count relative to coin's historical baseline (z-score)

**Sentiment content features:**
- Proportion of mentions in each content category (from taxonomy above)
- Dominant content category
- Content diversity score (how many categories are represented)
- Presence/absence of specific high-signal categories (e.g., exchange listing, whale alert)

**OHLCV features (per timeframe):**
- Bollinger Band width (squeeze detection)
- Volume z-score relative to coin's rolling baseline
- Price position within BB (upper/mid/lower)
- Funding rate (where available)
- RSI
- Price change % over last 1h, 4h, 12h, 24h

**Cross-domain features:**
- Sentiment-volume divergence (mentions rising but volume flat, or vice versa)
- Sentiment lead/lag vs price (is sentiment leading price movement or reacting to it?)
- Content-type shift detection (e.g., mentions switch from speculation to news)

### Phase 3: Correlation Analysis

For each feature against the SPIKED/NO_SPIKE outcome:

1. Compute correlation coefficient and statistical significance.
2. Compute at each timeframe independently — do not average across timeframes.
3. Rank features by predictive power.
4. Identify interaction effects: features that are weak alone but strong in combination (e.g., mention velocity + BB squeeze together).
5. Specifically test the WS1 finding: when coins are compressed AND funding rates are negative, does mention velocity become a stronger predictor?

**Output a feature importance table ranked by predictive power, with correlation, p-value, and optimal timeframe for each.**

### Phase 4: Pattern Discovery

Using the top features from Phase 3:

1. Cluster the SPIKED coins by their pre-spike sentiment-price signature. How many distinct patterns exist? (e.g., slow build over days vs explosive burst in hours vs steady mentions with sudden volume confirmation)
2. For each cluster, define the prototype pattern: what does the typical signal sequence look like in terms of mention count, velocity, content type, and OHLCV state?
3. For each cluster, measure: average runway (time from detectable signal to optimal entry), average spike magnitude, average duration of opportunity window.
4. Test: do the MISSED_ENTRY coins from WS1 fall into specific clusters? This tells us whether the system is blind to a particular opportunity type.

### Phase 5: Walk-Forward Validation

This is non-negotiable. Any finding from Phases 3-4 that isn't validated here is not a finding — it's a hypothesis.

1. Split the dataset chronologically. Minimum 6 periods. No lookahead.
2. For each candidate threshold or rule derived from the correlation analysis:
   - Train on the in-sample period.
   - Test on the out-of-sample period.
   - Record: precision, recall, false positive rate, average PnL per signal.
3. A rule only passes validation if it improves on the current system's performance in at least 4 of 6 out-of-sample periods.
4. Report the variance across periods — a rule that works brilliantly in 2 periods and terribly in 4 is not robust, even if the average looks good.

### Phase 6: Parameter Sweep

For each validated rule, sweep the key thresholds:

1. Identify the 2-3 parameters that most affect the rule's behaviour (e.g., mention velocity threshold, BB width threshold, minimum runway time).
2. Define a sweep range for each parameter. Use WS1 findings to set sensible bounds.
3. Run the full grid: every combination of parameter values, evaluated via walk-forward across all 6+ periods.
4. Produce a heatmap of performance (precision × recall, or PnL, whichever is the primary metric) across the parameter space.
5. Identify the optimal region — not a single point, but the zone where performance is robust to small parameter changes. If the optimal point is on a cliff edge (tiny parameter change = massive performance drop), flag it as fragile and widen the threshold.

### Phase 7: Output

Produce exactly four deliverables:

1. **Feature importance table** — ranked features with correlation, significance, optimal timeframe, and interaction effects noted.

2. **Pattern catalogue** — each distinct pre-spike pattern cluster with its prototype signature, runway stats, magnitude stats, and whether current system detects it.

3. **Validated rules** — only rules that passed walk-forward. For each: the rule logic, parameter values (with robust range), walk-forward performance per period, variance, and which WS1 failure mode it addresses.

4. **SOP integration spec** — exact changes to the ignition strategy SOP. Entry conditions, exit conditions, parameter values. Written as numbered steps ready to be inserted into the existing SOP. Each change references which validated rule it comes from and the expected impact based on walk-forward results.

## Rules

- Follow phases in order. Do not skip to pattern discovery without completing correlation analysis.
- Do not use in-sample results to justify any conclusion. Walk-forward or it doesn't count.
- Do not propose rules based on small sample sizes. Flag the minimum sample size per cluster/rule.
- If a feature or rule looks promising but the data is insufficient to validate it, say so explicitly and specify what additional data would be needed.
- Sentiment content analysis must examine actual text/content, not just mention counts. Volume of chatter without content context is insufficient.
- All proposed thresholds must include a robustness range, not a single point value.
- Do not merge WS1 and WS2 analysis. WS1 findings inform WS2 focus areas. They are separate workstreams.
