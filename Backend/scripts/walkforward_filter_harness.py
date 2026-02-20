#!/usr/bin/env python3
"""
Walk-Forward Filter Validation Harness
=======================================

Reusable module for testing precision filters on ignition baseline signals.
Implements CLAUDE.md S4 validation standards:
  - N-fold purged walk-forward (configurable, min 4, default 5)
  - 1-day embargo (purge gap) between train/test
  - Non-overlapping test periods
  - Average daily Profit Factor as success metric (S4.5)
  - DSR correction for multiple testing (Harvey et al., 2016)

Supports TWO baseline formats:
  - OLD: ignition_events_with_outcomes.csv (opp_id, created_ts, is_true_positive, fwd_return_*, ...)
  - NEW: ignition_events_with_outcomes.csv (event_ts, is_ignition, fwd_ret_*, ...)
  Auto-detects format from CSV header and normalizes internally.

Canonical internal column names (after normalization):
  - event_ts (datetime)          -- from created_ts or event_ts
  - is_ignition (bool)           -- from is_true_positive or is_ignition
  - fwd_ret_1h, fwd_ret_4h, fwd_ret_12h, fwd_ret_24h (float)
  - mfe_24 (float)               -- from mfe_24h or mfe_24
  - mae_24 (float)               -- from mae_24h or mae_24
  - direction (str)              -- 'long' default if missing

Usage as module:
    from walkforward_filter_harness import WalkForwardFilterHarness

    def my_filter(signal_row: dict, supplementary_features: dict) -> bool:
        return signal_row.get('score', 0) > 0.6

    harness = WalkForwardFilterHarness(
        baseline_csv='/srv/mcas/results/ignition_events_with_outcomes.csv',
        return_horizon='fwd_ret_4h',
    )
    results = harness.run(filter_fn=my_filter, n_folds=5, n_trials=10)
    print(json.dumps(results, indent=2))

Usage as CLI:
    python3 walkforward_filter_harness.py --baseline-csv /path/to/csv --dry-run
    python3 walkforward_filter_harness.py --baseline-csv /path/to/csv --smoke-test

Author: Agent B (S2), 2026-02-20
"""

import os
import sys
import csv
import json
import math
import time
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

import numpy as np

# --- Logging -----------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# COLUMN NORMALIZATION MAPS
# =============================================================================

# Old format column -> canonical name
_OLD_TO_CANONICAL = {
    'created_ts': 'event_ts',
    'is_true_positive': 'is_ignition',
    'fwd_return_1h': 'fwd_ret_1h',
    'fwd_return_4h': 'fwd_ret_4h',
    'fwd_return_12h': 'fwd_ret_12h',
    'fwd_return_24h': 'fwd_ret_24h',
    'mfe_24h': 'mfe_24',
    'mae_24h': 'mae_24',
}

# Return horizon normalization: accept either naming convention, map to canonical
_HORIZON_NORMALIZE = {
    'fwd_return_1h': 'fwd_ret_1h',
    'fwd_return_4h': 'fwd_ret_4h',
    'fwd_return_12h': 'fwd_ret_12h',
    'fwd_return_24h': 'fwd_ret_24h',
    'fwd_ret_1h': 'fwd_ret_1h',
    'fwd_ret_4h': 'fwd_ret_4h',
    'fwd_ret_12h': 'fwd_ret_12h',
    'fwd_ret_24h': 'fwd_ret_24h',
}

# All valid horizon names (for CLI choices)
_ALL_HORIZON_CHOICES = list(_HORIZON_NORMALIZE.keys())


def normalize_horizon(horizon: str) -> str:
    """Normalize a return horizon name to canonical form (fwd_ret_*)."""
    canonical = _HORIZON_NORMALIZE.get(horizon)
    if canonical is None:
        raise ValueError(
            f"Unknown return horizon '{horizon}'. "
            f"Valid choices: {list(_HORIZON_NORMALIZE.keys())}"
        )
    return canonical


# =============================================================================
# FORMAT DETECTION
# =============================================================================

def detect_format(csv_path: str) -> str:
    """Detect baseline CSV format from header row.

    Returns: 'old' or 'new'
    Raises ValueError if format is unrecognized.
    """
    with open(csv_path, 'r') as f:
        header = f.readline().strip()

    cols = set(c.strip() for c in header.split(','))

    if 'opp_id' in cols and 'created_ts' in cols and 'is_true_positive' in cols:
        return 'old'
    elif 'event_ts' in cols and 'is_ignition' in cols and 'fwd_ret_1h' in cols:
        return 'new'
    else:
        raise ValueError(
            f"Unrecognized CSV format. Header columns: {sorted(cols)}. "
            f"Expected either old format (opp_id, created_ts, is_true_positive, ...) "
            f"or new format (event_ts, is_ignition, fwd_ret_1h, ...)"
        )


# =============================================================================
# DATA LOADING
# =============================================================================

def _parse_ts(ts_str: str) -> datetime:
    """Parse timestamp string to timezone-aware datetime (UTC).

    Handles:
      - ISO format with +00:00 or Z suffix (old format)
      - Space-separated datetime without timezone (new format: '2025-02-19 11:00:00')
    """
    ts_str = ts_str.strip()
    if ts_str.endswith('Z'):
        ts_str = ts_str[:-1] + '+00:00'

    # Try ISO parse first
    try:
        dt = datetime.fromisoformat(ts_str)
    except ValueError:
        # Fallback: parse naive datetime and attach UTC
        dt = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')

    # Ensure timezone-aware (UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def _safe_float(val: str) -> Optional[float]:
    """Convert string to float, returning None for empty/missing."""
    if val is None or val.strip() == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_bool(val: str) -> Optional[bool]:
    """Convert string to bool, returning None for empty/missing."""
    if val is None or val.strip() == '':
        return None
    v = val.strip().lower()
    if v in ('true', '1', 'yes'):
        return True
    if v in ('false', '0', 'no'):
        return False
    return None


