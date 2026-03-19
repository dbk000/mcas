-- =============================================================================
-- Migration: 20260319_active_strategies_scaffold.sql
-- Adds config_active_strategies reference table and rewrites
-- v_unified_opportunity_stats to always show all active strategies
-- even when they have zero opportunities in a given window.
--
-- Idempotent: IF NOT EXISTS for table, CREATE OR REPLACE for view.
-- =============================================================================

SELECT pg_advisory_lock(202603192);

-- ---------------------------------------------------------------------------
-- Reference table: all strategies being actively evaluated
-- ---------------------------------------------------------------------------
-- Source of truth: governance/bootstrap.py _populate_strategies() catalog.
-- To add/remove a strategy from the dashboard, INSERT/DELETE here.

CREATE TABLE IF NOT EXISTS config_active_strategies (
    setup_id    TEXT PRIMARY KEY,
    asset_class TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
);

-- Upsert all currently evaluated strategies
INSERT INTO config_active_strategies (setup_id, asset_class, status) VALUES
    ('S9',  'crypto',   'active'),
    ('S10', 'crypto',   'active'),
    ('U1',  'crypto',   'active'),
    ('D1',  'crypto',   'active'),
    ('D2',  'crypto',   'paper'),
    ('EQFP',         'equities', 'active'),
    ('EQ_TSMOM',     'equities', 'active'),
    ('EQ_XSMOM',     'equities', 'active'),
    ('EQ_REV1A',     'equities', 'active'),
    ('EQ_REV1B',     'equities', 'active'),
    ('EQ_VPD',       'equities', 'active'),
    ('EQ_ATC',       'equities', 'active'),
    ('EQ_NVDA_MACRO','equities', 'active')
ON CONFLICT (setup_id) DO UPDATE SET
    asset_class = EXCLUDED.asset_class,
    status = EXCLUDED.status;

-- ---------------------------------------------------------------------------
-- Rewrite v_unified_opportunity_stats with scaffold
-- ---------------------------------------------------------------------------
-- CROSS JOIN (windows × active_strategies) produces every cell.
-- LEFT JOIN actual stats so zero-opp strategies show as 0, not missing.

