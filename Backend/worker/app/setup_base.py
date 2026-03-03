"""
Setup Base
==========

Base class for all trading setups.

Each setup declares:
1. Required indicators (for dependency management)
2. Parameters with defaults
3. State computation
4. Trigger evaluation

This enables:
- Compute only what's needed
- Clear parameter documentation
- Consistent interface across setups
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any, TypeVar, Generic
from enum import Enum


# =============================================================================
# Common Types (shared across all setups)
# =============================================================================

class Direction(Enum):
    """Trade direction."""
    LONG = "long"
    SHORT = "short"
    NONE = "none"


class ExitType(Enum):
    """Exit reason types."""
    STOP_LOSS = "STOP_LOSS"
    TP1 = "TP1"
    TP2 = "TP2"
    TIME_STOP = "TIME_STOP"
    FAIL_FAST = "FAIL_FAST"
    TRAILING_STOP = "TRAILING_STOP"
    MANUAL = "MANUAL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


class IndicatorRequirement(Enum):
    """
    Indicator categories that setups can require.
    
    Used for dependency resolution - engine computes only what's needed.
    """
    # Tier 0 - Raw
    OHLCV = "OHLCV"
    
    # Tier 1 - Basic
    ATR = "ATR"
    CLV = "CLV"
    VOLUME_Z = "VOLUME_Z"
    RSI = "RSI"
    
    # Tier 2 - MAs
    SMA_50 = "SMA_50"
    SMA_200 = "SMA_200"
    EMA_9 = "EMA_9"
    EMA_20 = "EMA_20"
    EMA_21 = "EMA_21"
    EMA_50 = "EMA_50"
    
    # Tier 2 - Bands
    BB_BANDS = "BB_BANDS"  # MB, UB, LB, width
    KC_BANDS = "KC_BANDS"  # MB, UB, LB
    
    # Tier 2 - Slopes
    SMA_SLOPES = "SMA_SLOPES"  # ATR-normalized
    
    # Tier 2 - Derived
    MA_GAP = "MA_GAP"  # SMA50-SMA200 gap
    
    # HTF
    HTF_INDICATORS = "HTF_INDICATORS"

    # Tier 2 - MACD
    MACD = "MACD"
    SMA_20 = "SMA_20"
    DONCHIAN = "DONCHIAN"
    VWAP_IND = "VWAP"
    EMA_SLOPES = "EMA_SLOPES"

    # Tier 2 - Dormancy / Accumulation (for D1)
    DORMANCY_SCORE = "DORMANCY_SCORE"
    ACCUM_DIVERGENCE = "ACCUM_DIVERGENCE"


@dataclass
class SetupParameter:
    """
    A setup parameter with metadata.
    
    Used for:
    - Documentation
    - UI generation
    - Validation
    - Fingerprinting
    """
    name: str
    default: Any
    description: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    param_type: str = "float"  # float, int, bool, str
    
    def validate(self, value: Any) -> bool:
        """Validate a parameter value."""
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        return True


@dataclass
class LiquidityTier:
    """
    Canon 7A: Liquidity tier definition for position sizing.

    Each setup declares which tiers it trades and the max sizing per tier.
    """
    name: str                  # e.g. "mega_cap", "large_cap", "mid_cap"
    min_24h_volume_usd: float  # Minimum 24h quote volume to qualify
    max_position_pct: float    # Max position as % of capital


@dataclass
class ExplainPayload:
    """
    Canon 7A: Explainability payload for setup triggers.

    Every signal must produce 3-8 human-readable "why" items.
    """
    why_items: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Return validation errors (empty if valid)."""
        errors = []
        if len(self.why_items) < 3:
            errors.append(f"Need at least 3 why_items, got {len(self.why_items)}")
        if len(self.why_items) > 8:
            errors.append(f"Maximum 8 why_items, got {len(self.why_items)}")
        return errors

    def to_dict(self) -> dict:
        return {"why_items": self.why_items}


@dataclass
class SetupMetadata:
    """
    Metadata about a setup.

    Used for cataloging, filtering, and documentation.
    """
    id: str  # e.g., "S1"
    name: str  # e.g., "BB Squeeze ON"
    version: str  # e.g., "v2.0"
    description: str
    author: str = "MCAS"

    # Categorization
    setup_type: str = "momentum"  # momentum, mean_reversion, breakout, etc.
    timeframe_affinity: list[str] = field(default_factory=lambda: ["1h", "4h"])

    # Trading characteristics
    typical_hold_bars: int = 12
    typical_win_rate: float = 0.45
    typical_r_multiple: float = 1.5

    # Requirements
    min_bars_required: int = 201

    # Canon 7A: concurrency rules (human bandwidth)
    max_concurrent: int = 3            # Max simultaneous open positions for this setup

    # Canon 7A: liquidity tiers / sizing caps
    liquidity_tiers: list[LiquidityTier] = field(default_factory=lambda: [
        LiquidityTier(name="mega_cap",  min_24h_volume_usd=500_000_000, max_position_pct=5.0),
        LiquidityTier(name="large_cap", min_24h_volume_usd=50_000_000,  max_position_pct=3.0),
        LiquidityTier(name="mid_cap",   min_24h_volume_usd=5_000_000,   max_position_pct=1.5),
        LiquidityTier(name="small_cap", min_24h_volume_usd=0,           max_position_pct=0.5),
    ])

    # Canon 7A: sizing caps
    sizing_caps: dict = field(default_factory=lambda: {
        "max_risk_per_trade_pct": 1.0,   # Max % capital risked per trade
        "max_notional_pct": 10.0,        # Max notional as % of capital
    })

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "setup_type": self.setup_type,
            "timeframe_affinity": self.timeframe_affinity,
            "typical_hold_bars": self.typical_hold_bars,
            "min_bars_required": self.min_bars_required,
            "max_concurrent": self.max_concurrent,
            "liquidity_tiers": [
                {"name": t.name, "min_24h_volume_usd": t.min_24h_volume_usd,
                 "max_position_pct": t.max_position_pct}
                for t in self.liquidity_tiers
            ],
            "sizing_caps": self.sizing_caps,
        }


