"""
Runs API
========

Endpoints for querying Run history, trades, and events.

GET /v1/runs              - List runs with filters
GET /v1/runs/{id}         - Get run details
GET /v1/runs/{id}/trades  - Get trades for a run
GET /v1/runs/{id}/events  - Get events for a run
GET /v1/runs/latest       - Get most recent successful scan
POST /v1/runs/compare     - Compare two runs by fingerprint
"""

import os
import json
from datetime import datetime, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, Query, HTTPException

# We'll import these dynamically since we're in API, not worker
DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    """Get database connection."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    return psycopg.connect(DATABASE_URL)


router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
def list_runs(
    run_type: Optional[str] = Query(default=None, description="Filter by run type"),
    status: Optional[str] = Query(default=None, description="Filter by status"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List runs with optional filters."""
    conditions = []
    params = []
    
    if run_type:
        conditions.append("run_type = %s")
        params.append(run_type.upper())
    if status:
        conditions.append("status = %s")
        params.append(status.upper())
    
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, run_type, status, name, fingerprint,
                       created_at, started_at, finished_at,
                       summary_json, error_text
                FROM runs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s;
            """, params + [limit])
            rows = cur.fetchall()
    
    runs = []
    for row in rows:
        summary = json.loads(row[8]) if row[8] else {}
        runs.append({
            "id": str(row[0]),
            "run_type": row[1],
            "status": row[2],
            "name": row[3],
            "fingerprint": row[4],
            "created_at": row[5].isoformat() if row[5] else None,
            "started_at": row[6].isoformat() if row[6] else None,
            "finished_at": row[7].isoformat() if row[7] else None,
            "summary": summary,
            "error": row[9],
        })
    
    return {"runs": runs, "count": len(runs)}


@router.get("/latest")
def get_latest_run(
    run_type: str = Query(default="SCAN", description="Run type to filter"),
):
    """Get the most recent successful run of a given type."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, run_type, status, name, fingerprint,
                       created_at, started_at, finished_at,
                       universe_json, strategy_json, summary_json
                FROM runs
                WHERE run_type = %s AND status = 'SUCCESS'
                ORDER BY finished_at DESC
                LIMIT 1;
            """, (run_type.upper(),))
            row = cur.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="No successful runs found")
    
    return {
        "id": str(row[0]),
        "run_type": row[1],
        "status": row[2],
        "name": row[3],
        "fingerprint": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
        "started_at": row[6].isoformat() if row[6] else None,
        "finished_at": row[7].isoformat() if row[7] else None,
        "universe": json.loads(row[8]) if row[8] else {},
        "strategy": json.loads(row[9]) if row[9] else {},
        "summary": json.loads(row[10]) if row[10] else {},
    }


@router.get("/{run_id}")
def get_run(run_id: str):
    """Get full run details by ID."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, run_type, status, name, notes, fingerprint,
                       engine_version,
                       universe_json, strategy_json, execution_json, data_json,
                       created_at, started_at, finished_at,
                       summary_json, error_text, parent_run_id
                FROM runs
                WHERE id = %s;
            """, (run_id,))
            row = cur.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    
    return {
        "id": str(row[0]),
        "run_type": row[1],
        "status": row[2],
        "name": row[3],
        "notes": row[4],
        "fingerprint": row[5],
        "engine_version": row[6],
        "universe": json.loads(row[7]) if row[7] else {},
        "strategy": json.loads(row[8]) if row[8] else {},
        "execution": json.loads(row[9]) if row[9] else {},
        "data": json.loads(row[10]) if row[10] else {},
        "created_at": row[11].isoformat() if row[11] else None,
        "started_at": row[12].isoformat() if row[12] else None,
        "finished_at": row[13].isoformat() if row[13] else None,
        "summary": json.loads(row[14]) if row[14] else {},
        "error": row[15],
        "parent_run_id": str(row[16]) if row[16] else None,
    }


@router.get("/{run_id}/trades")
def get_run_trades(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get all trades/signals for a run."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # First verify run exists
            cur.execute("SELECT id FROM runs WHERE id = %s;", (run_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Run not found")
            
            # Get trades
            cur.execute("""
                SELECT id, asset, timeframe, side, setup_id,
                       entry_ts, entry_price,
                       stop_price, tp1_price, tp2_price, r_value,
                       exit_ts, exit_price, exit_reason,
                       r_multiple, pnl_net,
                       setup_score, trace_json
                FROM run_trades
                WHERE run_id = %s
                ORDER BY setup_score DESC
                LIMIT %s;
            """, (run_id, limit))
            rows = cur.fetchall()
    
    trades = []
    for row in rows:
        trace = json.loads(row[17]) if row[17] else {}
        trades.append({
            "id": str(row[0]),
            "asset": row[1],
            "timeframe": row[2],
            "side": row[3],
            "setup_id": row[4],
            "entry_ts": row[5].isoformat() if row[5] else None,
            "entry_price": row[6],
            "stop_price": row[7],
            "tp1_price": row[8],
            "tp2_price": row[9],
            "r_value": row[10],
            "exit_ts": row[11].isoformat() if row[11] else None,
            "exit_price": row[12],
            "exit_reason": row[13],
            "r_multiple": row[14],
            "pnl_net": row[15],
            "setup_score": row[16],
            "trace": trace,
        })
    
    return {"trades": trades, "count": len(trades)}


@router.get("/{run_id}/events")
def get_run_events(
    run_id: str,
    event_type: Optional[str] = Query(default=None, description="Filter by event type"),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get events for a run (for debugging without spelunking logs)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # First verify run exists
            cur.execute("SELECT id FROM runs WHERE id = %s;", (run_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Run not found")
            
            # Get events
            if event_type:
                cur.execute("""
                    SELECT id, ts, event_type, message, payload_json
                    FROM run_events
                    WHERE run_id = %s AND event_type = %s
                    ORDER BY ts
                    LIMIT %s;
                """, (run_id, event_type.upper(), limit))
            else:
                cur.execute("""
                    SELECT id, ts, event_type, message, payload_json
                    FROM run_events
                    WHERE run_id = %s
                    ORDER BY ts
                    LIMIT %s;
                """, (run_id, limit))
            rows = cur.fetchall()
    
    events = []
    for row in rows:
        events.append({
            "id": str(row[0]),
            "ts": row[1].isoformat() if row[1] else None,
            "event_type": row[2],
            "message": row[3],
            "payload": json.loads(row[4]) if row[4] else {},
        })
    
    return {"events": events, "count": len(events)}


@router.get("/fingerprint/{fingerprint}")
def get_run_by_fingerprint(fingerprint: str):
    """
    Find runs by fingerprint.
    
    If fingerprint matches, the runs had identical inputs.
    Useful for checking if a run was already executed.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, run_type, status, name, created_at, finished_at, summary_json
                FROM runs
                WHERE fingerprint = %s
                ORDER BY created_at DESC;
            """, (fingerprint,))
            rows = cur.fetchall()
    
    runs = []
    for row in rows:
        runs.append({
            "id": str(row[0]),
            "run_type": row[1],
            "status": row[2],
            "name": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "finished_at": row[5].isoformat() if row[5] else None,
            "summary": json.loads(row[6]) if row[6] else {},
        })
    
    return {
        "fingerprint": fingerprint,
        "runs": runs,
        "count": len(runs),
    }
