-- Migration: Add DexScreener + Dune Analytics tables
-- Sources: DexScreener (free, no auth), Dune Analytics (free tier, 2500 credits/month)
-- Follows MCAS migration conventions (IF NOT EXISTS, advisory locks)

DO $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('add_dexscreener_dune_tables'));
END $$;

-- ═══════════════════════════════════════════════════════════════
-- DexScreener: Hourly DEX pair snapshots
-- ═══════════════════════════════════════════════════════════════
-- Each row = one DEX pair for one token at one point in time.
-- Multiple pairs per token (different DEXes, pools) are stored.

CREATE TABLE IF NOT EXISTS dex_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,                    -- Binance symbol (e.g. 'ETHUSDT')
    timestamp TIMESTAMPTZ NOT NULL,          -- collection time (hourly)
    chain TEXT NOT NULL,                     -- DexScreener chainId (e.g. 'ethereum', 'bsc')
    pair_address TEXT NOT NULL,              -- DEX pair contract address
    dex_id TEXT,                             -- e.g. 'uniswap', 'pancakeswap'
    base_token_address TEXT,
    quote_token_symbol TEXT,                 -- e.g. 'WETH', 'USDT', 'USDC'
    price_usd DOUBLE PRECISION,
    volume_h1_usd DOUBLE PRECISION,          -- rolling 1h volume
    volume_h6_usd DOUBLE PRECISION,          -- rolling 6h volume
    volume_h24_usd DOUBLE PRECISION,         -- rolling 24h volume
    liquidity_usd DOUBLE PRECISION,          -- total pool liquidity
    liquidity_base DOUBLE PRECISION,
    liquidity_quote DOUBLE PRECISION,
    txns_h1_buys INTEGER,
    txns_h1_sells INTEGER,
    txns_h24_buys INTEGER,
    txns_h24_sells INTEGER,
    price_change_h1 DOUBLE PRECISION,        -- percentage
    price_change_h6 DOUBLE PRECISION,
    price_change_h24 DOUBLE PRECISION,
    fdv DOUBLE PRECISION,                    -- fully diluted valuation
    market_cap DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'dexscreener',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, timestamp, chain, pair_address, source)
);
CREATE INDEX IF NOT EXISTS idx_dex_snapshots_lookup
    ON dex_snapshots(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_dex_snapshots_chain
    ON dex_snapshots(chain, timestamp DESC);

-- ═══════════════════════════════════════════════════════════════
-- Dune Analytics: Daily DEX volume aggregates
-- ═══════════════════════════════════════════════════════════════
-- From dex.trades on Dune — historical daily DEX volume per token per chain.
-- Used for: (1) backfill before DexScreener started, (2) cross-validation.

CREATE TABLE IF NOT EXISTS dune_dex_volume (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,                    -- Binance symbol
    block_date DATE NOT NULL,
    chain TEXT NOT NULL,                     -- 'ethereum', 'bnb'
    token_address TEXT NOT NULL,             -- on-chain contract address
    volume_usd DOUBLE PRECISION NOT NULL,
    trade_count INTEGER,
    source TEXT NOT NULL DEFAULT 'dune',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, block_date, chain, token_address, source)
);
CREATE INDEX IF NOT EXISTS idx_dune_dex_volume_lookup
    ON dune_dex_volume(symbol, block_date DESC);

-- ═══════════════════════════════════════════════════════════════
-- Dune Analytics: Daily large transfer aggregates (whale signals)
-- ═══════════════════════════════════════════════════════════════
-- Individual transfers >$100K aggregated per token per day.
-- Collected every 3 days to stay within credit budget.

CREATE TABLE IF NOT EXISTS dune_large_transfers (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,                    -- Binance symbol
    block_date DATE NOT NULL,
    chain TEXT NOT NULL,                     -- 'ethereum', 'bnb'
    token_address TEXT NOT NULL,
    transfer_count INTEGER NOT NULL,         -- count of transfers >$100K
    total_volume_usd DOUBLE PRECISION,       -- sum of all >$100K transfer values
    unique_senders INTEGER,
    unique_receivers INTEGER,
    source TEXT NOT NULL DEFAULT 'dune',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    UNIQUE(symbol, block_date, chain, token_address, source)
);
CREATE INDEX IF NOT EXISTS idx_dune_large_transfers_lookup
    ON dune_large_transfers(symbol, block_date DESC);

-- ═══════════════════════════════════════════════════════════════
-- Dune Analytics: Execution tracker (credit accounting + resume)
-- ═══════════════════════════════════════════════════════════════
-- Every Dune API execution is logged here for credit auditing.

CREATE TABLE IF NOT EXISTS dune_executions (
    id BIGSERIAL PRIMARY KEY,
    execution_id TEXT NOT NULL UNIQUE,       -- Dune execution UUID
    query_name TEXT NOT NULL,                -- 'dex_volume', 'large_transfers'
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'executing', 'completed', 'failed'
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    credits_used DOUBLE PRECISION,
    rows_returned INTEGER,
    date_from DATE,                          -- query date range
    date_to DATE,
    chunk_index INTEGER,                     -- which address chunk (0-based)
    error_message TEXT,
    batch_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_dune_executions_status
    ON dune_executions(status, submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_dune_executions_query
    ON dune_executions(query_name, submitted_at DESC);
