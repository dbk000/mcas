#!/usr/bin/env python3
"""
Phase 2: Targeted Exit Sweep
================================

Fixed signal layer from v3 (rolling 90-day window, 8-feature cap, P75 threshold).
Sweeps a targeted 2x3x2 factorial of exit parameters:

  Stop ATR mult: [2.0, 3.0]           -- tighter than v3's 4.0
  Trail activation: [1.0, 1.5, 2.0] ATR above entry -- reachable in 48 bars
  Take profit: [2.0, 3.0] ATR above entry            -- capture gains

= 12 configs. All with vol_filter ON, fail_fast OFF, 48-bar time stop.

Usage:
    tmux new -s phase2
    cd /srv/mcas/Backend && docker compose run --rm -T worker \
        python3 /scripts/ignition_phase1.py --days 365
"""

import os
import sys
import time
import json
import math
import argparse
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ---- Path setup ----
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/exit_checker.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))

for _k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(_k, '8')

import numpy as np
from exit_checker import ExitConfig

sys.path.insert(0, os.path.dirname(__file__))
import signal_genesis_ignition as ign

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("phase2")

# ---- Model selection constraints (v3, fixed) ----
MAX_MODEL_FEATURES = 8
TRAIN_DAYS = 90
DISCOVERY_ROUNDS = 3

# ---- Exit Sweep: 2x3x2 factorial ----
CONFIGS = {}
for _stop_atr in [2.0, 3.0]:
    for _trail_atr in [1.0, 1.5, 2.0]:
        for _tp_atr in [2.0, 3.0]:
            _name = f"S{_stop_atr:.0f}_T{_trail_atr:.1f}_P{_tp_atr:.0f}"
            CONFIGS[_name] = {
                "exit_type": "atr",
                "stop_atr_mult": _stop_atr,
                "trail_act_atr": _trail_atr,       # trail activation in ATR above entry
                "trail_dist_atr": 0.5,              # trail distance: 0.5 ATR
                "tp_atr": _tp_atr,                  # take profit in ATR above entry
                "time_stop_bars": 48,
                "fail_fast": False,
                "vol_filter": True,
            }

N_FOLDS = 5
HOLDOUT_DAYS = 30
EMBARGO_DAYS = 2


# =====================================================================
# ROLLING WINDOW FOLD BUILDER (from v3)
# =====================================================================

