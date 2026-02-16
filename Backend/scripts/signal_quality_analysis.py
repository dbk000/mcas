#!/usr/bin/env python3
"""
Signal Quality Analysis (Phase 7, Task 2)
==========================================

Feature correlation analysis + walk-forward L1 logistic regression to determine
whether opportunity payload features can predict trade outcomes.

Outputs:
  <outdir>/feature_matrix.csv         — Full feature matrix with outcomes
  <outdir>/feature_correlations.csv   — Per-feature Spearman correlations
  <outdir>/walk_forward_results.csv   — Per-fold, per-threshold metrics
  Console: comprehensive analysis report with verdict

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
      python3 /scripts/signal_quality_analysis.py \
      --batch-id backfill_20260216_5a7a894c \
      --outdir /tmp/signal_analysis/

Requires: numpy, scipy, scikit-learn (pip install if missing).
"""

import os
import sys
import csv
import json
import time
import math
import argparse
import logging
from datetime import datetime, timezone
from collections import defaultdict

# Setup logging before imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Load .env
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

# Add scripts dir for exit_sweep imports
scripts_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, scripts_dir)

from exit_sweep import (
    load_opportunities, compute_ohlcv_ranges, batch_load_ohlcv,
    prepare_trade, run_exit_for_trade, compute_pnl, ExitConfig,
)

# ML imports (graceful fallback)
try:
    import numpy as np
    from scipy import stats as scipy_stats
    HAS_STATS = True
except ImportError:
    HAS_STATS = False
    logger.error("numpy/scipy not available. Install: pip install numpy scipy --break-system-packages")

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    logger.warning("scikit-learn not available. Install: pip install scikit-learn --break-system-packages")

# ─── Constants ────────────────────────────────────────────────────────────────

UTC = timezone.utc
BASELINE = ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=1.0)

# Walk-forward folds: (name, train_start, val_start, val_end) — half-open [start, end)
FOLDS = [
    ("Fold1", datetime(2026, 2, 9, tzinfo=UTC), datetime(2026, 2, 12, tzinfo=UTC), datetime(2026, 2, 14, tzinfo=UTC)),
    ("Fold2", datetime(2026, 2, 9, tzinfo=UTC), datetime(2026, 2, 14, tzinfo=UTC), datetime(2026, 2, 16, tzinfo=UTC)),
    ("Fold3", datetime(2026, 2, 9, tzinfo=UTC), datetime(2026, 2, 16, tzinfo=UTC), datetime(2026, 2, 17, tzinfo=UTC)),
]

EXCLUDE_PATTERNS = ["mae", "mfe", "exit", "pnl", "r_multiple", "bars_held", "result", "outcome"]
THRESHOLDS = [0.50, 0.40, 0.30, 0.20]
MODEL_SETUPS = ["S6", "S7"]


# ─── Feature extraction ──────────────────────────────────────────────────────

def extract_features(opp, trade):
    """Extract numeric features from opportunity payload + trade structure."""
    payload = opp.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if payload is None:
        payload = {}

    f = {}

    # Structural features (known at entry time)
    f["is_long"] = 1.0 if trade.direction == "long" else 0.0
    f["opp_score"] = float(opp.get("score", 0) or 0)
    tf_map = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
    f["tf_minutes"] = float(tf_map.get(trade.timeframe, 60))

    if trade.entry_price > 0:
        f["r_value_pct"] = trade.r_value / trade.entry_price * 100
        f["stop_dist_pct"] = abs(trade.stop_price - trade.entry_price) / trade.entry_price * 100
    if trade.tp1_price is not None and trade.r_value > 0:
        f["tp1_dist_r"] = abs(trade.tp1_price - trade.entry_price) / trade.r_value
    if trade.tp2_price is not None and trade.r_value > 0:
        f["tp2_dist_r"] = abs(trade.tp2_price - trade.entry_price) / trade.r_value
    if trade.created_ts:
        f["hour_utc"] = float(trade.created_ts.hour)
        f["day_of_week"] = float(trade.created_ts.weekday())

    # Flatten all numeric values from payload
    _flatten(payload, "", f)
    return f


