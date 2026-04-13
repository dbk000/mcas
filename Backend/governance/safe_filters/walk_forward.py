"""Walk-forward + DSR harness shared across A2 (social) and A3 (indicator) SAFE sweeps.

CLAUDE.md §4 validation contract:
  - 5-fold purged rolling walk-forward, 1-day embargo.
  - Deflated Sharpe Ratio per Harvey, Liu, Zhu (2016).
  - Profit factor (per-day mean and aggregate) as primary metric.

Design rules:
  - Pure numpy + stdlib math — no pandas/scipy hard-deps; safer across environments.
  - `df` is a dict-of-arrays (or any mapping with ``__getitem__`` returning np.ndarray)
    with required keys: 'ts' (np.datetime64[s] or pd.Timestamp), 'pnl_net' (float),
    'win' (bool), 'r_multiple' (float).  Extra keys ignored.
  - Filter predicates are vectorised boolean numpy masks of len(df).  The caller
    constructs them (e.g. ``df['rsi'] < 30``).
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Tuple

import numpy as np


# ----------------------------------------------------------------------------- #
# Walk-forward split                                                            #
# ----------------------------------------------------------------------------- #

def walk_forward_split(
    timestamps: np.ndarray,
    n_folds: int = 5,
    embargo_days: int = 1,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Purged rolling walk-forward split.

    Splits ``timestamps`` (must be int64 seconds-since-epoch or np.datetime64)
    into ``n_folds`` non-overlapping test windows ordered chronologically; the
    training portion for each fold is everything BEFORE that test window
    (anchored), with an `embargo_days` gap purged between train and test.

    Returns: list of ``(train_idx, test_idx)`` numpy index arrays.  Any fold
    where the training set is empty is skipped (returned list may have <n_folds).
    """
    ts = _to_epoch_seconds(timestamps)
    n = len(ts)
    if n == 0:
        return []

    order = np.argsort(ts, kind="mergesort")
    ts_sorted = ts[order]
    embargo_sec = int(embargo_days * 86400)

    t_min, t_max = ts_sorted[0], ts_sorted[-1]
    span = t_max - t_min
    if span <= 0:
        return []

    # Test windows are equal-span chronological chunks of the whole range.
    # We anchor boundaries on time, not on index counts, so uneven signal
    # density doesn't make a fold 0.1× the size of another.
    edges = np.linspace(t_min, t_max + 1, n_folds + 1).astype(np.int64)
    folds: List[Tuple[np.ndarray, np.ndarray]] = []
    for f in range(n_folds):
        test_lo, test_hi = edges[f], edges[f + 1]
        test_mask = (ts >= test_lo) & (ts < test_hi)
        if f == 0:
            # Pure walk-forward: first fold has no train set → skip.
            # (Caller gets n_folds-1 usable folds.)
            continue
        train_mask = ts < (test_lo - embargo_sec)
        if train_mask.sum() == 0 or test_mask.sum() == 0:
            continue
        folds.append((np.where(train_mask)[0], np.where(test_mask)[0]))
    return folds


# ----------------------------------------------------------------------------- #
# Filter evaluation                                                             #
# ----------------------------------------------------------------------------- #

def _pf(pnl: np.ndarray) -> float:
    """Aggregate profit factor over a set of trades."""
    if len(pnl) == 0:
        return float("nan")
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / losses)


