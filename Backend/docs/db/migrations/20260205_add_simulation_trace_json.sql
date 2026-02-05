-- =============================================================================
-- MIGRATION: add_simulation_trace_json
-- =============================================================================
-- Adds trace_json column to simulation_results table to enable correlation
-- analysis on simulated trades.
--
-- Apply via: psql -U trading -d trading -f <this_file>
-- =============================================================================

SELECT pg_advisory_lock(202602051);

-- Add trace_json column to simulation_results
ALTER TABLE simulation_results
ADD COLUMN IF NOT EXISTS trace_json TEXT;

COMMENT ON COLUMN simulation_results.trace_json IS
    'Audit trace from original opportunity signal (indicator values, state, scores)';

SELECT pg_advisory_unlock(202602051);

-- =============================================================================
-- END OF MIGRATION
-- =============================================================================
