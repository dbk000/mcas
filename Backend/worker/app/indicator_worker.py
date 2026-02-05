"""
Indicator Worker — Redis candles → Redis indicators.

Separate Docker service (invariant #2). Consumes candle_closed stream
events, computes indicators via FeatureFactory, writes results to Redis
with ind_meta as commit marker (invariant #16).

Does NOT write price:{symbol} (invariant #8 — ingestor owns prices).
"""

import os
import sys
import time
import logging

import redis

from cache_contract import (
    get_candles_as_ohlcv, store_indicators,
    xadd_indicators_ready, ind_meta_key, candle_meta_key,
    b2s, b2f, symbol_partition, get_universe, get_timeframes,
    is_cache_ready, TF_SECONDS, RECONCILE_INTERVAL_S,
)
from indicators import FeatureFactory, OHLCV

logger = logging.getLogger("indicator_worker")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
WORKER_INDEX = int(os.getenv("WORKER_INDEX", "0"))
N_WORKERS = int(os.getenv("N_WORKERS", "1"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
CONSUMER_NAME = f"ind-{WORKER_INDEX}"
XREAD_BLOCK_MS = 5000  # 5s blocking read
XREAD_COUNT = 50        # Max events per read


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
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))


# ---------------------------------------------------------------------------
# HTF map for bias indicators
# ---------------------------------------------------------------------------

HTF_MAP = {"1m": "5m", "5m": "15m", "15m": "1h", "1h": "4h", "4h": "1d"}


# ---------------------------------------------------------------------------
# IndicatorWorker
# ---------------------------------------------------------------------------

class IndicatorWorker:
    """Consumes candle_closed events, computes indicators, publishes indicators_ready."""

    def __init__(self):
        # Invariant #24: decode_responses=True mandatory
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.ff = FeatureFactory()
        self.last_reconcile = 0.0
        # Per-worker consumer group: each worker instance gets ALL messages
        # and filters by partition locally. Fixes the shared-group bug where
        # ACK-ing another worker's message silently drops it.
        self.group_name = f"indicator_group_{WORKER_INDEX}"

    def run(self):
        """Main loop: wait for cache ready, then consume streams."""
        logger.info(f"Indicator worker {WORKER_INDEX}/{N_WORKERS} starting")

        # Wait for ingestor to finish seeding
        while not is_cache_ready(self.r):
            logger.info("Waiting for cache:ready...")
            time.sleep(5)

        logger.info("Cache ready, starting stream consumption")

        timeframes = get_timeframes(self.r)
        if not timeframes:
            timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]
            logger.warning(f"No timeframes in Redis, using defaults: {timeframes}")

        # Create per-worker consumer group on each stream (idempotent)
        for tf in timeframes:
            stream = f"stream:candle_closed:{tf}"
            try:
                self.r.xgroup_create(stream, self.group_name, id="0", mkstream=True)
                logger.info(f"Created consumer group {self.group_name} on {stream}")
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    raise
                # Group already exists — safe to continue

        # Build stream list for XREADGROUP
        streams = {f"stream:candle_closed:{tf}": ">" for tf in timeframes}

        while True:
            try:
                # Blocking read from all candle_closed streams
                results = self.r.xreadgroup(
                    groupname=self.group_name,
                    consumername=CONSUMER_NAME,
                    streams=streams,
                    count=XREAD_COUNT,
                    block=XREAD_BLOCK_MS,
                )

                if results:
                    for stream_name, messages in results:
                        # Extract tf from stream name
                        # stream_name = "stream:candle_closed:1h"
                        tf = b2s(stream_name).split(":")[-1]
                        for msg_id, fields in messages:
                            self._process_event(tf, fields, msg_id, stream_name)

                # Periodic reconciliation
                now = time.time()
                if now - self.last_reconcile > RECONCILE_INTERVAL_S:
                    self._reconcile()
                    self.last_reconcile = now

            except redis.exceptions.ConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                time.sleep(1)

    def _process_event(self, tf: str, fields: dict, msg_id, stream_name):
        """Process a single candle_closed event."""
        symbol = b2s(fields.get("symbol", ""))
        bar_start_ts = b2s(fields.get("bar_start_ts", ""))

        if not symbol or not bar_start_ts:
            self.r.xack(stream_name, self.group_name, msg_id)
            return

        # Only process symbols in our partition (invariant #17)
        if symbol_partition(symbol, N_WORKERS) != WORKER_INDEX:
            self.r.xack(stream_name, self.group_name, msg_id)
            return

        try:
            self._compute_and_store(symbol, tf, bar_start_ts)
        except Exception as e:
            logger.error(f"Failed to compute indicators for {symbol}:{tf}: {e}")

        # ACK the message
        self.r.xack(stream_name, self.group_name, msg_id)

    def _compute_and_store(self, symbol: str, tf: str, bar_start_ts: str):
        """Compute indicators and store in Redis."""
        # Read candles via cache_contract (invariant #13)
        candles = get_candles_as_ohlcv(self.r, symbol, tf)
        if not candles or len(candles) < 201:
            logger.debug(f"Insufficient candles for {symbol}:{tf} "
                         f"({len(candles) if candles else 0})")
            return

        # Get HTF candles if available
        htf = HTF_MAP.get(tf)
        htf_candles = None
        if htf:
            htf_candles = get_candles_as_ohlcv(self.r, symbol, htf)
            if htf_candles and len(htf_candles) < 50:
                htf_candles = None

        # Compute indicators (reuses exact same FeatureFactory.compute())
        snapshot = self.ff.compute(candles, htf_candles)

        # Store with commit marker (invariant #16, #21)
        store_indicators(self.r, symbol, tf, snapshot, bar_start_ts)

        # Publish indicators_ready
        xadd_indicators_ready(self.r, tf, symbol, bar_start_ts)

        logger.debug(f"Indicators computed for {symbol}:{tf} bar={bar_start_ts}")

    def _reconcile(self):
        """Periodic reconciliation: check for missed updates (invariant #17).

        Each worker only reconciles symbols it owns (sharded by crc32).
        Invariant #30: all smembers reads through b2s().
        """
        try:
            all_symbols = get_universe(self.r)
            timeframes = get_timeframes(self.r)

            my_symbols = [
                s for s in all_symbols
                if symbol_partition(s, N_WORKERS) == WORKER_INDEX
            ]

            recomputed = 0
            for symbol in my_symbols:
                for tf in timeframes:
                    candle_ts = b2f(self.r.hget(candle_meta_key(symbol, tf), "last_bar_ts"))
                    ind_ts = b2f(self.r.hget(ind_meta_key(symbol, tf), "bar_start_ts"))
                    if candle_ts > 0 and candle_ts > ind_ts:
                        try:
                            self._compute_and_store(symbol, tf, str(int(candle_ts)))
                            recomputed += 1
                        except Exception as e:
                            logger.debug(f"Reconcile failed for {symbol}:{tf}: {e}")

            if recomputed > 0:
                logger.info(f"Reconciliation: recomputed {recomputed} indicator sets")

        except Exception as e:
            logger.error(f"Reconciliation error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    setup_logging()
    worker = IndicatorWorker()
    worker.run()
