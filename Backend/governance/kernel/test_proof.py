"""
Proof suite for MCAS Governance.

Tests that:
    1. Happy path: registries populate, freeze, seal, lineage stamps correctly
    2. Missing key after freeze → process death (sys.exit)
    3. Post-freeze _register() → AttributeError (method is tombstoned)
    4. Post-freeze setattr → process death
    5. Double-freeze → process death
    6. Empty registry freeze → process death
    7. Foreign lineage → process death (peer shunning)
    8. Missing lineage → process death (peer shunning)
    9. Seal determinism — same content → same seal across runs
    10. Seal sensitivity — different content → different seal
"""

import subprocess
import sys
import textwrap
from pathlib import Path

PASS = 0
FAIL = 0


def run_snippet(code: str, expect_exit: bool = False, expected_exit_code: int = 78) -> dict:
    """Run a Python snippet in a subprocess and capture results."""
    preamble = f"import sys\nsys.path.insert(0, '{Path(__file__).parent.parent.parent}')\n"
    full_code = preamble + textwrap.dedent(code)
    result = subprocess.run(
        [sys.executable, '-c', full_code],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return {
        'returncode': result.returncode,
        'stdout': result.stdout,
        'stderr': result.stderr,
    }


def assert_test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} — {detail}")


def test_happy_path():
    """Registry populates, freezes, and provides values via attribute access."""
    print("\n[TEST] Happy path — populate, freeze, access")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg = FrozenRegistry("test")
        reg._register("alpha", {"value": 1})
        reg._register("beta", {"value": 2})
        seal = reg._freeze()

        # Access works
        assert reg.alpha == {"value": 1}, f"Got {reg.alpha}"
        assert reg.beta == {"value": 2}, f"Got {reg.beta}"
        assert isinstance(seal, str) and len(seal) == 64, f"Bad seal: {seal}"
        print(f"SEAL:{seal}")
        print("OK")
    """)
    assert_test("exits cleanly", r['returncode'] == 0, f"rc={r['returncode']}")
    assert_test("produces output", "OK" in r['stdout'], r['stdout'][:200])


def test_missing_key_kills_process():
    """Accessing an unregistered key after freeze → sys.exit(78)."""
    print("\n[TEST] Missing key after freeze → process death")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg = FrozenRegistry("test")
        reg._register("alpha", 1)
        reg._freeze()
        _ = reg.nonexistent  # This should kill the process
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")
    assert_test("did not continue", "SHOULD NOT REACH HERE" not in r['stdout'])
    assert_test("fatal message", "FATAL GOVERNANCE VIOLATION" in r['stderr'], r['stderr'][:200])


def test_getattr_default_does_not_save():
    """getattr(registry, 'missing', default) — the default never fires."""
    print("\n[TEST] getattr with default → process death (default bypassed)")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg = FrozenRegistry("test")
        reg._register("alpha", 1)
        reg._freeze()
        fallback = getattr(reg, "nonexistent", "FALLBACK")
        print(f"GOT:{fallback}")  # Should never reach here
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")
    assert_test("fallback never used", "GOT:FALLBACK" not in r['stdout'])


def test_post_freeze_register_fails():
    """_register() after freeze → tombstoned, not callable."""
    print("\n[TEST] Post-freeze _register() → tombstone")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg = FrozenRegistry("test")
        reg._register("alpha", 1)
        reg._freeze()
        reg._register("beta", 2)  # This calls the tombstone
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")
    assert_test("did not continue", "SHOULD NOT REACH HERE" not in r['stdout'])


def test_post_freeze_setattr_fails():
    """Direct attribute assignment after freeze → process death."""
    print("\n[TEST] Post-freeze setattr → process death")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg = FrozenRegistry("test")
        reg._register("alpha", 1)
        reg._freeze()
        reg.gamma = 3  # This should kill the process
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")


def test_double_freeze_fails():
    """Freezing an already-frozen registry → process death."""
    print("\n[TEST] Double freeze → process death")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg = FrozenRegistry("test")
        reg._register("alpha", 1)
        reg._freeze()
        reg._freeze()  # Calls tombstone
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")


def test_empty_registry_freeze_fails():
    """Freezing an empty registry → process death."""
    print("\n[TEST] Empty registry freeze → process death")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg = FrozenRegistry("test")
        reg._freeze()  # No entries registered
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")


def test_seal_determinism():
    """Same content → same seal across independent constructions."""
    print("\n[TEST] Seal determinism")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        def build():
            reg = FrozenRegistry("test")
            reg._register("alpha", {"x": 1, "y": [2, 3]})
            reg._register("beta", "hello")
            return reg._freeze()

        seal1 = build()
        seal2 = build()
        assert seal1 == seal2, f"{seal1} != {seal2}"
        print(f"DETERMINISTIC:{seal1}")
    """)
    assert_test("seals match", r['returncode'] == 0 and "DETERMINISTIC:" in r['stdout'],
                r['stderr'][:200] if r['returncode'] != 0 else "")


