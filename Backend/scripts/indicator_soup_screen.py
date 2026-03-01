#!/usr/bin/env python3
"""
Indicator Soup Screen — Phase 2 Cross-Sectional Screening
==========================================================

Loads OHLCV, computes all 586 indicator variants via iter_all_variants(),
and produces univariate stats (FM IC, quantile spread, monotonicity, regime IC,
stability) for every variant × horizon × tier × return_type combination.

Output feeds Phase 3 (combinations) and Phase 2b (reflexivity).

Usage:
    tmux new -s soup-screen
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/indicator_soup_screen.py --days 270 --timeframes 1h,4h,15m,5m

    # Resume from checkpoint after crash:
    ... --timeframes 1h --resume
"""

# ─── S0: Imports, Constants, Config ─────────────────────────────────────────

import gc
import os
import sys
import time
import json
import math
import resource
import argparse
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

# Execution context detection
if os.path.isdir('/app') and os.path.isfile('/app/worker.py'):
    SCRIPTS_DIR = '/scripts'
    sys.path.insert(0, '/scripts')
else:
    SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, SCRIPTS_DIR)

# .env loading
env_path = os.path.join(SCRIPTS_DIR, '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

# BLAS thread control — set BEFORE numpy import
os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')
os.environ.setdefault('MKL_NUM_THREADS', '4')

import warnings
warnings.filterwarnings('ignore', category=RuntimeWarning)

import numpy as np
import psycopg
from scipy import stats as sp_stats

from indicator_soup import iter_all_variants, total_variant_count
from indicator_soup.rolling_utils import rolling_mean, rolling_std

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("soup_screen")

# ─── Constants ──────────────────────────────────────────────────────────────

TF_BAR_MINUTES = {'5m': 5, '15m': 15, '1h': 60, '4h': 240}
HORIZON_HOURS = [0.5, 1, 2, 4, 6, 12, 24, 72]
SLIPPAGE_BPS = {'5m': 15, '15m': 10, '1h': 5, '4h': 3}
FEE_BPS = 4
WARMUP_BARS = 200
MIN_BARS_PER_SYMBOL = 200
BTC_SYMBOL = "BTCUSDT"
MIN_CS_SYMBOLS = 30        # Minimum valid symbols per cross-section
N_EFF_SUBSAMPLE_BARS = 1000
N_EFF_SUBSAMPLE_SYMS = 200
CHECKPOINT_INTERVAL = 50   # Variants between checkpoints
PROGRESS_INTERVAL = 10     # Variants between progress logs
MEMMAP_THRESHOLD_GB = 8.0  # Total OHLCV budget; switch to memmap above this


def _peak_rss_mb():
    """Current peak RSS in MB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _horizon_to_bars(horizon_hours, bar_minutes):
    """Convert horizon in hours to bars, minimum 1."""
    return max(1, round(horizon_hours * 60 / bar_minutes))


def _horizons_for_tf(bar_minutes):
    """Return list of (horizon_hours, n_bars) pairs valid for this timeframe.

    Deduplicates by n_bars — if multiple horizons map to the same bar count,
    keeps only the first (shortest horizon label).
    """
    out = []
    seen_bars = set()
    for h in HORIZON_HOURS:
        n_bars = _horizon_to_bars(h, bar_minutes)
        if n_bars >= 1 and n_bars not in seen_bars:
            out.append((h, n_bars))
            seen_bars.add(n_bars)
    return out


def _allocate_matrix(shape, cache_path=None, name=None, use_memmap=False):
    """
    Allocate a (n_bars, n_symbols) float64 matrix.

    If use_memmap: creates a memory-mapped file at cache_path/name.mmap.
    Otherwise: returns a standard np.ndarray.
    Both are initialized to NaN.
    """
    if use_memmap and cache_path and name:
        mmap_path = Path(cache_path) / f"{name}.mmap"
        arr = np.memmap(mmap_path, dtype=np.float64, mode='w+', shape=shape)
        arr[:] = np.nan
        return arr
    return np.full(shape, np.nan, dtype=np.float64)


# ─── S1: OHLCV Loading ─────────────────────────────────────────────────────

def load_ohlcv_matrices(database_url, timeframe, target_days, cache_dir=None):
    """
    Load OHLCV and build aligned (n_bars, n_symbols) matrices.

    If cache_dir is provided, saves/loads .npy files for crash recovery.
    Returns (open_, high, low, close, volume, ts_grid, symbols, btc_idx).
    """
    bar_minutes = TF_BAR_MINUTES[timeframe]
    warmup_days = math.ceil(WARMUP_BARS * bar_minutes / 1440) + 1
    total_days = target_days + warmup_days

    # Check cache — memmap first (large TFs), then .npy (small TFs)
    if cache_dir:
        cache_path = Path(cache_dir) / timeframe

        # Check memmap cache (large timeframes like 5m)
        mmap_meta_path = cache_path / "mmap" / "meta.json"
        if mmap_meta_path.exists():
            with open(mmap_meta_path) as f:
                meta = json.load(f)
            if meta.get("target_days") == target_days and meta.get("timeframe") == timeframe:
                logger.info("Loading %s OHLCV from memmap cache: %s", timeframe, cache_path / "mmap")
                t0 = time.time()
                shape = tuple(meta["shape"])
                mmap_dir = cache_path / "mmap"
                open_ = np.memmap(mmap_dir / "open.mmap", dtype=np.float64, mode='r', shape=shape)
                high = np.memmap(mmap_dir / "high.mmap", dtype=np.float64, mode='r', shape=shape)
                low = np.memmap(mmap_dir / "low.mmap", dtype=np.float64, mode='r', shape=shape)
                close = np.memmap(mmap_dir / "close.mmap", dtype=np.float64, mode='r', shape=shape)
                vol = np.memmap(mmap_dir / "volume.mmap", dtype=np.float64, mode='r', shape=shape)
                ts_grid = np.load(mmap_dir / "ts_grid.npy")
                symbols = json.loads((mmap_dir / "symbols.json").read_text())
                btc_idx = meta.get("btc_idx")
                logger.info("  Memmap cache loaded in %.1fs: %s, BTC idx=%s",
                            time.time() - t0, shape, btc_idx)
                return open_, high, low, close, vol, ts_grid, symbols, btc_idx

        # Check .npy cache (in-memory timeframes)
        cache_meta = cache_path / "meta.json"
        if cache_meta.exists():
            with open(cache_meta) as f:
                meta = json.load(f)
            if meta.get("target_days") == target_days and meta.get("timeframe") == timeframe:
                logger.info("Loading %s OHLCV from .npy cache: %s", timeframe, cache_path)
                t0 = time.time()
                open_ = np.load(cache_path / "open.npy")
                high = np.load(cache_path / "high.npy")
                low = np.load(cache_path / "low.npy")
                close = np.load(cache_path / "close.npy")
                vol = np.load(cache_path / "volume.npy")
                ts_grid = np.load(cache_path / "ts_grid.npy")
                symbols = json.loads((cache_path / "symbols.json").read_text())
                btc_idx = meta.get("btc_idx")
                logger.info("  Cache loaded in %.1fs: (%d, %d), BTC idx=%s",
                            time.time() - t0, close.shape[0], close.shape[1], btc_idx)
                return open_, high, low, close, vol, ts_grid, symbols, btc_idx

    start_ts = datetime.now(timezone.utc) - timedelta(days=total_days)
    end_ts = datetime.now(timezone.utc)

    logger.info("Loading %s OHLCV from DB: %s to %s (%d+%d days)",
                timeframe, start_ts.date(), end_ts.date(), target_days, warmup_days)
    t0 = time.time()

    use_server_cursor = timeframe in ('5m', '15m')

    with psycopg.connect(database_url) as conn:
        if use_server_cursor:
            cur = conn.cursor(name="ohlcv_stream")
            cur.itersize = 50_000
        else:
            cur = conn.cursor()

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
        """, (timeframe, start_ts, end_ts))

        sym_rows = defaultdict(list)
        row_count = 0
        for row in cur:
            sym_rows[row[0]].append(row)
            row_count += 1
            if row_count % 1_000_000 == 0:
                logger.info("  ... loaded %dM rows (%.0fs)", row_count // 1_000_000, time.time() - t0)

        if use_server_cursor:
            cur.close()

    logger.info("  Fetched %d rows for %d symbols in %.1fs",
                row_count, len(sym_rows), time.time() - t0)

    # Build timestamp grid
    all_ts = set()
    for rows in sym_rows.values():
        for r in rows:
            ts = r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None else r[1].timestamp()
            all_ts.add(ts)
    ts_grid = np.array(sorted(all_ts))
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)

    # Filter symbols with sufficient bars
    valid_symbols = []
    for sym, rows in sorted(sym_rows.items()):
        if len(rows) >= MIN_BARS_PER_SYMBOL:
            valid_symbols.append(sym)

    n_symbols = len(valid_symbols)
    logger.info("  %d symbols with >= %d bars, %d total bars in grid",
                n_symbols, MIN_BARS_PER_SYMBOL, n_bars)

    # Decide memmap vs in-memory based on estimated OHLCV size
    estimated_ohlcv_gb = 5 * n_bars * n_symbols * 8 / 1e9
    use_memmap = estimated_ohlcv_gb > MEMMAP_THRESHOLD_GB
    memmap_dir = None
    if use_memmap:
        logger.info("  Using memory-mapped arrays: %.1f GB estimated OHLCV", estimated_ohlcv_gb)
        memmap_dir = Path(cache_dir) / timeframe / "mmap" if cache_dir else Path(SCRIPTS_DIR) / "output" / "indicator_soup_cache" / timeframe / "mmap"
        memmap_dir.mkdir(parents=True, exist_ok=True)
    else:
        logger.info("  Using in-memory arrays: %.1f GB estimated OHLCV", estimated_ohlcv_gb)

    # Allocate matrices
    shape = (n_bars, n_symbols)
    open_ = _allocate_matrix(shape, memmap_dir, "open", use_memmap)
    high_m = _allocate_matrix(shape, memmap_dir, "high", use_memmap)
    low_m = _allocate_matrix(shape, memmap_dir, "low", use_memmap)
    close = _allocate_matrix(shape, memmap_dir, "close", use_memmap)
    volume = _allocate_matrix(shape, memmap_dir, "volume", use_memmap)

    btc_idx = None
    for s_idx, sym in enumerate(valid_symbols):
        if sym == BTC_SYMBOL:
            btc_idx = s_idx
        for r in sym_rows[sym]:
            ts = r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None else r[1].timestamp()
            t_idx = ts_to_idx[ts]
            open_[t_idx, s_idx] = float(r[2])
            high_m[t_idx, s_idx] = float(r[3])
            low_m[t_idx, s_idx] = float(r[4])
            close[t_idx, s_idx] = float(r[5])
            volume[t_idx, s_idx] = float(r[6])

    del sym_rows  # Free raw data

    # Forward-fill NaN gaps
    for s_idx in range(n_symbols):
        for arr in [close, open_, high_m, low_m, volume]:
            col = arr[:, s_idx]
            mask = np.isnan(col)
            if mask.any() and not mask.all():
                idx = np.where(~mask, np.arange(n_bars), 0)
                np.maximum.accumulate(idx, out=idx)
                arr[:, s_idx] = col[idx]

    mem_mb = (close.nbytes + open_.nbytes + high_m.nbytes + low_m.nbytes + volume.nbytes) / 1024**2
    logger.info("  Matrices: (%d, %d), %.1f MB, BTC idx=%s, peak RSS=%.0f MB",
                n_bars, n_symbols, mem_mb, btc_idx, _peak_rss_mb())

    # Save cache
    if cache_dir:
        if use_memmap:
            # Memmap files already written in place; save metadata + ts_grid + symbols
            for arr in [open_, high_m, low_m, close, volume]:
                if hasattr(arr, 'flush'):
                    arr.flush()
            np.save(memmap_dir / "ts_grid.npy", ts_grid)
            (memmap_dir / "symbols.json").write_text(json.dumps(valid_symbols))
            mmap_meta = {"target_days": target_days, "timeframe": timeframe,
                         "btc_idx": btc_idx, "shape": list(shape)}
            with open(memmap_dir / "meta.json", 'w') as f:
                json.dump(mmap_meta, f)
            logger.info("  Memmap cached to %s", memmap_dir)
        else:
            cache_path = Path(cache_dir) / timeframe
            cache_path.mkdir(parents=True, exist_ok=True)
            np.save(cache_path / "open.npy", open_)
            np.save(cache_path / "high.npy", high_m)
            np.save(cache_path / "low.npy", low_m)
            np.save(cache_path / "close.npy", close)
            np.save(cache_path / "volume.npy", volume)
            np.save(cache_path / "ts_grid.npy", ts_grid)
            (cache_path / "symbols.json").write_text(json.dumps(valid_symbols))
            cache_meta_path = cache_path / "meta.json"
            with open(cache_meta_path, 'w') as f:
                json.dump({"target_days": target_days, "timeframe": timeframe, "btc_idx": btc_idx}, f)
            logger.info("  Cached to %s", cache_path)

    return open_, high_m, low_m, close, volume, ts_grid, valid_symbols, btc_idx


