"""Unit tests for governance.regime.keyword_clusters.

Covers: per-category matching, negative matching, velocity normalisation,
velocity_ratio against handcrafted baseline.
"""

from __future__ import annotations

import sys
import os

# Allow running the file directly (python3 tests/test_keyword_clusters.py)
# without the pytest rootdir discovery path munging.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
_REPO = os.path.dirname(_BACKEND)
for p in (_REPO, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

# Prefer the Backend.governance import path (used by scripts running from
# the repo root). Fall back to governance.regime.keyword_clusters if we're
# executing from inside the Backend/ dir.
try:
    from Backend.governance.regime.keyword_clusters import (
        CLUSTERS,
        match_categories,
        velocity,
        baseline_velocity,
        velocity_ratio,
        hourly_counts,
    )
except ImportError:  # pragma: no cover — inside-Backend fallback
    from governance.regime.keyword_clusters import (  # type: ignore
        CLUSTERS,
        match_categories,
        velocity,
        baseline_velocity,
        velocity_ratio,
        hourly_counts,
    )


# ---------------------------------------------------------------------------
# match_categories — positive per-category
# ---------------------------------------------------------------------------

def test_geopolitical_match_iran_strait():
    cats = match_categories("Iran blockades the Strait of Hormuz")
    assert "GEOPOLITICAL" in cats


def test_monetary_match_fomc_cpi():
    cats = match_categories("FOMC rate decision reacts to hot CPI")
    assert "MONETARY" in cats


def test_regulatory_match_sec_lawsuit():
    cats = match_categories("SEC files lawsuit, subpoena issued")
    assert "REGULATORY" in cats


def test_structural_match_depeg():
    cats = match_categories("USDC depeg triggers liquidation cascade")
    assert "STRUCTURAL" in cats


def test_social_whale_match():
    cats = match_categories("Whale bought 1,000 BTC on the exchange deposit")
    assert "SOCIAL_WHALE" in cats


def test_social_news_match():
    cats = match_categories("CoinDesk breaking headline")
    assert "SOCIAL_NEWS" in cats


# ---------------------------------------------------------------------------
# match_categories — negative
# ---------------------------------------------------------------------------

def test_innocuous_text_matches_nothing():
    cats = match_categories("the cat sat on the mat and took a nap")
    assert cats == []


def test_empty_text_returns_empty():
    assert match_categories("") == []


# ---------------------------------------------------------------------------
# velocity — handcrafted fixture
# ---------------------------------------------------------------------------

def test_velocity_three_matches_six_hours():
    """3 matches in a 6h window -> velocity = 0.5 matches/hour."""
    # Six-hour window = 21_600 seconds. Three matching texts + one miss.
    window = [
        (1_000, "iran missile strike"),          # GEOPOLITICAL
        (2_000, "taiwan tensions"),              # GEOPOLITICAL
        (3_000, "airstrike on port"),            # GEOPOLITICAL
        (4_000, "ordinary market chatter"),      # no match
    ]
    v = velocity("GEOPOLITICAL", window, window_seconds=6 * 3600)
    # 3 / 6 = 0.5 matches per hour
    assert abs(v - 0.5) < 1e-9


def test_velocity_ratio_spike_over_baseline():
    """Recent rate clearly exceeds trailing baseline."""
    now = 10_000_000  # arbitrary epoch second
    one_hour = 3600

    texts: list[tuple[int, str]] = []

    # Baseline window: now-192h..now-24h = 168h. Place two GEO matches far
    # back in that window, plus filler non-matches.
    base_window_end = now - 24 * one_hour
    texts.append((base_window_end - 150 * one_hour, "old ukraine headline"))
    texts.append((base_window_end - 120 * one_hour, "old russia article"))
    for h in range(168):
        texts.append((base_window_end - h * one_hour - 60, "uneventful"))

    # Recent 24h window: many GEO matches (10) -> recent rate ~0.417/h.
    # Baseline rate ~ 2/168 = 0.0119/h. Floored at 0.5 -> ratio = 0.417/0.5
    # = 0.833. To get ratio>2 we need recent_rate > 1.0/h i.e. >24 matches.
    for h in range(25):
        texts.append((now - (h + 1) * 60, "iran missile strike underway"))

    r = velocity_ratio("GEOPOLITICAL", texts, now_ts=now,
                       lookback_hours=24, baseline_hours=168)
    # recent = 25/24 ≈ 1.042; baseline floored at 0.5 -> ratio ≈ 2.08
    assert r > 2.0, f"expected ratio > 2, got {r:.3f}"


def test_baseline_velocity_empty_window_is_zero():
    """No matches anywhere -> baseline rate is 0."""
    now = 10_000_000
    texts = [(now - 50_000, "nothing interesting")]
    assert baseline_velocity("GEOPOLITICAL", texts, now_ts=now) == 0.0


def test_unknown_category_raises():
    try:
        match_categories("anything")  # should not raise
    except KeyError:  # pragma: no cover
        assert False, "match_categories should never raise on unknown"

    import pytest  # late import — keeps module importable w/o pytest
    with pytest.raises(KeyError):
        velocity("NOT_A_REAL_CATEGORY", [], window_seconds=3600)


def test_hourly_counts_bucket_alignment():
    """hourly_counts must bucket to the top of the hour."""
    t0 = 3600 * 1000  # hour 1000
    texts = [
        (t0 + 5, "iran"),               # hour 1000
        (t0 + 59 * 60, "russia"),       # hour 1000
        (t0 + 3600 + 1, "ukraine"),     # hour 1001
    ]
    hc = hourly_counts(texts, start_ts=t0, end_ts=t0 + 2 * 3600)
    assert hc["GEOPOLITICAL"][t0] == 2
    assert hc["GEOPOLITICAL"][t0 + 3600] == 1


if __name__ == "__main__":
    # Minimal runner for direct execution without pytest harness.
    import traceback
    fns = [(k, v) for k, v in list(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(0 if failed == 0 else 1)
