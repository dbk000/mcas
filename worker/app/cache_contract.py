"""
Cache Contract - Redis Key Patterns and Operations
===================================================

Single source of truth for all Redis key patterns, constants, and serialization helpers.
All Redis access in the system should go through this module.

Design Invariants (see plan for full list):
- #7:  RPUSH oldest→newest for ring buffers (no reversal needed on read)
- #13: No LRANGE outside this module (all candle reads via get_candles)
- #16: ind_meta is the commit marker (written LAST by indicator_worker)
- #20: Stable partition hash via zlib.crc32() (NOT hash())
- #21: Transactional pipelines (MULTI/EXEC) for atomic writes
- #22: Mandatory m1/snap/m2 optimistic read-consistency
- #23: Dedupe gate on candle push
- #24: Decode safety via b2s()/b2f() helpers
- #26: Lua script for atomic stale signal pruning
- #27: Stream trimming is approximate (MAXLEN ~)
"""

import json
import time
import zlib
from dataclasses import dataclass, fields, is_dataclass
from typing import Optional, Any, Union
from enum import Enum

# Import types from existing modules
from indicators import OHLCV, IndicatorSnapshot


# =============================================================================
# Constants
# =============================================================================

RING_BUFFER_SIZE = 600          # Warmup needs 201 + margin
INDICATOR_HISTORY_SIZE = 200    # For percentile calcs
STALENESS_MULTIPLIER = 2.5      # Stale if age > 2.5× candle interval
STREAM_MAXLEN = 200_000         # Per stream (approximate trimming)
WS_MAX_STREAMS_PER_CONN = 200   # Binance safe limit per connection
SIGNAL_MAX_AGE_S = 3600         # 1h — prune signals older than this
SETUP_STATE_TTL_S = 86400       # 24h — prevent stale state for delisted symbols
RECONCILE_INTERVAL_S = 60       # Reconciliation loop frequency

TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# Higher timeframe mapping (for aggregation)
HTF_MAP = {
    "1m": "5m",
    "5m": "15m",
    "15m": "1h",
    "1h": "4h",
    "4h": "1d",
}

# All supported timeframes
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]


# =============================================================================
# Key Patterns
# =============================================================================

def candle_key(symbol: str, tf: str) -> str:
    """LIST key for candle ring buffer."""
    return f"candle:{symbol}:{tf}"


def candle_meta_key(symbol: str, tf: str) -> str:
    """HASH key for candle metadata."""
    return f"candle_meta:{symbol}:{tf}"


def ind_key(symbol: str, tf: str, indicator_id: str) -> str:
    """STRING key for individual indicator."""
    return f"ind:{symbol}:{tf}:{indicator_id}"


def ind_snap_key(symbol: str, tf: str) -> str:
    """HASH key for bulk indicator snapshot."""
    return f"ind_snap:{symbol}:{tf}"


def ind_meta_key(symbol: str, tf: str) -> str:
    """HASH key for indicator commit marker (written LAST)."""
    return f"ind_meta:{symbol}:{tf}"


def ind_hist_key(name: str, symbol: str, tf: str) -> str:
    """LIST key for indicator history (for percentile calcs)."""
    return f"ind_hist:{name}:{symbol}:{tf}"


def setup_state_key(setup_id: str, symbol: str, tf: str) -> str:
    """STRING key for setup state (with TTL)."""
    return f"setup_state:{setup_id}:{symbol}:{tf}"


def sig_key(symbol: str, tf: str, setup_id: str) -> str:
    """STRING key for signal payload."""
    return f"sig:{symbol}:{tf}:{setup_id}"


def sig_meta_key(symbol: str, tf: str, setup_id: str) -> str:
    """HASH key for signal metadata."""
    return f"sig_meta:{symbol}:{tf}:{setup_id}"


def signals_score_key(setup_id: str, tf: str) -> str:
    """ZSET key for signals sorted by score."""
    return f"signals:score:{setup_id}:{tf}"


def signals_ts_key(setup_id: str, tf: str) -> str:
    """ZSET key for signals sorted by timestamp."""
    return f"signals:ts:{setup_id}:{tf}"


def stream_candle_closed_key(tf: str) -> str:
    """STREAM key for candle closed events."""
    return f"stream:candle_closed:{tf}"


