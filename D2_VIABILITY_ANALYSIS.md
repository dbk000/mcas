# D2 "Fade the Casino" — Viability Analysis

## Context

The pump cluster analysis (Workstream 2) found 189 clusters with a 34.9% dump rate measured against pre-pump price. But that metric was wrong — it evaluated from a holder's perspective, not a short seller's. Many "sustained breakouts" actually dumped 40-70% from their peaks. The question is whether this is a tradeable short signal.

**Critical constraint:** You cannot short at the peak. In real-time, you don't know it's the peak until price has already fallen. Any backtest that assumes peak entry is look-ahead bias. D2 gets born honest or it doesn't get born.

---

## Phase 1: Honest Delayed-Entry Backtest

### 1.1 Recompute from short seller's perspective

Using the 189 classified pump clusters from Workstream 2, compute for each cluster:

```python
peak_to_72h_return = (post_72h_price - peak_price) / peak_price  # negative = profitable short
peak_to_48h_return = (post_48h_price - peak_price) / peak_price
peak_to_24h_return = (post_24h_price - peak_price) / peak_price
```

Report distribution: what % of clusters show >10%, >20%, >30%, >50% drops from peak at each horizon?

### 1.2 Delayed entry simulation

**You do NOT enter at the peak.** You enter after price has fallen X% from the local high, confirming the reversal has begun. Test entry delays:

```python
ENTRY_DELAYS = [0.10, 0.15, 0.20, 0.25, 0.30]  # enter after 10%, 15%, 20%, 25%, 30% drop from peak
```

For each cluster, for each delay level:

1. **Entry price:** `peak_price * (1 - delay)` — you enter the short after price has dropped this far from peak
2. **Stop loss:** `peak_price * 1.05` — 5% above the peak (if price makes a new high, your thesis is dead)
3. **Take profit:** `pre_pump_price` — full round-trip dump back to starting point
4. **Time stop:** 72 hours from cluster_start — if neither SL nor TP hit, exit at current price

For each trade, compute:
- `risk = entry_price - stop_loss` (what you lose if stopped out)
- `reward = entry_price - tp_price` (what you gain if TP hit)
- `r_multiple = actual_pnl / risk`
- `outcome`: TP_HIT, STOP_LOSS, TIME_STOP

**Critical: Walk forward through OHLCV to determine which exit hits first.** Do not use post_72h_price as a shortcut — a trade might hit the stop at hour 6 and then dump to TP at hour 48. The stop kills you before the thesis plays out. You must check bar by bar.

For the walk-forward, use 1h candles. For each bar after entry:
- If bar_high >= stop_loss → stopped out at stop_loss
- If bar_low <= tp_price → TP hit at tp_price
- If 72h elapsed → exit at current close

### 1.3 Results matrix

For each entry delay level, report:

```
Delay: 20% from peak
Clusters with valid OHLCV: X / 189
Entry possible (price actually dropped 20% from peak within 72h): X
Trades taken: X
TP hit: X (Y%)
Stop loss: X (Y%)
Time stop: X (Y%)
Win rate: X%
Mean R-multiple (winners): X.XX
Mean R-multiple (losers): X.XX
Profit Factor: X.XX
Total PnL ($1000 position): $X
Max consecutive losses: X
```

### 1.4 Viability threshold

The strategy is viable ONLY if:
- PF > 1.3 at a delay of >= 15% (you need breathing room for real-time entry)
- Win rate > 40% OR mean winner R > 3x mean loser R
- At least 50 trades have valid data (statistical significance)
- The stop loss is not hit on >50% of trades (getting squeezed too often)

---

## Phase 2: Mania Intensity as a Predictor

### 2.1 Build the mania intensity feature

For each pump cluster, compute a composite "mania score" from the bot signal data:

```python
mania_features = {
    # Volume of bot attention
    'alert_count': cluster.alert_count,                    # how many bot alerts fired
    'alert_velocity': cluster.alert_count / cluster_duration_hours,  # alerts per hour
    
    # Breadth of bot attention  
    'exchange_count': cluster.exchange_count,               # how many exchanges saw the pump
    'unique_bot_sources': n_distinct_channels,              # how many different bot channels reported it
    
    # Price intensity
    'max_pct_change': cluster.max_pct_change,              # peak single-alert % change
    'cumulative_pct_change': sum of all alert pct_changes,  # total reported movement
    'vol_change_max': cluster.max_vol_change_pct,          # peak volume spike
    
    # Market structure (where available)
    'funding_rate': cluster.funding_rate_at_peak,          # negative = shorts squeezed = more explosive
    'oi_change_max': max OI change across alerts,          # rising OI during pump = leveraged longs piling in
    'bid_ask_imbalance': (total_bids - total_asks) / (total_bids + total_asks),  # order book skew
}
```

