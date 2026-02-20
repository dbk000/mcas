REQUESTED_BY: S1
TASK: Engineer candidate sentiment features for the ignition precision filter. Using the audit results from /srv/mcas/results/data_audit_sentiment.json, build features aligned to the ignition hypothesis. Candidate features include but are not limited to: (1) social volume z-score in 24h before signal, (2) social mention acceleration (rate of change of mentions), (3) sentiment polarity shift, (4) source diversity (how many platforms mentioning the coin), (5) social volume relative to 30d baseline. Screen all features by information coefficient against ignition outcomes from /srv/mcas/results/ignition_baseline_outcomes.csv. Output: ranked feature list with IC, coverage %, and recommended top candidates.
SCOPE: Backend/scripts/, /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/feature_screen_sentiment.json
PRIORITY: normal
CONSTRAINT: Only features with >60% symbol coverage are candidates. Use same IC methodology as research_edge_discovery.py.
AGENT_TYPE: compute
DEPENDS_ON: high_20260220_153501_S1_audit_sentiment.md, high_20260220_153502_S1_audit_ignition_baseline.md
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20 18:00
COMPLETED_AT: 2026-02-20 18:30
RESULT: DEAD END — 2.2% join rate, sentiment data too sparse for point-in-time filtering. All ICs negative. Trying wider window approach next.
