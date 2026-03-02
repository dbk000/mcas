-- M5: (DEFERRED) Add trigger to materialize node_current_state
-- Wave: 2.9 (Timeline & Biography) — only if profiling shows VIEW is too slow
-- Lock ID: 2026030305
-- Dependencies: M4 (node_current_state view must exist first)
-- Purpose: If the VIEW from M4 is too slow, this converts it to a materialized table with trigger
-- Rollback: DROP TRIGGER IF EXISTS trg_update_node_current_state ON meta.version_event;
--           DROP FUNCTION IF EXISTS meta.update_node_current_state();
--           DROP TABLE IF EXISTS meta.node_current_state_mat;

-- This migration is DEFERRED. Only apply after profiling shows M4 VIEW is a bottleneck.
-- Steps:
-- 1. Profile the VIEW: EXPLAIN ANALYZE SELECT * FROM meta.node_current_state;
-- 2. If >5ms at 100+ nodes, proceed with this migration
-- 3. Test on dev first: insert a version_event, confirm mat table updates correctly
-- 4. Only then apply to production

-- STUB — implementation deferred until profiling justifies it.
-- See plan §3.3 "Design decision: node_current_state" for rationale.
