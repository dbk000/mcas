#!/usr/bin/env python3
"""
Signal Genesis — First-Principles Forward Return Prediction
============================================================

Engineer raw mathematical features from OHLCV, use information-theoretic
methods to discover what actually predicts forward returns, build simple
explainable models from survivors.

Usage:
    tmux new -s genesis
    docker compose run --rm -T worker python3 /scripts/signal_genesis.py --days 110
    # Quick smoke test:
    docker compose run --rm -T worker python3 /scripts/signal_genesis.py --days 30 --quick
"""

# ─── Standard library ────────────────────────────────────────────────────────

import os
import sys
import time
import json
import math
import argparse
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

# ─── Env + path setup ────────────────────────────────────────────────────────

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/exit_checker.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))

# ─── Third-party ──────────────────────────────────────────────────────────────

import numpy as np
import psycopg
from exit_checker import ExitConfig, check_exit_simple

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

WARMUP_BARS = 100          # longest lookback (89) + buffer
POSITION_SIZE_USD = 1000.0
FEE_BPS = 4.0
MIN_SHORT_PRICE = 1.00
BTC_SYMBOL = "BTCUSDT"
BASE_SLIPPAGE_1H = 5.0     # bps for 1h timeframe
MIN_BARS_PER_SYMBOL = 100

HORIZONS = [4, 8, 24]
HORIZON_CONFIGS = {
    4:  {"stop_atr_mult": 1.0, "time_stop_bars": 6,  "trail_activation_r": 0.75, "trail_distance_r": 0.3, "trail_breakeven_r": 0.5},
    8:  {"stop_atr_mult": 1.5, "time_stop_bars": 12, "trail_activation_r": 1.0,  "trail_distance_r": 0.5, "trail_breakeven_r": 0.75},
    24: {"stop_atr_mult": 2.0, "time_stop_bars": 36, "trail_activation_r": 1.5,  "trail_distance_r": 1.0, "trail_breakeven_r": 0.0},
}

# Feature selection thresholds
IC_TSTAT_THRESHOLD = 2.0
MI_SIGNIFICANCE_LEVEL = 0.05
STABILITY_TSTAT_THRESHOLD = 1.5
STABILITY_MIN_WINDOWS = 3  # out of 4
REDUNDANCY_CORR_THRESHOLD = 0.8
TOP_K_CANDIDATES = [2, 5, 10, 15, 20]  # percentile thresholds to optimize

# Walk-forward config
N_FOLDS = 4
EMBARGO_DAYS = 1
HOLDOUT_DAYS = 18
MIN_TRADES_TOTAL = 25
MIN_FOLD_CONSISTENCY = 0.75  # 3/4 folds


# ─── Binance API ──────────────────────────────────────────────────────────────

BINANCE_BASE = "https://api.binance.com/api/v3"

# Timeframe → milliseconds per bar
TF_MS = {
    "5m":  5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h":  60 * 60 * 1000,
    "4h":  4 * 60 * 60 * 1000,
}


