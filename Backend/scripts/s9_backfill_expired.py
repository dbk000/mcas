#!/usr/bin/env python3
"""
S9 Expired Opportunity Backfill
================================

Re-derives S9 signals for the past N days using the current code
(including 1s microstructure analysis), deletes old expired S9 opps
within the same batch scope, and inserts fresh ones matching the live
signal_worker payload format.

Honesty Fixes applied:
  Fix 1b: action_by_ts = candle_close + 48h (settler gets correct horizon)
  Fix 2:  Volume filter + exclusion list (universe parity with live)
  Fix 4:  Batch-id scoped deletes (no blanket DELETE)
  Fix 5:  executemany batch INSERT (Canon §2 compliance)

Run in tmux on production:
    tmux new -s s9_backfill
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/s9_backfill_expired.py --days 30 \
            --batch-id s9_honest_v1_20260309 --replace-batch
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

# Detect execution context
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))

from s9_microstructure import (
    fetch_1s_klines_batch, analyze_tape, Kline1s,
)
from setups.s9_short_reversion import S9Evaluator, S9State
from setup_base import Direction

logger = logging.getLogger("s9_backfill")

# ---------------------------------------------------------------------------
# Config — matches signal_worker exactly. DO NOT MODIFY.
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
PERCENTILE = 10
BREADTH_THRESHOLD = 0.3
HOLD_BARS = 48
SETUP_VERSION = "S9_v1.0"
FETCH_WORKERS = 6
BATCH_DELAY_S = 1.0   # Between hourly batches (rate limit headroom)
DB_BATCH_SIZE = 50     # INSERT batch size

# Fix 2: Volume filter parity with live monitored universe
MIN_24H_QUOTE_VOLUME_USD = float(os.environ.get("MIN_24H_QUOTE_VOLUME_USD", "5000000"))

# Output path for persistent results
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

INSERT_SQL = """
    INSERT INTO opportunities
        (created_ts, updated_ts, symbol, timeframe, setup_id,
         decision, score, reason, payload_json, status, setup_version)
    VALUES (%s, %s, %s, '1h', 'S9', 'SIGNAL', %s, %s, %s,
            'expired', %s)
"""


def get_db_connection():
    import psycopg
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL)


# ---------------------------------------------------------------------------
# Fix 2: Load exclusion list from Redis
# ---------------------------------------------------------------------------

def load_excluded_symbols() -> set:
    """Load quality-excluded symbols from Redis (same set the live worker uses)."""
    try:
        import redis
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        excluded = r.smembers("symbol:excluded:quality") or set()
        logger.info("Loaded %d excluded symbols from Redis", len(excluded))
        return excluded
    except Exception as e:
        logger.warning("Could not load exclusion list from Redis: %s (proceeding without)", e)
        return set()


# ---------------------------------------------------------------------------
# Phase 1: Load 1h candle data (with volume for Fix 2)
# ---------------------------------------------------------------------------

def load_1h_candles(conn, start_ts, end_ts):
    """Load all 1h candles for the window.

    Returns dict: symbol -> list of (bar_start_ts, close, volume) sorted by time.
    """
    logger.info("Loading 1h candles from %s to %s ...",
                start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d"))
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, bar_start_ts, close, volume
        FROM ohlcv
        WHERE timeframe = '1h'
          AND bar_start_ts >= %s
          AND bar_start_ts <= %s
          AND is_final = true
        ORDER BY symbol, bar_start_ts
    """, (start_ts, end_ts))

    data = defaultdict(list)
    count = 0
    for row in cur:
        data[row[0]].append((row[1], row[2], row[3]))  # (ts, close, volume)
        count += 1
    cur.close()

    logger.info("Loaded %d candles for %d symbols", count, len(data))
    return data


# ---------------------------------------------------------------------------
# Fix 2: Volume filter — compute trailing 24h quote volume per symbol
# ---------------------------------------------------------------------------

def get_volume_filtered_symbols(candle_data, eval_hour, excluded_symbols):
    """Filter symbols by trailing 24h quote volume, matching live monitored universe.

    Quote volume ≈ close * base_volume (ohlcv table has base volume, not quote).
    """
    eligible = {}
    window_start = eval_hour - timedelta(hours=23)

    for symbol, candles in candle_data.items():
        if symbol in excluded_symbols:
            continue

        # Compute trailing 24h quote volume
        quote_vol_24h = 0.0
        for ts, close, volume in candles:
            if window_start <= ts <= eval_hour and close and volume:
                quote_vol_24h += close * volume

        if quote_vol_24h >= MIN_24H_QUOTE_VOLUME_USD:
            eligible[symbol] = candles

    return eligible


