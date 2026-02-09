-- Migration: Align run_trades production table with mcas_schema.sql
-- Adds ALL columns that may be missing on production.
-- Uses ADD COLUMN IF NOT EXISTS (PG 9.6+) for idempotency.
--
-- Canon §1.10: No schema drift.

BEGIN;

SELECT pg_advisory_lock(hashtext('migration_20260209_align_run_trades'));

-- Status column
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'OPEN';

-- Canon 5A/5E timing columns
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS signal_bar_ts TIMESTAMPTZ;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS since_ts TIMESTAMPTZ;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS action_by_ts TIMESTAMPTZ;

-- Entry columns
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS entry_bar_idx INT;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS signal_price FLOAT;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS entry_price_with_slippage FLOAT;

-- Position
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS position_size FLOAT;

-- Exit columns
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS exit_bar_idx INT;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS exit_price_with_slippage FLOAT;

-- Results columns
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS bars_held INT;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS pnl_pct FLOAT;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS mae FLOAT;
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS mfe FLOAT;

-- Ensure existing columns have correct names (idempotent)
-- setup_score: code references it but it was never a column — add it
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS setup_score FLOAT DEFAULT 0;

-- pnl_net: runs_api.py SELECTs this but schema only has "pnl" — add as alias column
ALTER TABLE run_trades ADD COLUMN IF NOT EXISTS pnl_net FLOAT;

-- Track migration
INSERT INTO schema_versions (filename, applied_at)
VALUES ('20260209_align_run_trades_with_schema.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(hashtext('migration_20260209_align_run_trades'));

COMMIT;
