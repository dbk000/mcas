"""
Database Connection Pool
========================

Singleton ConnectionPool for MCAS worker services.
Replaces per-request psycopg.connect() calls.

IMPORTANT: This pool is for long-running service processes only (worker,
signal_worker, ingestor, etc.). One-off scripts that use multiprocessing
(e.g. sim_d1_matrix.py, backfill_d1_opportunities.py) should continue
using direct psycopg.connect() since the pool must NOT be shared across
fork boundaries.

Usage:
    from db_pool import get_pool, get_conn, pool_stats

    # Context manager (recommended)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")

    # Or get pool directly
    pool = get_pool()
"""

import os
import logging
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None

DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "30.0"))


def get_pool() -> ConnectionPool:
    """Get or create the singleton connection pool."""
    global _pool
    if _pool is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL not set")
        _pool = ConnectionPool(
            conninfo=database_url,
            min_size=DB_POOL_MIN,
            max_size=DB_POOL_MAX,
            timeout=DB_POOL_TIMEOUT,
            open=True,
        )
        logger.info(
            "Connection pool created",
            extra={"min_size": DB_POOL_MIN, "max_size": DB_POOL_MAX},
        )
    return _pool


@contextmanager
def get_conn():
    """Get a connection from the pool as a context manager."""
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def pool_stats() -> dict:
    """Get pool statistics for health checks."""
    if _pool is None:
        return {"status": "not_initialized"}
    stats = _pool.get_stats()
    return {
        "pool_min": DB_POOL_MIN,
        "pool_max": DB_POOL_MAX,
        "pool_size": stats.get("pool_size", 0),
        "pool_available": stats.get("pool_available", 0),
        "requests_waiting": stats.get("requests_waiting", 0),
        "requests_num": stats.get("requests_num", 0),
    }


def close_pool():
    """Close the pool (for graceful shutdown)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("Connection pool closed")
