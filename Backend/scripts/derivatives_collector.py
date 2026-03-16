#!/usr/bin/env python3
"""
Derivatives Data Collector — Binance Futures Pipeline
======================================================

Collects derivatives positioning data (funding rates, open interest,
long/short ratios, taker buy/sell volumes) from Binance Futures API
and materializes hourly feature tables for the ignition model.

Architecture (mirrors backfill_ohlcv_multi.py):
  1. ThreadPoolExecutor(20) for parallel HTTP fetches
  2. Separate DB writer thread with batched inserts
  3. Adaptive rate limiting from X-MBX-USED-WEIGHT-1M header
  4. Resume from existing data (queries max timestamp per table per symbol)
  5. Batched INSERT ... ON CONFLICT DO NOTHING
  6. Feature materialization after each collection cycle

Modes:
  --backfill   : Fetch 30 days of history (or full for funding rates),
                 materialize features, then enter continuous mode
  (default)    : Continuous collection every COLLECTION_INTERVAL_S seconds

Usage:
    tmux new -s derivatives
    docker compose run --rm -T worker python3 /scripts/derivatives_collector.py --backfill
    # Or continuous (as a service):
    docker compose up -d derivatives_collector
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

import requests as req_lib
import psycopg
import numpy as np

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

FAPI_BASE = "https://fapi.binance.com"

# Rate limits: Binance Futures = 2400 weight/min for market data
MAX_WEIGHT_PER_MIN = 2000       # stay under 2400 with buffer
N_WORKERS = 20                  # concurrent HTTP threads
DB_BATCH_SIZE = 5_000           # rows per DB insert batch
MAX_RETRIES = 5                 # per-request retry limit

# Collection intervals (overridable via env)
# GOVERNANCE_DEBT: migrate to TypedConfigRegistry
COLLECTION_INTERVAL_S = int(os.environ.get("COLLECTION_INTERVAL_S", "3600"))
# GOVERNANCE_DEBT: migrate to TypedConfigRegistry
FUNDING_INTERVAL_S = int(os.environ.get("FUNDING_INTERVAL_S", "14400"))

# Backfill depth
BACKFILL_DAYS = 30              # OI, L/S, taker (API limit ~30d)
FUNDING_BACKFILL_DAYS = 365     # funding rates have full history

# Feature materialization lookback
FEATURE_LOOKBACK_HOURS = 800    # ~33 days, enough for 30d z-score window

# API limits per request
OI_HIST_LIMIT = 500
FUNDING_LIMIT = 1000
LS_LIMIT = 500
TAKER_LIMIT = 500

# Endpoint weights
OI_WEIGHT = 5
FUNDING_WEIGHT = 1
LS_WEIGHT = 1
TAKER_WEIGHT = 1


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER (same pattern as backfill_ohlcv_multi.py)
# ═══════════════════════════════════════════════════════════════════════════════

class AdaptiveRateLimiter:
    """Thread-safe rate limiter that adapts from Binance response headers."""

    def __init__(self, max_weight=MAX_WEIGHT_PER_MIN):
        self._lock = threading.Lock()
        self._max_weight = max_weight
        self._tracked_weight = 0
        self._server_weight = 0
        self._window_start = time.monotonic()
        self._total_requests = 0
        self._total_wait_seconds = 0.0

    def acquire(self, weight=1):
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
            wait = 0.3
            self._total_wait_seconds += wait
            time.sleep(wait)

    def update_from_response(self, response):
        """Update server-side weight from response headers."""
        header = response.headers.get('X-MBX-USED-WEIGHT-1M')
        if header:
            with self._lock:
                try:
                    self._server_weight = int(header)
                except ValueError:
                    pass

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

def load_symbols(db_url):
    """Load overlapping symbols from futures_symbol_mapping.json or re-discover."""
    # Try loading from mapping file
    for candidate in [
        os.path.join(os.path.dirname(__file__), 'futures_symbol_mapping.json'),
        '/scripts/futures_symbol_mapping.json',
    ]:
        if os.path.exists(candidate):
            with open(candidate) as f:
                mapping = json.load(f)
            symbols = mapping.get('overlap_symbols', [])
            if symbols:
                logger.info("Loaded %d symbols from %s", len(symbols), candidate)
                return symbols

    # Fallback: re-discover from API + DB
    logger.info("No mapping file found, discovering symbols from API + DB...")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv
                WHERE timeframe = '1h'
                ORDER BY symbol
            """)
            spot_symbols = {row[0] for row in cur}

    resp = req_lib.get(f"{FAPI_BASE}/fapi/v1/exchangeInfo", timeout=10)
    resp.raise_for_status()
    futures_symbols = set()
    for s in resp.json()["symbols"]:
        if (s["contractType"] == "PERPETUAL" and
                s["quoteAsset"] == "USDT" and
                s["status"] == "TRADING"):
            futures_symbols.add(s["symbol"])

    overlap = sorted(spot_symbols & futures_symbols)
    logger.info("Discovered %d overlapping symbols (spot=%d, futures=%d)",
                len(overlap), len(spot_symbols), len(futures_symbols))
    return overlap


def get_max_timestamps(db_url, table, ts_column, symbols, extra_where=""):
    """Get max timestamp per symbol from a table for resume support.

    Returns: dict[symbol] -> datetime (UTC) or None
    """
    result = {}
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            query = f"""
                SELECT symbol, MAX({ts_column})
                FROM {table}
                WHERE symbol = ANY(%s) {extra_where}
                GROUP BY symbol
            """
            cur.execute(query, (symbols,))
            for row in cur:
                result[row[0]] = row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1]
    return result


