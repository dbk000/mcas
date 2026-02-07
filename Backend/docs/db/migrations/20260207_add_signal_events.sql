-- =============================================================================
-- Migration: Add signal_events table (Canon §1.9 — Evented Audit Logs)
-- =============================================================================
-- Purpose: Persist evented feature/signal logs at key decision events
-- (setup armed/fired/invalidated, threshold cross, rank changes).
--
-- This satisfies Canon §1.9 and Corrections Log entry
-- "Persist Audit Trail vs. Ephemeral Data".
--
-- Apply: psql -U trading -d trading -f 20260207_add_signal_events.sql
-- DO NOT execute from application code.
-- =============================================================================

-- Advisory lock to prevent concurrent migration runs
SELECT pg_advisory_lock(20260207);

-- =============================================================================
-- signal_events: evented feature/signal audit log
-- =============================================================================
CREATE TABLE IF NOT EXISTS signal_events (
    id BIGSERIAL PRIMARY KEY,

    -- Event identity
    event_type TEXT NOT NULL CHECK (event_type IN (
        'SETUP_ARMED',        -- Setup preconditions met (Tier 3 state active)
        'SETUP_FIRED',        -- Signal triggered (Tier 4)
        'SETUP_INVALIDATED',  -- Signal conditions no longer met
        'THRESHOLD_CROSS',    -- Key indicator crossed threshold
        'RANK_CHANGE',        -- Signal entered/left top-N
        'ENTRY_DECISION',     -- Trade entry decided
        'EXIT_DECISION',      -- Trade exit decided
        'STATE_FLIP'          -- Post-entry state change affecting guidance
    )),

    -- Context
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    setup_id TEXT NOT NULL,
    direction TEXT,                           -- 'long', 'short', or NULL

    -- Candle identity (Canon §4A)
    bar_start_ts TIMESTAMPTZ NOT NULL,        -- Which candle triggered this event
    as_of_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- When the system processed it

    -- Signal state
    setup_score FLOAT,                        -- Score at event time
    rank INT,                                 -- Rank at event time (NULL if not ranked)

    -- Minimal payload for decision reproduction (Canon §1.9)
    -- Key feature values and score components used for this decision
    feature_snapshot JSONB,                   -- {indicator_id: value, ...}
    score_components JSONB,                   -- {component: weight, ...}
    reason_codes TEXT[],                      -- Explainability items

    -- Reference to trade if applicable
    trade_id UUID,                            -- FK to run_trades or paper_positions

    -- Run context
    run_id UUID,                              -- FK to runs (NULL for live scans)

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_signal_events_symbol_ts
    ON signal_events(symbol, timeframe, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_type
    ON signal_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_setup
    ON signal_events(setup_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_bar
    ON signal_events(bar_start_ts DESC);

-- Retention policy: partition by month for easy cleanup
-- (For now, use index-based cleanup; partitioning can be added later)
COMMENT ON TABLE signal_events IS
    'Canon §1.9: Evented audit logs for signal state transitions. '
    'Retention: 30 days detail, roll up to daily aggregates after. '
    'Bounded: cleanup via DELETE WHERE created_at < NOW() - INTERVAL ''30 days''.';

-- =============================================================================
-- Verification
-- =============================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_name = 'signal_events') THEN
        RAISE EXCEPTION 'signal_events table was not created';
    END IF;
    RAISE NOTICE 'Signal Events Migration verification PASSED';
END $$;

-- Release advisory lock
SELECT pg_advisory_unlock(20260207);
