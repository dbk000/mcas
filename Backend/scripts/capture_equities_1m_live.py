#!/usr/bin/env python3
"""
Capture Equities 1m Live — Yahoo Finance polling for NVDA earnings reaction day.
================================================================================
Polls Yahoo Finance chart API every 60 seconds during market hours to capture
1-minute bars for NVDA and cascade stocks. First reaction day with 1m precision.

Targets: NVDA, META, AVGO, QQQ, XLK
Schedule: 9:25 AM ET → 4:05 PM ET (5 min buffer each side)
Source: yahoo_chart (same as existing backfill)

Usage:
  # In tmux (will run ~6.5 hours)
  tmux new -s capture_1m
  python3 Backend/scripts/capture_equities_1m_live.py

  # Or in Docker:
  tmux new -s capture_1m
  cd Backend && docker compose -f docker-compose.yml run --rm -T worker \
    python3 /scripts/capture_equities_1m_live.py
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

# Execution context detection (Corrections Log 2026-02-12)
if os.path.isdir('/scripts'):
    sys.path.insert(0, '/scripts')
elif os.path.isdir(os.path.join(os.path.dirname(__file__), 'equities_predictor')):
    sys.path.insert(0, os.path.dirname(__file__))

from equities_predictor.db import connect, upsert_ohlcv
from equities_predictor.sources import fetch_yahoo_chart, polite_sleep

# ── Configuration ─────────────────────────────────────────────────────────

SYMBOLS = ["NVDA", "META", "AVGO", "QQQ", "XLK"]

ET = ZoneInfo("America/New_York")
CAPTURE_START = dtime(9, 25)   # 5 min before open
CAPTURE_END = dtime(16, 5)     # 5 min after close
POLL_INTERVAL_SEC = 60         # Every 60 seconds

BATCH_PREFIX = "eq_1m_live"
SOURCE = "yahoo_chart"


def now_et() -> datetime:
    return datetime.now(ET)


def is_in_capture_window() -> bool:
    """Check if current time is within capture window."""
    t = now_et().time()
    return CAPTURE_START <= t <= CAPTURE_END


def wait_for_capture_window():
    """Sleep until capture window opens."""
    while True:
        current = now_et()
        t = current.time()
        if CAPTURE_START <= t <= CAPTURE_END:
            return

        # Calculate seconds until CAPTURE_START
        target = datetime.combine(current.date(), CAPTURE_START, tzinfo=ET)
        if t > CAPTURE_END:
            # Past today's window — wait for tomorrow
            target += timedelta(days=1)

        wait_sec = (target - current).total_seconds()
        if wait_sec > 0:
            print(f"[{current.strftime('%H:%M:%S ET')}] Outside capture window. "
                  f"Waiting {wait_sec/60:.0f} min until {CAPTURE_START}...")
            # Sleep in chunks so we can show progress
            while wait_sec > 0:
                chunk = min(wait_sec, 300)  # 5 min chunks
                time.sleep(chunk)
                wait_sec -= chunk
                if wait_sec > 0:
                    print(f"  ...{wait_sec/60:.0f} min remaining")


def fetch_and_store(conn, batch_id: str) -> int:
    """Fetch 1m bars for all symbols and upsert to DB. Returns total bars stored."""
    now = datetime.now(timezone.utc)
    # Fetch the current trading day's data
    # Yahoo 1m: use period that covers today's session
    today_et = now_et().date()
    start_ts = datetime.combine(today_et, dtime(4, 0), tzinfo=ET).astimezone(timezone.utc)
    end_ts = now + timedelta(minutes=5)  # Small future buffer

    total = 0
    for symbol in SYMBOLS:
        try:
            bars = fetch_yahoo_chart(
                conn,
                symbol=symbol,
                interval="1m",
                start_ts=start_ts,
                end_ts=end_ts,
                include_prepost=True,
            )
        except Exception as e:
            print(f"  WARN: {symbol} fetch failed: {e}")
            continue

        if not bars:
            continue

        for bar in bars:
            bar["timeframe"] = "1m"
            bar["is_final"] = True

        n = upsert_ohlcv(conn, bars, source=SOURCE, batch_id=batch_id)
        conn.commit()
        total += n

    return total


def main():
    print("=" * 60)
    print("NVDA Earnings Reaction Day — 1m Live Capture")
    print("=" * 60)
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Capture window: {CAPTURE_START} → {CAPTURE_END} ET")
    print(f"Poll interval: {POLL_INTERVAL_SEC}s")
    print(f"Current time: {now_et().strftime('%Y-%m-%d %H:%M:%S ET')}")
    print()

    conn = connect()
    batch_id = f"{BATCH_PREFIX}_{uuid.uuid4().hex[:8]}"

    # Wait for capture window if needed
    wait_for_capture_window()

    print(f"\n[{now_et().strftime('%H:%M:%S ET')}] Capture started. Batch: {batch_id}")
    print(f"{'='*60}\n")

    poll_count = 0
    total_bars_all = 0

    while is_in_capture_window():
        poll_count += 1
        t = now_et().strftime('%H:%M:%S ET')

        try:
            n = fetch_and_store(conn, batch_id)
            total_bars_all += n
            print(f"[{t}] Poll #{poll_count}: {n} bars upserted "
                  f"(session total: {total_bars_all})")
        except Exception as e:
            print(f"[{t}] Poll #{poll_count}: ERROR: {e}")
            # Reconnect on DB errors
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn = connect()
                print(f"  Reconnected to DB")
            except Exception as e2:
                print(f"  FATAL: Cannot reconnect: {e2}")
                return

        # Wait for next poll
        time.sleep(POLL_INTERVAL_SEC)

    print(f"\n{'='*60}")
    print(f"Capture complete.")
    print(f"  Polls: {poll_count}")
    print(f"  Total bars upserted: {total_bars_all}")
    print(f"  Batch: {batch_id}")

    # Print data summary
    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol, COUNT(*) as bars,
                   MIN(bar_start_ts) as earliest,
                   MAX(bar_start_ts) as latest
            FROM ohlcv
            WHERE symbol = ANY(%s)
              AND timeframe = '1m'
              AND batch_id = %s
            GROUP BY symbol
            ORDER BY symbol;
        """, (SYMBOLS, batch_id))
        rows = cur.fetchall()
        if rows:
            print(f"\nCapture summary:")
            for r in rows:
                print(f"  {r['symbol']}: {r['bars']} bars "
                      f"({r['earliest']} → {r['latest']})")

    conn.close()


if __name__ == "__main__":
    main()
