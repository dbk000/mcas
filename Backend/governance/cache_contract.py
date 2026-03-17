"""
Cache Contract — Single source of truth for all Redis key patterns,
constants, serialization helpers, and atomic read/write operations.

All Redis access for candle ring buffers, indicators, signals, and
universe management MUST go through this module.

Design invariants (see plan for full list):
- #7:  RPUSH oldest→newest for ring buffers
- #13: No LRANGE outside this module — all candle reads via get_candles()
- #20: Stable partition via zlib.crc32() (not Python hash())
- #21: Transactional pipelines (MULTI/EXEC) for atomic writes
- #22: Mandatory m1/snap/m2 optimistic read-consistency
- #23: Dedupe gate on push_candle
- #24: b2s()/b2f() decode helpers — belt-and-braces for decode_responses=True
- #26: Lua script for atomic stale signal pruning
- #27: Approximate stream trimming via MAXLEN ~N
"""

import json
import time
import zlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

RING_BUFFER_SIZE = 600          # Warmup needs 201 + margin
INDICATOR_HISTORY_SIZE = 200    # For percentile calcs
STALENESS_MULTIPLIER = 2.5     # Stale if age > 2.5x candle interval
STREAM_MAXLEN = 200_000        # Per stream (approximate trim)
WS_MAX_STREAMS_PER_CONN = 200  # Binance safe limit per connection
SIGNAL_MAX_AGE_S = 3600        # 1h — prune signals older than this

# Pipeline activity ring buffer (public feed heartbeat)
PIPELINE_ACTIVITY_KEY = "pipeline:activity"
PIPELINE_ACTIVITY_MAX = 200
SETUP_STATE_TTL_S = 86400      # 24h — prevent stale state for delisted symbols
RECONCILE_INTERVAL_S = 60      # Reconciliation loop frequency

TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


# =============================================================================
# Decode helpers (invariant #24)
# =============================================================================

def b2s(val) -> str:
    """Decode Redis bytes/str safely. Primary guarantee is
    decode_responses=True on client; this is belt-and-braces."""
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


def b2f(val, default: float = 0.0) -> float:
    """Decode Redis bytes/str to float safely."""
    if val is None:
        return default
    try:
        if isinstance(val, bytes):
            return float(val.decode("utf-8"))
        return float(val)
    except (ValueError, TypeError):
        return default


# =============================================================================
# Key builders
# =============================================================================

def candle_key(symbol: str, tf: str) -> str:
    return f"candle:{symbol}:{tf}"


def candle_meta_key(symbol: str, tf: str) -> str:
    return f"candle_meta:{symbol}:{tf}"


def ind_key(symbol: str, tf: str, indicator_id: str) -> str:
    return f"ind:{symbol}:{tf}:{indicator_id}"


def ind_snap_key(symbol: str, tf: str) -> str:
    return f"ind_snap:{symbol}:{tf}"


def ind_meta_key(symbol: str, tf: str) -> str:
    return f"ind_meta:{symbol}:{tf}"


def ind_hist_key(name: str, symbol: str, tf: str) -> str:
    return f"ind_hist:{name}:{symbol}:{tf}"


def setup_state_key(setup_id: str, symbol: str, tf: str) -> str:
    return f"setup_state:{setup_id}:{symbol}:{tf}"


def sig_key(symbol: str, tf: str, setup_id: str) -> str:
    return f"sig:{symbol}:{tf}:{setup_id}"


def sig_meta_key(symbol: str, tf: str, setup_id: str) -> str:
    return f"sig_meta:{symbol}:{tf}:{setup_id}"


def score_zset_key(setup_id: str, tf: str) -> str:
    return f"signals:score:{setup_id}:{tf}"


def ts_zset_key(setup_id: str, tf: str) -> str:
    return f"signals:ts:{setup_id}:{tf}"


# =============================================================================
# Candle serialization
# =============================================================================

def serialize_candle(candle_dict: dict) -> str:
    """Serialize candle dict to JSON string for Redis LIST storage."""
    return json.dumps(candle_dict, separators=(",", ":"))


