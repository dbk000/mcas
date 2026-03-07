-- MCAS Database Schema v7
-- Complete schema for trading system with paper trading support
--
-- =============================================================================
-- DDL APPLICATION ORDER (Canon §1.10)
-- =============================================================================
-- 1. Apply this file FIRST:
--       psql -U trading -d trading -f mcas_schema.sql
--
-- 2. Then apply migrations in filename order:
--       for f in migrations/*.sql; do psql -U trading -d trading -f "$f"; done
--
-- Migration files use IF NOT EXISTS and advisory locks for idempotency.
-- The schema_versions table (migration 20260207_add_schema_versions.sql)
-- tracks which migrations have been applied.
--
-- NOTE: ohlcv column was renamed from open_time to bar_start_ts (Canon §1.3)
-- via migration 20260207_rename_open_time_bar_start_ts.sql.
-- =============================================================================

-- =============================================================================
-- RUNS AND QUEUE
-- =============================================================================

CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY,
    run_type TEXT NOT NULL CHECK (run_type IN ('SCAN', 'BACKTEST', 'PAPER', 'LIVE', 'SIMULATION')),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')),
    name TEXT,
    fingerprint TEXT,
    
    -- Specs (JSON)
    engine_version TEXT DEFAULT '1.0.0',
    universe_json TEXT,
    strategy_json TEXT,
    execution_json TEXT,
    data_json TEXT,
    
    -- Summary
    summary_json TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    
    -- Duration
    duration_seconds FLOAT
);

CREATE INDEX IF NOT EXISTS idx_runs_type_status ON runs(run_type, status);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);

CREATE TABLE IF NOT EXISTS run_queue (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    
    -- Queue state
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'CLAIMED', 'COMPLETED', 'FAILED')),
    priority INT NOT NULL DEFAULT 0,
    
    -- Claim info
    claimed_by TEXT,
    claimed_at TIMESTAMPTZ,
    
    -- Completion
    completed_at TIMESTAMPTZ,
    last_error TEXT,
    
    -- Retry
    attempt_count INT NOT NULL DEFAULT 0,
    max_attempts INT DEFAULT 3,
    
    -- Timestamps
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_run_queue_run ON run_queue(run_id);
CREATE INDEX IF NOT EXISTS idx_run_queue_status ON run_queue(status, priority DESC, queued_at);

CREATE TABLE IF NOT EXISTS run_events (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('INFO', 'WARNING', 'ERROR', 'DEBUG')),
    message TEXT NOT NULL,
    payload_json TEXT,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, ts DESC);

CREATE TABLE IF NOT EXISTS run_trades (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,

    -- Position identity
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    setup_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),

    -- Status
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'CANCELLED')),

    -- Canon 5A: signal bar identity (distinct from entry_ts)
    signal_bar_ts TIMESTAMPTZ,             -- Which candle produced the trigger

    -- Canon 5E: signal timing metadata
    since_ts TIMESTAMPTZ,                  -- When signal entered current state
    action_by_ts TIMESTAMPTZ,              -- Latest time to act (time-stop deadline)

    -- Entry
    entry_ts TIMESTAMPTZ,
    entry_bar_idx INT,
    signal_price FLOAT,
    entry_price FLOAT,
    entry_price_with_slippage FLOAT,

    -- Position
    position_size FLOAT,
    r_value FLOAT,

    -- Levels
    stop_price FLOAT,
    tp1_price FLOAT,
    tp2_price FLOAT,

    -- Exit
    exit_ts TIMESTAMPTZ,
    exit_bar_idx INT,
    exit_price FLOAT,
    exit_price_with_slippage FLOAT,
    exit_reason TEXT,

    -- Results
    bars_held INT,
    pnl FLOAT,
    pnl_pct FLOAT,
    r_multiple FLOAT,
    mae FLOAT,  -- Maximum Adverse Excursion
    mfe FLOAT,  -- Maximum Favorable Excursion

    -- Fees
    fees_paid FLOAT DEFAULT 0,

    -- Audit trace (JSON)
    trace_json TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_trades_run ON run_trades(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_run_trades_symbol ON run_trades(symbol, timeframe, status);

-- =============================================================================
-- PAPER TRADING
-- =============================================================================

CREATE TABLE IF NOT EXISTS paper_accounts (
    run_id UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    
    -- Capital tracking
    initial_capital FLOAT NOT NULL,
    current_capital FLOAT NOT NULL,
    peak_capital FLOAT NOT NULL,
    
    -- Performance
    total_trades INT NOT NULL DEFAULT 0,
    winning_trades INT NOT NULL DEFAULT 0,
    losing_trades INT NOT NULL DEFAULT 0,
    total_pnl FLOAT NOT NULL DEFAULT 0,
    total_fees FLOAT NOT NULL DEFAULT 0,
    max_drawdown FLOAT NOT NULL DEFAULT 0,
    
    -- Risk tracking
    current_exposure FLOAT NOT NULL DEFAULT 0,
    
    -- Daily stats
    daily_pnl FLOAT NOT NULL DEFAULT 0,
    daily_pnl_reset_date DATE,
    trades_today INT DEFAULT 0,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    
    -- Position identity
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL DEFAULT '1h',
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    setup_id TEXT NOT NULL,
    
    -- Status
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('PENDING', 'OPEN', 'CLOSED')),
    
    -- Signal
    signal_ts TIMESTAMPTZ,
    signal_price FLOAT,
    
    -- Entry
    entry_ts TIMESTAMPTZ,
    entry_price FLOAT,
    entry_price_with_slippage FLOAT,
    
    -- Levels
    stop_price FLOAT,
    tp1_price FLOAT,
    tp2_price FLOAT,
    r_value FLOAT,
    
    -- Position sizing
    position_size FLOAT,
    notional_value FLOAT,
    risk_amount FLOAT,
    
    -- Current state
    current_price FLOAT,
    unrealized_pnl FLOAT,
    high_water_mark FLOAT,
    bars_held INT DEFAULT 0,
    
    -- Exit
    exit_ts TIMESTAMPTZ,
    exit_price FLOAT,
    exit_price_with_slippage FLOAT,
    exit_reason TEXT,
    realized_pnl FLOAT,
    r_multiple FLOAT,
    
    -- Fees
    fees_paid FLOAT DEFAULT 0,
    
    -- Audit trace (JSON)
    trace_json TEXT,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_run ON paper_positions(run_id, status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_symbol ON paper_positions(symbol, status);

-- =============================================================================
-- OHLCV DATA
-- =============================================================================

CREATE TABLE IF NOT EXISTS ohlcv (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,      -- Canon §1.3: canonical candle identifier (UTC)
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,

    -- Canon 4A: data provenance
    as_of_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),   -- When data was last refreshed
    is_final BOOLEAN NOT NULL DEFAULT TRUE,         -- Bar closed (Canon 8: anti-lookahead)

    -- Ingestion provenance
    source TEXT NOT NULL DEFAULT 'binance',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,

    UNIQUE(symbol, timeframe, bar_start_ts, source)
);

-- Retention policy (Canon §1.9):
--   1m candles: retain 90 days, prune older.
--   5m/15m candles: retain 180 days, prune older.
--   1h+ candles: retained indefinitely (small volume, needed for backtests).
--   Pruning is NOT yet automated — implement via worker scheduled task or pg_cron.

CREATE INDEX IF NOT EXISTS idx_ohlcv_bar_start ON ohlcv(symbol, timeframe, bar_start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_batch ON ohlcv(batch_id);

CREATE TABLE IF NOT EXISTS data_snapshots (
    id UUID PRIMARY KEY,
    name TEXT,
    description TEXT,
    
    -- Scope
    symbols_json TEXT,
    timeframes_json TEXT,
    start_ts TIMESTAMPTZ,
    end_ts TIMESTAMPTZ,
    
    -- Integrity
    total_bars INT,
    checksum TEXT,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- SIGNAL EVENTS (Canon §1.9 — Evented Audit Logs)
-- =============================================================================

CREATE TABLE IF NOT EXISTS signal_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'SETUP_ARMED', 'SETUP_FIRED', 'SETUP_INVALIDATED',
        'THRESHOLD_CROSS', 'RANK_CHANGE',
        'ENTRY_DECISION', 'EXIT_DECISION', 'STATE_FLIP'
    )),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    setup_id TEXT NOT NULL,
    direction TEXT,
    bar_start_ts TIMESTAMPTZ NOT NULL,
    as_of_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    setup_score FLOAT,
    rank INT,
    feature_snapshot JSONB,
    score_components JSONB,
    reason_codes TEXT[],
    trade_id UUID,
    run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signal_events_symbol_ts ON signal_events(symbol, timeframe, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_type ON signal_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_setup ON signal_events(setup_id, created_at DESC);

-- =============================================================================
-- LEGACY TABLES (for backward compatibility)
-- =============================================================================

CREATE TABLE IF NOT EXISTS opportunities (
    id SERIAL PRIMARY KEY,
    created_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_id INT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    setup_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    score FLOAT NOT NULL DEFAULT 0,
    reason TEXT,
    payload_json TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    setup_version TEXT,
    trace_json JSONB DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_opps_status_score_ts ON opportunities(status, score DESC, updated_ts DESC);

CREATE TABLE IF NOT EXISTS signal_queue (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    setup_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    result_json TEXT,
    updated_ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- FRONTEND TELEMETRY LOGS
-- =============================================================================

CREATE TABLE IF NOT EXISTS frontend_logs (
    id UUID PRIMARY KEY,
    session_id VARCHAR(32) NOT NULL,
    correlation_id VARCHAR(16),
    level VARCHAR(10) NOT NULL,
    category VARCHAR(64) NOT NULL,
    message TEXT NOT NULL,
    data JSONB,
    user_agent TEXT,
    client_ts TIMESTAMPTZ,
    server_ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_frontend_logs_session ON frontend_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_frontend_logs_correlation ON frontend_logs(correlation_id);
CREATE INDEX IF NOT EXISTS idx_frontend_logs_server_ts ON frontend_logs(server_ts DESC);

-- =============================================================================
-- SIMULATION RESULTS
-- =============================================================================

CREATE TABLE IF NOT EXISTS simulation_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    opportunity_id INT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    setup_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    opportunity_created_ts TIMESTAMPTZ NOT NULL,

    -- Entry
    entry_ts TIMESTAMPTZ,
    entry_price FLOAT,
    entry_price_with_slippage FLOAT,
    position_size FLOAT,

    -- Levels
    stop_price FLOAT,
    tp1_price FLOAT,
    tp2_price FLOAT,
    r_value FLOAT,

    -- Exit
    exit_ts TIMESTAMPTZ,
    exit_price FLOAT,
    exit_price_with_slippage FLOAT,
    exit_reason TEXT,
    bars_held INT,

    -- Results
    pnl_gross FLOAT,
    fees FLOAT,
    pnl_net FLOAT,
    r_multiple FLOAT,

    -- Metadata
    data_available BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_simulation_results_run ON simulation_results(run_id);
