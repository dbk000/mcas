"""
Computation Trace / Lineage — sealed audit trail for every opportunity.

Every promoted opportunity gets a TraceContext that records the full
computation lineage: candle windows, indicator values, gate evaluations,
score components, config snapshot, and a SHA-256 integrity hash.

Tracing is mandatory for all traceable strategies — there is no TRACE_ENABLED
toggle. Schema version is governed by TraceTypedConfig in the config registry.
"""

import time
import hashlib
import json
from dataclasses import dataclass, field
import contextvars

from governance.bootstrap import get_trace_config

# Backward compatibility — signal_worker.py still imports this symbol.
# Will be removed once signal_worker is updated to drop the conditional.
TRACE_ENABLED = True

def _deterministic_json(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(',', ':'))

@dataclass
class TraceContext:
    strategy: str = ""
    symbol: str = ""
    timeframe: str = ""
    eval_ts: float = field(default_factory=time.time)

    _candle_windows: list = field(default_factory=list)
    _indicators: dict = field(default_factory=dict)
    _indicator_params: dict = field(default_factory=dict)
    _state: dict = field(default_factory=dict)
    _gates: list = field(default_factory=list)
    _score: dict = field(default_factory=dict)
    _config: dict = field(default_factory=dict)
    _config_hash: str = ""
    _cross_sectional_context: dict = field(default_factory=dict)
    _microstructure: dict = field(default_factory=dict)
    _promotion: dict = field(default_factory=dict)
    _sealed: bool = False

    def record_candle_window(self, first_ts, last_ts, bar_count: int,
                             timeframe: str, note: str = None):
        entry = {
            "timeframe": timeframe,
            "first_ts": str(first_ts),
            "last_ts": str(last_ts),
            "bar_count": bar_count,
        }
        if note:
            entry["note"] = note
        self._candle_windows.append(entry)

    def record_indicator(self, name: str, value):
        self._indicators[name] = value

    def record_indicators_bulk(self, snapshot_dict: dict):
        self._indicators.update(snapshot_dict)

    def record_indicator_params(self, params: dict):
        self._indicator_params = params

    def record_state(self, state_dict: dict, is_hydrated: bool = False):
        self._state = dict(state_dict)
        self._state["is_hydrated"] = is_hydrated

    def record_gate(self, gate: str, value, threshold, op: str,
                    passed: bool, note: str = None):
        entry = {"gate": gate, "value": value, "threshold": threshold,
                 "op": op, "passed": passed}
        if note:
            entry["note"] = note
        self._gates.append(entry)

    def record_score(self, components: dict, raw: float,
                     calibration_mult: float, calibrated: float,
                     promotion_threshold: float = 0.0):
        self._score = {
            "components": components,
            "raw": round(raw, 6),
            "calibration_mult": round(calibration_mult, 4),
            "calibrated": round(calibrated, 6),
            "promotion_threshold": round(promotion_threshold, 4),
        }

    def record_config(self, config_dict: dict):
        self._config = config_dict
        raw = _deterministic_json(config_dict)
        self._config_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]

    def record_cross_sectional_context(self, ctx: dict):
        self._cross_sectional_context = ctx

    def record_microstructure(self, micro: dict):
        self._microstructure = micro

    def seal(self, setup_version: str) -> dict:
        self._sealed = True
        self._promotion = {
            "setup_version": setup_version,
            "sealed_at": time.time(),
        }

        # ── Structural validation (before hash) ──────────────────────────
        # These are STRUCTURAL mandatory fields — hardcoded, not configurable.
        # A trace without these is structurally invalid and must not be sealed.
        _MANDATORY = {
            "eval_ts": self.eval_ts,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }
        missing = [k for k, v in _MANDATORY.items() if not v]
        if not self._promotion.get("sealed_at"):
            missing.append("promotion.sealed_at")
        if missing:
            raise RuntimeError(
                f"TraceContext.seal() structural validation failed — "
                f"missing or empty: {', '.join(sorted(missing))}"
            )

        indicator_params_hash = ""
        if self._indicator_params:
            raw = _deterministic_json(self._indicator_params)
            indicator_params_hash = hashlib.sha256(
                raw.encode('utf-8')).hexdigest()[:16]

        trace = {
            "v": get_trace_config().schema_version,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "eval_ts": self.eval_ts,
            "candle_window": self._candle_windows,
            "indicator_snapshot": self._indicators,
            "indicator_params": self._indicator_params,
            "indicator_params_hash": indicator_params_hash,
            "state": self._state,
            "gates": self._gates,
            "score": self._score,
            "config_hash": self._config_hash,
            "config_snapshot": self._config,
        }

        if self._cross_sectional_context:
            trace["cross_sectional_context"] = self._cross_sectional_context
        if self._microstructure:
            trace["microstructure"] = self._microstructure

        trace["promotion"] = self._promotion

        trace_string = _deterministic_json(trace)
        trace["trace_hash"] = hashlib.sha256(trace_string.encode('utf-8')).hexdigest()

        return trace
