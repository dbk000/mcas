"""
Stage F — holdout portfolio sim. Plan §11.

For each Stage-E survivor, simulate on the reserved 15% holdout window under
slippage multipliers {1×, 1.5×, 2×}. Report agg PF, avg daily PF, n, DSR,
per-tier PF. SHORTLIST gate: agg_PF>1, avg_daily_PF>1, n>2000, DSR>0,
survives at 1.5×.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from GX_Research.lib import db, indicators as ind, exits, stats, storage, slippage, tracker
from GX_Research.triggers import gx_trend_triggers as gx
from GX_Research.scripts._common import (
    BATCH_ID, RESEARCH_START, RESEARCH_END, HOLDOUT_FRAC, UNIVERSE_PATH, log,
)


def rerun_trades_on_holdout(parent_b_row, symbol: str, df: pd.DataFrame, tier: str, tf: str, slip_multiplier: float) -> pd.DataFrame:
    """Re-simulate a Stage-B cell's trigger + exit on the holdout window for a single symbol."""
    family = parent_b_row["gx_family"]
    combo = parent_b_row["trigger_params"]
    if isinstance(combo, str):
        import json
        combo = json.loads(combo)
    exit_id = parent_b_row["exit_config_id"]
    exit_params = parent_b_row["exit_params"]
    if isinstance(exit_params, str):
        import json
        exit_params = json.loads(exit_params)
    direction = parent_b_row["direction"]

    if df.empty:
        return pd.DataFrame()
    open_ = df["open"].values
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    atr_arr = ind.atr(high, low, close, 14)
    sig_fn = gx.SIGNAL_FNS[family]
    try:
        long_e, short_e = sig_fn(combo, open_, high, low, close)
    except Exception:
        return pd.DataFrame()
    entries = long_e if direction == "long" else short_e
    if entries.sum() == 0:
        return pd.DataFrame()
    trades = exits.simulate_exit(exit_id, exit_params, entries, high, low, close, atr_arr, direction)
    if not trades:
        return pd.DataFrame()
    rows = []
    ts = df["bar_start_ts"].values
    for t in trades:
        if direction == "long":
            pnl_bps = (t.exit_price - t.entry_price) / t.entry_price * 10000.0
        else:
            pnl_bps = (t.entry_price - t.exit_price) / t.entry_price * 10000.0
        rows.append({
            "symbol": symbol, "tf": tf, "direction": direction,
            "entry_ts": ts[t.entry_idx] if t.entry_idx < len(ts) else pd.NaT,
            "r_multiple": t.r_multiple, "pnl_gross_bps": pnl_bps,
            "exit_type": t.exit_type, "bars_held": t.bars_held,
        })
    tdf = pd.DataFrame(rows)
    tdf["slip_bps_rt"] = slippage.total_cost_bps(tier, tf, slip_multiplier)
    tdf["pnl_net_bps"] = tdf["pnl_gross_bps"] - tdf["slip_bps_rt"]
    tdf["liquidity_tier"] = tier
    return tdf


def tier_pf_map(all_trades: pd.DataFrame) -> dict[str, float]:
    out = {f"tier_{i}_pf": 0.0 for i in range(1, 6)}
    for tier, grp in all_trades.groupby("liquidity_tier"):
        key = f"tier_{tier[-1]}_pf"  # T1→tier_1_pf
        if key in out:
            out[key] = stats.profit_factor(grp["pnl_net_bps"].values)
    return out


