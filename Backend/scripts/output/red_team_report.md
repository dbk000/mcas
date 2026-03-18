# MCAS Governance Kernel — Red Team Report

**Date:** 2026-03-15
**Auditor:** Claude Opus 4.6 (adversarial mode)
**Scope:** governance/kernel/, governance/bootstrap.py, governance/persistence.py, hooks/pre-commit

**Summary:** 7 CRITICAL, 6 HIGH, 3 MEDIUM, 2 LOW findings across all five attack surfaces.

---

## Attack Surface 1: FrozenRegistry Bypass

---

### Attack: os._exit monkey-patch
**Target:** FrozenRegistry process-death mechanism
**Method:** `os._exit` is a module attribute. Reassigning it with `os._exit = lambda c: None` before governance loads neutralises ALL process-death calls in the entire codebase. The CI gates scan for `object.__setattr__`, `type.__setattr__`, and `__dunder__ =` patterns — but `os._exit` is a single-underscore attribute on a module, matching none of those rules.
**Result:** BYPASSED
**Evidence:**
```python
# governance_killer.py — passes all CI gates
import os
os._exit = lambda code: None   # Silences all governance kills

# Now import and abuse the governance kernel freely
from governance.kernel.frozen_registry import FrozenRegistry
reg = FrozenRegistry("evil")
reg._register("a", 1)
reg._freeze()
reg.nonexistent  # Would normally kill process — now silently returns None
```
CI gate scan result: `CI DOCTRINE GATES: CLEAN` — no rules match `os._exit = ...`
**Severity:** CRITICAL
**Fix:** Add a CI gate rule in `visit_Assign` that flags assignments to `os._exit`, `os.kill`, `os.abort`, `signal.signal`. Or use a hash check at call sites: before calling `os._exit`, verify `os._exit is _original_exit` where `_original_exit` was captured at module import time in a closure.

---

### Attack: _entries dict post-freeze mutation
**Target:** FrozenRegistry internal state
**Method:** After freeze, `self._entries` remains on the instance as a plain mutable dict. It's never cleared, frozen, or deleted. Since `_entries` starts with `_`, `__getattr__` raises `AttributeError` (not process death), but normal attribute lookup finds it because it was set in `__init__` via `object.__setattr__`.
**Result:** PARTIAL
**Evidence:**
```python
# After freeze, _entries is still a live mutable dict
registry._entries["EVIL"] = "injected"   # Succeeds silently
registry._entries["D1"] = "overwritten"  # Succeeds silently

# BUT: registry.D1 still returns the original value (promoted to instance attr)
# And registry.EVIL triggers __getattr__ → os._exit(78)
# because "EVIL" was not promoted at freeze time
```
The dict is mutable but non-authoritative post-freeze. Entries promoted to instance attributes are the authority. Mutating `_entries` does not affect governed access.
**Severity:** LOW
**Fix:** Clear `_entries` after freeze: `object.__setattr__(self, '_entries', types.MappingProxyType({}))` in `_freeze()`. Or just `del self._entries` ... but `__delattr__` kills the process. Use `object.__delattr__(self, '_entries')`.

---

### Attack: vars() / __dict__ read-only extraction (via builtins module)
**Target:** FrozenRegistry internal state
**Method:** CI gates block `vars()` and `.__dict__`. But `import builtins; builtins.vars(registry)` is an `ast.Attribute` call, not an `ast.Name` call. The CI gate for VARS_INTROSPECTION checks `isinstance(func, ast.Name) and func.id == 'vars'` — the Attribute form doesn't match.
**Result:** BYPASSED
**Evidence:**
```python
import builtins
state = builtins.vars(registry)  # Returns full __dict__ — PASSES CI gates
# state == {'_name': 'config', '_frozen': True, '_entries': {...},
#           '_seal': '...', '_keys': (...), 'D1': ..., 'U1': ..., ...}
```
Read-only extraction of all frozen state. No process death because `vars()` doesn't trigger `__getattr__`.
**Severity:** MEDIUM
**Fix:** Extend all function-call CI gate rules to also match `ast.Attribute` forms where `node.attr` matches the forbidden name. See the systemic fix under Attack Surface 3.

---

