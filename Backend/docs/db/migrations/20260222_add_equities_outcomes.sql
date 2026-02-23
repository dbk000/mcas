-- Migration: Add append-only realized outcomes for equities opportunities
-- Date: 2026-02-22

SELECT pg_advisory_lock(2026022203);

CREATE TABLE IF NOT EXISTS equities_opportunity_outcomes (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    opportunity_id UUID NOT NULL REFERENCES equities_opportunities(id) ON DELETE CASCADE,
    prediction_id UUID NOT NULL REFERENCES equities_predictions(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    direction TEXT NOT NULL,
    regime_label TEXT,
    available_at_ts TIMESTAMPTZ NOT NULL,
    settle_bar_ts TIMESTAMPTZ NOT NULL,
    expected_return DOUBLE PRECISION,
    predicted_prob_up DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    edge_score DOUBLE PRECISION,
    realized_return DOUBLE PRECISION NOT NULL,
    directional_realized_return DOUBLE PRECISION NOT NULL,
    hit BOOLEAN NOT NULL,
    brier_loss DOUBLE PRECISION NOT NULL,
    log_loss DOUBLE PRECISION NOT NULL,
    outcome_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    deterministic_hash TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_equities_outcomes_opportunity
    ON equities_opportunity_outcomes (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_equities_outcomes_symbol_horizon
    ON equities_opportunity_outcomes (symbol, horizon, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_equities_outcomes_perf
    ON equities_opportunity_outcomes (horizon, hit, directional_realized_return DESC);

-- Ensure append-only guard exists.
CREATE OR REPLACE FUNCTION equities_append_only_guard()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Append-only table: %', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_equities_opportunity_outcomes_append_only ON equities_opportunity_outcomes;
CREATE TRIGGER trg_equities_opportunity_outcomes_append_only
BEFORE UPDATE OR DELETE ON equities_opportunity_outcomes
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

INSERT INTO schema_versions (filename, checksum)
VALUES (
    '20260222_add_equities_outcomes.sql',
    'equities_opportunity_outcomes_append_only'
)
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(2026022203);
