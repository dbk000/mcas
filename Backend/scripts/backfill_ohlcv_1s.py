#!/usr/bin/env python3
"""
1-Second OHLCV Backfill for TKO Markets
=========================================

Pulls 1s klines from Binance for a configurable set of symbols (TKO_MARKETS).
Same concurrent architecture as backfill_ohlcv_multi.py: ThreadPoolExecutor,
adaptive rate limiting, separate DB writer thread.

Symbol sources (checked in order):
  1. --symbols CLI arg (comma-separated)
  2. --symbols-file CLI arg (one symbol per line)
  3. TKO_MARKETS env var (comma-separated)
  4. Backend/scripts/tko_markets.txt file (one symbol per line)

Binance 1s klines have limited historical depth (varies by symbol, typically
days to weeks). The script auto-detects the available range by fetching
backwards and stopping when Binance returns empty.

Data volume per symbol: ~86,400 bars/day = 87 API requests/day (limit=1000, weight 5).
At 1100 weight/min: ~220 requests/min → ~38 symbols/day at full depth.

Examples:
    # Pull 14 days for specific symbols
    docker compose run --rm -T worker python3 /scripts/backfill_ohlcv_1s.py \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT --days 14

    # Pull from a file
    echo -e "BTCUSDT\\nETHUSDT\\nSOLUSDT" > /scripts/tko_markets.txt
    docker compose run --rm -T worker python3 /scripts/backfill_ohlcv_1s.py --days 14

    # Dry run to see the plan
    docker compose run --rm -T worker python3 /scripts/backfill_ohlcv_1s.py \
        --symbols BTCUSDT,ETHUSDT --days 7 --dry-run
"""

import os
import sys
import time
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
TF = "1s"
MS_PER_BAR = 1_000          # 1 second

MAX_WEIGHT_PER_MIN = 1100
KLINES_LIMIT = 1000
KLINES_WEIGHT = 5
N_WORKERS = 20
DB_BATCH_SIZE = 50_000       # larger batches for 1s volume
MAX_RETRIES = 5


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER (same as backfill_ohlcv_multi.py)
# ═══════════════════════════════════════════════════════════════════════════════

class AdaptiveRateLimiter:
    def __init__(self, max_weight=MAX_WEIGHT_PER_MIN):
        self._lock = threading.Lock()
        self._max_weight = max_weight
        self._tracked_weight = 0
        self._server_weight = 0
        self._window_start = time.monotonic()
        self._total_requests = 0
        self._total_wait_seconds = 0.0

    def acquire(self, weight=KLINES_WEIGHT):
        while True:
            with self._lock:
                now = time.monotonic()
                if now - self._window_start >= 60.0:
                    self._tracked_weight = 0
                    self._server_weight = 0
                    self._window_start = now
                current = max(self._tracked_weight, self._server_weight)
                if current + weight <= self._max_weight:
                    self._tracked_weight += weight
                    self._total_requests += 1
                    return
            wait = 0.3
            self._total_wait_seconds += wait
            time.sleep(wait)

    def update_from_response(self, response):
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

def get_existing_ranges(db_url, symbols):
    """Get max bar_start_ts for 1s data per symbol. Returns dict[symbol] -> epoch_ms."""
    ranges = {}
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, EXTRACT(EPOCH FROM MAX(bar_start_ts))::bigint * 1000
                FROM ohlcv
                WHERE timeframe = '1s' AND is_final = TRUE AND symbol = ANY(%s)
                GROUP BY symbol
            """, (symbols,))
            for row in cur:
                ranges[row[0]] = int(row[1])
    return ranges


def ensure_unique_index(db_url):
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ohlcv_unique_bar
                ON ohlcv(symbol, timeframe, bar_start_ts, source)
            """)
        conn.commit()


def db_writer_loop(db_url, write_queue, stats):
    conn = psycopg.connect(db_url)
    batch = []
    total_written = 0
    try:
        while True:
            try:
                item = write_queue.get(timeout=2)
            except queue.Empty:
                if batch:
                    _flush_batch(conn, batch)
                    total_written += len(batch)
                    stats['bars_written'] = total_written
                    batch = []
                continue
            if item is None:
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

_thread_local = threading.local()

def _get_session():
    if not hasattr(_thread_local, 'session'):
        s = req_lib.Session()
        adapter = req_lib.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
            max_retries=req_lib.adapters.Retry(total=2, backoff_factor=0.5)
        )
        s.mount('https://', adapter)
        _thread_local.session = s
    return _thread_local.session


