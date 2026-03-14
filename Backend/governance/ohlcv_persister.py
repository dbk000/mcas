"""
OHLCV Persister — Redis WS candles → Postgres.

Separate Docker service. Consumes candle_closed stream events for ALL
timeframes, reads the closed candle from Redis ring buffers, and batch-
inserts into Postgres ohlcv table with source='ws'.

Key guarantees:
- Exact bar_start_ts match only — no fallback to "latest candle"
- ACK only after successful DB flush — crash-safe, no lost candles
- source='ws' — no conflict with REST source='binance' rows
- ON CONFLICT DO NOTHING — closed candles are immutable
- Daily retention pruning for 1m (90d) and 5m/15m (180d)
"""

import sys
import time
import logging
import threading
from datetime import datetime, timezone, date

import redis
import psycopg

from governance.persistence import get_conn as _pool_get_conn

from governance.cache_contract import (
    get_candles, b2s, get_timeframes, is_cache_ready, TF_SECONDS,
    get_known_universe,
)
from governance.ohlcv_backfill import backfill_startup_recovery, backfill_recent_gaps

logger = logging.getLogger("ohlcv_persister")

# ---------------------------------------------------------------------------
# Config — module-level singletons set once by _init_config()
# ---------------------------------------------------------------------------

REDIS_URL = None
DATABASE_URL = None
LOG_LEVEL = None

# Constants (not from env/registry)
GROUP_NAME = "ohlcv_persist_group"
CONSUMER_NAME = "ohlcv-persist-0"
XREAD_BLOCK_MS = 5000
XREAD_COUNT = 100

FLUSH_SIZE = 500      # Flush when buffer reaches this size
FLUSH_AGE_S = 5.0     # Flush when oldest buffered item exceeds this age

RETENTION_1M_DAYS = 90
RETENTION_5M15M_DAYS = 180


def _init_config():
    """Read all config from the frozen ConfigRegistry. Called once from entry point."""
    global REDIS_URL, DATABASE_URL, LOG_LEVEL

    from governance.bootstrap import get_config
    worker = get_config().Worker

    REDIS_URL = worker.REDIS_URL
    DATABASE_URL = worker.DATABASE_URL
    LOG_LEVEL = worker.LOG_LEVEL


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.getLevelName(LOG_LEVEL.upper()))


# ---------------------------------------------------------------------------
# OHLCVPersister
# ---------------------------------------------------------------------------

