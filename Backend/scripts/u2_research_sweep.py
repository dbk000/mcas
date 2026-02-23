#!/usr/bin/env python3
"""
U2 Research Sweep — Systematic parameter exploration for Unicorn Ignition.

All-in-one: load data -> precompute features -> sweep params -> simulate trades
-> walk-forward validate -> DSR correction -> output results.

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/u2_research_sweep.py [--days 120]

Output: /scripts/u2_research_results.json
"""

import os
import sys
import time
import json
import hashlib
import logging
import argparse
import numpy as np
from datetime import datetime, timezone
from collections import defaultdict
from multiprocessing import Pool, cpu_count
from dataclasses import dataclass, field
from typing import Optional

# Path setup
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/indicators.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))
sys.path.insert(0, os.path.join(os.path.abspath(worker_app_dir), 'setups'))

import psycopg
from indicators import OHLCV

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

# =============================================================================
# Baseline filters (baked in, not swept)
# =============================================================================
EXCLUDED_SYMBOLS = {
    "SLFUSDT", "ALPACAUSDT", "PDAUSDT", "WINGUSDT", "VIDTUSDT",
    "VIBUSDT", "PROSUSDT", "BETAUSDT", "HARDUSDT", "FIROUSDT",
    "BURGERUSDT", "TROYUSDT", "AMBUSDT", "WANUSDT",
}

MAJOR_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "LTCUSDT", "SHIBUSDT", "TRXUSDT", "ATOMUSDT",
}

STABLECOIN_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD"}

# Exit config (locked — wide profile, not swept)
EXIT_CONFIG = {
    "stop_atr_mult": 4.0,       # 4x ATR stop (wide, validated for rare-event models)
    "tp1_r": 1.0,
    "tp2_r": 2.0,
    "fail_fast_bars": 2,
    "fail_fast_threshold": 0.003,
    "trail_breakeven_r": 0.75,
    "trail_activation_r": 1.0,
    "trail_distance_r": 0.5,
    "max_bars": 48,             # 48h time stop on 1h
}

# Transaction costs (1h timeframe)
FEE_BPS = 4
SLIPPAGE_BPS = 5
TOTAL_COST_BPS = FEE_BPS + SLIPPAGE_BPS  # 9 bps one-way, 18 round trip
SLIPPAGE_MULT_TEST = 1.5  # Also test at 1.5x slippage


# =============================================================================
# Data Loading
# =============================================================================

def load_ohlcv_1h(days: int) -> tuple[dict[str, np.ndarray], list[str], np.ndarray]:
    """
    Load 1h OHLCV from Postgres. Returns:
    - ohlcv_dict: {symbol: (n_bars, 6) array [ts_ms, o, h, l, c, v]}
    - symbols: sorted list of valid symbols
    - timestamps: unique sorted timestamps array (union of all symbols)
    """
    logger.info("Loading 1h OHLCV for %d days...", days)
    raw = defaultdict(list)
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE timeframe = '1h' AND is_final = TRUE
                  AND bar_start_ts >= NOW() - %s * INTERVAL '1 day'
                ORDER BY symbol, bar_start_ts ASC
            """, (days,))
            for row in cur:
                symbol, bar_ts, o, h, l, c, v = row
                ts_ms = int(bar_ts.timestamp() * 1000)
                raw[symbol].append([ts_ms, float(o), float(h), float(l), float(c), float(v)])

    # Filter symbols
    valid_symbols = []
    for sym in sorted(raw.keys()):
        if sym in EXCLUDED_SYMBOLS:
            continue
        base = sym[:-4] if sym.endswith("USDT") else sym
        if base in STABLECOIN_BASES:
            continue
        if any(base == p for p in ["WBTC", "WETH", "WBNB"]):
            continue
        if any(base.endswith(p) for p in ["UP", "DOWN", "BULL", "BEAR", "3L", "3S"]):
            continue
        valid_symbols.append(sym)

    ohlcv_dict = {}
    for sym in valid_symbols:
        arr = np.array(raw[sym], dtype=np.float64)
        if len(arr) >= 96:  # Need at least 96 bars (2 windows)
            ohlcv_dict[sym] = arr

    symbols = sorted(ohlcv_dict.keys())
    all_ts = set()
    for sym in symbols:
        all_ts.update(ohlcv_dict[sym][:, 0].astype(np.int64).tolist())
    timestamps = np.array(sorted(all_ts), dtype=np.int64)

    total_bars = sum(len(v) for v in ohlcv_dict.values())
    logger.info("Loaded %d bars across %d valid symbols, %d unique timestamps",
                total_bars, len(symbols), len(timestamps))
    return ohlcv_dict, symbols, timestamps


def load_social_symbols(days: int = 30) -> set[str]:
    """Load symbols that have social mentions in last N days (to exclude)."""
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT symbol FROM social_features_1h
                    WHERE bar_start_ts >= NOW() - %s * INTERVAL '1 day'
                """, (days,))
                return {row[0] for row in cur}
    except Exception as e:
        logger.warning("Could not load social features: %s", e)
        return set()


# =============================================================================
# Feature Precomputation (vectorized per symbol)
# =============================================================================

