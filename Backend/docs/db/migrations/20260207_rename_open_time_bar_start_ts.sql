-- Migration: Rename ohlcv.open_time → bar_start_ts
-- Canon §1.3: bar_start_ts (UTC) is the canonical candle identifier
-- Date: 2026-02-07
-- Apply AFTER mcas_schema.sql and all prior migrations

SELECT pg_advisory_lock(202602072);

-- Rename column (idempotent check via information_schema)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'ohlcv' AND column_name = 'open_time'
    ) THEN
        ALTER TABLE ohlcv RENAME COLUMN open_time TO bar_start_ts;
        RAISE NOTICE 'Renamed ohlcv.open_time → bar_start_ts';
    ELSE
        RAISE NOTICE 'Column already named bar_start_ts (or does not exist)';
    END IF;
END $$;

-- Recreate index with new name (drop old, create new — idempotent)
DROP INDEX IF EXISTS idx_ohlcv_lookup;
CREATE INDEX IF NOT EXISTS idx_ohlcv_bar_start ON ohlcv(symbol, timeframe, bar_start_ts DESC);

-- Update unique constraint (need to drop and recreate)
-- First find and drop the existing constraint
DO $$
DECLARE
    _constraint_name TEXT;
BEGIN
    SELECT constraint_name INTO _constraint_name
    FROM information_schema.table_constraints
    WHERE table_name = 'ohlcv' AND constraint_type = 'UNIQUE';

    IF _constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE ohlcv DROP CONSTRAINT %I', _constraint_name);
        RAISE NOTICE 'Dropped unique constraint: %', _constraint_name;
    END IF;
END $$;

ALTER TABLE ohlcv ADD CONSTRAINT ohlcv_symbol_timeframe_bar_start_ts_key
    UNIQUE(symbol, timeframe, bar_start_ts);

-- Track migration
INSERT INTO schema_versions (filename, applied_at) VALUES
    ('20260207_rename_open_time_bar_start_ts.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

-- Verify
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'ohlcv' AND column_name = 'bar_start_ts'
    ) THEN
        RAISE EXCEPTION 'Migration failed: bar_start_ts column not found';
    END IF;
    RAISE NOTICE 'Migration verified: ohlcv.bar_start_ts exists';
END $$;

SELECT pg_advisory_unlock(202602072);