def deserialize_candle(s) -> dict:
    """Deserialize JSON string from Redis LIST to candle dict."""
    if isinstance(s, bytes):
        s = s.decode("utf-8")
    return json.loads(s)


def candle_to_ohlcv(candle_dict: dict):
    """Convert candle dict to OHLCV dataclass (lazy import to avoid circular)."""
    from indicators.indicators import OHLCV
    return OHLCV(
        timestamp=int(candle_dict["bar_start_ts"]),
        open=float(candle_dict["open"]),
        high=float(candle_dict["high"]),
        low=float(candle_dict["low"]),
        close=float(candle_dict["close"]),
        volume=float(candle_dict["volume"]),
    )


def ohlcv_to_candle_dict(ohlcv) -> dict:
    """Convert OHLCV dataclass to candle dict for Redis storage."""
    return {
        "bar_start_ts": ohlcv.timestamp,
        "open": ohlcv.open,
        "high": ohlcv.high,
        "low": ohlcv.low,
        "close": ohlcv.close,
        "volume": ohlcv.volume,
    }


# =============================================================================
# Ring buffer operations (invariants #7, #13, #23)
# =============================================================================

def push_candle(r, symbol: str, tf: str, candle_dict: dict):
    """Append candle to ring buffer. Oldest at index 0, newest at end.

    DEDUPE GATE (invariant #23): compares bar_start_ts against candle_meta.
    Skips if this bar is older-or-equal to last_bar_ts already in the buffer.
    Prevents reconnect backfill from pushing overlapping bars.

    Invariant #29: reads is_final from candle_dict["is_final"], not a
    separate parameter.
    """
    meta_k = candle_meta_key(symbol, tf)
    existing_ts = r.hget(meta_k, "last_bar_ts")
    if existing_ts is not None:
        if int(candle_dict["bar_start_ts"]) <= int(b2s(existing_ts)):
            return  # Skip duplicate / older bar

    key = candle_key(symbol, tf)
    r.rpush(key, serialize_candle(candle_dict))
    r.ltrim(key, -RING_BUFFER_SIZE, -1)  # Keep last 600
    r.hset(meta_k, mapping={
        "last_bar_ts": str(candle_dict["bar_start_ts"]),
        "last_close": str(candle_dict["close"]),
        "is_final": str(candle_dict.get("is_final", True)),
        "as_of_ts": str(time.time()),
        "count": str(r.llen(key)),
    })


def get_candles(r, symbol: str, tf: str, limit: int = RING_BUFFER_SIZE) -> list[dict]:
    """Read candles oldest->newest. THIS IS THE ONLY CANDLE READ PATH (invariant #13).

    Uses LRANGE(key, -limit, -1) which returns a contiguous tail slice
    preserving list order (oldest->newest).
    """
    key = candle_key(symbol, tf)
    raw = r.lrange(key, -limit, -1)
    return [deserialize_candle(s) for s in raw]


def get_candles_as_ohlcv(r, symbol: str, tf: str, limit: int = RING_BUFFER_SIZE):
    """Read candles as OHLCV dataclass instances."""
    dicts = get_candles(r, symbol, tf, limit)
    return [candle_to_ohlcv(d) for d in dicts]


