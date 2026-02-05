-- =============================================================================
-- MIGRATION: add_cc_tables
-- =============================================================================
-- File: Backend/docs/db/migrations/20260203_000000_add_cc_tables.sql
-- Champion/Challenger Framework - Database Schema
--
-- DO NOT EXECUTE FROM APPLICATION CODE.
-- Apply via: psql -U trading -d trading -f <this_file>
-- =============================================================================
--
-- IMPORTANT NOTES & CAVEATS:
--
-- 1. IF NOT EXISTS CAN HIDE SCHEMA DRIFT
--    ---------------------------------
--    If any of these tables already exist with a different column set or
--    constraints, this script will silently skip creating them and you'll
--    think you're migrated when you're not.
--
--    MITIGATION: For anything beyond "fresh install", prefer versioned
--    migrations (Alembic) or run the verification query at the end of this
--    file to assert expected columns exist.
--
-- 2. DDL SHOULD NEVER RUN AT RUNTIME
--    --------------------------------
--    Even safe DDL causes locks and can break concurrent requests during
--    deploys. This file must only be run during deployment, not from app code.
--
--    MITIGATION (strong): Ensure the application DB role does NOT have CREATE
--    privileges in the schema. Only the migration role should run DDL.
--
--    Example:
--      REVOKE CREATE ON SCHEMA public FROM trading_app_role;
--      GRANT CREATE ON SCHEMA public TO trading_migrations_role;
--
-- 3. FLOAT COLUMNS FOR PRICES/PNL/FEES
--    ----------------------------------
--    This schema uses FLOAT for prices, pnl, fees, and metrics. This is
--    acceptable for analytics but is NOT exact (floating point precision).
--
--    FUTURE MITIGATION: If ledger-grade exactness is ever required, migrate
--    money fields to NUMERIC(precision, scale). Example:
--      entry_price NUMERIC(18, 8)  -- 18 total digits, 8 after decimal
--
-- 4. CONCURRENT MIGRATION RUNS
--    --------------------------
--    If two deploy processes run this file simultaneously, Postgres generally
--    handles IF NOT EXISTS, but edge-case contention/locks can occur.
--
--    MITIGATION: This migration acquires an advisory lock at the start.
--    Only one process can hold the lock; others will wait or fail.
--
-- =============================================================================

-- -----------------------------------------------------------------------------
-- ADVISORY LOCK: Prevent concurrent migration runs
-- -----------------------------------------------------------------------------
-- Lock ID 20260203 derived from migration date. pg_advisory_lock blocks until
-- acquired; use pg_try_advisory_lock if you prefer fail-fast behavior.
-- The lock is automatically released at end of session (connection close).
-- -----------------------------------------------------------------------------
SELECT pg_advisory_lock(20260203);

