"""
Stage D — filter + regime discovery on Stage-C survivors.

For each C-passing parent cell, test a grid of indicator/regime filters on a
4-fold purged rolling walk-forward. A filter passes if PF > baseline_PF × 1.1
AND n > 500 in ≥ 3 of 4 folds AND DSR > 0 (plan §7).

Optimisations vs v1:
  - OHLCV bundle loaded ONCE per TF. Per-symbol precomputed indicator frames are
    built once and shared across every parent's trade-enrichment merge.
  - Pool over parent cells for the per-parent filter×fold evaluation.
  - Tight progress logging so we can see pace.
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


FILTER_GRID = [
    ("atr_pct_high", lambda tdf: tdf["atr_pct"] > tdf["atr_pct"].quantile(0.66)),
    ("atr_pct_low",  lambda tdf: tdf["atr_pct"] < tdf["atr_pct"].quantile(0.33)),
    ("adx_ge_20",    lambda tdf: tdf["adx"] >= 20),
    ("adx_ge_25",    lambda tdf: tdf["adx"] >= 25),
    ("volz_gt_1",    lambda tdf: tdf["volume_z"] > 1),
    ("volz_gt_2",    lambda tdf: tdf["volume_z"] > 2),
    ("bb_width_hi",  lambda tdf: tdf["bb_width"] > tdf["bb_width"].quantile(0.66)),
    ("bb_width_lo",  lambda tdf: tdf["bb_width"] < tdf["bb_width"].quantile(0.33)),
    ("close_gt_ema50", lambda tdf: tdf["close"] > tdf["ema50"]),
    ("rsi_lt_70",    lambda tdf: tdf["rsi"] < 70),
    ("rsi_gt_30",    lambda tdf: tdf["rsi"] > 30),
]


# Module globals for workers (set once per TF group before Pool spawn)
_IND_BY_SYM: dict[str, pd.DataFrame] = {}
_TF: str = ""
_FOLDS = []


def load_trades_parquet(cell_id: str, stage: str) -> pd.DataFrame:
    p = SANDBOX_ROOT / "output" / "trades" / stage / BATCH_ID / f"{cell_id}.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def build_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol indicator panel, keyed by bar_start_ts."""
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    v = df["volume"].to_numpy()
    atr14 = ind.atr(h, l, c, 14)
    adx_arr = ind.adx(h, l, c, 14)
    volz = ind.volume_z(v, 20)
    _mid, _u, _lo, bbw = ind.bollinger(c, 20, 2.0)
    ema50 = ind.ema(c, 50)
    rsi_arr = ind.rsi(c, 14)
    atr_pct = atr14 / c
    out = pd.DataFrame({
        "bar_start_ts": df["bar_start_ts"].to_numpy(),
        "atr": atr14,
        "atr_pct": atr_pct,
        "adx": adx_arr,
        "volume_z": volz,
        "bb_width": bbw,
        "ema50": ema50,
        "rsi": rsi_arr,
        "close": c,
    })
    return out


def enrich_with_precomputed(trades: pd.DataFrame) -> pd.DataFrame:
    """Join trades to the precomputed indicator frames in _IND_BY_SYM (module global)."""
    if trades.empty:
        return trades
    trades = trades.copy()
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True, errors="coerce")
    pieces = []
    for sym, grp in trades.groupby("symbol"):
        ind_df = _IND_BY_SYM.get(sym)
        if ind_df is None:
            continue
        merged = grp.merge(ind_df, left_on="entry_ts", right_on="bar_start_ts", how="left")
        pieces.append(merged)
    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True)


