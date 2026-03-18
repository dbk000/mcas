"""
Live Opportunity Stats API
============================

GET /stats/opportunities        - Rolling stats from v_live_opportunity_stats view
GET /stats/opportunities/detail - Per-opportunity drill-down from v_live_opportunity_pnl view

Stats endpoint uses a 60-second in-memory TTL cache to amortize the cost of
querying the materialized view. The cache stores the full response payload.

All setup_ids are derived from view results, not hardcoded.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, HTTPException

from api.app.db_pool import get_conn as _pool_get_conn

logger = logging.getLogger(__name__)

# -------------------------
# Helpers
# -------------------------

_VALID_WINDOWS = {"24h", "7d", "30d", "all_time"}


def get_conn():
    """Get database connection from the pool."""
    return _pool_get_conn()


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


# -------------------------
# 60-second in-memory TTL cache
# -------------------------

_CACHE_TTL_S = 60
_stats_cache: dict = {"payload": None, "fetched_at": 0.0}


def _get_cached_stats() -> dict | None:
    """Return cached stats if fresh, else None."""
    if _stats_cache["payload"] is None:
        return None
    age = time.time() - _stats_cache["fetched_at"]
    if age < _CACHE_TTL_S:
        return _stats_cache["payload"]
    return None


def _set_cached_stats(payload: dict) -> None:
    """Store stats in the cache."""
    _stats_cache["payload"] = payload
    _stats_cache["fetched_at"] = time.time()


# -------------------------
# Router
# -------------------------

router = APIRouter(prefix="/stats", tags=["stats"])


# -------------------------
# Column name mappings (from view)
# -------------------------

_STATS_COLUMNS = [
    "time_window", "setup_id",
    "opp_count", "closed_count", "open_count",
    "avg_pnl_pct", "total_pnl_pct", "max_pnl_pct", "min_pnl_pct",
    "win_rate", "profit_factor",
    "avg_hours_held", "avg_score",
    "long_count", "short_count",
    "stop_loss_count", "time_stop_count", "tp_count", "fail_fast_count",
]

_PNL_COLUMNS = [
    "id", "asset_class", "setup_id", "symbol", "timeframe",
    "created_ts", "status", "score", "direction",
    "entry_price", "exit_price", "exit_reason", "exit_ts",
    "pnl_pct_net", "is_closed",
]


def _row_to_dict(columns: list[str], row: tuple) -> dict:
    """Convert a tuple row to a dict using column names."""
    d = {}
    for i, col in enumerate(columns):
        val = row[i]
        # Serialize datetimes to ISO-8601
        if isinstance(val, datetime):
            d[col] = val.isoformat()
        # Coerce Decimal/numeric to float for JSON
        elif hasattr(val, "as_tuple"):
            d[col] = float(val)
        else:
            d[col] = val
    return d


# -------------------------
# GET /stats/opportunities
# -------------------------

@router.get("/opportunities")
def get_opportunity_stats():
    """Rolling opportunity stats across time windows.

    Queries v_live_opportunity_stats view with 60s in-memory cache.
    Response grouped by window (24h, 7d, 30d, all_time), each containing
    per-setup_id rows with counts, PnL, win rate, profit factor, etc.
    Also returns pipeline_status with last signal timestamp.
    """
    t0 = time.time()

    # Check cache first
    cached = _get_cached_stats()
    if cached is not None:
        # Re-wrap with fresh compute_ms (near-zero for cache hit)
        return envelope(cached, t0)

    # --- Query the stats view ---
    windows: dict[str, list[dict]] = {w: [] for w in ("24h", "7d", "30d", "all_time")}

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM v_live_opportunity_stats ORDER BY time_window, setup_id;")
            rows = cur.fetchall()

            for row in rows:
                row_dict = _row_to_dict(_STATS_COLUMNS, row)
                window_key = row_dict.pop("time_window")
                if window_key in windows:
                    windows[window_key].append(row_dict)

            # --- Pipeline status: last live signal timestamp ---
            cur.execute(
                "SELECT MAX(created_ts) FROM opportunities WHERE batch_id IS NULL;"
            )
            last_signal_row = cur.fetchone()

    last_signal_ts = last_signal_row[0] if last_signal_row else None
    seconds_since = None
    if last_signal_ts is not None:
        if last_signal_ts.tzinfo is None:
            last_signal_ts = last_signal_ts.replace(tzinfo=timezone.utc)
        seconds_since = max(0, int((datetime.now(timezone.utc) - last_signal_ts).total_seconds()))

    pipeline_status = {
        "last_signal_ts": last_signal_ts.isoformat() if last_signal_ts else None,
        "seconds_since_last_signal": seconds_since,
    }

    data = {
        "windows": windows,
        "pipeline_status": pipeline_status,
    }

    # Store in cache
    _set_cached_stats(data)

    return envelope(data, t0)


# -------------------------
# GET /stats/opportunities/detail
# -------------------------

@router.get("/opportunities/detail")
def get_opportunity_detail(
    window: str = Query(
        default="24h",
        description="Time window filter: 24h, 7d, or 30d",
    ),
    setup_id: str | None = Query(
        default=None,
        description="Optional setup_id filter (e.g. S9, S10, D1)",
    ),
    direction: str | None = Query(
        default=None,
        description="Optional direction filter: long or short",
    ),
    limit: int = Query(default=100, ge=1, le=1000, description="Max rows to return"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
):
    """Per-opportunity drill-down from v_live_opportunity_pnl.

    Returns individual opportunity rows with PnL for the given time window,
    paginated via limit/offset. Useful for UI drill-down from the stats summary.
    """
    t0 = time.time()

    # Validate window
    valid_detail_windows = {"24h", "7d", "30d"}
    if window not in valid_detail_windows:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid window '{window}'. Must be one of: {', '.join(sorted(valid_detail_windows))}",
        )

    # Map window to interval
    interval_map = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}
    interval = interval_map[window]

    # Build query with filters
    where_clauses = ["created_ts >= NOW() - %s::interval"]
    params: list = [interval]

    if setup_id is not None:
        where_clauses.append("setup_id = %s")
        params.append(setup_id)

    if direction is not None:
        if direction not in ("long", "short"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid direction '{direction}'. Must be 'long' or 'short'.",
            )
        where_clauses.append("direction = %s")
        params.append(direction)

    where_sql = " AND ".join(where_clauses)

    # Count query for total (before pagination)
    count_sql = f"SELECT COUNT(*) FROM v_live_opportunity_pnl WHERE {where_sql}"

    # Data query with pagination
    data_sql = (
        f"SELECT * FROM v_live_opportunity_pnl"
        f" WHERE {where_sql}"
        f" ORDER BY created_ts DESC"
        f" LIMIT %s OFFSET %s"
    )
    params_data = params + [limit, offset]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            cur.execute(data_sql, params_data)
            rows = cur.fetchall()

    opportunities = [_row_to_dict(_PNL_COLUMNS, row) for row in rows]

    return envelope({
        "opportunities": opportunities,
        "total": total,
        "limit": limit,
        "offset": offset,
        "window": window,
        "filters": {
            "setup_id": setup_id,
            "direction": direction,
        },
    }, t0)
