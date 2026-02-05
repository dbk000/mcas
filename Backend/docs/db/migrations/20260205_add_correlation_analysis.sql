-- =============================================================================
-- MIGRATION: add_correlation_analysis
-- =============================================================================
-- File: Backend/docs/db/migrations/20260205_add_correlation_analysis.sql
-- Correlation Analysis Module - Database Schema
--
-- DO NOT EXECUTE FROM APPLICATION CODE.
-- Apply via: psql -U trading -d trading -f <this_file>
-- =============================================================================
--
-- IMPORTANT NOTES:
--
-- 1. This migration adds a table for async correlation analysis jobs.
--    Jobs are enqueued via API, processed by workers, results polled.
--
-- 2. Uses UUID[] for run_ids to match existing patterns in the codebase.
--
-- 3. config_hash enables result caching - identical analysis configs
--    return cached results instead of recomputing.
--
-- =============================================================================

-- -----------------------------------------------------------------------------
-- ADVISORY LOCK: Prevent concurrent migration runs
-- -----------------------------------------------------------------------------
SELECT pg_advisory_lock(20260205);

-- PREREQUISITE: Ensure pgcrypto extension is available
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- =============================================================================
-- CORRELATION ANALYSIS: Job Queue and Results Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS correlation_analysis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Job status
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')),

    -- Input specification
    run_ids UUID[] NOT NULL,              -- Array of run IDs to analyze
    setup_id TEXT,                        -- NULL = all setups

    -- Versioned caching: SHA256 of sorted canonical config JSON
    config_hash TEXT NOT NULL,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,

    -- Error tracking
    error_text TEXT,

    -- Summary (populated on completion)
    total_trades INT,
    winning_trades INT,
    losing_trades INT,

    -- Results (JSONB for flexibility)
    feature_analyses JSONB,               -- {feature: {winner_mean, loser_mean, cohens_d, bins}}
    top_predictors JSONB,                 -- [{feature, importance, direction}, ...]
    exit_reason_breakdown JSONB,          -- {exit_reason: {count, avg_r, avg_features}}
    insights JSONB,                       -- Generated text insights

    -- Analysis parameters (frozen at job creation)
    config_json JSONB                     -- {min_trades, max_trades, ...}
);

-- Index for job claiming: status + created_at for FIFO processing
CREATE INDEX IF NOT EXISTS idx_correlation_analysis_status
    ON correlation_analysis(status, created_at);

-- Index for run lookup: find analyses that included a specific run
CREATE INDEX IF NOT EXISTS idx_correlation_analysis_runs
    ON correlation_analysis USING GIN(run_ids);

-- Index for cache lookup: find existing analysis by config hash
CREATE INDEX IF NOT EXISTS idx_correlation_analysis_hash
    ON correlation_analysis(config_hash);


-- =============================================================================
-- SCHEMA VERIFICATION
-- =============================================================================

DO $$
DECLARE
    missing_tables TEXT[];
    expected_tables TEXT[] := ARRAY['correlation_analysis'];
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY expected_tables
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = tbl
        ) THEN
            missing_tables := array_append(missing_tables, tbl);
        END IF;
    END LOOP;

    IF array_length(missing_tables, 1) > 0 THEN
        RAISE EXCEPTION 'Correlation Analysis Migration verification FAILED. Missing tables: %', missing_tables;
    END IF;

    RAISE NOTICE 'Correlation Analysis Migration verification PASSED. Table present.';
END $$;


-- =============================================================================
-- RELEASE ADVISORY LOCK
-- =============================================================================
SELECT pg_advisory_unlock(20260205);


-- =============================================================================
-- END OF MIGRATION
-- =============================================================================
-- To apply: psql -U trading -d trading -f Backend/docs/db/migrations/20260205_add_correlation_analysis.sql
--
-- POST-MIGRATION CHECKLIST:
-- [ ] Verify "Correlation Analysis Migration verification PASSED" message
-- [ ] Rebuild worker container: docker-compose up -d --build worker
-- =============================================================================
