# MCAS Governance Kernel — Red Team Audit Report

**Date:** 2026-03-15
**Auditor:** Claude Opus 4.6 (adversarial mode)
**Scope:** governance/kernel/ — FrozenRegistry, ImportGuard, CI Gates, Lineage, Operational bypasses

---

## Attack Surface 1: FrozenRegistry Bypass

### Attack 1.1: Read `_entries` dict via `vars()` before freeze
**Target:** FrozenRegistry internal state
**Method:** Call `vars(registry)` during OPEN phase to get a reference to `_entries`, then hold that reference across the freeze boundary.
**Result:** BLOCKED (CI gates)
**Evidence:**
```python
# CI gates catches vars() — VARS_INTROSPECTION rule
ref = vars(registry)  # ← flagged at AST level
```
The `_entries` dict is cleared implicitly (entries promoted to attributes, but `_entries` itself is never deleted — it remains on the instance). However, `vars()` is forbidden.
**Severity:** LOW (blocked by CI)
**Residual risk:** `_entries` dict survives freeze — see Attack 1.1b.

### Attack 1.1b: Hold `_entries` reference from pre-freeze code
**Target:** FrozenRegistry stale reference
**Method:** During the bootstrap populator callback, you receive the OPEN registry. Save `registry._entries` to a local variable. After freeze, mutate through the saved dict reference.
**Result:** BYPASSED
**Evidence:**
```python
saved_ref = None

def evil_populator(reg):
    global saved_ref
    reg._register("legit_key", "legit_value")
    saved_ref = reg._entries  # _entries is a plain dict — no copy

# After freeze:
# saved_ref is the SAME dict object as reg._entries
# reg._entries still exists (never deleted)
# saved_ref["injected"] = "evil"  → mutates the internal dict
# BUT: frozen keys are already promoted to attributes
# The dict mutation doesn't affect attribute access post-freeze
```
**Severity:** LOW
**Fix:** After freeze, replace `_entries` with a frozen copy or delete it:
```python
object.__setattr__(self, '_entries', types.MappingProxyType(self._entries))
```
**Nuance:** The mutation is real but harmless — post-freeze access is via promoted attributes, not `_entries`. The dict becomes a dead reference. Still, it's architecturally unsatisfying.

### Attack 1.2: Read `__dict__` on the instance
**Target:** FrozenRegistry attribute exposure
**Method:** `registry.__dict__` gives full attribute dump including `_seal`, `_frozen`, `_name`, all promoted entries.
**Result:** BLOCKED (CI gates)
**Evidence:**
```python
# CI gates catches __dict__ — DUNDER_DICT_ACCESS rule
d = registry.__dict__  # ← flagged
```
**Severity:** LOW (blocked by CI)

### Attack 1.3: Intercept `os._exit` via monkey-patch
**Target:** Process death mechanism
**Method:** Replace `os._exit` before the kernel loads.
**Result:** BLOCKED (CI gates — multiple layers)
**Evidence:**
```python
# Approach 1: direct monkey-patch
import os
os._exit = lambda code: None  # ← DUNDER_DICT_ACCESS? No — it's attribute assignment
# But: ci_gates catches setattr-like patterns only on object/type
# os._exit = ... is a plain assignment — NOT caught by FROZEN_BYPASS_SETATTR
# However: this requires the attacker code to run BEFORE the kernel
```
**Result:** PARTIAL BYPASS
**Severity:** HIGH
**Detail:** The CI gates do NOT specifically scan for `os._exit = ...` or `os._exit` reassignment. The `FROZEN_BYPASS_SETATTR` rule only catches `object.__setattr__` and `type.__setattr__`. The `RUNTIME_SETATTR` rule catches `setattr()` calls, not plain attribute assignment. A module that runs before kernel import could do:
```python
# In a sitecustomize.py or early-loading module:
import os
_real_exit = os._exit
os._exit = lambda code: None  # Process death is now neutered
```
The CI gate would NOT flag `os._exit = lambda code: None` because:
- It's not `object.__setattr__` or `type.__setattr__` (FROZEN_BYPASS_SETATTR)
- It's not a `setattr()` call (RUNTIME_SETATTR)
- It's plain attribute assignment on a module object
**Fix:** Add CI gate rule: flag any assignment to `os._exit` or `os.exit`. Pattern:
```python
# In visit_Assign: check for targets like os._exit, os.exit, sys.exit
```
Also: the kernel could snapshot `os._exit` at import time and verify it hasn't been replaced:
```python
_REAL_EXIT = os._exit
# At enforcement time:
if os._exit is not _REAL_EXIT:
    _REAL_EXIT(78)
```