def evaluate_filter_folds(trades: pd.DataFrame, mask: pd.Series) -> tuple[int, float, float, int]:
    """Returns (folds_passed, mean_filtered_pf, mean_dsr, total_filtered_n)."""
    passes = 0
    filtered_pfs = []
    dsr_vals = []
    total_n = 0
    for fold in _FOLDS:
        in_fold = (trades["entry_ts"] >= fold.train_start) & (trades["entry_ts"] < fold.test_end) & (trades["entry_ts"] >= fold.test_start)
        # test window only
        in_test = (trades["entry_ts"] >= fold.test_start) & (trades["entry_ts"] < fold.test_end)
        test = trades[in_test]
        if len(test) == 0:
            continue
        base_pnl = test["pnl_net_bps"].to_numpy()
        base_pf = stats.profit_factor(base_pnl)
        fold_mask = mask.loc[test.index] if mask.index.equals(trades.index) else mask.iloc[in_test.to_numpy()]
        filt = test[fold_mask.fillna(False).astype(bool).to_numpy()]
        if len(filt) < 50:
            continue
        f_pnl = filt["pnl_net_bps"].to_numpy()
        f_pf = stats.profit_factor(f_pnl)
        filtered_pfs.append(f_pf)
        total_n += len(filt)
        if f_pf > base_pf * 1.1 and len(filt) > 500:
            passes += 1
        dsr = stats.deflated_sharpe_ratio(f_pnl / 100.0, n_trials=len(FILTER_GRID))
        dsr_vals.append(dsr)
    return passes, float(np.mean(filtered_pfs)) if filtered_pfs else 0.0, float(np.mean(dsr_vals)) if dsr_vals else 0.0, total_n


def _evaluate_parent_worker(parent_record: dict) -> tuple[str, list[dict], list[dict]]:
    """Worker: run full filter grid against one parent's enriched trade log."""
    pid = parent_record["cell_id"]
    trades = load_trades_parquet(pid, "B")
    if trades.empty:
        return pid, [], []
    trades = enrich_with_precomputed(trades)
    if trades.empty:
        return pid, [], []

    sweep_rows = []
    tracker_rows = []
    for filt_name, filt_fn in FILTER_GRID:
        try:
            mask = filt_fn(trades)
        except Exception:
            continue
        passes, mean_pf, mean_dsr, total_n = evaluate_filter_folds(trades, mask)
        gate = "PASS" if (passes >= 3 and mean_dsr > 0) else "FAIL"
        cell_id = f"D_{pid}_{filt_name}"
        cfg = storage.config_hash_of({"parent": pid, "filter": filt_name})
        sweep_rows.append({
            "cell_id": cell_id, "batch_id": BATCH_ID, "stage": "D",
            "parent_cell_id": pid, "gx_family": parent_record["gx_family"],
            "timeframe": parent_record["timeframe"], "direction": parent_record["direction"],
            "filter_params": {"filter_id": filt_name},
            "folds_passed": passes, "pf": mean_pf, "dsr": mean_dsr,
            "n_trades": total_n, "gate_result": gate, "config_hash": cfg,
            "utc_ts": tracker.utc_now_iso(),
        })
        tracker_rows.append({
            "cell_id": cell_id, "parent_cell_id": pid,
            "utc_ts": tracker.utc_now_iso(), "filter_id": filt_name,
            "filter_params_json": {"filter_id": filt_name}, "fold_idx": -1,
            "n": total_n, "pf": mean_pf, "dsr": mean_dsr,
            "folds_passed": passes, "gate_result": gate, "config_hash": cfg,
        })
    return pid, sweep_rows, tracker_rows


