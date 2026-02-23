#!/usr/bin/env python3
"""
Opus 4.6 Forward Predictor Discovery — Iteration 2
====================================================
Cross-Sectional Rank Long-Short + Non-Linear Models

Iteration 1 found:
- Social features are #1 and #2 by importance (mentions_6h, mentions_24h)
- Linear model achieves aggregate PF ≈ 1.0 (breakeven)
- Daily PF > 1 but inconsistent — few catastrophic days destroy aggregate
- DSR fails across all configs (-1.2)

Iteration 2 changes the METHODOLOGY:
1. Cross-sectional long-short: Rank symbols each hour by social/price features.
   Long bottom quintile (low attention, contrarian), short top quintile.
   This is market-neutral and exploits RELATIVE structure, not absolute returns.

2. Non-linear model: Gradient-boosted decision stumps that can capture threshold
   effects (attention > X AND vol > Y → short). Social attention almost certainly
   has a non-linear relationship with forward returns.

3. Regime-conditional signals: Split by volatility regime and test separately.

4. Feature interaction screening: Systematic pairwise scan of top features.

Per CLAUDE.md Section 0.1: "Start from the data and find what works."
The data told us social features matter. Now we exploit them properly.

Parallelism: Phase 1 serial (I/O), Phase 2 vectorised numpy,
Phase 3 multiprocessing across folds × configs.
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

TOP_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "BNBUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
    "PEPEUSDT", "SUIUSDT", "HBARUSDT", "XLMUSDT", "SHIBUSDT",
    "TRXUSDT", "LTCUSDT", "NEARUSDT", "MATICUSDT", "UNIUSDT",
]

HORIZONS = [6, 24, 72]
PURGE_BARS = 24
MIN_TRAIN = 2000
MIN_TEST = 500
N_FOLDS = 5
TIMEFRAME = "1h"

# Transaction costs (CLAUDE.md Section 4.4)
SLIPPAGE_BPS = 5.0
FEE_BPS = 4.0
ROUND_TRIP_BPS = 2.0 * (SLIPPAGE_BPS + FEE_BPS)  # 18 bps

# Cross-sectional long-short specifics
# +5 bps penalty for multi-symbol strategies (CLAUDE.md Section 4.4)
XSM_PENALTY_BPS = 5.0
QUINTILE_SIZE = 0.2  # Top/bottom 20% for L/S

COST_MULTIPLIERS = [1.0, 1.5, 2.0]


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ======================================================================
# PHASE 1: Data Loading (reuse from iter1)
# ======================================================================

def load_ohlcv_matrix(conn, symbols: list[str], start_ts: datetime, end_ts: datetime):
    """Load OHLCV into aligned (T, N) matrices."""
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
    from psycopg.rows import dict_row

    log(f"Loading social features for {len(symbols)} symbols...")
    social_cols = [
        "mention_count", "unique_authors", "platform_count",
        "engagement_sum", "mention_velocity", "mention_acceleration",
        "author_novelty_ratio", "mention_volume_ratio",
    ]
    F = len(social_cols)
    T, N = len(timestamps), len(symbols)

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
# PHASE 2: Feature Engineering
# ======================================================================

def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    cs = np.nancumsum(x, axis=0)
    cnt = np.nancumsum(np.isfinite(x).astype(float), axis=0)
    out = np.full_like(x, np.nan)
    out[w - 1:] = (cs[w - 1:] - np.concatenate([np.zeros_like(cs[:1]), cs[:-1]], axis=0)[w - 1:])
    cnt_w = cnt[w - 1:] - np.concatenate([np.zeros_like(cnt[:1]), cnt[:-1]], axis=0)[w - 1:]
    cnt_w[cnt_w == 0] = np.nan
    out[w - 1:] /= cnt_w
    return out


def _rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    mean = _rolling_mean(x, w)
    sq_mean = _rolling_mean(x ** 2, w)
    var = sq_mean - mean ** 2
    var = np.clip(var, 0, None)
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
    T, N = x.shape
    out = np.full_like(x, np.nan)
    for t in range(T):
        row = x[t]
        valid = np.isfinite(row)
        if valid.sum() < 3:  # Need at least 3 for meaningful ranks
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


def _cross_sectional_zscore(x: np.ndarray) -> np.ndarray:
    """Z-score each row cross-sectionally."""
    T, N = x.shape
    out = np.full_like(x, np.nan)
    for t in range(T):
        row = x[t]
        valid = np.isfinite(row)
        if valid.sum() < 3:
            continue
        mu = np.nanmean(row[valid])
        sigma = np.nanstd(row[valid])
        if sigma > 1e-12:
            out[t, valid] = (row[valid] - mu) / sigma
    return out


def engineer_features(ohlcv: dict, social_mat: np.ndarray, social_cols: list[str]) -> tuple[dict, list[str]]:
    """Engineer features. Returns dict of (T, N) arrays + feature names.

    Unlike iter1, we return the dict directly so we can rank/combine flexibly.
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

    # --- Returns ---
    ret_1 = C / _lag(C, 1) - 1.0
    ret_6 = C / _lag(C, 6) - 1.0
    ret_24 = C / _lag(C, 24) - 1.0
    ret_72 = C / _lag(C, 72) - 1.0

    features["ret_1h"] = ret_1
    features["ret_6h"] = ret_6
    features["ret_24h"] = ret_24
    features["ret_72h"] = ret_72

    # --- Intrabar microstructure ---
    bar_range = H - L
    bar_range_safe = np.where(bar_range > 0, bar_range, np.nan)
    body_position = (C - L) / bar_range_safe
    body_top = np.maximum(O, C)
    body_bot = np.minimum(O, C)
    upper_wick = (H - body_top) / bar_range_safe
    lower_wick = (body_bot - L) / bar_range_safe
    body_size = np.abs(C - O) / bar_range_safe

    features["body_position"] = body_position
    features["upper_wick_ratio"] = upper_wick
    features["lower_wick_ratio"] = lower_wick
    features["body_size_ratio"] = body_size

    # --- Volatility ---
    realized_vol_24 = _rolling_std(ret_1, 24)
    realized_vol_72 = _rolling_std(ret_1, 72)
    vol_regime = realized_vol_24 / np.where(realized_vol_72 > 0, realized_vol_72, np.nan)
    vol_change_6h = realized_vol_24 - _lag(realized_vol_24, 6)
    vol_accel = vol_change_6h - _lag(vol_change_6h, 6)

    features["realized_vol_24"] = realized_vol_24
    features["vol_regime_ratio"] = vol_regime
    features["vol_acceleration"] = vol_accel

    # --- Volume ---
    vol_ma_24 = _rolling_mean(V, 24)
    vol_ma_72 = _rolling_mean(V, 72)
    relative_volume = V / np.where(vol_ma_24 > 0, vol_ma_24, np.nan)
    features["relative_volume_24"] = relative_volume
    features["volume_regime"] = vol_ma_24 / np.where(vol_ma_72 > 0, vol_ma_72, np.nan)

    # --- Cross-symbol ---
    btc_ret_1 = ret_1[:, 0:1]
    btc_ret_6 = ret_6[:, 0:1]
    btc_ret_24 = ret_24[:, 0:1]
    features["idiosyncratic_ret_1h"] = ret_1 - btc_ret_1
    features["idiosyncratic_ret_6h"] = ret_6 - btc_ret_6
    features["idiosyncratic_ret_24h"] = ret_24 - btc_ret_24

    # --- Social features ---
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

    # Author concentration (high = few authors dominate = low info content)
    author_concentration = np.where(
        authors_24h > 0, mentions_24h / authors_24h, np.nan
    )
    features["author_concentration_24h"] = author_concentration

    # Social-price coupling and divergence
    mentions_z = (mentions_6h - _rolling_mean(mentions_6h, 72)) / np.where(
        _rolling_std(mentions_6h, 72) > 0, _rolling_std(mentions_6h, 72), np.nan
    )
    ret_z = (ret_6 - _rolling_mean(ret_6, 72)) / np.where(
        _rolling_std(ret_6, 72) > 0, _rolling_std(ret_6, 72), np.nan
    )
    features["social_price_divergence"] = mentions_z - np.abs(ret_z)
    features["social_price_coupling_72w"] = _rolling_corr(mentions, np.abs(ret_1), 72)
    features["mention_acceleration_raw"] = mention_accel

    # Vol-attention coupling
    features["vol_attention_coupling"] = realized_vol_24 * mentions_z

    # Mention velocity z-score (social momentum)
    features["mention_vel_z_72w"] = (mention_vel - _rolling_mean(mention_vel, 72)) / np.where(
        _rolling_std(mention_vel, 72) > 0, _rolling_std(mention_vel, 72), np.nan
    )

    # --- Cross-sectional ranks of KEY features ---
    # These are the core signals for the long-short strategy
    features["cs_rank_mentions_6h"] = _cross_sectional_rank(mentions_6h)
    features["cs_rank_mentions_24h"] = _cross_sectional_rank(mentions_24h)
    features["cs_rank_engagement_24h"] = _cross_sectional_rank(engagement_24h)
    features["cs_rank_ret_24h"] = _cross_sectional_rank(ret_24)
    features["cs_rank_ret_72h"] = _cross_sectional_rank(ret_72)
    features["cs_rank_volume"] = _cross_sectional_rank(relative_volume)
    features["cs_rank_vol"] = _cross_sectional_rank(realized_vol_24)
    features["cs_rank_author_concentration"] = _cross_sectional_rank(author_concentration)
    features["cs_rank_social_price_div"] = _cross_sectional_rank(mentions_z - np.abs(ret_z))

    # Cross-sectional z-scores for interaction features
    features["cs_z_mentions_6h"] = _cross_sectional_zscore(mentions_6h)
    features["cs_z_ret_24h"] = _cross_sectional_zscore(ret_24)
    features["cs_z_vol"] = _cross_sectional_zscore(realized_vol_24)

    # --- NEW: Composite ranking features (multi-factor) ---
    # Contrarian social score: high mentions + high concentration + negative momentum
    # (crowd is piling in on a few voices → likely overextended)
    contrarian_raw = (
        _cross_sectional_rank(mentions_24h) * 0.4
        + _cross_sectional_rank(author_concentration) * 0.3
        + (1.0 - _cross_sectional_rank(ret_24)) * 0.3  # Reverse: high return → more contrarian SHORT signal
    )
    features["contrarian_social_composite"] = contrarian_raw

    # Momentum + attention confirmation: rising price WITH rising attention = momentum
    # Rising price WITHOUT attention = potential fakeout
    momentum_confirmation = (
        _cross_sectional_rank(ret_24) * 0.5
        + _cross_sectional_rank(mentions_24h) * 0.3
        + _cross_sectional_rank(engagement_24h) * 0.2
    )
    features["momentum_confirmation_composite"] = momentum_confirmation

    # Attention divergence: high attention change but low price change
    mention_change_24h = mentions_6h - _lag(mentions_6h, 24)
    ret_change_24h = np.abs(ret_24) - np.abs(_lag(ret_24, 24))
    features["attention_price_divergence"] = (
        _cross_sectional_rank(mention_change_24h)
        - _cross_sectional_rank(ret_change_24h)
    )

    # Clip all features
    for k in features:
        features[k] = np.clip(features[k], -100, 100)

    elapsed = time.time() - t0
    log(f"  Engineered {len(features)} features in {elapsed:.1f}s")
    return features, sorted(features.keys())


