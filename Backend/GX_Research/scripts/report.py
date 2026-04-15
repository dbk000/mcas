"""
Consolidated decision memo — writes Markdown summary of each stage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from GX_Research.lib import storage, tracker
from GX_Research.scripts._common import BATCH_ID, SANDBOX_ROOT, TIMEFRAMES, RESEARCH_START, RESEARCH_END, log


SUBSTITUTION_NOTE = (
    "> **Data substitution (plan §14, user decision 2026-04-14).** The universe "
    "is 288 USDC pairs (`universe.txt`), but production OHLCV for USDC pairs only "
    "goes back ~2 days. `lib/db.logical_to_data_symbol()` translates every USDC "
    "symbol to its USDT counterpart at fetch time, so all pre-Stage-F results "
    "are priced on USDT history. Transportability USDT→USDC is tested only at "
    "Stage F. If no cell reaches Stage F, the null result is on USDT data.\n"
)


def stage_section(stage: str) -> str:
    df = storage.read_stage_cells(stage, BATCH_ID)
    if df.empty:
        return f"### Stage {stage}\n\n_No data_\n\n"
    n = len(df)
    n_pass = (df["gate_result"] == "PASS").sum() if "gate_result" in df.columns else 0
    lines = [
        f"### Stage {stage}", "",
        f"- cells: {n}",
        f"- passing gate: {n_pass}",
    ]
    if "pf" in df.columns:
        top = df.sort_values("pf", ascending=False).head(5)
        lines += ["", "**Top 5 by PF:**"]
        for _, r in top.iterrows():
            cell_id = r.get("cell_id", "?")
            pf = r.get("pf", 0)
            n_tr = r.get("n_trades", 0)
            fam = r.get("gx_family", "")
            tf = r.get("timeframe", "")
            d = r.get("direction", "")
            lines.append(f"  - `{cell_id}`  {fam} {tf} {d}  PF={pf:.2f} n={n_tr}")
    lines.append("")
    return "\n".join(lines)


def main():
    out = [
        f"# GX Research Decision Memo — batch {BATCH_ID}",
        "",
        f"**Window:** `{RESEARCH_START}` → `{RESEARCH_END}`   |   "
        f"**Timeframes:** `{', '.join(TIMEFRAMES)}`",
        "",
        SUBSTITUTION_NOTE,
    ]
    for stage in ("A", "B", "C", "D", "E", "F"):
        out.append(stage_section(stage))
    path = SANDBOX_ROOT / "output" / f"report_{BATCH_ID}.md"
    path.write_text("\n".join(out))
    log(f"Wrote report to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
