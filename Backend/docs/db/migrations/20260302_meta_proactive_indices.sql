-- Migration: Add proactive indices for pipeline mining features
-- Lock ID: 2026030201
-- Dependencies: 20260301_add_meta_introspection_schema.sql, 20260302_add_version_event.sql
-- Rollback: DROP INDEX statements at bottom

-- Advisory lock for idempotency
SELECT pg_advisory_lock(2026030201);

-- Index 1: Fast point-in-time version reconstruction
-- Used by: point-in-time queries ("show me the graph state at time T")
-- Query: SELECT MAX(version_id) WHERE node_id = X AND created_at <= T
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_meta_nv_node_created
    ON meta.node_version(node_id, created_at DESC);

-- Index 2: Event type + time range queries
-- Used by: event search, recent activity feed, event type filtering
-- Query: SELECT * FROM version_event WHERE event_type = X AND created_at BETWEEN A AND B
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ve_type_created
    ON meta.version_event(event_type, created_at DESC);

-- Index 3: Staleness state queries (partial index — only non-clean rows)
-- Used by: staleness badge computation, "show me all stale nodes"
-- Deferred until staleness_state column exists (migration M2)
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_meta_nv_staleness
--     ON meta.node_version(node_id, staleness_state) WHERE staleness_state != 'clean';

-- Index 4: Output hash lookups (partial index — only rows with hash)
-- Used by: DICE early cutoff ("has this output changed?")
-- Deferred until output_hash column exists (migration M1)
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_meta_nv_output_hash
--     ON meta.node_version(node_id, output_hash) WHERE output_hash IS NOT NULL;

-- Index 5: Causal chain traversal (downstream children lookup)
-- Used by: recursive CTE in get_causal_chain
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ve_caused_by
    ON meta.version_event(caused_by_event_id)
    WHERE caused_by_event_id IS NOT NULL;

-- Record migration
INSERT INTO schema_versions (filename, checksum, applied_at)
VALUES ('20260302_meta_proactive_indices', 'wave0_indices', NOW())
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030201);

-- === ROLLBACK ===
-- DROP INDEX CONCURRENTLY IF EXISTS meta.idx_meta_nv_node_created;
-- DROP INDEX CONCURRENTLY IF EXISTS meta.idx_ve_type_created;
-- DROP INDEX CONCURRENTLY IF EXISTS meta.idx_ve_caused_by;
-- DELETE FROM schema_versions WHERE version = '20260302_meta_proactive_indices';