# ---------------------------------------------------------------------------
# Phase 2: Re-derive S9 signals for one hour
# ---------------------------------------------------------------------------

def derive_signals_for_hour(candle_data, eval_hour, excluded_symbols):
    """Re-derive S9 signals for a specific hour.

    Fix 2: applies volume filter + exclusion list before signal derivation.
    Returns list of dicts with full signal info, or empty list.
    """
    # Fix 2: Volume-filtered + exclusion-list-filtered universe
    filtered_data = get_volume_filtered_symbols(candle_data, eval_hour, excluded_symbols)

    symbol_metrics = {}
    for symbol, candles in filtered_data.items():
        ts_close = {ts: c for ts, c, _v in candles}
        close_now = ts_close.get(eval_hour)
        close_4h = ts_close.get(eval_hour - timedelta(hours=4))
        close_24h = ts_close.get(eval_hour - timedelta(hours=24))

        if (close_now and close_now > 0 and close_4h and close_4h > 0
                and close_24h and close_24h > 0):
            symbol_metrics[symbol] = {
                "close": close_now,
                "ret_4h": close_now / close_4h - 1,
                "ret_24h": close_now / close_24h - 1,
            }

    if len(symbol_metrics) < 20:
        return []

    n_positive = sum(1 for m in symbol_metrics.values() if m["ret_24h"] > 0)
    breadth = n_positive / len(symbol_metrics)

    if breadth <= BREADTH_THRESHOLD:
        return []

    sorted_syms = sorted(
        symbol_metrics.items(), key=lambda x: x[1]["ret_4h"], reverse=True,
    )
    decile_n = max(1, len(sorted_syms) // PERCENTILE)
    top_decile = sorted_syms[:decile_n]

    signals = []
    for rank, (symbol, m) in enumerate(top_decile):
        signals.append({
            "symbol": symbol,
            "eval_hour": eval_hour,
            "close": m["close"],
            "ret_4h": m["ret_4h"],
            "ret_24h": m["ret_24h"],
            "breadth": breadth,
            "rank": rank,
            "decile_size": decile_n,
            "universe_size": len(symbol_metrics),
        })
    return signals


# ---------------------------------------------------------------------------
# Phase 3: Build payload matching live signal_worker format exactly
# ---------------------------------------------------------------------------

def build_opportunity_payload(sig, micro_ctx, batch_id):
    """Build payload dict matching signal_worker._evaluate_s9_cross_sectional."""
    state = S9State(
        breadth=sig["breadth"],
        breadth_passes=True,
        universe_size=sig["universe_size"],
        top_decile_threshold=0.0,
        top_decile_count=sig["decile_size"],
    )

    signal = S9Evaluator.build_signal(
        symbol=sig["symbol"], close=sig["close"],
        ret_4h=sig["ret_4h"], ret_24h=sig["ret_24h"],
        breadth=sig["breadth"], rank=sig["rank"],
        universe_size=sig["universe_size"],
        decile_size=sig["decile_size"], state=state,
    )

    # bar_start_ts matches the live signal_worker: the hour boundary when
    # the candle closes (eval_hour + 1h), NOT the candle's own bar_start_ts.
    candle_close = sig["eval_hour"] + timedelta(hours=1)
    bar_start_ts = str(int(candle_close.timestamp() * 1000))

    micro_payload = {}
    if micro_ctx and micro_ctx.n_klines >= 10:
        micro_payload = {
            "entry_type": micro_ctx.entry_type,
            "suggested_entry_price": micro_ctx.suggested_entry_price,
            "micro_reason": micro_ctx.reason,
            "drift_bps": round(micro_ctx.drift_bps, 1),
            "max_drift_bps": round(micro_ctx.max_drift_bps, 1),
            "max_price_300s": micro_ctx.max_price,
            "current_price_300s": micro_ctx.current_price,
            "still_climbing": micro_ctx.still_climbing,
            "already_reverted": micro_ctx.already_reverted,
            "volume_ratio_early_late": round(micro_ctx.volume_ratio_early_late, 1),
            "n_klines": micro_ctx.n_klines,
        }

    effective_entry = signal.entry_price
    if micro_ctx and micro_ctx.entry_type != "SKIP":
        effective_entry = micro_ctx.suggested_entry_price

    final_score = signal.setup_score
    skip_reason = None
    if micro_ctx and micro_ctx.entry_type == "SKIP":
        final_score = signal.setup_score * 0.5
        skip_reason = micro_ctx.reason

    payload = {
        "setup_id": "S9",
        "setup_name": "Short Reversion v1.0",
        "symbol": sig["symbol"],
        "timeframe": "1h",
        "direction": signal.direction.value,
        "setup_score": final_score,
        "raw_score": signal.setup_score,
        "entry_price": effective_entry,
        "naive_entry_price": signal.entry_price,
        "stop_price": signal.stop_price,
        "tp1_price": signal.tp1_price,
        "tp2_price": signal.tp2_price,
        "r_value": signal.r_value,
        "time_stop_bars": signal.time_stop_bars,
        "bar_start_ts": bar_start_ts,
        "as_of_ts": candle_close.timestamp() + 300,
        "why_items": signal.explain.why_items if signal.explain else [],
        "state": {
            "ret_4h": sig["ret_4h"],
            "ret_24h": sig["ret_24h"],
            "breadth": sig["breadth"],
            "rank": sig["rank"] + 1,
            "decile_size": sig["decile_size"],
            "universe_size": sig["universe_size"],
        },
        "score_components": {
            "rank_score": signal.rank_score,
            "breadth_score": signal.breadth_score,
            "magnitude_score": signal.magnitude_score,
        },
        "microstructure": micro_payload,
        # Fix 1b: action_by_ts = candle_close + 48h (settler reads this first)
        "action_by_ts": (candle_close + timedelta(hours=48)).isoformat(),
        # Fix 4: batch_id for scoped deletes
        "batch_id": batch_id,
    }

    if skip_reason:
        payload["skip_reason"] = skip_reason

    reason = "; ".join(signal.explain.why_items) if signal.explain else None

    return payload, final_score, reason


# ---------------------------------------------------------------------------
# Phase 4: DB operations
# ---------------------------------------------------------------------------

def delete_scoped_expired_s9(conn, batch_id):
    """Delete expired S9 opps scoped to batch_id (Fix 4: no blanket delete).

    Deletes opps matching this batch_id OR with NULL batch_id (legacy opps).
    payload_json is TEXT, must cast to jsonb for JSON operator access.
    """
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM opportunities
            WHERE setup_id = 'S9' AND status = 'expired'
              AND (payload_json::jsonb->>'batch_id' = %s
                   OR payload_json::jsonb->>'batch_id' IS NULL)
        """, (batch_id,))
        deleted = cur.rowcount
    conn.commit()
    logger.info("Deleted %d expired S9 opportunities (batch=%s + legacy NULL)", deleted, batch_id)
    return deleted


def insert_batch(conn, rows):
    """Insert a batch of opportunities using executemany (Fix 5)."""
    if not rows:
        return
    params = [
        (
            r["created_ts"], r["created_ts"],
            r["symbol"], r["score"], r["reason"],
            r["payload_json"], SETUP_VERSION,
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, params)
    conn.commit()


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------

def run_backfill(days=30, batch_id=None, replace_batch=False, dry_run=False):
    start_wall = time.time()

    # Fix 4: auto-generate batch_id if not provided
    if batch_id is None:
        batch_id = f"s9_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Persistent output (Canon §2 preflight #7)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(
        OUTPUT_DIR,
        f"s9_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt",
    )

    conn = get_db_connection()

    now = datetime.now(timezone.utc)
    signal_end = now - timedelta(hours=2)  # Don't overlap with active opps
    signal_start = signal_end - timedelta(days=days)
    data_start = signal_start - timedelta(hours=25)

    logger.info("=" * 70)
    logger.info("S9 EXPIRED OPPORTUNITY BACKFILL (HONEST)")
    logger.info("=" * 70)
    logger.info("Window: %s to %s (%d days)",
                signal_start.strftime("%Y-%m-%d"),
                signal_end.strftime("%Y-%m-%d"), days)
    logger.info("Batch ID: %s", batch_id)
    logger.info("Replace batch: %s", replace_batch)
    logger.info("Volume filter: %.0f USD", MIN_24H_QUOTE_VOLUME_USD)
    logger.info("Output: %s", output_path)
    if dry_run:
        logger.info("DRY RUN — no DB writes")

    # Fix 2: Load exclusion list from Redis
    excluded_symbols = load_excluded_symbols()

    # Fix 4: Scoped delete (only when --replace-batch)
    if replace_batch and not dry_run:
        delete_scoped_expired_s9(conn, batch_id)
    elif not dry_run:
        logger.info("No --replace-batch: skipping delete, adding alongside existing")

    # Load 1h candle data (with volume for Fix 2)
    candle_data = load_1h_candles(conn, data_start, now)
    if not candle_data:
        logger.error("No 1h candle data. Aborting.")
        conn.close()
        return

    # Iterate hours, derive signals, fetch 1s, insert
    current_hour = signal_start.replace(minute=0, second=0, microsecond=0)
    hours = []
    while current_hour <= signal_end:
        hours.append(current_hour)
        current_hour += timedelta(hours=1)

    total_hours = len(hours)
    total_inserted = 0
    total_signals = 0
    total_with_1s = 0
    pending_rows = []
    universe_sizes = []

    for h_idx, hour in enumerate(hours):
        signals = derive_signals_for_hour(candle_data, hour, excluded_symbols)
        if not signals:
            if (h_idx + 1) % 100 == 0:
                logger.info("Progress: %d/%d hours (%.0f%%), %d opps inserted",
                            h_idx + 1, total_hours,
                            (h_idx + 1) / total_hours * 100, total_inserted)
            continue

        total_signals += len(signals)
        if signals:
            universe_sizes.append(signals[0]["universe_size"])

        # Fetch 1s klines for this hour's signals.
        candle_close = hour + timedelta(hours=1)
        hour_start_ms = int(candle_close.timestamp() * 1000) + 1000
        symbols = [s["symbol"] for s in signals]

        kline_batch = {}
        try:
            kline_batch = fetch_1s_klines_batch(
                symbols, hour_start_ms, limit=300, max_workers=FETCH_WORKERS,
            )
        except Exception as e:
            logger.debug("1s fetch failed for hour %s: %s", hour, e)

        with_1s = len(kline_batch)
        total_with_1s += with_1s

        # Build and queue rows
        for sig in signals:
            klines = kline_batch.get(sig["symbol"], [])
            micro_ctx = analyze_tape(sig["close"], klines) if klines else None

            payload, score, reason = build_opportunity_payload(sig, micro_ctx, batch_id)

            pending_rows.append({
                "created_ts": candle_close + timedelta(seconds=300),
                "symbol": sig["symbol"],
                "score": score,
                "reason": reason,
                "payload_json": json.dumps(payload),
            })

        # Flush batch to DB
        if len(pending_rows) >= DB_BATCH_SIZE and not dry_run:
            insert_batch(conn, pending_rows)
            total_inserted += len(pending_rows)
            pending_rows = []
        elif dry_run and len(pending_rows) >= DB_BATCH_SIZE:
            total_inserted += len(pending_rows)
            pending_rows = []

        # Progress log every 24 hours of data
        if (h_idx + 1) % 24 == 0:
            elapsed = time.time() - start_wall
            rate = (h_idx + 1) / elapsed * 3600 if elapsed > 0 else 0
            eta = (total_hours - h_idx - 1) / max(rate / 3600, 0.01)
            logger.info(
                "Progress: %d/%d hours (%.0f%%), %d signals, %d with 1s data, "
                "%d inserted, elapsed=%.0fs, ETA=%.0fs",
                h_idx + 1, total_hours,
                (h_idx + 1) / total_hours * 100,
                total_signals, total_with_1s, total_inserted,
                elapsed, eta,
            )

        # Rate limit headroom between hours
        if kline_batch:
            time.sleep(BATCH_DELAY_S)

    # Flush remaining
    if pending_rows and not dry_run:
        insert_batch(conn, pending_rows)
        total_inserted += len(pending_rows)
    elif dry_run:
        total_inserted += len(pending_rows)

    conn.close()
    elapsed = time.time() - start_wall

    avg_universe = sum(universe_sizes) / len(universe_sizes) if universe_sizes else 0

    summary = (
        f"{'=' * 70}\n"
        f"BACKFILL COMPLETE {'(DRY RUN)' if dry_run else ''}\n"
        f"{'=' * 70}\n"
        f"Hours processed:  {total_hours}\n"
        f"Signals derived:  {total_signals}\n"
        f"With 1s data:     {total_with_1s} ({total_with_1s / max(total_signals, 1) * 100:.0f}%)\n"
        f"Opps inserted:    {total_inserted}\n"
        f"Batch ID:         {batch_id}\n"
        f"Avg universe:     {avg_universe:.0f} symbols (volume-filtered)\n"
        f"Excluded symbols: {len(excluded_symbols)}\n"
        f"Runtime:          {elapsed:.0f}s ({elapsed / 60:.1f} min)\n"
        f"{'=' * 70}\n"
    )
    logger.info("\n%s", summary)

    # Persist to file
    with open(output_path, "w") as f:
        f.write(summary)
    logger.info("Results written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )

    parser = argparse.ArgumentParser(description="S9 Expired Opportunity Backfill")
    parser.add_argument("--days", type=int, default=30,
                        help="Days to backfill (default: 30)")
    parser.add_argument("--batch-id", default=None,
                        help="Batch ID for this backfill run. Auto-generated if not set.")
    parser.add_argument("--replace-batch", action="store_true",
                        help="Delete existing opps from this batch before inserting.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate without writing to DB.")
    args = parser.parse_args()

    run_backfill(
        days=args.days,
        batch_id=args.batch_id,
        replace_batch=args.replace_batch,
        dry_run=args.dry_run,
    )
