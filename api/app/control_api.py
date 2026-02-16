"""
Control Plane API
=================

Endpoints for creating, triggering, and managing runs.

POST /v1/runs/scan          - Create and enqueue a SCAN run
POST /v1/runs/backtest      - Create and enqueue a BACKTEST run  
POST /v1/runs/paper/start   - Start a PAPER trading run
POST /v1/runs/paper/stop    - Stop a PAPER trading run
POST /v1/runs/{id}/cancel   - Cancel a pending/running run
GET  /v1/runs/queue         - View run queue status
GET  /v1/paper/status       - Get paper trading status

This is the "command" side of the API (vs the "query" side in runs_api.py).
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg
import redis
from fastapi import APIRouter, Query, HTTPException, Body
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Redis client for queue operations
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def get_conn():
    """Get database connection."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    return psycopg.connect(DATABASE_URL)


router = APIRouter(prefix="/control", tags=["control-plane"])


# =============================================================================
# Request Models
# =============================================================================

class ScanRunRequest(BaseModel):
    """Request to create a SCAN run."""
    name: Optional[str] = None
    symbols: Optional[list[str]] = None  # If None, use default universe
    timeframes: Optional[list[str]] = None  # If None, use default
    setup_ids: list[str] = Field(default=["S1", "S2"])
    top_n: int = Field(default=10, ge=1, le=100)
    priority: int = Field(default=0, ge=0, le=10)


class BacktestRunRequest(BaseModel):
    """Request to create a BACKTEST run."""
    name: Optional[str] = None
    symbols: list[str]
    timeframes: list[str]
    start_date: str  # ISO format: "2024-01-01"
    end_date: str    # ISO format: "2024-12-31"
    setup_ids: list[str] = Field(default=["S1", "S2"])
    
    # Execution config
    slippage_bps: float = Field(default=5.0)
    fee_bps: float = Field(default=10.0)
    
    # Data version (optional)
    data_snapshot_id: Optional[str] = None


class PaperStartRequest(BaseModel):
    """Request to start PAPER trading."""
    name: Optional[str] = None
    symbols: Optional[list[str]] = None
    timeframes: Optional[list[str]] = None
    setup_ids: list[str] = Field(default=["S1", "S2"])
    
    # Capital
    initial_capital: float = Field(default=10000.0, gt=0)
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=5.0)
    max_positions: int = Field(default=3, ge=1, le=10)
    
    # Auto-trade configuration
    auto_trade: bool = Field(default=True, description="Automatically open positions from signals")
    auto_trade_top_n: int = Field(default=3, ge=1, le=10, description="Take top N signals per scan")
    min_score: float = Field(default=0.0, description="Minimum signal score to auto-trade")


# =============================================================================
# Scan Endpoints
# =============================================================================

@router.post("/runs/scan")
def create_scan_run(request: ScanRunRequest):
    """
    Create and enqueue a SCAN run.
    
    Worker will pick it up from the queue.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    # Build specs
    universe_json = json.dumps({
        "assets": sorted(request.symbols) if request.symbols else [],
        "timeframes": sorted(request.timeframes) if request.timeframes else ["1h", "4h"],
        "venue": "binance",
    })
    
    strategy_json = json.dumps({
        "setup_id": ",".join(request.setup_ids),
        "setup_version": "v2.0",
        "setup_params": {"top_n": request.top_n},
    })
    
    execution_json = json.dumps({
        "entry_mode": "NEXT_OPEN",
        "slippage_entry_bps": 5.0,
        "fee_taker_bps": 4.0,
    })
    
    data_json = json.dumps({
        "data_source": "binance_rest",
        "data_snapshot_ts": now.isoformat(),
    })
    
    # Compute fingerprint
    import hashlib
    fp_input = f"{universe_json}{strategy_json}{execution_json}"
    fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]
    
    name = request.name or f"scan_{now.strftime('%Y%m%d_%H%M%S')}"
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Create run
            cur.execute("""
                INSERT INTO runs (
                    id, run_type, status, name, fingerprint,
                    engine_version, universe_json, strategy_json,
                    execution_json, data_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (
                run_id, "SCAN", "PENDING", name, fingerprint,
                "1.0.0", universe_json, strategy_json,
                execution_json, data_json, now,
            ))
            
            # Add to queue
            cur.execute("""
                INSERT INTO run_queue (id, run_id, priority, queued_at)
                VALUES (%s, %s, %s, %s);
            """, (str(uuid.uuid4()), run_id, request.priority, now))
        
        conn.commit()
    
    # Notify worker via Redis
    r.publish("run_queue", json.dumps({"run_id": run_id, "type": "SCAN"}))
    
    return {
        "run_id": run_id,
        "status": "QUEUED",
        "name": name,
        "fingerprint": fingerprint,
        "message": "Run created and queued for execution",
    }


