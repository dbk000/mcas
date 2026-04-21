"""Phase A.3 — geo-tier source authoring harness.

Per-country YAML under registry/news_sources/geo/{cc}.yaml. Same harness
pattern as universal_author: accept proposals, validate, merge, stamp lineage.

Extra rule: every source_id must match geo.{cc}.* where {cc} is the filename
(lowercased alpha-2). This guards against accidentally dropping a JP source
into de.yaml etc.

Invoke:
    python3 geo_author.py --validate-only
    python3 geo_author.py --validate-only --cc jp
    python3 geo_author.py --propose path.yaml --cc jp
    python3 geo_author.py --coverage-report
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import (
    NEWS_SOURCES_DIR,
    REGISTRY_DIR,
    NamespaceRules,
    dump_yaml,
    entry_fingerprint,
    load_event_type_slugs,
    load_yaml,
    utc_now_iso,
    validate_entry,
)

GEO_DIR = NEWS_SOURCES_DIR / "geo"
DEFAULT_AUTHOR = "phase_a_opus"


def _yaml_path(cc: str) -> Path:
    return GEO_DIR / f"{cc.lower()}.yaml"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = load_yaml(path)
    if data is None:
        return []
    if not isinstance(data, list):
        raise SystemExit(f"{path}: top-level must be a list")
    return list(data)


def _validate_file(cc: str, entries: list[dict]) -> list[str]:
    rules = NamespaceRules.load()
    ets = load_event_type_slugs()
    expected_prefix = f"geo.{cc}."
    msgs: list[str] = []
    seen: set[str] = set()
    for i, e in enumerate(entries):
        where = f"geo/{cc}.yaml[{i}]"
        errs = validate_entry(e, rules, ets, where)
        for err in errs:
            msgs.append(f"{where}: {err.message}")
        sid = e.get("source_id")
        if isinstance(sid, str):
            if not sid.startswith(expected_prefix):
                msgs.append(f"{where}: {sid!r} not under {expected_prefix}*")
            if sid in seen:
                msgs.append(f"{where}: duplicate source_id {sid!r}")
            seen.add(sid)
        juris = e.get("jurisdiction")
        if isinstance(juris, str) and juris.upper() != cc.upper():
            msgs.append(f"{where}: jurisdiction={juris!r} does not match file code {cc.upper()}")
    return msgs


def validate(cc_filter: str | None) -> int:
    countries_doc = load_yaml(REGISTRY_DIR / "countries_in_universe.yaml")
    universe_codes = {code.lower() for code in countries_doc["countries"].keys()}
    issues: list[str] = []
    checked = 0
    for p in sorted(GEO_DIR.glob("*.yaml")):
        cc = p.stem
        if cc_filter and cc != cc_filter.lower():
            continue
        if cc not in universe_codes:
            issues.append(f"geo/{cc}.yaml: country code not in universe")
        entries = _load(p)
        issues.extend(_validate_file(cc, entries))
        checked += 1
    if issues:
        print("VALIDATION FAILED:", file=sys.stderr)
        for m in issues:
            print(f"  {m}", file=sys.stderr)
        return 1
    print(f"validated {checked} geo file(s)")
    return 0


def propose(cc: str, proposal_path: Path, author: str) -> int:
    cc = cc.lower()
    target = _yaml_path(cc)
    current = {e["source_id"]: e for e in _load(target) if isinstance(e, dict) and "source_id" in e}
    proposed = _load(proposal_path)
    now = utc_now_iso()

    added: list[str] = []
    updated: list[str] = []
    for entry in proposed:
        sid = entry.get("source_id")
        if not isinstance(sid, str):
            raise SystemExit(f"proposal entry missing source_id: {entry!r}")
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
    msgs = _validate_file(cc, merged)
    if msgs:
        print("VALIDATION FAILED:", file=sys.stderr)
        for m in msgs:
            print(f"  {m}", file=sys.stderr)
        return 1
    dump_yaml(target, merged)
    print(f"wrote {target}")
    print(f"  added:   {len(added)}")
    for s in added:
        print(f"    + {s}")
    print(f"  updated: {len(updated)}")
    for s in updated:
        print(f"    ~ {s}")
    return 0


def coverage_report() -> int:
    countries_doc = load_yaml(REGISTRY_DIR / "countries_in_universe.yaml")
    missing: list[tuple[str, int, str]] = []
    present: list[tuple[str, int, int]] = []
    for code, rec in countries_doc["countries"].items():
        path = _yaml_path(code.lower())
        if not path.exists():
            missing.append((code, rec["symbol_count"], rec["country_name"]))
        else:
            entries = _load(path)
            if len(entries) == 0:
                missing.append((code, rec["symbol_count"], rec["country_name"]))
            else:
                present.append((code, rec["symbol_count"], len(entries)))
    print("--- present ---")
    for code, n_sym, n_src in sorted(present, key=lambda r: -r[1]):
        print(f"  {code}  symbols={n_sym:>5}  sources={n_src}")
    print("--- missing ---")
    for code, n_sym, name in sorted(missing, key=lambda r: -r[1]):
        print(f"  {code}  symbols={n_sym:>5}  {name}")
    return 0 if not missing else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--propose", type=Path)
    ap.add_argument("--cc", help="ISO 3166 alpha-2 country code")
    ap.add_argument("--coverage-report", action="store_true")
    ap.add_argument("--author", default=DEFAULT_AUTHOR)
    args = ap.parse_args()

    if args.coverage_report:
        return coverage_report()
    if args.validate_only:
        return validate(args.cc)
    if args.propose:
        if not args.cc:
            ap.error("--propose requires --cc")
        return propose(args.cc, args.propose, args.author)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
