"""Content-based verification of Holdings_Feed_URL per §7.2.

Status 200 is NOT sufficient. Response body must parse as declared format
and contain identifier-bearing holdings data (≥10 position rows with a
ticker/isin/cusip/security_id column).

Usage:
  from content_verify import verify_holdings_url
  ok, reason, info = verify_holdings_url(url, declared_format, session)
"""
from __future__ import annotations

import csv
import io
import json
import re
import tempfile
from typing import Optional

from bs4 import BeautifulSoup

from http_client import fetch, new_session

# Identifier columns any format must expose. Strict whole-token match, not loose substring.
# "Fund Holdings as of" must NOT match — only real identifier headers count.
ID_KEYS_STRICT = (
    "ticker", "symbol", "cusip", "isin", "sedol", "identifier",
    "security_id", "secid", "figi", "bloomberg",
    "bb_ticker", "stock_ticker", "stockticker", "accountticker",
    "security", "name of issuer", "issuer",
)
# ≥10 rows for csv/xlsx/html_table/json-array
MIN_ROWS_DATA = 10

MIN_BYTES_FOR_FORMAT = {
    "csv": 1024,       # ≥1 KB
    "xlsx": 5 * 1024,  # ≥5 KB
    "json": 512,
    "html_table": 2 * 1024,
}

EXCLUDED_FORMATS = {"pdf", "other", "edgar_fallback"}


def _looks_like_id_column(header: str) -> bool:
    """Accept a header cell only if it contains an id-like token as a whole word
    or recognised short token. 'Fund Holdings as of' must NOT match."""
    if not header:
        return False
    h = header.lower().strip()
    # Explicit short forms
    if h in ("ticker", "symbol", "cusip", "isin", "sedol", "figi"):
        return True
    # Word-boundary match for the strict set
    tokens = re.findall(r"[a-z_]+", h)
    token_set = set(tokens)
    STRICT_WORDS = {"ticker", "symbol", "cusip", "isin", "sedol", "figi",
                    "identifier", "secid", "bloomberg", "stockticker",
                    "accountticker", "issuer"}
    if token_set & STRICT_WORDS:
        return True
    # Phrase match
    PHRASES = ("security id", "security_id", "stock ticker", "account ticker",
               "name of issuer", "bb ticker", "bb_ticker", "security name",
               "bond ticker")
    for p in PHRASES:
        if p in h:
            return True
    # Generic "name" column only qualifies if h is exactly "name" or starts with "security"
    if h == "name" or h.startswith("security name"):
        return True
    return False


def _find_header_row(rows: list, min_cols: int = 4) -> int:
    """Pick the best header row: must have ≥ min_cols non-empty cells AND
    at least one id-like column. Prefer the row whose NEXT row has data.
    This filters out metadata rows like 'Ticker Symbol: SPY' (only 2 cells).
    """
    best = -1
    for i, row in enumerate(rows):
        nonblank = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if len(nonblank) < min_cols:
            continue
        id_hits = sum(1 for c in nonblank if _looks_like_id_column(c))
        if id_hits == 0:
            continue
        # Check that the row below also has data (≥ min_cols non-empty cells)
        if i + 1 < len(rows):
            below = [str(c).strip() for c in rows[i+1] if c is not None and str(c).strip()]
            if len(below) < min_cols - 1:
                continue
        best = i
        break  # first qualifying row wins
    return best


def _csv_ok(body: bytes) -> tuple[bool, str, dict]:
    """Parse as CSV. ≥10 data rows. ≥1 identifier column."""
    text = body.decode("utf-8", errors="replace").lstrip("﻿")
    rdr = csv.reader(io.StringIO(text))
    rows = []
    for i, row in enumerate(rdr):
        rows.append(row)
        if i > 5000:
            break
    header_idx = _find_header_row(rows)
    if header_idx < 0:
        return False, "no_valid_header_row_with_id_column", {"rows_scanned": len(rows)}
    data_rows = rows[header_idx + 1:]
    non_blank = [r for r in data_rows if any(c.strip() for c in r) and len([c for c in r if c.strip()]) >= 3]
    if len(non_blank) < MIN_ROWS_DATA:
        return False, f"too_few_rows ({len(non_blank)}<{MIN_ROWS_DATA})", {
            "header_idx": header_idx, "data_rows": len(non_blank)
        }
    return True, "ok", {
        "header_idx": header_idx,
        "header": rows[header_idx][:14],
        "data_rows": len(non_blank),
    }


def _xlsx_ok(body: bytes) -> tuple[bool, str, dict]:
    """Parse as XLSX. ≥1 sheet with ≥10 data rows + id header."""
    try:
        import openpyxl
    except ImportError:
        return False, "openpyxl_not_installed", {}
    # Write to temp file (openpyxl needs file path or stream)
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tf:
            tf.write(body)
            tf.flush()
            wb = openpyxl.load_workbook(tf.name, read_only=True, data_only=True)
            for sname in wb.sheetnames:
                sh = wb[sname]
                rows = []
                for i, r in enumerate(sh.iter_rows(values_only=True)):
                    rows.append(list(r))
                    if i > 5000:
                        break
                header_idx = _find_header_row(rows)
                if header_idx < 0:
                    continue
                data_rows = rows[header_idx + 1:]
                non_blank = [
                    r for r in data_rows
                    if r and len([c for c in r if c is not None and str(c).strip()]) >= 3
                ]
                if len(non_blank) >= MIN_ROWS_DATA:
                    return True, "ok", {
                        "sheet": sname, "header_idx": header_idx,
                        "header": [str(c) if c is not None else "" for c in rows[header_idx][:14]],
                        "data_rows": len(non_blank),
                    }
            return False, "no_sheet_with_id_column_and_10_rows", {"sheets": wb.sheetnames[:5]}
    except Exception as e:
        return False, f"xlsx_parse_error:{type(e).__name__}", {"err": str(e)[:150]}


