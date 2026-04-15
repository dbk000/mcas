-- GX Research sandbox tables. Prefixed `gx_` so they cannot collide with live tables.
-- Idempotent: safe to re-run. Uses pg_advisory_lock per root CLAUDE.md §3.8.

SELECT pg_advisory_lock(0x6778);  -- 'gx' in hex

CREATE TABLE IF NOT EXISTS gx_opportunities (
    id              BIGSERIAL PRIMARY KEY,
    batch_id        TEXT NOT NULL,
    stage           TEXT NOT NULL,                 -- 'A' .. 'F'
    gx_family       TEXT NOT NULL,                 -- 'GX1' .. 'GX5'
    symbol          TEXT NOT NULL,                 -- USDC canonical name
    data_symbol     TEXT NOT NULL,                 -- actual OHLCV symbol queried (often USDT alias)
    timeframe       TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('long','short')),
    bar_start_ts    TIMESTAMPTZ NOT NULL,
    entry_price     DOUBLE PRECISION NOT NULL,
    trigger_params  JSONB NOT NULL,
    config_hash     TEXT NOT NULL,
    created_ts      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gx_opps_batch       ON gx_opportunities (batch_id);
CREATE INDEX IF NOT EXISTS idx_gx_opps_cfg         ON gx_opportunities (config_hash);
CREATE INDEX IF NOT EXISTS idx_gx_opps_sym_tf_ts   ON gx_opportunities (symbol, timeframe, bar_start_ts);

CREATE TABLE IF NOT EXISTS gx_sweep_cells (
    cell_id                      TEXT PRIMARY KEY,
    batch_id                     TEXT NOT NULL,
    stage                        TEXT NOT NULL,
    parent_cell_id               TEXT,
    gx_family                    TEXT NOT NULL,
    timeframe                    TEXT NOT NULL,
    direction                    TEXT NOT NULL,
    trigger_params               JSONB NOT NULL,
    exit_config_id               TEXT,
    exit_params                  JSONB,
    stop_params                  JSONB,
    filter_params                JSONB,
    regime_params                JSONB,
    cross_tf_params              JSONB,
    n_trades                     INTEGER,
    pf                           DOUBLE PRECISION,
    win_rate                     DOUBLE PRECISION,
    avg_r                        DOUBLE PRECISION,
    avg_daily_pf                 DOUBLE PRECISION,
    dsr                          DOUBLE PRECISION,
    pct_stop_inside_entry_range  DOUBLE PRECISION,  -- Stage-A mechanical-failure diagnostic
    gate_result                  TEXT,               -- 'PASS' / 'FAIL' / 'PARK'
    gate_reason                  TEXT,
    tier_pf                      JSONB,              -- {"T1": 1.2, "T2": 0.9, ...}
    config_hash                  TEXT NOT NULL,
    utc_ts                       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gx_sweep_batch_stage ON gx_sweep_cells (batch_id, stage);
CREATE INDEX IF NOT EXISTS idx_gx_sweep_parent      ON gx_sweep_cells (parent_cell_id);
CREATE INDEX IF NOT EXISTS idx_gx_sweep_gate        ON gx_sweep_cells (stage, gate_result);

CREATE TABLE IF NOT EXISTS gx_sim_results (
    id             BIGSERIAL PRIMARY KEY,
    cell_id        TEXT NOT NULL REFERENCES gx_sweep_cells(cell_id) ON DELETE CASCADE,
    symbol         TEXT NOT NULL,
    data_symbol    TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    direction      TEXT NOT NULL,
    entry_ts       TIMESTAMPTZ NOT NULL,
    exit_ts        TIMESTAMPTZ,
    entry_price    DOUBLE PRECISION NOT NULL,
    exit_price     DOUBLE PRECISION,
    stop_price     DOUBLE PRECISION,
    tp1_price      DOUBLE PRECISION,
    tp2_price      DOUBLE PRECISION,
    exit_type      TEXT,              -- 'STOP' / 'TP1' / 'TP2' / 'TIME' / 'TRAIL' / 'BE'
    r_multiple     DOUBLE PRECISION,
    pnl_gross_bps  DOUBLE PRECISION,
    pnl_net_bps    DOUBLE PRECISION,  -- after fees + slippage
    bars_held      INTEGER,
    slip_bps_rt    DOUBLE PRECISION,
    liquidity_tier TEXT
);
CREATE INDEX IF NOT EXISTS idx_gx_sim_cell ON gx_sim_results (cell_id);
CREATE INDEX IF NOT EXISTS idx_gx_sim_entry_ts ON gx_sim_results (entry_ts);

SELECT pg_advisory_unlock(0x6778);
