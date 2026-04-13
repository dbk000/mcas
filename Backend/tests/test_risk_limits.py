"""Unit tests for governance.risk_limits.

Covers:
    * Redis override takes precedence over env.
    * Env override takes precedence over default.
    * Missing keys return defaults.
    * ``check_daily_loss_breach`` returns correct bool at -$119.99
      (no breach) and -$120.01 (breach) when max_daily_loss_usd=120.
    * ``set_notional`` bounds-checks and auto-scales max_daily_loss.
    * ``set_cap`` and ``set_live_trading`` write through.
    * ``kill_all`` flips live-trading, cancels runs, closes positions.
"""

from __future__ import annotations

import os
import sys
from typing import Any, List, Optional, Tuple

import fakeredis
import pytest

# Allow running from Backend/ or repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
_REPO = os.path.dirname(_BACKEND)
for p in (_REPO, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from Backend.governance import risk_limits as rl  # type: ignore
except ImportError:
    from governance import risk_limits as rl  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Cursor:
    """Minimal cursor with scripted fetchone + rowcount responses."""

    def __init__(self, outer: "MockPgConn") -> None:
        self._outer = outer
        self.rowcount = 0
        self._next_fetch: Optional[Tuple[Any, ...]] = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        self._outer.executed.append((sql, params))
        # If the outer has a scripted response keyed by SQL fragment, use it.
        for frag, response in self._outer.scripted.items():
            if frag in sql:
                self._next_fetch = response.get("fetchone")
                self.rowcount = response.get("rowcount", 0)
                return
        self._next_fetch = None
        self.rowcount = 0

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        return self._next_fetch


class MockPgConn:
    """Tiny hand-rolled psycopg-ish connection."""

    def __init__(self) -> None:
        self.executed: List[Tuple[str, Tuple[Any, ...]]] = []
        self.scripted: dict = {}
        self.autocommit = False
        self.committed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.committed = True


def _clear_env(monkeypatch):
    for k in (
        rl.REDIS_KEY_NOTIONAL,
        rl.REDIS_KEY_MAX_DAILY_LOSS,
        rl.REDIS_KEY_MAX_CONCURRENT,
        rl.REDIS_KEY_LIVE_TRADING,
    ):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# current_state precedence
# ---------------------------------------------------------------------------


def test_defaults_when_neither_redis_nor_env(monkeypatch):
    _clear_env(monkeypatch)
    r = fakeredis.FakeRedis()
    state = rl.current_state(r)
    assert state.notional_per_position_usd == rl.DEFAULT_NOTIONAL_USD
    assert state.max_daily_loss_usd == rl.DEFAULT_MAX_DAILY_LOSS_USD
    assert state.max_concurrent == rl.DEFAULT_MAX_CONCURRENT
    assert state.live_trading_enabled is False


def test_env_overrides_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(rl.REDIS_KEY_NOTIONAL, "50")
    monkeypatch.setenv(rl.REDIS_KEY_MAX_DAILY_LOSS, "150")
    monkeypatch.setenv(rl.REDIS_KEY_MAX_CONCURRENT, "2")
    monkeypatch.setenv(rl.REDIS_KEY_LIVE_TRADING, "true")
    r = fakeredis.FakeRedis()  # empty
    state = rl.current_state(r)
    assert state.notional_per_position_usd == 50.0
    assert state.max_daily_loss_usd == 150.0
    assert state.max_concurrent == 2
    assert state.live_trading_enabled is True


def test_redis_overrides_env(monkeypatch):
    _clear_env(monkeypatch)
    # Env says one thing…
    monkeypatch.setenv(rl.REDIS_KEY_NOTIONAL, "40")
    monkeypatch.setenv(rl.REDIS_KEY_MAX_DAILY_LOSS, "120")
    monkeypatch.setenv(rl.REDIS_KEY_MAX_CONCURRENT, "1")
    monkeypatch.setenv(rl.REDIS_KEY_LIVE_TRADING, "false")
    # …Redis overrides.
    r = fakeredis.FakeRedis()
    r.set(rl.REDIS_KEY_NOTIONAL, "75")
    r.set(rl.REDIS_KEY_MAX_DAILY_LOSS, "225")
    r.set(rl.REDIS_KEY_MAX_CONCURRENT, "3")
    r.set(rl.REDIS_KEY_LIVE_TRADING, "1")
    state = rl.current_state(r)
    assert state.notional_per_position_usd == 75.0
    assert state.max_daily_loss_usd == 225.0
    assert state.max_concurrent == 3
    assert state.live_trading_enabled is True


def test_corrupt_redis_falls_through_to_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(rl.REDIS_KEY_NOTIONAL, "42")
    r = fakeredis.FakeRedis()
    r.set(rl.REDIS_KEY_NOTIONAL, "not-a-number")
    state = rl.current_state(r)
    assert state.notional_per_position_usd == 42.0


# ---------------------------------------------------------------------------
# Daily-loss breach
# ---------------------------------------------------------------------------


def _state_120() -> rl.RiskState:
    return rl.RiskState(
        notional_per_position_usd=40.0,
        max_daily_loss_usd=120.0,
        max_concurrent=1,
        live_trading_enabled=True,
    )


def test_daily_loss_no_breach_just_above_threshold():
    conn = MockPgConn()
    conn.scripted["SELECT COALESCE(SUM(realized_pnl)"] = {
        "fetchone": (-119.99,),
        "rowcount": 1,
    }
    breached, pnl = rl.check_daily_loss_breach(conn, _state_120())
    assert breached is False
    assert pnl == pytest.approx(-119.99)


def test_daily_loss_breach_just_below_threshold():
    conn = MockPgConn()
    conn.scripted["SELECT COALESCE(SUM(realized_pnl)"] = {
        "fetchone": (-120.01,),
        "rowcount": 1,
    }
    breached, pnl = rl.check_daily_loss_breach(conn, _state_120())
    assert breached is True
    assert pnl == pytest.approx(-120.01)


def test_daily_loss_exact_threshold_is_breach():
    conn = MockPgConn()
    conn.scripted["SELECT COALESCE(SUM(realized_pnl)"] = {
        "fetchone": (-120.0,),
        "rowcount": 1,
    }
    breached, _ = rl.check_daily_loss_breach(conn, _state_120())
    assert breached is True  # <= limit is a breach


def test_daily_loss_no_pg_conn_is_zero():
    breached, pnl = rl.check_daily_loss_breach(None, _state_120())
    assert breached is False
    assert pnl == 0.0


# ---------------------------------------------------------------------------
# Setters
# ---------------------------------------------------------------------------


def test_set_notional_autoscale():
    r = fakeredis.FakeRedis()
    new_notional, new_loss = rl.set_notional(r, 80.0)
    assert new_notional == 80.0
    assert new_loss == 240.0
    assert float(r.get(rl.REDIS_KEY_NOTIONAL)) == 80.0
    assert float(r.get(rl.REDIS_KEY_MAX_DAILY_LOSS)) == 240.0


def test_set_notional_bounds_low():
    r = fakeredis.FakeRedis()
    with pytest.raises(ValueError):
        rl.set_notional(r, 0.0)


def test_set_notional_bounds_high():
    r = fakeredis.FakeRedis()
    with pytest.raises(ValueError):
        rl.set_notional(r, 501.0)


def test_set_notional_non_numeric():
    r = fakeredis.FakeRedis()
    with pytest.raises(ValueError):
        rl.set_notional(r, "oops")  # type: ignore[arg-type]


def test_set_cap():
    r = fakeredis.FakeRedis()
    assert rl.set_cap(r, 3) == 3
    assert int(r.get(rl.REDIS_KEY_MAX_CONCURRENT)) == 3


def test_set_cap_bounds():
    r = fakeredis.FakeRedis()
    with pytest.raises(ValueError):
        rl.set_cap(r, 0)


def test_set_live_trading():
    r = fakeredis.FakeRedis()
    assert rl.set_live_trading(r, True) is True
    assert r.get(rl.REDIS_KEY_LIVE_TRADING) == b"1"
    assert rl.set_live_trading(r, False) is False
    assert r.get(rl.REDIS_KEY_LIVE_TRADING) == b"0"


# ---------------------------------------------------------------------------
# kill_all
# ---------------------------------------------------------------------------


def test_kill_all_flips_flag_and_cancels(monkeypatch):
    _clear_env(monkeypatch)
    r = fakeredis.FakeRedis()
    r.set(rl.REDIS_KEY_LIVE_TRADING, "1")
    r.set("regime_state", "NORMAL")

    pg = MockPgConn()
    # Script the two UPDATE statements' rowcounts.
    pg.scripted["UPDATE run_queue"] = {"rowcount": 7}
    pg.scripted["UPDATE paper_positions"] = {"rowcount": 3}

    summary = rl.kill_all(pg, r, reason="unit-test", enforcer=None)

    assert summary["live_trading_enabled"] is False
    assert r.get(rl.REDIS_KEY_LIVE_TRADING) == b"0"
    assert r.get("regime_state") == b"SHOCK"
    # The last executed statement keeps the last rowcount — both writes
    # ran, so just assert both SQL strings appeared.
    sql_joined = "\n".join(sql for sql, _ in pg.executed)
    assert "UPDATE run_queue" in sql_joined
    assert "UPDATE paper_positions" in sql_joined
    assert pg.committed is True
