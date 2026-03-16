"""
Governed Persistence Layer
==========================

Production database connection pool (from db_pool.py) + Redis client.
Rule 4 & 5 Compliance: Centralized I/O.

IMPORTANT: This pool is for long-running service processes only (worker,
signal_worker, ingestor, etc.). One-off scripts that use multiprocessing
(e.g. sim_d1_matrix.py, backfill_d1_opportunities.py) should continue
using direct psycopg.connect() since the pool must NOT be shared across
fork boundaries.

Usage:
    from governance.persistence import get_pool, get_conn, pool_stats

    # Context manager (recommended)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")

    # Or get pool directly
    pool = get_pool()
"""

import logging
import json
import redis
from contextlib import contextmanager
from typing import Optional, Any, Dict, List

from psycopg_pool import ConnectionPool
from governance.bootstrap import get_config, get_lineage
from governance.kernel import LineageEnforcer

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Get or create the singleton connection pool."""
    global _pool
    if _pool is None:
        config = get_config().Worker
        database_url = config.DATABASE_URL
        if not database_url:
            raise RuntimeError("DATABASE_URL not set in WorkerTypedConfig")
        db_pool_min = config.DB_POOL_MIN
        db_pool_max = config.DB_POOL_MAX
        db_pool_timeout = config.DB_POOL_TIMEOUT
        _pool = ConnectionPool(
            conninfo=database_url,
            min_size=db_pool_min,
            max_size=db_pool_max,
            timeout=db_pool_timeout,
            open=True,
        )
        logger.info(
            "Connection pool created",
            extra={"min_size": db_pool_min, "max_size": db_pool_max},
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
    config = get_config().Worker
    stats = _pool.get_stats()
    return {
        "pool_min": config.DB_POOL_MIN,
        "pool_max": config.DB_POOL_MAX,
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


class RedisClient:
    """
    Governed Redis Client.
    Rule 4 & 5 Compliance: Centralized Cache I/O.
    """
    _instance: Optional[redis.Redis] = None

    @classmethod
    def get_client(cls) -> redis.Redis:
        if cls._instance is None:
            config = get_config().Worker
            if not config.REDIS_URL:
                raise RuntimeError("REDIS_URL not configured in WorkerTypedConfig.")
            cls._instance = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
        return cls._instance

    @classmethod
    def set_snapshot(cls, key: str, data: Any, ttl: Optional[int] = None):
        client = cls.get_client()
        client.set(key, json.dumps(data), ex=ttl)

    @classmethod
    def get_snapshot(cls, key: str) -> Optional[Any]:
        client = cls.get_client()
        data = client.get(key)
        if data:
            return json.loads(data)
        return None


class PersistenceClient:
    """
    Governed Database Client (Postgres) — convenience wrapper around pool.
    Rule 4 & 5 Compliance: Centralized SQL I/O.
    """

    @classmethod
    @contextmanager
    def get_connection(cls):
        with get_conn() as conn:
            yield conn

    @classmethod
    def execute_query(cls, sql: str, params: Optional[tuple] = None):
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()

    @classmethod
    def fetch_one(cls, sql: str, params: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    @classmethod
    def fetch_all(cls, sql: str, params: Optional[tuple] = None) -> List[Any]:
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()


# ═══════════════════════════════════════════════════════════════════════════════
# Lineage-Stamped Persistence
# ═══════════════════════════════════════════════════════════════════════════════

class GovernedPersistence(LineageEnforcer):
    """Persistence layer that stamps lineage on every governed write.

    Wraps PersistenceClient. Every INSERT/UPDATE to governed tables
    carries runtime_graph_seal, process_id, and bootstrap_epoch.
    """

    def __init__(self):
        super().__init__(get_lineage())

    def stamp_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Stamp lineage onto a record dict before persistence."""
        return self.stamp_lineage(record)

    def lineage_columns_sql(self) -> str:
        """Return the 3 lineage column names for SQL INSERT statements."""
        return "runtime_graph_seal, process_id, bootstrap_epoch"

    def lineage_values(self) -> tuple:
        """Return the 3 lineage values for parameterized SQL."""
        lin = self._active_lineage
        return (lin.runtime_graph_seal, lin.process_id, lin.bootstrap_epoch)


# Module-level singleton — created on first access after bootstrap
_governed_persistence: GovernedPersistence | None = None


def get_governed_persistence() -> GovernedPersistence:
    """Return the lineage-stamping persistence wrapper."""
    global _governed_persistence
    if _governed_persistence is None:
        _governed_persistence = GovernedPersistence()
    return _governed_persistence
