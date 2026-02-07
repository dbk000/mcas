-- Migration: Add source to OHLCV unique constraint
-- Canon §1.4A + Corrections Log #10: OHLCV keyed by market (exchange+pair)
-- Date: 2026-02-07
-- Apply AFTER 20260207_rename_open_time_bar_start_ts.sql

SELECT pg_advisory_lock(202602073);

-- Step 1: Drop existing unique constraint (uses bar_start_ts after rename migration)
DO $$
DECLARE
    _constraint_name TEXT;
BEGIN
    SELECT constraint_name INTO _constraint_name
    FROM information_schema.table_constraints
    WHERE table_name = 'ohlcv' AND constraint_type = 'UNIQUE';

    IF _constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE ohlcv DROP CONSTRAINT %I', _constraint_name);
        RAISE NOTICE 'Dropped old unique constraint: %', _constraint_name;
    END IF;
END $$;

-- Step 2: Add new unique constraint including source
ALTER TABLE ohlcv ADD CONSTRAINT ohlcv_symbol_timeframe_bar_start_ts_source_key
    UNIQUE(symbol, timeframe, bar_start_ts, source);

-- Step 3: Ensure source column has NOT NULL and default (already in base schema, but be safe)
DO $$
BEGIN
    -- Backfill any NULLs
    UPDATE ohlcv SET source = 'binance' WHERE source IS NULL;
    RAISE NOTICE 'Backfilled NULL source values';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'source backfill skipped: %', SQLERRM;
END $$;

-- Track migration
INSERT INTO schema_versions (filename, applied_at) VALUES
    ('20260207_add_source_to_ohlcv_unique.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

-- Verify
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'ohlcv'
        AND constraint_type = 'UNIQUE'
        AND constraint_name = 'ohlcv_symbol_timeframe_bar_start_ts_source_key'
    ) THEN
        RAISE EXCEPTION 'Migration failed: new unique constraint not found';
    END IF;
    RAISE NOTICE 'Migration verified: OHLCV unique constraint now includes source';
END $$;

SELECT pg_advisory_unlock(202602073);
