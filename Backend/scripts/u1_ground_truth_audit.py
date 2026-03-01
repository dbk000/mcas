# READ-ONLY audit. Does not modify database tables.
# U1 Ground Truth Audit — WS1 Missed Trades Autopsy
# Single-pass audit of all expired U1 v3.0 opportunities.
#
# Output: Backend/scripts/output/u1_ground_truth_audit.json + .md
#
# Usage:
#   tmux new -s u1-audit
#   cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/u1_ground_truth_audit.py

import json
import logging
import multiprocessing as mp
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
from scipy import stats as sp_stats

# ---------------------------------------------------------------------------
# Execution context detection (Docker vs host)
# ---------------------------------------------------------------------------
IN_DOCKER = os.path.isdir("/app")
if IN_DOCKER:
    SCRIPTS_DIR = Path("/scripts")
    sys.path.insert(0, "/app")
else:
    SCRIPTS_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(SCRIPTS_DIR.parent / "worker"))

OUTPUT_DIR = SCRIPTS_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("u1_audit")

# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading"
)

def get_conn():
    """Return a psycopg v3 connection with statement_timeout."""
    import psycopg
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '120s'")
    return conn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUDIT_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
AUDIT_END = datetime(2026, 2, 27, 23, 59, 59, tzinfo=timezone.utc)
FORWARD_WINDOW_HOURS = 168  # 7 days
PRE_ENTRY_HOURS = 72
SOCIAL_WINDOW_HOURS = 336  # ±14 days (widened from ±7d to avoid boundary artifact)
OHLCV_BATCH_SIZE = 50
OPP_PROGRESS_INTERVAL = 25
SYMBOL_PROGRESS_INTERVAL = 50
POOL_WORKERS = 12
INCREMENTAL_FLUSH_INTERVAL = 100

# Known contamination
KNOWN_STABLECOINS = {
    "USDCUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "RLUSDUSDT",
    "BFUSDUSDT", "USD1USDT", "USDEUSDT", "USTCUSDT", "FRAXUSDT",
    "XUSDUSDT", "UUSDT",
}
NON_CRYPTO_SYMBOLS = {"AVGO", "META", "NVDA", "QQQ", "XLK"}
GOLD_SYMBOLS = {"PAXGUSDT"}
FIAT_PATTERNS = re.compile(r"^(EUR|AEUR)")

# Whale alert channels for exclusion from headline social metrics
WHALE_CHANNELS = {"WhaleBotAlerts", "WhaleBotPumps", "whale_alert_io"}

# Content category mapping
CHANNEL_CATEGORIES = {
    "WhaleBotAlerts": "whale_alert",
    "WhaleBotPumps": "whale_alert",
    "whale_alert_io": "whale_alert",
    "Cointelegraph": "news",
    "Cryptopolitan": "news",
    "NewsBTC": "news",
    "Bitcoin World": "news",
    "U.Today": "news",
    "TimesTabloid": "news",
    "cryptonews": "news",
    "CryptoMoonShots": "community_hype",
    "CryptoMarkets": "community_hype",
    "CoinOtag": "community_hype",
    "solana": "technical",
}

# Whale transfer regex — direction + asset awareness
# "X TOKEN to exchange" is bearish for that TOKEN (selling), UNLESS the token is
# a stablecoin (USDT/USDC/etc) — depositing stablecoins to exchange = buying = bullish.
# "X TOKEN from exchange" is bullish (withdrawal), UNLESS stablecoin (withdrawing
# stablecoins = done buying = neutral/bearish).
WHALE_TO_EXCHANGE = re.compile(
    r"transfere?d from .+ to (Binance|Okex|OKX|Bybit|Coinbase|Kraken|Bitfinex)",
    re.IGNORECASE,
)
WHALE_FROM_EXCHANGE = re.compile(
    r"transfere?d from (Binance|Okex|OKX|Bybit|Coinbase|Kraken|Bitfinex) to",
    re.IGNORECASE,
)
# Stablecoins mentioned in whale alerts — polarity flips for these
WHALE_STABLECOIN_PATTERN = re.compile(
    r"\b(USDT|USDC|BUSD|DAI|TUSD|USDP|FDUSD)\b", re.IGNORECASE
)

# Stop calibration sweep
STOP_FLOORS = [0.005, 0.01, 0.02, 0.03, 0.05]
RANGE_MULTIPLIERS = [3, 5, 8, 12]

# Confusion matrix thresholds (fixed)
FIXED_THRESHOLDS = [0.03, 0.05, 0.10, 0.20, 0.50]


# ===================================================================
# Phase 0a: Symbol Exclusion (runtime-built)
# ===================================================================

def build_exclusion_set(opps):
    """Build exclusion set from opportunity data. Returns {symbol: reason}."""
    t0 = time.time()
    exclusions = {}

    # Collect all unique symbols
    symbols = {o["symbol"] for o in opps}
    # Build set of non-W symbols for wrapped detection
    non_w_symbols = {s for s in symbols if not s.startswith("W")}

    for sym in symbols:
        # 1. Known stablecoins
        if sym in KNOWN_STABLECOINS:
            exclusions[sym] = "stablecoin"
            continue
        # 2. Price-based stablecoin detection
        entry = _get_entry_price(opps, sym)
        if entry and 0.95 <= entry <= 1.05:
            exclusions[sym] = "stablecoin_price"
            continue
        # 3. Non-crypto (no USDT suffix)
        if not sym.endswith("USDT"):
            exclusions[sym] = "non_crypto"
            continue
        # 4. Wrapped tokens (W-prefix where non-W version exists)
        if sym.startswith("W"):
            base = sym[1:]  # WBTCUSDT -> BTCUSDT
            if base in non_w_symbols:
                exclusions[sym] = "wrapped"
                continue
        # 5. Fiat-pegged
        if FIAT_PATTERNS.match(sym):
            exclusions[sym] = "fiat_pegged"
            continue
        # 6. Gold-backed
        if sym in GOLD_SYMBOLS:
            exclusions[sym] = "gold_backed"
            continue

    elapsed = time.time() - t0
    reason_counts = defaultdict(int)
    for r in exclusions.values():
        reason_counts[r] += 1
    parts = ", ".join(f"{c} {r}" for r, c in sorted(reason_counts.items(), key=lambda x: -x[1]))
    log.info(f"[Phase 0a] Exclusions: {len(exclusions)} symbols flagged ({parts}) | {elapsed:.1f}s")
    return exclusions


def _get_entry_price(opps, symbol):
    """Get median entry price for a symbol from opps."""
    prices = []
    for o in opps:
        if o["symbol"] == symbol and o.get("_entry_price"):
            prices.append(o["_entry_price"])
    return float(np.median(prices)) if prices else None


# ===================================================================
# Phase 0b: Pump Threshold Derivation
# ===================================================================

def compute_pump_thresholds(ohlcv_data, exclusions, opps):
    """
    Compute empirical 24h max close-to-close return distribution
    for U1-relevant universe (75K-3M volume band, excluding stablecoins etc).
    Returns dict with percentiles and per-symbol return series.
    """
    t0 = time.time()

    # Identify U1-relevant symbols: in the opp set, not excluded,
    # AND in the volume band. We approximate volume from OHLCV.
    opp_symbols = {o["symbol"] for o in opps} - set(exclusions.keys())

    all_max_returns = []
    per_symbol_returns = {}
    per_period_returns = defaultdict(list)  # 2-week bucket -> returns
    symbols_used = 0
    wick_returns = []

    sorted_syms = sorted(ohlcv_data.keys())
    for i, sym in enumerate(sorted_syms):
        if sym in exclusions:
            continue
        data = ohlcv_data[sym]
        ts = data["ts"]
        close = data["close"]
        high = data["high"]
        low = data["low"]
        volume = data["volume"]

        if len(close) < 25:
            continue

        # Approximate 24h quote volume from 1h bars: sum(volume * close) over 24 bars
        # Check if symbol is in U1 volume band (75K-3M)
        if len(close) >= 24:
            qvol_24h_samples = []
            for start in range(0, len(close) - 23, 24):
                chunk_vol = np.sum(volume[start:start+24] * close[start:start+24])
                qvol_24h_samples.append(chunk_vol)
            median_qvol = np.median(qvol_24h_samples)
            if median_qvol < 75_000 or median_qvol > 3_000_000:
                continue

        # Rolling 24h max close-to-close return
        n = len(close)
        if n < 25:
            continue
        returns_24h = np.full(n - 24, np.nan)
        for j in range(24, n):
            window = close[j-24:j+1]
            base = close[j-24]
            if base > 0:
                max_ret = (np.max(window) / base - 1.0)
                returns_24h[j-24] = max_ret

        valid = returns_24h[~np.isnan(returns_24h)]
        if len(valid) < 10:
            continue

        all_max_returns.extend(valid.tolist())
        per_symbol_returns[sym] = valid
        symbols_used += 1

        # Per-2-week buckets
        for j in range(24, n):
            t_val = ts[j]
            if isinstance(t_val, (int, float)):
                dt = datetime.fromtimestamp(t_val, tz=timezone.utc)
            else:
                dt = t_val
            bucket = f"{dt.year}-W{dt.isocalendar()[1] // 2 * 2:02d}"
            if not np.isnan(returns_24h[j-24]):
                per_period_returns[bucket].append(returns_24h[j-24])

        # Wick-based diagnostic
        for j in range(24, n):
            window_high = high[j-24:j+1]
            window_low = low[j-24:j+1]
            min_low = np.min(window_low)
            if min_low > 0:
                wick_ret = np.max(window_high) / min_low - 1.0
                wick_returns.append(wick_ret)

        if (i + 1) % SYMBOL_PROGRESS_INTERVAL == 0:
            log.info(f"[Phase 0b] Calibration: {i+1}/{len(sorted_syms)} symbols | elapsed {time.time()-t0:.0f}s")

    arr = np.array(all_max_returns)
    percentiles = {
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
    }

    # Headline threshold = p95, floored at 5%
    headline = percentiles["p95"]
    if headline < 0.03:
        log.warning(f"[Phase 0b] p95={headline:.4f} < 3%, using 5% floor")
        headline = 0.05

    # Wick-based percentiles for diagnostic
    wick_arr = np.array(wick_returns) if wick_returns else np.array([0.0])
    wick_percentiles = {
        "p50": float(np.percentile(wick_arr, 50)),
        "p95": float(np.percentile(wick_arr, 95)),
    }

    # Per-2-week stability check
    period_p95s = {}
    for bucket, rets in sorted(per_period_returns.items()):
        if len(rets) >= 20:
            period_p95s[bucket] = float(np.percentile(rets, 95))
    p95_values = list(period_p95s.values())
    non_stationary = False
    if len(p95_values) >= 2:
        p95_var = np.var(p95_values)
        p95_mean = np.mean(p95_values)
        if p95_mean > 0 and p95_var / (p95_mean ** 2) > 2.0:
            non_stationary = True

    elapsed = time.time() - t0
    log.info(
        f"[Phase 0b] Calibration: p50={percentiles['p50']:.4f} p75={percentiles['p75']:.4f} "
        f"p90={percentiles['p90']:.4f} p95={percentiles['p95']:.4f} p99={percentiles['p99']:.4f} "
        f"(N={symbols_used} symbols, U1 volume band only) | {elapsed:.1f}s"
    )

    return {
        "percentiles": percentiles,
        "headline_threshold": headline,
        "wick_percentiles": wick_percentiles,
        "symbols_used": symbols_used,
        "total_observations": len(arr),
        "per_period_p95": period_p95s,
        "non_stationary": non_stationary,
        "per_symbol_returns": per_symbol_returns,
    }


# ===================================================================
# Data Loading Functions
# ===================================================================

def load_opportunities(conn):
    """Load all expired U1 opportunities."""
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, symbol, timeframe, setup_id, created_ts, score,
                   reason, payload_json, status, setup_version
            FROM opportunities
            WHERE setup_id = 'U1' AND status = 'expired'
            ORDER BY created_ts ASC
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    log.info(f"[Load] {len(rows)} expired U1 opportunities | {time.time()-t0:.1f}s")

    # Pre-parse payload_json and extract entry_price for exclusion detection
    for row in rows:
        payload = {}
        if row.get("payload_json"):
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        row["_payload"] = payload
        row["_entry_price"] = payload.get("entry_price")
    return rows