def fetch_symbol_1s(symbol, start_ms, end_ms, rate_limiter, write_queue, batch_id):
    """Fetch all 1s klines for one symbol.

    Returns: (n_bars_fetched, actual_earliest_ms or None)
    """
    session = _get_session()
    cursor_ms = start_ms
    total_bars = 0
    earliest_ms = None

    while cursor_ms < end_ms:
        rate_limiter.acquire(KLINES_WEIGHT)

        params = {
            "symbol": symbol,
            "interval": TF,
            "startTime": cursor_ms,
            "endTime": end_ms,
            "limit": KLINES_LIMIT,
        }

        retries = 0
        data = None
        while retries < MAX_RETRIES:
            try:
                resp = session.get(f"{BINANCE_BASE}/klines", params=params, timeout=30)
                rate_limiter.update_from_response(resp)

                if resp.status_code in (418, 429):
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
                    logger.warning("Failed %s 1s after %d retries: %s",
                                   symbol, MAX_RETRIES, e)
                    return total_bars, earliest_ms
                time.sleep(1.0 * retries)

        if data is None or not data:
            break

        # Parse klines
        now_ts = datetime.now(timezone.utc)
        rows = []
        for k in data:
            bar_ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
            rows.append((
                symbol, TF, bar_ts,
                float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
                now_ts, True, "binance", batch_id,
            ))

        if rows:
            write_queue.put(rows)
            total_bars += len(rows)
            if earliest_ms is None:
                earliest_ms = int(data[0][0])

        # Advance cursor
        last_ts_ms = int(data[-1][0])
        if last_ts_ms <= cursor_ms:
            break
        cursor_ms = last_ts_ms + MS_PER_BAR

        if len(data) < KLINES_LIMIT:
            break

    return total_bars, earliest_ms


# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOL RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_symbols(args):
    """Resolve TKO_MARKETS symbol list from multiple sources."""

    # 1. --symbols CLI arg
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if syms:
            logger.info("Symbols from --symbols arg: %d", len(syms))
            return syms

    # 2. --symbols-file CLI arg
    if args.symbols_file:
        if os.path.exists(args.symbols_file):
            with open(args.symbols_file) as f:
                syms = [line.strip().upper() for line in f if line.strip() and not line.startswith('#')]
            if syms:
                logger.info("Symbols from --symbols-file %s: %d", args.symbols_file, len(syms))
                return syms
        else:
            logger.warning("--symbols-file %s not found", args.symbols_file)

    # 3. TKO_MARKETS env var
    env_markets = os.environ.get("TKO_MARKETS", "")
    if env_markets:
        syms = [s.strip().upper() for s in env_markets.split(",") if s.strip()]
        if syms:
            logger.info("Symbols from TKO_MARKETS env: %d", len(syms))
            return syms

    # 4. tko_markets.txt file
    for path in [
        os.path.join(os.path.dirname(__file__), "tko_markets.txt"),
        "/scripts/tko_markets.txt",
    ]:
        if os.path.exists(path):
            with open(path) as f:
                syms = [line.strip().upper() for line in f if line.strip() and not line.startswith('#')]
            if syms:
                logger.info("Symbols from %s: %d", path, len(syms))
                return syms

    return []


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="1s OHLCV Backfill for TKO Markets")
    parser.add_argument("--days", type=int, default=14,
                        help="Days of 1s history to attempt (default 14)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbol list (e.g. BTCUSDT,ETHUSDT)")
    parser.add_argument("--symbols-file", type=str, default=None,
                        help="Path to file with one symbol per line")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without fetching")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    t_start = time.time()
    batch_id = f"1s_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # ── Resolve symbols ──
    symbols = resolve_symbols(args)
    if not symbols:
        logger.error("No symbols specified. Use --symbols, --symbols-file, "
                     "TKO_MARKETS env var, or create scripts/tko_markets.txt")
        sys.exit(1)

    # ── Estimates ──
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 24 * 60 * 60 * 1000
    bars_per_symbol = args.days * 86_400
    requests_per_symbol = (bars_per_symbol + KLINES_LIMIT - 1) // KLINES_LIMIT
    total_requests = len(symbols) * requests_per_symbol
    total_bars = len(symbols) * bars_per_symbol
    est_minutes = total_requests * KLINES_WEIGHT / MAX_WEIGHT_PER_MIN

    logger.info("=" * 70)
    logger.info("1-SECOND OHLCV BACKFILL — TKO MARKETS")
    logger.info("=" * 70)
    logger.info("Symbols: %d  [%s%s]",
                len(symbols),
                ", ".join(symbols[:5]),
                f", ... +{len(symbols)-5} more" if len(symbols) > 5 else "")
    logger.info("Days: %d  (attempting — Binance may have less)", args.days)
    logger.info("Est. bars/symbol: %s", f"{bars_per_symbol:,}")
    logger.info("Est. requests/symbol: %s", f"{requests_per_symbol:,}")
    logger.info("Est. total: %s requests, %s bars",
                f"{total_requests:,}", f"{total_bars:,}")
    logger.info("Est. time: %.0f minutes (%.1f hours)", est_minutes, est_minutes / 60)
    logger.info("Est. DB size: ~%.1f GB (at ~400 bytes/row)",
                total_bars * 400 / 1e9)

    if args.dry_run:
        logger.info("\n--dry-run: exiting without fetching")
        return

    # ── Check existing data for resume ──
    logger.info("\nChecking existing 1s data for resume...")
    existing = get_existing_ranges(db_url, symbols)
    skipped = 0
    tasks = []
    for sym in symbols:
        if sym in existing:
            sym_start = existing[sym] + MS_PER_BAR
            if sym_start >= now_ms - MS_PER_BAR:
                skipped += 1
                continue
        else:
            sym_start = start_ms
        tasks.append((sym, sym_start, now_ms))

    if existing:
        logger.info("  %d symbols have existing data (will resume), %d up-to-date (skipped)",
                    len(existing), skipped)
    logger.info("  %d symbols to fetch", len(tasks))

    if not tasks:
        logger.info("All symbols already up to date!")
        return

    # ── Ensure DB index ──
    ensure_unique_index(db_url)

    # ── Start DB writer thread ──
    write_queue = queue.Queue(maxsize=500)
    writer_stats = {"bars_written": 0}
    writer_thread = threading.Thread(
        target=db_writer_loop, args=(db_url, write_queue, writer_stats),
        daemon=True, name="db-writer-1s",
    )
    writer_thread.start()

    # ── Fetch ──
    logger.info("\nFetching 1s klines with %d workers...\n", N_WORKERS)
    rate_limiter = AdaptiveRateLimiter(MAX_WEIGHT_PER_MIN)

    completed = 0
    total_fetched = 0
    sym_results = {}

    with ThreadPoolExecutor(max_workers=N_WORKERS, thread_name_prefix="fetch-1s") as executor:
        futures = {}
        for sym, sym_start, sym_end in tasks:
            future = executor.submit(
                fetch_symbol_1s,
                sym, sym_start, sym_end,
                rate_limiter, write_queue, batch_id,
            )
            futures[future] = sym

        for future in as_completed(futures):
            sym = futures[future]
            try:
                n_bars, earliest_ms = future.result()
                sym_results[sym] = n_bars
                total_fetched += n_bars
            except Exception as e:
                logger.error("  %s failed: %s", sym, e)
                sym_results[sym] = 0

            completed += 1

            # Progress every symbol (1s tasks are individually large)
            elapsed = time.time() - t_start
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (len(tasks) - completed) / rate if rate > 0 else 0
            rl = rate_limiter.stats
            logger.info("  %s: %s bars | %d/%d symbols (%.2f/min), "
                        "DB: %s written, ETA: %.0fm | req: %d, wait: %.0fs",
                        sym, f"{sym_results[sym]:,}",
                        completed, len(tasks), rate * 60,
                        f"{writer_stats['bars_written']:,}",
                        eta / 60,
                        rl["total_requests"], rl["total_wait_seconds"])

    # ── Shutdown DB writer ──
    write_queue.put(None)
    writer_thread.join(timeout=120)

    # ── Summary ──
    elapsed_total = time.time() - t_start
    rl = rate_limiter.stats

    logger.info("\n" + "=" * 70)
    logger.info("1S BACKFILL COMPLETE")
    logger.info("=" * 70)
    logger.info("Elapsed: %.1f minutes (%.1f hours)", elapsed_total / 60, elapsed_total / 3600)
    logger.info("Symbols: %d fetched, %d skipped (up-to-date)", len(tasks), skipped)
    logger.info("API requests: %d (%.0fs waiting on rate limits)",
                rl["total_requests"], rl["total_wait_seconds"])
    logger.info("Bars fetched: %s", f"{total_fetched:,}")
    logger.info("Bars written to DB: %s", f"{writer_stats['bars_written']:,}")

    # Per-symbol breakdown
    if sym_results:
        sorted_syms = sorted(sym_results.items(), key=lambda x: -x[1])
        logger.info("\nPer-symbol (top 20):")
        for sym, n in sorted_syms[:20]:
            days_actual = n / 86400 if n > 0 else 0
            logger.info("  %-12s  %10s bars  (%.1f days)", sym, f"{n:,}", days_actual)
        if len(sorted_syms) > 20:
            logger.info("  ... +%d more symbols", len(sorted_syms) - 20)

        zero_syms = [s for s, n in sorted_syms if n == 0]
        if zero_syms:
            logger.info("\nSymbols with 0 bars (Binance may not support 1s): %s",
                        ", ".join(zero_syms[:10]))

    logger.info("=" * 70)


if __name__ == "__main__":
    main()
