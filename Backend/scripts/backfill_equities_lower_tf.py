#!/usr/bin/env python3
"""
Backfill lower-timeframe equities OHLCV from Yahoo Finance.

Fetches 5m, 15m, 30m bars for NVDA + cascade candidates.
Yahoo limits: 5m/15m = 60 days, 30m = ~2 years, 1m = 7 days.

Usage (in Docker):
  docker compose -f docker-compose.yml run --rm -T worker \
    python3 /scripts/backfill_equities_lower_tf.py
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

# Execution context detection
if os.path.isdir('/scripts'):
    sys.path.insert(0, '/scripts')
elif os.path.isdir(os.path.join(os.path.dirname(__file__), 'equities_predictor')):
    sys.path.insert(0, os.path.dirname(__file__))

from equities_predictor.db import connect, upsert_ohlcv
from equities_predictor.sources import fetch_yahoo_chart, polite_sleep

# ── Config ────────────────────────────────────────────────────────────────

SYMBOLS = ["NVDA", "META", "AVGO", "QQQ", "XLK"]

# Yahoo interval → max lookback days (conservative)
INTERVALS = {
    "5m": 58,
    "15m": 58,
    "30m": 700,
}

BATCH_ID = f"eq_lower_tf_{uuid.uuid4().hex[:8]}"


def main():
    conn = connect()
    now = datetime.now(timezone.utc)

    total_bars = 0
    for interval, max_days in INTERVALS.items():
        start_ts = now - timedelta(days=max_days)
        end_ts = now

        print(f"\n{'='*60}")
        print(f"Interval: {interval} | Lookback: {max_days} days | {start_ts.date()} → {end_ts.date()}")
        print(f"{'='*60}")

        for symbol in SYMBOLS:
            print(f"  Fetching {symbol} {interval}...", end=" ", flush=True)
            try:
                bars = fetch_yahoo_chart(
                    conn,
                    symbol=symbol,
                    interval=interval,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    include_prepost=True,
                )
            except Exception as e:
                print(f"FAILED: {e}")
                polite_sleep(2.0)
                continue

            if not bars:
                print("0 bars (empty response)")
                polite_sleep(1.0)
                continue

            # Add timeframe field for upsert
            for bar in bars:
                bar["timeframe"] = interval
                bar["is_final"] = True

            n = upsert_ohlcv(conn, bars, source="yahoo_chart", batch_id=BATCH_ID)
            conn.commit()
            total_bars += n
            earliest = bars[0]["bar_start_ts"].strftime("%Y-%m-%d")
            latest = bars[-1]["bar_start_ts"].strftime("%Y-%m-%d")
            print(f"{len(bars)} bars fetched, {n} upserted ({earliest} → {latest})")
            polite_sleep(1.0)

    print(f"\n{'='*60}")
    print(f"Done. Total bars upserted: {total_bars}")
    print(f"Batch ID: {BATCH_ID}")

    # Summary query
    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol, timeframe, COUNT(*) as bars,
                   MIN(bar_start_ts)::date as earliest,
                   MAX(bar_start_ts)::date as latest
            FROM ohlcv
            WHERE symbol = ANY(%s)
              AND timeframe IN ('5m', '15m', '30m')
            GROUP BY symbol, timeframe
            ORDER BY symbol, timeframe;
        """, (SYMBOLS,))
        rows = cur.fetchall()
        if rows:
            print(f"\nData summary:")
            print(f"{'Symbol':<8} {'TF':<6} {'Bars':>8} {'Earliest':<12} {'Latest':<12}")
            print("-" * 50)
            for r in rows:
                print(f"{r['symbol']:<8} {r['timeframe']:<6} {r['bars']:>8} {r['earliest']}   {r['latest']}")

    conn.close()


if __name__ == "__main__":
    main()
