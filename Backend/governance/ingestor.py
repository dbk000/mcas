"""
Ingestor — WebSocket → Redis candle ring buffers.

Separate Docker service. Ingestion only. Uses websocket-client library
(callback-driven, not asyncio — invariant #14).

Startup sequence (invariant #15 — seed-then-stream, no interleaving):
1. Build universe
2. Populate universe:monitored + universe:promoted + cache:timeframes
3. Create consumer groups for ALL timeframe streams (invariant #31)
4. Seed: fetch 600 x 1m bars per symbol via REST, aggregate to all TFs
5. Set cache:ready = "1"
6. THEN start WS shard threads (1s klines) — WS does NOT start until step 5 completes

Data flow: Binance WS 1s klines → CandleAggregator → 1m/5m/15m/1h/4h/1d
"""

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

from governance.cache_contract import (
    push_candle, update_forming_candle, get_candles,
    xadd_candle_closed, create_consumer_groups,
    candle_meta_key, b2s, b2f,
    WS_MAX_STREAMS_PER_CONN, TF_SECONDS, RING_BUFFER_SIZE,
    symbol_partition, is_bar_closed,
    get_social_candidates, SOCIAL_CANDIDATES_KEY,
    PIPELINE_ACTIVITY_KEY, PIPELINE_ACTIVITY_MAX,
    store_volume_24h,
)

logger = logging.getLogger("ingestor")

# ---------------------------------------------------------------------------
# Config — module-level singletons set once by _init_config()
# ---------------------------------------------------------------------------

REDIS_URL = None
DATABASE_URL = None
SYMBOL_COUNT = None  # DEPRECATED: fallback only — uses KNOWN_UNIVERSE_SIZE
USE_DYNAMIC_SYMBOLS = None
SEED_WORKERS = None
LOG_LEVEL = None
KNOWN_UNIVERSE_SIZE = None
MIN_24H_QUOTE_VOLUME_USD = None
MAX_SOCIAL_PROMOTIONS_PER_CYCLE = None
MAX_SOCIAL_PROMOTIONS_ACTIVE = None
TIMEFRAMES = None
BINANCE_BASE = None

# Constants (not from env/registry)
DEMOTION_FACTOR = 0.8  # Hysteresis: demote at 80% of promote threshold
REFRESH_INTERVAL_S = 3600  # Hourly universe refresh
BINANCE_TIMEOUT = 10


def _init_config():
    """Read all config from the frozen ConfigRegistry. Called once from entry point."""
    global REDIS_URL, DATABASE_URL, SYMBOL_COUNT, USE_DYNAMIC_SYMBOLS
    global SEED_WORKERS, LOG_LEVEL, KNOWN_UNIVERSE_SIZE, MIN_24H_QUOTE_VOLUME_USD
    global MAX_SOCIAL_PROMOTIONS_PER_CYCLE, MAX_SOCIAL_PROMOTIONS_ACTIVE
    global TIMEFRAMES, BINANCE_BASE

    from governance.bootstrap import get_config
    worker = get_config().Worker

    REDIS_URL = worker.REDIS_URL
    DATABASE_URL = worker.DATABASE_URL
    SYMBOL_COUNT = worker.KNOWN_UNIVERSE_SIZE  # DEPRECATED fallback uses universe size
    USE_DYNAMIC_SYMBOLS = worker.USE_DYNAMIC_SYMBOLS
    SEED_WORKERS = worker.SEED_WORKERS
    LOG_LEVEL = worker.LOG_LEVEL
    KNOWN_UNIVERSE_SIZE = worker.KNOWN_UNIVERSE_SIZE
    MIN_24H_QUOTE_VOLUME_USD = worker.MIN_24H_QUOTE_VOLUME_USD
    MAX_SOCIAL_PROMOTIONS_PER_CYCLE = worker.MAX_SOCIAL_PROMOTIONS_PER_CYCLE
    MAX_SOCIAL_PROMOTIONS_ACTIVE = worker.MAX_SOCIAL_PROMOTIONS_ACTIVE
    TIMEFRAMES = [t.strip() for t in worker.DEFAULT_TIMEFRAMES.split(",") if t.strip()]
    BINANCE_BASE = worker.BINANCE_API_V3_BASE


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
# CandleAggregator (invariant #32 — symbol-scoped state)
# ---------------------------------------------------------------------------

