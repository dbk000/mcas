"""Partition every (row, column) cell of fund_bible_v1.xlsx into TRUST / REPLACE / FILL
per §6.1 of PHASE_0_REMEDIATION_TASKBOARD.md. Writes a markdown report to
PHASE_0_REMEDIATION_STATE.md as the first deliverable.

TRUST  — populated by v1 from N-CEN; do not re-fetch.
REPLACE — placeholder URL (edgar_fallback / sec.gov search); must be re-fetched via §6.2.
FILL   — literal 'null' string from v1; must be populated via §6.2 / §6.3.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

BIBLE = Path(__file__).resolve().parents[2] / "ground_truth" / "fund_bible_v1.xlsx"
STATE = Path(__file__).resolve().parents[2] / "PHASE_0_REMEDIATION_STATE.md"

TRUST_COLS = {
    "#", "Ticker", "Fund_Name", "Sponsor", "Exchange",
    "Vehicle_Type", "Vehicle_Subtype", "Replication_Method",
    "Rebalance_Frequency_Prospectus", "Creation_Unit_Size",
    "AUM_USD", "AUM_AsOf_Date", "Has_Derivatives_Overlay",
    "Currency_Hedged", "Fund_Of_Funds", "Benchmark_Provider",
    "Primary_Market_Cap_Tier", "Geographic_Focus",
    "Lineage_Authored_By", "Lineage_Authored_At",
    "Notes",
}
REPLACE_COLS = {
    "Holdings_Feed_URL", "Holdings_Feed_Format",
    "Sponsor_Page_URL", "Prospectus_URL",
}
FILL_COLS = {
    "Inception_Date", "Shares_Outstanding", "Expense_Ratio_Bps",
    "Benchmark_Name", "Holdings_Feed_Lag_Hours",
    "Rebalance_Trigger_Rules", "Internal_Crossing_Disclosed",
}


def classify_cell(col: str, val) -> str:
    """TRUST cells stay. REPLACE cells have placeholder URLs. FILL cells are literal null."""
    if val is None:
        return "FILL"
    sval = str(val).strip()

    # Core REPLACE detection
    if col in ("Holdings_Feed_URL", "Sponsor_Page_URL", "Prospectus_URL"):
        if "sec.gov/cgi-bin/browse-edgar" in sval.lower():
            return "REPLACE"
        return "OK_REPLACE" if sval and sval != "null" else "REPLACE"
    if col == "Holdings_Feed_Format":
        if sval == "edgar_fallback":
            return "REPLACE"
        return "OK_REPLACE" if sval and sval != "null" else "REPLACE"
    if col == "Notes":
        if "edgar_fallback" in sval or "pending Phase 1" in sval:
            return "REPLACE_NOTE"
        return "TRUST"
    # FILL detection
    if col in FILL_COLS:
        if sval == "null" or sval == "":
            return "FILL"
        return "TRUST"
    # TRUST otherwise (assumed correctly populated)
    if sval == "null" or sval == "":
        return "FILL"
    return "TRUST"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", default=str(BIBLE))
    ap.add_argument("--out", default=str(STATE))
    args = ap.parse_args()

    print(f"Loading: {args.workbook}")
    wb = load_workbook(args.workbook, read_only=True, data_only=True)

    # per-column state counts (across all sheets)
    col_states: dict[str, Counter] = defaultdict(Counter)
    # per-sponsor REPLACE/FILL counts
    per_sponsor: dict[str, Counter] = defaultdict(Counter)
    # per-sponsor URL column REPLACE counts
    sponsor_url_replace: dict[str, int] = defaultdict(int)
    sponsor_row_count: dict[str, int] = defaultdict(int)
    # Total rows
    total_rows = 0

    headers = None

    for sheet_name in wb.sheetnames:
        if sheet_name == "Overview":
            continue
        sh = wb[sheet_name]
        rows = list(sh.iter_rows(values_only=True))
        if len(rows) < 5:
            continue
        # Header is row index 3 (row 4 in 1-indexed)
        header = rows[3]
        if headers is None:
            headers = header
        else:
            # Sanity: all sponsor sheets should share the same header
            if tuple(header) != tuple(headers):
                print(f"WARN: sheet {sheet_name} has divergent header; using its own")
        data = rows[4:]
        sponsor_row_count[sheet_name] = len(data)
        total_rows += len(data)

        for row in data:
            for h, v in zip(header, row):
                if h is None:
                    continue
                state = classify_cell(h, v)
                col_states[h][state] += 1
                if state in ("REPLACE", "FILL"):
                    per_sponsor[sheet_name][state] += 1
                if state == "REPLACE" and h == "Holdings_Feed_URL":
                    sponsor_url_replace[sheet_name] += 1

    # Emit markdown
    out = []
    out.append("# Phase 0 Remediation — State File\n")
    out.append("**Author:** phase_0_remediation_cc (Claude Code, Opus 4.7)\n")
    out.append("**Session started:** 2026-04-22 (UTC)\n")
    out.append(f"**Bible loaded from:** `{args.workbook}`\n")
    out.append(f"**Total data rows (excl Overview):** {total_rows}\n\n")
    out.append("## Partition Report (§6.1)\n\n")
    out.append("Per-column count of TRUST (v1 correctly populated, do not re-fetch) / ")
    out.append("REPLACE (v1 placeholder URL, must be re-fetched per §6.2) / ")
    out.append("FILL (literal 'null' from v1, must be populated per §6.2/§6.3). ")
    out.append("The 'REPLACE_NOTE' state on `Notes` is a Notes entry whose text itself references ")
    out.append("`edgar_fallback` / `pending Phase 1` and must be rewritten on remediated rows.\n\n")
    out.append("| Column | TRUST | REPLACE | FILL | REPLACE_NOTE | OK_REPLACE |\n")
    out.append("| --- | ---: | ---: | ---: | ---: | ---: |\n")
    for h in headers:
        if h is None:
            continue
        cs = col_states[h]
        out.append(f"| {h} | {cs.get('TRUST',0)} | {cs.get('REPLACE',0)} | {cs.get('FILL',0)} | "
                   f"{cs.get('REPLACE_NOTE',0)} | {cs.get('OK_REPLACE',0)} |\n")
    out.append("\n")

    # Per-sponsor breakdown
    out.append("## Per-sponsor Breakdown (sorted by Holdings_Feed_URL REPLACE count, desc)\n\n")
    out.append("`UrlReplace` is the count of rows whose `Holdings_Feed_URL` currently points at a SEC EDGAR search page (the primary remediation target). ")
    out.append("`TotalReplace` sums cells across the 4 REPLACE columns. ")
    out.append("`TotalFill` sums cells across the 7 FILL columns.\n\n")
    out.append("| Sponsor Sheet | Rows | UrlReplace | TotalReplace | TotalFill |\n")
    out.append("| --- | ---: | ---: | ---: | ---: |\n")
    sorted_sponsors = sorted(
        sponsor_row_count.keys(),
        key=lambda s: (-sponsor_url_replace[s], -sponsor_row_count[s], s),
    )
    for s in sorted_sponsors:
        rep = per_sponsor[s].get("REPLACE", 0)
        fil = per_sponsor[s].get("FILL", 0)
        out.append(f"| {s} | {sponsor_row_count[s]} | {sponsor_url_replace[s]} | {rep} | {fil} |\n")
    out.append("\n")

    # Work queue — sponsors needing endpoint discovery
    needs_discovery = [s for s in sorted_sponsors if sponsor_url_replace[s] > 0]
    needs_none = [s for s in sorted_sponsors if sponsor_url_replace[s] == 0]
    out.append(f"## Work queue\n\n")
    out.append(f"- **Sponsors needing endpoint discovery + URL replacement:** {len(needs_discovery)}\n")
    out.append(f"- **Sponsors where all URLs already sponsor-native (still need §7.2 content-verify):** {len(needs_none)}\n\n")
    out.append("### Priority order for §6.2 discovery (top-20 by UrlReplace)\n\n")
    for s in needs_discovery[:20]:
        out.append(f"1. **{s}** — {sponsor_url_replace[s]} URL REPLACE rows / {sponsor_row_count[s]} total\n")
    out.append("\n")

    out.append("## Next steps\n\n")
    out.append("1. Add `Lineage_Remediated_By` and `Lineage_Remediated_At` columns (init `null`) to every sponsor sheet.\n")
    out.append("2. Discover sponsor product-index endpoints per §6.2 Step 1, populate `registry/sponsor_endpoints.yaml`.\n")
    out.append("3. Batch-enrich per sponsor; content-verify per-sponsor; remove failed rows to `excluded_funds.yaml`.\n")
    out.append("4. Final schema_check / URL distinctness / coverage / sanity gates.\n")
    out.append("5. Atomic rename .tmp → xlsx; commit.\n")

    # Summary for CLI / session log
    with open(args.out, "w") as f:
        f.write("".join(out))

    # JSON sidecar for programmatic use by later scripts
    sidecar = {
        "total_rows": total_rows,
        "sponsor_row_count": dict(sponsor_row_count),
        "sponsor_url_replace": dict(sponsor_url_replace),
        "per_sponsor_replace": {s: dict(c) for s, c in per_sponsor.items()},
        "col_states": {c: dict(v) for c, v in col_states.items()},
        "work_queue": needs_discovery,
        "no_url_work_queue": needs_none,
        "headers": list(headers) if headers else [],
    }
    sidecar_path = Path(args.out).parent / "source_authoring" / "phase_0" / "partition.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sidecar_path, "w") as f:
        json.dump(sidecar, f, indent=2, default=str)

    print(f"Partition report written to {args.out}")
    print(f"Sidecar JSON written to {sidecar_path}")
    print(f"Total rows: {total_rows}")
    print(f"Sponsors needing URL replacement: {len(needs_discovery)}")
    print(f"Top 5 by UrlReplace:")
    for s in needs_discovery[:5]:
        print(f"  - {s}: {sponsor_url_replace[s]} / {sponsor_row_count[s]}")


if __name__ == "__main__":
    main()
