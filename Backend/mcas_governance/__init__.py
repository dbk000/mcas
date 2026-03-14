"""
MCAS Governance — Sealed Runtime Graph Architecture.

One bootstrap builds one sealed runtime graph from canonical registries;
every governed execution must carry lineage from that graph or be rejected.
"""

from .frozen_registry import FrozenRegistry
from .runtime_graph import Lineage, RuntimeGraphSeal
from .bootstrap import (
    ConfigRegistry,
    StrategyRegistry,
    TaskRegistry,
    StopPolicyRegistry,
    LineageEnforcer,
    bootstrap,
)
from .import_guard import install_import_guard

__all__ = [
    'FrozenRegistry',
    'Lineage',
    'RuntimeGraphSeal',
    'ConfigRegistry',
    'StrategyRegistry',
    'TaskRegistry',
    'StopPolicyRegistry',
    'LineageEnforcer',
    'bootstrap',
    'install_import_guard',
]
