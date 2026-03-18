-- =============================================================================
-- Migration: 20260319_unified_go_live_views.sql
-- Unified Go-Live Views — all signal sources in one place
--
-- Creates:
--   A: config_fee_schedule bar_duration_s column + eq_default row
--   B: v_unified_opportunity_pnl (UNION ALL of crypto, D2, D2 clusters, equities)
--   C: v_unified_opportunity_stats (aggregated rollup by window/asset_class/setup_id)
--   D: v_d2_funnel_stats (D2-specific funnel metrics)
--
-- Idempotent: ALTER ADD IF NOT EXISTS, CREATE OR REPLACE for views.
-- Non-destructive: no DROP of existing views.
-- =============================================================================

SELECT pg_advisory_lock(20260319);

-- ---------------------------------------------------------------------------
-- A: Extend config_fee_schedule with bar_duration_s + equities default
-- ---------------------------------------------------------------------------

ALTER TABLE config_fee_schedule ADD COLUMN IF NOT EXISTS bar_duration_s INT;

UPDATE config_fee_schedule SET bar_duration_s = CASE timeframe
    WHEN '1m' THEN 60 WHEN '5m' THEN 300 WHEN '15m' THEN 900
    WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400
    END
WHERE bar_duration_s IS NULL;

INSERT INTO config_fee_schedule (timeframe, slippage_bps, fee_bps, bar_duration_s) VALUES
    ('eq_default', 0.0, 6.0, 86400)
ON CONFLICT (timeframe) DO UPDATE SET
    slippage_bps = EXCLUDED.slippage_bps,
    fee_bps = EXCLUDED.fee_bps,
    bar_duration_s = EXCLUDED.bar_duration_s;

-- ---------------------------------------------------------------------------
-- B: v_unified_opportunity_pnl — per-opportunity detail across all sources
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
        END                                                         AS bars_held
    FROM opportunities o
    CROSS JOIN LATERAL (SELECT o.payload_json::JSONB AS pj) parsed
    LEFT JOIN config_fee_schedule fs ON fs.timeframe = o.timeframe
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
        END                                                         AS bars_held
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
        NULL::INT                                                   AS bars_held
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
        END                                                         AS bars_held
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
-- C: v_unified_opportunity_stats — aggregated rollup (24h, 7d, 30d only)
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
per_window AS (
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
)
SELECT * FROM per_window;

-- ---------------------------------------------------------------------------
-- D: v_d2_funnel_stats — D2-specific funnel metrics
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_d2_funnel_stats AS
WITH windowed AS (
    SELECT
        dce.*,
        CASE
            WHEN dce.cluster_start_ts >= NOW() - INTERVAL '24 hours' THEN '24h'
            WHEN dce.cluster_start_ts >= NOW() - INTERVAL '7 days'   THEN '7d'
            WHEN dce.cluster_start_ts >= NOW() - INTERVAL '30 days'  THEN '30d'
        END AS min_window
    FROM d2_cluster_events dce
    WHERE dce.cluster_start_ts >= NOW() - INTERVAL '30 days'
)
SELECT
    w.time_window,
    COUNT(*)                                                        AS clusters_detected,
    COUNT(*) FILTER (WHERE p.status = 'GATED_OUT')                  AS clusters_gated_out,
    COUNT(*) FILTER (WHERE p.status = 'TRACKING')                   AS clusters_tracking,
    COUNT(*) FILTER (WHERE p.status = 'ENTERED')                    AS clusters_entered,
    COUNT(*) FILTER (WHERE p.status = 'COMPLETED')                  AS clusters_completed,
    COUNT(*) FILTER (WHERE p.status = 'EXPIRED')                    AS clusters_expired,
    COUNT(*) FILTER (WHERE p.gate_reason LIKE '%perp%')             AS gated_no_perp,
    COUNT(*) FILTER (WHERE p.gate_reason LIKE '%mania%')            AS gated_mania,
    COUNT(*) FILTER (WHERE p.gate_reason LIKE '%liquid%')           AS gated_liquidity,
    ROUND(AVG(p.max_pct_change)::NUMERIC, 2)                        AS avg_pump_pct,
    ROUND(AVG(p.alert_count)::NUMERIC, 1)                           AS avg_alert_count
FROM windowed p
CROSS JOIN (VALUES ('24h'), ('7d'), ('30d')) AS w(time_window)
WHERE p.min_window IS NOT NULL
  AND (
      (w.time_window = '24h' AND p.min_window = '24h')
   OR (w.time_window = '7d'  AND p.min_window IN ('24h', '7d'))
   OR (w.time_window = '30d' AND p.min_window IN ('24h', '7d', '30d'))
  )
GROUP BY w.time_window;

SELECT pg_advisory_unlock(20260319);