# ─── S2: Forward Returns ───────────────────────────────────────────────────

def compute_forward_returns_raw(close, timeframe, cache_dir=None):
    """
    Compute raw forward returns for all horizons applicable to this TF.

    Uses memmap for forward returns when estimated size exceeds 4 GB.
    Returns dict[n_bars_horizon, (n_bars, n_symbols) float64 array].
    """
    bar_minutes = TF_BAR_MINUTES[timeframe]
    horizons = _horizons_for_tf(bar_minutes)

    # Decide memmap for forward returns
    estimated_fwd_gb = len(horizons) * close.shape[0] * close.shape[1] * 8 / 1e9
    use_memmap = estimated_fwd_gb > 4.0
    memmap_dir = None
    if use_memmap and cache_dir:
        memmap_dir = Path(cache_dir) / timeframe / "mmap"
        memmap_dir.mkdir(parents=True, exist_ok=True)
        logger.info("  Forward returns using memmap: %.1f GB estimated", estimated_fwd_gb)

    fwd_ret = {}
    for h_hours, n_bars_h in horizons:
        n_bars_total = close.shape[0]
        if n_bars_h >= n_bars_total:
            logger.warning("  Horizon %.1fh (%d bars) >= data length %d, skipping",
                           h_hours, n_bars_h, n_bars_total)
            continue

        if use_memmap and memmap_dir:
            mmap_path = memmap_dir / f"fwd_ret_{n_bars_h}.mmap"
            ret = np.memmap(mmap_path, dtype=np.float64, mode='w+', shape=close.shape)
            ret[:] = np.nan
        else:
            ret = np.full_like(close, np.nan)

        ret[:-n_bars_h] = close[n_bars_h:] / close[:-n_bars_h] - 1.0
        fwd_ret[n_bars_h] = ret

    logger.info("  Forward returns: %d horizons for %s%s",
                len(fwd_ret), timeframe, " (memmap)" if use_memmap else "")
    return fwd_ret


def derive_cost_adjusted(raw_ret, timeframe, tier_spread_proxy):
    """Subtract round-trip costs from raw return."""
    slippage_bps = max(SLIPPAGE_BPS[timeframe], tier_spread_proxy)
    cost = (slippage_bps + FEE_BPS) * 2 / 10_000  # Round-trip
    return raw_ret - cost


def derive_market_excess(raw_ret):
    """Subtract cross-sectional median return (market excess)."""
    median_ret = np.nanmedian(raw_ret, axis=1, keepdims=True)
    return raw_ret - median_ret


