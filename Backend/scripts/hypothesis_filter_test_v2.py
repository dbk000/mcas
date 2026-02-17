#!/usr/bin/env python3
"""
Hypothesis Filter Test v2 — Extended combos + exit config sweep
================================================================

Extends Phase 1 to test:
1. Missing combos (ABCDE/no_TP, etc.)
2. Different ExitConfig parameters on ABCE-filtered trades
3. Looser filter variants (RSI thresholds, stop width thresholds)

Usage:
    docker compose -f docker-compose.yml run --rm -T worker bash -c \
      "python3 /scripts/hypothesis_filter_test_v2.py --batch-id backfill_20260217_7f49b2d9"
"""

import os
import sys
import json
import time
import copy
import argparse
import logging
from datetime import datetime, timezone
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

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
)

UTC = timezone.utc


# ═══════════════════════════════════════════════════════════════════════════════
# FILTERS (same as v1, plus variants)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_payload(opp):
    raw = opp.get("payload_json")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw or {}

def filter_tf_1h(opp, trade): return trade.timeframe == "1h"

def filter_rsi_momentum(opp, trade):
    iv = _get_payload(opp).get("indicator_values", {})
    rsi, slope = iv.get("rsi"), iv.get("rsi_slope")
    if rsi is None or slope is None: return False
    return (rsi > 50 and slope > 0) if trade.direction == "long" else (rsi < 50 and slope < 0)

def make_rsi_filter(long_thresh, short_thresh):
    """Parameterized RSI filter with adjustable thresholds."""
    def f(opp, trade):
        iv = _get_payload(opp).get("indicator_values", {})
        rsi, slope = iv.get("rsi"), iv.get("rsi_slope")
        if rsi is None or slope is None: return False
        if trade.direction == "long":
            return rsi > long_thresh and slope > 0
        else:
            return rsi < short_thresh and slope < 0
    return f

def filter_htf_aligned(opp, trade):
    payload = _get_payload(opp)
    state = payload.get("state_snapshot", {})
    iv = payload.get("indicator_values", {})
    if "htf_aligned" in state: return state["htf_aligned"] is True
    if "htf_bias" in state:
        return state["htf_bias"] == trade.direction
    htf_close, htf_sma50 = iv.get("htf_close"), iv.get("htf_sma50")
    if htf_close is not None and htf_sma50 is not None:
        return (htf_close > htf_sma50) if trade.direction == "long" else (htf_close < htf_sma50)
    return False

def filter_ema_slope_strong(opp, trade):
    slope = _get_payload(opp).get("indicator_values", {}).get("ema20_slope_atr")
    if slope is None: return False
    return (slope > 0.10) if trade.direction == "long" else (slope < -0.10)

def make_stop_filter(max_pct):
    def f(opp, trade):
        if trade.entry_price <= 0: return False
        return abs(trade.stop_price - trade.entry_price) / trade.entry_price < max_pct
    return f

def filter_none(opp, trade): return True

def combine(*filters):
    def combined(opp, trade):
        return all(f(opp, trade) for f in filters)
    return combined


# ═══════════════════════════════════════════════════════════════════════════════
# TP OVERRIDES
# ═══════════════════════════════════════════════════════════════════════════════

def tp_original(trade): return trade
def tp_disabled(trade):
    t = copy.copy(trade); t.tp1_price = None; t.tp2_price = None; return t


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════

def pf_calc(pnls):
    if not pnls: return 0.0
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    return gp / gl if gl > 0 else (99.99 if gp > 0 else 0.0)


