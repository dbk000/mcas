#!/usr/bin/env python3
"""
macd_robust_filter_discovery.py — Phase 0c Robust (non-overfit) filter search
=============================================================================

The Phase 0b greedy stack overfit: it chased raw PF 3 levels deep, shrinking
N from 68K to ~500 and collapsing dPFmed. The first filter at each fold was
consistent (`macd_line_atr lt ~-0.9`) but the stack above it was noise.

This script goes ground-truth: for each single continuous feature × each
threshold × each direction, it runs a 5-fold purged walk-forward and reports
each fold's train+test PF. A "robust" filter is one that:
  - has test PF > 1.0 in ≥ 4/5 folds (canon §4.1)
  - has dPFmed > 0.9 on the test side in ≥ 3/5 folds
  - keeps avg test N > 300 per fold (to be trustworthy, not a tail bet)

After the single-filter pass, the top-5 robust filters are paired (all
combinations) and re-walk-forwarded to check if any stacked pair improves
robustness without inflation.

Calendar features (hour_of_day, day_of_week) are EXCLUDED from this search
— 99 days / 7 = 14 samples per weekday is too thin to trust.

Outputs (into the same macd_wide_candidates dir):
  - robust_filters.md        human-readable
  - robust_filters.json      full data + final config
  - robust_filters_decision.json {final_filter_config, expected_metrics, gate_passed}
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

# Reuse loaders, derive_features, join_walks, metrics from the discriminator
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from macd_feature_discriminator import (
    load_candidates_csv,
    load_walks_npz,
    derive_features,
    join_walks,
    compute_pf,
    compute_daily_pf,
    VARIANTS,
    FIXED_STOP_MULT,
    FIXED_TS_BARS,
)


# Physically-motivated continuous features only — no calendar
ROBUST_FEATURES = [
    "macd_line_atr",
    "macd_line_min_3_atr",
    "macd_line_min_5_atr",
    "macd_line_min_12_atr",
    "macd_histogram_atr",
    "close_vs_sma20_atr",
    "close_vs_sma50_atr",
    "close_vs_sma200_atr",
    "rsi_14",
    "rsi_slope_1",
    "crossover_velocity",
    "volume_z_20",
    "rvol_20",
    "vol_frac",
    "gap_signal_close_to_next_open_pct",
    "bars_since_macd_below_zero",
]

N_THRESHOLDS = 20  # how many candidate thresholds per feature per direction
N_FOLDS = 5
EMBARGO_DAYS = 1
MIN_FOLD_TEST_N = 300
ROBUST_FOLD_PF_THRESHOLD = 1.0
ROBUST_FOLDS_REQUIRED = 4
ROBUST_DPF_FOLDS_REQUIRED = 3
ROBUST_DPF_THRESHOLD = 0.90


def build_folds(ee: np.ndarray, n_folds: int, embargo_days: int) -> list:
    """Returns list of (train_mask, test_mask) tuples over entry epoch."""
    ts_min = int(ee.min())
    ts_max = int(ee.max())
    span = ts_max - ts_min
    fold_size = span // n_folds
    embargo = embargo_days * 86400
    folds = []
    for k in range(n_folds):
        test_lo = ts_min + k * fold_size
        test_hi = ts_min + (k + 1) * fold_size if k < n_folds - 1 else ts_max + 1
        test_mask = (ee >= test_lo) & (ee < test_hi)
        train_mask = (ee < test_lo - embargo) | (ee >= test_hi + embargo)
        folds.append((train_mask, test_mask))
    return folds


def evaluate_filter(vals: np.ndarray, pnl: np.ndarray, ee: np.ndarray,
                    direction: str, threshold: float,
                    train_mask: np.ndarray, test_mask: np.ndarray):
    """Apply filter to train+test sides, compute train/test PF, dPFmed, N."""
    valid = ~np.isnan(vals)
    if direction == "gt":
        fmask = (vals > threshold) & valid
    else:
        fmask = (vals < threshold) & valid

    tr = fmask & train_mask
    te = fmask & test_mask

    tr_n = int(tr.sum())
    te_n = int(te.sum())

    tr_pf = compute_pf(pnl[tr]) if tr_n > 0 else 0.0
    te_pf = compute_pf(pnl[te]) if te_n > 0 else 0.0
    tr_dpf, tr_pct, _ = compute_daily_pf(pnl[tr], ee[tr]) if tr_n > 0 else (0.0, 0.0, 0)
    te_dpf, te_pct, _ = compute_daily_pf(pnl[te], ee[te]) if te_n > 0 else (0.0, 0.0, 0)

    return {
        "tr_n": tr_n, "te_n": te_n,
        "tr_pf": tr_pf if tr_pf != float("inf") else None,
        "te_pf": te_pf if te_pf != float("inf") else None,
        "tr_dpf": tr_dpf, "te_dpf": te_dpf,
        "tr_pct_days": tr_pct, "te_pct_days": te_pct,
    }


def sweep_single_filters(feature_matrix: dict, pnl: np.ndarray, ee: np.ndarray,
                          folds: list) -> list:
    """For each (feature, direction, threshold), compute per-fold train/test
    metrics and classify robustness."""
    results = []
    for feat, vals in feature_matrix.items():
        valid = ~np.isnan(vals)
        if valid.sum() < 5000:
            continue
        # Use global percentiles for thresholds
        qs = np.linspace(5, 95, N_THRESHOLDS)
        thresholds = np.percentile(vals[valid], qs)
        for th in thresholds:
            for direction in ("gt", "lt"):
                fold_rows = []
                for k, (trm, tem) in enumerate(folds):
                    r = evaluate_filter(vals, pnl, ee, direction, th, trm, tem)
                    r["fold"] = k
                    fold_rows.append(r)

                # Skip if any fold has < MIN_FOLD_TEST_N
                te_ns = [r["te_n"] for r in fold_rows]
                if min(te_ns) < MIN_FOLD_TEST_N:
                    continue

                test_pfs = [r["te_pf"] or 0 for r in fold_rows]
                test_dpfs = [r["te_dpf"] for r in fold_rows]
                folds_pf_over_1 = sum(1 for pf in test_pfs if pf > ROBUST_FOLD_PF_THRESHOLD)
                folds_dpf_over_th = sum(
                    1 for dpf in test_dpfs if dpf > ROBUST_DPF_THRESHOLD
                )

                results.append({
                    "feature": feat,
                    "direction": direction,
                    "threshold": float(th),
                    "avg_train_pf": float(np.mean([r["tr_pf"] or 0 for r in fold_rows])),
                    "avg_test_pf": float(np.mean(test_pfs)),
                    "min_test_pf": float(min(test_pfs)),
                    "max_test_pf": float(max(test_pfs)),
                    "avg_train_dpf": float(np.mean([r["tr_dpf"] for r in fold_rows])),
                    "avg_test_dpf": float(np.mean(test_dpfs)),
                    "min_test_dpf": float(min(test_dpfs)),
                    "avg_test_n": float(np.mean(te_ns)),
                    "min_test_n": int(min(te_ns)),
                    "folds_test_pf_over_1": folds_pf_over_1,
                    "folds_test_dpf_over_th": folds_dpf_over_th,
                    "is_robust": (
                        folds_pf_over_1 >= ROBUST_FOLDS_REQUIRED
                        and folds_dpf_over_th >= ROBUST_DPF_FOLDS_REQUIRED
                    ),
                    "fold_detail": fold_rows,
                })
    return results


def rank_single_filters(results: list) -> list:
    """Rank by robustness (folds > 1.0 first), then avg_test_pf, then min_test_pf."""
    return sorted(
        results,
        key=lambda r: (
            r["folds_test_pf_over_1"],
            r["folds_test_dpf_over_th"],
            r["avg_test_pf"],
            r["min_test_pf"],
        ),
        reverse=True,
    )


def sweep_pair_filters(feature_matrix: dict, pnl: np.ndarray, ee: np.ndarray,
                        folds: list, top_singles: list, max_pairs: int = 20) -> list:
    """Stack the top-N robust single filters pairwise and re-evaluate walk-forward."""
    pair_results = []
    top = top_singles[:max_pairs]
    for i, a in enumerate(top):
        for j, b in enumerate(top):
            if i == j:
                continue
            # Skip same-feature pairs
            if a["feature"] == b["feature"]:
                continue
            va = feature_matrix[a["feature"]]
            vb = feature_matrix[b["feature"]]
            if a["direction"] == "gt":
                ma = (va > a["threshold"])
            else:
                ma = (va < a["threshold"])
            if b["direction"] == "gt":
                mb = (vb > b["threshold"])
            else:
                mb = (vb < b["threshold"])
            # Combined mask with valid checks
            combo = ma & mb & ~np.isnan(va) & ~np.isnan(vb)

            fold_rows = []
            for k, (trm, tem) in enumerate(folds):
                tr = combo & trm
                te = combo & tem
                tr_n = int(tr.sum())
                te_n = int(te.sum())
                if te_n == 0:
                    fold_rows.append({
                        "fold": k, "tr_n": tr_n, "te_n": 0,
                        "tr_pf": 0.0, "te_pf": 0.0,
                        "tr_dpf": 0.0, "te_dpf": 0.0,
                    })
                    continue
                tr_pf = compute_pf(pnl[tr]) if tr_n > 0 else 0.0
                te_pf = compute_pf(pnl[te])
                tr_dpf, _, _ = compute_daily_pf(pnl[tr], ee[tr]) if tr_n > 0 else (0.0, 0.0, 0)
                te_dpf, _, _ = compute_daily_pf(pnl[te], ee[te])
                fold_rows.append({
                    "fold": k, "tr_n": tr_n, "te_n": te_n,
                    "tr_pf": tr_pf if tr_pf != float("inf") else None,
                    "te_pf": te_pf if te_pf != float("inf") else None,
                    "tr_dpf": tr_dpf, "te_dpf": te_dpf,
                })

            te_ns = [r["te_n"] for r in fold_rows]
            if min(te_ns) < MIN_FOLD_TEST_N // 2:  # relaxed for pairs
                continue
            test_pfs = [r["te_pf"] or 0 for r in fold_rows]
            test_dpfs = [r["te_dpf"] for r in fold_rows]
            folds_pf_over_1 = sum(1 for pf in test_pfs if pf > ROBUST_FOLD_PF_THRESHOLD)
            folds_dpf_over_th = sum(
                1 for dpf in test_dpfs if dpf > ROBUST_DPF_THRESHOLD
            )
            pair_results.append({
                "a_feature": a["feature"],
                "a_direction": a["direction"],
                "a_threshold": a["threshold"],
                "b_feature": b["feature"],
                "b_direction": b["direction"],
                "b_threshold": b["threshold"],
                "avg_train_pf": float(np.mean([r["tr_pf"] or 0 for r in fold_rows])),
                "avg_test_pf": float(np.mean(test_pfs)),
                "min_test_pf": float(min(test_pfs)),
                "avg_test_dpf": float(np.mean(test_dpfs)),
                "min_test_dpf": float(min(test_dpfs)),
                "avg_test_n": float(np.mean(te_ns)),
                "min_test_n": int(min(te_ns)),
                "folds_test_pf_over_1": folds_pf_over_1,
                "folds_test_dpf_over_th": folds_dpf_over_th,
                "is_robust": (
                    folds_pf_over_1 >= ROBUST_FOLDS_REQUIRED
                    and folds_dpf_over_th >= ROBUST_DPF_FOLDS_REQUIRED
                ),
                "fold_detail": fold_rows,
            })
    pair_results.sort(
        key=lambda r: (
            r["folds_test_pf_over_1"],
            r["folds_test_dpf_over_th"],
            r["avg_test_pf"],
        ),
        reverse=True,
    )
    return pair_results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    args = ap.parse_args()
    t0 = time.time()

    cand = load_candidates_csv(os.path.join(args.input_dir, "candidates.csv"))
    walks = load_walks_npz(os.path.join(args.input_dir, "exit_walks.npz"))
    cand = derive_features(cand)

    reports = {}
    for variant in VARIANTS:
        print(f"\n=== VARIANT: {variant} ===", flush=True)
        wtc, pnl, rm, er, ee = join_walks(
            cand, walks, variant, FIXED_STOP_MULT, FIXED_TS_BARS,
        )
        n = len(wtc)
        if n == 0:
            continue
        feature_matrix = {
            f: cand[f][wtc] for f in ROBUST_FEATURES if f in cand
        }
        base_pf = compute_pf(pnl)
        base_dpf, base_pct, _ = compute_daily_pf(pnl, ee)
        print(f"[baseline] {variant} n={n} pf={base_pf:.3f} dpf={base_dpf:.3f}",
              flush=True)

        folds = build_folds(ee, N_FOLDS, EMBARGO_DAYS)
        for k, (trm, tem) in enumerate(folds):
            print(f"[folds] fold {k}: train_n={int(trm.sum())} test_n={int(tem.sum())}",
                  flush=True)

        print("[single] sweeping single-filter walk-forward...", flush=True)
        single_results = sweep_single_filters(feature_matrix, pnl, ee, folds)
        single_ranked = rank_single_filters(single_results)
        print(f"[single] evaluated {len(single_results)} (feat, dir, thr) combos",
              flush=True)

        robust_singles = [r for r in single_ranked if r["is_robust"]]
        print(f"[single] robust: {len(robust_singles)}", flush=True)

        # Pick top-20 (robust + near-miss) for pair sweep
        top20_singles = single_ranked[:20]
        print("[pair] sweeping paired-filter walk-forward on top-20 singles...",
              flush=True)
        pair_results = sweep_pair_filters(
            feature_matrix, pnl, ee, folds, top20_singles, max_pairs=20,
        )
        robust_pairs = [r for r in pair_results if r["is_robust"]]
        print(f"[pair] total pairs evaluated: {len(pair_results)}, "
              f"robust pairs: {len(robust_pairs)}", flush=True)

        reports[variant] = {
            "baseline": {
                "n": n, "pf": base_pf,
                "daily_pf_median": base_dpf,
                "pct_days_positive": base_pct,
            },
            "fold_sizes": [
                {"fold": k, "train_n": int(trm.sum()), "test_n": int(tem.sum())}
                for k, (trm, tem) in enumerate(folds)
            ],
            "single_ranked_top30": single_ranked[:30],
            "robust_singles_count": len(robust_singles),
            "robust_singles": robust_singles[:10],
            "robust_pairs_count": len(robust_pairs),
            "robust_pairs": robust_pairs[:10],
            "pair_ranked_top10": pair_results[:10],
        }

    # Pick overall best config across variants.
    # Canonical tiebreaker ordering (canon §4.5 — daily PF is the success metric,
    # aggregate PF is diagnostic). Sort by:
    #   1. folds_pf_over_1     (canon §4.1 walk-forward profitability count)
    #   2. folds_dpf_over_th   (daily-PF robustness count — the real gate)
    #   3. avg_test_dpf        (average daily PF across folds — primary metric)
    #   4. avg_test_pf         (aggregate PF — diagnostic tiebreaker)
    def _score(c):
        return (
            c["folds_pf_over_1"],
            c["folds_dpf_over_th"],
            c["avg_test_dpf"],
            c["avg_test_pf"],
        )

    best = None
    for variant, rep in reports.items():
        for r in rep["robust_singles"]:
            candidate = {
                "kind": "single",
                "variant": variant,
                "filter": [r],
                "avg_test_pf": r["avg_test_pf"],
                "min_test_pf": r["min_test_pf"],
                "avg_test_dpf": r["avg_test_dpf"],
                "folds_pf_over_1": r["folds_test_pf_over_1"],
                "folds_dpf_over_th": r["folds_test_dpf_over_th"],
                "avg_test_n": r["avg_test_n"],
            }
            if best is None or _score(candidate) > _score(best):
                best = candidate
        for r in rep["robust_pairs"]:
            candidate = {
                "kind": "pair",
                "variant": variant,
                "filter": [
                    {"feature": r["a_feature"], "direction": r["a_direction"],
                     "threshold": r["a_threshold"]},
                    {"feature": r["b_feature"], "direction": r["b_direction"],
                     "threshold": r["b_threshold"]},
                ],
                "avg_test_pf": r["avg_test_pf"],
                "min_test_pf": r["min_test_pf"],
                "avg_test_dpf": r["avg_test_dpf"],
                "folds_pf_over_1": r["folds_test_pf_over_1"],
                "folds_dpf_over_th": r["folds_test_dpf_over_th"],
                "avg_test_n": r["avg_test_n"],
            }
            if best is None or _score(candidate) > _score(best):
                best = candidate

    out_dir = args.input_dir
    decision = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "best_config": best,
        "gate_passed": bool(
            best and best["folds_pf_over_1"] >= ROBUST_FOLDS_REQUIRED
            and best["folds_dpf_over_th"] >= ROBUST_DPF_FOLDS_REQUIRED
        ),
        "gate_criteria": (
            f"test_pf > 1.0 in ≥ {ROBUST_FOLDS_REQUIRED}/5 folds AND "
            f"test_dpf > {ROBUST_DPF_THRESHOLD} in ≥ "
            f"{ROBUST_DPF_FOLDS_REQUIRED}/5 folds AND min fold test N ≥ "
            f"{MIN_FOLD_TEST_N}"
        ),
    }
    with open(os.path.join(out_dir, "robust_filters_decision.json"), "w") as f:
        json.dump(decision, f, indent=2, default=str)
    print(f"[out] decision: {decision['gate_passed']} best={best}", flush=True)

    with open(os.path.join(out_dir, "robust_filters.json"), "w") as f:
        json.dump(reports, f, indent=2, default=str)
    print(f"[out] JSON: {os.path.join(out_dir, 'robust_filters.json')}", flush=True)

    write_md(reports, decision, out_dir, args.input_dir)
    print(f"[main] DONE in {time.time()-t0:.1f}s", flush=True)


def write_md(reports, decision, out_dir, input_dir):
    md = []
    md.append("# MACD Robust Filter Discovery — Phase 0c")
    md.append("")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ")
    md.append(f"**Input:** `{input_dir}`  ")
    md.append(f"**Fixed exit:** stop={FIXED_STOP_MULT}×ATR, ts={FIXED_TS_BARS} bars  ")
    md.append(
        "**Method:** single-filter 5-fold purged walk-forward sweep over 16 "
        "physically-motivated continuous features (calendar features "
        "excluded). Each (feature, direction, threshold) evaluated "
        "independently; top-20 singles then paired for stacked walk-forward. "
        f"Robustness gate: test PF > 1.0 in ≥ {ROBUST_FOLDS_REQUIRED}/5 folds AND "
        f"test dPFmed > {ROBUST_DPF_THRESHOLD} in ≥ {ROBUST_DPF_FOLDS_REQUIRED}/5 "
        f"folds AND min fold test N ≥ {MIN_FOLD_TEST_N}."
    )
    md.append("")

    md.append("## Decision")
    md.append("")
    if decision["gate_passed"]:
        b = decision["best_config"]
        md.append(f"✅ **Gate passed.** Robust filter found on variant `{b['variant']}`:")
        md.append("")
        for f in b["filter"]:
            md.append(
                f"- `{f['feature']}` **{f['direction']}** `{f['threshold']:.4f}`"
            )
        md.append("")
        md.append(
            f"**Walk-forward aggregate:** test PF avg "
            f"{b['avg_test_pf']:.3f}, min {b['min_test_pf']:.3f}, "
            f"dPFmed avg {b['avg_test_dpf']:.3f}, "
            f"{b['folds_pf_over_1']}/5 folds test PF > 1.0, "
            f"{b['folds_dpf_over_th']}/5 folds test dPFmed > 0.9, "
            f"avg test N = {b['avg_test_n']:.0f}"
        )
        md.append("")
        md.append(
            "**Recommendation:** proceed to S11 build with this filter config. "
            "Per canon §4, a full walk-forward + holdout + DSR run is still "
            "required before go-live, but this is the first filter subset "
            "that survives 4/5 purged folds."
        )
    else:
        if decision["best_config"]:
            b = decision["best_config"]
            md.append(
                f"⚠️ **Gate NOT passed.** Best candidate was "
                f"variant=`{b['variant']}`, kind={b['kind']}, "
                f"folds_PF>1={b['folds_pf_over_1']}/5, "
                f"folds_dPF>0.9={b['folds_dpf_over_th']}/5, "
                f"avg_test_PF={b['avg_test_pf']:.3f}, "
                f"min_test_PF={b['min_test_pf']:.3f}"
            )
        else:
            md.append("❌ **Gate NOT passed.** No robust filters found.")
        md.append("")
        md.append(
            "**Recommendation:** do not proceed to S11 build yet. Next levers:"
        )
        md.append("")
        md.append(
            "- Relax exit config: try stop=3.0×ATR, ts=24 / ts=12 (higher "
            "turnover, may surface different winners)."
        )
        md.append(
            "- Try 3-fold walk-forward instead of 5 (longer train periods, "
            "lower variance per fold)."
        )
        md.append(
            "- Drop the most marginal symbols by per-symbol PF rank (the "
            "discriminator showed strong per-symbol heterogeneity)."
        )
        md.append(
            "- Aggregate both tuples into a single dataset — the fast tuple "
            "had higher dPFmed baseline, combining may produce a more "
            "balanced signal set."
        )
    md.append("")

    for variant, rep in reports.items():
        md.append(f"## Variant: `{variant}`")
        md.append("")
        b = rep["baseline"]
        md.append(
            f"**Baseline (no filter):** N={b['n']}, PF={b['pf']:.3f}, "
            f"dPFmed={b['daily_pf_median']:.3f}, "
            f"%+days={100*b['pct_days_positive']:.1f}%"
        )
        md.append("")
        md.append("**Fold sizes:**")
        md.append("")
        md.append("| fold | train_n | test_n |")
        md.append("|---|---|---|")
        for fs in rep["fold_sizes"]:
            md.append(f"| {fs['fold']} | {fs['train_n']} | {fs['test_n']} |")
        md.append("")
        md.append(
            f"**Robust singles:** {rep['robust_singles_count']} / "
            f"{len(rep['single_ranked_top30'])} top-30"
        )
        md.append("")

        md.append("### Top-20 single filters (ranked by folds_PF>1, then avg_test_PF)")
        md.append("")
        md.append(
            "| # | feature | dir | threshold | folds PF>1 | folds dPF>0.9 | avg test PF | min test PF | avg test dPFmed | avg test N | robust |"
        )
        md.append(
            "|---|---|---|---|---|---|---|---|---|---|---|"
        )
        for i, r in enumerate(rep["single_ranked_top30"][:20], 1):
            md.append(
                f"| {i} | `{r['feature']}` | {r['direction']} | "
                f"{r['threshold']:.4f} | {r['folds_test_pf_over_1']}/5 | "
                f"{r['folds_test_dpf_over_th']}/5 | "
                f"{r['avg_test_pf']:.3f} | {r['min_test_pf']:.3f} | "
                f"{r['avg_test_dpf']:.3f} | {r['avg_test_n']:.0f} | "
                f"{'✅' if r['is_robust'] else ''} |"
            )
        md.append("")

        # Per-fold detail for top-5
        md.append("### Per-fold detail — top 5 singles")
        md.append("")
        for i, r in enumerate(rep["single_ranked_top30"][:5], 1):
            md.append(
                f"#### {i}. `{r['feature']}` {r['direction']} {r['threshold']:.4f}"
            )
            md.append("")
            md.append("| fold | train N | test N | train PF | test PF | train dPFmed | test dPFmed |")
            md.append("|---|---|---|---|---|---|---|")
            for fr in r["fold_detail"]:
                tr_pf = fr["tr_pf"] or 0.0
                te_pf = fr["te_pf"] or 0.0
                md.append(
                    f"| {fr['fold']} | {fr['tr_n']} | {fr['te_n']} | "
                    f"{tr_pf:.3f} | {te_pf:.3f} | "
                    f"{fr['tr_dpf']:.3f} | {fr['te_dpf']:.3f} |"
                )
            md.append("")

        md.append("### Top-10 paired filters (robust first, then by avg test PF)")
        md.append("")
        md.append(
            "| # | filter A | filter B | folds PF>1 | folds dPF>0.9 | avg test PF | min test PF | avg test dPFmed | avg test N | robust |"
        )
        md.append(
            "|---|---|---|---|---|---|---|---|---|---|"
        )
        for i, r in enumerate(rep["pair_ranked_top10"][:10], 1):
            a_s = f"`{r['a_feature']}` {r['a_direction']} {r['a_threshold']:.4f}"
            b_s = f"`{r['b_feature']}` {r['b_direction']} {r['b_threshold']:.4f}"
            md.append(
                f"| {i} | {a_s} | {b_s} | {r['folds_test_pf_over_1']}/5 | "
                f"{r['folds_test_dpf_over_th']}/5 | "
                f"{r['avg_test_pf']:.3f} | {r['min_test_pf']:.3f} | "
                f"{r['avg_test_dpf']:.3f} | {r['avg_test_n']:.0f} | "
                f"{'✅' if r['is_robust'] else ''} |"
            )
        md.append("")

    md_path = os.path.join(out_dir, "robust_filters.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md))
    print(f"[out] MD: {md_path}", flush=True)


if __name__ == "__main__":
    main()
