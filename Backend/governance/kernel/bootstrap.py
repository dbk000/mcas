"""
Bootstrap — the genesis block builder for MCAS.

This module is the ONLY place where:
    - Environment variables are read (os.getenv)
    - Registries are populated
    - Registries are frozen
    - The runtime graph is sealed
    - The process-wide Lineage is made available

Everything downstream receives Lineage as a dependency.
Nothing downstream reads env, constructs registries, or resolves config.

If this module fails, the process is dead. That is correct behaviour.
A process that cannot build its genesis block has no right to run.

Usage:
    from governance.kernel.bootstrap import bootstrap
    lineage = bootstrap()
    # lineage is now the canonical Lineage for this process lifetime
    # Pass it explicitly to every governed component. No globals.
"""

import os
import sys
from typing import Any, Callable, Dict

from .frozen_registry import FrozenRegistry
from .runtime_graph import Lineage, RuntimeGraphSeal


# =============================================================================
# Registry Definitions
# =============================================================================
# Each registry is a FrozenRegistry subclass — no additional behaviour.
# The subclass exists only for type discrimination and clear repr.

class ConfigRegistry(FrozenRegistry):
    """Process-wide configuration values. Read from env at bootstrap only."""
    pass


class StrategyRegistry(FrozenRegistry):
    """
    Canonical strategy definitions.
    Each entry carries: setup_id, strategy_version, config_version,
    stop_policy_version, timeframe_affinity, dispatch_mode.
    """
    pass


class TaskRegistry(FrozenRegistry):
    """
    Governed task specifications.
    Each entry carries: task_id, handler identity, runtime family,
    version lineage.
    """
    pass


class StopPolicyRegistry(FrozenRegistry):
    """
    Exit / stop-loss policy definitions.
    Keyed by policy_id, carrying all parameters.
    """
    pass


# =============================================================================
# Bootstrap Orchestration
# =============================================================================

def bootstrap(
    *,
    config_populator: Callable[['ConfigRegistry'], None] | None = None,
    strategy_populator: Callable[['StrategyRegistry'], None] | None = None,
    task_populator: Callable[['TaskRegistry'], None] | None = None,
    stop_policy_populator: Callable[['StopPolicyRegistry'], None] | None = None,
) -> Lineage:
    """
    Build the genesis block.

    Populator callables receive an OPEN registry and must call
    _register() for every governed entry. If a populator is None,
    it means the registry population logic has not been implemented yet —
    the process dies with NotImplementedError, because every function
    does real work or raises NotImplementedError. There is no third option.

    Returns:
        Lineage — the immutable provenance stamp for this process lifetime.
    """
    print(f"[BOOTSTRAP] Building genesis block...", file=sys.stderr)

    # --- Phase 1: Create registries (OPEN) ---
    config_reg = ConfigRegistry("config")
    strategy_reg = StrategyRegistry("strategy")
    task_reg = TaskRegistry("task")
    stop_policy_reg = StopPolicyRegistry("stop_policy")

    # --- Phase 2: Populate from canonical sources ---
    # Config is special — it reads env. This is the ONLY place env is read.
    if config_populator is None:
        raise NotImplementedError(
            "config_populator must be provided. "
            "It reads os.getenv and calls registry._register() for each value."
        )
    config_populator(config_reg)

    if strategy_populator is None:
        raise NotImplementedError(
            "strategy_populator must be provided. "
            "It registers all canonical strategy definitions."
        )
    strategy_populator(strategy_reg)

    if task_populator is None:
        raise NotImplementedError(
            "task_populator must be provided. "
            "It registers all canonical task specifications."
        )
    task_populator(task_reg)

    if stop_policy_populator is None:
        raise NotImplementedError(
            "stop_policy_populator must be provided. "
            "It registers all stop/exit policy definitions."
        )
    stop_policy_populator(stop_policy_reg)

    # --- Phase 3: Freeze all registries ---
    config_seal = config_reg._freeze()
    strategy_seal = strategy_reg._freeze()
    task_seal = task_reg._freeze()
    stop_seal = stop_policy_reg._freeze()

    print(f"[BOOTSTRAP] Config seal    : {config_seal[:12]}...", file=sys.stderr)
    print(f"[BOOTSTRAP] Strategy seal  : {strategy_seal[:12]}...", file=sys.stderr)
    print(f"[BOOTSTRAP] Task seal      : {task_seal[:12]}...", file=sys.stderr)
    print(f"[BOOTSTRAP] StopPolicy seal: {stop_seal[:12]}...", file=sys.stderr)

    # --- Phase 4: Build and seal the runtime graph ---
    graph = RuntimeGraphSeal()
    graph.include(config_reg)
    graph.include(strategy_reg)
    graph.include(task_reg)
    graph.include(stop_policy_reg)
    lineage = graph.seal()

    print(
        f"[BOOTSTRAP] Runtime graph sealed: {lineage.runtime_graph_seal[:12]}...",
        file=sys.stderr,
    )
    print(
        f"[BOOTSTRAP] Process ID: {lineage.process_id}",
        file=sys.stderr,
    )
    print(f"[BOOTSTRAP] Genesis block complete.", file=sys.stderr)

    return lineage


