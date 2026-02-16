"""
Ingestor - WebSocket to Redis Candles
======================================

Separate Docker service. Ingestion only.
Uses websocket-client library (callback-driven, not asyncio).

Startup sequence (invariant #15 - seed-then-stream):
1. Build universe (get_symbols)
2. Populate universe:monitored + universe:promoted + cache:timeframes
3. Create consumer groups for all streams (invariant #31)
4. Seed: fetch 600 × 1m bars per symbol, aggregate to HTFs
5. Set cache:ready = "1"
6. THEN start WS shard threads (WS does NOT start until step 5 completes)

Design Invariants enforced:
- #5:  Seed only 1m then aggregate all HTFs
- #6:  Sharded WebSocket connections
- #7:  RPUSH oldest→newest
- #14: websocket-client (callback-driven)
- #15: Seed-then-stream (no interleaving)
- #23: Dedupe gate on candle push
- #25: WebSocketApp recreated on reconnect
- #28: HTF stream events carry HTF bar_start_ts
- #29: is_final in candle_dict
- #32: Aggregator state is symbol-scoped
"""

import os
import sys
import json
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from dataclasses import dataclass

import redis
import requests
import websocket

from cache_contract import (
    # Constants
    RING_BUFFER_SIZE,
    WS_MAX_STREAMS_PER_CONN,
    TIMEFRAMES,
    TF_SECONDS,
    HTF_MAP,
    # Key helpers
    candle_meta_key,
    # Operations
    push_candle,
    update_forming_candle,
    xadd_candle_closed,
    create_consumer_groups,
    set_cache_ready,
    set_universe,
    set_timeframes,
    b2s,
    b2i,
    symbol_partition,
)
from indicators import OHLCV

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ingestor")


# =============================================================================
# Configuration
# =============================================================================

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
SYMBOL_COUNT = int(os.environ.get("SYMBOL_COUNT", "200"))
SEED_WORKERS = int(os.environ.get("SEED_WORKERS", "20"))
TRACKED_TIMEFRAMES = os.environ.get("TRACKED_TIMEFRAMES", "1m,5m,15m,1h,4h,1d").split(",")
USE_DYNAMIC_SYMBOLS = os.environ.get("USE_DYNAMIC_SYMBOLS", "true").lower() == "true"
UNIVERSE_REFRESH_INTERVAL_S = int(os.environ.get("UNIVERSE_REFRESH_INTERVAL_S", "3600"))  # 1 hour


# =============================================================================
# Binance Client (REST for seeding)
# =============================================================================

class BinanceClient:
    """Simple Binance REST client for seeding."""

    BASE_URL = "https://api.binance.com/api/v3"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def get_top_symbols(self, count: int = 200) -> list[str]:
        """Get top symbols by 24h volume."""
        url = f"{self.BASE_URL}/ticker/24hr"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            tickers = resp.json()

            # Filter USDT pairs and sort by volume
            usdt_pairs = [
                t for t in tickers
                if t["symbol"].endswith("USDT")
                and float(t.get("quoteVolume", 0)) > 0
            ]
            usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)

            return [t["symbol"] for t in usdt_pairs[:count]]
        except Exception as e:
            logger.error(f"Failed to get symbols: {e}")
            return []

    def fetch_klines(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 600,
    ) -> list[dict]:
        """
        Fetch OHLCV candles from Binance REST.
        Returns list of candle dicts, oldest first.
        """
        url = f"{self.BASE_URL}/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }

        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            raw = resp.json()

            candles = []
            for k in raw:
                candles.append({
                    "bar_start_ts": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "is_final": True,
                })
            return candles
        except Exception as e:
            logger.error(f"Failed to fetch klines for {symbol}: {e}")
            return []


# =============================================================================
# Candle Aggregator (Invariant #32 - symbol-scoped state)
# =============================================================================

