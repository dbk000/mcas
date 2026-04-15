"""
Stage C — DOW × hour temporal decomposition on Stage-B survivors.
For each surviving cell: decompose its trade log by UTC weekday × UTC hour.
Gate: at least one (day, hour) bucket with PF > 1.0 AND n > 100.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from GX_Research.lib import storage, stats, tracker
from GX_Research.scripts._common import BATCH_ID, SANDBOX_ROOT, log


def load_cell_trades(cell_id: str, stage: str = "B") -> pd.DataFrame:
    p = SANDBOX_ROOT / "output" / "trades" / stage / BATCH_ID / f"{cell_id}.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def main():
    log(f"Stage C starting. batch={BATCH_ID}")
    t_start = time.time()
    survivors = storage.read_stage_cells("B", BATCH_ID)
    if survivors.empty:
        tracker.log_decision(f"STAGE_C_{BATCH_ID}", "C", "KILL", "No Stage B survivors")
        return 2
    survivors = survivors[survivors["gate_result"] == "PASS"].sort_values("pf", ascending=False)
    log(f"Stage B survivors: {len(survivors)}")
    if survivors.empty:
        tracker.log_decision(f"STAGE_C_{BATCH_ID}", "C", "KILL", "Zero Stage B survivors")
        return 2

    sweep_rows = []
    tracker_rows = []
    survivors_pass = []

    for _, parent in survivors.iterrows():
        trades = load_cell_trades(parent["cell_id"], "B")
        if trades.empty:
            continue
        trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True, errors="coerce")
        trades = trades.dropna(subset=["entry_ts"])
        trades["weekday"] = trades["entry_ts"].dt.weekday
        trades["hour"] = trades["entry_ts"].dt.hour

        any_pass = False
        for (wd, hr), grp in trades.groupby(["weekday", "hour"]):
            pnl = grp["pnl_net_bps"].values
            if pnl.size == 0:
                continue
            pf = stats.profit_factor(pnl)
            wr = stats.win_rate(pnl)
            n = int(len(grp))
            bucket_gate = "PASS" if (pf > 1.0 and n > 100) else "FAIL"
            if bucket_gate == "PASS":
                any_pass = True
            cell_id = f"C_{parent['cell_id']}_wd{wd}_hr{hr}"
            sweep_rows.append({
                "cell_id": cell_id, "batch_id": BATCH_ID, "stage": "C",
                "parent_cell_id": parent["cell_id"],
                "gx_family": parent["gx_family"], "timeframe": parent["timeframe"],
                "direction": parent["direction"],
                "utc_weekday": int(wd), "utc_hour": int(hr),
                "n_trades": n, "pf": pf, "win_rate": wr,
                "gate_result": bucket_gate,
                "config_hash": parent["config_hash"],
                "utc_ts": tracker.utc_now_iso(),
            })
            tracker_rows.append({
                "cell_id": cell_id, "parent_cell_id": parent["cell_id"],
                "utc_ts": tracker.utc_now_iso(),
                "utc_weekday": int(wd), "utc_hour": int(hr),
                "n": n, "pf": pf, "win_rate": wr, "gate_result": bucket_gate,
            })
        if any_pass:
            survivors_pass.append(parent["cell_id"])

    sweep_path = storage.write_sweep_cells("C", BATCH_ID, sweep_rows)
    tracker.append_rows("04_Stage_C_DOWHour", tracker_rows)
    log(f"Stage C: {len(survivors_pass)}/{len(survivors)} parent cells had at least one PASS bucket")
    tracker.log_decision(f"STAGE_C_{BATCH_ID}", "C", "PASS" if survivors_pass else "KILL",
                         f"{len(survivors_pass)} parent cells carry a PASS (PF>1, n>100) bucket")
    tracker.log_compute("C", tracker.utc_now_iso(), tracker.utc_now_iso(),
                        time.time() - t_start, None, len(sweep_rows), str(sweep_path))
    log(f"Stage C complete. Wall-clock {time.time()-t_start:.1f}s")
    return 0 if survivors_pass else 2


if __name__ == "__main__":
    sys.exit(main())
