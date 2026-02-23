-- Migration: Add selection policies + calibration alerts for equities predictor
-- Date: 2026-02-22

SELECT pg_advisory_lock(2026022204);

CREATE TABLE IF NOT EXISTS equities_selection_policies (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    min_confidence DOUBLE PRECISION NOT NULL,
    min_edge_score DOUBLE PRECISION NOT NULL,
    directional_prob_floor DOUBLE PRECISION NOT NULL,
    expected_return_floor DOUBLE PRECISION NOT NULL,
    min_samples INT NOT NULL,
    transaction_cost_bps DOUBLE PRECISION NOT NULL DEFAULT 6.0,
    objective_score DOUBLE PRECISION NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT,
    deterministic_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_equities_selection_policies_lookup
    ON equities_selection_policies (timeframe, horizon, created_at DESC);

CREATE TABLE IF NOT EXISTS equities_calibration_alerts (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    severity TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    deterministic_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_equities_calibration_alerts_lookup
    ON equities_calibration_alerts (timeframe, horizon, created_at DESC);

-- Ensure append-only guard exists.
CREATE OR REPLACE FUNCTION equities_append_only_guard()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Append-only table: %', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_equities_selection_policies_append_only ON equities_selection_policies;
CREATE TRIGGER trg_equities_selection_policies_append_only
BEFORE UPDATE OR DELETE ON equities_selection_policies
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

DROP TRIGGER IF EXISTS trg_equities_calibration_alerts_append_only ON equities_calibration_alerts;
CREATE TRIGGER trg_equities_calibration_alerts_append_only
BEFORE UPDATE OR DELETE ON equities_calibration_alerts
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

INSERT INTO schema_versions (filename, checksum)
VALUES (
    '20260222_add_equities_policy_tables.sql',
    'equities_selection_policies+equities_calibration_alerts_append_only'
)
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(2026022204);
