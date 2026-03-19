-- =============================================================================
-- Migration: 20260319_edge_reeval_and_dashboard.sql
-- Edge Re-evaluation & Dashboard Enrichment
--
-- Changes:
--   1. Add current_config_hash TEXT to config_active_strategies
--   2. Enrich v_unified_opportunity_pnl with config_hash + is_current_config
--   3. Enrich v_unified_opportunity_stats with current_config_count,
--      live_24h_count, live_7d_count
--
-- Idempotent: ALTER ADD IF NOT EXISTS, CREATE OR REPLACE for views.
-- =============================================================================

SELECT pg_advisory_lock(202603193);

-- ---------------------------------------------------------------------------
-- 1: Add current_config_hash to config_active_strategies
-- ---------------------------------------------------------------------------

ALTER TABLE config_active_strategies
    ADD COLUMN IF NOT EXISTS current_config_hash TEXT;

-- ---------------------------------------------------------------------------
-- 2: Enrich v_unified_opportunity_pnl with config_hash + is_current_config
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_unified_opportunity_pnl AS

-- CTE 1: Crypto (from opportunities, live only)
WITH crypto AS (
    SELECT
        o.id::TEXT                                                  AS opp_id,
        'crypto'::TEXT                                              AS source,
        'crypto'::TEXT                                              AS asset_class,
        o.setup_id,
        o.symbol,
        o.timeframe,
        o.created_ts,
        LOWER(pj->>'direction')                                     AS direction,
        o.score,
        (pj->>'entry_price')::DOUBLE PRECISION                      AS entry_price,
        (pj->>'exit_price')::DOUBLE PRECISION                       AS exit_price,
        (pj->>'exit_reason')::TEXT                                  AS exit_reason,
        (pj->>'exit_ts')::TIMESTAMPTZ                               AS exit_ts,
        (pj->>'exit_ts') IS NOT NULL                                AS is_closed,
        CASE
            WHEN (pj->>'exit_price')::DOUBLE PRECISION IS NOT NULL
                 AND (pj->>'entry_price')::DOUBLE PRECISION > 0 THEN
                CASE LOWER(pj->>'direction')
                    WHEN 'long'  THEN ((pj->>'exit_price')::DOUBLE PRECISION - (pj->>'entry_price')::DOUBLE PRECISION) / (pj->>'entry_price')::DOUBLE PRECISION * 100.0
                    WHEN 'short' THEN ((pj->>'entry_price')::DOUBLE PRECISION - (pj->>'exit_price')::DOUBLE PRECISION) / (pj->>'entry_price')::DOUBLE PRECISION * 100.0
                END
                - COALESCE(fs.roundtrip_cost_pct, 0.09)
        END                                                         AS pnl_pct_net,
        NULL::DOUBLE PRECISION                                      AS pnl_usd_actual,
        NULL::DOUBLE PRECISION                                      AS r_multiple,
        CASE
            WHEN (pj->>'exit_ts')::TIMESTAMPTZ IS NOT NULL AND fs.bar_duration_s > 0 THEN
                GREATEST(1, EXTRACT(EPOCH FROM ((pj->>'exit_ts')::TIMESTAMPTZ - o.created_ts)) / fs.bar_duration_s)::INT
        END                                                         AS bars_held,
        -- New columns: config lineage
        (o.trace_json->>'config_hash')::TEXT                        AS config_hash,
        (o.trace_json->>'config_hash' = cas.current_config_hash)    AS is_current_config
    FROM opportunities o
    CROSS JOIN LATERAL (SELECT o.payload_json::JSONB AS pj) parsed
    LEFT JOIN config_fee_schedule fs ON fs.timeframe = o.timeframe
    LEFT JOIN config_active_strategies cas ON cas.setup_id = o.setup_id
    WHERE o.batch_id IS NULL
      AND o.setup_id NOT LIKE 'EQ%'
),

-- CTE 2: D2 paper positions (OPEN + CLOSED)
d2_positions AS (
    SELECT
        pp.id::TEXT                                                 AS opp_id,
        'd2_paper'::TEXT                                            AS source,
        'crypto'::TEXT                                              AS asset_class,
        'D2'::TEXT                                                  AS setup_id,
        pp.symbol,
        pp.timeframe,
        COALESCE(pp.entry_ts, pp.signal_ts, pp.created_at)          AS created_ts,
        pp.side                                                     AS direction,
        NULL::DOUBLE PRECISION                                      AS score,
        pp.entry_price,
        pp.exit_price,
        pp.exit_reason,
        pp.exit_ts,
        pp.status = 'CLOSED'                                        AS is_closed,
        CASE WHEN pp.status = 'CLOSED' AND pp.realized_pnl IS NOT NULL AND pp.notional_value > 0 THEN
            pp.realized_pnl / pp.notional_value * 100.0
            - COALESCE(fs.roundtrip_cost_pct, 0.09)
        END                                                         AS pnl_pct_net,
        pp.realized_pnl                                             AS pnl_usd_actual,
        pp.r_multiple,
        CASE WHEN pp.status = 'CLOSED' AND pp.exit_ts IS NOT NULL THEN
            GREATEST(1, EXTRACT(EPOCH FROM (pp.exit_ts - COALESCE(pp.entry_ts, pp.signal_ts))) / COALESCE(fs.bar_duration_s, 3600))::INT
        END                                                         AS bars_held,
        NULL::TEXT                                                  AS config_hash,
        NULL::BOOLEAN                                               AS is_current_config
    FROM paper_positions pp
    LEFT JOIN config_fee_schedule fs ON fs.timeframe = pp.timeframe
    WHERE pp.setup_id = 'D2'
),

