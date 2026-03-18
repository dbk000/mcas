-- =============================================================================
-- Migration: 20260318_live_opportunity_stats.sql
-- Track B of Live Ops Stats Plan
--
-- Creates:
--   B0: config_fee_schedule table (governed fee model, sourced from execution.py)
--   B1: idx_opps_live_created partial index (perf: avoids seq scan on 1.77GB table)
--   B2: v_live_opportunity_pnl view (per-opportunity PnL with fee-adjusted returns)
--   B3: v_live_opportunity_stats view (wide rollup: 24h/7d/30d/all_time windows)
--
-- Idempotent: IF NOT EXISTS for table/index, CREATE OR REPLACE for views.
-- Non-destructive: no ALTER, no DROP, no data mutation.
--
-- Source of truth for fee values: execution.py SLIPPAGE_PROFILE_BPS + DEFAULT_FEE_BPS=4.0
--   1m=20+4=24bps, 5m=15+4=19bps, 15m=10+4=14bps,
--   1h=5+4=9bps,   4h=3+4=7bps,   1d=2+4=6bps
-- =============================================================================

SELECT pg_advisory_lock(20260318);

-- ---------------------------------------------------------------------------
-- B0: Fee schedule table
-- ---------------------------------------------------------------------------
-- Governance: roundtrip_cost_pct is a generated column so it cannot drift
-- from the component values. If execution.py changes, re-run the INSERT to sync.

CREATE TABLE IF NOT EXISTS config_fee_schedule (
    timeframe           TEXT PRIMARY KEY,
    slippage_bps        DOUBLE PRECISION NOT NULL,
    fee_bps             DOUBLE PRECISION NOT NULL DEFAULT 4.0,
    roundtrip_cost_pct  DOUBLE PRECISION GENERATED ALWAYS AS
        ((slippage_bps + fee_bps) / 100.0) STORED
);

INSERT INTO config_fee_schedule (timeframe, slippage_bps) VALUES
    ('1m',  20.0),
    ('5m',  15.0),
    ('15m', 10.0),
    ('1h',   5.0),
    ('4h',   3.0),
    ('1d',   2.0)
ON CONFLICT (timeframe) DO UPDATE SET slippage_bps = EXCLUDED.slippage_bps;

-- ---------------------------------------------------------------------------
-- B1: Partial index for live opportunities
-- ---------------------------------------------------------------------------
-- Filters batch_id IS NULL (live only) — avoids seq scan on the full table.
-- NOT using CONCURRENTLY because we are inside an advisory lock transaction context.

CREATE INDEX IF NOT EXISTS idx_opps_live_created
    ON opportunities (created_ts DESC)
    WHERE batch_id IS NULL;

-- ---------------------------------------------------------------------------
-- B2: v_live_opportunity_pnl — per-opportunity PnL with fee-adjusted returns
-- ---------------------------------------------------------------------------
-- payload_json is TEXT, cast to jsonb per row. Long-term: ALTER to jsonb.
-- COALESCE default 0.09 = 5bps slippage + 4bps fee (1h default, most common TF).
-- GOVERNANCE: setup_id NOT LIKE 'EQ%' filters equities. All equities strategies
-- in StrategyRegistry follow EQ_* naming convention (verified: EQFP, EQ_ATC, EQ_VPD).
-- Long-term: replace with JOIN against strategy_metadata table.

CREATE OR REPLACE VIEW v_live_opportunity_pnl AS
WITH crypto_opps AS (
    SELECT
        o.id,
        'crypto'::text                                              AS asset_class,
        o.setup_id,
        o.symbol,
        o.timeframe,
        o.created_ts,
        o.status,
        o.score,
        LOWER(pj->>'direction')                                     AS direction,
        (pj->>'entry_price')::double precision                      AS entry_price,
        (pj->>'exit_price')::double precision                       AS exit_price,
        (pj->>'exit_reason')::text                                  AS exit_reason,
        (pj->>'exit_ts')::timestamptz                               AS exit_ts
    FROM opportunities o,
         LATERAL (SELECT o.payload_json::jsonb AS pj) parsed
    WHERE o.batch_id IS NULL
      AND o.setup_id NOT LIKE 'EQ%'
),
with_pnl AS (
    SELECT
        co.*,
        CASE
            WHEN co.exit_price IS NOT NULL AND co.entry_price > 0 THEN
                CASE co.direction
                    WHEN 'long'  THEN (co.exit_price - co.entry_price) / co.entry_price * 100.0
                    WHEN 'short' THEN (co.entry_price - co.exit_price) / co.entry_price * 100.0
                END
                - COALESCE(fs.roundtrip_cost_pct, 0.09)
        END AS pnl_pct_net,
        (co.exit_ts IS NOT NULL) AS is_closed
    FROM crypto_opps co
    LEFT JOIN config_fee_schedule fs ON fs.timeframe = co.timeframe
)
SELECT * FROM with_pnl;

