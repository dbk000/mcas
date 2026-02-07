-- Migration: Partition signal_events by month
-- Canon §1.9 + P2-5: Monthly partitioning for efficient retention
-- Date: 2026-02-07
-- Apply AFTER 20260207_add_signal_events.sql

SELECT pg_advisory_lock(202602074);

-- PostgreSQL cannot convert an existing table to partitioned in-place.
-- Strategy: rename old table, create partitioned replacement, migrate data.

-- Step 1: Check if already partitioned (idempotent)
DO $$
BEGIN
    -- If the table is already partitioned, skip
    IF EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_partitioned_table pt ON c.oid = pt.partrelid
        WHERE c.relname = 'signal_events'
    ) THEN
        RAISE NOTICE 'signal_events is already partitioned — skipping migration';
        RETURN;
    END IF;

    -- Step 2: Rename existing table
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'signal_events') THEN
        ALTER TABLE signal_events RENAME TO signal_events_old;
        RAISE NOTICE 'Renamed signal_events → signal_events_old';
    END IF;

    -- Step 3: Create partitioned table with same schema
    CREATE TABLE IF NOT EXISTS signal_events (
        id BIGSERIAL,
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
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (id, created_at)
    ) PARTITION BY RANGE (created_at);

    RAISE NOTICE 'Created partitioned signal_events table';

    -- Step 4: Create partitions (current month + next 3 months)
    CREATE TABLE IF NOT EXISTS signal_events_2026_01
        PARTITION OF signal_events
        FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

    CREATE TABLE IF NOT EXISTS signal_events_2026_02
        PARTITION OF signal_events
        FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

    CREATE TABLE IF NOT EXISTS signal_events_2026_03
        PARTITION OF signal_events
        FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

    CREATE TABLE IF NOT EXISTS signal_events_2026_04
        PARTITION OF signal_events
        FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

    -- Step 5: Create default partition for out-of-range data
    CREATE TABLE IF NOT EXISTS signal_events_default
        PARTITION OF signal_events DEFAULT;

    -- Step 6: Recreate indexes on partitioned table
    CREATE INDEX IF NOT EXISTS idx_signal_events_symbol_ts
        ON signal_events(symbol, timeframe, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_signal_events_type
        ON signal_events(event_type, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_signal_events_setup
        ON signal_events(setup_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_signal_events_bar
        ON signal_events(bar_start_ts DESC);

    -- Step 7: Migrate existing data
    INSERT INTO signal_events (
        id, event_type, symbol, timeframe, setup_id, direction,
        bar_start_ts, as_of_ts, setup_score, rank,
        feature_snapshot, score_components, reason_codes,
        trade_id, run_id, created_at
    )
    SELECT
        id, event_type, symbol, timeframe, setup_id, direction,
        bar_start_ts, as_of_ts, setup_score, rank,
        feature_snapshot, score_components, reason_codes,
        trade_id, run_id, created_at
    FROM signal_events_old;

    -- Step 8: Sync sequence
    PERFORM setval(
        pg_get_serial_sequence('signal_events', 'id'),
        COALESCE((SELECT MAX(id) FROM signal_events), 0) + 1,
        false
    );

    -- Step 9: Drop old table
    DROP TABLE IF EXISTS signal_events_old;

    RAISE NOTICE 'Data migrated and old table dropped';

    -- Add comment
    COMMENT ON TABLE signal_events IS
        'Canon §1.9: Evented audit logs, partitioned by month. '
        'Retention: 30 days detail. Drop old partitions for cleanup. '
        'Create new monthly partitions before each month starts.';
END $$;

-- Track migration
INSERT INTO schema_versions (filename, applied_at) VALUES
    ('20260207_partition_signal_events.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

-- Verify
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'signal_events') THEN
        RAISE EXCEPTION 'signal_events table not found after migration';
    END IF;
    RAISE NOTICE 'signal_events partitioning migration complete';
END $$;

SELECT pg_advisory_unlock(202602074);
