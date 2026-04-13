"""Unit tests for governance.regime.enforcer.

Covers the Phase A scaffold acceptance criteria:
    1. Default state is NORMAL when Redis key is absent.
    2. set_state flips the Redis key.
    3. should_reject returns True for HIGH_BETA + SHOCK, False otherwise.
    4. Unknown symbol (not in beta table) → should_reject returns False.
    5. set_state writes an audit row when pg_conn is provided, skips
       silently when pg_conn is None.

Mocks
-----
- Redis: ``fakeredis.FakeRedis`` (in-memory; same client API surface).
- Postgres: a tiny hand-rolled ``MockPgConn`` that records executed SQL.

Both mocks deliberately keep the surface small (only the methods the
enforcer actually uses) — broader mocks would couple the test to
implementation details we don't care about.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, List, Tuple

import fakeredis
import pytest

# Allow running from the Backend/ dir or from repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
_REPO = os.path.dirname(_BACKEND)
for p in (_REPO, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from Backend.governance.regime.enforcer import (
        Enforcer,
        RegimeState,
        REDIS_KEY,
    )
except ImportError:  # pragma: no cover — inside-Backend fallback
    from governance.regime.enforcer import (  # type: ignore
        Enforcer,
        RegimeState,
        REDIS_KEY,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

#: Tiny synthetic beta table — three symbols across three classes.
_TEST_BETA_TABLE = {
    "meta": {
        "schema_version": "1.0",
        "n_symbols": 3,
    },
    "symbols": {
        "BTCUSDT": {
            "symbol": "BTCUSDT",
            "class_4h": "HIGH_BETA",
            "class_12h": "HIGH_BETA",
            "class_48h": "HIGH_BETA",
            "beta_short_4h": 1.7,
        },
        "ETHUSDT": {
            "symbol": "ETHUSDT",
            "class_4h": "NEUTRAL",
            "class_12h": "NEUTRAL",
            "class_48h": "NEUTRAL",
            "beta_short_4h": 1.05,
        },
        "USDCUSDT": {
            "symbol": "USDCUSDT",
            "class_4h": "DEFENSIVE",
            "class_12h": "DEFENSIVE",
            "class_48h": "DEFENSIVE",
            "beta_short_4h": 0.04,
        },
    },
}


@pytest.fixture()
def beta_table_path():
    """Write the synthetic beta table to a temp file. Removed on teardown."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="beta_test_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_TEST_BETA_TABLE, fh)
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:  # pragma: no cover
            pass


@pytest.fixture()
def redis_client():
    """Fresh fakeredis instance per test."""
    return fakeredis.FakeRedis()


@pytest.fixture()
def enforcer(redis_client, beta_table_path):
    return Enforcer(redis_client=redis_client, beta_table_path=beta_table_path)


# ---------------------------------------------------------------------------
# Mock Postgres connection
# ---------------------------------------------------------------------------

