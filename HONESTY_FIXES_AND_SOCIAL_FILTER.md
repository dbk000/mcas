# Simulation Honesty Fixes + D1 Social Silence Filter

## Context

Big bro reviewed Gemini's adversarial audit findings and CC's Phase 1 reconnaissance. This is the final implementation plan. Five simulation honesty fixes plus a new D1 social silence gate, all designed to produce honest PF numbers and then measure whether social data improves selectivity.

The fixes will make PF worse. The social filter might make it better. We need to measure both independently and in combination.

---

## Part A: Simulation Honesty Fixes (in execution order)

### A1. Entry Bar Exit Evaluation (HIGHEST PRIORITY — ~5-15% of trades affected)

**Problem:** The exit loop starts at `entry_bar_idx + 1`, meaning the entry bar is never checked for stop/TP hits. If a trade enters at bar N's open and bar N's low hits the stop, the simulator doesn't notice until bar N+1. CC's Phase 1 confirmed this affects an estimated 5-15% of D1 trades on 1h crypto bars.

**Implementation:**

1. Change the loop in `opportunity_simulator.py` from `range(entry_bar_idx + 1, max_bar_idx + 1)` to `range(entry_bar_idx, max_bar_idx + 1)`

2. Add an `is_entry_bar` parameter to `exit_checker.check_exit()`:
   ```python
   def check_exit(candle, config, state, is_entry_bar=False):
   ```

3. On the entry bar (`is_entry_bar=True`):
   - DO check if bar_low <= stop_price (longs) or bar_high >= stop_price (shorts) — the stop could be hit on entry day
   - DO check if bar_high >= tp1_price (longs) or bar_low <= tp1_price (shorts) — TP could be hit on entry day
   - DO apply intrabar_policy if both stop and TP are hit
   - DO NOT treat bar_open as a gap-through reference — entry already happened at that price. Suppress the gap-through logic entirely on bar 0
   - The "entry price" for exit evaluation on this bar is `actual_entry_price` (open + slippage), not bar_open

4. Pass `is_entry_bar=(i == entry_bar_idx)` on each iteration of the exit loop

