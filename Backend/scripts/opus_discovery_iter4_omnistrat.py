#!/usr/bin/env python3
"""
Opus 4.6 Forward Predictor Discovery — Iteration 4: Omni-Strategy
===================================================================
8 independent strategy families + portfolio combination testing.
50 symbols, 4 horizons, 3 cost levels, 3-tier DSR reporting.

Key changes from iter3:
1. 50 symbols (not 20) for better L/S diversification
2. Weekly (168h) horizon added — where academic crypto momentum peaks
3. Aggregate social as market-level regime indicator (not per-symbol)
4. Portfolio combination testing — the key insight from peer review
5. 8 genuinely independent strategy families
6. 3-tier DSR reporting (STRICT / R5 / R10)

Strategies:
 1a. S9-Enhanced: social silence + market social regime filter
 1b. S9-Enhanced: social silence per-symbol gate
 2.  Cross-sectional momentum LONG (complement to S9 SHORT)
 3.  Time-series momentum (TSMOM) — vol-scaled
 4.  BTC-neutral residual reversion (pairs trading)
 5.  Volume-price divergence (OBV)
 6.  Volatility regime router (meta-strategy: momentum in low vol, reversion in high)
 7.  Social catalyst event strategy (market-level aggregate)
 8.  Silence-before-pump detector (SHORT the reversal)
 9.  Portfolio combination of 5 most independent strategies

Compute profile:
 Phase 1: ~20s, 1 core (DB fetch 50 symbols)
 Phase 2: ~10s, 8-12 cores (feature engineering)
 Phase 3: ~540 jobs in Pool(12), ~6-10 min
 Phase 4: portfolio combination + reporting, ~30s
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
# Configuration
# ======================================================================

TOP_50_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "BNBUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
    "PEPEUSDT", "SUIUSDT", "HBARUSDT", "XLMUSDT", "SHIBUSDT",
    "TRXUSDT", "LTCUSDT", "NEARUSDT", "MATICUSDT", "UNIUSDT",
    "AAVEUSDT", "APTUSDT", "ARBUSDT", "ATOMUSDT", "FILUSDT",
    "FTMUSDT", "GALAUSDT", "GRTUSDT", "ICPUSDT", "INJUSDT",
    "LDOUSDT", "MANAUSDT", "MKRUSDT", "ONDOUSDT", "OPUSDT",
    "RENDERUSDT", "RUNEUSDT", "SANDUSDT", "SEIUSDT", "STXUSDT",
    "THETAUSDT", "TIAUSDT", "TONUSDT", "VETUSDT", "WIFUSDT",
    "WLDUSDT", "XAIUSDT", "JASMYUSDT", "ENAUSDT", "JUPUSDT",
]

HORIZONS = [6, 24, 72, 168]
PURGE_BARS = 24
MIN_TRAIN_BARS = 100  # Per bar-row: 100 bars × 50 symbols = 5000 obs
MIN_TEST_BARS = 25
N_FOLDS = 5
TIMEFRAME = "1h"

# Transaction costs (CLAUDE.md Section 4.4)
SLIPPAGE_BPS = 5.0
FEE_BPS = 4.0
COST_PER_SIDE_BPS = SLIPPAGE_BPS + FEE_BPS  # 9 bps per side
XSM_PENALTY_PER_SIDE_BPS = 2.5  # 5 bps total split across 2 sides
COST_PER_LEG_BPS = 2.0 * (COST_PER_SIDE_BPS + XSM_PENALTY_PER_SIDE_BPS)  # 23 bps

# Strategy-specific costs (bps round-trip)
COST_TSMOM_RT_BPS = 18.0    # Long-only: 9 bps entry + 9 bps exit
COST_SOCIAL_CAT_RT_BPS = 18.0  # Long-only
COST_BTC_NEUTRAL_RT_BPS = 92.0  # 4 legs × 23 bps
COST_DEFAULT_RT_BPS = 46.0  # 2 legs × 23 bps

QUINTILE_SIZE = 0.2
DECILE_SIZE = 0.1
COST_MULTIPLIERS = [1.0, 1.5, 2.0]

# DSR configuration
CUMULATIVE_TRIALS = 901  # 865 from iter1-3 + 36 new (9 families × 4 horizons)
NEW_TRIALS_ONLY = 36


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ======================================================================
# Vectorized Utilities (from iter3)
# ======================================================================

def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    cs = np.nancumsum(x, axis=0)
    cnt = np.nancumsum(np.isfinite(x).astype(float), axis=0)
    out = np.full_like(x, np.nan)
    prev_cs = np.concatenate([np.zeros_like(cs[:1]), cs[:-1]], axis=0)
    prev_cnt = np.concatenate([np.zeros_like(cnt[:1]), cnt[:-1]], axis=0)
    out[w - 1:] = cs[w - 1:] - prev_cs[w - 1:]
    cnt_w = cnt[w - 1:] - prev_cnt[w - 1:]
    cnt_w[cnt_w == 0] = np.nan
    out[w - 1:] /= cnt_w
    return out


def _rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    mean = _rolling_mean(x, w)
    sq_mean = _rolling_mean(x ** 2, w)
    var = np.clip(sq_mean - mean ** 2, 0, None)
    return np.sqrt(var)


def _lag(x: np.ndarray, n: int) -> np.ndarray:
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
    mx, my = _rolling_mean(x, w), _rolling_mean(y, w)
    mxy = _rolling_mean(x * y, w)
    sx, sy = _rolling_std(x, w), _rolling_std(y, w)
    denom = sx * sy
    denom[denom == 0] = np.nan
    return (mxy - mx * my) / denom


def _cross_sectional_rank_vectorized(x: np.ndarray) -> np.ndarray:
    T, N = x.shape
    x_fill = np.where(np.isfinite(x), x, np.inf)
    order = np.argsort(x_fill, axis=1)
    ranks = np.argsort(order, axis=1).astype(float)
    valid_mask = np.isfinite(x)
    valid_count = valid_mask.sum(axis=1, keepdims=True).astype(float)
    valid_count[valid_count <= 1] = np.nan
    ranks = ranks / (valid_count - 1)
    ranks[~valid_mask] = np.nan
    return ranks


def _cross_sectional_zscore_vectorized(x: np.ndarray) -> np.ndarray:
    valid_mask = np.isfinite(x)
    x_masked = np.where(valid_mask, x, 0.0)
    count = valid_mask.sum(axis=1, keepdims=True).astype(float)
    count[count < 3] = np.nan
    mu = x_masked.sum(axis=1, keepdims=True) / count
    diff_sq = np.where(valid_mask, (x - mu) ** 2, 0.0)
    variance = diff_sq.sum(axis=1, keepdims=True) / count
    sigma = np.sqrt(np.clip(variance, 1e-20, None))
    result = (x - mu) / sigma
    result[~valid_mask] = np.nan
    return result


def _rolling_ols_beta(y: np.ndarray, x: np.ndarray, w: int) -> np.ndarray:
    """Vectorized rolling OLS beta: y = alpha + beta*x. Both (T, N) or (T,1)."""
    xy = _rolling_mean(x * y, w)
    mx = _rolling_mean(x, w)
    my = _rolling_mean(y, w)
    x2 = _rolling_mean(x ** 2, w)
    var_x = x2 - mx ** 2
    var_x[var_x < 1e-20] = np.nan
    return (xy - mx * my) / var_x


def _rolling_linear_slope(x: np.ndarray, w: int) -> np.ndarray:
    """Rolling linear regression slope over window w. x is (T, N)."""
    T, N = x.shape
    t_vec = np.arange(w, dtype=float)
    t_mean = t_vec.mean()
    t_var = np.sum((t_vec - t_mean) ** 2)
    if t_var == 0:
        return np.full_like(x, np.nan)
    out = np.full_like(x, np.nan)
    for i in range(w - 1, T):
        window = x[i - w + 1:i + 1]  # (w, N)
        valid = np.isfinite(window).all(axis=0)
        if valid.any():
            y_mean = np.nanmean(window, axis=0)
            cov = np.sum((t_vec[:, None] - t_mean) * (window - y_mean[None, :]), axis=0)
            out[i, valid] = cov[valid] / t_var
    return out


# ======================================================================
# Data Loading
# ======================================================================

def load_ohlcv_matrix(conn, symbols: list[str], start_ts: datetime, end_ts: datetime):
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

    sym_idx = {s: i for i, s in enumerate(symbols)}
    all_ts = sorted(set(r["bar_start_ts"] for r in rows))
    ts_idx = {t: i for i, t in enumerate(all_ts)}
    T, N = len(all_ts), len(symbols)
    mats = {k: np.full((T, N), np.nan) for k in ("open", "high", "low", "close", "volume")}
    for r in rows:
        t, n = ts_idx[r["bar_start_ts"]], sym_idx.get(r["symbol"])
        if n is None:
            continue
        for k in ("open", "high", "low", "close", "volume"):
            mats[k][t, n] = float(r[k])

    # Report symbols with data
    symbols_with_data = []
    for i, s in enumerate(symbols):
        fill = np.isfinite(mats["close"][:, i]).mean()
        if fill > 0.5:
            symbols_with_data.append(s)
    log(f"  OHLCV matrix: ({T}, {N}), fill rate: {np.isfinite(mats['close']).mean():.2%}")
    log(f"  Symbols with >50% fill: {len(symbols_with_data)}/{N}")
    return {"timestamps": all_ts, "symbols": symbols, **mats}


def load_social_matrix(conn, symbols: list[str], timestamps: list[datetime]):
    from psycopg.rows import dict_row
    log(f"Loading social features for {len(symbols)} symbols...")
    social_cols = [
        "mention_count", "unique_authors", "platform_count",
        "engagement_sum", "mention_velocity", "mention_acceleration",
        "author_novelty_ratio", "mention_volume_ratio",
    ]
    T, N, F = len(timestamps), len(symbols), len(social_cols)
    social_mat = np.full((T, N, F), 0.0)
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
        t, n = ts_idx.get(r["bar_start_ts"]), sym_idx.get(r["symbol"])
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
# Feature Engineering (41 existing + 10 new = 51)
# ======================================================================

def engineer_features(ohlcv: dict, social_mat: np.ndarray) -> tuple[dict, list[str]]:
    log("Engineering features...")
    t0 = time.time()
    O, H, L, C, V = ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"]
    T, N = C.shape

    features = {}

    # Returns
    ret_1 = C / _lag(C, 1) - 1.0
    ret_6 = C / _lag(C, 6) - 1.0
    ret_24 = C / _lag(C, 24) - 1.0
    ret_72 = C / _lag(C, 72) - 1.0
    features["ret_1h"] = ret_1
    features["ret_6h"] = ret_6
    features["ret_24h"] = ret_24
    features["ret_72h"] = ret_72

    # NEW: 7d return for momentum/TSMOM
    ret_168 = C / _lag(C, 168) - 1.0
    features["ret_168h"] = ret_168

    # Intrabar microstructure
    bar_range = np.where(H - L > 0, H - L, np.nan)
    features["body_position"] = (C - L) / bar_range
    features["upper_wick_ratio"] = (H - np.maximum(O, C)) / bar_range
    features["lower_wick_ratio"] = (np.minimum(O, C) - L) / bar_range
    features["body_size_ratio"] = np.abs(C - O) / bar_range

    # Volatility
    realized_vol_24 = _rolling_std(ret_1, 24)
    realized_vol_72 = _rolling_std(ret_1, 72)
    vol_regime = realized_vol_24 / np.where(realized_vol_72 > 0, realized_vol_72, np.nan)
    vol_change_6h = realized_vol_24 - _lag(realized_vol_24, 6)
    features["realized_vol_24"] = realized_vol_24
    features["vol_regime_ratio"] = vol_regime
    features["vol_acceleration"] = vol_change_6h - _lag(vol_change_6h, 6)

    # Volume
    vol_ma_24 = _rolling_mean(V, 24)
    vol_ma_72 = _rolling_mean(V, 72)
    relative_volume = V / np.where(vol_ma_24 > 0, vol_ma_24, np.nan)
    features["relative_volume_24"] = relative_volume
    features["volume_regime"] = vol_ma_24 / np.where(vol_ma_72 > 0, vol_ma_72, np.nan)

    # Cross-symbol (BTC is index 0)
    btc_ret_1 = ret_1[:, 0:1]
    btc_ret_6 = ret_6[:, 0:1]
    btc_ret_24 = ret_24[:, 0:1]
    features["idiosyncratic_ret_1h"] = ret_1 - btc_ret_1
    features["idiosyncratic_ret_6h"] = ret_6 - btc_ret_6
    features["idiosyncratic_ret_24h"] = ret_24 - btc_ret_24

    # NEW: BTC beta and residuals for Strategy 4
    rolling_btc_beta = _rolling_ols_beta(ret_1, btc_ret_1 * np.ones((1, N)), 72)
    btc_residual = ret_1 - rolling_btc_beta * btc_ret_1
    btc_res_mean = _rolling_mean(btc_residual, 72)
    btc_res_std = _rolling_std(btc_residual, 72)
    btc_residual_zscore = (btc_residual - btc_res_mean) / np.where(btc_res_std > 0, btc_res_std, np.nan)
    features["rolling_btc_beta_72"] = rolling_btc_beta
    features["btc_residual"] = btc_residual
    features["btc_residual_zscore"] = btc_residual_zscore

    # Social features
    mentions = social_mat[:, :, 0]
    authors = social_mat[:, :, 1]
    engagement = social_mat[:, :, 3]
    mention_vel = social_mat[:, :, 4]
    mention_accel = social_mat[:, :, 5]
    author_novelty = social_mat[:, :, 6]

    mentions_6h = _rolling_mean(mentions, 6)
    mentions_24h = _rolling_mean(mentions, 24)
    authors_24h = _rolling_mean(authors, 24)
    engagement_24h = _rolling_mean(engagement, 24)

    features["mentions_6h_avg"] = mentions_6h
    features["mentions_24h_avg"] = mentions_24h
    features["engagement_24h_avg"] = engagement_24h
    features["author_novelty_24h_avg"] = _rolling_mean(author_novelty, 24)

    author_concentration = np.where(authors_24h > 0, mentions_24h / authors_24h, np.nan)
    features["author_concentration_24h"] = author_concentration

    # Social-price coupling
    m_std = _rolling_std(mentions_6h, 72)
    mentions_z = (mentions_6h - _rolling_mean(mentions_6h, 72)) / np.where(m_std > 0, m_std, np.nan)
    r_std = _rolling_std(ret_6, 72)
    ret_z = (ret_6 - _rolling_mean(ret_6, 72)) / np.where(r_std > 0, r_std, np.nan)
    features["social_price_divergence"] = mentions_z - np.abs(ret_z)
    features["social_price_coupling_72w"] = _rolling_corr(mentions, np.abs(ret_1), 72)
    features["mention_acceleration_raw"] = mention_accel
    features["vol_attention_coupling"] = realized_vol_24 * mentions_z

    mv_std = _rolling_std(mention_vel, 72)
    mention_vel_z = (mention_vel - _rolling_mean(mention_vel, 72)) / np.where(
        mv_std > 0, mv_std, np.nan
    )
    features["mention_vel_z_72w"] = mention_vel_z

    # Cross-sectional ranks
    features["cs_rank_mentions_6h"] = _cross_sectional_rank_vectorized(mentions_6h)
    features["cs_rank_mentions_24h"] = _cross_sectional_rank_vectorized(mentions_24h)
    features["cs_rank_engagement_24h"] = _cross_sectional_rank_vectorized(engagement_24h)
    features["cs_rank_ret_24h"] = _cross_sectional_rank_vectorized(ret_24)
    features["cs_rank_ret_72h"] = _cross_sectional_rank_vectorized(ret_72)
    features["cs_rank_ret_168h"] = _cross_sectional_rank_vectorized(ret_168)
    features["cs_rank_volume"] = _cross_sectional_rank_vectorized(relative_volume)
    features["cs_rank_vol"] = _cross_sectional_rank_vectorized(realized_vol_24)
    features["cs_rank_author_concentration"] = _cross_sectional_rank_vectorized(author_concentration)
    features["cs_rank_social_price_div"] = _cross_sectional_rank_vectorized(mentions_z - np.abs(ret_z))

    # Cross-sectional z-scores
    features["cs_z_mentions_6h"] = _cross_sectional_zscore_vectorized(mentions_6h)
    features["cs_z_ret_24h"] = _cross_sectional_zscore_vectorized(ret_24)
    features["cs_z_vol"] = _cross_sectional_zscore_vectorized(realized_vol_24)

    # Composites
    features["contrarian_social_composite"] = (
        _cross_sectional_rank_vectorized(mentions_24h) * 0.4
        + _cross_sectional_rank_vectorized(author_concentration) * 0.3
        + (1.0 - _cross_sectional_rank_vectorized(ret_24)) * 0.3
    )
    features["momentum_confirmation_composite"] = (
        _cross_sectional_rank_vectorized(ret_24) * 0.5
        + _cross_sectional_rank_vectorized(mentions_24h) * 0.3
        + _cross_sectional_rank_vectorized(engagement_24h) * 0.2
    )
    mention_change = mentions_6h - _lag(mentions_6h, 24)
    ret_change = np.abs(ret_24) - np.abs(_lag(ret_24, 24))
    features["attention_price_divergence"] = (
        _cross_sectional_rank_vectorized(mention_change)
        - _cross_sectional_rank_vectorized(ret_change)
    )

    # NEW: OBV and OBV slope for Strategy 5
    sign_ret = np.sign(ret_1)
    signed_vol = sign_ret * V
    obv = np.nancumsum(np.nan_to_num(signed_vol, nan=0.0), axis=0)
    features["obv"] = obv
    obv_slope = _rolling_linear_slope(obv, 24)
    features["obv_slope_24"] = obv_slope

    # NEW: Aggregate social velocity (market-level) for Strategies 1a, 7
    # Mean mention_velocity across all symbols at each timestamp
    agg_social_vel = np.nanmean(mention_vel, axis=1, keepdims=True)  # (T, 1)
    agg_social_vel_broadcast = np.broadcast_to(agg_social_vel, (T, N)).copy()
    agg_sv_mean = _rolling_mean(agg_social_vel_broadcast[:, 0:1], 72)
    agg_sv_std = _rolling_std(agg_social_vel_broadcast[:, 0:1], 72)
    agg_social_vel_z = (agg_social_vel - agg_sv_mean) / np.where(agg_sv_std > 0, agg_sv_std, np.nan)
    agg_social_vel_z_broadcast = np.broadcast_to(agg_social_vel_z, (T, N)).copy()
    features["aggregate_social_velocity"] = agg_social_vel_broadcast
    features["aggregate_social_velocity_zscore"] = agg_social_vel_z_broadcast

    # NEW: Silence days + pump trigger for Strategy 8
    # Silence: social_vel_z < -1 AND relative_volume_24 < 0.5
    silence_mask = (mention_vel_z < -1.0) & (relative_volume < 0.5)
    silence_mask = np.nan_to_num(silence_mask, nan=0).astype(float)
    # Count consecutive silence bars (rolling sum approximation)
    silence_cumsum = np.zeros_like(silence_mask)
    for t in range(1, T):
        silence_cumsum[t] = np.where(silence_mask[t] > 0, silence_cumsum[t - 1] + 1, 0)
    silence_days = silence_cumsum / 24.0  # Convert bars to days
    features["silence_days"] = silence_days

    # Pump trigger: silence_days >= 1 AND ret_6h > 3% AND relative_volume > 1.5
    # (Relaxed from 3d/5%/2.0 — too restrictive with 14% social coverage)
    pump_trigger = (
        (silence_days >= 1.0) | (_lag(silence_days, 1) >= 1.0)
    ) & (ret_6 > 0.03) & (relative_volume > 1.5)
    pump_trigger = np.where(np.isfinite(ret_6) & np.isfinite(relative_volume),
                            pump_trigger.astype(float), np.nan)
    features["pump_trigger"] = pump_trigger

    for k in features:
        features[k] = np.clip(features[k], -100, 100)

    elapsed = time.time() - t0
    log(f"  Engineered {len(features)} features in {elapsed:.1f}s")
    return features, sorted(features.keys())


# ======================================================================
# Walk-Forward Infrastructure
# ======================================================================

def build_temporal_folds(T: int, purge_bars: int, min_train: int, min_test: int,
                         n_folds: int) -> list[tuple[int, int, int, int]]:
    usable = T - purge_bars
    step = max(min_test, (usable - min_train) // max(1, n_folds))
    folds = []
    train_end = min_train
    while train_end + purge_bars + min_test <= T and len(folds) < n_folds:
        test_start = train_end + purge_bars
        test_end = min(T, test_start + step)
        folds.append((0, train_end, test_start, test_end))
        train_end = test_end
    return folds


def compute_gross_forward_returns(close_mat: np.ndarray, horizon: int) -> np.ndarray:
    future_close = _lead(close_mat, horizon)
    return future_close / close_mat - 1.0


# ======================================================================
# Daily PF (vectorized)
# ======================================================================

def _compute_daily_pf_vectorized(ts_days: np.ndarray, pnl: np.ndarray) -> dict:
    unique_days = np.unique(ts_days)
    daily_pfs = []
    for d in unique_days:
        mask = ts_days == d
        day_pnl = pnl[mask]
        profit = float(np.sum(day_pnl[day_pnl > 0]))
        loss = float(np.abs(np.sum(day_pnl[day_pnl < 0])))
        if loss > 0:
            daily_pfs.append(profit / loss)
        elif profit > 0:
            daily_pfs.append(5.0)
    return {
        "avg_daily_pf": float(np.mean(daily_pfs)) if daily_pfs else 0.0,
        "trading_days": len(daily_pfs),
        "pf_gt1_pct": float(np.mean([1 if pf > 1 else 0 for pf in daily_pfs])) if daily_pfs else 0.0,
    }


# ======================================================================
# DSR (3-tier)
# ======================================================================

def _ppf(p: float) -> float:
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


def _compute_e_max_sr(n_trials: int) -> float:
    if n_trials <= 1:
        return 0.0
    gamma = 0.5772156649
    z1 = _ppf(1.0 - 1.0 / max(2, n_trials))
    z2 = _ppf(1.0 - 1.0 / max(2, n_trials * math.e))
    return (1.0 - gamma) * z1 + gamma * z2


def _compute_dsr_3tier(observed_sr: float, n_obs: int) -> dict:
    """Compute DSR at 3 tiers: strict (cumulative), Bonferroni (new only), R5, R10."""
    e_max_cum = _compute_e_max_sr(CUMULATIVE_TRIALS)  # ~3.69
    e_max_new = _compute_e_max_sr(NEW_TRIALS_ONLY)    # ~2.98

    sr_se = 1.0 / math.sqrt(max(1, n_obs - 1))

    # Cumulative DSR tiers
    dsr_strict = observed_sr - e_max_cum
    dsr_r5 = observed_sr - (e_max_cum - 0.50)
    dsr_r10 = observed_sr - (e_max_cum - 1.00)

    # Bonferroni (new trials only)
    dsr_bonf_strict = observed_sr - e_max_new
    dsr_bonf_r5 = observed_sr - (e_max_new - 0.50)
    dsr_bonf_r10 = observed_sr - (e_max_new - 1.00)

    # Tier labels
    if dsr_strict > 0:
        tier_cum = "STRICT"
    elif dsr_r5 > 0:
        tier_cum = "R5"
    elif dsr_r10 > 0:
        tier_cum = "R10"
    else:
        tier_cum = "FAIL"

    if dsr_bonf_strict > 0:
        tier_bonf = "STRICT"
    elif dsr_bonf_r5 > 0:
        tier_bonf = "R5"
    elif dsr_bonf_r10 > 0:
        tier_bonf = "R10"
    else:
        tier_bonf = "FAIL"

    return {
        "oos_sharpe": observed_sr,
        "e_max_sr_cumulative": e_max_cum,
        "e_max_sr_bonferroni": e_max_new,
        "dsr_strict": dsr_strict,
        "dsr_r5": dsr_r5,
        "dsr_r10": dsr_r10,
        "tier_cumulative": tier_cum,
        "dsr_bonf_strict": dsr_bonf_strict,
        "dsr_bonf_r5": dsr_bonf_r5,
        "dsr_bonf_r10": dsr_bonf_r10,
        "tier_bonferroni": tier_bonf,
    }


# ======================================================================
# GBStumps (from iter3)
# ======================================================================

class GBStumpsVectorized:
    def __init__(self, n_stumps: int = 200, learning_rate: float = 0.05,
                 subsample: float = 0.8, n_thresholds: int = 20):
        self.n_stumps = n_stumps
        self.lr = learning_rate
        self.subsample = subsample
        self.n_thresholds = n_thresholds
        self.stumps: list[dict] = []
        self.init_pred: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "GBStumpsVectorized":
        n, p = X.shape
        self.init_pred = float(np.mean(y))
        residual = y.copy() - self.init_pred
        self.stumps = []
        thresholds = np.zeros((p, self.n_thresholds))
        for j in range(p):
            col = X[:, j]
            valid = np.isfinite(col) & (col != 0.0)
            if valid.sum() < 10:
                thresholds[j] = np.linspace(-1, 1, self.n_thresholds)
            else:
                qs = np.linspace(5, 95, self.n_thresholds)
                thresholds[j] = np.percentile(col[valid], qs)
        rng = np.random.default_rng(42)
        for s_i in range(self.n_stumps):
            if self.subsample < 1.0:
                mask = rng.random(n) < self.subsample
                Xi, ri = X[mask], residual[mask]
            else:
                Xi, ri = X, residual
            ni = Xi.shape[0]
            if ni < 10:
                continue
            Xi_exp = Xi[:, :, np.newaxis]
            thr_exp = thresholds[np.newaxis, :, :]
            left_masks = Xi_exp <= thr_exp
            n_left = left_masks.sum(axis=0)
            n_right = ni - n_left
            ri_exp = ri[:, np.newaxis, np.newaxis]
            left_sum = (left_masks * ri_exp).sum(axis=0)
            right_sum = ri.sum() - left_sum
            n_left_safe = np.where(n_left > 0, n_left, 1).astype(float)
            n_right_safe = np.where(n_right > 0, n_right, 1).astype(float)
            left_mean = left_sum / n_left_safe
            right_mean = right_sum / n_right_safe
            gain = n_left * left_mean ** 2 + n_right * right_mean ** 2
            valid_split = (n_left >= 5) & (n_right >= 5)
            gain[~valid_split] = -1.0
            best_flat = np.argmax(gain)
            best_j, best_t = np.unravel_index(best_flat, gain.shape)
            if gain[best_j, best_t] <= 0:
                continue
            stump = {
                "feat": int(best_j),
                "thresh": float(thresholds[best_j, best_t]),
                "left": float(left_mean[best_j, best_t] * self.lr),
                "right": float(right_mean[best_j, best_t] * self.lr),
            }
            self.stumps.append(stump)
            pred_update = np.where(
                X[:, best_j] <= stump["thresh"], stump["left"], stump["right"]
            )
            residual -= pred_update
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        pred = np.full(X.shape[0], self.init_pred)
        for stump in self.stumps:
            pred += np.where(
                X[:, stump["feat"]] <= stump["thresh"],
                stump["left"],
                stump["right"],
            )
        return pred

    def feature_importance(self, feature_names: list[str]) -> dict[str, float]:
        imp = defaultdict(float)
        for stump in self.stumps:
            fi = stump["feat"]
            gain = abs(stump["left"]) + abs(stump["right"])
            if fi < len(feature_names):
                imp[feature_names[fi]] += gain
        total = sum(imp.values()) or 1.0
        return {k: v / total for k, v in imp.items()}


# ======================================================================
# Strategy Evaluators
# ======================================================================

def _eval_s9_enhanced_fold(args: tuple) -> dict:
    """Strategy 1a/1b: S9-like reversion + social filter.
    1a: suppress entries when aggregate social velocity is top quartile
    1b: suppress entries when per-symbol mentions > median
    """
    (fold_i, variant, cs_rank_ret_test, gross_ret_test, day_indices_test,
     breadth_test, agg_social_vel_z_test, mentions_24h_test,
     cost_rt_bps, cost_mult) = args

    T_test, N = cs_rank_ret_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    # S9 base: short top quintile losers that will revert
    # Reversion signal: SHORT symbols with highest recent loss (bottom rank = most negative ret)
    # Actually S9 shorts oversold bounces — let's use contrarian: short top decile by 24h ret
    # and long bottom decile (mean reversion)
    low_thresh = QUINTILE_SIZE
    high_thresh = 1.0 - QUINTILE_SIZE

    valid = np.isfinite(cs_rank_ret_test) & np.isfinite(gross_ret_test)

    # Breadth filter: need >30% valid symbols
    valid_count = valid.sum(axis=1)
    min_valid = max(5, int(N * 0.3))
    tradeable = valid_count >= min_valid

    # Social filter
    if variant == "1a":
        # Suppress when aggregate social velocity z-score > 75th percentile
        agg_z_col = agg_social_vel_z_test[:, 0] if agg_social_vel_z_test.ndim > 1 else agg_social_vel_z_test
        agg_valid = np.isfinite(agg_z_col)
        if agg_valid.sum() > 10:
            q75 = np.nanpercentile(agg_z_col[agg_valid], 75)
            social_pass = agg_z_col <= q75
        else:
            social_pass = np.ones(T_test, dtype=bool)
        tradeable = tradeable & social_pass
    elif variant == "1b":
        # Per-symbol: suppress if symbol mentions > cross-sectional median
        # This is applied per position, not per bar — handled below
        pass

    if tradeable.sum() == 0:
        return {"fold": fold_i, "strategy": f"s9_enhanced_{variant}", "error": "no tradeable bars"}

    # L/S: long bottom quintile (oversold), short top quintile (overbought)
    long_mask = tradeable[:, np.newaxis] & valid & (cs_rank_ret_test <= low_thresh)
    short_mask = tradeable[:, np.newaxis] & valid & (cs_rank_ret_test >= high_thresh)

    if variant == "1b":
        # Per-symbol social gate: suppress positions where mentions > median
        mentions_median = np.nanmedian(mentions_24h_test, axis=1, keepdims=True)
        below_median = mentions_24h_test <= mentions_median
        long_mask = long_mask & below_median
        short_mask = short_mask & below_median

    n_long = long_mask.sum(axis=1).astype(float)
    n_short = short_mask.sum(axis=1).astype(float)

    long_ret = np.where(n_long > 0,
                        np.where(long_mask, gross_ret_test, 0.0).sum(axis=1) / np.maximum(n_long, 1), 0.0)
    short_ret = np.where(n_short > 0,
                         np.where(short_mask, gross_ret_test, 0.0).sum(axis=1) / np.maximum(n_short, 1), 0.0)

    portfolio_pnl = (long_ret - short_ret) / 2.0 - cost_frac
    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = day_indices_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": f"s9_enhanced_{variant}", "error": "no PnL"}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": f"s9_enhanced_{variant}",
        "test_n": int(len(pnl_arr)),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_xs_momentum_long_fold(args: tuple) -> dict:
    """Strategy 2: Cross-sectional momentum LONG — top decile by 7d return.
    Complement to S9 SHORT. Breadth filter: >30% valid."""
    (fold_i, cs_rank_ret168_test, gross_ret_test, day_indices_test,
     cost_rt_bps, cost_mult) = args

    T_test, N = cs_rank_ret168_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = np.isfinite(cs_rank_ret168_test) & np.isfinite(gross_ret_test)
    valid_count = valid.sum(axis=1)
    min_valid = max(5, int(N * 0.3))
    tradeable = valid_count >= min_valid

    if tradeable.sum() == 0:
        return {"fold": fold_i, "strategy": "xs_momentum_long", "error": "no tradeable bars"}

    # Long top decile by 7d return (momentum winners)
    high_thresh = 1.0 - DECILE_SIZE  # Top 10%
    long_mask = tradeable[:, np.newaxis] & valid & (cs_rank_ret168_test >= high_thresh)
    n_long = long_mask.sum(axis=1).astype(float)

    long_ret = np.where(n_long > 0,
                        np.where(long_mask, gross_ret_test, 0.0).sum(axis=1) / np.maximum(n_long, 1), 0.0)

    # Long-only: PnL = long_ret - cost
    portfolio_pnl = long_ret - cost_frac
    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = day_indices_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "xs_momentum_long", "error": "no PnL"}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "xs_momentum_long",
        "test_n": int(len(pnl_arr)),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_tsmom_fold(args: tuple) -> dict:
    """Strategy 3: Time-series momentum. Per-symbol: long if 7d ret > 0, flat otherwise.
    Vol-scaled position sizing."""
    (fold_i, ret_168_test, realized_vol_test, gross_ret_test, day_indices_test,
     cost_rt_bps, cost_mult) = args

    T_test, N = ret_168_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = np.isfinite(ret_168_test) & np.isfinite(gross_ret_test) & np.isfinite(realized_vol_test)

    # Signal: long when 7d return > 0
    signal = np.where(valid & (ret_168_test > 0), 1.0, 0.0)

    # Vol-scaling: inverse vol weighting (lower vol = larger position)
    target_vol = 0.02  # Target 2% vol per position
    vol_safe = np.where(realized_vol_test > 0.001, realized_vol_test, 0.001)
    position_size = np.minimum(target_vol / vol_safe, 3.0)  # Cap at 3x

    # Weighted return per bar
    weighted_ret = signal * position_size * gross_ret_test
    n_positions = (signal > 0).sum(axis=1).astype(float)
    n_positions[n_positions == 0] = np.nan

    # Average across symbols
    portfolio_ret = np.nansum(weighted_ret, axis=1) / np.where(n_positions > 0, n_positions, 1.0)

    # Cost: only when we have positions
    has_positions = n_positions > 0
    portfolio_pnl = np.where(has_positions, portfolio_ret - cost_frac, 0.0)

    tradeable = has_positions & np.isfinite(portfolio_pnl)
    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = day_indices_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "tsmom", "error": "no PnL"}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "tsmom",
        "test_n": int(len(pnl_arr)),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_btc_neutral_fold(args: tuple) -> dict:
    """Strategy 4: BTC-neutral residual reversion. Trade when btc_residual_zscore > 1.5 sigma."""
    (fold_i, btc_res_z_test, gross_ret_test, btc_gross_ret_test, btc_beta_test,
     day_indices_test, cost_rt_bps, cost_mult) = args

    T_test, N = btc_res_z_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = (np.isfinite(btc_res_z_test) & np.isfinite(gross_ret_test)
             & np.isfinite(btc_beta_test))

    # Signal: short alt when residual z > 1.5 (overextended relative to BTC)
    #         long alt when residual z < -1.5 (underextended relative to BTC)
    # (Relaxed from 2.0 — was generating zero signals)
    signal = np.zeros((T_test, N))
    signal[valid & (btc_res_z_test > 1.5)] = -1.0  # Short the alt
    signal[valid & (btc_res_z_test < -1.5)] = 1.0   # Long the alt

    # PnL: signal * alt_return - signal * beta * btc_return (hedge)
    # Simplified: signal * (alt_return - beta * btc_return) = signal * residual_return
    btc_ret_col = btc_gross_ret_test[:, 0:1] if btc_gross_ret_test.ndim > 1 else btc_gross_ret_test[:, np.newaxis]
    hedge_ret = btc_beta_test * btc_ret_col
    residual_pnl = signal * (gross_ret_test - hedge_ret)

    n_positions = (signal != 0).sum(axis=1).astype(float)
    has_positions = n_positions > 0

    portfolio_pnl = np.where(
        has_positions,
        np.nansum(residual_pnl, axis=1) / np.maximum(n_positions, 1) - cost_frac,
        0.0
    )

    tradeable = has_positions & np.isfinite(portfolio_pnl)
    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = day_indices_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "btc_neutral", "error": "no PnL"}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    n_short = int((signal[tradeable.nonzero()[0] if tradeable.ndim == 1 else tradeable] == -1).sum()) if tradeable.any() else 0
    n_long = int((signal[tradeable.nonzero()[0] if tradeable.ndim == 1 else tradeable] == 1).sum()) if tradeable.any() else 0

    return {
        "fold": fold_i,
        "strategy": "btc_neutral",
        "test_n": int(len(pnl_arr)),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_vol_price_div_fold(args: tuple) -> dict:
    """Strategy 5: Volume-price divergence (OBV).
    Short: price at 24h high but OBV slope negative.
    Long: price at 24h low but OBV slope positive."""
    (fold_i, close_test, high_24_test, low_24_test, obv_slope_test,
     gross_ret_test, day_indices_test, cost_rt_bps, cost_mult) = args

    T_test, N = close_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = (np.isfinite(close_test) & np.isfinite(obv_slope_test)
             & np.isfinite(gross_ret_test) & np.isfinite(high_24_test)
             & np.isfinite(low_24_test))

    # Price near 24h high: close within 1% of 24h high
    near_high = valid & (close_test >= high_24_test * 0.99)
    # Price near 24h low: close within 1% of 24h low
    near_low = valid & (close_test <= low_24_test * 1.01)

    signal = np.zeros((T_test, N))
    # Bearish divergence: price at high, OBV slope negative
    signal[near_high & (obv_slope_test < 0)] = -1.0
    # Bullish divergence: price at low, OBV slope positive
    signal[near_low & (obv_slope_test > 0)] = 1.0

    weighted_pnl = signal * gross_ret_test
    n_positions = (signal != 0).sum(axis=1).astype(float)
    has_positions = n_positions > 0

    portfolio_pnl = np.where(
        has_positions,
        np.nansum(weighted_pnl, axis=1) / np.maximum(n_positions, 1) - cost_frac,
        0.0
    )

    tradeable = has_positions & np.isfinite(portfolio_pnl)
    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = day_indices_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "vol_price_divergence", "error": "no PnL"}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "vol_price_divergence",
        "test_n": int(len(pnl_arr)),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_vol_regime_router_fold(args: tuple) -> dict:
    """Strategy 6: Vol regime router. LOW vol: momentum (Strat 2). HIGH vol: reversion (S9).
    Uses BTC realized vol to classify regime."""
    (fold_i, cs_rank_ret168_test, cs_rank_ret24_test, btc_vol_test,
     gross_ret_test, day_indices_test, cost_rt_bps, cost_mult) = args

    T_test, N = cs_rank_ret168_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = (np.isfinite(cs_rank_ret168_test) & np.isfinite(cs_rank_ret24_test)
             & np.isfinite(gross_ret_test))

    # BTC vol regime: compare current 24h vol to 72h vol
    btc_vol_col = btc_vol_test[:, 0] if btc_vol_test.ndim > 1 else btc_vol_test
    btc_vol_valid = np.isfinite(btc_vol_col)

    if btc_vol_valid.sum() < 10:
        return {"fold": fold_i, "strategy": "vol_regime_router", "error": "insufficient BTC vol data"}

    # Use tertiles instead of 0.8x/1.2x median (was leaving ~60% in "normal" = flat)
    q33 = np.nanpercentile(btc_vol_col[btc_vol_valid], 33)
    q67 = np.nanpercentile(btc_vol_col[btc_vol_valid], 67)
    high_vol = btc_vol_col > q67  # Top third
    low_vol = btc_vol_col < q33   # Bottom third
    # Middle third: flat

    valid_count = valid.sum(axis=1)
    min_valid = max(5, int(N * 0.3))
    tradeable = valid_count >= min_valid

    portfolio_pnl = np.zeros(T_test)

    for t in range(T_test):
        if not tradeable[t]:
            continue
        if not np.isfinite(btc_vol_col[t]):
            continue

        if low_vol[t]:
            # Momentum: long top decile by 7d return
            long_mask = valid[t] & (cs_rank_ret168_test[t] >= 1.0 - DECILE_SIZE)
            n = long_mask.sum()
            if n > 0:
                portfolio_pnl[t] = gross_ret_test[t, long_mask].mean() - cost_frac
            else:
                tradeable[t] = False
        elif high_vol[t]:
            # Reversion: L/S on 24h returns (long oversold, short overbought)
            v = valid[t]
            l_mask = v & (cs_rank_ret24_test[t] <= QUINTILE_SIZE)
            s_mask = v & (cs_rank_ret24_test[t] >= 1.0 - QUINTILE_SIZE)
            nl, ns = l_mask.sum(), s_mask.sum()
            if nl > 0 and ns > 0:
                lr = gross_ret_test[t, l_mask].mean()
                sr = gross_ret_test[t, s_mask].mean()
                portfolio_pnl[t] = (lr - sr) / 2.0 - cost_frac
            else:
                tradeable[t] = False
        else:
            # Normal vol: flat
            tradeable[t] = False

    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = day_indices_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "vol_regime_router", "error": "no PnL"}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    n_high = int(high_vol[tradeable].sum()) if tradeable.any() else 0
    n_low = int(low_vol[tradeable].sum()) if tradeable.any() else 0

    return {
        "fold": fold_i,
        "strategy": "vol_regime_router",
        "test_n": int(len(pnl_arr)),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "n_high_vol_bars": n_high,
        "n_low_vol_bars": n_low,
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_social_catalyst_fold(args: tuple) -> dict:
    """Strategy 7: Social catalyst event. Long when aggregate mention_vel_z > 3
    AND author_novelty > 0.5 (genuine organic discovery). Uses MARKET-LEVEL aggregate."""
    (fold_i, agg_social_vel_z_test, author_novelty_test, gross_ret_test,
     day_indices_test, cost_rt_bps, cost_mult) = args

    T_test, N = gross_ret_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    # Market-level signal (same for all symbols)
    agg_z_col = agg_social_vel_z_test[:, 0] if agg_social_vel_z_test.ndim > 1 else agg_social_vel_z_test

    valid = np.isfinite(gross_ret_test)

    # Signal: long ALL valid symbols when aggregate social z > 2
    # AND average author novelty across universe > 0.3
    # (Relaxed from z>3/novelty>0.5 — too restrictive with 14% social coverage)
    avg_novelty = np.nanmean(author_novelty_test, axis=1)
    catalyst_signal = (
        np.isfinite(agg_z_col) & (agg_z_col > 2.0)
        & np.isfinite(avg_novelty) & (avg_novelty > 0.3)
    )

    # Equal-weight long all valid symbols during catalyst events
    portfolio_pnl = np.zeros(T_test)
    tradeable = np.zeros(T_test, dtype=bool)
    n_events = 0

    for t in range(T_test):
        if catalyst_signal[t]:
            valid_t = valid[t]
            n_valid = valid_t.sum()
            if n_valid >= 5:
                portfolio_pnl[t] = gross_ret_test[t, valid_t].mean() - cost_frac
                tradeable[t] = True
                n_events += 1

    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = day_indices_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "social_catalyst", "error": "no catalyst events",
                "n_events": n_events}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "social_catalyst",
        "test_n": int(len(pnl_arr)),
        "n_events": n_events,
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_silence_pump_fold(args: tuple) -> dict:
    """Strategy 8: Silence-before-pump detector. SHORT when pump_trigger fires.
    Rare-event strategy."""
    (fold_i, pump_trigger_test, gross_ret_test, day_indices_test,
     cost_rt_bps, cost_mult) = args

    T_test, N = pump_trigger_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = np.isfinite(pump_trigger_test) & np.isfinite(gross_ret_test)

    # Signal: SHORT when pump trigger fires (expecting dump reversal)
    signal = np.where(valid & (pump_trigger_test > 0.5), -1.0, 0.0)

    weighted_pnl = signal * gross_ret_test
    n_positions = (signal != 0).sum(axis=1).astype(float)
    has_positions = n_positions > 0

    portfolio_pnl = np.where(
        has_positions,
        np.nansum(weighted_pnl, axis=1) / np.maximum(n_positions, 1) - cost_frac,
        0.0
    )

    tradeable = has_positions & np.isfinite(portfolio_pnl)
    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = day_indices_test[tradeable]

    total_events = int((signal != 0).sum())
    n_pump_bars = int(tradeable.sum())

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "silence_pump", "error": "no pump events",
                "total_events": total_events}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    # False positive analysis: how many pump signals led to FURTHER gains (bad for short)?
    positive_after = int(np.sum(pnl_arr < 0))  # Negative PnL on short = price went up more
    fp_rate = positive_after / max(1, len(pnl_arr))

    return {
        "fold": fold_i,
        "strategy": "silence_pump",
        "test_n": int(len(pnl_arr)),
        "total_events": total_events,
        "n_pump_bars": n_pump_bars,
        "false_positive_rate": fp_rate,
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


# ======================================================================
# Universal Job Dispatcher
# ======================================================================

def _dispatch_job(args: tuple) -> dict:
    job_type = args[0]
    try:
        if job_type == "s9_enhanced":
            return _eval_s9_enhanced_fold(args[1])
        elif job_type == "xs_momentum_long":
            return _eval_xs_momentum_long_fold(args[1])
        elif job_type == "tsmom":
            return _eval_tsmom_fold(args[1])
        elif job_type == "btc_neutral":
            return _eval_btc_neutral_fold(args[1])
        elif job_type == "vol_price_div":
            return _eval_vol_price_div_fold(args[1])
        elif job_type == "vol_regime_router":
            return _eval_vol_regime_router_fold(args[1])
        elif job_type == "social_catalyst":
            return _eval_social_catalyst_fold(args[1])
        elif job_type == "silence_pump":
            return _eval_silence_pump_fold(args[1])
        return {"error": f"unknown job type: {job_type}"}
    except Exception as e:
        return {"error": f"{job_type} exception: {str(e)}", "fold": args[1][0] if len(args) > 1 else -1}


# ======================================================================
# Job Builder
# ======================================================================

def build_all_jobs(
    features: dict[str, np.ndarray],
    ohlcv: dict,
    horizons: list[int],
    cost_multipliers: list[float],
) -> tuple[list[tuple], list[tuple]]:
    T, N = ohlcv["close"].shape
    timestamps = ohlcv["timestamps"]
    day_indices = np.array([(ts - timestamps[0]).days for ts in timestamps], dtype=np.int32)

    folds = build_temporal_folds(T, PURGE_BARS, MIN_TRAIN_BARS, MIN_TEST_BARS, N_FOLDS)
    log(f"  Built {len(folds)} temporal folds for {T} bars x {N} symbols")

    if not folds:
        return [], []

    # Pre-compute rolling 24h high/low for Strategy 5
    high_24 = np.full_like(ohlcv["high"], np.nan)
    low_24 = np.full_like(ohlcv["low"], np.nan)
    for t in range(23, T):
        high_24[t] = np.nanmax(ohlcv["high"][t - 23:t + 1], axis=0)
        low_24[t] = np.nanmin(ohlcv["low"][t - 23:t + 1], axis=0)

    all_jobs = []
    job_keys = []

    for horizon in horizons:
        gross_ret = compute_gross_forward_returns(ohlcv["close"], horizon)

        # Determine strategy-specific costs
        for cost_mult in cost_multipliers:
            config_key = f"h{horizon}_cost{cost_mult}x"

            for fi, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
                day_idx_test = day_indices[te_s:te_e]
                gross_ret_test = gross_ret[te_s:te_e]

                # Strategy 1a: S9 enhanced + aggregate social regime
                all_jobs.append(("s9_enhanced", (
                    fi + 1, "1a",
                    features["cs_rank_ret_24h"][te_s:te_e],
                    gross_ret_test,
                    day_idx_test,
                    features.get("cs_rank_ret_24h", np.full((T, N), np.nan))[te_s:te_e],  # breadth proxy
                    features["aggregate_social_velocity_zscore"][te_s:te_e],
                    features["mentions_24h_avg"][te_s:te_e],
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "s9_enhanced_1a", fi + 1))

                # Strategy 1b: S9 enhanced + per-symbol social gate
                all_jobs.append(("s9_enhanced", (
                    fi + 1, "1b",
                    features["cs_rank_ret_24h"][te_s:te_e],
                    gross_ret_test,
                    day_idx_test,
                    features.get("cs_rank_ret_24h", np.full((T, N), np.nan))[te_s:te_e],
                    features["aggregate_social_velocity_zscore"][te_s:te_e],
                    features["mentions_24h_avg"][te_s:te_e],
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "s9_enhanced_1b", fi + 1))

                # Strategy 2: XS Momentum Long (top decile 7d ret)
                all_jobs.append(("xs_momentum_long", (
                    fi + 1,
                    features["cs_rank_ret_168h"][te_s:te_e],
                    gross_ret_test,
                    day_idx_test,
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "xs_momentum_long", fi + 1))

                # Strategy 3: TSMOM
                all_jobs.append(("tsmom", (
                    fi + 1,
                    features["ret_168h"][te_s:te_e],
                    features["realized_vol_24"][te_s:te_e],
                    gross_ret_test,
                    day_idx_test,
                    COST_TSMOM_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "tsmom", fi + 1))

                # Strategy 4: BTC-neutral
                btc_gross_ret = compute_gross_forward_returns(ohlcv["close"][:, 0:1], horizon)
                all_jobs.append(("btc_neutral", (
                    fi + 1,
                    features["btc_residual_zscore"][te_s:te_e],
                    gross_ret_test,
                    btc_gross_ret[te_s:te_e],
                    features["rolling_btc_beta_72"][te_s:te_e],
                    day_idx_test,
                    COST_BTC_NEUTRAL_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "btc_neutral", fi + 1))

                # Strategy 5: Volume-price divergence
                all_jobs.append(("vol_price_div", (
                    fi + 1,
                    ohlcv["close"][te_s:te_e],
                    high_24[te_s:te_e],
                    low_24[te_s:te_e],
                    features["obv_slope_24"][te_s:te_e],
                    gross_ret_test,
                    day_idx_test,
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "vol_price_divergence", fi + 1))

                # Strategy 6: Vol regime router
                all_jobs.append(("vol_regime_router", (
                    fi + 1,
                    features["cs_rank_ret_168h"][te_s:te_e],
                    features["cs_rank_ret_24h"][te_s:te_e],
                    features["vol_regime_ratio"][te_s:te_e],
                    gross_ret_test,
                    day_idx_test,
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "vol_regime_router", fi + 1))

                # Strategy 7: Social catalyst
                all_jobs.append(("social_catalyst", (
                    fi + 1,
                    features["aggregate_social_velocity_zscore"][te_s:te_e],
                    features["author_novelty_24h_avg"][te_s:te_e],
                    gross_ret_test,
                    day_idx_test,
                    COST_SOCIAL_CAT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "social_catalyst", fi + 1))

                # Strategy 8: Silence-before-pump
                all_jobs.append(("silence_pump", (
                    fi + 1,
                    features["pump_trigger"][te_s:te_e],
                    gross_ret_test,
                    day_idx_test,
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "silence_pump", fi + 1))

    log(f"  Total jobs: {len(all_jobs)} ({len(horizons)} horizons x {len(cost_multipliers)} costs x 9 strategies x {len(folds)} folds)")
    return all_jobs, job_keys


# ======================================================================
# Aggregation
# ======================================================================

def _aggregate_strategy_results(fold_results: list[dict], strategy_name: str) -> dict:
    valid_folds = [f for f in fold_results if "error" not in f]
    if not valid_folds:
        return {"strategy": strategy_name, "error": "all folds failed", "folds": fold_results}

    all_pnl = []
    for f in valid_folds:
        all_pnl.extend(f.get("pnl_values", []))

    profitable_folds = sum(1 for f in valid_folds if f.get("avg_daily_pf", 0) > 1.0)
    pnl_arr = np.array(all_pnl) if all_pnl else np.array([0.0])
    oos_sharpe = float(pnl_arr.mean() / max(1e-10, pnl_arr.std()))
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    dsr = _compute_dsr_3tier(oos_sharpe, len(all_pnl))

    # Collect strategy-specific metadata
    extra = {}
    total_events = sum(f.get("total_events", 0) for f in valid_folds)
    if total_events > 0:
        extra["total_events"] = total_events
    fp_rates = [f.get("false_positive_rate", 0) for f in valid_folds if "false_positive_rate" in f]
    if fp_rates:
        extra["avg_false_positive_rate"] = float(np.mean(fp_rates))
    n_events_list = [f.get("n_events", 0) for f in valid_folds if "n_events" in f]
    if n_events_list:
        extra["total_catalyst_events"] = sum(n_events_list)

    return {
        "strategy": strategy_name,
        "n_folds": len(valid_folds),
        "profitable_folds": profitable_folds,
        "wf_pass_rate": profitable_folds / max(1, len(valid_folds)),
        "avg_daily_pf": float(np.mean([f["avg_daily_pf"] for f in valid_folds])),
        "avg_pf_gt1_pct": float(np.mean([f.get("pf_gt1_pct", 0) for f in valid_folds])),
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "oos_sharpe": oos_sharpe,
        "oos_mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "oos_total_trades": len(all_pnl),
        **dsr,
        **extra,
        "_pnl_all": all_pnl,  # Preserved for portfolio combination (stripped at serialization)
        "folds": [{k: v for k, v in f.items() if k != "pnl_values"} for f in fold_results],
    }


# ======================================================================
# Portfolio Combination (Strategy 9)
# ======================================================================

def compute_portfolio_combination(results_by_config: dict) -> dict:
    """Strategy 9: Combine the 5 most independent strategies at equal weight.
    Uses stored PnL values from the individual strategy folds."""
    log("Computing portfolio combinations...")

    # The 5 target strategies for portfolio combination
    target_strategies = [
        "s9_enhanced_1a",   # Reversion + social filter
        "xs_momentum_long", # Momentum long
        "tsmom",            # Time-series momentum
        "btc_neutral",      # Market-neutral pairs
        "silence_pump",     # Rare event short
    ]

    portfolio_results = {}

    for config_key, config_data in results_by_config.items():
        if config_key.startswith("_"):
            continue

        # Collect PnL arrays for each strategy in this config
        strat_pnls = {}
        for strat_name in target_strategies:
            strat_data = config_data.get(strat_name, {})
            if "error" in strat_data or not isinstance(strat_data, dict):
                continue
            # Read from _pnl_all (preserved during aggregation)
            all_pnl = strat_data.get("_pnl_all", [])
            if all_pnl:
                strat_pnls[strat_name] = np.array(all_pnl)

        if len(strat_pnls) < 2:
            portfolio_results[config_key] = {
                "strategy": "portfolio_combination",
                "error": f"only {len(strat_pnls)} strategies available, need >=2",
                "available_strategies": list(strat_pnls.keys()),
            }
            continue

        # Equal-weight combination: average PnL across strategies
        # Pad shorter arrays with 0 (conservative — missing signal = flat)
        max_len = max(len(v) for v in strat_pnls.values())
        combined_pnl = np.zeros(max_len)
        n_contributing = np.zeros(max_len)

        for strat_name, pnl in strat_pnls.items():
            combined_pnl[:len(pnl)] += pnl
            n_contributing[:len(pnl)] += 1.0

        n_contributing[n_contributing == 0] = 1.0
        combined_pnl /= n_contributing

        # Compute metrics
        total_profit = float(np.sum(combined_pnl[combined_pnl > 0]))
        total_loss = float(np.abs(np.sum(combined_pnl[combined_pnl < 0])))
        oos_sharpe = float(combined_pnl.mean() / max(1e-10, combined_pnl.std()))

        dsr = _compute_dsr_3tier(oos_sharpe, len(combined_pnl))

        # Correlation matrix between strategies
        corr_matrix = {}
        strat_names = list(strat_pnls.keys())
        for i, s1 in enumerate(strat_names):
            for j, s2 in enumerate(strat_names):
                if j > i:
                    min_len = min(len(strat_pnls[s1]), len(strat_pnls[s2]))
                    if min_len > 10:
                        corr = float(np.corrcoef(strat_pnls[s1][:min_len], strat_pnls[s2][:min_len])[0, 1])
                        corr_matrix[f"{s1}_vs_{s2}"] = round(corr, 3)

        # Daily PF (approximate: use sequential day index)
        day_idx = np.arange(len(combined_pnl)) // 24  # Approximate 24 bars per day
        daily = _compute_daily_pf_vectorized(day_idx, combined_pnl)

        portfolio_results[config_key] = {
            "strategy": "portfolio_combination",
            "n_strategies": len(strat_pnls),
            "strategies_included": list(strat_pnls.keys()),
            "n_obs": len(combined_pnl),
            "avg_daily_pf": daily["avg_daily_pf"],
            "pf_gt1_pct": daily["pf_gt1_pct"],
            "trading_days": daily["trading_days"],
            "aggregate_pf": total_profit / max(1e-10, total_loss),
            "oos_mean_pnl_bps": float(combined_pnl.mean() * 10000),
            **dsr,
            "correlation_matrix": corr_matrix,
        }

    return portfolio_results


# ======================================================================
# Reporting
# ======================================================================

def format_all_results(results_by_config: dict, portfolio_results: dict) -> str:
    lines = []

    lines.append("\n" + "=" * 110)
    lines.append("ITERATION 4: OMNI-STRATEGY DISCOVERY — INDIVIDUAL STRATEGY RESULTS")
    lines.append("=" * 110)

    for config_key in sorted(k for k in results_by_config if not k.startswith("_")):
        config = results_by_config[config_key]
        parts = config_key.split("_")
        horizon = parts[0].replace("h", "") + "h"
        cost = parts[1].replace("cost", "").replace("x", "") + "x"

        lines.append(f"\n{'='*110}")
        lines.append(f"HORIZON: {horizon} | COST: {cost}")
        lines.append(f"{'='*110}")

        strats = [(k, v) for k, v in config.items() if isinstance(v, dict) and "strategy" in v]
        strats.sort(key=lambda x: x[1].get("aggregate_pf", 0), reverse=True)

        lines.append(f"\n{'Strategy':<30s} {'AvgDPF':>7s} {'AggPF':>7s} {'Sharpe':>7s} {'MeanBps':>8s} {'WF%':>5s} {'Tier(C)':>8s} {'Tier(B)':>8s} {'Status':>8s}")
        lines.append("-" * 110)

        for key, val in strats:
            if "error" in val:
                lines.append(f"{key:<30s} {'ERROR':>7s} - {val['error']}")
                continue
            dpf = val.get("avg_daily_pf", 0)
            apf = val.get("aggregate_pf", 0)
            sharpe = val.get("oos_sharpe", 0)
            mean_bps = val.get("oos_mean_pnl_bps", 0)
            wf_rate = val.get("wf_pass_rate", 0)
            tier_c = val.get("tier_cumulative", "FAIL")
            tier_b = val.get("tier_bonferroni", "FAIL")

            if dpf > 1.0 and apf > 1.0 and tier_c in ("STRICT", "R5") and wf_rate >= 0.8:
                status = "PASS"
            elif dpf > 1.0 and apf > 1.0:
                status = "EDGE?"
            elif apf > 1.0:
                status = "WEAK"
            else:
                status = "FAIL"

            lines.append(
                f"{key:<30s} {dpf:>7.3f} {apf:>7.3f} {sharpe:>7.3f} {mean_bps:>8.1f} {wf_rate:>4.0%} {tier_c:>8s} {tier_b:>8s} {status:>8s}"
            )

        # Detail for top 3
        for key, val in strats[:3]:
            if "error" in val:
                continue
            lines.append(f"\n  [{key}]")
            lines.append(f"  Folds: {val.get('n_folds', 0)}, Profitable: {val.get('profitable_folds', 0)}, Trades: {val.get('oos_total_trades', 0)}")
            lines.append(f"  OOS Sharpe: {val.get('oos_sharpe', 0):.3f}")
            lines.append(f"  DSR strict: {val.get('dsr_strict', 0):.3f}, R5: {val.get('dsr_r5', 0):.3f}, R10: {val.get('dsr_r10', 0):.3f}")
            lines.append(f"  E[max(SR)] cumulative: {val.get('e_max_sr_cumulative', 0):.3f}, Bonferroni: {val.get('e_max_sr_bonferroni', 0):.3f}")
            if val.get("total_events"):
                lines.append(f"  Total events: {val['total_events']}")
            if val.get("avg_false_positive_rate") is not None:
                lines.append(f"  Avg false positive rate: {val['avg_false_positive_rate']:.2%}")
            for f in val.get("folds", []):
                if "error" in f:
                    lines.append(f"    Fold {f.get('fold', '?')}: ERROR - {f['error']}")
                else:
                    dpf_f = f.get("avg_daily_pf", 0)
                    pf_mark = "+" if dpf_f > 1.0 else "-"
                    lines.append(
                        f"    Fold {f.get('fold', '?')}: n={f.get('test_n', 0):>5d}  "
                        f"dpf={dpf_f:.3f}[{pf_mark}]  agg_pf={f.get('aggregate_pf', 0):.3f}  "
                        f"sharpe={f.get('sharpe', 0):.3f}  days={f.get('trading_days', 0)}"
                    )

    # Portfolio combination results
    lines.append("\n\n" + "=" * 110)
    lines.append("PORTFOLIO COMBINATION RESULTS (Strategy 9)")
    lines.append("=" * 110)

    for config_key in sorted(portfolio_results.keys()):
        pf = portfolio_results[config_key]
        lines.append(f"\n  [{config_key}]")
        if "error" in pf:
            lines.append(f"    ERROR: {pf['error']}")
            continue
        lines.append(f"    Strategies: {pf.get('strategies_included', [])}")
        lines.append(f"    N strategies: {pf.get('n_strategies', 0)}, Observations: {pf.get('n_obs', 0)}")
        lines.append(f"    AvgDailyPF: {pf.get('avg_daily_pf', 0):.3f}, AggPF: {pf.get('aggregate_pf', 0):.3f}")
        lines.append(f"    Mean PnL: {pf.get('oos_mean_pnl_bps', 0):.1f} bps")
        lines.append(f"    OOS Sharpe: {pf.get('oos_sharpe', 0):.3f}")
        lines.append(f"    Tier (cumulative): {pf.get('tier_cumulative', 'FAIL')}, Tier (Bonferroni): {pf.get('tier_bonferroni', 'FAIL')}")
        lines.append(f"    DSR strict: {pf.get('dsr_strict', 0):.3f}, R5: {pf.get('dsr_r5', 0):.3f}, R10: {pf.get('dsr_r10', 0):.3f}")
        if pf.get("correlation_matrix"):
            lines.append(f"    Correlation matrix:")
            for pair, corr in pf["correlation_matrix"].items():
                lines.append(f"      {pair}: {corr:.3f}")

    return "\n".join(lines)


def write_markdown_report(report_text: str, results_by_config: dict,
                          portfolio_results: dict, meta: dict, output_path: str) -> None:
    lines = []
    lines.append("# Opus 4.6 Forward Predictor Discovery — Iteration 4: Omni-Strategy")
    lines.append(f"\n**Date:** {meta.get('date', 'unknown')}")
    lines.append(f"**Symbols:** {meta.get('n_symbols', 0)}")
    lines.append(f"**Horizons:** {meta.get('horizons', [])}")
    lines.append(f"**Features:** {meta.get('n_features', 0)}")
    lines.append(f"**DSR trials (cumulative):** {CUMULATIVE_TRIALS}")
    lines.append(f"**DSR trials (Bonferroni, new only):** {NEW_TRIALS_ONLY}")
    lines.append(f"**E[max(SR)] cumulative:** {_compute_e_max_sr(CUMULATIVE_TRIALS):.3f}")
    lines.append(f"**E[max(SR)] Bonferroni:** {_compute_e_max_sr(NEW_TRIALS_ONLY):.3f}")
    lines.append(f"**Wall clock:** {meta.get('wall_clock_seconds', 0):.0f}s")

    lines.append("\n## Strategy Families")
    lines.append("| # | Strategy | Type | Cost (RT bps) |")
    lines.append("|---|----------|------|---------------|")
    lines.append("| 1a | S9 Enhanced (agg social filter) | L/S reversion | 46 |")
    lines.append("| 1b | S9 Enhanced (per-symbol social gate) | L/S reversion | 46 |")
    lines.append("| 2 | XS Momentum Long | Long-only momentum | 46 |")
    lines.append("| 3 | TSMOM (vol-scaled) | Long-only trend | 18 |")
    lines.append("| 4 | BTC-Neutral Residual Reversion | Market-neutral pairs | 92 |")
    lines.append("| 5 | Volume-Price Divergence (OBV) | L/S divergence | 46 |")
    lines.append("| 6 | Vol Regime Router | Meta-strategy | varies |")
    lines.append("| 7 | Social Catalyst Event | Long-only event | 18 |")
    lines.append("| 8 | Silence-Before-Pump | Short rare event | 46 |")
    lines.append("| 9 | Portfolio Combination | Combined | ~42 |")

    lines.append("\n## Results")
    lines.append("```")
    lines.append(report_text)
    lines.append("```")

    # DSR sensitivity table
    lines.append("\n## DSR Sensitivity Matrix")
    lines.append("| Config | Strategy | AggPF | Sharpe | STRICT | R5 | R10 | Bonf |")
    lines.append("|--------|----------|-------|--------|--------|----|----|------|")
    for config_key in sorted(k for k in results_by_config if not k.startswith("_")):
        config = results_by_config[config_key]
        strats = [(k, v) for k, v in config.items() if isinstance(v, dict) and "strategy" in v and "error" not in v]
        strats.sort(key=lambda x: x[1].get("aggregate_pf", 0), reverse=True)
        for key, val in strats[:3]:  # Top 3 per config
            lines.append(
                f"| {config_key} | {key} | {val.get('aggregate_pf', 0):.3f} | "
                f"{val.get('oos_sharpe', 0):.3f} | {val.get('tier_cumulative', 'FAIL')} | "
                f"{val.get('dsr_r5', 0):.3f} | {val.get('dsr_r10', 0):.3f} | "
                f"{val.get('tier_bonferroni', 'FAIL')} |"
            )

    # Portfolio results table
    lines.append("\n## Portfolio Combination Results")
    lines.append("| Config | Strategies | AggPF | Sharpe | Tier(C) | Tier(B) |")
    lines.append("|--------|-----------|-------|--------|---------|---------|")
    for config_key in sorted(portfolio_results.keys()):
        pf = portfolio_results[config_key]
        if "error" in pf:
            lines.append(f"| {config_key} | ERROR | - | - | - | - |")
        else:
            lines.append(
                f"| {config_key} | {pf.get('n_strategies', 0)} | "
                f"{pf.get('aggregate_pf', 0):.3f} | {pf.get('oos_sharpe', 0):.3f} | "
                f"{pf.get('tier_cumulative', 'FAIL')} | {pf.get('tier_bonferroni', 'FAIL')} |"
            )

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as fp:
            fp.write("\n".join(lines))
        log(f"Markdown report saved to: {output_path}")
    except Exception as e:
        log(f"Warning: could not write markdown report: {e}")


# ======================================================================
# MAIN
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="Opus 4.6 Forward Predictor Discovery — Iteration 4")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--symbols", default=",".join(TOP_50_SYMBOLS))
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", default="/scripts/opus_discovery_iter4_results.json")
    parser.add_argument("--report", default=None)
    parser.add_argument("--horizons", default=",".join(str(h) for h in HORIZONS))
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    db_url = args.database_url or DB_URL_DEFAULT
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(h.strip()) for h in args.horizons.split(",")]

    log("=" * 110)
    log("Opus 4.6 Forward Predictor Discovery — ITERATION 4: OMNI-STRATEGY")
    log("=" * 110)
    log(f"Symbols: {len(symbols)}, Lookback: {args.lookback_days}d, Workers: {args.workers}")
    log(f"Horizons: {horizons}")
    log(f"DSR trials: {CUMULATIVE_TRIALS} cumulative, {NEW_TRIALS_ONLY} new (Bonferroni)")
    log(f"E[max(SR)]: {_compute_e_max_sr(CUMULATIVE_TRIALS):.3f} cumulative, {_compute_e_max_sr(NEW_TRIALS_ONLY):.3f} Bonferroni")
    log(f"Cost model: default {COST_DEFAULT_RT_BPS} bps RT, TSMOM {COST_TSMOM_RT_BPS}, BTC-neutral {COST_BTC_NEUTRAL_RT_BPS}")
    log(f"Cost multipliers: {COST_MULTIPLIERS}")

    t_total_start = time.time()
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=args.lookback_days)

    conn = psycopg.connect(db_url, row_factory=dict_row)
    try:
        # Phase 1: Load data
        log("\n--- PHASE 1: DATA LOADING ---")
        t1 = time.time()
        ohlcv = load_ohlcv_matrix(conn, symbols, start_ts, end_ts)
        social_mat, social_cols = load_social_matrix(conn, symbols, ohlcv["timestamps"])
        log(f"Phase 1 complete in {time.time() - t1:.1f}s")

        # Phase 2: Feature engineering
        log("\n--- PHASE 2: FEATURE ENGINEERING ---")
        t2 = time.time()
        features, feature_names = engineer_features(ohlcv, social_mat)
        log(f"Total features: {len(feature_names)}")
        log(f"Phase 2 complete in {time.time() - t2:.1f}s")

        # Phase 3: Build and dispatch all jobs
        log("\n--- PHASE 3: STRATEGY EVALUATION ---")
        t3 = time.time()
        all_jobs, job_keys = build_all_jobs(features, ohlcv, horizons, COST_MULTIPLIERS)

        if not all_jobs:
            log("ERROR: No jobs to run.")
            return

        log(f"Dispatching {len(all_jobs)} jobs to Pool({args.workers})...")
        with Pool(args.workers) as pool:
            results_flat = pool.map(_dispatch_job, all_jobs)

        log(f"Phase 3 complete in {time.time() - t3:.1f}s ({len(results_flat)} jobs)")

        # Reconstruct results
        grouped: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for i, result in enumerate(results_flat):
            config_key, strat_name, fold_i = job_keys[i]
            grouped[config_key][strat_name].append(result)

        results_by_config = {}
        for config_key in sorted(grouped.keys()):
            config_results = {}
            for strat_name, fold_results in grouped[config_key].items():
                config_results[strat_name] = _aggregate_strategy_results(fold_results, strat_name)
            results_by_config[config_key] = config_results

        # Phase 4: Portfolio combination + reporting
        log("\n--- PHASE 4: PORTFOLIO COMBINATION & REPORTING ---")
        t4 = time.time()
        portfolio_results = compute_portfolio_combination(results_by_config)

        report = format_all_results(results_by_config, portfolio_results)
        print(report)

        # Grand summary
        log("\n" + "=" * 110)
        log("GRAND SUMMARY — ITERATION 4: OMNI-STRATEGY")
        log("=" * 110)
        log(f"\n{'Config':<22s} {'Best Strategy':<30s} {'AvgDPF':>7s} {'AggPF':>7s} {'Sharpe':>7s} {'BPS':>6s} {'Tier(C)':>8s} {'Tier(B)':>8s}")
        log("-" * 110)

        grand_summary = []
        for config_key in sorted(k for k in results_by_config if not k.startswith("_")):
            config = results_by_config[config_key]
            best_strat, best_apf = None, 0.0
            for k, v in config.items():
                if isinstance(v, dict) and v.get("aggregate_pf", 0) > best_apf:
                    best_apf = v["aggregate_pf"]
                    best_strat = k
            if best_strat:
                v = config[best_strat]
                dpf = v.get("avg_daily_pf", 0)
                apf = v.get("aggregate_pf", 0)
                sharpe = v.get("oos_sharpe", 0)
                bps = v.get("oos_mean_pnl_bps", 0)
                tier_c = v.get("tier_cumulative", "FAIL")
                tier_b = v.get("tier_bonferroni", "FAIL")

                if dpf > 1.0 and apf > 1.0 and tier_c in ("STRICT",) and v.get("wf_pass_rate", 0) >= 0.8:
                    status = " *** TRADEABLE ***"
                elif dpf > 1.0 and apf > 1.0 and tier_c in ("R5", "R10"):
                    status = " << EDGE >>"
                elif apf > 1.0:
                    status = " (weak)"
                else:
                    status = ""

                log(f"  {config_key:<20s} {best_strat:<30s} {dpf:>7.3f} {apf:>7.3f} {sharpe:>7.3f} {bps:>6.1f} {tier_c:>8s} {tier_b:>8s}{status}")
                grand_summary.append({
                    "config": config_key,
                    "best_strategy": best_strat,
                    "avg_daily_pf": dpf,
                    "aggregate_pf": apf,
                    "oos_sharpe": sharpe,
                    "mean_pnl_bps": bps,
                    "tier_cumulative": tier_c,
                    "tier_bonferroni": tier_b,
                    "wf_pass_rate": v.get("wf_pass_rate", 0),
                })

        # Portfolio summary
        log("\n  --- PORTFOLIO COMBINATION ---")
        for config_key in sorted(portfolio_results.keys()):
            pf = portfolio_results[config_key]
            if "error" in pf:
                log(f"  {config_key:<20s} PORTFOLIO ERROR: {pf['error']}")
            else:
                apf = pf.get("aggregate_pf", 0)
                sharpe = pf.get("oos_sharpe", 0)
                bps = pf.get("oos_mean_pnl_bps", 0)
                tier_c = pf.get("tier_cumulative", "FAIL")
                tier_b = pf.get("tier_bonferroni", "FAIL")
                log(f"  {config_key:<20s} {'PORTFOLIO':>30s} {'':>7s} {apf:>7.3f} {sharpe:>7.3f} {bps:>6.1f} {tier_c:>8s} {tier_b:>8s}")

        log(f"\nPhase 4 complete in {time.time() - t4:.1f}s")

        wall_clock = time.time() - t_total_start

        # Save JSON
        meta = {
            "script": "opus_discovery_iter4_omnistrat.py",
            "date": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "n_symbols": len(symbols),
            "horizons": horizons,
            "n_features": len(feature_names),
            "feature_names": feature_names,
            "cost_model": {
                "default_rt_bps": COST_DEFAULT_RT_BPS,
                "tsmom_rt_bps": COST_TSMOM_RT_BPS,
                "btc_neutral_rt_bps": COST_BTC_NEUTRAL_RT_BPS,
                "social_catalyst_rt_bps": COST_SOCIAL_CAT_RT_BPS,
                "multipliers": COST_MULTIPLIERS,
            },
            "dsr": {
                "cumulative_trials": CUMULATIVE_TRIALS,
                "new_trials": NEW_TRIALS_ONLY,
                "e_max_sr_cumulative": _compute_e_max_sr(CUMULATIVE_TRIALS),
                "e_max_sr_bonferroni": _compute_e_max_sr(NEW_TRIALS_ONLY),
            },
            "n_folds": N_FOLDS,
            "purge_bars": PURGE_BARS,
            "total_jobs": len(all_jobs),
            "wall_clock_seconds": wall_clock,
        }

        serialisable = {}
        for ck, cr in results_by_config.items():
            cs = {}
            for sk, sv in cr.items():
                if not isinstance(sv, dict):
                    cs[sk] = sv
                    continue
                sv2 = {k: v for k, v in sv.items() if k != "_pnl_all"}
                if "folds" in sv2:
                    sv2["folds"] = [{k: v for k, v in f.items() if k != "pnl_values"} for f in sv2["folds"]]
                cs[sk] = sv2
            serialisable[ck] = cs
        serialisable["_portfolio_combination"] = portfolio_results
        serialisable["_grand_summary"] = grand_summary
        serialisable["_meta"] = meta

        try:
            with open(args.output, "w") as fp:
                json.dump(serialisable, fp, indent=2, default=str)
            log(f"\nJSON results saved to: {args.output}")
        except Exception as e:
            log(f"Warning: Could not save to {args.output}: {e}")
            alt = "/tmp/opus_discovery_iter4_results.json"
            try:
                with open(alt, "w") as fp:
                    json.dump(serialisable, fp, indent=2, default=str)
                log(f"JSON results saved to: {alt}")
            except Exception:
                pass

        # Write markdown report
        report_path = args.report
        if report_path is None:
            if os.path.isdir("/app"):
                report_path = "/scripts/opus_iter4_omnistrat_results.md"
            else:
                report_path = os.path.join(os.path.dirname(__file__), "..", "..",
                                           ".claude", "output", "sentiment_audit",
                                           "08_opus_iter4_omnistrat_results.md")
        write_markdown_report(report, results_by_config, portfolio_results, meta, report_path)

        log(f"\nIteration 4 complete. Wall clock: {wall_clock:.0f}s")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
