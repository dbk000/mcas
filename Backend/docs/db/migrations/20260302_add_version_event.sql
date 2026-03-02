-- Migration: Add version_event table for biography system + causal event chaining
-- Also adds system_epoch table for knowledge boundary marking
-- Advisory lock: 2026030201

BEGIN;
SELECT pg_advisory_lock(2026030201);

-- version_event: every meaningful thing that happened to a version
CREATE TABLE IF NOT EXISTS meta.version_event (
    event_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id        UUID NOT NULL REFERENCES meta.node_version(version_id),
    event_type        TEXT NOT NULL,
    event_detail      JSONB NOT NULL DEFAULT '{}',
    actor             TEXT NOT NULL,
    caused_by_event_id UUID REFERENCES meta.version_event(event_id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ve_version ON meta.version_event(version_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ve_type ON meta.version_event(event_type);
CREATE INDEX IF NOT EXISTS idx_ve_actor ON meta.version_event(actor);
CREATE INDEX IF NOT EXISTS idx_ve_caused_by ON meta.version_event(caused_by_event_id);

-- system_epoch: knowledge boundary markers
CREATE TABLE IF NOT EXISTS meta.system_epoch (
    epoch_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    epoch_date  TIMESTAMPTZ NOT NULL,
    description TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Insert the initial epoch marker
INSERT INTO meta.system_epoch (epoch_date, description)
VALUES ('2026-03-01T00:00:00Z', 'Records before 2026-03-01 are reconstructed from repo mining. Pre-epoch queries should be treated as potentially incomplete.')
ON CONFLICT DO NOTHING;

-- Track migration
INSERT INTO schema_versions (filename)
VALUES ('20260302_add_version_event.sql')
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030201);
COMMIT;
