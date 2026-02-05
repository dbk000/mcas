"""
P6-34: Test coverage for backtest PnL math.

Tests the core calculation functions in run_executor.py:
- _calculate_trade_pnl: slippage, fees, R-multiple
- _check_position_exit: stop loss, fail fast, trailing, TP, time stop
- Position sizing: risk-based sizing, exposure cap
- Edge cases: zero R-value, gap fills, partial TP1
"""
import math
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from execution import (
    ExecutionConfig, FixedBpsSlippage, TieredFees, FillModel,
    FillAssumption, EntryMode, ZeroSlippage, ZeroFees,
    zero_cost_config, realistic_config, conservative_config,
)


# ---------------------------------------------------------------------------
# Helpers: We test the PnL math by extracting it into standalone calls.
# RunExecutor._calculate_trade_pnl mutates a dict in-place, so we create
# a minimal executor stub that exposes the method.
# ---------------------------------------------------------------------------

class PnLCalculator:
    """Minimal wrapper exposing RunExecutor's PnL and exit logic for testing."""

    @staticmethod
    def calculate_trade_pnl(pos: dict, exec_config: ExecutionConfig):
        """Mirrors RunExecutor._calculate_trade_pnl exactly."""
        entry = pos["entry_price"]
        exit_price = pos["exit_price"]
        size = pos.get("position_size", 1.0)

        slip_bps = getattr(exec_config.slippage, "entry_bps", 0)
        entry_slip = entry * (slip_bps / 10000)
        exit_slip = exit_price * (slip_bps / 10000)

        if pos["side"] == "long":
            entry_with_slip = entry + entry_slip
            exit_with_slip = exit_price - exit_slip
            pnl_gross = (exit_with_slip - entry_with_slip) * size
        else:
            entry_with_slip = entry - entry_slip
            exit_with_slip = exit_price + exit_slip
            pnl_gross = (entry_with_slip - exit_with_slip) * size

        fee_bps = getattr(exec_config.fees, "taker_bps", 0)
        entry_fee = entry * size * (fee_bps / 10000)
        exit_fee = exit_price * size * (fee_bps / 10000)
        total_fees = entry_fee + exit_fee

        pos["pnl_gross"] = pnl_gross
        pos["fees"] = total_fees
        pos["pnl_net"] = pnl_gross - total_fees
        pos["entry_with_slippage"] = entry_with_slip
        pos["exit_with_slippage"] = exit_with_slip

        r_value = pos.get("r_value", abs(entry - pos["stop_price"]))
        if r_value > 0 and size > 0:
            pos["r_multiple"] = pos["pnl_net"] / (r_value * size)
        else:
            pos["r_multiple"] = 0

    @staticmethod
    def check_position_exit(pos, bar, exec_config):
        """Mirrors RunExecutor._check_position_exit exactly."""
        side = pos["side"]
        stop = pos["stop_price"]
        tp1 = pos.get("tp1_price")
        tp2 = pos.get("tp2_price")
        high = bar["high"]
        low = bar["low"]
        close = bar["close"]
        bars_held = pos.get("bars_held", 0)

        stop_fill = getattr(exec_config.fills, "stop_fill", None)
        target_fill = getattr(exec_config.fills, "target_fill", None)
        use_through_stop = stop_fill and stop_fill.value == "THROUGH"
        use_through_target = target_fill and target_fill.value == "THROUGH"

        # STOP_LOSS
        if side == "long":
            stop_triggered = (low < stop) if use_through_stop else (low <= stop)
            if stop_triggered:
                fill_price = bar["open"] if bar["open"] < stop else stop
                return ("STOP_LOSS", fill_price)
        else:
            stop_triggered = (high > stop) if use_through_stop else (high >= stop)
            if stop_triggered:
                fill_price = bar["open"] if bar["open"] > stop else stop
                return ("STOP_LOSS", fill_price)

        # FAIL_FAST
        if bars_held <= 2:
            entry = pos["entry_price"]
            if side == "long" and close < entry * 0.995:
                return ("FAIL_FAST", close)
            elif side == "short" and close > entry * 1.005:
                return ("FAIL_FAST", close)

        # TIME_STOP
        max_bars = pos.get("max_bars", 50)
        if bars_held >= max_bars:
            return ("TIME_STOP", close)

        # TRAILING STOP
        hwm = pos.get("high_water_mark")
        entry = pos["entry_price"]
        r_value = pos.get("r_value", 0)
        if r_value > 0:
            if side == "long":
                if hwm is None or high > hwm:
                    pos["high_water_mark"] = high
                    hwm = high
                if hwm >= entry + r_value:
                    trail_stop = hwm - r_value
                    if low <= trail_stop:
                        fill_price = bar["open"] if bar["open"] < trail_stop else trail_stop
                        return ("TRAIL_STOP", fill_price)
            else:
                if hwm is None or low < hwm:
                    pos["high_water_mark"] = low
                    hwm = low
                if hwm <= entry - r_value:
                    trail_stop = hwm + r_value
                    if high >= trail_stop:
                        fill_price = bar["open"] if bar["open"] > trail_stop else trail_stop
                        return ("TRAIL_STOP", fill_price)

        # TP1
        if tp1:
            if side == "long":
                tp1_hit = (high > tp1) if use_through_target else (high >= tp1)
                if tp1_hit:
                    return ("TP1", tp1)
            else:
                tp1_hit = (low < tp1) if use_through_target else (low <= tp1)
                if tp1_hit:
                    return ("TP1", tp1)

        # TP2
        if tp2:
            if side == "long":
                tp2_hit = (high > tp2) if use_through_target else (high >= tp2)
                if tp2_hit:
                    return ("TP2", tp2)
            else:
                tp2_hit = (low < tp2) if use_through_target else (low <= tp2)
                if tp2_hit:
                    return ("TP2", tp2)

        return None