class CandleAggregator:
    """Aggregate 1s candles into all timeframes (1m through 1d).

    Maintains independent state per symbol (invariant #32).
    A single aggregator shared across a WS shard handling multiple
    symbols MUST NOT blend 1s closes from different symbols.

    Invariant #5: seed only 1s (or 1m from REST), then aggregate all TFs.
    """

    # Aggregation targets: minutes per timeframe.
    # 1s WS → aggregator builds everything including 1m.
    AGG_MAP = {
        "1m": 1,
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
                        Only timeframes present in AGG_MAP are used.
        """
        self.targets = {
            tf: self.AGG_MAP[tf]
            for tf in timeframes
            if tf in self.AGG_MAP
        }
        # state[symbol][tf] = {"open", "high", "low", "close", "volume",
        #                       "bar_start_ts", "count"}
        self.state: dict[str, dict[str, dict]] = defaultdict(dict)

    def on_1s_closed(self, symbol: str, candle_1s: dict) -> list[tuple[str, dict]]:
        """Process a closed 1s candle and return any completed TF candles.

        Args:
            symbol: e.g. "BTCUSDC" (quote follows registry QUOTE_CURRENCY)
            candle_1s: dict with bar_start_ts, open, high, low, close, volume

        Returns:
            List of (timeframe, completed_candle_dict) for each TF that completed.
        """
        completed = []
        bar_ts = int(candle_1s["bar_start_ts"])

        for tf, minutes in self.targets.items():
            tf_seconds = minutes * 60
            # Determine which TF bar this 1s candle belongs to
            tf_bar_start = (bar_ts // (tf_seconds * 1000)) * (tf_seconds * 1000)

            sym_state = self.state[symbol]
            current = sym_state.get(tf)

            if current is None or current["bar_start_ts"] != tf_bar_start:
                # New TF bar starting
                if current is not None and current["bar_start_ts"] != tf_bar_start:
                    # Previous bar is complete
                    completed.append((tf, dict(current)))

                sym_state[tf] = {
                    "bar_start_ts": tf_bar_start,
                    "open": float(candle_1s["open"]),
                    "high": float(candle_1s["high"]),
                    "low": float(candle_1s["low"]),
                    "close": float(candle_1s["close"]),
                    "volume": float(candle_1s["volume"]),
                    "count": 1,
                }
            else:
                # Update existing TF bar
                current["high"] = max(current["high"], float(candle_1s["high"]))
                current["low"] = min(current["low"], float(candle_1s["low"]))
                current["close"] = float(candle_1s["close"])
                current["volume"] += float(candle_1s["volume"])
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

        streams = "/".join(f"{s.lower()}@kline_1s" for s in self.symbols)
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
        """Callback: parse 1s kline, aggregate to all TFs, update Redis."""
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

            candle_1s = {
                "bar_start_ts": int(kline["t"]),
                "open": float(kline["o"]),
                "high": float(kline["h"]),
                "low": float(kline["l"]),
                "close": close_price,
                "volume": float(kline["v"]),
            }

            # Legacy price update on every tick (forming or closed)
            self.r.hset(f"price:{symbol}", mapping={
                "price": str(close_price),
                "timestamp": str(now),
            })

            if kline["x"]:  # 1s bar closed
                self.r.expire(f"price:{symbol}", 300)

                # Feed closed 1s candle into aggregator — produces completed
                # TF bars (1m, 5m, 15m, 1h, 4h, 1d) as they fill up.
                for tf, completed in self.aggregator.on_1s_closed(symbol, candle_1s):
                    completed["is_final"] = True  # Set in dict (invariant #29)
                    push_candle(self.r, symbol, tf, completed)
                    xadd_candle_closed(self.r, tf, symbol, completed["bar_start_ts"])

                    # Pipeline activity push — non-critical
                    try:
                        evt = json.dumps({
                            "ts": time.time(),
                            "stage": "ING",
                            "tf": tf,
                            "bars": 1,
                            "symbols": len(self._symbol_set),
                        }, separators=(",", ":"))
                        p = self.r.pipeline(transaction=False)
                        p.lpush(PIPELINE_ACTIVITY_KEY, evt)
                        p.ltrim(PIPELINE_ACTIVITY_KEY, 0, PIPELINE_ACTIVITY_MAX - 1)
                        p.execute()
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Shard {self.shard_id} message error: {e}", exc_info=True)

    def _backfill(self):
        """Backfill missed candles on reconnect via REST (1m granularity).

        REST only serves 1m minimum, so we feed 1m bars through the
        aggregator which produces 1m + all HTFs. Dedupe gate in
        push_candle() (invariant #23) silently skips overlapping bars.
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

                # Reset aggregator state to re-sync, then feed 1m bars
                # through aggregator — produces 1m + all HTFs
                self.aggregator.reset_symbol(symbol)
                for c in candles:
                    for tf, completed in self.aggregator.on_1s_closed(symbol, c):
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
    """DEPRECATED: fallback only. Use _fetch_volume_map() for two-tier universe."""
    from governance.bootstrap import quote_currency as _qc
    _quote = _qc()
    try:
        resp = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=15)
        resp.raise_for_status()
        tickers = resp.json()
        pairs = [
            t for t in tickers
            if t["symbol"].endswith(_quote)
            and float(t["quoteVolume"]) > 0
        ]
        pairs.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
        return [t["symbol"] for t in pairs[:n]]
    except Exception as e:
        logger.warning(f"Dynamic symbol fetch failed: {e}")
        # Fallback: import from signal_engine
        from governance.signal_engine import get_default_symbols
        return get_default_symbols()[:n]


def _fetch_volume_map() -> dict[str, float]:
    """Fetch 24h quote volume for all pairs quoted in the registry
    ``QUOTE_CURRENCY`` from Binance.

    Invariant: Known Universe is strictly single-quote so quoteVolume ≈ USD
    (no cross-rate conversion needed).

    Returns:
        {symbol: quoteVolume} for all qualifying pairs with volume > 0.
    """
    from governance.bootstrap import quote_currency as _qc
    _quote = _qc()
    try:
        resp = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=15)
        resp.raise_for_status()
        tickers = resp.json()
        # Single-quote filter — Canon §1.4C invariant
        return {
            t["symbol"]: float(t["quoteVolume"])
            for t in tickers
            if t["symbol"].endswith(_quote)
            and float(t["quoteVolume"]) > 0
        }
    except Exception as e:
        logger.warning(f"Volume map fetch failed: {e}")
        return {}


