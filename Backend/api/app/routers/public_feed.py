"""
Public Pipeline Feed — unauthenticated endpoint for landing page.

Serves recent opportunities with sealed trace_json and simulation results.
All operational params governed via get_config().
"""

import json
import time
import logging
from datetime import datetime, timezone

import redis
from fastapi import APIRouter

from governance.bootstrap import get_config
from governance.cache_contract import PIPELINE_ACTIVITY_KEY
from api.app.db_pool import get_conn as _pool_get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public"])

# ---------------------------------------------------------------------------
# Redis (lazy init for activity stream)
# ---------------------------------------------------------------------------
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            get_config().Worker.REDIS_URL, decode_responses=True
        )
    return _redis_client


def _read_activity(n: int = 50) -> list[dict]:
    """Read recent pipeline activity events from Redis ring buffer."""
    try:
        r = _get_redis()
        raw = r.lrange(PIPELINE_ACTIVITY_KEY, 0, n - 1)
        return [json.loads(item) for item in raw]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# In-memory cache (governed TTL)
# ---------------------------------------------------------------------------
_cache = {"data": None, "ts": 0.0}


def _cache_ttl() -> int:
    return get_config().Worker.PUBLIC_FEED_CACHE_TTL_S


def _max_events() -> int:
    return get_config().Worker.PUBLIC_FEED_MAX_EVENTS


def _build_pipeline_block(trace: dict) -> dict | None:
    if not trace:
        return None
    return {
        "v": trace.get("v"),
        "strategy": trace.get("strategy"),
        "eval_ts": trace.get("eval_ts"),
        "gates": trace.get("gates", []),
        "score": trace.get("score", {}),
        "state": trace.get("state", {}),
        "promotion": trace.get("promotion", {}),
        "trace_hash": trace.get("trace_hash"),
        "candle_window": trace.get("candle_window", []),
        "indicator_snapshot": trace.get("indicator_snapshot", {}),
        "indicator_params": trace.get("indicator_params", {}),
        "config_snapshot": trace.get("config_snapshot", {}),
        "cross_sectional_context": trace.get("cross_sectional_context", {}),
        "microstructure": trace.get("microstructure", {}),
    }


def _build_simulation_block(sim_trace: dict | None) -> dict | None:
    if not sim_trace:
        return None
    return {
        "conditions_met": sim_trace.get("conditions_met", {}),
        "indicator_values": sim_trace.get("indicator_values", {}),
        "exit_reason": sim_trace.get("exit_reason"),
        "exit_conditions": sim_trace.get("exit_conditions", {}),
    }


def _query_feed(max_events: int) -> list[dict]:
    rows = []
    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.id, o.setup_id, o.symbol, o.timeframe, o.score, o.status,
                       o.trace_json, o.payload_json, o.created_ts, o.updated_ts,
                       sr.trace_json as sim_trace
                FROM opportunities o
                LEFT JOIN LATERAL (
                    SELECT trace_json FROM simulation_results
                    WHERE opportunity_id = o.id ORDER BY created_at DESC LIMIT 1
                ) sr ON TRUE
                WHERE o.trace_json IS NOT NULL
                  AND o.created_ts > NOW() - INTERVAL '24 hours'
                ORDER BY o.created_ts DESC
                LIMIT %s
            """, (max_events,))
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))
    return rows


def _build_events(rows: list[dict]) -> list[dict]:
    events = []
    for row in rows:
        trace_raw = row.get("trace_json")
        if isinstance(trace_raw, str):
            try:
                trace_raw = json.loads(trace_raw)
            except (json.JSONDecodeError, TypeError):
                trace_raw = None

        sim_raw = row.get("sim_trace")
        if isinstance(sim_raw, str):
            try:
                sim_raw = json.loads(sim_raw)
            except (json.JSONDecodeError, TypeError):
                sim_raw = None

        created_ts = row.get("created_ts")
        updated_ts = row.get("updated_ts")

        events.append({
            "opportunity": {
                "id": row.get("id"),
                "setup_id": row.get("setup_id"),
                "symbol": row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "score": row.get("score"),
                "status": row.get("status"),
                "created_ts": created_ts.isoformat() if hasattr(created_ts, "isoformat") else str(created_ts) if created_ts else None,
                "updated_ts": updated_ts.isoformat() if hasattr(updated_ts, "isoformat") else str(updated_ts) if updated_ts else None,
            },
            "pipeline": _build_pipeline_block(trace_raw),
            "simulation": _build_simulation_block(sim_raw),
        })
    return events


def _refresh_cache() -> dict:
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < _cache_ttl():
        return _cache["data"]

    t0 = time.time()
    try:
        rows = _query_feed(_max_events())
        events = _build_events(rows)
        activity = _read_activity(_max_events())
        has_live = len(activity) > 0 and (time.time() - activity[0].get("ts", 0)) < 30
        result = {
            "data": {
                "events": events,
                "activity": activity,
                "count": len(events),
                "has_live_data": has_live or len(events) > 0,
            },
            "meta": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "v1",
                "compute_ms": round((time.time() - t0) * 1000, 1),
            },
        }
        _cache["data"] = result
        _cache["ts"] = time.time()
        return result
    except Exception:
        logger.exception("public feed query failed")
        if _cache["data"] is not None:
            return _cache["data"]
        return {
            "data": {"events": [], "count": 0, "has_live_data": False},
            "meta": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "v1",
                "compute_ms": round((time.time() - t0) * 1000, 1),
            },
        }


@router.get("/pipeline-feed")
def get_pipeline_feed():
    return _refresh_cache()
