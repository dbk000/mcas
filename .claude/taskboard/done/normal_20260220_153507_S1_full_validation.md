REQUESTED_BY: S1
TASK: Run the full CLAUDE.md §4 validation suite on the winning filter from W2. Load winning filter config from /srv/mcas/results/filter_walkforward_comparison.json. Execute: (1) 5-fold walk-forward with 1-day embargo — ≥80% folds profitable, (2) holdout evaluation on final 15-20% never-seen data, (3) DSR correction for number of filters tested, (4) cost sensitivity at base/1.5x/2x slippage — must survive 1.5x, (5) true positive rejection rate < 40%. If all pass: write integration spec. If any fail: report best achievable precision and gap analysis.
SCOPE: Backend/scripts/, /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/filter_full_validation.json
PRIORITY: normal
CONSTRAINT: This is the final gate. No further optimization after holdout. Report honestly.
AGENT_TYPE: compute
DEPENDS_ON: normal_20260220_153506_S1_walkforward_filters.md
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20 18:55
COMPLETED_AT: 2026-02-20 18:56
RESULT: PASS ALL GATES — 5/5 folds, holdout 40.6% precision (1.59x lift), DSR 0.999, survives 2x cost, 26.9% TP rejection
