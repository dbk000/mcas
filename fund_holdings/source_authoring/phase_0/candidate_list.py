"""
Phase 0 — Fund Bible: candidate enumeration from SEC Form N-CEN datasets.

Produces a deduplicated candidate list of US-listed ETFs that pass the
§4.1 structural tests (listed on US exchange, registered under the 1940
Act, plausibly transparent-basket under Rule 6c-11, AUM > $5M).

Output: source_authoring/phase_0/candidate_list.jsonl
        — one JSON line per candidate, keyed by SeriesId.

The N-CEN data set is the authoritative starting set for US registered
investment companies. N-CEN is filed annually; using the TRAILING FOUR
quarterly bundles gives the current-year universe (every fund filed at
least once in any 12-month window). Deduplication keeps the most recent
filing per SERIES_ID.

Rate limit: SEC fair-access policy permits 10 req/sec. We make ~5 requests
total (ZIP downloads), so no throttling needed in this phase.

Usage:
    python3 candidate_list.py \\
        --output ./candidate_list.jsonl \\
        --raw-dir ./raw_ncen \\
        --quarters 2025q2 2025q3 2025q4 2026q1

Exit codes:
    0 — success, candidate_list.jsonl written
    1 — download failure, SEC unreachable or HTTP error
    2 — schema error, N-CEN TSV missing required columns
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import pathlib
import re
import sys
import time
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Iterable

import yaml


SEC_USER_AGENT = "Darren Kenny dkcrypto520@gmail.com"
SEC_NCEN_BASE = "https://www.sec.gov/files/dera/data/form-n-cen-data-sets"

# Exchange MIC codes observed in N-CEN SECURITY_EXCHANGE.FUND_EXCHANGE.
# Restrict to US-listed venues per §4.1.1.
US_EXCHANGE_MICS = {
    "ARCX": "nyse_arca",        # NYSE Arca
    "XNYS": "nyse_arca",        # NYSE (occasionally used for ETFs)
    "XNAS": "nasdaq",           # Nasdaq
    "BATS": "cboe_bzx",         # Cboe BZX
    "BATY": "cboe_bzx",         # Cboe BYX (older code)
    "EDGA": "cboe_bzx",         # Cboe EDGA (rare)
    "EDGX": "cboe_bzx",         # Cboe EDGX (rare)
    "XASE": "nyse_arca",        # NYSE American (occasional ETF)
}

AUM_FLOOR_USD = 5_000_000.0

# --- logging -----------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("candidate_list")


# --- dataclasses --------------------------------------------------------------

@dataclass
class Candidate:
    """One ETF candidate, pre-enrichment. Emitted as a JSON line."""
    series_id: str
    fund_id: str
    accession_number: str
    filing_date: str                  # ISO date of the N-CEN filing
    report_ending_period: str         # ISO date, end of fiscal year covered
    ticker: str
    fund_name: str
    exchange_mic: str                 # raw MIC code from N-CEN
    exchange: str                     # mapped canonical slug (nyse_arca etc.)
    cik: str
    registrant_name: str
    family_name: str                  # REGISTRANT.FAMILY_INVESTMENT_COMPANY_NAME
    sponsor_raw: str                  # unnormalized, best-effort sponsor label
    sponsor_slug: str                 # normalized slug (e.g. 'ishares')
    daily_avg_net_assets: float | None
    monthly_avg_net_assets: float | None
    net_assets_used: float | None     # which AUM proxy we took (daily preferred)
    net_assets_as_of: str             # report_ending_period
    is_etf: bool
    is_etmf: bool
    is_index: bool
    is_multi_inverse_index: bool
    is_fund_of_fund: bool
    is_master_feeder: bool
    is_money_market: bool
    is_index_affiliated: bool
    is_index_exclusive: bool
    rely_on_rule_6c_11: bool
    is_in_kind_etf: bool | None
    num_shares_per_creation_unit: int | None
    redeemed_shares_per_creation_unit: int | None
    lei: str
    flags: dict = field(default_factory=dict)
    excluded: bool = False
    exclusion_reason: str = ""


# --- SEC download --------------------------------------------------------------

def download_ncen_zip(quarter: str, dest_dir: pathlib.Path) -> pathlib.Path:
    """Download one N-CEN quarterly ZIP to dest_dir. Idempotent — skips if present."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    # SEC versions some quarters with a _0 suffix (e.g. 2025q1_ncen_0.zip).
    # Probe both canonical and _0 variants.
    candidates = [
        f"{quarter}_ncen.zip",
        f"{quarter}_ncen_0.zip",
    ]
    for fname in candidates:
        url = f"{SEC_NCEN_BASE}/{fname}"
        local = dest_dir / fname
        if local.exists() and local.stat().st_size > 1_000_000:
            log.info("cached: %s (%d bytes)", fname, local.stat().st_size)
            return local
        req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            local.write_bytes(data)
            log.info("downloaded: %s (%d bytes)", fname, len(data))
            return local
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
    raise RuntimeError(f"no N-CEN ZIP found for quarter {quarter}")