# Generic types for setup state and signal
TState = TypeVar("TState")
TSignal = TypeVar("TSignal")


class SetupBase(ABC, Generic[TState, TSignal]):
    """
    Base class for all trading setups.
    
    Subclasses must implement:
    - metadata property
    - required_indicators property
    - parameters property
    - compute_state()
    - evaluate_trigger()
    
    Example:
        class S1Setup(SetupBase[S1State, S1Signal]):
            @property
            def metadata(self) -> SetupMetadata:
                return SetupMetadata(
                    id="S1",
                    name="BB Squeeze ON",
                    version="v2.0",
                    ...
                )
            
            @property
            def required_indicators(self) -> set[IndicatorRequirement]:
                return {
                    IndicatorRequirement.BB_BANDS,
                    IndicatorRequirement.KC_BANDS,
                    IndicatorRequirement.CLV,
                    IndicatorRequirement.VOLUME_Z,
                    IndicatorRequirement.RSI,
                }
    """
    
    @property
    @abstractmethod
    def metadata(self) -> SetupMetadata:
        """Return setup metadata."""
        pass
    
    @property
    @abstractmethod
    def required_indicators(self) -> set[IndicatorRequirement]:
        """
        Return set of required indicators.
        
        Used by engine to compute only what's needed.
        """
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> list[SetupParameter]:
        """
        Return list of parameters with defaults and metadata.
        
        Used for:
        - Documentation
        - UI generation
        - Fingerprinting
        """
        pass
    
    @abstractmethod
    def compute_state(self, snapshot: Any, **kwargs) -> TState:
        """
        Compute Tier 3 state from indicators.
        
        Args:
            snapshot: IndicatorSnapshot with computed values
            **kwargs: Additional context (e.g., history, prev_state)
        
        Returns:
            Setup-specific state object
        """
        pass
    
    @abstractmethod
    def evaluate_trigger(
        self,
        snapshot: Any,
        state: TState,
        symbol: str,
        timeframe: str,
    ) -> Optional[TSignal]:
        """
        Evaluate if trigger conditions are met.
        
        Args:
            snapshot: IndicatorSnapshot
            state: Computed state from compute_state()
            symbol: Trading pair
            timeframe: Candle timeframe
        
        Returns:
            Signal if triggered, None otherwise
        """
        pass
    
    def get_param_dict(self) -> dict:
        """Get parameters as a dictionary for fingerprinting."""
        return {p.name: p.default for p in self.parameters}
    
    def validate_params(self, params: dict) -> list[str]:
        """
        Validate parameter values.
        
        Returns list of error messages (empty if valid).
        """
        errors = []
        param_map = {p.name: p for p in self.parameters}
        
        for name, value in params.items():
            if name not in param_map:
                errors.append(f"Unknown parameter: {name}")
                continue
            
            param = param_map[name]
            if not param.validate(value):
                errors.append(
                    f"Invalid value for {name}: {value} "
                    f"(range: {param.min_value}-{param.max_value})"
                )
        
        return errors


# =============================================================================
# Setup Registry
# =============================================================================

class SetupRegistry:
    """
    Registry of available setups.
    
    Used by engine to:
    - Look up setups by ID
    - Get required indicators for a set of setups
    - List available setups
    """
    
    def __init__(self):
        self._setups: dict[str, SetupBase] = {}
    
    def register(self, setup: SetupBase):
        """Register a setup."""
        self._setups[setup.metadata.id] = setup
    
    def get(self, setup_id: str) -> Optional[SetupBase]:
        """Get setup by ID."""
        return self._setups.get(setup_id)
    
    def list_all(self) -> list[SetupMetadata]:
        """List all registered setups."""
        return [s.metadata for s in self._setups.values()]
    
    def get_required_indicators(self, setup_ids: list[str]) -> set[IndicatorRequirement]:
        """
        Get union of required indicators for a set of setups.
        
        Used by engine to compute only what's needed.
        """
        required = set()
        for sid in setup_ids:
            setup = self._setups.get(sid)
            if setup:
                required.update(setup.required_indicators)
        return required


# Global registry
_registry = SetupRegistry()


def register_setup(setup: SetupBase):
    """Register a setup in the global registry."""
    _registry.register(setup)


def get_setup(setup_id: str) -> Optional[SetupBase]:
    """Get setup from global registry."""
    return _registry.get(setup_id)


def list_setups() -> list[SetupMetadata]:
    """List all registered setups."""
    return _registry.list_all()


def get_required_indicators(setup_ids: list[str]) -> set[IndicatorRequirement]:
    """Get required indicators for setups."""
    return _registry.get_required_indicators(setup_ids)
