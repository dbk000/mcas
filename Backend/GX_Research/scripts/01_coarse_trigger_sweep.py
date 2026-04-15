"""
Stage A — coarse trigger geometry sweep (plan §4, §12).

Parallelised with multiprocessing.Pool + COW globals per the canonical MCAS
pattern (root CLAUDE.md §2 preflight 8, simulation canon). Sequence:

  1. Per TF: parallel OHLCV load → module globals _BUNDLE, _IC (IndicatorCache per sym)
  2. Build work list = (family, combo_idx, direction) tuples
  3. Pool(workers) → each worker evaluates one cell across all symbols (inherits globals)
  4. Parent aggregates, writes parquet + tracker

Env knobs:
  GX_WORKERS          (default 14)  number of Pool workers
  GX_SINGLE_PROCESS   (default unset) = '1' forces serial evaluation, used for parity tests
"""
from __future__ import annotations

import gc
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

from GX_Research.lib import db, indicators as ind, exits, stats, storage, slippage, tracker
from GX_Research.triggers import gx_trend_triggers as gx
from GX_Research.scripts._common import (
    BATCH_ID, RESEARCH_START, RESEARCH_END, HOLDOUT_FRAC,
    TIMEFRAMES, UNIVERSE_PATH, log, write_progress,
)

GATE_PF = 0.5
GATE_N = 500
DIAG_PF_UNDER = 0.3

# -------- Module-level COW globals populated per-TF before Pool spawn --------
_TF: str = ""
_BUNDLE: db.OhlcvBundle | None = None
_IC_BY_SYM: dict[str, ind.IndicatorCache] = {}
_MASK_BY_SYM: dict[str, np.ndarray] = {}
_TS_BY_SYM: dict[str, np.ndarray] = {}
_TIER_BY_SYM: dict[str, str] = {}
_COMBOS: list[tuple[str, dict]] = []


def _prepare_tf_globals(bundle: db.OhlcvBundle, tier_map: dict[str, str], holdout_start_ts: pd.Timestamp):
    """Populate module globals for one TF. Called in parent before forking workers.

    The bundle is consumed here: we pull the np arrays out of each DataFrame, then
    the caller drops the bundle reference so fork()ed workers don't inherit it.
    """
    global _BUNDLE, _IC_BY_SYM, _MASK_BY_SYM, _TS_BY_SYM, _TIER_BY_SYM, _TF
    _BUNDLE = None  # released; workers must not rely on the DataFrames
    _TF = bundle.timeframe
    _IC_BY_SYM = {}
    _MASK_BY_SYM = {}
    _TS_BY_SYM = {}
    _TIER_BY_SYM = dict(tier_map)
    for sym in list(bundle.frames.keys()):
        df = bundle.frames[sym]
        mask = (df["bar_start_ts"] < holdout_start_ts).to_numpy()
        if mask.sum() < 100:
            continue
        open_ = df["open"].to_numpy()[mask]
        high = df["high"].to_numpy()[mask]
        low = df["low"].to_numpy()[mask]
        close = df["close"].to_numpy()[mask]
        volume = df["volume"].to_numpy()[mask]
        _IC_BY_SYM[sym] = ind.IndicatorCache(open_, high, low, close, volume)
        _MASK_BY_SYM[sym] = mask
        _TS_BY_SYM[sym] = df["bar_start_ts"].to_numpy()[mask]
        # Drop the DataFrame from the bundle as we go so peak RAM is bounded by
        # the extracted numpy arrays, not bundle + extracted arrays in parallel.
        del bundle.frames[sym]
    log(f"  precomputed IndicatorCache for {len(_IC_BY_SYM)} symbols on TF={_TF}")


def _prewarm_ic_cache(combos: list[tuple[str, dict]]):
    """Populate each IndicatorCache._cache in the PARENT before forking workers.

    Why: if the cache is cold at fork time, each of the 14 workers lazily fills
    its own copy — 14× duplicated indicator arrays (tens of GB on 5m). If the
    cache is warm at fork, workers only *read* the shared pages (COW preserved
    for the large numpy buffers; only small Python header pages churn on refcount
    bumps). Also precomputes atr(14) used by the Stage-A baseline exit simulator.
    """
    t0 = time.time()
    for sym, ic in _IC_BY_SYM.items():
        # Warm atr(14) for the baseline exit path.
        ic.atr(14)
        # Every family's signal function is a cache-populating read of indicators.
        for family, combo in combos:
            try:
                gx.SIGNAL_FNS[family](combo, ic)
            except Exception:
                pass
    log(f"  prewarmed IndicatorCache for {len(_IC_BY_SYM)} symbols in {time.time()-t0:.1f}s")


