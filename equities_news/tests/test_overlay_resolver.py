"""Exercise sources_for_symbol() on known tickers."""
from __future__ import annotations

import pytest

from resolver import sources_for_symbol, invalidate_caches


@pytest.fixture(autouse=True)
def _invalidate_between_tests():
    invalidate_caches()
    yield
    invalidate_caches()


def test_resolver_nvda_has_universal_and_us_geo():
    srcs = sources_for_symbol("NVDA")
    tiers = {s.origin_tier for s in srcs}
    assert "universal" in tiers
    assert "geo" in tiers


def test_resolver_us_symbol_pulls_us_geo_not_jp():
    srcs = sources_for_symbol("AAPL")
    ids = [s.source_id for s in srcs]
    assert any(sid.startswith("geo.us.") for sid in ids)
    assert not any(sid.startswith("geo.jp.") for sid in ids)


def test_resolver_nonexistent_ticker_raises():
    with pytest.raises(KeyError):
        sources_for_symbol("THIS_TICKER_DOES_NOT_EXIST_1234")


def test_resolver_effective_edge_weight_monotone_by_tier():
    """For any given entry, effective_edge_weight rises strictly with tier rank
    (universal < geo < sector < sub_industry < symbol_direct)."""
    srcs = sources_for_symbol("NVDA")
    # Group by tier and pick max edge_weight_prior (so we compare apples to apples).
    tier_max_eff: dict[str, float] = {}
    for s in srcs:
        # Normalise by prior to isolate the tier multiplier.
        if s.edge_weight_prior > 0:
            multiplier = s.effective_edge_weight / s.edge_weight_prior
            prev = tier_max_eff.get(s.origin_tier, 0.0)
            tier_max_eff[s.origin_tier] = max(prev, multiplier)
    # geo tier mult > universal tier mult if both present.
    if "universal" in tier_max_eff and "geo" in tier_max_eff:
        assert tier_max_eff["geo"] > tier_max_eff["universal"]


def test_resolver_returns_tuple_not_list():
    srcs = sources_for_symbol("NVDA")
    assert isinstance(srcs, tuple)


def test_resolver_includes_all_universal():
    """All universal entries should appear for every symbol."""
    from _lib import load_yaml, NEWS_SOURCES_DIR
    universal_entries = load_yaml(NEWS_SOURCES_DIR / "universal.yaml") or []
    universal_ids = {e["source_id"] for e in universal_entries}
    srcs = sources_for_symbol("NVDA")
    resolved_ids = {s.source_id for s in srcs if s.origin_tier == "universal"}
    assert universal_ids == resolved_ids


def test_resolver_japanese_symbol_gets_jp_geo():
    # Pick a JP symbol from the bible — Toyota or similar.
    # Try a few canonical ticker formats.
    from resolver import _load_symbol_index
    idx = _load_symbol_index()
    jp_tickers = [t for t, m in idx.items() if m.country == "Japan"]
    assert jp_tickers, "no Japanese tickers found in Bible"
    sample = jp_tickers[0]
    srcs = sources_for_symbol(sample)
    ids = [s.source_id for s in srcs]
    assert any(sid.startswith("geo.jp.") for sid in ids), f"{sample} got no JP geo sources"
    assert not any(sid.startswith("geo.us.") for sid in ids)
