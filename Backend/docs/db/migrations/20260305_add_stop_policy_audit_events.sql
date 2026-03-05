-- Stop policy audit ledger (append-only)
-- Date: 2026-03-05

BEGIN;

CREATE TABLE IF NOT EXISTS meta.stop_policy_audit_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    node_id UUID NOT NULL REFERENCES meta.node(node_id),
    node_version_id UUID NOT NULL REFERENCES meta.node_version(version_id),
    setup_id TEXT,
    field_path TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB,
    source TEXT NOT NULL CHECK (source IN ('default_registry', 'user_override', 'migration_fill')),
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_stop_policy_audit_node_ts
ON meta.stop_policy_audit_events(node_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_stop_policy_audit_setup_ts
ON meta.stop_policy_audit_events(setup_id, ts DESC);

COMMIT;

