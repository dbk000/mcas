"""Rebuild v2 — like rebuild_bible.py but handles per-ticker URL maps for sponsors
whose holdings URL varies per instrument (e.g., Invesco uses CUSIP).

Inputs beyond v1:
  - invesco_verified_urls.json — pre-computed per-ticker holdings URLs for Invesco
  - sponsor_endpoints_v2_candidates.jsonl — template-based endpoints (schwab, etc.)
  - bucket_g_reinstated.json — intra-sheet reinstatements
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl import load_workbook
from curl_cffi import requests as cc_requests

PHASE0 = Path(__file__).parent
PROJECT = PHASE0.parent.parent

sys.path.insert(0, str(PHASE0))
from sponsor_discovery_v2 import (
    expand, fetch_curl_cffi, verify_body, guess_format_from_url, IMPERSONATE,
)

BIBLE_COLUMNS = [
    "#", "Ticker", "Fund_Name", "Sponsor", "Exchange", "Inception_Date",
    "AUM_USD", "AUM_AsOf_Date", "Shares_Outstanding", "Expense_Ratio_Bps",
    "Vehicle_Type", "Vehicle_Subtype", "Benchmark_Name", "Benchmark_Provider",
    "Replication_Method", "Rebalance_Frequency_Prospectus",
    "Rebalance_Trigger_Rules", "Creation_Unit_Size", "Holdings_Feed_URL",
    "Holdings_Feed_Format", "Holdings_Feed_Lag_Hours", "Sponsor_Page_URL",
    "Prospectus_URL", "Has_Derivatives_Overlay", "Internal_Crossing_Disclosed",
    "Currency_Hedged", "Fund_Of_Funds", "Primary_Market_Cap_Tier",
    "Geographic_Focus", "Notes", "Lineage_Authored_By", "Lineage_Authored_At",
    "Lineage_Remediated_By", "Lineage_Remediated_At",
]

LINEAGE_RECOVERED_BY = "phase_0_sponsor_recovery_cc"


def utc_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_bible_rows(path: Path) -> dict[str, list[dict]]:
    """Load bible → sheet → list-of-row-dicts."""
    # Handle non-.xlsx path
    if not str(path).endswith(".xlsx"):
        import shutil, tempfile
        tmp_copy = Path(tempfile.mktemp(suffix=".xlsx"))
        shutil.copy(path, tmp_copy)
        path = tmp_copy
    wb = load_workbook(path, read_only=True, data_only=True)
    out: dict[str, list[dict]] = {}
    for sn in wb.sheetnames:
        if sn.lower() == "overview":
            continue
        sh = wb[sn]
        rows = list(sh.iter_rows(values_only=True))
        if len(rows) < 5:
            continue
        header = rows[3]
        data = []
        for r in rows[4:]:
            if not any(c is not None for c in r):
                continue
            d = {h: v for h, v in zip(header, r) if h}
            d.pop("#", None)
            data.append(d)
        out[sn] = data
    wb.close()
    return out


def load_confirmed_endpoints(path: Path) -> dict[str, dict]:
    """sponsor → {template, format}. First confirmed pattern wins."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            s = d["sponsor"]
            if s in out:
                continue
            patterns = d.get("patterns") or []
            if patterns:
                p = patterns[0]
                out[s] = {"template": p["template"], "format": p["format"]}
    return out


def load_excluded_funds(path: Path) -> list[dict]:
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["records"]


def load_bucket_g(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)["reinstated"]


def load_invesco_urls(path: Path) -> dict[str, dict]:
    """Return {ticker: {"url": str, "format": str, "cusip": str, "data_rows": int}}"""
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    return data["verified"]


