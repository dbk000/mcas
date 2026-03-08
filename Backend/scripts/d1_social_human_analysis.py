#!/usr/bin/env python3
"""
Workstream 3: D1 Social Silence — Human-Only Analysis (Bot + Content Farm Excluded)
====================================================================================
Re-runs the B1 social silence analysis but queries social_posts directly
(not social_features_1h), excluding all bot and content farm channels.

This isolates genuine human discussion from algorithmic noise.

Excluded channels:
  - PUMP_SIGNAL_CHANNELS (WhaleBotAlerts, WhaleBotPumps, whale_alert_io, etc.)
  - CONTENT_FARM_CHANNELS (CoinOtag, Bitcoin World, TimesTabloid, etc.)

Output: scripts/output/d1_social_human_analysis_{ts}.json

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d1_social_human_analysis.py
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

if os.path.isdir("/app"):
    sys.path.insert(0, "/app")

import psycopg
from psycopg.rows import dict_row
from scipy.stats import mannwhitneyu

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    if os.path.isdir("/app"):
        print("ERROR: DATABASE_URL not set inside Docker")
        sys.exit(1)
    DATABASE_URL = "postgresql://trading:trading@localhost:5432/trading"

TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
OUTPUT_DIR = "/scripts/output" if os.path.isdir("/app") else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "output"
)
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_PATH = os.path.join(OUTPUT_DIR, f"d1_social_human_analysis_{TIMESTAMP}.json")

print(f"[START] D1 Social Silence — Human-Only Analysis")
print(f"[OUTPUT] {OUTPUT_PATH}")

# ---------------------------------------------------------------------------
# Channel exclusion lists
# ---------------------------------------------------------------------------
EXCLUDED_CHANNELS = [
    # Pump signal bots
    ("telegram_historical", "WhaleBotAlerts"),
    ("telegram_historical", "WhaleBotPumps"),
    ("telegram_historical", "whale_alert_io"),
    ("telegram_historical", "WhaleBotRektd"),
    ("telegram_historical", "CryptoScanner100eyes"),
    # Content farms
    ("news_historical", "CoinOtag"),
    ("news_historical", "Bitcoin World"),
    ("news_historical", "TimesTabloid"),
    ("news_historical", "Crypto Daily"),
    ("news_historical", "Coin Edition"),
    ("news_historical", "U.Today"),
    ("telegram_historical", "coingeckonews"),
    ("coingecko", "trending"),
    # Exchange announcements
    ("telegram_historical", "Kucoin_News"),
    ("telegram_historical", "binance_announcements"),
    ("telegram_historical", "CryptoComOfficialAnnouncements"),
    ("telegram_historical", "Bybit_Announcements"),
]

LOOKBACK_DAYS = [1, 3, 7, 14, 21, 30]


def classify_trade(row: dict) -> str:
    if row["pnl_net"] is not None and float(row["pnl_net"]) > 0:
        return "Winner"
    if row["exit_reason"] == "STOP_LOSS" and row["bars_held"] is not None and row["bars_held"] <= 3:
        return "Early SL"
    return "Late Exit"


def main():
    t_start = time.time()
    conn = psycopg.connect(DATABASE_URL)

    try:
        # ── Step 1: Load D1 simulation results ──
        print("[1/5] Fetching D1 simulation results...")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT DISTINCT ON (sr.opportunity_id)
                    sr.opportunity_id, sr.symbol, sr.timeframe,
                    sr.direction, sr.exit_reason, sr.pnl_net,
                    sr.r_multiple, sr.bars_held,
                    sr.opportunity_created_ts AS trigger_ts
                FROM simulation_results sr
                WHERE sr.setup_id = 'D1' AND sr.data_available = true
                ORDER BY sr.opportunity_id, sr.created_at DESC
            """)
            all_opps = cur.fetchall()
        print(f"       {len(all_opps)} D1 opportunities loaded")

        # ── Step 2: Build Honest Subset ──
        # For human-only analysis: symbol must have at least ONE human social post
        print("[2/5] Building Honest Subset (human posts, not social_features_1h)...")
        d1_symbols = sorted(set(opp["symbol"] for opp in all_opps))

        # Build exclusion WHERE clause
        exclusion_values = ", ".join(
            f"('{src}', '{ch}')" for src, ch in EXCLUDED_CHANNELS
        )

        with conn.cursor() as cur:
            # Symbols with aliases
            cur.execute("SELECT DISTINCT symbol FROM symbol_aliases")
            symbols_with_aliases = {row[0] for row in cur}

            # Symbols with at least one HUMAN social post
            cur.execute(f"""
                SELECT DISTINCT unnest(symbols) AS symbol
                FROM social_posts
                WHERE (source, channel) NOT IN (VALUES {exclusion_values})
                  AND symbols IS NOT NULL
            """)
            symbols_with_human_posts = {row[0] for row in cur}

        eligible = symbols_with_aliases & symbols_with_human_posts
        all_syms = set(d1_symbols)
        no_alias = all_syms - symbols_with_aliases
        alias_no_human = (all_syms & symbols_with_aliases) - symbols_with_human_posts
        honest_syms = all_syms & eligible

        honest_opps = [opp for opp in all_opps if opp["symbol"] in eligible]

        filter_stats = {
            "total_symbols": len(all_syms),
            "symbols_no_alias": len(no_alias),
            "symbols_alias_but_no_human_posts": len(alias_no_human),
            "symbols_honest": len(honest_syms),
            "opps_before": len(all_opps),
            "opps_after": len(honest_opps),
            "opps_excluded": len(all_opps) - len(honest_opps),
            "exclusion_rate_pct": round((1 - len(honest_opps) / len(all_opps)) * 100, 1) if all_opps else 0,
            "excluded_channels_count": len(EXCLUDED_CHANNELS),
        }
        print(f"       Symbols: {filter_stats['total_symbols']} total → {filter_stats['symbols_honest']} honest")
        print(f"       Opps: {filter_stats['opps_before']} → {filter_stats['opps_after']} "
              f"(excluded {filter_stats['opps_excluded']}, {filter_stats['exclusion_rate_pct']}%)")

        if not honest_opps:
            print("[ABORT] No opportunities survive Honest Subset filter.")
            sys.exit(1)

        # ── Step 3: Load human-only social posts for honest symbols ──
        print("[3/5] Loading human-only social posts for honest symbols...")
        honest_symbols_list = sorted(honest_syms)

        # Query: unnest symbols, filter to honest symbols, exclude bot/farm channels
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT unnest(symbols) AS symbol, post_ts
                FROM social_posts
                WHERE symbols && %s::text[]
                  AND (source, channel) NOT IN (VALUES {exclusion_values})
                  AND symbols IS NOT NULL
                ORDER BY 1, 2
            """, (honest_symbols_list,))
            rows = cur.fetchall()

        # Build per-symbol sorted timestamp lists
        symbol_posts = defaultdict(list)
        honest_syms_lower = honest_syms  # already uppercase USDT-suffixed
        for sym, post_ts in rows:
            if sym in honest_syms:
                symbol_posts[sym].append(post_ts)

        # Sort (should already be sorted from ORDER BY, but be safe)
        for sym in symbol_posts:
            symbol_posts[sym].sort()

        symbols_with_data = len(symbol_posts)
        total_human_posts = sum(len(v) for v in symbol_posts.values())
        print(f"       {total_human_posts:,} human post-symbol mentions across {symbols_with_data} symbols")

        # ── Step 4: Compute window stats ──
        print("[4/5] Computing mention stats per window...")

        classified = []
        for opp in honest_opps:
            group = classify_trade(opp)
            classified.append({"opp": opp, "group": group})

        group_counts = defaultdict(int)
        for item in classified:
            group_counts[item["group"]] += 1
        print(f"       Groups: {dict(group_counts)}")

        max_lb = max(LOOKBACK_DAYS)
        any_data = sum(1 for opp in honest_opps if opp["symbol"] in symbol_posts)
        coverage_pct = any_data / len(honest_opps) * 100 if honest_opps else 0

        results = {
            "version": "v3_human_only",
            "total_opportunities": len(honest_opps),
            "group_counts": dict(group_counts),
            "social_coverage_pct": round(coverage_pct, 2),
            "social_coverage_description": f"{any_data}/{len(honest_opps)} honest opps have ≥1 human post",
            "excluded_channels": [f"{s}/{c}" for s, c in EXCLUDED_CHANNELS],
            "lookback_windows": {},
        }

        for lb in LOOKBACK_DAYS:
            group_mentions = defaultdict(list)

            for item in classified:
                opp = item["opp"]
                sym = opp["symbol"]
                trigger_ts = opp["trigger_ts"]
                group = item["group"]

                posts_ts = symbol_posts.get(sym, [])
                if not posts_ts:
                    group_mentions[group].append(0)
                    continue

                window_start = trigger_ts - timedelta(days=lb)
                start_idx = bisect.bisect_left(posts_ts, window_start)
                end_idx = bisect.bisect_left(posts_ts, trigger_ts)  # strict < trigger

                mention_count = end_idx - start_idx
                group_mentions[group].append(mention_count)

            window_result = {"lookback_days": lb, "groups": {}}

            for group_name in ["Winner", "Early SL", "Late Exit"]:
                mentions = group_mentions.get(group_name, [])
                if not mentions:
                    window_result["groups"][group_name] = {"count": 0}
                    continue

                n = len(mentions)
                mean_m = sum(mentions) / n
                median_m = statistics.median(mentions)
                zero_count = sum(1 for m in mentions if m == 0)
                pct_zero = zero_count / n * 100

                window_result["groups"][group_name] = {
                    "count": n,
                    "mean_mentions": round(mean_m, 2),
                    "median_mentions": round(median_m, 2),
                    "pct_zero_mentions": round(pct_zero, 2),
                    "p25": round(float(sorted(mentions)[n // 4]), 1),
                    "p75": round(float(sorted(mentions)[3 * n // 4]), 1),
                    "max_mentions": max(mentions),
                }

            # Separation: zero-mention rate
            w = window_result["groups"].get("Winner", {})
            e = window_result["groups"].get("Early SL", {})
            w_zero = w.get("pct_zero_mentions")
            e_zero = e.get("pct_zero_mentions")

            if w_zero is not None and e_zero is not None:
                sep = round(w_zero - e_zero, 2)
                window_result["zero_rate_separation_pp"] = sep
                window_result["separation_note"] = (
                    f"Winners have {sep:+.1f}pp zero-mention rate vs Early SL"
                )
            else:
                window_result["zero_rate_separation_pp"] = None

            # Mann-Whitney U
            w_mentions = group_mentions.get("Winner", [])
            e_mentions = group_mentions.get("Early SL", [])
            if len(w_mentions) >= 5 and len(e_mentions) >= 5:
                stat, pval = mannwhitneyu(w_mentions, e_mentions, alternative="two-sided")
                window_result["mann_whitney"] = {
                    "statistic": round(float(stat), 2),
                    "p_value": round(float(pval), 6),
                    "sig_005": pval < 0.05,
                    "sig_010": pval < 0.10,
                }
            else:
                window_result["mann_whitney"] = {
                    "skipped": True,
                    "reason": f"n_winners={len(w_mentions)}, n_early_sl={len(e_mentions)}",
                }

            # Also test: median mentions comparison
            w_med = w.get("median_mentions")
            e_med = e.get("median_mentions")
            if w_med is not None and e_med is not None:
                window_result["median_comparison"] = {
                    "winners": w_med,
                    "early_sl": e_med,
                    "direction": "winners_higher" if w_med > e_med else "early_sl_higher" if e_med > w_med else "equal",
                }

            results["lookback_windows"][f"{lb}d"] = window_result

        # Overall recommendation
        best_window = None
        best_sep = 0
        any_sig = False
        for lb in LOOKBACK_DAYS:
            key = f"{lb}d"
            w = results["lookback_windows"][key]
            sep = w.get("zero_rate_separation_pp")
            mw = w.get("mann_whitney", {})
            if sep is not None and abs(sep) > abs(best_sep):
                best_sep = sep
                best_window = key
            if mw.get("sig_005"):
                any_sig = True

        if any_sig and abs(best_sep) > 10:
            results["recommendation"] = "A_STRONG_SIGNAL"
            results["recommendation_detail"] = (
                f"p<0.05 AND >10pp separation at {best_window} ({best_sep:+.1f}pp). "
                "Human-only mention count is a viable D1 gate."
            )
        elif any_sig:
            results["recommendation"] = "B_WEAK_SIGNAL"
            results["recommendation_detail"] = (
                f"p<0.05 at some window but separation only {best_sep:+.1f}pp at {best_window}. "
                "Proceed to Workstream 4 (qualitative classification)."
            )
        elif abs(best_sep) > 10:
            results["recommendation"] = "B_WEAK_SIGNAL"
            results["recommendation_detail"] = (
                f">10pp separation at {best_window} ({best_sep:+.1f}pp) but not statistically significant. "
                "Proceed to Workstream 4."
            )
        else:
            results["recommendation"] = "C_NO_SIGNAL"
            results["recommendation_detail"] = (
                f"Best separation: {best_sep:+.1f}pp at {best_window}. No statistical significance. "
                "The signal is qualitative, not quantitative. Proceed to Workstream 4."
            )

    finally:
        conn.close()

    # ── Step 5: Write output ──
    print("[5/5] Writing results...")
    results["honest_subset_filter"] = filter_stats
    results["generated_at"] = datetime.now(timezone.utc).isoformat()
    results["elapsed_seconds"] = round(time.time() - t_start, 1)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Print summary
    print(f"\n{'='*80}")
    print("D1 SOCIAL SILENCE — HUMAN-ONLY ANALYSIS (Bots + Content Farms Excluded)")
    print(f"{'='*80}")
    print(f"Filter: {filter_stats['excluded_channels_count']} channels excluded")
    print(f"  Opps: {filter_stats['opps_before']} → {filter_stats['opps_after']} "
          f"(excluded {filter_stats['opps_excluded']}, {filter_stats['exclusion_rate_pct']}%)")
    print(f"  Social coverage: {results['social_coverage_pct']}%")
    print(f"  Groups: {results['group_counts']}")
    print()

    for lb_key in [f"{d}d" for d in LOOKBACK_DAYS]:
        w = results["lookback_windows"][lb_key]
        print(f"--- {lb_key} lookback ---")
        for gname in ["Winner", "Early SL", "Late Exit"]:
            g = w["groups"].get(gname, {})
            if g.get("count", 0) == 0:
                print(f"  {gname:12s}: no data")
                continue
            print(f"  {gname:12s}: n={g['count']:>5d}  mean={g['mean_mentions']:>8.1f}  "
                  f"median={g['median_mentions']:>8.1f}  zero%={g['pct_zero_mentions']:>5.1f}%")
        sep = w.get("zero_rate_separation_pp")
        if sep is not None:
            print(f"  Separation: {sep:+.1f}pp")
        mw = w.get("mann_whitney", {})
        if not mw.get("skipped"):
            sig = " **" if mw.get("sig_005") else " *" if mw.get("sig_010") else ""
            print(f"  Mann-Whitney: p={mw['p_value']:.6f}{sig}")
        print()

    print(f"Recommendation: {results['recommendation']}")
    print(f"  {results['recommendation_detail']}")
    print(f"\nOutput: {OUTPUT_PATH}")
    print(f"Elapsed: {results['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