def _evaluate_cell_worker(args: tuple[str, int, str]) -> tuple[str, int, str, dict]:
    """Worker: evaluate one (family, combo_idx, direction) cell against all symbols in globals."""
    family, combo_idx, direction = args
    combo = _COMBOS[combo_idx][1]
    sig_fn = gx.SIGNAL_FNS[family]
    tf = _TF

    all_pnl = []
    all_r = []
    all_ts = []
    all_entry_in_range = 0
    all_losers = 0

    for sym, ic in _IC_BY_SYM.items():
        try:
            long_e, short_e = sig_fn(combo, ic)
        except Exception:
            continue
        entries = long_e if direction == "long" else short_e
        if not entries.any():
            continue
        atr_arr = ic.atr(14)
        trades = exits.simulate_baseline_stage_a(entries, ic.high, ic.low, ic.close, atr_arr, direction)
        if not trades:
            continue
        tier = _TIER_BY_SYM.get(sym, "T5")
        cost = slippage.total_cost_bps(tier, tf, 1.0)
        ts = _TS_BY_SYM[sym]
        for t in trades:
            if direction == "long":
                pnl_bps = (t.exit_price - t.entry_price) / t.entry_price * 10000.0
            else:
                pnl_bps = (t.entry_price - t.exit_price) / t.entry_price * 10000.0
            pnl_net = pnl_bps - cost
            all_pnl.append(pnl_net)
            all_r.append(t.r_multiple)
            all_ts.append(ts[t.entry_idx] if t.entry_idx < len(ts) else ts[-1])
            if t.r_multiple < 0:
                all_losers += 1
                if t.exit_idx == t.entry_idx:
                    all_entry_in_range += 1

    if not all_pnl:
        return (family, combo_idx, direction, {"n_trades": 0, "pf": 0.0, "win_rate": 0.0, "avg_r": 0.0, "avg_daily_pf": 0.0, "pct_stop_inside_entry_range": 0.0})

    pnl = np.asarray(all_pnl, dtype=np.float64)
    r = np.asarray(all_r, dtype=np.float64)
    metrics = {
        "n_trades": int(pnl.size),
        "pf": stats.profit_factor(pnl),
        "win_rate": stats.win_rate(pnl),
        "avg_r": stats.avg_r(r),
        "avg_daily_pf": stats.avg_daily_pf(pd.Series(pnl), pd.Series(pd.to_datetime(all_ts, utc=True))),
        "pct_stop_inside_entry_range": (all_entry_in_range / all_losers) if all_losers > 0 else 0.0,
    }
    return (family, combo_idx, direction, metrics)


