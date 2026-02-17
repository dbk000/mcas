#!/usr/bin/env python3
"""
Signal Discovery v3 — Multi-Timeframe Parallel Signal Engine
=============================================================

Tests 10 curated strategies with rigorous validation:
  - 5-fold purged walk-forward on days 0-91
  - 20% holdout (days 92-110), never seen until final confirmation
  - Deflated Sharpe Ratio (DSR) correction for multiple comparisons
  - TF-scaled slippage with sensitivity testing (base, 1.5×, 2×)

Strategies (4 categories, 10 strategies, ~192 configs):
  A1_TSM      — Time-Series Momentum (vol-normalized returns)
  A2_XSM      — Cross-Sectional Momentum (rank & trade top/bottom)
  A3_BREAKOUT — Donchian Breakout + Volume confirmation
  A4_VOL_MOM  — Volume-Confirmed Momentum Bar
  B1_VAMR     — Volatility-Adjusted Mean Reversion
  B2_RSI_SNAP — RSI Recovery (cross back from extreme)
  B3_ZSCORE   — Price Z-Score Reversion
  C1_BTCLEAD  — BTC Lead-Lag (alt catch-up after BTC impulse)
  D1_AUTOCORR — Autocorrelation Trading (regime-adaptive)
  D2_ORB      — Opening Range Breakout (session-based)

Architecture: Load → Folds → Pass 1 (fast screen) → Pass 2 (survivors)
             → Pass 3 (holdout + DSR) → Output + JSON

v3 Assessment & v4 Direction
-----------------------------
The validation framework is 2025. The strategies themselves are 2019.

DSR correction (Harvey et al. 2016), three-pass architecture, TF-scaled slippage sensitivity,
purged walk-forward with holdout — genuinely sophisticated methodology. But the 10 strategies
being tested through that framework are textbook. TSM, cross-sectional momentum, Donchian
breakout, z-score reversion, RSI snapback — introductory quant finance. The exception is
D1_AUTOCORR (let the regime tell you which strategy to run). Everything else is "apply
well-known pattern, hope it works in crypto."

The script is a very competent exploration of well-known territory. It's not a creative leap.
If 0/10 strategies pass the DSR gate — which is plausible given that these are all
heavily-arbitraged patterns — the right response isn't "try different parameters." It's "the
strategy space being searched is too conventional to contain an edge in crypto markets in 2025."

Let v3 run as-is; the results are informative either way. But have a v4 ready: if nothing
survives Pass 3, the next iteration should search a more creative strategy space, not
re-parameterise the same one.

v4 directions to explore (examples, not prescriptions):
  - Adaptive thresholds within folds: if D1 proves autocorrelation predicts regime, other
    strategies should USE that signal (e.g., A1_TSM threshold tightens when autocorr drops).
    Conditional gates during signal generation, not post-hoc ensemble.
  - Information-theoretic features: mutual information between volume and forward returns
    captures non-linear relationships that Pearson correlation misses entirely.
  - Cross-asset structure: crypto moves in clusters (DeFi tokens, meme coins, L1s). A strategy
    detecting sector rotation (money flowing from L1s to DeFi) exploits structure that no
    single-symbol strategy can see. U1's universe temperature scratches this surface.
  - Conditional signal generation: a momentum signal during a mean-reverting regime is noise.
    Strategies should talk to each other — regime awareness as a generation-time gate.

Usage:
    tmux new -s discovery
    docker compose -f docker-compose.yml run --rm -T worker \\
      python3 /scripts/signal_discovery_v3.py --days 110 --workers 16
"""

import os
import sys
import time
import json
import math
import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional
from multiprocessing import Pool, get_context

import numpy as np

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

import psycopg
from exit_checker import ExitConfig, check_exit_simple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

POSITION_SIZE_USD = 1000.0
FEE_BPS = 4.0
MIN_SHORT_PRICE = 1.00
MAX_BARS_FORWARD = 200
WARMUP_BARS = 250
BTC_SYMBOL = "BTCUSDT"

# TF-scaled slippage (bps)
TF_SLIPPAGE = {"4h": 3.0, "1h": 5.0, "15m": 10.0, "5m": 15.0}
XSM_SLIPPAGE_PENALTY = 5.0  # extra bps for XSM

# Stop presets for Pass 1
STOP_PRESETS = {
    "tight": {"atr_mult": 1.5, "time_bars": 12},
    "wide":  {"atr_mult": 2.0, "time_bars": 24},
}

# Trail configs for Pass 2
TRAIL_CONFIGS = {
    "tight":  ExitConfig(trail_breakeven_r=0.5, trail_activation_r=0.75, trail_distance_r=0.3),
    "medium": ExitConfig(trail_breakeven_r=0.75, trail_activation_r=1.0, trail_distance_r=0.5),
    "wide":   ExitConfig(trail_breakeven_r=0, trail_activation_r=1.5, trail_distance_r=1.0),
}

# ─── Module-level shared data (populated before fork, read-only in workers) ──

_shared_ohlcv = {}       # dict[(symbol, tf)] -> {ts, open, high, low, close, volume} np arrays
_shared_matrices = {}    # dict[tf] -> (close_mx, volume_mx, symbols, ts_grid)
_shared_folds = []       # fold definitions
_shared_holdout = ()     # (holdout_start_ts, holdout_end_ts)
_shared_indicators = {}  # dict[(symbol, tf)] -> compute_indicators() result
_shared_univ_temp = {}   # dict[tf] -> compute_universe_temperature() result
_shared_u1_indicators = {}  # dict[(symbol, tf)] -> {vol_slope_5, bb_width_pctile, vol_sma_20, ema_20, returns_12, range_pct, clv, body_ratio}

# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class Signal:
    strategy: str
    symbol: str
    direction: str
    ts_idx: int        # index into ts_grid for this TF
    score: float
    meta: dict


@dataclass
class TradeResult:
    strategy: str
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
class ConfigResult:
    strategy: str
    timeframe: str
    params: dict
    stop_preset: str
    fold_metrics: list  # list of dicts per fold
    total_trades: int
    avg_test_pf: float
    consistency: float
    ff_rate: float


@dataclass
class Pass2Result:
    config: ConfigResult
    trail_name: str
    slippage_mult: float
    fold_metrics: list
    avg_test_pf: float
    consistency: float
    trades_per_day: float
    ff_rate: float


@dataclass
class HoldoutResult:
    strategy: str
    timeframe: str
    params: dict
    trail_name: str
    pf: float
    wr: float
    sharpe: float
    dsr: float
    n_trades: int
    total_pnl: float
    example_trades: list


# ─── Memory estimation ───────────────────────────────────────────────────────

def estimate_memory(database_url: str, days: int, timeframes: list[str]) -> dict:
    """Query DB for row counts and estimate memory before loading."""
    start_ts = datetime.now(timezone.utc) - timedelta(days=days + 15)
    estimates = {}
    total_bytes = 0

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for tf in timeframes:
                cur.execute("""
                    SELECT COUNT(*) FROM ohlcv
                    WHERE timeframe = %s AND bar_start_ts >= %s AND is_final = TRUE
                """, (tf, start_ts))
                row_count = cur.fetchone()[0]
                # 6 columns × 8 bytes per float64
                bytes_est = row_count * 6 * 8
                estimates[tf] = {"rows": row_count, "bytes": bytes_est}
                total_bytes += bytes_est

    # Get available RAM
    try:
        import psutil
        available_ram = psutil.virtual_memory().available
    except ImportError:
        # Fallback: read from /proc/meminfo
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    available_ram = int(line.split()[1]) * 1024  # KB to bytes
                    break
            else:
                available_ram = 16 * 1024**3  # assume 16 GB

    return {
        "timeframes": estimates,
        "total_bytes": total_bytes,
        "total_mb": total_bytes / 1024**2,
        "available_ram_mb": available_ram / 1024**2,
        "pct_ram": total_bytes / available_ram * 100 if available_ram > 0 else 100,
    }


# ─── Data loading ────────────────────────────────────────────────────────────

def load_ohlcv_multi_tf(
    database_url: str, days: int, timeframes: list[str] = ("15m", "1h", "4h"),
) -> dict:
    """Load OHLCV for multiple timeframes. Returns dict[(symbol,tf)] -> numpy arrays.

    Uses one bulk query per timeframe (not per-symbol) to avoid O(symbols × TFs) round-trips.
    """
    start_ts = datetime.now(timezone.utc) - timedelta(days=days + 15)  # warmup
    end_ts = datetime.now(timezone.utc)
    result = {}
    total_loaded = 0

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for tf in timeframes:
                t_tf = time.time()
                # Single bulk query per TF — dedup via window function on (symbol, bar_start_ts)
                # with source priority, then split into per-symbol arrays in Python.
                cur.execute("""
                    SELECT symbol, bar_start_ts, open, high, low, close, volume
                    FROM (
                        SELECT symbol, bar_start_ts, open, high, low, close, volume,
                               ROW_NUMBER() OVER (
                                   PARTITION BY symbol, bar_start_ts
                                   ORDER BY CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END
                               ) AS rn
                        FROM ohlcv
                        WHERE timeframe = %s
                          AND bar_start_ts >= %s AND bar_start_ts < %s
                          AND is_final = TRUE
                    ) sub
                    WHERE rn = 1
                    ORDER BY symbol, bar_start_ts
                """, (tf, start_ts, end_ts))

                # Group rows by symbol in Python
                sym_rows = defaultdict(list)
                row_count = 0
                for row in cur:
                    sym_rows[row[0]].append(row)
                    row_count += 1

                logger.info("  %s: %d rows fetched for %d symbols in %.1fs",
                            tf, row_count, len(sym_rows), time.time() - t_tf)

                loaded = 0
                for sym, rows in sym_rows.items():
                    if len(rows) < WARMUP_BARS:
                        continue

                    ts_arr = np.array([
                        r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None
                        else r[1].timestamp()
                        for r in rows
                    ])
                    result[(sym, tf)] = {
                        "ts": ts_arr,
                        "open": np.array([float(r[2]) for r in rows]),
                        "high": np.array([float(r[3]) for r in rows]),
                        "low": np.array([float(r[4]) for r in rows]),
                        "close": np.array([float(r[5]) for r in rows]),
                        "volume": np.array([float(r[6]) for r in rows]),
                    }
                    loaded += 1

                logger.info("  %s: %d symbols with %d+ bars", tf, loaded, WARMUP_BARS)
                total_loaded += loaded

    logger.info("Total: %d symbol-tf combos loaded", total_loaded)
    return result


def build_matrices(ohlcv_data: dict, timeframe: str):
    """Build (n_timestamps × n_symbols) close and volume matrices for one TF."""
    # Collect symbols for this TF
    symbols = sorted(set(sym for (sym, tf) in ohlcv_data if tf == timeframe))
    if not symbols:
        return None, None, [], []

    # Build unified timestamp grid
    all_ts = set()
    for sym in symbols:
        key = (sym, timeframe)
        if key in ohlcv_data:
            all_ts.update(ohlcv_data[key]["ts"].tolist())
    ts_grid = np.array(sorted(all_ts))

    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_ts = len(ts_grid)
    n_sym = len(symbols)

    close_mx = np.full((n_ts, n_sym), np.nan)
    volume_mx = np.full((n_ts, n_sym), np.nan)

    for j, sym in enumerate(symbols):
        key = (sym, timeframe)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        for k, ts in enumerate(data["ts"]):
            idx = ts_to_idx.get(ts)
            if idx is not None:
                close_mx[idx, j] = data["close"][k]
                volume_mx[idx, j] = data["volume"][k]

    return close_mx, volume_mx, symbols, ts_grid


