# Migration Rollback Strategy

Canon §1.10: Schema governance requires documented rollback procedures.

## Principles

1. **Forward-only by default** — migrations use `IF NOT EXISTS` / `IF EXISTS` for idempotency.
2. **Rollback = reverse migration** — each migration has a documented rollback procedure below.
3. **Always backup first** — before any rollback, `pg_dump` the affected tables.
4. **Test rollback in staging** — never rollback in production without staging verification.

## Per-Migration Rollback Procedures

### `mcas_schema.sql` (base schema)
**Rollback:** `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` + re-apply.
**Data loss:** ALL DATA. Only use for fresh start.

### `20260203_000000_add_cc_tables.sql`
**Rollback:**
```sql
DROP TABLE IF EXISTS cc_insights CASCADE;
DROP TABLE IF EXISTS cc_conversations CASCADE;
DELETE FROM schema_versions WHERE filename = '20260203_000000_add_cc_tables.sql';
```
**Data loss:** CC conversation/insight data.

### `20260205_add_correlation_analysis.sql`
**Rollback:**
```sql
DROP TABLE IF EXISTS correlation_jobs CASCADE;
DROP TABLE IF EXISTS correlation_results CASCADE;
DELETE FROM schema_versions WHERE filename = '20260205_add_correlation_analysis.sql';
```
**Data loss:** Correlation analysis results.

### `20260205_add_ci_insights.sql`
**Rollback:**
```sql
-- Check what this migration added and reverse it
DELETE FROM schema_versions WHERE filename = '20260205_add_ci_insights.sql';
```

### `20260205_add_simulation_trace_json.sql`
**Rollback:**
```sql
ALTER TABLE run_trades DROP COLUMN IF EXISTS simulation_trace_json;
DELETE FROM schema_versions WHERE filename = '20260205_add_simulation_trace_json.sql';
```
**Data loss:** Simulation trace data on existing trades.

### `20260207_add_signal_events.sql`
**Rollback:**
```sql
DROP TABLE IF EXISTS signal_events CASCADE;
DELETE FROM schema_versions WHERE filename = '20260207_add_signal_events.sql';
```
**Data loss:** All signal event audit logs.

### `20260207_add_schema_versions.sql`
**Rollback:**
```sql
DROP TABLE IF EXISTS schema_versions CASCADE;
```
**Note:** This removes the migration tracking table itself. Re-apply all migrations after.

### `20260207_rename_open_time_bar_start_ts.sql`
**Rollback:**
```sql
ALTER TABLE ohlcv RENAME COLUMN bar_start_ts TO open_time;
DROP INDEX IF EXISTS idx_ohlcv_bar_start;
CREATE INDEX idx_ohlcv_lookup ON ohlcv(symbol, timeframe, open_time DESC);
DELETE FROM schema_versions WHERE filename = '20260207_rename_open_time_bar_start_ts.sql';
```
**Note:** Also revert all Python code referencing `bar_start_ts` back to `open_time`.

### `20260207_add_source_to_ohlcv_unique.sql`
**Rollback:**
```sql
ALTER TABLE ohlcv DROP CONSTRAINT IF EXISTS ohlcv_symbol_timeframe_bar_start_ts_source_key;
ALTER TABLE ohlcv ADD CONSTRAINT ohlcv_symbol_timeframe_bar_start_ts_key
    UNIQUE(symbol, timeframe, bar_start_ts);
DELETE FROM schema_versions WHERE filename = '20260207_add_source_to_ohlcv_unique.sql';
```

### `20260207_partition_signal_events.sql`
**Rollback:**
```sql
-- Recreate as non-partitioned table
CREATE TABLE signal_events_flat AS SELECT * FROM signal_events;
DROP TABLE signal_events CASCADE;
ALTER TABLE signal_events_flat RENAME TO signal_events;
-- Re-add primary key and indexes
ALTER TABLE signal_events ADD PRIMARY KEY (id);
-- Recreate indexes (see 20260207_add_signal_events.sql)
DELETE FROM schema_versions WHERE filename = '20260207_partition_signal_events.sql';
```

## Emergency Recovery

If rollback is not possible:
1. Restore from latest `pg_dump` backup
2. Re-apply migrations up to desired state
3. Verify via `SELECT * FROM schema_versions ORDER BY applied_at;`
