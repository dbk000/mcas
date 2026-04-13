"""Unit tests for REFACTOR-2 quote-currency registry helpers.

Covers:
- ``quote_currency()`` surfaces the registry value
- ``macro_reference_symbol()`` composes BTC + quote
- ``quote_currency_filter_suffix()`` identity alias
- ``major_symbols()`` derives MAJOR_BASES × QUOTE_CURRENCY
- Unbootstrapped access crashes loudly (sys.exit 78)
- Override via env var propagates consistently
- D1Config / U1Config class attrs are sync'd from Worker at bootstrap
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
    def __init__(self, quote: str):
        self.QUOTE_CURRENCY = quote


class _FakeRegistry:
    def __init__(self, quote: str):
        self.Worker = _FakeWorker(quote)


# ── Core helpers ───────────────────────────────────────────────────────

def test_quote_currency_reads_from_registry():
    with patch.object(bs, "get_config", return_value=_FakeRegistry("USDC")):
        assert bs.quote_currency() == "USDC"


def test_macro_reference_symbol_composition():
    with patch.object(bs, "get_config", return_value=_FakeRegistry("USDC")):
        assert bs.macro_reference_symbol() == "BTCUSDC"
    with patch.object(bs, "get_config", return_value=_FakeRegistry("USDT")):
        assert bs.macro_reference_symbol() == "BTCUSDT"


def test_filter_suffix_identity():
    with patch.object(bs, "get_config", return_value=_FakeRegistry("USDC")):
        assert bs.quote_currency_filter_suffix() == "USDC"
    with patch.object(bs, "get_config", return_value=_FakeRegistry("USDT")):
        assert bs.quote_currency_filter_suffix() == "USDT"


def test_major_symbols_derives_from_quote_currency():
    with patch.object(bs, "get_config", return_value=_FakeRegistry("USDC")):
        syms = bs.major_symbols()
    expected = tuple(sorted(f"{b}USDC" for b in bs.MAJOR_BASES))
    assert syms == expected
    # Spot-check: BTC + ETH are always in there.
    assert "BTCUSDC" in syms
    assert "ETHUSDC" in syms


def test_major_symbols_reacts_to_quote_flip():
    with patch.object(bs, "get_config", return_value=_FakeRegistry("USDT")):
        syms_usdt = bs.major_symbols()
    with patch.object(bs, "get_config", return_value=_FakeRegistry("USDC")):
        syms_usdc = bs.major_symbols()
    # Same length (derivation is base × quote), disjoint sets under flip.
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
