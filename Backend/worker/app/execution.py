"""
Execution Models
================

Explicit, locked-down models for trade execution simulation.

> "Trading starts when nothing important is implicit anymore."

These models determine how backtests and paper trades are simulated.
Every assumption is named, parameterized, and documented.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from abc import ABC, abstractmethod


# =============================================================================
# Timeframe-Scaled Slippage Profile (CLAUDE.md §4.4)
# =============================================================================
# Slippage scales with timeframe: shorter bars = thinner books at that instant
# = more market impact. These are empirical estimates for liquid crypto pairs.
SLIPPAGE_PROFILE_BPS: dict[str, float] = {
    "4h": 3.0,
    "1h": 5.0,
    "15m": 10.0,
    "5m": 15.0,
    "1d": 2.0,   # Daily is most liquid
    "1m": 20.0,  # 1-minute is most impactful
}
DEFAULT_SLIPPAGE_BPS = 5.0  # Fallback for unlisted timeframes

# Multi-symbol strategies (XSM-type) get an additional penalty because
# cross-symbol execution requires sweeping multiple order books.
XSM_SLIPPAGE_PENALTY_BPS = 5.0


class EntryMode(Enum):
    """How entries are executed."""
    NEXT_OPEN = "NEXT_OPEN"      # Enter at next bar's open
    BAR_CLOSE = "BAR_CLOSE"      # Enter at signal bar's close (optimistic)
    LIMIT_RETEST = "LIMIT_RETEST"  # Enter on retest of trigger level (not implemented)
    MOC = "MOC"                  # Market on close


class FillAssumption(Enum):
    """When we assume fills occur."""
    ALWAYS = "ALWAYS"            # Always get filled at target price
    TOUCH = "TOUCH"              # Fill if price touches our level
    THROUGH = "THROUGH"          # Fill only if price goes through our level
    VOLUME_WEIGHTED = "VOLUME_WEIGHTED"  # Partial fills based on volume (not implemented)


# =============================================================================
# Slippage Models
# =============================================================================

class SlippageModel(ABC):
    """Base class for slippage models."""

    @abstractmethod
    def calculate(
        self,
        price: float,
        side: str,
        volume: float = 0,
        is_entry: bool = True,
        atr: float = 0,
    ) -> float:
        """
        Calculate slippage amount.

        Args:
            price: Intended execution price
            side: "long" or "short"
            volume: Trade volume (for volume-based models)
            is_entry: True for entry, False for exit (for asymmetric models)
            atr: ATR value (for volatility-based models)

        Returns:
            Slippage amount (always positive, applied against the trader)
        """
        pass

    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize for fingerprinting."""
        pass


@dataclass
class FixedBpsSlippage(SlippageModel):
    """
    Fixed basis points slippage.

    Most common model. Assumes constant market impact regardless of size.
    Reasonable for liquid crypto pairs with moderate position sizes.
    """
    entry_bps: float = 5.0  # 0.05%
    exit_bps: float = 5.0   # 0.05%

    def calculate(
        self,
        price: float,
        side: str,
        volume: float = 0,
        is_entry: bool = True,
        atr: float = 0,
    ) -> float:
        bps = self.entry_bps if is_entry else self.exit_bps
        return price * (bps / 10000)

    def to_dict(self) -> dict:
        return {
            "model": "FIXED_BPS",
            "entry_bps": self.entry_bps,
            "exit_bps": self.exit_bps,
        }


@dataclass
class TimeframeScaledSlippage(SlippageModel):
    """
    Slippage that scales by timeframe per CLAUDE.md §4.4.

    Shorter timeframes have higher slippage because the order book is thinner
    at any given instant, and the market impact of a taker sweep is larger
    relative to the bar's price range.

    Optionally adds +5 bps for multi-symbol strategies (XSM-type).
    """
    timeframe: str = "1h"
    is_xsm: bool = False  # Multi-symbol strategy penalty
    profile: dict[str, float] = field(default_factory=lambda: dict(SLIPPAGE_PROFILE_BPS))

    @property
    def _effective_bps(self) -> float:
        base = self.profile.get(self.timeframe, DEFAULT_SLIPPAGE_BPS)
        if self.is_xsm:
            base += XSM_SLIPPAGE_PENALTY_BPS
        return base

    def calculate(
        self,
        price: float,
        side: str,
        volume: float = 0,
        is_entry: bool = True,
        atr: float = 0,
    ) -> float:
        return price * (self._effective_bps / 10000)

    def to_dict(self) -> dict:
        return {
            "model": "TIMEFRAME_SCALED",
            "timeframe": self.timeframe,
            "effective_bps": self._effective_bps,
            "is_xsm": self.is_xsm,
        }


def get_slippage_bps_for_timeframe(
    timeframe: str,
    is_xsm: bool = False,
    profile: Optional[dict[str, float]] = None,
) -> float:
    """
    Utility: look up slippage bps for a timeframe string.

    Useful for callers that need the raw number (e.g. sim_d1_matrix.py)
    without constructing a full SlippageModel instance.
    """
    p = profile or SLIPPAGE_PROFILE_BPS
    bps = p.get(timeframe, DEFAULT_SLIPPAGE_BPS)
    if is_xsm:
        bps += XSM_SLIPPAGE_PENALTY_BPS
    return bps


