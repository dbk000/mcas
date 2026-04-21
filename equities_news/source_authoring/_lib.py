"""Shared helpers for Phase A + Phase B authoring + verification scripts.

Everything that touches the registry goes through this module so we have a single
schema-validation path, a single YAML loader, and a single namespace rules parser.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_DIR = REPO_ROOT / "registry"
NEWS_SOURCES_DIR = REGISTRY_DIR / "news_sources"
GROUND_TRUTH_DIR = REPO_ROOT / "ground_truth"


# --------------------------------------------------------------------------- #
# YAML IO                                                                     #
# --------------------------------------------------------------------------- #

def load_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def dump_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            data,
            fh,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=120,
        )


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Namespace rules                                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NamespaceRules:
    version: int
    slug_component_regex: re.Pattern
    max_total_id_length: int
    country_code_regex: re.Pattern
    sector_slugs: frozenset[str]
    namespaces: dict[str, dict]
    required_fields: tuple[str, ...]
    lineage_required_fields: tuple[str, ...]
    enums: dict[str, frozenset[str]]
    edge_weight_bounds: tuple[float, float]
    lead_time_bounds: tuple[int, int]
    resolver_tier_weights: dict[str, int]

    @classmethod
    def load(cls) -> "NamespaceRules":
        raw = load_yaml(REGISTRY_DIR / "namespace_rules.yaml")
        return cls(
            version=raw["version"],
            slug_component_regex=re.compile(raw["slug_component_regex"]),
            max_total_id_length=int(raw["max_total_id_length"]),
            country_code_regex=re.compile(raw["country_code_regex"]),
            sector_slugs=frozenset(raw["sector_slugs"]),
            namespaces=raw["namespaces"],
            required_fields=tuple(raw["required_fields"]),
            lineage_required_fields=tuple(raw["lineage_required_fields"]),
            enums={k: frozenset(v) for k, v in raw["enums"].items()},
            edge_weight_bounds=(
                float(raw["edge_weight_prior"]["min"]),
                float(raw["edge_weight_prior"]["max"]),
            ),
            lead_time_bounds=(
                int(raw["typical_lead_time_vs_english_minutes"]["min"]),
                int(raw["typical_lead_time_vs_english_minutes"]["max"]),
            ),
            resolver_tier_weights=raw["resolver_tier_weights"],
        )


def classify_namespace(source_id: str, rules: NamespaceRules) -> tuple[str, list[str]]:
    """Return (namespace_name, parts). Raises ValueError on unknown namespace."""
    parts = source_id.split(".")
    if not parts:
        raise ValueError(f"empty source_id")
    prefix = parts[0]
    for ns_name, ns_cfg in rules.namespaces.items():
        if ns_cfg["prefix"] == prefix:
            expected_depth = int(ns_cfg["depth"])
            if len(parts) != expected_depth:
                raise ValueError(
                    f"{source_id!r}: namespace {ns_name!r} requires depth "
                    f"{expected_depth}, got {len(parts)}"
                )
            return ns_name, parts
    raise ValueError(f"{source_id!r}: prefix {prefix!r} not in any known namespace")


# --------------------------------------------------------------------------- #
# Source entry validation                                                      #
# --------------------------------------------------------------------------- #

@dataclass
class ValidationError:
    source_id: str | None
    where: str
    message: str


def validate_entry(
    entry: dict,
    rules: NamespaceRules,
    event_type_slugs: frozenset[str],
    where: str,
) -> list[ValidationError]:
    errs: list[ValidationError] = []
    sid = entry.get("source_id")

    def err(msg: str) -> None:
        errs.append(ValidationError(source_id=sid, where=where, message=msg))

    # Required fields.
    for f in rules.required_fields:
        if f not in entry:
            err(f"missing required field {f!r}")
    if "lineage" in entry:
        for f in rules.lineage_required_fields:
            if f not in entry["lineage"]:
                err(f"lineage missing required field {f!r}")

    if not isinstance(sid, str):
        err("source_id must be a string")
        return errs

    # Length.
    if len(sid) > rules.max_total_id_length:
        err(f"source_id length {len(sid)} exceeds {rules.max_total_id_length}")

    # Namespace + slug components.
    try:
        ns_name, parts = classify_namespace(sid, rules)
    except ValueError as exc:
        err(str(exc))
        return errs

    # Each slug component (skip country-code positions and sector-slug positions
    # which have their own regex / allowlist) must match slug_component_regex.
    for i, part in enumerate(parts):
        if ns_name == "geo" and i == 1:
            if not rules.country_code_regex.match(part):
                err(f"country code {part!r} fails regex {rules.country_code_regex.pattern}")
            continue
        if ns_name in ("sector", "sub_industry") and i == 1:
            if part not in rules.sector_slugs:
                err(f"sector slug {part!r} not in allowed set")
            continue
        if not rules.slug_component_regex.match(part):
            err(f"slug component {part!r} fails regex {rules.slug_component_regex.pattern}")

    # Enum constraints.
    for key in ("adapter_type", "cost_tier", "update_cadence"):
        val = entry.get(key)
        if val is not None and val not in rules.enums[key]:
            err(f"{key}={val!r} not in enum {sorted(rules.enums[key])}")

    # edge_weight_prior bounds.
    ewp = entry.get("edge_weight_prior")
    if isinstance(ewp, (int, float)):
        lo, hi = rules.edge_weight_bounds
        if not (lo <= ewp <= hi):
            err(f"edge_weight_prior={ewp} outside [{lo}, {hi}]")
    elif "edge_weight_prior" in entry:
        err("edge_weight_prior must be numeric")

    # lead time bounds.
    lt = entry.get("typical_lead_time_vs_english_minutes")
    if isinstance(lt, (int, float)):
        lo, hi = rules.lead_time_bounds
        if not (lo <= lt <= hi):
            err(f"typical_lead_time_vs_english_minutes={lt} outside [{lo}, {hi}]")
    elif "typical_lead_time_vs_english_minutes" in entry:
        err("typical_lead_time_vs_english_minutes must be numeric")

    # event_types_covered.
    etc = entry.get("event_types_covered")
    if isinstance(etc, list):
        for et in etc:
            if et not in event_type_slugs:
                err(f"event_type {et!r} not in event_types.yaml")
    elif "event_types_covered" in entry:
        err("event_types_covered must be a list")

    # Jurisdiction must be ISO alpha-2 uppercase OR the sentinel 'XX' (supranational).
    juris = entry.get("jurisdiction")
    if isinstance(juris, str) and juris != "XX" and not re.match(r"^[A-Z]{2}$", juris):
        err(f"jurisdiction={juris!r} not ISO 3166-1 alpha-2 uppercase or 'XX'")

    return errs


def load_event_type_slugs() -> frozenset[str]:
    raw = load_yaml(REGISTRY_DIR / "event_types.yaml")
    return frozenset(e["slug"] for e in raw["event_types"])


# --------------------------------------------------------------------------- #
# Registry scan                                                                #
# --------------------------------------------------------------------------- #

def iter_registry_files() -> Iterable[Path]:
    """Yield every YAML file under registry/news_sources/ in deterministic order."""
    if not NEWS_SOURCES_DIR.exists():
        return
    for p in sorted(NEWS_SOURCES_DIR.rglob("*.yaml")):
        yield p


def load_all_entries() -> list[tuple[Path, dict]]:
    """Load every entry from every registry file. Returns [(file_path, entry), ...]."""
    out: list[tuple[Path, dict]] = []
    for p in iter_registry_files():
        data = load_yaml(p)
        if data is None:
            continue
        if not isinstance(data, list):
            raise ValueError(f"{p}: top-level YAML must be a list of source entries")
        for entry in data:
            if not isinstance(entry, dict):
                raise ValueError(f"{p}: entry is not a mapping: {entry!r}")
            out.append((p, entry))
    return out


# --------------------------------------------------------------------------- #
# Hashing                                                                      #
# --------------------------------------------------------------------------- #

def entry_fingerprint(entry: dict) -> str:
    """Stable SHA-256 over entry contents excluding lineage verification timestamp."""
    redacted = dict(entry)
    lineage = dict(redacted.get("lineage", {}))
    lineage.pop("verified_at", None)
    redacted["lineage"] = lineage
    import json
    blob = json.dumps(redacted, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