def extract_needed_tables(zip_path: pathlib.Path, extract_dir: pathlib.Path) -> None:
    """Extract only the TSVs we need, so we don't balloon disk."""
    needed = {
        "SUBMISSION.tsv",
        "REGISTRANT.tsv",
        "REGISTRANT_REPORTING_SERIES.tsv",
        "FUND_REPORTED_INFO.tsv",
        "ETF.tsv",
        "SECURITY_EXCHANGE.tsv",
        "SHARES_OUTSTANDING.tsv",
    }
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name in needed:
                target = extract_dir / name
                if target.exists() and target.stat().st_size > 100:
                    continue
                z.extract(name, extract_dir)


# --- TSV parsing --------------------------------------------------------------

def read_tsv(path: pathlib.Path) -> list[dict[str, str]]:
    """Load a TSV into a list of dicts. Memory-cheap relative to pandas."""
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)


def parse_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip()
    if not s or s.upper() == "N/A":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_int(s: str | None) -> int | None:
    v = parse_float(s)
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, OverflowError):
        return None


def parse_bool(s: str | None) -> bool:
    """N-CEN uses Y/N/blank. Return True only on explicit Y."""
    if s is None:
        return False
    return s.strip().upper() == "Y"


# --- sponsor normalization ---------------------------------------------------

