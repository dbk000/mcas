"""
Import Boundary Enforcer — makes reversed dependency arrows physically impossible.

Architecture: core_logic/ → governance/ → api/
Imports flow DOWN only. core_logic is pure math. It cannot reach up into
governance (orchestration) or api (presentation).

This is not a linter. This is not a CI check. This is a sys.meta_path hook
that kills the process at import time if the boundary is violated.
CC cannot write `from governance.signal_worker import ...` inside core_logic
because the import itself will never complete.

Install once at bootstrap, before any application imports:
    from governance.kernel.import_guard import install_import_guard
    install_import_guard()
"""

import os
import sys


_FORBIDDEN = {
    "core_logic": ("governance", "api"),
    "governance": ("api",),
}


class _BoundaryEnforcer:
    """
    sys.meta_path finder that enforces import direction.
    Uses find_spec (Python 3.4+ protocol). Walks the call stack
    past importlib internals to find the real caller module.
    """

    def find_spec(self, fullname, path, target=None):
        target_root = fullname.split(".")[0]

        # Quick exit: if nobody forbids imports of this root, skip
        forbidden_by_anyone = any(
            target_root in targets for targets in _FORBIDDEN.values()
        )
        if not forbidden_by_anyone:
            return None

        # Walk the call stack past importlib internals
        frame = sys._getframe(1)
        caller_module = None
        for _ in range(20):
            if frame is None:
                break
            name = frame.f_globals.get("__name__", "")
            if not name.startswith(("_frozen_importlib", "importlib")):
                caller_module = name
                break
            frame = frame.f_back

        if not caller_module:
            return None

        caller_root = caller_module.split(".")[0]
        forbidden_targets = _FORBIDDEN.get(caller_root)

        if forbidden_targets and target_root in forbidden_targets:
            print(
                f"\n{'='*72}\n"
                f"FATAL IMPORT BOUNDARY VIOLATION\n"
                f"{'='*72}\n"
                f"Caller : {caller_module}\n"
                f"Target : {fullname}\n"
                f"Rule   : {caller_root}/ cannot import from {target_root}/\n"
                f"\n"
                f"Dependency arrows flow DOWN only:\n"
                f"  core_logic/ → governance/ → api/\n"
                f"\n"
                f"core_logic is pure math. It receives data, returns results.\n"
                f"It does not know governance exists.\n"
                f"{'='*72}\n",
                file=sys.stderr,
            )
            sys.stderr.flush()
            os._exit(78)

        return None


_installed = False


def install_import_guard() -> None:
    """
    Install the boundary enforcer on sys.meta_path.
    Idempotent — safe to call multiple times, installs only once.
    Must be called before any application imports.
    """
    global _installed
    if _installed:
        return
    sys.meta_path.insert(0, _BoundaryEnforcer())
    _installed = True