# ─── S3: Universe Stratification ───────────────────────────────────────────

def stratify_universe(close, volume, symbols):
    """
    Split symbols into volume tiers via KDE on log10(mean_daily_volume).

    Returns list of (tier_name, bool_mask) where mask is (n_symbols,).
    """
    # Mean daily volume per symbol (NaN-safe)
    mean_vol = np.nanmean(volume, axis=0) * np.nanmean(close, axis=0)
    log_vol = np.log10(np.maximum(mean_vol, 1.0))

    # KDE for natural tier boundaries
    valid = ~np.isnan(log_vol) & np.isfinite(log_vol)
    if valid.sum() < 20:
        logger.warning("  Too few symbols (%d) for stratification, using single tier", valid.sum())
        return [("all", np.ones(len(symbols), dtype=bool))]

    kde_data = log_vol[valid]
    kde = sp_stats.gaussian_kde(kde_data, bw_method=0.3)
    x_grid = np.linspace(kde_data.min(), kde_data.max(), 200)
    density = kde(x_grid)

    # Find local minima as tier boundaries
    minima = []
    for i in range(1, len(density) - 1):
        if density[i] < density[i - 1] and density[i] < density[i + 1]:
            minima.append(x_grid[i])

    # Use at most 2 boundaries → 3 tiers
    if len(minima) == 0:
        # Fallback: split at median
        med = np.median(kde_data)
        boundaries = [med]
    elif len(minima) == 1:
        boundaries = minima
    else:
        # Pick the 2 deepest minima
        min_vals = [kde(m)[0] for m in minima]
        sorted_idx = np.argsort(min_vals)
        boundaries = sorted([minima[sorted_idx[0]], minima[sorted_idx[1]]])

    # Build tier masks
    tiers = []
    boundary_labels = ['low', 'mid', 'high'] if len(boundaries) == 2 else ['low', 'high']
    cuts = [-np.inf] + boundaries + [np.inf]

    for i in range(len(cuts) - 1):
        mask = (log_vol >= cuts[i]) & (log_vol < cuts[i + 1]) & valid
        tier_name = boundary_labels[i]
        count = mask.sum()
        if count >= 15:  # Minimum tier size
            tiers.append((tier_name, mask))
            logger.info("  Tier '%s': %d symbols (vol range %.1f-%.1f)",
                        tier_name, count, cuts[i], cuts[i + 1])
        else:
            logger.info("  Tier '%s' too small (%d), merging with adjacent", tier_name, count)

    # If all merged away, single tier
    if not tiers:
        return [("all", valid)]

    return tiers


# ─── S4: n_effective Pre-Pass ───────────────────────────────────────────────

def compute_n_effective(open_, high, low, close, volume, ts_grid, fwd_ret_1h_bars, fwd_ret_dict):
    """
    Compute effective number of independent tests via eigenvalue decomposition
    of IC series correlations.

    Method:
    1. Subsample: last 1000 bars, 200 random symbols
    2. Compute ALL indicators on subsample via iter_all_variants()
    3. For each indicator, compute cross-sectional Spearman IC vs 1h forward returns
    4. Correlate IC series across indicators → (N, N) matrix
    5. Eigenvalue decomposition → n_effective = (sum(lambda))^2 / sum(lambda^2)
    """
    n_bars, n_symbols = close.shape

    # Subsample bars and symbols
    bar_end = n_bars
    bar_start = max(0, bar_end - N_EFF_SUBSAMPLE_BARS)
    n_sub_bars = bar_end - bar_start

    if n_symbols > N_EFF_SUBSAMPLE_SYMS:
        rng = np.random.RandomState(42)
        sym_idx = np.sort(rng.choice(n_symbols, N_EFF_SUBSAMPLE_SYMS, replace=False))
    else:
        sym_idx = np.arange(n_symbols)
    n_sub_syms = len(sym_idx)

    logger.info("  n_effective pre-pass: %d bars × %d symbols", n_sub_bars, n_sub_syms)
    t0 = time.time()

    # Subsample OHLCV
    sub_open = open_[bar_start:bar_end][:, sym_idx]
    sub_high = high[bar_start:bar_end][:, sym_idx]
    sub_low = low[bar_start:bar_end][:, sym_idx]
    sub_close = close[bar_start:bar_end][:, sym_idx]
    sub_vol = volume[bar_start:bar_end][:, sym_idx]
    sub_ts = ts_grid[bar_start:bar_end] if ts_grid is not None else None

    # Forward returns on subsample — pick the horizon closest to 1h
    if fwd_ret_1h_bars in fwd_ret_dict:
        sub_fwd = fwd_ret_dict[fwd_ret_1h_bars][bar_start:bar_end][:, sym_idx]
    else:
        # Fallback: use shortest available horizon
        shortest = min(fwd_ret_dict.keys())
        sub_fwd = fwd_ret_dict[shortest][bar_start:bar_end][:, sym_idx]
        logger.warning("  n_effective: no h=1h, using h=%d bars", shortest)

    # Compute all indicators on subsample
    ic_series_list = []
    indicator_names = []
    n_computed = 0

    for name, arr in iter_all_variants(sub_open, sub_high, sub_low, sub_close, sub_vol, sub_ts):
        # Compute cross-sectional Spearman IC at each bar
        ics = np.zeros(n_sub_bars)
        for t in range(n_sub_bars):
            ind_row = arr[t]
            ret_row = sub_fwd[t]
            valid = ~np.isnan(ind_row) & ~np.isnan(ret_row)
            nv = valid.sum()
            if nv < 20:
                ics[t] = 0.0  # Replace NaN with 0 for correlation
                continue
            i_ranks = np.argsort(np.argsort(ind_row[valid])).astype(np.float64)
            r_ranks = np.argsort(np.argsort(ret_row[valid])).astype(np.float64)
            im, rm = i_ranks.mean(), r_ranks.mean()
            isd, rsd = i_ranks.std(), r_ranks.std()
            if isd > 0 and rsd > 0:
                ics[t] = np.mean((i_ranks - im) * (r_ranks - rm)) / (isd * rsd)
            else:
                ics[t] = 0.0

        # Only keep if we have meaningful variation
        non_zero = np.count_nonzero(ics)
        if non_zero >= 10:
            ic_series_list.append(ics)
            indicator_names.append(name)

        n_computed += 1
        if n_computed % 50 == 0:
            logger.info("    n_effective: computed %d/%d variants (%.0fs)",
                        n_computed, total_variant_count(), time.time() - t0)

    n_usable = len(ic_series_list)
    logger.info("  n_effective: %d usable IC series from %d variants", n_usable, n_computed)

    if n_usable < 50:
        fallback = total_variant_count() // 5
        logger.warning("  WARNING: <50 usable IC series, falling back to n_effective=%d", fallback)
        return fallback

    # Correlation matrix of IC series
    ic_matrix = np.array(ic_series_list)  # (n_usable, n_sub_bars)
    corr = np.corrcoef(ic_matrix)  # (n_usable, n_usable)
    corr = np.nan_to_num(corr, nan=0.0)

    # Eigenvalue decomposition
    eigenvalues = np.linalg.eigvalsh(corr)
    eigenvalues = np.maximum(eigenvalues, 0)  # Numerical stability

    sum_lambda = eigenvalues.sum()
    sum_lambda_sq = (eigenvalues ** 2).sum()

    if sum_lambda_sq > 0:
        n_effective = (sum_lambda ** 2) / sum_lambda_sq
    else:
        n_effective = n_usable

    n_effective = int(round(n_effective))
    elapsed = time.time() - t0

    logger.info("  n_effective = %d (from %d variants, %.0fs)", n_effective, n_usable, elapsed)
    logger.info("  Top 5 eigenvalues: %s", np.sort(eigenvalues)[::-1][:5].round(2))

    # Sanity check
    if n_effective < 10 or n_effective > 500:
        logger.warning("  n_effective=%d outside expected 10-500 range, check data quality", n_effective)

    return n_effective