@dataclass
class VolatilitySlippage(SlippageModel):
    """
    Slippage as a fraction of ATR.

    More realistic for volatile conditions.
    Slippage increases when volatility is high.
    """
    atr_fraction: float = 0.1  # 10% of ATR
    min_bps: float = 2.0       # Floor
    max_bps: float = 20.0      # Cap

    def calculate(
        self,
        price: float,
        side: str,
        volume: float = 0,
        is_entry: bool = True,
        atr: float = 0,
    ) -> float:
        if atr <= 0:
            # Fallback to min
            return price * (self.min_bps / 10000)

        slippage = atr * self.atr_fraction
        slippage_bps = (slippage / price) * 10000
        slippage_bps = max(self.min_bps, min(self.max_bps, slippage_bps))
        return price * (slippage_bps / 10000)

    def to_dict(self) -> dict:
        return {
            "model": "VOLATILITY",
            "atr_fraction": self.atr_fraction,
            "min_bps": self.min_bps,
            "max_bps": self.max_bps,
        }


@dataclass
class ZeroSlippage(SlippageModel):
    """No slippage. For testing only."""

    def calculate(
        self,
        price: float,
        side: str,
        volume: float = 0,
        is_entry: bool = True,
        atr: float = 0,
    ) -> float:
        return 0.0

    def to_dict(self) -> dict:
        return {"model": "ZERO"}


# =============================================================================
# Fee Models
# =============================================================================

