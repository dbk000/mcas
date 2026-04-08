-- Phase 2 (S9 Paper Trading Readiness) — add columns required by the canonical
-- exit checker (core_logic.exit_checker.check_exit_simple).
--
-- The existing paper_trading.PaperTradingEngine used a custom 26-line _check_exit
-- that only covered STOP_LOSS / TP1 / TP2. The canonical exit checker additionally
-- tracks half-R progress (for time-stop), max_bars (time stop threshold),
-- entry-with-slippage r_value (for trail/time-stop math), and a per-setup
-- ExitConfig snapshot sealed at open time. These columns back that state.
--
-- Idempotent. Safe to re-run.

BEGIN;
SELECT pg_advisory_lock(20260408);

ALTER TABLE paper_positions
    -- Canon exit state
    ADD COLUMN IF NOT EXISTS half_r_touched BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS max_bars       INT     NOT NULL DEFAULT 72,
    ADD COLUMN IF NOT EXISTS r_value_slip   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS exit_config_json TEXT,
    -- Split-exit (TP1 partial close) tracking — mirrors opportunity_simulator
    ADD COLUMN IF NOT EXISTS tp1_taken BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS tp1_leg_exit_price           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tp1_leg_exit_with_slip       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tp1_leg_close_size           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tp1_leg_realized_gross       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tp1_leg_realized_fees        DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tp1_leg_exit_ts              TIMESTAMPTZ;

COMMENT ON COLUMN paper_positions.half_r_touched IS
    'Canon exit checker state: set to TRUE once price wicks through entry ±0.5R. Gates TIME_STOP.';
COMMENT ON COLUMN paper_positions.max_bars IS
    'Time-stop threshold in native-timeframe bars. Populated from signal.time_stop_bars at open.';
COMMENT ON COLUMN paper_positions.r_value_slip IS
    'abs(entry_with_slippage - stop_price) — the R used by the exit checker for trail/progress math.';
COMMENT ON COLUMN paper_positions.exit_config_json IS
    'Sealed per-setup ExitConfig snapshot (JSON) at position-open time. Reconstructed into ExitConfig(**json) on every update_positions call.';
COMMENT ON COLUMN paper_positions.tp1_taken IS
    'Split-exit state: TRUE once 50%% was closed at TP1. Runner continues with stop@breakeven, tp1 cleared, until TP2/STOP_LOSS/TIME_STOP/TRAIL_STOP closes the runner. Matches opportunity_simulator.';
COMMENT ON COLUMN paper_positions.tp1_leg_exit_price IS
    'TP1 partial close fill price (before slippage). NULL until the split fires.';
COMMENT ON COLUMN paper_positions.tp1_leg_close_size IS
    'Quantity closed at TP1 (50%% of original position_size). The remaining size lives on as position_size for the runner leg.';
COMMENT ON COLUMN paper_positions.tp1_leg_realized_gross IS
    'Gross PnL realized on the TP1 partial leg. Summed with runner-leg PnL at final close.';

SELECT pg_advisory_unlock(20260408);
COMMIT;
