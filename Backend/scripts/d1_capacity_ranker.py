#!/usr/bin/env python3
"""
D1 Capacity Overflow Ranker — Data-Driven Signal Selection
============================================================

The question: when more D1 signals fire than you can hold (capacity overflow),
which signals should you prioritize?

Approach:
  1. Load 889 Config D-surviving trades with all raw features from payload
  2. Train pointwise ridge regression: features → R-multiple
  3. Walk-forward validate (expanding window, 4 test folds):
     - At each capacity level, compare 4 selection methods:
       FIFO (chronological skip), Score (heuristic composite),
       Ranker (predicted R-multiple), Random (averaged over N shuffles)
  4. Verdict: does the ranker beat score? Does it beat random?

If the ranker can't beat random, that's the highest-leverage finding —
it means the scoring system cannot differentiate signal quality within
a validated strategy, and fixing that becomes priority #1.

Compute profile:
  ~2s DB load + ~15s OHLCV + Config D sim + ~5s ranker training/eval = ~22s total
  Single-threaded throughout; I/O bound on DB reads.

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d1_capacity_ranker.py \
            --batch-id 'backfill_20260303_D1v1%%' \
            --max-concurrent 3,5,8,15
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.stats import spearmanr

# ── Imports from sim_d1_matrix ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim_d1_matrix import (
    EXIT_CONFIGS,
    SIM_FEE_BPS,
    SIM_POSITION_USD,
    SIM_SLIPPAGE_BPS,
    compute_ohlcv_ranges,
    load_ohlcv_batch,
    load_opportunities,
    parse_payload,
    simulate_opportunity,
)

log = logging.getLogger("d1_capacity_ranker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

# ── Feature definitions ─────────────────────────────────────────────────

FEATURE_NAMES = [
    "dormancy_score",
    "volume_z_at_trigger",
    "rvol_at_trigger",
    "accum_divergence",
    "bb_width_at_trigger",
    "btc_beta",
    "r_value",
    "setup_score",
    "entry_price_log",
    "hour_of_day",
    "day_of_week",
]


def extract_features(meta: dict) -> np.ndarray:
    """Extract feature vector from enriched metadata."""
    ts = meta["created_ts"]
    return np.array([
        meta.get("dormancy_score", 0),
        meta.get("volume_z_at_trigger", 0),
        meta.get("rvol_at_trigger", 0),
        meta.get("accum_divergence", 0),
        meta.get("bb_width_at_trigger", 0),
        meta.get("btc_beta", 0),
        meta.get("r_value", 0),
        meta.get("setup_score", 0),
        np.log(max(meta.get("entry_price", 1), 1e-8)),
        ts.hour if hasattr(ts, "hour") else 0,
        ts.weekday() if hasattr(ts, "weekday") else 0,
    ], dtype=np.float64)


# ══════════════════════════════════════════════════════════════════════════
#  RANK MODEL — Ridge Regression (numpy-only, no sklearn needed)
# ══════════════════════════════════════════════════════════════════════════

class RidgeRanker:
    """Ridge regression for ranking signals by predicted R-multiple.

    Closed-form solution: w = (X'X + αI)^(-1) X'y
    Appropriate for N=889, p=11 — no need for iterative methods.
    """

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self.weights: np.ndarray | None = None
        self.bias: float = 0.0
        self.means: np.ndarray | None = None
        self.stds: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray):
        """Fit ridge regression. X: (n_samples, n_features), y: (n_samples,)."""
        # Standardize
        self.means = np.mean(X, axis=0)
        self.stds = np.std(X, axis=0) + 1e-8
        X_std = (X - self.means) / self.stds
        self.bias = np.mean(y)
        y_centered = y - self.bias

        # Closed-form ridge
        n_feat = X_std.shape[1]
        XtX = X_std.T @ X_std + self.alpha * np.eye(n_feat)
        Xty = X_std.T @ y_centered
        self.weights = np.linalg.solve(XtX, Xty)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict ranking score (predicted R-multiple)."""
        if self.weights is None:
            return np.zeros(X.shape[0])
        X_std = (X - self.means) / self.stds
        return X_std @ self.weights + self.bias

    def feature_importance(self) -> list[tuple[str, float]]:
        """Return (feature_name, abs_weight) sorted descending."""
        if self.weights is None:
            return []
        pairs = list(zip(FEATURE_NAMES, np.abs(self.weights)))
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs


# ══════════════════════════════════════════════════════════════════════════
#  PORTFOLIO WALK with selection method
# ══════════════════════════════════════════════════════════════════════════

def portfolio_walk_with_selection(
    trades: list[dict],
    max_concurrent: int,
    selection_method: str,
    position_usd: float = SIM_POSITION_USD,
    rng: np.random.Generator | None = None,
) -> dict:
    """Walk portfolio chronologically with capacity overflow handling.

    Within each day, signals are sorted by priority (selection_method)
    before processing. When capacity is full, lower-priority signals skip.

    Methods:
      fifo   — natural chronological order, skip when full
      score  — highest heuristic score gets priority within each day
      ranker — highest predicted R gets priority within each day
      random — shuffled order within each day
    """
    if not trades:
        return {"n_trades": 0, "n_skipped": 0, "total_pnl": 0.0,
                "daily_pf": 0.0, "agg_pf": 0.0, "win_rate": 0.0}

    # Group by day, sort within day by priority
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        entry_ts = t["result"].get("entry_ts") or t["meta"]["created_ts"]
        day = entry_ts.strftime("%Y-%m-%d")
        by_day[day].append(t)

    for day in by_day:
        if selection_method == "score":
            by_day[day].sort(key=lambda t: t["meta"].get("score", 0), reverse=True)
        elif selection_method == "ranker":
            by_day[day].sort(key=lambda t: t.get("predicted_r", 0), reverse=True)
        elif selection_method == "random" and rng is not None:
            rng.shuffle(by_day[day])
        # fifo: keep chronological order (already sorted by entry_ts from DB)

    # Flatten
    ordered = []
    for day in sorted(by_day.keys()):
        ordered.extend(by_day[day])

    # Walk
    open_positions: list[dict] = []
    closed: list[dict] = []
    n_skipped = 0
    equity = 0.0
    daily_pnls: dict[str, list[float]] = defaultdict(list)

    for trade in ordered:
        entry_ts = trade["result"].get("entry_ts") or trade["meta"]["created_ts"]

        # Close expired
        still_open = []
        for pos in open_positions:
            pos_exit_ts = pos["entry_ts"] + timedelta(hours=pos["bars_held"])
            if pos_exit_ts <= entry_ts:
                equity += pos["pnl_net"]
                closed.append(pos)
                exit_day = pos_exit_ts.strftime("%Y-%m-%d")
                daily_pnls[exit_day].append(pos["pnl_net"])
            else:
                still_open.append(pos)
        open_positions = still_open

        # Capacity check
        if len(open_positions) >= max_concurrent:
            n_skipped += 1
            continue

        # Open position
        result = trade["result"]
        open_positions.append({
            "entry_ts": entry_ts,
            "bars_held": result.get("bars_held", 72),
            "pnl_net": result["pnl_net"],
        })

    # Close remaining
    for pos in open_positions:
        equity += pos["pnl_net"]
        closed.append(pos)
        exit_day = (pos["entry_ts"] + timedelta(hours=pos["bars_held"])).strftime("%Y-%m-%d")
        daily_pnls[exit_day].append(pos["pnl_net"])

    # Daily PF
    daily_pfs = []
    for pnl_list in daily_pnls.values():
        gp = sum(p for p in pnl_list if p > 0)
        gl = abs(sum(p for p in pnl_list if p < 0))
        if gl > 0:
            daily_pfs.append(min(gp / gl, 5.0))
        elif gp > 0:
            daily_pfs.append(5.0)

    avg_daily_pf = float(np.mean(daily_pfs)) if daily_pfs else 0.0

    pnls = [c["pnl_net"] for c in closed]
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))

    return {
        "n_trades": len(closed),
        "n_skipped": n_skipped,
        "total_pnl": round(equity, 2),
        "daily_pf": round(avg_daily_pf, 4),
        "agg_pf": round(gross_profit / gross_loss, 4) if gross_loss > 0 else 99.0,
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
    }