### Attack: Race the freeze (thread injection)
**Target:** Registry population window
**Method:** Between `ConfigRegistry("config")` and `config_reg._freeze()`, the registry is OPEN and accepts `_register()` calls. If a populator started a background thread with a reference to the registry, that thread could inject entries.
**Result:** BLOCKED (by design)
**Evidence:** Registries are local variables in `bootstrap_genesis()`. They're not exposed globally until Phase 6 (after freeze). Populators run synchronously. No thread or async boundary exists between population and freeze. The window is architecturally sealed.
**Severity:** N/A (blocked)

---

### Attack: __class__ swap to unfrozen subclass
**Target:** FrozenRegistry immutability
**Method:** `registry.__class__ = UnfrozenClass` would swap the class, removing `__setattr__` and `__getattr__` guards.
**Result:** BLOCKED
**Evidence:** CI gates catch this via DUNDER_REASSIGNMENT rule: `target.attr.startswith('__') and target.attr.endswith('__')` and target is not `self`. The pattern `registry.__class__ = X` matches.
**Severity:** N/A (blocked)

---

### Attack: ctypes memory write
**Target:** FrozenRegistry frozen state
**Method:** `import ctypes; ctypes.pythonapi.PyObject_SetAttr(...)` to bypass Python-level `__setattr__` guards and write directly to the C-level object.
**Result:** BYPASSED (theoretically)
**Evidence:**
```python
import ctypes
# ctypes is not blocked by CI gates (no rule for it)
# Could use ctypes.pythonapi to call CPython C functions that bypass __setattr__
```
CI gates block `import inspect` but NOT `import ctypes`. However, `ctypes.pythonapi.PyObject_SetAttr` would require constructing `py_object` wrappers and knowing the exact C calling convention. Fragile and version-dependent.
**Severity:** MEDIUM (theoretically BYPASSED but impractical — requires CPython internals knowledge, fragile across versions)
**Fix:** Add `ctypes` and `ctypes.pythonapi` to CI gate forbidden imports.

---

## Attack Surface 2: Import Guard Bypass

---

### Attack: sys.modules cache bypass (pre-loaded modules)
**Target:** Import boundary enforcer (sys.meta_path hook)
**Method:** Python's import system checks `sys.modules` before calling meta_path finders. If `governance.kernel` is already loaded (which it always is after bootstrap), `from governance.kernel import FrozenRegistry` inside `core_logic/` never invokes `find_spec`. The import guard is completely inert.
**Result:** BYPASSED
**Evidence:**
```python
# core_logic/evil_math.py — imported AFTER bootstrap completes

def do_evil():
    """Called at runtime, not at import time."""
    # By this point, governance.kernel is in sys.modules.
    # Python returns the cached module without calling any meta_path finders.
    from governance.kernel.frozen_registry import FrozenRegistry
    # ^ Import guard's find_spec() is NEVER called
    # core_logic has now imported from governance — boundary violated

    from governance.bootstrap import _LINEAGE, _CONFIG
    # ^ Direct access to module-level singletons
```

Python's import protocol (CPython `importlib._bootstrap._find_and_load`):
```python
module = sys.modules.get(name, _NEEDS_LOADING)
if module is _NEEDS_LOADING:
    return _find_and_load_unlocked(name, import_)  # calls finders
return module  # CACHED — no finders called
```

**Severity:** CRITICAL
**Fix:** The import guard cannot prevent cached-module imports. The fix must be structural:
1. **Runtime enforcement:** core_logic modules could assert at function entry that no governance modules are in their `sys.modules` path (impractical).
2. **CI gate rule:** Add a rule that forbids `from governance` or `import governance` import statements inside `core_logic/` files at the AST level. This catches the pattern at commit time regardless of runtime sys.modules state.
3. **Module-level `__all__` restrictions** don't help (they only affect `from X import *`).

The best fix is option 2: a CI gate rule `REVERSED_IMPORT` that scans ImportFrom/Import nodes and checks if the importing file is in `core_logic/` and the target root is `governance` or `api`.

---