-- PREREQUISITE: Ensure pgcrypto extension is available
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- =============================================================================
-- CHAMPION/CHALLENGER: VARIANT REGISTRY
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_variants (
    variant_id TEXT PRIMARY KEY,              -- e.g., "S1@2.0.3"
    setup_id TEXT NOT NULL,                   -- e.g., "S1", "S2", "U1"
    status TEXT NOT NULL DEFAULT 'CHALLENGER'
        CHECK (status IN ('CHAMPION', 'CHALLENGER', 'BASELINE', 'RETIRED')),

    -- Lineage
    parent_variant_id TEXT REFERENCES cc_variants(variant_id),

    -- Diff specification (what changed from parent)
    -- module_kind: which slot changed (FILTER/REGIME/ENTRY/STOP/EXIT/SIZING/SCORE)
    -- diff_key: descriptive pointer (e.g., "EXIT.trailing", "ENTRY.trigger")
    diff_type TEXT CHECK (diff_type IN ('MODULE', 'PARAM_BUNDLE', 'SCORE_COMPONENT')),
    diff_module_kind TEXT CHECK (diff_module_kind IN ('FILTER', 'REGIME', 'ENTRY', 'STOP', 'EXIT', 'SIZING', 'SCORE')),
    diff_key TEXT,                            -- Descriptive: "EXIT.trailing"
    diff_description TEXT,                    -- Human-readable

    -- Version info
    version_major INT NOT NULL DEFAULT 1,
    version_minor INT NOT NULL DEFAULT 0,
    version_patch INT NOT NULL DEFAULT 0,

    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT DEFAULT 'system',
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_cc_variants_setup_status ON cc_variants(setup_id, status);
CREATE INDEX IF NOT EXISTS idx_cc_variants_parent ON cc_variants(parent_variant_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: MODULE DEFINITIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_modules (
    module_id TEXT PRIMARY KEY,               -- e.g., "exit.trailing.v1"
    module_kind TEXT NOT NULL
        CHECK (module_kind IN ('FILTER', 'REGIME', 'ENTRY', 'STOP', 'EXIT', 'SIZING', 'SCORE')),
    version TEXT NOT NULL,

    -- Implementation
    implementation_ref TEXT NOT NULL,         -- Python import path
    config_schema JSONB,                      -- JSON schema for params

    -- Metadata
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =============================================================================
-- CHAMPION/CHALLENGER: VARIANT MODULE MAP
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_variant_modules (
    id SERIAL PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id) ON DELETE CASCADE,
    module_kind TEXT NOT NULL
        CHECK (module_kind IN ('FILTER', 'REGIME', 'ENTRY', 'STOP', 'EXIT', 'SIZING', 'SCORE')),
    module_id TEXT NOT NULL REFERENCES cc_modules(module_id),
    module_params JSONB DEFAULT '{}',

    UNIQUE(variant_id, module_kind)
);

CREATE INDEX IF NOT EXISTS idx_cc_variant_modules_variant ON cc_variant_modules(variant_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: EXPERIMENT RUNS
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_experiment_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Variants being compared (3-way: baseline + champion + challenger)
    baseline_variant_id TEXT REFERENCES cc_variants(variant_id),  -- Always runs, can be NULL initially
    champion_variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    challenger_variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),

    -- Frozen module maps at run start (prevents mid-run changes)
    baseline_module_map_snapshot JSONB,
    champion_module_map_snapshot JSONB NOT NULL,
    challenger_module_map_snapshot JSONB NOT NULL,

    -- Run mode
    mode TEXT NOT NULL DEFAULT 'SHADOW'
        CHECK (mode IN ('SHADOW', 'LIVE_SMALL', 'SPLIT')),
    split_baseline_pct FLOAT,                 -- e.g., 10.0 for 10% to baseline
    split_champion_pct FLOAT,                 -- e.g., 60.0 for 60% to champion
    split_challenger_pct FLOAT,               -- e.g., 30.0 for 30% to challenger

    -- Scope
    setup_id TEXT NOT NULL,
    asset_universe_snapshot JSONB,            -- Frozen list of assets
    timeframes TEXT[] NOT NULL,

    -- Execution spec (frozen from paper trading defaults)
    execution_spec_snapshot JSONB NOT NULL,   -- Slippage, fees, fill model

    -- Status
    status TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'COMPLETED', 'CANCELLED', 'PROMOTED')),

    -- Timestamps
    start_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    end_ts TIMESTAMPTZ,

    -- Notes
    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cc_runs_status ON cc_experiment_runs(status, start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_cc_runs_setup ON cc_experiment_runs(setup_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: AGGREGATED METRICS
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_run_metrics (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),

    -- Core metrics
    trade_count INT NOT NULL DEFAULT 0,
    win_count INT NOT NULL DEFAULT 0,
    loss_count INT NOT NULL DEFAULT 0,

    -- Expectancy metrics
    expectancy FLOAT,                         -- Avg R per trade
    total_r FLOAT DEFAULT 0,
    avg_r FLOAT,

    -- PnL
    gross_pnl FLOAT DEFAULT 0,
    net_pnl FLOAT DEFAULT 0,
    total_fees FLOAT DEFAULT 0,

    -- Risk metrics
    max_drawdown_pct FLOAT,
    win_rate FLOAT,
    profit_factor FLOAT,

    -- Timestamps
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(run_id, variant_id)
);

CREATE INDEX IF NOT EXISTS idx_cc_metrics_run ON cc_run_metrics(run_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: PER-ASSET METRICS (for generalization gate)
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_run_metrics_by_asset (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    symbol TEXT NOT NULL,

    -- Metrics (same as aggregated)
    trade_count INT NOT NULL DEFAULT 0,
    expectancy FLOAT,
    total_r FLOAT DEFAULT 0,
    win_rate FLOAT,
    max_drawdown_pct FLOAT,

    UNIQUE(run_id, variant_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_cc_metrics_asset ON cc_run_metrics_by_asset(run_id, variant_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: EXPERIMENT TRADES
-- =============================================================================

-- NOTE: Using TEXT for trade_id to match Python str(uuid.uuid4()) pattern.
-- This avoids type casting issues between Postgres UUID and Python strings.
-- Consistent with approach used elsewhere in MCAS codebase.

CREATE TABLE IF NOT EXISTS cc_experiment_trades (
    id TEXT PRIMARY KEY,                      -- str(uuid.uuid4()) from Python
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),

    -- Trade identity
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_ts TIMESTAMPTZ NOT NULL,
    signal_bar_ts TIMESTAMPTZ,                -- Bar that produced the signal

    -- Entry
    entry_ts TIMESTAMPTZ,
    entry_price FLOAT,
    entry_price_with_slippage FLOAT,

    -- Exit
    exit_ts TIMESTAMPTZ,
    exit_price FLOAT,
    exit_price_with_slippage FLOAT,
    exit_reason TEXT,

    -- Results
    r_multiple FLOAT,
    pnl_gross FLOAT,
    pnl_net FLOAT,
    fees_paid FLOAT,
    bars_held INT,
    mae FLOAT,                                -- Max adverse excursion
    mfe FLOAT,                                -- Max favorable excursion

    -- For live/split modes
    was_executed BOOLEAN DEFAULT FALSE,
    execution_id TEXT,                        -- Links to paper_positions or run_trades

    -- Audit
    trace_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cc_trades_run ON cc_experiment_trades(run_id, variant_id);
CREATE INDEX IF NOT EXISTS idx_cc_trades_symbol ON cc_experiment_trades(symbol, timeframe, signal_ts);


-- =============================================================================
-- CHAMPION/CHALLENGER: TRADE DIAGNOSTICS (wins AND losses)
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_trade_diagnostics (
    id SERIAL PRIMARY KEY,
    trade_id TEXT REFERENCES cc_experiment_trades(id) ON DELETE CASCADE,  -- TEXT to match cc_experiment_trades.id
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    run_id UUID REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,

    -- Trade outcome
    is_winner BOOLEAN NOT NULL,

    -- Classification: separate buckets for wins and losses
    -- Loser buckets (when is_winner = false)
    failure_bucket TEXT
        CHECK (failure_bucket IS NULL OR failure_bucket IN (
            'BAD_REGIME',           -- Wrong market conditions
            'BAD_ENTRY_TIMING',     -- Entry too early/late
            'BAD_STOP',             -- Stop too tight/loose
            'BAD_TP_TRAILING',      -- Exit too early/late
            'LIQUIDITY_ISSUE',      -- Slippage, gap, thin book
            'SIGNAL_CONFLICT',      -- Conflicting signals
            'OTHER'
        )),

    -- Winner buckets (when is_winner = true)
    winner_bucket TEXT
        CHECK (winner_bucket IS NULL OR winner_bucket IN (
            'EARLY_EXIT',              -- Exited before full move captured
            'REGIME_TAILWIND',         -- Won due to favorable regime
            'ENTRY_PRECISION',         -- Entry timing was optimal
            'STOP_NEVER_THREATENED',   -- Stop was never close to being hit
            'HIGH_SLIPPAGE_WIN',       -- Won despite significant slippage
            'CONFLICTING_SIGNAL_WIN',  -- Won despite conflicting signals
            'CLEAN_WIN'                -- Textbook execution
        )),

    -- Constraint: exactly one bucket must be set based on is_winner
    CONSTRAINT chk_bucket_matches_outcome CHECK (
        (is_winner = true AND winner_bucket IS NOT NULL AND failure_bucket IS NULL) OR
        (is_winner = false AND failure_bucket IS NOT NULL AND winner_bucket IS NULL)
    ),

    -- Details
    notes TEXT,
    hypothesis_module_kind TEXT,              -- Module kind suggested by this diagnostic

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cc_diagnostics_run ON cc_trade_diagnostics(run_id, is_winner);
CREATE INDEX IF NOT EXISTS idx_cc_diagnostics_loser ON cc_trade_diagnostics(failure_bucket) WHERE is_winner = false;
CREATE INDEX IF NOT EXISTS idx_cc_diagnostics_winner ON cc_trade_diagnostics(winner_bucket) WHERE is_winner = true;


-- =============================================================================
-- CHAMPION/CHALLENGER: STATE PERSISTENCE (Redis TTL backup)
-- =============================================================================

-- Shadow positions persisted to Postgres as backup against Redis TTL expiry
CREATE TABLE IF NOT EXISTS cc_shadow_positions (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    -- Position state (mirrors Redis shadow position)
    trade_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_ts TIMESTAMPTZ NOT NULL,
    entry_price FLOAT NOT NULL,
    entry_price_with_slippage FLOAT NOT NULL,
    stop_price FLOAT NOT NULL,
    tp1_price FLOAT,
    r_value FLOAT NOT NULL,
    bars_held INT NOT NULL DEFAULT 0,
    half_r_touched BOOLEAN NOT NULL DEFAULT FALSE,
    mae FLOAT DEFAULT 0,
    mfe FLOAT DEFAULT 0,

    -- Pending exit (if flagged for next-bar exit)
    pending_exit_reason TEXT,
    pending_exit_flagged_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(run_id, variant_id, symbol, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_cc_shadow_pos_run ON cc_shadow_positions(run_id, variant_id);

-- Pending signals awaiting next-bar entry (prevents loss on Redis TTL expiry)
CREATE TABLE IF NOT EXISTS cc_pending_signals (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    -- Signal data
    signal_ts TIMESTAMPTZ NOT NULL,
    signal_bar_ts TIMESTAMPTZ NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    stop_price FLOAT NOT NULL,
    tp1_price FLOAT,
    tp2_price FLOAT,
    r_value FLOAT NOT NULL,

    -- Pending age tracking (for stale signal cancellation)
    bars_pending INT NOT NULL DEFAULT 0,
    max_pending_bars INT NOT NULL DEFAULT 3,  -- Cancel if exceeds this

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(run_id, variant_id, symbol, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_cc_pending_signals_run ON cc_pending_signals(run_id, variant_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: CHANGE LEDGER (for weekly cadence enforcement)
-- =============================================================================

-- NOTE (UTC wall-clock drift): created_at uses server-generated NOW() to ensure
-- consistent UTC timestamps regardless of API client clock. All cadence checks
-- compare against DB timestamps, not client timestamps.
CREATE TABLE IF NOT EXISTS cc_change_ledger (
    id SERIAL PRIMARY KEY,
    setup_id TEXT NOT NULL,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    change_type TEXT NOT NULL CHECK (change_type IN ('CHALLENGER_CREATED', 'PROMOTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()  -- Server-generated UTC
);

CREATE INDEX IF NOT EXISTS idx_cc_change_ledger_setup ON cc_change_ledger(setup_id, created_at DESC);


-- =============================================================================
-- SCHEMA VERIFICATION (Detect drift from IF NOT EXISTS)
-- =============================================================================
-- This query verifies all expected tables exist with their expected columns.
-- If any table/column is missing, this will raise an error.
--
-- Run this AFTER migration to verify schema integrity, especially when
-- migrating existing databases where IF NOT EXISTS might have skipped tables.
-- =============================================================================

DO $$
DECLARE
    missing_tables TEXT[];
    expected_tables TEXT[] := ARRAY[
        'cc_variants',
        'cc_modules',
        'cc_variant_modules',
        'cc_experiment_runs',
        'cc_run_metrics',
        'cc_run_metrics_by_asset',
        'cc_experiment_trades',
        'cc_trade_diagnostics',
        'cc_shadow_positions',
        'cc_pending_signals',
        'cc_change_ledger'
    ];
    tbl TEXT;
BEGIN
    -- Check each expected table exists
    FOREACH tbl IN ARRAY expected_tables
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = tbl
        ) THEN
            missing_tables := array_append(missing_tables, tbl);
        END IF;
    END LOOP;

    -- Raise error if any tables missing
    IF array_length(missing_tables, 1) > 0 THEN
        RAISE EXCEPTION 'CC Migration verification FAILED. Missing tables: %', missing_tables;
    END IF;

    RAISE NOTICE 'CC Migration verification PASSED. All 11 tables present.';
END $$;


-- =============================================================================
-- RELEASE ADVISORY LOCK
-- =============================================================================
-- The lock acquired at migration start is released here.
-- Note: Lock is also auto-released when the session/connection closes.
-- =============================================================================
SELECT pg_advisory_unlock(20260203);


-- =============================================================================
-- END OF MIGRATION
-- =============================================================================
-- To apply: psql -U trading -d trading -f this_file.sql
--
-- REMINDER: This file lives in Backend/docs/db/migrations/, NOT in app code.
--
-- POST-MIGRATION CHECKLIST:
-- [ ] Verify "CC Migration verification PASSED" message in output
-- [ ] If migrating existing DB, manually verify column sets match expected
-- [ ] Consider revoking CREATE from app role:
--     REVOKE CREATE ON SCHEMA public FROM trading_app_role;
-- =============================================================================