@dataclass
class SymbolFeatures:
    """Precomputed features for one symbol as numpy arrays."""
    symbol: str
    ts_ms: np.ndarray          # (n,) timestamps
    close: np.ndarray          # (n,) close prices
    high: np.ndarray           # (n,) high prices
    low: np.ndarray            # (n,) low prices
    open_: np.ndarray          # (n,) open prices
    volume: np.ndarray         # (n,) volumes
    # Gate 1 features (rolling 48-bar windows)
    spread_ratio: np.ndarray   # median(range/close, last48) / median(range/close, prev48)
    vol_cv_ratio: np.ndarray   # cv(vol, last48) / cv(vol, prev48)
    atr_ratio: np.ndarray      # atr_pct(last48) / atr_pct(prev48)
    # Gate 2 features
    vol_growth: np.ndarray     # sum(vol, last48) / sum(vol, prev48)
    active_bar_growth: np.ndarray  # active_bars(last48) / active_bars(prev48)
    whale_ratio: np.ndarray    # max_vol / mean_vol in last48
    # Gate 3 features
    range_pct: np.ndarray      # (max_high - min_low) / close over last48
    atr_contraction: np.ndarray  # atr_pct(last48) / atr_pct(prev48)
    vol_slope: np.ndarray      # linear slope of volume over last48
    rel_volume: np.ndarray     # current_vol / baseline_vol
    # Ignition features
    vol_z: np.ndarray          # 20-bar volume z-score
    vol_z_max_6: np.ndarray    # max vol_z over last 6 bars
    bb_width_pctile: np.ndarray  # BB width percentile
    ignition_score: np.ndarray  # combined ignition score
    # ATR for stop placement
    atr_20: np.ndarray         # 20-bar ATR in price terms
    # Median volume for universe classification
    median_vol_48: np.ndarray  # rolling 48-bar median volume