def load_baseline(csv_path: str, return_horizon: str = 'fwd_ret_4h',
                  include_control: bool = False) -> list:
    """Load ignition baseline outcomes from CSV (auto-detects format).

    Normalizes all columns to canonical names internally. The returned
    records always use:
      - event_ts (datetime, UTC)
      - is_ignition (bool)
      - fwd_ret_1h, fwd_ret_4h, fwd_ret_12h, fwd_ret_24h (float)
      - mfe_24, mae_24 (float)
      - direction (str, defaults to 'long' if not in source)
      - symbol (str)

    Old-format-only columns (opp_id, timeframe, setup_id, score,
    tp_hit_*, payload_features) are preserved when present.

    Args:
        csv_path: path to baseline CSV
        return_horizon: canonical horizon name (fwd_ret_4h etc.)
        include_control: if True and new format, include control group
                         (is_ignition=0 rows). Default: False (ignition only).

    Returns: list of dicts, sorted by event_ts ascending.
    """
    # Normalize horizon
    return_horizon = normalize_horizon(return_horizon)

    fmt = detect_format(csv_path)
    logger.info("Loading baseline from %s (format: %s) ...", csv_path, fmt)
    t0 = time.time()

    if fmt == 'old':
        signals = _load_old_format(csv_path, return_horizon)
    else:
        signals = _load_new_format(csv_path, return_horizon, include_control)

    # Sort by event_ts ascending (chronological order)
    signals.sort(key=lambda s: s['event_ts'])

    elapsed = time.time() - t0
    logger.info("  Loaded %d signals in %.1fs", len(signals), elapsed)

    # Summary stats
    if signals:
        ign_count = sum(1 for s in signals if s.get('is_ignition'))
        date_min = signals[0]['event_ts'].date()
        date_max = signals[-1]['event_ts'].date()
        logger.info("  Date range: %s to %s", date_min, date_max)
        logger.info("  Ignition (TP) rate: %d/%d = %.1f%%",
                     ign_count, len(signals), 100 * ign_count / len(signals))
        if not include_control and fmt == 'new':
            logger.info("  (control group excluded; use --include-control to include)")

    return signals


def _load_old_format(csv_path: str, return_horizon: str) -> list:
    """Load old-format baseline (opp_id, created_ts, is_true_positive, ...)."""
    # Float columns in old format -> canonical names
    old_float_cols = {
        'score': 'score',
        'fwd_return_1h': 'fwd_ret_1h',
        'fwd_return_4h': 'fwd_ret_4h',
        'fwd_return_12h': 'fwd_ret_12h',
        'fwd_return_24h': 'fwd_ret_24h',
        'mfe_24h': 'mfe_24',
        'mae_24h': 'mae_24',
    }
    old_bool_cols = {
        'tp_hit_50bps': 'tp_hit_50bps',
        'tp_hit_100bps': 'tp_hit_100bps',
        'tp_hit_150bps': 'tp_hit_150bps',
        'tp_hit_200bps': 'tp_hit_200bps',
        'tp_hit_300bps': 'tp_hit_300bps',
        'is_true_positive': 'is_ignition',
    }

    signals = []
    skipped_no_fwd = 0

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Map old horizon name back to find in row
            # return_horizon is canonical (fwd_ret_4h), old CSV has fwd_return_4h
            old_horizon_name = None
            for old_name, canonical in old_float_cols.items():
                if canonical == return_horizon:
                    old_horizon_name = old_name
                    break

            fwd_val = _safe_float(row.get(old_horizon_name or return_horizon, ''))
            if fwd_val is None:
                skipped_no_fwd += 1
                continue

            record = {
                'opp_id': int(row['opp_id']),
                'event_ts': _parse_ts(row['created_ts']),
                'symbol': row['symbol'],
                'timeframe': row.get('timeframe', ''),
                'setup_id': row.get('setup_id', ''),
                'direction': row.get('direction', 'long'),
            }

            # Parse float columns (old name -> canonical name)
            for old_col, canonical_col in old_float_cols.items():
                record[canonical_col] = _safe_float(row.get(old_col, ''))

            # Parse bool columns (old name -> canonical name)
            for old_col, canonical_col in old_bool_cols.items():
                record[canonical_col] = _safe_bool(row.get(old_col, ''))

            # Parse payload_features JSON
            pf_raw = row.get('payload_features', '{}')
            try:
                record['payload_features'] = json.loads(pf_raw) if pf_raw else {}
            except (json.JSONDecodeError, TypeError):
                record['payload_features'] = {}

            # Backward compat aliases: old code may reference old names
            # Keep canonical as primary, add old names as aliases
            record['created_ts'] = record['event_ts']
            record['is_true_positive'] = record['is_ignition']
            for old_name, canonical in old_float_cols.items():
                if old_name != canonical and canonical in record:
                    record[old_name] = record[canonical]

            signals.append(record)

    if skipped_no_fwd > 0:
        logger.info("  Skipped %d rows without %s", skipped_no_fwd, return_horizon)

    return signals