@router.post("/runs/backtest")
def create_backtest_run(request: BacktestRunRequest):
    """
    Create and enqueue a BACKTEST run.
    
    Requires OHLCV data to be available in the database.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    # Parse dates
    try:
        start_ts = datetime.fromisoformat(request.start_date)
        end_ts = datetime.fromisoformat(request.end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")
    
    if start_ts >= end_ts:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")
    
    # Build specs
    universe_json = json.dumps({
        "assets": sorted(request.symbols),
        "timeframes": sorted(request.timeframes),
        "start_ts": start_ts.isoformat(),
        "end_ts": end_ts.isoformat(),
        "venue": "binance",
    })
    
    strategy_json = json.dumps({
        "setup_id": ",".join(request.setup_ids),
        "setup_version": "v2.0",
        "setup_params": {},
    })
    
    execution_json = json.dumps({
        "entry_mode": "NEXT_OPEN",
        "slippage_entry_bps": request.slippage_bps,
        "slippage_exit_bps": request.slippage_bps,
        "fee_taker_bps": request.fee_bps,
        "stop_fill_on": "TOUCH",
        "target_fill_on": "TOUCH",
    })
    
    data_json = json.dumps({
        "data_source": "postgres_ohlcv",
        "data_snapshot_id": request.data_snapshot_id,
        "data_snapshot_ts": now.isoformat(),
    })
    
    # Compute fingerprint
    import hashlib
    fp_input = f"{universe_json}{strategy_json}{execution_json}{data_json}"
    fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]
    
    name = request.name or f"backtest_{start_ts.strftime('%Y%m%d')}_{end_ts.strftime('%Y%m%d')}"
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Check if we have data for this range
            cur.execute("""
                SELECT COUNT(*) FROM ohlcv
                WHERE symbol = ANY(%s)
                AND timeframe = ANY(%s)
                AND open_time >= %s AND open_time <= %s;
            """, (request.symbols, request.timeframes, start_ts, end_ts))
            data_count = cur.fetchone()[0]
            
            if data_count == 0:
                raise HTTPException(
                    status_code=400,
                    detail="No OHLCV data available for requested range. Ingest data first."
                )
            
            # Create run
            cur.execute("""
                INSERT INTO runs (
                    id, run_type, status, name, fingerprint,
                    engine_version, universe_json, strategy_json,
                    execution_json, data_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (
                run_id, "BACKTEST", "PENDING", name, fingerprint,
                "1.0.0", universe_json, strategy_json,
                execution_json, data_json, now,
            ))
            
            # Add to queue
            cur.execute("""
                INSERT INTO run_queue (id, run_id, priority, queued_at)
                VALUES (%s, %s, %s, %s);
            """, (str(uuid.uuid4()), run_id, 5, now))  # Higher priority for backtests
        
        conn.commit()
    
    return {
        "run_id": run_id,
        "status": "QUEUED",
        "name": name,
        "fingerprint": fingerprint,
        "data_bars_available": data_count,
        "message": "Backtest run created and queued",
    }


# =============================================================================
# Paper Trading Endpoints
# =============================================================================