# ══════════════════════════════════════════════════════════════════════════
#  WALK-FORWARD VALIDATION of the ranker
# ══════════════════════════════════════════════════════════════════════════

def walk_forward_ranker(
    trades: list[dict],
    max_concurrent_values: list[int],
    n_folds: int = 5,
    embargo_days: int = 1,
    n_random_runs: int = 50,
) -> dict:
    """Expanding-window walk-forward validation of the capacity ranker.

    Fold k: train on periods [0..k-1], test on period k.
    First fold is train-only (no test), giving n_folds - 1 test folds.
    """
    dates = sorted(set(
        (t["result"].get("entry_ts") or t["meta"]["created_ts"]).date()
        for t in trades
    ))
    total_days = len(dates)
    fold_size = total_days // n_folds

    if fold_size < 10:
        return {"error": f"insufficient data: {total_days} days for {n_folds} folds"}

    # Define fold boundaries
    fold_boundaries = []
    for f in range(n_folds):
        start_idx = f * fold_size
        end_idx = min(start_idx + fold_size, total_days)
        if f == n_folds - 1:
            end_idx = total_days
        fold_boundaries.append((dates[start_idx], dates[end_idx - 1]))

    log.info("  Fold boundaries:")
    for f, (s, e) in enumerate(fold_boundaries):
        n_in_fold = sum(1 for t in trades
                        if s <= (t["result"].get("entry_ts") or t["meta"]["created_ts"]).date() <= e)
        log.info("    Fold %d: %s to %s (%d trades)", f, s, e, n_in_fold)

    results = {"folds": [], "aggregate": {}, "feature_importance": []}
    all_importances = []

    # Test folds 1..n_folds-1 (fold 0 is train-only)
    for test_fold in range(1, n_folds):
        test_start, test_end = fold_boundaries[test_fold]
        embargo_date = test_start + timedelta(days=embargo_days)

        # Train: all data before test_start minus embargo
        train_cutoff = test_start - timedelta(days=embargo_days)
        train_trades = [
            t for t in trades
            if (t["result"].get("entry_ts") or t["meta"]["created_ts"]).date() < train_cutoff
        ]
        test_trades = [
            t for t in trades
            if embargo_date <= (t["result"].get("entry_ts") or t["meta"]["created_ts"]).date() <= test_end
        ]

        if len(train_trades) < 30 or len(test_trades) < 20:
            log.warning("  Fold %d: insufficient data (train=%d, test=%d), skipping",
                        test_fold, len(train_trades), len(test_trades))
            continue

        # ── Train ranker ────────────────────────────────────────────
        X_train = np.array([t["features"] for t in train_trades])
        y_train = np.array([t["result"]["r_multiple"] for t in train_trades])

        model = RidgeRanker(alpha=1.0)
        model.fit(X_train, y_train)
        all_importances.append(dict(model.feature_importance()))

        # ── Predict on test ─────────────────────────────────────────
        X_test = np.array([t["features"] for t in test_trades])
        predictions = model.predict(X_test)
        for i, t in enumerate(test_trades):
            t["predicted_r"] = float(predictions[i])

        # Spearman rank correlation on test set
        actual_r = np.array([t["result"]["r_multiple"] for t in test_trades])
        rho, p_val = spearmanr(predictions, actual_r)

        # ── Compare methods at each capacity level ──────────────────
        fold_result = {
            "fold": test_fold,
            "test_start": str(test_start),
            "test_end": str(test_end),
            "n_train": len(train_trades),
            "n_test": len(test_trades),
            "spearman_rho": round(float(rho), 4) if not np.isnan(rho) else 0.0,
            "spearman_p": round(float(p_val), 4) if not np.isnan(p_val) else 1.0,
            "methods": {},
        }

        rng = np.random.default_rng(42 + test_fold)

        for mc in max_concurrent_values:
            method_results = {}

            for method in ["fifo", "score", "ranker"]:
                r = portfolio_walk_with_selection(
                    test_trades, mc, method, position_usd=SIM_POSITION_USD,
                )
                method_results[method] = r

            # Random: average over N runs
            random_pnls = []
            random_trades_counts = []
            random_daily_pfs = []
            for _ in range(n_random_runs):
                r = portfolio_walk_with_selection(
                    test_trades, mc, "random", rng=rng,
                )
                random_pnls.append(r["total_pnl"])
                random_trades_counts.append(r["n_trades"])
                random_daily_pfs.append(r["daily_pf"])

            method_results["random"] = {
                "n_trades": int(np.mean(random_trades_counts)),
                "n_skipped": len(test_trades) - int(np.mean(random_trades_counts)),
                "total_pnl": round(float(np.mean(random_pnls)), 2),
                "daily_pf": round(float(np.mean(random_daily_pfs)), 4),
                "agg_pf": 0.0,  # Not tracked for averaged runs
                "win_rate": 0.0,
                "pnl_std": round(float(np.std(random_pnls)), 2),
                "pnl_p5": round(float(np.percentile(random_pnls, 5)), 2),
                "pnl_p95": round(float(np.percentile(random_pnls, 95)), 2),
            }

            fold_result["methods"][str(mc)] = method_results

        results["folds"].append(fold_result)
        log.info("  Fold %d: rho=%.3f (p=%.3f), train=%d, test=%d",
                 test_fold, fold_result["spearman_rho"], fold_result["spearman_p"],
                 len(train_trades), len(test_trades))

        # Per-fold method comparison at each capacity
        for mc in max_concurrent_values:
            mc_key = str(mc)
            m = fold_result["methods"][mc_key]
            log.info("    max_%s: FIFO=$%.0f, Score=$%.0f, Ranker=$%.0f, Random=$%.0f",
                     mc_key, m["fifo"]["total_pnl"], m["score"]["total_pnl"],
                     m["ranker"]["total_pnl"], m["random"]["total_pnl"])

    # ── Aggregate across folds ──────────────────────────────────────────
    if not results["folds"]:
        results["verdict"] = "INSUFFICIENT_DATA"
        return results

    # Feature importance (averaged)
    if all_importances:
        avg_imp = defaultdict(float)
        for imp_dict in all_importances:
            for feat, val in imp_dict.items():
                avg_imp[feat] += val / len(all_importances)
        results["feature_importance"] = sorted(
            [{"feature": f, "importance": round(v, 4)} for f, v in avg_imp.items()],
            key=lambda x: x["importance"], reverse=True,
        )

    # Aggregate PnL per method × capacity
    for mc in max_concurrent_values:
        mc_key = str(mc)
        agg: dict[str, dict] = {}

        for method in ["fifo", "score", "ranker", "random"]:
            total_pnl = sum(
                f["methods"][mc_key][method]["total_pnl"]
                for f in results["folds"] if mc_key in f["methods"]
            )
            total_trades = sum(
                f["methods"][mc_key][method]["n_trades"]
                for f in results["folds"] if mc_key in f["methods"]
            )
            total_skipped = sum(
                f["methods"][mc_key][method]["n_skipped"]
                for f in results["folds"] if mc_key in f["methods"]
            )
            agg[method] = {
                "total_pnl": round(total_pnl, 2),
                "total_trades": total_trades,
                "total_skipped": total_skipped,
            }

        # Deltas and verdicts
        ranker_pnl = agg["ranker"]["total_pnl"]
        score_pnl = agg["score"]["total_pnl"]
        random_pnl = agg["random"]["total_pnl"]
        fifo_pnl = agg["fifo"]["total_pnl"]

        agg["deltas"] = {
            "ranker_vs_fifo": round(ranker_pnl - fifo_pnl, 2),
            "ranker_vs_score": round(ranker_pnl - score_pnl, 2),
            "ranker_vs_random": round(ranker_pnl - random_pnl, 2),
            "score_vs_random": round(score_pnl - random_pnl, 2),
            "ranker_beats_fifo": ranker_pnl > fifo_pnl,
            "ranker_beats_score": ranker_pnl > score_pnl,
            "ranker_beats_random": ranker_pnl > random_pnl,
            "score_beats_random": score_pnl > random_pnl,
        }

        results["aggregate"][mc_key] = agg

    # ── Overall verdict ─────────────────────────────────────────────────
    # Ranker wins if it beats random at majority of capacity levels
    n_beats_random = sum(
        1 for mc in max_concurrent_values
        if results["aggregate"][str(mc)]["deltas"]["ranker_beats_random"]
    )
    n_beats_score = sum(
        1 for mc in max_concurrent_values
        if results["aggregate"][str(mc)]["deltas"]["ranker_beats_score"]
    )

    ranker_wins = n_beats_random > len(max_concurrent_values) / 2

    results["verdict"] = {
        "ranker_beats_random_at": f"{n_beats_random}/{len(max_concurrent_values)} capacity levels",
        "ranker_beats_score_at": f"{n_beats_score}/{len(max_concurrent_values)} capacity levels",
        "recommendation": "USE_RANKER" if ranker_wins else "USE_RANDOM",
        "explanation": (
            "Ranker adds value — use predicted R-multiple for signal priority"
            if ranker_wins else
            "Ranker cannot beat random — scoring system cannot differentiate "
            "signal quality within Config D. This is the highest-leverage finding: "
            "fix signal ranking before adding new strategies."
        ),
    }

    # Spearman rho summary
    rhos = [f["spearman_rho"] for f in results["folds"]]
    results["spearman_summary"] = {
        "mean_rho": round(float(np.mean(rhos)), 4),
        "rhos_by_fold": rhos,
        "any_significant": any(f["spearman_p"] < 0.05 for f in results["folds"]),
    }

    return results


