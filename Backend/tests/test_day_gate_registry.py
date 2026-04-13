"""Unit tests for DAY_GATE_VALIDATION (2026-04-13).

Covers the registry-governed day-gate helpers in governance.bootstrap:
the env-var parser, the per-strategy weekday map, and the active-check
wrapper (via a mocked config registry).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
_REPO = os.path.dirname(_BACKEND)
for p in (_REPO, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from Backend.governance import bootstrap as bs  # type: ignore
except ImportError:
    from governance import bootstrap as bs  # type: ignore


# ── _parse_day_gate_env ────────────────────────────────────────────────

def test_parse_empty_returns_empty():
    assert bs._parse_day_gate_env("") == {}


def test_parse_single_true():
    assert bs._parse_day_gate_env("C2M:true") == {"C2M": True}


def test_parse_mixed_case_and_whitespace():
    result = bs._parse_day_gate_env(" c2m : FALSE , C4M : true ")
    assert result == {"C2M": False, "C4M": True}


def test_parse_accepts_1_0_yes_no():
    result = bs._parse_day_gate_env("A:1,B:0,C:yes,D:no,E:on,F:off")
    assert result == {
        "A": True, "B": False, "C": True,
        "D": False, "E": True, "F": False,
    }


def test_parse_drops_malformed():
    result = bs._parse_day_gate_env("no_colon,:empty_key,C2M:true,C4M:notabool")
    assert result == {"C2M": True}


# ── day_gate_weekday ───────────────────────────────────────────────────

def test_weekday_map_monday_strategies():
    for sid in ("C2M", "C4M", "C4ML", "C3M"):
        assert bs.day_gate_weekday(sid) == 0


def test_weekday_map_saturday_strategies():
    for sid in ("C2S", "C2SH"):
        assert bs.day_gate_weekday(sid) == 5


def test_weekday_map_unknown_returns_none():
    assert bs.day_gate_weekday("NOT_A_STRATEGY") is None
    assert bs.day_gate_weekday("D1") is None


# ── day_gate_active ────────────────────────────────────────────────────

class _FakeWorker:
    def __init__(self, mapping: dict[str, bool]):
        self.DAY_GATE_ENABLED = mapping


class _FakeRegistry:
    def __init__(self, mapping: dict[str, bool]):
        self.Worker = _FakeWorker(mapping)


def test_active_returns_false_when_missing():
    with patch.object(bs, "get_config", return_value=_FakeRegistry({})):
        assert bs.day_gate_active("C2M") is False


def test_active_returns_true_when_set():
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry({"C2M": True})):
        assert bs.day_gate_active("C2M") is True


def test_active_returns_false_when_explicitly_disabled():
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry({"C2M": False})):
        assert bs.day_gate_active("C2M") is False


def test_active_handles_non_dict_config():
    """Registry corruption must not crash — default False (no-gate)."""
    class _Bad:
        class Worker:
            DAY_GATE_ENABLED = None
    with patch.object(bs, "get_config", return_value=_Bad()):
        assert bs.day_gate_active("C2M") is False


# ── C2SH decommission ──────────────────────────────────────────────────

def test_c2sh_removed_from_c_series_variants():
    """C2SH is decommissioned — absent from the mapping prevents future
    accidental re-enable. Importing signal_worker requires psycopg_pool
    which is container-only; skip if unavailable on the host."""
    try:
        from governance.signal_worker import C_SERIES_VARIANTS
    except ModuleNotFoundError as exc:  # pragma: no cover - host-only
        pytest.skip(f"signal_worker import unavailable on host: {exc}")
    assert "C2SH" not in C_SERIES_VARIANTS["C2"]
    # Sanity: C2M and C2S are still present.
    assert "C2M" in C_SERIES_VARIANTS["C2"]
    assert "C2S" in C_SERIES_VARIANTS["C2"]
