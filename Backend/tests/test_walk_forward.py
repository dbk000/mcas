"""Unit tests for Backend/governance/safe_filters/walk_forward.py.

Runnable via:
    python3 -m pytest Backend/tests/test_walk_forward.py -v
or
    python3 Backend/tests/test_walk_forward.py
"""
from __future__ import annotations

import sys
import os
import math
import numpy as np

# Path set-up so the test is runnable both via pytest from repo root and
# as a direct script from anywhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))  # .../MCAS
sys.path.insert(0, _REPO)

from Backend.governance.safe_filters.walk_forward import (  # noqa: E402
    walk_forward_split,
    evaluate_filter,
    deflated_sharpe,
)


# ----------------------------------------------------------------------------- #
# Fixture — 1000 deterministic synthetic trades                                 #
# ----------------------------------------------------------------------------- #

def _fixture(n: int = 1000, seed: int = 42):
    rng = np.random.default_rng(seed)
    # 180 days, trades roughly uniform in time
    t0 = np.datetime64("2025-01-01T00:00:00", "s").astype(np.int64)
    secs = np.sort(rng.integers(0, 180 * 86400, size=n)).astype(np.int64)
    ts = (t0 + secs).astype("datetime64[s]")

    # A "feature" that predicts win probability: high feature → higher WR
    feat = rng.uniform(0, 100, size=n)
    win_prob = 0.30 + 0.004 * feat  # feat=0 -> 0.30 WR, feat=100 -> 0.70 WR
    wins = rng.uniform(size=n) < win_prob
    # pnl: +1 on win, -1 on loss (R=1 always); r_multiple matches pnl
    pnl = np.where(wins, 1.0, -1.0)
    return dict(ts=ts, pnl_net=pnl, win=wins, r_multiple=pnl, feat=feat)


# ----------------------------------------------------------------------------- #
# Split tests                                                                   #
# ----------------------------------------------------------------------------- #

def test_split_returns_n_folds_minus_one():
    df = _fixture()
    folds = walk_forward_split(df["ts"], n_folds=5, embargo_days=1)
    # First fold is always dropped (no train data) → 4 folds expected
    assert len(folds) == 4


def test_split_train_before_test():
    df = _fixture()
    folds = walk_forward_split(df["ts"], n_folds=5, embargo_days=1)
    ts_s = df["ts"].astype("datetime64[s]").astype(np.int64)
    embargo = 86400
    for tr, te in folds:
        assert ts_s[tr].max() < ts_s[te].min() - embargo + 1


def test_split_non_overlapping_test_windows():
    df = _fixture()
    folds = walk_forward_split(df["ts"], n_folds=5, embargo_days=1)
    seen = set()
    for _, te in folds:
        for idx in te.tolist():
            assert idx not in seen
            seen.add(idx)


def test_split_empty_input():
    folds = walk_forward_split(np.array([], dtype="datetime64[s]"))
    assert folds == []


# ----------------------------------------------------------------------------- #
# evaluate_filter                                                               #
# ----------------------------------------------------------------------------- #

def test_filter_true_predictor_raises_pf_and_winrate():
    df = _fixture()
    # Filter: keep only feat >= 50 (the winning half)
    mask = df["feat"] >= 50
    res = evaluate_filter(df, mask, n_folds=5, embargo_days=1)
    # The true data-generating process gives WR≈0.30 overall, WR≈0.50 above feat=50
    assert res["winrate_post"] > res["winrate_pre"] + 0.05
    assert res["PF_post"] > res["PF_pre"]
    # Fold stability should be 4/4 (all folds show PF uplift for true predictor)
    assert res["fold_stability"] == res["fold_stability_max"]
    assert res["n_trades_post"] < res["n_trades_pre"]


def test_filter_identity_keeps_everything():
    df = _fixture()
    mask = np.ones(len(df["pnl_net"]), dtype=bool)
    res = evaluate_filter(df, mask)
    assert res["n_trades_post"] == res["n_trades_pre"]
    assert math.isclose(res["PF_post"], res["PF_pre"], rel_tol=1e-9)


def test_filter_noise_predictor_no_uplift():
    df = _fixture()
    rng = np.random.default_rng(7)
    noise = rng.uniform(size=len(df["pnl_net"]))
    mask = noise > 0.5
    res = evaluate_filter(df, mask)
    # Noise filter shouldn't consistently win folds
    assert res["fold_stability"] <= res["fold_stability_max"] - 1


# ----------------------------------------------------------------------------- #
# DSR                                                                           #
# ----------------------------------------------------------------------------- #

def test_dsr_monotone_in_n_trials():
    # Same raw_sr; more trials tested → smaller DSR (harder to clear null).
    # Use raw_sr=2.5 with n=500 obs — DSR sits in (0, 1) at both trial counts,
    # so the monotonicity is observable rather than saturated.
    a = deflated_sharpe(raw_sr=2.5, n_trials=10, n_obs=500)
    b = deflated_sharpe(raw_sr=2.5, n_trials=1000, n_obs=500)
    assert a > b


def test_dsr_zero_at_low_sr():
    # At SR=0 with 1000 trials, DSR must be near zero
    d = deflated_sharpe(raw_sr=0.0, n_trials=1000, n_obs=500)
    assert d < 0.01


def test_dsr_high_at_large_sr():
    # SR=2 on 500 obs with 10 trials → DSR near 1
    d = deflated_sharpe(raw_sr=2.0, n_trials=10, n_obs=500)
    assert d > 0.9


# ----------------------------------------------------------------------------- #
# Main                                                                          #
# ----------------------------------------------------------------------------- #

if __name__ == "__main__":
    fns = [f for name, f in sorted(globals().items()) if name.startswith("test_") and callable(f)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}  {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}  {type(e).__name__}: {e}")
    total = len(fns)
    print(f"\n{total - failed}/{total} passing")
    sys.exit(1 if failed else 0)