def evaluate(trades_with_opps, filter_fn, tp_fn, config, train_cutoff):
    filtered = [(o, t) for (o, t) in trades_with_opps if filter_fn(o, t)]
    if not filtered: return None

    train = [(o, t) for (o, t) in filtered if t.created_ts < train_cutoff]
    val = [(o, t) for (o, t) in filtered if t.created_ts >= train_cutoff]

    results = {}
    for split_name, pairs in [("train", train), ("val", val), ("all", filtered)]:
        pnls, r_mults, exits = [], [], defaultdict(int)
        for opp, trade in pairs:
            mt = tp_fn(trade)
            tp1_mode = "exit" if mt.tp1_price is not None else "disabled"
            reason, price, bh = run_exit_for_trade(mt, config, tp1_mode)
            pnl, rm = compute_pnl(mt, reason, price)
            pnls.append(pnl); r_mults.append(rm); exits[reason] += 1

        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        results[split_name] = {
            "n": n, "wins": wins,
            "wr": wins / n if n > 0 else 0,
            "pf": pf_calc(pnls),
            "pnl": sum(pnls),
            "avg_r": sum(r_mults) / n if n > 0 else 0,
            "ff_rate": exits.get("FAIL_FAST", 0) / n if n > 0 else 0,
            "exits": dict(exits),
        }
    dates = set(t.created_ts.date() for _, t in filtered)
    results["tpd"] = len(filtered) / max(1, len(dates))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--timeframes", default="15m,1h,4h,1d")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set"); sys.exit(1)

    timeframes = [t.strip() for t in args.timeframes.split(",")]
    t0 = time.time()

    logger.info("Loading data...")
    opportunities = load_opportunities(database_url, args.batch_id, timeframes)
    ranges = compute_ohlcv_ranges(opportunities)
    ohlcv_cache = batch_load_ohlcv(database_url, ranges)

    logger.info("Preparing trades...")
    twp = []
    for opp in opportunities:
        trade = prepare_trade(opp, ohlcv_cache)
        if trade: twp.append((opp, trade))
    logger.info("%d valid trades", len(twp))

    all_dates = sorted(set(t.created_ts.date() for _, t in twp))
    cutoff_idx = int(len(all_dates) * 0.65)
    train_cutoff = datetime.combine(all_dates[cutoff_idx], datetime.min.time()).replace(tzinfo=UTC)
    logger.info("Cutoff: %s", all_dates[cutoff_idx])

    # ── Filters ──
    A = filter_tf_1h
    B = filter_rsi_momentum
    C = filter_htf_aligned
    D = filter_ema_slope_strong
    E4 = make_stop_filter(0.04)
    E3 = make_stop_filter(0.03)
    E5 = make_stop_filter(0.05)
    B55 = make_rsi_filter(55, 45)  # stricter RSI

    # ── Exit configs ──
    configs = {
        "BE0_A1_D1": ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=1.0),
        "BE0.5_A1_D0.5": ExitConfig(trail_breakeven_r=0.5, trail_activation_r=1.0, trail_distance_r=0.5),
        "BE0.75_A1_D0.5": ExitConfig(trail_breakeven_r=0.75, trail_activation_r=1.0, trail_distance_r=0.5),
        "BE0_A0.75_D0.5": ExitConfig(trail_breakeven_r=0, trail_activation_r=0.75, trail_distance_r=0.5),
        "BE0_A1.5_D0.75": ExitConfig(trail_breakeven_r=0, trail_activation_r=1.5, trail_distance_r=0.75),
        "BE0_A0.5_D0.25": ExitConfig(trail_breakeven_r=0, trail_activation_r=0.5, trail_distance_r=0.25),
        "BE0.5_A0.75_D0.5": ExitConfig(trail_breakeven_r=0.5, trail_activation_r=0.75, trail_distance_r=0.5),
    }

    # ═════════════════════════════════════════════════════════════════
    # TEST 1: Extended filter combos with no_TP and baseline config
    # ═════════════════════════════════════════════════════════════════
    print("\n" + "=" * 130)
    print("TEST 1: EXTENDED FILTER COMBOS (no_TP, baseline exit config)")
    print("=" * 130)

    baseline = configs["BE0_A1_D1"]
    combos1 = [
        ("BASE",        filter_none),
        ("A(1h)",       A),
        ("AB",          combine(A, B)),
        ("ABC",         combine(A, B, C)),
        ("ABCD",        combine(A, B, C, D)),
        ("ABCDE4",      combine(A, B, C, D, E4)),
        ("ABCE4",       combine(A, B, C, E4)),
        ("ABCE3",       combine(A, B, C, E3)),
        ("ABCE5",       combine(A, B, C, E5)),
        ("AB55CE4",     combine(A, B55, C, E4)),
        ("ADE4",        combine(A, D, E4)),
        ("ABE4",        combine(A, B, E4)),
        ("ACE4",        combine(A, C, E4)),
        ("AE4",         combine(A, E4)),
        ("AE3",         combine(A, E3)),
    ]

    print(f"\n{'Combo':>14} {'TP':>6} "
          f"{'TrN':>5} {'TrWR':>6} {'TrPF':>7} "
          f"{'VlN':>5} {'VlWR':>6} {'VlPF':>7} {'VlPnL':>10} "
          f"{'T/Day':>5} {'VlFF%':>5}")
    print("-" * 100)

    for name, filt in combos1:
        for tp_label, tp_fn in [("orig", tp_original), ("no_TP", tp_disabled)]:
            r = evaluate(twp, filt, tp_fn, baseline, train_cutoff)
            if r is None:
                print(f"{name:>14} {tp_label:>6}   --- NO TRADES ---")
                continue
            tr, vl = r["train"], r["val"]
            marker = " ★" if vl["pf"] > 1.0 and vl["n"] >= 20 else ""
            print(f"{name:>14} {tp_label:>6} "
                  f"{tr['n']:>5} {tr['wr']*100:>5.1f}% {tr['pf']:>7.3f} "
                  f"{vl['n']:>5} {vl['wr']*100:>5.1f}% {vl['pf']:>7.3f} {vl['pnl']:>+10.2f} "
                  f"{r['tpd']:>5.1f} {vl['ff_rate']*100:>4.1f}%{marker}")

    # ═════════════════════════════════════════════════════════════════
    # TEST 2: Exit config sweep on ABCE4 filter with no_TP
    # ═════════════════════════════════════════════════════════════════
    print("\n" + "=" * 130)
    print("TEST 2: EXIT CONFIG SWEEP on ABCE4/no_TP filter")
    print("=" * 130)

    abce_filter = combine(A, B, C, E4)

    print(f"\n{'ExitConfig':>20} "
          f"{'TrN':>5} {'TrWR':>6} {'TrPF':>7} "
          f"{'VlN':>5} {'VlWR':>6} {'VlPF':>7} {'VlPnL':>10} "
          f"{'VlFF%':>5} {'VlTRL%':>6} {'VlTS%':>5}")
    print("-" * 110)

    for cfg_name, cfg in configs.items():
        r = evaluate(twp, abce_filter, tp_disabled, cfg, train_cutoff)
        if r is None: continue
        tr, vl = r["train"], r["val"]
        trl_pct = vl["exits"].get("TRAIL_STOP", 0) / vl["n"] * 100 if vl["n"] > 0 else 0
        ts_pct = vl["exits"].get("TIME_STOP", 0) / vl["n"] * 100 if vl["n"] > 0 else 0
        marker = " ★" if vl["pf"] > 1.0 and vl["n"] >= 20 else ""
        print(f"{cfg_name:>20} "
              f"{tr['n']:>5} {tr['wr']*100:>5.1f}% {tr['pf']:>7.3f} "
              f"{vl['n']:>5} {vl['wr']*100:>5.1f}% {vl['pf']:>7.3f} {vl['pnl']:>+10.2f} "
              f"{vl['ff_rate']*100:>4.1f}% {trl_pct:>5.1f}% {ts_pct:>4.1f}%{marker}")

    # Also test on ABCDE4 and ABE4 filters
    for filt_name, filt in [("ABCDE4", combine(A, B, C, D, E4)),
                              ("ABE4", combine(A, B, E4)),
                              ("AE4", combine(A, E4))]:
        print(f"\n  EXIT SWEEP on {filt_name}/no_TP:")
        for cfg_name, cfg in configs.items():
            r = evaluate(twp, filt, tp_disabled, cfg, train_cutoff)
            if r is None: continue
            tr, vl = r["train"], r["val"]
            marker = " ★" if vl["pf"] > 1.0 and vl["n"] >= 20 else ""
            print(f"    {cfg_name:>20} "
                  f"Tr:{tr['n']:>4} PF={tr['pf']:>6.3f} "
                  f"Vl:{vl['n']:>4} PF={vl['pf']:>6.3f} PnL={vl['pnl']:>+9.2f}{marker}")

    # ═════════════════════════════════════════════════════════════════
    # TEST 3: Per-setup PF for winning combos
    # ═════════════════════════════════════════════════════════════════
    print("\n" + "=" * 130)
    print("TEST 3: PER-SETUP BREAKDOWN of best combos (no_TP)")
    print("=" * 130)

    for filt_name, filt_fn in [("ABCE4", combine(A, B, C, E4)),
                                 ("ABCDE4", combine(A, B, C, D, E4)),
                                 ("ABE4", combine(A, B, E4)),
                                 ("AE4", combine(A, E4))]:
        print(f"\n  {filt_name}/no_TP — baseline exit config:")
        by_setup = defaultdict(list)
        for o, t in twp:
            if filt_fn(o, t):
                by_setup[t.setup_id].append((o, t))

        print(f"  {'Setup':>6} {'TrN':>5} {'TrPF':>7} {'VlN':>5} {'VlPF':>7} {'VlFF%':>5}")
        print("  " + "-" * 45)
        for sid in sorted(by_setup.keys()):
            r = evaluate(by_setup[sid], filter_none, tp_disabled, baseline, train_cutoff)
            if r is None: continue
            tr, vl = r["train"], r["val"]
            marker = " ★" if vl["pf"] > 1.0 and vl["n"] >= 10 else ""
            print(f"  {sid:>6} {tr['n']:>5} {tr['pf']:>7.3f} {vl['n']:>5} {vl['pf']:>7.3f} "
                  f"{vl['ff_rate']*100:>4.1f}%{marker}")

    logger.info("Done in %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
