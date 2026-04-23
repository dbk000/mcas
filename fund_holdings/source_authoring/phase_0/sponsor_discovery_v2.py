"""Tier 1 + 2 sponsor endpoint discovery (Phase 0 Recovery).

For each of the 59 excluded sponsors, try an expanded pattern catalogue with
curl_cffi Chrome-124 TLS impersonation. Patterns are applied to each sponsor's
known hostname(s) plus alternate subdomain probes (cdn/static/assets/api).

Two probe tickers per sponsor; a pattern is declared verified only if both
probe tickers content-verify per §7.2.

Output:
  - sponsor_probes_v2.jsonl : one line per (sponsor, pattern, ticker) attempt
  - sponsor_endpoints_v2_candidates.jsonl : deduplicated verified patterns
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

# curl_cffi with Chrome-124 TLS impersonation defeats most Akamai/Cloudflare/F5 TLS fingerprint blocks
from curl_cffi import requests as cc_requests

# Reuse existing content-verification helpers
sys.path.insert(0, str(Path(__file__).parent))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok, MIN_BYTES_FOR_FORMAT  # type: ignore

PHASE0 = Path(__file__).parent
LOG_DIR = PHASE0 / "logs" / "sponsor_discovery"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Force line-buffered stdout so progress is visible in real time
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

IMPERSONATE = "chrome124"
FETCH_TIMEOUT = 6.0  # hard cap — unresponsive host gets skipped quickly
MAX_BYTES = 25 * 1024 * 1024


# -----------------------------------------------------------------------------
# Sponsor base URL catalogue (hostnames + previously-attempted templates)
# Keys are sponsor_slug matching bible sheet / excluded_sponsors.yaml
# Each entry: base hostnames (primary + alternates) + known patterns from v1 probes.
# -----------------------------------------------------------------------------
SPONSOR_HOSTS: dict[str, dict] = {
    "invesco": {
        "hosts": ["www.invesco.com", "invesco.com"],
        "base_paths": ["/us/financial-products/etfs/product-detail",
                       "/us/financial-products/etfs", "/us/etfs"],
        "holdings_api": [
            "https://www.invesco.com/us/financial-products/etfs/holdings?audienceType=Investor&ticker={TICKER}&action=download&format=CSV",
            "https://www.invesco.com/us-rest/invesco-etf-holdings-csv?ticker={TICKER}",
        ],
        "known_fmt_hints": {"csv": 0.7},
    },
    "proshares": {
        "hosts": ["www.proshares.com", "services.proshares.com", "proshares.com"],
        "base_paths": ["/our-etfs/strategic/{ticker_lower}", "/our-etfs/leveraged-and-inverse/{ticker_lower}",
                       "/our-etfs/us-equity/{ticker_lower}", "/etfs/{ticker_lower}"],
        "holdings_api": [
            "https://www.proshares.com/our-etfs/download-file?ticker={TICKER}&downloadType=holdings",
            "https://services.proshares.com/proshares-portal-web-app/rest/v1/service/portal/fund/{TICKER}/holdings/CSV",
            "https://www.proshares.com/globalassets/proshares/resources/holdings/{ticker_lower}-holdings.csv",
        ],
    },
    "fidelity": {
        "hosts": ["fundresearch.fidelity.com", "www.fidelity.com", "api.fidelity.com"],
        "base_paths": ["/mutual-funds/composition/{TICKER}", "/etfs/{TICKER}", "/etfs/{ticker_lower}"],
        "holdings_api": [
            "https://fundresearch.fidelity.com/mutual-funds/download-csv/composition/{TICKER}",
            "https://fundresearch.fidelity.com/etfs/holdings/{TICKER}",
            "https://fundresearch.fidelity.com/etfs/portfolio/{TICKER}",
        ],
    },
    "jpmorgan": {
        "hosts": ["am.jpmorgan.com", "am-uk.jpmorgan.com", "www.jpmorgan.com"],
        "base_paths": ["/us/en/asset-management/adv/products/{ticker_lower}-etf-shares",
                       "/us/en/asset-management/adv/etfs/{TICKER}",
                       "/us/en/asset-management/adv/funds/{TICKER}"],
        "holdings_api": [
            "https://am.jpmorgan.com/services/etf-holdings?ticker={TICKER}",
            "https://am.jpmorgan.com/us/en/asset-management/adv/products/{ticker_lower}-etf-shares/holdings",
        ],
    },
    "dimensional": {
        "hosts": ["us.dimensional.com", "www.dimensional.com"],
        "base_paths": ["/funds/{ticker_lower}", "/etf/{ticker_lower}", "/funds/{ticker_lower}-etf"],
        "holdings_api": [
            "https://us.dimensional.com/api/funds/{TICKER}/holdings",
            "https://us.dimensional.com/funds/{ticker_lower}/holdings",
        ],
    },
    "vaneck": {
        "hosts": ["www.vaneck.com", "vaneck.com"],
        "base_paths": ["/us/en/investments/{ticker_lower}", "/etf/{ticker_lower}",
                       "/us/en/investments/morningstar-wide-moat-etf-{ticker_lower}",
                       "/us/en/investments/{ticker_lower}/holdings"],
        "holdings_api": [
            "https://www.vaneck.com/us/en/investments/{ticker_lower}/holdings/",
            "https://www.vaneck.com/etf/etf-holdings-csv/?fund={TICKER}",
        ],
    },
    "schwab": {
        "hosts": ["www.schwabassetmanagement.com", "schwabassetmanagement.com"],
        "base_paths": ["/products/{ticker_lower}", "/products/{TICKER}"],
        "holdings_api": [
            "https://www.schwabassetmanagement.com/allholdings/{TICKER}",
        ],
    },
    "wisdomtree": {
        "hosts": ["www.wisdomtree.com", "wisdomtree.com"],
        "base_paths": ["/investments/etfs/equity/{ticker_lower}", "/investments/etfs/{ticker_lower}",
                       "/us/en/etfs/{ticker_lower}", "/investments/etfs/fixed-income/{ticker_lower}"],
        "holdings_api": [
            "https://www.wisdomtree.com/-/media/us-media-files/documents/resource-library/fund-documents/holdings/{TICKER}-holdings.csv",
            "https://www.wisdomtree.com/api/etfs/holdings?fund={TICKER}",
        ],
    },
    "northern_trust": {
        "hosts": ["flexshares.com", "www.flexshares.com"],
        "base_paths": ["/funds/{ticker_lower}", "/fund/{ticker_lower}", "/en-us/funds/{ticker_lower}",
                       "/en-us/fund/{ticker_lower}", "/products/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "pacer": {
        "hosts": ["www.paceretfs.com", "paceretfs.com"],
        "base_paths": ["/products/{ticker_lower}", "/products/etf/{ticker_lower}", "/etfs/{ticker_lower}"],
        "holdings_api": [
            "https://www.paceretfs.com/products/{ticker_lower}/holdings",
        ],
    },
    "t_rowe_price": {
        "hosts": ["www.troweprice.com", "troweprice.com"],
        "base_paths": ["/financial-intermediary/us/en/investments/etfs/products/{ticker_lower}.html",
                       "/personal-investing/tools/fund-research/{TICKER}"],
        "holdings_api": [],
    },
    "john_hancock": {
        "hosts": ["www.jhinvestments.com", "jhinvestments.com"],
        "base_paths": ["/etf/{ticker_lower}", "/etf/multifactor-{ticker_lower}", "/etfs/{ticker_lower}"],
        "holdings_api": [],
    },
    "pimco": {
        "hosts": ["www.pimco.com", "pimco.com"],
        "base_paths": ["/en-us/investments/etf/{ticker_lower}", "/en-us/etf/{ticker_lower}",
                       "/en-us/investments/etf/{ticker_lower}-active-etf"],
        "holdings_api": [],
    },
    "franklin_templeton": {
        "hosts": ["www.franklintempleton.com", "franklintempleton.com"],
        "base_paths": ["/investments/options/exchange-traded-funds/products/{ticker_lower}-etf",
                       "/investments/options/exchange-traded-funds/products/{ticker_lower}",
                       "/us/en/fund/etfs/{ticker_lower}"],
        "holdings_api": [
            "https://www.franklintempleton.com/api/funds/etfs/{TICKER}",
            "https://www.franklintempleton.com/fundstore-api/v1/etfs/{TICKER}/holdings",
        ],
    },
    "innovator": {
        "hosts": ["www.innovatoretfs.com", "innovatoretfs.com"],
        "base_paths": ["/etf/?ticker={TICKER}", "/etf/{ticker_lower}", "/etfs/{ticker_lower}"],
        "holdings_api": [
            "https://www.innovatoretfs.com/api/etf/holdings?ticker={TICKER}",
        ],
    },
    "calamos": {
        "hosts": ["www.calamos.com", "calamos.com"],
        "base_paths": ["/funds/etfs/{ticker_lower}", "/funds/{ticker_lower}", "/etfs/{ticker_lower}"],
        "holdings_api": [],
    },
    "nuveen": {
        "hosts": ["www.nuveen.com", "nuveen.com"],
        "base_paths": ["/en-us/exchange-traded-funds/{TICKER}", "/en-us/etf/{ticker_lower}",
                       "/us/en/exchange-traded-funds/{TICKER}", "/en-us/etf/{TICKER}"],
        "holdings_api": [],
    },
    "janus_henderson": {
        "hosts": ["www.janushenderson.com", "janushenderson.com"],
        "base_paths": ["/en-us/investor/product/{ticker_lower}",
                       "/en-us/investor/product/{ticker_lower}-etf",
                       "/us/en/advisor/etf/{ticker_lower}",
                       "/en-us/advisor/fund/{ticker_lower}"],
        "holdings_api": [],
    },
    "capital_group": {
        "hosts": ["www.capitalgroup.com", "capitalgroup.com"],
        "base_paths": ["/advisor/investments/etf/{ticker_lower}",
                       "/individual/investments/fund/etf/{ticker_lower}",
                       "/advisor/investments/fund/{ticker_lower}",
                       "/advisor/investments/etf/{TICKER}"],
        "holdings_api": [],
    },
    "blackrock": {
        # Non-iShares BlackRock funds: BINC, DYNF, etc.
        "hosts": ["www.blackrock.com", "blackrock.com"],
        "base_paths": ["/us/individual/products/{ticker_lower}",
                       "/us/individual/products/etfs/{ticker_lower}",
                       "/us/individual/products/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "alliancebernstein": {
        "hosts": ["www.abfunds.com", "www.alliancebernstein.com", "abfunds.com"],
        "base_paths": ["/link/en/etf/{ticker_lower}-overview",
                       "/en/us/etfs/{ticker_lower}",
                       "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "alps": {
        "hosts": ["www.alpsfunds.com", "alpsfunds.com"],
        "base_paths": ["/etfs/{ticker_lower}", "/etf/{ticker_lower}", "/fund/{ticker_lower}"],
        "holdings_api": [],
    },
    "victory_capital": {
        "hosts": ["www.vcm.com", "vcm.com"],
        "base_paths": ["/etfs/{ticker_lower}", "/etfs/{TICKER}", "/us/investments/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "harbor": {
        "hosts": ["www.harborcapital.com", "harborcapital.com"],
        "base_paths": ["/etfs/{ticker_lower}", "/etfs/{TICKER}", "/etfs/{ticker_lower}/holdings"],
        "holdings_api": [],
    },
    "columbia": {
        "hosts": ["www.columbiathreadneedleus.com", "columbiathreadneedleus.com"],
        "base_paths": ["/etfs/{TICKER}", "/etfs/{ticker_lower}", "/us/etfs/{TICKER}"],
        "holdings_api": [],
    },
    "pgim": {
        "hosts": ["www.pgim.com", "pgim.com"],
        "base_paths": ["/investments/etf/{ticker_lower}", "/etf/{ticker_lower}", "/us/en/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "principal": {
        "hosts": ["www.principalam.com", "www.principal.com", "principalam.com"],
        "base_paths": ["/us/etf/{ticker_lower}", "/etf/{ticker_lower}", "/us/etfs/{ticker_lower}"],
        "holdings_api": [],
    },
    "neuberger_berman": {
        "hosts": ["www.nb.com", "nb.com"],
        "base_paths": ["/en/global/etf/{ticker_lower}", "/en/us/etf/{ticker_lower}",
                       "/us/en/etf/{ticker_lower}", "/en/global/fund/{ticker_lower}"],
        "holdings_api": [],
    },
    "bondbloxx": {
        "hosts": ["bondbloxx.com", "www.bondbloxx.com"],
        "base_paths": ["/etfs/{ticker_lower}", "/product/{ticker_lower}", "/fund/{ticker_lower}",
                       "/etf/{ticker_lower}", "/products/{ticker_lower}"],
        "holdings_api": [],
    },
    "xtrackers": {
        "hosts": ["etfus.dws.com", "etf.dws.com", "www.xtrackers.com", "xtrackers.com"],
        "base_paths": ["/en-us/{TICKER}", "/en-us/etf/{TICKER}", "/us/{TICKER}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "tidal": {
        "hosts": ["tidaletfservices.com", "www.tidaletfservices.com", "tidaltrust.com"],
        "base_paths": ["/etfs/{TICKER}", "/etfs/{ticker_lower}", "/funds/{TICKER}"],
        "holdings_api": [],
    },
    "alpha_architect": {
        "hosts": ["etfsite.alphaarchitect.com", "www.alphaarchitect.com", "alphaarchitect.com"],
        "base_paths": ["/{ticker_lower}/", "/funds/{ticker_lower}", "/etfs/{ticker_lower}"],
        "holdings_api": [],
    },
    "exchange_traded_concepts": {
        "hosts": ["www.etfarchitect.com", "etfarchitect.com", "www.etcfunds.com", "etcfunds.com"],
        "base_paths": ["/{TICKER}/", "/etfs/{TICKER}", "/funds/{TICKER}"],
        "holdings_api": [],
    },
    "bny_mellon": {
        "hosts": ["im.bnymellon.com", "www.bnymellonwealth.com"],
        "base_paths": ["/us/en/intermediary/product-detail.etfs-{ticker_lower}.html",
                       "/us/en/intermediary/etf/{ticker_lower}",
                       "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "hartford": {
        "hosts": ["www.hartfordfunds.com", "hartfordfunds.com"],
        "base_paths": ["/etfs/{ticker_lower}-overview.html",
                       "/etfs/{ticker_lower}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "touchstone": {
        "hosts": ["www.touchstoneinvestments.com", "touchstoneinvestments.com"],
        "base_paths": ["/etfs/{TICKER}", "/etfs/{ticker_lower}", "/etf/{TICKER}"],
        "holdings_api": [],
    },
    "doubleline": {
        "hosts": ["www.doubleline.com", "doubleline.com"],
        "base_paths": ["/etfs/{ticker_lower}/", "/etfs/{ticker_lower}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "strategy_shares": {
        "hosts": ["www.strategysharesetfs.com", "strategysharesetfs.com"],
        "base_paths": ["/etfs/{ticker_lower}/", "/etfs/{ticker_lower}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "unaffiliated_tradr": {
        "hosts": ["tradretfs.com", "www.tradretfs.com"],
        "base_paths": ["/etfs/{TICKER}/", "/etfs/{TICKER}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "frontier_asset": {
        "hosts": ["www.frontierasset.com", "frontierasset.com"],
        "base_paths": ["/etfs/{ticker_lower}", "/etf/{ticker_lower}", "/funds/{ticker_lower}"],
        "holdings_api": [],
    },
    "gmo": {
        "hosts": ["www.gmo.com", "gmo.com"],
        "base_paths": ["/americas/product-index/etfs/{ticker_lower}/",
                       "/americas/product-index/etfs/{ticker_lower}",
                       "/americas/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "macquarie": {
        "hosts": ["www.delawarefunds.com", "delawarefunds.com", "www.macquarie.com"],
        "base_paths": ["/etfs/{ticker_lower}", "/etf/{ticker_lower}", "/us/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "putnam": {
        "hosts": ["www.putnam.com", "putnam.com"],
        "base_paths": ["/individual/etf/{TICKER}", "/individual/etfs/{TICKER}",
                       "/us/en/etf/{TICKER}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "sprott": {
        "hosts": ["sprottetfs.com", "www.sprottetfs.com"],
        "base_paths": ["/sprott-physical-miner-etfs/{ticker_lower}-fund/",
                       "/etfs/{ticker_lower}/", "/{ticker_lower}/"],
        "holdings_api": [],
    },
    "virtus": {
        "hosts": ["www.virtusetfs.com", "virtusetfs.com"],
        "base_paths": ["/funds/{TICKER}", "/etfs/{TICKER}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "goldman_sachs": {
        "hosts": ["www.gsam.com", "gsam.com"],
        "base_paths": ["/content/gsam/us/en/advisors/fund-center/etf-fund-finder.html?fundId={TICKER}",
                       "/content/gsam/us/en/individual/products/etf/{ticker_lower}",
                       "/us/en/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "abrdn": {
        "hosts": ["www.abrdn.com", "abrdn.com"],
        "base_paths": ["/en-us/us/etf/fund-centre/etf-fund-centre/{ticker_lower}",
                       "/en-us/investor/etfs/{ticker_lower}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "alger": {
        "hosts": ["www.alger.com", "alger.com"],
        "base_paths": ["/etf/{ticker_lower}", "/etfs/{ticker_lower}", "/funds/{ticker_lower}"],
        "holdings_api": [],
    },
    "mfs": {
        "hosts": ["www.mfs.com", "mfs.com"],
        "base_paths": ["/en-us/investment-professional/products/mfs-etfs.html#{TICKER}",
                       "/en-us/investor/etf/{TICKER}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "russell_investments": {
        "hosts": ["russellinvestments.com", "www.russellinvestments.com"],
        "base_paths": ["/us/etfs/{TICKER}", "/us/etf/{ticker_lower}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "lazard": {
        "hosts": ["lazardfunds.com", "www.lazardfunds.com", "www.lazardassetmanagement.com"],
        "base_paths": ["/etf/{ticker_lower}", "/us/en-us/etf/{ticker_lower}", "/funds/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "tcw": {
        "hosts": ["www.tcw.com", "tcw.com"],
        "base_paths": ["/products/etf/{ticker_lower}", "/etfs/{ticker_lower}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "tortoise": {
        "hosts": ["tortoiseetfs.com", "www.tortoiseetfs.com", "tortoise.com"],
        "base_paths": ["/etfs/{ticker_lower}/", "/etfs/{ticker_lower}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "tema": {
        "hosts": ["www.temaetfs.com", "temaetfs.com"],
        "base_paths": ["/{TICKER}/", "/etfs/{TICKER}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "unaffiliated_oneascent": {
        "hosts": ["oneascent.com", "www.oneascent.com"],
        "base_paths": ["/etf/{TICKER}/", "/etfs/{ticker_lower}", "/fund/{ticker_lower}"],
        "holdings_api": [],
    },
    "themes": {
        "hosts": ["themesetfs.com", "www.themesetfs.com"],
        "base_paths": ["/themes/{TICKER}", "/etfs/{TICKER}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "inspire": {
        "hosts": ["www.inspireetf.com", "inspireetf.com", "www.inspireinvesting.com"],
        "base_paths": ["/{ticker_lower}/", "/etfs/{ticker_lower}", "/etf/{ticker_lower}"],
        "holdings_api": [],
    },
    "allspring": {
        "hosts": ["www.allspringglobal.com", "allspringglobal.com"],
        "base_paths": ["/api/etfs/{TICKER}", "/etfs/{TICKER}", "/etf/{ticker_lower}",
                       "/us/individual/etfs/{ticker_lower}"],
        "holdings_api": [
            "https://www.allspringglobal.com/api/etfs/{TICKER}",
            "https://www.allspringglobal.com/api/etfs/{TICKER}/holdings",
        ],
    },
    "other_sponsors": {
        # Aggregate — we can't meaningfully probe without sub-sponsor info; skip primary Tier 1.
        # Handled by Tier 7 subadviser decomposition in Phase 2.
        "hosts": [],
        "base_paths": [],
        "holdings_api": [],
    },
}


# Generic extra patterns applied to EVERY sponsor host
GENERIC_PATH_PATTERNS = [
    "/etf/{TICKER}", "/etfs/{TICKER}", "/etf/{ticker_lower}", "/etfs/{ticker_lower}",
    "/fund/{TICKER}", "/funds/{TICKER}", "/fund/{ticker_lower}", "/funds/{ticker_lower}",
    "/product/{TICKER}", "/products/{TICKER}", "/product/{ticker_lower}", "/products/{ticker_lower}",
    "/etf/{ticker_lower}/holdings", "/etfs/{ticker_lower}/holdings",
    "/etf/{ticker_lower}/holdings.csv", "/etfs/{ticker_lower}/holdings.csv",
    "/etf/{ticker_lower}/holdings.xlsx",
    "/api/etfs/{TICKER}", "/api/etf/{TICKER}", "/api/funds/{TICKER}",
    "/_api/etfs/{TICKER}/holdings",
    "/api/etf/holdings?ticker={TICKER}",
]


def expand(template: str, ticker: str) -> str:
    return (
        template
        .replace("{TICKER}", ticker.upper())
        .replace("{ticker_upper}", ticker.upper())
        .replace("{ticker_lower}", ticker.lower())
        .replace("{ticker_lower_clean}", ticker.lower().replace(".", "").replace("-", ""))
    )


def guess_format_from_url(url: str, ct: str, body: bytes) -> str:
    """Best-effort format inference for a URL we hit without a declared format."""
    u = url.lower()
    ct_low = (ct or "").lower()
    if u.endswith(".csv") or "csv" in ct_low:
        return "csv"
    if u.endswith(".xlsx") or "xlsx" in ct_low or "spreadsheet" in ct_low:
        return "xlsx"
    if "json" in ct_low or body.lstrip().startswith((b"{", b"[")):
        return "json"
    if "html" in ct_low or b"<html" in body[:500].lower() or b"<!doctype" in body[:500].lower():
        return "html_table"
    return "html_table"  # safest default


def verify_body(body: bytes, fmt: str) -> tuple[bool, str, dict]:
    """Run the format-specific verifier from content_verify."""
    if len(body) < MIN_BYTES_FOR_FORMAT.get(fmt, 100):
        return False, f"body_too_small:{len(body)}b", {}
    try:
        if fmt == "csv":
            return _csv_ok(body)
        elif fmt == "xlsx":
            return _xlsx_ok(body)
        elif fmt == "json":
            return _json_ok(body)
        elif fmt == "html_table":
            return _html_ok(body)
        else:
            return False, f"unsupported_format:{fmt}", {}
    except Exception as e:
        return False, f"verify_exception:{type(e).__name__}", {"err": str(e)[:150]}


def fetch_curl_cffi(session, url: str, referer: str = None) -> tuple[int, str, bytes, str]:
    """Fetch with curl_cffi Chrome-124 impersonation. Returns (status, final_url, body, content_type)."""
    try:
        headers = {}
        if referer:
            headers["Referer"] = referer
        r = session.get(
            url,
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
            headers=headers,
            max_recv_speed=0,  # no cap
        )
        body = r.content[:MAX_BYTES] if r.content else b""
        ct = r.headers.get("content-type", "") if r.headers else ""
        return r.status_code, str(r.url), body, ct
    except Exception as e:
        return 0, url, b"", f"{type(e).__name__}: {e}"


def build_patterns_for_sponsor(slug: str, cfg: dict) -> list[tuple[str, str, str]]:
    """Expand a sponsor config into a list of (label, url_template, guessed_format) entries."""
    patterns: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    # 1) Direct holdings API templates (highest priority — known payload format)
    for tmpl in cfg.get("holdings_api", []):
        if tmpl in seen:
            continue
        seen.add(tmpl)
        # Infer format from URL
        t_low = tmpl.lower()
        if ".csv" in t_low or "csv" in t_low:
            fmt = "csv"
        elif ".xlsx" in t_low or "xlsx" in t_low:
            fmt = "xlsx"
        elif "/api/" in t_low or "services." in t_low or ".json" in t_low:
            fmt = "json"
        else:
            fmt = "html_table"
        patterns.append((f"{slug}:api", tmpl, fmt))

    # 2) Explicit sponsor base paths
    for host in cfg.get("hosts", []):
        for bp in cfg.get("base_paths", []):
            tmpl = f"https://{host}{bp if bp.startswith('/') else '/' + bp}"
            if tmpl not in seen:
                seen.add(tmpl)
                patterns.append((f"{slug}:base:{host}", tmpl, "html_table"))

    # 3) Generic URL patterns applied to the sponsor's hostnames
    for host in cfg.get("hosts", [])[:2]:  # only primary + first alt to cap count
        for path in GENERIC_PATH_PATTERNS:
            tmpl = f"https://{host}{path}"
            if tmpl not in seen:
                seen.add(tmpl)
                fmt = "csv" if ".csv" in path else ("xlsx" if ".xlsx" in path else ("json" if "/api/" in path else "html_table"))
                patterns.append((f"{slug}:generic:{host}", tmpl, fmt))

    return patterns


def probe_sponsor(slug: str, cfg: dict, probe_tickers: list[str]) -> list[dict]:
    """Probe one sponsor across all its patterns. Return JSONL-serializable records."""
    records: list[dict] = []
    patterns = build_patterns_for_sponsor(slug, cfg)
    if not patterns:
        return [{"sponsor": slug, "event": "no_patterns", "note": "skipped (no hosts/paths/api)"}]
    if len(probe_tickers) < 2:
        # Can't content-verify across 2 tickers — mark as limited
        return [{"sponsor": slug, "event": "insufficient_probe_tickers", "tickers": probe_tickers}]

    # One curl_cffi session per sponsor — lets it persist cookies & TLS state
    session = cc_requests.Session(impersonate=IMPERSONATE)

    # First probe: one ticker per pattern. Early-exit once we have a first-pass hit
    # — then second-probe it and return on 2/2 confirmation. Saves ~10x time.
    first_ticker = probe_tickers[0]
    second_ticker = probe_tickers[1]

    for label, tmpl, fmt in patterns:
        url = expand(tmpl, first_ticker)
        t0 = time.monotonic()
        status, final_url, body, ct = fetch_curl_cffi(session, url)
        dt1 = time.monotonic() - t0
        detected_fmt = guess_format_from_url(final_url, ct, body) if status == 200 else fmt
        ok, reason, info = (False, f"http_{status}", {})
        if status == 200:
            ok, reason, info = verify_body(body, detected_fmt)
        rec = {
            "sponsor": slug, "label": label, "template": tmpl,
            "probe_ticker": first_ticker, "expanded_url": url,
            "final_url": final_url, "status": status, "content_type": ct[:80],
            "body_bytes": len(body), "declared_format": fmt,
            "detected_format": detected_fmt, "ok": bool(ok), "reason": reason,
            "info": info, "took_s": round(dt1, 2),
            "pass": "first",
        }
        records.append(rec)
        if not ok:
            continue
        # Second-ticker confirmation
        url2 = expand(tmpl, second_ticker)
        t0 = time.monotonic()
        status, final_url, body, ct = fetch_curl_cffi(session, url2)
        dt2 = time.monotonic() - t0
        detected_fmt2 = guess_format_from_url(final_url, ct, body) if status == 200 else detected_fmt
        ok2, reason2, info2 = (False, f"http_{status}", {})
        if status == 200:
            ok2, reason2, info2 = verify_body(body, detected_fmt2)
        records.append({
            "sponsor": slug, "label": label, "template": tmpl,
            "probe_ticker": second_ticker, "expanded_url": url2,
            "final_url": final_url, "status": status, "content_type": ct[:80],
            "body_bytes": len(body), "declared_format": fmt,
            "detected_format": detected_fmt2, "ok": bool(ok2), "reason": reason2,
            "info": info2, "took_s": round(dt2, 2),
            "pass": "second",
        })
        if ok2:
            # Confirmed — emit + stop probing this sponsor.
            records.append({
                "sponsor": slug, "event": "confirmed_pattern",
                "label": label, "template": tmpl, "format": detected_fmt,
                "verified_tickers": [first_ticker, second_ticker],
            })
            return records

    return records


def main():
    # Load sponsor → probe-ticker map
    rt_path = PHASE0 / "recovery_targets.json"
    if not rt_path.exists():
        print(f"Missing {rt_path}. Run the pre-step (bash that built recovery_targets.json).")
        sys.exit(1)
    with open(rt_path) as f:
        targets = json.load(f)
    test_tickers: dict[str, list] = targets["test_tickers"]

    # Build work list — slug → probe_tickers list (top 2)
    work = []
    for slug in SPONSOR_HOSTS:
        ts = [t for _, t, _ in test_tickers.get(slug, [])]
        if len(ts) < 2 and slug != "other_sponsors":
            # try fallback from excluded_funds
            excl = targets.get("excluded_funds_by_sponsor", {}).get(slug, [])
            ts = list({r["ticker"] for r in excl})[:4]
        work.append((slug, SPONSOR_HOSTS[slug], ts[:2]))

    out_path = PHASE0 / "sponsor_probes_v2.jsonl"
    confirmed_out = PHASE0 / "sponsor_endpoints_v2_candidates.jsonl"

    # Reset output files
    out_path.write_text("")
    confirmed_out.write_text("")

    print(f"[tier1] Probing {len(work)} sponsors with 8-way concurrency → {out_path}")
    print(f"[tier1] Target format: curl_cffi chrome124 TLS impersonation, timeout={FETCH_TIMEOUT}s")

    t_global = time.monotonic()
    total_records = 0
    confirmed_sponsors: list[str] = []
    probes_fh = open(out_path, "w", buffering=1)  # line-buffered
    confirmed_fh = open(confirmed_out, "w", buffering=1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(probe_sponsor, slug, cfg, tk): slug for slug, cfg, tk in work}
        for i, fut in enumerate(as_completed(futs), 1):
            slug = futs[fut]
            try:
                recs = fut.result()
                # Write immediately
                conf_this = [r for r in recs if r.get("event") == "confirmed_pattern"]
                for r in recs:
                    probes_fh.write(json.dumps(r) + "\n")
                total_records += len(recs)
                if conf_this:
                    confirmed_fh.write(json.dumps({
                        "sponsor": slug, "patterns": conf_this,
                    }) + "\n")
                    confirmed_sponsors.append(slug)
                n_attempts = sum(1 for r in recs if r.get("pass"))
                elapsed = time.monotonic() - t_global
                status = "CONFIRMED" if conf_this else "unconfirmed"
                print(f"[{i:2}/{len(work)}] {slug:28s} attempts={n_attempts:3d}  "
                      f"{status}  elapsed={elapsed:5.0f}s")
            except Exception as e:
                probes_fh.write(json.dumps({
                    "sponsor": slug, "event": "worker_exception",
                    "error": f"{type(e).__name__}: {e}",
                }) + "\n")
                print(f"[{i:2}/{len(work)}] {slug:28s} EXCEPTION {type(e).__name__}: {e}")

    probes_fh.close()
    confirmed_fh.close()

    print(f"\nWrote: {out_path}  ({total_records} records)")
    print(f"Wrote: {confirmed_out}  ({len(confirmed_sponsors)} sponsors confirmed)")
    print(f"\n=== SUMMARY ===")
    print(f"Sponsors tried: {len(work)}")
    print(f"Sponsors confirmed: {len(confirmed_sponsors)}")
    unconfirmed = [s for s, _, _ in work if s not in confirmed_sponsors]
    print(f"Unconfirmed → Tier 2/3/4: {len(unconfirmed)}")
    for s in unconfirmed:
        print(f"  {s}")

    total = time.monotonic() - t_global
    print(f"\nTotal wall-clock: {total:.1f}s")


if __name__ == "__main__":
    main()
