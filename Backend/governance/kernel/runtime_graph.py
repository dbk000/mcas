"""
RuntimeGraphSeal — the genesis block for a single MCAS process lifetime.

Built once at bootstrap from the seals of all canonical registries.
Immutable after construction. Stamps every governed execution artifact.

The seal is a hash-of-hashes: SHA-256 over the sorted (name, seal) pairs
from every frozen registry. Same governed truth → same seal.

Lineage is the value object that travels with every governed record —
runs, signals, opportunities, positions, tasks, audit outputs.
It is a frozen dataclass: structurally immutable, hashable, serialisable.
"""

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Tuple

from .frozen_registry import FrozenRegistry


@dataclass(frozen=True, slots=True)
class Lineage:
    """
    Immutable provenance stamp for every governed MCAS artifact.

    Travels with: runs, signals, opportunities, paper/live positions,
    simulation results, task payloads, audit outputs.

    frozen=True → __setattr__ raises FrozenInstanceError (Python-enforced)
    slots=True  → no __dict__, no dynamic attribute injection
    """
    runtime_graph_seal: str
    registry_seals: Tuple[Tuple[str, str], ...]  # ((name, seal), ...)
    bootstrap_epoch: float  # time.time() at seal creation
    process_id: str  # unique per bootstrap (seal + epoch hash)

    def to_dict(self) -> dict:
        """Serialise for persistence / API exposure."""
        return {
            'runtime_graph_seal': self.runtime_graph_seal,
            'registry_seals': {name: seal for name, seal in self.registry_seals},
            'bootstrap_epoch': self.bootstrap_epoch,
            'process_id': self.process_id,
        }

    def verify_against(self, other_seal: str) -> bool:
        """
        Check if a payload's seal matches this process's seal.
        Used by signal_worker and run_executor for peer rejection.
        """
        return self.runtime_graph_seal == other_seal


class RuntimeGraphSeal:
    """
    Aggregates all frozen registry seals into one canonical process seal.

    Usage:
        graph = RuntimeGraphSeal()
        graph.include(config_registry)    # must already be frozen
        graph.include(strategy_registry)
        graph.include(task_registry)
        graph.include(stop_policy_registry)
        lineage = graph.seal()
        # graph.include(...) → process death (sealed)
        # graph.seal() → process death (already sealed)
    """

    def __init__(self) -> None:
        self._registry_seals: Dict[str, str] = {}
        self._sealed = False
        self._lineage: Lineage | None = None

    def include(self, registry: FrozenRegistry) -> None:
        """
        Include a frozen registry's seal in the graph.
        The registry MUST be frozen — including an unfrozen registry
        kills the process.
        """
        if self._sealed:
            print(
                f"FATAL: Attempted to include registry after graph seal: "
                f"{registry._name}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        if not registry._frozen:
            print(
                f"FATAL: Cannot include unfrozen registry in runtime graph: "
                f"{registry._name}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        if registry._name in self._registry_seals:
            print(
                f"FATAL: Duplicate registry in runtime graph: {registry._name}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        self._registry_seals[registry._name] = registry._seal

    def seal(self) -> Lineage:
        """
        Compute the runtime graph seal and produce the canonical Lineage.

        One-way. After this call, no more registries can be included
        and seal() cannot be called again.

        Returns the Lineage object that must be stamped on every
        governed artifact for the lifetime of this process.
        """
        if self._sealed:
            print(
                "FATAL: Runtime graph already sealed. "
                "Double-seal is a governance violation.",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        if not self._registry_seals:
            print(
                "FATAL: Cannot seal empty runtime graph. "
                "No registries were included.",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        # Hash of hashes — sorted by registry name for determinism
        sorted_pairs = tuple(sorted(self._registry_seals.items()))
        seal_input = json.dumps(
            sorted_pairs,
            separators=(',', ':'),
        )
        graph_seal = hashlib.sha256(seal_input.encode('utf-8')).hexdigest()

        epoch = time.time()

        # Process ID = hash of (seal + epoch) — unique per bootstrap
        process_id_input = f"{graph_seal}:{epoch}"
        process_id = hashlib.sha256(
            process_id_input.encode('utf-8')
        ).hexdigest()[:16]

        lineage = Lineage(
            runtime_graph_seal=graph_seal,
            registry_seals=sorted_pairs,
            bootstrap_epoch=epoch,
            process_id=process_id,
        )

        self._sealed = True
        self._lineage = lineage

        # Destroy the include method on this instance
        self.include = _graph_tombstone  # type: ignore[assignment]
        self.seal = _graph_tombstone  # type: ignore[assignment]

        return lineage

    @property
    def lineage(self) -> Lineage:
        """Access the lineage after sealing. Crashes if not yet sealed."""
        if not self._sealed or self._lineage is None:
            print(
                "FATAL: Accessed lineage before runtime graph was sealed.",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)
        return self._lineage


def _graph_tombstone(*args, **kwargs):
    """Post-seal tombstone. Process death if called."""
    print(
        "FATAL: Called a tombstoned method on a sealed RuntimeGraphSeal.",
        file=sys.stderr,
    )
    sys.stderr.flush()
    os._exit(78)
