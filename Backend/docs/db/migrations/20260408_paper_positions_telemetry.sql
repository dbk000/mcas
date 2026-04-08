-- Paper trading telemetry — Phase A (immediately available fields)
--
-- Adds a single JSONB column that carries the full per-trade telemetry
-- blob. Schema v1 sections:
--
--   signal_context      — S9 rank/score/breadth/FnG + same-bar crowding +
--                         concurrent-at-entry + FIFO position
--   regime_context      — BTC price/return, liquidation cascade, session,
--                         days since last loss, rolling PF
--   entry_snapshot      — signal_price, fill, slippage_bps, ms-precision
--                         submit/fill timestamps, latency
--   exit_snapshot       — (set at close)  fill, slippage_bps, gap-through
--   lifetime            — bars held, seconds held, MAE/MFE (price/bps/USD
--                         + timestamps + bar_idx), HWM, time-in-profit vs
--                         drawdown, breakeven-then-reverse flag, funding
--                         intervals crossed
--   microstructure_entry — (follow-up) bid/ask spread, 1m vol, OI, funding
--   microstructure_exit  — (follow-up) same
--   post_exit           — (follow-up, 48h background job) price@24h/48h,
--                         counterfactual PnL under different stops, BTC
--                         correlation, repeat-pump detection
--   operational         — (follow-up) server/latency/reconciler/circuit
--                         breaker state
--
-- The JSONB blob is rewritten on every update_positions call during the
-- walk so MAE/MFE tracking stays current. At ~2KB per row this is cheap.
--
-- Idempotent. Safe to re-run.

BEGIN;
SELECT pg_advisory_lock(20260408001);

ALTER TABLE paper_positions
    ADD COLUMN IF NOT EXISTS telemetry_json JSONB;

COMMENT ON COLUMN paper_positions.telemetry_json IS
    'Per-trade telemetry (schema v1). Sections: signal_context, regime_context, entry_snapshot, exit_snapshot, lifetime (MAE/MFE/HWM/time-in-profit tracking), microstructure_entry/exit (follow-up), post_exit (48h background job), operational (follow-up). Rewritten every bar during walk so MAE/MFE stay current.';

-- Optional partial GIN index for future telemetry querying.
-- Commented out for now — can be added when analytical queries land.
-- CREATE INDEX IF NOT EXISTS idx_paper_positions_telemetry_gin
--     ON paper_positions USING gin (telemetry_json jsonb_path_ops)
--     WHERE telemetry_json IS NOT NULL;

SELECT pg_advisory_unlock(20260408001);
COMMIT;
