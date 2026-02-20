REQUESTED_BY: S1
TASK: Audit all sentiment and social tables in production DB. For each table: row count, column names/types, date range (min/max timestamp), symbol/source count, granularity, and a sample row. Tables: social_posts, social_signals, social_features_1h, ci_insights. Write structured JSON output.
SCOPE: /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/data_audit_sentiment.json
PRIORITY: high
CONSTRAINT: Run queries via docker exec backend-postgres-1. Keep each query light. Use COUNT(*), MIN/MAX on timestamp columns, COUNT(DISTINCT) on key columns.
AGENT_TYPE: analysis
DEPENDS_ON: none
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20 15:35
COMPLETED_AT: 2026-02-20 15:43
RESULT: 2/4 tables have data. social_posts 285K rows (12 months, 416 symbols, 5 sources). social_features_1h 103K rows (12 months, hourly). social_signals and ci_insights empty.