@router.post("/runs/paper/start")
def start_paper_trading(request: PaperStartRequest):
    """
    Start a PAPER trading run.
    
    Creates persistent account state and begins tracking positions.
    
    Auto-trade mode:
    - When enabled, automatically opens positions from scan signals
    - Uses top_n best scoring signals per scan
    - Respects max_positions limit
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    # Build specs
    universe_json = json.dumps({
        "assets": sorted(request.symbols) if request.symbols else [],
        "timeframes": sorted(request.timeframes) if request.timeframes else ["1h", "4h"],
        "venue": "binance",
    })
    
    strategy_json = json.dumps({
        "setup_id": ",".join(request.setup_ids),
        "setup_version": "v2.0",
        "setup_params": {
            "risk_per_trade_pct": request.risk_per_trade_pct,
            "max_positions": request.max_positions,
            "auto_trade": request.auto_trade,
            "auto_trade_top_n": request.auto_trade_top_n,
            "min_score": request.min_score,
        },
    })
    
    execution_json = json.dumps({
        "entry_mode": "NEXT_OPEN",
        "slippage_entry_bps": 5.0,
        "slippage_exit_bps": 5.0,
        "fee_maker_bps": 2.0,
        "fee_taker_bps": 4.0,
        "max_positions": request.max_positions,
    })
    
    data_json = json.dumps({
        "data_source": "binance_rest_live",
        "data_snapshot_ts": now.isoformat(),
    })
    
    # Compute fingerprint
    import hashlib
    fp_input = f"{universe_json}{strategy_json}{execution_json}"
    fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]
    
    name = request.name or f"paper_{now.strftime('%Y%m%d_%H%M%S')}"
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Check for existing active paper run
            cur.execute("""
                SELECT id FROM runs
                WHERE run_type = 'PAPER' AND status = 'RUNNING'
                LIMIT 1;
            """)
            existing = cur.fetchone()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"Paper trading run already active: {existing[0]}. Stop it first."
                )
            
            # Create run
            cur.execute("""
                INSERT INTO runs (
                    id, run_type, status, name, fingerprint,
                    engine_version, universe_json, strategy_json,
                    execution_json, data_json, created_at, started_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (
                run_id, "PAPER", "RUNNING", name, fingerprint,
                "1.0.0", universe_json, strategy_json,
                execution_json, data_json, now, now,
            ))
            
            # Create paper account
            cur.execute("""
                INSERT INTO paper_accounts (
                    run_id, initial_capital, current_capital, peak_capital,
                    daily_pnl_reset_date
                ) VALUES (%s, %s, %s, %s, %s);
            """, (
                run_id, request.initial_capital, request.initial_capital,
                request.initial_capital, now.date(),
            ))
        
        conn.commit()
    
    # Notify worker
    r.set("paper:active_run_id", run_id)
    r.publish("paper_control", json.dumps({
        "action": "start",
        "run_id": run_id,
        "auto_trade": request.auto_trade,
    }))
    
    return {
        "run_id": run_id,
        "status": "RUNNING",
        "name": name,
        "fingerprint": fingerprint,
        "initial_capital": request.initial_capital,
        "max_positions": request.max_positions,
        "risk_per_trade_pct": request.risk_per_trade_pct,
        "auto_trade": request.auto_trade,
        "auto_trade_top_n": request.auto_trade_top_n,
        "min_score": request.min_score,
        "message": "Paper trading started" + (" with auto-trade enabled" if request.auto_trade else ""),
    }


@router.post("/runs/paper/stop")
def stop_paper_trading():
    """
    Stop the active PAPER trading run.
    
    Closes all positions at market and finalizes the run.
    """
    active_run_id = r.get("paper:active_run_id")
    
    if not active_run_id:
        raise HTTPException(status_code=404, detail="No active paper trading run")
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get run
            cur.execute("""
                SELECT id, status FROM runs WHERE id = %s;
            """, (active_run_id,))
            row = cur.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")
            
            if row[1] != "RUNNING":
                raise HTTPException(status_code=400, detail=f"Run is not running: {row[1]}")
            
            # Get account stats for summary
            cur.execute("""
                SELECT total_trades, winning_trades, losing_trades, total_pnl,
                       total_fees, max_drawdown, current_capital, initial_capital
                FROM paper_accounts WHERE run_id = %s;
            """, (active_run_id,))
            account = cur.fetchone()
            
            if account:
                summary = {
                    "total_trades": account[0],
                    "winning_trades": account[1],
                    "losing_trades": account[2],
                    "win_rate": account[1] / max(1, account[0]),
                    "net_pnl": account[3],
                    "total_fees": account[4],
                    "max_drawdown_pct": account[5] * 100,
                    "final_capital": account[6],
                    "return_pct": ((account[6] - account[7]) / account[7]) * 100,
                }
            else:
                summary = {}
            
            # Update run status
            cur.execute("""
                UPDATE runs
                SET status = 'SUCCESS', finished_at = %s, summary_json = %s
                WHERE id = %s;
            """, (datetime.now(timezone.utc), json.dumps(summary), active_run_id))
            
            # Close any open positions
            cur.execute("""
                UPDATE paper_positions
                SET status = 'CLOSED', exit_reason = 'MANUAL_STOP',
                    exit_ts = %s, updated_at = %s
                WHERE run_id = %s AND status = 'OPEN';
            """, (datetime.now(timezone.utc), datetime.now(timezone.utc), active_run_id))
        
        conn.commit()
    
    # Clear active run
    r.delete("paper:active_run_id")
    r.publish("paper_control", json.dumps({"action": "stop", "run_id": active_run_id}))
    
    return {
        "run_id": active_run_id,
        "status": "SUCCESS",
        "summary": summary,
        "message": "Paper trading stopped",
    }