### Attack 1.4: Fork subprocess, let child die, continue in parent
**Target:** Process death scope
**Method:** `os.fork()` before accessing a missing key. Child dies, parent continues.
**Result:** BYPASSED (by design)
**Evidence:**
```python
import os
pid = os.fork()
if pid == 0:
    # Child: try the forbidden access
    val = registry.missing_key  # child dies with exit 78
else:
    # Parent: still alive, can continue
    os.waitpid(pid, 0)
    # But parent doesn't get the value either
```
**Severity:** LOW
**Detail:** The parent survives but gains nothing — it never receives the forbidden value. The child dies before it could communicate the value back. If the child wrote the value to a pipe/file before dying... but `os._exit` happens *inside* `__getattr__` before any return.
**Fix:** Not fixable — process isolation is an OS guarantee. But it's also not useful to an attacker.

### Attack 1.5: `ctypes` to write directly to object memory
**Target:** Frozen object mutation
**Method:** Use `ctypes.pythonapi` to modify the object's `__dict__` directly in memory.
**Result:** BLOCKED (CI gates would block `import ctypes` indirectly — `ctypes` uses `__dict__` patterns). But actually...
**Evidence:**
```python
import ctypes  # NOT blocked by CI gates — ctypes is not in the forbidden list
# ctypes is not 'inspect', not a forbidden builtin
# CI gates don't block ctypes imports
```
**Result:** PARTIAL BYPASS
**Severity:** MEDIUM
**Detail:** `import ctypes` is not blocked by CI gates. The `REFLECTIVE_IMPORT` rule only catches `import inspect`. An attacker could:
```python
import ctypes
ctypes.pythonapi.PyObject_GenericSetAttr(
    ctypes.py_object(registry),
    ctypes.py_object("new_key"),
    ctypes.py_object("evil_value")
)
```
This bypasses `__setattr__` entirely by calling the C-level attribute setter.
**Fix:** Add `ctypes` to `REFLECTIVE_IMPORT` or create a new rule `UNSAFE_C_IMPORT` that blocks `ctypes`, `cffi`, and `_ctypes`.

### Attack 1.6: `__class__` assignment to swap for unfrozen subclass
**Target:** Frozen registry type identity
**Method:** Replace `registry.__class__` with an unfrozen subclass.
**Result:** BLOCKED (CI gates)
**Evidence:**
```python
# __class__ is a dunder, and assigning to it:
registry.__class__ = UnfrozenRegistry  # ← DUNDER_REASSIGNMENT rule catches this
# ...but only if the target is not 'self'
# From outside the class, this WOULD be caught
```
**Severity:** LOW (blocked by CI)

### Attack 1.7: Race the freeze — thread injection between populate and freeze
**Target:** Registry population window
**Method:** During bootstrap, between `_populate_*` and `_freeze()`, another thread calls `_register()`.
**Result:** PARTIAL BYPASS (theoretically possible)
**Evidence:**
```python
# In governance/bootstrap.py:350-401
# Phase 3: Populate (line 372-375)
_populate_config(config_reg)      # Thread could _register() here
_populate_strategies(strategy_reg)
_populate_tasks(task_reg)
_populate_stop_policies(stop_policy_reg)
# Phase 4: Freeze (line 378-381)
config_seal = config_reg._freeze()  # Only NOW is config_reg frozen
```
Between line 372 and 378, the config_reg is OPEN and `_register()` works. If an attacker thread had a reference to `config_reg`, it could inject entries.
**Severity:** LOW
**Detail:** Getting a reference to the registry requires being inside the bootstrap flow. The registries are local variables, not module globals, until line 397-401. An attacker would need to be called from within a populator to get the reference, which means they already have controlled execution inside bootstrap. The threat model is "a future CC instance writing code" — but CC would need to modify a populator to inject the reference, which changes files and triggers CI gates.
**Fix:** No immediate fix needed. The registry objects are local variables during construction.

---

## Attack Surface 2: Import Guard Bypass

### Attack 2.1: Remove the hook from `sys.meta_path`
**Target:** `_BoundaryEnforcer` in `sys.meta_path`
**Method:** `sys.meta_path.remove()` or slice assignment.
**Result:** BLOCKED (CI gates)
**Evidence:**
```python
# sys.meta_path is flagged by META_PATH_ACCESS rule
sys.meta_path.remove(...)  # ← flagged: sys.meta_path access
sys.meta_path[:] = [...]   # ← flagged: sys.meta_path access
```
**Severity:** LOW (blocked by CI)

