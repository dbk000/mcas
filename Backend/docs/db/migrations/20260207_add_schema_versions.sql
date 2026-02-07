-- Migration: Add schema_versions tracking table
-- Canon §1.10: Schema governance — track applied migrations
-- Date: 2026-02-07
-- Apply AFTER mcas_schema.sql and all prior migrations

SELECT pg_advisory_lock(202602071);

CREATE TABLE IF NOT EXISTS schema_versions (
    id          SERIAL PRIMARY KEY,
    filename    TEXT NOT NULL UNIQUE,
    checksum    TEXT,                          -- SHA256 of migration file content
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by  TEXT DEFAULT current_user
);

COMMENT ON TABLE schema_versions IS
    'Canon §1.10: Tracks applied migrations to prevent drift and ensure repeatability. '
    'Each migration file should INSERT into this table after successful application.';

-- Seed with all existing migrations (retroactive)
INSERT INTO schema_versions (filename, applied_at) VALUES
    ('mcas_schema.sql', NOW()),
    ('20260203_000000_add_cc_tables.sql', NOW()),
    ('20260205_add_correlation_analysis.sql', NOW()),
    ('20260205_add_ci_insights.sql', NOW()),
    ('20260205_add_simulation_trace_json.sql', NOW()),
    ('20260207_add_signal_events.sql', NOW()),
    ('20260207_add_schema_versions.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

-- Verify
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'schema_versions') THEN
        RAISE EXCEPTION 'schema_versions table was not created';
    END IF;
    RAISE NOTICE 'schema_versions migration applied successfully. % migrations tracked.',
        (SELECT count(*) FROM schema_versions);
END $$;

SELECT pg_advisory_unlock(202602071);
