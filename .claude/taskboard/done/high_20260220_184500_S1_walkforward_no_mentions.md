REQUESTED_BY: S1
TASK: Walk-forward test the "no social mentions" negative filter on ignition events. Finding from sentiment screening: has_mentions_30d has IC=-0.25, lift=0.55 — coins WITHOUT social mentions in last 30 days are ~1.8x more likely to ignite. Test this as a precision filter: take ignition signals ONLY where has_mentions_30d=False (i.e., no social coverage). This keeps ~51.5% of events. Use the walkforward harness at /srv/mcas/Backend/scripts/walkforward_filter_harness.py (already deployed, already fixed to use correct baseline). The filter function is simple: load social_features_1h, for each signal check if any social data exists for that symbol in the 30d before the signal. If yes → reject. If no → accept. Run 4-fold walk-forward. Report: precision WITH filter vs WITHOUT (baseline 23.6%), true positive rejection rate, average daily PF, fold-by-fold results.
SCOPE: Backend/scripts/, /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/walkforward_no_mentions_filter.json
PRIORITY: high
CONSTRAINT: The filter must NOT just be a market cap proxy — check if the precision improvement persists when controlling for trading volume (the ignition model already filters by volume). If after volume control the filter adds nothing, report that honestly.
AGENT_TYPE: compute
DEPENDS_ON: none
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20 18:45
COMPLETED_AT: 2026-02-20 18:55
RESULT: PASS — 25.0% → 35.5% precision, 1.42x lift, 27% TP rejection, 4/4 folds positive, survives volume control
