"""
OHLCV Backfill — shared module for self-healing OHLCV pipeline.

Provides gap detection, Binance REST fetching, and batch upsert for:
1. Persister startup recovery (advisory-locked)
2. Persister hourly healing (advisory-locked)
3. Pre-simulation targeted backfill (scoped, no global lock)

All ranges are half-open [start, end).
All timestamps are UTC.
Binance timestamps are ms — divided by 1000 at conversion points.
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta

import requests
import psycopg

from cache_contract import TF_SECONDS

logger = logging.getLogger("ohlcv_backfill")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_TIMEOUT = 15
MAX_BARS_PER_REQUEST = 1000

# Timeframe → milliseconds
TF_MS = {tf: secs * 1000 for tf, secs in TF_SECONDS.items()}


# ---------------------------------------------------------------------------
# Boundary snapping (half-open ranges)
# ---------------------------------------------------------------------------

def snap_down(ts: datetime, tf_seconds: int) -> datetime:
    """Floor ts to nearest TF boundary."""
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(
        (epoch // tf_seconds) * tf_seconds, tz=timezone.utc
    )


def snap_up(ts: datetime, tf_seconds: int) -> datetime:
    """Ceil ts to next TF boundary (no-op if already on boundary)."""
    epoch = int(ts.timestamp())
    snapped = (epoch // tf_seconds) * tf_seconds
    if snapped < epoch:
        snapped += tf_seconds
    return datetime.fromtimestamp(snapped, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Rate limiter (thread-safe token bucket with backoff)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Thread-safe rate limiter using token bucket."""

    def __init__(self, rate: float = 6.0):
        self._lock = threading.Lock()
        self._rate = rate
        self._last = 0.0
        self._backoff_until = 0.0

    def wait(self):
        with self._lock:
            now = time.time()
            if now < self._backoff_until:
                wait = self._backoff_until - now
                time.sleep(wait)
                now = time.time()
            min_interval = 1.0 / self._rate
            elapsed = now - self._last
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last = time.time()

    def trigger_backoff(self, seconds: float = 10.0):
        """Called when 429/418 received — globally slow down."""
        with self._lock:
            self._backoff_until = max(self._backoff_until, time.time() + seconds)


_limiter = RateLimiter(rate=float(os.getenv("BACKFILL_RATE_PER_SEC", "6")))


# ---------------------------------------------------------------------------
# Advisory lock helpers (Postgres-level distributed lock)
# ---------------------------------------------------------------------------

def _acquire_backfill_lock(conn) -> bool:
    """Try to acquire system-wide backfill lock. Returns True if acquired."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext('ohlcv_backfill'))")
        return cur.fetchone()[0]


def _release_backfill_lock(conn):
    """Release system-wide backfill lock."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext('ohlcv_backfill'))")


# ---------------------------------------------------------------------------
# Binance REST: paginated kline fetch
# ---------------------------------------------------------------------------