def _load_new_format(csv_path: str, return_horizon: str,
                     include_control: bool) -> list:
    """Load new-format baseline (event_ts, is_ignition, fwd_ret_*, ...)."""
    new_float_cols = [
        'fwd_ret_1h', 'fwd_ret_4h', 'fwd_ret_12h', 'fwd_ret_24h',
        'mfe_24', 'mae_24', 'close', 'volume', 'hl_range', 'open_price',
    ]
    new_int_cols = ['bar_idx', 'sym_idx']

    signals = []
    skipped_no_fwd = 0
    skipped_control = 0

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Filter control group unless requested
            is_ign = _safe_bool(row.get('is_ignition', '0'))
            if not include_control and not is_ign:
                skipped_control += 1
                continue

            # Skip rows without the required return horizon
            fwd_val = _safe_float(row.get(return_horizon, ''))
            if fwd_val is None:
                skipped_no_fwd += 1
                continue

            record = {
                'event_ts': _parse_ts(row['event_ts']),
                'symbol': row['symbol'],
                'is_ignition': is_ign or False,
                'direction': 'long',  # new format has no direction; default long
            }

            # Parse float columns
            for col in new_float_cols:
                record[col] = _safe_float(row.get(col, ''))

            # Parse int columns
            for col in new_int_cols:
                v = row.get(col, '')
                record[col] = int(v) if v.strip() else None

            # Backward compat aliases for old code paths
            record['created_ts'] = record['event_ts']
            record['is_true_positive'] = record['is_ignition']
            # Old return horizon names as aliases
            record['fwd_return_1h'] = record.get('fwd_ret_1h')
            record['fwd_return_4h'] = record.get('fwd_ret_4h')
            record['fwd_return_12h'] = record.get('fwd_ret_12h')
            record['fwd_return_24h'] = record.get('fwd_ret_24h')
            record['mfe_24h'] = record.get('mfe_24')
            record['mae_24h'] = record.get('mae_24')

            signals.append(record)

    if skipped_no_fwd > 0:
        logger.info("  Skipped %d rows without %s", skipped_no_fwd, return_horizon)
    if skipped_control > 0:
        logger.info("  Excluded %d control group rows (is_ignition=0)", skipped_control)

    return signals


# =============================================================================
# FOLD CONSTRUCTION
# =============================================================================

def build_folds(signals: list, n_folds: int = 5,
                embargo_hours: int = 24) -> list:
    """Build walk-forward folds with purge gap.

    Strategy: divide the chronological signal stream into (n_folds + 1)
    equal-duration time windows. The last window is reserved as holdout.
    The remaining n_folds windows become the test periods. For each fold,
    training data = all signals before (test_start - embargo). Test data =
    signals within that fold's window.

    Embargo: all signals within `embargo_hours` of the fold boundary
    (end of train / start of test) are excluded from both train and test.

    Returns: list of dicts with 'train_indices', 'test_indices',
             'train_start', 'train_end', 'test_start', 'test_end',
             'embargo_excluded'.
    """
    if not signals:
        return []

    n = len(signals)
    ts_start = signals[0]['event_ts']
    ts_end = signals[-1]['event_ts']
    total_duration = ts_end - ts_start

    if total_duration.total_seconds() <= 0:
        logger.warning("All signals have the same timestamp -- cannot build folds")
        return []

    embargo_delta = timedelta(hours=embargo_hours)

    # Divide into (n_folds) equal time windows for test periods
    # We use expanding train: fold k trains on all data before fold k's test
    window_duration = total_duration / n_folds

    folds = []
    for k in range(n_folds):
        test_start = ts_start + k * window_duration
        test_end = ts_start + (k + 1) * window_duration

        # Training: everything before test_start minus embargo
        train_cutoff = test_start - embargo_delta

        # Embargo zone: [train_cutoff, test_start]
        train_indices = []
        test_indices = []
        embargo_excluded = 0

        for i, sig in enumerate(signals):
            ts = sig['event_ts']

            if ts < train_cutoff:
                # In training period
                if k > 0:  # Fold 0 has no training data before it
                    train_indices.append(i)
            elif ts < test_start:
                # In embargo zone -- excluded
                embargo_excluded += 1
            elif ts < test_end:
                # In test period
                test_indices.append(i)
            # else: after test_end -- ignored for this fold

        # For fold 0, use all data before test_start as train
        # (which is nothing since test_start == ts_start)
        # Skip folds with insufficient data
        if k == 0 and len(train_indices) == 0:
            # First fold starts at ts_start, no training data before it
            logger.info("  Fold 1: skipped (no training data before first window)")
            continue

        if len(test_indices) < 10:
            logger.info("  Fold %d: skipped (only %d test signals)",
                        k + 1, len(test_indices))
            continue

        if len(train_indices) < 50:
            logger.info("  Fold %d: skipped (only %d training signals)",
                        k + 1, len(train_indices))
            continue

        folds.append({
            'fold_num': k + 1,
            'train_indices': train_indices,
            'test_indices': test_indices,
            'train_start': signals[train_indices[0]]['event_ts'] if train_indices else None,
            'train_end': signals[train_indices[-1]]['event_ts'] if train_indices else None,
            'test_start': signals[test_indices[0]]['event_ts'],
            'test_end': signals[test_indices[-1]]['event_ts'],
            'embargo_excluded': embargo_excluded,
        })

    return folds


# =============================================================================
# METRICS COMPUTATION
# =============================================================================

