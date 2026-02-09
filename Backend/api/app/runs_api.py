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
import time
from datetime import datetime, timezone
from typing import Optional, Any

import csv
import io

import psycopg
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import Response

# We'll import these dynamically since we're in API, not worker
DATABASE_URL = os.getenv("DATABASE_URL")


def envelope(data: Any, compute_start: float, as_of_ts: float = None) -> dict:
    """Standard response envelope with metadata.

    Canon §1.5A: Every response includes as_of_ts (backend data freshness).
    """
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "v1",
        "compute_ms": round((time.time() - compute_start) * 1000, 1),
    }
    if as_of_ts is not None:
        meta["as_of_ts"] = datetime.fromtimestamp(as_of_ts, tz=timezone.utc).isoformat()
    else:
        meta["as_of_ts"] = datetime.now(timezone.utc).isoformat()
    return {"data": data, "meta": meta}


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
    t0 = time.time()
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
    
    return envelope({
        "runs": runs,
        "count": len(runs),
    }, t0)


@router.get("/latest")
def get_latest_run(
    run_type: str = Query(default="SCAN", description="Run type to filter"),
):
    """Get the most recent successful run of a given type."""
    t0 = time.time()
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
    
    return envelope({
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
    }, t0)


@router.get("/{run_id}")
def get_run(run_id: str):
    """Get full run details by ID."""
    t0 = time.time()
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
    
    return envelope({
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
        "data_spec": json.loads(row[10]) if row[10] else {},
        "created_at": row[11].isoformat() if row[11] else None,
        "started_at": row[12].isoformat() if row[12] else None,
        "finished_at": row[13].isoformat() if row[13] else None,
        "summary": json.loads(row[14]) if row[14] else {},
        "error": row[15],
        "parent_run_id": str(row[16]) if row[16] else None,
    }, t0)


