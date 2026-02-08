#!/usr/bin/env python3
"""
Backfill OHLCV data for expired opportunities that lack simulation data.

Queries the DB for symbols/timeframes with expired opportunities,
checks what OHLCV data is missing, and triggers ingestion from Binance.

Usage:
    python scripts/backfill_for_simulation.py

Requires DATABASE_URL and API_KEY env vars (reads from .env).
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# Load .env if available
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

import psycopg

DATABASE_URL = os.environ.get('DATABASE_URL')
API_KEY = os.environ.get('API_KEY')
API_BASE = os.environ.get('API_BASE', 'http://localhost:8000')

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)


def get_missing_coverage():
    """Find symbols/timeframes that have expired opportunities but no OHLCV data."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # Get all distinct symbol/timeframe combos from expired opportunities
            cur.execute("""
                SELECT DISTINCT o.symbol, o.timeframe,
                       MIN(o.created_ts) as earliest_opp,
                       MAX(o.created_ts) as latest_opp,
                       COUNT(*) as opp_count
                FROM opportunities o
                WHERE o.status = 'expired'
                GROUP BY o.symbol, o.timeframe
                ORDER BY opp_count DESC;
            """)
            opp_combos = cur.fetchall()

            # Get what OHLCV data we already have
            cur.execute("""
                SELECT symbol, timeframe,
                       MIN(bar_start_ts) as earliest,
                       MAX(bar_start_ts) as latest,
                       COUNT(*) as bar_count
                FROM ohlcv
                GROUP BY symbol, timeframe;
            """)
            ohlcv_coverage = {
                (row[0], row[1]): {
                    'earliest': row[2],
                    'latest': row[3],
                    'bars': row[4],
                }
                for row in cur.fetchall()
            }

    missing = []
    partial = []

    for symbol, tf, earliest_opp, latest_opp, opp_count in opp_combos:
        key = (symbol, tf)
        if key not in ohlcv_coverage:
            missing.append({
                'symbol': symbol,
                'timeframe': tf,
                'opp_count': opp_count,
                'earliest_opp': earliest_opp,
                'latest_opp': latest_opp,
                'reason': 'no_data',
            })
        else:
            cov = ohlcv_coverage[key]
            # Check if coverage spans the opportunity range
            # Need data from earliest_opp to latest_opp + some forward bars
            if cov['earliest'] > earliest_opp or cov['latest'] < latest_opp:
                partial.append({
                    'symbol': symbol,
                    'timeframe': tf,
                    'opp_count': opp_count,
                    'earliest_opp': earliest_opp,
                    'latest_opp': latest_opp,
                    'data_start': cov['earliest'],
                    'data_end': cov['latest'],
                    'reason': 'partial_coverage',
                })

    return missing, partial


def ingest_batch(symbols, timeframes, start_date, end_date=None, batch_size=10):
    """Call the ingestion API in batches to avoid timeouts."""
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
    }

    total_ingested = 0

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        payload = {
            'symbols': batch,
            'timeframes': timeframes,
            'start_date': start_date,
        }
        if end_date:
            payload['end_date'] = end_date

        print(f"\n  Batch {i // batch_size + 1}: {len(batch)} symbols "
              f"({', '.join(batch[:3])}{'...' if len(batch) > 3 else ''})")

        try:
            resp = requests.post(
                f"{API_BASE}/v1/data/ingest",
                headers=headers,
                json=payload,
                timeout=600,  # 10 min timeout for large batches
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data.get('data', data)
                bars = result.get('total_bars_ingested', 0)
                total_ingested += bars
                print(f"    -> {bars} bars ingested")
            else:
                print(f"    -> ERROR {resp.status_code}: {resp.text[:200]}")
        except requests.exceptions.Timeout:
            print(f"    -> TIMEOUT (batch too large, try smaller batch_size)")
        except Exception as e:
            print(f"    -> ERROR: {e}")

        time.sleep(1)  # Rate limit between batches

    return total_ingested


def main():
    print("=" * 60)
    print("MCAS OHLCV Backfill for Simulation")
    print("=" * 60)

    print("\nChecking missing OHLCV coverage...")
    missing, partial = get_missing_coverage()

    print(f"\n  Fully missing:    {len(missing)} symbol/timeframe combos")
    print(f"  Partial coverage: {len(partial)} symbol/timeframe combos")

    if not missing and not partial:
        print("\n  All expired opportunities have OHLCV data!")
        return

    # Group by timeframe for summary
    tf_counts = {}
    all_symbols = set()
    all_timeframes = set()
    earliest_date = None

    for item in missing + partial:
        tf = item['timeframe']
        tf_counts[tf] = tf_counts.get(tf, 0) + item['opp_count']
        all_symbols.add(item['symbol'])
        all_timeframes.add(tf)
        opp_earliest = item['earliest_opp']
        if opp_earliest.tzinfo is None:
            opp_earliest = opp_earliest.replace(tzinfo=timezone.utc)
        if earliest_date is None or opp_earliest < earliest_date:
            earliest_date = opp_earliest

    print(f"\n  Unique symbols needing data: {len(all_symbols)}")
    print(f"  Timeframes: {sorted(all_timeframes)}")
    print(f"  Earliest opportunity: {earliest_date.isoformat()}")
    print(f"\n  Per-timeframe opportunity counts:")
    for tf, count in sorted(tf_counts.items()):
        print(f"    {tf}: {count} opportunities")

    if not API_KEY:
        print("\n  ERROR: API_KEY not set. Cannot trigger ingestion.")
        print("  Set API_KEY in .env or environment.")
        sys.exit(1)

    # Start ingestion - go back 1 day before earliest opportunity
    start_date = (earliest_date - timedelta(days=1)).strftime('%Y-%m-%d')
    symbols_list = sorted(all_symbols)
    timeframes_list = sorted(all_timeframes)

    print(f"\n{'=' * 60}")
    print(f"Starting ingestion:")
    print(f"  Symbols: {len(symbols_list)}")
    print(f"  Timeframes: {timeframes_list}")
    print(f"  Start date: {start_date}")
    print(f"  End date: now")
    print(f"{'=' * 60}")

    total = ingest_batch(
        symbols=symbols_list,
        timeframes=timeframes_list,
        start_date=start_date,
        batch_size=5,  # 5 symbols per batch to avoid timeouts
    )

    print(f"\n{'=' * 60}")
    print(f"Backfill complete: {total} total bars ingested")
    print(f"{'=' * 60}")
    print(f"\nYou can now re-run the simulation from the UI.")


if __name__ == '__main__':
    main()
