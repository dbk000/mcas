#!/usr/bin/env python3
"""
Setup-Level Out-of-Sample Diagnosis (Phase 7, Task 1)
=====================================================

Answers: does ANY setup have positive expectancy out of sample?

Reuses exit_sweep.py infrastructure (same entry/exit logic), adds per-setup
and per-direction breakdowns with train/val split.

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
      python3 /scripts/setup_diagnosis.py \
      --batch-id backfill_20260216_5a7a894c
"""

import os
import sys
import time
import argparse
import logging
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Add scripts dir to path so we can import exit_sweep
scripts_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, scripts_dir)

from exit_sweep import (
    load_opportunities, compute_ohlcv_ranges, batch_load_ohlcv,
    prepare_trade, run_exit_for_trade, compute_pnl,
    TRAIN_CUTOFF, ExitConfig,
)


# ─── Evaluation helpers ──────────────────────────────────────────────────────

def evaluate_trades(trades, config, tp1_mode="exit"):
    """Run exit logic on trades, return per-trade result dicts."""
    results = []
    for trade in trades:
        reason, price, bars_held = run_exit_for_trade(trade, config, tp1_mode)
        pnl, r_mult = compute_pnl(trade, reason, price)
        results.append({
            "setup_id": trade.setup_id,
            "direction": trade.direction,
            "timeframe": trade.timeframe,
            "pnl": pnl,
            "r_mult": r_mult,
            "exit_reason": reason,
        })
    return results


def metrics(results):
    """Compute aggregate metrics from result dicts."""
    if not results:
        return {"n": 0, "wins": 0, "wr": 0, "pf": 0, "pnl": 0, "avg_r": 0, "exits": {}}

    pnls = [r["pnl"] for r in results]
    rs = [r["r_mult"] for r in results]
    exits = defaultdict(int)
    for r in results:
        exits[r["exit_reason"]] += 1

    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))

    return {
        "n": n,
        "wins": wins,
        "wr": wins / n if n > 0 else 0,
        "pf": gp / gl if gl > 0 else 99.99,
        "pnl": sum(pnls),
        "avg_r": sum(rs) / n if n > 0 else 0,
        "exits": dict(exits),
    }


# ─── Display helpers ─────────────────────────────────────────────────────────

HDR = (f"{'':>8} {'N':>5} {'Wins':>5} {'WR':>6} {'PF':>7} {'PnL':>10} {'AvgR':>8}"
       f"  {'FF':>3} {'SL':>3} {'TS':>3} {'TRL':>3} {'TP1':>3} {'TP2':>3}")
SEP = "-" * 80


def row(label, m):
    e = m["exits"]
    return (f"{label:>8} {m['n']:>5} {m['wins']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
            f"{m['pnl']:>+10.2f} {m['avg_r']:>+8.4f}"
            f"  {e.get('FAIL_FAST',0):>3} {e.get('STOP_LOSS',0):>3} "
            f"{e.get('TIME_STOP',0):>3} {e.get('TRAIL_STOP',0):>3} "
            f"{e.get('TP1',0):>3} {e.get('TP2',0):>3}")


