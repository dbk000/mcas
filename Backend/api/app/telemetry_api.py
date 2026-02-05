"""
Frontend telemetry log ingestion endpoint.

No API key auth — telemetry is fire-and-forget from the browser.
Simple in-memory rate limiting per IP (no Redis required).
"""

import os
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("telemetry_api")

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")

# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per-IP, 10s cooldown)
# ---------------------------------------------------------------------------
_rate_limit: dict[str, float] = {}
_RATE_LIMIT_SECONDS = 10
_RATE_LIMIT_EVICT_AFTER = 300  # evict stale entries after 5 min


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = time.time()

    # Lazy eviction of old entries
    if len(_rate_limit) > 500:
        stale = [ip for ip, ts in _rate_limit.items() if now - ts > _RATE_LIMIT_EVICT_AFTER]
        for ip in stale:
            del _rate_limit[ip]

    last = _rate_limit.get(client_ip, 0)
    if now - last < _RATE_LIMIT_SECONDS:
        return False

    _rate_limit[client_ip] = now
    return True


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class FrontendLogEntry(BaseModel):
    timestamp: str
    level: str
    logger: str
    message: str
    data: dict = {}


class FrontendLogBatch(BaseModel):
    session_id: str
    user_agent: str
    entries: list[FrontendLogEntry]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/telemetry/frontend-logs")
async def ingest_frontend_logs(batch: FrontendLogBatch, request: Request):
    """Receive a batch of frontend log entries and persist them."""
    client_ip = request.client.host if request.client else "unknown"

    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Database not configured")

    if not batch.entries:
        return {"status": "ok", "count": 0}

    now = datetime.now(timezone.utc)
    rows = []
    for entry in batch.entries:
        # Parse client timestamp (best-effort)
        client_ts = None
        try:
            client_ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        except Exception:
            pass

        # Merge top-level entry fields (level, logger, message) with the extra
        # data dict the frontend sends.  The frontend spreads globalContext
        # (including correlation_id) into the log entry at the top level, but
        # the Pydantic model only captures known fields; everything else lands
        # in entry.data.  We also pull correlation_id from data if present.
        merged_data = dict(entry.data) if entry.data else {}
        # Strip fields already stored in dedicated columns
        merged_data.pop("level", None)
        merged_data.pop("logger", None)
        merged_data.pop("message", None)
        merged_data.pop("timestamp", None)

        correlation_id = merged_data.pop("correlation_id", None)
        if correlation_id is None:
            correlation_id = batch.session_id[:16]

        rows.append((
            str(uuid.uuid4()),
            batch.session_id[:32],
            str(correlation_id)[:16],
            entry.level[:10],
            entry.logger[:64],
            entry.message[:2000],
            psycopg.types.json.Json(merged_data) if merged_data else None,
            batch.user_agent[:500] if batch.user_agent else None,
            client_ts,
            now,
        ))

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO frontend_logs
                        (id, session_id, correlation_id, level, category, message, data, user_agent, client_ts, server_ts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, rows)
            conn.commit()
    except Exception:
        logger.debug("Failed to persist frontend logs", exc_info=True)
        # Best-effort — don't fail the request
        return {"status": "ok", "count": 0}

    return {"status": "ok", "count": len(rows)}
