-- M7: Create meta.dataset table
-- Wave: 5.5 (Interoperability)
-- Lock ID: 2026030306
-- Dependencies: meta.node, meta.port
-- Purpose: OpenLineage-compatible dataset concept. Namespace+name identity, links to node+port.
-- Rollback: DROP TABLE IF EXISTS meta.dataset;

SELECT pg_advisory_lock(2026030306);

CREATE TABLE IF NOT EXISTS meta.dataset (
    dataset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    node_id UUID REFERENCES meta.node(node_id),
    port_id UUID,  -- references meta.port(port_id) if applicable
    schema_spec JSONB DEFAULT '{}',
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(namespace, name)
);

COMMENT ON TABLE meta.dataset IS
    'OpenLineage-compatible dataset identity. Maps to node+port for lineage tracking.';

CREATE INDEX IF NOT EXISTS idx_dataset_node ON meta.dataset(node_id);
CREATE INDEX IF NOT EXISTS idx_dataset_namespace ON meta.dataset(namespace);

INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('M7_dataset_table', 'wave5_dataset', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030306);
