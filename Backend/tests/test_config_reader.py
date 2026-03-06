"""
Tests for config_reader.LiveConfig — polling, caching, time-throttling.

Uses mocks for database access. No real Postgres required.
"""

import json
import time
from unittest.mock import patch, MagicMock

import pytest

from config_reader import LiveConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_conn():
    """Create a mock psycopg connection context manager."""
    conn = MagicMock()
    cur = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


@pytest.fixture
def live_config():
    """LiveConfig with short poll interval for testing."""
    return LiveConfig(database_url="postgresql://test:test@localhost/test", poll_interval_s=0.1)


# ---------------------------------------------------------------------------
# Time throttling
# ---------------------------------------------------------------------------

class TestTimeThrottling:
    def test_has_changed_respects_poll_interval(self, live_config):
        """has_changed() should not query DB more than once per poll_interval_s."""
        call_count = 0

        def mock_connect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            conn = MagicMock()
            cur = MagicMock()
            cur.fetchone.return_value = (0,)
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
            conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            return conn

        with patch("config_reader.psycopg.connect", side_effect=mock_connect):
            # First call: version check + reload (version -1 -> 0 = change detected)
            live_config.has_changed()
            first_count = call_count  # 2: one for version check, one for reload

            # Immediate second call should be throttled (no query)
            live_config.has_changed()
            assert call_count == first_count

            # After poll interval passes, should query again (version unchanged = no reload)
            time.sleep(0.15)
            live_config.has_changed()
            assert call_count == first_count + 1  # only version check, no reload

    def test_has_changed_returns_false_when_throttled(self, live_config):
        """Throttled calls return False regardless of actual DB state."""
        def mock_connect(*args, **kwargs):
            conn = MagicMock()
            cur = MagicMock()
            cur.fetchone.return_value = (99,)  # version changed
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
            conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            return conn

        with patch("config_reader.psycopg.connect", side_effect=mock_connect):
            # First call detects change (version -1 -> 99)
            assert live_config.has_changed() is True
            # Immediate second call is throttled
            assert live_config.has_changed() is False


# ---------------------------------------------------------------------------
# Version change detection
# ---------------------------------------------------------------------------

class TestVersionDetection:
    def _make_mock_connect(self, versions):
        """Returns a connect mock that returns versions in sequence."""
        idx = [0]

        def mock_connect(*args, **kwargs):
            conn = MagicMock()
            cur = MagicMock()
            v = versions[min(idx[0], len(versions) - 1)]
            idx[0] += 1
            # First query is always config_version, subsequent are param fetches
            cur.fetchone.return_value = (v,) if isinstance(v, int) else v
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
            conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            return conn

        return mock_connect

    def test_detects_version_bump(self):
        """has_changed() returns True when config_version changes."""
        lc = LiveConfig(database_url="postgresql://test/test", poll_interval_s=0.0)

        call_idx = [0]
        def mock_connect(*args, **kwargs):
            conn = MagicMock()
            cur = MagicMock()
            # First call: version=1, second call: version=2
            v = 1 if call_idx[0] == 0 else 2
            call_idx[0] += 1
            cur.fetchone.return_value = (v,)
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
            conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            return conn

        with patch("config_reader.psycopg.connect", side_effect=mock_connect):
            # First call: -1 -> 1 = changed
            assert lc.has_changed() is True
            # Second call: 1 -> 2 = changed
            assert lc.has_changed() is True

    def test_no_change_returns_false(self):
        """has_changed() returns False when version is unchanged."""
        lc = LiveConfig(database_url="postgresql://test/test", poll_interval_s=0.0)

        def mock_connect(*args, **kwargs):
            conn = MagicMock()
            cur = MagicMock()
            cur.fetchone.return_value = (5,)  # always version 5
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
            conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            return conn

        with patch("config_reader.psycopg.connect", side_effect=mock_connect):
            # First call: -1 -> 5 = changed (initial load)
            assert lc.has_changed() is True
            # Second call: 5 -> 5 = no change
            assert lc.has_changed() is False


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_db_unreachable_returns_false(self):
        """If DB is down, has_changed() returns False and keeps cached config."""
        lc = LiveConfig(database_url="postgresql://test/test", poll_interval_s=0.0)

        with patch("config_reader.psycopg.connect", side_effect=ConnectionError("DB down")):
            assert lc.has_changed() is False
            # Cached version should remain at -1 (never loaded)
            assert lc._cached_version == -1

    def test_db_failure_after_successful_load(self):
        """After loading config, DB failure keeps cached config."""
        lc = LiveConfig(database_url="postgresql://test/test", poll_interval_s=0.0)

        call_idx = [0]
        def mock_connect(*args, **kwargs):
            if call_idx[0] == 0:
                # First call succeeds
                call_idx[0] += 1
                conn = MagicMock()
                cur = MagicMock()
                cur.fetchone.return_value = (1,)
                conn.__enter__ = MagicMock(return_value=conn)
                conn.__exit__ = MagicMock(return_value=False)
                conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
                conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
                return conn
            else:
                raise ConnectionError("DB went away")

        with patch("config_reader.psycopg.connect", side_effect=mock_connect):
            assert lc.has_changed() is True
            assert lc._cached_version == 1
            # DB now down
            assert lc.has_changed() is False
            assert lc._cached_version == 1  # still cached


