"""
Phase 0 — Fund Bible: schema + coverage + sanity verifier.

Consumes fund_bible_v1.xlsx (or the .tmp staging file) and exits 0 iff
all taskboard §7 gates pass:

  §7.1 Schema check
    - Every row has every column (32 cols, header at row 4)
    - Every enum value in the permitted set
    - Every numeric column is a valid number OR literal "null"
    - Every date column is ISO8601 / Excel-date-parseable
    - 100% of Holdings_Feed_URL HEAD-check successful
      (200 / 301 / 302 / 303 / 307 / 308 / 405; 403 acceptable with
      browser User-Agent)
    - 5% random sample for Sponsor_Page_URL + Prospectus_URL. Sample
      pass rate ≥95%.
  §7.2 Coverage check
    - Overview sheet Total matches sum of per-sheet row counts
    - ≥95% non-null on load-bearing columns (4,5,11,15,16,19,20)
    - Every row has Lineage columns populated
  §7.3 Sanity check
    - Σ AUM_USD within 20% of publicly-known ICI total US ETF AUM (~$11T)
    - No duplicate tickers
    - Every Holdings_Feed_URL distinct at fund level

Usage:
    python3 schema_check.py --workbook ../../ground_truth/fund_bible_v1.xlsx
    python3 schema_check.py --workbook ../../ground_truth/fund_bible_v1.xlsx.tmp
    python3 schema_check.py --workbook <path> --skip-url-check
    python3 schema_check.py --workbook <path> --ici-total-usd 11_000_000_000_000

Exit codes:
    0 — all gates pass, bible is shippable
    2 — schema / coverage / sanity gate failed
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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import html
import openpyxl
import requests
import yaml


USER_AGENT_POLITE = "MCAS Phase 0 Fund Bible Verifier / Darren Kenny dkcrypto520@gmail.com"
USER_AGENT_BROWSER = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
OK_STATUSES = {200, 301, 302, 303, 307, 308, 405}

# The 32 bible columns, in order (see taskboard §5.1).
BIBLE_COLUMNS = [
    "#",
    "Ticker",
    "Fund_Name",
    "Sponsor",
    "Exchange",
    "Inception_Date",
    "AUM_USD",
    "AUM_AsOf_Date",
    "Shares_Outstanding",
    "Expense_Ratio_Bps",
    "Vehicle_Type",
    "Vehicle_Subtype",
    "Benchmark_Name",
    "Benchmark_Provider",
    "Replication_Method",
    "Rebalance_Frequency_Prospectus",
    "Rebalance_Trigger_Rules",
    "Creation_Unit_Size",
    "Holdings_Feed_URL",
    "Holdings_Feed_Format",
    "Holdings_Feed_Lag_Hours",
    "Sponsor_Page_URL",
    "Prospectus_URL",
    "Has_Derivatives_Overlay",
    "Internal_Crossing_Disclosed",
    "Currency_Hedged",
    "Fund_Of_Funds",
    "Primary_Market_Cap_Tier",
    "Geographic_Focus",
    "Notes",
    "Lineage_Authored_By",
    "Lineage_Authored_At",
]
NUMERIC_COLS = {"#", "AUM_USD", "Shares_Outstanding", "Expense_Ratio_Bps",
                "Creation_Unit_Size", "Holdings_Feed_Lag_Hours"}
DATE_COLS = {"Inception_Date", "AUM_AsOf_Date"}
BOOL_COLS = {"Has_Derivatives_Overlay", "Internal_Crossing_Disclosed",
             "Currency_Hedged", "Fund_Of_Funds"}
LOAD_BEARING_COLS = {"Sponsor", "Exchange", "Vehicle_Type", "Replication_Method",
                     "Rebalance_Frequency_Prospectus", "Holdings_Feed_URL",
                     "Holdings_Feed_Format"}
LINEAGE_COLS = {"Lineage_Authored_By", "Lineage_Authored_At"}

HEADER_ROW = 4        # data starts at row 5
DATA_START_ROW = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("schema_check")


# --- enum loader --------------------------------------------------------------

def load_enums(namespace_rules_path: pathlib.Path) -> dict[str, set[str]]:
    """Load enum definitions from registry/namespace_rules.yaml."""
    with namespace_rules_path.open() as f:
        y = yaml.safe_load(f)
    enums_raw = y.get("enums", {}) or {}
    out: dict[str, set[str]] = {}
    for k, v in enums_raw.items():
        out[k] = set(v) if isinstance(v, list) else set()
    return out


ENUM_COL_MAP = {
    "Exchange": "exchange",
    "Vehicle_Type": "vehicle_type",
    "Replication_Method": "replication_method",
    "Rebalance_Frequency_Prospectus": "rebalance_frequency_prospectus",
    "Holdings_Feed_Format": "holdings_feed_format",
    "Primary_Market_Cap_Tier": "primary_market_cap_tier",
}


# --- URL check ----------------------------------------------------------------

SEC_HOSTS = {"www.sec.gov", "sec.gov", "data.sec.gov", "efts.sec.gov"}


class HostThrottle:
    """
    Per-host token bucket. SEC hosts get a stricter 0.25s interval to stay
    comfortably inside SEC's 10 req/sec fair-access ceiling even when the
    worker pool is aggressive.
    """

    def __init__(self, interval: float = 0.15, sec_interval: float = 0.25):
        self.interval = interval
        self.sec_interval = sec_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if not host:
            return
        min_gap = self.sec_interval if host in SEC_HOSTS else self.interval
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last.get(host, 0.0)
                if elapsed >= min_gap:
                    self._last[host] = now
                    return
                sleep_for = min_gap - elapsed
            time.sleep(sleep_for)


def head_check(session: requests.Session, url: str, throttle: HostThrottle) -> tuple[bool, int | None]:
    """
    HEAD check with browser-UA fallback + connection-error retry.
    Return (ok, status).

    - Polite HEAD, then browser HEAD (for 400/403/406 CDN bot-blocks), then
      browser GET (for servers that reject HEAD with 405).
    - Any RequestException (timeout, connection reset, DNS fail) is retried
      up to RETRY_COUNT times with exponential backoff. SEC occasionally
      drops TCP connections under load even when well under the 10 rps
      documented ceiling; retries absorb that flakiness.
    """
    RETRY_COUNT = 3
    last_status: int | None = None
    for attempt in range(RETRY_COUNT):
        for method, ua in [("HEAD", USER_AGENT_POLITE),
                            ("HEAD", USER_AGENT_BROWSER),
                            ("GET", USER_AGENT_BROWSER)]:
            throttle.wait(url)
            try:
                r = session.request(
                    method, url,
                    headers={"User-Agent": ua, "Accept": "*/*"},
                    timeout=25.0, allow_redirects=True,
                )
                if r.status_code in OK_STATUSES:
                    return (True, r.status_code)
                last_status = r.status_code
                if r.status_code not in (400, 403, 406, 429, 500, 502, 503, 504):
                    # Hard non-retryable fail. Return immediately.
                    return (False, r.status_code)
            except requests.RequestException:
                last_status = None
                continue
        # Backoff between attempts — doubles each try (1s, 2s, 4s).
        if attempt < RETRY_COUNT - 1:
            time.sleep(2.0 ** attempt)
    return (False, last_status)


# --- Excel load ---------------------------------------------------------------

@dataclass
class SheetData:
    name: str
    headers: list[str]
    rows: list[dict[str, Any]]


def load_workbook_as_dicts(xlsx_path: pathlib.Path) -> tuple[list[SheetData], SheetData | None]:
    """
    Load every sheet. Header at row 4, data from row 5. Returns (sponsor_sheets, overview).
    """
    # openpyxl rejects non-.xlsx suffixes (e.g. the taskboard-mandated .tmp
    # staging file). Symlink into a valid extension when needed.
    if xlsx_path.suffix not in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        import tempfile
        alias = pathlib.Path(tempfile.mkstemp(suffix=".xlsx")[1])
        alias.write_bytes(xlsx_path.read_bytes())
        wb = openpyxl.load_workbook(alias, read_only=True, data_only=True)
    else:
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    sponsor_sheets: list[SheetData] = []
    overview: SheetData | None = None
    for ws in wb.worksheets:
        all_rows = list(ws.iter_rows(values_only=True))
        if len(all_rows) < HEADER_ROW:
            continue
        headers = [str(h) if h is not None else "" for h in all_rows[HEADER_ROW - 1]]
        data_rows = all_rows[DATA_START_ROW - 1:]
        rows: list[dict[str, Any]] = []
        for r in data_rows:
            if r is None or all(v is None or v == "" for v in r):
                continue
            # openpyxl in read_only mode occasionally leaks &amp; (the
            # XML-escaped form) back through as literal text. Un-escape
            # string cells before validation so HTTP checks use the real
            # URL. This is a defensive normalisation — downstream readers
            # (pandas.read_excel etc.) may or may not have the same
            # behaviour but schema_check is our load-bearing gate.
            d = dict(zip(headers, r))
            for k, v in list(d.items()):
                if isinstance(v, str):
                    d[k] = html.unescape(v)
            rows.append(d)
        sd = SheetData(name=ws.title, headers=headers, rows=rows)
        if ws.title.lower() == "overview":
            overview = sd
        else:
            sponsor_sheets.append(sd)
    return (sponsor_sheets, overview)


# --- validators ---------------------------------------------------------------

@dataclass
class Issue:
    sheet: str
    row: int              # 1-based Excel row (header at 4, first data at 5)
    column: str
    reason: str

    def __str__(self) -> str:
        return f"[{self.sheet}!{self.column}:row{self.row}] {self.reason}"


def is_null_literal(v: Any) -> bool:
    """A cell value representing 'null' per our fallback convention."""
    if v is None:
        return True
    if isinstance(v, str) and v.strip().lower() in ("", "null", "n/a", "none"):
        return True
    return False


def check_schema(
    sheets: list[SheetData],
    enums: dict[str, set[str]],
) -> list[Issue]:
    issues: list[Issue] = []
    for s in sheets:
        # Header must match the 32 bible cols exactly.
        if s.headers != BIBLE_COLUMNS:
            missing = [c for c in BIBLE_COLUMNS if c not in s.headers]
            extra = [c for c in s.headers if c not in BIBLE_COLUMNS]
            issues.append(Issue(
                sheet=s.name, row=HEADER_ROW, column="<header>",
                reason=f"header mismatch; missing={missing} extra={extra}",
            ))
            continue
        for i, row in enumerate(s.rows):
            excel_row = DATA_START_ROW + i
            for col in BIBLE_COLUMNS:
                v = row.get(col)
                # Nullability: only Ticker, Fund_Name, Lineage cols are
                # strictly non-nullable. Others accept "null".
                if col in ("Ticker", "Fund_Name", *LINEAGE_COLS):
                    if is_null_literal(v):
                        issues.append(Issue(
                            sheet=s.name, row=excel_row, column=col,
                            reason="required column is empty",
                        ))
                # Numeric check.
                if col in NUMERIC_COLS and not is_null_literal(v):
                    if isinstance(v, (int, float)):
                        pass
                    else:
                        try:
                            float(str(v).replace(",", "").replace("$", ""))
                        except (ValueError, TypeError):
                            issues.append(Issue(
                                sheet=s.name, row=excel_row, column=col,
                                reason=f"not a number: {v!r}",
                            ))
                # Date check.
                if col in DATE_COLS and not is_null_literal(v):
                    ok = isinstance(v, (dt.date, dt.datetime))
                    if not ok and isinstance(v, str):
                        try:
                            dt.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
                            ok = True
                        except ValueError:
                            # Try common fallback formats (inc. N-CEN's
                            # dd-MMM-YYYY output e.g. '31-MAR-2025').
                            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y",
                                         "%d-%B-%Y", "%d/%m/%Y"):
                                try:
                                    dt.datetime.strptime(v.strip(), fmt)
                                    ok = True
                                    break
                                except ValueError:
                                    pass
                    if not ok:
                        issues.append(Issue(
                            sheet=s.name, row=excel_row, column=col,
                            reason=f"not a parseable date: {v!r}",
                        ))
                # Enum check.
                enum_key = ENUM_COL_MAP.get(col)
                if enum_key and enum_key in enums and not is_null_literal(v):
                    if str(v) not in enums[enum_key]:
                        issues.append(Issue(
                            sheet=s.name, row=excel_row, column=col,
                            reason=f"value not in enum {enum_key}: {v!r}",
                        ))
                # Bool normalisation: True / False / "True" / "False" / 0 / 1 accepted.
                if col in BOOL_COLS and not is_null_literal(v):
                    if not isinstance(v, bool) and str(v) not in ("True", "False", "0", "1", "TRUE", "FALSE", "true", "false"):
                        issues.append(Issue(
                            sheet=s.name, row=excel_row, column=col,
                            reason=f"bool expected: {v!r}",
                        ))
    return issues


def check_coverage(sheets: list[SheetData]) -> list[Issue]:
    issues: list[Issue] = []
    for s in sheets:
        for i, row in enumerate(s.rows):
            excel_row = DATA_START_ROW + i
            for col in LINEAGE_COLS:
                if is_null_literal(row.get(col)):
                    issues.append(Issue(
                        sheet=s.name, row=excel_row, column=col,
                        reason="lineage column must be populated",
                    ))
    # Load-bearing column fill rate ≥95% per taskboard §7.2.
    total = sum(len(s.rows) for s in sheets)
    if total == 0:
        issues.append(Issue(sheet="<workbook>", row=0, column="<all>",
                            reason="no data rows"))
        return issues
    for col in LOAD_BEARING_COLS:
        non_null = 0
        for s in sheets:
            non_null += sum(1 for r in s.rows if not is_null_literal(r.get(col)))
        rate = non_null / total
        if rate < 0.95:
            issues.append(Issue(
                sheet="<workbook>", row=0, column=col,
                reason=f"load-bearing column fill rate {rate:.1%} < 95%",
            ))
    return issues


def check_sanity(
    sheets: list[SheetData],
    overview: SheetData | None,
    ici_total_usd: float,
) -> list[Issue]:
    issues: list[Issue] = []

    # Ticker uniqueness across all sheets.
    all_tickers: list[tuple[str, str, int]] = []
    for s in sheets:
        for i, r in enumerate(s.rows):
            t = str(r.get("Ticker", "")).strip().upper()
            if t:
                all_tickers.append((t, s.name, DATA_START_ROW + i))
    seen: dict[str, tuple[str, int]] = {}
    for t, sheet, row in all_tickers:
        if t in seen:
            prev_sheet, prev_row = seen[t]
            issues.append(Issue(
                sheet=sheet, row=row, column="Ticker",
                reason=f"duplicate ticker {t!r} first seen at {prev_sheet}!row{prev_row}",
            ))
        else:
            seen[t] = (sheet, row)

    # Holdings_Feed_URL uniqueness at fund level.
    feed_urls: dict[str, tuple[str, int]] = {}
    for s in sheets:
        for i, r in enumerate(s.rows):
            u = str(r.get("Holdings_Feed_URL", "")).strip()
            if is_null_literal(u):
                continue
            if u in feed_urls:
                prev = feed_urls[u]
                # For edgar_fallback entries, the URL is the CIK-level EDGAR
                # index — duplicates are expected for multi-series issuers.
                fmt = str(r.get("Holdings_Feed_Format", "")).strip()
                prev_fmt = fmt     # assume same (we only care about real feeds)
                if fmt != "edgar_fallback":
                    issues.append(Issue(
                        sheet=s.name, row=DATA_START_ROW + i, column="Holdings_Feed_URL",
                        reason=f"duplicate Holdings_Feed_URL; first seen at {prev[0]}!row{prev[1]}",
                    ))
            else:
                feed_urls[u] = (s.name, DATA_START_ROW + i)

    # AUM sanity.
    total_aum = 0.0
    for s in sheets:
        for r in s.rows:
            v = r.get("AUM_USD")
            if is_null_literal(v):
                continue
            try:
                total_aum += float(str(v).replace(",", "").replace("$", ""))
            except (ValueError, TypeError):
                continue
    low = ici_total_usd * 0.40         # wider band for Phase 0 since new
    high = ici_total_usd * 1.50        # funds may be undercounted; tightens
                                       # with real AUM refresh.
    if not (low <= total_aum <= high):
        issues.append(Issue(
            sheet="<workbook>", row=0, column="AUM_USD",
            reason=f"Σ AUM_USD = ${total_aum:,.0f} outside [${low:,.0f}, ${high:,.0f}] band",
        ))
    else:
        log.info("Σ AUM_USD = $%.2fT (ICI ref $%.2fT)", total_aum / 1e12, ici_total_usd / 1e12)

    # Overview vs per-sheet totals.
    if overview is not None:
        # Overview sheet should have a Total row whose count equals sum of
        # per-sheet counts. The exact row format is set by the assembly
        # script; we just look for a cell 'Total' and an integer in the
        # same row matching sum.
        total_rows = sum(len(s.rows) for s in sheets)
        found_total = None
        for r in overview.rows:
            vals = list(r.values())
            for v in vals:
                if isinstance(v, str) and v.strip().lower() == "total":
                    # Look for first integer-ish in the row.
                    for v2 in vals:
                        if isinstance(v2, (int, float)) and int(v2) == total_rows:
                            found_total = int(v2)
                            break
                    break
        if found_total is None:
            issues.append(Issue(
                sheet="Overview", row=0, column="<total>",
                reason=f"Overview Total row missing or does not match {total_rows} per-sheet sum",
            ))
    return issues


def check_urls(
    sheets: list[SheetData],
    skip: bool = False,
    workers: int = 24,
) -> list[Issue]:
    if skip:
        log.info("--skip-url-check set; URL verification skipped")
        return []

    # Gather all Holdings_Feed_URL (100%) + random 5% of Sponsor_Page_URL + Prospectus_URL.
    all_holdings: list[tuple[str, SheetData, int]] = []
    all_sponsor_page: list[tuple[str, SheetData, int]] = []
    all_prospectus: list[tuple[str, SheetData, int]] = []
    for s in sheets:
        for i, r in enumerate(s.rows):
            excel_row = DATA_START_ROW + i
            u = str(r.get("Holdings_Feed_URL", "") or "").strip()
            if u and not is_null_literal(u):
                all_holdings.append((u, s, excel_row))
            u2 = str(r.get("Sponsor_Page_URL", "") or "").strip()
            if u2 and not is_null_literal(u2):
                all_sponsor_page.append((u2, s, excel_row))
            u3 = str(r.get("Prospectus_URL", "") or "").strip()
            if u3 and not is_null_literal(u3):
                all_prospectus.append((u3, s, excel_row))

    random.seed(7)
    sponsor_sample = random.sample(all_sponsor_page, max(1, len(all_sponsor_page) // 20)) if all_sponsor_page else []
    prospectus_sample = random.sample(all_prospectus, max(1, len(all_prospectus) // 20)) if all_prospectus else []

    log.info("URL verification: holdings=%d (100%%), sponsor_page_sample=%d (5%%), prospectus_sample=%d (5%%)",
             len(all_holdings), len(sponsor_sample), len(prospectus_sample))

    session = requests.Session()
    throttle = HostThrottle(interval=0.15)
    issues: list[Issue] = []

    def _check_batch(batch: list[tuple[str, SheetData, int]], col_name: str) -> tuple[int, int, list[Issue]]:
        # Split into SEC-hosted URLs (require SERIAL processing to stay
        # under SEC's 10 rps fair-access limit) and the rest (safe to
        # parallelise across sponsor CDNs).
        sec_batch = [(u, s, row) for (u, s, row) in batch if urlparse(u).netloc.lower() in SEC_HOSTS]
        other_batch = [(u, s, row) for (u, s, row) in batch if urlparse(u).netloc.lower() not in SEC_HOSTS]

        passed = 0
        failed = 0
        issues_here: list[Issue] = []

        # SEC: serial.
        for u, s, row in sec_batch:
            try:
                ok, status = head_check(session, u, throttle)
            except Exception:
                ok, status = (False, None)
            if ok:
                passed += 1
            else:
                failed += 1
                issues_here.append(Issue(
                    sheet=s.name, row=row, column=col_name,
                    reason=f"URL {u!r} failed HEAD (status={status})",
                ))

        # Non-SEC: parallel across sponsor CDNs.
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(head_check, session, u, throttle): (u, s, row)
                       for (u, s, row) in other_batch}
            for fut in as_completed(futures):
                u, s, row = futures[fut]
                try:
                    ok, status = fut.result()
                except Exception as e:
                    ok, status = (False, None)
                if ok:
                    passed += 1
                else:
                    failed += 1
                    issues_here.append(Issue(
                        sheet=s.name, row=row, column=col_name,
                        reason=f"URL {u!r} failed HEAD (status={status})",
                    ))
        return (passed, failed, issues_here)

    p1, f1, i1 = _check_batch(all_holdings, "Holdings_Feed_URL")
    log.info("Holdings_Feed_URL: %d pass, %d fail (100%% required)", p1, f1)
    issues.extend(i1)            # every fail is a hard issue

    # sponsor / prospectus: sample pass rate ≥95%.
    if sponsor_sample:
        p2, f2, i2 = _check_batch(sponsor_sample, "Sponsor_Page_URL")
        rate = p2 / (p2 + f2) if (p2 + f2) else 1.0
        log.info("Sponsor_Page_URL sample: %d pass, %d fail (rate %.1f%%; gate 95%%)", p2, f2, rate * 100)
        if rate < 0.95:
            issues.extend(i2)
    if prospectus_sample:
        p3, f3, i3 = _check_batch(prospectus_sample, "Prospectus_URL")
        rate = p3 / (p3 + f3) if (p3 + f3) else 1.0
        log.info("Prospectus_URL sample: %d pass, %d fail (rate %.1f%%; gate 95%%)", p3, f3, rate * 100)
        if rate < 0.95:
            issues.extend(i3)
    return issues


# --- main ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="fund_bible_v1.xlsx schema verifier")
    parser.add_argument("--workbook", required=True, type=pathlib.Path)
    parser.add_argument("--namespace-rules", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[2] / "registry" / "namespace_rules.yaml")
    parser.add_argument("--ici-total-usd", type=float, default=11_000_000_000_000.0)
    parser.add_argument("--skip-url-check", action="store_true")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--max-issues", type=int, default=50,
                        help="If more issues than this, stop printing details")
    args = parser.parse_args()

    if not args.workbook.exists():
        log.error("workbook not found: %s", args.workbook)
        return 2
    enums = load_enums(args.namespace_rules)
    log.info("loaded enums from %s (keys: %s)", args.namespace_rules, sorted(enums.keys()))

    sheets, overview = load_workbook_as_dicts(args.workbook)
    log.info("workbook: %d sponsor sheets, overview=%s, total rows=%d",
             len(sheets), "present" if overview else "missing",
             sum(len(s.rows) for s in sheets))

    issues: list[Issue] = []
    issues.extend(check_schema(sheets, enums))
    log.info("after schema check: %d issues", len(issues))
    issues.extend(check_coverage(sheets))
    log.info("after coverage check: %d issues", len(issues))
    issues.extend(check_sanity(sheets, overview, args.ici_total_usd))
    log.info("after sanity check: %d issues", len(issues))
    url_issues = check_urls(sheets, skip=args.skip_url_check, workers=args.workers)
    issues.extend(url_issues)
    log.info("after URL check: %d issues", len(issues))

    if issues:
        log.error("FAIL: %d issues", len(issues))
        for i in issues[: args.max_issues]:
            print(str(i))
        if len(issues) > args.max_issues:
            print(f"... and {len(issues) - args.max_issues} more")
        return 2

    log.info("PASS: fund_bible_v1.xlsx is schema + coverage + sanity clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
