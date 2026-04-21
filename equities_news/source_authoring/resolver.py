"""resolver — sources_for_symbol(ticker) resolution.

Pure function. Combines:
    universal.* + geo.{country}.* + sector.{sector}.*
    + (sub_industry.{sector}.{override}.* if overlay.sub_industry_override else [])
    + per-symbol overlay sources (IR URL, press release feed, X handle, LinkedIn,
      transcript provider, local regulatory filings URL).

Each ResolvedSource carries:
    - all frozen schema fields
    - origin_tier  (universal | geo | sector | sub_industry | symbol_direct)
    - effective_edge_weight = edge_weight_prior * resolver_tier_weight / max_tier_weight
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import (
    NEWS_SOURCES_DIR,
    NamespaceRules,
    classify_namespace,
    load_all_entries,
    load_yaml,
    GROUND_TRUTH_DIR,
    REGISTRY_DIR,
)


# --------------------------------------------------------------------------- #
# Resolved source dataclass                                                   #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ResolvedSource:
    source_id: str
    source_name: str
    url: str
    adapter_type: str
    jurisdiction: str
    language: str
    cost_tier: str
    update_cadence: str
    event_types_covered: tuple[str, ...]
    edge_weight_prior: float
    typical_lead_time_vs_english_minutes: int
    origin_tier: str          # universal | geo | sector | sub_industry | symbol_direct
    effective_edge_weight: float


# --------------------------------------------------------------------------- #
# Symbol metadata loader                                                       #
# --------------------------------------------------------------------------- #

_SECTOR_TO_SLUG = {
    "Information Technology": "information_technology",
    "Communication Services": "communication_services",
    "Consumer Discretionary": "consumer_discretionary",
    "Consumer Staples": "consumer_staples",
    "Health Care": "health_care",
    "Financials": "financials",
    "Industrials": "industrials",
    "Energy": "energy",
    "Utilities": "utilities",
    "Real Estate": "real_estate",
    "Materials": "materials",
}


@dataclass(frozen=True)
class SymbolMeta:
    ticker: str
    company: str
    country: str
    country_code: str
    exchange: str
    sector_slug: str


@lru_cache(maxsize=1)
def _load_symbol_index() -> dict[str, SymbolMeta]:
    """Build {ticker -> SymbolMeta} from bible_v1.xlsx."""
    import openpyxl
    bible = GROUND_TRUTH_DIR / "bible_v1.xlsx"
    iso = load_yaml(REGISTRY_DIR / "iso_3166_codes.yaml")["country_to_code"]
    wb = openpyxl.load_workbook(bible, read_only=True, data_only=True)
    out: dict[str, SymbolMeta] = {}
    for sheet_name in wb.sheetnames:
        if sheet_name == "Overview":
            continue
        slug = _SECTOR_TO_SLUG.get(sheet_name)
        if slug is None:
            continue
        ws = wb[sheet_name]
        for row in ws.iter_rows(min_row=5, values_only=True):
            if not row or not isinstance(row[1], str):
                continue
            ticker = row[1]
            country = row[4] or ""
            cc = iso.get(country, "")
            out[ticker] = SymbolMeta(
                ticker=ticker,
                company=row[2] or "",
                country=country,
                country_code=cc,
                exchange=row[5] or "",
                sector_slug=slug,
            )
    return out


# --------------------------------------------------------------------------- #
# Registry loader                                                              #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _load_registry_by_tier() -> dict[str, dict[str, dict]]:
    """
    Returns {
        'universal':      {source_id: entry},
        'geo.{cc}':       {source_id: entry},
        'sector.{slug}':  {source_id: entry},
        'sub_industry.{sector}.{sub}': {source_id: entry},
    }
    """
    rules = NamespaceRules.load()
    out: dict[str, dict[str, dict]] = {}
    for _, entry in load_all_entries():
        sid = entry.get("source_id")
        if not isinstance(sid, str):
            continue
        try:
            ns_name, parts = classify_namespace(sid, rules)
        except ValueError:
            continue
        if ns_name == "universal":
            key = "universal"
        elif ns_name == "geo":
            key = f"geo.{parts[1]}"
        elif ns_name == "sector":
            key = f"sector.{parts[1]}"
        elif ns_name == "sub_industry":
            key = f"sub_industry.{parts[1]}.{parts[2]}"
        else:
            continue
        out.setdefault(key, {})[sid] = entry
    return out


# --------------------------------------------------------------------------- #
# Overlay loader (optional — reads from enriched bible if present)             #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _load_overlay_index() -> dict[str, dict]:
    """
    Return {ticker -> overlay_dict} from bible_v1_enriched.xlsx if it exists,
    else {}. Overlay dict has the six overlay column keys.
    """
    import openpyxl
    path = GROUND_TRUTH_DIR / "bible_v1_enriched.xlsx"
    if not path.exists():
        return {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: dict[str, dict] = {}
    for sheet_name in wb.sheetnames:
        if sheet_name == "Overview":
            continue
        ws = wb[sheet_name]
        # Header row assumed at row 4; find overlay columns by name.
        header = None
        for r in ws.iter_rows(min_row=4, max_row=4, values_only=True):
            header = list(r)
            break
        if header is None:
            continue
        name_to_idx = {h: i for i, h in enumerate(header) if h}
        overlay_cols = [
            "IR_URL", "Press_Release_Feed_URL", "Official_X_Handle",
            "Official_LinkedIn_URL", "Earnings_Transcript_Provider",
            "Local_Regulatory_Filings_URL", "Sub_Industry_Override",
        ]
        ticker_idx = name_to_idx.get("Ticker")
        if ticker_idx is None:
            continue
        for row in ws.iter_rows(min_row=5, values_only=True):
            if not row or not isinstance(row[ticker_idx], str):
                continue
            t = row[ticker_idx]
            entry: dict = {}
            for col in overlay_cols:
                idx = name_to_idx.get(col)
                if idx is None:
                    continue
                val = row[idx]
                if val == "null" or val is None:
                    entry[col] = None
                else:
                    entry[col] = val
            out[t] = entry
    return out


# --------------------------------------------------------------------------- #
# Public resolver                                                              #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=2048)
def sources_for_symbol(ticker: str) -> tuple[ResolvedSource, ...]:
    """Resolve the full source tuple applicable to `ticker`.

    Returns a tuple (not a list) for cache-safety.
    Raises KeyError if ticker is not in the Bible.
    """
    idx = _load_symbol_index()
    meta = idx.get(ticker)
    if meta is None:
        raise KeyError(f"ticker {ticker!r} not in bible_v1.xlsx")

    rules = NamespaceRules.load()
    reg = _load_registry_by_tier()
    tier_w = rules.resolver_tier_weights
    max_w = max(tier_w.values())

    def _mk(entry: dict, tier: str) -> ResolvedSource:
        prior = float(entry.get("edge_weight_prior", 0.0))
        eff = prior * tier_w[tier] / max_w
        return ResolvedSource(
            source_id=entry["source_id"],
            source_name=entry.get("source_name", ""),
            url=entry.get("url", ""),
            adapter_type=entry.get("adapter_type", "manual"),
            jurisdiction=entry.get("jurisdiction", "XX"),
            language=entry.get("language", "en"),
            cost_tier=entry.get("cost_tier", "free"),
            update_cadence=entry.get("update_cadence", "ad_hoc"),
            event_types_covered=tuple(entry.get("event_types_covered", []) or ()),
            edge_weight_prior=prior,
            typical_lead_time_vs_english_minutes=int(
                entry.get("typical_lead_time_vs_english_minutes", 0)
            ),
            origin_tier=tier,
            effective_edge_weight=eff,
        )

    resolved: list[ResolvedSource] = []

    # 1. universal.*
    for e in reg.get("universal", {}).values():
        resolved.append(_mk(e, "universal"))

    # 2. geo.{cc}
    cc = meta.country_code.lower()
    if cc:
        for e in reg.get(f"geo.{cc}", {}).values():
            resolved.append(_mk(e, "geo"))

    # 3. sector.{slug}
    for e in reg.get(f"sector.{meta.sector_slug}", {}).values():
        resolved.append(_mk(e, "sector"))

    # 4. sub_industry.{sector}.{override} — only if overlay contains one.
    overlay = _load_overlay_index().get(ticker, {})
    override = overlay.get("Sub_Industry_Override")
    if override:
        # override may be a full slug "sub_industry.{sector}.{sub}" or just "{sub}".
        if override.startswith("sub_industry."):
            parts = override.split(".")
            if len(parts) >= 3:
                key = ".".join(parts[:3])
            else:
                key = None
        else:
            key = f"sub_industry.{meta.sector_slug}.{override}"
        if key:
            for e in reg.get(key, {}).values():
                resolved.append(_mk(e, "sub_industry"))

    # 5. symbol_direct — per-symbol overlay entries synthesised as ad-hoc sources.
    direct_specs = [
        ("IR_URL", "ir", "manual", ["material_disclosure", "earnings_report"]),
        ("Press_Release_Feed_URL", "press_release_feed", "rss",
         ["material_disclosure", "product_launch", "executive_change"]),
        ("Official_X_Handle", "x_handle", "api",
         ["ceo_statement_social", "social_mention_spike"]),
        ("Official_LinkedIn_URL", "linkedin", "scraper",
         ["executive_change"]),
        ("Earnings_Transcript_Provider", "transcript_provider", "manual",
         ["earnings_report"]),
        ("Local_Regulatory_Filings_URL", "local_filings", "scraper",
         ["regulatory_filing", "material_disclosure"]),
    ]
    for col, slug_suffix, adapter, events in direct_specs:
        val = overlay.get(col)
        if not val or val == "none":
            continue
        sid = f"symbol_direct.{ticker.lower()}.{slug_suffix}"
        synthetic = {
            "source_id": sid,
            "source_name": f"{ticker} — {col}",
            "url": val if col != "Official_X_Handle" else f"https://x.com/{val}",
            "adapter_type": adapter,
            "jurisdiction": meta.country_code or "XX",
            "language": "en",
            "cost_tier": "free",
            "update_cadence": "realtime",
            "event_types_covered": events,
            "edge_weight_prior": 1.0,
            "typical_lead_time_vs_english_minutes": 0,
        }
        resolved.append(_mk(synthetic, "symbol_direct"))

    return tuple(resolved)


def invalidate_caches() -> None:
    """Call after registry mutation within a single process."""
    _load_symbol_index.cache_clear()
    _load_registry_by_tier.cache_clear()
    _load_overlay_index.cache_clear()
    sources_for_symbol.cache_clear()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", help="e.g. NVDA, 7203 (Toyota), HSBA")
    args = ap.parse_args()
    try:
        srcs = sources_for_symbol(args.ticker)
    except KeyError as e:
        print(e, file=sys.stderr)
        raise SystemExit(2)
    print(f"{args.ticker}: {len(srcs)} sources")
    by_tier: dict[str, int] = {}
    for s in srcs:
        by_tier[s.origin_tier] = by_tier.get(s.origin_tier, 0) + 1
        print(f"  [{s.origin_tier:>14}]  eff={s.effective_edge_weight:.2f}  {s.source_id}")
    print(f"\nby tier: {by_tier}")