# ─── S5: Per-Indicator Statistics Functions ─────────────────────────────────

@dataclass
class VariantStats:
    """Stats for one variant × horizon × tier × return_type."""
    name: str
    family: str
    timeframe: str
    horizon_hours: float
    horizon_bars: int
    tier: str
    return_type: str  # 'raw', 'cost_adj', 'market_excess'
    mean_ic: float = 0.0
    t_stat: float = 0.0
    n_cs: int = 0
    ic_ir: float = 0.0
    quantile_spread: float = np.nan
    monotonicity: float = np.nan
    n_bins: int = 0
    stability_sign_flips: int = 0
    stability_n_windows: int = 0
    regime_ic_bearish: float = np.nan
    regime_ic_neutral: float = np.nan
    regime_ic_bullish: float = np.nan


def fama_macbeth_ic_single(indicator, fwd_ret, tier_mask):
    """
    Vectorized Fama-MacBeth IC for a single indicator.

    Uses 2D argsort on bars where all tier symbols have valid values (fast path),
    with per-bar fallback for bars with partial NaN (slow path).

    Spearman rank IC is invariant to monotonic transforms (constant subtraction,
    per-bar median subtraction), so this result is identical for raw, cost_adj,
    and market_excess return types. Callers should compute once per (indicator,
    horizon, tier) and reuse.

    Args:
        indicator: (n_bars, n_symbols) array
        fwd_ret: (n_bars, n_symbols) array
        tier_mask: (n_symbols,) bool

    Returns:
        (mean_ic, t_stat, n_cs, ic_ir, ic_series)
    """
    ind = indicator[:, tier_mask]   # (n_bars, n_tier)
    ret = fwd_ret[:, tier_mask]     # (n_bars, n_tier)
    n_bars, n_tier = ind.shape

    ic_series = np.full(n_bars, np.nan)

    if n_tier < MIN_CS_SYMBOLS:
        return 0.0, 0.0, 0, 0.0, ic_series

    # Joint validity mask
    both_valid = ~np.isnan(ind) & ~np.isnan(ret)  # (n_bars, n_tier)
    n_valid_per_bar = both_valid.sum(axis=1)       # (n_bars,)
    all_valid_bars = (n_valid_per_bar == n_tier)    # fully clean bars

    # ── FAST PATH: vectorized 2D rank correlation ──
    n_fast = all_valid_bars.sum()
    if n_fast > 0:
        fast_ind = ind[all_valid_bars]  # (n_fast, n_tier)
        fast_ret = ret[all_valid_bars]  # (n_fast, n_tier)

        # 2D argsort → ranks
        i_ranks = np.argsort(np.argsort(fast_ind, axis=1), axis=1).astype(np.float64)
        r_ranks = np.argsort(np.argsort(fast_ret, axis=1), axis=1).astype(np.float64)

        # Row-wise Pearson of ranks
        i_dev = i_ranks - i_ranks.mean(axis=1, keepdims=True)
        r_dev = r_ranks - r_ranks.mean(axis=1, keepdims=True)

        cov = (i_dev * r_dev).mean(axis=1)
        i_std = np.sqrt((i_dev ** 2).mean(axis=1))
        r_std = np.sqrt((r_dev ** 2).mean(axis=1))
        denom = i_std * r_std

        fast_ics = np.where(denom > 0, cov / denom, np.nan)
        ic_series[all_valid_bars] = fast_ics

    # ── SLOW PATH: bars with partial NaN but enough valid symbols ──
    partial_mask = ~all_valid_bars & (n_valid_per_bar >= MIN_CS_SYMBOLS)
    for t in np.where(partial_mask)[0]:
        valid = both_valid[t]
        i_r = np.argsort(np.argsort(ind[t, valid])).astype(np.float64)
        r_r = np.argsort(np.argsort(ret[t, valid])).astype(np.float64)
        im, rm = i_r.mean(), r_r.mean()
        isd, rsd = i_r.std(), r_r.std()
        if isd > 0 and rsd > 0:
            ic_series[t] = np.mean((i_r - im) * (r_r - rm)) / (isd * rsd)

    # ── Aggregate ──
    valid_ic = ~np.isnan(ic_series)
    n_cs = int(valid_ic.sum())

    if n_cs < 5:
        return 0.0, 0.0, n_cs, 0.0, ic_series

    ic_vals = ic_series[valid_ic]
    mean_ic = float(np.mean(ic_vals))
    std_ic = float(np.std(ic_vals, ddof=1))
    t_stat = mean_ic / (std_ic / np.sqrt(n_cs)) if std_ic > 0 else 0.0
    ic_ir = mean_ic / std_ic if std_ic > 0 else 0.0

    return mean_ic, t_stat, n_cs, ic_ir, ic_series


def quantile_spread_and_monotonicity(indicator, fwd_ret, tier_mask, n_bins):
    """
    Vectorized quantile spread and monotonicity.

    Fast path: for bars where all tier symbols are valid, sort returns by
    indicator rank and split into equal-count bins via reshape. This avoids
    per-bar np.percentile + np.digitize.

    Returns (spread, monotonicity_rho).
    """
    ind = indicator[:, tier_mask]   # (n_bars, n_tier)
    ret = fwd_ret[:, tier_mask]     # (n_bars, n_tier)
    n_bars, n_tier = ind.shape
    min_valid = max(n_bins * 5, MIN_CS_SYMBOLS)

    if n_tier < min_valid:
        return np.nan, np.nan

    both_valid = ~np.isnan(ind) & ~np.isnan(ret)
    n_valid_per_bar = both_valid.sum(axis=1)
    all_valid = (n_valid_per_bar == n_tier)

    spreads = []
    bin_means_all = []  # (n_bars_used, n_bins)

    # ── FAST PATH: all symbols valid → vectorized sort + bin ──
    n_fast = all_valid.sum()
    if n_fast > 0 and n_tier >= min_valid:
        fast_ind = ind[all_valid]   # (n_fast, n_tier)
        fast_ret = ret[all_valid]   # (n_fast, n_tier)

        # Sort returns by indicator rank order
        sort_idx = np.argsort(fast_ind, axis=1)
        ret_sorted = np.take_along_axis(fast_ret, sort_idx, axis=1)

        # Equal-count bins via linspace split points
        bin_edges = np.linspace(0, n_tier, n_bins + 1, dtype=int)
        bm_fast = np.zeros((n_fast, n_bins))
        for b in range(n_bins):
            bm_fast[:, b] = ret_sorted[:, bin_edges[b]:bin_edges[b + 1]].mean(axis=1)

        # Spreads: top bin minus bottom bin
        fast_spreads = bm_fast[:, -1] - bm_fast[:, 0]
        spreads.extend(fast_spreads.tolist())
        bin_means_all.append(bm_fast)

    # ── SLOW PATH: bars with partial NaN but enough valid symbols ──
    partial = ~all_valid & (n_valid_per_bar >= min_valid)
    for t in np.where(partial)[0]:
        valid = both_valid[t]
        iv = ind[t, valid]
        rv = ret[t, valid]
        nv = int(valid.sum())

        sort_idx = np.argsort(iv)
        rv_sorted = rv[sort_idx]

        edges = np.linspace(0, nv, n_bins + 1, dtype=int)
        bm = np.array([rv_sorted[edges[b]:edges[b + 1]].mean() for b in range(n_bins)])

        spreads.append(float(bm[-1] - bm[0]))
        bin_means_all.append(bm.reshape(1, -1))

    if len(spreads) < 10:
        return np.nan, np.nan

    mean_spread = float(np.mean(spreads))

    # Monotonicity: Spearman of bin_rank vs bin_mean, averaged across bars
    all_bm = np.vstack(bin_means_all)  # (n_total_bars, n_bins)
    bin_ranks = np.arange(n_bins, dtype=np.float64)
    ret_ranks = np.argsort(np.argsort(all_bm, axis=1), axis=1).astype(np.float64)

    # Vectorized row-wise Spearman
    b_dev = bin_ranks - bin_ranks.mean()  # same for all rows
    r_dev = ret_ranks - ret_ranks.mean(axis=1, keepdims=True)
    b_std = bin_ranks.std()
    r_std = np.sqrt((r_dev ** 2).mean(axis=1))
    denom = b_std * r_std
    cov = (b_dev * r_dev).mean(axis=1)
    valid_d = denom > 0
    rhos = cov[valid_d] / denom[valid_d]

    mono = float(rhos.mean()) if len(rhos) > 0 else np.nan
    return mean_spread, mono