def load_url_map(path: Path) -> dict[str, dict]:
    """Generic per-ticker URL map loader: {"verified": {ticker: {url, ...}}}."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    return data.get("verified", {})


def verify_fresh_url(session, url: str, fmt: str, referer: str = None) -> tuple[bool, str]:
    status, final_url, body, ct = fetch_curl_cffi(session, url, referer=referer)
    if status != 200:
        return False, f"http_{status}"
    fmt_real = guess_format_from_url(final_url, ct, body)
    ok, reason, _ = verify_body(body, fmt_real or fmt)
    return bool(ok), reason


def build_sheet_from_template(slug: str, endpoint: dict, excluded_rows: list[dict],
                               session, source_sheet: str = None,
                               per_ticker_verify: bool = True) -> list[dict]:
    """Template-based (ticker substitution)."""
    tmpl = endpoint["template"]
    fmt = endpoint["format"]
    src = source_sheet or slug
    verified: list[dict] = []
    failed: list[str] = []

    for er in excluded_rows:
        if er["sheet"] != src:
            continue
        if source_sheet:
            fname_low = er["fund_name"].lower()
            tokens = {"yieldmax": "yieldmax", "roundhill": "roundhill",
                       "bitwise": "bitwise", "defiance": "defiance"}
            if slug in tokens and tokens[slug] not in fname_low:
                continue
        ticker = er["ticker"]
        v1 = dict(er["v1_row"])
        url = expand(tmpl, ticker)
        if per_ticker_verify:
            ok, reason = verify_fresh_url(session, url, fmt)
            if not ok:
                failed.append(f"{ticker}:{reason}")
                continue
        v1["Holdings_Feed_URL"] = url
        v1["Holdings_Feed_Format"] = fmt
        v1["Sponsor_Page_URL"] = url
        v1["Lineage_Remediated_By"] = LINEAGE_RECOVERED_BY
        v1["Lineage_Remediated_At"] = utc_iso()
        v1["Notes"] = "recovered_via_phase_0_sponsor_recovery"
        verified.append(v1)
    if failed:
        print(f"    [{slug}] dropped {len(failed)}/{len(failed) + len(verified)} failing "
              f"(first 3: {failed[:3]})")
    return verified


def build_sheet_from_url_map(slug: str, url_map: dict[str, dict],
                              excluded_rows: list[dict],
                              fmt: str = "json",
                              sponsor_page_tmpl: str | None = None,
                              notes_tmpl: str = "recovered_via_phase_0_sponsor_recovery") -> list[dict]:
    """URL-map-based (one URL per ticker)."""
    verified: list[dict] = []
    skipped = 0
    for er in excluded_rows:
        if er["sheet"] != slug:
            continue
        ticker = er["ticker"]
        info = url_map.get(ticker)
        if not info or not info.get("ok"):
            skipped += 1
            continue
        v1 = dict(er["v1_row"])
        v1["Holdings_Feed_URL"] = info["url"]
        v1["Holdings_Feed_Format"] = info.get("format", fmt)
        if sponsor_page_tmpl:
            v1["Sponsor_Page_URL"] = expand(sponsor_page_tmpl, ticker)
        else:
            v1["Sponsor_Page_URL"] = info["url"]
        v1["Lineage_Remediated_By"] = LINEAGE_RECOVERED_BY
        v1["Lineage_Remediated_At"] = utc_iso()
        # Include CUSIP or other ID if present
        extra = ""
        if info.get("cusip"):
            extra = f" (cusip={info['cusip']})"
        elif info.get("portfolioNumber"):
            extra = f" (portfolioNumber={info['portfolioNumber']})"
        v1["Notes"] = notes_tmpl + extra
        verified.append(v1)
    if skipped:
        print(f"    [{slug}] url_map: admitted {len(verified)}, skipped {skipped}")
    return verified


def write_bible(out_path: Path, all_sheets: dict[str, list[dict]]):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    overview = wb.create_sheet("Overview")
    overview.cell(row=1, column=1, value="MCAS Phase 0 — Fund Bible Overview").font = Font(bold=True, size=14)
    overview.cell(row=2, column=1,
        value="Summary of sponsor-family sheets after Phase 0 Top-5 Recovery. All rows have content-verified Holdings_Feed_URL per §7.2.")
    overview.cell(row=3, column=1, value="")
    headers = ["Sheet_Name", "Sponsor_Slug", "Row_Count", "Sum_AUM_USD",
               "Count_With_AUM", "Pct_With_Sponsor_Feed", "Edgar_Fallback_Count"]
    for ci, h in enumerate(headers, 1):
        overview.cell(row=4, column=ci, value=h).font = Font(bold=True)
    ri = 5
    total_rows = 0
    total_aum = 0.0
    for sheet_name in sorted(all_sheets):
        rows = all_sheets[sheet_name]
        sheet_aum = sum(r.get("AUM_USD") or 0 for r in rows if isinstance(r.get("AUM_USD"), (int, float)))
        count_with_aum = sum(1 for r in rows if isinstance(r.get("AUM_USD"), (int, float)) and r["AUM_USD"] > 0)
        fallback_count = sum(1 for r in rows if r.get("Holdings_Feed_Format") == "edgar_fallback")
        pct_sponsor_feed = round(100 * (len(rows) - fallback_count) / max(1, len(rows)), 1)
        overview.cell(row=ri, column=1, value=sheet_name)
        overview.cell(row=ri, column=2, value=sheet_name)
        overview.cell(row=ri, column=3, value=len(rows))
        overview.cell(row=ri, column=4, value=round(sheet_aum, 2))
        overview.cell(row=ri, column=5, value=count_with_aum)
        overview.cell(row=ri, column=6, value=pct_sponsor_feed)
        overview.cell(row=ri, column=7, value=fallback_count)
        ri += 1
        total_rows += len(rows)
        total_aum += sheet_aum
    overview.cell(row=ri, column=1, value="Total").font = Font(bold=True)
    overview.cell(row=ri, column=3, value=total_rows).font = Font(bold=True)
    overview.cell(row=ri, column=4, value=round(total_aum, 2)).font = Font(bold=True)

    for sheet_name in sorted(all_sheets):
        rows = all_sheets[sheet_name]
        rows_sorted = sorted(rows, key=lambda r: (-(r.get("AUM_USD") or 0), r.get("Ticker", "")))
        ws = wb.create_sheet(sheet_name[:31])
        ws.cell(row=1, column=1, value=f"Sheet: {sheet_name}").font = Font(bold=True, size=14)
        ws.cell(row=2, column=1, value=f"Row count: {len(rows_sorted)}")
        ws.cell(row=3, column=1,
            value=("Generated by MCAS Phase 0 Sponsor Recovery (phase_0_sponsor_recovery_cc). "
                   "Header at row 4, data from row 5. 34 columns."))
        for ci, col in enumerate(BIBLE_COLUMNS, start=1):
            c = ws.cell(row=4, column=ci, value=col)
            c.font = Font(bold=True)
            c.fill = PatternFill(start_color="FFE8E8E8", end_color="FFE8E8E8", fill_type="solid")
        for ri2, r in enumerate(rows_sorted, start=5):
            ws.cell(row=ri2, column=1, value=ri2 - 4)
            for ci, col in enumerate(BIBLE_COLUMNS, start=1):
                if col == "#":
                    continue
                v = r.get(col)
                if v is None:
                    v = "null"
                if isinstance(v, bool):
                    v = "True" if v else "False"
                ws.cell(row=ri2, column=ci, value=v)
    wb.save(out_path)
    print(f"[rebuild_v2] wrote {out_path}  total_rows={total_rows}  aum=${total_aum/1e12:.2f}T")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bible", default=str(PROJECT / "ground_truth" / "fund_bible_v1.xlsx"))
    p.add_argument("--endpoints", default=str(PHASE0 / "sponsor_endpoints_v2_candidates.jsonl"))
    p.add_argument("--excluded", default=str(PHASE0 / "excluded_funds.yaml"))
    p.add_argument("--bucket-g", default=str(PHASE0 / "bucket_g_reinstated.json"))
    p.add_argument("--invesco-urls", default=str(PHASE0 / "invesco_verified_urls.json"))
    p.add_argument("--dimensional-urls", default=str(PHASE0 / "recovered_dimensional_urls.json"))
    p.add_argument("--out-tmp", default=str(PROJECT / "ground_truth" / "fund_bible_v1.xlsx.tmp"))
    p.add_argument("--state", default=str(PROJECT / "PHASE_0_SPONSOR_RECOVERY_STATE.md"))
    args = p.parse_args()

    # 1) Settled bible (current, includes prior 5 sponsors)
    sheets = load_bible_rows(Path(args.bible))
    print(f"[rebuild_v2] current bible sheets: {list(sheets.keys())}")
    settled_count = sum(len(rs) for rs in sheets.values())

    # 2) Excluded funds (original v1 records)
    excluded_rows = load_excluded_funds(Path(args.excluded))
    excl_by_sheet: dict[str, list[dict]] = defaultdict(list)
    for r in excluded_rows:
        excl_by_sheet[r["sheet"]].append(r)

    # 3) Invesco per-ticker URL map
    invesco_map = load_invesco_urls(Path(args.invesco_urls))
    print(f"[rebuild_v2] invesco verified urls: {len(invesco_map)}")

    # 3b) Dimensional per-ticker URL map (from session-replay recovery)
    dim_map = load_url_map(Path(args.dimensional_urls))
    print(f"[rebuild_v2] dimensional verified urls: {len(dim_map)}")

    # 4) Add invesco sheet (URL-map-based)
    session = cc_requests.Session(impersonate=IMPERSONATE)
    recovered_summary = []
    if invesco_map and "invesco" not in sheets:
        built = build_sheet_from_url_map(
            "invesco", invesco_map, excluded_rows,
            fmt="json",
            sponsor_page_tmpl="https://www.invesco.com/us/financial-products/etfs/product-detail?audienceType=Investor&ticker={TICKER}",
            notes_tmpl="recovered_via_phase_0_sponsor_recovery",
        )
        if built:
            sheets["invesco"] = built
            recovered_summary.append(("invesco", len(built),
                                       "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/{CUSIP}/holdings/fund?idType=cusip&productType=ETF"))
            print(f"[rebuild_v2] invesco: admitted {len(built)} funds")

    # 4b) Add dimensional sheet (URL-map-based, per-ticker CSV)
    if dim_map and "dimensional" not in sheets:
        built = build_sheet_from_url_map(
            "dimensional", dim_map, excluded_rows,
            fmt="csv",
            sponsor_page_tmpl="https://www.dimensional.com/us-en/funds/{ticker_lower}",
            notes_tmpl="recovered_via_session_replay_pipeline",
        )
        if built:
            sheets["dimensional"] = built
            recovered_summary.append(("dimensional", len(built),
                                       "https://tools-blob.dimensional.com/etf/{YYYYMMDD}/{TICKER}.csv"))
            print(f"[rebuild_v2] dimensional: admitted {len(built)} funds")

    # 5) Confirmed template-based (from jsonl)
    confirmed = load_confirmed_endpoints(Path(args.endpoints))
    print(f"[rebuild_v2] template-based confirmed: {list(confirmed.keys())}")
    SUBADV_SOURCE = {"yieldmax": "tidal", "roundhill": "tidal",
                      "bitwise": "tidal", "defiance": "tidal"}
    for slug, endpoint in confirmed.items():
        if slug in sheets:
            continue  # already present (either settled or invesco just added)
        src_sheet = SUBADV_SOURCE.get(slug, slug)
        sheet_excluded = excluded_rows if slug in SUBADV_SOURCE else excl_by_sheet.get(slug, [])
        if not sheet_excluded:
            continue
        print(f"[rebuild_v2] processing {slug}...")
        built = build_sheet_from_template(
            slug, endpoint, sheet_excluded, session,
            source_sheet=src_sheet if slug in SUBADV_SOURCE else None,
            per_ticker_verify=True,
        )
        if built:
            sheets[slug] = built
            recovered_summary.append((slug, len(built), endpoint["template"]))
            print(f"[rebuild_v2] {slug}: admitted {len(built)} funds")

    # 6) Write
    write_bible(Path(args.out_tmp), sheets)

    total = sum(len(rs) for rs in sheets.values())
    print(f"\n[rebuild_v2] bible total: {total}")
    for slug, n, tmpl in sorted(recovered_summary):
        print(f"  {slug:28s} admitted={n}  tmpl={tmpl[:80]}")


if __name__ == "__main__":
    main()
