-- Config version singleton + extend audit source CHECK
-- Date: 2026-03-06
-- Purpose: Add meta.config_version polling target for LiveConfig,
--          extend stop_policy_audit_events source CHECK for new sources.

BEGIN;

SELECT pg_advisory_lock(20260306);

-- 1) Singleton config_version table for cheap polling
CREATE TABLE IF NOT EXISTS meta.config_version (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    version BIGINT NOT NULL DEFAULT 0,
    updated_ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO meta.config_version (id, version) VALUES (1, 0) ON CONFLICT DO NOTHING;

-- 2) Extend source CHECK on stop_policy_audit_events to allow new sources.
--    The existing CHECK only allows: default_registry, user_override, migration_fill.
--    We need: startup_sync, hot_reload, pipeline_ui.
--    Postgres doesn't support ALTER CONSTRAINT, so drop+recreate.
DO $$
BEGIN
    -- Drop existing CHECK constraint on source column (name varies)
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints tc
        JOIN information_schema.check_constraints cc ON cc.constraint_name = tc.constraint_name
        WHERE tc.table_schema = 'meta'
          AND tc.table_name = 'stop_policy_audit_events'
          AND cc.check_clause LIKE '%source%'
    ) THEN
        EXECUTE (
            SELECT 'ALTER TABLE meta.stop_policy_audit_events DROP CONSTRAINT ' || tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.check_constraints cc ON cc.constraint_name = tc.constraint_name
            WHERE tc.table_schema = 'meta'
              AND tc.table_name = 'stop_policy_audit_events'
              AND cc.check_clause LIKE '%source%'
            LIMIT 1
        );
    END IF;
END $$;

ALTER TABLE meta.stop_policy_audit_events
    ADD CONSTRAINT stop_policy_audit_source_check
    CHECK (source IN (
        'default_registry', 'user_override', 'migration_fill',
        'startup_sync', 'hot_reload', 'pipeline_ui'
    ));

SELECT pg_advisory_unlock(20260306);

COMMIT;
