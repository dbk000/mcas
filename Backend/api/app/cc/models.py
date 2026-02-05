"""
Champion/Challenger Framework - Data Models
============================================

Core dataclasses, enums, and type definitions for the C/C framework.

Definitions:
- Variant: A versioned configuration of a setup (Champion, Challenger, or Baseline)
- Module: A pluggable component (FILTER, REGIME, ENTRY, STOP, EXIT, SIZING, SCORE)
- ExperimentRun: A parallel evaluation comparing baseline + champion + challenger
- ShadowTrade: A simulated trade for metrics comparison

Canon compliance:
- One-module-different rule: Challenger differs from Champion by exactly one module
- Baseline runs forever: Dead-simple variant always evaluated for reference
- Weekly cadence: Max 1 change per week per setup
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any
import uuid


# =============================================================================
# ENUMS
# =============================================================================

class VariantStatus(Enum):
    """Status of a variant in the C/C lifecycle."""
    CHAMPION = "CHAMPION"
    CHALLENGER = "CHALLENGER"
    BASELINE = "BASELINE"
    RETIRED = "RETIRED"


class ModuleKind(Enum):
    """
    Module slot categories per canon.

    Priority order for next-tweak recommendations:
    FILTER -> REGIME -> ENTRY -> STOP -> EXIT -> SIZING -> SCORE
    """
    FILTER = "FILTER"     # Trade eligibility / liquidity filters
    REGIME = "REGIME"     # Regime tagging
    ENTRY = "ENTRY"       # Entry triggers
    STOP = "STOP"         # Stops / invalidation
    EXIT = "EXIT"         # Take-profit / trailing
    SIZING = "SIZING"     # Position sizing
    SCORE = "SCORE"       # Scoring weights (last)


class DiffType(Enum):
    """Type of difference between Champion and Challenger."""
    MODULE = "MODULE"                     # Different module_id at a slot
    PARAM_BUNDLE = "PARAM_BUNDLE"         # Same module_id, different params
    SCORE_COMPONENT = "SCORE_COMPONENT"   # Scoring weight change


class ExperimentMode(Enum):
    """How the experiment is executed."""
    SHADOW = "SHADOW"           # All variants evaluate, none execute
    LIVE_SMALL = "LIVE_SMALL"   # Small allocation to test execution
    SPLIT = "SPLIT"             # Split traffic between variants


class ExperimentStatus(Enum):
    """Status of an experiment run."""
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    PROMOTED = "PROMOTED"


class ExecutionContext(Enum):
    """
    Execution context flag passed to all execution functions.

    INVARIANT: Functions MUST check this flag before any:
    - Queue publish (order submission)
    - External API call (exchange)
    - Shared state mutation
    """
    LIVE = "live"           # Real execution - can publish orders
    PAPER = "paper"         # Paper trading - writes to paper tables only
    SHADOW = "shadow"       # CC shadow - NO external effects, metrics only


class Direction(Enum):
    """Trade direction."""
    LONG = "LONG"
    SHORT = "SHORT"


# Failure buckets for losing trades
class FailureBucket(Enum):
    """Classification buckets for losing trades."""
    BAD_REGIME = "BAD_REGIME"               # Wrong market conditions
    BAD_ENTRY_TIMING = "BAD_ENTRY_TIMING"   # Entry too early/late
    BAD_STOP = "BAD_STOP"                   # Stop too tight/loose
    BAD_TP_TRAILING = "BAD_TP_TRAILING"     # Exit too early/late
    LIQUIDITY_ISSUE = "LIQUIDITY_ISSUE"     # Slippage, gap, thin book
    SIGNAL_CONFLICT = "SIGNAL_CONFLICT"     # Conflicting signals
    OTHER = "OTHER"                         # Unclassified


# Winner buckets for winning trades (edge leak detection)
class WinnerBucket(Enum):
    """Classification buckets for winning trades."""
    EARLY_EXIT = "EARLY_EXIT"                         # Exited before full move captured
    REGIME_TAILWIND = "REGIME_TAILWIND"               # Won due to favorable regime
    ENTRY_PRECISION = "ENTRY_PRECISION"               # Entry timing was optimal
    STOP_NEVER_THREATENED = "STOP_NEVER_THREATENED"   # Stop was never close
    HIGH_SLIPPAGE_WIN = "HIGH_SLIPPAGE_WIN"           # Won despite slippage
    CONFLICTING_SIGNAL_WIN = "CONFLICTING_SIGNAL_WIN" # Won despite conflicts
    CLEAN_WIN = "CLEAN_WIN"                           # Textbook execution


# =============================================================================
# MODULE DEFINITIONS
# =============================================================================

@dataclass
class Module:
    """
    A module definition (pluggable component).

    Stored in cc_modules table.
    """
    module_id: str                      # e.g., "exit.trailing.v1"
    module_kind: ModuleKind             # FILTER, REGIME, ENTRY, etc.
    version: str                        # e.g., "v1.0"
    implementation_ref: str             # Python import path
    config_schema: Optional[dict] = None  # JSON schema for params
    description: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "module_id": self.module_id,
            "module_kind": self.module_kind.value,
            "version": self.version,
            "implementation_ref": self.implementation_ref,
            "config_schema": self.config_schema,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class VariantModule:
    """
    A module assignment within a variant.

    Maps module_kind -> module_id + params for a specific variant.
    """
    module_kind: ModuleKind
    module_id: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "module_kind": self.module_kind.value,
            "module_id": self.module_id,
            "params": self.params,
        }


# =============================================================================
# VARIANT
# =============================================================================

@dataclass
class Variant:
    """
    A versioned configuration of a setup.

    Stored in cc_variants table.
    """
    variant_id: str                           # e.g., "S1@2.0.3"
    setup_id: str                             # e.g., "S1", "S2", "U1"
    status: VariantStatus = VariantStatus.CHALLENGER

    # Lineage
    parent_variant_id: Optional[str] = None

    # Diff specification (what changed from parent)
    diff_type: Optional[DiffType] = None
    diff_module_kind: Optional[ModuleKind] = None
    diff_key: Optional[str] = None            # e.g., "EXIT.trailing"
    diff_description: Optional[str] = None

    # Version info
    version_major: int = 1
    version_minor: int = 0
    version_patch: int = 0

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "system"
    notes: Optional[str] = None

    # Module map (loaded separately)
    modules: dict[str, VariantModule] = field(default_factory=dict)

    @property
    def version_string(self) -> str:
        return f"{self.version_major}.{self.version_minor}.{self.version_patch}"

    def to_dict(self) -> dict:
        return {
            "variant_id": self.variant_id,
            "setup_id": self.setup_id,
            "status": self.status.value,
            "parent_variant_id": self.parent_variant_id,
            "diff_type": self.diff_type.value if self.diff_type else None,
            "diff_module_kind": self.diff_module_kind.value if self.diff_module_kind else None,
            "diff_key": self.diff_key,
            "diff_description": self.diff_description,
            "version_major": self.version_major,
            "version_minor": self.version_minor,
            "version_patch": self.version_patch,
            "version_string": self.version_string,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "notes": self.notes,
            "modules": {k: v.to_dict() for k, v in self.modules.items()},
        }


@dataclass
class CachedVariant:
    """
    A variant with its evaluator loaded and ready for use.

    Created at experiment run start, cached for the run lifetime.
    """
    variant_id: str
    setup_id: str
    status: VariantStatus
    module_map_snapshot: dict
    evaluator: Any  # The actual setup evaluator instance

    def to_dict(self) -> dict:
        return {
            "variant_id": self.variant_id,
            "setup_id": self.setup_id,
            "status": self.status.value,
            "module_map_snapshot": self.module_map_snapshot,
        }


# =============================================================================
# EXPERIMENT RUN
# =============================================================================

@dataclass
class ExperimentRun:
    """
    A parallel evaluation comparing baseline + champion + challenger.

    Stored in cc_experiment_runs table.
    """
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Variants being compared
    baseline_variant_id: Optional[str] = None
    champion_variant_id: str = ""
    challenger_variant_id: str = ""

    # Frozen module maps at run start
    baseline_module_map_snapshot: Optional[dict] = None
    champion_module_map_snapshot: dict = field(default_factory=dict)
    challenger_module_map_snapshot: dict = field(default_factory=dict)

    # Run mode
    mode: ExperimentMode = ExperimentMode.SHADOW
    split_baseline_pct: Optional[float] = None
    split_champion_pct: Optional[float] = None
    split_challenger_pct: Optional[float] = None

    # Scope
    setup_id: str = ""
    asset_universe_snapshot: Optional[list[str]] = None
    timeframes: list[str] = field(default_factory=list)

    # Execution spec (frozen from paper trading defaults)
    execution_spec_snapshot: dict = field(default_factory=dict)

    # Status
    status: ExperimentStatus = ExperimentStatus.RUNNING

    # Timestamps
    start_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_ts: Optional[datetime] = None

    # Notes
    notes: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "baseline_variant_id": self.baseline_variant_id,
            "champion_variant_id": self.champion_variant_id,
            "challenger_variant_id": self.challenger_variant_id,
            "baseline_module_map_snapshot": self.baseline_module_map_snapshot,
            "champion_module_map_snapshot": self.champion_module_map_snapshot,
            "challenger_module_map_snapshot": self.challenger_module_map_snapshot,
            "mode": self.mode.value,
            "split_baseline_pct": self.split_baseline_pct,
            "split_champion_pct": self.split_champion_pct,
            "split_challenger_pct": self.split_challenger_pct,
            "setup_id": self.setup_id,
            "asset_universe_snapshot": self.asset_universe_snapshot,
            "timeframes": self.timeframes,
            "execution_spec_snapshot": self.execution_spec_snapshot,
            "status": self.status.value,
            "start_ts": self.start_ts.isoformat(),
            "end_ts": self.end_ts.isoformat() if self.end_ts else None,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
        }


# =============================================================================
# SHADOW TRADE
# =============================================================================

@dataclass
class ShadowTrade:
    """
    A simulated trade for C/C metrics comparison.

    Stored in cc_experiment_trades table.
    """
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Identity
    symbol: str = ""
    timeframe: str = ""
    direction: Direction = Direction.LONG

    # Signal timing
    signal_ts: Optional[datetime] = None
    signal_bar_ts: Optional[datetime] = None

    # Entry
    entry_ts: Optional[datetime] = None
    entry_price: Optional[float] = None
    entry_price_with_slippage: Optional[float] = None

    # Risk levels
    stop_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    r_value: float = 0.0

    # Exit
    exit_ts: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_price_with_slippage: Optional[float] = None
    exit_reason: Optional[str] = None

    # Results
    r_multiple: float = 0.0
    pnl_gross: float = 0.0
    pnl_net: float = 0.0
    fees_paid: float = 0.0

    # Tracking
    bars_held: int = 0
    half_r_touched: bool = False
    mae: float = 0.0   # Max adverse excursion
    mfe: float = 0.0   # Max favorable excursion
    high_water_mark: float = 0.0

    # For live/split modes
    was_executed: bool = False
    execution_id: Optional[str] = None

    # Audit
    trace_json: Optional[dict] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "signal_ts": self.signal_ts.isoformat() if self.signal_ts else None,
            "signal_bar_ts": self.signal_bar_ts.isoformat() if self.signal_bar_ts else None,
            "entry_ts": self.entry_ts.isoformat() if self.entry_ts else None,
            "entry_price": self.entry_price,
            "entry_price_with_slippage": self.entry_price_with_slippage,
            "stop_price": self.stop_price,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "r_value": self.r_value,
            "exit_ts": self.exit_ts.isoformat() if self.exit_ts else None,
            "exit_price": self.exit_price,
            "exit_price_with_slippage": self.exit_price_with_slippage,
            "exit_reason": self.exit_reason,
            "r_multiple": self.r_multiple,
            "pnl_gross": self.pnl_gross,
            "pnl_net": self.pnl_net,
            "fees_paid": self.fees_paid,
            "bars_held": self.bars_held,
            "half_r_touched": self.half_r_touched,
            "mae": self.mae,
            "mfe": self.mfe,
            "was_executed": self.was_executed,
            "execution_id": self.execution_id,
            "trace_json": self.trace_json,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ShadowPosition:
    """
    A shadow position being held (for exit monitoring).

    Stored in cc_shadow_positions table as Redis backup.
    """
    trade_id: str
    run_id: str
    variant_id: str
    symbol: str
    timeframe: str
    direction: Direction

    # Position state
    entry_ts: datetime
    entry_price: float
    entry_price_with_slippage: float
    stop_price: float
    tp1_price: Optional[float] = None
    r_value: float = 0.0
    position_size: float = 1.0  # Notional or units

    # Tracking
    bars_held: int = 0
    half_r_touched: bool = False
    mae: float = 0.0
    mfe: float = 0.0

    # Pending exit
    pending_exit: Optional["ExitResult"] = None

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "run_id": self.run_id,
            "variant_id": self.variant_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "entry_ts": self.entry_ts.isoformat(),
            "entry_price": self.entry_price,
            "entry_price_with_slippage": self.entry_price_with_slippage,
            "stop_price": self.stop_price,
            "tp1_price": self.tp1_price,
            "r_value": self.r_value,
            "position_size": self.position_size,
            "bars_held": self.bars_held,
            "half_r_touched": self.half_r_touched,
            "mae": self.mae,
            "mfe": self.mfe,
            "pending_exit": self.pending_exit.to_dict() if self.pending_exit else None,
        }

    def to_redis_hash(self) -> dict:
        """Convert to Redis hash format (all values as strings)."""
        return {
            "trade_id": self.trade_id,
            "run_id": self.run_id,
            "variant_id": self.variant_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "entry_ts": self.entry_ts.isoformat(),
            "entry_price": str(self.entry_price),
            "entry_price_with_slippage": str(self.entry_price_with_slippage),
            "stop_price": str(self.stop_price),
            "tp1_price": str(self.tp1_price) if self.tp1_price else "",
            "r_value": str(self.r_value),
            "position_size": str(self.position_size),
            "bars_held": str(self.bars_held),
            "half_r_touched": "1" if self.half_r_touched else "0",
            "mae": str(self.mae),
            "mfe": str(self.mfe),
        }


@dataclass
class ExitResult:
    """Result from checking exit conditions."""
    should_exit: bool = False
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    exit_at_next_open: bool = False  # For TIME_STOP: exit at next bar open
    bars_pending: int = 0  # For stale exit tracking

    def to_dict(self) -> dict:
        return {
            "should_exit": self.should_exit,
            "exit_reason": self.exit_reason,
            "exit_price": self.exit_price,
            "exit_at_next_open": self.exit_at_next_open,
            "bars_pending": self.bars_pending,
        }


@dataclass
class PendingSignal:
    """
    A signal awaiting next-bar entry.

    Stored in cc_pending_signals table as Redis backup.
    """
    run_id: str
    variant_id: str
    symbol: str
    timeframe: str

    # Signal data
    signal_ts: datetime
    signal_bar_ts: datetime
    direction: Direction
    stop_price: float
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    r_value: float = 0.0

    # Pending age tracking
    bars_pending: int = 0
    max_pending_bars: int = 3

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "variant_id": self.variant_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signal_ts": self.signal_ts.isoformat(),
            "signal_bar_ts": self.signal_bar_ts.isoformat(),
            "direction": self.direction.value,
            "stop_price": self.stop_price,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "r_value": self.r_value,
            "bars_pending": self.bars_pending,
            "max_pending_bars": self.max_pending_bars,
        }


# =============================================================================
# METRICS
# =============================================================================

@dataclass
class RunMetrics:
    """Aggregated metrics for a variant in a run."""
    run_id: str
    variant_id: str

    # Core metrics
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0

    # Expectancy metrics
    expectancy: Optional[float] = None
    total_r: float = 0.0
    avg_r: Optional[float] = None

    # PnL
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    total_fees: float = 0.0

    # Risk metrics
    max_drawdown_pct: Optional[float] = None
    win_rate: Optional[float] = None
    profit_factor: Optional[float] = None

    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "variant_id": self.variant_id,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "expectancy": self.expectancy,
            "total_r": self.total_r,
            "avg_r": self.avg_r,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "total_fees": self.total_fees,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "computed_at": self.computed_at.isoformat(),
        }


@dataclass
class AssetMetrics:
    """Per-asset metrics for generalization gate."""
    run_id: str
    variant_id: str
    symbol: str

    trade_count: int = 0
    expectancy: Optional[float] = None
    total_r: float = 0.0
    win_rate: Optional[float] = None
    max_drawdown_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "variant_id": self.variant_id,
            "symbol": self.symbol,
            "trade_count": self.trade_count,
            "expectancy": self.expectancy,
            "total_r": self.total_r,
            "win_rate": self.win_rate,
            "max_drawdown_pct": self.max_drawdown_pct,
        }


# =============================================================================
# PROMOTION
# =============================================================================

@dataclass
class PromotionConfig:
    """Configuration for promotion gates."""

    # Gate 1: Expectancy improvement
    min_expectancy_improvement_pct: float = 0.0  # Must improve (>0)

    # Gate 2: Drawdown tolerance
    max_drawdown_increase_pct: float = 5.0  # Challenger DD can be at most 5% worse

    # Gate 3: Multi-asset generalization
    min_assets_improved: int = 3            # Must beat Champion on >= 3 assets
    min_trades_per_asset: int = 5           # Each asset must have >= 5 trades
    use_median_comparison: bool = True      # Use median per-asset expectancy

    def to_dict(self) -> dict:
        return {
            "min_expectancy_improvement_pct": self.min_expectancy_improvement_pct,
            "max_drawdown_increase_pct": self.max_drawdown_increase_pct,
            "min_assets_improved": self.min_assets_improved,
            "min_trades_per_asset": self.min_trades_per_asset,
            "use_median_comparison": self.use_median_comparison,
        }


@dataclass
class GateResult:
    """Result of a single promotion gate check."""
    gate_name: str
    passed: bool
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "details": self.details,
        }


@dataclass
class PromotionResult:
    """Overall promotion result."""
    should_promote: bool
    gates: list[GateResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "should_promote": self.should_promote,
            "gates": [g.to_dict() for g in self.gates],
        }


# =============================================================================
# DIAGNOSTICS
# =============================================================================

@dataclass
class TradeDiagnostic:
    """Diagnostic classification for a trade."""
    trade_id: str
    variant_id: str
    run_id: str
    is_winner: bool

    # Bucket (exactly one must be set)
    failure_bucket: Optional[FailureBucket] = None
    winner_bucket: Optional[WinnerBucket] = None

    # Details
    notes: Optional[str] = None
    hypothesis_module_kind: Optional[ModuleKind] = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "variant_id": self.variant_id,
            "run_id": self.run_id,
            "is_winner": self.is_winner,
            "failure_bucket": self.failure_bucket.value if self.failure_bucket else None,
            "winner_bucket": self.winner_bucket.value if self.winner_bucket else None,
            "notes": self.notes,
            "hypothesis_module_kind": self.hypothesis_module_kind.value if self.hypothesis_module_kind else None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class NextTweakRecommendation:
    """Recommendation for next module to tweak."""
    module_kind: Optional[ModuleKind] = None
    reason: str = ""
    loser_count: int = 0
    winner_count: int = 0
    combined_signal_strength: int = 0
    loser_pct: float = 0.0
    winner_pct: float = 0.0
    converging_alternatives: list[dict] = field(default_factory=list)
    loser_module_scores: dict = field(default_factory=dict)
    winner_module_scores: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "module_kind": self.module_kind.value if self.module_kind else None,
            "reason": self.reason,
            "loser_count": self.loser_count,
            "winner_count": self.winner_count,
            "combined_signal_strength": self.combined_signal_strength,
            "loser_pct": self.loser_pct,
            "winner_pct": self.winner_pct,
            "converging_alternatives": self.converging_alternatives,
            "loser_module_scores": self.loser_module_scores,
            "winner_module_scores": self.winner_module_scores,
        }


# =============================================================================
# CACHE
# =============================================================================

@dataclass
class CCEvaluatorCacheEntry:
    """
    Canonical cache entry structure.

    CRITICAL: All cache reads/writes MUST use this structure.
    Do NOT store raw ParallelEvaluator or tuples - use this dataclass.
    """
    evaluator: Any  # ParallelEvaluator instance
    last_used: float  # time.time() timestamp

    def to_dict(self) -> dict:
        return {
            "last_used": self.last_used,
        }


# =============================================================================
# CHANGE LEDGER
# =============================================================================

@dataclass
class ChangeLedgerEntry:
    """Entry in the change ledger for cadence tracking."""
    id: int
    setup_id: str
    variant_id: str
    change_type: str  # "CHALLENGER_CREATED" or "PROMOTED"
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "setup_id": self.setup_id,
            "variant_id": self.variant_id,
            "change_type": self.change_type,
            "created_at": self.created_at.isoformat(),
        }


# =============================================================================
# EXCEPTIONS
# =============================================================================

class CCError(Exception):
    """Base exception for Champion/Challenger errors."""
    pass


class CadenceViolation(CCError):
    """Raised when weekly cadence is violated."""
    pass


class ActiveChallengerExists(CCError):
    """Raised when trying to create challenger but one already exists."""
    pass


class ActiveRunExists(CCError):
    """Raised when trying to start a run but one already exists for the setup."""
    pass


class OneModuleDifferentViolation(CCError):
    """Raised when challenger differs by != 1 module from champion."""
    pass


class PromotionDenied(CCError):
    """Raised when promotion gates don't pass."""
    pass