def rolling_ic_stability(ic_series, window=90):
    """
    Count sign flips in rolling IC windows.

    Returns (n_sign_flips, n_windows).
    """
    valid = ~np.isnan(ic_series)
    ic_clean = ic_series[valid]
    n = len(ic_clean)

    if n < window * 2:
        return 0, 0

    n_windows = n - window + 1
    sign_flips = 0
    prev_sign = None

    for start in range(n_windows):
        w_mean = ic_clean[start:start + window].mean()
        curr_sign = 1 if w_mean >= 0 else -1
        if prev_sign is not None and curr_sign != prev_sign:
            sign_flips += 1
        prev_sign = curr_sign

    return sign_flips, n_windows


def regime_conditional_ic(indicator, fwd_ret, btc_close, tier_mask, trailing_window=720):
    """
    BTC trailing return → terciles → IC per regime.

    NOTE: trailing_window bars of warmup data (~11% at any TF) is consumed here.
    This only affects regime IC computation — primary FM IC uses all available bars.

    Args:
        trailing_window: bars of trailing BTC return (default 720 = ~30d at 1h)

    Returns (ic_bearish, ic_neutral, ic_bullish).
    """
    n_bars = indicator.shape[0]

    if btc_close is None or len(btc_close) < trailing_window + 10:
        return np.nan, np.nan, np.nan

    # BTC trailing return
    btc_ret = np.full(n_bars, np.nan)
    btc_ret[trailing_window:] = btc_close[trailing_window:] / btc_close[:-trailing_window] - 1.0

    # Tercile boundaries on the valid portion
    valid_btc = ~np.isnan(btc_ret)
    if valid_btc.sum() < 100:
        return np.nan, np.nan, np.nan

    p33, p66 = np.percentile(btc_ret[valid_btc], [33.3, 66.7])

    regimes = {
        'bearish': valid_btc & (btc_ret <= p33),
        'neutral': valid_btc & (btc_ret > p33) & (btc_ret <= p66),
        'bullish': valid_btc & (btc_ret > p66),
    }

    ind = indicator[:, tier_mask]   # (n_bars, n_tier)
    ret = fwd_ret[:, tier_mask]     # (n_bars, n_tier)
    n_tier = ind.shape[1]
    both_valid = ~np.isnan(ind) & ~np.isnan(ret)
    n_valid_per_bar = both_valid.sum(axis=1)
    all_valid = (n_valid_per_bar == n_tier)

    results = {}
    for regime_name, regime_mask in regimes.items():
        regime_bars = np.where(regime_mask)[0]
        if len(regime_bars) < 5:
            results[regime_name] = np.nan
            continue

        # Fast path: bars in this regime where all tier symbols are valid
        fast_bars = regime_bars[all_valid[regime_bars]]
        ic_vals = []
        if len(fast_bars) > 0 and n_tier >= MIN_CS_SYMBOLS:
            i_ranks = np.argsort(np.argsort(ind[fast_bars], axis=1), axis=1).astype(np.float64)
            r_ranks = np.argsort(np.argsort(ret[fast_bars], axis=1), axis=1).astype(np.float64)
            i_dev = i_ranks - i_ranks.mean(axis=1, keepdims=True)
            r_dev = r_ranks - r_ranks.mean(axis=1, keepdims=True)
            cov = (i_dev * r_dev).mean(axis=1)
            i_std = np.sqrt((i_dev ** 2).mean(axis=1))
            r_std = np.sqrt((r_dev ** 2).mean(axis=1))
            denom = i_std * r_std
            valid_denom = denom > 0
            ic_vals.extend(cov[valid_denom] / denom[valid_denom])

        # Slow path: partially valid bars
        slow_bars = regime_bars[~all_valid[regime_bars] & (n_valid_per_bar[regime_bars] >= MIN_CS_SYMBOLS)]
        for t in slow_bars:
            valid = both_valid[t]
            i_r = np.argsort(np.argsort(ind[t, valid])).astype(np.float64)
            r_r = np.argsort(np.argsort(ret[t, valid])).astype(np.float64)
            im, rm = i_r.mean(), r_r.mean()
            isd, rsd = i_r.std(), r_r.std()
            if isd > 0 and rsd > 0:
                ic_vals.append(np.mean((i_r - im) * (r_r - rm)) / (isd * rsd))

        results[regime_name] = float(np.mean(ic_vals)) if len(ic_vals) >= 5 else np.nan

    return results.get('bearish', np.nan), results.get('neutral', np.nan), results.get('bullish', np.nan)


# ─── S6: Main Screening Loop ───────────────────────────────────────────────

def _family_from_name(name):
    """Extract family prefix from variant name (e.g. 'RSI_14' → 'RSI')."""
    parts = name.split('_')
    # Try to find the family: everything before the first numeric part
    family_parts = []
    for p in parts:
        if p.isdigit() or (p.replace('.', '').isdigit()):
            break
        family_parts.append(p)
    return '_'.join(family_parts) if family_parts else name


def _load_checkpoint(checkpoint_path):
    """Load completed variant names from checkpoint."""
    if not checkpoint_path.exists():
        return set(), []
    data = np.load(checkpoint_path, allow_pickle=True)
    completed = set(data['completed_names'].tolist()) if 'completed_names' in data else set()
    stats = list(data['stats'].tolist()) if 'stats' in data else []
    logger.info("  Loaded checkpoint: %d completed variants, %d stat entries", len(completed), len(stats))
    return completed, stats


def _save_checkpoint(checkpoint_path, completed_names, stats_list):
    """Save screening progress."""
    np.savez_compressed(
        checkpoint_path,
        completed_names=np.array(list(completed_names)),
        stats=np.array([asdict(s) for s in stats_list], dtype=object),
    )