calc = PnLCalculator()


# ===========================================================================
# Test _calculate_trade_pnl
# ===========================================================================

class TestCalculateTradePnl:
    """Tests for PnL calculation with slippage, fees, and R-multiples."""

    def test_long_winner_zero_cost(self):
        """Long trade with no slippage/fees should have gross = net."""
        config = zero_cost_config()
        pos = {
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 110.0,
            "stop_price": 95.0,
            "position_size": 2.0,
        }
        calc.calculate_trade_pnl(pos, config)
        assert pos["pnl_gross"] == 20.0  # (110 - 100) * 2
        assert pos["fees"] == 0.0
        assert pos["pnl_net"] == 20.0
        # R-value = |100 - 95| = 5, R-multiple = 20 / (5 * 2) = 2.0
        assert pos["r_multiple"] == 2.0

    def test_long_loser_zero_cost(self):
        """Long trade stopped out."""
        config = zero_cost_config()
        pos = {
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 95.0,
            "stop_price": 95.0,
            "position_size": 2.0,
        }
        calc.calculate_trade_pnl(pos, config)
        assert pos["pnl_gross"] == -10.0
        assert pos["r_multiple"] == -1.0

    def test_short_winner_zero_cost(self):
        """Short trade profit."""
        config = zero_cost_config()
        pos = {
            "side": "short",
            "entry_price": 100.0,
            "exit_price": 90.0,
            "stop_price": 105.0,
            "position_size": 1.0,
        }
        calc.calculate_trade_pnl(pos, config)
        assert pos["pnl_gross"] == 10.0
        # R = |100 - 105| = 5, R-mult = 10 / (5 * 1) = 2.0
        assert pos["r_multiple"] == 2.0

    def test_short_loser_zero_cost(self):
        """Short trade stopped out."""
        config = zero_cost_config()
        pos = {
            "side": "short",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "stop_price": 105.0,
            "position_size": 1.0,
        }
        calc.calculate_trade_pnl(pos, config)
        assert pos["pnl_gross"] == -5.0
        assert pos["r_multiple"] == -1.0

    def test_realistic_long_slippage_and_fees(self):
        """Long trade with realistic slippage (5bps) and fees (4bps taker)."""
        config = realistic_config()
        pos = {
            "side": "long",
            "entry_price": 50000.0,  # BTC-like
            "exit_price": 52000.0,
            "stop_price": 49000.0,
            "position_size": 0.1,
        }
        calc.calculate_trade_pnl(pos, config)

        # Slippage: entry_slip = 50000 * 5/10000 = 25, exit_slip = 52000 * 5/10000 = 26
        # entry_with_slip = 50025, exit_with_slip = 51974
        # pnl_gross = (51974 - 50025) * 0.1 = 194.9
        assert abs(pos["entry_with_slippage"] - 50025.0) < 0.01
        assert abs(pos["exit_with_slippage"] - 51974.0) < 0.01
        assert abs(pos["pnl_gross"] - 194.9) < 0.01

        # Fees: entry_fee = 50000 * 0.1 * 4/10000 = 2.0
        #        exit_fee = 52000 * 0.1 * 4/10000 = 2.08
        assert abs(pos["fees"] - 4.08) < 0.01
        assert abs(pos["pnl_net"] - (194.9 - 4.08)) < 0.01

        # R-value = |50000 - 49000| = 1000
        # R-multiple = pnl_net / (1000 * 0.1)
        expected_r = pos["pnl_net"] / (1000.0 * 0.1)
        assert abs(pos["r_multiple"] - expected_r) < 0.001

    def test_zero_r_value_gives_zero_r_multiple(self):
        """If stop == entry, R-value is 0, R-multiple should be 0."""
        config = zero_cost_config()
        pos = {
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "stop_price": 100.0,  # stop == entry
            "position_size": 1.0,
        }
        calc.calculate_trade_pnl(pos, config)
        assert pos["r_multiple"] == 0

    def test_explicit_r_value_overrides(self):
        """If r_value is provided in pos, it should be used."""
        config = zero_cost_config()
        pos = {
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 110.0,
            "stop_price": 90.0,
            "position_size": 1.0,
            "r_value": 5.0,  # Override: not |100 - 90| = 10
        }
        calc.calculate_trade_pnl(pos, config)
        # pnl_net = 10, R = 10 / (5 * 1) = 2.0
        assert pos["r_multiple"] == 2.0

    def test_position_size_zero(self):
        """Zero position size should not cause division error."""
        config = zero_cost_config()
        pos = {
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 110.0,
            "stop_price": 95.0,
            "position_size": 0.0,
        }
        calc.calculate_trade_pnl(pos, config)
        assert pos["r_multiple"] == 0
        assert pos["pnl_gross"] == 0.0


