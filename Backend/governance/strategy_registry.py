from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
import logging

from governance.bootstrap import get_config, strategy_as_api_dict, strategy_active_setup_ids

logger = logging.getLogger(__name__)

@dataclass
class StrategyHandler:
    evaluator: Any
    compute_state: Callable
    evaluate_trigger: Callable
    score_multiplier: float = 1.0
    timeframe_affinity: Optional[List[str]] = None  # TFs this strategy evaluates on (None = all)
    dispatch_mode: str = "per_symbol"  # "per_symbol" or "cross_sectional"
    worker_evaluate: Optional[Callable] = None  # Bound at worker init — callable ref, not string dispatch
    version: str = "v1.0"  # Strategy version for signal/trace versioning
    label: str = ""  # Human-readable display name (from frozen StrategyRegistry)
    supports_config_sync: bool = False  # Whether this strategy participates in hot-reload config sync
    traceable: bool = False  # Whether this strategy participates in trace governance


class RuntimeDispatchTable:
    """
    Runtime dispatch table for strategy evaluator handlers.

    Maps setup_ids to their respective evaluator handlers at worker init.
    The frozen StrategyRegistry in bootstrap stores metadata (labels, status);
    this class stores live evaluator instances for signal dispatch.
    """
    _strategies: Dict[str, StrategyHandler] = {}

    @classmethod
    def register(cls, setup_id: str, handler: StrategyHandler):
        if setup_id in cls._strategies:
            logger.warning(f"Strategy '{setup_id}' is already registered, overwriting.")
        cls._strategies[setup_id] = handler

    @classmethod
    def get_handler(cls, setup_id: str) -> Optional[StrategyHandler]:
        return cls._strategies.get(setup_id)

    @classmethod
    def active_setup_ids(cls) -> List[str]:
        return list(cls._strategies.keys())

    @classmethod
    def get_multiplier(cls, setup_id: str) -> float:
        handler = cls.get_handler(setup_id)
        return handler.score_multiplier if handler else 1.0

    @classmethod
    def strategies_for_timeframe(cls, tf: str) -> List[tuple]:
        """Return (setup_id, handler) pairs whose timeframe_affinity includes tf.

        Strategies with timeframe_affinity=None match all timeframes.
        Cross-sectional strategies (dispatch_mode="cross_sectional") are excluded
        — they run on their own schedule, not per-symbol dispatch.
        """
        result = []
        for setup_id, handler in cls._strategies.items():
            if handler.dispatch_mode == "cross_sectional":
                continue
            if handler.timeframe_affinity is None or tf in handler.timeframe_affinity:
                result.append((setup_id, handler))
        return result

    @classmethod
    def as_api_dict(cls) -> Dict[str, Dict[str, str]]:
        """Return strategy metadata from the frozen StrategyRegistry."""
        return strategy_as_api_dict()


# Backward-compatible alias: existing code that imports StrategyRegistry
# from this module will get the RuntimeDispatchTable.
StrategyRegistry = RuntimeDispatchTable

def register_all_strategies(worker=None):
    """Wire up available strategies from core_logic.

    Args:
        worker: Optional SignalWorker instance. When provided, worker_evaluate
                callables are bound at registration time — one authority, one place.
                No separate binding dict needed.
    """
    from core_logic.u1_unicorn_ignition import U1Evaluator
    from core_logic.d1_dormancy_ignition import D1Evaluator
    from core_logic.s10_macd_scalp import S10Evaluator
    from core_logic.s9_short_reversion import S9Evaluator
    from core_logic.s11_macd_long_reversion import S11Evaluator

    config = get_config()
    worker_config = config.Worker

    # U1 Strategy — evaluates on 5m (GATE_TIMEFRAME)
    u1_eval = U1Evaluator()
    RuntimeDispatchTable.register("U1", StrategyHandler(
        evaluator=u1_eval,
        compute_state=lambda snap, history, prev_state, **kwargs: u1_eval.evaluate_gates(history, prev_state),
        evaluate_trigger=lambda snap, state, sym, tf, **kwargs: u1_eval.evaluate_trigger(kwargs.get("candles", []), state, sym, tf),
        score_multiplier=worker_config.U1_SCORE_MULT,
        timeframe_affinity=["5m"],
        worker_evaluate=worker._evaluate_u1 if worker else None,
        version="U1_v3.0",
        label="Unicorn Ignition v3.0",
    ))

    # D1 Strategy — evaluates on 1h
    d1_eval = D1Evaluator()
    RuntimeDispatchTable.register("D1", StrategyHandler(
        evaluator=d1_eval,
        compute_state=lambda snap, history, prev_state, **kwargs: d1_eval.compute_state(snap, prev_state, kwargs.get("btc_returns")),
        evaluate_trigger=lambda snap, state, sym, tf, **kwargs: d1_eval.evaluate_trigger(snap, state, sym, tf),
        score_multiplier=worker_config.D1_SCORE_MULT,
        timeframe_affinity=["1h"],
        worker_evaluate=worker._evaluate_d1 if worker else None,
        version="D1_v1.2",
        label="Dormancy-to-Ignition v1.2",
        supports_config_sync=True,
    ))

    # S10 Strategy — evaluates on 5m
    s10_eval = S10Evaluator()
    RuntimeDispatchTable.register("S10", StrategyHandler(
        evaluator=s10_eval,
        compute_state=lambda snap, history, prev_state, **kwargs: s10_eval.compute_state(snap),
        evaluate_trigger=lambda snap, state, sym, tf, **kwargs: s10_eval.evaluate_trigger(snap, state, sym, tf),
        score_multiplier=worker_config.S10_SCORE_MULT,
        timeframe_affinity=["5m"],
        worker_evaluate=worker._evaluate_s10 if worker else None,
        version="S10_v1.0",
        label="MACD Scalp v1.0",
    ))

    # S9 Strategy — cross-sectional (runs on timer, not per-symbol dispatch)
    s9_eval = S9Evaluator()
    RuntimeDispatchTable.register("S9", StrategyHandler(
        evaluator=s9_eval,
        compute_state=lambda snap, history, prev_state, **kwargs: None,
        evaluate_trigger=lambda snap, state, sym, tf, **kwargs: None,
        score_multiplier=worker_config.S9_SCORE_MULT,
        dispatch_mode="cross_sectional",
        version="S9_v1.0",
        label="Short Reversion v1.0",
    ))

    # S11 Strategy — MACD Long Reversion on 1h
    s11_eval = S11Evaluator()
    RuntimeDispatchTable.register("S11", StrategyHandler(
        evaluator=s11_eval,
        compute_state=lambda snap, history, prev_state, **kwargs: s11_eval.compute_state(snap),
        evaluate_trigger=lambda snap, state, sym, tf, **kwargs: s11_eval.evaluate_trigger(snap, state, sym, tf),
        score_multiplier=worker_config.S11_SCORE_MULT,
        timeframe_affinity=["1h"],
        worker_evaluate=worker._evaluate_s11 if worker else None,
        version="S11_v1.0",
        label="MACD Long Reversion v1.0",
        traceable=True,
    ))
