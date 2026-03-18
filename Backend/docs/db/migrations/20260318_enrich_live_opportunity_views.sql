-- =============================================================================
-- Migration: 20260318_enrich_live_opportunity_views.sql
-- Enriches v_live_opportunity_pnl with S9 signal context fields and
-- v_live_opportunity_stats with concentration/risk metrics.
--
-- Idempotent: CREATE OR REPLACE VIEW only, no schema changes.
-- =============================================================================

SELECT pg_advisory_lock(202603182);

-- Must drop dependent view first, then recreate both (column order changed)
DROP VIEW IF EXISTS v_live_opportunity_stats CASCADE;
DROP VIEW IF EXISTS v_live_opportunity_pnl CASCADE;

-- ---------------------------------------------------------------------------
-- B2 (enriched): v_live_opportunity_pnl — adds ret_4h, breadth, rank, prices,
--   skip_reason, score_components from payload_json
-- ---------------------------------------------------------------------------

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
        (pj->>'exit_ts')::timestamptz                               AS exit_ts,
        -- S9 signal context
        (pj->'state'->>'ret_4h')::double precision                  AS ret_4h,
        (pj->'state'->>'breadth')::double precision                 AS breadth,
        (pj->'state'->>'rank')::int                                 AS rank,
        (pj->'state'->>'decile_size')::int                          AS decile_size,
        (pj->'state'->>'universe_size')::int                        AS universe_size,
        -- Price levels
        (pj->>'r_value')::double precision                          AS r_value,
        (pj->>'stop_price')::double precision                       AS stop_price,
        (pj->>'tp1_price')::double precision                        AS tp1_price,
        (pj->>'tp2_price')::double precision                        AS tp2_price,
        -- Skip reason
        (pj->>'skip_reason')::text                                  AS skip_reason,
        (pj->>'skip_reason') IS NOT NULL                            AS is_skip,
        -- Score components (S9)
        (pj->'score_components'->>'rank_score')::double precision   AS rank_score,
        (pj->'score_components'->>'breadth_score')::double precision AS breadth_score,
        (pj->'score_components'->>'magnitude_score')::double precision AS magnitude_score
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
-- B3 (enriched): v_live_opportunity_stats — adds concentration, magnitude,
--   breadth, skip, stddev metrics
-- ---------------------------------------------------------------------------

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
        ROUND(STDDEV(p.pnl_pct_net)
            FILTER (WHERE p.is_closed)::numeric, 3)                 AS pnl_stddev,

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
        COUNT(*) FILTER (WHERE p.exit_reason = 'FAIL_FAST')         AS fail_fast_count,

        -- Concentration
        COUNT(DISTINCT p.symbol)                                    AS distinct_symbols,

        -- S9-specific aggregates (NULL for non-S9)
        ROUND(AVG(p.ret_4h)::numeric, 4)                           AS avg_ret_4h,
        ROUND(AVG(p.breadth)::numeric, 3)                           AS avg_breadth,

        -- Skip count
        COUNT(*) FILTER (WHERE p.is_skip)                           AS skip_count

    FROM windowed p
    CROSS JOIN (VALUES ('24h'), ('7d'), ('30d'), ('all_time')) AS w(time_window)
    WHERE (w.time_window = '24h'      AND p.min_window = '24h')
       OR (w.time_window = '7d'       AND p.min_window IN ('24h', '7d'))
       OR (w.time_window = '30d'      AND p.min_window IN ('24h', '7d', '30d'))
       OR (w.time_window = 'all_time')
    GROUP BY w.time_window, p.setup_id
)
SELECT * FROM per_window;

SELECT pg_advisory_unlock(202603182);
