"""
Canon §1.8 + §1.11: Candle Finality Test
=========================================

Automated checks:
- S1 evaluate_trigger() returns None when is_final=False
- S2 evaluate_trigger() returns None when is_final=False
- Both setups accept is_final parameter
- Backtest data layer supports final_only filtering
"""
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from indicators import IndicatorSnapshot, OHLCV


# =============================================================================
# Helpers — Build minimal snapshot/state for trigger evaluation
# =============================================================================

def _make_ohlcv(ts=1700000000000, close=100.0, high=105.0, low=95.0, volume=1000.0):
    return OHLCV(
        timestamp=ts,
        open=98.0,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _make_snapshot(**overrides):
    """Build a minimal IndicatorSnapshot with defaults."""
    defaults = dict(
        ohlcv=[_make_ohlcv()] * 5,
        atr=2.0,
        clv=0.8,
        volume_z=2.0,
        rsi=55.0,
        rsi_sma=50.0,
        rsi_slope=1.0,
        sma_50=98.0,
        sma_200=95.0,
        ema_9=99.0,
        ema_20=98.5,
        ema_21=98.4,
        ema_50=97.0,
        bb_mb=98.5,
        bb_ub=103.0,
        bb_lb=94.0,
        bb_width=0.09,
        kc_mb=98.5,
        kc_ub=101.5,
        kc_lb=95.5,
        sma50_slope_atr=0.5,
        sma200_slope_atr=0.2,
        ma_gap_atr=1.5,
    )
    defaults.update(overrides)
    return IndicatorSnapshot(**defaults)


# =============================================================================
# S1 Candle Finality
# =============================================================================

class TestS1CandleFinality:
    """Canon §1.8: S1 must gate on is_final=True."""

    def test_s1_returns_none_when_not_final(self):
        """S1 evaluate_trigger() must return None when is_final=False."""
        from setups.s1_bb_squeeze import S1Evaluator

        evaluator = S1Evaluator()
        snapshot = _make_snapshot()

        # Build a minimal S1State that would normally trigger
        state = MagicMock()
        state.squeeze_on = True
        state.bb_width_pctile = 5.0
        state.squeeze_age = 10
        state.ripe_at_prev_bar = True
        state.long_allowed = True
        state.short_allowed = True
        state.box_high = 90.0  # Close > box_high → trigger
        state.box_low = 80.0
        state.bias_score = 0.8

        result = evaluator.evaluate_trigger(
            snapshot, state, "BTCUSDT", "1h", is_final=False
        )
        assert result is None, "S1 must return None for forming candles (is_final=False)"

    def test_s1_accepts_is_final_parameter(self):
        """S1 evaluate_trigger() signature includes is_final."""
        from setups.s1_bb_squeeze import S1Evaluator
        import inspect
        sig = inspect.signature(S1Evaluator.evaluate_trigger)
        assert "is_final" in sig.parameters, "S1 evaluate_trigger missing is_final parameter"

    def test_s1_is_final_defaults_true(self):
        """S1 is_final defaults to True for backward compatibility."""
        from setups.s1_bb_squeeze import S1Evaluator
        import inspect
        sig = inspect.signature(S1Evaluator.evaluate_trigger)
        param = sig.parameters["is_final"]
        assert param.default is True, "S1 is_final should default to True"


# =============================================================================
# S2 Candle Finality
# =============================================================================

class TestS2CandleFinality:
    """Canon §1.8: S2 must gate on is_final=True."""

    def test_s2_returns_none_when_not_final(self):
        """S2 evaluate_trigger() must return None when is_final=False."""
        from setups.s2_golden_cross import S2Evaluator

        evaluator = S2Evaluator()
        snapshot = _make_snapshot()

        state = MagicMock()
        state.converging = True
        state.long_allowed = True
        state.short_allowed = True
        state.ma_gap_atr = 1.0
        state.dec_count = 5
        state.was_wide = True
        state.is_narrow = True
        state.gap_decreasing = True
        state.pre_cross_long = True
        state.pre_cross_short = False

        result = evaluator.evaluate_trigger(
            snapshot, state, "BTCUSDT", "1h", is_final=False
        )
        assert result is None, "S2 must return None for forming candles (is_final=False)"

    def test_s2_accepts_is_final_parameter(self):
        """S2 evaluate_trigger() signature includes is_final."""
        from setups.s2_golden_cross import S2Evaluator
        import inspect
        sig = inspect.signature(S2Evaluator.evaluate_trigger)
        assert "is_final" in sig.parameters, "S2 evaluate_trigger missing is_final parameter"

    def test_s2_is_final_defaults_true(self):
        """S2 is_final defaults to True for backward compatibility."""
        from setups.s2_golden_cross import S2Evaluator
        import inspect
        sig = inspect.signature(S2Evaluator.evaluate_trigger)
        param = sig.parameters["is_final"]
        assert param.default is True, "S2 is_final should default to True"


# =============================================================================
# U1 Candle Finality
# =============================================================================

class TestU1CandleFinality:
    """Canon §1.8: U1 must gate on is_final=True."""

    def test_u1_returns_none_when_not_final(self):
        """U1 evaluate_trigger() must return None when is_final=False."""
        from setups.u1_unicorn_ignition import U1Evaluator, U1State, U1Phase

        evaluator = U1Evaluator()
        # Build minimal candles
        candles = [_make_ohlcv()] * 100

        state = U1State()
        state.phase = U1Phase.EMBRYO_ACTIVE

        result = evaluator.evaluate_trigger(
            candles, state, "TESTUSDT", "5m", is_final=False
        )
        assert result is None, "U1 must return None for forming candles (is_final=False)"

    def test_u1_accepts_is_final_parameter(self):
        """U1 evaluate_trigger() signature includes is_final."""
        from setups.u1_unicorn_ignition import U1Evaluator
        import inspect
        sig = inspect.signature(U1Evaluator.evaluate_trigger)
        assert "is_final" in sig.parameters, "U1 evaluate_trigger missing is_final parameter"

    def test_u1_is_final_defaults_true(self):
        """U1 is_final defaults to True for backward compatibility."""
        from setups.u1_unicorn_ignition import U1Evaluator
        import inspect
        sig = inspect.signature(U1Evaluator.evaluate_trigger)
        param = sig.parameters["is_final"]
        assert param.default is True, "U1 is_final should default to True"

    def test_u1_evaluate_gates_accepts_is_final(self):
        """U1 evaluate_gates() signature includes is_final."""
        from setups.u1_unicorn_ignition import U1Evaluator
        import inspect
        sig = inspect.signature(U1Evaluator.evaluate_gates)
        assert "is_final" in sig.parameters, "U1 evaluate_gates missing is_final parameter"


# =============================================================================
# Data Layer Finality Support
# =============================================================================

class TestDataLayerFinality:
    """Canon §1.8: Data layer must support final_only filtering."""

    def test_get_bars_has_final_only_param(self):
        """OHLCVRepository.get_bars() must accept final_only parameter."""
        psycopg = pytest.importorskip("psycopg", reason="psycopg not installed (CI only)")
        from data_layer import OHLCVRepository
        import inspect
        sig = inspect.signature(OHLCVRepository.get_bars)
        assert "final_only" in sig.parameters, "get_bars missing final_only parameter"

    def test_ohlcv_dataclass_has_is_final(self):
        """OHLCV dataclass must include is_final field."""
        psycopg = pytest.importorskip("psycopg", reason="psycopg not installed (CI only)")
        from data_layer import OHLCV as DL_OHLCV
        import dataclasses
        field_names = [f.name for f in dataclasses.fields(DL_OHLCV)]
        assert "is_final" in field_names, "data_layer.OHLCV missing is_final field"