def _filter_monitored_universe(
    known: list[str],
    volume_map: dict[str, float],
    min_volume: float,
    current_monitored: set[str] | None = None,
) -> list[str]:
    """Filter Known Universe to Monitored Universe with hysteresis.

    Promotion: symbol enters Monitored if quoteVolume >= min_volume.
    Demotion:  symbol leaves Monitored only if quoteVolume < min_volume * DEMOTION_FACTOR.
    This prevents churn from symbols oscillating near the threshold.

    Safety net: if fewer than 50 symbols pass filter, fall back to top SYMBOL_COUNT
    by volume to avoid an empty monitored set.

    The macro reference symbol (BTC{QUOTE_CURRENCY}) is always included.
    """
    if current_monitored is None:
        current_monitored = set()

    demotion_threshold = min_volume * DEMOTION_FACTOR
    monitored = set()

    for sym in known:
        vol = volume_map.get(sym, 0)
        if vol >= min_volume:
            # Promote: meets full threshold
            monitored.add(sym)
        elif sym in current_monitored and vol >= demotion_threshold:
            # Hysteresis: already monitored and above demotion threshold
            monitored.add(sym)

    # Safety net: ensure at least 50 symbols
    if len(monitored) < 50:
        logger.warning(f"Only {len(monitored)} symbols pass filter, "
                       f"falling back to top {SYMBOL_COUNT} by volume")
        sorted_known = sorted(known, key=lambda s: volume_map.get(s, 0), reverse=True)
        monitored = set(sorted_known[:SYMBOL_COUNT])

    # Macro reference symbol always included
    from governance.bootstrap import macro_reference_symbol
    _macro_sym = macro_reference_symbol()
    if _macro_sym in volume_map or _macro_sym in known:
        monitored.add(_macro_sym)

    return sorted(monitored)