### Attack: 2-arg getattr to access sys.meta_path
**Target:** Import guard installation
**Method:** CI gates catch `sys.meta_path` via `visit_Attribute` (looking for `Attribute(Name('sys'), 'meta_path')`). But `getattr(sys, 'meta_path')` is a Call node, not an Attribute node. The SILENT_GETATTR rule only fires for 3+ arg getattr. 2-arg getattr is unguarded.
**Result:** BYPASSED
**Evidence:**
```python
import sys

# This is blocked:
# sys.meta_path.clear()  # META_PATH_ACCESS violation

# This passes CI gates:
mp = getattr(sys, 'meta_path')  # 2-arg getattr — no CI rule matches
# Remove the import guard:
mp[:] = [f for f in mp if type(f).__name__ != '_BoundaryEnforcer']
# Import guard is now gone. All boundary enforcement disabled.
```
**Severity:** CRITICAL
**Fix:** Either:
1. Flag ALL `getattr()` calls (not just 3-arg) — aggressive but thorough.
2. Add a CI gate rule that flags `getattr(sys, ...)` specifically.
3. Add a CI gate rule that catches string literals matching protected attributes when passed as args to getattr: `getattr(X, 'meta_path')`, `getattr(X, '__dict__')`, etc.

---

### Attack: __name__ spoofing to confuse caller detection
**Target:** Import guard stack walker
**Method:** The import guard reads `frame.f_globals["__name__"]` to identify the caller. A module that sets `__name__ = "governance.fake"` at the top would appear to be in the `governance` package. The DUNDER_REASSIGNMENT CI rule only catches `Attribute` targets (`obj.__dunder__ = ...`), not `Name` targets (`__name__ = ...`).
**Result:** BYPASSED
**Evidence:**
```python
# core_logic/trojan.py
__name__ = "governance.internal_helper"  # Spoofs identity

# Now, if this module imports governance targets during its execution,
# the import guard sees caller_root = "governance", not "core_logic"
# And governance → governance is not in _FORBIDDEN
from governance.bootstrap import get_lineage  # PASSES import guard
```
CI gate check: DUNDER_REASSIGNMENT looks for `isinstance(target, ast.Attribute)` — but `__name__ = "..."` is `ast.Name`, not `ast.Attribute`. Not caught.
**Severity:** HIGH
**Fix:** Add CI rule: flag bare `__name__ = ...` assignments (ast.Name targets where `id == '__name__'`), except in `if __name__ == '__main__':` guard blocks.

---

### Attack: importlib.util manual import
**Target:** Import boundary
**Method:** `importlib.util.find_spec()` itself walks `sys.meta_path` — so this DOES trigger the import guard. But `importlib.util.module_from_spec()` + `spec.loader.exec_module()` on an already-loaded spec would not re-trigger the guard.
**Result:** BLOCKED (partially)
**Evidence:** The import guard fires during `importlib.util.find_spec()` because it invokes the meta_path finders. However, CI gates catch `importlib.import_module()` but NOT `importlib.util.find_spec()` or `importlib.util.module_from_spec()`. The runtime guard catches it, but the CI gate doesn't flag the attempt.
**Severity:** MEDIUM (runtime-blocked but CI-invisible)
**Fix:** Extend RUNTIME_IMPORT CI rule to flag `importlib.util` usage.

---

## Attack Surface 3: CI Gates Bypass

---

### Attack: builtins module prefix (systemic bypass of all function-call rules)
**Target:** ALL function-call-based CI gate rules
**Method:** Every function-call rule checks `isinstance(func, ast.Name)` — bare name calls like `getattr(...)`, `eval(...)`, `exec(...)`. But `import builtins; builtins.getattr(...)` produces `ast.Attribute(ast.Name('builtins'), 'getattr')` — an Attribute node, not a Name node. None of the Call rules match.
**Result:** BYPASSED
**Evidence:**
```python
import builtins

# ALL of these pass CI gates:
builtins.getattr(registry, 'secret', None)   # Bypasses SILENT_GETATTR
builtins.hasattr(obj, 'maybe')               # Bypasses HASATTR_DISPATCH
builtins.setattr(obj, 'key', 'val')          # Bypasses RUNTIME_SETATTR
builtins.globals()                            # Bypasses DYNAMIC_DISPATCH
builtins.locals()                             # Bypasses DYNAMIC_DISPATCH
builtins.vars(obj)                            # Bypasses VARS_INTROSPECTION
builtins.eval("os._exit = lambda c: None")   # Bypasses DYNAMIC_CODE_EXEC
builtins.exec("import governance")            # Bypasses DYNAMIC_CODE_EXEC
builtins.compile("code", "<>", "exec")       # Bypasses DYNAMIC_CODE_EXEC
builtins.__import__('governance')             # Bypasses DYNAMIC_IMPORT
```
This is a systemic flaw. EVERY function-call CI gate rule can be defeated by routing through `builtins.*`.
**Severity:** CRITICAL
**Fix:** For every rule that checks `isinstance(func, ast.Name) and func.id == 'X'`, add an additional check: `isinstance(func, ast.Attribute) and func.attr == 'X' and isinstance(func.value, ast.Name) and func.value.id in ('builtins', '__builtins__')`. Or more broadly, check `func.attr` regardless of the object it's called on.

