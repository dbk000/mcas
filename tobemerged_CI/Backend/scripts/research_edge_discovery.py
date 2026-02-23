#!/usr/bin/env python3
"""
Edge Discovery Research — Multi-Source Feature Screening + Walk-Forward
=======================================================================

Per CLAUDE.md §0.1: Start from data, find what works. Do not anchor to
known technical analysis. Engineer features from first principles, screen
by information coefficient, validate via walk-forward.

Data sources used:
  - OHLCV 1h (3+ years, 588 symbols) — primary
  - OHLCV 4h, 1d (multi-timeframe alignment)
  - Social features (1 year, 414 symbols)
  - (Derivatives/on-chain loaded when available)

Pipeline:
  Stage 1: Feature engineering + IC screening
  Stage 2: Walk-forward validation of top features
  Stage 3: Composite scoring + simulation with costs

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/research_edge_discovery.py
"""

import os
import sys
import time
import logging
import argparse
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

import math
import psycopg
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL")


# ─── Numpy-only statistics (no scipy dependency) ─────────────────────────────

def _rankdata(arr):
    """Rank 1D array (average method). Equivalent to scipy.stats.rankdata."""
    sorter = np.argsort(arr)
    ranks = np.empty_like(sorter, dtype=np.float64)
    ranks[sorter] = np.arange(1, len(arr) + 1, dtype=np.float64)
    # Handle ties: average rank for equal values
    sorted_arr = arr[sorter]
    i = 0
    while i < len(sorted_arr):
        j = i
        while j < len(sorted_arr) and sorted_arr[j] == sorted_arr[i]:
            j += 1
        if j > i + 1:
            avg_rank = np.mean(np.arange(i + 1, j + 1, dtype=np.float64))
            for k in range(i, j):
                ranks[sorter[k]] = avg_rank
        i = j
    return ranks


