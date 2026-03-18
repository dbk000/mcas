# MCAS Governance Kernel — Consolidated Red Team Report

**Date:** 2026-03-15
**Sources:** 3 independent adversarial audits (amalgamated)
**Scope:** governance/kernel/, governance/bootstrap.py, governance/persistence.py, CI gates, pre-commit hooks, operational attack surface

**Consolidated findings:** 8 CRITICAL, 9 HIGH, 5 MEDIUM, 4 LOW across 5 attack surfaces.

---

## Attack Surface 1: FrozenRegistry Bypass

### 1.1 — os._exit monkey-patch [CRITICAL]
**Target:** Process-death mechanism (single point of failure for all governance)
**Method:** `os._exit` is a single-underscore module attribute. Reassigning it before governance loads neutralises every enforcement call in the kernel. CI gates have no rule matching `os._exit = ...` — it's not a dunder, not `object.__setattr__`, not a `setattr()` call.
**Result:** BYPASSED
**Evidence:**
```python
import os
os._exit = lambda code: None   # Passes all CI gates — CLEAN
```
**Additional vectors:**
- `sitecustomize.py` placed in the working directory or site-packages — auto-loaded by Python before any application code.
- `.pth` file with `import os; os._exit = lambda c: None` — loaded at interpreter startup, invisible to CI hooks.
- `subprocess.fork()` — parent survives child's process death (lower severity: parent gains nothing).

**Why CRITICAL:** The entire governance kernel relies on `os._exit(78)` being uncatchable. If patched, ALL enforcement degrades to print statements. This is a single-point-of-failure across FrozenRegistry, ImportGuard, RuntimeGraphSeal, and LineageEnforcer.

**Fix:**
1. CI gate rule: flag assignments to `os._exit`, `os.kill`, `os.abort`, `signal.signal`.
2. CI gate rule: block files named `sitecustomize.py` or `usercustomize.py`.
3. Runtime check at bootstrap: `assert type(os._exit).__name__ == 'builtin_function_or_method'` — or snapshot `_REAL_EXIT = os._exit` at import time and verify identity before every kill.

---

### 1.2 — sys.exit vs os._exit inconsistency in bootstrap accessors [CRITICAL]
**Target:** `governance/bootstrap.py` accessor functions (`get_lineage`, `get_config`, `get_strategy`, `get_task`, `get_stop_policy`)
**Method:** The kernel (`frozen_registry.py`, `runtime_graph.py`) uses `os._exit(78)` — uncatchable. But the application-level bootstrap (`governance/bootstrap.py`) uses `sys.exit(78)` in 5 accessor functions, which raises `SystemExit` — a catchable exception.
**Result:** BYPASSED
**Evidence:**
```python
from contextlib import suppress

with suppress(SystemExit):
    from governance.bootstrap import get_lineage
    lineage = get_lineage()
    # SystemExit caught. Process continues without valid lineage.
```
CI gates have no rule for `contextlib.suppress(SystemExit)` — it's a `with` statement, not an `ExceptHandler` node.

**Fix:** Change all `sys.exit(78)` calls in `governance/bootstrap.py` to `os._exit(78)`. 5-line change. Aligns application-level enforcement with kernel-level enforcement.

---

### 1.3 — _entries dict survives freeze [LOW]
**Target:** FrozenRegistry internal state
**Method:** After freeze, `_entries` remains as a plain mutable dict on the instance. It's non-authoritative (promoted attributes are the authority), but a reference held from pre-freeze code can still mutate the dict.
**Result:** PARTIAL (mutation is real but harmless — post-freeze access uses promoted attributes)
**Evidence:**
```python
def evil_populator(reg):
    saved = reg._entries  # plain dict reference
    reg._register("legit", "value")
    # After freeze: saved["injected"] = "evil" succeeds silently
    # But registry.injected → os._exit(78) (not promoted at freeze time)
```

**Fix:** In `_freeze()`, replace `_entries` with frozen proxy: `object.__setattr__(self, '_entries', types.MappingProxyType({}))`.

---