---

### Attack: operator.attrgetter for silent attribute access
**Target:** SILENT_GETATTR / HASATTR_DISPATCH rules
**Method:** `operator.attrgetter('key')(obj)` is equivalent to `getattr(obj, 'key')`. The CI gates have no rule for `operator.attrgetter` or `operator.itemgetter`.
**Result:** BYPASSED
**Evidence:**
```python
from operator import attrgetter

# Equivalent to getattr(registry, '_entries') — no CI gate violation
get_entries = attrgetter('_entries')
entries = get_entries(registry)  # Returns the full entries dict

# Chain traversal — equivalent to registry._entries['D1']
get_deep = attrgetter('_entries')
data = get_deep(registry)['D1']
```
**Severity:** HIGH
**Fix:** Add CI gate rule flagging `from operator import attrgetter/itemgetter` and `operator.attrgetter`/`operator.itemgetter` calls.

---

### Attack: functools.reduce(getattr, ...) for chained traversal
**Target:** SILENT_GETATTR / attribute access rules
**Method:** Passing `getattr` as a first-class function reference to `functools.reduce` is not a Call to getattr — the AST shows it as a Name argument to reduce's Call. No CI rule fires on function references passed as arguments.
**Result:** BYPASSED
**Evidence:**
```python
import sys
from functools import reduce

# Traverse: sys → modules → governance.bootstrap → _LINEAGE
lineage = reduce(getattr, ['modules'], sys)['governance.bootstrap']._LINEAGE
# ^ No CI gate violations — getattr is passed as a reference, not called
```
**Severity:** HIGH
**Fix:** Add CI rule: flag any use of forbidden builtins as arguments (Name nodes in call args where id is in `_FORBIDDEN_BUILTIN_ALIASES`).

---

### Attack: contextlib.suppress for silent exception swallowing
**Target:** SILENT_ATTR_FALLBACK / GOVERNANCE_EXCEPTION_CATCH rules
**Method:** `contextlib.suppress(AttributeError)` is functionally `try: ... except AttributeError: pass` but the AST shows a `with` statement and a function call, not an `ExceptHandler` node. CI gates only inspect `visit_ExceptHandler`.
**Result:** BYPASSED
**Evidence:**
```python
from contextlib import suppress

# Bypasses SILENT_ATTR_FALLBACK:
with suppress(AttributeError):
    value = getattr(registry, 'maybe_missing')  # 2-arg getattr also unguarded

# Bypasses GOVERNANCE_EXCEPTION_CATCH for sys.exit (not os._exit):
with suppress(SystemExit):
    from governance.bootstrap import get_lineage
    get_lineage()  # sys.exit(78) is caught by suppress!
```
Note: `contextlib.suppress(SystemExit)` catches `sys.exit()` but NOT `os._exit()`. The kernel uses `os._exit()` (uncatchable). But `governance/bootstrap.py`'s accessor functions (`get_lineage()`, `get_config()`, etc.) use `sys.exit()` — which IS catchable.
**Severity:** HIGH
**Fix:**
1. Add CI rule: flag `contextlib.suppress(SystemExit)`, `contextlib.suppress(BaseException)`, etc.
2. **Change `governance/bootstrap.py` to use `os._exit(78)` instead of `sys.exit(78)` in all accessor functions.** This is the most important fix — it closes the catchability gap between kernel and application-level bootstrap.

---

