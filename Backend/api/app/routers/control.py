"""
Control Plane API
=================

Endpoints for creating, triggering, and managing runs.

POST /runs/scan          - Create and enqueue a SCAN run
POST /runs/backtest      - Create and enqueue a BACKTEST run
POST /runs/paper/start   - Start a PAPER trading run
POST /runs/paper/stop    - Stop a PAPER trading run
POST /runs/{id}/cancel   - Cancel a pending/running run
GET  /runs/queue         - View run queue status
GET  /paper/status       - Get paper trading status
POST /runs/simulation    - Create and enqueue a SIMULATION run
"""

import re
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

import psycopg
import redis
from fastapi import APIRouter, Query, HTTPException, Body
from pydantic import BaseModel, Field, model_validator

from api.app.db_pool import get_conn as _pool_get_conn
from governance.bootstrap import get_config, strategy_active_setup_ids
from governance.cache_contract import TF_SECONDS, STALENESS_MULTIPLIER

_worker_cfg = get_config().Worker
r = redis.Redis.from_url(_worker_cfg.REDIS_URL, decode_responses=True)


def get_conn():
    """Get database connection from the pool."""
    if not get_config().Worker.DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    return _pool_get_conn()


def _resolve_symbols(requested: list[str] | None) -> list[str]:
    """Resolve symbol list: use requested symbols, else read universe:monitored from Redis."""
    if requested:
        return sorted(requested)
    try:
        members = r.smembers("universe:monitored")
        if members:
            return sorted(members)
    except Exception:
        pass
    return []


def envelope(data: Any, compute_start: float, as_of_ts: float = None) -> dict:
    """Standard response envelope with metadata."""
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


router = APIRouter(prefix="/control", tags=["control-plane"])


# =============================================================================
# Request Models
# =============================================================================

class ScanRequest(BaseModel):
    """Request body for a SCAN run."""
    name: Optional[str] = None
    symbols: Optional[list[str]] = None
    timeframes: list[str] = Field(default=["1h"])
    setup_ids: Optional[list[str]] = Field(default=None, description="Setup IDs to evaluate. Defaults to all active strategies.")
    notes: Optional[str] = None


class BacktestRequest(BaseModel):
    """Request body for a BACKTEST run."""
    name: Optional[str] = None
    symbols: Optional[list[str]] = None
    timeframes: list[str] = Field(default=["1h"])
    setup_ids: Optional[list[str]] = Field(default=None, description="Setup IDs to evaluate. Defaults to all active strategies.")
    lookback_days: int = Field(default=30, ge=1, le=365)
    notes: Optional[str] = None


class SimulationRequest(BaseModel):
    """Request body for a SIMULATION run."""
    name: Optional[str] = None
    setup_ids: Optional[list[str]] = None
    timeframes: Optional[list[str]] = None
    exit_configs: Optional[list[str]] = None
    stop_policy: Optional[dict] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    batch_id: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self):
        _iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if self.from_date and not _iso_re.match(self.from_date):
            raise ValueError(f"from_date must be YYYY-MM-DD, got '{self.from_date}'")
        if self.to_date and not _iso_re.match(self.to_date):
            raise ValueError(f"to_date must be YYYY-MM-DD, got '{self.to_date}'")
        return self


class PaperStartRequest(BaseModel):
    """Request body to start a PAPER trading run."""
    name: Optional[str] = None
    initial_capital: float = Field(default=10000.0, ge=100)
    setup_ids: Optional[list[str]] = Field(default=None, description="Setup IDs to evaluate. Defaults to all active strategies.")
    timeframes: list[str] = Field(default=["1h"])
    max_positions: int = Field(default=3, ge=1, le=20)
    risk_per_trade_pct: float = Field(default=1.0, ge=0.1, le=5.0)
    notes: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/runs/scan")
