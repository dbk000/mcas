"""Phase A.2 — universal source authoring loop.

This script is the *harness*, not the author. An Opus session (or a
Darren-driven maintenance run) proposes new entries or edits existing ones,
and this harness:

  1. Loads current universal.yaml.
  2. Accepts a proposed set of additions/edits (passed in via import or stdin).
  3. Runs schema validation against namespace_rules.yaml + event_types.yaml.
  4. On clean validation, merges into universal.yaml and updates each entry's
     lineage.authored_at / authored_by.
  5. Emits a diff summary.

Format validation (HTTP HEAD) is deliberately NOT here — it lives in
verification/format_validator.py so it can run as a separate step with its own
network governance.

Invoke:
    python3 universal_author.py --validate-only        # check current file
    python3 universal_author.py --propose path.yaml    # merge proposed entries
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import (
    NEWS_SOURCES_DIR,
    NamespaceRules,
    dump_yaml,
    entry_fingerprint,
    load_event_type_slugs,
    load_yaml,
    utc_now_iso,
    validate_entry,
)

UNIVERSAL_YAML = NEWS_SOURCES_DIR / "universal.yaml"
DEFAULT_AUTHOR = "phase_a_opus"


def _load(path: Path) -> list[dict]:
    data = load_yaml(path)
    if data is None:
        return []
    if not isinstance(data, list):
        raise SystemExit(f"{path}: top-level must be a list of source entries")
    return list(data)


def validate(entries: list[dict]) -> int:
    rules = NamespaceRules.load()
    ets = load_event_type_slugs()
    errs = []
    seen_ids: set[str] = set()
    for i, e in enumerate(entries):
        where = f"universal.yaml[{i}]"
        errs.extend(validate_entry(e, rules, ets, where))
        sid = e.get("source_id")
        if isinstance(sid, str):
            if sid in seen_ids:
                errs.append(("", where, f"duplicate source_id {sid!r}"))
            seen_ids.add(sid)
            if not sid.startswith("universal."):
                errs.append(("", where, f"{sid!r} not in universal.* namespace"))
    if errs:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errs:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"validated {len(entries)} entries in universal.yaml")
    return 0


def propose(proposal_path: Path, author: str) -> int:
    current = {e["source_id"]: e for e in _load(UNIVERSAL_YAML) if isinstance(e, dict) and "source_id" in e}
    proposed = _load(proposal_path)
    now = utc_now_iso()

    added: list[str] = []
    updated: list[str] = []

    for entry in proposed:
        sid = entry.get("source_id")
        if not isinstance(sid, str):
            raise SystemExit(f"proposal has entry without source_id: {entry!r}")
        lineage = dict(entry.get("lineage") or {})
        lineage.setdefault("authored_by", author)
        lineage.setdefault("authored_at", now)
        entry["lineage"] = lineage

        if sid in current:
            if entry_fingerprint(current[sid]) != entry_fingerprint(entry):
                updated.append(sid)
        else:
            added.append(sid)
        current[sid] = entry

    merged = sorted(current.values(), key=lambda e: e["source_id"])
    rc = validate(merged)
    if rc != 0:
        return rc
    dump_yaml(UNIVERSAL_YAML, merged)
    print(f"wrote {UNIVERSAL_YAML}")
    print(f"  added:   {len(added)}")
    for s in added:
        print(f"    + {s}")
    print(f"  updated: {len(updated)}")
    for s in updated:
        print(f"    ~ {s}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate-only", action="store_true", help="validate existing universal.yaml")
    ap.add_argument("--propose", type=Path, help="merge proposed entries from this YAML file")
    ap.add_argument("--author", default=DEFAULT_AUTHOR, help="lineage.authored_by")
    args = ap.parse_args()

    if args.validate_only:
        return validate(_load(UNIVERSAL_YAML))
    if args.propose:
        return propose(args.propose, args.author)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
