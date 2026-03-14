"""
Equities Strategy Base
======================
Base class and data structures for equities signal generation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass
class EquitiesSignal:
    """A single equities strategy signal for one symbol."""
    strategy_id: str
    symbol: str
    direction: str        # "LONG" or "SHORT"
    horizon_days: int
    expected_return_bps: float
    confidence: float     # 0-1
    entry_price: float
    reason: str           # human-readable 1-liner
    explain: list[str] = field(default_factory=list)  # 3-5 why items
    features: dict = field(default_factory=dict)       # key feature snapshot


class EquitiesStrategy(ABC):
    """Abstract base for equities strategies."""

    strategy_id: str = ""
    horizon_days: int = 1
    version: str = "v1.0"

    @abstractmethod
    def evaluate(
        self,
        features: dict[str, np.ndarray],
        bar_idx: int,
        symbols: list[str],
        close_prices: np.ndarray,
    ) -> list[EquitiesSignal]:
        """Evaluate at a single bar index. Returns signals for qualifying symbols.

        Args:
            features: dict of feature name -> (T, N) arrays
            bar_idx: index into the T dimension (current bar)
            symbols: list of N symbol names
            close_prices: (T, N) close price matrix
        """
        ...
