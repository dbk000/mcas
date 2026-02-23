#!/usr/bin/env python3
"""
Opus 4.6 Forward Predictor Discovery — Iteration 4: Equities Omni-Strategy
===========================================================================
Adaptation of the crypto omni-strategy for US equities (daily bars).
62 symbols (50 large-cap stocks + 12 ETFs), 4 horizons, 3 cost levels,
3-tier DSR reporting. SPY as market proxy (replaces BTC).

Key differences from crypto iter4:
1. Daily bars (1d) — 500 bars covering Feb 2024 - Feb 2026
2. SPY-neutral instead of BTC-neutral
3. Much lower costs (~3 bps RT vs 46 bps for crypto)
4. News events from equities_events (not social_features_1h)
5. Weekly PF (not daily PF) — daily bars = 1 obs/day is degenerate for PF
6. Horizons in trading days: 1d, 5d, 10d, 20d
7. Overnight gap feature (unique to equities)
8. Fewer DSR cumulative trials (fresh asset class)

Strategies:
 1a. Reversion + aggregate news regime filter
 1b. Reversion + per-symbol news gate
 2.  Cross-sectional momentum LONG (60d formation)
 3.  Time-series momentum (TSMOM) — vol-scaled
 4.  SPY-neutral residual reversion
 5.  Volume-price divergence (OBV)
 6.  Volatility regime router (meta-strategy)
 7.  News catalyst event strategy
 8.  News silence-then-spike detector
 9.  Portfolio combination of 5 most independent strategies

Compute profile:
 Phase 1: <2s (DB fetch 62 symbols × 500 daily bars + 69K events)
 Phase 2: <1s (feature engineering on 500×62 matrices)
 Phase 3: 540 jobs in Pool(12), <30s
 Phase 4: portfolio combination + reporting, <1s
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

MARKET_PROXY = "SPY"

EQUITY_SYMBOLS = [
    "SPY",  # Index 0 — market proxy (like BTC in crypto version)
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "BRK-B", "JPM", "V", "MA", "BAC", "GS", "WFC", "C",
    "UNH", "JNJ", "PFE", "MRK", "ABBV", "TMO",
    "XOM", "CVX", "COP", "SLB",
    "PG", "KO", "PEP", "WMT", "COST",
    "MCD", "HD", "LOW", "NKE",
    "CRM", "ORCL", "ADBE", "AMD", "QCOM", "AVGO", "TXN", "INTC", "IBM",
    "CAT", "GE", "HON", "RTX", "LMT", "NEE", "DUK",
    "QQQ", "XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU", "XLB", "XLRE",
]

HORIZONS = [1, 5, 10, 20]  # Trading days
PURGE_BARS = 5              # 1 trading week embargo
MIN_TRAIN_BARS = 100        # ~5 months
MIN_TEST_BARS = 20          # ~1 month
N_FOLDS = 5
TIMEFRAME = "1d"

# Equities cost model — MUCH cheaper than crypto
# Commission: $0 (modern brokers), Slippage: ~1.5 bps large-cap
COST_DEFAULT_RT_BPS = 3.0       # 2 legs × 1.5 bps
COST_TSMOM_RT_BPS = 3.0         # Long-only: 1.5 entry + 1.5 exit
COST_SPY_NEUTRAL_RT_BPS = 6.0   # 4 legs × 1.5 bps
COST_NEWS_CAT_RT_BPS = 3.0      # Long-only

QUINTILE_SIZE = 0.2
DECILE_SIZE = 0.1
COST_MULTIPLIERS = [1.0, 1.5, 2.0]

# DSR — fresh asset class, lower trial burden
CUMULATIVE_TRIALS = 50
NEW_TRIALS_ONLY = 36


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


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
    xy = _rolling_mean(x * y, w)
    mx = _rolling_mean(x, w)
    my = _rolling_mean(y, w)
    x2 = _rolling_mean(x ** 2, w)
    var_x = x2 - mx ** 2
    var_x[var_x < 1e-20] = np.nan
    return (xy - mx * my) / var_x


def _rolling_linear_slope(x: np.ndarray, w: int) -> np.ndarray:
    T, N = x.shape
    t_vec = np.arange(w, dtype=float)
    t_mean = t_vec.mean()
    t_var = np.sum((t_vec - t_mean) ** 2)
    if t_var == 0:
        return np.full_like(x, np.nan)
    out = np.full_like(x, np.nan)
    for i in range(w - 1, T):
        window = x[i - w + 1:i + 1]
        valid = np.isfinite(window).all(axis=0)
        if valid.any():
            y_mean = np.nanmean(window, axis=0)
            cov = np.sum((t_vec[:, None] - t_mean) * (window - y_mean[None, :]), axis=0)
            out[i, valid] = cov[valid] / t_var
    return out


# ======================================================================
# Data Loading
# ======================================================================

def load_equities_ohlcv_matrix(conn, symbols: list[str], start_ts: datetime, end_ts: datetime):
    from psycopg.rows import dict_row
    log(f"Loading equities OHLCV for {len(symbols)} symbols (1d)...")
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT symbol, bar_start_ts, open, high, low, close, volume
        FROM (
            SELECT symbol, bar_start_ts, open, high, low, close, volume,
                   ROW_NUMBER() OVER (PARTITION BY symbol, bar_start_ts ORDER BY as_of_ts DESC) AS rn
            FROM ohlcv
            WHERE symbol = ANY(%s)
              AND timeframe = '1d'
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

    symbols_with_data = []
    for i, s in enumerate(symbols):
        fill = np.isfinite(mats["close"][:, i]).mean()
        if fill > 0.5:
            symbols_with_data.append(s)
    log(f"  OHLCV matrix: ({T}, {N}), fill rate: {np.isfinite(mats['close']).mean():.2%}")
    log(f"  Symbols with >50% fill: {len(symbols_with_data)}/{N}")
    return {"timestamps": all_ts, "symbols": symbols, **mats}


def load_equities_news_matrix(conn, symbols: list[str], timestamps: list[datetime]):
    """Aggregate equities_events into daily (T, N, 4) matrix.
    Channels: event_count, unique_publishers, avg_sentiment, publisher_novelty.
    Look-ahead safe: events binned to the first bar AFTER they become available."""
    from psycopg.rows import dict_row
    log(f"Loading news/events for {len(symbols)} symbols...")
    T, N = len(timestamps), len(symbols)
    sym_idx = {s: i for i, s in enumerate(symbols)}

    ts_epochs = np.array([t.timestamp() for t in timestamps])

    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT symbol, available_at_ts, sentiment_score, publisher
        FROM equities_events
        WHERE symbol = ANY(%s)
          AND available_at_ts >= %s AND available_at_ts < %s
        ORDER BY available_at_ts;
    """, (symbols, timestamps[0] - timedelta(days=7), timestamps[-1] + timedelta(days=1)))
    rows = cur.fetchall()
    cur.close()
    log(f"  Loaded {len(rows)} event rows")

    news_count = np.zeros((T, N))
    news_sentiment_sum = np.zeros((T, N))
    news_sentiment_cnt = np.zeros((T, N))
    # Track unique publishers per (t, n) — use dict of sets
    pub_sets: dict[tuple[int, int], set] = {}

    for r in rows:
        n = sym_idx.get(r["symbol"])
        if n is None:
            continue
        event_epoch = r["available_at_ts"].timestamp()
        t = int(np.searchsorted(ts_epochs, event_epoch, side='right'))
        if t >= T:
            continue
        if t < 0:
            t = 0

        news_count[t, n] += 1
        if r.get("sentiment_score") is not None:
            news_sentiment_sum[t, n] += float(r["sentiment_score"])
            news_sentiment_cnt[t, n] += 1
        if r.get("publisher"):
            key = (t, n)
            if key not in pub_sets:
                pub_sets[key] = set()
            pub_sets[key].add(r["publisher"])

    unique_pubs = np.zeros((T, N))
    for (t, n), pset in pub_sets.items():
        unique_pubs[t, n] = len(pset)

    news_mat = np.zeros((T, N, 4))
    news_mat[:, :, 0] = news_count
    news_mat[:, :, 1] = unique_pubs
    with np.errstate(divide='ignore', invalid='ignore'):
        news_mat[:, :, 2] = np.where(news_sentiment_cnt > 0,
                                      news_sentiment_sum / news_sentiment_cnt, 0.0)
        news_mat[:, :, 3] = np.where(news_count > 0,
                                      unique_pubs / news_count, 0.0)

    coverage = np.sum(news_count > 0) / max(1, T * N)
    symbols_with_news = int(np.sum(np.any(news_count > 0, axis=0)))
    log(f"  News matrix: ({T}, {N}, 4), coverage: {coverage:.2%}")
    log(f"  Symbols with any news: {symbols_with_news}/{N}")
    return news_mat, ["event_count", "unique_publishers", "avg_sentiment", "publisher_novelty"]


