#!/usr/bin/env python3
"""
sweep_macd_negative_regime.py — Phase 0 Sweep: negative_regime × exit_matrix
=============================================================================

Loads candidates.npz + exit_walks.npz from a macd_wide_candidates run and
applies the negative_regime filter at each lookback window in {3, 5, 8, 12}
(plus a baseline_no_filter row) against the exit matrix
(stop_atr_mult × time_stop_bars), per MACD variant.

Because each candidate was walked ONCE for each of 9 exit profiles in the
generator, the sweep is a pure post-hoc mask — no re-walking. Guarantees
bit-identical exit behavior across lookbacks.

Decision gate (canon §4.5 aligned):
    winner := pf_agg > 1.0 AND daily_pf_median > 1.0

Usage:
    python3 sweep_macd_negative_regime.py --input-dir <macd_wide_candidates_*>
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np


STOP_ATR_MULT_VALUES = [2.0, 3.0, 5.0]
TIME_STOP_BARS_VALUES = [24, 48, 96]
LOOKBACK_WINDOWS = [3, 5, 8, 12]
MACD_VARIANTS = ["std_12_26_9", "fast_3_10_16"]


def compute_metrics(pnl: np.ndarray, r_mult: np.ndarray,
                    exit_reason: np.ndarray, entry_epoch: np.ndarray) -> dict:
    n = int(len(pnl))
    base = {
        "n": n, "pf_agg": 0.0, "daily_pf_mean": 0.0, "daily_pf_median": 0.0,
        "n_days": 0, "pct_days_positive": 0.0, "win_pct": 0.0,
        "stop_rate": 0.0, "ts_rate": 0.0, "eod_rate": 0.0, "gap_rate": 0.0,
        "avg_r": 0.0, "expectancy": 0.0, "total_pnl": 0.0, "daily_sharpe": 0.0,
    }
    if n == 0:
        return base

    wins = pnl > 0
    losses = pnl <= 0
    gw = float(pnl[wins].sum())
    gl = float(-pnl[losses].sum())
    pf_agg = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    total = float(pnl.sum())
    avg_r = float(r_mult.mean())
    expectancy = total / n
    win_pct = 100.0 * float(wins.sum()) / n

    stops = (exit_reason == "STOP_LOSS")
    ts_ = (exit_reason == "TIME_STOP")
    eods = (exit_reason == "END_OF_DATA")
    stop_rate = float(stops.sum()) / n
    ts_rate = float(ts_.sum()) / n
    eod_rate = float(eods.sum()) / n
    n_stops = int(stops.sum())
    gap_rate = float(((r_mult < -1.05) & stops).sum()) / n_stops if n_stops > 0 else 0.0

    # Group by day (vectorized: divide epoch seconds by 86400 then int cast)
    day_keys = (entry_epoch // 86400).astype(np.int64)
    # Per-day gw / gl
    uniq_days, inv = np.unique(day_keys, return_inverse=True)
    n_days = len(uniq_days)
    day_gw = np.zeros(n_days, dtype=np.float64)
    day_gl = np.zeros(n_days, dtype=np.float64)
    pos_pnl = np.where(pnl > 0, pnl, 0.0)
    neg_pnl = np.where(pnl < 0, -pnl, 0.0)
    np.add.at(day_gw, inv, pos_pnl)
    np.add.at(day_gl, inv, neg_pnl)
    with np.errstate(divide="ignore", invalid="ignore"):
        day_pfs = np.where(day_gl > 0, day_gw / day_gl, np.nan)
    zero_loss_days = int(((day_gl == 0) & (day_gw > 0)).sum())
    valid_pfs = day_pfs[~np.isnan(day_pfs)]
    daily_pf_mean = float(valid_pfs.mean()) if len(valid_pfs) > 0 else 0.0
    daily_pf_median = float(np.median(valid_pfs)) if len(valid_pfs) > 0 else 0.0
    positive_days = int((valid_pfs > 1.0).sum()) + zero_loss_days
    pct_days_positive = positive_days / n_days if n_days > 0 else 0.0

    # Daily sharpe: sum of pnl per day
    day_sum = np.zeros(n_days, dtype=np.float64)
    np.add.at(day_sum, inv, pnl)
    daily_sharpe = 0.0
    if n_days >= 10:
        m = float(day_sum.mean())
        s = float(day_sum.std(ddof=1)) if n_days > 1 else 0.0
        if s > 0:
            daily_sharpe = (m / s) * math.sqrt(365)

    base.update({
        "n": n,
        "pf_agg": pf_agg if pf_agg != float("inf") else None,
        "daily_pf_mean": daily_pf_mean,
        "daily_pf_median": daily_pf_median,
        "n_days": n_days,
        "pct_days_positive": pct_days_positive,
        "win_pct": win_pct,
        "stop_rate": stop_rate,
        "ts_rate": ts_rate,
        "eod_rate": eod_rate,
        "gap_rate": gap_rate,
        "avg_r": avg_r,
        "expectancy": expectancy,
        "total_pnl": total,
        "daily_sharpe": daily_sharpe,
    })
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    args = ap.parse_args()

    print(f"[sweep] loading from {args.input_dir}", flush=True)
    cands = np.load(os.path.join(args.input_dir, "candidates.npz"), allow_pickle=False)
    walks = np.load(os.path.join(args.input_dir, "exit_walks.npz"), allow_pickle=False)

    cand_ids = cands["candidate_id"]
    cand_variants = cands["variant_id"]
    cand_line_mins = {w: cands[f"macd_line_min_{w}"] for w in LOOKBACK_WINDOWS}
    print(f"[sweep] candidates: {len(cand_ids)}", flush=True)

    walk_ids = walks["candidate_id"]
    walk_variant = walks["variant_id"]
    walk_stop = walks["stop_atr_mult"].astype(np.float64)
    walk_ts = walks["time_stop_bars"].astype(np.int64)
    walk_pnl = walks["pnl_net"]
    walk_r = walks["r_multiple"]
    walk_exit = walks["exit_reason"]
    walk_entry = walks["entry_ts_epoch"]
    print(f"[sweep] walks: {len(walk_ids)}", flush=True)

    # Map walk_id → cand_idx for vectorized regime lookup
    cand_id_to_idx = {cid: i for i, cid in enumerate(cand_ids)}
    walk_to_cand_idx = np.empty(len(walk_ids), dtype=np.int64)
    miss = 0
    for i, wid in enumerate(walk_ids):
        idx = cand_id_to_idx.get(wid, -1)
        walk_to_cand_idx[i] = idx
        if idx < 0:
            miss += 1
    if miss > 0:
        print(f"[sweep] WARNING: {miss}/{len(walk_ids)} walks missed candidate map", flush=True)
    valid_walk_mask = walk_to_cand_idx >= 0

    sweep_rows: list = []

    def _row(variant, lookback_label, stop_mult, ts_bars, sel_mask):
        m = compute_metrics(
            walk_pnl[sel_mask], walk_r[sel_mask],
            walk_exit[sel_mask], walk_entry[sel_mask],
        )
        m.update({
            "variant_id": variant,
            "lookback": lookback_label,
            "stop_atr_mult": stop_mult,
            "time_stop_bars": ts_bars,
        })
        return m

    for variant in MACD_VARIANTS:
        variant_mask = (walk_variant == variant) & valid_walk_mask
        # Baseline: no regime filter
        for stop_mult in STOP_ATR_MULT_VALUES:
            stop_mask = (walk_stop == stop_mult)
            for ts_bars in TIME_STOP_BARS_VALUES:
                ts_mask = (walk_ts == ts_bars)
                sel = variant_mask & stop_mask & ts_mask
                sweep_rows.append(_row(variant, "baseline", stop_mult, ts_bars, sel))

        # Lookback filters
        for lookback in LOOKBACK_WINDOWS:
            # Candidate-level regime mask
            cand_pass = cand_line_mins[lookback] < 0
            # Gather into walk space (safe: -1 indices map to cand_pass[-1] which is True/False, but we mask via valid_walk_mask)
            # To be safe, clip walk_to_cand_idx to 0 for invalid and then AND with valid_walk_mask
            safe_idx = np.where(walk_to_cand_idx >= 0, walk_to_cand_idx, 0)
            walk_regime_pass = cand_pass[safe_idx] & valid_walk_mask
            base_mask = variant_mask & walk_regime_pass
            for stop_mult in STOP_ATR_MULT_VALUES:
                stop_mask = (walk_stop == stop_mult)
                for ts_bars in TIME_STOP_BARS_VALUES:
                    ts_mask = (walk_ts == ts_bars)
                    sel = base_mask & stop_mask & ts_mask
                    sweep_rows.append(_row(variant, f"L{lookback}", stop_mult, ts_bars, sel))

    # Write summary CSV
    csv_fields = [
        "variant_id", "lookback", "stop_atr_mult", "time_stop_bars",
        "n", "pf_agg", "daily_pf_mean", "daily_pf_median", "n_days",
        "pct_days_positive", "win_pct", "stop_rate", "ts_rate", "eod_rate",
        "gap_rate", "avg_r", "expectancy", "total_pnl", "daily_sharpe",
    ]
    out_dir = args.input_dir
    csv_path = os.path.join(out_dir, "sweep_summary.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        w.writeheader()
        for r in sweep_rows:
            row = dict(r)
            if row.get("pf_agg") is None:
                row["pf_agg"] = "inf"
            w.writerow(row)
    print(f"[sweep] CSV: {csv_path}", flush=True)

    # Winners
    winners = [
        r for r in sweep_rows
        if (r.get("pf_agg") is not None
            and r["pf_agg"] > 1.0
            and r["daily_pf_median"] > 1.0
            and r["n"] >= 50)
    ]
    any_pf_over_1 = any(
        (r.get("pf_agg") is not None and r["pf_agg"] > 1.0 and r["n"] >= 50)
        for r in sweep_rows
    )
    any_daily_over_1 = any(
        r["daily_pf_median"] > 1.0 and r["n"] >= 50
        for r in sweep_rows
    )
    best = None
    if winners:
        best = sorted(
            winners,
            key=lambda r: (r["daily_pf_median"], r["pf_agg"] or 0),
            reverse=True,
        )[0]

    decision = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate_criteria": "pf_agg > 1.0 AND daily_pf_median > 1.0 AND n >= 50",
        "any_winner": bool(winners),
        "winner_count": len(winners),
        "any_pf_over_1": any_pf_over_1,
        "any_daily_pf_over_1": any_daily_over_1,
        "best_config": best,
        "recommendation": (
            f"PROCEED to S11 build with variant={best['variant_id']}, "
            f"lookback={best['lookback']}, stop_atr_mult={best['stop_atr_mult']}, "
            f"time_stop_bars={best['time_stop_bars']} "
            f"(PF={best['pf_agg']:.3f}, dPFmed={best['daily_pf_median']:.3f}, "
            f"N={best['n']})"
            if best else
            "STOP. No filtered config rescues the naked MACD baseline on 1h. "
            "Do not proceed with S11 build as specified in "
            "macd_long_reversion_live_plan.md. See verdict section of "
            "sweep_summary.md for next-lever recommendations."
        ),
    }
    with open(os.path.join(out_dir, "sweep_decision.json"), "w") as f:
        json.dump(decision, f, indent=2)
    print(f"[sweep] decision: {decision['recommendation']}", flush=True)

    # Markdown
    md = []
    md.append("# MACD Negative-Regime Sweep — Phase 0")
    md.append("")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ")
    md.append(f"**Input:** `{args.input_dir}`  ")
    md.append(f"**Gate:** aggregate PF > 1.0 **AND** daily-PF median > 1.0 **AND** N ≥ 50  ")
    md.append("")
    md.append(
        "Each row is (variant, negative_regime lookback, stop×ATR, time_stop). "
        "`baseline` rows apply no regime filter and should match "
        "`research_macd_multi_tf_20260409_174344` for the std tuple at 1h "
        "(prior best was PF=0.786 at stop=5.0/ts=24)."
    )
    md.append("")

    def _pf(v):
        return (
            f"{v:.3f}"
            if (v is not None and v != float("inf"))
            else ("inf" if v == float("inf") else "—")
        )

    for variant in MACD_VARIANTS:
        md.append(f"## Variant: `{variant}`")
        md.append("")
        md.append(
            "| Lookback | stop× | ts | N | PF | dPFmed | dPFmean | %+days | "
            "Win% | Stop% | TS% | EOD% | Gap% | AvgR | Sharpe | Exp$ |"
        )
        md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        rows_v = [r for r in sweep_rows if r["variant_id"] == variant]
        lookback_order = ["baseline"] + [f"L{w}" for w in LOOKBACK_WINDOWS]
        rows_v.sort(
            key=lambda r: (
                lookback_order.index(r["lookback"]),
                r["stop_atr_mult"],
                r["time_stop_bars"],
            )
        )
        for r in rows_v:
            md.append(
                f"| {r['lookback']} | {r['stop_atr_mult']} | {r['time_stop_bars']} | "
                f"{r['n']} | {_pf(r['pf_agg'])} | {r['daily_pf_median']:.3f} | "
                f"{r['daily_pf_mean']:.3f} | {100*r['pct_days_positive']:.1f}% | "
                f"{r['win_pct']:.1f}% | {100*r['stop_rate']:.1f}% | "
                f"{100*r['ts_rate']:.1f}% | {100*r['eod_rate']:.1f}% | "
                f"{100*r['gap_rate']:.1f}% | {r['avg_r']:.3f} | "
                f"{r['daily_sharpe']:.2f} | ${r['expectancy']:.2f} |"
            )
        md.append("")

    # Top-10 by daily_pf_median (N ≥ 100)
    top = sorted(
        [r for r in sweep_rows if r["n"] >= 100],
        key=lambda r: (r["daily_pf_median"], r.get("pf_agg") or 0),
        reverse=True,
    )[:10]
    md.append("## Top 10 by daily-PF median (N ≥ 100)")
    md.append("")
    md.append("| # | variant | lookback | stop× | ts | N | PF | dPFmed | %+days | Win% | Sharpe |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(top, 1):
        md.append(
            f"| {i} | {r['variant_id']} | {r['lookback']} | {r['stop_atr_mult']} | "
            f"{r['time_stop_bars']} | {r['n']} | {_pf(r['pf_agg'])} | "
            f"{r['daily_pf_median']:.3f} | {100*r['pct_days_positive']:.1f}% | "
            f"{r['win_pct']:.1f}% | {r['daily_sharpe']:.2f} |"
        )
    md.append("")

    md.append("## Verdict")
    md.append("")
    if best:
        md.append(
            f"✅ **Winner found.** `{best['variant_id']}` with "
            f"lookback=`{best['lookback']}`, stop=`{best['stop_atr_mult']}×ATR`, "
            f"time_stop=`{best['time_stop_bars']} bars` — "
            f"PF={best['pf_agg']:.3f}, dPFmed={best['daily_pf_median']:.3f}, "
            f"N={best['n']}, %+days={100*best['pct_days_positive']:.1f}%."
        )
        md.append("")
        md.append(
            "**Recommendation:** proceed to S11 build with this config. "
            "Per canon §4, must still pass walk-forward + holdout + DSR "
            "before going live."
        )
    elif any_pf_over_1 or any_daily_over_1:
        md.append(
            "⚠️ Some cells cross ONE of the gates (pf_agg OR dPFmed > 1.0) "
            "but NO cell crosses both simultaneously. Marginal at best; "
            "not worth committing to live infrastructure."
        )
        md.append("")
        md.append(
            "**Recommendation:** STOP. Do not proceed to S11 build as "
            "currently specified."
        )
    else:
        md.append(
            "❌ **No config clears the gate.** Neither aggregate PF nor "
            "daily-PF median crosses 1.0 for any (variant, lookback, stop, "
            "ts) cell with N ≥ 50."
        )
        md.append("")
        md.append("**Recommendation:** STOP. Next levers to consider:")
        md.append("")
        md.append(
            "- Different regime semantics: require BOTH macd_line AND "
            "histogram below zero within the window, or require macd_line "
            "below some multiple of ATR."
        )
        md.append(
            "- Different trigger: zero-line reclaim (line crosses 0), "
            "divergence, or multi-TF confirmation (all deferred in plan §14)."
        )
        md.append(
            "- Cross-sectional: rank candidates by MACD depth and only "
            "trade the deepest-reversion subset per day."
        )
        md.append(
            "- Entry-lead research (microstructure detection BEFORE MACD "
            "crossover fires) — the next-lever recommended in the prior "
            "research_macd_multi_tf summary."
        )
    md.append("")
    md.append("## Method notes")
    md.append("")
    md.append(
        "- **Walks are stateless in lookback.** Candidates were walked once "
        "per exit profile in the generator; the sweep applies the regime "
        "mask post-hoc to the same walk set."
    )
    md.append(
        "- **Regime filter:** `macd_line_min_N < 0` where the window "
        "`[i−N+1 .. i]` includes the signal bar."
    )
    md.append(
        "- **Gate uses BOTH pf_agg and daily-PF median** to avoid "
        "outlier-day inflation (per canon §4.5)."
    )
    md.append(
        "- **Gap-through rate reported but not filtered** (per 2026-03-08 "
        "correction — excluding gaps models a broker that doesn't exist)."
    )
    md.append("")

    md_path = os.path.join(out_dir, "sweep_summary.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md))
    print(f"[sweep] MD: {md_path}", flush=True)

    print("\n" + "=" * 80, flush=True)
    print(f"DECISION: {decision['recommendation']}", flush=True)
    print("=" * 80, flush=True)


if __name__ == "__main__":
    main()