### Attack 2.2: Import without triggering `find_spec`
**Target:** Import machinery bypass
**Method:** Use `importlib.util.find_spec()` + `module_from_spec()` + `exec_module()`.
**Result:** BLOCKED (CI gates)
**Evidence:**
```python
# importlib.import_module is caught by RUNTIME_IMPORT
# But importlib.util.find_spec is NOT the same as importlib.import_module
# CI gates check: func.attr == 'import_module' and func.value.id == 'importlib'
# importlib.util.find_spec would NOT match this pattern
```
Wait — let me be precise. The CI gate checks:
```python
isinstance(func.value, ast.Name) and func.value.id == 'importlib'
```
`importlib.util.find_spec` has AST: `Attribute(value=Attribute(value=Name('importlib'), attr='util'), attr='find_spec')`. The `func.value` is an `Attribute` node, not a `Name` node. So `isinstance(func.value, ast.Name)` is **False**.
**Result:** BYPASSED
**Severity:** HIGH
**Evidence (working PoC):**
```python
# In core_logic/evil.py:
import importlib.util
spec = importlib.util.find_spec("governance.signal_worker")  # NOT caught by CI gates
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # Module loaded, import guard bypassed
```
**But wait** — does `find_spec` trigger the meta_path hook? Yes, `importlib.util.find_spec` iterates `sys.meta_path` and calls each finder's `find_spec`. So the `_BoundaryEnforcer.find_spec` WOULD be called.

Let me re-examine the stack walking:
```python
frame = sys._getframe(1)  # caller of _BoundaryEnforcer.find_spec
# When triggered via importlib.util.find_spec, the call stack is:
# importlib.util.find_spec → meta_path iteration → _BoundaryEnforcer.find_spec
# Walking past importlib frames... the first non-importlib frame is the attacker's module
```
The import guard would still catch it because it walks past importlib frames and finds the real caller.
**Result:** BLOCKED (import guard catches it at runtime)
**Severity:** LOW
**Fix:** Add CI gate rule for `importlib.util.find_spec` and `importlib.util.module_from_spec` anyway (defence in depth).

### Attack 2.3: `exec()` an import statement with crafted `__name__`
**Target:** Caller detection in import guard
**Method:** `exec("from governance import X", {"__name__": "governance.internal"})` — the guard reads `frame.f_globals["__name__"]` to determine the caller package.
**Result:** BLOCKED (CI gates)
**Evidence:**
```python
# exec() is caught by DYNAMIC_CODE_EXEC rule
exec("from governance import signal_worker", {"__name__": "governance.fake"})
```
**Severity:** LOW (blocked by CI)

### Attack 2.4: Pre-loaded module in `sys.modules`
**Target:** Module cache bypass
**Method:** If `governance.signal_worker` is already in `sys.modules`, does `from governance.signal_worker import X` in `core_logic/` skip `find_spec`?
**Result:** BYPASSED
**Evidence:**
Python's import system checks `sys.modules` BEFORE calling `find_spec` on `sys.meta_path` finders. If the module is already cached, no finder is consulted.
```python
# If governance.signal_worker was imported earlier (e.g., by the worker process):
import sys
# sys.modules["governance.signal_worker"] already exists
# Now in core_logic/:
from governance.signal_worker import SignalWorker  # sys.modules hit — no find_spec call
```
**Severity:** HIGH
**Detail:** In a real MCAS process, the bootstrap imports governance modules before core_logic is loaded. By the time any core_logic code runs, `governance.*` modules are already in `sys.modules`. An attacker writing in `core_logic/` could `from governance.signal_worker import X` and it would work silently — the import guard never fires.

**Verification needed:** Does CPython always skip `find_spec` for cached modules?
- Python docs: "If the named module is already in `sys.modules`, then it is simply returned." (importlib docs)
- `from governance.X import Y` first resolves `governance.X` — if it's in `sys.modules`, `find_spec` is never called.

**Fix:** Two options:
1. **Verify at the end of bootstrap** that no core_logic module has imported governance by scanning `sys.modules`.
2. **Add a runtime check**: In the import guard, also hook `__getattr__` on governance modules to detect access from wrong callers (expensive, fragile).
3. **CI-level fix**: Add a CI gate rule that scans `core_logic/*.py` files for `import governance` or `from governance import` using AST `visit_Import` / `visit_ImportFrom`. This is the simplest and most robust fix.

