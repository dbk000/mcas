-- MCAS Database Schema v7
-- Complete schema for trading system with paper trading support
-- Run: psql -U trading -d trading -f mcas_schema.sql

-- =============================================================================
-- RUNS AND QUEUE
-- =============================================================================

CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY,
    run_type TEXT NOT NULL CHECK (run_type IN ('SCAN', 'BACKTEST', 'PAPER', 'LIVE')),
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
    data_json TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, created_at DESC);

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
    open_time BIGINT NOT NULL,  -- Unix ms
    open FLOAT NOT NULL,
    high FLOAT NOT NULL,
    low FLOAT NOT NULL,
    close FLOAT NOT NULL,
    volume FLOAT NOT NULL,
    close_time BIGINT,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE(symbol, timeframe, open_time)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup ON ohlcv(symbol, timeframe, open_time DESC);

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
    status TEXT NOT NULL DEFAULT 'active'
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