def _get_social_promoted_symbols(r) -> list[str]:
    """Read social promotion candidates from Redis ZSET.

    Returns up to MAX_SOCIAL_PROMOTIONS_PER_CYCLE symbols, capped at
    MAX_SOCIAL_PROMOTIONS_ACTIVE total. Both default to 0 (disabled).

    Brain 1 publishes candidates to universe:social_candidates ZSET.
    See: Backend/social_collector/app/brain1.py (_publish_social_candidates)
    See: Backend/worker/app/cache_contract.py (SOCIAL_CANDIDATES_KEY)
    """
    if MAX_SOCIAL_PROMOTIONS_PER_CYCLE <= 0 or MAX_SOCIAL_PROMOTIONS_ACTIVE <= 0:
        return []

    try:
        candidates = get_social_candidates(r)
    except Exception as e:
        logger.warning("Social promotion: failed to read candidates: %s", e)
        return []

    if not candidates:
        return []

    # Cap per cycle and total active
    promoted = candidates[:min(MAX_SOCIAL_PROMOTIONS_PER_CYCLE, MAX_SOCIAL_PROMOTIONS_ACTIVE)]

    if promoted:
        logger.info("Social promotion: %d candidates available, promoting %d "
                     "(per_cycle=%d, max_active=%d)",
                     len(candidates), len(promoted),
                     MAX_SOCIAL_PROMOTIONS_PER_CYCLE, MAX_SOCIAL_PROMOTIONS_ACTIVE)

    return promoted


# ---------------------------------------------------------------------------
# Ingestor main class
# ---------------------------------------------------------------------------

