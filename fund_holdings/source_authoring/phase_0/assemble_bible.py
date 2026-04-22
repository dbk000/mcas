"""
Phase 0 — Fund Bible: assemble phase_0.jsonl into fund_bible_v1.xlsx.

Consumes phase_0.jsonl (produced by metadata_enrich.py) and lays it out
per the taskboard §5 conventions:

  - One sheet per sponsor family, plus an "Overview" sheet.
  - Sheet header at row 4, data from row 5 (rows 1-3 reserved for title /
    generation metadata). Matches the equities_news bible_v1.xlsx pattern.
  - 32 columns in the taskboard §5.1 order.
  - Sponsors with fewer than MIN_SPONSOR_ROWS funds are combined into an
    "Other_Sponsors" sheet to keep sheet count manageable.
  - Overview contains: per-sheet row counts + per-sheet Σ AUM + grand total.
  - Writes to fund_bible_v1.xlsx.tmp; the caller performs the atomic
    rename to fund_bible_v1.xlsx after schema_check succeeds.

Usage:
    python3 assemble_bible.py \\
        --input logs/phase_0.jsonl \\
        --output-tmp ../../ground_truth/fund_bible_v1.xlsx.tmp \\
        --min-sponsor-rows 5

Exit codes:
    0 — workbook written successfully
    2 — input missing / malformed
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys
from collections import defaultdict, Counter
from dataclasses import dataclass
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("assemble_bible")


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
]


# Map phase_0.jsonl field names → Excel column names.
FIELD_TO_COLUMN = {
    "ticker": "Ticker",
    "fund_name": "Fund_Name",
    "sponsor_raw": "Sponsor",
    "exchange": "Exchange",
    "inception_date": "Inception_Date",
    "aum_usd": "AUM_USD",
    "aum_as_of_date": "AUM_AsOf_Date",
    "shares_outstanding": "Shares_Outstanding",
    "expense_ratio_bps": "Expense_Ratio_Bps",
    "vehicle_type": "Vehicle_Type",
    "vehicle_subtype": "Vehicle_Subtype",
    "benchmark_name": "Benchmark_Name",
    "benchmark_provider": "Benchmark_Provider",
    "replication_method": "Replication_Method",
    "rebalance_frequency_prospectus": "Rebalance_Frequency_Prospectus",
    "rebalance_trigger_rules": "Rebalance_Trigger_Rules",
    "creation_unit_size": "Creation_Unit_Size",
    "holdings_feed_url": "Holdings_Feed_URL",
    "holdings_feed_format": "Holdings_Feed_Format",
    "holdings_feed_lag_hours": "Holdings_Feed_Lag_Hours",
    "sponsor_page_url": "Sponsor_Page_URL",
    "prospectus_url": "Prospectus_URL",
    "has_derivatives_overlay": "Has_Derivatives_Overlay",
    "internal_crossing_disclosed": "Internal_Crossing_Disclosed",
    "currency_hedged": "Currency_Hedged",
    "fund_of_funds": "Fund_Of_Funds",
    "primary_market_cap_tier": "Primary_Market_Cap_Tier",
    "geographic_focus": "Geographic_Focus",
    "notes": "Notes",
    "lineage_authored_by": "Lineage_Authored_By",
    "lineage_authored_at": "Lineage_Authored_At",
}


def safe_sheet_name(slug: str, existing: set[str]) -> str:
    """
    Excel sheet-name constraints: ≤31 chars, no [ ] : * ? / \\ . Must be
    unique within workbook. Use title-cased slug; truncate if needed.
    """
    name = slug.strip() or "unknown"
    # Humanise: "state_street" -> "State_Street" is fine. Keep underscores.
    name = name.replace(":", "_").replace("/", "_").replace("\\", "_")
    name = name.replace("[", "(").replace("]", ")").replace("*", "").replace("?", "")
    name = name[:31]
    base = name
    i = 2
    while name in existing:
        suffix = f"_{i}"
        name = (base[: 31 - len(suffix)]) + suffix
        i += 1
    existing.add(name)
    return name


def coerce_cell(col: str, value: Any) -> Any:
    """Convert a phase_0.jsonl value into an Excel-friendly cell value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if value.strip() == "" or value.strip().lower() == "none":
            return "null"
        return value
    return str(value)


