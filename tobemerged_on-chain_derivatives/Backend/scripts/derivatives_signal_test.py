#!/usr/bin/env python3
"""
Derivatives Signal Test — Kill/Confirm Protocol
=================================================

Answers one question: do derivatives features (funding rates, open interest,
long/short ratios, taker buy/sell) contain any predictive information for
the ignition model?

Two phases:
  Phase 1: Conditional analysis — for compressed bars with derivatives data,
           do derivatives features predict which compressions ignite?
  Phase 2: IC screening — for ALL bar-symbol observations, rank-IC of each
           derivatives feature vs forward returns.

If no feature survives either phase → KILL.
If any feature survives → PROCEED to walk-forward validation.

Usage:
    tmux new -s deriv-test
    docker compose run --rm -T worker python3 /scripts/derivatives_signal_test.py --days 45 --funding-days 400
"""

# ─── Standard library ────────────────────────────────────────────────────────

import os
import sys
import time
import json
import argparse
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

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
if os.path.isdir('/app'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))

# ─── BLAS thread control (before numpy import) ───────────────────────────────
_BLAS_KEYS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')
for _k in _BLAS_KEYS:
    os.environ.setdefault(_k, '8')

# ─── Third-party ─────────────────────────────────────────────────────────────

import numpy as np
import psycopg

try:
    from scipy.stats import chi2_contingency, spearmanr
except ImportError:
    import subprocess
    subprocess.check_call(["pip", "install", "scipy", "--break-system-packages", "-q"])
    from scipy.stats import chi2_contingency, spearmanr

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants (from signal_genesis_ignition.py) ──────────────────────────────

HORIZON = 24                   # bars forward for ignition detection
IGNITION_THRESHOLD = 0.20      # 20% max forward high return = ignition event
WARMUP_BARS = 55               # longest lookback + buffer
MIN_BARS_PER_SYMBOL = 100
BTC_SYMBOL = "BTCUSDT"

# Kill/confirm thresholds (deliberately lenient)
IC_THRESHOLD = 0.015           # |IC| > this with p < 0.01 to survive
MI_THRESHOLD = 0.005           # binned MI > this as nonlinear cross-check


# ═══════════════════════════════════════════════════════════════════════════════
# ROLLING UTILITIES (copied verbatim from signal_genesis_ignition.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_sum_2d(arr, w):
    cs = np.cumsum(arr, axis=0)
    out = np.empty_like(arr)
    out[:w] = cs[:w]
    out[w:] = cs[w:] - cs[:-w]
    return out


def _rolling_mean_2d(arr, w):
    rs = _rolling_sum_2d(arr, w)
    out = rs / w
    out[:w-1] = np.nan
    return out


def _rolling_std_2d(arr, w):
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


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING (load_ohlcv_matrices copied from signal_genesis_ignition.py)
# ═══════════════════════════════════════════════════════════════════════════════

def load_ohlcv_matrices(database_url: str, days: int):
    """Load 1h OHLCV and build aligned (n_bars, n_symbols) matrices."""
    start_ts = datetime.now(timezone.utc) - timedelta(days=days + 15)
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

    all_ts = set()
    for rows in sym_rows.values():
        for r in rows:
            ts = r[1].replace(tzinfo=timezone.utc).timestamp() if r[1].tzinfo is None else r[1].timestamp()
            all_ts.add(ts)
    ts_grid = np.array(sorted(all_ts))
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)

    valid_symbols = []
    for sym, rows in sorted(sym_rows.items()):
        if len(rows) >= MIN_BARS_PER_SYMBOL:
            valid_symbols.append(sym)

    n_symbols = len(valid_symbols)
    logger.info("  %d symbols with >= %d bars, %d total bars in grid",
                n_symbols, MIN_BARS_PER_SYMBOL, n_bars)

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

    for s_idx in range(n_symbols):
        for arr in [close, open_, high, low, volume]:
            col = arr[:, s_idx]
            mask = np.isnan(col)
            if mask.any() and not mask.all():
                idx = np.where(~mask, np.arange(n_bars), 0)
                np.maximum.accumulate(idx, out=idx)
                arr[:, s_idx] = col[idx]

    mem_mb = (close.nbytes + open_.nbytes + high.nbytes + low.nbytes + volume.nbytes) / 1024**2
    logger.info("  Matrices: (%d, %d), %.1f MB, BTC idx=%s",
                n_bars, n_symbols, mem_mb, btc_idx)

    return close, open_, high, low, volume, ts_grid, valid_symbols, btc_idx