def main():
    log(f"Stage A starting. batch={BATCH_ID}  window={RESEARCH_START}..{RESEARCH_END}")
    t_start = time.time()
    universe = db.load_universe(UNIVERSE_PATH)
    log(f"Universe: {len(universe)} symbols")

    _, holdout_start_ts, _ = stats.holdout_split(RESEARCH_START, RESEARCH_END, HOLDOUT_FRAC)
    log(f"Holdout boundary: {holdout_start_ts.isoformat()}")

    log("Computing per-symbol 24h median quote volume...")
    qv_map = db.median_24h_quote_volume_usd(universe, RESEARCH_START, RESEARCH_END)
    tier_map = {s: slippage.assign_tier(qv_map.get(s, 0.0)) for s in universe}

    # Populate combos list as module global so workers can index into it via combo_idx.
    global _COMBOS
    _COMBOS = list(gx.all_combos())
    log(f"Total trigger combos: {len(_COMBOS)}")

    n_workers = int(os.environ.get("GX_WORKERS", "14"))
    single_proc = os.environ.get("GX_SINGLE_PROCESS", "") == "1"
    log(f"Pool workers: {n_workers}  single_process={single_proc}")

    total_cells = 0
    total_pass = 0
    shard_paths: list[str] = []
    maxtasks = int(os.environ.get("GX_WORKER_MAXTASKS", "100"))

    for tf_idx, tf in enumerate(TIMEFRAMES):
        log(f"--- TF {tf} ---")
        tf_start = time.time()
        bundle = db.build_bundle(universe, tf, RESEARCH_START, RESEARCH_END)
        log(f"  Bundle loaded: {len(bundle.frames)} symbols, {bundle.mem_mb():.1f} MB")
        _prepare_tf_globals(bundle, tier_map, holdout_start_ts)
        # bundle.frames was drained inside _prepare_tf_globals; release the shell.
        del bundle
        gc.collect()

        # Warm the per-symbol IndicatorCache so forked workers inherit populated
        # caches as read-only shared memory instead of each worker filling its own.
        _prewarm_ic_cache(_COMBOS)
        gc.collect()

        # Build work list — heaviest-first (GX3/GX5 first so tail is cheap)
        family_order = ["GX5", "GX3", "GX2", "GX4", "GX1"]
        work = []
        for fam in family_order:
            for i, (f, c) in enumerate(_COMBOS):
                if f != fam:
                    continue
                for direction in ("long", "short"):
                    work.append((fam, i, direction))
        log(f"  work list: {len(work)} cells")

        tf_sweep_rows: list[dict] = []
        tf_tracker_rows: list[dict] = []

        def _consume(family, combo_idx, direction, metrics):
            nonlocal total_cells, total_pass
            combo = _COMBOS[combo_idx][1]
            cell_cfg = {"family": family, "combo": combo, "tf": tf, "direction": direction}
            cfg_hash = storage.config_hash_of(cell_cfg)
            cell_id = f"A_{family}_{tf}_{direction}_{cfg_hash}"
            gate = "PASS" if (metrics["pf"] >= GATE_PF and metrics["n_trades"] >= GATE_N) else "FAIL"
            row = {
                "cell_id": cell_id, "batch_id": BATCH_ID, "stage": "A",
                "parent_cell_id": None, "gx_family": family, "timeframe": tf, "direction": direction,
                "trigger_params": combo,
                "exit_config_id": "baseline_A",
                "exit_params": {"k_stop": 2.0, "tp1_r": 1.0, "tp2_r": 2.0, "split_tp1": 0.5, "time_stop_bars": 48},
                **metrics,
                "gate_result": gate,
                "gate_reason": f"pf={metrics['pf']:.2f},n={metrics['n_trades']}",
                "config_hash": cfg_hash,
                "utc_ts": tracker.utc_now_iso(),
            }
            tf_sweep_rows.append(row)
            tf_tracker_rows.append({
                "cell_id": cell_id, "utc_ts": row["utc_ts"], "gx_family": family,
                "trigger_params_json": combo, "tf": tf, "direction": direction,
                "n_trades": metrics["n_trades"], "pf": metrics["pf"],
                "win_rate": metrics["win_rate"], "avg_r": metrics["avg_r"],
                "pct_stop_inside_entry_range": metrics["pct_stop_inside_entry_range"],
                "gate_result": gate, "config_hash": cfg_hash,
            })
            total_cells += 1
            if gate == "PASS":
                total_pass += 1

        if single_proc:
            for w in work:
                family, combo_idx, direction, metrics = _evaluate_cell_worker(w)
                _consume(family, combo_idx, direction, metrics)
        else:
            with Pool(processes=n_workers, maxtasksperchild=maxtasks) as pool:
                for i, res in enumerate(pool.imap_unordered(_evaluate_cell_worker, work, chunksize=4)):
                    family, combo_idx, direction, metrics = res
                    _consume(family, combo_idx, direction, metrics)
                    if (i + 1) % 200 == 0:
                        log(f"  progress: {i+1}/{len(work)} cells, elapsed {time.time()-tf_start:.1f}s")

        # Incremental flush: one parquet shard per TF so a crash preserves prior TFs.
        shard_path = storage.write_sweep_cells("A", BATCH_ID, tf_sweep_rows, shard=f"tf{tf}")
        shard_paths.append(str(shard_path))
        tracker.append_rows("02_Stage_A_Triggers", tf_tracker_rows)
        log(f"  TF {tf} flushed {len(tf_sweep_rows)} rows → {shard_path}")
        log(f"  TF {tf} complete: {len(work)} cells in {time.time()-tf_start:.1f}s")

        write_progress("A", BATCH_ID, {
            "last_tf_completed": tf,
            "tf_index": tf_idx + 1,
            "tf_total": len(TIMEFRAMES),
            "cells_written": total_cells,
            "pass_so_far": total_pass,
            "shards": shard_paths,
        })

        # Release per-TF globals before loading the next bundle.
        _IC_BY_SYM.clear()
        _MASK_BY_SYM.clear()
        _TS_BY_SYM.clear()
        del tf_sweep_rows, tf_tracker_rows
        gc.collect()

    t_total = time.time() - t_start
    tracker.log_decision(f"STAGE_A_{BATCH_ID}", "A",
                         "PASS" if total_pass > 0 else "KILL",
                         f"{total_pass}/{total_cells} cells passed Stage-A gate")
    tracker.log_compute("A", tracker.utc_now_iso(), tracker.utc_now_iso(),
                        t_total, None, total_cells, ";".join(shard_paths))
    log(f"Stage A complete. {total_pass}/{total_cells} cells PASS across {len(shard_paths)} shards. Wall-clock {t_total:.1f}s")
    return 0 if total_pass > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
