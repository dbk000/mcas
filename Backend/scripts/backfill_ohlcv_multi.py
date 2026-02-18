#!/usr/bin/env python3
"""
Multi-Timeframe OHLCV Backfill — Concurrent Pipeline
======================================================

Backfills OHLCV from Binance REST API for multiple timeframes, using every
trick available to minimize wall-clock time:

  1. ThreadPoolExecutor(20) for parallel HTTP (I/O-bound, not CPU-bound)
  2. Adaptive rate limiting from X-MBX-USED-WEIGHT-1M response header
  3. Separate DB writer thread with batched inserts (never blocks HTTP workers)
  4. Resume from existing data (queries max bar_start_ts per symbol+timeframe)
  5. Priority ordering: largest timeframes first (3d/1d done in seconds)
  6. Connection pooling via requests.Session per thread
  7. Exponential backoff on rate limits, immediate retry on transient errors

Timeframes fetched: 1m, 3m, 30m, 2h, 6h, 8h, 12h, 1d, 3d
(1s excluded: 4.3B bars for 110d × 454 symbols ≈ 300 hours at API rate limits.
 Binance 1s klines have limited historical depth anyway.)

Estimated requests: ~102K (dominated by 1m at ~72K)
Estimated time at Binance weight limits: ~5-7 hours

Usage:
    tmux new -s backfill
    docker compose run --rm -T worker python3 /scripts/backfill_ohlcv_multi.py
    # Or specify days:
    docker compose run --rm -T worker python3 /scripts/backfill_ohlcv_multi.py --days 125
    # Only specific timeframes:
    docker compose run --rm -T worker python3 /scripts/backfill_ohlcv_multi.py --timeframes 1m,3m,30m
"""

import os
import sys
import time
import json
import queue
import logging
import argparse
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Env + path setup ────────────────────────────────────────────────────────

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))

import requests as req_lib
import psycopg

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

BINANCE_BASE = "https://api.binance.com/api/v3"

# Timeframes ordered largest-first (fast completions first, 1m last)
ALL_TIMEFRAMES = ['3d', '1d', '12h', '8h', '6h', '2h', '30m', '3m', '1m']

TF_MS = {
    '1m':  60_000,
    '3m':  180_000,
    '5m':  300_000,
    '15m': 900_000,
    '30m': 1_800_000,
    '1h':  3_600_000,
    '2h':  7_200_000,
    '4h':  14_400_000,
    '6h':  21_600_000,
    '8h':  28_800_000,
    '12h': 43_200_000,
    '1d':  86_400_000,
    '3d':  259_200_000,
}

# Binance rate limits: 1200 weight/minute. Klines at limit=1000 costs weight 5.
MAX_WEIGHT_PER_MIN = 1100   # stay under 1200 with buffer
KLINES_LIMIT = 1000         # bars per request (max)
KLINES_WEIGHT = 5           # weight at limit=1000

N_WORKERS = 20              # concurrent HTTP threads
DB_BATCH_SIZE = 10_000      # rows per DB insert batch
MAX_RETRIES = 5             # per-request retry limit


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════════

