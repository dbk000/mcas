# R7 — CI-gate extension: catch `...` and `pass` as sole function bodies

**Item.** R7 (playbook §5).
**Owner.** W2 slot, batch execution 2026-04-16T21:49Z.
**Status.** PASS.

## Decisions made

- **Violation number is #19.** `ci_gates.py`'s docstring lists checks 1-18; appended #19 = `ELLIPSIS_OR_PASS_BODY`. Matches brief.
- **Rule detects exactly two single-statement bodies:** `[Pass]` and `[Expr(Constant(value=Ellipsis))]`. Anything else - including two-statement `docstring + Ellipsis`, `return None`, `raise NotImplementedError(...)`, real work - is not flagged. This is a deliberately narrow rule aligned to the brief; the two patterns are the ones the governance law names.
- **Exemption via `@abstractmethod` decorator.** Matches both bare-name form (`@abstractmethod`) and attribute form (`@abc.abstractmethod`). Also supports the called-decorator form (`@abstractmethod()`) by unwrapping `ast.Call.func`. No other decorator exempts.
- **Added `visit_FunctionDef` AND `visit_AsyncFunctionDef`.** Both call the same private `_check_empty_body` helper. Async stubs are just as dangerous as sync stubs.
- **Detail string includes function name and body kind** (`Ellipsis` vs `pass`) to make grep-triage from the full-repo scan trivial.
- **No change to `FULLY_EXEMPT_FILES` or `SCRIPTS_EXEMPT_RULES`.** `ci_gates.py` itself is already in `FULLY_EXEMPT_FILES` - a self-scan of `ci_gates.py` is trivially CLEAN regardless of content. Step 3 of the brief ("self-scan shows no violations") is satisfied by exemption, not by virtue of the file being violation-free in absolute terms. Noted as a gotcha below.
- **New test file lives at `Backend/tests/governance/test_ci_gates_violation_19.py`.** Created a new `tests/governance/` subdirectory (with `__init__.py`) since no governance-focused tests existed in `Backend/tests/` previously. `pytest.ini`'s `testpaths = tests` discovers it automatically.

## Data sampled

### 1. Python AST shape verification (host Python 3.12.3, same as container)

```
>>> ast.dump(ast.parse('def foo(): ...').body[0].body[0])
"Expr(value=Constant(value=Ellipsis))"
>>> ast.dump(ast.parse('def foo(): pass').body[0].body[0])
'Pass()'
```

Confirmed the `Constant(value=Ellipsis)` shape is current and stable (brief flagged potential 3.11 vs 3.12 drift - not an issue; deployment is 3.12 per `Backend/Dockerfile.computers` line 1).

### 2. Baseline full-repo scan (pre-modification)

- Command: `cd Backend && python3 -m governance.kernel.ci_gates .`
- Pre-violation count: **248** (see `full_repo_scan_before.log`).
- First two lines:
  ```
  CI DOCTRINE GATES: 248 VIOLATION(S) FOUND
  ========================================================================
  ```

### 3. Post-modification full-repo scan

- Command: `cd Backend && python3 -m governance.kernel.ci_gates .`
- Post-violation count: **248** (see `full_repo_scan_after.log`, also `full_repo_scan_output.log`).
- New ELLIPSIS_OR_PASS_BODY hits: **0**.
- Diff of before vs after scan logs: **IDENTICAL**.

### 4. Independent enumeration via raw AST walk

A one-off script walked every `.py` file under `Backend/` and collected functions whose body is exactly `[Pass]` or `[Expr(Constant(Ellipsis))]`. Result: **0 functions** match, across the entire Backend tree. The gate's zero-hit count on real code is a true negative, not a false negative. Artifact: `ast_walk_enumeration.log`.

Symptom-of-this: three grep hits for standalone `...` in Backend (`core_logic/setup_base.py:244`, `governance/equities/base.py:51`, `scripts/compute_asset_macro_beta.py:57`) are:
- `setup_base.py:244` -> `...` inside a kwarg list of `SetupMetadata(...)` in a docstring example.
- `equities/base.py:51` -> two-statement body `docstring + Ellipsis` on an `@abstractmethod` interface (exempt on two grounds: len(body)==2 and `@abstractmethod`).
- `compute_asset_macro_beta.py:57` -> `...` inside a module-level docstring string literal, not a function body.

None of these are policy violations under the brief's rule.

### 5. Pytest run

```
9 passed in 0.03s
```

Full log: `pytest_test_ci_gates_violation_19.log`.

Covers 9 cases: Ellipsis-fires, pass-fires, @abstractmethod-Ellipsis-exempt, @abstractmethod-pass-exempt, real-body-exempt, raise-NotImplementedError-exempt, @abc.abstractmethod-qualified-exempt, async-function-fires, docstring+Ellipsis-not-fired (len(body)==2 gap).