-- ---------------------------------------------------------------------------
-- B3: v_live_opportunity_stats — wide rollup per window per setup_id
-- ---------------------------------------------------------------------------
-- Windows: 24h, 7d, 30d, all_time
-- Metrics: counts, PnL stats, win rate, profit factor, timing, score, direction
--          split, exit reason distribution.
-- Descoping a column from a VIEW is trivial; adding one later requires re-reading
-- the data model. The 60s TTL API cache amortises compute. Be generous.

CREATE OR REPLACE VIEW v_live_opportunity_stats AS
WITH windowed AS (
    SELECT
        p.*,
        CASE
            WHEN p.created_ts >= NOW() - INTERVAL '24 hours' THEN '24h'
            WHEN p.created_ts >= NOW() - INTERVAL '7 days'   THEN '7d'
            WHEN p.created_ts >= NOW() - INTERVAL '30 days'  THEN '30d'
            ELSE 'all_time'
        END AS min_window
    FROM v_live_opportunity_pnl p
),
per_window AS (
    SELECT
        w.time_window,
        p.setup_id,

        -- Counts
        COUNT(*)                                                    AS opp_count,
        COUNT(*) FILTER (WHERE p.is_closed)                         AS closed_count,
        COUNT(*) FILTER (WHERE NOT p.is_closed)                     AS open_count,

        -- PnL (closed only)
        ROUND(AVG(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::numeric, 3)                 AS avg_pnl_pct,
        ROUND(SUM(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::numeric, 3)                 AS total_pnl_pct,
        ROUND(MAX(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::numeric, 3)                 AS max_pnl_pct,
        ROUND(MIN(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::numeric, 3)                 AS min_pnl_pct,

        -- Win rate
        ROUND(
            COUNT(*) FILTER (WHERE p.is_closed AND p.pnl_pct_net > 0)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE p.is_closed), 0),
            3
        )                                                           AS win_rate,

        -- Profit factor = gross_wins / abs(gross_losses)
        ROUND((
            (SUM(p.pnl_pct_net) FILTER (WHERE p.is_closed AND p.pnl_pct_net > 0))
            / NULLIF(
                ABS(SUM(p.pnl_pct_net) FILTER (WHERE p.is_closed AND p.pnl_pct_net <= 0)),
                0
            )
        )::numeric, 3)                                              AS profit_factor,

        -- Timing
        ROUND(
            AVG(EXTRACT(EPOCH FROM (p.exit_ts - p.created_ts)) / 3600.0)
                FILTER (WHERE p.is_closed)::numeric,
            1
        )                                                           AS avg_hours_held,

        -- Score
        ROUND(AVG(p.score)::numeric, 3)                             AS avg_score,

        -- Direction split
        COUNT(*) FILTER (WHERE p.direction = 'long')                AS long_count,
        COUNT(*) FILTER (WHERE p.direction = 'short')               AS short_count,

        -- Exit reason distribution
        COUNT(*) FILTER (WHERE p.exit_reason = 'STOP_LOSS')         AS stop_loss_count,
        COUNT(*) FILTER (WHERE p.exit_reason IN ('TIME_HORIZON', 'TIME_HORIZON_NO_CANDLES'))
                                                                    AS time_stop_count,
        COUNT(*) FILTER (WHERE p.exit_reason LIKE 'TP%'
                            OR p.exit_reason LIKE 'TAKE%')          AS tp_count,
        COUNT(*) FILTER (WHERE p.exit_reason = 'FAIL_FAST')         AS fail_fast_count

    FROM windowed p
    CROSS JOIN (VALUES ('24h'), ('7d'), ('30d'), ('all_time')) AS w(time_window)
    WHERE (w.time_window = '24h'      AND p.min_window = '24h')
       OR (w.time_window = '7d'       AND p.min_window IN ('24h', '7d'))
       OR (w.time_window = '30d'      AND p.min_window IN ('24h', '7d', '30d'))
       OR (w.time_window = 'all_time')
    GROUP BY w.time_window, p.setup_id
)
SELECT * FROM per_window;

SELECT pg_advisory_unlock(20260318);