### Attack: # EXEMPT: escape hatch (social engineering)
**Target:** All CI gate rules
**Method:** Any line with `# EXEMPT: <reason>` skips violation detection. There is no validation of the reason, no approval workflow, and no auditing of exemptions.
**Result:** BYPASSED (process bypass, not technical)
**Evidence:**
```python
# This passes CI gates:
result = getattr(registry, 'secret', None)  # EXEMPT: totally legitimate I promise
evil = eval("os._exit = lambda c: None")    # EXEMPT: required for unit test
```
**Severity:** MEDIUM
**Fix:**
1. Add a CI gate rule that counts exemptions per file and flags files with more than N exemptions.
2. Require exemptions to be in a specific format: `# EXEMPT(RULE_NAME): <ticket-or-justification>`.
3. Add a second-pass scanner that reports all exemptions for manual review.

---

### Attack: test_*.py / conftest.py as governance-violating import targets
**Target:** File exemption list
**Method:** Files starting with `test_` and files named `conftest.py` are fully exempt from CI gates. Create `governance/conftest.py` or `governance/test_bypass.py` with governance-violating code, then import it from governed modules.
**Result:** BYPASSED
**Evidence:**
```python
# governance/conftest.py — FULLY EXEMPT from CI gates
import os
os._exit = lambda c: None  # Kill governance process death
import builtins
# Export bypass utilities
def silent_getattr(obj, key, default=None):
    return builtins.getattr(obj, key, default)
```

```python
# governance/some_module.py — governed, but imports exempt file
from governance.conftest import silent_getattr  # Normal import — passes CI gates
value = silent_getattr(registry, 'secret', None)  # Uses bypass utility
```
The import is a normal `from X import Y` statement, not caught by any CI rule (only `importlib.import_module()` and `__import__()` are flagged).
**Severity:** HIGH
**Fix:**
1. CI gates should flag imports from exempt files in non-test contexts. Add rule: if a non-test file imports from a file matching `test_*.py` or `conftest.py`, flag it.
2. Or: restrict exemptions to files under `tests/` directory only, not anywhere in the tree.

---

### Attack: sitecustomize.py for pre-bootstrap patching
**Target:** All governance enforcement (os._exit)
**Method:** Python automatically imports `sitecustomize.py` from the current directory (or site-packages) at startup, before any application code. Place `Backend/sitecustomize.py` with `os._exit = lambda c: None`. This passes CI gates (no rule matches the assignment) and executes before governance loads.
**Result:** BYPASSED
**Evidence:**
```python
# Backend/sitecustomize.py — auto-loaded by Python before any imports
import os
os._exit = lambda c: None  # All governance kills become no-ops

# CI gate scan: CLEAN (no rules match os._exit = ...)
# Pre-commit hook: CLEAN (scans this file, finds no violations)
# Effect: Every os._exit(78) in the entire governance kernel does nothing
```
**Severity:** CRITICAL
**Fix:**
1. Add CI gate rule flagging any file named `sitecustomize.py` or `usercustomize.py`.
2. Add CI gate rule flagging `os._exit = ...` and `os.kill = ...` assignments.
3. Governance kernel should verify at bootstrap that `os._exit` hasn't been patched: `assert os._exit.__module__ == 'posix'` or similar.

---

### Attack: .pyc without .py source
**Target:** CI gate scanning
**Method:** CI gates scan `.py` files only. `__pycache__/` is skipped. If a `.py` file is deleted but its `.pyc` remains, Python loads the `.pyc`. If the `.pyc` was compiled from pre-governance code containing violations, those violations execute without CI detection.
**Result:** BYPASSED (requires manual .pyc manipulation)
**Evidence:**
```bash
# Compile a governance-violating file
python3 -c "
import py_compile
py_compile.compile('evil.py', 'governance/__pycache__/evil.cpython-312.pyc')
"
rm evil.py  # Delete source — CI gates can't scan what doesn't exist

# Python will load the .pyc:
# import governance.evil  # Loads from __pycache__, no CI check
```
**Severity:** LOW (requires deliberate manipulation, Docker builds start fresh)
**Fix:** Add CI gate that flags any `.pyc` file that doesn't have a corresponding `.py` source. Or add `__pycache__` to `.gitignore` (likely already done) and verify in pre-commit hook.

---