### Attack 2.5: `__import__()` bypass
**Target:** Import guard via alternative import mechanism
**Method:** `__import__("governance.signal_worker")`
**Result:** BLOCKED (CI gates catch `__import__` via DYNAMIC_IMPORT rule)
**Evidence:**
```python
__import__("governance.signal_worker")  # ← DYNAMIC_IMPORT flagged
```
**Severity:** LOW (blocked by CI)
**Note:** Even if it got past CI gates, `__import__()` goes through the normal import machinery including `sys.meta_path` hooks.

---

## Attack Surface 3: CI Gates Bypass

### Attack 3.1: Construct forbidden calls dynamically via string concatenation
**Target:** AST scanner limitation
**Method:** `eval("getat" + "tr(obj, 'key', None)")` — the scanner can't see the runtime-assembled string.
**Result:** BLOCKED (CI gates catch `eval()`)
**Evidence:**
```python
eval("getat" + "tr(obj, 'key', None)")  # ← DYNAMIC_CODE_EXEC: eval()
```
**Severity:** LOW

### Attack 3.2: Access forbidden builtins via `__builtins__`
**Target:** AST scanner's function call detection
**Method:** `__builtins__['getattr'](obj, 'key', None)` — this is a subscript, not a call to `getattr`.
**Result:** BLOCKED (CI gates)
**Evidence:**
```python
# __builtins__ access involves __dict__ or subscript
# But more importantly, the CI gates check for __dict__ access
# __builtins__.__dict__['getattr'] → DUNDER_DICT_ACCESS
# __builtins__['getattr'] → this is a subscript on a module, but __builtins__
# is either a module or a dict depending on context
# In a governed module: __builtins__ is a module → __builtins__.getattr would be
# Attribute access → not caught for module-level attribute access
```
Actually, let me reconsider:
```python
f = __builtins__.__dict__['getattr']  # ← DUNDER_DICT_ACCESS on __builtins__
```
But:
```python
import builtins
f = builtins.getattr  # This is plain attribute access — NOT caught
f(registry, "secret", "default")  # This is a call to 'f', not 'getattr'
```
**Result:** BYPASSED
**Severity:** HIGH
**Evidence (working PoC):**
```python
# In any governed file:
import builtins
_ga = builtins.getattr  # NOT caught — it's attribute access on a module
# Wait — is this caught by BUILTIN_ALIAS?
# BUILTIN_ALIAS checks: isinstance(node.value, ast.Name) and node.value.id in _FORBIDDEN_BUILTIN_ALIASES
# builtins.getattr → node.value is Attribute(value=Name('builtins'), attr='getattr')
# The RHS is an Attribute node, not a Name node → NOT caught by BUILTIN_ALIAS
val = _ga(registry, "secret", "fallback")  # NOT caught — call to '_ga', not 'getattr'
```
The AST scanner checks for `Name(id='getattr')` calls, not arbitrary callables that happen to be `getattr`.
**Fix:** Add CI gate rule that flags `builtins.getattr`, `builtins.setattr`, `builtins.hasattr`, etc. Pattern: attribute access on `builtins` module for forbidden names.

### Attack 3.3: Use the `# EXEMPT:` escape hatch
**Target:** Exemption validation
**Method:** Add `# EXEMPT: legitimate use` to any line.
**Result:** BYPASSED
**Severity:** CRITICAL
**Evidence:**
```python
val = getattr(registry, "secret", None)  # EXEMPT: necessary for compatibility
```
The `_has_exempt_comment` function checks ONLY for `"# EXEMPT:"` in the line:
```python
def _has_exempt_comment(source: str, lineno: int) -> bool:
    lines = source.splitlines()
    if 0 < lineno <= len(lines):
        return "# EXEMPT:" in lines[lineno - 1]
    return False
```
There is **no validation** of the exemption reason. No allowlist. No review gate. Any string after `# EXEMPT:` works. A future CC instance could exempt any violation by appending this comment.
**Fix:** Options:
1. **Require exemptions to be in a governance-approved allowlist file** (e.g., `governance/kernel/exemptions.yaml`)
2. **Count exemptions in CI output** and fail if count exceeds a threshold
3. **Require exemptions in governance/kernel/ files only** — exemptions outside the kernel directory are rejected
4. **Human-reviewed exemption registry**: exemptions only valid if their SHA-256 hash is in a committed allowlist