### 1.4 — Race the freeze — thread injection [LOW]
**Target:** Registry population window
**Method:** Between `_register()` calls and `_freeze()`, inject entries from another thread.
**Result:** BLOCKED by design in 2 of 3 audits; PARTIAL in 1
**Consensus:** Registries are local variables in `bootstrap_genesis()`, not exposed globally until after freeze. Populators run synchronously. No thread boundary exists between population and freeze. Exploitation requires controlled execution inside a populator — at which point the attacker already owns bootstrap code.
**Severity reduced:** LOW (architecturally sealed; requires write access to bootstrap module).

---

### 1.5 — ctypes C-level mutation [MEDIUM]
**Target:** FrozenRegistry frozen state
**Method:** `ctypes.pythonapi.PyObject_GenericSetAttr()` bypasses Python-level `__setattr__` guards. CI gates block `import inspect` but NOT `import ctypes`.
**Result:** BYPASSED (theoretically — fragile, CPython-version-dependent)
**Evidence:**
```python
import ctypes  # Not blocked by any CI gate rule
ctypes.pythonapi.PyObject_GenericSetAttr(
    ctypes.py_object(registry),
    ctypes.py_object("new_key"),
    ctypes.py_object("evil_value")
)
```

**Fix:** Add `ctypes`, `cffi`, `_ctypes` to CI gate `REFLECTIVE_IMPORT` or new `UNSAFE_C_IMPORT` rule.

---

### 1.6 — __class__ swap [BLOCKED]
**Target:** Frozen registry type identity
**Method:** `registry.__class__ = UnfrozenClass`
**Result:** BLOCKED — CI gate `DUNDER_REASSIGNMENT` catches dunder assignments on non-self targets. Runtime `__setattr__` guard kills process post-freeze.

---

### 1.7 — pickle/copy extraction [LOW]
**Target:** FrozenRegistry read-only state extraction
**Method:** `copy.copy(registry)` or pickle. Read-only — no mutation possible. Clone is also frozen.
**Severity:** LOW

---

## Attack Surface 2: Import Guard Bypass

### 2.1 — sys.modules cache bypass [CRITICAL]
**Target:** `_BoundaryEnforcer` meta_path hook
**Method:** Python checks `sys.modules` before calling `find_spec`. After bootstrap, all `governance.*` modules are cached. Any `core_logic/` code can `from governance.signal_worker import X` — Python returns the cached module without consulting the import guard.
**Result:** BYPASSED
**Evidence:**
```python
# CPython importlib._bootstrap._find_and_load:
module = sys.modules.get(name, _NEEDS_LOADING)
if module is _NEEDS_LOADING:
    return _find_and_load_unlocked(name, import_)  # calls finders
return module  # CACHED — no finders called
```
In a real MCAS process, bootstrap imports governance modules first. By the time core_logic runs, governance modules are in `sys.modules`. The import guard is inert.

**Fix:** The import guard cannot prevent cached-module imports. The fix must be structural:
1. **CI gate rule `REVERSED_IMPORT`:** scan Import/ImportFrom nodes in `core_logic/` files, flag targets in `governance/` or `api/`. Catches violations at commit time regardless of runtime state.
2. **Post-bootstrap verification:** scan all loaded `core_logic` module namespaces for governance references.

---

### 2.2 — 2-arg getattr to access sys.meta_path [CRITICAL]
**Target:** Import guard installation
**Method:** CI gates catch `sys.meta_path` via `visit_Attribute`. But `getattr(sys, 'meta_path')` is a Call node, not an Attribute node. `SILENT_GETATTR` only fires for 3+ arg getattr. 2-arg is unguarded.
**Result:** BYPASSED
**Evidence:**
```python
mp = getattr(sys, 'meta_path')  # 2-arg — no CI rule matches
mp[:] = [f for f in mp if type(f).__name__ != '_BoundaryEnforcer']
# Import guard removed. All boundary enforcement disabled.
```

**Fix:** Flag ALL `getattr()` calls on `sys` object regardless of arg count, or flag string literals matching protected attributes when passed to getattr.

---

### 2.3 — __name__ spoofing [HIGH]
**Target:** Import guard stack walker
**Method:** The guard reads `frame.f_globals["__name__"]` to identify the caller module. `__name__ = "governance.fake"` at module level spoofs identity. `DUNDER_REASSIGNMENT` only checks `Attribute` targets, not bare `Name` targets.
**Result:** BYPASSED
**Evidence:**
```python
# core_logic/trojan.py
__name__ = "governance.internal_helper"  # Spoofs identity
from governance.bootstrap import get_lineage  # Passes import guard
```