# ======================================================================
# PHASE 2b: Gradient-Boosted Decision Stumps (Pure Numpy)
# ======================================================================

class GBStumps:
    """Gradient-boosted decision stumps — pure numpy, no sklearn dependency.

    Each weak learner is a 1-level decision stump: picks the best feature
    and threshold to split on. This naturally captures threshold effects
    like "mentions > X AND vol > Y → short".

    Boosting assembles many such thresholds into a non-linear prediction.
    """

    def __init__(self, n_stumps: int = 200, learning_rate: float = 0.05,
                 subsample: float = 0.8, n_thresholds: int = 20):
        self.n_stumps = n_stumps
        self.lr = learning_rate
        self.subsample = subsample
        self.n_thresholds = n_thresholds
        self.stumps: list[dict] = []
        self.init_pred: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "GBStumps":
        n, p = X.shape
        self.init_pred = float(np.mean(y))
        residual = y - self.init_pred
        self.stumps = []

        # Pre-compute threshold candidates per feature
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
            # Subsample
            if self.subsample < 1.0:
                mask = rng.random(n) < self.subsample
                Xi, ri = X[mask], residual[mask]
            else:
                Xi, ri = X, residual
                mask = np.ones(n, dtype=bool)

            ni = Xi.shape[0]
            if ni < 10:
                continue

            best_gain = -1e30
            best_feat = 0
            best_thresh = 0.0
            best_left_val = 0.0
            best_right_val = 0.0

            # Find best stump: vectorised per feature
            for j in range(p):
                col = Xi[:, j]
                for ti in range(self.n_thresholds):
                    thr = thresholds[j, ti]
                    left_mask = col <= thr
                    right_mask = ~left_mask

                    n_left = left_mask.sum()
                    n_right = right_mask.sum()
                    if n_left < 5 or n_right < 5:
                        continue

                    left_val = ri[left_mask].mean()
                    right_val = ri[right_mask].mean()

                    # MSE reduction
                    gain = n_left * left_val ** 2 + n_right * right_val ** 2
                    if gain > best_gain:
                        best_gain = gain
                        best_feat = j
                        best_thresh = thr
                        best_left_val = left_val
                        best_right_val = right_val

            stump = {
                "feat": best_feat,
                "thresh": float(best_thresh),
                "left": float(best_left_val * self.lr),
                "right": float(best_right_val * self.lr),
            }
            self.stumps.append(stump)

            # Update residual (full dataset, not just subsample)
            pred_update = np.where(
                X[:, best_feat] <= best_thresh,
                stump["left"],
                stump["right"],
            )
            residual -= pred_update

            if (s_i + 1) % 50 == 0:
                mse = np.mean(residual ** 2)
                # Only log from main process (not from multiprocessing workers)
                pass

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
        """Feature importance by total absolute gain."""
        imp = defaultdict(float)
        for stump in self.stumps:
            fi = stump["feat"]
            gain = abs(stump["left"]) + abs(stump["right"])
            if fi < len(feature_names):
                imp[feature_names[fi]] += gain
        # Normalise
        total = sum(imp.values()) or 1.0
        return {k: v / total for k, v in imp.items()}