## Cross-checks

- **Manual seeded-violation test.** `/tmp/cigate_test/seeded.py` with four functions (`foo=...`, `bar=pass`, `abstract_method=@abstractmethod+...`, `has_real_body`) scanned directly via `python3 -m governance.kernel.ci_gates`. Output: 2 hits (`foo`, `bar`), exit code 1. Two exempt cases (`abstract_method`, `has_real_body`) not reported. Matches pytest results.
- **Before/after log diff.** `diff full_repo_scan_before.log full_repo_scan_after.log` -> identical (no new false positives, no disappearing true positives).
- **Self-scan of new test file.** `python3 -m governance.kernel.ci_gates tests/governance/test_ci_gates_violation_19.py` -> CLEAN (test file matches `test_` prefix in `_is_fully_exempt`).

## Gotchas

- **Self-scan is trivially CLEAN due to `FULLY_EXEMPT_FILES`.** `ci_gates.py` is its own exempt-file list entry. Anyone relying on the self-scan as a sanity check should understand: it validates that the file parses, not that its content is doctrine-compliant. This is existing behaviour, preserved.
- **Rule is narrow - single-statement bodies only.** A function with `docstring + Ellipsis` (two statements) is NOT flagged. Brief's rule is explicit (`len(body) == 1`); I did not broaden it. If Darren wants docstring+ellipsis caught too, that's a follow-up rule (or loosen the `len == 1` clamp to "last statement is pass/ellipsis, no meaningful code before it").
- **Decorator detection handles `@abstractmethod`, `@abc.abstractmethod`, and `@abstractmethod(...)` (called).** Does NOT handle `@MyCustomDecorator(...)` that wraps abstractmethod via composition - those would not be exempted. No such cases exist in Backend as of 2026-04-16.
- **Codebase is already 100% clean of empty-body functions.** The rule fires on zero existing code. This looks suspicious but is real: independent AST enumeration (script above) confirms zero cases. Either (a) this rule was already enforced by convention, or (b) recent cleanup removed them.
- **Test file uses `tempfile.mkstemp` not pytest's `tmp_path` fixture.** Kept it dependency-light since `scan_file` takes a path string. Tests clean up via `os.unlink` in `finally`.

## Stress-test targets

Cases not exercised by the current test set; noted for future hardening:

1. **Function with `docstring + pass` (two statements).** Not flagged by design. If Darren wants this caught, either broaden `len == 1` to "trailing Pass/Ellipsis after docstring" or add a companion rule.
2. **Static/class/property decorators combined with abstractmethod.** E.g. `@staticmethod` + `@abstractmethod`. The current decorator walk scans ALL decorators, so this is exempted correctly - but add an explicit test.
3. **Nested function with Ellipsis body.** `def outer():` with inner `def inner(): ...` - the visitor walks both, so this would fire on `inner`. No explicit test; add one if nested stubs become common.
4. **Lambda expressions.** Python can't express a "lambda with only `...`" cleanly (`lambda: ...` is valid and returns Ellipsis). Not within scope; lambdas are not `FunctionDef`.
5. **Class body with only Ellipsis/Pass.** Class-level `class Foo: ...` is a valid empty-class stub. Not covered; brief scopes to functions. Add a separate rule if needed.
6. **ci_gates.py's own exempt status.** If anyone adds a genuine violation to ci_gates.py, the self-scan won't catch it. Consider removing from `FULLY_EXEMPT_FILES` (but then the file needs to avoid its own forbidden patterns - currently it uses `getattr(node, 'body', None)` which is 3-arg getattr and would trip SILENT_GETATTR). This is pre-existing design tension, not something R7 introduced.

## Commit

See `commit_sha.txt`.

## Artifacts in this directory

- `findings.md` (this file)
- `ci_gates_self_scan_before.log` - `python -m governance.kernel.ci_gates governance/kernel/ci_gates.py` pre-edit (CLEAN - file is exempt)
- `ci_gates_self_scan_after.log` - same command post-edit (CLEAN)
- `full_repo_scan_before.log` - `python -m governance.kernel.ci_gates .` pre-edit (248 violations)
- `full_repo_scan_after.log` - same post-edit (248 - no regression)
- `full_repo_scan_output.log` - alias of `full_repo_scan_after.log` per brief artifact naming
- `pytest_test_ci_gates_violation_19.log` - 9/9 green
- `ast_walk_enumeration.log` - independent enumeration of empty-body functions (0 found)
- `reproduction.md` - one-screen cold reproduction steps
- `commit_sha.txt` - short SHA of R7 commit
