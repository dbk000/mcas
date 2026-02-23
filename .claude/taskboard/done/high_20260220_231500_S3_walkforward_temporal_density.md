REQUESTED_BY: S3
TASK: Walk-forward validation of 5 new ignition filters discovered by S3 hunting. Features: (1) hour_of_day, (2) day_of_week, (3) BTC_regime (trending_up/down/ranging based on 24h return + SMA50), (4) ignition_density (symbols firing per hour), (5) recency_cooldown (hours since last ignition per symbol). All showed IC > 0.02 in-sample. Must validate with 5-fold purged walk-forward per CLAUDE.md §4. Use the existing walkforward harness if possible (see results/filter_walkforward_comparison.json for prior methodology). Data: /srv/mcas/results/ignition_events_with_outcomes.csv (655K events). BTC OHLCV from DB for regime features. Key concerns: (a) recency >72h bin has only 3115 events — may not survive OOS, (b) density could be confounded with market direction, (c) hour/day effects may be unstable across regimes. Test each feature individually AND the best 2-3 combos. Report per §4 standards: fold-level precision, avg daily PF, DSR where applicable. Hunt results at: results/hunting/S3_A_temporal_regime_hunt.json and results/hunting/S3_B_density_clustering_hunt.json
SCOPE: Backend/scripts/, results/
OUTPUT_TO: /srv/mcas/results/walkforward_temporal_density_validation.json
PRIORITY: high
CONSTRAINT: Use all 16 cores for fold parallelism. Check CPU before/during. Runtime target <15min.
AGENT_TYPE: compute
DEPENDS_ON: none
CLAIMED_BY: S3
CLAIMED_AT: 2026-02-20T23:20:00Z
COMPLETED_AT: 2026-02-20T23:35:00Z
RESULT: 2/5 individual passed WF (density +3.4pp, recency_gt_12h +2pp). Hour/day/regime failed OOS. DSR=1.92 (p=0.027).
