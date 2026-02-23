-- Migration: Add on-chain data tables for full data pipeline
-- Follows MCAS migration conventions (IF NOT EXISTS, advisory locks)
-- Sources: DefiLlama, CoinGecko, Etherscan/BSCScan, Coinglass, Santiment

DO $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('add_onchain_tables'));
END $$;

-- ═══════════════════════════════════════════════════════════════
-- RAW DATA TABLES (append-only, full provenance)
-- ═══════════════════════════════════════════════════════════════

-- ─── DefiLlama: Protocol TVL ─────────────────────────────────
CREATE TABLE IF NOT EXISTS protocol_tvl (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    tvl_usd DOUBLE PRECISION NOT NULL,
    chain TEXT,                          -- e.g. 'ethereum', 'bsc', 'multi'
    source TEXT NOT NULL DEFAULT 'defillama',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_protocol_tvl_lookup
    ON protocol_tvl(symbol, timestamp DESC);

-- ─── DefiLlama: DEX Volume ───────────────────────────────────
CREATE TABLE IF NOT EXISTS dex_volume (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    volume_usd DOUBLE PRECISION NOT NULL,
    source_dex TEXT,                     -- e.g. 'uniswap', 'pancakeswap'
    chain TEXT,
    source TEXT NOT NULL DEFAULT 'defillama',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source_dex, source)
);
CREATE INDEX IF NOT EXISTS idx_dex_volume_lookup
    ON dex_volume(symbol, timestamp DESC);

-- ─── DefiLlama: Stablecoin Supply ────────────────────────────
CREATE TABLE IF NOT EXISTS stablecoin_supply (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,                -- e.g. 'USDT', 'USDC'
    timestamp TIMESTAMPTZ NOT NULL,
    total_circulating DOUBLE PRECISION NOT NULL,
    total_circulating_usd DOUBLE PRECISION,
    chain TEXT DEFAULT 'all',            -- per-chain or 'all'
    source TEXT NOT NULL DEFAULT 'defillama',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, chain, source)
);
CREATE INDEX IF NOT EXISTS idx_stablecoin_supply_lookup
    ON stablecoin_supply(symbol, timestamp DESC);

-- ─── CoinGecko: Market Metadata ──────────────────────────────
CREATE TABLE IF NOT EXISTS market_metadata (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    market_cap_usd DOUBLE PRECISION,
    market_cap_rank INTEGER,
    circulating_supply DOUBLE PRECISION,
    total_supply DOUBLE PRECISION,
    max_supply DOUBLE PRECISION,
    twitter_followers INTEGER,
    reddit_subscribers INTEGER,
    github_stars INTEGER,
    github_forks INTEGER,
    github_commits_4w INTEGER,
    source TEXT NOT NULL DEFAULT 'coingecko',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_market_metadata_lookup
    ON market_metadata(symbol, timestamp DESC);

-- ─── Etherscan/BSCScan: Token Holder Stats ───────────────────
CREATE TABLE IF NOT EXISTS token_holder_stats (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    chain TEXT NOT NULL,                 -- 'ethereum', 'bsc'
    unique_holders INTEGER,
    top10_pct DOUBLE PRECISION,          -- % held by top 10 holders
    top50_pct DOUBLE PRECISION,          -- % held by top 50 holders
    contract_address TEXT,
    source TEXT NOT NULL DEFAULT 'etherscan',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, chain, source)
);
CREATE INDEX IF NOT EXISTS idx_token_holder_stats_lookup
    ON token_holder_stats(symbol, timestamp DESC);

-- ─── Coinglass: Liquidation Data ─────────────────────────────
CREATE TABLE IF NOT EXISTS liquidations (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    long_liquidation_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    short_liquidation_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    long_count INTEGER DEFAULT 0,
    short_count INTEGER DEFAULT 0,
    period TEXT NOT NULL DEFAULT '1h',   -- '5m', '15m', '1h', '4h', '1d'
    source TEXT NOT NULL DEFAULT 'coinglass',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, period, source)
);
CREATE INDEX IF NOT EXISTS idx_liquidations_lookup
    ON liquidations(symbol, timestamp DESC);

-- ─── Coinglass: Extended OI History ──────────────────────────
CREATE TABLE IF NOT EXISTS oi_history_extended (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open_interest_usd DOUBLE PRECISION NOT NULL,
    oi_change_pct DOUBLE PRECISION,
    exchange TEXT DEFAULT 'binance',
    source TEXT NOT NULL DEFAULT 'coinglass',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, exchange, source)
);
CREATE INDEX IF NOT EXISTS idx_oi_history_ext_lookup
    ON oi_history_extended(symbol, timestamp DESC);