def build_rolling_folds(ts_grid, n_folds, train_days, holdout_days, embargo_days):
    """Build ROLLING (fixed-size) training window folds."""
    bars_per_day = 24
    n_bars = len(ts_grid)
    day_start = ts_grid[0] // 86400

    total_days = int((ts_grid[-1] // 86400) - day_start) + 1

    logger.info("  Total span: %d days (%s to %s)",
                total_days,
                datetime.fromtimestamp(ts_grid[0], tz=timezone.utc).strftime("%Y-%m-%d"),
                datetime.fromtimestamp(ts_grid[-1], tz=timezone.utc).strftime("%Y-%m-%d"))

    holdout_start_day = total_days - holdout_days
    available_days = holdout_start_day
    earliest_test_day = train_days + embargo_days
    test_space_days = available_days - earliest_test_day

    if test_space_days < n_folds * 5:
        logger.error("  Insufficient data for %d folds with %d-day training window",
                      n_folds, train_days)
        return [], np.zeros(n_bars, dtype=bool)

    test_size_days = test_space_days // n_folds

    folds = []
    for fold_idx in range(n_folds):
        test_start_day = earliest_test_day + fold_idx * test_size_days
        test_end_day = test_start_day + test_size_days
        if fold_idx == n_folds - 1:
            test_end_day = available_days

        train_end_day = test_start_day - embargo_days
        train_start_day = train_end_day - train_days
        if train_start_day < 0:
            train_start_day = 0

        train_mask = np.zeros(n_bars, dtype=bool)
        test_mask = np.zeros(n_bars, dtype=bool)

        for i, ts in enumerate(ts_grid):
            day = int(ts // 86400 - day_start)
            if train_start_day <= day < train_end_day:
                train_mask[i] = True
            elif test_start_day <= day < test_end_day:
                test_mask[i] = True

        train_bars = train_mask.sum()
        test_bars = test_mask.sum()

        if train_bars < 100 or test_bars < 48:
            continue

        logger.info("    Fold %d: train [%d, %d) = %d bars, test [%d, %d) = %d bars",
                     fold_idx, train_start_day, train_end_day, train_bars,
                     test_start_day, test_end_day, test_bars)
        folds.append((train_mask, test_mask))

    holdout_mask = np.zeros(n_bars, dtype=bool)
    for i, ts in enumerate(ts_grid):
        day = int(ts // 86400 - day_start)
        if holdout_start_day <= day < total_days:
            holdout_mask[i] = True

    logger.info("    Holdout: days [%d, %d) = %d bars", holdout_start_day, total_days, holdout_mask.sum())

    return folds, holdout_mask


# =====================================================================
# VOLUME FILTER
# =====================================================================

def compute_volume_filter(volume, close, symbols, percentile=50):
    """Compute set of symbols above volume percentile by avg hourly USD volume."""
    n_symbols = volume.shape[1]
    avg_vol_usd = np.zeros(n_symbols)
    for s in range(n_symbols):
        avg_vol_usd[s] = np.nanmean(volume[:, s] * close[:, s])

    valid = avg_vol_usd[~np.isnan(avg_vol_usd) & (avg_vol_usd > 0)]
    if len(valid) < 4:
        return set(symbols), avg_vol_usd

    threshold = np.percentile(valid, percentile)
    filtered = set()
    for s in range(n_symbols):
        if avg_vol_usd[s] >= threshold:
            filtered.add(symbols[s])

    logger.info("  Volume filter: keeping %d/%d symbols (>= $%.0f/hr)",
                len(filtered), n_symbols, threshold)
    return filtered, avg_vol_usd


# =====================================================================
# SIMULATION WITH TRAIL + TP
# =====================================================================

def simulate_corrected(signals, close, high, low, open_, volume, ts_grid,
                       symbols, cfg, vol_filter_set=None, slippage_mult=1.0):
    """Simulate trades with ATR exits, trailing stop, and take profit."""
    n_bars = close.shape[0]
    sym_to_idx = {s: i for i, s in enumerate(symbols)}

    # Per-config exit params
    stop_atr_mult = cfg["stop_atr_mult"]
    trail_act_atr = cfg.get("trail_act_atr", 0)
    trail_dist_atr = cfg.get("trail_dist_atr", 0.5)
    tp_atr_val = cfg.get("tp_atr", 0)
    time_stop_bars = cfg["time_stop_bars"]

    # ExitConfig: trail params as r_value ratios
    ec = ExitConfig()
    ec.fail_fast_threshold = 1.0  # disabled (all configs have fail_fast=False)

    if trail_act_atr > 0:
        ec.trail_activation_r = trail_act_atr / stop_atr_mult
        ec.trail_distance_r = trail_dist_atr / stop_atr_mult
        ec.trail_breakeven_r = 0.5 * trail_act_atr / stop_atr_mult
    else:
        ec.trail_activation_r = 999.0
        ec.trail_distance_r = 999.0
        ec.trail_breakeven_r = 0.0

    sorted_signals = sorted(signals, key=lambda s: (s.ts_idx, -s.score))
    open_positions = {}
    results = []

    for sig in sorted_signals:
        if vol_filter_set is not None and sig.symbol not in vol_filter_set:
            continue

        s_idx = sym_to_idx.get(sig.symbol, -1)
        if s_idx < 0:
            continue
        i = sig.ts_idx
        if i + 1 >= n_bars or i < 14:
            continue
        if sig.symbol in open_positions and open_positions[sig.symbol] > i:
            continue

        entry_raw = open_[i + 1, s_idx]
        if np.isnan(entry_raw) or entry_raw <= 0:
            continue

        # Slippage
        vol_window = min(24, i)
        avg_vol = np.nanmean(volume[max(0, i - vol_window):i + 1, s_idx])
        avg_price = close[i, s_idx]
        vol_usd = avg_vol * avg_price if not np.isnan(avg_vol) else 0
        slip_bps = ign._dynamic_slippage(vol_usd) * slippage_mult
        entry = entry_raw * (1 + slip_bps / 10000)

        # ATR from recent bars (avoids NaN contamination from cumsum)
        atr_start = max(0, i - 27)
        h_slice = high[atr_start:i + 1, s_idx]
        l_slice = low[atr_start:i + 1, s_idx]
        c_slice = close[atr_start:i + 1, s_idx]
        valid_mask = ~(np.isnan(h_slice) | np.isnan(l_slice) | np.isnan(c_slice))
        if valid_mask.sum() < 14:
            continue
        h_v, l_v, c_v = h_slice[valid_mask], l_slice[valid_mask], c_slice[valid_mask]
        atr_arr = ign._rolling_atr_1d(h_v, l_v, c_v, 14)
        atr = atr_arr[-1]
        if atr <= 0 or np.isnan(atr):
            continue

        # Stop, R-value, TP
        stop = entry - stop_atr_mult * atr
        r_value = stop_atr_mult * atr
        tp1_price = entry + tp_atr_val * atr if tp_atr_val > 0 else None

        if stop <= 0 or r_value <= 0:
            continue
        if r_value / entry > 0.20:
            continue
        if 2 * entry * ign.FEE_BPS / 10000 > 0.3 * r_value:
            continue

        max_idx = min(i + 2 + time_stop_bars + 20, n_bars)
        hwm = None
        half_r_touched = False
        bars_held = 0
        exit_reason = "TIME_STOP"
        exit_price = close[min(max_idx - 1, n_bars - 1), s_idx]

        for j in range(i + 2, max_idx):
            bars_held += 1
            bh, bl, bc, bo = high[j, s_idx], low[j, s_idx], close[j, s_idx], open_[j, s_idx]
            if any(np.isnan(x) for x in [bh, bl, bc, bo]):
                continue
            bar = {"open": bo, "high": bh, "low": bl, "close": bc}
            result, new_hwm, new_half_r = ign.check_exit_simple(
                side="long", bar=bar, entry_price=entry, stop_price=stop,
                tp1_price=tp1_price, tp2_price=None, r_value=r_value,
                bars_held=bars_held, max_bars=time_stop_bars,
                high_water_mark=hwm, half_r_touched=half_r_touched, config=ec)
            hwm = new_hwm
            half_r_touched = new_half_r
            if result:
                exit_reason, exit_price = result
                break

        exit_bar = i + 1 + bars_held
        open_positions[sig.symbol] = exit_bar

        ep = exit_price * (1 - slip_bps / 10000) if exit_reason != "STOP_LOSS" else exit_price
        pos_size = ign.POSITION_SIZE_USD / entry
        pnl = (ep - entry) * pos_size - (entry + ep) * pos_size * ign.FEE_BPS / 10000
        r_mult = pnl / (r_value * pos_size) if r_value * pos_size > 0 else 0

        results.append(ign.TradeResult(
            symbol=sig.symbol, direction="long", ts_idx=sig.ts_idx,
            entry_price=entry, exit_price=ep, pnl=pnl, r_mult=r_mult,
            exit_reason=exit_reason, bars_held=bars_held))

    return results


# =====================================================================
# FOLD PROCESSING (signal pipeline from v3 + exit sweep)
# =====================================================================

def process_fold(fold_idx, train_mask, test_mask, close, open_, high, low, volume,
                 ts_grid, symbols, mfh, configs, vol_filter_set,
                 max_features=MAX_MODEL_FEATURES, max_rounds=DISCOVERY_ROUNDS):
    """Process one fold: build model once, sweep all exit configs."""
    t0 = time.time()
    n_bars, n_symbols = close.shape

    # 1. Event catalog on training
    train_mfh = np.full_like(mfh, np.nan)
    train_mfh[train_mask] = mfh[train_mask]
    events, event_mask = ign.build_event_catalog(train_mfh, ign.IGNITION_THRESHOLD)

    if len(events) < 20:
        logger.warning("  Fold %d: only %d events, skipping", fold_idx, len(events))
        return {name: None for name in configs}

    # 2. Control group
    controls = ign.build_control_group(
        event_mask, n_bars, n_symbols, events,
        controls_per_event=ign.CONTROLS_PER_EVENT, min_sep=ign.CONTROL_MIN_SEP)

    # 3. Features + discovery (serial)
    features = ign.generate_features_round1(close, open_, high, low, volume, parallel=False)
    survivors, all_features = ign.run_discovery_loop(
        features, events, controls, close, open_, high, low, volume, max_rounds=max_rounds)

    if not survivors:
        logger.warning("  Fold %d: no survivors", fold_idx)
        return {name: None for name in configs}

    # Cap features (v3 Fix #2)
    original_count = len(survivors)
    survivors = survivors[:max_features]
    logger.info("  Features: %d survivors -> capped to %d", original_count, len(survivors))

    # 4. Build model
    model = ign.build_conditional_model(survivors, all_features, events, controls)

    # P75 threshold (v3 Fix #3)
    original_threshold = model["threshold"]
    event_scores = _score_events(model, all_features, events)
    if len(event_scores) > 0:
        model["threshold"] = float(np.percentile(event_scores, 75))
    logger.info("  Threshold: optimized=%.2f -> P75 fixed=%.2f",
                original_threshold, model["threshold"])

    # 5. Signals on test
    signals = ign.generate_signals(model, all_features, test_mask, symbols)

    model_time = time.time() - t0
    logger.info("  Fold %d: %d events, %d features, threshold=%.2f, "
                "%d signals (%.1fs)",
                fold_idx, len(events), len(model["features"]),
                model["threshold"], len(signals), model_time)

    if not signals:
        logger.warning("  Fold %d: no signals", fold_idx)
        return {name: None for name in configs}

    # Pre-compute precision (same for all configs with same vol_filter)
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    filtered_sigs = [s for s in signals if vol_filter_set is None or s.symbol in vol_filter_set]
    hits = 0
    for sig in filtered_sigs:
        s_idx = sym_to_idx.get(sig.symbol, -1)
        if s_idx >= 0 and sig.ts_idx < mfh.shape[0]:
            v = mfh[sig.ts_idx, s_idx]
            if not np.isnan(v) and v >= ign.IGNITION_THRESHOLD:
                hits += 1
    precision = hits / len(filtered_sigs) if filtered_sigs else 0

    # 6. Simulate each exit config
    results = {}
    for cfg_name, cfg in configs.items():
        vf = vol_filter_set if cfg["vol_filter"] else None
        trades = simulate_corrected(
            signals, close, high, low, open_, volume, ts_grid, symbols, cfg, vf)

        robust_pf = ign.compute_robust_daily_pf(trades, ts_grid)
        total_pnl = sum(t.pnl for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        win_rate = wins / len(trades) if trades else 0
        gw = sum(t.pnl for t in trades if t.pnl > 0)
        gl = sum(abs(t.pnl) for t in trades if t.pnl < 0)
        agg_pf = gw / gl if gl > 0 else (5.0 if gw > 0 else 0)

        exit_counts = defaultdict(int)
        for t in trades:
            exit_counts[t.exit_reason] += 1

        results[cfg_name] = {
            "fold_idx": fold_idx,
            "n_trades": len(trades),
            "robust_daily_pf": round(robust_pf, 4),
            "aggregate_pf": round(agg_pf, 4),
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "precision": round(precision, 4),
            "n_signals": len(filtered_sigs),
            "exit_causes": dict(exit_counts),
        }

        # Compact exit summary
        n_t = len(trades) or 1
        exit_pcts = {k: f"{v / n_t * 100:.0f}%" for k, v in sorted(exit_counts.items())}
        logger.info("    [%-12s] %d trades, agg=%.3f, robust=%.3f, WR=%.0f%%, "
                     "PnL=$%.0f, exits=%s",
                     cfg_name, len(trades), agg_pf, robust_pf,
                     win_rate * 100, total_pnl, exit_pcts)

    return results


def _score_events(model, all_features, events, n_bins=5):
    """Score training events to compute threshold statistics."""
    if not events or not model["features"]:
        return np.array([])

    all_points = np.array(list(events), dtype=np.int64)
    point_scores = np.zeros(len(all_points), dtype=np.float64)
    point_valid = np.ones(len(all_points), dtype=bool)

    for fname in model["features"]:
        if fname not in all_features:
            point_valid[:] = False
            break
        farr = all_features[fname]
        edges = np.array(model["bin_edges"][fname])
        log_lifts_arr = np.array(model["log_lifts"][fname])

        vals = farr[all_points[:, 0], all_points[:, 1]]
        nan_m = np.isnan(vals)
        point_valid &= ~nan_m

        bins = np.digitize(vals, edges) - 1
        bins = np.clip(bins, 0, n_bins - 1)
        bins[nan_m] = 0
        point_scores += log_lifts_arr[bins]

    return point_scores[point_valid]


# =====================================================================
# MAIN
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Phase 2: Targeted Exit Sweep")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    parser.add_argument("--train-days", type=int, default=TRAIN_DAYS)
    parser.add_argument("--max-features", type=int, default=MAX_MODEL_FEATURES)
    parser.add_argument("--quick", action="store_true", help="3 folds, 2 discovery rounds")
    args = parser.parse_args()

    t_start = time.time()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    output_dir = os.path.join(os.path.dirname(__file__), "ignition_phase1")
    os.makedirs(output_dir, exist_ok=True)
    results_path = os.path.join(output_dir, f"phase2_exits_{args.days}d.json")
    jsonl_path = os.path.join(output_dir, f"phase2_exits_{args.days}d_incremental.jsonl")

    # Clear incremental file
    if os.path.exists(jsonl_path):
        os.remove(jsonl_path)

    n_folds = 3 if args.quick else N_FOLDS
    max_rounds = 2 if args.quick else DISCOVERY_ROUNDS

    logger.info("=" * 70)
    logger.info("PHASE 2: TARGETED EXIT SWEEP")
    logger.info("=" * 70)
    logger.info("  Signal layer: v3 (rolling %d-day, %d features, P75 threshold)",
                args.train_days, args.max_features)
    logger.info("  Exit factorial: 2 stops x 3 trails x 2 TPs = %d configs", len(CONFIGS))
    logger.info("  Folds=%d, holdout=%d, discovery_rounds=%d",
                n_folds, args.holdout_days, max_rounds)
    logger.info("")
    logger.info("  Exit configs:")
    for name, cfg in sorted(CONFIGS.items()):
        logger.info("    %-12s  stop=%s*ATR  trail=%s*ATR  TP=%s*ATR",
                     name, cfg["stop_atr_mult"], cfg["trail_act_atr"], cfg["tp_atr"])

    # ---- Data Loading ----
    logger.info("\n" + "-" * 50)
    logger.info("LOADING DATA")
    logger.info("-" * 50)

    close, open_, high, low, volume, ts_grid, symbols, btc_idx, ohlcv_data = \
        ign.load_ohlcv_matrices(database_url, args.days)

    n_bars, n_symbols = close.shape
    logger.info("  Data: %d bars x %d symbols, %s to %s", n_bars, n_symbols,
                datetime.fromtimestamp(ts_grid[0], tz=timezone.utc).strftime("%Y-%m-%d"),
                datetime.fromtimestamp(ts_grid[-1], tz=timezone.utc).strftime("%Y-%m-%d"))

    # ---- Precompute ----
    logger.info("\n" + "-" * 50)
    logger.info("PRECOMPUTE")
    logger.info("-" * 50)

    mfh = ign.compute_max_forward_high(close, high, ign.HORIZON)
    events_all, event_mask_all = ign.build_event_catalog(mfh, ign.IGNITION_THRESHOLD)
    logger.info("  Total events: %d", len(events_all))

    vol_filter_set, avg_vol_usd = compute_volume_filter(volume, close, symbols, percentile=50)

    # ---- Build Rolling Folds ----
    logger.info("\n" + "-" * 50)
    logger.info("WALK-FORWARD (%d folds, %d-day rolling window)", n_folds, args.train_days)
    logger.info("-" * 50)

    folds, holdout_mask = build_rolling_folds(
        ts_grid, n_folds=n_folds, train_days=args.train_days,
        holdout_days=args.holdout_days, embargo_days=EMBARGO_DAYS)

    if not folds:
        logger.error("No folds constructed")
        sys.exit(1)

    logger.info("  %d usable folds, holdout=%d bars", len(folds), holdout_mask.sum())

    # ---- Process Folds ----
    all_fold_results = []

    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        logger.info("\n  === FOLD %d/%d ===", fold_idx + 1, len(folds))
        fold_result = process_fold(
            fold_idx, train_mask, test_mask,
            close, open_, high, low, volume, ts_grid, symbols,
            mfh, CONFIGS, vol_filter_set,
            max_features=args.max_features, max_rounds=max_rounds)
        all_fold_results.append(fold_result)

        # Incremental write
        with open(jsonl_path, "a") as f:
            f.write(json.dumps({
                "type": "fold_complete",
                "fold_idx": fold_idx,
                "results": fold_result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, default=str) + "\n")

    # ---- Aggregate Walk-Forward Results ----
    logger.info("\n" + "-" * 50)
    logger.info("WALK-FORWARD SUMMARY")
    logger.info("-" * 50)

    wf_summary = {}
    for cfg_name in CONFIGS:
        fold_robust_pfs = []
        fold_agg_pfs = []
        total_trades = 0
        total_pnl = 0.0
        total_signals = 0
        precisions = []
        agg_exit_counts = defaultdict(int)

        for fr in all_fold_results:
            r = fr.get(cfg_name)
            if r is None:
                continue
            fold_robust_pfs.append(r["robust_daily_pf"])
            fold_agg_pfs.append(r["aggregate_pf"])
            total_trades += r["n_trades"]
            total_pnl += r["total_pnl"]
            total_signals += r["n_signals"]
            precisions.append(r["precision"])
            for reason, cnt in r.get("exit_causes", {}).items():
                agg_exit_counts[reason] += cnt

        if not fold_robust_pfs:
            wf_summary[cfg_name] = {"status": "NO_DATA"}
            continue

        avg_robust_pf = float(np.mean(fold_robust_pfs))
        avg_agg_pf = float(np.mean(fold_agg_pfs))
        consistency_robust = sum(1 for pf in fold_robust_pfs if pf > 1.0) / len(fold_robust_pfs)
        consistency_agg = sum(1 for pf in fold_agg_pfs if pf > 1.0) / len(fold_agg_pfs)
        avg_precision = float(np.mean(precisions)) if precisions else 0

        # Exit distribution percentages
        exit_pct = {}
        if total_trades > 0:
            for reason, cnt in agg_exit_counts.items():
                exit_pct[reason] = round(cnt / total_trades * 100, 1)

        wf_summary[cfg_name] = {
            "avg_robust_pf": round(avg_robust_pf, 4),
            "avg_agg_pf": round(avg_agg_pf, 4),
            "fold_robust_pfs": [round(p, 4) for p in fold_robust_pfs],
            "fold_agg_pfs": [round(p, 4) for p in fold_agg_pfs],
            "consistency_robust": round(consistency_robust, 4),
            "consistency_agg": round(consistency_agg, 4),
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "avg_precision": round(avg_precision, 4),
            "total_signals": total_signals,
            "exit_distribution_pct": exit_pct,
            "n_folds": len(fold_robust_pfs),
        }

        pass_wf = (avg_agg_pf > 1.0 and consistency_agg >= 0.60 and total_trades >= 25)
        status = "PASS" if pass_wf else "FAIL"
        wf_summary[cfg_name]["status"] = status

    # Sort by avg aggregate PF (descending)
    sorted_configs = sorted(
        [(name, ws) for name, ws in wf_summary.items() if ws.get("status") != "NO_DATA"],
        key=lambda x: x[1].get("avg_agg_pf", 0),
        reverse=True)

    logger.info("")
    logger.info("  %-4s %-12s %6s  %6s  %-28s  %5s  %8s  %s",
                "Rank", "Config", "AvgAgg", "AvgRob", "Fold Agg PFs", "Trd", "PnL", "Exits")
    logger.info("  " + "-" * 105)

    for rank, (name, ws) in enumerate(sorted_configs, 1):
        fold_str = ",".join(f"{p:.2f}" for p in ws["fold_agg_pfs"])
        exit_str = " ".join(f"{k}:{v:.0f}%" for k, v in
                            sorted(ws.get("exit_distribution_pct", {}).items()))
        status_mark = "*" if ws["status"] == "PASS" else " "
        logger.info("  %s%-3d %-12s %6.3f  %6.3f  [%-26s]  %5d  %+8.0f  %s",
                     status_mark, rank, name,
                     ws["avg_agg_pf"], ws["avg_robust_pf"],
                     fold_str, ws["total_trades"],
                     ws["total_pnl"], exit_str)

    # ---- Holdout (if best config passes WF) ----
    holdout_result = None
    best_passing = None
    for name, ws in sorted_configs:
        if ws["status"] == "PASS":
            best_passing = name
            break

    if best_passing:
        logger.info("\n" + "-" * 50)
        logger.info("HOLDOUT (1.5x slippage) for %s", best_passing)
        logger.info("-" * 50)

        ho_start_idx = np.where(holdout_mask)[0]
        if len(ho_start_idx) > 0:
            ho_bar = ho_start_idx[0]
            embargo_bars = EMBARGO_DAYS * 24
            train_bars = args.train_days * 24
            train_end = ho_bar - embargo_bars
            train_start = max(0, train_end - train_bars)

            full_train_mask = np.zeros(len(ts_grid), dtype=bool)
            full_train_mask[train_start:train_end] = True

            train_mfh = np.full_like(mfh, np.nan)
            train_mfh[full_train_mask] = mfh[full_train_mask]
            events, event_mask = ign.build_event_catalog(train_mfh, ign.IGNITION_THRESHOLD)
            logger.info("  Training: bars [%d, %d), %d events", train_start, train_end, len(events))

            if len(events) >= 20:
                controls = ign.build_control_group(
                    event_mask, n_bars, n_symbols, events,
                    controls_per_event=ign.CONTROLS_PER_EVENT, min_sep=ign.CONTROL_MIN_SEP)
                features = ign.generate_features_round1(close, open_, high, low, volume, parallel=False)
                survivors, all_features = ign.run_discovery_loop(
                    features, events, controls, close, open_, high, low, volume,
                    max_rounds=max_rounds)

                if survivors:
                    survivors = survivors[:args.max_features]
                    model = ign.build_conditional_model(survivors, all_features, events, controls)

                    event_scores = _score_events(model, all_features, events)
                    if len(event_scores) > 0:
                        model["threshold"] = float(np.percentile(event_scores, 75))

                    signals = ign.generate_signals(model, all_features, holdout_mask, symbols)
                    logger.info("  Holdout signals: %d (threshold=%.2f)",
                                len(signals), model["threshold"])

                    if signals:
                        cfg_best = CONFIGS[best_passing]
                        trades = simulate_corrected(
                            signals, close, high, low, open_, volume, ts_grid, symbols,
                            cfg_best, vol_filter_set, slippage_mult=1.5)

                        robust_pf = ign.compute_robust_daily_pf(trades, ts_grid)
                        total_pnl = sum(t.pnl for t in trades)
                        wins = sum(1 for t in trades if t.pnl > 0)
                        win_rate = wins / len(trades) if trades else 0
                        gw = sum(t.pnl for t in trades if t.pnl > 0)
                        gl = sum(abs(t.pnl) for t in trades if t.pnl < 0)
                        agg_pf = gw / gl if gl > 0 else (5.0 if gw > 0 else 0)

                        exit_counts = defaultdict(int)
                        for t in trades:
                            exit_counts[t.exit_reason] += 1

                        pnl_arr = np.array([t.pnl for t in trades])
                        n_obs = len(pnl_arr)
                        mean_pnl = np.mean(pnl_arr)
                        std_pnl = np.std(pnl_arr, ddof=1) if n_obs > 1 else 1e-10
                        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0
                        n_trials = len(CONFIGS)  # 12 exit configs tested
                        dsr = ign.deflated_sharpe_ratio(sharpe, n_trials, n_obs)

                        holdout_result = {
                            "config": best_passing,
                            "n_trades": len(trades),
                            "robust_daily_pf": round(robust_pf, 4),
                            "aggregate_pf": round(agg_pf, 4),
                            "win_rate": round(win_rate, 4),
                            "total_pnl": round(total_pnl, 2),
                            "exit_causes": dict(exit_counts),
                            "sharpe": round(sharpe, 4),
                            "dsr": round(dsr, 4),
                            "n_trials": n_trials,
                            "slippage_mult": 1.5,
                        }

                        n_t = len(trades) or 1
                        exit_str = " ".join(f"{k}:{v / n_t * 100:.0f}%"
                                            for k, v in sorted(exit_counts.items()))
                        logger.info("  Holdout: %d trades, agg_pf=%.3f, robust_pf=%.3f, "
                                     "WR=%.0f%%, PnL=$%.2f, Sharpe=%.3f, DSR=%.3f",
                                     len(trades), agg_pf, robust_pf, win_rate * 100,
                                     total_pnl, sharpe, dsr)
                        logger.info("  Exits: %s", exit_str)
    else:
        logger.info("\n  No config passed walk-forward. Skipping holdout.")

    # ---- Final Verdict ----
    verdict = "FAIL_WALK_FORWARD"
    fail_reasons = []

    if best_passing:
        ws_best = wf_summary[best_passing]
        if holdout_result is None or holdout_result["n_trades"] == 0:
            verdict = "FAIL_HOLDOUT"
            fail_reasons.append("no holdout trades")
        elif holdout_result["aggregate_pf"] <= 1.0:
            verdict = "FAIL_HOLDOUT"
            fail_reasons.append(f"holdout agg_pf={holdout_result['aggregate_pf']:.3f}")
        elif holdout_result["dsr"] <= 0:
            verdict = "FAIL_DSR"
            fail_reasons.append(f"DSR={holdout_result['dsr']:.3f}")
        else:
            verdict = "CONFIRMED"
    else:
        # Find best aggregate PF for reporting
        if sorted_configs:
            best_name, best_ws = sorted_configs[0]
            if best_ws.get("avg_agg_pf", 0) <= 1.0:
                fail_reasons.append(f"best avg_agg_pf={best_ws['avg_agg_pf']:.3f} ({best_name})")
            if best_ws.get("consistency_agg", 0) < 0.60:
                fail_reasons.append(f"best consistency_agg={best_ws['consistency_agg']:.2f}")

    # ---- Write Report ----
    elapsed = time.time() - t_start
    report = {
        "run_info": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "days": args.days,
            "n_bars": n_bars,
            "n_symbols": n_symbols,
            "n_folds": len(folds),
            "holdout_days": args.holdout_days,
            "train_days": args.train_days,
            "max_features": args.max_features,
            "discovery_rounds": max_rounds,
            "signal_layer": "v3 (rolling window, feature cap, P75 threshold)",
            "exit_factorial": {
                "stop_atr": [2.0, 3.0],
                "trail_act_atr": [1.0, 1.5, 2.0],
                "tp_atr": [2.0, 3.0],
                "trail_dist_atr": 0.5,
                "n_configs": len(CONFIGS),
            },
            "configs": {k: v for k, v in CONFIGS.items()},
        },
        "volume_filter": {
            "n_symbols_total": n_symbols,
            "n_symbols_filtered": len(vol_filter_set),
            "percentile": 50,
        },
        "walk_forward": wf_summary,
        "ranking": [name for name, _ in sorted_configs],
        "holdout": holdout_result,
        "verdict": verdict,
        "fail_reasons": fail_reasons,
    }

    with open(results_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    logger.info("\n" + "=" * 70)
    logger.info("VERDICT: %s", verdict)
    if best_passing:
        logger.info("Best passing config: %s", best_passing)
    elif sorted_configs:
        logger.info("Best config (not passing): %s (avg_agg_pf=%.3f)",
                     sorted_configs[0][0], sorted_configs[0][1].get("avg_agg_pf", 0))
    if fail_reasons:
        logger.info("Reasons: %s", "; ".join(fail_reasons))
    logger.info("Elapsed: %.1f min", elapsed / 60)
    logger.info("Results: %s", results_path)
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
