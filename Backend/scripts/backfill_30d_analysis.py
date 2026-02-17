#!/usr/bin/env python3
"""
30-Day Backfill Comprehensive Analysis
=======================================

Phase 7 Resumption: With ~4x the data (30 days vs 8 days), this script runs:
1. Setup-level diagnosis with dynamic train/val split
2. Feature extraction + correlation analysis
3. Walk-forward CV with gradient boosted trees (feasible at N>3000)
4. Per-setup models with regime features
5. Deployability assessment

Usage:
    docker compose -f docker-compose.yml run --rm -T worker bash -c \
      "pip install numpy scipy scikit-learn lightgbm --break-system-packages -q && \
       python3 /scripts/backfill_30d_analysis.py --batch-id backfill_20260217_7f49b2d9"
"""

import os
import sys
import json
import time
import math
import argparse
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

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
    PreparedTrade,
)

# ML imports
try:
    import numpy as np
    from scipy import stats as scipy_stats
    HAS_NP = True
except ImportError:
    HAS_NP = False
    logger.error("numpy/scipy required: pip install numpy scipy")

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, log_loss
    HAS_SK = True
except ImportError:
    HAS_SK = False
    logger.warning("scikit-learn not available")

try:
    import lightgbm as lgb
    HAS_LGB = True
except (ImportError, OSError):
    HAS_LGB = False
    logger.info("lightgbm not available, using sklearn GBT only")

UTC = timezone.utc
BASELINE = ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=1.0)
EXCLUDE_PATTERNS = ["mae", "mfe", "exit", "pnl", "r_multiple", "bars_held", "result", "outcome"]


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: DATA LOADING AND PREPARATION
# ═══════════════════════════════════════════════════════════════════════════════

def load_and_prepare(database_url, batch_id, timeframes):
    """Load opportunities, OHLCV, run entry logic, extract features."""
    logger.info("Loading opportunities (batch=%s)...", batch_id)
    opportunities = load_opportunities(database_url, batch_id, timeframes)
    if not opportunities:
        logger.error("No opportunities found")
        sys.exit(1)

    logger.info("Loading OHLCV...")
    ranges = compute_ohlcv_ranges(opportunities)
    ohlcv_cache = batch_load_ohlcv(database_url, ranges)

    logger.info("Running entry logic on %d opportunities...", len(opportunities))
    records = []
    filtered = 0
    for opp in opportunities:
        trade = prepare_trade(opp, ohlcv_cache)
        if trade is None:
            filtered += 1
            continue

        reason, price, bars_held = run_exit_for_trade(trade, BASELINE, "exit")
        pnl_val, r_mult = compute_pnl(trade, reason, price)
        features = extract_features(opp, trade, ohlcv_cache)

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
            "bars_held": bars_held,
        })

    logger.info("%d valid trades (%d filtered, %.1f%%)",
                len(records), filtered,
                filtered / len(opportunities) * 100 if opportunities else 0)
    return records


def extract_features(opp, trade, ohlcv_cache=None):
    """Extract features from opportunity payload + trade + regime context."""
    payload = opp.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if payload is None:
        payload = {}

    f = {}

    # --- Structural features ---
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

    # --- Time features ---
    if trade.created_ts:
        f["hour_utc"] = float(trade.created_ts.hour)
        f["day_of_week"] = float(trade.created_ts.weekday())
        f["is_weekend"] = 1.0 if trade.created_ts.weekday() >= 5 else 0.0
        # Time-of-day buckets (Asian/European/US sessions)
        hour = trade.created_ts.hour
        f["session_asia"] = 1.0 if 0 <= hour < 8 else 0.0
        f["session_europe"] = 1.0 if 8 <= hour < 16 else 0.0
        f["session_us"] = 1.0 if 16 <= hour < 24 else 0.0

    # --- Flatten payload numeric fields ---
    _flatten(payload, "", f)

    return f


