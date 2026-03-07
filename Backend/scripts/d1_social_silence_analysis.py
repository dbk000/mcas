#!/usr/bin/env python3
"""
D1 Social Silence Analysis
===========================
Analyzes whether social silence (low/zero social mentions) before a D1
opportunity predicts trade quality (winner vs early stop-loss vs late exit).

Queries social_features_1h for multiple lookback windows and compares
mention distributions across trade outcome groups.

Run inside Docker:
    cd /home/darren/Desktop/MCAS/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d1_social_silence_analysis.py
"""

from __future__ import annotations

import bisect
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# ── Path setup for Docker ──────────────────────────────────────────────
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")

import psycopg
from psycopg.rows import dict_row

# ── Database URL ───────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    if os.path.isdir("/app"):
        print("ERROR: DATABASE_URL not set inside Docker")
        sys.exit(1)
    DATABASE_URL = "postgresql://trading:trading@localhost:5432/trading"

# ── Output path ────────────────────────────────────────────────────────
TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
if os.path.isdir("/app"):
    OUTPUT_DIR = "/scripts/output"
else:
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_PATH = os.path.join(OUTPUT_DIR, f"d1_social_silence_analysis_{TIMESTAMP}.json")

print(f"[START] D1 Social Silence Analysis")
print(f"[OUTPUT] Results will be written to: {OUTPUT_PATH}")
print(f"[DB] {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")

# ── Configuration ──────────────────────────────────────────────────────
LOOKBACK_DAYS = [1, 3, 7, 14, 21, 30]
SOCIAL_QUERY_BATCH_SIZE = 200  # symbols per batch