**Fix:** CI gate: flag bare `__name__ = ...` assignments (ast.Name targets where `id == '__name__'`), except in `if __name__ == '__main__':` guard blocks.

---

### 2.4 — importlib.util manual import [MEDIUM]
**Target:** Import boundary
**Method:** `importlib.util.find_spec()` is NOT caught by the CI gate (which only checks `importlib.import_module`). However, `find_spec` does trigger the meta_path hook at runtime.
**Result:** BLOCKED at runtime, CI-invisible
**Severity:** MEDIUM (runtime-blocked but CI gap)
**Fix:** Extend `RUNTIME_IMPORT` CI rule to flag `importlib.util` usage.

---

## Attack Surface 3: CI Gates Bypass

### 3.1 — builtins.* prefix (systemic bypass of ALL function-call rules) [CRITICAL]
**Target:** Every function-call-based CI gate rule
**Method:** Every function-call rule checks `isinstance(func, ast.Name)` — bare calls like `getattr(...)`. But `builtins.getattr(...)` produces `ast.Attribute(ast.Name('builtins'), 'getattr')` — an Attribute node, not a Name node. None of the Call rules match.
**Result:** BYPASSED
**Evidence:**
```python
import builtins

# ALL of these pass CI gates:
builtins.getattr(registry, 'secret', None)   # SILENT_GETATTR
builtins.hasattr(obj, 'maybe')               # HASATTR_DISPATCH
builtins.setattr(obj, 'key', 'val')          # RUNTIME_SETATTR
builtins.globals()                            # DYNAMIC_DISPATCH
builtins.vars(obj)                            # VARS_INTROSPECTION
builtins.eval("os._exit = lambda c: None")   # DYNAMIC_CODE_EXEC
builtins.exec("import governance")            # DYNAMIC_CODE_EXEC
builtins.__import__('governance')             # DYNAMIC_IMPORT
```
This is a **systemic flaw**. EVERY function-call CI gate rule can be defeated by routing through `builtins.*`.

**Fix:** For every rule checking `isinstance(func, ast.Name) and func.id == 'X'`, add: `isinstance(func, ast.Attribute) and func.attr == 'X' and isinstance(func.value, ast.Name) and func.value.id in ('builtins', '__builtins__')`.

---

### 3.2 — operator.attrgetter [HIGH]
**Target:** SILENT_GETATTR / HASATTR_DISPATCH rules
**Method:** `operator.attrgetter('key')(obj)` is equivalent to `getattr(obj, 'key')`. No CI rule covers it.
**Result:** BYPASSED
```python
from operator import attrgetter
entries = attrgetter('_entries')(registry)  # Full entries dict
```

**Fix:** Add CI gate rule flagging `operator.attrgetter`/`operator.itemgetter`.

---

### 3.3 — functools.reduce(getattr, ...) [HIGH]
**Target:** Attribute access rules
**Method:** `getattr` passed as a first-class function reference to `functools.reduce`. The AST shows a Name argument, not a Call to getattr.
**Result:** BYPASSED
```python
from functools import reduce
value = reduce(getattr, ['signal_worker'], governance)
```

**Fix:** Flag forbidden builtins used as arguments (Name nodes in call args matching forbidden list).

---

### 3.4 — contextlib.suppress(SystemExit) [HIGH]
**Target:** GOVERNANCE_EXCEPTION_CATCH rules
**Method:** `contextlib.suppress(SystemExit)` is a `with` statement, not an `ExceptHandler`. CI gates only inspect `visit_ExceptHandler`. Catches `sys.exit()` but not `os._exit()`.
**Result:** BYPASSED (for code paths using `sys.exit`)
**Evidence:** `governance/bootstrap.py` accessor functions use `sys.exit(78)` — catchable via suppress.

**Fix:**
1. CI gate: flag `contextlib.suppress(SystemExit)`, `contextlib.suppress(BaseException)`.
2. Change `governance/bootstrap.py` to use `os._exit(78)` (closes this and finding 1.2).

---

