-- Migration: Add node_file_mapping table for git hook auto-capture
-- Maps file patterns to nodes so commits can be linked to affected nodes
-- Advisory lock: 2026030202

BEGIN;
SELECT pg_advisory_lock(2026030202);

CREATE TABLE IF NOT EXISTS meta.node_file_mapping (
    node_id      UUID NOT NULL REFERENCES meta.node(node_id),
    file_pattern TEXT NOT NULL,
    mapping_type TEXT NOT NULL,
    PRIMARY KEY (node_id, file_pattern)
);

COMMENT ON TABLE meta.node_file_mapping IS 'Maps file glob patterns to nodes for auto-capture. mapping_type: primary, config, test, dependency';

-- Track migration
INSERT INTO schema_versions (filename)
VALUES ('20260302_add_node_file_mapping.sql')
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030202);
COMMIT;