## Attack Surface 4: Lineage Enforcement Bypass

---

### Attack: PersistenceClient direct write (ungoverned path)
**Target:** Lineage enforcement layer
**Method:** `PersistenceClient.execute_query()` provides raw SQL execution with no lineage enforcement. Any governance module can import it and write to the DB without lineage stamps. `GovernedPersistence` exists alongside `PersistenceClient` but nothing prevents use of the ungoverned path.
**Result:** BYPASSED
**Evidence:**
```python
from governance.persistence import PersistenceClient

# Direct write — no lineage stamps, no enforce_lineage() check
PersistenceClient.execute_query(
    "INSERT INTO opportunities (symbol, setup_id, score) VALUES (%s, %s, %s)",
    ("BTCUSDT", "D1", 0.95)
)
# runtime_graph_seal, process_id, bootstrap_epoch are NULL
# No DB constraint rejects NULL lineage
```
**Severity:** HIGH
**Fix:**
1. Make `PersistenceClient.execute_query()` require a `Lineage` parameter. Or remove `PersistenceClient` entirely and route all writes through `GovernedPersistence`.
2. Add `NOT NULL` constraints on lineage columns in governed tables (opportunities, simulation_results, signals).
3. Add CHECK constraint: `CHECK (runtime_graph_seal IS NOT NULL AND length(runtime_graph_seal) = 64)`.

---

### Attack: Forge Lineage dataclass
**Target:** Lineage integrity
**Method:** `Lineage` is a plain frozen dataclass. Its constructor accepts any values. There is no HMAC, no signature, no secret — just a deterministic hash of public content.
**Result:** BYPASSED
**Evidence:**
```python
from governance.kernel.runtime_graph import Lineage

# Read the current seal from the database
# SELECT runtime_graph_seal, process_id, bootstrap_epoch FROM opportunities LIMIT 1
# Result: ('a1b2c3...', 'deadbeef12345678', 1710500000.0)

forged = Lineage(
    runtime_graph_seal='a1b2c3...',  # Copied from DB
    registry_seals=(('config', 'fake'),),
    bootstrap_epoch=1710500000.0,
    process_id='deadbeef12345678',
)

# This lineage passes enforce_lineage():
from governance.kernel.bootstrap import LineageEnforcer
enforcer = LineageEnforcer(forged)
payload = {'runtime_graph_seal': 'a1b2c3...', 'process_id': 'deadbeef12345678'}
enforcer.enforce_lineage(payload)  # PASSES — seal and process_id match
```
**Severity:** HIGH
**Fix:**
1. Add an HMAC to the seal computation, keyed with a process-local secret generated at bootstrap (e.g., `os.urandom(32)`). Store the secret only in memory, never in DB.
2. Or: include a nonce/challenge in enforce_lineage that requires the caller to prove they hold the original Lineage object, not a copy of its fields.

---

### Attack: Seal replay from database
**Target:** Lineage integrity
**Method:** The runtime_graph_seal is stored in every governed DB record. Anyone with read access to the DB can extract a valid seal and stamp it on forged records.
**Result:** BYPASSED
**Evidence:**
```sql
-- Extract seal from production DB
SELECT runtime_graph_seal, process_id, bootstrap_epoch
FROM opportunities
ORDER BY created_at DESC LIMIT 1;

-- Stamp forged record with valid seal
INSERT INTO opportunities (symbol, setup_id, score, runtime_graph_seal, process_id, bootstrap_epoch)
VALUES ('FAKEUSDT', 'D1', 9.99, 'a1b2c3...', 'deadbeef12345678', 1710500000.0);
-- Passes any application-level lineage check
```
**Severity:** CRITICAL
**Fix:**
1. DB-level: use `pg_advisory_lock` or row-level security policies that restrict INSERT to connections from governed processes only.
2. Application-level: each record should include a per-row signature: `HMAC(seal + process_id + row_content, bootstrap_secret)`. This prevents replay because the signature covers the specific record content.
3. Or: use PostgreSQL's `pgcrypto` extension for row-level signatures.

---

