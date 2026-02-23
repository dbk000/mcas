-- Migration: Add equities forward predictor tables
-- Date: 2026-02-22

SELECT pg_advisory_lock(2026022202);

CREATE TABLE IF NOT EXISTS equities_symbol_registry (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    sector TEXT,
    is_etf BOOLEAN NOT NULL DEFAULT FALSE,
    cik TEXT,
    aliases TEXT[] NOT NULL DEFAULT '{}'::text[],
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS equities_source_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    content_hash TEXT,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_equities_source_cache_expiry
    ON equities_source_cache (expires_at);

CREATE TABLE IF NOT EXISTS equities_bar_meta (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    session_state TEXT NOT NULL CHECK (session_state IN ('RTH', 'ETH', 'CLOSED')),
    next_open_ts TIMESTAMPTZ,
    last_close_ts TIMESTAMPTZ,
    open_gap_pct DOUBLE PRECISION,
    overnight_return DOUBLE PRECISION,
    PRIMARY KEY (symbol, timeframe, bar_start_ts, source)
);

CREATE INDEX IF NOT EXISTS idx_equities_bar_meta_lookup
    ON equities_bar_meta (symbol, timeframe, bar_start_ts DESC);

CREATE TABLE IF NOT EXISTS equities_events (
    id UUID PRIMARY KEY,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    publisher TEXT,
    title TEXT NOT NULL,
    url TEXT,
    published_ts TIMESTAMPTZ NOT NULL,
    available_at_ts TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    session_relation TEXT NOT NULL CHECK (
        session_relation IN ('published_preopen', 'intraday', 'postclose', 'weekend', 'unknown')
    ),
    sentiment_score DOUBLE PRECISION,
    anti_bot_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    dedupe_hash TEXT NOT NULL,
    simhash BIGINT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(source, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_equities_events_symbol_published
    ON equities_events (symbol, published_ts DESC);
CREATE INDEX IF NOT EXISTS idx_equities_events_available
    ON equities_events (available_at_ts DESC);
CREATE INDEX IF NOT EXISTS idx_equities_events_dedupe
    ON equities_events (dedupe_hash);
CREATE INDEX IF NOT EXISTS idx_equities_events_payload_gin
    ON equities_events USING GIN(payload);

CREATE TABLE IF NOT EXISTS equities_feature_events (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,
    available_at_ts TIMESTAMPTZ NOT NULL,
    horizon TEXT NOT NULL,
    session_state TEXT NOT NULL CHECK (session_state IN ('RTH', 'ETH', 'CLOSED')),
    features JSONB NOT NULL,
    regime_labels JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(symbol, timeframe, bar_start_ts, horizon, available_at_ts)
);

CREATE INDEX IF NOT EXISTS idx_equities_feature_events_lookup
    ON equities_feature_events (symbol, timeframe, bar_start_ts DESC, horizon);
CREATE INDEX IF NOT EXISTS idx_equities_feature_events_available
    ON equities_feature_events (available_at_ts DESC);

CREATE TABLE IF NOT EXISTS equities_predictions (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,
    horizon TEXT NOT NULL,
    available_at_ts TIMESTAMPTZ NOT NULL,
    predicted_return DOUBLE PRECISION,
    predicted_prob_up DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    gap_risk_score DOUBLE PRECISION,
    regime_label TEXT,
    feature_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb,
    deterministic_hash TEXT NOT NULL,
    UNIQUE(model_name, model_version, symbol, timeframe, bar_start_ts, horizon)
);

CREATE INDEX IF NOT EXISTS idx_equities_predictions_symbol_horizon
    ON equities_predictions (symbol, horizon, bar_start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_equities_predictions_prob
    ON equities_predictions (horizon, predicted_prob_up DESC);

CREATE TABLE IF NOT EXISTS equities_opportunities (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prediction_id UUID NOT NULL REFERENCES equities_predictions(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,
    available_at_ts TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    expected_return DOUBLE PRECISION,
    probability_up DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    regime_label TEXT,
    rationale JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    legacy_opportunity_id INT REFERENCES opportunities(id)
);

CREATE INDEX IF NOT EXISTS idx_equities_opportunities_status_score
    ON equities_opportunities (status, confidence DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_equities_opportunities_symbol
    ON equities_opportunities (symbol, horizon, created_at DESC);

CREATE TABLE IF NOT EXISTS equities_model_evaluations (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    split_type TEXT NOT NULL,
    metrics JSONB NOT NULL,
    notes TEXT,
    deterministic_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_equities_model_evaluations_lookup
    ON equities_model_evaluations (model_name, model_version, horizon, created_at DESC);

CREATE TABLE IF NOT EXISTS equities_pipeline_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_equities_pipeline_events_ts
    ON equities_pipeline_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_equities_pipeline_events_type
    ON equities_pipeline_events (event_type, ts DESC);

-- Append-only guards
CREATE OR REPLACE FUNCTION equities_append_only_guard()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Append-only table: %', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_equities_events_append_only ON equities_events;
CREATE TRIGGER trg_equities_events_append_only
BEFORE UPDATE OR DELETE ON equities_events
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

DROP TRIGGER IF EXISTS trg_equities_feature_events_append_only ON equities_feature_events;
CREATE TRIGGER trg_equities_feature_events_append_only
BEFORE UPDATE OR DELETE ON equities_feature_events
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

DROP TRIGGER IF EXISTS trg_equities_predictions_append_only ON equities_predictions;
CREATE TRIGGER trg_equities_predictions_append_only
BEFORE UPDATE OR DELETE ON equities_predictions
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

DROP TRIGGER IF EXISTS trg_equities_model_evaluations_append_only ON equities_model_evaluations;
CREATE TRIGGER trg_equities_model_evaluations_append_only
BEFORE UPDATE OR DELETE ON equities_model_evaluations
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

DROP TRIGGER IF EXISTS trg_equities_pipeline_events_append_only ON equities_pipeline_events;
CREATE TRIGGER trg_equities_pipeline_events_append_only
BEFORE UPDATE OR DELETE ON equities_pipeline_events
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

INSERT INTO schema_versions (filename, checksum)
VALUES (
    '20260222_add_equities_predictor_tables.sql',
    'equities_symbol_registry+equities_events+equities_feature_events+equities_predictions+equities_opportunities'
)
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(2026022202);
