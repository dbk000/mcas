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

import hashlib

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
from indicators import FeatureFactory, IndicatorSnapshot, OHLCV
from setups import S1Evaluator, S1Signal, S2Evaluator, S2Signal
from setups import S3Evaluator, S3Signal, S4Evaluator, S4Signal
from setups import S5Evaluator, S5Signal, S6Evaluator, S6Signal
from setups import S7Evaluator, S7Signal
from exit_checker import check_exit, ExitConfig, PositionState
from opportunity_simulator import OpportunitySimulator, SimulationConfig

logger = logging.getLogger(__name__)

# Engine version - in production, pull from git or docker tag
ENGINE_VERSION = os.getenv("ENGINE_VERSION", "1.0.0")


def _build_why_items(signal: RankedSignal) -> list[str]:
    """
    Canon 7A: Build 3-8 explainability items from signal payload.

    Synthesises human-readable "why" strings from state + score components
    when the setup didn't provide them explicitly.
    """
    items: list[str] = []
    state = signal.payload.get("state", {})
    comps = signal.payload.get("score_components", {})
    setup = signal.setup_id

    if setup == "S1":
        if state.get("squeeze_on"):
            items.append(f"BB squeeze ON for {state.get('squeeze_age', '?')} bars")
        pctile = state.get("bb_width_pctile")
        if pctile is not None:
            items.append(f"BB width at {pctile:.0f}th percentile (tight)")
        if comps.get("participation"):
            items.append(f"Volume participation: {comps['participation']:.0%}")
        if comps.get("break_quality"):
            items.append(f"Break quality: {comps['break_quality']:.0%}")
        if comps.get("clean_close"):
            items.append(f"Clean close score: {comps['clean_close']:.0%}")
        bias = state.get("bias_score")
        if bias is not None:
            items.append(f"Bias score: {bias:+.1f}")
    elif setup == "S2":
        if state.get("converging"):
            items.append("SMA50/200 converging from wide to narrow")
        gap = state.get("ma_gap_atr")
        if gap is not None:
            items.append(f"MA gap: {gap:.2f} ATR (narrow)")
        if comps.get("conv_speed"):
            items.append(f"Convergence speed: {comps['conv_speed']:.0%}")
        if comps.get("trend_strength"):
            items.append(f"Trend strength: {comps['trend_strength']:.0%}")
        if comps.get("participation"):
            items.append(f"Volume participation: {comps['participation']:.0%}")
    else:
        items.append(f"Setup {setup} triggered")
        items.append(f"Score: {signal.setup_score:.2f}")
        items.append(f"Direction: {signal.direction}")

    # Pad to minimum 3
    while len(items) < 3:
        items.append(f"Score: {signal.setup_score:.2f}")

    return items[:8]


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
        top_n: int = None,
    ) -> Run:
        """Create a new SCAN run."""
        setup_ids = setup_ids or ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "U1"]

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
                setup_params={"setup_ids": setup_ids},
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
            elif run.run_type == RunType.SIMULATION:
                self._execute_simulation(run)
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
        # Determine data source: prefer Redis if cache is ready
        source = "rest"
        if self.redis_client:
            from cache_contract import is_cache_ready
            if is_cache_ready(self.redis_client):
                source = "redis"

        # Initialize engine with run's universe
        engine = SignalEngine(
            symbols=run.universe.assets,
            timeframes=run.universe.timeframes,
            top_n=run.strategy.setup_params.get("top_n"),
            source=source,
            redis_client=self.redis_client,
            run_id=run.id,
        )

        # Record data source on the run
        run.data.data_source = "redis_cache" if source == "redis" else "binance_rest"

        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Starting scan: {len(run.universe.assets)} assets × {len(run.universe.timeframes)} timeframes (source={source})",
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

        Resolves P0 blockers:
        - P0-1: Signal generation via FeatureFactory + S1/S2 evaluators
        - P0-2: NEXT_OPEN pending-entry queue (signal on bar N, fill at bar N+1 open)
        - P0-3: Position sizing via risk_amount / r_value
        - P0-4: Virtual account with equity tracking and drawdown
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

        # Canon 2C: compute and store data checksum for reproducibility
        ohlcv_checksum = self._compute_ohlcv_checksum(bars_by_symbol)
        run.data.ohlcv_checksum = ohlcv_checksum
        run.data.data_version = f"backtest_{run.id[:8]}"
        run.data.data_snapshot_ts = datetime.now(timezone.utc)
        self._update_run_data_json(run)

        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Data checksum: {ohlcv_checksum[:16]}",
            payload={"ohlcv_checksum": ohlcv_checksum, "data_version": run.data.data_version},
        ))

        # Build execution config from run specs
        exec_config = self._build_execution_config(run.execution)

        # Determine which setups to run
        setup_params = run.strategy.setup_params or {}
        active_setup_ids = set(setup_params.get("setup_ids", ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "U1"]))

        # =====================================================================
        # P0-4: Virtual account
        # =====================================================================
        starting_capital = run.execution.starting_capital
        capital = starting_capital
        peak_equity = starting_capital
        max_drawdown_pct = 0.0
        equity_curve = []
        daily_pnl: dict = {}  # date_str -> float (for circuit breaker)

        # =====================================================================
        # P0-1: Setup evaluators and per-symbol state
        # =====================================================================
        feature_factory = FeatureFactory()
        s1_evaluator = S1Evaluator()
        s2_evaluator = S2Evaluator()
        s3_evaluator = S3Evaluator()
        s4_evaluator = S4Evaluator()
        s5_evaluator = S5Evaluator()
        s6_evaluator = S6Evaluator()
        s7_evaluator = S7Evaluator()

        candle_history: dict[str, list] = {}       # key -> list[OHLCV]
        bb_width_history: dict[str, list] = {}     # key -> list[float]
        ma_gap_history: dict[str, list] = {}       # key -> list[float]
        vwap_distance_history: dict[str, list] = {} # key -> list[float]
        s1_prev_state: dict[str, object] = {}      # key -> S1State
        s6_prev_state: dict[str, object] = {}      # key -> S6State
        s7_prev_snapshot: dict[str, object] = {}   # key -> IndicatorSnapshot

        # =====================================================================
        # P0-2: Pending entry queue (NEXT_OPEN fills)
        # =====================================================================
        pending_entries: list[dict] = []

        # Pre-index bars for O(1) lookup (also fixes P1-5 O(N) scan)
        bar_index: dict[str, dict] = {}
        for key, bars in bars_by_symbol.items():
            bar_index[key] = {bar["bar_start_ts"]: bar for bar in bars}

        open_positions: dict[str, dict] = {}  # pos_key -> position dict
        closed_trades: list[dict] = []
        total_bars = 0
        signals_generated = 0
        signals_per_setup = {s: 0 for s in ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "U1"]}
        skip_counters = {"max_positions": 0, "one_per_asset": 0, "circuit_breaker": 0, "invalid_r_value": 0, "position_size_zero": 0}

        # Collect all unique timestamps and sort
        all_timestamps: set = set()
        for bars in bars_by_symbol.values():
            for bar in bars:
                all_timestamps.add(bar["bar_start_ts"])
        sorted_timestamps = sorted(all_timestamps)

        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Processing {len(sorted_timestamps)} bars across {len(bars_by_symbol)} symbols",
        ))

        # Progress reporting (every 10%)
        progress_interval = max(1, len(sorted_timestamps) // 10)

        # =================================================================
        # Bar-by-bar simulation
        # =================================================================
        for bar_idx, ts in enumerate(sorted_timestamps):
            # Gather current bars across all symbol_tf keys
            current_bars: dict[str, dict] = {}
            for key in bars_by_symbol:
                bar = bar_index[key].get(ts)
                if bar:
                    current_bars[key] = bar

            if not current_bars:
                continue

            total_bars += 1

            # Progress event
            if bar_idx > 0 and bar_idx % progress_interval == 0:
                pct = int(bar_idx / len(sorted_timestamps) * 100)
                self.repo.add_event(RunEvent(
                    run_id=run.id,
                    event_type="INFO",
                    message=f"Progress: {pct}% ({bar_idx}/{len(sorted_timestamps)} bars, {len(closed_trades)} trades)",
                ))

            # ---------------------------------------------------------
            # Step 1: Fill pending entries at this bar's open (NEXT_OPEN)
            # ---------------------------------------------------------
            fill_queue = pending_entries
            pending_entries = []

            # Sort by score so best signals fill first
            fill_queue.sort(key=lambda e: e["setup_score"], reverse=True)

            today_str = ts.isoformat()[:10] if hasattr(ts, "isoformat") else str(ts)[:10]

            for entry in fill_queue:
                # Max positions check
                if len(open_positions) >= exec_config.max_positions:
                    skip_counters["max_positions"] += 1
                    break

                sym_key = f"{entry['symbol']}_{entry['timeframe']}"
                if sym_key not in current_bars:
                    continue

                # One-per-asset check
                if exec_config.one_per_asset:
                    existing_symbols = {p["symbol"] for p in open_positions.values()}
                    if entry["symbol"] in existing_symbols:
                        skip_counters["one_per_asset"] += 1
                        continue

                # Daily loss circuit breaker
                if daily_pnl.get(today_str, 0.0) <= -(starting_capital * exec_config.max_daily_loss_pct / 100.0):
                    skip_counters["circuit_breaker"] += 1
                    continue

                bar = current_bars[sym_key]
                fill_price = bar["open"]

                # Apply slippage to entry
                side = entry["direction"]
                fill_price = exec_config.apply_entry_slippage(fill_price, side)

                # Recalculate R with actual fill price
                stop_price = entry["stop_price"]
                if side == "long":
                    r_value = fill_price - stop_price
                else:
                    r_value = stop_price - fill_price

                if r_value <= 0:
                    skip_counters["invalid_r_value"] += 1
                    continue

                # P0-3: Position sizing = risk_amount / r_value
                risk_amount = capital * (exec_config.max_risk_per_trade_pct / 100.0)
                position_size = risk_amount / r_value
                notional = fill_price * position_size

                # Cap notional at available capital
                if notional > capital * (exec_config.max_exposure_pct / 100.0):
                    position_size = capital * (exec_config.max_exposure_pct / 100.0) / fill_price
                    notional = fill_price * position_size

                if position_size <= 0:
                    skip_counters["position_size_zero"] += 1
                    continue

                pos_key = f"{entry['symbol']}_{entry['timeframe']}_{bar_idx}"
                open_positions[pos_key] = {
                    "symbol": entry["symbol"],
                    "timeframe": entry["timeframe"],
                    "side": side,
                    "setup_id": entry["setup_id"],
                    "entry_price": fill_price,
                    "entry_ts": ts,
                    "stop_price": stop_price,
                    "tp1_price": entry["tp1_price"],
                    "tp2_price": entry["tp2_price"],
                    "r_value": r_value,
                    "position_size": position_size,
                    "bars_held": 0,
                    "max_bars": entry.get("time_stop_bars", 50),
                    "setup_score": entry["setup_score"],
                    "signal_bar_ts": entry.get("signal_bar_ts"),
                    "since_ts": entry.get("since_ts"),
                    "action_by_ts": entry.get("action_by_ts"),
                    "entry_reason": f"{entry['setup_id']}_TRIGGER",
                    "conditions_met": {"setup_triggered": True},
                    "indicator_values": entry.get("indicator_values", {}),
                    "state_snapshot": entry.get("state_snapshot", {}),
                    "score_components": entry.get("score_components", {}),
                    "why_items": entry.get("why_items", []),
                }

            # ---------------------------------------------------------
            # Step 2: Check exits on open positions
            # ---------------------------------------------------------
            positions_to_close: list[str] = []
            new_remainder_positions: dict[str, dict] = {}
            for pos_key, pos in open_positions.items():
                sym_key = f"{pos['symbol']}_{pos['timeframe']}"
                if sym_key not in current_bars:
                    continue

                bar = current_bars[sym_key]
                exit_result = self._check_position_exit(pos, bar, exec_config)

                if exit_result:
                    exit_reason, exit_price = exit_result
                    pos["bars_held"] = pos.get("bars_held", 0) + 1

                    # P4-24: Partial close at TP1 — close 50%, trail remainder to TP2
                    if exit_reason == "TP1" and pos.get("tp2_price") and not pos.get("_is_remainder"):
                        full_size = pos["position_size"]
                        close_size = full_size * 0.5

                        # Close the TP1 half
                        tp1_pos = dict(pos)
                        tp1_pos["position_size"] = close_size
                        tp1_pos["exit_ts"] = ts
                        tp1_pos["exit_price"] = exit_price
                        tp1_pos["exit_reason"] = "TP1"
                        self._calculate_trade_pnl(tp1_pos, exec_config)
                        capital += tp1_pos["pnl_net"]
                        daily_pnl[today_str] = daily_pnl.get(today_str, 0.0) + tp1_pos["pnl_net"]
                        closed_trades.append(tp1_pos)

                        # Create remainder position trailing to TP2
                        remainder = dict(pos)
                        remainder["position_size"] = full_size - close_size
                        remainder["_is_remainder"] = True
                        # Move stop to entry (breakeven) for remainder
                        remainder["stop_price"] = pos["entry_price"]
                        remainder_key = f"{pos_key}_rem"
                        new_remainder_positions[remainder_key] = remainder
                        positions_to_close.append(pos_key)
                    else:
                        pos["exit_ts"] = ts
                        pos["exit_price"] = exit_price
                        pos["exit_reason"] = exit_reason
                        self._calculate_trade_pnl(pos, exec_config)
                        capital += pos["pnl_net"]
                        daily_pnl[today_str] = daily_pnl.get(today_str, 0.0) + pos["pnl_net"]
                        closed_trades.append(pos)
                        positions_to_close.append(pos_key)
                else:
                    pos["bars_held"] = pos.get("bars_held", 0) + 1

            for pos_key in positions_to_close:
                del open_positions[pos_key]
            # Add remainder positions from TP1 partial closes
            open_positions.update(new_remainder_positions)

            # ---------------------------------------------------------
            # Step 3: Generate signals (accumulate history, compute
            #          indicators, run S1/S2 evaluators)
            # ---------------------------------------------------------
            for key in bars_by_symbol:
                if key not in current_bars:
                    continue

                bar = current_bars[key]
                symbol = bar["symbol"]
                timeframe = bar["timeframe"]

                # Accumulate candle history
                if key not in candle_history:
                    candle_history[key] = []

                # Convert bar dict to OHLCV
                bar_ts = bar["bar_start_ts"]
                ts_ms = int(bar_ts.timestamp() * 1000) if hasattr(bar_ts, "timestamp") else int(bar_ts)
                candle_history[key].append(OHLCV(
                    timestamp=ts_ms,
                    open=bar["open"],
                    high=bar["high"],
                    low=bar["low"],
                    close=bar["close"],
                    volume=bar["volume"],
                ))

                # Trim to 500 bars
                if len(candle_history[key]) > 500:
                    candle_history[key] = candle_history[key][-500:]

                # Need 201 bars warmup
                if len(candle_history[key]) < 201:
                    continue

                # Skip signal generation if max positions already open
                if len(open_positions) >= exec_config.max_positions:
                    continue

                # One-per-asset: skip if symbol already has an open position
                if exec_config.one_per_asset:
                    open_symbols = {p["symbol"] for p in open_positions.values()}
                    if symbol in open_symbols:
                        continue

                # Compute indicators
                try:
                    snapshot = feature_factory.compute(candle_history[key])
                except Exception:
                    continue

                # Update indicator caches
                if key not in bb_width_history:
                    bb_width_history[key] = []
                bb_width_history[key].append(snapshot.bb_width)
                if len(bb_width_history[key]) > 200:
                    bb_width_history[key] = bb_width_history[key][-200:]

                if key not in ma_gap_history:
                    ma_gap_history[key] = []
                ma_gap_history[key].append(snapshot.ma_gap_atr)
                if len(ma_gap_history[key]) > 200:
                    ma_gap_history[key] = ma_gap_history[key][-200:]

                if snapshot.vwap_distance_atr is not None:
                    if key not in vwap_distance_history:
                        vwap_distance_history[key] = []
                    vwap_distance_history[key].append(abs(snapshot.vwap_distance_atr))
                    if len(vwap_distance_history[key]) > 200:
                        vwap_distance_history[key] = vwap_distance_history[key][-200:]

                # BTC snapshot for correlation/shock filters
                btc_snapshot = None
                btc_key = f"BTCUSDT_{timeframe}"
                if symbol != "BTCUSDT" and btc_key in candle_history and len(candle_history[btc_key]) >= 201:
                    try:
                        btc_snapshot = feature_factory.compute(candle_history[btc_key])
                    except Exception:
                        pass

                # ------ S1: BB Squeeze ON ------
                if "S1" in active_setup_ids:
                    btc_bb_hist = bb_width_history.get(btc_key)
                    s1_state = s1_evaluator.compute_state(
                        snapshot,
                        bb_width_history[key],
                        s1_prev_state.get(key),
                        btc_snapshot,
                        btc_bb_hist,
                    )
                    s1_prev_state[key] = s1_state

                    s1_signal = s1_evaluator.evaluate_trigger(snapshot, s1_state, symbol, timeframe)
                    if s1_signal:
                        signals_generated += 1
                        signals_per_setup["S1"] += 1
                        time_stop = s1_signal.time_stop_bars
                        pending_entries.append({
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "direction": s1_signal.direction.value,
                            "setup_id": "S1",
                            "stop_price": s1_signal.stop_price,
                            "tp1_price": s1_signal.tp1_price,
                            "tp2_price": s1_signal.tp2_price,
                            "r_value": s1_signal.r_value,
                            "setup_score": s1_signal.setup_score,
                            "time_stop_bars": time_stop,
                            "signal_bar_ts": ts,
                            "since_ts": ts,
                            "action_by_ts": sorted_timestamps[min(bar_idx + time_stop, len(sorted_timestamps) - 1)] if bar_idx + time_stop < len(sorted_timestamps) else None,
                            "indicator_values": {
                                "bb_width_pctile": s1_state.bb_width_pctile,
                                "squeeze_age": s1_state.squeeze_age,
                                "clv": snapshot.clv,
                                "volume_z": snapshot.volume_z,
                            },
                            "state_snapshot": {k: (v.value if hasattr(v, 'value') else v) for k, v in asdict(s1_state).items()},
                            "score_components": {
                                "severity": s1_signal.severity,
                                "bias_strength": s1_signal.bias_strength,
                                "participation": s1_signal.participation,
                                "break_quality": s1_signal.break_quality,
                                "clean_close": s1_signal.clean_close,
                            },
                            "why_items": s1_signal.explain.why_items if s1_signal.explain else [],
                        })

                # ------ S2: Golden Cross Anticipation ------
                if "S2" in active_setup_ids:
                    s2_state = s2_evaluator.compute_state(
                        snapshot,
                        ma_gap_history[key],
                        btc_snapshot=btc_snapshot,
                    )

                    s2_signal = s2_evaluator.evaluate_trigger(snapshot, s2_state, symbol, timeframe)
                    if s2_signal:
                        signals_generated += 1
                        signals_per_setup["S2"] += 1
                        time_stop = s2_signal.time_stop_bars
                        pending_entries.append({
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "direction": s2_signal.direction.value,
                            "setup_id": "S2",
                            "stop_price": s2_signal.stop_price,
                            "tp1_price": s2_signal.tp1_price,
                            "tp2_price": s2_signal.tp2_price,
                            "r_value": s2_signal.r_value,
                            "setup_score": s2_signal.setup_score,
                            "time_stop_bars": time_stop,
                            "signal_bar_ts": ts,
                            "since_ts": ts,
                            "action_by_ts": sorted_timestamps[min(bar_idx + time_stop, len(sorted_timestamps) - 1)] if bar_idx + time_stop < len(sorted_timestamps) else None,
                            "indicator_values": {
                                "ma_gap_atr": s2_state.ma_gap_atr,
                                "dec_count": s2_state.dec_count,
                                "clv": snapshot.clv,
                                "volume_z": snapshot.volume_z,
                            },
                            "state_snapshot": asdict(s2_state),
                            "score_components": {
                                "conv_tight": s2_signal.conv_tight,
                                "conv_speed": s2_signal.conv_speed,
                                "trend_strength": s2_signal.trend_strength,
                                "participation": s2_signal.participation,
                                "break_quality": s2_signal.break_quality,
                                "clean_close": s2_signal.clean_close,
                            },
                            "why_items": s2_signal.explain.why_items if s2_signal.explain else [],
                        })

                # ------ S3: RSI Divergence Reversal ------
                if "S3" in active_setup_ids and len(candle_history[key]) >= 50:
                    try:
                        import numpy as _np
                        _rsi_series = feature_factory._rsi_series(
                            _np.array([c.close for c in candle_history[key]]), 14
                        )
                        s3_state = s3_evaluator.compute_state(snapshot, _rsi_series)
                        s3_signal = s3_evaluator.evaluate_trigger(snapshot, s3_state, symbol, timeframe)
                    except Exception as e:
                        logger.debug(f"S3 backtest eval error {symbol}:{timeframe}: {e}")
                        s3_signal = None
                    if s3_signal:
                        signals_generated += 1
                        signals_per_setup["S3"] += 1
                        time_stop = s3_signal.time_stop_bars
                        div = s3_state.bullish_divergence or s3_state.bearish_divergence
                        pending_entries.append({
                            "symbol": symbol, "timeframe": timeframe,
                            "direction": s3_signal.direction.value,
                            "setup_id": "S3",
                            "stop_price": s3_signal.stop_price,
                            "tp1_price": s3_signal.tp1_price,
                            "tp2_price": s3_signal.tp2_price,
                            "r_value": s3_signal.r_value,
                            "setup_score": s3_signal.setup_score,
                            "time_stop_bars": time_stop,
                            "signal_bar_ts": ts, "since_ts": ts,
                            "action_by_ts": sorted_timestamps[min(bar_idx + time_stop, len(sorted_timestamps) - 1)] if bar_idx + time_stop < len(sorted_timestamps) else None,
                            "indicator_values": {
                                "rsi": snapshot.rsi, "rsi_slope": snapshot.rsi_slope,
                                "clv": snapshot.clv, "volume_z": snapshot.volume_z,
                            },
                            "score_components": {
                                "div_magnitude": s3_signal.div_magnitude,
                                "rsi_extremity": s3_signal.rsi_extremity,
                                "participation": s3_signal.participation,
                                "clean_close": s3_signal.clean_close,
                                "span_quality": s3_signal.span_quality,
                            },
                            "why_items": s3_signal.explain.why_items if s3_signal.explain else [],
                        })

                # ------ S4: VWAP Reversion (intraday only) ------
                if "S4" in active_setup_ids:
                    vwap_hist = vwap_distance_history.get(key, [])
                    s4_state = s4_evaluator.compute_state(snapshot, vwap_hist)
                    s4_signal = s4_evaluator.evaluate_trigger(snapshot, s4_state, symbol, timeframe)
                    if s4_signal:
                        signals_generated += 1
                        signals_per_setup["S4"] += 1
                        time_stop = s4_signal.time_stop_bars
                        pending_entries.append({
                            "symbol": symbol, "timeframe": timeframe,
                            "direction": s4_signal.direction.value,
                            "setup_id": "S4",
                            "stop_price": s4_signal.stop_price,
                            "tp1_price": s4_signal.tp1_price,
                            "tp2_price": s4_signal.tp2_price,
                            "r_value": s4_signal.r_value,
                            "setup_score": s4_signal.setup_score,
                            "time_stop_bars": time_stop,
                            "signal_bar_ts": ts, "since_ts": ts,
                            "action_by_ts": sorted_timestamps[min(bar_idx + time_stop, len(sorted_timestamps) - 1)] if bar_idx + time_stop < len(sorted_timestamps) else None,
                            "indicator_values": {
                                "vwap_distance_atr": snapshot.vwap_distance_atr,
                                "rsi": snapshot.rsi, "clv": snapshot.clv,
                                "volume_z": snapshot.volume_z,
                            },
                            "score_components": {
                                "extension_magnitude": s4_signal.extension_magnitude,
                                "extreme_pctile": s4_signal.extreme_pctile,
                                "momentum_fade_strength": s4_signal.momentum_fade_strength,
                                "clean_close": s4_signal.clean_close,
                                "volume_fade": s4_signal.volume_fade,
                            },
                            "why_items": s4_signal.explain.why_items if s4_signal.explain else [],
                        })

                # ------ S5: S/R Breakout ------
                if "S5" in active_setup_ids and len(candle_history[key]) >= 50:
                    s5_state = s5_evaluator.compute_state(snapshot)
                    s5_signal = s5_evaluator.evaluate_trigger(snapshot, s5_state, symbol, timeframe)
                    if s5_signal:
                        signals_generated += 1
                        signals_per_setup["S5"] += 1
                        time_stop = s5_signal.time_stop_bars
                        pending_entries.append({
                            "symbol": symbol, "timeframe": timeframe,
                            "direction": s5_signal.direction.value,
                            "setup_id": "S5",
                            "stop_price": s5_signal.stop_price,
                            "tp1_price": s5_signal.tp1_price,
                            "tp2_price": s5_signal.tp2_price,
                            "r_value": s5_signal.r_value,
                            "setup_score": s5_signal.setup_score,
                            "time_stop_bars": time_stop,
                            "signal_bar_ts": ts, "since_ts": ts,
                            "action_by_ts": sorted_timestamps[min(bar_idx + time_stop, len(sorted_timestamps) - 1)] if bar_idx + time_stop < len(sorted_timestamps) else None,
                            "indicator_values": {
                                "clv": snapshot.clv, "volume_z": snapshot.volume_z,
                                "atr": snapshot.atr,
                            },
                            "score_components": {
                                "level_strength": s5_signal.level_strength,
                                "participation": s5_signal.participation,
                                "htf_confluence": s5_signal.htf_confluence,
                                "clean_close": s5_signal.clean_close,
                                "recency": s5_signal.recency,
                            },
                            "why_items": s5_signal.explain.why_items if s5_signal.explain else [],
                        })

                # ------ S6: Trend Continuation Pullback ------
                if "S6" in active_setup_ids:
                    s6_state = s6_evaluator.compute_state(snapshot, s6_prev_state.get(key))
                    s6_prev_state[key] = s6_state
                    s6_signal = s6_evaluator.evaluate_trigger(snapshot, s6_state, symbol, timeframe)
                    if s6_signal:
                        signals_generated += 1
                        signals_per_setup["S6"] += 1
                        time_stop = s6_signal.time_stop_bars
                        pending_entries.append({
                            "symbol": symbol, "timeframe": timeframe,
                            "direction": s6_signal.direction.value,
                            "setup_id": "S6",
                            "stop_price": s6_signal.stop_price,
                            "tp1_price": s6_signal.tp1_price,
                            "tp2_price": s6_signal.tp2_price,
                            "r_value": s6_signal.r_value,
                            "setup_score": s6_signal.setup_score,
                            "time_stop_bars": time_stop,
                            "signal_bar_ts": ts, "since_ts": ts,
                            "action_by_ts": sorted_timestamps[min(bar_idx + time_stop, len(sorted_timestamps) - 1)] if bar_idx + time_stop < len(sorted_timestamps) else None,
                            "indicator_values": {
                                "ema20_slope_atr": snapshot.ema20_slope_atr,
                                "ema50_slope_atr": snapshot.ema50_slope_atr,
                                "clv": snapshot.clv, "volume_z": snapshot.volume_z,
                            },
                            "score_components": {
                                "trend_strength": s6_signal.trend_strength,
                                "pullback_quality": s6_signal.pullback_quality,
                                "retrace_depth": s6_signal.retrace_depth,
                                "participation": s6_signal.participation,
                                "clean_close": s6_signal.clean_close,
                            },
                            "why_items": s6_signal.explain.why_items if s6_signal.explain else [],
                        })

                # ------ S7: Momentum Breakout (Donchian) ------
                if "S7" in active_setup_ids:
                    s7_state = s7_evaluator.compute_state(snapshot, s7_prev_snapshot.get(key))
                    s7_prev_snapshot[key] = snapshot
                    s7_signal = s7_evaluator.evaluate_trigger(snapshot, s7_state, symbol, timeframe)
                    if s7_signal:
                        signals_generated += 1
                        signals_per_setup["S7"] += 1
                        time_stop = s7_signal.time_stop_bars
                        pending_entries.append({
                            "symbol": symbol, "timeframe": timeframe,
                            "direction": s7_signal.direction.value,
                            "setup_id": "S7",
                            "stop_price": s7_signal.stop_price,
                            "tp1_price": s7_signal.tp1_price,
                            "tp2_price": s7_signal.tp2_price,
                            "r_value": s7_signal.r_value,
                            "setup_score": s7_signal.setup_score,
                            "time_stop_bars": time_stop,
                            "signal_bar_ts": ts, "since_ts": ts,
                            "action_by_ts": sorted_timestamps[min(bar_idx + time_stop, len(sorted_timestamps) - 1)] if bar_idx + time_stop < len(sorted_timestamps) else None,
                            "indicator_values": {
                                "donchian_high_20": snapshot.donchian_high_20,
                                "donchian_low_20": snapshot.donchian_low_20,
                                "clv": snapshot.clv, "volume_z": snapshot.volume_z,
                            },
                            "score_components": {
                                "break_strength": s7_signal.break_strength,
                                "volume_quality": s7_signal.volume_quality,
                                "bias_strength": s7_signal.bias_strength,
                                "clean_close": s7_signal.clean_close,
                                "htf_confluence": s7_signal.htf_confluence,
                            },
                            "why_items": s7_signal.explain.why_items if s7_signal.explain else [],
                        })

            # ---------------------------------------------------------
            # P0-4: Track equity (capital + unrealized PnL)
            # ---------------------------------------------------------
            unrealized = 0.0
            for pos in open_positions.values():
                sym_key = f"{pos['symbol']}_{pos['timeframe']}"
                if sym_key in current_bars:
                    bar = current_bars[sym_key]
                    if pos["side"] == "long":
                        unrealized += (bar["close"] - pos["entry_price"]) * pos["position_size"]
                    else:
                        unrealized += (pos["entry_price"] - bar["close"]) * pos["position_size"]

            equity = capital + unrealized
            if equity > peak_equity:
                peak_equity = equity
            dd_pct = ((peak_equity - equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct

            equity_curve.append({
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "equity": round(equity, 2),
                "drawdown_pct": round(dd_pct, 2),
            })

        # =================================================================
        # Force-close remaining positions at last bar's close (fixes P1-11)
        # =================================================================
        for pos_key, pos in open_positions.items():
            sym_key = f"{pos['symbol']}_{pos['timeframe']}"
            last_bar = bars_by_symbol[sym_key][-1] if sym_key in bars_by_symbol else None
            pos["exit_ts"] = sorted_timestamps[-1] if sorted_timestamps else datetime.now(timezone.utc)
            pos["exit_price"] = last_bar["close"] if last_bar else pos["entry_price"]
            pos["exit_reason"] = "END_OF_BACKTEST"
            self._calculate_trade_pnl(pos, exec_config)
            capital += pos["pnl_net"]
            closed_trades.append(pos)

        # =================================================================
        # Store trades and compute summary
        # =================================================================
        trades = [self._position_to_trade(run.id, pos) for pos in closed_trades]
        self.repo.add_trades_batch(trades)

        run.summary = self._calculate_backtest_summary(closed_trades, total_bars, len(bars_by_symbol))
        run.summary.starting_capital = starting_capital
        run.summary.final_equity = round(capital, 2)
        run.summary.max_drawdown_pct = round(max_drawdown_pct, 2)
        run.summary.signals_generated = signals_generated
        run.summary.equity_curve = equity_curve
        # P2-13: Return % and Calmar ratio (need equity + drawdown)
        run.summary.return_pct = round(((capital - starting_capital) / starting_capital) * 100, 2) if starting_capital > 0 else 0.0
        if max_drawdown_pct > 0:
            annualized_return = run.summary.return_pct  # simple approximation
            run.summary.calmar_ratio = round(annualized_return / max_drawdown_pct, 3)

        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=(
                f"Backtest complete: {len(closed_trades)} trades, "
                f"{run.summary.win_rate:.1%} win rate, "
                f"capital {starting_capital:.0f} -> {capital:.2f}, "
                f"max DD {max_drawdown_pct:.1f}%, "
                f"{signals_generated} signals generated "
                f"(per-setup: {signals_per_setup})"
            ),
        ))

        # Enhancement (c): Log skip summary for debugging fill-loop filtering
        total_skips = sum(skip_counters.values())
        if total_skips > 0:
            self.repo.add_event(RunEvent(
                run_id=run.id,
                event_type="INFO",
                message=f"Backtest fill skips: {total_skips} total — {skip_counters}",
            ))

    def _load_backtest_data(self, run: Run) -> dict[str, list[dict]]:
        """
        Load OHLCV data for backtest from database.

        Canon 8 (anti-lookahead): filters to is_final = TRUE only so
        forming candles are never included in historical simulation.

        P3-16: Detects gaps and applies gaps_policy from DataSpec.
        P3-18: Skips symbols with fewer than min_bars_required bars.
        """
        bars_by_symbol = {}
        assets_skipped = 0

        start_ts = run.universe.start_ts
        end_ts = run.universe.end_ts
        min_bars = run.data.min_bars_required
        gaps_policy = run.data.gaps_policy
        max_gap_bars = run.data.max_gap_bars

        # Timeframe to expected interval in seconds for gap detection
        tf_seconds = {
            "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14400, "1d": 86400,
        }

        with self._conn() as conn:
            with conn.cursor() as cur:
                for symbol in run.universe.assets:
                    for tf in run.universe.timeframes:
                        cur.execute("""
                            SELECT DISTINCT ON (bar_start_ts)
                                   bar_start_ts, open, high, low, close, volume
                            FROM ohlcv
                            WHERE symbol = %s AND timeframe = %s
                              AND bar_start_ts >= %s AND bar_start_ts <= %s
                              AND is_final = TRUE
                            ORDER BY bar_start_ts,
                                     CASE source
                                         WHEN 'ws' THEN 0
                                         WHEN 'binance' THEN 1
                                         ELSE 2
                                     END;
                        """, (symbol, tf, start_ts, end_ts))

                        rows = cur.fetchall()
                        if not rows:
                            continue

                        key = f"{symbol}_{tf}"

                        # P3-18: Skip symbols with insufficient bars
                        if len(rows) < min_bars:
                            assets_skipped += 1
                            logger.info(
                                f"Skipping {key}: only {len(rows)} bars (need {min_bars})",
                                extra={"event": "asset_skipped_min_bars", "symbol": symbol, "tf": tf, "bars": len(rows)},
                            )
                            continue

                        bars = [
                            {
                                "bar_start_ts": row[0],
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

                        # P3-16: Gap detection
                        expected_interval = tf_seconds.get(tf, 3600)
                        max_gap_found = 0
                        gap_count = 0
                        for i in range(1, len(bars)):
                            t0 = bars[i - 1]["bar_start_ts"]
                            t1 = bars[i]["bar_start_ts"]
                            if hasattr(t0, "timestamp"):
                                delta = t1.timestamp() - t0.timestamp()
                            else:
                                delta = float(t1 - t0)
                            gap_bars = int(delta / expected_interval) - 1
                            if gap_bars > 0:
                                gap_count += 1
                                max_gap_found = max(max_gap_found, gap_bars)

                        if max_gap_found > max_gap_bars:
                            if gaps_policy == "SKIP_ASSET":
                                assets_skipped += 1
                                logger.info(
                                    f"Skipping {key}: gap of {max_gap_found} bars exceeds max {max_gap_bars}",
                                    extra={"event": "asset_skipped_gap", "symbol": symbol, "tf": tf, "max_gap": max_gap_found},
                                )
                                continue
                            # FORWARD_FILL / DROP_BARS: log warning but include
                            logger.warning(
                                f"{key}: {gap_count} gaps detected, max={max_gap_found} bars (policy={gaps_policy})",
                                extra={"event": "data_gap_detected", "symbol": symbol, "tf": tf,
                                       "gap_count": gap_count, "max_gap": max_gap_found},
                            )

                        bars_by_symbol[key] = bars

        run.summary.assets_skipped = assets_skipped
        return bars_by_symbol
    
    def _check_position_exit(
        self,
        pos: dict,
        bar: dict,
        exec_config: ExecutionConfig,
    ) -> Optional[tuple[str, float]]:
        """
        Check if position should exit. Returns (reason, price) or None.

        Delegates to shared exit_checker module for consistent exit logic
        across BACKTEST, PAPER, and SIMULATION runs.

        Priority order (Canon): STOP_LOSS -> FAIL_FAST -> TIME_STOP -> TRAIL_STOP -> TP1 -> TP2.
        """
        # Build ExitConfig from exec_config
        stop_fill = getattr(exec_config.fills, "stop_fill", None) if hasattr(exec_config, 'fills') else None
        target_fill = getattr(exec_config.fills, "target_fill", None) if hasattr(exec_config, 'fills') else None
        use_through_stop = stop_fill and stop_fill.value == "THROUGH"
        use_through_target = target_fill and target_fill.value == "THROUGH"

        exit_config = ExitConfig(
            stop_fill_through=use_through_stop,
            target_fill_through=use_through_target,
        )

        # Build PositionState from pos dict
        state = PositionState(
            bars_held=pos.get("bars_held", 0),
            high_water_mark=pos.get("high_water_mark"),
            half_r_touched=pos.get("_half_r_touched", False),
        )

        # Call shared exit checker
        result = check_exit(
            side=pos["side"],
            bar=bar,
            entry_price=pos["entry_price"],
            stop_price=pos["stop_price"],
            tp1_price=pos.get("tp1_price"),
            tp2_price=pos.get("tp2_price"),
            r_value=pos.get("r_value", 0),
            max_bars=pos.get("max_bars", 50),
            state=state,
            config=exit_config,
        )

        # Update pos dict with state changes
        pos["high_water_mark"] = state.high_water_mark
        pos["_half_r_touched"] = state.half_r_touched

        return result
    
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
        
        # R-multiple (net of fees — reflects actual returns)
        r_value = pos.get("r_value", abs(entry - pos["stop_price"]))
        if r_value > 0 and size > 0:
            pos["r_multiple"] = pos["pnl_net"] / (r_value * size)
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
        total_slippage = sum(
            (abs(t.get("entry_with_slippage", t.get("entry_price", 0)) - t.get("entry_price", 0)) +
             abs(t.get("exit_with_slippage", t.get("exit_price", 0)) - t.get("exit_price", 0))) *
            t.get("position_size", 1.0)
            for t in trades
        )

        r_multiples = [t.get("r_multiple", 0) for t in trades]

        win_rate = len(winners) / len(trades) if trades else 0
        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0

        # Profit factor
        gross_profit = sum(t.get("pnl_net", 0) for t in winners)
        gross_loss = abs(sum(t.get("pnl_net", 0) for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # P2-13: Extended risk metrics
        # Average holding period
        bars_held_list = [t.get("bars_held", 0) for t in trades]
        avg_holding = sum(bars_held_list) / len(bars_held_list) if bars_held_list else 0

        # Average win/loss R
        win_rs = [t.get("r_multiple", 0) for t in winners]
        loss_rs = [t.get("r_multiple", 0) for t in losers]
        avg_win_r = sum(win_rs) / len(win_rs) if win_rs else 0
        avg_loss_r = sum(loss_rs) / len(loss_rs) if loss_rs else 0
        payoff_ratio = abs(avg_win_r / avg_loss_r) if avg_loss_r != 0 else 0

        # Max consecutive wins/losses
        max_con_wins = 0
        max_con_losses = 0
        cur_wins = 0
        cur_losses = 0
        for t in trades:
            if t.get("pnl_net", 0) > 0:
                cur_wins += 1
                cur_losses = 0
                max_con_wins = max(max_con_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_con_losses = max(max_con_losses, cur_losses)

        # Sharpe, Sortino, Calmar (using R-multiples as return series)
        import math
        sharpe = 0.0
        sortino = 0.0
        calmar = 0.0
        if len(r_multiples) > 1:
            mean_r = avg_r
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in r_multiples) / (len(r_multiples) - 1))
            sharpe = (mean_r / std_r) if std_r > 0 else 0

            # Sortino: downside deviation only
            downside = [min(0, r - 0) for r in r_multiples]  # target = 0
            downside_var = sum(d ** 2 for d in downside) / (len(r_multiples) - 1)
            downside_std = math.sqrt(downside_var)
            sortino = (mean_r / downside_std) if downside_std > 0 else 0

        # P2-14: Per-setup breakdown
        per_setup: dict[str, dict] = {}
        for t in trades:
            sid = t.get("setup_id", "unknown")
            if sid not in per_setup:
                per_setup[sid] = {"trades": 0, "wins": 0, "pnl_net": 0.0, "total_r": 0.0}
            per_setup[sid]["trades"] += 1
            if t.get("pnl_net", 0) > 0:
                per_setup[sid]["wins"] += 1
            per_setup[sid]["pnl_net"] += t.get("pnl_net", 0)
            per_setup[sid]["total_r"] += t.get("r_multiple", 0)
        for sid in per_setup:
            s = per_setup[sid]
            s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0
            s["avg_r"] = s["total_r"] / s["trades"] if s["trades"] > 0 else 0
            s["pnl_net"] = round(s["pnl_net"], 2)
            s["total_r"] = round(s["total_r"], 2)

        # P2-14: Per-symbol breakdown (top 20 by abs PnL)
        per_symbol: dict[str, dict] = {}
        for t in trades:
            sym = t.get("symbol", "unknown")
            if sym not in per_symbol:
                per_symbol[sym] = {"trades": 0, "wins": 0, "pnl_net": 0.0, "total_r": 0.0}
            per_symbol[sym]["trades"] += 1
            if t.get("pnl_net", 0) > 0:
                per_symbol[sym]["wins"] += 1
            per_symbol[sym]["pnl_net"] += t.get("pnl_net", 0)
            per_symbol[sym]["total_r"] += t.get("r_multiple", 0)
        for sym in per_symbol:
            s = per_symbol[sym]
            s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0
            s["avg_r"] = s["total_r"] / s["trades"] if s["trades"] > 0 else 0
            s["pnl_net"] = round(s["pnl_net"], 2)
            s["total_r"] = round(s["total_r"], 2)
        # Trim to top 20 by absolute PnL
        if len(per_symbol) > 20:
            top_syms = sorted(per_symbol, key=lambda s: abs(per_symbol[s]["pnl_net"]), reverse=True)[:20]
            per_symbol = {s: per_symbol[s] for s in top_syms}

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
            total_slippage=total_slippage,
            net_pnl=total_pnl,
            profit_factor=profit_factor,
            expectancy=avg_r,
            bars_processed=total_bars,
            assets_processed=total_assets,
            # P2-13
            sharpe_ratio=round(sharpe, 3),
            sortino_ratio=round(sortino, 3),
            calmar_ratio=round(calmar, 3),
            max_consecutive_wins=max_con_wins,
            max_consecutive_losses=max_con_losses,
            avg_holding_bars=round(avg_holding, 1),
            avg_win_r=round(avg_win_r, 3),
            avg_loss_r=round(avg_loss_r, 3),
            payoff_ratio=round(payoff_ratio, 3),
            # P2-14
            per_setup=per_setup,
            per_symbol=per_symbol,
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
            run_id=run.id,
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
    # SIMULATION Execution
    # =========================================================================

    def _execute_simulation(self, run: Run):
        """
        Execute a SIMULATION run - simulate expired opportunities.

        For each expired opportunity:
        1. Assume a position opened at signal generation time
        2. Walk forward through OHLCV data
        3. Close when the system's exit logic would have triggered

        Results are stored in simulation_results table for analysis.
        """
        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message="Starting simulation of expired opportunities",
        ))

        # Get simulation config from strategy params
        setup_params = run.strategy.setup_params or {}
        position_size_usd = setup_params.get("position_size_usd", 1000.0)
        slippage_bps = setup_params.get("slippage_bps", 5.0)
        setup_ids = setup_params.get("setup_ids")  # Optional filter
        min_score = setup_params.get("min_score", 0.0)
        max_age_hours = setup_params.get("max_age_hours")
        limit = setup_params.get("limit", 5000)
        setup_version = setup_params.get("setup_version")  # Backfill filter
        batch_id = setup_params.get("batch_id")  # Backfill batch filter
        timeframes = setup_params.get("timeframes")  # Timeframe filter
        directions = setup_params.get("directions")  # Direction filter (long/short)
        from_date = setup_params.get("from_date")  # Date range lower bound
        to_date = setup_params.get("to_date")  # Date range upper bound
        include_dates = setup_params.get("include_dates")  # Specific dates only
        exclude_dates = setup_params.get("exclude_dates")  # Skip these dates

        # Build simulator config
        sim_config = SimulationConfig(
            position_size_usd=position_size_usd,
            slippage_entry_bps=slippage_bps,
            slippage_exit_bps=slippage_bps,
            fee_taker_bps=run.execution.fee_taker_bps,
            max_bars_forward=100,
        )

        # Load expired opportunities from database
        opportunities = self._load_expired_opportunities(
            setup_ids=setup_ids,
            min_score=min_score,
            max_age_hours=max_age_hours,
            limit=limit,
            setup_version=setup_version,
            batch_id=batch_id,
            timeframes=timeframes,
            directions=directions,
            from_date=from_date,
            to_date=to_date,
            include_dates=include_dates,
            exclude_dates=exclude_dates,
        )

        if not opportunities:
            self.repo.add_event(RunEvent(
                run_id=run.id,
                event_type="WARNING",
                message="No expired opportunities found matching criteria",
            ))
            run.summary.total_trades = 0
            run.summary.signals_generated = 0
            return

        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=f"Found {len(opportunities)} expired opportunities to simulate",
        ))

        # Create simulator and run
        simulator = OpportunitySimulator(
            database_url=self.database_url,
            config=sim_config,
        )

        results = simulator.simulate_batch(opportunities, run.id)

        # Log degraded run warning if pre-sim backfill failed
        if getattr(simulator, '_backfill_degraded', False):
            self.repo.add_event(RunEvent(
                run_id=run.id,
                event_type="WARNING",
                message="Pre-sim OHLCV backfill failed — results may have gaps",
            ))

        # Compute summary statistics
        summary_stats = simulator.compute_summary(results, position_size_usd)

        # Update run summary
        run.summary.total_trades = summary_stats["simulated_count"]
        run.summary.signals_generated = summary_stats["total_opportunities"]
        run.summary.winning_trades = len([r for r in results if r.data_available and r.pnl_net and r.pnl_net > 0])
        run.summary.losing_trades = len([r for r in results if r.data_available and r.pnl_net and r.pnl_net <= 0])
        run.summary.win_rate = summary_stats["win_rate"]
        run.summary.total_r = summary_stats["total_r"]
        run.summary.avg_r = summary_stats["avg_r"]
        run.summary.profit_factor = summary_stats["profit_factor"] or 0
        run.summary.net_pnl = summary_stats["total_pnl_net"]
        run.summary.expectancy = summary_stats["expectancy_r"]
        run.summary.per_setup = summary_stats["per_setup"]

        # Store summary stats as run event for API retrieval
        self.repo.add_event(RunEvent(
            run_id=run.id,
            event_type="INFO",
            message=(
                f"Simulation complete: {summary_stats['simulated_count']} opportunities, "
                f"{summary_stats['win_rate']:.1%} win rate, "
                f"{summary_stats['total_r']:.1f}R total, "
                f"${summary_stats['total_pnl_net']:.2f} PnL"
            ),
            payload=summary_stats,
        ))

    def _load_expired_opportunities(
        self,
        setup_ids: list[str] = None,
        min_score: float = 0.0,
        max_age_hours: int = None,
        limit: int = 500,
        setup_version: str = None,
        batch_id: str = None,
        timeframes: list[str] = None,
        directions: list[str] = None,
        from_date: str = None,
        to_date: str = None,
        include_dates: list[str] = None,
        exclude_dates: list[str] = None,
    ) -> list[dict]:
        """
        Load expired opportunities from the database.

        Args:
            setup_ids: Optional filter by setup ID (S1, S2, U1)
            min_score: Minimum score filter
            max_age_hours: How far back to look
            limit: Maximum number of opportunities
            setup_version: Optional filter by setup version (e.g. 'S1_v2.2')
            batch_id: Optional filter by batch ID (e.g. 'backfill_20260208_abc123')
            from_date: Inclusive lower bound (YYYY-MM-DD)
            to_date: Inclusive upper bound (YYYY-MM-DD)
            include_dates: Only these specific dates (overrides from/to)
            exclude_dates: Skip these dates

        Returns:
            List of opportunity dicts
        """
        opportunities = []

        with self._conn() as conn:
            with conn.cursor() as cur:
                # Build query with filters
                conditions = ["status = 'expired'"]
                params = []

                if setup_ids:
                    conditions.append("setup_id = ANY(%s)")
                    params.append(setup_ids)

                if min_score > 0:
                    conditions.append("score >= %s")
                    params.append(min_score)

                if max_age_hours:
                    conditions.append("created_ts >= NOW() - INTERVAL '%s hours'")
                    params.append(max_age_hours)

                if setup_version:
                    conditions.append("setup_version = %s")
                    params.append(setup_version)

                if batch_id:
                    conditions.append("batch_id = %s")
                    params.append(batch_id)

                if timeframes:
                    conditions.append("timeframe = ANY(%s)")
                    params.append(timeframes)

                if directions:
                    conditions.append("payload_json::jsonb ->> 'direction' = ANY(%s)")
                    params.append(directions)

                # Date filters — include_dates overrides from/to
                if include_dates:
                    conditions.append("created_ts::date = ANY(%s::date[])")
                    params.append(include_dates)
                else:
                    if from_date:
                        conditions.append("created_ts >= %s::date")
                        params.append(from_date)
                    if to_date:
                        conditions.append("created_ts < (%s::date + INTERVAL '1 day')")
                        params.append(to_date)

                if exclude_dates:
                    conditions.append("NOT (created_ts::date = ANY(%s::date[]))")
                    params.append(exclude_dates)

                where_clause = " AND ".join(conditions)
                params.append(limit)

                # Every expired opportunity is a distinct historical event —
                # simulation needs all of them for real expectancy stats.
                cur.execute(f"""
                    SELECT id, created_ts, symbol, timeframe, setup_id,
                           decision, score, reason, payload_json
                    FROM opportunities
                    WHERE {where_clause}
                    ORDER BY created_ts ASC
                    LIMIT %s;
                """, params)

                rows = cur.fetchall()
                for row in rows:
                    opportunities.append({
                        "id": row[0],
                        "created_ts": row[1],
                        "symbol": row[2],
                        "timeframe": row[3],
                        "setup_id": row[4],
                        "decision": row[5],
                        "score": row[6],
                        "reason": row[7],
                        "payload_json": row[8],
                    })

        return opportunities

    # =========================================================================
    # Helpers
    # =========================================================================
    
    def _compute_ohlcv_checksum(self, bars_by_symbol: dict[str, list[dict]]) -> str:
        """
        Canon 2C: compute SHA256 checksum of all OHLCV data used in backtest.

        On replay, recompute and compare to verify data hasn't changed.
        """
        h = hashlib.sha256()
        for key in sorted(bars_by_symbol.keys()):
            for bar in bars_by_symbol[key]:
                h.update(f"{bar['bar_start_ts']}:{bar['close']}".encode())
        return h.hexdigest()

    def _update_run_data_json(self, run: Run):
        """Persist updated data_json (with checksum) back to the run record."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE runs SET data_json = %s WHERE id = %s;
                """, (json.dumps(run.data.to_dict()), run.id))
            conn.commit()

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
            max_exposure_pct=spec.max_exposure_pct,
            max_risk_per_trade_pct=spec.max_risk_per_trade_pct,
            max_daily_loss_pct=spec.max_daily_loss_pct,
        )
    
    def _signal_to_trade(self, run_id: str, signal: RankedSignal) -> RunTrade:
        """Convert a RankedSignal to a RunTrade."""
        # Canon 7A: validate explainability payload
        why_items = signal.payload.get("why_items", [])
        if len(why_items) < 3:
            why_items = _build_why_items(signal)

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
        # Attach validated why_items to trace
        trace.state_snapshot["why_items"] = why_items

        signal_ts = datetime.fromtimestamp(signal.timestamp, tz=timezone.utc)

        return RunTrade(
            run_id=run_id,
            asset=signal.symbol,
            timeframe=signal.timeframe,
            side=signal.direction,
            setup_id=signal.setup_id,
            signal_bar_ts=signal_ts,                     # Canon 5A
            since_ts=signal.payload.get("since_ts") or signal_ts,  # Canon 5E
            action_by_ts=signal.payload.get("action_by_ts"),       # Canon 5E
            entry_ts=signal_ts,
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
        # Defensive copy: avoid mutating shared dict (TP1 partial-close creates shallow copies)
        state_snap = dict(pos.get("state_snapshot") or {})
        state_snap["position_size"] = pos.get("position_size", 1.0)
        why_items = pos.get("why_items", [])
        if why_items:
            state_snap["why_items"] = list(why_items)  # copy to avoid shared ref

        trace = TradeTrace(
            conditions_met=pos.get("conditions_met", {}),
            entry_reason=pos.get("entry_reason", ""),
            indicator_values=pos.get("indicator_values", {}),
            exit_reason=pos.get("exit_reason"),
            exit_explanation=f"Exit at {pos.get('r_multiple', 0):.2f}R after {pos.get('bars_held', 0)} bars",
            state_snapshot=state_snap,
            score_components=pos.get("score_components", {}),
        )

        # Compute total slippage cost for audit (P1-8)
        entry_slip = abs(pos.get("entry_with_slippage", pos.get("entry_price", 0)) - pos.get("entry_price", 0))
        exit_slip = abs(pos.get("exit_with_slippage", pos.get("exit_price", 0)) - pos.get("exit_price", 0))
        total_slippage = (entry_slip + exit_slip) * pos.get("position_size", 1.0)

        return RunTrade(
            run_id=run_id,
            asset=pos.get("symbol", ""),
            timeframe=pos.get("timeframe", "1h"),
            side=pos.get("side", "long"),
            setup_id=pos.get("setup_id", ""),
            signal_bar_ts=pos.get("signal_bar_ts"),    # Canon 5A
            since_ts=pos.get("since_ts"),              # Canon 5E
            action_by_ts=pos.get("action_by_ts"),      # Canon 5E
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
            slippage=total_slippage,
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