### Attack 3.4: Put code in a scanner-skipped filename
**Target:** File exemption rules
**Method:** Name a file `test_helper.py` (starts with `test_`) and put governed code in it. Import from a governed module.
**Result:** BYPASSED
**Severity:** HIGH
**Evidence:**
```python
# File: governance/test_helper.py ← fully exempt from CI gates
import os
val = os.getenv("SECRET")  # Not scanned
forbidden = getattr(registry, "secret", "default")  # Not scanned

# File: governance/signal_worker.py
from governance.test_helper import forbidden  # Regular import, passes CI gates
```
The `_is_fully_exempt` function:
```python
def _is_fully_exempt(filename: str) -> bool:
    return filename in FULLY_EXEMPT_FILES or filename.startswith("test_")
```
Any file starting with `test_` is fully exempt. The exemption is by filename, not by directory. A `test_` file in `governance/` is just as exempt as one in `tests/`.
**Fix:** Restrict `test_` exemption to files under a `tests/` directory:
```python
def _is_fully_exempt(filepath: str) -> bool:
    filename = Path(filepath).name
    if filename in FULLY_EXEMPT_FILES:
        return True
    if filename.startswith("test_"):
        # Only exempt if under tests/ directory
        return 'tests' in Path(filepath).parts
    return False
```

### Attack 3.5: Bypass at the git hook level — `.pth` file
**Target:** Pre-commit hook scope
**Method:** Commit a `.pth` file that imports a module containing forbidden patterns. Python processes `.pth` files at startup from site-packages.
**Result:** BYPASSED
**Severity:** MEDIUM
**Evidence:**
```
# File: evil.pth (committed to repo)
import evil_bootstrap  # Runs at Python interpreter startup

# File: evil_bootstrap.py
import os
os._exit = lambda code: None  # Neuter all governance kills
```
The pre-commit hook only runs when `.py` files are staged:
```bash
git diff --cached --name-only --diff-filter=ACM | grep -q '\.py$' || exit 0
```
A `.pth` file is not a `.py` file — it won't trigger the hook. If it's added to the Docker image's site-packages, it would execute at interpreter startup before any governance code.
**Fix:** Extend hook to also check `.pth`, `.cfg`, `sitecustomize.py` files:
```bash
git diff --cached --name-only --diff-filter=ACM | grep -qE '\.(py|pth|cfg)$' || exit 0
```
Also: CI gates should scan `.pth` files for import statements.

### Attack 3.6: `operator.attrgetter` bypass
**Target:** AST scanner's function call detection
**Method:** `operator.attrgetter('_seal')(registry)` — this is not `getattr()`.
**Result:** BYPASSED
**Severity:** MEDIUM
**Evidence:**
```python
import operator
get_seal = operator.attrgetter('_seal')
seal_value = get_seal(registry)  # Gets _seal without triggering CI gates
# operator.attrgetter is not in the AST scanner's forbidden patterns
```
The CI scanner checks for `getattr()` calls by name. `operator.attrgetter` is a completely different code path.
**Fix:** Add `operator.attrgetter` and `operator.itemgetter` to CI gate checks:
```python
# In visit_Call: check for operator.attrgetter
if (isinstance(func, ast.Attribute) and func.attr == 'attrgetter'
    and isinstance(func.value, ast.Name) and func.value.id == 'operator'):
    self._add(node, "OPERATOR_ATTRGETTER", "operator.attrgetter bypasses getattr CI gates")
```

### Attack 3.7: Descriptor protocol — property that calls getattr inside `__get__`
**Target:** Indirect forbidden call
**Method:** Define a descriptor class whose `__get__` method calls `getattr` with a default. The CI scanner doesn't trace through descriptors.
**Result:** BYPASSED (code in descriptor body IS scanned, but...)
**Evidence:**
```python
class Extractor:
    def __init__(self, key):
        self.key = key
    def __get__(self, obj, cls):
        # This WOULD be caught if it used getattr(..., ..., default)
        # But we can use try/except + plain getattr (2 args)
        return getattr(obj, self.key)  # Only 2 args — NOT SILENT_GETATTR
```
Two-arg `getattr` is NOT flagged by CI gates (only 3+ args). And it raises AttributeError on missing keys — BUT on a FrozenRegistry, `__getattr__` calls `os._exit` for non-underscore attributes, so `AttributeError` is never raised. The 2-arg getattr is actually safe against FrozenRegistry.
**Severity:** LOW
**Detail:** The 2-arg getattr gap is intentional — it raises (or in this case, exits) instead of silently defaulting.