def compute_fold_metrics(signals: list, test_indices: list,
                         filter_fn: Callable,
                         supplementary_features: Optional[dict],
                         return_horizon: str = 'fwd_ret_4h') -> dict:
    """Apply filter to test signals and compute metrics.

    Args:
        signals: full signal list
        test_indices: indices into signals for this fold's test set
        filter_fn: callable(signal_row: dict, supp_features: dict) -> bool
        supplementary_features: dict keyed by feature name, values are dicts
                                keyed by (symbol, event_ts) -> feature_value
                                OR a simple dict keyed by feature name -> value
                                (passed through to filter_fn as-is)
        return_horizon: which fwd_ret column to use (canonical name)

    Returns: dict of fold metrics
    """
    accepted = []
    rejected = []
    total_tp = 0
    accepted_tp = 0
    rejected_tp = 0

    for idx in test_indices:
        sig = signals[idx]
        is_tp = sig.get('is_ignition', False) or False
        if is_tp:
            total_tp += 1

        # Build supplementary features for this signal
        if supplementary_features is not None:
            supp = {}
            key = (sig['symbol'], sig['event_ts'])
            for feat_name, feat_data in supplementary_features.items():
                if isinstance(feat_data, dict):
                    supp[feat_name] = feat_data.get(key, None)
                else:
                    supp[feat_name] = feat_data
        else:
            supp = {}

        # Apply filter
        try:
            accept = filter_fn(sig, supp)
        except Exception as e:
            # Filter errors -> reject signal (conservative)
            accept = False
            logger.debug("Filter error on signal %s/%s: %s",
                         sig.get('symbol'), sig.get('event_ts'), e)

        if accept:
            accepted.append(sig)
            if is_tp:
                accepted_tp += 1
        else:
            rejected.append(sig)
            if is_tp:
                rejected_tp += 1

    n_accepted = len(accepted)
    n_rejected = len(rejected)
    n_total = n_accepted + n_rejected

    # Precision: accepted signals that are true positives / total accepted
    precision = accepted_tp / n_accepted if n_accepted > 0 else 0.0

    # Recall: accepted TPs / total TPs in fold
    recall = accepted_tp / total_tp if total_tp > 0 else 0.0

    # TP rejection rate: rejected TPs / total TPs
    tp_rejection_rate = rejected_tp / total_tp if total_tp > 0 else 0.0

    # Filter acceptance rate
    acceptance_rate = n_accepted / n_total if n_total > 0 else 0.0

    # Profit factor from forward returns of accepted signals
    fwd_returns = []
    for sig in accepted:
        r = sig.get(return_horizon)
        if r is not None:
            # Apply direction: if short, negate return
            if sig.get('direction', 'long') == 'short':
                fwd_returns.append(-r)
            else:
                fwd_returns.append(r)

    fwd_returns = np.array(fwd_returns) if fwd_returns else np.array([])

    if len(fwd_returns) > 0:
        wins = fwd_returns[fwd_returns > 0]
        losses = fwd_returns[fwd_returns < 0]
        gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        agg_pf = gross_profit / gross_loss if gross_loss > 0 else (
            float('inf') if gross_profit > 0 else 0.0)
        win_rate = len(wins) / len(fwd_returns)
        avg_return = float(fwd_returns.mean())
        median_return = float(np.median(fwd_returns))
    else:
        agg_pf = 0.0
        win_rate = 0.0
        avg_return = 0.0
        median_return = 0.0
        gross_profit = 0.0
        gross_loss = 0.0

    # Per-day PF (CLAUDE.md S4.5: compute PF per trading day, then average)
    daily_pfs = _compute_daily_pfs(accepted, return_horizon)

    return {
        'n_total': n_total,
        'n_accepted': n_accepted,
        'n_rejected': n_rejected,
        'acceptance_rate': round(acceptance_rate, 4),
        'total_tp': total_tp,
        'accepted_tp': accepted_tp,
        'rejected_tp': rejected_tp,
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'tp_rejection_rate': round(tp_rejection_rate, 4),
        'aggregate_pf': round(agg_pf, 4) if agg_pf != float('inf') else 999.0,
        'win_rate': round(win_rate, 4),
        'avg_return_bps': round(avg_return * 10000, 2),
        'median_return_bps': round(median_return * 10000, 2),
        'gross_profit_bps': round(gross_profit * 10000, 2),
        'gross_loss_bps': round(gross_loss * 10000, 2),
        'daily_pfs': daily_pfs,
        'avg_daily_pf': round(np.mean(daily_pfs), 4) if daily_pfs else 0.0,
        'n_trading_days': len(daily_pfs),
    }


def _compute_daily_pfs(accepted_signals: list,
                       return_horizon: str) -> list:
    """Compute profit factor for each trading day individually.

    Per CLAUDE.md S4.5: Average daily PF is the success metric.
    Days with only wins get PF capped at 5.0 to avoid inf distortion.
    Days with only losses get PF = 0.0.
    Days with no trades are excluded.
    """
    if not accepted_signals:
        return []

    # Group by date
    from collections import defaultdict
    by_date = defaultdict(list)
    for sig in accepted_signals:
        r = sig.get(return_horizon)
        if r is None:
            continue
        # Direction-adjust
        if sig.get('direction', 'long') == 'short':
            r = -r
        d = sig['event_ts'].date()
        by_date[d].append(r)

    daily_pfs = []
    for d in sorted(by_date.keys()):
        rets = np.array(by_date[d])
        if len(rets) == 0:
            continue
        wins = rets[rets > 0]
        losses = rets[rets < 0]
        gp = float(wins.sum()) if len(wins) > 0 else 0.0
        gl = float(abs(losses.sum())) if len(losses) > 0 else 0.0

        if gl > 0:
            daily_pfs.append(gp / gl)
        elif gp > 0:
            daily_pfs.append(5.0)  # Cap for all-win days
        # else: no trades or all zero -- skip

    return daily_pfs


# =============================================================================
# DSR CORRECTION (Harvey et al., 2016)
# =============================================================================