# ======================================================================
# PHASE 3: Walk-Forward Strategies
# ======================================================================

def compute_forward_returns(close_mat: np.ndarray, horizon: int, cost_bps: float) -> np.ndarray:
    """Forward returns with transaction costs."""
    future_close = _lead(close_mat, horizon)
    gross_ret = future_close / close_mat - 1.0
    return gross_ret - (cost_bps / 10000.0)


def _compute_daily_pf(timestamps: list, pnl_values: list[float]) -> dict:
    """Compute per-day PF then average. CLAUDE.md Section 4.5."""
    daily_pnl: dict[str, list[float]] = defaultdict(list)
    for ts, pnl in zip(timestamps, pnl_values):
        day_key = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
        daily_pnl[day_key].append(pnl)

    daily_pfs = []
    for trades in daily_pnl.values():
        gross_profit = sum(p for p in trades if p > 0)
        gross_loss = abs(sum(p for p in trades if p < 0))
        if gross_loss > 0:
            daily_pfs.append(gross_profit / gross_loss)
        elif gross_profit > 0:
            daily_pfs.append(5.0)  # Cap for all-win days

    return {
        "avg_daily_pf": float(np.mean(daily_pfs)) if daily_pfs else 0.0,
        "trading_days": len(daily_pfs),
        "pf_gt1_pct": float(np.mean([1 if pf > 1.0 else 0 for pf in daily_pfs])) if daily_pfs else 0.0,
        "daily_pfs": daily_pfs,
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
    return {"dsr": float(observed_sr - e_max_sr), "e_max_sr": float(e_max_sr), "p_value": float(p_value)}


def _ppf(p: float) -> float:
    """Inverse standard normal CDF."""
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
# Strategy 1: Pure Cross-Sectional Rank Long-Short
# ======================================================================

def _evaluate_xsectional_ls_fold(args: tuple) -> dict:
    """Cross-sectional long-short: rank by ranking_feature, long bottom quintile,
    short top quintile. Market-neutral."""

    fold_i, ranking_feature_train, ranking_feature_test, \
        target_train, target_test, test_timestamps, \
        quintile_size, strategy_name = args

    T_test, N = ranking_feature_test.shape
    pnl_all = []
    ts_all = []

    for t in range(T_test):
        rank_row = ranking_feature_test[t]
        ret_row = target_test[t]
        valid = np.isfinite(rank_row) & np.isfinite(ret_row)
        n_valid = valid.sum()
        if n_valid < 5:
            continue

        # Quintile cutoffs
        n_q = max(1, int(n_valid * quintile_size))

        # Sort by rank
        valid_idx = np.where(valid)[0]
        sorted_by_rank = valid_idx[np.argsort(rank_row[valid_idx])]

        # Long bottom quintile (lowest rank → lowest attention/lowest past return)
        long_idx = sorted_by_rank[:n_q]
        # Short top quintile (highest rank → highest attention/highest past return)
        short_idx = sorted_by_rank[-n_q:]

        # PnL: long returns + (-1 × short returns), divided by number of positions
        long_pnl = ret_row[long_idx].mean() if len(long_idx) > 0 else 0.0
        short_pnl = -ret_row[short_idx].mean() if len(short_idx) > 0 else 0.0
        portfolio_pnl = (long_pnl + short_pnl) / 2.0  # Dollar-neutral

        if np.isfinite(portfolio_pnl):
            pnl_all.append(float(portfolio_pnl))
            ts_all.append(test_timestamps[t])

    if not pnl_all:
        return {"fold": fold_i, "strategy": strategy_name, "error": "no valid trades"}

    pnl_arr = np.array(pnl_all)
    daily = _compute_daily_pf(ts_all, pnl_all)

    return {
        "fold": fold_i,
        "strategy": strategy_name,
        "test_n": len(pnl_all),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": sum(p for p in pnl_all if p > 0) / max(1e-10, abs(sum(p for p in pnl_all if p < 0))),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "total_pnl_bps": float(pnl_arr.sum() * 10000),
        "pnl_values": pnl_all,
    }


# ======================================================================
# Strategy 2: Non-Linear Model (Gradient-Boosted Stumps)
# ======================================================================

def _evaluate_gbstumps_fold(args: tuple) -> dict:
    """Evaluate a single walk-forward fold with GBStumps."""
    fold_i, X_train, y_train, X_test, y_test, test_timestamps, feature_names = args

    # Standardise using training stats
    mean = np.nanmean(X_train, axis=0)
    std = np.nanstd(X_train, axis=0)
    std[std == 0] = 1.0

    Xn_train = np.nan_to_num((X_train - mean) / std, nan=0.0, posinf=0.0, neginf=0.0)
    Xn_test = np.nan_to_num((X_test - mean) / std, nan=0.0, posinf=0.0, neginf=0.0)
    y_train_clean = np.nan_to_num(y_train, nan=0.0)

    # Fit GBStumps
    model = GBStumps(n_stumps=200, learning_rate=0.05, subsample=0.8, n_thresholds=20)
    model.fit(Xn_train, y_train_clean)
    y_pred = model.predict(Xn_test)

    # Directional trading
    direction = np.sign(y_pred)
    pnl = direction * y_test

    valid = np.isfinite(y_test)
    if valid.sum() == 0:
        return {"fold": fold_i, "strategy": "gbstumps", "error": "no valid test data"}

    pnl_valid = pnl[valid]
    ts_valid = [test_timestamps[i] for i in range(len(test_timestamps)) if valid[i]]

    accuracy = float(np.mean((y_test[valid] > 0).astype(float) == (y_pred[valid] > 0).astype(float)))
    daily = _compute_daily_pf(ts_valid, pnl_valid.tolist())
    feat_imp = model.feature_importance(feature_names)

    total_profit = sum(float(p) for p in pnl_valid if p > 0)
    total_loss = abs(sum(float(p) for p in pnl_valid if p < 0))

    return {
        "fold": fold_i,
        "strategy": "gbstumps",
        "train_n": len(y_train),
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


# ======================================================================
# Strategy 3: Regime-Conditional Cross-Sectional
# ======================================================================

def _evaluate_regime_conditional_fold(args: tuple) -> dict:
    """Split bars by volatility regime, then apply XS rank strategy within each."""
    fold_i, ranking_feat_test, vol_regime_test, target_test, \
        test_timestamps, quintile_size = args

    T_test, N = ranking_feat_test.shape
    pnl_all = []
    ts_all = []

    # Compute median vol regime cross-sectionally each bar
    for t in range(T_test):
        rank_row = ranking_feat_test[t]
        ret_row = target_test[t]
        vol_row = vol_regime_test[t]

        valid = np.isfinite(rank_row) & np.isfinite(ret_row) & np.isfinite(vol_row)
        n_valid = valid.sum()
        if n_valid < 6:
            continue

        # Determine regime: above-median vol = high vol
        vol_median = np.nanmedian(vol_row[valid])
        high_vol = valid & (vol_row >= vol_median)
        low_vol = valid & (vol_row < vol_median)

        # In high vol: contrarian (short high attention)
        # In low vol: momentum (long high attention)
        # This tests the hypothesis from iter1

        n_q = max(1, int(n_valid * quintile_size))
        valid_idx = np.where(valid)[0]
        sorted_by_rank = valid_idx[np.argsort(rank_row[valid_idx])]

        long_idx = sorted_by_rank[:n_q]
        short_idx = sorted_by_rank[-n_q:]

        # Regime-aware weighting
        # In high vol regime: increase short weight (contrarian)
        # In low vol regime: increase long weight (momentum)
        bar_vol_level = np.nanmean(vol_row[valid])
        if bar_vol_level > vol_median:
            # High vol: emphasise contrarian (short the hyped)
            long_wt, short_wt = 0.3, 0.7
        else:
            # Low vol: emphasise momentum (long the quiet)
            long_wt, short_wt = 0.7, 0.3

        long_pnl = ret_row[long_idx].mean() if len(long_idx) > 0 else 0.0
        short_pnl = -ret_row[short_idx].mean() if len(short_idx) > 0 else 0.0
        portfolio_pnl = long_wt * long_pnl + short_wt * short_pnl

        if np.isfinite(portfolio_pnl):
            pnl_all.append(float(portfolio_pnl))
            ts_all.append(test_timestamps[t])

    if not pnl_all:
        return {"fold": fold_i, "strategy": "regime_conditional", "error": "no valid trades"}

    pnl_arr = np.array(pnl_all)
    daily = _compute_daily_pf(ts_all, pnl_all)

    return {
        "fold": fold_i,
        "strategy": "regime_conditional",
        "test_n": len(pnl_all),
        "trading_days": daily["trading_days"],
        "avg_daily_pf": daily["avg_daily_pf"],
        "pf_gt1_pct": daily["pf_gt1_pct"],
        "aggregate_pf": sum(p for p in pnl_all if p > 0) / max(1e-10, abs(sum(p for p in pnl_all if p < 0))),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_all,
    }


# ======================================================================
# PHASE 3b: Walk-Forward Orchestration
# ======================================================================

def build_temporal_folds(T: int, n_folds: int, purge_bars: int,
                         min_train_bars: int, min_test_bars: int) -> list[tuple[range, range]]:
    """Build purged walk-forward fold indices at the BAR level (not flattened).

    Returns list of (train_range, test_range) where each is a range of timestamp indices.
    """
    usable = T - purge_bars
    step = max(min_test_bars, (usable - min_train_bars) // max(1, n_folds))

    folds = []
    train_end = min_train_bars
    while train_end + purge_bars + min_test_bars <= T and len(folds) < n_folds:
        test_start = train_end + purge_bars
        test_end = min(T, test_start + step)

        folds.append((range(0, train_end), range(test_start, test_end)))
        train_end = test_end

    return folds


def run_all_strategies(
    features: dict[str, np.ndarray],
    feature_names: list[str],
    ohlcv: dict,
    social_mat: np.ndarray,
    horizon: int,
    cost_mult: float,
    n_workers: int,
) -> dict:
    """Run all strategies for a single horizon × cost_mult configuration."""
    T, N = ohlcv["close"].shape
    timestamps = ohlcv["timestamps"]

    total_cost_bps = ROUND_TRIP_BPS * cost_mult + XSM_PENALTY_BPS
    target = compute_forward_returns(ohlcv["close"], horizon, total_cost_bps)

    # Build temporal folds
    folds = build_temporal_folds(T, N_FOLDS, PURGE_BARS, MIN_TRAIN // N, MIN_TEST // N)
    log(f"  Built {len(folds)} temporal folds for {T} bars × {N} symbols")

    if not folds:
        return {"error": "insufficient data for walk-forward folds"}

    results = {}

    # --- Strategy 1: Pure XS Rank L/S on each ranking feature ---
    ranking_features_to_test = [
        "cs_rank_mentions_6h",
        "cs_rank_mentions_24h",
        "cs_rank_engagement_24h",
        "cs_rank_ret_24h",
        "cs_rank_author_concentration",
        "contrarian_social_composite",
        "momentum_confirmation_composite",
        "attention_price_divergence",
        "cs_rank_social_price_div",
    ]

    for rank_feat_name in ranking_features_to_test:
        if rank_feat_name not in features:
            continue
        rank_feat = features[rank_feat_name]

        fold_args = []
        for fi, (train_range, test_range) in enumerate(folds):
            fold_args.append((
                fi + 1,
                rank_feat[train_range.start:train_range.stop],
                rank_feat[test_range.start:test_range.stop],
                target[train_range.start:train_range.stop],
                target[test_range.start:test_range.stop],
                [timestamps[t] for t in test_range],
                QUINTILE_SIZE,
                f"xs_ls_{rank_feat_name}",
            ))

        fold_results = [_evaluate_xsectional_ls_fold(a) for a in fold_args]
        results[f"xs_ls_{rank_feat_name}"] = _aggregate_strategy_results(fold_results, f"xs_ls_{rank_feat_name}")

    # --- Strategy 2: GBStumps non-linear model ---
    # Stack features into (T, N, F) array, then flatten for model
    feat_array = np.stack([features[name] for name in feature_names], axis=2)
    feat_array = np.clip(feat_array, -100, 100)
    F = len(feature_names)

    X_flat = feat_array.reshape(T * N, F)
    y_flat = target.reshape(T * N)
    ts_flat = []
    for t in range(T):
        for n in range(N):
            ts_flat.append(timestamps[t])

    X_flat = np.nan_to_num(X_flat, nan=0.0, posinf=0.0, neginf=0.0)

    gb_fold_args = []
    for fi, (train_range, test_range) in enumerate(folds):
        train_rows = []
        for t in train_range:
            for n in range(N):
                train_rows.append(t * N + n)
        test_rows = []
        for t in test_range:
            for n in range(N):
                test_rows.append(t * N + n)

        train_rows = np.array(train_rows)
        test_rows = np.array(test_rows)

        # Filter to valid targets
        train_valid = np.isfinite(y_flat[train_rows])
        test_valid = np.isfinite(y_flat[test_rows])

        gb_fold_args.append((
            fi + 1,
            X_flat[train_rows[train_valid]],
            y_flat[train_rows[train_valid]],
            X_flat[test_rows[test_valid]],
            y_flat[test_rows[test_valid]],
            [ts_flat[test_rows[j]] for j in range(len(test_rows)) if test_valid[j]],
            feature_names,
        ))

    # Run GBStumps folds (these are heavier, so parallelise)
    if n_workers > 1 and len(gb_fold_args) > 1:
        with Pool(min(n_workers, len(gb_fold_args))) as pool:
            gb_fold_results = pool.map(_evaluate_gbstumps_fold, gb_fold_args)
    else:
        gb_fold_results = [_evaluate_gbstumps_fold(a) for a in gb_fold_args]

    results["gbstumps"] = _aggregate_strategy_results(gb_fold_results, "gbstumps")

    # --- Strategy 3: Regime-conditional XS L/S ---
    # Use mentions_24h rank as the ranking feature, vol_regime as the regime
    rank_feat = features.get("cs_rank_mentions_24h", np.full((T, N), np.nan))
    vol_regime = features.get("vol_regime_ratio", np.full((T, N), np.nan))

    regime_fold_args = []
    for fi, (train_range, test_range) in enumerate(folds):
        regime_fold_args.append((
            fi + 1,
            rank_feat[test_range.start:test_range.stop],
            vol_regime[test_range.start:test_range.stop],
            target[test_range.start:test_range.stop],
            [timestamps[t] for t in test_range],
            QUINTILE_SIZE,
        ))

    regime_fold_results = [_evaluate_regime_conditional_fold(a) for a in regime_fold_args]
    results["regime_conditional_mentions"] = _aggregate_strategy_results(
        regime_fold_results, "regime_conditional_mentions"
    )

    # --- Strategy 4: Feature interaction screening ---
    # Test pairwise combinations of top features as composite ranking signals
    top_interaction_pairs = [
        ("cs_rank_mentions_24h", "cs_rank_ret_24h"),
        ("cs_rank_mentions_6h", "cs_rank_vol"),
        ("cs_rank_engagement_24h", "cs_rank_volume"),
        ("cs_rank_author_concentration", "cs_rank_ret_24h"),
        ("cs_rank_mentions_24h", "cs_rank_volume"),
        ("cs_z_mentions_6h", "cs_z_vol"),
    ]

    for feat_a_name, feat_b_name in top_interaction_pairs:
        if feat_a_name not in features or feat_b_name not in features:
            continue
        # Composite: average of the two XS ranks
        composite = (features[feat_a_name] + features[feat_b_name]) / 2.0
        strat_name = f"xs_interaction_{feat_a_name}_x_{feat_b_name}"

        fold_args = []
        for fi, (train_range, test_range) in enumerate(folds):
            fold_args.append((
                fi + 1,
                composite[train_range.start:train_range.stop],
                composite[test_range.start:test_range.stop],
                target[train_range.start:train_range.stop],
                target[test_range.start:test_range.stop],
                [timestamps[t] for t in test_range],
                QUINTILE_SIZE,
                strat_name,
            ))
        fold_results = [_evaluate_xsectional_ls_fold(a) for a in fold_args]
        results[strat_name] = _aggregate_strategy_results(fold_results, strat_name)

    return results


def _aggregate_strategy_results(fold_results: list[dict], strategy_name: str) -> dict:
    """Aggregate fold results into strategy-level metrics."""
    valid_folds = [f for f in fold_results if "error" not in f]
    if not valid_folds:
        return {"strategy": strategy_name, "error": "all folds failed", "folds": fold_results}

    all_pnl = []
    for f in valid_folds:
        all_pnl.extend(f.get("pnl_values", []))

    profitable_folds = sum(1 for f in valid_folds if f.get("avg_daily_pf", 0) > 1.0)

    if all_pnl:
        pnl_arr = np.array(all_pnl)
        oos_sharpe = float(pnl_arr.mean() / max(1e-10, pnl_arr.std()))
        total_profit = sum(p for p in all_pnl if p > 0)
        total_loss = abs(sum(p for p in all_pnl if p < 0))
    else:
        oos_sharpe = 0.0
        total_profit = 0.0
        total_loss = 0.0

    n_configs_tested = max(1, len(valid_folds))
    dsr = _compute_dsr(oos_sharpe, n_trials=n_configs_tested, n_obs=len(all_pnl))

    return {
        "strategy": strategy_name,
        "n_folds": len(valid_folds),
        "profitable_folds": profitable_folds,
        "wf_pass_rate": profitable_folds / max(1, len(valid_folds)),
        "avg_daily_pf": float(np.mean([f["avg_daily_pf"] for f in valid_folds])),
        "avg_pf_gt1_pct": float(np.mean([f.get("pf_gt1_pct", 0) for f in valid_folds])),
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "oos_sharpe": oos_sharpe,
        "oos_mean_pnl_bps": float(np.mean(all_pnl) * 10000) if all_pnl else 0.0,
        "oos_total_trades": len(all_pnl),
        "dsr": dsr["dsr"],
        "dsr_e_max_sr": dsr["e_max_sr"],
        "dsr_p_value": dsr["p_value"],
        "folds": [{k: v for k, v in f.items() if k != "pnl_values"} for f in fold_results],
        # Include top features if GBStumps
        "top_features": _merge_feature_importances(valid_folds),
    }


def _merge_feature_importances(folds: list[dict]) -> dict:
    """Merge feature importances across folds."""
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
# PHASE 4: Reporting
# ======================================================================

def format_strategy_report(results: dict, horizon: int, cost_mult: float) -> str:
    """Format all strategy results for a single config."""
    lines = []
    total_cost = ROUND_TRIP_BPS * cost_mult + XSM_PENALTY_BPS
    lines.append(f"\n{'='*80}")
    lines.append(f"HORIZON: {horizon}h | COST: {cost_mult}x + XSM penalty = {total_cost:.0f} bps round-trip")
    lines.append(f"{'='*80}")

    if "error" in results:
        lines.append(f"ERROR: {results['error']}")
        return "\n".join(lines)

    # Sort strategies by aggregate PF descending
    strat_results = []
    for key, val in results.items():
        if isinstance(val, dict) and "strategy" in val:
            strat_results.append((key, val))
    strat_results.sort(key=lambda x: x[1].get("aggregate_pf", 0), reverse=True)

    lines.append(f"\n{'Strategy':<55s} {'AvgDPF':>7s} {'AggPF':>7s} {'Sharpe':>7s} {'MeanBps':>8s} {'WF%':>5s} {'DSR':>7s} {'Status':>8s}")
    lines.append("-" * 106)

    for key, val in strat_results:
        if "error" in val:
            lines.append(f"{key:<55s} {'ERROR':>7s}")
            continue
        dpf = val.get("avg_daily_pf", 0)
        apf = val.get("aggregate_pf", 0)
        sharpe = val.get("oos_sharpe", 0)
        mean_bps = val.get("oos_mean_pnl_bps", 0)
        wf_rate = val.get("wf_pass_rate", 0)
        dsr = val.get("dsr", 0)

        # Status determination
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

    # Print detail for top 3 strategies
    lines.append(f"\n--- TOP 3 STRATEGY DETAILS ---")
    for key, val in strat_results[:3]:
        if "error" in val:
            continue
        lines.append(f"\n  [{key}]")
        lines.append(f"  Folds: {val.get('n_folds', 0)}, Profitable: {val.get('profitable_folds', 0)}")
        lines.append(f"  Total OOS trades: {val.get('oos_total_trades', 0)}")
        lines.append(f"  DSR: {val.get('dsr', 0):.3f}, E[max(SR)]: {val.get('dsr_e_max_sr', 0):.3f}")

        if val.get("top_features"):
            lines.append(f"  Top features:")
            for feat, score in list(val["top_features"].items())[:10]:
                lines.append(f"    {feat:40s}  {score:.6f}")

        for f in val.get("folds", []):
            if "error" in f:
                lines.append(f"    Fold {f.get('fold', '?')}: ERROR - {f['error']}")
            else:
                dpf = f.get("avg_daily_pf", 0)
                pf_status = "+" if dpf > 1.0 else "-"
                acc = f.get("accuracy", "N/A")
                acc_str = f"{acc:.3f}" if isinstance(acc, float) else str(acc)
                lines.append(
                    f"    Fold {f.get('fold', '?')}: test={f.get('test_n', 0):>5d}  "
                    f"dpf={dpf:.3f}[{pf_status}]  agg_pf={f.get('aggregate_pf', 0):.3f}  "
                    f"sharpe={f.get('sharpe', 0):.3f}  acc={acc_str}  days={f.get('trading_days', 0)}"
                )

    return "\n".join(lines)


# ======================================================================
# MAIN
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="Opus 4.6 Forward Predictor Discovery — Iteration 2")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--symbols", default=",".join(TOP_SYMBOLS))
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--output", default="/scripts/opus_discovery_iter2_results.json")
    parser.add_argument("--horizons", default=",".join(str(h) for h in HORIZONS))
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    db_url = args.database_url or DB_URL_DEFAULT
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(h.strip()) for h in args.horizons.split(",")]

    log("=" * 80)
    log("Opus 4.6 Forward Predictor Discovery — ITERATION 2")
    log("Cross-Sectional Rank Long-Short + Non-Linear Models")
    log("=" * 80)
    log(f"Symbols: {len(symbols)}, Lookback: {args.lookback_days}d, Workers: {args.workers}")
    log(f"Horizons: {horizons}")
    log(f"Quintile size: {QUINTILE_SIZE:.0%} (top/bottom)")
    log(f"Base cost: {ROUND_TRIP_BPS} bps + XSM penalty: {XSM_PENALTY_BPS} bps")

    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=args.lookback_days)

    conn = psycopg.connect(db_url, row_factory=dict_row)
    try:
        # Phase 1: Load data
        ohlcv = load_ohlcv_matrix(conn, symbols, start_ts, end_ts)
        social_mat, social_cols = load_social_matrix(conn, symbols, ohlcv["timestamps"])

        # Phase 2: Engineer features
        features, feature_names = engineer_features(ohlcv, social_mat, social_cols)
        log(f"Total features: {len(feature_names)}")

        # Phase 3: Run all strategies for each horizon × cost multiplier
        all_results = {}
        grand_summary = []

        for horizon in horizons:
            log(f"\n{'#'*80}")
            log(f"# HORIZON: {horizon}h")
            log(f"{'#'*80}")

            for cost_mult in COST_MULTIPLIERS:
                config_key = f"h{horizon}_cost{cost_mult}x"
                log(f"\n  Running strategies for {config_key}...")

                config_results = run_all_strategies(
                    features, feature_names, ohlcv, social_mat,
                    horizon, cost_mult, args.workers,
                )
                all_results[config_key] = config_results

                # Print report
                report = format_strategy_report(config_results, horizon, cost_mult)
                print(report)

                # Collect best strategy for this config
                best_strat = None
                best_apf = 0.0
                for key, val in config_results.items():
                    if isinstance(val, dict) and val.get("aggregate_pf", 0) > best_apf:
                        best_apf = val["aggregate_pf"]
                        best_strat = key
                if best_strat:
                    val = config_results[best_strat]
                    grand_summary.append({
                        "config": config_key,
                        "best_strategy": best_strat,
                        "avg_daily_pf": val.get("avg_daily_pf", 0),
                        "aggregate_pf": val.get("aggregate_pf", 0),
                        "oos_sharpe": val.get("oos_sharpe", 0),
                        "dsr": val.get("dsr", 0),
                        "wf_pass_rate": val.get("wf_pass_rate", 0),
                    })

        # Phase 4: Grand Summary
        log("\n" + "=" * 80)
        log("GRAND SUMMARY — ALL CONFIGURATIONS")
        log("=" * 80)
        log(f"\n{'Config':<20s} {'Best Strategy':<45s} {'AvgDPF':>7s} {'AggPF':>7s} {'Sharpe':>7s} {'DSR':>7s} {'WF%':>5s}")
        log("-" * 98)
        for s in grand_summary:
            dpf = s["avg_daily_pf"]
            apf = s["aggregate_pf"]
            sharpe = s["oos_sharpe"]
            dsr = s["dsr"]
            wf = s["wf_pass_rate"]

            if dpf > 1.0 and apf > 1.0 and dsr > 0 and wf >= 0.8:
                status = " *** TRADEABLE ***"
            elif dpf > 1.0 and apf > 1.0:
                status = " << EDGE CANDIDATE >>"
            else:
                status = ""

            log(f"  {s['config']:<18s} {s['best_strategy']:<45s} {dpf:>7.3f} {apf:>7.3f} {sharpe:>7.3f} {dsr:>7.3f} {wf:>4.0%}{status}")

        # Save results
        serialisable = {}
        for config_key, config_results in all_results.items():
            config_ser = {}
            for strat_key, strat_val in config_results.items():
                if not isinstance(strat_val, dict):
                    continue
                # Remove pnl_values from folds for serialisation
                sv = {k: v for k, v in strat_val.items()}
                if "folds" in sv:
                    sv["folds"] = [{k: v for k, v in f.items() if k != "pnl_values"} for f in sv["folds"]]
                config_ser[strat_key] = sv
            serialisable[config_key] = config_ser

        serialisable["_grand_summary"] = grand_summary
        serialisable["_meta"] = {
            "script": "opus_discovery_iter2_xsectional.py",
            "date": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "horizons": horizons,
            "cost_multipliers": COST_MULTIPLIERS,
            "quintile_size": QUINTILE_SIZE,
            "n_folds": N_FOLDS,
            "purge_bars": PURGE_BARS,
            "round_trip_bps": ROUND_TRIP_BPS,
            "xsm_penalty_bps": XSM_PENALTY_BPS,
            "n_features": len(feature_names),
            "feature_names": feature_names,
        }

        output_path = args.output
        try:
            with open(output_path, "w") as fp:
                json.dump(serialisable, fp, indent=2, default=str)
            log(f"\nResults saved to: {output_path}")
        except Exception as e:
            log(f"Warning: Could not save to {output_path}: {e}")
            alt_path = "/tmp/opus_discovery_iter2_results.json"
            try:
                with open(alt_path, "w") as fp:
                    json.dump(serialisable, fp, indent=2, default=str)
                log(f"Results saved to: {alt_path}")
            except Exception:
                pass

        log("\nIteration 2 complete.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
