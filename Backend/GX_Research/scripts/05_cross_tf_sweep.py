"""
Stage E — cross-TF agreement sweep on Stage-D survivors.

For each D-survivor on TF_A, test boolean reference conditions on higher TF_B.
Gate: cross_tf_PF > baseline_PF AND n > 2000 AND DSR > 0 (plan §9).

Optimisations vs v1:
  - Higher-TF reference panels precomputed ONCE per (TF_B, condition) across the
    universe, shared via module globals.
  - Pool over (parent × tf_b × condition) cells.
"""
from __future__ import annotations

import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from GX_Research.lib import db, indicators as ind, storage, stats, tracker
from GX_Research.scripts._common import (
    BATCH_ID, RESEARCH_START, RESEARCH_END, HOLDOUT_FRAC, UNIVERSE_PATH, SANDBOX_ROOT, log,
)

TF_ORDER = ["5m", "15m", "1h", "4h", "1d"]
N_GATE = 2000


def _cond_ema20_gt_ema50(df):
    c = df["close"].to_numpy()
    return ind.ema(c, 20) > ind.ema(c, 50)

def _cond_ema20_lt_ema50(df):
    c = df["close"].to_numpy()
    return ind.ema(c, 20) < ind.ema(c, 50)

def _cond_sma50_gt_sma200(df):
    c = df["close"].to_numpy()
    return ind.sma(c, 50) > ind.sma(c, 200)

def _cond_adx_gt_20(df):
    return ind.adx(df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy(), 14) > 20

def _cond_macd_hist_gt_0(df):
    return ind.macd(df["close"].to_numpy(), 12, 26, 9)[2] > 0

def _cond_close_gt_ema50(df):
    c = df["close"].to_numpy()
    return c > ind.ema(c, 50)

REFERENCE_CONDITIONS = {
    "ema20_gt_ema50": _cond_ema20_gt_ema50,
    "ema20_lt_ema50": _cond_ema20_lt_ema50,
    "sma50_gt_sma200": _cond_sma50_gt_sma200,
    "adx_gt_20": _cond_adx_gt_20,
    "macd_hist_gt_0": _cond_macd_hist_gt_0,
    "close_gt_ema50": _cond_close_gt_ema50,
}


def load_trades_parquet(cell_id: str, stage: str) -> pd.DataFrame:
    p = SANDBOX_ROOT / "output" / "trades" / stage / BATCH_ID / f"{cell_id}.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


# Module globals for workers
_HIGHER_TF_PANELS: dict[tuple[str, str, str], pd.DataFrame] = {}  # (tf_b, condition, sym) -> DataFrame(bar_start_ts, flag)


def _align_flags(trades: pd.DataFrame, tf_b: str, cond: str) -> np.ndarray:
    """Per-trade boolean flag from higher-TF panel. Returns bool array aligned to trades rows."""
    out = np.zeros(len(trades), dtype=bool)
    for sym, grp in trades.groupby("symbol"):
        panel = _HIGHER_TF_PANELS.get((tf_b, cond, sym))
        if panel is None or panel.empty:
            continue
        panel_ts = panel["bar_start_ts"].to_numpy()
        panel_flag = panel["flag"].to_numpy()
        grp_idx = grp.index.to_numpy()
        entry_ts = pd.to_datetime(grp["entry_ts"], utc=True, errors="coerce").to_numpy()
        pos = np.searchsorted(panel_ts, entry_ts, side="right") - 1
        pos = np.clip(pos, 0, len(panel_flag) - 1)
        out[[trades.index.get_loc(i) for i in grp_idx]] = panel_flag[pos]
    return out


def _evaluate_cell(args: tuple[dict, str, str]) -> dict | None:
    """Worker: evaluate one (parent, tf_b, condition) cell."""
    parent, tf_b, cond = args
    pid = parent["cell_id"]
    trades = load_trades_parquet(pid, "B")
    if trades.empty:
        return None
    trades = trades.reset_index(drop=True)
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True, errors="coerce")
    baseline_pf = stats.profit_factor(trades["pnl_net_bps"].to_numpy())

    mask = _align_flags(trades, tf_b, cond)
    filt = trades[mask]
    if len(filt) < 50:
        return None
    pf = stats.profit_factor(filt["pnl_net_bps"].to_numpy())
    dsr = stats.deflated_sharpe_ratio(filt["pnl_net_bps"].to_numpy() / 100.0, n_trials=len(REFERENCE_CONDITIONS))
    gate = "PASS" if (pf > baseline_pf and len(filt) > N_GATE and dsr > 0) else "FAIL"
    cell_id = f"E_{pid}_{tf_b}_{cond}"
    cfg = storage.config_hash_of({"parent": pid, "tf_b": tf_b, "cond": cond})
    return {
        "cell_id": cell_id, "batch_id": BATCH_ID, "stage": "E",
        "parent_cell_id": pid, "gx_family": parent["gx_family"],
        "timeframe": parent["timeframe"], "direction": parent["direction"],
        "cross_tf_params": {"tf_a": parent["timeframe"], "tf_b": tf_b, "condition": cond},
        "n_trades": int(len(filt)), "pf": pf, "dsr": dsr,
        "baseline_pf": baseline_pf,
        "gate_result": gate, "config_hash": cfg,
        "utc_ts": tracker.utc_now_iso(),
        "filtered_records": filt.to_dict(orient="records") if gate == "PASS" else None,
    }