def _daily_pf_mean(ts: np.ndarray, pnl: np.ndarray) -> float:
    """Average daily PF = mean over days of (wins_day / losses_day).

    CLAUDE.md §0.2 law 4 canonical success metric.  Days with zero losses
    (all wins or all break-even) contribute `inf` to the mean, which would
    poison the average — those days are treated as PF=2.0 by convention
    (caller can also read the median if that matters more).
    """
    if len(pnl) == 0:
        return float("nan")
    ts_sec = _to_epoch_seconds(ts)
    day = (ts_sec // 86400).astype(np.int64)
    uniq = np.unique(day)
    pfs: List[float] = []
    for d in uniq:
        mask = day == d
        w = pnl[mask & (pnl > 0)].sum()
        l = -pnl[mask & (pnl < 0)].sum()
        if l == 0:
            if w > 0:
                pfs.append(2.0)  # convention cap
            # else: day had zero activity → skip
            continue
        pfs.append(float(w / l))
    return float(np.mean(pfs)) if pfs else float("nan")


def evaluate_filter(
    df: Dict[str, np.ndarray],
    filter_mask: np.ndarray,
    n_folds: int = 5,
    embargo_days: int = 1,
) -> Dict:
    """Apply ``filter_mask`` (True = KEEP) across walk-forward folds.

    Returns a dict with aggregated and per-fold statistics.

    Keys:
      folds               : list of per-fold dicts
      n_trades_pre        : total trades before filter (aggregate)
      n_trades_post       : total trades after filter (aggregate)
      PF_pre              : aggregate PF across all test-fold trades, unfiltered
      PF_post             : aggregate PF across all test-fold trades, filtered
      avg_daily_PF_pre    : mean daily PF (unfiltered test)
      avg_daily_PF_post   : mean daily PF (filtered test)
      winrate_pre / _post : fraction pnl>0
      fold_stability      : #folds where PF_post > PF_pre
      fold_stability_str  : '4/5' etc.
      mean_fold_SR        : mean Sharpe ratio of fold-level PF uplift (for DSR)
      std_fold_SR         : std of fold-level PF uplift
    """
    ts = df["ts"]
    pnl = df["pnl_net"]
    folds = walk_forward_split(ts, n_folds=n_folds, embargo_days=embargo_days)

    per_fold: List[Dict] = []
    n_trades_pre_tot = 0
    n_trades_post_tot = 0
    pnl_pre_all: List[np.ndarray] = []
    pnl_post_all: List[np.ndarray] = []
    ts_pre_all: List[np.ndarray] = []
    ts_post_all: List[np.ndarray] = []
    fold_uplifts: List[float] = []
    fold_stability_hits = 0

    for fi, (_tr_idx, te_idx) in enumerate(folds):
        te_pnl = pnl[te_idx]
        te_ts = ts[te_idx]
        te_mask = filter_mask[te_idx]
        pre_pf = _pf(te_pnl)
        post_pnl = te_pnl[te_mask]
        post_ts = te_ts[te_mask]
        post_pf = _pf(post_pnl)
        n_trades_pre_tot += len(te_pnl)
        n_trades_post_tot += len(post_pnl)
        pnl_pre_all.append(te_pnl)
        pnl_post_all.append(post_pnl)
        ts_pre_all.append(te_ts)
        ts_post_all.append(post_ts)

        # Fold uplift = per-trade mean pnl (post) - per-trade mean pnl (pre)
        # Used as input to DSR.
        mu_pre = float(te_pnl.mean()) if len(te_pnl) else 0.0
        mu_post = float(post_pnl.mean()) if len(post_pnl) else 0.0
        uplift = mu_post - mu_pre
        fold_uplifts.append(uplift)

        if (not math.isnan(post_pf)) and (not math.isnan(pre_pf)):
            if post_pf > pre_pf:
                fold_stability_hits += 1

        per_fold.append(
            dict(
                fold=fi,
                n_pre=int(len(te_pnl)),
                n_post=int(len(post_pnl)),
                PF_pre=pre_pf,
                PF_post=post_pf,
                winrate_pre=float((te_pnl > 0).mean()) if len(te_pnl) else float("nan"),
                winrate_post=float((post_pnl > 0).mean()) if len(post_pnl) else float("nan"),
                uplift_mean_pnl=uplift,
            )
        )

    pnl_pre_cat = np.concatenate(pnl_pre_all) if pnl_pre_all else np.array([])
    pnl_post_cat = np.concatenate(pnl_post_all) if pnl_post_all else np.array([])
    ts_pre_cat = np.concatenate(ts_pre_all) if ts_pre_all else np.array([])
    ts_post_cat = np.concatenate(ts_post_all) if ts_post_all else np.array([])

    # Fold-level Sharpe of mean-pnl-uplift across folds (feed to DSR)
    uplift_arr = np.array(fold_uplifts, dtype=float)
    if len(uplift_arr) >= 2 and uplift_arr.std(ddof=1) > 0:
        fold_sr = float(uplift_arr.mean() / uplift_arr.std(ddof=1))
    else:
        fold_sr = 0.0

    # Daily-aggregated Sharpe of the filtered returns.  Using per-trade SR
    # as the DSR input doesn't work here: with thousands of trades (large
    # n_obs), E[max(SR)] under the IID-trade null is O(sqrt(log n_trials))
    # while per-trade SRs in crypto are typically 0.05-0.3, so DSR saturates
    # at 0 trivially.  Bailey & LdP's DSR formulation assumes SR computed
    # from a daily return series (the standard quant convention), where
    # E[max(SR)] is on the same scale as actual daily Sharpes (~1-3).  We
    # therefore aggregate r_multiple to daily PnL, compute Sharpe of the
    # daily-return series, and feed that into DSR along with n_obs=n_days.
    rm = df.get("r_multiple")
    returns = rm if rm is not None and len(rm) else pnl_post_cat
    # post-filter daily aggregation
    rm_post_all = [returns[idx][filter_mask[idx]] for (_tr, idx) in folds]
    ts_post_all_arr = [ts[idx][filter_mask[idx]] for (_tr, idx) in folds]
    rm_cat = np.concatenate(rm_post_all) if rm_post_all else np.array([])
    ts_cat = np.concatenate(ts_post_all_arr) if ts_post_all_arr else np.array([])
    if len(rm_cat) >= 2:
        ts_sec = _to_epoch_seconds(ts_cat)
        day = (ts_sec // 86400).astype(np.int64)
        uniq_days, inv = np.unique(day, return_inverse=True)
        daily_sum = np.zeros(len(uniq_days), dtype=np.float64)
        np.add.at(daily_sum, inv, rm_cat)
        n_days = len(uniq_days)
        if n_days >= 2 and daily_sum.std(ddof=1) > 0:
            trade_sr_post = float(daily_sum.mean() / daily_sum.std(ddof=1))
        else:
            trade_sr_post = 0.0
        n_obs_for_dsr = n_days
    else:
        trade_sr_post = 0.0
        n_obs_for_dsr = 0

    return dict(
        n_folds_used=len(folds),
        n_trades_pre=n_trades_pre_tot,
        n_trades_post=n_trades_post_tot,
        PF_pre=_pf(pnl_pre_cat),
        PF_post=_pf(pnl_post_cat),
        avg_daily_PF_pre=_daily_pf_mean(ts_pre_cat, pnl_pre_cat),
        avg_daily_PF_post=_daily_pf_mean(ts_post_cat, pnl_post_cat),
        winrate_pre=float((pnl_pre_cat > 0).mean()) if len(pnl_pre_cat) else float("nan"),
        winrate_post=float((pnl_post_cat > 0).mean()) if len(pnl_post_cat) else float("nan"),
        fold_stability=fold_stability_hits,
        fold_stability_max=len(folds),
        fold_stability_str=f"{fold_stability_hits}/{len(folds)}",
        fold_mean_uplift_SR=fold_sr,
        trade_level_SR_post=trade_sr_post,    # now daily-aggregated SR
        n_days_for_dsr=n_obs_for_dsr,
        folds=per_fold,
    )


# ----------------------------------------------------------------------------- #
# Deflated Sharpe Ratio (Harvey, Liu, Zhu 2016 / Bailey & LdP 2014)             #
# ----------------------------------------------------------------------------- #

def _std_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def deflated_sharpe(
    raw_sr: float,
    n_trials: int,
    n_obs: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio statistic.

    Args:
      raw_sr   : observed (trade-level) Sharpe.
      n_trials : number of independent configurations tested.
      n_obs    : number of observations (trades) the raw SR was computed over.
      skew     : sample skewness of pnl returns (default 0).
      kurt     : sample excess-kurtosis + 3 of pnl returns (default 3 = normal).

    Returns:
      DSR = Φ( ( (raw_sr - E[max(SR)]) * sqrt(n_obs - 1) )
               / sqrt(1 - skew * raw_sr + ((kurt - 1) / 4) * raw_sr**2) )

    DSR ∈ [0, 1].  DSR > 0.5 is "at the median of the null"; DSR > 0.95 means
    the observed SR beats the multiple-testing-adjusted null at 95%.  The
    CLAUDE.md gate is ``DSR > 0`` — kept as specified, but real robustness
    lives above 0.5.

    Caveat: this function returns the *probability* form (the Φ-applied value),
    which is the canonical DSR per Bailey & LdP.  A raw DSR statistic (the
    Φ-input) can be recovered by its ``stats.norm.ppf`` inverse.
    """
    if n_trials < 1 or n_obs < 2:
        return 0.0
    # E[max(SR)] under the null (Bailey & LdP 2014 eq. 6)
    e_max = (
        (1 - np.euler_gamma) * _inv_std_normal_cdf(1 - 1.0 / n_trials)
        + np.euler_gamma * _inv_std_normal_cdf(1 - 1.0 / (n_trials * math.e))
    )
    num = (raw_sr - e_max) * math.sqrt(max(n_obs - 1, 1))
    denom_sq = 1.0 - skew * raw_sr + ((kurt - 1.0) / 4.0) * raw_sr * raw_sr
    if denom_sq <= 0:
        return 0.0
    denom = math.sqrt(denom_sq)
    z = num / denom
    return _std_normal_cdf(z)


def _inv_std_normal_cdf(p: float) -> float:
    """Acklam's rational approximation; accurate to ~1e-9."""
    if p <= 0 or p >= 1:
        # Numerical guards; caller feeds 1 - 1/n where n>=1, so p ∈ (0, 1).
        return 0.0
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]
    plow  = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2.0 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
             ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# ----------------------------------------------------------------------------- #
# Utility                                                                       #
# ----------------------------------------------------------------------------- #

def _to_epoch_seconds(ts: np.ndarray) -> np.ndarray:
    """Accept ns/ms/s datetime64, float seconds, or int epoch seconds; return int64 s."""
    if ts.dtype.kind == "M":
        return ts.astype("datetime64[s]").astype(np.int64)
    if ts.dtype.kind in ("i", "u"):
        sample = int(ts[0]) if len(ts) else 0
        if sample > 10**12:  # ms
            return (ts // 1000).astype(np.int64)
        return ts.astype(np.int64)
    if ts.dtype.kind == "f":
        # float seconds-since-epoch (e.g. from datetime.timestamp())
        sample = float(ts[0]) if len(ts) else 0.0
        if sample > 1e12:  # ms as float
            return (ts / 1000).astype(np.int64)
        return ts.astype(np.int64)
    # Fallback: pd.Timestamp or python datetime — coerce via numpy
    return np.array([int(np.datetime64(x, "s").astype(np.int64)) for x in ts], dtype=np.int64)