def print_section(label, results):
    """Print per-setup table for one split."""
    print(f"\n{label}")
    print(HDR)
    print(SEP)

    by_setup = defaultdict(list)
    for r in results:
        by_setup[r["setup_id"]].append(r)

    for sid in sorted(by_setup.keys()):
        print(row(sid, metrics(by_setup[sid])))

    print(SEP)
    print(row("ALL", metrics(results)))
    ex_s6 = [r for r in results if r["setup_id"] != "S6"]
    print(row("ex-S6", metrics(ex_s6)))


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Setup-level out-of-sample diagnosis")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--timeframes", default="15m,1h,4h,1d")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    timeframes = [t.strip() for t in args.timeframes.split(",")]
    t0 = time.time()

    # ── Load data (reuses exit_sweep infrastructure) ──────────────────────
    logger.info("Loading opportunities (batch=%s)...", args.batch_id)
    opportunities = load_opportunities(database_url, args.batch_id, timeframes)
    if not opportunities:
        logger.error("No opportunities found")
        sys.exit(1)

    logger.info("Loading OHLCV...")
    ranges = compute_ohlcv_ranges(opportunities)
    ohlcv_cache = batch_load_ohlcv(database_url, ranges)

    logger.info("Running entry logic...")
    all_trades = [t for t in (prepare_trade(o, ohlcv_cache) for o in opportunities) if t is not None]
    train = [t for t in all_trades if t.created_ts < TRAIN_CUTOFF]
    val = [t for t in all_trades if t.created_ts >= TRAIN_CUTOFF]
    logger.info("%d trades: %d train, %d val", len(all_trades), len(train), len(val))

    # ── Evaluate with original baseline config ────────────────────────────
    baseline = ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=1.0)

    train_res = evaluate_trades(train, baseline)
    val_res = evaluate_trades(val, baseline)
    all_res = evaluate_trades(all_trades, baseline)

    print("\n" + "=" * 80)
    print("SETUP-LEVEL OUT-OF-SAMPLE DIAGNOSIS")
    print(f"Config: Original Baseline (BE=0, ACT=1.0, DIST=1.0, TP1=exit)")
    print(f"Train: Feb 9-13 ({len(train)}) | Val: Feb 14-16 ({len(val)})")
    print("=" * 80)

    print_section("TRAINING (Feb 9-13)", train_res)
    print_section("VALIDATION (Feb 14-16)", val_res)
    print_section("FULL DATASET (Feb 9-16)", all_res)

    # ── Direction breakdown ───────────────────────────────────────────────
    print(f"\nDIRECTION BREAKDOWN (Full Dataset)")
    print(f"{'':>8} {'Dir':>6} {'N':>5} {'Wins':>5} {'WR':>6} {'PF':>7} {'PnL':>10} {'AvgR':>8}")
    print("-" * 65)

    by_sd = defaultdict(list)
    for r in all_res:
        by_sd[(r["setup_id"], r["direction"])].append(r)

    for (sid, d) in sorted(by_sd.keys()):
        m = metrics(by_sd[(sid, d)])
        print(f"{sid:>8} {d:>6} {m['n']:>5} {m['wins']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
              f"{m['pnl']:>+10.2f} {m['avg_r']:>+8.4f}")

    # ALL long vs short
    for d in ["long", "short"]:
        subset = [r for r in all_res if r["direction"] == d]
        m = metrics(subset)
        print(f"{'ALL':>8} {d:>6} {m['n']:>5} {m['wins']:>5} {m['wr']*100:>5.1f}% {m['pf']:>7.3f} "
              f"{m['pnl']:>+10.2f} {m['avg_r']:>+8.4f}")

    # ── Config comparison ─────────────────────────────────────────────────
    configs = [
        ("No trail", ExitConfig(trail_breakeven_r=0, trail_activation_r=999, trail_distance_r=1.0)),
        ("Baseline", baseline),
        ("Phase 5b", ExitConfig(trail_breakeven_r=0.75, trail_activation_r=1.0, trail_distance_r=0.5)),
    ]

    for portfolio_label, filter_fn in [("ALL setups", lambda r: True),
                                        ("ex-S6", lambda r: r["setup_id"] != "S6")]:
        print(f"\nCONFIG COMPARISON ({portfolio_label})")
        print(f"{'Config':>12} {'TrnN':>5} {'TrnWR':>6} {'TrnPF':>7} {'TrnPnL':>10}"
              f" {'ValN':>5} {'ValWR':>6} {'ValPF':>7} {'ValPnL':>10}")
        print("-" * 80)

        for name, cfg in configs:
            tr = metrics([r for r in evaluate_trades(train, cfg) if filter_fn(r)])
            vr = metrics([r for r in evaluate_trades(val, cfg) if filter_fn(r)])
            print(f"{name:>12} {tr['n']:>5} {tr['wr']*100:>5.1f}% {tr['pf']:>7.3f} {tr['pnl']:>+10.2f}"
                  f" {vr['n']:>5} {vr['wr']*100:>5.1f}% {vr['pf']:>7.3f} {vr['pnl']:>+10.2f}")

    # ── Decision gate ─────────────────────────────────────────────────────
    val_by_setup = defaultdict(list)
    train_by_setup = defaultdict(list)
    for r in val_res:
        val_by_setup[r["setup_id"]].append(r)
    for r in train_res:
        train_by_setup[r["setup_id"]].append(r)

    print(f"\n{'DECISION GATE':^80}")
    print("=" * 80)

    # Gate 1: Any setup val PF > 1.0 with N > 30
    gate1 = [(k, metrics(v)) for k, v in val_by_setup.items()
             if metrics(v)["pf"] > 1.0 and metrics(v)["n"] > 30]
    if gate1:
        parts = [f"{k}(PF={m['pf']:.3f} N={m['n']})" for k, m in gate1]
        print(f"  [PASS] Val PF > 1.0 & N > 30: {', '.join(parts)}")
    else:
        best = max(((k, metrics(v)) for k, v in val_by_setup.items()), key=lambda x: x[1]["pf"], default=None)
        if best:
            print(f"  [FAIL] No setup has val PF > 1.0 & N > 30 "
                  f"(best: {best[0]} PF={best[1]['pf']:.3f} N={best[1]['n']})")

    # Gate 2: ex-S6 portfolio val PF > 1.0
    ex_s6_val_m = metrics([r for r in val_res if r["setup_id"] != "S6"])
    if ex_s6_val_m["pf"] > 1.0:
        print(f"  [PASS] ex-S6 portfolio val PF = {ex_s6_val_m['pf']:.3f}")
    else:
        print(f"  [FAIL] ex-S6 portfolio val PF = {ex_s6_val_m['pf']:.3f}")

    # Gate 3: Any setup train PF > 0.85
    gate3 = [(k, metrics(v)) for k, v in train_by_setup.items()
             if metrics(v)["pf"] > 0.85]
    if gate3:
        parts = [f"{k}({m['pf']:.3f})" for k, m in gate3]
        print(f"  [INFO] Train PF > 0.85: {', '.join(parts)}")
    else:
        print(f"  [FAIL] No setup has train PF > 0.85")

    # Small-N setups that might have edge but insufficient sample
    small_n_edge = [(k, metrics(v)) for k, v in val_by_setup.items()
                    if metrics(v)["pf"] > 1.0 and metrics(v)["n"] <= 30 and metrics(v)["n"] > 5]
    if small_n_edge:
        parts = [f"{k}(PF={m['pf']:.3f} N={m['n']})" for k, m in small_n_edge]
        print(f"  [NOTE] Small-N edge candidates (val PF > 1.0, N <= 30): {', '.join(parts)}")

    # Verdict
    print()
    if gate1:
        print("  VERDICT: Signal exists → proceed to Task 2 (Feature Analysis)")
    elif ex_s6_val_m["pf"] > 1.0:
        print("  VERDICT: S6 is the problem → disable or rebuild S6 entry logic")
    elif gate3:
        print("  VERDICT: Regime sensitivity → proceed to Task 2 with walk-forward validation")
    else:
        print("  VERDICT: No edge detected in current entries")
        print("  Recommend: new signal types, more selective criteria, or different conditions")

    print()
    logger.info("Done in %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
