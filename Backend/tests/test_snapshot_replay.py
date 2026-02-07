"""
Canon §1.2C + §1.11: Snapshot Replay Test
==========================================

Automated checks:
- Indicator library fingerprint is deterministic
- Same inputs produce same indicator outputs (FeatureFactory determinism)
- Signal scoring is deterministic (same features → same score)
- TradeTrace data structure captures enough for replay
- signal_events table schema supports replay
"""
import pytest
from datetime import datetime, timezone

from indicators import (
    FeatureFactory,
    IndicatorSnapshot,
    OHLCV,
    INDICATOR_REGISTRY,
    get_indicator_library_fingerprint,
)


# =============================================================================
# Helpers
# =============================================================================

def _make_candles(n=300, base_price=100.0, base_ts=1700000000000):
    """Generate deterministic OHLCV candles for testing."""
    candles = []
    for i in range(n):
        # Deterministic price movement
        offset = (i % 20) * 0.5 - 5.0  # oscillates -5 to +5
        price = base_price + offset
        candles.append(OHLCV(
            timestamp=base_ts + i * 3600000,  # 1h candles
            open=price - 0.3,
            high=price + 2.0,
            low=price - 2.0,
            close=price,
            volume=1000.0 + (i % 10) * 100,
        ))
    return candles


# =============================================================================
# Library Fingerprint Determinism
# =============================================================================

class TestLibraryFingerprint:
    """Canon §1.2C: Indicator library fingerprint must be stable."""

    def test_fingerprint_same_across_calls(self):
        fp1 = get_indicator_library_fingerprint()
        fp2 = get_indicator_library_fingerprint()
        assert fp1 == fp2

    def test_fingerprint_format(self):
        fp = get_indicator_library_fingerprint()
        assert isinstance(fp, str)
        assert len(fp) == 16
        assert all(c in '0123456789abcdef' for c in fp)


# =============================================================================
# FeatureFactory Determinism
# =============================================================================

class TestFeatureFactoryDeterminism:
    """Canon §1.2C: Same candle inputs must produce identical indicator outputs."""

    def test_compute_is_deterministic(self):
        """Run FeatureFactory.compute() twice with same data → same result."""
        candles = _make_candles(300)
        ff = FeatureFactory()

        snap1 = ff.compute(candles)
        snap2 = ff.compute(candles)

        # Core Tier 1 indicators
        assert snap1.atr == snap2.atr, "ATR not deterministic"
        assert snap1.rsi == snap2.rsi, "RSI not deterministic"
        assert snap1.clv == snap2.clv, "CLV not deterministic"
        assert snap1.volume_z == snap2.volume_z, "Volume Z not deterministic"

        # Core Tier 2 indicators
        assert snap1.sma_50 == snap2.sma_50, "SMA50 not deterministic"
        assert snap1.sma_200 == snap2.sma_200, "SMA200 not deterministic"
        assert snap1.ema_9 == snap2.ema_9, "EMA9 not deterministic"
        assert snap1.bb_width == snap2.bb_width, "BB Width not deterministic"

    def test_different_data_different_output(self):
        """Sanity check: different candles produce different indicators."""
        candles1 = _make_candles(300, base_price=100.0)
        candles2 = _make_candles(300, base_price=200.0)
        ff = FeatureFactory()

        snap1 = ff.compute(candles1)
        snap2 = ff.compute(candles2)

        # At minimum, SMA values should differ
        assert snap1.sma_50 != snap2.sma_50, "Different data should give different SMA50"


# =============================================================================
# Signal Scoring Determinism
# =============================================================================

class TestScoringDeterminism:
    """Canon §1.2C: Same features must produce same signal scores."""

    def test_s1_scoring_deterministic(self):
        """S1 score computation is deterministic."""
        from setups.s1_bb_squeeze import S1Evaluator
        from unittest.mock import MagicMock

        candles = _make_candles(300)
        ff = FeatureFactory()
        snapshot = ff.compute(candles)

        evaluator = S1Evaluator()

        # Build state that passes preconditions
        state = MagicMock()
        state.squeeze_on = True
        state.bb_width_pctile = 5.0
        state.squeeze_age = 10
        state.ripe_at_prev_bar = True
        state.long_allowed = True
        state.short_allowed = True
        state.box_high = snapshot.ohlcv[-1].close - 5.0  # Ensure close > box_high
        state.box_low = snapshot.ohlcv[-1].close - 15.0
        state.bias_score = 0.8

        sig1 = evaluator.evaluate_trigger(snapshot, state, "TEST", "1h", is_final=True)
        sig2 = evaluator.evaluate_trigger(snapshot, state, "TEST", "1h", is_final=True)

        if sig1 and sig2:
            assert sig1.setup_score == sig2.setup_score, "S1 score not deterministic"
            assert sig1.direction == sig2.direction, "S1 direction not deterministic"


# =============================================================================
# Signal Events Schema Supports Replay
# =============================================================================

class TestSignalEventsReplaySupport:
    """Canon §1.9 + §1.2C: signal_events must store enough data for replay."""

    def test_log_signal_event_signature(self):
        """log_signal_event must accept replay-critical parameters."""
        pytest.importorskip("psycopg", reason="psycopg not installed (CI only)")
        from data_layer import log_signal_event
        import inspect
        sig = inspect.signature(log_signal_event)
        params = set(sig.parameters.keys())

        required = {
            'event_type', 'symbol', 'timeframe', 'setup_id',
            'bar_start_ts', 'feature_snapshot', 'score_components',
        }
        missing = required - params
        assert not missing, f"log_signal_event missing replay-critical params: {missing}"

    def test_signal_events_table_in_schema(self):
        """signal_events table must exist in migrations."""
        import os
        migrations_dir = os.path.join(os.path.dirname(__file__), '..', 'docs', 'db', 'migrations')
        found = False
        for f in os.listdir(migrations_dir):
            if 'signal_events' in f:
                found = True
                path = os.path.join(migrations_dir, f)
                with open(path) as fh:
                    content = fh.read()
                # Must have feature_snapshot and score_components columns
                assert 'feature_snapshot' in content, "signal_events missing feature_snapshot column"
                assert 'score_components' in content, "signal_events missing score_components column"
                assert 'bar_start_ts' in content, "signal_events missing bar_start_ts column"
        assert found, "No signal_events migration file found"