class FeeModel(ABC):
    """Base class for fee models."""
    
    @abstractmethod
    def calculate(self, notional: float, is_maker: bool = False) -> float:
        """
        Calculate fee amount.
        
        Args:
            notional: Trade notional value
            is_maker: Whether this is a maker order
        
        Returns:
            Fee amount
        """
        pass
    
    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize for fingerprinting."""
        pass


@dataclass
class TieredFees(FeeModel):
    """
    Tiered maker/taker fees.
    
    Standard exchange fee structure.
    """
    maker_bps: float = 2.0   # 0.02%
    taker_bps: float = 4.0   # 0.04%
    
    def calculate(self, notional: float, is_maker: bool = False) -> float:
        bps = self.maker_bps if is_maker else self.taker_bps
        return notional * (bps / 10000)
    
    def to_dict(self) -> dict:
        return {
            "model": "TIERED",
            "maker_bps": self.maker_bps,
            "taker_bps": self.taker_bps,
        }


@dataclass 
class FlatFees(FeeModel):
    """
    Flat fee regardless of maker/taker.
    
    Simpler model for quick estimates.
    """
    fee_bps: float = 10.0  # 0.10% round-trip assumption
    
    def calculate(self, notional: float, is_maker: bool = False) -> float:
        return notional * (self.fee_bps / 10000)
    
    def to_dict(self) -> dict:
        return {
            "model": "FLAT",
            "fee_bps": self.fee_bps,
        }


@dataclass
class ZeroFees(FeeModel):
    """No fees. For testing only."""
    
    def calculate(self, notional: float, is_maker: bool = False) -> float:
        return 0.0
    
    def to_dict(self) -> dict:
        return {"model": "ZERO"}


# =============================================================================
# Fill Models
# =============================================================================

@dataclass
class FillModel:
    """
    Defines how and when orders get filled.
    
    This is critical for backtest realism.
    """
    # Entry assumptions
    entry_mode: EntryMode = EntryMode.NEXT_OPEN
    
    # Stop/target fill assumptions
    stop_fill: FillAssumption = FillAssumption.TOUCH
    target_fill: FillAssumption = FillAssumption.TOUCH
    
    # Gap handling
    gap_fill_at_open: bool = True  # If price gaps through stop, fill at open

    # Same-bar stop/TP conflict policy (deterministic intrabar assumption)
    intrabar_policy: str = "STOP_FIRST"  # STOP_FIRST, TP_FIRST, OHLC_HEURISTIC
    one_event_per_bar: bool = True
    
    # Partial fills (not implemented yet)
    allow_partial: bool = False
    min_fill_pct: float = 1.0  # 100% = no partial
    
    def to_dict(self) -> dict:
        return {
            "entry_mode": self.entry_mode.value,
            "stop_fill": self.stop_fill.value,
            "target_fill": self.target_fill.value,
            "gap_fill_at_open": self.gap_fill_at_open,
            "intrabar_policy": self.intrabar_policy,
            "one_event_per_bar": self.one_event_per_bar,
            "allow_partial": self.allow_partial,
            "min_fill_pct": self.min_fill_pct,
        }


# =============================================================================
# Complete Execution Spec
# =============================================================================

@dataclass
class ExecutionConfig:
    """
    Complete execution configuration.
    
    This is what goes into the Run fingerprint.
    Every assumption is explicit and named.
    """
    # Models
    slippage: SlippageModel = field(default_factory=lambda: FixedBpsSlippage())
    fees: FeeModel = field(default_factory=lambda: TieredFees())
    fills: FillModel = field(default_factory=FillModel)
    
    # Position constraints
    max_positions: int = 3
    one_per_asset: bool = True
    max_exposure_pct: float = 1.0  # 100% of capital max
    
    # Risk constraints
    max_risk_per_trade_pct: float = 1.0  # 1% risk per trade
    max_daily_loss_pct: float = 5.0  # 5% max daily loss
    
    def to_dict(self) -> dict:
        """Serialize for fingerprinting and storage."""
        return {
            "slippage": self.slippage.to_dict(),
            "fees": self.fees.to_dict(),
            "fills": self.fills.to_dict(),
            "max_positions": self.max_positions,
            "one_per_asset": self.one_per_asset,
            "max_exposure_pct": self.max_exposure_pct,
            "max_risk_per_trade_pct": self.max_risk_per_trade_pct,
            "max_daily_loss_pct": self.max_daily_loss_pct,
        }
    
    def apply_entry_slippage(self, price: float, side: str) -> float:
        """Apply slippage to entry price (adverse to trader)."""
        slip = self.slippage.calculate(price, side, is_entry=True)
        if side == "long":
            return price + slip  # Pay more to buy
        else:
            return price - slip  # Receive less to sell
    
    def apply_exit_slippage(self, price: float, side: str) -> float:
        """Apply slippage to exit price (adverse to trader)."""
        slip = self.slippage.calculate(price, side, is_entry=False)
        if side == "long":
            return price - slip  # Receive less when selling
        else:
            return price + slip  # Pay more when covering
    
    def calculate_fees(self, notional: float, is_maker: bool = False) -> float:
        """Calculate fees for a trade."""
        return self.fees.calculate(notional, is_maker)


# =============================================================================
# Preset Configurations
# =============================================================================

def realistic_config(
    timeframe: Optional[str] = None,
    is_xsm: bool = False,
) -> ExecutionConfig:
    """
    Realistic execution config for production backtests.

    Args:
        timeframe: If provided, uses TimeframeScaledSlippage for that
                   timeframe (CLAUDE.md §4.4). If None, falls back to
                   flat 5 bps (backwards compatible).
        is_xsm:   If True, adds +5 bps XSM penalty (CLAUDE.md §4.4).

    Assumes:
    - Timeframe-scaled slippage (or flat 5 bps if no timeframe given)
    - Standard Binance fees (0.02% maker, 0.04% taker)
    - Entry at next bar open
    - Stops fill on touch
    """
    if timeframe is not None:
        slippage = TimeframeScaledSlippage(timeframe=timeframe, is_xsm=is_xsm)
    else:
        slippage = FixedBpsSlippage(entry_bps=5.0, exit_bps=5.0)

    return ExecutionConfig(
        slippage=slippage,
        fees=TieredFees(maker_bps=2.0, taker_bps=4.0),
        fills=FillModel(
            entry_mode=EntryMode.NEXT_OPEN,
            stop_fill=FillAssumption.TOUCH,
            target_fill=FillAssumption.TOUCH,
        ),
        max_positions=3,
        one_per_asset=True,
    )


def conservative_config() -> ExecutionConfig:
    """
    Conservative config for stress testing.
    
    Higher slippage, worse fills.
    If strategy works here, it's robust.
    """
    return ExecutionConfig(
        slippage=FixedBpsSlippage(entry_bps=10.0, exit_bps=10.0),
        fees=TieredFees(maker_bps=4.0, taker_bps=8.0),
        fills=FillModel(
            entry_mode=EntryMode.NEXT_OPEN,
            stop_fill=FillAssumption.THROUGH,  # Stricter
            target_fill=FillAssumption.THROUGH,
        ),
        max_positions=3,
        one_per_asset=True,
    )


def optimistic_config() -> ExecutionConfig:
    """
    Optimistic config for upper-bound estimates.
    
    Use only for comparison, not for decision-making.
    """
    return ExecutionConfig(
        slippage=FixedBpsSlippage(entry_bps=2.0, exit_bps=2.0),
        fees=TieredFees(maker_bps=2.0, taker_bps=2.0),
        fills=FillModel(
            entry_mode=EntryMode.BAR_CLOSE,  # Optimistic
            stop_fill=FillAssumption.TOUCH,
            target_fill=FillAssumption.ALWAYS,  # Very optimistic
        ),
        max_positions=5,
        one_per_asset=True,
    )


def zero_cost_config() -> ExecutionConfig:
    """
    Zero-cost config for testing logic only.
    
    NEVER use for production decisions.
    """
    return ExecutionConfig(
        slippage=ZeroSlippage(),
        fees=ZeroFees(),
        fills=FillModel(entry_mode=EntryMode.BAR_CLOSE),
        max_positions=10,
        one_per_asset=False,
    )
