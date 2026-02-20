REQUESTED_BY: S1
TASK: Engineer candidate on-chain features for the ignition precision filter. AUDIT RESULT: derivatives data (funding, OI, taker, long_short) has <2 days — UNUSABLE for backtesting. Focus ONLY on Santiment daily tables with actual depth: network_activity (111K rows, 314 symbols, 365 days, stops 2026-01-21), whale_transactions (60K rows, 186 symbols, 336 days), exchange_flows (57K rows, 176 symbols, 336 days). Build features: (1) whale transaction volume z-score in compression window, (2) exchange net flow direction (accumulation vs distribution), (3) network activity anomaly during compression, (4) whale-to-exchange flow ratio. Screen by IC against ignition outcomes from /srv/mcas/results/ignition_baseline_outcomes.csv. NOTE: data stops 2026-01-21 so backtest window is limited to ignition signals before that date. Output: ranked feature list with IC, coverage %, recommended candidates, and honest assessment of whether the limited window is sufficient.
SCOPE: Backend/scripts/, /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/feature_screen_onchain.json
PRIORITY: normal
CONSTRAINT: Only features with >60% symbol coverage. Data ends 2026-01-21 — flag if too few ignition signals in that window to draw conclusions.
AGENT_TYPE: compute
DEPENDS_ON: high_20260220_153502_S1_audit_ignition_baseline.md
CLAIMED_BY: S2
CLAIMED_AT: 2026-02-20T17:58:09Z
RELEASED_BY: S2
RELEASE_REASON: Baseline superseded by high_20260220_180500_S1_correct_baseline.md — Agent A failed before producing output. DEPENDS_ON must update to new baseline output.
CLAIMED_BY: S2
CLAIMED_AT: 2026-02-20T18:13:30Z
COMPLETED_AT: 2026-02-20T18:39:13Z
RESULT: 52 features from 3 Santiment tables, 38 recommended. Top: cross_whale_per_addr IC=-0.154 (t=-37). Output at /srv/mcas/results/feature_screen_onchain.json
COMPLETED_BY: S2
