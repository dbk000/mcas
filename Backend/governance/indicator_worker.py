"""
EXTRACTED PURE LOGIC FROM: WRK_APP_indicator_worker.py
V5 Updates: Full Quarantine enforced.
have been physically replaced to satisfy the zero-contamination mandate.
Dataclass imports are explicitly injected to guarantee semantics.
"""

from dataclasses import dataclass, field, asdict
from governance.bootstrap import bootstrap_genesis
bootstrap_genesis()  # Must populate registry before module-level reads below
from governance.bootstrap import get_config
from governance.infrastructure import SystemClient

import time

import logging

import threading

from concurrent.futures import ThreadPoolExecutor

import redis

from governance.cache_contract import (
    get_candles_as_ohlcv, store_indicators,
    xadd_indicators_ready, ind_meta_key, candle_meta_key,
    b2s, b2f, symbol_partition, get_universe, get_timeframes,
    is_cache_ready, TF_SECONDS, RECONCILE_INTERVAL_S,
)

from indicators.indicators import FeatureFactory, OHLCV

logger = logging.getLogger("indicator_worker")

_worker_config = get_config().Worker

REDIS_URL = _worker_config.REDIS_URL

WORKER_INDEX = _worker_config.WORKER_INDEX

N_WORKERS = _worker_config.N_WORKERS

LOG_LEVEL = _worker_config.LOG_LEVEL

CONSUMER_NAME = f"ind-{WORKER_INDEX}"

COMPUTE_WORKERS = 4     # ThreadPool size for parallel indicator compute

XREAD_BLOCK_MS = 5000   # 5s blocking read

XREAD_COUNT = COMPUTE_WORKERS  # Cap to pool size — prevents unbounded future accumulation

def setup_logging():
    handler = logging.StreamHandler(SystemClient.get_stdout())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.getLevelName(LOG_LEVEL.upper()))

HTF_MAP = {"1m": "5m", "5m": "15m", "15m": "1h", "1h": "4h", "4h": "1d"}

class IndicatorWorker:
    """Consumes candle_closed events, computes indicators, publishes indicators_ready."""

    # Thread-local FeatureFactory — one per compute thread, avoids sharing
    _thread_local = threading.local()

    def __init__(self):
        # Invariant #24: decode_responses=True mandatory
        # Connection pool sized >= COMPUTE_WORKERS + main thread + headroom
        self.r = redis.Redis.from_url(
            REDIS_URL, decode_responses=True,
            max_connections=COMPUTE_WORKERS + 4,
        )
        self.last_reconcile = 0.0
        # Per-worker consumer group: each worker instance gets ALL messages
        # and filters by partition locally. Fixes the shared-group bug where
        # ACK-ing another worker's message silently drops it.
        self.group_name = f"indicator_group_{WORKER_INDEX}"
        self._compute_pool = ThreadPoolExecutor(max_workers=COMPUTE_WORKERS)

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
                # count=COMPUTE_WORKERS ensures at most pool-size futures outstanding
                results = self.r.xreadgroup(
                    groupname=self.group_name,
                    consumername=CONSUMER_NAME,
                    streams=streams,
                    count=XREAD_COUNT,
                    block=XREAD_BLOCK_MS,
                )

                if results:
                    # Collect batch of work items
                    batch = []
                    for stream_name, messages in results:
                        tf = b2s(stream_name).split(":")[-1]
                        for msg_id, fields in messages:
                            batch.append((tf, fields, msg_id, stream_name))

                    # Submit batch to thread pool, wait for ALL to complete
                    # before reading next batch (bounded concurrency)
                    futures = [
                        self._compute_pool.submit(
                            self._process_event_safe, tf, fields, msg_id, stream_name
                        )
                        for tf, fields, msg_id, stream_name in batch
                    ]
                    for future in futures:
                        try:
                            future.result()
                        except Exception as e:
                            # Message NOT acked — will be redelivered
                            logger.error(f"Event processing failed: {e}")

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

    def _get_factory(self):
        """Get thread-local FeatureFactory instance."""
        try:
            self._thread_local.ff
        except AttributeError:  # EXEMPT: thread-local lazy init — first access on each thread
            self._thread_local.ff = FeatureFactory()
        return self._thread_local.ff

    def _process_event_safe(self, tf: str, fields: dict, msg_id, stream_name):
        """Process event in thread pool. ACK only on success."""
        symbol = b2s(fields.get("symbol", ""))
        bar_start_ts = b2s(fields.get("bar_start_ts", ""))

        if not symbol or not bar_start_ts:
            self.r.xack(stream_name, self.group_name, msg_id)
            return

        # Only process symbols in our partition (invariant #17)
        if symbol_partition(symbol, N_WORKERS) != WORKER_INDEX:
            self.r.xack(stream_name, self.group_name, msg_id)
            return

        # 1. Compute + store + publish
        # 2. ACK only on success — on failure, message will be redelivered
        # NOTE: on redelivery, indicators_ready may fire again for the same
        # (symbol, tf, bar_start_ts). Downstream signal_worker consumers
        # MUST dedupe on (symbol, tf, bar_start_ts). See signal_worker.py.
        self._compute_and_store(symbol, tf, bar_start_ts)
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

        # Compute indicators — thread-local FeatureFactory (one per compute thread)
        snapshot = self._get_factory().compute(candles, htf_candles)

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

