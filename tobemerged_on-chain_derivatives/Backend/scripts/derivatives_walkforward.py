#!/usr/bin/env python3
"""
Derivatives Walk-Forward Validation
====================================

Tests whether derivatives features (funding rates, open interest, long/short ratios)
improve ignition prediction in a walk-forward framework.

Three tests:
  Test A: Funding-only walk-forward (3-fold expanding window, logistic regression)
          Compares baseline OHLCV-only model vs enhanced model with funding features.
          Uses long funding history (365 days).

  Test B: Full-feature short-window (21-day single train/test split)
          All derivatives features, but only 21 days of data.
          Reported with caveats — not decision-grade.

  Test C: Conditional precision (compression + bearish funding → ignition rate)
          Does bearish funding during compression periods predict ignition
          better than compression alone? Fisher's exact test.

Plus: Feature correlation analysis (pairwise Spearman, flag |ρ| > 0.8).

Accept criteria:
  - Test A precision improvement >= 1pp in 2/3+ folds, OR
  - Test C odds ratio > 1.3 with Fisher p < 0.10

Usage:
    tmux new -s deriv-wf
    docker compose run --rm -T worker python3 /scripts/derivatives_walkforward.py --days 140 --funding-days 400 --n-folds 3
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
    from scipy.stats import spearmanr, fisher_exact
except ImportError:
    import subprocess
    subprocess.check_call(["pip", "install", "scipy", "--break-system-packages", "-q"])
    from scipy.stats import spearmanr, fisher_exact

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import precision_score, recall_score, f1_score, log_loss
except ImportError:
    import subprocess
    subprocess.check_call(["pip", "install", "scikit-learn", "--break-system-packages", "-q"])
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import precision_score, recall_score, f1_score, log_loss

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


# ═══════════════════════════════════════════════════════════════════════════════
# ROLLING UTILITIES (copied from signal_genesis_ignition.py)
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
# DATA LOADING
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


def load_derivatives_features(database_url: str, symbols: list, ts_grid: np.ndarray):
    """Load derivatives_features_1h aligned to the OHLCV grid."""
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
    for fname in feature_names:
        non_nan = np.count_nonzero(~np.isnan(features[fname]))
        total = n_bars * n_symbols
        logger.info("  %s: %.1f%% non-NaN (%d/%d)", fname, 100 * non_nan / total, non_nan, total)

    return features, feature_names


def load_funding_features_long(database_url: str, symbols: list, ts_grid: np.ndarray):
    """Compute funding rate features from raw funding_rates table for full history.

    Funding rates are every 4h or 8h. Forward-fill to hourly grid,
    then compute: rate, ma8, acceleration, zscore.
    """
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)
    n_symbols = len(symbols)

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

    # Compute features
    ma8 = _rolling_mean_2d(raw_rate, 8)
    accel = np.full_like(raw_rate, np.nan)
    accel[3:] = raw_rate[3:] - raw_rate[:-3]
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


def detect_compressions(close, lookback=100, pctile_threshold=10.0):
    """Detect volatility compression: BB width in bottom percentile."""
    n_bars, n_symbols = close.shape
    sma_20 = _rolling_mean_2d(close, 20)
    std_20 = _rolling_std_2d(close, 20)
    bb_width = np.where(sma_20 > 0, 4 * std_20 / sma_20, np.nan)

    compressed = np.full((n_bars, n_symbols), False)
    if n_bars <= lookback:
        return compressed

    try:
        from numpy.lib.stride_tricks import sliding_window_view
        windows = sliding_window_view(bb_width, lookback, axis=0)
        current = bb_width[lookback - 1:]
        n_below = np.nansum(windows < current[:, :, np.newaxis], axis=2)
        n_valid = np.nansum(~np.isnan(windows), axis=2)
        pctile = np.where(n_valid > 0, 100.0 * n_below / n_valid, 50.0)
        compressed[lookback - 1:] = pctile < pctile_threshold
    except MemoryError:
        logger.warning("  sliding_window_view OOM, falling back to loop")
        for t in range(lookback, n_bars):
            window = bb_width[t - lookback:t, :]
            current_val = bb_width[t, :]
            n_below = np.nansum(window < current_val[np.newaxis, :], axis=0)
            n_valid = np.nansum(~np.isnan(window), axis=0)
            pctile = np.where(n_valid > 0, 100.0 * n_below / n_valid, 50.0)
            compressed[t, :] = pctile < pctile_threshold

    n_compressed = np.sum(compressed)
    logger.info("Compressions: %d bar-symbols (%.2f%% of %d total)",
                n_compressed, 100 * n_compressed / (n_bars * n_symbols), n_bars * n_symbols)
    return compressed


# ═══════════════════════════════════════════════════════════════════════════════
# BASELINE OHLCV FEATURES (for model comparison)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_bb_width_percentile(close, sma_window=20, pctile_lookback=100):
    """BB width as percentile of trailing history. Shape: (n_bars, n_symbols).

    Matches the live ignition model's compression feature.
    """
    sma = _rolling_mean_2d(close, sma_window)
    std = _rolling_std_2d(close, sma_window)
    bb_width = np.where(sma > 0, 4 * std / sma, np.nan)

    n_bars, n_symbols = close.shape
    pctile = np.full_like(bb_width, np.nan)

    if n_bars > pctile_lookback:
        try:
            from numpy.lib.stride_tricks import sliding_window_view
            windows = sliding_window_view(bb_width, pctile_lookback, axis=0)
            current = bb_width[pctile_lookback - 1:]
            n_below = np.nansum(windows < current[:, :, np.newaxis], axis=2)
            n_valid = np.nansum(~np.isnan(windows), axis=2)
            pctile[pctile_lookback - 1:] = np.where(
                n_valid > 0, 100.0 * n_below / n_valid, 50.0)
        except MemoryError:
            for t in range(pctile_lookback, n_bars):
                window = bb_width[t - pctile_lookback:t]
                current = bb_width[t]
                n_below = np.nansum(window < current[np.newaxis, :], axis=0)
                n_valid = np.nansum(~np.isnan(window), axis=0)
                pctile[t] = np.where(n_valid > 0, 100.0 * n_below / n_valid, 50.0)

    return pctile


def compute_vol_z(volume, window=20):
    """Volume z-score. Shape: (n_bars, n_symbols).

    Matches the live ignition model's volume feature.
    """
    mean = _rolling_mean_2d(volume, window)
    std = _rolling_std_2d(volume, window)
    return np.where(std > 0, (volume - mean) / std, 0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST A: FUNDING-ONLY WALK-FORWARD
# ═══════════════════════════════════════════════════════════════════════════════

def walk_forward_funding(close, high, low, volume, ts_grid, symbols,
                         funding_features, n_folds=3, embargo_bars=48):
    """Test A: Walk-forward with expanding window.

    Both baseline and enhanced models train/test on the SAME observation set —
    only observations where ALL features (baseline + funding) are non-NaN.
    Baseline just ignores the funding columns but sees the same rows.

    Baseline features (2): bb_width_pctile, vol_z (matching live ignition model)
    Enhanced features (2+N): baseline + funding_rate_long, funding_rate_ma8_long

    Returns per-fold results: precision, recall, lift, McNemar p.
    """
    n_bars, n_symbols = close.shape

    logger.info("\n" + "=" * 70)
    logger.info("TEST A: FUNDING-ONLY WALK-FORWARD (%d folds, embargo=%d bars)",
                n_folds, embargo_bars)
    logger.info("=" * 70)

    # Compute targets
    mfh = compute_max_forward_high(close, high, HORIZON)
    ignition = (~np.isnan(mfh)) & (mfh >= IGNITION_THRESHOLD)

    # Compute baseline features (2 only — matching live ignition model)
    bb_width_pctile = compute_bb_width_percentile(close)
    vol_z = compute_vol_z(volume)
    logger.info("  Baseline features: bb_width_pctile, vol_z")

    # Identify available funding features
    available_funding = {}
    for k in ['funding_rate_long', 'funding_rate_ma8_long']:
        if k in funding_features:
            available_funding[k] = funding_features[k]
    funding_names = sorted(available_funding.keys())
    logger.info("  Funding features: %s", ", ".join(funding_names))

    # Build unified observation set: only rows where ALL features are non-NaN
    # Both models see the same rows — baseline just uses first 2 columns
    obs_t = []
    obs_s = []
    baseline_X_list = []
    enhanced_X_list = []
    y_list = []

    for t in range(WARMUP_BARS, n_bars - HORIZON):
        for s in range(n_symbols):
            # Must have valid target
            if np.isnan(mfh[t, s]):
                continue

            # Baseline features — must be non-NaN
            bb = bb_width_pctile[t, s]
            vz = vol_z[t, s]
            if np.isnan(bb) or np.isnan(vz):
                continue

            # Funding features — must ALL be non-NaN
            fr_vals = []
            all_valid = True
            for fname in funding_names:
                v = available_funding[fname][t, s]
                if np.isnan(v):
                    all_valid = False
                    break
                fr_vals.append(v)

            if not all_valid:
                continue

            # This observation has ALL features valid — include it
            obs_t.append(t)
            obs_s.append(s)
            baseline_X_list.append([bb, vz])
            enhanced_X_list.append([bb, vz] + fr_vals)
            y_list.append(1 if ignition[t, s] else 0)

    obs_t = np.array(obs_t)
    baseline_X = np.array(baseline_X_list)
    enhanced_X = np.array(enhanced_X_list)
    y = np.array(y_list)

    logger.info("  Unified observation set: %d obs, %.2f%% positive",
                len(y), 100 * np.mean(y) if len(y) > 0 else 0)

    if len(y) < 200:
        logger.error("  Insufficient observations (%d) for walk-forward", len(y))
        return []

    # Define fold boundaries using expanding window (spec: 40%+20*fold)
    t_min, t_max = obs_t.min(), obs_t.max()
    t_range = t_max - t_min

    fold_results = []

    for fold_idx in range(n_folds):
        # Expanding window: train on first (40 + 20*fold)%, test on next 20%
        train_frac = 0.40 + 0.20 * fold_idx
        test_start_frac = train_frac
        test_end_frac = min(train_frac + 0.20, 1.0)

        train_end_t = t_min + int(t_range * train_frac)
        test_start_t = train_end_t + embargo_bars
        test_end_t = t_min + int(t_range * test_end_frac)

        if test_start_t >= test_end_t:
            logger.warning("  Fold %d: skipped (embargo eats test window)", fold_idx)
            continue

        train_mask = obs_t <= train_end_t
        test_mask = (obs_t >= test_start_t) & (obs_t <= test_end_t)

        n_train = int(np.sum(train_mask))
        n_test = int(np.sum(test_mask))

        if n_train < 100 or n_test < 50:
            logger.warning("  Fold %d: skipped (train=%d, test=%d)", fold_idx, n_train, n_test)
            continue

        y_train = y[train_mask]
        y_test = y[test_mask]

        if np.sum(y_train) < 5 or np.sum(y_test) < 2:
            logger.warning("  Fold %d: skipped (too few positives: train=%d, test=%d)",
                          fold_idx, np.sum(y_train), np.sum(y_test))
            continue

        logger.info("\n  Fold %d: train=%d obs (%.2f%% pos), test=%d obs (%.2f%% pos)",
                    fold_idx, n_train, 100 * np.mean(y_train),
                    n_test, 100 * np.mean(y_test))

        # === Baseline model (bb_width_pctile + vol_z only) ===
        scaler_b = StandardScaler()
        X_train_b = scaler_b.fit_transform(baseline_X[train_mask])
        X_test_b = scaler_b.transform(baseline_X[test_mask])

        model_b = LogisticRegression(
            class_weight='balanced', C=0.1, max_iter=1000, random_state=42
        )
        model_b.fit(X_train_b, y_train)
        pred_b = model_b.predict(X_test_b)
        prob_b = model_b.predict_proba(X_test_b)[:, 1]

        prec_b = precision_score(y_test, pred_b, zero_division=0)
        rec_b = recall_score(y_test, pred_b, zero_division=0)
        f1_b = f1_score(y_test, pred_b, zero_division=0)

        # Top-decile lift
        top_k = max(1, len(prob_b) // 10)
        top_idx_b = np.argsort(prob_b)[-top_k:]
        base_rate = np.mean(y_test)
        lift_b = float(np.mean(y_test[top_idx_b]) / base_rate) if base_rate > 0 else 0.0

        logger.info("    BASELINE: prec=%.4f, rec=%.4f, F1=%.4f, lift@10%%=%.2f",
                    prec_b, rec_b, f1_b, lift_b)

        # === Enhanced model (baseline + funding) ===
        scaler_e = StandardScaler()
        X_train_e = scaler_e.fit_transform(enhanced_X[train_mask])
        X_test_e = scaler_e.transform(enhanced_X[test_mask])

        model_e = LogisticRegression(
            class_weight='balanced', C=0.1, max_iter=1000, random_state=42
        )
        model_e.fit(X_train_e, y_train)
        pred_e = model_e.predict(X_test_e)
        prob_e = model_e.predict_proba(X_test_e)[:, 1]

        prec_e = precision_score(y_test, pred_e, zero_division=0)
        rec_e = recall_score(y_test, pred_e, zero_division=0)
        f1_e = f1_score(y_test, pred_e, zero_division=0)

        # Top-decile lift
        top_idx_e = np.argsort(prob_e)[-top_k:]
        lift_e = float(np.mean(y_test[top_idx_e]) / base_rate) if base_rate > 0 else 0.0

        logger.info("    ENHANCED: prec=%.4f, rec=%.4f, F1=%.4f, lift@10%%=%.2f",
                    prec_e, rec_e, f1_e, lift_e)

        # McNemar's test (chi-squared with continuity correction)
        # Both models predict on the SAME observations, so direct comparison
        b_right_e_wrong = int(np.sum((pred_b == y_test) & (pred_e != y_test)))
        b_wrong_e_right = int(np.sum((pred_b != y_test) & (pred_e == y_test)))

        if (b_right_e_wrong + b_wrong_e_right) > 0:
            from scipy.stats import chi2 as chi2_dist
            mcnemar_chi2 = (abs(b_right_e_wrong - b_wrong_e_right) - 1) ** 2 / \
                           (b_right_e_wrong + b_wrong_e_right)
            mcnemar_p = float(1 - chi2_dist.cdf(mcnemar_chi2, df=1))
        else:
            mcnemar_chi2, mcnemar_p = 0.0, 1.0

        logger.info("    McNemar: b_right_e_wrong=%d, b_wrong_e_right=%d, chi2=%.2f, p=%.4f",
                    b_right_e_wrong, b_wrong_e_right, mcnemar_chi2, mcnemar_p)

        prec_delta = prec_e - prec_b
        logger.info("    DELTA: precision %+.4f (%+.2f pp), F1 %+.4f, lift %+.2f",
                    prec_delta, prec_delta * 100, f1_e - f1_b, lift_e - lift_b)

        # Enhanced model coefficients
        enh_coef_names = ['bb_width_pctile', 'vol_z'] + funding_names
        enh_coefs = {name: float(c) for name, c in zip(enh_coef_names, model_e.coef_[0])}

        fold_results.append({
            "fold": fold_idx,
            "n_train": n_train,
            "n_test": n_test,
            "test_positive_rate": float(np.mean(y_test)),
            "n_positive_train": int(np.sum(y_train)),
            "n_positive_test": int(np.sum(y_test)),
            "baseline": {
                "precision": float(prec_b),
                "recall": float(rec_b),
                "f1": float(f1_b),
                "lift_top10": float(lift_b),
            },
            "enhanced": {
                "precision": float(prec_e),
                "recall": float(rec_e),
                "f1": float(f1_e),
                "lift_top10": float(lift_e),
            },
            "precision_delta_pp": float(prec_delta * 100),
            "lift_delta": float(lift_e - lift_b),
            "mcnemar_chi2": float(mcnemar_chi2),
            "mcnemar_p": float(mcnemar_p),
            "enhanced_model_coefs": enh_coefs,
        })

    # Summarize
    logger.info("\n  " + "-" * 60)
    logger.info("  TEST A SUMMARY:")
    if fold_results:
        deltas = [r["precision_delta_pp"] for r in fold_results]
        lift_deltas = [r["lift_delta"] for r in fold_results]
        improved = sum(1 for d in deltas if d >= 1.0)
        logger.info("    Precision delta per fold: %s",
                    ", ".join(f"{d:+.2f}pp" for d in deltas))
        logger.info("    Lift delta per fold: %s",
                    ", ".join(f"{d:+.2f}" for d in lift_deltas))
        logger.info("    Folds with >= 1pp improvement: %d / %d", improved, len(fold_results))
        logger.info("    PASS: %s", "YES" if improved >= 2 else "NO")
    else:
        logger.info("    No valid folds completed")

    return fold_results


# ═══════════════════════════════════════════════════════════════════════════════
# TEST B: FULL-FEATURE SHORT-WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_testb_baseline(close, high, low, volume):
    """Broader OHLCV baseline for Test B (7 features). NOT used in Test A."""
    n_bars, n_symbols = close.shape
    features = {}
    for h in [1, 4, 24]:
        ret = np.full((n_bars, n_symbols), np.nan)
        if n_bars > h:
            ret[h:] = close[h:] / close[:-h] - 1.0
        features[f'ret_{h}h'] = ret
    sma_20 = _rolling_mean_2d(close, 20)
    std_20 = _rolling_std_2d(close, 20)
    features['bb_width'] = np.where(sma_20 > 0, 4 * std_20 / sma_20, np.nan)
    tr = (high - low) / np.where(close > 0, close, np.nan)
    features['atr_pct_14'] = _rolling_mean_2d(tr, 14)
    vol_sma = _rolling_mean_2d(volume, 24)
    features['vol_ratio_24'] = np.where(vol_sma > 0, volume / vol_sma, np.nan)
    delta = np.diff(close, axis=0, prepend=np.nan)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = _rolling_mean_2d(gains, 14)
    avg_loss = _rolling_mean_2d(losses, 14)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    features['rsi_14'] = 100.0 - 100.0 / (1.0 + rs)
    return features


def short_window_validation(close, high, low, volume, ts_grid, symbols,
                            deriv_features, deriv_names):
    """Test B: Full derivatives features, 21-day window, single split.

    Train on first 70%, test on last 30%. Reported with caveats:
    - Only 21 days of OI/LS data
    - Single split, not walk-forward
    - Not decision-grade, but indicative
    """
    n_bars, n_symbols = close.shape

    logger.info("\n" + "=" * 70)
    logger.info("TEST B: FULL-FEATURE SHORT-WINDOW VALIDATION")
    logger.info("  CAVEAT: Only ~21 days of OI/LS data. Single split. Not decision-grade.")
    logger.info("=" * 70)

    # Compute targets
    mfh = compute_max_forward_high(close, high, HORIZON)
    _, ignition_mask = build_event_catalog(mfh, IGNITION_THRESHOLD)

    # Baseline features (7-feature set for Test B — broader than Test A's 2-feature baseline)
    baseline_feats = _compute_testb_baseline(close, high, low, volume)
    baseline_names = sorted(baseline_feats.keys())

    # Only use derivatives features that have >10% coverage
    usable_deriv = []
    for fname in deriv_names:
        coverage = np.count_nonzero(~np.isnan(deriv_features[fname])) / (n_bars * n_symbols)
        if coverage > 0.10:
            usable_deriv.append(fname)
    logger.info("  Usable derivatives features (>10%% coverage): %d / %d",
                len(usable_deriv), len(deriv_names))

    all_names = baseline_names + usable_deriv

    # Find the region where derivatives data exists
    has_deriv = ~np.isnan(deriv_features.get('oi_value', np.full((n_bars, n_symbols), np.nan)))
    deriv_rows = np.any(has_deriv, axis=1)
    deriv_start = np.argmax(deriv_rows) if deriv_rows.any() else 0
    deriv_end = n_bars - np.argmax(deriv_rows[::-1]) if deriv_rows.any() else n_bars
    logger.info("  Derivatives data range: bars [%d, %d) of %d total", deriv_start, deriv_end, n_bars)

    # Split: 70/30 within derivatives window
    deriv_len = deriv_end - deriv_start
    split_bar = deriv_start + int(deriv_len * 0.70)

    # Build observation vectors
    train_X, train_y = [], []
    test_X, test_y = [], []

    for t in range(max(WARMUP_BARS, deriv_start), deriv_end):
        for s in range(n_symbols):
            if np.isnan(mfh[t, s]):
                continue
            label = 1 if ignition_mask[t, s] else 0

            row = []
            skip = False
            for fname in baseline_names:
                v = baseline_feats[fname][t, s]
                if np.isnan(v):
                    skip = True
                    break
                row.append(v)
            if skip:
                continue

            for fname in usable_deriv:
                v = deriv_features[fname][t, s]
                if np.isnan(v):
                    skip = True
                    break
                row.append(v)
            if skip:
                continue

            if t < split_bar:
                train_X.append(row)
                train_y.append(label)
            else:
                test_X.append(row)
                test_y.append(label)

    train_X = np.array(train_X) if train_X else np.empty((0, len(all_names)))
    test_X = np.array(test_X) if test_X else np.empty((0, len(all_names)))
    train_y = np.array(train_y)
    test_y = np.array(test_y)

    logger.info("  Train: %d obs (%.2f%% positive)", len(train_y),
                100 * np.mean(train_y) if len(train_y) > 0 else 0)
    logger.info("  Test:  %d obs (%.2f%% positive)", len(test_y),
                100 * np.mean(test_y) if len(test_y) > 0 else 0)

    result = {
        "n_train": len(train_y),
        "n_test": len(test_y),
        "n_features": len(all_names),
        "feature_names": all_names,
        "caveat": "21-day window, single split, not decision-grade",
    }

    if len(train_y) < 50 or len(test_y) < 20:
        logger.warning("  Insufficient observations for Test B")
        result["status"] = "skipped_insufficient_data"
        return result

    if np.sum(train_y) < 3 or np.sum(test_y) < 2:
        logger.warning("  Too few positive examples for Test B")
        result["status"] = "skipped_insufficient_positives"
        return result

    # Fit model
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train_X)
    X_test = scaler.transform(test_X)

    model = LogisticRegression(
        class_weight='balanced', C=0.1, max_iter=1000, random_state=42
    )
    model.fit(X_train, train_y)

    pred = model.predict(X_test)
    proba = model.predict_proba(X_test)[:, 1]

    prec = precision_score(test_y, pred, zero_division=0)
    rec = recall_score(test_y, pred, zero_division=0)
    f1 = f1_score(test_y, pred, zero_division=0)
    ll = log_loss(test_y, proba) if len(np.unique(test_y)) > 1 else 999.0

    logger.info("  RESULTS: precision=%.4f, recall=%.4f, F1=%.4f, log_loss=%.4f",
                prec, rec, f1, ll)

    # Feature importances (logistic regression coefficients)
    coefs = model.coef_[0]
    feat_importance = sorted(zip(all_names, coefs), key=lambda x: -abs(x[1]))
    logger.info("  Top features by |coefficient|:")
    for fname, coef in feat_importance[:10]:
        logger.info("    %s: %.4f", fname, coef)

    result.update({
        "status": "completed",
        "precision": float(prec),
        "recall": float(rec),
        "f1": float(f1),
        "log_loss": float(ll),
        "feature_importance": {fn: float(c) for fn, c in feat_importance},
        "n_positive_train": int(np.sum(train_y)),
        "n_positive_test": int(np.sum(test_y)),
    })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TEST C: CONDITIONAL PRECISION
# ═══════════════════════════════════════════════════════════════════════════════

def conditional_precision_test(close, high, ts_grid, symbols,
                               funding_features, compressed):
    """Test C: Does bearish funding during compression predict ignition better?

    Compares:
      - compression alone → ignition rate
      - compression + bearish funding → ignition rate
      - compression + bullish funding → ignition rate

    Uses Fisher's exact test on the 2x2 table:
      bearish funding | not bearish
      ignited         |  a  |  b
      not ignited     |  c  |  d

    Accept: odds ratio > 1.3 AND Fisher p < 0.10
    """
    n_bars, n_symbols = close.shape

    logger.info("\n" + "=" * 70)
    logger.info("TEST C: CONDITIONAL PRECISION (compression + bearish funding → ignition)")
    logger.info("=" * 70)

    # Compute targets
    mfh = compute_max_forward_high(close, high, HORIZON)
    _, ignition_mask = build_event_catalog(mfh, IGNITION_THRESHOLD)

    # Use funding_rate (or funding_rate_long if available)
    fr_key = 'funding_rate_long' if 'funding_rate_long' in funding_features else 'funding_rate'
    if fr_key not in funding_features:
        logger.error("  No funding rate feature available for Test C")
        return {"status": "no_funding_data"}

    funding_rate = funding_features[fr_key]

    # Define "bearish funding" = funding rate < 0 (shorts paying longs)
    # This indicates bearish sentiment — contrarian signal for ignition
    bearish_funding = funding_rate < 0

    # Select compressed bars with valid targets
    valid = compressed & (~np.isnan(mfh))
    comp_indices = np.where(valid)
    n_compressed = len(comp_indices[0])

    if n_compressed < 30:
        logger.warning("  Only %d compressed bars with valid targets — insufficient", n_compressed)
        return {"status": "insufficient_compressions", "n_compressed": n_compressed}

    # Get funding status and ignition status for compressed bars
    has_funding = ~np.isnan(funding_rate[comp_indices])
    n_with_funding = int(np.sum(has_funding))

    if n_with_funding < 30:
        logger.warning("  Only %d compressed bars with funding data — insufficient", n_with_funding)
        return {"status": "insufficient_funding_overlap", "n_with_funding": n_with_funding}

    # Filter to only those with funding data
    is_bearish = bearish_funding[comp_indices][has_funding]
    is_ignited = ignition_mask[comp_indices][has_funding]

    n_total = len(is_bearish)
    n_bearish = int(np.sum(is_bearish))
    n_bullish = n_total - n_bearish
    n_ignited = int(np.sum(is_ignited))

    logger.info("  Compressed bars with funding: %d", n_total)
    logger.info("  Bearish funding (rate < 0): %d (%.1f%%)", n_bearish, 100 * n_bearish / n_total)
    logger.info("  Ignited overall: %d (%.2f%% base rate)", n_ignited, 100 * n_ignited / n_total)

    # Rates
    bearish_ignited = int(np.sum(is_bearish & is_ignited))
    bearish_not = n_bearish - bearish_ignited
    bullish_ignited = int(np.sum(~is_bearish & is_ignited))
    bullish_not = n_bullish - bullish_ignited

    rate_bearish = bearish_ignited / n_bearish if n_bearish > 0 else 0
    rate_bullish = bullish_ignited / n_bullish if n_bullish > 0 else 0
    rate_overall = n_ignited / n_total if n_total > 0 else 0

    logger.info("  Ignition rate | bearish funding: %.2f%% (%d/%d)",
                100 * rate_bearish, bearish_ignited, n_bearish)
    logger.info("  Ignition rate | bullish funding: %.2f%% (%d/%d)",
                100 * rate_bullish, bullish_ignited, n_bullish)
    logger.info("  Ignition rate | all compressed:  %.2f%% (%d/%d)",
                100 * rate_overall, n_ignited, n_total)

    # Fisher's exact test: 2x2 table
    # [[bearish_ignited, bullish_ignited],
    #  [bearish_not,     bullish_not]]
    table = np.array([[bearish_ignited, bullish_ignited],
                      [bearish_not, bullish_not]])

    odds_ratio, fisher_p = fisher_exact(table, alternative='greater')

    logger.info("\n  Fisher's exact test (one-sided, bearish > bullish):")
    logger.info("  Odds ratio: %.4f", odds_ratio)
    logger.info("  p-value: %.6f", fisher_p)

    # Also test at multiple funding thresholds
    logger.info("\n  Sensitivity analysis (different bearish thresholds):")
    fr_vals = funding_rate[comp_indices][has_funding]
    for threshold_name, threshold_fn in [
        ("rate < -0.0001", lambda x: x < -0.0001),
        ("rate < 0 (baseline)", lambda x: x < 0),
        ("rate < median", lambda x: x < np.nanmedian(fr_vals)),
        ("zscore < -1", None),  # use zscore if available
    ]:
        if threshold_fn is None:
            zs_key = 'funding_rate_zscore_long' if 'funding_rate_zscore_long' in funding_features else 'funding_rate_zscore'
            if zs_key not in funding_features:
                continue
            zs = funding_features[zs_key][comp_indices][has_funding]
            if np.all(np.isnan(zs)):
                continue
            is_bear = zs < -1
        else:
            is_bear = threshold_fn(fr_vals)

        n_b = int(np.sum(is_bear))
        n_nb = n_total - n_b
        if n_b < 5 or n_nb < 5:
            continue

        bi = int(np.sum(is_bear & is_ignited))
        nbi = int(np.sum(~is_bear & is_ignited))
        tbl = np.array([[bi, nbi], [n_b - bi, n_nb - nbi]])
        odr, fp = fisher_exact(tbl, alternative='greater')
        rate_b = bi / n_b if n_b > 0 else 0
        logger.info("    %s: rate=%.2f%% (%d/%d), OR=%.3f, p=%.4f",
                    threshold_name, 100 * rate_b, bi, n_b, odr, fp)

    # Verdict
    accept_c = odds_ratio > 1.3 and fisher_p < 0.10
    logger.info("\n  TEST C VERDICT: %s (OR=%.3f %s 1.3, p=%.4f %s 0.10)",
                "ACCEPT" if accept_c else "REJECT",
                odds_ratio, ">" if odds_ratio > 1.3 else "<=",
                fisher_p, "<" if fisher_p < 0.10 else ">=")

    return {
        "status": "completed",
        "n_compressed_with_funding": n_total,
        "n_bearish": n_bearish,
        "n_bullish": n_bullish,
        "rate_bearish_ignition": float(rate_bearish),
        "rate_bullish_ignition": float(rate_bullish),
        "rate_overall_ignition": float(rate_overall),
        "odds_ratio": float(odds_ratio),
        "fisher_p": float(fisher_p),
        "contingency_table": table.tolist(),
        "accept": accept_c,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def feature_correlation_analysis(features_dict, feature_names, redundancy_threshold=0.8):
    """Pairwise Spearman correlation between derivatives features.

    Flags pairs with |ρ| > threshold as redundant.
    Reports the full matrix.
    """
    logger.info("\n" + "=" * 70)
    logger.info("FEATURE CORRELATION ANALYSIS")
    logger.info("  Redundancy threshold: |ρ| > %.2f", redundancy_threshold)
    logger.info("=" * 70)

    # Pool all valid observations per feature
    flat_features = {}
    for fname in feature_names:
        if fname not in features_dict:
            continue
        arr = features_dict[fname]
        flat = arr.flatten()
        valid = ~np.isnan(flat)
        flat_features[fname] = (flat, valid)

    usable_names = [fn for fn in feature_names if fn in flat_features]

    if len(usable_names) < 2:
        logger.info("  Only %d features — skipping correlation analysis", len(usable_names))
        return {"status": "insufficient_features", "n_features": len(usable_names)}

    # Compute pairwise Spearman
    n_feats = len(usable_names)
    corr_matrix = np.full((n_feats, n_feats), np.nan)
    redundant_pairs = []

    for i in range(n_feats):
        for j in range(i, n_feats):
            if i == j:
                corr_matrix[i, j] = 1.0
                continue

            fi, vi = flat_features[usable_names[i]]
            fj, vj = flat_features[usable_names[j]]
            both_valid = vi & vj
            n_valid = int(np.sum(both_valid))

            if n_valid < 100:
                continue

            rho, p = spearmanr(fi[both_valid], fj[both_valid])
            corr_matrix[i, j] = rho
            corr_matrix[j, i] = rho

            if abs(rho) > redundancy_threshold:
                redundant_pairs.append((usable_names[i], usable_names[j], float(rho)))

    # Print matrix
    logger.info("\n  Correlation matrix (Spearman ρ):")
    # Abbreviated names for display
    abbrevs = [fn[:20] for fn in usable_names]
    header = "%-22s " + " ".join(f"{a[:7]:>7s}" for a in abbrevs)
    logger.info("  " + header, "")
    for i, fn in enumerate(usable_names):
        row_str = " ".join(
            f"{corr_matrix[i, j]:+7.3f}" if not np.isnan(corr_matrix[i, j]) else "    ---"
            for j in range(n_feats)
        )
        logger.info("  %-22s %s", fn[:22], row_str)

    # Report redundancy
    if redundant_pairs:
        logger.info("\n  REDUNDANT PAIRS (|ρ| > %.2f):", redundancy_threshold)
        for fn1, fn2, rho in sorted(redundant_pairs, key=lambda x: -abs(x[2])):
            logger.info("    %s ↔ %s: ρ = %+.4f", fn1, fn2, rho)
        logger.info("  Recommendation: drop one from each pair before model fitting")
    else:
        logger.info("\n  No redundant pairs found at threshold %.2f", redundancy_threshold)

    return {
        "status": "completed",
        "feature_names": usable_names,
        "correlation_matrix": [[float(x) if not np.isnan(x) else None
                                for x in corr_matrix[i]]
                               for i in range(n_feats)],
        "redundant_pairs": [{"f1": p[0], "f2": p[1], "rho": p[2]} for p in redundant_pairs],
        "n_redundant": len(redundant_pairs),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _serialize(obj):
    """Ensure JSON-serializable."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _write_results(data, filename="derivatives_walkforward_results.json"):
    out_path = os.path.join(os.path.dirname(__file__), filename)
    with open(out_path, "w") as f:
        json.dump(_serialize(data), f, indent=2, default=str)
    logger.info("Results written to %s", out_path)