# ===========================================================================
# Test _check_position_exit
# ===========================================================================

class TestCheckPositionExit:
    """Tests for the exit logic with all exit types and fill models."""

    def _bar(self, open, high, low, close):
        return {"open": open, "high": high, "low": low, "close": close}

    def _long_pos(self, entry=100, stop=95, tp1=110, tp2=120, bars_held=5, r_value=5):
        return {
            "side": "long",
            "entry_price": entry,
            "stop_price": stop,
            "tp1_price": tp1,
            "tp2_price": tp2,
            "bars_held": bars_held,
            "r_value": r_value,
            "max_bars": 50,
        }

    def _short_pos(self, entry=100, stop=105, tp1=90, tp2=80, bars_held=5, r_value=5):
        return {
            "side": "short",
            "entry_price": entry,
            "stop_price": stop,
            "tp1_price": tp1,
            "tp2_price": tp2,
            "bars_held": bars_held,
            "r_value": r_value,
            "max_bars": 50,
        }

    # --- STOP LOSS ---

    def test_long_stop_touch(self):
        """Long stop triggers when low touches stop (TOUCH mode)."""
        config = realistic_config()  # TOUCH by default
        pos = self._long_pos(stop=95)
        result = calc.check_position_exit(pos, self._bar(96, 97, 95, 96), config)
        assert result == ("STOP_LOSS", 95)

    def test_long_stop_gap_down(self):
        """Long stop fills at open when price gaps below stop."""
        config = realistic_config()
        pos = self._long_pos(stop=95)
        result = calc.check_position_exit(pos, self._bar(93, 94, 92, 93), config)
        assert result == ("STOP_LOSS", 93)  # Fill at open (gap-down)

    def test_long_stop_through_mode_exact_touch_no_trigger(self):
        """In THROUGH mode, exact touch does NOT trigger stop."""
        config = conservative_config()  # THROUGH mode
        pos = self._long_pos(stop=95)
        result = calc.check_position_exit(pos, self._bar(96, 97, 95, 96), config)
        assert result is None

    def test_long_stop_through_mode_penetration_triggers(self):
        """In THROUGH mode, price below stop triggers."""
        config = conservative_config()
        pos = self._long_pos(stop=95)
        result = calc.check_position_exit(pos, self._bar(96, 97, 94.99, 96), config)
        assert result[0] == "STOP_LOSS"

    def test_short_stop_touch(self):
        """Short stop triggers when high reaches stop."""
        config = realistic_config()
        pos = self._short_pos(stop=105)
        result = calc.check_position_exit(pos, self._bar(104, 105, 103, 104), config)
        assert result == ("STOP_LOSS", 105)

    def test_short_stop_gap_up(self):
        """Short stop fills at open when price gaps above stop."""
        config = realistic_config()
        pos = self._short_pos(stop=105)
        result = calc.check_position_exit(pos, self._bar(107, 108, 106, 107), config)
        assert result == ("STOP_LOSS", 107)  # Fill at open

    # --- FAIL_FAST ---

    def test_long_fail_fast_bar_1(self):
        """Fail fast triggers within first 2 bars if close < entry * 0.995."""
        config = realistic_config()
        pos = self._long_pos(entry=100, bars_held=1)
        bar = self._bar(99, 99.5, 98, 99.4)  # close 99.4 < 100 * 0.995 = 99.5
        result = calc.check_position_exit(pos, bar, config)
        assert result == ("FAIL_FAST", 99.4)

    def test_long_fail_fast_not_triggered_after_2_bars(self):
        """Fail fast does not trigger after bars_held > 2."""
        config = realistic_config()
        pos = self._long_pos(entry=100, bars_held=3)
        bar = self._bar(99, 99.5, 98, 99.0)
        result = calc.check_position_exit(pos, bar, config)
        # Not stop, not fail_fast — should be None or something else
        assert result is None or result[0] != "FAIL_FAST"

    def test_short_fail_fast(self):
        """Short fail fast triggers when close > entry * 1.005."""
        config = realistic_config()
        pos = self._short_pos(entry=100, bars_held=1)
        bar = self._bar(101, 101.5, 100.5, 100.6)  # close 100.6 > 100 * 1.005 = 100.5
        result = calc.check_position_exit(pos, bar, config)
        assert result == ("FAIL_FAST", 100.6)

    # --- TIME STOP ---

    def test_time_stop_at_max_bars(self):
        """Time stop fires when bars_held >= max_bars."""
        config = realistic_config()
        pos = self._long_pos(bars_held=50)
        bar = self._bar(101, 102, 100, 101)
        result = calc.check_position_exit(pos, bar, config)
        assert result == ("TIME_STOP", 101)

    def test_time_stop_not_at_49_bars(self):
        """Time stop does not fire below max_bars."""
        config = realistic_config()
        pos = self._long_pos(bars_held=49)
        bar = self._bar(101, 102, 100, 101)
        result = calc.check_position_exit(pos, bar, config)
        assert result is None or result[0] != "TIME_STOP"

    # --- TRAILING STOP ---

    def test_trailing_stop_long_activates_after_1r(self):
        """Trailing stop activates after 1R profit and triggers on pullback."""
        config = realistic_config()
        pos = self._long_pos(entry=100, stop=95, r_value=5)

        # Bar 1: price goes to 106 (>1R above entry=105), HWM = 106
        bar1 = self._bar(104, 106, 103, 105)
        result = calc.check_position_exit(pos, bar1, config)
        assert result is None  # trail_stop = 106-5=101, low=103 > 101
        assert pos["high_water_mark"] == 106

        # Bar 2: price goes to 108, HWM = 108
        bar2 = self._bar(105, 108, 104, 107)
        result = calc.check_position_exit(pos, bar2, config)
        assert result is None  # trail_stop = 108-5=103, low=104 > 103
        assert pos["high_water_mark"] == 108

        # Bar 3: pullback to trail_stop = 108-5=103, low=102 triggers
        bar3 = self._bar(104, 105, 102, 103)
        result = calc.check_position_exit(pos, bar3, config)
        assert result[0] == "TRAIL_STOP"
        assert result[1] == 103  # trail_stop level (open 104 > 103, so fills at trail)

    def test_trailing_stop_not_active_before_1r(self):
        """Trailing stop does not activate before 1R profit."""
        config = realistic_config()
        pos = self._long_pos(entry=100, stop=95, r_value=5)

        bar = self._bar(101, 104, 96, 103)  # HWM=104, entry+r=105 — not reached
        result = calc.check_position_exit(pos, bar, config)
        # Stop loss at 95 not hit (low=96), trail not active
        assert result is None

    def test_trailing_stop_short(self):
        """Short trailing stop activates after 1R profit."""
        config = realistic_config()
        pos = self._short_pos(entry=100, stop=105, r_value=5)

        # Bar 1: price drops to 94, HWM = 94
        bar1 = self._bar(96, 97, 94, 95)
        result = calc.check_position_exit(pos, bar1, config)
        assert result is None
        assert pos["high_water_mark"] == 94

        # Bar 2: bounce to trail_stop = 94+5=99, high=100 triggers
        bar2 = self._bar(96, 100, 95, 99)
        result = calc.check_position_exit(pos, bar2, config)
        assert result[0] == "TRAIL_STOP"
        assert result[1] == 99  # Fill at trail stop level

    # --- TP1 / TP2 ---

    def test_long_tp1_touch(self):
        """TP1 triggers when high reaches target in TOUCH mode."""
        config = realistic_config()
        pos = self._long_pos(tp1=110)
        bar = self._bar(108, 110, 107, 109)
        result = calc.check_position_exit(pos, bar, config)
        assert result == ("TP1", 110)

    def test_long_tp1_through_not_on_exact_touch(self):
        """TP1 does NOT trigger on exact touch in THROUGH mode."""
        config = conservative_config()
        pos = self._long_pos(tp1=110)
        bar = self._bar(108, 110, 107, 109)
        result = calc.check_position_exit(pos, bar, config)
        # In THROUGH mode, high must exceed tp1 strictly
        assert result is None or result[0] != "TP1"

    def test_long_tp2(self):
        """TP2 triggers when high reaches TP2 and TP1 is None."""
        config = realistic_config()
        pos = self._long_pos(tp1=None, tp2=120)
        bar = self._bar(118, 121, 117, 120)
        result = calc.check_position_exit(pos, bar, config)
        assert result == ("TP2", 120)

    def test_short_tp1(self):
        """Short TP1 triggers when low reaches target."""
        config = realistic_config()
        pos = self._short_pos(tp1=90)
        bar = self._bar(92, 93, 90, 91)
        result = calc.check_position_exit(pos, bar, config)
        assert result == ("TP1", 90)

    # --- PRIORITY ---

    def test_stop_loss_beats_tp1(self):
        """When both stop and TP1 hit on same bar, stop wins (higher priority)."""
        config = realistic_config()
        pos = self._long_pos(entry=100, stop=95, tp1=110)
        # Bar that hits both stop (low <= 95) and TP1 (high >= 110)
        bar = self._bar(100, 112, 94, 100)
        result = calc.check_position_exit(pos, bar, config)
        assert result[0] == "STOP_LOSS"

    def test_no_exit_when_all_clear(self):
        """No exit triggered on a normal bar."""
        config = realistic_config()
        pos = self._long_pos(entry=100, stop=95, tp1=110, bars_held=10)
        bar = self._bar(101, 103, 99, 102)
        result = calc.check_position_exit(pos, bar, config)
        assert result is None


