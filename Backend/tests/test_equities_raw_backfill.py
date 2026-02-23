"""Unit tests for equities raw backfill helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from equities_raw_mentions_backfill import (
    _gdelt_query_for_symbol,
    _iter_windows,
    _select_symbols,
)


def test_select_symbols_is_deterministic_with_top_n():
    symbols = _select_symbols(None, top_n=5)
    assert symbols == ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]


def test_iter_windows_covers_full_interval():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 11, tzinfo=timezone.utc)
    windows = _iter_windows(start, end, days=3)

    assert windows[0][0] == start
    assert windows[-1][1] == end
    assert len(windows) == 4


def test_gdelt_query_uses_company_name_for_short_ticker():
    query = _gdelt_query_for_symbol("C", "Citigroup Inc.")
    assert "Citigroup Inc." in query
    assert '"C"' not in query
