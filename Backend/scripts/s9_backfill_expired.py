#!/usr/bin/env python3
"""
S9 Expired Opportunity Backfill
================================

Re-derives S9 signals for the past N days using the current code
(including 1s microstructure analysis), deletes old expired S9 opps,
and inserts fresh ones matching the live signal_worker payload format.

This is a history-rewrite: makes the expired opportunities table look
like the current code was always running.

Run in tmux on production:
    tmux new -s s9_backfill
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/s9_backfill_expired.py --days 30
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


def get_db_connection():
    import psycopg
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL)


# ---------------------------------------------------------------------------
# Phase 1: Load 1h candle data
# ---------------------------------------------------------------------------

def load_1h_candles(conn, start_ts, end_ts):
    """Load all 1h candle closes for the window.

    Returns dict: symbol -> list of (bar_start_ts, close) sorted by time.
    """
    logger.info("Loading 1h candles from %s to %s ...",
                start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d"))
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, bar_start_ts, close
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
        data[row[0]].append((row[1], row[2]))
        count += 1
    cur.close()

    logger.info("Loaded %d candles for %d symbols", count, len(data))
    return data


# ---------------------------------------------------------------------------
# Phase 2: Re-derive S9 signals for one hour
# ---------------------------------------------------------------------------

def derive_signals_for_hour(candle_data, eval_hour):
    """Re-derive S9 signals for a specific hour.

    Returns list of dicts with full signal info, or empty list.
    """
    symbol_metrics = {}
    for symbol, candles in candle_data.items():
        ts_close = {ts: c for ts, c in candles}
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

def build_opportunity_payload(sig, micro_ctx):
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

    bar_start_ts = str(int(sig["eval_hour"].timestamp() * 1000))

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
        "as_of_ts": sig["eval_hour"].timestamp() + 300,
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
    }

    if skip_reason:
        payload["skip_reason"] = skip_reason

    reason = "; ".join(signal.explain.why_items) if signal.explain else None

    return payload, final_score, reason


# ---------------------------------------------------------------------------
# Phase 4: DB operations
# ---------------------------------------------------------------------------

def delete_old_expired_s9(conn):
    """Delete only expired S9 opportunities (will be replaced by backfill)."""
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM opportunities
            WHERE setup_id = 'S9' AND status = 'expired'
        """)
        deleted = cur.rowcount
    conn.commit()
    logger.info("Deleted %d expired S9 opportunities", deleted)
    return deleted


def insert_batch(conn, rows):
    """Insert a batch of opportunities."""
    if not rows:
        return
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO opportunities
                    (created_ts, updated_ts, symbol, timeframe, setup_id,
                     decision, score, reason, payload_json, status, setup_version)
                VALUES (%s, %s, %s, '1h', 'S9', 'SIGNAL', %s, %s, %s,
                        'expired', %s)
            """, (
                r["created_ts"], r["created_ts"],
                r["symbol"], r["score"], r["reason"],
                r["payload_json"], SETUP_VERSION,
            ))
    conn.commit()


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------

def run_backfill(days=30):
    start_wall = time.time()

    conn = get_db_connection()

    now = datetime.now(timezone.utc)
    signal_end = now - timedelta(hours=2)  # Don't overlap with active opps
    signal_start = signal_end - timedelta(days=days)
    data_start = signal_start - timedelta(hours=25)

    logger.info("=" * 70)
    logger.info("S9 EXPIRED OPPORTUNITY BACKFILL")
    logger.info("=" * 70)
    logger.info("Window: %s to %s (%d days)",
                signal_start.strftime("%Y-%m-%d"),
                signal_end.strftime("%Y-%m-%d"), days)

    # Step 1: Delete only expired S9 opps (backfill replaces them)
    delete_old_expired_s9(conn)

    # Step 2: Load 1h candle data
    candle_data = load_1h_candles(conn, data_start, now)
    if not candle_data:
        logger.error("No 1h candle data. Aborting.")
        conn.close()
        return

    # Step 3: Iterate hours, derive signals, fetch 1s, insert
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

    for h_idx, hour in enumerate(hours):
        signals = derive_signals_for_hour(candle_data, hour)
        if not signals:
            if (h_idx + 1) % 100 == 0:
                logger.info("Progress: %d/%d hours (%.0f%%), %d opps inserted",
                            h_idx + 1, total_hours,
                            (h_idx + 1) / total_hours * 100, total_inserted)
            continue

        total_signals += len(signals)

        # Fetch 1s klines for this hour's signals
        hour_start_ms = int(hour.timestamp() * 1000) + 1000
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

            payload, score, reason = build_opportunity_payload(sig, micro_ctx)

            pending_rows.append({
                "created_ts": hour + timedelta(seconds=300),
                "symbol": sig["symbol"],
                "score": score,
                "reason": reason,
                "payload_json": json.dumps(payload),
            })

        # Flush batch to DB
        if len(pending_rows) >= DB_BATCH_SIZE:
            insert_batch(conn, pending_rows)
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
    if pending_rows:
        insert_batch(conn, pending_rows)
        total_inserted += len(pending_rows)

    conn.close()
    elapsed = time.time() - start_wall

    logger.info("=" * 70)
    logger.info("BACKFILL COMPLETE")
    logger.info("=" * 70)
    logger.info("Hours processed:  %d", total_hours)
    logger.info("Signals derived:  %d", total_signals)
    logger.info("With 1s data:     %d (%.0f%%)",
                total_with_1s,
                total_with_1s / max(total_signals, 1) * 100)
    logger.info("Opps inserted:    %d", total_inserted)
    logger.info("Runtime:          %.0fs (%.1f min)", elapsed, elapsed / 60)
    logger.info("=" * 70)


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
    args = parser.parse_args()

    run_backfill(days=args.days)
