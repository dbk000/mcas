"""CI gate violation #19 — function body is Ellipsis or pass without @abstractmethod.

Protects the governance law (root CLAUDE.md, §2.2): every function body is real
work or `raise NotImplementedError("<reason + plan ref>")`. A bare `...` or `pass`
looks "already stubbed" to downstream tools — the check must fire on it.

Real `@abstractmethod`-decorated interfaces are exempt; single-statement bodies
with real statements (assignments, returns, raise) must not fire.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Allow running from Backend/ or repo root — mirror pattern in test_counter_semantics.py.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(_HERE))
_REPO = os.path.dirname(_BACKEND)
for p in (_REPO, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

from governance.kernel.ci_gates import scan_file  # noqa: E402


def _write_tmp(content: str) -> str:
    """Write `content` to a temporary .py file and return its path.

    The file is NOT deleted by the helper — pytest's tmp_path fixture would be
    cleaner, but scan_file takes a filepath string, so a direct tempfile keeps
    the test free of fixture plumbing. Tests clean up via os.unlink.
    """
    fd, path = tempfile.mkstemp(suffix='.py', prefix='cigate_fixture_')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def _filter_v19(violations):
    return [v for v in violations if v.rule == 'ELLIPSIS_OR_PASS_BODY']


def test_ellipsis_body_fires():
    """Single-statement Ellipsis body on a plain function must fire."""
    path = _write_tmp("def foo():\n    ...\n")
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 1, f"expected 1 hit, got {len(hits)}: {[str(v) for v in hits]}"
        assert "'foo'" in hits[0].detail
        assert "Ellipsis" in hits[0].detail
        assert hits[0].line == 1
    finally:
        os.unlink(path)


def test_pass_body_fires():
    """Single-statement `pass` body on a plain function must fire."""
    path = _write_tmp("def foo():\n    pass\n")
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 1, f"expected 1 hit, got {len(hits)}: {[str(v) for v in hits]}"
        assert "'foo'" in hits[0].detail
        assert "pass" in hits[0].detail
        assert hits[0].line == 1
    finally:
        os.unlink(path)


def test_abstractmethod_ellipsis_exempt():
    """`@abstractmethod` with Ellipsis body is a real abstract interface; must NOT fire."""
    src = (
        "from abc import abstractmethod\n"
        "class X:\n"
        "    @abstractmethod\n"
        "    def foo(self):\n"
        "        ...\n"
    )
    path = _write_tmp(src)
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 0, f"expected 0 hits, got {len(hits)}: {[str(v) for v in hits]}"
    finally:
        os.unlink(path)


def test_abstractmethod_pass_exempt():
    """`@abstractmethod` with `pass` body is also exempt."""
    src = (
        "from abc import abstractmethod\n"
        "class X:\n"
        "    @abstractmethod\n"
        "    def foo(self):\n"
        "        pass\n"
    )
    path = _write_tmp(src)
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 0, f"expected 0 hits, got {len(hits)}: {[str(v) for v in hits]}"
    finally:
        os.unlink(path)


def test_real_body_does_not_fire():
    """Real work (assignment + return) must not fire."""
    path = _write_tmp("def foo():\n    x = 1\n    return x\n")
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 0, f"expected 0 hits, got {len(hits)}: {[str(v) for v in hits]}"
    finally:
        os.unlink(path)


def test_raise_not_implemented_does_not_fire():
    """The canonical stub form (raise NotImplementedError) must not fire."""
    path = _write_tmp("def foo():\n    raise NotImplementedError('R7 plan ref')\n")
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 0, f"expected 0 hits, got {len(hits)}: {[str(v) for v in hits]}"
    finally:
        os.unlink(path)


def test_qualified_abstractmethod_exempt():
    """`@abc.abstractmethod` (qualified attribute form) is also exempt."""
    src = (
        "import abc\n"
        "class X:\n"
        "    @abc.abstractmethod\n"
        "    def foo(self):\n"
        "        ...\n"
    )
    path = _write_tmp(src)
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 0, f"expected 0 hits, got {len(hits)}: {[str(v) for v in hits]}"
    finally:
        os.unlink(path)


def test_async_function_fires():
    """`async def foo(): ...` must also fire (AsyncFunctionDef path)."""
    path = _write_tmp("async def foo():\n    ...\n")
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 1, f"expected 1 hit, got {len(hits)}: {[str(v) for v in hits]}"
        assert "'foo'" in hits[0].detail
    finally:
        os.unlink(path)


def test_docstring_plus_ellipsis_does_not_fire_len_gt_1():
    """Two-statement body (docstring + Ellipsis) is out of scope for this rule.

    This is a deliberate gap — the rule targets bodies with exactly one
    statement. Documented in findings/R7/findings.md as a stress-test target.
    """
    path = _write_tmp('def foo():\n    """docstring"""\n    ...\n')
    try:
        hits = _filter_v19(scan_file(path))
        assert len(hits) == 0
    finally:
        os.unlink(path)