# ======================================================================
# Feature Engineering (adapted for daily bars + SPY + news)
# ======================================================================

def engineer_equities_features(ohlcv: dict, news_mat: np.ndarray) -> tuple[dict, list[str]]:
    log("Engineering equities features...")
    t0 = time.time()
    O, H, L, C, V = ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"]
    T, N = C.shape

    features = {}

    # Returns (1d, 5d, 20d, 60d — maps to crypto's 1h, 6h, 24h, 72h)
    ret_1 = C / _lag(C, 1) - 1.0
    ret_5 = C / _lag(C, 5) - 1.0
    ret_20 = C / _lag(C, 20) - 1.0
    ret_60 = C / _lag(C, 60) - 1.0
    features["ret_1d"] = ret_1
    features["ret_5d"] = ret_5
    features["ret_20d"] = ret_20
    features["ret_60d"] = ret_60

    # Overnight gap (unique to equities — no equivalent in 24/7 crypto)
    overnight_gap = O / _lag(C, 1) - 1.0
    features["overnight_gap"] = overnight_gap

    # Intrabar microstructure
    bar_range = np.where(H - L > 0, H - L, np.nan)
    features["body_position"] = (C - L) / bar_range
    features["upper_wick_ratio"] = (H - np.maximum(O, C)) / bar_range
    features["lower_wick_ratio"] = (np.minimum(O, C) - L) / bar_range
    features["body_size_ratio"] = np.abs(C - O) / bar_range

    # Volatility (20d and 60d — maps to 24h and 72h)
    realized_vol_20 = _rolling_std(ret_1, 20)
    realized_vol_60 = _rolling_std(ret_1, 60)
    vol_regime = realized_vol_20 / np.where(realized_vol_60 > 0, realized_vol_60, np.nan)
    vol_change_5 = realized_vol_20 - _lag(realized_vol_20, 5)
    features["realized_vol_20"] = realized_vol_20
    features["vol_regime_ratio"] = vol_regime
    features["vol_acceleration"] = vol_change_5 - _lag(vol_change_5, 5)

    # Volume (5d and 20d — maps to 24h and 72h)
    vol_ma_5 = _rolling_mean(V, 5)
    vol_ma_20 = _rolling_mean(V, 20)
    relative_volume = V / np.where(vol_ma_5 > 0, vol_ma_5, np.nan)
    features["relative_volume_5"] = relative_volume
    features["volume_regime"] = vol_ma_5 / np.where(vol_ma_20 > 0, vol_ma_20, np.nan)

    # Cross-symbol: SPY (index 0) replaces BTC
    spy_ret_1 = ret_1[:, 0:1]
    spy_ret_5 = ret_5[:, 0:1]
    spy_ret_20 = ret_20[:, 0:1]
    features["idiosyncratic_ret_1d"] = ret_1 - spy_ret_1
    features["idiosyncratic_ret_5d"] = ret_5 - spy_ret_5
    features["idiosyncratic_ret_20d"] = ret_20 - spy_ret_20

    # SPY beta and residuals (Strategy 4)
    rolling_spy_beta = _rolling_ols_beta(ret_1, spy_ret_1 * np.ones((1, N)), 60)
    spy_residual = ret_1 - rolling_spy_beta * spy_ret_1
    spy_res_mean = _rolling_mean(spy_residual, 60)
    spy_res_std = _rolling_std(spy_residual, 60)
    spy_residual_zscore = (spy_residual - spy_res_mean) / np.where(spy_res_std > 0, spy_res_std, np.nan)
    features["rolling_spy_beta_60"] = rolling_spy_beta
    features["spy_residual"] = spy_residual
    features["spy_residual_zscore"] = spy_residual_zscore

    # News features (replaces crypto social features)
    event_count = news_mat[:, :, 0]
    unique_pubs = news_mat[:, :, 1]
    avg_sentiment = news_mat[:, :, 2]
    pub_novelty = news_mat[:, :, 3]

    events_5d = _rolling_mean(event_count, 5)
    events_20d = _rolling_mean(event_count, 20)
    pubs_20d = _rolling_mean(unique_pubs, 20)
    sentiment_5d = _rolling_mean(avg_sentiment, 5)
    sentiment_20d = _rolling_mean(avg_sentiment, 20)

    features["events_5d_avg"] = events_5d
    features["events_20d_avg"] = events_20d
    features["sentiment_5d_avg"] = sentiment_5d
    features["sentiment_20d_avg"] = sentiment_20d
    features["publisher_novelty_20d_avg"] = _rolling_mean(pub_novelty, 20)

    pub_concentration = np.where(pubs_20d > 0, events_20d / pubs_20d, np.nan)
    features["publisher_concentration_20d"] = pub_concentration

    # News-price coupling
    e_std = _rolling_std(events_5d, 60)
    events_z = (events_5d - _rolling_mean(events_5d, 60)) / np.where(e_std > 0, e_std, np.nan)
    r_std = _rolling_std(ret_5, 60)
    ret_z = (ret_5 - _rolling_mean(ret_5, 60)) / np.where(r_std > 0, r_std, np.nan)
    features["news_price_divergence"] = events_z - np.abs(ret_z)
    features["news_price_coupling_60w"] = _rolling_corr(event_count, np.abs(ret_1), 60)
    features["sentiment_acceleration"] = avg_sentiment - _lag(avg_sentiment, 5)
    features["vol_attention_coupling"] = realized_vol_20 * events_z

    # Event velocity z-score
    event_vel = event_count - _lag(event_count, 1)
    ev_std = _rolling_std(event_vel, 60)
    event_vel_z = (event_vel - _rolling_mean(event_vel, 60)) / np.where(ev_std > 0, ev_std, np.nan)
    features["event_vel_z_60w"] = event_vel_z

    # Cross-sectional ranks
    features["cs_rank_events_5d"] = _cross_sectional_rank_vectorized(events_5d)
    features["cs_rank_events_20d"] = _cross_sectional_rank_vectorized(events_20d)
    features["cs_rank_sentiment_20d"] = _cross_sectional_rank_vectorized(sentiment_20d)
    features["cs_rank_ret_20d"] = _cross_sectional_rank_vectorized(ret_20)
    features["cs_rank_ret_60d"] = _cross_sectional_rank_vectorized(ret_60)
    features["cs_rank_volume"] = _cross_sectional_rank_vectorized(relative_volume)
    features["cs_rank_vol"] = _cross_sectional_rank_vectorized(realized_vol_20)
    features["cs_rank_pub_concentration"] = _cross_sectional_rank_vectorized(pub_concentration)
    features["cs_rank_news_price_div"] = _cross_sectional_rank_vectorized(events_z - np.abs(ret_z))
    features["cs_rank_overnight_gap"] = _cross_sectional_rank_vectorized(overnight_gap)

    # Cross-sectional z-scores
    features["cs_z_events_5d"] = _cross_sectional_zscore_vectorized(events_5d)
    features["cs_z_ret_20d"] = _cross_sectional_zscore_vectorized(ret_20)
    features["cs_z_vol"] = _cross_sectional_zscore_vectorized(realized_vol_20)

    # Composites
    features["contrarian_news_composite"] = (
        _cross_sectional_rank_vectorized(events_20d) * 0.4
        + _cross_sectional_rank_vectorized(pub_concentration) * 0.3
        + (1.0 - _cross_sectional_rank_vectorized(ret_20)) * 0.3
    )
    features["momentum_confirmation_composite"] = (
        _cross_sectional_rank_vectorized(ret_20) * 0.5
        + _cross_sectional_rank_vectorized(events_20d) * 0.3
        + _cross_sectional_rank_vectorized(sentiment_20d) * 0.2
    )
    event_change = events_5d - _lag(events_5d, 20)
    ret_change = np.abs(ret_20) - np.abs(_lag(ret_20, 20))
    features["attention_price_divergence"] = (
        _cross_sectional_rank_vectorized(event_change)
        - _cross_sectional_rank_vectorized(ret_change)
    )

    # OBV and OBV slope (Strategy 5)
    sign_ret = np.sign(ret_1)
    signed_vol = sign_ret * V
    obv = np.nancumsum(np.nan_to_num(signed_vol, nan=0.0), axis=0)
    features["obv"] = obv
    obv_slope = _rolling_linear_slope(obv, 5)
    features["obv_slope_5"] = obv_slope

    # Aggregate news velocity (market-level) for Strategies 1a, 7
    agg_event_vel = np.nanmean(event_vel, axis=1, keepdims=True)  # (T, 1)
    agg_ev_broadcast = np.broadcast_to(agg_event_vel, (T, N)).copy()
    agg_ev_mean = _rolling_mean(agg_ev_broadcast[:, 0:1], 60)
    agg_ev_std = _rolling_std(agg_ev_broadcast[:, 0:1], 60)
    agg_event_vel_z = (agg_event_vel - agg_ev_mean) / np.where(agg_ev_std > 0, agg_ev_std, np.nan)
    agg_event_vel_z_broadcast = np.broadcast_to(agg_event_vel_z, (T, N)).copy()
    features["aggregate_news_velocity"] = agg_ev_broadcast
    features["aggregate_news_velocity_zscore"] = agg_event_vel_z_broadcast

    # Silence days + spike trigger for Strategy 8
    silence_mask = (event_vel_z < -1.0) & (relative_volume < 0.5)
    silence_mask = np.nan_to_num(silence_mask, nan=0).astype(float)
    silence_cumsum = np.zeros_like(silence_mask)
    for t in range(1, T):
        silence_cumsum[t] = np.where(silence_mask[t] > 0, silence_cumsum[t - 1] + 1, 0)
    silence_days = silence_cumsum  # Each bar IS a day
    features["silence_days"] = silence_days

    # Spike trigger: silence >= 3 days AND ret_5d > 5% AND relative_volume > 2
    spike_trigger = (
        (silence_days >= 3.0) | (_lag(silence_days, 1) >= 3.0)
    ) & (ret_5 > 0.05) & (relative_volume > 2.0)
    spike_trigger = np.where(
        np.isfinite(ret_5) & np.isfinite(relative_volume),
        spike_trigger.astype(float), np.nan
    )
    features["spike_trigger"] = spike_trigger

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
# Weekly PF (adapted from daily PF — daily bars have 1 obs/day)
# ======================================================================