@router.get("/{run_id}/trades")
def get_run_trades(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get all trades/signals for a run."""
    t0 = time.time()
    with get_conn() as conn:
        with conn.cursor() as cur:
            # First verify run exists
            cur.execute("SELECT id FROM runs WHERE id = %s;", (run_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Run not found")
            
            # Get trades (Canon 5A: include signal_bar_ts; Canon 5E: since_ts, action_by_ts)
            cur.execute("""
                SELECT id, symbol, timeframe, side, setup_id,
                       signal_bar_ts, since_ts, action_by_ts,
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
        trace = json.loads(row[20]) if row[20] else {}
        trades.append({
            "id": str(row[0]),
            "symbol": row[1],
            "timeframe": row[2],
            "side": row[3],
            "setup_id": row[4],
            "signal_bar_ts": row[5].isoformat() if row[5] else None,
            "since_ts": row[6].isoformat() if row[6] else None,
            "action_by_ts": row[7].isoformat() if row[7] else None,
            "entry_ts": row[8].isoformat() if row[8] else None,
            "entry_price": row[9],
            "stop_price": row[10],
            "tp1_price": row[11],
            "tp2_price": row[12],
            "r_value": row[13],
            "exit_ts": row[14].isoformat() if row[14] else None,
            "exit_price": row[15],
            "exit_reason": row[16],
            "r_multiple": row[17],
            "pnl_net": row[18],
            "setup_score": row[19],
            "trace": trace,
        })

    return envelope({
        "trades": trades,
        "count": len(trades),
    }, t0)


@router.get("/{run_id}/events")
def get_run_events(
    run_id: str,
    event_type: Optional[str] = Query(default=None, description="Filter by event type"),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get events for a run (for debugging without spelunking logs)."""
    t0 = time.time()
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
    
    return envelope({
        "events": events,
        "count": len(events),
    }, t0)


@router.get("/fingerprint/{fingerprint}")
def get_run_by_fingerprint(fingerprint: str):
    """
    Find runs by fingerprint.

    If fingerprint matches, the runs had identical inputs.
    Useful for checking if a run was already executed.
    """
    t0 = time.time()
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

    return envelope({
        "fingerprint": fingerprint,
        "runs": runs,
        "count": len(runs),
    }, t0)


@router.get("/{run_id}/simulation-results")
def get_simulation_results(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """
    Get simulation results for a SIMULATION run.

    Returns individual opportunity simulation outcomes with pagination.
    """
    t0 = time.time()
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Verify run exists and is SIMULATION type
            cur.execute("""
                SELECT run_type, status, summary_json FROM runs WHERE id = %s;
            """, (run_id,))
            run_row = cur.fetchone()

            if not run_row:
                raise HTTPException(status_code=404, detail="Run not found")

            if run_row[0] != "SIMULATION":
                raise HTTPException(
                    status_code=400,
                    detail=f"Run is not a SIMULATION run (type={run_row[0]})"
                )

            # Get summary from run
            summary = json.loads(run_row[2]) if run_row[2] else {}

            # Get total count
            cur.execute("""
                SELECT COUNT(*) FROM simulation_results WHERE run_id = %s;
            """, (run_id,))
            total_count = cur.fetchone()[0]

            # Get simulation results
            cur.execute("""
                SELECT id, opportunity_id, symbol, timeframe, setup_id, direction,
                       opportunity_created_ts, entry_ts, entry_price,
                       entry_price_with_slippage, position_size, stop_price,
                       tp1_price, tp2_price, r_value, exit_ts, exit_price,
                       exit_price_with_slippage, exit_reason, bars_held,
                       pnl_gross, fees, pnl_net, r_multiple,
                       data_available, error_message
                FROM simulation_results
                WHERE run_id = %s
                ORDER BY r_multiple DESC NULLS LAST, opportunity_id
                LIMIT %s OFFSET %s;
            """, (run_id, limit, offset))
            rows = cur.fetchall()

    results = []
    for row in rows:
        results.append({
            "id": str(row[0]),
            "opportunity_id": row[1],
            "symbol": row[2],
            "timeframe": row[3],
            "setup_id": row[4],
            "direction": row[5],
            "opportunity_created_ts": row[6].isoformat() if row[6] else None,
            "entry_ts": row[7].isoformat() if row[7] else None,
            "entry_price": row[8],
            "entry_price_with_slippage": row[9],
            "position_size": row[10],
            "stop_price": row[11],
            "tp1_price": row[12],
            "tp2_price": row[13],
            "r_value": row[14],
            "exit_ts": row[15].isoformat() if row[15] else None,
            "exit_price": row[16],
            "exit_price_with_slippage": row[17],
            "exit_reason": row[18],
            "bars_held": row[19],
            "pnl_gross": row[20],
            "fees": row[21],
            "pnl_net": row[22],
            "r_multiple": row[23],
            "data_available": row[24],
            "error_message": row[25],
        })

    # Compute aggregate stats from returned results if summary not available
    if not summary and results:
        valid_results = [r for r in results if r["data_available"] and r["pnl_net"] is not None]
        if valid_results:
            winners = [r for r in valid_results if r["pnl_net"] > 0]
            total_pnl = sum(r["pnl_net"] for r in valid_results)
            total_r = sum(r["r_multiple"] for r in valid_results if r["r_multiple"])
            summary = {
                "simulated_count": len(valid_results),
                "win_rate": len(winners) / len(valid_results) if valid_results else 0,
                "total_pnl_net": round(total_pnl, 2),
                "total_r": round(total_r, 2),
                "avg_r": round(total_r / len(valid_results), 4) if valid_results else 0,
            }

    return envelope({
        "run_id": run_id,
        "run_status": run_row[1],
        "summary": summary,
        "results": results,
        "count": len(results),
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    }, t0)


@router.get("/{run_id}/simulation-results/csv")
def export_simulation_results_csv(run_id: str):
    """Export all simulation results as CSV."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT run_type FROM runs WHERE id = %s;", (run_id,))
            run_row = cur.fetchone()
            if not run_row:
                raise HTTPException(status_code=404, detail="Run not found")
            if run_row[0] != "SIMULATION":
                raise HTTPException(status_code=400, detail="Not a SIMULATION run")

            cur.execute("""
                SELECT sr.symbol, sr.timeframe, sr.setup_id, sr.direction,
                       o.score AS opportunity_score,
                       sr.opportunity_created_ts, sr.entry_ts, sr.entry_price,
                       sr.entry_price_with_slippage, sr.position_size, sr.stop_price,
                       sr.tp1_price, sr.tp2_price, sr.r_value, sr.exit_ts, sr.exit_price,
                       sr.exit_price_with_slippage, sr.exit_reason, sr.bars_held,
                       sr.pnl_gross, sr.fees, sr.pnl_net, sr.r_multiple,
                       sr.data_available, sr.error_message
                FROM simulation_results sr
                LEFT JOIN opportunities o ON o.id = sr.opportunity_id
                WHERE sr.run_id = %s
                ORDER BY sr.r_multiple DESC NULLS LAST, sr.opportunity_id;
            """, (run_id,))
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([
            v.isoformat() if hasattr(v, 'isoformat') else v
            for v in row
        ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=sim_{run_id[:8]}.csv"},
    )
