-- M1: Add output_hash column to node_version
-- Wave: 3.2 (Dependency Hardening)
-- Lock ID: 2026030301
-- Dependencies: 20260301_add_meta_introspection_schema.sql
-- Purpose: DICE early cutoff — if recomputed output hash matches previous, skip downstream cascade
-- Rollback: ALTER TABLE meta.node_version DROP COLUMN IF EXISTS output_hash;

SELECT pg_advisory_lock(2026030301);

ALTER TABLE meta.node_version ADD COLUMN IF NOT EXISTS output_hash TEXT;

COMMENT ON COLUMN meta.node_version.output_hash IS
    'SHA256 hash of node output. Used for DICE early cutoff — same hash means skip downstream cascade.';

-- Partial index for hash lookups (only rows that have a hash)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_meta_nv_output_hash
    ON meta.node_version(node_id, output_hash) WHERE output_hash IS NOT NULL;

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M1_output_hash', 'wave3_output_hash', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030301);
