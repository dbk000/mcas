REQUESTED_BY: S2
TASK: Walk-forward validate hour-of-day filter for S2-15m. Hunting found massive PnL dispersion: good hours (2,5,6,9,12,16,17 UTC) avg +11.4 PnL vs bad hours (7,8,10,15,18 UTC) avg -3.5 PnL in simulation_results over 30d. Effect is stable across 3 weekly splits. Need proper 5-fold walk-forward per Section 4 with DSR correction. Test binary filter (accept good hours only) AND continuous hour-score. Use simulation_results joined with opportunities for full feature set. Must test on 90+ days if data available, otherwise use maximum available window.
SCOPE: Backend/scripts/, results/
OUTPUT_TO: /srv/mcas/results/walkforward_s2_hour_filter.json
PRIORITY: high
CONSTRAINT: Must follow Section 4 validation standards — purged walk-forward, holdout, DSR. Use existing walkforward harness if available (check results/ for harness scripts from S2 earlier work). Report average daily PF as success gate.
AGENT_TYPE: compute
DEPENDS_ON: none
HUNTING_REF: /srv/mcas/results/hunting/S2_B_20260220_2328_hour_of_day_filter.json

CLAIMED_BY: S3
CLAIMED_AT: 2026-02-21T00:15:00Z
