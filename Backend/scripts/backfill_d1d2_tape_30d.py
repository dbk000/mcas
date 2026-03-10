#!/usr/bin/env python3
"""
D1/D2 Tape Overlay — Enrich expired opportunities with 1s microstructure
==========================================================================

Fetches 300 x 1s klines after each opportunity's hourly close and runs
analyze_tape_directional() to derive entry refinement. Adds tape_entry_price,
tape_entry_source, and microstructure fields to each opportunity's payload.

The original entry_price is NEVER modified — tape data is additive only.

The simulator already checks for microstructure.suggested_entry_price in the
payload (setup-agnostic guard) and uses it when present.

Run in tmux on production:
    tmux new -s tape_overlay
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/backfill_d1d2_tape_30d.py --days 30

Dry run first:
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/backfill_d1d2_tape_30d.py --days 30 --dry-run
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# Detect execution context
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))

from s9_microstructure import (
    fetch_1s_klines_batch,
    analyze_tape_directional,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tape_overlay")

DATABASE_URL = os.getenv("DATABASE_URL")
FETCH_WORKERS = 8
DB_BATCH_SIZE = 50
BATCH_DELAY_S = 1.0   # Between hourly batches (rate limit headroom)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def get_db_connection():
    import psycopg
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL)


# ---------------------------------------------------------------------------
# Load expired D1/D2 opportunities
# ---------------------------------------------------------------------------

def load_opportunities(conn, days):
    """Load expired D1/D2 opportunities from the last N days.

    Returns list of dicts with id, symbol, timeframe, setup_id, payload_json,
    created_ts, direction, and hourly_close (entry_price from payload).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, symbol, timeframe, setup_id, payload_json, created_ts
            FROM opportunities
            WHERE setup_id IN ('D1', 'D2')
              AND status = 'expired'
              AND created_ts >= %s
            ORDER BY created_ts
        """, (cutoff,))
        rows = cur.fetchall()

    opps = []
    for row in rows:
        opp_id, symbol, timeframe, setup_id, payload_text, created_ts = row
        try:
            payload = json.loads(payload_text) if payload_text else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("Opp %s: invalid payload_json, skipping", opp_id)
            continue

        # Skip if already has microstructure (idempotent)
        if "microstructure" in payload:
            continue

        direction = payload.get("direction", "long")
        entry_price = payload.get("entry_price")
        bar_start_ts = payload.get("bar_start_ts")

        if not entry_price or not bar_start_ts:
            logger.warning("Opp %s: missing entry_price or bar_start_ts", opp_id)
            continue

        opps.append({
            "id": opp_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "setup_id": setup_id,
            "payload": payload,
            "created_ts": created_ts,
            "direction": direction,
            "entry_price": float(entry_price),
            "bar_start_ts_ms": int(bar_start_ts),
        })

    return opps


# ---------------------------------------------------------------------------
# Group opportunities by hour for batch fetching
# ---------------------------------------------------------------------------

def group_by_hour(opps):
    """Group opportunities by their hourly bar_start_ts.

    Returns dict: bar_start_ts_ms -> list of opps.
    """
    groups = defaultdict(list)
    for opp in opps:
        # bar_start_ts is the 1h candle bar_start — klines start at candle close
        # which is bar_start + 1h
        groups[opp["bar_start_ts_ms"]].append(opp)
    return groups


# ---------------------------------------------------------------------------
# Enrich and update
# ---------------------------------------------------------------------------

def enrich_opp(opp, micro_ctx):
    """Add tape fields to an opportunity's payload. Returns updated payload."""
    payload = opp["payload"]

    payload["tape_entry_price"] = micro_ctx.suggested_entry_price
    payload["tape_entry_source"] = "1s"
    payload["microstructure"] = {
        "direction": micro_ctx.direction,
        "entry_type": micro_ctx.entry_type,
        "suggested_entry_price": micro_ctx.suggested_entry_price,
        "reason": micro_ctx.reason,
        "drift_bps": round(micro_ctx.drift_bps, 1),
        "max_drift_bps": round(micro_ctx.max_drift_bps, 1),
        "n_klines": micro_ctx.n_klines,
        "still_trending_favorable": micro_ctx.still_trending_favorable,
        "already_moved_adverse": micro_ctx.already_moved_adverse,
        "best_entry_price": micro_ctx.best_entry_price,
        "best_entry_time_offset_s": micro_ctx.best_entry_time_offset_s,
        "retracement_from_best_bps": round(micro_ctx.retracement_from_best_bps, 1),
        "volume_ratio_early_late": round(micro_ctx.volume_ratio_early_late, 2),
    }

    return payload


