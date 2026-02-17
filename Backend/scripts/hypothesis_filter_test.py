#!/usr/bin/env python3
"""
Hypothesis Filter Test — Phase 1 of Signal Architecture Redesign
=================================================================

Tests whether filtering existing 30-day opportunities by momentum, HTF alignment,
timeframe, EMA slope strength, and stop width can push walk-forward PF above 1.0.

All indicator values are already stored in opportunity payloads — this script
reads them and applies hypothetical filters without changing any production code.

Usage:
    docker compose -f docker-compose.yml run --rm -T worker bash -c \
      "pip install numpy scipy scikit-learn --break-system-packages -q && \
       python3 /scripts/hypothesis_filter_test.py --batch-id backfill_20260217_7f49b2d9"
"""

import os
import sys
import json
import time
import copy
import argparse
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
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

scripts_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, scripts_dir)

from exit_sweep import (
    load_opportunities, compute_ohlcv_ranges, batch_load_ohlcv,
    prepare_trade, run_exit_for_trade, compute_pnl, ExitConfig,
    PreparedTrade,
)

UTC = timezone.utc
BASELINE_CONFIG = ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# FILTER FUNCTIONS: each takes (opp_payload_dict, PreparedTrade) -> bool
# ═══════════════════════════════════════════════════════════════════════════════

def _get_payload(opp):
    """Parse payload_json from opportunity dict."""
    raw = opp.get("payload_json")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw or {}


def filter_tf_1h(opp, trade):
    """Filter A: 1h timeframe only."""
    return trade.timeframe == "1h"


def filter_rsi_momentum(opp, trade):
    """Filter B: RSI must align with direction + positive slope."""
    payload = _get_payload(opp)
    iv = payload.get("indicator_values", {})
    rsi = iv.get("rsi")
    rsi_slope = iv.get("rsi_slope")
    if rsi is None or rsi_slope is None:
        return False
    if trade.direction == "long":
        return rsi > 50 and rsi_slope > 0
    else:
        return rsi < 50 and rsi_slope < 0


def filter_htf_aligned(opp, trade):
    """Filter C: Higher timeframe must align with trade direction."""
    payload = _get_payload(opp)
    state = payload.get("state_snapshot", {})
    iv = payload.get("indicator_values", {})

    # S6 stores htf_aligned directly
    if "htf_aligned" in state:
        return state["htf_aligned"] is True

    # S7 stores htf_bias as string
    if "htf_bias" in state:
        bias = state["htf_bias"]
        if trade.direction == "long":
            return bias == "long"
        else:
            return bias == "short"

    # Other setups: derive from indicator_values
    htf_close = iv.get("htf_close")
    htf_sma50 = iv.get("htf_sma50")
    if htf_close is not None and htf_sma50 is not None:
        if trade.direction == "long":
            return htf_close > htf_sma50
        else:
            return htf_close < htf_sma50

    return False  # No HTF data available


def filter_ema_slope_strong(opp, trade):
    """Filter D: EMA20 slope must be strong (|slope| > 0.10 ATR/bar)."""
    payload = _get_payload(opp)
    iv = payload.get("indicator_values", {})
    slope = iv.get("ema20_slope_atr")
    if slope is None:
        return False
    if trade.direction == "long":
        return slope > 0.10
    else:
        return slope < -0.10


def filter_stop_width(opp, trade):
    """Filter E: Stop distance < 4% of entry price."""
    if trade.entry_price <= 0:
        return False
    stop_pct = abs(trade.stop_price - trade.entry_price) / trade.entry_price
    return stop_pct < 0.04


def filter_none(opp, trade):
    """No filter (baseline)."""
    return True


def combine_filters(*filters):
    """Create a composite filter that requires ALL sub-filters to pass."""
    def combined(opp, trade):
        return all(f(opp, trade) for f in filters)
    return combined


# ═══════════════════════════════════════════════════════════════════════════════
# TP OVERRIDE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def tp_original(trade):
    """Original TP levels (no change)."""
    return trade


def tp_disabled(trade):
    """Disable TP1, let trail/time-stop handle exits."""
    t = copy.copy(trade)
    t.tp1_price = None
    t.tp2_price = None
    return t


def tp_wide(trade):
    """TP1 at 1.0R, TP2 at 2.0R."""
    t = copy.copy(trade)
    if trade.direction == "long":
        t.tp1_price = trade.entry_price + 1.0 * trade.r_value
        t.tp2_price = trade.entry_price + 2.0 * trade.r_value
    else:
        t.tp1_price = trade.entry_price - 1.0 * trade.r_value
        t.tp2_price = trade.entry_price - 2.0 * trade.r_value
    return t


