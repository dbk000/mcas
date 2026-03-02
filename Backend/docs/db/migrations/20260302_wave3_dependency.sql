-- Wave 3: Dependency Hardening migrations
-- M1: output_hash column on node_version
-- M2: staleness_state column on node_version
-- M7: health_status column on node_version
-- Applied: 2026-03-02
--
-- Rollback:
--   ALTER TABLE meta.node_version DROP COLUMN IF EXISTS output_hash;
--   ALTER TABLE meta.node_version DROP COLUMN IF EXISTS staleness_state;
--   ALTER TABLE meta.node_version DROP COLUMN IF EXISTS health_status;
--   DROP INDEX IF EXISTS meta.idx_meta_nv_staleness;
--   DROP INDEX IF EXISTS meta.idx_meta_nv_output_hash;

-- === M1: output_hash ===
SELECT pg_advisory_lock(2026030201);

ALTER TABLE meta.node_version ADD COLUMN IF NOT EXISTS output_hash TEXT;

COMMENT ON COLUMN meta.node_version.output_hash IS
    'Hash of the node output (backtest results, metrics). Used for DICE early cutoff — if output unchanged, skip downstream staleness propagation.';

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M1_output_hash', 'wave3_output_hash', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030201);

-- === M2: staleness_state ===
SELECT pg_advisory_lock(2026030202);

ALTER TABLE meta.node_version ADD COLUMN IF NOT EXISTS staleness_state TEXT
    DEFAULT 'clean'
    CHECK (staleness_state IN ('clean', 'potentially_stale', 'confirmed_stale'));

COMMENT ON COLUMN meta.node_version.staleness_state IS
    'Three-state staleness (Buck2 DICE pattern). clean = verified current. potentially_stale = upstream changed but output may be same. confirmed_stale = upstream output actually differs.';

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M2_staleness_state', 'wave3_staleness_state', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030202);

-- === M7: health_status ===
SELECT pg_advisory_lock(2026030207);

ALTER TABLE meta.node_version ADD COLUMN IF NOT EXISTS health_status TEXT
    DEFAULT 'unknown'
    CHECK (health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown'));

COMMENT ON COLUMN meta.node_version.health_status IS
    'Operational health separate from version sync status (Argo CD pattern). healthy = working as expected. degraded = partial issues. unhealthy = broken. unknown = not yet evaluated.';

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M7_health_status', 'wave3_health_status', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030207);

-- === Indices (proactive, from data architecture review) ===
-- These use IF NOT EXISTS via DO blocks since CREATE INDEX CONCURRENTLY
-- cannot run inside a transaction block in some contexts

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_meta_nv_staleness') THEN
        CREATE INDEX idx_meta_nv_staleness ON meta.node_version(node_id, staleness_state)
            WHERE staleness_state != 'clean';
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_meta_nv_output_hash') THEN
        CREATE INDEX idx_meta_nv_output_hash ON meta.node_version(node_id, output_hash)
            WHERE output_hash IS NOT NULL;
    END IF;
END$$;
