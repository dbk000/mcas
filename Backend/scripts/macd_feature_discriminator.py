#!/usr/bin/env python3
"""
macd_feature_discriminator.py — Phase 0b Filter Discovery
=========================================================

Given the wide candidate dataset + exit walks from macd_wide_candidates.py,
find the feature subspace where MACD crossover signals have forward edge.
The baseline PF ≈ 0.78 on 68k trades is a structured drag, not noise — this
hunts for the axes along which it separates.

Methodology:
  1. Fix exit config: stop=5.0×ATR, time_stop=24 bars (best cell from sweep).
  2. Join candidates to their walks at that exit config per variant.
  3. Univariate sweep: 10-quantile bins per feature → PF/dPFmed/N/win% per bin.
  4. Rank features by max-bin PF and spread.
  5. Bivariate 5×5 heatmap on the top-5 features.
  6. Greedy filter stacking: at each step pick the (feature, quantile,
     direction) that maximizes residual PF subject to N ≥ min_n.
  7. 5-fold purged walk-forward: discover filter stack on train, evaluate on test.

Pure numpy. Reads candidates.csv + exit_walks.npz. Writes:
  - feature_discriminator.md    (human-readable report)
  - feature_discriminator.json  (everything, for programmatic consumption)
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


FIXED_STOP_MULT = 5.0
FIXED_TS_BARS = 24

N_BINS = 10
MIN_BIN_N = 500
MIN_FOLD_N = 300

VARIANTS = ["std_12_26_9", "fast_3_10_16"]

CONTINUOUS_FEATURES = [
    "rsi_14",
    "rsi_slope_1",
    "volume_z_20",
    "rvol_20",
    "close_vs_sma20_atr",
    "close_vs_sma50_atr",
    "close_vs_sma200_atr",
    "gap_signal_close_to_next_open_pct",
    "bars_since_macd_below_zero",
    "macd_line_atr",
    "macd_histogram_atr",
    "macd_line_min_3_atr",
    "macd_line_min_5_atr",
    "macd_line_min_12_atr",
    "crossover_velocity",
    "vol_frac",
    "hour_of_day",
    "day_of_week",
    "atr_14",
    "signal_close",
    "signal_volume",
]


# ─────────────────────────────── Loaders ────────────────────────────────
def load_candidates_csv(path: str) -> dict:
    print(f"[load] {path}", flush=True)
    t0 = time.time()
    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    n = len(rows)
    print(f"[load]   {n} rows in {time.time()-t0:.1f}s", flush=True)

    def _float(key):
        out = np.empty(n, dtype=np.float64)
        for i, r in enumerate(rows):
            v = r.get(key, "")
            try:
                out[i] = float(v)
            except (ValueError, TypeError):
                out[i] = np.nan
        return out

    def _int(key):
        out = np.empty(n, dtype=np.int64)
        for i, r in enumerate(rows):
            v = r.get(key, "")
            try:
                out[i] = int(float(v))
            except (ValueError, TypeError):
                out[i] = -1
        return out

    def _str(key):
        return np.array([r.get(key, "") for r in rows], dtype="U80")

    cols = {
        "candidate_id": _str("candidate_id"),
        "variant_id": _str("variant_id"),
        "symbol": _str("symbol"),
        "signal_bar_start_ts_epoch": _int("signal_bar_start_ts_epoch"),
        "entry_bar_start_ts_epoch": _int("entry_bar_start_ts_epoch"),
        "signal_close": _float("signal_close"),
        "signal_volume": _float("signal_volume"),
        "macd_line": _float("macd_line"),
        "macd_histogram": _float("macd_histogram"),
        "macd_histogram_prev_1": _float("macd_histogram_prev_1"),
        "macd_line_min_3": _float("macd_line_min_3"),
        "macd_line_min_5": _float("macd_line_min_5"),
        "macd_line_min_12": _float("macd_line_min_12"),
        "bars_since_macd_below_zero": _float("bars_since_macd_below_zero"),
        "atr_14": _float("atr_14"),
        "rsi_14": _float("rsi_14"),
        "rsi_slope_1": _float("rsi_slope_1"),
        "volume_z_20": _float("volume_z_20"),
        "rvol_20": _float("rvol_20"),
        "close_vs_sma20_atr": _float("close_vs_sma20_atr"),
        "close_vs_sma50_atr": _float("close_vs_sma50_atr"),
        "close_vs_sma200_atr": _float("close_vs_sma200_atr"),
        "gap_signal_close_to_next_open_pct": _float("gap_signal_close_to_next_open_pct"),
    }
    return cols


def load_walks_npz(path: str) -> dict:
    print(f"[load] {path}", flush=True)
    npz = np.load(path, allow_pickle=False)
    return {
        "candidate_id": npz["candidate_id"],
        "variant_id": npz["variant_id"],
        "stop_atr_mult": npz["stop_atr_mult"].astype(np.float64),
        "time_stop_bars": npz["time_stop_bars"].astype(np.int64),
        "entry_ts_epoch": npz["entry_ts_epoch"],
        "exit_reason": npz["exit_reason"],
        "pnl_net": npz["pnl_net"],
        "r_multiple": npz["r_multiple"],
    }


def derive_features(cand: dict) -> dict:
    atr = cand["atr_14"]
    safe_atr = np.where(atr > 0, atr, np.nan)
    cand["macd_line_atr"] = cand["macd_line"] / safe_atr
    cand["macd_histogram_atr"] = cand["macd_histogram"] / safe_atr
    cand["macd_line_min_3_atr"] = cand["macd_line_min_3"] / safe_atr
    cand["macd_line_min_5_atr"] = cand["macd_line_min_5"] / safe_atr
    cand["macd_line_min_12_atr"] = cand["macd_line_min_12"] / safe_atr
    cand["crossover_velocity"] = (cand["macd_histogram"] - cand["macd_histogram_prev_1"]) / safe_atr
    cand["vol_frac"] = safe_atr / cand["signal_close"]
    bs = cand["bars_since_macd_below_zero"].copy()
    bs = np.where(bs < 0, 999.0, bs)
    cand["bars_since_macd_below_zero"] = bs
    ts = cand["entry_bar_start_ts_epoch"]
    secs_of_day = ts % 86400
    days_since_epoch = ts // 86400
    cand["hour_of_day"] = (secs_of_day // 3600).astype(np.float64)
    cand["day_of_week"] = ((days_since_epoch + 4) % 7).astype(np.float64)
    return cand


def join_walks(cand: dict, walks: dict, variant: str, stop_mult: float, ts_bars: int):
    wmask = (
        (walks["variant_id"] == variant)
        & (walks["stop_atr_mult"] == stop_mult)
        & (walks["time_stop_bars"] == ts_bars)
    )
    wids = walks["candidate_id"][wmask]
    pnl = walks["pnl_net"][wmask]
    rm = walks["r_multiple"][wmask]
    er = walks["exit_reason"][wmask]
    ee = walks["entry_ts_epoch"][wmask]

    cvmask = (cand["variant_id"] == variant)
    cand_rows = np.where(cvmask)[0]
    cand_id_to_row = {cand["candidate_id"][i]: i for i in cand_rows}
    wtc = np.array([cand_id_to_row.get(cid, -1) for cid in wids], dtype=np.int64)
    valid = wtc >= 0
    if (~valid).any():
        print(f"[join] {variant}: {(~valid).sum()} walks missed", flush=True)
    return wtc[valid], pnl[valid], rm[valid], er[valid], ee[valid]


# ─────────────────────────────── Metrics ────────────────────────────────
def compute_pf(pnl: np.ndarray) -> float:
    if len(pnl) == 0:
        return 0.0
    gw = float(pnl[pnl > 0].sum())
    gl = float(-pnl[pnl <= 0].sum())
    if gl == 0:
        return float("inf") if gw > 0 else 0.0
    return gw / gl


def compute_daily_pf(pnl: np.ndarray, ee: np.ndarray):
    if len(pnl) == 0:
        return 0.0, 0.0, 0
    days = (ee // 86400).astype(np.int64)
    uniq, inv = np.unique(days, return_inverse=True)
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
    valid = dpf[~np.isnan(dpf)]
    if len(valid) == 0:
        return 0.0, (zl / n_days if n_days > 0 else 0.0), n_days
    med = float(np.median(valid))
    pct = (int((valid > 1.0).sum()) + zl) / n_days if n_days > 0 else 0.0
    return med, pct, n_days


# ─────────────────────────────── Univariate sweep ────────────────────────────────
def univariate_sweep(fv, pnl, rm, ee, fname, n_bins=N_BINS):
    valid = ~np.isnan(fv)
    if valid.sum() < MIN_BIN_N * 2:
        return []
    v = fv[valid]
    p = pnl[valid]
    r = rm[valid]
    e = ee[valid]
    edges = np.percentile(v, np.linspace(0, 100, n_bins + 1))
    # np.digitize edge handling: put values at n_bins into bin n_bins-1
    bins = np.clip(np.digitize(v, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        sel = bins == b
        n = int(sel.sum())
        if n < MIN_BIN_N:
            continue
        ps = p[sel]
        pf = compute_pf(ps)
        dpf, pct, _ = compute_daily_pf(ps, e[sel])
        wins = int((ps > 0).sum())
        rows.append({
            "feature": fname,
            "bin": b,
            "bin_lo": float(edges[b]),
            "bin_hi": float(edges[b + 1]),
            "n": n,
            "pf": pf if pf != float("inf") else None,
            "daily_pf_median": dpf,
            "pct_days_positive": pct,
            "win_pct": 100 * wins / n,
            "avg_r": float(r[sel].mean()),
            "expectancy": float(ps.mean()),
        })
    return rows


def symbol_sweep(sym, pnl, rm, ee):
    uniq = np.unique(sym)
    rows = []
    for s in uniq:
        sel = sym == s
        n = int(sel.sum())
        if n < 50:
            continue
        ps = pnl[sel]
        pf = compute_pf(ps)
        dpf, pct, _ = compute_daily_pf(ps, ee[sel])
        wins = int((ps > 0).sum())
        rows.append({
            "symbol": str(s),
            "n": n,
            "pf": pf if pf != float("inf") else None,
            "daily_pf_median": dpf,
            "pct_days_positive": pct,
            "win_pct": 100 * wins / n,
            "expectancy": float(ps.mean()),
        })
    return rows


def rank_features(univ_rows):
    by = defaultdict(list)
    for r in univ_rows:
        by[r["feature"]].append(r)
    summary = []
    for feat, rows in by.items():
        pfs = [r["pf"] for r in rows if r["pf"] is not None]
        dpfs = [r["daily_pf_median"] for r in rows if r["daily_pf_median"] is not None]
        if not pfs:
            continue
        max_pf = max(pfs)
        min_pf = min(pfs)
        max_dpf = max(dpfs) if dpfs else 0.0
        spread = max_pf - min_pf
        best = max(rows, key=lambda r: r["pf"] or 0)
        bin_idx = np.array([r["bin"] for r in rows])
        pf_arr = np.array([r["pf"] or 0 for r in rows])
        if len(bin_idx) >= 3:
            rb = np.argsort(np.argsort(bin_idx))
            rp = np.argsort(np.argsort(pf_arr))
            mono = float(np.corrcoef(rb, rp)[0, 1])
        else:
            mono = 0.0
        summary.append({
            "feature": feat,
            "n_bins_valid": len(rows),
            "max_pf": max_pf,
            "min_pf": min_pf,
            "pf_spread": spread,
            "max_daily_pf_median": max_dpf,
            "best_bin": best["bin"],
            "best_bin_lo": best["bin_lo"],
            "best_bin_hi": best["bin_hi"],
            "best_bin_n": best["n"],
            "best_bin_pf": best["pf"],
            "best_bin_dpf": best["daily_pf_median"],
            "monotonicity": mono,
        })
    summary.sort(key=lambda r: r["max_pf"], reverse=True)
    return summary


# ─────────────────────────────── Bivariate sweep ────────────────────────────────
def bivariate_sweep(feature_matrix, top_feats, pnl, ee, n_bins=5):
    cells = []
    for i in range(len(top_feats)):
        for j in range(i + 1, len(top_feats)):
            f1, f2 = top_feats[i], top_feats[j]
            v1 = feature_matrix[f1]
            v2 = feature_matrix[f2]
            valid = ~np.isnan(v1) & ~np.isnan(v2)
            if valid.sum() < MIN_BIN_N * 2:
                continue
            q1 = np.percentile(v1[valid], np.linspace(0, 100, n_bins + 1))
            q2 = np.percentile(v2[valid], np.linspace(0, 100, n_bins + 1))
            b1 = np.clip(np.digitize(v1, q1[1:-1]), 0, n_bins - 1)
            b2 = np.clip(np.digitize(v2, q2[1:-1]), 0, n_bins - 1)
            for a in range(n_bins):
                for b in range(n_bins):
                    sel = (b1 == a) & (b2 == b) & valid
                    n = int(sel.sum())
                    if n < MIN_BIN_N // 3:
                        continue
                    ps = pnl[sel]
                    pf = compute_pf(ps)
                    if pf is None or pf == float("inf"):
                        continue
                    dpf, _, _ = compute_daily_pf(ps, ee[sel])
                    cells.append({
                        "f1": f1, "f2": f2, "b1": a, "b2": b,
                        "f1_lo": float(q1[a]), "f1_hi": float(q1[a + 1]),
                        "f2_lo": float(q2[b]), "f2_hi": float(q2[b + 1]),
                        "n": n, "pf": pf, "daily_pf_median": dpf,
                    })
    cells.sort(key=lambda r: r["pf"], reverse=True)
    return cells


# ─────────────────────────────── Greedy stack ────────────────────────────────
def greedy_stack(feature_matrix, pnl, rm, ee, max_depth=5, target_pf=1.20,
                  min_n=500, n_thresh=10):
    mask = np.ones(len(pnl), dtype=bool)
    stack = []
    history = []
    for step in range(max_depth):
        cur_n = int(mask.sum())
        cur_pf = compute_pf(pnl[mask])
        cur_dpf, cur_pct, _ = compute_daily_pf(pnl[mask], ee[mask])
        history.append({
            "step": step, "n": cur_n, "pf": cur_pf,
            "daily_pf_median": cur_dpf, "pct_days_positive": cur_pct,
        })
        print(f"[stack] step={step} n={cur_n} pf={cur_pf:.3f} dpf={cur_dpf:.3f}",
              flush=True)
        if cur_pf >= target_pf and cur_dpf > 1.0:
            break
        best = None
        for feat, vals in feature_matrix.items():
            v_mask = vals[mask]
            valid = ~np.isnan(v_mask)
            if valid.sum() < min_n * 2:
                continue
            vv = v_mask[valid]
            pv = pnl[mask][valid]
            ev = ee[mask][valid]
            qs = np.linspace(10, 90, n_thresh)
            thresholds = np.percentile(vv, qs)
            for th in thresholds:
                for direction in ("gt", "lt"):
                    lm = (vv > th) if direction == "gt" else (vv < th)
                    if lm.sum() < min_n:
                        continue
                    sp = pv[lm]
                    se = ev[lm]
                    spf = compute_pf(sp)
                    if spf is None or spf == float("inf"):
                        continue
                    sdpf, _, _ = compute_daily_pf(sp, se)
                    score = spf
                    if best is None or score > best["score"]:
                        best = {
                            "score": score, "feature": feat,
                            "direction": direction, "threshold": float(th),
                            "pf": spf, "daily_pf_median": sdpf,
                            "n": int(lm.sum()),
                        }
        if best is None or best["score"] <= cur_pf + 0.001:
            print(f"[stack] no improvement at step {step}, stopping", flush=True)
            break
        feat = best["feature"]
        vals = feature_matrix[feat]
        if best["direction"] == "gt":
            mask = mask & (vals > best["threshold"]) & ~np.isnan(vals)
        else:
            mask = mask & (vals < best["threshold"]) & ~np.isnan(vals)
        stack.append(best)
        print(f"[stack]   {feat} {best['direction']} {best['threshold']:.4f} "
              f"→ pf={best['pf']:.3f} dpf={best['daily_pf_median']:.3f} n={best['n']}",
              flush=True)
    final_n = int(mask.sum())
    final_pf = compute_pf(pnl[mask])
    final_dpf, final_pct, _ = compute_daily_pf(pnl[mask], ee[mask])
    return {
        "final_mask": mask, "final_n": final_n,
        "final_pf": final_pf, "final_dpf": final_dpf,
        "final_pct_days_positive": final_pct,
        "stack": stack, "history": history,
    }


# ─────────────────────────────── Walk-forward ────────────────────────────────
def walk_forward(feature_matrix, pnl, rm, ee, n_folds=5, embargo_days=1,
                 **stack_kwargs):
    ts_min = int(ee.min())
    ts_max = int(ee.max())
    span = ts_max - ts_min
    fold_size = span // n_folds
    embargo = embargo_days * 86400
    fold_rows = []
    for k in range(n_folds):
        test_lo = ts_min + k * fold_size
        test_hi = ts_min + (k + 1) * fold_size if k < n_folds - 1 else ts_max + 1
        test_mask = (ee >= test_lo) & (ee < test_hi)
        train_mask = (ee < test_lo - embargo) | (ee >= test_hi + embargo)
        if train_mask.sum() < MIN_FOLD_N * 2 or test_mask.sum() < MIN_FOLD_N:
            print(f"[wf] fold {k} too small, skip", flush=True)
            continue
        train_fm = {kk: v[train_mask] for kk, v in feature_matrix.items()}
        res = greedy_stack(
            train_fm, pnl[train_mask], rm[train_mask], ee[train_mask],
            **stack_kwargs,
        )
        tst_mask = test_mask.copy()
        for f in res["stack"]:
            vals = feature_matrix[f["feature"]]
            if f["direction"] == "gt":
                tst_mask &= (vals > f["threshold"]) & ~np.isnan(vals)
            else:
                tst_mask &= (vals < f["threshold"]) & ~np.isnan(vals)
        tn = int(tst_mask.sum())
        tpf = compute_pf(pnl[tst_mask]) if tn > 0 else 0.0
        if tn > 0:
            tdpf, tpct, _ = compute_daily_pf(pnl[tst_mask], ee[tst_mask])
        else:
            tdpf, tpct = 0.0, 0.0
        fold_rows.append({
            "fold": k,
            "train_n": int(train_mask.sum()),
            "train_final_pf": res["final_pf"],
            "train_final_dpf": res["final_dpf"],
            "train_stack": res["stack"],
            "test_n": tn,
            "test_pf": tpf,
            "test_daily_pf_median": tdpf,
            "test_pct_days_positive": tpct,
        })
    return fold_rows


# ─────────────────────────────── Main ────────────────────────────────
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
            f: cand[f][wtc] for f in CONTINUOUS_FEATURES if f in cand
        }
        symbol_arr = cand["symbol"][wtc]

        base_pf = compute_pf(pnl)
        base_dpf, base_pct, _ = compute_daily_pf(pnl, ee)
        print(f"[baseline] {variant} n={n} pf={base_pf:.3f} dpf={base_dpf:.3f}",
              flush=True)

        print(f"[univ] sweeping {len(feature_matrix)} features...", flush=True)
        univ_rows = []
        for feat, vals in feature_matrix.items():
            univ_rows.extend(univariate_sweep(vals, pnl, rm, ee, feat))
        sym_rows = symbol_sweep(symbol_arr, pnl, rm, ee)
        feature_rank = rank_features(univ_rows)

        top5 = [r["feature"] for r in feature_rank[:5]]
        print(f"[bivar] top-5: {top5}", flush=True)
        bivar = bivariate_sweep(feature_matrix, top5, pnl, ee, n_bins=5)

        print("[stack] in-sample greedy...", flush=True)
        stack_is = greedy_stack(
            feature_matrix, pnl, rm, ee,
            max_depth=5, target_pf=1.20, min_n=500,
        )

        print("[wf] 5-fold walk-forward...", flush=True)
        wf = walk_forward(
            feature_matrix, pnl, rm, ee, n_folds=5, embargo_days=1,
            max_depth=5, target_pf=1.20, min_n=300,
        )

        reports[variant] = {
            "baseline": {
                "n": n, "pf": base_pf,
                "daily_pf_median": base_dpf,
                "pct_days_positive": base_pct,
            },
            "univariate": univ_rows,
            "symbol": sym_rows,
            "feature_rank": feature_rank,
            "bivariate_top20": bivar[:20],
            "in_sample_stack": {
                "final_n": stack_is["final_n"],
                "final_pf": stack_is["final_pf"],
                "final_dpf": stack_is["final_dpf"],
                "final_pct_days_positive": stack_is["final_pct_days_positive"],
                "stack": stack_is["stack"],
                "history": stack_is["history"],
            },
            "walk_forward": wf,
        }

    out_dir = args.input_dir
    json_path = os.path.join(out_dir, "feature_discriminator.json")
    with open(json_path, "w") as f:
        json.dump(reports, f, indent=2, default=str)
    print(f"[out] JSON: {json_path}", flush=True)

    write_md(reports, out_dir, args.input_dir)
    print(f"[main] DONE in {time.time()-t0:.1f}s", flush=True)


def write_md(reports, out_dir, input_dir):
    md = []
    md.append("# MACD Feature Discriminator — Phase 0b")
    md.append("")
    md.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ")
    md.append(f"**Input:** `{input_dir}`  ")
    md.append(f"**Fixed exit:** stop={FIXED_STOP_MULT}×ATR, ts={FIXED_TS_BARS} bars  ")
    md.append(
        "**Method:** univariate 10-quantile PF bins → rank by max-bin PF → "
        "bivariate 5×5 heatmap on top-5 pairs → greedy filter stack → "
        "5-fold purged walk-forward (1-day embargo)."
    )
    md.append("")

    for variant, rep in reports.items():
        md.append(f"## Variant: `{variant}`")
        md.append("")
        b = rep["baseline"]
        pf_s = f"{b['pf']:.3f}" if b['pf'] is not None else "—"
        md.append(
            f"**Baseline (no filter):** N={b['n']}, PF={pf_s}, "
            f"dPFmed={b['daily_pf_median']:.3f}, "
            f"%+days={100*b['pct_days_positive']:.1f}%"
        )
        md.append("")

        md.append("### Feature rank (top 15 by max-bin PF)")
        md.append("")
        md.append("| # | feature | max_pf | min_pf | spread | max_dpf | best bin range | best N | mono |")
        md.append("|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(rep["feature_rank"][:15], 1):
            md.append(
                f"| {i} | `{r['feature']}` | {r['max_pf']:.3f} | "
                f"{r['min_pf']:.3f} | {r['pf_spread']:.3f} | "
                f"{r['max_daily_pf_median']:.3f} | "
                f"[{r['best_bin_lo']:.3f}, {r['best_bin_hi']:.3f}] | "
                f"{r['best_bin_n']} | {r['monotonicity']:+.2f} |"
            )
        md.append("")

        md.append("### Top-5 features — univariate quantile PF")
        md.append("")
        top5 = [r["feature"] for r in rep["feature_rank"][:5]]
        for feat in top5:
            md.append(f"#### `{feat}`")
            md.append("")
            md.append("| bin | range | N | PF | dPFmed | %+days | win% | avg_r | exp$ |")
            md.append("|---|---|---|---|---|---|---|---|---|")
            rows = [r for r in rep["univariate"] if r["feature"] == feat]
            rows.sort(key=lambda r: r["bin"])
            for r in rows:
                pf_v = r["pf"] if r["pf"] is not None else 0.0
                md.append(
                    f"| {r['bin']} | [{r['bin_lo']:.3f}, {r['bin_hi']:.3f}] | "
                    f"{r['n']} | {pf_v:.3f} | {r['daily_pf_median']:.3f} | "
                    f"{100*r['pct_days_positive']:.1f}% | {r['win_pct']:.1f}% | "
                    f"{r['avg_r']:.3f} | ${r['expectancy']:.2f} |"
                )
            md.append("")

        sym_rows = sorted(
            rep["symbol"], key=lambda r: (r["pf"] or 0), reverse=True,
        )
        md.append("### Top-10 symbols by PF (N ≥ 50)")
        md.append("")
        md.append("| symbol | N | PF | dPFmed | %+days | win% | exp$ |")
        md.append("|---|---|---|---|---|---|---|")
        for r in sym_rows[:10]:
            pf_v = r["pf"] if r["pf"] is not None else 0.0
            md.append(
                f"| {r['symbol']} | {r['n']} | {pf_v:.3f} | "
                f"{r['daily_pf_median']:.3f} | "
                f"{100*r['pct_days_positive']:.1f}% | "
                f"{r['win_pct']:.1f}% | ${r['expectancy']:.2f} |"
            )
        md.append("")
        md.append("### Bottom-10 symbols by PF (N ≥ 50)")
        md.append("")
        md.append("| symbol | N | PF | dPFmed | win% | exp$ |")
        md.append("|---|---|---|---|---|---|")
        for r in sym_rows[-10:]:
            pf_v = r["pf"] if r["pf"] is not None else 0.0
            md.append(
                f"| {r['symbol']} | {r['n']} | {pf_v:.3f} | "
                f"{r['daily_pf_median']:.3f} | {r['win_pct']:.1f}% | "
                f"${r['expectancy']:.2f} |"
            )
        md.append("")

        md.append("### Top-10 bivariate cells (5×5 grid on top-5 pair-wise)")
        md.append("")
        md.append("| # | f1 × f2 | f1 range | f2 range | N | PF | dPFmed |")
        md.append("|---|---|---|---|---|---|---|")
        for i, c in enumerate(rep["bivariate_top20"][:10], 1):
            md.append(
                f"| {i} | `{c['f1']}` × `{c['f2']}` | "
                f"[{c['f1_lo']:.3f}, {c['f1_hi']:.3f}] | "
                f"[{c['f2_lo']:.3f}, {c['f2_hi']:.3f}] | "
                f"{c['n']} | {c['pf']:.3f} | {c['daily_pf_median']:.3f} |"
            )
        md.append("")

        stk = rep["in_sample_stack"]
        md.append("### In-sample greedy filter stack")
        md.append("")
        md.append(
            f"**Final (in-sample):** N={stk['final_n']}, "
            f"PF={stk['final_pf']:.3f}, dPFmed={stk['final_dpf']:.3f}, "
            f"%+days={100*stk['final_pct_days_positive']:.1f}%"
        )
        md.append("")
        md.append("| step | feature | dir | threshold | PF | dPFmed | N |")
        md.append("|---|---|---|---|---|---|---|")
        for i, f in enumerate(stk["stack"], 1):
            md.append(
                f"| {i} | `{f['feature']}` | {f['direction']} | "
                f"{f['threshold']:.4f} | {f['pf']:.3f} | "
                f"{f['daily_pf_median']:.3f} | {f['n']} |"
            )
        md.append("")

        md.append("### Walk-forward (5-fold purged, 1-day embargo)")
        md.append("")
        md.append("| fold | train_n | train_PF | test_n | test_PF | test_dPFmed |")
        md.append("|---|---|---|---|---|---|")
        for r in rep["walk_forward"]:
            md.append(
                f"| {r['fold']} | {r['train_n']} | "
                f"{r['train_final_pf']:.3f} | {r['test_n']} | "
                f"{r['test_pf']:.3f} | {r['test_daily_pf_median']:.3f} |"
            )
        md.append("")
        test_pfs = [r["test_pf"] for r in rep["walk_forward"]]
        test_dpfs = [r["test_daily_pf_median"] for r in rep["walk_forward"]]
        if test_pfs:
            fp = sum(1 for pf in test_pfs if pf > 1.0)
            md.append(
                f"**Aggregate:** {fp}/{len(test_pfs)} folds test PF > 1.0; "
                f"avg test PF = {sum(test_pfs)/len(test_pfs):.3f}; "
                f"avg test dPFmed = {sum(test_dpfs)/len(test_dpfs):.3f}"
            )
        md.append("")

    md_path = os.path.join(out_dir, "feature_discriminator.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md))
    print(f"[out] MD: {md_path}", flush=True)


if __name__ == "__main__":
    main()