def load_phase_0(path: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as e:
                log.warning("skipping malformed line: %s", e)
                continue
            if r.get("excluded"):
                continue
            records.append(r)
    return records


def group_by_sponsor(
    records: list[dict[str, Any]],
    min_sponsor_rows: int,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Return (sponsor → [records sorted AUM desc], other_threshold_count)."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        slug = r.get("sponsor_slug", "") or "unaffiliated_generic"
        groups[slug].append(r)

    # Collapse small sponsors.
    small_sponsors = [s for s, rs in groups.items() if len(rs) < min_sponsor_rows]
    other_records: list[dict[str, Any]] = []
    for s in small_sponsors:
        other_records.extend(groups.pop(s))
    if other_records:
        groups["other_sponsors"] = other_records

    # Sort within each sponsor by AUM desc (null last) then ticker asc.
    for slug, rs in groups.items():
        rs.sort(key=lambda r: (-(r.get("aum_usd") or 0), r.get("ticker", "")))
    return (groups, len(small_sponsors))


def write_sheet(
    ws: openpyxl.worksheet.worksheet.Worksheet,
    sheet_label: str,
    records: list[dict[str, Any]],
) -> None:
    # Rows 1-3: header metadata.
    ws.cell(row=1, column=1, value=f"Sheet: {sheet_label}").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"Row count: {len(records)}")
    ws.cell(row=3, column=1, value=(
        "Generated by MCAS Phase 0 Fund Bible (phase_0_fund_bible_cc). "
        "Header at row 4, data from row 5. 32 columns per taskboard §5.1."
    ))
    # Row 4: column headers.
    for ci, col in enumerate(BIBLE_COLUMNS, start=1):
        c = ws.cell(row=4, column=ci, value=col)
        c.font = Font(bold=True)
        c.fill = PatternFill(start_color="FFE8E8E8", end_color="FFE8E8E8", fill_type="solid")
    # Rows 5+: data.
    for ri, rec in enumerate(records, start=5):
        # Column 1 is '#' — per-sheet sequence.
        ws.cell(row=ri, column=1, value=ri - 4)
        for ci, col in enumerate(BIBLE_COLUMNS, start=1):
            if col == "#":
                continue
            field = next((fk for fk, fv in FIELD_TO_COLUMN.items() if fv == col), None)
            val = rec.get(field) if field else None
            ws.cell(row=ri, column=ci, value=coerce_cell(col, val))
    # Column widths — pragmatic defaults.
    width_overrides = {
        "Ticker": 10, "Fund_Name": 48, "Sponsor": 24, "Exchange": 11,
        "Inception_Date": 14, "AUM_USD": 16, "AUM_AsOf_Date": 14,
        "Shares_Outstanding": 16, "Expense_Ratio_Bps": 10,
        "Vehicle_Type": 15, "Vehicle_Subtype": 18, "Benchmark_Name": 28,
        "Benchmark_Provider": 18, "Replication_Method": 18,
        "Rebalance_Frequency_Prospectus": 22, "Rebalance_Trigger_Rules": 28,
        "Creation_Unit_Size": 12, "Holdings_Feed_URL": 70,
        "Holdings_Feed_Format": 16, "Holdings_Feed_Lag_Hours": 10,
        "Sponsor_Page_URL": 70, "Prospectus_URL": 70,
        "Has_Derivatives_Overlay": 14, "Internal_Crossing_Disclosed": 14,
        "Currency_Hedged": 12, "Fund_Of_Funds": 12,
        "Primary_Market_Cap_Tier": 18, "Geographic_Focus": 18,
        "Notes": 40, "Lineage_Authored_By": 22, "Lineage_Authored_At": 22,
    }
    for ci, col in enumerate(BIBLE_COLUMNS, start=1):
        letter = openpyxl.utils.get_column_letter(ci)
        ws.column_dimensions[letter].width = width_overrides.get(col, 14)
    # Freeze top-4 rows + first 2 columns (Ticker visible while scrolling).
    ws.freeze_panes = "C5"


def write_overview(
    ws: openpyxl.worksheet.worksheet.Worksheet,
    groups: dict[str, list[dict[str, Any]]],
    sheet_name_map: dict[str, str],
    small_count: int,
) -> None:
    ws.cell(row=1, column=1, value="MCAS Phase 0 — Fund Bible Overview").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=(
        "Summary of sponsor-family sheets. Columns: sheet name, row count, "
        "Σ AUM (USD, from most-recent N-CEN filing), Σ with AUM reported, "
        "% with verified Holdings_Feed_URL (non-edgar_fallback)."
    ))
    header_row = 4
    headers = ["Sheet_Name", "Sponsor_Slug", "Row_Count", "Sum_AUM_USD",
               "Count_With_AUM", "Pct_With_Sponsor_Feed", "Edgar_Fallback_Count"]
    for ci, h in enumerate(headers, start=1):
        c = ws.cell(row=header_row, column=ci, value=h)
        c.font = Font(bold=True)
        c.fill = PatternFill(start_color="FFE8E8E8", end_color="FFE8E8E8", fill_type="solid")

    sorted_slugs = sorted(groups.keys(), key=lambda s: (-len(groups[s]), s))
    grand_rows = 0
    grand_aum = 0.0
    grand_with_aum = 0
    grand_sponsor_feed = 0
    grand_edgar_fallback = 0
    for ri, slug in enumerate(sorted_slugs, start=5):
        recs = groups[slug]
        row_count = len(recs)
        sum_aum = sum(r.get("aum_usd") or 0.0 for r in recs)
        count_aum = sum(1 for r in recs if (r.get("aum_usd") or 0.0) > 0)
        n_sponsor_feed = sum(
            1 for r in recs
            if (r.get("holdings_feed_format") or "").lower() not in ("edgar_fallback", "null", "")
        )
        n_edgar_fb = sum(
            1 for r in recs
            if (r.get("holdings_feed_format") or "").lower() == "edgar_fallback"
        )
        pct_sponsor = (n_sponsor_feed / row_count * 100.0) if row_count else 0.0
        ws.cell(row=ri, column=1, value=sheet_name_map.get(slug, slug))
        ws.cell(row=ri, column=2, value=slug)
        ws.cell(row=ri, column=3, value=row_count)
        ws.cell(row=ri, column=4, value=round(sum_aum, 2))
        ws.cell(row=ri, column=5, value=count_aum)
        ws.cell(row=ri, column=6, value=round(pct_sponsor, 1))
        ws.cell(row=ri, column=7, value=n_edgar_fb)
        grand_rows += row_count
        grand_aum += sum_aum
        grand_with_aum += count_aum
        grand_sponsor_feed += n_sponsor_feed
        grand_edgar_fallback += n_edgar_fb

    total_row = 5 + len(sorted_slugs)
    ws.cell(row=total_row, column=1, value="Total").font = Font(bold=True)
    ws.cell(row=total_row, column=3, value=grand_rows).font = Font(bold=True)
    ws.cell(row=total_row, column=4, value=round(grand_aum, 2)).font = Font(bold=True)
    ws.cell(row=total_row, column=5, value=grand_with_aum).font = Font(bold=True)
    pct = (grand_sponsor_feed / grand_rows * 100.0) if grand_rows else 0.0
    ws.cell(row=total_row, column=6, value=round(pct, 1)).font = Font(bold=True)
    ws.cell(row=total_row, column=7, value=grand_edgar_fallback).font = Font(bold=True)
    ws.cell(row=total_row + 2, column=1, value=(
        f"{small_count} small sponsors (< MIN_SPONSOR_ROWS) were merged "
        f"into the 'other_sponsors' sheet."
    ))

    # Column widths.
    widths = [24, 28, 12, 18, 14, 20, 16]
    for ci, w in enumerate(widths, start=1):
        letter = openpyxl.utils.get_column_letter(ci)
        ws.column_dimensions[letter].width = w
    ws.freeze_panes = "A5"


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble fund_bible_v1.xlsx")
    parser.add_argument("--input", required=True, type=pathlib.Path)
    parser.add_argument("--output-tmp", required=True, type=pathlib.Path)
    parser.add_argument("--min-sponsor-rows", type=int, default=5)
    args = parser.parse_args()

    if not args.input.exists():
        log.error("input %s missing", args.input)
        return 2
    records = load_phase_0(args.input)
    log.info("loaded %d non-excluded records", len(records))
    if not records:
        log.error("no records to write — aborting")
        return 2

    groups, small_count = group_by_sponsor(records, args.min_sponsor_rows)
    log.info("%d sponsor groups (after collapse of %d small sponsors)",
             len(groups), small_count)

    wb = openpyxl.Workbook()
    # Remove default sheet.
    wb.remove(wb.active)

    # Overview first (Excel convention).
    overview_ws = wb.create_sheet(title="Overview")

    # Per-sponsor sheets, sorted by size desc.
    sorted_slugs = sorted(groups.keys(), key=lambda s: (-len(groups[s]), s))
    existing: set[str] = {"Overview"}
    sheet_name_map: dict[str, str] = {}
    for slug in sorted_slugs:
        sheet_label = safe_sheet_name(slug, existing)
        sheet_name_map[slug] = sheet_label
        ws = wb.create_sheet(title=sheet_label)
        write_sheet(ws, sheet_label=sheet_label, records=groups[slug])

    write_overview(overview_ws, groups, sheet_name_map, small_count)

    args.output_tmp.parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.output_tmp)
    log.info("wrote %s (%d sheets)", args.output_tmp, 1 + len(sorted_slugs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