# ---------------------------------------------------------------------------
# D1Evaluator.update_config
# ---------------------------------------------------------------------------

class TestD1UpdateConfig:
    def test_flat_param_update(self):
        """update_config applies flat parameters to D1Config."""
        from setups.d1_dormancy_ignition import D1Evaluator, D1Config

        evaluator = D1Evaluator()
        original_stop = D1Config.STOP_PCT

        params = {"exit_config": {"stop_pct": 0.025}}
        changed = evaluator.update_config(params)

        assert "STOP_PCT" in changed
        assert changed["STOP_PCT"]["old"] == original_stop
        assert changed["STOP_PCT"]["new"] == 0.025
        assert D1Config.STOP_PCT == 0.025

        # Restore
        D1Config.STOP_PCT = original_stop

    def test_nested_weights_update(self):
        """update_config applies nested scoring_weights."""
        from setups.d1_dormancy_ignition import D1Evaluator, D1Config

        evaluator = D1Evaluator()
        original_w = D1Config.W_DORMANCY

        params = {"scoring_weights": {"dormancy": 0.50}}
        changed = evaluator.update_config(params)

        assert "W_DORMANCY" in changed
        assert D1Config.W_DORMANCY == 0.50

        # Restore
        D1Config.W_DORMANCY = original_w

    def test_no_change_returns_empty(self):
        """update_config returns empty dict when params match current values."""
        from setups.d1_dormancy_ignition import D1Evaluator, D1Config

        evaluator = D1Evaluator()
        params = {"exit_config": {"stop_pct": D1Config.STOP_PCT}}
        changed = evaluator.update_config(params)

        assert changed == {}

    def test_unknown_params_ignored(self):
        """update_config silently ignores unknown parameter keys."""
        from setups.d1_dormancy_ignition import D1Evaluator

        evaluator = D1Evaluator()
        params = {"nonexistent_param": 42, "exit_config": {"fake_key": 99}}
        changed = evaluator.update_config(params)

        assert changed == {}

    def test_does_not_reset_state(self):
        """update_config changes config but doesn't touch runtime state attributes."""
        from setups.d1_dormancy_ignition import D1Evaluator, D1Config

        evaluator = D1Evaluator()
        original = D1Config.STOP_PCT

        # Simulate some runtime state
        evaluator._some_state = "preserved"

        params = {"exit_config": {"stop_pct": 0.030}}
        evaluator.update_config(params)

        assert evaluator._some_state == "preserved"
        assert D1Config.STOP_PCT == 0.030

        # Restore
        D1Config.STOP_PCT = original