def stream_indicators_ready_key(tf: str) -> str:
    """STREAM key for indicators ready events."""
    return f"stream:indicators_ready:{tf}"


# =============================================================================
# Decode Safety Helpers (Invariant #24)
# =============================================================================

def b2s(val: Any) -> str:
    """
    Decode Redis bytes/str safely to str.

    Primary guarantee is decode_responses=True on client, but this is
    belt-and-braces fallback for any edge case.
    """
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return str(val)


def b2f(val: Any, default: float = 0.0) -> float:
    """
    Decode Redis bytes/str safely to float.

    Handles None, bytes, str, and NaN cases.
    """
    if val is None:
        return default
    try:
        if isinstance(val, bytes):
            val = val.decode("utf-8")
        f = float(val)
        # NaN check
        if f != f:
            return default
        return f
    except (ValueError, TypeError):
        return default


def b2i(val: Any, default: int = 0) -> int:
    """Decode Redis bytes/str safely to int."""
    if val is None:
        return default
    try:
        if isinstance(val, bytes):
            val = val.decode("utf-8")
        return int(float(val))
    except (ValueError, TypeError):
        return default


# =============================================================================
# Serialization Helpers
# =============================================================================

def serialize_candle(ohlcv: Union[OHLCV, dict]) -> str:
    """Serialize OHLCV to JSON string."""
    if isinstance(ohlcv, OHLCV):
        return json.dumps({
            "timestamp": ohlcv.timestamp,
            "open": ohlcv.open,
            "high": ohlcv.high,
            "low": ohlcv.low,
            "close": ohlcv.close,
            "volume": ohlcv.volume,
        })
    # Already a dict
    return json.dumps({
        "timestamp": ohlcv.get("timestamp") or ohlcv.get("bar_start_ts"),
        "open": ohlcv["open"],
        "high": ohlcv["high"],
        "low": ohlcv["low"],
        "close": ohlcv["close"],
        "volume": ohlcv["volume"],
    })


def deserialize_candle(s: str) -> OHLCV:
    """Deserialize JSON string to OHLCV."""
    d = json.loads(s)
    return OHLCV(
        timestamp=d.get("timestamp") or d.get("bar_start_ts", 0),
        open=float(d["open"]),
        high=float(d["high"]),
        low=float(d["low"]),
        close=float(d["close"]),
        volume=float(d["volume"]),
    )


def snapshot_to_indicator_dict(snapshot: IndicatorSnapshot) -> dict[str, float]:
    """
    Convert IndicatorSnapshot to dict of indicator_id -> value.
    Excludes ohlcv and HTF fields.
    """
    exclude = {"ohlcv", "htf_close", "htf_sma50", "htf_bb_mb"}
    result = {}
    for f in fields(snapshot):
        if f.name in exclude:
            continue
        val = getattr(snapshot, f.name)
        if val is not None and isinstance(val, (int, float)):
            result[f.name] = float(val)
    return result


def snapshot_to_flat_dict(snapshot: IndicatorSnapshot) -> dict[str, str]:
    """
    Convert IndicatorSnapshot to flat dict for HSET.
    Values are stringified for Redis.
    """
    d = snapshot_to_indicator_dict(snapshot)
    return {k: str(v) for k, v in d.items()}


def flat_dict_to_snapshot(d: dict) -> Optional[IndicatorSnapshot]:
    """
    Convert flat dict from HGETALL to IndicatorSnapshot.
    Returns None if missing required fields.
    """
    if not d:
        return None

    try:
        return IndicatorSnapshot(
            ohlcv=[],  # Not stored in flat dict
            atr=b2f(d.get("atr")),
            clv=b2f(d.get("clv")),
            volume_z=b2f(d.get("volume_z")),
            rsi=b2f(d.get("rsi")),
            rsi_sma=b2f(d.get("rsi_sma")),
            rsi_slope=b2f(d.get("rsi_slope")),
            sma_50=b2f(d.get("sma_50")),
            sma_200=b2f(d.get("sma_200")),
            ema_9=b2f(d.get("ema_9")),
            ema_20=b2f(d.get("ema_20")),
            ema_21=b2f(d.get("ema_21")),
            ema_50=b2f(d.get("ema_50")),
            bb_mb=b2f(d.get("bb_mb")),
            bb_ub=b2f(d.get("bb_ub")),
            bb_lb=b2f(d.get("bb_lb")),
            bb_width=b2f(d.get("bb_width")),
            kc_mb=b2f(d.get("kc_mb")),
            kc_ub=b2f(d.get("kc_ub")),
            kc_lb=b2f(d.get("kc_lb")),
            sma50_slope_atr=b2f(d.get("sma50_slope_atr")),
            sma200_slope_atr=b2f(d.get("sma200_slope_atr")),
            ma_gap_atr=b2f(d.get("ma_gap_atr")),
            htf_close=None,
            htf_sma50=None,
            htf_bb_mb=None,
        )
    except Exception:
        return None


