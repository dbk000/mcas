"""
FrozenRegistry — the base class for all MCAS governed registries.

Lifecycle:
    OPEN   → population allowed (only during bootstrap)
    FROZEN → immutable, sealed, process-lethal on missing keys

Design invariants:
    - After freeze(), the _register() method is DELETED from the instance.
      Not guarded by a flag. Deleted. CC cannot call what does not exist.
    - There is no .get(), .resolve(), .lookup(), or __getitem__.
      The only access path is attribute access: registry.thing
    - Missing attributes trigger __getattr__ which calls os._exit().
      Not AttributeError (catchable). Not KeyError (recoverable). Process death.
      getattr(registry, "missing", default) never reaches the default.
    - The seal is a SHA-256 of the frozen contents. It changes if and only if
      governed truth changes. It is not a version counter.

Usage:
    class StrategyRegistry(FrozenRegistry):
        pass

    reg = StrategyRegistry()
    reg._register("D1", d1_config)   # OPEN phase
    reg._register("U1", u1_config)
    reg._freeze()                     # Transition — one-way, destructive
    # reg._register("S9", ...) → AttributeError: 'StrategyRegistry' has no attribute '_register'
    # reg.D1 → d1_config
    # reg.MISSING → os._exit(1)
"""

import hashlib
import json
import os
import sys
from typing import Any


