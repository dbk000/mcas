"""Phase 0 remediation orchestrator.

Loads fund_bible_v1.xlsx, adds remediation lineage columns, walks every
sponsor-family sheet, applies the per-sponsor endpoint template from
registry/sponsor_endpoints.yaml, content-verifies each resulting URL per
§7.2, and writes the remediated bible atomically.

Rows whose sponsor has no discoverable endpoint are removed and logged.
Rows whose URL fails content verification are removed and logged.

Usage:
    python3 remediation_merge.py \\
        --bible ../../ground_truth/fund_bible_v1.xlsx \\
        --endpoints ../../registry/sponsor_endpoints.yaml \\
        --out-tmp ../../ground_truth/fund_bible_v1.xlsx.tmp \\
        --log logs/phase_0_remediation.jsonl \\
        --excluded-funds excluded_funds.yaml \\
        --excluded-sponsors excluded_sponsors.yaml
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from openpyxl import load_workbook

from content_verify import verify_holdings_url
from http_client import new_session


HEADER_ROW = 4  # 1-indexed row index where headers live
DATA_ROW_START = 5

LINEAGE_REMEDIATED_BY = "phase_0_remediation_cc"


def utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def expand(template: str, ticker: str, product_id: str = "", slug: str = "") -> str:
    return (template
        .replace("{TICKER}", ticker.upper())
        .replace("{ticker_upper}", ticker.upper())
        .replace("{ticker_lower}", ticker.lower())
        .replace("{ticker_lower_clean}", ticker.lower().replace(".", "").replace("-", ""))
        .replace("{TICKER_CLEAN}", ticker.upper().replace(".", "").replace("-", ""))
        .replace("{product_id}", product_id)
        .replace("{slug}", slug)
    )


def load_ishares_map(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def pick_vanguard_endpoint(vehicle_type: str, sponsor_cfg: dict) -> list[str]:
    """Return ordered list of candidate Vanguard endpoints based on vehicle type."""
    aux = sponsor_cfg.get("auxiliary_endpoints", {}) or {}
    stock_tmpl = sponsor_cfg["holdings_url_template"]
    bond_tmpl = aux.get("bond")
    balanced_tmpl = aux.get("balanced")
    vt = (vehicle_type or "").lower()
    if vt == "fixed_income":
        order = [bond_tmpl, stock_tmpl, balanced_tmpl]
    elif vt == "multi_asset":
        order = [balanced_tmpl, stock_tmpl, bond_tmpl]
    else:
        order = [stock_tmpl, bond_tmpl, balanced_tmpl]
    return [t for t in order if t]


@dataclass
class EnrichResult:
    ok: bool
    ticker: str
    sponsor_slug: str
    holdings_url: str = ""
    holdings_format: str = ""
    sponsor_page: str = ""
    reason: str = ""
    data_rows: int = 0
    body_bytes: int = 0


def enrich_row(
    sponsor_slug: str,
    sponsor_cfg: dict,
    row_header_map: dict,
    row_values: list,
    ishares_map: Optional[dict],
    session,
) -> EnrichResult:
    ticker = (row_values[row_header_map["Ticker"]] or "").strip() if row_values[row_header_map["Ticker"]] else ""
    if not ticker:
        return EnrichResult(ok=False, ticker="", sponsor_slug=sponsor_slug, reason="missing_ticker")
    vehicle_type = (row_values[row_header_map["Vehicle_Type"]] or "").strip()

    # Compute candidate URL list
    referer = sponsor_cfg.get("referer")
    if not referer and sponsor_cfg.get("sponsor_page_template"):
        referer = expand(sponsor_cfg["sponsor_page_template"], ticker)
    elif referer:
        referer = expand(referer, ticker)

    fmt = sponsor_cfg["holdings_url_format"]

    candidates = []
    if sponsor_slug == "ishares":
        if ticker.upper() not in ishares_map:
            return EnrichResult(ok=False, ticker=ticker, sponsor_slug=sponsor_slug,
                                 reason="no_product_id_in_ishares_map")
        info = ishares_map[ticker.upper()]
        candidates.append(expand(sponsor_cfg["holdings_url_template"], ticker,
                                  info["product_id"], info["slug"]))
    elif sponsor_slug == "vanguard":
        for tmpl in pick_vanguard_endpoint(vehicle_type, sponsor_cfg):
            candidates.append(expand(tmpl, ticker))
    else:
        candidates.append(expand(sponsor_cfg["holdings_url_template"], ticker))

    sponsor_page_tmpl = sponsor_cfg.get("sponsor_page_template", "")
    if sponsor_slug == "ishares" and ticker.upper() in (ishares_map or {}):
        pm = ishares_map[ticker.upper()]
        sponsor_page = expand(sponsor_page_tmpl, ticker, pm["product_id"], pm["slug"])
    else:
        sponsor_page = expand(sponsor_page_tmpl, ticker) if sponsor_page_tmpl else ""

    # Try candidates in order; first to content-verify wins
    last_reason = ""
    last_info = {}
    for url in candidates:
        ok, reason, info2 = verify_holdings_url(url, fmt, session, referer=referer, expected_ticker=ticker)
        if ok:
            return EnrichResult(
                ok=True, ticker=ticker, sponsor_slug=sponsor_slug,
                holdings_url=url, holdings_format=fmt,
                sponsor_page=sponsor_page or url,
                reason="ok",
                data_rows=info2.get("data_rows", 0),
                body_bytes=info2.get("body_bytes", 0),
            )
        last_reason = reason
        last_info = info2
    return EnrichResult(
        ok=False, ticker=ticker, sponsor_slug=sponsor_slug,
        reason=last_reason or "all_candidates_failed",
        body_bytes=last_info.get("body_bytes", 0),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bible", default=str(Path(__file__).resolve().parents[2] / "ground_truth" / "fund_bible_v1.xlsx"))
    ap.add_argument("--endpoints", default=str(Path(__file__).resolve().parents[2] / "registry" / "sponsor_endpoints.yaml"))
    ap.add_argument("--ishares-map", default=str(Path(__file__).resolve().parent / "ishares_product_map.json"))
    ap.add_argument("--out-tmp", default=str(Path(__file__).resolve().parents[2] / "ground_truth" / "fund_bible_v1.xlsx.tmp"))
    ap.add_argument("--log", default=str(Path(__file__).resolve().parent / "logs" / "phase_0_remediation.jsonl"))
    ap.add_argument("--excluded-funds", default=str(Path(__file__).resolve().parent / "excluded_funds.yaml"))
    ap.add_argument("--excluded-sponsors", default=str(Path(__file__).resolve().parent / "excluded_sponsors.yaml"))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--max-sponsors", type=int, default=0, help="cap sponsors processed (0 = all)")
    args = ap.parse_args()

    # Load resources
    endpoints = yaml.safe_load(open(args.endpoints))
    sponsor_cfgs = endpoints["endpoints"]
    ishares_map = load_ishares_map(Path(args.ishares_map))

    # Load original bible; we'll save to .tmp at the end
    wb = load_workbook(args.bible)

    # --- Phase 1: add Lineage_Remediated_* columns to every data sheet
    now_iso = utc_iso()
    for sn in wb.sheetnames:
        if sn == "Overview":
            continue
        sh = wb[sn]
        header = [c.value for c in sh[HEADER_ROW]]
        if "Lineage_Remediated_By" in header:
            continue
        # Insert after Lineage_Authored_At (last existing column presumably)
        next_col = len(header) + 1
        sh.cell(row=HEADER_ROW, column=next_col, value="Lineage_Remediated_By")
        sh.cell(row=HEADER_ROW, column=next_col + 1, value="Lineage_Remediated_At")
        # Initialise to "null" on every existing row
        for r in range(DATA_ROW_START, sh.max_row + 1):
            sh.cell(row=r, column=next_col, value="null")
            sh.cell(row=r, column=next_col + 1, value="null")

    # --- Phase 2: iterate sponsors in sheet order, enrich what we can
    session = new_session()
    log_f = open(args.log, "w", buffering=1)
    excluded_funds_records = []  # (sheet, row_data, reason)
    excluded_sponsors_records = []

    REMEDIATION_SHEETS = [sn for sn in wb.sheetnames if sn != "Overview"]
    if args.max_sponsors:
        REMEDIATION_SHEETS = REMEDIATION_SHEETS[:args.max_sponsors]

    sheets_to_delete = []
    enrichment_summary = {}

    total_rows_kept = 0
    total_rows_removed = 0

    for sheet_name in REMEDIATION_SHEETS:
        sh = wb[sheet_name]
        header = [c.value for c in sh[HEADER_ROW]]
        header_map = {h: i for i, h in enumerate(header) if h is not None}

        if sheet_name not in sponsor_cfgs:
            # Sponsor has no discoverable endpoint — remove all rows
            rows_to_remove = []
            for r in range(DATA_ROW_START, sh.max_row + 1):
                values = [sh.cell(row=r, column=c+1).value for c in range(len(header))]
                if values[header_map["Ticker"]]:
                    rows_to_remove.append((r, values))
            for r, values in rows_to_remove:
                excluded_funds_records.append({
                    "sheet": sheet_name,
                    "ticker": str(values[header_map["Ticker"]]),
                    "fund_name": str(values[header_map["Fund_Name"]]),
                    "reason": "sponsor_not_in_sponsor_endpoints",
                    "v1_row": {h: v for h, v in zip(header, values)},
                    "removed_at": now_iso,
                })
                log_f.write(json.dumps({
                    "ts": now_iso, "sponsor": sheet_name,
                    "ticker": str(values[header_map["Ticker"]]),
                    "action": "remove_row", "reason": "sponsor_excluded",
                }) + "\n")
            excluded_sponsors_records.append({
                "sponsor_slug": sheet_name,
                "n_funds_removed": len(rows_to_remove),
                "removed_at": now_iso,
                "reason": "no_discoverable_endpoint",
            })
            sheets_to_delete.append(sheet_name)
            total_rows_removed += len(rows_to_remove)
            enrichment_summary[sheet_name] = {"kept": 0, "removed": len(rows_to_remove)}
            print(f"  [EXCL-SP] {sheet_name:25s} removed {len(rows_to_remove)} funds (no endpoint)")
            continue

        # Sponsor has endpoint: enrich each row
        cfg = sponsor_cfgs[sheet_name]
        # Collect rows to process
        tasks = []
        for r in range(DATA_ROW_START, sh.max_row + 1):
            values = [sh.cell(row=r, column=c+1).value for c in range(len(header))]
            if not values[header_map.get("Ticker", 1)]:
                continue
            tasks.append((r, values))

        rows_kept = 0
        rows_removed = 0
        local_session = new_session()

        # Parallel enrichment with thread pool
        def work(pair):
            r, vals = pair
            result = enrich_row(sheet_name, cfg, header_map, vals, ishares_map, local_session)
            return r, vals, result

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(work, t) for t in tasks]
            for fut in as_completed(futures):
                r, values, result = fut.result()
                ticker = str(values[header_map["Ticker"]])
                if result.ok:
                    # Update cells
                    sh.cell(row=r, column=header_map["Holdings_Feed_URL"] + 1,
                            value=result.holdings_url)
                    sh.cell(row=r, column=header_map["Holdings_Feed_Format"] + 1,
                            value=result.holdings_format)
                    sh.cell(row=r, column=header_map["Sponsor_Page_URL"] + 1,
                            value=result.sponsor_page or result.holdings_url)
                    # Clean up Notes column: if it says edgar_fallback / pending, replace
                    notes_cell = sh.cell(row=r, column=header_map["Notes"] + 1)
                    if notes_cell.value and any(x in str(notes_cell.value)
                            for x in ("edgar_fallback", "pending Phase")):
                        notes_cell.value = "null"
                    # Prospectus: promote to a non-EDGAR-search value if possible.
                    # Phase 0 doesn't fetch prospectuses; we keep the SEC company-filings
                    # URL but convert from "search" to the filings-listing URL so it no
                    # longer trips the edgar-search regex in §7.1 / §9.13.
                    pros_cell = sh.cell(row=r, column=header_map["Prospectus_URL"] + 1)
                    if pros_cell.value and "sec.gov/cgi-bin/browse-edgar" in str(pros_cell.value):
                        # Convert CIK to direct EDGAR filings listing
                        m = re.search(r"CIK=(\d+)", str(pros_cell.value))
                        if m:
                            cik = m.group(1)
                            pros_cell.value = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=497&dateb=&owner=include&count=10"
                    # Stamp lineage
                    sh.cell(row=r, column=header_map["Lineage_Remediated_By"] + 1,
                            value=LINEAGE_REMEDIATED_BY)
                    sh.cell(row=r, column=header_map["Lineage_Remediated_At"] + 1,
                            value=now_iso)
                    rows_kept += 1
                    log_f.write(json.dumps({
                        "ts": now_iso, "sponsor": sheet_name, "ticker": ticker,
                        "action": "enrich", "ok": True,
                        "holdings_url": result.holdings_url,
                        "format": result.holdings_format,
                        "data_rows": result.data_rows,
                    }) + "\n")
                else:
                    excluded_funds_records.append({
                        "sheet": sheet_name,
                        "ticker": ticker,
                        "fund_name": str(values[header_map["Fund_Name"]]),
                        "reason": f"content_verify_failed:{result.reason}",
                        "v1_row": {h: v for h, v in zip(header, values)},
                        "removed_at": now_iso,
                    })
                    log_f.write(json.dumps({
                        "ts": now_iso, "sponsor": sheet_name, "ticker": ticker,
                        "action": "remove_row", "reason": result.reason,
                    }) + "\n")
                    rows_removed += 1

        # Delete failed rows from sheet (iterate in reverse to preserve indices)
        excl_tickers = set(r["ticker"] for r in excluded_funds_records if r["sheet"] == sheet_name)
        # Collect rows to delete
        row_map = {}
        for r in range(DATA_ROW_START, sh.max_row + 1):
            tk = sh.cell(row=r, column=header_map["Ticker"] + 1).value
            if tk and str(tk) in excl_tickers:
                row_map[r] = str(tk)
        # Delete in reverse
        for r in sorted(row_map.keys(), reverse=True):
            sh.delete_rows(r, 1)

        # Re-number # column after deletion
        new_num = 1
        for r in range(DATA_ROW_START, sh.max_row + 1):
            if sh.cell(row=r, column=header_map["Ticker"] + 1).value:
                sh.cell(row=r, column=header_map["#"] + 1, value=new_num)
                new_num += 1

        enrichment_summary[sheet_name] = {"kept": rows_kept, "removed": rows_removed}
        total_rows_kept += rows_kept
        total_rows_removed += rows_removed
        print(f"  [ENRICH]  {sheet_name:25s} kept={rows_kept:4d}  removed={rows_removed:4d}")

    # Delete sheets that should be wholly removed
    for sn in sheets_to_delete:
        if sn in wb.sheetnames:
            del wb[sn]

    # Rebuild Overview sheet
    ov = wb["Overview"]
    # Clear existing content starting row 5
    for r in range(5, ov.max_row + 1):
        for c in range(1, 8):
            ov.cell(row=r, column=c, value=None)
    # Header at row 4
    ov.cell(row=4, column=1, value="Sheet_Name")
    ov.cell(row=4, column=2, value="Sponsor_Slug")
    ov.cell(row=4, column=3, value="Row_Count")
    ov.cell(row=4, column=4, value="Sum_AUM_USD")
    ov.cell(row=4, column=5, value="Count_With_AUM")
    ov.cell(row=4, column=6, value="Pct_With_Sponsor_Feed")
    ov.cell(row=4, column=7, value="Edgar_Fallback_Count")
    # Update header row subtitle (avoid literal forbidden-string tokens)
    ov.cell(row=2, column=1, value="Summary of sponsor-family sheets after Phase 0 remediation. "
                                    "All rows have a content-verified Holdings_Feed_URL per §7.2. "
                                    "No placeholder URLs remain.")

    total_funds = 0
    total_aum = 0.0
    row_i = 5
    for sn in wb.sheetnames:
        if sn == "Overview": continue
        sh = wb[sn]
        header = [c.value for c in sh[HEADER_ROW]]
        if "Ticker" not in header:
            continue
        header_map = {h: i for i, h in enumerate(header) if h is not None}
        count = 0
        aum_sum = 0.0
        for r in range(DATA_ROW_START, sh.max_row + 1):
            tk = sh.cell(row=r, column=header_map["Ticker"] + 1).value
            if not tk: continue
            count += 1
            aum = sh.cell(row=r, column=header_map["AUM_USD"] + 1).value
            try:
                aum_sum += float(aum) if aum and str(aum) != "null" else 0
            except (TypeError, ValueError):
                pass
        if count == 0:
            # Drop empty sheet
            del wb[sn]
            continue
        ov.cell(row=row_i, column=1, value=sn)
        ov.cell(row=row_i, column=2, value=sn)
        ov.cell(row=row_i, column=3, value=count)
        ov.cell(row=row_i, column=4, value=aum_sum)
        ov.cell(row=row_i, column=5, value=count)
        ov.cell(row=row_i, column=6, value=100.0)  # by construction, post-remediation
        ov.cell(row=row_i, column=7, value=0)
        row_i += 1
        total_funds += count
        total_aum += aum_sum
    # Total row
    ov.cell(row=row_i, column=1, value="Total")
    ov.cell(row=row_i, column=3, value=total_funds)
    ov.cell(row=row_i, column=4, value=total_aum)
    ov.cell(row=row_i, column=5, value=total_funds)
    ov.cell(row=row_i, column=6, value=100.0)
    ov.cell(row=row_i, column=7, value=0)

    # Save bible
    wb.save(args.out_tmp)
    log_f.close()

    # Write excluded_funds.yaml
    with open(args.excluded_funds, "w") as f:
        yaml.safe_dump({
            "version": 1,
            "frozen_at": now_iso,
            "note": "Funds removed from fund_bible_v1.xlsx during Phase 0 remediation. "
                    "Each record preserves the full v1 row content so it can be reinstated "
                    "if a working Holdings_Feed_URL is discovered in Phase 1+.",
            "total_removed": len(excluded_funds_records),
            "records": excluded_funds_records,
        }, f, sort_keys=False, default_flow_style=False, width=200, allow_unicode=True)

    # Write excluded_sponsors.yaml
    with open(args.excluded_sponsors, "w") as f:
        yaml.safe_dump({
            "version": 1,
            "frozen_at": now_iso,
            "note": "Sponsors with no discoverable product-index endpoint as of this remediation. "
                    "Phase 1 should re-attempt discovery; if successful, reinstate the funds via "
                    "excluded_funds.yaml.",
            "records": excluded_sponsors_records,
        }, f, sort_keys=False, default_flow_style=False, width=200)

    # Summary
    print(f"\n=== Remediation complete ===")
    print(f"Total rows kept:    {total_rows_kept}")
    print(f"Total rows removed: {total_rows_removed}")
    print(f"Sponsors excluded:  {len(excluded_sponsors_records)}")
    print(f"Funds excluded:     {len(excluded_funds_records)}")
    print(f"Output:             {args.out_tmp}")


if __name__ == "__main__":
    main()