def _flatten(obj, prefix, out):
    """Recursively extract numeric values, skip outcome-adjacent fields."""
    if not isinstance(obj, dict):
        return
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if any(pat in key.lower() for pat in EXCLUDE_PATTERNS):
            continue
        if isinstance(v, bool):
            out[key] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            if not (math.isnan(v) or math.isinf(v)):
                out[key] = float(v)
        elif isinstance(v, dict):
            _flatten(v, key, out)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def pf(pnls):
    """Profit factor from a list/array of PnL values."""
    if hasattr(pnls, '__len__') and len(pnls) == 0:
        return 0.0
    if isinstance(pnls, np.ndarray):
        gp = float(pnls[pnls > 0].sum())
        gl = float(abs(pnls[pnls <= 0].sum()))
    else:
        gp = sum(p for p in pnls if p > 0)
        gl = abs(sum(p for p in pnls if p <= 0))
    return gp / gl if gl > 0 else (99.99 if gp > 0 else 0.0)


# ─── Correlation analysis ─────────────────────────────────────────────────────

def compute_correlations(records):
    """Spearman correlations between features and binary win/loss."""
    if not HAS_STATS:
        return []

    # Collect all feature keys
    all_keys = set()
    for r in records:
        all_keys.update(r["features"].keys())

    results = []
    for key in sorted(all_keys):
        vals, wins, setups = [], [], []
        for r in records:
            v = r["features"].get(key)
            if v is not None and not math.isnan(v) and not math.isinf(v):
                vals.append(v)
                wins.append(r["winner"])
                setups.append(r["setup_id"])

        if len(vals) < 10 or len(set(vals)) < 2:
            continue

        try:
            rho, p_val = scipy_stats.spearmanr(vals, wins)
        except Exception:
            continue
        if math.isnan(rho):
            continue

        # Per-setup correlations
        per_setup = {}
        groups = defaultdict(lambda: ([], []))
        for v, w, s in zip(vals, wins, setups):
            groups[s][0].append(v)
            groups[s][1].append(w)

        for sid, (sv, sw) in groups.items():
            if len(sv) >= 10 and len(set(sv)) >= 2:
                try:
                    sr, sp = scipy_stats.spearmanr(sv, sw)
                    if not math.isnan(sr):
                        per_setup[sid] = (sr, sp, len(sv))
                except Exception:
                    pass

        results.append({
            "feature": key,
            "rho": rho,
            "p": p_val,
            "n": len(vals),
            "score": abs(rho) * math.sqrt(len(vals)),
            "per_setup": per_setup,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ─── Walk-forward model ──────────────────────────────────────────────────────

def run_walk_forward(records, setup):
    """Walk-forward L1 logistic regression for one setup."""
    if not HAS_SKLEARN:
        return []

    recs = [r for r in records if r["setup_id"] == setup]
    if len(recs) < 20:
        logger.info("  %s: %d trades (too few), skipping", setup, len(recs))
        return []

    # Feature keys with >70% coverage in this setup
    counts = defaultdict(int)
    for r in recs:
        for k in r["features"]:
            counts[k] += 1
    min_cov = int(len(recs) * 0.7)
    fkeys = sorted(k for k, c in counts.items() if c >= min_cov)

    if len(fkeys) < 3:
        logger.info("  %s: %d features (too few), skipping", setup, len(fkeys))
        return []

    logger.info("  %s: %d trades, %d features (>70%% coverage)", setup, len(recs), len(fkeys))

    fold_results = []
    for fold_name, t_start, v_start, v_end in FOLDS:
        train = [r for r in recs if t_start <= r["created_ts"] < v_start]
        val = [r for r in recs if v_start <= r["created_ts"] < v_end]

        if len(train) < 10 or len(val) < 5:
            logger.info("    %s %s: train=%d val=%d (skip)", setup, fold_name, len(train), len(val))
            continue

        # Build matrices
        X_tr = np.zeros((len(train), len(fkeys)))
        X_vl = np.zeros((len(val), len(fkeys)))
        y_tr = np.array([r["winner"] for r in train])
        y_vl = np.array([r["winner"] for r in val])
        pnl_vl = np.array([r["pnl"] for r in val])

        for row, r in enumerate(train):
            for col, k in enumerate(fkeys):
                X_tr[row, col] = r["features"].get(k, np.nan)
        for row, r in enumerate(val):
            for col, k in enumerate(fkeys):
                X_vl[row, col] = r["features"].get(k, np.nan)

        # Impute NaN with training median
        medians = np.nanmedian(X_tr, axis=0)
        for col in range(len(fkeys)):
            med = medians[col] if not np.isnan(medians[col]) else 0.0
            X_tr[np.isnan(X_tr[:, col]), col] = med
            X_vl[np.isnan(X_vl[:, col]), col] = med

        # Standardize
        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_vl_s = scaler.transform(X_vl)

        if y_tr.sum() == 0 or y_tr.sum() == len(y_tr):
            logger.info("    %s %s: single class in training, skip", setup, fold_name)
            continue

        # Fit L1 logistic regression (C=0.1 = strong regularization for high feature/sample ratio)
        try:
            model = LogisticRegression(penalty='l1', solver='liblinear', C=0.1, max_iter=1000)
            model.fit(X_tr_s, y_tr)
        except Exception as e:
            logger.warning("    %s %s: fit failed: %s", setup, fold_name, e)
            continue

        scores = model.predict_proba(X_vl_s)[:, 1]

        # Non-zero coefficients
        nonzero = [(fkeys[i], float(model.coef_[0][i]))
                    for i in range(len(fkeys)) if abs(model.coef_[0][i]) > 1e-6]
        nonzero.sort(key=lambda x: abs(x[1]), reverse=True)

        # Threshold analysis
        thresh_rows = []
        sorted_idx = np.argsort(scores)[::-1]
        for thr in THRESHOLDS:
            n_take = max(1, int(len(val) * thr))
            take_i = sorted_idx[:n_take]
            skip_i = sorted_idx[n_take:]

            take_pnl = pnl_vl[take_i]
            skip_pnl = pnl_vl[skip_i] if len(skip_i) > 0 else np.array([])

            take_pf_val = pf(take_pnl)
            skip_pf_val = pf(skip_pnl) if len(skip_pnl) > 0 else 0.0

            thresh_rows.append({
                "threshold": thr,
                "take_n": len(take_i),
                "take_wr": float(np.mean(take_pnl > 0)) if len(take_pnl) > 0 else 0,
                "take_pf": take_pf_val,
                "take_pnl": float(take_pnl.sum()),
                "skip_n": len(skip_i),
                "skip_wr": float(np.mean(skip_pnl > 0)) if len(skip_pnl) > 0 else 0,
                "skip_pf": skip_pf_val,
                "skip_pnl": float(skip_pnl.sum()) if len(skip_pnl) > 0 else 0,
                "separation": take_pf_val - skip_pf_val,
            })

        fold_results.append({
            "fold": fold_name,
            "train_n": len(train),
            "val_n": len(val),
            "val_wr": float(np.mean(y_vl)),
            "val_pf": pf(pnl_vl),
            "nonzero": nonzero,
            "thresholds": thresh_rows,
        })

        logger.info("    %s %s: train=%d val=%d val_PF=%.3f nonzero=%d",
                     setup, fold_name, len(train), len(val), pf(pnl_vl), len(nonzero))

    return fold_results


# ─── CSV output ───────────────────────────────────────────────────────────────

def save_feature_matrix(records, path):
    """Save feature matrix + outcomes to CSV."""
    all_keys = sorted(set(k for r in records for k in r["features"]))

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["setup_id", "direction", "timeframe", "symbol", "created_ts",
                     "winner", "pnl", "r_mult", "exit_reason"] + all_keys)
        for r in records:
            row = [r["setup_id"], r["direction"], r["timeframe"], r["symbol"],
                   r["created_ts"].isoformat(), r["winner"],
                   round(r["pnl"], 4), round(r["r_mult"], 4), r["exit_reason"]]
            for k in all_keys:
                v = r["features"].get(k)
                row.append(round(v, 6) if v is not None else "")
            w.writerow(row)
    logger.info("  Feature matrix: %s (%d × %d)", path, len(records), len(all_keys))


def save_correlations(corr_results, path):
    """Save correlation results to CSV."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["feature", "rho", "p_value", "n", "score",
                     "S6_rho", "S6_p", "S6_n", "S7_rho", "S7_p", "S7_n"])
        for cr in corr_results:
            s6 = cr["per_setup"].get("S6", (None, None, None))
            s7 = cr["per_setup"].get("S7", (None, None, None))
            w.writerow([
                cr["feature"], round(cr["rho"], 6), round(cr["p"], 6), cr["n"],
                round(cr["score"], 4),
                round(s6[0], 6) if s6[0] is not None else "",
                round(s6[1], 6) if s6[1] is not None else "",
                s6[2] if s6[2] is not None else "",
                round(s7[0], 6) if s7[0] is not None else "",
                round(s7[1], 6) if s7[1] is not None else "",
                s7[2] if s7[2] is not None else "",
            ])
    logger.info("  Correlations: %s (%d features)", path, len(corr_results))


def save_walk_forward(all_wf, path):
    """Save walk-forward results to CSV."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["setup", "fold", "train_n", "val_n", "val_wr", "val_pf",
                     "threshold", "take_n", "take_wr", "take_pf", "take_pnl",
                     "skip_n", "skip_wr", "skip_pf", "skip_pnl", "separation",
                     "nonzero_features"])
        for setup, folds in all_wf.items():
            for fold in folds:
                feats_str = "; ".join(f"{n}={c:+.4f}" for n, c in fold["nonzero"][:10])
                for tm in fold["thresholds"]:
                    w.writerow([
                        setup, fold["fold"], fold["train_n"], fold["val_n"],
                        round(fold["val_wr"], 4), round(fold["val_pf"], 3),
                        tm["threshold"], tm["take_n"],
                        round(tm["take_wr"], 4), round(tm["take_pf"], 3),
                        round(tm["take_pnl"], 2),
                        tm["skip_n"], round(tm["skip_wr"], 4),
                        round(tm["skip_pf"], 3), round(tm["skip_pnl"], 2),
                        round(tm["separation"], 3), feats_str,
                    ])
    logger.info("  Walk-forward: %s", path)


