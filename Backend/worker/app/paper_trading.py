"""
Paper Trading Mode
==================

Forward test on live data with simulated execution.

> "This is where confidence is earned."

Paper trading:
1. Uses the same engine as backtest/live
2. Feeds live data
3. Simulates execution with realistic models
4. Tracks virtual positions and PnL
5. Validates that signals fire when expected

Key features:
- Persistent state (survives restarts)
- Idempotent operations (no double entries)
- Full audit trail
- Explicit execution models

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
from datetime import datetime, timezone, date
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from contextlib import contextmanager

import psycopg

from runs.models import (
    Run, RunType, RunStatus,
    RunTrade, RunEvent, RunSummary,
    TradeTrace, ExitReason,
)
from execution import ExecutionConfig, realistic_config

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
        """Get database connection."""
        conn = psycopg.connect(self.database_url)
        try:
            yield conn
        finally:
            conn.close()
    
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
                trace_dict["time_stop_bars"] = state.get("time_stop_bars", 24 if signal.get("setup_id") == "S2" else 4)
                
                cur.execute("""
                    INSERT INTO paper_positions (
                        id, run_id, symbol, timeframe, side, setup_id,
                        status, signal_ts, signal_price,
                        entry_ts, entry_price, entry_price_with_slippage,
                        stop_price, tp1_price, tp2_price, r_value,
                        position_size, notional_value, risk_amount,
                        current_price, high_water_mark, fees_paid,
                        trace_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s
                    );
                """, (
                    position_id, run_id, symbol, signal.get("timeframe", "1h"), side, signal.get("setup_id", ""),
                    "OPEN", now, entry_price,
                    now, entry_price, entry_with_slip,
                    signal.get("stop_price", 0), signal.get("tp1_price"), signal.get("tp2_price"), r_value,
                    position_size, notional_value, risk_amount,
                    entry_price, entry_price, entry_fee,
                    json.dumps(trace_dict),
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
        Update all open positions with current prices.
        
        Args:
            run_id: Paper trading run ID
            price_data: Dict of {symbol: {high, low, close}}
        
        Returns:
            List of position IDs that were closed
        """
        closed_positions = []
        
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Get all open positions
                cur.execute("""
                    SELECT id, symbol, side, stop_price, tp1_price, tp2_price,
                           entry_price_with_slippage, position_size, r_value,
                           risk_amount, notional_value, fees_paid, high_water_mark
                    FROM paper_positions
                    WHERE run_id = %s AND status = 'OPEN';
                """, (run_id,))
                positions = cur.fetchall()
                
                for pos in positions:
                    pos_id, symbol, side, stop, tp1, tp2, entry, size, r_val, risk, notional, fees, hwm = pos
                    
                    if symbol not in price_data:
                        continue
                    
                    candle = price_data[symbol]
                    high = candle.get("high", 0)
                    low = candle.get("low", 0)
                    close = candle.get("close", 0)
                    
                    # Update high water mark
                    new_hwm = max(hwm or entry, high if side == "long" else entry)
                    if side == "short":
                        new_hwm = min(hwm or entry, low)
                    
                    # Check exit conditions
                    exit_reason, exit_price = self._check_exit(
                        side, stop, tp1, tp2, high, low, close
                    )
                    
                    now = datetime.now(timezone.utc)
                    
                    if exit_reason:
                        # Close the position
                        exit_with_slip = self._apply_exit_slippage(exit_price, side)
                        exit_fee = self._calculate_fee(size * exit_with_slip, is_maker=False)
                        total_fees = fees + exit_fee
                        
                        # Calculate PnL
                        if side == "long":
                            pnl = (exit_with_slip - entry) * size
                        else:
                            pnl = (entry - exit_with_slip) * size
                        
                        realized_pnl = pnl - total_fees
                        r_multiple = pnl / risk if risk > 0 else 0
                        
                        # Update position
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
                                bars_held = bars_held + 1,
                                updated_at = %s
                            WHERE id = %s;
                        """, (
                            close, now, exit_price, exit_with_slip,
                            exit_reason, realized_pnl, total_fees, r_multiple,
                            new_hwm, now, pos_id,
                        ))
                        
                        # Update account
                        win = 1 if realized_pnl > 0 else 0
                        loss = 1 if realized_pnl <= 0 else 0
                        
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
                            realized_pnl, notional,
                            win, loss,
                            realized_pnl, total_fees, realized_pnl,
                            now, run_id,
                        ))
                        
                        # Update peak/drawdown
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
                            "exit_reason": exit_reason,
                            "exit_price": round(exit_with_slip, 6),
                            "entry_price": round(entry, 6),
                            "realized_pnl": round(realized_pnl, 2),
                            "gross_pnl": round(gross_pnl, 2),
                            "total_fees": round(total_fees, 4),
                            "r_multiple": round(r_multiple, 2),
                        })
                    
                    else:
                        # Just update current price and bars held
                        unrealized = ((close - entry) * size if side == "long"
                                      else (entry - close) * size)
                        
                        cur.execute("""
                            UPDATE paper_positions
                            SET current_price = %s,
                                unrealized_pnl = %s,
                                high_water_mark = %s,
                                bars_held = bars_held + 1,
                                updated_at = %s
                            WHERE id = %s;
                        """, (close, unrealized, new_hwm, now, pos_id))
            
            conn.commit()
        
        # Check daily loss limit
        self._check_daily_loss_limit(run_id)
        
        return closed_positions
    
    def _check_exit(
        self,
        side: str,
        stop: float,
        tp1: Optional[float],
        tp2: Optional[float],
        high: float,
        low: float,
        close: float,
    ) -> tuple[Optional[str], float]:
        """Check if any exit conditions are met."""
        if side == "long":
            if low <= stop:
                return "STOP_LOSS", stop
            if tp1 and high >= tp1:
                return "TP1", tp1
            if tp2 and high >= tp2:
                return "TP2", tp2
        else:  # short
            if high >= stop:
                return "STOP_LOSS", stop
            if tp1 and low <= tp1:
                return "TP1", tp1
            if tp2 and low <= tp2:
                return "TP2", tp2
        
        return None, 0
    
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
        """Apply slippage to entry price."""
        slip_bps = self.execution_config.slippage.entry_bps
        slip = price * (slip_bps / 10000)
        if side == "long":
            return price + slip
        return price - slip
    
    def _apply_exit_slippage(self, price: float, side: str) -> float:
        """Apply slippage to exit price."""
        slip_bps = self.execution_config.slippage.exit_bps
        slip = price * (slip_bps / 10000)
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