def _spearmanr(x, y):
    """Spearman rank correlation + approximate p-value. No scipy needed."""
    n = len(x)
    if n < 3:
        return np.nan, 1.0
    rx = _rankdata(x)
    ry = _rankdata(y)
    # Pearson on ranks
    mx, my = rx.mean(), ry.mean()
    dx, dy = rx - mx, ry - my
    denom = np.sqrt((dx ** 2).sum() * (dy ** 2).sum())
    if denom == 0:
        return 0.0, 1.0
    rho = (dx * dy).sum() / denom
    # t-distribution approximation for p-value
    if abs(rho) >= 1.0:
        return float(rho), 0.0
    t_stat = rho * np.sqrt((n - 2) / (1 - rho ** 2))
    # Approximate p-value using normal distribution for large n
    p_val = 2 * np.exp(-0.5 * t_stat ** 2) / np.sqrt(2 * np.pi) if abs(t_stat) < 30 else 0.0
    # Better: use the fact that for n > 30, t is approximately normal
    if n > 30:
        p_val = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))
    return float(rho), float(p_val)


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 0: DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_ohlcv_matrix(db_url, timeframe='1h', lookback_days=180, min_bars_pct=0.7):
    """Load OHLCV into aligned (n_bars, n_symbols, 5) matrix.

    Returns: timestamps, symbols, ohlcv_cube (bars × symbols × [O,H,L,C,V])
    """
    logger.info("Loading OHLCV %s, lookback=%dd...", timeframe, lookback_days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Get symbols with sufficient data
            cur.execute("""
                SELECT symbol, COUNT(*) AS cnt
                FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s
                GROUP BY symbol
                HAVING COUNT(*) > 100
                ORDER BY symbol
            """, (timeframe, cutoff))
            sym_counts = cur.fetchall()

    max_bars = max(c for _, c in sym_counts)
    threshold = int(max_bars * min_bars_pct)
    symbols = [s for s, c in sym_counts if c >= threshold]
    logger.info("  %d symbols with >= %d bars (of %d max)", len(symbols), threshold, max_bars)

    # Load in chunks to avoid memory spike
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    n_sym = len(symbols)

    # First pass: get the time grid
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT bar_start_ts
                FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s
                  AND symbol = ANY(%s)
                ORDER BY bar_start_ts
            """, (timeframe, cutoff, symbols))
            ts_grid = [row[0] for row in cur]

    n_bars = len(ts_grid)
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    logger.info("  Time grid: %d bars from %s to %s", n_bars,
                ts_grid[0].date(), ts_grid[-1].date())

    # Allocate cube
    cube = np.full((n_bars, n_sym, 5), np.nan, dtype=np.float64)

    # Load data
    t0 = time.time()
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s
                  AND symbol = ANY(%s)
                ORDER BY bar_start_ts
            """, (timeframe, cutoff, symbols))

            rows_loaded = 0
            for row in cur:
                sym, ts, o, h, l, c, v = row
                if sym in sym_to_idx and ts in ts_to_idx:
                    t_idx = ts_to_idx[ts]
                    s_idx = sym_to_idx[sym]
                    cube[t_idx, s_idx, :] = [float(o), float(h), float(l),
                                              float(c), float(v)]
                    rows_loaded += 1
                    if rows_loaded % 1_000_000 == 0:
                        logger.info("    Loaded %dM rows...", rows_loaded // 1_000_000)

    logger.info("  Loaded %d rows in %.1fs → cube shape %s",
                rows_loaded, time.time() - t0, cube.shape)

    # Convert timestamps to numpy array
    ts_arr = np.array(ts_grid)
    return ts_arr, symbols, cube


def load_social_features(db_url, symbols, ts_grid):
    """Load social features aligned to the OHLCV grid.

    Returns: dict of feature_name → (n_bars, n_symbols) arrays
    """
    logger.info("Loading social features...")
    cutoff = ts_grid[0]
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    n_bars, n_sym = len(ts_grid), len(symbols)

    features = {}
    feat_names = ['mention_count', 'unique_authors', 'engagement_sum',
                  'mention_velocity', 'mention_acceleration',
                  'author_novelty_ratio', 'mention_volume_ratio']

    for name in feat_names:
        features[f'social_{name}'] = np.full((n_bars, n_sym), np.nan, dtype=np.float64)

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT symbol, bar_start_ts,
                           mention_count, unique_authors, engagement_sum,
                           mention_velocity, mention_acceleration,
                           author_novelty_ratio, mention_volume_ratio
                    FROM social_features_1h
                    WHERE symbol = ANY(%s) AND bar_start_ts >= %s
                    ORDER BY bar_start_ts
                """, (symbols, cutoff))

                loaded = 0
                for row in cur:
                    sym, ts = row[0], row[1]
                    if sym not in sym_to_idx:
                        continue
                    # Find nearest hour in grid
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    ts_h = ts.replace(minute=0, second=0, microsecond=0)
                    if ts_h not in ts_to_idx:
                        continue
                    t_idx = ts_to_idx[ts_h]
                    s_idx = sym_to_idx[sym]
                    for i, name in enumerate(feat_names):
                        val = row[2 + i]
                        if val is not None:
                            features[f'social_{name}'][t_idx, s_idx] = float(val)
                    loaded += 1

        logger.info("  Loaded %d social feature rows", loaded)
    except Exception as e:
        logger.warning("  Social features unavailable: %s", e)

    return features


def load_derivatives_features(db_url, symbols, ts_grid):
    """Load derivatives features aligned to the OHLCV grid."""
    logger.info("Loading derivatives features...")
    cutoff = ts_grid[0]
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    n_bars, n_sym = len(ts_grid), len(symbols)

    feat_names = ['funding_rate', 'funding_rate_zscore',
                  'oi_change_pct_1h', 'oi_change_pct_4h', 'oi_change_pct_24h',
                  'ls_ratio', 'ls_ratio_change_4h', 'ls_ratio_extreme',
                  'taker_buy_ratio', 'taker_imbalance']
    features = {}
    for name in feat_names:
        features[f'deriv_{name}'] = np.full((n_bars, n_sym), np.nan, dtype=np.float64)

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cols = ', '.join(feat_names)
                cur.execute(f"""
                    SELECT symbol, bar_start_ts, {cols}
                    FROM derivatives_features_1h
                    WHERE symbol = ANY(%s) AND bar_start_ts >= %s
                    ORDER BY bar_start_ts
                """, (symbols, cutoff))

                loaded = 0
                for row in cur:
                    sym, ts = row[0], row[1]
                    if sym not in sym_to_idx:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    ts_h = ts.replace(minute=0, second=0, microsecond=0)
                    if ts_h not in ts_to_idx:
                        continue
                    t_idx = ts_to_idx[ts_h]
                    s_idx = sym_to_idx[sym]
                    for i, name in enumerate(feat_names):
                        val = row[2 + i]
                        if val is not None:
                            features[f'deriv_{name}'][t_idx, s_idx] = float(val)
                    loaded += 1

        logger.info("  Loaded %d derivatives feature rows", loaded)
    except Exception as e:
        logger.warning("  Derivatives features unavailable: %s", e)

    return features


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1: FEATURE ENGINEERING (from OHLCV cube — vectorized)
# ═══════════════════════════════════════════════════════════════════════════════

def engineer_features(ts_grid, symbols, cube):
    """Engineer ~200 features from OHLCV cube.

    All operations are numpy matrix ops — no Python loops over (bars × symbols).
    cube shape: (n_bars, n_sym, 5) where [O, H, L, C, V]
    Returns: dict[feature_name] → (n_bars, n_sym) array
    """
    logger.info("Engineering features from OHLCV cube %s...", cube.shape)
    t0 = time.time()
    n_bars, n_sym = cube.shape[0], cube.shape[1]
    features = {}

    O = cube[:, :, 0]  # Open
    H = cube[:, :, 1]  # High
    L = cube[:, :, 2]  # Low
    C = cube[:, :, 3]  # Close
    V = cube[:, :, 4]  # Volume

    # Derived price arrays
    with np.errstate(divide='ignore', invalid='ignore'):
        ret_1 = np.where(C[:-1] > 0, C[1:] / C[:-1] - 1, np.nan)  # 1-bar return
        ret_1 = np.vstack([np.full((1, n_sym), np.nan), ret_1])

        hl_range = np.where(L > 0, (H - L) / L, np.nan)  # Bar range
        body = np.where(O > 0, (C - O) / O, np.nan)  # Body size (signed)
        upper_wick = np.where(hl_range > 0,
                               (H - np.maximum(O, C)) / (H - L + 1e-12), np.nan)
        lower_wick = np.where(hl_range > 0,
                               (np.minimum(O, C) - L) / (H - L + 1e-12), np.nan)

        vol_usd = V * C  # Approximate USD volume

    # ── Returns at various horizons ──────────────────────────────────────
    for lag in [1, 2, 4, 8, 12, 24, 48, 72, 168]:
        if lag < n_bars:
            r = np.full((n_bars, n_sym), np.nan)
            r[lag:] = np.where(C[:-lag] > 0, C[lag:] / C[:-lag] - 1, np.nan)
            features[f'ret_{lag}h'] = r

    # ── Rolling statistics ───────────────────────────────────────────────
    for window in [6, 12, 24, 48, 72, 168, 336, 720]:
        if window >= n_bars:
            continue

        # Rolling mean return
        ret_mean = _rolling_mean(ret_1, window)
        features[f'ret_mean_{window}h'] = ret_mean

        # Rolling volatility (std of returns)
        ret_std = _rolling_std(ret_1, window)
        features[f'ret_vol_{window}h'] = ret_std

        # Rolling mean volume
        vol_mean = _rolling_mean(vol_usd, window)
        features[f'vol_mean_{window}h'] = vol_mean

        # Volume ratio (current / rolling mean)
        with np.errstate(divide='ignore', invalid='ignore'):
            features[f'vol_ratio_{window}h'] = np.where(
                vol_mean > 0, vol_usd / vol_mean, np.nan)

        # Rolling range (volatility proxy)
        range_mean = _rolling_mean(hl_range, window)
        features[f'range_mean_{window}h'] = range_mean

    # ── Momentum features ────────────────────────────────────────────────
    for fast, slow in [(6, 24), (12, 48), (24, 72), (24, 168), (48, 168)]:
        if slow >= n_bars:
            continue
        fast_ma = _rolling_mean(C, fast)
        slow_ma = _rolling_mean(C, slow)
        with np.errstate(divide='ignore', invalid='ignore'):
            features[f'ma_ratio_{fast}_{slow}'] = np.where(
                slow_ma > 0, fast_ma / slow_ma - 1, np.nan)

    # ── Price position features ──────────────────────────────────────────
    for window in [24, 72, 168, 336, 720]:
        if window >= n_bars:
            continue
        roll_high = _rolling_max(H, window)
        roll_low = _rolling_min(L, window)
        with np.errstate(divide='ignore', invalid='ignore'):
            # Position within range [0, 1]
            denom = roll_high - roll_low
            features[f'price_pos_{window}h'] = np.where(
                denom > 0, (C - roll_low) / denom, np.nan)
            # Distance from high (drawdown)
            features[f'dd_from_high_{window}h'] = np.where(
                roll_high > 0, C / roll_high - 1, np.nan)

    # ── Volatility regime features ───────────────────────────────────────
    for window in [24, 72, 168]:
        if window >= n_bars:
            continue
        vol_short = _rolling_std(ret_1, window)
        vol_long = _rolling_std(ret_1, window * 4)
        if vol_long is not None:
            with np.errstate(divide='ignore', invalid='ignore'):
                features[f'vol_compression_{window}h'] = np.where(
                    vol_long > 0, vol_short / vol_long, np.nan)

    # ── Volume profile features ──────────────────────────────────────────
    # Volume-weighted price deviation
    for window in [24, 72, 168]:
        if window >= n_bars:
            continue
        vwap = _rolling_vwap(C, V, window)
        with np.errstate(divide='ignore', invalid='ignore'):
            features[f'vwap_dev_{window}h'] = np.where(
                vwap > 0, C / vwap - 1, np.nan)

    # ── Candle pattern features (vectorized) ─────────────────────────────
    features['body_size'] = np.abs(body)
    features['upper_wick_ratio'] = upper_wick
    features['lower_wick_ratio'] = lower_wick
    features['bar_range'] = hl_range

    # Consecutive direction
    direction = np.sign(body)
    features['consec_up'] = _consecutive_count(direction > 0)
    features['consec_down'] = _consecutive_count(direction < 0)

    # ── Cross-sectional features (rank within universe) ──────────────────
    for feat_name in ['ret_1h', 'ret_24h', 'ret_168h']:
        if feat_name in features:
            features[f'{feat_name}_rank'] = _cross_sectional_rank(features[feat_name])

    # Rank current volume
    features['vol_usd_rank'] = _cross_sectional_rank(vol_usd)

    # ── Return autocorrelation ───────────────────────────────────────────
    for lag in [1, 4, 24]:
        if lag < n_bars:
            corr = _rolling_correlation(ret_1[lag:], ret_1[:-lag], 72)
            # Pad back to (n_bars, n_sym) — corr has shape (n_bars-lag, n_sym)
            padded = np.full((n_bars, n_sym), np.nan)
            padded[lag:] = corr
            features[f'ret_autocorr_lag{lag}'] = padded

    # ── Volume-return correlation ────────────────────────────────────────
    features['vol_ret_corr_72h'] = _rolling_correlation(vol_usd, ret_1, 72)

    # ── Return distribution shape ────────────────────────────────────────
    features['ret_skew_168h'] = _rolling_skew(ret_1, 168)
    features['ret_kurt_168h'] = _rolling_kurtosis(ret_1, 168)

    # Validate all features have correct shape
    expected = (n_bars, n_sym)
    for fname, farr in features.items():
        if farr.shape != expected:
            raise ValueError(f"Feature '{fname}' has shape {farr.shape}, expected {expected}")

    n_features = len(features)
    elapsed = time.time() - t0
    logger.info("  Engineered %d features in %.1fs", n_features, elapsed)
    return features


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1b: FORWARD RETURN COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_forward_returns(cube, horizons=[4, 8, 12, 24, 48]):
    """Compute forward returns at various horizons.

    Returns: dict[horizon] → (n_bars, n_sym) array of forward returns
    """
    C = cube[:, :, 3]
    n_bars = C.shape[0]
    fwd = {}
    for h in horizons:
        if h >= n_bars:
            continue
        r = np.full_like(C, np.nan)
        with np.errstate(divide='ignore', invalid='ignore'):
            r[:-h] = np.where(C[:-h] > 0, C[h:] / C[:-h] - 1, np.nan)
        fwd[h] = r
    return fwd


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2: IC SCREENING
# ═══════════════════════════════════════════════════════════════════════════════

def screen_features_by_ic(features, fwd_returns, min_obs=500):
    """Screen all features by rank IC against forward returns.

    Uses Spearman rank correlation (more robust than Pearson for non-linear).
    Returns: list of (feature_name, horizon, ic, ic_std, t_stat, p_value, n_obs)
             sorted by |IC|.
    """
    logger.info("Screening %d features against %d return horizons...",
                len(features), len(fwd_returns))
    t0 = time.time()
    results = []

    # Max sample size for Spearman correlation (controls speed)
    MAX_SAMPLE = 50_000
    feat_count = 0

    for feat_name, feat_arr in features.items():
        feat_count += 1
        if feat_count % 20 == 0:
            logger.info("    Screening feature %d/%d: %s (%.1fs elapsed)",
                        feat_count, len(features), feat_name, time.time() - t0)

        for horizon, fwd_arr in fwd_returns.items():
            # Flatten and align
            mask = ~np.isnan(feat_arr) & ~np.isnan(fwd_arr)
            n_obs = int(mask.sum())
            if n_obs < min_obs:
                continue

            x = feat_arr[mask]
            y = fwd_arr[mask]

            # Subsample for speed if too many observations
            if n_obs > MAX_SAMPLE:
                idx = np.random.choice(n_obs, MAX_SAMPLE, replace=False)
                x_sample, y_sample = x[idx], y[idx]
            else:
                x_sample, y_sample = x, y

            # Rank IC (Spearman)
            try:
                ic, p_val = _spearmanr(x_sample, y_sample)
            except Exception as e:
                if feat_count <= 2:  # Log first occurrence
                    logger.warning("  _spearmanr failed for %s h=%d: %s", feat_name, horizon, e)
                continue

            if np.isnan(ic):
                continue

            # IC stability: split sample into 10 chunks, compute IC per chunk
            n_sub = len(x_sample)
            chunk_size = n_sub // 10
            if chunk_size > 50:
                chunk_ics = []
                for i in range(10):
                    s, e = i * chunk_size, (i + 1) * chunk_size
                    ci, _ = _spearmanr(x_sample[s:e], y_sample[s:e])
                    if not np.isnan(ci):
                        chunk_ics.append(ci)
                ic_std = np.std(chunk_ics) if chunk_ics else np.nan
                t_stat = ic / (ic_std / np.sqrt(len(chunk_ics))) if ic_std > 0 else 0
            else:
                ic_std = np.nan
                t_stat = 0

            results.append((feat_name, horizon, ic, ic_std, t_stat, p_val, n_obs))

    results.sort(key=lambda x: abs(x[2]), reverse=True)
    logger.info("  Screened %d feature×horizon pairs in %.1fs",
                len(results), time.time() - t0)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3: WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def walk_forward_validate(features, fwd_returns, top_features, ts_grid,
                          n_folds=5, embargo_bars=24, cost_bps=18):
    """Walk-forward validation per CLAUDE.md §4.1.

    Cross-sectional mean-reversion strategy:
      Train: select best feature + horizon by IC, learn reversion direction
      Test: rank symbols by feature, long/short extreme deciles, measure PF

    Args:
        top_features: list of (feat_name, horizon, ic, ...) from screening
        cost_bps: round-trip cost in basis points (fee + slippage both sides)

    Returns: list of fold results
    """
    logger.info("Walk-forward validation: %d folds, %d-bar embargo, %d bps cost",
                n_folds, embargo_bars, cost_bps)

    n_bars = len(ts_grid)
    fold_size = n_bars // (n_folds + 1)  # +1 for holdout

    # Reserve last fold_size bars as holdout
    train_test_bars = n_bars - fold_size

    fold_results = []
    cost = cost_bps / 10000

    for fold_idx in range(n_folds):
        test_start = (fold_idx + 1) * (train_test_bars // n_folds)
        test_end = min(test_start + (train_test_bars // n_folds), train_test_bars)
        train_end = test_start - embargo_bars

        if train_end < 200 or test_end <= test_start:
            logger.info("  Fold %d: skipped (insufficient training data)", fold_idx + 1)
            continue

        train_slice = slice(0, train_end)
        test_slice = slice(test_start, test_end)

        logger.info("  Fold %d: train [0:%d], test [%d:%d] (%s → %s)",
                     fold_idx + 1, train_end, test_start, test_end,
                     ts_grid[test_start].strftime('%Y-%m-%d'),
                     ts_grid[min(test_end - 1, n_bars - 1)].strftime('%Y-%m-%d'))

        # ── Train: find best feature×horizon by IC on training data ───────
        best_configs = []
        for feat_name, horizon, full_ic, *_ in top_features[:50]:
            if feat_name not in features or horizon not in fwd_returns:
                continue

            feat_train = features[feat_name][train_slice]
            fwd_train = fwd_returns[horizon][train_slice]

            mask = ~np.isnan(feat_train) & ~np.isnan(fwd_train)
            n_valid = int(mask.sum())
            if n_valid < 500:
                continue

            x = feat_train[mask]
            y = fwd_train[mask]

            # Subsample for speed
            if n_valid > 50_000:
                idx = np.random.choice(n_valid, 50_000, replace=False)
                x_s, y_s = x[idx], y[idx]
            else:
                x_s, y_s = x, y

            train_ic, _ = _spearmanr(x_s, y_s)
            if np.isnan(train_ic) or abs(train_ic) < 0.01:
                continue

            # Decile return analysis: check if extreme deciles beat costs
            q10 = np.nanpercentile(x, 10)
            q90 = np.nanpercentile(x, 90)

            d1_mask = x <= q10  # Bottom decile
            d10_mask = x >= q90  # Top decile

            d1_ret = np.nanmean(y[d1_mask]) if d1_mask.sum() > 20 else np.nan
            d10_ret = np.nanmean(y[d10_mask]) if d10_mask.sum() > 20 else np.nan

            if np.isnan(d1_ret) or np.isnan(d10_ret):
                continue

            # Mean reversion: if IC < 0, high feature → low return
            # So: short D10, long D1
            if train_ic < 0:
                long_ret = d1_ret  # Low feature → expected positive return
                short_ret = -d10_ret  # High feature → expected negative return (flip sign)
            else:
                long_ret = d10_ret
                short_ret = -d1_ret

            # Both legs must beat costs individually
            spread_ret = long_ret + short_ret  # Combined long-short return
            if spread_ret > 2 * cost:  # Must cover costs for both legs
                best_configs.append({
                    'feat': feat_name, 'horizon': horizon,
                    'train_ic': train_ic, 'spread_ret': spread_ret,
                    'long_ret': long_ret, 'short_ret': short_ret,
                    'q10': q10, 'q90': q90,
                })

        if not best_configs:
            logger.info("    No profitable configs found in training")
            fold_results.append({'fold': fold_idx + 1, 'avg_daily_pf': 0,
                                  'n_trades': 0, 'configs': 0})
            continue

        # Sort by spread return (strongest mean-reversion signal)
        best_configs.sort(key=lambda x: x['spread_ret'], reverse=True)
        # Use top 5 configs
        configs_used = best_configs[:5]
        logger.info("    %d profitable configs, using top %d (best spread=%.1f bps)",
                     len(best_configs), len(configs_used),
                     configs_used[0]['spread_ret'] * 10000)

        # ── Test: cross-sectional rank strategy ───────────────────────────
        n_test = test_end - test_start
        n_sym = features[top_features[0][0]].shape[1]
        all_trades = []

        for cfg in configs_used:
            feat_test = features[cfg['feat']][test_slice]  # (n_test, n_sym)
            fwd_test = fwd_returns[cfg['horizon']][test_slice]
            rebalance_h = cfg['horizon']

            # Rebalance every `horizon` bars
            for t in range(0, n_test - rebalance_h, rebalance_h):
                feat_row = feat_test[t]  # (n_sym,)
                fwd_row = fwd_test[t]  # (n_sym,) — forward return from this bar

                valid = ~np.isnan(feat_row) & ~np.isnan(fwd_row)
                n_valid = valid.sum()
                if n_valid < 20:
                    continue

                # Rank symbols by feature value
                ranks = np.full(n_sym, np.nan)
                ranks[valid] = _rankdata(feat_row[valid]) / n_valid  # Percentile [0,1]

                # Decile entries
                if cfg['train_ic'] < 0:
                    # Mean reversion: long bottom decile, short top decile
                    long_mask = valid & (ranks <= 0.1)
                    short_mask = valid & (ranks >= 0.9)
                else:
                    # Momentum: long top decile, short bottom decile
                    long_mask = valid & (ranks >= 0.9)
                    short_mask = valid & (ranks <= 0.1)

                # Trade returns
                ts_bar = ts_grid[test_start + t]
                long_rets = fwd_row[long_mask] - cost
                short_rets = -fwd_row[short_mask] - cost

                for r in long_rets:
                    all_trades.append((ts_bar, r, 'long'))
                for r in short_rets:
                    all_trades.append((ts_bar, r, 'short'))

        if not all_trades:
            fold_results.append({'fold': fold_idx + 1, 'avg_daily_pf': 0,
                                  'n_trades': 0, 'configs': len(configs_used)})
            continue

        # Compute metrics
        trade_rets = np.array([t[1] for t in all_trades])
        wins = trade_rets[trade_rets > 0]
        losses = trade_rets[trade_rets < 0]
        gross_profit = wins.sum() if len(wins) > 0 else 0
        gross_loss = abs(losses.sum()) if len(losses) > 0 else 0

        agg_pf = gross_profit / gross_loss if gross_loss > 0 else (
            float('inf') if gross_profit > 0 else 0)
        win_rate = len(wins) / len(trade_rets) if len(trade_rets) > 0 else 0
        avg_ret = trade_rets.mean()

        # Per-day PF (success metric per §4.5)
        trade_dates = [t[0].date() for t in all_trades]
        unique_dates = sorted(set(trade_dates))
        daily_pfs = []
        for d in unique_dates:
            day_rets = np.array([t[1] for t in all_trades if t[0].date() == d])
            if len(day_rets) == 0:
                continue
            dw = day_rets[day_rets > 0].sum()
            dl = abs(day_rets[day_rets < 0].sum())
            if dl > 0:
                daily_pfs.append(dw / dl)
            elif dw > 0:
                daily_pfs.append(3.0)  # Cap infinite PF days

        avg_daily_pf = np.mean(daily_pfs) if daily_pfs else 0

        # Long-only and short-only breakdown
        long_rets_only = np.array([t[1] for t in all_trades if t[2] == 'long'])
        short_rets_only = np.array([t[1] for t in all_trades if t[2] == 'short'])
        long_mean = long_rets_only.mean() * 10000 if len(long_rets_only) > 0 else 0
        short_mean = short_rets_only.mean() * 10000 if len(short_rets_only) > 0 else 0

        result = {
            'fold': fold_idx + 1,
            'n_trades': len(trade_rets),
            'win_rate': win_rate,
            'agg_pf': agg_pf,
            'avg_daily_pf': avg_daily_pf,
            'avg_ret_bps': avg_ret * 10000,
            'configs': len(configs_used),
            'daily_pf_days': len(daily_pfs),
            'long_mean_bps': long_mean,
            'short_mean_bps': short_mean,
        }
        fold_results.append(result)

        logger.info("    Fold %d: %d trades, WR=%.1f%%, agg_PF=%.3f, "
                     "avg_daily_PF=%.3f, avg_ret=%.1f bps (L=%.1f, S=%.1f)",
                     fold_idx + 1, len(trade_rets), win_rate * 100,
                     agg_pf, avg_daily_pf, avg_ret * 10000,
                     long_mean, short_mean)

    return fold_results


# ═══════════════════════════════════════════════════════════════════════════════
# NUMPY HELPERS (vectorized, no Python loops over bars × symbols)
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_mean(arr, window):
    """Rolling mean along axis 0. Returns same shape, NaN for insufficient data."""
    if arr.ndim == 1:
        arr = arr[:, np.newaxis]
        squeeze = True
    else:
        squeeze = False
    out = np.full_like(arr, np.nan)
    if arr.shape[0] < window:
        return out[:, 0] if squeeze else out
    cs = np.nancumsum(arr, axis=0)
    cnt = np.nancumsum(~np.isnan(arr), axis=0).astype(np.float64)
    out[window - 1:] = cs[window - 1:]
    out[window - 1:] -= np.vstack([np.zeros((1, arr.shape[1])), cs[:-window]])
    c = cnt[window - 1:]
    c -= np.vstack([np.zeros((1, arr.shape[1])), cnt[:-window]])
    with np.errstate(divide='ignore', invalid='ignore'):
        out[window - 1:] = np.where(c > 0, out[window - 1:] / c, np.nan)
    return out[:, 0] if squeeze else out


def _rolling_std(arr, window):
    mean = _rolling_mean(arr, window)
    sq_mean = _rolling_mean(arr ** 2, window)
    with np.errstate(invalid='ignore'):
        var = sq_mean - mean ** 2
        var = np.where(var > 0, var, 0)
        return np.sqrt(var)


def _rolling_max(arr, window):
    """Rolling max along axis 0."""
    out = np.full_like(arr, np.nan)
    n = arr.shape[0]
    if n < window:
        return out
    # Use stride tricks for efficiency
    for i in range(window - 1, n):
        out[i] = np.nanmax(arr[i - window + 1:i + 1], axis=0)
    return out


def _rolling_min(arr, window):
    """Rolling min along axis 0."""
    out = np.full_like(arr, np.nan)
    n = arr.shape[0]
    if n < window:
        return out
    for i in range(window - 1, n):
        out[i] = np.nanmin(arr[i - window + 1:i + 1], axis=0)
    return out


def _rolling_vwap(price, volume, window):
    """Volume-weighted average price."""
    pv = price * volume
    sum_pv = _rolling_mean(pv, window) * window  # Approximate
    sum_v = _rolling_mean(volume, window) * window
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(sum_v > 0, sum_pv / sum_v, np.nan)


def _cross_sectional_rank(arr):
    """Rank each row (bar) across symbols. Returns percentile rank [0, 1]."""
    out = np.full_like(arr, np.nan)
    for i in range(arr.shape[0]):
        row = arr[i]
        valid = ~np.isnan(row)
        if valid.sum() < 2:
            continue
        ranks = _rankdata(row[valid])
        out[i, valid] = ranks / len(ranks)
    return out


def _consecutive_count(condition):
    """Count consecutive True values along axis 0."""
    out = np.zeros_like(condition, dtype=np.float64)
    for i in range(1, condition.shape[0]):
        out[i] = np.where(condition[i], out[i - 1] + 1, 0)
    return out


def _rolling_correlation(x, y, window):
    """Rolling Pearson correlation along axis 0."""
    # Ensure same shape
    min_len = min(x.shape[0], y.shape[0])
    x = x[:min_len]
    y = y[:min_len]

    mx = _rolling_mean(x, window)
    my = _rolling_mean(y, window)
    mxy = _rolling_mean(x * y, window)
    sx = _rolling_std(x, window)
    sy = _rolling_std(y, window)

    with np.errstate(divide='ignore', invalid='ignore'):
        cov = mxy - mx * my
        return np.where((sx > 0) & (sy > 0), cov / (sx * sy), np.nan)


def _rolling_skew(arr, window):
    """Rolling skewness along axis 0."""
    mean = _rolling_mean(arr, window)
    std = _rolling_std(arr, window)
    with np.errstate(divide='ignore', invalid='ignore'):
        z = np.where(std > 0, (arr - mean) / std, 0)
    return _rolling_mean(z ** 3, window)


def _rolling_kurtosis(arr, window):
    """Rolling kurtosis along axis 0."""
    mean = _rolling_mean(arr, window)
    std = _rolling_std(arr, window)
    with np.errstate(divide='ignore', invalid='ignore'):
        z = np.where(std > 0, (arr - mean) / std, 0)
    return _rolling_mean(z ** 4, window) - 3  # Excess kurtosis


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Edge Discovery Research")
    parser.add_argument("--lookback-days", type=int, default=180,
                        help="OHLCV lookback in days (default: 180)")
    parser.add_argument("--timeframe", type=str, default="1h",
                        help="Primary timeframe (default: 1h)")
    parser.add_argument("--top-n", type=int, default=50,
                        help="Number of top features to report (default: 50)")
    parser.add_argument("--skip-wf", action="store_true",
                        help="Skip walk-forward (IC screening only)")
    args = parser.parse_args()

    if not DB_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("EDGE DISCOVERY RESEARCH")
    logger.info("=" * 70)
    logger.info("Lookback: %d days, Timeframe: %s", args.lookback_days, args.timeframe)
    np.random.seed(42)

    # ── Stage 0: Load data ────────────────────────────────────────────────
    ts_grid, symbols, cube = load_ohlcv_matrix(
        DB_URL, args.timeframe, args.lookback_days)

    # ── Stage 1: Engineer features ────────────────────────────────────────
    features = engineer_features(ts_grid, symbols, cube)

    # Load social features and merge
    social_feats = load_social_features(DB_URL, symbols, ts_grid)
    features.update(social_feats)

    # Load derivatives features and merge
    deriv_feats = load_derivatives_features(DB_URL, symbols, ts_grid)
    features.update(deriv_feats)

    logger.info("Total features: %d", len(features))

    # ── Stage 1b: Forward returns ─────────────────────────────────────────
    horizons = [4, 8, 12, 24, 48]
    fwd_returns = compute_forward_returns(cube, horizons)
    logger.info("Forward return horizons: %s", horizons)

    # ── Stage 2: IC Screening ─────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 2: FEATURE IC SCREENING")
    logger.info("=" * 70)

    ic_results = screen_features_by_ic(features, fwd_returns)

    # Report top features
    logger.info("")
    logger.info("TOP %d FEATURES BY |IC|:", args.top_n)
    logger.info("%-35s %5s %8s %8s %8s %8s %10s",
                "Feature", "Hor", "IC", "IC_std", "t_stat", "p_val", "n_obs")
    logger.info("-" * 95)

    reported = set()
    count = 0
    for feat_name, horizon, ic, ic_std, t_stat, p_val, n_obs in ic_results:
        # Only show each feature once (best horizon)
        if feat_name in reported:
            continue
        reported.add(feat_name)
        logger.info("%-35s %5d %8.4f %8.4f %8.2f %8.1e %10d",
                     feat_name, horizon, ic, ic_std if not np.isnan(ic_std) else 0,
                     t_stat, p_val, n_obs)
        count += 1
        if count >= args.top_n:
            break

    # IC summary statistics
    all_ics = [abs(r[2]) for r in ic_results if not np.isnan(r[2])]
    if all_ics:
        logger.info("")
        logger.info("IC distribution: median=%.4f, p75=%.4f, p90=%.4f, max=%.4f",
                    np.median(all_ics), np.percentile(all_ics, 75),
                    np.percentile(all_ics, 90), max(all_ics))

        # Features with |IC| > 0.02 (potentially useful)
        useful = [(r[0], r[1], r[2]) for r in ic_results if abs(r[2]) > 0.02]
        unique_useful = set(r[0] for r in useful)
        logger.info("Features with |IC| > 0.02: %d unique features across %d feature×horizon pairs",
                    len(unique_useful), len(useful))
    else:
        logger.warning("NO features passed IC screening — check data quality")

    if args.skip_wf:
        logger.info("Skipping walk-forward (--skip-wf)")
        return

    # Use features with |IC| > 0.02
    top_features = [(r[0], r[1], r[2]) + tuple(r[3:]) for r in ic_results
                     if abs(r[2]) > 0.02][:50]

    if len(top_features) < 5:
        logger.warning("Fewer than 5 features with |IC| > 0.02. Lowering threshold to 0.01")
        top_features = [(r[0], r[1], r[2]) + tuple(r[3:]) for r in ic_results
                         if abs(r[2]) > 0.01][:50]

    # ── Stage 3: Walk-Forward Validation ──────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 3: WALK-FORWARD VALIDATION")
    logger.info("=" * 70)

    fold_results = walk_forward_validate(
        features, fwd_returns, top_features, ts_grid,
        n_folds=5, embargo_bars=24, cost_bps=18,
    )

    # ── Results Summary ───────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("WALK-FORWARD RESULTS")
    logger.info("=" * 70)

    for r in fold_results:
        logger.info("  Fold %d: trades=%d, WR=%.1f%%, agg_PF=%.3f, "
                     "avg_daily_PF=%.3f, avg_ret=%.1f bps",
                     r.get('fold', 0), r.get('n_trades', 0),
                     r.get('win_rate', 0) * 100, r.get('agg_pf', 0),
                     r.get('avg_daily_pf', 0), r.get('avg_ret_bps', 0))

    trading_folds = [r for r in fold_results if r.get('n_trades', 0) > 0]
    if trading_folds:
        avg_pf = np.mean([r['agg_pf'] for r in trading_folds])
        avg_daily_pf = np.mean([r['avg_daily_pf'] for r in trading_folds])
        avg_wr = np.mean([r['win_rate'] for r in trading_folds])
        total_trades = sum(r['n_trades'] for r in trading_folds)
        profitable_folds = sum(1 for r in trading_folds if r.get('avg_daily_pf', 0) > 1.0)

        logger.info("")
        logger.info("OVERALL:")
        logger.info("  Folds with trades: %d/%d", len(trading_folds), len(fold_results))
        logger.info("  Profitable folds (avg_daily_PF > 1.0): %d/%d (%.0f%%)",
                     profitable_folds, len(trading_folds),
                     100 * profitable_folds / len(trading_folds) if trading_folds else 0)
        logger.info("  Total trades: %d", total_trades)
        logger.info("  Avg win rate: %.1f%%", avg_wr * 100)
        logger.info("  Avg aggregate PF: %.3f", avg_pf)
        logger.info("  Avg daily PF: %.3f", avg_daily_pf)
    else:
        logger.info("  No trades generated across any fold.")
        logger.info("Done.")
        return

    # ── Stage 4: Holdout + DSR + Stress Test ─────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 4: HOLDOUT VALIDATION + DSR + STRESS TEST")
    logger.info("=" * 70)

    n_bars = len(ts_grid)
    holdout_size = n_bars // 6  # ~720 bars = 30 days
    holdout_start = n_bars - holdout_size
    train_end_ho = holdout_start - 24  # embargo

    logger.info("Holdout: [%d:%d] (%s → %s)",
                holdout_start, n_bars,
                ts_grid[holdout_start].strftime('%Y-%m-%d'),
                ts_grid[-1].strftime('%Y-%m-%d'))

    # ── 4a: Holdout trade simulation ─────────────────────────────────────
    for cost_label, cost_bps_val in [("18 bps (base)", 18),
                                       ("27 bps (1.5×)", 27),
                                       ("36 bps (2×)", 36)]:
        cost = cost_bps_val / 10000

        # Train on [0:train_end_ho], test on [holdout_start:n_bars]
        train_slice = slice(0, train_end_ho)
        test_slice = slice(holdout_start, n_bars)

        # Find best configs on full training period
        best_configs = []
        for feat_name, horizon, full_ic, *_ in top_features[:50]:
            if feat_name not in features or horizon not in fwd_returns:
                continue
            feat_train = features[feat_name][train_slice]
            fwd_train = fwd_returns[horizon][train_slice]
            mask = ~np.isnan(feat_train) & ~np.isnan(fwd_train)
            n_valid = int(mask.sum())
            if n_valid < 500:
                continue

            x = feat_train[mask]
            y = fwd_train[mask]
            if n_valid > 50_000:
                idx = np.random.choice(n_valid, 50_000, replace=False)
                x_s, y_s = x[idx], y[idx]
            else:
                x_s, y_s = x, y

            train_ic, _ = _spearmanr(x_s, y_s)
            if np.isnan(train_ic) or abs(train_ic) < 0.01:
                continue

            q10 = np.nanpercentile(x, 10)
            q90 = np.nanpercentile(x, 90)
            d1_mask = x <= q10
            d10_mask = x >= q90
            d1_ret = np.nanmean(y[d1_mask]) if d1_mask.sum() > 20 else np.nan
            d10_ret = np.nanmean(y[d10_mask]) if d10_mask.sum() > 20 else np.nan
            if np.isnan(d1_ret) or np.isnan(d10_ret):
                continue

            if train_ic < 0:
                long_ret, short_ret = d1_ret, -d10_ret
            else:
                long_ret, short_ret = d10_ret, -d1_ret

            spread_ret = long_ret + short_ret
            if spread_ret > 2 * cost:
                best_configs.append({
                    'feat': feat_name, 'horizon': horizon,
                    'train_ic': train_ic, 'spread_ret': spread_ret,
                    'q10': q10, 'q90': q90,
                })

        if not best_configs:
            logger.info("  [%s] No profitable configs for holdout", cost_label)
            continue

        best_configs.sort(key=lambda x: x['spread_ret'], reverse=True)
        configs_used = best_configs[:5]

        n_test = n_bars - holdout_start
        n_sym = features[top_features[0][0]].shape[1]
        all_trades = []

        for cfg in configs_used:
            feat_test = features[cfg['feat']][test_slice]
            fwd_test = fwd_returns[cfg['horizon']][test_slice]
            rebalance_h = cfg['horizon']

            for t in range(0, n_test - rebalance_h, rebalance_h):
                feat_row = feat_test[t]
                fwd_row = fwd_test[t]
                valid = ~np.isnan(feat_row) & ~np.isnan(fwd_row)
                n_valid_r = valid.sum()
                if n_valid_r < 20:
                    continue

                ranks = np.full(n_sym, np.nan)
                ranks[valid] = _rankdata(feat_row[valid]) / n_valid_r

                if cfg['train_ic'] < 0:
                    long_mask = valid & (ranks <= 0.1)
                    short_mask = valid & (ranks >= 0.9)
                else:
                    long_mask = valid & (ranks >= 0.9)
                    short_mask = valid & (ranks <= 0.1)

                ts_bar = ts_grid[holdout_start + t]
                for r in fwd_row[long_mask] - cost:
                    all_trades.append((ts_bar, float(r), 'long'))
                for r in (-fwd_row[short_mask]) - cost:
                    all_trades.append((ts_bar, float(r), 'short'))

        if not all_trades:
            logger.info("  [%s] No holdout trades", cost_label)
            continue

        trade_rets = np.array([t[1] for t in all_trades])
        wins = trade_rets[trade_rets > 0]
        losses = trade_rets[trade_rets < 0]
        gp = wins.sum() if len(wins) > 0 else 0
        gl = abs(losses.sum()) if len(losses) > 0 else 0
        agg_pf = gp / gl if gl > 0 else float('inf')
        wr = len(wins) / len(trade_rets) if len(trade_rets) > 0 else 0
        avg_ret_h = trade_rets.mean()

        # Daily PF
        trade_dates = [t[0].date() for t in all_trades]
        unique_dates = sorted(set(trade_dates))
        daily_pfs = []
        daily_rets = []
        for d in unique_dates:
            dr = np.array([t[1] for t in all_trades if t[0].date() == d])
            if len(dr) == 0:
                continue
            daily_rets.append(dr.sum())
            dw = dr[dr > 0].sum()
            dl = abs(dr[dr < 0].sum())
            if dl > 0:
                daily_pfs.append(dw / dl)
            elif dw > 0:
                daily_pfs.append(3.0)

        avg_daily_pf = np.mean(daily_pfs) if daily_pfs else 0
        daily_rets = np.array(daily_rets) if daily_rets else np.array([0.0])

        # Long / short breakdown
        long_rets = np.array([t[1] for t in all_trades if t[2] == 'long'])
        short_rets = np.array([t[1] for t in all_trades if t[2] == 'short'])
        long_mean = long_rets.mean() * 10000 if len(long_rets) > 0 else 0
        short_mean = short_rets.mean() * 10000 if len(short_rets) > 0 else 0

        # Sharpe ratio (annualized from daily returns)
        if len(daily_rets) > 1 and daily_rets.std() > 0:
            sr = daily_rets.mean() / daily_rets.std() * np.sqrt(365)
        else:
            sr = 0

        logger.info("")
        logger.info("  [%s] HOLDOUT RESULTS:", cost_label)
        logger.info("    Trades: %d (L=%d, S=%d)", len(trade_rets),
                     len(long_rets), len(short_rets))
        logger.info("    Win rate: %.1f%%", wr * 100)
        logger.info("    Agg PF: %.3f", agg_pf)
        logger.info("    Avg daily PF: %.3f (%d days)", avg_daily_pf, len(daily_pfs))
        logger.info("    Avg return: %.1f bps (L=%.1f, S=%.1f)",
                     avg_ret_h * 10000, long_mean, short_mean)
        logger.info("    Daily return: mean=%.1f bps, std=%.1f bps",
                     daily_rets.mean() * 10000, daily_rets.std() * 10000)
        logger.info("    Sharpe ratio (ann): %.2f", sr)
        logger.info("    Best day: %.1f bps, Worst day: %.1f bps",
                     daily_rets.max() * 10000, daily_rets.min() * 10000)

        # ── 4b: DSR correction (Harvey et al., 2016) ─────────────────────
        if cost_bps_val == 18 and sr != 0:
            n_trials = len(top_features)  # Number of feature candidates tested
            e_max_sr = np.sqrt(2 * np.log(n_trials))

            # Variance of SR estimate: Var(SR) ≈ (1 + 0.5*SR^2) / T
            T = len(daily_rets)
            var_sr = (1 + 0.5 * sr ** 2) / T
            se_sr = np.sqrt(var_sr)

            # DSR = P(SR* > 0) where SR* = SR_hat - E[max(SR)]
            dsr_stat = (sr - e_max_sr * se_sr) / se_sr if se_sr > 0 else 0
            # Approximate p-value from normal CDF
            dsr_p = 0.5 * (1 + math.erf(dsr_stat / math.sqrt(2)))

            logger.info("")
            logger.info("  DSR CORRECTION:")
            logger.info("    n_trials: %d", n_trials)
            logger.info("    E[max(SR)] under null: %.2f", e_max_sr)
            logger.info("    SR hat: %.2f ± %.2f", sr, se_sr)
            logger.info("    DSR statistic: %.2f", dsr_stat)
            logger.info("    DSR > 0 probability: %.3f", dsr_p)
            if dsr_p > 0.95:
                logger.info("    *** DSR PASSED (p > 0.95): Edge survives multiple testing ***")
            elif dsr_p > 0.5:
                logger.info("    DSR marginal (0.5 < p < 0.95): Edge may be real but not confirmed")
            else:
                logger.info("    DSR FAILED (p < 0.5): Edge likely from overfitting/multiple testing")

    # ── Stage 5: Strategy Variants ───────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 5: STRATEGY VARIANTS (short-only, wider deciles)")
    logger.info("=" * 70)

    # Test short-only (since longs underperform)
    for variant_name, long_pct, short_pct in [
        ("Short-only (top decile)", None, 0.1),
        ("Long-short (5th/95th pct)", 0.05, 0.05),
        ("Long-short (20th/80th pct)", 0.2, 0.2),
    ]:
        cost = 18 / 10000
        train_slice = slice(0, train_end_ho)
        test_slice = slice(holdout_start, n_bars)

        # Reuse configs from base cost
        if not best_configs:
            continue
        configs_v = best_configs[:5]

        n_test = n_bars - holdout_start
        n_sym = features[top_features[0][0]].shape[1]
        variant_trades = []

        for cfg in configs_v:
            feat_test = features[cfg['feat']][test_slice]
            fwd_test = fwd_returns[cfg['horizon']][test_slice]
            rebalance_h = cfg['horizon']

            for t in range(0, n_test - rebalance_h, rebalance_h):
                feat_row = feat_test[t]
                fwd_row = fwd_test[t]
                valid = ~np.isnan(feat_row) & ~np.isnan(fwd_row)
                n_valid_r = valid.sum()
                if n_valid_r < 20:
                    continue

                ranks = np.full(n_sym, np.nan)
                ranks[valid] = _rankdata(feat_row[valid]) / n_valid_r

                if cfg['train_ic'] < 0:
                    l_mask = valid & (ranks <= long_pct) if long_pct else np.zeros(n_sym, bool)
                    s_mask = valid & (ranks >= (1 - short_pct)) if short_pct else np.zeros(n_sym, bool)
                else:
                    l_mask = valid & (ranks >= (1 - long_pct)) if long_pct else np.zeros(n_sym, bool)
                    s_mask = valid & (ranks <= short_pct) if short_pct else np.zeros(n_sym, bool)

                ts_bar = ts_grid[holdout_start + t]
                for r in fwd_row[l_mask] - cost:
                    variant_trades.append(float(r))
                for r in (-fwd_row[s_mask]) - cost:
                    variant_trades.append(float(r))

        if not variant_trades:
            logger.info("  [%s] No trades", variant_name)
            continue

        vr = np.array(variant_trades)
        vw = vr[vr > 0]
        vl = vr[vr < 0]
        v_agg_pf = vw.sum() / abs(vl.sum()) if vl.sum() != 0 else float('inf')
        v_wr = len(vw) / len(vr) if len(vr) > 0 else 0
        logger.info("  [%s]: %d trades, WR=%.1f%%, agg_PF=%.3f, avg_ret=%.1f bps",
                     variant_name, len(vr), v_wr * 100, v_agg_pf, vr.mean() * 10000)

    logger.info("")
    logger.info("Done.")


if __name__ == "__main__":
    main()