def main():
    parser = argparse.ArgumentParser(description="Derivatives Walk-Forward Validation")
    parser.add_argument("--days", type=int, default=140,
                        help="OHLCV lookback days for Tests B/C (default: 140)")
    parser.add_argument("--funding-days", type=int, default=400,
                        help="OHLCV lookback for Test A funding-only (default: 400)")
    parser.add_argument("--n-folds", type=int, default=3,
                        help="Walk-forward folds for Test A (default: 3)")
    parser.add_argument("--test-a-only", action="store_true",
                        help="Run only Test A")
    parser.add_argument("--test-c-only", action="store_true",
                        help="Run only Test C")
    args = parser.parse_args()

    database_url = os.environ["DATABASE_URL"]

    logger.info("=" * 70)
    logger.info("DERIVATIVES WALK-FORWARD VALIDATION")
    logger.info("=" * 70)
    logger.info("Date: %s", datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    logger.info("Config: days=%d, funding_days=%d, n_folds=%d",
                args.days, args.funding_days, args.n_folds)
    logger.info("")

    t_total = time.time()
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {"days": args.days, "funding_days": args.funding_days, "n_folds": args.n_folds},
    }

    # ══════════════════════════════════════════════════════════════════════════
    # TEST A: FUNDING-ONLY WALK-FORWARD (long window)
    # ══════════════════════════════════════════════════════════════════════════

    if not args.test_c_only:
        logger.info("Loading OHLCV for Test A (%d days)...", args.funding_days)
        close_long, open_long, high_long, low_long, vol_long, ts_grid_long, \
            symbols_long, btc_idx_long = load_ohlcv_matrices(database_url, args.funding_days)

        logger.info("Computing funding features from raw data (long window)...")
        funding_long = load_funding_features_long(database_url, symbols_long, ts_grid_long)

        test_a_results = walk_forward_funding(
            close_long, high_long, low_long, vol_long, ts_grid_long, symbols_long,
            funding_long, n_folds=args.n_folds, embargo_bars=48,
        )
        results["test_a"] = test_a_results

    if args.test_a_only:
        results["elapsed_s"] = time.time() - t_total
        _write_results(results)
        return

    # ══════════════════════════════════════════════════════════════════════════
    # Load short-window data for Tests B and C
    # ══════════════════════════════════════════════════════════════════════════

    logger.info("\nLoading OHLCV for Tests B/C (%d days)...", args.days)
    close, open_, high, low, volume, ts_grid, symbols, btc_idx = \
        load_ohlcv_matrices(database_url, args.days)

    logger.info("Loading derivatives features (short window)...")
    deriv_features, deriv_names = load_derivatives_features(database_url, symbols, ts_grid)

    logger.info("Loading funding features (long window, aligned to short grid)...")
    funding_short = load_funding_features_long(database_url, symbols, ts_grid)

    # Merge funding into deriv_features for Test C
    all_features = dict(deriv_features)
    all_features.update(funding_short)

    # ══════════════════════════════════════════════════════════════════════════
    # CORRELATION ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════

    if not args.test_c_only:
        all_feature_names = deriv_names + list(funding_short.keys())
        corr_results = feature_correlation_analysis(all_features, all_feature_names)
        results["correlation"] = corr_results

    # ══════════════════════════════════════════════════════════════════════════
    # TEST B: FULL-FEATURE SHORT-WINDOW
    # ══════════════════════════════════════════════════════════════════════════

    if not args.test_c_only:
        test_b_results = short_window_validation(
            close, high, low, volume, ts_grid, symbols,
            deriv_features, deriv_names,
        )
        results["test_b"] = test_b_results

    # ══════════════════════════════════════════════════════════════════════════
    # TEST C: CONDITIONAL PRECISION
    # ══════════════════════════════════════════════════════════════════════════

    logger.info("\nDetecting compressions for Test C...")
    compressed = detect_compressions(close)

    test_c_results = conditional_precision_test(
        close, high, ts_grid, symbols,
        all_features, compressed,
    )
    results["test_c"] = test_c_results

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ══════════════════════════════════════════════════════════════════════════

    elapsed = time.time() - t_total

    logger.info("\n" + "=" * 70)
    logger.info("FINAL VERDICT")
    logger.info("=" * 70)

    # Test A criterion: precision >= 1pp in 2/3+ folds
    test_a_pass = False
    if "test_a" in results and results["test_a"]:
        deltas = [r["precision_delta_pp"] for r in results["test_a"]]
        improved = sum(1 for d in deltas if d >= 1.0)
        test_a_pass = improved >= 2
        logger.info("Test A (funding walk-forward): %d/%d folds with >= 1pp precision improvement → %s",
                    improved, len(deltas), "PASS" if test_a_pass else "FAIL")
        for i, r in enumerate(results["test_a"]):
            logger.info("  Fold %d: baseline=%.4f, enhanced=%.4f, delta=%+.2fpp  "
                        "lift_base=%.2f lift_enh=%.2f  McNemar p=%.4f",
                        i, r["baseline"]["precision"], r["enhanced"]["precision"],
                        r["precision_delta_pp"],
                        r["baseline"]["lift_top10"], r["enhanced"]["lift_top10"],
                        r["mcnemar_p"])

    # Test C criterion: OR > 1.3 AND Fisher p < 0.10
    test_c_pass = False
    if "test_c" in results and results["test_c"].get("status") == "completed":
        tc = results["test_c"]
        test_c_pass = tc.get("accept", False)
        logger.info("Test C (conditional precision): OR=%.3f, p=%.4f → %s",
                    tc["odds_ratio"], tc["fisher_p"],
                    "PASS" if test_c_pass else "FAIL")

    # Test B (informational only)
    if "test_b" in results and results["test_b"].get("status") == "completed":
        tb = results["test_b"]
        logger.info("Test B (short-window, informational): precision=%.4f, F1=%.4f [CAVEAT: not decision-grade]",
                    tb["precision"], tb["f1"])

    # Overall verdict
    accept = test_a_pass or test_c_pass
    logger.info("")
    if accept:
        reasons = []
        if test_a_pass:
            reasons.append("Test A passed (funding features improve precision in walk-forward)")
        if test_c_pass:
            reasons.append("Test C passed (bearish funding during compression → higher ignition rate)")
        logger.info("+--------------------------------------------------------------+")
        logger.info("|  ACCEPT: Derivatives features provide predictive value.       |")
        for r in reasons:
            logger.info("|  - %-56s |", r)
        logger.info("|  Next: Integrate funding features into ignition model.        |")
        logger.info("+--------------------------------------------------------------+")
    else:
        logger.info("+--------------------------------------------------------------+")
        logger.info("|  REJECT: Derivatives features do not meet acceptance criteria.|")
        logger.info("|  Neither Test A nor Test C passed.                            |")
        logger.info("|  Recommendation: Keep collector running. Revisit with more    |")
        logger.info("|  data or alternative feature engineering.                     |")
        logger.info("+--------------------------------------------------------------+")

    results["verdict"] = {
        "accept": accept,
        "test_a_pass": test_a_pass,
        "test_c_pass": test_c_pass,
        "reasons": [],
    }
    if test_a_pass:
        results["verdict"]["reasons"].append("Test A: precision improvement in walk-forward")
    if test_c_pass:
        results["verdict"]["reasons"].append("Test C: conditional precision improvement")

    results["elapsed_s"] = elapsed
    logger.info("\nTotal runtime: %.1fs", elapsed)

    _write_results(results)


if __name__ == "__main__":
    main()
