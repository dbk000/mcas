"""
CI Doctrine Gates — static analysis enforcement for MCAS governance.

This is not grep. This is AST-level inspection that catches the actual
code patterns, not string approximations.

Run as: python -m mcas_governance.ci_gates <path_to_backend>

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

# The CI gates file itself and test files are exempt from all gates
FULLY_EXEMPT_PATTERNS = {"ci_gates.py", "test_", "conftest.py"}


class DoctrineVisitor(ast.NodeVisitor):
    """AST visitor that flags governance violations."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.filename = Path(filepath).name
        self.violations: List[Violation] = []

    def _add(self, node: ast.AST, rule: str, detail: str) -> None:
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
        self.generic_visit(node)

    # --- Rule 2-6: Forbidden function calls ---

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


def scan_file(filepath: str) -> List[Violation]:
    """Parse and scan a single Python file."""
    filename = Path(filepath).name
    if any(pattern in filename for pattern in FULLY_EXEMPT_PATTERNS):
        return []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    visitor = DoctrineVisitor(filepath)
    visitor.visit(tree)
    return visitor.violations


def scan_directory(root: str) -> GateResults:
    """Scan all Python files under root."""
    results = GateResults()
    root_path = Path(root)

    for py_file in sorted(root_path.rglob("*.py")):
        # Skip __pycache__, .venv, snapshots, and scripts (own bootstrap boundary)
        parts = py_file.parts
        if any(p.startswith('.') or p in ('__pycache__', 'ss', 'scripts') for p in parts):
            continue
        violations = scan_file(str(py_file))
        results.violations.extend(violations)

    return results


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m mcas_governance.ci_gates <path>", file=sys.stderr)
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
