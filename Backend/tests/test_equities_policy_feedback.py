"""Tests for policy optimizer and calibration watchdog helpers."""

from __future__ import annotations

import numpy as np

from equities_policy_optimizer import _optimize_policy_for_rows
from equities_calibration_watchdog import _bucket_rows, _detect_violations


def _make_rows(n: int = 200, seed: int = 7):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        confidence = float(rng.uniform(0.05, 0.9))
        edge = float(rng.uniform(0.0, 0.004))
        # Higher confidence and edge imply better directional return.
        directional = (
            -0.002
            + 0.010 * max(0.0, confidence - 0.4)
            + 0.8 * edge
            + float(rng.normal(scale=0.002))
        )
        hit = directional > 0.0
        brier = float((max(1e-6, min(1 - 1e-6, confidence)) - (1.0 if hit else 0.0)) ** 2)
        rows.append(
            {
                "confidence": confidence,
                "edge_score": edge,
                "directional_realized_return": directional,
                "hit": hit,
                "brier_loss": brier,
            }
        )
    return rows


def test_optimize_policy_returns_thresholds():
    rows = _make_rows()
    policy = _optimize_policy_for_rows(
        rows,
        timeframe="1d",
        horizon="1d",
        transaction_cost_bps=6.0,
        min_samples=20,
    )
    assert policy is not None
    assert 0.05 <= policy["min_confidence"] <= 0.95
    assert policy["min_edge_score"] >= 0.0
    assert policy["metrics"]["selected"]["count"] >= 20
    assert policy["objective_score"] != 0.0
    assert policy["risk_mode"] in {"normal", "risk_off", "risk_guarded"}


def test_optimize_policy_requires_samples():
    rows = _make_rows(n=8)
    policy = _optimize_policy_for_rows(
        rows,
        timeframe="1d",
        horizon="3d",
        min_samples=20,
    )
    assert policy is None


def test_calibration_violation_detected():
    rows = []
    # High-confidence bucket performs worse than lower bucket.
    for _ in range(20):
        rows.append({"confidence": 0.85, "directional_realized_return": -0.002, "hit": False, "brier_loss": 0.64})
    for _ in range(20):
        rows.append({"confidence": 0.65, "directional_realized_return": 0.004, "hit": True, "brier_loss": 0.12})

    buckets = _bucket_rows(rows)
    violations = _detect_violations(
        buckets,
        min_bucket_samples=10,
        return_tolerance=0.0001,
        hit_tolerance=0.01,
    )
    assert any(v["type"] == "return_monotonicity_break" for v in violations)


def test_calibration_monotonic_ok():
    rows = []
    for _ in range(20):
        rows.append({"confidence": 0.85, "directional_realized_return": 0.006, "hit": True, "brier_loss": 0.10})
    for _ in range(20):
        rows.append({"confidence": 0.65, "directional_realized_return": 0.003, "hit": True, "brier_loss": 0.18})
    for _ in range(20):
        rows.append({"confidence": 0.45, "directional_realized_return": 0.001, "hit": False, "brier_loss": 0.28})

    buckets = _bucket_rows(rows)
    violations = _detect_violations(
        buckets,
        min_bucket_samples=10,
        return_tolerance=0.0001,
        hit_tolerance=0.01,
    )
    assert violations == []


def test_optimize_policy_can_switch_risk_off():
    rows = []
    # Construct consistently poor outcomes so objective should be negative.
    for i in range(120):
        rows.append(
            {
                "confidence": 0.15 + (0.6 * (i % 10) / 10.0),
                "edge_score": 0.0003 + (0.001 * ((i + 3) % 7) / 7.0),
                "directional_realized_return": -0.006 + (0.001 * ((i % 5) - 2)),
                "hit": False,
                "brier_loss": 0.45,
            }
        )

    policy = _optimize_policy_for_rows(
        rows,
        timeframe="1d",
        horizon="gap_open",
        transaction_cost_bps=6.0,
        min_samples=20,
    )
    assert policy is not None
    assert policy["objective_score"] < 0.0
    assert policy["directional_prob_floor"] >= 0.56
    assert policy["risk_mode"] in {"risk_off", "risk_guarded"}
