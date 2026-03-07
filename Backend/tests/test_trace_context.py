"""Tests for TraceContext — computation lineage sealed audit trail."""

import hashlib
import json
import os
import sys

import pytest

# Allow imports from worker/app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))

from trace_context import TraceContext, TRACE_SCHEMA_VERSION, TRACE_ENABLED, _deterministic_json


def _make_trace(**kwargs):
    """Create a TraceContext with sane defaults for testing."""
    defaults = dict(strategy="D1", symbol="XYZUSDT", timeframe="1h")
    defaults.update(kwargs)
    return TraceContext(**defaults)


def _seal_basic_trace():
    """Build a fully populated trace and seal it."""
    t = _make_trace()
    t.record_candle_window("2024-01-01T00:00:00Z", "2024-03-01T08:00:00Z", 201, "1h")
    t.record_indicator_params({"n_atr": 14, "n_bb": 20})
    t.record_indicators_bulk({"dormancy_score": 0.85, "bb_width": 0.009})
    t.record_state({"phase": "ACCUMULATING", "bar_index": 201}, is_hydrated=False)
    t.record_gate("dormancy", 0.85, 0.75, ">=", True)
    t.record_gate("compression", 0.009, 0.02, "<", True)
    t.record_score({"dormancy": 0.17, "vol_z": 0.12}, raw=0.67,
                   calibration_mult=0.75, calibrated=0.50,
                   promotion_threshold=0.30)
    t.record_config({"DORMANCY_THRESHOLD": 0.75, "STOP_PCT": 0.015})
    return t.seal("D1_v1.2")