### 3.5 — # EXEMPT: social engineering [HIGH]
**Target:** All exemptible CI gate rules
**Method:** `# EXEMPT: <any text>` on any line skips violation detection. No validation of reason, no approval workflow, no audit trail.
**Result:** BYPASSED
**Evidence:**
```python
object.__setattr__(registry, 'key', 'evil')  # EXEMPT: totally legitimate
sys.meta_path.clear()  # EXEMPT: testing purposes
```
Rules that honour EXEMPT include: SILENT_GETATTR, FROZEN_BYPASS_SETATTR, META_PATH_ACCESS, and 9 others.

**Fix:**
1. Require `# EXEMPT(RULE_NAME): <ticket-number>` format.
2. Make FROZEN_BYPASS_SETATTR and META_PATH_ACCESS non-exemptible.
3. Add a second-pass scanner reporting all exemptions for review.
4. Cap EXEMPT count per file; fail CI if threshold exceeded.

---

### 3.6 — test_*.py / conftest.py file laundering [CRITICAL]
**Target:** File exemption list
**Method:** `_is_fully_exempt` checks filename only, not path. Any file starting with `test_` or named `conftest.py` in ANY directory — including `governance/` — is fully exempt from all CI gates. Governed code can then import from the exempt file.
**Result:** BYPASSED
**Evidence:**
```python
# governance/test_helper.py — FULLY EXEMPT
import os
os._exit = lambda code: None
def silent_getattr(obj, key, default=None):
    return __builtins__.__dict__['getattr'](obj, key, default)

# governance/some_worker.py — governed, but imports exempt file
from governance.test_helper import silent_getattr  # Normal import
```

**Fix:** Restrict `test_` / `conftest.py` exemption to files under a `tests/` directory:
```python
def _is_fully_exempt(filepath: str) -> bool:
    filename = Path(filepath).name
    if filename in FULLY_EXEMPT_FILES:
        return True
    if filename.startswith("test_") or filename == "conftest.py":
        return 'tests' in Path(filepath).parts
    return False
```

---

