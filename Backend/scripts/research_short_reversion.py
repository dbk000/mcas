#!/usr/bin/env python3
"""
Short-Only Mean-Reversion Deep Dive
====================================

Follows from edge discovery: short-only top-decile mean reversion showed
PF=1.827, WR=59.2% on holdout. This script:

1. Tests each feature individually for short-only performance
2. Tests multiple horizons (4h, 8h, 12h, 24h, 48h)
3. Walk-forward validation with proper train/test separation
4. Holdout with DSR correction
5. Per-symbol, per-day analysis
6. Drawdown and equity curve
7. Feature combination (ensemble of best features)
8. Cost sensitivity (18, 27, 36 bps)

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker python3 -u /scripts/research_short_reversion.py
"""

import os
import sys
import time
import math
import logging
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

import psycopg
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL")


# ── Import helpers from edge_discovery ──────────────────────────────────────
# Instead of duplicating, import the data loading and feature engineering
# We'll copy just the critical numpy helper functions inline

def _rankdata(arr):
    """Rank 1D array (average method)."""
    sorter = np.argsort(arr)
    ranks = np.empty_like(sorter, dtype=np.float64)
    ranks[sorter] = np.arange(1, len(arr) + 1, dtype=np.float64)
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
    """Spearman rank correlation + approximate p-value."""
    n = len(x)
    if n < 3:
        return np.nan, 1.0
    rx = _rankdata(x)
    ry = _rankdata(y)
    mx, my = rx.mean(), ry.mean()
    dx, dy = rx - mx, ry - my
    denom = np.sqrt((dx ** 2).sum() * (dy ** 2).sum())
    if denom == 0:
        return 0.0, 1.0
    rho = (dx * dy).sum() / denom
    if abs(rho) >= 1.0:
        return float(rho), 0.0
    t_stat = rho * np.sqrt((n - 2) / (1 - rho ** 2))
    p_val = 2 * np.exp(-0.5 * t_stat ** 2) / np.sqrt(2 * np.pi) if abs(t_stat) < 30 else 0.0
    if n > 30:
        p_val = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))
    return float(rho), float(p_val)


def _rolling_mean(arr, window):
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
    out = np.full_like(arr, np.nan)
    n = arr.shape[0]
    if n < window:
        return out
    for i in range(window - 1, n):
        out[i] = np.nanmax(arr[i - window + 1:i + 1], axis=0)
    return out


def _rolling_min(arr, window):
    out = np.full_like(arr, np.nan)
    n = arr.shape[0]
    if n < window:
        return out
    for i in range(window - 1, n):
        out[i] = np.nanmin(arr[i - window + 1:i + 1], axis=0)
    return out


def _rolling_vwap(price, volume, window):
    pv = price * volume
    sum_pv = _rolling_mean(pv, window) * window
    sum_v = _rolling_mean(volume, window) * window
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(sum_v > 0, sum_pv / sum_v, np.nan)


def _cross_sectional_rank(arr):
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
    out = np.zeros_like(condition, dtype=np.float64)
    for i in range(1, condition.shape[0]):
        out[i] = np.where(condition[i], out[i - 1] + 1, 0)
    return out


def _rolling_correlation(x, y, window):
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
    mean = _rolling_mean(arr, window)
    std = _rolling_std(arr, window)
    with np.errstate(divide='ignore', invalid='ignore'):
        z = np.where(std > 0, (arr - mean) / std, 0)
    return _rolling_mean(z ** 3, window)


def _rolling_kurtosis(arr, window):
    mean = _rolling_mean(arr, window)
    std = _rolling_std(arr, window)
    with np.errstate(divide='ignore', invalid='ignore'):
        z = np.where(std > 0, (arr - mean) / std, 0)
    return _rolling_mean(z ** 4, window) - 3


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING (copied from edge_discovery to avoid import issues)
# ═══════════════════════════════════════════════════════════════════════════════

