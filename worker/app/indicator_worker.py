"""
Indicator Worker - Redis Candles to Redis Indicators
=====================================================

Separate Docker service (invariant #2).
Consumes candle_closed events, computes indicators, emits indicators_ready.

Design Invariants enforced:
- #2:  Separate Docker service
- #16: ind_meta is the commit marker (written LAST)
- #17: Reconciliation is sharded
- #20: Stable partition via zlib.crc32
- #21: Transactional pipelines (MULTI/EXEC)
- #24: Decode safety via b2s()/b2f()
- #30: All Redis access through helpers
"""

import os
import sys
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import redis

from cache_contract import (
    # Constants
    TIMEFRAMES,
    RECONCILE_INTERVAL_S,
    TF_SECONDS,
    HTF_MAP,
    # Key helpers
    candle_meta_key,
    ind_meta_key,
    stream_candle_closed_key,
    # Operations
    get_candles,
    store_indicators,
    xadd_indicators_ready,
    get_universe,
    get_timeframes,
    is_cache_ready,
    b2s,
    b2f,
    b2i,
    symbol_partition,
)
from indicators import FeatureFactory, OHLCV

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("indicator_worker")


# =============================================================================
# Configuration
# =============================================================================

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
WORKER_INDEX = int(os.environ.get("WORKER_INDEX", "0"))
N_WORKERS = int(os.environ.get("N_WORKERS", "1"))
CONSUMER_NAME = f"indicator_worker_{WORKER_INDEX}"
MIN_CANDLES = 201  # Minimum candles needed for indicator computation


# =============================================================================
# Indicator Worker
# =============================================================================