def main():
    log(f"Stage D starting. batch={BATCH_ID}")
    t_start = time.time()
    c_cells = storage.read_stage_cells("C", BATCH_ID)
    if c_cells.empty:
        tracker.log_decision(f"STAGE_D_{BATCH_ID}", "D", "KILL", "No Stage C output")
        return 2
    c_pass = c_cells[c_cells["gate_result"] == "PASS"]
    if c_pass.empty:
        tracker.log_decision(f"STAGE_D_{BATCH_ID}", "D", "KILL", "Zero Stage C survivors")
        return 2

    b_cells = storage.read_stage_cells("B", BATCH_ID).set_index("cell_id")
    parent_ids = list(c_pass["parent_cell_id"].unique())
    log(f"Stage C parents to evaluate: {len(parent_ids)}")

    global _FOLDS
    # Fold geometry must live entirely inside the non-holdout window. Computing
    # folds over [start, end] and post-filtering silently drops the final fold
    # (its test_end == end, overlapping holdout) and turns 4-fold WF into
    # 3-fold WF with a `>= 3` unanimity gate. Build folds directly on
    # [start, holdout_start] instead.
    _, holdout_start_ts, _ = stats.holdout_split(RESEARCH_START, RESEARCH_END, HOLDOUT_FRAC)
    _FOLDS = stats.purged_rolling_folds(RESEARCH_START, holdout_start_ts.isoformat(), n_folds=4, embargo_days=1)
    log(f"Using {len(_FOLDS)} folds over non-holdout window [{RESEARCH_START} .. {holdout_start_ts.isoformat()}]")

    universe = db.load_universe(UNIVERSE_PATH)
    n_workers = int(os.environ.get("GX_WORKERS", "14"))
    single_proc = os.environ.get("GX_SINGLE_PROCESS", "") == "1"

    # Group parents by TF so bundles / indicator frames load once
    tf_to_parents: dict[str, list[dict]] = {}
    for pid in parent_ids:
        if pid not in b_cells.index:
            continue
        parent = b_cells.loc[pid]
        tf_to_parents.setdefault(parent["timeframe"], []).append({
            "cell_id": pid, "gx_family": parent["gx_family"],
            "timeframe": parent["timeframe"], "direction": parent["direction"],
        })

    all_sweep_rows: list[dict] = []
    all_tracker_rows: list[dict] = []
    passing_parents = 0

    global _IND_BY_SYM, _TF
    for tf, parents in tf_to_parents.items():
        log(f"--- TF {tf}: {len(parents)} parents ---")
        tf_start = time.time()
        bundle = db.build_bundle(universe, tf, RESEARCH_START, RESEARCH_END)
        log(f"  Bundle loaded: {len(bundle.frames)} symbols, {bundle.mem_mb():.1f} MB")
        _IND_BY_SYM = {sym: build_indicator_frame(df) for sym, df in bundle.frames.items()}
        _TF = tf
        log(f"  precomputed indicator panels for {len(_IND_BY_SYM)} symbols in {time.time()-tf_start:.1f}s")

        if single_proc:
            results = []
            for i, p in enumerate(parents):
                results.append(_evaluate_parent_worker(p))
                if (i + 1) % 100 == 0:
                    log(f"    progress: {i+1}/{len(parents)} parents, elapsed {time.time()-tf_start:.1f}s")
        else:
            with Pool(processes=n_workers) as pool:
                results = []
                for i, res in enumerate(pool.imap_unordered(_evaluate_parent_worker, parents, chunksize=4)):
                    results.append(res)
                    if (i + 1) % 100 == 0:
                        log(f"    progress: {i+1}/{len(parents)} parents, elapsed {time.time()-tf_start:.1f}s")

        for pid, sweep_rows, tracker_rows in results:
            if any(r["gate_result"] == "PASS" for r in sweep_rows):
                passing_parents += 1
            all_sweep_rows.extend(sweep_rows)
            all_tracker_rows.extend(tracker_rows)
        log(f"  TF {tf} complete in {time.time()-tf_start:.1f}s")

    sweep_path = storage.write_sweep_cells("D", BATCH_ID, all_sweep_rows)
    tracker.append_rows("05_Stage_D_Filters", all_tracker_rows)
    tracker.log_decision(f"STAGE_D_{BATCH_ID}", "D", "PASS" if passing_parents else "KILL",
                         f"{passing_parents} parents carry ≥1 filter surviving 3/4 folds + DSR>0")
    tracker.log_compute("D", tracker.utc_now_iso(), tracker.utc_now_iso(),
                        time.time() - t_start, None, len(all_sweep_rows), str(sweep_path))
    log(f"Stage D complete. {passing_parents} parents with passing filter. Wall-clock {time.time()-t_start:.1f}s")
    return 0 if passing_parents else 2


if __name__ == "__main__":
    sys.exit(main())
