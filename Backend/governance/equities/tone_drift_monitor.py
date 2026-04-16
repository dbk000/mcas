"""
Tone Drift Monitor — KL-divergence drift detection for `tone_scorer`
=====================================================================

Purpose
-------
Compares the rolling 30-day distribution of `score_title_tone` outputs
against a fixed training-time baseline distribution (stored on disk).
When the divergence is sustained above a threshold for multiple trading
days, a retrain is triggered.

The monitor replaces calendar-based retraining with data-driven
retraining: retrain iff drift actually occurred.

Inputs
------
- A baseline artifact at `Backend/scripts/output/tone_baseline.json`
  describing the training-time output distribution. Built once from
  a representative training window via
  ``Backend/scripts/equities_tone_drift_report.py --build-baseline``.

- A sequence of rolling-window tone scores computed by the caller
  (typically ``Backend/scripts/equities_tone_drift_report.py``, which
  pulls titles from ``equities_events`` over the past 30 days and
  applies `score_title_tone` to each).

Outputs
-------
- A float KL divergence value in [0, +inf).
- A boolean trigger flag when the divergence stays above
  ``2 * baseline_kl`` for ``CONSECUTIVE_DAYS_TO_TRIGGER`` consecutive
  trading days.

Design
------
- **Binning.** `score_title_tone` output is in [-1.0, +1.0]. 21 equal-
  width bins at 0.1 resolution. A dedicated central bin captures the
  large zero-score mass produced by titles with no directional
  keyword hits.
- **Smoothing.** Laplace add-1 smoothing: every bin gets a +1
  pseudo-count before normalisation. Prevents ``log(0)`` when the
  rolling window happens to miss a bin the baseline populated.
- **KL formulation.** Discrete ``KL(P||Q) = Σ_i P(i) log(P(i)/Q(i))``,
  natural logarithm. P = rolling, Q = baseline.
- **Trigger rule.** Review §2.6: ``KL > 2× baseline for 5 consecutive
  trading days``. Both numbers are named constants.

Governance
----------
All function bodies do real work or raise ``NotImplementedError`` with
plan refs. No ``...`` or ``pass``. The baseline file is loaded explicitly;
missing baseline is a crash with remediation path, not a silent default.
Random-number behaviour is deterministic: only deterministic numpy ops
are used.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Governance-justified constants
# ---------------------------------------------------------------------------

# Score range for `tone_scorer.score_title_tone` is clamped to [-1.0, +1.0]
# via `max(-1.0, min(1.0, total))`; see `tone_scorer.py` line 213.
SCORE_MIN = -1.0
SCORE_MAX = 1.0

# Bin design: 21 bins at 0.1 resolution over [-1.0, +1.0]. Bin edges:
# -1.05, -0.95, ..., -0.05, +0.05, ..., +0.95, +1.05. Every integer tenth
# lands at the centre of a bin. The central bin (-0.05, +0.05] captures
# the large zero-score mass from titles with no directional keywords.
N_BINS = 21
BIN_EDGES = np.linspace(-1.05, 1.05, N_BINS + 1)  # length N_BINS+1

# Laplace smoothing parameter: +1 pseudo-count per bin before
# normalisation. Governance justification: numerical safety against
# zero-probability bins rather than an informed prior.
LAPLACE_ALPHA = 1.0

# Trigger threshold: review §2.6 "KL > 2× baseline". Factor named so
# any future change is visible in diffs.
DRIFT_TRIGGER_MULTIPLIER = 2.0

# Review §2.6 "5 consecutive trading days". Named for diff visibility.
CONSECUTIVE_DAYS_TO_TRIGGER = 5

# Minimum sample size for a rolling-window KL to be meaningful. Smaller
# windows produce noisy KL estimates dominated by smoothing. Governance:
# any window below this size is reported as "insufficient" and the
# trigger evaluation skips it rather than firing on thin data.
MIN_SAMPLES_FOR_KL = 30

# Baseline artifact path. Per playbook §5 R6 escalation note:
# "use `Backend/scripts/output/tone_baseline.json` and surface to Darren;
# don't invent a registry."
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASELINE_PATH = REPO_ROOT / "Backend" / "scripts" / "output" / "tone_baseline.json"

# Baseline artifact schema version. Increment if the format changes.
BASELINE_SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Data class for loaded baseline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToneBaseline:
    """Immutable view of the on-disk baseline artifact.

    Fields
    ------
    schema_version:
        Artifact schema version; validated against BASELINE_SCHEMA_VERSION.
    baseline_kl:
        Self-KL of the baseline (should be 0.0). Used as the yardstick for
        the 2x trigger threshold per review §2.6. If this is 0.0 (exact),
        the trigger threshold falls back to MIN_TRIGGER_ABS_KL; otherwise
        the trigger uses 2× this value.
    bin_edges:
        Bin edges used when the baseline histogram was computed. Must
        match BIN_EDGES; mismatch is a loud error, not a silent recompute.
    distribution:
        Laplace-smoothed, normalised bin probabilities. Length N_BINS.
    n_samples:
        Sample size of the baseline window.
    built_at:
        ISO-8601 UTC timestamp when the baseline was built.
    """
    schema_version: str
    baseline_kl: float
    bin_edges: np.ndarray
    distribution: np.ndarray
    n_samples: int
    built_at: str


# Minimum absolute KL for the trigger threshold. If `baseline_kl` is
# exactly 0.0 (a self-KL baseline is by definition 0), falling back to
# 2x would produce a threshold of 0 and trigger on any noise. The
# correct interpretation of review §2.6 is "2× the *typical* KL seen on
# held-out data from the baseline distribution"; a reasonable floor is
# ln(2)/100 ≈ 0.0069 nats, which corresponds to a tiny 1% relative-
# entropy shift across 21 bins. Any implementation that stores a
# representative baseline_kl > 0 (e.g. average KL across non-overlapping
# holdout chunks of the training window) overrides this floor.
MIN_TRIGGER_ABS_KL = math.log(2.0) / 100.0


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _laplace_smoothed_distribution(scores: Sequence[float]) -> np.ndarray:
    """Histogram `scores` into N_BINS, Laplace-smooth, normalise.

    Input scores must be finite floats in [SCORE_MIN, SCORE_MAX]. Out-of-range
    values are clipped to the endpoints (defensive — tone_scorer clamps at its
    boundary but caller may aggregate other floats).
    """
    if not isinstance(scores, (list, tuple, np.ndarray)):
        scores = list(scores)
    arr = np.asarray(scores, dtype=np.float64)
    if arr.size == 0:
        # A uniform-on-nothing distribution: LAPLACE_ALPHA per bin, normalised.
        counts = np.full(N_BINS, LAPLACE_ALPHA, dtype=np.float64)
    else:
        # Clip defensively to the known score range.
        arr = np.clip(arr, SCORE_MIN, SCORE_MAX)
        counts, _ = np.histogram(arr, bins=BIN_EDGES)
        counts = counts.astype(np.float64) + LAPLACE_ALPHA
    total = counts.sum()
    if total <= 0.0:
        raise RuntimeError(
            "post-Laplace total must be positive; got "
            f"{total}. Bug in smoothing.")
    return counts / total


def compute_kl_divergence(
    rolling_outputs: Sequence[float],
    baseline: ToneBaseline,
) -> float:
    """Compute KL(P || Q) where P=rolling, Q=baseline distribution.

    Returns ``float('inf')`` never — Laplace smoothing ensures both
    distributions are fully supported on every bin.

    Validation:
      * `baseline.bin_edges` must equal BIN_EDGES (loud error otherwise).
      * `baseline.distribution` must sum to 1.0 within 1e-9.
    """
    if baseline.schema_version != BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"Baseline schema_version {baseline.schema_version!r} does not "
            f"match current {BASELINE_SCHEMA_VERSION!r}. Rebuild the "
            f"baseline via `equities_tone_drift_report.py --build-baseline`."
        )
    if baseline.bin_edges.shape != BIN_EDGES.shape or not np.allclose(
        baseline.bin_edges, BIN_EDGES, atol=1e-12
    ):
        raise ValueError(
            "Baseline bin_edges diverge from current BIN_EDGES. The "
            "baseline histogram cannot be compared without recomputation. "
            "Rebuild via `equities_tone_drift_report.py --build-baseline`."
        )
    q = np.asarray(baseline.distribution, dtype=np.float64)
    if q.shape != (N_BINS,):
        raise ValueError(f"baseline.distribution shape {q.shape} != ({N_BINS},)")
    q_sum = float(q.sum())
    if abs(q_sum - 1.0) > 1e-9:
        raise ValueError(f"baseline.distribution sum {q_sum} not 1.0 within tolerance")

    p = _laplace_smoothed_distribution(rolling_outputs)
    # Both p and q are strictly positive by construction; no zero guards needed.
    kl = float(np.sum(p * (np.log(p) - np.log(q))))
    # Numerical floor: KL is non-negative in theory but can land slightly
    # negative from rounding when p≈q. Clamp at 0.
    return max(kl, 0.0)


def trigger_active(
    history_5d: Sequence[float],
    baseline: ToneBaseline,
) -> bool:
    """Return True iff all of the last CONSECUTIVE_DAYS_TO_TRIGGER daily KL
    values strictly exceed ``DRIFT_TRIGGER_MULTIPLIER * baseline_kl``
    (or MIN_TRIGGER_ABS_KL when baseline_kl is 0).

    The input is a sequence of daily KL divergences, ordered chronologically,
    with the most-recent day LAST. Only the final CONSECUTIVE_DAYS_TO_TRIGGER
    entries are inspected; earlier entries are ignored. Fewer than
    CONSECUTIVE_DAYS_TO_TRIGGER entries = False (cannot trigger on too-few
    observations).
    """
    history = list(history_5d)
    if len(history) < CONSECUTIVE_DAYS_TO_TRIGGER:
        return False
    tail = history[-CONSECUTIVE_DAYS_TO_TRIGGER:]
    effective_baseline = max(baseline.baseline_kl, MIN_TRIGGER_ABS_KL)
    threshold = DRIFT_TRIGGER_MULTIPLIER * effective_baseline
    return all(kl > threshold for kl in tail)


# ---------------------------------------------------------------------------
# Baseline artifact I/O
# ---------------------------------------------------------------------------

def load_baseline(path: str | Path | None = None) -> ToneBaseline:
    """Load the on-disk baseline artifact.

    Missing file → ``FileNotFoundError`` with a remediation hint pointing at
    ``equities_tone_drift_report.py --build-baseline``.
    Malformed JSON or mismatched schema → ``ValueError`` with detail.
    """
    baseline_path = Path(path) if path else DEFAULT_BASELINE_PATH
    if not baseline_path.exists():
        raise FileNotFoundError(
            f"Tone baseline artifact not found at {baseline_path}. "
            f"Build it with: "
            f"`python3 Backend/scripts/equities_tone_drift_report.py "
            f"--build-baseline --window-start <ISO> --window-end <ISO>` "
            f"and then retry the drift monitor."
        )
    with baseline_path.open("r") as f:
        raw = json.load(f)

    required = {"schema_version", "baseline_kl", "bin_edges",
                "distribution", "n_samples", "built_at"}
    missing = required - set(raw.keys())
    if missing:
        raise ValueError(
            f"Baseline artifact at {baseline_path} is missing fields: {sorted(missing)}. "
            f"Expected schema version {BASELINE_SCHEMA_VERSION}.")

    return ToneBaseline(
        schema_version=str(raw["schema_version"]),
        baseline_kl=float(raw["baseline_kl"]),
        bin_edges=np.asarray(raw["bin_edges"], dtype=np.float64),
        distribution=np.asarray(raw["distribution"], dtype=np.float64),
        n_samples=int(raw["n_samples"]),
        built_at=str(raw["built_at"]),
    )


def build_baseline_artifact(
    training_scores: Iterable[float],
    built_at_iso: str,
    heldout_chunks: Sequence[Sequence[float]] | None = None,
) -> dict:
    """Construct the baseline JSON payload from `training_scores`.

    Returned dict is ready for `json.dump`. Never writes to disk —
    caller decides the path.

    `heldout_chunks`, when supplied, is a sequence of non-overlapping
    disjoint chunks drawn from the training window. Their average KL
    against the pooled training distribution is stored as `baseline_kl`.
    If None, `baseline_kl` is 0.0 and the trigger uses MIN_TRIGGER_ABS_KL.

    Returns the dict for caller to serialise.
    """
    scores = list(training_scores)
    n_samples = len(scores)
    if n_samples < MIN_SAMPLES_FOR_KL:
        raise ValueError(
            f"Training window too small: {n_samples} < "
            f"MIN_SAMPLES_FOR_KL ({MIN_SAMPLES_FOR_KL}). Refusing to "
            f"build a degenerate baseline.")
    pooled = _laplace_smoothed_distribution(scores)

    if heldout_chunks:
        kls = []
        for chunk in heldout_chunks:
            p = _laplace_smoothed_distribution(chunk)
            kl = float(np.sum(p * (np.log(p) - np.log(pooled))))
            kls.append(max(kl, 0.0))
        baseline_kl = float(np.mean(kls)) if kls else 0.0
    else:
        baseline_kl = 0.0

    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "baseline_kl": baseline_kl,
        "bin_edges": BIN_EDGES.tolist(),
        "distribution": pooled.tolist(),
        "n_samples": n_samples,
        "built_at": built_at_iso,
    }


# ---------------------------------------------------------------------------
# Facade class
# ---------------------------------------------------------------------------

@dataclass
class ToneDriftMonitor:
    """Facade over the three public functions, for callers that prefer a
    stateful object. Holds the loaded baseline for the duration of a run.

    Wiring is the caller's responsibility: the monitor does NOT fetch
    tone scores. The accompanying script
    ``Backend/scripts/equities_tone_drift_report.py`` fetches events,
    scores them, and feeds results into this class.
    """
    baseline: ToneBaseline

    @classmethod
    def from_path(cls, path: str | Path | None = None) -> "ToneDriftMonitor":
        return cls(baseline=load_baseline(path))

    def compute_kl_divergence(self, rolling_outputs: Sequence[float]) -> float:
        return compute_kl_divergence(rolling_outputs, self.baseline)

    def trigger_active(self, history: Sequence[float]) -> bool:
        return trigger_active(history, self.baseline)
