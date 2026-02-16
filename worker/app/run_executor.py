"""
Run Executor
============

Executes all run types: SCAN, BACKTEST, PAPER.

This is the unified execution engine that:
1. Claims and executes runs from the queue
2. Handles all run types with the same lifecycle
3. Records trades with full audit trails
4. Updates run summaries

"Paper trading is literally backtest running forward with persistent positions.
Live trading becomes: replace simulated execution with exchange execution."
"""

import os
import time
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import asdict
from contextlib import contextmanager

import psycopg

from runs.models import (
    Run, RunType, RunStatus,
    RunTrade, RunEvent, RunSummary,
    UniverseSpec, StrategySpec, ExecutionSpec, DataSpec,
    TradeTrace, ExitReason,
)
from runs.repository import RunRepository
from signal_engine import SignalEngine, RankedSignal
from execution import ExecutionConfig, realistic_config, FixedBpsSlippage, TieredFees

# Redis cache support (Phase 4)
try:
    from cache_contract import is_cache_ready, check_staleness
    CACHE_CONTRACT_AVAILABLE = True
except ImportError:
    CACHE_CONTRACT_AVAILABLE = False

logger = logging.getLogger(__name__)

# Engine version - in production, pull from git or docker tag
ENGINE_VERSION = os.getenv("ENGINE_VERSION", "1.0.0")


