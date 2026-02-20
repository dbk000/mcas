REQUESTED_BY: S1
TASK: Run walk-forward validation on top candidate features from W1 as precision filters. Load ranked feature lists from /srv/mcas/results/feature_screen_onchain.json and /srv/mcas/results/feature_screen_sentiment.json. For each top candidate (and combinations), use the harness at Backend/scripts/walkforward_filter_harness.py to test as a precision gate on ignition signals. For each filter: compute precision WITH vs WITHOUT, true positive rejection rate, average daily PF. The winning filter maximises precision_improvement / true_positive_rejection_rate. Output: comparison table, precision/recall curves data, recommended filter.
SCOPE: Backend/scripts/, /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/filter_walkforward_comparison.json
PRIORITY: normal
CONSTRAINT: Follow CLAUDE.md §4. 4-fold walk-forward, 1-day embargo. Test individual features and top-2/top-3 combinations.
AGENT_TYPE: compute
DEPENDS_ON: normal_20260220_153504_S1_feature_engineering_onchain.md, normal_20260220_153505_S1_feature_engineering_sentiment.md, normal_20260220_153503_S1_prepare_walkforward_harness.md
CLAIMED_BY: S2
CLAIMED_AT: 2026-02-20T18:39:24Z
COMPLETED_AT: 2026-02-20T18:50:11Z
COMPLETED_BY: S2
RESULT: 8 individual + 4 combo filters tested. Best individual: cross_whale_per_addr +1.3pp but is volume proxy. Best combo: top-3 onchain 37.1% precision (+12.1pp) at 95% TP rejection. Sentiment genuine but low coverage hurts. cross_whale_per_addr is volume proxy (Q4 only). Honest: no clear dominant filter.
