"""
Emergency Control API
=====================

Kill switch, pause/resume, and circuit breaker enforcement for MCAS.

POST /emergency/kill    - Full emergency stop
POST /emergency/pause   - Stop new entries, keep monitoring
POST /emergency/resume  - Resume normal operations
GET  /emergency/status  - Get current emergency state

Phase E extensions (operator control surface) — mounted as sibling routers
under ``/v1``:

POST /regime/shock              - Flip regime_state to SHOCK via Enforcer
POST /regime/clear              - Flip regime_state to NORMAL via Enforcer
POST /system/kill_all           - Red button: disable live trading + SHOCK
POST /system/set_cap            - Set BINANCE_MAX_CONCURRENT in Redis
POST /system/set_notional       - Set BINANCE_NOTIONAL_PER_POSITION_USD
                                  (+ auto-scale BINANCE_MAX_DAILY_LOSS_USD)
POST /system/live_trading/enable  - Set live_trading_enabled=1
POST /system/live_trading/disable - Set live_trading_enabled=0
GET  /system/status             - Consolidated operator-facing status
"""

import json
import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg
import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.app.db_pool import get_conn as _pool_get_conn
from governance.bootstrap import get_config
from governance import risk_limits as rl
from governance.regime.enforcer import Enforcer, RegimeState

logger = logging.getLogger("emergency_api")

_worker_cfg = get_config().Worker
r = redis.Redis.from_url(_worker_cfg.REDIS_URL, decode_responses=True)

router = APIRouter(prefix="/emergency", tags=["emergency"])

# Sibling routers (mounted separately at /v1 in api.app.main).
regime_router = APIRouter(prefix="/regime", tags=["regime"])
system_router = APIRouter(prefix="/system", tags=["system"])


# =============================================================================
# Enforcer singleton (lazy) — shared by /regime/* endpoints and /system/kill_all.
# =============================================================================

_enforcer: Optional[Enforcer] = None


