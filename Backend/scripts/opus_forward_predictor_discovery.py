#!/usr/bin/env python3
"""
Opus 4.6 Forward Predictor Discovery
=====================================
First-principles feature engineering from OHLCV + social data.
5-fold purged walk-forward validation with DSR correction.
Average daily PF as success metric.

Per CLAUDE.md Section 0.1: "Start from the data and find what works.
If you discover a pattern that has no name in any textbook, that's not
a problem — it's the point."

This script does NOT start from named indicators. It examines:
1. Intrabar microstructure (wick ratios, body position within range)
2. Cross-symbol information flow (BTC→altcoin lead/lag)
3. Social-price coupling dynamics (divergence, regime transitions)
4. Return autocorrelation structure at multiple lags
5. Volume-weighted price dispersion
6. Cross-timeframe momentum alignment
7. Author concentration → contrarian signals
8. Social velocity vs price velocity at multiple timescales

Parallelism: Phase 1 serial (I/O), Phase 2 vectorised numpy,
Phase 3 multiprocessing across walk-forward folds.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from multiprocessing import Pool
from typing import Any

import numpy as np

# --- Execution context detection ---
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    DB_URL_DEFAULT = os.getenv("DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading")
else:
    DB_URL_DEFAULT = os.getenv("DATABASE_URL", "postgresql://trading:trading@localhost:5432/trading")


# ======================================================================
# PHASE 0: Configuration
# ======================================================================

# Top symbols by social + OHLCV coverage (must have both)
TOP_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "BNBUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
    "PEPEUSDT", "SUIUSDT", "HBARUSDT", "XLMUSDT", "SHIBUSDT",
    "TRXUSDT", "LTCUSDT", "NEARUSDT", "MATICUSDT", "UNIUSDT",
]

HORIZONS = [6, 24, 72]  # 6h, 24h, 72h forward return at 1h bars
PURGE_BARS = 24  # 1-day embargo between train and test
MIN_TRAIN = 2000  # Minimum training records per fold
MIN_TEST = 500   # Minimum test records per fold
N_FOLDS = 5
TIMEFRAME = "1h"

# Transaction cost model (CLAUDE.md Section 4.4)
SLIPPAGE_BPS = 5.0   # 1h timeframe
FEE_BPS = 4.0        # Binance taker
ROUND_TRIP_BPS = 2.0 * (SLIPPAGE_BPS + FEE_BPS)  # 18 bps

# Cost multipliers for robustness testing
COST_MULTIPLIERS = [1.0, 1.5, 2.0]


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ======================================================================
# PHASE 1: Data Loading (serial, I/O bound)
# ======================================================================

def load_ohlcv_matrix(conn, symbols: list[str], start_ts: datetime, end_ts: datetime):
    """Load OHLCV into aligned (T, N) matrices. T=timestamps, N=symbols."""
    import psycopg
    from psycopg.rows import dict_row

    log(f"Loading OHLCV for {len(symbols)} symbols...")
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT symbol, bar_start_ts, open, high, low, close, volume
        FROM (
            SELECT symbol, bar_start_ts, open, high, low, close, volume,
                   ROW_NUMBER() OVER (PARTITION BY symbol, bar_start_ts ORDER BY as_of_ts DESC) AS rn
            FROM ohlcv
            WHERE symbol = ANY(%s)
              AND timeframe = '1h'
              AND is_final = true
              AND bar_start_ts >= %s AND bar_start_ts < %s
        ) q WHERE rn = 1
        ORDER BY bar_start_ts, symbol;
    """, (symbols, start_ts, end_ts))
    rows = cur.fetchall()
    cur.close()
    log(f"  Loaded {len(rows)} OHLCV rows")

    # Build aligned matrices
    sym_idx = {s: i for i, s in enumerate(symbols)}
    all_ts = sorted(set(r["bar_start_ts"] for r in rows))
    ts_idx = {t: i for i, t in enumerate(all_ts)}

    T, N = len(all_ts), len(symbols)
    open_mat = np.full((T, N), np.nan)
    high_mat = np.full((T, N), np.nan)
    low_mat = np.full((T, N), np.nan)
    close_mat = np.full((T, N), np.nan)
    volume_mat = np.full((T, N), np.nan)

    for r in rows:
        t = ts_idx[r["bar_start_ts"]]
        n = sym_idx.get(r["symbol"])
        if n is None:
            continue
        open_mat[t, n] = float(r["open"])
        high_mat[t, n] = float(r["high"])
        low_mat[t, n] = float(r["low"])
        close_mat[t, n] = float(r["close"])
        volume_mat[t, n] = float(r["volume"])

    log(f"  OHLCV matrix: ({T}, {N}), fill rate: {np.isfinite(close_mat).mean():.2%}")
    return {
        "timestamps": all_ts,
        "symbols": symbols,
        "open": open_mat,
        "high": high_mat,
        "low": low_mat,
        "close": close_mat,
        "volume": volume_mat,
    }


