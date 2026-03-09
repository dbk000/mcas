-- Migration: Add config_json to simulation_results
-- Stores the complete execution config used for each simulation run
-- for reproducibility and audit trail.
BEGIN;
SELECT pg_advisory_lock(20260309);

ALTER TABLE simulation_results
    ADD COLUMN IF NOT EXISTS config_json JSONB DEFAULT NULL;

COMMENT ON COLUMN simulation_results.config_json IS
    'Complete execution config snapshot (slippage, fees, fills, exit config) at time of simulation. Immutable after write.';

SELECT pg_advisory_unlock(20260309);
COMMIT;