def db_writer_loop(db_url, write_queue, stats):
    """Dedicated DB writer thread. Reads from queue, writes in batches.

    Queue items are (table_name, rows) tuples. None sentinel = shutdown.
    """
    conn = psycopg.connect(db_url)
    batches = defaultdict(list)  # table -> rows
    total_written = 0

    try:
        while True:
            try:
                item = write_queue.get(timeout=2)
            except queue.Empty:
                # Flush partial batches on timeout
                for table_name, rows in list(batches.items()):
                    if rows:
                        _flush_batch(conn, table_name, rows)
                        total_written += len(rows)
                        stats['rows_written'] = total_written
                batches.clear()
                continue

            if item is None:
                # Sentinel: flush and exit
                for table_name, rows in list(batches.items()):
                    if rows:
                        _flush_batch(conn, table_name, rows)
                        total_written += len(rows)
                        stats['rows_written'] = total_written
                break

            table_name, rows = item
            batches[table_name].extend(rows)
            if len(batches[table_name]) >= DB_BATCH_SIZE:
                _flush_batch(conn, table_name, batches[table_name])
                total_written += len(batches[table_name])
                stats['rows_written'] = total_written
                batches[table_name] = []
    finally:
        conn.close()

    logger.info("DB writer: %d total rows written", total_written)


# Per-table INSERT statements
_INSERT_SQL = {
    'funding_rates': """
        INSERT INTO funding_rates (symbol, funding_rate, funding_time, mark_price, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'open_interest': """
        INSERT INTO open_interest (symbol, timestamp, sum_open_interest, sum_open_interest_value, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'long_short_ratio': """
        INSERT INTO long_short_ratio (symbol, timestamp, long_short_ratio, long_account, short_account, ratio_type, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'taker_buy_sell': """
        INSERT INTO taker_buy_sell (symbol, timestamp, buy_vol, sell_vol, buy_sell_ratio, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
}


def _flush_batch(conn, table_name, rows):
    """Insert a batch of rows into the specified table."""
    if not rows:
        return
    sql = _INSERT_SQL[table_name]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

_thread_local = threading.local()


def _get_session():
    if not hasattr(_thread_local, 'session'):  # EXEMPT: thread-local session init
        s = req_lib.Session()
        adapter = req_lib.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
            max_retries=req_lib.adapters.Retry(total=2, backoff_factor=0.5)
        )
        s.mount('https://', adapter)
        _thread_local.session = s
    return _thread_local.session


def _api_get(url, params, rate_limiter, weight=1):
    """Make a rate-limited GET request with retries. Returns parsed JSON or None."""
    session = _get_session()
    rate_limiter.acquire(weight)

    retries = 0
    while retries < MAX_RETRIES:
        try:
            resp = session.get(url, params=params, timeout=30)
            rate_limiter.update_from_response(resp)

            if resp.status_code in (418, 429):
                retry_after = int(resp.headers.get("Retry-After", 30))
                logger.warning("Rate limited (%d), sleeping %ds...",
                               resp.status_code, retry_after)
                time.sleep(retry_after)
                retries += 1
                continue

            resp.raise_for_status()
            return resp.json()

        except (req_lib.exceptions.RequestException, ValueError) as e:
            retries += 1
            if retries >= MAX_RETRIES:
                return None
            time.sleep(1.0 * retries)

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# COLLECTION FUNCTIONS (one per endpoint type)
# ═══════════════════════════════════════════════════════════════════════════════

def collect_funding_rates(symbol, start_ms, end_ms, rate_limiter, write_queue, batch_id):
    """Fetch funding rate history for one symbol. Paginates with limit=1000."""
    cursor_ms = start_ms
    total = 0

    while cursor_ms < end_ms:
        data = _api_get(
            f"{FAPI_BASE}/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": cursor_ms, "endTime": end_ms, "limit": FUNDING_LIMIT},
            rate_limiter, weight=FUNDING_WEIGHT,
        )
        if not data:
            break

        rows = []
        for item in data:
            ts = datetime.fromtimestamp(int(item["fundingTime"]) / 1000, tz=timezone.utc)
            rows.append((
                symbol,
                float(item["fundingRate"]),
                ts,
                float(item.get("markPrice", 0)) if item.get("markPrice") else None,
                'binance_futures',
                batch_id,
            ))

        if rows:
            write_queue.put(('funding_rates', rows))
            total += len(rows)

        # Advance cursor
        last_ts_ms = int(data[-1]["fundingTime"])
        if last_ts_ms <= cursor_ms:
            break
        cursor_ms = last_ts_ms + 1

        if len(data) < FUNDING_LIMIT:
            break

    return total


def collect_oi_hist(symbol, start_ms, end_ms, rate_limiter, write_queue, batch_id):
    """Fetch open interest statistics (1h) for one symbol."""
    cursor_ms = start_ms
    total = 0

    while cursor_ms < end_ms:
        data = _api_get(
            f"{FAPI_BASE}/futures/data/openInterestHist",
            {"symbol": symbol, "period": "1h", "startTime": cursor_ms,
             "endTime": end_ms, "limit": OI_HIST_LIMIT},
            rate_limiter, weight=OI_WEIGHT,
        )
        if not data:
            break

        rows = []
        for item in data:
            ts = datetime.fromtimestamp(int(item["timestamp"]) / 1000, tz=timezone.utc)
            rows.append((
                symbol,
                ts,
                float(item["sumOpenInterest"]),
                float(item["sumOpenInterestValue"]),
                'binance_futures',
                batch_id,
            ))

        if rows:
            write_queue.put(('open_interest', rows))
            total += len(rows)

        last_ts_ms = int(data[-1]["timestamp"])
        if last_ts_ms <= cursor_ms:
            break
        cursor_ms = last_ts_ms + 1

        if len(data) < OI_HIST_LIMIT:
            break

    return total