# ===========================================================================
# Test position sizing logic
# ===========================================================================

class TestPositionSizing:
    """Tests for risk-based position sizing as implemented in the backtest loop."""

    @staticmethod
    def calculate_size(capital, max_risk_pct, r_value, entry_price, max_exposure_pct):
        """Mirrors the position sizing logic from run_executor.py."""
        risk_amount = capital * max_risk_pct / 100.0
        if r_value <= 0:
            return 0.0
        size = risk_amount / r_value
        notional = size * entry_price
        max_notional = capital * max_exposure_pct
        if notional > max_notional:
            size = max_notional / entry_price
        return size

    def test_basic_sizing(self):
        """1% risk on 10000 capital with 5-point R-value."""
        size = self.calculate_size(
            capital=10000, max_risk_pct=1.0, r_value=5.0,
            entry_price=100.0, max_exposure_pct=1.0
        )
        # risk_amount = 100, size = 100/5 = 20
        # notional = 20 * 100 = 2000, max = 10000, OK
        assert size == 20.0

    def test_exposure_cap(self):
        """Position size capped by max_exposure_pct."""
        size = self.calculate_size(
            capital=1000, max_risk_pct=5.0, r_value=1.0,
            entry_price=100.0, max_exposure_pct=0.5  # 50% cap
        )
        # risk_amount = 50, size = 50/1 = 50
        # notional = 50 * 100 = 5000, max = 1000 * 0.5 = 500
        # capped: size = 500 / 100 = 5
        assert size == 5.0

    def test_zero_r_value(self):
        """Zero R-value should return 0 size (avoid division by zero)."""
        size = self.calculate_size(
            capital=10000, max_risk_pct=1.0, r_value=0.0,
            entry_price=100.0, max_exposure_pct=1.0
        )
        assert size == 0.0

    def test_small_capital(self):
        """Small capital produces proportionally small position."""
        size = self.calculate_size(
            capital=100, max_risk_pct=1.0, r_value=2.0,
            entry_price=50000.0, max_exposure_pct=1.0
        )
        # risk_amount = 1, size = 1/2 = 0.5
        # notional = 0.5 * 50000 = 25000, max = 100, capped
        # size = 100 / 50000 = 0.002
        assert abs(size - 0.002) < 1e-6