def test_seal_sensitivity():
    """Different content → different seal."""
    print("\n[TEST] Seal sensitivity")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg1 = FrozenRegistry("test")
        reg1._register("alpha", {"x": 1})
        seal1 = reg1._freeze()

        reg2 = FrozenRegistry("test")
        reg2._register("alpha", {"x": 2})  # Different value
        seal2 = reg2._freeze()

        assert seal1 != seal2, f"Seals should differ: {seal1}"
        print("DIFFERENT")
    """)
    assert_test("seals differ", r['returncode'] == 0 and "DIFFERENT" in r['stdout'],
                r['stderr'][:200] if r['returncode'] != 0 else "")


def test_runtime_graph_seal():
    """Multiple registries → single graph seal via RuntimeGraphSeal."""
    print("\n[TEST] Runtime graph seal aggregation")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry
        from governance.kernel.runtime_graph import RuntimeGraphSeal

        r1 = FrozenRegistry("config")
        r1._register("db_url", "postgres://...")
        r1._freeze()

        r2 = FrozenRegistry("strategy")
        r2._register("D1", {"setup": "dormancy"})
        r2._freeze()

        graph = RuntimeGraphSeal()
        graph.include(r1)
        graph.include(r2)
        lineage = graph.seal()

        assert len(lineage.runtime_graph_seal) == 64
        assert len(lineage.registry_seals) == 2
        assert lineage.process_id
        print(f"GRAPH_SEAL:{lineage.runtime_graph_seal[:12]}")
        print(f"PROCESS_ID:{lineage.process_id}")
        print("OK")
    """)
    assert_test("graph seals correctly", r['returncode'] == 0 and "OK" in r['stdout'],
                r['stderr'][:200] if r['returncode'] != 0 else "")


def test_lineage_enforcement_rejects_foreign():
    """LineageEnforcer rejects payload with wrong seal → process death."""
    print("\n[TEST] Peer shunning — foreign lineage → process death")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry
        from governance.kernel.runtime_graph import RuntimeGraphSeal
        from governance.kernel.bootstrap import LineageEnforcer

        r1 = FrozenRegistry("config")
        r1._register("key", "val")
        r1._freeze()

        graph = RuntimeGraphSeal()
        graph.include(r1)
        lineage = graph.seal()

        class Worker(LineageEnforcer):
            pass

        worker = Worker(lineage)

        # Foreign seal
        worker.enforce_lineage({"runtime_graph_seal": "0000" * 16})
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")
    assert_test("lineage violation reported", "FATAL LINEAGE VIOLATION" in r['stderr'])


def test_lineage_enforcement_rejects_missing():
    """LineageEnforcer rejects payload with no seal → process death."""
    print("\n[TEST] Peer shunning — missing lineage → process death")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry
        from governance.kernel.runtime_graph import RuntimeGraphSeal
        from governance.kernel.bootstrap import LineageEnforcer

        r1 = FrozenRegistry("config")
        r1._register("key", "val")
        r1._freeze()

        graph = RuntimeGraphSeal()
        graph.include(r1)
        lineage = graph.seal()

        class Worker(LineageEnforcer):
            pass

        worker = Worker(lineage)
        worker.enforce_lineage({"some_data": 123})  # No seal
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")


def test_lineage_enforcement_accepts_valid():
    """LineageEnforcer accepts payload with matching seal."""
    print("\n[TEST] Peer acceptance — valid lineage passes")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry
        from governance.kernel.runtime_graph import RuntimeGraphSeal
        from governance.kernel.bootstrap import LineageEnforcer

        r1 = FrozenRegistry("config")
        r1._register("key", "val")
        r1._freeze()

        graph = RuntimeGraphSeal()
        graph.include(r1)
        lineage = graph.seal()

        class Worker(LineageEnforcer):
            pass

        worker = Worker(lineage)
        payload = {"data": 42}
        payload = worker.stamp_lineage(payload)
        worker.enforce_lineage(payload)  # Should pass
        print("ACCEPTED")
    """)
    assert_test("payload accepted", r['returncode'] == 0 and "ACCEPTED" in r['stdout'],
                r['stderr'][:200] if r['returncode'] != 0 else "")


def test_delete_attr_blocked():
    """Deleting an attribute → process death."""
    print("\n[TEST] Attribute deletion → process death")
    r = run_snippet("""
        from governance.kernel.frozen_registry import FrozenRegistry

        reg = FrozenRegistry("test")
        reg._register("alpha", 1)
        reg._freeze()
        del reg.alpha
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")