# ═══════════════════════════════════════════════════════════════════════════════
# EVENT CATALOG (copied from signal_genesis_ignition.py)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_max_forward_high(close, high, horizon=HORIZON):
    """Compute max(high[t+1:t+horizon+1]) / close[t] - 1 for each bar."""
    n_bars, n_symbols = close.shape
    mfh = np.full((n_bars, n_symbols), np.nan, dtype=np.float32)
    for h_offset in range(1, horizon + 1):
        if h_offset >= n_bars:
            break
        ratio = high[h_offset:] / close[:-h_offset]
        end = min(n_bars - h_offset, n_bars)
        current = mfh[:end]
        candidate = ratio[:end].astype(np.float32) - 1.0
        mfh[:end] = np.where(np.isnan(current), candidate,
                             np.where(np.isnan(candidate), current,
                                      np.maximum(current, candidate)))
    return mfh


def build_event_catalog(mfh, threshold=IGNITION_THRESHOLD):
    """Identify ignition events: bars where max forward high >= threshold."""
    event_mask = (~np.isnan(mfh)) & (mfh >= threshold)
    events = list(zip(*np.where(event_mask)))
    return events, event_mask


# ═══════════════════════════════════════════════════════════════════════════════
# DERIVATIVES FEATURE LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_derivatives_features(database_url: str, symbols: list, ts_grid: np.ndarray):
    """Load derivatives_features_1h aligned to the OHLCV grid.

    Returns dict: {feature_name: np.ndarray of shape (n_bars, n_symbols)} with NaN for missing.
    """
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}

    n_bars = len(ts_grid)
    n_symbols = len(symbols)

    feature_names = [
        'funding_rate', 'funding_rate_ma8', 'funding_rate_acceleration', 'funding_rate_zscore',
        'oi_value', 'oi_change_pct_1h', 'oi_change_pct_4h', 'oi_change_pct_24h',
        'ls_ratio', 'ls_ratio_change_4h', 'ls_ratio_extreme',
        'taker_buy_ratio', 'taker_imbalance', 'taker_imbalance_ma',
    ]

    features = {name: np.full((n_bars, n_symbols), np.nan) for name in feature_names}

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cols = ', '.join(feature_names)
            cur.execute(f"""
                SELECT symbol, bar_start_ts, {cols}
                FROM derivatives_features_1h
                ORDER BY symbol, bar_start_ts
            """)
            loaded = 0
            for row in cur:
                sym, ts_val = row[0], row[1]
                if sym not in sym_to_idx:
                    continue
                s_idx = sym_to_idx[sym]
                ts_epoch = ts_val.replace(tzinfo=timezone.utc).timestamp() if ts_val.tzinfo is None else ts_val.timestamp()
                if ts_epoch not in ts_to_idx:
                    continue
                t_idx = ts_to_idx[ts_epoch]
                for f_idx, fname in enumerate(feature_names):
                    val = row[2 + f_idx]
                    if val is not None:
                        features[fname][t_idx, s_idx] = float(val)
                loaded += 1

    logger.info("Loaded %d derivatives feature rows, %d feature columns", loaded, len(feature_names))

    # Report coverage
    for fname in feature_names:
        non_nan = np.count_nonzero(~np.isnan(features[fname]))
        total = n_bars * n_symbols
        logger.info("  %s: %.1f%% non-NaN (%d/%d)", fname, 100 * non_nan / total, non_nan, total)

    return features, feature_names


