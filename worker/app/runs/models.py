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
    
    # Position constraints
    max_positions: int = 3
    one_per_asset: bool = True
    max_exposure_pct: float = 100.0  # Max % of capital exposed
    
    # Risk constraints
    max_risk_per_trade_pct: float = 1.0  # 1% risk per trade
    max_daily_loss_pct: float = 5.0      # Circuit breaker
    
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
            "max_positions": self.max_positions,
            "one_per_asset": self.one_per_asset,
            "max_exposure_pct": self.max_exposure_pct,
            "max_risk_per_trade_pct": self.max_risk_per_trade_pct,
            "max_daily_loss_pct": self.max_daily_loss_pct,
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
    
    def to_dict(self) -> dict:
        return asdict(self)


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
        """
        canonical = {
            "engine_version": self.engine_version,
            "engine_commit": self.engine_commit,
            "run_type": self.run_type.value,
            "universe": self.universe.to_dict(),
            "strategy": self.strategy.to_dict(),
            "execution": self.execution.to_dict(),
            "data": self.data.to_dict(),
        }
        
        # Sort keys for determinism
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


# SQL Schema for Postgres
RUNS_SCHEMA = """
-- =============================================================================
-- EXTENSIONS (required for UUID generation)
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- RUNS SYSTEM
-- =============================================================================

-- Runs table
CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    name TEXT,
    notes TEXT,
    fingerprint TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    engine_commit TEXT,
    
    -- Specs stored as JSONB for flexibility
    universe_json JSONB NOT NULL,
    strategy_json JSONB NOT NULL,
    execution_json JSONB NOT NULL,
    data_json JSONB NOT NULL,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    
    -- Results
    summary_json JSONB,
    error_text TEXT,
    parent_run_id UUID REFERENCES runs(id),
    
    -- Unique constraint on fingerprint + type
    CONSTRAINT runs_fingerprint_type_unique UNIQUE (fingerprint, run_type)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_type ON runs(run_type);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_fingerprint ON runs(fingerprint);

-- Run trades table
CREATE TABLE IF NOT EXISTS run_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    asset TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    side TEXT NOT NULL,
    setup_id TEXT NOT NULL,
    
    -- Entry
    entry_ts TIMESTAMPTZ,
    entry_price DOUBLE PRECISION NOT NULL,
    entry_bar_index INTEGER,
    
    -- Risk levels
    stop_price DOUBLE PRECISION NOT NULL,
    tp1_price DOUBLE PRECISION,
    tp2_price DOUBLE PRECISION,
    r_value DOUBLE PRECISION NOT NULL,
    
    -- Exit
    exit_ts TIMESTAMPTZ,
    exit_price DOUBLE PRECISION,
    exit_reason TEXT NOT NULL DEFAULT 'OPEN',
    
    -- Results
    r_multiple DOUBLE PRECISION DEFAULT 0,
    pnl_gross DOUBLE PRECISION DEFAULT 0,
    fees DOUBLE PRECISION DEFAULT 0,
    slippage DOUBLE PRECISION DEFAULT 0,
    pnl_net DOUBLE PRECISION DEFAULT 0,
    
    -- Score and audit
    setup_score DOUBLE PRECISION NOT NULL,
    trace_json JSONB NOT NULL,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_trades_run_id ON run_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_run_trades_asset ON run_trades(asset);
CREATE INDEX IF NOT EXISTS idx_run_trades_setup ON run_trades(setup_id);
CREATE INDEX IF NOT EXISTS idx_run_trades_run_asset ON run_trades(run_id, asset);

-- Run events table (warnings, info, errors)
CREATE TABLE IF NOT EXISTS run_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json JSONB,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);
CREATE INDEX IF NOT EXISTS idx_run_events_type ON run_events(event_type);