def collect_ls_ratio(symbol, endpoint, ratio_type, start_ms, end_ms,
                     rate_limiter, write_queue, batch_id):
    """Fetch long/short ratio for one symbol from a specific endpoint."""
    cursor_ms = start_ms
    total = 0

    while cursor_ms < end_ms:
        data = _api_get(
            f"{FAPI_BASE}/futures/data/{endpoint}",
            {"symbol": symbol, "period": "1h", "startTime": cursor_ms,
             "endTime": end_ms, "limit": LS_LIMIT},
            rate_limiter, weight=LS_WEIGHT,
        )
        if not data:
            break

        rows = []
        for item in data:
            ts = datetime.fromtimestamp(int(item["timestamp"]) / 1000, tz=timezone.utc)
            rows.append((
                symbol,
                ts,
                float(item["longShortRatio"]),
                float(item.get("longAccount", 0)),
                float(item.get("shortAccount", 0)),
                ratio_type,
                'binance_futures',
                batch_id,
            ))

        if rows:
            write_queue.put(('long_short_ratio', rows))
            total += len(rows)

        last_ts_ms = int(data[-1]["timestamp"])
        if last_ts_ms <= cursor_ms:
            break
        cursor_ms = last_ts_ms + 1

        if len(data) < LS_LIMIT:
            break

    return total


def collect_taker_volume(symbol, start_ms, end_ms, rate_limiter, write_queue, batch_id):
    """Fetch taker buy/sell volume for one symbol."""
    cursor_ms = start_ms
    total = 0

    while cursor_ms < end_ms:
        data = _api_get(
            f"{FAPI_BASE}/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": "1h", "startTime": cursor_ms,
             "endTime": end_ms, "limit": TAKER_LIMIT},
            rate_limiter, weight=TAKER_WEIGHT,
        )
        if not data:
            break

        rows = []
        for item in data:
            ts = datetime.fromtimestamp(int(item["timestamp"]) / 1000, tz=timezone.utc)
            rows.append((
                symbol,
                ts,
                float(item["buyVol"]),
                float(item["sellVol"]),
                float(item.get("buySellRatio", 0)),
                'binance_futures',
                batch_id,
            ))

        if rows:
            write_queue.put(('taker_buy_sell', rows))
            total += len(rows)

        last_ts_ms = int(data[-1]["timestamp"])
        if last_ts_ms <= cursor_ms:
            break
        cursor_ms = last_ts_ms + 1

        if len(data) < TAKER_LIMIT:
            break

    return total


