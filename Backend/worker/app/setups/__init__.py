"""
Setup Evaluators
================

Each setup is a self-contained module that:
1. Computes Tier 3 state from indicators
2. Evaluates Tier 4 triggers
3. Returns a Signal if triggered (or None)

Active setups:
- S9: Short Reversion v1.0 (cross-sectional short top-decile by ret_4h)
- U1: Unicorn Ignition v1.0 (low-volume pennycoin ignition detection)

Archived (S1-S8): moved to Archive/setups/
"""

# Add parent directory to path for imports when running in Docker
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from setups.u1_unicorn_ignition import U1Evaluator, U1State, U1Signal, U1Phase, U1Config
from setups.s9_short_reversion import S9Evaluator, S9State, S9Signal
from setups.s10_macd_scalp import S10Evaluator, S10State, S10Signal
from setups.d1_dormancy_ignition import D1Evaluator, D1State, D1Signal, D1Phase, D1Config
from setup_base import Direction

__all__ = [
    "S9Evaluator", "S9State", "S9Signal",
    "S10Evaluator", "S10State", "S10Signal",
    "U1Evaluator", "U1State", "U1Signal", "U1Phase", "U1Config",
    "D1Evaluator", "D1State", "D1Signal", "D1Phase", "D1Config",
    "Direction",
]