def _get_enforcer() -> Optional[Enforcer]:
    """Lazily build the Enforcer. Returns None if the beta table is missing
    (e.g. mis-configured env) rather than raising — the endpoint falls back
    to a raw Redis write so the operator is not locked out of the kill path.
    """
    global _enforcer
    if _enforcer is not None:
        return _enforcer
    beta_path = os.environ.get(
        "ENFORCER_BETA_TABLE",
        "/backend/governance/regime/asset_beta_table_binance_v1.json",
    )
    try:
        _enforcer = Enforcer(redis_client=r, beta_table_path=beta_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning(
            "Enforcer not available (%s); /regime/* endpoints will fall back "
            "to raw Redis writes",
            exc,
        )
        _enforcer = None
    return _enforcer


def get_conn():
    """Get database connection from the pool."""
    if not get_config().Worker.DATABASE_URL:
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
    """Full emergency stop."""
    t0 = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    r.set("kill_switch:active", "1")
    r.set("kill_switch:activated_at", now_iso)
    r.set("kill_switch:reason", req.reason)

    r.publish("emergency_control", json.dumps({
        "action": "kill",
        "reason": req.reason,
        "activated_at": now_iso,
    }))

    cancelled_runs = 0
    closed_positions = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE run_queue
                SET status = 'FAILED',
                    completed_at = %s,
                    last_error = %s
                WHERE status IN ('QUEUED', 'CLAIMED')
            """, (datetime.now(timezone.utc), f"EMERGENCY_KILL: {req.reason}"))
            cancelled_runs = cur.rowcount

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
    """Pause trading -- stop new entries, keep monitoring existing positions."""
    t0 = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    reason = req.reason if req and req.reason else "Manual pause"

    r.set("trading_paused", "1")
    r.set("trading_paused:at", now_iso)
    r.set("trading_paused:reason", reason)

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
    """Resume normal operations."""
    t0 = time.time()

    was_killed = r.get("kill_switch:active") == "1"
    was_paused = r.get("trading_paused") == "1"
    prev_reason = r.get("kill_switch:reason") or r.get("trading_paused:reason")

    r.delete(
        "kill_switch:active",
        "kill_switch:activated_at",
        "kill_switch:reason",
        "trading_paused",
        "trading_paused:at",
        "trading_paused:reason",
    )

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
    """Get current emergency state."""
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


# =============================================================================
# Phase E — Operator Control Surface
# =============================================================================


class ShockRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class ClearRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class CapRequest(BaseModel):
    cap: int = Field(..., ge=1, le=100)


class NotionalRequest(BaseModel):
    notional_usd: float = Field(..., gt=0.0, le=10_000.0)


class KillAllRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


def _regime_state_str() -> str:
    """Read the current regime state as a string (defaults to NORMAL)."""
    raw = r.get("regime_state")
    return RegimeState.from_string(raw).value


@regime_router.post("/shock")
def regime_shock(req: ShockRequest):
    """Manually flip regime_state to SHOCK via the enforcer."""
    t0 = time.time()
    old = _regime_state_str()
    enf = _get_enforcer()
    if enf is not None:
        try:
            with get_conn() as conn:
                enf.set_state(
                    RegimeState.SHOCK,
                    reason=req.reason,
                    source="http_override",
                    pg_conn=conn,
                )
        except Exception as exc:
            # State already transitioned in Redis; audit failure is logged.
            logger.warning("regime shock audit write failed: %s", exc)
            try:
                enf.set_state(
                    RegimeState.SHOCK,
                    reason=req.reason,
                    source="http_override",
                )
            except Exception:
                pass
    else:
        r.set("regime_state", RegimeState.SHOCK.value)
    new = _regime_state_str()
    logger.warning(
        "REGIME SHOCK (http_override): %s -> %s, reason=%s", old, new, req.reason
    )
    return envelope({"old_state": old, "new_state": new, "reason": req.reason}, t0)


@regime_router.post("/clear")
def regime_clear(req: Optional[ClearRequest] = None):
    """Manually clear regime_state back to NORMAL via the enforcer."""
    t0 = time.time()
    reason = (req.reason if req and req.reason else "manual clear") or "manual clear"
    old = _regime_state_str()
    enf = _get_enforcer()
    if enf is not None:
        try:
            with get_conn() as conn:
                enf.set_state(
                    RegimeState.NORMAL,
                    reason=reason,
                    source="http_override",
                    pg_conn=conn,
                )
        except Exception as exc:
            logger.warning("regime clear audit write failed: %s", exc)
            try:
                enf.set_state(
                    RegimeState.NORMAL,
                    reason=reason,
                    source="http_override",
                )
            except Exception:
                pass
    else:
        r.set("regime_state", RegimeState.NORMAL.value)
    new = _regime_state_str()
    logger.info("REGIME CLEAR (http_override): %s -> %s", old, new)
    return envelope({"old_state": old, "new_state": new, "reason": reason}, t0)


@system_router.post("/kill_all")
def system_kill_all(req: Optional[KillAllRequest] = None):
    """Red button: disable live trading, cancel queued runs, set SHOCK."""
    t0 = time.time()
    reason = (req.reason if req and req.reason else "http_kill_all") or "http_kill_all"
    enf = _get_enforcer()
    summary: dict
    with get_conn() as conn:
        summary = rl.kill_all(conn, r, reason=reason, enforcer=enf)
    logger.warning("SYSTEM KILL_ALL: %s", summary)
    return envelope(summary, t0)


@system_router.post("/set_cap")
def system_set_cap(req: CapRequest):
    """Set BINANCE_MAX_CONCURRENT (applied live — no worker restart needed)."""
    t0 = time.time()
    old = rl.current_state(r).max_concurrent
    try:
        new = rl.set_cap(r, req.cap)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return envelope({"old": old, "new": new}, t0)


@system_set_cap  # type: ignore[misc]
def _noop() -> None:  # pragma: no cover — decorator anchor (unused)
    return None


@system_router.post("/set_notional")
def system_set_notional(req: NotionalRequest):
    """Set notional per position + auto-scale max_daily_loss = 3x notional."""
    t0 = time.time()
    prev = rl.current_state(r)
    try:
        new_notional, new_loss = rl.set_notional(r, req.notional_usd)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return envelope(
        {
            "old_notional_usd": prev.notional_per_position_usd,
            "new_notional_usd": new_notional,
            "old_max_daily_loss_usd": prev.max_daily_loss_usd,
            "new_max_daily_loss_usd": new_loss,
        },
        t0,
    )


@system_router.post("/live_trading/enable")
def system_live_trading_enable():
    """Enable live trading (set live_trading_enabled=1 in Redis)."""
    t0 = time.time()
    prev = rl.current_state(r).live_trading_enabled
    new = rl.set_live_trading(r, True)
    logger.warning("LIVE TRADING ENABLED: %s -> %s", prev, new)
    return envelope({"old": prev, "new": new}, t0)


@system_router.post("/live_trading/disable")
def system_live_trading_disable():
    """Disable live trading (set live_trading_enabled=0 in Redis)."""
    t0 = time.time()
    prev = rl.current_state(r).live_trading_enabled
    new = rl.set_live_trading(r, False)
    logger.warning("LIVE TRADING DISABLED: %s -> %s", prev, new)
    return envelope({"old": prev, "new": new}, t0)


@system_router.get("/status")
def system_status():
    """Operator-facing consolidated status."""
    t0 = time.time()
    state = rl.current_state(r)
    regime = _regime_state_str()
    open_positions_count = 0
    today_pnl_val = 0.0
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM live_positions WHERE closed_ts IS NULL"
                )
                row = cur.fetchone()
                open_positions_count = int(row[0]) if row else 0
            today_pnl_val = rl.today_pnl(conn)
    except Exception as exc:
        logger.warning("system_status DB read failed: %s", exc)
    last_hb = r.get("execution_worker_heartbeat_ts")
    return envelope(
        {
            "regime_state": regime,
            "max_concurrent": state.max_concurrent,
            "notional_per_position_usd": state.notional_per_position_usd,
            "max_daily_loss_usd": state.max_daily_loss_usd,
            "live_trading_enabled": state.live_trading_enabled,
            "open_positions_count": open_positions_count,
            "today_pnl_usd": today_pnl_val,
            "last_heartbeat_ts": last_hb,
        },
        t0,
    )
