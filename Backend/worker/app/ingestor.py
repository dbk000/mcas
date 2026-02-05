"""
Ingestor — WebSocket → Redis candle ring buffers.

Separate Docker service. Ingestion only. Uses websocket-client library
(callback-driven, not asyncio — invariant #14).

Startup sequence (invariant #15 — seed-then-stream, no interleaving):
1. Build universe
2. Populate universe:monitored + universe:promoted + cache:timeframes
3. Create consumer groups for ALL timeframe streams (invariant #31)
4. Seed: fetch 600 x 1m bars per symbol, aggregate to HTFs
5. Set cache:ready = "1"
6. THEN start WS shard threads — WS does NOT start until step 5 completes
"""

import os
import sys
import json
import time
import math
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import redis
import requests

from cache_contract import (
    push_candle, update_forming_candle, get_candles,
    xadd_candle_closed, create_consumer_groups,
    candle_meta_key, b2s, b2f,
    WS_MAX_STREAMS_PER_CONN, TF_SECONDS, RING_BUFFER_SIZE,
    symbol_partition,
)

logger = logging.getLogger("ingestor")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SYMBOL_COUNT = int(os.getenv("SYMBOL_COUNT", "200"))
USE_DYNAMIC_SYMBOLS = os.getenv("USE_DYNAMIC_SYMBOLS", "false").lower() == "true"
DEFAULT_TIMEFRAMES_STR = os.getenv("DEFAULT_TIMEFRAMES", "1m,5m,15m,1h,4h,1d")
SEED_WORKERS = int(os.getenv("SEED_WORKERS", "20"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

TIMEFRAMES = [t.strip() for t in DEFAULT_TIMEFRAMES_STR.split(",") if t.strip()]

# Binance REST
BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_TIMEOUT = 10


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
# CandleAggregator (invariant #32 — symbol-scoped state)
# ---------------------------------------------------------------------------

class CandleAggregator:
    """Aggregate 1m candles into higher timeframes.

    Maintains independent state per symbol (invariant #32).
    A single aggregator shared across a WS shard handling multiple
    symbols MUST NOT blend 1m closes from different symbols.

    Invariant #5: seed only 1m, then aggregate all HTFs.
    """

    # Aggregation targets: only timeframes that are multiples of 1m
    AGG_MAP = {
        "5m": 5,
        "15m": 15,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }

    def __init__(self, timeframes: list[str]):
        """
        Args:
            timeframes: list of target timeframes to aggregate into.
                        Only non-1m timeframes present in AGG_MAP are used.
        """
        self.targets = {
            tf: self.AGG_MAP[tf]
            for tf in timeframes
            if tf in self.AGG_MAP
        }
        # state[symbol][tf] = {"open", "high", "low", "close", "volume",
        #                       "bar_start_ts", "count"}
        self.state: dict[str, dict[str, dict]] = defaultdict(dict)

    def on_1m_closed(self, symbol: str, candle_1m: dict) -> list[tuple[str, dict]]:
        """Process a closed 1m candle and return any completed HTF candles.

        Args:
            symbol: e.g. "BTCUSDT"
            candle_1m: dict with bar_start_ts, open, high, low, close, volume

        Returns:
            List of (timeframe, completed_candle_dict) for each HTF that completed.
        """
        completed = []
        bar_ts = int(candle_1m["bar_start_ts"])

        for tf, minutes in self.targets.items():
            tf_seconds = minutes * 60
            # Determine which HTF bar this 1m candle belongs to
            htf_bar_start = (bar_ts // (tf_seconds * 1000)) * (tf_seconds * 1000)

            sym_state = self.state[symbol]
            current = sym_state.get(tf)

            if current is None or current["bar_start_ts"] != htf_bar_start:
                # New HTF bar starting
                if current is not None and current["bar_start_ts"] != htf_bar_start:
                    # Previous bar is complete
                    completed.append((tf, dict(current)))

                sym_state[tf] = {
                    "bar_start_ts": htf_bar_start,
                    "open": float(candle_1m["open"]),
                    "high": float(candle_1m["high"]),
                    "low": float(candle_1m["low"]),
                    "close": float(candle_1m["close"]),
                    "volume": float(candle_1m["volume"]),
                    "count": 1,
                }
            else:
                # Update existing HTF bar
                current["high"] = max(current["high"], float(candle_1m["high"]))
                current["low"] = min(current["low"], float(candle_1m["low"]))
                current["close"] = float(candle_1m["close"])
                current["volume"] += float(candle_1m["volume"])
                current["count"] += 1

        return completed

    def reset_symbol(self, symbol: str):
        """Clear aggregator state for a symbol (e.g., on reconnect)."""
        self.state.pop(symbol, None)


# ---------------------------------------------------------------------------
# ConnectionShard (invariants #6, #14, #25)
# ---------------------------------------------------------------------------

class ConnectionShard(threading.Thread):
    """One WS connection per shard, callback-driven via websocket-client.

    Invariant #25: WebSocketApp is recreated on each reconnect attempt.
    """

    def __init__(self, shard_id: int, symbols: list[str],
                 redis_url: str, timeframes: list[str]):
        super().__init__(daemon=True, name=f"ws-shard-{shard_id}")
        self.shard_id = shard_id
        self.symbols = symbols
        self.redis_url = redis_url
        self.timeframes = timeframes
        self._running = True
        self._retry_count = 0

        # Each shard gets its own Redis connection (decode_responses=True — invariant #24)
        self.r = redis.Redis.from_url(redis_url, decode_responses=True)

        # Each shard gets its own aggregator (invariant #32 — symbol-scoped)
        self.aggregator = CandleAggregator(timeframes)

        self._symbol_set = set(symbols)

    def run(self):
        """Thread entry: connect and run_forever.

        Invariant #25: fresh WebSocketApp each reconnect.
        """
        import websocket

        streams = "/".join(f"{s.lower()}@kline_1m" for s in self.symbols)
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        logger.info(f"Shard {self.shard_id} starting with {len(self.symbols)} symbols")

        while self._running:
            # Fresh WebSocketApp each attempt (invariant #25)
            ws = websocket.WebSocketApp(
                url,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open,
            )
            ws.run_forever(ping_interval=30)

            if not self._running:
                break

            delay = min(2 ** self._retry_count, 60)
            logger.warning(f"Shard {self.shard_id} disconnected, "
                           f"reconnecting in {delay}s (attempt {self._retry_count + 1})")
            time.sleep(delay)
            self._retry_count += 1

            # Backfill missed candles on reconnect
            self._backfill()

    def stop(self):
        self._running = False

    def _on_open(self, ws):
        logger.info(f"Shard {self.shard_id} connected")
        self._retry_count = 0

    def _on_error(self, ws, error):
        logger.warning(f"Shard {self.shard_id} WS error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info(f"Shard {self.shard_id} WS closed: "
                     f"status={close_status_code} msg={close_msg}")

    def _on_message(self, ws, message):
        """Callback: parse kline, update Redis."""
        try:
            data = json.loads(message)
            kline = data.get("data", {}).get("k")
            if kline is None:
                return

            symbol = kline["s"]
            if symbol not in self._symbol_set:
                return

            close_price = float(kline["c"])
            now = time.time()

            candle = {
                "bar_start_ts": int(kline["t"]),
                "open": float(kline["o"]),
                "high": float(kline["h"]),
                "low": float(kline["l"]),
                "close": close_price,
                "volume": float(kline["v"]),
            }

            if kline["x"]:  # is_closed
                candle["is_final"] = True  # Set in dict (invariant #29)
                push_candle(self.r, symbol, "1m", candle)

                # Legacy price (backward compat, ingestor-owned — invariant #8)
                self.r.hset(f"price:{symbol}", mapping={
                    "price": str(close_price),
                    "timestamp": str(now),
                })
                self.r.expire(f"price:{symbol}", 300)

                xadd_candle_closed(self.r, "1m", symbol, candle["bar_start_ts"])

                # Aggregate to HTFs
                for tf, completed in self.aggregator.on_1m_closed(symbol, candle):
                    completed["is_final"] = True  # Set in dict (invariant #29)
                    push_candle(self.r, symbol, tf, completed)
                    # Invariant #28: publish the HTF bar_start_ts
                    xadd_candle_closed(self.r, tf, symbol, completed["bar_start_ts"])
            else:
                update_forming_candle(self.r, symbol, "1m", candle)
                # Legacy price update for forming candles too
                self.r.hset(f"price:{symbol}", mapping={
                    "price": str(close_price),
                    "timestamp": str(now),
                })

        except Exception as e:
            logger.error(f"Shard {self.shard_id} message error: {e}", exc_info=True)

    def _backfill(self):
        """Backfill missed 1m candles on reconnect via REST.

        Dedupe gate in push_candle() (invariant #23) silently skips
        overlapping bars, so we can fetch generously.
        """
        logger.info(f"Shard {self.shard_id} backfilling {len(self.symbols)} symbols")
        for symbol in self.symbols:
            try:
                # Check how many minutes we might have missed
                meta = self.r.hgetall(candle_meta_key(symbol, "1m"))
                if meta:
                    last_ts = b2f(meta.get("as_of_ts", 0))
                    gap_minutes = max(1, int((time.time() - last_ts) / 60) + 2)
                    limit = min(gap_minutes, 500)
                else:
                    limit = 500

                candles = _fetch_klines_rest(symbol, "1m", limit=limit)
                for c in candles:
                    c["is_final"] = True
                    push_candle(self.r, symbol, "1m", c)

                # Re-aggregate HTFs from backfilled candles
                # Reset aggregator state for this symbol to re-sync
                self.aggregator.reset_symbol(symbol)
                for c in candles:
                    for tf, completed in self.aggregator.on_1m_closed(symbol, c):
                        completed["is_final"] = True
                        push_candle(self.r, symbol, tf, completed)
                        xadd_candle_closed(self.r, tf, symbol, completed["bar_start_ts"])

            except Exception as e:
                logger.warning(f"Backfill failed for {symbol}: {e}")


# ---------------------------------------------------------------------------
# Binance REST helpers
# ---------------------------------------------------------------------------

def _fetch_klines_rest(symbol: str, interval: str, limit: int = 500) -> list[dict]:
    """Fetch OHLCV from Binance REST. Returns list of candle dicts."""
    url = f"{BINANCE_BASE}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=BINANCE_TIMEOUT)
        if resp.status_code in (418, 429):
            logger.warning(f"Rate limited fetching {symbol} {interval}")
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"REST fetch failed for {symbol} {interval}: {e}")
        return []

    candles = []
    for k in data:
        candles.append({
            "bar_start_ts": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    return candles


def _fetch_top_symbols(n: int = 200) -> list[str]:
    """Fetch top N USDT pairs by 24h quote volume."""
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
        return [t["symbol"] for t in usdt_pairs[:n]]
    except Exception as e:
        logger.warning(f"Dynamic symbol fetch failed: {e}")
        # Fallback: import from signal_engine
        from signal_engine import get_default_symbols
        return get_default_symbols()[:n]


# ---------------------------------------------------------------------------
# Ingestor main class
# ---------------------------------------------------------------------------

class Ingestor:
    """Main ingestor service: seed + stream."""

    def __init__(self):
        # Invariant #24: decode_responses=True mandatory
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.symbols: list[str] = []
        self.timeframes = TIMEFRAMES
        self.shards: list[ConnectionShard] = []

    def run(self):
        """Main entry point. Seed-then-stream (invariant #15)."""
        logger.info("Ingestor starting")

        # Step 1: Build universe
        if USE_DYNAMIC_SYMBOLS:
            self.symbols = _fetch_top_symbols(SYMBOL_COUNT)
        else:
            from signal_engine import get_default_symbols
            self.symbols = get_default_symbols()[:SYMBOL_COUNT]

        # Ensure BTCUSDT is always included (needed for BTC filters)
        if "BTCUSDT" not in self.symbols:
            self.symbols.insert(0, "BTCUSDT")

        logger.info(f"Universe: {len(self.symbols)} symbols, "
                     f"timeframes: {self.timeframes}")

        # Step 2: Populate universe sets + cache:timeframes
        pipe = self.r.pipeline(transaction=False)
        pipe.delete("universe:monitored", "universe:promoted", "cache:timeframes")
        if self.symbols:
            pipe.sadd("universe:monitored", *self.symbols)
            # Top 50 by volume get full treatment
            pipe.sadd("universe:promoted", *self.symbols[:50])
        if self.timeframes:
            pipe.sadd("cache:timeframes", *self.timeframes)
        pipe.execute()

        # Step 3: Create consumer groups (invariant #31)
        create_consumer_groups(self.r, self.timeframes)

        # Step 4: Seed — fetch 600 x 1m bars, aggregate HTFs
        logger.info("Seeding candle data...")
        self._seed()

        # Step 5: Mark ready
        self.r.set("cache:ready", "1")
        logger.info("Seeding complete, cache:ready=1")

        # Step 6: Start WS shard threads (invariant #15 — only after seed)
        self._start_shards()

        # Keep main thread alive
        try:
            while True:
                time.sleep(60)
                self._log_health()
        except KeyboardInterrupt:
            logger.info("Shutting down")
            for shard in self.shards:
                shard.stop()

    def _seed(self):
        """Fetch 1m bars only, aggregate all HTFs (invariant #5)."""
        def seed_symbol(symbol):
            try:
                candles_1m = _fetch_klines_rest(symbol, "1m", limit=RING_BUFFER_SIZE)
                if not candles_1m:
                    return symbol, False

                for c in candles_1m:
                    c["is_final"] = True
                    push_candle(self.r, symbol, "1m", c)

                aggregator = CandleAggregator(self.timeframes)
                for c in candles_1m:
                    for tf, completed in aggregator.on_1m_closed(symbol, c):
                        completed["is_final"] = True
                        push_candle(self.r, symbol, tf, completed)

                return symbol, True
            except Exception as e:
                logger.warning(f"Seed failed for {symbol}: {e}")
                return symbol, False

        succeeded = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=SEED_WORKERS) as pool:
            futures = pool.map(seed_symbol, self.symbols)
            for symbol, ok in futures:
                if ok:
                    succeeded += 1
                else:
                    failed += 1
                if (succeeded + failed) % 50 == 0:
                    logger.info(f"Seeded {succeeded}/{succeeded + failed} symbols")

        logger.info(f"Seeding done: {succeeded} ok, {failed} failed")

    def _start_shards(self):
        """Start WS shard threads (invariant #6)."""
        n_shards = max(1, math.ceil(len(self.symbols) / WS_MAX_STREAMS_PER_CONN))

        # Sort symbols for deterministic partitioning
        sorted_symbols = sorted(self.symbols)
        chunk_size = math.ceil(len(sorted_symbols) / n_shards)

        for i in range(n_shards):
            chunk = sorted_symbols[i * chunk_size:(i + 1) * chunk_size]
            if not chunk:
                continue
            shard = ConnectionShard(
                shard_id=i,
                symbols=chunk,
                redis_url=REDIS_URL,
                timeframes=self.timeframes,
            )
            shard.start()
            self.shards.append(shard)

        logger.info(f"Started {len(self.shards)} WS shards")

    def _log_health(self):
        """Periodic health log."""
        alive = sum(1 for s in self.shards if s.is_alive())
        logger.info(f"Health: {alive}/{len(self.shards)} shards alive, "
                     f"cache:ready={b2s(self.r.get('cache:ready'))}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    setup_logging()
    ingestor = Ingestor()
    ingestor.run()