# =============================================================================
# Partitioning (Invariant #20 - stable hash via zlib.crc32)
# =============================================================================

def symbol_partition(symbol: str, n_partitions: int) -> int:
    """
    Deterministic partition assignment for a symbol.

    Uses zlib.crc32() for stable hash across process restarts and services.
    Python's hash() is randomized per-process by default (PYTHONHASHSEED).
    """
    if n_partitions <= 0:
        return 0
    return zlib.crc32(symbol.encode("utf-8")) % n_partitions


# =============================================================================
# Candle Operations (Invariants #7, #13, #23)
# =============================================================================

def push_candle(r, symbol: str, tf: str, candle_dict: dict) -> bool:
    """
    Append candle to ring buffer. Oldest at index 0, newest at end.

    DEDUPE GATE (invariant #23): compares bar_start_ts against candle_meta.
    Skips if this bar is older-or-equal to last_bar_ts already in the buffer.
    Prevents reconnect backfill from pushing overlapping bars.

    Args:
        r: Redis client (with decode_responses=True)
        symbol: Trading pair
        tf: Timeframe
        candle_dict: Dict with bar_start_ts, open, high, low, close, volume, is_final

    Returns:
        True if candle was pushed, False if skipped (duplicate)
    """
    meta_key = candle_meta_key(symbol, tf)
    existing_ts = r.hget(meta_key, "last_bar_ts")

    if existing_ts is not None:
        if b2i(candle_dict.get("bar_start_ts", 0)) <= b2i(existing_ts):
            return False  # Skip duplicate / older bar

    key = candle_key(symbol, tf)
    r.rpush(key, serialize_candle(candle_dict))
    r.ltrim(key, -RING_BUFFER_SIZE, -1)  # Keep last 600

    r.hset(meta_key, mapping={
        "last_bar_ts": str(candle_dict.get("bar_start_ts", candle_dict.get("timestamp", 0))),
        "last_close": str(candle_dict["close"]),
        "is_final": str(candle_dict.get("is_final", True)),
        "as_of_ts": str(time.time()),
        "count": str(r.llen(key)),
    })

    return True


def get_candles(r, symbol: str, tf: str, limit: int = RING_BUFFER_SIZE) -> list[OHLCV]:
    """
    Read candles oldest→newest. THIS IS THE ONLY CANDLE READ PATH.

    Uses LRANGE(key, -limit, -1) which returns a contiguous tail slice
    preserving list order (oldest→newest).

    Invariant #13: No LRANGE outside cache_contract.py
    """
    key = candle_key(symbol, tf)
    raw = r.lrange(key, -limit, -1)
    return [deserialize_candle(b2s(s)) for s in raw]


def update_forming_candle(r, symbol: str, tf: str, candle_dict: dict):
    """
    Update the most recent (forming) candle in-place.

    Handles empty buffer gracefully: if list is empty (cold start,
    reconnect before seed), does RPUSH instead of LSET to avoid error.
    """
    key = candle_key(symbol, tf)
    if r.llen(key) == 0:
        r.rpush(key, serialize_candle(candle_dict))
    else:
        r.lset(key, -1, serialize_candle(candle_dict))

    r.hset(candle_meta_key(symbol, tf), mapping={
        "last_close": str(candle_dict["close"]),
        "is_final": "False",
        "as_of_ts": str(time.time()),
    })


# =============================================================================
# Indicator Operations (Invariants #16, #21)
# =============================================================================