def load_funding_features_long(database_url: str, symbols: list, ts_grid: np.ndarray):
    """Compute funding rate features from raw funding_rates table for full history.

    Funding rates are every 4h or 8h. Forward-fill to hourly grid,
    then compute: rate, ma8, acceleration, zscore.

    Returns dict: {feature_name: np.ndarray (n_bars, n_symbols)}.
    """
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)
    n_symbols = len(symbols)

    # Load raw funding rates
    raw_rate = np.full((n_bars, n_symbols), np.nan)

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, funding_time, funding_rate
                FROM funding_rates
                WHERE source = 'binance_futures'
                ORDER BY symbol, funding_time
            """)
            loaded = 0
            for sym, ft, rate in cur:
                if sym not in sym_to_idx:
                    continue
                s_idx = sym_to_idx[sym]
                ts_epoch = ft.replace(tzinfo=timezone.utc).timestamp() if ft.tzinfo is None else ft.timestamp()
                if ts_epoch in ts_to_idx:
                    t_idx = ts_to_idx[ts_epoch]
                    raw_rate[t_idx, s_idx] = float(rate)
                    loaded += 1
                else:
                    # Snap to nearest hour (funding times are on 0/8/16 UTC boundaries)
                    snapped = (ts_epoch // 3600) * 3600
                    if snapped in ts_to_idx:
                        t_idx = ts_to_idx[snapped]
                        raw_rate[t_idx, s_idx] = float(rate)
                        loaded += 1

    logger.info("Loaded %d raw funding rate points for long-window analysis", loaded)

    # Forward-fill to hourly grid (per symbol)
    for s in range(n_symbols):
        col = raw_rate[:, s]
        mask = ~np.isnan(col)
        if mask.any():
            idx = np.where(mask, np.arange(n_bars), 0)
            np.maximum.accumulate(idx, out=idx)
            raw_rate[:, s] = col[idx]

    # Compute features from forward-filled rates
    # 1. funding_rate = raw (forward-filled)
    # 2. funding_rate_ma8 = 8-hour rolling mean
    ma8 = _rolling_mean_2d(raw_rate, 8)

    # 3. funding_rate_acceleration = rate[t] - rate[t-3]
    accel = np.full_like(raw_rate, np.nan)
    accel[3:] = raw_rate[3:] - raw_rate[:-3]

    # 4. funding_rate_zscore = (rate - 30d rolling mean) / 30d rolling std
    roll_mean = _rolling_mean_2d(raw_rate, 720)   # 720 hours = 30 days
    roll_std = _rolling_std_2d(raw_rate, 720)
    zscore = np.full_like(raw_rate, np.nan)
    valid = roll_std > 0
    zscore[valid] = (raw_rate[valid] - roll_mean[valid]) / roll_std[valid]

    features = {
        'funding_rate_long': raw_rate,
        'funding_rate_ma8_long': ma8,
        'funding_rate_acceleration_long': accel,
        'funding_rate_zscore_long': zscore,
    }

    non_nan = np.count_nonzero(~np.isnan(raw_rate))
    total = n_bars * n_symbols
    logger.info("Funding long-window: %.1f%% coverage (%d/%d)", 100 * non_nan / total, non_nan, total)

    return features


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: CONDITIONAL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_compressions(close, lookback=100, pctile_threshold=10.0):
    """Detect volatility compression: BB width in bottom percentile.

    Returns boolean mask (n_bars, n_symbols) where True = compressed.
    """
    n_bars, n_symbols = close.shape

    # BB width = 4 * std_20 / sma_20
    sma_20 = _rolling_mean_2d(close, 20)
    std_20 = _rolling_std_2d(close, 20)
    bb_width = np.where(sma_20 > 0, 4 * std_20 / sma_20, np.nan)

    compressed = np.full((n_bars, n_symbols), False)

    if n_bars <= lookback:
        return compressed

    # Try fast vectorized approach first, fall back to loop if OOM
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        # sliding_window_view gives (n_bars - lookback + 1, n_symbols, lookback)
        windows = sliding_window_view(bb_width, lookback, axis=0)
        current = bb_width[lookback - 1:]  # (n_windows, n_symbols)

        # Count values below current in each window
        n_below = np.nansum(windows < current[:, :, np.newaxis], axis=2)
        n_valid = np.nansum(~np.isnan(windows), axis=2)
        pctile = np.where(n_valid > 0, 100.0 * n_below / n_valid, 50.0)
        compressed[lookback - 1:] = pctile < pctile_threshold

    except MemoryError:
        logger.warning("  sliding_window_view OOM, falling back to loop")
        for t in range(lookback, n_bars):
            window = bb_width[t - lookback:t, :]
            current = bb_width[t, :]
            n_below = np.nansum(window < current[np.newaxis, :], axis=0)
            n_valid = np.nansum(~np.isnan(window), axis=0)
            pctile = np.where(n_valid > 0, 100.0 * n_below / n_valid, 50.0)
            compressed[t, :] = pctile < pctile_threshold

    n_compressed = np.sum(compressed)
    logger.info("Compressions: %d bar-symbols (%.2f%% of %d total)",
                n_compressed, 100 * n_compressed / (n_bars * n_symbols), n_bars * n_symbols)
    return compressed


def contingency_analysis(compressed, ignition_mask, features, feature_names,
                         n_quantiles=3):
    """Phase 1: For compressed bars, does each derivatives feature predict ignition?

    For each feature:
      1. Select bars where compressed=True AND feature is not NaN
      2. Split into terciles (low/mid/high)
      3. Compute ignition rate per bucket
      4. Chi-squared test
      5. Report lift (top bucket rate / overall base rate)
    """
    results = []

    comp_indices = np.where(compressed)
    n_compressed = len(comp_indices[0])

    if n_compressed < 50:
        logger.warning("Only %d compressed bars — too few for contingency analysis", n_compressed)
        return results

    is_ignited = ignition_mask[comp_indices]

    logger.info("\n" + "=" * 70)
    logger.info("PHASE 1: CONDITIONAL ANALYSIS")
    logger.info("  Question: For compressed bars with derivatives data,")
    logger.info("  does derivatives positioning predict which compressions ignite?")
    logger.info("=" * 70)
    logger.info("Compressed bars with derivatives data: %d", n_compressed)
    logger.info("Of those, ignited (MFH >= 20%%): %d (%.2f%% base rate)",
                np.sum(is_ignited), 100 * np.mean(is_ignited))
    logger.info("")
    logger.info("%-30s  %-45s  %8s  %8s  %8s",
                "Feature", "Tercile rates [Low | Mid | High]", "chi2", "p-value", "lift_top")
    logger.info("-" * 130)

    for fname in feature_names:
        feat_vals = features[fname][comp_indices]
        valid = ~np.isnan(feat_vals)

        if np.sum(valid) < 30:
            logger.info("%-30s  skipped (only %d valid values)", fname, np.sum(valid))
            continue

        vals = feat_vals[valid]
        labels = is_ignited[valid]

        # Split into quantile buckets
        try:
            quantile_edges = np.nanpercentile(vals, np.linspace(0, 100, n_quantiles + 1))
            # Ensure unique edges to avoid empty buckets
            quantile_edges = np.unique(quantile_edges)
            if len(quantile_edges) < 3:
                logger.info("%-30s  skipped (degenerate distribution)", fname)
                continue
            actual_n_q = len(quantile_edges) - 1
            buckets = np.digitize(vals, quantile_edges[1:-1])
        except Exception as e:
            logger.info("%-30s  error: %s", fname, e)
            continue

        # Ignition rate per bucket
        bucket_rates = []
        bucket_counts = []
        for b in range(actual_n_q):
            mask = buckets == b
            n_in = int(np.sum(mask))
            n_ign = int(np.sum(labels[mask])) if n_in > 0 else 0
            rate = n_ign / n_in if n_in > 0 else 0.0
            bucket_rates.append(rate)
            bucket_counts.append((n_in, n_ign))

        # Chi-squared test
        table = np.array([[c[1], c[0] - c[1]] for c in bucket_counts])
        if np.any(table.sum(axis=0) == 0) or np.any(table.sum(axis=1) == 0):
            chi2, p_val = 0.0, 1.0
        else:
            chi2, p_val, dof, expected = chi2_contingency(table)

        # Lift: top bucket rate / overall base rate
        overall_rate = float(np.mean(labels))
        lift_top = bucket_rates[-1] / overall_rate if overall_rate > 0 else 0.0
        lift_bottom = bucket_rates[0] / overall_rate if overall_rate > 0 else 0.0

        result = {
            "feature": fname,
            "n_valid": int(np.sum(valid)),
            "bucket_rates": [float(r) for r in bucket_rates],
            "bucket_counts": [(int(n), int(ig)) for n, ig in bucket_counts],
            "chi2": float(chi2),
            "p_value": float(p_val),
            "lift_top_vs_base": float(lift_top),
            "lift_bottom_vs_base": float(lift_bottom),
            "separation": float(bucket_rates[-1] - bucket_rates[0]),
        }
        results.append(result)

        # Pretty print
        bucket_labels = ["Low", "Mid", "High"][:actual_n_q]
        rates_str = " | ".join(f"{bl}: {r:.1%} ({c[1]}/{c[0]})"
                               for bl, r, c in zip(bucket_labels, bucket_rates, bucket_counts))
        sig = "***" if p_val < 0.01 else "**" if p_val < 0.05 else "*" if p_val < 0.10 else ""
        logger.info("%-30s  %-45s  %8.2f  %8.4f  %8.2f %s",
                    fname, rates_str, chi2, p_val, lift_top, sig)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: IC SCREENING
# ═══════════════════════════════════════════════════════════════════════════════

def ic_screening(close, high, features, feature_names, symbols, horizon=HORIZON,
                 label="30-day"):
    """Phase 2: Rank IC of each feature vs forward max high return.

    IC = Spearman rank correlation between feature value and forward return.
    Pooled across all symbols. Also computes per-symbol IC and reports median.
    """
    n_bars, n_symbols = close.shape

    # Forward max high return (same target as ignition model)
    mfh = compute_max_forward_high(close, high, horizon)

    # Simple forward close return (24-bar)
    fwd_ret = np.full_like(close, np.nan)
    if n_bars > horizon:
        fwd_ret[:-horizon] = close[horizon:] / close[:-horizon] - 1.0

    logger.info("\n" + "=" * 70)
    logger.info("PHASE 2: IC SCREENING (%s window)", label)
    logger.info("  Target 1: Max Forward High %d-bar (ignition detection target)", horizon)
    logger.info("  Target 2: Forward Close Return %d-bar", horizon)
    logger.info("=" * 70)
    logger.info("")
    logger.info("%-35s  %8s  %10s  %8s  %10s  %10s  %8s  [n]",
                "Feature", "IC_mfh", "p_mfh", "IC_ret", "med_symIC", "IC_std", "MI")
    logger.info("-" * 130)

    results = []

    for fname in feature_names:
        feat = features[fname]

        # Pooled IC: flatten all valid (feature, target) pairs
        valid = ~np.isnan(feat) & ~np.isnan(mfh) & ~np.isnan(fwd_ret)
        n_valid = int(np.sum(valid))

        if n_valid < 100:
            logger.info("%-35s  skipped (%d valid observations)", fname, n_valid)
            continue

        feat_flat = feat[valid]
        mfh_flat = mfh[valid].astype(np.float64)
        ret_flat = fwd_ret[valid]

        # Pooled Spearman IC
        ic_mfh, p_mfh = spearmanr(feat_flat, mfh_flat)
        ic_ret, p_ret = spearmanr(feat_flat, ret_flat)

        # Per-symbol IC (median across symbols)
        sym_ics_mfh = []
        sym_ics_ret = []
        for s in range(n_symbols):
            sv = ~np.isnan(feat[:, s]) & ~np.isnan(mfh[:, s]) & ~np.isnan(fwd_ret[:, s])
            if np.sum(sv) < 30:
                continue
            ic_s_mfh, _ = spearmanr(feat[sv, s], mfh[sv, s].astype(np.float64))
            ic_s_ret, _ = spearmanr(feat[sv, s], fwd_ret[sv, s])
            if not np.isnan(ic_s_mfh):
                sym_ics_mfh.append(ic_s_mfh)
            if not np.isnan(ic_s_ret):
                sym_ics_ret.append(ic_s_ret)

        median_ic_mfh = float(np.median(sym_ics_mfh)) if sym_ics_mfh else 0.0
        median_ic_ret = float(np.median(sym_ics_ret)) if sym_ics_ret else 0.0
        ic_std_mfh = float(np.std(sym_ics_mfh)) if len(sym_ics_mfh) > 1 else 0.0

        # Mutual information (binned, nonlinear cross-check)
        mi = _binned_mi(feat_flat, mfh_flat, n_bins=10)

        result = {
            "feature": fname,
            "n_valid": n_valid,
            "n_symbols_tested": len(sym_ics_mfh),
            "pooled_ic_mfh": float(ic_mfh),
            "pooled_ic_mfh_p": float(p_mfh),
            "pooled_ic_ret": float(ic_ret),
            "pooled_ic_ret_p": float(p_ret),
            "median_symbol_ic_mfh": median_ic_mfh,
            "median_symbol_ic_ret": median_ic_ret,
            "ic_cross_symbol_std": ic_std_mfh,
            "mutual_info_mfh": float(mi),
        }
        results.append(result)

        sig_mfh = "***" if p_mfh < 0.001 else "**" if p_mfh < 0.01 else "*" if p_mfh < 0.05 else ""
        logger.info("%-35s  %+8.4f  %10.1e  %+8.4f  %+10.4f  %10.4f  %8.5f  [%dk] %s",
                    fname, ic_mfh, p_mfh, ic_ret, median_ic_mfh, ic_std_mfh, mi,
                    n_valid // 1000, sig_mfh)

    return results


def _binned_mi(x, y, n_bins=10):
    """Compute binned mutual information between x and y.
    Uses equal-frequency bins (quantile bins) to handle skewed distributions.
    """
    try:
        n = len(x)
        if n < n_bins * 2:
            return 0.0

        # Quantile-bin both variables
        x_edges = np.percentile(x, np.linspace(0, 100, n_bins + 1))
        y_edges = np.percentile(y, np.linspace(0, 100, n_bins + 1))
        x_bins = np.clip(np.searchsorted(x_edges[1:-1], x), 0, n_bins - 1)
        y_bins = np.clip(np.searchsorted(y_edges[1:-1], y), 0, n_bins - 1)

        # Joint and marginal counts
        joint = np.zeros((n_bins, n_bins))
        for i in range(n):
            joint[x_bins[i], y_bins[i]] += 1
        joint /= n

        px = joint.sum(axis=1)
        py = joint.sum(axis=0)

        # MI = sum p(x,y) * log(p(x,y) / (p(x)*p(y)))
        mi = 0.0
        for i in range(n_bins):
            for j in range(n_bins):
                if joint[i, j] > 0 and px[i] > 0 and py[j] > 0:
                    mi += joint[i, j] * np.log(joint[i, j] / (px[i] * py[j]))
        return mi
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Derivatives Signal Test — Kill/Confirm Protocol")
    parser.add_argument("--days", type=int, default=45,
                        help="OHLCV lookback days (default: 45 for 30d overlap + warmup)")
    parser.add_argument("--funding-days", type=int, default=400,
                        help="OHLCV lookback for funding-only long-window test")
    parser.add_argument("--phase1-only", action="store_true",
                        help="Run only Phase 1 (contingency analysis)")
    args = parser.parse_args()

    database_url = os.environ["DATABASE_URL"]

    logger.info("=" * 70)
    logger.info("DERIVATIVES SIGNAL TEST — Kill/Confirm Protocol")
    logger.info("=" * 70)
    logger.info("Date: %s", datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    logger.info("")

    t_total = time.time()

    # ══════════════════════════════════════════════════════════════════════════
    # DATA LOADING
    # ══════════════════════════════════════════════════════════════════════════

    # Load OHLCV
    logger.info("Loading OHLCV matrices (%d days)...", args.days)
    close, open_, high, low, volume, ts_grid, symbols, btc_idx = \
        load_ohlcv_matrices(database_url, args.days)
    n_bars, n_symbols = close.shape
    logger.info("OHLCV: %d bars x %d symbols", n_bars, n_symbols)

    # Load 30-day derivatives features
    logger.info("\nLoading 30-day derivatives features...")
    deriv_features, deriv_names = load_derivatives_features(database_url, symbols, ts_grid)

    # Compute targets
    logger.info("\nComputing forward returns and ignition labels...")
    mfh = compute_max_forward_high(close, high, HORIZON)
    events, ignition_mask = build_event_catalog(mfh, IGNITION_THRESHOLD)
    n_ignitions = len(events)
    total_valid = np.sum(~np.isnan(mfh))
    logger.info("Ignition events: %d (%.3f%% base rate, out of %d valid bar-symbols)",
                n_ignitions, 100 * n_ignitions / total_valid if total_valid > 0 else 0, total_valid)

    # Detect compressions
    logger.info("\nDetecting volatility compressions...")
    compressed = detect_compressions(close)

    # Overlap check: how many compressed bars have derivatives data?
    has_deriv = ~np.isnan(deriv_features['oi_value'])  # OI as proxy for "data exists"
    compressed_with_deriv = compressed & has_deriv
    n_overlap = int(np.sum(compressed_with_deriv))
    logger.info("Compressed bars WITH derivatives data: %d (of %d compressed total)",
                n_overlap, np.sum(compressed))

    if n_overlap < 30:
        logger.error("FATAL: Only %d compressed bars have derivatives data. Need >= 30.", n_overlap)
        logger.error("The derivatives window (%d bars) is too short or compression too rare.",
                     int(np.sum(has_deriv.any(axis=1))))
        _write_results({"error": "insufficient_overlap", "n_overlap": n_overlap})
        sys.exit(1)

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1: CONDITIONAL ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════

    phase1_results = contingency_analysis(
        compressed_with_deriv, ignition_mask, deriv_features, deriv_names
    )

    # Phase 1 verdict
    significant_features = [r for r in phase1_results if r["p_value"] < 0.10]
    strong_features = [r for r in phase1_results
                       if r["p_value"] < 0.05 and abs(r["separation"]) > 0.02]

    logger.info("\n" + "-" * 70)
    logger.info("PHASE 1 VERDICT:")
    logger.info("  Features with p < 0.10: %d / %d", len(significant_features), len(phase1_results))
    logger.info("  Features with p < 0.05 AND separation > 2pp: %d", len(strong_features))

    if not significant_features:
        logger.info("  No features show significant separation in contingency analysis.")
        logger.info("  This is not fatal — Phase 2 may reveal signal outside compression context.")
    else:
        for r in sorted(significant_features, key=lambda x: x["p_value"]):
            logger.info("  + %s: separation=%.1f%%, chi2=%.2f, p=%.4f, lift_top=%.2f",
                        r["feature"], 100 * r["separation"], r["chi2"],
                        r["p_value"], r["lift_top_vs_base"])

    if args.phase1_only:
        _write_results({"phase1_conditional": phase1_results})
        return

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2: IC SCREENING (30-day window)
    # ══════════════════════════════════════════════════════════════════════════

    phase2_30d = ic_screening(close, high, deriv_features, deriv_names,
                              symbols, label="30-day")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2b: IC SCREENING (365-day funding-only)
    # ══════════════════════════════════════════════════════════════════════════

    logger.info("\n\nLoading extended OHLCV for funding long-window test (%d days)...",
                args.funding_days)
    close_long, _, high_long, _, _, ts_grid_long, symbols_long, _ = \
        load_ohlcv_matrices(database_url, args.funding_days)
    n_bars_long = close_long.shape[0]
    logger.info("Long OHLCV: %d bars x %d symbols", n_bars_long, close_long.shape[1])

    logger.info("Computing funding features from raw data (long window)...")
    funding_long = load_funding_features_long(database_url, symbols_long, ts_grid_long)

    phase2_funding = ic_screening(close_long, high_long, funding_long,
                                  list(funding_long.keys()), symbols_long,
                                  label="funding-long")

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ══════════════════════════════════════════════════════════════════════════

    all_ic = phase2_30d + phase2_funding

    survivors_ic = [r for r in all_ic
                    if abs(r["pooled_ic_mfh"]) > IC_THRESHOLD and r["pooled_ic_mfh_p"] < 0.01]
    survivors_mi = [r for r in all_ic if r["mutual_info_mfh"] > MI_THRESHOLD]
    survivors = {r["feature"] for r in survivors_ic} | {r["feature"] for r in survivors_mi}

    elapsed = time.time() - t_total

    logger.info("\n" + "=" * 70)
    logger.info("FINAL VERDICT")
    logger.info("=" * 70)
    logger.info("IC threshold: |IC| > %.3f with p < 0.01", IC_THRESHOLD)
    logger.info("MI threshold: MI > %.4f", MI_THRESHOLD)
    logger.info("")
    logger.info("Survivors (IC): %d features", len(survivors_ic))
    for r in sorted(survivors_ic, key=lambda x: -abs(x["pooled_ic_mfh"])):
        logger.info("  + %s: IC=%.4f (p=%.1e), median_sym_IC=%.4f, MI=%.5f",
                    r["feature"], r["pooled_ic_mfh"], r["pooled_ic_mfh_p"],
                    r["median_symbol_ic_mfh"], r["mutual_info_mfh"])

    mi_only = {r["feature"] for r in survivors_mi} - {r["feature"] for r in survivors_ic}
    if mi_only:
        logger.info("Survivors (MI only, not IC): %d", len(mi_only))
        for r in [x for x in survivors_mi if x["feature"] in mi_only]:
            logger.info("  + %s: MI=%.5f, IC=%.4f (p=%.1e)",
                        r["feature"], r["mutual_info_mfh"],
                        r["pooled_ic_mfh"], r["pooled_ic_mfh_p"])

    logger.info("")
    if not survivors:
        logger.info("+--------------------------------------------------------------+")
        logger.info("|  KILL: No derivatives feature survives IC/MI screening.       |")
        logger.info("|  Recommendation: Redirect effort to social pipeline.          |")
        logger.info("|  Keep derivatives collector running (free) for future test.   |")
        logger.info("+--------------------------------------------------------------+")
    else:
        logger.info("+--------------------------------------------------------------+")
        logger.info("|  PROCEED: %d feature(s) survive screening.                   |", len(survivors))
        logger.info("|  Next: Walk-forward validation with surviving features.      |")
        logger.info("+--------------------------------------------------------------+")

    logger.info("\nTotal runtime: %.1fs", elapsed)

    # Write machine-readable results
    _write_results({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_inventory": {
            "ohlcv_bars": n_bars,
            "ohlcv_symbols": n_symbols,
            "ohlcv_long_bars": n_bars_long,
            "ohlcv_long_symbols": close_long.shape[1],
            "ignition_events": n_ignitions,
            "ignition_base_rate_pct": float(100 * n_ignitions / total_valid) if total_valid > 0 else 0,
            "compressed_bars": int(np.sum(compressed)),
            "compressed_with_deriv": n_overlap,
        },
        "phase1_conditional": phase1_results,
        "phase2_30d_ic": [_serialize(r) for r in phase2_30d],
        "phase2_funding_long_ic": [_serialize(r) for r in phase2_funding],
        "verdict": {
            "survivors": sorted(survivors),
            "kill": len(survivors) == 0,
            "ic_threshold": IC_THRESHOLD,
            "mi_threshold": MI_THRESHOLD,
            "n_survivors_ic": len(survivors_ic),
            "n_survivors_mi_only": len(mi_only),
        }
    })


def _serialize(obj):
    """Ensure JSON-serializable."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def _write_results(data):
    out_path = os.path.join(os.path.dirname(__file__), "derivatives_signal_test_results.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("Results written to %s", out_path)


if __name__ == "__main__":
    main()
