"""Contract checks for equities API endpoints."""

from __future__ import annotations

import os

import pytest


API_DIR = os.path.join(os.path.dirname(__file__), "..", "api", "app")
MAIN_PY = os.path.join(API_DIR, "main.py")
EQUITIES_API_PY = os.path.join(API_DIR, "equities_api.py")


class TestEquitiesApiSource:
    def _read(self, path: str) -> str:
        with open(path) as f:
            return f.read()

    def test_main_mounts_equities_router(self):
        src = self._read(MAIN_PY)
        assert "from equities_api import router as equities_router" in src
        assert "app.include_router(equities_router" in src

    def test_equities_api_has_envelope(self):
        src = self._read(EQUITIES_API_PY)
        assert "def envelope(" in src
        assert "as_of_ts" in src

    def test_equities_api_has_required_endpoints(self):
        src = self._read(EQUITIES_API_PY)
        for route in [
            '/opportunities',
            '/opportunities/{opp_id}',
            '/opportunities/{opp_id}/explain',
            '/dashboard/performance',
            '/dashboard/realized',
            '/dashboard/policy',
            '/pipeline/health',
        ]:
            assert route in src, f"Missing route: {route}"


@pytest.mark.usefixtures("_require_api_deps")
class TestEquitiesApiImport:
    @pytest.fixture
    def _require_api_deps(self):
        pytest.importorskip("psycopg", reason="psycopg not installed (CI only)")
        pytest.importorskip("fastapi", reason="fastapi not installed (CI only)")

    def test_router_prefix(self):
        from equities_api import router

        assert router.prefix == "/equities"
        paths = {route.path for route in router.routes}
        assert "/equities/opportunities" in paths
        assert "/equities/opportunities/{opp_id}" in paths
        assert "/equities/opportunities/{opp_id}/explain" in paths
        assert "/equities/dashboard/performance" in paths
        assert "/equities/dashboard/realized" in paths
        assert "/equities/dashboard/policy" in paths
        assert "/equities/pipeline/health" in paths
