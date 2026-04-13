"""Unit tests for REFACTOR-2 quote-currency registry helpers (split
into DATA + EXECUTION quotes, 2026-04-13).

Covers:
- ``data_quote_currency()`` returns the data-layer quote (USDT default)
- ``execution_quote_currency()`` returns the execution-layer quote (USDC)
- ``quote_currency()`` deprecated alias still returns data-quote
- ``macro_reference_symbol()`` composes BTC + DATA quote
- ``major_symbols()`` derives MAJOR_BASES × DATA quote
- Unbootstrapped access crashes loudly (sys.exit 78)
- D1Config / U1Config class attrs are sync'd from DATA quote at bootstrap
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


class _FakeWorker:
    def __init__(self, data: str = "USDT", execution: str = "USDC"):
        self.DATA_QUOTE_CURRENCY = data
        self.EXECUTION_QUOTE_CURRENCY = execution


class _FakeRegistry:
    def __init__(self, data: str = "USDT", execution: str = "USDC"):
        self.Worker = _FakeWorker(data=data, execution=execution)


# ── Core helpers ───────────────────────────────────────────────────────

def test_data_quote_currency_reads_from_registry():
    with patch.object(bs, "get_config", return_value=_FakeRegistry(data="USDT")):
        assert bs.data_quote_currency() == "USDT"


def test_execution_quote_currency_reads_from_registry():
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(execution="USDC")):
        assert bs.execution_quote_currency() == "USDC"


def test_data_and_execution_are_independent():
    """Flipping one does not affect the other."""
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDT", execution="USDC")):
        assert bs.data_quote_currency() == "USDT"
        assert bs.execution_quote_currency() == "USDC"
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDC", execution="USDT")):
        assert bs.data_quote_currency() == "USDC"
        assert bs.execution_quote_currency() == "USDT"


def test_quote_currency_legacy_alias_is_data():
    """Legacy ``quote_currency()`` must resolve to the data quote so
    existing callers (tests, scripts) read the historically-correct value."""
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDT", execution="USDC")):
        assert bs.quote_currency() == "USDT"


def test_macro_reference_symbol_uses_data_quote():
    """Macro ref reads OHLCV → must use the data quote, never execution."""
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDT", execution="USDC")):
        assert bs.macro_reference_symbol() == "BTCUSDT"
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDC", execution="USDT")):
        assert bs.macro_reference_symbol() == "BTCUSDC"


def test_filter_suffix_uses_data_quote():
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDT")):
        assert bs.quote_currency_filter_suffix() == "USDT"


def test_major_symbols_uses_data_quote():
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDT", execution="USDC")):
        syms = bs.major_symbols()
    expected = tuple(sorted(f"{b}USDT" for b in bs.MAJOR_BASES))
    assert syms == expected
    # Data-quote-bound — executionquote must NOT appear.
    assert "BTCUSDT" in syms
    assert "BTCUSDC" not in syms


def test_major_symbols_reacts_to_data_quote_flip():
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDT")):
        syms_usdt = bs.major_symbols()
    with patch.object(bs, "get_config",
                      return_value=_FakeRegistry(data="USDC")):
        syms_usdc = bs.major_symbols()
    assert len(syms_usdt) == len(syms_usdc) == len(bs.MAJOR_BASES)
    assert not (set(syms_usdt) & set(syms_usdc))


def test_unbootstrapped_access_crashes_loudly():
    """Accessing the registry before bootstrap should sys.exit(78)."""
    saved = bs._CONFIG
    bs._CONFIG = None
    try:
        with pytest.raises(SystemExit) as ei:
            bs.get_config()
        assert ei.value.code == 78
    finally:
        bs._CONFIG = saved


# ── Core_logic sync ────────────────────────────────────────────────────

def test_d1config_quote_currency_class_attr_exists():
    from core_logic.d1_dormancy_ignition import D1Config
    assert hasattr(D1Config, "QUOTE_CURRENCY")
    assert D1Config.QUOTE_CURRENCY in ("USDC", "USDT")
    # MAJOR_SYMBOLS now includes the quote-swap cross product.
    assert "BTCUSDC" in D1Config.MAJOR_SYMBOLS
    assert "BTCUSDT" in D1Config.MAJOR_SYMBOLS


def test_u1config_quote_currency_class_attr_exists():
    from core_logic.u1_unicorn_ignition import U1Config
    assert hasattr(U1Config, "QUOTE_CURRENCY")
    assert U1Config.QUOTE_CURRENCY in ("USDC", "USDT")
    assert "BTCUSDC" in U1Config.MAJOR_SYMBOLS
    assert "BTCUSDT" in U1Config.MAJOR_SYMBOLS


def test_is_d1_excluded_follows_quote_currency():
    """Flipping D1Config.QUOTE_CURRENCY changes the universe filter."""
    from core_logic.d1_dormancy_ignition import D1Config, is_d1_excluded
    saved = D1Config.QUOTE_CURRENCY
    try:
        D1Config.QUOTE_CURRENCY = "USDC"
        # Non-major coin + correct quote → accepted
        assert is_d1_excluded("AUDIOUSDC") is False
        # Wrong quote → excluded
        assert is_d1_excluded("AUDIOUSDT") is True

        D1Config.QUOTE_CURRENCY = "USDT"
        assert is_d1_excluded("AUDIOUSDT") is False
        assert is_d1_excluded("AUDIOUSDC") is True
    finally:
        D1Config.QUOTE_CURRENCY = saved


def test_d1_majors_excluded_regardless_of_quote():
    """A major is excluded under both USDC and USDT."""
    from core_logic.d1_dormancy_ignition import D1Config, is_d1_excluded
    saved = D1Config.QUOTE_CURRENCY
    try:
        D1Config.QUOTE_CURRENCY = "USDC"
        assert is_d1_excluded("BTCUSDC") is True
        assert is_d1_excluded("ETHUSDC") is True
        D1Config.QUOTE_CURRENCY = "USDT"
        assert is_d1_excluded("BTCUSDT") is True
        assert is_d1_excluded("ETHUSDT") is True
    finally:
        D1Config.QUOTE_CURRENCY = saved
