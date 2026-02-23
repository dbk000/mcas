#!/usr/bin/env python3
"""Train/score equities forward predictor and generate opportunities."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from equities_predictor.calendar import ET
from equities_predictor.db import (
    connect,
    insert_legacy_opportunity,
    load_latest_selection_policies,
    insert_model_evaluation,
    insert_predictions,
    list_symbols,
    load_feature_events,
    load_ohlcv,
    load_recent_events_for_symbol,
    log_pipeline_event,
    upsert_equities_opportunity,
)
from equities_predictor.ids import deterministic_uuid, stable_hash
from equities_predictor.universe import get_default_symbols

FEATURE_COLUMNS = [
    "price_return_1",
    "price_return_5",
    "price_return_20",
    "rolling_vol_20",
    "event_count_6h",
    "event_count_24h",
    "event_count_3d",
    "event_sentiment_mean_6h",
    "event_sentiment_mean_24h",
    "event_sentiment_mean_3d",
    "anti_bot_mean_24h",
    "sec_event_count_3d",
    "earnings_window_flag",
    "source_count_6h",
    "source_count_24h",
    "source_count_3d",
]


@dataclass
class ModelBundle:
    weights_return: np.ndarray
    weights_prob: np.ndarray
    feat_mean: np.ndarray
    feat_std: np.ndarray


def _tf_delta(timeframe: str) -> timedelta:
    return timedelta(hours=1) if timeframe == "1h" else timedelta(days=1)


def _horizon_steps(timeframe: str, horizon: str) -> int:
    if timeframe == "1d":
        mapping = {"1d": 1, "3d": 3, "5d": 5}
    else:
        mapping = {"1d": 24, "3d": 72, "5d": 120}
    return mapping[horizon]


def _build_bar_map(bars: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in bars:
        out.setdefault(row["symbol"], []).append(row)
    for sym in out:
        out[sym].sort(key=lambda x: x["bar_start_ts"])
    return out


def _next_rth_open_gap(rows: list[dict[str, Any]], idx: int) -> float | None:
    cur_close = float(rows[idx]["close"])
    if cur_close <= 0:
        return None
    for j in range(idx + 1, len(rows)):
        ts_et = rows[j]["bar_start_ts"].astimezone(ET)
        if ts_et.hour == 9 and ts_et.minute == 30:
            return (float(rows[j]["open"]) - cur_close) / cur_close
    return None


def _forward_return(rows: list[dict[str, Any]], idx: int, steps: int) -> float | None:
    j = idx + steps
    if j >= len(rows):
        return None
    base = float(rows[idx]["close"])
    if base <= 0:
        return None
    return (float(rows[j]["close"]) - base) / base


def _extract_feature_vector(row: dict[str, Any]) -> np.ndarray:
    f = row.get("features") or {}
    c = row.get("source_coverage") or {}
    values = []
    for key in FEATURE_COLUMNS:
        if key in f:
            values.append(float(f.get(key) or 0.0))
        else:
            values.append(float(c.get(key) or 0.0))
    return np.array(values, dtype=float)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _clamp(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


def _fit_linear_ridge(X: np.ndarray, y: np.ndarray, ridge: float = 1e-3) -> np.ndarray:
    xtx = X.T @ X
    reg = ridge * np.eye(xtx.shape[0])
    w = np.linalg.pinv(xtx + reg) @ (X.T @ y)
    return w


def _fit_logistic(X: np.ndarray, y: np.ndarray, lr: float = 0.05, iters: int = 400) -> np.ndarray:
    w = np.zeros(X.shape[1], dtype=float)
    for _ in range(iters):
        p = _sigmoid(X @ w)
        grad = (X.T @ (p - y)) / max(1, len(y))
        w -= lr * grad
    return w


def _classification_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, float]:
    y_pred = (y_prob >= 0.5).astype(int)
    acc = float(np.mean(y_pred == y_true)) if len(y_true) else 0.0

    tp = int(np.sum((y_pred == 1) & (y_true == 1)))
    fp = int(np.sum((y_pred == 1) & (y_true == 0)))
    fn = int(np.sum((y_pred == 0) & (y_true == 1)))
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)

    return {
        "accuracy": acc,
        "precision": float(precision),
        "recall": float(recall),
    }


def _regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    err = y_pred - y_true
    rmse = float(np.sqrt(np.mean(err ** 2))) if len(err) else 0.0
    mae = float(np.mean(np.abs(err))) if len(err) else 0.0
    sign_acc = float(np.mean((y_pred >= 0) == (y_true >= 0))) if len(err) else 0.0
    return {"rmse": rmse, "mae": mae, "sign_accuracy": sign_acc}


def _compute_daily_pf(
    timestamps: list[datetime],
    pnl_values: list[float],
) -> dict[str, float]:
    """Compute average daily Profit Factor per CLAUDE.md Section 4.5.

    PF is computed per trading day individually, then averaged.
    This prevents outlier days from masking consistent losses.
    """
    from collections import defaultdict

    daily_pnl: dict[str, list[float]] = defaultdict(list)
    for ts, pnl in zip(timestamps, pnl_values):
        day_key = ts.strftime("%Y-%m-%d")
        daily_pnl[day_key].append(pnl)

    daily_pfs: list[float] = []
    for day_key in sorted(daily_pnl.keys()):
        trades = daily_pnl[day_key]
        gross_profit = sum(p for p in trades if p > 0)
        gross_loss = abs(sum(p for p in trades if p < 0))
        if gross_loss > 0:
            daily_pfs.append(gross_profit / gross_loss)
        elif gross_profit > 0:
            daily_pfs.append(5.0)  # Cap: all-win day
        # Skip days with zero activity

    if not daily_pfs:
        return {"avg_daily_pf": 0.0, "trading_days": 0, "pf_gt1_pct": 0.0, "aggregate_pf": 0.0}

    total_profit = sum(p for p in pnl_values if p > 0)
    total_loss = abs(sum(p for p in pnl_values if p < 0))
    aggregate_pf = total_profit / total_loss if total_loss > 0 else 5.0

    return {
        "avg_daily_pf": float(np.mean(daily_pfs)),
        "median_daily_pf": float(np.median(daily_pfs)),
        "trading_days": len(daily_pfs),
        "pf_gt1_pct": float(np.mean([1.0 if pf > 1.0 else 0.0 for pf in daily_pfs])),
        "aggregate_pf": aggregate_pf,
    }


def _deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    n_obs: int,
    sr_std: float = 1.0,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> dict[str, float]:
    """Deflated Sharpe Ratio per Harvey et al. (2016), CLAUDE.md Section 4.3.

    Corrects for the number of configurations tested (multiple testing).
    DSR > 0 required for confirmation.
    """
    from scipy import stats as scipy_stats

    if n_trials <= 0 or n_obs <= 1:
        return {"dsr": 0.0, "e_max_sr": 0.0, "n_trials": n_trials, "p_value": 1.0}

    # Expected max Sharpe Ratio under null (Euler-Mascheroni approximation)
    gamma = 0.5772156649
    e_max_sr = sr_std * (
        (1.0 - gamma) * stats_ppf(1.0 - 1.0 / n_trials)
        + gamma * stats_ppf(1.0 - 1.0 / (n_trials * math.e))
    )

    # Variance of SR estimator with non-normal corrections
    sr_var = (1.0 + 0.25 * (kurt - 3.0) * observed_sr**2 - skew * observed_sr) / max(1, n_obs - 1)

    # DSR = P(SR* > E[max(SR)] | SR^ = observed_sr)
    if sr_var <= 0:
        return {"dsr": 0.0, "e_max_sr": float(e_max_sr), "n_trials": n_trials, "p_value": 1.0}

    test_stat = (observed_sr - e_max_sr) / max(1e-10, math.sqrt(sr_var))
    p_value = 1.0 - _norm_cdf(test_stat)

    return {
        "dsr": float(observed_sr - e_max_sr),
        "e_max_sr": float(e_max_sr),
        "n_trials": n_trials,
        "p_value": float(p_value),
        "test_statistic": float(test_stat),
    }


def stats_ppf(p: float) -> float:
    """Inverse standard normal CDF (percent point function). Pure numpy."""
    # Rational approximation (Abramowitz & Stegun 26.2.23)
    if p <= 0:
        return -6.0
    if p >= 1:
        return 6.0
    if p == 0.5:
        return 0.0
    if p > 0.5:
        return -stats_ppf(1.0 - p)
    t = math.sqrt(-2.0 * math.log(p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return -(t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t))


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _apply_transaction_costs(
    gross_return: float,
    *,
    timeframe: str,
    cost_multiplier: float = 1.0,
) -> float:
    """Deduct transaction costs from gross return per CLAUDE.md Section 4.4.

    Equities cost model:
    - Slippage: 5 bps for daily, 3 bps for longer horizons
    - Fee: 2 bps (average across commission-free + spread)
    - Round-trip = 2 * (slippage + fee)
    """
    if timeframe == "1h":
        slippage_bps = 5.0
    else:
        slippage_bps = 3.0
    fee_bps = 2.0
    round_trip_bps = 2.0 * (slippage_bps + fee_bps) * cost_multiplier
    return gross_return - (round_trip_bps / 10000.0)


def _time_symbol_split(records: list[dict[str, Any]]) -> dict[str, list[int]]:
    if not records:
        return {"train": [], "time_test": [], "symbol_test": []}

    symbols = sorted({r["symbol"] for r in records})
    holdout_n = max(1, int(math.ceil(len(symbols) * 0.2)))
    holdout_symbols = set(symbols[:holdout_n])

    ts_values = sorted(r["bar_start_ts"] for r in records)
    train_cut = ts_values[int(len(ts_values) * 0.70)]

    train_idx, time_test_idx, symbol_test_idx = [], [], []
    for i, r in enumerate(records):
        if r["symbol"] in holdout_symbols:
            symbol_test_idx.append(i)
            continue
        if r["bar_start_ts"] <= train_cut:
            train_idx.append(i)
        else:
            time_test_idx.append(i)

    if not time_test_idx:
        time_test_idx = train_idx[-max(1, min(50, len(train_idx))):]

    return {
        "train": train_idx,
        "time_test": time_test_idx,
        "symbol_test": symbol_test_idx,
    }


def _walk_forward_splits(
    records: list[dict[str, Any]],
    *,
    min_train: int = 200,
    min_test: int = 30,
    folds: int = 5,
    purge_bars: int = 24,
) -> list[tuple[list[int], list[int]]]:
    """Purged rolling walk-forward per CLAUDE.md Section 4.1.

    - Minimum 5-fold purged rolling walk-forward
    - purge_bars embargo gap between train and test (default 24 = 1 day at 1h)
    - Non-overlapping test periods
    - Expanding window training sets
    """
    if len(records) < min_train + min_test + purge_bars:
        return []

    ordered = sorted(range(len(records)), key=lambda i: (records[i]["bar_start_ts"], records[i]["symbol"]))
    total = len(ordered)
    # Reserve space for purge gap in each fold
    usable = total - purge_bars
    step = max(min_test, (usable - min_train) // max(1, folds))
    out: list[tuple[list[int], list[int]]] = []

    train_end = min_train
    while train_end + purge_bars + min_test <= total and len(out) < folds:
        # Purge gap: skip purge_bars records after train end
        test_start = train_end + purge_bars
        test_end = min(total, test_start + step)
        train_idx = ordered[:train_end]
        test_idx = ordered[test_start:test_end]
        if train_idx and test_idx:
            out.append((train_idx, test_idx))
        # Advance train_end to include the test period (expanding window)
        train_end = test_start + (test_end - test_start)
    return out


def _model_quality_factor(combined: dict[str, float], null_model: dict[str, float]) -> float:
    null_rmse = float(max(1e-9, null_model.get("rmse", 0.0)))
    rmse_gain = (null_rmse - float(combined.get("rmse", 0.0))) / null_rmse
    sign_gain = float(combined.get("sign_accuracy", 0.0)) - float(null_model.get("sign_accuracy", 0.0))
    precision_gain = float(combined.get("precision", 0.0)) - float(null_model.get("precision", 0.0))
    accuracy_gain = float(combined.get("accuracy", 0.0)) - float(null_model.get("accuracy", 0.0))
    score = 1.0 + (0.8 * rmse_gain) + (0.6 * sign_gain) + (0.4 * precision_gain) + (0.2 * accuracy_gain)
    return _clamp(score, 0.6, 1.6)


def _coverage_quality_factor(source_coverage: dict[str, Any], anti_bot_score: float) -> float:
    source_24h = float(source_coverage.get("source_count_24h", 0.0) or 0.0)
    source_3d = float(source_coverage.get("source_count_3d", 0.0) or 0.0)
    base = 0.85 + (0.08 * min(source_24h, 5.0)) + (0.03 * min(source_3d, 8.0))
    anti_bot_penalty = 1.0 - (0.45 * _clamp(float(anti_bot_score), 0.0, 1.0))
    return _clamp(base * anti_bot_penalty, 0.45, 1.5)


def _regime_quality_factor(regime_labels: dict[str, Any], direction: str) -> float:
    trend = str((regime_labels or {}).get("trend_regime") or "").lower()
    vol = str((regime_labels or {}).get("vol_regime") or "").lower()
    score = 1.0

    if trend == "bull" and direction == "LONG":
        score += 0.08
    elif trend == "bear" and direction == "SHORT":
        score += 0.08
    elif trend in {"bull", "bear"}:
        score -= 0.06

    if vol == "high":
        score -= 0.08
    elif vol == "low":
        score += 0.04

    return _clamp(score, 0.75, 1.25)


def _opportunity_edge_score(
    *,
    predicted_return: float,
    predicted_prob_up: float,
    direction: str,
    model_quality_factor: float,
    coverage_quality_factor: float,
    regime_quality_factor: float,
) -> float:
    if direction == "SHORT":
        directional_prob = 1.0 - float(predicted_prob_up)
    else:
        directional_prob = float(predicted_prob_up)
    directional_prob = _clamp(directional_prob, 0.0, 1.0)
    conviction = max(0.0, (directional_prob - 0.5) * 2.0)
    raw_edge = abs(float(predicted_return)) * conviction
    adjusted = raw_edge * float(model_quality_factor) * float(coverage_quality_factor) * float(regime_quality_factor)
    return float(max(0.0, adjusted))


def _min_expected_return_floor(timeframe: str, horizon: str) -> float:
    floor = 0.0008 if timeframe == "1h" else 0.0015
    if horizon == "gap_open":
        floor *= 1.2
    elif horizon == "5d":
        floor *= 0.9
    return float(floor)


def _train_combined_model(X_train: np.ndarray, y_ret: np.ndarray, y_cls: np.ndarray) -> ModelBundle:
    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0)
    std[std == 0] = 1.0
    Xn = (X_train - mean) / std

    w_ret = _fit_linear_ridge(Xn, y_ret, ridge=1e-2)
    w_cls = _fit_logistic(Xn, y_cls, lr=0.05, iters=500)
    return ModelBundle(weights_return=w_ret, weights_prob=w_cls, feat_mean=mean, feat_std=std)


def _score_combined(bundle: ModelBundle, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    Xn = (X - bundle.feat_mean) / bundle.feat_std
    pred_ret = Xn @ bundle.weights_return
    pred_prob = _sigmoid(Xn @ bundle.weights_prob)
    return pred_ret, pred_prob


def run_predictor(
    *,
    database_url: str | None,
    actor: str,
    symbols: list[str],
    timeframe: str,
    lookback_days: int,
    model_version: str,
    min_confidence: float,
) -> dict[str, Any]:
    conn = connect(database_url)
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=lookback_days)

    try:
        features = load_feature_events(
            conn,
            symbols=symbols,
            timeframe=timeframe,
            horizons=["1d", "3d", "5d", "gap_open"],
            start_ts=start_ts,
            end_ts=end_ts,
        )
        bars = load_ohlcv(
            conn,
            symbols=symbols,
            timeframe=timeframe,
            start_ts=start_ts - timedelta(days=10),
            end_ts=end_ts + timedelta(days=10),
            sources=["equities_yahoo", "equities_stooq"],
        )
        bars_by_symbol = _build_bar_map(bars)
        # Build O(1) bar_ts → index lookup per symbol (replaces O(n) linear scan)
        bar_ts_index: dict[str, dict[datetime, int]] = {}
        for sym, series in bars_by_symbol.items():
            bar_ts_index[sym] = {b["bar_start_ts"]: i for i, b in enumerate(series)}

        records = []
        for row in features:
            symbol = row["symbol"]
            horizon = row["horizon"]
            series = bars_by_symbol.get(symbol)
            if not series:
                continue

            bar_ts = row["bar_start_ts"]
            idx = bar_ts_index.get(symbol, {}).get(bar_ts)
            if idx is None:
                continue

            if horizon == "gap_open":
                y = _next_rth_open_gap(series, idx)
            else:
                steps = _horizon_steps(timeframe, horizon)
                y = _forward_return(series, idx, steps)
            if y is None:
                continue

            # Apply transaction costs to forward returns (CLAUDE.md Section 4.4)
            y_net = _apply_transaction_costs(y, timeframe=timeframe)

            x = _extract_feature_vector(row)
            records.append(
                {
                    "symbol": symbol,
                    "bar_start_ts": bar_ts,
                    "available_at_ts": row["available_at_ts"],
                    "horizon": horizon,
                    "session_state": row["session_state"],
                    "regime_labels": row.get("regime_labels") or {},
                    "source_coverage": row.get("source_coverage") or {},
                    "feature_row_id": str(row["id"]),
                    "X": x,
                    "y_ret": float(y_net),
                    "y_ret_gross": float(y),
                    "y_cls": 1.0 if y_net > 0 else 0.0,
                }
            )

        if not records:
            log_pipeline_event(
                conn,
                actor=actor,
                event_type="PREDICTOR_EMPTY",
                message="No training records available for equities predictor run",
                data={"timeframe": timeframe},
            )
            conn.commit()
            return {"records": 0, "predictions": 0, "opportunities": 0}

        split = _time_symbol_split(records)
        train_idx = split["train"]
        time_eval_idx = split["time_test"]
        symbol_eval_idx = split["symbol_test"]

        X = np.vstack([r["X"] for r in records])
        y_ret = np.array([r["y_ret"] for r in records], dtype=float)
        y_cls = np.array([r["y_cls"] for r in records], dtype=float)

        X_train = X[train_idx]
        y_ret_train = y_ret[train_idx]
        y_cls_train = y_cls[train_idx]

        bundle = _train_combined_model(X_train, y_ret_train, y_cls_train)

        # Baselines for comparison.
        pred_null_ret = np.full_like(y_ret, fill_value=float(np.mean(y_ret_train)))
        pred_momo_ret = X[:, FEATURE_COLUMNS.index("price_return_5")]
        pred_sent_ret = X[:, FEATURE_COLUMNS.index("event_sentiment_mean_24h")]

        combined_ret, combined_prob = _score_combined(bundle, X)

        def _eval_indices(
            *,
            name: str,
            yhat_ret: np.ndarray,
            idxs: list[int],
            yhat_prob: np.ndarray | None = None,
            split_type: str,
        ) -> dict[str, float]:
            if not idxs:
                return {
                    "model": name,
                    "split_type": split_type,
                    "samples": 0.0,
                    "rmse": 0.0,
                    "mae": 0.0,
                    "sign_accuracy": 0.0,
                    "accuracy": 0.0,
                    "precision": 0.0,
                    "recall": 0.0,
                }
            idx = np.array(idxs, dtype=int)
            reg = _regression_metrics(y_ret[idx], yhat_ret[idx])
            if yhat_prob is None:
                yhat_prob = (yhat_ret >= 0).astype(float)
            cls = _classification_metrics(y_cls[idx], yhat_prob[idx])
            return {**reg, **cls, "model": name, "split_type": split_type, "samples": float(len(idxs))}

        eval_by_split: dict[str, dict[str, dict[str, float]]] = {}
        for split_type, idxs in {
            "time_test": time_eval_idx,
            "symbol_test": symbol_eval_idx,
        }.items():
            eval_by_split[split_type] = {
                "null": _eval_indices(name="null", yhat_ret=pred_null_ret, idxs=idxs, split_type=split_type),
                "momentum": _eval_indices(name="momentum", yhat_ret=pred_momo_ret, idxs=idxs, split_type=split_type),
                "sentiment_only": _eval_indices(name="sentiment_only", yhat_ret=pred_sent_ret, idxs=idxs, split_type=split_type),
                "combined": _eval_indices(
                    name="combined",
                    yhat_ret=combined_ret,
                    yhat_prob=combined_prob,
                    idxs=idxs,
                    split_type=split_type,
                ),
            }

        # --- Walk-forward validation (CLAUDE.md Section 4.1) ---
        # 5-fold purged rolling walk-forward with 1-day embargo
        purge_bars = 24 if timeframe == "1h" else 1  # 1-day embargo
        wf_splits = _walk_forward_splits(records, purge_bars=purge_bars)
        wf_fold_metrics: list[dict[str, float]] = []
        wf_fold_pnl: list[list[float]] = []  # For daily PF per fold
        wf_fold_timestamps: list[list[datetime]] = []
        n_trials_tested = 0  # Track for DSR

        for fold_i, (wf_train_idx, wf_test_idx) in enumerate(wf_splits, start=1):
            idx_train = np.array(wf_train_idx, dtype=int)
            idx_test = np.array(wf_test_idx, dtype=int)
            n_trials_tested += 1
            wf_bundle = _train_combined_model(X[idx_train], y_ret[idx_train], y_cls[idx_train])
            wf_ret, wf_prob = _score_combined(wf_bundle, X[idx_test])
            reg = _regression_metrics(y_ret[idx_test], wf_ret)
            cls = _classification_metrics(y_cls[idx_test], wf_prob)

            # Compute PnL for each test record (predicted direction * actual net return)
            fold_pnl = []
            fold_ts = []
            for j, test_i in enumerate(wf_test_idx):
                direction_sign = 1.0 if wf_prob[j] >= 0.5 else -1.0
                pnl = direction_sign * y_ret[test_i]
                fold_pnl.append(pnl)
                fold_ts.append(records[test_i]["bar_start_ts"])
            wf_fold_pnl.append(fold_pnl)
            wf_fold_timestamps.append(fold_ts)

            # Daily PF for this fold
            fold_daily_pf = _compute_daily_pf(fold_ts, fold_pnl)

            wf_fold_metrics.append(
                {
                    **reg,
                    **cls,
                    "fold": float(fold_i),
                    "train_samples": float(len(wf_train_idx)),
                    "test_samples": float(len(wf_test_idx)),
                    "avg_daily_pf": fold_daily_pf["avg_daily_pf"],
                    "aggregate_pf": fold_daily_pf["aggregate_pf"],
                    "trading_days": fold_daily_pf["trading_days"],
                    "pf_gt1_pct": fold_daily_pf["pf_gt1_pct"],
                }
            )

        # Walk-forward passes if profitable in >= 80% of folds (4/5)
        profitable_folds = sum(1 for m in wf_fold_metrics if m.get("avg_daily_pf", 0) > 1.0)
        wf_pass_rate = profitable_folds / max(1, len(wf_fold_metrics))

        if wf_fold_metrics:
            # Compute overall OOS daily PF across all folds
            all_wf_pnl = [p for fold in wf_fold_pnl for p in fold]
            all_wf_ts = [t for fold in wf_fold_timestamps for t in fold]
            overall_daily_pf = _compute_daily_pf(all_wf_ts, all_wf_pnl)

            # Compute OOS Sharpe for DSR
            if all_wf_pnl:
                oos_returns = np.array(all_wf_pnl)
                oos_sharpe = float(np.mean(oos_returns) / max(1e-10, np.std(oos_returns)))
                oos_skew = float(np.mean(((oos_returns - np.mean(oos_returns)) / max(1e-10, np.std(oos_returns))) ** 3))
                oos_kurt = float(np.mean(((oos_returns - np.mean(oos_returns)) / max(1e-10, np.std(oos_returns))) ** 4))
            else:
                oos_sharpe, oos_skew, oos_kurt = 0.0, 0.0, 3.0

            # DSR correction (CLAUDE.md Section 4.3)
            dsr_result = _deflated_sharpe_ratio(
                observed_sr=oos_sharpe,
                n_trials=max(1, n_trials_tested),
                n_obs=len(all_wf_pnl),
                skew=oos_skew,
                kurt=oos_kurt,
            )

            wf_summary = {
                "model": "combined",
                "split_type": "walk_forward",
                "samples": float(sum(int(m["test_samples"]) for m in wf_fold_metrics)),
                "fold_count": float(len(wf_fold_metrics)),
                "rmse": float(np.mean([m["rmse"] for m in wf_fold_metrics])),
                "mae": float(np.mean([m["mae"] for m in wf_fold_metrics])),
                "sign_accuracy": float(np.mean([m["sign_accuracy"] for m in wf_fold_metrics])),
                "accuracy": float(np.mean([m["accuracy"] for m in wf_fold_metrics])),
                "precision": float(np.mean([m["precision"] for m in wf_fold_metrics])),
                "recall": float(np.mean([m["recall"] for m in wf_fold_metrics])),
                "rmse_std": float(np.std([m["rmse"] for m in wf_fold_metrics])),
                "accuracy_std": float(np.std([m["accuracy"] for m in wf_fold_metrics])),
                # Opus-grade additions: daily PF + DSR
                "avg_daily_pf": overall_daily_pf["avg_daily_pf"],
                "median_daily_pf": overall_daily_pf["median_daily_pf"],
                "aggregate_pf": overall_daily_pf["aggregate_pf"],
                "trading_days": overall_daily_pf["trading_days"],
                "pf_gt1_pct": overall_daily_pf["pf_gt1_pct"],
                "profitable_folds": float(profitable_folds),
                "wf_pass_rate": wf_pass_rate,
                "oos_sharpe": oos_sharpe,
                "dsr": dsr_result["dsr"],
                "dsr_e_max_sr": dsr_result["e_max_sr"],
                "dsr_p_value": dsr_result["p_value"],
                "dsr_n_trials": float(dsr_result["n_trials"]),
            }
        else:
            wf_summary = {
                "model": "combined",
                "split_type": "walk_forward",
                "samples": 0.0,
                "fold_count": 0.0,
                "rmse": 0.0,
                "mae": 0.0,
                "sign_accuracy": 0.0,
                "accuracy": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "rmse_std": 0.0,
                "accuracy_std": 0.0,
                "avg_daily_pf": 0.0,
                "median_daily_pf": 0.0,
                "aggregate_pf": 0.0,
                "trading_days": 0,
                "pf_gt1_pct": 0.0,
                "profitable_folds": 0.0,
                "wf_pass_rate": 0.0,
                "oos_sharpe": 0.0,
                "dsr": 0.0,
                "dsr_e_max_sr": 0.0,
                "dsr_p_value": 1.0,
                "dsr_n_trials": 0.0,
            }

        eval_rows = []
        for split_type, split_metrics in eval_by_split.items():
            for model_name, metrics in split_metrics.items():
                eval_rows.append(
                    {
                        "id": deterministic_uuid("eq_eval", model_version, timeframe, split_type, model_name),
                        "model_name": "EQFP",
                        "model_version": f"{model_version}_{model_name}",
                        "timeframe": timeframe,
                        "horizon": "multi",
                        "split_type": split_type,
                        "metrics": metrics,
                        "notes": "free/open baseline set",
                        "deterministic_hash": stable_hash(
                            model_version,
                            timeframe,
                            split_type,
                            model_name,
                            metrics["rmse"],
                            metrics["accuracy"],
                            metrics["samples"],
                        ),
                    }
                )
        eval_rows.append(
            {
                "id": deterministic_uuid("eq_eval", model_version, timeframe, "walk_forward", "combined"),
                "model_name": "EQFP",
                "model_version": f"{model_version}_combined",
                "timeframe": timeframe,
                "horizon": "multi",
                "split_type": "walk_forward",
                "metrics": {**wf_summary, "fold_metrics": wf_fold_metrics[:6]},
                "notes": "expanding walk-forward folds",
                "deterministic_hash": stable_hash(
                    model_version,
                    timeframe,
                    "walk_forward",
                    "combined",
                    wf_summary["rmse"],
                    wf_summary["accuracy"],
                    wf_summary["fold_count"],
                ),
            }
        )
        for row in eval_rows:
            insert_model_evaluation(conn, row)

        time_quality = _model_quality_factor(
            eval_by_split["time_test"]["combined"],
            eval_by_split["time_test"]["null"],
        )
        symbol_quality = _model_quality_factor(
            eval_by_split["symbol_test"]["combined"],
            eval_by_split["symbol_test"]["null"],
        )
        if eval_by_split["symbol_test"]["combined"].get("samples", 0.0) <= 0:
            symbol_quality = time_quality
        walk_quality = _model_quality_factor(
            wf_summary,
            eval_by_split["time_test"]["null"],
        )
        if wf_summary.get("samples", 0.0) <= 0:
            walk_quality = time_quality

        # Incorporate daily PF and DSR into quality factor (Opus-grade gate)
        pf_bonus = 0.0
        if wf_summary.get("avg_daily_pf", 0) > 1.0:
            pf_bonus = 0.1 * min(wf_summary["avg_daily_pf"] - 1.0, 0.5)
        pf_penalty = 0.0
        if wf_summary.get("avg_daily_pf", 0) < 1.0 and wf_summary.get("trading_days", 0) > 5:
            pf_penalty = 0.15  # Penalize models that lose money OOS
        dsr_penalty = 0.0
        if wf_summary.get("dsr", 0) < 0 and wf_summary.get("dsr_n_trials", 0) > 0:
            dsr_penalty = 0.10  # Penalize models that fail DSR correction

        model_quality_factor = _clamp(
            (time_quality + symbol_quality + walk_quality) / 3.0 + pf_bonus - pf_penalty - dsr_penalty,
            0.6, 1.6,
        )
        policy_map = load_latest_selection_policies(conn, timeframe=timeframe)

        # Generate predictions for latest bar per symbol+horizon.
        latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for rec, ret_hat, prob_hat in zip(records, combined_ret, combined_prob):
            key = (rec["symbol"], rec["horizon"])
            cur = latest_by_key.get(key)
            if cur is None or rec["bar_start_ts"] > cur["bar_start_ts"]:
                latest_by_key[key] = {
                    **rec,
                    "pred_ret": float(ret_hat),
                    "pred_prob": float(prob_hat),
                }

        prediction_rows = []
        opportunity_candidates: list[dict[str, Any]] = []
        anti_bot_idx = FEATURE_COLUMNS.index("anti_bot_mean_24h")
        for rec in sorted(latest_by_key.values(), key=lambda x: (x["symbol"], x["horizon"])):
            confidence = float(abs(rec["pred_prob"] - 0.5) * 2.0)
            direction = "LONG" if rec["pred_prob"] >= 0.5 else "SHORT"
            policy = policy_map.get((timeframe, rec["horizon"])) or {}
            policy_min_conf = _clamp(float(policy.get("min_confidence", 0.10)), 0.0, 1.0)
            policy_dir_prob_floor = _clamp(float(policy.get("directional_prob_floor", 0.53)), 0.5, 0.9)
            policy_expected_floor = max(0.0, float(policy.get("expected_return_floor", 0.0)))
            policy_edge_floor = max(0.0, float(policy.get("min_edge_score", 0.0)))
            policy_tx_cost_bps = max(0.0, float(policy.get("transaction_cost_bps", 0.0)))
            pred_id = deterministic_uuid(
                "equities_prediction",
                "EQFP",
                model_version,
                rec["symbol"],
                timeframe,
                rec["bar_start_ts"].isoformat(),
                rec["horizon"],
            )

            Xn = (rec["X"] - bundle.feat_mean) / bundle.feat_std
            contrib = bundle.weights_return * Xn
            top_idx = np.argsort(np.abs(contrib))[-3:][::-1]
            top_features = [
                {
                    "feature": FEATURE_COLUMNS[i],
                    "contribution": float(contrib[i]),
                }
                for i in top_idx
            ]

            anti_bot = float(rec["X"][anti_bot_idx]) if anti_bot_idx < len(rec["X"]) else 0.0
            coverage_quality_factor = _coverage_quality_factor(rec["source_coverage"], anti_bot)
            regime_quality_factor = _regime_quality_factor(rec["regime_labels"], direction)
            edge_score = _opportunity_edge_score(
                predicted_return=rec["pred_ret"],
                predicted_prob_up=rec["pred_prob"],
                direction=direction,
                model_quality_factor=model_quality_factor,
                coverage_quality_factor=coverage_quality_factor,
                regime_quality_factor=regime_quality_factor,
            )
            directional_prob = float(rec["pred_prob"] if direction == "LONG" else (1.0 - rec["pred_prob"]))
            expected_return_floor = max(
                _min_expected_return_floor(timeframe, rec["horizon"]),
                policy_expected_floor,
            )
            min_edge_score = max(expected_return_floor * 0.35, policy_edge_floor)

            recent_events = load_recent_events_for_symbol(
                conn,
                symbol=rec["symbol"],
                lookback_start=rec["available_at_ts"] - timedelta(days=3),
                as_of_ts=rec["available_at_ts"],
            )
            event_refs = [str(ev["id"]) for ev in recent_events[-5:]]

            prediction_rows.append(
                {
                    "id": pred_id,
                    "model_name": "EQFP",
                    "model_version": model_version,
                    "symbol": rec["symbol"],
                    "timeframe": timeframe,
                    "bar_start_ts": rec["bar_start_ts"],
                    "horizon": rec["horizon"],
                    "available_at_ts": rec["available_at_ts"],
                    "predicted_return": rec["pred_ret"],
                    "predicted_prob_up": rec["pred_prob"],
                    "confidence": confidence,
                    "gap_risk_score": float(
                        _clamp(abs(rec["pred_ret"]) / max(1e-6, float(np.std(y_ret[y_ret != 0]))) * 2.0, 0.0, 10.0)
                        if rec["horizon"] == "gap_open" else 0.0
                    ),
                    "regime_label": rec["regime_labels"].get("trend_regime"),
                    "feature_refs": {
                        "feature_row_id": rec["feature_row_id"],
                        "top_features": top_features,
                    },
                    "event_refs": {
                        "event_ids": event_refs,
                        "source_coverage": rec["source_coverage"],
                    },
                    "diagnostics": {
                        "session_state": rec["session_state"],
                        "regime": rec["regime_labels"],
                        "model_quality_factor": model_quality_factor,
                        "coverage_quality_factor": coverage_quality_factor,
                        "regime_quality_factor": regime_quality_factor,
                        "edge_score": edge_score,
                        "directional_prob": directional_prob,
                        "min_edge_score": min_edge_score,
                        "expected_return_floor": expected_return_floor,
                        "policy_min_confidence": policy_min_conf,
                        "policy_directional_prob_floor": policy_dir_prob_floor,
                        "policy_transaction_cost_bps": policy_tx_cost_bps,
                    },
                    "deterministic_hash": stable_hash(
                        "EQFP",
                        model_version,
                        rec["symbol"],
                        timeframe,
                        rec["bar_start_ts"].isoformat(),
                        rec["horizon"],
                        round(rec["pred_ret"], 8),
                        round(rec["pred_prob"], 8),
                    ),
                }
            )

            if confidence < max(min_confidence, policy_min_conf):
                continue
            if abs(rec["pred_ret"]) < expected_return_floor:
                continue
            if directional_prob < policy_dir_prob_floor:
                continue
            if edge_score < min_edge_score:
                continue
            if model_quality_factor < 0.80:
                continue

            opportunity_candidates.append(
                {
                    "pred_id": pred_id,
                    "symbol": rec["symbol"],
                    "horizon": rec["horizon"],
                    "timeframe": timeframe,
                    "bar_start_ts": rec["bar_start_ts"],
                    "available_at_ts": rec["available_at_ts"],
                    "pred_ret": rec["pred_ret"],
                    "pred_prob": rec["pred_prob"],
                    "confidence": confidence,
                    "direction": direction,
                    "top_features": top_features,
                    "event_refs": event_refs,
                    "regime_labels": rec["regime_labels"],
                    "feature_row_id": rec["feature_row_id"],
                    "edge_score": edge_score,
                    "directional_prob": directional_prob,
                    "expected_return_floor": expected_return_floor,
                    "min_edge_score": min_edge_score,
                    "model_quality_factor": model_quality_factor,
                    "coverage_quality_factor": coverage_quality_factor,
                    "regime_quality_factor": regime_quality_factor,
                    "policy_min_confidence": policy_min_conf,
                    "policy_directional_prob_floor": policy_dir_prob_floor,
                    "policy_transaction_cost_bps": policy_tx_cost_bps,
                }
            )

        prediction_count = insert_predictions(conn, prediction_rows)
        opportunities = 0
        for cand in opportunity_candidates:
            legacy_payload = {
                "deterministic_opportunity_id": deterministic_uuid(
                    "equities_opportunity",
                    cand["pred_id"],
                    cand["symbol"],
                    cand["horizon"],
                ),
                "horizon": cand["horizon"],
                "predicted_return": cand["pred_ret"],
                "predicted_prob_up": cand["pred_prob"],
                "confidence": cand["confidence"],
                "edge_score": cand["edge_score"],
                "directional_prob": cand["directional_prob"],
                "quality": {
                    "model_quality_factor": cand["model_quality_factor"],
                    "coverage_quality_factor": cand["coverage_quality_factor"],
                    "regime_quality_factor": cand["regime_quality_factor"],
                },
                "top_features": cand["top_features"],
                "event_ids": cand["event_refs"],
                "model": {"name": "EQFP", "version": model_version},
            }
            legacy_id = insert_legacy_opportunity(
                conn,
                {
                    "symbol": cand["symbol"],
                    "timeframe": cand["timeframe"],
                    "setup_id": "EQFP",
                    "decision": cand["direction"],
                    "score": cand["confidence"] * 100.0,
                    "reason": f"EQFP {cand['horizon']} {cand['direction']}",
                    "payload_json": legacy_payload,
                    "status": "active",
                },
            )

            eq_opp_id = deterministic_uuid("equities_opportunity", cand["pred_id"], legacy_id)
            upsert_equities_opportunity(
                conn,
                {
                    "id": eq_opp_id,
                    "prediction_id": cand["pred_id"],
                    "symbol": cand["symbol"],
                    "timeframe": cand["timeframe"],
                    "horizon": cand["horizon"],
                    "bar_start_ts": cand["bar_start_ts"],
                    "available_at_ts": cand["available_at_ts"],
                    "status": "active",
                    "expected_return": cand["pred_ret"],
                    "probability_up": cand["pred_prob"],
                    "confidence": cand["confidence"],
                    "regime_label": cand["regime_labels"].get("trend_regime"),
                    "rationale": {
                        "direction": cand["direction"],
                        "top_features": cand["top_features"],
                        "edge_score": cand["edge_score"],
                        "directional_prob": cand["directional_prob"],
                        "model_quality_factor": cand["model_quality_factor"],
                        "coverage_quality_factor": cand["coverage_quality_factor"],
                        "regime_quality_factor": cand["regime_quality_factor"],
                        "expected_return_floor": cand["expected_return_floor"],
                        "min_edge_score": cand["min_edge_score"],
                        "policy_min_confidence": cand["policy_min_confidence"],
                        "policy_directional_prob_floor": cand["policy_directional_prob_floor"],
                        "policy_transaction_cost_bps": cand["policy_transaction_cost_bps"],
                    },
                    "provenance": {
                        "feature_row_id": cand["feature_row_id"],
                        "event_ids": cand["event_refs"],
                        "model_name": "EQFP",
                        "model_version": model_version,
                        "selection_rule": "confidence+return_floor+directional_prob+edge_score",
                    },
                    "legacy_opportunity_id": legacy_id,
                },
            )
            opportunities += 1

        log_pipeline_event(
            conn,
            actor=actor,
            event_type="PREDICTOR_RUN_COMPLETE",
            message="Equities forward predictor run completed",
            data={
                "records": len(records),
                "predictions": prediction_count,
                "opportunities": opportunities,
                "timeframe": timeframe,
                "model_version": model_version,
                "selection": {
                    "model_quality_factor": model_quality_factor,
                    "min_confidence": min_confidence,
                    "directional_prob_floor_default": 0.53,
                    "active_policy_count": len(policy_map),
                },
                "validation": {
                    "walk_forward_folds": wf_summary.get("fold_count", 0),
                    "purge_bars": purge_bars,
                    "avg_daily_pf": wf_summary.get("avg_daily_pf", 0),
                    "aggregate_pf": wf_summary.get("aggregate_pf", 0),
                    "profitable_folds": wf_summary.get("profitable_folds", 0),
                    "wf_pass_rate": wf_summary.get("wf_pass_rate", 0),
                    "oos_sharpe": wf_summary.get("oos_sharpe", 0),
                    "dsr": wf_summary.get("dsr", 0),
                    "dsr_p_value": wf_summary.get("dsr_p_value", 1.0),
                    "dsr_n_trials": wf_summary.get("dsr_n_trials", 0),
                    "transaction_cost_model": "base_rate",
                    "cost_multiplier": 1.0,
                },
                "metrics": {
                    "time_test": eval_by_split["time_test"],
                    "symbol_test": eval_by_split["symbol_test"],
                    "walk_forward": wf_summary,
                },
            },
        )
        conn.commit()

        return {
            "records": len(records),
            "predictions": prediction_count,
            "opportunities": opportunities,
            "model_quality_factor": model_quality_factor,
            "validation": {
                "avg_daily_pf": wf_summary.get("avg_daily_pf", 0),
                "aggregate_pf": wf_summary.get("aggregate_pf", 0),
                "wf_pass_rate": wf_summary.get("wf_pass_rate", 0),
                "oos_sharpe": wf_summary.get("oos_sharpe", 0),
                "dsr": wf_summary.get("dsr", 0),
                "dsr_p_value": wf_summary.get("dsr_p_value", 1.0),
                "profitable_folds": wf_summary.get("profitable_folds", 0),
                "fold_count": wf_summary.get("fold_count", 0),
            },
            "metrics": {
                "time_test": eval_by_split["time_test"],
                "symbol_test": eval_by_split["symbol_test"],
                "walk_forward": wf_summary,
            },
        }
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run equities forward predictor")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--symbols", default=",".join(get_default_symbols()))
    parser.add_argument("--timeframe", default="1h", choices=["1h", "1d"])
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--model-version", default="v1_freeopen")
    parser.add_argument("--min-confidence", type=float, default=0.10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    summary = run_predictor(
        database_url=args.database_url,
        actor=args.actor,
        symbols=symbols,
        timeframe=args.timeframe,
        lookback_days=max(30, int(args.lookback_days)),
        model_version=args.model_version,
        min_confidence=max(0.0, float(args.min_confidence)),
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
