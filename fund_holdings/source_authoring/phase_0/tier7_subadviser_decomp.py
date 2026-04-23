"""Tier 7: Subadviser decomposition for white-label trust hosts.

tidal, exchange_traded_concepts, alpha_architect, and `other_sponsors` are not
real sponsors — they're trust structures hosting dozens of sub-advisers with
their own websites. The v1 probe tried a single host per trust and failed.

This script partitions each trust's fund list by fund-name prefix, identifies
each sub-adviser's probable website, and probes those sites directly using
the Tier 1 + Tier 3 toolchain.

Approach:
  1) For each trust, group funds by fund-name prefix (first 1-3 tokens before "ETF").
  2) Map each prefix → candidate sub-adviser hostnames (heuristic + lookup).
  3) Run the Tier 1 pattern probe against each sub-adviser host.
  4) Cap at top-10 sub-advisers per trust by fund count.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok, MIN_BYTES_FOR_FORMAT  # type: ignore
from sponsor_discovery_v2 import (
    expand, fetch_curl_cffi, guess_format_from_url, verify_body,
    IMPERSONATE, GENERIC_PATH_PATTERNS,
)

PHASE0 = Path(__file__).parent

# Known sub-adviser → website mapping (Tidal, EA Series Trust, etc.)
# Empirically derived from the Phase 0 planning notes.
SUBADVISER_HOSTS: dict[str, list[str]] = {
    # Tidal sub-advisers
    "yieldmax": ["yieldmaxetfs.com", "www.yieldmaxetfs.com"],
    "roundhill": ["www.roundhillinvestments.com", "roundhillinvestments.com"],
    "simplify": ["www.simplify.us", "simplify.us"],
    "themes": ["themesetfs.com", "www.themesetfs.com"],
    "cambria": ["www.cambriafunds.com", "cambriafunds.com", "cambriainvestments.com"],
    "bitwise": ["bitwiseinvestments.com", "www.bitwiseinvestments.com"],
    "defiance": ["www.defianceetfs.com", "defianceetfs.com"],
    "granite": ["graniteshares.com", "www.graniteshares.com"],
    "sp_funds": ["www.spfunds.com", "spfunds.com"],
    "horizon_kinetics": ["horizonkinetics.com", "www.horizonkinetics.com"],
    "nightshares": ["www.nightshares.com", "nightshares.com"],
    "distillate": ["distillatecapital.com", "www.distillatecapital.com"],
    "convexityshares": ["www.convexityshares.com", "convexityshares.com"],
    "sofi": ["www.sofi.com"],
    "gotham": ["www.gothametfs.com", "gothametfs.com"],
    # Alpha Architect / EA Series Trust sub-advisers
    "alphaarchitect": ["etfsite.alphaarchitect.com", "www.alphaarchitect.com"],
    "strive": ["www.strive.com", "strive.com"],
    "bridgeway": ["bridgeway.com", "www.bridgeway.com"],
    "sequoia": ["www.sequoiaglobaladvisors.com"],
    "euclidean": ["www.euclidean.com"],
    "bluestone": ["bluestonefunds.com"],
    # ETC (Exchange Traded Concepts) sub-advisers
    "robo_global": ["www.roboglobal.com"],
    "etc_6_meridian": ["www.6-meridian.com"],
    "emqq": ["emqqetf.com"],
    "range": ["rangefunds.com", "www.rangefunds.com"],
    # Trust infrastructure (also for other_sponsors)
    "innovator": ["www.innovatoretfs.com"],
    "roundhill_etf_trust": ["www.roundhillinvestments.com"],
    "bitwise_bit": ["bitwiseinvestments.com"],
    "horizon": ["www.horizoncapital.com"],
    "madison": ["www.madisonfunds.com"],
    "baron": ["www.baronfunds.com"],
    "adaptive": ["adaptivefunds.com"],
    "angel_oak": ["www.angeloakcapital.com"],
    "timothy_plan": ["timothyplan.com"],
    "sei": ["www.seic.com", "funds.seic.com"],
    "thornburg": ["www.thornburg.com"],
    "thrivent": ["www.thrivent.com"],
}


def classify_fund_by_name(fund_name: str) -> str | None:
    """Heuristic: extract probable subadviser from fund name."""
    low = fund_name.lower()
    mapping = [
        ("yieldmax", "yieldmax"),
        ("roundhill", "roundhill"),
        ("simplify", "simplify"),
        ("themes", "themes"),
        ("cambria", "cambria"),
        ("bitwise", "bitwise"),
        ("defiance", "defiance"),
        ("sp funds", "sp_funds"),
        ("sp fund", "sp_funds"),
        ("horizon kinetics", "horizon_kinetics"),
        ("kinetics", "horizon_kinetics"),
        ("nightshares", "nightshares"),
        ("distillate", "distillate"),
        ("convexity", "convexityshares"),
        ("sofi", "sofi"),
        ("gotham", "gotham"),
        ("alpha architect", "alphaarchitect"),
        ("strive", "strive"),
        ("bridgeway", "bridgeway"),
        ("sequoia", "sequoia"),
        ("euclidean", "euclidean"),
        ("bluestone", "bluestone"),
        ("robo global", "robo_global"),
        (" robo", "robo_global"),
        ("6 meridian", "etc_6_meridian"),
        ("emqq", "emqq"),
        ("range ", "range"),
        ("innovator", "innovator"),
        ("madison", "madison"),
        ("baron", "baron"),
        ("adaptive", "adaptive"),
        ("angel oak", "angel_oak"),
        ("timothy plan", "timothy_plan"),
        ("sei ", "sei"),
        ("thornburg", "thornburg"),
        ("thrivent", "thrivent"),
        ("tema", "tema"),
        ("nuveen", "nuveen"),
    ]
    for token, slug in mapping:
        if token in low:
            return slug
    # fallback: first token
    toks = re.split(r"[\s\-\.]+", fund_name.strip())
    if toks:
        return toks[0].lower()
    return None


def build_patterns_for_host(host: str) -> list[tuple[str, str]]:
    """Generate a pattern catalogue for a single host (ticker placeholder)."""
    patterns: list[tuple[str, str]] = []
    for path in GENERIC_PATH_PATTERNS:
        tmpl = f"https://{host}{path}"
        fmt = ("csv" if ".csv" in path else
               ("xlsx" if ".xlsx" in path else
                ("json" if "/api/" in path else "html_table")))
        patterns.append((tmpl, fmt))
    return patterns


def probe_subadviser(subadviser_slug: str, host: str, probe_tickers: list[str]) -> list[dict]:
    """Probe one (subadviser, host) pair across all patterns."""
    results: list[dict] = []
    if len(probe_tickers) < 2:
        return [{"subadviser": subadviser_slug, "host": host,
                 "event": "insufficient_probe_tickers"}]
    session = cc_requests.Session(impersonate=IMPERSONATE)
    patterns = build_patterns_for_host(host)
    # First pass
    passing = []
    t1 = probe_tickers[0]
    for tmpl, fmt in patterns:
        url = expand(tmpl, t1)
        status, final_url, body, ct = fetch_curl_cffi(session, url)
        fmt_real = guess_format_from_url(final_url, ct, body) if status == 200 else fmt
        ok, reason, info = (False, f"http_{status}", {})
        if status == 200:
            ok, reason, info = verify_body(body, fmt_real)
        results.append({
            "subadviser": subadviser_slug, "host": host, "template": tmpl,
            "ticker": t1, "url": url, "status": status,
            "ok": bool(ok), "reason": reason, "format": fmt_real,
            "pass": "first",
        })
        if ok:
            passing.append((tmpl, fmt_real))
    # Second pass on passing patterns
    t2 = probe_tickers[1]
    confirmed = []
    for tmpl, fmt in passing:
        url = expand(tmpl, t2)
        status, final_url, body, ct = fetch_curl_cffi(session, url)
        fmt_real = guess_format_from_url(final_url, ct, body) if status == 200 else fmt
        ok, reason, info = (False, f"http_{status}", {})
        if status == 200:
            ok, reason, info = verify_body(body, fmt_real)
        results.append({
            "subadviser": subadviser_slug, "host": host, "template": tmpl,
            "ticker": t2, "url": url, "status": status,
            "ok": bool(ok), "reason": reason, "format": fmt_real,
            "pass": "second",
        })
        if ok:
            confirmed.append((tmpl, fmt_real))
    for tmpl, fmt in confirmed:
        results.append({
            "sponsor": f"{subadviser_slug}",
            "event": "confirmed_pattern",
            "label": f"subadv:{subadviser_slug}",
            "template": tmpl, "format": fmt,
            "verified_tickers": [t1, t2],
        })
    return results


def partition_trust_funds(trust_slug: str) -> dict[str, list[dict]]:
    """Group a trust's excluded funds by subadviser slug. Cap top-10 by count."""
    rt_path = PHASE0 / "recovery_targets.json"
    with open(rt_path) as f:
        targets = json.load(f)
    excl = targets.get("excluded_funds_by_sponsor", {}).get(trust_slug, [])
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for r in excl:
        sub = classify_fund_by_name(r["fund_name"])
        if sub:
            by_sub[sub].append(r)
    # Sort by count descending; keep top 10
    top = sorted(by_sub.items(), key=lambda kv: -len(kv[1]))[:10]
    return dict(top)