def create_scan_run(req: ScanRequest):
    """Create and enqueue a SCAN run."""
    if req.setup_ids is None:
        req.setup_ids = strategy_active_setup_ids()
    t0 = time.time()
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    symbols = _resolve_symbols(req.symbols)
    name = req.name or f"scan_{now.strftime('%Y%m%d_%H%M%S')}"

    strategy_json = json.dumps({
        "setup_id": ",".join(req.setup_ids),
        "setup_params": {"setup_ids": req.setup_ids},
    })
    universe_json = json.dumps({"symbols": symbols, "count": len(symbols)})
    execution_json = json.dumps({"timeframes": req.timeframes})

    import hashlib
    fp_input = f"{strategy_json}{universe_json}{execution_json}"
    fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO runs (id, run_type, status, name, notes, fingerprint,
                                  engine_version, universe_json, strategy_json,
                                  execution_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                run_id, "SCAN", "QUEUED", name, req.notes, fingerprint,
                "1.0.0", universe_json, strategy_json, execution_json, now,
            ))

            cur.execute("""
                INSERT INTO run_queue (run_id, status, priority, created_at)
                VALUES (%s, %s, %s, %s);
            """, (run_id, "QUEUED", 5, now))
        conn.commit()

    r.publish("run_queue", json.dumps({"run_id": run_id, "run_type": "SCAN"}))

    return envelope({
        "run_id": run_id,
        "status": "QUEUED",
        "name": name,
        "fingerprint": fingerprint,
        "symbols_count": len(symbols),
    }, t0)


@router.post("/runs/backtest")
def create_backtest_run(req: BacktestRequest):
    """Create and enqueue a BACKTEST run."""
    if req.setup_ids is None:
        req.setup_ids = strategy_active_setup_ids()
    t0 = time.time()
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    symbols = _resolve_symbols(req.symbols)
    name = req.name or f"backtest_{now.strftime('%Y%m%d_%H%M%S')}"

    strategy_json = json.dumps({
        "setup_id": ",".join(req.setup_ids),
        "setup_params": {"setup_ids": req.setup_ids, "lookback_days": req.lookback_days},
    })
    universe_json = json.dumps({"symbols": symbols, "count": len(symbols)})
    execution_json = json.dumps({"timeframes": req.timeframes})

    import hashlib
    fp_input = f"{strategy_json}{universe_json}{execution_json}"
    fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO runs (id, run_type, status, name, notes, fingerprint,
                                  engine_version, universe_json, strategy_json,
                                  execution_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                run_id, "BACKTEST", "QUEUED", name, req.notes, fingerprint,
                "1.0.0", universe_json, strategy_json, execution_json, now,
            ))

            cur.execute("""
                INSERT INTO run_queue (run_id, status, priority, created_at)
                VALUES (%s, %s, %s, %s);
            """, (run_id, "QUEUED", 5, now))
        conn.commit()

    r.publish("run_queue", json.dumps({"run_id": run_id, "run_type": "BACKTEST"}))

    return envelope({
        "run_id": run_id,
        "status": "QUEUED",
        "name": name,
        "fingerprint": fingerprint,
        "symbols_count": len(symbols),
    }, t0)


@router.post("/runs/simulation")
def create_simulation_run(req: SimulationRequest):
    """Create and enqueue a SIMULATION run."""
    t0 = time.time()
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    name = req.name or f"sim_{now.strftime('%Y%m%d_%H%M%S')}"

    strategy_json = json.dumps({
        "setup_id": ",".join(req.setup_ids) if req.setup_ids else "ALL",
        "setup_params": {
            "setup_ids": req.setup_ids,
            "exit_configs": req.exit_configs,
            "stop_policy": req.stop_policy,
            "from_date": req.from_date,
            "to_date": req.to_date,
            "batch_id": req.batch_id,
        },
    })
    execution_json = json.dumps({
        "timeframes": req.timeframes,
        "exit_configs": req.exit_configs,
    })

    import hashlib
    fp_input = f"{strategy_json}{execution_json}{run_id}"
    fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO runs (id, run_type, status, name, notes, fingerprint,
                                  engine_version, strategy_json, execution_json,
                                  created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                run_id, "SIMULATION", "QUEUED", name, req.notes, fingerprint,
                "1.0.0", strategy_json, execution_json, now,
            ))

            cur.execute("""
                INSERT INTO run_queue (run_id, status, priority, created_at)
                VALUES (%s, %s, %s, %s);
            """, (run_id, "QUEUED", 5, now))
        conn.commit()

    r.publish("run_queue", json.dumps({"run_id": run_id, "run_type": "SIMULATION"}))

    return envelope({
        "run_id": run_id,
        "status": "QUEUED",
        "name": name,
        "fingerprint": fingerprint,
    }, t0)