def screen_timeframe(database_url, timeframe, target_days, cache_dir, n_effective=None, resume=False):
    """
    Run the full screening pipeline for one timeframe.

    Returns (all_stats, n_effective, tier_info).
    """
    bar_minutes = TF_BAR_MINUTES[timeframe]
    horizons = _horizons_for_tf(bar_minutes)
    trailing_window_bars = max(1, round(30 * 24 * 60 / bar_minutes))  # ~30 days

    logger.info("=" * 70)
    logger.info("SCREENING %s — %d horizons, trailing_window=%d bars",
                timeframe, len(horizons), trailing_window_bars)
    logger.info("=" * 70)

    # 1. Load OHLCV
    open_, high, low, close, volume, ts_grid, symbols, btc_idx = \
        load_ohlcv_matrices(database_url, timeframe, target_days, cache_dir)
    n_bars, n_symbols = close.shape
    logger.info("  Peak RSS after OHLCV load: %.0f MB", _peak_rss_mb())

    # 2. Forward returns (memmap-aware)
    fwd_ret_raw = compute_forward_returns_raw(close, timeframe, cache_dir)
    if not fwd_ret_raw:
        logger.error("  No valid horizons for %s, skipping", timeframe)
        return [], n_effective, []
    logger.info("  Peak RSS after fwd returns: %.0f MB", _peak_rss_mb())

    # 3. Universe stratification
    tiers = stratify_universe(close, volume, symbols)

    # Compute empirical spread proxy per tier (for cost-adjusted returns)
    tier_spread_proxies = {}
    one_bar_ret = np.abs(close[1:] / close[:-1] - 1.0)
    for tier_name, tier_mask in tiers:
        tier_ret = one_bar_ret[:, tier_mask]
        med_spread = float(np.nanmedian(tier_ret)) * 2 * 10_000  # in bps
        tier_spread_proxies[tier_name] = med_spread
        logger.info("  Tier '%s' empirical spread proxy: %.1f bps", tier_name, med_spread)

    # Adaptive bin count per tier
    tier_n_bins = {}
    for tier_name, tier_mask in tiers:
        count = tier_mask.sum()
        tier_n_bins[tier_name] = max(3, min(10, count // 15))

    # 4. n_effective pre-pass (first TF only — caller passes in for subsequent TFs)
    if n_effective is None:
        # Find closest horizon to 1h in bars
        h1_bars = max(1, round(60 / bar_minutes))
        available = list(fwd_ret_raw.keys())
        closest = min(available, key=lambda x: abs(x - h1_bars))
        n_effective = compute_n_effective(
            open_, high, low, close, volume, ts_grid, closest, fwd_ret_raw
        )
        logger.info("  Peak RSS after n_effective: %.0f MB", _peak_rss_mb())

    # 5. BTC close for regime conditioning
    btc_close = close[:, btc_idx] if btc_idx is not None else None

    # 6. Checkpoint setup
    checkpoint_dir = Path(cache_dir) if cache_dir else Path(SCRIPTS_DIR) / "output" / "indicator_soup_cache"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"stats_checkpoint_{timeframe}.npz"

    completed_names = set()
    all_stats = []
    if resume:
        completed_names, recovered_dicts = _load_checkpoint(checkpoint_path)
        for d in recovered_dicts:
            all_stats.append(VariantStats(**d))

    # 7. Stream through indicators
    expected_total = total_variant_count()
    logger.info("  Starting variant screening: %d expected, %d already completed",
                expected_total, len(completed_names))
    t_screen_start = time.time()
    n_processed = 0
    n_skipped = 0

    for name, indicator_arr in iter_all_variants(open_, high, low, close, volume, ts_grid):
        if name in completed_names:
            n_skipped += 1
            continue

        family = _family_from_name(name)

        # For each horizon
        for h_hours, h_bars in horizons:
            if h_bars not in fwd_ret_raw:
                continue
            raw_ret = fwd_ret_raw[h_bars]

            # For each tier
            for tier_name, tier_mask in tiers:
                n_bins = tier_n_bins[tier_name]

                # Spearman rank IC is invariant to monotonic transforms:
                #   cost_adj = raw - constant → same ranks
                #   market_excess = raw - per-bar median → same ranks
                # Compute IC ONCE on raw returns, reuse for all 3 return types.
                mean_ic, t_stat, n_cs, ic_ir, ic_series = \
                    fama_macbeth_ic_single(indicator_arr, raw_ret, tier_mask)

                for ret_type in ['raw', 'cost_adj', 'market_excess']:
                    stat = VariantStats(
                        name=name,
                        family=family,
                        timeframe=timeframe,
                        horizon_hours=h_hours,
                        horizon_bars=h_bars,
                        tier=tier_name,
                        return_type=ret_type,
                        mean_ic=mean_ic,
                        t_stat=t_stat,
                        n_cs=n_cs,
                        ic_ir=ic_ir,
                    )

                    # Quantile spread + monotonicity: cost_adj only (uses actual return values, not ranks)
                    if ret_type == 'cost_adj':
                        cost_adj_ret = derive_cost_adjusted(raw_ret, timeframe, tier_spread_proxies[tier_name])
                        spread, mono = quantile_spread_and_monotonicity(
                            indicator_arr, cost_adj_ret, tier_mask, n_bins
                        )
                        stat.quantile_spread = spread
                        stat.monotonicity = mono
                        stat.n_bins = n_bins

                    # Rolling IC stability: raw + top tier only
                    if ret_type == 'raw' and tier_name == tiers[-1][0]:
                        flips, n_win = rolling_ic_stability(ic_series, window=90)
                        stat.stability_sign_flips = flips
                        stat.stability_n_windows = n_win

                    # Regime IC: raw + top tier only
                    if ret_type == 'raw' and tier_name == tiers[-1][0]:
                        ic_b, ic_n, ic_bull = regime_conditional_ic(
                            indicator_arr, raw_ret, btc_close, tier_mask, trailing_window_bars
                        )
                        stat.regime_ic_bearish = ic_b
                        stat.regime_ic_neutral = ic_n
                        stat.regime_ic_bullish = ic_bull

                    all_stats.append(stat)

        completed_names.add(name)
        n_processed += 1
        del indicator_arr  # Explicit release — keeps RSS flat across iterations

        # Progress logging
        if n_processed % PROGRESS_INTERVAL == 0:
            elapsed = time.time() - t_screen_start
            rate = n_processed / elapsed if elapsed > 0 else 0
            eta = (expected_total - n_processed - n_skipped) / rate / 60 if rate > 0 else 0
            logger.info("  [%s] %d/%d variants (%.1f/s, ETA %.0f min, RSS=%.0f MB)",
                        timeframe, n_processed + n_skipped, expected_total,
                        rate, eta, _peak_rss_mb())

        # Checkpoint
        if n_processed % CHECKPOINT_INTERVAL == 0:
            _save_checkpoint(checkpoint_path, completed_names, all_stats)
            logger.info("  Checkpoint saved: %d variants", len(completed_names))

    # Final checkpoint
    _save_checkpoint(checkpoint_path, completed_names, all_stats)
    elapsed = time.time() - t_screen_start
    logger.info("  %s screening complete: %d variants in %.1f min (%d stat entries)",
                timeframe, n_processed, elapsed / 60, len(all_stats))
    logger.info("  Peak RSS: %.0f MB", _peak_rss_mb())

    tier_info = [(tn, int(tm.sum())) for tn, tm in tiers]

    # Explicit cleanup of large arrays before returning
    for arr in fwd_ret_raw.values():
        if hasattr(arr, 'flush'):
            arr.flush()
    del open_, high, low, close, volume, fwd_ret_raw
    if 'btc_close' in locals():
        del btc_close
    gc.collect()
    logger.info("  Memory cleanup complete. RSS: %.0f MB", _peak_rss_mb())

    return all_stats, n_effective, tier_info


# ─── S7: BH FDR Correction ─────────────────────────────────────────────────

def apply_bh_fdr(stats, n_effective, n_tiers, n_horizons, alpha=0.05):
    """
    Benjamini-Hochberg FDR correction on FM IC t-stats.

    Total effective tests = n_effective × n_tiers × n_horizons.
    Returns list of (stat, survives_fdr, adjusted_pvalue).
    """
    m = n_effective * n_tiers * n_horizons

    # Convert t-stats to two-tailed p-values
    entries = []
    for stat in stats:
        if stat.return_type != 'raw':  # FDR on raw returns only (cost_adj/market_excess are derivatives)
            entries.append((stat, False, 1.0))
            continue
        if stat.n_cs < 30:
            entries.append((stat, False, 1.0))
            continue

        p_val = 2 * sp_stats.norm.sf(abs(stat.t_stat))
        entries.append((stat, None, p_val))

    # BH step-up on the raw return entries
    raw_entries = [(i, e) for i, e in enumerate(entries) if e[1] is None]
    if not raw_entries:
        return entries

    # Sort by p-value
    raw_entries.sort(key=lambda x: x[1][2])
    n_tests = len(raw_entries)

    # BH procedure: find largest k where p(k) <= k/m * alpha
    # Use the effective m, not the raw number of tests
    survivors = set()
    for k_idx, (orig_idx, entry) in enumerate(raw_entries):
        k = k_idx + 1
        threshold = (k / m) * alpha
        if entry[2] <= threshold:
            survivors.add(orig_idx)

    # If any survive, all with smaller p-values also survive (step-up)
    if survivors:
        max_survivor_idx = max(
            k_idx for k_idx, (orig_idx, _) in enumerate(raw_entries) if orig_idx in survivors
        )
        survivors = {raw_entries[k_idx][0] for k_idx in range(max_survivor_idx + 1)}

    # Compute adjusted p-values (BH-adjusted)
    result = list(entries)
    for k_idx in range(len(raw_entries) - 1, -1, -1):
        orig_idx, entry = raw_entries[k_idx]
        k = k_idx + 1
        adj_p = entry[2] * m / k
        if k_idx < len(raw_entries) - 1:
            next_orig_idx = raw_entries[k_idx + 1][0]
            adj_p = min(adj_p, result[next_orig_idx][2])
        adj_p = min(adj_p, 1.0)
        survives = orig_idx in survivors
        result[orig_idx] = (entry[0], survives, adj_p)

    n_survive = sum(1 for _, s, _ in result if s)
    n_raw_tested = len(raw_entries)
    logger.info("  BH FDR: %d/%d raw-return entries survive (m_eff=%d, alpha=%.2f)",
                n_survive, n_raw_tested, m, alpha)

    return result


# ─── S8: Residual IC Check ─────────────────────────────────────────────────

def compute_u1_features(close, high, low, volume):
    """
    Compute the 5 U1 features as (n_bars, n_symbols) matrices.

    1. vol_z: (vol - mean_20) / std_20
    2. bb_width: 4 * std_20 / sma_20
    3. clv: (close - low) / (high - low)
    4. range_pct: (high - low) / close
    5. return_12: close / close[-12] - 1
    """
    sma_20 = rolling_mean(close, 20)
    std_20 = rolling_std(close, 20)
    vol_mean_20 = rolling_mean(volume, 20)
    vol_std_20 = rolling_std(volume, 20, ddof=0)

    vol_z = np.full_like(volume, np.nan)
    valid_std = vol_std_20 > 0
    vol_z[valid_std] = (volume[valid_std] - vol_mean_20[valid_std]) / vol_std_20[valid_std]

    bb_width = np.full_like(close, np.nan)
    valid_sma = sma_20 > 0
    bb_width[valid_sma] = 4 * std_20[valid_sma] / sma_20[valid_sma]

    hl_range = high - low
    clv = np.full_like(close, np.nan)
    valid_range = hl_range > 0
    clv[valid_range] = (close[valid_range] - low[valid_range]) / hl_range[valid_range]

    range_pct = np.full_like(close, np.nan)
    valid_close = close > 0
    range_pct[valid_close] = hl_range[valid_close] / close[valid_close]

    return_12 = np.full_like(close, np.nan)
    return_12[12:] = close[12:] / close[:-12] - 1.0

    return {'vol_z': vol_z, 'bb_width': bb_width, 'clv': clv,
            'range_pct': range_pct, 'return_12': return_12}


def residual_ic_check(survivors, open_, high, low, close, volume, ts_grid, fwd_ret_raw, tiers):
    """
    For each FDR survivor, regress out U1 features and compute partial IC.

    Re-runs iter_all_variants to get survivor indicator arrays (fast from .npy cache).
    Returns dict[name, {unconditional_ic, conditional_ic, is_proxy}].
    """
    survivor_names = {s.name for s in survivors}
    if not survivor_names:
        logger.info("  No FDR survivors — skipping residual IC check")
        return {}

    logger.info("  Residual IC check: %d survivors", len(survivor_names))
    t0 = time.time()

    # Compute U1 features
    u1_feats = compute_u1_features(close, high, low, volume)
    u1_names = sorted(u1_feats.keys())
    n_u1 = len(u1_names)

    # Get survivor stats to know which horizon/tier to use
    survivor_best = {}
    for s in survivors:
        key = s.name
        if key not in survivor_best or abs(s.t_stat) > abs(survivor_best[key].t_stat):
            survivor_best[key] = s

    # Collect survivor arrays
    survivor_arrays = {}
    for name, arr in iter_all_variants(open_, high, low, close, volume, ts_grid):
        if name in survivor_names:
            survivor_arrays[name] = arr
        if len(survivor_arrays) == len(survivor_names):
            break  # Got all of them

    # Top tier mask (last tier)
    top_tier_name, top_tier_mask = tiers[-1]

    results = {}
    for name, arr in survivor_arrays.items():
        best = survivor_best[name]
        h_bars = best.horizon_bars
        if h_bars not in fwd_ret_raw:
            continue
        fwd = fwd_ret_raw[h_bars]

        n_bars = arr.shape[0]
        uncond_ics = []
        cond_ics = []

        for t in range(n_bars):
            ind_row = arr[t, top_tier_mask]
            ret_row = fwd[t, top_tier_mask]

            # Collect U1 features for this bar
            u1_rows = []
            for u1n in u1_names:
                u1_rows.append(u1_feats[u1n][t, top_tier_mask])

            # Valid mask: all must be non-NaN
            valid = ~np.isnan(ind_row) & ~np.isnan(ret_row)
            for u1r in u1_rows:
                valid &= ~np.isnan(u1r)
            nv = valid.sum()
            if nv < MIN_CS_SYMBOLS:
                continue

            # Rank everything
            i_ranks = np.argsort(np.argsort(ind_row[valid])).astype(np.float64)
            r_ranks = np.argsort(np.argsort(ret_row[valid])).astype(np.float64)
            u1_rank_matrix = np.column_stack([
                np.argsort(np.argsort(u1r[valid])).astype(np.float64) for u1r in u1_rows
            ])  # (nv, n_u1)

            # Unconditional Spearman IC
            im, rm = i_ranks.mean(), r_ranks.mean()
            isd, rsd = i_ranks.std(), r_ranks.std()
            if isd > 0 and rsd > 0:
                uncond_ic = np.mean((i_ranks - im) * (r_ranks - rm)) / (isd * rsd)
                uncond_ics.append(uncond_ic)

            # OLS residuals: regress indicator ranks and return ranks on U1 ranks
            # Add intercept
            X = np.column_stack([np.ones(nv), u1_rank_matrix])

            try:
                # Indicator residual
                beta_i = np.linalg.lstsq(X, i_ranks, rcond=None)[0]
                resid_i = i_ranks - X @ beta_i

                # Return residual
                beta_r = np.linalg.lstsq(X, r_ranks, rcond=None)[0]
                resid_r = r_ranks - X @ beta_r

                # Partial Spearman IC on residuals
                ri_std = resid_i.std()
                rr_std = resid_r.std()
                if ri_std > 0 and rr_std > 0:
                    cond_ic = np.mean(resid_i * resid_r) / (ri_std * rr_std)
                    cond_ics.append(cond_ic)
            except np.linalg.LinAlgError:
                continue

        uncond = float(np.mean(uncond_ics)) if uncond_ics else 0.0
        cond = float(np.mean(cond_ics)) if cond_ics else 0.0
        is_proxy = abs(uncond) > 0.01 and abs(cond) < 0.005

        results[name] = {
            'unconditional_ic': uncond,
            'conditional_ic': cond,
            'is_proxy': is_proxy,
            'n_cs': len(uncond_ics),
        }

        logger.info("    %s: uncond=%.4f, cond=%.4f, proxy=%s",
                     name, uncond, cond, is_proxy)

    logger.info("  Residual IC complete in %.1fs", time.time() - t0)
    return results


# ─── S9: Output ─────────────────────────────────────────────────────────────

def write_output(all_stats_by_tf, fdr_results_by_tf, residual_results,
                 n_effective, tier_info_by_tf, args, output_dir):
    """Write JSON + NPZ output files."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / f"indicator_soup_screen_{timestamp}.json"
    npz_path = output_path / f"indicator_soup_screen_{timestamp}.npz"

    # Identify FDR survivors across all timeframes
    survivors = []
    all_fdr_stats = []
    for tf, fdr_list in fdr_results_by_tf.items():
        for stat, survives, adj_p in fdr_list:
            d = asdict(stat)
            d['survives_fdr'] = bool(survives) if survives is not None else False
            d['adjusted_pvalue'] = float(adj_p)
            all_fdr_stats.append(d)
            if survives:
                survivors.append(d)

    # Summary
    summary = {
        'timestamp': timestamp,
        'args': vars(args),
        'n_effective': n_effective,
        'total_variants': total_variant_count(),
        'timeframes_screened': list(all_stats_by_tf.keys()),
        'per_tf_summary': {},
    }

    for tf, stats in all_stats_by_tf.items():
        n_raw = sum(1 for s in stats if s.return_type == 'raw')
        n_survive = sum(1 for s, surv, _ in fdr_results_by_tf.get(tf, []) if surv)
        summary['per_tf_summary'][tf] = {
            'n_stat_entries': len(stats),
            'n_raw_entries': n_raw,
            'n_fdr_survivors': n_survive,
            'tiers': tier_info_by_tf.get(tf, []),
        }

    # Residual IC results
    residual_output = {}
    for name, res in residual_results.items():
        residual_output[name] = {
            'unconditional_ic': res['unconditional_ic'],
            'conditional_ic': res['conditional_ic'],
            'is_proxy': res['is_proxy'],
            'n_cs': res['n_cs'],
        }

    output = {
        'summary': summary,
        'survivors': survivors,
        'residual_ic': residual_output,
        'all_stats_count': sum(len(s) for s in all_stats_by_tf.values()),
    }

    # NaN-safe JSON encoding
    class NanEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                return None
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return float(obj) if np.isfinite(obj) else None
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return super().default(obj)

    with open(json_path, 'w') as f:
        json.dump(output, f, indent=2, cls=NanEncoder)
    logger.info("  JSON output: %s (%.1f KB)", json_path, json_path.stat().st_size / 1024)

    # NPZ with all stat arrays for programmatic access
    stat_dicts = []
    for tf, stats in all_stats_by_tf.items():
        for s in stats:
            stat_dicts.append(asdict(s))

    np.savez_compressed(
        npz_path,
        all_stats=np.array(stat_dicts, dtype=object),
        n_effective=np.array(n_effective),
        tier_info=np.array(str(tier_info_by_tf), dtype=object),
        survivors=np.array([s['name'] for s in survivors]) if survivors else np.array([]),
    )
    logger.info("  NPZ output: %s (%.1f KB)", npz_path, npz_path.stat().st_size / 1024)

    return json_path, npz_path


# ─── S10: Main + CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Indicator Soup Phase 2: Cross-Sectional Screening")
    parser.add_argument('--days', type=int, default=270,
                        help="Target days of data (default: 270)")
    parser.add_argument('--timeframes', type=str, default='1h,4h,15m,5m',
                        help="Comma-separated timeframes (default: 1h,4h,15m,5m)")
    parser.add_argument('--cache-dir', type=str, default=None,
                        help="Cache directory for .npy files (default: output/indicator_soup_cache)")
    parser.add_argument('--resume', action='store_true',
                        help="Resume from checkpoint")
    parser.add_argument('--alpha', type=float, default=0.05,
                        help="FDR alpha threshold (default: 0.05)")
    args = parser.parse_args()

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    timeframes = [tf.strip() for tf in args.timeframes.split(',')]
    for tf in timeframes:
        if tf not in TF_BAR_MINUTES:
            logger.error("Unknown timeframe: %s", tf)
            sys.exit(1)

    # Order TFs: 1h first (for n_effective), then ascending memory (4h, 15m, 5m)
    if '1h' in timeframes:
        ordered = ['1h'] + sorted([tf for tf in timeframes if tf != '1h'],
                                  key=lambda tf: TF_BAR_MINUTES[tf], reverse=True)
    else:
        ordered = sorted(timeframes, key=lambda tf: TF_BAR_MINUTES[tf], reverse=True)
    timeframes = ordered

    # Default cache dir
    if args.cache_dir:
        cache_dir = args.cache_dir
    elif os.path.isdir('/scripts'):
        cache_dir = '/scripts/output/indicator_soup_cache'
    else:
        cache_dir = os.path.join(SCRIPTS_DIR, 'output', 'indicator_soup_cache')
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    # Output dir
    if os.path.isdir('/scripts'):
        output_dir = '/scripts/output'
    else:
        output_dir = os.path.join(SCRIPTS_DIR, 'output')

    logger.info("Indicator Soup Screen — Phase 2")
    logger.info("  Days: %d, Timeframes: %s, Cache: %s", args.days, timeframes, cache_dir)
    logger.info("  Variant count: %d", total_variant_count())
    logger.info("  Peak RSS at start: %.0f MB", _peak_rss_mb())

    t_total_start = time.time()
    all_stats_by_tf = {}
    fdr_results_by_tf = {}
    tier_info_by_tf = {}
    n_effective = None  # Computed on first TF, reused

    for tf in timeframes:
        # 5m uses memmap — no day cap needed
        tf_days = args.days

        stats, n_effective, tier_info = screen_timeframe(
            database_url, tf, tf_days, cache_dir,
            n_effective=n_effective, resume=args.resume,
        )
        all_stats_by_tf[tf] = stats
        tier_info_by_tf[tf] = tier_info

        # FDR correction per timeframe
        n_tiers = len(tier_info)
        n_horizons = len(_horizons_for_tf(TF_BAR_MINUTES[tf]))
        fdr_results = apply_bh_fdr(stats, n_effective, n_tiers, n_horizons, alpha=args.alpha)
        fdr_results_by_tf[tf] = fdr_results

        # Force GC between timeframes
        gc.collect()
        logger.info("  Post-%s GC. RSS: %.0f MB", tf, _peak_rss_mb())

    # Collect all FDR survivors for residual IC check
    all_survivors = []
    for tf, fdr_list in fdr_results_by_tf.items():
        for stat, survives, adj_p in fdr_list:
            if survives:
                all_survivors.append(stat)

    logger.info("=" * 70)
    logger.info("FDR SURVIVORS: %d total across all timeframes", len(all_survivors))
    for s in all_survivors[:20]:
        logger.info("  %s @ %s h=%.1f tier=%s IC=%.4f t=%.2f",
                     s.name, s.timeframe, s.horizon_hours, s.tier, s.mean_ic, s.t_stat)
    if len(all_survivors) > 20:
        logger.info("  ... and %d more", len(all_survivors) - 20)

    # Residual IC check (use first timeframe's data)
    residual_results = {}
    if all_survivors:
        first_tf = timeframes[0]
        logger.info("Residual IC check using %s data...", first_tf)

        # Reload OHLCV from cache (fast)
        open_, high, low, close, volume, ts_grid, symbols, btc_idx = \
            load_ohlcv_matrices(database_url, first_tf, args.days, cache_dir)
        fwd_ret_raw = compute_forward_returns_raw(close, first_tf, cache_dir)
        tiers = stratify_universe(close, volume, symbols)

        residual_results = residual_ic_check(
            all_survivors, open_, high, low, close, volume, ts_grid, fwd_ret_raw, tiers
        )

        n_proxies = sum(1 for r in residual_results.values() if r['is_proxy'])
        logger.info("  Proxies flagged: %d/%d", n_proxies, len(residual_results))

    # Write output
    json_path, npz_path = write_output(
        all_stats_by_tf, fdr_results_by_tf, residual_results,
        n_effective, tier_info_by_tf, args, output_dir,
    )

    total_elapsed = time.time() - t_total_start
    logger.info("=" * 70)
    logger.info("COMPLETE in %.1f min", total_elapsed / 60)
    logger.info("  JSON: %s", json_path)
    logger.info("  NPZ:  %s", npz_path)
    logger.info("  Peak RSS: %.0f MB", _peak_rss_mb())


if __name__ == '__main__':
    main()
