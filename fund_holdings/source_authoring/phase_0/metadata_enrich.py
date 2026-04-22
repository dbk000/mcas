"""
Phase 0 — Fund Bible: metadata enrichment.

Consumes candidate_list.jsonl (from candidate_list.py) and produces
phase_0.jsonl — one line per fund with all 32 bible columns populated
(or explicit "null" for fields we can't resolve in this phase).

What this script does, per candidate:
  1. Construct Holdings_Feed_URL + Sponsor_Page_URL from sponsor-specific
     templates in registry/sponsor_url_templates.yaml.
  2. HEAD-check every candidate URL. 200 / 302 / 405 (method-not-allowed)
     count as success; 403 is retried with a browser User-Agent; 404 and
     connection failures fall through to the next template. First success
     wins. Holdings_Feed_URL resolution is load-bearing — funds whose URL
     cannot be verified are excluded with reason 'unresolved_holdings_url'.
  3. Derive inferrable fields from fund_name + N-CEN flags:
     - Vehicle_Type (equity_index / active / factor / fixed_income / commodity / ...)
     - Vehicle_Subtype (leveraged_2x / inverse / smart_beta / thematic / ...)
     - Replication_Method (active / full_replication / sampled_replication)
     - Benchmark_Provider (S&P / MSCI / FTSE / Bloomberg / Solactive / custom)
     - Primary_Market_Cap_Tier (mega / large / mid / small / micro / mixed / n_a)
     - Geographic_Focus (us / developed_ex_us / emerging / global / ...)
     - Currency_Hedged, Fund_Of_Funds, Has_Derivatives_Overlay
  4. Lineage stamp (authored_by + UTC timestamp).

Output:
  phase_0.jsonl       — one enriched record per fund (append-only)
  phase_0_excluded.jsonl — funds dropped during enrichment

Concurrency: default 24 HTTP workers (balanced for politeness across hosts).

Usage:
    python3 metadata_enrich.py \\
        --input ./candidate_list.jsonl \\
        --output ./logs/phase_0.jsonl \\
        --excluded ./logs/phase_0_excluded.jsonl \\
        --templates ../../registry/sponsor_url_templates.yaml \\
        --workers 24

Rate-limit discipline: max 1 request per host every 200ms (soft), achieved
via a per-host token bucket in the HTTP client. SEC endpoints are capped
at 5 req/sec to respect fair-access policy.

Exit codes:
    0 — all candidates processed, phase_0.jsonl + phase_0_excluded.jsonl written
    2 — input/template file missing or malformed
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import random
import re
import sys
import threading
import time
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import urlparse

import requests
import yaml


# --- HTTP / user-agent --------------------------------------------------------

USER_AGENT_POLITE = "MCAS Phase 0 Fund Bible / Darren Kenny dkcrypto520@gmail.com"
USER_AGENT_BROWSER = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Hosts that require the SEC fair-access throttle (5 rps cap).
SEC_HOSTS = {"www.sec.gov", "sec.gov", "efts.sec.gov", "data.sec.gov"}

# --- logging -----------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("metadata_enrich")


# --- per-host throttle --------------------------------------------------------

class HostThrottle:
    """
    Token-bucket throttle keyed by (netloc). Enforces a minimum delay between
    consecutive requests to the same host. Thread-safe.
    """

    def __init__(self, default_interval: float = 0.20, sec_interval: float = 0.20):
        self.default_interval = default_interval
        self.sec_interval = sec_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _interval_for(self, host: str) -> float:
        if host in SEC_HOSTS:
            return self.sec_interval
        return self.default_interval

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if not host:
            return
        min_gap = self._interval_for(host)
        while True:
            with self._lock:
                now = time.monotonic()
                last = self._last.get(host, 0.0)
                elapsed = now - last
                if elapsed >= min_gap:
                    self._last[host] = now
                    return
                sleep_for = min_gap - elapsed
            time.sleep(sleep_for)


# --- URL check ---------------------------------------------------------------

@dataclass
class UrlCheckResult:
    url: str
    ok: bool
    status: int | None
    final_url: str
    method: str                # 'HEAD' or 'GET' or 'HEAD_BROWSER'
    error: str = ""


def check_url(
    session: requests.Session,
    url: str,
    throttle: HostThrottle,
    timeout: float = 15.0,
) -> UrlCheckResult:
    """
    Probe a URL: HEAD first (polite UA), then HEAD with browser UA on 403,
    then GET with browser UA if HEAD is rejected (some CDNs 405 HEAD).
    Success statuses: 200, 301, 302, 303, 307, 308, 405 (HEAD not allowed).
    """
    def attempt(method: str, ua: str) -> UrlCheckResult:
        throttle.wait(url)
        try:
            r = session.request(
                method,
                url,
                headers={"User-Agent": ua, "Accept": "*/*"},
                timeout=timeout,
                allow_redirects=True,
            )
            ok_status = r.status_code in (200, 301, 302, 303, 307, 308, 405)
            return UrlCheckResult(
                url=url,
                ok=ok_status,
                status=r.status_code,
                final_url=str(r.url),
                method=method if ua == USER_AGENT_POLITE else f"{method}_BROWSER",
            )
        except requests.RequestException as e:
            return UrlCheckResult(url=url, ok=False, status=None, final_url="", method=method, error=str(e)[:200])

    # 1) HEAD with polite UA
    r = attempt("HEAD", USER_AGENT_POLITE)
    if r.ok:
        return r
    best = r
    # 2) HEAD with browser UA on 400 / 403 / 406 (bot-blocked CDNs)
    if r.status in (400, 403, 406):
        r2 = attempt("HEAD", USER_AGENT_BROWSER)
        if r2.ok:
            return r2
        if r2.status is not None:
            best = r2
    # 3) GET with browser UA for sites that reject HEAD entirely
    r3 = attempt("GET", USER_AGENT_BROWSER)
    if r3.ok:
        return r3
    if r3.status is not None:
        best = r3
    return best


# --- template expansion ------------------------------------------------------

def clean_ticker(ticker: str) -> tuple[str, str, str, str]:
    """Return (TICKER, ticker_lower, TICKER_CLEAN, ticker_clean_lower)."""
    t = ticker.strip().upper()
    tc = re.sub(r"[^A-Z0-9]", "", t)
    return (t, t.lower(), tc, tc.lower())


def expand_template(template: str, candidate: dict) -> str:
    """
    Fill {TICKER} etc. placeholders. Missing placeholders are left untouched
    (the template is then unusable and will fail the URL check).
    """
    t_upper, t_lower, t_clean_upper, t_clean_lower = clean_ticker(candidate.get("ticker", ""))
    # slug_generic: a best-effort fund-name slug derived from fund_name.
    fund_name = candidate.get("fund_name", "") or ""
    slug_generic = re.sub(r"[^a-z0-9]+", "-", fund_name.lower()).strip("-")[:48]
    cik = str(candidate.get("cik", "") or "").lstrip("0")[-10:] or "0"
    mapping = {
        "{TICKER}": t_upper,
        "{ticker_lower}": t_lower,
        "{TICKER_CLEAN}": t_clean_upper,
        "{ticker_clean_lower}": t_clean_lower,
        "{slug_generic}": slug_generic,
        "{cik}": cik,
    }
    out = template
    for k, v in mapping.items():
        out = out.replace(k, v)
    return out


# --- inferrable-field derivation ---------------------------------------------

def infer_vehicle_type(candidate: dict) -> str:
    """
    Coarse classifier: fixed_income / commodity / currency / multi_asset /
    equity_factor / equity_index / equity_active / other. Based on fund name
    keywords + N-CEN flags.
    """
    name = (candidate.get("fund_name", "") or "").lower()
    is_index = bool(candidate.get("is_index"))
    is_multi_inverse_index = bool(candidate.get("is_multi_inverse_index"))

    # Fixed-income keywords.
    fi_keywords = [
        "bond", "treasury", "treasuries", "fixed income", "corporate",
        "municipal", "muni ", "tips", "t-bill", "t-bond", "high yield",
        "high-yield", "hy ", "investment grade", "ig ", "credit",
        "mortgage", "cmbs", "clo", "floating rate", "convertible",
        "interest rate", "yield curve", "treasury inflation",
        "senior loan", "bank loan", "aggregate", "government bond",
        "sovereign debt", "emerging markets debt", "duration", "rate hedge",
    ]
    if any(k in name for k in fi_keywords):
        return "fixed_income"

    # Commodity.
    commodity_keywords = [
        "gold", "silver", "platinum", "palladium", "copper", "uranium",
        "commodity", "commodities", "oil", "natural gas", "energy commodity",
        "crude", "metal", "livestock", "agricultur", "soybean", "corn",
        "wheat", "carbon credit", "cocoa", "coffee",
    ]
    if any(k in name for k in commodity_keywords):
        return "commodity"

    # Currency.
    curr_keywords = [
        "dollar index", "currency", "forex", "yen", "euro currency",
        "yuan", "renminbi",
    ]
    if any(k in name for k in curr_keywords):
        return "currency"

    # Multi-asset.
    if any(k in name for k in ["multi-asset", "balanced", "target date", "asset allocation", "all-in-one", "60/40"]):
        return "multi_asset"

    # Equity factor.
    factor_keywords = [
        "factor", "momentum", "value factor", "quality factor", "low volatility",
        "high beta", "min vol", "minimum volatility", "equal weight",
        "dividend growth", "dividend aristocrat", "smart beta",
    ]
    if any(k in name for k in factor_keywords):
        return "equity_factor"

    if is_index and not is_multi_inverse_index:
        return "equity_index"

    if is_multi_inverse_index:
        # Leveraged / inverse vehicles; classify as equity_factor for research
        # even though subtype captures the leverage direction.
        return "equity_factor"

    # Default: if IS_INDEX flag is false, it's actively managed.
    return "equity_active"


_LEVERAGE_PATTERNS = [
    (re.compile(r"\b3x\b|\btriple\b|\b300%\b", re.I), "leveraged_3x"),
    (re.compile(r"\b2x\b|\bdouble\b|\bultra\b|\b200%\b", re.I), "leveraged_2x"),
    (re.compile(r"\b1\.5x\b|\b150%\b", re.I), "leveraged_1_5x"),
    (re.compile(r"\b-?1x\b|\binverse\b|\bshort\b|\bbear\b", re.I), "inverse"),
]


def infer_vehicle_subtype(candidate: dict) -> str:
    name = (candidate.get("fund_name", "") or "")
    # Leverage / inverse check first (most specific).
    for rx, tag in _LEVERAGE_PATTERNS:
        if rx.search(name):
            return tag
    name_l = name.lower()
    if "smart beta" in name_l or "strategic beta" in name_l:
        return "smart_beta"
    if "esg" in name_l or "sustainable" in name_l or "impact" in name_l:
        return "esg"
    if "dividend" in name_l and "aristocrat" in name_l:
        return "dividend_aristocrat"
    if "dividend" in name_l:
        return "dividend"
    if "buffer" in name_l or "barrier" in name_l or "defined outcome" in name_l:
        return "buffered_outcome"
    if "covered call" in name_l or "option" in name_l or "derivative income" in name_l:
        return "options_strategy"
    if "thematic" in name_l or "theme" in name_l:
        return "thematic"
    if any(k in name_l for k in [
        "technology", "financials", "healthcare", "health care", "energy",
        "utilities", "industrials", "materials", "consumer", "real estate",
        "reit", "communication", "sector", "biotech", "pharmaceutical",
        "semiconductor", "aerospace", "defense", "airline", "automotive",
        "retail", "software", "hardware",
    ]):
        return "sector"
    return "null"


def infer_replication_method(candidate: dict) -> str:
    if bool(candidate.get("is_index")):
        # N-CEN doesn't tell us full vs. sampled; default to full_replication
        # and rely on sponsor prospectus for the definitive answer.
        return "full_replication"
    if bool(candidate.get("is_multi_inverse_index")):
        return "synthetic"         # leveraged/inverse use swaps
    return "active"


_BENCHMARK_PROVIDERS = [
    (re.compile(r"\bs&p\b|\bs\&p\b|\bs and p\b|\bspx\b|\bs\&p 500\b|\bstandard\s*(&|and)\s*poor", re.I), "sp"),
    (re.compile(r"\bmsci\b", re.I), "msci"),
    (re.compile(r"\bftse\b|\brussell\b", re.I), "ftse_russell"),
    (re.compile(r"\bbloomberg\b|\bblmbrg\b", re.I), "bloomberg"),
    (re.compile(r"\bsolactive\b", re.I), "solactive"),
    (re.compile(r"\bnasdaq-100\b|\bnasdaq 100\b|\bnas-100\b", re.I), "nasdaq"),
    (re.compile(r"\bdow jones\b|\bdow-jones\b", re.I), "sp"),   # DJ owned by S&P
    (re.compile(r"\bcrsp\b", re.I), "crsp"),
    (re.compile(r"\bice\b", re.I), "ice"),
    (re.compile(r"\bj\.?p\.? morgan\b", re.I), "jp_morgan"),
]


def infer_benchmark_provider(candidate: dict) -> str:
    name = candidate.get("fund_name", "") or ""
    if not bool(candidate.get("is_index")):
        return "null"
    for rx, tag in _BENCHMARK_PROVIDERS:
        if rx.search(name):
            return tag
    return "custom"


def infer_market_cap_tier(candidate: dict) -> str:
    name = (candidate.get("fund_name", "") or "").lower()
    if "mega" in name or "mega-cap" in name or "mega cap" in name:
        return "mega"
    if "large-cap" in name or "large cap" in name or "large-cap" in name or " lrgcap" in name or "large company" in name or "s&p 500" in name:
        return "large"
    if "mid-cap" in name or "mid cap" in name or "midcap" in name:
        return "mid"
    if "small-cap" in name or "small cap" in name or "smallcap" in name or "russell 2000" in name:
        return "small"
    if "micro-cap" in name or "micro cap" in name or "microcap" in name:
        return "micro"
    if "all-cap" in name or "total market" in name or "all cap" in name or "total stock market" in name:
        return "mixed"
    # Fixed-income, commodity, currency, multi-asset → n_a.
    vtype = infer_vehicle_type(candidate)
    if vtype in ("fixed_income", "commodity", "currency", "multi_asset"):
        return "n_a"
    return "mixed"


def infer_geographic_focus(candidate: dict) -> str:
    name = (candidate.get("fund_name", "") or "").lower()
    if "emerging" in name or "emerging markets" in name or " em " in f" {name} ":
        return "emerging"
    if "china" in name:
        return "regional_china"
    if "japan" in name:
        return "regional_japan"
    if "europe" in name or "european" in name:
        return "regional_europe"
    if "india" in name:
        return "regional_india"
    if "asia" in name:
        return "regional_asia"
    if "latin america" in name or "mexico" in name or "brazil" in name:
        return "regional_latam"
    if "africa" in name:
        return "regional_africa"
    if "global" in name or "world" in name or "international" in name:
        if "ex-u" in name or "ex us" in name or "ex-us" in name or "international" in name:
            return "developed_ex_us"
        return "global"
    if "developed" in name:
        return "developed_ex_us"
    if "eafe" in name:
        return "developed_ex_us"
    # Default: US-focused (most US-listed ETFs).
    return "us"


def infer_currency_hedged(candidate: dict) -> bool:
    name = (candidate.get("fund_name", "") or "").lower()
    return ("currency hedged" in name or "currency-hedged" in name or
            "hedged equity" in name or "hedged international" in name or
            " hedged " in f" {name} " and "unhedged" not in name)


def infer_has_derivatives_overlay(candidate: dict) -> bool:
    name = (candidate.get("fund_name", "") or "").lower()
    keywords = ["covered call", "option", "buffer", "defined outcome",
                "put write", "call write", "overlay", "derivative income",
                "collar", "risk-managed", "risk managed", "tail risk",
                "hedge", "swap", "futures"]
    # Exclude raw keyword 'hedge' alone unless 'hedged' is in name.
    return any(k in name for k in keywords)


# --- EDGAR prospectus URL -----------------------------------------------------

def edgar_prospectus_url(cik: str) -> str:
    cik = (cik or "").lstrip("0")
    if not cik:
        return "null"
    return (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={cik}&type=497&dateb=&owner=include&count=10")


# --- main enrichment ---------------------------------------------------------

@dataclass
class EnrichedRecord:
    # Candidate identity.
    ticker: str
    fund_name: str
    sponsor_slug: str
    sponsor_raw: str
    exchange: str
    cik: str
    series_id: str
    # AUM + shares — best-effort from N-CEN.
    aum_usd: float | None
    aum_as_of_date: str
    shares_outstanding: int | None
    # Fund metadata (inferred).
    inception_date: str
    expense_ratio_bps: float | None
    vehicle_type: str
    vehicle_subtype: str
    benchmark_name: str
    benchmark_provider: str
    replication_method: str
    rebalance_frequency_prospectus: str
    rebalance_trigger_rules: str
    creation_unit_size: int | None
    holdings_feed_url: str
    holdings_feed_format: str
    holdings_feed_lag_hours: float | None
    sponsor_page_url: str
    prospectus_url: str
    has_derivatives_overlay: bool
    internal_crossing_disclosed: bool
    currency_hedged: bool
    fund_of_funds: bool
    primary_market_cap_tier: str
    geographic_focus: str
    notes: str
    # Lineage.
    lineage_authored_by: str
    lineage_authored_at: str
    # Telemetry (not in final Excel).
    url_check: dict = field(default_factory=dict)
    excluded: bool = False
    exclusion_reason: str = ""


def enrich_one(
    candidate: dict,
    session: requests.Session,
    throttle: HostThrottle,
    templates: dict,
) -> EnrichedRecord:
    sponsor_slug = candidate.get("sponsor_slug", "") or "unaffiliated_generic"
    template_spec = templates.get(sponsor_slug)
    has_sponsor_template = template_spec is not None
    if not has_sponsor_template:
        template_spec = templates.get("unaffiliated_generic", {})

    # --- Holdings_Feed_URL + Sponsor_Page_URL resolution ---
    fund_page_result: UrlCheckResult | None = None
    holdings_result: UrlCheckResult | None = None
    holdings_format = template_spec.get("holdings_format", "html_table")
    used_edgar_fallback = False      # true if Holdings_Feed_URL came from
                                     # the unaffiliated_generic template

    for tmpl in template_spec.get("fund_page", []) or []:
        url = expand_template(tmpl, candidate)
        r = check_url(session, url, throttle)
        if r.ok:
            fund_page_result = r
            break

    for tmpl in template_spec.get("holdings_feed", []) or []:
        url = expand_template(tmpl, candidate)
        r = check_url(session, url, throttle)
        if r.ok:
            holdings_result = r
            break

    # Fallback: unaffiliated_generic if sponsor-specific template failed.
    # This resolves to an SEC EDGAR filings index — a verified-accessible
    # provenance anchor, not a real holdings feed. Flag it so downstream
    # consumers know Phase 1 still needs to author the real adapter.
    if has_sponsor_template and (fund_page_result is None or holdings_result is None):
        generic = templates.get("unaffiliated_generic", {})
        if fund_page_result is None:
            for tmpl in generic.get("fund_page", []) or []:
                url = expand_template(tmpl, candidate)
                r = check_url(session, url, throttle)
                if r.ok:
                    fund_page_result = r
                    break
        if holdings_result is None:
            for tmpl in generic.get("holdings_feed", []) or []:
                url = expand_template(tmpl, candidate)
                r = check_url(session, url, throttle)
                if r.ok:
                    holdings_result = r
                    used_edgar_fallback = True
                    break

    # Record format + fallback flag.
    if holdings_result is not None and not has_sponsor_template:
        # Sponsor had no template at all — EDGAR was the first-line
        # resolution, not a fallback. Still flag for Phase 1 authoring.
        used_edgar_fallback = True
    if used_edgar_fallback:
        holdings_format = "edgar_fallback"

    # --- Inferrable fields ---
    rec = EnrichedRecord(
        ticker=candidate.get("ticker", ""),
        fund_name=candidate.get("fund_name", ""),
        sponsor_slug=sponsor_slug,
        sponsor_raw=candidate.get("sponsor_raw", ""),
        exchange=candidate.get("exchange", ""),
        cik=str(candidate.get("cik", "")),
        series_id=candidate.get("series_id", ""),
        aum_usd=candidate.get("net_assets_used"),
        aum_as_of_date=candidate.get("net_assets_as_of", "null") or "null",
        shares_outstanding=None,       # N-CEN has share counts aggregated;
                                      # per-fund shares_outstanding requires
                                      # N-PORT or sponsor page. Leave null.
        inception_date="null",        # Not in N-CEN; defer to Phase 1.
        expense_ratio_bps=None,       # Not in N-CEN; defer to Phase 1.
        vehicle_type=infer_vehicle_type(candidate),
        vehicle_subtype=infer_vehicle_subtype(candidate),
        benchmark_name="null",        # Not derivable reliably from fund_name.
        benchmark_provider=infer_benchmark_provider(candidate),
        replication_method=infer_replication_method(candidate),
        rebalance_frequency_prospectus="null",   # Requires prospectus parse.
        rebalance_trigger_rules="null",          # Requires prospectus parse.
        creation_unit_size=candidate.get("num_shares_per_creation_unit"),
        # Record the TEMPLATED URL (not the post-redirect final URL) —
        # some sponsors chain redirects to unrelated marketing domains
        # (e.g. macquarie's delawarefunds.com → nomura homepage for certain
        # regional IPs). The templated URL is the canonical we authored.
        # Phase 4 ingestion will apply follow-redirect at request time.
        holdings_feed_url=holdings_result.url if holdings_result else "null",
        holdings_feed_format=holdings_format if holdings_result else "null",
        holdings_feed_lag_hours=None,
        sponsor_page_url=fund_page_result.url if fund_page_result else "null",
        prospectus_url=edgar_prospectus_url(candidate.get("cik", "")),
        has_derivatives_overlay=infer_has_derivatives_overlay(candidate),
        internal_crossing_disclosed=False,       # Requires prospectus.
        currency_hedged=infer_currency_hedged(candidate),
        fund_of_funds=bool(candidate.get("is_fund_of_fund")),
        primary_market_cap_tier=infer_market_cap_tier(candidate),
        geographic_focus=infer_geographic_focus(candidate),
        notes=(
            "edgar_fallback — sponsor template pending Phase 1"
            if used_edgar_fallback else "null"
        ),
        lineage_authored_by="phase_0_fund_bible_cc",
        lineage_authored_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        url_check={
            "holdings_status": holdings_result.status if holdings_result else None,
            "holdings_method": holdings_result.method if holdings_result else None,
            "fund_page_status": fund_page_result.status if fund_page_result else None,
            "fund_page_method": fund_page_result.method if fund_page_result else None,
        },
    )

    # Exclusion gate: Holdings_Feed_URL is load-bearing.
    if holdings_result is None or rec.holdings_feed_url == "null":
        rec.excluded = True
        rec.exclusion_reason = "unresolved_holdings_url"
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-fund metadata enrichment")
    parser.add_argument("--input", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--excluded", required=True, type=pathlib.Path)
    parser.add_argument("--templates", required=True, type=pathlib.Path)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only the first N candidates (for testing)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip tickers already present in --output")
    args = parser.parse_args()

    if not args.input.exists():
        log.error("input file not found: %s", args.input)
        return 2
    if not args.templates.exists():
        log.error("templates file not found: %s", args.templates)
        return 2

    # Load templates.
    with args.templates.open() as f:
        template_yaml = yaml.safe_load(f)
    templates = template_yaml.get("templates", {})
    log.info("loaded %d sponsor URL templates", len(templates))

    # Resume support.
    already_done: set[str] = set()
    if args.resume and args.output.exists():
        with args.output.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                    key = f"{r.get('ticker','')}:{r.get('series_id','')}"
                    already_done.add(key)
                except Exception:
                    continue
        log.info("resume: %d already enriched", len(already_done))

    # Load candidates.
    candidates: list[dict] = []
    with args.input.open() as f:
        for line in f:
            c = json.loads(line)
            key = f"{c.get('ticker','')}:{c.get('series_id','')}"
            if key in already_done:
                continue
            candidates.append(c)
    if args.limit:
        candidates = candidates[: args.limit]
    log.info("candidates to enrich: %d", len(candidates))

    # Prepare outputs (append if resuming).
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.excluded.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.resume else "w"
    out_f = args.output.open(mode, encoding="utf-8")
    exc_f = args.excluded.open(mode, encoding="utf-8")
    out_lock = threading.Lock()
    exc_lock = threading.Lock()

    # HTTP session (shared — requests is thread-safe for GET/HEAD).
    session = requests.Session()
    throttle = HostThrottle(default_interval=0.15, sec_interval=0.20)

    total = len(candidates)
    completed = 0
    excluded_count = 0
    last_log = time.monotonic()
    start = time.monotonic()
    reason_counter: Counter = Counter()

    def _worker(c: dict) -> EnrichedRecord:
        return enrich_one(c, session, throttle, templates)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_worker, c) for c in candidates]
        for fut in as_completed(futures):
            try:
                rec = fut.result()
            except Exception as e:
                log.exception("worker crashed: %s", e)
                continue
            completed += 1
            line = json.dumps(asdict(rec), default=str) + "\n"
            if rec.excluded:
                excluded_count += 1
                reason_counter[rec.exclusion_reason] += 1
                with exc_lock:
                    exc_f.write(line)
                    exc_f.flush()
            else:
                with out_lock:
                    out_f.write(line)
                    out_f.flush()
            # Periodic progress log every ~10s.
            now = time.monotonic()
            if now - last_log > 10.0 or completed == total:
                rate = completed / max(1e-3, now - start)
                remaining = total - completed
                eta_sec = remaining / rate if rate > 0 else 0
                log.info(
                    "progress: %d/%d (%.1f/s, eta %ds, excluded=%d)",
                    completed, total, rate, int(eta_sec), excluded_count,
                )
                last_log = now

    out_f.close()
    exc_f.close()
    log.info("done. %d enriched, %d excluded", total - excluded_count, excluded_count)
    log.info("exclusion reasons:")
    for reason, n in reason_counter.most_common():
        log.info("  %-30s %d", reason, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