def fetch_klines_paginated(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """
    Fetch all klines from Binance between start_ms and end_ms (inclusive).

    Paginates with startTime/endTime, max 1000 bars per request.
    Rate-limited via shared _limiter. Returns list of candle dicts.

    Candle bar_start_ts values are Binance ms epoch.
    """
    all_candles = []
    current_start = start_ms

    while current_start < end_ms:
        _limiter.wait()

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
                    logger.warning(
                        "Rate limited (%d) for %s %s, backoff %ds",
                        resp.status_code, symbol, interval, wait,
                    )
                    _limiter.trigger_backoff(wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    logger.warning(
                        "Failed after 3 attempts for %s %s: %s",
                        symbol, interval, e,
                    )
                    return all_candles
                time.sleep(1)
                continue
        else:
            break

        if not data:
            break

        for k in data:
            all_candles.append({
                "bar_start_ts": int(k[0]),  # Binance ms epoch
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

        # Move start past last candle
        last_ts = int(data[-1][0])
        tf_ms = TF_MS.get(interval, 60_000)
        current_start = last_ts + tf_ms

    return all_candles


# ---------------------------------------------------------------------------
# Batch upsert: temp table + ON CONFLICT DO NOTHING
# ---------------------------------------------------------------------------

def upsert_candles_batch(
    db_url: str,
    candles: list[dict],
    symbol: str,
    tf: str,
    batch_id: str,
) -> int:
    """
    Bulk upsert candles into ohlcv table via temp staging table.

    source='binance', ON CONFLICT DO NOTHING. Never overwrites existing rows.
    Returns count of rows inserted.
    """
    if not candles:
        return 0

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
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

            values = []
            for c in candles:
                # Binance ms epoch → Python datetime
                ts = datetime.fromtimestamp(
                    c["bar_start_ts"] / 1000, tz=timezone.utc  # Binance ms epoch
                )
                values.append((
                    symbol, tf, ts,
                    c["open"], c["high"], c["low"], c["close"], c["volume"],
                    "binance", batch_id,
                ))

            cur.executemany("""
                INSERT INTO _ohlcv_staging
                    (symbol, timeframe, bar_start_ts, open, high, low, close,
                     volume, source, batch_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, values)

            cur.execute("""
                INSERT INTO ohlcv
                    (symbol, timeframe, bar_start_ts, open, high, low, close,
                     volume, is_final, source, batch_id, as_of_ts, ingested_at)
                SELECT symbol, timeframe, bar_start_ts, open, high, low, close,
                       volume, TRUE, source, batch_id, NOW(), NOW()
                FROM _ohlcv_staging
                ON CONFLICT (symbol, timeframe, bar_start_ts, source) DO NOTHING;
            """)

            inserted = cur.rowcount
        conn.commit()

    return inserted


# ---------------------------------------------------------------------------
# Gap detection: generate_series + NOT EXISTS
# ---------------------------------------------------------------------------

def detect_missing_bars(
    db_url: str,
    symbol: str,
    tf: str,
    start_ts: datetime,
    end_ts: datetime,
) -> list[datetime]:
    """
    Detect missing final bars in [start_ts, end_ts) using generate_series.

    Returns list of missing bar_start_ts values (UTC datetimes).
    """
    tf_secs = TF_SECONDS.get(tf)
    if not tf_secs:
        return []

    interval_str = f"{tf_secs} seconds"

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT gs.expected_ts
                FROM generate_series(
                    %s::timestamptz,
                    %s::timestamptz - %s::interval,
                    %s::interval
                ) AS gs(expected_ts)
                WHERE NOT EXISTS (
                    SELECT 1 FROM ohlcv
                    WHERE symbol = %s AND timeframe = %s
                    AND bar_start_ts = gs.expected_ts AND is_final = TRUE
                )
                ORDER BY gs.expected_ts;
            """, (start_ts, end_ts, interval_str, interval_str, symbol, tf))

            return [row[0] for row in cur.fetchall()]


def get_latest_bar_ts(
    db_url: str,
    symbol: str,
    tf: str,
) -> datetime | None:
    """Get the most recent bar_start_ts in Postgres for this symbol/tf."""
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(bar_start_ts)
                FROM ohlcv
                WHERE symbol = %s AND timeframe = %s AND is_final = TRUE;
            """, (symbol, tf))
            row = cur.fetchone()
            return row[0] if row and row[0] else None


# ---------------------------------------------------------------------------
# Coalesce missing timestamps into contiguous ranges
# ---------------------------------------------------------------------------

def _coalesce_ranges(
    missing: list[datetime],
    tf_seconds: int,
) -> list[tuple[datetime, datetime]]:
    """
    Coalesce consecutive missing timestamps into (start, end) ranges.

    Each range is half-open [start, end) where end = last_missing + tf_seconds.
    """
    if not missing:
        return []

    ranges = []
    range_start = missing[0]
    prev = missing[0]

    for ts in missing[1:]:
        expected_next = prev + timedelta(seconds=tf_seconds)
        if ts == expected_next:
            prev = ts
        else:
            # End of contiguous range: end = prev + tf_seconds (half-open)
            range_end = prev + timedelta(seconds=tf_seconds)
            ranges.append((range_start, range_end))
            range_start = ts
            prev = ts

    # Close final range
    range_end = prev + timedelta(seconds=tf_seconds)
    ranges.append((range_start, range_end))

    return ranges


# ---------------------------------------------------------------------------
# Backfill a single (symbol, timeframe) range
# ---------------------------------------------------------------------------

def backfill_symbol_timeframe(
    db_url: str,
    symbol: str,
    tf: str,
    start_ts: datetime,
    end_ts: datetime,
    batch_id: str,
) -> dict:
    """
    Detect gaps and backfill a single (symbol, timeframe) in [start_ts, end_ts).

    Returns stats dict: {fetched, inserted, gaps_found}.
    """
    tf_secs = TF_SECONDS.get(tf)
    if not tf_secs:
        return {"fetched": 0, "inserted": 0, "gaps_found": 0}

    # Snap boundaries
    snapped_start = snap_down(start_ts, tf_secs)
    snapped_end = snap_up(end_ts, tf_secs)

    # Detect missing bars
    missing = detect_missing_bars(db_url, symbol, tf, snapped_start, snapped_end)
    if not missing:
        return {"fetched": 0, "inserted": 0, "gaps_found": 0}

    # Coalesce into ranges for batch fetching
    ranges = _coalesce_ranges(missing, tf_secs)

    total_fetched = 0
    total_inserted = 0

    for range_start, range_end in ranges:
        start_ms = int(range_start.timestamp() * 1000)
        end_ms = int(range_end.timestamp() * 1000)

        candles = fetch_klines_paginated(symbol, tf, start_ms, end_ms)
        total_fetched += len(candles)

        if candles:
            inserted = upsert_candles_batch(db_url, candles, symbol, tf, batch_id)
            total_inserted += inserted

    return {
        "fetched": total_fetched,
        "inserted": total_inserted,
        "gaps_found": len(missing),
    }


# ---------------------------------------------------------------------------
# High-level backfill functions
# ---------------------------------------------------------------------------

def backfill_startup_recovery(
    db_url: str,
    symbols: list[str],
    timeframes: list[str],
    lookback_hours: int = 48,
) -> dict:
    """
    Advisory-locked startup recovery: fill gaps from min(latest_bar, now-48h) to now.

    Called once after cache:ready. If another process holds the lock, skip silently.
    Returns stats dict or None if lock not acquired.
    """
    batch_id = f"startup_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    now = datetime.now(timezone.utc)
    default_start = now - timedelta(hours=lookback_hours)

    with psycopg.connect(db_url) as lock_conn:
        if not _acquire_backfill_lock(lock_conn):
            logger.info("Startup recovery: another process holds lock, skipping")
            return None

        try:
            total_fetched = 0
            total_inserted = 0
            total_gaps = 0

            for symbol in symbols:
                for tf in timeframes:
                    tf_secs = TF_SECONDS.get(tf)
                    if not tf_secs:
                        continue

                    # Start from min(latest_bar, default_start)
                    latest = get_latest_bar_ts(db_url, symbol, tf)
                    if latest:
                        if latest.tzinfo is None:
                            latest = latest.replace(tzinfo=timezone.utc)
                        start = min(latest, default_start)
                    else:
                        start = default_start

                    stats = backfill_symbol_timeframe(
                        db_url, symbol, tf, start, now, batch_id,
                    )
                    total_fetched += stats["fetched"]
                    total_inserted += stats["inserted"]
                    total_gaps += stats["gaps_found"]

            logger.info(
                "Startup recovery complete: fetched=%d inserted=%d gaps=%d batch=%s",
                total_fetched, total_inserted, total_gaps, batch_id,
            )
            return {
                "fetched": total_fetched,
                "inserted": total_inserted,
                "gaps_found": total_gaps,
                "batch_id": batch_id,
            }
        finally:
            _release_backfill_lock(lock_conn)


def backfill_recent_gaps(
    db_url: str,
    symbols: list[str],
    timeframes: list[str],
    lookback_hours: int = 48,
) -> dict:
    """
    Advisory-locked hourly healing: fill gaps in the last lookback_hours.

    Called periodically. If another process holds the lock, skip silently.
    Returns stats dict or None if lock not acquired.
    """
    batch_id = f"heal_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=lookback_hours)

    with psycopg.connect(db_url) as lock_conn:
        if not _acquire_backfill_lock(lock_conn):
            logger.debug("Hourly healing: another process holds lock, skipping")
            return None

        try:
            total_fetched = 0
            total_inserted = 0
            total_gaps = 0

            for symbol in symbols:
                for tf in timeframes:
                    stats = backfill_symbol_timeframe(
                        db_url, symbol, tf, start, now, batch_id,
                    )
                    total_fetched += stats["fetched"]
                    total_inserted += stats["inserted"]
                    total_gaps += stats["gaps_found"]

            if total_gaps > 0:
                logger.info(
                    "Hourly healing complete: fetched=%d inserted=%d gaps=%d batch=%s",
                    total_fetched, total_inserted, total_gaps, batch_id,
                )
            else:
                logger.debug("Hourly healing: no gaps found")

            return {
                "fetched": total_fetched,
                "inserted": total_inserted,
                "gaps_found": total_gaps,
                "batch_id": batch_id,
            }
        finally:
            _release_backfill_lock(lock_conn)


def backfill_for_simulation(
    db_url: str,
    symbol_tf_ranges: dict[str, tuple[datetime, datetime]],
) -> dict:
    """
    Pre-simulation targeted backfill (no global lock — scoped per sim).

    Args:
        symbol_tf_ranges: dict keyed by "SYMBOL_TF" with (start_ts, end_ts) values.

    Returns stats dict: {fetched, inserted, gaps_found}.
    """
    batch_id = f"sim_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    total_fetched = 0
    total_inserted = 0
    total_gaps = 0

    for key, (start_ts, end_ts) in symbol_tf_ranges.items():
        symbol, tf = key.rsplit("_", 1)

        stats = backfill_symbol_timeframe(
            db_url, symbol, tf, start_ts, end_ts, batch_id,
        )
        total_fetched += stats["fetched"]
        total_inserted += stats["inserted"]
        total_gaps += stats["gaps_found"]

    if total_gaps > 0:
        logger.info(
            "Pre-sim backfill: fetched=%d inserted=%d gaps=%d symbols=%d batch=%s",
            total_fetched, total_inserted, total_gaps,
            len(symbol_tf_ranges), batch_id,
        )
    else:
        logger.debug("Pre-sim backfill: no gaps found")

    return {
        "fetched": total_fetched,
        "inserted": total_inserted,
        "gaps_found": total_gaps,
        "batch_id": batch_id,
    }