def _rolling_median(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling median using sorted insertion. Returns array same length, NaN-padded."""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        result[i] = np.median(arr[i - window + 1:i + 1])
    return result


def _rolling_sum(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling sum, NaN-padded."""
    cs = np.cumsum(arr)
    result = np.full(len(arr), np.nan)
    result[window - 1] = cs[window - 1]
    result[window:] = cs[window:] - cs[:-window]
    return result


def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling mean."""
    return _rolling_sum(arr, window) / window


def _rolling_std(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling std (population)."""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        result[i] = np.std(arr[i - window + 1:i + 1])
    return result


def _rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling max."""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        result[i] = np.max(arr[i - window + 1:i + 1])
    return result


def _rolling_min(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling min."""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        result[i] = np.min(arr[i - window + 1:i + 1])
    return result


def _count_nonzero_rolling(arr: np.ndarray, window: int) -> np.ndarray:
    """Count of nonzero values in rolling window."""
    nz = (arr > 0).astype(np.float64)
    return _rolling_sum(nz, window)


def _rolling_slope(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling linear regression slope."""
    n = len(arr)
    result = np.full(n, np.nan)
    x = np.arange(window, dtype=np.float64)
    x_mean = x.mean()
    x_var = np.sum((x - x_mean) ** 2)
    if x_var == 0:
        return result
    for i in range(window - 1, n):
        y = arr[i - window + 1:i + 1]
        y_mean = y.mean()
        result[i] = np.sum((x - x_mean) * (y - y_mean)) / x_var
    return result


def precompute_symbol_features(ohlcv: np.ndarray, symbol: str) -> Optional[SymbolFeatures]:
    """Precompute all gate and ignition features for one symbol."""
    n = len(ohlcv)
    if n < 96:
        return None

    ts_ms = ohlcv[:, 0].astype(np.int64)
    opens = ohlcv[:, 1]
    highs = ohlcv[:, 2]
    lows = ohlcv[:, 3]
    closes = ohlcv[:, 4]
    volumes = ohlcv[:, 5]

    W = 48  # gate window

    # --- Range / close for each bar ---
    bar_range = highs - lows
    range_over_close = np.where(closes > 0, bar_range / closes, 0)

    # Gate 1: spread ratio, vol CV ratio, ATR ratio
    spread_med_last = _rolling_median(range_over_close, W)
    spread_med_prev = np.full(n, np.nan)
    spread_med_prev[W:] = spread_med_last[:-W]
    # Avoid shifting issue: for bar i, prev window is [i-2W, i-W), last is [i-W, i)
    # spread_med_last[i] = median of [i-47..i], so prev = spread_med_last[i-48]
    spread_ratio = np.where(spread_med_prev > 0, spread_med_last / spread_med_prev, np.nan)

    vol_mean_last = _rolling_mean(volumes, W)
    vol_std_last = _rolling_std(volumes, W)
    vol_cv_last = np.where(vol_mean_last > 0, vol_std_last / vol_mean_last, np.inf)

    vol_mean_prev = np.full(n, np.nan)
    vol_std_prev = np.full(n, np.nan)
    vol_mean_prev[W:] = vol_mean_last[:-W]
    vol_std_prev[W:] = vol_std_last[:-W]
    vol_cv_prev = np.where(vol_mean_prev > 0, vol_std_prev / vol_mean_prev, np.inf)
    vol_cv_ratio = np.where(vol_cv_prev > 0, vol_cv_last / vol_cv_prev, np.nan)

    # ATR % (14-bar)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr_14 = _rolling_mean(tr, 14)
    atr_pct = np.where(closes > 0, atr_14 / closes, 0)
    # ATR% over last 48 vs prev 48: approximate by comparing atr_pct at bar i vs i-48
    atr_pct_prev = np.full(n, np.nan)
    atr_pct_prev[W:] = atr_pct[:-W]
    atr_ratio = np.where(atr_pct_prev > 0, atr_pct / atr_pct_prev, np.nan)

    # Gate 2: volume growth, active bar growth, whale ratio
    vol_sum_last = _rolling_sum(volumes, W)
    vol_sum_prev = np.full(n, np.nan)
    vol_sum_prev[W:] = vol_sum_last[:-W]
    vol_growth = np.where(vol_sum_prev > 0, vol_sum_last / vol_sum_prev, np.nan)

    active_last = _count_nonzero_rolling(volumes, W)
    active_prev = np.full(n, np.nan)
    active_prev[W:] = active_last[:-W]
    active_bar_growth = np.where(active_prev > 0, active_last / active_prev, np.nan)

    vol_max_last = _rolling_max(volumes, W)
    whale_ratio = np.where(vol_mean_last > 0, vol_max_last / vol_mean_last, np.inf)

    # Gate 3: range_pct (high-low range over last 48), vol_slope
    high_48 = _rolling_max(highs, W)
    low_48 = _rolling_min(lows, W)
    range_pct_48 = np.where(closes > 0, (high_48 - low_48) / closes, 0)

    atr_contraction = atr_ratio.copy()  # same as gate 1 ATR ratio

    vol_slope_48 = _rolling_slope(volumes, W)

    # Baseline volume (median of all available, capped at 2016 bars = 84 days on 1h)
    baseline_window = min(n, 2016)
    baseline_vol = _rolling_median(volumes, baseline_window)
    # For early bars where we don't have full window, use what we have
    for i in range(baseline_window - 1):
        if i > 0:
            baseline_vol[i] = np.median(volumes[:i + 1])
    rel_volume = np.where(baseline_vol > 0, volumes / baseline_vol, 0)

    # Ignition features
    vol_mean_20 = _rolling_mean(volumes, 20)
    vol_std_20 = _rolling_std(volumes, 20)
    vol_z = np.where(vol_std_20 > 0, (volumes - vol_mean_20) / vol_std_20, 0)

    # vol_z_max_6
    vol_z_max_6 = _rolling_max(vol_z, 6)

    # BB width percentile
    sma_20 = _rolling_mean(closes, 20)
    std_20 = _rolling_std(closes, 20)
    bb_width = np.where(sma_20 > 0, (4 * std_20) / sma_20, 0)
    # Percentile of current bb_width vs all prior bb_widths
    bb_pctile = np.full(n, 50.0)
    for i in range(20, n):
        history = bb_width[20:i]
        if len(history) > 1:
            bb_pctile[i] = np.sum(history < bb_width[i]) / len(history) * 100

    # Ignition score (simplified vectorized version)
    s_vol_z = np.clip(np.maximum(0, vol_z) / 5.0, 0, 1)
    s_vol_z_max = np.clip(np.maximum(0, vol_z_max_6) / 5.0, 0, 1)
    s_bb_compress = 1.0 - bb_pctile / 100.0
    s_range = np.clip(range_over_close / 0.05, 0, 1)  # current bar range

    # 12-bar return
    ret_12 = np.zeros(n)
    ret_12[12:] = np.where(closes[:-12] > 0, (closes[12:] - closes[:-12]) / closes[:-12], 0)
    s_return = np.clip(np.maximum(0, ret_12) * 5, 0, 1)

    # CLV
    clv = np.where(bar_range > 0, (closes - lows) / bar_range, 0.5)
    # Body ratio
    body = np.abs(closes - opens)
    body_ratio = np.where(bar_range > 0, body / bar_range, 0.5)

    # Combined score (U1Config weights)
    ignition_score = (
        0.25 * s_vol_z +
        0.15 * s_vol_z_max +
        0.10 * np.zeros(n) +  # vol_slope normalized — hard to do per-bar, approximate as 0
        0.15 * s_bb_compress +
        0.10 * s_range +
        0.10 * s_return +
        0.08 * clv +
        0.07 * body_ratio
    )
    # Scale by (1 - W_UNIVERSE_TEMP) since we don't have universe temp in research
    ignition_score *= 0.85

    # ATR for stop placement (20-bar mean range)
    mean_range_20 = _rolling_mean(bar_range, 20)

    # Median volume for universe classification
    median_vol_48 = _rolling_median(volumes, W)

    return SymbolFeatures(
        symbol=symbol,
        ts_ms=ts_ms,
        close=closes,
        high=highs,
        low=lows,
        open_=opens,
        volume=volumes,
        spread_ratio=spread_ratio,
        vol_cv_ratio=vol_cv_ratio,
        atr_ratio=atr_ratio,
        vol_growth=vol_growth,
        active_bar_growth=active_bar_growth,
        whale_ratio=whale_ratio,
        range_pct=range_pct_48,
        atr_contraction=atr_contraction,
        vol_slope=vol_slope_48,
        rel_volume=rel_volume,
        vol_z=vol_z,
        vol_z_max_6=vol_z_max_6,
        bb_width_pctile=bb_pctile,
        ignition_score=ignition_score,
        atr_20=mean_range_20,
        median_vol_48=median_vol_48,
    )


def _precompute_worker(args):
    """Worker for parallel precomputation."""
    ohlcv_arr, symbol = args
    return precompute_symbol_features(ohlcv_arr, symbol)


# =============================================================================
# Config definition and signal generation
# =============================================================================

@dataclass
class SweepConfig:
    """One parameter configuration to evaluate."""
    config_id: int
    max_volume_usd: float = 1_000_000
    exclude_majors: bool = True
    gate2_pass_of_3: int = 3       # How many of 3 gate2 sub-metrics must pass
    gate3_pass_of_3: int = 3       # How many of 3 gate3 sub-metrics must pass
    participation_growth: float = 1.5
    range_pct_threshold: float = 0.05
    ignition_score_threshold: float = 0.55
    vol_z_min: float = 2.0
    rel_vol_ignition: float = 5.0
    acceptance_bars: int = 2
    confirmations_required: int = 2


def generate_configs() -> list[SweepConfig]:
    """Generate ~400 configs: anchored sweep + focused grid + latin hypercube."""
    configs = []
    config_id = 0

    # Defaults
    D = SweepConfig(config_id=0)

    # --- Phase 1: Anchored sweep (~50 configs) ---
    # Vary one param at a time, rest at defaults

    for val in [1_000_000, 5_000_000, 50_000_000]:
        if val != D.max_volume_usd:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, max_volume_usd=val))

    for val in [True, False]:
        if val != D.exclude_majors:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, exclude_majors=val))

    for val in [1, 2, 3]:
        if val != D.gate2_pass_of_3:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, gate2_pass_of_3=val))

    for val in [1, 2, 3]:
        if val != D.gate3_pass_of_3:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, gate3_pass_of_3=val))

    for val in [1.0, 1.2, 1.5, 2.0]:
        if val != D.participation_growth:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, participation_growth=val))

    for val in [0.03, 0.05, 0.08, 0.10, 0.15]:
        if val != D.range_pct_threshold:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, range_pct_threshold=val))

    for val in [0.25, 0.35, 0.45, 0.55]:
        if val != D.ignition_score_threshold:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, ignition_score_threshold=val))

    for val in [1.0, 1.5, 2.0, 2.5]:
        if val != D.vol_z_min:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, vol_z_min=val))

    for val in [2.0, 3.0, 5.0, 8.0]:
        if val != D.rel_vol_ignition:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, rel_vol_ignition=val))

    for val in [1, 2]:
        if val != D.acceptance_bars:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, acceptance_bars=val))

    for val in [1, 2]:
        if val != D.confirmations_required:
            config_id += 1
            configs.append(SweepConfig(config_id=config_id, confirmations_required=val))

    # Add the default config itself
    config_id += 1
    configs.append(SweepConfig(config_id=config_id))

    n_anchored = len(configs)
    logger.info("Phase 1: %d anchored configs", n_anchored)

    # --- Phase 2: Focused grid on top impactful params ---
    # From prior analysis: gate2_pass, gate3_pass, ignition_score_threshold, vol_z_min
    # are the most impactful (each removes 90%+ when strict)
    for g2 in [1, 2, 3]:
        for g3 in [1, 2, 3]:
            for ign in [0.25, 0.35, 0.45, 0.55]:
                for vz in [1.0, 1.5, 2.0]:
                    config_id += 1
                    configs.append(SweepConfig(
                        config_id=config_id,
                        gate2_pass_of_3=g2,
                        gate3_pass_of_3=g3,
                        ignition_score_threshold=ign,
                        vol_z_min=vz,
                    ))

    n_grid = len(configs) - n_anchored
    logger.info("Phase 2: %d focused grid configs", n_grid)

    # --- Phase 3: Latin hypercube sampling (~150 random) ---
    rng = np.random.default_rng(42)
    for _ in range(150):
        config_id += 1
        configs.append(SweepConfig(
            config_id=config_id,
            max_volume_usd=rng.choice([1_000_000, 5_000_000, 50_000_000]),
            exclude_majors=rng.choice([True, False]),
            gate2_pass_of_3=int(rng.choice([1, 2, 3])),
            gate3_pass_of_3=int(rng.choice([1, 2, 3])),
            participation_growth=float(rng.choice([1.0, 1.2, 1.5, 2.0])),
            range_pct_threshold=float(rng.choice([0.03, 0.05, 0.08, 0.10, 0.15])),
            ignition_score_threshold=float(rng.choice([0.25, 0.35, 0.45, 0.55])),
            vol_z_min=float(rng.choice([1.0, 1.5, 2.0, 2.5])),
            rel_vol_ignition=float(rng.choice([2.0, 3.0, 5.0, 8.0])),
            acceptance_bars=int(rng.choice([1, 2])),
            confirmations_required=int(rng.choice([1, 2])),
        ))

    logger.info("Phase 3: 150 LHS configs | Total: %d", len(configs))
    return configs


# =============================================================================
# Signal Generation (vectorized per config)
# =============================================================================

def evaluate_config(
    cfg: SweepConfig,
    features_list: list[SymbolFeatures],
    social_symbols: set[str],
    eval_start_idx: int,
) -> list[dict]:
    """
    Evaluate one config across all symbols. Returns list of signal dicts.
    Each signal: {symbol, bar_idx, ts_ms, entry_price, atr_20, score}
    """
    signals = []

    for feat in features_list:
        sym = feat.symbol
        n = len(feat.close)

        # Universe filter
        med_vol = feat.median_vol_48
        # Use the last valid median volume for universe check
        last_valid_vol = med_vol[~np.isnan(med_vol)]
        if len(last_valid_vol) == 0:
            continue
        typical_vol = float(np.median(last_valid_vol[-96:]) if len(last_valid_vol) >= 96 else np.median(last_valid_vol))
        if typical_vol > cfg.max_volume_usd:
            continue
        if typical_vol < 10_000:  # MIN_VOLUME always enforced
            continue
        if cfg.exclude_majors and sym in MAJOR_SYMBOLS:
            continue

        # Social silence filter
        if sym in social_symbols:
            continue

        # Evaluate each bar from eval_start_idx onwards
        for i in range(max(96, eval_start_idx), n):
            # Gate 1: tradability (pass 2 of 3)
            sr = feat.spread_ratio[i]
            vcr = feat.vol_cv_ratio[i]
            ar = feat.atr_ratio[i]
            if np.isnan(sr) or np.isnan(vcr) or np.isnan(ar):
                continue
            g1_checks = [sr <= 0.7, vcr <= 0.8, ar <= 0.7]
            if sum(g1_checks) < 2:
                continue

            # Gate 2: participation (pass N of 3)
            vg = feat.vol_growth[i]
            abg = feat.active_bar_growth[i]
            wr = feat.whale_ratio[i]
            if np.isnan(vg) or np.isnan(abg):
                continue
            g2_checks = [
                vg >= cfg.participation_growth,
                abg >= 1.3,
                wr < 10.0,
            ]
            if sum(g2_checks) < cfg.gate2_pass_of_3:
                continue

            # Gate 3: compression (pass N of 3)
            rp = feat.range_pct[i]
            ac = feat.atr_contraction[i]
            vs = feat.vol_slope[i]
            rv = feat.rel_volume[i]
            if np.isnan(rp) or np.isnan(ac):
                continue
            g3_checks = [
                rp <= cfg.range_pct_threshold,
                ac <= 0.7,
                vs >= 0 and rv < 3.0,  # not igniting yet
            ]
            if sum(g3_checks) < cfg.gate3_pass_of_3:
                continue

            # Ignition check
            score = feat.ignition_score[i]
            vz = feat.vol_z[i]
            if score < cfg.ignition_score_threshold:
                continue
            if vz < cfg.vol_z_min:
                continue

            # Relative volume ignition check
            if rv < cfg.rel_vol_ignition:
                continue

            # Acceptance: N consecutive closes above compression box high
            # Compression box high = rolling max high over prior 48 bars (excluding current)
            if i < cfg.acceptance_bars + 1:
                continue
            box_high = np.max(feat.high[max(0, i - 49):i - 1]) if i > 1 else feat.high[0]
            accepted = True
            for j in range(cfg.acceptance_bars):
                if feat.close[i - j] <= box_high:
                    accepted = False
                    break
            # Prior bar was inside
            if accepted and feat.close[i - cfg.acceptance_bars] > box_high:
                accepted = False
            if not accepted:
                continue

            # Confirmations (simplified: vol rising + activity + trend accel)
            if i >= 96:
                last48 = slice(i - 48, i)
                prev48 = slice(i - 96, i - 48)
                c_vol_rising = np.sum(feat.volume[last48]) > np.sum(feat.volume[prev48])
                c_active = np.sum(feat.volume[last48] > 0) >= np.sum(feat.volume[prev48] > 0)
                c_slopes = feat.close[last48]
                half = 24
                if len(c_slopes) >= 48:
                    slope_full = np.polyfit(np.arange(48), feat.close[i-48:i], 1)[0]
                    slope_first = np.polyfit(np.arange(half), feat.close[i-48:i-24], 1)[0]
                    slope_second = np.polyfit(np.arange(half), feat.close[i-24:i], 1)[0]
                    c_trend = slope_full > 0 and slope_second > slope_first
                else:
                    c_trend = False
                confirmations = sum([c_vol_rising, c_active, c_trend])
            else:
                confirmations = 0

            if confirmations < cfg.confirmations_required:
                continue

            # Signal passes!
            atr_val = feat.atr_20[i] if not np.isnan(feat.atr_20[i]) else 0
            signals.append({
                "symbol": sym,
                "bar_idx": i,
                "ts_ms": int(feat.ts_ms[i]),
                "entry_price": float(feat.close[i]),  # next bar open approx
                "atr_20": float(atr_val),
                "score": float(score),
                "vol_z": float(vz),
            })

    return signals


# =============================================================================
# Trade Simulation
# =============================================================================

def simulate_trades(
    signals: list[dict],
    features_dict: dict[str, SymbolFeatures],
    slippage_mult: float = 1.0,
) -> list[dict]:
    """
    Simulate trades for a list of signals using exit_checker logic.
    Returns list of trade results.
    """
    slippage = SLIPPAGE_BPS * slippage_mult / 10000
    fee = FEE_BPS / 10000
    one_way_cost = slippage + fee

    trades = []
    for sig in signals:
        sym = sig["symbol"]
        feat = features_dict.get(sym)
        if feat is None:
            continue

        bar_idx = sig["bar_idx"]
        n = len(feat.close)

        # Entry at next bar open
        entry_idx = bar_idx + 1
        if entry_idx >= n:
            continue
        entry_price = float(feat.open_[entry_idx])
        if entry_price <= 0:
            continue

        # Stop: 4x ATR (wide)
        atr = sig["atr_20"]
        if atr <= 0:
            atr = entry_price * 0.02  # fallback 2%
        stop_distance = EXIT_CONFIG["stop_atr_mult"] * atr
        stop_price = entry_price - stop_distance
        stop_price = max(stop_price, entry_price * 0.80)  # Floor: 20% max loss

        r_value = entry_price - stop_price
        if r_value <= 0:
            continue

        tp1_price = entry_price + EXIT_CONFIG["tp1_r"] * r_value
        tp2_price = entry_price + EXIT_CONFIG["tp2_r"] * r_value
        max_bars = EXIT_CONFIG["max_bars"]

        # Walk forward through bars
        hwm = None
        half_r_touched = False
        exit_reason = None
        exit_price = entry_price
        exit_idx = entry_idx

        for j in range(entry_idx, min(entry_idx + max_bars + 5, n)):
            bars_held = j - entry_idx
            bar_h = float(feat.high[j])
            bar_l = float(feat.low[j])
            bar_c = float(feat.close[j])
            bar_o = float(feat.open_[j])

            # Track HWM
            if hwm is None or bar_h > hwm:
                hwm = bar_h

            # STOP_LOSS
            if bar_l <= stop_price:
                exit_reason = "STOP_LOSS"
                exit_price = stop_price if bar_o >= stop_price else max(bar_o, stop_price - 2 * r_value)
                exit_idx = j
                break

            # FAIL_FAST
            if bars_held <= EXIT_CONFIG["fail_fast_bars"]:
                if bar_c < entry_price * (1 - EXIT_CONFIG["fail_fast_threshold"]):
                    exit_reason = "FAIL_FAST"
                    exit_price = bar_c
                    exit_idx = j
                    break

            # Progress check
            if not half_r_touched and r_value > 0:
                if bar_h >= entry_price + 0.5 * r_value:
                    half_r_touched = True

            # TIME_STOP
            if bars_held > max_bars and not half_r_touched:
                exit_reason = "TIME_STOP"
                exit_price = bar_o
                exit_idx = j
                break

            # TRAIL_STOP
            if r_value > 0 and hwm is not None:
                if hwm >= entry_price + EXIT_CONFIG["trail_activation_r"] * r_value:
                    trail_stop = hwm - EXIT_CONFIG["trail_distance_r"] * r_value
                    if bar_l <= trail_stop:
                        # Check if TP1 also hit
                        if bar_h >= tp1_price:
                            pass  # let TP1 fire
                        else:
                            exit_reason = "TRAIL_STOP"
                            exit_price = trail_stop if bar_o >= trail_stop else bar_o
                            exit_idx = j
                            break
                elif EXIT_CONFIG["trail_breakeven_r"] > 0 and hwm >= entry_price + EXIT_CONFIG["trail_breakeven_r"] * r_value:
                    if bar_l <= entry_price:
                        if bar_h >= tp1_price:
                            pass
                        else:
                            exit_reason = "TRAIL_STOP"
                            exit_price = entry_price if bar_o >= entry_price else bar_o
                            exit_idx = j
                            break

            # TP1
            if bar_h >= tp1_price:
                exit_reason = "TP1"
                exit_price = tp1_price
                exit_idx = j
                break

            # TP2
            if bar_h >= tp2_price:
                exit_reason = "TP2"
                exit_price = tp2_price
                exit_idx = j
                break

        if exit_reason is None:
            # Forced exit at end of data
            exit_reason = "DATA_END"
            exit_price = float(feat.close[min(entry_idx + max_bars, n - 1)])
            exit_idx = min(entry_idx + max_bars, n - 1)

        # PnL
        gross_pnl_pct = (exit_price - entry_price) / entry_price
        cost_pct = 2 * one_way_cost  # round trip
        net_pnl_pct = gross_pnl_pct - cost_pct

        trades.append({
            "symbol": sym,
            "entry_ts": int(feat.ts_ms[entry_idx]),
            "exit_ts": int(feat.ts_ms[exit_idx]),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "bars_held": exit_idx - entry_idx,
            "gross_pnl_pct": gross_pnl_pct,
            "net_pnl_pct": net_pnl_pct,
            "cost_pct": cost_pct,
            "score": sig["score"],
        })

    return trades


# =============================================================================
# Walk-Forward Validation
# =============================================================================

def compute_daily_pf(trades: list[dict]) -> tuple[float, int, int]:
    """
    Compute average daily Profit Factor.
    Returns (avg_daily_pf, n_profitable_days, n_total_days).
    """
    if not trades:
        return 0.0, 0, 0

    # Group trades by day (entry_ts)
    day_pnl = defaultdict(lambda: {"wins": 0.0, "losses": 0.0})
    for t in trades:
        day = t["entry_ts"] // (86400 * 1000)  # day bucket
        pnl = t["net_pnl_pct"]
        if pnl > 0:
            day_pnl[day]["wins"] += pnl
        elif pnl < 0:
            day_pnl[day]["losses"] += abs(pnl)

    daily_pfs = []
    for day, pnl in day_pnl.items():
        if pnl["losses"] > 0:
            daily_pfs.append(pnl["wins"] / pnl["losses"])
        elif pnl["wins"] > 0:
            daily_pfs.append(10.0)  # cap at 10 for days with only wins
        # Skip days with no trades

    if not daily_pfs:
        return 0.0, 0, 0

    avg_pf = float(np.mean(daily_pfs))
    n_profitable = sum(1 for pf in daily_pfs if pf > 1.0)
    return avg_pf, n_profitable, len(daily_pfs)


def walk_forward_validate(
    cfg: SweepConfig,
    features_list: list[SymbolFeatures],
    features_dict: dict[str, SymbolFeatures],
    social_symbols: set[str],
    all_timestamps: np.ndarray,
    n_folds: int = 5,
    embargo_bars: int = 24,
    holdout_pct: float = 0.20,
) -> dict:
    """
    5-fold purged rolling walk-forward validation.
    Returns validation results dict.
    """
    # Determine evaluation window (exclude warmup)
    warmup_bars = 96
    eval_ts = all_timestamps[warmup_bars:]
    n_eval = len(eval_ts)

    if n_eval < 100:
        return {"valid": False, "reason": "insufficient_eval_bars"}

    # Reserve holdout
    holdout_start = int(n_eval * (1 - holdout_pct))
    train_test_ts = eval_ts[:holdout_start]
    holdout_ts = eval_ts[holdout_start:]

    n_tt = len(train_test_ts)
    fold_size = n_tt // n_folds

    fold_results = []
    for fold in range(n_folds):
        test_start = fold * fold_size
        test_end = min(test_start + fold_size, n_tt)

        # Convert to actual timestamp boundaries
        test_ts_start = train_test_ts[test_start]
        test_ts_end = train_test_ts[min(test_end, n_tt - 1)]

        # Generate signals for this config on the full period
        # Then filter to test window
        signals = evaluate_config(cfg, features_list, social_symbols, warmup_bars)

        # Filter signals to test window (by entry ts)
        test_signals = [s for s in signals if test_ts_start <= s["ts_ms"] <= test_ts_end]

        if len(test_signals) < 3:
            fold_results.append({
                "fold": fold, "n_signals": len(test_signals),
                "avg_daily_pf": 0.0, "profitable_days": 0, "total_days": 0,
            })
            continue

        # Simulate trades
        trades = simulate_trades(test_signals, features_dict)
        avg_pf, n_prof, n_days = compute_daily_pf(trades)

        fold_results.append({
            "fold": fold,
            "n_signals": len(test_signals),
            "n_trades": len(trades),
            "avg_daily_pf": avg_pf,
            "profitable_days": n_prof,
            "total_days": n_days,
            "win_rate": sum(1 for t in trades if t["net_pnl_pct"] > 0) / max(len(trades), 1),
            "mean_pnl": float(np.mean([t["net_pnl_pct"] for t in trades])) if trades else 0,
        })

    # Fold pass rate
    passing_folds = sum(1 for f in fold_results if f["avg_daily_pf"] > 1.0)
    passes_wf = passing_folds >= 4  # 4/5 folds

    return {
        "valid": True,
        "n_folds": n_folds,
        "fold_results": fold_results,
        "passing_folds": passing_folds,
        "passes_walk_forward": passes_wf,
    }


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun, max error 1.5e-7)."""
    # No scipy dependency needed
    if x < -8:
        return 0.0
    if x > 8:
        return 1.0
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.3989422804014327  # 1/sqrt(2*pi)
    p = d * np.exp(-x * x / 2.0) * (
        t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    )
    return float(1.0 - p) if x > 0 else float(p)


def compute_dsr(observed_sr: float, n_trials: int, sr_std: float = 1.0) -> float:
    """
    Deflated Sharpe Ratio (Harvey et al. 2016).
    Returns DSR (probability that observed SR is not due to overfitting).
    Pure numpy implementation — no scipy required.
    """
    if n_trials <= 1 or sr_std <= 0:
        return 0.0

    # E[max(SR)] ≈ sqrt(2 * ln(n_trials)) for standard normal
    e_max_sr = np.sqrt(2 * np.log(n_trials))

    # DSR = Prob(SR > E[max(SR)])
    z = (observed_sr - e_max_sr) / sr_std
    return _norm_cdf(z)


# =============================================================================
# Main Orchestrator
# =============================================================================

def run_sweep(days: int):
    t0 = time.time()

    # Phase 0: Load data
    ohlcv_dict, symbols, timestamps = load_ohlcv_1h(days + 30)  # warmup buffer
    social_symbols = load_social_symbols(30)
    logger.info("Social silence filter: %d symbols with mentions (will be excluded)", len(social_symbols))

    # Phase 1: Precompute features (parallel)
    logger.info("Precomputing features for %d symbols...", len(symbols))
    t_feat = time.time()

    n_workers = min(12, cpu_count())
    work_items = [(ohlcv_dict[sym], sym) for sym in symbols]

    with Pool(n_workers) as pool:
        results = pool.map(_precompute_worker, work_items)

    features_list = [r for r in results if r is not None]
    features_dict = {f.symbol: f for f in features_list}
    logger.info("Precomputed features for %d symbols in %.1fs", len(features_list), time.time() - t_feat)

    # Phase 2: Generate configs
    configs = generate_configs()

    # Phase 3: Evaluate all configs (signals only — fast since features precomputed)
    logger.info("Evaluating %d configs...", len(configs))
    t_eval = time.time()

    warmup_bars = 96
    config_results = []

    for idx, cfg in enumerate(configs):
        signals = evaluate_config(cfg, features_list, social_symbols, warmup_bars)

        config_results.append({
            "config_id": cfg.config_id,
            "config": {
                "max_volume_usd": cfg.max_volume_usd,
                "exclude_majors": cfg.exclude_majors,
                "gate2_pass_of_3": cfg.gate2_pass_of_3,
                "gate3_pass_of_3": cfg.gate3_pass_of_3,
                "participation_growth": cfg.participation_growth,
                "range_pct_threshold": cfg.range_pct_threshold,
                "ignition_score_threshold": cfg.ignition_score_threshold,
                "vol_z_min": cfg.vol_z_min,
                "rel_vol_ignition": cfg.rel_vol_ignition,
                "acceptance_bars": cfg.acceptance_bars,
                "confirmations_required": cfg.confirmations_required,
            },
            "n_signals": len(signals),
            "signals": signals,
        })

        if (idx + 1) % 50 == 0:
            elapsed = time.time() - t_eval
            logger.info("  [%d/%d] configs evaluated, %.1fs elapsed", idx + 1, len(configs), elapsed)

    logger.info("Config evaluation done in %.1fs", time.time() - t_eval)

    # Filter configs with enough signals (>=25)
    viable = [r for r in config_results if r["n_signals"] >= 25]
    logger.info("Viable configs (>=25 signals): %d / %d", len(viable), len(configs))

    # Signal count distribution
    sig_counts = [r["n_signals"] for r in config_results]
    logger.info("Signal counts: min=%d, median=%d, max=%d, mean=%.0f",
                min(sig_counts) if sig_counts else 0,
                int(np.median(sig_counts)) if sig_counts else 0,
                max(sig_counts) if sig_counts else 0,
                np.mean(sig_counts) if sig_counts else 0)

    # Phase 4: Simulate + Walk-Forward for viable configs
    logger.info("Simulating trades + walk-forward for %d viable configs...", len(viable))
    t_sim = time.time()

    full_results = []
    for idx, cr in enumerate(viable):
        cfg_dict = cr["config"]
        cfg = SweepConfig(
            config_id=cr["config_id"],
            **cfg_dict,
        )
        signals = cr["signals"]

        # Simulate at base cost
        trades = simulate_trades(signals, features_dict, slippage_mult=1.0)
        avg_pf, n_prof_days, n_days = compute_daily_pf(trades)

        # Simulate at 1.5x slippage
        trades_15x = simulate_trades(signals, features_dict, slippage_mult=SLIPPAGE_MULT_TEST)
        avg_pf_15x, _, _ = compute_daily_pf(trades_15x)

        # Walk-forward validation
        wf = walk_forward_validate(cfg, features_list, features_dict, social_symbols, timestamps)

        # Compute Sharpe-like metric for DSR
        if trades:
            daily_returns = defaultdict(float)
            for t in trades:
                day = t["entry_ts"] // (86400 * 1000)
                daily_returns[day] += t["net_pnl_pct"]
            rets = list(daily_returns.values())
            sr = float(np.mean(rets) / np.std(rets)) if len(rets) > 1 and np.std(rets) > 0 else 0
        else:
            sr = 0

        result = {
            "config_id": cr["config_id"],
            "config": cfg_dict,
            "n_signals": len(signals),
            "n_trades": len(trades),
            "avg_daily_pf": avg_pf,
            "avg_daily_pf_15x_slip": avg_pf_15x,
            "profitable_days": n_prof_days,
            "total_trading_days": n_days,
            "win_rate": sum(1 for t in trades if t["net_pnl_pct"] > 0) / max(len(trades), 1),
            "mean_net_pnl_pct": float(np.mean([t["net_pnl_pct"] for t in trades])) if trades else 0,
            "sharpe_ratio": sr,
            "walk_forward": wf,
            "exit_reasons": {},
        }

        # Exit reason distribution
        if trades:
            reasons = defaultdict(int)
            for t in trades:
                reasons[t["exit_reason"]] += 1
            result["exit_reasons"] = dict(reasons)

        full_results.append(result)

        if (idx + 1) % 20 == 0:
            logger.info("  [%d/%d] sim+WF done, %.1fs elapsed", idx + 1, len(viable), time.time() - t_sim)

    logger.info("Simulation + WF done in %.1fs", time.time() - t_sim)

    # Phase 5: Rank results and compute DSR for top configs
    full_results.sort(key=lambda x: x["avg_daily_pf"], reverse=True)

    # DSR for top configs that pass WF
    n_total_configs = len(configs)
    wf_passing = [r for r in full_results if r["walk_forward"].get("passes_walk_forward", False)]
    logger.info("Configs passing walk-forward (4/5 folds, daily PF > 1.0): %d", len(wf_passing))

    for r in wf_passing:
        r["dsr"] = compute_dsr(r["sharpe_ratio"], n_total_configs)

    # Holdout evaluation for the single best WF-passing config
    holdout_result = None
    if wf_passing:
        best = wf_passing[0]
        logger.info("Best WF-passing config: id=%d, avg_daily_pf=%.3f, n_signals=%d",
                     best["config_id"], best["avg_daily_pf"], best["n_signals"])

        # Run holdout: last 20% of timestamps
        holdout_start_ts = timestamps[int(len(timestamps) * 0.80)]
        cfg = SweepConfig(config_id=best["config_id"], **best["config"])
        all_signals = evaluate_config(cfg, features_list, social_symbols, 96)
        holdout_signals = [s for s in all_signals if s["ts_ms"] >= holdout_start_ts]
        holdout_trades = simulate_trades(holdout_signals, features_dict)
        ho_pf, ho_prof, ho_days = compute_daily_pf(holdout_trades)

        holdout_result = {
            "config_id": best["config_id"],
            "n_signals": len(holdout_signals),
            "n_trades": len(holdout_trades),
            "avg_daily_pf": ho_pf,
            "profitable_days": ho_prof,
            "total_days": ho_days,
            "win_rate": sum(1 for t in holdout_trades if t["net_pnl_pct"] > 0) / max(len(holdout_trades), 1),
            "mean_pnl": float(np.mean([t["net_pnl_pct"] for t in holdout_trades])) if holdout_trades else 0,
        }
        logger.info("Holdout: avg_daily_pf=%.3f, %d trades, %d/%d profitable days",
                     ho_pf, len(holdout_trades), ho_prof, ho_days)

    # Save results
    elapsed = time.time() - t0
    output = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "days": days,
            "timeframe": "1h",
            "n_symbols": len(symbols),
            "n_features_computed": len(features_list),
            "n_configs_total": len(configs),
            "n_viable_configs": len(viable),
            "n_wf_passing": len(wf_passing),
            "total_runtime_seconds": elapsed,
            "excluded_symbols": sorted(EXCLUDED_SYMBOLS),
            "social_excluded_count": len(social_symbols),
            "exit_config": EXIT_CONFIG,
            "cost_model": {
                "fee_bps": FEE_BPS,
                "slippage_bps": SLIPPAGE_BPS,
                "slippage_test_mult": SLIPPAGE_MULT_TEST,
            },
        },
        "top_10_by_daily_pf": [
            {k: v for k, v in r.items() if k != "walk_forward"}
            for r in full_results[:10]
        ],
        "wf_passing_configs": [
            {k: v for k, v in r.items()}
            for r in wf_passing[:20]
        ],
        "holdout_result": holdout_result,
        "signal_count_distribution": {
            "min": int(min(sig_counts)) if sig_counts else 0,
            "p25": int(np.percentile(sig_counts, 25)) if sig_counts else 0,
            "median": int(np.median(sig_counts)) if sig_counts else 0,
            "p75": int(np.percentile(sig_counts, 75)) if sig_counts else 0,
            "max": int(max(sig_counts)) if sig_counts else 0,
        },
    }

    out_dir = "/scripts" if os.path.isdir("/scripts") else os.path.dirname(__file__)
    out_path = os.path.join(out_dir, "u2_research_results.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info("Results saved to %s", out_path)

    # Print summary
    print("\n" + "=" * 70)
    print("U2 RESEARCH SWEEP — Results Summary")
    print("=" * 70)
    print(f"\nConfigs tested: {len(configs)}")
    print(f"Viable (>=25 signals): {len(viable)}")
    print(f"Walk-forward passing (4/5 folds): {len(wf_passing)}")
    print(f"\nTop 5 by avg daily PF:")
    for r in full_results[:5]:
        wf_status = "PASS" if r["walk_forward"].get("passes_walk_forward") else "FAIL"
        print(f"  config={r['config_id']:>4d}: PF={r['avg_daily_pf']:.3f} "
              f"PF@1.5x={r['avg_daily_pf_15x_slip']:.3f} "
              f"signals={r['n_signals']:>4d} trades={r['n_trades']:>4d} "
              f"WR={r['win_rate']:.1%} WF={wf_status}")

    if wf_passing:
        print(f"\nBest WF-passing config:")
        b = wf_passing[0]
        print(f"  Config: {json.dumps(b['config'], indent=4)}")
        print(f"  Avg daily PF: {b['avg_daily_pf']:.3f}")
        print(f"  PF @ 1.5x slippage: {b['avg_daily_pf_15x_slip']:.3f}")
        print(f"  Signals: {b['n_signals']}, Trades: {b['n_trades']}")
        if b.get("dsr") is not None:
            print(f"  DSR: {b['dsr']:.4f} (>{0} required)")

    if holdout_result:
        print(f"\nHoldout (final 20%):")
        print(f"  Avg daily PF: {holdout_result['avg_daily_pf']:.3f}")
        print(f"  Trades: {holdout_result['n_trades']}")
        print(f"  Win rate: {holdout_result['win_rate']:.1%}")

    print(f"\nRuntime: {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="U2 Research Sweep")
    parser.add_argument("--days", type=int, default=120, help="Days of data (default: 120)")
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    run_sweep(args.days)


if __name__ == "__main__":
    main()