class AdaptiveRateLimiter:
    """Thread-safe rate limiter that adapts from Binance response headers.

    Uses X-MBX-USED-WEIGHT-1M header to track actual server-side weight usage.
    Falls back to self-tracked weight when header isn't available.
    """

    def __init__(self, max_weight=MAX_WEIGHT_PER_MIN):
        self._lock = threading.Lock()
        self._max_weight = max_weight
        self._tracked_weight = 0
        self._server_weight = 0
        self._window_start = time.monotonic()
        self._total_requests = 0
        self._total_wait_seconds = 0.0

    def acquire(self, weight=KLINES_WEIGHT):
        """Block until we have budget for this request."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._window_start
                if elapsed >= 60.0:
                    self._tracked_weight = 0
                    self._server_weight = 0
                    self._window_start = now

                current = max(self._tracked_weight, self._server_weight)
                if current + weight <= self._max_weight:
                    self._tracked_weight += weight
                    self._total_requests += 1
                    return
            # Wait and retry
            wait = 0.3
            self._total_wait_seconds += wait
            time.sleep(wait)

    def update_from_response(self, response):
        """Update server-side weight from response headers."""
        header = response.headers.get('X-MBX-USED-WEIGHT-1M')
        if header:
            with self._lock:
                self._server_weight = int(header)

    @property
    def stats(self):
        with self._lock:
            return {
                "total_requests": self._total_requests,
                "total_wait_seconds": round(self._total_wait_seconds, 1),
                "current_weight": max(self._tracked_weight, self._server_weight),
            }


# ═══════════════════════════════════════════════════════════════════════════════
# DB OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_existing_symbols(db_url):
    """Get symbols from existing 1h OHLCV data (our canonical symbol set)."""
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv
                WHERE timeframe = '1h' AND is_final = TRUE
                ORDER BY symbol
            """)
            return [row[0] for row in cur]


def get_existing_ranges(db_url, symbols, timeframes):
    """Get max bar_start_ts per (symbol, timeframe) for resume support.

    Returns: dict[(symbol, timeframe)] -> max_ts_epoch_ms (or 0 if no data)
    """
    ranges = {}
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Batch query
            for tf in timeframes:
                cur.execute("""
                    SELECT symbol, EXTRACT(EPOCH FROM MAX(bar_start_ts))::bigint * 1000
                    FROM ohlcv
                    WHERE timeframe = %s AND is_final = TRUE AND symbol = ANY(%s)
                    GROUP BY symbol
                """, (tf, symbols))
                for row in cur:
                    ranges[(row[0], tf)] = int(row[1])
    return ranges


