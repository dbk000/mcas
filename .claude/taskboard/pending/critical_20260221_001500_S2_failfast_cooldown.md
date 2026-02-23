# Walk-Forward Validation: FAIL_FAST Cooldown Filter ★★★

**POSTED BY:** S2
**POSTED AT:** 2026-02-21 00:15
**PRIORITY:** critical
**TYPE:** validation

## Why This Is Highest Priority

FAIL_FAST is 58.3% of all system losses (-$39,385 of -$67,575 over 30 days). 83% of FF trades follow another FF on the same symbol/setup/timeframe. A cooldown filter could recover $15K-$32K, potentially flipping the entire system from net negative to net positive.

## What to Validate

### Cooldown Filter
- **Rule:** After a FAIL_FAST exit on (symbol, setup_id, timeframe), suppress the next N entries on that same (symbol, setup_id, timeframe)
- **Variants to test:**
  - N=1 (skip next trade only)
  - N=2 (skip next 2 trades)
  - Time-based: skip for next 6h / 12h / 24h on that tuple
- **In-sample:** FF_after_FF = 5,787 trades at -$5.67 avg = -$32,840 total. TP1_after_FF = only 258 trades at +$4.51. Net expected recovery: ~$31,675.

### Key Concern
- S6 accounts for 60% of all FF trades. The cooldown may primarily be an S6 fix. Must validate per-setup.
- Need to measure: does cooldown just delay losses (FF_after_FF_after_cooldown still loses?) or genuinely filter regime.

## Validation Requirements (per Section 4)
- 5-fold purged rolling walk-forward, 1-day embargo
- ≥4/5 folds profitable improvement for PASS
- Test per-setup AND aggregate
- DSR correction for n_trials (at least 6 variants: N=1,2 × all/S6-only/S9-only)
- Compare against: no filter, cooldown N=1, cooldown N=2, 12h time cooldown

## Source Data
- Hunt results: /srv/mcas/results/hunting/S2_B_20260221_0015_failfast_structural.json
- simulation_results table: 72K+ trades, 30-day window
- Key query: transition matrix (LAG over symbol/setup/tf partition ordered by entry_ts)

## Implementation Notes
- The cooldown filter is an EXIT-triggered ENTRY filter — it uses post-trade information to gate future entries
- This is fundamentally different from pre-trade scoring/filtering
- Must be careful not to look ahead: cooldown applies AFTER exit, not at entry time
- Sequential walk-forward naturally handles this (each fold sees only past exits)