def _flatten(obj, prefix, out):
    """Recursively extract numeric values from payload."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: SETUP-LEVEL DIAGNOSIS
# ═══════════════════════════════════════════════════════════════════════════════

def pf(pnls):
    """Profit factor from list of PnL values."""
    if isinstance(pnls, np.ndarray):
        if len(pnls) == 0:
            return 0.0
        gp = float(pnls[pnls > 0].sum())
        gl = float(abs(pnls[pnls <= 0].sum()))
    else:
        if not pnls:
            return 0.0
        gp = sum(p for p in pnls if p > 0)
        gl = abs(sum(p for p in pnls if p <= 0))
    return gp / gl if gl > 0 else (99.99 if gp > 0 else 0.0)


def setup_metrics(records):
    """Compute metrics from a list of records."""
    if not records:
        return {"n": 0, "wins": 0, "wr": 0, "pf": 0, "pnl": 0, "avg_r": 0,
                "exits": {}, "win_pnl": 0, "loss_pnl": 0}
    pnls = [r["pnl"] for r in records]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    exits = defaultdict(int)
    for r in records:
        exits[r["exit_reason"]] += 1
    return {
        "n": n,
        "wins": wins,
        "wr": wins / n if n > 0 else 0,
        "pf": gp / gl if gl > 0 else (99.99 if gp > 0 else 0.0),
        "pnl": sum(pnls),
        "avg_r": sum(r["r_mult"] for r in records) / n if n > 0 else 0,
        "exits": dict(exits),
        "win_pnl": gp,
        "loss_pnl": gl,
    }


def run_diagnosis(records, train_cutoff):
    """Run per-setup diagnosis with train/val split."""
    train = [r for r in records if r["created_ts"] < train_cutoff]
    val = [r for r in records if r["created_ts"] >= train_cutoff]

    dates = sorted(set(r["created_ts"].date() for r in records))
    train_dates = sorted(set(r["created_ts"].date() for r in train))
    val_dates = sorted(set(r["created_ts"].date() for r in val))

    print("\n" + "=" * 90)
    print("SETUP-LEVEL DIAGNOSIS (30-Day Backfill)")
    print(f"Data: {dates[0]} to {dates[-1]} ({len(dates)} active days)")
    print(f"Train: {train_dates[0]} to {train_dates[-1]} ({len(train)} trades, {len(train_dates)} days)")
    print(f"Val:   {val_dates[0]} to {val_dates[-1]} ({len(val)} trades, {len(val_dates)} days)")
    print(f"Config: Baseline (BE=0, ACT=1.0, DIST=1.0, TP1=exit)")
    print("=" * 90)

    setups = sorted(set(r["setup_id"] for r in records))

    # --- Full dataset ---
    print(f"\n{'FULL DATASET':^90}")
    _print_table(records, setups)

    # --- Train ---
    print(f"\n{'TRAINING SET':^90}")
    _print_table(train, setups)

    # --- Validation ---
    print(f"\n{'VALIDATION SET':^90}")
    _print_table(val, setups)

    # --- Direction breakdown ---
    print(f"\n{'DIRECTION BREAKDOWN (Full)':^90}")
    print(f"{'Setup':>6} {'Dir':>6} {'N':>5} {'Wins':>5} {'WR':>6} {'PF':>7} {'PnL':>10} {'AvgR':>8}")
    print("-" * 60)
    by_sd = defaultdict(list)
    for r in records:
        by_sd[(r["setup_id"], r["direction"])].append(r)
    for (sid, d) in sorted(by_sd.keys()):
        m = setup_metrics(by_sd[(sid, d)])
        print(f"{sid:>6} {d:>6} {m['n']:>5} {m['wins']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
              f"{m['pnl']:>+10.2f} {m['avg_r']:>+8.4f}")

    # --- Timeframe breakdown ---
    print(f"\n{'TIMEFRAME BREAKDOWN (Full)':^90}")
    print(f"{'Setup':>6} {'TF':>4} {'N':>5} {'Wins':>5} {'WR':>6} {'PF':>7} {'PnL':>10}")
    print("-" * 50)
    by_stf = defaultdict(list)
    for r in records:
        by_stf[(r["setup_id"], r["timeframe"])].append(r)
    for (sid, tf) in sorted(by_stf.keys()):
        m = setup_metrics(by_stf[(sid, tf)])
        if m["n"] >= 5:
            print(f"{sid:>6} {tf:>4} {m['n']:>5} {m['wins']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
                  f"{m['pnl']:>+10.2f}")

    # --- Return summary for reporting ---
    summary = {}
    for sid in setups:
        full_m = setup_metrics([r for r in records if r["setup_id"] == sid])
        train_m = setup_metrics([r for r in train if r["setup_id"] == sid])
        val_m = setup_metrics([r for r in val if r["setup_id"] == sid])
        summary[sid] = {"full": full_m, "train": train_m, "val": val_m}

    return summary


def _print_table(records, setups):
    """Print per-setup metrics table."""
    hdr = (f"{'Setup':>6} {'N':>5} {'Wins':>5} {'WR':>6} {'PF':>7} {'PnL':>10} {'AvgR':>8}"
           f"  {'FF':>3} {'SL':>3} {'TS':>3} {'TRL':>3} {'TP1':>3} {'TP2':>3}")
    print(hdr)
    print("-" * 80)

    for sid in setups:
        subset = [r for r in records if r["setup_id"] == sid]
        m = setup_metrics(subset)
        e = m["exits"]
        print(f"{sid:>6} {m['n']:>5} {m['wins']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
              f"{m['pnl']:>+10.2f} {m['avg_r']:>+8.4f}"
              f"  {e.get('FAIL_FAST',0):>3} {e.get('STOP_LOSS',0):>3} "
              f"{e.get('TIME_STOP',0):>3} {e.get('TRAIL_STOP',0):>3} "
              f"{e.get('TP1',0):>3} {e.get('TP2',0):>3}")

    print("-" * 80)
    m = setup_metrics(records)
    e = m["exits"]
    print(f"{'ALL':>6} {m['n']:>5} {m['wins']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
          f"{m['pnl']:>+10.2f} {m['avg_r']:>+8.4f}"
          f"  {e.get('FAIL_FAST',0):>3} {e.get('STOP_LOSS',0):>3} "
          f"{e.get('TIME_STOP',0):>3} {e.get('TRAIL_STOP',0):>3} "
          f"{e.get('TP1',0):>3} {e.get('TP2',0):>3}")

    ex_s6 = [r for r in records if r["setup_id"] != "S6"]
    m = setup_metrics(ex_s6)
    e = m["exits"]
    print(f"{'ex-S6':>6} {m['n']:>5} {m['wins']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
          f"{m['pnl']:>+10.2f} {m['avg_r']:>+8.4f}"
          f"  {e.get('FAIL_FAST',0):>3} {e.get('STOP_LOSS',0):>3} "
          f"{e.get('TIME_STOP',0):>3} {e.get('TRAIL_STOP',0):>3} "
          f"{e.get('TP1',0):>3} {e.get('TP2',0):>3}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: CORRELATION ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def run_correlation_analysis(records):
    """Spearman correlations between features and win/loss."""
    if not HAS_NP:
        return []

    all_keys = set()
    for r in records:
        all_keys.update(r["features"].keys())

    results = []
    for key in sorted(all_keys):
        vals, wins = [], []
        for r in records:
            v = r["features"].get(key)
            if v is not None and not math.isnan(v) and not math.isinf(v):
                vals.append(v)
                wins.append(r["winner"])

        if len(vals) < 20 or len(set(vals)) < 3:
            continue

        try:
            rho, p_val = scipy_stats.spearmanr(vals, wins)
        except Exception:
            continue
        if math.isnan(rho):
            continue

        results.append({
            "feature": key,
            "rho": rho,
            "p": p_val,
            "n": len(vals),
            "abs_rho": abs(rho),
        })

    results.sort(key=lambda x: x["abs_rho"], reverse=True)

    # Print top 25
    print("\n" + "=" * 90)
    print("FEATURE CORRELATION ANALYSIS (Spearman rho vs win/loss)")
    print("=" * 90)
    print(f"\n{'#':>3} {'Feature':>35} {'rho':>8} {'p-val':>8} {'N':>6} {'Sig':>4}")
    print("-" * 75)

    for i, cr in enumerate(results[:25]):
        sig = "***" if cr["p"] < 0.001 else ("**" if cr["p"] < 0.01 else ("*" if cr["p"] < 0.05 else ""))
        print(f"{i+1:>3} {cr['feature']:>35} {cr['rho']:>+.5f} {cr['p']:>8.4f} {cr['n']:>6} {sig:>4}")

    sig_n = sum(1 for c in results if c["p"] < 0.05)
    strong_n = sum(1 for c in results if abs(c["rho"]) > 0.08 and c["p"] < 0.05)
    print(f"\n  Total features: {len(results)}")
    print(f"  Significant (p < 0.05): {sig_n}")
    print(f"  Meaningful (|rho| > 0.08, p < 0.05): {strong_n}")

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: WALK-FORWARD ML MODELS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_walk_forward_folds(records, n_folds=5):
    """Generate expanding-window walk-forward folds from the data."""
    dates = sorted(set(r["created_ts"].date() for r in records))
    n_dates = len(dates)

    # Reserve at least 5 days for training and 3 days for validation
    min_train_days = max(5, n_dates // 3)
    val_window_days = max(3, n_dates // (n_folds + 1))

    folds = []
    for i in range(n_folds):
        # Expanding window: train grows, val slides forward
        val_end_idx = n_dates - (n_folds - 1 - i) * val_window_days
        val_start_idx = val_end_idx - val_window_days

        if val_start_idx < min_train_days:
            continue

        train_start = datetime.combine(dates[0], datetime.min.time()).replace(tzinfo=UTC)
        val_start = datetime.combine(dates[val_start_idx], datetime.min.time()).replace(tzinfo=UTC)
        val_end = datetime.combine(dates[min(val_end_idx, n_dates - 1)], datetime.min.time()).replace(tzinfo=UTC) + timedelta(days=1)

        folds.append({
            "name": f"Fold{i+1}",
            "train_start": train_start,
            "val_start": val_start,
            "val_end": val_end,
            "train_days": val_start_idx,
            "val_days": min(val_window_days, n_dates - val_start_idx),
        })

    return folds


def build_feature_matrix(records, feature_keys):
    """Build numpy arrays from records."""
    X = np.zeros((len(records), len(feature_keys)))
    y = np.array([r["winner"] for r in records])
    pnls = np.array([r["pnl"] for r in records])
    r_mults = np.array([r["r_mult"] for r in records])

    for row, r in enumerate(records):
        for col, k in enumerate(feature_keys):
            v = r["features"].get(k)
            X[row, col] = v if v is not None and not math.isnan(v) and not math.isinf(v) else np.nan

    return X, y, pnls, r_mults


def impute_and_scale(X_tr, X_vl):
    """Impute NaN with training median, standardize."""
    medians = np.nanmedian(X_tr, axis=0)
    for col in range(X_tr.shape[1]):
        med = medians[col] if not np.isnan(medians[col]) else 0.0
        X_tr[np.isnan(X_tr[:, col]), col] = med
        X_vl[np.isnan(X_vl[:, col]), col] = med

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_vl_s = scaler.transform(X_vl)
    return X_tr_s, X_vl_s, scaler


def evaluate_model_at_thresholds(scores, pnls, r_mults, y):
    """Evaluate a model's scores at various top-% thresholds."""
    thresholds = [1.0, 0.75, 0.50, 0.40, 0.30, 0.20]
    results = []
    sorted_idx = np.argsort(scores)[::-1]

    for thr in thresholds:
        n_take = max(1, int(len(scores) * thr))
        take_i = sorted_idx[:n_take]
        skip_i = sorted_idx[n_take:]

        take_pnl = pnls[take_i]
        skip_pnl = pnls[skip_i] if len(skip_i) > 0 else np.array([])

        take_pf_val = pf(take_pnl)
        skip_pf_val = pf(skip_pnl) if len(skip_pnl) > 0 else 0.0

        results.append({
            "threshold": thr,
            "take_n": len(take_i),
            "take_wr": float(np.mean(y[take_i])) if len(take_i) > 0 else 0,
            "take_pf": take_pf_val,
            "take_pnl": float(take_pnl.sum()),
            "take_avg_r": float(r_mults[take_i].mean()) if len(take_i) > 0 else 0,
            "skip_n": len(skip_i),
            "skip_pf": skip_pf_val,
            "skip_pnl": float(skip_pnl.sum()) if len(skip_pnl) > 0 else 0,
            "separation": take_pf_val - skip_pf_val,
        })

    return results


