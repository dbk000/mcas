#!/usr/bin/env python3
"""
S9 Causality Audit
===================

Proves causal integrity of S9 backfilled opportunities by computing
data_leak_seconds for every opp: the gap between the latest market data
timestamp used and the opportunity's created_ts.

If data_leak_seconds > 0, we have a time-travel leak (future data used).

Output: scripts/output/s9_causality_proof_{timestamp}.csv
        + summary stats to stdout

Run:
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/s9_causality_audit.py [--limit 1000]
"""

import os
import sys
import csv
import json
import argparse
import logging
from datetime import datetime, timezone

if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    OUTPUT_DIR = "/scripts/output"
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))
    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

logger = logging.getLogger("s9_causality_audit")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

DATABASE_URL = os.getenv("DATABASE_URL")


def run_audit(limit: int = 1000):
    import psycopg

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUTPUT_DIR, f"s9_causality_proof_{ts_tag}.csv")
    logger.info("Output: %s", out_path)

    conn = psycopg.connect(DATABASE_URL)
    cur = conn.cursor()

    # Query S9 expired opps ordered by created_ts DESC (most recent backfill first)
    cur.execute("""
        SELECT id, symbol, created_ts, payload_json
        FROM opportunities
        WHERE setup_id = 'S9' AND status = 'expired'
        ORDER BY created_ts DESC
        LIMIT %s
    """, (limit,))

    rows = cur.fetchall()
    logger.info("Fetched %d S9 opportunities", len(rows))

    fieldnames = [
        "id", "symbol", "created_ts",
        "trigger_bar_start_ts", "trigger_bar_start_utc",
        "eval_candle_close_utc",
        "as_of_ts", "as_of_utc",
        "micro_entry_type", "suggested_entry_price",
        "micro_n_klines", "micro_tape_end_utc",
        "latest_data_ts", "latest_data_utc",
        "created_epoch",
        "data_leak_seconds",
        "is_strictly_causal",
        "is_upsert_refreshed",
    ]

    results = []
    violations = 0

    for row in rows:
        opp_id, symbol, created_ts, payload_str = row
        payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str

        created_epoch = created_ts.timestamp()

        # --- bar_start_ts: the hour boundary when signal was generated ---
        # This is candle_close = eval_hour + 1h (NOT the eval candle's bar_start_ts)
        bar_start_ts_ms = int(payload.get("bar_start_ts", "0"))
        bar_start_ts_epoch = bar_start_ts_ms / 1000.0
        bar_start_utc = datetime.fromtimestamp(bar_start_ts_epoch, tz=timezone.utc)

        # The eval candle's bar_start_ts = bar_start_ts - 1h
        # The eval candle CLOSES at bar_start_ts (that's when its close price is finalized)
        eval_candle_close_utc = bar_start_utc  # candle close = bar_start_ts

        # --- as_of_ts: when the signal was generated ---
        as_of_ts = float(payload.get("as_of_ts", 0))
        as_of_utc = datetime.fromtimestamp(as_of_ts, tz=timezone.utc)

        # --- Microstructure: 1s kline tape ---
        micro = payload.get("microstructure", {}) or {}
        micro_entry_type = micro.get("entry_type")
        suggested_entry = micro.get("suggested_entry_price")
        n_klines = micro.get("n_klines")

        # 1s klines start at candle_close + 1s, each kline = 1s
        # So the LAST kline ends at candle_close + n_klines seconds
        if n_klines and int(n_klines) > 0:
            micro_tape_end_epoch = bar_start_ts_epoch + int(n_klines)
            micro_tape_end_utc = datetime.fromtimestamp(micro_tape_end_epoch, tz=timezone.utc)
        else:
            micro_tape_end_epoch = None
            micro_tape_end_utc = None

        # --- Latest data timestamp used ---
        # Three data sources, take the MAX:
        # 1. eval candle close (bar_start_ts_epoch) — the 1h candle's finalized close
        # 2. as_of_ts — when signal was computed (uses data up to this point)
        # 3. micro tape end — last 1s kline timestamp
        data_timestamps = [bar_start_ts_epoch]
        if micro_tape_end_epoch:
            data_timestamps.append(micro_tape_end_epoch)

        latest_data_ts = max(data_timestamps)
        latest_data_utc = datetime.fromtimestamp(latest_data_ts, tz=timezone.utc)

        # --- Causality check ---
        # Use as_of_ts (not created_ts) as the causal reference.
        # created_ts is the original DB INSERT time and is NOT updated on
        # UPSERT refreshes. Live opps get refreshed every hour — created_ts
        # stays at hour 1, but bar_start_ts/as_of_ts advance to hour N.
        # as_of_ts IS the true signal generation timestamp.
        #
        # data_leak_seconds = latest_data_used - as_of_ts
        # If positive: future data used (time-travel leak)
        # If zero or negative: causal (data was history when signal was generated)
        data_leak_seconds = round(latest_data_ts - as_of_ts, 2)
        is_causal = data_leak_seconds <= 0

        # Also flag UPSERT-refreshed rows for transparency
        is_upsert_refreshed = abs(created_epoch - as_of_ts) > 10

        if not is_causal:
            violations += 1

        results.append({
            "id": opp_id,
            "symbol": symbol,
            "created_ts": created_ts.isoformat(),
            "trigger_bar_start_ts": bar_start_ts_ms,
            "trigger_bar_start_utc": bar_start_utc.isoformat(),
            "eval_candle_close_utc": eval_candle_close_utc.isoformat(),
            "as_of_ts": as_of_ts,
            "as_of_utc": as_of_utc.isoformat(),
            "micro_entry_type": micro_entry_type or "",
            "suggested_entry_price": suggested_entry or "",
            "micro_n_klines": n_klines or "",
            "micro_tape_end_utc": micro_tape_end_utc.isoformat() if micro_tape_end_utc else "",
            "latest_data_ts": latest_data_ts,
            "latest_data_utc": latest_data_utc.isoformat(),
            "created_epoch": round(created_epoch, 3),
            "data_leak_seconds": data_leak_seconds,
            "is_strictly_causal": is_causal,
            "is_upsert_refreshed": is_upsert_refreshed,
        })

    # Write CSV
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    logger.info("Wrote %d rows to %s", len(results), out_path)

    # Summary
    causal_count = sum(1 for r in results if r["is_strictly_causal"])
    violation_count = len(results) - causal_count

    # Leak stats
    leak_values = [r["data_leak_seconds"] for r in results]
    min_leak = min(leak_values) if leak_values else 0
    max_leak = max(leak_values) if leak_values else 0
    avg_leak = sum(leak_values) / len(leak_values) if leak_values else 0

    # Micro coverage
    with_micro = sum(1 for r in results if r["micro_entry_type"])
    without_micro = len(results) - with_micro

    logger.info("=" * 60)
    logger.info("S9 CAUSALITY AUDIT SUMMARY")
    logger.info("=" * 60)
    logger.info("Total opps audited:    %d", len(results))
    logger.info("Strictly causal:       %d (%.1f%%)", causal_count, causal_count / len(results) * 100)
    logger.info("VIOLATIONS:            %d", violation_count)
    logger.info("data_leak_seconds min: %.2f", min_leak)
    logger.info("data_leak_seconds max: %.2f", max_leak)
    logger.info("data_leak_seconds avg: %.2f", avg_leak)
    logger.info("With microstructure:   %d", with_micro)
    logger.info("Without microstructure:%d", without_micro)
    logger.info("Output: %s", out_path)

    if violation_count > 0:
        logger.warning("CAUSAL VIOLATIONS FOUND — inspect CSV for rows with is_strictly_causal=False")
        # Print worst violations
        worst = sorted([r for r in results if not r["is_strictly_causal"]],
                       key=lambda x: x["data_leak_seconds"], reverse=True)[:5]
        for w in worst:
            logger.warning(
                "  VIOLATION id=%s %s leak=%.1fs created=%s latest_data=%s",
                w["id"], w["symbol"], w["data_leak_seconds"],
                w["created_ts"], w["latest_data_utc"],
            )
    else:
        logger.info("ALL OPPS STRICTLY CAUSAL — no time-travel detected")

    cur.close()
    conn.close()
    return out_path, violation_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="S9 Causality Audit")
    parser.add_argument("--limit", type=int, default=1000, help="Max opps to audit (default 1000)")
    args = parser.parse_args()
    run_audit(args.limit)