def load_social_matrix(conn, symbols: list[str], timestamps: list[datetime]):
    """Load social features into aligned (T, N, F) matrix."""
    import psycopg
    from psycopg.rows import dict_row

    log(f"Loading social features for {len(symbols)} symbols...")
    social_cols = [
        "mention_count", "unique_authors", "platform_count",
        "engagement_sum", "mention_velocity", "mention_acceleration",
        "author_novelty_ratio", "mention_volume_ratio",
    ]
    F = len(social_cols)
    T, N = len(timestamps), len(symbols)

    social_mat = np.full((T, N, F), 0.0)  # Default to 0 (no social activity)

    sym_idx = {s: i for i, s in enumerate(symbols)}
    ts_idx = {t: i for i, t in enumerate(timestamps)}

    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT symbol, bar_start_ts, mention_count, unique_authors,
               platform_count, engagement_sum, mention_velocity,
               mention_acceleration, author_novelty_ratio, mention_volume_ratio
        FROM social_features_1h
        WHERE symbol = ANY(%s)
          AND bar_start_ts >= %s AND bar_start_ts < %s
        ORDER BY bar_start_ts, symbol;
    """, (symbols, timestamps[0], timestamps[-1] + timedelta(hours=1)))
    rows = cur.fetchall()
    cur.close()
    log(f"  Loaded {len(rows)} social feature rows")

    for r in rows:
        t = ts_idx.get(r["bar_start_ts"])
        n = sym_idx.get(r["symbol"])
        if t is None or n is None:
            continue
        for f_i, col in enumerate(social_cols):
            val = r.get(col)
            if val is not None:
                social_mat[t, n, f_i] = float(val)

    coverage = np.sum(social_mat[:, :, 0] > 0) / max(1, T * N)
    log(f"  Social matrix: ({T}, {N}, {F}), coverage: {coverage:.2%}")
    return social_mat, social_cols


# ======================================================================
# PHASE 2: Feature Engineering (vectorised numpy)
# ======================================================================

def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    """Rolling mean along axis 0. Cumsum-based, no Python loops."""
    cs = np.nancumsum(x, axis=0)
    cnt = np.nancumsum(np.isfinite(x).astype(float), axis=0)
    out = np.full_like(x, np.nan)
    out[w - 1:] = (cs[w - 1:] - np.concatenate([np.zeros_like(cs[:1]), cs[:-1]], axis=0)[w - 1:])
    cnt_w = cnt[w - 1:] - np.concatenate([np.zeros_like(cnt[:1]), cnt[:-1]], axis=0)[w - 1:]
    cnt_w[cnt_w == 0] = np.nan
    out[w - 1:] /= cnt_w
    return out


def _rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    """Rolling std along axis 0."""
    mean = _rolling_mean(x, w)
    sq_mean = _rolling_mean(x ** 2, w)
    var = sq_mean - mean ** 2
    var = np.clip(var, 0, None)
    return np.sqrt(var)


def _lag(x: np.ndarray, n: int) -> np.ndarray:
    """Lag array by n positions along axis 0."""
    out = np.full_like(x, np.nan)
    if n > 0:
        out[n:] = x[:-n]
    elif n < 0:
        out[:n] = x[-n:]
    else:
        out[:] = x
    return out


def _lead(x: np.ndarray, n: int) -> np.ndarray:
    return _lag(x, -n)


def _rolling_corr(x: np.ndarray, y: np.ndarray, w: int) -> np.ndarray:
    """Rolling Pearson correlation between x and y along axis 0."""
    mx = _rolling_mean(x, w)
    my = _rolling_mean(y, w)
    mxy = _rolling_mean(x * y, w)
    sx = _rolling_std(x, w)
    sy = _rolling_std(y, w)
    denom = sx * sy
    denom[denom == 0] = np.nan
    return (mxy - mx * my) / denom


def _cross_sectional_rank(x: np.ndarray) -> np.ndarray:
    """Rank each row cross-sectionally. Returns ranks in [0, 1]."""
    out = np.full_like(x, np.nan)
    for t in range(x.shape[0]):
        row = x[t]
        valid = np.isfinite(row)
        if valid.sum() < 2:
            continue
        ranks = np.zeros_like(row)
        order = np.argsort(row[valid])
        ranks_valid = np.zeros(valid.sum())
        ranks_valid[order] = np.arange(valid.sum(), dtype=float)
        ranks_valid /= max(1, valid.sum() - 1)
        ranks[valid] = ranks_valid
        ranks[~valid] = np.nan
        out[t] = ranks
    return out


def engineer_features(ohlcv: dict, social_mat: np.ndarray, social_cols: list[str]) -> tuple[np.ndarray, list[str]]:
    """Engineer features from first principles.

    Returns (T, N, n_features) array and feature names.
    Every feature is computed as numpy matrix ops — no Python loops
    over (bars × symbols).
    """
    log("Engineering features from first principles...")
    t0 = time.time()

    O = ohlcv["open"]
    H = ohlcv["high"]
    L = ohlcv["low"]
    C = ohlcv["close"]
    V = ohlcv["volume"]
    T, N = C.shape

    features = {}

    # --- 1. Returns at multiple timescales ---
    ret_1 = C / _lag(C, 1) - 1.0  # 1h return
    ret_6 = C / _lag(C, 6) - 1.0  # 6h return
    ret_24 = C / _lag(C, 24) - 1.0  # 24h return
    ret_72 = C / _lag(C, 72) - 1.0  # 72h return

    features["ret_1h"] = ret_1
    features["ret_6h"] = ret_6
    features["ret_24h"] = ret_24
    features["ret_72h"] = ret_72

    # --- 2. Intrabar microstructure ---
    # Bar body position within range: where does close sit in [low, high]?
    bar_range = H - L
    bar_range[bar_range == 0] = np.nan
    body_position = (C - L) / bar_range  # 0=closed at low, 1=closed at high

    # Upper wick ratio: how much of the range is above the body?
    body_top = np.maximum(O, C)
    upper_wick = (H - body_top) / bar_range

    # Lower wick ratio
    body_bot = np.minimum(O, C)
    lower_wick = (body_bot - L) / bar_range

    # Body size as fraction of range
    body_size = np.abs(C - O) / bar_range

    features["body_position"] = body_position
    features["upper_wick_ratio"] = upper_wick
    features["lower_wick_ratio"] = lower_wick
    features["body_size_ratio"] = body_size

    # Rolling averages of microstructure (detect regime changes)
    features["body_position_6h_avg"] = _rolling_mean(body_position, 6)
    features["upper_wick_6h_trend"] = upper_wick - _rolling_mean(upper_wick, 24)

    # --- 3. Volatility dynamics (not just level — the SPEED of change) ---
    realized_vol_24 = _rolling_std(ret_1, 24)
    realized_vol_72 = _rolling_std(ret_1, 72)

    # Volatility regime ratio
    vol_regime = realized_vol_24 / np.where(realized_vol_72 > 0, realized_vol_72, np.nan)

    # Volatility acceleration: how fast is vol changing?
    vol_change_6h = realized_vol_24 - _lag(realized_vol_24, 6)
    vol_change_24h = realized_vol_24 - _lag(realized_vol_24, 24)
    vol_accel = vol_change_6h - _lag(vol_change_6h, 6)  # Second derivative

    features["realized_vol_24"] = realized_vol_24
    features["vol_regime_ratio"] = vol_regime
    features["vol_acceleration"] = vol_accel
    features["vol_change_speed_24h"] = vol_change_24h

    # --- 4. Volume dynamics ---
    vol_ma_24 = _rolling_mean(V, 24)
    vol_ma_72 = _rolling_mean(V, 72)
    relative_volume = V / np.where(vol_ma_24 > 0, vol_ma_24, np.nan)

    # Volume-weighted price dispersion: is volume concentrated at one price
    # or spread across the range?
    vwap_proxy = (H + L + C) / 3.0  # Simplified VWAP
    price_vol_spread = np.abs(C - vwap_proxy) / np.where(bar_range > 0, bar_range, np.nan)

    features["relative_volume_24"] = relative_volume
    features["volume_regime"] = vol_ma_24 / np.where(vol_ma_72 > 0, vol_ma_72, np.nan)
    features["price_volume_spread"] = price_vol_spread

    # --- 5. Return autocorrelation structure ---
    # At what lag does autocorrelation flip? This is regime-specific.
    autocorr_1 = _rolling_corr(ret_1, _lag(ret_1, 1), 24)  # 1-bar lag
    autocorr_6 = _rolling_corr(ret_1, _lag(ret_1, 6), 24)  # 6-bar lag
    autocorr_24 = _rolling_corr(ret_1, _lag(ret_1, 24), 72)  # 24-bar lag

    features["autocorr_lag1_24w"] = autocorr_1
    features["autocorr_lag6_24w"] = autocorr_6
    features["autocorr_lag24_72w"] = autocorr_24

    # Autocorrelation flip: positive at short lag, negative at long lag = mean-reversion
    features["autocorr_flip_signal"] = autocorr_1 - autocorr_24

    # --- 6. Cross-symbol information flow ---
    # BTC is column 0. How does BTC's return predict other symbols?
    btc_ret_1 = ret_1[:, 0:1]  # (T, 1) — broadcast across all symbols
    btc_ret_6 = ret_6[:, 0:1]

    # Lagged BTC return → current symbol return correlation
    btc_lead_corr = _rolling_corr(_lag(btc_ret_1, 1) * np.ones((1, N)), ret_1, 72)
    features["btc_lead_correlation_72w"] = btc_lead_corr

    # BTC momentum divergence: symbol ret - BTC ret (idiosyncratic return)
    features["idiosyncratic_ret_6h"] = ret_6 - btc_ret_6

    # Cross-sectional rank of return (where does this symbol rank vs peers?)
    features["cs_rank_ret_24h"] = _cross_sectional_rank(ret_24)
    features["cs_rank_volume_24h"] = _cross_sectional_rank(relative_volume)

    # --- 7. Social features (from social_mat) ---
    # social_mat shape: (T, N, F)
    # social_cols: mention_count(0), unique_authors(1), platform_count(2),
    #   engagement_sum(3), mention_velocity(4), mention_acceleration(5),
    #   author_novelty_ratio(6), mention_volume_ratio(7)

    mentions = social_mat[:, :, 0]  # (T, N)
    authors = social_mat[:, :, 1]
    platforms = social_mat[:, :, 2]
    engagement = social_mat[:, :, 3]
    mention_vel = social_mat[:, :, 4]
    mention_accel = social_mat[:, :, 5]
    author_novelty = social_mat[:, :, 6]
    mention_vol_ratio = social_mat[:, :, 7]

    # Rolling social features
    mentions_6h = _rolling_mean(mentions, 6)
    mentions_24h = _rolling_mean(mentions, 24)
    authors_24h = _rolling_mean(authors, 24)

    features["mentions_6h_avg"] = mentions_6h
    features["mentions_24h_avg"] = mentions_24h
    features["author_novelty_24h_avg"] = _rolling_mean(author_novelty, 24)

    # --- 8. Social-price coupling dynamics ---
    # Z-score of mentions over 72h window
    mentions_z = (mentions_6h - _rolling_mean(mentions_6h, 72)) / np.where(
        _rolling_std(mentions_6h, 72) > 0, _rolling_std(mentions_6h, 72), np.nan
    )
    # Z-score of returns over 72h window
    ret_z = (ret_6 - _rolling_mean(ret_6, 72)) / np.where(
        _rolling_std(ret_6, 72) > 0, _rolling_std(ret_6, 72), np.nan
    )

    # Social-price divergence: social screaming but price quiet
    features["social_price_divergence"] = mentions_z - np.abs(ret_z)

    # Social-price coupling strength (rolling correlation)
    features["social_price_coupling_72w"] = _rolling_corr(mentions, np.abs(ret_1), 72)

    # Coupling CHANGE — when does social decouple from price?
    coupling_now = _rolling_corr(mentions, np.abs(ret_1), 24)
    coupling_prev = _lag(_rolling_corr(mentions, np.abs(ret_1), 24), 24)
    features["coupling_regime_change"] = coupling_now - coupling_prev

    # --- 9. Author concentration → contrarian signal ---
    # When few authors dominate, information content is low
    author_concentration = np.where(
        authors_24h > 0,
        mentions_24h / authors_24h,  # posts per author (high = concentrated)
        np.nan
    )
    features["author_concentration_24h"] = author_concentration

    # Concentration change (transition from diverse to concentrated = bearish?)
    features["concentration_change_24h"] = author_concentration - _lag(author_concentration, 24)

    # --- 10. Social velocity vs price velocity at multiple timescales ---
    # Social momentum relative to price momentum
    mention_momentum_6h = mention_vel  # Already velocity
    price_momentum_6h = ret_6

    # Normalised divergence
    mm_z = (mention_momentum_6h - _rolling_mean(mention_momentum_6h, 72)) / np.where(
        _rolling_std(mention_momentum_6h, 72) > 0, _rolling_std(mention_momentum_6h, 72), np.nan
    )
    pm_z = (price_momentum_6h - _rolling_mean(price_momentum_6h, 72)) / np.where(
        _rolling_std(price_momentum_6h, 72) > 0, _rolling_std(price_momentum_6h, 72), np.nan
    )
    features["social_vs_price_momentum_div"] = mm_z - pm_z

    # Mention acceleration (second derivative of social) — panic/euphoria detector
    features["mention_acceleration_raw"] = mention_accel
    features["mention_accel_z_72w"] = (mention_accel - _rolling_mean(mention_accel, 72)) / np.where(
        _rolling_std(mention_accel, 72) > 0, _rolling_std(mention_accel, 72), np.nan
    )

    # --- 11. Multi-feature interaction terms ---
    # These explore regions of feature space no human would check

    # Vol-attention coupling: high vol AND high social attention → different from either alone
    features["vol_attention_coupling"] = realized_vol_24 * mentions_z

    # Trapped buyers signal: upper wick + volume spike + return reversal
    features["trapped_buyers"] = upper_wick * relative_volume * np.where(ret_1 < 0, -ret_1, 0)

    # Panic signal: mention acceleration + volatility spike + negative return
    features["panic_composite"] = np.where(
        mention_accel > 0,
        mention_accel * vol_accel * np.where(ret_1 < 0, 1.0, 0.0),
        0.0
    )

    # Information flow: BTC social leads altcoin price?
    btc_mention_vel = mention_vel[:, 0:1]  # BTC's mention velocity
    features["btc_social_lead"] = _rolling_corr(
        _lag(btc_mention_vel * np.ones((1, N)), 3), ret_1, 72
    )

    # Stack all features into (T, N, n_features) array
    feature_names = sorted(features.keys())
    feat_array = np.stack([features[name] for name in feature_names], axis=2)

    # Clip extreme values to prevent numerical issues
    feat_array = np.clip(feat_array, -100, 100)

    elapsed = time.time() - t0
    log(f"  Engineered {len(feature_names)} features in {elapsed:.1f}s")
    log(f"  Feature matrix: ({feat_array.shape[0]}, {feat_array.shape[1]}, {feat_array.shape[2]})")
    return feat_array, feature_names


# ======================================================================
# PHASE 3: Walk-Forward Validation
# ======================================================================

def compute_forward_returns(close_mat: np.ndarray, horizon: int, cost_bps: float = ROUND_TRIP_BPS) -> np.ndarray:
    """Compute forward returns with transaction costs baked in."""
    future_close = _lead(close_mat, horizon)
    gross_ret = future_close / close_mat - 1.0
    return gross_ret - (cost_bps / 10000.0)


def _fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float = 1e-2) -> np.ndarray:
    """Ridge regression. Returns weight vector."""
    xtx = X.T @ X
    reg = alpha * np.eye(xtx.shape[0])
    return np.linalg.solve(xtx + reg, X.T @ y)


def _evaluate_fold(args: tuple) -> dict:
    """Evaluate a single walk-forward fold. Designed for multiprocessing."""
    fold_i, X_train, y_train, X_test, y_test, test_timestamps, feature_names = args

    # Standardise using training stats only
    mean = np.nanmean(X_train, axis=0)
    std = np.nanstd(X_train, axis=0)
    std[std == 0] = 1.0

    Xn_train = (X_train - mean) / std
    Xn_test = (X_test - mean) / std

    # Replace NaN with 0 (missing features get neutral value)
    Xn_train = np.nan_to_num(Xn_train, nan=0.0)
    Xn_test = np.nan_to_num(Xn_test, nan=0.0)
    y_train_clean = np.nan_to_num(y_train, nan=0.0)

    # Fit ridge regression
    w = _fit_ridge(Xn_train, y_train_clean, alpha=1e-2)
    y_pred = Xn_test @ w

    # Compute per-trade PnL (directional: go long if pred > 0, short if pred < 0)
    direction = np.sign(y_pred)
    pnl = direction * y_test

    # Classification metrics
    y_true_dir = (y_test > 0).astype(float)
    y_pred_dir = (y_pred > 0).astype(float)
    valid = np.isfinite(y_test)
    if valid.sum() == 0:
        return {"fold": fold_i, "error": "no valid test data"}

    pnl_valid = pnl[valid]
    ts_valid = [test_timestamps[i] for i in range(len(test_timestamps)) if valid[i]]

    accuracy = float(np.mean(y_true_dir[valid] == y_pred_dir[valid]))
    rmse = float(np.sqrt(np.mean((y_pred[valid] - y_test[valid]) ** 2)))

    # Feature importances (absolute weight magnitude)
    feat_importance = {feature_names[i]: float(np.abs(w[i])) for i in range(len(w))}

    # Daily PF
    daily_pnl: dict[str, list[float]] = defaultdict(list)
    for ts, p in zip(ts_valid, pnl_valid):
        daily_pnl[ts.strftime("%Y-%m-%d")].append(float(p))

    daily_pfs = []
    for day_trades in daily_pnl.values():
        gross_profit = sum(p for p in day_trades if p > 0)
        gross_loss = abs(sum(p for p in day_trades if p < 0))
        if gross_loss > 0:
            daily_pfs.append(gross_profit / gross_loss)
        elif gross_profit > 0:
            daily_pfs.append(5.0)

    avg_daily_pf = float(np.mean(daily_pfs)) if daily_pfs else 0.0
    total_profit = sum(float(p) for p in pnl_valid if p > 0)
    total_loss = abs(sum(float(p) for p in pnl_valid if p < 0))
    aggregate_pf = total_profit / total_loss if total_loss > 0 else 0.0

    return {
        "fold": fold_i,
        "train_n": int(len(y_train)),
        "test_n": int(valid.sum()),
        "accuracy": accuracy,
        "rmse": rmse,
        "avg_daily_pf": avg_daily_pf,
        "aggregate_pf": aggregate_pf,
        "trading_days": len(daily_pfs),
        "pf_gt1_pct": float(np.mean([1.0 if pf > 1.0 else 0.0 for pf in daily_pfs])) if daily_pfs else 0.0,
        "mean_pnl": float(np.mean(pnl_valid)),
        "sharpe": float(np.mean(pnl_valid) / max(1e-10, np.std(pnl_valid))),
        "top_features": dict(sorted(feat_importance.items(), key=lambda x: -x[1])[:10]),
        "pnl_values": [float(p) for p in pnl_valid],
    }


def run_walk_forward(
    feat_array: np.ndarray,
    target_array: np.ndarray,
    timestamps: list[datetime],
    feature_names: list[str],
    *,
    n_folds: int = N_FOLDS,
    purge_bars: int = PURGE_BARS,
    min_train: int = MIN_TRAIN,
    min_test: int = MIN_TEST,
    n_workers: int = 5,
) -> dict:
    """Run purged walk-forward validation across all symbols.

    Flattens (T, N) matrices to (T*N,) for pooled cross-sectional regression.
    """
    T, N, F = feat_array.shape

    # Flatten: each (timestamp, symbol) is one observation
    X_flat = feat_array.reshape(T * N, F)
    y_flat = target_array.reshape(T * N)
    ts_flat = []
    for t in range(T):
        for n in range(N):
            ts_flat.append(timestamps[t])

    # Replace NaN features with 0 (NaN = "no signal" = neutral value)
    # This is essential because social features have ~30% coverage,
    # and rolling windows need warmup. Requiring all features non-NaN
    # would discard >99% of data.
    nan_rate = np.isnan(X_flat).mean()
    X_flat = np.nan_to_num(X_flat, nan=0.0, posinf=0.0, neginf=0.0)
    log(f"  Feature NaN rate before imputation: {nan_rate:.1%}")

    # Only require target (forward return) to be valid
    valid_mask = np.isfinite(y_flat)
    valid_idx = np.where(valid_mask)[0]
    log(f"  Valid observations: {len(valid_idx)} / {T * N} ({len(valid_idx) / max(1, T*N):.1%})")

    if len(valid_idx) < min_train + min_test + purge_bars * N:
        return {"error": "insufficient data", "valid_n": len(valid_idx)}

    # Sort by timestamp for temporal ordering
    valid_ts = np.array([ts_flat[i].timestamp() for i in valid_idx])
    sort_order = np.argsort(valid_ts)
    valid_idx = valid_idx[sort_order]

    total = len(valid_idx)
    usable = total - purge_bars * N
    step = max(min_test, (usable - min_train) // max(1, n_folds))

    fold_args = []
    train_end = min_train
    while train_end + purge_bars * N + min_test <= total and len(fold_args) < n_folds:
        test_start = train_end + purge_bars * N
        test_end = min(total, test_start + step)

        train_i = valid_idx[:train_end]
        test_i = valid_idx[test_start:test_end]

        fold_args.append((
            len(fold_args) + 1,
            X_flat[train_i],
            y_flat[train_i],
            X_flat[test_i],
            y_flat[test_i],
            [ts_flat[i] for i in test_i],
            feature_names,
        ))
        train_end = test_start + (test_end - test_start)

    log(f"  Created {len(fold_args)} walk-forward folds")

    # Run folds in parallel
    if n_workers > 1 and len(fold_args) > 1:
        with Pool(min(n_workers, len(fold_args))) as pool:
            fold_results = pool.map(_evaluate_fold, fold_args)
    else:
        fold_results = [_evaluate_fold(a) for a in fold_args]

    # Aggregate results
    profitable_folds = sum(1 for f in fold_results if f.get("avg_daily_pf", 0) > 1.0)
    all_pnl = []
    for f in fold_results:
        all_pnl.extend(f.get("pnl_values", []))

    if all_pnl:
        pnl_arr = np.array(all_pnl)
        oos_sharpe = float(np.mean(pnl_arr) / max(1e-10, np.std(pnl_arr)))
    else:
        oos_sharpe = 0.0

    # DSR correction
    dsr = _compute_dsr(oos_sharpe, n_trials=max(1, len(fold_args)), n_obs=len(all_pnl))

    return {
        "folds": fold_results,
        "n_folds": len(fold_results),
        "profitable_folds": profitable_folds,
        "wf_pass_rate": profitable_folds / max(1, len(fold_results)),
        "avg_daily_pf": float(np.mean([f["avg_daily_pf"] for f in fold_results if "avg_daily_pf" in f])) if fold_results else 0.0,
        "aggregate_pf": float(np.mean([f["aggregate_pf"] for f in fold_results if "aggregate_pf" in f])) if fold_results else 0.0,
        "oos_sharpe": oos_sharpe,
        "oos_mean_pnl": float(np.mean(all_pnl)) if all_pnl else 0.0,
        "oos_total_trades": len(all_pnl),
        "dsr": dsr["dsr"],
        "dsr_e_max_sr": dsr["e_max_sr"],
        "dsr_p_value": dsr["p_value"],
    }


def _compute_dsr(observed_sr: float, n_trials: int, n_obs: int) -> dict:
    """Deflated Sharpe Ratio (Harvey et al., 2016)."""
    if n_trials <= 0 or n_obs <= 1:
        return {"dsr": 0.0, "e_max_sr": 0.0, "p_value": 1.0}

    gamma = 0.5772156649
    z1 = _ppf(1.0 - 1.0 / max(2, n_trials))
    z2 = _ppf(1.0 - 1.0 / max(2, n_trials * math.e))
    e_max_sr = (1.0 - gamma) * z1 + gamma * z2

    sr_var = 1.0 / max(1, n_obs - 1)
    test_stat = (observed_sr - e_max_sr) / max(1e-10, math.sqrt(sr_var))
    p_value = 1.0 - 0.5 * (1.0 + math.erf(test_stat / math.sqrt(2.0)))

    return {
        "dsr": float(observed_sr - e_max_sr),
        "e_max_sr": float(e_max_sr),
        "p_value": float(p_value),
    }


def _ppf(p: float) -> float:
    """Inverse standard normal CDF (Abramowitz & Stegun 26.2.23)."""
    if p <= 0:
        return -6.0
    if p >= 1:
        return 6.0
    if p > 0.5:
        return -_ppf(1.0 - p)
    t = math.sqrt(-2.0 * math.log(max(1e-300, p)))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return -(t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t))


# ======================================================================
# PHASE 4: Reporting
# ======================================================================

def format_report(results: dict, horizon: int, cost_mult: float) -> str:
    """Format results into human-readable report."""
    lines = []
    lines.append(f"\n{'='*70}")
    lines.append(f"HORIZON: {horizon}h | COST MULTIPLIER: {cost_mult}x | ROUND-TRIP: {ROUND_TRIP_BPS * cost_mult:.0f} bps")
    lines.append(f"{'='*70}")

    if "error" in results:
        lines.append(f"ERROR: {results['error']}")
        return "\n".join(lines)

    lines.append(f"Walk-forward folds: {results['n_folds']}")
    lines.append(f"Profitable folds:   {results['profitable_folds']}/{results['n_folds']} ({results['wf_pass_rate']:.0%})")
    lines.append(f"OOS total trades:   {results['oos_total_trades']}")
    lines.append("")
    lines.append(f"--- SUCCESS METRIC (CLAUDE.md Section 4.5) ---")
    lines.append(f"Average daily PF:   {results['avg_daily_pf']:.3f}  {'PASS' if results['avg_daily_pf'] > 1.0 else 'FAIL'}")
    lines.append(f"Aggregate PF:       {results['aggregate_pf']:.3f}")
    lines.append(f"OOS Sharpe:         {results['oos_sharpe']:.3f}")
    lines.append(f"OOS Mean PnL:       {results['oos_mean_pnl']*10000:.1f} bps")
    lines.append("")
    lines.append(f"--- DSR CORRECTION (CLAUDE.md Section 4.3) ---")
    lines.append(f"DSR:                {results['dsr']:.3f}  {'PASS' if results['dsr'] > 0 else 'FAIL'}")
    lines.append(f"E[max(SR)]:         {results['dsr_e_max_sr']:.3f}")
    lines.append(f"DSR p-value:        {results['dsr_p_value']:.4f}")
    lines.append("")

    # Per-fold details
    lines.append(f"--- PER-FOLD DETAILS ---")
    for f in results.get("folds", []):
        if "error" in f:
            lines.append(f"  Fold {f['fold']}: ERROR - {f['error']}")
            continue
        pf_status = "+" if f["avg_daily_pf"] > 1.0 else "-"
        lines.append(
            f"  Fold {f['fold']}: train={f['train_n']:>6d}  test={f['test_n']:>6d}  "
            f"acc={f['accuracy']:.3f}  daily_pf={f['avg_daily_pf']:.3f} [{pf_status}]  "
            f"sharpe={f['sharpe']:.3f}  days={f['trading_days']}"
        )

    # Top features across folds
    feat_scores: dict[str, float] = defaultdict(float)
    for f in results.get("folds", []):
        for feat, score in f.get("top_features", {}).items():
            feat_scores[feat] += score

    if feat_scores:
        lines.append("")
        lines.append(f"--- TOP FEATURES (by cumulative importance across folds) ---")
        for feat, score in sorted(feat_scores.items(), key=lambda x: -x[1])[:15]:
            lines.append(f"  {feat:40s}  {score:.6f}")

    return "\n".join(lines)


# ======================================================================
# MAIN
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="Opus 4.6 Forward Predictor Discovery")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--symbols", default=",".join(TOP_SYMBOLS))
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--output", default="/scripts/opus_discovery_results.json")
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    db_url = args.database_url or DB_URL_DEFAULT
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    log(f"Opus 4.6 Forward Predictor Discovery")
    log(f"Symbols: {len(symbols)}, Lookback: {args.lookback_days}d, Workers: {args.workers}")

    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=args.lookback_days)

    conn = psycopg.connect(db_url, row_factory=dict_row)
    try:
        # Phase 1: Load data
        ohlcv = load_ohlcv_matrix(conn, symbols, start_ts, end_ts)
        social_mat, social_cols = load_social_matrix(conn, symbols, ohlcv["timestamps"])

        # Phase 2: Engineer features
        feat_array, feature_names = engineer_features(ohlcv, social_mat, social_cols)

        # Phase 3: Walk-forward validation for each horizon × cost multiplier
        all_results = {}
        for horizon in HORIZONS:
            log(f"\n--- Evaluating horizon: {horizon}h ---")
            target = compute_forward_returns(ohlcv["close"], horizon)

            for cost_mult in COST_MULTIPLIERS:
                if cost_mult != 1.0:
                    target_adj = compute_forward_returns(ohlcv["close"], horizon, ROUND_TRIP_BPS * cost_mult)
                else:
                    target_adj = target

                key = f"h{horizon}_cost{cost_mult}x"
                log(f"  Running walk-forward: {key}")
                results = run_walk_forward(
                    feat_array, target_adj, ohlcv["timestamps"], feature_names,
                    n_workers=args.workers,
                )
                all_results[key] = results

                # Print report
                report = format_report(results, horizon, cost_mult)
                print(report)

        # Phase 4: Summary
        log("\n" + "=" * 70)
        log("FINAL SUMMARY")
        log("=" * 70)

        for key, results in all_results.items():
            if "error" in results:
                log(f"  {key}: ERROR - {results['error']}")
                continue
            pf = results['avg_daily_pf']
            dsr = results['dsr']
            wf_pass = results['wf_pass_rate']
            status = "TRADEABLE" if pf > 1.0 and dsr > 0 and wf_pass >= 0.8 else "NOT YET"
            log(f"  {key}: avg_daily_pf={pf:.3f}, dsr={dsr:.3f}, wf_pass={wf_pass:.0%} → {status}")

        # Save results to JSON
        output_path = args.output
        serialisable = {}
        for key, results in all_results.items():
            r = {k: v for k, v in results.items() if k != "folds"}
            r["fold_summaries"] = []
            for f in results.get("folds", []):
                fold_summary = {k: v for k, v in f.items() if k != "pnl_values"}
                r["fold_summaries"].append(fold_summary)
            serialisable[key] = r

        try:
            with open(output_path, "w") as fp:
                json.dump(serialisable, fp, indent=2, default=str)
            log(f"\nResults saved to: {output_path}")
        except Exception as e:
            log(f"Warning: Could not save to {output_path}: {e}")
            # Try alternative path
            alt_path = "/tmp/opus_discovery_results.json"
            try:
                with open(alt_path, "w") as fp:
                    json.dump(serialisable, fp, indent=2, default=str)
                log(f"Results saved to: {alt_path}")
            except Exception:
                pass

        log("\nDone.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
