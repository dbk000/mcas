"""
Statistics: PF, WR, avg daily PF, DSR (Harvey-Liu 2016), walk-forward splitter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd


def profit_factor(pnl: np.ndarray) -> float:
    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def win_rate(pnl: np.ndarray) -> float:
    if pnl.size == 0:
        return 0.0
    return float((pnl > 0).mean())


def avg_r(r_multiples: np.ndarray) -> float:
    if r_multiples.size == 0:
        return 0.0
    return float(np.mean(r_multiples))


def avg_daily_pf(pnl: pd.Series, dates: pd.Series) -> float:
    """
    Compute PF per UTC trading day, then average. Root CLAUDE.md §4.5.
    dates: pd.Series of date (or datetime) aligned with pnl.
    """
    if pnl.empty:
        return 0.0
    df = pd.DataFrame({"pnl": pnl.values, "day": pd.to_datetime(dates).dt.date})
    daily_pfs = []
    for _, grp in df.groupby("day"):
        p = grp["pnl"].values
        if len(p) == 0:
            continue
        gains = p[p > 0].sum()
        losses = -p[p < 0].sum()
        if losses > 0:
            daily_pfs.append(gains / losses)
        elif gains > 0:
            daily_pfs.append(10.0)  # cap infinite PF days at 10 so average doesn't blow up
    return float(np.mean(daily_pfs)) if daily_pfs else 0.0


def sharpe_ratio(pnl: np.ndarray) -> float:
    if pnl.size < 2:
        return 0.0
    mu = pnl.mean()
    sd = pnl.std(ddof=1)
    if sd == 0:
        return 0.0
    return float(mu / sd)


def deflated_sharpe_ratio(pnl: np.ndarray, n_trials: int, sr_std: float | None = None) -> float:
    """
    Harvey-Liu 2016 DSR. Returns the probabilistic Sharpe after correcting for n_trials.
    If insufficient data, returns 0.0 (conservative).
    """
    if pnl.size < 20 or n_trials < 1:
        return 0.0
    sr = sharpe_ratio(pnl)
    # Expected max SR under null with n_trials ~iid attempts.
    # E[max SR] ≈ sqrt(2 * log(n_trials)) (extreme-value approximation).
    e_max_sr = np.sqrt(2.0 * np.log(max(n_trials, 2))) * (sr_std if sr_std else 1.0)
    # Skew/kurt adjustments would refine further — keeping the core correction.
    gamma3 = _skew(pnl)
    gamma4 = _kurt(pnl)
    n = pnl.size
    denom = np.sqrt((1 - gamma3 * sr + 0.25 * (gamma4 - 1) * sr ** 2) / (n - 1))
    if denom <= 0:
        return 0.0
    # Probabilistic Sharpe Ratio with hurdle = e_max_sr
    psr_z = (sr - e_max_sr) / denom
    from math import erf, sqrt
    dsr = 0.5 * (1.0 + erf(psr_z / sqrt(2)))
    # Re-center: DSR > 0.5 corresponds to SR beating the adjusted hurdle. We
    # report dsr - 0.5 so the >0 gate matches the plan (§4.3).
    return float(dsr - 0.5)


def _skew(x: np.ndarray) -> float:
    if x.size < 2:
        return 0.0
    mu = x.mean()
    sd = x.std(ddof=0)
    if sd == 0:
        return 0.0
    return float(((x - mu) ** 3).mean() / sd ** 3)


def _kurt(x: np.ndarray) -> float:
    if x.size < 2:
        return 0.0
    mu = x.mean()
    sd = x.std(ddof=0)
    if sd == 0:
        return 0.0
    return float(((x - mu) ** 4).mean() / sd ** 4)


# ---------- Walk-forward splitter ----------

@dataclass
class Fold:
    idx: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def purged_rolling_folds(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    n_folds: int = 4,
    embargo_days: int = 1,
) -> list[Fold]:
    """
    Purged rolling walk-forward. Non-overlapping test windows, 1-day embargo between train and test.
    Plan §4.1 / §7.3: 4 folds.
    """
    start = pd.Timestamp(start, tz="UTC")
    end = pd.Timestamp(end, tz="UTC")
    total_days = (end - start).total_seconds() / 86400
    test_frac = 1.0 / (n_folds + 1)  # each test window is 1/(n+1) of the research window
    test_days = total_days * test_frac
    folds: list[Fold] = []
    for i in range(n_folds):
        test_end = end - pd.Timedelta(days=test_days * (n_folds - 1 - i))
        test_start = test_end - pd.Timedelta(days=test_days)
        train_end = test_start - pd.Timedelta(days=embargo_days)
        train_start = start
        folds.append(Fold(
            idx=i,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
        ))
    return folds


def holdout_split(start: str, end: str, holdout_frac: float = 0.15) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """Returns (research_start, holdout_start, holdout_end). Holdout = final holdout_frac."""
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    total = end_ts - start_ts
    holdout_start = end_ts - total * holdout_frac
    return start_ts, holdout_start, end_ts