# ─── Walk-Forward + Holdout ──────────────────────────────────────────────────

def compute_folds_and_holdout(ts_grid: np.ndarray, holdout_days: int = 18):
    """
    5-fold purged rolling walk-forward on days 0-91, holdout on days 92-110.
    Returns (folds, holdout_range) where folds is list of (train_start_ts, train_end_ts, test_start_ts, test_end_ts).
    """
    first_ts = ts_grid[WARMUP_BARS] if len(ts_grid) > WARMUP_BARS else ts_grid[0]
    last_ts = ts_grid[-1]
    total_seconds = last_ts - first_ts
    total_days = total_seconds / 86400

    # Holdout: last holdout_days
    holdout_start = last_ts - holdout_days * 86400
    holdout_end = last_ts

    # Walk-forward zone: first_ts to holdout_start
    wf_days = (holdout_start - first_ts) / 86400

    # 5 folds: train=25d, test=13d, purge=1d, step=13d
    train_days = 25
    test_days = 13
    purge_days = 1
    step_days = 13

    folds = []
    for f in range(5):
        train_start = first_ts + f * step_days * 86400
        train_end = train_start + train_days * 86400
        test_start = train_end + purge_days * 86400
        test_end = test_start + test_days * 86400

        # Don't let test bleed into holdout
        if test_end > holdout_start:
            test_end = holdout_start
        if test_start >= holdout_start:
            break

        folds.append((train_start, train_end, test_start, test_end))

    return folds, (holdout_start, holdout_end)


# ─── Indicator helpers (vectorized) ──────────────────────────────────────────

def rolling_atr(highs, lows, closes, n=14):
    """ATR series using vectorized true range."""
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

    # Simple rolling mean ATR
    atr = np.zeros(length)
    cumtr = np.cumsum(tr)
    atr[n-1:] = (cumtr[n-1:] - np.concatenate([[0], cumtr[:-n]])) / n
    return atr


def rolling_std(data, n):
    """Rolling standard deviation."""
    length = len(data)
    result = np.full(length, np.nan)
    if length < n:
        return result
    # Use cumulative sums for efficiency
    cumsum = np.cumsum(data)
    cumsum2 = np.cumsum(data**2)
    cumsum = np.insert(cumsum, 0, 0)
    cumsum2 = np.insert(cumsum2, 0, 0)
    window_sum = cumsum[n:] - cumsum[:-n]
    window_sum2 = cumsum2[n:] - cumsum2[:-n]
    mean = window_sum / n
    var = window_sum2 / n - mean**2
    var = np.maximum(var, 0)  # numerical safety
    result[n-1:] = np.sqrt(var)
    return result


def rolling_sma(data, n):
    """Rolling simple moving average."""
    length = len(data)
    result = np.full(length, np.nan)
    if length < n:
        return result
    cumsum = np.cumsum(data)
    cumsum = np.insert(cumsum, 0, 0)
    result[n-1:] = (cumsum[n:] - cumsum[:-n]) / n
    return result


def rolling_rsi(closes, n=14):
    """RSI series using Wilder smoothing."""
    length = len(closes)
    if length < n + 1:
        return np.full(length, 50.0)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = np.zeros(len(deltas))
    avg_l = np.zeros(len(deltas))
    avg_g[n-1] = np.mean(gains[:n])
    avg_l[n-1] = np.mean(losses[:n])
    for i in range(n, len(deltas)):
        avg_g[i] = (avg_g[i-1] * (n-1) + gains[i]) / n
        avg_l[i] = (avg_l[i-1] * (n-1) + losses[i]) / n
    rsi = np.full(length, 50.0)
    for i in range(n, length):
        if avg_l[i-1] == 0:
            rsi[i] = 100.0
        else:
            rsi[i] = 100.0 - 100.0 / (1.0 + avg_g[i-1] / avg_l[i-1])
    return rsi


def rolling_volume_z(volumes, n=20):
    """Volume Z-score."""
    length = len(volumes)
    vz = np.zeros(length)
    mean = rolling_sma(volumes, n)
    std = rolling_std(volumes, n)
    valid = (std > 0) & ~np.isnan(std) & ~np.isnan(mean)
    vz[valid] = (volumes[valid] - mean[valid]) / std[valid]
    return vz


def rolling_donchian_high(highs, n):
    """Rolling N-bar high."""
    length = len(highs)
    result = np.full(length, np.nan)
    for i in range(n-1, length):
        result[i] = np.max(highs[i-n+1:i+1])
    return result


def rolling_donchian_low(lows, n):
    """Rolling N-bar low."""
    length = len(lows)
    result = np.full(length, np.nan)
    for i in range(n-1, length):
        result[i] = np.min(lows[i-n+1:i+1])
    return result


def rolling_autocorr(data, window, lag=1):
    """Rolling autocorrelation of returns."""
    length = len(data)
    result = np.full(length, np.nan)
    for i in range(window + lag - 1, length):
        x = data[i-window-lag+1:i-lag+1]
        y = data[i-window+1:i+1]
        if len(x) == len(y) and len(x) > 2:
            mx, my = np.mean(x), np.mean(y)
            sx, sy = np.std(x), np.std(y)
            if sx > 0 and sy > 0:
                result[i] = np.mean((x - mx) * (y - my)) / (sx * sy)
    return result


def compute_indicators(data: dict) -> dict:
    """Compute common indicators for one symbol-tf combo."""
    closes = data["close"]
    highs = data["high"]
    lows = data["low"]
    opens = data["open"]
    volumes = data["volume"]

    atr = rolling_atr(highs, lows, closes, 14)
    rsi = rolling_rsi(closes, 14)
    vol_z = rolling_volume_z(volumes, 20)
    sma_20 = rolling_sma(closes, 20)
    std_20 = rolling_std(closes, 20)

    # BB bands
    bb_upper = sma_20 + 2 * std_20
    bb_lower = sma_20 - 2 * std_20
    bb_width = np.where(sma_20 > 0, (bb_upper - bb_lower) / sma_20, 0)

    # Returns
    returns_1 = np.zeros(len(closes))
    returns_1[1:] = (closes[1:] - closes[:-1]) / np.where(closes[:-1] != 0, closes[:-1], 1)

    # Body ratio (safe_range avoids RuntimeWarning from numpy evaluating both branches)
    bar_range = highs - lows
    safe_range = np.where(bar_range > 0, bar_range, 1.0)
    body = np.abs(closes - opens)
    body_ratio = np.where(bar_range > 0, body / safe_range, 0)

    # CLV (Close Location Value)
    clv = np.where(bar_range > 0, (closes - lows) / safe_range, 0.5)

    return {
        "atr": atr, "rsi": rsi, "vol_z": vol_z,
        "sma_20": sma_20, "std_20": std_20,
        "bb_upper": bb_upper, "bb_lower": bb_lower, "bb_width": bb_width,
        "returns_1": returns_1, "body_ratio": body_ratio, "clv": clv,
    }


# ─── Trade simulation ────────────────────────────────────────────────────────

def simulate_trades(
    signals: list[Signal], ohlcv_data: dict, tf: str,
    stop_atr_mult: float, time_stop_bars: int,
    slippage_bps: float, exit_config: ExitConfig = None,
    indicators: dict = None,
) -> list[TradeResult]:
    """Simulate signals to trades with exit logic."""
    if exit_config is None:
        exit_config = ExitConfig()
    results = []

    for sig in signals:
        key = (sig.symbol, tf)
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

        # ATR at signal bar — use pre-computed if available, else fallback
        if indicators and key in indicators:
            atr = indicators[key]["atr"][i]
        else:
            atr = rolling_atr(highs[:i+1], lows[:i+1], closes[:i+1], 14)[-1]
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
        # Fee gate
        if 2 * entry * FEE_BPS / 10000 > 0.3 * r_value:
            continue

        # Run exit logic
        max_idx = min(i + 2 + MAX_BARS_FORWARD, n)
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
            strategy=sig.strategy, symbol=sig.symbol, direction=sig.direction,
            ts_idx=sig.ts_idx, entry_price=entry, exit_price=ep,
            pnl=pnl_net, r_mult=r_mult, exit_reason=exit_reason, bars_held=bars_held,
        ))

    return results


def compute_metrics(trades: list[TradeResult]) -> dict:
    """Compute PF, WR, PnL, FF rate from trade list."""
    if not trades:
        return {"n": 0, "pf": 0, "wr": 0, "pnl": 0, "avg_r": 0, "ff_rate": 0, "sharpe": 0}
    pnls = [t.pnl for t in trades]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    ff = sum(1 for t in trades if t.exit_reason == "FAIL_FAST")
    pnl_arr = np.array(pnls)
    sharpe = float(np.mean(pnl_arr) / np.std(pnl_arr) * np.sqrt(252)) if np.std(pnl_arr) > 0 else 0
    return {
        "n": n,
        "pf": gp / gl if gl > 0 else (99.99 if gp > 0 else 0),
        "wr": wins / n,
        "pnl": sum(pnls),
        "avg_r": sum(t.r_mult for t in trades) / n,
        "ff_rate": ff / n,
        "sharpe": sharpe,
    }


# ─── Strategy implementations ────────────────────────────────────────────────