def main():
    trusts = ["tidal", "alpha_architect", "exchange_traded_concepts", "other_sponsors"]
    all_results: list[dict] = []
    confirmed_new: list[dict] = []
    mapping_summary: list[str] = []

    for trust in trusts:
        parts = partition_trust_funds(trust)
        print(f"\n=== {trust}: {len(parts)} sub-advisers (top 10) ===")
        for sub, funds in parts.items():
            tickers = [f["ticker"] for f in funds[:4]]
            # skip if no known hosts
            hosts = SUBADVISER_HOSTS.get(sub)
            print(f"  {sub:30s} n_funds={len(funds):3d}  probe_tickers={tickers[:2]}  hosts={hosts}")
            mapping_summary.append(f"{trust} / {sub} (n={len(funds)}): hosts={hosts}")
            if not hosts or len(tickers) < 2:
                all_results.append({
                    "trust": trust, "subadviser": sub,
                    "event": "no_host_mapping" if not hosts else "insufficient_tickers",
                    "n_funds": len(funds),
                })
                continue
            # Probe across each host
            got_confirmed = False
            for host in hosts[:2]:
                recs = probe_subadviser(sub, host, tickers[:2])
                all_results.extend(recs)
                for r in recs:
                    if r.get("event") == "confirmed_pattern":
                        confirmed_new.append(r)
                        got_confirmed = True
                        break
                if got_confirmed:
                    break

    # Append to global probes + confirmed
    out_probes = PHASE0 / "sponsor_probes_v2.jsonl"
    with open(out_probes, "a") as f:
        for r in all_results:
            f.write(json.dumps(r) + "\n")

    confirmed_path = PHASE0 / "sponsor_endpoints_v2_candidates.jsonl"
    by_sponsor: dict[str, list] = {}
    for r in confirmed_new:
        by_sponsor.setdefault(r["sponsor"], []).append(r)
    with open(confirmed_path, "a") as f:
        for s, recs in by_sponsor.items():
            f.write(json.dumps({"sponsor": s, "patterns": recs, "origin": "subadviser"}) + "\n")

    # Summary
    summary_path = PHASE0 / "logs" / "sponsor_discovery" / "subadviser_mapping.md"
    summary_path.write_text(
        "# Subadviser Partition & Probe Summary\n\n" + "\n".join(mapping_summary)
        + f"\n\n## Confirmed subadviser endpoints: {len(by_sponsor)}\n"
        + "\n".join(f"- {s}: {v[0]['template']}" for s, v in by_sponsor.items())
    )
    print(f"\nT7 confirmed {len(by_sponsor)} subadvisers.")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
