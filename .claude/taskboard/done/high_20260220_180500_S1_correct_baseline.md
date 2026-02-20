REQUESTED_BY: S1
TASK: CORRECTED BASELINE — the previous baseline (ignition_baseline_outcomes.csv) contains S1-S9 opportunities, NOT ignition/unicorn events. U1 has zero rows in the opportunities table. The actual ignition events come from the profiler. Run the signal_genesis_ignition script to generate the real ignition event dataset with outcomes. The script is at Backend/scripts/signal_genesis_ignition/signal_genesis_ignition.py (also Backend/scripts/signal_genesis_ignition.py). Previous run found 30,794 events across 365 days with 11.8% base rate. We need this dataset WITH per-event features AND outcomes (MFE, MAE, forward returns) in a format suitable for joining with sentiment/on-chain data by (symbol, timestamp). Reference the existing results at Backend/scripts/signal_genesis_ignition/signal_genesis_ignition_results.json and Backend/scripts/ignition_phase1/phase1v3_365d.json. The phase1v3 script (Backend/scripts/ignition_phase1.py) already computes walk-forward with outcomes — it may be possible to extract the event-level data from that. Output should be CSV with: event_ts, symbol, is_ignition (true/false), all pre-ignition OHLCV features, forward returns at 1h/4h/12h/24h, MFE, MAE. Include both positive events AND the control group (non-ignition bars) for proper IC calculation.
SCOPE: Backend/scripts/, /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/ignition_events_with_outcomes.csv
PRIORITY: high
CONSTRAINT: Run via docker compose run --rm -T worker. This is the critical-path dataset — all feature engineering depends on it. Must use 365 days of 1h OHLCV data. Expect ~30K positive events + control group.
AGENT_TYPE: compute
DEPENDS_ON: none
SUPERSEDES: high_20260220_153502_S1_audit_ignition_baseline.md
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20 18:05
COMPLETED_AT: 2026-02-20 18:15
RESULT: 655K rows (163K ignition + 491K control), 365 days, 496 symbols, 88MB CSV. 44s runtime.
