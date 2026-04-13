"""Regime enforcer — minimum-viable for Enforcer-First deployment.

Reads ``regime_state`` from Redis. Values: ``NORMAL`` | ``SHOCK``
(no ``ELEVATED`` in this sprint).

On SHOCK:
    - Reject new signals where ``symbol.class_4h == 'HIGH_BETA'``
      (per the beta table at ``Backend/governance/regime/asset_beta_table_binance_v1.json``).
    - Pending-order cancellation is the execution_worker's responsibility,
      NOT this module's.

On transition back to NORMAL: resume accepting signals (existing positions
exit on their own via strategy-native exit logic).

Two writers for ``regime_state`` (Phase A scope):
    1. Operator Telegram command ``/regime_shock <reason>``
    2. Automated composite trigger (``Backend/workers/regime_detector.py``,
       which calls :meth:`Enforcer.set_state`).

This module exposes:
    - :class:`RegimeState` enum
    - :class:`Enforcer` class with
      :meth:`current_state`, :meth:`should_reject`, :meth:`set_state`
    - ``enforcer_events`` audit row writer (when ``pg_conn`` is provided)

Design notes
------------
- Beta table is loaded at init time; HIGH_BETA symbol set is cached
  in-memory for O(1) lookups on every signal.
- Symbols absent from the beta table default to NOT high-beta — the
  enforcer takes the safe route (do not reject) when classification is
  unknown. Decoupling unknown-symbol handling from the enforcer means a
  later universe filter can be added without changing this module.
- :meth:`set_state` is idempotent: calling it with the current state is
  a no-op (no Redis write, no audit row). This matters because the
  composite-trigger poller fires every 5 min and most polls will be
  no-ops.
- The audit row writer uses ``executemany``-friendly parameterised SQL
  but writes a single row per call (audit events are low-volume).
"""

from __future__ import annotations

import enum
import json
import logging
import os
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)


REDIS_KEY = "regime_state"


class RegimeState(enum.Enum):
    """Regime states recognised by the enforcer.

    NORMAL — accept all signals.
    SHOCK  — reject HIGH_BETA new signals; existing positions ride their
             native exits; execution_worker cancels pending HIGH_BETA orders.
    """

    NORMAL = "NORMAL"
    SHOCK = "SHOCK"

    @classmethod
    def from_string(cls, raw: Optional[str]) -> "RegimeState":
        """Parse a Redis-stored string into RegimeState. Default: NORMAL.

        Unknown values fall back to NORMAL with a warning — fail-safe (the
        wrong default during a SHOCK is "trade", which is bad; the wrong
        default during NORMAL is "don't trade", which is worse for a
        production system that already has filters. NORMAL with a warning
        gives the operator a chance to notice and correct.)
        """
        if raw is None:
            return cls.NORMAL
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        raw = raw.strip().upper()
        if raw == "":
            return cls.NORMAL
        try:
            return cls(raw)
        except ValueError:
            logger.warning(
                "regime_state has unknown value %r — defaulting to NORMAL",
                raw,
            )
            return cls.NORMAL


_ALLOWED_SOURCES = (
    "telegram_override",
    "composite_trigger",
    "manual_api",
    "http_override",
)


