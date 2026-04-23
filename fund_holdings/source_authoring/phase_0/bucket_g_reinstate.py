"""Bucket G reinstatement: re-verify ~93 intra-sheet content-verify failures
under relaxed row-count thresholds keyed by Vehicle_Subtype.

Policy (per Recovery Plan §7):
  - target_maturity_bond (iShares iBonds)     → min_rows = 3  (was 10)
  - leveraged_2x / leveraged_3x / inverse     → min_rows = 3  (was 10)
  - single_stock_leveraged / single_stock_inverse → min_rows = 1

Scope: only applies to funds already in excluded_funds.yaml with sheet in the
9 remediated sponsors (ishares, direxion, etc.). Does NOT loosen the gate for
Phase 1 new recoveries.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml
from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import (
    _find_header_row, _looks_like_id_column, _csv_ok, _xlsx_ok, _json_ok, _html_ok,
    MIN_BYTES_FOR_FORMAT,
)
from sponsor_discovery_v2 import fetch_curl_cffi, IMPERSONATE

PHASE0 = Path(__file__).parent
REMEDIATED_SHEETS = {"ishares", "state_street", "direxion", "first_trust", "global_x",
                      "vanguard", "graniteshares", "krane", "neos"}

# min_rows overrides per (vehicle_type, vehicle_subtype) or by name pattern
MIN_ROWS_OVERRIDES = {
    "target_maturity_bond": 3,
    "leveraged_2x": 3, "leveraged_3x": 3, "inverse": 3,
    "single_stock_leveraged": 1, "single_stock_inverse": 1,
    "buffered_outcome": 3,
    "options_strategy": 3,
}


def classify_relaxed_min_rows(v1_row: dict) -> int | None:
    """Given a v1_row dict, return relaxed min_rows if eligible, else None."""
    vst = (v1_row.get("Vehicle_Subtype") or "null").lower()
    if vst in MIN_ROWS_OVERRIDES:
        return MIN_ROWS_OVERRIDES[vst]
    # Name-based fallback: iBonds target-maturity
    fname = (v1_row.get("Fund_Name") or "").lower()
    if "ibonds" in fname or "target maturity" in fname:
        return 3
    if "2x" in fname or "3x" in fname:
        return 3
    if "single stock" in fname or "daily target" in fname:
        return 1
    return None


def relaxed_verify(body: bytes, fmt: str, min_rows: int) -> tuple[bool, str, dict]:
    """Like verify_holdings_url but with custom min_rows threshold."""
    if len(body) < MIN_BYTES_FOR_FORMAT.get(fmt, 100):
        return False, f"body_too_small:{len(body)}b", {}
    # Patch the module-level MIN_ROWS_DATA temporarily
    import content_verify as cv
    orig = cv.MIN_ROWS_DATA
    cv.MIN_ROWS_DATA = min_rows
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
        return False, f"verify_exc:{type(e).__name__}", {"err": str(e)[:120]}
    finally:
        cv.MIN_ROWS_DATA = orig


# iShares product map — needed for per-fund URL reconstruction
ISHARES_MAP_PATH = PHASE0 / "ishares_product_map.json"
ISHARES_MAP: dict[str, dict] = {}
if ISHARES_MAP_PATH.exists():
    with open(ISHARES_MAP_PATH) as f:
        ISHARES_MAP = json.load(f)


def build_url_for_excluded(row: dict) -> tuple[str, str] | None:
    """Given an excluded v1_row, attempt to construct a fresh URL based on its sheet."""
    sheet = row["sheet"]
    ticker = row["ticker"]
    tl = ticker.lower()
    tu = ticker.upper()
    if sheet == "ishares":
        # Use existing product map
        info = ISHARES_MAP.get(tu)
        if info:
            return (
                f"https://www.ishares.com/us/products/{info['product_id']}/"
                f"{info['slug']}/1467271812596.ajax?fileType=csv&fileName={tu}_holdings&dataType=fund",
                "csv",
            )
        return None
    if sheet == "state_street":
        return (
            f"https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{tl}.xlsx",
            "xlsx",
        )
    if sheet == "direxion":
        return (f"https://www.direxion.com/holdings/{tu}.csv", "csv")
    if sheet == "first_trust":
        return (
            f"https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker={tu}",
            "html_table",
        )
    if sheet == "global_x":
        return (f"https://www.globalxetfs.com/funds/{tl}/", "html_table")
    if sheet == "vanguard":
        return (
            f"https://api.vanguard.com/rs/ire/01/ind/fund/{tu}/portfolio-holding/stock.json",
            "json",
        )
    if sheet == "graniteshares":
        return (f"https://graniteshares.com/institutional/us/en-us/etfs/{tl}/", "html_table")
    if sheet == "krane":
        return (f"https://kraneshares.com/etf/{tl}/", "html_table")
    if sheet == "neos":
        return (f"https://neosfunds.com/etfs/{tl}/", "html_table")
    return None


def main():
    excl_path = PHASE0 / "excluded_funds.yaml"
    with open(excl_path) as f:
        data = yaml.safe_load(f)

    # Candidates: intra-sheet (remediated sponsor) + relaxed eligibility
    candidates: list[dict] = []
    for r in data["records"]:
        if r["sheet"] not in REMEDIATED_SHEETS:
            continue
        mr = classify_relaxed_min_rows(r["v1_row"])
        if mr is None:
            continue
        url_fmt = build_url_for_excluded(r)
        if not url_fmt:
            continue
        url, fmt = url_fmt
        candidates.append({**r, "relaxed_min_rows": mr, "url": url, "fmt": fmt})

    print(f"[bucket_g] {len(candidates)} intra-sheet candidates eligible for relaxed re-verify")

    # Fetch + re-verify under relaxed gate
    reinstated: list[dict] = []
    still_fail: list[dict] = []
    session = cc_requests.Session(impersonate=IMPERSONATE)
    # Vanguard needs Referer
    for c in candidates:
        url, fmt, mr = c["url"], c["fmt"], c["relaxed_min_rows"]
        referer = None
        if "api.vanguard.com" in url:
            referer = url.replace("/rs/ire/01/ind/fund/", "/rs/ire/01/ind/fund/").replace(
                "/portfolio-holding/stock.json", ""
            )
            referer = (
                f"https://investor.vanguard.com/investment-products/etfs/profile/{c['ticker'].lower()}"
            )
        status, final_url, body, ct = fetch_curl_cffi(session, url, referer=referer)
        if status != 200:
            still_fail.append({**c, "reason": f"http_{status}"})
            continue
        ok, reason, info = relaxed_verify(body, fmt, mr)
        if ok:
            reinstated.append({**c, "verify": reason, "info": info,
                               "data_rows": info.get("data_rows")})
        else:
            still_fail.append({**c, "reason": reason})

    # Summary
    print(f"[bucket_g] reinstated: {len(reinstated)} / {len(candidates)}")
    print(f"[bucket_g] still_fail: {len(still_fail)}")

    out_path = PHASE0 / "bucket_g_reinstated.json"
    with open(out_path, "w") as f:
        json.dump({
            "reinstated": reinstated,
            "still_fail": still_fail,
            "policy": MIN_ROWS_OVERRIDES,
            "scope_note": "Applies only to funds already in excluded_funds.yaml within REMEDIATED_SHEETS.",
        }, f, indent=2, default=str)
    print(f"[bucket_g] wrote {out_path}")

    # Write forward-looking precedent note
    notes_path = PHASE0 / "notes.md"
    with open(notes_path, "a") as f:
        f.write(f"""

---
## Bucket G — Relaxed Row-Count Gate Applied ({time.strftime('%Y-%m-%d')})

As part of Phase 0 Sponsor Recovery (Revision 2 §7):

Relaxed `MIN_ROWS_DATA` per Vehicle_Subtype for intra-sheet re-verification of
previously-excluded rows in REMEDIATED_SHEETS only. Thresholds:

- `target_maturity_bond` (iShares iBonds-style ladders): 3 rows
- `leveraged_2x`, `leveraged_3x`, `inverse` (Direxion etc.): 3 rows
- `single_stock_leveraged`, `single_stock_inverse`: 1 row
- `buffered_outcome`, `options_strategy`: 3 rows

**Execution result:** {len(reinstated)} of {len(candidates)} intra-sheet candidates passed under
the relaxed gate.

**Forward-looking precedent:** this relaxation applies ONLY to the Bucket G
recovery. Whether future Phase 0 work admits these vehicle_subtypes at the
relaxed threshold is a Phase 1+ governance decision. Do NOT codify in
PHASE_0_REMEDIATION_TASKBOARD.md — recorded here only as execution record.
""")


if __name__ == "__main__":
    main()
