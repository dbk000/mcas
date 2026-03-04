-- Speed latest-outcome lookups by opportunity id (used by include_outcome and settlement sync).

SELECT pg_advisory_lock(2026030403);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sim_results_opp_created_desc
    ON simulation_results(opportunity_id, created_at DESC);

INSERT INTO public.schema_versions (filename)
VALUES ('20260304_add_simulation_results_opp_created_idx.sql')
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(2026030403);