def tp_wider(trade):
    """TP1 at 1.5R, TP2 at 3.0R."""
    t = copy.copy(trade)
    if trade.direction == "long":
        t.tp1_price = trade.entry_price + 1.5 * trade.r_value
        t.tp2_price = trade.entry_price + 3.0 * trade.r_value
    else:
        t.tp1_price = trade.entry_price - 1.5 * trade.r_value
        t.tp2_price = trade.entry_price - 3.0 * trade.r_value
    return t


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def pf_calc(pnls):
    """Profit factor from list of PnL values."""
    if not pnls:
        return 0.0
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    return gp / gl if gl > 0 else (99.99 if gp > 0 else 0.0)


def evaluate_combo(trades_with_opps, filter_fn, tp_fn, config, train_cutoff):
    """
    Apply filter, override TPs, run exit sim, compute train/val metrics.

    Returns dict with metrics or None if no trades pass the filter.
    """
    # Apply filter
    filtered = [(opp, trade) for (opp, trade) in trades_with_opps if filter_fn(opp, trade)]
    if not filtered:
        return None

    # Split
    train_pairs = [(o, t) for (o, t) in filtered if t.created_ts < train_cutoff]
    val_pairs = [(o, t) for (o, t) in filtered if t.created_ts >= train_cutoff]

    results = {"train": {}, "val": {}, "all": {}}

    for split_name, pairs in [("train", train_pairs), ("val", val_pairs), ("all", filtered)]:
        pnls, r_mults, exits = [], [], defaultdict(int)
        for opp, trade in pairs:
            # Apply TP override
            modified_trade = tp_fn(trade)
            # Determine TP mode for exit checker
            tp1_mode = "exit" if modified_trade.tp1_price is not None else "disabled"
            reason, price, bars_held = run_exit_for_trade(modified_trade, config, tp1_mode)
            pnl, r_mult = compute_pnl(modified_trade, reason, price)
            pnls.append(pnl)
            r_mults.append(r_mult)
            exits[reason] += 1

        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        results[split_name] = {
            "n": n,
            "wins": wins,
            "wr": wins / n if n > 0 else 0,
            "pf": pf_calc(pnls),
            "pnl": sum(pnls),
            "avg_r": sum(r_mults) / n if n > 0 else 0,
            "exits": dict(exits),
            "ff_rate": exits.get("FAIL_FAST", 0) / n if n > 0 else 0,
        }

    # Compute trades/day
    if filtered:
        dates = set(t.created_ts.date() for _, t in filtered)
        results["trades_per_day"] = len(filtered) / max(1, len(dates))
    else:
        results["trades_per_day"] = 0

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════════════════════════

def print_comparison_table(all_results):
    """Print the main comparison table across all filter combos."""
    print("\n" + "=" * 120)
    print("HYPOTHESIS FILTER TEST — PHASE 1 RESULTS")
    print("=" * 120)

    print(f"\n{'Combo':>16} {'TP':>8} "
          f"{'TrN':>5} {'TrWR':>6} {'TrPF':>7} {'TrPnL':>10} "
          f"{'VlN':>5} {'VlWR':>6} {'VlPF':>7} {'VlPnL':>10} "
          f"{'T/Day':>5} {'VlFF%':>6}")
    print("-" * 120)

    for combo_name, tp_name, result in all_results:
        if result is None:
            print(f"{combo_name:>16} {tp_name:>8}   --- NO TRADES PASS FILTER ---")
            continue

        tr = result["train"]
        vl = result["val"]
        tpd = result["trades_per_day"]
        vl_ff = vl["ff_rate"] * 100

        # Highlight promising results
        marker = ""
        if vl["pf"] > 1.0 and vl["n"] >= 20:
            marker = " ★"
        elif vl["pf"] > 1.0:
            marker = " •"

        print(f"{combo_name:>16} {tp_name:>8} "
              f"{tr['n']:>5} {tr['wr']*100:>5.1f}% {tr['pf']:>7.3f} {tr['pnl']:>+10.2f} "
              f"{vl['n']:>5} {vl['wr']*100:>5.1f}% {vl['pf']:>7.3f} {vl['pnl']:>+10.2f} "
              f"{tpd:>5.1f} {vl_ff:>5.1f}%{marker}")


def print_exit_breakdown(result, label):
    """Print exit reason breakdown for a result."""
    if result is None:
        return
    print(f"\n  {label} — Exit Distribution:")
    for split_name in ["train", "val"]:
        r = result[split_name]
        e = r["exits"]
        total = r["n"]
        if total == 0:
            continue
        parts = []
        for reason in ["FAIL_FAST", "STOP_LOSS", "TIME_STOP", "TRAIL_STOP", "TP1", "TP2"]:
            cnt = e.get(reason, 0)
            if cnt > 0:
                parts.append(f"{reason}={cnt}({cnt/total*100:.0f}%)")
        print(f"    {split_name:>5}: {', '.join(parts)}")