# =============================================================================
# Lineage Enforcement Mixin
# =============================================================================

class LineageEnforcer:
    """
    Mixin for any component that must reject foreign lineage.

    Used by signal_worker, run_executor, persistence, and any other
    component that processes governed payloads.

    Usage:
        class SignalWorker(LineageEnforcer):
            def __init__(self, lineage: Lineage):
                super().__init__(lineage)

            def process(self, payload: dict):
                self.enforce_lineage(payload)  # dies if foreign
                # ... real work ...
    """

    def __init__(self, lineage: Lineage) -> None:
        self._active_lineage = lineage

    def enforce_lineage(self, payload: Dict[str, Any]) -> None:
        """
        Verify that a payload carries the active process seal.
        If the seal is missing or foreign, the process dies.

        This is peer shunning: governed components reject work
        that was not produced under the same genesis block.
        """
        payload_seal = payload.get('runtime_graph_seal')

        if payload_seal is None:
            print(
                f"\n{'='*72}\n"
                f"FATAL LINEAGE VIOLATION — {type(self).__name__}\n"
                f"{'='*72}\n"
                f"Received payload with NO lineage seal.\n"
                f"Active process seal: {self._active_lineage.runtime_graph_seal[:12]}...\n"
                f"Payload keys: {sorted(payload.keys())}\n"
                f"\n"
                f"Every governed payload must carry runtime_graph_seal.\n"
                f"This payload was produced outside canonical governance.\n"
                f"{'='*72}\n",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        if not self._active_lineage.verify_against(payload_seal):
            print(
                f"\n{'='*72}\n"
                f"FATAL LINEAGE VIOLATION — {type(self).__name__}\n"
                f"{'='*72}\n"
                f"Payload seal   : {payload_seal[:12]}...\n"
                f"Active seal    : {self._active_lineage.runtime_graph_seal[:12]}...\n"
                f"\n"
                f"This payload was produced under a different genesis block.\n"
                f"Possible causes:\n"
                f"  - Stale payload from a previous process\n"
                f"  - Payload from a differently-configured instance\n"
                f"  - Manual tampering\n"
                f"{'='*72}\n",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        # Defence in depth: verify process_id matches even if seal matched
        payload_process_id = payload.get('process_id')
        if payload_process_id != self._active_lineage.process_id:
            print(
                f"FATAL LINEAGE VIOLATION — {type(self).__name__}\n"
                f"Payload process_id : {payload_process_id}\n"
                f"Active process_id  : {self._active_lineage.process_id}\n"
                f"Seal matched but process identity does not.\n",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

    def stamp_lineage(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stamp the active lineage onto an outgoing record.
        Used by persistence and any producer of governed artifacts.
        """
        record['runtime_graph_seal'] = self._active_lineage.runtime_graph_seal
        record['process_id'] = self._active_lineage.process_id
        record['bootstrap_epoch'] = self._active_lineage.bootstrap_epoch
        return record