def gen_a1_tsm(indicators: dict, ohlcv_data: dict, symbols: list[str],
               tf: str, ts_grid: np.ndarray, lookback: int, threshold: float,
               min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: Assets that have moved strongly in one direction tend to continue.
    Vol-normalize to compare across assets.
    """
    signals = []
    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        ind = indicators.get(key)
        if ind is None:
            continue
        atr = ind["atr"]
        n = len(closes)

        for i in range(max(WARMUP_BARS, lookback), n - 1):
            ts = data["ts"][i]
            if ts < min_ts or ts >= max_ts:
                continue
            if atr[i] <= 0:
                continue
            norm_ret = (closes[i] - closes[i - lookback]) / (atr[i] * math.sqrt(lookback))

            if norm_ret > threshold:
                signals.append(Signal("A1_TSM", sym, "long", i,
                    score=min(1.0, norm_ret / (2 * threshold)),
                    meta={"lookback": lookback, "norm_ret": round(norm_ret, 4)}))
            elif norm_ret < -threshold:
                if closes[i] >= MIN_SHORT_PRICE:
                    signals.append(Signal("A1_TSM", sym, "short", i,
                        score=min(1.0, abs(norm_ret) / (2 * threshold)),
                        meta={"lookback": lookback, "norm_ret": round(norm_ret, 4)}))
    return signals


def gen_a2_xsm(indicators: dict, ohlcv_data: dict, symbols: list[str],
               tf: str, ts_grid: np.ndarray, close_mx: np.ndarray,
               lookback: int, rebalance: int,
               min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: In any given period, top-performing assets continue to outperform
    bottom-performers. Most robust factor in quantitative finance.
    """
    signals = []
    n_ts, n_sym = close_mx.shape

    for i in range(max(WARMUP_BARS, lookback), n_ts - 1, rebalance):
        ts = ts_grid[i]
        if ts < min_ts or ts >= max_ts:
            continue

        past = close_mx[i - lookback]
        current = close_mx[i]
        valid = ~np.isnan(past) & ~np.isnan(current) & (past > 0)
        if valid.sum() < 20:
            continue

        returns = np.full(n_sym, np.nan)
        returns[valid] = (current[valid] - past[valid]) / past[valid]

        valid_indices = np.where(valid)[0]
        valid_returns = returns[valid_indices]
        sorted_idx = np.argsort(valid_returns)
        n_valid = len(sorted_idx)
        n_top = max(1, int(n_valid * 0.10))

        # Long top performers
        for rank in range(n_valid - n_top, n_valid):
            j = valid_indices[sorted_idx[rank]]
            sym = symbols[j]
            ret = valid_returns[sorted_idx[rank]]
            signals.append(Signal("A2_XSM", sym, "long", i,
                score=min(1.0, abs(ret) * 10),
                meta={"lookback": lookback, "return": round(float(ret), 4)}))

        # Short bottom performers
        for rank in range(n_top):
            j = valid_indices[sorted_idx[rank]]
            sym = symbols[j]
            if current[j] < MIN_SHORT_PRICE:
                continue
            ret = valid_returns[sorted_idx[rank]]
            signals.append(Signal("A2_XSM", sym, "short", i,
                score=min(1.0, abs(ret) * 10),
                meta={"lookback": lookback, "return": round(float(ret), 4)}))

    return signals


def gen_a3_breakout(indicators: dict, ohlcv_data: dict, symbols: list[str],
                    tf: str, ts_grid: np.ndarray, lookback: int, vol_z_min: float,
                    min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: Price breaking to N-bar high/low with volume confirmation signals
    institutional participation.
    """
    signals = []
    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        highs = data["high"]
        lows = data["low"]
        ind = indicators.get(key)
        if ind is None:
            continue
        vol_z = ind["vol_z"]
        n = len(closes)

        don_high = rolling_donchian_high(highs, lookback)
        don_low = rolling_donchian_low(lows, lookback)

        for i in range(max(WARMUP_BARS, lookback), n - 1):
            ts = data["ts"][i]
            if ts < min_ts or ts >= max_ts:
                continue
            if np.isnan(don_high[i-1]) or np.isnan(don_low[i-1]):
                continue

            # Long breakout: close above previous donchian high + volume
            if closes[i] > don_high[i-1] and vol_z[i] > vol_z_min:
                signals.append(Signal("A3_BREAKOUT", sym, "long", i,
                    score=min(1.0, vol_z[i] / (2 * vol_z_min)),
                    meta={"lookback": lookback, "vol_z": round(float(vol_z[i]), 2)}))
            # Short breakout
            elif closes[i] < don_low[i-1] and vol_z[i] > vol_z_min:
                if closes[i] >= MIN_SHORT_PRICE:
                    signals.append(Signal("A3_BREAKOUT", sym, "short", i,
                        score=min(1.0, vol_z[i] / (2 * vol_z_min)),
                        meta={"lookback": lookback, "vol_z": round(float(vol_z[i]), 2)}))
    return signals


def gen_a4_vol_mom(indicators: dict, ohlcv_data: dict, symbols: list[str],
                   tf: str, ts_grid: np.ndarray, move_thresh: float, vol_z_min: float,
                   min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: Large-body candles with high volume are more likely to continue
    than low-volume moves.
    """
    signals = []
    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        opens = data["open"]
        ind = indicators.get(key)
        if ind is None:
            continue
        atr = ind["atr"]
        vol_z = ind["vol_z"]
        body_ratio = ind["body_ratio"]
        n = len(closes)

        for i in range(WARMUP_BARS, n - 1):
            ts = data["ts"][i]
            if ts < min_ts or ts >= max_ts:
                continue
            if atr[i] <= 0:
                continue
            bar_move = (closes[i] - opens[i]) / atr[i]

            if (abs(bar_move) > move_thresh and vol_z[i] > vol_z_min
                    and body_ratio[i] > 0.6):
                direction = "long" if bar_move > 0 else "short"
                if direction == "short" and closes[i] < MIN_SHORT_PRICE:
                    continue
                signals.append(Signal("A4_VOL_MOM", sym, direction, i,
                    score=min(1.0, abs(bar_move) / (2 * move_thresh)),
                    meta={"bar_move": round(float(bar_move), 3),
                           "vol_z": round(float(vol_z[i]), 2)}))
    return signals


def gen_b1_vamr(indicators: dict, ohlcv_data: dict, symbols: list[str],
                tf: str, ts_grid: np.ndarray, bb_pctl_min: float, rsi_extreme: float,
                min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: Mean reversion only works in high-volatility regimes. BB width
    percentile gates for regime.
    """
    signals = []
    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        ind = indicators.get(key)
        if ind is None:
            continue
        rsi = ind["rsi"]
        bb_width = ind["bb_width"]
        bb_upper = ind["bb_upper"]
        bb_lower = ind["bb_lower"]
        clv = ind["clv"]
        n = len(closes)

        for i in range(WARMUP_BARS, n - 1):
            ts = data["ts"][i]
            if ts < min_ts or ts >= max_ts:
                continue
            if np.isnan(bb_width[i]):
                continue

            # BB width percentile (rolling 200-bar)
            lb = min(200, i)
            bw_hist = bb_width[i-lb:i+1]
            bw_hist = bw_hist[~np.isnan(bw_hist)]
            if len(bw_hist) < 50:
                continue
            bb_pctl = np.sum(bw_hist < bb_width[i]) / len(bw_hist) * 100
            if bb_pctl < bb_pctl_min:
                continue

            # Long: RSI extreme low + price at BB lower + reversal candle (CLV > 0.45)
            if rsi[i] < rsi_extreme and closes[i] < bb_lower[i] and clv[i] > 0.45:
                signals.append(Signal("B1_VAMR", sym, "long", i,
                    score=min(1.0, (rsi_extreme - rsi[i]) / 20),
                    meta={"rsi": round(float(rsi[i]), 1), "bb_pctl": round(float(bb_pctl), 1)}))

            # Short: RSI extreme high + price at BB upper + rejection (CLV < 0.55)
            if rsi[i] > (100 - rsi_extreme) and closes[i] > bb_upper[i] and clv[i] < 0.55:
                if closes[i] >= MIN_SHORT_PRICE:
                    signals.append(Signal("B1_VAMR", sym, "short", i,
                        score=min(1.0, (rsi[i] - (100 - rsi_extreme)) / 20),
                        meta={"rsi": round(float(rsi[i]), 1), "bb_pctl": round(float(bb_pctl), 1)}))
    return signals


def gen_b2_rsi_snap(indicators: dict, ohlcv_data: dict, symbols: list[str],
                    tf: str, ts_grid: np.ndarray, extreme: float, recovery_delta: float,
                    min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: RSI crossing BACK from extreme (not the extreme itself) is a
    higher-probability entry. The extreme has already absorbed selling pressure.
    """
    signals = []
    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        ind = indicators.get(key)
        if ind is None:
            continue
        rsi = ind["rsi"]
        vol_z = ind["vol_z"]
        n = len(closes)

        for i in range(WARMUP_BARS, n - 1):
            ts = data["ts"][i]
            if ts < min_ts or ts >= max_ts:
                continue

            # Long: RSI was below extreme, now recovered above extreme + delta
            # Check previous bars were in extreme zone
            if i < 3:
                continue
            was_extreme_low = any(rsi[i-k] < extreme for k in range(1, min(4, i)))
            was_extreme_high = any(rsi[i-k] > (100 - extreme) for k in range(1, min(4, i)))

            recovery_thresh_low = extreme + recovery_delta
            recovery_thresh_high = (100 - extreme) - recovery_delta

            # Long snap: was below extreme, now above recovery
            if was_extreme_low and rsi[i] > recovery_thresh_low and vol_z[i] < 1.0:
                signals.append(Signal("B2_RSI_SNAP", sym, "long", i,
                    score=min(1.0, recovery_delta / 10),
                    meta={"rsi": round(float(rsi[i]), 1),
                           "extreme": extreme, "delta": recovery_delta}))

            # Short snap: was above extreme, now below recovery
            if was_extreme_high and rsi[i] < recovery_thresh_high and vol_z[i] < 1.0:
                if closes[i] >= MIN_SHORT_PRICE:
                    signals.append(Signal("B2_RSI_SNAP", sym, "short", i,
                        score=min(1.0, recovery_delta / 10),
                        meta={"rsi": round(float(rsi[i]), 1),
                               "extreme": extreme, "delta": recovery_delta}))
    return signals


def gen_b3_zscore(indicators: dict, ohlcv_data: dict, symbols: list[str],
                  tf: str, ts_grid: np.ndarray, window: int, z_threshold: float,
                  min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: Price > 2 sigma from rolling mean tends to revert. Pure statistical
    arbitrage.
    """
    signals = []
    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        n = len(closes)

        sma = rolling_sma(closes, window)
        std = rolling_std(closes, window)

        for i in range(max(WARMUP_BARS, window), n - 1):
            ts = data["ts"][i]
            if ts < min_ts or ts >= max_ts:
                continue
            if np.isnan(sma[i]) or np.isnan(std[i]) or std[i] <= 0:
                continue
            z = (closes[i] - sma[i]) / std[i]

            # Long: price far below mean
            if z < -z_threshold:
                signals.append(Signal("B3_ZSCORE", sym, "long", i,
                    score=min(1.0, abs(z) / (2 * z_threshold)),
                    meta={"window": window, "z": round(float(z), 3)}))

            # Short: price far above mean
            elif z > z_threshold:
                if closes[i] >= MIN_SHORT_PRICE:
                    signals.append(Signal("B3_ZSCORE", sym, "short", i,
                        score=min(1.0, abs(z) / (2 * z_threshold)),
                        meta={"window": window, "z": round(float(z), 3)}))
    return signals


def gen_c1_btclead(indicators: dict, ohlcv_data: dict, symbols: list[str],
                   tf: str, ts_grid: np.ndarray,
                   impulse_mult: float, lag_thresh: float,
                   min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: When BTC makes a large impulse move, altcoins follow with a delay
    of 1-6 hours. Trade alts that haven't caught up yet.
    """
    btc_key = (BTC_SYMBOL, tf)
    if btc_key not in ohlcv_data:
        return []
    btc_data = ohlcv_data[btc_key]
    btc_closes = btc_data["close"]
    btc_opens = btc_data["open"]
    btc_ind = indicators.get(btc_key)
    if btc_ind is None:
        return []
    btc_atr = btc_ind["atr"]

    # Build BTC timestamp index
    btc_ts_to_idx = {ts: i for i, ts in enumerate(btc_data["ts"])}

    # Pre-build alt timestamp indices once per symbol (not per BTC bar)
    alt_ts_indices = {}
    for sym in symbols:
        if sym == BTC_SYMBOL:
            continue
        key = (sym, tf)
        if key in ohlcv_data:
            alt_ts_indices[sym] = {t: j for j, t in enumerate(ohlcv_data[key]["ts"])}

    signals = []
    for i in range(WARMUP_BARS, len(btc_closes) - 1):
        ts = btc_data["ts"][i]
        if ts < min_ts or ts >= max_ts:
            continue
        if btc_atr[i] <= 0:
            continue

        btc_move = (btc_closes[i] - btc_opens[i]) / btc_atr[i]
        btc_ret = (btc_closes[i] - btc_opens[i]) / btc_opens[i] if btc_opens[i] > 0 else 0

        if abs(btc_move) < impulse_mult:
            continue

        btc_dir = "long" if btc_move > 0 else "short"

        for sym in symbols:
            if sym == BTC_SYMBOL:
                continue
            key = (sym, tf)
            if key not in ohlcv_data:
                continue
            alt_data = ohlcv_data[key]
            alt_idx = alt_ts_indices.get(sym, {}).get(ts)
            if alt_idx is None or alt_idx + 1 >= len(alt_data["close"]) or alt_idx < 2:
                continue

            alt_open = alt_data["open"][alt_idx]
            alt_close = alt_data["close"][alt_idx]
            if alt_open <= 0:
                continue
            alt_ret = (alt_close - alt_open) / alt_open

            # Alt hasn't caught up
            if btc_dir == "long" and alt_ret < btc_ret * lag_thresh:
                signals.append(Signal("C1_BTCLEAD", sym, "long", alt_idx,
                    score=min(1.0, abs(btc_move) / (2 * impulse_mult)),
                    meta={"btc_move_atr": round(float(btc_move), 2),
                           "alt_ret": round(float(alt_ret), 4)}))
            elif btc_dir == "short" and alt_ret > btc_ret * lag_thresh:
                if alt_close >= MIN_SHORT_PRICE:
                    signals.append(Signal("C1_BTCLEAD", sym, "short", alt_idx,
                        score=min(1.0, abs(btc_move) / (2 * impulse_mult)),
                        meta={"btc_move_atr": round(float(btc_move), 2),
                               "alt_ret": round(float(alt_ret), 4)}))
    return signals


def gen_d1_autocorr(indicators: dict, ohlcv_data: dict, symbols: list[str],
                    tf: str, ts_grid: np.ndarray,
                    window: int, min_autocorr: float,
                    min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: Rolling autocorrelation of returns reveals the current regime.
    Positive autocorr = trending (apply momentum). Negative = mean-reverting
    (apply MR). Self-adapting.
    """
    signals = []
    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        ind = indicators.get(key)
        if ind is None:
            continue
        returns_1 = ind["returns_1"]
        atr = ind["atr"]
        rsi = ind["rsi"]
        bb_upper = ind["bb_upper"]
        bb_lower = ind["bb_lower"]
        clv = ind["clv"]
        n = len(closes)

        ac = rolling_autocorr(returns_1, window, lag=1)

        for i in range(max(WARMUP_BARS, window + 1), n - 1):
            ts = data["ts"][i]
            if ts < min_ts or ts >= max_ts:
                continue
            if np.isnan(ac[i]) or atr[i] <= 0:
                continue

            # Trending regime: positive autocorr → momentum signal
            if ac[i] > min_autocorr:
                lookback = min(window, i)
                if lookback > 0:
                    norm_ret = (closes[i] - closes[i - lookback]) / (atr[i] * math.sqrt(lookback))
                    if norm_ret > 0.10:
                        signals.append(Signal("D1_AUTOCORR", sym, "long", i,
                            score=min(1.0, ac[i] / 0.3),
                            meta={"ac": round(float(ac[i]), 3), "mode": "momentum"}))
                    elif norm_ret < -0.10 and closes[i] >= MIN_SHORT_PRICE:
                        signals.append(Signal("D1_AUTOCORR", sym, "short", i,
                            score=min(1.0, ac[i] / 0.3),
                            meta={"ac": round(float(ac[i]), 3), "mode": "momentum"}))

            # Mean-reverting regime: negative autocorr → MR signal
            elif ac[i] < -min_autocorr:
                if rsi[i] < 30 and closes[i] < bb_lower[i] and clv[i] > 0.45:
                    signals.append(Signal("D1_AUTOCORR", sym, "long", i,
                        score=min(1.0, abs(ac[i]) / 0.3),
                        meta={"ac": round(float(ac[i]), 3), "mode": "mean_reversion"}))
                elif rsi[i] > 70 and closes[i] > bb_upper[i] and clv[i] < 0.55:
                    if closes[i] >= MIN_SHORT_PRICE:
                        signals.append(Signal("D1_AUTOCORR", sym, "short", i,
                            score=min(1.0, abs(ac[i]) / 0.3),
                            meta={"ac": round(float(ac[i]), 3), "mode": "mean_reversion"}))
    return signals


def gen_d2_orb(indicators: dict, ohlcv_data: dict, symbols: list[str],
               tf: str, ts_grid: np.ndarray,
               range_bars: int, margin_atr: float,
               min_ts: float, max_ts: float) -> list[Signal]:
    """
    THESIS: First N hours of UTC day establish support/resistance. Breakout
    from that range with volume = directional signal. Works because Asian session
    sets range, London/NY provide the breakout.
    """
    signals = []

    # Determine bar duration in hours
    tf_hours = {"15m": 0.25, "1h": 1, "4h": 4, "5m": 1/12}
    bar_hours = tf_hours.get(tf, 1)

    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        highs = data["high"]
        lows = data["low"]
        ts_arr = data["ts"]
        ind = indicators.get(key)
        if ind is None:
            continue
        atr = ind["atr"]
        vol_z = ind["vol_z"]
        n = len(closes)

        # Group bars by UTC day
        for i in range(WARMUP_BARS, n - 1):
            ts = ts_arr[i]
            if ts < min_ts or ts >= max_ts:
                continue
            if atr[i] <= 0:
                continue

            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            # Check if current bar is past the opening range
            bar_of_day = dt.hour / bar_hours + dt.minute / (60 * bar_hours)
            if bar_of_day < range_bars:
                continue  # Still in opening range

            # Compute opening range: high and low of first range_bars bars of this UTC day
            day_start_ts = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp()
            range_end_ts = day_start_ts + range_bars * bar_hours * 3600

            # Find bars in opening range
            mask = (ts_arr >= day_start_ts) & (ts_arr < range_end_ts)
            if mask.sum() < range_bars:
                continue
            range_high = np.max(highs[mask])
            range_low = np.min(lows[mask])

            margin = margin_atr * atr[i]

            # Long breakout
            if closes[i] > range_high + margin and vol_z[i] > 1.0:
                signals.append(Signal("D2_ORB", sym, "long", i,
                    score=min(1.0, (closes[i] - range_high) / (atr[i] + 1e-10)),
                    meta={"range_bars": range_bars, "range_high": round(float(range_high), 2)}))
            # Short breakdown
            elif closes[i] < range_low - margin and vol_z[i] > 1.0:
                if closes[i] >= MIN_SHORT_PRICE:
                    signals.append(Signal("D2_ORB", sym, "short", i,
                        score=min(1.0, (range_low - closes[i]) / (atr[i] + 1e-10)),
                        meta={"range_bars": range_bars, "range_low": round(float(range_low), 2)}))
    return signals


# ─── U1 Helpers ──────────────────────────────────────────────────────────────

# Major symbols to exclude from pennycoin universe
U1_MAJOR_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "LTCUSDT", "SHIBUSDT", "TRXUSDT", "ATOMUSDT",
}

# Custom stop presets for U1 (penny coins need different stops)
U1_STOP_PRESETS = {
    "vol_env_tight": {"atr_mult": 1.5, "time_bars": 6, "range_mult": 1.5,
                      "fail_fast_bars": 3, "fail_fast_pct": 0.001},
    "vol_env_wide":  {"atr_mult": 2.5, "time_bars": 12, "range_mult": 2.5,
                      "fail_fast_bars": 3, "fail_fast_pct": 0.003},
}


def rolling_linear_slope_v3(data, n):
    """Linear regression slope over rolling window of n bars."""
    length = len(data)
    result = np.zeros(length)
    if length < n:
        return result
    x = np.arange(n, dtype=np.float64)
    x_mean = x.mean()
    x_var = np.sum((x - x_mean) ** 2)
    if x_var == 0:
        return result
    for i in range(n - 1, length):
        y = data[i - n + 1:i + 1]
        y_mean = y.mean()
        result[i] = np.sum((x - x_mean) * (y - y_mean)) / x_var
    return result


def rolling_percentile_v3(data, n):
    """Percentile rank of current value vs last n values (0-100)."""
    length = len(data)
    result = np.full(length, 50.0)
    for i in range(n, length):
        window = data[i - n:i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) > 1:
            result[i] = np.sum(valid < data[i]) / (len(valid) - 1) * 100
    return result


def compute_universe_temperature(ohlcv_data: dict, tf: str, symbols: list[str]) -> dict:
    """
    Count coins with vol_z > 2.0 at each timestamp.
    Returns dict[ts_float] -> {n_gt2, n_gt3, n_positive_4h, mean_vol_z}
    """
    ts_vol_z = defaultdict(list)
    ts_returns_4 = defaultdict(list)

    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data or sym in U1_MAJOR_SYMBOLS:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        volumes = data["volume"]
        ts_arr = data["ts"]

        vz = rolling_volume_z(volumes, 20)
        ret_4 = np.zeros(len(closes))
        if len(closes) > 4:
            ret_4[4:] = (closes[4:] - closes[:-4]) / np.where(closes[:-4] != 0, closes[:-4], 1)

        for i in range(WARMUP_BARS, len(closes)):
            t = float(ts_arr[i])
            ts_vol_z[t].append(vz[i])
            ts_returns_4[t].append(ret_4[i])

    result = {}
    for t in ts_vol_z:
        vz_arr = np.array(ts_vol_z[t])
        ret_arr = np.array(ts_returns_4.get(t, [0]))
        result[t] = {
            "n_gt2": int(np.sum(vz_arr > 2.0)),
            "n_gt3": int(np.sum(vz_arr > 3.0)),
            "n_positive_4h": int(np.sum(ret_arr > 0)),
            "mean_vol_z": float(np.mean(vz_arr)),
        }
    return result


def gen_u1_ignition(indicators: dict, ohlcv_data: dict, symbols: list[str],
                    tf: str, ts_grid: np.ndarray,
                    vol_z_min: float, score_threshold: float, temp_weight: float,
                    min_ts: float, max_ts: float,
                    universe_temp: dict = None) -> list[Signal]:
    """
    THESIS: Pennycoin ignitions (+50% in 24h) are preceded by volume anomalies,
    volatility compression, and universe-level clustering. Features and weights
    derived empirically from u1_ignition_profiler.py.

    LONG-only. Filters out major symbols and applies volume floor/ceiling.
    """
    signals = []

    # Pre-compute universe temp if not provided (first call per fold)
    if universe_temp is None:
        universe_temp = compute_universe_temperature(ohlcv_data, tf, symbols)

    for sym in symbols:
        # Skip majors
        if sym in U1_MAJOR_SYMBOLS:
            continue

        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        ts_arr = data["ts"]
        n = len(closes)

        ind = indicators.get(key)
        if ind is None:
            continue
        atr = ind["atr"]
        vol_z = ind["vol_z"]

        # Use pre-computed U1-specific indicators
        u1_ind = _shared_u1_indicators.get(key)
        if u1_ind is None:
            continue
        vol_slope_5 = u1_ind["vol_slope_5"]
        bb_width_pctile = u1_ind["bb_width_pctile"]
        returns_12 = u1_ind["returns_12"]
        clv = u1_ind["clv"]
        range_pct = u1_ind["range_pct"]
        body_ratio = u1_ind["body_ratio"]
        cum_vol_std = u1_ind["cum_vol_std"]
        vol_z_max_6 = u1_ind["vol_z_max_6"]
        temp_n_gt2 = u1_ind["temp_n_gt2"]
        temp_mean_vz = u1_ind["temp_mean_vz"]

        # ── Vectorized scoring (replaces per-bar Python loop) ──
        # Build valid-bar mask
        valid = np.zeros(n, dtype=bool)
        valid[WARMUP_BARS:n - 1] = True
        valid &= (ts_arr >= min_ts) & (ts_arr < max_ts)
        valid &= (atr > 0) & (closes > 0)
        valid &= (vol_z >= vol_z_min)

        if not np.any(valid):
            continue

        # Vectorized score components
        s_vol_z = np.minimum(1.0, vol_z / 5.0)
        s_vol_z_max = np.minimum(1.0, vol_z_max_6 / 5.0)
        s_vol_slope = np.minimum(1.0, np.maximum(0, vol_slope_5 / (cum_vol_std + 1e-10)))
        bb_clean = np.where(np.isnan(bb_width_pctile), 50.0, bb_width_pctile)
        s_bb_compress = 1.0 - bb_clean / 100.0
        s_range = np.minimum(1.0, range_pct / 0.05)
        s_return = np.minimum(1.0, np.maximum(0, returns_12 * 5))

        per_coin_score = (
            0.25 * s_vol_z +
            0.15 * s_vol_z_max +
            0.10 * s_vol_slope +
            0.15 * s_bb_compress +
            0.10 * s_range +
            0.10 * s_return +
            0.08 * clv +
            0.07 * body_ratio
        )

        # Universe temperature component (vectorized from pre-built arrays)
        temp_score = (np.minimum(1.0, temp_n_gt2 / 20.0) * 0.5 +
                      np.minimum(1.0, np.maximum(0, temp_mean_vz) / 2.0) * 0.5)

        score = (1 - temp_weight) * per_coin_score + temp_weight * temp_score

        # Apply threshold + valid mask, collect signals
        signal_mask = valid & (score >= score_threshold)
        signal_indices = np.where(signal_mask)[0]

        for i in signal_indices:
            signals.append(Signal("U1_IGNITION", sym, "long", int(i),
                score=min(1.0, float(score[i])),
                meta={
                    "vol_z": round(float(vol_z[i]), 2),
                    "vol_z_max_6": round(float(vol_z_max_6[i]), 2),
                    "bb_pctile": round(float(bb_clean[i]), 1),
                    "temp_n_gt2": int(temp_n_gt2[i]),
                    "per_coin_score": round(float(per_coin_score[i]), 3),
                    "temp_score": round(float(temp_score[i]), 3),
                }))

    return signals


# ─── Strategy registry ───────────────────────────────────────────────────────

STRATEGY_REGISTRY = {
    "A1_TSM": {
        "func": gen_a1_tsm,
        "category": "A",
        "param_grid": [
            {"lookback": lb, "threshold": th}
            for lb in [8, 12, 24, 48] for th in [0.08, 0.12, 0.18]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": False,
    },
    "A2_XSM": {
        "func": gen_a2_xsm,
        "category": "A",
        "param_grid": [
            {"lookback": lb, "rebalance": rb}
            for lb in [12, 24, 48] for rb in [6, 12]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": True,
        "extra_slippage": XSM_SLIPPAGE_PENALTY,
    },
    "A3_BREAKOUT": {
        "func": gen_a3_breakout,
        "category": "A",
        "param_grid": [
            {"lookback": lb, "vol_z_min": vz}
            for lb in [12, 20, 30, 48] for vz in [1.0, 1.5]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": False,
    },
    "A4_VOL_MOM": {
        "func": gen_a4_vol_mom,
        "category": "A",
        "param_grid": [
            {"move_thresh": mt, "vol_z_min": vz}
            for mt in [0.6, 0.8, 1.0] for vz in [1.5, 2.0]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": False,
    },
    "B1_VAMR": {
        "func": gen_b1_vamr,
        "category": "B",
        "param_grid": [
            {"bb_pctl_min": bp, "rsi_extreme": rs}
            for bp in [60, 75, 85] for rs in [25, 30]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": False,
    },
    "B2_RSI_SNAP": {
        "func": gen_b2_rsi_snap,
        "category": "B",
        "param_grid": [
            {"extreme": ex, "recovery_delta": rd}
            for ex in [20, 25] for rd in [5, 10]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": False,
    },
    "B3_ZSCORE": {
        "func": gen_b3_zscore,
        "category": "B",
        "param_grid": [
            {"window": w, "z_threshold": zt}
            for w in [24, 48, 96] for zt in [2.0, 2.5]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": False,
    },
    "C1_BTCLEAD": {
        "func": gen_c1_btclead,
        "category": "C",
        "param_grid": [
            {"impulse_mult": im, "lag_thresh": lt}
            for im in [1.5, 2.0, 2.5] for lt in [0.2, 0.3]
        ],
        "timeframes": ["15m", "1h"],  # 4h too slow for lead-lag
        "needs_matrix": False,
    },
    "D1_AUTOCORR": {
        "func": gen_d1_autocorr,
        "category": "D",
        "param_grid": [
            {"window": w, "min_autocorr": ma}
            for w in [24, 48, 96] for ma in [0.05, 0.10]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": False,
    },
    "D2_ORB": {
        "func": gen_d2_orb,
        "category": "D",
        "param_grid": [
            {"range_bars": rb, "margin_atr": ma}
            for rb in [2, 4, 6] for ma in [0.2, 0.5]
        ],
        "timeframes": ["15m", "1h", "4h"],
        "needs_matrix": False,
    },
    "U1_IGNITION": {
        "func": gen_u1_ignition,
        "category": "U",
        "param_grid": [
            {"vol_z_min": vz, "score_threshold": st, "temp_weight": tw}
            for vz in [1.5, 2.0, 3.0]
            for st in [0.40, 0.50, 0.60, 0.70]
            for tw in [0.0, 0.15, 0.30]
        ],
        "timeframes": ["15m", "1h"],
        "needs_matrix": False,
        "extra_slippage": 5,
    },
}


# ─── Worker function for Pass 1 ─────────────────────────────────────────────

def pass1_worker(work_unit: dict) -> list[dict]:
    """
    One worker handles one (strategy, timeframe) combo across all param + stop combos.
    Uses module-level shared data.
    """
    strat_name = work_unit["strategy"]
    tf = work_unit["timeframe"]
    param_grid = work_unit["param_grid"]
    needs_matrix = work_unit.get("needs_matrix", False)
    extra_slip = work_unit.get("extra_slippage", 0)
    strat_reg = STRATEGY_REGISTRY[strat_name]
    strat_func = strat_reg["func"]

    base_slippage = TF_SLIPPAGE.get(tf, 5.0) + extra_slip

    # Get symbols for this TF
    symbols = sorted(set(sym for (sym, tf_) in _shared_ohlcv if tf_ == tf))

    # Use pre-computed indicators (read-only, shared via CoW from parent)
    indicators = {k: v for k, v in _shared_indicators.items() if k[1] == tf}

    # Get matrix data if needed
    matrix_data = _shared_matrices.get(tf)

    # Use pre-computed universe temperature for U1
    u1_universe_temp = _shared_univ_temp.get(tf) if strat_name == "U1_IGNITION" else None

    results = []

    for params in param_grid:
        for stop_name, stop_cfg in STOP_PRESETS.items():
            # Generate signals for all folds
            all_fold_metrics = []
            total_trades = 0

            for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(_shared_folds):
                # Build signal kwargs
                sig_kwargs = {
                    "indicators": indicators,
                    "ohlcv_data": _shared_ohlcv,
                    "symbols": symbols,
                    "tf": tf,
                    "ts_grid": matrix_data[3] if matrix_data else np.array([]),
                    **params,
                }

                # Generate signals for train+test period
                if needs_matrix and matrix_data:
                    sig_kwargs["close_mx"] = matrix_data[0]

                # Pass pre-computed universe temp for U1
                if u1_universe_temp is not None:
                    sig_kwargs["universe_temp"] = u1_universe_temp

                train_signals = strat_func(**sig_kwargs, min_ts=train_start, max_ts=train_end)
                test_signals = strat_func(**sig_kwargs, min_ts=test_start, max_ts=test_end)

                # Simple exit config for Pass 1
                exit_cfg = ExitConfig(
                    trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=0.5
                )

                # Simulate trades
                train_trades = simulate_trades(
                    train_signals, _shared_ohlcv, tf,
                    stop_cfg["atr_mult"], stop_cfg["time_bars"],
                    base_slippage, exit_cfg, indicators=indicators
                )
                test_trades = simulate_trades(
                    test_signals, _shared_ohlcv, tf,
                    stop_cfg["atr_mult"], stop_cfg["time_bars"],
                    base_slippage, exit_cfg, indicators=indicators
                )

                train_m = compute_metrics(train_trades)
                test_m = compute_metrics(test_trades)
                total_trades += test_m["n"]

                all_fold_metrics.append({
                    "fold": fold_idx,
                    "train_n": train_m["n"], "train_pf": train_m["pf"],
                    "test_n": test_m["n"], "test_pf": test_m["pf"],
                    "test_wr": test_m["wr"], "test_pnl": test_m["pnl"],
                    "test_ff_rate": test_m["ff_rate"],
                })

            # Aggregate
            test_pfs = [fm["test_pf"] for fm in all_fold_metrics if fm["test_n"] >= 5]
            avg_pf = np.mean(test_pfs) if test_pfs else 0
            profitable_folds = sum(1 for pf in test_pfs if pf > 0.8)
            consistency = profitable_folds / len(test_pfs) if test_pfs else 0
            avg_ff = np.mean([fm["test_ff_rate"] for fm in all_fold_metrics]) if all_fold_metrics else 0

            results.append({
                "strategy": strat_name,
                "timeframe": tf,
                "params": params,
                "stop_preset": stop_name,
                "fold_metrics": all_fold_metrics,
                "total_trades": total_trades,
                "avg_test_pf": float(avg_pf),
                "consistency": float(consistency),
                "ff_rate": float(avg_ff),
            })

    return results


# ─── Worker function for Pass 2 ─────────────────────────────────────────────

def pass2_worker(work_unit: dict) -> list[dict]:
    """Test survivor with trail configs × slippage levels."""
    survivor = work_unit["survivor"]
    strat_name = survivor["strategy"]
    tf = survivor["timeframe"]
    params = survivor["params"]
    stop_preset = survivor["stop_preset"]
    stop_cfg = STOP_PRESETS[stop_preset]
    strat_reg = STRATEGY_REGISTRY[strat_name]
    strat_func = strat_reg["func"]
    extra_slip = strat_reg.get("extra_slippage", 0)
    base_slippage = TF_SLIPPAGE.get(tf, 5.0) + extra_slip
    needs_matrix = strat_reg.get("needs_matrix", False)

    symbols = sorted(set(sym for (sym, tf_) in _shared_ohlcv if tf_ == tf))

    # Use pre-computed indicators (read-only, shared via CoW from parent)
    indicators = {k: v for k, v in _shared_indicators.items() if k[1] == tf}

    matrix_data = _shared_matrices.get(tf)

    # Use pre-computed universe temperature for U1
    u1_universe_temp = _shared_univ_temp.get(tf) if strat_name == "U1_IGNITION" else None

    results = []
    slippage_mults = [1.0, 1.5, 2.0]

    for trail_name, trail_cfg in TRAIL_CONFIGS.items():
        for slip_mult in slippage_mults:
            slippage = base_slippage * slip_mult

            all_fold_metrics = []
            total_trades = 0
            total_days = 0

            for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(_shared_folds):
                sig_kwargs = {
                    "indicators": indicators,
                    "ohlcv_data": _shared_ohlcv,
                    "symbols": symbols,
                    "tf": tf,
                    "ts_grid": matrix_data[3] if matrix_data else np.array([]),
                    **params,
                }
                if needs_matrix and matrix_data:
                    sig_kwargs["close_mx"] = matrix_data[0]
                if u1_universe_temp is not None:
                    sig_kwargs["universe_temp"] = u1_universe_temp

                test_signals = strat_func(**sig_kwargs, min_ts=test_start, max_ts=test_end)
                test_trades = simulate_trades(
                    test_signals, _shared_ohlcv, tf,
                    stop_cfg["atr_mult"], stop_cfg["time_bars"],
                    slippage, trail_cfg, indicators=indicators,
                )

                test_m = compute_metrics(test_trades)
                total_trades += test_m["n"]
                total_days += (test_end - test_start) / 86400

                all_fold_metrics.append({
                    "fold": fold_idx,
                    "test_n": test_m["n"], "test_pf": test_m["pf"],
                    "test_wr": test_m["wr"], "test_pnl": test_m["pnl"],
                    "test_ff_rate": test_m["ff_rate"],
                })

            test_pfs = [fm["test_pf"] for fm in all_fold_metrics if fm["test_n"] >= 5]
            avg_pf = np.mean(test_pfs) if test_pfs else 0
            profitable_folds = sum(1 for pf in test_pfs if pf > 1.0)
            consistency = profitable_folds / len(test_pfs) if test_pfs else 0
            avg_ff = np.mean([fm["test_ff_rate"] for fm in all_fold_metrics]) if all_fold_metrics else 0
            tpd = total_trades / max(total_days, 1)

            results.append({
                "strategy": strat_name,
                "timeframe": tf,
                "params": params,
                "stop_preset": stop_preset,
                "trail_name": trail_name,
                "slippage_mult": slip_mult,
                "fold_metrics": all_fold_metrics,
                "avg_test_pf": float(avg_pf),
                "consistency": float(consistency),
                "trades_per_day": float(tpd),
                "ff_rate": float(avg_ff),
                "total_trades": total_trades,
            })

    return results


# ─── Deflated Sharpe Ratio ───────────────────────────────────────────────────

def deflated_sharpe_ratio(observed_sr, n_trials, n_observations, skew=0, kurtosis=3):
    """
    Harvey, Liu, Zhu (2016) — adjusts for multiple testing.

    E[max(SR)] under null = sqrt(2 * log(n_trials)) * (1 - euler_gamma / log(n_trials))
    SE(SR) = sqrt((1 + 0.5*sr^2 - skew*sr + (kurtosis-3)/4 * sr^2) / n_observations)

    DSR > 0 means strategy's performance is unlikely to be a statistical artifact.
    """
    if n_trials <= 1 or n_observations <= 1:
        return 0.0

    euler_gamma = 0.5772156649

    # Expected maximum Sharpe under null (i.i.d. normal returns)
    log_n = math.log(n_trials)
    if log_n <= 0:
        return 0.0
    e_max_sr = math.sqrt(2 * log_n) * (1 - euler_gamma / log_n) + euler_gamma / (2 * math.sqrt(2 * log_n))

    # Standard error of Sharpe ratio
    se_sr = math.sqrt(
        (1 + 0.5 * observed_sr**2 - skew * observed_sr + (kurtosis - 3) / 4 * observed_sr**2)
        / n_observations
    )
    if se_sr <= 0:
        return 0.0

    dsr = (observed_sr - e_max_sr) / se_sr
    return float(dsr)


# ─── Strategy cards & example trades ─────────────────────────────────────────

STRATEGY_THESES = {
    "A1_TSM": "Assets moving strongly over N bars tend to continue. Vol-normalized returns filter out noise.",
    "A2_XSM": "Top-performing assets continue to outperform bottom-performers. Most robust quantitative factor.",
    "A3_BREAKOUT": "Price breaking to N-bar high/low with volume signals institutional participation.",
    "A4_VOL_MOM": "Large-body candles with high volume are more likely to continue than low-volume moves.",
    "B1_VAMR": "Mean reversion only works in high-volatility regimes. BB width percentile gates for regime.",
    "B2_RSI_SNAP": "RSI crossing back from extreme is higher-probability than the extreme itself.",
    "B3_ZSCORE": "Price > 2 sigma from rolling mean tends to revert. Pure statistical arbitrage.",
    "C1_BTCLEAD": "When BTC makes a large impulse move, altcoins follow with a delay. Trade the laggards.",
    "D1_AUTOCORR": "Rolling autocorrelation reveals regime: positive = trending, negative = mean-reverting.",
    "D2_ORB": "First N hours of UTC day set support/resistance. Asian range → London/NY breakout.",
    "U1_IGNITION": "Pennycoin ignitions (+50% in 24h) preceded by volume anomalies + volatility compression. Empirical weights from profiler.",
}


def format_strategy_card(result: dict, n_configs_tested: int) -> str:
    """Format a strategy card for output."""
    strat = result["strategy"]
    tf = result["timeframe"]
    params = result["params"]
    trail = result.get("trail_name", "medium")

    lines = [
        f"STRATEGY CARD: {strat} ({tf}, {', '.join(f'{k}={v}' for k, v in params.items())})",
        f"  Thesis: {STRATEGY_THESES.get(strat, 'N/A')}",
        f"  Stop: {result.get('stop_preset', 'wide')}, Trail: {trail}",
        f"  Walk-forward: PF {result.get('avg_test_pf', 0):.2f} at 1.5x slippage, "
        f"{result.get('consistency', 0)*100:.0f}% fold consistency",
    ]

    if "holdout_pf" in result:
        lines.append(
            f"  Holdout: PF {result['holdout_pf']:.2f}, Sharpe {result['holdout_sharpe']:.2f}, "
            f"DSR {result['dsr']:+.2f} (n_trials={n_configs_tested})"
        )

    # Example trades
    examples = result.get("example_trades", [])
    if examples:
        lines.append("  Example trades:")
        for ex in examples[:3]:
            ts_str = datetime.fromtimestamp(ex["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            pnl_pct = (ex["exit_price"] - ex["entry_price"]) / ex["entry_price"] * 100
            if ex["direction"] == "short":
                pnl_pct = -pnl_pct
            lines.append(
                f"    {ex['symbol']:>10}  {ts_str}  {ex['direction'].upper():>5}  "
                f"entry={ex['entry_price']:.2f}  exit={ex['exit_price']:.2f}  "
                f"({pnl_pct:+.1f}%)  {ex['exit_reason']}"
            )

    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    global _shared_ohlcv, _shared_matrices, _shared_folds, _shared_holdout

    parser = argparse.ArgumentParser(description="Signal Discovery v3 — Multi-TF Parallel Engine")
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--workers", type=int, default=16, help="Parallel workers")
    parser.add_argument("--timeframes", type=str, default="15m,1h,4h", help="Comma-separated TFs")
    parser.add_argument("--include-5m", action="store_true", help="Add 5m to timeframes")
    parser.add_argument("--pass1-only", action="store_true", help="Stop after Pass 1")
    parser.add_argument("--category", type=str, default=None, help="Run single category: A, B, C, or D")
    parser.add_argument("--holdout-days", type=int, default=18, help="Days reserved for holdout")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    t0 = time.time()
    timeframes = args.timeframes.split(",")
    if args.include_5m and "5m" not in timeframes:
        timeframes.append("5m")

    # ── 1. Memory estimation ─────────────────────────────────────────────
    logger.info("Estimating memory for %s (%dd)...", timeframes, args.days)
    mem_est = estimate_memory(database_url, args.days, timeframes)
    logger.info("Memory estimate: %.0f MB (%.1f%% of available %.0f MB RAM)",
                mem_est["total_mb"], mem_est["pct_ram"], mem_est["available_ram_mb"])
    for tf, info in mem_est["timeframes"].items():
        logger.info("  %s: %d rows → %.0f MB", tf, info["rows"], info["bytes"] / 1024**2)

    if mem_est["pct_ram"] > 80:
        logger.error("Estimated memory (%.0f MB) exceeds 80%% of available RAM (%.0f MB). Aborting.",
                      mem_est["total_mb"], mem_est["available_ram_mb"])
        logger.error("Try reducing timeframes or days.")
        sys.exit(1)

    # ── 2. Load OHLCV ────────────────────────────────────────────────────
    logger.info("Loading %dd of OHLCV for %s...", args.days, timeframes)
    _shared_ohlcv = load_ohlcv_multi_tf(database_url, args.days, timeframes)
    if not _shared_ohlcv:
        logger.error("No data loaded")
        sys.exit(1)

    # Build cross-sectional matrices per TF
    logger.info("Building cross-sectional matrices...")
    for tf in timeframes:
        close_mx, vol_mx, symbols, ts_grid = build_matrices(_shared_ohlcv, tf)
        if close_mx is not None:
            _shared_matrices[tf] = (close_mx, vol_mx, symbols, ts_grid)
            pct_valid = np.sum(~np.isnan(close_mx)) / close_mx.size * 100 if close_mx.size > 0 else 0
            logger.info("  %s: %d symbols × %d bars (%.1f%% valid)",
                        tf, len(symbols), len(ts_grid), pct_valid)

    # ── 2b. Pre-compute indicators (once in parent, shared via CoW) ─────
    global _shared_indicators, _shared_univ_temp, _shared_u1_indicators

    logger.info("Pre-computing indicators for %d symbol-tf combos...", len(_shared_ohlcv))
    t_ind = time.time()
    for key, data in _shared_ohlcv.items():
        _shared_indicators[key] = compute_indicators(data)
    logger.info("  Standard indicators: %.1fs", time.time() - t_ind)

    # Pre-compute universe temperature for U1's timeframes
    u1_timeframes = [tf for tf in ["15m", "1h"] if tf in _shared_matrices]
    for tf in u1_timeframes:
        symbols_tf = sorted(set(sym for (sym, t) in _shared_ohlcv if t == tf))
        _shared_univ_temp[tf] = compute_universe_temperature(_shared_ohlcv, tf, symbols_tf)
    logger.info("  Universe temperature: %.1fs (%d TFs)", time.time() - t_ind, len(u1_timeframes))

    # Pre-compute U1-specific per-symbol indicators (the dominant cost)
    t_u1 = time.time()
    n_u1 = 0
    for (sym, tf), data in _shared_ohlcv.items():
        if tf not in ("15m", "1h"):
            continue
        if sym in U1_MAJOR_SYMBOLS:
            continue
        closes = data["close"]
        highs = data["high"]
        lows = data["low"]
        opens = data["open"]
        volumes = data["volume"]
        n = len(closes)

        ind = _shared_indicators.get((sym, tf))
        if ind is None:
            continue
        bb_width = ind["bb_width"]

        vol_slope_5 = rolling_linear_slope_v3(volumes, 5)
        bb_width_pctile = rolling_percentile_v3(bb_width, min(200, n))
        vol_sma_20 = rolling_sma(volumes, 20)

        ema_20 = np.full(n, np.nan)
        if n >= 20:
            alpha = 2.0 / 21
            ema_20[19] = np.mean(closes[:20])
            for ei in range(20, n):
                ema_20[ei] = alpha * closes[ei] + (1 - alpha) * ema_20[ei - 1]

        returns_12 = np.zeros(n)
        if n > 12:
            returns_12[12:] = (closes[12:] - closes[:-12]) / np.where(
                closes[:-12] != 0, closes[:-12], 1)

        bar_range = highs - lows
        safe_range = np.where(bar_range > 0, bar_range, 1.0)
        clv = np.where(bar_range > 0, (closes - lows) / safe_range, 0.5)
        range_pct = np.where(closes > 0, bar_range / closes, 0)
        body_ratio = np.where(bar_range > 0, np.abs(closes - opens) / safe_range, 0)

        # Cumulative std of volumes (for s_vol_slope normalization)
        cs = np.cumsum(volumes)
        cs2 = np.cumsum(volumes ** 2)
        counts = np.arange(1, n + 1, dtype=np.float64)
        cum_var = cs2 / counts - (cs / counts) ** 2
        cum_vol_std = np.sqrt(np.maximum(0, cum_var))

        # Rolling max of vol_z over 6 bars
        vol_z = ind["vol_z"]
        vol_z_max_6 = np.full(n, 0.0)
        if n >= 6:
            from numpy.lib.stride_tricks import sliding_window_view
            windows = sliding_window_view(vol_z, 6)
            vol_z_max_6[5:] = np.max(windows, axis=1)
        for j in range(min(6, n)):
            vol_z_max_6[j] = np.max(vol_z[:j+1])

        # Pre-build universe temp arrays aligned to this symbol's ts_arr
        ts_arr = data["ts"]
        temp_dict = _shared_univ_temp.get(tf, {})
        temp_n_gt2 = np.zeros(n)
        temp_mean_vz = np.zeros(n)
        for j in range(n):
            td = temp_dict.get(float(ts_arr[j]))
            if td:
                temp_n_gt2[j] = td["n_gt2"]
                temp_mean_vz[j] = td["mean_vol_z"]

        _shared_u1_indicators[(sym, tf)] = {
            "vol_slope_5": vol_slope_5,
            "bb_width_pctile": bb_width_pctile,
            "vol_sma_20": vol_sma_20,
            "ema_20": ema_20,
            "returns_12": returns_12,
            "clv": clv,
            "range_pct": range_pct,
            "body_ratio": body_ratio,
            "cum_vol_std": cum_vol_std,
            "vol_z_max_6": vol_z_max_6,
            "temp_n_gt2": temp_n_gt2,
            "temp_mean_vz": temp_mean_vz,
        }
        n_u1 += 1
    logger.info("  U1 per-symbol indicators: %.1fs (%d symbols)", time.time() - t_u1, n_u1)
    logger.info("Total pre-compute: %.1fs", time.time() - t_ind)

    # ── 3. Compute folds + holdout ───────────────────────────────────────
    # Use the first available TF's ts_grid as reference
    ref_tf = timeframes[0]
    if ref_tf not in _shared_matrices:
        ref_tf = next(iter(_shared_matrices))
    ref_ts_grid = _shared_matrices[ref_tf][3]

    folds, holdout = compute_folds_and_holdout(ref_ts_grid, args.holdout_days)
    _shared_folds = folds
    _shared_holdout = holdout

    logger.info("Walk-forward: %d folds on days 0-%d",
                len(folds), int((holdout[0] - ref_ts_grid[WARMUP_BARS]) / 86400))
    for i, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
        logger.info("  Fold %d: train %s→%s, test %s→%s",
                    i+1,
                    datetime.fromtimestamp(tr_s, tz=timezone.utc).strftime("%m/%d"),
                    datetime.fromtimestamp(tr_e, tz=timezone.utc).strftime("%m/%d"),
                    datetime.fromtimestamp(te_s, tz=timezone.utc).strftime("%m/%d"),
                    datetime.fromtimestamp(te_e, tz=timezone.utc).strftime("%m/%d"))
    logger.info("  Holdout: %s→%s (SEALED until Pass 3)",
                datetime.fromtimestamp(holdout[0], tz=timezone.utc).strftime("%m/%d"),
                datetime.fromtimestamp(holdout[1], tz=timezone.utc).strftime("%m/%d"))

    # Validate fold boundaries: no test overlap
    for i in range(len(folds) - 1):
        assert folds[i][3] <= folds[i+1][2], f"Test periods overlap: fold {i} and {i+1}"

    # ── 4. Build work units ──────────────────────────────────────────────
    MAX_PARAMS_PER_CHUNK = 6  # default chunk size
    CHUNK_OVERRIDES = {"U1_IGNITION": 3}  # U1 still heavier per param even after vectorization
    COST_FACTORS = {"U1_IGNITION": 2.0}  # cost weight for sort ordering

    work_units = []
    for strat_name, strat_cfg in STRATEGY_REGISTRY.items():
        if args.category and strat_cfg["category"] != args.category:
            continue
        for tf in strat_cfg["timeframes"]:
            if tf not in timeframes:
                continue
            if tf not in _shared_matrices:
                continue
            grid = strat_cfg["param_grid"]
            # Split large param grids into chunks for parallelism
            chunk_sz = CHUNK_OVERRIDES.get(strat_name, MAX_PARAMS_PER_CHUNK)
            for chunk_start in range(0, len(grid), chunk_sz):
                chunk = grid[chunk_start:chunk_start + chunk_sz]
                work_units.append({
                    "strategy": strat_name,
                    "timeframe": tf,
                    "param_grid": chunk,
                    "needs_matrix": strat_cfg.get("needs_matrix", False),
                    "extra_slippage": strat_cfg.get("extra_slippage", 0),
                })

    total_configs = sum(
        len(wu["param_grid"]) * len(STOP_PRESETS) for wu in work_units
    )
    logger.info("Pass 1: %d work units, %d total configs", len(work_units), total_configs)

    # ── 5. PASS 1 — Fast screen ─────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"PASS 1 — FAST SCREEN ({total_configs} configs, 5-fold walk-forward on days 0-91)")
    print("=" * 100)

    p1_start = time.time()
    n_workers = min(args.workers, len(work_units))

    # Cost-aware sort: heaviest chunks first for better load balancing
    work_units.sort(
        key=lambda wu: len(wu["param_grid"]) * COST_FACTORS.get(wu["strategy"], 1.0),
        reverse=True,
    )

    if n_workers > 1:
        ctx = get_context("fork")
        with ctx.Pool(n_workers) as pool:
            all_results_nested = []
            for i, result in enumerate(pool.imap_unordered(pass1_worker, work_units)):
                all_results_nested.append(result)
                wu = work_units[min(i, len(work_units)-1)]
                elapsed = time.time() - p1_start
                logger.info("  Pass 1: %d/%d work units done (%.1fs elapsed)",
                            i+1, len(work_units), elapsed)
    else:
        all_results_nested = []
        for i, wu in enumerate(work_units):
            result = pass1_worker(wu)
            all_results_nested.append(result)
            elapsed = time.time() - p1_start
            logger.info("  Pass 1: %d/%d work units done (%.1fs elapsed)",
                        i+1, len(work_units), elapsed)

    all_pass1 = [r for batch in all_results_nested for r in batch]
    logger.info("Pass 1 complete: %d configs evaluated in %.1fs", len(all_pass1), time.time() - p1_start)

    # Print Pass 1 results table
    print(f"\n{'Strategy':>12} {'TF':>4} {'Params':>30} {'Stop':>6} {'AvgPF':>6} "
          f"{'Consist':>7} {'Trades':>7} {'FF%':>5} {'Status':>8}")
    print("-" * 95)

    # Filter survivors: avg test PF > 0.8 AND consistency >= 60% AND >= 25 total test trades
    survivors = []
    for r in sorted(all_pass1, key=lambda x: x["avg_test_pf"], reverse=True):
        passes = (r["avg_test_pf"] > 0.8 and r["consistency"] >= 0.60
                  and r["total_trades"] >= 25)
        status = "SURV" if passes else "—"
        param_str = ",".join(f"{k}={v}" for k, v in r["params"].items())[:30]
        print(f"{r['strategy']:>12} {r['timeframe']:>4} {param_str:>30} {r['stop_preset']:>6} "
              f"{r['avg_test_pf']:>6.2f} {r['consistency']*100:>5.0f}%  {r['total_trades']:>6} "
              f"{r['ff_rate']*100:>4.0f}% {status:>8}")
        if passes:
            survivors.append(r)

    print(f"\nSurvivors: {len(survivors)} / {len(all_pass1)} ({len(survivors)/max(len(all_pass1),1)*100:.1f}%)")

    if args.pass1_only:
        print(f"\n--pass1-only flag set. Stopping after Pass 1.")
        logger.info("Total time: %.1fs", time.time() - t0)
        return

    if not survivors:
        print("\nNo survivors from Pass 1. No strategies meet the criteria.")
        logger.info("Total time: %.1fs", time.time() - t0)
        return

    # ── 6. PASS 2 — Detailed sim ────────────────────────────────────────
    p2_configs = len(survivors) * len(TRAIL_CONFIGS) * 3  # × 3 slippage levels
    print(f"\n{'=' * 100}")
    print(f"PASS 2 — DETAILED SIM ({len(survivors)} survivors × {len(TRAIL_CONFIGS)} trails × 3 slippage = {p2_configs} evaluations)")
    print("=" * 100)

    p2_start = time.time()
    p2_work = [{"survivor": s} for s in survivors]

    if n_workers > 1:
        ctx = get_context("fork")
        with ctx.Pool(min(n_workers, len(p2_work))) as pool:
            p2_results_nested = []
            for i, result in enumerate(pool.imap_unordered(pass2_worker, p2_work)):
                p2_results_nested.append(result)
                elapsed = time.time() - p2_start
                logger.info("  Pass 2: %d/%d survivors done (%.1fs elapsed)",
                            i+1, len(p2_work), elapsed)
    else:
        p2_results_nested = []
        for i, wu in enumerate(p2_work):
            result = pass2_worker(wu)
            p2_results_nested.append(result)
            elapsed = time.time() - p2_start
            logger.info("  Pass 2: %d/%d survivors done (%.1fs elapsed)",
                        i+1, len(p2_work), elapsed)

    all_pass2 = [r for batch in p2_results_nested for r in batch]
    logger.info("Pass 2 complete: %d evaluations in %.1fs", len(all_pass2), time.time() - p2_start)

    # Print Pass 2 results
    print(f"\n{'Strategy':>12} {'TF':>4} {'Trail':>6} {'Slip':>5} {'AvgPF':>6} "
          f"{'Con':>4} {'Tr/d':>5} {'FF%':>5} {'Status':>8}")
    print("-" * 70)

    # Gate: avg PF > 1.0 at 1.5× slippage AND consistency >= 80% AND >= 5 trades/fold
    pass2_winners = []
    for r in sorted(all_pass2, key=lambda x: (-x["avg_test_pf"])):
        is_1_5x = abs(r["slippage_mult"] - 1.5) < 0.01
        min_trades_per_fold = all(fm["test_n"] >= 5 for fm in r["fold_metrics"])
        passes = (is_1_5x and r["avg_test_pf"] > 1.0
                  and r["consistency"] >= 0.80 and min_trades_per_fold)
        status = "PASS" if passes else "—"

        # Only print base, 1.5×, and passing entries to reduce noise
        if r["slippage_mult"] in [1.0, 1.5, 2.0]:
            param_str = ",".join(f"{k}={v}" for k, v in r["params"].items())[:20]
            print(f"{r['strategy']:>12} {r['timeframe']:>4} {r['trail_name']:>6} "
                  f"{r['slippage_mult']:>4.1f}x {r['avg_test_pf']:>6.2f} "
                  f"{r['consistency']*100:>3.0f}% {r['trades_per_day']:>5.1f} "
                  f"{r['ff_rate']*100:>4.0f}% {status:>8}"
                  + (" <-" if passes else ""))

        if passes:
            pass2_winners.append(r)

    # Deduplicate: keep best trail per (strategy, tf, params)
    seen = set()
    pass2_unique = []
    for w in sorted(pass2_winners, key=lambda x: -x["avg_test_pf"]):
        key = (w["strategy"], w["timeframe"], json.dumps(w["params"], sort_keys=True))
        if key not in seen:
            seen.add(key)
            pass2_unique.append(w)

    print(f"\nPASS at 1.5x slippage: {len(pass2_unique)} configs (from {len(pass2_winners)} before dedup)")

    if not pass2_unique:
        print("\nNo strategies pass the 1.5x slippage gate.")
        logger.info("Total time: %.1fs", time.time() - t0)
        return

    # ── 7. PASS 3 — Holdout confirmation + DSR ──────────────────────────
    holdout_start, holdout_end = _shared_holdout

    print(f"\n{'=' * 100}")
    print(f"PASS 3 — HOLDOUT CONFIRMATION (days 92-110, never seen before)")
    print(f"  Testing {len(pass2_unique)} strategies on holdout period")
    print(f"  DSR correction for n_trials = {total_configs}")
    print("=" * 100)

    final_results = []

    for winner in pass2_unique:
        strat_name = winner["strategy"]
        tf = winner["timeframe"]
        params = winner["params"]
        stop_preset = winner["stop_preset"]
        trail_name = winner["trail_name"]
        stop_cfg = STOP_PRESETS[stop_preset]
        trail_cfg = TRAIL_CONFIGS[trail_name]
        strat_reg = STRATEGY_REGISTRY[strat_name]
        strat_func = strat_reg["func"]
        extra_slip = strat_reg.get("extra_slippage", 0)
        base_slippage = TF_SLIPPAGE.get(tf, 5.0) + extra_slip
        needs_matrix = strat_reg.get("needs_matrix", False)

        symbols = sorted(set(sym for (sym, tf_) in _shared_ohlcv if tf_ == tf))

        # Use pre-computed indicators
        indicators = {k: v for k, v in _shared_indicators.items() if k[1] == tf}

        matrix_data = _shared_matrices.get(tf)

        sig_kwargs = {
            "indicators": indicators,
            "ohlcv_data": _shared_ohlcv,
            "symbols": symbols,
            "tf": tf,
            "ts_grid": matrix_data[3] if matrix_data else np.array([]),
            **params,
        }
        if needs_matrix and matrix_data:
            sig_kwargs["close_mx"] = matrix_data[0]
        if strat_name == "U1_IGNITION":
            sig_kwargs["universe_temp"] = _shared_univ_temp.get(tf, {})

        # Use 1.5× slippage for holdout (conservative)
        slippage = base_slippage * 1.5

        holdout_signals = strat_func(**sig_kwargs, min_ts=holdout_start, max_ts=holdout_end)
        holdout_trades = simulate_trades(
            holdout_signals, _shared_ohlcv, tf,
            stop_cfg["atr_mult"], stop_cfg["time_bars"],
            slippage, trail_cfg, indicators=indicators,
        )

        m = compute_metrics(holdout_trades)

        # Compute DSR
        if m["n"] > 1:
            pnl_arr = np.array([t.pnl for t in holdout_trades])
            if len(pnl_arr) > 2:
                n_pnl = len(pnl_arr)
                mean_pnl = np.mean(pnl_arr)
                std_pnl = np.std(pnl_arr, ddof=1)
                if std_pnl > 0:
                    m3 = np.mean(((pnl_arr - mean_pnl) / std_pnl) ** 3)
                    m4 = np.mean(((pnl_arr - mean_pnl) / std_pnl) ** 4)
                    skew = float(m3 * (n_pnl * (n_pnl - 1))**0.5 / (n_pnl - 2)) if n_pnl > 2 else 0
                    kurt = float(m4)  # excess kurtosis not needed, use raw (Fisher=False equiv)
                else:
                    skew, kurt = 0.0, 3.0
            else:
                skew, kurt = 0.0, 3.0
            dsr = deflated_sharpe_ratio(m["sharpe"], total_configs, m["n"], skew, kurt)
        else:
            dsr = -99

        # Get example trades
        example_trades = []
        for t in sorted(holdout_trades, key=lambda t: abs(t.pnl), reverse=True)[:3]:
            example_trades.append({
                "symbol": t.symbol, "direction": t.direction,
                "ts": _shared_ohlcv[(t.symbol, tf)]["ts"][t.ts_idx],
                "entry_price": round(t.entry_price, 4),
                "exit_price": round(t.exit_price, 4),
                "exit_reason": t.exit_reason,
                "pnl": round(t.pnl, 2),
            })

        verdict = "CONFIRMED" if m["pf"] > 1.0 and dsr > 0 else "REJECTED"

        final_results.append({
            "strategy": strat_name,
            "timeframe": tf,
            "params": params,
            "stop_preset": stop_preset,
            "trail_name": trail_name,
            "holdout_pf": m["pf"],
            "holdout_wr": m["wr"],
            "holdout_sharpe": m["sharpe"],
            "dsr": dsr,
            "holdout_trades": m["n"],
            "holdout_pnl": m["pnl"],
            "avg_test_pf": winner["avg_test_pf"],
            "consistency": winner["consistency"],
            "verdict": verdict,
            "example_trades": example_trades,
        })

    # Print holdout results
    print(f"\n{'Strategy':>12} {'TF':>4} {'HoldPF':>7} {'HoldSR':>7} {'DSR':>7} "
          f"{'Trades':>7} {'Verdict':>12}")
    print("-" * 65)

    for r in final_results:
        marker = " +" if r["verdict"] == "CONFIRMED" else " x"
        print(f"{r['strategy']:>12} {r['timeframe']:>4} {r['holdout_pf']:>7.2f} "
              f"{r['holdout_sharpe']:>7.2f} {r['dsr']:>+7.2f} {r['holdout_trades']:>7} "
              f"{r['verdict']:>12}{marker}")

    # Print strategy cards for confirmed
    confirmed = [r for r in final_results if r["verdict"] == "CONFIRMED"]

    print(f"\n{'-' * 100}")
    for r in confirmed:
        print()
        print(format_strategy_card(r, total_configs))
    print(f"\n{'-' * 100}")

    n_confirmed = len(confirmed)
    n_rejected = len(final_results) - n_confirmed
    print(f"\nFINAL: {n_confirmed} strategies CONFIRMED after holdout + DSR correction."
          f" ({n_rejected} rejected)")

    # ── 8. JSON export ───────────────────────────────────────────────────
    output_path = os.path.join(os.path.dirname(__file__), "signal_discovery_v3_results.json")
    export = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "days": args.days,
            "timeframes": timeframes,
            "holdout_days": args.holdout_days,
            "n_folds": len(folds),
            "total_configs_tested": total_configs,
            "pass1_survivors": len(survivors),
            "pass2_winners": len(pass2_unique),
        },
        "confirmed_strategies": [
            {
                "strategy": r["strategy"],
                "timeframe": r["timeframe"],
                "params": r["params"],
                "stop_preset": r["stop_preset"],
                "trail_name": r["trail_name"],
                "walk_forward_pf": r["avg_test_pf"],
                "walk_forward_consistency": r["consistency"],
                "holdout_pf": r["holdout_pf"],
                "holdout_sharpe": r["holdout_sharpe"],
                "dsr": r["dsr"],
                "holdout_trades": r["holdout_trades"],
                "thesis": STRATEGY_THESES.get(r["strategy"], ""),
                "example_trades": r["example_trades"],
            }
            for r in confirmed
        ],
        "rejected_strategies": [
            {
                "strategy": r["strategy"],
                "timeframe": r["timeframe"],
                "params": r["params"],
                "holdout_pf": r["holdout_pf"],
                "holdout_sharpe": r["holdout_sharpe"],
                "dsr": r["dsr"],
                "verdict": r["verdict"],
            }
            for r in final_results if r["verdict"] != "CONFIRMED"
        ],
        "all_pass1_results": [
            {
                "strategy": r["strategy"],
                "timeframe": r["timeframe"],
                "params": r["params"],
                "stop_preset": r["stop_preset"],
                "avg_test_pf": r["avg_test_pf"],
                "consistency": r["consistency"],
                "total_trades": r["total_trades"],
            }
            for r in sorted(all_pass1, key=lambda x: -x["avg_test_pf"])[:50]
        ],
    }

    with open(output_path, "w") as f:
        json.dump(export, f, indent=2, default=str)
    logger.info("Results written to %s", output_path)

    elapsed = time.time() - t0
    logger.info("Total time: %.1fs (%.1f min)", elapsed, elapsed / 60)

    # Data coverage report
    print(f"\n{'DATA COVERAGE':^100}")
    print("=" * 100)
    for tf in timeframes:
        if tf in _shared_matrices:
            _, _, symbols, ts_grid = _shared_matrices[tf]
            print(f"  {tf}: {len(symbols)} symbols, {len(ts_grid)} bars, "
                  f"span {datetime.fromtimestamp(ts_grid[0], tz=timezone.utc).strftime('%Y-%m-%d')} → "
                  f"{datetime.fromtimestamp(ts_grid[-1], tz=timezone.utc).strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    main()
