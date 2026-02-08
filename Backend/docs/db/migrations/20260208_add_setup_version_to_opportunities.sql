-- Migration: Add setup_version and batch_id to opportunities table
-- Purpose: Track which code version generated each opportunity (append-only versioning)
-- Date: 2026-02-08
-- Canon: §1.7 (Champion/Challenger), §1.12 (Canon Patch Process)

SELECT pg_advisory_lock(20260208001);

DO $$
BEGIN
    -- Track migration
    INSERT INTO schema_versions (filename, applied_at)
    VALUES ('20260208_add_setup_version_to_opportunities.sql', NOW())
    ON CONFLICT (filename) DO NOTHING;

    -- Add setup_version column: tracks which code version generated this opportunity
    -- e.g. 'S1_v2.1', 'S1_v2.2', 'S2_v1.4'
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'opportunities' AND column_name = 'setup_version'
    ) THEN
        ALTER TABLE opportunities ADD COLUMN setup_version TEXT;
    END IF;

    -- Add batch_id column: groups opportunities from the same backfill run
    -- e.g. 'backfill_20260208_abc123'
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'opportunities' AND column_name = 'batch_id'
    ) THEN
        ALTER TABLE opportunities ADD COLUMN batch_id TEXT;
    END IF;

    -- Backfill existing rows with current versions
    UPDATE opportunities SET setup_version = 'S1_v2.1' WHERE setup_id = 'S1' AND setup_version IS NULL;
    UPDATE opportunities SET setup_version = 'S2_v1.4' WHERE setup_id = 'S2' AND setup_version IS NULL;
    UPDATE opportunities SET setup_version = 'U1_v1.0' WHERE setup_id = 'U1' AND setup_version IS NULL;

    -- Index for filtering by setup_version (common query path)
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_opportunities_setup_version'
    ) THEN
        CREATE INDEX idx_opportunities_setup_version
        ON opportunities (setup_version)
        WHERE status = 'expired';
    END IF;

    -- Index for filtering by batch_id
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_opportunities_batch_id'
    ) THEN
        CREATE INDEX idx_opportunities_batch_id
        ON opportunities (batch_id)
        WHERE batch_id IS NOT NULL;
    END IF;
END
$$;

SELECT pg_advisory_unlock(20260208001);
