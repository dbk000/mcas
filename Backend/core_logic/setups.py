"""
Compatibility re-export — maps old flat `setups/` package to governed `core_logic/` modules.

This shim exists so that `from core_logic.setups import X` works without
changing every import site in signal_engine.py, run_executor.py, etc.
"""

from core_logic.s9_short_reversion import S9Evaluator, S9State, S9Signal
from core_logic.s10_macd_scalp import S10Evaluator, S10State, S10Signal
from core_logic.u1_unicorn_ignition import U1Evaluator, U1State, U1Signal, U1Phase, U1Config
from core_logic.d1_dormancy_ignition import D1Evaluator, D1State, D1Signal, D1Phase, D1Config
from core_logic.setup_base import Direction

__all__ = [
    "S9Evaluator", "S9State", "S9Signal",
    "S10Evaluator", "S10State", "S10Signal",
    "U1Evaluator", "U1State", "U1Signal", "U1Phase", "U1Config",
    "D1Evaluator", "D1State", "D1Signal", "D1Phase", "D1Config",
    "Direction",
]