class Ingestor:
    """Main ingestor service: seed + stream.

    Two-tier universe (Canon §1.4C):
    - Known Universe (~1024): top USDT pairs by volume. OHLCV ingested hourly via REST.
    - Monitored Universe (dynamic ~150-300): filtered by MIN_24H_QUOTE_VOLUME_USD.
      Gets WS 1m streams, indicator computation, signal evaluation.
    """

    def __init__(self):
        # Invariant #24: decode_responses=True mandatory
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.known: list[str] = []
        self.monitored: list[str] = []
        self.symbols: list[str] = []  # Alias for monitored (backward compat)
        self.timeframes = TIMEFRAMES
        self.shards: list[ConnectionShard] = []
        self._shards_lock = threading.Lock()
        self._last_refresh_ts: float = 0.0

    def run(self):
        """Main entry point. Seed-then-stream (invariant #15)."""
        logger.info("Ingestor starting")

        # Step 1: Build two-tier universe
        volume_map = _fetch_volume_map()
        if not volume_map:
            # Fallback: no volume data, use legacy static list
            logger.warning("Volume map empty, falling back to static symbols")
            if USE_DYNAMIC_SYMBOLS:
                self.monitored = _fetch_top_symbols(SYMBOL_COUNT)
            else:
                from governance.signal_engine import get_default_symbols
                self.monitored = get_default_symbols()[:SYMBOL_COUNT]
            self.known = list(self.monitored)
        else:
            # Known Universe: top N by volume
            sorted_symbols = sorted(volume_map.keys(),
                                    key=lambda s: volume_map[s], reverse=True)
            self.known = sorted_symbols[:KNOWN_UNIVERSE_SIZE]

            # Monitored Universe: filtered by volume threshold with hysteresis
            self.monitored = _filter_monitored_universe(
                self.known, volume_map, MIN_24H_QUOTE_VOLUME_USD)

            # SNAPSHOT_ENRICH: persist volume map so signal_worker can inject
            # quote_volume_24h_usd into IndicatorSnapshot payloads. Permissive
            # on failure — signal_worker handles missing data.
            try:
                store_volume_24h(self.r, volume_map)
                logger.info("volume_24h: persisted %d symbols", len(volume_map))
            except Exception as e:
                logger.warning("volume_24h: persist failed: %s", e)

        # Ensure the macro reference symbol is always included (needed
        # for BTC filters against the current QUOTE_CURRENCY)
        from governance.bootstrap import macro_reference_symbol
        _macro_sym = macro_reference_symbol()
        if _macro_sym not in self.monitored:
            self.monitored.insert(0, _macro_sym)
        if _macro_sym not in self.known:
            self.known.insert(0, _macro_sym)

        # Social promotion: merge candidates into monitored universe
        social_promoted = _get_social_promoted_symbols(self.r)
        if social_promoted:
            before = len(self.monitored)
            self.monitored = sorted(set(self.monitored) | set(social_promoted))
            logger.info(f"Social promotion: added {len(self.monitored) - before} "
                        f"symbols to monitored universe")

        self.symbols = self.monitored  # backward compat alias

        logger.info(f"Known Universe: {len(self.known)} symbols")
        logger.info(f"Monitored Universe: {len(self.monitored)} symbols")
        logger.info(f"Timeframes: {self.timeframes}")

        # Step 2: Atomic universe swap to Redis
        self._atomic_universe_swap(self.known, self.monitored)

        # Populate cache:timeframes
        pipe = self.r.pipeline(transaction=False)
        pipe.delete("cache:timeframes")
        if self.timeframes:
            pipe.sadd("cache:timeframes", *self.timeframes)
        pipe.execute()

        # Step 3: Create consumer groups (invariant #31)
        create_consumer_groups(self.r, self.timeframes)

        # Step 4: Seed monitored symbols — fetch 600 x 1m bars, aggregate HTFs
        logger.info("Seeding candle data for monitored universe...")
        self._seed(self.monitored)

        # Step 5: Mark ready
        self.r.set("cache:ready", "1")
        logger.info("Seeding complete, cache:ready=1")

        # Step 6: Start WS shard threads for monitored symbols (invariant #15)
        self._start_shards()

        # Step 7: Start hourly refresh loop as daemon thread
        self._last_refresh_ts = time.time()
        refresh_thread = threading.Thread(
            target=self._refresh_loop, daemon=True, name="universe-refresh")
        refresh_thread.start()
        logger.info("Hourly universe refresh loop started")

        # Keep main thread alive
        try:
            while True:
                time.sleep(60)
                self._log_health()
        except KeyboardInterrupt:  # EXEMPT: graceful SIGINT shutdown — os._exit(78) is uncatchable
            logger.info("Shutting down")
            with self._shards_lock:
                for shard in self.shards:
                    shard.stop()

    def _seed(self, symbols: list[str]):
        """Fetch 1m bars only, aggregate all HTFs (invariant #5).

        Args:
            symbols: list of symbols to seed (typically monitored universe).
        """
        def seed_symbol(symbol):
            try:
                candles_1m = _fetch_klines_rest(symbol, "1m", limit=RING_BUFFER_SIZE)
                if not candles_1m:
                    return symbol, False

                # Feed 1m REST bars through aggregator — produces 1m + all HTFs
                aggregator = CandleAggregator(self.timeframes)
                for c in candles_1m:
                    for tf, completed in aggregator.on_1s_closed(symbol, c):
                        completed["is_final"] = True
                        push_candle(self.r, symbol, tf, completed)

                return symbol, True
            except Exception as e:
                logger.warning(f"Seed failed for {symbol}: {e}")
                return symbol, False

        succeeded = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=SEED_WORKERS) as pool:
            futures = pool.map(seed_symbol, symbols)
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

    def _atomic_universe_swap(self, known: list[str], monitored: list[str]):
        """Write universe SETs via atomic RENAME (Canon §1.4C).

        Writes to staging keys then RENAMEs to live keys.
        RENAME is O(1) and atomic — readers never see an empty or partial set.
        """
        pipe = self.r.pipeline(transaction=False)
        # Clear staging keys in case of leftover from previous crash
        pipe.delete("universe:known:staging", "universe:monitored:staging")
        pipe.execute()

        # Populate staging keys
        if known:
            self.r.sadd("universe:known:staging", *known)
        else:
            # Must have at least one member for RENAME to work
            self.r.sadd("universe:known:staging", "__placeholder__")
        if monitored:
            self.r.sadd("universe:monitored:staging", *monitored)
        else:
            self.r.sadd("universe:monitored:staging", "__placeholder__")

        # Atomic swap: RENAME is O(1)
        self.r.rename("universe:known:staging", "universe:known")
        self.r.rename("universe:monitored:staging", "universe:monitored")

        # Clean up deprecated universe:promoted key
        self.r.delete("universe:promoted")

        logger.info(f"Universe swap complete: "
                     f"known={len(known)}, monitored={len(monitored)}")

    def _refresh_loop(self):
        """Hourly universe refresh loop (runs in daemon thread).

        Every REFRESH_INTERVAL_S seconds:
        1. Re-fetch volume map from Binance
        2. Re-derive Known Universe (top N by volume)
        3. Re-filter Monitored Universe (with hysteresis)
        4. Ingest 1h OHLCV for entire Known Universe to Postgres
        5. If monitored changed: atomic swap, seed new symbols, restart WS shards
        """
        while True:
            time.sleep(60)  # Check every 60s
            elapsed = time.time() - self._last_refresh_ts
            if elapsed < REFRESH_INTERVAL_S:
                continue

            self._last_refresh_ts = time.time()
            logger.info("Hourly refresh: starting")

            try:
                volume_map = _fetch_volume_map()
                if not volume_map:
                    logger.warning("Hourly refresh: volume map empty, skipping")
                    continue

                # SNAPSHOT_ENRICH: refresh Redis hash for signal_worker reads.
                try:
                    store_volume_24h(self.r, volume_map)
                    logger.info("volume_24h: refreshed %d symbols", len(volume_map))
                except Exception as e:
                    logger.warning("volume_24h: refresh failed: %s", e)

                # Re-derive Known Universe
                sorted_symbols = sorted(volume_map.keys(),
                                        key=lambda s: volume_map[s], reverse=True)
                new_known = sorted_symbols[:KNOWN_UNIVERSE_SIZE]

                # Re-filter Monitored with hysteresis (pass current monitored set)
                current_monitored_set = set(self.monitored)
                new_monitored = _filter_monitored_universe(
                    new_known, volume_map, MIN_24H_QUOTE_VOLUME_USD,
                    current_monitored=current_monitored_set)

                # Ensure macro reference symbol always included
                from governance.bootstrap import macro_reference_symbol
                _macro_sym = macro_reference_symbol()
                if _macro_sym not in new_monitored:
                    new_monitored.insert(0, _macro_sym)
                if _macro_sym not in new_known:
                    new_known.insert(0, _macro_sym)

                # Social promotion: merge candidates into refreshed monitored
                social_promoted = _get_social_promoted_symbols(self.r)
                if social_promoted:
                    before = len(new_monitored)
                    new_monitored = sorted(set(new_monitored) | set(social_promoted))
                    if len(new_monitored) > before:
                        logger.info(f"Social promotion (refresh): added "
                                    f"{len(new_monitored) - before} symbols")

                # Ingest 1h OHLCV for Known Universe to Postgres (Task 2)
                self._ingest_known_ohlcv(new_known)

                # Diff monitored universe
                old_set = set(self.monitored)
                new_set = set(new_monitored)
                added = new_set - old_set
                removed = old_set - new_set

                if added or removed:
                    logger.info(f"Universe changed: +{len(added)} -{len(removed)} "
                                f"(was {len(self.monitored)}, now {len(new_monitored)})")
                    if added:
                        logger.info(f"  Added: {sorted(added)[:20]}{'...' if len(added) > 20 else ''}")
                    if removed:
                        logger.info(f"  Removed: {sorted(removed)[:20]}{'...' if len(removed) > 20 else ''}")

                    # Update state
                    self.known = new_known
                    self.monitored = new_monitored
                    self.symbols = self.monitored

                    # Atomic swap Redis SETs
                    self._atomic_universe_swap(self.known, self.monitored)

                    # Seed newly added symbols
                    if added:
                        logger.info(f"Seeding {len(added)} new monitored symbols")
                        self._seed(sorted(added))

                    # Restart WS shards with new monitored set
                    self._restart_ws_shards(self.monitored)
                else:
                    # Just update known (may have changed) without WS restart
                    self.known = new_known
                    self._atomic_universe_swap(self.known, self.monitored)
                    logger.info("Universe unchanged, atomic swap for known update")

                logger.info("Hourly refresh: complete")

            except Exception as e:
                logger.error(f"Hourly refresh failed: {e}", exc_info=True)

    def _restart_ws_shards(self, new_monitored: list[str]):
        """Stop all existing WS shards and start new ones for new_monitored.

        After restart: backfill gap window per symbol (last 5 min of 1m candles
        via REST) to cover the gap during shard restart.
        """
        with self._shards_lock:
            # Stop existing shards
            logger.info(f"Stopping {len(self.shards)} existing WS shards")
            for shard in self.shards:
                shard.stop()
            for shard in self.shards:
                shard.join(timeout=10)
            self.shards.clear()

            # Start new shards
            n_shards = max(1, math.ceil(len(new_monitored) / WS_MAX_STREAMS_PER_CONN))
            sorted_symbols = sorted(new_monitored)
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

            logger.info(f"Started {len(self.shards)} new WS shards")

        # Backfill gap window (last 5 min of 1m candles per symbol)
        logger.info(f"Backfilling gap window for {len(new_monitored)} symbols")
        def backfill_gap(symbol):
            try:
                candles = _fetch_klines_rest(symbol, "1m", limit=5)
                agg = CandleAggregator(self.timeframes)
                for c in candles:
                    for tf, completed in agg.on_1s_closed(symbol, c):
                        completed["is_final"] = True
                        push_candle(self.r, symbol, tf, completed)
            except Exception as e:
                logger.warning(f"Gap backfill failed for {symbol}: {e}")

        with ThreadPoolExecutor(max_workers=SEED_WORKERS) as pool:
            pool.map(backfill_gap, new_monitored)

    def _ingest_known_ohlcv(self, known_symbols: list[str]):
        """Ingest 1h OHLCV for entire Known Universe to Postgres.

        Fetches latest 2 bars of 1h candles per symbol via REST.
        The 2nd-to-last bar is guaranteed is_final=True.

        Rate limiting: ThreadPoolExecutor with 20 workers + per-thread delay.
        Batched DB writes: single bulk INSERT with executemany.
        """
        from datetime import datetime as dt, timezone as tz

        logger.info(f"Ingesting 1h OHLCV for {len(known_symbols)} known symbols")
        all_bars = []  # Thread-safe collection via append
        bars_lock = threading.Lock()

        def fetch_one(symbol):
            try:
                candles = _fetch_klines_rest(symbol, "1h", limit=2)
                if candles:
                    now = dt.now(tz.utc)
                    rows = []
                    for c in candles:
                        ts = dt.fromtimestamp(c["bar_start_ts"] / 1000, tz=tz.utc)
                        # Canon §8 anti-lookahead: skip forming bar entirely.
                        # REST returns the current (still-forming) hour as the
                        # last candle — writing it with is_final=TRUE would
                        # violate ohlcv_is_final_canon (migration 20260409).
                        if not is_bar_closed(ts, "1h", reference_ts=now):
                            continue
                        rows.append((
                            symbol, "1h", ts,
                            c["open"], c["high"], c["low"], c["close"], c["volume"],
                            now, True, "binance",
                        ))
                    if rows:
                        with bars_lock:
                            all_bars.extend(rows)
                time.sleep(0.05)  # Rate limiting: ~20 req/s per worker
            except Exception as e:
                logger.warning(f"Known OHLCV fetch failed for {symbol}: {e}")

        with ThreadPoolExecutor(max_workers=SEED_WORKERS) as pool:
            pool.map(fetch_one, known_symbols)

        if not all_bars:
            logger.warning("No OHLCV bars collected for known universe")
            return

        # Bulk insert to Postgres
        try:
            from governance.persistence import get_conn as _pool_get_conn
            db_url = DATABASE_URL  # Use module-level BOOTSTRAP_BOUNDARY constant
            if not db_url:
                logger.warning("DATABASE_URL not set, skipping OHLCV persistence")
                return

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
                    """, all_bars)
                conn.commit()

            logger.info(f"Hourly refresh: ingested {len(all_bars)} known OHLCV bars "
                        f"for {len(known_symbols)} symbols")
        except Exception as e:
            logger.error(f"Known OHLCV DB insert failed: {e}", exc_info=True)

    def _log_health(self):
        """Periodic health log."""
        with self._shards_lock:
            alive = sum(1 for s in self.shards if s.is_alive())
            total = len(self.shards)
        known_count = self.r.scard("universe:known")
        monitored_count = self.r.scard("universe:monitored")
        logger.info(f"Health: {alive}/{total} shards alive, "
                     f"known={known_count}, monitored={monitored_count}, "
                     f"cache:ready={b2s(self.r.get('cache:ready'))}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run():
    """Async wrapper for supervisor. Runs Ingestor in a thread."""
    import asyncio
    _init_config()
    setup_logging()
    ingestor = Ingestor()
    await asyncio.to_thread(ingestor.run)


if __name__ == "__main__":
    from governance.bootstrap import bootstrap_genesis
    bootstrap_genesis()
    _init_config()
    setup_logging()
    ingestor = Ingestor()
    ingestor.run()