def backfill_ohlcv(database_url, days, timeframes, n_symbols=600):
    """Fetch historical OHLCV from Binance REST API and insert into Postgres.

    Fetches top n_symbols USDT pairs by 24h quote volume, then for each
    symbol × timeframe, paginates through klines API (1000 bars/request).
    Uses ON CONFLICT DO NOTHING to avoid overwriting existing data.
    """
    import requests

    t0 = time.time()
    now_ms = int(time.time() * 1000)

    # ── Step 1: Get top USDT symbols by volume ──
    logger.info("Backfill: fetching top %d USDT symbols by volume...", n_symbols)
    try:
        resp = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=15)
        resp.raise_for_status()
        tickers = resp.json()
    except Exception as e:
        logger.error("Failed to fetch ticker data: %s", e)
        return False

    usdt_pairs = [
        (t["symbol"], float(t["quoteVolume"]))
        for t in tickers
        if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > 0
    ]
    usdt_pairs.sort(key=lambda x: -x[1])
    symbols = [s for s, _ in usdt_pairs[:n_symbols]]

    # Always include BTC
    if BTC_SYMBOL not in symbols:
        symbols.append(BTC_SYMBOL)

    logger.info("  %d symbols selected (top volume: %s ... %s)",
                len(symbols), symbols[0], symbols[min(9, len(symbols)-1)])

    # ── Step 2: For each timeframe, fetch and insert ──
    batch_id = f"genesis_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    with psycopg.connect(database_url) as conn:
        for tf in timeframes:
            ms_per_bar = TF_MS.get(tf)
            if not ms_per_bar:
                logger.warning("  Unknown timeframe %s, skipping", tf)
                continue

            # How far back: days + 15 extra for warmup
            fetch_days = days + 15
            start_ms = now_ms - fetch_days * 24 * 60 * 60 * 1000
            bars_needed = int(fetch_days * 24 * 60 * 60 * 1000 / ms_per_bar)

            logger.info("  Timeframe %s: %d symbols × %d bars (~%d requests)",
                        tf, len(symbols), bars_needed,
                        len(symbols) * max(1, bars_needed // 1000))

            total_inserted = 0
            tf_t0 = time.time()

            for s_idx, symbol in enumerate(symbols):
                # Paginate: Binance returns max 1000 bars per request
                cursor_ms = start_ms
                sym_bars = []

                while cursor_ms < now_ms:
                    params = {
                        "symbol": symbol,
                        "interval": tf,
                        "startTime": cursor_ms,
                        "endTime": now_ms,
                        "limit": 1000,
                    }

                    try:
                        resp = requests.get(f"{BINANCE_BASE}/klines", params=params, timeout=30)
                        if resp.status_code in (418, 429):
                            # Rate limited — back off
                            retry_after = int(resp.headers.get("Retry-After", 10))
                            logger.warning("  Rate limited, sleeping %ds...", retry_after)
                            time.sleep(retry_after)
                            continue
                        resp.raise_for_status()
                        data = resp.json()
                    except requests.exceptions.RequestException as e:
                        logger.warning("  Failed %s %s: %s", symbol, tf, e)
                        time.sleep(1)
                        break

                    if not data:
                        break

                    for k in data:
                        bar_ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
                        sym_bars.append((
                            symbol, tf, bar_ts,
                            float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
                            datetime.now(timezone.utc), True, "binance", batch_id,
                        ))

                    # Advance cursor past the last bar
                    last_ts_ms = int(data[-1][0])
                    if last_ts_ms <= cursor_ms:
                        break  # no progress
                    cursor_ms = last_ts_ms + ms_per_bar

                    # Small rate limit safety
                    if len(data) == 1000:
                        time.sleep(0.05)

                # Batch insert for this symbol
                if sym_bars:
                    with conn.cursor() as cur:
                        cur.executemany("""
                            INSERT INTO ohlcv (
                                symbol, timeframe, bar_start_ts,
                                open, high, low, close, volume,
                                as_of_ts, is_final, source, batch_id
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (symbol, timeframe, bar_start_ts, source)
                            DO NOTHING
                        """, sym_bars)
                    conn.commit()
                    total_inserted += len(sym_bars)

                # Progress log every 25 symbols
                if (s_idx + 1) % 25 == 0 or s_idx == len(symbols) - 1:
                    elapsed = time.time() - tf_t0
                    rate = (s_idx + 1) / elapsed if elapsed > 0 else 0
                    eta = (len(symbols) - s_idx - 1) / rate if rate > 0 else 0
                    logger.info("    %s: %d/%d symbols (%.1f/s), %d bars inserted, ETA %.0fs",
                                tf, s_idx + 1, len(symbols), rate, total_inserted, eta)

            logger.info("  %s complete: %d total bars in %.1fs",
                        tf, total_inserted, time.time() - tf_t0)

    elapsed = time.time() - t0
    logger.info("Backfill complete in %.1f min", elapsed / 60)
    return True


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol: str
    direction: str
    ts_idx: int
    score: float


@dataclass
class TradeResult:
    symbol: str
    direction: str
    ts_idx: int
    entry_price: float
    exit_price: float
    pnl: float
    r_mult: float
    exit_reason: str
    bars_held: int


@dataclass
class FoldResult:
    fold_idx: int
    horizon: int
    n_features: int
    feature_names: list
    n_trades: int
    avg_daily_pf: float
    aggregate_pf: float
    win_rate: float
    total_pnl: float
    best_k: int


@dataclass
class HorizonResult:
    horizon: int
    fold_results: list
    avg_daily_pf: float
    consistency: float  # fraction of folds with avg_daily_pf > 1.0
    total_trades: int
    holdout_avg_daily_pf: float = 0.0
    holdout_aggregate_pf: float = 0.0
    holdout_dsr: float = -99.0
    holdout_n_trades: int = 0
    holdout_features: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: DATA LOADING & MATRIX CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

def load_ohlcv_matrices(database_url: str, days: int):
    """Load 1h OHLCV and build aligned (n_bars, n_symbols) matrices.

    Returns: close, open_, high, low, volume matrices (float64),
             ts_grid (1d), symbols list, btc_idx (int or None)
    """
    start_ts = datetime.now(timezone.utc) - timedelta(days=days + 15)  # extra for warmup
    end_ts = datetime.now(timezone.utc)

    logger.info("Loading 1h OHLCV: %s to %s (%d+15 days)", start_ts.date(), end_ts.date(), days)
    t0 = time.time()

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM (
                    SELECT symbol, bar_start_ts, open, high, low, close, volume,
                           ROW_NUMBER() OVER (
                               PARTITION BY symbol, bar_start_ts
                               ORDER BY CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END
                           ) AS rn
                    FROM ohlcv
                    WHERE timeframe = '1h'
                      AND bar_start_ts >= %s AND bar_start_ts < %s
                      AND is_final = TRUE
                ) sub
                WHERE rn = 1
                ORDER BY symbol, bar_start_ts
            """, (start_ts, end_ts))

            sym_rows = defaultdict(list)
            row_count = 0
            for row in cur:
                sym_rows[row[0]].append(row)
                row_count += 1

    logger.info("  Fetched %d rows for %d symbols in %.1fs",
                row_count, len(sym_rows), time.time() - t0)

    # Build global timestamp grid from union of all timestamps
    all_ts = set()
    for rows in sym_rows.values():
        for r in rows:
            ts = r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None else r[1].timestamp()
            all_ts.add(ts)
    ts_grid = np.array(sorted(all_ts))
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)

    # Filter symbols with enough bars
    valid_symbols = []
    for sym, rows in sorted(sym_rows.items()):
        if len(rows) >= MIN_BARS_PER_SYMBOL:
            valid_symbols.append(sym)

    n_symbols = len(valid_symbols)
    logger.info("  %d symbols with >= %d bars, %d total bars in grid",
                n_symbols, MIN_BARS_PER_SYMBOL, n_bars)

    # Build matrices (float64 for price accuracy, convert features to float32 later)
    close = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    open_ = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    high = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    low = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
    volume = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)

    btc_idx = None
    for s_idx, sym in enumerate(valid_symbols):
        if sym == BTC_SYMBOL:
            btc_idx = s_idx
        for r in sym_rows[sym]:
            ts = r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None else r[1].timestamp()
            t_idx = ts_to_idx[ts]
            open_[t_idx, s_idx] = float(r[2])
            high[t_idx, s_idx] = float(r[3])
            low[t_idx, s_idx] = float(r[4])
            close[t_idx, s_idx] = float(r[5])
            volume[t_idx, s_idx] = float(r[6])

    # Forward-fill NaN gaps (symbols may have missing bars)
    for s_idx in range(n_symbols):
        for arr in [close, open_, high, low, volume]:
            col = arr[:, s_idx]
            mask = np.isnan(col)
            if mask.any() and not mask.all():
                idx = np.where(~mask, np.arange(n_bars), 0)
                np.maximum.accumulate(idx, out=idx)
                arr[:, s_idx] = col[idx]

    # Build per-symbol OHLCV dict for trade simulation (keyed by (symbol, '1h'))
    ohlcv_data = {}
    for s_idx, sym in enumerate(valid_symbols):
        ohlcv_data[(sym, '1h')] = {
            "ts": ts_grid,
            "open": open_[:, s_idx],
            "high": high[:, s_idx],
            "low": low[:, s_idx],
            "close": close[:, s_idx],
            "volume": volume[:, s_idx],
        }

    mem_mb = (close.nbytes + open_.nbytes + high.nbytes + low.nbytes + volume.nbytes) / 1024**2
    logger.info("  Matrices: (%d, %d), %.1f MB, BTC idx=%s",
                n_bars, n_symbols, mem_mb, btc_idx)

    return close, open_, high, low, volume, ts_grid, valid_symbols, btc_idx, ohlcv_data


def compute_forward_returns(close, horizons=HORIZONS):
    """Compute forward returns for each horizon. Returns dict[h] -> (n_bars, n_symbols) array."""
    n_bars, n_symbols = close.shape
    fwd = {}
    for h in horizons:
        ret = np.full((n_bars, n_symbols), np.nan, dtype=np.float32)
        valid = (close[:-h] != 0) & ~np.isnan(close[:-h]) & ~np.isnan(close[h:])
        ret_vals = np.where(valid, (close[h:] - close[:-h]) / close[:-h], np.nan)
        ret[:n_bars - h] = ret_vals.astype(np.float32)
        fwd[h] = ret
        logger.info("  Forward returns h=%d: %d valid bars",
                    h, np.sum(~np.isnan(ret)))
    return fwd


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: FEATURE ENGINEERING (vectorized numpy on matrices)
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_sum_2d(arr, w):
    """Rolling sum over axis=0 for 2D array. Returns same shape, first w-1 rows are partial."""
    cs = np.cumsum(arr, axis=0)
    out = np.empty_like(arr)
    out[:w] = cs[:w]
    out[w:] = cs[w:] - cs[:-w]
    return out


def _rolling_mean_2d(arr, w):
    """Rolling mean over axis=0."""
    rs = _rolling_sum_2d(arr, w)
    out = rs / w
    out[:w-1] = np.nan
    return out


def _rolling_std_2d(arr, w):
    """Rolling std over axis=0."""
    cs1 = np.cumsum(arr, axis=0)
    cs2 = np.cumsum(arr**2, axis=0)
    s1 = np.empty_like(arr)
    s2 = np.empty_like(arr)
    s1[:w] = cs1[:w]
    s1[w:] = cs1[w:] - cs1[:-w]
    s2[:w] = cs2[:w]
    s2[w:] = cs2[w:] - cs2[:-w]
    mean = s1 / w
    var = s2 / w - mean**2
    var = np.maximum(var, 0)
    out = np.sqrt(var)
    out[:w-1] = np.nan
    return out


def _rolling_max_2d(arr, w):
    """Rolling max over axis=0 using sliding_window_view."""
    from numpy.lib.stride_tricks import sliding_window_view
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w-1:] = np.max(sliding_window_view(arr, w, axis=0), axis=-1)
    return out


def _rolling_min_2d(arr, w):
    """Rolling min over axis=0 using sliding_window_view."""
    from numpy.lib.stride_tricks import sliding_window_view
    n = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n >= w:
        out[w-1:] = np.min(sliding_window_view(arr, w, axis=0), axis=-1)
    return out


def _rolling_corr_2d(x, y, w):
    """Rolling Pearson correlation between two (n_bars, n_symbols) arrays."""
    sx = _rolling_sum_2d(x, w)
    sy = _rolling_sum_2d(y, w)
    sx2 = _rolling_sum_2d(x**2, w)
    sy2 = _rolling_sum_2d(y**2, w)
    sxy = _rolling_sum_2d(x * y, w)
    numer = w * sxy - sx * sy
    dx = w * sx2 - sx**2
    dy = w * sy2 - sy**2
    denom = np.sqrt(np.maximum(dx, 0) * np.maximum(dy, 0))
    out = np.full_like(x, np.nan)
    valid = denom > 0
    out[valid] = numer[valid] / denom[valid]
    out[:w-1] = np.nan
    return out


def _rolling_slope_2d(arr, w):
    """Rolling OLS slope over axis=0 (slope of arr vs time index)."""
    # slope = (n*sum(i*y) - sum(i)*sum(y)) / (n*sum(i^2) - sum(i)^2)
    n = w
    i_vals = np.arange(n, dtype=np.float64)
    sum_i = i_vals.sum()
    sum_i2 = (i_vals**2).sum()
    denom = n * sum_i2 - sum_i**2
    if denom == 0:
        return np.full_like(arr, np.nan)

    # Create time-weighted version: multiply each element by its position in window
    # For rolling computation, we need i*y where i is position within the window
    # Use convolution approach: slope = (n * sum(i*y) - sum_i * sum(y)) / denom
    # sum(i*y) for window ending at t: sum_{k=0}^{n-1} k * arr[t-n+1+k]
    weights = np.arange(n, dtype=np.float64)
    # Weighted rolling sum
    n_bars = arr.shape[0]
    out = np.full_like(arr, np.nan)
    if n_bars < w:
        return out

    from numpy.lib.stride_tricks import sliding_window_view
    windows = sliding_window_view(arr, w, axis=0)  # (n_bars-w+1, n_symbols, w)
    iy_sum = np.sum(windows * weights[np.newaxis, np.newaxis, :], axis=-1)  # (n_bars-w+1, n_symbols)
    y_sum = np.sum(windows, axis=-1)
    slopes = (n * iy_sum - sum_i * y_sum) / denom
    out[w-1:] = slopes
    return out


def _xs_rank_2d(arr):
    """Cross-sectional percentile rank at each row. Returns (n_bars, n_symbols) in [0, 1]."""
    out = np.full_like(arr, np.nan)
    for t in range(arr.shape[0]):
        row = arr[t]
        valid = ~np.isnan(row)
        if valid.sum() < 2:
            continue
        ranks = np.argsort(np.argsort(row[valid]))
        out[t, valid] = ranks / (valid.sum() - 1)
    return out


def _xs_zscore_2d(arr):
    """Cross-sectional z-score at each row."""
    mean = np.nanmean(arr, axis=1, keepdims=True)
    std = np.nanstd(arr, axis=1, keepdims=True)
    std = np.where(std > 0, std, 1.0)
    return (arr - mean) / std


def _atr_2d(high, low, close, n=14):
    """ATR for (n_bars, n_symbols) matrices."""
    prev_close = np.full_like(close, np.nan)
    prev_close[1:] = close[:-1]
    hl = high - low
    hc = np.abs(high - prev_close)
    lc = np.abs(low - prev_close)
    tr = np.maximum(hl, np.maximum(hc, lc))
    tr[0] = hl[0]
    return _rolling_mean_2d(tr, n)


def engineer_features(close, open_, high, low, volume, ts_grid, btc_idx, quick=False):
    """Engineer all features. Returns dict[name] -> (n_bars, n_symbols) float32 array."""
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    t0 = time.time()
    n_bars, n_symbols = close.shape
    features = {}

    def _store(name, arr):
        """Store feature, converting to float32 and clamping infinities."""
        a = np.array(arr, dtype=np.float32, copy=True)
        a[~np.isfinite(a)] = np.nan
        features[name] = a

    # Precompute common intermediates
    logger.info("  Computing intermediates...")
    bar_range = high - low
    safe_range = np.where(bar_range > 0, bar_range, 1e-10)
    body = np.abs(close - open_)

    ret_1 = np.full((n_bars, n_symbols), np.nan)
    valid_prev = (close[:-1] != 0) & ~np.isnan(close[:-1])
    ret_1[1:] = np.where(valid_prev, (close[1:] - close[:-1]) / np.where(valid_prev, close[:-1], 1.0), np.nan)

    sign_ret_1 = np.sign(ret_1)
    abs_ret_1 = np.abs(ret_1)

    atr_14 = _atr_2d(high, low, close, 14)
    safe_close = np.where(close > 0, close, 1e-10)

    # ── Category A: Single-Bar Geometry (6) ──
    logger.info("  Category A: Single-bar geometry (6 features)...")
    _store("body_ratio", body / safe_range)
    _store("upper_wick_ratio", (high - np.maximum(open_, close)) / safe_range)
    _store("lower_wick_ratio", (np.minimum(open_, close) - low) / safe_range)

    gap_pct = np.full((n_bars, n_symbols), np.nan)
    gap_pct[1:] = np.where(valid_prev, (open_[1:] - close[:-1]) / np.where(valid_prev, close[:-1], 1.0), np.nan)
    _store("gap_pct", gap_pct)

    _store("range_pct", bar_range / safe_close)

    vol_per_range = volume / (bar_range / safe_close + 1e-10)
    _store("volume_per_range", _xs_zscore_2d(vol_per_range))

    # ── Category B: Multi-Scale Returns & Acceleration (25) ──
    logger.info("  Category B: Multi-scale returns (25 features)...")
    lookbacks = [2, 3, 5, 8, 13, 21, 34, 55, 89]
    if quick:
        lookbacks = [5, 13, 21, 55]

    ret_cache = {}
    for L in lookbacks:
        ret_L = np.full((n_bars, n_symbols), np.nan)
        valid_L = (close[:-L] != 0) & ~np.isnan(close[:-L]) & ~np.isnan(close[L:])
        ret_L[L:] = np.where(valid_L, (close[L:] - close[:-L]) / np.where(valid_L, close[:-L], 1.0), np.nan)
        _store(f"ret_{L}", ret_L)
        ret_cache[L] = ret_L

    # Accelerations
    for L in ([5, 13, 21, 34, 55] if not quick else [5, 21]):
        if L in ret_cache:
            acc = np.full((n_bars, n_symbols), np.nan)
            acc[L:] = ret_cache[L][L:] - ret_cache[L][:-L]
            _store(f"acc_{L}", acc)

    # Vol-normalized returns
    for L in ([5, 13, 21, 55] if not quick else [5, 21]):
        if L in ret_cache:
            vol_L = _rolling_std_2d(ret_1, L)
            safe_vol = np.where(vol_L > 0, vol_L, 1e-10)
            _store(f"ret_norm_{L}", ret_cache[L] / safe_vol)

    # Sign consistency
    for L in ([8, 21, 55] if not quick else [21]):
        _store(f"sign_consistency_{L}", _rolling_mean_2d(sign_ret_1, L))

    # Cross-scale divergence
    if not quick:
        _store("xs_div_5_21", ret_cache[5] - ret_cache[21] / (21/5))
        _store("xs_div_5_55", ret_cache[5] - ret_cache[55] / (55/5))
        _store("xs_div_13_55", ret_cache[13] - ret_cache[55] / (55/13))
        _store("xs_div_8_34", ret_cache[8] - ret_cache[34] / (34/8))

    # ── Category C: Price Range Dynamics (10) ──
    logger.info("  Category C: Price range dynamics (10 features)...")
    for L in ([21, 55, 89] if not quick else [21, 55]):
        rmax = _rolling_max_2d(close, L)
        rmin = _rolling_min_2d(close, L)
        rng = rmax - rmin
        safe_rng = np.where(rng > 0, rng, 1e-10)
        _store(f"pos_in_range_{L}", (close - rmin) / safe_rng)

    # Range expansion ratios
    range_8 = _rolling_max_2d(high, 8) - _rolling_min_2d(low, 8)
    range_21 = _rolling_max_2d(high, 21) - _rolling_min_2d(low, 21)
    range_55 = _rolling_max_2d(high, 55) - _rolling_min_2d(low, 55)
    safe_r21 = np.where(range_21 > 0, range_21, 1e-10)
    safe_r55 = np.where(range_55 > 0, range_55, 1e-10)
    _store("range_expansion_8v21", range_8 / safe_r21)
    if not quick:
        _store("range_expansion_21v55", range_21 / safe_r55)

    # Distance from high/low in ATR units
    high_21 = _rolling_max_2d(high, 21)
    low_21 = _rolling_min_2d(low, 21)
    safe_atr = np.where(atr_14 > 0, atr_14, 1e-10)
    _store("dist_from_high_21", (high_21 - close) / safe_atr)
    _store("dist_from_low_21", (close - low_21) / safe_atr)

    # New high/low counts
    if not quick:
        from numpy.lib.stride_tricks import sliding_window_view
        if n_bars >= 21:
            c_win = sliding_window_view(close, 21, axis=0)  # (n_bars-20, n_sym, 21)
            new_high = np.sum(c_win == np.max(c_win, axis=-1, keepdims=True), axis=-1)
            new_low = np.sum(c_win == np.min(c_win, axis=-1, keepdims=True), axis=-1)
            nhc = np.full((n_bars, n_symbols), np.nan)
            nlc = np.full((n_bars, n_symbols), np.nan)
            nhc[20:] = new_high
            nlc[20:] = new_low
            _store("new_high_count_21", nhc)
            _store("new_low_count_21", nlc)

    # Range contraction
    range_5 = _rolling_max_2d(high, 5) - _rolling_min_2d(low, 5)
    _store("range_contraction_ratio", range_5 / safe_r21)

    # ── Category D: Volatility Dynamics (14) ──
    logger.info("  Category D: Volatility dynamics (14 features)...")
    vol_8 = _rolling_std_2d(ret_1, 8)
    vol_21 = _rolling_std_2d(ret_1, 21)
    vol_55 = _rolling_std_2d(ret_1, 55)
    _store("vol_8", vol_8)
    _store("vol_21", vol_21)
    if not quick:
        _store("vol_55", vol_55)

    safe_v21 = np.where(vol_21 > 0, vol_21, 1e-10)
    safe_v55 = np.where(vol_55 > 0, vol_55, 1e-10)
    _store("vol_ratio_8_21", vol_8 / safe_v21)
    _store("vol_ratio_8_55", vol_8 / safe_v55)
    if not quick:
        _store("vol_ratio_21_55", vol_21 / safe_v55)
        _store("vol_trend_21", _rolling_slope_2d(vol_8, 21))

    # Vol percentile: rank of vol_8 in last 55 bars
    if not quick and n_bars >= 55:
        from numpy.lib.stride_tricks import sliding_window_view
        v8_win = sliding_window_view(vol_8, 55, axis=0)  # (n-54, n_sym, 55)
        current = vol_8[54:]
        pctile = np.sum(v8_win <= current[:, :, np.newaxis], axis=-1) / 55.0
        vp = np.full((n_bars, n_symbols), np.nan)
        vp[54:] = pctile
        _store("vol_percentile_55", vp)

    _store("atr_14_norm", atr_14 / safe_close)
    atr_55 = _atr_2d(high, low, close, 55)
    safe_a55 = np.where(atr_55 > 0, atr_55, 1e-10)
    _store("atr_ratio_14_55", atr_14 / safe_a55)

    # Parkinson volatility
    log_hl = np.log(np.where(high > 0, high, 1e-10)) - np.log(np.where(low > 0, low, 1e-10))
    _store("parkinson_vol_21", np.sqrt(_rolling_mean_2d(log_hl**2, 21) / (4 * np.log(2))))

    # Garman-Klass volatility
    if not quick:
        log_co = np.log(np.where(close > 0, close, 1e-10)) - np.log(np.where(open_ > 0, open_, 1e-10))
        gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
        _store("garman_klass_21", np.sqrt(np.abs(_rolling_mean_2d(gk, 21))))

    # Vol of vol
    _store("vol_of_vol_21", _rolling_std_2d(vol_8, 21))

    # ── Category E: Volume-Price Interactions (12) ──
    logger.info("  Category E: Volume-price interactions (12 features)...")
    volume_z = _xs_zscore_2d(volume)
    _store("volume_z_20", _rolling_mean_2d(volume_z, 20))

    if not quick:
        _store("vol_z_max_6", _rolling_max_2d(volume_z, 6))

    _store("vol_price_corr_21", _rolling_corr_2d(abs_ret_1, volume, 21))
    if not quick:
        _store("vol_price_corr_55", _rolling_corr_2d(abs_ret_1, volume, 55))

    _store("volume_trend_21", _rolling_slope_2d(volume, 21))

    # OBV slope
    obv = np.cumsum(sign_ret_1 * volume, axis=0)
    obv[0] = 0
    _store("obv_slope_21", _rolling_slope_2d(obv, 21))

    # VWAP distance (21-bar VWAP approximation)
    vwap_num = _rolling_sum_2d(close * volume, 21)
    vwap_den = _rolling_sum_2d(volume, 21)
    safe_vd = np.where(vwap_den > 0, vwap_den, 1e-10)
    vwap = vwap_num / safe_vd
    _store("vwap_distance_21", (close - vwap) / safe_atr)

    # Volume asymmetry
    up_vol = np.where(ret_1 > 0, volume, 0)
    down_vol = np.where(ret_1 < 0, volume, 0)
    up_sum = _rolling_sum_2d(up_vol, 21)
    down_sum = _rolling_sum_2d(down_vol, 21)
    safe_ds = np.where(down_sum > 0, down_sum, 1e-10)
    _store("volume_asymmetry_21", up_sum / safe_ds)

    # MFI (14-bar)
    if not quick:
        typical = (high + low + close) / 3
        mf = typical * volume
        pos_mf = np.where(typical > np.roll(typical, 1, axis=0), mf, 0)
        neg_mf = np.where(typical < np.roll(typical, 1, axis=0), mf, 0)
        pos_sum = _rolling_sum_2d(pos_mf, 14)
        neg_sum = _rolling_sum_2d(neg_mf, 14)
        safe_ns = np.where(neg_sum > 0, neg_sum, 1e-10)
        _store("mfi_14", 100 - 100 / (1 + pos_sum / safe_ns))

    # AD line slope
    clv = np.where(bar_range > 0, ((close - low) - (high - close)) / safe_range, 0)
    ad = np.cumsum(clv * volume, axis=0)
    _store("ad_slope_21", _rolling_slope_2d(ad, 21))

    # Volume breakout / drought
    _store("volume_breakout", (volume_z > 2.0).astype(np.float32))
    _store("volume_drought", (volume_z < -1.0).astype(np.float32))

    # ── Category F: Distributional (12) ──
    logger.info("  Category F: Distributional (12 features)...")
    # Skewness & kurtosis via rolling moments
    for L in ([21, 55] if not quick else [21]):
        mean_L = _rolling_mean_2d(ret_1, L)
        std_L = _rolling_std_2d(ret_1, L)
        safe_std = np.where(std_L > 0, std_L, 1e-10)
        dev = (ret_1 - mean_L) / safe_std
        _store(f"skew_{L}", _rolling_mean_2d(dev**3, L))
        _store(f"kurtosis_{L}", _rolling_mean_2d(dev**4, L))

    # Autocorrelation via rolling correlation of ret_1 with lagged ret_1
    for lag in ([1] if quick else [1, 2, 3]):
        for L in ([21] if quick else ([21, 55] if lag == 1 else [21])):
            x = ret_1[:-lag] if lag > 0 else ret_1
            y = ret_1[lag:]
            corr = _rolling_corr_2d(x, y, L)
            out = np.full((n_bars, n_symbols), np.nan)
            out[lag:lag+corr.shape[0]] = corr
            name = f"autocorr_lag{lag}_{L}"
            _store(name, out)

    # Tail ratio
    if not quick and n_bars >= 21:
        from numpy.lib.stride_tricks import sliding_window_view
        r1_win = sliding_window_view(ret_1, 21, axis=0)
        p95 = np.percentile(r1_win, 95, axis=-1)
        p05 = np.abs(np.percentile(r1_win, 5, axis=-1))
        safe_p05 = np.where(p05 > 0, p05, 1e-10)
        tr = np.full((n_bars, n_symbols), np.nan)
        tr[20:] = p95 / safe_p05
        _store("tail_ratio_21", tr)

    # Max drawdown (21-bar)
    if n_bars >= 21:
        from numpy.lib.stride_tricks import sliding_window_view
        c_win = sliding_window_view(close, 21, axis=0)
        cum_max = np.maximum.accumulate(c_win, axis=-1)
        dd = (c_win - cum_max) / np.where(cum_max > 0, cum_max, 1e-10)
        mdd = np.min(dd, axis=-1)
        md = np.full((n_bars, n_symbols), np.nan)
        md[20:] = mdd
        _store("max_drawdown_21", md)

    # Up/down ratio
    up_bars = (ret_1 > 0).astype(np.float64)
    down_bars = (ret_1 < 0).astype(np.float64)
    up_cnt = _rolling_sum_2d(up_bars, 21)
    dn_cnt = _rolling_sum_2d(down_bars, 21)
    safe_dc = np.where(dn_cnt > 0, dn_cnt, 1e-10)
    _store("up_down_ratio_21", up_cnt / safe_dc)

    # Mean reversion score
    ac1_21 = features.get("autocorr_lag1_21")
    if ac1_21 is not None:
        vr_ratio = features.get("vol_ratio_8_21")
        if vr_ratio is not None:
            _store("mean_reversion_score_21", -ac1_21 * vr_ratio)

    # ── Category G: Cross-Sectional (14) ──
    logger.info("  Category G: Cross-sectional (14 features)...")
    for L in ([5, 21, 55] if not quick else [5, 21]):
        if L in ret_cache:
            _store(f"xs_ret_rank_{L}", _xs_rank_2d(ret_cache[L]))

    # Rank velocity
    xr5 = features.get("xs_ret_rank_5")
    if xr5 is not None:
        rv5 = np.full((n_bars, n_symbols), np.nan)
        rv5[5:] = xr5[5:] - xr5[:-5]
        _store("xs_rank_velocity_5", rv5)
    xr21 = features.get("xs_ret_rank_21")
    if not quick and xr21 is not None:
        rv21 = np.full((n_bars, n_symbols), np.nan)
        rv21[21:] = xr21[21:] - xr21[:-21]
        _store("xs_rank_velocity_21", rv21)

    _store("xs_vol_rank", _xs_rank_2d(vol_8))
    _store("xs_volume_rank", _xs_rank_2d(volume_z))

    # BTC correlation / beta
    if btc_idx is not None:
        btc_ret = ret_1[:, btc_idx:btc_idx+1]  # keep 2D for broadcasting
        btc_ret_broad = np.broadcast_to(btc_ret, (n_bars, n_symbols))
        _store("btc_corr_21", _rolling_corr_2d(ret_1, btc_ret_broad, 21))
        if not quick:
            _store("btc_corr_55", _rolling_corr_2d(ret_1, btc_ret_broad, 55))

        # Beta = cov / var_btc
        cov_21 = _rolling_mean_2d(ret_1 * btc_ret_broad, 21) - _rolling_mean_2d(ret_1, 21) * _rolling_mean_2d(btc_ret_broad, 21)
        var_btc_21 = _rolling_mean_2d(btc_ret_broad**2, 21) - _rolling_mean_2d(btc_ret_broad, 21)**2
        safe_vb = np.where(var_btc_21 > 0, var_btc_21, 1e-10)
        beta_21 = cov_21 / safe_vb
        _store("btc_beta_21", beta_21)

        # Relative strength
        univ_mean_ret = np.nanmean(ret_cache.get(21, ret_1), axis=1, keepdims=True)
        if 21 in ret_cache:
            _store("relative_strength_21", ret_cache[21] - univ_mean_ret)

        # BTC lead-lag residual
        _store("btc_lead_lag_residual", ret_1 - beta_21 * btc_ret_broad)

    # Cross-sectional dispersion
    if 5 in ret_cache:
        disp = np.nanstd(ret_cache[5], axis=1, keepdims=True)
        disp_broad = np.broadcast_to(disp, (n_bars, n_symbols))
        _store("xs_dispersion_21", _rolling_mean_2d(disp_broad, 21))
        _store("xs_dispersion_trend", _rolling_slope_2d(disp_broad, 13))

    # ── Category H: Temporal (8) ──
    logger.info("  Category H: Temporal (8 features)...")
    hours = ((ts_grid % 86400) / 3600).astype(np.float64)
    dow = (((ts_grid / 86400) + 4) % 7).astype(np.float64)  # Unix epoch was Thursday

    hour_sin = np.sin(2 * np.pi * hours / 24)[:, np.newaxis] * np.ones((1, n_symbols))
    hour_cos = np.cos(2 * np.pi * hours / 24)[:, np.newaxis] * np.ones((1, n_symbols))
    _store("hour_sin", hour_sin)
    _store("hour_cos", hour_cos)
    dow_sin = np.sin(2 * np.pi * dow / 7)[:, np.newaxis] * np.ones((1, n_symbols))
    dow_cos = np.cos(2 * np.pi * dow / 7)[:, np.newaxis] * np.ones((1, n_symbols))
    _store("dow_sin", dow_sin)
    _store("dow_cos", dow_cos)

    # Bars since vol spike (volume_z > 3)
    vol_spike = (volume_z > 3.0).astype(np.float64)
    bars_since_vs = np.zeros((n_bars, n_symbols))
    for t in range(1, n_bars):
        bars_since_vs[t] = np.where(vol_spike[t] > 0, 0, bars_since_vs[t-1] + 1)
    _store("bars_since_vol_spike", bars_since_vs)

    # Bars since range spike (range_pct > 95th percentile of trailing 55)
    if not quick:
        rp = bar_range / safe_close
        bars_since_rs = np.zeros((n_bars, n_symbols))
        rp_thresh = _rolling_max_2d(rp, 55) * 0.95  # approximate 95th pctile
        range_spike = (rp > rp_thresh).astype(np.float64)
        for t in range(1, n_bars):
            bars_since_rs[t] = np.where(range_spike[t] > 0, 0, bars_since_rs[t-1] + 1)
        _store("bars_since_range_spike", bars_since_rs)

    # Session encoding (Asia=0-8 UTC, Europe=8-16, US=16-24)
    session = hours / 8.0  # 0-3 range
    sess_sin = np.sin(2 * np.pi * session / 3)[:, np.newaxis] * np.ones((1, n_symbols))
    sess_cos = np.cos(2 * np.pi * session / 3)[:, np.newaxis] * np.ones((1, n_symbols))
    _store("session_sin", sess_sin)
    _store("session_cos", sess_cos)

    # ── Category I: Variance Ratio (7) ──
    logger.info("  Category I: Variance ratio (7 features)...")
    var_1 = _rolling_mean_2d(ret_1**2, 21)  # variance of 1-bar returns
    for q in ([2, 5, 8, 13] if not quick else [2, 5]):
        # VR(q) = Var(q-bar returns) / (q * Var(1-bar returns))
        ret_q = np.full((n_bars, n_symbols), np.nan)
        if q <= 1:
            continue
        ret_q[q:] = (close[q:] - close[:-q]) / np.where(close[:-q] > 0, close[:-q], 1e-10)
        var_q = _rolling_mean_2d(ret_q**2, 21)
        safe_v1 = np.where(var_1 > 0, var_1, 1e-10)
        _store(f"vr_{q}", var_q / (q * safe_v1))

    # VR trend
    vr5 = features.get("vr_5")
    if vr5 is not None:
        _store("vr_trend", _rolling_slope_2d(vr5, 13))

    # Hurst proxy
    if vr5 is not None:
        log_vr5 = np.log(np.where(vr5 > 0, vr5, 1e-10))
        _store("hurst_proxy", log_vr5 / (2 * np.log(5)))

    # Regime composite
    ac1 = features.get("autocorr_lag1_21")
    vr5_f = features.get("vr_5")
    vr_8_21 = features.get("vol_ratio_8_21")
    if ac1 is not None and vr5_f is not None and vr_8_21 is not None:
        _store("regime_composite", 0.4 * vr5_f + 0.3 * ac1 + 0.3 * vr_8_21)

    # ── Category J: Market-Level Dispersion (4) ──
    logger.info("  Category J: Market-level dispersion (4 features)...")
    mkt_ret_mean = np.nanmean(ret_1, axis=1, keepdims=True)
    mkt_ret_std = np.nanstd(ret_1, axis=1, keepdims=True)
    mkt_breadth = np.nanmean((ret_1 > 0).astype(np.float64), axis=1, keepdims=True)
    mkt_vol_breadth = np.nanmean((volume_z > 1.5).astype(np.float64), axis=1, keepdims=True)

    _store("market_ret_mean", np.broadcast_to(mkt_ret_mean, (n_bars, n_symbols)))
    _store("market_ret_std", np.broadcast_to(mkt_ret_std, (n_bars, n_symbols)))
    _store("market_breadth", np.broadcast_to(mkt_breadth, (n_bars, n_symbols)))
    _store("market_vol_breadth", np.broadcast_to(mkt_vol_breadth, (n_bars, n_symbols)))

    # ── Category K: Unnamed — Ratios of Ratios (18) ──
    logger.info("  Category K: Ratios of ratios (18 features)...")
    vr_8_21 = features.get("vol_ratio_8_21")
    vr_21_55 = features.get("vol_ratio_21_55")
    if vr_8_21 is not None and vr_21_55 is not None:
        safe_vr2155 = np.where(np.abs(vr_21_55) > 1e-10, vr_21_55, 1e-10)
        _store("vol_acceleration_ratio", vr_8_21 / safe_vr2155)

    # Momentum quality divergence
    rn5 = features.get("ret_norm_5")
    rn21 = features.get("ret_norm_21")
    if rn5 is not None and rn21 is not None:
        _store("momentum_quality_divergence", rn5 - rn21)

    # Range-body evolution
    br = features.get("body_ratio")
    if br is not None:
        br_mean8 = _rolling_mean_2d(br, 8)
        safe_brm = np.where(br_mean8 > 0, br_mean8, 1e-10)
        _store("range_body_evolution", br / safe_brm)

    # Wick trend
    wick_diff = features.get("upper_wick_ratio", np.zeros((n_bars, n_symbols))) - features.get("lower_wick_ratio", np.zeros((n_bars, n_symbols)))
    _store("wick_trend_8", _rolling_slope_2d(wick_diff, 8))

    # Volume-price jerk (3rd derivative of vol_price_corr)
    vpc21 = features.get("vol_price_corr_21")
    if vpc21 is not None:
        d1 = np.full_like(vpc21, np.nan)
        d1[1:] = vpc21[1:] - vpc21[:-1]
        d2 = np.full_like(d1, np.nan)
        d2[1:] = d1[1:] - d1[:-1]
        _store("volume_price_jerk", d2)

    # ret cubed mean
    _store("ret_cubed_mean_21", _rolling_mean_2d(ret_1**3, 21))

    # Gap-vol interaction
    gp = features.get("gap_pct")
    if gp is not None:
        _store("gap_vol_interaction", gp * volume_z)

    # Range-return efficiency
    range_sum_5 = _rolling_sum_2d(bar_range, 5)
    safe_rs5 = np.where(range_sum_5 > 0, range_sum_5, 1e-10)
    ret5 = ret_cache.get(5)
    if ret5 is not None:
        _store("range_ret_efficiency", np.abs(ret5) * safe_close / safe_rs5)

    # Vol-price asymmetry ratio
    vpc55 = features.get("vol_price_corr_55")
    if vpc21 is not None and vpc55 is not None:
        safe_vpc55 = np.where(np.abs(vpc55) > 1e-10, vpc55, 1e-10)
        _store("vol_price_asymmetry_ratio", vpc21 / safe_vpc55)

    # Acceleration of acceleration (jerk)
    acc13 = features.get("acc_13")
    if acc13 is not None:
        aa = np.full_like(acc13, np.nan)
        aa[5:] = acc13[5:] - acc13[:-5]
        _store("acceleration_acceleration", aa)

    # Body-volume coupling
    _store("body_volume_coupling", _rolling_corr_2d(body / safe_range, volume_z, 21))

    # Range-volume decoupling
    rv_product = (bar_range / safe_close) * volume_z
    rv_mean = _rolling_mean_2d(rv_product, 21)
    _store("range_volume_decoupling", rv_product - rv_mean)

    # Norm ret vs vol_z residual (simplified: ret_norm_21 - k*vol_z, k from trailing 55)
    rn21 = features.get("ret_norm_21")
    if rn21 is not None:
        k = _rolling_corr_2d(rn21, volume_z, 55) * _rolling_std_2d(rn21, 55) / np.where(_rolling_std_2d(volume_z, 55) > 0, _rolling_std_2d(volume_z, 55), 1e-10)
        _store("norm_ret_vs_vol_z_residual", rn21 - k * volume_z)

    # Close-open range ratio diff
    co_ratio = (close - open_) / safe_range
    _store("close_open_range_ratio_diff", co_ratio - _rolling_mean_2d(co_ratio, 21))

    # Wick asymmetry of asymmetry
    if not quick:
        wick_asym = features.get("upper_wick_ratio", np.zeros((n_bars, n_symbols))) - features.get("lower_wick_ratio", np.zeros((n_bars, n_symbols)))
        _store("high_low_asymmetry_of_asymmetry", _rolling_std_2d(wick_asym, 13))

    # Vol z curvature
    _store("vol_z_curvature", volume_z - 2 * np.roll(volume_z, 1, axis=0) + np.roll(volume_z, 2, axis=0))

    # Ret-vol cross moment
    _store("ret_vol_cross_moment_21", _rolling_mean_2d(ret_1 * volume_z, 21))

    # Efficiency ratio 8
    abs_ret_sum_8 = _rolling_sum_2d(abs_ret_1, 8)
    safe_ars = np.where(abs_ret_sum_8 > 0, abs_ret_sum_8, 1e-10)
    if 8 in ret_cache:
        _store("efficiency_ratio_8", np.abs(ret_cache[8]) / safe_ars)

    # ── Category L: Rank Distribution Dynamics (15) ──
    logger.info("  Category L: Rank distribution dynamics (15 features)...")

    # XS return concentration (Herfindahl)
    abs_ret_5 = np.abs(ret_cache.get(5, ret_1))
    abs_sum = np.nansum(abs_ret_5, axis=1, keepdims=True)
    safe_as = np.where(abs_sum > 0, abs_sum, 1e-10)
    shares = abs_ret_5 / safe_as
    hhi = np.nansum(shares**2, axis=1, keepdims=True)
    _store("xs_ret_concentration", np.broadcast_to(hhi, (n_bars, n_symbols)))

    # Winner/loser gaps
    if 5 in ret_cache:
        p95 = np.nanpercentile(ret_cache[5], 95, axis=1, keepdims=True)
        p50 = np.nanpercentile(ret_cache[5], 50, axis=1, keepdims=True)
        p05 = np.nanpercentile(ret_cache[5], 5, axis=1, keepdims=True)
        wg = p95 - p50
        lg = p50 - p05
        _store("xs_winner_gap", np.broadcast_to(wg, (n_bars, n_symbols)))
        _store("xs_loser_gap", np.broadcast_to(lg, (n_bars, n_symbols)))
        _store("xs_winner_loser_asymmetry", np.broadcast_to(wg - lg, (n_bars, n_symbols)))

    # Rank volatility & persistence
    xr5 = features.get("xs_ret_rank_5")
    if xr5 is not None:
        _store("rank_volatility_21", _rolling_std_2d(xr5, 21))
        # Rank persistence (autocorr of rank)
        xr5_lag = np.full_like(xr5, np.nan)
        xr5_lag[1:] = xr5[:-1]
        _store("rank_persistence", _rolling_corr_2d(xr5, xr5_lag, 13))

        # Rank mean reversion score
        xr5_lag8 = np.full_like(xr5, np.nan)
        xr5_lag8[8:] = xr5[:-8]
        _store("rank_mean_reversion_score", xr5 * xr5_lag8)

        # Rank acceleration
        rv5 = features.get("xs_rank_velocity_5")
        if rv5 is not None:
            ra = np.full_like(rv5, np.nan)
            ra[5:] = rv5[5:] - rv5[:-5]
            _store("rank_acceleration", ra)

    # Peer momentum (mean ret_5 of same volume decile)
    if not quick and 5 in ret_cache:
        peer_mom = np.full((n_bars, n_symbols), np.nan, dtype=np.float64)
        vol_ranks = _xs_rank_2d(volume)
        for t in range(n_bars):
            valid = ~np.isnan(vol_ranks[t]) & ~np.isnan(ret_cache[5][t])
            if valid.sum() < 10:
                continue
            decile = (vol_ranks[t] * 10).astype(int)
            decile = np.clip(decile, 0, 9)
            for d in range(10):
                mask = (decile == d) & valid
                if mask.sum() > 0:
                    peer_mom[t, mask] = np.nanmean(ret_cache[5][t, mask])
        _store("peer_momentum", peer_mom)

    # XS correlation dispersion
    btc_c21 = features.get("btc_corr_21")
    if btc_c21 is not None:
        cd = np.nanstd(btc_c21, axis=1, keepdims=True)
        _store("xs_correlation_dispersion", np.broadcast_to(cd, (n_bars, n_symbols)))
        # Decoupling rate
        dc = np.nanmean((np.abs(btc_c21) < 0.3).astype(np.float64), axis=1, keepdims=True)
        _store("xs_decoupling_rate", np.broadcast_to(dc, (n_bars, n_symbols)))

    # XS return entropy (binned)
    if not quick:
        ent = np.full((n_bars, 1), np.nan)
        for t in range(n_bars):
            vals = ret_1[t]
            valid = ~np.isnan(vals)
            if valid.sum() < 20:
                continue
            hist, _ = np.histogram(vals[valid], bins=10)
            p = hist / hist.sum()
            p = p[p > 0]
            ent[t, 0] = -np.sum(p * np.log(p))
        _store("xs_return_entropy_21", np.broadcast_to(ent, (n_bars, n_symbols)))

    # Volume-return mismatch
    xvr = features.get("xs_volume_rank")
    xrr5 = features.get("xs_ret_rank_5")
    if xvr is not None and xrr5 is not None:
        _store("xs_volume_return_mismatch", np.abs(xvr - xrr5))

    # Sector rotation proxy
    xr21_f = features.get("xs_ret_rank_21")
    xr55 = features.get("xs_ret_rank_55")
    if xr21_f is not None and xr55 is not None:
        _store("sector_rotation_proxy", np.abs(xr21_f - xr55))

    # XS lead-lag asymmetry
    if not quick:
        mkt_ret_lag1 = np.full((n_bars, n_symbols), np.nan)
        mkt_mean = np.nanmean(ret_1, axis=1)
        mkt_mean_lag = np.full(n_bars, np.nan)
        mkt_mean_lag[1:] = mkt_mean[:-1]
        mkt_lag_broad = mkt_mean_lag[:, np.newaxis] * np.ones((1, n_symbols))
        _store("xs_lead_lag_asymmetry", _rolling_corr_2d(ret_1, mkt_lag_broad, 21))

    # ── Category M: Multi-Bar Microstructure Sequences (16) ──
    logger.info("  Category M: Multi-bar sequences (16 features)...")

    # Consecutive rejection counts
    upper_wick = features.get("upper_wick_ratio", (high - np.maximum(open_, close)) / safe_range)
    lower_wick = features.get("lower_wick_ratio", (np.minimum(open_, close) - low) / safe_range)
    _store("consecutive_rejection_count_up", _rolling_sum_2d((upper_wick > 0.3).astype(np.float64), 8))
    _store("consecutive_rejection_count_down", _rolling_sum_2d((lower_wick > 0.3).astype(np.float64), 8))

    # Body direction streak
    bar_dir = np.sign(close - open_)
    _store("body_direction_streak", _rolling_sum_2d(bar_dir, 8))

    # Range expansion streak
    range_expanding = np.zeros((n_bars, n_symbols))
    range_expanding[1:] = (bar_range[1:] > bar_range[:-1]).astype(np.float64)
    _store("range_expansion_streak", _rolling_sum_2d(range_expanding, 8))

    # Volume climax distance
    if not quick:
        vol_climax = np.zeros((n_bars, n_symbols))
        for t in range(55, n_bars):
            window = volume_z[max(0, t-55):t+1]
            max_idx = np.argmax(window, axis=0)
            vol_climax[t] = window.shape[0] - 1 - max_idx
        _store("volume_climax_distance", vol_climax)

    # Return roughness
    ret_diff = np.full((n_bars, n_symbols), np.nan)
    ret_diff[1:] = np.abs(ret_1[1:] - ret_1[:-1])
    roughness_num = _rolling_sum_2d(ret_diff, 21)
    ret21 = ret_cache.get(21)
    if ret21 is not None:
        safe_r21v = np.where(np.abs(ret21) > 1e-10, np.abs(ret21), 1e-10)
        _store("return_roughness_21", roughness_num / safe_r21v)

    # Candle type entropy
    if not quick:
        # Encode: up/down (2) × big/small body (2) × wick/body dominated (2) = 8 types
        is_up = (close > open_).astype(int)
        body_big = (body / safe_range > 0.5).astype(int)
        wick_dom = (1 - body / safe_range > 0.5).astype(int)
        candle_type = is_up * 4 + body_big * 2 + wick_dom  # 0-7
        # Rolling entropy over 21 bars
        if n_bars >= 21:
            from numpy.lib.stride_tricks import sliding_window_view
            ct_win = sliding_window_view(candle_type, 21, axis=0)
            ent_arr = np.full((n_bars, n_symbols), np.nan)
            for t in range(ct_win.shape[0]):
                for s in range(n_symbols):
                    counts = np.bincount(ct_win[t, s], minlength=8)
                    p = counts / 21.0
                    p = p[p > 0]
                    ent_arr[t + 20, s] = -np.sum(p * np.log(p))
            _store("candle_type_entropy_21", ent_arr)

    # Price path convexity
    ret4 = ret_cache.get(4, np.full((n_bars, n_symbols), np.nan))
    ret8 = ret_cache.get(8, np.full((n_bars, n_symbols), np.nan))
    _store("price_path_convexity_8", ret8 - 2 * ret4)

    # Volume wave phase
    vz_lag5 = np.roll(volume_z, 5, axis=0)
    vz_lag2 = np.roll(volume_z, 2, axis=0)
    vz_lag7 = np.roll(volume_z, 7, axis=0)
    _store("volume_wave_phase", np.arctan2(volume_z - vz_lag5, vz_lag2 - vz_lag7))

    # Vol surprise
    bar_activity = abs_ret_1 * volume_z
    ba_mean = _rolling_mean_2d(bar_activity, 21)
    safe_bam = np.where(ba_mean > 0, ba_mean, 1e-10)
    _store("vol_surprise", bar_activity / safe_bam)

    # Gap reversal signal
    gp = features.get("gap_pct")
    if gp is not None:
        _store("gap_reversal_signal", gp * (-ret_1))

    # Body alternation score
    dir_change = np.zeros((n_bars, n_symbols))
    dir_change[1:] = (bar_dir[1:] != bar_dir[:-1]).astype(np.float64)
    _store("body_alternation_score", _rolling_mean_2d(dir_change, 8))

    # Wick to next open
    if not quick:
        wno = np.full((n_bars, n_symbols), np.nan)
        wno[:-1] = (open_[1:] - close[:-1]) / safe_range[:-1]
        _store("wick_to_next_open", wno)

    # Trapped trader score (simplified: close reversal within 3 bars)
    if not quick:
        in_upper_q = (close > open_ + 0.75 * bar_range).astype(np.float64)
        in_lower_q = (close < open_ - 0.75 * bar_range + low).astype(np.float64)
        # Rough: count bars where close was in upper quartile followed by close in lower within 3
        trapped = np.zeros((n_bars, n_symbols))
        for lag in [1, 2, 3]:
            if lag < n_bars:
                upper_then_lower = np.zeros((n_bars, n_symbols))
                upper_then_lower[lag:] = in_upper_q[:-lag] * in_lower_q[lag:]
                lower_then_upper = np.zeros((n_bars, n_symbols))
                lower_then_upper[lag:] = in_lower_q[:-lag] * in_upper_q[lag:]
                trapped += upper_then_lower + lower_then_upper
        _store("trapped_trader_score", _rolling_sum_2d(trapped, 13))

    # Overnight momentum
    gp = features.get("gap_pct")
    if gp is not None:
        _store("overnight_momentum", gp * volume_z)

    # Multi-bar absorption
    bv = body / safe_range * volume
    _store("multi_bar_absorption", _rolling_sum_2d(bv, 5) / np.where(_rolling_sum_2d(volume, 5) > 0, _rolling_sum_2d(volume, 5), 1e-10))

    elapsed = time.time() - t0
    logger.info("Feature engineering complete: %d features in %.1fs (%.1f MB)",
                len(features), elapsed,
                sum(f.nbytes for f in features.values()) / 1024**2)

    return features


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: FEATURE SELECTION PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def _spearman_rank(x):
    """Rank array (handles NaN by leaving them out). Returns ranks as float."""
    out = np.empty_like(x, dtype=np.float64)
    out[:] = np.nan
    valid = ~np.isnan(x)
    if valid.sum() < 2:
        return out
    vals = x[valid]
    ranks = np.argsort(np.argsort(vals)).astype(np.float64)
    out[valid] = ranks
    return out


def fama_macbeth_ic(feature, fwd_ret, bar_mask):
    """Compute Fama-MacBeth IC (cross-sectional Spearman at each bar, then average).

    Args:
        feature: (n_bars, n_symbols) array
        fwd_ret: (n_bars, n_symbols) array
        bar_mask: (n_bars,) bool array — which bars to include

    Returns: (mean_ic, t_stat, n_cross_sections)
    """
    ics = []
    bar_indices = np.where(bar_mask)[0]

    for t in bar_indices:
        f_row = feature[t]
        r_row = fwd_ret[t]
        valid = ~np.isnan(f_row) & ~np.isnan(r_row)
        n_valid = valid.sum()
        if n_valid < 20:
            continue

        f_ranks = _spearman_rank(f_row)
        r_ranks = _spearman_rank(r_row)
        valid2 = ~np.isnan(f_ranks) & ~np.isnan(r_ranks)
        if valid2.sum() < 20:
            continue

        fr = f_ranks[valid2]
        rr = r_ranks[valid2]
        n = len(fr)
        mean_f = fr.mean()
        mean_r = rr.mean()
        cov = np.mean((fr - mean_f) * (rr - mean_r))
        std_f = np.std(fr)
        std_r = np.std(rr)
        if std_f > 0 and std_r > 0:
            ic = cov / (std_f * std_r)
            ics.append(ic)

    if len(ics) < 5:
        return 0.0, 0.0, len(ics)

    ics = np.array(ics)
    mean_ic = np.mean(ics)
    std_ic = np.std(ics, ddof=1)
    if std_ic > 0:
        t_stat = mean_ic / (std_ic / np.sqrt(len(ics)))
    else:
        t_stat = 0.0

    return float(mean_ic), float(t_stat), len(ics)


def binned_mutual_information(feature, target, bar_mask, n_bins=10):
    """Compute binned MI between feature and target using cross-sectional stacking.

    Uses chi-squared approximation for significance.
    Returns: (mi, is_significant, n_samples)
    """
    # Stack all valid cross-sectional observations
    bar_indices = np.where(bar_mask)[0]
    f_vals = []
    t_vals = []

    for t in bar_indices:
        valid = ~np.isnan(feature[t]) & ~np.isnan(target[t])
        if valid.sum() < 20:
            continue
        f_vals.append(feature[t, valid])
        t_vals.append(target[t, valid])

    if len(f_vals) == 0:
        return 0.0, False, 0

    f_all = np.concatenate(f_vals)
    t_all = np.concatenate(t_vals)
    n = len(f_all)

    if n < 100:
        return 0.0, False, n

    # Quantile-bin both
    f_bins = np.clip(
        np.searchsorted(np.percentile(f_all, np.linspace(0, 100, n_bins + 1)[1:-1]), f_all),
        0, n_bins - 1
    )
    t_bins = np.clip(
        np.searchsorted(np.percentile(t_all, np.linspace(0, 100, n_bins + 1)[1:-1]), t_all),
        0, n_bins - 1
    )

    # Contingency table
    contingency = np.zeros((n_bins, n_bins), dtype=np.float64)
    for i in range(n):
        contingency[f_bins[i], t_bins[i]] += 1

    # MI = sum p(i,j) * log(p(i,j) / (p(i)*p(j)))
    p_joint = contingency / n
    p_f = p_joint.sum(axis=1)
    p_t = p_joint.sum(axis=0)

    mi = 0.0
    for i in range(n_bins):
        for j in range(n_bins):
            if p_joint[i, j] > 0 and p_f[i] > 0 and p_t[j] > 0:
                mi += p_joint[i, j] * np.log(p_joint[i, j] / (p_f[i] * p_t[j]))

    # Chi-squared significance: 2*N*MI ~ chi2(df=(n_bins-1)^2)
    df = (n_bins - 1) ** 2
    chi2_stat = 2 * n * mi

    # Chi-squared CDF approximation (Wilson-Hilferty)
    # P(X > x) where X ~ chi2(df)
    z = ((chi2_stat / df) ** (1/3) - (1 - 2/(9*df))) / np.sqrt(2/(9*df))
    # z > 1.645 means p < 0.05
    is_significant = z > 1.645

    return float(mi), bool(is_significant), n


def permutation_mi_test(feature, target, bar_mask, observed_mi, n_perms=100, n_bins=10):
    """Permutation test for MI significance. Returns p-value."""
    count_above = 0
    bar_indices = np.where(bar_mask)[0]

    # Stack observations once
    f_vals, t_vals = [], []
    for t in bar_indices:
        valid = ~np.isnan(feature[t]) & ~np.isnan(target[t])
        if valid.sum() < 20:
            continue
        f_vals.append(feature[t, valid])
        t_vals.append(target[t, valid])

    if len(f_vals) == 0:
        return 1.0

    f_all = np.concatenate(f_vals)
    t_all = np.concatenate(t_vals)
    n = len(f_all)

    f_pctiles = np.percentile(f_all, np.linspace(0, 100, n_bins + 1)[1:-1])
    t_pctiles = np.percentile(t_all, np.linspace(0, 100, n_bins + 1)[1:-1])
    f_bins = np.clip(np.searchsorted(f_pctiles, f_all), 0, n_bins - 1)
    t_bins = np.clip(np.searchsorted(t_pctiles, t_all), 0, n_bins - 1)

    rng = np.random.default_rng(42)
    for _ in range(n_perms):
        # Shuffle target bins
        t_shuf = rng.permutation(t_bins)
        contingency = np.zeros((n_bins, n_bins), dtype=np.float64)
        for i in range(n):
            contingency[f_bins[i], t_shuf[i]] += 1

        p_joint = contingency / n
        p_f = p_joint.sum(axis=1)
        p_t = p_joint.sum(axis=0)
        mi = 0.0
        for i in range(n_bins):
            for j in range(n_bins):
                if p_joint[i, j] > 0 and p_f[i] > 0 and p_t[j] > 0:
                    mi += p_joint[i, j] * np.log(p_joint[i, j] / (p_f[i] * p_t[j]))
        if mi >= observed_mi:
            count_above += 1

    return (count_above + 1) / (n_perms + 1)


def temporal_stability_check(feature, fwd_ret, bar_mask, n_windows=4):
    """Check if feature IC is stable across sub-windows of training period.

    Returns: (n_stable_windows, total_windows)
    """
    bar_indices = np.where(bar_mask)[0]
    if len(bar_indices) < n_windows * 10:
        return 0, n_windows

    chunk_size = len(bar_indices) // n_windows
    stable_count = 0

    for w in range(n_windows):
        start = w * chunk_size
        end = start + chunk_size if w < n_windows - 1 else len(bar_indices)
        sub_mask = np.zeros(feature.shape[0], dtype=bool)
        sub_mask[bar_indices[start:end]] = True

        _, t_stat, n_cs = fama_macbeth_ic(feature, fwd_ret, sub_mask)
        if abs(t_stat) > STABILITY_TSTAT_THRESHOLD:
            stable_count += 1

    return stable_count, n_windows


def select_features(features, fwd_ret, bar_mask, horizon, fold_idx):
    """Full feature selection pipeline for one fold/horizon.

    Returns: list of (feature_name, ic, mi, ic_sign) tuples for selected features.
    """
    t0 = time.time()
    feature_names = sorted(features.keys())
    n_features = len(feature_names)
    logger.info("    [Fold %d, h=%d] Feature selection: %d candidates",
                fold_idx, horizon, n_features)

    # Step 1: Fama-MacBeth IC gate
    ic_results = {}
    for name in feature_names:
        mean_ic, t_stat, n_cs = fama_macbeth_ic(features[name], fwd_ret, bar_mask)
        if abs(t_stat) > IC_TSTAT_THRESHOLD:
            ic_results[name] = (mean_ic, t_stat, n_cs)

    logger.info("      IC gate: %d / %d pass (|t| > %.1f)",
                len(ic_results), n_features, IC_TSTAT_THRESHOLD)

    # Step 2: MI gate (catches non-linear relationships IC misses)
    mi_results = {}
    for name in feature_names:
        if name in ic_results:
            continue  # already passed IC
        mi, sig, n_samp = binned_mutual_information(features[name], fwd_ret, bar_mask)
        if sig:
            mi_results[name] = (mi, n_samp)

    logger.info("      MI gate: %d additional features pass chi-squared test",
                len(mi_results))

    # Union of IC and MI survivors
    candidates = {}
    for name, (ic, t, n_cs) in ic_results.items():
        mi, _, _ = binned_mutual_information(features[name], fwd_ret, bar_mask)
        candidates[name] = {"ic": ic, "t_stat": t, "mi": mi}

    for name, (mi, n_samp) in mi_results.items():
        ic, t, n_cs = fama_macbeth_ic(features[name], fwd_ret, bar_mask)
        candidates[name] = {"ic": ic, "t_stat": t, "mi": mi}

    if not candidates:
        logger.info("      No features survive IC or MI gates")
        return []

    # Step 3: Permutation validation for top 30 MI features
    mi_sorted = sorted(candidates.items(), key=lambda x: -x[1]["mi"])[:30]
    to_remove = []
    for name, info in mi_sorted:
        if info["mi"] > 0:
            p_val = permutation_mi_test(features[name], fwd_ret, bar_mask, info["mi"])
            if p_val > MI_SIGNIFICANCE_LEVEL:
                to_remove.append(name)
                candidates[name]["mi_pval"] = p_val
    for name in to_remove:
        # Only remove if it didn't also pass IC gate
        if name not in ic_results:
            del candidates[name]

    logger.info("      After permutation validation: %d candidates", len(candidates))

    # Step 4: Temporal stability
    stable_candidates = {}
    for name, info in candidates.items():
        n_stable, n_total = temporal_stability_check(features[name], fwd_ret, bar_mask)
        if n_stable >= STABILITY_MIN_WINDOWS:
            info["stability"] = n_stable
            stable_candidates[name] = info

    logger.info("      Temporal stability: %d / %d pass (%d/%d windows)",
                len(stable_candidates), len(candidates),
                STABILITY_MIN_WINDOWS, 4)

    if not stable_candidates:
        return []

    # Step 5: Redundancy elimination
    names = sorted(stable_candidates.keys())
    n_cand = len(names)
    if n_cand > 1:
        # Compute pairwise correlation matrix
        # Stack cross-sectional means for correlation
        stacked = np.column_stack([
            np.nanmean(features[name], axis=1) for name in names
        ])
        # Pearson correlation on temporal profiles
        corr_mat = np.corrcoef(stacked.T)

        removed = set()
        for i in range(n_cand):
            if names[i] in removed:
                continue
            for j in range(i + 1, n_cand):
                if names[j] in removed:
                    continue
                if abs(corr_mat[i, j]) > REDUNDANCY_CORR_THRESHOLD:
                    # Keep the one with higher MI
                    mi_i = stable_candidates[names[i]]["mi"]
                    mi_j = stable_candidates[names[j]]["mi"]
                    loser = names[j] if mi_i >= mi_j else names[i]
                    removed.add(loser)

        for name in removed:
            del stable_candidates[name]

    logger.info("      After redundancy elimination: %d features", len(stable_candidates))

    # Step 6: Conditional MI (interaction features)
    top_features = sorted(stable_candidates.items(), key=lambda x: -abs(x[1]["ic"]))[:15]
    interactions = []

    if len(top_features) >= 2:
        for i in range(len(top_features)):
            for j in range(i + 1, len(top_features)):
                name_i, info_i = top_features[i]
                name_j, info_j = top_features[j]

                # Conditional MI: MI(fi; target | fj) via 3D contingency
                # Simplified: compute MI of fi*fj with target
                f_product = features[name_i] * features[name_j]
                mi_product, sig_product, _ = binned_mutual_information(
                    f_product, fwd_ret, bar_mask, n_bins=8
                )

                if sig_product and mi_product > max(info_i["mi"], info_j["mi"]) * 0.8:
                    inter_name = f"{name_i}__x__{name_j}"
                    interactions.append((inter_name, f_product, mi_product))

                    # Also try sign interaction
                    f_sign = features[name_i] * np.sign(features[name_j])
                    mi_sign, sig_sign, _ = binned_mutual_information(
                        f_sign, fwd_ret, bar_mask, n_bins=8
                    )
                    if sig_sign and mi_sign > info_i["mi"] * 0.8:
                        inter_name2 = f"{name_i}__xsign__{name_j}"
                        interactions.append((inter_name2, f_sign, mi_sign))

                if len(interactions) >= 30:
                    break
            if len(interactions) >= 30:
                break

    logger.info("      Generated %d interaction features", len(interactions))

    # Build final selection list
    selected = []
    for name, info in stable_candidates.items():
        selected.append((name, info["ic"], info["mi"], np.sign(info["ic"])))

    for inter_name, f_arr, mi in interactions:
        ic, t, _ = fama_macbeth_ic(f_arr, fwd_ret, bar_mask)
        selected.append((inter_name, ic, mi, np.sign(ic)))

    elapsed = time.time() - t0
    logger.info("    Feature selection complete: %d features in %.1fs",
                len(selected), elapsed)

    return selected, interactions


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: MODEL & SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_composite_model(features, selected, interactions, bar_mask):
    """Build IC-weighted composite z-score model.

    Returns: (n_bars, n_symbols) composite scores for bars in bar_mask.
    """
    n_bars, n_symbols = next(iter(features.values())).shape
    composite = np.zeros((n_bars, n_symbols), dtype=np.float64)
    total_weight = 0.0

    # Named features
    for name, ic, mi, sign in selected:
        if "__x__" in name or "__xsign__" in name:
            continue  # handled below

        f = features[name].astype(np.float64)
        # Cross-sectional z-score at each bar
        mean_xs = np.nanmean(f, axis=1, keepdims=True)
        std_xs = np.nanstd(f, axis=1, keepdims=True)
        safe_std = np.where(std_xs > 0, std_xs, 1e-10)
        z = (f - mean_xs) / safe_std

        w = ic  # sign already in ic
        composite += w * z
        total_weight += abs(w)

    # Interaction features
    for inter_name, f_arr, mi in interactions:
        matched = [s for s in selected if s[0] == inter_name]
        if not matched:
            continue
        _, ic, _, sign = matched[0]

        f = f_arr.astype(np.float64)
        mean_xs = np.nanmean(f, axis=1, keepdims=True)
        std_xs = np.nanstd(f, axis=1, keepdims=True)
        safe_std = np.where(std_xs > 0, std_xs, 1e-10)
        z = (f - mean_xs) / safe_std

        w = ic
        composite += w * z
        total_weight += abs(w)

    if total_weight > 0:
        composite /= total_weight

    return composite


def generate_signals(composite, bar_mask, k_pct, symbols):
    """Generate LONG/SHORT signals from composite scores.

    At each bar, top k_pct% → LONG, bottom k_pct% → SHORT.
    Returns list of Signal objects.
    """
    n_bars, n_symbols = composite.shape
    signals = []

    bar_indices = np.where(bar_mask)[0]
    for t in bar_indices:
        row = composite[t]
        valid = ~np.isnan(row)
        n_valid = valid.sum()
        if n_valid < 20:
            continue

        # Rank
        vals = row.copy()
        vals[~valid] = np.nan
        ranks = np.full(n_symbols, np.nan)
        order = np.argsort(vals[valid])
        ranks_valid = np.empty(valid.sum())
        ranks_valid[order] = np.arange(valid.sum()) / (valid.sum() - 1)

        idx_valid = np.where(valid)[0]
        for i, idx in enumerate(idx_valid):
            ranks[idx] = ranks_valid[i]

        threshold_high = 1.0 - k_pct / 100.0
        threshold_low = k_pct / 100.0

        for s_idx in range(n_symbols):
            if np.isnan(ranks[s_idx]):
                continue
            if ranks[s_idx] >= threshold_high:
                score = (ranks[s_idx] - threshold_high) / (1.0 - threshold_high + 1e-10)
                signals.append(Signal(
                    symbol=symbols[s_idx],
                    direction="long",
                    ts_idx=t,
                    score=score,
                ))
            elif ranks[s_idx] <= threshold_low:
                score = (threshold_low - ranks[s_idx]) / (threshold_low + 1e-10)
                signals.append(Signal(
                    symbol=symbols[s_idx],
                    direction="short",
                    ts_idx=t,
                    score=score,
                ))

    return signals


def optimize_k(composite, fwd_ret, bar_mask, symbols, ohlcv_data, horizon, k_candidates=TOP_K_CANDIDATES):
    """Find best K% threshold using mini train/val split within training data.

    Returns: best_k
    """
    bar_indices = np.where(bar_mask)[0]
    n_bars_train = len(bar_indices)
    split_idx = int(n_bars_train * 0.7)

    mini_train_bars = bar_indices[:split_idx]
    mini_val_bars = bar_indices[split_idx:]

    # Build val mask
    val_mask = np.zeros(composite.shape[0], dtype=bool)
    val_mask[mini_val_bars] = True

    cfg = HORIZON_CONFIGS[horizon]
    exit_cfg = _make_exit_config(cfg)

    best_k = k_candidates[len(k_candidates) // 2]  # default middle
    best_pf = 0.0

    for k in k_candidates:
        signals = generate_signals(composite, val_mask, k, symbols)
        if len(signals) < 5:
            continue

        trades = simulate_genesis_trades(
            signals, ohlcv_data, cfg["stop_atr_mult"],
            cfg["time_stop_bars"], BASE_SLIPPAGE_1H, exit_cfg
        )
        if len(trades) < 3:
            continue

        avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
        if avg_pf > best_pf:
            best_pf = avg_pf
            best_k = k

    logger.info("      K optimization: best K=%d%% (avg daily PF=%.3f)", best_k, best_pf)
    return best_k


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATION (adapted from v3)
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_atr_1d(highs, lows, closes, n=14):
    """ATR for 1D arrays."""
    length = len(closes)
    tr = np.empty(length)
    tr[0] = highs[0] - lows[0]
    prev_close = np.empty(length)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]
    hl = highs - lows
    hc = np.abs(highs - prev_close)
    lc = np.abs(lows - prev_close)
    tr = np.maximum(hl, np.maximum(hc, lc))
    tr[0] = highs[0] - lows[0]
    atr = np.zeros(length)
    cumtr = np.cumsum(tr)
    atr[n-1:] = (cumtr[n-1:] - np.concatenate([[0], cumtr[:-n]])) / n
    return atr


def simulate_genesis_trades(signals, ohlcv_data, stop_atr_mult, time_stop_bars,
                            slippage_bps, exit_config=None):
    """Simulate signals to trades. Reuses v3 exit logic."""
    if exit_config is None:
        exit_config = ExitConfig()
    results = []

    for sig in signals:
        key = (sig.symbol, '1h')
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        highs = data["high"]
        lows = data["low"]
        opens = data["open"]
        n = len(closes)

        i = sig.ts_idx
        if i + 1 >= n or i < 14:
            continue

        # ATR at signal bar
        atr = _rolling_atr_1d(highs[:i+1], lows[:i+1], closes[:i+1], 14)[-1]
        if atr <= 0:
            continue

        # Entry at next bar open + slippage
        entry_raw = opens[i + 1]
        if sig.direction == "long":
            entry = entry_raw * (1 + slippage_bps / 10000)
            stop = entry - stop_atr_mult * atr
        else:
            entry = entry_raw * (1 - slippage_bps / 10000)
            stop = entry + stop_atr_mult * atr
            if entry < MIN_SHORT_PRICE:
                continue

        if stop <= 0 or entry <= 0:
            continue
        r_value = abs(entry - stop)
        if r_value <= 0 or r_value / entry > 0.10:
            continue
        if 2 * entry * FEE_BPS / 10000 > 0.3 * r_value:
            continue

        max_idx = min(i + 2 + time_stop_bars + 20, n)  # extra buffer beyond time stop
        hwm = None
        half_r_touched = False
        bars_held = 0
        exit_reason = "TIME_STOP"
        exit_price = closes[min(max_idx - 1, n - 1)]

        for j in range(i + 2, max_idx):
            bars_held += 1
            bar = {"open": opens[j], "high": highs[j], "low": lows[j], "close": closes[j]}
            result, new_hwm, new_half_r = check_exit_simple(
                side=sig.direction, bar=bar,
                entry_price=entry, stop_price=stop,
                tp1_price=None, tp2_price=None,
                r_value=r_value, bars_held=bars_held,
                max_bars=time_stop_bars, high_water_mark=hwm,
                half_r_touched=half_r_touched, config=exit_config,
            )
            hwm = new_hwm
            half_r_touched = new_half_r
            if result:
                exit_reason, exit_price = result
                break

        # PnL with exit slippage
        if exit_reason != "STOP_LOSS":
            if sig.direction == "long":
                ep = exit_price * (1 - slippage_bps / 10000)
            else:
                ep = exit_price * (1 + slippage_bps / 10000)
        else:
            ep = exit_price

        pos_size = POSITION_SIZE_USD / entry
        if sig.direction == "long":
            pnl_gross = (ep - entry) * pos_size
        else:
            pnl_gross = (entry - ep) * pos_size
        fees = (entry + ep) * pos_size * FEE_BPS / 10000
        pnl_net = pnl_gross - fees
        r_mult = pnl_net / (r_value * pos_size) if r_value * pos_size > 0 else 0

        results.append(TradeResult(
            symbol=sig.symbol, direction=sig.direction,
            ts_idx=sig.ts_idx, entry_price=entry, exit_price=ep,
            pnl=pnl_net, r_mult=r_mult, exit_reason=exit_reason, bars_held=bars_held,
        ))

    return results


def compute_avg_daily_pf(trades, ohlcv_data, symbols):
    """Compute average daily profit factor. PF per day, then average."""
    if not trades:
        return 0.0

    # Get timestamps for each trade
    ts_grid = ohlcv_data[(symbols[0], '1h')]["ts"]

    # Group trades by day
    daily = defaultdict(lambda: {"wins": 0.0, "losses": 0.0})
    for t in trades:
        ts = ts_grid[t.ts_idx]
        day = int(ts // 86400)
        if t.pnl > 0:
            daily[day]["wins"] += t.pnl
        elif t.pnl < 0:
            daily[day]["losses"] += abs(t.pnl)

    if not daily:
        return 0.0

    pfs = []
    for day_data in daily.values():
        w = day_data["wins"]
        l = day_data["losses"]
        if l > 0:
            pfs.append(w / l)
        elif w > 0:
            pfs.append(10.0)  # cap at 10 for days with only wins
        # Skip days with no trades

    if not pfs:
        return 0.0

    return float(np.mean(pfs))


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_folds(ts_grid, n_folds=N_FOLDS, holdout_days=HOLDOUT_DAYS, embargo_days=EMBARGO_DAYS):
    """Build expanding window folds from timestamp grid.

    Returns: list of (train_mask, test_mask) bool arrays, holdout_mask
    """
    # Convert to days since start
    day_start = ts_grid[0] // 86400
    day_end = ts_grid[-1] // 86400
    total_days = int(day_end - day_start) + 1

    logger.info("  Total span: %d days (%s to %s)",
                total_days,
                datetime.fromtimestamp(ts_grid[0], tz=timezone.utc).strftime("%Y-%m-%d"),
                datetime.fromtimestamp(ts_grid[-1], tz=timezone.utc).strftime("%Y-%m-%d"))

    # Guard: need enough days for at least 1 fold + holdout
    min_required = holdout_days + 2 * 5 + embargo_days  # holdout + train + test + embargo
    if total_days < min_required:
        logger.error("  Insufficient data: %d days available, need >= %d (holdout=%d + folds). "
                     "Increase --days or check DB has 1h OHLCV data.", total_days, min_required, holdout_days)
        return [], np.zeros(len(ts_grid), dtype=bool)

    # Reserve holdout at the end
    holdout_start_day = total_days - holdout_days
    train_test_days = holdout_start_day

    # Divide train_test into folds
    # Expanding window: each fold adds ~test_size days of training
    test_size = train_test_days // (n_folds + 1)  # first chunk is initial training only
    if test_size < 5:
        test_size = 5

    folds = []
    for fold_idx in range(n_folds):
        train_end_day = (fold_idx + 1) * test_size + test_size  # expanding
        test_start_day = train_end_day + embargo_days
        test_end_day = test_start_day + test_size

        if test_end_day > holdout_start_day:
            test_end_day = holdout_start_day

        if test_start_day >= holdout_start_day:
            break

        # Convert days to bar masks
        train_mask = np.zeros(len(ts_grid), dtype=bool)
        test_mask = np.zeros(len(ts_grid), dtype=bool)

        for i, ts in enumerate(ts_grid):
            day = int(ts // 86400 - day_start)
            if day < train_end_day:
                train_mask[i] = True
            elif test_start_day <= day < test_end_day:
                test_mask[i] = True

        train_bars = train_mask.sum()
        test_bars = test_mask.sum()
        if train_bars < 100 or test_bars < 50:
            continue

        folds.append((train_mask, test_mask))
        logger.info("    Fold %d: train days [0, %d) = %d bars, test days [%d, %d) = %d bars",
                    fold_idx, train_end_day, train_bars,
                    test_start_day, test_end_day, test_bars)

    # Holdout mask
    holdout_mask = np.zeros(len(ts_grid), dtype=bool)
    for i, ts in enumerate(ts_grid):
        day = int(ts // 86400 - day_start)
        if day >= holdout_start_day:
            holdout_mask[i] = True

    logger.info("    Holdout: days [%d, %d) = %d bars",
                holdout_start_day, total_days, holdout_mask.sum())

    return folds, holdout_mask


def _make_exit_config(cfg):
    """Build ExitConfig from horizon config dict, compatible with any ExitConfig version."""
    ec = ExitConfig()
    for key in ("trail_breakeven_r", "trail_activation_r", "trail_distance_r"):
        if hasattr(ec, key):
            setattr(ec, key, cfg[key])
    return ec


def run_walk_forward(features, fwd_ret, folds, horizon, symbols, ohlcv_data):
    """Run walk-forward validation for one horizon.

    Returns: list of FoldResult, list of per-fold feature sets
    """
    cfg = HORIZON_CONFIGS[horizon]
    exit_cfg = _make_exit_config(cfg)

    fold_results = []
    fold_features_list = []

    for fold_idx, (train_mask, test_mask) in enumerate(folds):
        logger.info("  [h=%d] Fold %d: selecting features...", horizon, fold_idx)
        t0 = time.time()

        # Feature selection on training data
        selection_result = select_features(features, fwd_ret[horizon], train_mask, horizon, fold_idx)
        if not selection_result or (isinstance(selection_result, tuple) and not selection_result[0]):
            logger.info("    Fold %d: no features selected, skipping", fold_idx)
            fold_results.append(FoldResult(
                fold_idx=fold_idx, horizon=horizon, n_features=0,
                feature_names=[], n_trades=0, avg_daily_pf=0.0,
                aggregate_pf=0.0, win_rate=0.0, total_pnl=0.0, best_k=10,
            ))
            fold_features_list.append(set())
            continue

        selected, interactions = selection_result
        feature_names = [s[0] for s in selected]
        fold_features_list.append(set(feature_names))

        # Build composite model on training data
        composite = build_composite_model(features, selected, interactions, train_mask)

        # Optimize K on training data
        best_k = optimize_k(composite, fwd_ret[horizon], train_mask, symbols, ohlcv_data, horizon)

        # Generate signals on TEST data
        test_signals = generate_signals(composite, test_mask, best_k, symbols)
        logger.info("    Fold %d: %d signals on test period", fold_idx, len(test_signals))

        # Simulate trades
        trades = simulate_genesis_trades(
            test_signals, ohlcv_data, cfg["stop_atr_mult"],
            cfg["time_stop_bars"], BASE_SLIPPAGE_1H, exit_cfg,
        )

        # Compute metrics
        if trades:
            avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
            total_pnl = sum(t.pnl for t in trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            win_rate = wins / len(trades)
            gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
            gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
            agg_pf = gross_wins / gross_losses if gross_losses > 0 else 10.0
        else:
            avg_pf = 0.0
            total_pnl = 0.0
            win_rate = 0.0
            agg_pf = 0.0

        elapsed = time.time() - t0
        logger.info("    Fold %d: %d trades, avg daily PF=%.3f, agg PF=%.3f, WR=%.1f%%, PnL=$%.2f (%.1fs)",
                    fold_idx, len(trades), avg_pf, agg_pf, win_rate * 100, total_pnl, elapsed)

        fold_results.append(FoldResult(
            fold_idx=fold_idx, horizon=horizon, n_features=len(selected),
            feature_names=feature_names, n_trades=len(trades),
            avg_daily_pf=avg_pf, aggregate_pf=agg_pf, win_rate=win_rate,
            total_pnl=total_pnl, best_k=best_k,
        ))

    return fold_results, fold_features_list


def regime_shift_diagnostic(fold_features_list):
    """Compute Jaccard similarity between fold feature sets."""
    n = len(fold_features_list)
    report = []

    # Pairwise Jaccard
    for i in range(n):
        for j in range(i + 1, n):
            a = fold_features_list[i]
            b = fold_features_list[j]
            if not a and not b:
                continue
            overlap = len(a & b)
            union = len(a | b)
            jaccard = overlap / union if union > 0 else 0
            report.append({
                "fold_i": i, "fold_j": j,
                "overlap": overlap, "union": union, "jaccard": jaccard,
            })

    # Features in ALL folds vs unique to one
    all_features = set()
    for fs in fold_features_list:
        all_features |= fs

    in_all = set.intersection(*fold_features_list) if all(fold_features_list) else set()
    in_one = set()
    for f in all_features:
        count = sum(1 for fs in fold_features_list if f in fs)
        if count == 1:
            in_one.add(f)

    return report, in_all, in_one


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: HOLDOUT & DSR
# ═══════════════════════════════════════════════════════════════════════════════

def deflated_sharpe_ratio(observed_sr, n_trials, n_observations, skew=0, kurtosis=3):
    """Harvey, Liu, Zhu (2016) — adjusts for multiple testing."""
    if n_trials <= 1 or n_observations <= 1:
        return 0.0

    euler_gamma = 0.5772156649
    log_n = math.log(n_trials)
    if log_n <= 0:
        return 0.0
    e_max_sr = (math.sqrt(2 * log_n) * (1 - euler_gamma / log_n)
                + euler_gamma / (2 * math.sqrt(2 * log_n)))

    se_sr = math.sqrt(
        (1 + 0.5 * observed_sr**2 - skew * observed_sr
         + (kurtosis - 3) / 4 * observed_sr**2)
        / n_observations
    )
    if se_sr <= 0:
        return 0.0

    return float((observed_sr - e_max_sr) / se_sr)


def run_holdout(features, fwd_ret, folds, holdout_mask, horizon, symbols, ohlcv_data,
                n_total_features_tested):
    """Run holdout evaluation for one horizon.

    Uses all pre-holdout data for feature selection + model, tests on holdout.
    """
    cfg = HORIZON_CONFIGS[horizon]
    exit_cfg = _make_exit_config(cfg)

    # Full training mask = everything before holdout
    full_train_mask = ~holdout_mask & np.any(
        np.column_stack([f[0] | f[1] for f in folds]), axis=1
    )
    # Simpler: just use all bars before holdout
    full_train_mask = np.zeros(len(holdout_mask), dtype=bool)
    holdout_start = np.where(holdout_mask)[0]
    if len(holdout_start) > 0:
        full_train_mask[:holdout_start[0]] = True
    else:
        return None

    logger.info("  [h=%d] Holdout: feature selection on %d training bars...",
                horizon, full_train_mask.sum())

    selection_result = select_features(features, fwd_ret[horizon], full_train_mask, horizon, -1)
    if not selection_result or (isinstance(selection_result, tuple) and not selection_result[0]):
        logger.info("  Holdout: no features selected")
        return None

    selected, interactions = selection_result
    composite = build_composite_model(features, selected, interactions, full_train_mask)
    best_k = optimize_k(composite, fwd_ret[horizon], full_train_mask, symbols, ohlcv_data, horizon)

    # Generate signals on holdout
    holdout_signals = generate_signals(composite, holdout_mask, best_k, symbols)
    logger.info("  Holdout: %d signals generated", len(holdout_signals))

    # Test at 1.5× slippage
    slippage_15x = BASE_SLIPPAGE_1H * 1.5
    trades = simulate_genesis_trades(
        holdout_signals, ohlcv_data, cfg["stop_atr_mult"],
        cfg["time_stop_bars"], slippage_15x, exit_cfg,
    )

    if not trades:
        logger.info("  Holdout: 0 trades")
        return None

    avg_pf = compute_avg_daily_pf(trades, ohlcv_data, symbols)
    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / len(trades)
    gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses = sum(abs(t.pnl) for t in trades if t.pnl < 0)
    agg_pf = gross_wins / gross_losses if gross_losses > 0 else 10.0

    # Sharpe ratio for DSR
    pnl_arr = np.array([t.pnl for t in trades])
    n_obs = len(pnl_arr)
    mean_pnl = np.mean(pnl_arr)
    std_pnl = np.std(pnl_arr, ddof=1)
    sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

    # Skew and kurtosis for DSR
    if n_obs > 2 and std_pnl > 0:
        z_pnl = (pnl_arr - mean_pnl) / std_pnl
        m3 = np.mean(z_pnl**3)
        m4 = np.mean(z_pnl**4)
        skew = float(m3 * (n_obs * (n_obs - 1))**0.5 / (n_obs - 2)) if n_obs > 2 else 0
        kurt = float(m4)
    else:
        skew, kurt = 0.0, 3.0

    dsr = deflated_sharpe_ratio(sharpe, n_total_features_tested, n_obs, skew, kurt)

    logger.info("  Holdout (1.5x slip): %d trades, avg daily PF=%.3f, agg PF=%.3f, "
                "WR=%.1f%%, Sharpe=%.3f, DSR=%.3f, PnL=$%.2f",
                len(trades), avg_pf, agg_pf, win_rate * 100, sharpe, dsr, total_pnl)

    feature_names = [s[0] for s in selected]
    return {
        "avg_daily_pf": avg_pf,
        "aggregate_pf": agg_pf,
        "dsr": dsr,
        "n_trades": len(trades),
        "sharpe": sharpe,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "features": feature_names,
        "best_k": best_k,
        "trades": trades,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 7: REPORTING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_feature_decile_analysis(feature, fwd_ret, bar_mask, n_deciles=10):
    """Compute mean forward return per feature decile."""
    bar_indices = np.where(bar_mask)[0]
    decile_returns = [[] for _ in range(n_deciles)]

    for t in bar_indices:
        valid = ~np.isnan(feature[t]) & ~np.isnan(fwd_ret[t])
        if valid.sum() < 20:
            continue
        f_vals = feature[t, valid]
        r_vals = fwd_ret[t, valid]

        pctiles = np.percentile(f_vals, np.linspace(0, 100, n_deciles + 1))
        for d in range(n_deciles):
            lo = pctiles[d]
            hi = pctiles[d + 1] if d < n_deciles - 1 else np.inf
            if d == 0:
                mask = f_vals <= hi
            elif d == n_deciles - 1:
                mask = f_vals > lo
            else:
                mask = (f_vals > lo) & (f_vals <= hi)
            if mask.sum() > 0:
                decile_returns[d].append(np.mean(r_vals[mask]))

    return [float(np.mean(d)) if d else 0.0 for d in decile_returns]


def generate_report(horizon_results, features, fwd_ret, holdout_mask, ts_grid,
                    regime_reports, total_features, elapsed_total):
    """Generate the final report dict."""
    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed_total,
        "total_base_features": total_features,
        "horizons": {},
    }

    for hr in horizon_results:
        h = hr.horizon
        h_report = {
            "walk_forward": {
                "avg_daily_pf": hr.avg_daily_pf,
                "consistency": hr.consistency,
                "total_trades": hr.total_trades,
                "folds": [asdict(fr) for fr in hr.fold_results],
            },
            "holdout": {
                "avg_daily_pf": hr.holdout_avg_daily_pf,
                "aggregate_pf": hr.holdout_aggregate_pf,
                "dsr": hr.holdout_dsr,
                "n_trades": hr.holdout_n_trades,
                "features": hr.holdout_features,
            },
            "verdict": "PENDING",
        }

        # Verdict logic
        wf_pass = (hr.avg_daily_pf > 1.0 and hr.consistency >= MIN_FOLD_CONSISTENCY
                    and hr.total_trades >= MIN_TRADES_TOTAL)
        if not wf_pass:
            h_report["verdict"] = "FAIL_WALK_FORWARD"
            reasons = []
            if hr.avg_daily_pf <= 1.0:
                reasons.append(f"avg_daily_pf={hr.avg_daily_pf:.3f} <= 1.0")
            if hr.consistency < MIN_FOLD_CONSISTENCY:
                reasons.append(f"consistency={hr.consistency:.2f} < {MIN_FOLD_CONSISTENCY}")
            if hr.total_trades < MIN_TRADES_TOTAL:
                reasons.append(f"trades={hr.total_trades} < {MIN_TRADES_TOTAL}")
            h_report["fail_reasons"] = reasons
        elif hr.holdout_avg_daily_pf <= 1.0:
            h_report["verdict"] = "FAIL_HOLDOUT"
        elif hr.holdout_dsr <= 0:
            h_report["verdict"] = "FAIL_DSR"
            h_report["dsr_note"] = f"DSR={hr.holdout_dsr:.3f} <= 0"
        else:
            h_report["verdict"] = "CONFIRMED"

        report["horizons"][f"h{h}"] = h_report

    # Regime shift diagnostics
    report["regime_diagnostics"] = {}
    for h, (pairwise, in_all, in_one) in regime_reports.items():
        report["regime_diagnostics"][f"h{h}"] = {
            "pairwise_jaccard": pairwise,
            "features_in_all_folds": sorted(in_all),
            "features_in_one_fold_only": sorted(in_one),
            "avg_jaccard": float(np.mean([p["jaccard"] for p in pairwise])) if pairwise else 0.0,
        }

    # Feature decile analysis for holdout features
    report["feature_analysis"] = {}
    for hr in horizon_results:
        if hr.holdout_features:
            h = hr.horizon
            analyses = {}
            for fname in hr.holdout_features[:10]:  # top 10
                if fname in features:
                    deciles = compute_feature_decile_analysis(
                        features[fname], fwd_ret[h], holdout_mask
                    )
                    analyses[fname] = {"decile_mean_returns": deciles}
            report["feature_analysis"][f"h{h}"] = analyses

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Signal Genesis — First-Principles Forward Return Prediction")
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--quick", action="store_true", help="Quick mode: fewer features, 2 folds")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS, help="Days reserved for holdout")
    parser.add_argument("--horizons", type=str, default="4,8,24", help="Comma-separated horizons")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill OHLCV from Binance before running pipeline")
    parser.add_argument("--backfill-only", action="store_true",
                        help="Only backfill, don't run the pipeline")
    parser.add_argument("--backfill-symbols", type=int, default=600,
                        help="Number of top USDT symbols to backfill (default 600)")
    parser.add_argument("--backfill-timeframes", type=str, default="1h",
                        help="Comma-separated timeframes to backfill (default '1h')")
    args = parser.parse_args()

    t_start = time.time()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    # ── Backfill if requested ──
    if args.backfill or args.backfill_only:
        bf_timeframes = [t.strip() for t in args.backfill_timeframes.split(",")]
        logger.info("=" * 70)
        logger.info("BACKFILL: %d days, %d symbols, timeframes=%s",
                    args.days, args.backfill_symbols, bf_timeframes)
        logger.info("=" * 70)
        ok = backfill_ohlcv(database_url, args.days, bf_timeframes, args.backfill_symbols)
        if not ok:
            logger.error("Backfill failed")
            sys.exit(1)
        if args.backfill_only:
            logger.info("Backfill complete. Exiting (--backfill-only).")
            return

    horizons = [int(h) for h in args.horizons.split(",")]
    n_folds = 2 if args.quick else N_FOLDS

    logger.info("=" * 70)
    logger.info("SIGNAL GENESIS — First-Principles Forward Return Prediction")
    logger.info("=" * 70)
    logger.info("Config: days=%d, horizons=%s, folds=%d, holdout=%d days, quick=%s",
                args.days, horizons, n_folds, args.holdout_days, args.quick)

    # ── Phase 1: Data Loading ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 1: Data Loading & Matrix Construction")
    logger.info("─" * 50)

    close, open_, high, low, volume, ts_grid, symbols, btc_idx, ohlcv_data = \
        load_ohlcv_matrices(database_url, args.days)

    logger.info("Computing forward returns...")
    fwd_ret = compute_forward_returns(close, horizons)

    # ── Phase 2: Feature Engineering ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 2: Feature Engineering")
    logger.info("─" * 50)

    features = engineer_features(close, open_, high, low, volume, ts_grid, btc_idx, quick=args.quick)

    # ── Phase 3-5: Walk-Forward per Horizon ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 3-5: Walk-Forward Validation")
    logger.info("─" * 50)

    folds, holdout_mask = build_folds(ts_grid, n_folds=n_folds, holdout_days=args.holdout_days)

    if not folds:
        logger.error("No folds could be constructed. Need more data (try --days 110).")
        sys.exit(1)

    horizon_results = []
    regime_reports = {}

    # Write incremental results as JSONL
    jsonl_path = os.path.join(os.path.dirname(__file__), "signal_genesis_incremental.jsonl")

    for h in horizons:
        logger.info("\n" + "━" * 40)
        logger.info("HORIZON: %d bars", h)
        logger.info("━" * 40)

        fold_results, fold_features_list = run_walk_forward(
            features, fwd_ret, folds, h, symbols, ohlcv_data
        )

        # Compute aggregate metrics
        valid_folds = [fr for fr in fold_results if fr.n_trades > 0]
        if valid_folds:
            avg_pf = float(np.mean([fr.avg_daily_pf for fr in valid_folds]))
            consistency = sum(1 for fr in valid_folds if fr.avg_daily_pf > 1.0) / len(valid_folds)
            total_trades = sum(fr.n_trades for fr in valid_folds)
        else:
            avg_pf = 0.0
            consistency = 0.0
            total_trades = 0

        hr = HorizonResult(
            horizon=h,
            fold_results=fold_results,
            avg_daily_pf=avg_pf,
            consistency=consistency,
            total_trades=total_trades,
        )

        # Regime shift diagnostic
        pairwise, in_all, in_one = regime_shift_diagnostic(fold_features_list)
        regime_reports[h] = (pairwise, in_all, in_one)

        avg_jaccard = float(np.mean([p["jaccard"] for p in pairwise])) if pairwise else 0.0
        logger.info("\n  Regime diagnostic: avg Jaccard=%.3f", avg_jaccard)
        if in_all:
            logger.info("  Features in ALL folds: %s", sorted(in_all))
        if avg_jaccard < 0.3:
            logger.info("  ⚠ Low Jaccard (<0.3) — significant regime shift detected")

        logger.info("\n  Walk-forward summary h=%d: avg daily PF=%.3f, consistency=%.1f%%, trades=%d",
                    h, avg_pf, consistency * 100, total_trades)

        # Write incremental result
        with open(jsonl_path, "a") as f:
            fold_data = {
                "type": "walk_forward",
                "horizon": h,
                "avg_daily_pf": avg_pf,
                "consistency": consistency,
                "total_trades": total_trades,
                "folds": [asdict(fr) for fr in fold_results],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            f.write(json.dumps(fold_data, default=str) + "\n")

        horizon_results.append(hr)

    # ── Phase 6: Holdout ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 6: Holdout Evaluation")
    logger.info("─" * 50)

    n_total_features = len(features) * len(horizons)

    for hr in horizon_results:
        h = hr.horizon
        # Only run holdout if walk-forward passed
        wf_pass = (hr.avg_daily_pf > 1.0 and hr.consistency >= MIN_FOLD_CONSISTENCY
                    and hr.total_trades >= MIN_TRADES_TOTAL)

        if not wf_pass:
            logger.info("  h=%d: SKIPPING holdout (walk-forward failed: PF=%.3f, cons=%.1f%%, trades=%d)",
                        h, hr.avg_daily_pf, hr.consistency * 100, hr.total_trades)
            continue

        holdout_result = run_holdout(
            features, fwd_ret, folds, holdout_mask, h, symbols, ohlcv_data,
            n_total_features
        )

        if holdout_result:
            hr.holdout_avg_daily_pf = holdout_result["avg_daily_pf"]
            hr.holdout_aggregate_pf = holdout_result["aggregate_pf"]
            hr.holdout_dsr = holdout_result["dsr"]
            hr.holdout_n_trades = holdout_result["n_trades"]
            hr.holdout_features = holdout_result["features"]

            # Slippage sensitivity at 2.0x
            cfg = HORIZON_CONFIGS[h]
            exit_cfg = _make_exit_config(cfg)

            # Re-generate holdout signals for 2x test
            full_train_mask = np.zeros(len(holdout_mask), dtype=bool)
            holdout_start = np.where(holdout_mask)[0]
            if len(holdout_start) > 0:
                full_train_mask[:holdout_start[0]] = True

            sel_result = select_features(features, fwd_ret[h], full_train_mask, h, -1)
            if sel_result:
                selected, interactions = sel_result
                composite = build_composite_model(features, selected, interactions, full_train_mask)
                signals_2x = generate_signals(composite, holdout_mask, holdout_result["best_k"], symbols)
                trades_2x = simulate_genesis_trades(
                    signals_2x, ohlcv_data, cfg["stop_atr_mult"],
                    cfg["time_stop_bars"], BASE_SLIPPAGE_1H * 2.0, exit_cfg,
                )
                if trades_2x:
                    pf_2x = compute_avg_daily_pf(trades_2x, ohlcv_data, symbols)
                    logger.info("  h=%d: 2.0x slippage test: %d trades, avg daily PF=%.3f",
                                h, len(trades_2x), pf_2x)

            # Write holdout incremental
            with open(jsonl_path, "a") as f:
                ho_data = {
                    "type": "holdout",
                    "horizon": h,
                    **{k: v for k, v in holdout_result.items() if k != "trades"},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                f.write(json.dumps(ho_data, default=str) + "\n")

    # ── Phase 7: Report ──
    logger.info("\n" + "─" * 50)
    logger.info("PHASE 7: Final Report")
    logger.info("─" * 50)

    elapsed_total = time.time() - t_start
    report = generate_report(
        horizon_results, features, fwd_ret, holdout_mask, ts_grid,
        regime_reports, len(features), elapsed_total
    )

    output_path = os.path.join(os.path.dirname(__file__), "signal_genesis_results.json")
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Results written to %s", output_path)

    # Print summary
    logger.info("\n" + "═" * 70)
    logger.info("SUMMARY")
    logger.info("═" * 70)
    logger.info("Total elapsed: %.1f min", elapsed_total / 60)
    logger.info("Features engineered: %d", len(features))

    any_confirmed = False
    for h_key, h_data in report["horizons"].items():
        verdict = h_data["verdict"]
        wf = h_data["walk_forward"]
        ho = h_data["holdout"]
        logger.info("\n  %s: %s", h_key, verdict)
        logger.info("    Walk-forward: avg daily PF=%.3f, consistency=%.1f%%, trades=%d",
                    wf["avg_daily_pf"], wf["consistency"] * 100 if wf["consistency"] else 0,
                    wf["total_trades"])
        if ho["n_trades"] > 0:
            logger.info("    Holdout: avg daily PF=%.3f, DSR=%.3f, trades=%d",
                        ho["avg_daily_pf"], ho["dsr"], ho["n_trades"])
        if verdict == "CONFIRMED":
            any_confirmed = True

    if not any_confirmed:
        logger.info("\nNo horizons confirmed. Analysis of failure modes:")
        for h_key, h_data in report["horizons"].items():
            if "fail_reasons" in h_data:
                logger.info("  %s: %s", h_key, "; ".join(h_data["fail_reasons"]))
            elif h_data["verdict"] == "FAIL_HOLDOUT":
                logger.info("  %s: Passed walk-forward but failed holdout (avg daily PF <= 1.0)", h_key)
            elif h_data["verdict"] == "FAIL_DSR":
                logger.info("  %s: Passed walk-forward + holdout but DSR <= 0 (multiple testing correction)", h_key)

    # Regime diagnostics summary
    logger.info("\nRegime diagnostics:")
    for h, diag in report["regime_diagnostics"].items():
        avg_j = diag["avg_jaccard"]
        stability = "STABLE" if avg_j > 0.6 else ("MODERATE" if avg_j > 0.3 else "UNSTABLE")
        logger.info("  %s: avg Jaccard=%.3f (%s), %d features in all folds, %d in one only",
                    h, avg_j, stability, len(diag["features_in_all_folds"]),
                    len(diag["features_in_one_fold_only"]))

    logger.info("\n" + "═" * 70)
    logger.info("Done.")


if __name__ == "__main__":
    main()
