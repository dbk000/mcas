"""
GX_Research_Tracker.xlsx — human audit trail. Plan §12a.
Thin wrappers around openpyxl in append mode.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
from openpyxl import Workbook, load_workbook

SANDBOX_ROOT = Path("/app/GX_Research") if os.path.isdir("/app/GX_Research") else Path(__file__).resolve().parent.parent
TRACKER_PATH = SANDBOX_ROOT / "GX_Research_Tracker.xlsx"

SHEETS: dict[str, list[str]] = {
    "00_Index": ["sheet_name", "rows", "last_updated_utc", "last_commit_sha"],
    "01_Plan_History": ["version", "utc_ts", "author", "change_summary", "git_sha"],
    "02_Stage_A_Triggers": [
        "cell_id", "utc_ts", "gx_family", "trigger_params_json", "tf", "direction",
        "n_trades", "pf", "win_rate", "avg_r", "pct_stop_inside_entry_range",
        "gate_result", "config_hash",
    ],
    "03_Stage_B_StopExit": [
        "cell_id", "parent_cell_id", "utc_ts", "stop_params_json", "exit_config_id",
        "exit_params_json", "n_trades", "pf", "win_rate", "avg_r", "avg_daily_pf",
        "gate_result", "config_hash",
    ],
    "04_Stage_C_DOWHour": [
        "cell_id", "parent_cell_id", "utc_ts", "utc_weekday", "utc_hour",
        "n", "pf", "win_rate", "gate_result",
    ],
    "05_Stage_D_Filters": [
        "cell_id", "parent_cell_id", "utc_ts", "filter_id", "filter_params_json",
        "fold_idx", "n", "pf", "dsr", "folds_passed", "gate_result", "config_hash",
    ],
    "06_Stage_D_Regimes": [
        "cell_id", "parent_cell_id", "utc_ts", "regime_scheme", "bucket",
        "n", "pf", "win_rate", "stratify_or_filter", "gate_result",
    ],
    "07_Stage_E_CrossTF": [
        "cell_id", "parent_cell_id", "utc_ts", "tf_a", "tf_b", "reference_condition",
        "include_flag", "n", "pf", "dsr", "baseline_pf", "gate_result", "config_hash",
    ],
    "08_Stage_F_Holdout": [
        "cell_id", "utc_ts", "agg_pf", "avg_daily_pf", "n", "dsr",
        "slip_1x_pf", "slip_15x_pf", "slip_2x_pf",
        "tier_1_pf", "tier_2_pf", "tier_3_pf", "tier_4_pf", "tier_5_pf",
        "shortlist_decision",
    ],
    "09_Decisions_Log": ["utc_ts", "cell_id", "stage", "decision", "reason"],
    "10_Slippage_Table": ["utc_ts", "symbol", "tier", "median_24h_quote_vol_usd", "tf", "slip_bps_roundtrip"],
    "11_Universe_Snapshot": ["utc_ts", "symbol", "quote_ccy", "data_symbol", "tier"],
    "12_Compute_Log": ["stage", "utc_start", "utc_end", "wall_clock_secs", "peak_cpu_pct", "n_cells_processed", "output_parquet_path"],
    "13_Open_Questions": ["id", "utc_ts", "question", "resolution", "resolution_utc_ts"],
    "14_Corrections": ["utc_ts", "issue", "root_cause", "fix", "preventative_rule"],
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def initialise(path: Path | None = None) -> Path:
    p = Path(path) if path else TRACKER_PATH
    if p.exists():
        return p
    wb = Workbook()
    # Remove default sheet, create all sheets with headers
    default = wb.active
    wb.remove(default)
    for name, cols in SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append(cols)
    # Initial index rows
    idx_ws = wb["00_Index"]
    for name in SHEETS:
        idx_ws.append([name, 0, utc_now_iso(), ""])
    wb.save(p)
    return p


def append_rows(sheet: str, rows: Iterable[dict], path: Path | None = None) -> int:
    p = Path(path) if path else TRACKER_PATH
    if not p.exists():
        initialise(p)
    rows = list(rows)
    if not rows:
        return 0
    wb = load_workbook(p)
    if sheet not in wb.sheetnames:
        ws = wb.create_sheet(sheet)
        ws.append(list(rows[0].keys()))
    ws = wb[sheet]
    cols = SHEETS.get(sheet) or list(rows[0].keys())
    n = 0
    for row in rows:
        ws.append([_serialize(row.get(c, "")) for c in cols])
        n += 1
    # Update index last_updated
    idx_ws = wb["00_Index"]
    for r in idx_ws.iter_rows(min_row=2, values_only=False):
        if r[0].value == sheet:
            r[1].value = (r[1].value or 0) + n
            r[2].value = utc_now_iso()
            break
    wb.save(p)
    return n


def _serialize(v):
    if isinstance(v, (dict, list)):
        import json
        return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)
    if v is None:
        return ""
    return v


def log_decision(cell_id: str, stage: str, decision: str, reason: str):
    append_rows("09_Decisions_Log", [{
        "utc_ts": utc_now_iso(),
        "cell_id": cell_id,
        "stage": stage,
        "decision": decision,
        "reason": reason,
    }])


def log_compute(stage: str, utc_start: str, utc_end: str, wall_clock_secs: float, peak_cpu_pct: float | None, n_cells_processed: int, output_parquet_path: str):
    append_rows("12_Compute_Log", [{
        "stage": stage, "utc_start": utc_start, "utc_end": utc_end,
        "wall_clock_secs": wall_clock_secs, "peak_cpu_pct": peak_cpu_pct or "",
        "n_cells_processed": n_cells_processed,
        "output_parquet_path": output_parquet_path,
    }])


def log_correction(issue: str, root_cause: str, fix: str, preventative_rule: str):
    append_rows("14_Corrections", [{
        "utc_ts": utc_now_iso(), "issue": issue, "root_cause": root_cause,
        "fix": fix, "preventative_rule": preventative_rule,
    }])