### 2.2 Correlate mania intensity with dump severity

For each cluster, compute the dump severity:
```python
dump_severity = (peak_price - post_72h_price) / peak_price  # positive = bigger dump
```

Then test which mania features predict dump severity:

1. **Univariate:** For each mania feature, compute Spearman rank correlation with dump_severity. Report correlation coefficient and p-value.

2. **Threshold analysis:** Split clusters into high-mania (above median on each feature) and low-mania (below median). Compare dump rates and mean dump severity between the two groups.

3. **The key question:** Do the most manic pumps dump harder? If `alert_velocity > 2/hour` AND `exchange_count >= 2` AND `max_pct_change > 15%` clusters have a 70%+ dump rate while calm pumps have a 30% dump rate, the mania score becomes an entry filter.

### 2.3 Mania-filtered backtest

If Phase 2.2 shows that high-mania clusters dump more reliably:

1. Define a mania threshold from the correlation analysis (e.g., top quartile of mania score)
2. Re-run the Phase 1 delayed-entry backtest but ONLY on clusters that exceed the mania threshold
3. Compare PF of mania-filtered vs unfiltered

The hypothesis: filtering for peak mania intensity improves the win rate enough to make the delayed entry viable, because the most manic pumps are the most unsustainable.

### 2.4 Temporal mania pattern

For each cluster, pull the individual bot alert timestamps and compute the alert arrival pattern:

```python
alert_gaps = [alerts[i+1].ts - alerts[i].ts for i in range(len(alerts)-1)]
acceleration = alert_gaps[-1] < alert_gaps[0]  # are alerts coming faster (acceleration) or slower (deceleration)?
```

Test whether **acceleration** (alerts getting closer together) predicts the pump is approaching its peak, while **deceleration** (alerts spacing out) predicts the pump is running out of steam. If acceleration correlates with imminent peak, it's a real-time timing signal for when to enter the short.

---

## Phase 3: Mechanical Feasibility

### 3.1 Shortability check

For each cluster's symbol, determine if it's shortable on Binance Perpetual Futures:

```python
# Check if the symbol has a Binance perp listing
binance_perp_symbols = set()  # load from exchange info or a reference list
shortable = cluster.symbol.replace('USDT', '') + 'USDT' in binance_perp_symbols
```

Report:
- How many of the 189 clusters are on Binance-shortable symbols?
- If we filter to only Binance-shortable symbols, does the PF change?
- What % of clusters are MEXC/Kucoin-only microcaps that can't be shorted?

If >70% of clusters are on non-shortable symbols, the strategy is mechanically unexecutable regardless of the PF.

### 3.2 Capacity assessment

- How many clusters per month on average?
- With a 72h hold time, what's the max concurrent positions?
- What's the typical funding rate environment during these pumps? (Positive funding = you get paid to short. Negative funding = you pay to hold the short.)

---

## Output

Report all results to big bro at a single checkpoint:

```
PHASE 1: Delayed Entry Backtest
  [table of PF/WR/trades for each delay level]
  Viable at 15% delay? YES/NO
  Viable at 20% delay? YES/NO

PHASE 2: Mania Intensity
  [correlation table: mania feature vs dump severity]
  Best predictor: [feature], correlation: [r], p: [p]
  Mania-filtered PF improvement: [X.XX → Y.YY]
  Alert acceleration predicts peak? YES/NO (p=[p])

PHASE 3: Feasibility
  Shortable on Binance: X / 189 (Y%)
  Binance-only PF: [X.XX]
  Mean clusters/month: [X]
  Mean concurrent positions: [X]
  Typical funding during pumps: [X%]

VERDICT: D2 is [VIABLE / NOT VIABLE / NEEDS MORE DATA]
```

---

## Safety Rules

- **No look-ahead.** Entry at peak is forbidden. All entries must be delayed.
- **Walk-forward exits.** Check SL/TP bar by bar. Do not use endpoint prices as shortcuts.
- **This is analysis only.** Do not implement D2 as a strategy. Do not create new evaluator files or signal paths. The output is a viability report, nothing more.
- **Honest about limitations.** If the sample is too small (<50 valid trades at a given delay), say so. Don't report a PF from 12 trades as if it's meaningful.