@router.post("/runs/paper/start")
def start_paper_trading(req: PaperStartRequest):
    """Start a PAPER trading run."""
    if req.setup_ids is None:
        req.setup_ids = strategy_active_setup_ids()
    t0 = time.time()
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    name = req.name or f"paper_{now.strftime('%Y%m%d_%H%M%S')}"

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
                    detail=f"Paper trading already active: run_id={existing[0]}"
                )

            strategy_json = json.dumps({
                "setup_ids": req.setup_ids,
                "timeframes": req.timeframes,
                "max_positions": req.max_positions,
                "risk_per_trade_pct": req.risk_per_trade_pct,
            })

            cur.execute("""
                INSERT INTO runs (id, run_type, status, name, notes,
                                  engine_version, strategy_json,
                                  created_at, started_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                run_id, "PAPER", "RUNNING", name, req.notes,
                "1.0.0", strategy_json, now, now,
            ))

            cur.execute("""
                INSERT INTO paper_accounts (run_id, initial_capital, current_capital,
                                            peak_capital, created_at)
                VALUES (%s, %s, %s, %s, %s);
            """, (run_id, req.initial_capital, req.initial_capital, req.initial_capital, now))
        conn.commit()

    r.set("paper:active_run_id", run_id)
    r.publish("paper_control", json.dumps({"action": "start", "run_id": run_id}))

    return envelope({
        "run_id": run_id,
        "status": "RUNNING",
        "name": name,
        "initial_capital": req.initial_capital,
    }, t0)


@router.post("/runs/paper/stop")
def stop_paper_trading():
    """Stop the active PAPER trading run."""
    t0 = time.time()
    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM runs
                WHERE run_type = 'PAPER' AND status = 'RUNNING'
                ORDER BY started_at DESC
                LIMIT 1;
            """)
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No active paper trading session")

            run_id = str(row[0])

            cur.execute("""
                UPDATE runs SET status = 'COMPLETED', finished_at = %s
                WHERE id = %s;
            """, (now, run_id))
        conn.commit()

    r.delete("paper:active_run_id")
    r.publish("paper_control", json.dumps({"action": "stop", "run_id": run_id}))

    return envelope({
        "run_id": run_id,
        "status": "COMPLETED",
    }, t0)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """Cancel a pending/running run."""
    t0 = time.time()
    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status FROM runs WHERE id = %s;
            """, (run_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")

            if row[0] not in ("QUEUED", "RUNNING"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel run with status '{row[0]}'"
                )

            cur.execute("""
                UPDATE runs SET status = 'CANCELLED', finished_at = %s
                WHERE id = %s;
            """, (now, run_id))

            cur.execute("""
                UPDATE run_queue SET status = 'CANCELLED', completed_at = %s
                WHERE run_id = %s AND status IN ('QUEUED', 'CLAIMED');
            """, (now, run_id))
        conn.commit()

    return envelope({
        "run_id": run_id,
        "status": "CANCELLED",
    }, t0)


@router.get("/runs/queue")
def get_run_queue():
    """View current run queue status."""
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT rq.run_id, rq.status, rq.priority, rq.created_at, rq.claimed_at,
                       r.run_type, r.name
                FROM run_queue rq
                JOIN runs r ON r.id = rq.run_id
                WHERE rq.status IN ('QUEUED', 'CLAIMED')
                ORDER BY rq.priority DESC, rq.created_at ASC;
            """)
            rows = cur.fetchall()

    queue = []
    for row in rows:
        queue.append({
            "run_id": str(row[0]),
            "queue_status": row[1],
            "priority": row[2],
            "queued_at": row[3].isoformat() if row[3] else None,
            "claimed_at": row[4].isoformat() if row[4] else None,
            "run_type": row[5],
            "name": row[6],
        })

    return envelope({
        "queue": queue,
        "count": len(queue),
    }, t0)


@router.get("/paper/status")
def get_paper_status():
    """Get paper trading status."""
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.id, r.name, r.started_at, r.status,
                       pa.initial_capital, pa.current_capital, pa.peak_capital,
                       pa.total_trades, pa.winning_trades, pa.losing_trades,
                       pa.total_pnl, pa.total_fees, pa.max_drawdown,
                       pa.current_exposure
                FROM runs r
                JOIN paper_accounts pa ON r.id = pa.run_id
                WHERE r.run_type = 'PAPER' AND r.status = 'RUNNING'
                ORDER BY r.started_at DESC
                LIMIT 1;
            """)
            row = cur.fetchone()

    if not row:
        return envelope({"active": False, "message": "No active paper trading session"}, t0)

    open_pos = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM paper_positions
                WHERE run_id = %s AND status = 'OPEN';
            """, (row[0],))
            open_pos = cur.fetchone()[0]

    return envelope({
        "active": True,
        "run_id": str(row[0]),
        "name": row[1],
        "started_at": row[2].isoformat() if row[2] else None,
        "status": row[3],
        "account": {
            "initial_capital": row[4],
            "current_capital": row[5],
            "peak_capital": row[6],
            "total_pnl": row[10],
            "total_fees": row[11],
            "max_drawdown": row[12],
            "current_exposure": row[13],
        },
        "performance": {
            "total_trades": row[7],
            "winning_trades": row[8],
            "losing_trades": row[9],
            "win_rate": row[8] / max(1, row[7]) if row[7] else 0,
        },
        "open_positions": open_pos,
    }, t0)


