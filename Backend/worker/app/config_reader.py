"""
LiveConfig — single module for live-reloading Pipeline config.

Consumers poll meta.config_version (one integer) on a time-throttled
interval. When the version bumps, the full config is reloaded from
meta.node_version. Between bumps, cached config is returned at zero cost.

Usage:
    from config_reader import LiveConfig

    config = LiveConfig(database_url=DATABASE_URL)

    # In hot loop (time-throttled internally):
    if config.has_changed():
        d1_params = config.get_strategy_params("D1")
        evaluator.update_config(d1_params)

    # One-shot read (batch/daily workers):
    d1_params = config.get_strategy_params("D1", force_reload=True)

V2 optimisation: replace polling with Postgres NOTIFY/LISTEN for
sub-100ms propagation latency.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import psycopg

from db_pool import get_conn as _pool_get_conn

logger = logging.getLogger("config_reader")

# Node name → setup_id mapping (must match meta.node entries)
_NODE_NAME_TO_SETUP = {
    "D1 Dormancy-to-Ignition": "D1",
    "U1 Unicorn Ignition": "U1",
    "S9 Short Reversion": "S9",
    "S10 BB Squeeze": "S10",
}

_SETUP_TO_NODE_NAME = {v: k for k, v in _NODE_NAME_TO_SETUP.items()}

# Status priority for deployed-first resolution (matches run_executor.py:188)
_STATUS_ORDER_SQL = """
    CASE nv.status
        WHEN 'deployed' THEN 0
        WHEN 'promising' THEN 1
        WHEN 'tested' THEN 2
        WHEN 'baseline' THEN 3
        ELSE 4
    END
"""


class LiveConfig:
    """Live-reloading config reader backed by meta.config_version polling.

    Thread-safe for single-threaded async loops (signal_worker pattern).
    Not designed for multi-threaded concurrent access.
    """

    def __init__(self, database_url: str, poll_interval_s: float = 10.0):
        self._database_url = database_url
        self._poll_interval_s = poll_interval_s

        # Cache state
        self._cached_version: int = -1
        self._last_check_ts: float = 0.0
        self._strategy_params: dict[str, dict] = {}  # setup_id → params dict
        self._stop_policy: Optional[dict] = None

        # Track whether last poll detected a change
        self._changed = False

    def has_changed(self) -> bool:
        """Check if config has changed since last reload.

        Time-throttled: only queries Postgres once per poll_interval_s.
        Returns True if config_version has bumped since last check.
        """
        now = time.time()
        if now - self._last_check_ts < self._poll_interval_s:
            return False

        self._last_check_ts = now
        try:
            current_version = self._read_config_version()
        except Exception as e:
            # Graceful degradation: if DB unreachable, keep running with cached config
            logger.warning("config_version poll failed (keeping cached config): %s", e)
            return False

        if current_version != self._cached_version:
            if self._cached_version >= 0:
                logger.info(
                    "Config version changed: %d -> %d — reloading",
                    self._cached_version, current_version,
                )
            self._cached_version = current_version
            self._reload_all()
            self._changed = True
            return True

        self._changed = False
        return False

    def get_strategy_params(self, setup_id: str, force_reload: bool = False) -> dict:
        """Get strategy parameters from Pipeline's deployed node_version.

        For continuous workers: call has_changed() first, then this.
        For batch/daily workers: pass force_reload=True to skip cache.

        Returns the nested parameters dict as stored in meta.node_version,
        with sub-objects like gates, exit_config, scoring_weights intact.
        """
        if force_reload:
            self._reload_strategy(setup_id)
        elif setup_id not in self._strategy_params:
            self._reload_strategy(setup_id)
        return self._strategy_params.get(setup_id, {})

    def get_stop_policy(self, force_reload: bool = False) -> dict:
        """Get stop policy config from Pipeline's deployed Stop-Loss Config node.

        Uses same deployed-first resolution as run_executor.py.
        Falls back to empty dict if no deployed version exists.
        """
        if force_reload or self._stop_policy is None:
            self._reload_stop_policy()
        return self._stop_policy or {}

    def _read_config_version(self) -> int:
        """Read the singleton config_version counter. O(1) query."""
        with _pool_get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM meta.config_version WHERE id = 1")
                row = cur.fetchone()
                return row[0] if row else 0

    def _reload_all(self):
        """Reload all cached config from meta.node_version."""
        try:
            with _pool_get_conn() as conn:
                for setup_id, node_name in _SETUP_TO_NODE_NAME.items():
                    params = self._fetch_deployed_params(conn, node_name)
                    if params is not None:
                        self._strategy_params[setup_id] = params

                # Reload stop policy
                self._reload_stop_policy_with_conn(conn)
        except Exception as e:
            logger.error("Config reload failed (keeping cached): %s", e)

    def _reload_strategy(self, setup_id: str):
        """Reload a single strategy's config."""
        node_name = _SETUP_TO_NODE_NAME.get(setup_id)
        if not node_name:
            logger.warning("No node name mapping for setup_id=%s", setup_id)
            return
        try:
            with _pool_get_conn() as conn:
                params = self._fetch_deployed_params(conn, node_name)
                if params is not None:
                    self._strategy_params[setup_id] = params
        except Exception as e:
            logger.error("Failed to reload %s config: %s", setup_id, e)

    def _fetch_deployed_params(self, conn, node_name: str) -> Optional[dict]:
        """Fetch deployed parameters for a node, using status-priority resolution."""
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT nv.parameters, nv.status, nv.version_number
                FROM meta.node n
                JOIN meta.node_version nv ON nv.node_id = n.node_id
                WHERE lower(n.name) = lower(%s)
                ORDER BY {_STATUS_ORDER_SQL}, nv.version_number DESC
                LIMIT 1
            """, (node_name,))
            row = cur.fetchone()
            if not row:
                return None
            raw = row[0]
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, dict):
                return None
            logger.debug(
                "Loaded %s params (status=%s, ver=%s): %d keys",
                node_name, row[1], row[2], len(raw),
            )
            return raw

    def _reload_stop_policy(self):
        """Reload stop policy config."""
        try:
            with _pool_get_conn() as conn:
                self._reload_stop_policy_with_conn(conn)
        except Exception as e:
            logger.error("Failed to reload stop policy: %s", e)

    def _reload_stop_policy_with_conn(self, conn):
        """Reload stop policy using an existing connection."""
        params = self._fetch_deployed_params(conn, "Stop-Loss Config")
        if params is not None:
            self._stop_policy = params
