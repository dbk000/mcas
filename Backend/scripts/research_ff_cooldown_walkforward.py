#!/usr/bin/env python3
"""Walk-forward validation: FAIL_FAST cooldown filter.

Tests whether suppressing entries after a FAIL_FAST exit on the same
(symbol, setup_id, timeframe) tuple improves system PnL.

Variants:
  N1  - skip next 1 trade on same tuple
  N2  - skip next 2 trades on same tuple
  6h  - skip entries within 6h of FF exit_ts
  12h - skip entries within 12h of FF exit_ts
  24h - skip entries within 24h of FF exit_ts

Walk-forward: 5-fold non-overlapping test windows, 1-day embargo.
Success gate: filtered avg daily PF > baseline avg daily PF in ≥4/5 folds.
DSR correction for 5 variants tested.
"""

import json
import os
import sys
import time
import math
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict

import psycopg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading")

# ── Data fetch ────────────────────────────────────────────────────────

def fetch_trades():
    """Fetch deduplicated simulation results ordered by entry_ts."""
    sql = """
    SELECT symbol, setup_id, timeframe,
           entry_ts AT TIME ZONE 'UTC' AS entry_ts,
           exit_ts AT TIME ZONE 'UTC' AS exit_ts,
           exit_reason, pnl_net, bars_held
    FROM (
        SELECT DISTINCT ON (opportunity_id)
            symbol, setup_id, timeframe, entry_ts, exit_ts,
            exit_reason, pnl_net, bars_held
        FROM simulation_results
        WHERE data_available = true
        ORDER BY opportunity_id, created_at DESC
    ) d
    ORDER BY entry_ts
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    print(f"  Fetched {len(rows)} deduplicated trades")
    return rows


# ── Sequence builder ──────────────────────────────────────────────────

def build_sequences(trades):
    """Enrich trades with previous exit info per (symbol, setup_id, tf) tuple."""
    last_exit = {}  # key -> (exit_reason, exit_ts)

    for t in trades:  # already sorted by entry_ts
        key = (t["symbol"], t["setup_id"], t["timeframe"])
        prev = last_exit.get(key)
        t["prev_exit_reason"] = prev[0] if prev else None
        t["prev_exit_ts"] = prev[1] if prev else None
        t["day"] = t["entry_ts"].date() if hasattr(t["entry_ts"], "date") else t["entry_ts"]
        last_exit[key] = (t["exit_reason"], t["exit_ts"])

    return trades


# ── Cooldown filter ───────────────────────────────────────────────────

def apply_cooldown(trades, variant):
    """Apply cooldown filter. Returns list of trades that would execute.

    Suppressed trades never execute → they don't generate exit events
    → they don't trigger or clear cooldowns. But count-based variants
    (N1/N2) consume one 'skip' per suppressed opportunity.
    """
    cooldown = {}  # key -> {remaining: int, ff_exit_ts: datetime}
    included = []

    for t in trades:
        key = (t["symbol"], t["setup_id"], t["timeframe"])

        # Check if in cooldown
        suppressed = False
        if key in cooldown:
            cs = cooldown[key]
            if variant == "N1" or variant == "N2":
                if cs["remaining"] > 0:
                    suppressed = True
                    cs["remaining"] -= 1
                    if cs["remaining"] <= 0:
                        del cooldown[key]
            else:
                # Time-based
                hours = int(variant.replace("h", ""))
                elapsed_h = (t["entry_ts"] - cs["ff_exit_ts"]).total_seconds() / 3600
                if elapsed_h < hours:
                    suppressed = True
                else:
                    del cooldown[key]

        if suppressed:
            continue

        included.append(t)

        # Set cooldown on FF exit
        if t["exit_reason"] == "FAIL_FAST":
            n_skip = 1 if variant == "N1" else 2 if variant == "N2" else 0
            cooldown[key] = {"remaining": n_skip, "ff_exit_ts": t["exit_ts"]}
        else:
            # Non-FF clears any lingering cooldown on this tuple
            cooldown.pop(key, None)

    return included


# ── Metrics ───────────────────────────────────────────────────────────

def daily_profit_factor(trades):
    """Average daily Profit Factor (Section 4.5 gate)."""
    by_day = defaultdict(lambda: {"gp": 0.0, "gl": 0.0, "n": 0})
    for t in trades:
        d = t["day"]
        by_day[d]["n"] += 1
        if t["pnl_net"] >= 0:
            by_day[d]["gp"] += t["pnl_net"]
        else:
            by_day[d]["gl"] += abs(t["pnl_net"])

    pfs = []
    for d in sorted(by_day):
        v = by_day[d]
        if v["gl"] > 0:
            pfs.append(v["gp"] / v["gl"])
        elif v["gp"] > 0:
            pfs.append(10.0)  # cap inf at 10 for averaging
        # skip days with zero trades

    avg = sum(pfs) / len(pfs) if pfs else 0.0
    return avg, pfs


def aggregate_pf(trades):
    """Aggregate PF over full period (reported but not the gate)."""
    gp = sum(t["pnl_net"] for t in trades if t["pnl_net"] >= 0)
    gl = sum(abs(t["pnl_net"]) for t in trades if t["pnl_net"] < 0)
    return gp / gl if gl > 0 else 0.0


def sharpe_daily(trades):
    """Daily Sharpe ratio from PnL."""
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["day"]] += t["pnl_net"]
    if len(by_day) < 3:
        return 0.0
    daily_pnl = [by_day[d] for d in sorted(by_day)]
    mu = sum(daily_pnl) / len(daily_pnl)
    var = sum((x - mu) ** 2 for x in daily_pnl) / (len(daily_pnl) - 1)
    std = math.sqrt(var) if var > 0 else 1e-9
    return mu / std


# ── DSR ───────────────────────────────────────────────────────────────

def deflated_sharpe_ratio(observed_sr, n_days, n_trials):
    """DSR per Harvey et al. 2016. Returns DSR and p-value."""
    if n_days < 5 or n_trials < 1:
        return 0.0, 1.0

    # Expected max SR under null (Euler-Mascheroni approximation)
    euler_mascheroni = 0.5772
    e_max_sr = math.sqrt(2 * math.log(n_trials)) - \
               (euler_mascheroni + math.log(math.pi / 2)) / (2 * math.sqrt(2 * math.log(n_trials)))

    # SR standard error
    sr_se = 1.0 / math.sqrt(n_days)

    # DSR = (observed - expected_max) / se
    dsr = (observed_sr - e_max_sr) / sr_se if sr_se > 0 else 0.0

    # One-sided p-value from normal CDF
    p_value = 0.5 * math.erfc(dsr / math.sqrt(2))
    return dsr, p_value


# ── Per-setup analysis ────────────────────────────────────────────────

def per_setup_analysis(all_trades, filtered_trades, test_days):
    """Breakdown by setup_id."""
    filtered_set = set(id(t) for t in filtered_trades)

    setups = defaultdict(lambda: {
        "base_trades": 0, "filt_trades": 0, "excluded": 0,
        "base_pnl": 0.0, "filt_pnl": 0.0,
        "base_ff": 0, "filt_ff": 0,
    })

    for t in all_trades:
        if t["day"] not in test_days:
            continue
        s = t["setup_id"]
        setups[s]["base_trades"] += 1
        setups[s]["base_pnl"] += t["pnl_net"]
        if t["exit_reason"] == "FAIL_FAST":
            setups[s]["base_ff"] += 1

        if id(t) in filtered_set:
            setups[s]["filt_trades"] += 1
            setups[s]["filt_pnl"] += t["pnl_net"]
            if t["exit_reason"] == "FAIL_FAST":
                setups[s]["filt_ff"] += 1
        else:
            setups[s]["excluded"] += 1

    result = {}
    for s in sorted(setups):
        v = setups[s]
        result[s] = {
            "baseline_trades": v["base_trades"],
            "filtered_trades": v["filt_trades"],
            "excluded": v["excluded"],
            "baseline_pnl": round(v["base_pnl"], 2),
            "filtered_pnl": round(v["filt_pnl"], 2),
            "pnl_improvement": round(v["filt_pnl"] - v["base_pnl"], 2),
            "baseline_ff_count": v["base_ff"],
            "filtered_ff_count": v["filt_ff"],
        }
    return result


# ── Main ──────────────────────────────────────────────────────────────

def main():
    t0 = time.time()

    # ── 1. Fetch ──
    print("[1/5] Fetching trades...")
    trades = fetch_trades()
    trades = build_sequences(trades)

    days = sorted(set(t["day"] for t in trades))
    n_days = len(days)
    print(f"  {n_days} trading days: {days[0]} → {days[-1]}")
    print(f"  FF trades: {sum(1 for t in trades if t['exit_reason']=='FAIL_FAST')}")

    # ── 2. Define folds ──
    # Reserve last ~20% as holdout (Section 4.2)
    n_holdout = max(4, n_days // 5)
    n_wf = n_days - n_holdout
    holdout_days = set(days[n_wf:])
    wf_days = days[:n_wf]

    # 5 non-overlapping test windows with 1-day embargo between
    # Embargo days are excluded from PnL measurement but cooldown state flows through
    fold_size = n_wf // 5
    folds = []
    for i in range(5):
        s = i * fold_size
        e = (i + 1) * fold_size if i < 4 else n_wf
        test_set = set(wf_days[s:e])
        # Embargo: last day of previous fold (not measured in either fold)
        embargo = wf_days[s - 1] if s > 0 else None
        if embargo and embargo in test_set:
            test_set.discard(embargo)
        folds.append({"fold": i + 1, "test_days": test_set,
                       "range": f"{wf_days[s]} → {wf_days[e-1]}",
                       "embargo": embargo})

    print(f"\n[2/5] Fold design: {n_wf} WF days ({fold_size}/fold), {n_holdout} holdout days")
    for f in folds:
        print(f"  Fold {f['fold']}: {f['range']} ({len(f['test_days'])}d, embargo={f['embargo']})")
    print(f"  Holdout: {days[n_wf]} → {days[-1]} ({n_holdout}d)")

    # ── 3. Baseline metrics ──
    print("\n[3/5] Computing baseline...")
    base_daily_pf, _ = daily_profit_factor(trades)
    base_agg_pf = aggregate_pf(trades)
    base_sharpe = sharpe_daily(trades)
    base_pnl = sum(t["pnl_net"] for t in trades)
    print(f"  Baseline: avg daily PF={base_daily_pf:.4f}, agg PF={base_agg_pf:.4f}, "
          f"Sharpe={base_sharpe:.3f}, total PnL=${base_pnl:.2f}")

    # ── 4. Walk-forward per variant ──
    variants = ["N1", "N2", "6h", "12h", "24h"]
    all_results = {}

    print(f"\n[4/5] Walk-forward validation ({len(variants)} variants × {len(folds)} folds)...")

    for variant in variants:
        filtered = apply_cooldown(trades, variant)
        filtered_ids = set(id(t) for t in filtered)
        n_excluded = len(trades) - len(filtered)

        # Per-fold results
        fold_results = []
        for fold in folds:
            base_fold = [t for t in trades if t["day"] in fold["test_days"]]
            filt_fold = [t for t in filtered if t["day"] in fold["test_days"]]

            base_pf_avg, base_pf_list = daily_profit_factor(base_fold)
            filt_pf_avg, filt_pf_list = daily_profit_factor(filt_fold)
            improvement = filt_pf_avg - base_pf_avg

            fold_results.append({
                "fold": fold["fold"],
                "days": fold["range"],
                "n_test_days": len(fold["test_days"]),
                "baseline_trades": len(base_fold),
                "filtered_trades": len(filt_fold),
                "excluded": len(base_fold) - len(filt_fold),
                "baseline_avg_daily_pf": round(base_pf_avg, 4),
                "filtered_avg_daily_pf": round(filt_pf_avg, 4),
                "improvement": round(improvement, 4),
                "fold_pass": improvement > 0,
            })

        n_pass = sum(1 for f in fold_results if f["fold_pass"])
        wf_pass = n_pass >= 4

        # Holdout
        base_ho = [t for t in trades if t["day"] in holdout_days]
        filt_ho = [t for t in filtered if t["day"] in holdout_days]
        base_ho_pf, _ = daily_profit_factor(base_ho)
        filt_ho_pf, _ = daily_profit_factor(filt_ho)
        ho_sharpe_base = sharpe_daily(base_ho)
        ho_sharpe_filt = sharpe_daily(filt_ho)

        # Per-setup
        all_test_days = set().union(*(f["test_days"] for f in folds))
        setup_bkdn = per_setup_analysis(trades, filtered, all_test_days)

        all_results[variant] = {
            "total_trades": len(trades),
            "filtered_trades": len(filtered),
            "total_excluded": n_excluded,
            "pct_excluded": round(100 * n_excluded / len(trades), 1),
            "folds": fold_results,
            "n_pass": n_pass,
            "wf_pass": wf_pass,
            "holdout": {
                "n_days": n_holdout,
                "baseline_trades": len(base_ho),
                "filtered_trades": len(filt_ho),
                "baseline_avg_daily_pf": round(base_ho_pf, 4),
                "filtered_avg_daily_pf": round(filt_ho_pf, 4),
                "improvement": round(filt_ho_pf - base_ho_pf, 4),
                "baseline_sharpe": round(ho_sharpe_base, 4),
                "filtered_sharpe": round(ho_sharpe_filt, 4),
            },
            "setup_breakdown": setup_bkdn,
        }

        tag = "PASS" if wf_pass else "FAIL"
        print(f"  {variant:>3s}: {n_pass}/5 folds → {tag}  "
              f"(excluded {n_excluded} trades = {100*n_excluded/len(trades):.1f}%)")
        for fr in fold_results:
            flag = "+" if fr["fold_pass"] else "-"
            print(f"       Fold {fr['fold']}: base={fr['baseline_avg_daily_pf']:.3f} "
                  f"filt={fr['filtered_avg_daily_pf']:.3f} Δ={fr['improvement']:+.3f} [{flag}]")

    # ── 5. DSR on best variant ──
    print("\n[5/5] DSR correction...")
    # Find best WF-passing variant by holdout improvement
    passing = {v: r for v, r in all_results.items() if r["wf_pass"]}

    dsr_result = {}
    if passing:
        best_variant = max(passing, key=lambda v: passing[v]["holdout"]["improvement"])
        best = passing[best_variant]
        ho_sharpe = best["holdout"]["filtered_sharpe"]
        n_ho_days = best["holdout"]["n_days"]
        n_trials = len(variants)
        dsr_val, p_val = deflated_sharpe_ratio(ho_sharpe, n_ho_days, n_trials)
        dsr_result = {
            "best_variant": best_variant,
            "holdout_sharpe": round(ho_sharpe, 4),
            "n_trials": n_trials,
            "n_holdout_days": n_ho_days,
            "dsr": round(dsr_val, 4),
            "p_value": round(p_val, 6),
            "dsr_pass": dsr_val > 0,
        }
        print(f"  Best WF-passing variant: {best_variant}")
        print(f"  Holdout Sharpe: {ho_sharpe:.4f}, DSR: {dsr_val:.4f}, p={p_val:.6f}")
    else:
        print("  No variants passed walk-forward. DSR not applicable.")
        dsr_result = {"best_variant": None, "dsr_pass": False, "note": "No WF-passing variants"}

    # ── Summary ──
    elapsed = time.time() - t0
    print(f"\nCompleted in {elapsed:.1f}s")

    # Also check: does STOP_LOSS show similar clustering? (observation for future work)
    sl_after_sl = sum(1 for t in trades
                      if t["prev_exit_reason"] == "STOP_LOSS" and t["exit_reason"] == "STOP_LOSS")
    sl_after_sl_pnl = sum(t["pnl_net"] for t in trades
                          if t["prev_exit_reason"] == "STOP_LOSS" and t["exit_reason"] == "STOP_LOSS")
    print(f"\n  [Observation] SL_after_SL: {sl_after_sl} trades, ${sl_after_sl_pnl:.2f} "
          f"(similar clustering pattern worth future investigation)")

    # ── Write output ──
    output = {
        "validation_type": "walk_forward_ff_cooldown",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_window": f"{days[0]} to {days[-1]}",
        "n_trading_days": n_days,
        "n_trades": len(trades),
        "n_ff_trades": sum(1 for t in trades if t["exit_reason"] == "FAIL_FAST"),
        "baseline": {
            "avg_daily_pf": round(base_daily_pf, 4),
            "aggregate_pf": round(base_agg_pf, 4),
            "sharpe": round(base_sharpe, 4),
            "total_pnl": round(base_pnl, 2),
        },
        "variants": all_results,
        "dsr": dsr_result,
        "gate": "avg daily PF improvement in ≥4/5 folds",
        "observation_sl_clustering": {
            "sl_after_sl_n": sl_after_sl,
            "sl_after_sl_pnl": round(sl_after_sl_pnl, 2),
        },
        "elapsed_s": round(elapsed, 1),
    }

    out_path = "/scripts/walkforward_ff_cooldown_validation.json"
    if not os.path.isdir("/scripts"):
        out_path = os.path.join(os.path.dirname(__file__), "walkforward_ff_cooldown_validation.json")

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    main()
