-- Phase D (Enforcer-First Deployment, plan ENFORCER_FIRST_DEPLOYMENT_PLAN_20260412.md)
-- live_positions — single source of truth for live (non-paper) executed positions.
--
-- Tracks the lifecycle of every live order from idempotent submission (clientOrderId)
-- through fills, exits, and settlement. Mirrors the role of paper_positions but for
-- real-money execution under the Cap3 enforcer pipeline.
--
-- Notes on column types:
--   * opportunities.id is SERIAL (INTEGER), not BIGINT — opportunity_id matches that.
--   * id is BIGSERIAL because live_positions can outlive an opportunities table reset.
--   * client_order_id is the Binance clientOrderId (must be globally unique to make
--     submissions idempotent across worker restarts and retry loops).
--
-- Idempotent. Safe to re-run. Wrapped in pg_advisory_lock(20260413001).
--
-- See also: 20260408_paper_positions_canon_exit.sql, 20260408_paper_positions_telemetry.sql.

BEGIN;
SELECT pg_advisory_lock(20260413001);

CREATE TABLE IF NOT EXISTS live_positions (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    setup_id TEXT NOT NULL,
    variant_id TEXT NULL,
    symbol TEXT NOT NULL,
    cluster_id INTEGER NULL,
    client_order_id TEXT NOT NULL,
    exchange_order_id TEXT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    requested_price NUMERIC NOT NULL,
    requested_qty NUMERIC NOT NULL,
    requested_notional_usd NUMERIC NOT NULL,
    fills JSONB NOT NULL DEFAULT '[]'::jsonb,
    fill_qty NUMERIC NOT NULL DEFAULT 0,
    avg_fill_price NUMERIC NULL,
    realized_pnl NUMERIC NULL,
    exit_reason TEXT NULL,
    created_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    opened_ts TIMESTAMPTZ NULL,
    closed_ts TIMESTAMPTZ NULL,
    audit JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- CHECK constraints — wrapped in DO blocks so re-running the migration is a no-op
-- (Postgres does not support `ADD CONSTRAINT IF NOT EXISTS` directly).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'live_positions_side_chk'
    ) THEN
        ALTER TABLE live_positions
          ADD CONSTRAINT live_positions_side_chk
          CHECK (side IN ('BUY', 'SELL'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'live_positions_exit_reason_chk'
    ) THEN
        ALTER TABLE live_positions
          ADD CONSTRAINT live_positions_exit_reason_chk
          CHECK (
            exit_reason IS NULL OR exit_reason IN (
              'STOP_LOSS', 'TP1', 'TP2', 'TIME_STOP',
              'TRAILING_STOP', 'REGIME_CANCEL', 'MANUAL', 'FAIL_FAST'
            )
          );
    END IF;
END $$;

-- Indices
CREATE UNIQUE INDEX IF NOT EXISTS idx_live_positions_client_order_id
    ON live_positions(client_order_id);

CREATE INDEX IF NOT EXISTS idx_live_positions_symbol_closed
    ON live_positions(symbol, closed_ts);

CREATE INDEX IF NOT EXISTS idx_live_positions_closed_ts
    ON live_positions(closed_ts);

CREATE INDEX IF NOT EXISTS idx_live_positions_setup_created
    ON live_positions(setup_id, created_ts);

-- Partial index — open positions per cluster (cluster cap lookup is hot path).
CREATE INDEX IF NOT EXISTS idx_live_positions_cluster_open
    ON live_positions(cluster_id)
    WHERE closed_ts IS NULL;

-- Documentation
COMMENT ON TABLE  live_positions IS
    'Live (non-paper) executed positions — Phase D of Enforcer-First Deployment. '
    'Created by Cap3 enforcer at order-submit time, updated by execution worker as '
    'fills arrive, finalized at exit. client_order_id is the Binance idempotency key.';
COMMENT ON COLUMN live_positions.opportunity_id IS
    'FK to opportunities.id (the signal that birthed this position).';
COMMENT ON COLUMN live_positions.variant_id IS
    'Cap3Pipeline decision variant (e.g. config_a, config_d) chosen at submission time.';
COMMENT ON COLUMN live_positions.cluster_id IS
    'Correlation cluster id captured at signal time (used by cluster-cap risk check).';
COMMENT ON COLUMN live_positions.client_order_id IS
    'Binance clientOrderId — globally unique per submission, enables idempotent retry.';
COMMENT ON COLUMN live_positions.exchange_order_id IS
    'Binance orderId returned after successful placement; NULL until ack received.';
COMMENT ON COLUMN live_positions.fills IS
    'Array of fill records: [{ts, qty, price, fee, fee_asset}, ...]. Append-only.';
COMMENT ON COLUMN live_positions.fill_qty IS
    'Cumulative filled quantity (denormalized from fills for fast WHERE clauses).';
COMMENT ON COLUMN live_positions.avg_fill_price IS
    'Quantity-weighted average fill price; NULL until first fill.';
COMMENT ON COLUMN live_positions.realized_pnl IS
    'Realized PnL net of fees; NULL while position is open, set at exit.';
COMMENT ON COLUMN live_positions.exit_reason IS
    'Canonical exit code: STOP_LOSS | TP1 | TP2 | TIME_STOP | TRAILING_STOP | REGIME_CANCEL | MANUAL | FAIL_FAST.';
COMMENT ON COLUMN live_positions.opened_ts IS
    'Timestamp of first fill (position truly opened).';
COMMENT ON COLUMN live_positions.closed_ts IS
    'Timestamp of final close (position fully exited).';
COMMENT ON COLUMN live_positions.audit IS
    'Pipeline decision audit + execution notes (Cap3 gate evaluations, retry log, etc.).';

SELECT pg_advisory_unlock(20260413001);
COMMIT;

-- To apply: cat <file> | docker exec -i mcas-dev-postgres-1 psql -U trading -d trading (DEV FIRST per CLAUDE.md §1.5). Then: backend-postgres-1.
