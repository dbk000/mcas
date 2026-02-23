"""Acceptance + leakage tests for equities forward predictor core logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from equities_predictor.calendar import (
    is_trading_day,
    last_close_ts,
    next_open_ts,
    session_relation,
    session_state,
)
from equities_predictor.ids import deterministic_uuid, stable_hash
from equities_forward_predictor import (
    _apply_transaction_costs,
    _classification_metrics,
    _compute_daily_pf,
    _coverage_quality_factor,
    _deflated_sharpe_ratio,
    _fit_linear_ridge,
    _model_quality_factor,
    _norm_cdf,
    _opportunity_edge_score,
    _regime_quality_factor,
    _score_combined,
    _time_symbol_split,
    _train_combined_model,
    _walk_forward_splits,
    stats_ppf,
)
from equities_feature_builder import _event_window_stats


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_pred - y_true) ** 2)))


def test_calendar_session_and_timezone_monotonicity():
    # Friday pre-open ET
    ts_pre = datetime(2026, 2, 20, 13, 0, tzinfo=timezone.utc)
    assert session_relation(ts_pre) == "published_preopen"
    assert session_state(ts_pre) in {"ETH", "CLOSED"}

    # During RTH ET
    ts_rth = datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc)
    assert session_relation(ts_rth) == "intraday"
    assert session_state(ts_rth) == "RTH"

    # Weekend
    ts_weekend = datetime(2026, 2, 21, 15, 0, tzinfo=timezone.utc)
    assert session_relation(ts_weekend) == "weekend"
    assert not is_trading_day(ts_weekend.date())

    nxt = next_open_ts(ts_weekend)
    prev = last_close_ts(ts_weekend)
    assert nxt > ts_weekend
    assert prev <= ts_weekend
    assert nxt > prev


def test_event_window_stats_never_uses_future_events():
    as_of = datetime(2026, 2, 22, 15, 0, tzinfo=timezone.utc)
    events = [
        {
            "available_at_ts": datetime(2026, 2, 22, 14, 0, tzinfo=timezone.utc),
            "sentiment_score": 0.8,
            "anti_bot_score": 0.1,
            "source": "SEC_EDGAR",
        },
        {
            "available_at_ts": datetime(2026, 2, 22, 16, 0, tzinfo=timezone.utc),  # future
            "sentiment_score": -0.9,
            "anti_bot_score": 0.9,
            "source": "OPEN_NEWS",
        },
    ]
    feats, coverage = _event_window_stats(events, as_of_ts=as_of)
    assert feats["event_count_6h"] == 1
    assert coverage["source_count_6h"] == 1
    assert feats["event_sentiment_mean_6h"] > 0


def test_time_symbol_split_has_no_overlap_and_symbol_holdout():
    rows = []
    symbols = ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"]
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for s in symbols:
        for i in range(100):
            rows.append({"symbol": s, "bar_start_ts": base.replace(day=min(28, 1 + (i % 28)))})

    split = _time_symbol_split(rows)
    train = set(split["train"])
    time_test = set(split["time_test"])
    symbol_test = set(split["symbol_test"])

    assert train.isdisjoint(time_test)
    assert train.isdisjoint(symbol_test)
    assert time_test.isdisjoint(symbol_test)
    assert len(symbol_test) > 0


def test_determinism_ids_and_model_outputs():
    id1 = deterministic_uuid("equities_prediction", "EQFP", "v1", "AAPL", "1h", "2026-01-01T00:00:00+00:00", "1d")
    id2 = deterministic_uuid("equities_prediction", "EQFP", "v1", "AAPL", "1h", "2026-01-01T00:00:00+00:00", "1d")
    assert id1 == id2
    assert stable_hash("a", 1, 2.0) == stable_hash("a", 1, 2.0)

    rng = np.random.default_rng(42)
    X = rng.normal(size=(400, 8))
    y = (0.8 * X[:, 0] - 0.4 * X[:, 1] + 0.2 * X[:, 2] + rng.normal(scale=0.1, size=400))
    yc = (y > 0).astype(float)

    bundle1 = _train_combined_model(X[:300], y[:300], yc[:300])
    bundle2 = _train_combined_model(X[:300], y[:300], yc[:300])

    p1_ret, p1_prob = _score_combined(bundle1, X[300:])
    p2_ret, p2_prob = _score_combined(bundle2, X[300:])

    assert np.allclose(p1_ret, p2_ret)
    assert np.allclose(p1_prob, p2_prob)


def test_leakage_randomized_timestamps_collapse_signal():
    rng = np.random.default_rng(123)
    n = 1200
    event_signal = rng.normal(size=n)
    price_signal = rng.normal(size=n)
    noise = rng.normal(scale=0.25, size=n)

    y = 0.75 * event_signal + 0.25 * price_signal + noise
    split = int(n * 0.7)

    X_train = np.column_stack([event_signal[:split], price_signal[:split]])
    X_test = np.column_stack([event_signal[split:], price_signal[split:]])
    y_train = y[:split]
    y_test = y[split:]

    w = _fit_linear_ridge(X_train, y_train)
    pred = X_test @ w
    baseline_rmse = _rmse(y_test, pred)

    shuffled_event = event_signal.copy()
    rng.shuffle(shuffled_event)
    X_train_leak = np.column_stack([shuffled_event[:split], price_signal[:split]])
    X_test_leak = np.column_stack([shuffled_event[split:], price_signal[split:]])

    w_leak = _fit_linear_ridge(X_train_leak, y_train)
    pred_leak = X_test_leak @ w_leak
    leak_rmse = _rmse(y_test, pred_leak)

    assert leak_rmse > baseline_rmse * 1.2


def test_label_shift_degrades_accuracy():
    rng = np.random.default_rng(7)
    n = 1000
    x = rng.normal(size=(n, 3))
    y = (0.9 * x[:, 0] - 0.3 * x[:, 1] + rng.normal(scale=0.3, size=n))

    split = 700
    w = _fit_linear_ridge(x[:split], y[:split])
    pred = x[split:] @ w
    base_rmse = _rmse(y[split:], pred)

    # leakage-prone wrong label alignment: shift labels backward by 1
    y_shift = np.roll(y, -1)
    w_shift = _fit_linear_ridge(x[:split], y_shift[:split])
    pred_shift = x[split:] @ w_shift
    shift_rmse = _rmse(y[split:], pred_shift)

    assert shift_rmse > base_rmse * 1.1


def test_ablation_matrix_expected_deltas():
    rng = np.random.default_rng(99)
    n = 1000

    price = rng.normal(size=n)
    sentiment = rng.normal(size=n)
    events = rng.normal(size=n)
    y = 0.5 * price + 0.35 * sentiment + 0.2 * events + rng.normal(scale=0.2, size=n)

    split = 700

    def fit_rmse(X):
        w = _fit_linear_ridge(X[:split], y[:split])
        pred = X[split:] @ w
        return _rmse(y[split:], pred)

    X_full = np.column_stack([price, sentiment, events])
    X_no_sent = np.column_stack([price, np.zeros_like(sentiment), events])
    X_no_events = np.column_stack([price, sentiment, np.zeros_like(events)])
    X_no_price = np.column_stack([np.zeros_like(price), sentiment, events])

    rmse_full = fit_rmse(X_full)
    rmse_no_sent = fit_rmse(X_no_sent)
    rmse_no_events = fit_rmse(X_no_events)
    rmse_no_price = fit_rmse(X_no_price)

    assert rmse_no_sent > rmse_full
    assert rmse_no_events > rmse_full
    assert rmse_no_price > rmse_full


def test_classification_metrics_sanity():
    y_true = np.array([0, 1, 1, 0, 1], dtype=float)
    y_prob = np.array([0.1, 0.9, 0.8, 0.3, 0.6], dtype=float)
    m = _classification_metrics(y_true, y_prob)
    assert 0.0 <= m["accuracy"] <= 1.0
    assert 0.0 <= m["precision"] <= 1.0
    assert 0.0 <= m["recall"] <= 1.0


def test_walk_forward_splits_are_forward_only_with_purge():
    """Walk-forward must have purge gap (embargo) between train and test."""
    rows = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(600):
        rows.append(
            {
                "symbol": "AAPL" if i % 2 == 0 else "MSFT",
                "bar_start_ts": base + timedelta(hours=i),
            }
        )
    # Default: 5 folds, 24-bar purge gap
    folds = _walk_forward_splits(rows, min_train=120, min_test=30, folds=5, purge_bars=24)
    assert len(folds) >= 4, f"Expected >= 4 folds, got {len(folds)}"
    for train_idx, test_idx in folds:
        assert train_idx
        assert test_idx
        train_last = max(rows[i]["bar_start_ts"] for i in train_idx)
        test_first = min(rows[i]["bar_start_ts"] for i in test_idx)
        # Purge gap: test_first must be > train_last + purge period
        gap = (test_first - train_last).total_seconds() / 3600
        assert gap >= 12, f"Purge gap too small: {gap}h (need >= 12h for 24-bar purge on alternating symbols)"
        assert train_last < test_first


def test_walk_forward_default_5_folds():
    """CLAUDE.md Section 4.1: Minimum 5-fold purged rolling walk-forward."""
    rows = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(1200):
        rows.append(
            {
                "symbol": "AAPL" if i % 3 == 0 else ("MSFT" if i % 3 == 1 else "NVDA"),
                "bar_start_ts": base + timedelta(hours=i),
            }
        )
    folds = _walk_forward_splits(rows)  # Should use default folds=5
    assert len(folds) == 5, f"Expected 5 folds (CLAUDE.md minimum), got {len(folds)}"


def test_daily_pf_computation():
    """CLAUDE.md Section 4.5: Average daily PF > 1.0."""
    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)  # Monday
    timestamps = [
        base,
        base + timedelta(hours=1),
        base + timedelta(hours=2),
        base + timedelta(days=1),
        base + timedelta(days=1, hours=1),
        base + timedelta(days=2),
    ]
    # Day 1: +100, -50, +30 => PF = 130/50 = 2.6
    # Day 2: +200, -300 => PF = 200/300 = 0.667
    # Day 3: +50 => PF = 5.0 (capped, all-win)
    pnl_values = [100.0, -50.0, 30.0, 200.0, -300.0, 50.0]
    result = _compute_daily_pf(timestamps, pnl_values)
    assert result["trading_days"] == 3
    assert result["avg_daily_pf"] > 0
    # Day 1 PF=2.6, Day 2 PF=0.667, Day 3 PF=5.0 => avg = 2.756
    assert abs(result["avg_daily_pf"] - (2.6 + 200 / 300 + 5.0) / 3) < 0.01


def test_deflated_sharpe_ratio():
    """CLAUDE.md Section 4.3: DSR correction for multiple testing."""
    # High SR with few trials should pass
    dsr_good = _deflated_sharpe_ratio(observed_sr=2.0, n_trials=1, n_obs=252)
    assert dsr_good["dsr"] > 0, "High SR with 1 trial should have positive DSR"

    # Mediocre SR with many trials should fail
    dsr_bad = _deflated_sharpe_ratio(observed_sr=0.5, n_trials=100, n_obs=252)
    assert dsr_bad["dsr"] < 0, "Low SR with 100 trials should have negative DSR"
    assert dsr_bad["e_max_sr"] > 0.5, "E[max(SR)] with 100 trials should exceed 0.5"


def test_transaction_costs_deducted():
    """CLAUDE.md Section 4.4: Forward returns must include transaction costs."""
    gross = 0.01  # 100 bps
    net_1h = _apply_transaction_costs(gross, timeframe="1h")
    net_1d = _apply_transaction_costs(gross, timeframe="1d")
    # Both should be less than gross
    assert net_1h < gross
    assert net_1d < gross
    # 1h has higher slippage than 1d
    assert net_1h < net_1d
    # At 1.5x multiplier, costs should be higher
    net_1h_15x = _apply_transaction_costs(gross, timeframe="1h", cost_multiplier=1.5)
    assert net_1h_15x < net_1h


def test_model_quality_factor_rewards_better_model():
    null = {"rmse": 0.03, "sign_accuracy": 0.51, "precision": 0.50, "accuracy": 0.51}
    better = {"rmse": 0.02, "sign_accuracy": 0.57, "precision": 0.56, "accuracy": 0.56}
    worse = {"rmse": 0.04, "sign_accuracy": 0.48, "precision": 0.47, "accuracy": 0.48}

    q_better = _model_quality_factor(better, null)
    q_worse = _model_quality_factor(worse, null)
    assert q_better > 1.0
    assert q_worse < 1.0
    assert q_better > q_worse


def test_edge_score_penalizes_low_quality_inputs():
    source_cov_good = {"source_count_24h": 4, "source_count_3d": 6}
    source_cov_bad = {"source_count_24h": 0, "source_count_3d": 0}

    cov_good = _coverage_quality_factor(source_cov_good, anti_bot_score=0.1)
    cov_bad = _coverage_quality_factor(source_cov_bad, anti_bot_score=0.95)
    assert cov_good > cov_bad

    reg_good = _regime_quality_factor({"trend_regime": "bull", "vol_regime": "low"}, "LONG")
    reg_bad = _regime_quality_factor({"trend_regime": "bull", "vol_regime": "high"}, "SHORT")
    assert reg_good > reg_bad

    score_good = _opportunity_edge_score(
        predicted_return=0.012,
        predicted_prob_up=0.68,
        direction="LONG",
        model_quality_factor=1.2,
        coverage_quality_factor=cov_good,
        regime_quality_factor=reg_good,
    )
    score_bad = _opportunity_edge_score(
        predicted_return=0.012,
        predicted_prob_up=0.68,
        direction="LONG",
        model_quality_factor=0.8,
        coverage_quality_factor=cov_bad,
        regime_quality_factor=reg_bad,
    )
    assert score_good > score_bad