# ─── Console report ──────────────────────────────────────────────────────────

def print_correlations(corr_results):
    """Print top 20 feature correlations."""
    print("\n" + "=" * 105)
    print("FEATURE CORRELATION ANALYSIS (Spearman rho with win/loss, all setups)")
    print("=" * 105)

    print(f"\n{'#':>3} {'Feature':>30} {'rho':>7} {'p':>8}   {'N':>5} {'Score':>6}"
          f"  {'S6_rho':>7} {'S7_rho':>7}")
    print("-" * 105)

    for i, cr in enumerate(corr_results[:20]):
        s6 = cr["per_setup"].get("S6")
        s7 = cr["per_setup"].get("S7")
        s6s = f"{s6[0]:>+.3f}" if s6 else "    n/a"
        s7s = f"{s7[0]:>+.3f}" if s7 else "    n/a"
        sig = "*" if cr["p"] < 0.05 else " "
        print(f"{i+1:>3} {cr['feature']:>30} {cr['rho']:>+.4f} {cr['p']:>8.4f}{sig}"
              f" {cr['n']:>5} {cr['score']:>6.3f}  {s6s} {s7s}")

    sig_n = sum(1 for c in corr_results if c["p"] < 0.05)
    strong_n = sum(1 for c in corr_results if abs(c["rho"]) > 0.10 and c["p"] < 0.05)
    print(f"\n  Total features: {len(corr_results)}")
    print(f"  Significant (p < 0.05): {sig_n}")
    print(f"  Strong (|rho| > 0.10, p < 0.05): {strong_n}")

    if strong_n == 0:
        print("  → No features have meaningful correlation with outcomes")


