"""
CI Doctrine Gates — static analysis enforcement for MCAS governance.

This is not grep. This is AST-level inspection that catches the actual
code patterns, not string approximations.

Run as: python -m governance.kernel.ci_gates <path_to_backend>

Exit codes:
    0  — clean
    1  — violations found (build must fail)

Violations caught:
    1. os.getenv / os.environ outside governance/bootstrap.py
    2. getattr(obj, name, default) in governed paths (silent defaulting)
    3. hasattr() in governed paths (existence checks = dynamic dispatch)
    4. importlib usage (runtime imports = dynamic logic)
    5. setattr() in governed paths (runtime mutation)
    6. inspect module usage (reflective behaviour)
    7. globals() / locals() for dispatch (variable-as-function)
    8. __getattribute__ calls (CI gate bypass pattern)
    9. vars() calls (dynamic attribute introspection)
   10. try/except AttributeError (silent attribute fallback)
   11. eval() / exec() / compile() builtins (dynamic code execution)
   12. __import__() (dynamic import)
   13. __dict__ access (dynamic attribute introspection)
   14. object.__setattr__ / type.__setattr__ (frozen object bypass)
   15. bare except / except SystemExit / except BaseException / except KeyboardInterrupt
   16. builtin aliasing (ga = getattr, e = exec, etc.)
   17. sys.meta_path / sys.path access
   18. dunder reassignment (ClassName.__method__ = ...)
"""

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Violation:
    file: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"  {self.file}:{self.line} [{self.rule}] {self.detail}"


@dataclass
class GateResults:
    violations: List[Violation] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return len(self.violations) == 0

    def report(self) -> str:
        if self.clean:
            return "CI DOCTRINE GATES: CLEAN"
        lines = [
            f"CI DOCTRINE GATES: {len(self.violations)} VIOLATION(S) FOUND",
            "=" * 72,
        ]
        by_rule = {}
        for v in self.violations:
            by_rule.setdefault(v.rule, []).append(v)
        for rule, vs in sorted(by_rule.items()):
            lines.append(f"\n[{rule}] — {len(vs)} occurrence(s)")
            for v in vs:
                lines.append(str(v))
        lines.append("\n" + "=" * 72)
        lines.append("Fix all violations before merging.")
        return "\n".join(lines)


# ============================================================================
# Paths exempt from certain rules
# ============================================================================

# Only bootstrap.py may read environment variables
ENV_EXEMPT = {"bootstrap.py"}

# The CI gates file itself, test files, and import_guard (uses sys.meta_path) are exempt
# V14 fix: exact match for ci_gates.py/conftest.py/import_guard.py, prefix match for test_
FULLY_EXEMPT_FILES = {"ci_gates.py", "conftest.py", "import_guard.py"}

# Scripts are scanned for ALL rules but ENV_OUTSIDE_BOOTSTRAP is exempted
# since scripts don't go through bootstrap and legitimately use env vars
SCRIPTS_EXEMPT_RULES = {"ENV_OUTSIDE_BOOTSTRAP"}

# Builtins that must not be aliased to local names
_FORBIDDEN_BUILTIN_ALIASES = frozenset({
    'getattr', 'setattr', 'hasattr', 'exec', 'eval',
    'compile', '__import__', 'globals', 'locals', 'vars',
})

# Exception types that governance code must not catch
_GOVERNANCE_EXCEPTIONS = frozenset({
    'SystemExit', 'BaseException', 'KeyboardInterrupt',
})


def _is_fully_exempt(filename: str) -> bool:
    """Check if a file is fully exempt from all gates."""
    return filename in FULLY_EXEMPT_FILES or filename.startswith("test_")


def _has_exempt_comment(source: str, lineno: int) -> bool:
    """Check if a line has an # EXEMPT: comment."""
    lines = source.splitlines()
    if 0 < lineno <= len(lines):
        return "# EXEMPT:" in lines[lineno - 1]
    return False


def _is_under_scripts(filepath: str) -> bool:
    """Check if a file is under the scripts/ directory."""
    parts = Path(filepath).parts
    return 'scripts' in parts