### Attack 3.8: Commit a binary `.pyc` file directly
**Target:** Pre-commit hook + CI scanner scope
**Method:** Compile forbidden code to `.pyc` and commit it. The scanner skips `__pycache__/`.
**Result:** PARTIAL BYPASS
**Evidence:**
```bash
python3 -c "import py_compile; py_compile.compile('evil.py', 'evil.cpython-312.pyc')"
git add evil.cpython-312.pyc
git commit  # .pyc is not .py — hook doesn't trigger
# But: Python only loads .pyc from __pycache__/ or with matching .py
```
Python's import system normally requires `.pyc` files to be in `__pycache__/` with the correct magic number and timestamp. Standalone `.pyc` files need explicit loading.
**Severity:** LOW
**Fix:** Hook should check for `.pyc` files being added outside `__pycache__/`.

---

## Attack Surface 4: Lineage Enforcement Bypass

### Attack 4.1: Compute seal independently and forge lineage
**Target:** Seal as a shared secret
**Method:** The seal is deterministic — same registry contents → same seal. An attacker who knows the registry contents can compute the seal independently.
**Result:** BYPASSED
**Severity:** CRITICAL
**Evidence:**
```python
# The seal computation is entirely from known data:
# 1. Registry contents are hardcoded in bootstrap.py (_populate_* functions)
# 2. Seal algorithm is SHA-256 of sorted JSON
# 3. graph_seal is SHA-256 of sorted (name, seal) pairs

# An attacker can replicate the entire bootstrap:
from governance.kernel import ConfigRegistry, StrategyRegistry, TaskRegistry, StopPolicyRegistry, RuntimeGraphSeal
# ... populate with same data ...
# ... freeze ...
# ... seal ...
# Now you have a valid Lineage you can stamp on anything

# OR: the seal is printed to stderr at every bootstrap:
# [BOOTSTRAP] Runtime graph sealed: abc123def456...
# It's in container logs: docker logs signal_worker_0
# It's visible to anyone with log access
```
The `process_id` is also computable: `SHA-256(graph_seal + ':' + epoch)[:16]`. If you know the graph_seal and bootstrap_epoch (both are in logs or stamps on any record), you can forge a complete Lineage.
**Fix:** Options:
1. **Add a process-local random nonce** to the seal — break determinism for forgery while keeping it deterministic per-process
2. **Don't log the full seal** — only log a truncated version
3. **Use HMAC instead of SHA-256** with a process-local secret key
4. Most practically: this doesn't matter if lineage enforcement is used to prevent *accidental* cross-talk, not adversarial forgery. The threat model should be clarified.

### Attack 4.2: Skip the check — write directly via `PersistenceClient`
**Target:** Lineage enforcement gap
**Method:** `PersistenceClient.execute_query()` writes to the DB without any lineage check.
**Result:** BYPASSED
**Severity:** CRITICAL
**Evidence:**
```python
# governance/persistence.py — PersistenceClient has NO lineage enforcement
from governance.persistence import PersistenceClient

PersistenceClient.execute_query("""
    INSERT INTO opportunities (symbol, timeframe, setup_id, decision, score, reason, status)
    VALUES (%s, %s, %s, %s, %s, %s, 'active')
""", ('BTCUSDT', '1h', 'D1', 'SIGNAL', 0.95, 'forged', ))
# No lineage columns set. No enforcement. Record created.
```
`GovernedPersistence` exists as a wrapper but `PersistenceClient` is the unwrapped utility and is importable by any governed module. There is no mechanism to prevent direct use of `PersistenceClient`.

Furthermore, the existing `signal_worker._persist_signal_to_pg` method uses its OWN connection pool (`_pool_get_conn`), NOT `GovernedPersistence`, and does NOT stamp lineage columns.
**Fix:**
1. **Remove `PersistenceClient` from public API** or make it private (`_PersistenceClient`)
2. **Add `NOT NULL` constraints** on lineage columns in DB — any write without lineage fails at DB level
3. **Wire `GovernedPersistence` into all existing write paths** (signal_worker, run_executor, etc.)

### Attack 4.3: Forge the Lineage dataclass manually
**Target:** Lineage object construction
**Method:** `Lineage(...)` constructor is public. Nothing prevents manual construction.
**Result:** BYPASSED
**Severity:** HIGH
**Evidence:**
```python
from governance.kernel.runtime_graph import Lineage

forged = Lineage(
    runtime_graph_seal="abc123...",  # Known from logs
    registry_seals=(("config", "..."), ("strategy", "..."), ("task", "..."), ("stop_policy", "...")),
    bootstrap_epoch=1710500000.0,
    process_id="1234567890abcdef",
)
# forged is a perfectly valid Lineage object
# It passes isinstance checks, has all methods, works with stamp_lineage
```
The Lineage dataclass is a pure value object with no construction restrictions. Any code that can import it can create one.
**Fix:**
1. **Lineage construction guard**: Make `Lineage.__init__` only callable from `RuntimeGraphSeal.seal()` by checking the call stack (fragile) or using a module-private token
2. **Use `__init_subclass__` or `__post_init__` to verify** the seal is consistent with registry_seals
3. Accept this as a design choice: Lineage is a value object, and forgery requires knowledge of the seal, which requires access to bootstrap logs