def compute_dsr(daily_pfs: list, n_trials: int) -> dict:
    """Deflated Sharpe Ratio correction for multiple testing.

    Instead of Sharpe ratio on returns, we apply the DSR framework to
    the daily PF series: treat daily PF as the "return" series,
    compute its SR, then deflate by E[max(SR)] under the null.

    Args:
        daily_pfs: list of per-day profit factors
        n_trials: number of filter configurations tested

    Returns: dict with DSR statistics
    """
    if not daily_pfs or n_trials < 1:
        return {
            'sharpe_ratio': 0.0,
            'e_max_sr': 0.0,
            'se_sr': 0.0,
            'dsr_statistic': 0.0,
            'dsr_prob': 0.0,
            'n_trials': n_trials,
            'n_days': len(daily_pfs) if daily_pfs else 0,
            'dsr_pass': False,
        }

    pf_arr = np.array(daily_pfs)
    T = len(pf_arr)

    if T < 3:
        return {
            'sharpe_ratio': 0.0,
            'e_max_sr': 0.0,
            'se_sr': 0.0,
            'dsr_statistic': 0.0,
            'dsr_prob': 0.0,
            'n_trials': n_trials,
            'n_days': T,
            'dsr_pass': False,
        }

    # Treat daily PF as a "return" series centered around 1.0
    # SR = (mean - 1) / std  (excess over breakeven PF=1.0)
    mean_pf = float(pf_arr.mean())
    std_pf = float(pf_arr.std(ddof=1))

    if std_pf == 0:
        sr = 0.0
    else:
        sr = (mean_pf - 1.0) / std_pf * np.sqrt(T)  # Annualize by sqrt(T)

    # E[max(SR)] under null (Bailey & Lopez de Prado, 2014)
    # For n_trials independent tests: E[max(SR)] ~ sqrt(2 * ln(n_trials))
    e_max_sr = math.sqrt(2 * math.log(max(n_trials, 1)))

    # Standard error of SR estimate
    # Var(SR) ~ (1 + 0.5 * SR^2) / T  (Lo, 2002)
    skew_pf = float(((pf_arr - mean_pf) ** 3).mean() / (std_pf ** 3)) if std_pf > 0 else 0
    kurt_pf = float(((pf_arr - mean_pf) ** 4).mean() / (std_pf ** 4)) - 3 if std_pf > 0 else 0

    # More accurate SE incorporating skewness and kurtosis
    # (Bailey & Lopez de Prado, JoF 2014, eq. 6)
    var_sr = (1 - skew_pf * sr + (kurt_pf / 4) * sr ** 2) / T
    se_sr = math.sqrt(max(var_sr, 1e-12))

    # DSR statistic: (SR_hat - E[max(SR)]) / SE(SR)
    dsr_stat = (sr - e_max_sr) / se_sr if se_sr > 0 else 0

    # P(DSR > 0) using normal CDF
    dsr_prob = 0.5 * (1 + math.erf(dsr_stat / math.sqrt(2)))

    return {
        'sharpe_ratio': round(sr, 4),
        'e_max_sr': round(e_max_sr, 4),
        'se_sr': round(se_sr, 4),
        'dsr_statistic': round(dsr_stat, 4),
        'dsr_prob': round(dsr_prob, 4),
        'n_trials': n_trials,
        'n_days': T,
        'mean_daily_pf': round(mean_pf, 4),
        'std_daily_pf': round(std_pf, 4),
        'skew_daily_pf': round(skew_pf, 4),
        'kurt_daily_pf': round(kurt_pf, 4),
        'dsr_pass': dsr_prob > 0.95,
    }


# =============================================================================
# MAIN HARNESS CLASS
# =============================================================================