class DoctrineVisitor(ast.NodeVisitor):
    """AST visitor that flags governance violations."""

    def __init__(self, filepath: str, source: str) -> None:
        self.filepath = filepath
        self.filename = Path(filepath).name
        self.source = source
        self.violations: List[Violation] = []

    def _add(self, node: ast.AST, rule: str, detail: str) -> None:
        # Allow explicit exemptions via # EXEMPT: comment
        if _has_exempt_comment(self.source, node.lineno):
            return
        # Scripts are exempt from certain rules
        if _is_under_scripts(self.filepath) and rule in SCRIPTS_EXEMPT_RULES:
            return
        self.violations.append(Violation(
            file=self.filepath,
            line=node.lineno,
            rule=rule,
            detail=detail,
        ))

    # --- Rule 1: os.getenv / os.environ outside bootstrap.py ---

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.filename not in ENV_EXEMPT:
            # os.getenv
            if (
                node.attr == 'getenv'
                and isinstance(node.value, ast.Name)
                and node.value.id == 'os'
            ):
                self._add(node, "ENV_OUTSIDE_BOOTSTRAP",
                           "os.getenv() only allowed in bootstrap.py")
            # os.environ
            if (
                node.attr == 'environ'
                and isinstance(node.value, ast.Name)
                and node.value.id == 'os'
            ):
                self._add(node, "ENV_OUTSIDE_BOOTSTRAP",
                           "os.environ only allowed in bootstrap.py")

        # V12: __dict__ access — dynamic attribute introspection
        if node.attr == '__dict__':
            self._add(node, "DUNDER_DICT_ACCESS",
                       "__dict__ access enables dynamic attribute introspection; "
                       "use dataclasses.asdict() or explicit field access")

        # V13: object.__setattr__ / type.__setattr__ — frozen object bypass
        if (
            node.attr == '__setattr__'
            and isinstance(node.value, ast.Name)
            and node.value.id in ('object', 'type')
        ):
            self._add(node, "FROZEN_BYPASS_SETATTR",
                       f"{node.value.id}.__setattr__() bypasses frozen object protections")

        # sys.meta_path access — import system manipulation
        if (
            node.attr == 'meta_path'
            and isinstance(node.value, ast.Name)
            and node.value.id == 'sys'
        ):
            self._add(node, "META_PATH_ACCESS",
                       "sys.meta_path access enables import system manipulation")

        # sys.path access — import path manipulation
        if (
            node.attr == 'path'
            and isinstance(node.value, ast.Name)
            and node.value.id == 'sys'
        ):
            self._add(node, "SYS_PATH_ACCESS",
                       "sys.path access enables import path manipulation")

        self.generic_visit(node)

    # --- Rules 2-13: Forbidden function calls ---

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func

        # getattr(obj, name, default) — 3 args = silent defaulting
        if isinstance(func, ast.Name) and func.id == 'getattr':
            if len(node.args) >= 3:
                self._add(node, "SILENT_GETATTR",
                           "getattr() with default silently hides missing dependencies")

        # hasattr() — existence check enables dynamic dispatch
        if isinstance(func, ast.Name) and func.id == 'hasattr':
            self._add(node, "HASATTR_DISPATCH",
                       "hasattr() enables dynamic dispatch; "
                       "use explicit registry attribute access")

        # setattr()
        if isinstance(func, ast.Name) and func.id == 'setattr':
            self._add(node, "RUNTIME_SETATTR",
                       "setattr() enables runtime mutation of governed objects")

        # globals() / locals() for dispatch
        if isinstance(func, ast.Name) and func.id in ('globals', 'locals'):
            self._add(node, "DYNAMIC_DISPATCH",
                       f"{func.id}() enables variable-as-function dispatch")

        # importlib.import_module
        if (
            isinstance(func, ast.Attribute)
            and func.attr == 'import_module'
            and isinstance(func.value, ast.Name)
            and func.value.id == 'importlib'
        ):
            self._add(node, "RUNTIME_IMPORT",
                       "importlib.import_module() is dynamic logic; "
                       "all imports must resolve at module load time")

        # V7: __getattribute__ calls — CI gate bypass pattern
        if isinstance(func, ast.Attribute) and func.attr == '__getattribute__':
            self._add(node, "GETATTRIBUTE_BYPASS",
                       "__getattribute__() bypasses CI gate getattr checks; "
                       "use direct attribute access")

        # V8: vars() — dynamic attribute introspection
        if isinstance(func, ast.Name) and func.id == 'vars':
            self._add(node, "VARS_INTROSPECTION",
                       "vars() enables dynamic attribute introspection; "
                       "use dataclasses.asdict() or explicit field access")

        # V10: eval() / exec() / compile() builtins — dynamic code execution
        # Only flag bare name calls (ast.Name), not method calls like r.eval()
        if isinstance(func, ast.Name) and func.id in ('eval', 'exec', 'compile'):
            self._add(node, "DYNAMIC_CODE_EXEC",
                       f"{func.id}() enables dynamic code execution")

        # V11: __import__() — dynamic import
        if isinstance(func, ast.Name) and func.id == '__import__':
            self._add(node, "DYNAMIC_IMPORT",
                       "__import__() is dynamic logic; "
                       "use static import statements")

        self.generic_visit(node)

    # --- Rule 6: inspect module import ---

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == 'inspect' or alias.name.startswith('inspect.'):
                self._add(node, "REFLECTIVE_IMPORT",
                           "inspect module enables reflective behaviour")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and (
            node.module == 'inspect' or node.module.startswith('inspect.')
        ):
            self._add(node, "REFLECTIVE_IMPORT",
                       "inspect module enables reflective behaviour")
        self.generic_visit(node)

    # --- Exception handler rules ---

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # V9: try/except AttributeError — silent attribute fallback
        if (
            node.type is not None
            and isinstance(node.type, ast.Name)
            and node.type.id == 'AttributeError'
        ):
            self._add(node, "SILENT_ATTR_FALLBACK",
                       "try/except AttributeError silently hides missing attributes; "
                       "use declared fields or registry access. "
                       "Add '# EXEMPT: <reason>' comment to suppress if legitimate")

        # Bare except: — catches everything including SystemExit
        if node.type is None:
            self._add(node, "BARE_EXCEPT",
                       "bare except: catches SystemExit and governance kills; "
                       "use explicit exception types")

        # except SystemExit / BaseException / KeyboardInterrupt
        if node.type is not None:
            if isinstance(node.type, ast.Name) and node.type.id in _GOVERNANCE_EXCEPTIONS:
                self._add(node, "GOVERNANCE_EXCEPTION_CATCH",
                           f"except {node.type.id} intercepts governance process death; "
                           f"governance kills must be uncatchable")
            # Tuple handlers like except (SystemExit, Exception):
            if isinstance(node.type, ast.Tuple):
                for elt in node.type.elts:
                    if isinstance(elt, ast.Name) and elt.id in _GOVERNANCE_EXCEPTIONS:
                        self._add(node, "GOVERNANCE_EXCEPTION_CATCH",
                                   f"except (..., {elt.id}, ...) intercepts governance process death; "
                                   f"governance kills must be uncatchable")

        self.generic_visit(node)

    # --- Assignment rules: builtin aliasing + dunder reassignment ---

    def visit_Assign(self, node: ast.Assign) -> None:
        # Builtin aliasing: ga = getattr, e = exec, etc.
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in _FORBIDDEN_BUILTIN_ALIASES
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._add(node, "BUILTIN_ALIAS",
                               f"{target.id} = {node.value.id} — aliasing forbidden builtin "
                               f"bypasses CI gate detection")

        # Dunder reassignment: ClassName.__method__ = ...
        # self.__x = ... in __init__ is legitimate; anything else is an attack
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr.startswith('__')
                and target.attr.endswith('__')
                and len(target.attr) > 4  # at least __x__
            ):
                # Skip self.__dunder__ = ... (legitimate in __init__)
                if isinstance(target.value, ast.Name) and target.value.id == 'self':
                    continue
                self._add(node, "DUNDER_REASSIGNMENT",
                           f"{ast.dump(target.value) if isinstance(target.value, ast.Name) else '...'}"
                           f".{target.attr} = ... — dunder reassignment on non-self target "
                           f"enables governance bypass")

        self.generic_visit(node)


