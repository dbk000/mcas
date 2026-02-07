"""
Canon §1.5 + §1.11: API Contract Tests
=======================================

Automated checks:
- All API envelope() functions include as_of_ts in meta
- Response envelope is consistent: {data, meta}
- Envelope function produces correct structure
- All three modules (main, trading_api, control_api) have envelope()

Tests are split into:
1. Source analysis (always runs — verifies envelope pattern in source code)
2. Import-based (skips when psycopg/redis not installed — runs in CI)
"""
import os
import re
import time
import pytest
from datetime import datetime, timezone


# =============================================================================
# Paths
# =============================================================================

API_DIR = os.path.join(os.path.dirname(__file__), '..', 'api', 'app')
MAIN_PY = os.path.join(API_DIR, 'main.py')
TRADING_API_PY = os.path.join(API_DIR, 'trading_api.py')
CONTROL_API_PY = os.path.join(API_DIR, 'control_api.py')


# =============================================================================
# Source Analysis — Always runs (no imports needed)
# =============================================================================

class TestEnvelopeSourceContract:
    """Verify envelope() exists and includes as_of_ts by inspecting source."""

    def _read(self, path):
        with open(path) as f:
            return f.read()

    def test_main_has_envelope_function(self):
        src = self._read(MAIN_PY)
        assert 'def envelope(' in src, "main.py missing envelope() function"

    def test_main_envelope_returns_as_of_ts(self):
        src = self._read(MAIN_PY)
        assert 'as_of_ts' in src, "main.py envelope does not reference as_of_ts"

    def test_main_envelope_returns_data_meta(self):
        src = self._read(MAIN_PY)
        # Envelope should return dict with "data" and "meta" keys
        assert '"data"' in src, "main.py envelope does not return 'data' key"
        assert '"meta"' in src, "main.py envelope does not return 'meta' key"

    def test_trading_api_has_envelope_function(self):
        src = self._read(TRADING_API_PY)
        assert 'def envelope(' in src, "trading_api.py missing envelope() function"

    def test_trading_api_envelope_has_as_of_ts(self):
        src = self._read(TRADING_API_PY)
        assert 'as_of_ts' in src, "trading_api.py envelope does not reference as_of_ts"

    def test_control_api_has_envelope_function(self):
        src = self._read(CONTROL_API_PY)
        assert 'def envelope(' in src, "control_api.py missing envelope() function"

    def test_control_api_envelope_has_as_of_ts(self):
        src = self._read(CONTROL_API_PY)
        assert 'as_of_ts' in src, "control_api.py envelope does not reference as_of_ts"

    def test_all_modules_have_canon_comment(self):
        """Envelope functions should reference Canon §1.5."""
        for name, path in [("main", MAIN_PY), ("trading_api", TRADING_API_PY), ("control_api", CONTROL_API_PY)]:
            src = self._read(path)
            assert 'Canon' in src and '1.5' in src, (
                f"{name}.py envelope does not reference Canon §1.5"
            )

    def test_envelope_meta_includes_required_fields(self):
        """All envelope functions must set timestamp, version, compute_ms, as_of_ts."""
        required = ['timestamp', 'version', 'compute_ms', 'as_of_ts']
        for name, path in [("main", MAIN_PY), ("trading_api", TRADING_API_PY), ("control_api", CONTROL_API_PY)]:
            src = self._read(path)
            for field in required:
                assert field in src, f"{name}.py envelope missing '{field}' in meta"


# =============================================================================
# Import-based Tests — Runs in CI where psycopg/redis are available
# =============================================================================

@pytest.fixture
def _require_api_deps():
    """Skip tests if API dependencies aren't available."""
    pytest.importorskip("psycopg", reason="psycopg not installed (CI only)")
    pytest.importorskip("redis", reason="redis not installed (CI only)")


@pytest.mark.usefixtures("_require_api_deps")
class TestMainEnvelopeImport:
    """Canon §1.5A: main.py envelope() runtime verification."""

    def test_envelope_has_data_and_meta(self):
        from main import envelope
        result = envelope({"foo": "bar"}, time.time())
        assert "data" in result, "Envelope missing 'data' key"
        assert "meta" in result, "Envelope missing 'meta' key"

    def test_envelope_meta_has_as_of_ts(self):
        from main import envelope
        result = envelope({"foo": "bar"}, time.time())
        assert "as_of_ts" in result["meta"], "Envelope meta missing 'as_of_ts'"

    def test_envelope_as_of_ts_is_iso_string(self):
        from main import envelope
        result = envelope({"foo": "bar"}, time.time())
        as_of = result["meta"]["as_of_ts"]
        dt = datetime.fromisoformat(as_of)
        assert dt.tzinfo is not None, "as_of_ts should be timezone-aware"

    def test_envelope_custom_as_of_ts(self):
        from main import envelope
        custom_ts = 1700000000.0
        result = envelope({"test": True}, time.time(), as_of_ts=custom_ts)
        expected = datetime.fromtimestamp(custom_ts, tz=timezone.utc).isoformat()
        assert result["meta"]["as_of_ts"] == expected

    def test_envelope_meta_has_compute_ms(self):
        from main import envelope
        t0 = time.time() - 0.05
        result = envelope({}, t0)
        assert "compute_ms" in result["meta"]
        assert result["meta"]["compute_ms"] >= 0

    def test_envelope_meta_has_version(self):
        from main import envelope
        result = envelope({}, time.time())
        assert result["meta"]["version"] == "v1"


@pytest.mark.usefixtures("_require_api_deps")
class TestEnvelopeConsistencyImport:
    """All envelope() functions must produce identical structure (runtime check)."""

    def test_all_envelopes_have_same_meta_keys(self):
        from main import envelope as main_env
        from trading_api import envelope as trading_env
        from control_api import envelope as control_env

        t0 = time.time()
        r1 = main_env({}, t0)
        r2 = trading_env({}, t0)
        r3 = control_env({}, t0)

        assert set(r1.keys()) == set(r2.keys()) == set(r3.keys()), (
            "Envelope top-level keys differ across modules"
        )
        assert set(r1["meta"].keys()) == set(r2["meta"].keys()) == set(r3["meta"].keys()), (
            "Envelope meta keys differ across modules"
        )

    def test_all_envelopes_include_required_meta_fields(self):
        from main import envelope as main_env
        from trading_api import envelope as trading_env
        from control_api import envelope as control_env

        required_meta = {"timestamp", "version", "compute_ms", "as_of_ts"}

        for name, env_fn in [("main", main_env), ("trading", trading_env), ("control", control_env)]:
            result = env_fn({}, time.time())
            missing = required_meta - set(result["meta"].keys())
            assert not missing, f"{name} envelope missing meta fields: {missing}"