class _MockCursor:
    def __init__(self, calls: List[Tuple[str, tuple]]) -> None:
        self._calls = calls

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._calls.append((sql, tuple(params)))

    def __enter__(self) -> "_MockCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class MockPgConn:
    """Minimal psycopg-shaped connection that records executed SQL."""

    def __init__(self, autocommit: bool = True) -> None:
        self.calls: List[Tuple[str, tuple]] = []
        self.commits: int = 0
        self.autocommit = autocommit

    def cursor(self) -> _MockCursor:
        return _MockCursor(self.calls)

    def commit(self) -> None:
        self.commits += 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class _Signal:
    """Tiny stand-in for a signal object — duck-typed by .symbol."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


def test_default_state_is_normal_when_redis_key_absent(enforcer, redis_client):
    """Test 1: Default state is NORMAL when Redis key absent."""
    assert redis_client.get(REDIS_KEY) is None
    assert enforcer.current_state() is RegimeState.NORMAL


def test_set_state_flips_redis_key(enforcer, redis_client):
    """Test 2: set_state flips the Redis key."""
    assert enforcer.current_state() is RegimeState.NORMAL

    changed = enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="manual test flip",
        source="manual_api",
    )
    assert changed is True

    raw = redis_client.get(REDIS_KEY)
    # fakeredis returns bytes by default — accept either str or bytes.
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    assert raw == "SHOCK"
    assert enforcer.current_state() is RegimeState.SHOCK

    # Idempotent: same state -> no-op.
    changed_again = enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="duplicate call",
        source="manual_api",
    )
    assert changed_again is False

    # Flip back.
    enforcer.set_state(
        state=RegimeState.NORMAL,
        reason="rollback",
        source="manual_api",
    )
    assert enforcer.current_state() is RegimeState.NORMAL


def test_should_reject_returns_true_for_high_beta_and_shock(enforcer):
    """Test 3a: HIGH_BETA + SHOCK ⇒ reject."""
    enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="test",
        source="manual_api",
    )
    reject, reason = enforcer.should_reject(_Signal("BTCUSDT"))
    assert reject is True
    assert "BTCUSDT" in reason
    assert "HIGH_BETA" in reason


def test_should_reject_false_when_normal(enforcer):
    """Test 3b: HIGH_BETA + NORMAL ⇒ accept."""
    # state is NORMAL by default
    reject, reason = enforcer.should_reject(_Signal("BTCUSDT"))
    assert reject is False
    assert reason == ""


def test_should_reject_false_for_neutral_symbol_in_shock(enforcer):
    """Test 3c: NEUTRAL symbol + SHOCK ⇒ accept (only HIGH_BETA gets rejected)."""
    enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="test",
        source="manual_api",
    )
    reject, _ = enforcer.should_reject(_Signal("ETHUSDT"))
    assert reject is False


def test_should_reject_false_for_defensive_symbol_in_shock(enforcer):
    """Test 3d: DEFENSIVE symbol + SHOCK ⇒ accept."""
    enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="test",
        source="manual_api",
    )
    reject, _ = enforcer.should_reject(_Signal("USDCUSDT"))
    assert reject is False


def test_should_reject_false_for_unknown_symbol(enforcer):
    """Test 4: Unknown symbol (not in beta table) ⇒ should_reject False."""
    enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="test",
        source="manual_api",
    )
    reject, reason = enforcer.should_reject(_Signal("FAKEUSDT"))
    assert reject is False
    assert reason == ""


def test_set_state_writes_audit_row_when_pg_conn_provided(enforcer):
    """Test 5a: pg_conn provided ⇒ enforcer_events row written."""
    conn = MockPgConn(autocommit=False)
    changed = enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="composite_trigger: BTC -3.2%, MONETARY 4.1x",
        source="composite_trigger",
        pg_conn=conn,
    )
    assert changed is True
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "INSERT INTO enforcer_events" in sql
    assert params[0] == "NORMAL"  # old_state
    assert params[1] == "SHOCK"   # new_state
    assert params[2] == "composite_trigger: BTC -3.2%, MONETARY 4.1x"
    assert params[3] == "composite_trigger"
    # operator_chat_id and signal_count_affected default to None
    assert params[4] is None
    assert params[5] is None
    # Non-autocommit conn ⇒ enforcer commits.
    assert conn.commits == 1


def test_set_state_skips_audit_silently_when_pg_conn_absent(enforcer):
    """Test 5b: pg_conn absent ⇒ no audit attempt, state still flips."""
    # Should not raise, should not need a connection.
    changed = enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="telegram /regime_shock test",
        source="telegram_override",
        pg_conn=None,
    )
    assert changed is True
    assert enforcer.current_state() is RegimeState.SHOCK


def test_set_state_audit_with_operator_chat_id(enforcer):
    """Telegram override path passes through chat_id correctly."""
    conn = MockPgConn(autocommit=True)
    enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="manual /regime_shock",
        source="telegram_override",
        pg_conn=conn,
        operator_chat_id=123456789,
    )
    _, params = conn.calls[0]
    assert params[3] == "telegram_override"
    assert params[4] == 123456789
    # autocommit=True ⇒ enforcer should not call commit().
    assert conn.commits == 0


def test_set_state_rejects_unknown_source(enforcer):
    with pytest.raises(ValueError, match="source must be one of"):
        enforcer.set_state(
            state=RegimeState.SHOCK,
            reason="test",
            source="bogus_source",
        )


def test_set_state_rejects_empty_reason(enforcer):
    with pytest.raises(ValueError, match="reason is required"):
        enforcer.set_state(
            state=RegimeState.SHOCK,
            reason="",
            source="manual_api",
        )


def test_set_state_rejects_non_enum_state(enforcer):
    with pytest.raises(TypeError):
        enforcer.set_state(
            state="SHOCK",  # string, not enum
            reason="test",
            source="manual_api",
        )


def test_high_beta_set_loaded_from_table(enforcer):
    """Sanity: only BTCUSDT is HIGH_BETA in the synthetic table."""
    assert enforcer.high_beta_symbols == frozenset({"BTCUSDT"})
    assert enforcer.is_high_beta("BTCUSDT") is True
    assert enforcer.is_high_beta("ETHUSDT") is False
    assert enforcer.is_high_beta("FAKEUSDT") is False


def test_regime_state_from_string_unknown_falls_back_normal():
    assert RegimeState.from_string(None) is RegimeState.NORMAL
    assert RegimeState.from_string("") is RegimeState.NORMAL
    assert RegimeState.from_string("NORMAL") is RegimeState.NORMAL
    assert RegimeState.from_string("shock") is RegimeState.SHOCK
    assert RegimeState.from_string(b"SHOCK") is RegimeState.SHOCK
    # Unknown ⇒ NORMAL (logged warning; no exception)
    assert RegimeState.from_string("ELEVATED") is RegimeState.NORMAL
    assert RegimeState.from_string("garbage") is RegimeState.NORMAL


def test_should_reject_handles_dict_signal(enforcer):
    """Dict-shaped signal works (mapping access fallback)."""
    enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="test",
        source="manual_api",
    )
    reject, _ = enforcer.should_reject({"symbol": "BTCUSDT"})
    assert reject is True


def test_should_reject_handles_signal_without_symbol(enforcer):
    """Signal missing symbol returns (False, '') — safe default."""
    enforcer.set_state(
        state=RegimeState.SHOCK,
        reason="test",
        source="manual_api",
    )
    reject, _ = enforcer.should_reject({})
    assert reject is False
    reject, _ = enforcer.should_reject(None)
    assert reject is False


def test_init_rejects_missing_beta_table(redis_client, tmp_path):
    bogus = str(tmp_path / "does_not_exist.json")
    with pytest.raises(FileNotFoundError):
        Enforcer(redis_client=redis_client, beta_table_path=bogus)


def test_init_rejects_missing_redis(beta_table_path):
    with pytest.raises(ValueError, match="redis_client is required"):
        Enforcer(redis_client=None, beta_table_path=beta_table_path)
