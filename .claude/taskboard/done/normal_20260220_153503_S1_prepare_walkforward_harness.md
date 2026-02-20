REQUESTED_BY: S1
TASK: Prepare the walk-forward validation harness for testing precision filters. Adapt the existing walk-forward framework (reference Backend/scripts/research_* for patterns) to accept external filter features alongside the OHLCV-based ignition signal. The harness should: (1) load ignition baseline outcomes from /srv/mcas/results/ignition_baseline_outcomes.csv, (2) accept a filter function that takes a signal event + supplementary features and returns accept/reject, (3) run 4-fold walk-forward with 1-day embargo, (4) compute per-fold: precision, recall, true positive rejection rate, profit factor, (5) compute aggregate: average daily PF, DSR. Write as a reusable Python module.
SCOPE: Backend/scripts/
OUTPUT_TO: Backend/scripts/walkforward_filter_harness.py
PRIORITY: normal
CONSTRAINT: Must follow CLAUDE.md §4 validation standards exactly. This harness will be used by all sessions for W2 testing.
AGENT_TYPE: analysis
DEPENDS_ON: high_20260220_153502_S1_audit_ignition_baseline.md
CLAIMED_BY: S2
CLAIMED_AT: 2026-02-20T17:56:29Z
COMPLETED_AT: 2026-02-20T18:05:41Z
RESULT: Harness built and smoke-tested — 6 filter configs across 39,549 signals, all passing WF gate