### Attack: enforce_lineage() not called on all code paths
**Target:** Lineage enforcement coverage
**Method:** `enforce_lineage()` is opt-in — components must explicitly call it. If a code path writes to the DB without calling `enforce_lineage()`, the lineage is not verified. There's no compile-time or runtime guarantee that all write paths go through enforcement.
**Result:** PARTIAL
**Evidence:** `PersistenceClient.execute_query()` does not call `enforce_lineage()`. `get_conn()` provides raw connection access. Any module in `governance/` can use these to write ungoverned records. The only protection is code review — there's no structural enforcement.
**Severity:** HIGH (overlaps with "PersistenceClient direct write" finding)
**Fix:** Same as PersistenceClient fix — make governed writes the only path.

---

## Attack Surface 5: Operational Bypasses

---

### Attack: Direct database writes via psql
**Target:** All Python-level governance
**Method:** `docker exec backend-postgres-1 psql -U trading -d trading -c "INSERT INTO ..."` bypasses all Python code. Lineage columns are nullable — no DB constraint rejects NULL lineage.
**Result:** BYPASSED
**Evidence:**
```bash
docker exec backend-postgres-1 psql -U trading -d trading -c "
INSERT INTO opportunities (symbol, setup_id, direction, score, decision_ts)
VALUES ('FAKEUSDT', 'D1', 'long', 9.99, now())
"
# Succeeds. runtime_graph_seal is NULL. No constraint violation.
```
**Severity:** HIGH
**Fix:**
1. `ALTER TABLE opportunities ALTER COLUMN runtime_graph_seal SET NOT NULL;`
2. Same for `simulation_results`, signals, and all other governed tables.
3. Even better: `CHECK (length(runtime_graph_seal) = 64)` to prevent empty strings.
4. Consider restricting the `trading` DB role to only accept connections from governed application containers (pg_hba.conf).

---

### Attack: sys.exit vs os._exit inconsistency
**Target:** governance/bootstrap.py accessor functions
**Method:** The kernel (`frozen_registry.py`, `runtime_graph.py`, `kernel/bootstrap.py`) consistently uses `os._exit(78)` which is uncatchable. But the application-level bootstrap (`governance/bootstrap.py`) uses `sys.exit(78)` in accessor functions (`get_lineage()`, `get_config()`, etc.), which raises `SystemExit` — a catchable exception.
**Result:** BYPASSED
**Evidence:**
```python
from contextlib import suppress

# governance/bootstrap.py:54-57 uses sys.exit(78), NOT os._exit(78):
#   if _LINEAGE is None:
#       print("FATAL: ...", file=sys.stderr)
#       sys.exit(78)   # <-- CATCHABLE!

with suppress(SystemExit):
    from governance.bootstrap import get_lineage
    lineage = get_lineage()
    # SystemExit is caught. Process continues without lineage.
    # Code can now run without valid bootstrap.
```
**Severity:** CRITICAL
**Fix:** Change all `sys.exit(78)` calls in `governance/bootstrap.py` to `os._exit(78)`. The 5 accessor functions (`get_lineage`, `get_config`, `get_strategy`, `get_task`, `get_stop_policy`) all use `sys.exit(78)` and should use `os._exit(78)` for consistency with the kernel.

---

### Attack: Environment variable override changes config but seal adapts
**Target:** Configuration integrity
**Method:** Docker environment overrides at `docker compose up` time change the config values, which changes the registry seal, which changes the runtime_graph_seal. The process runs with different governed truth but is internally self-consistent.
**Result:** PARTIAL
**Evidence:**
```bash
docker compose up -d -e "API_KEY=hacked" -e "D1_SCORE_MULT=999.0" worker
# Worker starts with different config → different seal → different lineage
# Its records carry the new seal — consistent but divergent from canonical truth
# Other processes reject its payloads (peer shunning works)
# But it can write its own divergent records to the DB
```
Peer shunning catches cross-process inconsistency. But within the single compromised process, governance is self-consistent with the attacker's config.
**Severity:** MEDIUM
**Fix:** Pin expected seal values in deployment config. At bootstrap, compare computed seal against a known-good seal from an environment variable or secrets manager. If they don't match, refuse to start.

---

## Summary Table

