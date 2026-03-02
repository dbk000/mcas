-- Wave 2: Timeline & Biography migrations
-- Combines M3 (causal_type) and M4 (node_current_state VIEW)
-- Applied: 2026-03-02
--
-- M3: causal_type column on version_event
-- M4: node_current_state VIEW
--
-- Rollback:
--   DROP VIEW IF EXISTS meta.node_current_state;
--   ALTER TABLE meta.version_event DROP COLUMN IF EXISTS causal_type;

-- === M3: causal_type ===
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

-- === M4: node_current_state VIEW ===
SELECT pg_advisory_lock(2026030304);

CREATE OR REPLACE VIEW meta.node_current_state AS
SELECT DISTINCT ON (n.node_id)
    n.node_id,
    nv.version_id AS current_version_id,
    nv.status AS current_status,
    nv.created_at AS version_created_at,
    (
        SELECT ve.event_id
        FROM meta.version_event ve
        WHERE ve.version_id = nv.version_id
        ORDER BY ve.created_at DESC
        LIMIT 1
    ) AS last_event_id,
    NOW() AS computed_at
FROM meta.node n
LEFT JOIN meta.node_version nv ON nv.node_id = n.node_id
ORDER BY n.node_id, nv.created_at DESC;

COMMENT ON VIEW meta.node_current_state IS
    'Current state per node. Computed from node_version. Upgrade to table+trigger if perf requires.';

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M4_node_current_state', 'wave2_current_state', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030304);