class IndicatorWorker:
    """
    Indicator computation worker.

    Consumes candle_closed events from streams, computes indicators,
    stores in Redis, and emits indicators_ready events.
    """

    def __init__(self):
        self.r = redis.from_url(REDIS_URL, decode_responses=True)
        self.factory = FeatureFactory()
        self._running = True
        self._reconcile_thread = None

    def run(self):
        """Main entry point."""
        logger.info("=" * 60)
        logger.info(f"MCAS Indicator Worker {WORKER_INDEX}/{N_WORKERS} Starting")
        logger.info("=" * 60)

        # Wait for cache to be ready
        logger.info("Waiting for cache to be ready...")
        while not is_cache_ready(self.r):
            time.sleep(5)
        logger.info("Cache is ready, starting processing")

        # Start reconciliation thread
        self._reconcile_thread = threading.Thread(
            target=self._reconciliation_loop,
            daemon=True,
        )
        self._reconcile_thread.start()

        # Main event loop
        self._event_loop()

    def _event_loop(self):
        """
        Main event loop: consume candle_closed events.

        Uses XREADGROUP with consumer groups for durable delivery.
        """
        timeframes = get_timeframes(self.r) or TIMEFRAMES

        # Build stream list for XREADGROUP
        streams = {stream_candle_closed_key(tf): ">" for tf in timeframes}

        logger.info(f"Listening on streams: {list(streams.keys())}")

        while self._running:
            try:
                # Block for up to 5 seconds waiting for events
                results = self.r.xreadgroup(
                    groupname="indicator_group",
                    consumername=CONSUMER_NAME,
                    streams=streams,
                    count=100,
                    block=5000,
                )

                if not results:
                    continue

                for stream_name, messages in results:
                    tf = stream_name.split(":")[-1]  # Extract TF from stream:candle_closed:{tf}

                    for message_id, data in messages:
                        try:
                            symbol = b2s(data.get("symbol", ""))
                            bar_start_ts = b2i(data.get("bar_start_ts", 0))

                            if not symbol:
                                continue

                            # Check if this symbol belongs to our partition (invariant #17, #20)
                            if symbol_partition(symbol, N_WORKERS) != WORKER_INDEX:
                                # ACK but skip - belongs to another worker
                                self.r.xack(stream_name, "indicator_group", message_id)
                                continue

                            # Process the event
                            success = self._process_event(symbol, tf, bar_start_ts)

                            # ACK the message
                            self.r.xack(stream_name, "indicator_group", message_id)

                            if success:
                                logger.debug(f"Processed {symbol}:{tf} @ {bar_start_ts}")

                        except Exception as e:
                            logger.error(f"Error processing message {message_id}: {e}")
                            # Still ACK to avoid reprocessing bad messages
                            self.r.xack(stream_name, "indicator_group", message_id)

            except redis.ConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Event loop error: {e}")
                time.sleep(1)

    def _process_event(self, symbol: str, tf: str, bar_start_ts: int) -> bool:
        """
        Process a candle_closed event.

        1. Get candles from Redis (via cache_contract - only read path)
        2. Skip if insufficient candles
        3. Get HTF candles if available
        4. Compute indicators via FeatureFactory
        5. Store indicators with atomic pipeline (invariant #21)
        6. Emit indicators_ready event

        Returns True if successful, False otherwise.
        """
        try:
            # Step 1: Get candles (invariant #13 - only read path)
            candles = get_candles(self.r, symbol, tf)

            # Step 2: Check minimum candles
            if len(candles) < MIN_CANDLES:
                logger.debug(f"Skipping {symbol}:{tf} - only {len(candles)} candles")
                return False

            # Step 3: Get HTF candles if available
            htf_candles = None
            htf_tf = HTF_MAP.get(tf)
            if htf_tf:
                htf_candles = get_candles(self.r, symbol, htf_tf)
                if len(htf_candles) < 50:
                    htf_candles = None

            # Step 4: Compute indicators
            snapshot = self.factory.compute(candles, htf_candles)

            # Step 5: Store indicators (invariant #16, #21 - atomic with commit marker)
            store_indicators(self.r, symbol, tf, snapshot, bar_start_ts)

            # Step 6: Emit indicators_ready event
            xadd_indicators_ready(self.r, tf, symbol, bar_start_ts)

            return True

        except Exception as e:
            logger.error(f"Process error for {symbol}:{tf}: {e}")
            return False

    def _reconciliation_loop(self):
        """
        Periodic reconciliation for missed updates.

        INVARIANT #17: Each worker only reconciles symbols it owns (hash partition).
        INVARIANT #30: All Redis access through helpers (b2s/b2f).
        """
        while self._running:
            try:
                time.sleep(RECONCILE_INTERVAL_S)

                if not is_cache_ready(self.r):
                    continue

                self._reconcile()

            except Exception as e:
                logger.error(f"Reconciliation error: {e}")

    def _reconcile(self):
        """
        Check for symbols needing indicator updates.

        Compares candle_meta timestamps against ind_meta timestamps.
        """
        # Get symbols owned by this worker (invariant #17, #20, #30)
        all_symbols = get_universe(self.r)
        my_symbols = [
            s for s in all_symbols
            if symbol_partition(s, N_WORKERS) == WORKER_INDEX
        ]

        timeframes = get_timeframes(self.r) or TIMEFRAMES
        updates_needed = 0

        for symbol in my_symbols:
            for tf in timeframes:
                try:
                    # Get candle timestamp
                    candle_meta = self.r.hgetall(candle_meta_key(symbol, tf))
                    candle_ts = b2f(candle_meta.get("last_bar_ts", 0)) if candle_meta else 0

                    # Get indicator timestamp
                    ind_meta = self.r.hgetall(ind_meta_key(symbol, tf))
                    ind_ts = b2f(ind_meta.get("bar_start_ts", 0)) if ind_meta else 0

                    # If candle is newer than indicators, recompute
                    if candle_ts > ind_ts:
                        success = self._process_event(symbol, tf, int(candle_ts))
                        if success:
                            updates_needed += 1

                except Exception as e:
                    logger.error(f"Reconcile error for {symbol}:{tf}: {e}")

        if updates_needed > 0:
            logger.info(f"Reconciliation updated {updates_needed} indicators")

    def stop(self):
        """Stop the worker."""
        self._running = False


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    worker = IndicatorWorker()
    try:
        worker.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        worker.stop()
