-- Migration: Rename run_trades.asset -> symbol
-- Aligns production table with mcas_schema.sql (which already uses "symbol").
-- Uses pg_advisory_lock for concurrency safety, IF column EXISTS for idempotency.
--
-- Canon §1.10: No schema drift — schema changes are versioned and repeatable.

BEGIN;

SELECT pg_advisory_lock(hashtext('migration_20260209_rename_run_trades_asset'));

-- Rename column if the old name still exists
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'run_trades' AND column_name = 'asset'
    ) THEN
        ALTER TABLE run_trades RENAME COLUMN asset TO symbol;
        RAISE NOTICE 'Renamed run_trades.asset -> symbol';
    ELSE
        RAISE NOTICE 'run_trades.symbol already exists, skipping rename';
    END IF;
END $$;

-- Update index if it references old column name
DROP INDEX IF EXISTS idx_run_trades_symbol;
CREATE INDEX IF NOT EXISTS idx_run_trades_symbol ON run_trades(symbol, timeframe, status);

-- Track migration
INSERT INTO schema_versions (filename, description, applied_at)
VALUES ('20260209_rename_run_trades_asset_to_symbol.sql', 'Rename run_trades.asset to symbol', NOW())
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(hashtext('migration_20260209_rename_run_trades_asset'));

COMMIT;