def store_indicators(r, symbol: str, tf: str, snapshot: IndicatorSnapshot, bar_start_ts: int):
    """
    Write indicators with ind_meta as commit marker (written last).

    Uses transaction=True (MULTI/EXEC) so the entire batch is atomic.
    A concurrent reader either sees the old complete state or the new
    complete state — never a mix (invariant #21).

    Invariant #16: ind_meta is written LAST as commit marker.
    """
    pipe = r.pipeline(transaction=True)  # MULTI/EXEC

    # 1. Per-indicator STRING keys
    for ind_id, value in snapshot_to_indicator_dict(snapshot).items():
        pipe.set(ind_key(symbol, tf, ind_id), json.dumps({
            "value": value,
            "bar_start_ts": bar_start_ts,
            "as_of_ts": time.time(),
            "is_final": True,
        }))

    # 2. Bulk snapshot HASH
    pipe.hset(ind_snap_key(symbol, tf), mapping=snapshot_to_flat_dict(snapshot))

    # 3. History lists (for percentile calculations)
    pipe.rpush(ind_hist_key("bb_width", symbol, tf), str(snapshot.bb_width))
    pipe.ltrim(ind_hist_key("bb_width", symbol, tf), -INDICATOR_HISTORY_SIZE, -1)
    pipe.rpush(ind_hist_key("ma_gap", symbol, tf), str(snapshot.ma_gap_atr))
    pipe.ltrim(ind_hist_key("ma_gap", symbol, tf), -INDICATOR_HISTORY_SIZE, -1)

    # 4. COMMIT MARKER — written LAST (invariant #16)
    pipe.hset(ind_meta_key(symbol, tf), mapping={
        "bar_start_ts": str(bar_start_ts),
        "as_of_ts": str(time.time()),
    })

    pipe.execute()


def read_indicator_snapshot(r, symbol: str, tf: str) -> Optional[IndicatorSnapshot]:
    """
    Read indicator snapshot from HASH.
    Returns None if not found or invalid.
    """
    snap_dict = r.hgetall(ind_snap_key(symbol, tf))
    return flat_dict_to_snapshot(snap_dict)


def read_fresh_snapshot(
    r,
    symbol: str,
    tf: str,
    expected_bar_start_ts: Optional[int] = None
) -> tuple[Optional[IndicatorSnapshot], Optional[str]]:
    """
    Read snapshot with m1/snap/m2 consistency check.

    MANDATORY (invariant #22): reads ind_meta twice (before and after
    reading ind_snap). Only proceeds if bar_start_ts matches across
    both meta reads AND matches expected_bar_start_ts from the stream
    event. Rejects if any mismatch — prevents reading a half-written
    ind_snap mid-update and generating phantom signals.

    Returns:
        (IndicatorSnapshot, bar_start_ts) or (None, None)
    """
    # m1: read meta before snapshot
    m1 = r.hgetall(ind_meta_key(symbol, tf))
    if not m1:
        return None, None
    m1_ts = b2s(m1.get("bar_start_ts", ""))

    # If caller provided expected ts, verify m1 matches
    if expected_bar_start_ts is not None and m1_ts != str(expected_bar_start_ts):
        return None, None  # Meta doesn't match expected — stale or not yet written

    # snap: read the indicator snapshot
    snap = read_indicator_snapshot(r, symbol, tf)
    if snap is None:
        return None, None

    # m2: re-read meta after snapshot
    m2 = r.hgetall(ind_meta_key(symbol, tf))
    m2_ts = b2s(m2.get("bar_start_ts", "")) if m2 else ""

    # Reject if meta changed between m1 and m2 (concurrent write in progress)
    if m1_ts != m2_ts:
        return None, None

    return snap, m1_ts


def get_indicator_history(r, name: str, symbol: str, tf: str) -> list[float]:
    """Get indicator history list (for percentile calculations)."""
    raw = r.lrange(ind_hist_key(name, symbol, tf), 0, -1)
    return [b2f(x) for x in raw]


# =============================================================================
# Stream Operations (Invariants #27, #28)
# =============================================================================

def xadd_candle_closed(r, tf: str, symbol: str, bar_start_ts: int):
    """
    Add candle closed event to stream.

    Uses MAXLEN ~STREAM_MAXLEN for approximate trimming (invariant #27).
    Payload always includes symbol, bar_start_ts, as_of_ts (invariant #28).
    """
    r.xadd(
        stream_candle_closed_key(tf),
        {
            "symbol": symbol,
            "bar_start_ts": str(bar_start_ts),
            "as_of_ts": str(time.time()),
        },
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )


