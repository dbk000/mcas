"""
Live Opportunity Stats API
============================

GET /stats/opportunities        - Rolling stats from v_live_opportunity_stats view
GET /stats/opportunities/detail - Per-opportunity drill-down from v_live_opportunity_pnl view
GET /stats/go-live              - Unified stats across all signal sources (crypto, D2, equities)
GET /stats/go-live/detail       - Per-opportunity drill-down across all sources

Stats endpoints use 60-second in-memory TTL caches to amortize view query cost.
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


def _row_to_dict_dynamic(desc, row: tuple) -> dict:
    """Convert row to dict using cursor.description column names.

    Replaces hardcoded column lists — automatically picks up new view columns.
    """
    d = {}
    for i, col_desc in enumerate(desc):
        val = row[i]
        if isinstance(val, datetime):
            d[col_desc.name] = val.isoformat()
        elif hasattr(val, "as_tuple"):
            d[col_desc.name] = float(val)
        else:
            d[col_desc.name] = val
    return d


def _pipeline_health_status(seconds_since: int | None, threshold_hours: int) -> str:
    """Traffic light: healthy / stale / dead."""
    if seconds_since is None:
        return "dead"
    threshold_s = threshold_hours * 3600
    if seconds_since < threshold_s:
        return "healthy"
    if seconds_since < threshold_s * 2:
        return "stale"
    return "dead"


def _last_signal_info(cur, query: str, threshold_hours: int) -> dict:
    """Execute a MAX(ts) query and return health info dict."""
    cur.execute(query)
    row = cur.fetchone()
    ts = row[0] if row else None
    seconds_since = None
    if ts is not None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        seconds_since = max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))
    return {
        "last_signal_ts": ts.isoformat() if ts else None,
        "seconds_since": seconds_since,
        "status": _pipeline_health_status(seconds_since, threshold_hours),
    }


# -------------------------
# 60-second in-memory TTL caches
# -------------------------

_CACHE_TTL_S = 60
_stats_cache: dict = {"payload": None, "fetched_at": 0.0}
_go_live_cache: dict = {"payload": None, "fetched_at": 0.0}


def _get_cached(cache: dict) -> dict | None:
    if cache["payload"] is None:
        return None
    if time.time() - cache["fetched_at"] < _CACHE_TTL_S:
        return cache["payload"]
    return None


def _set_cached(cache: dict, payload: dict) -> None:
    cache["payload"] = payload
    cache["fetched_at"] = time.time()


# -------------------------
# Router
# -------------------------

router = APIRouter(prefix="/stats", tags=["stats"])


# -------------------------
# GET /stats/opportunities (existing, crypto-only)
# -------------------------

@router.get("/opportunities")
def get_opportunity_stats():
    """Rolling opportunity stats across time windows.

    Queries v_live_opportunity_stats view with 60s in-memory cache.
    Response grouped by window (24h, 7d, 30d, all_time), each containing
    per-setup_id rows with counts, PnL, win rate, profit factor, etc.
    """
    t0 = time.time()

    cached = _get_cached(_stats_cache)
    if cached is not None:
        return envelope(cached, t0)

    windows: dict[str, list[dict]] = {w: [] for w in ("24h", "7d", "30d", "all_time")}

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM v_live_opportunity_stats ORDER BY time_window, setup_id;")
            desc = cur.description
            rows = cur.fetchall()

            for row in rows:
                row_dict = _row_to_dict_dynamic(desc, row)
                window_key = row_dict.pop("time_window")
                if window_key in windows:
                    windows[window_key].append(row_dict)

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

    _set_cached(_stats_cache, data)
    return envelope(data, t0)


# -------------------------
# GET /stats/opportunities/detail (existing, crypto-only)
# -------------------------

@router.get("/opportunities/detail")
def get_opportunity_detail(
    window: str = Query(default="24h", description="Time window: 24h, 7d, or 30d"),
    setup_id: str | None = Query(default=None, description="Optional setup_id filter"),
    direction: str | None = Query(default=None, description="Optional direction: long or short"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Per-opportunity drill-down from v_live_opportunity_pnl."""
    t0 = time.time()

    valid_windows = {"24h", "7d", "30d"}
    if window not in valid_windows:
        raise HTTPException(status_code=400, detail=f"Invalid window. Must be: {', '.join(sorted(valid_windows))}")

    interval_map = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}
    interval = interval_map[window]

    where_clauses = ["created_ts >= NOW() - %s::interval"]
    params: list = [interval]

    if setup_id is not None:
        where_clauses.append("setup_id = %s")
        params.append(setup_id)
    if direction is not None:
        if direction not in ("long", "short"):
            raise HTTPException(status_code=400, detail="direction must be 'long' or 'short'")
        where_clauses.append("direction = %s")
        params.append(direction)

    where_sql = " AND ".join(where_clauses)
    count_sql = f"SELECT COUNT(*) FROM v_live_opportunity_pnl WHERE {where_sql}"
    data_sql = (
        f"SELECT * FROM v_live_opportunity_pnl WHERE {where_sql}"
        f" ORDER BY created_ts DESC LIMIT %s OFFSET %s"
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            cur.execute(data_sql, params + [limit, offset])
            desc = cur.description
            rows = cur.fetchall()

    opportunities = [_row_to_dict_dynamic(desc, row) for row in rows]

    return envelope({
        "opportunities": opportunities,
        "total": total,
        "limit": limit,
        "offset": offset,
        "window": window,
        "filters": {"setup_id": setup_id, "direction": direction},
    }, t0)


# -------------------------
# GET /stats/go-live — Unified stats (all sources)
# -------------------------

@router.get("/go-live")
def get_go_live_stats():
    """Unified opportunity stats across all signal sources.

    Combines crypto, D2, and equities into a single view.
    Windows: 24h, 7d, 30d (no all_time — bounded for performance).
    Includes pipeline health per source and D2 funnel metrics.
    """
    t0 = time.time()

    cached = _get_cached(_go_live_cache)
    if cached is not None:
        return envelope(cached, t0)

    windows: dict[str, list[dict]] = {w: [] for w in ("24h", "7d", "30d")}
    d2_funnel: dict[str, dict] = {}

    with get_conn() as conn:
        with conn.cursor() as cur:
            # --- Unified stats ---
            cur.execute("SELECT * FROM v_unified_opportunity_stats ORDER BY time_window, asset_class, setup_id;")
            desc = cur.description
            for row in cur.fetchall():
                row_dict = _row_to_dict_dynamic(desc, row)
                window_key = row_dict.pop("time_window")
                if window_key in windows:
                    windows[window_key].append(row_dict)

            # --- Pipeline health per source ---
            crypto_health = _last_signal_info(
                cur,
                "SELECT MAX(created_ts) FROM opportunities WHERE batch_id IS NULL AND setup_id NOT LIKE 'EQ%%';",
                2,
            )
            d2_health = _last_signal_info(
                cur,
                "SELECT MAX(entry_ts) FROM paper_positions WHERE setup_id = 'D2';",
                6,
            )
            eq_health = _last_signal_info(
                cur,
                "SELECT MAX(created_at) FROM equities_opportunities;",
                24,
            )

            # --- D2 funnel ---
            cur.execute("SELECT * FROM v_d2_funnel_stats ORDER BY time_window;")
            funnel_desc = cur.description
            for row in cur.fetchall():
                row_dict = _row_to_dict_dynamic(funnel_desc, row)
                window_key = row_dict.pop("time_window")
                d2_funnel[window_key] = row_dict

    compute_ms = round((time.time() - t0) * 1000, 1)
    if compute_ms > 2000:
        logger.warning("go-live query slow: %.0fms", compute_ms)

    data = {
        "supported_windows": ["24h", "7d", "30d"],
        "windows": windows,
        "pipeline_health": {
            "crypto": crypto_health,
            "d2_paper": d2_health,
            "equities": eq_health,
        },
        "d2_funnel": d2_funnel,
    }

    _set_cached(_go_live_cache, data)
    return envelope(data, t0)


# -------------------------
# GET /stats/go-live/detail — Unified drill-down
# -------------------------

@router.get("/go-live/detail")
def get_go_live_detail(
    window: str = Query(default="7d", description="Time window: 24h, 7d, or 30d"),
    setup_id: str | None = Query(default=None, description="Optional setup_id filter"),
    asset_class: str | None = Query(default=None, description="Optional: crypto or equities"),
    source: str | None = Query(default=None, description="Optional: crypto, d2_paper, d2_cluster, equities"),
    direction: str | None = Query(default=None, description="Optional: long or short"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Per-opportunity drill-down from v_unified_opportunity_pnl."""
    t0 = time.time()

    valid_windows = {"24h", "7d", "30d"}
    if window not in valid_windows:
        raise HTTPException(status_code=400, detail=f"Invalid window. Must be: {', '.join(sorted(valid_windows))}")

    interval_map = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}
    interval = interval_map[window]

    where_clauses = ["created_ts >= NOW() - %s::interval"]
    params: list = [interval]

    if setup_id is not None:
        where_clauses.append("setup_id = %s")
        params.append(setup_id)
    if asset_class is not None:
        where_clauses.append("asset_class = %s")
        params.append(asset_class)
    if source is not None:
        where_clauses.append("source = %s")
        params.append(source)
    if direction is not None:
        if direction not in ("long", "short", "neutral"):
            raise HTTPException(status_code=400, detail="direction must be long, short, or neutral")
        where_clauses.append("direction = %s")
        params.append(direction)

    where_sql = " AND ".join(where_clauses)
    count_sql = f"SELECT COUNT(*) FROM v_unified_opportunity_pnl WHERE {where_sql}"
    data_sql = (
        f"SELECT * FROM v_unified_opportunity_pnl WHERE {where_sql}"
        f" ORDER BY created_ts DESC LIMIT %s OFFSET %s"
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            cur.execute(data_sql, params + [limit, offset])
            desc = cur.description
            rows = cur.fetchall()

    opportunities = [_row_to_dict_dynamic(desc, row) for row in rows]

    return envelope({
        "opportunities": opportunities,
        "total": total,
        "limit": limit,
        "offset": offset,
        "window": window,
        "filters": {
            "setup_id": setup_id,
            "asset_class": asset_class,
            "source": source,
            "direction": direction,
        },
    }, t0)