### 3.7 — sitecustomize.py / .pth pre-bootstrap patching [CRITICAL]
**Target:** All governance enforcement
**Method:** Python auto-imports `sitecustomize.py` at startup. `.pth` files with `import` lines are executed at interpreter startup. Neither are `.py` files that the pre-commit hook processes (hook filters on `\.py$` in staged files, but `.pth` doesn't match).
**Result:** BYPASSED
**Evidence:**
```python
# Backend/sitecustomize.py — auto-loaded before any imports
import os
os._exit = lambda c: None  # All governance kills become no-ops
# CI gate scan: CLEAN. Pre-commit hook: CLEAN.
```

**Fix:**
1. CI gate: block files named `sitecustomize.py`, `usercustomize.py`.
2. Extend pre-commit hook to scan `.pth` files: `grep -qE '\.(py|pth)$'`.
3. Bootstrap: verify `os._exit` integrity (see finding 1.1).

---

### 3.8 — .pyc without .py source [LOW]
**Target:** CI gate scanning
**Method:** CI gates scan `.py` files only. If a `.pyc` survives from pre-governance code (e.g., dev volume mounts), Python may load it.
**Result:** BYPASSED (requires deliberate manipulation; Docker builds start fresh)
**Severity:** LOW

**Fix:** `PYTHONDONTWRITEBYTECODE=1` in Dockerfiles. Clear `__pycache__` in build.

---

### 3.9 — builtins.__dict__['getattr'] indirection [HIGH]
**Target:** BUILTIN_ALIAS rule
**Method:** `getattr(__builtins__, 'getattr')` returns the getattr function. The result is assigned to a variable and called — the call is on a variable name, not "getattr", so no CI rule fires. `BUILTIN_ALIAS` only checks `Name` nodes on RHS, not `Call` nodes.
**Result:** BYPASSED
```python
result = getattr(__builtins__, 'getattr')(obj, 'key', None)
# 2-arg getattr (not SILENT_GETATTR) → returns fn → call on return value (not "getattr")
```

**Fix:** BUILTIN_ALIAS should also check `ast.Call` nodes on RHS. Flag `getattr(__builtins__, ...)` specifically.

---

## Attack Surface 4: Lineage Enforcement Bypass

### 4.1 — PersistenceClient direct write (ungoverned path) [CRITICAL]
**Target:** Lineage enforcement layer
**Method:** `PersistenceClient.execute_query()` and `get_conn()` provide raw SQL execution with zero lineage enforcement. Any governance module can import and use them. Furthermore, `signal_worker._persist_signal_to_pg()` uses its own connection pool, NOT `GovernedPersistence`, and does NOT stamp lineage columns.
**Result:** BYPASSED
**Evidence:**
```python
from governance.persistence import PersistenceClient
PersistenceClient.execute_query(
    "INSERT INTO opportunities (symbol, setup_id, score) VALUES (%s, %s, %s)",
    ("BTCUSDT", "D1", 0.95)
)
# runtime_graph_seal, process_id, bootstrap_epoch are all NULL
# No DB constraint rejects NULL lineage
```
The lineage enforcement infrastructure is **built but not fully wired into production code paths**.

**Fix:**
1. Remove `PersistenceClient` from public API or restrict to read-only.
2. Wire `GovernedPersistence` into signal_worker and all write paths.
3. Add NOT NULL constraints on lineage columns (see finding 5.1).

---

### 4.2 — Forge Lineage dataclass [HIGH]
**Target:** Lineage integrity
**Method:** `Lineage` is a plain frozen dataclass. Constructor accepts any values. No HMAC, no signature, no secret — just a deterministic hash of public content.
**Result:** BYPASSED
```python
from governance.kernel.runtime_graph import Lineage
forged = Lineage(
    runtime_graph_seal='abc123...',   # Copied from DB or logs
    registry_seals=(('config', 'fake'),),
    bootstrap_epoch=1710500000.0,
    process_id='deadbeef12345678',
)
```

**Fix:** Add HMAC-based seal signing. Generate a per-process secret at bootstrap (`os.urandom(32)`), store only in memory. Sign each record: `HMAC(seal + content, secret)`.

---

### 4.3 — Seal replay from database / logs [CRITICAL]
**Target:** Lineage integrity
**Method:** The seal is deterministic (same config = same seal). It's readable from: (a) container logs at bootstrap, (b) any governed DB record, (c) Redis payloads. `process_id = SHA-256(seal + epoch)[:16]` — also computable from known values.
**Result:** BYPASSED
```sql
SELECT runtime_graph_seal, process_id, bootstrap_epoch
FROM opportunities ORDER BY created_at DESC LIMIT 1;

INSERT INTO opportunities (symbol, setup_id, score, runtime_graph_seal, process_id, bootstrap_epoch)
VALUES ('FAKEUSDT', 'D1', 9.99, 'a1b2c3...', 'deadbeef12345678', 1710500000.0);
```

**Fix:**
1. Per-record HMAC: `HMAC(seal + row_content, bootstrap_secret)`.
2. Truncate seal in log output.
3. Consider pgcrypto row-level signatures or pg_hba.conf source restrictions.

---

### 4.4 — enforce_lineage() opt-in gaps [CRITICAL]
**Target:** Lineage enforcement coverage
**Method:** `enforce_lineage()` is opt-in. Components must explicitly call it. Multiple write paths exist without enforcement: `PersistenceClient`, `get_conn()`, signal_worker's own pool.
**Result:** BYPASSED
**Evidence:** The `_persist_signal_to_pg()` INSERT does not include lineage columns. `GovernedPersistence` exists but is unused in the actual write path.

**Fix:** Same as 4.1 — make governed writes the only path. Add integration tests verifying lineage columns are non-null after signal persistence.

---

## Attack Surface 5: Operational Bypasses

### 5.1 — Direct psql writes [CRITICAL]
**Target:** All Python-level governance
**Method:** `docker exec backend-postgres-1 psql -U trading -d trading -c "INSERT INTO ..."` bypasses all Python code. Lineage columns are nullable — no DB constraint rejects NULL.
**Result:** BYPASSED
**Evidence:**
```sql
-- Migration used: ADD COLUMN IF NOT EXISTS runtime_graph_seal VARCHAR(64)
-- No NOT NULL. No CHECK. No trigger.
INSERT INTO opportunities (symbol, setup_id, direction, score, decision_ts)
VALUES ('FAKEUSDT', 'D1', 'long', 9.99, now());
-- Succeeds with NULL lineage.
```

**Fix:**
1. `ALTER TABLE opportunities ALTER COLUMN runtime_graph_seal SET NOT NULL;` (after backfilling existing rows)
2. Same for `process_id`, `bootstrap_epoch` on all governed tables.
3. `CHECK (length(runtime_graph_seal) = 64)` to prevent empty strings.
4. Consider restricting the `trading` DB role via `pg_hba.conf`.

---

### 5.2 — Environment variable override [MEDIUM]
**Target:** Configuration integrity
**Method:** Docker environment overrides change config values, which changes the seal. The compromised process is internally self-consistent but divergent from canonical truth.
**Result:** PARTIAL — peer shunning correctly detects cross-process inconsistency. But if ALL workers are restarted with attacker's env, they're consistently wrong and don't detect it.

**Fix:** Pin expected seal values in deployment config. Bootstrap compares computed seal against known-good seal from secrets manager.

---

### 5.3 — Stale __pycache__ [MEDIUM]
**Target:** CI gates, runtime enforcement
**Method:** Pre-governance `.pyc` files surviving in dev volume mounts or Docker images.
**Result:** PARTIAL (unlikely in production Docker builds; real risk in dev)

**Fix:** `PYTHONDONTWRITEBYTECODE=1` in Dockerfiles. Clear `__pycache__` in build step.

---

## Consolidated Summary Table

| # | Attack | Surface | Result | Severity |
|---|--------|---------|--------|----------|
| 1.1 | os._exit monkey-patch | Registry | BYPASSED | **CRITICAL** |
| 1.2 | sys.exit vs os._exit in bootstrap | Operational | BYPASSED | **CRITICAL** |
| 1.3 | _entries dict survives freeze | Registry | PARTIAL | LOW |
| 1.4 | Race the freeze | Registry | BLOCKED | LOW |
| 1.5 | ctypes C-level mutation | Registry | BYPASSED* | MEDIUM |
| 1.6 | __class__ swap | Registry | BLOCKED | — |
| 1.7 | pickle/copy extraction | Registry | PARTIAL | LOW |
| 2.1 | sys.modules cache bypass | Import Guard | BYPASSED | **CRITICAL** |
| 2.2 | 2-arg getattr for sys.meta_path | Import Guard | BYPASSED | **CRITICAL** |
| 2.3 | __name__ spoofing | Import Guard | BYPASSED | HIGH |
| 2.4 | importlib.util (runtime-blocked) | Import Guard | PARTIAL | MEDIUM |
| 3.1 | builtins.* prefix (systemic) | CI Gates | BYPASSED | **CRITICAL** |
| 3.2 | operator.attrgetter | CI Gates | BYPASSED | HIGH |
| 3.3 | functools.reduce(getattr) | CI Gates | BYPASSED | HIGH |
| 3.4 | contextlib.suppress(SystemExit) | CI Gates | BYPASSED | HIGH |
| 3.5 | # EXEMPT: social engineering | CI Gates | BYPASSED | HIGH |
| 3.6 | test_*/conftest.py file laundering | CI Gates | BYPASSED | **CRITICAL** |
| 3.7 | sitecustomize.py / .pth | CI Gates | BYPASSED | **CRITICAL** |
| 3.8 | .pyc without .py | CI Gates | BYPASSED | LOW |
| 3.9 | builtins.__dict__ indirection | CI Gates | BYPASSED | HIGH |
| 4.1 | PersistenceClient direct write | Lineage | BYPASSED | **CRITICAL** |
| 4.2 | Forge Lineage dataclass | Lineage | BYPASSED | HIGH |
| 4.3 | Seal replay from DB/logs | Lineage | BYPASSED | **CRITICAL** |
| 4.4 | enforce_lineage() opt-in gaps | Lineage | BYPASSED | **CRITICAL** (production paths unwired) |
| 5.1 | Direct psql writes (nullable cols) | Operational | BYPASSED | **CRITICAL** |
| 5.2 | Env var override (seal adapts) | Operational | PARTIAL | MEDIUM |
| 5.3 | Stale __pycache__ | Operational | PARTIAL | MEDIUM |

**Totals:** 8 CRITICAL, 9 HIGH, 5 MEDIUM, 4 LOW

---

## Priority Fix Plan

### P0 — Immediate (each <= 1 hour, closes CRITICAL findings)

| Fix | Effort | Closes |
|-----|--------|--------|
| **1. `governance/bootstrap.py`: change `sys.exit(78)` to `os._exit(78)` in all 5 accessors** | 5 lines | #1.2, #3.4 |
| **2. CI gate: flag `os._exit =`, `os.kill =`, `os.abort =` assignments** | ~15 lines in `visit_Assign` | #1.1, #3.7 |
| **3. CI gate: extend all function-call rules to match `builtins.*` Attribute forms** | ~20 lines across existing rules | #3.1 (systemic) |
| **4. CI gate: block `sitecustomize.py` / `usercustomize.py` filenames** | ~5 lines | #3.7 |
| **5. CI gate: restrict test_/conftest.py exemption to `tests/` directory** | ~10 lines in `_is_fully_exempt` | #3.6 |

### P1 — This sprint (closes remaining CRITICAL + HIGH findings)

| Fix | Effort | Closes |
|-----|--------|--------|
| **6. CI gate: `REVERSED_IMPORT` rule** — scan core_logic/ for governance/api imports at AST level | ~30 lines new rule | #2.1 |
| **7. DB: NOT NULL + CHECK on lineage columns** — backfill existing rows, then constrain | Migration + backfill | #5.1, partially #4.3 |
| **8. Wire GovernedPersistence into signal_worker** and all write paths; call enforce_lineage() on Redis payloads | Moderate refactor | #4.1, #4.4 |
| **9. CI gate: flag `operator.attrgetter`, `functools.reduce(getattr)`, `contextlib.suppress(SystemExit/BaseException)`** | ~40 lines across rules | #3.2, #3.3, #3.4 |
| **10. CI gate: flag 2-arg `getattr(sys, ...)` and bare `__name__ = ...` assignments** | ~20 lines | #2.2, #2.3 |
| **11. EXEMPT hardening** — make FROZEN_BYPASS_SETATTR and META_PATH_ACCESS non-exemptible; require ticket format | ~20 lines | #3.5 |
| **12. CI gate: flag `ctypes`/`cffi` imports** | ~5 lines | #1.5 |
| **13. Extend pre-commit hook** to scan `.pth` files | ~5 lines in hook | #3.7 |

### P2 — This quarter (defense in depth)

| Fix | Effort | Closes |
|-----|--------|--------|
| **14. Lineage HMAC signing** — per-process secret, per-record signature | Moderate | #4.2, #4.3 |
| **15. Remove/privatize PersistenceClient** — route all writes through GovernedPersistence | Moderate refactor | #4.1 |
| **16. Bootstrap os._exit integrity check** — verify identity at first use | ~10 lines | #1.1 (runtime defense) |
| **17. Delete `_entries` after freeze** | ~3 lines | #1.3 |
| **18. PYTHONDONTWRITEBYTECODE=1 in Dockerfiles** + __pycache__ cleanup | ~3 lines | #3.8, #5.3 |
| **19. Post-bootstrap verification pass** — scan loaded modules for cross-boundary references | ~50 lines | #2.1 (runtime defense) |
| **20. Pin expected seal in deployment** — compare at bootstrap against known-good value | ~20 lines | #5.2 |

---

## Threat Model Notes

Three recurring patterns emerge across all audits:

1. **The `os._exit` monoculture.** Every enforcement mechanism terminates via a single function. Patching it once disables everything. The fix is both CI-level (prevent patching) and runtime (verify integrity).

2. **The CI gate AST scanner has a Maginot Line problem.** It checks `ast.Name` call forms exhaustively but doesn't handle `ast.Attribute` call forms (builtins prefix), functional indirection (reduce, attrgetter), or `with`-statement exception suppression. The systemic fix is: for every rule that checks `func.id`, also check `func.attr`.

3. **Lineage is built but not wired.** The enforcement infrastructure (GovernedPersistence, LineageEnforcer, enforce_lineage) exists. But production write paths (signal_worker, PersistenceClient) bypass it. DB columns are nullable. The governance kernel enforces *access* rigorously but *persistence* is advisory.

The P0 fixes (items 1-5) are collectively ~55 lines of code and close 5 CRITICAL findings. They should be deployed before any other governance work proceeds.