CREATE OR REPLACE VIEW v_unified_opportunity_stats AS
WITH windowed AS (
    SELECT
        p.*,
        CASE
            WHEN p.created_ts >= NOW() - INTERVAL '24 hours' THEN '24h'
            WHEN p.created_ts >= NOW() - INTERVAL '7 days'   THEN '7d'
            WHEN p.created_ts >= NOW() - INTERVAL '30 days'  THEN '30d'
        END AS min_window
    FROM v_unified_opportunity_pnl p
    WHERE p.created_ts >= NOW() - INTERVAL '30 days'
),
actual AS (
    SELECT
        w.time_window,
        p.asset_class,
        p.setup_id,

        COUNT(*)                                                    AS opp_count,
        COUNT(*) FILTER (WHERE p.is_closed)                         AS closed_count,
        COUNT(*) FILTER (WHERE NOT p.is_closed)                     AS open_count,

        ROUND(AVG(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::NUMERIC, 3)                 AS avg_pnl_pct,
        ROUND(SUM(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::NUMERIC, 3)                 AS total_pnl_pct,
        ROUND(MAX(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::NUMERIC, 3)                 AS max_pnl_pct,
        ROUND(MIN(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::NUMERIC, 3)                 AS min_pnl_pct,
        ROUND(STDDEV(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::NUMERIC, 3)                 AS pnl_stddev,

        ROUND(AVG(p.pnl_pct_net * 10.0)
            FILTER (WHERE p.is_closed)::NUMERIC, 2)                 AS avg_pnl_usd_per1k,
        ROUND(SUM(p.pnl_pct_net * 10.0)
            FILTER (WHERE p.is_closed)::NUMERIC, 2)                 AS total_pnl_usd_per1k,

        ROUND(AVG(p.r_multiple)
            FILTER (WHERE p.is_closed)::NUMERIC, 3)                 AS avg_r_multiple,

        ROUND(
            COUNT(*) FILTER (WHERE p.is_closed AND p.pnl_pct_net > 0)::NUMERIC
            / NULLIF(COUNT(*) FILTER (WHERE p.is_closed), 0),
            3
        )                                                           AS win_rate,

        ROUND((
            (SUM(p.pnl_pct_net) FILTER (WHERE p.is_closed AND p.pnl_pct_net > 0))
            / NULLIF(
                ABS(SUM(p.pnl_pct_net) FILTER (WHERE p.is_closed AND p.pnl_pct_net <= 0)),
                0
            )
        )::NUMERIC, 3)                                              AS profit_factor,

        ROUND(
            AVG(EXTRACT(EPOCH FROM (p.exit_ts - p.created_ts)) / 3600.0)
                FILTER (WHERE p.is_closed)::NUMERIC,
            1
        )                                                           AS avg_hours_held,

        ROUND(AVG(p.score)::NUMERIC, 3)                             AS avg_score,

        COUNT(*) FILTER (WHERE p.direction = 'long')                AS long_count,
        COUNT(*) FILTER (WHERE p.direction = 'short')               AS short_count,

        COUNT(*) FILTER (WHERE p.exit_reason = 'STOP_LOSS')         AS stop_loss_count,
        COUNT(*) FILTER (WHERE p.exit_reason IN ('TIME_HORIZON', 'TIME_HORIZON_NO_CANDLES', 'TIME_STOP', 'HORIZON'))
                                                                    AS time_stop_count,
        COUNT(*) FILTER (WHERE p.exit_reason LIKE 'TP%'
                            OR p.exit_reason LIKE 'TAKE%')          AS tp_count,
        COUNT(*) FILTER (WHERE p.exit_reason = 'FAIL_FAST')         AS fail_fast_count,

        COUNT(DISTINCT p.symbol)                                    AS distinct_symbols

    FROM windowed p
    CROSS JOIN (VALUES ('24h'), ('7d'), ('30d')) AS w(time_window)
    WHERE p.min_window IS NOT NULL
      AND (
          (w.time_window = '24h' AND p.min_window = '24h')
       OR (w.time_window = '7d'  AND p.min_window IN ('24h', '7d'))
       OR (w.time_window = '30d' AND p.min_window IN ('24h', '7d', '30d'))
      )
    GROUP BY w.time_window, p.asset_class, p.setup_id
),
scaffold AS (
    SELECT
        w.time_window,
        s.asset_class,
        s.setup_id
    FROM config_active_strategies s
    CROSS JOIN (VALUES ('24h'), ('7d'), ('30d')) AS w(time_window)
)
SELECT
    sc.time_window,
    sc.asset_class,
    sc.setup_id,
    COALESCE(a.opp_count, 0)            AS opp_count,
    COALESCE(a.closed_count, 0)          AS closed_count,
    COALESCE(a.open_count, 0)            AS open_count,
    a.avg_pnl_pct,
    a.total_pnl_pct,
    a.max_pnl_pct,
    a.min_pnl_pct,
    a.pnl_stddev,
    a.avg_pnl_usd_per1k,
    a.total_pnl_usd_per1k,
    a.avg_r_multiple,
    a.win_rate,
    a.profit_factor,
    a.avg_hours_held,
    a.avg_score,
    COALESCE(a.long_count, 0)            AS long_count,
    COALESCE(a.short_count, 0)           AS short_count,
    COALESCE(a.stop_loss_count, 0)       AS stop_loss_count,
    COALESCE(a.time_stop_count, 0)       AS time_stop_count,
    COALESCE(a.tp_count, 0)              AS tp_count,
    COALESCE(a.fail_fast_count, 0)       AS fail_fast_count,
    COALESCE(a.distinct_symbols, 0)      AS distinct_symbols
FROM scaffold sc
LEFT JOIN actual a
    ON a.time_window = sc.time_window
   AND a.asset_class = sc.asset_class
   AND a.setup_id = sc.setup_id;

SELECT pg_advisory_unlock(202603192);