class OHLCVPersister:
    """Consumes candle_closed events, persists WS candles to Postgres."""

    def __init__(self):
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self._buffer = []          # List of (symbol, tf, bar_start_ts_dt, o, h, l, c, v, now, True, 'ws')
        self._pending_acks = []    # List of (stream_name, msg_id)
        self._buffer_start = 0.0   # time.time() when first item was buffered
        self._last_prune_date = None
        self._last_heal_time = 0.0  # time.time() of last hourly healing
        self._counters = {
            "persisted": 0,
            "candle_miss": 0,
            "backfill_skip": 0,
            "flush_count": 0,
            "flush_error": 0,
        }
        self._last_stats_log = 0.0

    def run(self):
        """Main loop: wait for cache ready, then consume streams."""
        logger.info("OHLCV Persister starting")

        # Wait for ingestor to finish seeding
        while not is_cache_ready(self.r):
            logger.info("Waiting for cache:ready...")
            time.sleep(5)

        logger.info("Cache ready, starting stream consumption")

        # Launch startup recovery in a daemon thread (advisory-locked)
        self._launch_startup_recovery()

        timeframes = get_timeframes(self.r)
        if not timeframes:
            timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]
            logger.warning("No timeframes in Redis, using defaults: %s", timeframes)

        # Create consumer group on each stream (idempotent)
        for tf in timeframes:
            stream = f"stream:candle_closed:{tf}"
            try:
                self.r.xgroup_create(stream, GROUP_NAME, id="$", mkstream=True)
                logger.info("Created consumer group %s on %s", GROUP_NAME, stream)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    raise
                # Group already exists — safe to continue

        # Build stream list for XREADGROUP
        streams = {f"stream:candle_closed:{tf}": ">" for tf in timeframes}

        while True:
            try:
                results = self.r.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams=streams,
                    count=XREAD_COUNT,
                    block=XREAD_BLOCK_MS,
                )

                if results:
                    for stream_name, messages in results:
                        tf = b2s(stream_name).split(":")[-1]
                        for msg_id, fields in messages:
                            self._process_event(tf, fields, msg_id, b2s(stream_name))

                # Check if buffer needs flushing (age-based)
                if self._buffer and (time.time() - self._buffer_start >= FLUSH_AGE_S):
                    self._flush_batch()

                # Daily retention prune
                self._daily_retention()

                # Hourly gap healing (advisory-locked, daemon thread)
                self._maybe_heal_gaps()

                # Periodic stats
                now = time.time()
                if now - self._last_stats_log > 300:  # Every 5 min
                    logger.info(
                        "stats persisted=%d candle_miss=%d backfill_skip=%d flushes=%d flush_errors=%d buf=%d",
                        self._counters["persisted"],
                        self._counters["candle_miss"],
                        self._counters["backfill_skip"],
                        self._counters["flush_count"],
                        self._counters["flush_error"],
                        len(self._buffer),
                    )
                    self._last_stats_log = now

            except redis.exceptions.ConnectionError as e:
                logger.error("Redis connection error: %s", e)
                time.sleep(5)
            except Exception as e:
                logger.error("Unexpected error: %s", e, exc_info=True)
                time.sleep(1)

    def _process_event(self, tf: str, fields: dict, msg_id, stream_name: str):
        """Process a single candle_closed event."""
        symbol = b2s(fields.get("symbol", ""))
        bar_start_ts_str = b2s(fields.get("bar_start_ts", ""))

        if not symbol or not bar_start_ts_str:
            # Malformed event — ACK immediately to avoid infinite redelivery
            self.r.xack(stream_name, GROUP_NAME, msg_id)
            return

        try:
            bar_start_ts_epoch = int(bar_start_ts_str)
        except (ValueError, TypeError):
            self.r.xack(stream_name, GROUP_NAME, msg_id)
            return

        # Skip historical backfill events (WS reconnect publishes old candle_closed
        # events that can't match the ring buffer). Allow up to 10× timeframe age.
        now_ms = int(time.time() * 1000)
        max_age_ms = TF_SECONDS.get(tf, 60) * 1000 * 10
        if now_ms - bar_start_ts_epoch > max_age_ms:
            self.r.xack(stream_name, GROUP_NAME, msg_id)
            self._counters["backfill_skip"] += 1
            return

        # Read recent candles from Redis ring buffer
        candles = get_candles(self.r, symbol, tf, limit=10)

        # Find exact match by bar_start_ts
        matched = None
        for c in candles:
            if int(c["bar_start_ts"]) == bar_start_ts_epoch:
                matched = c
                break

        if matched is None:
            # GUARDRAIL: no fallback — skip if exact match not found
            logger.error(
                "candle_not_found symbol=%s tf=%s bar_start_ts=%s ring_size=%d",
                symbol, tf, bar_start_ts_str, len(candles),
            )
            self._counters["candle_miss"] += 1
            # ACK to avoid infinite redelivery of unfindable candle
            self.r.xack(stream_name, GROUP_NAME, msg_id)
            return

        # Convert bar_start_ts (epoch milliseconds — Binance convention) to datetime
        bar_start_dt = datetime.fromtimestamp(bar_start_ts_epoch / 1000, tz=timezone.utc)
        now_dt = datetime.now(tz=timezone.utc)

        row = (
            symbol,
            tf,
            bar_start_dt,
            float(matched["open"]),
            float(matched["high"]),
            float(matched["low"]),
            float(matched["close"]),
            float(matched["volume"]),
            now_dt,
            True,   # is_final
            "ws",   # source
        )

        self._buffer.append(row)
        self._pending_acks.append((stream_name, msg_id))

        if not self._buffer_start:
            self._buffer_start = time.time()

        # Flush if buffer full
        if len(self._buffer) >= FLUSH_SIZE:
            self._flush_batch()

    def _flush_batch(self):
        """Batch insert buffered candles to Postgres, then ACK."""
        if not self._buffer:
            return

        batch_size = len(self._buffer)
        try:
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO ohlcv (
                            symbol, timeframe, bar_start_ts,
                            open, high, low, close, volume,
                            as_of_ts, is_final, source
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, timeframe, bar_start_ts, source)
                        DO NOTHING
                    """, self._buffer)
                conn.commit()

            # Success — ACK all pending messages
            for stream_name, msg_id in self._pending_acks:
                self.r.xack(stream_name, GROUP_NAME, msg_id)

            self._counters["persisted"] += batch_size
            self._counters["flush_count"] += 1
            logger.debug("Flushed %d candles to Postgres", batch_size)

        except Exception as e:
            logger.error("DB flush failed (%d rows): %s", batch_size, e)
            self._counters["flush_error"] += 1
            # Do NOT ACK — messages will be redelivered on next read

        # Clear buffer regardless (on error, messages redeliver from stream)
        self._buffer.clear()
        self._pending_acks.clear()
        self._buffer_start = 0.0

    def _get_backfill_symbols_and_timeframes(self):
        """Get current known universe symbols and active timeframes."""
        symbols = list(get_known_universe(self.r))
        timeframes = get_timeframes(self.r)
        if not timeframes:
            timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]
        return symbols, timeframes

    def _launch_startup_recovery(self):
        """Launch startup recovery in a daemon thread."""
        def _run():
            try:
                symbols, timeframes = self._get_backfill_symbols_and_timeframes()
                if not symbols:
                    logger.warning("Startup recovery: no symbols in known universe, skipping")
                    return
                logger.info(
                    "Startup recovery: checking %d symbols × %d timeframes",
                    len(symbols), len(timeframes),
                )
                backfill_startup_recovery(DATABASE_URL, symbols, timeframes)
            except Exception as e:
                logger.error("Startup recovery failed: %s", e, exc_info=True)

        t = threading.Thread(target=_run, name="backfill-startup", daemon=True)
        t.start()

    def _maybe_heal_gaps(self):
        """Launch hourly gap healing in a daemon thread if due."""
        now = time.time()
        if now - self._last_heal_time < 3600:
            return
        self._last_heal_time = now

        def _run():
            try:
                symbols, timeframes = self._get_backfill_symbols_and_timeframes()
                if not symbols:
                    return
                backfill_recent_gaps(DATABASE_URL, symbols, timeframes)
            except Exception as e:
                logger.error("Hourly healing failed: %s", e, exc_info=True)

        t = threading.Thread(target=_run, name="backfill-heal", daemon=True)
        t.start()

    def _daily_retention(self):
        """Prune old WS candles once per calendar day."""
        today = date.today()
        if self._last_prune_date == today:
            return
        self._last_prune_date = today

        try:
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    # 1m candles: keep 90 days
                    cur.execute("""
                        DELETE FROM ohlcv
                        WHERE source = 'ws' AND timeframe = '1m'
                          AND bar_start_ts < NOW() - %s * INTERVAL '1 day'
                    """, (RETENTION_1M_DAYS,))
                    deleted_1m = cur.rowcount

                    # 5m/15m candles: keep 180 days
                    cur.execute("""
                        DELETE FROM ohlcv
                        WHERE source = 'ws' AND timeframe IN ('5m', '15m')
                          AND bar_start_ts < NOW() - %s * INTERVAL '1 day'
                    """, (RETENTION_5M15M_DAYS,))
                    deleted_5m15m = cur.rowcount

                conn.commit()

            logger.info(
                "Retention prune: deleted %d 1m rows, %d 5m/15m rows",
                deleted_1m, deleted_5m15m,
            )
        except Exception as e:
            logger.error("Retention prune failed: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from governance.bootstrap import bootstrap_genesis
    bootstrap_genesis()
    _init_config()
    setup_logging()
    persister = OHLCVPersister()
    persister.run()
