"""
Exit Checker
=============

Shared exit logic for positions. Used by both run_executor (BACKTEST/PAPER)
and opportunity_simulator (SIMULATION) to ensure consistent exit behavior.

Canon exit priority order: STOP_LOSS -> FAIL_FAST -> TIME_STOP -> TRAIL_STOP -> TP1 -> TP2
"""

from typing import Optional
from dataclasses import dataclass

INTRABAR_STOP_FIRST = "STOP_FIRST"
INTRABAR_TP_FIRST = "TP_FIRST"
INTRABAR_OHLC_HEURISTIC = "OHLC_HEURISTIC"
_VALID_INTRABAR_POLICIES = {
    INTRABAR_STOP_FIRST,
    INTRABAR_TP_FIRST,
    INTRABAR_OHLC_HEURISTIC,
}


@dataclass
class ExitConfig:
    """Configuration for exit checking."""
    # Fill assumptions
    stop_fill_through: bool = False  # True = THROUGH, False = TOUCH
    target_fill_through: bool = False  # True = THROUGH, False = TOUCH

    # Fail-fast threshold
    fail_fast_bars: int = 2
    fail_fast_threshold: float = 0.003  # 0.3% below/above entry (tightened from 0.5% per sim analysis)

    # Trail parameters (sweepable via exit_sweep.py)
    trail_breakeven_r: float = 0.75   # HWM threshold for breakeven trail (0 = disabled)
    trail_activation_r: float = 1.0   # HWM threshold for tight trail
    trail_distance_r: float = 0.5     # Distance from HWM for tight trail

    # Intrabar conflict policy for same-bar stop/TP ambiguity.
    # Conservative default: STOP_FIRST, one event per bar.
    intrabar_policy: str = INTRABAR_STOP_FIRST
    one_event_per_bar: bool = True


@dataclass
class PositionState:
    """Mutable state tracked for a position during exit checking."""
    bars_held: int = 0
    high_water_mark: Optional[float] = None
    half_r_touched: bool = False