-- CTE 3: D2 cluster funnel (non-COMPLETED clusters — COMPLETED have matching paper_positions)
d2_clusters AS (
    SELECT
        dce.id::TEXT                                                AS opp_id,
        'd2_cluster'::TEXT                                          AS source,
        'crypto'::TEXT                                              AS asset_class,
        'D2'::TEXT                                                  AS setup_id,
        dce.symbol,
        '1h'::TEXT                                                  AS timeframe,
        dce.cluster_start_ts                                        AS created_ts,
        'short'::TEXT                                               AS direction,
        NULL::DOUBLE PRECISION                                      AS score,
        dce.first_price                                             AS entry_price,
        NULL::DOUBLE PRECISION                                      AS exit_price,
        COALESCE(dce.gate_reason, dce.status)                       AS exit_reason,
        NULL::TIMESTAMPTZ                                           AS exit_ts,
        dce.status IN ('GATED_OUT', 'EXPIRED', 'CANCELLED')         AS is_closed,
        NULL::DOUBLE PRECISION                                      AS pnl_pct_net,
        NULL::DOUBLE PRECISION                                      AS pnl_usd_actual,
        NULL::DOUBLE PRECISION                                      AS r_multiple,
        NULL::INT                                                   AS bars_held,
        NULL::TEXT                                                  AS config_hash,
        NULL::BOOLEAN                                               AS is_current_config
    FROM d2_cluster_events dce
    WHERE dce.status != 'COMPLETED'
),

-- CTE 4: Equities
equities AS (
    SELECT
        eo.id::TEXT                                                 AS opp_id,
        'equities'::TEXT                                            AS source,
        'equities'::TEXT                                            AS asset_class,
        ep.model_name                                               AS setup_id,
        eo.symbol,
        eo.timeframe,
        eo.created_at                                               AS created_ts,
        COALESCE(
            eoo.direction,
            CASE
                WHEN eo.expected_return > 0 THEN 'long'
                WHEN eo.expected_return < 0 THEN 'short'
                ELSE 'neutral'
            END
        )                                                           AS direction,
        eo.confidence                                               AS score,
        NULL::DOUBLE PRECISION                                      AS entry_price,
        NULL::DOUBLE PRECISION                                      AS exit_price,
        CASE WHEN eoo.id IS NOT NULL THEN 'HORIZON' ELSE NULL END   AS exit_reason,
        eoo.settle_bar_ts                                           AS exit_ts,
        eoo.id IS NOT NULL                                          AS is_closed,
        CASE WHEN eoo.id IS NOT NULL THEN
            eoo.directional_realized_return * 100.0
            - COALESCE(fs.roundtrip_cost_pct, 0.06)
        END                                                         AS pnl_pct_net,
        NULL::DOUBLE PRECISION                                      AS pnl_usd_actual,
        NULL::DOUBLE PRECISION                                      AS r_multiple,
        CASE eo.horizon
            WHEN '1d' THEN 1 WHEN '3d' THEN 3 WHEN '5d' THEN 5
            WHEN '10d' THEN 10 WHEN '20d' THEN 20
            ELSE NULL
        END                                                         AS bars_held,
        NULL::TEXT                                                  AS config_hash,
        NULL::BOOLEAN                                               AS is_current_config
    FROM equities_opportunities eo
    JOIN equities_predictions ep ON ep.id = eo.prediction_id
    LEFT JOIN equities_opportunity_outcomes eoo ON eoo.opportunity_id = eo.id
    LEFT JOIN config_fee_schedule fs ON fs.timeframe = 'eq_default'
)

SELECT * FROM crypto
UNION ALL
SELECT * FROM d2_positions
UNION ALL
SELECT * FROM d2_clusters
UNION ALL
SELECT * FROM equities;

-- ---------------------------------------------------------------------------
-- 3: Enrich v_unified_opportunity_stats with current_config_count,
--    live_24h_count, live_7d_count
-- ---------------------------------------------------------------------------

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

        COUNT(DISTINCT p.symbol)                                    AS distinct_symbols,

        -- New: count of opps matching current config hash
        COUNT(*) FILTER (WHERE p.is_current_config)                 AS current_config_count

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
-- Live activity counts (independent of time_window)
live_activity AS (
    SELECT
        p.asset_class,
        p.setup_id,
        COUNT(*) FILTER (WHERE p.created_ts >= NOW() - INTERVAL '24 hours') AS live_24h_count,
        COUNT(*) FILTER (WHERE p.created_ts >= NOW() - INTERVAL '7 days')   AS live_7d_count,
        ROUND(SUM(p.pnl_pct_net) FILTER (WHERE p.is_closed AND p.created_ts >= NOW() - INTERVAL '24 hours')::NUMERIC, 3) AS pnl_24h,
        ROUND(SUM(p.pnl_pct_net) FILTER (WHERE p.is_closed AND p.created_ts >= NOW() - INTERVAL '7 days')::NUMERIC, 3)   AS pnl_7d
    FROM v_unified_opportunity_pnl p
    WHERE p.created_ts >= NOW() - INTERVAL '7 days'
    GROUP BY p.asset_class, p.setup_id
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
    COALESCE(a.distinct_symbols, 0)      AS distinct_symbols,
    COALESCE(a.current_config_count, 0)  AS current_config_count,
    COALESCE(la.live_24h_count, 0)       AS live_24h_count,
    COALESCE(la.live_7d_count, 0)        AS live_7d_count,
    la.pnl_24h,
    la.pnl_7d
FROM scaffold sc
LEFT JOIN actual a
    ON a.time_window = sc.time_window
   AND a.asset_class = sc.asset_class
   AND a.setup_id = sc.setup_id
LEFT JOIN live_activity la
    ON la.asset_class = sc.asset_class
   AND la.setup_id = sc.setup_id;

SELECT pg_advisory_unlock(202603193);