# ══════════════════════════════════════════════════════════════════════════
#  COHORT ANALYSIS — overlap statistics
# ══════════════════════════════════════════════════════════════════════════

def compute_overlap_stats(trades: list[dict], time_stop_hours: int = 72) -> dict:
    """How often do D1 signals compete for the same capacity slots?

    A signal is 'active' from entry_ts to entry_ts + time_stop_hours.
    Overlap = signals whose active windows intersect.
    """
    if not trades:
        return {}

    # Sort by entry time
    sorted_trades = sorted(
        trades,
        key=lambda t: t["result"].get("entry_ts") or t["meta"]["created_ts"],
    )

    # Walk through, count concurrent at each entry event
    concurrent_at_entry = []
    active: list[datetime] = []  # exit timestamps of active positions

    for trade in sorted_trades:
        entry_ts = trade["result"].get("entry_ts") or trade["meta"]["created_ts"]
        exit_ts = entry_ts + timedelta(hours=time_stop_hours)

        # Remove expired
        active = [a for a in active if a > entry_ts]
        active.append(exit_ts)
        concurrent_at_entry.append(len(active))

    concurrent = np.array(concurrent_at_entry)
    return {
        "n_signals": len(trades),
        "mean_concurrent_at_entry": round(float(np.mean(concurrent)), 2),
        "median_concurrent_at_entry": int(np.median(concurrent)),
        "p75_concurrent": int(np.percentile(concurrent, 75)),
        "p90_concurrent": int(np.percentile(concurrent, 90)),
        "p95_concurrent": int(np.percentile(concurrent, 95)),
        "max_concurrent": int(np.max(concurrent)),
        "pct_entries_above_5": round(float(np.mean(concurrent > 5)) * 100, 1),
        "pct_entries_above_8": round(float(np.mean(concurrent > 8)) * 100, 1),
        "pct_entries_above_15": round(float(np.mean(concurrent > 15)) * 100, 1),
    }


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="D1 Capacity Overflow Ranker")
    parser.add_argument("--batch-id", type=str, required=True,
                        help="Batch ID pattern (SQL LIKE)")
    parser.add_argument("--max-concurrent", type=str, default="3,5,8,15",
                        help="Capacity levels to test (comma-separated)")
    parser.add_argument("--n-folds", type=int, default=5,
                        help="Walk-forward folds (default: 5)")
    parser.add_argument("--n-random-runs", type=int, default=50,
                        help="Random baseline averaging runs (default: 50)")
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--slippage-bps", type=float, default=SIM_SLIPPAGE_BPS)
    args = parser.parse_args()

    # Output setup
    if args.output_dir:
        output_dir = args.output_dir
    elif os.path.isdir("/scripts/output"):
        output_dir = "/scripts/output"
    elif os.path.isdir(os.path.join(os.path.dirname(__file__), "output")):
        output_dir = os.path.join(os.path.dirname(__file__), "output")
    else:
        output_dir = "/scripts/output"
    os.makedirs(output_dir, exist_ok=True)

    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(output_dir, f"d1_capacity_ranker_{ts_tag}.json")
    log_path = os.path.join(output_dir, f"d1_capacity_ranker_{ts_tag}.log")

    file_handler = logging.FileHandler(log_path, mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(file_handler)

    log.info("Output: %s", json_path)
    log.info("Log: %s", log_path)

    max_concurrent_values = [int(x.strip()) for x in args.max_concurrent.split(",")]

    # ── Database connection ─────────────────────────────────────────────
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        os.environ.setdefault(key.strip(), val.strip())
            database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    t0 = time.time()

    # ══════════════════════════════════════════════════════════════════════
    #  LOAD & SIMULATE
    # ══════════════════════════════════════════════════════════════════════
    log.info("=" * 80)
    log.info("  D1 CAPACITY OVERFLOW RANKER")
    log.info("=" * 80)
    log.info("Loading D1 opportunities matching: %s", args.batch_id)

    opps = load_opportunities(database_url, args.batch_id)
    log.info("  Found %d opportunities", len(opps))
    if not opps:
        log.error("No opportunities found")
        sys.exit(1)

    # Parse payloads with full feature extraction
    metas = []
    for opp in opps:
        payload = parse_payload(opp)
        score_components = payload.get("score_components", {})
        metas.append({
            "symbol": opp["symbol"],
            "timeframe": opp["timeframe"],
            "created_ts": opp["created_ts"],
            "direction": payload.get("direction", "long"),
            "entry_price": float(payload.get("entry_price", 0)),
            "dormancy_score": float(payload.get("dormancy_score", 0)),
            "volume_z_at_trigger": float(payload.get("volume_z_at_trigger", 0)),
            "rvol_at_trigger": float(payload.get("rvol_at_trigger", 0)),
            "accum_divergence": float(payload.get("accum_divergence", 0)),
            "bb_width_at_trigger": float(payload.get("bb_width_at_trigger", 0)),
            "btc_beta": float(payload.get("btc_beta", 0)),
            "r_value": float(payload.get("r_value", 0)),
            "setup_score": float(payload.get("setup_score", 0)),
            "score": float(opp.get("score", 0)),
            "idx": len(metas),
        })

    n_symbols = len(set(m["symbol"] for m in metas))
    date_min = min(m["created_ts"] for m in metas)
    date_max = max(m["created_ts"] for m in metas)
    log.info("  Symbols: %d unique, dates: %s to %s", n_symbols, date_min.date(), date_max.date())

    # Load OHLCV
    ohlcv_ranges = compute_ohlcv_ranges(opps, metas)
    log.info("  Loading OHLCV for %d symbol/tf combos...", len(ohlcv_ranges))
    t1 = time.time()
    ohlcv_cache = load_ohlcv_batch(database_url, ohlcv_ranges)
    log.info("  OHLCV loaded in %.1fs (%d combos)", time.time() - t1, len(ohlcv_cache))

    # Simulate Config D
    cfg = EXIT_CONFIGS["D"]
    log.info("  Simulating Config D (1.5%% stop, 10%% TP, 3d TS)...")
    t1 = time.time()

    sim_results = []
    for opp, meta in zip(opps, metas):
        cache_key = f"{meta['symbol']}_{meta['timeframe']}"
        bars = ohlcv_cache.get(cache_key, [])
        tf = meta["timeframe"]
        ts_bars = cfg["ts"].get(tf, 72)

        result = simulate_opportunity(
            opp_ts=meta["created_ts"],
            direction=meta["direction"],
            entry_price=meta["entry_price"],
            bars=bars,
            tf=tf,
            stop_pct=cfg["stop_pct"],
            tp_pct=cfg["tp_pct"],
            time_stop_bars=ts_bars,
            exit_config=cfg["ec"],
            slippage_bps=args.slippage_bps,
        )
        sim_results.append(result)

    log.info("  Simulated %d opportunities in %.1fs", len(sim_results), time.time() - t1)

    # Filter to Config D trades (survived fee/R guard)
    trades = []
    for result, meta in zip(sim_results, metas):
        if not result.get("data_available"):
            continue
        if result.get("pnl_net") is None:
            continue
        if result.get("r_multiple", 0) < -1.05:
            continue

        features = extract_features(meta)
        trades.append({
            "result": result,
            "meta": meta,
            "features": features,
            "predicted_r": 0.0,  # Will be filled by ranker
        })

    log.info("  Config D trades: %d (from %d opps)", len(trades), len(opps))

    # ══════════════════════════════════════════════════════════════════════
    #  OVERLAP ANALYSIS — how often does capacity matter?
    # ══════════════════════════════════════════════════════════════════════
    log.info("\n" + "=" * 80)
    log.info("  OVERLAP ANALYSIS")
    log.info("=" * 80)

    overlap = compute_overlap_stats(trades)
    log.info("  Mean concurrent at entry: %.1f", overlap["mean_concurrent_at_entry"])
    log.info("  Median concurrent at entry: %d", overlap["median_concurrent_at_entry"])
    log.info("  P90 concurrent: %d, P95: %d, Max: %d",
             overlap["p90_concurrent"], overlap["p95_concurrent"], overlap["max_concurrent"])
    log.info("  %% entries with >5 concurrent: %.1f%%", overlap["pct_entries_above_5"])
    log.info("  %% entries with >8 concurrent: %.1f%%", overlap["pct_entries_above_8"])
    log.info("  %% entries with >15 concurrent: %.1f%%", overlap["pct_entries_above_15"])

    # ══════════════════════════════════════════════════════════════════════
    #  FEATURE ANALYSIS — which raw features predict R-multiple?
    # ══════════════════════════════════════════════════════════════════════
    log.info("\n" + "=" * 80)
    log.info("  FEATURE → R-MULTIPLE CORRELATIONS (full sample, for diagnostics only)")
    log.info("=" * 80)

    X_all = np.array([t["features"] for t in trades])
    y_all = np.array([t["result"]["r_multiple"] for t in trades])

    for i, feat_name in enumerate(FEATURE_NAMES):
        rho, p = spearmanr(X_all[:, i], y_all)
        sig = "*" if p < 0.05 else " "
        log.info("  %-25s rho=%+.4f  p=%.4f %s", feat_name, rho, p, sig)

    # Heuristic score correlation
    scores = np.array([t["meta"]["score"] for t in trades])
    rho_score, p_score = spearmanr(scores, y_all)
    log.info("  %-25s rho=%+.4f  p=%.4f %s",
             "heuristic_score (composite)", rho_score, p_score,
             "*" if p_score < 0.05 else " ")

    # ══════════════════════════════════════════════════════════════════════
    #  WALK-FORWARD RANKER VALIDATION
    # ══════════════════════════════════════════════════════════════════════
    log.info("\n" + "=" * 80)
    log.info("  WALK-FORWARD RANKER VALIDATION")
    log.info("  %d-fold expanding window, %d-day embargo, %d random runs",
             args.n_folds, 1, args.n_random_runs)
    log.info("  Capacity levels: %s", max_concurrent_values)
    log.info("=" * 80)

    wf = walk_forward_ranker(
        trades,
        max_concurrent_values=max_concurrent_values,
        n_folds=args.n_folds,
        embargo_days=1,
        n_random_runs=args.n_random_runs,
    )

    # Print results
    if "error" in wf:
        log.error("  Walk-forward failed: %s", wf["error"])
    else:
        log.info("\n  SPEARMAN RANK CORRELATION (predicted vs actual R-multiple):")
        for f in wf["folds"]:
            log.info("    Fold %d: rho=%.3f (p=%.4f) %s",
                     f["fold"], f["spearman_rho"], f["spearman_p"],
                     "significant" if f["spearman_p"] < 0.05 else "")
        summary = wf.get("spearman_summary", {})
        log.info("    Mean rho: %.3f, any significant: %s",
                 summary.get("mean_rho", 0), summary.get("any_significant", False))

        log.info("\n  FEATURE IMPORTANCE (averaged across folds):")
        for fi in wf.get("feature_importance", []):
            log.info("    %-25s %.4f", fi["feature"], fi["importance"])

        log.info("\n  AGGREGATE RESULTS (sum across all test folds):")
        log.info("  %-6s  %10s %10s %10s %10s  | %10s %10s",
                 "Cap", "FIFO", "Score", "Ranker", "Random",
                 "Rnk-Score", "Rnk-Rnd")
        log.info("  " + "-" * 80)

        for mc in max_concurrent_values:
            mc_key = str(mc)
            agg = wf["aggregate"].get(mc_key, {})
            fifo = agg.get("fifo", {}).get("total_pnl", 0)
            score = agg.get("score", {}).get("total_pnl", 0)
            ranker = agg.get("ranker", {}).get("total_pnl", 0)
            random_ = agg.get("random", {}).get("total_pnl", 0)
            deltas = agg.get("deltas", {})
            log.info("  %-6s  $%9.0f $%9.0f $%9.0f $%9.0f  | $%+9.0f $%+9.0f",
                     mc_key, fifo, score, ranker, random_,
                     deltas.get("ranker_vs_score", 0),
                     deltas.get("ranker_vs_random", 0))

        verdict = wf.get("verdict", {})
        log.info("\n  VERDICT: %s", verdict.get("recommendation", "UNKNOWN"))
        log.info("  Ranker beats random at %s", verdict.get("ranker_beats_random_at", "?"))
        log.info("  Ranker beats score at %s", verdict.get("ranker_beats_score_at", "?"))
        log.info("  %s", verdict.get("explanation", ""))

    # ══════════════════════════════════════════════════════════════════════
    #  WRITE OUTPUT
    # ══════════════════════════════════════════════════════════════════════
    output = {
        "batch_id": args.batch_id,
        "n_opportunities": len(opps),
        "n_config_d_trades": len(trades),
        "n_symbols": n_symbols,
        "date_range": [date_min.isoformat(), date_max.isoformat()],
        "overlap_stats": overlap,
        "feature_correlations": {
            feat: {
                "spearman_rho": round(float(spearmanr(X_all[:, i], y_all)[0]), 4),
                "p_value": round(float(spearmanr(X_all[:, i], y_all)[1]), 4),
            }
            for i, feat in enumerate(FEATURE_NAMES)
        },
        "heuristic_score_correlation": {
            "spearman_rho": round(float(rho_score), 4),
            "p_value": round(float(p_score), 4),
        },
        "walk_forward": wf,
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    log.info("\n  JSON: %s", json_path)
    log.info("  Log: %s", log_path)
    log.info("  Elapsed: %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