def print_walk_forward(all_wf):
    """Print walk-forward model results."""
    print("\n" + "=" * 105)
    print("WALK-FORWARD MODEL (L1 Logistic Regression, C=0.1)")
    print("=" * 105)

    for setup, folds in all_wf.items():
        if not folds:
            print(f"\n  {setup}: SKIPPED (insufficient data)")
            continue

        print(f"\n  {setup}")
        print(f"  {'Fold':>6} {'TrN':>4} {'VlN':>4} {'VlWR':>6} {'VlPF':>6} {'#Feat':>5}"
              f"  Top features (name=coef)")
        print("  " + "-" * 95)

        for fl in folds:
            top = ", ".join(f"{n}({c:+.3f})" for n, c in fl["nonzero"][:4])
            print(f"  {fl['fold']:>6} {fl['train_n']:>4} {fl['val_n']:>4} "
                  f"{fl['val_wr']*100:>5.1f}% {fl['val_pf']:>6.3f} "
                  f"{len(fl['nonzero']):>5}  {top}")

        # Threshold summary across folds
        print(f"\n  Threshold analysis (mean across {len(folds)} folds):")
        print(f"  {'Thresh':>8} {'Take_N':>6} {'TakeWR':>7} {'TakePF':>7}"
              f" {'SkipPF':>7} {'Separ':>7} {'Consistent':>10}")
        print("  " + "-" * 65)

        for thr in THRESHOLDS:
            t_ns, t_wrs, t_pfs, s_pfs, seps = [], [], [], [], []
            for fl in folds:
                for tm in fl["thresholds"]:
                    if tm["threshold"] == thr:
                        t_ns.append(tm["take_n"])
                        t_wrs.append(tm["take_wr"])
                        t_pfs.append(tm["take_pf"])
                        s_pfs.append(tm["skip_pf"])
                        seps.append(tm["separation"])

            if not seps:
                continue

            n_pos = sum(1 for s in seps if s > 0)
            consistent = "YES" if n_pos == len(seps) else f"{n_pos}/{len(seps)}"
            mean = lambda xs: sum(xs) / len(xs) if xs else 0

            print(f"  Top {thr*100:>3.0f}% {mean(t_ns):>6.0f} {mean(t_wrs)*100:>6.1f}% "
                  f"{mean(t_pfs):>7.3f} {mean(s_pfs):>7.3f} {mean(seps):>+7.3f} "
                  f"{consistent:>10}")

        # Feature consistency across folds
        feat_counts = defaultdict(list)
        for fl in folds:
            for name, coef in fl["nonzero"]:
                feat_counts[name].append(coef)

        consistent_feats = [
            (name, coefs) for name, coefs in feat_counts.items()
            if len(coefs) >= len(folds) - 1  # present in almost all folds
            and (all(c > 0 for c in coefs) or all(c < 0 for c in coefs))  # same sign
        ]
        consistent_feats.sort(key=lambda x: abs(sum(x[1]) / len(x[1])), reverse=True)

        if consistent_feats:
            print(f"\n  Features consistent across folds (same sign, present in >={len(folds)-1} folds):")
            for name, coefs in consistent_feats[:8]:
                mc = sum(coefs) / len(coefs)
                direction = "→ predicts WIN" if mc > 0 else "→ predicts LOSS"
                print(f"    {name:>30}: mean_coef={mc:>+.4f} ({len(coefs)} folds) {direction}")
        else:
            print(f"\n  No features consistent across folds (model is unstable)")