def scan_file(filepath: str) -> List[Violation]:
    """Parse and scan a single Python file."""
    filename = Path(filepath).name
    # V14 fix: exact match for known files, startswith for test_ prefix
    if _is_fully_exempt(filename):
        return []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return []

    # V15 fix: unparseable files are violations, not silent passes
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        return [Violation(
            file=filepath,
            line=e.lineno or 0,
            rule="PARSE_ERROR",
            detail=f"SyntaxError: {e.msg} — unparseable files cannot be governance-checked",
        )]

    visitor = DoctrineVisitor(filepath, source)
    visitor.visit(tree)
    return visitor.violations


def scan_directory(root: str) -> GateResults:
    """Scan all Python files under root."""
    results = GateResults()
    root_path = Path(root)

    for py_file in sorted(root_path.rglob("*.py")):
        # Skip __pycache__ and dot-directories only
        parts = py_file.parts
        if any(p.startswith('.') or p in ('__pycache__',) for p in parts):
            continue
        violations = scan_file(str(py_file))
        results.violations.extend(violations)

    return results


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m governance.kernel.ci_gates <path>", file=sys.stderr)
        sys.exit(2)

    target = sys.argv[1]
    target_path = Path(target)

    if target_path.is_file():
        violations = scan_file(str(target_path))
        results = GateResults(violations=violations)
    elif target_path.is_dir():
        results = scan_directory(str(target_path))
    else:
        print(f"Path not found: {target}", file=sys.stderr)
        sys.exit(2)

    print(results.report())
    sys.exit(0 if results.clean else 1)


if __name__ == '__main__':
    main()
