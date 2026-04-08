"""
Paper Trading Mode
==================

Forward test on live data with simulated execution.

> "This is where confidence is earned."

Paper trading:
1. Uses `core_logic.exit_checker.check_exit_simple` — the SAME canonical exit
   engine as the backtest simulator (`opportunity_simulator`). Paper trades
   are bit-for-bit reconcilable with simulated trades on the same OHLCV.
2. Feeds live data
3. Simulates execution with realistic slippage/fee models
4. Tracks virtual positions and PnL
5. Validates that signals fire when expected

Key features:
- Persistent state (survives restarts)
- Idempotent operations (no double entries)
- Full audit trail
- Explicit execution models
- Per-position sealed ExitConfig snapshot (from bootstrap's
  setup_exit_configs registry) so exit parameters are frozen at open time

Paper trading is NOT:
- A demo account (those have their own issues)
- Backtesting on recent data (that's just a backtest)

Paper trading IS:
- Running forward in real-time
- Making virtual decisions before you see outcomes
- The final validation before real money
"""

import os
import time
import json
import uuid
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, date
from typing import Optional
from enum import Enum
from contextlib import contextmanager

import psycopg

from governance.persistence import get_conn as _pool_get_conn

from governance.runs.models import (
    Run, RunType, RunStatus,
    RunTrade, RunEvent, RunSummary,
    TradeTrace, ExitReason,
)
from core_logic.execution import ExecutionConfig, realistic_config
from core_logic.exit_checker import ExitConfig, check_exit_simple
from governance.telemetry import (
    build_entry_telemetry_blob,
    compute_exit_snapshot,
    finalize_lifetime,
    update_mae_mfe_in_telemetry,
)

logger = logging.getLogger(__name__)


class PositionStatus(Enum):
    PENDING = "PENDING"    # Signal generated, waiting for entry
    OPEN = "OPEN"          # Position is open
    CLOSED = "CLOSED"      # Position was closed


