"""Tone-drift monitor unit tests (R6).

Exercises KL-divergence maths, trigger logic, baseline artifact round-trip,
and the critical negative case: a 4-day sustained drift must NOT trigger
(only 5 consecutive days do).

Self-contained: no DB access. Each test builds a synthetic baseline
in memory via `build_baseline_artifact`, round-trips it through the
`ToneBaseline` dataclass (directly — no JSON round-trip unless we're
also testing on-disk I/O), and feeds synthetic rolling scores.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

# Make Backend/ importable regardless of pytest invocation cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(_HERE))
_REPO = os.path.dirname(_BACKEND)
for p in (_REPO, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

from governance.equities.tone_drift_monitor import (  # noqa: E402
    BASELINE_SCHEMA_VERSION,
    BIN_EDGES,
    CONSECUTIVE_DAYS_TO_TRIGGER,
    DRIFT_TRIGGER_MULTIPLIER,
    MIN_TRIGGER_ABS_KL,
    N_BINS,
    ToneBaseline,
    ToneDriftMonitor,
    build_baseline_artifact,
    compute_kl_divergence,
    load_baseline,
    trigger_active,
)


# ---------------------------------------------------------------------------
# Synthetic distributions
# ---------------------------------------------------------------------------

def _symmetric_baseline(rng: np.random.Generator, n: int = 2000) -> list[float]:
    """Symmetric-ish distribution peaked at 0 — approximates real tone output
    (lots of zero-mass, some +/- directional)."""
    choices = rng.choice(
        [-1.0, -0.9, -0.4, 0.0, 0.0, 0.0, 0.0, 0.4, 0.9, 1.0],
        size=n,
    )
    return choices.astype(float).tolist()


def _drifted_distribution(rng: np.random.Generator, n: int = 2000) -> list[float]:
    """Strongly-bullish-skewed drifted distribution — zero mass shrinks,
    positive tail inflates."""
    choices = rng.choice(
        [-0.4, 0.0, 0.0, 0.4, 0.4, 0.9, 0.9, 1.0, 1.0, 1.0],
        size=n,
    )
    return choices.astype(float).tolist()


def _baseline_from_scores(scores: list[float]) -> ToneBaseline:
    payload = build_baseline_artifact(
        training_scores=scores,
        built_at_iso="2026-04-16T00:00:00+00:00",
        heldout_chunks=None,
    )
    return ToneBaseline(
        schema_version=payload["schema_version"],
        baseline_kl=payload["baseline_kl"],
        bin_edges=np.asarray(payload["bin_edges"], dtype=np.float64),
        distribution=np.asarray(payload["distribution"], dtype=np.float64),
        n_samples=payload["n_samples"],
        built_at=payload["built_at"],
    )


# ---------------------------------------------------------------------------
# KL divergence maths
# ---------------------------------------------------------------------------

def test_kl_of_identical_distribution_is_zero():
    rng = np.random.default_rng(42)
    scores = _symmetric_baseline(rng, n=5000)
    baseline = _baseline_from_scores(scores)
    kl = compute_kl_divergence(scores, baseline)
    # Rolling vs its own baseline: ≈0; tolerate smoothing + bin jitter < 1e-4.
    assert kl < 1e-4, f"Self-KL should be ~0, got {kl}"


def test_kl_increases_with_drift():
    """KL of a drifted distribution must exceed KL of the same baseline."""
    rng = np.random.default_rng(7)
    baseline_scores = _symmetric_baseline(rng, n=5000)
    baseline = _baseline_from_scores(baseline_scores)

    drifted = _drifted_distribution(rng, n=2000)
    kl_drifted = compute_kl_divergence(drifted, baseline)
    kl_self = compute_kl_divergence(baseline_scores, baseline)

    assert kl_drifted > 10 * kl_self, (
        f"Drifted KL {kl_drifted} should be >> self-KL {kl_self}")
    # Also establish: drifted KL crosses DRIFT_TRIGGER_MULTIPLIER × floor.
    assert kl_drifted > DRIFT_TRIGGER_MULTIPLIER * MIN_TRIGGER_ABS_KL


def test_kl_clamped_nonnegative():
    rng = np.random.default_rng(1)
    scores = _symmetric_baseline(rng, n=3000)
    baseline = _baseline_from_scores(scores)
    kl = compute_kl_divergence(scores, baseline)
    assert kl >= 0.0


def test_kl_bin_edge_mismatch_rejected():
    """Baseline with tampered bin_edges must be rejected by compute_kl_divergence."""
    rng = np.random.default_rng(3)
    scores = _symmetric_baseline(rng, n=3000)
    baseline = _baseline_from_scores(scores)
    # Replace bin_edges with a subtly-different array (11 bins instead of 21).
    broken = ToneBaseline(
        schema_version=baseline.schema_version,
        baseline_kl=baseline.baseline_kl,
        bin_edges=np.linspace(-1.05, 1.05, 12),
        distribution=np.full(11, 1.0 / 11.0),
        n_samples=baseline.n_samples,
        built_at=baseline.built_at,
    )
    with pytest.raises(ValueError, match="bin_edges"):
        compute_kl_divergence(scores, broken)


def test_kl_schema_version_mismatch_rejected():
    rng = np.random.default_rng(5)
    scores = _symmetric_baseline(rng, n=3000)
    baseline = _baseline_from_scores(scores)
    bad = ToneBaseline(
        schema_version="0.9",
        baseline_kl=baseline.baseline_kl,
        bin_edges=baseline.bin_edges,
        distribution=baseline.distribution,
        n_samples=baseline.n_samples,
        built_at=baseline.built_at,
    )
    with pytest.raises(ValueError, match="schema_version"):
        compute_kl_divergence(scores, bad)


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------

def test_trigger_fires_on_five_consecutive_days():
    """Synthetic drifted distribution produces KL > baseline × 2 consistently
    for 5 consecutive days → trigger_active must be True."""
    rng = np.random.default_rng(11)
    baseline_scores = _symmetric_baseline(rng, n=5000)
    baseline = _baseline_from_scores(baseline_scores)

    # Compute 5 independently-drawn drifted distributions, score each.
    daily_kls = []
    for seed in range(5):
        drift_rng = np.random.default_rng(seed + 100)
        d = _drifted_distribution(drift_rng, n=2000)
        daily_kls.append(compute_kl_divergence(d, baseline))

    # Every day must individually exceed threshold; this is a drift so should.
    threshold = DRIFT_TRIGGER_MULTIPLIER * max(
        baseline.baseline_kl, MIN_TRIGGER_ABS_KL)
    for i, kl in enumerate(daily_kls):
        assert kl > threshold, (
            f"Day {i} drifted KL {kl} did not exceed threshold {threshold}")
    assert trigger_active(daily_kls, baseline) is True


def test_trigger_does_NOT_fire_on_four_days():
    """A 4-day drift streak (below CONSECUTIVE_DAYS_TO_TRIGGER) must not fire.
    This catches the off-by-one bug where the threshold day becomes the 4th,
    not the 5th."""
    rng = np.random.default_rng(17)
    baseline_scores = _symmetric_baseline(rng, n=5000)
    baseline = _baseline_from_scores(baseline_scores)
    four_days_drift = [
        compute_kl_divergence(_drifted_distribution(
            np.random.default_rng(seed + 200), n=2000), baseline)
        for seed in range(4)
    ]
    assert trigger_active(four_days_drift, baseline) is False


def test_trigger_does_NOT_fire_on_mixed_history():
    """5 entries where only days 1-3 are over-threshold and days 4-5 fall
    back under threshold → no trigger (tail not sustained)."""
    rng = np.random.default_rng(23)
    baseline_scores = _symmetric_baseline(rng, n=5000)
    baseline = _baseline_from_scores(baseline_scores)
    high = compute_kl_divergence(_drifted_distribution(rng, n=2000), baseline)
    low = compute_kl_divergence(_symmetric_baseline(rng, n=2000), baseline)
    history = [high, high, high, low, low]
    assert trigger_active(history, baseline) is False


def test_trigger_uses_tail_of_history():
    """7-day history: first 2 entries below threshold, final 5 above.
    Trigger should fire — only the last CONSECUTIVE_DAYS_TO_TRIGGER matter."""
    rng = np.random.default_rng(29)
    baseline_scores = _symmetric_baseline(rng, n=5000)
    baseline = _baseline_from_scores(baseline_scores)
    high_vals = [
        compute_kl_divergence(_drifted_distribution(
            np.random.default_rng(i + 300), n=2000), baseline)
        for i in range(5)
    ]
    history = [0.0, 0.0] + high_vals
    assert trigger_active(history, baseline) is True


def test_trigger_on_empty_history_is_false():
    rng = np.random.default_rng(31)
    baseline_scores = _symmetric_baseline(rng, n=5000)
    baseline = _baseline_from_scores(baseline_scores)
    assert trigger_active([], baseline) is False


# ---------------------------------------------------------------------------
# Baseline artifact I/O
# ---------------------------------------------------------------------------

def test_build_baseline_artifact_shape():
    rng = np.random.default_rng(37)
    scores = _symmetric_baseline(rng, n=3000)
    payload = build_baseline_artifact(
        training_scores=scores,
        built_at_iso="2026-04-16T12:00:00+00:00",
        heldout_chunks=None,
    )
    assert payload["schema_version"] == BASELINE_SCHEMA_VERSION
    assert payload["n_samples"] == len(scores)
    assert len(payload["distribution"]) == N_BINS
    assert abs(sum(payload["distribution"]) - 1.0) < 1e-9
    assert len(payload["bin_edges"]) == N_BINS + 1
    # With no heldout_chunks, baseline_kl is 0.0.
    assert payload["baseline_kl"] == 0.0


def test_build_baseline_with_heldout_chunks_is_positive():
    rng = np.random.default_rng(41)
    scores = _symmetric_baseline(rng, n=6000)
    payload = build_baseline_artifact(
        training_scores=scores,
        built_at_iso="2026-04-16T12:00:00+00:00",
        heldout_chunks=[scores[i:i + 500] for i in range(0, 3000, 500)],
    )
    # Chunks drawn from the same distribution: baseline_kl small but non-negative.
    assert payload["baseline_kl"] >= 0.0
    # With 6000 samples of the symmetric dist and 500-sample chunks, the KL
    # should be in the "small but nonzero" band.
    assert payload["baseline_kl"] < 0.5


def test_build_baseline_rejects_tiny_window():
    with pytest.raises(ValueError, match="Training window too small"):
        build_baseline_artifact(
            training_scores=[0.0, 0.0, 0.0],
            built_at_iso="2026-04-16T12:00:00+00:00",
            heldout_chunks=None,
        )


def test_load_baseline_missing_file_raises():
    with pytest.raises(FileNotFoundError, match="baseline artifact not found"):
        load_baseline(Path("/nonexistent/path/to/baseline.json"))


def test_load_baseline_missing_fields_raises():
    """If the on-disk JSON is malformed or missing a required field, loader must
    fail loudly with a hint."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump({"schema_version": "1.0"}, f)
        path = f.name
    try:
        with pytest.raises(ValueError, match="missing fields"):
            load_baseline(path)
    finally:
        os.unlink(path)


