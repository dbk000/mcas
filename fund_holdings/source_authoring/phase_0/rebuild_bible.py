"""Rebuild fund_bible_v1.xlsx integrating Phase 0 recovery results.

Reads:
  - ground_truth/fund_bible_v1.xlsx                  (806 settled rows, 9 sponsors)
  - source_authoring/phase_0/sponsor_endpoints_v2_candidates.jsonl (confirmed new endpoints)
  - source_authoring/phase_0/excluded_funds.yaml     (1,757 excluded v1 rows to re-import)
  - source_authoring/phase_0/bucket_g_reinstated.json (intra-sheet re-verifications)
  - source_authoring/phase_0/logs/phase_0_metadata_v2.jsonl (prospectus URL upgrades)

Writes:
  - ground_truth/fund_bible_v1.xlsx.tmp     (all rows merged + verified)

Does NOT perform atomic rename — caller (run_phase_0_recovery.sh) does that
after the full §7 verification suite passes.
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
PROJECT = PHASE0.parent.parent  # fund_holdings/

sys.path.insert(0, str(PHASE0))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok, MIN_BYTES_FOR_FORMAT  # type: ignore
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
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def load_settled_bible(path: Path) -> dict[str, list[dict]]:
    """Load v1 bible into sheet → list-of-row-dicts."""
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


def sheet_slug_from_sponsor_slug(slug: str) -> str:
    """Map sponsor_slug (snake_case) → bible sheet name (same convention)."""
    return slug


def verify_fresh_url(session, url: str, fmt: str, referer: str = None) -> tuple[bool, str]:
    """Content-verify a single URL."""
    status, final_url, body, ct = fetch_curl_cffi(session, url, referer=referer)
    if status != 200:
        return False, f"http_{status}"
    fmt_real = guess_format_from_url(final_url, ct, body)
    ok, reason, _ = verify_body(body, fmt_real)
    return bool(ok), reason


def build_sheet_for_new_sponsor(
    slug: str, endpoint: dict, excluded_rows: list[dict], session,
    source_sheet: str = None,
    per_ticker_verify: bool = True,
) -> list[dict]:
    """Apply endpoint template to excluded rows, per-ticker verify each.

    If source_sheet is provided, pull rows from that sheet (subadviser case:
    yieldmax rows live in `tidal` sheet but map to `yieldmax` sheet).
    """
    tmpl = endpoint["template"]
    fmt = endpoint["format"]
    src = source_sheet or slug
    verified: list[dict] = []
    failed: list[str] = []

    for er in excluded_rows:
        if er["sheet"] != src:
            continue
        # Subadviser filtering: only include rows whose fund_name matches
        if source_sheet:
            fname_low = er["fund_name"].lower()
            tokens = {
                "yieldmax": "yieldmax",
                "roundhill": "roundhill",
                "bitwise": "bitwise",
                "defiance": "defiance",
            }
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
        print(f"    [{slug}] dropped {len(failed)}/{len(failed) + len(verified)} failing tickers "
              f"(first 3: {failed[:3]})")
    return verified


def sample_verify_sponsor(slug: str, endpoint: dict, excluded_rows: list[dict], session) -> tuple[bool, list[dict]]:
    """Sample-verify on 3 rows of the sponsor. Return (sponsor_ok, failed_samples)."""
    rows = [er for er in excluded_rows if er["sheet"] == slug][:3]
    tmpl = endpoint["template"]
    fmt = endpoint["format"]
    failures = []
    for er in rows:
        url = expand(tmpl, er["ticker"])
        ok, reason = verify_fresh_url(session, url, fmt)
        if not ok:
            failures.append({"ticker": er["ticker"], "reason": reason, "url": url})
    return (len(failures) == 0), failures


def write_bible(out_path: Path, all_sheets: dict[str, list[dict]]):
    """Write consolidated bible to out_path (tmp)."""
    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # Compute overview first
    overview = wb.create_sheet("Overview")
    overview.cell(row=1, column=1, value="MCAS Phase 0 — Fund Bible Overview").font = Font(bold=True, size=14)
    overview.cell(row=2, column=1,
        value="Summary of sponsor-family sheets after Phase 0 recovery. All rows have a content-verified Holdings_Feed_URL per §7.2.")
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
        overview.cell(row=ri, column=2, value=sheet_name)  # slug == sheet name
        overview.cell(row=ri, column=3, value=len(rows))
        overview.cell(row=ri, column=4, value=round(sheet_aum, 2))
        overview.cell(row=ri, column=5, value=count_with_aum)
        overview.cell(row=ri, column=6, value=pct_sponsor_feed)
        overview.cell(row=ri, column=7, value=fallback_count)
        ri += 1
        total_rows += len(rows)
        total_aum += sheet_aum
    # Total row
    overview.cell(row=ri, column=1, value="Total").font = Font(bold=True)
    overview.cell(row=ri, column=3, value=total_rows).font = Font(bold=True)
    overview.cell(row=ri, column=4, value=round(total_aum, 2)).font = Font(bold=True)

    # Each sponsor sheet
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
    print(f"[rebuild] wrote {out_path}  total_rows={total_rows}  aum=${total_aum/1e12:.2f}T")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bible", default=str(PROJECT / "ground_truth" / "fund_bible_v1.xlsx"))
    p.add_argument("--endpoints", default=str(PHASE0 / "sponsor_endpoints_v2_candidates.jsonl"))
    p.add_argument("--excluded", default=str(PHASE0 / "excluded_funds.yaml"))
    p.add_argument("--bucket-g", default=str(PHASE0 / "bucket_g_reinstated.json"))
    p.add_argument("--metadata", default=str(PHASE0 / "logs" / "phase_0_metadata_v2.jsonl"))
    p.add_argument("--out-tmp", default=str(PROJECT / "ground_truth" / "fund_bible_v1.xlsx.tmp"))
    p.add_argument("--state", default=str(PROJECT / "PHASE_0_SPONSOR_RECOVERY_STATE.md"))
    p.add_argument("--sample-verify", action="store_true",
                   help="Sample-verify each new sponsor (3 tickers) before admitting")
    args = p.parse_args()

    # 1) Load settled bible (9 sponsors)
    sheets = load_settled_bible(Path(args.bible))
    print(f"[rebuild] settled sheets: {list(sheets.keys())}")
    settled_count = sum(len(rs) for rs in sheets.values())

    # 2) Load confirmed new endpoints
    confirmed = load_confirmed_endpoints(Path(args.endpoints))
    print(f"[rebuild] confirmed new sponsor endpoints: {len(confirmed)}")
    for s, e in sorted(confirmed.items()):
        print(f"    {s:28s} {e['format']:10s} {e['template'][:100]}")

    # 3) Load excluded funds
    excluded_rows = load_excluded_funds(Path(args.excluded))
    excl_by_sheet: dict[str, list[dict]] = defaultdict(list)
    for r in excluded_rows:
        excl_by_sheet[r["sheet"]].append(r)
    print(f"[rebuild] excluded funds: {len(excluded_rows)} across {len(excl_by_sheet)} sheets")

    # 4) Load Bucket G reinstatements
    bucket_g = load_bucket_g(Path(args.bucket_g))
    bg_by_sheet: dict[str, list[dict]] = defaultdict(list)
    for r in bucket_g:
        bg_by_sheet[r["sheet"]].append(r)
    print(f"[rebuild] bucket G reinstatements: {len(bucket_g)}")

    # 5) Reinstate Bucket G into their settled sheets
    for sheet_name, bg_rows in bg_by_sheet.items():
        existing_tickers = {r.get("Ticker") for r in sheets.get(sheet_name, [])}
        for bg in bg_rows:
            if bg["ticker"] in existing_tickers:
                continue  # already there
            v1 = dict(bg["v1_row"])
            # Fix feed URL per the rebuild (use relaxed URL that passed)
            v1["Holdings_Feed_URL"] = bg["url"]
            v1["Holdings_Feed_Format"] = bg["fmt"]
            v1["Notes"] = "bucket_g_reinstated (relaxed row-count gate for vehicle_subtype)"
            v1["Lineage_Remediated_By"] = LINEAGE_RECOVERED_BY
            v1["Lineage_Remediated_At"] = utc_iso()
            sheets.setdefault(sheet_name, []).append(v1)

    # 6) Add new sponsor sheets (from confirmed endpoints)
    session = cc_requests.Session(impersonate=IMPERSONATE)
    recovered_summary = []
    # Subadviser mapping: some confirmed "sponsors" are actually sub-advisers whose funds
    # live in a different excluded sheet (typically `tidal`).
    SUBADV_SOURCE = {
        "yieldmax": "tidal",
        "roundhill": "tidal",
        "bitwise": "tidal",
        "defiance": "tidal",
    }
    for slug, endpoint in confirmed.items():
        if slug in sheets:
            continue
        src_sheet = SUBADV_SOURCE.get(slug, slug)
        sheet_excluded = excluded_rows if slug in SUBADV_SOURCE else excl_by_sheet.get(slug, [])
        if not sheet_excluded:
            continue
        print(f"[rebuild] processing {slug} (src_sheet={src_sheet}, pool={len(sheet_excluded)} rows)...")
        built = build_sheet_for_new_sponsor(
            slug, endpoint, sheet_excluded, session,
            source_sheet=src_sheet if slug in SUBADV_SOURCE else None,
            per_ticker_verify=True,
        )
        if built:
            sheets[slug] = built
            recovered_summary.append((slug, len(built), endpoint["template"]))
            print(f"[rebuild] {slug}: admitted {len(built)} funds")

    # 7) Apply Prospectus_URL upgrades if metadata file exists
    if Path(args.metadata).exists():
        prospectus_map: dict[str, str] = {}
        with open(args.metadata) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                tk = d.get("ticker")
                pu = d.get("prospectus_url")
                if tk and pu and "sec.gov/Archives" in pu:
                    prospectus_map[tk] = pu
        n_upgraded = 0
        for rows in sheets.values():
            for r in rows:
                tk = r.get("Ticker")
                if tk and tk in prospectus_map:
                    r["Prospectus_URL"] = prospectus_map[tk]
                    n_upgraded += 1
        print(f"[rebuild] upgraded Prospectus_URL on {n_upgraded} rows")

    # 8) Write
    write_bible(Path(args.out_tmp), sheets)

    # 9) State file
    total = sum(len(rs) for rs in sheets.values())
    state_text = [
        "# Phase 0 Sponsor Recovery — State Report",
        "",
        f"Generated: {utc_iso()}",
        f"Author: {LINEAGE_RECOVERED_BY}",
        "",
        "## Before",
        f"- Bible v1 (settled): 9 sponsors, {settled_count} funds.",
        "- 59 sponsors excluded, 1,757 funds removed via excluded_funds.yaml.",
        "- 93 intra-sheet content-verify failures (Bucket G).",
        "",
        "## After",
        f"- Bible total: **{total} funds across {len(sheets)} sponsor sheets.**",
        f"- Newly recovered sponsors: **{len(recovered_summary)}** (below).",
        f"- Bucket G intra-sheet reinstatements: {len(bucket_g)}",
        "",
        "## Recovered sponsors",
    ]
    for slug, n, tmpl in sorted(recovered_summary):
        state_text.append(f"- `{slug}` ({n} funds): `{tmpl}`")
    Path(args.state).write_text("\n".join(state_text) + "\n")
    print(f"[rebuild] wrote state: {args.state}")


if __name__ == "__main__":
    main()
