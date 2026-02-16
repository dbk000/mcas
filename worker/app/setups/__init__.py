"""
Setup Evaluators
================

Each setup is a self-contained module that:
1. Computes Tier 3 state from indicators
2. Evaluates Tier 4 triggers
3. Returns a Signal if triggered (or None)

Current setups:
- S1: BB Squeeze ON v2 (volatility compression → expansion)
- S2: Golden Cross Anticipation v1.1 (MA convergence → momentum confirmation)
"""

# Add parent directory to path for imports when running in Docker
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from setups.s1_bb_squeeze import S1Evaluator, S1State, S1Signal, Direction
from setups.s2_golden_cross import S2Evaluator, S2State, S2Signal

__all__ = [
    "S1Evaluator",
    "S1State", 
    "S1Signal",
    "S2Evaluator",
    "S2State",
    "S2Signal",
    "Direction",
]