def print_verdict(corr_results, all_wf):
    """Print final verdict."""
    print("\n" + "=" * 105)
    print("VERDICT")
    print("=" * 105)

    has_signal = False

    for setup, folds in all_wf.items():
        if not folds:
            continue

        # Find best threshold with consistent positive separation
        best_thr = None
        best_mean_take_pf = 0
        best_mean_sep = -999

        for thr in THRESHOLDS:
            seps, take_pfs = [], []
            for fl in folds:
                for tm in fl["thresholds"]:
                    if tm["threshold"] == thr:
                        seps.append(tm["separation"])
                        take_pfs.append(tm["take_pf"])

            if not seps:
                continue

            all_pos = all(s > 0 for s in seps)
            mean_take = sum(take_pfs) / len(take_pfs)
            mean_sep = sum(seps) / len(seps)

            if all_pos and mean_take > 1.0:
                print(f"\n  {setup} — SIGNAL DETECTED at top {thr*100:.0f}%")
                print(f"    Mean take PF = {mean_take:.3f}, separation = {mean_sep:+.3f}")
                print(f"    Positive separation in ALL {len(seps)} folds")
                has_signal = True
                break

            if mean_sep > best_mean_sep:
                best_mean_sep = mean_sep
                best_thr = thr
                best_mean_take_pf = mean_take

        else:
            n_pos = 0
            for fl in folds:
                for tm in fl["thresholds"]:
                    if tm["threshold"] == best_thr and tm["separation"] > 0:
                        n_pos += 1
            print(f"\n  {setup} — NO CONSISTENT SIGNAL")
            if best_thr is not None:
                print(f"    Best: top {best_thr*100:.0f}%, mean take PF = {best_mean_take_pf:.3f}, "
                      f"separation = {best_mean_sep:+.3f}, positive in {n_pos}/{len(folds)} folds")

    # Feature summary
    strong = [c for c in corr_results if abs(c["rho"]) > 0.10 and c["p"] < 0.05]
    print(f"\n  Feature landscape: {len(strong)} strong correlations (|rho|>0.10, p<0.05)"
          f" out of {len(corr_results)} features")

    print(f"\n  {'='*60}")
    if has_signal:
        print("  OVERALL: DEPLOYABLE SIGNAL EXISTS")
        print("  Proceed to scoring function implementation.")
    else:
        print("  OVERALL: NO DEPLOYABLE SIGNAL FOUND")
        print()
        print("  The current feature set does not contain sufficient predictive")
        print("  power to achieve positive expectancy after fees and slippage.")
        print()
        print("  What would need to change:")
        print("    1. New signal types with stronger directional prediction")
        print("    2. Higher timeframes only (reduce noise, increase per-trade edge)")
        print("    3. More selective entry criteria (quality over quantity)")
        print("    4. Regime detection to avoid adverse market conditions")
        print("    5. Longer evaluation period (8 days is thin for regime analysis)")
    print(f"  {'='*60}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Signal quality analysis (Phase 7 Task 2)")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--outdir", default="/tmp/signal_analysis")
    parser.add_argument("--timeframes", default="15m,1h,4h,1d")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    if not HAS_STATS:
        logger.error("numpy/scipy required. Exiting.")
        sys.exit(1)

    timeframes = [t.strip() for t in args.timeframes.split(",")]
    os.makedirs(args.outdir, exist_ok=True)
    t0 = time.time()

    # ── Phase 1: Load data ────────────────────────────────────────────────
    logger.info("Phase 1: Loading data...")
    opportunities = load_opportunities(database_url, args.batch_id, timeframes)
    if not opportunities:
        logger.error("No opportunities found")
        sys.exit(1)

    ranges = compute_ohlcv_ranges(opportunities)
    ohlcv_cache = batch_load_ohlcv(database_url, ranges)
    logger.info("  Data loaded in %.1fs", time.time() - t0)

    # ── Phase 2: Prepare trades + features + outcomes ─────────────────────
    t1 = time.time()
    logger.info("Phase 2: Preparing trades and extracting features...")

    records = []
    filtered = 0
    for opp in opportunities:
        trade = prepare_trade(opp, ohlcv_cache)
        if trade is None:
            filtered += 1
            continue

        reason, price, bars_held = run_exit_for_trade(trade, BASELINE, "exit")
        pnl_val, r_mult = compute_pnl(trade, reason, price)
        features = extract_features(opp, trade)

        records.append({
            "features": features,
            "winner": 1 if pnl_val > 0 else 0,
            "pnl": pnl_val,
            "r_mult": r_mult,
            "setup_id": trade.setup_id,
            "created_ts": trade.created_ts,
            "exit_reason": reason,
            "direction": trade.direction,
            "timeframe": trade.timeframe,
            "symbol": trade.symbol,
        })

    n_features = len(set(k for r in records for k in r["features"]))
    logger.info("  %d trades (%d filtered), %d features extracted in %.1fs",
                len(records), filtered, n_features, time.time() - t1)

    save_feature_matrix(records, os.path.join(args.outdir, "feature_matrix.csv"))

    # ── Phase 3: Correlation analysis ─────────────────────────────────────
    t2 = time.time()
    logger.info("Phase 3: Correlation analysis...")
    corr = compute_correlations(records)
    save_correlations(corr, os.path.join(args.outdir, "feature_correlations.csv"))
    print_correlations(corr)
    logger.info("  Correlations computed in %.1fs", time.time() - t2)

    # ── Phase 4: Walk-forward models ──────────────────────────────────────
    all_wf = {}
    if HAS_SKLEARN:
        t3 = time.time()
        logger.info("Phase 4: Walk-forward model evaluation...")
        for setup in MODEL_SETUPS:
            all_wf[setup] = run_walk_forward(records, setup)
        save_walk_forward(all_wf, os.path.join(args.outdir, "walk_forward_results.csv"))
        print_walk_forward(all_wf)
        logger.info("  Models evaluated in %.1fs", time.time() - t3)
    else:
        logger.warning("Phase 4: Skipped (scikit-learn not available)")

    # ── Phase 5: Verdict ──────────────────────────────────────────────────
    print_verdict(corr, all_wf)

    logger.info("Done. Total: %.1fs. Output: %s", time.time() - t0, args.outdir)


if __name__ == "__main__":
    main()
