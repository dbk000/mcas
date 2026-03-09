-- Migration: Add caused_by columns to meta.node_version
-- Links every node version change to the commit, experiment, or event that motivated it.
-- Advisory lock: 2026030902

BEGIN;
SELECT pg_advisory_lock(2026030902);

-- caused_by: stores a commit hash, experiment ID, or event reference
ALTER TABLE meta.node_version
    ADD COLUMN IF NOT EXISTS caused_by TEXT DEFAULT NULL;

COMMENT ON COLUMN meta.node_version.caused_by IS
    'Reference to the commit hash, experiment ID, or event that motivated this version. '
    'Format depends on caused_by_type.';

-- caused_by_type: classifies the reference
ALTER TABLE meta.node_version
    ADD COLUMN IF NOT EXISTS caused_by_type TEXT DEFAULT NULL;

-- Add CHECK constraint only if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_nv_caused_by_type'
          AND conrelid = 'meta.node_version'::regclass
    ) THEN
        ALTER TABLE meta.node_version
            ADD CONSTRAINT chk_nv_caused_by_type
            CHECK (caused_by_type IS NULL OR caused_by_type IN (
                'commit', 'experiment', 'manual', 'config_change'
            ));
    END IF;
END;
$$;

COMMENT ON COLUMN meta.node_version.caused_by_type IS
    'Classification of the caused_by reference: commit, experiment, manual, config_change.';

-- Index for querying versions by their cause
CREATE INDEX IF NOT EXISTS idx_meta_nv_caused_by
    ON meta.node_version(caused_by)
    WHERE caused_by IS NOT NULL;

-- Track migration
INSERT INTO schema_versions (filename)
VALUES ('20260309_add_caused_by_to_node_versions.sql')
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030902);
COMMIT;