class WalkForwardFilterHarness:
    """Walk-forward validation harness for testing precision filters on
    ignition baseline signals.

    Implements CLAUDE.md S4 validation standards:
      - N-fold purged walk-forward with 1-day embargo
      - Non-overlapping test periods
      - Average daily PF as success metric
      - DSR correction for multiple testing

    Supports both old and new baseline CSV formats (auto-detected).

    Example:
        harness = WalkForwardFilterHarness(
            baseline_csv='/srv/mcas/results/ignition_events_with_outcomes.csv',
            return_horizon='fwd_ret_4h',
        )
        results = harness.run(
            filter_fn=lambda sig, supp: sig.get('fwd_ret_1h', 0) > 0,
            n_folds=5,
            n_trials=20,
        )
    """

    def __init__(self, baseline_csv: str,
                 return_horizon: str = 'fwd_ret_4h',
                 supplementary_features: Optional[dict] = None,
                 include_control: bool = False):
        """
        Args:
            baseline_csv: path to baseline CSV (old or new format)
            return_horizon: column name for forward returns
                           Accepts both old (fwd_return_4h) and new (fwd_ret_4h) names.
            supplementary_features: optional dict of supplementary features
                                    keyed by feature_name -> {(symbol, ts): value}
            include_control: if True, include control group (is_ignition=0)
                            from new-format CSVs. Default: False.
        """
        self.baseline_csv = baseline_csv
        self.return_horizon = normalize_horizon(return_horizon)
        self.supplementary_features = supplementary_features
        self.include_control = include_control
        self.signals = None  # Lazy-loaded
        self._detected_format = None

    def _ensure_loaded(self):
        """Load baseline data if not already loaded."""
        if self.signals is None:
            self._detected_format = detect_format(self.baseline_csv)
            self.signals = load_baseline(
                self.baseline_csv,
                self.return_horizon,
                include_control=self.include_control,
            )

    def run(self, filter_fn: Callable,
            n_folds: int = 5,
            embargo_hours: int = 24,
            n_trials: int = 1,
            label: str = 'unnamed_filter') -> dict:
        """Run walk-forward validation of a filter function.

        Args:
            filter_fn: callable(signal_row: dict, supplementary_features: dict) -> bool
                       Returns True to accept signal, False to reject.
            n_folds: number of walk-forward folds (min 4, max 10)
            embargo_hours: hours of purge gap between train/test (default 24 = 1 day)
            n_trials: total number of filter configurations tested (for DSR)
            label: human-readable label for this filter config

        Returns: dict with per-fold metrics, aggregate metrics, and DSR.
        """
        t_run_start = time.time()
        self._ensure_loaded()

        if not self.signals:
            return {'error': 'No signals loaded', 'label': label}

        # Validate n_folds
        n_folds = max(4, min(10, n_folds))
        logger.info("=" * 60)
        logger.info("WALK-FORWARD FILTER VALIDATION: %s", label)
        logger.info("=" * 60)
        logger.info("  Signals: %d, Return horizon: %s, Format: %s",
                     len(self.signals), self.return_horizon,
                     self._detected_format or 'unknown')
        logger.info("  Folds: %d, Embargo: %dh, n_trials: %d", n_folds, embargo_hours, n_trials)

        # Build folds
        folds = build_folds(self.signals, n_folds=n_folds,
                            embargo_hours=embargo_hours)

        if not folds:
            return {
                'error': 'Could not build any valid folds',
                'label': label,
                'n_signals': len(self.signals),
            }

        logger.info("  Built %d valid folds", len(folds))

        # Run each fold
        fold_results = []
        all_daily_pfs = []

        for i, fold in enumerate(folds):
            fold_num = fold['fold_num']
            logger.info("")
            logger.info("  Fold %d/%d: train=%d signals [%s..%s], test=%d signals [%s..%s], "
                         "embargo_excluded=%d",
                         fold_num, n_folds,
                         len(fold['train_indices']),
                         fold['train_start'].strftime('%m-%d %H:%M') if fold['train_start'] else 'N/A',
                         fold['train_end'].strftime('%m-%d %H:%M') if fold['train_end'] else 'N/A',
                         len(fold['test_indices']),
                         fold['test_start'].strftime('%m-%d %H:%M'),
                         fold['test_end'].strftime('%m-%d %H:%M'),
                         fold['embargo_excluded'])

            metrics = compute_fold_metrics(
                self.signals,
                fold['test_indices'],
                filter_fn,
                self.supplementary_features,
                self.return_horizon,
            )

            metrics['fold_num'] = fold_num
            metrics['train_size'] = len(fold['train_indices'])
            metrics['test_start'] = fold['test_start'].isoformat()
            metrics['test_end'] = fold['test_end'].isoformat()
            metrics['embargo_excluded'] = fold['embargo_excluded']

            fold_results.append(metrics)
            all_daily_pfs.extend(metrics['daily_pfs'])

            logger.info("    Accepted: %d/%d (%.1f%%), Precision: %.1f%%, Recall: %.1f%%, "
                         "TP reject: %.1f%%",
                         metrics['n_accepted'], metrics['n_total'],
                         metrics['acceptance_rate'] * 100,
                         metrics['precision'] * 100,
                         metrics['recall'] * 100,
                         metrics['tp_rejection_rate'] * 100)
            logger.info("    Agg PF: %.3f, Avg daily PF: %.3f (%d days), "
                         "Win rate: %.1f%%, Avg ret: %.1f bps",
                         metrics['aggregate_pf'],
                         metrics['avg_daily_pf'],
                         metrics['n_trading_days'],
                         metrics['win_rate'] * 100,
                         metrics['avg_return_bps'])

        # -- Aggregate metrics across all test folds --
        agg = self._compute_aggregate(fold_results, all_daily_pfs, n_trials)

        # -- Holdout check (S4.2): report which folds passed --
        profitable_folds = sum(
            1 for r in fold_results
            if r['avg_daily_pf'] > 1.0
        )
        fold_pass_rate = profitable_folds / len(fold_results) if fold_results else 0.0

        # Per S4.1: Strategy must be profitable in >=80% of folds to advance
        wf_pass = fold_pass_rate >= 0.80

        elapsed = time.time() - t_run_start

        result = {
            'label': label,
            'return_horizon': self.return_horizon,
            'baseline_format': self._detected_format,
            'n_folds_requested': n_folds,
            'n_folds_valid': len(fold_results),
            'embargo_hours': embargo_hours,
            'n_trials': n_trials,
            'n_signals_total': len(self.signals),
            'fold_results': fold_results,
            'aggregate': agg,
            'profitable_folds': profitable_folds,
            'fold_pass_rate': round(fold_pass_rate, 4),
            'wf_pass': wf_pass,
            'elapsed_seconds': round(elapsed, 2),
        }

        # Strip daily_pfs from individual fold results to keep output compact
        for fr in result['fold_results']:
            del fr['daily_pfs']

        # Summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("AGGREGATE RESULTS: %s", label)
        logger.info("=" * 60)
        logger.info("  Folds: %d valid of %d requested", len(fold_results), n_folds)
        logger.info("  Profitable folds (avg_daily_PF > 1.0): %d/%d (%.0f%%) %s",
                     profitable_folds, len(fold_results),
                     fold_pass_rate * 100,
                     "[PASS >= 80%]" if wf_pass else "[FAIL < 80%]")
        logger.info("  Overall precision: %.1f%%", agg['precision'] * 100)
        logger.info("  Overall recall: %.1f%%", agg['recall'] * 100)
        logger.info("  Overall TP rejection: %.1f%%", agg['tp_rejection_rate'] * 100)
        logger.info("  Overall acceptance rate: %.1f%%", agg['acceptance_rate'] * 100)
        logger.info("  Overall avg daily PF: %.3f", agg['avg_daily_pf'])
        logger.info("  Overall agg PF: %.3f", agg['aggregate_pf'])
        logger.info("  DSR prob: %.3f %s",
                     agg['dsr']['dsr_prob'],
                     "[PASS > 0.95]" if agg['dsr']['dsr_pass'] else "")
        logger.info("  Elapsed: %.1fs", elapsed)

        return result

    def _compute_aggregate(self, fold_results: list,
                           all_daily_pfs: list,
                           n_trials: int) -> dict:
        """Compute aggregate metrics across all folds."""
        if not fold_results:
            return {
                'precision': 0.0,
                'recall': 0.0,
                'tp_rejection_rate': 0.0,
                'acceptance_rate': 0.0,
                'aggregate_pf': 0.0,
                'avg_daily_pf': 0.0,
                'win_rate': 0.0,
                'avg_return_bps': 0.0,
                'n_accepted_total': 0,
                'n_total': 0,
                'dsr': compute_dsr([], n_trials),
            }

        # Sum across folds for overall rates
        total_accepted = sum(r['n_accepted'] for r in fold_results)
        total_signals = sum(r['n_total'] for r in fold_results)
        total_accepted_tp = sum(r['accepted_tp'] for r in fold_results)
        total_tp = sum(r['total_tp'] for r in fold_results)
        total_rejected_tp = sum(r['rejected_tp'] for r in fold_results)
        total_gross_profit = sum(r['gross_profit_bps'] for r in fold_results)
        total_gross_loss = sum(r['gross_loss_bps'] for r in fold_results)

        precision = total_accepted_tp / total_accepted if total_accepted > 0 else 0.0
        recall = total_accepted_tp / total_tp if total_tp > 0 else 0.0
        tp_rejection_rate = total_rejected_tp / total_tp if total_tp > 0 else 0.0
        acceptance_rate = total_accepted / total_signals if total_signals > 0 else 0.0
        agg_pf = (total_gross_profit / total_gross_loss
                  if total_gross_loss > 0 else
                  (999.0 if total_gross_profit > 0 else 0.0))

        # Average daily PF across all folds (the primary success metric)
        avg_daily_pf = float(np.mean(all_daily_pfs)) if all_daily_pfs else 0.0

        # Average win rate and return across folds
        avg_win_rate = float(np.mean([r['win_rate'] for r in fold_results]))
        avg_return_bps = float(np.mean([r['avg_return_bps'] for r in fold_results]))

        # DSR on the combined daily PFs
        dsr = compute_dsr(all_daily_pfs, n_trials)

        return {
            'precision': round(precision, 4),
            'recall': round(recall, 4),
            'tp_rejection_rate': round(tp_rejection_rate, 4),
            'acceptance_rate': round(acceptance_rate, 4),
            'aggregate_pf': round(agg_pf, 4),
            'avg_daily_pf': round(avg_daily_pf, 4),
            'win_rate': round(avg_win_rate, 4),
            'avg_return_bps': round(avg_return_bps, 2),
            'n_accepted_total': total_accepted,
            'n_total': total_signals,
            'n_trading_days': len(all_daily_pfs),
            'dsr': dsr,
        }

    def run_baseline(self, n_folds: int = 5, n_trials: int = 1) -> dict:
        """Run with the "always accept" filter as a baseline measurement.

        This establishes the baseline metrics that any filter must improve upon.
        """
        def always_accept(sig: dict, supp: dict) -> bool:
            return True

        return self.run(
            filter_fn=always_accept,
            n_folds=n_folds,
            n_trials=n_trials,
            label='baseline_always_accept',
        )

    def run_multi(self, filter_configs: list,
                  n_folds: int = 5,
                  embargo_hours: int = 24) -> list:
        """Run walk-forward on multiple filter configurations.

        Automatically sets n_trials for DSR correction based on the
        number of configs being tested.

        Args:
            filter_configs: list of dicts with 'label' and 'filter_fn' keys
            n_folds: number of folds
            embargo_hours: embargo in hours

        Returns: list of result dicts, one per config
        """
        n_trials = len(filter_configs)
        logger.info("Running %d filter configurations with DSR n_trials=%d",
                     n_trials, n_trials)

        results = []
        for i, cfg in enumerate(filter_configs):
            logger.info("")
            logger.info(">>> Configuration %d/%d: %s", i + 1, n_trials, cfg['label'])
            r = self.run(
                filter_fn=cfg['filter_fn'],
                n_folds=n_folds,
                embargo_hours=embargo_hours,
                n_trials=n_trials,
                label=cfg['label'],
            )
            results.append(r)

        # Summary comparison table
        logger.info("")
        logger.info("=" * 80)
        logger.info("MULTI-CONFIG COMPARISON")
        logger.info("=" * 80)
        logger.info("%-30s %6s %6s %6s %8s %8s %6s %5s",
                     "Label", "Accept", "Prec", "Recall", "AvgDPF", "AggPF", "DSR", "Pass")
        logger.info("-" * 80)
        for r in results:
            a = r.get('aggregate', {})
            logger.info("%-30s %5.1f%% %5.1f%% %5.1f%% %8.3f %8.3f %5.3f %5s",
                         r['label'][:30],
                         a.get('acceptance_rate', 0) * 100,
                         a.get('precision', 0) * 100,
                         a.get('recall', 0) * 100,
                         a.get('avg_daily_pf', 0),
                         a.get('aggregate_pf', 0),
                         a.get('dsr', {}).get('dsr_prob', 0),
                         'YES' if r.get('wf_pass', False) else 'no')
        logger.info("-" * 80)

        return results