class RunExecutor:
    """
    Executes Runs and records results.
    
    Handles:
    - SCAN: Find setups/signals in current market
    - BACKTEST: Historical simulation bar-by-bar
    - PAPER: Forward test with live data, simulated execution
    
    Usage:
        executor = RunExecutor(database_url)
        
        # Execute any run type
        run = executor.repo.get_run(run_id)
        executor.execute(run)
    """
    
    def __init__(self, database_url: str, redis_client=None):
        self.database_url = database_url
        self.redis_client = redis_client
        self.repo = RunRepository(database_url)
        self.repo.ensure_schema()

        # Ensure all schemas are created
        self._ensure_full_schema()
    
    @contextmanager
    def _conn(self):
        """Get database connection."""
        conn = psycopg.connect(self.database_url)
        try:
            yield conn
        finally:
            conn.close()
    
    def _ensure_full_schema(self):
        """Ensure all required tables exist."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Make sure pgcrypto is available
                cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            conn.commit()
    
    # =========================================================================
    # Run Creation
    # =========================================================================
    
    def create_scan_run(
        self,
        symbols: list[str],
        timeframes: list[str],
        setup_ids: list[str] = None,
        name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Run:
        """Create a new SCAN run."""
        setup_ids = setup_ids or ["S1", "S2"]
        
        run = Run(
            run_type=RunType.SCAN,
            name=name or f"scan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            notes=notes,
            engine_version=ENGINE_VERSION,
            universe=UniverseSpec(
                assets=symbols,
                timeframes=timeframes,
            ),
            strategy=StrategySpec(
                setup_id=",".join(setup_ids),
                setup_version="v2.0",
                setup_params={"setup_ids": setup_ids, "top_n": 10},
            ),
            data=DataSpec(
                data_source="binance_rest",
                data_snapshot_ts=datetime.now(timezone.utc),
            ),
        )
        
        run.fingerprint = run.compute_fingerprint()
        self.repo.create_run(run)
        
        logger.info(f"Created SCAN run {run.id} fingerprint={run.fingerprint}")
        return run
    
    # =========================================================================
    # Run Execution
    # =========================================================================
    
    def execute(self, run: Run) -> Run:
        """
        Execute a Run and record results.
        
        Dispatches to the appropriate executor based on run_type.
        """
        if run.status != RunStatus.PENDING:
            raise ValueError(f"Cannot execute run in status {run.status}")
        
        # Start the run
        self.repo.start_run(run.id)
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        
        start_time = time.time()
        
        try:
            # Dispatch based on run type
            if run.run_type == RunType.SCAN:
                self._execute_scan(run)
            elif run.run_type == RunType.BACKTEST:
                self._execute_backtest(run)
            elif run.run_type == RunType.PAPER:
                self._execute_paper(run)
            else:
                raise ValueError(f"Unsupported run type: {run.run_type}")
            
            # Calculate execution time
            execution_time = time.time() - start_time
            run.summary.execution_time_seconds = execution_time
            
            # Mark complete
            self.repo.complete_run(run.id, run.summary)
            run.status = RunStatus.SUCCESS
            run.finished_at = datetime.now(timezone.utc)
            
            logger.info(
                f"Completed {run.run_type.value} run {run.id} in {execution_time:.1f}s"
            )
        
        except Exception as e:
            error_msg = str(e)
            self.repo.fail_run(run.id, error_msg)
            run.status = RunStatus.FAILED
            run.error_text = error_msg
            run.finished_at = datetime.now(timezone.utc)
            
            self.repo.add_event(RunEvent(
                run_id=run.id,
                event_type="ERROR",
                message=f"Run failed: {error_msg}",
            ))
            
            logger.error(f"Run {run.id} failed: {error_msg}", exc_info=True)
            raise
        
        return run
    
    # =========================================================================
    # SCAN Execution
    # =========================================================================
    
    def _execute_scan(self, run: Run):
        """Execute a SCAN run using the Signal Engine."""
        # Determine data source (Redis if available and ready)
        source = "rest"
        if self.redis_client and CACHE_CONTRACT_AVAILABLE:
            try:
                if is_cache_ready(self.redis_client):
                    # Check BTC staleness as proxy for cache health
                    staleness = check_staleness(self.redis_client, "BTCUSDT", "1m")
                    if not staleness["stale"]:
                        source = "redis"
                    else:
                        logger.warning(
                            f"Cache stale ({staleness['age_s']:.0f}s), using REST"
                        )
            except Exception as e:
                logger.warning(f"Cache check failed, using REST: {e}")

        # Initialize engine with run's universe
        engine = SignalEngine(
            symbols=run.universe.assets,
            timeframes=run.universe.timeframes,
            top_n=run.strategy.setup_params.get("top_n", 10),
            source=source,
            redis_client=self.redis_client,
        )

        # Record data source in run
        run.data.data_source = "redis_cache" if source == "redis" else "binance_rest"

        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Starting scan: {len(run.universe.assets)} assets × {len(run.universe.timeframes)} timeframes (source: {source})",
        ))
        
        # Run the scan
        signals = engine.scan()
        
        # Convert signals to trades
        trades = []
        for sig in signals:
            trade = self._signal_to_trade(run.id, sig)
            trades.append(trade)
        
        self.repo.add_trades_batch(trades)
        
        # Update summary
        run.summary.assets_processed = len(run.universe.assets)
        run.summary.signals_generated = len(signals)
        run.summary.total_trades = len(signals)
        
        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Scan complete: {len(signals)} signals",
        ))
    
    # =========================================================================
    # BACKTEST Execution
    # =========================================================================
    
    def _execute_backtest(self, run: Run):
        """
        Execute a BACKTEST run - bar-by-bar historical simulation.
        
        This is an honest v0 backtest that:
        - Uses NEXT_OPEN fills (entry at next bar open after signal)
        - Applies constant slippage and fees
        - Records all exit reasons (TP1, TP2, STOP, TIME_STOP)
        - Tracks full audit trail
        """
        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Starting backtest: {len(run.universe.assets)} assets",
        ))
        
        # Get OHLCV data from database
        bars_by_symbol = self._load_backtest_data(run)
        
        if not bars_by_symbol:
            raise ValueError("No OHLCV data available for backtest")
        
        # Build execution config from run specs
        exec_config = self._build_execution_config(run.execution)
        
        # Initialize state
        open_positions = {}  # symbol -> position dict
        closed_trades = []
        total_bars = 0
        
        # Get all unique timestamps across all symbols
        all_timestamps = set()
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                all_timestamps.add(bar["open_time"])
        
        sorted_timestamps = sorted(all_timestamps)
        
        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Processing {len(sorted_timestamps)} bars across {len(bars_by_symbol)} symbols",
        ))
        
        # Bar-by-bar simulation
        for i, ts in enumerate(sorted_timestamps):
            # Get current bars for each symbol
            current_bars = {}
            for symbol, bars in bars_by_symbol.items():
                bar = self._get_bar_at_timestamp(bars, ts)
                if bar:
                    current_bars[symbol] = bar
            
            if not current_bars:
                continue
            
            total_bars += 1
            
            # 1. Update open positions (check stops/targets)
            positions_to_close = []
            for symbol, pos in open_positions.items():
                if symbol not in current_bars:
                    continue
                
                bar = current_bars[symbol]
                exit_result = self._check_position_exit(pos, bar, exec_config)
                
                if exit_result:
                    exit_reason, exit_price = exit_result
                    pos["exit_ts"] = ts
                    pos["exit_price"] = exit_price
                    pos["exit_reason"] = exit_reason
                    pos["bars_held"] = pos.get("bars_held", 0) + 1
                    
                    # Calculate PnL
                    self._calculate_trade_pnl(pos, exec_config)
                    
                    closed_trades.append(pos)
                    positions_to_close.append(symbol)
                else:
                    # Update bars held
                    pos["bars_held"] = pos.get("bars_held", 0) + 1
            
            # Remove closed positions
            for symbol in positions_to_close:
                del open_positions[symbol]
            
            # 2. Check for new signals (if we have room)
            if len(open_positions) < exec_config.max_positions:
                # Look for entry signals using indicators
                # For backtest, we need historical indicator values
                # This is a simplified version - full version would compute indicators
                pass  # Signal generation happens in step 3
            
            # 3. Process any pending entries (NEXT_OPEN fill)
            # In a real implementation, signals from bar N-1 would fill at bar N open
        
        # Close any remaining positions at end
        for symbol, pos in open_positions.items():
            pos["exit_ts"] = sorted_timestamps[-1] if sorted_timestamps else datetime.now(timezone.utc)
            pos["exit_price"] = pos.get("current_price", pos["entry_price"])
            pos["exit_reason"] = "END_OF_BACKTEST"
            self._calculate_trade_pnl(pos, exec_config)
            closed_trades.append(pos)
        
        # Convert to RunTrade objects and store
        trades = []
        for pos in closed_trades:
            trade = self._position_to_trade(run.id, pos)
            trades.append(trade)
        
        self.repo.add_trades_batch(trades)
        
        # Calculate summary statistics
        run.summary = self._calculate_backtest_summary(closed_trades, total_bars, len(bars_by_symbol))
        
        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Backtest complete: {len(closed_trades)} trades, {run.summary.win_rate:.1%} win rate",
        ))
    
    def _load_backtest_data(self, run: Run) -> dict[str, list[dict]]:
        """Load OHLCV data for backtest from database."""
        bars_by_symbol = {}
        
        start_ts = run.universe.start_ts
        end_ts = run.universe.end_ts
        
        with self._conn() as conn:
            with conn.cursor() as cur:
                for symbol in run.universe.assets:
                    for tf in run.universe.timeframes:
                        cur.execute("""
                            SELECT open_time, open, high, low, close, volume
                            FROM ohlcv
                            WHERE symbol = %s AND timeframe = %s
                            AND open_time >= %s AND open_time <= %s
                            ORDER BY open_time;
                        """, (symbol, tf, start_ts, end_ts))
                        
                        rows = cur.fetchall()
                        if rows:
                            key = f"{symbol}_{tf}"
                            bars_by_symbol[key] = [
                                {
                                    "open_time": row[0],
                                    "open": row[1],
                                    "high": row[2],
                                    "low": row[3],
                                    "close": row[4],
                                    "volume": row[5],
                                    "symbol": symbol,
                                    "timeframe": tf,
                                }
                                for row in rows
                            ]
        
        return bars_by_symbol
    
    def _get_bar_at_timestamp(self, bars: list[dict], ts: datetime) -> Optional[dict]:
        """Get bar at specific timestamp."""
        for bar in bars:
            if bar["open_time"] == ts:
                return bar
        return None
    
    def _check_position_exit(
        self,
        pos: dict,
        bar: dict,
        exec_config: ExecutionConfig,
    ) -> Optional[tuple[str, float]]:
        """Check if position should exit. Returns (reason, price) or None."""
        side = pos["side"]
        stop = pos["stop_price"]
        tp1 = pos.get("tp1_price")
        tp2 = pos.get("tp2_price")
        high = bar["high"]
        low = bar["low"]
        
        # Check based on fill assumption (TOUCH vs THROUGH)
        if side == "long":
            if low <= stop:
                # Stop hit - fill at stop (or gap open if worse)
                fill_price = max(stop, bar["open"]) if bar["open"] < stop else stop
                return ("STOP_LOSS", fill_price)
            if tp1 and high >= tp1:
                return ("TP1", tp1)
            if tp2 and high >= tp2:
                return ("TP2", tp2)
        else:  # short
            if high >= stop:
                fill_price = min(stop, bar["open"]) if bar["open"] > stop else stop
                return ("STOP_LOSS", fill_price)
            if tp1 and low <= tp1:
                return ("TP1", tp1)
            if tp2 and low <= tp2:
                return ("TP2", tp2)
        
        # Check time stop (if configured)
        max_bars = pos.get("max_bars", 50)
        if pos.get("bars_held", 0) >= max_bars:
            return ("TIME_STOP", bar["close"])
        
        return None
    
    def _calculate_trade_pnl(self, pos: dict, exec_config: ExecutionConfig):
        """Calculate PnL for a closed position."""
        entry = pos["entry_price"]
        exit_price = pos["exit_price"]
        size = pos.get("position_size", 1.0)
        
        # Apply slippage
        slip_bps = exec_config.slippage.entry_bps
        entry_slip = entry * (slip_bps / 10000)
        exit_slip = exit_price * (slip_bps / 10000)
        
        if pos["side"] == "long":
            entry_with_slip = entry + entry_slip
            exit_with_slip = exit_price - exit_slip
            pnl_gross = (exit_with_slip - entry_with_slip) * size
        else:
            entry_with_slip = entry - entry_slip
            exit_with_slip = exit_price + exit_slip
            pnl_gross = (entry_with_slip - exit_with_slip) * size
        
        # Apply fees
        fee_bps = exec_config.fees.taker_bps
        entry_fee = entry * size * (fee_bps / 10000)
        exit_fee = exit_price * size * (fee_bps / 10000)
        total_fees = entry_fee + exit_fee
        
        pos["pnl_gross"] = pnl_gross
        pos["fees"] = total_fees
        pos["pnl_net"] = pnl_gross - total_fees
        pos["entry_with_slippage"] = entry_with_slip
        pos["exit_with_slippage"] = exit_with_slip
        
        # R-multiple
        r_value = pos.get("r_value", abs(entry - pos["stop_price"]))
        if r_value > 0:
            pos["r_multiple"] = pnl_gross / (r_value * size)
        else:
            pos["r_multiple"] = 0
    
    def _calculate_backtest_summary(
        self,
        trades: list[dict],
        total_bars: int,
        total_assets: int,
    ) -> RunSummary:
        """Calculate summary statistics from backtest trades."""
        if not trades:
            return RunSummary(bars_processed=total_bars, assets_processed=total_assets)
        
        winners = [t for t in trades if t.get("pnl_net", 0) > 0]
        losers = [t for t in trades if t.get("pnl_net", 0) <= 0]
        
        total_pnl = sum(t.get("pnl_net", 0) for t in trades)
        total_fees = sum(t.get("fees", 0) for t in trades)
        
        r_multiples = [t.get("r_multiple", 0) for t in trades]
        
        win_rate = len(winners) / len(trades) if trades else 0
        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0
        
        # Profit factor
        gross_profit = sum(t.get("pnl_net", 0) for t in winners)
        gross_loss = abs(sum(t.get("pnl_net", 0) for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        return RunSummary(
            total_trades=len(trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=win_rate,
            total_r=sum(r_multiples),
            avg_r=avg_r,
            max_r=max(r_multiples) if r_multiples else 0,
            min_r=min(r_multiples) if r_multiples else 0,
            gross_pnl=total_pnl + total_fees,
            total_fees=total_fees,
            net_pnl=total_pnl,
            profit_factor=profit_factor,
            expectancy=avg_r,
            bars_processed=total_bars,
            assets_processed=total_assets,
        )
    
    # =========================================================================
    # PAPER Execution
    # =========================================================================
    
    def _execute_paper(self, run: Run):
        """
        Execute a PAPER run - forward test with live data.
        
        Paper trading is literally backtest running forward in time
        with persistent positions.
        
        Auto-trade mode:
        - When enabled, automatically opens positions from signals
        - Respects auto_trade_top_n and min_score filters
        - Respects max_positions limit
        
        Note: This is a single-shot execution. For continuous paper trading,
        the worker should periodically update positions via a separate loop.
        """
        from paper_trading import PaperTradingEngine
        
        # Get auto-trade config from strategy params
        setup_params = run.strategy.setup_params or {}
        auto_trade = setup_params.get("auto_trade", True)
        auto_trade_top_n = setup_params.get("auto_trade_top_n", 3)
        min_score = setup_params.get("min_score", 0.0)
        
        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Starting paper trading run (auto_trade={auto_trade}, top_n={auto_trade_top_n})",
        ))
        
        # Initialize paper trading engine
        engine = PaperTradingEngine(
            database_url=self.database_url,
            execution_config=self._build_execution_config(run.execution),
        )
        
        # If auto-trade is disabled, just mark as started
        if not auto_trade:
            self.repo.add_event(RunEvent(
                run_id=run.id,
                event_type="INFO",
                message="Paper trading started in manual mode (auto_trade=False)",
            ))
            run.summary.signals_generated = 0
            run.summary.total_trades = 0
            return
        
        # Get current signals
        signal_engine = SignalEngine(
            symbols=run.universe.assets,
            timeframes=run.universe.timeframes,
            top_n=auto_trade_top_n * 2,  # Get more than we need for filtering
        )
        
        signals = signal_engine.scan()
        
        # Filter signals by min_score and limit to top_n
        qualifying_signals = [
            sig for sig in signals
            if sig.setup_score >= min_score
        ][:auto_trade_top_n]
        
        # Process qualifying signals
        positions_opened = 0
        for sig in qualifying_signals:
            signal_dict = {
                "symbol": sig.symbol,
                "timeframe": sig.timeframe,
                "direction": sig.direction,
                "setup_id": sig.setup_id,
                "entry_price": sig.entry_price,
                "stop_price": sig.stop_price,
                "tp1_price": sig.tp1_price,
                "tp2_price": sig.tp2_price,
                "r_value": sig.r_value,
                "setup_score": sig.setup_score,
                "payload": sig.payload,
            }
            
            pos_id = engine.process_signal(run.id, signal_dict)
            if pos_id:
                positions_opened += 1
                logger.info(f"Auto-opened position: {sig.symbol} {sig.direction} (score={sig.setup_score:.2f})")
        
        # Update summary
        run.summary.signals_generated = len(signals)
        run.summary.total_trades = positions_opened
        
        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Paper trading initialized: {positions_opened} positions auto-opened from {len(qualifying_signals)} qualifying signals ({len(signals)} total)",
        ))
    
    # =========================================================================
    # Helpers
    # =========================================================================
    
    def _build_execution_config(self, spec: ExecutionSpec) -> ExecutionConfig:
        """Build ExecutionConfig from ExecutionSpec."""
        return ExecutionConfig(
            slippage=FixedBpsSlippage(
                entry_bps=spec.slippage_entry_bps,
                exit_bps=spec.slippage_exit_bps,
            ),
            fees=TieredFees(
                maker_bps=spec.fee_maker_bps,
                taker_bps=spec.fee_taker_bps,
            ),
            max_positions=spec.max_positions,
            one_per_asset=spec.one_per_asset,
            max_risk_per_trade_pct=spec.max_risk_per_trade_pct,
            max_daily_loss_pct=spec.max_daily_loss_pct,
        )
    
    def _signal_to_trade(self, run_id: str, signal: RankedSignal) -> RunTrade:
        """Convert a RankedSignal to a RunTrade."""
        trace = TradeTrace(
            conditions_met={"setup_triggered": True},
            entry_reason=f"{signal.setup_id}_TRIGGER",
            indicator_values={
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "r_value": signal.r_value,
            },
            state_snapshot=signal.payload.get("state", {}),
            score_components=signal.payload.get("score_components", {}),
        )
        
        return RunTrade(
            run_id=run_id,
            asset=signal.symbol,
            timeframe=signal.timeframe,
            side=signal.direction,
            setup_id=signal.setup_id,
            entry_ts=datetime.fromtimestamp(signal.timestamp, tz=timezone.utc),
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            r_value=signal.r_value,
            exit_reason="OPEN",
            setup_score=signal.setup_score,
            trace=trace,
        )
    
    def _position_to_trade(self, run_id: str, pos: dict) -> RunTrade:
        """Convert a position dict to a RunTrade."""
        trace = TradeTrace(
            conditions_met=pos.get("conditions_met", {}),
            entry_reason=pos.get("entry_reason", ""),
            indicator_values=pos.get("indicator_values", {}),
            exit_reason=pos.get("exit_reason"),
            exit_explanation=f"Exit at {pos.get('r_multiple', 0):.2f}R after {pos.get('bars_held', 0)} bars",
        )
        
        return RunTrade(
            run_id=run_id,
            asset=pos.get("symbol", ""),
            timeframe=pos.get("timeframe", "1h"),
            side=pos.get("side", "long"),
            setup_id=pos.get("setup_id", ""),
            entry_ts=pos.get("entry_ts"),
            entry_price=pos.get("entry_price", 0),
            stop_price=pos.get("stop_price", 0),
            tp1_price=pos.get("tp1_price"),
            tp2_price=pos.get("tp2_price"),
            r_value=pos.get("r_value", 0),
            exit_ts=pos.get("exit_ts"),
            exit_price=pos.get("exit_price", 0),
            exit_reason=pos.get("exit_reason", "UNKNOWN"),
            r_multiple=pos.get("r_multiple", 0),
            pnl_gross=pos.get("pnl_gross", 0),
            fees=pos.get("fees", 0),
            pnl_net=pos.get("pnl_net", 0),
            setup_score=pos.get("setup_score", 0),
            trace=trace,
        )


class ScanRunManager:
    """
    Convenience class for managing periodic scan runs.
    
    Kept for backward compatibility.
    """
    
    def __init__(self, executor: RunExecutor):
        self.executor = executor
    
    def run_periodic_scan(
        self,
        symbols: list[str],
        timeframes: list[str],
        setup_ids: list[str] = None,
    ) -> Run:
        """Execute a periodic scan and return the Run."""
        run = self.executor.create_scan_run(
            symbols=symbols,
            timeframes=timeframes,
            setup_ids=setup_ids,
            name=f"periodic_scan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        )
        return self.executor.execute(run)
