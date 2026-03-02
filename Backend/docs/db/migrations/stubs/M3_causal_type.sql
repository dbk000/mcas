-- M3: Add causal_type column to version_event
-- Wave: 2.11 (Timeline & Biography)
-- Lock ID: 2026030303
-- Dependencies: 20260302_add_version_event.sql
-- Purpose: Distinguish blocking (CHILD_OF) vs informational (FOLLOWS_FROM) causal links
-- Rollback: ALTER TABLE meta.version_event DROP COLUMN IF EXISTS causal_type;

SELECT pg_advisory_lock(2026030303);

ALTER TABLE meta.version_event ADD COLUMN IF NOT EXISTS causal_type TEXT
    DEFAULT 'blocking'
    CHECK (causal_type IN ('blocking', 'informational'));

COMMENT ON COLUMN meta.version_event.causal_type IS
    'blocking = this event was directly caused by parent (on critical path). informational = async/advisory link (conversation_note, manual_observation).';

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M3_causal_type', 'wave2_causal_type', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030303);