def main():
    log(f"Stage F starting. batch={BATCH_ID}")
    t_start = time.time()
    e_cells = storage.read_stage_cells("E", BATCH_ID)
    if e_cells.empty:
        tracker.log_decision(f"STAGE_F_{BATCH_ID}", "F", "KILL", "No Stage E output")
        return 2
    e_pass = e_cells[e_cells["gate_result"] == "PASS"]
    if e_pass.empty:
        tracker.log_decision(f"STAGE_F_{BATCH_ID}", "F", "KILL", "Zero Stage E survivors")
        return 2

    b_cells = storage.read_stage_cells("B", BATCH_ID).set_index("cell_id")
    universe = db.load_universe(UNIVERSE_PATH)
    qv_map = db.median_24h_quote_volume_usd(universe, RESEARCH_START, RESEARCH_END)
    tier_map = {s: slippage.assign_tier(qv_map.get(s, 0.0)) for s in universe}

    _, holdout_start_ts, holdout_end_ts = stats.holdout_split(RESEARCH_START, RESEARCH_END, HOLDOUT_FRAC)
    log(f"Holdout window: {holdout_start_ts.isoformat()} .. {holdout_end_ts.isoformat()}")

    shortlist_rows = []
    tracker_rows = []
    n_shortlist = 0

    for _, e_row in e_pass.iterrows():
        pid = e_row["parent_cell_id"]
        if pid not in b_cells.index:
            continue
        parent = b_cells.loc[pid]
        tf = parent["timeframe"]

        results_by_slip = {}
        all_trades_1x = None
        for slip_mult in (1.0, 1.5, 2.0):
            all_trades = []
            for sym in universe:
                tier = tier_map.get(sym, "T5")
                df = db.load_ohlcv(sym, tf, holdout_start_ts.isoformat(), holdout_end_ts.isoformat())
                tdf = rerun_trades_on_holdout(parent, sym, df, tier, tf, slip_mult)
                if not tdf.empty:
                    all_trades.append(tdf)
            if not all_trades:
                results_by_slip[slip_mult] = {"pf": 0.0, "avg_daily_pf": 0.0, "n": 0, "dsr": 0.0}
                continue
            full = pd.concat(all_trades, ignore_index=True)
            if slip_mult == 1.0:
                all_trades_1x = full
            pf = stats.profit_factor(full["pnl_net_bps"].values)
            adpf = stats.avg_daily_pf(full["pnl_net_bps"], full["entry_ts"])
            dsr = stats.deflated_sharpe_ratio(full["pnl_net_bps"].values / 100.0, n_trials=len(e_pass))
            results_by_slip[slip_mult] = {"pf": pf, "avg_daily_pf": adpf, "n": int(len(full)), "dsr": dsr}

        slip_1x = results_by_slip[1.0]
        slip_15x = results_by_slip[1.5]
        slip_2x = results_by_slip[2.0]
        tiers = tier_pf_map(all_trades_1x) if all_trades_1x is not None else {f"tier_{i}_pf": 0.0 for i in range(1, 6)}

        shortlist = (
            slip_1x["pf"] > 1.0
            and slip_1x["avg_daily_pf"] > 1.0
            and slip_1x["n"] > 2000
            and slip_1x["dsr"] > 0
            and slip_15x["pf"] > 1.0
        )
        decision = "SHORTLIST" if shortlist else "PARK"
        row = {
            "cell_id": f"F_{e_row['cell_id']}", "batch_id": BATCH_ID, "stage": "F",
            "parent_cell_id": e_row["cell_id"], "gx_family": parent["gx_family"],
            "timeframe": tf, "direction": parent["direction"],
            "n_trades": slip_1x["n"], "pf": slip_1x["pf"], "avg_daily_pf": slip_1x["avg_daily_pf"],
            "dsr": slip_1x["dsr"], "tier_pf": tiers,
            "gate_result": "PASS" if shortlist else "FAIL",
            "gate_reason": decision,
            "config_hash": storage.config_hash_of({"parent": pid, "stage": "F"}),
            "utc_ts": tracker.utc_now_iso(),
        }
        shortlist_rows.append(row)
        tracker_rows.append({
            "cell_id": row["cell_id"], "utc_ts": row["utc_ts"],
            "agg_pf": slip_1x["pf"], "avg_daily_pf": slip_1x["avg_daily_pf"],
            "n": slip_1x["n"], "dsr": slip_1x["dsr"],
            "slip_1x_pf": slip_1x["pf"], "slip_15x_pf": slip_15x["pf"], "slip_2x_pf": slip_2x["pf"],
            **tiers, "shortlist_decision": decision,
        })
        if shortlist:
            n_shortlist += 1
        tracker.log_decision(row["cell_id"], "F", decision,
                             f"pf={slip_1x['pf']:.2f},adpf={slip_1x['avg_daily_pf']:.2f},n={slip_1x['n']},dsr={slip_1x['dsr']:.3f},pf15x={slip_15x['pf']:.2f}")

    sweep_path = storage.write_sweep_cells("F", BATCH_ID, shortlist_rows)
    tracker.append_rows("08_Stage_F_Holdout", tracker_rows)
    tracker.log_compute("F", tracker.utc_now_iso(), tracker.utc_now_iso(),
                        time.time() - t_start, None, len(shortlist_rows), str(sweep_path))
    log(f"Stage F complete. {n_shortlist} SHORTLIST. Wall-clock {time.time()-t_start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
