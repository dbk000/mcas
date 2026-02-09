"""
Setup Evaluators
================

Each setup is a self-contained module that:
1. Computes Tier 3 state from indicators
2. Evaluates Tier 4 triggers
3. Returns a Signal if triggered (or None)

Current setups:
- S1: BB Squeeze ON v2.1 (volatility compression -> expansion)
- S2: Golden Cross Anticipation v1.4 (MA convergence -> momentum confirmation)
- S3: RSI Divergence Reversal v1.0 (divergence -> reversal)
- S4: VWAP Reversion v1.0 (extreme extension -> mean reversion, intraday)
- S5: S/R Breakout v1.0 (horizontal level breakout)
- S6: Trend Continuation Pullback v1.0 (trend pullback -> reclaim)
- S7: Momentum Breakout (Donchian) v1.0 (channel breakout)
- U1: Unicorn Ignition v1.0 (low-volume pennycoin ignition detection)
"""

# Add parent directory to path for imports when running in Docker
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from setups.s1_bb_squeeze import S1Evaluator, S1State, S1Signal, Direction
from setups.s2_golden_cross import S2Evaluator, S2State, S2Signal
from setups.s3_rsi_divergence import S3Evaluator, S3State, S3Signal
from setups.s4_vwap_reversion import S4Evaluator, S4State, S4Signal
from setups.s5_sr_breakout import S5Evaluator, S5State, S5Signal
from setups.s6_trend_pullback import S6Evaluator, S6State, S6Signal
from setups.s7_momentum_breakout import S7Evaluator, S7State, S7Signal
from setups.u1_unicorn_ignition import U1Evaluator, U1State, U1Signal, U1Phase, U1Config

__all__ = [
    "S1Evaluator", "S1State", "S1Signal",
    "S2Evaluator", "S2State", "S2Signal",
    "S3Evaluator", "S3State", "S3Signal",
    "S4Evaluator", "S4State", "S4Signal",
    "S5Evaluator", "S5State", "S5Signal",
    "S6Evaluator", "S6State", "S6Signal",
    "S7Evaluator", "S7State", "S7Signal",
    "U1Evaluator", "U1State", "U1Signal", "U1Phase", "U1Config",
    "Direction",
]
