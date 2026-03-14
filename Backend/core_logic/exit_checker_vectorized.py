"""
Vectorized Exit Checker
========================

Numpy-vectorized exit logic for simple configs (no trailing stops, no fail-fast).
Replaces the bar-by-bar Python loop in simulate_opportunity() for configs A-E, G.

Configs F (runner trail) and H (split exit) keep the scalar path — they have
stateful HWM/partial-close logic that can't be vectorized.

Usage:
    from core_logic.exit_checker_vectorized import check_exit_vectorized

    result = check_exit_vectorized(
        direction="long",
        entry_slip=100.5,
        stop_price=98.5,
        tp1_price=110.5,
        r_value=2.0,
        time_stop_bars=72,
        highs=np.array([...]),   # bars after entry
        lows=np.array([...]),
        opens=np.array([...]),
        closes=np.array([...]),
    )
"""
from typing import Optional
import numpy as np


def check_exit_vectorized(
    direction: str,
    entry_slip: float,
    stop_price: float,
    tp1_price: Optional[float],
    r_value: float,
    time_stop_bars: int,
    highs: np.ndarray,
    lows: np.ndarray,
    opens: np.ndarray,
    closes: np.ndarray,
    min_r_preservation: float = 0.5,
) -> Optional[tuple[str, float, int]]:
    """Vectorized exit check for simple configs (no trail, no fail-fast).

    Args:
        direction: "long" or "short"
        entry_slip: Entry price after slippage
        stop_price: Stop loss price
        tp1_price: Take profit price (or None for runner configs)
        r_value: Risk value
        time_stop_bars: Max bars for time stop
        highs/lows/opens/closes: numpy arrays of bar data AFTER entry bar
        min_r_preservation: Half-R progress threshold fraction

    Returns:
        (exit_reason, exit_price, bars_held) or None if no exit found
    """
    n_bars = len(highs)
    if n_bars == 0:
        return None

    # --- STOP_LOSS: find first bar where stop is hit ---
    if direction == "long":
        stop_mask = lows <= stop_price
    else:
        stop_mask = highs >= stop_price

    stop_indices = np.nonzero(stop_mask)[0]
    stop_bar = stop_indices[0] if len(stop_indices) > 0 else n_bars

    # --- TP1: find first bar where TP is hit ---
    tp1_bar = n_bars
    if tp1_price is not None:
        if direction == "long":
            tp1_mask = highs >= tp1_price
        else:
            tp1_mask = lows <= tp1_price
        tp1_indices = np.nonzero(tp1_mask)[0]
        tp1_bar = tp1_indices[0] if len(tp1_indices) > 0 else n_bars

    # --- TIME_STOP: check half-R progress within time_stop_bars ---
    # Time stop fires at bar time_stop_bars+1 if no half-R progress made
    time_stop_bar = n_bars
    if time_stop_bars < n_bars and r_value > 0:
        half_r_target = min_r_preservation * r_value
        if direction == "long":
            progress_mask = highs[:time_stop_bars + 1] >= entry_slip + half_r_target
        else:
            progress_mask = lows[:time_stop_bars + 1] <= entry_slip - half_r_target

        if not np.any(progress_mask):
            # No progress — time stop at bar time_stop_bars+1
            time_stop_bar = time_stop_bars + 1 if time_stop_bars + 1 < n_bars else n_bars

    # --- Determine which exit fires first ---
    # Same-bar stop+TP conflict: STOP_FIRST policy (matches scalar path)
    if stop_bar == tp1_bar and stop_bar < n_bars:
        # Both hit on same bar — stop wins (STOP_FIRST policy)
        exit_bar = stop_bar
        exit_reason = "STOP_LOSS"
    elif stop_bar <= tp1_bar and stop_bar <= time_stop_bar and stop_bar < n_bars:
        exit_bar = stop_bar
        exit_reason = "STOP_LOSS"
    elif tp1_bar < stop_bar and tp1_bar <= time_stop_bar and tp1_bar < n_bars:
        exit_bar = tp1_bar
        exit_reason = "TP1"
    elif time_stop_bar < n_bars:
        exit_bar = time_stop_bar
        exit_reason = "TIME_STOP"
    else:
        # MAX_BARS — no exit triggered
        return "MAX_BARS", float(closes[-1]), int(n_bars)

    # --- Compute exit price ---
    bars_held = int(exit_bar) + 1  # 0-indexed bar -> 1-indexed count (cast from numpy)

    if exit_reason == "STOP_LOSS":
        bar_open = float(opens[exit_bar])
        if direction == "long":
            exit_price = bar_open if bar_open < stop_price else stop_price
        else:
            exit_price = bar_open if bar_open > stop_price else stop_price
    elif exit_reason == "TP1":
        bar_open = float(opens[exit_bar])
        if direction == "long":
            exit_price = bar_open if bar_open > tp1_price else tp1_price
        else:
            exit_price = bar_open if bar_open < tp1_price else tp1_price
    elif exit_reason == "TIME_STOP":
        exit_price = float(opens[exit_bar])

    return exit_reason, exit_price, bars_held