def test_import_guard_blocks_violation():
    """core_logic importing from governance → process death."""
    print("\n[TEST] Import guard — core_logic → governance = process death")
    # Create a temporary directory with fake packages to prove the boundary
    r = run_snippet("""
        import os, tempfile, textwrap
        tmpdir = tempfile.mkdtemp()

        # Create governance/ package
        os.makedirs(os.path.join(tmpdir, "governance"))
        with open(os.path.join(tmpdir, "governance", "__init__.py"), "w") as f:
            f.write("SECRET = 42\\n")

        # Create core_logic/ package that tries to import governance
        os.makedirs(os.path.join(tmpdir, "core_logic"))
        with open(os.path.join(tmpdir, "core_logic", "__init__.py"), "w") as f:
            f.write("")
        with open(os.path.join(tmpdir, "core_logic", "math_module.py"), "w") as f:
            f.write("from governance import SECRET\\n")

        # Import guard BEFORE adding tmpdir (which shadows governance/)
        from governance.kernel.import_guard import install_import_guard
        install_import_guard()

        import sys
        sys.path.insert(0, tmpdir)

        # This import should kill the process
        from core_logic import math_module
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")
    assert_test("boundary violation reported", "FATAL IMPORT BOUNDARY VIOLATION" in r['stderr'],
                r['stderr'][:300])


def test_import_guard_allows_legal():
    """core_logic importing stdlib or itself → allowed."""
    print("\n[TEST] Import guard — legal imports pass through")
    r = run_snippet("""
        import os, tempfile
        tmpdir = tempfile.mkdtemp()

        # Create core_logic/ package that imports only stdlib
        os.makedirs(os.path.join(tmpdir, "core_logic"))
        with open(os.path.join(tmpdir, "core_logic", "__init__.py"), "w") as f:
            f.write("")
        with open(os.path.join(tmpdir, "core_logic", "safe_module.py"), "w") as f:
            f.write("import math\\nresult = math.sqrt(16)\\n")

        # Import guard before adding tmpdir
        from governance.kernel.import_guard import install_import_guard
        install_import_guard()

        import sys
        sys.path.insert(0, tmpdir)

        from core_logic import safe_module
        assert safe_module.result == 4.0
        print("LEGAL_IMPORT_OK")
    """)
    assert_test("legal import passes", r['returncode'] == 0 and "LEGAL_IMPORT_OK" in r['stdout'],
                r['stderr'][:200] if r['returncode'] != 0 else "")


def test_import_guard_blocks_governance_to_api():
    """governance importing from api → process death."""
    print("\n[TEST] Import guard — governance → api = process death")
    r = run_snippet("""
        import os, tempfile
        tmpdir = tempfile.mkdtemp()

        from governance.kernel.import_guard import install_import_guard
        install_import_guard()

        os.makedirs(os.path.join(tmpdir, "api"))
        with open(os.path.join(tmpdir, "api", "__init__.py"), "w") as f:
            f.write("ROUTE = 42\\n")

        import sys
        sys.path.insert(0, tmpdir)

        globs = {"__name__": "governance.bad_worker", "__builtins__": __builtins__}
        exec("from api import ROUTE", globs)
        print("SHOULD NOT REACH HERE")
    """, expect_exit=True)
    assert_test("process died", r['returncode'] == 78, f"rc={r['returncode']}")


if __name__ == '__main__':
    print("=" * 72)
    print("MCAS GOVERNANCE — PROOF SUITE")
    print("=" * 72)

    test_happy_path()
    test_missing_key_kills_process()
    test_getattr_default_does_not_save()
    test_post_freeze_register_fails()
    test_post_freeze_setattr_fails()
    test_double_freeze_fails()
    test_empty_registry_freeze_fails()
    test_seal_determinism()
    test_seal_sensitivity()
    test_runtime_graph_seal()
    test_lineage_enforcement_rejects_foreign()
    test_lineage_enforcement_rejects_missing()
    test_lineage_enforcement_accepts_valid()
    test_delete_attr_blocked()
    test_import_guard_blocks_violation()
    test_import_guard_allows_legal()
    test_import_guard_blocks_governance_to_api()

    print("\n" + "=" * 72)
    print(f"RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
    print("=" * 72)
    sys.exit(1 if FAIL > 0 else 0)
