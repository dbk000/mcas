"""
Run System - Data Models and Schema
===================================

A Run is a fully specified, replayable execution of the engine.

"If you can't rerun it and get the same output, it wasn't a Run — it was a vibe."

Run types:
- BACKTEST: Historical simulation, bar-by-bar
- SCAN: Find setups/alerts without simulating PnL
- PAPER: Forward test on live data, simulated execution
- LIVE: Real execution (future)
- REPLAY: Rerun of previous Run for verification
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any


class RunType(Enum):
    BACKTEST = "BACKTEST"
    SCAN = "SCAN"
    PAPER = "PAPER"
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    SIMULATION = "SIMULATION"


class RunStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class ExitReason(Enum):
    STOP_LOSS = "STOP_LOSS"
    TP1 = "TP1"
    TP2 = "TP2"
    TRAIL_STOP = "TRAIL_STOP"
    TIME_STOP = "TIME_STOP"
    FAIL_FAST = "FAIL_FAST"
    MANUAL = "MANUAL"
    SIGNAL_INVALIDATION = "SIGNAL_INVALIDATION"
    OPEN = "OPEN"  # Still open


@dataclass
class UniverseSpec:
    """Defines what assets/timeframes/date range to process."""
    assets: list[str]  # Resolved list at execution time
    timeframes: list[str]
    start_ts: Optional[datetime] = None  # None = live/current
    end_ts: Optional[datetime] = None
    venue: str = "binance"
    session_filter: Optional[str] = None  # Future: market hours
    
    def to_dict(self) -> dict:
        return {
            "assets": sorted(self.assets),  # Sorted for determinism
            "timeframes": sorted(self.timeframes),
            "start_ts": self.start_ts.isoformat() if self.start_ts else None,
            "end_ts": self.end_ts.isoformat() if self.end_ts else None,
            "venue": self.venue,
            "session_filter": self.session_filter,
        }


@dataclass
class StrategySpec:
    """Defines the setup and its parameters."""
    setup_id: str  # e.g., "S1", "S2"
    setup_version: str  # e.g., "v2.0"
    setup_params: dict = field(default_factory=dict)
    bias_mode: str = "HTF_TREND_PROXY"
    risk_model_id: str = "fixed_r"
    risk_params: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "setup_id": self.setup_id,
            "setup_version": self.setup_version,
            "setup_params": self.setup_params,
            "bias_mode": self.bias_mode,
            "risk_model_id": self.risk_model_id,
            "risk_params": self.risk_params,
        }


@dataclass
class ExecutionSpec:
    """
    Defines how trades are entered/exited in simulation.
    
    > "Trading starts when nothing important is implicit anymore."
    
    Every assumption is named and parameterized.
    This goes into the fingerprint.
    """
    # Entry mode
    entry_mode: str = "NEXT_OPEN"  # NEXT_OPEN, BAR_CLOSE, MOC, LIMIT_RETEST
    
    # Slippage model (explicit)
    slippage_model: str = "FIXED_BPS"  # FIXED_BPS, VOLATILITY, ZERO
    slippage_entry_bps: float = 5.0   # 0.05% entry slippage
    slippage_exit_bps: float = 5.0    # 0.05% exit slippage
    
    # Fee model (explicit)
    fee_model: str = "TIERED"  # TIERED, FLAT, ZERO
    fee_maker_bps: float = 2.0   # 0.02% maker
    fee_taker_bps: float = 4.0   # 0.04% taker
    
    # Fill assumptions (explicit)
    fill_model: str = "BAR_BASED"  # BAR_BASED, IMMEDIATE, PARTIAL
    stop_fill_on: str = "TOUCH"    # TOUCH, THROUGH
    target_fill_on: str = "TOUCH"  # TOUCH, THROUGH
    gap_fill_at_open: bool = True  # If price gaps through stop
    intrabar_policy: str = "STOP_FIRST"  # STOP_FIRST, TP_FIRST, OHLC_HEURISTIC
    one_event_per_bar: bool = True
    
    # Position constraints
    max_positions: int = 3
    one_per_asset: bool = True
    max_exposure_pct: float = 100.0  # Max % of capital exposed
    
    # Risk constraints
    max_risk_per_trade_pct: float = 1.0  # 1% risk per trade
    max_daily_loss_pct: float = 5.0      # Circuit breaker

    # Virtual account (for backtest)
    starting_capital: float = 10000.0    # USDT

    def to_dict(self) -> dict:
        return {
            "entry_mode": self.entry_mode,
            "slippage_model": self.slippage_model,
            "slippage_entry_bps": self.slippage_entry_bps,
            "slippage_exit_bps": self.slippage_exit_bps,
            "fee_model": self.fee_model,
            "fee_maker_bps": self.fee_maker_bps,
            "fee_taker_bps": self.fee_taker_bps,
            "fill_model": self.fill_model,
            "stop_fill_on": self.stop_fill_on,
            "target_fill_on": self.target_fill_on,
            "gap_fill_at_open": self.gap_fill_at_open,
            "intrabar_policy": self.intrabar_policy,
            "one_event_per_bar": self.one_event_per_bar,
            "max_positions": self.max_positions,
            "one_per_asset": self.one_per_asset,
            "max_exposure_pct": self.max_exposure_pct,
            "max_risk_per_trade_pct": self.max_risk_per_trade_pct,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "starting_capital": self.starting_capital,
        }


@dataclass
class DataSpec:
    """
    Defines data source and versioning for reproducibility.
    
    Without data versioning, backtests drift silently over time
    as more data is ingested or corrections are made.
    
    > "Trading starts when nothing important is implicit anymore."
    """
    # Source identification
    data_source: str = "binance_rest"  # Pipeline identifier
    venue: str = "binance_spot"  # Exchange/venue
    
    # Versioning (critical for reproducibility)
    data_version: Optional[str] = None  # Ingestion batch ID
    data_snapshot_ts: Optional[datetime] = None  # "As of" timestamp
    data_ingested_up_to: Optional[datetime] = None  # Latest bar timestamp
    
    # Quality handling
    gaps_policy: str = "FORWARD_FILL"  # FORWARD_FILL, DROP_BARS, SKIP_ASSET
    max_gap_bars: int = 5  # Skip asset if gap exceeds this
    min_bars_required: int = 201  # For indicator warmup
    
    # Checksums (for verification)
    ohlcv_checksum: Optional[str] = None  # Hash of OHLCV data used
    
    def to_dict(self) -> dict:
        return {
            "data_source": self.data_source,
            "venue": self.venue,
            "data_version": self.data_version,
            "data_snapshot_ts": self.data_snapshot_ts.isoformat() if self.data_snapshot_ts else None,
            "data_ingested_up_to": self.data_ingested_up_to.isoformat() if self.data_ingested_up_to else None,
            "gaps_policy": self.gaps_policy,
            "max_gap_bars": self.max_gap_bars,
            "min_bars_required": self.min_bars_required,
            "ohlcv_checksum": self.ohlcv_checksum,
        }


@dataclass
class TradeTrace:
    """
    Complete audit trail for a single trade.
    
    This is the difference between:
    - "the system did something"
    - "I trust the system"
    
    Every entry stores why it entered.
    Every exit stores why it exited.
    Failed gates are inspectable.
    """
    # -------------------------------------------------------------------------
    # Entry audit
    # -------------------------------------------------------------------------
    
    # Which conditions passed/failed at entry
    conditions_met: dict[str, bool] = field(default_factory=dict)
    # Example: {"squeeze_on": True, "bb_width_pctile_ok": True, "clv_ok": True}
    
    # Why we entered (human-readable)
    entry_reason: str = ""
    # Example: "S1_TRIGGER: Squeeze ON breakout with strong close"
    
    # Computed indicator values at entry (frozen snapshot)
    indicator_values: dict[str, float] = field(default_factory=dict)
    # Example: {"bb_width_pctile": 8.3, "clv": 0.78, "volume_z": 2.1}
    
    # Full state at entry
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    # Example: {"squeeze_on": True, "squeeze_age": 7, "bias_score": 1.5}
    
    # Score breakdown
    score_components: dict[str, float] = field(default_factory=dict)
    # Example: {"severity": 0.92, "bias_strength": 0.75, "participation": 0.55}
    
    # -------------------------------------------------------------------------
    # Exit audit
    # -------------------------------------------------------------------------
    
    # Why we exited
    exit_reason: Optional[str] = None
    # Values: STOP_LOSS, TP1, TP2, TRAIL_STOP, TIME_STOP, FAIL_FAST, MANUAL
    
    # Detailed exit explanation
    exit_explanation: str = ""
    # Example: "Hit TP1 at 1.0R after 8 bars"
    
    # Conditions at exit
    exit_conditions: dict[str, Any] = field(default_factory=dict)
    # Example: {"bars_held": 8, "price_at_exit": 142.50, "high_water_mark": 143.20}
    
    # Indicator values at exit (for comparison with entry)
    exit_indicator_values: dict[str, float] = field(default_factory=dict)
    
    # -------------------------------------------------------------------------
    # Failed attempts (for debugging)
    # -------------------------------------------------------------------------
    
    # If signal was generated but trade wasn't taken, why?
    rejection_reason: Optional[str] = None
    # Example: "max_positions_reached", "duplicate_asset", "below_min_score"
    
    # Which gates failed (if any)
    failed_gates: list[str] = field(default_factory=list)
    # Example: ["clv_below_threshold", "volume_z_insufficient"]
    
    def to_dict(self) -> dict:
        return {
            "conditions_met": self.conditions_met,
            "entry_reason": self.entry_reason,
            "indicator_values": self.indicator_values,
            "state_snapshot": self.state_snapshot,
            "score_components": self.score_components,
            "exit_reason": self.exit_reason,
            "exit_explanation": self.exit_explanation,
            "exit_conditions": self.exit_conditions,
            "exit_indicator_values": self.exit_indicator_values,
            "rejection_reason": self.rejection_reason,
            "failed_gates": self.failed_gates,
        }


@dataclass
class RunTrade:
    """A trade produced by a Run."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    asset: str = ""
    timeframe: str = ""
    side: str = ""  # "long" or "short"
    setup_id: str = ""

    # Canon 5A: which bar produced the signal (distinct from entry_ts)
    signal_bar_ts: Optional[datetime] = None

    # Canon 5E: signal timing metadata
    since_ts: Optional[datetime] = None      # When signal entered current state
    action_by_ts: Optional[datetime] = None  # Latest time to act (time-stop deadline)

    # Entry
    entry_ts: Optional[datetime] = None
    entry_price: float = 0.0
    entry_bar_index: int = 0

    # Risk levels
    stop_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    r_value: float = 0.0

    # Exit
    exit_ts: Optional[datetime] = None
    exit_price: float = 0.0
    exit_reason: str = "OPEN"

    # Results
    r_multiple: float = 0.0
    pnl_gross: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    pnl_net: float = 0.0

    # Audit trail
    setup_score: float = 0.0
    trace: TradeTrace = field(default_factory=TradeTrace)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "asset": self.asset,
            "timeframe": self.timeframe,
            "side": self.side,
            "setup_id": self.setup_id,
            "signal_bar_ts": self.signal_bar_ts.isoformat() if self.signal_bar_ts else None,
            "since_ts": self.since_ts.isoformat() if self.since_ts else None,
            "action_by_ts": self.action_by_ts.isoformat() if self.action_by_ts else None,
            "entry_ts": self.entry_ts.isoformat() if self.entry_ts else None,
            "entry_price": self.entry_price,
            "entry_bar_index": self.entry_bar_index,
            "stop_price": self.stop_price,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "r_value": self.r_value,
            "exit_ts": self.exit_ts.isoformat() if self.exit_ts else None,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "r_multiple": self.r_multiple,
            "pnl_gross": self.pnl_gross,
            "fees": self.fees,
            "slippage": self.slippage,
            "pnl_net": self.pnl_net,
            "setup_score": self.setup_score,
            "trace": self.trace.to_dict(),
        }