@router.get("/data/availability")
def get_data_availability():
    """Data availability and freshness across the monitored universe.

    Returns per-timeframe OHLCV stats, stale symbol detection, and
    governance parameters (staleness_multiplier, tf_seconds) so the
    frontend can compute staleness thresholds from the same source.
    """
    t0 = time.time()
    cfg = get_config().Worker
    default_tfs = [tf.strip() for tf in cfg.DEFAULT_TIMEFRAMES.split(",")]

    # Universe counts from Redis
    try:
        known_count = r.scard("universe:known") or 0
        monitored_count = r.scard("universe:monitored") or 0
    except Exception:
        known_count = 0
        monitored_count = 0

    # Per-timeframe OHLCV stats from Postgres
    timeframe_stats = []
    stale_symbols = []
    now_utc = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            for tf in default_tfs:
                cur.execute("""
                    SELECT
                        COUNT(DISTINCT symbol)          AS symbol_count,
                        COUNT(*)                        AS total_rows,
                        MAX(bar_start_ts)               AS latest_bar,
                        MIN(bar_start_ts)               AS earliest_bar,
                        MAX(ingested_at)                AS freshest_ingestion
                    FROM ohlcv
                    WHERE timeframe = %s;
                """, (tf,))
                row = cur.fetchone()
                if not row or row[0] == 0:
                    timeframe_stats.append({
                        "timeframe": tf,
                        "symbol_count": 0,
                        "total_rows": 0,
                        "latest_bar": None,
                        "earliest_bar": None,
                        "freshest_ingestion": None,
                        "lag_seconds": None,
                    })
                    continue

                freshest = row[4]
                lag_s = None
                if freshest:
                    if freshest.tzinfo is None:
                        freshest = freshest.replace(tzinfo=timezone.utc)
                    lag_s = max(0, int((now_utc - freshest).total_seconds()))

                timeframe_stats.append({
                    "timeframe": tf,
                    "symbol_count": row[0],
                    "total_rows": row[1],
                    "latest_bar": row[2].isoformat() if row[2] else None,
                    "earliest_bar": row[3].isoformat() if row[3] else None,
                    "freshest_ingestion": freshest.isoformat() if freshest else None,
                    "lag_seconds": lag_s,
                })

            # Stale symbol detection: per-timeframe thresholds.
            # Each TF has its own staleness cutoff = TF_SECONDS[tf] * STALENESS_MULTIPLIER.
            # A 1d symbol is stale after 216000s, a 1h symbol after 9000s.
            stale_seen = set()
            for tf in default_tfs:
                tf_threshold_s = TF_SECONDS.get(tf, 3600) * STALENESS_MULTIPLIER
                tf_cutoff = now_utc - timedelta(seconds=tf_threshold_s)

                cur.execute("""
                    SELECT symbol, MAX(bar_start_ts) AS latest
                    FROM ohlcv
                    WHERE timeframe = %s
                    GROUP BY symbol
                    HAVING MAX(bar_start_ts) < %s
                    ORDER BY MAX(bar_start_ts) ASC
                    LIMIT 50;
                """, (tf, tf_cutoff))
                for row in cur.fetchall():
                    stale_seen.add(str(row[0]))

            stale_symbols = sorted(stale_seen)

    with_ohlcv = timeframe_stats[0]["symbol_count"] if timeframe_stats else 0
    healthy = len(stale_symbols) == 0 and all(
        ts["symbol_count"] > 0 for ts in timeframe_stats
    )

    return envelope({
        "universe": {
            "known": known_count,
            "monitored": monitored_count,
            "with_ohlcv": with_ohlcv,
        },
        "timeframes": timeframe_stats,
        "stale_symbols": stale_symbols,
        "stale_count": len(stale_symbols),
        "healthy": healthy,
        "governance": {
            "staleness_multiplier": STALENESS_MULTIPLIER,
            "tf_seconds": {tf: TF_SECONDS.get(tf, 60) for tf in default_tfs},
        },
    }, t0)