def xadd_indicators_ready(r, tf: str, symbol: str, bar_start_ts: int):
    """
    Add indicators ready event to stream.

    Uses MAXLEN ~STREAM_MAXLEN for approximate trimming (invariant #27).
    """
    r.xadd(
        stream_indicators_ready_key(tf),
        {
            "symbol": symbol,
            "bar_start_ts": str(bar_start_ts),
            "as_of_ts": str(time.time()),
        },
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )


def create_consumer_groups(r, timeframes: list[str]):
    """
    Create consumer groups for all streams idempotently.

    Invariant #31: Consumer groups must be created for ALL timeframe streams
    at startup. Missing any stream causes XREADGROUP to error.
    """
    for tf in timeframes:
        # Candle closed streams -> indicator_group
        try:
            r.xgroup_create(
                stream_candle_closed_key(tf),
                "indicator_group",
                id="0",
                mkstream=True,
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

        # Indicators ready streams -> signal_group
        try:
            r.xgroup_create(
                stream_indicators_ready_key(tf),
                "signal_group",
                id="0",
                mkstream=True,
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise


# =============================================================================
# Signal Operations (Invariants #4, #21, #26)
# =============================================================================

def store_signal(
    r,
    symbol: str,
    tf: str,
    setup_id: str,
    signal_payload: dict,
    bar_start_ts: int,
    setup_score: float,
):
    """
    Store signal with dual ZSET indexing.

    Uses transaction=True (MULTI/EXEC) for atomicity (invariant #21).
    Dual ZSETs (invariant #4): signals:score for ranking, signals:ts for pruning.
    """
    now = time.time()

    pipe = r.pipeline(transaction=True)
    pipe.set(sig_key(symbol, tf, setup_id), json.dumps(signal_payload), ex=SIGNAL_MAX_AGE_S)
    pipe.hset(sig_meta_key(symbol, tf, setup_id), mapping={
        "bar_start_ts": str(bar_start_ts),
        "as_of_ts": str(now),
    })
    pipe.expire(sig_meta_key(symbol, tf, setup_id), SIGNAL_MAX_AGE_S)
    pipe.zadd(signals_score_key(setup_id, tf), {symbol: setup_score})
    pipe.zadd(signals_ts_key(setup_id, tf), {symbol: now})
    pipe.execute()


# Lua script for atomic stale signal pruning (invariant #26)
PRUNE_STALE_SIGNALS_LUA = """
local stale = redis.call('ZRANGEBYSCORE', KEYS[1], 0, ARGV[1])
if #stale == 0 then return 0 end
redis.call('ZREM', KEYS[1], unpack(stale))
redis.call('ZREM', KEYS[2], unpack(stale))
for _, sym in ipairs(stale) do
    redis.call('DEL', 'sig:' .. sym .. ':' .. ARGV[2] .. ':' .. ARGV[3])
    redis.call('DEL', 'sig_meta:' .. sym .. ':' .. ARGV[2] .. ':' .. ARGV[3])
end
return #stale
"""


def prune_stale_signals(r, setup_id: str, tf: str) -> int:
    """
    Prune signals older than SIGNAL_MAX_AGE_S.

    Uses Lua script for atomic ZRANGEBYSCORE + ZREM + DELETE (invariant #26).
    Prevents race conditions when store_signal runs concurrently.

    Returns number of pruned signals.
    """
    cutoff = time.time() - SIGNAL_MAX_AGE_S
    ts_zset = signals_ts_key(setup_id, tf)
    score_zset = signals_score_key(setup_id, tf)

    return r.eval(
        PRUNE_STALE_SIGNALS_LUA,
        2,  # number of keys
        ts_zset,
        score_zset,
        str(cutoff),
        tf,
        setup_id,
    )


def get_top_signals(
    r,
    setup_ids: list[str],
    timeframes: list[str],
    top_n: int = 10,
) -> list[dict]:
    """
    Get top signals across setups and timeframes, sorted by score.

    Returns list of dicts with symbol, setup_id, tf, score, signal payload.
    """
    results = []

    for setup_id in setup_ids:
        for tf in timeframes:
            # Get top N by score (descending)
            members = r.zrevrange(
                signals_score_key(setup_id, tf),
                0,
                top_n - 1,
                withscores=True,
            )

            for symbol, score in members:
                symbol = b2s(symbol)
                payload_raw = r.get(sig_key(symbol, tf, setup_id))
                if payload_raw:
                    try:
                        payload = json.loads(b2s(payload_raw))
                        results.append({
                            "symbol": symbol,
                            "setup_id": setup_id,
                            "tf": tf,
                            "score": score,
                            "signal": payload,
                        })
                    except json.JSONDecodeError:
                        pass

    # Sort by score descending, take top N overall
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


# =============================================================================
# Cache State Operations
# =============================================================================

def is_cache_ready(r) -> bool:
    """Check if cache seeding is complete."""
    return b2s(r.get("cache:ready")) == "1"


def set_cache_ready(r, ready: bool = True):
    """Set cache ready flag."""
    r.set("cache:ready", "1" if ready else "0")


def check_staleness(r, symbol: str, tf: str) -> dict:
    """
    Check if candle data is stale.

    Returns dict with:
    - stale: bool
    - age_s: float (seconds since last update)
    """
    meta = r.hgetall(candle_meta_key(symbol, tf))
    if not meta:
        return {"stale": True, "age_s": float("inf")}

    age = time.time() - b2f(meta.get("as_of_ts", 0))
    threshold = TF_SECONDS.get(tf, 60) * STALENESS_MULTIPLIER

    return {"stale": age > threshold, "age_s": age}


def get_universe(r, promoted_only: bool = False) -> set[str]:
    """Get tracked symbols from universe sets."""
    if promoted_only:
        return {b2s(s) for s in r.smembers("universe:promoted")}
    return {b2s(s) for s in r.smembers("universe:monitored")}


def set_universe(r, symbols: list[str], promoted: Optional[list[str]] = None):
    """Set universe (monitored and promoted symbols)."""
    pipe = r.pipeline()
    pipe.delete("universe:monitored")
    if symbols:
        pipe.sadd("universe:monitored", *symbols)

    pipe.delete("universe:promoted")
    if promoted:
        pipe.sadd("universe:promoted", *promoted)
    elif symbols:
        # Default: top 50 are promoted
        pipe.sadd("universe:promoted", *symbols[:50])

    pipe.execute()


def get_timeframes(r) -> list[str]:
    """Get tracked timeframes."""
    return [b2s(t) for t in r.smembers("cache:timeframes")]


def set_timeframes(r, timeframes: list[str]):
    """Set tracked timeframes."""
    pipe = r.pipeline()
    pipe.delete("cache:timeframes")
    if timeframes:
        pipe.sadd("cache:timeframes", *timeframes)
    pipe.execute()


# =============================================================================
# Setup State Operations (Invariant #19 - TTL)
# =============================================================================

def get_setup_state(r, setup_id: str, symbol: str, tf: str) -> Optional[dict]:
    """Get setup state (S1State, S2State, etc.)."""
    raw = r.get(setup_state_key(setup_id, symbol, tf))
    if raw:
        try:
            return json.loads(b2s(raw))
        except json.JSONDecodeError:
            pass
    return None


def set_setup_state(r, setup_id: str, symbol: str, tf: str, state: dict):
    """Set setup state with TTL (invariant #19)."""
    r.set(
        setup_state_key(setup_id, symbol, tf),
        json.dumps(state),
        ex=SETUP_STATE_TTL_S,
    )


# =============================================================================
# Price Operations (Invariant #8)
# =============================================================================

def get_latest_price(r, symbol: str) -> Optional[dict]:
    """
    Get latest price from cache.

    Priority (invariant #8):
    1. candle_meta (authoritative, ingestor-owned)
    2. price:{symbol} (legacy, backward compat)

    Returns dict with price and timestamp, or None if not found/stale.
    """
    # 1. Authoritative: candle_meta
    meta = r.hgetall(candle_meta_key(symbol, "1m"))
    if meta:
        as_of = b2f(meta.get("as_of_ts", 0))
        if as_of > time.time() - 30:  # Fresh within 30s
            return {
                "price": b2f(meta.get("last_close")),
                "timestamp": as_of,
                "source": "candle_meta",
            }

    # 2. Legacy: price:{symbol}
    price_data = r.hgetall(f"price:{symbol}")
    if price_data and "price" in price_data:
        return {
            "price": b2f(price_data.get("price")),
            "timestamp": b2f(price_data.get("timestamp", 0)),
            "source": "legacy",
        }

    return None