def check_exit(
    side: str,
    bar: dict,
    entry_price: float,
    stop_price: float,
    tp1_price: Optional[float],
    tp2_price: Optional[float],
    r_value: float,
    max_bars: int,
    state: PositionState,
    config: ExitConfig = None,
    is_entry_bar: bool = False,
) -> Optional[tuple[str, float]]:
    """
    Check if position should exit. Returns (reason, exit_price) or None.

    Priority order (Canon): STOP_LOSS -> FAIL_FAST -> TIME_STOP -> TRAIL_STOP -> TP1 -> TP2.

    Args:
        side: "long" or "short"
        bar: dict with {open, high, low, close}
        entry_price: Position entry price
        stop_price: Stop loss price
        tp1_price: Take profit 1 price (optional)
        tp2_price: Take profit 2 price (optional)
        r_value: Risk value (entry - stop for long, stop - entry for short)
        max_bars: Maximum bars before time stop
        state: Mutable PositionState object (will be updated)
        config: ExitConfig for fill assumptions

    Returns:
        (exit_reason, exit_price) if exit triggered, else None
    """
    if config is None:
        config = ExitConfig()

    high = bar["high"]
    low = bar["low"]
    close = bar["close"]
    bar_open = bar["open"]
    bars_held = state.bars_held

    use_through_stop = config.stop_fill_through
    use_through_target = config.target_fill_through

    # --- SAME-BAR STOP/TP1 CONFLICT RESOLUTION ---
    # When both stop and TP1 are hit inside one candle, choose a deterministic
    # policy. Default remains conservative STOP_FIRST.
    if tp1_price is not None:
        if side == "long":
            stop_hit = (low < stop_price) if use_through_stop else (low <= stop_price)
            tp1_hit = (high > tp1_price) if use_through_target else (high >= tp1_price)
        else:
            stop_hit = (high > stop_price) if use_through_stop else (high >= stop_price)
            tp1_hit = (low < tp1_price) if use_through_target else (low <= tp1_price)

        if stop_hit and tp1_hit:
            policy = config.intrabar_policy if config.intrabar_policy in _VALID_INTRABAR_POLICIES else INTRABAR_STOP_FIRST
            if policy == INTRABAR_TP_FIRST:
                # Windfall gap: if bar opened past TP, fill at bar_open
                if side == "long" and bar_open > tp1_price:
                    return ("TP1", bar_open)
                elif side == "short" and bar_open < tp1_price:
                    return ("TP1", bar_open)
                return ("TP1", tp1_price)
            if policy == INTRABAR_OHLC_HEURISTIC:
                # Heuristic path:
                # close >= open => Open->High->Low->Close
                # close < open  => Open->Low->High->Close
                up_bar = close >= bar_open
                if side == "long":
                    if up_bar:
                        tp_fill = bar_open if bar_open > tp1_price else tp1_price
                        return ("TP1", tp_fill)
                else:
                    if not up_bar:
                        tp_fill = bar_open if bar_open < tp1_price else tp1_price
                        return ("TP1", tp_fill)
            # STOP_FIRST falls through to normal stop check below.

    # --- STOP_LOSS (highest priority) ---
    if side == "long":
        stop_triggered = (low < stop_price) if use_through_stop else (low <= stop_price)
        if stop_triggered:
            if bar_open < stop_price and not is_entry_bar:
                fill_price = bar_open  # honest gap-through fill (slippage applied by simulator)
            else:
                fill_price = stop_price
            return ("STOP_LOSS", fill_price)
    else:
        stop_triggered = (high > stop_price) if use_through_stop else (high >= stop_price)
        if stop_triggered:
            if bar_open > stop_price and not is_entry_bar:
                fill_price = bar_open  # honest gap-through fill (slippage applied by simulator)
            else:
                fill_price = stop_price
            return ("STOP_LOSS", fill_price)

    # --- FAIL_FAST: close on wrong side of entry within first N bars ---
    # bars_held=0 is the entry bar — trade hasn't had time to "fail fast" yet
    if bars_held > 0 and bars_held <= config.fail_fast_bars:
        threshold = config.fail_fast_threshold
        if side == "long" and close < entry_price * (1 - threshold):
            return ("FAIL_FAST", close)
        elif side == "short" and close > entry_price * (1 + threshold):
            return ("FAIL_FAST", close)

    # --- TIME_STOP (Canon v2: wick-based progress check) ---
    # Requirement: Price must reach Entry +/- 0.5R within max_bars.
    # If not reached, exit at the next bar's open (window_end + 1).
    progress_made = state.half_r_touched

    if not progress_made and r_value > 0:
        half_r_target = 0.5 * r_value
        if side == "long":
            # Progress made if wick reached Entry + 0.5R
            if high >= entry_price + half_r_target:
                state.half_r_touched = True
                progress_made = True
        else:
            # Progress made if wick reached Entry - 0.5R
            if low <= entry_price - half_r_target:
                state.half_r_touched = True
                progress_made = True

    # Time stop triggers at bar max_bars + 1 (window_end + 1) IF no progress made
    if bars_held > max_bars and not progress_made:
        return ("TIME_STOP", bar_open)

    # --- TRAILING STOP: two-stage (breakeven + tight trail, thresholds from config) ---
    hwm = state.high_water_mark
    if r_value > 0:
        if side == "long":
            # Track high water mark
            if hwm is None or high > hwm:
                state.high_water_mark = high
                hwm = high
            # Stage 2: tight trail after activation_r profit
            if hwm >= entry_price + config.trail_activation_r * r_value:
                trail_stop = hwm - config.trail_distance_r * r_value
                if low <= trail_stop:
                    # Guard: prefer TP1 if it also triggers on this bar (better fill)
                    if tp1_price and high >= tp1_price:
                        pass  # TP1 will fire at priority 5
                    else:
                        fill_price = bar_open if bar_open < trail_stop else trail_stop
                        return ("TRAIL_STOP", fill_price)
            # Stage 1: breakeven after breakeven_r profit (0 = disabled)
            elif config.trail_breakeven_r > 0 and hwm >= entry_price + config.trail_breakeven_r * r_value:
                trail_stop = entry_price
                if low <= trail_stop:
                    if tp1_price and high >= tp1_price:
                        pass  # TP1 will fire at priority 5
                    else:
                        fill_price = bar_open if bar_open < trail_stop else trail_stop
                        return ("TRAIL_STOP", fill_price)
        else:
            if hwm is None or low < hwm:
                state.high_water_mark = low
                hwm = low
            # Stage 2: tight trail after activation_r profit
            if hwm <= entry_price - config.trail_activation_r * r_value:
                trail_stop = hwm + config.trail_distance_r * r_value
                if high >= trail_stop:
                    if tp1_price and low <= tp1_price:
                        pass  # TP1 will fire at priority 5
                    else:
                        fill_price = bar_open if bar_open > trail_stop else trail_stop
                        return ("TRAIL_STOP", fill_price)
            # Stage 1: breakeven after breakeven_r profit (0 = disabled)
            elif config.trail_breakeven_r > 0 and hwm <= entry_price - config.trail_breakeven_r * r_value:
                trail_stop = entry_price
                if high >= trail_stop:
                    if tp1_price and low <= tp1_price:
                        pass  # TP1 will fire at priority 5
                    else:
                        fill_price = bar_open if bar_open > trail_stop else trail_stop
                        return ("TRAIL_STOP", fill_price)

    # --- TP1 (partial close handled by caller) ---
    if tp1_price:
        if side == "long":
            tp1_hit = (high > tp1_price) if use_through_target else (high >= tp1_price)
            if tp1_hit:
                # Windfall gap: bar opened past TP, fill at bar_open (better than TP)
                if bar_open > tp1_price:
                    return ("TP1", bar_open)
                return ("TP1", tp1_price)
        else:
            tp1_hit = (low < tp1_price) if use_through_target else (low <= tp1_price)
            if tp1_hit:
                if bar_open < tp1_price:
                    return ("TP1", bar_open)
                return ("TP1", tp1_price)

    # --- TP2 ---
    if tp2_price:
        if side == "long":
            tp2_hit = (high > tp2_price) if use_through_target else (high >= tp2_price)
            if tp2_hit:
                if bar_open > tp2_price:
                    return ("TP2", bar_open)
                return ("TP2", tp2_price)
        else:
            tp2_hit = (low < tp2_price) if use_through_target else (low <= tp2_price)
            if tp2_hit:
                if bar_open < tp2_price:
                    return ("TP2", bar_open)
                return ("TP2", tp2_price)

    return None


def check_exit_simple(
    side: str,
    bar: dict,
    entry_price: float,
    stop_price: float,
    tp1_price: Optional[float],
    tp2_price: Optional[float],
    r_value: float,
    bars_held: int,
    max_bars: int,
    high_water_mark: Optional[float] = None,
    half_r_touched: bool = False,
    config: ExitConfig = None,
    is_entry_bar: bool = False,
) -> tuple[Optional[tuple[str, float]], Optional[float], bool]:
    """
    Simplified exit check that doesn't require PositionState object.

    Returns:
        (exit_result, new_hwm, new_half_r_touched)
        where exit_result is (reason, price) or None
    """
    state = PositionState(
        bars_held=bars_held,
        high_water_mark=high_water_mark,
        half_r_touched=half_r_touched,
    )

    result = check_exit(
        side=side,
        bar=bar,
        entry_price=entry_price,
        stop_price=stop_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        r_value=r_value,
        max_bars=max_bars,
        state=state,
        config=config,
        is_entry_bar=is_entry_bar,
    )

    return result, state.high_water_mark, state.half_r_touched