def print_per_setup_breakdown(trades_with_opps, filter_fn, tp_fn, config, train_cutoff, label):
    """Print per-setup metrics for a specific filter combo."""
    print(f"\n  PER-SETUP BREAKDOWN: {label}")
    print(f"  {'Setup':>6} {'TrN':>5} {'TrWR':>6} {'TrPF':>7} {'VlN':>5} {'VlWR':>6} {'VlPF':>7} {'VlFF%':>6}")
    print("  " + "-" * 60)

    # Group by setup
    by_setup = defaultdict(list)
    for opp, trade in trades_with_opps:
        if filter_fn(opp, trade):
            by_setup[trade.setup_id].append((opp, trade))

    for sid in sorted(by_setup.keys()):
        pairs = by_setup[sid]
        result = evaluate_combo(pairs, filter_none, tp_fn, config, train_cutoff)
        if result is None:
            continue
        tr = result["train"]
        vl = result["val"]
        print(f"  {sid:>6} {tr['n']:>5} {tr['wr']*100:>5.1f}% {tr['pf']:>7.3f} "
              f"{vl['n']:>5} {vl['wr']*100:>5.1f}% {vl['pf']:>7.3f} {vl['ff_rate']*100:>5.1f}%")


def print_decision_gate(all_results):
    """Print the automated decision gate."""
    print("\n" + "=" * 120)
    print("DECISION GATE")
    print("=" * 120)

    best_combo = None
    best_val_pf = 0
    best_tpd = 0

    for combo_name, tp_name, result in all_results:
        if result is None:
            continue
        vl = result["val"]
        tpd = result["trades_per_day"]
        if vl["pf"] > best_val_pf and vl["n"] >= 10:
            best_val_pf = vl["pf"]
            best_combo = f"{combo_name}/{tp_name}"
            best_tpd = tpd

    print(f"\n  Best combo: {best_combo}")
    print(f"  Best val PF: {best_val_pf:.3f}")
    print(f"  Trades/day: {best_tpd:.1f}")

    if best_val_pf > 1.2 and best_tpd >= 3:
        print(f"\n  ✓ STRONG SIGNAL — Val PF > 1.2 with sufficient volume")
        print(f"  → Proceed: add gates to existing S6/S7 (or build dedicated S8)")
    elif best_val_pf > 1.0 and best_tpd >= 2:
        print(f"\n  ✓ SIGNAL FOUND — Val PF > 1.0")
        print(f"  → Proceed to Phase 2: build S8 with these filters as hard gates")
    elif best_val_pf > 0.9:
        print(f"\n  ~ MARGINAL — Val PF {best_val_pf:.3f}")
        print(f"  → Filtering helps but isn't enough alone. Phase 2 with additional modifications.")
    else:
        print(f"\n  ✗ NO SIGNAL — Best val PF {best_val_pf:.3f}")
        print(f"  → Filtering cannot fix these entries. Pivot to new signal types.")

    # Check fail-fast improvement
    baseline = None
    best_filtered = None
    for combo_name, tp_name, result in all_results:
        if result is None:
            continue
        if combo_name == "BASE" and tp_name == "original":
            baseline = result
        if combo_name == best_combo.split("/")[0] if best_combo else "":
            best_filtered = result

    if baseline and best_filtered:
        base_ff = baseline["val"]["ff_rate"]
        filt_ff = best_filtered["val"]["ff_rate"]
        print(f"\n  FAIL_FAST rate: {base_ff*100:.1f}% → {filt_ff*100:.1f}% "
              f"({'improved' if filt_ff < base_ff else 'unchanged'})")

    print(f"\n{'=' * 120}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Hypothesis filter test (Phase 1)")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--timeframes", default="15m,1h,4h,1d")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    timeframes = [t.strip() for t in args.timeframes.split(",")]
    t0 = time.time()

    # ── Step 1: Load data ────────────────────────────────────────────────
    logger.info("Loading opportunities (batch=%s)...", args.batch_id)
    opportunities = load_opportunities(database_url, args.batch_id, timeframes)
    if not opportunities:
        logger.error("No opportunities found")
        sys.exit(1)

    logger.info("Loading OHLCV...")
    ranges = compute_ohlcv_ranges(opportunities)
    ohlcv_cache = batch_load_ohlcv(database_url, ranges)

    # ── Step 2: Prepare all trades and pair with opportunities ───────────
    logger.info("Preparing trades...")
    trades_with_opps = []
    filtered_count = 0
    for opp in opportunities:
        trade = prepare_trade(opp, ohlcv_cache)
        if trade is not None:
            trades_with_opps.append((opp, trade))
        else:
            filtered_count += 1

    logger.info("%d valid trades (%d filtered by entry gates)", len(trades_with_opps), filtered_count)

    # ── Step 3: Compute dynamic train/val cutoff (65%) ───────────────────
    all_dates = sorted(set(t.created_ts.date() for _, t in trades_with_opps))
    cutoff_idx = int(len(all_dates) * 0.65)
    train_cutoff = datetime.combine(all_dates[cutoff_idx], datetime.min.time()).replace(tzinfo=UTC)
    logger.info("Train/val cutoff: %s (65%% of %d dates)", all_dates[cutoff_idx], len(all_dates))

    # ── Step 4: Define filter combos ─────────────────────────────────────
    A = filter_tf_1h
    B = filter_rsi_momentum
    C = filter_htf_aligned
    D = filter_ema_slope_strong
    E = filter_stop_width

    combos = [
        # Name, filter_fn, tp_fn
        ("BASE",     filter_none,              tp_original),
        ("A(1h)",    A,                        tp_original),
        ("B(RSI)",   B,                        tp_original),
        ("C(HTF)",   C,                        tp_original),
        ("AB",       combine_filters(A, B),    tp_original),
        ("AC",       combine_filters(A, C),    tp_original),
        ("ABC",      combine_filters(A, B, C), tp_original),
        ("ABCD",     combine_filters(A, B, C, D), tp_original),
        ("ABCDE",    combine_filters(A, B, C, D, E), tp_original),
        # Relaxed combos (in case ABCDE is too selective)
        ("ABE",      combine_filters(A, B, E), tp_original),
        ("ACE",      combine_filters(A, C, E), tp_original),
        ("ABCE",     combine_filters(A, B, C, E), tp_original),
        # TP variants on the most promising filter combos
        ("ABC",      combine_filters(A, B, C), tp_disabled),
        ("ABC",      combine_filters(A, B, C), tp_wide),
        ("ABC",      combine_filters(A, B, C), tp_wider),
        ("ABCE",     combine_filters(A, B, C, E), tp_disabled),
        ("ABCE",     combine_filters(A, B, C, E), tp_wide),
        ("ABCE",     combine_filters(A, B, C, E), tp_wider),
        ("AB",       combine_filters(A, B),    tp_wide),
        ("AB",       combine_filters(A, B),    tp_wider),
    ]

    tp_names = {
        id(tp_original): "original",
        id(tp_disabled): "no_TP",
        id(tp_wide): "1R/2R",
        id(tp_wider): "1.5R/3R",
    }

    # ── Step 5: Run all combos ───────────────────────────────────────────
    all_results = []
    logger.info("Running %d filter combos...", len(combos))

    for idx, (name, filt, tp) in enumerate(combos):
        tp_name = tp_names.get(id(tp), "?")
        result = evaluate_combo(trades_with_opps, filt, tp, BASELINE_CONFIG, train_cutoff)
        all_results.append((name, tp_name, result))

        # Progress
        n_str = f"{result['all']['n']}" if result else "0"
        pf_str = f"{result['val']['pf']:.3f}" if result else "n/a"
        logger.info("  [%d/%d] %s/%s → N=%s val_PF=%s",
                     idx + 1, len(combos), name, tp_name, n_str, pf_str)

    # ── Step 6: Report ───────────────────────────────────────────────────
    print_comparison_table(all_results)

    # Per-setup breakdowns for key combos
    key_combos = [
        ("ABC/original", combine_filters(A, B, C), tp_original, "original"),
        ("ABCE/original", combine_filters(A, B, C, E), tp_original, "original"),
        ("ABC/1R/2R", combine_filters(A, B, C), tp_wide, "1R/2R"),
        ("ABCE/1R/2R", combine_filters(A, B, C, E), tp_wide, "1R/2R"),
        ("AB/1R/2R", combine_filters(A, B), tp_wide, "1R/2R"),
    ]

    print("\n" + "=" * 120)
    print("PER-SETUP BREAKDOWNS")
    print("=" * 120)

    for label, filt, tp, tp_name in key_combos:
        print_per_setup_breakdown(trades_with_opps, filt, tp, BASELINE_CONFIG, train_cutoff, label)
        result = evaluate_combo(trades_with_opps, filt, tp, BASELINE_CONFIG, train_cutoff)
        print_exit_breakdown(result, label)

    # ── Step 7: Decision gate ────────────────────────────────────────────
    print_decision_gate(all_results)

    logger.info("Total time: %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
