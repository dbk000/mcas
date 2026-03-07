"""
Test entry bar exit evaluation (A1 honesty fix).

The exit loop now starts at entry_bar_idx (not entry_bar_idx + 1),
so stop/TP checks run on the entry bar itself with bars_held=0.
The is_entry_bar flag suppresses gap-through detection on the entry bar
because entry happened at bar_open — gap-through is meaningless there.
"""

from exit_checker import check_exit, check_exit_simple, ExitConfig, PositionState


def _bar(open_px, high, low, close):
    return {"open": open_px, "high": high, "low": low, "close": close}


def test_entry_bar_stop_hit():
    """Entry bar low breaches stop -> STOP_LOSS at bars_held=0."""
    result = check_exit(
        side="long",
        bar=_bar(100, 102, 93, 98),  # low=93 < stop=95
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=0),
        config=ExitConfig(),
        is_entry_bar=True,
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 95  # fills at stop_price, not gap-through


def test_entry_bar_tp_hit():
    """Entry bar high reaches TP1 -> TP1 at bars_held=0."""
    result = check_exit(
        side="long",
        bar=_bar(100, 112, 99, 108),  # high=112 >= tp1=110
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=0),
        config=ExitConfig(),
        is_entry_bar=True,
    )
    assert result is not None
    assert result[0] == "TP1"
    assert result[1] == 110


def test_entry_bar_both_hit():
    """Both stop and TP hit on entry bar. STOP_FIRST policy applies.

    NOTE: The OHLC heuristic (up-bar -> assumes high hit before low for longs)
    is a modelling choice, not an objective truth. This test uses STOP_FIRST
    to avoid coupling to heuristic direction.
    """
    result = check_exit(
        side="long",
        bar=_bar(100, 112, 93, 105),  # both stop=95 and tp=110 hit
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=0),
        config=ExitConfig(intrabar_policy="STOP_FIRST"),
        is_entry_bar=True,
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 95  # stop_price, not gap-through


def test_entry_bar_no_gap_through():
    """is_entry_bar suppresses gap-through; fill at stop_price not bar_open.

    On the entry bar, bar_open IS the entry price. Gap-through detection
    (bar_open < stop_price) would be testing whether the entry was below stop,
    which is a pre-entry guard concern, not an exit concern.
    """
    # Construct a bar where open < stop (would trigger gap-through if not entry bar)
    result = check_exit(
        side="long",
        bar=_bar(90, 92, 88, 91),  # open=90 < stop=95 -> gap-through on non-entry bar
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=0),
        config=ExitConfig(),
        is_entry_bar=True,
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    # Key assertion: fill at stop_price, NOT at bar_open (gap-through suppressed)
    assert result[1] == 95

    # Verify non-entry bar WOULD gap-through
    result_normal = check_exit(
        side="long",
        bar=_bar(90, 92, 88, 91),
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=1),
        config=ExitConfig(),
        is_entry_bar=False,
    )
    assert result_normal is not None
    assert result_normal[0] == "STOP_LOSS"
    # Gap-through: fill is capped at max(bar_open, stop - 2R) = max(90, 85) = 90
    assert result_normal[1] == 90


def test_entry_bar_gap_past_stop():
    """Entry gaps past stop (actual_r_value <= 0) -> trade REJECTED by pre-entry guards.

    Three existing pre-entry guards in opportunity_simulator.py prevent this:
    - Line 537: actual_r_value <= 0 -> rejects
    - Line 542: Uneconomic fee guard
    - Line 552: R-preservation guard (actual_r < 50% of original_r)

    This test documents the guarantee at the exit_checker level: even if
    somehow called with is_entry_bar=True and open < stop, it fills at
    stop_price (not the worse bar_open), so the loss is bounded.
    """
    # Long entry at 90, stop at 95 -> R is negative (entry below stop)
    # In practice, opportunity_simulator rejects this at actual_r_value <= 0
    # But at exit_checker level, is_entry_bar suppresses gap-through
    result = check_exit(
        side="long",
        bar=_bar(90, 92, 88, 91),
        entry_price=90,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=0),
        config=ExitConfig(),
        is_entry_bar=True,
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 95  # fills at stop, gap-through suppressed


def test_entry_bar_short_stop():
    """Short entry bar: high breaches stop -> STOP_LOSS, no gap-through."""
    result = check_exit(
        side="short",
        bar=_bar(100, 107, 98, 99),  # high=107 > stop=105
        entry_price=100,
        stop_price=105,
        tp1_price=90,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=0),
        config=ExitConfig(),
        is_entry_bar=True,
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 105  # stop_price, not gap-through


def test_entry_bar_short_no_gap_through():
    """Short entry bar: open > stop would trigger gap-through if not entry bar."""
    result = check_exit(
        side="short",
        bar=_bar(110, 112, 108, 109),  # open=110 > stop=105
        entry_price=100,
        stop_price=105,
        tp1_price=90,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=0),
        config=ExitConfig(),
        is_entry_bar=True,
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 105  # stop_price, gap-through suppressed


def test_check_exit_simple_forwards_is_entry_bar():
    """check_exit_simple passes is_entry_bar through to check_exit."""
    result, hwm, half_r = check_exit_simple(
        side="long",
        bar=_bar(90, 92, 88, 91),  # open=90 < stop=95 -> would gap-through
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        bars_held=0,
        max_bars=50,
        config=ExitConfig(),
        is_entry_bar=True,
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 95  # gap-through suppressed via is_entry_bar