@router.get("/paper/status")
def get_paper_status():
    """
    Get current paper trading status.
    
    Returns account state, open positions, and recent trades.
    """
    active_run_id = r.get("paper:active_run_id")
    
    if not active_run_id:
        return {"active": False, "message": "No active paper trading run"}
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get run
            cur.execute("""
                SELECT id, name, started_at, strategy_json
                FROM runs WHERE id = %s;
            """, (active_run_id,))
            run = cur.fetchone()
            
            if not run:
                return {"active": False, "message": "Run not found"}
            
            # Get account
            cur.execute("""
                SELECT initial_capital, current_capital, peak_capital,
                       total_trades, winning_trades, losing_trades,
                       total_pnl, total_fees, max_drawdown, current_exposure
                FROM paper_accounts WHERE run_id = %s;
            """, (active_run_id,))
            account = cur.fetchone()
            
            # Get open positions
            cur.execute("""
                SELECT symbol, side, setup_id, entry_price, stop_price,
                       tp1_price, current_price, unrealized_pnl, bars_held
                FROM paper_positions
                WHERE run_id = %s AND status = 'OPEN'
                ORDER BY created_at DESC;
            """, (active_run_id,))
            positions = cur.fetchall()
            
            # Get recent closed
            cur.execute("""
                SELECT symbol, side, setup_id, entry_price, exit_price,
                       exit_reason, realized_pnl, r_multiple, exit_ts
                FROM paper_positions
                WHERE run_id = %s AND status = 'CLOSED'
                ORDER BY exit_ts DESC
                LIMIT 10;
            """, (active_run_id,))
            recent_closed = cur.fetchall()
    
    return {
        "active": True,
        "run_id": active_run_id,
        "run_name": run[1],
        "started_at": run[2].isoformat() if run[2] else None,
        "account": {
            "initial_capital": account[0],
            "current_capital": account[1],
            "peak_capital": account[2],
            "total_trades": account[3],
            "winning_trades": account[4],
            "losing_trades": account[5],
            "win_rate": account[4] / max(1, account[3]),
            "total_pnl": account[6],
            "total_fees": account[7],
            "max_drawdown": account[8],
            "current_exposure": account[9],
        } if account else {},
        "open_positions": [
            {
                "symbol": p[0],
                "side": p[1],
                "setup_id": p[2],
                "entry_price": p[3],
                "stop_price": p[4],
                "tp1_price": p[5],
                "current_price": p[6],
                "unrealized_pnl": p[7],
                "bars_held": p[8],
            }
            for p in positions
        ],
        "recent_trades": [
            {
                "symbol": t[0],
                "side": t[1],
                "setup_id": t[2],
                "entry_price": t[3],
                "exit_price": t[4],
                "exit_reason": t[5],
                "realized_pnl": t[6],
                "r_multiple": t[7],
                "exit_ts": t[8].isoformat() if t[8] else None,
            }
            for t in recent_closed
        ],
    }


# =============================================================================
# Run Control Endpoints
# =============================================================================

@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """
    Cancel a pending or running run.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status FROM runs WHERE id = %s;
            """, (run_id,))
            row = cur.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")
            
            status = row[0]
            if status not in ("PENDING", "RUNNING"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel run in status: {status}"
                )
            
            # Update run
            cur.execute("""
                UPDATE runs
                SET status = 'CANCELED', finished_at = %s
                WHERE id = %s;
            """, (datetime.now(timezone.utc), run_id))
            
            # Update queue
            cur.execute("""
                UPDATE run_queue
                SET status = 'CANCELED', completed_at = %s
                WHERE run_id = %s;
            """, (datetime.now(timezone.utc), run_id))
        
        conn.commit()
    
    # Notify worker
    r.publish("run_control", json.dumps({"action": "cancel", "run_id": run_id}))
    
    return {
        "run_id": run_id,
        "status": "CANCELED",
        "message": "Run canceled",
    }


@router.get("/queue")
def get_queue_status():
    """
    Get run queue status.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT rq.run_id, rq.status, rq.priority, rq.queued_at,
                       rq.claimed_by, rq.claimed_at, rq.attempt_count,
                       r.run_type, r.name
                FROM run_queue rq
                JOIN runs r ON rq.run_id = r.id
                WHERE rq.status IN ('QUEUED', 'CLAIMED')
                ORDER BY rq.priority DESC, rq.queued_at
                LIMIT 50;
            """)
            rows = cur.fetchall()
    
    return {
        "queue": [
            {
                "run_id": str(row[0]),
                "status": row[1],
                "priority": row[2],
                "queued_at": row[3].isoformat() if row[3] else None,
                "claimed_by": row[4],
                "claimed_at": row[5].isoformat() if row[5] else None,
                "attempts": row[6],
                "run_type": row[7],
                "run_name": row[8],
            }
            for row in rows
        ],
        "count": len(rows),
    }