def ensure_unique_index(db_url):
    """Ensure the unique index exists for ON CONFLICT."""
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ohlcv_unique_bar
                ON ohlcv(symbol, timeframe, bar_start_ts, source)
            """)
        conn.commit()
    logger.info("Unique index ensured")


def db_writer_loop(db_url, write_queue, stats):
    """Dedicated DB writer thread. Reads from queue, writes in batches.

    Runs until it receives None sentinel.
    """
    conn = psycopg.connect(db_url)
    batch = []
    total_written = 0

    try:
        while True:
            try:
                item = write_queue.get(timeout=2)
            except queue.Empty:
                # Flush partial batch on timeout
                if batch:
                    _flush_batch(conn, batch)
                    total_written += len(batch)
                    stats['bars_written'] = total_written
                    batch = []
                continue

            if item is None:
                # Sentinel: flush and exit
                if batch:
                    _flush_batch(conn, batch)
                    total_written += len(batch)
                    stats['bars_written'] = total_written
                break

            batch.extend(item)
            if len(batch) >= DB_BATCH_SIZE:
                _flush_batch(conn, batch)
                total_written += len(batch)
                stats['bars_written'] = total_written
                batch = []
    finally:
        conn.close()

    logger.info("DB writer: %d total bars written", total_written)


def _flush_batch(conn, batch):
    """Insert a batch of rows into ohlcv."""
    if not batch:
        return
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO ohlcv (
                symbol, timeframe, bar_start_ts,
                open, high, low, close, volume,
                as_of_ts, is_final, source, batch_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, batch)
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

# Thread-local storage for requests.Session (connection pooling per thread)
_thread_local = threading.local()


def _get_session():
    if not hasattr(_thread_local, 'session'):
        s = req_lib.Session()
        # Connection pooling: keep-alive with generous pool size
        adapter = req_lib.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
            max_retries=req_lib.adapters.Retry(total=2, backoff_factor=0.5)
        )
        s.mount('https://', adapter)
        _thread_local.session = s
    return _thread_local.session


def fetch_symbol_timeframe(symbol, tf, start_ms, end_ms,
                           rate_limiter, write_queue, batch_id):
    """Fetch all klines for one (symbol, timeframe) range.

    Paginates through Binance klines API (1000 bars/request).
    Pushes parsed rows to write_queue for async DB insertion.
    Returns number of bars fetched.
    """
    session = _get_session()
    ms_per_bar = TF_MS[tf]
    cursor_ms = start_ms
    total_bars = 0

    while cursor_ms < end_ms:
        # Acquire rate limit budget
        rate_limiter.acquire(KLINES_WEIGHT)

        params = {
            "symbol": symbol,
            "interval": tf,
            "startTime": cursor_ms,
            "endTime": end_ms,
            "limit": KLINES_LIMIT,
        }

        retries = 0
        while retries < MAX_RETRIES:
            try:
                resp = session.get(f"{BINANCE_BASE}/klines", params=params, timeout=30)
                rate_limiter.update_from_response(resp)

                if resp.status_code in (418, 429):
                    # Rate limited — back off using server's Retry-After
                    retry_after = int(resp.headers.get("Retry-After", 30))
                    logger.warning("Rate limited (%d), sleeping %ds...",
                                   resp.status_code, retry_after)
                    time.sleep(retry_after)
                    retries += 1
                    continue

                resp.raise_for_status()
                data = resp.json()
                break

            except (req_lib.exceptions.RequestException, ValueError) as e:
                retries += 1
                if retries >= MAX_RETRIES:
                    logger.warning("Failed %s %s after %d retries: %s",
                                   symbol, tf, MAX_RETRIES, e)
                    return total_bars
                time.sleep(1.0 * retries)
                continue
        else:
            # All retries exhausted
            return total_bars

        if not data:
            break

        # Parse klines into DB rows
        now_ts = datetime.now(timezone.utc)
        rows = []
        for k in data:
            bar_ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
            rows.append((
                symbol, tf, bar_ts,
                float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
                now_ts, True, "binance", batch_id,
            ))

        if rows:
            write_queue.put(rows)
            total_bars += len(rows)

        # Advance cursor past the last bar
        last_ts_ms = int(data[-1][0])
        if last_ts_ms <= cursor_ms:
            break  # no progress
        cursor_ms = last_ts_ms + ms_per_bar

        # If we got fewer than limit, we've reached the end
        if len(data) < KLINES_LIMIT:
            break

    return total_bars


# ═══════════════════════════════════════════════════════════════════════════════
# TASK PLANNING
# ═══════════════════════════════════════════════════════════════════════════════

def build_tasks(symbols, timeframes, existing_ranges, days):
    """Build list of (symbol, tf, start_ms, end_ms) fetch tasks.

    Resumes from existing data: if we already have data for a symbol+tf,
    start from the last bar + 1 interval. Skips tasks where we're already
    up to date.

    Returns: list of tasks, dict of estimated request counts per timeframe
    """
    now_ms = int(time.time() * 1000)
    full_start_ms = now_ms - days * 24 * 60 * 60 * 1000

    tasks = []
    estimates = defaultdict(lambda: {"tasks": 0, "est_requests": 0, "est_bars": 0})

    for tf in timeframes:
        ms_per_bar = TF_MS[tf]
        for symbol in symbols:
            key = (symbol, tf)
            if key in existing_ranges:
                # Resume from last bar + 1 interval
                start_ms = existing_ranges[key] + ms_per_bar
            else:
                start_ms = full_start_ms

            if start_ms >= now_ms - ms_per_bar:
                continue  # already up to date

            bars_needed = (now_ms - start_ms) // ms_per_bar
            requests_needed = max(1, (bars_needed + KLINES_LIMIT - 1) // KLINES_LIMIT)

            tasks.append((symbol, tf, start_ms, now_ms))
            estimates[tf]["tasks"] += 1
            estimates[tf]["est_requests"] += requests_needed
            estimates[tf]["est_bars"] += bars_needed

    return tasks, dict(estimates)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Multi-Timeframe OHLCV Backfill")
    parser.add_argument("--days", type=int, default=125,
                        help="Days of history to backfill (default 125, covers 110 + warmup)")
    parser.add_argument("--timeframes", type=str, default=None,
                        help="Comma-separated timeframes (default: all new TFs)")
    parser.add_argument("--symbols", type=int, default=0,
                        help="Limit to top N symbols by existing bar count (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show task plan without fetching")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    t_start = time.time()
    batch_id = f"multi_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    if args.timeframes:
        timeframes = [tf.strip() for tf in args.timeframes.split(",")]
        # Validate
        for tf in timeframes:
            if tf not in TF_MS:
                logger.error("Unknown timeframe: %s. Valid: %s", tf, list(TF_MS.keys()))
                sys.exit(1)
        # Sort by bar size descending (largest first)
        timeframes.sort(key=lambda tf: -TF_MS[tf])
    else:
        timeframes = ALL_TIMEFRAMES

    logger.info("=" * 70)
    logger.info("MULTI-TIMEFRAME OHLCV BACKFILL")
    logger.info("=" * 70)
    logger.info("Config: days=%d, timeframes=%s, workers=%d", args.days, timeframes, N_WORKERS)

    # ── Step 1: Get symbol list from existing 1h data ──
    logger.info("\nStep 1: Loading symbol list from existing 1h data...")
    symbols = get_existing_symbols(db_url)
    if args.symbols > 0:
        symbols = symbols[:args.symbols]
    logger.info("  %d symbols", len(symbols))

    # ── Step 2: Check existing data ranges for resume ──
    logger.info("\nStep 2: Checking existing data for resume...")
    existing_ranges = get_existing_ranges(db_url, symbols, timeframes)
    logger.info("  %d existing (symbol, tf) ranges found", len(existing_ranges))

    # ── Step 3: Build task list ──
    logger.info("\nStep 3: Planning fetch tasks...")
    tasks, estimates = build_tasks(symbols, timeframes, existing_ranges, args.days)

    total_requests = sum(e["est_requests"] for e in estimates.values())
    total_bars = sum(e["est_bars"] for e in estimates.values())
    est_minutes = total_requests * KLINES_WEIGHT / MAX_WEIGHT_PER_MIN

    logger.info("  Task plan:")
    logger.info("  %-6s  %6s  %10s  %12s", "TF", "Tasks", "Requests", "Est. bars")
    logger.info("  " + "-" * 42)
    for tf in timeframes:
        if tf in estimates:
            e = estimates[tf]
            logger.info("  %-6s  %6d  %10d  %12s",
                        tf, e["tasks"], e["est_requests"],
                        f"{e['est_bars']:,}")
    logger.info("  " + "-" * 42)
    logger.info("  Total: %d tasks, %d requests, %s bars",
                len(tasks), total_requests, f"{total_bars:,}")
    logger.info("  Estimated time: %.0f minutes (%.1f hours) at %d weight/min",
                est_minutes, est_minutes / 60, MAX_WEIGHT_PER_MIN)

    if args.dry_run:
        logger.info("\n  --dry-run: exiting without fetching")
        return

    if not tasks:
        logger.info("\n  All data already up to date!")
        return

    # ── Step 4: Ensure DB index ──
    ensure_unique_index(db_url)

    # ── Step 5: Start DB writer thread ──
    write_queue = queue.Queue(maxsize=1000)
    writer_stats = {"bars_written": 0}
    writer_thread = threading.Thread(
        target=db_writer_loop, args=(db_url, write_queue, writer_stats),
        daemon=True, name="db-writer",
    )
    writer_thread.start()
    logger.info("\nDB writer thread started")

    # ── Step 6: Execute fetch tasks with thread pool ──
    logger.info("\nStep 6: Fetching data with %d workers...\n", N_WORKERS)
    rate_limiter = AdaptiveRateLimiter(MAX_WEIGHT_PER_MIN)

    # Group tasks by timeframe for ordered progress reporting
    tasks_by_tf = defaultdict(list)
    for task in tasks:
        tasks_by_tf[task[1]].append(task)

    completed_tasks = 0
    total_fetched_bars = 0
    tf_bars = defaultdict(int)

    with ThreadPoolExecutor(max_workers=N_WORKERS, thread_name_prefix="fetcher") as executor:
        for tf in timeframes:
            tf_tasks = tasks_by_tf.get(tf, [])
            if not tf_tasks:
                continue

            logger.info("── Timeframe: %s (%d symbol tasks, ~%s bars) ──",
                        tf, len(tf_tasks),
                        f"{estimates.get(tf, {}).get('est_bars', 0):,}")

            tf_t0 = time.time()

            # Submit all tasks for this timeframe
            futures = {}
            for symbol, tf_, start_ms, end_ms in tf_tasks:
                future = executor.submit(
                    fetch_symbol_timeframe,
                    symbol, tf_, start_ms, end_ms,
                    rate_limiter, write_queue, batch_id,
                )
                futures[future] = symbol

            # Collect results with progress
            tf_completed = 0
            tf_fetched = 0
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    n_bars = future.result()
                    tf_fetched += n_bars
                    total_fetched_bars += n_bars
                except Exception as e:
                    logger.error("  %s %s failed: %s", symbol, tf, e)

                tf_completed += 1
                completed_tasks += 1

                # Progress every 25 completions or at end
                if tf_completed % 25 == 0 or tf_completed == len(tf_tasks):
                    elapsed = time.time() - t_start
                    rate = completed_tasks / elapsed if elapsed > 0 else 0
                    eta = (len(tasks) - completed_tasks) / rate if rate > 0 else 0
                    rl_stats = rate_limiter.stats
                    logger.info("  %s: %d/%d symbols, %s bars | "
                                "Overall: %d/%d tasks (%.1f/s), "
                                "DB: %s written, ETA: %.0fm | "
                                "Weight: %d/%d, wait: %.0fs",
                                tf, tf_completed, len(tf_tasks),
                                f"{tf_fetched:,}",
                                completed_tasks, len(tasks), rate,
                                f"{writer_stats['bars_written']:,}",
                                eta / 60,
                                rl_stats["current_weight"], MAX_WEIGHT_PER_MIN,
                                rl_stats["total_wait_seconds"])

            tf_elapsed = time.time() - tf_t0
            tf_bars[tf] = tf_fetched
            logger.info("  %s complete: %s bars in %.1fs\n",
                        tf, f"{tf_fetched:,}", tf_elapsed)

    # ── Step 7: Shutdown DB writer ──
    write_queue.put(None)  # sentinel
    writer_thread.join(timeout=60)
    logger.info("DB writer finished: %s bars written", f"{writer_stats['bars_written']:,}")

    # ── Summary ──
    elapsed_total = time.time() - t_start
    rl_stats = rate_limiter.stats

    logger.info("\n" + "=" * 70)
    logger.info("BACKFILL COMPLETE")
    logger.info("=" * 70)
    logger.info("Elapsed: %.1f minutes (%.1f hours)", elapsed_total / 60, elapsed_total / 3600)
    logger.info("Symbols: %d", len(symbols))
    logger.info("API requests: %d (%.0fs waiting on rate limits)",
                rl_stats["total_requests"], rl_stats["total_wait_seconds"])
    logger.info("Bars fetched: %s", f"{total_fetched_bars:,}")
    logger.info("Bars written to DB: %s", f"{writer_stats['bars_written']:,}")
    logger.info("\nPer timeframe:")
    for tf in timeframes:
        if tf in tf_bars:
            logger.info("  %-6s  %s bars", tf, f"{tf_bars[tf]:,}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
