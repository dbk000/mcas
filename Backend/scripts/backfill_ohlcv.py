#!/usr/bin/env python3
"""
Backfill OHLCV data from Binance REST into Postgres ohlcv table.

Fetches historical candles for top USDT pairs across all timeframes and
upserts into the existing ohlcv table. Data is reusable across all future
backfill/sim runs — fetch once, regenerate opportunities many times.

Usage:
    python Backend/scripts/backfill_ohlcv.py --days 7
    python Backend/scripts/backfill_ohlcv.py --days 7 --symbols 50 --timeframes 1h,4h,1d

Requires DATABASE_URL env var (reads from .env if available).
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')
BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_TIMEOUT = 15

# Timeframe → milliseconds mapping
TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

ALL_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
MAX_BARS_PER_REQUEST = 1000
MAX_WORKERS = 20
RATE_LIMIT_DELAY = 0.1  # seconds between requests per worker


def fetch_top_symbols(n: int = 200) -> list[str]:
    """Fetch top N USDT pairs by 24h quote volume from Binance."""
    try:
        resp = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=15)
        resp.raise_for_status()
        tickers = resp.json()
        usdt_pairs = [
            t for t in tickers
            if t["symbol"].endswith("USDT")
            and float(t["quoteVolume"]) > 0
        ]
        usdt_pairs.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
        symbols = [t["symbol"] for t in usdt_pairs[:n]]
        logger.info(f"Fetched {len(symbols)} symbols from Binance")
        return symbols
    except Exception as e:
        logger.error(f"Failed to fetch symbols: {e}")
        sys.exit(1)


def fetch_klines_paginated(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Fetch all klines for a symbol/interval between start_ms and end_ms.

    Paginates with startTime/endTime, max 1000 bars per request.
    Returns list of candle dicts, oldest first.
    """
    all_candles = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": MAX_BARS_PER_REQUEST,
        }

        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{BINANCE_BASE}/klines",
                    params=params,
                    timeout=BINANCE_TIMEOUT,
                )
                if resp.status_code in (418, 429):
                    wait = (2 ** attempt) * 2
                    logger.warning(f"Rate limited ({resp.status_code}) for {symbol} {interval}, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    logger.warning(f"Failed after 3 attempts for {symbol} {interval}: {e}")
                    return all_candles
                time.sleep(1)
                continue
        else:
            break

        if not data:
            break

        for k in data:
            all_candles.append({
                "bar_start_ts": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

        # Move start to after last candle
        last_ts = int(data[-1][0])
        tf_ms = TF_MS.get(interval, 60_000)
        current_start = last_ts + tf_ms

        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

    return all_candles


def upsert_candles(candles: list[dict], symbol: str, timeframe: str, batch_id: str) -> int:
    """Bulk upsert candles into ohlcv table. Returns count of rows inserted."""
    if not candles:
        return 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # Use COPY for bulk insert with conflict handling via temp table
            cur.execute("""
                CREATE TEMP TABLE _ohlcv_staging (
                    symbol TEXT,
                    timeframe TEXT,
                    bar_start_ts TIMESTAMPTZ,
                    open DOUBLE PRECISION,
                    high DOUBLE PRECISION,
                    low DOUBLE PRECISION,
                    close DOUBLE PRECISION,
                    volume DOUBLE PRECISION,
                    source TEXT,
                    batch_id TEXT
                ) ON COMMIT DROP;
            """)

            # Batch insert into staging
            values = []
            for c in candles:
                ts = datetime.fromtimestamp(c["bar_start_ts"] / 1000, tz=timezone.utc)
                values.append((
                    symbol, timeframe, ts,
                    c["open"], c["high"], c["low"], c["close"], c["volume"],
                    "binance", batch_id,
                ))

            cur.executemany("""
                INSERT INTO _ohlcv_staging
                    (symbol, timeframe, bar_start_ts, open, high, low, close, volume, source, batch_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, values)

            # Upsert from staging to main table
            cur.execute("""
                INSERT INTO ohlcv
                    (symbol, timeframe, bar_start_ts, open, high, low, close, volume,
                     is_final, source, batch_id, as_of_ts, ingested_at)
                SELECT symbol, timeframe, bar_start_ts, open, high, low, close, volume,
                       TRUE, source, batch_id, NOW(), NOW()
                FROM _ohlcv_staging
                ON CONFLICT (symbol, timeframe, bar_start_ts, source) DO NOTHING;
            """)

            inserted = cur.rowcount
        conn.commit()

    return inserted


def backfill_symbol(
    symbol: str,
    timeframes: list[str],
    start_ms: int,
    end_ms: int,
    batch_id: str,
) -> dict:
    """Backfill all timeframes for one symbol. Returns stats dict."""
    stats = {"symbol": symbol, "bars_fetched": 0, "bars_inserted": 0, "timeframes": {}}

    for tf in timeframes:
        # For warmup, go back extra bars
        warmup_bars = 500
        tf_ms = TF_MS[tf]
        warmup_ms = warmup_bars * tf_ms
        effective_start = start_ms - warmup_ms

        candles = fetch_klines_paginated(symbol, tf, effective_start, end_ms)
        inserted = upsert_candles(candles, symbol, tf, batch_id)

        stats["bars_fetched"] += len(candles)
        stats["bars_inserted"] += inserted
        stats["timeframes"][tf] = {"fetched": len(candles), "inserted": inserted}

    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill OHLCV data from Binance")
    parser.add_argument("--days", type=int, default=7, help="Number of days to backfill (default: 7)")
    parser.add_argument("--symbols", type=int, default=200, help="Number of top symbols to fetch (default: 200)")
    parser.add_argument("--timeframes", type=str, default=None,
                        help="Comma-separated timeframes (default: all 6)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"Concurrent fetch workers (default: {MAX_WORKERS})")
    parser.add_argument("--symbol-list", type=str, default=None,
                        help="Comma-separated specific symbols (overrides --symbols count)")
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set. Set it in .env or environment.")
        sys.exit(1)

    # Determine symbols
    if args.symbol_list:
        symbols = [s.strip().upper() for s in args.symbol_list.split(",")]
        logger.info(f"Using {len(symbols)} specified symbols")
    else:
        symbols = fetch_top_symbols(args.symbols)

    # Determine timeframes
    timeframes = ALL_TIMEFRAMES
    if args.timeframes:
        timeframes = [t.strip() for t in args.timeframes.split(",")]
        for tf in timeframes:
            if tf not in TF_MS:
                logger.error(f"Invalid timeframe: {tf}. Valid: {list(TF_MS.keys())}")
                sys.exit(1)

    # Time range
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=args.days)).timestamp() * 1000)
    batch_id = f"backfill_{now.strftime('%Y%m%d_%H%M%S')}"

    logger.info("=" * 60)
    logger.info("MCAS OHLCV Backfill")
    logger.info("=" * 60)
    logger.info(f"  Symbols:    {len(symbols)}")
    logger.info(f"  Timeframes: {timeframes}")
    logger.info(f"  Days:       {args.days}")
    logger.info(f"  Workers:    {args.workers}")
    logger.info(f"  Batch ID:   {batch_id}")
    logger.info(f"  Start:      {datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).isoformat()}")
    logger.info(f"  End:        {now.isoformat()}")
    logger.info("=" * 60)

    t0 = time.time()
    total_fetched = 0
    total_inserted = 0
    completed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                backfill_symbol, sym, timeframes, start_ms, end_ms, batch_id
            ): sym
            for sym in symbols
        }

        for future in as_completed(futures):
            sym = futures[future]
            completed += 1
            try:
                stats = future.result()
                total_fetched += stats["bars_fetched"]
                total_inserted += stats["bars_inserted"]
                if completed % 10 == 0 or completed == len(symbols):
                    elapsed = time.time() - t0
                    logger.info(
                        f"  [{completed}/{len(symbols)}] "
                        f"{total_fetched:,} fetched, {total_inserted:,} inserted "
                        f"({elapsed:.0f}s elapsed)"
                    )
            except Exception as e:
                logger.error(f"  Failed for {sym}: {e}")

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info(f"Backfill complete in {elapsed:.1f}s")
    logger.info(f"  Total bars fetched:  {total_fetched:,}")
    logger.info(f"  Total bars inserted: {total_inserted:,}")
    logger.info(f"  Batch ID: {batch_id}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