-- Run assets table (for tracking per-asset status)
CREATE TABLE IF NOT EXISTS run_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    asset TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bars_processed INTEGER DEFAULT 0,
    signals_found INTEGER DEFAULT 0,
    skipped BOOLEAN DEFAULT FALSE,
    skip_reason TEXT,
    
    UNIQUE(run_id, asset, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_run_assets_run_id ON run_assets(run_id);

-- =============================================================================
-- DATA LAYER (for backtesting)
-- =============================================================================

-- OHLCV data table (enables real backtesting beyond Binance window limits)
CREATE TABLE IF NOT EXISTS ohlcv (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    
    -- Data provenance
    source TEXT NOT NULL DEFAULT 'binance',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,  -- For data versioning
    
    UNIQUE(symbol, timeframe, open_time)
);

-- Indexes for OHLCV queries
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf ON ohlcv(symbol, timeframe);
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf_time ON ohlcv(symbol, timeframe, open_time DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_batch ON ohlcv(batch_id);

-- Data snapshots (for versioning)
CREATE TABLE IF NOT EXISTS data_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT,
    
    -- Coverage
    symbols_json JSONB NOT NULL,  -- List of symbols
    timeframes_json JSONB NOT NULL,  -- List of timeframes
    start_ts TIMESTAMPTZ NOT NULL,
    end_ts TIMESTAMPTZ NOT NULL,
    bar_count BIGINT NOT NULL,
    
    -- Checksum for verification
    checksum TEXT
);

-- =============================================================================
-- PAPER TRADING (persistent state)
-- =============================================================================

-- Paper positions (survives restarts, enables resilience)
CREATE TABLE IF NOT EXISTS paper_positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    
    -- Position identity
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    side TEXT NOT NULL,
    setup_id TEXT NOT NULL,
    
    -- Status
    status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING, OPEN, CLOSED
    
    -- Signal
    signal_ts TIMESTAMPTZ NOT NULL,
    signal_price DOUBLE PRECISION NOT NULL,
    
    -- Entry
    entry_ts TIMESTAMPTZ,
    entry_price DOUBLE PRECISION,
    entry_price_with_slippage DOUBLE PRECISION,
    
    -- Risk levels (frozen at entry)
    stop_price DOUBLE PRECISION NOT NULL,
    tp1_price DOUBLE PRECISION,
    tp2_price DOUBLE PRECISION,
    r_value DOUBLE PRECISION NOT NULL,
    
    -- Position sizing
    position_size DOUBLE PRECISION NOT NULL,
    notional_value DOUBLE PRECISION NOT NULL,
    risk_amount DOUBLE PRECISION NOT NULL,
    
    -- Current state (updated on each bar)
    current_price DOUBLE PRECISION,
    unrealized_pnl DOUBLE PRECISION DEFAULT 0,
    high_water_mark DOUBLE PRECISION,  -- For trailing stops
    bars_held INTEGER DEFAULT 0,
    
    -- Exit
    exit_ts TIMESTAMPTZ,
    exit_price DOUBLE PRECISION,
    exit_price_with_slippage DOUBLE PRECISION,
    exit_reason TEXT,
    
    -- Results
    realized_pnl DOUBLE PRECISION DEFAULT 0,
    fees_paid DOUBLE PRECISION DEFAULT 0,
    r_multiple DOUBLE PRECISION DEFAULT 0,
    
    -- Audit
    trace_json JSONB NOT NULL,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- One position per asset per run
    UNIQUE(run_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_run_id ON paper_positions(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_run_status ON paper_positions(run_id, status);

-- Paper account state (persistent capital/stats)
CREATE TABLE IF NOT EXISTS paper_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE UNIQUE,
    
    -- Capital
    initial_capital DOUBLE PRECISION NOT NULL,
    current_capital DOUBLE PRECISION NOT NULL,
    peak_capital DOUBLE PRECISION NOT NULL,
    
    -- Stats
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    total_pnl DOUBLE PRECISION DEFAULT 0,
    total_fees DOUBLE PRECISION DEFAULT 0,
    max_drawdown DOUBLE PRECISION DEFAULT 0,
    
    -- Risk state
    current_exposure DOUBLE PRECISION DEFAULT 0,
    daily_pnl DOUBLE PRECISION DEFAULT 0,
    daily_pnl_reset_date DATE,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- CONTROL PLANE (run queue)
-- =============================================================================

-- Run queue (for control plane to enqueue runs)
CREATE TABLE IF NOT EXISTS run_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE UNIQUE,
    
    -- Queue state
    status TEXT NOT NULL DEFAULT 'QUEUED',  -- QUEUED, CLAIMED, COMPLETED, FAILED
    priority INTEGER DEFAULT 0,  -- Higher = more urgent
    
    -- Worker tracking
    claimed_by TEXT,  -- Worker ID
    claimed_at TIMESTAMPTZ,
    
    -- Timestamps
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    
    -- Error handling
    attempt_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_queue_status ON run_queue(status);
CREATE INDEX IF NOT EXISTS idx_run_queue_priority ON run_queue(status, priority DESC, queued_at);
"""