class PromotionRaceError(CCError):
    """Raised when a concurrent promotion attempt is detected."""
    pass


class NoBaselineError(CCError):
    """Raised when no baseline exists for a setup."""
    pass


class InvalidIdentifierError(CCError):
    """Raised when an identifier contains forbidden characters."""
    pass


# =============================================================================
# CONSTANTS
# =============================================================================

# Redis TTLs
CC_STATE_TTL_S = 86400 * 14     # 14 days (2x min_run_duration to handle delays)
CC_VARIANT_CACHE_TTL_S = 86400  # 1 day (refreshed on run start)

# Max bars a pending signal/exit can wait before being cancelled
MAX_PENDING_BARS = 3

# Heartbeat interval for refreshing open positions
HEARTBEAT_INTERVAL_HOURS = 24

# Cache cleanup interval
CACHE_CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour

# Cache size alert threshold
CACHE_SIZE_ALERT_THRESHOLD = 100

# Forbidden characters in identifiers (breaks key parsing)
FORBIDDEN_ID_CHARS = frozenset([":"])

# Module priority order (canon)
MODULE_PRIORITY_ORDER = [
    ModuleKind.FILTER,
    ModuleKind.REGIME,
    ModuleKind.ENTRY,
    ModuleKind.STOP,
    ModuleKind.EXIT,
    ModuleKind.SIZING,
    ModuleKind.SCORE,
]

