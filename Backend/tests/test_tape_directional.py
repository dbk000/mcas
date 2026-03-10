"""
Unit tests for analyze_tape_directional() — direction-aware tape analysis.

Tests LONG classification logic with synthetic klines, then confirms SHORT
parity with the existing analyze_tape() output.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))

from s9_microstructure import (
    analyze_tape_directional,
    analyze_tape,
    Kline1s,
    DirectionalMicroContext,
)


def _make_klines(prices, base_ts_ms=1700000000000, volume=1.0):
    """Build synthetic 1s klines from a list of prices.

    Each price becomes O=H=L=C (flat bar) unless tuples are given.
    """
    klines = []
    for i, p in enumerate(prices):
        if isinstance(p, (list, tuple)):
            o, h, l, c = p
        else:
            o = h = l = c = p
        klines.append(Kline1s(
            timestamp_ms=base_ts_ms + i * 1000,
            open=o, high=h, low=l, close=c,
            volume=volume,
        ))
    return klines


# ── LONG tests ──────────────────────────────────────────────────────

class TestLongSteadyDrop:
    """Price drops steadily below close → LIMIT (still falling)."""

    def test_still_falling_gives_limit(self):
        close = 100.0
        # 60 bars dropping steadily, then continue dropping in last 30
        prices = [100.0 - i * 0.05 for i in range(60)]
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "long")

        assert ctx.entry_type == "LIMIT"
        assert ctx.direction == "long"
        assert ctx.still_trending_favorable is True
        assert ctx.suggested_entry_price < close


class TestLongDropThenBounce:
    """Price drops then bounces partway back (still below close) → MARKET."""

    def test_fading_momentum_gives_market(self):
        close = 100.0
        # Drop for 20 bars (100→98.1), then bounce for 40 bars (98.1→99.3)
        # Last 30 bars are all bounce — no new low in that window
        prices = (
            [100.0 - i * 0.1 for i in range(20)]    # drop to 98.1
            + [98.1 + i * 0.03 for i in range(40)]   # bounce to ~99.27
        )
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "long")

        assert ctx.entry_type == "MARKET"
        assert ctx.current_price < close
        assert ctx.direction == "long"


class TestLongDropThenFullRecovery:
    """Price drops then fully recovers above close → SKIP."""

    def test_recovery_above_close_gives_skip(self):
        close = 100.0
        # Drop for 20 bars, then recover above close
        prices = (
            [100.0 - i * 0.3 for i in range(20)]    # drop to ~94.3
            + [94.3 + i * 0.3 for i in range(40)]   # recover to ~106.3
        )
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "long")

        assert ctx.entry_type == "SKIP"
        assert ctx.already_moved_adverse is True


class TestLongSideways:
    """No meaningful move (sideways near close) → MARKET at ~close."""

    def test_sideways_gives_market(self):
        close = 100.0
        # Flat near close for 60 bars
        prices = [100.0 + (i % 3 - 1) * 0.01 for i in range(60)]
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "long")

        assert ctx.entry_type == "MARKET"
        assert abs(ctx.suggested_entry_price - close) < 0.5


# ── SHORT tests (parity with existing analyze_tape) ─────────────────

class TestShortStillClimbing:
    """Price still making new highs → LIMIT."""

    def test_climbing_gives_limit(self):
        close = 100.0
        prices = [100.0 + i * 0.05 for i in range(60)]
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "short")

        assert ctx.entry_type == "LIMIT"
        assert ctx.still_trending_favorable is True


class TestShortAboveCloseFading:
    """Above close but momentum fading → MARKET."""

    def test_fading_gives_market(self):
        close = 100.0
        # Spike up then flatten above close
        prices = (
            [100.0 + i * 0.2 for i in range(20)]    # rise to ~103.8
            + [103.8 - i * 0.02 for i in range(40)]  # slow fade to ~103.0
        )
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "short")

        assert ctx.entry_type == "MARKET"
        assert ctx.current_price > close


class TestShortAlreadyReverted:
    """Price climbed then crashed below close → SKIP."""

    def test_reverted_gives_skip(self):
        close = 100.0
        prices = (
            [100.0 + i * 0.3 for i in range(20)]    # rise to ~105.7
            + [105.7 - i * 0.4 for i in range(40)]   # crash to ~89.7
        )
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "short")

        assert ctx.entry_type == "SKIP"
        assert ctx.already_moved_adverse is True


class TestShortSideways:
    """Sideways near close → MARKET."""

    def test_sideways_gives_market(self):
        close = 100.0
        prices = [100.0 + (i % 3 - 1) * 0.01 for i in range(60)]
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "short")

        assert ctx.entry_type == "MARKET"


# ── Edge cases ──────────────────────────────────────────────────────

class TestInsufficientData:
    """< 10 klines → SKIP with empty context."""

    def test_too_few_klines(self):
        close = 100.0
        klines = _make_klines([100.0] * 5)

        ctx = analyze_tape_directional(close, klines, "long")
        assert ctx.entry_type == "SKIP"
        assert ctx.n_klines == 5

    def test_empty_klines(self):
        ctx = analyze_tape_directional(100.0, [], "short")
        assert ctx.entry_type == "SKIP"
        assert ctx.n_klines == 0


class TestDirectionalMicroContextFields:
    """Verify all direction-agnostic fields are populated correctly."""

    def test_long_best_entry_is_min(self):
        close = 100.0
        prices = [100.0 - i * 0.1 for i in range(60)]
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "long")
        assert ctx.best_entry_price <= close
        assert ctx.best_entry_price == min(prices)

    def test_short_best_entry_is_max(self):
        close = 100.0
        prices = [100.0 + i * 0.1 for i in range(60)]
        klines = _make_klines(prices)

        ctx = analyze_tape_directional(close, klines, "short")
        assert ctx.best_entry_price >= close
        assert ctx.best_entry_price == max(prices)

    def test_to_dict_works(self):
        close = 100.0
        klines = _make_klines([100.0] * 30)
        ctx = analyze_tape_directional(close, klines, "long")
        d = ctx.to_dict()
        assert isinstance(d, dict)
        assert d["direction"] == "long"
        assert "suggested_entry_price" in d