# ═══════════════════════════════════════════════════════════════════════════════
# BACKFILL ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def _collect_symbol_all(symbol, start_ms, end_ms, funding_start_ms,
                        rate_limiter, write_queue, batch_id,
                        existing_funding, existing_oi, existing_ls_global,
                        existing_ls_top_pos, existing_ls_top_acc, existing_taker):
    """Collect all endpoint types for a single symbol. Returns stats dict."""
    stats = defaultdict(int)

    # Per-symbol resume: use existing max timestamp if available
    def _resume_ms(existing_dict, default_ms):
        ts = existing_dict.get(symbol)
        if ts:
            return int(ts.timestamp() * 1000) + 1
        return default_ms

    fr_start = _resume_ms(existing_funding, funding_start_ms)
    oi_start = _resume_ms(existing_oi, start_ms)
    gl_start = _resume_ms(existing_ls_global, start_ms)
    tp_start = _resume_ms(existing_ls_top_pos, start_ms)
    ta_start = _resume_ms(existing_ls_top_acc, start_ms)
    tk_start = _resume_ms(existing_taker, start_ms)

    try:
        stats['funding_rates'] = collect_funding_rates(
            symbol, fr_start, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s funding_rates failed: %s", symbol, e)

    try:
        stats['open_interest'] = collect_oi_hist(
            symbol, oi_start, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s open_interest failed: %s", symbol, e)

    try:
        stats['ls_global'] = collect_ls_ratio(
            symbol, 'globalLongShortAccountRatio', 'global',
            gl_start, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s ls_global failed: %s", symbol, e)

    try:
        stats['ls_top_position'] = collect_ls_ratio(
            symbol, 'topLongShortPositionRatio', 'top_trader_position',
            tp_start, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s ls_top_position failed: %s", symbol, e)

    try:
        stats['ls_top_account'] = collect_ls_ratio(
            symbol, 'topLongShortAccountRatio', 'top_trader_account',
            ta_start, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s ls_top_account failed: %s", symbol, e)

    try:
        stats['taker_buy_sell'] = collect_taker_volume(
            symbol, tk_start, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s taker_buy_sell failed: %s", symbol, e)

    return dict(stats)


def run_backfill(db_url, symbols, rate_limiter, write_queue, batch_id):
    """Backfill all symbols across all endpoints using ThreadPoolExecutor."""
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=BACKFILL_DAYS)).timestamp() * 1000)
    funding_start_ms = int((now - timedelta(days=FUNDING_BACKFILL_DAYS)).timestamp() * 1000)

    logger.info("Backfill: %d symbols, OI/LS/taker from %dd ago, funding from %dd ago",
                len(symbols), BACKFILL_DAYS, FUNDING_BACKFILL_DAYS)

    # Get existing max timestamps for resume
    logger.info("Checking existing data for resume...")
    existing_funding = get_max_timestamps(db_url, 'funding_rates', 'funding_time', symbols)
    existing_oi = get_max_timestamps(db_url, 'open_interest', 'timestamp', symbols)
    existing_ls_global = get_max_timestamps(
        db_url, 'long_short_ratio', 'timestamp', symbols,
        "AND ratio_type = 'global'")
    existing_ls_top_pos = get_max_timestamps(
        db_url, 'long_short_ratio', 'timestamp', symbols,
        "AND ratio_type = 'top_trader_position'")
    existing_ls_top_acc = get_max_timestamps(
        db_url, 'long_short_ratio', 'timestamp', symbols,
        "AND ratio_type = 'top_trader_account'")
    existing_taker = get_max_timestamps(db_url, 'taker_buy_sell', 'timestamp', symbols)

    logger.info("  Resume points: funding=%d, oi=%d, ls_g=%d, ls_tp=%d, ls_ta=%d, taker=%d",
                len(existing_funding), len(existing_oi), len(existing_ls_global),
                len(existing_ls_top_pos), len(existing_ls_top_acc), len(existing_taker))

    t_start = time.time()
    completed = 0
    total_stats = defaultdict(int)
    failed_symbols = 0

    with ThreadPoolExecutor(max_workers=N_WORKERS, thread_name_prefix="fetcher") as executor:
        futures = {}
        for symbol in symbols:
            future = executor.submit(
                _collect_symbol_all,
                symbol, start_ms, now_ms, funding_start_ms,
                rate_limiter, write_queue, batch_id,
                existing_funding, existing_oi, existing_ls_global,
                existing_ls_top_pos, existing_ls_top_acc, existing_taker,
            )
            futures[future] = symbol

        for future in as_completed(futures):
            symbol = futures[future]
            completed += 1
            try:
                stats = future.result()
                sym_total = sum(stats.values())
                if sym_total == 0:
                    failed_symbols += 1
                for k, v in stats.items():
                    total_stats[k] += v
            except Exception as e:
                logger.error("  %s collection failed: %s", symbol, e)
                failed_symbols += 1

            # Progress every 25 symbols or at end
            if completed % 25 == 0 or completed == len(symbols):
                elapsed = time.time() - t_start
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (len(symbols) - completed) / rate if rate > 0 else 0
                rl_stats = rate_limiter.stats
                logger.info("  Backfill: %d/%d symbols (%.1f/s), ETA: %.0fm | "
                            "Weight: %d/%d, wait: %.0fs | "
                            "rows: fr=%d oi=%d ls=%d taker=%d",
                            completed, len(symbols), rate, eta / 60,
                            rl_stats["current_weight"], MAX_WEIGHT_PER_MIN,
                            rl_stats["total_wait_seconds"],
                            total_stats.get('funding_rates', 0),
                            total_stats.get('open_interest', 0),
                            total_stats.get('ls_global', 0) +
                            total_stats.get('ls_top_position', 0) +
                            total_stats.get('ls_top_account', 0),
                            total_stats.get('taker_buy_sell', 0))

    elapsed = time.time() - t_start
    failure_pct = 100 * failed_symbols / len(symbols) if symbols else 0

    logger.info("Backfill complete: %.1f min, %d/%d symbols succeeded (%.1f%% failed)",
                elapsed / 60, len(symbols) - failed_symbols, len(symbols), failure_pct)
    for k, v in sorted(total_stats.items()):
        logger.info("  %s: %d rows", k, v)

    if failure_pct > 50:
        logger.critical("BACKFILL: >50%% of symbols failed (%d/%d). Data may be incomplete.",
                        failed_symbols, len(symbols))

    return dict(total_stats)


# ═══════════════════════════════════════════════════════════════════════════════
# CONTINUOUS COLLECTION (single cycle)
# ═══════════════════════════════════════════════════════════════════════════════

def run_collection_cycle(db_url, symbols, rate_limiter, write_queue, batch_id,
                         include_funding=False):
    """Run one collection cycle: fetch latest data for all symbols.

    For continuous mode, we fetch the last few hours to ensure no gaps.
    """
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    # Fetch last 4 hours for overlap/gap safety
    start_ms = int((now - timedelta(hours=4)).timestamp() * 1000)
    funding_start_ms = int((now - timedelta(hours=24)).timestamp() * 1000)

    logger.info("Collection cycle: %d symbols, funding=%s", len(symbols), include_funding)
    t_start = time.time()
    completed = 0
    total_stats = defaultdict(int)
    failed_symbols = 0

    # No resume needed for continuous — short window, ON CONFLICT handles dupes
    empty = {}

    with ThreadPoolExecutor(max_workers=N_WORKERS, thread_name_prefix="fetcher") as executor:
        futures = {}
        for symbol in symbols:
            if include_funding:
                future = executor.submit(
                    _collect_symbol_all,
                    symbol, start_ms, now_ms, funding_start_ms,
                    rate_limiter, write_queue, batch_id,
                    empty, empty, empty, empty, empty, empty,
                )
            else:
                # Skip funding on non-funding cycles
                future = executor.submit(
                    _collect_symbol_nonfunding,
                    symbol, start_ms, now_ms,
                    rate_limiter, write_queue, batch_id,
                )
            futures[future] = symbol

        for future in as_completed(futures):
            symbol = futures[future]
            completed += 1
            try:
                stats = future.result()
                sym_total = sum(stats.values())
                if sym_total == 0:
                    failed_symbols += 1
                for k, v in stats.items():
                    total_stats[k] += v
            except Exception as e:
                logger.error("  %s collection failed: %s", symbol, e)
                failed_symbols += 1

            if completed % 50 == 0 or completed == len(symbols):
                elapsed = time.time() - t_start
                logger.info("  Cycle: %d/%d symbols (%.1fs elapsed)",
                            completed, len(symbols), elapsed)

    elapsed = time.time() - t_start
    failure_pct = 100 * failed_symbols / len(symbols) if symbols else 0

    logger.info("Cycle complete: %.1fs, %d rows total, %.1f%% symbols failed",
                elapsed, sum(total_stats.values()), failure_pct)

    if failure_pct > 50:
        logger.critical("CYCLE: >50%% of symbols failed (%d/%d).",
                        failed_symbols, len(symbols))

    return dict(total_stats)


def _collect_symbol_nonfunding(symbol, start_ms, end_ms,
                               rate_limiter, write_queue, batch_id):
    """Collect OI, L/S ratios, and taker volume for one symbol (no funding)."""
    stats = defaultdict(int)

    try:
        stats['open_interest'] = collect_oi_hist(
            symbol, start_ms, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s open_interest failed: %s", symbol, e)

    try:
        stats['ls_global'] = collect_ls_ratio(
            symbol, 'globalLongShortAccountRatio', 'global',
            start_ms, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s ls_global failed: %s", symbol, e)

    try:
        stats['ls_top_position'] = collect_ls_ratio(
            symbol, 'topLongShortPositionRatio', 'top_trader_position',
            start_ms, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s ls_top_position failed: %s", symbol, e)

    try:
        stats['ls_top_account'] = collect_ls_ratio(
            symbol, 'topLongShortAccountRatio', 'top_trader_account',
            start_ms, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s ls_top_account failed: %s", symbol, e)

    try:
        stats['taker_buy_sell'] = collect_taker_volume(
            symbol, start_ms, end_ms, rate_limiter, write_queue, batch_id)
    except Exception as e:
        logger.warning("  %s taker_buy_sell failed: %s", symbol, e)

    return dict(stats)


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE MATERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def materialize_features(db_url, symbols, lookback_hours=FEATURE_LOOKBACK_HOURS):
    """Compute derived features from raw tables and UPSERT into derivatives_features_1h.

    Uses numpy for vectorized computation over all symbols in one pass.
    """
    logger.info("Materializing features for %d symbols, lookback=%dh...",
                len(symbols), lookback_hours)
    t0 = time.time()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    # ── Load raw data ─────────────────────────────────────────────────────────

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Funding rates
            cur.execute("""
                SELECT symbol, funding_time, funding_rate, mark_price
                FROM funding_rates
                WHERE symbol = ANY(%s) AND funding_time >= %s
                ORDER BY symbol, funding_time
            """, (symbols, cutoff))
            funding_rows = cur.fetchall()

            # Open interest
            cur.execute("""
                SELECT symbol, timestamp, sum_open_interest, sum_open_interest_value
                FROM open_interest
                WHERE symbol = ANY(%s) AND timestamp >= %s
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            oi_rows = cur.fetchall()

            # Long/short ratio (global only for features)
            cur.execute("""
                SELECT symbol, timestamp, long_short_ratio
                FROM long_short_ratio
                WHERE symbol = ANY(%s) AND timestamp >= %s AND ratio_type = 'global'
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            ls_rows = cur.fetchall()

            # Taker buy/sell
            cur.execute("""
                SELECT symbol, timestamp, buy_vol, sell_vol
                FROM taker_buy_sell
                WHERE symbol = ANY(%s) AND timestamp >= %s
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            taker_rows = cur.fetchall()

    logger.info("  Loaded raw data: funding=%d, oi=%d, ls=%d, taker=%d rows (%.1fs)",
                len(funding_rows), len(oi_rows), len(ls_rows), len(taker_rows),
                time.time() - t0)

    if not oi_rows and not ls_rows and not taker_rows:
        logger.warning("  No raw data to materialize features from.")
        return 0

    # ── Build hourly grid ─────────────────────────────────────────────────────

    # Collect all hourly timestamps from OI/LS/taker (these are already 1h-aligned)
    all_ts = set()
    for row in oi_rows:
        ts = row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1]
        # Floor to hour
        all_ts.add(ts.replace(minute=0, second=0, microsecond=0))
    for row in ls_rows:
        ts = row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1]
        all_ts.add(ts.replace(minute=0, second=0, microsecond=0))
    for row in taker_rows:
        ts = row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1]
        all_ts.add(ts.replace(minute=0, second=0, microsecond=0))

    if not all_ts:
        logger.warning("  No hourly timestamps found in raw data.")
        return 0

    ts_grid = sorted(all_ts)
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)

    sym_to_idx = {sym: i for i, sym in enumerate(symbols)}
    n_sym = len(symbols)

    logger.info("  Grid: %d bars × %d symbols", n_bars, n_sym)

    # ── Build per-symbol data matrices ────────────────────────────────────────

    # Funding rate: forward-fill to hourly grid
    funding_mat = np.full((n_bars, n_sym), np.nan, dtype=np.float64)
    for row in funding_rows:
        sym = row[0]
        if sym not in sym_to_idx:
            continue
        ts = row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1]
        ts_h = ts.replace(minute=0, second=0, microsecond=0)
        if ts_h in ts_to_idx:
            funding_mat[ts_to_idx[ts_h], sym_to_idx[sym]] = float(row[2])

    # Forward-fill funding rates column-by-column
    for s in range(n_sym):
        col = funding_mat[:, s]
        mask = ~np.isnan(col)
        if mask.any():
            idx = np.where(mask, np.arange(n_bars), 0)
            np.maximum.accumulate(idx, out=idx)
            funding_mat[:, s] = col[idx]

    # OI value matrix
    oi_mat = np.full((n_bars, n_sym), np.nan, dtype=np.float64)
    for row in oi_rows:
        sym = row[0]
        if sym not in sym_to_idx:
            continue
        ts = row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1]
        ts_h = ts.replace(minute=0, second=0, microsecond=0)
        if ts_h in ts_to_idx:
            oi_mat[ts_to_idx[ts_h], sym_to_idx[sym]] = float(row[3])  # sum_open_interest_value

    # L/S ratio matrix
    ls_mat = np.full((n_bars, n_sym), np.nan, dtype=np.float64)
    for row in ls_rows:
        sym = row[0]
        if sym not in sym_to_idx:
            continue
        ts = row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1]
        ts_h = ts.replace(minute=0, second=0, microsecond=0)
        if ts_h in ts_to_idx:
            ls_mat[ts_to_idx[ts_h], sym_to_idx[sym]] = float(row[2])

    # Taker buy/sell matrices
    taker_buy_mat = np.full((n_bars, n_sym), np.nan, dtype=np.float64)
    taker_sell_mat = np.full((n_bars, n_sym), np.nan, dtype=np.float64)
    for row in taker_rows:
        sym = row[0]
        if sym not in sym_to_idx:
            continue
        ts = row[1].replace(tzinfo=timezone.utc) if row[1].tzinfo is None else row[1]
        ts_h = ts.replace(minute=0, second=0, microsecond=0)
        if ts_h in ts_to_idx:
            taker_buy_mat[ts_to_idx[ts_h], sym_to_idx[sym]] = float(row[2])
            taker_sell_mat[ts_to_idx[ts_h], sym_to_idx[sym]] = float(row[3])

    # ── Compute derived features (vectorized) ─────────────────────────────────

    # Funding rate features
    # funding_rate: already in funding_mat (forward-filled)
    # funding_rate_ma8: 8-period moving average
    funding_rate_ma8 = _rolling_mean(funding_mat, 8)
    # funding_rate_acceleration: f[t] - f[t-3]
    funding_rate_accel = np.full_like(funding_mat, np.nan)
    funding_rate_accel[3:] = funding_mat[3:] - funding_mat[:-3]
    # funding_rate_zscore: (f - rolling_30d_mean) / rolling_30d_std
    fr_mean_30d = _rolling_mean(funding_mat, 720)  # 30 days × 24 hours
    fr_std_30d = _rolling_std(funding_mat, 720)
    with np.errstate(divide='ignore', invalid='ignore'):
        funding_zscore = np.where(fr_std_30d > 0,
                                  (funding_mat - fr_mean_30d) / fr_std_30d,
                                  np.nan)

    # OI features
    # oi_change_pct_Nh: (oi[t] - oi[t-N]) / oi[t-N]
    with np.errstate(divide='ignore', invalid='ignore'):
        oi_change_1h = np.full_like(oi_mat, np.nan)
        oi_change_1h[1:] = np.where(oi_mat[:-1] > 0,
                                     (oi_mat[1:] - oi_mat[:-1]) / oi_mat[:-1],
                                     np.nan)
        oi_change_4h = np.full_like(oi_mat, np.nan)
        oi_change_4h[4:] = np.where(oi_mat[:-4] > 0,
                                     (oi_mat[4:] - oi_mat[:-4]) / oi_mat[:-4],
                                     np.nan)
        oi_change_24h = np.full_like(oi_mat, np.nan)
        oi_change_24h[24:] = np.where(oi_mat[:-24] > 0,
                                       (oi_mat[24:] - oi_mat[:-24]) / oi_mat[:-24],
                                       np.nan)

    # L/S features
    # ls_ratio_change_4h: ls[t] - ls[t-4]
    ls_change_4h = np.full_like(ls_mat, np.nan)
    ls_change_4h[4:] = ls_mat[4:] - ls_mat[:-4]

    # ls_ratio_extreme: percentile rank within 720-bar window
    ls_extreme = _rolling_percentile_rank(ls_mat, 720)

    # Taker features
    taker_total = taker_buy_mat + taker_sell_mat
    with np.errstate(divide='ignore', invalid='ignore'):
        taker_buy_ratio = np.where(taker_total > 0,
                                   taker_buy_mat / taker_total,
                                   np.nan)
        taker_imbalance = np.where(taker_total > 0,
                                   (taker_buy_mat - taker_sell_mat) / taker_total,
                                   np.nan)
    taker_imbalance_ma = _rolling_mean(taker_imbalance, 4)

    # Data completeness: count non-null features / total features
    TOTAL_FEATURES = 14  # count of non-metadata feature columns
    feature_arrays = [
        funding_mat, funding_rate_ma8, funding_rate_accel, funding_zscore,
        oi_mat, oi_change_1h, oi_change_4h, oi_change_24h,
        ls_mat, ls_change_4h, ls_extreme,
        taker_buy_ratio, taker_imbalance, taker_imbalance_ma,
    ]
    non_null_count = np.zeros((n_bars, n_sym), dtype=np.float64)
    for arr in feature_arrays:
        non_null_count += (~np.isnan(arr)).astype(np.float64)
    data_completeness = non_null_count / TOTAL_FEATURES

    # ── UPSERT into derivatives_features_1h ───────────────────────────────────

    logger.info("  Writing features to derivatives_features_1h...")
    t_write = time.time()
    upsert_count = 0

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            batch = []
            for t_idx in range(n_bars):
                ts = ts_grid[t_idx]
                for s_idx in range(n_sym):
                    # Skip rows where we have zero data
                    if data_completeness[t_idx, s_idx] == 0:
                        continue

                    sym = symbols[s_idx]

                    def _val(arr, t=t_idx, s=s_idx):
                        v = arr[t, s]
                        return None if np.isnan(v) else float(v)

                    batch.append((
                        sym, ts,
                        _val(funding_mat), _val(funding_rate_ma8),
                        _val(funding_rate_accel), _val(funding_zscore),
                        _val(oi_mat), _val(oi_change_1h),
                        _val(oi_change_4h), _val(oi_change_24h),
                        None,  # oi_volume_ratio — requires OHLCV join, NULL for now
                        _val(ls_mat), _val(ls_change_4h), _val(ls_extreme),
                        _val(taker_buy_ratio), _val(taker_imbalance),
                        _val(taker_imbalance_ma),
                        float(data_completeness[t_idx, s_idx]),
                    ))

                    if len(batch) >= DB_BATCH_SIZE:
                        _upsert_features(cur, batch)
                        upsert_count += len(batch)
                        batch = []

            if batch:
                _upsert_features(cur, batch)
                upsert_count += len(batch)

        conn.commit()

    logger.info("  Materialized %d feature rows in %.1fs",
                upsert_count, time.time() - t_write)
    logger.info("Feature materialization complete: %.1fs total", time.time() - t0)
    return upsert_count


def _upsert_features(cur, batch):
    """UPSERT a batch of feature rows."""
    cur.executemany("""
        INSERT INTO derivatives_features_1h (
            symbol, bar_start_ts,
            funding_rate, funding_rate_ma8, funding_rate_acceleration, funding_rate_zscore,
            oi_value, oi_change_pct_1h, oi_change_pct_4h, oi_change_pct_24h, oi_volume_ratio,
            ls_ratio, ls_ratio_change_4h, ls_ratio_extreme,
            taker_buy_ratio, taker_imbalance, taker_imbalance_ma,
            data_completeness, materialized_at
        ) VALUES (
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, NOW()
        )
        ON CONFLICT (symbol, bar_start_ts) DO UPDATE SET
            funding_rate = EXCLUDED.funding_rate,
            funding_rate_ma8 = EXCLUDED.funding_rate_ma8,
            funding_rate_acceleration = EXCLUDED.funding_rate_acceleration,
            funding_rate_zscore = EXCLUDED.funding_rate_zscore,
            oi_value = EXCLUDED.oi_value,
            oi_change_pct_1h = EXCLUDED.oi_change_pct_1h,
            oi_change_pct_4h = EXCLUDED.oi_change_pct_4h,
            oi_change_pct_24h = EXCLUDED.oi_change_pct_24h,
            oi_volume_ratio = EXCLUDED.oi_volume_ratio,
            ls_ratio = EXCLUDED.ls_ratio,
            ls_ratio_change_4h = EXCLUDED.ls_ratio_change_4h,
            ls_ratio_extreme = EXCLUDED.ls_ratio_extreme,
            taker_buy_ratio = EXCLUDED.taker_buy_ratio,
            taker_imbalance = EXCLUDED.taker_imbalance,
            taker_imbalance_ma = EXCLUDED.taker_imbalance_ma,
            data_completeness = EXCLUDED.data_completeness,
            materialized_at = NOW()
    """, batch)


# ── Numpy helpers for feature computation ─────────────────────────────────────

def _rolling_mean(arr, window):
    """Compute rolling mean along axis=0. First (window-1) rows are NaN."""
    out = np.full_like(arr, np.nan)
    if arr.shape[0] < window:
        return out
    cs = np.nancumsum(arr, axis=0)
    count = np.nancumsum(~np.isnan(arr), axis=0).astype(np.float64)
    out[window - 1:] = cs[window - 1:]
    out[window - 1:] -= np.concatenate([np.zeros((1, arr.shape[1])), cs[:-window]], axis=0)
    cnt = count[window - 1:]
    cnt -= np.concatenate([np.zeros((1, arr.shape[1])), count[:-window]], axis=0)
    with np.errstate(divide='ignore', invalid='ignore'):
        out[window - 1:] = np.where(cnt > 0, out[window - 1:] / cnt, np.nan)
    return out


def _rolling_std(arr, window):
    """Compute rolling std along axis=0. Uses Welford-like approach via mean of squares."""
    mean = _rolling_mean(arr, window)
    sq_mean = _rolling_mean(arr ** 2, window)
    with np.errstate(invalid='ignore'):
        var = sq_mean - mean ** 2
        var = np.where(var > 0, var, 0)
        return np.sqrt(var)


def _rolling_percentile_rank(arr, window):
    """Compute rolling percentile rank of current value in window.

    Returns value in [0, 1]. Uses a vectorized approach: for each position,
    count how many values in the window are <= current value.
    """
    n_bars, n_sym = arr.shape
    out = np.full_like(arr, np.nan)

    for t in range(window, n_bars):
        window_data = arr[t - window:t, :]  # (window, n_sym)
        current = arr[t, :]                  # (n_sym,)
        # Count non-NaN values <= current
        valid = ~np.isnan(window_data)
        le_current = (window_data <= current[np.newaxis, :]) & valid
        n_le = np.sum(le_current, axis=0).astype(np.float64)
        n_valid = np.sum(valid, axis=0).astype(np.float64)
        with np.errstate(divide='ignore', invalid='ignore'):
            out[t, :] = np.where(n_valid > 0, n_le / n_valid, np.nan)

    return out


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Derivatives Data Collector")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill historical data before entering continuous mode")
    parser.add_argument("--backfill-only", action="store_true",
                        help="Backfill and exit (don't enter continuous mode)")
    parser.add_argument("--materialize-only", action="store_true",
                        help="Only run feature materialization (no collection)")
    parser.add_argument("--symbols", type=int, default=0,
                        help="Limit to first N symbols (0 = all, for testing)")
    args = parser.parse_args()

    # GOVERNANCE_DEBT: migrate to TypedConfigRegistry
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("DERIVATIVES DATA COLLECTOR")
    logger.info("=" * 70)
    logger.info("Config: collection_interval=%ds, funding_interval=%ds, workers=%d",
                COLLECTION_INTERVAL_S, FUNDING_INTERVAL_S, N_WORKERS)

    # ── Load symbols ──────────────────────────────────────────────────────────
    symbols = load_symbols(db_url)
    if args.symbols > 0:
        symbols = symbols[:args.symbols]
    logger.info("Symbols: %d", len(symbols))

    if not symbols:
        logger.error("No symbols found. Run map_futures_symbols.py first.")
        sys.exit(1)

    # ── Materialize-only mode ─────────────────────────────────────────────────
    if args.materialize_only:
        n = materialize_features(db_url, symbols)
        logger.info("Done. %d feature rows materialized.", n)
        return

    # ── Start DB writer thread ────────────────────────────────────────────────
    write_queue = queue.Queue(maxsize=1000)
    writer_stats = {"rows_written": 0}
    writer_thread = threading.Thread(
        target=db_writer_loop, args=(db_url, write_queue, writer_stats),
        daemon=True, name="db-writer",
    )
    writer_thread.start()
    logger.info("DB writer thread started")

    rate_limiter = AdaptiveRateLimiter(MAX_WEIGHT_PER_MIN)

    # ── Backfill ──────────────────────────────────────────────────────────────
    if args.backfill or args.backfill_only:
        batch_id = f"deriv_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        run_backfill(db_url, symbols, rate_limiter, write_queue, batch_id)

        # Flush writer before materializing
        write_queue.put(None)
        writer_thread.join(timeout=120)
        logger.info("DB writer: %d rows written", writer_stats["rows_written"])

        # Materialize features from backfilled data
        materialize_features(db_url, symbols)

        if args.backfill_only:
            rl = rate_limiter.stats
            logger.info("=" * 70)
            logger.info("BACKFILL COMPLETE")
            logger.info("Total API requests: %d, wait time: %.0fs",
                        rl["total_requests"], rl["total_wait_seconds"])
            logger.info("Total rows written: %d", writer_stats["rows_written"])
            logger.info("=" * 70)
            return

        # Restart writer for continuous mode
        write_queue = queue.Queue(maxsize=1000)
        writer_stats = {"rows_written": 0}
        writer_thread = threading.Thread(
            target=db_writer_loop, args=(db_url, write_queue, writer_stats),
            daemon=True, name="db-writer",
        )
        writer_thread.start()

    # ── Continuous collection loop ────────────────────────────────────────────
    logger.info("\nEntering continuous collection mode")
    logger.info("  Collection interval: %ds (%s)",
                COLLECTION_INTERVAL_S,
                str(timedelta(seconds=COLLECTION_INTERVAL_S)))
    logger.info("  Funding interval: %ds (%s)",
                FUNDING_INTERVAL_S,
                str(timedelta(seconds=FUNDING_INTERVAL_S)))

    last_funding_time = 0
    cycle_count = 0

    try:
        while True:
            cycle_count += 1
            cycle_start = time.time()
            now = datetime.now(timezone.utc)

            # Determine if this cycle includes funding rates
            include_funding = (cycle_start - last_funding_time) >= FUNDING_INTERVAL_S
            if include_funding:
                last_funding_time = cycle_start

            batch_id = f"deriv_cycle_{now.strftime('%Y%m%d_%H%M%S')}"
            logger.info("\n── Cycle %d at %s (funding=%s) ──",
                        cycle_count, now.strftime('%H:%M:%S'), include_funding)

            # Collect
            run_collection_cycle(db_url, symbols, rate_limiter,
                                 write_queue, batch_id, include_funding)

            # Flush writer before materializing
            # Send a flush signal by draining the queue
            time.sleep(3)  # allow writer to catch up

            # Materialize features
            materialize_features(db_url, symbols, lookback_hours=48)

            # Report
            rl = rate_limiter.stats
            logger.info("  Cycle %d stats: API reqs=%d, wait=%.0fs, DB rows=%d",
                        cycle_count, rl["total_requests"],
                        rl["total_wait_seconds"], writer_stats["rows_written"])

            # Sleep until next cycle
            cycle_elapsed = time.time() - cycle_start
            sleep_time = max(0, COLLECTION_INTERVAL_S - cycle_elapsed)
            if sleep_time > 0:
                logger.info("  Sleeping %.0fs until next cycle...", sleep_time)
                time.sleep(sleep_time)

    except KeyboardInterrupt:  # EXEMPT: graceful SIGINT shutdown
        logger.info("\nShutting down...")
    finally:
        write_queue.put(None)
        writer_thread.join(timeout=30)
        logger.info("DB writer: %d total rows written", writer_stats["rows_written"])
        logger.info("Derivatives collector stopped.")


if __name__ == "__main__":
    main()