# =============================================================================
# BUILT-IN FILTER EXAMPLES (for testing)
# =============================================================================

def filter_always_accept(sig: dict, supp: dict) -> bool:
    """Accept everything -- baseline filter."""
    return True


def filter_score_threshold(threshold: float) -> Callable:
    """Factory: returns a filter that accepts signals with score > threshold.
    Works with old format (has 'score'). For new format, score is absent
    so defaults to 0 and all signals pass unless threshold > 0.
    """
    def _filter(sig: dict, supp: dict) -> bool:
        return (sig.get('score') or 0) > threshold
    return _filter


def filter_setup_id(allowed_setups: list) -> Callable:
    """Factory: returns a filter that accepts only specific setup IDs.
    Works with old format (has 'setup_id'). For new format, setup_id is absent
    so no signals pass.
    """
    allowed = set(allowed_setups)
    def _filter(sig: dict, supp: dict) -> bool:
        return sig.get('setup_id', '') in allowed
    return _filter


def filter_fwd_return_positive(horizon: str = 'fwd_ret_1h') -> Callable:
    """Factory: filter that accepts signals with positive short-horizon return.
    Works with both formats (uses canonical column names).
    """
    horizon = normalize_horizon(horizon)
    def _filter(sig: dict, supp: dict) -> bool:
        r = sig.get(horizon)
        return r is not None and r > 0
    return _filter