class CandleAggregator:
    """
    Aggregates 1m candles into higher timeframes.

    INVARIANT #32: State is symbol-scoped. Each symbol has independent
    state per timeframe. A shared aggregator across multiple symbols
    MUST NOT blend data from different symbols.
    """

    def __init__(self, timeframes: list[str]):
        # State per symbol per timeframe
        # self.state[symbol][tf] = {"open": ..., "high": ..., etc.}
        self.state: dict[str, dict[str, dict]] = {}
        self.timeframes = [tf for tf in timeframes if tf != "1m"]

    def _get_htf_bar_start(self, ts_ms: int, tf: str) -> int:
        """Get the bar start timestamp for a higher timeframe."""
        interval_ms = TF_SECONDS[tf] * 1000
        return (ts_ms // interval_ms) * interval_ms

    def on_1m_closed(
        self,
        symbol: str,
        candle_1m: dict,
    ) -> list[tuple[str, dict]]:
        """
        Process a closed 1m candle and return any completed HTF candles.

        Args:
            symbol: Trading pair
            candle_1m: Closed 1m candle dict

        Returns:
            List of (tf, completed_candle) tuples for any HTF candles that closed
        """
        if symbol not in self.state:
            self.state[symbol] = {}

        completed = []
        bar_ts = candle_1m["bar_start_ts"]

        for tf in self.timeframes:
            htf_bar_start = self._get_htf_bar_start(bar_ts, tf)

            if tf not in self.state[symbol]:
                # Initialize new HTF bar
                self.state[symbol][tf] = {
                    "bar_start_ts": htf_bar_start,
                    "open": candle_1m["open"],
                    "high": candle_1m["high"],
                    "low": candle_1m["low"],
                    "close": candle_1m["close"],
                    "volume": candle_1m["volume"],
                }
            else:
                current = self.state[symbol][tf]

                # Check if we've moved to a new HTF bar
                if htf_bar_start > current["bar_start_ts"]:
                    # Complete the previous bar
                    completed.append((tf, current.copy()))

                    # Start new bar
                    self.state[symbol][tf] = {
                        "bar_start_ts": htf_bar_start,
                        "open": candle_1m["open"],
                        "high": candle_1m["high"],
                        "low": candle_1m["low"],
                        "close": candle_1m["close"],
                        "volume": candle_1m["volume"],
                    }
                else:
                    # Update current bar
                    current["high"] = max(current["high"], candle_1m["high"])
                    current["low"] = min(current["low"], candle_1m["low"])
                    current["close"] = candle_1m["close"]
                    current["volume"] += candle_1m["volume"]

        return completed


# =============================================================================
# WebSocket Shard (Invariants #6, #14, #25)
# =============================================================================

class ConnectionShard(threading.Thread):
    """
    One WS connection per shard, callback-driven via websocket-client.

    INVARIANT #14: Uses websocket-client (not asyncio)
    INVARIANT #25: WebSocketApp recreated on each reconnect
    """

    def __init__(
        self,
        shard_id: int,
        symbols: list[str],
        redis_client,
        aggregator: CandleAggregator,
    ):
        super().__init__(daemon=True)
        self.shard_id = shard_id
        self.symbols = symbols
        self.r = redis_client
        self.aggregator = aggregator
        self._running = True
        self._retry_count = 0
        self.name = f"shard-{shard_id}"

    def stop(self):
        self._running = False

    def run(self):
        """
        Thread entry: connect and run_forever with on_message callback.

        INVARIANT #25: WebSocketApp is recreated on each reconnect attempt.
        """
        streams = "/".join(f"{s.lower()}@kline_1m" for s in self.symbols)
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        while self._running:
            logger.info(f"[Shard {self.shard_id}] Connecting to {len(self.symbols)} streams...")

            # Fresh WebSocketApp each attempt (invariant #25)
            ws = websocket.WebSocketApp(
                url,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open,
            )

            try:
                ws.run_forever(ping_interval=30)
            except Exception as e:
                logger.error(f"[Shard {self.shard_id}] run_forever error: {e}")

            if self._running:
                backoff = min(2 ** self._retry_count, 60)
                logger.warning(f"[Shard {self.shard_id}] Reconnecting in {backoff}s...")
                time.sleep(backoff)
                self._retry_count += 1

                # On reconnect, backfill missed candles
                self._backfill_missed()

    def _on_open(self, ws):
        logger.info(f"[Shard {self.shard_id}] Connected")
        self._retry_count = 0

    def _on_error(self, ws, error):
        logger.error(f"[Shard {self.shard_id}] Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"[Shard {self.shard_id}] Closed: {close_status_code} {close_msg}")

    def _on_message(self, ws, message):
        """Callback: parse kline, update Redis."""
        try:
            data = json.loads(message)
            kline = data.get("data", {}).get("k", {})
            if not kline:
                return

            symbol = kline["s"]
            is_closed = kline["x"]

            candle = {
                "bar_start_ts": int(kline["t"]),
                "open": float(kline["o"]),
                "high": float(kline["h"]),
                "low": float(kline["l"]),
                "close": float(kline["c"]),
                "volume": float(kline["v"]),
                "is_final": is_closed,  # invariant #29: in dict
            }

            if is_closed:
                # Push closed candle (with dedupe gate - invariant #23)
                pushed = push_candle(self.r, symbol, "1m", candle)

                if pushed:
                    # Legacy price (backward compat, ingestor-owned)
                    now = time.time()
                    self.r.hset(f"price:{symbol}", mapping={
                        "price": str(candle["close"]),
                        "timestamp": str(now),
                    })
                    self.r.expire(f"price:{symbol}", 300)

                    # Emit stream event
                    xadd_candle_closed(self.r, "1m", symbol, candle["bar_start_ts"])

                    # Aggregate to HTFs
                    for tf, completed in self.aggregator.on_1m_closed(symbol, candle):
                        completed["is_final"] = True  # invariant #29
                        push_candle(self.r, symbol, tf, completed)
                        # INVARIANT #28: publish HTF bar_start_ts, not 1m
                        xadd_candle_closed(self.r, tf, symbol, completed["bar_start_ts"])
            else:
                # Update forming candle
                update_forming_candle(self.r, symbol, "1m", candle)
                self.r.hset(f"price:{symbol}", mapping={
                    "price": str(candle["close"]),
                    "timestamp": str(time.time()),
                })

        except Exception as e:
            logger.error(f"[Shard {self.shard_id}] Message error: {e}")

    def _backfill_missed(self):
        """On reconnect, fetch missed 1m bars via REST and re-aggregate HTFs."""
        logger.info(f"[Shard {self.shard_id}] Backfilling missed candles...")
        client = BinanceClient()

        for symbol in self.symbols:
            try:
                # Check last bar timestamp
                meta = self.r.hgetall(candle_meta_key(symbol, "1m"))
                last_ts = b2i(meta.get("last_bar_ts", 0)) if meta else 0

                if last_ts == 0:
                    continue

                # Calculate how many bars we missed
                now_ms = int(time.time() * 1000)
                missed_bars = (now_ms - last_ts) // 60000

                if missed_bars <= 0:
                    continue

                # Fetch missed bars (limit to reasonable amount)
                limit = min(missed_bars + 5, 100)
                candles = client.fetch_klines(symbol, "1m", limit=limit)

                for candle in candles:
                    # Dedupe gate will handle duplicates (invariant #23)
                    pushed = push_candle(self.r, symbol, "1m", candle)
                    if pushed:
                        for tf, completed in self.aggregator.on_1m_closed(symbol, candle):
                            completed["is_final"] = True
                            push_candle(self.r, symbol, tf, completed)

                logger.debug(f"[Shard {self.shard_id}] Backfilled {len(candles)} candles for {symbol}")

            except Exception as e:
                logger.error(f"[Shard {self.shard_id}] Backfill error for {symbol}: {e}")


# =============================================================================
# Ingestor Main
# =============================================================================

class Ingestor:
    """
    Main ingestor service.

    Startup sequence (invariant #15 - seed-then-stream):
    1. Build universe
    2. Populate Redis universe sets
    3. Create consumer groups (invariant #31)
    4. Seed 1m candles + aggregate HTFs
    5. Set cache:ready
    6. Start WS shards

    Dynamic universe:
    - Periodically refreshes symbol list (every UNIVERSE_REFRESH_INTERVAL_S)
    - New symbols are seeded and added to appropriate shard
    - Removed symbols stay in cache until TTL expires
    """

    def __init__(self):
        self.r = redis.from_url(REDIS_URL, decode_responses=True)
        self.client = BinanceClient()
        self.symbols: list[str] = []
        self.shards: list[ConnectionShard] = []
        self.aggregator = CandleAggregator(TRACKED_TIMEFRAMES)
        self._symbol_lock = threading.Lock()
        self._last_universe_refresh = 0

    def run(self):
        """Main entry point."""
        logger.info("=" * 60)
        logger.info("MCAS Ingestor Starting")
        logger.info("=" * 60)

        # Step 1: Build universe
        logger.info(f"Step 1: Fetching top {SYMBOL_COUNT} symbols...")
        self.symbols = self.client.get_top_symbols(SYMBOL_COUNT)
        if not self.symbols:
            logger.error("No symbols found, exiting")
            sys.exit(1)
        logger.info(f"  Got {len(self.symbols)} symbols")

        # Step 2: Populate universe in Redis
        logger.info("Step 2: Setting universe and timeframes in Redis...")
        set_universe(self.r, self.symbols, promoted=self.symbols[:50])
        set_timeframes(self.r, TRACKED_TIMEFRAMES)
        logger.info(f"  Timeframes: {TRACKED_TIMEFRAMES}")

        # Step 3: Create consumer groups (invariant #31)
        logger.info("Step 3: Creating consumer groups...")
        create_consumer_groups(self.r, TRACKED_TIMEFRAMES)
        logger.info("  Consumer groups created")

        # Step 4: Seed (invariant #5 - seed only 1m, aggregate HTFs)
        logger.info(f"Step 4: Seeding {len(self.symbols)} symbols with {RING_BUFFER_SIZE} 1m candles...")
        self._seed()
        logger.info("  Seeding complete")

        # Step 5: Set cache ready
        logger.info("Step 5: Setting cache:ready...")
        set_cache_ready(self.r, True)
        logger.info("  Cache is ready")

        # Step 6: Start WS shards (invariant #15 - only AFTER seeding)
        logger.info("Step 6: Starting WebSocket shards...")
        self._start_shards()
        logger.info(f"  Started {len(self.shards)} shards")

        logger.info("=" * 60)
        logger.info("Ingestor running. Press Ctrl+C to stop.")
        logger.info("=" * 60)

        self._last_universe_refresh = time.time()

        # Keep main thread alive
        try:
            while True:
                time.sleep(60)
                self._log_stats()

                # Periodic universe refresh
                if USE_DYNAMIC_SYMBOLS:
                    elapsed = time.time() - self._last_universe_refresh
                    if elapsed >= UNIVERSE_REFRESH_INTERVAL_S:
                        self._refresh_universe()
                        self._last_universe_refresh = time.time()

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            for shard in self.shards:
                shard.stop()

    def _seed(self):
        """
        Seed Redis with historical candles.

        INVARIANT #5: Seed only 1m, then aggregate all HTFs.
        """
        def seed_symbol(symbol: str):
            try:
                # Fetch 1m candles
                candles = self.client.fetch_klines(symbol, "1m", limit=RING_BUFFER_SIZE)
                if not candles:
                    return

                # Create aggregator for this symbol's seeding
                agg = CandleAggregator(TRACKED_TIMEFRAMES)

                # Push 1m candles and aggregate HTFs
                for candle in candles:
                    push_candle(self.r, symbol, "1m", candle)

                    for tf, completed in agg.on_1m_closed(symbol, candle):
                        completed["is_final"] = True
                        push_candle(self.r, symbol, tf, completed)

                logger.debug(f"Seeded {symbol}: {len(candles)} 1m candles")

            except Exception as e:
                logger.error(f"Seed error for {symbol}: {e}")

        # Parallel seeding
        with ThreadPoolExecutor(max_workers=SEED_WORKERS) as pool:
            pool.map(seed_symbol, self.symbols)

    def _start_shards(self):
        """
        Start WebSocket shards.

        INVARIANT #6: Sharded connections, deterministic partitioning.
        """
        # Calculate number of shards needed
        n_shards = (len(self.symbols) + WS_MAX_STREAMS_PER_CONN - 1) // WS_MAX_STREAMS_PER_CONN
        n_shards = max(1, n_shards)

        # Sort symbols for deterministic sharding
        sorted_symbols = sorted(self.symbols)

        for shard_id in range(n_shards):
            # Get symbols for this shard
            start = shard_id * WS_MAX_STREAMS_PER_CONN
            end = start + WS_MAX_STREAMS_PER_CONN
            shard_symbols = sorted_symbols[start:end]

            if not shard_symbols:
                break

            shard = ConnectionShard(
                shard_id=shard_id,
                symbols=shard_symbols,
                redis_client=self.r,
                aggregator=self.aggregator,
            )
            shard.start()
            self.shards.append(shard)

    def _log_stats(self):
        """Log periodic stats."""
        try:
            # Sample a few symbols
            with self._symbol_lock:
                sample = self.symbols[:3]
            for symbol in sample:
                meta = self.r.hgetall(candle_meta_key(symbol, "1m"))
                if meta:
                    age = time.time() - float(meta.get("as_of_ts", 0))
                    logger.info(
                        f"[Stats] {symbol}: last_close={meta.get('last_close')}, "
                        f"age={age:.1f}s, count={meta.get('count')}"
                    )
        except Exception as e:
            logger.error(f"Stats error: {e}")

    def _refresh_universe(self):
        """
        Refresh the symbol universe dynamically.

        - Fetches new top symbols by volume
        - Seeds any new symbols
        - Updates Redis universe sets
        - Note: WebSocket shards continue with original symbols
          (full restart needed for WS changes)
        """
        logger.info("Refreshing symbol universe...")

        try:
            new_symbols = self.client.get_top_symbols(SYMBOL_COUNT)
            if not new_symbols:
                logger.warning("Failed to refresh universe, keeping existing")
                return

            with self._symbol_lock:
                current_set = set(self.symbols)
                new_set = set(new_symbols)

                added = new_set - current_set
                removed = current_set - new_set

                if added:
                    logger.info(f"Universe: +{len(added)} symbols: {list(added)[:5]}...")
                    # Seed new symbols
                    self._seed_symbols(list(added))

                if removed:
                    logger.info(f"Universe: -{len(removed)} symbols (will age out)")

                # Update tracked symbols
                self.symbols = new_symbols

            # Update Redis
            set_universe(self.r, new_symbols, promoted=new_symbols[:50])

            logger.info(f"Universe refreshed: {len(new_symbols)} symbols")

        except Exception as e:
            logger.error(f"Universe refresh error: {e}")

    def _seed_symbols(self, symbols: list[str]):
        """Seed a subset of symbols (for dynamic adds)."""
        def seed_one(symbol: str):
            try:
                candles = self.client.fetch_klines(symbol, "1m", limit=RING_BUFFER_SIZE)
                if not candles:
                    return
                agg = CandleAggregator(TRACKED_TIMEFRAMES)
                for candle in candles:
                    push_candle(self.r, symbol, "1m", candle)
                    for tf, completed in agg.on_1m_closed(symbol, candle):
                        completed["is_final"] = True
                        push_candle(self.r, symbol, tf, completed)
                logger.debug(f"Seeded new symbol: {symbol}")
            except Exception as e:
                logger.error(f"Seed error for {symbol}: {e}")

        with ThreadPoolExecutor(max_workers=SEED_WORKERS) as pool:
            pool.map(seed_one, symbols)


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    ingestor = Ingestor()
    ingestor.run()