def load_ohlcv_batched(conn, symbols, timeframe="1h", start_ts=None, end_ts=None):
    """
    Load OHLCV for multiple symbols in batches. Returns:
    {symbol: {"ts": np.array, "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}}
    """
    if start_ts is None:
        start_ts = AUDIT_START - timedelta(days=3)
    if end_ts is None:
        end_ts = AUDIT_END + timedelta(days=8)

    result = {}
    sym_list = sorted(set(symbols))
    t0 = time.time()

    for batch_start in range(0, len(sym_list), OHLCV_BATCH_SIZE):
        batch = sym_list[batch_start:batch_start + OHLCV_BATCH_SIZE]
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM (
                    SELECT symbol, bar_start_ts, open, high, low, close, volume,
                           ROW_NUMBER() OVER (
                               PARTITION BY symbol, bar_start_ts
                               ORDER BY CASE source
                                   WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2
                               END
                           ) as rn
                    FROM ohlcv
                    WHERE symbol = ANY(%s) AND timeframe = %s
                      AND bar_start_ts >= %s AND bar_start_ts < %s
                      AND is_final = TRUE
                ) sub WHERE rn = 1
                ORDER BY symbol, bar_start_ts
            """, (batch, timeframe, start_ts, end_ts))
            rows = cur.fetchall()

        # Group by symbol
        sym_rows = defaultdict(list)
        for r in rows:
            sym_rows[r[0]].append(r[1:])  # (bar_start_ts, o, h, l, c, v)

        for sym, srows in sym_rows.items():
            srows.sort(key=lambda x: x[0])
            ts_arr = np.array([r[0].timestamp() if hasattr(r[0], 'timestamp') else float(r[0]) for r in srows])
            result[sym] = {
                "ts": ts_arr,
                "open": np.array([float(r[1]) for r in srows]),
                "high": np.array([float(r[2]) for r in srows]),
                "low": np.array([float(r[3]) for r in srows]),
                "close": np.array([float(r[4]) for r in srows]),
                "volume": np.array([float(r[5]) for r in srows]),
            }

        loaded = batch_start + len(batch)
        if loaded % SYMBOL_PROGRESS_INTERVAL == 0 or loaded == len(sym_list):
            log.info(f"[Load] OHLCV batch {loaded}/{len(sym_list)} symbols | elapsed {time.time()-t0:.0f}s")

    return result


def load_social_features(conn):
    """Bulk load social_features_1h for audit date range. Returns {symbol: [rows]}."""
    t0 = time.time()
    start = AUDIT_START - timedelta(days=15)   # ±14d window + 1d buffer
    end = AUDIT_END + timedelta(days=15)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol, bar_start_ts, mention_count, unique_authors,
                   platform_count, engagement_sum, mention_velocity,
                   mention_acceleration, author_novelty_ratio, first_mention,
                   mention_volume_ratio
            FROM social_features_1h
            WHERE bar_start_ts >= %s AND bar_start_ts < %s
            ORDER BY symbol, bar_start_ts
        """, (start, end))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

    social = defaultdict(list)
    for r in rows:
        d = dict(zip(cols, r))
        social[d["symbol"]].append(d)

    log.info(f"[Load] social_features_1h: {len(rows)} rows, {len(social)} symbols | {time.time()-t0:.1f}s")
    return dict(social)