5. Update MAE/MFE tracking to include the entry bar (it currently only tracks entry bar for MAE/MFE but doesn't act on exits)

**Tests:**
- `test_entry_bar_stop_hit.py`: Synthetic 1h bar where low = entry - 2% (below 1.5% stop). Verify trade exits on bar 0 with STOP_LOSS, not bar 1.
- `test_entry_bar_tp_hit.py`: Synthetic bar where high = entry + 12% (above 10% TP1). Verify TP1 on bar 0.
- `test_entry_bar_both_hit.py`: Synthetic bar hitting both stop and TP. Verify intrabar_policy applies on bar 0.
- `test_entry_bar_no_gap_through.py`: Verify gap-through logic is suppressed on entry bar (bar_open should not trigger gap fill logic since entry already happened there).
- Extend `test_exit_checker_intrabar_policy.py` with entry-bar-specific scenario.

### A2. Gap-Through Cap Diagnostic + Removal

**Problem:** `exit_checker.py` caps gap-through fills at `stop_price - 2*R`, fabricating liquidity. CC confirmed there's zero observability — no log, no flag, no field when the cap fires.

**Step 1 — Diagnostic (before removing anything):**

Run the 1535-opp D1 simulation with the current code. For every trade with `exit_reason = 'STOP_LOSS'`, compare `exit_price` to the bar's open. If `exit_price != bar_open` (accounting for slippage), the cap fired. Report:
- Total count of cap activations
- Distribution of how much the cap "saved" each trade (difference between bar_open and capped fill)
- Worst-case: what would the R-multiple have been at bar_open vs at the capped price?

Report these numbers before proceeding to removal.

**Step 2 — Removal (after big bro reviews diagnostic):**

- Remove the `worst_fill` bounding logic from `exit_checker.py`
- Gap-through fills become: `fill_price = bar_open` (plus exit slippage if applicable)
- This applies symmetrically to longs (gap-down through stop) and shorts (gap-up through stop)
- Add a field to SimulationResult or exit metadata: `gap_through = True/False` and `gap_distance_r` so we have permanent observability

**Tests:**
- `test_gap_through_honest.py`: Synthetic bar where open gaps 5R past stop. Verify fill is at bar_open, not capped.
- `test_gap_through_short.py`: Same for shorts — gap up past stop, fill at bar_open.

### A3. Remove R-Multiple Floor (Cleanup)

**Problem:** `min_r_multiple_floor` drops trades based on their outcome — textbook survivorship bias. CC confirmed: default is None, UI passes None, zero trades affected in production. Dead code.

**Implementation:**
- Remove `min_r_multiple_floor` from `SimulationConfig` dataclass
- Remove the check in `_simulate_single()` (lines ~811-820) that sets `data_available = False` based on R-multiple
- Remove from `control_api.py` `SimulationRequest` field
- Remove from `sim_baseline_diff.py` references
- Grep for any other references across the codebase and remove
- **DO NOT touch the causal gate** — `d1_causal_min_stop_pct` etc. stay, they filter on entry-time data which is correct

**Tests:**
- `test_r_floor_removed.py`: Run a simulation with an extreme -10R trade. Verify it appears in results with `data_available=True` and is included in PF calculation.

### A4. Fix Retry-After min → max (Three Files)

**Problem:** `min(retry_after, backoff, max_wait)` ignores the exchange's demanded wait time. Confirmed in two files, a third ignores Retry-After entirely.

**Fixes:**

1. `signal_engine.py:182`:
   ```python
   # OLD: wait = min(retry_after, 2**attempt + jitter, MAX_RETRY_WAIT_S)
   # NEW: wait at LEAST retry_after, but also at least exponential backoff
   wait = min(max(retry_after, 2**attempt + jitter), MAX_RETRY_WAIT_S)
   ```

2. `s9_microstructure.py:140`: Same pattern — find the equivalent line and apply the same fix.

3. `backfill_ohlcv.py`: Currently uses hardcoded backoff and ignores Retry-After header entirely. Add `Retry-After` header parsing matching the pattern in the other two files.

**Tests:**
- `test_retry_after_respected.py`: Mock a 429 response with `Retry-After: 60`. Verify wait time is >= 60s, not 1.5s.

### A5. Add Thread Lock to OHLCVCache

**Problem:** `ohlcv_cache` is a plain dict used by a 10-worker ThreadPoolExecutor with no synchronization. `IndicatorCache` has `_cache_lock` but `OHLCVCache` doesn't.

**Fix:**
- Add `threading.Lock()` to `OHLCVCache.__init__()`
- Wrap `.set()` and `.get()` with `with self._lock:`
- Or if the class is simple enough, switch to using the existing locked `IndicatorCache` pattern

**Tests:**
- Verify existing tests still pass (the lock shouldn't change behavior, just safety)

---

## Part B: D1 Social Silence Filter

### B1. Empirical Analysis (DO THIS BEFORE BUILDING THE GATE)

For every D1 opportunity in the 1535-opp backfill batch (`backfill_20260304_D1v1_8138bd8a`), pull social collector data for that symbol in the N days before the trigger fired.

**Metrics to compute for each opportunity, for N = 1, 3, 7, 14, 21, 30 days before trigger:**
- `total_mentions`: sum of all social mentions for this symbol across all sources
- `unique_sources`: count of distinct sources mentioning this symbol
- `max_single_day_mentions`: peak daily mention count in the window
- `days_since_last_mention`: calendar days from most recent mention to trigger date (infinity if no mentions found)
- `mention_velocity`: total_mentions / N (average daily rate)

**Split the 1535 opportunities into three groups based on simulation outcomes:**
1. **Winners**: positive R-multiple in simulation
2. **Early stop losses**: negative R-multiple AND bars_held < 10
3. **Late exits**: negative R-multiple AND bars_held >= 10

**For each group, report the distribution (median, mean, p25, p75) of each social metric at each N-day window.**

**The hypothesis:** Early stop losses (the PF bleeders) will cluster around HIGHER social activity — meaning the "dormancy" was an illusion because people were already talking about the coin. Winners will cluster around LOWER social activity — genuinely undiscovered setups.

**Output format:**
```
Window: N=7 days
                    | total_mentions | days_since_last | mention_velocity
Winners (n=xxx)     | median=0, p75=2 | median=inf     | median=0.0
Early SL (n=xxx)    | median=5, p75=18| median=3       | median=0.7
Late exits (n=xxx)  | median=1, p75=4 | median=14      | median=0.1
```

If the distributions show clear separation, the optimal threshold will be visible at the crossover point. If they don't separate, the social gate won't help and we skip it.

**Data source:** Check what tables the social collector writes to. Likely `social_mentions` or similar. Query for symbol + timestamp range. If the social data doesn't cover the backfill period (March 2026 opps), note this — the analysis may need to use a different dataset or a shorter lookback.

**Report findings to big bro before building the gate.**

### B2. Gate Implementation (AFTER big bro reviews B1 analysis)

Assuming the empirical analysis shows separation, implement the social silence gate as a new optional D1 trigger gate:

1. **New D1Config fields:**
   ```python
   SOCIAL_SILENCE_ENABLED: bool = False  # off by default
   SOCIAL_SILENCE_WINDOW_DAYS: int = 7   # lookback window (TBD from B1 analysis)
   SOCIAL_SILENCE_MAX_MENTIONS: int = 0  # max mentions allowed in window (TBD from B1)
   ```

2. **New env var overrides:**
   - `D1_SOCIAL_SILENCE_ENABLED` (bool)
   - `D1_SOCIAL_SILENCE_WINDOW_DAYS` (int)
   - `D1_SOCIAL_SILENCE_MAX_MENTIONS` (int)

3. **Gate location:** After the existing gates in `evaluate_trigger()`, before the final trigger fires. This is a filter — if social mentions exceed the threshold, the trigger is suppressed (returns no trigger, not a failure).

4. **Data access:** The gate needs to query social data for the symbol. In the live path, this means a Redis or Postgres query from within `_evaluate_d1()` in signal_worker.py. In the backfill path, it means a Postgres query from `backfill_d1_opportunities.py`. Design the query as a shared utility that both paths can use.

5. **Pipeline integration:** The gate MUST be controllable from Pipeline UI via the LiveConfig propagation system we just built:
   - Add `social_silence_enabled`, `social_silence_window_days`, `social_silence_max_mentions` to the D1 node's parameters in `meta.node_version`
   - Add them to `update_config()` in `D1Evaluator` (extend the `_FLAT_MAP`)
   - Pipeline UI toggle: on/off switch, window days slider, max mentions input
   - Changing the toggle in Pipeline should take effect within 10s via the hot-reload mechanism

6. **Signal payload:** When the gate fires (suppresses a trigger), record it in the D1 state or log:
   ```python
   "social_silence_gate": {
       "enabled": True,
       "window_days": 7,
       "mentions_found": 12,
       "max_allowed": 0,
       "result": "SUPPRESSED"
   }
   ```

7. **Backfill support:** `backfill_d1_opportunities.py` must also respect this gate when `--social-silence` flag is passed, so we can backfill with and without the gate for comparison.

### B3. Comparison Tests (AFTER gate is implemented)

Run the D1 simulation four ways on the same 1535-opp dataset:

| Run | Honesty fixes | Social silence | Purpose |
|-----|--------------|----------------|---------|
| 1 | OFF (pre-fix code) | OFF | Baseline — current PF |
| 2 | ON | OFF | Impact of honesty fixes alone |
| 3 | OFF | ON | Impact of social filter alone (on inflated numbers) |
| 4 | ON | ON | The honest number with social filtering |

For Run 1, use the existing `sim_full_premerge.json` baseline (already captured).

**Report:**
```
                        | Trades | Winners | Win% | PF   | Sharpe | MaxDD
Pre-fix, no social      | xxxx   | xxx     | xx%  | x.xx | x.xx   | xx%
Post-fix, no social     | xxxx   | xxx     | xx%  | x.xx | x.xx   | xx%
Pre-fix, with social    | xxxx   | xxx     | xx%  | x.xx | x.xx   | xx%
Post-fix, with social   | xxxx   | xxx     | xx%  | x.xx | x.xx   | xx%
```

The key number is whether Run 4's PF is still above 1.0 (profitable after honest sim + social filtering) or whether the honesty fixes killed the edge entirely. If Run 4 PF > Run 2 PF, the social filter is adding genuine value by removing the early stop-loss bleeders.

---

## Execution Sequence

1. **A1** — Entry bar fix (write code + tests, run baseline diff, report PF impact)
2. **A2 Step 1** — Gap cap diagnostic only (report count + severity, do NOT remove yet)
3. **A3** — R-floor removal (cleanup, zero PF impact expected)
4. **B1** — Social silence empirical analysis (parallel with A2 review — no code dependency)
5. **CHECKPOINT** — Report A1 PF impact, A2 diagnostic count, and B1 analysis to big bro
6. **A2 Step 2** — Gap cap removal (after big bro reviews diagnostic)
7. **A4** — Retry-After fixes (three files)
8. **A5** — Thread lock
9. **B2** — Social silence gate implementation (after big bro reviews B1 and sets thresholds)
10. **B3** — Four-way comparison matrix
11. **FINAL** — Full test suite, updated D1 trace Section 13 (Reproducibility Gate) with new baseline diff results

---

## Safety Rules

- **Snapshot before every fix.** Before each of A1, A2, A3: save current simulation output for the 1535-opp dataset. After each fix: re-run and diff. Only expected divergences should appear.
- **One fix at a time.** Don't combine fixes in a single diff. Each fix gets its own before/after comparison so we can attribute PF changes to specific fixes.
- **PF decrease is expected and correct.** If PF goes UP after an honesty fix, something is wrong — investigate before proceeding.
- **The social filter is experimental.** If B1 shows no separation between winners and early losers on social metrics, skip B2 entirely. Don't build a gate that the data doesn't support.
- **All new gates must be Pipeline-controllable.** No hardcoded thresholds that can't be changed from Pipeline UI.
