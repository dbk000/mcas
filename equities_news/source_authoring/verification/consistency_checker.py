"""consistency_checker — cross-namespace collision + orphan ref detection.

Checks across the full registry:

  1. Schema validity of every entry (delegates to validate_entry).
  2. No duplicate source_ids across files (registry-wide uniqueness).
  3. No URL appears under two different source_ids (independent rediscovery).
  4. No source references an undefined event_type (from event_types.yaml).
  5. No geo.yaml file for a country outside countries_in_universe.yaml.
  6. No sub_industry.* entry pointing at a nonexistent parent sector.
  7. No namespace slug violating namespace_rules.yaml depth/prefix rules.

Exits non-zero on any failure.

Usage:
    python3 consistency_checker.py            # scan everything
    python3 consistency_checker.py --strict   # also fail on warnings
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import (
    NEWS_SOURCES_DIR,
    REGISTRY_DIR,
    NamespaceRules,
    classify_namespace,
    load_all_entries,
    load_event_type_slugs,
    load_yaml,
    validate_entry,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="fail on warnings too")
    args = ap.parse_args()

    rules = NamespaceRules.load()
    ets = load_event_type_slugs()
    countries_doc = load_yaml(REGISTRY_DIR / "countries_in_universe.yaml")
    universe_codes = {cc.lower() for cc in countries_doc["countries"].keys()}

    entries = load_all_entries()
    errors: list[str] = []
    warnings: list[str] = []

    # Per-entry validation + uniqueness.
    seen_ids: dict[str, Path] = {}
    url_to_id: dict[str, str] = {}
    for path, entry in entries:
        where = f"{path.relative_to(NEWS_SOURCES_DIR)}"
        errs = validate_entry(entry, rules, ets, where)
        for e in errs:
            errors.append(f"{where}: {e.message}")
        sid = entry.get("source_id")
        if isinstance(sid, str):
            if sid in seen_ids:
                errors.append(f"duplicate source_id {sid!r}: {seen_ids[sid].name} and {path.name}")
            else:
                seen_ids[sid] = path
        url = entry.get("url")
        if isinstance(url, str) and isinstance(sid, str):
            canonical = url.rstrip("/").lower()
            existing = url_to_id.get(canonical)
            if existing and existing != sid:
                warnings.append(f"url {url!r} appears under both {existing!r} and {sid!r}")
            url_to_id[canonical] = sid

    # Namespace routing sanity.
    sector_slugs_present: set[str] = set()
    for path, entry in entries:
        sid = entry.get("source_id")
        if not isinstance(sid, str):
            continue
        try:
            ns_name, parts = classify_namespace(sid, rules)
        except ValueError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        # geo file location must match country.
        if ns_name == "geo":
            cc = parts[1]
            if path.parent.name == "geo":
                expected_file = f"{cc}.yaml"
                if path.name != expected_file:
                    errors.append(
                        f"{path.name}: contains {sid!r} but filename implies {path.stem!r}"
                    )
                if cc not in universe_codes:
                    warnings.append(f"{path.name}: country {cc!r} not in countries_in_universe.yaml")
        if ns_name == "sector":
            sector_slugs_present.add(parts[1])

    # Sub-industry parent-sector existence.
    for path, entry in entries:
        sid = entry.get("source_id")
        if not isinstance(sid, str):
            continue
        try:
            ns_name, parts = classify_namespace(sid, rules)
        except ValueError:
            continue
        if ns_name == "sub_industry":
            parent_sector = parts[1]
            sector_file = NEWS_SOURCES_DIR / "sectors" / f"{parent_sector}.yaml"
            if not sector_file.exists():
                errors.append(
                    f"{path.name}: {sid!r} references sector {parent_sector!r} "
                    f"but {sector_file} does not exist"
                )

    # Coverage check — every country in universe has a non-empty geo file.
    for cc in universe_codes:
        p = NEWS_SOURCES_DIR / "geo" / f"{cc}.yaml"
        if not p.exists() or not (load_yaml(p) or []):
            warnings.append(f"coverage: geo/{cc}.yaml missing or empty")

    print(f"--- consistency check ---")
    print(f"  files: {len(set(p for p,_ in entries))}")
    print(f"  entries: {len(entries)}")
    print(f"  errors: {len(errors)}")
    print(f"  warnings: {len(warnings)}")
    for e in errors:
        print(f"  ERROR   {e}")
    for w in warnings:
        print(f"  WARN    {w}")

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
