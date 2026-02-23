#!/usr/bin/env python3
"""
Opus 4.6 Forward Predictor Discovery — Iteration 3
====================================================
Vectorized + Properly Parallelized + Fixed Cost Model

Fixes from Iteration 2:
1. COST BUG FIX: L/S cost cancellation. Costs embedded in target cancel
   in dollar-neutral portfolios. Now: compute PnL from GROSS returns,
   subtract cost_per_side from each leg independently.
2. VECTORIZED GBStumps: Inner loop replaced with numpy batch operations.
   Old: 200 stumps × 41 features × 20 thresholds = 164K Python iterations.
   New: 200 stumps × 1 vectorized feature scan = 200 Python iterations.
3. VECTORIZED XS rank: argsort-argsort trick, no per-timestamp Python loop.
4. VECTORIZED XS L/S evaluation: full (T, N) matrix ops, no timestamp loop.
5. MULTIPROCESS across strategies × folds: Pool(12) for all independent work.

Expected CPU profile:
- Phase 1: ~10s, 1 core (I/O bound, DB fetch)
- Phase 2: ~3s, 8-12 cores (numpy BLAS for rolling ops)
- Phase 3: ~5-8 min, 12 cores sustained (12 workers across strategies × folds)
- Phase 4: <1s, 1 core (reporting)

Total: ~6 min. CPU utilisation: ~75% for ~5 min, tapering to ~50% for ~1 min.
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

TOP_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "BNBUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
    "PEPEUSDT", "SUIUSDT", "HBARUSDT", "XLMUSDT", "SHIBUSDT",
    "TRXUSDT", "LTCUSDT", "NEARUSDT", "MATICUSDT", "UNIUSDT",
]

HORIZONS = [6, 24, 72]
PURGE_BARS = 24
MIN_TRAIN_BARS = 100  # Per-symbol: 100 bars × 20 symbols = 2000 obs
MIN_TEST_BARS = 25    # 25 bars × 20 symbols = 500 obs
N_FOLDS = 5
TIMEFRAME = "1h"

# Transaction costs (CLAUDE.md Section 4.4)
SLIPPAGE_BPS = 5.0
FEE_BPS = 4.0
COST_PER_SIDE_BPS = SLIPPAGE_BPS + FEE_BPS  # 9 bps per side
# For L/S: each leg pays cost_per_side on entry + exit = 2 × 9 = 18 bps per leg
# + XSM penalty of 5 bps per leg
XSM_PENALTY_PER_SIDE_BPS = 2.5  # 5 bps total split across 2 sides
COST_PER_LEG_BPS = 2.0 * (COST_PER_SIDE_BPS + XSM_PENALTY_PER_SIDE_BPS)  # 23 bps

QUINTILE_SIZE = 0.2
COST_MULTIPLIERS = [1.0, 1.5, 2.0]


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ======================================================================
# Data Loading (unchanged from iter2)
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
    log(f"  OHLCV matrix: ({T}, {N}), fill rate: {np.isfinite(mats['close']).mean():.2%}")
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
# Vectorized Utilities
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
    """Vectorized XS rank using argsort-argsort trick. No Python loops."""
    T, N = x.shape
    out = np.full_like(x, np.nan)

    # For each row: rank = argsort(argsort(x))
    # Handle NaN: set to +inf so they sort last, then mask them out
    x_fill = np.where(np.isfinite(x), x, np.inf)
    order = np.argsort(x_fill, axis=1)  # (T, N) — indices that sort each row
    ranks = np.argsort(order, axis=1).astype(float)  # (T, N) — rank of each element

    # Count valid per row for normalisation
    valid_mask = np.isfinite(x)
    valid_count = valid_mask.sum(axis=1, keepdims=True).astype(float)  # (T, 1)
    valid_count[valid_count <= 1] = np.nan  # Need at least 2 for meaningful rank

    # Normalise to [0, 1]
    ranks = ranks / (valid_count - 1)
    ranks[~valid_mask] = np.nan
    return ranks


def _cross_sectional_zscore_vectorized(x: np.ndarray) -> np.ndarray:
    """Vectorized XS z-score. No Python loops."""
    valid_mask = np.isfinite(x)
    # Compute row-wise mean and std using masked operations
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


# ======================================================================
# Feature Engineering
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

    # Cross-symbol
    btc_ret_1 = ret_1[:, 0:1]
    btc_ret_6 = ret_6[:, 0:1]
    btc_ret_24 = ret_24[:, 0:1]
    features["idiosyncratic_ret_1h"] = ret_1 - btc_ret_1
    features["idiosyncratic_ret_6h"] = ret_6 - btc_ret_6
    features["idiosyncratic_ret_24h"] = ret_24 - btc_ret_24

    # Social
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
    features["mention_vel_z_72w"] = (mention_vel - _rolling_mean(mention_vel, 72)) / np.where(
        mv_std > 0, mv_std, np.nan
    )

    # Cross-sectional ranks (VECTORIZED)
    features["cs_rank_mentions_6h"] = _cross_sectional_rank_vectorized(mentions_6h)
    features["cs_rank_mentions_24h"] = _cross_sectional_rank_vectorized(mentions_24h)
    features["cs_rank_engagement_24h"] = _cross_sectional_rank_vectorized(engagement_24h)
    features["cs_rank_ret_24h"] = _cross_sectional_rank_vectorized(ret_24)
    features["cs_rank_ret_72h"] = _cross_sectional_rank_vectorized(ret_72)
    features["cs_rank_volume"] = _cross_sectional_rank_vectorized(relative_volume)
    features["cs_rank_vol"] = _cross_sectional_rank_vectorized(realized_vol_24)
    features["cs_rank_author_concentration"] = _cross_sectional_rank_vectorized(author_concentration)
    features["cs_rank_social_price_div"] = _cross_sectional_rank_vectorized(mentions_z - np.abs(ret_z))

    # Cross-sectional z-scores (VECTORIZED)
    features["cs_z_mentions_6h"] = _cross_sectional_zscore_vectorized(mentions_6h)
    features["cs_z_ret_24h"] = _cross_sectional_zscore_vectorized(ret_24)
    features["cs_z_vol"] = _cross_sectional_zscore_vectorized(realized_vol_24)

    # Composite signals
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

    for k in features:
        features[k] = np.clip(features[k], -100, 100)

    elapsed = time.time() - t0
    log(f"  Engineered {len(features)} features in {elapsed:.1f}s")
    return features, sorted(features.keys())


# ======================================================================
# Vectorized GBStumps
# ======================================================================

class GBStumpsVectorized:
    """Gradient-boosted decision stumps — vectorized inner loop.

    Key optimization: Instead of looping over features × thresholds in Python,
    we compute ALL splits simultaneously as numpy operations:
    - Pre-compute threshold masks: (n_features, n_thresholds, n_samples) bool
    - For each stump: compute all left/right means in one vectorized pass
    - Pick best split from the gain matrix

    This turns O(n_stumps × n_features × n_thresholds) Python iterations
    into O(n_stumps) iterations with numpy batch ops inside.
    """

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

        # Pre-compute threshold candidates per feature: (p, n_thresholds)
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

            # VECTORIZED: compute all (feature, threshold) splits at once
            # Xi: (ni, p), thresholds: (p, n_thresholds)
            # For each (j, t): left_mask = Xi[:, j] <= thresholds[j, t]

            # Reshape for broadcasting:
            # Xi_expanded: (ni, p, 1)
            # thresholds_expanded: (1, p, n_thresholds)
            Xi_exp = Xi[:, :, np.newaxis]  # (ni, p, 1)
            thr_exp = thresholds[np.newaxis, :, :]  # (1, p, n_thresholds)

            left_masks = Xi_exp <= thr_exp  # (ni, p, n_thresholds)

            # Count left/right per split
            n_left = left_masks.sum(axis=0)  # (p, n_thresholds)
            n_right = ni - n_left

            # Sum of residuals in left/right
            ri_exp = ri[:, np.newaxis, np.newaxis]  # (ni, 1, 1)
            left_sum = (left_masks * ri_exp).sum(axis=0)  # (p, n_thresholds)
            right_sum = ri.sum() - left_sum

            # Mean of residuals
            n_left_safe = np.where(n_left > 0, n_left, 1).astype(float)
            n_right_safe = np.where(n_right > 0, n_right, 1).astype(float)
            left_mean = left_sum / n_left_safe
            right_mean = right_sum / n_right_safe

            # MSE reduction (gain)
            gain = n_left * left_mean ** 2 + n_right * right_mean ** 2

            # Mask out splits with too few samples
            valid_split = (n_left >= 5) & (n_right >= 5)
            gain[~valid_split] = -1.0

            # Find best split
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

            # Update residual on full dataset
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
# Walk-Forward Infrastructure
# ======================================================================

def build_temporal_folds(T: int, purge_bars: int, min_train: int, min_test: int,
                         n_folds: int) -> list[tuple[int, int, int, int]]:
    """Returns list of (train_start, train_end, test_start, test_end) as bar indices."""
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
    """Gross forward returns (NO costs embedded)."""
    future_close = _lead(close_mat, horizon)
    return future_close / close_mat - 1.0


# ======================================================================
# Strategy Evaluators (all take a standard fold tuple as input)
# ======================================================================

def _eval_xs_ls_fold(args: tuple) -> dict:
    """Vectorized XS L/S. No per-timestamp Python loop.

    FIXED COST MODEL: PnL = long_gross_ret - short_gross_ret - 2 * cost_per_leg
    (Both legs pay costs independently — costs never cancel.)
    """
    fold_i, ranking_feat_test, gross_ret_test, test_ts_days, \
        quintile_size, cost_per_leg, strategy_name = args

    T_test, N = ranking_feat_test.shape

    # Valid mask: both rank and return must be finite
    valid = np.isfinite(ranking_feat_test) & np.isfinite(gross_ret_test)
    valid_count = valid.sum(axis=1)  # (T_test,) — number of valid symbols per bar

    # Minimum valid symbols to form quintiles
    min_valid = max(5, int(N * quintile_size * 2))
    tradeable = valid_count >= min_valid  # (T_test,)

    if tradeable.sum() == 0:
        return {"fold": fold_i, "strategy": strategy_name, "error": "no tradeable bars"}

    # For each tradeable bar: find quintile cutoffs
    # We use the rank directly — bottom quintile = rank < 0.2, top = rank > 0.8
    # This is valid because ranks are already in [0, 1]
    low_thresh = quintile_size  # 0.2
    high_thresh = 1.0 - quintile_size  # 0.8

    # Long positions: rank <= low_thresh (bottom quintile)
    long_mask = tradeable[:, np.newaxis] & valid & (ranking_feat_test <= low_thresh)
    # Short positions: rank >= high_thresh (top quintile)
    short_mask = tradeable[:, np.newaxis] & valid & (ranking_feat_test >= high_thresh)

    # Count positions per bar
    n_long = long_mask.sum(axis=1).astype(float)
    n_short = short_mask.sum(axis=1).astype(float)

    # Average gross return per leg
    long_ret = np.where(n_long > 0,
                        np.where(long_mask, gross_ret_test, 0.0).sum(axis=1) / np.maximum(n_long, 1),
                        0.0)
    short_ret = np.where(n_short > 0,
                         np.where(short_mask, gross_ret_test, 0.0).sum(axis=1) / np.maximum(n_short, 1),
                         0.0)

    # FIXED PnL: both legs pay costs independently
    cost_frac = cost_per_leg / 10000.0
    portfolio_pnl = (long_ret - short_ret) / 2.0 - cost_frac  # Cost on each leg

    # Filter to tradeable bars
    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = test_ts_days[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": strategy_name, "error": "no PnL computed"}

    # Daily PF
    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)

    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": strategy_name,
        "test_n": int(len(pnl_arr)),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_gbstumps_fold(args: tuple) -> dict:
    """GBStumps with FIXED cost model."""
    fold_i, X_train, y_gross_train, X_test, y_gross_test, \
        test_ts_days, feature_names, cost_per_leg = args

    # Standardise
    mean = np.nanmean(X_train, axis=0)
    std = np.nanstd(X_train, axis=0)
    std[std == 0] = 1.0
    Xn_train = np.nan_to_num((X_train - mean) / std, nan=0.0, posinf=0.0, neginf=0.0)
    Xn_test = np.nan_to_num((X_test - mean) / std, nan=0.0, posinf=0.0, neginf=0.0)
    # Train on GROSS returns (model learns the signal, not the costs)
    y_train_clean = np.nan_to_num(y_gross_train, nan=0.0)

    model = GBStumpsVectorized(n_stumps=200, learning_rate=0.05, subsample=0.8, n_thresholds=20)
    model.fit(Xn_train, y_train_clean)
    y_pred = model.predict(Xn_test)

    direction = np.sign(y_pred)
    # FIXED: PnL = direction * gross_return - |cost| (always subtract cost)
    cost_frac = cost_per_leg / 10000.0
    pnl = direction * y_gross_test - cost_frac

    valid = np.isfinite(y_gross_test)
    if valid.sum() == 0:
        return {"fold": fold_i, "strategy": "gbstumps", "error": "no valid test data"}

    pnl_valid = pnl[valid]
    ts_days_valid = test_ts_days[valid]

    accuracy = float(np.mean((y_gross_test[valid] > 0).astype(float) == (y_pred[valid] > 0).astype(float)))
    daily = _compute_daily_pf_vectorized(ts_days_valid, pnl_valid)
    feat_imp = model.feature_importance(feature_names)

    total_profit = float(np.sum(pnl_valid[pnl_valid > 0]))
    total_loss = float(np.abs(np.sum(pnl_valid[pnl_valid < 0])))

    return {
        "fold": fold_i,
        "strategy": "gbstumps",
        "train_n": len(y_gross_train),
        "test_n": int(valid.sum()),
        "accuracy": accuracy,
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "trading_days": daily["trading_days"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_valid.mean() * 10000),
        "sharpe": float(pnl_valid.mean() / max(1e-10, pnl_valid.std())),
        "top_features": dict(sorted(feat_imp.items(), key=lambda x: -x[1])[:15]),
        "pnl_values": pnl_valid.tolist(),
    }


def _eval_regime_xs_fold(args: tuple) -> dict:
    """Regime-conditional XS L/S. Vectorized."""
    fold_i, rank_feat_test, vol_regime_test, gross_ret_test, \
        test_ts_days, quintile_size, cost_per_leg = args

    T_test, N = rank_feat_test.shape
    valid = np.isfinite(rank_feat_test) & np.isfinite(gross_ret_test) & np.isfinite(vol_regime_test)
    valid_count = valid.sum(axis=1)
    min_valid = max(6, int(N * quintile_size * 2))
    tradeable = valid_count >= min_valid

    if tradeable.sum() == 0:
        return {"fold": fold_i, "strategy": "regime_conditional", "error": "no tradeable bars"}

    low_thresh = quintile_size
    high_thresh = 1.0 - quintile_size

    long_mask = tradeable[:, np.newaxis] & valid & (rank_feat_test <= low_thresh)
    short_mask = tradeable[:, np.newaxis] & valid & (rank_feat_test >= high_thresh)

    n_long = long_mask.sum(axis=1).astype(float)
    n_short = short_mask.sum(axis=1).astype(float)

    long_ret = np.where(n_long > 0,
                        np.where(long_mask, gross_ret_test, 0.0).sum(axis=1) / np.maximum(n_long, 1), 0.0)
    short_ret = np.where(n_short > 0,
                         np.where(short_mask, gross_ret_test, 0.0).sum(axis=1) / np.maximum(n_short, 1), 0.0)

    # Regime-adaptive weighting: high vol → more contrarian (short emphasis)
    vol_median = np.nanmedian(vol_regime_test, axis=1)  # (T_test,)
    bar_vol_mean = np.nanmean(np.where(valid, vol_regime_test, np.nan), axis=1)
    high_vol_mask = bar_vol_mean > vol_median

    long_wt = np.where(high_vol_mask, 0.3, 0.7)
    short_wt = np.where(high_vol_mask, 0.7, 0.3)

    cost_frac = cost_per_leg / 10000.0
    portfolio_pnl = long_wt * long_ret - short_wt * short_ret - cost_frac

    pnl_arr = portfolio_pnl[tradeable]
    ts_days_arr = test_ts_days[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "regime_conditional", "error": "no PnL"}

    daily = _compute_daily_pf_vectorized(ts_days_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "regime_conditional",
        "test_n": int(len(pnl_arr)),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


# ======================================================================
# Daily PF (vectorized)
# ======================================================================

def _compute_daily_pf_vectorized(ts_days: np.ndarray, pnl: np.ndarray) -> dict:
    """Compute per-day PF then average. ts_days is int array of day indices."""
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
# DSR
# ======================================================================

def _compute_dsr(observed_sr: float, n_trials: int, n_obs: int) -> dict:
    if n_trials <= 0 or n_obs <= 1:
        return {"dsr": 0.0, "e_max_sr": 0.0, "p_value": 1.0}
    gamma = 0.5772156649
    z1 = _ppf(1.0 - 1.0 / max(2, n_trials))
    z2 = _ppf(1.0 - 1.0 / max(2, n_trials * math.e))
    e_max_sr = (1.0 - gamma) * z1 + gamma * z2
    sr_var = 1.0 / max(1, n_obs - 1)
    test_stat = (observed_sr - e_max_sr) / max(1e-10, math.sqrt(sr_var))
    p_value = 1.0 - 0.5 * (1.0 + math.erf(test_stat / math.sqrt(2.0)))
    return {"dsr": float(observed_sr - e_max_sr), "e_max_sr": float(e_max_sr), "p_value": float(p_value)}


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


# ======================================================================
# Orchestrator — builds ALL jobs and runs in single Pool(12)
# ======================================================================

def _dispatch_job(args: tuple) -> dict:
    """Universal dispatcher for multiprocessing Pool."""
    job_type = args[0]
    if job_type == "xs_ls":
        return _eval_xs_ls_fold(args[1])
    elif job_type == "gbstumps":
        return _eval_gbstumps_fold(args[1])
    elif job_type == "regime":
        return _eval_regime_xs_fold(args[1])
    return {"error": f"unknown job type: {job_type}"}


def build_all_jobs(
    features: dict[str, np.ndarray],
    feature_names: list[str],
    ohlcv: dict,
    horizons: list[int],
    cost_multipliers: list[float],
) -> list[tuple]:
    """Pre-build ALL jobs across horizons × costs × strategies × folds.

    Returns list of (job_type, job_args) tuples for the Pool.
    Also returns a mapping to reconstruct results: job_key → (config_key, strategy_name, fold_i).
    """
    T, N = ohlcv["close"].shape
    timestamps = ohlcv["timestamps"]

    # Pre-compute day indices for daily PF (int array, not datetime objects — picklable)
    day_indices = np.array([(ts - timestamps[0]).days for ts in timestamps], dtype=np.int32)

    folds = build_temporal_folds(T, PURGE_BARS, MIN_TRAIN_BARS, MIN_TEST_BARS, N_FOLDS)
    log(f"  Built {len(folds)} temporal folds for {T} bars × {N} symbols")

    if not folds:
        return [], {}

    # Pre-stack feature array for GBStumps
    feat_array = np.stack([features[name] for name in feature_names], axis=2)
    feat_array = np.clip(feat_array, -100, 100)
    F = len(feature_names)
    X_flat = feat_array.reshape(T * N, F)
    X_flat = np.nan_to_num(X_flat, nan=0.0, posinf=0.0, neginf=0.0)

    # Flatten day indices for GBStumps
    day_flat = np.repeat(day_indices, N)

    # XS L/S ranking features to test
    ranking_features = [
        "cs_rank_mentions_6h", "cs_rank_mentions_24h", "cs_rank_engagement_24h",
        "cs_rank_ret_24h", "cs_rank_author_concentration",
        "contrarian_social_composite", "momentum_confirmation_composite",
        "attention_price_divergence", "cs_rank_social_price_div",
    ]

    # Interaction pairs
    interaction_pairs = [
        ("cs_rank_author_concentration", "cs_rank_ret_24h"),
        ("cs_rank_mentions_24h", "cs_rank_ret_24h"),
        ("cs_rank_mentions_6h", "cs_rank_vol"),
        ("cs_rank_engagement_24h", "cs_rank_volume"),
        ("cs_rank_mentions_24h", "cs_rank_volume"),
        ("cs_z_mentions_6h", "cs_z_vol"),
    ]

    all_jobs = []
    job_keys = []  # Parallel array: (config_key, strategy_name, fold_i)

    for horizon in horizons:
        gross_ret = compute_gross_forward_returns(ohlcv["close"], horizon)
        y_gross_flat = gross_ret.reshape(T * N)

        for cost_mult in cost_multipliers:
            config_key = f"h{horizon}_cost{cost_mult}x"
            cost_per_leg = COST_PER_LEG_BPS * cost_mult

            # --- XS L/S for each ranking feature ---
            for rank_feat_name in ranking_features:
                if rank_feat_name not in features:
                    continue
                rank_feat = features[rank_feat_name]
                strat_name = f"xs_ls_{rank_feat_name}"

                for fi, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
                    job_args = (
                        fi + 1,
                        rank_feat[te_s:te_e],
                        gross_ret[te_s:te_e],
                        day_indices[te_s:te_e],
                        QUINTILE_SIZE,
                        cost_per_leg,
                        strat_name,
                    )
                    all_jobs.append(("xs_ls", job_args))
                    job_keys.append((config_key, strat_name, fi + 1))

            # --- XS L/S for interaction pairs ---
            for feat_a, feat_b in interaction_pairs:
                if feat_a not in features or feat_b not in features:
                    continue
                composite = (features[feat_a] + features[feat_b]) / 2.0
                strat_name = f"xs_interaction_{feat_a}_x_{feat_b}"

                for fi, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
                    job_args = (
                        fi + 1,
                        composite[te_s:te_e],
                        gross_ret[te_s:te_e],
                        day_indices[te_s:te_e],
                        QUINTILE_SIZE,
                        cost_per_leg,
                        strat_name,
                    )
                    all_jobs.append(("xs_ls", job_args))
                    job_keys.append((config_key, strat_name, fi + 1))

            # --- GBStumps ---
            for fi, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
                train_rows = np.arange(tr_s * N, tr_e * N)
                test_rows = np.arange(te_s * N, te_e * N)
                train_valid = np.isfinite(y_gross_flat[train_rows])
                test_valid = np.isfinite(y_gross_flat[test_rows])

                job_args = (
                    fi + 1,
                    X_flat[train_rows[train_valid]],
                    y_gross_flat[train_rows[train_valid]],
                    X_flat[test_rows[test_valid]],
                    y_gross_flat[test_rows[test_valid]],
                    day_flat[test_rows[test_valid]],
                    feature_names,
                    cost_per_leg,
                )
                all_jobs.append(("gbstumps", job_args))
                job_keys.append((config_key, "gbstumps", fi + 1))

            # --- Regime-conditional ---
            rank_feat = features.get("cs_rank_mentions_24h", np.full((T, N), np.nan))
            vol_regime = features.get("vol_regime_ratio", np.full((T, N), np.nan))

            for fi, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
                job_args = (
                    fi + 1,
                    rank_feat[te_s:te_e],
                    vol_regime[te_s:te_e],
                    gross_ret[te_s:te_e],
                    day_indices[te_s:te_e],
                    QUINTILE_SIZE,
                    cost_per_leg,
                )
                all_jobs.append(("regime", job_args))
                job_keys.append((config_key, "regime_conditional_mentions", fi + 1))

    return all_jobs, job_keys


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

    dsr = _compute_dsr(oos_sharpe, n_trials=max(1, len(valid_folds)), n_obs=len(all_pnl))

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
        "dsr": dsr["dsr"],
        "dsr_e_max_sr": dsr["e_max_sr"],
        "dsr_p_value": dsr["p_value"],
        "folds": [{k: v for k, v in f.items() if k != "pnl_values"} for f in fold_results],
        "top_features": _merge_feature_importances(valid_folds),
    }


def _merge_feature_importances(folds: list[dict]) -> dict:
    merged = defaultdict(float)
    count = 0
    for f in folds:
        fi = f.get("top_features", {})
        if fi:
            count += 1
            for feat, score in fi.items():
                merged[feat] += score
    if count > 0:
        merged = {k: v / count for k, v in merged.items()}
    return dict(sorted(merged.items(), key=lambda x: -x[1])[:20])


# ======================================================================
# Reporting
# ======================================================================

def format_all_results(results_by_config: dict) -> str:
    lines = []
    for config_key in sorted(results_by_config.keys()):
        if config_key.startswith("_"):
            continue
        config = results_by_config[config_key]
        parts = config_key.split("_")
        horizon = parts[0].replace("h", "") + "h"
        cost = parts[1].replace("cost", "").replace("x", "") + "x"

        lines.append(f"\n{'='*90}")
        lines.append(f"HORIZON: {horizon} | COST: {cost} | Cost per leg: {config.get('_cost_per_leg', '?')} bps")
        lines.append(f"{'='*90}")

        strats = [(k, v) for k, v in config.items() if isinstance(v, dict) and "strategy" in v]
        strats.sort(key=lambda x: x[1].get("aggregate_pf", 0), reverse=True)

        lines.append(f"\n{'Strategy':<55s} {'AvgDPF':>7s} {'AggPF':>7s} {'Sharpe':>7s} {'MeanBps':>8s} {'WF%':>5s} {'DSR':>7s} {'Status':>8s}")
        lines.append("-" * 106)

        for key, val in strats:
            if "error" in val:
                lines.append(f"{key:<55s} {'ERROR':>7s} - {val['error']}")
                continue
            dpf = val.get("avg_daily_pf", 0)
            apf = val.get("aggregate_pf", 0)
            sharpe = val.get("oos_sharpe", 0)
            mean_bps = val.get("oos_mean_pnl_bps", 0)
            wf_rate = val.get("wf_pass_rate", 0)
            dsr = val.get("dsr", 0)

            if dpf > 1.0 and apf > 1.0 and dsr > 0 and wf_rate >= 0.8:
                status = "PASS"
            elif dpf > 1.0 and apf > 1.0:
                status = "EDGE?"
            elif apf > 1.0:
                status = "WEAK"
            else:
                status = "FAIL"

            lines.append(
                f"{key:<55s} {dpf:>7.3f} {apf:>7.3f} {sharpe:>7.3f} {mean_bps:>8.1f} {wf_rate:>4.0%} {dsr:>7.3f} {status:>8s}"
            )

        # Detail for top 3
        for key, val in strats[:3]:
            if "error" in val:
                continue
            lines.append(f"\n  [{key}]")
            lines.append(f"  Folds: {val.get('n_folds', 0)}, Profitable: {val.get('profitable_folds', 0)}, Trades: {val.get('oos_total_trades', 0)}")
            lines.append(f"  DSR: {val.get('dsr', 0):.3f}, E[max(SR)]: {val.get('dsr_e_max_sr', 0):.3f}")
            if val.get("top_features"):
                lines.append(f"  Top features:")
                for feat, score in list(val["top_features"].items())[:8]:
                    lines.append(f"    {feat:40s}  {score:.6f}")
            for f in val.get("folds", []):
                if "error" in f:
                    lines.append(f"    Fold {f.get('fold', '?')}: ERROR - {f['error']}")
                else:
                    dpf_f = f.get("avg_daily_pf", 0)
                    pf_mark = "+" if dpf_f > 1.0 else "-"
                    acc = f.get("accuracy", "N/A")
                    acc_str = f"{acc:.3f}" if isinstance(acc, float) else str(acc)
                    lines.append(
                        f"    Fold {f.get('fold', '?')}: n={f.get('test_n', 0):>5d}  "
                        f"dpf={dpf_f:.3f}[{pf_mark}]  agg_pf={f.get('aggregate_pf', 0):.3f}  "
                        f"sharpe={f.get('sharpe', 0):.3f}  acc={acc_str}  days={f.get('trading_days', 0)}"
                    )

    return "\n".join(lines)


# ======================================================================
# MAIN
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="Opus 4.6 Forward Predictor Discovery — Iteration 3")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--symbols", default=",".join(TOP_SYMBOLS))
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", default="/scripts/opus_discovery_iter3_results.json")
    parser.add_argument("--horizons", default=",".join(str(h) for h in HORIZONS))
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    db_url = args.database_url or DB_URL_DEFAULT
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(h.strip()) for h in args.horizons.split(",")]

    log("=" * 90)
    log("Opus 4.6 Forward Predictor Discovery — ITERATION 3 (Vectorized + Fixed Costs)")
    log("=" * 90)
    log(f"Symbols: {len(symbols)}, Lookback: {args.lookback_days}d, Workers: {args.workers}")
    log(f"Horizons: {horizons}, Quintile: {QUINTILE_SIZE:.0%}")
    log(f"Cost per leg (base): {COST_PER_LEG_BPS} bps (slippage {SLIPPAGE_BPS} + fee {FEE_BPS} + XSM {XSM_PENALTY_PER_SIDE_BPS}) × 2 sides")
    log(f"Cost multipliers: {COST_MULTIPLIERS}")

    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=args.lookback_days)

    conn = psycopg.connect(db_url, row_factory=dict_row)
    try:
        # Phase 1: Load data
        ohlcv = load_ohlcv_matrix(conn, symbols, start_ts, end_ts)
        social_mat, social_cols = load_social_matrix(conn, symbols, ohlcv["timestamps"])

        # Phase 2: Engineer features
        features, feature_names = engineer_features(ohlcv, social_mat)
        log(f"Total features: {len(feature_names)}")

        # Phase 3: Build ALL jobs, dispatch to Pool(12)
        t_start = time.time()
        all_jobs, job_keys = build_all_jobs(features, feature_names, ohlcv, horizons, COST_MULTIPLIERS)
        log(f"Built {len(all_jobs)} total jobs across {len(horizons)} horizons × {len(COST_MULTIPLIERS)} costs × strategies × {N_FOLDS} folds")

        if not all_jobs:
            log("ERROR: No jobs to run.")
            return

        # Run everything in one Pool
        log(f"Dispatching to Pool({args.workers})...")
        with Pool(args.workers) as pool:
            results_flat = pool.map(_dispatch_job, all_jobs)

        elapsed = time.time() - t_start
        log(f"All {len(results_flat)} jobs completed in {elapsed:.1f}s")

        # Reconstruct: group results by (config_key, strategy_name)
        grouped: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for i, result in enumerate(results_flat):
            config_key, strat_name, fold_i = job_keys[i]
            grouped[config_key][strat_name].append(result)

        # Aggregate per strategy
        results_by_config = {}
        grand_summary = []

        for config_key in sorted(grouped.keys()):
            config_results = {}
            for strat_name, fold_results in grouped[config_key].items():
                config_results[strat_name] = _aggregate_strategy_results(fold_results, strat_name)

            # Add cost info
            parts = config_key.split("_")
            cost_mult = float(parts[1].replace("cost", "").replace("x", ""))
            config_results["_cost_per_leg"] = COST_PER_LEG_BPS * cost_mult
            results_by_config[config_key] = config_results

            # Best strategy for summary
            best_strat, best_apf = None, 0.0
            for k, v in config_results.items():
                if isinstance(v, dict) and v.get("aggregate_pf", 0) > best_apf:
                    best_apf = v["aggregate_pf"]
                    best_strat = k
            if best_strat:
                v = config_results[best_strat]
                grand_summary.append({
                    "config": config_key,
                    "best_strategy": best_strat,
                    "avg_daily_pf": v.get("avg_daily_pf", 0),
                    "aggregate_pf": v.get("aggregate_pf", 0),
                    "oos_sharpe": v.get("oos_sharpe", 0),
                    "dsr": v.get("dsr", 0),
                    "wf_pass_rate": v.get("wf_pass_rate", 0),
                    "mean_pnl_bps": v.get("oos_mean_pnl_bps", 0),
                })

        # Phase 4: Report
        report = format_all_results(results_by_config)
        print(report)

        log("\n" + "=" * 90)
        log("GRAND SUMMARY — ITERATION 3 (FIXED COSTS)")
        log("=" * 90)
        log(f"\n{'Config':<20s} {'Best Strategy':<45s} {'AvgDPF':>7s} {'AggPF':>7s} {'Sharpe':>7s} {'BPS':>6s} {'DSR':>7s} {'WF%':>5s}")
        log("-" * 105)
        for s in grand_summary:
            dpf = s["avg_daily_pf"]
            apf = s["aggregate_pf"]
            sharpe = s["oos_sharpe"]
            dsr = s["dsr"]
            wf = s["wf_pass_rate"]
            bps = s.get("mean_pnl_bps", 0)
            if dpf > 1.0 and apf > 1.0 and dsr > 0 and wf >= 0.8:
                status = " *** TRADEABLE ***"
            elif dpf > 1.0 and apf > 1.0:
                status = " << EDGE? >>"
            elif apf > 1.0:
                status = " (weak)"
            else:
                status = ""
            log(f"  {s['config']:<18s} {s['best_strategy']:<45s} {dpf:>7.3f} {apf:>7.3f} {sharpe:>7.3f} {bps:>6.1f} {dsr:>7.3f} {wf:>4.0%}{status}")

        # Save
        serialisable = {}
        for ck, cr in results_by_config.items():
            cs = {}
            for sk, sv in cr.items():
                if not isinstance(sv, dict):
                    cs[sk] = sv
                    continue
                sv2 = {k: v for k, v in sv.items()}
                if "folds" in sv2:
                    sv2["folds"] = [{k: v for k, v in f.items() if k != "pnl_values"} for f in sv2["folds"]]
                cs[sk] = sv2
            serialisable[ck] = cs
        serialisable["_grand_summary"] = grand_summary
        serialisable["_meta"] = {
            "script": "opus_discovery_iter3_vectorized.py",
            "date": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "horizons": horizons,
            "cost_model": "FIXED — costs always subtracted, never cancel in L/S",
            "cost_per_leg_base_bps": COST_PER_LEG_BPS,
            "cost_multipliers": COST_MULTIPLIERS,
            "quintile_size": QUINTILE_SIZE,
            "n_folds": N_FOLDS,
            "purge_bars": PURGE_BARS,
            "n_features": len(feature_names),
            "feature_names": feature_names,
            "total_jobs": len(all_jobs),
            "wall_clock_seconds": elapsed,
        }

        try:
            with open(args.output, "w") as fp:
                json.dump(serialisable, fp, indent=2, default=str)
            log(f"\nResults saved to: {args.output}")
        except Exception as e:
            log(f"Warning: Could not save to {args.output}: {e}")
            alt = "/tmp/opus_discovery_iter3_results.json"
            try:
                with open(alt, "w") as fp:
                    json.dump(serialisable, fp, indent=2, default=str)
                log(f"Results saved to: {alt}")
            except Exception:
                pass

        log(f"\nIteration 3 complete. Wall clock: {elapsed:.0f}s")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