def update_forming_candle(r, symbol: str, tf: str, candle_dict: dict):
    """Update the most recent (forming) candle in-place.

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
# Indicator snapshot serialization
# =============================================================================

# Fields from IndicatorSnapshot that we store in ind_snap HASH
# (excludes ohlcv which is stored separately in the ring buffer)
SNAPSHOT_FIELDS = [
    "atr", "clv", "volume_z", "rsi", "rsi_sma", "rsi_slope",
    "sma_50", "sma_200", "ema_9", "ema_20", "ema_21", "ema_50",
    "bb_mb", "bb_ub", "bb_lb", "bb_width",
    "kc_mb", "kc_ub", "kc_lb",
    "sma50_slope_atr", "sma200_slope_atr", "ma_gap_atr",
    "htf_close", "htf_sma50", "htf_bb_mb",
    # S3-S7 indicators
    "ema20_slope_atr", "ema50_slope_atr",
    "donchian_high_20", "donchian_low_20", "donchian_mid_20",
    "vwap", "vwap_upper_2sd", "vwap_lower_2sd", "vwap_distance_atr",
]

# Mapping from indicator_id to snapshot field name for per-indicator keys
INDICATOR_ID_TO_FIELD = {
    "ATR_14": "atr",
    "CLV": "clv",
    "VOLUME_Z_20": "volume_z",
    "RSI_14": "rsi",
    "RSI_SMA_14": "rsi_sma",
    "RSI_SLOPE": "rsi_slope",
    "SMA_50": "sma_50",
    "SMA_200": "sma_200",
    "EMA_9": "ema_9",
    "EMA_20": "ema_20",
    "EMA_21": "ema_21",
    "EMA_50": "ema_50",
    "BB_MB": "bb_mb",
    "BB_UB": "bb_ub",
    "BB_LB": "bb_lb",
    "BB_WIDTH": "bb_width",
    "KC_MB": "kc_mb",
    "KC_UB": "kc_ub",
    "KC_LB": "kc_lb",
    "SMA50_SLOPE_ATR": "sma50_slope_atr",
    "SMA200_SLOPE_ATR": "sma200_slope_atr",
    "MA_GAP_ATR": "ma_gap_atr",
    "HTF_CLOSE": "htf_close",
    "HTF_SMA50": "htf_sma50",
    "HTF_BB_MB": "htf_bb_mb",
    # S3-S7 indicators
    "EMA20_SLOPE_ATR": "ema20_slope_atr",
    "EMA50_SLOPE_ATR": "ema50_slope_atr",
    "DONCHIAN_HIGH_20": "donchian_high_20",
    "DONCHIAN_LOW_20": "donchian_low_20",
    "DONCHIAN_MID_20": "donchian_mid_20",
    "VWAP": "vwap",
    "VWAP_UPPER_2SD": "vwap_upper_2sd",
    "VWAP_LOWER_2SD": "vwap_lower_2sd",
    "VWAP_DISTANCE_ATR": "vwap_distance_atr",
}


def snapshot_to_indicator_dict(snapshot) -> dict:
    """Convert IndicatorSnapshot to {indicator_id: value} dict for per-key storage."""
    from dataclasses import asdict
    snap_dict = asdict(snapshot)
    result = {}
    for ind_id, field_name in INDICATOR_ID_TO_FIELD.items():
        val = snap_dict.get(field_name)
        if val is not None:
            result[ind_id] = val
    return result


def snapshot_to_flat_dict(snapshot) -> dict:
    """Convert IndicatorSnapshot to flat {field: str(value)} dict for HSET."""
    from dataclasses import asdict
    snap_dict = asdict(snapshot)
    result = {}
    for field_name in SNAPSHOT_FIELDS:
        val = snap_dict.get(field_name)
        if val is not None:
            result[field_name] = str(val)
    return result


def read_indicator_snapshot(r, symbol: str, tf: str):
    """Read ind_snap HASH and reconstruct an IndicatorSnapshot.

    Returns IndicatorSnapshot or None if no data.
    Requires candles to be passed separately (ohlcv field).
    """
    from indicators.indicators import IndicatorSnapshot
    snap_data = r.hgetall(ind_snap_key(symbol, tf))
    if not snap_data:
        return None

    kwargs = {}
    for field_name in SNAPSHOT_FIELDS:
        raw = snap_data.get(field_name)
        if raw is not None:
            val = b2f(raw)
            kwargs[field_name] = val
        elif field_name in ("htf_close", "htf_sma50", "htf_bb_mb",
                            "vwap", "vwap_upper_2sd", "vwap_lower_2sd",
                            "vwap_distance_atr"):
            kwargs[field_name] = None
        else:
            kwargs[field_name] = 0.0

    # ohlcv is not stored in ind_snap — set to empty list
    # Callers that need ohlcv should read candles separately
    kwargs["ohlcv"] = []

    return IndicatorSnapshot(**kwargs)


# =============================================================================
# Indicator storage (invariants #16, #21)
# =============================================================================

def store_indicators(r, symbol: str, tf: str, snapshot, bar_start_ts):
    """Write indicators with ind_meta as commit marker (written last).

    Uses transaction=True (MULTI/EXEC) so the entire batch is atomic.
    A concurrent reader either sees the old complete state or the new
    complete state — never a mix (invariant #21).
    """
    now = time.time()
    pipe = r.pipeline(transaction=True)  # MULTI/EXEC (invariant #21)

    # 1. Per-indicator keys
    for ind_id, value in snapshot_to_indicator_dict(snapshot).items():
        pipe.set(ind_key(symbol, tf, ind_id), json.dumps({
            "value": value,
            "bar_start_ts": bar_start_ts,
            "as_of_ts": now,
            "is_final": True,
        }))

    # 2. Bulk snapshot HASH
    pipe.hset(ind_snap_key(symbol, tf), mapping=snapshot_to_flat_dict(snapshot))

    # 3. History lists (for percentile calcs)
    bb_hist_k = ind_hist_key("bb_width", symbol, tf)
    pipe.rpush(bb_hist_k, str(snapshot.bb_width))
    pipe.ltrim(bb_hist_k, -INDICATOR_HISTORY_SIZE, -1)

    ma_hist_k = ind_hist_key("ma_gap", symbol, tf)
    pipe.rpush(ma_hist_k, str(snapshot.ma_gap_atr))
    pipe.ltrim(ma_hist_k, -INDICATOR_HISTORY_SIZE, -1)

    # VWAP distance history (for S4 extension percentile)
    if snapshot.vwap_distance_atr is not None:
        vwap_hist_k = ind_hist_key("vwap_dist", symbol, tf)
        pipe.rpush(vwap_hist_k, str(abs(snapshot.vwap_distance_atr)))
        pipe.ltrim(vwap_hist_k, -INDICATOR_HISTORY_SIZE, -1)

    # RSI history (for S3 divergence pivot detection)
    rsi_hist_k = ind_hist_key("rsi", symbol, tf)
    pipe.rpush(rsi_hist_k, str(snapshot.rsi))
    pipe.ltrim(rsi_hist_k, -INDICATOR_HISTORY_SIZE, -1)

    # 4. COMMIT MARKER — written LAST (invariant #16)
    pipe.hset(ind_meta_key(symbol, tf), mapping={
        "bar_start_ts": str(bar_start_ts),
        "as_of_ts": str(now),
    })

    pipe.execute()


# =============================================================================
# Optimistic read-consistency (invariant #22 — MANDATORY)
# =============================================================================

def read_fresh_snapshot(r, symbol: str, tf: str, expected_bar_start_ts=None):
    """Read snapshot with m1/snap/m2 consistency check.

    MANDATORY (invariant #22): reads ind_meta twice (before and after
    reading ind_snap). Only proceeds if bar_start_ts matches across
    both meta reads AND matches expected_bar_start_ts from the stream
    event. Rejects if any mismatch — prevents reading a half-written
    ind_snap mid-update and generating phantom signals.

    Returns (IndicatorSnapshot, bar_start_ts) or (None, None).
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


# =============================================================================
# Stream helpers (invariants #27, #28)
# =============================================================================

def xadd_candle_closed(r, tf: str, symbol: str, bar_start_ts):
    """Publish candle-closed event to stream.

    Invariant #28: bar_start_ts MUST be the bar identity for THAT
    specific timeframe (e.g., the 5m bar start for 5m events, NOT
    the 1m bar that triggered the aggregation).

    Payload always includes {symbol, bar_start_ts, as_of_ts} matching
    the contract table.
    """
    stream = f"stream:candle_closed:{tf}"
    r.xadd(stream, {
        "symbol": symbol,
        "bar_start_ts": str(bar_start_ts),
        "as_of_ts": str(time.time()),
    }, maxlen=STREAM_MAXLEN, approximate=True)


def xadd_indicators_ready(r, tf: str, symbol: str, bar_start_ts):
    """Publish indicators-ready event to stream.

    Payload always includes {symbol, bar_start_ts, as_of_ts} matching
    the contract table.
    """
    stream = f"stream:indicators_ready:{tf}"
    r.xadd(stream, {
        "symbol": symbol,
        "bar_start_ts": str(bar_start_ts),
        "as_of_ts": str(time.time()),
    }, maxlen=STREAM_MAXLEN, approximate=True)


def create_consumer_groups(r, timeframes: list[str]):
    """Create consumer groups for all timeframe streams (invariant #31).

    Idempotent: catches BUSYGROUP error if group already exists.
    Must be called for EVERY tf — missing any stream causes XREADGROUP
    to error on first run for that TF.
    """
    for tf in timeframes:
        for stream, group in [
            (f"stream:candle_closed:{tf}", "indicator_group"),
            (f"stream:indicators_ready:{tf}", "signal_group"),
        ]:
            try:
                r.xgroup_create(stream, group, id="0", mkstream=True)
                logger.info(f"Created consumer group {group} on {stream}")
            except Exception as e:
                if "BUSYGROUP" in str(e):
                    pass  # Already exists, fine
                else:
                    logger.error(f"Failed to create group {group} on {stream}: {e}")
                    raise


# =============================================================================
# Signal storage (invariants #4, #10, #21, #26)
# =============================================================================

# Lua script: atomic ZRANGEBYSCORE + ZREM + DELETE (invariant #26)
# Prevents race conditions when _store_signal() runs concurrently.
# KEYS[1] = ts_zset, KEYS[2] = score_zset
# ARGV[1] = cutoff timestamp, ARGV[2] = tf, ARGV[3] = setup_id
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


def store_signal(r, symbol: str, tf: str, setup_id: str,
                 signal_payload: dict, setup_score: float, bar_start_ts):
    """Store signal + meta + ZSETs atomically (invariant #21).

    Dual ZSETs (invariant #4):
    - signals:score:{setup_id}:{tf} — member=symbol, score=setup_score
    - signals:ts:{setup_id}:{tf}   — member=symbol, score=as_of_ts
    """
    now = time.time()
    s_key = sig_key(symbol, tf, setup_id)
    m_key = sig_meta_key(symbol, tf, setup_id)
    sc_zset = score_zset_key(setup_id, tf)
    t_zset = ts_zset_key(setup_id, tf)

    pipe = r.pipeline(transaction=True)  # MULTI/EXEC (invariant #21)
    pipe.set(s_key, json.dumps(signal_payload), ex=SIGNAL_MAX_AGE_S)
    pipe.hset(m_key, mapping={
        "bar_start_ts": str(bar_start_ts),
        "as_of_ts": str(now),
    })
    pipe.expire(m_key, SIGNAL_MAX_AGE_S)
    pipe.zadd(sc_zset, {symbol: setup_score})
    pipe.zadd(t_zset, {symbol: now})
    pipe.execute()


def prune_stale_signals(r, setup_id: str, tf: str):
    """Remove signals older than SIGNAL_MAX_AGE_S using Lua (invariant #26)."""
    cutoff = time.time() - SIGNAL_MAX_AGE_S
    t_zset = ts_zset_key(setup_id, tf)
    sc_zset = score_zset_key(setup_id, tf)
    removed = r.eval(PRUNE_STALE_SIGNALS_LUA, 2, t_zset, sc_zset,
                     str(cutoff), tf, setup_id)
    if removed and int(removed) > 0:
        logger.debug(f"Pruned {removed} stale signals for {setup_id}:{tf}")


def get_top_signals(r, setup_ids: list[str], timeframes: list[str],
                    top_n: int = 10) -> list[dict]:
    """Read top signals from Redis ZSETs across all setup_id/tf combos.

    Returns list of signal dicts sorted by score descending.
    """
    results = []
    seen_symbols = set()

    for setup_id in setup_ids:
        for tf in timeframes:
            sc_zset = score_zset_key(setup_id, tf)
            # ZREVRANGE returns highest scores first
            top = r.zrevrange(sc_zset, 0, top_n - 1, withscores=True)
            for member, score in top:
                symbol = b2s(member)
                if symbol in seen_symbols:
                    continue

                # Read the full signal payload
                s_key = sig_key(symbol, tf, setup_id)
                raw = r.get(s_key)
                if raw is None:
                    continue

                try:
                    payload = json.loads(b2s(raw))
                except (json.JSONDecodeError, TypeError):
                    continue

                # Read meta for freshness
                m_key = sig_meta_key(symbol, tf, setup_id)
                meta = r.hgetall(m_key)
                as_of_ts = b2f(meta.get("as_of_ts", 0)) if meta else 0

                results.append({
                    "symbol": symbol,
                    "timeframe": tf,
                    "setup_id": setup_id,
                    "setup_score": score,
                    "as_of_ts": as_of_ts,
                    "payload": payload,
                })
                seen_symbols.add(symbol)

    # Sort by score descending
    results.sort(key=lambda x: x["setup_score"], reverse=True)
    return results[:top_n]


# =============================================================================
# Staleness check
# =============================================================================

def check_staleness(r, symbol: str, tf: str) -> dict:
    """Check if candle data for symbol/tf is stale.

    Returns {"stale": bool, "age_s": float}.
    Uses b2f() for all Redis reads (invariant #24).
    """
    meta = r.hgetall(candle_meta_key(symbol, tf))
    if not meta:
        return {"stale": True, "age_s": float("inf")}
    age = time.time() - b2f(meta.get("as_of_ts", 0))
    threshold = TF_SECONDS.get(tf, 60) * STALENESS_MULTIPLIER
    return {"stale": age > threshold, "age_s": age}


def is_cache_ready(r) -> bool:
    """Check if the ingestor has finished seeding."""
    val = r.get("cache:ready")
    return b2s(val) == "1"


# =============================================================================
# Universe & partition (invariant #20)
# =============================================================================

def symbol_partition(symbol: str, n_partitions: int) -> int:
    """Stable partition hash via zlib.crc32() (invariant #20).

    NOT Python hash() which is randomized per-process via PYTHONHASHSEED.
    Ensures shard ownership is identical across process restarts and
    across different services (ingestor, indicator_worker, signal_worker).
    """
    # Normalize to str in case bytes leak through (invariant #30)
    s = b2s(symbol) if not isinstance(symbol, str) else symbol
    return zlib.crc32(s.encode("utf-8")) % n_partitions


def get_universe(r, key: str = "universe:monitored") -> list[str]:
    """Read universe set from Redis with decode safety (invariant #30)."""
    members = r.smembers(key)
    return [b2s(m) for m in members]


# ---------------------------------------------------------------------------
# Social Promotion Gate (disabled by default)
# ---------------------------------------------------------------------------
# ZSET of symbols nominated by Brain 1 for temporary monitored-universe
# promotion. Scored by unix timestamp for TTL pruning.
# Key is hardcoded in brain1.py (social_collector container) — cross-reference.
# See: Backend/social_collector/app/brain1.py (_publish_social_candidates)
SOCIAL_CANDIDATES_KEY = "universe:social_candidates"
SOCIAL_CANDIDATES_TTL_S = 86400  # 24h


def get_social_candidates(r, max_age_s: int = SOCIAL_CANDIDATES_TTL_S) -> list[str]:
    """Read current social promotion candidates from ZSET.

    Returns symbols added within max_age_s (default 24h).
    """
    import time
    cutoff = time.time() - max_age_s
    members = r.zrangebyscore(SOCIAL_CANDIDATES_KEY, cutoff, "+inf")
    return [b2s(m) for m in members]


def get_known_universe(r) -> list[str]:
    """Read Known Universe set from Redis (Canon §1.4C).

    Known Universe (~1024 symbols): all USDT pairs worth tracking.
    OHLCV ingested hourly via REST. Stored in Redis `universe:known` SET.

    Note: `universe:promoted` is deprecated, replaced by the two-tier
    known/monitored model. Use get_universe() for the Monitored Universe
    (symbols with WS streams + full compute pipeline).
    """
    return get_universe(r, key="universe:known")


def get_timeframes(r) -> list[str]:
    """Read tracked timeframes from Redis."""
    members = r.smembers("cache:timeframes")
    return [b2s(m) for m in members]


# =============================================================================
# Unicorn Pipeline Keys (U1 Setup)
# =============================================================================

# TTLs for Unicorn state
UNICORN_STATE_TTL_S = 86400 * 28  # 28 days (2× T_ACTIVE_MAX of 14 days)
UNICORN_METRICS_TTL_S = 3600     # 1 hour


def unicorn_universe_key() -> str:
    """SET of all symbols in embryo universe."""
    return "unicorn:embryo_universe"


def unicorn_active_key() -> str:
    """ZSET of EMBRYO_ACTIVE symbols, scored by embryo_active_score."""
    return "unicorn:embryo_active"


def unicorn_state_key(symbol: str) -> str:
    """HASH with all U1State fields for a symbol."""
    return f"unicorn:state:{symbol}"


def unicorn_metrics_key(symbol: str) -> str:
    """HASH with latest gate/trigger metrics for a symbol."""
    return f"unicorn:metrics:{symbol}"


def unicorn_pipeline_meta_key() -> str:
    """HASH with pipeline metadata (last_full_refresh, last_cycle, etc.)."""
    return "unicorn:pipeline_meta"


def store_unicorn_state(r, symbol: str, state_dict: dict):
    """Store U1 state with TTL refresh (pipeline worker calls this each cycle)."""
    key = unicorn_state_key(symbol)
    r.hset(key, mapping={k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                          for k, v in state_dict.items()})
    r.expire(key, UNICORN_STATE_TTL_S)  # Refresh TTL on each cycle


def read_unicorn_state(r, symbol: str) -> dict:
    """Read U1 state from Redis."""
    key = unicorn_state_key(symbol)
    raw = r.hgetall(key)
    if not raw:
        return {}
    result = {}
    for k, v in raw.items():
        # Try to parse JSON, fallback to string
        try:
            result[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            result[k] = b2s(v)
    return result


def store_unicorn_metrics(r, symbol: str, metrics: dict):
    """Store gate/trigger metrics with TTL."""
    key = unicorn_metrics_key(symbol)
    r.hset(key, mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v)
                          for k, v in metrics.items()})
    r.expire(key, UNICORN_METRICS_TTL_S)


def read_unicorn_metrics(r, symbol: str) -> dict:
    """Read gate/trigger metrics from Redis."""
    key = unicorn_metrics_key(symbol)
    raw = r.hgetall(key)
    if not raw:
        return {}
    result = {}
    for k, v in raw.items():
        try:
            result[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            result[k] = b2f(v) if v.replace('.', '').replace('-', '').isdigit() else b2s(v)
    return result


def get_embryo_active_symbols(r, limit: int = 300) -> list[tuple[str, float]]:
    """Get EMBRYO_ACTIVE symbols with scores (highest first)."""
    key = unicorn_active_key()
    return [(b2s(m), s) for m, s in r.zrevrange(key, 0, limit - 1, withscores=True)]


def add_to_embryo_active(r, symbol: str, score: float):
    """Add/update symbol in EMBRYO_ACTIVE ZSET."""
    r.zadd(unicorn_active_key(), {symbol: score})


def remove_from_embryo_active(r, symbol: str):
    """Remove symbol from EMBRYO_ACTIVE ZSET."""
    r.zrem(unicorn_active_key(), symbol)


def get_embryo_active_count(r) -> int:
    """Get current count of EMBRYO_ACTIVE symbols."""
    return r.zcard(unicorn_active_key())