def main():
    log(f"Stage E starting. batch={BATCH_ID}")
    t_start = time.time()
    d_cells = storage.read_stage_cells("D", BATCH_ID)
    if d_cells.empty:
        tracker.log_decision(f"STAGE_E_{BATCH_ID}", "E", "KILL", "No Stage D output")
        return 2
    d_pass = d_cells[d_cells["gate_result"] == "PASS"]
    if d_pass.empty:
        tracker.log_decision(f"STAGE_E_{BATCH_ID}", "E", "KILL", "Zero Stage D survivors")
        return 2

    b_cells = storage.read_stage_cells("B", BATCH_ID).set_index("cell_id")
    parent_ids = list(d_pass["parent_cell_id"].unique())
    log(f"D-passing parent cells: {len(parent_ids)}")

    # Determine which (TF_A → TF_B) pairs we actually need
    tf_a_set = set()
    for pid in parent_ids:
        if pid in b_cells.index:
            tf_a_set.add(b_cells.loc[pid]["timeframe"])
    tf_b_needed = set()
    for tf_a in tf_a_set:
        if tf_a in TF_ORDER:
            idx_a = TF_ORDER.index(tf_a)
            tf_b_needed.update(TF_ORDER[idx_a + 1:])
    log(f"TF_A set: {tf_a_set}  TF_B needed: {tf_b_needed}")

    universe = db.load_universe(UNIVERSE_PATH)

    # Precompute higher-TF reference panels: load each TF_B bundle once, run each condition.
    global _HIGHER_TF_PANELS
    _HIGHER_TF_PANELS = {}
    for tf_b in tf_b_needed:
        log(f"  Loading bundle for TF_B={tf_b}")
        bundle = db.build_bundle(universe, tf_b, RESEARCH_START, RESEARCH_END)
        for cond, fn in REFERENCE_CONDITIONS.items():
            for sym, df in bundle.frames.items():
                try:
                    flags = fn(df)
                except Exception:
                    continue
                panel = pd.DataFrame({
                    "bar_start_ts": df["bar_start_ts"].to_numpy(),
                    "flag": np.nan_to_num(flags, nan=False).astype(bool),
                })
                panel = panel.sort_values("bar_start_ts").reset_index(drop=True)
                _HIGHER_TF_PANELS[(tf_b, cond, sym)] = panel
        log(f"  Precomputed {len([k for k in _HIGHER_TF_PANELS if k[0] == tf_b])} panels for TF_B={tf_b}")

    # Build work list
    work = []
    for pid in parent_ids:
        if pid not in b_cells.index:
            continue
        parent = b_cells.loc[pid]
        tf_a = parent["timeframe"]
        if tf_a not in TF_ORDER:
            continue
        higher = TF_ORDER[TF_ORDER.index(tf_a) + 1:]
        parent_rec = {
            "cell_id": pid, "gx_family": parent["gx_family"],
            "timeframe": tf_a, "direction": parent["direction"],
        }
        for tf_b in higher:
            for cond in REFERENCE_CONDITIONS:
                work.append((parent_rec, tf_b, cond))
    log(f"Work list: {len(work)} (parent × tf_b × condition) cells")

    n_workers = int(os.environ.get("GX_WORKERS", "14"))
    single_proc = os.environ.get("GX_SINGLE_PROCESS", "") == "1"

    if single_proc:
        results = []
        for i, w in enumerate(work):
            res = _evaluate_cell(w)
            if res is not None:
                results.append(res)
            if (i + 1) % 500 == 0:
                log(f"  progress: {i+1}/{len(work)} cells, elapsed {time.time()-t_start:.1f}s")
    else:
        with Pool(processes=n_workers) as pool:
            results = []
            for i, res in enumerate(pool.imap_unordered(_evaluate_cell, work, chunksize=8)):
                if res is not None:
                    results.append(res)
                if (i + 1) % 500 == 0:
                    log(f"  progress: {i+1}/{len(work)} cells, elapsed {time.time()-t_start:.1f}s")

    sweep_rows = []
    tracker_rows = []
    n_pass = 0
    for r in results:
        filt_records = r.pop("filtered_records", None)
        sweep_rows.append(r)
        tracker_rows.append({
            "cell_id": r["cell_id"], "parent_cell_id": r["parent_cell_id"], "utc_ts": r["utc_ts"],
            "tf_a": r["timeframe"], "tf_b": r["cross_tf_params"]["tf_b"],
            "reference_condition": r["cross_tf_params"]["condition"], "include_flag": True,
            "n": r["n_trades"], "pf": r["pf"], "dsr": r["dsr"],
            "baseline_pf": r["baseline_pf"], "gate_result": r["gate_result"], "config_hash": r["config_hash"],
        })
        if r["gate_result"] == "PASS":
            n_pass += 1
            if filt_records:
                storage.write_trades(r["cell_id"], BATCH_ID, "E", filt_records)

    sweep_path = storage.write_sweep_cells("E", BATCH_ID, sweep_rows)
    tracker.append_rows("07_Stage_E_CrossTF", tracker_rows)
    tracker.log_decision(f"STAGE_E_{BATCH_ID}", "E", "PASS" if n_pass else "KILL",
                         f"{n_pass}/{len(sweep_rows)} cross-TF cells pass")
    tracker.log_compute("E", tracker.utc_now_iso(), tracker.utc_now_iso(),
                        time.time() - t_start, None, len(sweep_rows), str(sweep_path))
    log(f"Stage E complete. {n_pass} PASS. Wall-clock {time.time()-t_start:.1f}s")
    return 0 if n_pass else 2


if __name__ == "__main__":
    sys.exit(main())
