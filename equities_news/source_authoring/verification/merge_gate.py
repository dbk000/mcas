"""merge_gate — orchestrator that blocks a merge on any verification failure.

Runs:
  1. consistency_checker (schema + uniqueness + coverage + orphan refs).
  2. format_validator --dry-run (HTTP HEAD; dry-run unless --write-verified).
  3. Optional: content audit hook (delegated — not implemented in this script).

Exit codes:
  0  — all gates pass.
  1  — one or more gates failed.

Usage:
    python3 merge_gate.py                 # strict gate
    python3 merge_gate.py --skip-format   # skip HTTP check (offline run)
    python3 merge_gate.py --write-verified # allow format validator to update lineage

Phase A exit gate: run this on the full registry before tagging phase-a-locked.
Phase B exit gate: run this after all 11 sector agents report done.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

VERIFY_DIR = Path(__file__).resolve().parent
PY = sys.executable


def run(cmd: list[str]) -> int:
    print(f"\n>>> {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    print(f">>> exit={rc}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-format", action="store_true", help="skip format validator (no network)")
    ap.add_argument("--write-verified", action="store_true",
                    help="let format_validator update lineage.verified_at on live URLs")
    ap.add_argument("--only", help="limit format validator to a single YAML filename")
    ap.add_argument("--strict-consistency", action="store_true")
    args = ap.parse_args()

    failures: list[str] = []

    rc = run([PY, str(VERIFY_DIR / "consistency_checker.py")] + (["--strict"] if args.strict_consistency else []))
    if rc != 0:
        failures.append("consistency_checker")

    if not args.skip_format:
        fmt_cmd = [PY, str(VERIFY_DIR / "format_validator.py")]
        if not args.write_verified:
            fmt_cmd.append("--dry-run")
        if args.only:
            fmt_cmd.extend(["--only", args.only])
        rc = run(fmt_cmd)
        if rc != 0:
            failures.append("format_validator")

    print("\n--- merge gate summary ---")
    if failures:
        print(f"  FAILED: {', '.join(failures)}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
