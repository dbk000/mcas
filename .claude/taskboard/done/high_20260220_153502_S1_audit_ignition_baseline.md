REQUESTED_BY: S1
TASK: Extract the ignition model baseline data needed for W2/W3. Pull all ignition signal events (opportunities from the unicorn pipeline) with their outcomes. Need: signal timestamp, symbol, triggered features, actual price movement after signal, whether the trade was a true positive (hit target) or false positive. This becomes the ground truth dataset that all filter candidates will be tested against. Reference existing research scripts in Backend/scripts/research_* for methodology. Write as CSV for easy consumption by other agents.
SCOPE: /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/ignition_baseline_outcomes.csv
PRIORITY: high
CONSTRAINT: Must include every ignition signal with its outcome. Do not filter or deduplicate — we need the full history. Run via docker compose run --rm -T worker.
AGENT_TYPE: compute
DEPENDS_ON: none
CLAIMED_BY: S3
CLAIMED_AT: 2026-02-20T16:51:04Z
COMPLETED_AT: 2026-02-20T18:01:51Z
RESULT: 39,685 opportunities with forward outcomes extracted to /srv/mcas/results/ignition_baseline_outcomes.csv (99.9% coverage, 8 setups, 448 symbols)
