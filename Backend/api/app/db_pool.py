"""
Database Connection Pool (API Layer)
=====================================

Thin delegate to governance.persistence for API-local pool management.
Falls back to local singleton pool if governance layer not available.
"""

import logging
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from governance.bootstrap import get_config

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Get or create the singleton connection pool."""
    global _pool
    if _pool is None:
        cfg = get_config().Worker
        database_url = cfg.DATABASE_URL
        if not database_url:
            raise RuntimeError("DATABASE_URL not set")
        pool_min = cfg.DB_POOL_MIN
        pool_max = cfg.DB_POOL_MAX
        pool_timeout = cfg.DB_POOL_TIMEOUT
        _pool = ConnectionPool(
            conninfo=database_url,
            min_size=pool_min,
            max_size=pool_max,
            timeout=pool_timeout,
            open=True,
        )
        logger.info(
            "Connection pool created",
            extra={"min_size": pool_min, "max_size": pool_max},
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
    cfg = get_config().Worker
    stats = _pool.get_stats()
    return {
        "pool_min": cfg.DB_POOL_MIN,
        "pool_max": cfg.DB_POOL_MAX,
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
