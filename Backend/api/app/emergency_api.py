"""
Emergency Control API
=====================

Kill switch, pause/resume, and circuit breaker enforcement for MCAS.

POST /v1/emergency/kill    — Full emergency stop
POST /v1/emergency/pause   — Stop new entries, keep monitoring
POST /v1/emergency/resume  — Resume normal operations
GET  /v1/emergency/status  — Get current emergency state
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg
import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db_pool import get_conn as _pool_get_conn

logger = logging.getLogger("emergency_api")

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

router = APIRouter(prefix="/emergency", tags=["emergency"])


def get_conn():
    """Get database connection from the pool."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    return _pool_get_conn()


def envelope(data, compute_start: float, as_of_ts: float = None) -> dict:
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


# =============================================================================
# Request Models
# =============================================================================

class KillRequest(BaseModel):
    """Request body for the kill switch."""
    reason: str = Field(..., min_length=1, max_length=500, description="Why the kill switch was activated")


class PauseRequest(BaseModel):
    """Request body for pause (reason is optional)."""
    reason: Optional[str] = Field(default=None, max_length=500, description="Why trading was paused")


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/kill")
def emergency_kill(req: KillRequest):
    """Full emergency stop.

    1. Sets kill switch Redis keys
    2. Publishes kill event to emergency_control channel
    3. Cancels all QUEUED/CLAIMED runs in run_queue
    4. Closes all OPEN paper positions with EMERGENCY_KILL exit reason
    """
    t0 = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Set Redis kill switch keys
    r.set("kill_switch:active", "1")
    r.set("kill_switch:activated_at", now_iso)
    r.set("kill_switch:reason", req.reason)

    # 2. Publish to emergency_control channel
    r.publish("emergency_control", json.dumps({
        "action": "kill",
        "reason": req.reason,
        "activated_at": now_iso,
    }))

    cancelled_runs = 0
    closed_positions = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 3. Cancel all QUEUED/CLAIMED runs
            cur.execute("""
                UPDATE run_queue
                SET status = 'FAILED',
                    completed_at = %s,
                    last_error = %s
                WHERE status IN ('QUEUED', 'CLAIMED')
            """, (datetime.now(timezone.utc), f"EMERGENCY_KILL: {req.reason}"))
            cancelled_runs = cur.rowcount

            # 4. Close all OPEN paper positions
            cur.execute("""
                UPDATE paper_positions
                SET status = 'CLOSED',
                    exit_reason = 'EMERGENCY_KILL',
                    exit_ts = %s,
                    exit_price = COALESCE(current_price, entry_price),
                    updated_at = %s
                WHERE status = 'OPEN'
            """, (datetime.now(timezone.utc), datetime.now(timezone.utc)))
            closed_positions = cur.rowcount

        conn.commit()

    logger.warning("KILL SWITCH ACTIVATED", extra={
        "event": "kill_switch_activated",
        "reason": req.reason,
        "cancelled_runs": cancelled_runs,
        "closed_positions": closed_positions,
    })

    return envelope({
        "status": "killed",
        "reason": req.reason,
        "activated_at": now_iso,
        "cancelled_runs": cancelled_runs,
        "closed_positions": closed_positions,
    }, t0)


@router.post("/pause")
def emergency_pause(req: PauseRequest = None):
    """Pause trading — stop new entries, keep monitoring existing positions.

    Sets trading_paused Redis key and notifies workers via pubsub.
    Workers will stop opening new positions but continue monitoring existing ones.
    """
    t0 = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    reason = req.reason if req and req.reason else "Manual pause"

    # Set pause keys
    r.set("trading_paused", "1")
    r.set("trading_paused:at", now_iso)
    r.set("trading_paused:reason", reason)

    # Publish to emergency_control channel
    r.publish("emergency_control", json.dumps({
        "action": "pause",
        "reason": reason,
        "paused_at": now_iso,
    }))

    logger.warning("TRADING PAUSED", extra={
        "event": "trading_paused",
        "reason": reason,
    })

    return envelope({
        "status": "paused",
        "reason": reason,
        "paused_at": now_iso,
    }, t0)


@router.post("/resume")
def emergency_resume():
    """Resume normal operations.

    Clears all kill switch and pause Redis keys, notifies workers.
    """
    t0 = time.time()

    # Capture previous state for logging
    was_killed = r.get("kill_switch:active") == "1"
    was_paused = r.get("trading_paused") == "1"
    prev_reason = r.get("kill_switch:reason") or r.get("trading_paused:reason")

    # Clear all emergency keys
    r.delete(
        "kill_switch:active",
        "kill_switch:activated_at",
        "kill_switch:reason",
        "trading_paused",
        "trading_paused:at",
        "trading_paused:reason",
    )

    # Publish resume event
    r.publish("emergency_control", json.dumps({
        "action": "resume",
        "resumed_at": datetime.now(timezone.utc).isoformat(),
    }))

    logger.info("TRADING RESUMED", extra={
        "event": "trading_resumed",
        "was_killed": was_killed,
        "was_paused": was_paused,
        "previous_reason": prev_reason,
    })

    return envelope({
        "status": "resumed",
        "was_killed": was_killed,
        "was_paused": was_paused,
        "previous_reason": prev_reason,
    }, t0)


@router.get("/status")
def emergency_status():
    """Get current emergency state.

    Returns kill switch status, pause status, open positions count,
    and queued runs count.
    """
    t0 = time.time()

    kill_active = r.get("kill_switch:active") == "1"
    trading_paused = r.get("trading_paused") == "1"
    activated_at = r.get("kill_switch:activated_at")
    kill_reason = r.get("kill_switch:reason")
    paused_at = r.get("trading_paused:at")
    pause_reason = r.get("trading_paused:reason")

    open_positions = 0
    queued_runs = 0

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM paper_positions
                    WHERE status = 'OPEN'
                """)
                open_positions = cur.fetchone()[0]

                cur.execute("""
                    SELECT COUNT(*) FROM run_queue
                    WHERE status IN ('QUEUED', 'CLAIMED')
                """)
                queued_runs = cur.fetchone()[0]
    except Exception as e:
        logger.warning("Failed to query DB for emergency status: %s", e)

    return envelope({
        "kill_switch_active": kill_active,
        "trading_paused": trading_paused,
        "activated_at": activated_at,
        "kill_reason": kill_reason,
        "paused_at": paused_at,
        "pause_reason": pause_reason,
        "open_positions_count": open_positions,
        "queued_runs_count": queued_runs,
    }, t0)
