REQUESTED_BY: S3
TASK: Walk-forward validation of symbol-quality filter. S3 hunting found 14 chronic-fire symbols generate 53.5% of all ignition events at only 2.2% precision (vs 41.6% baseline). Removing these symbols would be the highest-leverage filter available. Two approaches to validate: (1) Static tier filter: exclude symbols with historical precision <40% (trained per fold). (2) Dynamic frequency filter: exclude symbols with >N ignitions in trailing 7 days (adapts to new chronic firers). Both must pass 5-fold purged walk-forward per CLAUDE.md §4. The key question is temporal STABILITY — does a symbol's "chronic firer" status persist out-of-sample? Hunt data shows quarterly Spearman r=0.35 and first/second-half Pearson r=0.72 — promising but needs formal WF confirmation. Data: /srv/mcas/results/ignition_events_with_outcomes.csv. Hunt results: /srv/mcas/results/hunting/S3_B_symbol_quality_hunt.json. ALSO test interaction with the confirmed ignition_density filter from prior WF validation.
SCOPE: Backend/scripts/, results/
OUTPUT_TO: /srv/mcas/results/walkforward_symbol_quality_validation.json
PRIORITY: high
CONSTRAINT: Use multiprocessing for fold parallelism. Check CPU before/during. The static filter requires identifying chronic symbols on TRAIN data only then testing on TEST data — never leak future symbol stats into training.
AGENT_TYPE: compute
DEPENDS_ON: none

CLAIMED_BY: S3
CLAIMED_AT: 2026-02-20T23:40:00Z