def load_social_posts(conn):
    """Bulk load social_posts for audit date range. Returns {symbol: [posts]}."""
    t0 = time.time()
    start = AUDIT_START - timedelta(days=15)   # ±14d window + 1d buffer
    end = AUDIT_END + timedelta(days=15)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT channel, post_ts, ingested_ts, content, symbols
            FROM social_posts
            WHERE post_ts >= %s AND post_ts < %s
            ORDER BY post_ts
        """, (start, end))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

    posts_by_sym = defaultdict(list)
    for r in rows:
        d = dict(zip(cols, r))
        syms = d.get("symbols") or []
        for s in syms:
            posts_by_sym[s].append(d)

    log.info(f"[Load] social_posts: {len(rows)} posts, {len(posts_by_sym)} symbols | {time.time()-t0:.1f}s")
    return dict(posts_by_sym)


# ===================================================================
# Payload Parsing
# ===================================================================

def parse_payload(opp):
    """Extract structured fields from payload_json + parse why_items."""
    payload = opp.get("_payload", {})
    result = {}

    # Structured fields (directly available)
    # trigger_path may be None in payload even when present in why_items
    result["trigger_path"] = payload.get("trigger_path") or "unknown"
    result["compression_box_high"] = payload.get("compression_box_high")
    result["compression_box_low"] = payload.get("compression_box_low")
    result["confirmations_met"] = payload.get("confirmations_met", 0)
    result["entry_price"] = payload.get("entry_price")
    result["stop_price"] = payload.get("stop_price")
    result["tp1_price"] = payload.get("tp1_price")
    result["tp2_price"] = payload.get("tp2_price")
    result["r_value"] = payload.get("r_value")
    result["setup_score"] = payload.get("setup_score")
    result["rel_volume"] = payload.get("rel_volume")
    result["direction"] = payload.get("direction", "long")

    # Compression width
    if result["compression_box_high"] and result["compression_box_low"] and result["entry_price"]:
        try:
            width = (result["compression_box_high"] - result["compression_box_low"])
            result["compression_width_pct"] = width / result["entry_price"] * 100 if result["entry_price"] > 0 else None
        except (TypeError, ZeroDivisionError):
            result["compression_width_pct"] = None
    else:
        result["compression_width_pct"] = None

    # R-value percentage
    if result["entry_price"] and result["stop_price"] and result["entry_price"] > 0:
        result["r_value_pct"] = (result["entry_price"] - result["stop_price"]) / result["entry_price"] * 100
    else:
        result["r_value_pct"] = None

    # bar_start_ts — handle both ms int and ISO string
    raw_ts = payload.get("bar_start_ts")
    if raw_ts is not None:
        if isinstance(raw_ts, (int, float)):
            if raw_ts > 1e12:  # milliseconds
                result["signal_ts"] = raw_ts / 1000.0
            else:
                result["signal_ts"] = float(raw_ts)
        elif isinstance(raw_ts, str):
            try:
                dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                result["signal_ts"] = dt.timestamp()
            except (ValueError, TypeError):
                result["signal_ts"] = None
        else:
            result["signal_ts"] = None
    else:
        # Fall back to created_ts
        ct = opp.get("created_ts")
        if ct and hasattr(ct, "timestamp"):
            result["signal_ts"] = ct.timestamp()
        else:
            result["signal_ts"] = None

    # Parse why_items for fields not in structured payload
    why_items = payload.get("why_items", [])
    result["ignition_score"] = None
    result["ignition_threshold"] = None
    result["vol_z_score"] = None
    result["vol_z_min"] = None
    result["per_coin_score"] = None
    result["universe_temp"] = None
    result["_parse_errors"] = []

    for item in why_items:
        if not isinstance(item, str):
            continue
        # Trigger path: "Trigger path: score_based"
        m = re.search(r"Trigger path:\s*(\w+)", item)
        if m and result["trigger_path"] == "unknown":
            result["trigger_path"] = m.group(1)
            continue
        # Ignition score: "Ignition score: 0.672 (threshold: 0.4)"
        m = re.search(r"Ignition score:\s*([\d.]+)\s*\(threshold:\s*([\d.]+)\)", item)
        if m:
            result["ignition_score"] = float(m.group(1))
            result["ignition_threshold"] = float(m.group(2))
            continue
        # Vol z-score: "Vol z-score: 3.2 (min: 1.5)"
        m = re.search(r"Vol z-score:\s*([\d.]+)\s*\(min:\s*([\d.]+)\)", item)
        if m:
            result["vol_z_score"] = float(m.group(1))
            result["vol_z_min"] = float(m.group(2))
            continue
        # Per-coin score: "Per-coin score: 0.581, Universe temp: 0.243"
        m = re.search(r"Per-coin score:\s*([\d.]+),\s*Universe temp:\s*([\d.]+)", item)
        if m:
            result["per_coin_score"] = float(m.group(1))
            result["universe_temp"] = float(m.group(2))
            continue

    return result


# ===================================================================
# Phase 1+2: Per-Opportunity Worker (runs inside Pool)
# ===================================================================

# Module-level globals for Pool workers (set before fork, inherited via COW)
_WORKER_OHLCV = None
_WORKER_BTC = None
_WORKER_SOCIAL_FEATURES = None
_WORKER_SOCIAL_POSTS = None
_WORKER_EXCLUSIONS = None
_WORKER_THRESHOLDS = None


def _init_worker(ohlcv_data, btc_data, social_features, social_posts_data,
                 exclusions, thresholds):
    """Pool initializer — sets module-level refs to shared data (fork COW)."""
    global _WORKER_OHLCV, _WORKER_BTC, _WORKER_SOCIAL_FEATURES
    global _WORKER_SOCIAL_POSTS, _WORKER_EXCLUSIONS, _WORKER_THRESHOLDS
    _WORKER_OHLCV = ohlcv_data
    _WORKER_BTC = btc_data
    _WORKER_SOCIAL_FEATURES = social_features
    _WORKER_SOCIAL_POSTS = social_posts_data
    _WORKER_EXCLUSIONS = exclusions
    _WORKER_THRESHOLDS = thresholds


def process_opp(opp):
    """
    Process a single opportunity. Reads shared data from module-level globals
    (inherited via fork COW, not pickled). ZERO database calls.
    """
    ohlcv_data = _WORKER_OHLCV
    btc_data = _WORKER_BTC
    social_features = _WORKER_SOCIAL_FEATURES
    social_posts_data = _WORKER_SOCIAL_POSTS
    exclusions = _WORKER_EXCLUSIONS
    thresholds = _WORKER_THRESHOLDS

    sym = opp["symbol"]
    opp_id = opp["id"]
    parsed = parse_payload(opp)
    signal_ts = parsed["signal_ts"]

    result = {
        "opp_id": opp_id,
        "symbol": sym,
        "timeframe": opp["timeframe"],
        "created_ts": opp["created_ts"].isoformat() if hasattr(opp["created_ts"], "isoformat") else str(opp["created_ts"]),
        "score": opp.get("score"),
        "setup_version": opp.get("setup_version"),
    }
    result.update(parsed)

    # Exclusion flag
    if sym in exclusions:
        result["excluded"] = True
        result["exclusion_reason"] = exclusions[sym]
    else:
        result["excluded"] = False
        result["exclusion_reason"] = None

    if signal_ts is None:
        result["_error"] = "no_signal_ts"
        result.update(_empty_price_metrics())
        result.update(_empty_social_metrics())
        return result

    signal_dt = datetime.fromtimestamp(signal_ts, tz=timezone.utc)

    # -------------------------------------------------------------------
    # Phase 1a: Price action metrics
    # -------------------------------------------------------------------
    sym_ohlcv = ohlcv_data.get(sym)
    if sym_ohlcv is None or len(sym_ohlcv["ts"]) < 5:
        result["_error"] = "no_ohlcv"
        result.update(_empty_price_metrics())
        result.update(_empty_social_metrics())
        return result

    ts = sym_ohlcv["ts"]
    close = sym_ohlcv["close"]
    high = sym_ohlcv["high"]
    low = sym_ohlcv["low"]

    entry_price = parsed["entry_price"] or close[np.searchsorted(ts, signal_ts, side="right") - 1] if len(close) > 0 else None
    if entry_price is None or entry_price <= 0:
        result["_error"] = "no_entry_price"
        result.update(_empty_price_metrics())
        result.update(_empty_social_metrics())
        return result

    # Forward window: bars after signal_ts
    fwd_mask = ts > signal_ts
    fwd_ts = ts[fwd_mask]
    fwd_close = close[fwd_mask]
    fwd_high = high[fwd_mask]
    fwd_low = low[fwd_mask]

    # Limit to 7 days (168 1h bars)
    max_fwd_ts = signal_ts + FORWARD_WINDOW_HOURS * 3600
    time_mask = fwd_ts <= max_fwd_ts
    fwd_ts = fwd_ts[time_mask]
    fwd_close = fwd_close[time_mask]
    fwd_high = fwd_high[time_mask]
    fwd_low = fwd_low[time_mask]

    bars_received = len(fwd_close)
    result["bars_received"] = bars_received
    result["incomplete_data"] = bars_received < 134  # 80% of 168

    if bars_received == 0:
        result.update(_empty_price_metrics())
        result.update(_empty_social_metrics())
        return result

    # MFE / MAE calculations (close-to-close from entry)
    returns_from_entry = (fwd_close / entry_price) - 1.0

    def _mfe_at_horizon(hours):
        mask = fwd_ts <= (signal_ts + hours * 3600)
        if not np.any(mask):
            return 0.0
        return float(np.max(returns_from_entry[mask]))

    def _mae_at_horizon(hours):
        mask = fwd_ts <= (signal_ts + hours * 3600)
        if not np.any(mask):
            return 0.0
        return float(np.min(returns_from_entry[mask]))

    result["mfe_1h"] = _mfe_at_horizon(1)
    result["mfe_4h"] = _mfe_at_horizon(4)
    result["mfe_24h"] = _mfe_at_horizon(24)
    result["mfe_48h"] = _mfe_at_horizon(48)
    result["mfe_7d"] = _mfe_at_horizon(168)
    result["mae_24h"] = _mae_at_horizon(24)
    result["mae_7d"] = _mae_at_horizon(168)

    # Time to MFE (7d)
    mfe_7d_val = result["mfe_7d"]
    if mfe_7d_val > 0:
        mfe_idx = np.argmax(returns_from_entry)
        result["time_to_mfe_bars"] = int(mfe_idx + 1)
        # Pump duration: time until 50% MFE retracement
        peak_val = returns_from_entry[mfe_idx]
        retrace_level = peak_val * 0.5
        post_peak = returns_from_entry[mfe_idx:]
        retrace_mask = post_peak <= retrace_level
        if np.any(retrace_mask):
            result["pump_duration_bars"] = int(mfe_idx + np.argmax(retrace_mask))
        else:
            result["pump_duration_bars"] = bars_received
    else:
        result["time_to_mfe_bars"] = 0
        result["pump_duration_bars"] = 0

    # TP / Stop hit detection
    tp1 = parsed.get("tp1_price")
    tp2 = parsed.get("tp2_price")
    stop = parsed.get("stop_price")
    result["hit_tp1"] = bool(tp1 and np.any(fwd_high >= tp1))
    result["hit_tp2"] = bool(tp2 and np.any(fwd_high >= tp2))
    result["hit_stop"] = bool(stop and np.any(fwd_low <= stop))

    # Pre-entry 72h return (detect ALREADY_PUMPED)
    pre_mask = (ts >= (signal_ts - PRE_ENTRY_HOURS * 3600)) & (ts <= signal_ts)
    if np.any(pre_mask):
        pre_close = close[pre_mask]
        if len(pre_close) >= 2 and pre_close[0] > 0:
            result["pre_entry_72h_return"] = float(np.max(pre_close) / pre_close[0] - 1.0)
        else:
            result["pre_entry_72h_return"] = 0.0
    else:
        result["pre_entry_72h_return"] = 0.0

    # BTC context
    if btc_data and len(btc_data["ts"]) > 0:
        btc_ts = btc_data["ts"]
        btc_close = btc_data["close"]
        # BTC 7d return ending at signal time
        btc_mask_7d = (btc_ts >= (signal_ts - 7 * 24 * 3600)) & (btc_ts <= signal_ts)
        if np.any(btc_mask_7d):
            btc_c = btc_close[btc_mask_7d]
            result["btc_7d_return"] = float(btc_c[-1] / btc_c[0] - 1.0) if btc_c[0] > 0 else 0.0
        else:
            result["btc_7d_return"] = 0.0
        # BTC 24h return
        btc_mask_24h = (btc_ts >= (signal_ts - 24 * 3600)) & (btc_ts <= signal_ts)
        if np.any(btc_mask_24h):
            btc_c = btc_close[btc_mask_24h]
            result["btc_24h_return"] = float(btc_c[-1] / btc_c[0] - 1.0) if btc_c[0] > 0 else 0.0
        else:
            result["btc_24h_return"] = 0.0
    else:
        result["btc_7d_return"] = 0.0
        result["btc_24h_return"] = 0.0

    # -------------------------------------------------------------------
    # Phase 1c: Outcome + Failure Mode Classification
    # -------------------------------------------------------------------
    p = thresholds["percentiles"]
    mfe_24h = result["mfe_24h"]
    mfe_7d = result["mfe_7d"]
    mae_24h = result["mae_24h"]
    mae_7d = result["mae_7d"]

    # Multi-threshold hit flags (for confusion matrix table)
    all_thresholds = FIXED_THRESHOLDS + [p["p95"]]
    result["threshold_hits"] = {f"{t:.2f}": mfe_24h >= t for t in all_thresholds}

    # Outcome classification
    if result["excluded"]:
        result["outcome"] = "EXCLUDED"
    elif mfe_7d >= 2 * p["p95"]:
        result["outcome"] = "TP_STRONG"
    elif mfe_7d >= p["p95"]:
        result["outcome"] = "TP_MODERATE"
    elif mfe_7d >= p["p75"]:
        result["outcome"] = "TP_WEAK"
    elif mfe_7d >= p["p50"]:
        result["outcome"] = "MARGINAL"
    elif mae_7d < -p["p95"] and mfe_7d < p["p75"]:
        # Check ADVERSE before FALSE_POSITIVE
        result["outcome"] = "ADVERSE"
    else:
        result["outcome"] = "FALSE_POSITIVE"

    # Failure mode classification
    if result["excluded"]:
        result["failure_mode"] = "EXCLUDED_SYMBOL"
    elif mfe_24h >= p["p95"] and result["time_to_mfe_bars"] > 0:
        result["failure_mode"] = "CORRECT_SIGNAL"
    elif mfe_24h >= p["p95"] and tp2 and not result["hit_tp2"]:
        result["failure_mode"] = "BAD_EXIT_PARAMS"
    elif result["pre_entry_72h_return"] > 2 * p["p95"]:
        result["failure_mode"] = "ALREADY_PUMPED"
    else:
        # Check LATE_ENTRY: MFE from 6h pre-entry
        pre_6h_mask = (ts >= (signal_ts - 6 * 3600)) & (ts <= signal_ts)
        if np.any(pre_6h_mask):
            pre_6h_close = close[pre_6h_mask]
            combined = np.concatenate([pre_6h_close, fwd_close])
            if len(pre_6h_close) > 0 and pre_6h_close[0] > 0:
                mfe_from_pre6h = float(np.max(combined) / pre_6h_close[0] - 1.0)
            else:
                mfe_from_pre6h = 0.0
        else:
            mfe_from_pre6h = 0.0

        if mfe_24h < p["p75"] and mfe_from_pre6h >= p["p95"]:
            result["failure_mode"] = "LATE_ENTRY"
        elif mfe_24h >= p["p75"] and result["hit_stop"] and result["time_to_mfe_bars"] < 24:
            result["failure_mode"] = "DEAD_CAT_BOUNCE"
        elif mae_24h < -0.03 and mfe_24h < p["p50"]:
            # Check slow bleed: no single bar drop > 2%
            fwd_returns_bar = np.diff(fwd_close[:min(24, len(fwd_close))]) / fwd_close[:min(24, len(fwd_close))-1] if len(fwd_close) > 1 else np.array([])
            if len(fwd_returns_bar) > 0 and np.min(fwd_returns_bar) > -0.02:
                result["failure_mode"] = "SLOW_BLEED"
            elif mae_24h < -3 * max(mfe_24h, 0.001):
                result["failure_mode"] = "WRONG_DIRECTION"
            else:
                result["failure_mode"] = "NO_MOVE"
        elif mfe_24h < p["p50"]:
            result["failure_mode"] = "NO_MOVE"
        elif mae_24h < -3 * max(mfe_24h, 0.001):
            result["failure_mode"] = "WRONG_DIRECTION"
        else:
            result["failure_mode"] = "NO_MOVE"

    # Root cause classification (for FP/ADVERSE outcomes)
    result["root_cause"] = _classify_root_cause(result, parsed, p)

    # -------------------------------------------------------------------
    # Phase 2a: Social timing
    # -------------------------------------------------------------------
    result.update(_compute_social_metrics(
        sym, signal_ts, signal_dt, mfe_7d,
        social_features, social_posts_data, result
    ))

    return result


def _empty_price_metrics():
    return {
        "bars_received": 0, "incomplete_data": True,
        "mfe_1h": 0, "mfe_4h": 0, "mfe_24h": 0, "mfe_48h": 0, "mfe_7d": 0,
        "mae_24h": 0, "mae_7d": 0,
        "time_to_mfe_bars": 0, "pump_duration_bars": 0,
        "hit_tp1": False, "hit_tp2": False, "hit_stop": False,
        "pre_entry_72h_return": 0, "btc_7d_return": 0, "btc_24h_return": 0,
        "threshold_hits": {}, "outcome": "NO_DATA", "failure_mode": "NO_DATA",
        "root_cause": "NO_DATA",
    }


def _empty_social_metrics():
    return {
        "first_mention_ts": None, "social_lead_hours": None,
        "mentions_24h_pre": 0, "mentions_1h_pre": 0,
        "velocity_at_signal": None, "acceleration_at_signal": None,
        "engagement_24h_pre": 0, "author_novelty_at_signal": None,
        "mention_runway_hours": None,
        "social_timing_category": "no_social",
        "social_lead_hours_no_whale": None,
        "mentions_24h_pre_no_whale": 0,
        "velocity_at_signal_no_whale": None,
        "dominant_content_category": None,
        "content_diversity": 0,
        "whale_alert_present": False,
        "whale_bullish_count": 0, "whale_bearish_count": 0,
        "news_present": False,
        "post_count_by_category": {},
        "has_social_data": False,
        "backfill_pct": None,
    }


def _classify_root_cause(result, parsed, p):
    """Classify root cause for any outcome."""
    if result["excluded"]:
        return "EXCLUDED_SYMBOL"

    outcome = result["outcome"]
    if outcome in ("TP_STRONG", "TP_MODERATE"):
        return "CORRECT_SIGNAL"
    if outcome == "TP_WEAK":
        return "WEAK_SIGNAL"

    # For FP / ADVERSE / MARGINAL outcomes
    ign = parsed.get("ignition_score")
    conf = parsed.get("confirmations_met", 0)
    comp_w = parsed.get("compression_width_pct")
    vol_z = parsed.get("vol_z_score")
    mfe_24h = result["mfe_24h"]
    btc_7d = result.get("btc_7d_return", 0)

    if ign is not None and ign < 0.45 and conf <= 1:
        return "NOISE_TRIGGER"
    if comp_w is not None and comp_w < 0.3:
        return "COMPRESSION_ARTIFACT"
    if vol_z is not None and vol_z < 2.0 and mfe_24h < p["p50"]:
        return "LOW_VOL_Z"
    if btc_7d < -0.05:
        return "REGIME_MISMATCH"

    return "GENUINE_FP"


def _compute_social_metrics(sym, signal_ts, signal_dt, mfe_7d,
                            social_features, social_posts_data, result):
    """Compute all social metrics for one opportunity."""
    metrics = _empty_social_metrics()

    # Social features (±7 day window)
    sf_rows = social_features.get(sym, [])
    if not sf_rows:
        return metrics

    metrics["has_social_data"] = True

    # Convert to arrays for fast search
    sf_ts = np.array([
        r["bar_start_ts"].timestamp() if hasattr(r["bar_start_ts"], "timestamp")
        else float(r["bar_start_ts"])
        for r in sf_rows
    ])
    window_start = signal_ts - SOCIAL_WINDOW_HOURS * 3600
    window_end = signal_ts + SOCIAL_WINDOW_HOURS * 3600
    mask = (sf_ts >= window_start) & (sf_ts <= window_end)

    if not np.any(mask):
        return metrics

    window_rows = [sf_rows[i] for i in range(len(sf_rows)) if mask[i]]
    window_ts = sf_ts[mask]

    # First mention (earliest bar with mention_count > 0 before signal)
    pre_mask = window_ts <= signal_ts
    pre_rows = [window_rows[i] for i in range(len(window_rows)) if window_ts[i] <= signal_ts]
    pre_ts = window_ts[pre_mask] if np.any(pre_mask) else np.array([])

    mention_rows_pre = [(r, t) for r, t in zip(pre_rows, pre_ts) if r["mention_count"] and r["mention_count"] > 0]
    if mention_rows_pre:
        first_ts = mention_rows_pre[0][1]
        metrics["first_mention_ts"] = float(first_ts)
        metrics["social_lead_hours"] = (signal_ts - first_ts) / 3600.0

    # Mentions in 24h / 1h before signal
    pre_24h_mask = (sf_ts >= (signal_ts - 24 * 3600)) & (sf_ts <= signal_ts)
    pre_1h_mask = (sf_ts >= (signal_ts - 3600)) & (sf_ts <= signal_ts)
    metrics["mentions_24h_pre"] = int(sum(
        sf_rows[i]["mention_count"] or 0 for i in range(len(sf_rows)) if pre_24h_mask[i]
    ))
    metrics["mentions_1h_pre"] = int(sum(
        sf_rows[i]["mention_count"] or 0 for i in range(len(sf_rows)) if pre_1h_mask[i]
    ))

    # Velocity / acceleration / engagement at signal
    signal_idx = np.argmin(np.abs(sf_ts - signal_ts))
    closest = sf_rows[signal_idx]
    metrics["velocity_at_signal"] = closest.get("mention_velocity")
    metrics["acceleration_at_signal"] = closest.get("mention_acceleration")
    metrics["author_novelty_at_signal"] = closest.get("author_novelty_ratio")

    # Engagement 24h pre
    metrics["engagement_24h_pre"] = int(sum(
        sf_rows[i]["engagement_sum"] or 0 for i in range(len(sf_rows)) if pre_24h_mask[i]
    ))

    # Mention runway (first mention to MFE peak)
    if metrics["first_mention_ts"] and mfe_7d > 0 and result.get("time_to_mfe_bars", 0) > 0:
        mfe_ts = signal_ts + result["time_to_mfe_bars"] * 3600
        metrics["mention_runway_hours"] = (mfe_ts - metrics["first_mention_ts"]) / 3600.0

    # Social timing category (±48h focused)
    lead = metrics.get("social_lead_hours")
    if lead is None:
        metrics["social_timing_category"] = "no_social"
    elif lead > 6:
        metrics["social_timing_category"] = "social_precedes_6h+"
    elif lead > 1:
        metrics["social_timing_category"] = "social_precedes_1_6h"
    elif lead >= -1:
        metrics["social_timing_category"] = "social_concurrent"
    else:
        metrics["social_timing_category"] = "social_follows"

    # -------------------------------------------------------------------
    # Phase 2b: Content categorization from social_posts
    # -------------------------------------------------------------------
    posts = social_posts_data.get(sym, [])
    if posts:
        # Filter to ±48h window
        post_window = [
            p for p in posts
            if abs((p["post_ts"].timestamp() if hasattr(p["post_ts"], "timestamp") else float(p["post_ts"])) - signal_ts) <= 48 * 3600
        ]

        cat_counts = defaultdict(int)
        whale_bull = 0
        whale_bear = 0
        backfill_count = 0
        total_posts = len(post_window)

        # Non-whale features for headline metrics
        non_whale_first_ts = None
        non_whale_mentions_24h = 0

        for post in post_window:
            ch = post.get("channel", "") or ""
            cat = CHANNEL_CATEGORIES.get(ch)
            if cat is None:
                src = post.get("source", "") if "source" in post else ""
                if "reddit" in str(src).lower():
                    cat = "reddit"
                else:
                    cat = "other"
            cat_counts[cat] += 1

            # Whale polarity (asset-aware)
            # Token-to-exchange = bearish (selling), UNLESS stablecoin (depositing capital = bullish)
            # Token-from-exchange = bullish (withdrawal), UNLESS stablecoin (done buying = bearish)
            if cat == "whale_alert" and post.get("content"):
                content = post["content"]
                is_stablecoin_transfer = bool(WHALE_STABLECOIN_PATTERN.search(content))
                if WHALE_TO_EXCHANGE.search(content):
                    if is_stablecoin_transfer:
                        whale_bull += 1   # stablecoin TO exchange = buying
                    else:
                        whale_bear += 1   # token TO exchange = selling
                elif WHALE_FROM_EXCHANGE.search(content):
                    if is_stablecoin_transfer:
                        whale_bear += 1   # stablecoin FROM exchange = done buying
                    else:
                        whale_bull += 1   # token FROM exchange = accumulating

            # Backfill detection
            pt = post["post_ts"].timestamp() if hasattr(post["post_ts"], "timestamp") else float(post["post_ts"])
            it = post["ingested_ts"].timestamp() if hasattr(post["ingested_ts"], "timestamp") else float(post["ingested_ts"])
            if (it - pt) > 86400:  # > 24h lag
                backfill_count += 1

            # Non-whale first mention
            if cat != "whale_alert":
                if pt <= signal_ts:
                    if non_whale_first_ts is None or pt < non_whale_first_ts:
                        non_whale_first_ts = pt
                    if pt >= signal_ts - 24 * 3600:
                        non_whale_mentions_24h += 1

        # Category excluding whale_alert for dominant category
        non_whale_cats = {k: v for k, v in cat_counts.items() if k != "whale_alert"}
        if non_whale_cats:
            metrics["dominant_content_category"] = max(non_whale_cats, key=non_whale_cats.get)
        metrics["content_diversity"] = len([k for k in cat_counts if cat_counts[k] > 0])
        metrics["whale_alert_present"] = cat_counts.get("whale_alert", 0) > 0
        metrics["whale_bullish_count"] = whale_bull
        metrics["whale_bearish_count"] = whale_bear
        metrics["news_present"] = cat_counts.get("news", 0) > 0
        metrics["post_count_by_category"] = dict(cat_counts)

        # Whale-excluded social lead
        if non_whale_first_ts and non_whale_first_ts <= signal_ts:
            metrics["social_lead_hours_no_whale"] = (signal_ts - non_whale_first_ts) / 3600.0
        metrics["mentions_24h_pre_no_whale"] = non_whale_mentions_24h

        # Backfill artifact percentage
        if total_posts > 0:
            metrics["backfill_pct"] = backfill_count / total_posts

    return metrics


# ===================================================================
# Phase 1d: Stop Calibration Heatmap
# ===================================================================

def compute_stop_heatmap(opp_results, thresholds):
    """Sweep stop floors × range multipliers on TP_MODERATE+ opps."""
    # Filter to opps with meaningful moves
    tp_opps = [
        o for o in opp_results
        if o.get("outcome") in ("TP_STRONG", "TP_MODERATE", "TP_WEAK")
        and o.get("entry_price") and o.get("entry_price") > 0
        and o.get("mfe_7d", 0) > 0
    ]

    heatmap = {}
    for floor in STOP_FLOORS:
        for mult in RANGE_MULTIPLIERS:
            hits = 0
            total = 0
            pnl_1r_sum = 0
            pnl_2r_sum = 0
            for o in tp_opps:
                ep = o["entry_price"]
                # Compute stop from floor and range mult
                actual_r = max(floor * ep, o.get("r_value", 0))
                stop = ep - actual_r
                tp1 = ep + mult * actual_r
                tp2 = ep + 2 * mult * actual_r

                mfe_abs = o["mfe_7d"] * ep  # absolute MFE
                mae_abs = abs(o.get("mae_7d", 0)) * ep

                total += 1
                if mfe_abs >= (tp1 - ep):
                    hits += 1
                    pnl_1r_sum += (tp1 - ep) / ep
                elif mae_abs >= actual_r:
                    pnl_1r_sum -= actual_r / ep
                else:
                    pnl_1r_sum += o["mfe_7d"] * 0.5  # partial capture estimate

            hit_rate = hits / total if total > 0 else 0
            # avg_pnl already IS the EV per trade: it sums gains (TP hits),
            # losses (stop hits), and partial captures across all trades,
            # then divides by total. No further weighting by hit_rate needed.
            ev_per_trade = pnl_1r_sum / total * 100 if total > 0 else 0

            key = f"{floor:.3f}_{mult}"
            heatmap[key] = {
                "stop_floor_pct": floor * 100,
                "range_mult": mult,
                "n_opps": total,
                "hit_rate": hit_rate,
                "ev_per_trade_pct": ev_per_trade,
            }

    return heatmap


# ===================================================================
# Phase 3: Missed Pump Detection (False Negatives)
# ===================================================================

def detect_missed_pumps(ohlcv_data, exclusions, thresholds, opp_results):
    """Find all pumps in the period and cross-reference with U1 opps.
    Detects at the LOWEST threshold (min of FIXED_THRESHOLDS) so the
    multi-threshold confusion matrix can filter per-threshold correctly."""
    t0 = time.time()
    headline = thresholds["headline_threshold"]
    # Detect at lowest threshold so all pump sizes are captured
    detection_threshold = min(FIXED_THRESHOLDS)
    per_sym_returns = thresholds.get("per_symbol_returns", {})

    # Build lookup: symbol -> list of opp signal_ts
    opp_lookup = defaultdict(list)
    for o in opp_results:
        if o.get("signal_ts"):
            opp_lookup[o["symbol"]].append(o["signal_ts"])

    all_pumps = []

    for sym, ret_arr in per_sym_returns.items():
        if sym in exclusions:
            continue
        data = ohlcv_data.get(sym)
        if data is None:
            continue

        ts = data["ts"]
        close = data["close"]
        volume = data["volume"]
        n = len(close)
        if n < 25:
            continue

        # Find pump events (24h return >= detection_threshold for multi-threshold matrix)
        last_pump_ts = 0
        for j in range(24, n):
            base = close[j - 24]
            if base <= 0:
                continue
            max_close_in_window = np.max(close[j-24:j+1])
            ret = max_close_in_window / base - 1.0
            if ret >= detection_threshold:
                bar_ts = ts[j]
                # 48h cooldown dedup
                if bar_ts - last_pump_ts < 48 * 3600:
                    continue
                last_pump_ts = bar_ts

                # Find pump_start (first bar crossing 50% of peak return in the 24h window)
                pump_start_ts = bar_ts
                for k in range(j - 24, j + 1):
                    if close[k] / base - 1.0 >= ret * 0.5:
                        pump_start_ts = ts[k]
                        break

                # Pump peak
                peak_idx = j - 24 + np.argmax(close[j-24:j+1])
                pump_peak_ts = ts[peak_idx]
                peak_return = float(max_close_in_window / base - 1.0)

                # Multi-threshold hits
                t_hits = {f"{t:.2f}": ret >= t for t in FIXED_THRESHOLDS + [headline]}

                # Cross-reference with U1 opps
                opp_signal_times = opp_lookup.get(sym, [])
                caught_class = "MISSED"
                best_lead = None
                for ots in opp_signal_times:
                    lead_h = (pump_start_ts - ots) / 3600.0
                    if ots < pump_start_ts - 3600:
                        if best_lead is None or lead_h > (best_lead or 0):
                            caught_class = "CAUGHT_EARLY"
                            best_lead = lead_h
                    elif abs(ots - pump_start_ts) <= 3600:
                        if caught_class not in ("CAUGHT_EARLY",):
                            caught_class = "CAUGHT_CONCURRENT"
                            best_lead = lead_h
                    elif ots > pump_start_ts and ots < pump_peak_ts:
                        if caught_class not in ("CAUGHT_EARLY", "CAUGHT_CONCURRENT"):
                            caught_class = "CAUGHT_LATE"
                            best_lead = lead_h
                    elif ots >= pump_peak_ts and abs(ots - pump_peak_ts) < 24 * 3600:
                        if caught_class == "MISSED":
                            caught_class = "CAUGHT_AFTER_PEAK"
                            best_lead = lead_h

                # Volume analysis for misses
                miss_reason = None
                if caught_class == "MISSED":
                    # Quote volume pre-pump (24h before pump_start)
                    pre_pump_mask = (ts >= (pump_start_ts - 24 * 3600)) & (ts < pump_start_ts)
                    during_mask = (ts >= pump_start_ts) & (ts <= pump_peak_ts + 3600)

                    qvol_pre = float(np.sum(volume[pre_pump_mask] * close[pre_pump_mask])) if np.any(pre_pump_mask) else 0
                    qvol_during = float(np.sum(volume[during_mask] * close[during_mask])) if np.any(during_mask) else 0

                    in_band_pre = 75_000 <= qvol_pre <= 3_000_000
                    in_band_during = 75_000 <= qvol_during <= 3_000_000

                    if not in_band_pre and not in_band_during:
                        miss_reason = "not_in_volume_band_either"
                    elif not in_band_pre and in_band_during:
                        miss_reason = "reachable_during_pump"
                    else:
                        miss_reason = "in_universe_no_signal"

                all_pumps.append({
                    "symbol": sym,
                    "pump_start_ts": float(pump_start_ts),
                    "pump_peak_ts": float(pump_peak_ts),
                    "peak_return": peak_return,
                    "caught_class": caught_class,
                    "signal_lead_hours": best_lead,
                    "miss_reason": miss_reason,
                    "threshold_hits": t_hits,
                })

    # Sort by peak_return descending
    all_pumps.sort(key=lambda x: -x["peak_return"])

    caught_early = sum(1 for p in all_pumps if p["caught_class"] == "CAUGHT_EARLY")
    caught_conc = sum(1 for p in all_pumps if p["caught_class"] == "CAUGHT_CONCURRENT")
    caught_late = sum(1 for p in all_pumps if p["caught_class"] == "CAUGHT_LATE")
    caught_after = sum(1 for p in all_pumps if p["caught_class"] == "CAUGHT_AFTER_PEAK")
    missed = sum(1 for p in all_pumps if p["caught_class"] == "MISSED")

    log.info(
        f"[Phase 3] Cross-ref: {len(all_pumps)} pumps found, "
        f"{caught_early} CAUGHT_EARLY, {caught_conc} CAUGHT_CONCURRENT, "
        f"{caught_late} CAUGHT_LATE, {caught_after} CAUGHT_AFTER_PEAK, "
        f"{missed} MISSED | {time.time()-t0:.1f}s"
    )

    return all_pumps


def enrich_missed_pumps_social(pumps, social_features, social_posts_data, max_enrich=50):
    """For top missed pumps, check social presence before the pump."""
    missed = [p for p in pumps if p["caught_class"] == "MISSED" and p.get("miss_reason") == "in_universe_no_signal"]
    missed.sort(key=lambda x: -x["peak_return"])

    for pump in missed[:max_enrich]:
        sym = pump["symbol"]
        pump_start = pump["pump_start_ts"]

        sf_rows = social_features.get(sym, [])
        social_pre_pump = []
        for r in sf_rows:
            sf_ts = r["bar_start_ts"].timestamp() if hasattr(r["bar_start_ts"], "timestamp") else float(r["bar_start_ts"])
            if (pump_start - 48 * 3600) <= sf_ts <= pump_start:
                if r["mention_count"] and r["mention_count"] > 0:
                    social_pre_pump.append(r)

        pump["social_before_pump"] = len(social_pre_pump) > 0
        pump["social_mentions_48h_pre"] = sum(r["mention_count"] for r in social_pre_pump)

        # Content category of pre-pump posts
        posts = social_posts_data.get(sym, [])
        pre_posts = []
        for post in posts:
            pt = post["post_ts"].timestamp() if hasattr(post["post_ts"], "timestamp") else float(post["post_ts"])
            if (pump_start - 48 * 3600) <= pt <= pump_start:
                pre_posts.append(post)

        cats = defaultdict(int)
        for post in pre_posts:
            ch = post.get("channel", "") or ""
            cat = CHANNEL_CATEGORIES.get(ch, "other")
            cats[cat] += 1
        pump["pre_pump_content_categories"] = dict(cats)
        pump["social_lead_before_pump_h"] = None
        if social_pre_pump:
            earliest = min(
                r["bar_start_ts"].timestamp() if hasattr(r["bar_start_ts"], "timestamp") else float(r["bar_start_ts"])
                for r in social_pre_pump
            )
            pump["social_lead_before_pump_h"] = (pump_start - earliest) / 3600.0

    return pumps


# ===================================================================
# Phase 4: Report Generation
# ===================================================================

def generate_report(opp_results, thresholds, exclusions, pumps, stop_heatmap, social_features):
    """Generate the full markdown report."""
    t0 = time.time()
    log.info("[Phase 4] Generating 21-section report...")

    p = thresholds["percentiles"]
    headline = thresholds["headline_threshold"]
    non_excluded = [o for o in opp_results if not o.get("excluded")]
    excluded = [o for o in opp_results if o.get("excluded")]

    headline_key = f"{headline:.2f}"
    lines = []

    def h1(t): lines.append(f"\n# {t}\n")
    def h2(t): lines.append(f"\n## {t}\n")
    def h3(t): lines.append(f"\n### {t}\n")
    def p_line(t): lines.append(f"{t}\n")
    def blank(): lines.append("")

    h1("U1 Ground Truth Audit — WS1 Missed Trades Autopsy")
    p_line(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    p_line(f"Period: {AUDIT_START.strftime('%Y-%m-%d')} to {AUDIT_END.strftime('%Y-%m-%d')} ({(AUDIT_END - AUDIT_START).days} days)")
    p_line(f"Total opportunities: {len(opp_results)} ({len(non_excluded)} non-excluded, {len(excluded)} excluded)")

    # -------------------------------------------------------------------
    # 1. Executive Summary
    # -------------------------------------------------------------------
    h2("1. Executive Summary")

    # Compute headline precision/recall
    tp_count = sum(1 for o in non_excluded if o.get("mfe_24h", 0) >= headline)
    fp_count = len(non_excluded) - tp_count
    # Filter pumps to headline threshold for executive summary
    headline_key = f"{headline:.2f}"
    headline_pumps = [pp for pp in pumps if pp.get("threshold_hits", {}).get(headline_key, False)]
    caught_early_conc = sum(1 for pp in headline_pumps if pp["caught_class"] in ("CAUGHT_EARLY", "CAUGHT_CONCURRENT"))
    missed_count = sum(1 for pp in headline_pumps if pp["caught_class"] == "MISSED")
    fn_count = missed_count
    precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 0
    recall = caught_early_conc / (caught_early_conc + fn_count) if (caught_early_conc + fn_count) > 0 else 0

    # Dominant failure mode
    fm_counts = defaultdict(int)
    for o in non_excluded:
        fm_counts[o.get("failure_mode", "UNKNOWN")] += 1
    dominant_fm = max(fm_counts, key=fm_counts.get) if fm_counts else "UNKNOWN"

    # Social lead
    social_leads = [o["social_lead_hours"] for o in non_excluded if o.get("social_lead_hours") is not None and o["social_lead_hours"] > 0]
    median_social_lead = float(np.median(social_leads)) if social_leads else None

    p_line(f"**Precision** (at p95={headline:.1%} threshold): **{precision:.1%}** ({tp_count} TP / {tp_count + fp_count} signaled)")
    p_line(f"**Recall** (CAUGHT_EARLY + CAUGHT_CONCURRENT only): **{recall:.1%}** ({caught_early_conc} caught / {caught_early_conc + fn_count} total pumps)")
    p_line(f"**Dominant failure mode**: {dominant_fm} ({fm_counts.get(dominant_fm, 0)} opps, {fm_counts.get(dominant_fm, 0)/len(non_excluded)*100:.0f}%)")
    if median_social_lead is not None:
        p_line(f"**Social lead** (median, whale-excluded): {median_social_lead:.1f}h before signal")
    blank()
    p_line(f"**Verdict**: U1 v3.0 achieves {precision:.0%} precision at the data-derived p95 pump threshold "
           f"of {headline:.1%}. {fm_counts.get('NO_MOVE', 0) + fm_counts.get('FALSE_POSITIVE', 0)} of {len(non_excluded)} "
           f"non-excluded signals showed no meaningful move. Recall is {recall:.0%} — "
           f"{missed_count} pumps had no U1 signal within ±24h.")

    # -------------------------------------------------------------------
    # 2. Independence Diagnostics
    # -------------------------------------------------------------------
    h2("2. Independence Diagnostics")

    # Symbol concentration
    sym_counts = defaultdict(int)
    for o in non_excluded:
        sym_counts[o["symbol"]] += 1
    top10 = sorted(sym_counts.items(), key=lambda x: -x[1])[:10]
    top10_pct = sum(c for _, c in top10) / len(non_excluded) * 100 if non_excluded else 0

    p_line("**Top 10 symbols by opp count:**")
    p_line("| Symbol | Count | % |")
    p_line("|--------|-------|---|")
    for sym, cnt in top10:
        p_line(f"| {sym} | {cnt} | {cnt/len(non_excluded)*100:.1f}% |")
    blank()
    if top10_pct > 40:
        p_line(f"**WARNING**: Top 10 symbols account for {top10_pct:.0f}% of all opps — effective sample size may be much smaller than {len(non_excluded)}.")

    # Temporal clustering
    day_counts = defaultdict(int)
    for o in non_excluded:
        if o.get("signal_ts"):
            day = datetime.fromtimestamp(o["signal_ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            day_counts[day] += 1

    cluster_days = {d: c for d, c in day_counts.items() if c > 15}
    if cluster_days:
        p_line(f"\n**Cluster days** (>15 signals in 24h): {len(cluster_days)}")
        for d, c in sorted(cluster_days.items(), key=lambda x: -x[1])[:5]:
            p_line(f"  - {d}: {c} signals")

    # Effective sample size
    sym_weeks = set()
    for o in non_excluded:
        if o.get("signal_ts"):
            dt = datetime.fromtimestamp(o["signal_ts"], tz=timezone.utc)
            sym_weeks.add((o["symbol"], dt.isocalendar()[1]))
    p_line(f"\n**Effective sample size**: {len(sym_weeks)} unique (symbol, week) combinations")

    # Cluster-adjusted precision
    if cluster_days:
        non_cluster_opps = [o for o in non_excluded if o.get("signal_ts") and
                           datetime.fromtimestamp(o["signal_ts"], tz=timezone.utc).strftime("%Y-%m-%d") not in cluster_days]
        if non_cluster_opps:
            ntp = sum(1 for o in non_cluster_opps if o.get("mfe_24h", 0) >= headline)
            adj_prec = ntp / len(non_cluster_opps) if non_cluster_opps else 0
            p_line(f"**Cluster-adjusted precision**: {adj_prec:.1%} ({len(non_cluster_opps)} non-cluster opps)")

    # -------------------------------------------------------------------
    # 3. Calibration Results
    # -------------------------------------------------------------------
    h2("3. Calibration Results")
    p_line(f"24h close-to-close max return percentiles (U1 volume band 75K-3M only, {thresholds['symbols_used']} symbols):")
    for k, v in thresholds["percentiles"].items():
        p_line(f"  - **{k}**: {v:.2%}")
    p_line(f"\nWick-based diagnostic: p50={thresholds['wick_percentiles']['p50']:.2%}, p95={thresholds['wick_percentiles']['p95']:.2%}")
    p_line(f"Headline threshold (p95): **{headline:.2%}**")
    if thresholds["non_stationary"]:
        p_line("**FINDING — NON-STATIONARY THRESHOLD**: Per-2-week p95 coefficient of variation "
               "exceeds 2× mean. The p95 pump threshold varies significantly across sub-periods, "
               "suggesting regime-dependent thresholds would outperform a fixed threshold. "
               "**WS2 input**: implement regime-adaptive pump threshold (e.g., rolling 30-day p95).")
    else:
        p_line("Per-2-week p95 is stable (CV < 2× mean) — fixed threshold is appropriate.")
    blank()
    p_line("**Per-2-week p95 stability** (elevated from diagnostic to finding):")
    period_p95 = thresholds.get("per_period_p95", {})
    for bucket, val in sorted(period_p95.items()):
        p_line(f"  - {bucket}: {val:.2%}")
    if len(period_p95) >= 2:
        p95_vals = list(period_p95.values())
        p_line(f"\n  p95 range: {min(p95_vals):.2%} to {max(p95_vals):.2%} "
               f"(ratio: {max(p95_vals)/max(min(p95_vals), 0.001):.1f}×)")
        p_line(f"  p95 std: {np.std(p95_vals):.4f}, mean: {np.mean(p95_vals):.4f}")

    # -------------------------------------------------------------------
    # 4. Contamination Audit
    # -------------------------------------------------------------------
    h2("4. Contamination Audit")
    reason_groups = defaultdict(list)
    for o in excluded:
        reason_groups[o.get("exclusion_reason", "unknown")].append(o["symbol"])
    p_line(f"Total excluded: {len(excluded)} opps across {len(exclusions)} symbols")
    p_line("| Reason | Symbols | Opp Count |")
    p_line("|--------|---------|-----------|")
    for reason, syms in sorted(reason_groups.items()):
        unique_syms = sorted(set(syms))
        p_line(f"| {reason} | {', '.join(unique_syms[:5])}{'...' if len(unique_syms)>5 else ''} | {len(syms)} |")

    # -------------------------------------------------------------------
    # 5. Multi-threshold Confusion Matrix
    # -------------------------------------------------------------------
    h2("5. Multi-threshold Confusion Matrix")
    p_line("TP/FP = U1 signals whose MFE_24h did/didn't reach threshold. "
           "FN = Phase 3 pumps at that threshold with no timely U1 signal (MISSED). "
           "Recall uses Phase 3 CAUGHT_EARLY + CAUGHT_CONCURRENT as TP (consistent with headline).")
    blank()
    # Dedup by formatted key to avoid duplicate rows (e.g. 0.10 and 0.1012 both format to "0.10")
    seen_keys = set()
    all_thresh = []
    for t in sorted(FIXED_THRESHOLDS + [headline]):
        key = f"{t:.2f}"
        if key not in seen_keys:
            seen_keys.add(key)
            all_thresh.append(t)
    p_line("| Threshold | Signaled TP | Signaled FP | Caught (E+C) | Missed (FN) | Precision | Recall |")
    p_line("|-----------|-------------|-------------|--------------|-------------|-----------|--------|")
    for t in all_thresh:
        sig_tp = sum(1 for o in non_excluded if o.get("mfe_24h", 0) >= t)
        sig_fp = len(non_excluded) - sig_tp
        # FN/TP from Phase 3 pump detection at this threshold
        t_key = f"{t:.2f}"
        pumps_at_t = [pp for pp in pumps if pp.get("threshold_hits", {}).get(t_key, False)]
        caught_ec = sum(1 for pp in pumps_at_t if pp["caught_class"] in ("CAUGHT_EARLY", "CAUGHT_CONCURRENT"))
        fn = sum(1 for pp in pumps_at_t if pp["caught_class"] == "MISSED")
        prec = sig_tp / (sig_tp + sig_fp) if (sig_tp + sig_fp) > 0 else 0
        rec = caught_ec / (caught_ec + fn) if (caught_ec + fn) > 0 else 0
        label = f"{t:.0%}" + (" (p95)" if abs(t - headline) < 0.001 else "")
        p_line(f"| {label} | {sig_tp} | {sig_fp} | {caught_ec} | {fn} | {prec:.1%} | {rec:.1%} |")

    # -------------------------------------------------------------------
    # 6. Outcome Distribution
    # -------------------------------------------------------------------
    h2("6. Outcome Distribution")
    outcome_counts = defaultdict(int)
    for o in non_excluded:
        outcome_counts[o.get("outcome", "UNKNOWN")] += 1
    p_line("| Outcome | Count | % |")
    p_line("|---------|-------|---|")
    for outcome in ["TP_STRONG", "TP_MODERATE", "TP_WEAK", "MARGINAL", "FALSE_POSITIVE", "ADVERSE"]:
        c = outcome_counts.get(outcome, 0)
        pct = c / len(non_excluded) * 100 if non_excluded else 0
        p_line(f"| {outcome} | {c} | {pct:.1f}% |")

    # -------------------------------------------------------------------
    # 7. Failure Mode Table
    # -------------------------------------------------------------------
    h2("7. Failure Mode Distribution")
    p_line("| Failure Mode | Count | % | Example Symbols |")
    p_line("|-------------|-------|---|-----------------|")
    fm_examples = defaultdict(set)
    for o in non_excluded:
        fm_examples[o.get("failure_mode", "UNKNOWN")].add(o["symbol"])
    for fm in ["CORRECT_SIGNAL", "BAD_EXIT_PARAMS", "LATE_ENTRY", "ALREADY_PUMPED",
               "DEAD_CAT_BOUNCE", "SLOW_BLEED", "NO_MOVE", "WRONG_DIRECTION"]:
        c = fm_counts.get(fm, 0)
        pct = c / len(non_excluded) * 100 if non_excluded else 0
        examples = sorted(fm_examples.get(fm, set()))[:3]
        p_line(f"| {fm} | {c} | {pct:.1f}% | {', '.join(examples)} |")

    # -------------------------------------------------------------------
    # 8. Root Cause Table
    # -------------------------------------------------------------------
    h2("8. Root Cause Distribution")
    rc_counts = defaultdict(int)
    for o in non_excluded:
        rc_counts[o.get("root_cause", "UNKNOWN")] += 1
    p_line("| Root Cause | Count | % |")
    p_line("|-----------|-------|---|")
    for rc in ["CORRECT_SIGNAL", "WEAK_SIGNAL", "NOISE_TRIGGER", "COMPRESSION_ARTIFACT",
               "LOW_VOL_Z", "REGIME_MISMATCH", "GENUINE_FP"]:
        c = rc_counts.get(rc, 0)
        pct = c / len(non_excluded) * 100 if non_excluded else 0
        p_line(f"| {rc} | {c} | {pct:.1f}% |")

    # -------------------------------------------------------------------
    # 9. Trigger Path × Outcome Cross-tab
    # -------------------------------------------------------------------
    h2("9. Trigger Path × Outcome")
    paths = sorted(set(o.get("trigger_path", "unknown") for o in non_excluded))
    outcomes = ["TP_STRONG", "TP_MODERATE", "TP_WEAK", "MARGINAL", "FALSE_POSITIVE", "ADVERSE"]
    header = "| Trigger Path | " + " | ".join(outcomes) + " | Total |"
    p_line(header)
    p_line("|" + "---|" * (len(outcomes) + 2))
    for path in paths:
        path_opps = [o for o in non_excluded if o.get("trigger_path") == path]
        cells = [str(sum(1 for o in path_opps if o.get("outcome") == oc)) for oc in outcomes]
        p_line(f"| {path} | " + " | ".join(cells) + f" | {len(path_opps)} |")

    # -------------------------------------------------------------------
    # 10. Signal State Statistics
    # -------------------------------------------------------------------
    h2("10. Signal State Statistics")
    for field, label in [
        ("ignition_score", "Ignition Score"),
        ("vol_z_score", "Vol Z-Score"),
        ("per_coin_score", "Per-Coin Score"),
        ("universe_temp", "Universe Temp"),
        ("r_value_pct", "R-Value %"),
        ("setup_score", "Setup Score (confidence)"),
    ]:
        vals = [o[field] for o in non_excluded if o.get(field) is not None]
        if vals:
            arr = np.array(vals)
            p_line(f"**{label}**: N={len(vals)}, mean={np.mean(arr):.3f}, "
                   f"median={np.median(arr):.3f}, std={np.std(arr):.3f}, "
                   f"p25={np.percentile(arr,25):.3f}, p75={np.percentile(arr,75):.3f}")

    # Trigger path distribution
    blank()
    p_line("**Trigger path distribution:**")
    for path in paths:
        c = sum(1 for o in non_excluded if o.get("trigger_path") == path)
        p_line(f"  - {path}: {c} ({c/len(non_excluded)*100:.1f}%)")

    # Confirmations distribution
    conf_counts = defaultdict(int)
    for o in non_excluded:
        conf_counts[o.get("confirmations_met", "?")] += 1
    p_line("\n**Confirmations met:**")
    for k in sorted(conf_counts.keys()):
        p_line(f"  - {k}/3: {conf_counts[k]} opps")

    # -------------------------------------------------------------------
    # 11. Stop Calibration Heatmap
    # -------------------------------------------------------------------
    h2("11. Stop Calibration Heatmap")
    p_line("Hit rate = TP reached before stop, on TP_MODERATE+ opps. "
           "EV/trade = avg across all trades (wins + losses + partials).")
    p_line("")
    p_line("| Stop Floor | Range Mult | N | Hit Rate | EV/Trade % |")
    p_line("|-----------|-----------|---|----------|------------|")
    for key in sorted(stop_heatmap.keys()):
        h = stop_heatmap[key]
        p_line(f"| {h['stop_floor_pct']:.1f}% | {h['range_mult']}× | {h['n_opps']} | {h['hit_rate']:.1%} | {h.get('ev_per_trade_pct', 0):.2f}% |")

    # -------------------------------------------------------------------
    # 12. Social Correlation Matrix
    # -------------------------------------------------------------------
    h2("12. Social Correlation Matrix")
    p_line("**⚠️ BACKFILL ARTIFACT WARNING**: Social data was backfilled historically. "
           "`ingested_ts` for backfilled posts is weeks/months after `post_ts`. "
           "Any 'social precedes price' finding proves temporal correlation in historical data, "
           "NOT that a live system would have seen the signal in time. "
           "Findings are provisional pending live validation.**")
    blank()

    # Social timing × outcome cross-tab
    timing_cats = ["social_precedes_6h+", "social_precedes_1_6h", "social_concurrent", "social_follows", "no_social"]
    p_line("| Social Timing | " + " | ".join(outcomes) + " | Total | Precision |")
    p_line("|" + "---|" * (len(outcomes) + 3))
    for tc in timing_cats:
        tc_opps = [o for o in non_excluded if o.get("social_timing_category") == tc]
        cells = [str(sum(1 for o in tc_opps if o.get("outcome") == oc)) for oc in outcomes]
        tp_tc = sum(1 for o in tc_opps if o.get("mfe_24h", 0) >= headline)
        prec = tp_tc / len(tc_opps) if tc_opps else 0
        p_line(f"| {tc} | " + " | ".join(cells) + f" | {len(tc_opps)} | {prec:.1%} |")

    # -------------------------------------------------------------------
    # 13. Social Lead Cross-correlation
    # -------------------------------------------------------------------
    h2("13. Social Lead Cross-correlation")
    p_line("Top 40 events by MFE, lag -24h to +24h (1h steps).")
    top40 = sorted(non_excluded, key=lambda x: -x.get("mfe_7d", 0))[:40]
    top40_with_social = [o for o in top40 if o.get("has_social_data")]
    p_line(f"Events with social data: {len(top40_with_social)}/40")
    if top40_with_social:
        leads = [o["social_lead_hours"] for o in top40_with_social if o.get("social_lead_hours") is not None]
        if leads:
            p_line(f"Social lead distribution for top performers: median={np.median(leads):.1f}h, "
                   f"mean={np.mean(leads):.1f}h, min={np.min(leads):.1f}h, max={np.max(leads):.1f}h")

    # -------------------------------------------------------------------
    # 14. Mention Runway Distributions
    # -------------------------------------------------------------------
    h2("14. Mention Runway Distributions")
    leads_all = [o["social_lead_hours"] for o in non_excluded if o.get("social_lead_hours") is not None]
    if leads_all:
        arr = np.array(leads_all)
        p_line(f"**social_lead_hours**: N={len(arr)}, median={np.median(arr):.1f}h, mean={np.mean(arr):.1f}h")

        # Bimodality coefficient
        skew = float(sp_stats.skew(arr))
        kurt = float(sp_stats.kurtosis(arr, fisher=False))  # excess=False for Pearson kurtosis
        bc = (skew ** 2 + 1) / kurt if kurt > 0 else 0
        p_line(f"Bimodality coefficient: BC={bc:.3f} ({'suggests bimodality' if bc > 0.555 else 'unimodal'})")
        p_line(f"  skewness={skew:.3f}, kurtosis={kurt:.3f}")

        # KS test and Mann-Whitney: TP vs FP
        tp_leads = [o["social_lead_hours"] for o in non_excluded
                     if o.get("social_lead_hours") is not None and o.get("outcome") in ("TP_STRONG", "TP_MODERATE")]
        fp_leads = [o["social_lead_hours"] for o in non_excluded
                     if o.get("social_lead_hours") is not None and o.get("outcome") in ("FALSE_POSITIVE", "ADVERSE")]
        if len(tp_leads) >= 5 and len(fp_leads) >= 5:
            ks_stat, ks_p = sp_stats.ks_2samp(tp_leads, fp_leads)
            mw_stat, mw_p = sp_stats.mannwhitneyu(tp_leads, fp_leads, alternative='two-sided')
            p_line(f"\nTP vs FP social lead comparison:")
            p_line(f"  KS test: stat={ks_stat:.3f}, p={ks_p:.4f}")
            p_line(f"  Mann-Whitney U: stat={mw_stat:.0f}, p={mw_p:.4f}")
            p_line(f"  TP median lead: {np.median(tp_leads):.1f}h (N={len(tp_leads)})")
            p_line(f"  FP median lead: {np.median(fp_leads):.1f}h (N={len(fp_leads)})")

    # Mention runway
    runways = [o["mention_runway_hours"] for o in non_excluded if o.get("mention_runway_hours") is not None]
    if runways:
        p_line(f"\n**mention_runway_hours**: N={len(runways)}, median={np.median(runways):.1f}h, mean={np.mean(runways):.1f}h")

    # Histogram bins for social_lead_hours
    if leads_all:
        hist, edges = np.histogram(arr, bins=10)
        p_line("\nHistogram (social_lead_hours):")
        for i in range(len(hist)):
            p_line(f"  [{edges[i]:.0f}h, {edges[i+1]:.0f}h): {hist[i]}")

    # -------------------------------------------------------------------
    # 15. Content Category Analysis
    # -------------------------------------------------------------------
    h2("15. Content Category Analysis")
    cat_outcome = defaultdict(lambda: defaultdict(int))
    for o in non_excluded:
        dc = o.get("dominant_content_category")
        if dc:
            cat_outcome[dc][o.get("outcome", "UNKNOWN")] += 1

    all_cats = sorted(cat_outcome.keys())
    p_line("| Content Category | " + " | ".join(outcomes) + " | Total |")
    p_line("|" + "---|" * (len(outcomes) + 2))
    for cat in all_cats:
        cells = [str(cat_outcome[cat].get(oc, 0)) for oc in outcomes]
        total = sum(cat_outcome[cat].values())
        p_line(f"| {cat} | " + " | ".join(cells) + f" | {total} |")

    # Whale polarity
    whale_opps = [o for o in non_excluded if o.get("whale_alert_present")]
    if whale_opps:
        bull = sum(o.get("whale_bullish_count", 0) for o in whale_opps)
        bear = sum(o.get("whale_bearish_count", 0) for o in whale_opps)
        p_line(f"\n**Whale alert polarity** ({len(whale_opps)} opps with whale data): "
               f"{bull} bullish (from-exchange), {bear} bearish (to-exchange)")

    # -------------------------------------------------------------------
    # 16. BTC Regime Context
    # -------------------------------------------------------------------
    h2("16. BTC Regime Context")
    tp_btc = [o["btc_7d_return"] for o in non_excluded if o.get("outcome") in ("TP_STRONG", "TP_MODERATE") and o.get("btc_7d_return") is not None]
    fp_btc = [o["btc_7d_return"] for o in non_excluded if o.get("outcome") in ("FALSE_POSITIVE", "ADVERSE") and o.get("btc_7d_return") is not None]
    if tp_btc and fp_btc:
        p_line(f"BTC 7d return at signal time:")
        p_line(f"  TP: median={np.median(tp_btc):.2%}, mean={np.mean(tp_btc):.2%} (N={len(tp_btc)})")
        p_line(f"  FP: median={np.median(fp_btc):.2%}, mean={np.mean(fp_btc):.2%} (N={len(fp_btc)})")

        # One-parameter regime filter assessment
        bear_opps = [o for o in non_excluded if o.get("btc_7d_return", 0) < -0.05]
        if bear_opps:
            bear_tp = sum(1 for o in bear_opps if o.get("mfe_24h", 0) >= headline)
            p_line(f"\n**Regime filter assessment**: {len(bear_opps)} opps during BTC 7d < -5%, "
                   f"{bear_tp} TP ({bear_tp/len(bear_opps)*100:.0f}% precision)")
            p_line(f"  vs overall precision {precision:.1%} — "
                   f"{'filtering would help' if bear_tp/len(bear_opps) < precision * 0.5 else 'marginal benefit'}")

    # -------------------------------------------------------------------
    # 17. Top 20 Best Catches
    # -------------------------------------------------------------------
    h2("17. Top 20 Best Catches")
    best = sorted(non_excluded, key=lambda x: -x.get("mfe_7d", 0))[:20]
    p_line("| # | Symbol | MFE_24h | MFE_7d | Trigger | Ign Score | Social Lead | Content |")
    p_line("|---|--------|---------|--------|---------|-----------|-------------|---------|")
    for i, o in enumerate(best, 1):
        sl = f"{o.get('social_lead_hours', 0):.0f}h" if o.get('social_lead_hours') is not None else "—"
        p_line(f"| {i} | {o['symbol']} | {o.get('mfe_24h',0):.1%} | {o.get('mfe_7d',0):.1%} | "
               f"{o.get('trigger_path','?')} | {o.get('ignition_score','?')} | {sl} | {o.get('dominant_content_category','—')} |")

    # -------------------------------------------------------------------
    # 18. Top 20 Worst False Positives
    # -------------------------------------------------------------------
    h2("18. Top 20 Worst False Positives")
    worst = sorted(
        [o for o in non_excluded if o.get("outcome") in ("FALSE_POSITIVE", "ADVERSE")],
        key=lambda x: x.get("mae_7d", 0)
    )[:20]
    p_line("| # | Symbol | MAE_7d | MFE_24h | Root Cause | Ign Score | Vol Z |")
    p_line("|---|--------|--------|---------|------------|-----------|-------|")
    for i, o in enumerate(worst, 1):
        p_line(f"| {i} | {o['symbol']} | {o.get('mae_7d',0):.1%} | {o.get('mfe_24h',0):.1%} | "
               f"{o.get('root_cause','?')} | {o.get('ignition_score','?')} | {o.get('vol_z_score','?')} |")

    # -------------------------------------------------------------------
    # 19. Top 20 Missed Pumps
    # -------------------------------------------------------------------
    h2("19. Top 20 Missed Pumps")
    missed_pumps = [pp for pp in pumps
                     if pp["caught_class"] == "MISSED"
                     and pp.get("threshold_hits", {}).get(headline_key, False)][:20]
    p_line("| # | Symbol | Peak Return | Miss Reason | Social Pre-Pump | Social Lead |")
    p_line("|---|--------|-------------|-------------|-----------------|-------------|")
    for i, pp in enumerate(missed_pumps, 1):
        soc = "Yes" if pp.get("social_before_pump") else "No"
        lead = f"{pp.get('social_lead_before_pump_h', 0):.0f}h" if pp.get("social_lead_before_pump_h") else "—"
        p_line(f"| {i} | {pp['symbol']} | {pp['peak_return']:.1%} | {pp.get('miss_reason','?')} | {soc} | {lead} |")

    # -------------------------------------------------------------------
    # 20. Per-Opp Classification Table
    # -------------------------------------------------------------------
    h2("20. Per-Opportunity Classification Table")
    p_line("| ID | Symbol | Excl | Outcome | Failure Mode | Root Cause | MFE_24h | MAE_24h | Social Lead | Content | Ign Score | Trigger | BTC_7d | Bars |")
    p_line("|" + "---|" * 14)
    for o in opp_results:
        sl = f"{o.get('social_lead_hours',0):.0f}h" if o.get('social_lead_hours') is not None else "—"
        p_line(
            f"| {o['opp_id']} | {o['symbol']} | {'Y' if o.get('excluded') else ''} | "
            f"{o.get('outcome','?')} | {o.get('failure_mode','?')} | {o.get('root_cause','?')} | "
            f"{o.get('mfe_24h',0):.2%} | {o.get('mae_24h',0):.2%} | {sl} | "
            f"{o.get('dominant_content_category','—')} | {o.get('ignition_score','?')} | "
            f"{o.get('trigger_path','?')} | {o.get('btc_7d_return',0):.2%} | {o.get('bars_received',0)} |"
        )

    # -------------------------------------------------------------------
    # 21. SOP Change Proposals
    # -------------------------------------------------------------------
    h2("21. SOP Change Proposals")
    proposals = []

    # Stop floor — select by EV per trade (already the correct metric)
    if stop_heatmap:
        best_config = max(stop_heatmap.values(), key=lambda x: x.get("ev_per_trade_pct", 0))
        proposals.append(
            f"1. **Increase MIN_STOP_PCT** from 0.5% to {best_config['stop_floor_pct']:.1f}% "
            f"with {best_config['range_mult']}× range multiplier. "
            f"(Stop heatmap shows {best_config['hit_rate']:.0%} hit rate, "
            f"{best_config['ev_per_trade_pct']:.2f}% EV per trade on TP opps)"
        )

    nm_count = fm_counts.get("NO_MOVE", 0) + fm_counts.get("WRONG_DIRECTION", 0)
    if nm_count > len(non_excluded) * 0.3:
        proposals.append(
            f"2. **Tighten entry filters**: {nm_count} opps ({nm_count/len(non_excluded)*100:.0f}%) "
            f"were NO_MOVE or WRONG_DIRECTION. Root causes: "
            f"{rc_counts.get('NOISE_TRIGGER',0)} noise trigger, "
            f"{rc_counts.get('LOW_VOL_Z',0)} low vol_z, "
            f"{rc_counts.get('COMPRESSION_ARTIFACT',0)} compression artifact."
        )

    ap_count = fm_counts.get("ALREADY_PUMPED", 0)
    if ap_count > 5:
        proposals.append(
            f"3. **Add pre-entry momentum filter**: {ap_count} opps triggered on coins "
            f"that had already moved >{2*headline:.0%} in 72h. "
            f"Filter: reject if pre_entry_72h_return > 2× p95."
        )

    regime_count = rc_counts.get("REGIME_MISMATCH", 0)
    if regime_count > 5:
        proposals.append(
            f"4. **Add BTC regime filter**: {regime_count} FP/adverse opps occurred "
            f"during BTC 7d < -5%. Filtering these improves precision."
        )

    social_data_opps = [o for o in non_excluded if o.get("has_social_data")]
    soc_prec_tp = [o for o in social_data_opps if o.get("social_timing_category", "").startswith("social_precedes") and o.get("mfe_24h", 0) >= headline]
    nosoc_prec_tp = [o for o in non_excluded if o.get("social_timing_category") == "no_social" and o.get("mfe_24h", 0) >= headline]
    soc_total = sum(1 for o in non_excluded if o.get("social_timing_category", "").startswith("social_precedes"))
    nosoc_total = sum(1 for o in non_excluded if o.get("social_timing_category") == "no_social")
    if soc_total > 10 and nosoc_total > 10:
        soc_pr = len(soc_prec_tp) / soc_total
        nosoc_pr = len(nosoc_prec_tp) / nosoc_total
        proposals.append(
            f"5. **Social-adjusted confidence**: Precision when social precedes = {soc_pr:.1%} "
            f"(N={soc_total}) vs no social = {nosoc_pr:.1%} (N={nosoc_total}). "
            f"{'Social presence is a confirming signal.' if soc_pr > nosoc_pr * 1.3 else 'Social presence shows limited differentiation.'}"
        )

    # Missed pump social analysis
    in_universe_missed = [pp for pp in pumps if pp.get("miss_reason") == "in_universe_no_signal"]
    soc_missed = [pp for pp in in_universe_missed if pp.get("social_before_pump")]
    if in_universe_missed:
        proposals.append(
            f"6. **Social → universe integration**: {len(in_universe_missed)} missed pumps were "
            f"in the volume band with no U1 signal. Of these, {len(soc_missed)} "
            f"({len(soc_missed)/len(in_universe_missed)*100:.0f}%) had social mentions "
            f"before the pump. This quantifies the gap a social promotion gate would fill."
        )

    for pr in proposals:
        p_line(pr)

    # -------------------------------------------------------------------
    # WS2 Handoff
    # -------------------------------------------------------------------
    h2("WS2 Handoff Notes")
    p_line("For WS2 (Sentiment-Price Correlation Study):")
    p_line(f"- Feature importance: ignition_score, vol_z, social_lead_hours, btc_7d_return "
           f"are the primary candidates for multivariate analysis")
    p_line(f"- Recommended SPIKED threshold: {headline:.2%} (p95 from this audit)")
    p_line(f"- Pattern clusters: Check content category × outcome cross-tab (Section 15) "
           f"for category-specific prediction power")
    p_line(f"- Sample size caveat: {len(sym_weeks)} unique (symbol, week) — "
           f"{'sufficient' if len(sym_weeks) > 100 else 'may need augmentation'} for ML")

    report = "\n".join(lines)
    log.info(f"[Phase 4] Report generated ({len(lines)} lines) | {time.time()-t0:.1f}s")
    return report


# ===================================================================
# Correlation Cluster Detection (Phase 1c root cause)
# ===================================================================

def detect_correlation_clusters(opp_results):
    """Flag opps that are part of >3 signals within 2h (correlation clusters)."""
    sorted_opps = sorted(
        [o for o in opp_results if o.get("signal_ts")],
        key=lambda x: x["signal_ts"]
    )
    for i, o in enumerate(sorted_opps):
        ts = o["signal_ts"]
        nearby = sum(
            1 for other in sorted_opps
            if other["opp_id"] != o["opp_id"]
            and abs(other["signal_ts"] - ts) <= 7200  # 2h
        )
        if nearby > 3:
            if o.get("root_cause") == "GENUINE_FP":
                o["root_cause"] = "CORRELATION_CLUSTER"


# ===================================================================
# Time-of-day filter (Phase 1c root cause)
# ===================================================================

def detect_time_of_day_issues(opp_results):
    """Flag FP/ADVERSE signals during 00:00-06:00 UTC."""
    for o in opp_results:
        if o.get("signal_ts") and o.get("outcome") in ("FALSE_POSITIVE", "ADVERSE"):
            hour = datetime.fromtimestamp(o["signal_ts"], tz=timezone.utc).hour
            if 0 <= hour < 6 and o.get("root_cause") == "GENUINE_FP":
                o["root_cause"] = "TIME_OF_DAY"


# ===================================================================
# Main
# ===================================================================

def main():
    t_start = time.time()
    log.info("=" * 70)
    log.info("U1 Ground Truth Audit — WS1 Missed Trades Autopsy")
    log.info(f"Context: {'Docker' if IN_DOCKER else 'Host'}")
    log.info("=" * 70)

    # ------------------------------------------------------------------
    # Data Loading (single-threaded, pre-Pool)
    # ------------------------------------------------------------------
    conn = get_conn()

    log.info("[Load] Loading opportunities...")
    opps = load_opportunities(conn)
    if not opps:
        log.error("No expired U1 opportunities found. Exiting.")
        return

    # Collect all symbols needed
    opp_symbols = sorted(set(o["symbol"] for o in opps))
    log.info(f"[Load] {len(opp_symbols)} unique symbols across {len(opps)} opps")

    # Phase 0a: Exclusions
    exclusions = build_exclusion_set(opps)

    # Load OHLCV — covers Phase 0b (full period) + Phase 1 (+7d after latest opp)
    log.info("[Load] Loading 1h OHLCV for all symbols...")
    # We need OHLCV for all symbols that appear in opps PLUS any additional symbols
    # for Phase 3 (missed pump detection). For Phase 3 we need the full U1-universe,
    # but we can reuse what we load plus a broader pass later.
    # For now: load all opp symbols + extend window for pre-entry and forward lookout.
    ohlcv_start = AUDIT_START - timedelta(days=4)  # 72h pre-entry buffer
    ohlcv_end = AUDIT_END + timedelta(days=8)  # 7d forward buffer

    ohlcv_data = load_ohlcv_batched(conn, opp_symbols, "1h", ohlcv_start, ohlcv_end)
    log.info(f"[Load] OHLCV loaded: {len(ohlcv_data)} symbols with data")

    # BTC OHLCV
    btc_data = ohlcv_data.get("BTCUSDT")
    if btc_data is None:
        log.info("[Load] Loading BTC OHLCV separately...")
        btc_loaded = load_ohlcv_batched(conn, ["BTCUSDT"], "1h", ohlcv_start, ohlcv_end)
        btc_data = btc_loaded.get("BTCUSDT", {"ts": np.array([]), "close": np.array([])})

    # Social data
    log.info("[Load] Loading social_features_1h...")
    social_features = load_social_features(conn)

    log.info("[Load] Loading social_posts...")
    social_posts_data = load_social_posts(conn)

    conn.close()

    # ------------------------------------------------------------------
    # Phase 0b: Pump Threshold Derivation
    # ------------------------------------------------------------------
    log.info("[Phase 0b] Computing pump thresholds...")

    # For Phase 3, we also need OHLCV for symbols NOT in the opp set but in the universe.
    # To avoid a second massive load, Phase 3 will operate only on symbols we already have.
    # The plan says "reuses Phase 0b return series" — so we compute on loaded symbols.
    thresholds = compute_pump_thresholds(ohlcv_data, exclusions, opps)

    # ------------------------------------------------------------------
    # Phase 1+2: Per-Opportunity Processing (Pool)
    # ------------------------------------------------------------------
    log.info(f"[Phase 1-2] Processing {len(opps)} opportunities with Pool({POOL_WORKERS})...")
    t_p12 = time.time()

    # Data dicts are set as module-level globals before fork.
    # Pool workers inherit them via COW — no pickling.
    opp_results = []
    completed = 0
    with mp.Pool(
        POOL_WORKERS,
        initializer=_init_worker,
        initargs=(ohlcv_data, btc_data, social_features, social_posts_data,
                  exclusions, thresholds),
    ) as pool:
        for result in pool.imap_unordered(process_opp, opps, chunksize=4):
            opp_results.append(result)
            completed += 1
            if completed % OPP_PROGRESS_INTERVAL == 0:
                # Count outcomes so far
                oc = defaultdict(int)
                for r in opp_results:
                    oc[r.get("outcome", "?")] += 1
                tp_s = oc.get("TP_STRONG", 0)
                tp_m = oc.get("TP_MODERATE", 0)
                fp = oc.get("FALSE_POSITIVE", 0) + oc.get("ADVERSE", 0)
                log.info(
                    f"[Phase 1-2] {completed}/{len(opps)} opps | "
                    f"{tp_s} TP_strong, {tp_m} TP_mod, {fp} FP+adv | "
                    f"elapsed {time.time()-t_p12:.0f}s"
                )

            # Incremental flush
            if completed % INCREMENTAL_FLUSH_INTERVAL == 0:
                _flush_partial(opp_results)

    # Sort by opp_id for deterministic output
    opp_results.sort(key=lambda x: x.get("opp_id", 0))

    log.info(f"[Phase 1-2] Complete: {len(opp_results)} opps processed | {time.time()-t_p12:.1f}s")

    # Post-processing: correlation clusters and time-of-day
    detect_correlation_clusters(opp_results)
    detect_time_of_day_issues(opp_results)

    # ------------------------------------------------------------------
    # Phase 1d: Stop Calibration Heatmap
    # ------------------------------------------------------------------
    log.info("[Phase 1d] Computing stop calibration heatmap...")
    stop_heatmap = compute_stop_heatmap(opp_results, thresholds)

    # ------------------------------------------------------------------
    # Phase 3: Missed Pump Detection
    # ------------------------------------------------------------------
    log.info("[Phase 3] Detecting missed pumps...")
    pumps = detect_missed_pumps(ohlcv_data, exclusions, thresholds, opp_results)
    pumps = enrich_missed_pumps_social(pumps, social_features, social_posts_data)

    # ------------------------------------------------------------------
    # Phase 4: Report
    # ------------------------------------------------------------------
    report = generate_report(opp_results, thresholds, exclusions, pumps, stop_heatmap, social_features)

    # ------------------------------------------------------------------
    # Write outputs
    # ------------------------------------------------------------------
    json_path = OUTPUT_DIR / "u1_ground_truth_audit.json"
    md_path = OUTPUT_DIR / "u1_ground_truth_audit.md"

    # Prepare JSON-serializable output
    json_output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "audit_start": AUDIT_START.isoformat(),
            "audit_end": AUDIT_END.isoformat(),
            "total_opps": len(opp_results),
            "excluded_count": sum(1 for o in opp_results if o.get("excluded")),
            "elapsed_seconds": time.time() - t_start,
        },
        "thresholds": {
            "percentiles": thresholds["percentiles"],
            "headline_threshold": thresholds["headline_threshold"],
            "wick_percentiles": thresholds["wick_percentiles"],
            "symbols_used": thresholds["symbols_used"],
            "non_stationary": thresholds["non_stationary"],
            "per_period_p95": thresholds.get("per_period_p95", {}),
        },
        "exclusions": exclusions,
        "opp_results": _sanitize_for_json(opp_results),
        "stop_heatmap": stop_heatmap,
        "pumps_summary": {
            "total": len(pumps),
            "caught_early": sum(1 for p in pumps if p["caught_class"] == "CAUGHT_EARLY"),
            "caught_concurrent": sum(1 for p in pumps if p["caught_class"] == "CAUGHT_CONCURRENT"),
            "caught_late": sum(1 for p in pumps if p["caught_class"] == "CAUGHT_LATE"),
            "caught_after_peak": sum(1 for p in pumps if p["caught_class"] == "CAUGHT_AFTER_PEAK"),
            "missed": sum(1 for p in pumps if p["caught_class"] == "MISSED"),
        },
        "pumps_top50": _sanitize_for_json(pumps[:50]),
    }

    with open(json_path, "w") as f:
        json.dump(json_output, f, indent=2, default=str)
    log.info(f"[Output] JSON: {json_path} ({json_path.stat().st_size / 1024:.0f} KB)")

    with open(md_path, "w") as f:
        f.write(report)
    log.info(f"[Output] Markdown: {md_path} ({md_path.stat().st_size / 1024:.0f} KB)")

    elapsed = time.time() - t_start
    log.info(f"{'=' * 70}")
    log.info(f"U1 Ground Truth Audit complete in {elapsed:.1f}s")
    log.info(f"{'=' * 70}")


def _flush_partial(results):
    """Incremental flush of partial results to disk."""
    path = OUTPUT_DIR / "u1_ground_truth_audit_partial.json"
    try:
        with open(path, "w") as f:
            json.dump({"partial": True, "count": len(results),
                       "results": _sanitize_for_json(results[-INCREMENTAL_FLUSH_INTERVAL:])},
                      f, indent=2, default=str)
    except Exception:
        pass  # Non-critical


def _sanitize_for_json(obj):
    """Make objects JSON-serializable."""
    if isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


if __name__ == "__main__":
    main()