### Attack 4.4: `enforce_lineage()` is called manually — missing call paths
**Target:** Enforcement coverage
**Method:** Find governed code paths that write to DB without calling `enforce_lineage()` on incoming data.
**Result:** BYPASSED
**Severity:** CRITICAL
**Evidence:**
The persistence audit found:
- `signal_worker._persist_signal_to_pg()` writes to `opportunities` without stamping lineage
- The INSERT statement at signal_worker:~1230 does not include `runtime_graph_seal`, `process_id`, `bootstrap_epoch`
- `GovernedPersistence` exists but is not used in the actual write path
- `enforce_lineage()` is a method on `LineageEnforcer`, inherited by `SignalWorker` and `RunExecutor`, but there's no evidence it's called on incoming Redis payloads in the message consumption loop

The lineage enforcement infrastructure is **built but not fully wired into production code paths**.
**Fix:** This is the #1 priority fix:
1. **Replace `_pool_get_conn`** in signal_worker with `GovernedPersistence`
2. **Add lineage columns** to all INSERT/UPDATE statements in signal_worker
3. **Call `enforce_lineage()`** on every incoming Redis XREAD payload
4. **Add integration test**: insert an opportunity via signal_worker, verify lineage columns are non-null

---

## Attack Surface 5: Operational Bypasses

### Attack 5.1: Direct database writes bypass all governance
**Target:** PostgreSQL layer
**Method:** `docker exec backend-postgres-1 psql -U trading -d trading -c "INSERT INTO ..."`
**Result:** BYPASSED
**Severity:** CRITICAL
**Evidence:**
```bash
docker exec backend-postgres-1 psql -U trading -d trading -c "
INSERT INTO opportunities
    (symbol, timeframe, setup_id, decision, score, reason, status)
VALUES
    ('BTCUSDT', '1h', 'D1', 'SIGNAL', 0.99, 'manual injection', 'active');
"
# Success — no Python, no governance, no lineage
```
The lineage columns (`runtime_graph_seal`, `process_id`, `bootstrap_epoch`) are **nullable** — the migration uses `ADD COLUMN IF NOT EXISTS` without `NOT NULL`:
```sql
ADD COLUMN IF NOT EXISTS runtime_graph_seal VARCHAR(64),  -- nullable!
ADD COLUMN IF NOT EXISTS process_id VARCHAR(16),           -- nullable!
ADD COLUMN IF NOT EXISTS bootstrap_epoch DOUBLE PRECISION; -- nullable!
```
**Fix:**
1. **Add `NOT NULL` constraints** after backfilling existing rows:
   ```sql
   ALTER TABLE opportunities ALTER COLUMN runtime_graph_seal SET NOT NULL;
   ALTER TABLE opportunities ALTER COLUMN process_id SET NOT NULL;
   ALTER TABLE opportunities ALTER COLUMN bootstrap_epoch SET NOT NULL;
   ```
2. **Add a CHECK constraint** on `runtime_graph_seal` length: `CHECK (length(runtime_graph_seal) = 64)`
3. **Add a DB-level trigger** that validates seal format on INSERT/UPDATE (defense in depth)

### Attack 5.2: Environment variable injection changes config without changing seal
**Target:** Seal-config coupling
**Method:** Change env vars between container restarts.
**Result:** NOT A BYPASS (seal changes correctly)
**Evidence:**
The seal is computed from registry contents, which are populated from env vars. If env changes → registry contents change → seal changes → old payloads are shunned.
**Severity:** N/A
**Detail:** This is working as designed. Different env = different seal = peer rejection of old payloads.

