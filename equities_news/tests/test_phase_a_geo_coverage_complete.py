"""Every country in the Bible universe must have a non-empty geo/{cc}.yaml."""
from __future__ import annotations

from pathlib import Path

from _lib import NEWS_SOURCES_DIR, REGISTRY_DIR, load_yaml


def test_countries_file_exists():
    assert (REGISTRY_DIR / "countries_in_universe.yaml").exists()


def test_every_country_has_geo_file():
    countries = load_yaml(REGISTRY_DIR / "countries_in_universe.yaml")["countries"]
    missing: list[str] = []
    empty: list[str] = []
    for code, rec in countries.items():
        p = NEWS_SOURCES_DIR / "geo" / f"{code.lower()}.yaml"
        if not p.exists():
            missing.append(code)
            continue
        entries = load_yaml(p) or []
        if not entries:
            empty.append(code)
    assert not missing, f"missing geo files: {missing}"
    assert not empty, f"empty geo files: {empty}"


def test_geo_file_jurisdictions_match_filename():
    """Every entry in geo/xx.yaml must have jurisdiction == XX."""
    offenders: list[str] = []
    for p in sorted((NEWS_SOURCES_DIR / "geo").glob("*.yaml")):
        cc = p.stem.upper()
        entries = load_yaml(p) or []
        for e in entries:
            if not isinstance(e, dict):
                continue
            juris = e.get("jurisdiction")
            if juris != cc:
                offenders.append(f"{p.name}: {e.get('source_id')} jurisdiction={juris}")
    assert not offenders, "\n".join(offenders)


def test_geo_source_ids_under_correct_prefix():
    offenders: list[str] = []
    for p in sorted((NEWS_SOURCES_DIR / "geo").glob("*.yaml")):
        cc = p.stem
        expected = f"geo.{cc}."
        for e in load_yaml(p) or []:
            sid = e.get("source_id") if isinstance(e, dict) else None
            if not isinstance(sid, str) or not sid.startswith(expected):
                offenders.append(f"{p.name}: {sid!r}")
    assert not offenders, "\n".join(offenders)


def test_universal_yaml_exists_and_nonempty():
    p = NEWS_SOURCES_DIR / "universal.yaml"
    assert p.exists()
    entries = load_yaml(p) or []
    assert len(entries) > 20, f"universal.yaml too sparse: {len(entries)} entries"
    for e in entries:
        sid = e.get("source_id") if isinstance(e, dict) else None
        assert isinstance(sid, str) and sid.startswith("universal."), f"bad universal source_id: {sid}"
