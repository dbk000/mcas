"""
Test honest gap-through fills (A2 Step 2).

After removing the fabricated 2R cap:
- Stop gap-throughs fill at bar_open (no cap)
- TP windfall gaps fill at bar_open (better than TP)
- Gap-through stops get directional exit slippage
"""

from exit_checker import check_exit, ExitConfig, PositionState


def _bar(open_px, high, low, close):
    return {"open": open_px, "high": high, "low": low, "close": close}


def test_gap_long_stop_fill_at_open():
    """Long: 5R adverse gap fills at bar_open, not capped at 2R."""
    # Entry 100, stop 95, R=5. Bar opens at 70 => 5R gap past stop.
    # Old behavior: fill at max(70, 95-10) = max(70, 85) = 85
    # New behavior: fill at 70 (honest)
    result = check_exit(
        side="long",
        bar=_bar(70, 72, 68, 71),  # open=70, far below stop=95
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(),
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 70  # honest fill at bar_open, no 2R cap


def test_gap_short_stop_fill_at_open():
    """Short: adverse gap fills at bar_open."""
    # Entry 100, stop 105, R=5. Bar opens at 130 => 5R gap past stop.
    result = check_exit(
        side="short",
        bar=_bar(130, 132, 128, 129),  # open=130, far above stop=105
        entry_price=100,
        stop_price=105,
        tp1_price=90,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(),
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 130  # honest fill at bar_open


def test_gap_stop_no_cap_extreme():
    """Even extreme gaps (10R+) fill honestly at bar_open."""
    # Entry 100, stop 95, R=5. Bar opens at 40 => 11R gap.
    # Old: would cap at max(40, 95-10) = 85. New: 40.
    result = check_exit(
        side="long",
        bar=_bar(40, 42, 38, 41),
        entry_price=100,
        stop_price=95,
        tp1_price=None,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(),
    )
    assert result[0] == "STOP_LOSS"
    assert result[1] == 40  # no cap, full honest loss


def test_windfall_gap_long_tp1():
    """Long: bar gaps past TP1, fill at bar_open (better than TP)."""
    # Entry 100, TP1=110. Bar opens at 115 => windfall gap.
    result = check_exit(
        side="long",
        bar=_bar(115, 118, 114, 116),  # open=115 > tp1=110
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(),
    )
    assert result is not None
    assert result[0] == "TP1"
    assert result[1] == 115  # windfall: filled at bar_open, better than 110


def test_windfall_gap_short_tp1():
    """Short: bar gaps past TP1, fill at bar_open (better than TP)."""
    # Entry 100, TP1=90. Bar opens at 85 => windfall gap.
    result = check_exit(
        side="short",
        bar=_bar(85, 88, 83, 86),  # open=85 < tp1=90
        entry_price=100,
        stop_price=105,
        tp1_price=90,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(),
    )
    assert result is not None
    assert result[0] == "TP1"
    assert result[1] == 85  # windfall: filled at bar_open, better than 90


def test_windfall_gap_long_tp2():
    """Long: bar gaps past TP2, fill at bar_open."""
    result = check_exit(
        side="long",
        bar=_bar(125, 128, 124, 126),  # open=125 > tp2=120
        entry_price=100,
        stop_price=95,
        tp1_price=None,  # TP1 already taken
        tp2_price=120,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=5),
        # Disable trailing stop so TP2 isn't preempted by TRAIL_STOP
        config=ExitConfig(trail_breakeven_r=0, trail_activation_r=999),
    )
    assert result is not None
    assert result[0] == "TP2"
    assert result[1] == 125  # windfall at bar_open


def test_tp_normal_fill_unchanged():
    """When bar doesn't gap past TP, fill at TP price (unchanged behavior)."""
    result = check_exit(
        side="long",
        bar=_bar(105, 112, 104, 109),  # open=105 < tp1=110
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(),
    )
    assert result is not None
    assert result[0] == "TP1"
    assert result[1] == 110  # normal fill at TP price


def test_normal_stop_fill_unchanged():
    """When bar doesn't gap through stop, fill at stop_price (unchanged)."""
    result = check_exit(
        side="long",
        bar=_bar(97, 98, 93, 96),  # open=97 > stop=95, low=93 triggers
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(),
    )
    assert result is not None
    assert result[0] == "STOP_LOSS"
    assert result[1] == 95  # normal fill at stop_price


def test_intrabar_tp_first_windfall():
    """When intrabar policy picks TP and bar gapped past TP, fill at bar_open."""
    result = check_exit(
        side="long",
        bar=_bar(115, 118, 93, 116),  # both stop=95 and tp=110 hit, open=115>tp
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(intrabar_policy="TP_FIRST"),
    )
    assert result is not None
    assert result[0] == "TP1"
    assert result[1] == 115  # windfall at bar_open