class TestTraceContextUnit:
    def test_seal_produces_valid_schema(self):
        trace = _seal_basic_trace()
        required_keys = {
            "v", "strategy", "symbol", "timeframe", "eval_ts",
            "candle_window", "indicator_snapshot", "indicator_params",
            "indicator_params_hash", "state", "gates", "score",
            "config_hash", "config_snapshot", "promotion", "trace_hash",
        }
        assert required_keys.issubset(trace.keys())

    def test_schema_version_constant(self):
        trace = _seal_basic_trace()
        assert trace["v"] == "1.0"
        assert trace["v"] == TRACE_SCHEMA_VERSION

    def test_gate_recording_order(self):
        t = _make_trace()
        t.record_gate("alpha", 1, 0, ">", True)
        t.record_gate("beta", 2, 1, ">", True)
        t.record_gate("gamma", 3, 2, ">", True)
        t.record_config({})
        trace = t.seal("test_v1")
        gate_names = [g["gate"] for g in trace["gates"]]
        assert gate_names == ["alpha", "beta", "gamma"]

    def test_config_hash_deterministic(self):
        cfg = {"B_KEY": 2, "A_KEY": 1}
        t1 = _make_trace()
        t1.record_config(cfg)
        t2 = _make_trace()
        t2.record_config(dict(reversed(list(cfg.items()))))
        # Same config regardless of insertion order
        t1_sealed = t1.seal("v1")
        t2_sealed = t2.seal("v1")
        assert t1_sealed["config_hash"] == t2_sealed["config_hash"]

    def test_config_hash_length(self):
        trace = _seal_basic_trace()
        assert len(trace["config_hash"]) == 16
        # Must be valid hex
        int(trace["config_hash"], 16)

    def test_trace_hash_integrity(self):
        trace = _seal_basic_trace()
        stored_hash = trace.pop("trace_hash")
        recomputed = hashlib.sha256(
            _deterministic_json(trace).encode('utf-8')
        ).hexdigest()
        assert stored_hash == recomputed

    def test_trace_hash_detects_tampering(self):
        trace = _seal_basic_trace()
        stored_hash = trace["trace_hash"]
        # Tamper with a field
        trace["score"]["raw"] = 0.99
        # Remove hash, recompute
        tampered = dict(trace)
        tampered.pop("trace_hash")
        recomputed = hashlib.sha256(
            _deterministic_json(tampered).encode('utf-8')
        ).hexdigest()
        assert stored_hash != recomputed

    def test_indicator_params_hash_same(self):
        t1 = _make_trace()
        t1.record_indicator_params({"n_atr": 14, "n_bb": 20})
        t1.record_config({})
        s1 = t1.seal("v1")

        t2 = _make_trace()
        t2.record_indicator_params({"n_bb": 20, "n_atr": 14})  # different order
        t2.record_config({})
        s2 = t2.seal("v1")

        assert s1["indicator_params_hash"] == s2["indicator_params_hash"]

    def test_indicator_params_hash_different(self):
        t1 = _make_trace()
        t1.record_indicator_params({"n_atr": 14})
        t1.record_config({})
        s1 = t1.seal("v1")

        t2 = _make_trace()
        t2.record_indicator_params({"n_atr": 20})
        t2.record_config({})
        s2 = t2.seal("v1")

        assert s1["indicator_params_hash"] != s2["indicator_params_hash"]

    def test_deterministic_json_ordering(self):
        d1 = {"z": 1, "a": 2, "m": 3}
        d2 = {"a": 2, "m": 3, "z": 1}
        assert _deterministic_json(d1) == _deterministic_json(d2)

    def test_deterministic_json_no_whitespace(self):
        result = _deterministic_json({"a": 1, "b": 2})
        assert " " not in result
        assert "\n" not in result

    def test_candle_window_multi_timeframe(self):
        t = _make_trace()
        t.record_candle_window("t0", "t1", 5, "1h")
        t.record_candle_window("t2", "t3", 300, "1s", note="microstructure tape")
        t.record_config({})
        trace = t.seal("v1")
        assert len(trace["candle_window"]) == 2
        assert trace["candle_window"][0]["timeframe"] == "1h"
        assert trace["candle_window"][1]["timeframe"] == "1s"
        assert trace["candle_window"][1]["note"] == "microstructure tape"

    def test_state_is_hydrated_flag(self):
        t = _make_trace()
        t.record_state({"phase": "X"}, is_hydrated=True)
        t.record_config({})
        trace = t.seal("v1")
        assert trace["state"]["is_hydrated"] is True

        t2 = _make_trace()
        t2.record_state({"phase": "Y"}, is_hydrated=False)
        t2.record_config({})
        trace2 = t2.seal("v1")
        assert trace2["state"]["is_hydrated"] is False

    def test_promotion_threshold_in_score(self):
        trace = _seal_basic_trace()
        assert "promotion_threshold" in trace["score"]
        assert trace["score"]["promotion_threshold"] == 0.30

    def test_cross_sectional_context(self):
        t = _make_trace(strategy="S9")
        t.record_cross_sectional_context({
            "universe_size": 284,
            "breadth": 0.45,
            "breadth_threshold": 0.30,
        })
        t.record_config({})
        trace = t.seal("S9_v1.0")
        assert "cross_sectional_context" in trace
        assert trace["cross_sectional_context"]["universe_size"] == 284

    def test_cross_sectional_context_absent_when_empty(self):
        trace = _seal_basic_trace()
        assert "cross_sectional_context" not in trace

    def test_microstructure_block(self):
        t = _make_trace(strategy="S9")
        t.record_microstructure({"n_klines": 300, "entry_type": "limit"})
        t.record_config({})
        trace = t.seal("S9_v1.0")
        assert "microstructure" in trace
        assert trace["microstructure"]["n_klines"] == 300

    def test_promotion_fields(self):
        trace = _seal_basic_trace()
        assert "promotion" in trace
        assert trace["promotion"]["setup_version"] == "D1_v1.2"
        assert "sealed_at" in trace["promotion"]
        assert isinstance(trace["promotion"]["sealed_at"], float)

    def test_strategy_and_symbol_recorded(self):
        trace = _seal_basic_trace()
        assert trace["strategy"] == "D1"
        assert trace["symbol"] == "XYZUSDT"
        assert trace["timeframe"] == "1h"

    def test_indicator_params_hash_empty_when_no_params(self):
        t = _make_trace()
        t.record_config({})
        trace = t.seal("v1")
        assert trace["indicator_params_hash"] == ""

    def test_record_state_does_not_mutate_input(self):
        original = {"phase": "SCANNING", "bar_index": 100}
        original_copy = dict(original)
        t = _make_trace()
        t.record_state(original, is_hydrated=True)
        # Original dict should not be mutated
        assert original == original_copy

    def test_score_rounding(self):
        t = _make_trace()
        t.record_score({"a": 0.1}, raw=0.123456789, calibration_mult=0.7777777,
                       calibrated=0.098765432, promotion_threshold=0.12345)
        t.record_config({})
        trace = t.seal("v1")
        assert trace["score"]["raw"] == 0.123457  # 6 decimal places
        assert trace["score"]["calibration_mult"] == 0.7778  # 4 decimal places
        assert trace["score"]["calibrated"] == 0.098765  # 6 decimal places
        assert trace["score"]["promotion_threshold"] == 0.1235  # 4 decimal places
