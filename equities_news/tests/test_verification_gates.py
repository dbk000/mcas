"""Test verification/consistency_checker failure modes + merge_gate orchestration."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "source_authoring" / "verification"


def test_consistency_checker_passes_current_registry():
    rc = subprocess.call([sys.executable, str(VERIFY / "consistency_checker.py")])
    assert rc == 0


def test_consistency_checker_detects_injected_duplicate(tmp_path, monkeypatch):
    """Copy one entry, inject a duplicate source_id into a temp file under registry/news_sources/geo,
    expect consistency_checker to fail."""
    from _lib import NEWS_SOURCES_DIR, dump_yaml, load_yaml

    # Back up target and inject duplicate.
    target = NEWS_SOURCES_DIR / "geo" / "us.yaml"
    original = load_yaml(target)
    # Take the first entry and append a clone with identical source_id (duplicate within same file).
    clone = dict(original[0])
    backup = list(original)
    try:
        poisoned = list(original) + [clone]
        dump_yaml(target, poisoned)
        rc = subprocess.call([sys.executable, str(VERIFY / "consistency_checker.py")])
        assert rc != 0, "consistency_checker should fail on duplicate source_id"
    finally:
        dump_yaml(target, backup)


def test_consistency_checker_detects_invalid_event_type(tmp_path):
    from _lib import NEWS_SOURCES_DIR, dump_yaml, load_yaml
    target = NEWS_SOURCES_DIR / "geo" / "us.yaml"
    backup = load_yaml(target)
    try:
        poisoned = [dict(e) for e in backup]
        poisoned[0] = dict(poisoned[0])
        poisoned[0]["event_types_covered"] = ["fake_event_type_xyz"]
        dump_yaml(target, poisoned)
        rc = subprocess.call([sys.executable, str(VERIFY / "consistency_checker.py")])
        assert rc != 0, "consistency_checker should fail on unknown event_type"
    finally:
        dump_yaml(target, backup)


def test_merge_gate_skip_format_passes():
    rc = subprocess.call(
        [sys.executable, str(VERIFY / "merge_gate.py"), "--skip-format"]
    )
    assert rc == 0
