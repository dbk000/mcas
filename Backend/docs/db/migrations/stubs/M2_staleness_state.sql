-- M2: Add staleness_state column to node_version
-- Wave: 3.1 (Dependency Hardening)
-- Lock ID: 2026030302
-- Dependencies: 20260301_add_meta_introspection_schema.sql
-- Purpose: Three-state staleness (clean / potentially_stale / confirmed_stale)
-- Rollback: ALTER TABLE meta.node_version DROP COLUMN IF EXISTS staleness_state;

SELECT pg_advisory_lock(2026030302);

ALTER TABLE meta.node_version ADD COLUMN IF NOT EXISTS staleness_state TEXT
    DEFAULT 'clean'
    CHECK (staleness_state IN ('clean', 'potentially_stale', 'confirmed_stale'));

COMMENT ON COLUMN meta.node_version.staleness_state IS
    'Three-state staleness: clean (verified current), potentially_stale (upstream changed, not yet revalidated), confirmed_stale (revalidation showed different output).';

-- Partial index for non-clean lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_meta_nv_staleness
    ON meta.node_version(node_id, staleness_state) WHERE staleness_state != 'clean';

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M2_staleness_state', 'wave3_staleness', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030302);
