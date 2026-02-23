#!/usr/bin/env python3
"""
U2 Diagnostic — Gate-by-gate rejection analysis for U1 Unicorn Ignition.

Runs U1's gate logic on 90 days of 1h OHLCV data and reports:
- Per-gate rejection rates
- Which symbols pass each gate most often
- Which parameter changes would have the biggest impact

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/u2_diagnostic.py [--days 90]
"""

import os
import sys
import time
import logging
import argparse
import json
import numpy as np
from datetime import datetime, timezone
from collections import defaultdict

# Path setup (Docker vs host)
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/indicators.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))
sys.path.insert(0, os.path.join(os.path.abspath(worker_app_dir), 'setups'))

import psycopg
from indicators import OHLCV
from setups.u1_unicorn_ignition import (
    U1Config, U1Evaluator, U1State, U1Phase,
    evaluate_gate1_tradability_proxy, evaluate_gate2_participation_proxy,
    evaluate_gate3_compression, compute_baseline_volume,
    compute_ignition_features, compute_ignition_score,
    is_stablecoin_base, is_wrapped, is_leverage_token,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

# Baseline filters — baked in, not swept
EXCLUDED_SYMBOLS = {
    "SLFUSDT", "ALPACAUSDT", "PDAUSDT", "WINGUSDT", "VIDTUSDT",
    "VIBUSDT", "PROSUSDT", "BETAUSDT", "HARDUSDT", "FIROUSDT",
    "BURGERUSDT", "TROYUSDT", "AMBUSDT", "WANUSDT",
}


def load_ohlcv_1h(days: int) -> dict[str, list[OHLCV]]:
    """Load 1h OHLCV from Postgres, keyed by symbol."""
    logger.info("Loading 1h OHLCV data for %d days...", days)
    data = defaultdict(list)
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE timeframe = '1h'
                  AND is_final = TRUE
                  AND bar_start_ts >= NOW() - %s * INTERVAL '1 day'
                ORDER BY symbol, bar_start_ts ASC
            """, (days,))
            for row in cur:
                symbol, bar_start_ts, o, h, l, c, v = row
                ts_ms = int(bar_start_ts.timestamp() * 1000)
                data[symbol].append(OHLCV(timestamp=ts_ms, open=o, high=h, low=l, close=c, volume=v))
    total_bars = sum(len(v) for v in data.values())
    logger.info("Loaded %d bars across %d symbols", total_bars, len(data))
    return dict(data)


def classify_universe(symbol: str, candles: list[OHLCV]) -> dict:
    """Check if symbol passes U1 universe filter."""
    # Exclude stablecoins, wrapped, leverage tokens
    if is_stablecoin_base(symbol) or is_wrapped(symbol) or is_leverage_token(symbol):
        return {"passes": False, "reason": "token_type_excluded"}

    # Check volume range (use median of last 48 bars)
    if len(candles) < 48:
        return {"passes": False, "reason": "insufficient_bars"}

    recent_vols = [c.volume for c in candles[-48:]]
    median_vol = float(np.median(recent_vols))

    if median_vol < U1Config.MIN_VOLUME_USD:
        return {"passes": False, "reason": "below_min_vol", "median_vol": median_vol}
    if median_vol > U1Config.MAX_VOLUME_USD:
        return {"passes": False, "reason": "above_max_vol", "median_vol": median_vol}

    # Major exclusion
    if symbol in U1Config.MAJOR_SYMBOLS:
        return {"passes": False, "reason": "major_symbol"}

    return {"passes": True, "median_vol": median_vol}


def run_diagnostic(days: int):
    t0 = time.time()
    all_data = load_ohlcv_1h(days + 30)  # warmup buffer

    # Filter out excluded symbols
    symbols = sorted(s for s in all_data if s not in EXCLUDED_SYMBOLS)
    logger.info("Analyzing %d symbols (excluded %d chronic firers)", len(symbols), len(EXCLUDED_SYMBOLS))

    window = U1Config.GATE_WINDOW_BARS  # 48

    # Counters
    stats = {
        "total_symbols": len(symbols),
        "universe_pass": 0,
        "universe_fail_reasons": defaultdict(int),
        "gate1_evals": 0, "gate1_pass": 0,
        "gate2_evals": 0, "gate2_pass": 0,
        "gate3_evals": 0, "gate3_pass": 0,
        "all_gates_pass": 0,
        "ignition_score_evals": 0,
        "ignition_score_above_055": 0,
        "ignition_score_above_035": 0,
        "ignition_score_above_025": 0,
        "vol_z_above_2": 0,
        "vol_z_above_1": 0,
        "full_trigger_pass": 0,
    }

    # Per-gate sub-metric analysis
    gate2_sub = {"vol_rising": 0, "activity_rising": 0, "no_whale": 0, "total": 0}
    gate3_sub = {"range_tight": 0, "atr_contracting": 0, "not_igniting": 0, "total": 0}

    # Per-symbol best scores
    sym_best_score = {}
    sym_gate_pass_counts = defaultdict(lambda: {"g1": 0, "g2": 0, "g3": 0, "all": 0, "bars": 0})

    processed = 0
    for symbol in symbols:
        candles = all_data[symbol]

        # Universe check
        univ = classify_universe(symbol, candles)
        if not univ["passes"]:
            stats["universe_fail_reasons"][univ["reason"]] += 1
            continue
        stats["universe_pass"] += 1

        if len(candles) < window * 2:
            continue

        best_score = 0.0
        n_bars = len(candles)

        # Sample every 6th bar (6 hours) to keep runtime manageable
        for i in range(window * 2, n_bars, 6):
            candles_last = candles[i - window:i]
            candles_prev = candles[i - window * 2:i - window]
            current = candles[i - 1]

            # Gate 1
            g1_pass, g1_m = evaluate_gate1_tradability_proxy(candles_last, candles_prev)
            stats["gate1_evals"] += 1
            if g1_pass:
                stats["gate1_pass"] += 1
            sym_gate_pass_counts[symbol]["g1"] += int(g1_pass)

            # Gate 2
            g2_pass, g2_m = evaluate_gate2_participation_proxy(candles_last, candles_prev)
            stats["gate2_evals"] += 1
            if g2_pass:
                stats["gate2_pass"] += 1
            sym_gate_pass_counts[symbol]["g2"] += int(g2_pass)

            gate2_sub["total"] += 1
            gate2_sub["vol_rising"] += int(g2_m.get("volume_rising", False))
            gate2_sub["activity_rising"] += int(g2_m.get("activity_rising", False))
            gate2_sub["no_whale"] += int(g2_m.get("no_whale_dominance", False))

            # Gate 3
            baseline, _ = compute_baseline_volume(candles[max(0, i - 500):i])
            rel_vol = current.volume / baseline if baseline > 0 else 0
            g3_pass, g3_m = evaluate_gate3_compression(candles_last, candles_prev, rel_vol)
            stats["gate3_evals"] += 1
            if g3_pass:
                stats["gate3_pass"] += 1
            sym_gate_pass_counts[symbol]["g3"] += int(g3_pass)

            gate3_sub["total"] += 1
            gate3_sub["range_tight"] += int(g3_m.get("range_tight", False))
            gate3_sub["atr_contracting"] += int(g3_m.get("atr_contracting", False))
            gate3_sub["not_igniting"] += int(g3_m.get("not_igniting", False))

            # All gates
            all_pass = g1_pass and g2_pass and g3_pass
            if all_pass:
                stats["all_gates_pass"] += 1
                sym_gate_pass_counts[symbol]["all"] += 1

            sym_gate_pass_counts[symbol]["bars"] += 1

            # Ignition score (only compute if gates pass to save time, but also sample broadly)
            ign_features = compute_ignition_features(candles[max(0, i - 500):i], baseline)
            if ign_features:
                stats["ignition_score_evals"] += 1
                total, _, _ = compute_ignition_score(ign_features)
                if total >= 0.55:
                    stats["ignition_score_above_055"] += 1
                if total >= 0.35:
                    stats["ignition_score_above_035"] += 1
                if total >= 0.25:
                    stats["ignition_score_above_025"] += 1

                vol_z = ign_features.get("vol_z", 0)
                if vol_z >= 2.0:
                    stats["vol_z_above_2"] += 1
                if vol_z >= 1.0:
                    stats["vol_z_above_1"] += 1

                if total > best_score:
                    best_score = total

                # Full trigger: all gates + score >= 0.55 + vol_z >= 2.0
                if all_pass and total >= 0.55 and vol_z >= 2.0:
                    stats["full_trigger_pass"] += 1

        sym_best_score[symbol] = best_score
        processed += 1
        if processed % 50 == 0:
            elapsed = time.time() - t0
            logger.info("[%d/%d] symbols processed, %.0fs elapsed", processed, stats["universe_pass"], elapsed)

    elapsed = time.time() - t0

    # Compute rates
    total_evals = stats["gate1_evals"] or 1
    ign_evals = stats["ignition_score_evals"] or 1

    results = {
        "diagnostic": "U2_gate_rejection_analysis",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "days": days,
            "timeframe": "1h",
            "gate_window_bars": window,
            "sample_stride": 6,
        },
        "universe": {
            "total_symbols": stats["total_symbols"],
            "pass_universe_filter": stats["universe_pass"],
            "fail_reasons": dict(stats["universe_fail_reasons"]),
        },
        "gate_rejection_rates": {
            "total_bar_evaluations": total_evals,
            "gate1_tradability": {
                "pass_count": stats["gate1_pass"],
                "pass_rate": stats["gate1_pass"] / total_evals,
                "reject_rate": 1 - stats["gate1_pass"] / total_evals,
            },
            "gate2_participation": {
                "pass_count": stats["gate2_pass"],
                "pass_rate": stats["gate2_pass"] / total_evals,
                "reject_rate": 1 - stats["gate2_pass"] / total_evals,
                "sub_metrics": {
                    "volume_rising_rate": gate2_sub["vol_rising"] / max(gate2_sub["total"], 1),
                    "activity_rising_rate": gate2_sub["activity_rising"] / max(gate2_sub["total"], 1),
                    "no_whale_rate": gate2_sub["no_whale"] / max(gate2_sub["total"], 1),
                },
            },
            "gate3_compression": {
                "pass_count": stats["gate3_pass"],
                "pass_rate": stats["gate3_pass"] / total_evals,
                "reject_rate": 1 - stats["gate3_pass"] / total_evals,
                "sub_metrics": {
                    "range_tight_rate": gate3_sub["range_tight"] / max(gate3_sub["total"], 1),
                    "atr_contracting_rate": gate3_sub["atr_contracting"] / max(gate3_sub["total"], 1),
                    "not_igniting_rate": gate3_sub["not_igniting"] / max(gate3_sub["total"], 1),
                },
            },
            "all_three_gates": {
                "pass_count": stats["all_gates_pass"],
                "pass_rate": stats["all_gates_pass"] / total_evals,
            },
        },
        "ignition_thresholds": {
            "total_evals": ign_evals,
            "score_above_0.55": {"count": stats["ignition_score_above_055"], "rate": stats["ignition_score_above_055"] / ign_evals},
            "score_above_0.35": {"count": stats["ignition_score_above_035"], "rate": stats["ignition_score_above_035"] / ign_evals},
            "score_above_0.25": {"count": stats["ignition_score_above_025"], "rate": stats["ignition_score_above_025"] / ign_evals},
            "vol_z_above_2.0": {"count": stats["vol_z_above_2"], "rate": stats["vol_z_above_2"] / ign_evals},
            "vol_z_above_1.0": {"count": stats["vol_z_above_1"], "rate": stats["vol_z_above_1"] / ign_evals},
        },
        "full_trigger": {
            "all_gates_and_score_and_volz": stats["full_trigger_pass"],
            "rate_vs_total_evals": stats["full_trigger_pass"] / total_evals,
        },
        "top_symbols_by_gate_pass_rate": [],
        "runtime_seconds": elapsed,
    }

    # Top symbols that pass all gates most often
    sym_rates = []
    for sym, counts in sym_gate_pass_counts.items():
        if counts["bars"] > 0:
            sym_rates.append({
                "symbol": sym,
                "bars_evaluated": counts["bars"],
                "all_gates_pass_count": counts["all"],
                "all_gates_pass_rate": counts["all"] / counts["bars"],
                "g1_rate": counts["g1"] / counts["bars"],
                "g2_rate": counts["g2"] / counts["bars"],
                "g3_rate": counts["g3"] / counts["bars"],
                "best_ignition_score": sym_best_score.get(sym, 0),
            })
    sym_rates.sort(key=lambda x: x["all_gates_pass_rate"], reverse=True)
    results["top_symbols_by_gate_pass_rate"] = sym_rates[:30]

    # Print summary
    print("\n" + "=" * 70)
    print("U2 DIAGNOSTIC — Gate Rejection Analysis")
    print("=" * 70)
    print(f"\nUniverse: {stats['universe_pass']}/{stats['total_symbols']} symbols pass volume/type filters")
    for reason, count in sorted(stats["universe_fail_reasons"].items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")

    print(f"\nGate evaluations: {total_evals:,} bar-samples across {processed} symbols")
    print(f"\n  Gate 1 (Tradability):    pass {stats['gate1_pass']:>7,} / {total_evals:,} = {stats['gate1_pass']/total_evals:.1%}")
    print(f"  Gate 2 (Participation):  pass {stats['gate2_pass']:>7,} / {total_evals:,} = {stats['gate2_pass']/total_evals:.1%}")
    g2s = results["gate_rejection_rates"]["gate2_participation"]["sub_metrics"]
    print(f"    vol_rising: {g2s['volume_rising_rate']:.1%} | activity_rising: {g2s['activity_rising_rate']:.1%} | no_whale: {g2s['no_whale_rate']:.1%}")
    print(f"  Gate 3 (Compression):    pass {stats['gate3_pass']:>7,} / {total_evals:,} = {stats['gate3_pass']/total_evals:.1%}")
    g3s = results["gate_rejection_rates"]["gate3_compression"]["sub_metrics"]
    print(f"    range_tight: {g3s['range_tight_rate']:.1%} | atr_contracting: {g3s['atr_contracting_rate']:.1%} | not_igniting: {g3s['not_igniting_rate']:.1%}")
    print(f"  ALL 3 gates:             pass {stats['all_gates_pass']:>7,} / {total_evals:,} = {stats['all_gates_pass']/total_evals:.1%}")

    print(f"\nIgnition thresholds ({ign_evals:,} evals):")
    print(f"  score >= 0.55: {stats['ignition_score_above_055']:>7,} ({stats['ignition_score_above_055']/ign_evals:.1%})")
    print(f"  score >= 0.35: {stats['ignition_score_above_035']:>7,} ({stats['ignition_score_above_035']/ign_evals:.1%})")
    print(f"  score >= 0.25: {stats['ignition_score_above_025']:>7,} ({stats['ignition_score_above_025']/ign_evals:.1%})")
    print(f"  vol_z >= 2.0:  {stats['vol_z_above_2']:>7,} ({stats['vol_z_above_2']/ign_evals:.1%})")
    print(f"  vol_z >= 1.0:  {stats['vol_z_above_1']:>7,} ({stats['vol_z_above_1']/ign_evals:.1%})")
    print(f"\n  Full trigger (all gates + score>=0.55 + vol_z>=2): {stats['full_trigger_pass']}")

    print(f"\nTop 10 symbols by all-gates pass rate:")
    for s in sym_rates[:10]:
        print(f"  {s['symbol']:>15}: all={s['all_gates_pass_rate']:.1%} (g1={s['g1_rate']:.1%} g2={s['g2_rate']:.1%} g3={s['g3_rate']:.1%}) best_score={s['best_ignition_score']:.3f}")

    print(f"\nRuntime: {elapsed:.1f}s")
    print("=" * 70)

    # Save results
    out_dir = "/scripts" if os.path.isdir("/scripts") else os.path.join(os.path.dirname(__file__))
    out_path = os.path.join(out_dir, "u2_diagnostic_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("Results saved to %s", out_path)


def main():
    parser = argparse.ArgumentParser(description="U2 Diagnostic: gate rejection analysis")
    parser.add_argument("--days", type=int, default=90, help="Days of data to analyze (default: 90)")
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    run_diagnostic(args.days)


if __name__ == "__main__":
    main()