def flush_updates(conn, pending):
    """Write pending payload updates to DB."""
    if not pending:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE opportunities SET payload_json = %s WHERE id = %s",
            [(json.dumps(p["payload"]), p["id"]) for p in pending],
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_overlay(days=30, dry_run=False):
    start_wall = time.time()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"tape_overlay_d1d2_{ts_str}.txt")
    logger.info("Output: %s", output_path)

    conn = get_db_connection()

    # Load opportunities
    opps = load_opportunities(conn, days)
    logger.info("Loaded %d D1/D2 expired opps (last %d days) without microstructure",
                len(opps), days)

    if not opps:
        logger.info("Nothing to process.")
        conn.close()
        return

    # Group by hour
    hour_groups = group_by_hour(opps)
    total_hours = len(hour_groups)
    logger.info("Grouped into %d distinct hours", total_hours)

    # Stats
    total_enriched = 0
    total_skipped = 0        # No 1s data available
    total_skip_entry = 0     # Tape said SKIP
    total_market = 0
    total_limit = 0
    entry_deltas_bps = []
    pending = []

    for h_idx, (bar_ts_ms, hour_opps) in enumerate(sorted(hour_groups.items())):
        # 1s klines start 1h after bar_start (= candle close) + 1s
        kline_start_ms = bar_ts_ms + 3600 * 1000 + 1000
        symbols = list({o["symbol"] for o in hour_opps})

        # Batch fetch 1s klines
        kline_batch = {}
        try:
            kline_batch = fetch_1s_klines_batch(
                symbols, kline_start_ms, limit=300, max_workers=FETCH_WORKERS,
            )
        except Exception as e:
            logger.debug("1s fetch failed for hour %s: %s", bar_ts_ms, e)

        for opp in hour_opps:
            sym = opp["symbol"]
            klines = kline_batch.get(sym, [])

            if not klines or len(klines) < 10:
                total_skipped += 1
                continue

            micro_ctx = analyze_tape_directional(
                opp["entry_price"], klines, opp["direction"],
            )

            if micro_ctx.entry_type == "SKIP":
                total_skip_entry += 1
            elif micro_ctx.entry_type == "MARKET":
                total_market += 1
            elif micro_ctx.entry_type == "LIMIT":
                total_limit += 1

            # Compute entry delta
            orig = opp["entry_price"]
            tape = micro_ctx.suggested_entry_price
            if orig > 0:
                delta_bps = (tape - orig) / orig * 10000
                entry_deltas_bps.append(delta_bps)

            enriched_payload = enrich_opp(opp, micro_ctx)
            pending.append({"id": opp["id"], "payload": enriched_payload})
            total_enriched += 1

        # Flush batch
        if len(pending) >= DB_BATCH_SIZE and not dry_run:
            flush_updates(conn, pending)
            pending = []

        # Progress every 50 hours
        if (h_idx + 1) % 50 == 0 or h_idx == total_hours - 1:
            elapsed = time.time() - start_wall
            rate = (h_idx + 1) / elapsed if elapsed > 0 else 0
            eta = (total_hours - h_idx - 1) / max(rate, 0.01)
            logger.info(
                "Progress: %d/%d hours (%.0f%%), enriched=%d, skipped=%d, "
                "elapsed=%.0fs, ETA=%.0fs",
                h_idx + 1, total_hours,
                (h_idx + 1) / total_hours * 100,
                total_enriched, total_skipped,
                elapsed, eta,
            )

        # Rate limit headroom
        if kline_batch:
            time.sleep(BATCH_DELAY_S)

    # Flush remaining
    if pending and not dry_run:
        flush_updates(conn, pending)

    conn.close()
    elapsed = time.time() - start_wall

    # Summary
    import statistics
    summary_lines = [
        "=" * 70,
        "D1/D2 TAPE OVERLAY COMPLETE",
        "=" * 70,
        f"Total opps processed:  {total_enriched + total_skipped}",
        f"Enriched with tape:    {total_enriched}",
        f"No 1s data (skipped):  {total_skipped}",
        f"",
        f"Entry type breakdown:",
        f"  MARKET:  {total_market}",
        f"  LIMIT:   {total_limit}",
        f"  SKIP:    {total_skip_entry}",
        f"",
    ]

    if entry_deltas_bps:
        summary_lines.extend([
            f"Entry price delta (tape vs original, bps):",
            f"  Mean:    {statistics.mean(entry_deltas_bps):+.1f}",
            f"  Median:  {statistics.median(entry_deltas_bps):+.1f}",
            f"  Stdev:   {statistics.stdev(entry_deltas_bps):.1f}" if len(entry_deltas_bps) > 1 else "",
            f"  Min:     {min(entry_deltas_bps):+.1f}",
            f"  Max:     {max(entry_deltas_bps):+.1f}",
            f"",
        ])

    summary_lines.extend([
        f"Coverage: {total_enriched}/{total_enriched + total_skipped} "
        f"({total_enriched / max(total_enriched + total_skipped, 1) * 100:.1f}%)",
        f"Elapsed: {elapsed:.0f}s",
        f"Dry run: {dry_run}",
    ])

    summary = "\n".join(summary_lines)
    logger.info("\n%s", summary)

    with open(output_path, "w") as f:
        f.write(summary + "\n")
    logger.info("Results written to %s", output_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="D1/D2 tape overlay enrichment")
    parser.add_argument("--days", type=int, default=30,
                        help="Look back N days (default: 30, max for 1s data)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyze but don't write to DB")
    args = parser.parse_args()

    run_overlay(days=args.days, dry_run=args.dry_run)