def load_ohlcv_matrix(db_url, timeframe, lookback_days, min_bar_pct=0.74):
    """Load OHLCV into a (n_bars, n_symbols, 5) numpy cube."""
    logger.info("Loading OHLCV %s, lookback=%dd...", timeframe, lookback_days)
    t0 = time.time()
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s
            """, (timeframe, cutoff))
            all_symbols = [r[0] for r in cur.fetchall()]

            cur.execute("""
                SELECT symbol, COUNT(*) as cnt FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s
                GROUP BY symbol ORDER BY cnt DESC
            """, (timeframe, cutoff))
            sym_counts = cur.fetchall()

    max_bars = sym_counts[0][1] if sym_counts else 0
    min_bars = int(max_bars * min_bar_pct)
    symbols = [s for s, c in sym_counts if c >= min_bars]
    logger.info("  %d symbols with >= %d bars (of %d max)", len(symbols), min_bars, max_bars)

    tf_hours = {'1m': 1/60, '5m': 5/60, '15m': 0.25, '1h': 1, '4h': 4, '1d': 24}
    hours = tf_hours.get(timeframe, 1)
    n_bars = int(lookback_days * 24 / hours)

    ts_end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ts_grid = [ts_end - timedelta(hours=hours * (n_bars - 1 - i)) for i in range(n_bars)]
    logger.info("  Time grid: %d bars from %s to %s",
                n_bars, ts_grid[0].strftime('%Y-%m-%d'), ts_grid[-1].strftime('%Y-%m-%d'))

    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_sym = len(symbols)
    cube = np.full((n_bars, n_sym, 5), np.nan, dtype=np.float64)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s
                  AND symbol = ANY(%s)
                ORDER BY bar_start_ts
            """, (timeframe, cutoff, symbols))

            loaded = 0
            for row in cur:
                sym, ts, o, h, l, c, v = row
                if sym not in sym_to_idx:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts_h = ts.replace(minute=0, second=0, microsecond=0)
                if ts_h not in ts_to_idx:
                    continue
                t_idx = ts_to_idx[ts_h]
                s_idx = sym_to_idx[sym]
                cube[t_idx, s_idx] = [float(o), float(h), float(l), float(c), float(v)]
                loaded += 1
                if loaded % 1_000_000 == 0:
                    logger.info("    Loaded %dM rows...", loaded // 1_000_000)

    elapsed = time.time() - t0
    logger.info("  Loaded %d rows in %.1fs → cube shape %s", loaded, elapsed, cube.shape)
    return ts_grid, symbols, cube


def load_social_features(db_url, symbols, ts_grid):
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
                cur.execute("""
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
# FEATURE ENGINEERING (same as edge_discovery)
# ═══════════════════════════════════════════════════════════════════════════════

def engineer_features(ts_grid, symbols, cube):
    """Engineer features from OHLCV cube."""
    logger.info("Engineering features from OHLCV cube %s...", cube.shape)
    t0 = time.time()
    n_bars, n_sym = cube.shape[0], cube.shape[1]
    features = {}

    O = cube[:, :, 0]
    H = cube[:, :, 1]
    L = cube[:, :, 2]
    C = cube[:, :, 3]
    V = cube[:, :, 4]

    with np.errstate(divide='ignore', invalid='ignore'):
        ret_1 = np.where(C[:-1] > 0, C[1:] / C[:-1] - 1, np.nan)
        ret_1 = np.vstack([np.full((1, n_sym), np.nan), ret_1])
        hl_range = np.where(L > 0, (H - L) / L, np.nan)
        body = np.where(O > 0, (C - O) / O, np.nan)
        upper_wick = np.where(hl_range > 0, (H - np.maximum(O, C)) / (H - L + 1e-12), np.nan)
        lower_wick = np.where(hl_range > 0, (np.minimum(O, C) - L) / (H - L + 1e-12), np.nan)
        vol_usd = V * C

    # Returns at various horizons
    for lag in [1, 2, 4, 8, 12, 24, 48, 72, 168]:
        if lag < n_bars:
            r = np.full((n_bars, n_sym), np.nan)
            r[lag:] = np.where(C[:-lag] > 0, C[lag:] / C[:-lag] - 1, np.nan)
            features[f'ret_{lag}h'] = r

    # Rolling statistics
    for window in [6, 12, 24, 48, 72, 168, 336, 720]:
        if window >= n_bars:
            continue
        ret_mean = _rolling_mean(ret_1, window)
        features[f'ret_mean_{window}h'] = ret_mean
        ret_std = _rolling_std(ret_1, window)
        features[f'ret_vol_{window}h'] = ret_std
        vol_mean = _rolling_mean(vol_usd, window)
        features[f'vol_mean_{window}h'] = vol_mean
        with np.errstate(divide='ignore', invalid='ignore'):
            features[f'vol_ratio_{window}h'] = np.where(vol_mean > 0, vol_usd / vol_mean, np.nan)
        range_mean = _rolling_mean(hl_range, window)
        features[f'range_mean_{window}h'] = range_mean

    # Momentum features
    for fast, slow in [(6, 24), (12, 48), (24, 72), (24, 168), (48, 168)]:
        if slow >= n_bars:
            continue
        fast_ma = _rolling_mean(C, fast)
        slow_ma = _rolling_mean(C, slow)
        with np.errstate(divide='ignore', invalid='ignore'):
            features[f'ma_ratio_{fast}_{slow}'] = np.where(slow_ma > 0, fast_ma / slow_ma - 1, np.nan)

    # Price position features
    for window in [24, 72, 168, 336, 720]:
        if window >= n_bars:
            continue
        roll_high = _rolling_max(H, window)
        roll_low = _rolling_min(L, window)
        with np.errstate(divide='ignore', invalid='ignore'):
            denom = roll_high - roll_low
            features[f'price_pos_{window}h'] = np.where(denom > 0, (C - roll_low) / denom, np.nan)
            features[f'dd_from_high_{window}h'] = np.where(roll_high > 0, C / roll_high - 1, np.nan)

    # Volatility regime
    for window in [24, 72, 168]:
        if window >= n_bars:
            continue
        vol_short = _rolling_std(ret_1, window)
        vol_long = _rolling_std(ret_1, window * 4)
        if vol_long is not None:
            with np.errstate(divide='ignore', invalid='ignore'):
                features[f'vol_compression_{window}h'] = np.where(vol_long > 0, vol_short / vol_long, np.nan)

    # VWAP deviation
    for window in [24, 72, 168]:
        if window >= n_bars:
            continue
        vwap = _rolling_vwap(C, V, window)
        with np.errstate(divide='ignore', invalid='ignore'):
            features[f'vwap_dev_{window}h'] = np.where(vwap > 0, C / vwap - 1, np.nan)

    # Candle patterns
    features['body_size'] = np.abs(body)
    features['upper_wick_ratio'] = upper_wick
    features['lower_wick_ratio'] = lower_wick
    features['bar_range'] = hl_range

    direction = np.sign(body)
    features['consec_up'] = _consecutive_count(direction > 0)
    features['consec_down'] = _consecutive_count(direction < 0)

    # Cross-sectional ranks
    for feat_name in ['ret_1h', 'ret_24h', 'ret_168h']:
        if feat_name in features:
            features[f'{feat_name}_rank'] = _cross_sectional_rank(features[feat_name])
    features['vol_usd_rank'] = _cross_sectional_rank(vol_usd)

    # Return autocorrelation (padded to n_bars)
    for lag in [1, 4, 24]:
        if lag < n_bars:
            corr = _rolling_correlation(ret_1[lag:], ret_1[:-lag], 72)
            padded = np.full((n_bars, n_sym), np.nan)
            padded[lag:] = corr
            features[f'ret_autocorr_lag{lag}'] = padded

    features['vol_ret_corr_72h'] = _rolling_correlation(vol_usd, ret_1, 72)
    features['ret_skew_168h'] = _rolling_skew(ret_1, 168)
    features['ret_kurt_168h'] = _rolling_kurtosis(ret_1, 168)

    # Validate shapes
    expected = (n_bars, n_sym)
    for fname, farr in features.items():
        if farr.shape != expected:
            raise ValueError(f"Feature '{fname}' has shape {farr.shape}, expected {expected}")

    logger.info("  Engineered %d features in %.1fs", len(features), time.time() - t0)
    return features


def compute_forward_returns(cube, horizons=[4, 8, 12, 24, 48]):
    C = cube[:, :, 3]
    fwd = {}
    for h in horizons:
        if h >= C.shape[0]:
            continue
        r = np.full_like(C, np.nan)
        with np.errstate(divide='ignore', invalid='ignore'):
            r[:-h] = np.where(C[:-h] > 0, C[h:] / C[:-h] - 1, np.nan)
        fwd[h] = r
    return fwd


# ═══════════════════════════════════════════════════════════════════════════════
# SHORT-ONLY SIMULATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def simulate_short_only(features, fwd_returns, symbols, ts_grid,
                        feat_name, horizon, train_slice, test_slice,
                        percentile=10, cost_bps=18, rebalance_every=None):
    """Simulate short-only mean-reversion on top percentile.

    Returns dict with trades, metrics, per-day breakdown.
    """
    if rebalance_every is None:
        rebalance_every = horizon

    cost = cost_bps / 10000
    feat_train = features[feat_name][train_slice]
    fwd_train = fwd_returns[horizon][train_slice]

    # Compute IC on training to determine direction
    mask = ~np.isnan(feat_train) & ~np.isnan(fwd_train)
    n_valid = int(mask.sum())
    if n_valid < 500:
        return None

    x = feat_train[mask]
    y = fwd_train[mask]
    if n_valid > 50_000:
        idx = np.random.choice(n_valid, 50_000, replace=False)
        x_s, y_s = x[idx], y[idx]
    else:
        x_s, y_s = x, y

    train_ic, _ = _spearmanr(x_s, y_s)
    if np.isnan(train_ic):
        return None

    # For short-only: we short the decile that's predicted to go DOWN
    # If IC < 0: high feature → low return → short top percentile
    # If IC > 0: high feature → high return → short bottom percentile
    short_top = (train_ic < 0)  # True = short high-feature symbols

    feat_test = features[feat_name][test_slice]
    fwd_test = fwd_returns[horizon][test_slice]
    n_test = feat_test.shape[0]
    n_sym = feat_test.shape[1]

    trades = []  # (ts, symbol_idx, return, holding_period)

    for t in range(0, n_test - horizon, rebalance_every):
        feat_row = feat_test[t]
        fwd_row = fwd_test[t]
        valid = ~np.isnan(feat_row) & ~np.isnan(fwd_row)
        n_valid_r = int(valid.sum())
        if n_valid_r < 20:
            continue

        ranks = np.full(n_sym, np.nan)
        ranks[valid] = _rankdata(feat_row[valid]) / n_valid_r

        if short_top:
            short_mask = valid & (ranks >= (1 - percentile / 100))
        else:
            short_mask = valid & (ranks <= percentile / 100)

        ts_bar = ts_grid[test_slice.start + t]
        short_indices = np.where(short_mask)[0]
        for si in short_indices:
            trade_ret = -fwd_row[si] - cost  # Short: profit when price drops
            trades.append({
                'ts': ts_bar,
                'symbol_idx': si,
                'symbol': symbols[si],
                'return': float(trade_ret),
                'raw_return': float(-fwd_row[si]),
                'horizon': horizon,
            })

    if not trades:
        return None

    trade_rets = np.array([t['return'] for t in trades])
    raw_rets = np.array([t['raw_return'] for t in trades])

    wins = trade_rets[trade_rets > 0]
    losses = trade_rets[trade_rets < 0]
    gp = wins.sum() if len(wins) > 0 else 0
    gl = abs(losses.sum()) if len(losses) > 0 else 0
    agg_pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
    wr = len(wins) / len(trade_rets) if len(trade_rets) > 0 else 0

    # Per-day metrics
    trade_dates = [t['ts'].date() for t in trades]
    unique_dates = sorted(set(trade_dates))
    daily_pfs = []
    daily_rets_sum = []  # Average return per trade per day, not sum
    daily_trade_counts = []

    for d in unique_dates:
        day_rets = np.array([t['return'] for t in trades if t['ts'].date() == d])
        n_day = len(day_rets)
        if n_day == 0:
            continue
        daily_trade_counts.append(n_day)
        daily_rets_sum.append(day_rets.mean())  # Average per-trade return per day
        dw = day_rets[day_rets > 0].sum()
        dl = abs(day_rets[day_rets < 0].sum())
        if dl > 0:
            daily_pfs.append(dw / dl)
        elif dw > 0:
            daily_pfs.append(3.0)

    avg_daily_pf = np.mean(daily_pfs) if daily_pfs else 0
    daily_rets_arr = np.array(daily_rets_sum) if daily_rets_sum else np.array([0.0])

    # Equity curve (cumulative mean daily return)
    cum_rets = np.cumsum(daily_rets_arr)
    max_dd = 0
    peak = cum_rets[0]
    for cr in cum_rets:
        if cr > peak:
            peak = cr
        dd = peak - cr
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualized from daily avg returns)
    if len(daily_rets_arr) > 1 and daily_rets_arr.std() > 0:
        sr = daily_rets_arr.mean() / daily_rets_arr.std() * np.sqrt(365)
    else:
        sr = 0

    # Per-symbol analysis
    sym_rets = defaultdict(list)
    for t in trades:
        sym_rets[t['symbol']].append(t['return'])

    top_syms = sorted(sym_rets.items(), key=lambda x: np.mean(x[1]), reverse=True)[:10]
    bottom_syms = sorted(sym_rets.items(), key=lambda x: np.mean(x[1]))[:10]

    return {
        'trades': trades,
        'n_trades': len(trades),
        'win_rate': wr,
        'agg_pf': agg_pf,
        'avg_daily_pf': avg_daily_pf,
        'avg_ret_bps': trade_rets.mean() * 10000,
        'avg_raw_ret_bps': raw_rets.mean() * 10000,
        'sharpe': sr,
        'max_dd_bps': max_dd * 10000,
        'daily_pfs': daily_pfs,
        'daily_rets': daily_rets_arr,
        'n_days': len(unique_dates),
        'train_ic': train_ic,
        'top_symbols': [(s, np.mean(r) * 10000, len(r)) for s, r in top_syms],
        'bottom_symbols': [(s, np.mean(r) * 10000, len(r)) for s, r in bottom_syms],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Short-Only Mean Reversion Deep Dive")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--timeframe", type=str, default="1h")
    args = parser.parse_args()

    if not DB_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    np.random.seed(42)

    logger.info("=" * 70)
    logger.info("SHORT-ONLY MEAN REVERSION DEEP DIVE")
    logger.info("=" * 70)

    # ── Load data ─────────────────────────────────────────────────────────
    ts_grid, symbols, cube = load_ohlcv_matrix(DB_URL, args.timeframe, args.lookback_days)
    features = engineer_features(ts_grid, symbols, cube)

    social_feats = load_social_features(DB_URL, symbols, ts_grid)
    features.update(social_feats)
    deriv_feats = load_derivatives_features(DB_URL, symbols, ts_grid)
    features.update(deriv_feats)

    horizons = [4, 8, 12, 24, 48]
    fwd_returns = compute_forward_returns(cube, horizons)
    logger.info("Total features: %d", len(features))

    n_bars = len(ts_grid)
    holdout_size = n_bars // 6  # ~720 bars = 30 days
    holdout_start = n_bars - holdout_size
    train_end = holdout_start - 24  # embargo

    logger.info("Train: [0:%d], Holdout: [%d:%d] (%s → %s)",
                train_end, holdout_start, n_bars,
                ts_grid[holdout_start].strftime('%Y-%m-%d'),
                ts_grid[-1].strftime('%Y-%m-%d'))

    # ═════════════════════════════════════════════════════════════════════
    # STAGE 1: Per-feature short-only screening on TRAINING data
    # ═════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 1: PER-FEATURE SHORT-ONLY SCREENING (train only)")
    logger.info("=" * 70)

    # Use last 30% of training as validation
    val_size = int(train_end * 0.3)
    val_start = train_end - val_size
    inner_train = slice(0, val_start - 24)
    inner_val = slice(val_start, train_end)

    logger.info("Inner train: [0:%d], Inner val: [%d:%d]",
                val_start - 24, val_start, train_end)

    results = []
    feat_count = 0
    total = len(features) * len(horizons)

    for feat_name in features:
        for horizon in horizons:
            feat_count += 1
            if feat_count % 100 == 0:
                logger.info("  Screening %d/%d...", feat_count, total)

            if horizon not in fwd_returns:
                continue

            result = simulate_short_only(
                features, fwd_returns, symbols, ts_grid,
                feat_name, horizon, inner_train, inner_val,
                percentile=10, cost_bps=18,
            )
            if result is None:
                continue
            if result['n_trades'] < 50:
                continue

            results.append({
                'feat': feat_name,
                'horizon': horizon,
                **result,
            })

    # Sort by aggregate PF (more conservative than daily PF)
    results.sort(key=lambda x: x['agg_pf'], reverse=True)

    logger.info("")
    logger.info("TOP 30 SHORT-ONLY STRATEGIES (inner validation):")
    logger.info("%-30s %4s %6s %6s %8s %8s %8s %8s",
                "Feature", "Hor", "Trades", "WR%", "AggPF", "DlyPF", "AvgBps", "IC")
    logger.info("-" * 105)

    for r in results[:30]:
        logger.info("%-30s %4d %6d %6.1f %8.3f %8.3f %8.1f %8.4f",
                     r['feat'], r['horizon'], r['n_trades'],
                     r['win_rate'] * 100, r['agg_pf'], r['avg_daily_pf'],
                     r['avg_ret_bps'], r['train_ic'])

    # ═════════════════════════════════════════════════════════════════════
    # STAGE 2: Walk-forward validation of top strategies
    # ═════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 2: WALK-FORWARD VALIDATION (5-fold, top 10 strategies)")
    logger.info("=" * 70)

    # Take top 10 unique features (best horizon per feature)
    seen_feats = set()
    top_strats = []
    for r in results:
        if r['feat'] not in seen_feats and r['agg_pf'] > 1.0:
            seen_feats.add(r['feat'])
            top_strats.append((r['feat'], r['horizon']))
            if len(top_strats) >= 10:
                break

    n_folds = 5
    fold_size = train_end // n_folds

    for feat_name, best_horizon in top_strats:
        logger.info("")
        logger.info("  Strategy: %s @ %dh", feat_name, best_horizon)

        fold_results = []
        for fold_idx in range(n_folds):
            f_test_start = (fold_idx + 1) * fold_size
            f_test_end = min(f_test_start + fold_size, train_end)
            f_train_end = f_test_start - 24

            if f_train_end < 200 or f_test_end <= f_test_start:
                continue

            f_train = slice(0, f_train_end)
            f_test = slice(f_test_start, f_test_end)

            result = simulate_short_only(
                features, fwd_returns, symbols, ts_grid,
                feat_name, best_horizon, f_train, f_test,
                percentile=10, cost_bps=18,
            )

            if result is None:
                fold_results.append({'fold': fold_idx + 1, 'agg_pf': 0, 'n_trades': 0})
                continue

            fold_results.append({
                'fold': fold_idx + 1,
                'n_trades': result['n_trades'],
                'wr': result['win_rate'],
                'agg_pf': result['agg_pf'],
                'daily_pf': result['avg_daily_pf'],
                'avg_ret': result['avg_ret_bps'],
            })

        # Report
        trading_folds = [f for f in fold_results if f.get('n_trades', 0) > 0]
        if not trading_folds:
            logger.info("    No trades in any fold")
            continue

        profitable = sum(1 for f in trading_folds if f.get('agg_pf', 0) > 1.0)
        avg_agg_pf = np.mean([f['agg_pf'] for f in trading_folds])
        avg_daily_pf = np.mean([f.get('daily_pf', 0) for f in trading_folds])

        for f in fold_results:
            if f.get('n_trades', 0) > 0:
                logger.info("    Fold %d: %d trades, WR=%.1f%%, agg_PF=%.3f, daily_PF=%.3f, avg=%.1f bps",
                             f['fold'], f['n_trades'], f.get('wr', 0) * 100,
                             f['agg_pf'], f.get('daily_pf', 0), f.get('avg_ret', 0))
            else:
                logger.info("    Fold %d: no trades", f['fold'])

        logger.info("    ─── Profitable folds: %d/%d, Avg agg_PF: %.3f, Avg daily_PF: %.3f",
                     profitable, len(trading_folds), avg_agg_pf, avg_daily_pf)

    # ═════════════════════════════════════════════════════════════════════
    # STAGE 3: HOLDOUT (never-seen data)
    # ═════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 3: HOLDOUT VALIDATION")
    logger.info("=" * 70)

    full_train = slice(0, train_end)
    holdout_slice = slice(holdout_start, n_bars)

    holdout_results = []
    for feat_name, best_horizon in top_strats:
        for cost_bps_val in [18, 27, 36]:
            result = simulate_short_only(
                features, fwd_returns, symbols, ts_grid,
                feat_name, best_horizon, full_train, holdout_slice,
                percentile=10, cost_bps=cost_bps_val,
            )
            if result is None:
                continue

            holdout_results.append({
                'feat': feat_name,
                'horizon': best_horizon,
                'cost_bps': cost_bps_val,
                **result,
            })

    # Report holdout at base cost
    logger.info("")
    logger.info("HOLDOUT RESULTS (18 bps):")
    logger.info("%-30s %4s %6s %6s %8s %8s %8s %8s %8s",
                "Feature", "Hor", "Trades", "WR%", "AggPF", "DlyPF", "AvgBps", "SR", "MaxDD")
    logger.info("-" * 115)

    for r in holdout_results:
        if r['cost_bps'] == 18:
            logger.info("%-30s %4d %6d %6.1f %8.3f %8.3f %8.1f %8.2f %8.1f",
                         r['feat'], r['horizon'], r['n_trades'],
                         r['win_rate'] * 100, r['agg_pf'], r['avg_daily_pf'],
                         r['avg_ret_bps'], r['sharpe'], r['max_dd_bps'])

    # Cost sensitivity
    logger.info("")
    logger.info("COST SENSITIVITY:")
    for feat_name, best_horizon in top_strats[:3]:
        costs = [(r['cost_bps'], r['agg_pf'], r['avg_ret_bps'])
                 for r in holdout_results
                 if r['feat'] == feat_name and r['horizon'] == best_horizon]
        if costs:
            line = f"  {feat_name} h={best_horizon}: "
            line += " | ".join(f"{c}bps→PF={pf:.3f}(ret={ret:.1f})" for c, pf, ret in costs)
            logger.info(line)

    # ═════════════════════════════════════════════════════════════════════
    # STAGE 4: BEST STRATEGY DEEP ANALYSIS
    # ═════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 4: BEST STRATEGY DEEP ANALYSIS")
    logger.info("=" * 70)

    # Find best holdout strategy (highest agg_pf at 18 bps)
    base_holdout = [r for r in holdout_results if r['cost_bps'] == 18]
    if not base_holdout:
        logger.info("No holdout results")
        return

    best = max(base_holdout, key=lambda r: r['agg_pf'])
    logger.info("")
    logger.info("Best strategy: %s @ %dh", best['feat'], best['horizon'])
    logger.info("  Trades: %d, WR: %.1f%%, Agg PF: %.3f", best['n_trades'],
                best['win_rate'] * 100, best['agg_pf'])
    logger.info("  Avg daily PF: %.3f, Sharpe: %.2f", best['avg_daily_pf'], best['sharpe'])
    logger.info("  Avg return (after cost): %.1f bps", best['avg_ret_bps'])
    logger.info("  Avg raw return (pre-cost): %.1f bps", best['avg_raw_ret_bps'])
    logger.info("  Max drawdown: %.1f bps", best['max_dd_bps'])

    # Per-day analysis
    logger.info("")
    logger.info("  PER-DAY ANALYSIS (%d trading days):", best['n_days'])
    daily_rets = best['daily_rets']
    daily_pfs = best['daily_pfs']
    profitable_days = sum(1 for d in daily_rets if d > 0)
    logger.info("    Profitable days: %d/%d (%.0f%%)",
                profitable_days, len(daily_rets),
                100 * profitable_days / len(daily_rets) if len(daily_rets) > 0 else 0)
    logger.info("    Avg daily return: %.1f bps", daily_rets.mean() * 10000)
    logger.info("    Std daily return: %.1f bps", daily_rets.std() * 10000)
    if len(daily_pfs) > 0:
        logger.info("    Median daily PF: %.3f", np.median(daily_pfs))
        logger.info("    Daily PF range: [%.3f, %.3f]", min(daily_pfs), max(daily_pfs))

    # Top/bottom symbols
    logger.info("")
    logger.info("  TOP SYMBOLS (highest avg short return):")
    for sym, ret, n in best['top_symbols'][:5]:
        logger.info("    %s: %.1f bps avg (%d trades)", sym, ret, n)

    logger.info("")
    logger.info("  BOTTOM SYMBOLS (worst avg short return):")
    for sym, ret, n in best['bottom_symbols'][:5]:
        logger.info("    %s: %.1f bps avg (%d trades)", sym, ret, n)

    # ═════════════════════════════════════════════════════════════════════
    # STAGE 5: ENSEMBLE (top 3 features)
    # ═════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 5: ENSEMBLE (top 3 features voting)")
    logger.info("=" * 70)

    # For each rebalance bar: a symbol is shorted if >= 2 of top 3 features
    # rank it in the short zone
    top3 = top_strats[:3]
    cost = 18 / 10000

    # Train IC for each feature
    top3_configs = []
    for feat_name, horizon in top3:
        feat_train = features[feat_name][full_train]
        fwd_train = fwd_returns[horizon][full_train]
        mask = ~np.isnan(feat_train) & ~np.isnan(fwd_train)
        x = feat_train[mask]
        y = fwd_train[mask]
        if len(x) > 50_000:
            idx = np.random.choice(len(x), 50_000, replace=False)
            x, y = x[idx], y[idx]
        ic, _ = _spearmanr(x, y)
        top3_configs.append({
            'feat': feat_name, 'horizon': horizon, 'ic': ic,
            'short_top': ic < 0,
        })

    # Use the median horizon for rebalance
    rebal_h = sorted([c['horizon'] for c in top3_configs])[1]

    n_test = n_bars - holdout_start
    n_sym = len(symbols)
    ensemble_trades = []

    for t in range(0, n_test - max(c['horizon'] for c in top3_configs), rebal_h):
        # For each feature, compute which symbols are in the short zone
        short_votes = np.zeros(n_sym, dtype=int)
        fwd_arr = None
        for cfg in top3_configs:
            feat_test = features[cfg['feat']][holdout_start + t]
            fwd_test = fwd_returns[cfg['horizon']][holdout_start + t]
            if fwd_arr is None:
                fwd_arr = fwd_test  # Use first feature's horizon for returns
            valid = ~np.isnan(feat_test) & ~np.isnan(fwd_test)
            n_valid_r = int(valid.sum())
            if n_valid_r < 20:
                continue
            ranks = np.full(n_sym, np.nan)
            ranks[valid] = _rankdata(feat_test[valid]) / n_valid_r
            if cfg['short_top']:
                short_votes[valid & (ranks >= 0.9)] += 1
            else:
                short_votes[valid & (ranks <= 0.1)] += 1

        # Require >= 2 votes
        if fwd_arr is None:
            continue
        consensus_mask = (short_votes >= 2) & ~np.isnan(fwd_arr)
        ts_bar = ts_grid[holdout_start + t]
        for si in np.where(consensus_mask)[0]:
            trade_ret = -fwd_arr[si] - cost
            ensemble_trades.append({
                'ts': ts_bar,
                'symbol': symbols[si],
                'return': float(trade_ret),
                'votes': int(short_votes[si]),
            })

    if ensemble_trades:
        ens_rets = np.array([t['return'] for t in ensemble_trades])
        ens_wins = ens_rets[ens_rets > 0]
        ens_losses = ens_rets[ens_rets < 0]
        ens_pf = ens_wins.sum() / abs(ens_losses.sum()) if ens_losses.sum() != 0 else float('inf')
        ens_wr = len(ens_wins) / len(ens_rets)

        logger.info("  Ensemble (>= 2/3 votes): %d trades, WR=%.1f%%, agg_PF=%.3f, avg=%.1f bps",
                     len(ens_rets), ens_wr * 100, ens_pf, ens_rets.mean() * 10000)

        # 3/3 unanimous
        unan_rets = np.array([t['return'] for t in ensemble_trades if t['votes'] == 3])
        if len(unan_rets) > 10:
            unan_w = unan_rets[unan_rets > 0]
            unan_l = unan_rets[unan_rets < 0]
            unan_pf = unan_w.sum() / abs(unan_l.sum()) if unan_l.sum() != 0 else float('inf')
            logger.info("  Ensemble (3/3 unanimous): %d trades, WR=%.1f%%, agg_PF=%.3f, avg=%.1f bps",
                         len(unan_rets), len(unan_w) / len(unan_rets) * 100,
                         unan_pf, unan_rets.mean() * 10000)
    else:
        logger.info("  Ensemble: no trades")

    # ═════════════════════════════════════════════════════════════════════
    # STAGE 6: DSR CORRECTION
    # ═════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 6: DSR CORRECTION")
    logger.info("=" * 70)

    if best['sharpe'] != 0:
        # Count total strategies tested (conservative: all feature×horizon combos)
        n_trials = len(results)  # From stage 1 screening
        e_max_sr = np.sqrt(2 * np.log(max(n_trials, 2)))

        T = best['n_days']
        sr = best['sharpe']
        var_sr = (1 + 0.5 * sr ** 2) / T
        se_sr = np.sqrt(var_sr)

        dsr_stat = (sr - e_max_sr * se_sr) / se_sr if se_sr > 0 else 0
        dsr_p = 0.5 * (1 + math.erf(dsr_stat / math.sqrt(2)))

        logger.info("  Strategies tested (n_trials): %d", n_trials)
        logger.info("  E[max(SR)] under null: %.2f", e_max_sr)
        logger.info("  SR hat: %.2f ± %.2f", sr, se_sr)
        logger.info("  DSR statistic: %.2f", dsr_stat)
        logger.info("  DSR > 0 probability: %.3f", dsr_p)
        if dsr_p > 0.95:
            logger.info("  *** DSR PASSED (p > 0.95) ***")
        elif dsr_p > 0.5:
            logger.info("  DSR marginal (0.5 < p < 0.95)")
        else:
            logger.info("  DSR FAILED (p < 0.5)")

    # ═════════════════════════════════════════════════════════════════════
    # STAGE 7: PERCENTILE SWEEP
    # ═════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 7: SHORT PERCENTILE SWEEP (best feature)")
    logger.info("=" * 70)

    for pct in [5, 10, 15, 20, 25, 30]:
        r = simulate_short_only(
            features, fwd_returns, symbols, ts_grid,
            best['feat'], best['horizon'], full_train, holdout_slice,
            percentile=pct, cost_bps=18,
        )
        if r:
            logger.info("  Top %2d%%: %5d trades, WR=%5.1f%%, agg_PF=%.3f, avg=%.1f bps",
                         pct, r['n_trades'], r['win_rate'] * 100,
                         r['agg_pf'], r['avg_ret_bps'])

    # ═════════════════════════════════════════════════════════════════════
    # STAGE 8: HORIZON SWEEP
    # ═════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("STAGE 8: HOLDING PERIOD SWEEP (best feature)")
    logger.info("=" * 70)

    for h in [4, 8, 12, 24, 48]:
        if h not in fwd_returns:
            continue
        r = simulate_short_only(
            features, fwd_returns, symbols, ts_grid,
            best['feat'], h, full_train, holdout_slice,
            percentile=10, cost_bps=18,
        )
        if r:
            logger.info("  Hold %2dh: %5d trades, WR=%5.1f%%, agg_PF=%.3f, avg=%.1f bps",
                         h, r['n_trades'], r['win_rate'] * 100,
                         r['agg_pf'], r['avg_ret_bps'])

    logger.info("")
    logger.info("=" * 70)
    logger.info("COMPLETE")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
