"""
Stage B — Stop + exit sweep on Stage-A survivors.

For every Stage-A passing parent cell (capped at GX_STAGE_B_TOP_N by PF), sweep
the (stop_grid × exit_grid) cells from lib/exits.py. Key optimisation vs v1:
entries per (parent, symbol) are computed ONCE using the TF's IndicatorCache,
then reused across every (stop, exit) variant. Pool is used over cell variants.

Env knobs: GX_STAGE_B_TOP_N, GX_STAGE_B_EXITS, GX_WORKERS, GX_SINGLE_PROCESS
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
    BATCH_ID, RESEARCH_START, RESEARCH_END, HOLDOUT_FRAC, UNIVERSE_PATH, log, write_progress,
)

GATE_PF = 0.7
GATE_N = 2000

EXIT_IDS = os.environ.get("GX_STAGE_B_EXITS", "E1,E2,E3,E4").split(",")


# Module globals for workers (set once per parent cell)
_PARENT_ENTRIES: dict[str, np.ndarray] = {}   # sym -> bool array of entries (trigger already applied)
_PARENT_OHLCV: dict[str, dict[str, np.ndarray]] = {}  # sym -> {high, low, close, ts, atr}
_PARENT_TIER: dict[str, str] = {}
_PARENT_TF: str = ""


def _trade_to_rows(trades, symbol, tf, direction, ts_arr, tier):
    out = []
    cost = slippage.total_cost_bps(tier, tf, 1.0)
    for t in trades:
        if direction == "long":
            pnl_bps = (t.exit_price - t.entry_price) / t.entry_price * 10000.0
        else:
            pnl_bps = (t.entry_price - t.exit_price) / t.entry_price * 10000.0
        out.append({
            "symbol": symbol, "tf": tf, "direction": direction,
            "entry_idx": t.entry_idx, "exit_idx": t.exit_idx,
            "entry_ts": ts_arr[t.entry_idx] if t.entry_idx < len(ts_arr) else pd.NaT,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "r_multiple": t.r_multiple,
            "pnl_gross_bps": pnl_bps,
            "pnl_net_bps": pnl_bps - cost,
            "slip_bps_rt": cost,
            "liquidity_tier": tier,
            "exit_type": t.exit_type, "bars_held": t.bars_held,
        })
    return out


def _evaluate_variant_worker(args):
    """Worker: run one variant across all pre-cached symbols.

    Writes the trades parquet IN THE WORKER (only if the cell passes the Stage-B
    gate), then returns just metrics + the parquet path. This keeps trade records
    out of IPC and out of the parent's memory — previously the parent was holding
    every variant's full trade list (multi-GB per parent) before flushing.
    """
    exit_id, cfg_key, exit_params, direction, parent_cell_id, batch_id = args
    tf = _PARENT_TF
    all_rows = []
    for sym, entries in _PARENT_ENTRIES.items():
        o = _PARENT_OHLCV[sym]
        trades = exits.simulate_exit(exit_id, exit_params, entries, o["high"], o["low"], o["close"], o["atr"], direction)
        if not trades:
            continue
        all_rows.extend(_trade_to_rows(trades, sym, tf, direction, o["ts"], _PARENT_TIER.get(sym, "T5")))

    cell_cfg = {"parent": parent_cell_id, "exit_id": exit_id, "exit_params": exit_params}
    cfg_hash = storage.config_hash_of(cell_cfg)
    cell_id = f"B_{parent_cell_id}_{cfg_key}_{cfg_hash}"

    if not all_rows:
        return (cfg_key, exit_id, exit_params, cell_id, cfg_hash, None,
                {"n_trades": 0, "pf": 0.0, "win_rate": 0.0, "avg_r": 0.0, "avg_daily_pf": 0.0})

    df = pd.DataFrame(all_rows)
    del all_rows  # large — drop the list once DataFrame owns the data
    pnl = df["pnl_net_bps"].to_numpy()
    metrics = {
        "n_trades": int(len(df)),
        "pf": stats.profit_factor(pnl),
        "win_rate": stats.win_rate(pnl),
        "avg_r": stats.avg_r(df["r_multiple"].to_numpy()),
        "avg_daily_pf": stats.avg_daily_pf(df["pnl_net_bps"], df["entry_ts"]),
    }
    trades_path = None
    if metrics["pf"] >= GATE_PF and metrics["n_trades"] >= GATE_N:
        p = storage.write_trades(cell_id, batch_id, "B", df)
        trades_path = str(p) if p is not None else None
    del df
    return (cfg_key, exit_id, exit_params, cell_id, cfg_hash, trades_path, metrics)


def main():
    log(f"Stage B starting. batch={BATCH_ID}")
    t_start = time.time()

    survivors = storage.read_stage_cells("A", BATCH_ID)
    if survivors.empty:
        tracker.log_decision(f"STAGE_B_{BATCH_ID}", "B", "KILL", "No Stage A parquet")
        return 2
    survivors = survivors[survivors["gate_result"] == "PASS"]
    if survivors.empty:
        tracker.log_decision(f"STAGE_B_{BATCH_ID}", "B", "KILL", "Zero A survivors")
        return 2
    # Per-(TF, direction) cap. Plan §5.3 wanted all survivors (~530, ~240K
    # Stage-B cells), but 5m/15m make that intractable at 306 variants each.
    # Flat top-N by global PF collapses to 1d-only because low-n buckets
    # inflate PF — hence the 2026-04-15 rerun where Stage B saw 1d-shorts
    # exclusively. This cap guarantees every (TF, direction) is represented.
    top_n_per_bucket = int(os.environ.get("GX_STAGE_B_TOP_N_PER_TF_DIR", "15"))
    legacy_cap = os.environ.get("GX_STAGE_B_TOP_N")
    if legacy_cap:
        log(f"WARNING: legacy GX_STAGE_B_TOP_N={legacy_cap} is IGNORED; use GX_STAGE_B_TOP_N_PER_TF_DIR.")
    survivors = (
        survivors.sort_values("pf", ascending=False)
                 .groupby(["timeframe", "direction"], group_keys=False)
                 .head(top_n_per_bucket)
                 .reset_index(drop=True)
    )
    bucket_counts = survivors.groupby(["timeframe", "direction"]).size().to_dict()
    log(f"Parent cells to sweep: {len(survivors)} (cap {top_n_per_bucket} per TF×direction, buckets: {bucket_counts})")

    universe = db.load_universe(UNIVERSE_PATH)
    _, holdout_start_ts, _ = stats.holdout_split(RESEARCH_START, RESEARCH_END, HOLDOUT_FRAC)
    qv_map = db.median_24h_quote_volume_usd(universe, RESEARCH_START, RESEARCH_END)
    tier_map = {s: slippage.assign_tier(qv_map.get(s, 0.0)) for s in universe}

    n_workers = int(os.environ.get("GX_WORKERS", "14"))
    single_proc = os.environ.get("GX_SINGLE_PROCESS", "") == "1"

    # Group parents by TF so bundles load once
    parents_by_tf: dict[str, list] = {}
    for _, row in survivors.iterrows():
        parents_by_tf.setdefault(row["timeframe"], []).append(row)

    n_cells_total = 0
    n_pass_total = 0
    shard_paths: list[str] = []
    parents_done = 0
    parents_total = sum(len(v) for v in parents_by_tf.values())
    maxtasks = int(os.environ.get("GX_WORKER_MAXTASKS", "16"))

    global _PARENT_ENTRIES, _PARENT_OHLCV, _PARENT_TIER, _PARENT_TF

    for tf, parents in parents_by_tf.items():
        log(f"--- TF {tf}: {len(parents)} parent cells ---")
        tf_start = time.time()
        bundle = db.build_bundle(universe, tf, RESEARCH_START, RESEARCH_END)
        log(f"  Bundle loaded: {len(bundle.frames)} symbols, {bundle.mem_mb():.1f} MB")
        # Precompute IndicatorCache + masks per symbol for this TF. Drain the
        # bundle as we go so peak RAM at fork time is the extracted arrays only,
        # not bundle + extracted arrays in parallel.
        ic_by_sym = {}
        ohlcv_by_sym = {}
        for sym in list(bundle.frames.keys()):
            df_sym = bundle.frames[sym]
            mask = (df_sym["bar_start_ts"] < holdout_start_ts).to_numpy()
            if mask.sum() < 100:
                del bundle.frames[sym]
                continue
            open_ = df_sym["open"].to_numpy()[mask]
            high = df_sym["high"].to_numpy()[mask]
            low = df_sym["low"].to_numpy()[mask]
            close = df_sym["close"].to_numpy()[mask]
            ts = df_sym["bar_start_ts"].to_numpy()[mask]
            ic = ind.IndicatorCache(open_, high, low, close, None)
            ic_by_sym[sym] = ic
            ohlcv_by_sym[sym] = {"high": high, "low": low, "close": close, "ts": ts, "atr": ic.atr(14)}
            del bundle.frames[sym]
        del bundle
        gc.collect()

        for parent in parents:
            parent_start = time.time()
            family = parent["gx_family"]
            combo = parent["trigger_params"]
            if isinstance(combo, str):
                combo = json.loads(combo)
            direction = parent["direction"]
            sig_fn = gx.SIGNAL_FNS[family]

            # Compute entries once per parent using the TF's IndicatorCache
            entries_by_sym = {}
            for sym, ic in ic_by_sym.items():
                try:
                    long_e, short_e = sig_fn(combo, ic)
                except Exception:
                    continue
                entries = long_e if direction == "long" else short_e
                if entries.any():
                    entries_by_sym[sym] = entries
            if not entries_by_sym:
                parents_done += 1
                continue

            _PARENT_ENTRIES = entries_by_sym
            _PARENT_OHLCV = {s: ohlcv_by_sym[s] for s in entries_by_sym if s in ohlcv_by_sym}
            _PARENT_TIER = tier_map
            _PARENT_TF = tf

            # Build variant list for this parent
            variants = []
            for exit_id in EXIT_IDS:
                for cfg_key, exit_params in exits.iter_stage_b_configs(exit_id):
                    variants.append((exit_id, cfg_key, exit_params, direction, parent["cell_id"], BATCH_ID))

            parent_sweep_rows: list[dict] = []
            parent_tracker_rows: list[dict] = []
            parent_pass = 0

            def _consume_variant(cfg_key, exit_id, exit_params, cell_id, cfg_hash, trades_path, metrics):
                nonlocal parent_pass
                gate = "PASS" if (metrics["pf"] >= GATE_PF and metrics["n_trades"] >= GATE_N) else "FAIL"
                row = {
                    "cell_id": cell_id, "batch_id": BATCH_ID, "stage": "B",
                    "parent_cell_id": parent["cell_id"],
                    "gx_family": family, "timeframe": tf, "direction": direction,
                    "trigger_params": combo, "exit_config_id": exit_id, "exit_params": exit_params,
                    **metrics,
                    "gate_result": gate,
                    "gate_reason": f"pf={metrics['pf']:.2f},n={metrics['n_trades']}",
                    "config_hash": cfg_hash,
                    "trades_path": trades_path,
                    "utc_ts": tracker.utc_now_iso(),
                }
                parent_sweep_rows.append(row)
                parent_tracker_rows.append({
                    "cell_id": cell_id, "parent_cell_id": parent["cell_id"],
                    "utc_ts": row["utc_ts"],
                    "stop_params_json": {"k_stop": exit_params.get("k_stop", 0), "bar_range_mult": exit_params.get("bar_range_mult", 0)},
                    "exit_config_id": exit_id, "exit_params_json": exit_params,
                    **{k: metrics[k] for k in ("n_trades", "pf", "win_rate", "avg_r", "avg_daily_pf")},
                    "gate_result": gate, "config_hash": cfg_hash,
                })
                if gate == "PASS":
                    parent_pass += 1

            if single_proc:
                for v in variants:
                    r = _evaluate_variant_worker(v)
                    _consume_variant(*r)
            else:
                # imap_unordered + chunksize=1 streams metrics-only results back
                # one at a time — trade records never leave the worker (they were
                # written to parquet there). Parent RAM is bounded by the row
                # dicts for the current parent only.
                with Pool(processes=n_workers, maxtasksperchild=maxtasks) as pool:
                    for r in pool.imap_unordered(_evaluate_variant_worker, variants, chunksize=1):
                        _consume_variant(*r)

            # Flush this parent's sweep rows to its own parquet shard.
            shard_token = parent["cell_id"].replace("/", "_")
            shard_path = storage.write_sweep_cells("B", BATCH_ID, parent_sweep_rows, shard=shard_token)
            shard_paths.append(str(shard_path))
            tracker.append_rows("03_Stage_B_StopExit", parent_tracker_rows)

            n_cells_total += len(parent_sweep_rows)
            n_pass_total += parent_pass
            parents_done += 1

            log(f"    parent {parent['cell_id']}: {len(variants)} variants, {parent_pass} PASS, flushed → {shard_path.name}, elapsed {time.time()-parent_start:.1f}s")

            write_progress("B", BATCH_ID, {
                "last_parent_completed": parent["cell_id"],
                "parents_done": parents_done,
                "parents_total": parents_total,
                "cells_written": n_cells_total,
                "pass_so_far": n_pass_total,
                "shards": shard_paths,
            })

            # Release per-parent state before the next parent's Pool forks.
            _PARENT_ENTRIES = {}
            _PARENT_OHLCV = {}
            del entries_by_sym, variants, parent_sweep_rows, parent_tracker_rows
            gc.collect()

        # Release per-TF arrays before the next TF's bundle loads.
        ic_by_sym.clear()
        ohlcv_by_sym.clear()
        gc.collect()
        log(f"  TF {tf} complete in {time.time()-tf_start:.1f}s")

    tracker.log_decision(f"STAGE_B_{BATCH_ID}", "B", "PASS" if n_pass_total else "KILL",
                         f"{n_pass_total}/{n_cells_total} cells passed (PF≥{GATE_PF}, n≥{GATE_N})")
    tracker.log_compute("B", tracker.utc_now_iso(), tracker.utc_now_iso(),
                        time.time() - t_start, None, n_cells_total, ";".join(shard_paths))
    log(f"Stage B complete. {n_pass_total}/{n_cells_total} PASS across {len(shard_paths)} shards. Wall-clock {time.time()-t_start:.1f}s")
    return 0 if n_pass_total > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
