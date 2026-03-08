-- Migration: D2 cluster events table for paper trader
-- Date: 2026-03-08
-- Advisory lock for idempotency

DO $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('d2_cluster_events'));
END $$;

CREATE TABLE IF NOT EXISTS d2_cluster_events (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL DEFAULT 'pump',
    exchange TEXT,
    alert_count INT NOT NULL DEFAULT 0,
    cluster_start_ts TIMESTAMPTZ NOT NULL,
    cluster_end_ts TIMESTAMPTZ,
    first_price FLOAT,
    max_pct_change FLOAT,
    vol_24h_usd FLOAT,
    funding_rate FLOAT,
    pre_pump_price FLOAT,
    status TEXT NOT NULL DEFAULT 'NEW',
    gate_reason TEXT,
    source_post_ids BIGINT[],
    metadata_json JSONB,
    created_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_d2ce_symbol_status
    ON d2_cluster_events(symbol, status);
CREATE INDEX IF NOT EXISTS idx_d2ce_created
    ON d2_cluster_events(created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_d2ce_cluster_start
    ON d2_cluster_events(cluster_start_ts DESC);

-- Track migration
INSERT INTO schema_versions (filename, checksum)
VALUES ('20260308_d2_cluster_events.sql', 'd2_cluster_events')
ON CONFLICT (filename) DO NOTHING;