def _json_ok(body: bytes) -> tuple[bool, str, dict]:
    try:
        doc = json.loads(body)
    except Exception as e:
        return False, f"json_parse_error:{type(e).__name__}", {"err": str(e)[:100]}

    def _find_id_array(obj, depth=0, path=""):
        if depth > 6:
            return None
        if isinstance(obj, list):
            if len(obj) >= MIN_ROWS_DATA:
                # Array of dicts with id-like key?
                if obj and isinstance(obj[0], dict):
                    keys = list(obj[0].keys())
                    if any(_looks_like_id_column(k) for k in keys):
                        return path, keys, len(obj)
                # Array of arrays (first might be header)
                if obj and isinstance(obj[0], list) and len(obj) >= MIN_ROWS_DATA + 1:
                    if any(_looks_like_id_column(str(c)) for c in obj[0]):
                        return path, obj[0][:12], len(obj) - 1
            # recurse into elements
            for i, it in enumerate(obj[:5]):
                r = _find_id_array(it, depth + 1, f"{path}[{i}]")
                if r:
                    return r
        elif isinstance(obj, dict):
            for k, v in obj.items():
                r = _find_id_array(v, depth + 1, f"{path}.{k}")
                if r:
                    return r
        return None

    hit = _find_id_array(doc)
    if not hit:
        return False, "no_id_array_in_json", {}
    path, keys, n = hit
    return True, "ok", {"path": path, "keys": list(keys)[:12], "data_rows": n}


def _html_ok(body: bytes) -> tuple[bool, str, dict]:
    try:
        soup = BeautifulSoup(body, "lxml")
    except Exception:
        soup = BeautifulSoup(body, "html.parser")
    tables = soup.find_all("table")
    for t in tables:
        rows = t.find_all("tr")
        if len(rows) < MIN_ROWS_DATA + 1:
            continue
        # Check header row for id-like column
        header = rows[0].find_all(["th", "td"])
        header_text = [(h.get_text() or "").strip() for h in header]
        if not any(_looks_like_id_column(h) for h in header_text):
            continue
        data_rows = [r for r in rows[1:] if r.find_all(["td", "th"])]
        if len(data_rows) >= MIN_ROWS_DATA:
            return True, "ok", {"header": header_text[:12], "data_rows": len(data_rows)}
    return False, "no_html_table_with_id_column_and_10_rows", {
        "n_tables": len(tables),
    }


def verify_holdings_url(
    url: str,
    declared_format: str,
    session=None,
    referer: Optional[str] = None,
    expected_ticker: Optional[str] = None,
) -> tuple[bool, str, dict]:
    """Content-verify `url`. Returns (ok, reason, info)."""
    declared_format = (declared_format or "").strip().lower()
    if declared_format in EXCLUDED_FORMATS:
        return False, f"format_excluded:{declared_format}", {}
    sess = session or new_session()
    status, final_url, body, ct = fetch(sess, url, referer=referer)
    if status != 200:
        return False, f"http_{status}", {"final_url": final_url, "content_type": ct[:80]}
    if len(body) < MIN_BYTES_FOR_FORMAT.get(declared_format, 100):
        return False, f"body_too_small:{len(body)}b", {"ct": ct[:80]}
    # If sponsor silently returned HTML error page when CSV/XLSX was expected
    ct_low = ct.lower()
    if declared_format == "csv":
        if "text/html" in ct_low and b"<html" in body[:200].lower():
            return False, "html_returned_for_csv_request", {"ct": ct[:80]}
        ok, reason, info = _csv_ok(body)
    elif declared_format == "xlsx":
        if "text/html" in ct_low and b"<html" in body[:200].lower():
            return False, "html_returned_for_xlsx_request", {"ct": ct[:80]}
        ok, reason, info = _xlsx_ok(body)
    elif declared_format == "json":
        ok, reason, info = _json_ok(body)
    elif declared_format == "html_table":
        ok, reason, info = _html_ok(body)
    else:
        return False, f"unsupported_format:{declared_format}", {"ct": ct[:80]}
    info["final_url"] = final_url
    info["content_type"] = ct[:80]
    info["body_bytes"] = len(body)
    if expected_ticker and ok:
        # Warn-only: check ticker appears in first 4KB of body
        preview = body[:8192].decode("utf-8", errors="replace").upper()
        if expected_ticker.upper() not in preview:
            info["ticker_not_in_preview"] = True
    return ok, reason, info


if __name__ == "__main__":
    # Smoke test
    sess = new_session()
    tests = [
        ("iShares IVV", "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund", "csv"),
        ("SSGA SPY", "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx", "xlsx"),
        ("Direxion SOXL", "https://www.direxion.com/holdings/SOXL.csv", "csv"),
        ("VTI profile (html_table — expect fail)", "https://investor.vanguard.com/investment-products/etfs/profile/vti", "html_table"),
    ]
    for name, url, fmt in tests:
        ok, reason, info = verify_holdings_url(url, fmt, sess)
        print(f"{name:45s} ok={ok!s:6s} reason={reason:50s} info={str(info)[:100]}")
