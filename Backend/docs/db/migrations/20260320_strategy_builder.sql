-- =============================================================================
-- Migration: 20260320_strategy_builder.sql
-- Strategy Builder — user-defined signal composition
--
-- Creates:
--   user_strategies: User-defined strategy rules
--   user_opportunities: Signals fired by user strategies
--   Amends v_unified_opportunity_pnl with user_custom CTE
--
-- Idempotent: CREATE IF NOT EXISTS, CREATE OR REPLACE for views.
-- =============================================================================

SELECT pg_advisory_lock(20260320);

-- ---------------------------------------------------------------------------
-- A: user_strategies — user-defined strategy rules
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_strategies (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    conditions JSONB NOT NULL,
    logic TEXT NOT NULL DEFAULT 'AND',
    timeframes TEXT[] NOT NULL,
    symbol_filter TEXT DEFAULT 'all_monitored',
    cooldown_minutes INT NOT NULL DEFAULT 60,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    auto_paused BOOLEAN NOT NULL DEFAULT FALSE,
    backtest_results JSONB,
    performance_stats JSONB,
    created_ts TIMESTAMPTZ DEFAULT NOW(),
    updated_ts TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_us_user ON user_strategies (user_id);
CREATE INDEX IF NOT EXISTS idx_us_active ON user_strategies (active) WHERE active = TRUE;

-- ---------------------------------------------------------------------------
-- B: user_opportunities — signals fired by user strategies
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_opportunities (
    id SERIAL PRIMARY KEY,
    strategy_id INT NOT NULL REFERENCES user_strategies(id),
    user_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT,
    triggered_ts TIMESTAMPTZ NOT NULL,
    conditions_snapshot JSONB NOT NULL,
    notified BOOLEAN DEFAULT FALSE,
    created_ts TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_uo_user_ts ON user_opportunities (user_id, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_uo_strategy ON user_opportunities (strategy_id, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_uo_cooldown ON user_opportunities (strategy_id, symbol, timeframe, created_ts DESC);

-- ---------------------------------------------------------------------------
-- C: Amend v_unified_opportunity_pnl with user_custom CTE
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_unified_opportunity_pnl AS

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
        o.trace_json->>'config_hash'                                    AS config_hash,
        (o.trace_json->>'config_hash') = cas.current_config_hash        AS is_current_config
    FROM opportunities o
    CROSS JOIN LATERAL (SELECT o.payload_json::JSONB AS pj) parsed
    LEFT JOIN config_fee_schedule fs ON fs.timeframe = o.timeframe
    LEFT JOIN config_active_strategies cas ON cas.setup_id = o.setup_id
    WHERE o.batch_id IS NULL
      AND o.setup_id NOT LIKE 'EQ%'
),

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
        NULL::TEXT                                                      AS config_hash,
        NULL::BOOLEAN                                                   AS is_current_config
    FROM paper_positions pp
    LEFT JOIN config_fee_schedule fs ON fs.timeframe = pp.timeframe
    WHERE pp.setup_id = 'D2'
),

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
        NULL::TEXT                                                      AS config_hash,
        NULL::BOOLEAN                                                   AS is_current_config
    FROM d2_cluster_events dce
    WHERE dce.status != 'COMPLETED'
),

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
        NULL::TEXT                                                      AS config_hash,
        NULL::BOOLEAN                                                   AS is_current_config
    FROM equities_opportunities eo
    JOIN equities_predictions ep ON ep.id = eo.prediction_id
    LEFT JOIN equities_opportunity_outcomes eoo ON eoo.opportunity_id = eo.id
    LEFT JOIN config_fee_schedule fs ON fs.timeframe = 'eq_default'
),

user_custom AS (
    SELECT
        uo.id::TEXT                                                 AS opp_id,
        'user_custom'::TEXT                                         AS source,
        'crypto'::TEXT                                              AS asset_class,
        'USR_' || uo.strategy_id                                    AS setup_id,
        uo.symbol,
        uo.timeframe,
        uo.created_ts,
        uo.direction,
        NULL::DOUBLE PRECISION                                      AS score,
        NULL::DOUBLE PRECISION                                      AS entry_price,
        NULL::DOUBLE PRECISION                                      AS exit_price,
        NULL::TEXT                                                   AS exit_reason,
        NULL::TIMESTAMPTZ                                           AS exit_ts,
        FALSE                                                       AS is_closed,
        NULL::DOUBLE PRECISION                                      AS pnl_pct_net,
        NULL::DOUBLE PRECISION                                      AS pnl_usd_actual,
        NULL::DOUBLE PRECISION                                      AS r_multiple,
        NULL::INT                                                   AS bars_held,
        NULL::TEXT                                                      AS config_hash,
        NULL::BOOLEAN                                                   AS is_current_config
    FROM user_opportunities uo
)

SELECT * FROM crypto
UNION ALL
SELECT * FROM d2_positions
UNION ALL
SELECT * FROM d2_clusters
UNION ALL
SELECT * FROM equities
UNION ALL
SELECT * FROM user_custom;

SELECT pg_advisory_unlock(20260320);