class FrozenRegistry:
    """
    Base class for all MCAS governed registries.

    Two-phase lifecycle with destructive state transition.
    After freeze, the object is structurally immutable — not because
    mutation is guarded, but because mutation methods no longer exist.
    """

    def __init__(self, name: str) -> None:
        # Use object.__setattr__ to bypass our own __setattr__ override
        object.__setattr__(self, '_name', name)  # EXEMPT: FrozenRegistry internal — must bypass own __setattr__
        object.__setattr__(self, '_frozen', False)  # EXEMPT: FrozenRegistry internal
        object.__setattr__(self, '_entries', {})  # EXEMPT: FrozenRegistry internal
        object.__setattr__(self, '_seal', None)  # EXEMPT: FrozenRegistry internal

    def _register(self, key: str, value: Any) -> None:
        """
        Register a governed entry. Only callable during OPEN phase.
        After freeze(), this method does not exist on the instance.
        """
        if self._frozen:
            # Belt-and-suspenders — this line should be unreachable because
            # _register is deleted at freeze time. If somehow reached,
            # the process dies anyway.
            print(
                f"FATAL: Attempted post-freeze registration on {self._name}: {key}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)  # EX_CONFIG

        if key.startswith('_'):
            print(
                f"FATAL: Registry keys must not start with underscore: {key}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        if key in self._entries:
            print(
                f"FATAL: Duplicate registration in {self._name}: {key}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        self._entries[key] = value

    def _freeze(self) -> str:
        """
        Transition OPEN → FROZEN. One-way. Destructive.

        - Computes content-addressed seal (SHA-256)
        - Copies entries to instance attributes (direct attribute access)
        - Deletes _register and _freeze from the instance
        - Activates __setattr__ guard and lethal __getattr__

        Returns the seal string.
        """
        if self._frozen:
            print(
                f"FATAL: Double-freeze attempted on {self._name}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        if not self._entries:
            print(
                f"FATAL: Cannot freeze empty registry: {self._name}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        # Compute seal from canonical sorted representation
        seal_input = json.dumps(
            {k: _stable_repr(v) for k, v in sorted(self._entries.items())},
            sort_keys=True,
            separators=(',', ':'),
        )
        seal = hashlib.sha256(seal_input.encode('utf-8')).hexdigest()

        # Promote entries to instance attributes for direct access
        for key, value in self._entries.items():
            object.__setattr__(self, key, value)  # EXEMPT: FrozenRegistry freeze — promoting entries

        # Store seal and frozen state
        object.__setattr__(self, '_seal', seal)  # EXEMPT: FrozenRegistry freeze
        object.__setattr__(self, '_frozen', True)  # EXEMPT: FrozenRegistry freeze

        # Record the key manifest (for introspection / lineage)
        object.__setattr__(self, '_keys', tuple(sorted(self._entries.keys())))  # EXEMPT: FrozenRegistry freeze

        # === THE DESTRUCTION ===
        # Poison the mutation methods on this instance by overwriting with tombstones.
        # After this point, self._register() and self._freeze() call _TOMBSTONE
        # which kills the process. The class methods still exist for new instances,
        # but THIS instance can never use them.
        object.__setattr__(self, '_register', _TOMBSTONE)  # EXEMPT: FrozenRegistry freeze — tombstoning
        object.__setattr__(self, '_freeze', _TOMBSTONE)  # EXEMPT: FrozenRegistry freeze — tombstoning

        return seal

    def __getattr__(self, name: str) -> Any:
        """
        Called ONLY when normal attribute lookup fails.
        After freeze, this means the key was not registered at bootstrap.
        The process dies. getattr(registry, "missing", default) never
        reaches the default because we call os._exit, not raise.
        """
        if name.startswith('_'):
            # Internal Python attribute lookups (pickling, copying, etc.)
            # should raise normally, not kill the process.
            raise AttributeError(
                f"'{type(self).__name__}' has no attribute '{name}'"
            )

        if not self._frozen:
            raise AttributeError(
                f"Registry '{self._name}' is not yet frozen. "
                f"Call _register() then _freeze() during bootstrap."
            )

        # === PROCESS DEATH ===
        # This is not a recoverable error. A missing registry key means
        # the bootstrap did not provide a dependency that live code requires.
        # The process was built wrong. It must not run.
        print(
            f"\n{'='*72}\n"
            f"FATAL GOVERNANCE VIOLATION — {self._name}\n"
            f"{'='*72}\n"
            f"Requested key : {name}\n"
            f"Available keys: {', '.join(self._keys)}\n"
            f"Registry seal : {self._seal}\n"
            f"\n"
            f"A governed dependency was not registered at bootstrap.\n"
            f"This process cannot run. Fix the bootstrap population.\n"
            f"{'='*72}\n",
            file=sys.stderr,
        )
        sys.stderr.flush()
        os._exit(78)  # EX_CONFIG — configuration error

    def __setattr__(self, name: str, value: Any) -> None:
        """
        After freeze, no attribute can be set. Period.
        Before freeze, only internal attributes (underscore-prefixed) are allowed.
        """
        if self._frozen:
            print(
                f"FATAL: Attempted mutation on frozen registry {self._name}: {name}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        if not name.startswith('_'):
            print(
                f"FATAL: Use _register() to add entries, not direct assignment: {name}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        object.__setattr__(self, name, value)  # EXEMPT: FrozenRegistry — pre-freeze setattr

    def __delattr__(self, name: str) -> None:
        """No deletions. Ever."""
        print(
            f"FATAL: Attempted deletion on registry {self._name}: {name}",
            file=sys.stderr,
        )
        sys.stderr.flush()
        os._exit(78)

    def __repr__(self) -> str:
        state = "FROZEN" if self._frozen else "OPEN"
        count = len(self._entries)
        seal_short = self._seal[:12] if self._seal else "unsealed"
        return f"<{type(self).__name__}({self._name}) {state} entries={count} seal={seal_short}>"


def _TOMBSTONE(*args: Any, **kwargs: Any) -> None:
    """
    Replaces _register and _freeze on frozen instances.
    If somehow called, the process dies.
    """
    print(
        "FATAL: Called a tombstoned method on a frozen registry.",
        file=sys.stderr,
    )
    sys.stderr.flush()
    os._exit(78)


def _stable_repr(value: Any) -> str:
    """
    Produce a stable string representation for seal computation.
    Must be deterministic across runs for the same logical content.
    """
    if isinstance(value, dict):
        return json.dumps(
            {str(k): _stable_repr(v) for k, v in sorted(value.items())},
            sort_keys=True,
            separators=(',', ':'),
        )
    if isinstance(value, (list, tuple)):
        return json.dumps(
            [_stable_repr(v) for v in value],
            separators=(',', ':'),
        )
    if isinstance(value, (int, float, bool)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value)
    if value is None:
        return 'null'
    # For objects: use their qualified class name + repr
    # This ensures the seal changes if the class definition changes location
    return f"{type(value).__module__}.{type(value).__qualname__}:{repr(value)}"
