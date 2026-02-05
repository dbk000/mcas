"""
Run Repository
==============

Database operations for Runs and related entities.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from contextlib import contextmanager

import psycopg

from .models import (
    Run, RunType, RunStatus,
    RunTrade, RunEvent, RunSummary,
    UniverseSpec, StrategySpec, ExecutionSpec, DataSpec,
    TradeTrace,
    RUNS_SCHEMA,
)

logger = logging.getLogger(__name__)


class RunRepository:
    """
    Handles all database operations for Runs.
    
    Usage:
        repo = RunRepository(database_url)
        repo.ensure_schema()
        
        run = Run(...)
        repo.create_run(run)
        repo.start_run(run.id)
        repo.add_trade(trade)
        repo.complete_run(run.id, summary)
    """
    
    def __init__(self, database_url: str):
        self.database_url = database_url
    
    @contextmanager
    def _conn(self):
        """Context manager for database connections."""
        conn = psycopg.connect(self.database_url)
        try:
            yield conn
        finally:
            conn.close()
    
    def ensure_schema(self):
        """Create tables if they don't exist."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Split schema into individual statements
                statements = [s.strip() for s in RUNS_SCHEMA.split(';') if s.strip()]
                for stmt in statements:
                    try:
                        cur.execute(stmt + ';')
                    except Exception as e:
                        # Log but continue - some statements might fail if already exists
                        logger.debug(f"Schema statement skipped: {e}")
            conn.commit()
        logger.info("Run schema ensured")
    
    # -------------------------------------------------------------------------
    # Run CRUD
    # -------------------------------------------------------------------------
    
    def create_run(self, run: Run) -> Run:
        """Create a new run in PENDING status."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO runs (
                        id, run_type, status, name, notes, fingerprint,
                        engine_version, engine_commit,
                        universe_json, strategy_json, execution_json, data_json,
                        created_at, parent_run_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (fingerprint, run_type) DO UPDATE
                    SET name = EXCLUDED.name  -- Just return existing
                    RETURNING id;
                """, (
                    run.id,
                    run.run_type.value,
                    run.status.value,
                    run.name,
                    run.notes,
                    run.fingerprint,
                    run.engine_version,
                    run.engine_commit,
                    json.dumps(run.universe.to_dict()),
                    json.dumps(run.strategy.to_dict()),
                    json.dumps(run.execution.to_dict()),
                    json.dumps(run.data.to_dict()),
                    run.created_at,
                    run.parent_run_id,
                ))
                result = cur.fetchone()
            conn.commit()
        
        logger.info(f"Created run {run.id} fingerprint={run.fingerprint}")
        return run
    
    def get_run(self, run_id: str) -> Optional[Run]:
        """Get a run by ID."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, run_type, status, name, notes, fingerprint,
                           engine_version, engine_commit,
                           universe_json, strategy_json, execution_json, data_json,
                           created_at, started_at, finished_at,
                           summary_json, error_text, parent_run_id
                    FROM runs WHERE id = %s;
                """, (run_id,))
                row = cur.fetchone()
        
        if not row:
            return None
        
        return self._row_to_run(row)
    
    def get_run_by_fingerprint(self, fingerprint: str, run_type: RunType) -> Optional[Run]:
        """Get a run by fingerprint and type."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, run_type, status, name, notes, fingerprint,
                           engine_version, engine_commit,
                           universe_json, strategy_json, execution_json, data_json,
                           created_at, started_at, finished_at,
                           summary_json, error_text, parent_run_id
                    FROM runs WHERE fingerprint = %s AND run_type = %s;
                """, (fingerprint, run_type.value))
                row = cur.fetchone()
        
        if not row:
            return None
        
        return self._row_to_run(row)
    
    def list_runs(
        self,
        run_type: Optional[RunType] = None,
        status: Optional[RunStatus] = None,
        limit: int = 50,
    ) -> list[Run]:
        """List runs with optional filters."""
        conditions = []
        params = []
        
        if run_type:
            conditions.append("run_type = %s")
            params.append(run_type.value)
        if status:
            conditions.append("status = %s")
            params.append(status.value)
        
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, run_type, status, name, notes, fingerprint,
                           engine_version, engine_commit,
                           universe_json, strategy_json, execution_json, data_json,
                           created_at, started_at, finished_at,
                           summary_json, error_text, parent_run_id
                    FROM runs {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s;
                """, params + [limit])
                rows = cur.fetchall()
        
        return [self._row_to_run(row) for row in rows]
    
    def _row_to_run(self, row: tuple) -> Run:
        """Convert a database row to a Run object."""
        (
            id_, run_type, status, name, notes, fingerprint,
            engine_version, engine_commit,
            universe_json, strategy_json, execution_json, data_json,
            created_at, started_at, finished_at,
            summary_json, error_text, parent_run_id
        ) = row
        
        universe_dict = json.loads(universe_json) if isinstance(universe_json, str) else universe_json
        strategy_dict = json.loads(strategy_json) if isinstance(strategy_json, str) else strategy_json
        execution_dict = json.loads(execution_json) if isinstance(execution_json, str) else execution_json
        data_dict = json.loads(data_json) if isinstance(data_json, str) else data_json
        
        run = Run(
            id=str(id_),
            run_type=RunType(run_type),
            status=RunStatus(status),
            name=name,
            notes=notes,
            fingerprint=fingerprint,
            engine_version=engine_version,
            engine_commit=engine_commit,
            universe=UniverseSpec(
                assets=universe_dict.get("assets", []),
                timeframes=universe_dict.get("timeframes", []),
                venue=universe_dict.get("venue", "binance"),
            ),
            strategy=StrategySpec(
                setup_id=strategy_dict.get("setup_id", ""),
                setup_version=strategy_dict.get("setup_version", ""),
                setup_params=strategy_dict.get("setup_params", {}),
                bias_mode=strategy_dict.get("bias_mode", "HTF_TREND_PROXY"),
            ),
            execution=ExecutionSpec(**{k: v for k, v in execution_dict.items() if k in ExecutionSpec.__dataclass_fields__}),
            data=DataSpec(**{k: v for k, v in data_dict.items() if k in DataSpec.__dataclass_fields__}),
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            error_text=error_text,
            parent_run_id=str(parent_run_id) if parent_run_id else None,
        )
        
        if summary_json:
            summary_dict = json.loads(summary_json) if isinstance(summary_json, str) else summary_json
            run.summary = RunSummary(**{k: v for k, v in summary_dict.items() if k in RunSummary.__dataclass_fields__})
        
        return run
    
    # -------------------------------------------------------------------------
    # Run lifecycle
    # -------------------------------------------------------------------------
    
    def start_run(self, run_id: str) -> bool:
        """Mark run as RUNNING."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE runs 
                    SET status = %s, started_at = %s
                    WHERE id = %s AND status = %s;
                """, (RunStatus.RUNNING.value, datetime.now(timezone.utc), run_id, RunStatus.PENDING.value))
                updated = cur.rowcount > 0
            conn.commit()
        
        if updated:
            logger.info(f"Started run {run_id}")
        return updated
    
    def complete_run(self, run_id: str, summary: RunSummary) -> bool:
        """Mark run as SUCCESS with summary."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE runs 
                    SET status = %s, finished_at = %s, summary_json = %s
                    WHERE id = %s AND status = %s;
                """, (
                    RunStatus.SUCCESS.value,
                    datetime.now(timezone.utc),
                    json.dumps(summary.to_dict()),
                    run_id,
                    RunStatus.RUNNING.value,
                ))
                updated = cur.rowcount > 0
            conn.commit()
        
        if updated:
            logger.info(f"Completed run {run_id}")
        return updated
    
    def fail_run(self, run_id: str, error: str) -> bool:
        """Mark run as FAILED."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE runs 
                    SET status = %s, finished_at = %s, error_text = %s
                    WHERE id = %s;
                """, (RunStatus.FAILED.value, datetime.now(timezone.utc), error, run_id))
                updated = cur.rowcount > 0
            conn.commit()
        
        if updated:
            logger.error(f"Failed run {run_id}: {error}")
        return updated
    
    # -------------------------------------------------------------------------
    # Trades
    # -------------------------------------------------------------------------
    
    def add_trade(self, trade: RunTrade):
        """Add a trade to a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO run_trades (
                        id, run_id, asset, timeframe, side, setup_id,
                        signal_bar_ts, since_ts, action_by_ts,
                        entry_ts, entry_price, entry_bar_index,
                        stop_price, tp1_price, tp2_price, r_value,
                        exit_ts, exit_price, exit_reason,
                        r_multiple, pnl_gross, fees, slippage, pnl_net,
                        setup_score, trace_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s
                    );
                """, (
                    trade.id, trade.run_id, trade.asset, trade.timeframe, trade.side, trade.setup_id,
                    trade.signal_bar_ts, trade.since_ts, trade.action_by_ts,
                    trade.entry_ts, trade.entry_price, trade.entry_bar_index,
                    trade.stop_price, trade.tp1_price, trade.tp2_price, trade.r_value,
                    trade.exit_ts, trade.exit_price, trade.exit_reason,
                    trade.r_multiple, trade.pnl_gross, trade.fees, trade.slippage, trade.pnl_net,
                    trade.setup_score, json.dumps(trade.trace.to_dict()),
                ))
            conn.commit()
    
    def add_trades_batch(self, trades: list[RunTrade]):
        """Add multiple trades efficiently."""
        if not trades:
            return

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO run_trades (
                        id, run_id, asset, timeframe, side, setup_id,
                        signal_bar_ts, since_ts, action_by_ts,
                        entry_ts, entry_price, entry_bar_index,
                        stop_price, tp1_price, tp2_price, r_value,
                        exit_ts, exit_price, exit_reason,
                        r_multiple, pnl_gross, fees, slippage, pnl_net,
                        setup_score, trace_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s
                    );
                """, [
                    (
                        t.id, t.run_id, t.asset, t.timeframe, t.side, t.setup_id,
                        t.signal_bar_ts, t.since_ts, t.action_by_ts,
                        t.entry_ts, t.entry_price, t.entry_bar_index,
                        t.stop_price, t.tp1_price, t.tp2_price, t.r_value,
                        t.exit_ts, t.exit_price, t.exit_reason,
                        t.r_multiple, t.pnl_gross, t.fees, t.slippage, t.pnl_net,
                        t.setup_score, json.dumps(t.trace.to_dict()),
                    )
                    for t in trades
                ])
            conn.commit()

        logger.debug(f"Added {len(trades)} trades")
    
    def get_trades(self, run_id: str) -> list[RunTrade]:
        """Get all trades for a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, run_id, asset, timeframe, side, setup_id,
                           signal_bar_ts, since_ts, action_by_ts,
                           entry_ts, entry_price, entry_bar_index,
                           stop_price, tp1_price, tp2_price, r_value,
                           exit_ts, exit_price, exit_reason,
                           r_multiple, pnl_gross, fees, slippage, pnl_net,
                           setup_score, trace_json
                    FROM run_trades
                    WHERE run_id = %s
                    ORDER BY entry_ts;
                """, (run_id,))
                rows = cur.fetchall()

        return [self._row_to_trade(row) for row in rows]

    def _row_to_trade(self, row: tuple) -> RunTrade:
        """Convert a database row to a RunTrade object."""
        trace_json = row[25]
        trace_dict = json.loads(trace_json) if isinstance(trace_json, str) else (trace_json or {})

        return RunTrade(
            id=str(row[0]),
            run_id=str(row[1]),
            asset=row[2],
            timeframe=row[3],
            side=row[4],
            setup_id=row[5],
            signal_bar_ts=row[6],
            since_ts=row[7],
            action_by_ts=row[8],
            entry_ts=row[9],
            entry_price=row[10],
            entry_bar_index=row[11],
            stop_price=row[12],
            tp1_price=row[13],
            tp2_price=row[14],
            r_value=row[15],
            exit_ts=row[16],
            exit_price=row[17],
            exit_reason=row[18],
            r_multiple=row[19],
            pnl_gross=row[20],
            fees=row[21],
            slippage=row[22],
            pnl_net=row[23],
            setup_score=row[24],
            trace=TradeTrace(
                conditions_met=trace_dict.get("conditions_met", {}),
                indicator_values=trace_dict.get("indicator_values", {}),
                state_snapshot=trace_dict.get("state_snapshot", {}),
                score_components=trace_dict.get("score_components", {}),
            ),
        )
    
    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------
    
    def add_event(self, event: RunEvent):
        """Add an event to a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO run_events (id, run_id, ts, event_type, message, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s);
                """, (
                    event.id, event.run_id, event.ts,
                    event.event_type, event.message, json.dumps(event.payload),
                ))
            conn.commit()
    
    def get_events(self, run_id: str, event_type: Optional[str] = None) -> list[RunEvent]:
        """Get events for a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                if event_type:
                    cur.execute("""
                        SELECT id, run_id, ts, event_type, message, payload_json
                        FROM run_events
                        WHERE run_id = %s AND event_type = %s
                        ORDER BY ts;
                    """, (run_id, event_type))
                else:
                    cur.execute("""
                        SELECT id, run_id, ts, event_type, message, payload_json
                        FROM run_events
                        WHERE run_id = %s
                        ORDER BY ts;
                    """, (run_id,))
                rows = cur.fetchall()
        
        return [
            RunEvent(
                id=str(row[0]),
                run_id=str(row[1]),
                ts=row[2],
                event_type=row[3],
                message=row[4],
                payload=json.loads(row[5]) if row[5] else {},
            )
            for row in rows
        ]
