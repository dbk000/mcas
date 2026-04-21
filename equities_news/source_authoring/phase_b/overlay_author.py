"""overlay_author — heuristic helpers for per-symbol overlay fill.

A Phase B agent session calls these helpers as starting points; the agent
then verifies + overrides based on real-world checks (search, HTTP HEAD,
LinkedIn resolution).

The helpers here NEVER make network calls. They return best-guess overlay
values from the symbol metadata alone. All network verification belongs in
verification/format_validator.py, run separately.
"""
from __future__ import annotations

import re
from typing import Optional


def guess_linkedin_slug(company: str) -> Optional[str]:
    """Convert 'NVIDIA CORP' -> 'nvidia' (dropping corporate suffixes).

    Very conservative — returns None when uncertain. Always verify the URL
    resolves before committing to overlay.
    """
    if not company:
        return None
    s = company.strip().lower()
    # Drop suffixes.
    suffixes = [
        r"\s+corp$", r"\s+corporation$", r"\s+inc\.?$", r"\s+incorporated$",
        r"\s+plc$", r"\s+ltd\.?$", r"\s+limited$", r"\s+llc$",
        r"\s+co\.?$", r"\s+company$", r"\s+ag$", r"\s+sa$", r"\s+s\.a\.$",
        r"\s+spa$", r"\s+s\.p\.a\.?$", r"\s+nv$", r"\s+n\.v\.?$",
        r"\s+bv$", r"\s+b\.v\.?$", r"\s+gmbh$", r"\s+ab$", r"\s+a/s$",
        r"\s+oyj$", r"\s+asa$", r"\s+sas$", r"\s+sarl$", r"\s+plc\.?$",
        r"\s+holdings$", r"\s+holding$", r"\s+group$",
    ]
    for pat in suffixes:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-z0-9]+", "-", s.strip())
    s = s.strip("-")
    if len(s) < 2:
        return None
    return s


def linkedin_url_from_slug(slug: Optional[str]) -> Optional[str]:
    if not slug:
        return None
    return f"https://www.linkedin.com/company/{slug}"


_TRANSCRIPT_ENUM = {"quartr", "seekingalpha", "motley_fool", "company_ir", "none"}


def default_earnings_transcript_provider(
    exchange: str,
    country_code: str,
    cap_tier: str,
) -> str:
    """Pick a reasonable default per §B.4.

    Heuristics per the Phase 1.b spec:
      - US-listed with market cap > $500M → seekingalpha
      - mid/large-cap globals (non-US)    → quartr
      - names with own transcript archive → company_ir  (agent must override)
      - otherwise                         → none
    Cap tier enum in Bible: Mega / Large / Mid / Small / Micro.
    """
    ex = (exchange or "").upper()
    us_listed = any(k in ex for k in ("NASDAQ", "NYSE", "NYSE MKT", "CBOE BZX", "NNQS"))
    tier = (cap_tier or "").lower()
    large_enough = tier in ("mega", "large", "mid")

    if us_listed and large_enough:
        return "seekingalpha"
    if not us_listed and large_enough:
        return "quartr"
    if tier in ("small", "micro"):
        return "none"
    return "none"


# --------------------------------------------------------------------------- #
# Local filings URL templates (per-jurisdiction pattern)                       #
# --------------------------------------------------------------------------- #

def us_edgar_url(cik: str) -> str:
    """SEC EDGAR per-filer URL. CIK is numeric, e.g. '1045810' for NVDA."""
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40"
    )


def tdnet_issuer_url(securities_code: str) -> str:
    """TDnet per-filer URL. JP securities code is 4-digit, e.g. '7203' for Toyota."""
    return f"https://www.release.tdnet.info/inbs/I_list_001_{securities_code}.html"


def asx_issuer_url(ticker: str) -> str:
    return f"https://www2.asx.com.au/markets/company/{ticker.upper()}"


def lse_issuer_url(ticker: str) -> str:
    return f"https://www.londonstockexchange.com/stock/{ticker.upper()}/company-page"


def sgx_issuer_url(stock_code: str) -> str:
    return f"https://www.sgx.com/securities/equities/{stock_code}"


def hkex_issuer_url(stock_code: str) -> str:
    return (
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?"
        f"tid=2&s1={stock_code}&btnSearch=%3E"
    )


_LOCAL_FILINGS_BUILDERS = {
    "US": us_edgar_url,     # requires CIK
    "JP": tdnet_issuer_url, # requires securities code
    "AU": asx_issuer_url,
    "GB": lse_issuer_url,
    "SG": sgx_issuer_url,
    "HK": hkex_issuer_url,
}


def local_filings_builder_for_country(cc: str):
    return _LOCAL_FILINGS_BUILDERS.get(cc.upper())


def validate_transcript_enum(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value not in _TRANSCRIPT_ENUM:
        raise ValueError(f"invalid transcript provider {value!r}")
    return value
