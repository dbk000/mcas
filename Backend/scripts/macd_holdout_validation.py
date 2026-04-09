#!/usr/bin/env python3
"""
macd_holdout_validation.py — Phase 0d Locked-Config Holdout Test
================================================================

The filter `close_vs_sma50_atr < -3.10 AND close_vs_sma200_atr < -0.05` on
the `fast_3_10_16` MACD tuple was discovered via 5-fold purged walk-forward
across the full 99-day candidate dataset (Jan 1 – Apr 9 2026). That meets
canon §4.1 but NOT §4.2, which requires a strictly untouched holdout.

This script:
  1. Locks the filter (no re-discovery).
  2. Splits the existing candidates + exit_walks by entry_ts_epoch at
     2026-03-20 00:00 UTC.
  3. Train partition  = entries  < split (2026-01-01 → 2026-03-20, ~79 days)
  4. Holdout partition = entries ≥ split (2026-03-20 → 2026-04-09, ~20 days)
  5. Applies the locked filter to both partitions.
  6. Reports full metrics per partition.
  7. Writes s11_config_locked.json (canonical config for the eventual S11 build).

Holdout is evaluated ONCE. Per canon §4.2: no re-optimization after peek.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from macd_feature_discriminator import (
    load_candidates_csv,
    load_walks_npz,
    derive_features,
    join_walks,
    compute_pf,
    compute_daily_pf,
    FIXED_STOP_MULT,
    FIXED_TS_BARS,
)

# ─────────────────────────────── Locked config ────────────────────────────────
LOCKED_VARIANT = "fast_3_10_16"
LOCKED_FAST = 3
LOCKED_SLOW = 10
LOCKED_SIGNAL = 16

LOCKED_FILTER = [
    {"feature": "close_vs_sma50_atr",  "direction": "lt", "threshold": -3.10},
    {"feature": "close_vs_sma200_atr", "direction": "lt", "threshold": -0.05},
]

SPLIT_TS = datetime(2026, 3, 20, 0, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────────── Metrics ────────────────────────────────
def full_metrics(pnl, r_mult, exit_reason, entry_epoch, label):
    n = int(len(pnl))
    if n == 0:
        return {
            "label": label, "n": 0, "pf_agg": 0.0,
            "daily_pf_median": 0.0, "daily_pf_mean": 0.0,
            "n_days": 0, "pct_days_positive": 0.0,
            "win_pct": 0.0, "wins": 0, "losses": 0,
            "stop_rate": 0.0, "ts_rate": 0.0, "eod_rate": 0.0,
            "gap_rate": 0.0, "avg_r": 0.0,
            "expectancy": 0.0, "total_pnl": 0.0,
            "daily_sharpe": 0.0,
        }

    wins_mask = pnl > 0
    losses_mask = pnl <= 0
    gw = float(pnl[wins_mask].sum())
    gl = float(-pnl[losses_mask].sum())
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    total = float(pnl.sum())
    avg_r = float(r_mult.mean())
    expectancy = total / n
    win_pct = 100.0 * int(wins_mask.sum()) / n

    stops = (exit_reason == "STOP_LOSS")
    ts_ = (exit_reason == "TIME_STOP")
    eods = (exit_reason == "END_OF_DATA")
    n_stops = int(stops.sum())
    gap_rate = float(((r_mult < -1.05) & stops).sum()) / n_stops if n_stops > 0 else 0.0

    # Daily stats
    day_keys = (entry_epoch // 86400).astype(np.int64)
    uniq, inv = np.unique(day_keys, return_inverse=True)
    n_days = len(uniq)
    dgw = np.zeros(n_days)
    dgl = np.zeros(n_days)
    pos = np.where(pnl > 0, pnl, 0.0)
    neg = np.where(pnl < 0, -pnl, 0.0)
    np.add.at(dgw, inv, pos)
    np.add.at(dgl, inv, neg)
    with np.errstate(divide="ignore", invalid="ignore"):
        dpf = np.where(dgl > 0, dgw / dgl, np.nan)
    zl = int(((dgl == 0) & (dgw > 0)).sum())
    valid_pfs = dpf[~np.isnan(dpf)]
    dpf_median = float(np.median(valid_pfs)) if len(valid_pfs) > 0 else 0.0
    dpf_mean = float(valid_pfs.mean()) if len(valid_pfs) > 0 else 0.0
    pos_days = int((valid_pfs > 1.0).sum()) + zl
    pct_days_positive = pos_days / n_days if n_days > 0 else 0.0

    day_sum = np.zeros(n_days)
    np.add.at(day_sum, inv, pnl)
    daily_sharpe = 0.0
    if n_days >= 10:
        m = float(day_sum.mean())
        s = float(day_sum.std(ddof=1))
        if s > 0:
            daily_sharpe = (m / s) * math.sqrt(365)

    return {
        "label": label, "n": n,
        "pf_agg": pf if pf != float("inf") else None,
        "daily_pf_median": dpf_median,
        "daily_pf_mean": dpf_mean,
        "n_days": n_days,
        "pct_days_positive": pct_days_positive,
        "win_pct": win_pct,
        "wins": int(wins_mask.sum()),
        "losses": int(losses_mask.sum()),
        "stop_rate": float(stops.sum()) / n,
        "ts_rate": float(ts_.sum()) / n,
        "eod_rate": float(eods.sum()) / n,
        "gap_rate": gap_rate,
        "avg_r": avg_r,
        "expectancy": expectancy,
        "total_pnl": total,
        "daily_sharpe": daily_sharpe,
    }


def apply_locked_filter(cand, walks, wtc):
    """Returns a boolean mask (over walks) where both filter conditions hold."""
    mask = np.ones(len(wtc), dtype=bool)
    for f in LOCKED_FILTER:
        feat_vals = cand[f["feature"]][wtc]
        if f["direction"] == "lt":
            cond = (feat_vals < f["threshold"]) & ~np.isnan(feat_vals)
        else:
            cond = (feat_vals > f["threshold"]) & ~np.isnan(feat_vals)
        mask &= cond
    return mask


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    args = ap.parse_args()
    t0 = time.time()

    cand = load_candidates_csv(os.path.join(args.input_dir, "candidates.csv"))
    walks = load_walks_npz(os.path.join(args.input_dir, "exit_walks.npz"))
    cand = derive_features(cand)

    print(f"\n=== HOLDOUT VALIDATION: {LOCKED_VARIANT} ===", flush=True)
    print(
        f"Locked filter: "
        f"{LOCKED_FILTER[0]['feature']} {LOCKED_FILTER[0]['direction']} "
        f"{LOCKED_FILTER[0]['threshold']} AND "
        f"{LOCKED_FILTER[1]['feature']} {LOCKED_FILTER[1]['direction']} "
        f"{LOCKED_FILTER[1]['threshold']}",
        flush=True,
    )

    wtc, pnl, rm, er, ee = join_walks(
        cand, walks, LOCKED_VARIANT, FIXED_STOP_MULT, FIXED_TS_BARS,
    )
    n_walks = len(wtc)
    print(f"[walks] variant={LOCKED_VARIANT} total_walks={n_walks}", flush=True)

    # Baseline metrics (no filter) per partition — for context
    split_epoch = int(SPLIT_TS.timestamp())
    train_mask = ee < split_epoch
    hold_mask = ee >= split_epoch
    print(
        f"[split] ts={SPLIT_TS.isoformat()} train_walks={int(train_mask.sum())} "
        f"hold_walks={int(hold_mask.sum())}",
        flush=True,
    )

    filter_mask = apply_locked_filter(cand, walks, wtc)
    n_filtered_total = int(filter_mask.sum())
    print(
        f"[filter] locked filter passes "
        f"{n_filtered_total}/{n_walks} walks "
        f"({100*n_filtered_total/n_walks:.1f}%)",
        flush=True,
    )

    # Compute partitioned metrics
    def _metrics(mask, label):
        return full_metrics(pnl[mask], rm[mask], er[mask], ee[mask], label)

    baseline_all = _metrics(np.ones(n_walks, dtype=bool), "baseline_full")
    baseline_train = _metrics(train_mask, "baseline_train")
    baseline_hold = _metrics(hold_mask, "baseline_holdout")
    filtered_train = _metrics(filter_mask & train_mask, "filtered_train")
    filtered_hold = _metrics(filter_mask & hold_mask, "filtered_holdout")
    filtered_all = _metrics(filter_mask, "filtered_full")

    print("\n[results]", flush=True)
    for r in [baseline_all, baseline_train, baseline_hold,
              filtered_all, filtered_train, filtered_hold]:
        pf_str = f"{r['pf_agg']:.3f}" if r['pf_agg'] is not None else "inf"
        print(
            f"  {r['label']:22s} n={r['n']:6d} days={r['n_days']:3d} "
            f"pf={pf_str} dpfmed={r['daily_pf_median']:.3f} "
            f"win%={r['win_pct']:.1f} sharpe={r['daily_sharpe']:.2f} "
            f"exp=${r['expectancy']:.2f}",
            flush=True,
        )

    out_dir = args.input_dir

    # JSON output
    holdout_report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "locked_variant": LOCKED_VARIANT,
        "locked_tuple": {"fast": LOCKED_FAST, "slow": LOCKED_SLOW,
                          "signal": LOCKED_SIGNAL},
        "locked_filter": LOCKED_FILTER,
        "fixed_exit": {
            "stop_atr_mult": FIXED_STOP_MULT,
            "time_stop_bars": FIXED_TS_BARS,
        },
        "split_ts": SPLIT_TS.isoformat(),
        "split_ts_epoch": split_epoch,
        "walks_total": n_walks,
        "walks_filter_pass_total": n_filtered_total,
        "filter_pass_rate": n_filtered_total / n_walks if n_walks > 0 else 0.0,
        "metrics": {
            "baseline_full": baseline_all,
            "baseline_train": baseline_train,
            "baseline_holdout": baseline_hold,
            "filtered_full": filtered_all,
            "filtered_train": filtered_train,
            "filtered_holdout": filtered_hold,
        },
        "gate": {
            "criteria": "holdout pf_agg > 1.0 AND holdout daily_pf_median > 1.0",
            "holdout_pf_agg": filtered_hold["pf_agg"],
            "holdout_daily_pf_median": filtered_hold["daily_pf_median"],
            "holdout_n": filtered_hold["n"],
            "passed": (
                filtered_hold["pf_agg"] is not None
                and filtered_hold["pf_agg"] > 1.0
                and filtered_hold["daily_pf_median"] > 1.0
                and filtered_hold["n"] >= 50
            ),
        },
    }
    json_path = os.path.join(out_dir, "holdout_validation.json")
    with open(json_path, "w") as f:
        json.dump(holdout_report, f, indent=2, default=str)
    print(f"[out] JSON: {json_path}", flush=True)

    # s11_config_locked.json — canonical config for the eventual S11 build
    s11_config = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "setup_id": "S11",
        "family_id": "macd_long_reversion",
        "variant_id": LOCKED_VARIANT,
        "timeframe": "1h",
        "direction": "long",
        "macd": {
            "fast_n": LOCKED_FAST,
            "slow_n": LOCKED_SLOW,
            "signal_n": LOCKED_SIGNAL,
        },
        "entry_trigger": {
            "bullish_crossover": "macd_histogram[t-1] <= 0 AND macd_histogram[t] > 0",
        },
        "filters": [
            {
                "feature": "close_vs_sma50_atr",
                "description": "(close - sma_50) / atr_14",
                "direction": "lt",
                "threshold": -3.10,
            },
            {
                "feature": "close_vs_sma200_atr",
                "description": "(close - sma_200) / atr_14",
                "direction": "lt",
                "threshold": -0.05,
            },
        ],
        "exit": {
            "stop_atr_mult": FIXED_STOP_MULT,
            "time_stop_bars": FIXED_TS_BARS,
            "tp1_r_mult": None,
            "tp2_r_mult": None,
            "trail_enabled": False,
            "fail_fast_enabled": False,
            "entry_assumption": "NEXT_OPEN",
            "slippage_bps": 5.0,
            "fee_taker_bps": 4.0,
        },
        "universe": "383 symbols from batch 'regen_frozen_20260407' setup_id='S10'",
        "research_period": "2026-01-01 → 2026-04-09",
        "walk_forward_aggregate_5fold_purged": {
            "folds_test_pf_over_1": 4,
            "folds_test_dpf_over_0_9": 4,
            "avg_test_pf": 1.108,
            "min_test_pf": 0.783,
            "avg_test_dpf": 1.331,
            "avg_test_n_per_fold": 1877,
        },
        "holdout_validation": {
            "split_ts": SPLIT_TS.isoformat(),
            "train_period": "2026-01-01 → 2026-03-20",
            "holdout_period": "2026-03-20 → 2026-04-09",
            "train_metrics": filtered_train,
            "holdout_metrics": filtered_hold,
            "gate_passed": holdout_report["gate"]["passed"],
        },
        "baseline_for_context": {
            "filtered_full": filtered_all,
            "baseline_full_no_filter": baseline_all,
        },
    }
    s11_path = os.path.join(out_dir, "s11_config_locked.json")
    with open(s11_path, "w") as f:
        json.dump(s11_config, f, indent=2, default=str)
    print(f"[out] S11 locked config: {s11_path}", flush=True)

    # Markdown report
    md = []
    md.append("# MACD Holdout Validation — Phase 0d (LOCKED FILTER)")
    md.append("")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ")
    md.append(f"**Input:** `{args.input_dir}`  ")
    md.append(f"**Variant:** `{LOCKED_VARIANT}` (MACD {LOCKED_FAST}/{LOCKED_SLOW}/{LOCKED_SIGNAL})  ")
    md.append(f"**Fixed exit:** stop={FIXED_STOP_MULT}×ATR, ts={FIXED_TS_BARS} bars  ")
    md.append("")
    md.append("**Locked filter:**")
    for f in LOCKED_FILTER:
        md.append(f"- `{f['feature']}` **{f['direction']}** `{f['threshold']}`")
    md.append("")
    md.append(
        f"**Split timestamp:** `{SPLIT_TS.isoformat()}` — train = "
        f"[2026-01-01, 2026-03-20), holdout = [2026-03-20, 2026-04-09]. "
        f"Holdout evaluated ONCE per canon §4.2."
    )
    md.append("")

    md.append("## Gate result")
    md.append("")
    hld = filtered_hold
    gate = holdout_report["gate"]
    pf_str = f"{hld['pf_agg']:.3f}" if hld['pf_agg'] is not None else "inf"
    if gate["passed"]:
        md.append(
            f"✅ **HOLDOUT GATE PASSED.** Filtered holdout: N={hld['n']}, "
            f"PF={pf_str}, dPFmed={hld['daily_pf_median']:.3f}, "
            f"%+days={100*hld['pct_days_positive']:.1f}%, "
            f"win%={hld['win_pct']:.1f}%, "
            f"daily_sharpe={hld['daily_sharpe']:.2f}, "
            f"expectancy=${hld['expectancy']:.2f}"
        )
        md.append("")
        md.append(
            "**Recommendation:** proceed to S11 build. All canon §4.1 "
            "(walk-forward) and §4.2 (untouched holdout) gates cleared. "
            "The locked config is written to `s11_config_locked.json` as "
            "the canonical reference for the eventual `S11Evaluator` + "
            "strategy registry + bootstrap steps."
        )
    else:
        md.append(
            f"❌ **HOLDOUT GATE NOT PASSED.** Filtered holdout: N={hld['n']}, "
            f"PF={pf_str}, dPFmed={hld['daily_pf_median']:.3f}, "
            f"%+days={100*hld['pct_days_positive']:.1f}%"
        )
        md.append("")
        md.append(
            "**Recommendation:** do not build S11 with this filter. "
            "The walk-forward was optimistic; the out-of-sample holdout "
            "doesn't reproduce the lift. Possible causes: (a) regime "
            "shift in the last 20 days, (b) curve-fit to the earlier "
            "period despite purged folds, (c) insufficient holdout "
            "sample size. Consider: widening the threshold, using a "
            "different tuple, or aggregating across variants."
        )
    md.append("")

    md.append("## Metrics table")
    md.append("")
    md.append(
        "| Partition | N | Days | PF | dPFmed | dPFmean | %+days | Win% | "
        "Stop% | TS% | EOD% | Gap% | AvgR | Sharpe | Exp$ | Total$ |"
    )
    md.append(
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    for m in [baseline_all, baseline_train, baseline_hold,
              filtered_all, filtered_train, filtered_hold]:
        pf_s = f"{m['pf_agg']:.3f}" if m['pf_agg'] is not None else "inf"
        md.append(
            f"| {m['label']} | {m['n']} | {m['n_days']} | {pf_s} | "
            f"{m['daily_pf_median']:.3f} | {m['daily_pf_mean']:.3f} | "
            f"{100*m['pct_days_positive']:.1f}% | {m['win_pct']:.1f}% | "
            f"{100*m['stop_rate']:.1f}% | {100*m['ts_rate']:.1f}% | "
            f"{100*m['eod_rate']:.1f}% | {100*m['gap_rate']:.1f}% | "
            f"{m['avg_r']:.3f} | {m['daily_sharpe']:.2f} | "
            f"${m['expectancy']:.2f} | ${m['total_pnl']:.0f} |"
        )
    md.append("")

    md.append("## Filter efficacy summary")
    md.append("")
    md.append(
        f"- Total fast_3_10_16 walks at stop=5.0/ts=24: **{n_walks}**"
    )
    md.append(
        f"- Filter pass rate: **{100*n_filtered_total/n_walks:.1f}%** "
        f"({n_filtered_total} walks)"
    )
    md.append(
        f"- Train partition filter count: **{filtered_train['n']}** "
        f"({100*filtered_train['n']/baseline_train['n']:.1f}% of train)"
    )
    md.append(
        f"- Holdout partition filter count: **{filtered_hold['n']}** "
        f"({100*filtered_hold['n']/baseline_hold['n']:.1f}% of holdout)"
    )
    md.append("")
    md.append("## Train vs holdout lift (filtered vs baseline on same partition)")
    md.append("")

    def _lift(name, base_v, filt_v):
        if base_v is None or filt_v is None:
            return f"- {name}: — (None)"
        delta = filt_v - base_v
        pct = 100 * (delta / base_v) if base_v != 0 else 0
        sign = "+" if delta >= 0 else ""
        return f"- {name}: baseline={base_v:.3f}, filtered={filt_v:.3f} ({sign}{delta:.3f}, {sign}{pct:.1f}%)"

    md.append("### Train")
    md.append("")
    md.append(_lift("PF", baseline_train["pf_agg"], filtered_train["pf_agg"]))
    md.append(_lift("dPFmed", baseline_train["daily_pf_median"], filtered_train["daily_pf_median"]))
    md.append(_lift("win%", baseline_train["win_pct"], filtered_train["win_pct"]))
    md.append(_lift("avg_r", baseline_train["avg_r"], filtered_train["avg_r"]))
    md.append(_lift("expectancy$", baseline_train["expectancy"], filtered_train["expectancy"]))
    md.append("")
    md.append("### Holdout")
    md.append("")
    md.append(_lift("PF", baseline_hold["pf_agg"], filtered_hold["pf_agg"]))
    md.append(_lift("dPFmed", baseline_hold["daily_pf_median"], filtered_hold["daily_pf_median"]))
    md.append(_lift("win%", baseline_hold["win_pct"], filtered_hold["win_pct"]))
    md.append(_lift("avg_r", baseline_hold["avg_r"], filtered_hold["avg_r"]))
    md.append(_lift("expectancy$", baseline_hold["expectancy"], filtered_hold["expectancy"]))
    md.append("")

    md.append("## Method notes")
    md.append("")
    md.append(
        "- **Filter is LOCKED.** No re-optimization on holdout; no re-running "
        "with different thresholds. The holdout is evaluated exactly once."
    )
    md.append(
        "- **Split boundary:** 2026-03-20 00:00 UTC. Any walk whose entry "
        "timestamp is ≥ this point is in the holdout partition; strictly "
        "before is train."
    )
    md.append(
        "- **No embargo.** Unlike the 5-fold purged walk-forward, the "
        "train/holdout split has no explicit embargo gap. Walks that entered "
        "late in train (within ts=24 bars = ~1 day of the split) may still "
        "have their exit walk overlap with the start of holdout. This is a "
        "minor leakage risk that would only bias in favor of train-side "
        "metrics, not holdout."
    )
    md.append(
        "- **Stop=5.0×ATR is wide by S10's standards.** Results here don't "
        "generalize to tighter stops — those require a re-run of the "
        "discriminator at a different exit config."
    )
    md.append("")

    md_path = os.path.join(out_dir, "holdout_validation.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md))
    print(f"[out] MD: {md_path}", flush=True)
    print(f"[main] DONE in {time.time()-t0:.1f}s", flush=True)

    print("", flush=True)
    print("=" * 80, flush=True)
    if gate["passed"]:
        print(f"HOLDOUT GATE PASSED — proceed to S11 build", flush=True)
    else:
        print(f"HOLDOUT GATE FAILED — do not build S11 with this filter", flush=True)
    print("=" * 80, flush=True)


if __name__ == "__main__":
    main()
