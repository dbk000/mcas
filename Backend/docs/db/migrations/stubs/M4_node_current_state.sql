-- M4: Create node_current_state VIEW
-- Wave: 2.9 (Timeline & Biography)
-- Lock ID: 2026030304
-- Dependencies: meta.node, meta.node_version, meta.version_event
-- Purpose: O(1) lookup of current state per node (latest version, last event, status)
-- Design decision: VIEW first (always consistent, ~1ms for 31 nodes). Upgrade to
--   materialized table + trigger only if profiling shows the view is too slow.
-- Rollback: DROP VIEW IF EXISTS meta.node_current_state;

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
    'Materialized view of current state per node. Computed from node_version. Upgrade to table+trigger if perf requires.';

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M4_node_current_state', 'wave2_current_state', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030304);