-- ─── Santiment: Exchange Flows ───────────────────────────────
CREATE TABLE IF NOT EXISTS exchange_flows (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    exchange_inflow DOUBLE PRECISION,    -- tokens flowing into exchanges
    exchange_outflow DOUBLE PRECISION,   -- tokens flowing out of exchanges
    exchange_netflow DOUBLE PRECISION,   -- inflow - outflow
    source TEXT NOT NULL DEFAULT 'santiment',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_exchange_flows_lookup
    ON exchange_flows(symbol, timestamp DESC);

-- ─── Santiment: Whale Transactions ───────────────────────────
CREATE TABLE IF NOT EXISTS whale_transactions (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    whale_tx_count INTEGER,              -- count of txns > $100K
    whale_tx_volume_usd DOUBLE PRECISION, -- total USD volume of whale txns
    source TEXT NOT NULL DEFAULT 'santiment',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_whale_tx_lookup
    ON whale_transactions(symbol, timestamp DESC);

-- ─── Santiment: Network Activity ─────────────────────────────
CREATE TABLE IF NOT EXISTS network_activity (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    active_addresses_24h INTEGER,
    network_growth INTEGER,              -- new addresses created
    transaction_volume DOUBLE PRECISION,
    dev_activity DOUBLE PRECISION,       -- github proxy metric
    social_volume INTEGER,               -- mentions across crypto social
    social_dominance DOUBLE PRECISION,   -- % of total crypto social
    source TEXT NOT NULL DEFAULT 'santiment',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_network_activity_lookup
    ON network_activity(symbol, timestamp DESC);

-- ─── Santiment: Holder Distribution ──────────────────────────
CREATE TABLE IF NOT EXISTS holder_distribution (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    tier TEXT NOT NULL,                   -- e.g. '0_to_1', '1_to_10', '10k_to_100k', '100k_to_1m', 'whale'
    address_count INTEGER,
    total_held DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'santiment',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, tier, source)
);
CREATE INDEX IF NOT EXISTS idx_holder_distribution_lookup
    ON holder_distribution(symbol, timestamp DESC);

-- ═══════════════════════════════════════════════════════════════
-- SYMBOL MAPPING TABLE (maps Binance symbols to external IDs)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS symbol_mapping (
    symbol TEXT NOT NULL,                -- Binance symbol e.g. 'BTCUSDT'
    source TEXT NOT NULL,                -- e.g. 'coingecko', 'santiment', 'defillama', 'etherscan'
    external_id TEXT NOT NULL,           -- Source-specific ID
    chain TEXT,                          -- Blockchain (for etherscan/bscscan)
    contract_address TEXT,               -- Token contract (for etherscan/bscscan)
    mapped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, source)
);

-- ═══════════════════════════════════════════════════════════════
-- MATERIALIZED FEATURE TABLE (rebuilt hourly from raw tables)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS onchain_features_1h (
    symbol TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,

    -- Exchange flow features (Santiment)
    exchange_inflow_zscore DOUBLE PRECISION,
    exchange_outflow_zscore DOUBLE PRECISION,
    exchange_netflow_zscore DOUBLE PRECISION,
    exchange_netflow_ma24 DOUBLE PRECISION,

    -- Whale features (Santiment)
    whale_tx_count DOUBLE PRECISION,
    whale_tx_count_zscore DOUBLE PRECISION,
    whale_tx_volume_zscore DOUBLE PRECISION,

    -- Holder distribution (Santiment + Etherscan)
    whale_holder_pct_change_7d DOUBLE PRECISION,
    unique_holders_growth_rate DOUBLE PRECISION,
    holder_concentration_change DOUBLE PRECISION,

    -- Network activity (Santiment)
    active_addresses_zscore DOUBLE PRECISION,
    network_growth_zscore DOUBLE PRECISION,
    social_volume_zscore DOUBLE PRECISION,
    social_volume_acceleration DOUBLE PRECISION,
    dev_activity_zscore DOUBLE PRECISION,

    -- TVL features (DefiLlama)
    tvl_change_pct_1d DOUBLE PRECISION,
    tvl_change_pct_7d DOUBLE PRECISION,
    tvl_zscore DOUBLE PRECISION,

    -- DEX features (DefiLlama)
    dex_volume_zscore DOUBLE PRECISION,
    dex_cex_volume_ratio DOUBLE PRECISION,

    -- Stablecoin features (DefiLlama) — market-level, same for all symbols
    stablecoin_supply_change_1d DOUBLE PRECISION,
    stablecoin_supply_change_7d DOUBLE PRECISION,

    -- Liquidation features (Coinglass)
    long_liquidation_zscore DOUBLE PRECISION,
    short_liquidation_zscore DOUBLE PRECISION,
    liquidation_imbalance DOUBLE PRECISION,   -- (long - short) / (long + short)
    liquidation_cascade_score DOUBLE PRECISION, -- cumulative long liqs in prior 24h

    -- Extended OI features (Coinglass — fills the 30d gap from Binance)
    oi_extended_change_7d DOUBLE PRECISION,
    oi_extended_change_30d DOUBLE PRECISION,
    oi_extended_zscore DOUBLE PRECISION,

    -- Supply features (CoinGecko)
    circulating_supply_change_7d DOUBLE PRECISION,
    market_cap_rank_change_7d DOUBLE PRECISION,

    -- Metadata
    data_completeness DOUBLE PRECISION,
    materialized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (symbol, bar_start_ts)
);
CREATE INDEX IF NOT EXISTS idx_onchain_features_ts
    ON onchain_features_1h(bar_start_ts DESC);

-- Track migration
INSERT INTO schema_versions (version, description)
VALUES ('add_onchain_tables', 'On-chain raw data tables + materialized 1h feature table for full data pipeline')
ON CONFLICT DO NOTHING;
