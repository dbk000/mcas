-- Wave 5.5: meta.dataset table
-- OpenLineage-compatible dataset identity (namespace + name)
-- Links to node + port for data lineage tracking
-- Migration ID: 2026030205

DO $$ BEGIN
    PERFORM pg_advisory_lock(2026030205);
END $$;

-- M7: Dataset table
CREATE TABLE IF NOT EXISTS meta.dataset (
    dataset_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace      TEXT NOT NULL DEFAULT 'mcas.pipeline',
    name           TEXT NOT NULL,
    description    TEXT,
    -- Link to pipeline node (optional — external datasets may not map to a node)
    node_id        UUID REFERENCES meta.node(node_id),
    -- Link to specific port (optional — for fine-grained lineage)
    port_id        UUID REFERENCES meta.port(port_id),
    -- OpenLineage facets stored as JSONB
    facets         JSONB DEFAULT '{}',
    -- Dataset versioning
    current_version TEXT,
    -- Metadata
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(namespace, name)
);

-- Index for lookups by node
CREATE INDEX IF NOT EXISTS idx_meta_dataset_node ON meta.dataset(node_id) WHERE node_id IS NOT NULL;

-- Record migration
INSERT INTO meta.schema_versions (version_id, description)
VALUES ('20260302_wave5_dataset', 'Wave 5.5: meta.dataset table for OpenLineage interop')
ON CONFLICT (version_id) DO NOTHING;

DO $$ BEGIN
    PERFORM pg_advisory_unlock(2026030205);
END $$;
