#!/usr/bin/env python3
"""
Gap-Through Diagnostic — 2R Cap Impact Analysis
=================================================

NO CODE CHANGE diagnostic. Analyzes existing D1 simulation results to determine:
  1. How often STOP_LOSS exits are gap-throughs (bar opens past the stop)
  2. How often the 2R cap fires (gap severity > 2R)
  3. What the PF impact of the cap is

A gap-through occurs when a bar opens beyond the stop price (long: open < stop,
short: open > stop). The exit_checker caps the worst fill at stop_price +/- 2R,
so the actual fill is max(open, stop - 2R) for longs and min(open, stop + 2R)
for shorts.

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/gap_through_diagnostic.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# Docker path resolution
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def get_db_url() -> str:
    """Detect Docker vs host and return the appropriate DB URL."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Host fallback — production DB on localhost
    return "postgresql://trading:trading@localhost:5432/trading"


def main():
    t0 = time.time()
    db_url = get_db_url()

    # Output path
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if os.path.isdir("/app"):
        output_dir = "/scripts/output"
    else:
        output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"gap_through_diagnostic_{ts_str}.json")
    log.info(f"Output will be written to: {output_path}")

    # ── Step 1: Fetch all STOP_LOSS exits from latest D1 simulation run ──
    log.info("Querying STOP_LOSS exits from D1 simulation results...")

    query_stops = """
    SELECT * FROM (
        SELECT DISTINCT ON (sr.opportunity_id)
            sr.opportunity_id,
            sr.symbol,
            sr.timeframe,
            sr.direction,
            sr.entry_price,
            sr.entry_price_with_slippage,
            sr.exit_price,
            sr.exit_price_with_slippage,
            sr.exit_reason,
            sr.r_value,
            sr.stop_price,
            sr.pnl_net,
            sr.bars_held,
            sr.exit_ts
        FROM simulation_results sr
        WHERE sr.exit_reason = 'STOP_LOSS'
          AND sr.setup_id = 'D1'
          AND sr.data_available = true
        ORDER BY sr.opportunity_id, sr.created_at DESC
    ) sub
    ORDER BY sub.exit_ts
    """

    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query_stops)
            stops = cur.fetchall()

    log.info(f"Found {len(stops)} STOP_LOSS exits (deduplicated by opportunity_id)")

    if not stops:
        log.warning("No STOP_LOSS exits found. Exiting.")
        result = {"error": "No STOP_LOSS exits found for D1", "total_stop_loss_exits": 0}
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        return

    # ── Step 2: For each stop-loss, fetch exit bar OHLCV ─────────────────
    log.info("Fetching exit bar OHLCV for each STOP_LOSS trade...")

    query_ohlcv = """
    SELECT open, high, low, close
    FROM ohlcv
    WHERE symbol = %s AND timeframe = %s
      AND bar_start_ts = %s
    LIMIT 1
    """

    gap_throughs = []
    non_gap_throughs = []
    missing_ohlcv = 0
    missing_r_value = 0

    batch_size = 500
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for i, trade in enumerate(stops):
                if (i + 1) % 200 == 0 or i == 0:
                    log.info(f"  Processing trade {i + 1}/{len(stops)}...")

                r_value = trade["r_value"]
                if r_value is None or r_value <= 0:
                    missing_r_value += 1
                    continue

                direction = trade["direction"]
                entry_price = trade["entry_price_with_slippage"]
                stop_price = trade["stop_price"]
                exit_ts = trade["exit_ts"]

                if stop_price is None:
                    # Reconstruct from entry + r_value
                    if direction == "long":
                        stop_price = entry_price - r_value
                    else:
                        stop_price = entry_price + r_value

                # Fetch exit bar OHLCV
                cur.execute(query_ohlcv, (trade["symbol"], trade["timeframe"], exit_ts))
                bar = cur.fetchone()

                if bar is None:
                    missing_ohlcv += 1
                    continue

                bar_open = bar["open"]

                # Determine if gap-through
                if direction == "long":
                    is_gap = bar_open < stop_price
                else:
                    is_gap = bar_open > stop_price

                # Gap severity in R-multiples
                gap_severity_r = abs(bar_open - stop_price) / r_value if is_gap else 0.0

                # Was the 2R cap applied?
                was_capped = is_gap and gap_severity_r > 2.0

                # Calculate cap impact (how much the 2R cap helped)
                cap_impact = 0.0
                if was_capped:
                    if direction == "long":
                        # Without cap: fill at bar_open
                        # With cap: fill at max(bar_open, stop_price - 2*r_value)
                        worst_fill_capped = stop_price - 2 * r_value
                        # The cap RAISES the fill for longs (less negative)
                        cap_impact = worst_fill_capped - bar_open  # positive = cap helped
                    else:
                        # Without cap: fill at bar_open
                        # With cap: fill at min(bar_open, stop_price + 2*r_value)
                        worst_fill_capped = stop_price + 2 * r_value
                        # The cap LOWERS the fill for shorts (less negative)
                        cap_impact = bar_open - worst_fill_capped  # positive = cap helped

                trade_info = {
                    "opportunity_id": trade["opportunity_id"],
                    "symbol": trade["symbol"],
                    "timeframe": trade["timeframe"],
                    "direction": direction,
                    "entry_price": entry_price,
                    "stop_price": stop_price,
                    "exit_price": trade["exit_price"],
                    "exit_price_with_slippage": trade["exit_price_with_slippage"],
                    "bar_open": bar_open,
                    "r_value": r_value,
                    "pnl_net": trade["pnl_net"],
                    "bars_held": trade["bars_held"],
                    "exit_ts": str(exit_ts),
                    "is_gap_through": is_gap,
                    "gap_severity_r": round(gap_severity_r, 4),
                    "was_capped": was_capped,
                    "cap_impact_usd": round(cap_impact, 6),
                }

                if is_gap:
                    gap_throughs.append(trade_info)
                else:
                    non_gap_throughs.append(trade_info)

    elapsed = time.time() - t0
    log.info(f"OHLCV lookup complete in {elapsed:.1f}s")
    log.info(f"  Gap-throughs: {len(gap_throughs)}")
    log.info(f"  Non gap-throughs: {len(non_gap_throughs)}")
    log.info(f"  Missing OHLCV: {missing_ohlcv}")
    log.info(f"  Missing/zero R-value: {missing_r_value}")

    # ── Step 3: Compute statistics ───────────────────────────────────────
    total_analyzed = len(gap_throughs) + len(non_gap_throughs)
    gap_through_count = len(gap_throughs)
    gap_through_pct = (gap_through_count / total_analyzed * 100) if total_analyzed > 0 else 0

    capped_trades = [t for t in gap_throughs if t["was_capped"]]
    capped_count = len(capped_trades)
    capped_pct = (capped_count / gap_through_count * 100) if gap_through_count > 0 else 0

    # Gap severity distribution
    severity_buckets = {
        "0-0.5R": 0,
        "0.5-1R": 0,
        "1-2R": 0,
        "2-3R": 0,
        "3-5R": 0,
        "5R+": 0,
    }
    for t in gap_throughs:
        s = t["gap_severity_r"]
        if s < 0.5:
            severity_buckets["0-0.5R"] += 1
        elif s < 1.0:
            severity_buckets["0.5-1R"] += 1
        elif s < 2.0:
            severity_buckets["1-2R"] += 1
        elif s < 3.0:
            severity_buckets["2-3R"] += 1
        elif s < 5.0:
            severity_buckets["3-5R"] += 1
        else:
            severity_buckets["5R+"] += 1

    # Cap impact metrics
    total_cap_impact_usd = sum(t["cap_impact_usd"] for t in capped_trades)
    cap_impacts_r = []
    for t in capped_trades:
        if t["r_value"] > 0:
            cap_impacts_r.append(t["cap_impact_usd"] / t["r_value"])
    avg_cap_impact_r = (sum(cap_impacts_r) / len(cap_impacts_r)) if cap_impacts_r else 0

    # Sample capped trades (top 10 by gap severity)
    sample_capped = sorted(capped_trades, key=lambda t: t["gap_severity_r"], reverse=True)[:10]
    sample_capped_clean = [
        {
            "symbol": t["symbol"],
            "timeframe": t["timeframe"],
            "direction": t["direction"],
            "gap_severity_r": t["gap_severity_r"],
            "cap_impact_usd": t["cap_impact_usd"],
            "bar_open": t["bar_open"],
            "stop_price": t["stop_price"],
            "r_value": t["r_value"],
            "exit_ts": t["exit_ts"],
        }
        for t in sample_capped
    ]

    # ── Step 4: Build output ─────────────────────────────────────────────
    result = {
        "diagnostic": "gap_through_2R_cap_analysis",
        "setup_id": "D1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_stop_loss_exits": len(stops),
        "analyzed": total_analyzed,
        "skipped_missing_ohlcv": missing_ohlcv,
        "skipped_missing_r_value": missing_r_value,
        "gap_through_count": gap_through_count,
        "gap_through_pct": round(gap_through_pct, 2),
        "capped_count": capped_count,
        "capped_pct_of_gap_throughs": round(capped_pct, 2),
        "capped_pct_of_all_stops": round(
            (capped_count / total_analyzed * 100) if total_analyzed > 0 else 0, 2
        ),
        "gap_severity_distribution": severity_buckets,
        "total_cap_impact_usd": round(total_cap_impact_usd, 4),
        "avg_cap_impact_r": round(avg_cap_impact_r, 4),
        "sample_capped_trades": sample_capped_clean,
    }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    elapsed_total = time.time() - t0
    log.info(f"Done in {elapsed_total:.1f}s. Output written to: {output_path}")

    # ── Print summary to console ─────────────────────────────────────────
    log.info("=" * 60)
    log.info("GAP-THROUGH DIAGNOSTIC SUMMARY")
    log.info("=" * 60)
    log.info(f"Total STOP_LOSS exits (D1):    {len(stops)}")
    log.info(f"Analyzed (had OHLCV + R):      {total_analyzed}")
    log.info(f"Gap-throughs:                  {gap_through_count} ({gap_through_pct:.1f}%)")
    log.info(f"2R cap fired:                  {capped_count} ({capped_pct:.1f}% of gap-throughs)")
    log.info(f"Total cap impact (USD):        {total_cap_impact_usd:.4f}")
    log.info(f"Avg cap impact (R-multiples):  {avg_cap_impact_r:.4f}")
    log.info("-" * 60)
    log.info("Gap severity distribution:")
    for bucket, count in severity_buckets.items():
        pct = (count / gap_through_count * 100) if gap_through_count > 0 else 0
        log.info(f"  {bucket:>8s}: {count:>5d} ({pct:>5.1f}%)")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