### Attack 5.3: Hot-patching `_LINEAGE` in a running container
**Target:** Module-level singleton
**Method:** `docker exec` into container and overwrite the singleton.
**Result:** BYPASSED
**Severity:** HIGH
**Evidence:**
```bash
docker exec -it signal_worker_0 python3 -c "
import governance.bootstrap
from governance.kernel.runtime_graph import Lineage
# governance.bootstrap._LINEAGE is a module-level variable
# It CAN be reassigned — there is no __setattr__ guard on the module
governance.bootstrap._LINEAGE = Lineage(
    runtime_graph_seal='forged',
    registry_seals=(('config','a'),('strategy','b'),('task','c'),('stop_policy','d')),
    bootstrap_epoch=0.0,
    process_id='deadbeefdeadbeef',
)
print('Lineage replaced:', governance.bootstrap.get_lineage())
"
```
Wait — this runs in a **separate Python process** (`python3 -c "..."`), not in the container's running worker process. The worker process's `_LINEAGE` is unaffected. To affect the running process, you'd need to connect to its interpreter (e.g., via a debug endpoint or signal handler).
**Result:** NOT A BYPASS (separate process isolation)
**Severity:** LOW
**Detail:** Module-level singletons are per-process. `docker exec python3 -c` starts a new process. To hot-patch the running process, you'd need:
- A debug endpoint (not present)
- A `/proc/<pid>/mem` write (requires root + knowledge of memory layout)
- A UNIX socket debug server (not running)
**Fix:** No fix needed — process isolation handles this.

### Attack 5.4: Stale `__pycache__` from pre-governance code
**Target:** Python bytecode cache
**Method:** If `.pyc` files from before the governance migration survive in the Docker image, Python may load them instead of the new `.py` source.
**Result:** PARTIAL BYPASS (unlikely but possible)
**Evidence:**
```bash
# If the Docker build doesn't clear __pycache__:
# Old .pyc files may have: getattr with defaults, os.getenv reads, etc.
# Python checks mtime: if .pyc is newer than .py, it loads .pyc
# Docker COPY preserves timestamps from the build context
```
**Severity:** MEDIUM
**Fix:** Add to Dockerfile:
```dockerfile
RUN find /backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
```
Or set `PYTHONDONTWRITEBYTECODE=1` in the Docker environment (already a common practice).

---

## Summary

| # | Attack | Target | Result | Severity |
|---|--------|--------|--------|----------|
| 1.1b | Hold `_entries` ref across freeze | FrozenRegistry | BYPASSED (harmless) | LOW |
| 1.3 | Monkey-patch `os._exit` | Process death | BYPASSED | **HIGH** |
| 1.5 | ctypes C-level setattr | FrozenRegistry | BYPASSED | MEDIUM |
| 2.4 | Pre-loaded module in sys.modules | Import guard | BYPASSED | **HIGH** |
| 3.2 | `builtins.getattr` alias | CI gates | BYPASSED | **HIGH** |
| 3.3 | `# EXEMPT:` without validation | CI gates | BYPASSED | **CRITICAL** |
| 3.4 | `test_` filename in governed dir | CI gates | BYPASSED | **HIGH** |
| 3.5 | `.pth` file bypass hook trigger | Pre-commit | BYPASSED | MEDIUM |
| 3.6 | `operator.attrgetter` | CI gates | BYPASSED | MEDIUM |
| 4.1 | Compute seal from known data | Lineage | BYPASSED | **CRITICAL** |
| 4.2 | Direct `PersistenceClient` write | Lineage | BYPASSED | **CRITICAL** |
| 4.3 | Forge Lineage constructor | Lineage | BYPASSED | HIGH |
| 4.4 | Missing `enforce_lineage()` calls | Lineage | BYPASSED | **CRITICAL** |
| 5.1 | Direct psql INSERT (nullable cols) | DB lineage | BYPASSED | **CRITICAL** |
| 5.4 | Stale __pycache__ | CI gates | PARTIAL | MEDIUM |

### Critical Fixes (Priority Order)

1. **DB-level NOT NULL** on lineage columns (closes 5.1 — the widest hole)
2. **Wire `GovernedPersistence` into signal_worker** and all write paths (closes 4.4)
3. **Add `enforce_lineage()` calls** on all incoming Redis payloads (closes 4.4)
4. **Restrict `# EXEMPT:` to governance/kernel/ only** or require allowlist (closes 3.3)
5. **Restrict `test_` exemption to `tests/` directory** (closes 3.4)
6. **Add CI gate for `builtins.getattr/setattr/hasattr`** (closes 3.2)
7. **Add CI gate for `os._exit` reassignment** (closes 1.3)
8. **Add CI gate for `operator.attrgetter`** (closes 3.6)
9. **Add CI gate for `ctypes`/`cffi` imports** (closes 1.5)
10. **Add `.pth` scanning to pre-commit hook** (closes 3.5)
11. **AST-level `from governance` check in `core_logic/` files** as CI gate (closes 2.4)
12. **Clear `__pycache__` in Dockerfile** (closes 5.4)