def run_walk_forward_models(records, setup_filter=None, label="ALL"):
    """Run walk-forward CV with multiple model types."""
    if not HAS_SK:
        logger.error("scikit-learn required for models")
        return {}

    recs = records if setup_filter is None else [r for r in records if r["setup_id"] == setup_filter]
    if len(recs) < 30:
        logger.info("  %s: %d trades (too few), skipping", label, len(recs))
        return {}

    # Feature keys with >60% coverage
    counts = defaultdict(int)
    for r in recs:
        for k in r["features"]:
            counts[k] += 1
    min_cov = int(len(recs) * 0.6)
    fkeys = sorted(k for k, c in counts.items() if c >= min_cov)

    if len(fkeys) < 3:
        logger.info("  %s: %d features (too few), skipping", label, len(fkeys))
        return {}

    logger.info("  %s: %d trades, %d features", label, len(recs), len(fkeys))

    folds = generate_walk_forward_folds(recs, n_folds=5)
    if not folds:
        logger.info("  %s: insufficient data for walk-forward folds", label)
        return {}

    logger.info("  Generated %d walk-forward folds", len(folds))

    model_types = ["L1_Logistic", "GBT_sklearn"]
    if HAS_LGB:
        model_types.append("LightGBM")

    all_results = {}

    for model_type in model_types:
        fold_results = []

        for fold in folds:
            train_recs = [r for r in recs if fold["train_start"] <= r["created_ts"] < fold["val_start"]]
            val_recs = [r for r in recs if fold["val_start"] <= r["created_ts"] < fold["val_end"]]

            if len(train_recs) < 20 or len(val_recs) < 10:
                continue

            X_tr, y_tr, pnl_tr, r_tr = build_feature_matrix(train_recs, fkeys)
            X_vl, y_vl, pnl_vl, r_vl = build_feature_matrix(val_recs, fkeys)

            X_tr_s, X_vl_s, scaler = impute_and_scale(X_tr.copy(), X_vl.copy())

            if y_tr.sum() == 0 or y_tr.sum() == len(y_tr):
                continue

            try:
                if model_type == "L1_Logistic":
                    model = LogisticRegression(solver='liblinear', C=0.1, l1_ratio=1.0, max_iter=1000)
                    model.fit(X_tr_s, y_tr)
                    scores = model.predict_proba(X_vl_s)[:, 1]
                    # Extract feature importance
                    importances = [(fkeys[i], float(model.coef_[0][i]))
                                   for i in range(len(fkeys)) if abs(model.coef_[0][i]) > 1e-6]

                elif model_type == "GBT_sklearn":
                    model = GradientBoostingClassifier(
                        n_estimators=100, max_depth=3, learning_rate=0.05,
                        min_samples_leaf=max(10, len(train_recs) // 50),
                        subsample=0.8, random_state=42,
                    )
                    # Use raw (non-standardized) for tree models, but impute NaN
                    X_tr_imp = X_tr.copy()
                    X_vl_imp = X_vl.copy()
                    medians = np.nanmedian(X_tr_imp, axis=0)
                    for col in range(X_tr_imp.shape[1]):
                        med = medians[col] if not np.isnan(medians[col]) else 0.0
                        X_tr_imp[np.isnan(X_tr_imp[:, col]), col] = med
                        X_vl_imp[np.isnan(X_vl_imp[:, col]), col] = med
                    model.fit(X_tr_imp, y_tr)
                    scores = model.predict_proba(X_vl_imp)[:, 1]
                    importances = [(fkeys[i], float(model.feature_importances_[i]))
                                   for i in range(len(fkeys)) if model.feature_importances_[i] > 0.01]

                elif model_type == "LightGBM":
                    X_tr_imp = X_tr.copy()
                    X_vl_imp = X_vl.copy()
                    medians = np.nanmedian(X_tr_imp, axis=0)
                    for col in range(X_tr_imp.shape[1]):
                        med = medians[col] if not np.isnan(medians[col]) else 0.0
                        X_tr_imp[np.isnan(X_tr_imp[:, col]), col] = med
                        X_vl_imp[np.isnan(X_vl_imp[:, col]), col] = med

                    dtrain = lgb.Dataset(X_tr_imp, label=y_tr)
                    params = {
                        "objective": "binary", "metric": "binary_logloss",
                        "num_leaves": 15, "learning_rate": 0.05,
                        "min_child_samples": max(10, len(train_recs) // 50),
                        "feature_fraction": 0.7, "bagging_fraction": 0.8,
                        "bagging_freq": 5, "verbose": -1, "seed": 42,
                    }
                    model = lgb.train(params, dtrain, num_boost_round=100)
                    scores = model.predict(X_vl_imp)
                    raw_imp = model.feature_importance(importance_type='gain')
                    importances = [(fkeys[i], float(raw_imp[i]))
                                   for i in range(len(fkeys)) if raw_imp[i] > 0]
                else:
                    continue

            except Exception as e:
                logger.warning("    %s %s: model failed: %s", label, fold["name"], e)
                continue

            importances.sort(key=lambda x: abs(x[1]), reverse=True)

            # Compute AUC
            try:
                auc = roc_auc_score(y_vl, scores)
            except Exception:
                auc = 0.5

            # Threshold analysis
            thresh = evaluate_model_at_thresholds(scores, pnl_vl, r_vl, y_vl)

            fold_results.append({
                "fold": fold["name"],
                "train_n": len(train_recs),
                "val_n": len(val_recs),
                "val_wr": float(np.mean(y_vl)),
                "val_pf": pf(pnl_vl),
                "val_pnl": float(pnl_vl.sum()),
                "auc": auc,
                "importances": importances[:10],
                "thresholds": thresh,
            })

        all_results[model_type] = fold_results

    return all_results


def print_model_results(all_results, label):
    """Print walk-forward model results."""
    if not all_results:
        return

    print(f"\n{'=' * 90}")
    print(f"WALK-FORWARD MODELS: {label}")
    print(f"{'=' * 90}")

    for model_type, folds in all_results.items():
        if not folds:
            print(f"\n  {model_type}: NO VALID FOLDS")
            continue

        print(f"\n  {model_type}")
        print(f"  {'Fold':>6} {'TrN':>5} {'VlN':>5} {'VlWR':>6} {'VlPF':>7} {'AUC':>6}"
              f"  Top features")
        print("  " + "-" * 80)

        for fl in folds:
            top_feat = ", ".join(f"{n}({c:+.3f})" for n, c in fl["importances"][:3])
            print(f"  {fl['fold']:>6} {fl['train_n']:>5} {fl['val_n']:>5} "
                  f"{fl['val_wr']*100:>5.1f}% {fl['val_pf']:>7.3f} {fl['auc']:>6.3f}"
                  f"  {top_feat}")

        # Threshold summary
        print(f"\n  Threshold analysis (mean across {len(folds)} folds):")
        print(f"  {'Take%':>6} {'TakeN':>6} {'TakeWR':>7} {'TakePF':>7} {'TakePnL':>9}"
              f" {'SkipPF':>7} {'Separ':>7} {'Consistent':>10}")
        print("  " + "-" * 75)

        for thr in [1.0, 0.75, 0.50, 0.40, 0.30, 0.20]:
            t_ns, t_wrs, t_pfs, t_pnls, s_pfs, seps = [], [], [], [], [], []
            for fl in folds:
                for tm in fl["thresholds"]:
                    if tm["threshold"] == thr:
                        t_ns.append(tm["take_n"])
                        t_wrs.append(tm["take_wr"])
                        t_pfs.append(tm["take_pf"])
                        t_pnls.append(tm["take_pnl"])
                        s_pfs.append(tm["skip_pf"])
                        seps.append(tm["separation"])

            if not seps:
                continue

            n_pos = sum(1 for s in seps if s > 0)
            consistent = "YES" if n_pos == len(seps) else f"{n_pos}/{len(seps)}"
            avg = lambda xs: sum(xs) / len(xs) if xs else 0

            print(f"  {thr*100:>5.0f}% {avg(t_ns):>6.0f} {avg(t_wrs)*100:>6.1f}% "
                  f"{avg(t_pfs):>7.3f} {avg(t_pnls):>+9.2f} {avg(s_pfs):>7.3f} "
                  f"{avg(seps):>+7.3f} {consistent:>10}")

        # Feature consistency
        feat_counts = defaultdict(list)
        for fl in folds:
            for name, coef in fl["importances"]:
                feat_counts[name].append(coef)

        consistent_feats = [
            (name, coefs) for name, coefs in feat_counts.items()
            if len(coefs) >= max(2, len(folds) - 1)
        ]
        consistent_feats.sort(key=lambda x: abs(sum(x[1]) / len(x[1])), reverse=True)

        if consistent_feats:
            print(f"\n  Consistent features (present in >={max(2, len(folds)-1)} folds):")
            for name, coefs in consistent_feats[:8]:
                mc = sum(coefs) / len(coefs)
                signs = "same_sign" if (all(c > 0 for c in coefs) or all(c < 0 for c in coefs)) else "mixed"
                print(f"    {name:>35}: mean_imp={mc:>+.4f} ({len(coefs)} folds, {signs})")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: REGIME ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def run_regime_analysis(records):
    """Analyze whether PF varies by time-of-day, day-of-week, etc."""
    print(f"\n{'=' * 90}")
    print("REGIME ANALYSIS")
    print(f"{'=' * 90}")

    # By day-of-week
    print(f"\n  BY DAY OF WEEK:")
    print(f"  {'Day':>10} {'N':>5} {'WR':>6} {'PF':>7} {'PnL':>10}")
    print("  " + "-" * 45)
    dow_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    by_dow = defaultdict(list)
    for r in records:
        by_dow[r["created_ts"].weekday()].append(r)
    for dow in sorted(by_dow.keys()):
        m = setup_metrics(by_dow[dow])
        if m["n"] >= 5:
            print(f"  {dow_names[dow]:>10} {m['n']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} {m['pnl']:>+10.2f}")

    # By session
    print(f"\n  BY TRADING SESSION:")
    print(f"  {'Session':>10} {'N':>5} {'WR':>6} {'PF':>7} {'PnL':>10}")
    print("  " + "-" * 45)
    by_session = defaultdict(list)
    for r in records:
        h = r["created_ts"].hour
        if 0 <= h < 8:
            by_session["Asia"].append(r)
        elif 8 <= h < 16:
            by_session["Europe"].append(r)
        else:
            by_session["US"].append(r)
    for sess in ["Asia", "Europe", "US"]:
        m = setup_metrics(by_session[sess])
        if m["n"] >= 5:
            print(f"  {sess:>10} {m['n']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} {m['pnl']:>+10.2f}")

    # By week (regime proxy)
    print(f"\n  BY WEEK (regime proxy):")
    print(f"  {'Week':>12} {'N':>5} {'WR':>6} {'PF':>7} {'PnL':>10}")
    print("  " + "-" * 50)
    by_week = defaultdict(list)
    for r in records:
        week_key = r["created_ts"].isocalendar()[:2]
        by_week[week_key].append(r)
    for wk in sorted(by_week.keys()):
        m = setup_metrics(by_week[wk])
        if m["n"] >= 5:
            print(f"  {wk[0]}-W{wk[1]:02d} {m['n']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} {m['pnl']:>+10.2f}")

    # S6 by week — does it have ANY good regime?
    print(f"\n  S6 BY WEEK:")
    print(f"  {'Week':>12} {'N':>5} {'WR':>6} {'PF':>7} {'PnL':>10}")
    print("  " + "-" * 50)
    s6_by_week = defaultdict(list)
    for r in records:
        if r["setup_id"] == "S6":
            week_key = r["created_ts"].isocalendar()[:2]
            s6_by_week[week_key].append(r)
    for wk in sorted(s6_by_week.keys()):
        m = setup_metrics(s6_by_week[wk])
        if m["n"] >= 5:
            pf_marker = " <-- PROFITABLE" if m["pf"] > 1.0 else ""
            print(f"  {wk[0]}-W{wk[1]:02d} {m['n']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
                  f"{m['pnl']:>+10.2f}{pf_marker}")

    # S7 by week
    print(f"\n  S7 BY WEEK:")
    print(f"  {'Week':>12} {'N':>5} {'WR':>6} {'PF':>7} {'PnL':>10}")
    print("  " + "-" * 50)
    s7_by_week = defaultdict(list)
    for r in records:
        if r["setup_id"] == "S7":
            week_key = r["created_ts"].isocalendar()[:2]
            s7_by_week[week_key].append(r)
    for wk in sorted(s7_by_week.keys()):
        m = setup_metrics(s7_by_week[wk])
        if m["n"] >= 5:
            pf_marker = " <-- PROFITABLE" if m["pf"] > 1.0 else ""
            print(f"  {wk[0]}-W{wk[1]:02d} {m['n']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
                  f"{m['pnl']:>+10.2f}{pf_marker}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: FINAL VERDICT
# ═══════════════════════════════════════════════════════════════════════════════

def print_verdict(diag_summary, corr_results, all_model_results):
    """Print final assessment."""
    print("\n" + "=" * 90)
    print("FINAL VERDICT")
    print("=" * 90)

    # 1. Setup assessment
    print("\n  PER-SETUP ASSESSMENT:")
    for sid in sorted(diag_summary.keys()):
        s = diag_summary[sid]
        full_pf = s["full"]["pf"]
        val_pf = s["val"]["pf"]
        train_pf = s["train"]["pf"]
        n = s["full"]["n"]
        val_n = s["val"]["n"]

        if val_pf > 1.0 and val_n > 50:
            status = "EDGE CONFIRMED"
        elif val_pf > 1.0 and val_n > 20:
            status = "EDGE (small N)"
        elif train_pf > 1.0 and val_pf < 1.0:
            status = "OVERFIT / REGIME-DEP"
        else:
            status = "NO EDGE"

        print(f"    {sid}: full PF={full_pf:.3f} (N={n}), train PF={train_pf:.3f}, "
              f"val PF={val_pf:.3f} (N={val_n}) → {status}")

    # 2. Model assessment
    print("\n  ML MODEL ASSESSMENT:")
    any_deployable = False
    for label, results in all_model_results.items():
        for model_type, folds in results.items():
            if not folds:
                continue

            # Check if any threshold has consistent positive separation with PF > 1.0
            for thr in [0.50, 0.40, 0.30, 0.20]:
                seps, take_pfs = [], []
                for fl in folds:
                    for tm in fl["thresholds"]:
                        if tm["threshold"] == thr:
                            seps.append(tm["separation"])
                            take_pfs.append(tm["take_pf"])

                if not seps:
                    continue

                n_pos = sum(1 for s in seps if s > 0)
                mean_pf = sum(take_pfs) / len(take_pfs)
                mean_sep = sum(seps) / len(seps)

                if n_pos == len(seps) and mean_pf > 1.0:
                    print(f"    {label} / {model_type} @ top {thr*100:.0f}%: "
                          f"PF={mean_pf:.3f}, sep={mean_sep:+.3f} "
                          f"(ALL {len(seps)} folds positive) → DEPLOYABLE SIGNAL")
                    any_deployable = True
                elif n_pos >= len(seps) * 0.6 and mean_pf > 1.0:
                    print(f"    {label} / {model_type} @ top {thr*100:.0f}%: "
                          f"PF={mean_pf:.3f}, sep={mean_sep:+.3f} "
                          f"({n_pos}/{len(seps)} folds positive) → PARTIAL SIGNAL")

    # 3. Feature signal strength
    strong = [c for c in corr_results if abs(c["rho"]) > 0.08 and c["p"] < 0.05]
    print(f"\n  FEATURE LANDSCAPE: {len(strong)} features with |rho|>0.08, p<0.05")
    if strong:
        for f in strong[:5]:
            print(f"    {f['feature']:>35}: rho={f['rho']:+.4f} (p={f['p']:.4f})")

    # 4. Overall verdict
    print(f"\n  {'=' * 70}")

    # Check for any setup with val PF > 1.0 and N > 50
    edge_setups = [sid for sid, s in diag_summary.items()
                   if s["val"]["pf"] > 1.0 and s["val"]["n"] > 50]

    if edge_setups:
        print(f"  VERDICT: DEPLOYABLE SIGNAL FOUND")
        print(f"  Setups with validated edge: {', '.join(edge_setups)}")
        print(f"  Recommend: enable only these setups, disable rest")
    elif any_deployable:
        print(f"  VERDICT: DEPLOYABLE SCORING FUNCTION FOUND")
        print(f"  ML model can separate winners from losers")
        print(f"  Recommend: implement scoring filter, test in paper trading")
    elif any(s["val"]["pf"] > 1.0 for s in diag_summary.values()):
        print(f"  VERDICT: PARTIAL SIGNAL / REGIME-DEPENDENT")
        small_edge = [sid for sid, s in diag_summary.items() if s["val"]["pf"] > 1.0]
        print(f"  Small-N edge candidates: {', '.join(small_edge)}")
        print(f"  Recommend: regime detection, longer evaluation, selective entry")
    else:
        print(f"  VERDICT: NO SIGNAL CONFIRMED")
        print(f"  30 days of data confirms: current setups do not have positive expectancy")
        print(f"  The entry signals themselves need fundamental rework")
        print()
        print(f"  What we know:")
        print(f"    - Exit calibration alone cannot fix negative-expectancy entries (Phase 6)")
        print(f"    - Feature-based scoring cannot rescue entries with no predictive features")
        print(f"    - The signal definitions produce entries that are not better than random")
        print()
        print(f"  What would need to change:")
        print(f"    1. Signal definitions need directional edge (current ones are noise)")
        print(f"    2. Higher timeframes reduce noise but N becomes too small")
        print(f"    3. Regime detection could help IF any setup works in some regime")
        print(f"    4. Different entry criteria entirely (momentum, mean reversion, etc.)")

    print(f"  {'=' * 70}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="30-day backfill comprehensive analysis")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--timeframes", default="15m,1h,4h,1d")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    if not HAS_NP:
        sys.exit(1)

    timeframes = [t.strip() for t in args.timeframes.split(",")]
    t0 = time.time()

    # ── Load and prepare data ────────────────────────────────────────────
    records = load_and_prepare(database_url, args.batch_id, timeframes)
    if not records:
        logger.error("No valid trades")
        sys.exit(1)

    # ── Dynamic train/val split (65% by date) ────────────────────────────
    dates = sorted(set(r["created_ts"].date() for r in records))
    cutoff_idx = int(len(dates) * 0.65)
    cutoff_date = dates[cutoff_idx]
    train_cutoff = datetime.combine(cutoff_date, datetime.min.time()).replace(tzinfo=UTC)
    logger.info("Dynamic train/val cutoff: %s (65%% of %d dates)", cutoff_date, len(dates))

    # ── Step 1: Setup-level diagnosis ────────────────────────────────────
    logger.info("Step 1: Setup-level diagnosis...")
    diag = run_diagnosis(records, train_cutoff)

    # ── Step 2: Correlation analysis ─────────────────────────────────────
    logger.info("Step 2: Correlation analysis...")
    corr = run_correlation_analysis(records)

    # ── Step 3: Regime analysis ──────────────────────────────────────────
    logger.info("Step 3: Regime analysis...")
    run_regime_analysis(records)

    # ── Step 4: Walk-forward ML models ───────────────────────────────────
    logger.info("Step 4: Walk-forward ML models...")
    all_model_results = {}

    # Global model (all setups)
    logger.info("  Running global model (all setups)...")
    global_results = run_walk_forward_models(records, setup_filter=None, label="ALL")
    if global_results:
        print_model_results(global_results, "ALL SETUPS")
        all_model_results["ALL"] = global_results

    # Per-setup models for setups with enough data
    for setup_id in sorted(set(r["setup_id"] for r in records)):
        n = sum(1 for r in records if r["setup_id"] == setup_id)
        if n >= 50:
            logger.info("  Running per-setup model: %s (N=%d)...", setup_id, n)
            results = run_walk_forward_models(records, setup_filter=setup_id, label=setup_id)
            if results:
                print_model_results(results, setup_id)
                all_model_results[setup_id] = results

    # ── Step 5: Verdict ──────────────────────────────────────────────────
    print_verdict(diag, corr, all_model_results)

    logger.info("Total analysis time: %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
