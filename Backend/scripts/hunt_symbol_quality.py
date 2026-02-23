#!/usr/bin/env python3
"""
Hunt: Symbol-Level Quality Analysis for Ignition Events
========================================================
Session S3, Agent B

Investigates whether symbol-level characteristics predict ignition success.
Tests if precision varies by symbol and whether that variation is temporally stable.

Output: /results/hunting/S3_B_symbol_quality_hunt.json

Dependencies: numpy, csv, json (stdlib) — no pandas/scipy required.
"""

import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
import numpy as np

# ---------- paths ----------
if os.path.isdir("/app"):
    CSV_PATH = "/results/ignition_events_with_outcomes.csv"
    VOL_PATH = "/results/hunting/symbol_avg_volume.csv"
    OUT_PATH = "/results/hunting/S3_B_symbol_quality_hunt.json"
else:
    CSV_PATH = "/srv/mcas/results/ignition_events_with_outcomes.csv"
    VOL_PATH = "/srv/mcas/results/hunting/symbol_avg_volume.csv"
    OUT_PATH = "/srv/mcas/results/hunting/S3_B_symbol_quality_hunt.json"

COST_THRESHOLD_BPS = 9
COST_THRESHOLD = COST_THRESHOLD_BPS / 10000.0
MIN_EVENTS = 30

t0 = time.time()