class Enforcer:
    """Regime enforcer — Redis-backed state reader + writer.

    Parameters
    ----------
    redis_client
        A redis-py compatible client. Real production: ``redis.Redis``.
        Tests: ``fakeredis.FakeRedis``. Only ``get``, ``set`` are used.
    beta_table_path
        Absolute path to ``asset_beta_table_binance_v1.json``. Read once
        at init time.

    Notes
    -----
    Connection pooling is the caller's responsibility. The enforcer
    accepts an already-constructed Redis client (matches the existing
    governance/persistence pattern).
    """

    def __init__(self, redis_client: Any, beta_table_path: str) -> None:
        if redis_client is None:
            raise ValueError("redis_client is required")
        if not beta_table_path:
            raise ValueError("beta_table_path is required")
        if not os.path.isfile(beta_table_path):
            raise FileNotFoundError(
                f"beta table not found at {beta_table_path}"
            )

        self._redis = redis_client
        self._beta_table_path = beta_table_path
        self._high_beta_symbols: frozenset[str] = self._load_high_beta(
            beta_table_path
        )
        logger.info(
            "Enforcer initialised: %d HIGH_BETA symbols loaded from %s",
            len(self._high_beta_symbols),
            beta_table_path,
        )

    # ------------------------------------------------------------------
    # Beta table
    # ------------------------------------------------------------------
    @staticmethod
    def _load_high_beta(path: str) -> frozenset[str]:
        """Load the HIGH_BETA symbol set from the beta table JSON.

        Uses ``class_4h`` (the short-window classification) per the
        deployment plan. Symbols with ``class_4h == 'HIGH_BETA'`` are
        included; everything else is excluded.
        """
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        symbols = blob.get("symbols", {}) or {}
        out: set[str] = set()
        for sym, meta in symbols.items():
            if not isinstance(meta, dict):
                continue
            if meta.get("class_4h") == "HIGH_BETA":
                out.add(sym)
        return frozenset(out)

    @property
    def high_beta_symbols(self) -> frozenset[str]:
        """Return the cached HIGH_BETA symbol set (frozenset, immutable)."""
        return self._high_beta_symbols

    def is_high_beta(self, symbol: str) -> bool:
        """Return True iff symbol is classified HIGH_BETA at the 4h horizon."""
        if not symbol:
            return False
        return symbol in self._high_beta_symbols

    # ------------------------------------------------------------------
    # State reads
    # ------------------------------------------------------------------
    def current_state(self) -> RegimeState:
        """Read the ``regime_state`` key from Redis.

        Returns NORMAL if the key is absent or unreadable.
        """
        try:
            raw = self._redis.get(REDIS_KEY)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception(
                "regime_state read failed; defaulting to NORMAL: %s", exc
            )
            return RegimeState.NORMAL
        return RegimeState.from_string(raw)

    # ------------------------------------------------------------------
    # Signal evaluation
    # ------------------------------------------------------------------
    def should_reject(self, signal: Any) -> Tuple[bool, str]:
        """Return ``(reject, reason)`` for a given signal.

        Returns ``(True, reason)`` only when:
            - current state is SHOCK, AND
            - signal.symbol is in the HIGH_BETA set.

        Otherwise returns ``(False, "")``.

        ``signal`` is treated duck-typed: it must expose either a
        ``.symbol`` attribute or a ``["symbol"]`` mapping key. Unknown
        symbols (not in the beta table) are NOT rejected — safe default.
        """
        symbol = self._extract_symbol(signal)
        state = self.current_state()
        if state is not RegimeState.SHOCK:
            return False, ""
        if not symbol:
            # No symbol → cannot classify → safe default: do not reject.
            return False, ""
        if symbol in self._high_beta_symbols:
            return (
                True,
                f"regime_state=SHOCK; symbol={symbol} class_4h=HIGH_BETA",
            )
        return False, ""

    @staticmethod
    def _extract_symbol(signal: Any) -> Optional[str]:
        """Pull a symbol string off ``signal`` regardless of shape."""
        if signal is None:
            return None
        # Attribute-style first (matches SetupBase output / dataclasses).
        sym = getattr(signal, "symbol", None)
        if sym:
            return str(sym)
        # Mapping-style fallback.
        try:
            return str(signal["symbol"])
        except (KeyError, TypeError):
            return None

    # ------------------------------------------------------------------
    # State writes
    # ------------------------------------------------------------------
    def set_state(
        self,
        state: RegimeState,
        reason: str,
        source: str,
        pg_conn: Any = None,
        operator_chat_id: Optional[int] = None,
        signal_count_affected: Optional[int] = None,
    ) -> bool:
        """Write the new state to Redis and (optionally) an audit row.

        Parameters
        ----------
        state
            Target state (RegimeState.NORMAL or RegimeState.SHOCK).
        reason
            Human-readable reason (e.g. ``"composite_trigger: BTC -3.2%, MONETARY 4.1x"``).
        source
            One of ``'telegram_override'``, ``'composite_trigger'``,
            ``'manual_api'``. Validated.
        pg_conn
            Optional psycopg connection. If provided, an audit row is
            written to ``enforcer_events``. If absent, the audit step is
            skipped silently.
        operator_chat_id
            Optional operator chat id (only meaningful for
            ``telegram_override``).
        signal_count_affected
            Optional count of signals affected by this transition (filled
            in by callers that do their own tally; the enforcer does not
            compute this).

        Returns
        -------
        bool
            True if the state changed; False if it was a no-op (state
            already matched).
        """
        if not isinstance(state, RegimeState):
            raise TypeError(
                f"state must be RegimeState, got {type(state).__name__}"
            )
        if source not in _ALLOWED_SOURCES:
            raise ValueError(
                f"source must be one of {_ALLOWED_SOURCES}, got {source!r}"
            )
        if not reason:
            raise ValueError("reason is required (non-empty string)")

        old_state = self.current_state()
        if old_state is state:
            logger.debug(
                "regime_state already %s — no-op (source=%s)",
                state.value,
                source,
            )
            return False

        # Redis write (string value — matches the operator-driven
        # `/regime_shock` Telegram command write contract).
        self._redis.set(REDIS_KEY, state.value)
        logger.info(
            "regime_state transitioned %s -> %s (source=%s, reason=%s)",
            old_state.value,
            state.value,
            source,
            reason,
        )

        # Audit row (best-effort — never block the state flip on audit
        # failure; log and continue).
        if pg_conn is not None:
            try:
                self._write_audit_row(
                    pg_conn=pg_conn,
                    old_state=old_state,
                    new_state=state,
                    reason=reason,
                    source=source,
                    operator_chat_id=operator_chat_id,
                    signal_count_affected=signal_count_affected,
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.exception(
                    "enforcer_events audit write failed (state already "
                    "transitioned in Redis): %s",
                    exc,
                )

        return True

    @staticmethod
    def _write_audit_row(
        pg_conn: Any,
        old_state: RegimeState,
        new_state: RegimeState,
        reason: str,
        source: str,
        operator_chat_id: Optional[int],
        signal_count_affected: Optional[int],
    ) -> None:
        """Insert one row into ``enforcer_events`` via the supplied conn.

        Uses parameterised SQL (psycopg-compatible). Caller owns the
        connection lifecycle; we do not commit here unless the
        connection is in autocommit mode.
        """
        sql = (
            "INSERT INTO enforcer_events "
            "(old_state, new_state, reason, source, "
            "operator_chat_id, signal_count_affected) "
            "VALUES (%s, %s, %s, %s, %s, %s)"
        )
        params = (
            old_state.value,
            new_state.value,
            reason,
            source,
            operator_chat_id,
            signal_count_affected,
        )
        with pg_conn.cursor() as cur:
            cur.execute(sql, params)
        # If the caller opened the connection without autocommit, they
        # are responsible for committing. We don't commit unconditionally
        # because that would interfere with batched-transaction callers.
        if getattr(pg_conn, "autocommit", False) is False:
            try:
                pg_conn.commit()
            except Exception:  # pragma: no cover — defensive
                logger.exception("audit row commit failed")
                raise
