-- Migration: Add governance lineage columns to governed tables.
-- Every governed write carries runtime_graph_seal, process_id, bootstrap_epoch.
-- These columns enable peer shunning and causality verification.
--
-- Run on dev first, then production. Uses pg_advisory_lock + IF NOT EXISTS.

BEGIN;
SELECT pg_advisory_lock(20260314);

-- opportunities
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS runtime_graph_seal VARCHAR(64),
    ADD COLUMN IF NOT EXISTS process_id VARCHAR(16),
    ADD COLUMN IF NOT EXISTS bootstrap_epoch DOUBLE PRECISION;

-- simulation_results
ALTER TABLE simulation_results
    ADD COLUMN IF NOT EXISTS runtime_graph_seal VARCHAR(64),
    ADD COLUMN IF NOT EXISTS process_id VARCHAR(16),
    ADD COLUMN IF NOT EXISTS bootstrap_epoch DOUBLE PRECISION;

-- paper_positions
ALTER TABLE paper_positions
    ADD COLUMN IF NOT EXISTS runtime_graph_seal VARCHAR(64),
    ADD COLUMN IF NOT EXISTS process_id VARCHAR(16),
    ADD COLUMN IF NOT EXISTS bootstrap_epoch DOUBLE PRECISION;

-- d2_cluster_events (D2 paper trading)
ALTER TABLE d2_cluster_events
    ADD COLUMN IF NOT EXISTS runtime_graph_seal VARCHAR(64),
    ADD COLUMN IF NOT EXISTS process_id VARCHAR(16),
    ADD COLUMN IF NOT EXISTS bootstrap_epoch DOUBLE PRECISION;

SELECT pg_advisory_unlock(20260314);
COMMIT;