@dataclass
class RunEvent:
    """A logged event during a Run (warnings, info, errors)."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = "INFO"  # INFO, WARN, ERROR
    message: str = ""
    payload: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "ts": self.ts.isoformat(),
            "event_type": self.event_type,
            "message": self.message,
            "payload": self.payload,
        }


@dataclass
class RunSummary:
    """Summary metrics for a completed Run."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    total_r: float = 0.0
    avg_r: float = 0.0
    max_r: float = 0.0
    min_r: float = 0.0

    gross_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    net_pnl: float = 0.0

    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    bars_processed: int = 0
    assets_processed: int = 0
    assets_skipped: int = 0
    signals_generated: int = 0

    execution_time_seconds: float = 0.0

    # Virtual account (P0-4)
    starting_capital: float = 0.0
    final_equity: float = 0.0
    equity_curve: list = field(default_factory=list)

    # P2-13: Extended risk metrics
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_holding_bars: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    payoff_ratio: float = 0.0
    return_pct: float = 0.0

    # P2-14: Per-setup and per-symbol breakdowns
    per_setup: dict = field(default_factory=dict)
    per_symbol: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Trim equity_curve to reduce JSON size: keep every Nth point
        curve = d.get("equity_curve", [])
        if len(curve) > 500:
            step = max(1, len(curve) // 500)
            d["equity_curve"] = curve[::step] + [curve[-1]]
        return d


@dataclass
class Run:
    """
    A fully specified, replayable execution of the engine.
    
    Immutability rule: Never mutate a Run. Create a new one instead.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_type: RunType = RunType.SCAN
    status: RunStatus = RunStatus.PENDING
    
    # Human identity
    name: Optional[str] = None
    notes: Optional[str] = None
    
    # Deterministic identity
    fingerprint: str = ""
    
    # Engine version
    engine_version: str = "1.0.0"
    engine_commit: Optional[str] = None
    
    # Specs
    universe: UniverseSpec = field(default_factory=UniverseSpec)
    strategy: StrategySpec = field(default_factory=StrategySpec)
    execution: ExecutionSpec = field(default_factory=ExecutionSpec)
    data: DataSpec = field(default_factory=DataSpec)
    
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    
    # Results (populated after run)
    summary: RunSummary = field(default_factory=RunSummary)
    error_text: Optional[str] = None
    
    # Parent run (for REPLAY type)
    parent_run_id: Optional[str] = None
    
    def compute_fingerprint(self) -> str:
        """
        Compute deterministic fingerprint from all effective inputs.
        If anything changes, fingerprint changes.

        Canon §6: includes indicator library version so two backtests
        using different indicator code produce different fingerprints.
        """
        # Late import to avoid circular dependency
        try:
            from indicators import INDICATOR_LIBRARY_VERSION, get_indicator_library_fingerprint
            indicator_fp = get_indicator_library_fingerprint()
            indicator_ver = INDICATOR_LIBRARY_VERSION
        except ImportError:
            indicator_fp = None
            indicator_ver = None

        canonical = {
            "engine_version": self.engine_version,
            "engine_commit": self.engine_commit,
            "run_type": self.run_type.value,
            "universe": self.universe.to_dict(),
            "strategy": self.strategy.to_dict(),
            "execution": self.execution.to_dict(),
            "data": self.data.to_dict(),
            "indicator_library_version": indicator_ver,
            "indicator_library_fingerprint": indicator_fp,
        }

        canonical_json = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical_json.encode()).hexdigest()[:16]
    
    def __post_init__(self):
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_type": self.run_type.value,
            "status": self.status.value,
            "name": self.name,
            "notes": self.notes,
            "fingerprint": self.fingerprint,
            "engine_version": self.engine_version,
            "engine_commit": self.engine_commit,
            "universe": self.universe.to_dict(),
            "strategy": self.strategy.to_dict(),
            "execution": self.execution.to_dict(),
            "data": self.data.to_dict(),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "summary": self.summary.to_dict(),
            "error_text": self.error_text,
            "parent_run_id": self.parent_run_id,
        }


# =============================================================================
# DDL GOVERNANCE (Canon 10)
# =============================================================================
# Schema is governed by a single source of truth:
#   Backend/docs/db/schema/mcas_schema.sql
#
# This module does NOT define DDL inline. The repository.ensure_schema()
# method creates only the pgcrypto extension; all table DDL comes from
# the canonical schema file applied via psql during deployment.
#
# See CLAUDE.md Canon §10: "Avoid duplicating DDL in multiple runtime
# entrypoints."
# =============================================================================

RUNS_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
"""