| # | Attack | Surface | Result | Severity |
|---|--------|---------|--------|----------|
| 1 | os._exit monkey-patch | Registry | BYPASSED | CRITICAL |
| 2 | _entries dict mutation | Registry | PARTIAL | LOW |
| 3 | builtins.vars() extraction | Registry | BYPASSED | MEDIUM |
| 4 | Race the freeze | Registry | BLOCKED | — |
| 5 | __class__ swap | Registry | BLOCKED | — |
| 6 | ctypes memory write | Registry | BYPASSED* | MEDIUM |
| 7 | sys.modules cache bypass | Import Guard | BYPASSED | CRITICAL |
| 8 | 2-arg getattr for sys.meta_path | Import Guard | BYPASSED | CRITICAL |
| 9 | __name__ spoofing | Import Guard | BYPASSED | HIGH |
| 10 | importlib.util manual import | Import Guard | BLOCKED | MEDIUM |
| 11 | builtins.* prefix (systemic) | CI Gates | BYPASSED | CRITICAL |
| 12 | operator.attrgetter | CI Gates | BYPASSED | HIGH |
| 13 | functools.reduce(getattr, ...) | CI Gates | BYPASSED | HIGH |
| 14 | contextlib.suppress | CI Gates | BYPASSED | HIGH |
| 15 | # EXEMPT: social engineering | CI Gates | BYPASSED | MEDIUM |
| 16 | test_*.py / conftest.py laundering | CI Gates | BYPASSED | HIGH |
| 17 | sitecustomize.py pre-bootstrap | CI Gates | BYPASSED | CRITICAL |
| 18 | .pyc without .py | CI Gates | BYPASSED | LOW |
| 19 | PersistenceClient direct write | Lineage | BYPASSED | HIGH |
| 20 | Forge Lineage dataclass | Lineage | BYPASSED | HIGH |
| 21 | Seal replay from database | Lineage | BYPASSED | CRITICAL |
| 22 | enforce_lineage() opt-in gaps | Lineage | PARTIAL | HIGH |
| 23 | Direct psql writes | Operational | BYPASSED | HIGH |
| 24 | sys.exit vs os._exit inconsistency | Operational | BYPASSED | CRITICAL |
| 25 | Env var override (seal adapts) | Operational | PARTIAL | MEDIUM |

**CRITICAL findings (7):** #1, #7, #8, #11, #17, #21, #24
**HIGH findings (6):** #9, #12, #13, #14, #16, #19, #20, #22, #23
**MEDIUM findings (3):** #3, #6, #15, #25
**LOW findings (2):** #2, #18

---

## Priority Fixes (by effort/impact)

### Immediate (low effort, high impact)

1. **`governance/bootstrap.py`: Change `sys.exit(78)` → `os._exit(78)`** in all 5 accessor functions. 5-line change. Closes #24.

2. **CI gate: Add `os._exit = ...` assignment detection.** Flag any assignment to `os._exit`, `os.kill`, `os.abort`. Closes #1 and #17.

3. **CI gate: Add `builtins.*` detection.** Extend all function-call rules to also match `Attribute` nodes where `func.attr` matches the forbidden name. Closes #11 (systemic).

4. **CI gate: Flag `sitecustomize.py` / `usercustomize.py`.** Block these filenames entirely. Closes #17.

### Short-term (moderate effort)

5. **CI gate: Add REVERSED_IMPORT rule.** Scan Import/ImportFrom in `core_logic/` files, flag targets in `governance/` or `api/`. Catches violations at commit time regardless of sys.modules state. Closes #7.

6. **CI gate: Flag `operator.attrgetter`, `functools.reduce(getattr, ...)`, `contextlib.suppress(SystemExit/BaseException)`.** Closes #12, #13, #14.

7. **CI gate: Restrict test file exemption to `tests/` directory.** Closes #16.

8. **DB: Add NOT NULL + CHECK constraints on lineage columns.** Closes #23, partially closes #21.

### Medium-term (higher effort)

9. **Lineage: Add HMAC-based seal signing.** Generate a per-process secret at bootstrap. Sign each record with HMAC(seal + content, secret). Closes #20, #21.

10. **Persistence: Remove ungoverned PersistenceClient.execute_query() path.** Or require Lineage parameter. Closes #19, #22.

11. **Import guard: Verify os._exit integrity at bootstrap.** `assert os._exit is posix_exit` (capture reference at module import time). Closes #1 for the runtime case.
