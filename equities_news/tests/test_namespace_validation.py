"""Exercise namespace_rules.yaml and validate_entry() on the current registry."""
from __future__ import annotations

import pytest
from _lib import (
    NamespaceRules,
    classify_namespace,
    load_all_entries,
    load_event_type_slugs,
    validate_entry,
)


def _good_entry(sid: str = "universal.test_source") -> dict:
    return {
        "source_id": sid,
        "source_name": "Test",
        "url": "https://example.com",
        "adapter_type": "rss",
        "jurisdiction": "XX",
        "language": "en",
        "cost_tier": "free",
        "update_cadence": "daily",
        "event_types_covered": ["earnings_report"],
        "edge_weight_prior": 0.5,
        "typical_lead_time_vs_english_minutes": 0,
        "lineage": {"authored_by": "test", "authored_at": "2026-04-21T00:00:00Z"},
    }


def test_rules_load():
    r = NamespaceRules.load()
    assert r.version >= 1
    assert r.slug_component_regex.pattern.startswith("^[a-z0-9]")
    assert "information_technology" in r.sector_slugs
    assert r.resolver_tier_weights["symbol_direct"] > r.resolver_tier_weights["universal"]


def test_classify_namespace_universal():
    r = NamespaceRules.load()
    ns, parts = classify_namespace("universal.reuters", r)
    assert ns == "universal"
    assert parts == ["universal", "reuters"]


def test_classify_namespace_geo():
    r = NamespaceRules.load()
    ns, parts = classify_namespace("geo.jp.tdnet", r)
    assert ns == "geo"
    assert parts[1] == "jp"


def test_classify_namespace_sector():
    r = NamespaceRules.load()
    ns, parts = classify_namespace("sector.information_technology.trendforce", r)
    assert ns == "sector"


def test_classify_namespace_sub_industry():
    r = NamespaceRules.load()
    ns, parts = classify_namespace("sub_industry.health_care.biotechnology_clinical_stage.endpoints", r)
    assert ns == "sub_industry"


def test_classify_namespace_rejects_bad_prefix():
    r = NamespaceRules.load()
    with pytest.raises(ValueError):
        classify_namespace("random.foo", r)


def test_classify_namespace_rejects_bad_depth():
    r = NamespaceRules.load()
    with pytest.raises(ValueError):
        classify_namespace("universal.reuters.extra", r)


def test_validate_entry_accepts_canonical():
    r = NamespaceRules.load()
    ets = load_event_type_slugs()
    errs = validate_entry(_good_entry(), r, ets, "test")
    assert errs == []


def test_validate_entry_rejects_bad_adapter():
    r = NamespaceRules.load()
    ets = load_event_type_slugs()
    e = _good_entry()
    e["adapter_type"] = "webhook"
    errs = validate_entry(e, r, ets, "test")
    assert any("adapter_type" in x.message for x in errs)


def test_validate_entry_rejects_bad_edge_weight():
    r = NamespaceRules.load()
    ets = load_event_type_slugs()
    e = _good_entry()
    e["edge_weight_prior"] = 1.5
    errs = validate_entry(e, r, ets, "test")
    assert any("edge_weight_prior" in x.message for x in errs)


def test_validate_entry_rejects_unknown_event_type():
    r = NamespaceRules.load()
    ets = load_event_type_slugs()
    e = _good_entry()
    e["event_types_covered"] = ["not_a_real_event_type"]
    errs = validate_entry(e, r, ets, "test")
    assert any("event_type" in x.message for x in errs)


def test_validate_entry_rejects_missing_lineage_authored_by():
    r = NamespaceRules.load()
    ets = load_event_type_slugs()
    e = _good_entry()
    e["lineage"] = {"authored_at": "2026-04-21T00:00:00Z"}
    errs = validate_entry(e, r, ets, "test")
    assert any("authored_by" in x.message for x in errs)


def test_full_registry_passes_schema():
    r = NamespaceRules.load()
    ets = load_event_type_slugs()
    bad: list[tuple[str, str]] = []
    for p, e in load_all_entries():
        errs = validate_entry(e, r, ets, p.name)
        for err in errs:
            bad.append((str(p.name), err.message))
    assert not bad, f"schema violations:\n  " + "\n  ".join(f"{f}: {m}" for f, m in bad)


def test_registry_source_ids_unique():
    seen: dict[str, str] = {}
    dupes: list[tuple[str, str, str]] = []
    for p, e in load_all_entries():
        sid = e.get("source_id")
        if not isinstance(sid, str):
            continue
        if sid in seen:
            dupes.append((sid, seen[sid], str(p.name)))
        else:
            seen[sid] = str(p.name)
    assert not dupes, f"duplicate source_ids: {dupes}"
