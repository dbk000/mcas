from exit_checker import check_exit, ExitConfig, PositionState


def _bar(open_px, high, low, close):
    return {"open": open_px, "high": high, "low": low, "close": close}


def test_intrabar_conflict_default_stop_first_long():
    result = check_exit(
        side="long",
        bar=_bar(100, 112, 94, 101),
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(),
    )
    assert result[0] == "STOP_LOSS"


def test_intrabar_conflict_tp_first_long():
    result = check_exit(
        side="long",
        bar=_bar(100, 112, 94, 101),
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(intrabar_policy="TP_FIRST"),
    )
    assert result == ("TP1", 110)


def test_intrabar_conflict_ohlc_heuristic_long_upbar_prefers_tp():
    result = check_exit(
        side="long",
        bar=_bar(100, 112, 94, 101),
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(intrabar_policy="OHLC_HEURISTIC"),
    )
    assert result == ("TP1", 110)


def test_intrabar_conflict_ohlc_heuristic_long_downbar_prefers_stop():
    result = check_exit(
        side="long",
        bar=_bar(100, 112, 94, 99),
        entry_price=100,
        stop_price=95,
        tp1_price=110,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(intrabar_policy="OHLC_HEURISTIC"),
    )
    assert result[0] == "STOP_LOSS"


def test_intrabar_conflict_short_symmetry():
    # Short conflict: high breaches stop and low reaches TP1 in same bar.
    # STOP_FIRST should stop out.
    stop_first = check_exit(
        side="short",
        bar=_bar(100, 106, 89, 95),
        entry_price=100,
        stop_price=105,
        tp1_price=90,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(intrabar_policy="STOP_FIRST"),
    )
    assert stop_first[0] == "STOP_LOSS"

    # TP_FIRST should hit TP1 first.
    tp_first = check_exit(
        side="short",
        bar=_bar(100, 106, 89, 95),
        entry_price=100,
        stop_price=105,
        tp1_price=90,
        tp2_price=None,
        r_value=5,
        max_bars=50,
        state=PositionState(bars_held=3),
        config=ExitConfig(intrabar_policy="TP_FIRST"),
    )
    assert tp_first == ("TP1", 90)
