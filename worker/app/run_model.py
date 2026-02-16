"""
Run Model
=========

A Run is a fully specified, replayable execution of the engine over a dataset.

Run types:
- BACKTEST: Historical simulation, bar-by-bar
- SCAN: Find setups/alerts without simulating PnL (current worker mode)
- PAPER: Forward test, simulated execution on live data
- LIVE: Real execution
- REPLAY: Rerun of a previous Run with identical inputs for verification

Key principle: Runs are IMMUTABLE. Never mutate a Run - create a new one.
"""

import json
import hashlib
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Any
from enum import Enum


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


@dataclass
class UniverseSpec:
    """Resolved universe specification - stored at execution time."""
    assets: list[str]  # Resolved asset list (not the rule, the actual list)
    timeframes: list[str]
    start_ts: Optional[int] = None  # Unix timestamp, None for SCAN
    end_ts: Optional[int] = None
    venue: str = "binance"
    session_filter: Optional[str] = None  # e.g., "UTC_0800_1600"
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategySpec:
    """Strategy specification."""
    setup_id: str  # e.g., "S1", "S2"
    setup_version: str  # e.g., "v2.0", "v1.1"
    setup_params: dict  # Full parameter set
    bias_mode: str = "HTF_TREND_PROXY"
    risk_model_id: str = "FIXED_R"
    risk_params: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionSpec:
    """Execution model specification - where truth lives."""
    entry_mode: str = "NEXT_OPEN"  # NEXT_OPEN, MOC, LIMIT_RETEST
    slippage_bps: float = 5.0  # Basis points
    fee_bps: float = 10.0  # Basis points (maker + taker avg)
    fill_model: str = "BAR_CLOSE"  # BAR_CLOSE, PARTIAL, etc.
    max_positions: int = 10
    one_per_asset: bool = True
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DataSpec:
    """Data versioning specification."""
    data_source: str = "binance_rest"
    data_version: str = ""  # Ingestion batch ID or "as of" timestamp
    data_gaps_policy: str = "FORWARD_FILL"  # FORWARD_FILL, DROP_BARS, SKIP_ASSET
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunSummary:
    """Summary metrics for a completed Run."""
    bar_count: int = 0
    asset_count: int = 0
    signal_count: int = 0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_r: float = 0.0
    max_drawdown_r: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    duration_ms: int = 0
    warnings: int = 0
    errors: int = 0
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Run:
    """
    A fully specified, replayable execution of the engine.
    
    Usage:
        run = Run.create_scan(
            name="Hourly scan",
            universe=UniverseSpec(assets=["BTCUSDT", ...], timeframes=["1h"]),
            strategy=StrategySpec(setup_id="S1", setup_version="v2.0", setup_params={...}),
        )
        # Worker claims and executes
        run.start()
        # ... execute ...
        run.complete(summary=RunSummary(...))
    """
    id: str
    run_type: RunType
    status: RunStatus
    fingerprint: str
    
    # Human identity
    name: Optional[str] = None
    notes: Optional[str] = None
    
    # Specs
    universe: UniverseSpec = field(default_factory=lambda: UniverseSpec(assets=[], timeframes=[]))
    strategy: StrategySpec = field(default_factory=lambda: StrategySpec(setup_id="", setup_version="", setup_params={}))
    execution: ExecutionSpec = field(default_factory=ExecutionSpec)
    data: DataSpec = field(default_factory=DataSpec)
    
    # Environment
    engine_version: str = ""  # Git SHA or docker tag
    
    # Execution tracking
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    
    # Results
    summary: Optional[RunSummary] = None
    error_text: Optional[str] = None
    
    # Lineage
    parent_run_id: Optional[str] = None  # For REPLAY runs
    
    @classmethod
    def create(
        cls,
        run_type: RunType,
        universe: UniverseSpec,
        strategy: StrategySpec,
        execution: Optional[ExecutionSpec] = None,
        data: Optional[DataSpec] = None,
        engine_version: str = "",
        name: Optional[str] = None,
        notes: Optional[str] = None,
        parent_run_id: Optional[str] = None,
    ) -> "Run":
        """Create a new Run with computed fingerprint."""
        execution = execution or ExecutionSpec()
        data = data or DataSpec()
        
        # Generate fingerprint from all effective inputs
        fingerprint = cls._compute_fingerprint(
            run_type=run_type,
            universe=universe,
            strategy=strategy,
            execution=execution,
            data=data,
            engine_version=engine_version,
        )
        
        return cls(
            id=str(uuid.uuid4()),
            run_type=run_type,
            status=RunStatus.PENDING,
            fingerprint=fingerprint,
            name=name,
            notes=notes,
            universe=universe,
            strategy=strategy,
            execution=execution,
            data=data,
            engine_version=engine_version,
            created_at=datetime.now(timezone.utc),
            parent_run_id=parent_run_id,
        )
    
    @classmethod
    def create_scan(
        cls,
        universe: UniverseSpec,
        strategy: StrategySpec,
        engine_version: str = "",
        name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> "Run":
        """Convenience method for SCAN runs (current worker mode)."""
        return cls.create(
            run_type=RunType.SCAN,
            universe=universe,
            strategy=strategy,
            engine_version=engine_version,
            name=name,
            notes=notes,
        )
    
    @staticmethod
    def _compute_fingerprint(
        run_type: RunType,
        universe: UniverseSpec,
        strategy: StrategySpec,
        execution: ExecutionSpec,
        data: DataSpec,
        engine_version: str,
    ) -> str:
        """
        Compute deterministic fingerprint from all effective inputs.
        
        If anything changes, fingerprint changes.
        No more "why did this backtest change?" mystery.
        """
        canonical = {
            "run_type": run_type.value,
            "engine_version": engine_version,
            "universe": {
                "assets": sorted(universe.assets),  # Sort for determinism
                "timeframes": sorted(universe.timeframes),
                "start_ts": universe.start_ts,
                "end_ts": universe.end_ts,
                "venue": universe.venue,
            },
            "strategy": {
                "setup_id": strategy.setup_id,
                "setup_version": strategy.setup_version,
                "setup_params": strategy.setup_params,
                "bias_mode": strategy.bias_mode,
                "risk_model_id": strategy.risk_model_id,
                "risk_params": strategy.risk_params,
            },
            "execution": {
                "entry_mode": execution.entry_mode,
                "slippage_bps": execution.slippage_bps,
                "fee_bps": execution.fee_bps,
                "fill_model": execution.fill_model,
            },
            "data": {
                "data_source": data.data_source,
                "data_version": data.data_version,
                "data_gaps_policy": data.data_gaps_policy,
            },
        }
        
        # Canonical JSON with sorted keys
        canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        
        return hashlib.sha256(canonical_json.encode()).hexdigest()[:16]
    
    def start(self):
        """Mark run as started (called by worker)."""
        if self.status != RunStatus.PENDING:
            raise ValueError(f"Cannot start run in status {self.status}")
        self.status = RunStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)
    
    def complete(self, summary: RunSummary):
        """Mark run as successfully completed."""
        if self.status != RunStatus.RUNNING:
            raise ValueError(f"Cannot complete run in status {self.status}")
        self.status = RunStatus.SUCCESS
        self.finished_at = datetime.now(timezone.utc)
        self.summary = summary
    
    def fail(self, error: str):
        """Mark run as failed."""
        self.status = RunStatus.FAILED
        self.finished_at = datetime.now(timezone.utc)
        self.error_text = error
    
    def cancel(self):
        """Mark run as canceled."""
        self.status = RunStatus.CANCELED
        self.finished_at = datetime.now(timezone.utc)
    
    def to_dict(self) -> dict:
        """Serialize to dict for DB storage."""
        return {
            "id": self.id,
            "run_type": self.run_type.value,
            "status": self.status.value,
            "fingerprint": self.fingerprint,
            "name": self.name,
            "notes": self.notes,
            "universe_json": json.dumps(self.universe.to_dict()),
            "strategy_json": json.dumps(self.strategy.to_dict()),
            "execution_json": json.dumps(self.execution.to_dict()),
            "data_json": json.dumps(self.data.to_dict()),
            "engine_version": self.engine_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "summary_json": json.dumps(self.summary.to_dict()) if self.summary else None,
            "error_text": self.error_text,
            "parent_run_id": self.parent_run_id,
        }


