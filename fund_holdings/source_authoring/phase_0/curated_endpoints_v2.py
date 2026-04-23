"""Curated endpoint patterns for major ETF sponsors, based on industry knowledge
of common API/URL structures. Each pattern is empirically verified by this
script against 2+ probe tickers before being admitted to
sponsor_endpoints_v2_candidates.jsonl.

This is the "Tier 5 manual" of the recovery plan — hand-curated endpoint
patterns that are content-verified with curl_cffi.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from sponsor_discovery_v2 import (
    expand, fetch_curl_cffi, guess_format_from_url, verify_body, IMPERSONATE,
)

PHASE0 = Path(__file__).parent

# Curated patterns: [(sponsor, template, format, referer_template_or_None), ...]
# Each template verified on at least 2 probe tickers.
CURATED = [
    # === WAF/TLS blocked (Bucket A) — curl_cffi clears these ===
    # Invesco: the holdings download endpoint is unique per fund but uses a known API
    ("invesco",
     "https://www.invesco.com/us/financial-products/etfs/product-detail?audienceType=Investor&ticker={TICKER}",
     "html_table", None),
    # Actually Invesco's data is AJAX-loaded; try the direct API
    ("invesco",
     "https://www.invesco.com/us-rest/contentdetail?contentId=us-etf-product-detail.holdings&ticker={TICKER}",
     "json", None),

    # WisdomTree — holdings CSV via direct download
    ("wisdomtree",
     "https://www.wisdomtree.com/-/media/us-media-files/documents/resource-library/fund-documents/holdings/{TICKER}-holdings.csv",
     "csv", None),

    # JPMorgan AM — holdings via the docs API
    ("jpmorgan",
     "https://am.jpmorgan.com/services/etf-holdings?ticker={TICKER}",
     "json", None),

    # Dimensional — CSV download pattern
    ("dimensional",
     "https://us.dimensional.com/api/funds/{TICKER}/holdings.csv",
     "csv", None),
    ("dimensional",
     "https://us.dimensional.com/api/funds/{TICKER}/holdings",
     "json", None),

    # VanEck — holdings CSV/JSON
    ("vaneck",
     "https://www.vaneck.com/api/funds/{ticker_lower}/holdings.json",
     "json", None),
    ("vaneck",
     "https://www.vaneck.com/etf/etf-holdings-csv/?fund={TICKER}",
     "csv", None),

    # Franklin Templeton — fund holdings JSON
    ("franklin_templeton",
     "https://www.franklintempleton.com/api/etf/holdings/{TICKER}",
     "json", None),
    ("franklin_templeton",
     "https://www.franklintempleton.com/us-etfs/holdings?fund={TICKER}",
     "html_table", None),

    # Fidelity — the composition CSV download
    ("fidelity",
     "https://fundresearch.fidelity.com/mutual-funds/download-csv/composition/{TICKER}",
     "csv", None),

    # Nuveen — holdings endpoint
    ("nuveen",
     "https://www.nuveen.com/api/etf/{TICKER}/holdings",
     "json", None),

    # Schwab — already confirmed via Tier 1 but let's add the CSV fallback
    ("schwab",
     "https://www.schwabassetmanagement.com/allholdings/{TICKER}",
     "html_table", None),

    # T. Rowe Price — page holdings
    ("t_rowe_price",
     "https://www.troweprice.com/services/advisor-api/etfs/holdings/{TICKER}",
     "json", None),

    # Northern Trust FlexShares — CSV
    ("northern_trust",
     "https://flexshares.com/funds/{ticker_lower}/holdings",
     "html_table", None),
    ("northern_trust",
     "https://flexshares.com/documents/{ticker_lower}_holdings.csv",
     "csv", None),

    # Pacer ETFs
    ("pacer",
     "https://www.paceretfs.com/products/{ticker_lower}",
     "html_table", None),

    # John Hancock
    ("john_hancock",
     "https://www.jhinvestments.com/api/etf-holdings/{TICKER}",
     "json", None),

    # Janus Henderson — holdings
    ("janus_henderson",
     "https://www.janushenderson.com/api/etf/{TICKER}/holdings",
     "json", None),

    # Capital Group
    ("capital_group",
     "https://www.capitalgroup.com/api/advisor/funds/{TICKER}/holdings",
     "json", None),

    # Blackrock non-iShares (BINC, DYNF, etc.) — uses same iShares pattern
    ("blackrock",
     "https://www.blackrock.com/us/individual/products/{TICKER}",
     "html_table", None),

    # Alliance Bernstein
    ("alliancebernstein",
     "https://www.abfunds.com/link/en/etf/{ticker_lower}-overview",
     "html_table", None),

    # WisdomTree alternate — api route
    ("wisdomtree",
     "https://www.wisdomtree.com/api/fund-holdings?ticker={TICKER}",
     "json", None),

    # ProShares — confirmed via Tier 1 (product page) but try the holdings CSV
    ("proshares",
     "https://www.proshares.com/globalassets/proshares/resources/holdings/{ticker_lower}-holdings.csv",
     "csv", None),

    # PIMCO
    ("pimco",
     "https://www.pimco.com/en-us/investments/etf/{ticker_lower}",
     "html_table", None),

    # AllSpring
    ("allspring",
     "https://www.allspringglobal.com/api/etfs/{TICKER}/holdings",
     "json", None),

    # Victory Capital
    ("victory_capital",
     "https://www.vcm.com/api/etf-holdings/{TICKER}",
     "json", None),

    # Harbor
    ("harbor",
     "https://www.harborcapital.com/api/etfs/{ticker_lower}/holdings",
     "json", None),

    # Columbia
    ("columbia",
     "https://www.columbiathreadneedleus.com/api/funds/{TICKER}/holdings",
     "json", None),

    # PGIM
    ("pgim",
     "https://www.pgim.com/api/etf/{ticker_lower}/holdings",
     "json", None),

    # Principal
    ("principal",
     "https://www.principalam.com/api/etfs/{TICKER}/holdings",
     "json", None),

    # BondBloxx — pages
    ("bondbloxx",
     "https://bondbloxx.com/etfs/{ticker_lower}/",
     "html_table", None),

    # Neuberger Berman
    ("neuberger_berman",
     "https://www.nb.com/en/global/etf/{ticker_lower}",
     "html_table", None),

    # BNY Mellon
    ("bny_mellon",
     "https://im.bnymellon.com/us/en/intermediary/etf/{ticker_lower}",
     "html_table", None),

    # Hartford
    ("hartford",
     "https://www.hartfordfunds.com/etfs/{ticker_lower}-overview.html",
     "html_table", None),

    # Touchstone
    ("touchstone",
     "https://www.touchstoneinvestments.com/etfs/{TICKER}",
     "html_table", None),

    # DoubleLine
    ("doubleline",
     "https://www.doubleline.com/etfs/{ticker_lower}/",
     "html_table", None),

    # Strategy Shares
    ("strategy_shares",
     "https://www.strategysharesetfs.com/etfs/{ticker_lower}/",
     "html_table", None),

    # ALPS
    ("alps",
     "https://www.alpsfunds.com/etfs/{ticker_lower}",
     "html_table", None),

    # Alpha Architect — etfsite only hosts some
    ("alpha_architect",
     "https://etfsite.alphaarchitect.com/{ticker_lower}/",
     "html_table", None),

    # Innovator — confirmed Tier 1
    ("innovator",
     "https://www.innovatoretfs.com/etf/?ticker={TICKER}",
     "html_table", None),

    # Calamos — confirmed Tier 1
    ("calamos",
     "https://www.calamos.com/etfs/{ticker_lower}",
     "html_table", None),

    # Sprott
    ("sprott",
     "https://sprottetfs.com/etfs/{ticker_lower}/",
     "html_table", None),

    # Xtrackers — DWS
    ("xtrackers",
     "https://etfus.dws.com/en-us/{TICKER}",
     "html_table", None),

    # Goldman Sachs
    ("goldman_sachs",
     "https://www.gsam.com/content/gsam/us/en/individual/products/etf/{ticker_lower}",
     "html_table", None),

    # MFS
    ("mfs",
     "https://www.mfs.com/en-us/investment-professional/products/mfs-etfs.html?ticker={TICKER}",
     "html_table", None),

    # abrdn
    ("abrdn",
     "https://www.abrdn.com/en-us/us/investor/etf/fund-centre/{ticker_lower}",
     "html_table", None),

    # Alger
    ("alger",
     "https://www.alger.com/etfs/{ticker_lower}",
     "html_table", None),

    # Russell Investments
    ("russell_investments",
     "https://russellinvestments.com/us/etf/{TICKER}",
     "html_table", None),

    # Macquarie
    ("macquarie",
     "https://www.delawarefunds.com/etfs/{ticker_lower}",
     "html_table", None),

    # Putnam
    ("putnam",
     "https://www.putnam.com/individual/etf/{TICKER}",
     "html_table", None),

    # Virtus
    ("virtus",
     "https://www.virtusetfs.com/etf/{TICKER}",
     "html_table", None),

    # TCW
    ("tcw",
     "https://www.tcw.com/products/etf/{ticker_lower}",
     "html_table", None),

    # Tortoise
    ("tortoise",
     "https://tortoiseetfs.com/etfs/{ticker_lower}/",
     "html_table", None),

    # TEMA
    ("tema",
     "https://www.temaetfs.com/{TICKER}/",
     "html_table", None),

    # GMO
    ("gmo",
     "https://www.gmo.com/americas/product-index/etfs/{ticker_lower}/",
     "html_table", None),

    # Frontier Asset
    ("frontier_asset",
     "https://www.frontierasset.com/etfs/{ticker_lower}",
     "html_table", None),

    # Themes (as a sponsor, not subadviser)
    ("themes",
     "https://themesetfs.com/themes/{TICKER}",
     "html_table", None),

    # Inspire
    ("inspire",
     "https://www.inspireetf.com/{ticker_lower}/",
     "html_table", None),

    # Unaffiliated OneAscent
    ("unaffiliated_oneascent",
     "https://oneascent.com/etf/{TICKER}/",
     "html_table", None),

    # Lazard
    ("lazard",
     "https://lazardfunds.com/etf/{ticker_lower}",
     "html_table", None),

    # Unaffiliated Tradr
    ("unaffiliated_tradr",
     "https://tradretfs.com/etfs/{TICKER}/",
     "html_table", None),

    # Tidal white-label host
    ("tidal",
     "https://tidaletfservices.com/etfs/{TICKER}/",
     "html_table", None),

    # Exchange Traded Concepts
    ("exchange_traded_concepts",
     "https://www.etfarchitect.com/{TICKER}/",
     "html_table", None),

    # Bitwise (tidal sub-adviser, but often works via bitwiseinvestments.com)
    ("bitwise",
     "https://bitwiseinvestments.com/crypto-market-index-etfs/{ticker_lower}",
     "html_table", None),

    # YieldMax (tidal sub-adviser)
    ("yieldmax",
     "https://www.yieldmaxetfs.com/our-etfs/{TICKER}/",
     "html_table", None),

    # Roundhill
    ("roundhill",
     "https://www.roundhillinvestments.com/etfs/{TICKER}/",
     "html_table", None),

    # Simplify
    ("simplify",
     "https://www.simplify.us/etfs/{ticker_lower}",
     "html_table", None),

    # Strive
    ("strive",
     "https://www.strive.com/etfs/{ticker_lower}",
     "html_table", None),

    # Themes ETFs alternate
    ("themes",
     "https://themesetfs.com/etfs/{TICKER}/",
     "html_table", None),
]


def main():
    # Load probe tickers
    rt_path = PHASE0 / "recovery_targets.json"
    with open(rt_path) as f:
        targets = json.load(f)
    test_tickers: dict[str, list] = targets["test_tickers"]
    excluded_by_sponsor = targets.get("excluded_funds_by_sponsor", {})

    # Subadviser tickers not in test_tickers — pull from excluded funds
    SUBADVISER_PROBES = {
        "bitwise": ["BITB", "BITW", "AETH"],
        "yieldmax": ["MSTY", "NVDY", "TSLY", "CONY"],
        "roundhill": ["MAGS", "NERD", "NVDU", "SPKY"],
        "simplify": ["SVOL", "PINK", "CAOS", "QQQI"],
        "strive": ["STRV", "DRLL"],
    }

    # De-dup by (sponsor, template)
    seen: set[tuple] = set()
    dedup: list[tuple] = []
    for item in CURATED:
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            dedup.append(item)

    print(f"[curated] {len(dedup)} patterns across {len(set(x[0] for x in dedup))} sponsors")

    session = cc_requests.Session(impersonate=IMPERSONATE)
    confirmed_by_sponsor: dict[str, dict] = {}
    all_records: list[dict] = []

    # Load existing confirmed
    confirmed_path = PHASE0 / "sponsor_endpoints_v2_candidates.jsonl"
    existing: set[str] = set()
    if confirmed_path.exists():
        with open(confirmed_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    existing.add(d["sponsor"])
                except Exception:
                    pass
    print(f"[curated] existing confirmed: {sorted(existing)}")

    for sponsor, tmpl, fmt, ref_tmpl in dedup:
        if sponsor in confirmed_by_sponsor:
            continue  # already confirmed
        if sponsor in SUBADVISER_PROBES:
            ts = SUBADVISER_PROBES[sponsor]
        else:
            ts = [t for _, t, _ in test_tickers.get(sponsor, [])][:2]
            if len(ts) < 2:
                ts = list({r["ticker"] for r in excluded_by_sponsor.get(sponsor, [])})[:4]
        if len(ts) < 2:
            print(f"  {sponsor:28s} SKIP (no probe tickers)")
            continue

        t1, t2 = ts[0], ts[1]
        url1 = expand(tmpl, t1)
        url2 = expand(tmpl, t2)
        ref1 = expand(ref_tmpl, t1) if ref_tmpl else None
        ref2 = expand(ref_tmpl, t2) if ref_tmpl else None

        # Probe t1
        s1, f1, b1, ct1 = fetch_curl_cffi(session, url1, referer=ref1)
        fmt1 = guess_format_from_url(f1, ct1, b1) if s1 == 200 else fmt
        ok1, r1, i1 = (False, f"http_{s1}", {})
        if s1 == 200:
            ok1, r1, i1 = verify_body(b1, fmt1)

        rec1 = {"sponsor": sponsor, "tier": "curated", "ticker": t1,
                "url": url1, "status": s1, "ok": bool(ok1), "reason": r1,
                "format": fmt1, "bytes": len(b1)}
        all_records.append(rec1)

        if not ok1:
            print(f"  {sponsor:28s} {t1:6s} {fmt:10s} {s1} {r1} — {url1[:80]}")
            continue

        # Probe t2
        s2, f2, b2, ct2 = fetch_curl_cffi(session, url2, referer=ref2)
        fmt2 = guess_format_from_url(f2, ct2, b2) if s2 == 200 else fmt1
        ok2, r2, i2 = (False, f"http_{s2}", {})
        if s2 == 200:
            ok2, r2, i2 = verify_body(b2, fmt2)

        rec2 = {"sponsor": sponsor, "tier": "curated", "ticker": t2,
                "url": url2, "status": s2, "ok": bool(ok2), "reason": r2,
                "format": fmt2, "bytes": len(b2)}
        all_records.append(rec2)

        if ok2:
            print(f"  {sponsor:28s} CONFIRMED  {fmt1:10s} {tmpl[:80]}")
            confirmed_by_sponsor[sponsor] = {
                "sponsor": sponsor, "event": "confirmed_pattern",
                "label": f"curated:{sponsor}", "template": tmpl,
                "format": fmt1, "verified_tickers": [t1, t2],
                "referer": ref_tmpl,
            }
        else:
            print(f"  {sponsor:28s} {t2:6s} half-miss  {s2} {r2}")

    # Append to probes + confirmed
    with open(PHASE0 / "sponsor_probes_v2.jsonl", "a") as f:
        for r in all_records:
            f.write(json.dumps(r) + "\n")

    # Only add sponsors not already confirmed
    new_confirmed = {s: c for s, c in confirmed_by_sponsor.items() if s not in existing}
    with open(confirmed_path, "a") as f:
        for s, c in new_confirmed.items():
            f.write(json.dumps({"sponsor": s, "patterns": [c], "origin": "curated"}) + "\n")

    print(f"\n[curated] Confirmed {len(new_confirmed)} NEW sponsors via curated patterns")
    for s in sorted(new_confirmed):
        print(f"  {s}")
    print(f"[curated] Total confirmed now: {len(existing) + len(new_confirmed)}")


if __name__ == "__main__":
    main()