# ================================================================
# Helper: Spearman rank correlation (no scipy)
# ================================================================
def _ranks(arr):
    """Compute ranks with average tie-breaking."""
    n = len(arr)
    order = np.argsort(arr)
    ranks = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i
        while j < n - 1 and arr[order[j + 1]] == arr[order[j]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_r(x, y):
    """Spearman rank correlation + approximate p-value."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    n = len(x)
    if n < 3:
        return 0.0, 1.0
    rx, ry = _ranks(x), _ranks(y)
    return pearson_r(rx, ry)


def pearson_r(x, y):
    """Pearson correlation + approximate 2-sided p-value via t-test."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    n = len(x)
    if n < 3:
        return 0.0, 1.0
    mx, my = x.mean(), y.mean()
    dx, dy = x - mx, y - my
    denom = math.sqrt(float(np.sum(dx ** 2)) * float(np.sum(dy ** 2)))
    if denom == 0:
        return 0.0, 1.0
    r = float(np.sum(dx * dy)) / denom
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        return r, 0.0
    t_stat = r * math.sqrt((n - 2) / (1 - r * r))
    # Approximate p-value from t-distribution using normal approx for large n
    # For n >= 30 this is very close; for smaller n it's approximate
    p = 2.0 * _norm_cdf(-abs(t_stat))
    return r, p


def _norm_cdf(x):
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    if x < -8:
        return 0.0
    if x > 8:
        return 1.0
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.3989422804014327  # 1/sqrt(2*pi)
    p = d * math.exp(-x * x / 2.0) * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    return p if x < 0 else 1.0 - p


# ================================================================
# LOAD DATA
# ================================================================
print(f"[{time.time()-t0:.1f}s] Loading ignition events CSV...")

# Column indices from header: event_ts,symbol,is_ignition,fwd_ret_1h,fwd_ret_4h,fwd_ret_12h,fwd_ret_24h,mfe_24,mae_24,close,volume,hl_range,open_price,bar_idx,sym_idx
# We need: event_ts(0), symbol(1), is_ignition(2), fwd_ret_24h(6), mfe_24(7), mae_24(8), close(9), volume(10)

# Per-symbol accumulators
sym_data = defaultdict(lambda: {
    "timestamps": [],
    "fwd_ret_24h": [],
    "mfe_24": [],
    "mae_24": [],
    "volume": [],
    "close": [],
    "is_winner": [],
})

n_total = 0
n_ignition = 0

with open(CSV_PATH, "r") as f:
    reader = csv.reader(f)
    header = next(reader)
    # Verify expected columns
    col_map = {name: i for i, name in enumerate(header)}
    idx_ts = col_map["event_ts"]
    idx_sym = col_map["symbol"]
    idx_ign = col_map["is_ignition"]
    idx_fwd = col_map["fwd_ret_24h"]
    idx_mfe = col_map["mfe_24"]
    idx_mae = col_map["mae_24"]
    idx_close = col_map["close"]
    idx_vol = col_map["volume"]

    for row in reader:
        n_total += 1
        if row[idx_ign] != "1":
            continue
        # Skip rows with missing forward return data
        fwd_str = row[idx_fwd]
        mfe_str = row[idx_mfe]
        mae_str = row[idx_mae]
        if not fwd_str or not mfe_str or not mae_str:
            continue
        try:
            fwd = float(fwd_str)
            mfe = float(mfe_str)
            mae = float(mae_str)
            vol = float(row[idx_vol]) if row[idx_vol] else 0.0
            close = float(row[idx_close]) if row[idx_close] else 0.0
        except ValueError:
            continue
        n_ignition += 1
        sym = row[idx_sym]
        sd = sym_data[sym]
        sd["timestamps"].append(row[idx_ts])
        sd["fwd_ret_24h"].append(fwd)
        sd["mfe_24"].append(mfe)
        sd["mae_24"].append(mae)
        sd["volume"].append(vol)
        sd["close"].append(close)
        sd["is_winner"].append(1 if fwd > COST_THRESHOLD else 0)

        if n_total % 200000 == 0:
            print(f"  ... {n_total:,} rows read, {n_ignition:,} ignitions")

print(f"[{time.time()-t0:.1f}s] Total rows: {n_total:,}, Ignition events: {n_ignition:,}, Unique symbols: {len(sym_data)}")

# ================================================================
# PART 1: Per-Symbol Precision Distribution
# ================================================================
print(f"\n[{time.time()-t0:.1f}s] === PART 1: Per-Symbol Precision Distribution ===")

# Build per-symbol stats
sym_stats = []
for sym, sd in sym_data.items():
    n = len(sd["fwd_ret_24h"])
    wins = sum(sd["is_winner"])
    fwd_arr = np.array(sd["fwd_ret_24h"], dtype=np.float64)
    mfe_arr = np.array(sd["mfe_24"], dtype=np.float64)
    mae_arr = np.array(sd["mae_24"], dtype=np.float64)
    vol_arr = np.array(sd["volume"], dtype=np.float64)

    # First-half / second-half precision
    half = n // 2
    wins_first = sum(sd["is_winner"][:half]) if half > 0 else 0
    wins_second = sum(sd["is_winner"][half:]) if half > 0 else 0
    n_first = half
    n_second = n - half
    prec_first = wins_first / n_first if n_first > 0 else 0
    prec_second = wins_second / n_second if n_second > 0 else 0

    avg_mfe = float(np.mean(mfe_arr))

    sym_stats.append({
        "symbol": sym,
        "n_events": n,
        "n_winners": wins,
        "precision": wins / n if n > 0 else 0,
        "avg_fwd_ret_24h": float(np.mean(fwd_arr)),
        "med_fwd_ret_24h": float(np.median(fwd_arr)),
        "avg_mfe_24h": avg_mfe,
        "avg_mae_24h": float(np.mean(mae_arr)),
        "mae_mfe_ratio": float(np.mean(mae_arr)) / avg_mfe if avg_mfe > 0 else float("nan"),
        "avg_volume": float(np.mean(vol_arr)),
        "avg_close": float(np.mean(sd["close"])),
        "precision_first_half": prec_first,
        "precision_second_half": prec_second,
        "precision_stability": abs(prec_first - prec_second),
    })

# Filter to meaningful symbols
sym_meaningful = [s for s in sym_stats if s["n_events"] >= MIN_EVENTS]
sym_meaningful.sort(key=lambda x: x["precision"], reverse=True)

precisions = np.array([s["precision"] for s in sym_meaningful])
prec_dist = {
    "count": len(precisions),
    "mean": round(float(np.mean(precisions)), 4),
    "std": round(float(np.std(precisions, ddof=1)), 4) if len(precisions) > 1 else 0,
    "min": round(float(np.min(precisions)), 4),
    "p10": round(float(np.percentile(precisions, 10)), 4),
    "p25": round(float(np.percentile(precisions, 25)), 4),
    "median": round(float(np.median(precisions)), 4),
    "p75": round(float(np.percentile(precisions, 75)), 4),
    "p90": round(float(np.percentile(precisions, 90)), 4),
    "max": round(float(np.max(precisions)), 4),
    "spread_p90_minus_p10": round(float(np.percentile(precisions, 90) - np.percentile(precisions, 10)), 4),
}

print(f"Symbols with >= {MIN_EVENTS} events: {len(sym_meaningful)} / {len(sym_stats)}")
print(f"\nPrecision distribution across {len(sym_meaningful)} symbols:")
for k, v in prec_dist.items():
    print(f"  {k:25s}: {v}")

# Top-20 and bottom-20
top20 = sym_meaningful[:20]
bot20 = sym_meaningful[-20:]

print(f"\n--- Top 20 Most Reliable Ignition Symbols ---")
for s in top20:
    print(f"  {s['symbol']:16s}  n={s['n_events']:5d}  prec={s['precision']:.3f}  avg_ret={s['avg_fwd_ret_24h']:.4f}  stability_gap={s['precision_stability']:.3f}")

print(f"\n--- Bottom 20 Least Reliable ---")
for s in bot20:
    print(f"  {s['symbol']:16s}  n={s['n_events']:5d}  prec={s['precision']:.3f}  avg_ret={s['avg_fwd_ret_24h']:.4f}  stability_gap={s['precision_stability']:.3f}")

# ================================================================
# PART 2: What Makes a Good Ignition Symbol?
# ================================================================
print(f"\n[{time.time()-t0:.1f}s] === PART 2: What Makes a Good Ignition Symbol? ===")

prec_arr = np.array([s["precision"] for s in sym_meaningful])
correlations = {}

for key, label in [
    ("n_events", "Event Count"),
    ("avg_volume", "Avg Volume (from events)"),
    ("avg_mfe_24h", "Avg MFE 24h"),
    ("mae_mfe_ratio", "MAE/MFE Ratio"),
    ("avg_close", "Avg Price"),
]:
    vals = np.array([s[key] for s in sym_meaningful], dtype=np.float64)
    mask = np.isfinite(vals) & np.isfinite(prec_arr)
    if mask.sum() > 10:
        r_pe, p_pe = pearson_r(vals[mask], prec_arr[mask])
        r_sp, p_sp = spearman_r(vals[mask], prec_arr[mask])
        correlations[label] = {
            "pearson_r": round(r_pe, 4),
            "pearson_p": round(p_pe, 6),
            "spearman_r": round(r_sp, 4),
            "spearman_p": round(p_sp, 6),
        }
        print(f"  {label:25s}  Pearson r={r_pe:+.4f} (p={p_pe:.4f})  Spearman r={r_sp:+.4f} (p={p_sp:.4f})")

# First-half vs second-half precision
prec_first_arr = np.array([s["precision_first_half"] for s in sym_meaningful])
prec_second_arr = np.array([s["precision_second_half"] for s in sym_meaningful])
mask_stab = np.isfinite(prec_first_arr) & np.isfinite(prec_second_arr)
if mask_stab.sum() > 10:
    r_pe, p_pe = pearson_r(prec_first_arr[mask_stab], prec_second_arr[mask_stab])
    r_sp, p_sp = spearman_r(prec_first_arr[mask_stab], prec_second_arr[mask_stab])
    correlations["Precision Stability (1st vs 2nd half)"] = {
        "pearson_r": round(r_pe, 4),
        "pearson_p": round(p_pe, 6),
        "spearman_r": round(r_sp, 4),
        "spearman_p": round(p_sp, 6),
    }
    print(f"  {'Precision 1st vs 2nd half':25s}  Pearson r={r_pe:+.4f} (p={p_pe:.4f})  Spearman r={r_sp:+.4f} (p={p_sp:.4f})")

# ================================================================
# PART 3: Symbol Tier Filter
# ================================================================
print(f"\n[{time.time()-t0:.1f}s] === PART 3: Symbol Tier Filter ===")

tiers = {"Tier1_gt60": [], "Tier2_40_60": [], "Tier3_lt40": []}
for s in sym_meaningful:
    if s["precision"] > 0.60:
        tiers["Tier1_gt60"].append(s)
    elif s["precision"] >= 0.40:
        tiers["Tier2_40_60"].append(s)
    else:
        tiers["Tier3_lt40"].append(s)

total_events = sum(s["n_events"] for s in sym_meaningful)
tier_summary = {}
for tier_label in ["Tier1_gt60", "Tier2_40_60", "Tier3_lt40"]:
    tier_syms = tiers[tier_label]
    n_sym = len(tier_syms)
    n_ev = sum(s["n_events"] for s in tier_syms)
    n_win = sum(s["n_winners"] for s in tier_syms)
    t_prec = n_win / n_ev if n_ev > 0 else 0
    avg_ret = np.mean([s["avg_fwd_ret_24h"] for s in tier_syms]) if n_sym > 0 else 0
    avg_mfe = np.mean([s["avg_mfe_24h"] for s in tier_syms]) if n_sym > 0 else 0
    avg_mae = np.mean([s["avg_mae_24h"] for s in tier_syms]) if n_sym > 0 else 0
    coverage = n_ev / total_events * 100 if total_events > 0 else 0

    tier_summary[tier_label] = {
        "n_symbols": n_sym,
        "n_events": n_ev,
        "n_winners": n_win,
        "precision": round(t_prec, 4),
        "avg_fwd_ret_24h": round(float(avg_ret), 6),
        "avg_mfe_24h": round(float(avg_mfe), 6),
        "avg_mae_24h": round(float(avg_mae), 6),
        "coverage_pct": round(coverage, 2),
    }
    print(f"  {tier_label:15s}  symbols={n_sym:4d}  events={n_ev:6d}  precision={t_prec:.4f}  avg_ret={float(avg_ret):.5f}  coverage={coverage:.1f}%")

tier1_events = tier_summary.get("Tier1_gt60", {}).get("n_events", 0)
tier1_cov = tier1_events / total_events * 100 if total_events > 0 else 0
print(f"\n  If we only traded Tier 1: {tier1_events:,} events out of {total_events:,} ({tier1_cov:.1f}% coverage)")

# ================================================================
# PART 4: Temporal Stability of Symbol Quality (Quarterly)
# ================================================================
print(f"\n[{time.time()-t0:.1f}s] === PART 4: Temporal Stability (Quarterly Rank Correlation) ===")

# Collect all timestamps to define quarter edges
all_ts = []
for sym, sd in sym_data.items():
    all_ts.extend(sd["timestamps"])
all_ts.sort()
n_ts = len(all_ts)
q_size = n_ts // 4
q_edges_ts = [all_ts[0], all_ts[q_size], all_ts[2 * q_size], all_ts[3 * q_size], all_ts[-1]]
print(f"  Quarter edges: {q_edges_ts}")

# Assign events to quarters for each symbol
# quarter_sym_data[q][sym] = {"wins": int, "n": int}
quarter_sym_data = [defaultdict(lambda: {"wins": 0, "n": 0}) for _ in range(4)]

for sym, sd in sym_data.items():
    for i, ts in enumerate(sd["timestamps"]):
        # Determine quarter
        if ts < q_edges_ts[1]:
            q = 0
        elif ts < q_edges_ts[2]:
            q = 1
        elif ts < q_edges_ts[3]:
            q = 2
        else:
            q = 3
        quarter_sym_data[q][sym]["n"] += 1
        quarter_sym_data[q][sym]["wins"] += sd["is_winner"][i]

MIN_Q_EVENTS = 10
quarter_labels = []
for q in range(4):
    n_ev = sum(d["n"] for d in quarter_sym_data[q].values())
    n_syms = sum(1 for d in quarter_sym_data[q].values() if d["n"] >= MIN_Q_EVENTS)
    quarter_labels.append(f"Q{q+1}: {q_edges_ts[q]} to {q_edges_ts[q+1]}")
    print(f"  {quarter_labels[-1]}  events={n_ev:,}  symbols(>={MIN_Q_EVENTS}ev)={n_syms}")

# Compute precision per symbol per quarter (min 10 events)
q_prec = {}  # q_prec[q] = {sym: precision}
for q in range(4):
    q_prec[q] = {}
    for sym, d in quarter_sym_data[q].items():
        if d["n"] >= MIN_Q_EVENTS:
            q_prec[q][sym] = d["wins"] / d["n"]

# Adjacent quarter rank correlations
rank_corrs = []
for q in range(3):
    common = set(q_prec[q].keys()) & set(q_prec[q + 1].keys())
    common = sorted(common)
    if len(common) >= 10:
        x = np.array([q_prec[q][s] for s in common])
        y = np.array([q_prec[q + 1][s] for s in common])
        r_sp, p_sp = spearman_r(x, y)
        r_pe, p_pe = pearson_r(x, y)
        rank_corrs.append({
            "transition": f"Q{q+1}->Q{q+2}",
            "n_symbols": len(common),
            "spearman_r": round(r_sp, 4),
            "spearman_p": round(p_sp, 6),
            "pearson_r": round(r_pe, 4),
            "pearson_p": round(p_pe, 6),
        })
        print(f"  {rank_corrs[-1]['transition']}:  n={len(common):3d} symbols  Spearman r={r_sp:+.4f} (p={p_sp:.4f})  Pearson r={r_pe:+.4f} (p={p_pe:.4f})")
    else:
        rank_corrs.append({
            "transition": f"Q{q+1}->Q{q+2}",
            "n_symbols": len(common),
            "spearman_r": None,
            "note": "insufficient overlap",
        })
        print(f"  Q{q+1}->Q{q+2}: insufficient symbol overlap ({len(common)} symbols)")

# Track top-20 from Q1 through later quarters
q1_sorted = sorted(q_prec[0].items(), key=lambda x: -x[1])
q1_top20_syms = [s for s, _ in q1_sorted[:20]]
print(f"\n  Q1 Top-20 symbols: {', '.join(q1_top20_syms[:10])}...")

top20_tracking = {}
for q in range(4):
    q_sorted = sorted(q_prec[q].items(), key=lambda x: -x[1])
    q_rank_map = {sym: rank + 1 for rank, (sym, _) in enumerate(q_sorted)}
    n_total_q = len(q_sorted)
    found_ranks = [q_rank_map[s] for s in q1_top20_syms if s in q_rank_map]
    if found_ranks:
        avg_rank = np.mean(found_ranks)
        med_rank = np.median(found_ranks)
        top20_tracking[f"Q{q+1}"] = {
            "found_in_quarter": len(found_ranks),
            "avg_rank": round(float(avg_rank), 1),
            "median_rank": round(float(med_rank), 1),
            "total_ranked_symbols": n_total_q,
            "avg_rank_percentile": round(float(avg_rank / n_total_q * 100), 1),
        }
        print(f"  Q1-top-20 in Q{q+1}: {len(found_ranks)}/20 found, avg rank={avg_rank:.1f}/{n_total_q} (percentile={avg_rank/n_total_q*100:.1f}%)")

# ================================================================
# PART 5: Volume Proxy Join
# ================================================================
print(f"\n[{time.time()-t0:.1f}s] === PART 5: Market Cap/Volume Proxy ===")

vol_map = {}
try:
    with open(VOL_PATH, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            vol_map[row["symbol"]] = float(row["avg_daily_vol"])
    print(f"  Loaded {len(vol_map)} symbols with avg daily volume")
except FileNotFoundError:
    print(f"  WARNING: Volume file not found at {VOL_PATH}")

# Join with symbol precision data
sym_with_vol = [(s, vol_map[s["symbol"]]) for s in sym_meaningful if s["symbol"] in vol_map]
print(f"  Joined: {len(sym_with_vol)} symbols with both precision and volume data")

volume_correlation = {}
vol_quintile_stats = {}
if len(sym_with_vol) > 10:
    prec_v = np.array([s["precision"] for s, _ in sym_with_vol])
    vol_v = np.array([v for _, v in sym_with_vol])
    log_vol_v = np.log10(np.clip(vol_v, 1, None))

    r_pe, p_pe = pearson_r(log_vol_v, prec_v)
    r_sp, p_sp = spearman_r(vol_v, prec_v)
    print(f"  Log(Avg Daily Vol) vs Precision:  Pearson r={r_pe:+.4f} (p={p_pe:.4f})  Spearman r={r_sp:+.4f} (p={p_sp:.4f})")

    volume_correlation = {
        "pearson_r_log_vol": round(r_pe, 4),
        "pearson_p_log_vol": round(p_pe, 6),
        "spearman_r": round(r_sp, 4),
        "spearman_p": round(p_sp, 6),
    }

    # Volume quintiles
    sorted_by_vol = sorted(sym_with_vol, key=lambda x: x[1])
    q_size_v = len(sorted_by_vol) // 5
    q_labels = ["Q1_lowest", "Q2", "Q3", "Q4", "Q5_highest"]
    for qi, ql in enumerate(q_labels):
        start = qi * q_size_v
        end = (qi + 1) * q_size_v if qi < 4 else len(sorted_by_vol)
        q_syms = sorted_by_vol[start:end]
        n_ev = sum(s["n_events"] for s, _ in q_syms)
        n_win = sum(s["n_winners"] for s, _ in q_syms)
        vq_prec = n_win / n_ev if n_ev > 0 else 0
        avg_ret = float(np.mean([s["avg_fwd_ret_24h"] for s, _ in q_syms]))
        vol_quintile_stats[ql] = {
            "n_symbols": len(q_syms),
            "n_events": n_ev,
            "precision": round(vq_prec, 4),
            "avg_fwd_ret_24h": round(avg_ret, 6),
        }
        print(f"    {ql:12s}  symbols={len(q_syms):4d}  events={n_ev:6d}  precision={vq_prec:.4f}  avg_ret={avg_ret:.5f}")

    volume_correlation["quintile_stats"] = vol_quintile_stats
else:
    volume_correlation = {"note": "insufficient join data"}

# ================================================================
# ASSEMBLE OUTPUT
# ================================================================
print(f"\n[{time.time()-t0:.1f}s] === Assembling output ===")

valid_rank_corrs = [rc["spearman_r"] for rc in rank_corrs if rc.get("spearman_r") is not None]
avg_rank_corr = float(np.mean(valid_rank_corrs)) if valid_rank_corrs else 0.0
precision_spread = prec_dist["spread_p90_minus_p10"]
stability_verdict = "persistent" if avg_rank_corr > 0.3 else ("weak" if avg_rank_corr > 0.15 else "not persistent")

# Build recommendation
if stability_verdict == "persistent" and precision_spread > 0.15:
    recommendation = (
        f"Symbol quality IS persistent (avg quarter Spearman r={avg_rank_corr:.3f}) with wide precision spread "
        f"({precision_spread:.3f}). A symbol-quality filter is viable. Tier 1 symbols cover "
        f"{tier1_cov:.1f}% of events at {tier_summary.get('Tier1_gt60', {}).get('precision', 0):.1%} precision."
    )
elif stability_verdict == "weak":
    recommendation = (
        f"Symbol quality shows WEAK persistence (avg quarter Spearman r={avg_rank_corr:.3f}). "
        f"Precision spread is {precision_spread:.3f}. A static symbol filter has limited value; "
        f"consider rolling/adaptive symbol scoring instead of fixed tiers."
    )
else:
    recommendation = (
        f"Symbol quality is NOT persistent (avg quarter Spearman r={avg_rank_corr:.3f}). "
        f"Despite precision spread of {precision_spread:.3f}, symbol quality shuffles across quarters. "
        f"A static filter would not generalize. Regime-conditional symbol scoring might work."
    )

# Simplify top20/bot20 for JSON (select key fields)
def slim_sym(s):
    return {
        "symbol": s["symbol"],
        "n_events": s["n_events"],
        "precision": round(s["precision"], 4),
        "avg_fwd_ret_24h": round(s["avg_fwd_ret_24h"], 6),
        "avg_mfe_24h": round(s["avg_mfe_24h"], 6),
        "avg_mae_24h": round(s["avg_mae_24h"], 6),
        "precision_stability": round(s["precision_stability"], 4),
    }

output = {
    "hunt": "symbol_quality",
    "session": "S3_B",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "cost_threshold_bps": COST_THRESHOLD_BPS,
    "min_events_per_symbol": MIN_EVENTS,
    "total_ignition_events": n_ignition,
    "total_symbols_analyzed": len(sym_meaningful),
    "total_symbols_all": len(sym_stats),

    "part1_precision_distribution": {
        **prec_dist,
        "top20_symbols": [slim_sym(s) for s in top20],
        "bottom20_symbols": [slim_sym(s) for s in bot20],
    },

    "part2_predictors_of_precision": correlations,

    "part3_tier_filter": {
        "tiers": tier_summary,
        "total_events_in_meaningful_symbols": total_events,
        "tier1_coverage_pct": round(tier1_cov, 2),
    },

    "part4_temporal_stability": {
        "quarter_labels": quarter_labels,
        "adjacent_quarter_rank_correlations": rank_corrs,
        "avg_spearman_r_across_transitions": round(avg_rank_corr, 4),
        "q1_top20_tracking": top20_tracking,
        "stability_verdict": stability_verdict,
    },

    "part5_volume_proxy": volume_correlation,

    "verdict": {
        "precision_spread_p90_p10": round(precision_spread, 4),
        "temporal_stability": stability_verdict,
        "avg_quarter_rank_correlation": round(avg_rank_corr, 4),
        "recommendation": recommendation,
    },
}

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w") as f:
    json.dump(output, f, indent=2, default=str)

elapsed = time.time() - t0
print(f"\n[{elapsed:.1f}s] === DONE ===")
print(f"Output: {OUT_PATH}")
print(f"Runtime: {elapsed:.1f}s")
print(f"\n=== VERDICT ===")
print(recommendation)