def _compute_weekly_pf_vectorized(week_indices: np.ndarray, pnl: np.ndarray) -> dict:
    """Compute average PF over calendar weeks. Equities equivalent of crypto's daily PF."""
    unique_weeks = np.unique(week_indices)
    weekly_pfs = []
    for w in unique_weeks:
        mask = week_indices == w
        w_pnl = pnl[mask]
        profit = float(np.sum(w_pnl[w_pnl > 0]))
        loss = float(np.abs(np.sum(w_pnl[w_pnl < 0])))
        if loss > 0:
            weekly_pfs.append(profit / loss)
        elif profit > 0:
            weekly_pfs.append(5.0)
    return {
        "avg_weekly_pf": float(np.mean(weekly_pfs)) if weekly_pfs else 0.0,
        "trading_weeks": len(weekly_pfs),
        "pf_gt1_pct": float(np.mean([1 if pf > 1 else 0 for pf in weekly_pfs])) if weekly_pfs else 0.0,
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
    e_max_cum = _compute_e_max_sr(CUMULATIVE_TRIALS)
    e_max_new = _compute_e_max_sr(NEW_TRIALS_ONLY)

    dsr_strict = observed_sr - e_max_cum
    dsr_r5 = observed_sr - (e_max_cum - 0.50)
    dsr_r10 = observed_sr - (e_max_cum - 1.00)

    dsr_bonf_strict = observed_sr - e_max_new
    dsr_bonf_r5 = observed_sr - (e_max_new - 0.50)
    dsr_bonf_r10 = observed_sr - (e_max_new - 1.00)

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
# GBStumps
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

def _eval_reversion_fold(args: tuple) -> dict:
    """Strategy 1a/1b: L/S reversion on 20d returns + news filter."""
    (fold_i, variant, cs_rank_ret_test, gross_ret_test, week_idx_test,
     breadth_test, agg_news_vel_z_test, events_20d_test,
     cost_rt_bps, cost_mult) = args

    T_test, N = cs_rank_ret_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    low_thresh = QUINTILE_SIZE
    high_thresh = 1.0 - QUINTILE_SIZE

    valid = np.isfinite(cs_rank_ret_test) & np.isfinite(gross_ret_test)
    valid_count = valid.sum(axis=1)
    min_valid = max(5, int(N * 0.3))
    tradeable = valid_count >= min_valid

    if variant == "1a":
        agg_z_col = agg_news_vel_z_test[:, 0] if agg_news_vel_z_test.ndim > 1 else agg_news_vel_z_test
        agg_valid = np.isfinite(agg_z_col)
        if agg_valid.sum() > 10:
            q75 = np.nanpercentile(agg_z_col[agg_valid], 75)
            social_pass = agg_z_col <= q75
        else:
            social_pass = np.ones(T_test, dtype=bool)
        tradeable = tradeable & social_pass

    if tradeable.sum() == 0:
        return {"fold": fold_i, "strategy": f"reversion_{variant}", "error": "no tradeable bars"}

    long_mask = tradeable[:, np.newaxis] & valid & (cs_rank_ret_test <= low_thresh)
    short_mask = tradeable[:, np.newaxis] & valid & (cs_rank_ret_test >= high_thresh)

    if variant == "1b":
        events_median = np.nanmedian(events_20d_test, axis=1, keepdims=True)
        below_median = events_20d_test <= events_median
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
    wk_arr = week_idx_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": f"reversion_{variant}", "error": "no PnL"}

    weekly = _compute_weekly_pf_vectorized(wk_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": f"reversion_{variant}",
        "test_n": int(len(pnl_arr)),
        "trading_weeks": weekly["trading_weeks"],
        "avg_weekly_pf": weekly["avg_weekly_pf"],
        "pf_gt1_pct": weekly["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_xs_momentum_long_fold(args: tuple) -> dict:
    """Strategy 2: XS momentum LONG — top decile by 60d return."""
    (fold_i, cs_rank_ret60_test, gross_ret_test, week_idx_test,
     cost_rt_bps, cost_mult) = args

    T_test, N = cs_rank_ret60_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = np.isfinite(cs_rank_ret60_test) & np.isfinite(gross_ret_test)
    valid_count = valid.sum(axis=1)
    min_valid = max(5, int(N * 0.3))
    tradeable = valid_count >= min_valid

    if tradeable.sum() == 0:
        return {"fold": fold_i, "strategy": "xs_momentum_long", "error": "no tradeable bars"}

    high_thresh = 1.0 - DECILE_SIZE
    long_mask = tradeable[:, np.newaxis] & valid & (cs_rank_ret60_test >= high_thresh)
    n_long = long_mask.sum(axis=1).astype(float)

    long_ret = np.where(n_long > 0,
                        np.where(long_mask, gross_ret_test, 0.0).sum(axis=1) / np.maximum(n_long, 1), 0.0)

    portfolio_pnl = long_ret - cost_frac
    pnl_arr = portfolio_pnl[tradeable]
    wk_arr = week_idx_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "xs_momentum_long", "error": "no PnL"}

    weekly = _compute_weekly_pf_vectorized(wk_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "xs_momentum_long",
        "test_n": int(len(pnl_arr)),
        "trading_weeks": weekly["trading_weeks"],
        "avg_weekly_pf": weekly["avg_weekly_pf"],
        "pf_gt1_pct": weekly["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_tsmom_fold(args: tuple) -> dict:
    """Strategy 3: Time-series momentum. Long if 60d ret > 0, vol-scaled."""
    (fold_i, ret_60_test, realized_vol_test, gross_ret_test, week_idx_test,
     cost_rt_bps, cost_mult) = args

    T_test, N = ret_60_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = np.isfinite(ret_60_test) & np.isfinite(gross_ret_test) & np.isfinite(realized_vol_test)
    signal = np.where(valid & (ret_60_test > 0), 1.0, 0.0)

    target_vol = 0.01  # 1% daily vol target (equities)
    vol_safe = np.where(realized_vol_test > 0.001, realized_vol_test, 0.001)
    position_size = np.minimum(target_vol / vol_safe, 3.0)

    weighted_ret = signal * position_size * gross_ret_test
    n_positions = (signal > 0).sum(axis=1).astype(float)
    n_positions[n_positions == 0] = np.nan

    portfolio_ret = np.nansum(weighted_ret, axis=1) / np.where(n_positions > 0, n_positions, 1.0)
    has_positions = n_positions > 0
    portfolio_pnl = np.where(has_positions, portfolio_ret - cost_frac, 0.0)

    tradeable = has_positions & np.isfinite(portfolio_pnl)
    pnl_arr = portfolio_pnl[tradeable]
    wk_arr = week_idx_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "tsmom", "error": "no PnL"}

    weekly = _compute_weekly_pf_vectorized(wk_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "tsmom",
        "test_n": int(len(pnl_arr)),
        "trading_weeks": weekly["trading_weeks"],
        "avg_weekly_pf": weekly["avg_weekly_pf"],
        "pf_gt1_pct": weekly["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_spy_neutral_fold(args: tuple) -> dict:
    """Strategy 4: SPY-neutral residual reversion. Trade when spy_residual_zscore > 1.5 sigma."""
    (fold_i, spy_res_z_test, gross_ret_test, spy_gross_ret_test, spy_beta_test,
     week_idx_test, cost_rt_bps, cost_mult) = args

    T_test, N = spy_res_z_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = (np.isfinite(spy_res_z_test) & np.isfinite(gross_ret_test)
             & np.isfinite(spy_beta_test))

    signal = np.zeros((T_test, N))
    signal[valid & (spy_res_z_test > 1.5)] = -1.0   # Short overextended
    signal[valid & (spy_res_z_test < -1.5)] = 1.0    # Long underextended

    spy_ret_col = spy_gross_ret_test[:, 0:1] if spy_gross_ret_test.ndim > 1 else spy_gross_ret_test[:, np.newaxis]
    hedge_ret = spy_beta_test * spy_ret_col
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
    wk_arr = week_idx_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "spy_neutral", "error": "no PnL"}

    weekly = _compute_weekly_pf_vectorized(wk_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "spy_neutral",
        "test_n": int(len(pnl_arr)),
        "trading_weeks": weekly["trading_weeks"],
        "avg_weekly_pf": weekly["avg_weekly_pf"],
        "pf_gt1_pct": weekly["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_vol_price_div_fold(args: tuple) -> dict:
    """Strategy 5: Volume-price divergence (OBV).
    Short: price at 5d high + OBV slope negative.
    Long: price at 5d low + OBV slope positive."""
    (fold_i, close_test, high_5_test, low_5_test, obv_slope_test,
     gross_ret_test, week_idx_test, cost_rt_bps, cost_mult) = args

    T_test, N = close_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = (np.isfinite(close_test) & np.isfinite(obv_slope_test)
             & np.isfinite(gross_ret_test) & np.isfinite(high_5_test)
             & np.isfinite(low_5_test))

    near_high = valid & (close_test >= high_5_test * 0.99)
    near_low = valid & (close_test <= low_5_test * 1.01)

    signal = np.zeros((T_test, N))
    signal[near_high & (obv_slope_test < 0)] = -1.0
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
    wk_arr = week_idx_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "vol_price_divergence", "error": "no PnL"}

    weekly = _compute_weekly_pf_vectorized(wk_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "vol_price_divergence",
        "test_n": int(len(pnl_arr)),
        "trading_weeks": weekly["trading_weeks"],
        "avg_weekly_pf": weekly["avg_weekly_pf"],
        "pf_gt1_pct": weekly["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_vol_regime_router_fold(args: tuple) -> dict:
    """Strategy 6: Vol regime router. LOW SPY vol: momentum. HIGH SPY vol: reversion."""
    (fold_i, cs_rank_ret60_test, cs_rank_ret20_test, spy_vol_test,
     gross_ret_test, week_idx_test, cost_rt_bps, cost_mult) = args

    T_test, N = cs_rank_ret60_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = (np.isfinite(cs_rank_ret60_test) & np.isfinite(cs_rank_ret20_test)
             & np.isfinite(gross_ret_test))

    spy_vol_col = spy_vol_test[:, 0] if spy_vol_test.ndim > 1 else spy_vol_test
    spy_vol_valid = np.isfinite(spy_vol_col)

    if spy_vol_valid.sum() < 10:
        return {"fold": fold_i, "strategy": "vol_regime_router", "error": "insufficient SPY vol data"}

    q33 = np.nanpercentile(spy_vol_col[spy_vol_valid], 33)
    q67 = np.nanpercentile(spy_vol_col[spy_vol_valid], 67)
    high_vol = spy_vol_col > q67
    low_vol = spy_vol_col < q33

    valid_count = valid.sum(axis=1)
    min_valid = max(5, int(N * 0.3))
    tradeable = valid_count >= min_valid

    portfolio_pnl = np.zeros(T_test)

    for t in range(T_test):
        if not tradeable[t] or not np.isfinite(spy_vol_col[t]):
            continue
        if low_vol[t]:
            long_mask = valid[t] & (cs_rank_ret60_test[t] >= 1.0 - DECILE_SIZE)
            n = long_mask.sum()
            if n > 0:
                portfolio_pnl[t] = gross_ret_test[t, long_mask].mean() - cost_frac
            else:
                tradeable[t] = False
        elif high_vol[t]:
            v = valid[t]
            l_mask = v & (cs_rank_ret20_test[t] <= QUINTILE_SIZE)
            s_mask = v & (cs_rank_ret20_test[t] >= 1.0 - QUINTILE_SIZE)
            nl, ns = l_mask.sum(), s_mask.sum()
            if nl > 0 and ns > 0:
                lr = gross_ret_test[t, l_mask].mean()
                sr = gross_ret_test[t, s_mask].mean()
                portfolio_pnl[t] = (lr - sr) / 2.0 - cost_frac
            else:
                tradeable[t] = False
        else:
            tradeable[t] = False

    pnl_arr = portfolio_pnl[tradeable]
    wk_arr = week_idx_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "vol_regime_router", "error": "no PnL"}

    weekly = _compute_weekly_pf_vectorized(wk_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "vol_regime_router",
        "test_n": int(len(pnl_arr)),
        "trading_weeks": weekly["trading_weeks"],
        "avg_weekly_pf": weekly["avg_weekly_pf"],
        "pf_gt1_pct": weekly["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "n_high_vol_bars": int(high_vol[tradeable].sum()) if tradeable.any() else 0,
        "n_low_vol_bars": int(low_vol[tradeable].sum()) if tradeable.any() else 0,
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_news_catalyst_fold(args: tuple) -> dict:
    """Strategy 7: News catalyst. Long when aggregate news velocity z > 2
    AND publisher novelty > 0.3 (diverse genuine coverage)."""
    (fold_i, agg_news_vel_z_test, pub_novelty_test, gross_ret_test,
     week_idx_test, cost_rt_bps, cost_mult) = args

    T_test, N = gross_ret_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    agg_z_col = agg_news_vel_z_test[:, 0] if agg_news_vel_z_test.ndim > 1 else agg_news_vel_z_test
    valid = np.isfinite(gross_ret_test)

    avg_novelty = np.nanmean(pub_novelty_test, axis=1)
    catalyst_signal = (
        np.isfinite(agg_z_col) & (agg_z_col > 2.0)
        & np.isfinite(avg_novelty) & (avg_novelty > 0.3)
    )

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
    wk_arr = week_idx_test[tradeable]

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "news_catalyst", "error": "no catalyst events",
                "n_events": n_events}

    weekly = _compute_weekly_pf_vectorized(wk_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    return {
        "fold": fold_i,
        "strategy": "news_catalyst",
        "test_n": int(len(pnl_arr)),
        "n_events": n_events,
        "trading_weeks": weekly["trading_weeks"],
        "avg_weekly_pf": weekly["avg_weekly_pf"],
        "pf_gt1_pct": weekly["pf_gt1_pct"],
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "sharpe": float(pnl_arr.mean() / max(1e-10, pnl_arr.std())),
        "pnl_values": pnl_arr.tolist(),
    }


def _eval_news_silence_spike_fold(args: tuple) -> dict:
    """Strategy 8: News silence-then-spike detector. SHORT when spike_trigger fires."""
    (fold_i, spike_trigger_test, gross_ret_test, week_idx_test,
     cost_rt_bps, cost_mult) = args

    T_test, N = spike_trigger_test.shape
    cost_frac = (cost_rt_bps * cost_mult) / 10000.0

    valid = np.isfinite(spike_trigger_test) & np.isfinite(gross_ret_test)
    signal = np.where(valid & (spike_trigger_test > 0.5), -1.0, 0.0)

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
    wk_arr = week_idx_test[tradeable]

    total_events = int((signal != 0).sum())

    if len(pnl_arr) == 0:
        return {"fold": fold_i, "strategy": "news_silence_spike", "error": "no spike events",
                "total_events": total_events}

    weekly = _compute_weekly_pf_vectorized(wk_arr, pnl_arr)
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    positive_after = int(np.sum(pnl_arr < 0))
    fp_rate = positive_after / max(1, len(pnl_arr))

    return {
        "fold": fold_i,
        "strategy": "news_silence_spike",
        "test_n": int(len(pnl_arr)),
        "total_events": total_events,
        "false_positive_rate": fp_rate,
        "trading_weeks": weekly["trading_weeks"],
        "avg_weekly_pf": weekly["avg_weekly_pf"],
        "pf_gt1_pct": weekly["pf_gt1_pct"],
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
        if job_type == "reversion":
            return _eval_reversion_fold(args[1])
        elif job_type == "xs_momentum_long":
            return _eval_xs_momentum_long_fold(args[1])
        elif job_type == "tsmom":
            return _eval_tsmom_fold(args[1])
        elif job_type == "spy_neutral":
            return _eval_spy_neutral_fold(args[1])
        elif job_type == "vol_price_div":
            return _eval_vol_price_div_fold(args[1])
        elif job_type == "vol_regime_router":
            return _eval_vol_regime_router_fold(args[1])
        elif job_type == "news_catalyst":
            return _eval_news_catalyst_fold(args[1])
        elif job_type == "news_silence_spike":
            return _eval_news_silence_spike_fold(args[1])
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
    week_indices = day_indices // 7  # Calendar weeks for weekly PF

    folds = build_temporal_folds(T, PURGE_BARS, MIN_TRAIN_BARS, MIN_TEST_BARS, N_FOLDS)
    log(f"  Built {len(folds)} temporal folds for {T} bars x {N} symbols")

    if not folds:
        return [], []

    # Rolling 5d high/low for Strategy 5
    high_5 = np.full_like(ohlcv["high"], np.nan)
    low_5 = np.full_like(ohlcv["low"], np.nan)
    for t in range(4, T):
        high_5[t] = np.nanmax(ohlcv["high"][t - 4:t + 1], axis=0)
        low_5[t] = np.nanmin(ohlcv["low"][t - 4:t + 1], axis=0)

    all_jobs = []
    job_keys = []

    for horizon in horizons:
        gross_ret = compute_gross_forward_returns(ohlcv["close"], horizon)

        for cost_mult in cost_multipliers:
            config_key = f"d{horizon}_cost{cost_mult}x"

            for fi, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
                wk_idx_test = week_indices[te_s:te_e]
                gross_ret_test = gross_ret[te_s:te_e]

                # Strategy 1a: Reversion + aggregate news regime
                all_jobs.append(("reversion", (
                    fi + 1, "1a",
                    features["cs_rank_ret_20d"][te_s:te_e],
                    gross_ret_test,
                    wk_idx_test,
                    features["cs_rank_ret_20d"][te_s:te_e],
                    features["aggregate_news_velocity_zscore"][te_s:te_e],
                    features["events_20d_avg"][te_s:te_e],
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "reversion_1a", fi + 1))

                # Strategy 1b: Reversion + per-symbol news gate
                all_jobs.append(("reversion", (
                    fi + 1, "1b",
                    features["cs_rank_ret_20d"][te_s:te_e],
                    gross_ret_test,
                    wk_idx_test,
                    features["cs_rank_ret_20d"][te_s:te_e],
                    features["aggregate_news_velocity_zscore"][te_s:te_e],
                    features["events_20d_avg"][te_s:te_e],
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "reversion_1b", fi + 1))

                # Strategy 2: XS Momentum Long (top decile 60d ret)
                all_jobs.append(("xs_momentum_long", (
                    fi + 1,
                    features["cs_rank_ret_60d"][te_s:te_e],
                    gross_ret_test,
                    wk_idx_test,
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "xs_momentum_long", fi + 1))

                # Strategy 3: TSMOM
                all_jobs.append(("tsmom", (
                    fi + 1,
                    features["ret_60d"][te_s:te_e],
                    features["realized_vol_20"][te_s:te_e],
                    gross_ret_test,
                    wk_idx_test,
                    COST_TSMOM_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "tsmom", fi + 1))

                # Strategy 4: SPY-neutral
                spy_gross_ret = compute_gross_forward_returns(ohlcv["close"][:, 0:1], horizon)
                all_jobs.append(("spy_neutral", (
                    fi + 1,
                    features["spy_residual_zscore"][te_s:te_e],
                    gross_ret_test,
                    spy_gross_ret[te_s:te_e],
                    features["rolling_spy_beta_60"][te_s:te_e],
                    wk_idx_test,
                    COST_SPY_NEUTRAL_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "spy_neutral", fi + 1))

                # Strategy 5: Volume-price divergence
                all_jobs.append(("vol_price_div", (
                    fi + 1,
                    ohlcv["close"][te_s:te_e],
                    high_5[te_s:te_e],
                    low_5[te_s:te_e],
                    features["obv_slope_5"][te_s:te_e],
                    gross_ret_test,
                    wk_idx_test,
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "vol_price_divergence", fi + 1))

                # Strategy 6: Vol regime router
                all_jobs.append(("vol_regime_router", (
                    fi + 1,
                    features["cs_rank_ret_60d"][te_s:te_e],
                    features["cs_rank_ret_20d"][te_s:te_e],
                    features["vol_regime_ratio"][te_s:te_e],
                    gross_ret_test,
                    wk_idx_test,
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "vol_regime_router", fi + 1))

                # Strategy 7: News catalyst
                all_jobs.append(("news_catalyst", (
                    fi + 1,
                    features["aggregate_news_velocity_zscore"][te_s:te_e],
                    features["publisher_novelty_20d_avg"][te_s:te_e],
                    gross_ret_test,
                    wk_idx_test,
                    COST_NEWS_CAT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "news_catalyst", fi + 1))

                # Strategy 8: News silence-spike
                all_jobs.append(("news_silence_spike", (
                    fi + 1,
                    features["spike_trigger"][te_s:te_e],
                    gross_ret_test,
                    wk_idx_test,
                    COST_DEFAULT_RT_BPS, cost_mult,
                )))
                job_keys.append((config_key, "news_silence_spike", fi + 1))

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

    profitable_folds = sum(1 for f in valid_folds if f.get("avg_weekly_pf", 0) > 1.0)
    pnl_arr = np.array(all_pnl) if all_pnl else np.array([0.0])
    oos_sharpe = float(pnl_arr.mean() / max(1e-10, pnl_arr.std()))
    total_profit = float(np.sum(pnl_arr[pnl_arr > 0]))
    total_loss = float(np.abs(np.sum(pnl_arr[pnl_arr < 0])))

    dsr = _compute_dsr_3tier(oos_sharpe, len(all_pnl))

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
        "avg_weekly_pf": float(np.mean([f["avg_weekly_pf"] for f in valid_folds])),
        "avg_pf_gt1_pct": float(np.mean([f.get("pf_gt1_pct", 0) for f in valid_folds])),
        "aggregate_pf": total_profit / max(1e-10, total_loss),
        "oos_sharpe": oos_sharpe,
        "oos_mean_pnl_bps": float(pnl_arr.mean() * 10000),
        "oos_total_trades": len(all_pnl),
        **dsr,
        **extra,
        "_pnl_all": all_pnl,
        "folds": [{k: v for k, v in f.items() if k != "pnl_values"} for f in fold_results],
    }


# ======================================================================
# Portfolio Combination (Strategy 9)
# ======================================================================

def compute_portfolio_combination(results_by_config: dict) -> dict:
    log("Computing portfolio combinations...")

    target_strategies = [
        "reversion_1a",
        "xs_momentum_long",
        "tsmom",
        "spy_neutral",
        "news_silence_spike",
    ]

    portfolio_results = {}

    for config_key, config_data in results_by_config.items():
        if config_key.startswith("_"):
            continue

        strat_pnls = {}
        for strat_name in target_strategies:
            strat_data = config_data.get(strat_name, {})
            if "error" in strat_data or not isinstance(strat_data, dict):
                continue
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

        max_len = max(len(v) for v in strat_pnls.values())
        combined_pnl = np.zeros(max_len)
        n_contributing = np.zeros(max_len)

        for strat_name, pnl in strat_pnls.items():
            combined_pnl[:len(pnl)] += pnl
            n_contributing[:len(pnl)] += 1.0

        n_contributing[n_contributing == 0] = 1.0
        combined_pnl /= n_contributing

        total_profit = float(np.sum(combined_pnl[combined_pnl > 0]))
        total_loss = float(np.abs(np.sum(combined_pnl[combined_pnl < 0])))
        oos_sharpe = float(combined_pnl.mean() / max(1e-10, combined_pnl.std()))

        dsr = _compute_dsr_3tier(oos_sharpe, len(combined_pnl))

        corr_matrix = {}
        strat_names = list(strat_pnls.keys())
        for i, s1 in enumerate(strat_names):
            for j, s2 in enumerate(strat_names):
                if j > i:
                    min_len = min(len(strat_pnls[s1]), len(strat_pnls[s2]))
                    if min_len > 10:
                        corr = float(np.corrcoef(strat_pnls[s1][:min_len], strat_pnls[s2][:min_len])[0, 1])
                        corr_matrix[f"{s1}_vs_{s2}"] = round(corr, 3)

        wk_idx = np.arange(len(combined_pnl)) // 5  # ~5 bars per week
        weekly = _compute_weekly_pf_vectorized(wk_idx, combined_pnl)

        portfolio_results[config_key] = {
            "strategy": "portfolio_combination",
            "n_strategies": len(strat_pnls),
            "strategies_included": list(strat_pnls.keys()),
            "n_obs": len(combined_pnl),
            "avg_weekly_pf": weekly["avg_weekly_pf"],
            "pf_gt1_pct": weekly["pf_gt1_pct"],
            "trading_weeks": weekly["trading_weeks"],
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
    lines.append("ITERATION 4 EQUITIES: OMNI-STRATEGY DISCOVERY — INDIVIDUAL STRATEGY RESULTS")
    lines.append("=" * 110)

    for config_key in sorted(k for k in results_by_config if not k.startswith("_")):
        config = results_by_config[config_key]
        parts = config_key.split("_")
        horizon = parts[0].replace("d", "") + "d"
        cost = parts[1].replace("cost", "").replace("x", "") + "x"

        lines.append(f"\n{'='*110}")
        lines.append(f"HORIZON: {horizon} | COST: {cost}")
        lines.append(f"{'='*110}")

        strats = [(k, v) for k, v in config.items() if isinstance(v, dict) and "strategy" in v]
        strats.sort(key=lambda x: x[1].get("aggregate_pf", 0), reverse=True)

        lines.append(f"\n{'Strategy':<30s} {'AvgWPF':>7s} {'AggPF':>7s} {'Sharpe':>7s} {'MeanBps':>8s} {'WF%':>5s} {'Tier(C)':>8s} {'Tier(B)':>8s} {'Status':>8s}")
        lines.append("-" * 110)

        for key, val in strats:
            if "error" in val:
                lines.append(f"{key:<30s} {'ERROR':>7s} - {val['error']}")
                continue
            wpf = val.get("avg_weekly_pf", 0)
            apf = val.get("aggregate_pf", 0)
            sharpe = val.get("oos_sharpe", 0)
            mean_bps = val.get("oos_mean_pnl_bps", 0)
            wf_rate = val.get("wf_pass_rate", 0)
            tier_c = val.get("tier_cumulative", "FAIL")
            tier_b = val.get("tier_bonferroni", "FAIL")

            if wpf > 1.0 and apf > 1.0 and tier_c in ("STRICT", "R5") and wf_rate >= 0.8:
                status = "PASS"
            elif wpf > 1.0 and apf > 1.0:
                status = "EDGE?"
            elif apf > 1.0:
                status = "WEAK"
            else:
                status = "FAIL"

            lines.append(
                f"{key:<30s} {wpf:>7.3f} {apf:>7.3f} {sharpe:>7.3f} {mean_bps:>8.1f} {wf_rate:>4.0%} {tier_c:>8s} {tier_b:>8s} {status:>8s}"
            )

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
                    wpf_f = f.get("avg_weekly_pf", 0)
                    pf_mark = "+" if wpf_f > 1.0 else "-"
                    lines.append(
                        f"    Fold {f.get('fold', '?')}: n={f.get('test_n', 0):>5d}  "
                        f"wpf={wpf_f:.3f}[{pf_mark}]  agg_pf={f.get('aggregate_pf', 0):.3f}  "
                        f"sharpe={f.get('sharpe', 0):.3f}  weeks={f.get('trading_weeks', 0)}"
                    )

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
        lines.append(f"    AvgWeeklyPF: {pf.get('avg_weekly_pf', 0):.3f}, AggPF: {pf.get('aggregate_pf', 0):.3f}")
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
    lines.append("# Opus 4.6 Forward Predictor Discovery — Iteration 4: Equities Omni-Strategy")
    lines.append(f"\n**Date:** {meta.get('date', 'unknown')}")
    lines.append(f"**Asset Class:** US Equities (large-cap + sector ETFs)")
    lines.append(f"**Symbols:** {meta.get('n_symbols', 0)}")
    lines.append(f"**Timeframe:** Daily (1d)")
    lines.append(f"**Horizons:** {meta.get('horizons', [])} trading days")
    lines.append(f"**Features:** {meta.get('n_features', 0)}")
    lines.append(f"**DSR trials (cumulative):** {CUMULATIVE_TRIALS}")
    lines.append(f"**DSR trials (Bonferroni, new only):** {NEW_TRIALS_ONLY}")
    lines.append(f"**E[max(SR)] cumulative:** {_compute_e_max_sr(CUMULATIVE_TRIALS):.3f}")
    lines.append(f"**E[max(SR)] Bonferroni:** {_compute_e_max_sr(NEW_TRIALS_ONLY):.3f}")
    lines.append(f"**Cost model:** {COST_DEFAULT_RT_BPS} bps RT (equities, ~15x cheaper than crypto)")
    lines.append(f"**Wall clock:** {meta.get('wall_clock_seconds', 0):.0f}s")

    lines.append("\n## Strategy Families")
    lines.append("| # | Strategy | Type | Cost (RT bps) |")
    lines.append("|---|----------|------|---------------|")
    lines.append("| 1a | Reversion (agg news filter) | L/S reversion | 3 |")
    lines.append("| 1b | Reversion (per-symbol news gate) | L/S reversion | 3 |")
    lines.append("| 2 | XS Momentum Long (60d) | Long-only momentum | 3 |")
    lines.append("| 3 | TSMOM (vol-scaled) | Long-only trend | 3 |")
    lines.append("| 4 | SPY-Neutral Residual Reversion | Market-neutral pairs | 6 |")
    lines.append("| 5 | Volume-Price Divergence (OBV) | L/S divergence | 3 |")
    lines.append("| 6 | Vol Regime Router (SPY vol) | Meta-strategy | 3 |")
    lines.append("| 7 | News Catalyst Event | Long-only event | 3 |")
    lines.append("| 8 | News Silence-Spike | Short rare event | 3 |")
    lines.append("| 9 | Portfolio Combination | Combined | ~3 |")

    lines.append("\n## Results")
    lines.append("```")
    lines.append(report_text)
    lines.append("```")

    lines.append("\n## DSR Sensitivity Matrix")
    lines.append("| Config | Strategy | AggPF | Sharpe | STRICT | R5 | R10 | Bonf |")
    lines.append("|--------|----------|-------|--------|--------|----|----|------|")
    for config_key in sorted(k for k in results_by_config if not k.startswith("_")):
        config = results_by_config[config_key]
        strats = [(k, v) for k, v in config.items() if isinstance(v, dict) and "strategy" in v and "error" not in v]
        strats.sort(key=lambda x: x[1].get("aggregate_pf", 0), reverse=True)
        for key, val in strats[:3]:
            lines.append(
                f"| {config_key} | {key} | {val.get('aggregate_pf', 0):.3f} | "
                f"{val.get('oos_sharpe', 0):.3f} | {val.get('tier_cumulative', 'FAIL')} | "
                f"{val.get('dsr_r5', 0):.3f} | {val.get('dsr_r10', 0):.3f} | "
                f"{val.get('tier_bonferroni', 'FAIL')} |"
            )

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
    parser = argparse.ArgumentParser(description="Opus 4.6 Forward Predictor Discovery — Iter4 Equities")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--symbols", default=",".join(EQUITY_SYMBOLS))
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", default="/scripts/opus_discovery_iter4_equities_results.json")
    parser.add_argument("--report", default=None)
    parser.add_argument("--horizons", default=",".join(str(h) for h in HORIZONS))
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    db_url = args.database_url or DB_URL_DEFAULT
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    horizons = [int(h.strip()) for h in args.horizons.split(",")]

    log("=" * 110)
    log("Opus 4.6 Forward Predictor Discovery — ITERATION 4: EQUITIES OMNI-STRATEGY")
    log("=" * 110)
    log(f"Symbols: {len(symbols)}, Lookback: {args.lookback_days}d, Workers: {args.workers}")
    log(f"Horizons: {horizons} trading days")
    log(f"DSR trials: {CUMULATIVE_TRIALS} cumulative, {NEW_TRIALS_ONLY} new (Bonferroni)")
    log(f"E[max(SR)]: {_compute_e_max_sr(CUMULATIVE_TRIALS):.3f} cumulative, {_compute_e_max_sr(NEW_TRIALS_ONLY):.3f} Bonferroni")
    log(f"Cost model: default {COST_DEFAULT_RT_BPS} bps RT, SPY-neutral {COST_SPY_NEUTRAL_RT_BPS} bps RT")
    log(f"Cost multipliers: {COST_MULTIPLIERS}")

    t_total_start = time.time()
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=args.lookback_days)

    conn = psycopg.connect(db_url, row_factory=dict_row)
    try:
        # Phase 1: Load data
        log("\n--- PHASE 1: DATA LOADING ---")
        t1 = time.time()
        ohlcv = load_equities_ohlcv_matrix(conn, symbols, start_ts, end_ts)
        news_mat, news_cols = load_equities_news_matrix(conn, symbols, ohlcv["timestamps"])
        log(f"Phase 1 complete in {time.time() - t1:.1f}s")

        # Phase 2: Feature engineering
        log("\n--- PHASE 2: FEATURE ENGINEERING ---")
        t2 = time.time()
        features, feature_names = engineer_equities_features(ohlcv, news_mat)
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
        log("GRAND SUMMARY — EQUITIES ITERATION 4: OMNI-STRATEGY")
        log("=" * 110)
        log(f"\n{'Config':<22s} {'Best Strategy':<30s} {'AvgWPF':>7s} {'AggPF':>7s} {'Sharpe':>7s} {'BPS':>6s} {'Tier(C)':>8s} {'Tier(B)':>8s}")
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
                wpf = v.get("avg_weekly_pf", 0)
                apf = v.get("aggregate_pf", 0)
                sharpe = v.get("oos_sharpe", 0)
                bps = v.get("oos_mean_pnl_bps", 0)
                tier_c = v.get("tier_cumulative", "FAIL")
                tier_b = v.get("tier_bonferroni", "FAIL")

                if wpf > 1.0 and apf > 1.0 and tier_c == "STRICT" and v.get("wf_pass_rate", 0) >= 0.8:
                    status = " *** TRADEABLE ***"
                elif wpf > 1.0 and apf > 1.0 and tier_c in ("R5", "R10"):
                    status = " << EDGE >>"
                elif apf > 1.0:
                    status = " (weak)"
                else:
                    status = ""

                log(f"  {config_key:<20s} {best_strat:<30s} {wpf:>7.3f} {apf:>7.3f} {sharpe:>7.3f} {bps:>6.1f} {tier_c:>8s} {tier_b:>8s}{status}")
                grand_summary.append({
                    "config": config_key,
                    "best_strategy": best_strat,
                    "avg_weekly_pf": wpf,
                    "aggregate_pf": apf,
                    "oos_sharpe": sharpe,
                    "mean_pnl_bps": bps,
                    "tier_cumulative": tier_c,
                    "tier_bonferroni": tier_b,
                    "wf_pass_rate": v.get("wf_pass_rate", 0),
                })

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
            "script": "opus_discovery_iter4_equities.py",
            "asset_class": "US Equities",
            "date": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "n_symbols": len(symbols),
            "horizons": horizons,
            "n_features": len(feature_names),
            "feature_names": feature_names,
            "cost_model": {
                "default_rt_bps": COST_DEFAULT_RT_BPS,
                "tsmom_rt_bps": COST_TSMOM_RT_BPS,
                "spy_neutral_rt_bps": COST_SPY_NEUTRAL_RT_BPS,
                "news_catalyst_rt_bps": COST_NEWS_CAT_RT_BPS,
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
            alt = "/tmp/opus_discovery_iter4_equities_results.json"
            try:
                with open(alt, "w") as fp:
                    json.dump(serialisable, fp, indent=2, default=str)
                log(f"JSON results saved to: {alt}")
            except Exception:
                pass

        report_path = args.report
        if report_path is None:
            if os.path.isdir("/app"):
                report_path = "/scripts/opus_iter4_equities_results.md"
            else:
                report_path = os.path.join(os.path.dirname(__file__), "..", "..",
                                           ".claude", "output", "sentiment_audit",
                                           "09_opus_iter4_equities_results.md")
        write_markdown_report(report, results_by_config, portfolio_results, meta, report_path)

        log(f"\nEquities Iteration 4 complete. Wall clock: {wall_clock:.0f}s")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