# Bucket to module mappings
LOSER_BUCKET_TO_MODULE = {
    FailureBucket.BAD_REGIME: ModuleKind.REGIME,
    FailureBucket.BAD_ENTRY_TIMING: ModuleKind.ENTRY,
    FailureBucket.BAD_STOP: ModuleKind.STOP,
    FailureBucket.BAD_TP_TRAILING: ModuleKind.EXIT,
    FailureBucket.LIQUIDITY_ISSUE: ModuleKind.FILTER,
    FailureBucket.SIGNAL_CONFLICT: ModuleKind.SCORE,
}

WINNER_BUCKET_TO_MODULE = {
    WinnerBucket.EARLY_EXIT: ModuleKind.EXIT,
    WinnerBucket.REGIME_TAILWIND: ModuleKind.REGIME,
    WinnerBucket.ENTRY_PRECISION: ModuleKind.ENTRY,
    WinnerBucket.STOP_NEVER_THREATENED: ModuleKind.STOP,
    WinnerBucket.HIGH_SLIPPAGE_WIN: ModuleKind.FILTER,
    WinnerBucket.CONFLICTING_SIGNAL_WIN: ModuleKind.SCORE,
}

# Weekly cadence in seconds
CADENCE_SECONDS = 7 * 24 * 60 * 60  # 7 days
