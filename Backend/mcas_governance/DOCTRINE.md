# MCAS Governance Doctrine

**One bootstrap builds one sealed runtime graph from canonical registries; every governed execution must carry lineage from that graph or be rejected.**

---

## The Mechanism

Dynamic logic is not prohibited. It is **mechanically impossible**.

The `FrozenRegistry` base class has a two-phase lifecycle:

1. **OPEN** — exists only during bootstrap. Registries accept `_register()` calls.
2. **FROZEN** — exists for the entire process lifetime thereafter. The `_register()` and `_freeze()` methods are **deleted from the instance** (replaced with tombstones that kill the process). There is no `.get()`, `.resolve()`, `.lookup()`, or `__getitem__`. The only access path is `registry.thing`. Missing attributes call `sys.exit(78)` — not `AttributeError`, not `KeyError`. Process death. `getattr(registry, "missing", default)` never reaches the default.

After freeze, `__setattr__` kills the process. `__delattr__` kills the process. The registry is structurally immutable — not because mutation is guarded by a flag, but because the mutation API no longer exists on the object.

## The Seal

Each registry computes a SHA-256 seal over its canonical sorted contents at freeze time. The `RuntimeGraphSeal` aggregates all registry seals into one hash-of-hashes — the **genesis block** for this process lifetime.

The seal changes if and only if governed truth changes. It is not a version counter.

## Lineage

Every governed artifact — runs, signals, opportunities, positions, tasks, audit outputs — carries a `Lineage` frozen dataclass:
- `runtime_graph_seal` — the genesis block hash
- `registry_seals` — individual registry hashes
- `bootstrap_epoch` — when this process booted
- `process_id` — unique per bootstrap

## Peer Shunning

`LineageEnforcer` is a mixin for signal_worker, run_executor, persistence, and any component that processes governed payloads. It calls `enforce_lineage(payload)` which kills the process if:
- The payload has no `runtime_graph_seal` (unsealed work)
- The payload's seal doesn't match the active process seal (foreign lineage)

## Import Boundary Enforcement

`core_logic/` is pure math. It physically cannot import from `governance/` or `api/`.

`install_import_guard()` installs a `sys.meta_path` hook at bootstrap that intercepts every import. It walks the call stack past importlib internals to find the real caller module. If `core_logic` attempts to import `governance` or `api`, or if `governance` attempts to import `api`, the process dies at import time — the module never loads.

This is not a CI check. CC cannot write `from governance.signal_worker import X` inside `core_logic/` and have it run. The import itself kills the process before the module's first line executes.

Called once in the bootstrap entrypoint, before any application imports:
```python
from mcas_governance.import_guard import install_import_guard
install_import_guard()
```

## CI Doctrine Gates

AST-level static analysis (not grep) that fails the build on:
- `os.getenv` / `os.environ` outside `bootstrap.py`
- `getattr(obj, name, default)` with 3 args (silent defaulting)
- `hasattr()` (existence checks = dynamic dispatch)
- `importlib.import_module()` (runtime imports)
- `setattr()` (runtime mutation)
- `inspect` module (reflective behaviour)
- `globals()` / `locals()` for dispatch

Run as: `python -m mcas_governance.ci_gates <path>`

## What CC Must Do

1. **Every new module** inherits from or receives a frozen registry. No module reads env, constructs registries, or resolves config. That is bootstrap's job.
2. **Every governed function** receives `Lineage` as an explicit parameter. No globals.
3. **Every persistence write** stamps lineage alongside the record.
4. **Every worker/executor** calls `enforce_lineage()` before processing any payload.
5. **Every new registry entry** goes through `_register()` during bootstrap population. There is no other path.

## What CC Must Not Do

CC cannot write `registry.get("key")` — `.get()` does not exist.
CC cannot write `registry.register("key", val)` post-freeze — `_register` is a tombstone.
CC cannot write `getattr(registry, "key", default)` — the process dies before the default is reached.
CC cannot write `os.getenv("VAR")` outside bootstrap — CI gates reject it.
CC cannot write `importlib.import_module(name)` — CI gates reject it.
CC cannot write `hasattr(obj, "method")` — CI gates reject it.

These are not rules CC must remember. They are walls with no doors.

## Files

```
mcas_governance/
├── __init__.py           # Public API surface
├── frozen_registry.py    # FrozenRegistry base class
├── runtime_graph.py      # RuntimeGraphSeal + Lineage
├── bootstrap.py          # Genesis block builder + registry subclasses + LineageEnforcer
├── import_guard.py       # sys.meta_path hook — reversed dependency arrows = process death
├── ci_gates.py           # AST-level static analysis enforcement
└── test_proof.py         # 24/24 proof suite
```

## Proof Results

```
24 passed, 0 failed out of 24
CI DOCTRINE GATES: CLEAN (self-scan)
CI DOCTRINE GATES: 8/8 violations caught (dirty file scan)
```
