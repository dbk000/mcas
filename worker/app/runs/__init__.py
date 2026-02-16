"""
Runs Module
===========

Provides reproducible, auditable, immutable execution tracking.

A Run is a fully specified, replayable execution of the engine over a dataset.

Key concepts:
- Runs are immutable - never mutate, create new
- Fingerprint ensures determinism - same inputs = same fingerprint
- Trade traces provide full audit trail for postmortems
- Events capture warnings/errors without spelunking logs
"""

from .models import (
    Run,
    RunType,
    RunStatus,
    RunTrade,
    RunEvent,
    RunSummary,
    UniverseSpec,
    StrategySpec,
    ExecutionSpec,
    DataSpec,
    TradeTrace,
    ExitReason,
    RUNS_SCHEMA,
)

from .repository import RunRepository

__all__ = [
    # Core models
    "Run",
    "RunType",
    "RunStatus",
    "RunTrade",
    "RunEvent",
    "RunSummary",
    
    # Spec models
    "UniverseSpec",
    "StrategySpec",
    "ExecutionSpec",
    "DataSpec",
    "TradeTrace",
    "ExitReason",
    
    # Repository
    "RunRepository",
    
    # Schema
    "RUNS_SCHEMA",
]
