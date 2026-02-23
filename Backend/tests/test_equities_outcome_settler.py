"""Core tests for equities opportunity outcome settlement."""

from __future__ import annotations

from datetime import datetime, timezone

from equities_outcome_settler import (
    _clamp_prob,
    _compute_realized_return,
    _summarize,
)


def test_compute_realized_return_close_step():
    bars = [
        {
            "bar_start_ts": datetime(2026, 2, 1, tzinfo=timezone.utc),
            "open": 100.0,
            "close": 100.0,
        },
        {
            "bar_start_ts": datetime(2026, 2, 2, tzinfo=timezone.utc),
            "open": 105.0,
            "close": 105.0,
        },
    ]

    out = _compute_realized_return(
        series=bars,
        bar_start_ts=bars[0]["bar_start_ts"],
        timeframe="1d",
        horizon="1d",
    )
    assert out is not None
    assert out["settle_bar_ts"] == bars[1]["bar_start_ts"]
    assert abs(out["realized_return"] - 0.05) < 1e-9
    assert out["target_kind"] == "close_step"


def test_compute_realized_return_gap_open():
    bars = [
        {
            "bar_start_ts": datetime(2026, 2, 20, 21, 0, tzinfo=timezone.utc),  # Fri close (16:00 ET)
            "open": 100.0,
            "close": 100.0,
        },
        {
            "bar_start_ts": datetime(2026, 2, 23, 14, 30, tzinfo=timezone.utc),  # Mon 09:30 ET
            "open": 103.0,
            "close": 104.0,
        },
    ]

    out = _compute_realized_return(
        series=bars,
        bar_start_ts=bars[0]["bar_start_ts"],
        timeframe="1h",
        horizon="gap_open",
    )
    assert out is not None
    assert out["settle_bar_ts"] == bars[1]["bar_start_ts"]
    assert abs(out["realized_return"] - 0.03) < 1e-9
    assert out["target_kind"] == "next_rth_open"


def test_probability_clamp_bounds():
    assert _clamp_prob(0.0) > 0.0
    assert _clamp_prob(1.0) < 1.0
    assert _clamp_prob(0.5) == 0.5


def test_summary_metrics_shape():
    rows = [
        {
            "horizon": "1d",
            "directional_realized_return": 0.02,
            "hit": True,
            "brier_loss": 0.16,
        },
        {
            "horizon": "1d",
            "directional_realized_return": -0.01,
            "hit": False,
            "brier_loss": 0.36,
        },
        {
            "horizon": "3d",
            "directional_realized_return": 0.03,
            "hit": True,
            "brier_loss": 0.09,
        },
    ]
    summary = _summarize(rows)
    assert summary["count"] == 3
    assert "1d" in summary["by_horizon"]
    assert "3d" in summary["by_horizon"]
    assert 0.0 <= summary["by_horizon"]["1d"]["hit_rate"] <= 1.0