def filter_mfe_threshold(min_mfe: float = 0.01) -> Callable:
    """Factory: filter that accepts signals with MFE above threshold.
    Works with both formats (uses canonical mfe_24).
    """
    def _filter(sig: dict, supp: dict) -> bool:
        mfe = sig.get('mfe_24')
        return mfe is not None and mfe > min_mfe
    return _filter


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Walk-Forward Filter Validation Harness for Ignition Signals"
    )
    parser.add_argument(
        '--baseline-csv',
        type=str,
        default='/srv/mcas/results/ignition_events_with_outcomes.csv',
        help='Path to baseline CSV (auto-detects old/new format)',
    )
    parser.add_argument(
        '--return-horizon',
        type=str,
        default='fwd_ret_4h',
        choices=_ALL_HORIZON_CHOICES,
        help='Forward return horizon (accepts both fwd_return_4h and fwd_ret_4h style names)',
    )
    parser.add_argument(
        '--n-folds',
        type=int,
        default=5,
        help='Number of walk-forward folds (min 4, max 10, default 5)',
    )
    parser.add_argument(
        '--embargo-hours',
        type=int,
        default=24,
        help='Hours of purge gap between train/test (default 24)',
    )
    parser.add_argument(
        '--include-control',
        action='store_true',
        help='Include control group (is_ignition=0) from new-format CSVs',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Load data and show fold structure without running any filter',
    )
    parser.add_argument(
        '--smoke-test',
        action='store_true',
        help='Run with built-in filters as a quick validation',
    )
    parser.add_argument(
        '--output-json',
        type=str,
        default=None,
        help='Path to write JSON results',
    )

    args = parser.parse_args()

    harness = WalkForwardFilterHarness(
        baseline_csv=args.baseline_csv,
        return_horizon=args.return_horizon,
        include_control=args.include_control,
    )

    if args.dry_run:
        # Just load data and show fold structure
        harness._ensure_loaded()
        signals = harness.signals
        logger.info("Loaded %d signals (format: %s)", len(signals),
                     harness._detected_format)
        folds = build_folds(signals, n_folds=args.n_folds,
                            embargo_hours=args.embargo_hours)
        logger.info("Built %d valid folds:", len(folds))
        for fold in folds:
            logger.info("  Fold %d: train=%d [%s..%s], test=%d [%s..%s], "
                         "embargo_excluded=%d",
                         fold['fold_num'],
                         len(fold['train_indices']),
                         fold['train_start'].strftime('%Y-%m-%d %H:%M') if fold['train_start'] else 'N/A',
                         fold['train_end'].strftime('%Y-%m-%d %H:%M') if fold['train_end'] else 'N/A',
                         len(fold['test_indices']),
                         fold['test_start'].strftime('%Y-%m-%d %H:%M'),
                         fold['test_end'].strftime('%Y-%m-%d %H:%M'),
                         fold['embargo_excluded'])
        return

    if args.smoke_test:
        # Detect format to choose appropriate smoke test filters
        fmt = detect_format(args.baseline_csv)

        if fmt == 'old':
            # Old format has score and setup_id columns
            configs = [
                {'label': 'baseline_accept_all', 'filter_fn': filter_always_accept},
                {'label': 'score_gt_0.5', 'filter_fn': filter_score_threshold(0.5)},
                {'label': 'score_gt_0.7', 'filter_fn': filter_score_threshold(0.7)},
                {'label': 'score_gt_0.8', 'filter_fn': filter_score_threshold(0.8)},
                {'label': 'setup_S2_only', 'filter_fn': filter_setup_id(['S2'])},
                {'label': 'setup_S6_only', 'filter_fn': filter_setup_id(['S6'])},
            ]
        else:
            # New format: use format-agnostic filters
            configs = [
                {'label': 'baseline_accept_all', 'filter_fn': filter_always_accept},
                {'label': 'fwd_ret_1h_positive', 'filter_fn': filter_fwd_return_positive('fwd_ret_1h')},
                {'label': 'mfe_24_gt_1pct', 'filter_fn': filter_mfe_threshold(0.01)},
                {'label': 'mfe_24_gt_5pct', 'filter_fn': filter_mfe_threshold(0.05)},
                {'label': 'mfe_24_gt_10pct', 'filter_fn': filter_mfe_threshold(0.10)},
                {'label': 'fwd_ret_4h_positive', 'filter_fn': filter_fwd_return_positive('fwd_ret_4h')},
            ]

        results = harness.run_multi(
            configs,
            n_folds=args.n_folds,
            embargo_hours=args.embargo_hours,
        )

        if args.output_json:
            # Serialize to JSON
            _write_json(results, args.output_json)
        return

    # Default: run baseline
    result = harness.run_baseline(n_folds=args.n_folds)

    if args.output_json:
        _write_json(result, args.output_json)


def _write_json(data, path: str):
    """Write results to JSON file, handling non-serializable types."""
    def default_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return str(obj)

    with open(path, 'w') as f:
        json.dump(data, f, indent=2, default=default_serializer)
    logger.info("Results written to %s", path)


if __name__ == '__main__':
    main()
