-- Migration: Add derivatives data tables for on-chain pipeline
-- Follows MCAS migration conventions (IF NOT EXISTS, advisory locks)

DO $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('add_derivatives_tables'));
END $$;

-- ═══════════════════════════════════════════════════════════════
-- RAW DATA TABLES (append-only, full provenance)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS funding_rates (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    funding_rate DOUBLE PRECISION NOT NULL,
    funding_time TIMESTAMPTZ NOT NULL,
    mark_price DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'binance_futures',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, funding_time, source)
);
CREATE INDEX IF NOT EXISTS idx_funding_rates_lookup
    ON funding_rates(symbol, funding_time DESC);

CREATE TABLE IF NOT EXISTS open_interest (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    sum_open_interest DOUBLE PRECISION NOT NULL,
    sum_open_interest_value DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL DEFAULT 'binance_futures',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_open_interest_lookup
    ON open_interest(symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS long_short_ratio (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    long_short_ratio DOUBLE PRECISION NOT NULL,
    long_account DOUBLE PRECISION,
    short_account DOUBLE PRECISION,
    long_position DOUBLE PRECISION,
    short_position DOUBLE PRECISION,
    ratio_type TEXT NOT NULL DEFAULT 'global',
    source TEXT NOT NULL DEFAULT 'binance_futures',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, ratio_type, source)
);
CREATE INDEX IF NOT EXISTS idx_long_short_ratio_lookup
    ON long_short_ratio(symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS taker_buy_sell (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    buy_vol DOUBLE PRECISION NOT NULL,
    sell_vol DOUBLE PRECISION NOT NULL,
    buy_sell_ratio DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'binance_futures',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_taker_buy_sell_lookup
    ON taker_buy_sell(symbol, timestamp DESC);

-- ═══════════════════════════════════════════════════════════════
-- MATERIALIZED FEATURE TABLE (rebuilt hourly by feature pipeline)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS derivatives_features_1h (
    symbol TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,

    -- Funding rate features
    funding_rate DOUBLE PRECISION,
    funding_rate_ma8 DOUBLE PRECISION,
    funding_rate_acceleration DOUBLE PRECISION,
    funding_rate_zscore DOUBLE PRECISION,

    -- Open interest features
    oi_value DOUBLE PRECISION,
    oi_change_pct_1h DOUBLE PRECISION,
    oi_change_pct_4h DOUBLE PRECISION,
    oi_change_pct_24h DOUBLE PRECISION,
    oi_volume_ratio DOUBLE PRECISION,

    -- Long/short features
    ls_ratio DOUBLE PRECISION,
    ls_ratio_change_4h DOUBLE PRECISION,
    ls_ratio_extreme DOUBLE PRECISION,

    -- Taker flow features
    taker_buy_ratio DOUBLE PRECISION,
    taker_imbalance DOUBLE PRECISION,
    taker_imbalance_ma DOUBLE PRECISION,

    -- Metadata
    data_completeness DOUBLE PRECISION,
    materialized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (symbol, bar_start_ts)
);
CREATE INDEX IF NOT EXISTS idx_deriv_features_ts
    ON derivatives_features_1h(bar_start_ts DESC);

-- Track migration
INSERT INTO schema_versions (filename)
VALUES ('20260219_add_derivatives_tables.sql')
ON CONFLICT DO NOTHING;
