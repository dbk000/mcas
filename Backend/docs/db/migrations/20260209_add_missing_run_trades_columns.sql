-- Migration: Add remaining missing columns to run_trades
-- The previous align migration missed pnl, r_multiple, fees_paid, trace_json
-- because it assumed they existed on production. They don't.
--
-- Canon §1.10: No schema drift.

BEGIN;

SELECT pg_advisory_lock(hashtext('migration_20260209_add_missing_run_trades'));

-- Results columns the INSERT statement needs
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS pnl FLOAT;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS r_multiple FLOAT;

-- Fees
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS fees_paid FLOAT DEFAULT 0;

-- Audit trace
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS trace_json TEXT;

-- Track migration
INSERT INTO schema_versions (filename, applied_at)
VALUES ('20260209_add_missing_run_trades_columns.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(hashtext('migration_20260209_add_missing_run_trades'));

COMMIT;