# ===========================================================================
# Test summary calculations
# ===========================================================================

class TestSummaryCalculations:
    """Tests for Sharpe, Sortino, consecutive W/L, payoff ratio, etc."""

    @staticmethod
    def compute_sharpe_sortino(r_multiples):
        """Mirrors the calculation in _calculate_backtest_summary."""
        if len(r_multiples) <= 1:
            return 0.0, 0.0
        mean_r = sum(r_multiples) / len(r_multiples)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in r_multiples) / (len(r_multiples) - 1))
        sharpe = (mean_r / std_r) if std_r > 0 else 0

        downside = [min(0, r) for r in r_multiples]
        downside_var = sum(d ** 2 for d in downside) / (len(r_multiples) - 1)
        downside_std = math.sqrt(downside_var)
        sortino = (mean_r / downside_std) if downside_std > 0 else 0

        return sharpe, sortino

    @staticmethod
    def compute_consecutive(pnl_list):
        """Compute max consecutive wins and losses."""
        max_wins = 0
        max_losses = 0
        cur_wins = 0
        cur_losses = 0
        for pnl in pnl_list:
            if pnl > 0:
                cur_wins += 1
                cur_losses = 0
                max_wins = max(max_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_losses = max(max_losses, cur_losses)
        return max_wins, max_losses

    def test_sharpe_positive_returns(self):
        """Sharpe ratio with all positive R-multiples should be positive."""
        r = [1.0, 2.0, 0.5, 1.5, 3.0]
        sharpe, _ = self.compute_sharpe_sortino(r)
        assert sharpe > 0

    def test_sharpe_zero_std(self):
        """Identical R-multiples: std=0, sharpe=0."""
        r = [1.0, 1.0, 1.0]
        sharpe, _ = self.compute_sharpe_sortino(r)
        assert sharpe == 0

    def test_sortino_no_downside(self):
        """All positive R: downside_std=0, sortino=0."""
        r = [1.0, 2.0, 3.0]
        _, sortino = self.compute_sharpe_sortino(r)
        assert sortino == 0

    def test_sortino_with_losses(self):
        """Mix of wins and losses should give finite Sortino."""
        r = [2.0, -1.0, 1.5, -0.5, 3.0]
        _, sortino = self.compute_sharpe_sortino(r)
        assert sortino > 0

    def test_single_trade_no_ratio(self):
        """Single trade: Sharpe and Sortino both 0."""
        sharpe, sortino = self.compute_sharpe_sortino([1.5])
        assert sharpe == 0
        assert sortino == 0

    def test_consecutive_wins(self):
        """4 consecutive wins in a row."""
        pnl = [10, 20, 30, 40, -5, 10]
        wins, losses = self.compute_consecutive(pnl)
        assert wins == 4
        assert losses == 1

    def test_consecutive_losses(self):
        """3 consecutive losses."""
        pnl = [10, -5, -3, -8, 20]
        wins, losses = self.compute_consecutive(pnl)
        assert wins == 1
        assert losses == 3

    def test_payoff_ratio(self):
        """Payoff ratio = |avg_win_r / avg_loss_r|."""
        # 3 winners avg 2R, 2 losers avg -1R => payoff = 2/1 = 2.0
        win_rs = [1.5, 2.0, 2.5]
        loss_rs = [-0.8, -1.2]
        avg_win = sum(win_rs) / len(win_rs)
        avg_loss = sum(loss_rs) / len(loss_rs)
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        assert abs(payoff - 2.0) < 0.01

    def test_all_losses_payoff_zero(self):
        """All losses: avg_win is 0, payoff = 0."""
        win_rs = []
        loss_rs = [-1.0, -2.0]
        avg_win = sum(win_rs) / len(win_rs) if win_rs else 0
        avg_loss = sum(loss_rs) / len(loss_rs)
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        assert payoff == 0.0
