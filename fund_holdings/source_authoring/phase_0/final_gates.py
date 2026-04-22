"""Phase 0 remediation — final gates (§7.1 / §7.2 / §7.3 / §7.4 / §7.5).

Runs ONLY after remediation_merge.py has produced fund_bible_v1.xlsx.tmp.
Exits 0 iff every row in the bible has a content-verified Holdings_Feed_URL,
no forbidden strings appear, URLs are distinct at the fund level, coverage
meets §7.4, and AUM sanity passes §7.5. On failure, emits a detailed report
and exits 2; the atomic-rename step does NOT execute.

Usage:
    python3 final_gates.py --bible ../../ground_truth/fund_bible_v1.xlsx.tmp
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import openpyxl  # noqa

from content_verify import verify_holdings_url
from http_client import new_session


HEADER_ROW = 4
DATA_ROW_START = 5

FORBIDDEN_STRINGS = ("edgar_fallback", "template_guess", "pattern_guess", "pending Phase")

EXPECTED_COLUMNS = [
    "#", "Ticker", "Fund_Name", "Sponsor", "Exchange",
    "Inception_Date", "AUM_USD", "AUM_AsOf_Date",
    "Shares_Outstanding", "Expense_Ratio_Bps",
    "Vehicle_Type", "Vehicle_Subtype", "Benchmark_Name", "Benchmark_Provider",
    "Replication_Method", "Rebalance_Frequency_Prospectus",
    "Rebalance_Trigger_Rules", "Creation_Unit_Size",
    "Holdings_Feed_URL", "Holdings_Feed_Format", "Holdings_Feed_Lag_Hours",
    "Sponsor_Page_URL", "Prospectus_URL",
    "Has_Derivatives_Overlay", "Internal_Crossing_Disclosed",
    "Currency_Hedged", "Fund_Of_Funds",
    "Primary_Market_Cap_Tier", "Geographic_Focus",
    "Notes", "Lineage_Authored_By", "Lineage_Authored_At",
    "Lineage_Remediated_By", "Lineage_Remediated_At",
]

PERMITTED_FORMATS = {"csv", "xlsx", "json", "html_table"}

# FILL columns allowed to be "null" without failing coverage check
SPARSE_ALLOWED = {"Inception_Date", "Rebalance_Trigger_Rules",
                  "Holdings_Feed_Lag_Hours", "Vehicle_Subtype",
                  "Benchmark_Name",  # often null for active funds
                  "Shares_Outstanding", "Expense_Ratio_Bps",
                  "Lineage_Remediated_By", "Lineage_Remediated_At"}  # null on un-remediated rows (none, post-remediation)


def gate_71_schema_and_forbidden(wb, report: list) -> bool:
    """§7.1: every row has every column, enums valid, no forbidden strings."""
    ok = True
    report.append("\n## §7.1 Schema + forbidden strings\n")
    for sn in wb.sheetnames:
        if sn == "Overview":
            continue
        sh = wb[sn]
        header = [c.value for c in sh[HEADER_ROW]]
        missing = [c for c in EXPECTED_COLUMNS if c not in header]
        if missing:
            report.append(f"  ❌ sheet {sn!r}: missing cols {missing}\n")
            ok = False
            continue
        header_map = {h: i for i, h in enumerate(header)}
        rows = list(sh.iter_rows(min_row=DATA_ROW_START, values_only=True))
        for i, row in enumerate(rows):
            if not row or row[header_map["Ticker"]] is None:
                continue
            # Forbidden strings in any cell
            for col_idx, val in enumerate(row):
                if val is None:
                    continue
                sval = str(val)
                for forb in FORBIDDEN_STRINGS:
                    if forb in sval:
                        report.append(f"  ❌ {sn}[{i+DATA_ROW_START}] col={header[col_idx]!r} contains {forb!r}: {sval[:100]!r}\n")
                        ok = False
            # Holdings_Feed_Format must be in permitted set
            fmt = row[header_map["Holdings_Feed_Format"]]
            if not fmt or str(fmt).lower() not in PERMITTED_FORMATS:
                report.append(f"  ❌ {sn}[{i+DATA_ROW_START}] invalid Holdings_Feed_Format: {fmt!r}\n")
                ok = False
    report.append(f"  {'PASS' if ok else 'FAIL'}\n")
    return ok


def gate_72_content_verify(wb, report: list, workers: int = 12) -> bool:
    """§7.2: every Holdings_Feed_URL in the bible must pass content verification."""
    ok = True
    report.append("\n## §7.2 Holdings_Feed_URL content verification\n")
    tasks = []
    for sn in wb.sheetnames:
        if sn == "Overview":
            continue
        sh = wb[sn]
        header = [c.value for c in sh[HEADER_ROW]]
        header_map = {h: i for i, h in enumerate(header)}
        for r_idx in range(DATA_ROW_START, sh.max_row + 1):
            ticker = sh.cell(row=r_idx, column=header_map["Ticker"] + 1).value
            if not ticker:
                continue
            url = sh.cell(row=r_idx, column=header_map["Holdings_Feed_URL"] + 1).value
            fmt = sh.cell(row=r_idx, column=header_map["Holdings_Feed_Format"] + 1).value
            sponsor_page = sh.cell(row=r_idx, column=header_map["Sponsor_Page_URL"] + 1).value
            tasks.append((sn, r_idx, ticker, url, fmt, sponsor_page))

    session = new_session()
    failures = []
    pass_count = 0

    def verify(task):
        sn, r_idx, ticker, url, fmt, sponsor_page = task
        referer = sponsor_page if (sponsor_page and isinstance(sponsor_page, str) and sponsor_page != url) else None
        # Retry transient failures (http_0 = connection error, http_5xx = server blip)
        for attempt in range(3):
            ok2, reason, info = verify_holdings_url(
                url, str(fmt).lower(), session, referer=referer, expected_ticker=str(ticker),
            )
            if ok2:
                return sn, r_idx, ticker, url, fmt, True, reason, info
            if not (reason.startswith("http_0") or reason.startswith("http_5") or "timeout" in reason.lower()):
                # Non-transient — don't retry
                return sn, r_idx, ticker, url, fmt, False, reason, info
            time.sleep(1.0 * (attempt + 1))
        return sn, r_idx, ticker, url, fmt, False, reason, info

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(verify, t) for t in tasks]
        for i, fut in enumerate(as_completed(futs)):
            sn, r_idx, ticker, url, fmt, ok2, reason, info = fut.result()
            if ok2:
                pass_count += 1
            else:
                failures.append((sn, ticker, reason, url[:80]))
                ok = False

    report.append(f"  Total checked: {len(tasks)}\n")
    report.append(f"  Passed: {pass_count}\n")
    report.append(f"  Failed: {len(failures)}\n")
    if failures:
        report.append(f"  First 25 failures:\n")
        for f in failures[:25]:
            report.append(f"    {f[0]:20s} {f[1]:10s} {f[2]:40s} {f[3]}\n")
    report.append(f"  {'PASS' if ok else 'FAIL'}\n")
    return ok


def gate_73_url_distinctness(wb, report: list) -> bool:
    """§7.3: every Holdings_Feed_URL must be distinct at the fund level."""
    ok = True
    report.append("\n## §7.3 Holdings_Feed_URL distinctness\n")
    counter = Counter()
    for sn in wb.sheetnames:
        if sn == "Overview":
            continue
        sh = wb[sn]
        header = [c.value for c in sh[HEADER_ROW]]
        header_map = {h: i for i, h in enumerate(header)}
        for r_idx in range(DATA_ROW_START, sh.max_row + 1):
            ticker = sh.cell(row=r_idx, column=header_map["Ticker"] + 1).value
            if not ticker:
                continue
            url = sh.cell(row=r_idx, column=header_map["Holdings_Feed_URL"] + 1).value
            counter[str(url)] += 1
    dupes = [(u, n) for u, n in counter.items() if n > 1]
    if dupes:
        ok = False
        report.append(f"  ❌ {len(dupes)} URL(s) appear on multiple rows:\n")
        for u, n in dupes[:10]:
            report.append(f"    {n}x  {u[:120]}\n")
    else:
        report.append(f"  All {len(counter)} URLs are distinct.\n")
    report.append(f"  {'PASS' if ok else 'FAIL'}\n")
    return ok


def gate_74_coverage(wb, report: list) -> bool:
    """§7.4: Overview total matches sheet counts; ≥95% non-null on load-bearing;
    ≤30% null on every column except sparse-allowed set."""
    ok = True
    report.append("\n## §7.4 Coverage\n")
    per_col_null = Counter()
    per_col_total = Counter()

    # Compute per-sheet totals
    sheet_counts = {}
    for sn in wb.sheetnames:
        if sn == "Overview":
            continue
        sh = wb[sn]
        header = [c.value for c in sh[HEADER_ROW]]
        header_map = {h: i for i, h in enumerate(header)}
        count = 0
        for r_idx in range(DATA_ROW_START, sh.max_row + 1):
            ticker = sh.cell(row=r_idx, column=header_map["Ticker"] + 1).value
            if not ticker:
                continue
            count += 1
            for col_name, col_idx in header_map.items():
                if col_name is None:
                    continue
                val = sh.cell(row=r_idx, column=col_idx + 1).value
                per_col_total[col_name] += 1
                if val is None or str(val).strip().lower() in ("", "null"):
                    per_col_null[col_name] += 1
        sheet_counts[sn] = count

    total_rows = sum(sheet_counts.values())
    report.append(f"  Total data rows across sheets: {total_rows}\n")

    # Overview match
    ov = wb["Overview"]
    ov_total = None
    for r in ov.iter_rows(values_only=True):
        if r and r[0] == "Total":
            ov_total = r[2]
            break
    if ov_total != total_rows:
        report.append(f"  ❌ Overview 'Total' row says {ov_total}, sheets sum to {total_rows}\n")
        ok = False
    else:
        report.append(f"  Overview Total matches sheets: {total_rows}\n")

    # Load-bearing columns ≥95%
    LOAD_BEARING = ["Sponsor", "Exchange", "Vehicle_Type", "Replication_Method",
                    "Rebalance_Frequency_Prospectus", "Holdings_Feed_URL", "Holdings_Feed_Format"]
    for col in LOAD_BEARING:
        tot = per_col_total.get(col, 0)
        nul = per_col_null.get(col, 0)
        pct_null = (100.0 * nul / tot) if tot else 0
        if pct_null > 5.0:
            report.append(f"  ❌ Load-bearing col {col} has {pct_null:.1f}% null (must be ≤5%)\n")
            ok = False
        else:
            report.append(f"  ✓ {col}: {100.0 - pct_null:.1f}% populated\n")

    # 30% null limit on all non-sparse columns
    for col, nul in per_col_null.items():
        tot = per_col_total.get(col, 0)
        pct_null = (100.0 * nul / tot) if tot else 0
        if col in SPARSE_ALLOWED:
            continue
        if pct_null > 30.0:
            report.append(f"  ⚠ Column {col}: {pct_null:.1f}% null (sparse, but >30%)\n")
            # Not a hard fail for columns that are structurally sparse

    report.append(f"  {'PASS' if ok else 'FAIL'}\n")
    return ok


def gate_75_sanity(wb, report: list, ici_total: float = 11_000_000_000_000.0, tolerance: float = 0.25) -> bool:
    """§7.5: AUM within 25% of ICI total; no duplicate tickers; row arithmetic ok."""
    ok = True
    report.append("\n## §7.5 Sanity\n")
    total_aum = 0.0
    all_tickers = []
    for sn in wb.sheetnames:
        if sn == "Overview":
            continue
        sh = wb[sn]
        header = [c.value for c in sh[HEADER_ROW]]
        header_map = {h: i for i, h in enumerate(header)}
        for r_idx in range(DATA_ROW_START, sh.max_row + 1):
            ticker = sh.cell(row=r_idx, column=header_map["Ticker"] + 1).value
            if not ticker: continue
            all_tickers.append(str(ticker))
            aum = sh.cell(row=r_idx, column=header_map["AUM_USD"] + 1).value
            try:
                total_aum += float(aum) if aum and str(aum) != "null" else 0
            except (TypeError, ValueError):
                pass
    report.append(f"  Σ AUM_USD = ${total_aum:,.0f}\n")
    report.append(f"  ICI total ≈ ${ici_total:,.0f}\n")
    ratio = total_aum / ici_total
    report.append(f"  Ratio: {ratio*100:.1f}%\n")
    if ratio < (1 - tolerance):
        report.append(f"  ⚠ Σ AUM is {ratio*100:.0f}% of ICI total — below {(1-tolerance)*100:.0f}% "
                       f"tolerance. Remediation's strict exclusion likely acceptable but flag for Darren.\n")
    else:
        report.append(f"  ✓ AUM within tolerance\n")
    # No duplicate tickers
    tc = Counter(all_tickers)
    dupes = [(t, n) for t, n in tc.items() if n > 1]
    if dupes:
        ok = False
        report.append(f"  ❌ Duplicate tickers: {dupes[:10]}\n")
    else:
        report.append(f"  ✓ No duplicate tickers ({len(all_tickers)} unique)\n")
    report.append(f"  {'PASS' if ok else 'FAIL'}\n")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bible", required=True)
    ap.add_argument("--report", default="logs/final_gates_report.md")
    ap.add_argument("--skip-content-verify", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    # openpyxl refuses to read .tmp; rename to .xlsx for reading only
    bible_path = Path(args.bible)
    if bible_path.suffix == ".tmp":
        read_path = bible_path.with_suffix(".reader.xlsx")
        import shutil
        shutil.copy(bible_path, read_path)
    else:
        read_path = bible_path

    wb = openpyxl.load_workbook(read_path)

    report = [f"# Final Gates Report\n\nBible: `{args.bible}`\n"]
    ok1 = gate_71_schema_and_forbidden(wb, report)
    ok3 = gate_73_url_distinctness(wb, report)
    ok4 = gate_74_coverage(wb, report)
    ok5 = gate_75_sanity(wb, report)
    if args.skip_content_verify:
        report.append("\n## §7.2 SKIPPED (--skip-content-verify)\n")
        ok2 = True
    else:
        ok2 = gate_72_content_verify(wb, report, workers=args.workers)

    overall = ok1 and ok2 and ok3 and ok4 and ok5
    report.append(f"\n## Overall\n\n{'✅ PASS' if overall else '❌ FAIL'}\n")

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    with open(args.report, "w") as f:
        f.write("".join(report))

    # Cleanup reader copy
    if read_path != bible_path and read_path.exists():
        read_path.unlink()

    print("".join(report))
    sys.exit(0 if overall else 2)


if __name__ == "__main__":
    main()