def fetch_d1_simulation_results(conn) -> list[dict]:
    """Fetch latest simulation result per D1 opportunity."""
    print("[1/4] Fetching D1 simulation results (latest run per opportunity)...")
    t0 = time.time()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT DISTINCT ON (sr.opportunity_id)
                sr.opportunity_id,
                sr.symbol,
                sr.timeframe,
                sr.direction,
                sr.entry_price_with_slippage,
                sr.exit_reason,
                sr.pnl_net,
                sr.r_multiple,
                sr.bars_held,
                sr.opportunity_created_ts AS trigger_bar_ts
            FROM simulation_results sr
            WHERE sr.setup_id = 'D1'
              AND sr.data_available = true
            ORDER BY sr.opportunity_id, sr.created_at DESC
        """)
        results = cur.fetchall()
    elapsed = time.time() - t0
    print(f"       Fetched {len(results)} D1 opportunities in {elapsed:.1f}s")
    return results


def classify_trade(row: dict) -> str:
    """Classify trade into Winner / Early SL / Late Exit."""
    if row["pnl_net"] is not None and row["pnl_net"] > 0:
        return "Winner"
    if row["exit_reason"] == "STOP_LOSS" and row["bars_held"] is not None and row["bars_held"] <= 3:
        return "Early SL"
    return "Late Exit"


def fetch_social_mentions_batched(
    conn,
    opportunities: list[dict],
    lookback_days: list[int],
) -> dict[tuple[int, int], dict]:
    """
    Batch-query social mentions for all opportunities across all lookback windows.

    Returns dict keyed by (opportunity_id, lookback_days) -> {total_mentions, hours_with_data}.
    """
    print("[2/4] Querying social_features_1h in batches...")
    t0 = time.time()

    # Build unique (symbol, trigger_ts) pairs
    opp_list = []
    for opp in opportunities:
        trigger_ts = opp["trigger_bar_ts"]
        opp_list.append({
            "opportunity_id": opp["opportunity_id"],
            "symbol": opp["symbol"],
            "trigger_ts": trigger_ts,
        })

    # Group by symbol to batch efficiently
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for item in opp_list:
        by_symbol[item["symbol"]].append(item)

    symbols = sorted(by_symbol.keys())
    max_lookback = max(lookback_days)

    # Results: (opportunity_id, lookback_days) -> {total_mentions, hours_with_data}
    mention_data: dict[tuple[int, int], dict] = {}

    # Initialize all to zero
    for opp in opp_list:
        for lb in lookback_days:
            mention_data[(opp["opportunity_id"], lb)] = {
                "total_mentions": 0,
                "hours_with_data": 0,
            }

    # Process in batches of symbols
    total_symbols = len(symbols)
    processed = 0

    for batch_start in range(0, total_symbols, SOCIAL_QUERY_BATCH_SIZE):
        batch_symbols = symbols[batch_start : batch_start + SOCIAL_QUERY_BATCH_SIZE]

        # For each symbol in batch, find the earliest trigger_ts minus max_lookback
        # and latest trigger_ts to bound the query
        with conn.cursor() as cur:
            for symbol in batch_symbols:
                opps_for_symbol = by_symbol[symbol]

                # Get the time range we need for this symbol
                trigger_times = [o["trigger_ts"] for o in opps_for_symbol]
                earliest_need = min(trigger_times)
                latest_need = max(trigger_times)

                # Fetch all social data for this symbol in the full needed range
                # Social data from the trigger bar is excluded — it captures the
                # reaction to the breakout, not the pre-breakout silence.
                cur.execute("""
                    SELECT bar_start_ts, mention_count
                    FROM social_features_1h
                    WHERE symbol = %s
                      AND bar_start_ts >= %s - %s * INTERVAL '1 day'
                      AND bar_start_ts < %s
                    ORDER BY bar_start_ts
                """, (symbol, earliest_need, max_lookback, latest_need))

                social_rows = cur.fetchall()

                # Sort social data by timestamp for bisect lookups
                # social_rows is already ORDER BY bar_start_ts from the query
                social_sorted_ts = [row[0] for row in social_rows]
                social_sorted_mc = [row[1] for row in social_rows]

                # For each opportunity of this symbol, compute mentions per lookback
                for opp in opps_for_symbol:
                    trigger_ts = opp["trigger_ts"]
                    oid = opp["opportunity_id"]

                    # Social data from the trigger bar is excluded — it captures the
                    # reaction to the breakout, not the pre-breakout silence.
                    # Upper bound: strict < trigger_ts
                    end_idx = bisect.bisect_left(social_sorted_ts, trigger_ts)

                    for lb in lookback_days:
                        window_start = trigger_ts - timedelta(days=lb)
                        start_idx = bisect.bisect_left(social_sorted_ts, window_start)

                        # Slice [start_idx:end_idx] gives all rows in window
                        window_mentions = social_sorted_mc[start_idx:end_idx]
                        total_mentions = sum(window_mentions)
                        hours_with_data = len(window_mentions)

                        mention_data[(oid, lb)] = {
                            "total_mentions": total_mentions,
                            "hours_with_data": hours_with_data,
                        }

        processed += len(batch_symbols)
        elapsed = time.time() - t0
        print(f"       Processed {processed}/{total_symbols} symbols ({elapsed:.1f}s)")

    total_elapsed = time.time() - t0
    print(f"       Social query complete in {total_elapsed:.1f}s")
    return mention_data


def compute_statistics(
    opportunities: list[dict],
    mention_data: dict[tuple[int, int], dict],
    lookback_days: list[int],
) -> dict:
    """Compute per-group statistics for each lookback window."""
    print("[3/4] Computing statistics...")

    # Classify trades
    classified = []
    for opp in opportunities:
        group = classify_trade(opp)
        classified.append({"opp": opp, "group": group})

    group_counts = defaultdict(int)
    for item in classified:
        group_counts[item["group"]] += 1
    print(f"       Groups: {dict(group_counts)}")

    # Check social coverage
    any_social_data = 0
    max_lb = max(lookback_days)
    for opp in opportunities:
        key = (opp["opportunity_id"], max_lb)
        if mention_data[key]["hours_with_data"] > 0:
            any_social_data += 1
    coverage_pct = (any_social_data / len(opportunities) * 100) if opportunities else 0

    results = {
        "total_opportunities": len(opportunities),
        "group_counts": dict(group_counts),
        "social_data_coverage": round(coverage_pct, 2),
        "social_data_coverage_description": f"{any_social_data}/{len(opportunities)} opportunities have social data in {max_lb}-day lookback",
        "lookback_windows": {},
    }

    for lb in lookback_days:
        # Gather mentions per group
        group_mentions: dict[str, list[int]] = defaultdict(list)
        for item in classified:
            oid = item["opp"]["opportunity_id"]
            key = (oid, lb)
            total_mentions = mention_data[key]["total_mentions"]
            group_mentions[item["group"]].append(total_mentions)

        window_result = {"lookback_days": lb, "groups": {}}

        for group_name in ["Winner", "Early SL", "Late Exit"]:
            mentions = group_mentions.get(group_name, [])
            if not mentions:
                window_result["groups"][group_name] = {
                    "count": 0,
                    "mean_mentions": None,
                    "median_mentions": None,
                    "pct_zero_mentions": None,
                }
                continue

            n = len(mentions)
            mean_m = sum(mentions) / n
            sorted_m = sorted(mentions)
            median_m = statistics.median(sorted_m)
            zero_count = sum(1 for m in mentions if m == 0)
            pct_zero = zero_count / n * 100

            window_result["groups"][group_name] = {
                "count": n,
                "mean_mentions": round(mean_m, 2),
                "median_mentions": round(median_m, 2),
                "pct_zero_mentions": round(pct_zero, 2),
            }

        # Separation metric: zero-mention rate difference (Winners vs Early SL)
        w_stats = window_result["groups"].get("Winner", {})
        e_stats = window_result["groups"].get("Early SL", {})
        w_zero = w_stats.get("pct_zero_mentions")
        e_zero = e_stats.get("pct_zero_mentions")

        if w_zero is not None and e_zero is not None:
            separation_pp = round(w_zero - e_zero, 2)
            window_result["zero_mention_rate_separation_pp"] = separation_pp
            window_result["separation_description"] = (
                f"Winners have {separation_pp:+.1f}pp zero-mention rate vs Early SL"
            )
        else:
            window_result["zero_mention_rate_separation_pp"] = None
            window_result["separation_description"] = "Insufficient data for separation"

        # Statistical test: Mann-Whitney U (Winners vs Early SL)
        try:
            from scipy.stats import mannwhitneyu

            w_mentions = group_mentions.get("Winner", [])
            e_mentions = group_mentions.get("Early SL", [])
            if len(w_mentions) >= 5 and len(e_mentions) >= 5:
                stat, p_value = mannwhitneyu(
                    w_mentions, e_mentions, alternative="two-sided"
                )
                window_result["mann_whitney_u"] = {
                    "statistic": round(float(stat), 2),
                    "p_value": round(float(p_value), 6),
                    "significant_at_005": p_value < 0.05,
                    "significant_at_010": p_value < 0.10,
                }
            else:
                window_result["mann_whitney_u"] = {
                    "skipped": True,
                    "reason": f"Insufficient samples (Winners={len(w_mentions)}, Early SL={len(e_mentions)}; need >=5 each)",
                }
        except ImportError:
            window_result["mann_whitney_u"] = {
                "skipped": True,
                "reason": "scipy not available",
            }

        results["lookback_windows"][f"{lb}d"] = window_result

    # Overall recommendation
    any_significant_separation = False
    for lb in lookback_days:
        key = f"{lb}d"
        sep = results["lookback_windows"][key].get("zero_mention_rate_separation_pp")
        if sep is not None and abs(sep) > 10:
            any_significant_separation = True
            break

    results["recommendation"] = (
        "PROCEED" if any_significant_separation else "INSUFFICIENT_SEPARATION"
    )
    results["recommendation_detail"] = (
        "At least one lookback window shows >10pp zero-mention rate difference between Winners and Early SL"
        if any_significant_separation
        else "No lookback window shows >10pp zero-mention rate difference between Winners and Early SL"
    )

    return results


def main():
    t_start = time.time()

    conn = psycopg.connect(DATABASE_URL)
    try:
        # Step 1: Fetch D1 simulation results
        opportunities = fetch_d1_simulation_results(conn)
        if not opportunities:
            print("[ABORT] No D1 simulation results found.")
            sys.exit(1)

        # Step 2: Batch-query social mentions
        mention_data = fetch_social_mentions_batched(conn, opportunities, LOOKBACK_DAYS)

        # Step 3: Compute statistics
        results = compute_statistics(opportunities, mention_data, LOOKBACK_DAYS)
    finally:
        conn.close()

    # Step 4: Write output
    print("[4/4] Writing results...")
    results["generated_at"] = datetime.now(timezone.utc).isoformat()
    results["elapsed_seconds"] = round(time.time() - t_start, 1)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"Total opportunities:    {results['total_opportunities']}")
    print(f"Social data coverage:   {results['social_data_coverage']}% ({results['social_data_coverage_description']})")
    print(f"Group counts:           {results['group_counts']}")
    print(f"Recommendation:         {results['recommendation']}")
    print(f"  Detail:               {results['recommendation_detail']}")
    print()

    for lb_key, window in results["lookback_windows"].items():
        print(f"--- {lb_key} lookback ---")
        for group_name, stats in window["groups"].items():
            if stats["count"] == 0:
                print(f"  {group_name:12s}: no data")
                continue
            print(
                f"  {group_name:12s}: n={stats['count']:>5d}  "
                f"mean={stats['mean_mentions']:>8.1f}  "
                f"median={stats['median_mentions']:>8.1f}  "
                f"zero%={stats['pct_zero_mentions']:>5.1f}%"
            )
        sep = window.get("zero_mention_rate_separation_pp")
        if sep is not None:
            print(f"  Separation: {sep:+.1f}pp (Winner zero% - Early SL zero%)")
        mwu = window.get("mann_whitney_u", {})
        if not mwu.get("skipped", False):
            print(f"  Mann-Whitney U: stat={mwu['statistic']}, p={mwu['p_value']:.6f} {'*' if mwu['significant_at_005'] else ''}")
        elif "reason" in mwu:
            print(f"  Mann-Whitney U: skipped ({mwu['reason']})")
        print()

    print(f"Output written to: {OUTPUT_PATH}")
    print(f"Elapsed: {results['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
