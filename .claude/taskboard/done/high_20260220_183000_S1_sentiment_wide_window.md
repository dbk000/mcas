REQUESTED_BY: S1
TASK: Re-screen sentiment features with WIDER time windows. Point-in-time (1h match) yielded 2.2% join rate — dead end. Try 7-day and 30-day lookback windows instead. Hypothesis: "has this coin had ANY social activity recently?" as a binary or continuous feature. Key insight from first screen: negative IC means high-buzz coins are LESS likely to ignite (consistent with compression thesis). So the filter direction is "skip high-buzz events" not "take high-buzz events." For each event, compute: (1) has_mentions_7d (binary), (2) mention_count_7d_sum, (3) max_mention_volume_ratio_7d, (4) mean_sentiment_7d, (5) source_diversity_7d, (6) author_novelty_ratio_7d, (7) same features for 30d. Screen by IC against is_ignition. Report coverage at each window width.
SCOPE: Backend/scripts/, /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/feature_screen_sentiment_wide.json
PRIORITY: high
CONSTRAINT: Use ignition_events_with_outcomes.csv (655K rows). Need coverage >30% for any feature to be a viable filter.
AGENT_TYPE: compute
DEPENDS_ON: none
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20 18:30
COMPLETED_AT: 2026-02-20 18:40
RESULT: 30d coverage 48.5% but ALL ICs negative. Social presence is anti-predictive of ignition. Coins with no mentions ignite more. Sentiment cannot serve as a positive precision filter.