class PaperTradingEngine:
    """
    Paper trading engine with persistent database state.
    
    All state is stored in Postgres, so it survives restarts.
    Operations are idempotent to prevent double entries on retry.
    
    Usage:
        engine = PaperTradingEngine(database_url)
        
        # Start a paper run
        run_id = engine.start_run(initial_capital=10000)
        
        # Process signals from scanner
        engine.process_signal(run_id, signal)
        
        # Update positions on each bar
        engine.update_positions(run_id, price_data)
        
        # Get status
        status = engine.get_status(run_id)
        
        # Stop run
        engine.stop_run(run_id)
    """
    
    def __init__(
        self,
        database_url: str,
        execution_config: Optional[ExecutionConfig] = None,
    ):
        self.database_url = database_url
        self.execution_config = execution_config or realistic_config()
    
    @contextmanager
    def _conn(self):
        """Get database connection from the pool."""
        with _pool_get_conn() as conn:
            yield conn

    # -------------------------------------------------------------------------
    # Canonical exit config resolution
    # -------------------------------------------------------------------------
    def _resolve_exit_config(self, setup_id: str) -> ExitConfig:
        """Return the canonical per-setup ExitConfig from the frozen registry.

        Falls back to the default ExitConfig if the registry is unavailable
        or the setup is not registered (e.g. legacy tests running outside of
        the governed bootstrap). Logs a warning on fallback.
        """
        try:
            from governance.bootstrap import get_stop_policy
            stop_reg = get_stop_policy()
            setup_exit_configs = stop_reg.setup_exit_configs
            cfg = setup_exit_configs.get(setup_id)
            if cfg is not None:
                return cfg
            logger.warning(
                "No setup_exit_configs entry for %s — using default ExitConfig", setup_id,
            )
        except Exception as e:  # pragma: no cover — defensive fallback
            logger.warning(
                "Exit config resolution failed for %s (%s) — using default ExitConfig",
                setup_id, e,
            )
        return ExitConfig()
    
    # =========================================================================
    # Run Lifecycle
    # =========================================================================
    
    def get_active_run(self) -> Optional[str]:
        """Get the currently active paper trading run ID."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id FROM runs
                    WHERE run_type = 'PAPER' AND status = 'RUNNING'
                    ORDER BY started_at DESC
                    LIMIT 1;
                """)
                row = cur.fetchone()
        return str(row[0]) if row else None
    
    def get_account(self, run_id: str) -> Optional[dict]:
        """Get paper account state."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT initial_capital, current_capital, peak_capital,
                           total_trades, winning_trades, losing_trades,
                           total_pnl, total_fees, max_drawdown,
                           current_exposure, daily_pnl, daily_pnl_reset_date
                    FROM paper_accounts
                    WHERE run_id = %s;
                """, (run_id,))
                row = cur.fetchone()
        
        if not row:
            return None
        
        return {
            "initial_capital": row[0],
            "current_capital": row[1],
            "peak_capital": row[2],
            "total_trades": row[3],
            "winning_trades": row[4],
            "losing_trades": row[5],
            "total_pnl": row[6],
            "total_fees": row[7],
            "max_drawdown": row[8],
            "current_exposure": row[9],
            "daily_pnl": row[10],
            "daily_pnl_reset_date": row[11],
        }
    
    # =========================================================================
    # Signal Processing
    # =========================================================================
    
    def process_signal(self, run_id: str, signal: dict) -> Optional[str]:
        """
        Process a signal from the scanner.
        
        Idempotent: won't create duplicate positions for same signal.
        
        Checks:
        1. No existing position for symbol (one-per-asset)
        2. Daily trade cap not exceeded
        3. Per-asset cooldown after stop-out
        4. Max concurrent positions
        5. Daily loss limit
        
        Args:
            run_id: Active paper trading run ID
            signal: Signal dict with symbol, direction, entry_price, etc.
        
        Returns:
            Position ID if opened, None if rejected
        """
        symbol = signal.get("symbol")
        
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Check if we already have a position for this symbol
                cur.execute("""
                    SELECT id FROM paper_positions
                    WHERE run_id = %s AND symbol = %s AND status IN ('PENDING', 'OPEN');
                """, (run_id, symbol))
                existing = cur.fetchone()
                
                if existing:
                    logger.debug(f"Already have position for {symbol}, skipping")
                    return None
                
                # Check per-asset cooldown (no immediate re-entry after stop-out)
                # Cooldown: 6 hours after a stop-loss exit
                cur.execute("""
                    SELECT exit_ts, exit_reason FROM paper_positions
                    WHERE run_id = %s AND symbol = %s AND status = 'CLOSED'
                    ORDER BY exit_ts DESC LIMIT 1;
                """, (run_id, symbol))
                last_exit = cur.fetchone()
                
                if last_exit and last_exit[1] == "STOP_LOSS":
                    exit_ts = last_exit[0]
                    if exit_ts:
                        from datetime import timedelta
                        cooldown_until = exit_ts + timedelta(hours=6)
                        if datetime.now(timezone.utc) < cooldown_until:
                            logger.debug(f"Cooldown active for {symbol} after stop-out, skipping")
                            return None
                
                # Get account state including daily trade count
                cur.execute("""
                    SELECT current_capital, current_exposure, daily_pnl, 
                           daily_pnl_reset_date, trades_today
                    FROM paper_accounts
                    WHERE run_id = %s;
                """, (run_id,))
                account = cur.fetchone()
                
                if not account:
                    logger.error(f"No account found for run {run_id}")
                    return None
                
                current_capital, current_exposure, daily_pnl, reset_date, trades_today = account
                
                # Reset daily counters if new day
                today = date.today()
                if reset_date and reset_date < today:
                    cur.execute("""
                        UPDATE paper_accounts
                        SET daily_pnl = 0, daily_pnl_reset_date = %s, trades_today = 0
                        WHERE run_id = %s;
                    """, (today, run_id))
                    conn.commit()
                    trades_today = 0
                
                # Check daily trade cap (default 20 trades per day)
                max_trades_per_day = 20  # Could be made configurable
                if trades_today and trades_today >= max_trades_per_day:
                    logger.debug(f"Daily trade cap reached ({trades_today}), skipping {symbol}")
                    return None
                
                # Check daily loss limit
                max_daily_loss_pct = self.execution_config.max_daily_loss_pct
                if current_capital > 0:
                    daily_loss_pct = abs(daily_pnl) / current_capital * 100 if daily_pnl < 0 else 0
                    if daily_loss_pct >= max_daily_loss_pct:
                        logger.warning(f"Daily loss limit reached ({daily_loss_pct:.1f}%), skipping {symbol}")
                        return None
                
                # Count open positions
                cur.execute("""
                    SELECT COUNT(*) FROM paper_positions
                    WHERE run_id = %s AND status = 'OPEN';
                """, (run_id,))
                open_count = cur.fetchone()[0]
                
                if open_count >= self.execution_config.max_positions:
                    logger.debug(f"Max positions reached ({open_count}), skipping {symbol}")
                    return None
                
                # Calculate position size
                risk_pct = self.execution_config.max_risk_per_trade_pct / 100
                risk_amount = current_capital * risk_pct
                r_value = signal.get("r_value", 0)
                
                if r_value <= 0:
                    logger.warning(f"Invalid R value for {symbol}: {r_value}")
                    return None
                
                entry_price = signal.get("entry_price", 0)
                position_size = risk_amount / r_value
                notional_value = position_size * entry_price
                
                # Apply entry slippage
                side = signal.get("direction", "long")
                entry_with_slip = self._apply_entry_slippage(entry_price, side)
                
                # Calculate entry fee
                entry_fee = self._calculate_fee(notional_value, is_maker=False)
                
                # Create position
                position_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc)

                # Get setup-specific exit parameters from signal payload
                payload = signal.get("payload", {})
                state = payload.get("state", {})

                trace = TradeTrace(
                    conditions_met=payload.get("conditions_met", {}),
                    entry_reason=f"{signal.get('setup_id', 'UNKNOWN')}_TRIGGER",
                    indicator_values=payload.get("indicator_values", {}),
                    score_components=payload.get("score_components", {}),
                )

                # Add exit parameters to trace for PositionMonitor
                trace_dict = trace.to_dict()
                trace_dict["box_high"] = state.get("box_high")
                trace_dict["box_low"] = state.get("box_low")
                trace_dict["fail_fast_bars"] = state.get("fail_fast_bars", 3)
                trace_dict["time_stop_bars"] = state.get("time_stop_bars", signal.get("time_stop_bars", 4))

                # === Canonical exit checker state (Phase 2) ==========================
                # Seal the per-setup ExitConfig snapshot at open time so trail/fail-fast
                # settings cannot drift if bootstrap is later re-populated. Pull from
                # the frozen registry — this is the sole source of truth.
                setup_id = signal.get("setup_id", "")
                exit_cfg = self._resolve_exit_config(setup_id)
                exit_config_json = json.dumps(asdict(exit_cfg), sort_keys=True)

                # r_value for exit-checker math must use entry WITH slippage — the
                # sim does the same. The r_value passed in the signal payload is a
                # pre-slippage estimate and can diverge from reality by a few bps.
                r_value_slip = abs(entry_with_slip - signal.get("stop_price", 0.0))

                # Time-stop in native-timeframe bars. S9 = 48, D1 = 72, etc.
                max_bars_native = int(
                    state.get("time_stop_bars")
                    or signal.get("time_stop_bars")
                    or trace_dict.get("time_stop_bars")
                    or 72
                )

                # === Telemetry blob (schema v1) =====================================
                # Build the full telemetry blob at open time. Signal/regime
                # context + entry snapshot populated now; lifetime section
                # initialized for bar-by-bar MAE/MFE tracking; microstructure
                # and post_exit remain None (follow-up).
                submit_ts_ms = int(now.timestamp() * 1000)
                fill_ts_ms = submit_ts_ms  # paper trading — no broker latency
                try:
                    telemetry_blob = build_entry_telemetry_blob(
                        signal=signal,
                        run_id=run_id,
                        conn=conn,
                        fill_price=entry_price,
                        fill_price_with_slippage=entry_with_slip,
                        submit_ts_ms=submit_ts_ms,
                        fill_ts_ms=fill_ts_ms,
                        position_size=position_size,
                        r_value_slip=r_value_slip,
                        entry_ts=now,
                    )
                    telemetry_json = json.dumps(telemetry_blob, default=str)
                except Exception as e:
                    logger.warning(
                        "Failed to build entry telemetry for %s: %s — "
                        "position will still be opened without telemetry",
                        symbol, e,
                    )
                    telemetry_json = None

                cur.execute("""
                    INSERT INTO paper_positions (
                        id, run_id, symbol, timeframe, side, setup_id,
                        status, signal_ts, signal_price,
                        entry_ts, entry_price, entry_price_with_slippage,
                        stop_price, tp1_price, tp2_price, r_value,
                        position_size, notional_value, risk_amount,
                        current_price, high_water_mark, fees_paid,
                        trace_json,
                        half_r_touched, max_bars, r_value_slip, exit_config_json,
                        telemetry_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s,
                        %s, %s, %s, %s,
                        %s
                    );
                """, (
                    position_id, run_id, symbol, signal.get("timeframe", "1h"), side, setup_id,
                    "OPEN", now, entry_price,
                    now, entry_price, entry_with_slip,
                    signal.get("stop_price", 0), signal.get("tp1_price"), signal.get("tp2_price"), r_value,
                    position_size, notional_value, risk_amount,
                    entry_price, entry_with_slip, entry_fee,
                    json.dumps(trace_dict),
                    False, max_bars_native, r_value_slip, exit_config_json,
                    telemetry_json,
                ))
                
                # Update account exposure and increment trades_today
                cur.execute("""
                    UPDATE paper_accounts
                    SET current_exposure = current_exposure + %s,
                        trades_today = COALESCE(trades_today, 0) + 1,
                        updated_at = %s
                    WHERE run_id = %s;
                """, (notional_value, now, run_id))
            
            conn.commit()
        
        logger.info("Paper position opened", extra={
            "event": "position_opened",
            "position_id": position_id,
            "symbol": symbol,
            "side": side,
            "setup_id": signal.get("setup_id"),
            "entry_price": round(entry_with_slip, 6),
            "stop_price": signal.get("stop_price"),
            "tp1_price": signal.get("tp1_price"),
            "tp2_price": signal.get("tp2_price"),
            "position_size": round(position_size, 6),
            "notional_value": round(notional_value, 2),
            "risk_amount": round(risk_amount, 2),
            "r_value": round(r_value, 4),
            "entry_fee": round(entry_fee, 4),
        })
        
        return position_id
    
    # =========================================================================
    # Position Updates
    # =========================================================================
    
    def update_positions(self, run_id: str, price_data: dict[str, dict]) -> list[str]:
        """
        Update all open positions with current prices using the canonical
        exit checker (core_logic.exit_checker.check_exit_simple) plus the
        split-exit orchestration that opportunity_simulator implements.

        Exit semantics (matches opportunity_simulator.simulate exactly):
          - On TP1 AND tp2_price is set AND !tp1_taken: partially close 50%,
            move stop to breakeven, clear tp1_price, mark tp1_taken=TRUE.
            Position STAYS OPEN on the runner.
          - On runner-leg exit (TP2, STOP_LOSS, TIME_STOP, TRAIL_STOP, FAIL_FAST):
            close the remaining position. If tp1_taken, exit_reason is reported
            as 'TP1+<runner_reason>' with a weighted-average exit price.

        Args:
            run_id: Paper trading run ID
            price_data: Dict of {symbol: {open, high, low, close}}
                Note: `open` is REQUIRED for gap-through handling. Older callers
                that pass only {high, low, close} will get `bar_open` == close
                which degrades gap-through fidelity but does not crash.

        Returns:
            List of position IDs that were FULLY closed on this tick.
        """
        closed_positions: list[str] = []

        with self._conn() as conn:
            with conn.cursor() as cur:
                # Pull all open positions including split-exit state columns
                # and the telemetry blob for MAE/MFE tracking.
                cur.execute("""
                    SELECT id, symbol, side, stop_price, tp1_price, tp2_price,
                           entry_price_with_slippage, position_size, r_value,
                           risk_amount, notional_value, fees_paid,
                           high_water_mark, bars_held,
                           half_r_touched, max_bars, r_value_slip, exit_config_json,
                           tp1_taken,
                           tp1_leg_exit_price, tp1_leg_exit_with_slip,
                           tp1_leg_close_size, tp1_leg_realized_gross,
                           tp1_leg_realized_fees, tp1_leg_exit_ts,
                           telemetry_json
                    FROM paper_positions
                    WHERE run_id = %s AND status = 'OPEN';
                """, (run_id,))
                positions = cur.fetchall()

                for pos in positions:
                    (
                        pos_id, symbol, side, stop, tp1, tp2,
                        entry, size, r_val, risk, notional, fees,
                        hwm, bars_held,
                        half_r_touched, max_bars, r_value_slip, exit_config_json,
                        tp1_taken,
                        tp1_leg_exit_price, tp1_leg_exit_with_slip,
                        tp1_leg_close_size, tp1_leg_realized_gross,
                        tp1_leg_realized_fees, tp1_leg_exit_ts,
                        telemetry_db,
                    ) = pos

                    if symbol not in price_data:
                        continue

                    candle = price_data[symbol]
                    bar_open = candle.get("open", candle.get("close", 0))
                    high = candle.get("high", 0)
                    low = candle.get("low", 0)
                    close = candle.get("close", 0)

                    # telemetry_db arrives as a dict from psycopg's jsonb adapter
                    # (or None for legacy rows that predate the telemetry migration).
                    telemetry = telemetry_db if isinstance(telemetry_db, dict) else None

                    bar = {
                        "open": bar_open,
                        "high": high,
                        "low": low,
                        "close": close,
                    }

                    # --- Telemetry: MAE/MFE/HWM/time-in-profit on this bar ---
                    # bar_idx inside the telemetry frame is the position's current
                    # bars_held (0 at entry bar, 1 next hour, etc.). The update
                    # happens BEFORE the exit check so the bar that triggers the
                    # exit also contributes to MAE/MFE stats.
                    if telemetry is not None:
                        try:
                            telemetry = update_mae_mfe_in_telemetry(
                                telemetry=telemetry,
                                bar=bar,
                                bar_ts=candle.get("bar_start_ts") or datetime.now(timezone.utc),
                                bar_idx=int(bars_held or 0),
                            )
                        except Exception as e:
                            logger.warning(
                                "telemetry update failed for %s: %s", pos_id, e,
                            )

                    # Reconstruct the sealed ExitConfig (or fall back for
                    # legacy positions that predate the migration).
                    if exit_config_json:
                        try:
                            exit_cfg = ExitConfig(**json.loads(exit_config_json))
                        except Exception as e:
                            logger.warning(
                                "Invalid exit_config_json for %s (%s) — using default",
                                pos_id, e,
                            )
                            exit_cfg = ExitConfig()
                    else:
                        exit_cfg = self._resolve_exit_config(
                            self._fetch_setup_id(cur, pos_id)
                        )

                    r_for_exit = r_value_slip
                    if r_for_exit is None or r_for_exit <= 0:
                        r_for_exit = abs(entry - (stop or 0.0))

                    exit_result, new_hwm, new_half_r = check_exit_simple(
                        side=side,
                        bar=bar,
                        entry_price=entry,
                        stop_price=stop,
                        tp1_price=tp1,
                        tp2_price=tp2,
                        r_value=r_for_exit,
                        bars_held=bars_held or 0,
                        max_bars=max_bars if max_bars is not None else 72,
                        high_water_mark=hwm,
                        half_r_touched=bool(half_r_touched),
                        config=exit_cfg,
                        is_entry_bar=((bars_held or 0) == 0),
                    )

                    now = datetime.now(timezone.utc)

                    # ─────────────────────────────────────────────────────────
                    # CASE 1 — TP1 partial close (split-exit first leg)
                    # ─────────────────────────────────────────────────────────
                    if (
                        exit_result is not None
                        and exit_result[0] == "TP1"
                        and tp2 is not None
                        and not tp1_taken
                    ):
                        _, leg_price = exit_result
                        leg_price_with_slip = self._apply_exit_slippage(leg_price, side)
                        leg_close_size = size * 0.5
                        if side == "long":
                            leg_pnl_gross = (leg_price_with_slip - entry) * leg_close_size
                        else:
                            leg_pnl_gross = (entry - leg_price_with_slip) * leg_close_size
                        leg_fee = self._calculate_fee(leg_close_size * leg_price_with_slip, is_maker=False)

                        remaining_size = size - leg_close_size

                        # Reduce notional exposure by the fraction closed so the
                        # account's current_exposure stays accurate.
                        notional_freed = notional * 0.5

                        # Persist the bar's MAE/MFE update into telemetry
                        tp1_telemetry_json = (
                            json.dumps(telemetry, default=str)
                            if telemetry is not None else None
                        )
                        cur.execute("""
                            UPDATE paper_positions
                            SET position_size = %s,
                                notional_value = notional_value - %s,
                                stop_price = %s,
                                tp1_price = NULL,
                                tp1_taken = TRUE,
                                tp1_leg_exit_price = %s,
                                tp1_leg_exit_with_slip = %s,
                                tp1_leg_close_size = %s,
                                tp1_leg_realized_gross = %s,
                                tp1_leg_realized_fees = %s,
                                tp1_leg_exit_ts = %s,
                                current_price = %s,
                                high_water_mark = %s,
                                half_r_touched = %s,
                                fees_paid = fees_paid + %s,
                                bars_held = bars_held + 1,
                                telemetry_json = %s,
                                updated_at = %s
                            WHERE id = %s;
                        """, (
                            remaining_size, notional_freed,
                            entry,  # stop -> breakeven
                            leg_price, leg_price_with_slip,
                            leg_close_size, leg_pnl_gross, leg_fee, now,
                            close, new_hwm, bool(new_half_r),
                            leg_fee,
                            tp1_telemetry_json,
                            now, pos_id,
                        ))

                        # Book the TP1 leg PnL into account; keep position open
                        cur.execute("""
                            UPDATE paper_accounts
                            SET current_capital = current_capital + %s,
                                current_exposure = current_exposure - %s,
                                total_fees = total_fees + %s,
                                updated_at = %s
                            WHERE run_id = %s;
                        """, (
                            leg_pnl_gross - leg_fee,
                            notional_freed,
                            leg_fee,
                            now, run_id,
                        ))

                        logger.info("Paper position TP1 partial", extra={
                            "event": "position_tp1_partial",
                            "position_id": pos_id,
                            "symbol": symbol,
                            "leg_exit_price": round(leg_price_with_slip, 6),
                            "leg_close_size": round(leg_close_size, 6),
                            "leg_pnl_gross": round(leg_pnl_gross, 2),
                            "remaining_size": round(remaining_size, 6),
                            "stop_moved_to_breakeven": round(entry, 6),
                        })
                        continue  # Next position — this one stays OPEN

                    # ─────────────────────────────────────────────────────────
                    # CASE 2 — Final close (full close or runner close)
                    # ─────────────────────────────────────────────────────────
                    if exit_result is not None:
                        runner_reason, runner_price = exit_result
                        # Slippage rules (mirroring opportunity_simulator):
                        #   - STOP_LOSS at the exact stop_price (no gap-through):
                        #     limit order, NO slippage applied.
                        #   - STOP_LOSS with gap-through (runner_price != stop):
                        #     market sweep, apply directional slippage.
                        #   - Every other exit reason (TP1/TP2/TRAIL/TIME/FAIL_FAST):
                        #     market exit, apply directional slippage.
                        # (Note: for the TP1-partial path above, TP1 is always slipped —
                        # that's correct too since TP1 is a market exit.)
                        if runner_reason == "STOP_LOSS" and stop is not None and runner_price == stop:
                            runner_price_with_slip = runner_price
                        else:
                            runner_price_with_slip = self._apply_exit_slippage(runner_price, side)

                        if side == "long":
                            runner_gross = (runner_price_with_slip - entry) * size
                        else:
                            runner_gross = (entry - runner_price_with_slip) * size
                        runner_fee = self._calculate_fee(size * runner_price_with_slip, is_maker=False)

                        if tp1_taken:
                            # Combine legs into a weighted-average exit price
                            original_size = (tp1_leg_close_size or 0.0) + size
                            if original_size > 0:
                                weighted_exit_price = (
                                    (tp1_leg_exit_price or 0.0) * (tp1_leg_close_size or 0.0)
                                    + runner_price * size
                                ) / original_size
                                weighted_exit_with_slip = (
                                    (tp1_leg_exit_with_slip or 0.0) * (tp1_leg_close_size or 0.0)
                                    + runner_price_with_slip * size
                                ) / original_size
                            else:
                                weighted_exit_price = runner_price
                                weighted_exit_with_slip = runner_price_with_slip

                            final_exit_reason = f"TP1+{runner_reason}"
                            final_exit_price = weighted_exit_price
                            final_exit_with_slip = weighted_exit_with_slip
                            gross_pnl = (tp1_leg_realized_gross or 0.0) + runner_gross
                        else:
                            final_exit_reason = runner_reason
                            final_exit_price = runner_price
                            final_exit_with_slip = runner_price_with_slip
                            gross_pnl = runner_gross

                        # total_fees reflects ALL fees — entry, TP1 exit (if any), runner exit.
                        # `fees` already includes entry + tp1_leg fee (stored in paper_positions
                        # after the TP1 partial path ran). Add the runner exit fee.
                        total_fees = fees + runner_fee
                        # realized_pnl = gross PnL minus ALL fees (matches sim's pnl_net).
                        realized_pnl = gross_pnl - total_fees
                        # r_multiple is computed on gross PnL (no fees) / risk budget —
                        # matches simulator reporting convention.
                        r_multiple = gross_pnl / risk if risk and risk > 0 else 0

                        # Finalize telemetry with exit snapshot + lifetime totals
                        final_telemetry_json: Optional[str] = None
                        if telemetry is not None:
                            try:
                                telemetry = dict(telemetry)
                                telemetry["exit_snapshot"] = compute_exit_snapshot(
                                    exit_price=final_exit_price,
                                    exit_price_with_slippage=final_exit_with_slip,
                                    exit_reason=final_exit_reason,
                                    stop_price=stop,
                                    tp1_price=tp1_leg_exit_price,
                                    tp2_price=tp2,
                                    exit_ts=now,
                                    side=side,
                                    bar_open=bar_open,
                                )
                                telemetry = finalize_lifetime(
                                    telemetry=telemetry,
                                    exit_ts=now,
                                    bars_held=int(bars_held or 0),
                                    exit_reason=final_exit_reason,
                                )
                                final_telemetry_json = json.dumps(telemetry, default=str)
                            except Exception as e:
                                logger.warning(
                                    "final telemetry build failed for %s: %s",
                                    pos_id, e,
                                )

                        # Store the CURRENT bars_held (the iteration that triggered
                        # the exit) — do NOT increment. Matches opportunity_simulator,
                        # which reports result.bars_held = elapsed_strategy_bars at
                        # the exit bar, not bars_held + 1.
                        cur.execute("""
                            UPDATE paper_positions
                            SET status = 'CLOSED',
                                current_price = %s,
                                exit_ts = %s,
                                exit_price = %s,
                                exit_price_with_slippage = %s,
                                exit_reason = %s,
                                realized_pnl = %s,
                                fees_paid = %s,
                                r_multiple = %s,
                                high_water_mark = %s,
                                half_r_touched = %s,
                                telemetry_json = COALESCE(%s::jsonb, telemetry_json),
                                updated_at = %s
                            WHERE id = %s;
                        """, (
                            close, now, final_exit_price, final_exit_with_slip,
                            final_exit_reason, realized_pnl, total_fees, r_multiple,
                            new_hwm, bool(new_half_r),
                            final_telemetry_json,
                            now, pos_id,
                        ))

                        win = 1 if realized_pnl > 0 else 0
                        loss = 1 if realized_pnl <= 0 else 0

                        # Delta to current_capital at final close.
                        # For split trades, the TP1 partial already added
                        # (tp1_leg_realized_gross - tp1_leg_realized_fees)
                        # to capital. For non-split, nothing was added yet.
                        # The net delta that makes total capital change = realized_pnl:
                        already_added = (
                            (tp1_leg_realized_gross or 0.0) - (tp1_leg_realized_fees or 0.0)
                            if tp1_taken else 0.0
                        )
                        capital_delta = realized_pnl - already_added

                        cur.execute("""
                            UPDATE paper_accounts
                            SET current_capital = current_capital + %s,
                                current_exposure = current_exposure - %s,
                                total_trades = total_trades + 1,
                                winning_trades = winning_trades + %s,
                                losing_trades = losing_trades + %s,
                                total_pnl = total_pnl + %s,
                                total_fees = total_fees + %s,
                                daily_pnl = daily_pnl + %s,
                                updated_at = %s
                            WHERE run_id = %s;
                        """, (
                            capital_delta,
                            notional,
                            win, loss,
                            capital_delta, runner_fee, capital_delta,
                            now, run_id,
                        ))

                        cur.execute("""
                            UPDATE paper_accounts
                            SET peak_capital = GREATEST(peak_capital, current_capital),
                                max_drawdown = GREATEST(
                                    max_drawdown,
                                    (peak_capital - current_capital) / NULLIF(peak_capital, 0)
                                )
                            WHERE run_id = %s;
                        """, (run_id,))

                        closed_positions.append(pos_id)

                        logger.info("Paper position closed", extra={
                            "event": "position_closed",
                            "position_id": pos_id,
                            "symbol": symbol,
                            "side": side,
                            "exit_reason": final_exit_reason,
                            "exit_price": round(final_exit_with_slip, 6),
                            "entry_price": round(entry, 6),
                            "realized_pnl": round(realized_pnl, 2),
                            "gross_pnl": round(gross_pnl, 2),
                            "total_fees": round(total_fees, 4),
                            "r_multiple": round(r_multiple, 2),
                            "tp1_taken": bool(tp1_taken),
                        })

                    else:
                        # No exit — advance bars, refresh HWM / half_r state,
                        # persist the bar's MAE/MFE update into telemetry.
                        unrealized = ((close - entry) * size if side == "long"
                                      else (entry - close) * size)
                        noexit_telemetry_json = (
                            json.dumps(telemetry, default=str)
                            if telemetry is not None else None
                        )
                        cur.execute("""
                            UPDATE paper_positions
                            SET current_price = %s,
                                unrealized_pnl = %s,
                                high_water_mark = %s,
                                half_r_touched = %s,
                                bars_held = bars_held + 1,
                                telemetry_json = COALESCE(%s::jsonb, telemetry_json),
                                updated_at = %s
                            WHERE id = %s;
                        """, (
                            close, unrealized, new_hwm, bool(new_half_r),
                            noexit_telemetry_json, now, pos_id,
                        ))

            conn.commit()

        # Check daily loss limit
        self._check_daily_loss_limit(run_id)

        return closed_positions

    def _fetch_setup_id(self, cur, pos_id: str) -> str:
        """Read back setup_id for a legacy row missing exit_config_json."""
        cur.execute("SELECT setup_id FROM paper_positions WHERE id = %s;", (pos_id,))
        row = cur.fetchone()
        return row[0] if row else ""
    
    def _check_daily_loss_limit(self, run_id: str):
        """Check if daily loss limit has been hit."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT daily_pnl, initial_capital, daily_pnl_reset_date
                    FROM paper_accounts
                    WHERE run_id = %s;
                """, (run_id,))
                row = cur.fetchone()
                
                if not row:
                    return
                
                daily_pnl, initial_capital, reset_date = row
                today = date.today()
                
                # Reset daily PnL if new day
                if reset_date and reset_date < today:
                    cur.execute("""
                        UPDATE paper_accounts
                        SET daily_pnl = 0, daily_pnl_reset_date = %s
                        WHERE run_id = %s;
                    """, (today, run_id))
                    conn.commit()
                    return
                
                # Check limit
                max_daily_loss = initial_capital * (self.execution_config.max_daily_loss_pct / 100)
                
                if daily_pnl < -max_daily_loss:
                    logger.warning("Daily loss circuit breaker triggered", extra={
                        "event": "circuit_breaker",
                        "daily_pnl": round(daily_pnl, 2),
                        "max_daily_loss": round(max_daily_loss, 2),
                        "limit_pct": self.execution_config.max_daily_loss_pct,
                    })
                    # Could close all positions here for circuit breaker
    
    # =========================================================================
    # Helpers
    # =========================================================================
    
    def _apply_entry_slippage(self, price: float, side: str) -> float:
        """Apply slippage to entry price via the polymorphic SlippageModel.

        Works with FixedBpsSlippage, TimeframeScaledSlippage, and any other
        SlippageModel subclass — they all implement `.calculate()`.
        """
        slip = self.execution_config.slippage.calculate(
            price=price, side=side, is_entry=True,
        )
        if side == "long":
            return price + slip
        return price - slip

    def _apply_exit_slippage(self, price: float, side: str) -> float:
        """Apply slippage to exit price via the polymorphic SlippageModel."""
        slip = self.execution_config.slippage.calculate(
            price=price, side=side, is_entry=False,
        )
        if side == "long":
            return price - slip
        return price + slip
    
    def _calculate_fee(self, notional: float, is_maker: bool = False) -> float:
        """Calculate trading fee."""
        return self.execution_config.fees.calculate(notional, is_maker)
    
    # =========================================================================
    # Status
    # =========================================================================
    
    def get_open_positions(self, run_id: str) -> list[dict]:
        """Get all open positions."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, symbol, side, setup_id,
                           entry_price_with_slippage, stop_price, tp1_price,
                           current_price, unrealized_pnl, bars_held,
                           position_size, r_value
                    FROM paper_positions
                    WHERE run_id = %s AND status = 'OPEN'
                    ORDER BY created_at DESC;
                """, (run_id,))
                rows = cur.fetchall()
        
        return [
            {
                "id": str(row[0]),
                "symbol": row[1],
                "side": row[2],
                "setup_id": row[3],
                "entry_price": row[4],
                "stop_price": row[5],
                "tp1_price": row[6],
                "current_price": row[7],
                "unrealized_pnl": row[8],
                "bars_held": row[9],
                "position_size": row[10],
                "r_value": row[11],
            }
            for row in rows
        ]
    
    def get_recent_trades(self, run_id: str, limit: int = 20) -> list[dict]:
        """Get recently closed positions."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT symbol, side, setup_id,
                           entry_price_with_slippage, exit_price_with_slippage,
                           exit_reason, realized_pnl, r_multiple, exit_ts
                    FROM paper_positions
                    WHERE run_id = %s AND status = 'CLOSED'
                    ORDER BY exit_ts DESC
                    LIMIT %s;
                """, (run_id, limit))
                rows = cur.fetchall()
        
        return [
            {
                "symbol": row[0],
                "side": row[1],
                "setup_id": row[2],
                "entry_price": row[3],
                "exit_price": row[4],
                "exit_reason": row[5],
                "realized_pnl": row[6],
                "r_multiple": row[7],
                "exit_ts": row[8].isoformat() if row[8] else None,
            }
            for row in rows
        ]