@dataclass
class RunTrade:
    """
    A trade within a Run, with full audit trail.
    
    The trace_json contains the exact indicator values at entry -
    this is gold for postmortems.
    """
    id: str
    run_id: str
    asset: str
    timeframe: str
    side: str  # "long" or "short"
    setup_id: str
    
    # Entry
    entry_ts: int
    entry_px: float
    entry_bar_idx: int
    
    # Stops/targets at entry (frozen)
    stop_px: float
    tp1_px: float
    tp2_px: float
    r_value: float
    
    # Exit (filled after trade closes)
    exit_ts: Optional[int] = None
    exit_px: Optional[float] = None
    exit_reason: Optional[str] = None  # TP1, TP2, STOP, TIME_STOP, FAIL_FAST, TRAIL
    exit_bar_idx: Optional[int] = None
    
    # Results
    r_multiple: Optional[float] = None
    pnl_gross: Optional[float] = None
    fees: float = 0.0
    slippage: float = 0.0
    pnl_net: Optional[float] = None
    
    # Audit trail - the exact state at entry
    trace_json: str = "{}"  # Full indicator values + gate states
    
    @classmethod
    def create(
        cls,
        run_id: str,
        asset: str,
        timeframe: str,
        side: str,
        setup_id: str,
        entry_ts: int,
        entry_px: float,
        entry_bar_idx: int,
        stop_px: float,
        tp1_px: float,
        tp2_px: float,
        r_value: float,
        trace: dict,
    ) -> "RunTrade":
        return cls(
            id=str(uuid.uuid4()),
            run_id=run_id,
            asset=asset,
            timeframe=timeframe,
            side=side,
            setup_id=setup_id,
            entry_ts=entry_ts,
            entry_px=entry_px,
            entry_bar_idx=entry_bar_idx,
            stop_px=stop_px,
            tp1_px=tp1_px,
            tp2_px=tp2_px,
            r_value=r_value,
            trace_json=json.dumps(trace),
        )
    
    def close(
        self,
        exit_ts: int,
        exit_px: float,
        exit_reason: str,
        exit_bar_idx: int,
        fees: float = 0.0,
        slippage: float = 0.0,
    ):
        """Close the trade with exit details."""
        self.exit_ts = exit_ts
        self.exit_px = exit_px
        self.exit_reason = exit_reason
        self.exit_bar_idx = exit_bar_idx
        self.fees = fees
        self.slippage = slippage
        
        # Calculate R-multiple
        if self.side == "long":
            self.r_multiple = (exit_px - self.entry_px) / self.r_value
            self.pnl_gross = exit_px - self.entry_px
        else:
            self.r_multiple = (self.entry_px - exit_px) / self.r_value
            self.pnl_gross = self.entry_px - exit_px
        
        self.pnl_net = self.pnl_gross - fees - slippage
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunEvent:
    """
    An event within a Run for debugging without spelunking logs.
    """
    id: str
    run_id: str
    ts: datetime
    event_type: str  # INFO, WARN, ERROR
    message: str
    payload_json: str = "{}"
    
    @classmethod
    def info(cls, run_id: str, message: str, payload: Optional[dict] = None) -> "RunEvent":
        return cls(
            id=str(uuid.uuid4()),
            run_id=run_id,
            ts=datetime.now(timezone.utc),
            event_type="INFO",
            message=message,
            payload_json=json.dumps(payload or {}),
        )
    
    @classmethod
    def warn(cls, run_id: str, message: str, payload: Optional[dict] = None) -> "RunEvent":
        return cls(
            id=str(uuid.uuid4()),
            run_id=run_id,
            ts=datetime.now(timezone.utc),
            event_type="WARN",
            message=message,
            payload_json=json.dumps(payload or {}),
        )
    
    @classmethod
    def error(cls, run_id: str, message: str, payload: Optional[dict] = None) -> "RunEvent":
        return cls(
            id=str(uuid.uuid4()),
            run_id=run_id,
            ts=datetime.now(timezone.utc),
            event_type="ERROR",
            message=message,
            payload_json=json.dumps(payload or {}),
        )
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "ts": self.ts.isoformat(),
            "event_type": self.event_type,
            "message": self.message,
            "payload_json": self.payload_json,
        }