def test_load_baseline_roundtrip():
    """Build, write to disk, load back, verify shape preserved."""
    rng = np.random.default_rng(43)
    scores = _symmetric_baseline(rng, n=3000)
    payload = build_baseline_artifact(
        training_scores=scores,
        built_at_iso="2026-04-16T12:00:00+00:00",
        heldout_chunks=None,
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump(payload, f)
        path = f.name
    try:
        loaded = load_baseline(path)
        assert loaded.schema_version == payload["schema_version"]
        assert loaded.n_samples == payload["n_samples"]
        np.testing.assert_allclose(loaded.bin_edges, BIN_EDGES)
        np.testing.assert_allclose(loaded.distribution, payload["distribution"])
    finally:
        os.unlink(path)


def test_monitor_facade_delegates():
    """ToneDriftMonitor facade should produce identical KL values to the
    pure function form."""
    rng = np.random.default_rng(47)
    scores = _symmetric_baseline(rng, n=3000)
    payload = build_baseline_artifact(
        training_scores=scores,
        built_at_iso="2026-04-16T12:00:00+00:00",
        heldout_chunks=None,
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump(payload, f)
        path = f.name
    try:
        monitor = ToneDriftMonitor.from_path(path)
        probe = _drifted_distribution(rng, n=1000)
        direct = compute_kl_divergence(probe, monitor.baseline)
        via_facade = monitor.compute_kl_divergence(probe)
        assert direct == via_facade
    finally:
        os.unlink(path)