def load_disclosers_yaml(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Sponsor keyword → canonical slug. Ordering matters — more specific before
# general (e.g. "JPMORGAN" before "MORGAN"). Each entry is (regex, slug).
# Regex is case-insensitive, applied to REGISTRANT_NAME + family_name.
SPONSOR_PATTERNS: list[tuple[str, str]] = [
    (r"\bishares\b", "ishares"),
    (r"\bblackrock\b", "blackrock"),
    (r"\bvanguard\b", "vanguard"),
    (r"\bstate street\b|\bssga\b|\bspdr\b", "state_street"),
    (r"\binvesco\b|\bpowershares\b", "invesco"),
    (r"\bschwab\b", "schwab"),
    (r"\bfirst trust\b|\bf/t portfolios\b", "first_trust"),
    (r"\bwisdomtree\b|\bwisdom tree\b", "wisdomtree"),
    (r"\bproshares\b", "proshares"),
    (r"\bdirexion\b", "direxion"),
    (r"\bglobal x\b", "global_x"),
    (r"\bark etf\b|\bark investment\b|\bark innovation\b|\bark series\b", "ark"),
    (r"\bvaneck\b|\bvan eck\b", "vaneck"),
    (r"\bpacer\b", "pacer"),
    (r"\bjpmorgan\b|\bj\.?p\.? morgan\b", "jpmorgan"),
    (r"\bgoldman sachs\b", "goldman_sachs"),
    (r"\bpimco\b", "pimco"),
    (r"\binnovator\b", "innovator"),
    (r"\bdefiance\b", "defiance"),
    (r"\btidal\b|\btoroso\b|\blisted funds trust\b", "tidal"),
    (r"\bkraneshares\b|\bkrane\b", "krane"),
    (r"\bamplify\b", "amplify"),
    (r"\balps\b", "alps"),
    (r"\broundhill\b", "roundhill"),
    (r"\bgraniteshares\b|\bgranite shares\b", "graniteshares"),
    (r"\byieldmax\b", "yieldmax"),
    (r"\bsimplify\b", "simplify"),
    (r"\bdimensional\b|\bdfa\b", "dimensional"),
    (r"\bnuveen\b", "nuveen"),
    (r"\bpgim\b", "pgim"),
    (r"\bfranklin\b|\btempleton\b", "franklin_templeton"),
    (r"\bfidelity\b", "fidelity"),
    (r"\bcolumbia\b", "columbia"),
    (r"\bhartford\b", "hartford"),
    (r"\bxtrackers\b|\bdws\b", "xtrackers"),
    (r"\bamerican century\b", "american_century"),
    (r"\bvictory\b", "victory_capital"),
    (r"\bvirtus\b", "virtus"),
    (r"\baptus\b", "aptus"),
    (r"\bbondbloxx\b", "bondbloxx"),
    # Single-sponsor trusts that were unmatched in the first run.
    (r"\bcalamos\b", "calamos"),
    (r"\balliancebernstein\b|\bab active etfs\b|\bab etfs\b|^\s*ab\s+", "alliancebernstein"),
    (r"\bjohn hancock\b", "john_hancock"),
    (r"\bjanus\b", "janus_henderson"),
    (r"\bsprott\b", "sprott"),
    (r"\bmacquarie\b", "macquarie"),
    (r"\bneos\b", "neos"),
    (r"\bstrategy shares\b", "strategy_shares"),
    (r"\babrdn\b|\baberdeen\b", "abrdn"),
    (r"\btortoise\b", "tortoise"),
    (r"\balger\b", "alger"),
    (r"\bmadison\b", "madison"),
    (r"\ballspring\b", "allspring"),
    (r"\bsei\b", "sei"),
    (r"\brussell investments\b", "russell_investments"),
    (r"\bangel oak\b", "angel_oak"),
    (r"\btimothy plan\b", "timothy_plan"),
    (r"\bthemes etf\b|\bthemes\s+(globalized|us|genai|big data|ai|copper|semiconductor|robotics|defense|airlines|space|cloud|cyber|electrification|battery|generative)\b", "themes"),
    (r"\bmotleyfool\b", "motley_fool"),
    (r"\bemles\b", "emles"),
    (r"\bfrontier (asset|portfolio)\b", "frontier_asset"),
    (r"\bpmv adaptive\b", "pmv"),
    (r"\bcastleark\b", "castleark"),
    (r"\bhoya capital\b", "hoya_capital"),
    (r"\bnetlease corporate\b", "netlease"),
    (r"\bclearshares\b", "clearshares"),
    (r"\bspear\b", "spear"),
    (r"\bwahed\b", "wahed"),
    (r"\bstf\b", "stf"),
    (r"\balexis\b", "alexis"),
    (r"\bkeating active etf\b", "keating"),
    (r"\bstocksnips\b", "stocksnips"),
    (r"\brelative sentiment\b", "relative_sentiment"),
    (r"\beuclidean\b", "euclidean"),
    (r"\bbridges capital\b", "bridges_capital"),
    (r"\bmkam etf\b", "mkam"),
    (r"\bbridgeway\b", "bridgeway"),
    (r"\bstrive\b", "strive"),
    (r"\bqraft\b", "qraft"),
    (r"\bcabana\b", "cabana"),
    (r"\bbancreek\b", "bancreek"),
    (r"\bengine no\.?\s*1\b|\bengine 1\b", "engine_no_1"),
    (r"\bstone ridge\b", "stone_ridge"),
    (r"\bmeritas\b", "meritas"),
    (r"\bavantis\b", "avantis"),
    (r"\bfm focus\b|\bfm compounders\b", "fm"),
    (r"\bworld gold trust\b|\bsprott\b", "sprott"),
    (r"\btruist\b", "truist"),
    (r"\bsage\b", "sage"),
    (r"\benvironmental impact\b", "impact_shares"),
    (r"\bcboe\b", "cboe"),
    (r"\bchange finance\b", "change_finance"),
    (r"\bopal custom\b|\bopal\b", "opal"),
    (r"\bcarillon\b|\beagle\b", "carillon"),
    (r"\bpalmer square\b", "palmer_square"),
    (r"\bpenn mutual\b", "penn_mutual"),
    (r"\bpacific global\b", "pacific_global"),
    (r"\bfutureland\b", "futureland"),
    (r"\bopal\b", "opal"),
    (r"\bmeridian\b", "meridian"),
    (r"\bclockwise\b", "clockwise"),
    (r"\bcambria\b", "cambria"),
    (r"\bthird avenue\b", "third_avenue"),
    (r"\bxai\b", "xai"),
    (r"\bzacks\b", "zacks"),
    (r"\bmonarch blue chips\b|\bmonarch\b", "monarch"),
    (r"\bmarket vectors\b", "vaneck"),
    (r"\brayliant\b", "rayliant"),
    (r"\bhumankind\b", "humankind"),
    (r"\bimpact shares\b", "impact_shares"),
    (r"\balpha architect\b", "alpha_architect"),
    (r"\bea (bridgeway|series)\b", "alpha_architect"),
    (r"\bcapital group\b|\bamerican funds\b", "capital_group"),
    # Additional commonly-encountered sponsors not already in the YAML.
    (r"\btouchstone\b", "touchstone"),
    (r"\bmotley fool\b", "motley_fool"),
    (r"\bfrontier\b", "frontier"),
    (r"\bishares\b", "ishares"),
    (r"\bopal\b", "opal"),
    (r"\bbny\b|\bmellon\b", "bny_mellon"),
    (r"\bnorthern trust\b|\bftse russell\b|\bflexshares\b", "northern_trust"),
    (r"\bgmo\b", "gmo"),
    (r"\btcw\b", "tcw"),
    (r"\beaton vance\b|\bparametric\b|\bcalvert\b", "eaton_vance"),
    (r"\bmorgan stanley\b|\bcalvert\b", "morgan_stanley"),
    (r"\bcitigroup\b|\bcitigroup first investors\b", "citigroup"),
    (r"\brayliant\b", "rayliant"),
    (r"\bhumankind\b", "humankind"),
    (r"\bmatthews asia\b|\bmatthews\b", "matthews"),
    (r"\bcohen & steers\b|\bcohen steers\b", "cohen_steers"),
    (r"\bnuveen\b", "nuveen"),
    (r"\bfidelity active\b|\bfidelity blue chip\b|\bfidelity small mid\b|\bfidelity magellan\b|\bfidelity real estate\b", "fidelity_active"),
    (r"\bcapital group\b|\bamerican funds\b", "capital_group"),
    (r"\bmorgan dempsey\b", "morgan_dempsey"),
    (r"\bredwheel\b", "redwheel"),
    (r"\badvisor shares\b|\badvisorshares\b", "advisorshares"),
    (r"\bexchange traded concepts\b", "exchange_traded_concepts"),
    (r"\bharbor\b", "harbor"),
    (r"\benginer\b|\bengineered\b", "engineered"),
    (r"\binspire\b", "inspire"),
    (r"\bsyntax\b", "syntax"),
    (r"\btemasek\b", "temasek"),
    (r"\bdriehaus\b", "driehaus"),
    (r"\bmeridian\b", "meridian"),
    (r"\bmfs\b|\bmassachusetts financial\b", "mfs"),
    (r"\bneuberger\b", "neuberger_berman"),
    (r"\blazard\b", "lazard"),
    (r"\bcrossingbridge\b", "crossingbridge"),
    (r"\bstone ridge\b", "stone_ridge"),
    (r"\bformidable\b", "formidable"),
    (r"\bfederated hermes\b|\bfederated\b", "federated"),
    (r"\bpanagram\b", "panagram"),
    (r"\bgabelli\b|\bgamco\b", "gabelli"),
    (r"\bkensho\b", "kensho"),
    (r"\bbrandes\b", "brandes"),
    (r"\bclarkson\b", "clarkson"),
    (r"\bphocas\b", "phocas"),
    (r"\bprecidian\b", "precidian"),
    (r"\bblue tractor\b", "blue_tractor"),
    (r"\bnatixis\b", "natixis"),
    (r"\bt\.? rowe\b", "t_rowe_price"),
    (r"\bnationwide\b", "nationwide"),
    (r"\brex shares\b|\brexshares\b", "rex_shares"),
    (r"\btema\b", "tema"),
    (r"\bswan\b", "swan"),
    (r"\bputnam\b", "putnam"),
    (r"\blegg mason\b", "legg_mason"),
    (r"\bimpact shares\b", "impact_shares"),
    (r"\bdoubleline\b", "doubleline"),
    (r"\bprincipal\b", "principal"),
    (r"\btransamerica\b", "transamerica"),
    (r"\bblackstone\b", "blackstone"),
    (r"\bsterling capital\b", "sterling_capital"),
    (r"\bthornburg\b", "thornburg"),
    (r"\bmuzinich\b", "muzinich"),
    (r"\broyce\b", "royce"),
    (r"\bbrown advisory\b", "brown_advisory"),
    (r"\bbarclays\b|\bipath\b", "barclays"),
    (r"\bubs\b", "ubs"),
]


def normalize_sponsor(
    registrant_name: str,
    family_name: str,
    fund_name: str = "",
) -> tuple[str, str]:
    """
    Return (sponsor_raw, sponsor_slug).
    Matches the first pattern in SPONSOR_PATTERNS against registrant_name,
    family_name, and fund_name (in that order of specificity). The
    'sponsor_raw' is the family_name if populated, else registrant_name.
    The slug is empty when no pattern matches — those candidates flow
    through unmatched; the enrichment phase can resolve via sponsor page.
    """
    raw = (family_name or "").strip()
    if not raw:
        raw = (registrant_name or "").strip()
    # Order matters: registrant/family names first (more specific). Only
    # fall back to fund_name for shared trusts (multiple sponsors host
    # funds under one legal entity, e.g. EA Series Trust).
    haystack_primary = f"{registrant_name} {family_name}".lower()
    for pattern, slug in SPONSOR_PATTERNS:
        if re.search(pattern, haystack_primary, re.IGNORECASE):
            return (raw, slug)
    # Fund-name fallback for multi-sponsor trusts.
    if fund_name:
        for pattern, slug in SPONSOR_PATTERNS:
            if re.search(pattern, fund_name.lower(), re.IGNORECASE):
                return (raw, slug)
        # Final fallback: use the first 1-2 words of the fund name as a
        # makeshift slug. E.g. "Tradr 2X Long Innovation ETF" → "tradr",
        # "American Beacon AHL Trend ETF" → "american_beacon". Sheet
        # grouping works even when sponsor isn't formally canonicalised.
        words = re.findall(r"[A-Za-z]+", fund_name)
        stopwords = {"the", "a", "an", "etf", "fund", "trust", "shares", "portfolio"}
        cleaned = [w.lower() for w in words if w.lower() not in stopwords and len(w) > 1]
        if cleaned:
            # Two-word fallback when first word is a 1-letter ambiguous tag
            # (e.g. "SP Funds Trust" → "sp_funds_trust" isn't useful; just
            # use the first two meaningful words).
            take = cleaned[:2] if len(cleaned[0]) <= 3 else cleaned[:1]
            slug = "_".join(take)
            slug = re.sub(r"[^a-z0-9_]", "", slug)[:64]
            if slug:
                return (raw, f"unaffiliated_{slug}")
    return (raw, "")


# --- per-quarter assembly -----------------------------------------------------

def assemble_quarter(extract_dir: pathlib.Path, quarter_label: str) -> list[Candidate]:
    """
    Load the key TSVs from one quarter, join into a list of ETF candidates.
    """
    required = [
        "SUBMISSION.tsv",
        "REGISTRANT.tsv",
        "FUND_REPORTED_INFO.tsv",
        "ETF.tsv",
        "SECURITY_EXCHANGE.tsv",
    ]
    for fname in required:
        if not (extract_dir / fname).exists():
            raise FileNotFoundError(f"missing {fname} in {extract_dir}")

    submissions = {r["ACCESSION_NUMBER"]: r for r in read_tsv(extract_dir / "SUBMISSION.tsv")}
    registrants = {r["ACCESSION_NUMBER"]: r for r in read_tsv(extract_dir / "REGISTRANT.tsv")}
    funds_info = {r["FUND_ID"]: r for r in read_tsv(extract_dir / "FUND_REPORTED_INFO.tsv")}
    etf_rows = {r["FUND_ID"]: r for r in read_tsv(extract_dir / "ETF.tsv")}

    # SECURITY_EXCHANGE may have MULTIPLE rows per fund (e.g. dual-listed).
    # Prefer an ARCX/XNAS/BATS/XASE row; fall back to the first row.
    exch_rows: dict[str, list[dict[str, str]]] = {}
    for r in read_tsv(extract_dir / "SECURITY_EXCHANGE.tsv"):
        exch_rows.setdefault(r["FUND_ID"], []).append(r)

    out: list[Candidate] = []
    for fund_id, etf_row in etf_rows.items():
        info = funds_info.get(fund_id)
        if info is None:
            # ETF row with no FUND_REPORTED_INFO — anomalous, skip. N-CEN join
            # integrity is ~100% in practice; a miss means we skip one fund.
            continue
        if not parse_bool(info.get("IS_ETF")):
            # Defensive — should always be Y since fund is in ETF.tsv.
            continue

        accession = info["ACCESSION_NUMBER"]
        sub = submissions.get(accession, {})
        reg = registrants.get(accession, {})

        filing_date = sub.get("FILING_DATE", "").strip()
        report_end = sub.get("REPORT_ENDING_PERIOD", "").strip()
        cik = sub.get("CIK", "").strip() or reg.get("CIK", "").strip()

        exch_candidates = exch_rows.get(fund_id, [])
        exch_mic = ""
        ticker = ""
        for ex in exch_candidates:
            if ex.get("FUND_EXCHANGE", "").upper() in US_EXCHANGE_MICS:
                exch_mic = ex["FUND_EXCHANGE"].upper()
                ticker = ex.get("FUND_TICKER_SYMBOL", "").strip()
                break
        if not exch_mic and exch_candidates:
            # Fund registered on non-US venue; will be excluded below.
            exch_mic = exch_candidates[0].get("FUND_EXCHANGE", "").upper()
            ticker = exch_candidates[0].get("FUND_TICKER_SYMBOL", "").strip()

        sponsor_raw, sponsor_slug = normalize_sponsor(
            reg.get("REGISTRANT_NAME", ""),
            reg.get("FAMILY_INVESTMENT_COMPANY_NAME", ""),
            info.get("FUND_NAME", ""),
        )

        daily_aum = parse_float(info.get("DAILY_AVG_NET_ASSETS"))
        monthly_aum = parse_float(info.get("MONTHLY_AVG_NET_ASSETS"))
        used = daily_aum if daily_aum is not None else monthly_aum

        cand = Candidate(
            series_id=info.get("SERIES_ID", "").strip(),
            fund_id=fund_id,
            accession_number=accession,
            filing_date=filing_date,
            report_ending_period=report_end,
            ticker=ticker,
            fund_name=info.get("FUND_NAME", "").strip(),
            exchange_mic=exch_mic,
            exchange=US_EXCHANGE_MICS.get(exch_mic, ""),
            cik=cik,
            registrant_name=reg.get("REGISTRANT_NAME", "").strip(),
            family_name=reg.get("FAMILY_INVESTMENT_COMPANY_NAME", "").strip(),
            sponsor_raw=sponsor_raw,
            sponsor_slug=sponsor_slug,
            daily_avg_net_assets=daily_aum,
            monthly_avg_net_assets=monthly_aum,
            net_assets_used=used,
            net_assets_as_of=report_end,
            is_etf=parse_bool(info.get("IS_ETF")),
            is_etmf=parse_bool(info.get("IS_ETMF")),
            is_index=parse_bool(info.get("IS_INDEX")),
            is_multi_inverse_index=parse_bool(info.get("IS_MULTI_INVERSE_INDEX")),
            is_fund_of_fund=parse_bool(info.get("IS_FUND_OF_FUND")),
            is_master_feeder=parse_bool(info.get("IS_MASTER_FEEDER")),
            is_money_market=parse_bool(info.get("IS_MONEY_MARKET")),
            is_index_affiliated=parse_bool(info.get("IS_INDEX_AFFILIATED")),
            is_index_exclusive=parse_bool(info.get("IS_INDEX_EXCLUSIVE")),
            rely_on_rule_6c_11=parse_bool(info.get("IS_RELYON_RULE_6C_11")),
            is_in_kind_etf=parse_bool(etf_row.get("IS_FUND_IN_KIND_ETF")) if etf_row else None,
            num_shares_per_creation_unit=parse_int(etf_row.get("NUM_SHARES_PER_CREATION_UNIT")) if etf_row else None,
            redeemed_shares_per_creation_unit=parse_int(etf_row.get("REDEEMED_SHARES_PER_CREATION_UNIT")) if etf_row else None,
            lei=info.get("LEI", "").strip(),
            flags={"source_quarter": quarter_label},
        )
        out.append(cand)

    return out


# --- de-duplication + filtering ----------------------------------------------

def dedupe_latest_by_series(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Keep the most recent filing per SERIES_ID. Ties → first encountered."""
    best: dict[str, Candidate] = {}
    for c in candidates:
        key = c.series_id or c.fund_id
        existing = best.get(key)
        if existing is None:
            best[key] = c
            continue
        # Compare by filing_date (ISO yyyy-mm-dd sorts lexicographically).
        if c.filing_date > existing.filing_date:
            best[key] = c
    return list(best.values())


def apply_structural_filters(
    candidates: list[Candidate],
    excluded_sponsors: set[str],
) -> tuple[list[Candidate], list[Candidate]]:
    """
    Apply §4.1 structural tests. Returns (included, excluded).
    Mutates candidate.excluded + exclusion_reason on exclusions.
    """
    included: list[Candidate] = []
    excluded: list[Candidate] = []
    for c in candidates:
        reason = None
        if not c.is_etf:
            reason = "not_an_etf"
        elif c.is_etmf:
            reason = "etmf_not_in_scope"
        elif c.is_money_market:
            reason = "money_market_not_in_scope"
        elif c.is_master_feeder:
            # Master-feeder structures: the feeder fund doesn't publish
            # independent holdings. OUT per §4.1.3 spirit. Master funds are
            # also out because they're not exchange-listed.
            reason = "master_feeder_structure"
        elif not c.exchange_mic or c.exchange_mic not in US_EXCHANGE_MICS:
            reason = f"non_us_exchange:{c.exchange_mic or 'none'}"
        elif not c.ticker:
            reason = "no_ticker"
        elif c.sponsor_slug in excluded_sponsors:
            reason = f"ant_sponsor:{c.sponsor_slug}"
        elif c.net_assets_used is None:
            # Accept funds with no reported AUM? These are usually newly
            # launched funds that filed N-CEN for a short stub period.
            # Defer the $5M check; flag + keep. Metadata enrichment will
            # resolve AUM from sponsor page.
            c.flags["aum_from_ncen"] = "missing"
        elif c.net_assets_used < AUM_FLOOR_USD:
            reason = f"sub_5m_aum:{c.net_assets_used:.0f}"

        if reason is not None:
            c.excluded = True
            c.exclusion_reason = reason
            excluded.append(c)
        else:
            included.append(c)
    return included, excluded


# --- main ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="SEC N-CEN ETF candidate enumerator")
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--raw-dir", required=True, type=pathlib.Path)
    parser.add_argument("--excluded-log", type=pathlib.Path, default=None)
    parser.add_argument("--disclosers-yaml", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[2] / "registry" / "known_daily_disclosers.yaml")
    parser.add_argument("--quarters", nargs="+", required=True,
                        help="e.g. 2025q2 2025q3 2025q4 2026q1")
    args = parser.parse_args()

    # Load exclusion set.
    disclosers = load_disclosers_yaml(args.disclosers_yaml)
    excluded_sponsors = set(disclosers.get("ant_sponsors_excluded", []) or [])
    log.info("excluded ANT sponsors: %d (%s)", len(excluded_sponsors), sorted(excluded_sponsors))

    # Download + extract each quarter.
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    all_candidates: list[Candidate] = []
    for q in args.quarters:
        log.info("=== quarter %s ===", q)
        zip_path = download_ncen_zip(q, args.raw_dir)
        extract_dir = args.raw_dir / q
        extract_needed_tables(zip_path, extract_dir)
        qc = assemble_quarter(extract_dir, q)
        log.info("quarter %s: %d ETF rows", q, len(qc))
        all_candidates.extend(qc)
        # Be polite to the SEC even though downloads are done — small gap
        # between extracts in case we add more quarters later.
        time.sleep(0.1)

    log.info("total pre-dedup: %d", len(all_candidates))
    deduped = dedupe_latest_by_series(all_candidates)
    log.info("total post-dedup (by SERIES_ID): %d", len(deduped))

    included, excluded = apply_structural_filters(deduped, excluded_sponsors)

    # Summarize exclusion reasons.
    from collections import Counter
    reason_counts = Counter(c.exclusion_reason for c in excluded)
    log.info("structural exclusions: %d", len(excluded))
    for reason, n in reason_counts.most_common():
        log.info("  %-30s %d", reason, n)

    # Summarize sponsor distribution in included set.
    sponsor_counts = Counter(c.sponsor_slug or "<unmatched>" for c in included)
    log.info("included by sponsor (top 30):")
    for sponsor, n in sponsor_counts.most_common(30):
        log.info("  %-30s %d", sponsor, n)
    unmatched = sponsor_counts.get("<unmatched>", 0)
    if unmatched:
        log.warning("%d included candidates have unmatched sponsor slugs", unmatched)

    # Write outputs.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for c in included:
            f.write(json.dumps(asdict(c), default=str) + "\n")
    log.info("wrote %d included candidates to %s", len(included), args.output)

    if args.excluded_log is not None:
        with args.excluded_log.open("w", encoding="utf-8") as f:
            for c in excluded:
                f.write(json.dumps(asdict(c), default=str) + "\n")
        log.info("wrote %d excluded rows to %s", len(excluded), args.excluded_log)

    # Sanity: target band 2500-3800 candidates before enrichment.
    if len(included) < 1500:
        log.warning(
            "included count %d is below the expected 2500+ band; "
            "investigate the quarter selection or exclusion rules",
            len(included),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
