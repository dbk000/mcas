from enum import Enum
from typing import List
from indicators import OHLCV

class RegimeClassification(Enum):
    STRONG_UP = "STRONG_UP"
    UP = "UP"
    NEUTRAL = "NEUTRAL"
    DOWN = "DOWN"
    STRONG_DOWN = "STRONG_DOWN"

# Regime thresholds (slope of DXY over 20 trading days)
SLOPE_STRONG_UP = 0.10
SLOPE_UP = 0.03
SLOPE_DOWN = -0.03
SLOPE_STRONG_DOWN = -0.10

def evaluate_dxy_regime(slope: float) -> RegimeClassification:
    """
    Pure mathematical evaluation of DXY regime.
    """
    if slope is None:
        return RegimeClassification.NEUTRAL
    if slope > SLOPE_STRONG_UP:
        return RegimeClassification.STRONG_UP
    elif slope > SLOPE_UP:
        return RegimeClassification.UP
    elif slope < SLOPE_STRONG_DOWN:
        return RegimeClassification.STRONG_DOWN
    elif slope < SLOPE_DOWN:
        return RegimeClassification.DOWN
    else:
        return RegimeClassification.NEUTRAL
