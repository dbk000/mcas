-- Migration: Rename run_events columns for consistency
-- data_json → payload_json, created_at → ts
-- Date: 2026-02-08
-- Apply AFTER mcas_schema.sql and all prior migrations

SELECT pg_advisory_lock(202602080);

-- Rename data_json → payload_json (idempotent)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'run_events' AND column_name = 'data_json'
    ) THEN
        ALTER TABLE run_events RENAME COLUMN data_json TO payload_json;
        RAISE NOTICE 'Renamed run_events.data_json → payload_json';
    ELSE
        RAISE NOTICE 'Column already named payload_json (or does not exist)';
    END IF;
END $$;

-- Rename created_at → ts (idempotent)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'run_events' AND column_name = 'created_at'
    ) THEN
        ALTER TABLE run_events RENAME COLUMN created_at TO ts;
        RAISE NOTICE 'Renamed run_events.created_at → ts';
    ELSE
        RAISE NOTICE 'Column already named ts (or does not exist)';
    END IF;
END $$;

-- Recreate index with new column name
DROP INDEX IF EXISTS idx_run_events_run;
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, ts DESC);

-- Verify
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'run_events' AND column_name = 'payload_json'
    ) THEN
        RAISE EXCEPTION 'Migration failed: payload_json column not found';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'run_events' AND column_name = 'ts'
    ) THEN
        RAISE EXCEPTION 'Migration failed: ts column not found';
    END IF;
    RAISE NOTICE 'Migration verified: run_events columns renamed';
END $$;

SELECT pg_advisory_unlock(202602080);
