#!/usr/bin/env python3
"""
Indicator Soup Reflexivity — Phase 2b: Per-Symbol Time-Series IC + Clustering
==============================================================================

Tests the reflexivity hypothesis: do specific indicators predict specific symbols
(concentrated signal) even when cross-sectional IC averages to zero?

Standard TA may not predict returns on average across all symbols. But if enough
participants in a specific symbol trade based on RSI, then RSI actually predicts
that symbol — not because RSI captures physics, but because those traders create
the pattern (Soros reflexivity). Crypto small-caps are ideal: concentrated
participant pools, low institutional presence, high retail TA usage.

Cross-sectional IC (Phase 2a) averages this signal away. Per-symbol time-series
IC reveals it.

Produces:
1. IC Profile Matrix: (n_indicators, n_symbols) per TF/horizon
2. Per-cell significance test (BH FDR at 10%)
3. Behavioral clusters via agglomerative clustering (Ward, cosine-equivalent)
4. Temporal stability validation (split-half + permutation ARI)
5. Cluster characterization (top indicators per cluster)

Usage:
    tmux new -s reflexivity
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/indicator_soup_reflexivity.py --days 270 --timeframes 1h,4h,15m

    # With Phase 2a screening results for overlap reporting:
    ... --screening-json /scripts/output/indicator_soup_screen_TIMESTAMP.json
"""

# ─── S0: Imports, Constants, Config ─────────────────────────────────────────

import gc
import os
import sys
import time
import json
import math
import resource
import argparse
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

# Execution context detection
if os.path.isdir('/app') and os.path.isfile('/app/worker.py'):
    SCRIPTS_DIR = '/scripts'
    sys.path.insert(0, '/scripts')
else:
    SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, SCRIPTS_DIR)

# .env loading
env_path = os.path.join(SCRIPTS_DIR, '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

# BLAS thread control — set BEFORE numpy import
os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')
os.environ.setdefault('MKL_NUM_THREADS', '4')

import numpy as np
from scipy import stats as sp_stats
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import pdist, squareform

from indicator_soup import iter_all_variants, total_variant_count
from indicator_soup_screen import (
    TF_BAR_MINUTES, MIN_CS_SYMBOLS, _horizons_for_tf,
    load_ohlcv_matrices, compute_forward_returns_raw, stratify_universe,
)

import warnings
warnings.filterwarnings('ignore', category=RuntimeWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("reflexivity")

# ─── Constants ──────────────────────────────────────────────────────────────

MIN_VALID_FRACTION = 0.8   # ≥80% valid bars in window for partial path
MIN_WINDOWS = 3            # Minimum non-overlapping windows for valid t-stat
MIN_SIG_INDICATORS = 3     # Min significant indicators for a symbol to cluster
MIN_SIG_SYMBOLS = 3        # Min significant symbols for an indicator to include
FDR_ALPHA = 0.10           # BH FDR alpha for per-cell significance
K_RANGE = (2, 30)          # Range of cluster counts to evaluate
N_PERMUTATIONS = 1000      # Permutation test iterations for ARI null
PROGRESS_INTERVAL = 10     # Variants between progress logs
DEFAULT_CLUSTER_HORIZON_HOURS = 4.0  # Default horizon for clustering


def _peak_rss_mb():
    """Current peak RSS in MB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


# ─── S1: Time-Series IC (Vectorized) ───────────────────────────────────────

def compute_window_size(n_bars, horizon_bars):
    """
    Window size for non-overlapping time-series IC.

    Scales with horizon: ≥10 independent returns per window.
    Caps at n_bars/5 to ensure ≥5 non-overlapping windows.
    """
    min_window = max(100, horizon_bars * 10)
    max_window = n_bars // 5
    window = min(max_window, max(min_window, 500))
    return max(window, 50)  # absolute floor


def time_series_ic_vectorized(indicator, fwd_ret, window):
    """
    Per-symbol time-series Spearman IC using non-overlapping windows.

    Vectorized across symbols per window via 2D argsort.
    For each window, all fully-valid columns are ranked simultaneously.
    Partially-valid columns (≥80% valid) use a per-column fallback.

    Args:
        indicator: (n_bars, n_symbols) float64
        fwd_ret: (n_bars, n_symbols) float64
        window: int — non-overlapping window size

    Returns:
        mean_ic: (n_symbols,) — mean IC per symbol (NaN if insufficient)
        t_stat: (n_symbols,) — t-statistic per symbol
        n_valid_windows: (n_symbols,) int — count of valid windows
        ic_per_window: (n_windows, n_symbols) — per-window ICs for split-half
    """
    n_bars, n_symbols = indicator.shape
    n_windows = n_bars // window

    if n_windows < MIN_WINDOWS:
        empty = np.full(n_symbols, np.nan)
        return empty, empty.copy(), np.zeros(n_symbols, dtype=int), None

    valid = ~(np.isnan(indicator) | np.isnan(fwd_ret))
    ic_per_window = np.full((n_windows, n_symbols), np.nan)

    for w_idx in range(n_windows):
        s = w_idx * window
        e = s + window

        ind_w = indicator[s:e, :]
        ret_w = fwd_ret[s:e, :]
        val_w = valid[s:e, :]

        n_valid_per_sym = val_w.sum(axis=0)

        # Fast path: fully valid columns (no NaN in entire window)
        fully_valid = n_valid_per_sym == window
        n_fv = fully_valid.sum()

        if n_fv > 0:
            ind_fv = ind_w[:, fully_valid]
            ret_fv = ret_w[:, fully_valid]

            # Rank per column: argsort(argsort(x)) = rank transform
            ind_r = np.argsort(np.argsort(ind_fv, axis=0), axis=0).astype(np.float64)
            ret_r = np.argsort(np.argsort(ret_fv, axis=0), axis=0).astype(np.float64)

            # Demean per column
            ind_r -= ind_r.mean(axis=0, keepdims=True)
            ret_r -= ret_r.mean(axis=0, keepdims=True)

            # Per-column Pearson on ranks = Spearman
            num = (ind_r * ret_r).sum(axis=0)
            ind_ss = (ind_r ** 2).sum(axis=0)
            ret_ss = (ret_r ** 2).sum(axis=0)
            den = np.sqrt(ind_ss * ret_ss)
            ic = np.where(den > 0, num / den, np.nan)

            ic_per_window[w_idx, fully_valid] = ic

        # Partial path: ≥80% valid bars (per-symbol fallback)
        min_valid = int(window * MIN_VALID_FRACTION)
        partial = (~fully_valid) & (n_valid_per_sym >= min_valid)
        partial_indices = np.where(partial)[0]

        for sym_idx in partial_indices:
            v = val_w[:, sym_idx]
            iv = ind_w[v, sym_idx]
            rv = ret_w[v, sym_idx]

            ir = np.argsort(np.argsort(iv)).astype(np.float64)
            rr = np.argsort(np.argsort(rv)).astype(np.float64)
            ir -= ir.mean()
            rr -= rr.mean()

            d = np.sqrt((ir ** 2).sum() * (rr ** 2).sum())
            if d > 0:
                ic_per_window[w_idx, sym_idx] = (ir * rr).sum() / d

    # Aggregate across windows per symbol
    n_valid_wins = np.sum(~np.isnan(ic_per_window), axis=0).astype(int)

    with np.errstate(invalid='ignore'):
        mean_ic = np.nanmean(ic_per_window, axis=0)

    # t-stat using empirical SE (more robust than theoretical 1/sqrt(n))
    with np.errstate(invalid='ignore', divide='ignore'):
        ic_std = np.nanstd(ic_per_window, axis=0, ddof=1)
        se = np.where(n_valid_wins > 1, ic_std / np.sqrt(n_valid_wins), np.inf)
        t_stat = np.where(se < np.inf, mean_ic / se, 0.0)

    # Require ≥MIN_WINDOWS valid windows
    insufficient = n_valid_wins < MIN_WINDOWS
    mean_ic[insufficient] = np.nan
    t_stat[insufficient] = np.nan

    return mean_ic, t_stat, n_valid_wins, ic_per_window


# ─── S2: IC Profile Matrix Assembly ────────────────────────────────────────

def build_ic_profiles(open_, high, low, close, volume, ts_grid,
                      fwd_ret_dict, horizons, cluster_h_bars):
    """
    Build IC profile matrices for all indicators at all horizons.

    Streams through iter_all_variants(), computing per-symbol time-series IC.
    For the clustering horizon, also stores split-half IC profiles for
    temporal stability validation.

    Returns:
        ic_profiles: dict[h_bars, (n_indicators, n_symbols)]
        t_profiles: dict[h_bars, (n_indicators, n_symbols)]
        nwin_profiles: dict[h_bars, (n_indicators, n_symbols)] int
        first_half_ic: (n_indicators, n_symbols) — IC from first half of windows
        second_half_ic: (n_indicators, n_symbols) — IC from second half of windows
        indicator_names: list[str]
        window_size_by_h: dict[h_bars, int]
    """
    n_bars, n_symbols = close.shape
    expected_total = total_variant_count()

    # Pre-compute window sizes per horizon
    window_size_by_h = {}
    for h_hours, h_bars in horizons:
        if h_bars in fwd_ret_dict:
            window_size_by_h[h_bars] = compute_window_size(n_bars, h_bars)

    # Cluster horizon window info
    cluster_window = window_size_by_h.get(cluster_h_bars)
    cluster_n_windows = n_bars // cluster_window if cluster_window else 0

    logger.info("  Window sizes: %s",
                {f"{h}h={b}bars": f"w={window_size_by_h.get(b, 'N/A')}"
                 for h, b in horizons if b in fwd_ret_dict})
    logger.info("  Cluster horizon: %d bars, window=%s, n_windows=%d",
                cluster_h_bars, cluster_window, cluster_n_windows)

    # Pre-allocate profile matrices
    ic_profiles = {}
    t_profiles = {}
    nwin_profiles = {}
    for h_bars in window_size_by_h:
        ic_profiles[h_bars] = np.full((expected_total, n_symbols), np.nan)
        t_profiles[h_bars] = np.full((expected_total, n_symbols), np.nan)
        nwin_profiles[h_bars] = np.zeros((expected_total, n_symbols), dtype=int)

    # Split-half profiles for clustering horizon (temporal stability)
    first_half_ic = np.full((expected_total, n_symbols), np.nan)
    second_half_ic = np.full((expected_total, n_symbols), np.nan)

    indicator_names = []
    t_start = time.time()

    for idx, (name, indicator_arr) in enumerate(
        iter_all_variants(open_, high, low, close, volume, ts_grid)
    ):
        indicator_names.append(name)

        for h_bars, window in window_size_by_h.items():
            if h_bars not in fwd_ret_dict:
                continue

            fwd = fwd_ret_dict[h_bars]
            mean_ic, t_stat, n_wins, ic_pw = time_series_ic_vectorized(
                indicator_arr, fwd, window
            )

            ic_profiles[h_bars][idx] = mean_ic
            t_profiles[h_bars][idx] = t_stat
            nwin_profiles[h_bars][idx] = n_wins

            # Split-half for cluster horizon
            if h_bars == cluster_h_bars and ic_pw is not None:
                n_w = ic_pw.shape[0]
                mid = n_w // 2
                if mid >= 2:
                    first_half_ic[idx] = np.nanmean(ic_pw[:mid], axis=0)
                    second_half_ic[idx] = np.nanmean(ic_pw[mid:], axis=0)

        del indicator_arr

        if (idx + 1) % PROGRESS_INTERVAL == 0:
            elapsed = time.time() - t_start
            rate = (idx + 1) / elapsed
            eta = (expected_total - idx - 1) / rate if rate > 0 else 0
            logger.info("    [%d/%d] %.1f variants/s, ETA %.0fs, RSS %.0f MB",
                        idx + 1, expected_total, rate, eta, _peak_rss_mb())

    n_actual = len(indicator_names)
    if n_actual != expected_total:
        logger.warning("  Variant count mismatch: expected %d, got %d",
                        expected_total, n_actual)
        for h_bars in ic_profiles:
            ic_profiles[h_bars] = ic_profiles[h_bars][:n_actual]
            t_profiles[h_bars] = t_profiles[h_bars][:n_actual]
            nwin_profiles[h_bars] = nwin_profiles[h_bars][:n_actual]
        first_half_ic = first_half_ic[:n_actual]
        second_half_ic = second_half_ic[:n_actual]

    elapsed = time.time() - t_start
    logger.info("  IC profiles complete: %d indicators, %.1fs (%.1f/s)",
                n_actual, elapsed, n_actual / elapsed if elapsed > 0 else 0)
    logger.info("  Peak RSS: %.0f MB", _peak_rss_mb())

    return (ic_profiles, t_profiles, nwin_profiles,
            first_half_ic, second_half_ic,
            indicator_names, window_size_by_h)


# ─── S3: Significance Filter ───────────────────────────────────────────────

def significance_filter(t_profiles, nwin_profiles, alpha=FDR_ALPHA):
    """
    Per-cell significance test using BH FDR on t-statistics.

    For each (indicator, symbol) cell, tests H0: IC=0 using the empirical
    t-stat from time_series_ic_vectorized. Global BH FDR across all cells
    and all horizons simultaneously.

    Returns:
        sig_masks: dict[h_bars, (n_indicators, n_symbols) bool]
        n_sig_total: int
    """
    # Collect all valid p-values across horizons
    p_list = []
    loc_list = []  # each row: [h_bars, indicator_idx, symbol_idx]

    for h_bars in sorted(t_profiles.keys()):
        t_mat = t_profiles[h_bars]
        nwin_mat = nwin_profiles[h_bars]

        valid = (~np.isnan(t_mat)) & (nwin_mat >= MIN_WINDOWS)
        valid_rows, valid_cols = np.where(valid)

        if len(valid_rows) == 0:
            continue

        t_vals = np.abs(t_mat[valid_rows, valid_cols])
        df_vals = np.maximum(nwin_mat[valid_rows, valid_cols].astype(float) - 1, 1)
        p_vals = 2.0 * sp_stats.t.sf(t_vals, df=df_vals)

        p_list.append(p_vals)
        loc_list.append(np.column_stack([
            np.full(len(valid_rows), h_bars),
            valid_rows,
            valid_cols
        ]))

    sig_masks = {h: np.zeros(t_profiles[h].shape, dtype=bool) for h in t_profiles}

    if not p_list:
        logger.info("  No valid cells for significance testing")
        return sig_masks, 0

    all_p = np.concatenate(p_list)
    all_locs = np.concatenate(loc_list).astype(int)
    m = len(all_p)

    # BH step-up procedure
    order = np.argsort(all_p)
    sorted_p = all_p[order]
    thresholds = np.arange(1, m + 1) / m * alpha
    reject = sorted_p <= thresholds

    if reject.any():
        max_k = int(np.max(np.where(reject)[0]))
        rej_locs = all_locs[order[:max_k + 1]]
    else:
        rej_locs = np.empty((0, 3), dtype=int)

    # Populate sig masks
    for h_bars in sig_masks:
        h_mask = rej_locs[:, 0] == h_bars
        if h_mask.any():
            rows = rej_locs[h_mask, 1]
            cols = rej_locs[h_mask, 2]
            sig_masks[h_bars][rows, cols] = True

    n_sig = len(rej_locs)
    logger.info("  Significance filter: %d/%d cells survive BH FDR at alpha=%.2f",
                n_sig, m, alpha)
    for h_bars in sorted(sig_masks.keys()):
        n_h = sig_masks[h_bars].sum()
        if n_h > 0:
            logger.info("    h=%d bars: %d significant cells", h_bars, n_h)

    return sig_masks, n_sig


# ─── S4: Filter + Cluster ──────────────────────────────────────────────────

def filter_for_clustering(ic_profile, sig_mask):
    """
    Filter IC profile for clustering: keep indicators with enough significant
    symbols AND symbols with enough significant indicators.

    Non-significant cells set to 0 (no signal). NaN set to 0.
    Iterative filtering until stable.

    Returns:
        filtered_ic: (n_kept_indicators, n_kept_symbols) or None if too few
        kept_indicators: array of original indicator indices
        kept_symbols: array of original symbol indices
    """
    n_ind, n_sym = ic_profile.shape
    keep_ind = sig_mask.sum(axis=1) >= MIN_SIG_SYMBOLS
    keep_sym = sig_mask.sum(axis=0) >= MIN_SIG_INDICATORS

    # Iterative pruning: removing indicators can reduce symbol sig counts
    for _ in range(10):
        if keep_ind.sum() == 0 or keep_sym.sum() == 0:
            break

        sub_mask = sig_mask[np.ix_(np.where(keep_ind)[0], np.where(keep_sym)[0])]
        new_ind_sub = sub_mask.sum(axis=1) >= MIN_SIG_SYMBOLS
        new_sym_sub = sub_mask.sum(axis=0) >= MIN_SIG_INDICATORS

        ind_idx = np.where(keep_ind)[0]
        sym_idx = np.where(keep_sym)[0]

        keep_ind_new = np.zeros(n_ind, dtype=bool)
        keep_sym_new = np.zeros(n_sym, dtype=bool)
        keep_ind_new[ind_idx[new_ind_sub]] = True
        keep_sym_new[sym_idx[new_sym_sub]] = True

        if (np.array_equal(keep_ind_new, keep_ind) and
                np.array_equal(keep_sym_new, keep_sym)):
            break
        keep_ind = keep_ind_new
        keep_sym = keep_sym_new

    kept_indicators = np.where(keep_ind)[0]
    kept_symbols = np.where(keep_sym)[0]

    if len(kept_indicators) < 5 or len(kept_symbols) < 10:
        logger.warning("  Too few for clustering: %d indicators, %d symbols",
                        len(kept_indicators), len(kept_symbols))
        return None, kept_indicators, kept_symbols

    # Extract filtered IC, zero-out non-significant cells
    filtered_ic = ic_profile[np.ix_(kept_indicators, kept_symbols)].copy()
    filtered_sig = sig_mask[np.ix_(kept_indicators, kept_symbols)]
    filtered_ic[~filtered_sig] = 0.0
    filtered_ic = np.nan_to_num(filtered_ic, nan=0.0)

    logger.info("  Clustering filter: %d indicators, %d symbols retained",
                len(kept_indicators), len(kept_symbols))

    return filtered_ic, kept_indicators, kept_symbols


def cluster_symbols(feature_matrix, k):
    """
    Cluster symbols using Ward linkage on L2-normalized feature vectors.

    Ward linkage requires Euclidean distance. For cosine-equivalent clustering,
    L2-normalize each symbol vector first. Then ||a-b||² = 2(1-cos(a,b))
    for unit vectors, so Ward on normalized = Ward on cosine similarity.

    Args:
        feature_matrix: (n_indicators, n_symbols) — columns are symbol vectors
        k: number of clusters

    Returns:
        labels: (n_symbols,) int array (1-indexed from fcluster)
        linkage_matrix: Z from scipy linkage (reusable for other k values)
    """
    X = feature_matrix.T.copy()  # (n_symbols, n_indicators)

    # L2 normalize each row
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X_norm = X / norms

    Z = linkage(X_norm, method='ward', metric='euclidean')
    labels = fcluster(Z, t=k, criterion='maxclust')

    return labels, Z


# ─── S5: Cluster Quality Metrics ───────────────────────────────────────────

def silhouette_score(X, labels):
    """
    Mean silhouette coefficient. X is (n_samples, n_features).
    Uses precomputed Euclidean distance matrix.
    """
    n = X.shape[0]
    unique_labels = np.unique(labels)
    k = len(unique_labels)

    if k < 2 or k >= n:
        return -1.0

    # Euclidean distance matrix
    dist = squareform(pdist(X, metric='euclidean'))

    sil = np.zeros(n)
    for i in range(n):
        same = labels == labels[i]
        same[i] = False
        n_same = same.sum()

        if n_same == 0:
            sil[i] = 0.0
            continue

        a_i = dist[i, same].mean()

        # Mean distance to nearest other cluster
        b_i = np.inf
        for lbl in unique_labels:
            if lbl == labels[i]:
                continue
            other = labels == lbl
            if other.sum() > 0:
                b_i = min(b_i, dist[i, other].mean())

        sil[i] = (b_i - a_i) / max(a_i, b_i) if max(a_i, b_i) > 0 else 0.0

    return float(np.mean(sil))


def calinski_harabasz_score(X, labels):
    """
    Calinski-Harabasz index (higher = better).
    CH = (trace(B)/(k-1)) / (trace(W)/(n-k))
    """
    n, d = X.shape
    unique_labels = np.unique(labels)
    k = len(unique_labels)

    if k < 2 or k >= n:
        return 0.0

    overall_mean = X.mean(axis=0)

    # Between-cluster and within-cluster scatter
    trace_B = 0.0
    trace_W = 0.0

    for lbl in unique_labels:
        mask = labels == lbl
        n_k = mask.sum()
        cluster_mean = X[mask].mean(axis=0)

        # Between: n_k * ||centroid - overall_mean||²
        trace_B += n_k * np.sum((cluster_mean - overall_mean) ** 2)

        # Within: Σ ||x - centroid||²
        trace_W += np.sum((X[mask] - cluster_mean) ** 2)

    if trace_W == 0:
        return 0.0

    ch = (trace_B / (k - 1)) / (trace_W / (n - k))
    return float(ch)


def davies_bouldin_score(X, labels):
    """
    Davies-Bouldin index (lower = better).
    DB = (1/k) Σ max_{j≠i} (s_i + s_j) / d(c_i, c_j)
    """
    unique_labels = np.unique(labels)
    k = len(unique_labels)

    if k < 2:
        return np.inf

    centroids = np.zeros((k, X.shape[1]))
    scatter = np.zeros(k)

    for idx, lbl in enumerate(unique_labels):
        mask = labels == lbl
        cluster_pts = X[mask]
        centroids[idx] = cluster_pts.mean(axis=0)
        scatter[idx] = np.mean(np.sqrt(np.sum((cluster_pts - centroids[idx]) ** 2, axis=1)))

    db_sum = 0.0
    for i in range(k):
        max_ratio = 0.0
        for j in range(k):
            if i == j:
                continue
            d_ij = np.sqrt(np.sum((centroids[i] - centroids[j]) ** 2))
            if d_ij > 0:
                ratio = (scatter[i] + scatter[j]) / d_ij
                max_ratio = max(max_ratio, ratio)
        db_sum += max_ratio

    return float(db_sum / k)


def select_optimal_k(feature_matrix, k_range=K_RANGE):
    """
    Evaluate clustering quality across k=k_range[0]..k_range[1].
    Uses silhouette, CH, DB indices. Returns best k and all metrics.

    feature_matrix: (n_indicators, n_symbols)
    """
    X = feature_matrix.T.copy()  # (n_symbols, n_indicators)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X_norm = X / norms

    Z = linkage(X_norm, method='ward', metric='euclidean')

    k_min, k_max = k_range
    k_max = min(k_max, X.shape[0] // 2)  # Need at least 2 per cluster avg

    metrics = {}
    for k in range(k_min, k_max + 1):
        labels = fcluster(Z, t=k, criterion='maxclust')

        # Skip if any cluster has <3 members
        counts = np.bincount(labels)
        if counts.min() < 3:
            continue

        sil = silhouette_score(X_norm, labels)
        ch = calinski_harabasz_score(X_norm, labels)
        db = davies_bouldin_score(X_norm, labels)

        metrics[k] = {
            'silhouette': sil,
            'calinski_harabasz': ch,
            'davies_bouldin': db,
            'labels': labels,
            'cluster_sizes': [int(c) for c in np.bincount(labels)[1:]],
        }

        logger.info("    k=%2d: sil=%.3f  CH=%.1f  DB=%.3f  sizes=%s",
                     k, sil, ch, db, metrics[k]['cluster_sizes'])

    if not metrics:
        logger.warning("  No valid k found in range %s", k_range)
        return None, {}

    # Rank each metric (silhouette: higher better, CH: higher better, DB: lower better)
    ks = sorted(metrics.keys())
    sil_rank = {k: r for r, k in enumerate(sorted(ks, key=lambda k: -metrics[k]['silhouette']))}
    ch_rank = {k: r for r, k in enumerate(sorted(ks, key=lambda k: -metrics[k]['calinski_harabasz']))}
    db_rank = {k: r for r, k in enumerate(sorted(ks, key=lambda k: metrics[k]['davies_bouldin']))}

    # Composite rank (lower = better)
    composite = {k: sil_rank[k] + ch_rank[k] + db_rank[k] for k in ks}
    best_k = min(composite, key=composite.get)

    logger.info("  Best k=%d (composite rank %d): sil=%.3f, CH=%.1f, DB=%.3f",
                best_k,
                composite[best_k],
                metrics[best_k]['silhouette'],
                metrics[best_k]['calinski_harabasz'],
                metrics[best_k]['davies_bouldin'])

    return best_k, metrics


# ─── S6: Temporal Stability ────────────────────────────────────────────────

def adjusted_rand_index(labels_a, labels_b):
    """
    Compute Adjusted Rand Index between two label vectors.
    ARI = (RI - E[RI]) / (max(RI) - E[RI])
    """
    n = len(labels_a)
    if n < 2:
        return 0.0

    # Contingency table
    classes_a = np.unique(labels_a)
    classes_b = np.unique(labels_b)

    contingency = np.zeros((len(classes_a), len(classes_b)), dtype=int)
    a_map = {c: i for i, c in enumerate(classes_a)}
    b_map = {c: i for i, c in enumerate(classes_b)}

    for i in range(n):
        contingency[a_map[labels_a[i]], b_map[labels_b[i]]] += 1

    # Sum of C(n_ij, 2)
    sum_comb_ij = np.sum(contingency * (contingency - 1)) / 2

    # Row sums and column sums
    row_sums = contingency.sum(axis=1)
    col_sums = contingency.sum(axis=0)

    sum_comb_rows = np.sum(row_sums * (row_sums - 1)) / 2
    sum_comb_cols = np.sum(col_sums * (col_sums - 1)) / 2

    n_comb = n * (n - 1) / 2

    expected = sum_comb_rows * sum_comb_cols / n_comb if n_comb > 0 else 0
    max_index = (sum_comb_rows + sum_comb_cols) / 2

    if max_index == expected:
        return 1.0 if sum_comb_ij == expected else 0.0

    ari = (sum_comb_ij - expected) / (max_index - expected)
    return float(ari)


def temporal_stability_test(first_half_ic, second_half_ic,
                            kept_indicators, kept_symbols,
                            sig_mask, best_k, n_permutations=N_PERMUTATIONS):
    """
    Test temporal stability via split-half clustering + permutation ARI.

    1. Cluster first-half IC profiles -> labels_1
    2. Cluster second-half IC profiles -> labels_2
    3. ARI(labels_1, labels_2) = real ARI
    4. Permute labels_2 1000 times -> null ARI distribution
    5. Stable if real ARI > 95th percentile of null

    Returns:
        real_ari: float
        null_threshold: float (95th percentile)
        is_stable: bool
    """
    # Extract and filter the half profiles (same indicators/symbols as full)
    fh = first_half_ic[np.ix_(kept_indicators, kept_symbols)].copy()
    sh = second_half_ic[np.ix_(kept_indicators, kept_symbols)].copy()

    # Apply significance mask (same as full clustering)
    filt_sig = sig_mask[np.ix_(kept_indicators, kept_symbols)]
    fh[~filt_sig] = 0.0
    sh[~filt_sig] = 0.0
    fh = np.nan_to_num(fh, nan=0.0)
    sh = np.nan_to_num(sh, nan=0.0)

    # Check for degenerate data
    if fh.shape[1] < 10 or sh.shape[1] < 10:
        logger.warning("  Too few symbols for temporal stability test")
        return 0.0, 1.0, False

    # Cluster each half
    labels_1, _ = cluster_symbols(fh, best_k)
    labels_2, _ = cluster_symbols(sh, best_k)

    real_ari = adjusted_rand_index(labels_1, labels_2)

    # Permutation null
    rng = np.random.default_rng(42)
    null_aris = np.zeros(n_permutations)
    for p in range(n_permutations):
        shuffled = labels_2.copy()
        rng.shuffle(shuffled)
        null_aris[p] = adjusted_rand_index(labels_1, shuffled)

    null_threshold = float(np.percentile(null_aris, 95))
    is_stable = real_ari > null_threshold

    logger.info("  Temporal stability: ARI=%.4f, null_95th=%.4f, stable=%s",
                real_ari, null_threshold, is_stable)
    logger.info("    Null ARI distribution: mean=%.4f, std=%.4f, max=%.4f",
                null_aris.mean(), null_aris.std(), null_aris.max())

    return real_ari, null_threshold, is_stable


# ─── S7: Cluster Characterization ──────────────────────────────────────────

def characterize_clusters(ic_profile_filtered, labels, indicator_names_kept,
                          symbols_kept, top_n=5):
    """
    For each cluster, identify top indicators by mean |IC| within cluster.

    Returns list of cluster descriptions:
    [{cluster_id, size, symbols, top_indicators: [{name, mean_ic, n_sig}]}]
    """
    unique_labels = np.unique(labels)
    clusters = []

    for lbl in unique_labels:
        member_mask = labels == lbl
        n_members = member_mask.sum()
        member_syms = [symbols_kept[i] for i in range(len(labels)) if labels[i] == lbl]

        # Mean |IC| per indicator across cluster members
        cluster_ic = ic_profile_filtered[:, member_mask]  # (n_ind, n_members)
        mean_abs_ic = np.nanmean(np.abs(cluster_ic), axis=1)

        # Top indicators by mean |IC|
        top_idx = np.argsort(-mean_abs_ic)[:top_n]
        top_indicators = []
        for idx in top_idx:
            ind_name = indicator_names_kept[idx]
            mic = float(mean_abs_ic[idx])
            # Count significant cells in this cluster
            n_sig = int(np.sum(cluster_ic[idx] != 0))
            top_indicators.append({
                'name': ind_name,
                'mean_abs_ic': mic,
                'n_sig_symbols': n_sig,
            })

        clusters.append({
            'cluster_id': int(lbl),
            'size': int(n_members),
            'symbols': member_syms,
            'top_indicators': top_indicators,
        })

    return clusters


# ─── S8: Output ────────────────────────────────────────────────────────────

def write_output(timeframe, ic_profiles, t_profiles, nwin_profiles,
                 indicator_names, symbols, cluster_result, stability_result,
                 sig_summary, screening_overlap, output_dir, args):
    """Write JSON report + .npy IC profile matrices."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Save .npy IC profile matrices (one per horizon)
    npy_dir = output_path / f"reflexivity_{timeframe}"
    npy_dir.mkdir(parents=True, exist_ok=True)

    for h_bars, ic_mat in ic_profiles.items():
        np.save(npy_dir / f"ic_profile_h{h_bars}.npy", ic_mat)
        np.save(npy_dir / f"t_profile_h{h_bars}.npy", t_profiles[h_bars])
        np.save(npy_dir / f"nwin_profile_h{h_bars}.npy", nwin_profiles[h_bars])

    # Save metadata
    meta = {
        'timeframe': timeframe,
        'n_indicators': len(indicator_names),
        'n_symbols': len(symbols),
        'indicator_names': indicator_names,
        'symbols': symbols,
        'horizons_bars': sorted(ic_profiles.keys()),
    }
    with open(npy_dir / "meta.json", 'w') as f:
        json.dump(meta, f, indent=2)

    # JSON report
    report = {
        'timestamp': timestamp,
        'timeframe': timeframe,
        'args': vars(args),
        'n_indicators': len(indicator_names),
        'n_symbols': len(symbols),
        'significance': sig_summary,
        'screening_overlap': screening_overlap,
    }

    if cluster_result:
        report['clustering'] = {
            'best_k': cluster_result['best_k'],
            'metrics': {
                str(k): {mk: mv for mk, mv in v.items() if mk != 'labels'}
                for k, v in cluster_result['metrics'].items()
            },
            'clusters': cluster_result['clusters'],
        }

    if stability_result:
        report['temporal_stability'] = {
            'ari': stability_result['ari'],
            'null_threshold': stability_result['null_threshold'],
            'is_stable': stability_result['is_stable'],
        }

    json_path = output_path / f"indicator_soup_reflexivity_{timeframe}_{timestamp}.json"
    with open(json_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    logger.info("  Output written:")
    logger.info("    Profiles: %s", npy_dir)
    logger.info("    Report:   %s", json_path)

    return str(json_path), str(npy_dir)


# ─── S9: Main + CLI ────────────────────────────────────────────────────────

def process_timeframe(database_url, timeframe, target_days, cache_dir,
                      cluster_horizon_hours, screening_survivors=None,
                      fdr_alpha=FDR_ALPHA):
    """
    Run full Phase 2b pipeline for one timeframe.

    Returns dict with all results for output.
    """
    bar_minutes = TF_BAR_MINUTES[timeframe]
    horizons = _horizons_for_tf(bar_minutes)

    # Find closest horizon to cluster_horizon_hours
    cluster_h_bars = max(1, round(cluster_horizon_hours * 60 / bar_minutes))
    available_h = [h_bars for _, h_bars in horizons]
    cluster_h_bars = min(available_h, key=lambda x: abs(x - cluster_h_bars))

    logger.info("=" * 70)
    logger.info("REFLEXIVITY %s — %d horizons, cluster at h=%d bars",
                timeframe, len(horizons), cluster_h_bars)
    logger.info("=" * 70)

    # 1. Load OHLCV from cache
    open_, high, low, close, volume, ts_grid, symbols, btc_idx = \
        load_ohlcv_matrices(database_url, timeframe, target_days, cache_dir)
    n_bars, n_symbols = close.shape
    logger.info("  Loaded: (%d, %d), Peak RSS: %.0f MB", n_bars, n_symbols, _peak_rss_mb())

    # 2. Forward returns
    fwd_ret_dict = compute_forward_returns_raw(close, timeframe, cache_dir)
    if not fwd_ret_dict:
        logger.error("  No valid horizons, skipping %s", timeframe)
        return None
    logger.info("  Forward returns: %d horizons, Peak RSS: %.0f MB",
                len(fwd_ret_dict), _peak_rss_mb())

    # 3. Build IC profiles (main computation)
    (ic_profiles, t_profiles, nwin_profiles,
     first_half_ic, second_half_ic,
     indicator_names, window_size_by_h) = build_ic_profiles(
        open_, high, low, close, volume, ts_grid,
        fwd_ret_dict, horizons, cluster_h_bars
    )

    # 4. Significance filter
    sig_masks, n_sig_total = significance_filter(t_profiles, nwin_profiles, alpha=fdr_alpha)

    sig_summary = {
        'total_cells_tested': sum(
            int((~np.isnan(t_profiles[h]) & (nwin_profiles[h] >= MIN_WINDOWS)).sum())
            for h in t_profiles
        ),
        'n_significant': n_sig_total,
        'fdr_alpha': fdr_alpha,
        'per_horizon': {
            str(h): int(sig_masks[h].sum()) for h in sorted(sig_masks.keys())
        },
    }

    # 5. Check overlap with Phase 2a survivors (if provided)
    screening_overlap = {}
    if screening_survivors:
        survivor_names = set(screening_survivors)
        for h_bars in sig_masks:
            sig_indicators = set()
            for i in range(sig_masks[h_bars].shape[0]):
                if sig_masks[h_bars][i].any() and i < len(indicator_names):
                    sig_indicators.add(indicator_names[i])
            overlap = sig_indicators & survivor_names
            if overlap:
                screening_overlap[str(h_bars)] = sorted(overlap)

    # 6. Clustering (on cluster horizon)
    cluster_result = None
    stability_result = None

    if cluster_h_bars in sig_masks and sig_masks[cluster_h_bars].sum() > 0:
        logger.info("  Clustering at h=%d bars...", cluster_h_bars)

        filtered_ic, kept_indicators, kept_symbols = filter_for_clustering(
            ic_profiles[cluster_h_bars], sig_masks[cluster_h_bars]
        )

        if filtered_ic is not None:
            # Select optimal k
            best_k, metrics = select_optimal_k(filtered_ic)

            if best_k is not None:
                labels = metrics[best_k]['labels']

                # Characterize clusters
                ind_names_kept = [indicator_names[i] for i in kept_indicators]
                sym_names_kept = [symbols[i] for i in kept_symbols]
                clusters = characterize_clusters(
                    filtered_ic, labels, ind_names_kept, sym_names_kept
                )

                cluster_result = {
                    'best_k': best_k,
                    'metrics': metrics,
                    'clusters': clusters,
                    'kept_indicators': kept_indicators.tolist(),
                    'kept_symbols': kept_symbols.tolist(),
                }

                # 7. Temporal stability
                logger.info("  Temporal stability test...")
                ari, null_thresh, is_stable = temporal_stability_test(
                    first_half_ic, second_half_ic,
                    kept_indicators, kept_symbols,
                    sig_masks[cluster_h_bars], best_k
                )

                stability_result = {
                    'ari': ari,
                    'null_threshold': null_thresh,
                    'is_stable': is_stable,
                }

                # Log cluster summary
                for cl in clusters:
                    logger.info("  Cluster %d (%d symbols): %s",
                                cl['cluster_id'], cl['size'],
                                ", ".join(t['name'] for t in cl['top_indicators'][:3]))
    else:
        logger.info("  No significant cells at cluster horizon — skipping clustering")

    # 8. DBSCAN cross-check (optional, if sklearn available)
    try:
        from sklearn.cluster import DBSCAN
        if cluster_result and filtered_ic is not None:
            X_norm = filtered_ic.T.copy()
            norms = np.linalg.norm(X_norm, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            X_norm = X_norm / norms

            db = DBSCAN(eps=0.5, min_samples=5, metric='euclidean').fit(X_norm)
            db_labels = db.labels_
            n_clusters_db = len(set(db_labels)) - (1 if -1 in db_labels else 0)
            n_noise = (db_labels == -1).sum()
            logger.info("  DBSCAN cross-check: %d clusters, %d noise points",
                        n_clusters_db, n_noise)
            if cluster_result:
                cluster_result['dbscan_n_clusters'] = n_clusters_db
                cluster_result['dbscan_n_noise'] = n_noise
    except ImportError:
        logger.info("  sklearn not available — skipping DBSCAN cross-check")

    return {
        'ic_profiles': ic_profiles,
        't_profiles': t_profiles,
        'nwin_profiles': nwin_profiles,
        'indicator_names': indicator_names,
        'symbols': symbols,
        'cluster_result': cluster_result,
        'stability_result': stability_result,
        'sig_summary': sig_summary,
        'screening_overlap': screening_overlap,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Indicator Soup Phase 2b: Per-Symbol IC + Behavioral Clustering"
    )
    parser.add_argument('--days', type=int, default=270,
                        help="Target days of data (default: 270)")
    parser.add_argument('--timeframes', type=str, default='1h,4h,15m',
                        help="Comma-separated timeframes (default: 1h,4h,15m)")
    parser.add_argument('--cache-dir', type=str, default=None,
                        help="Cache directory for .npy files")
    parser.add_argument('--screening-json', type=str, default=None,
                        help="Phase 2a screening JSON for overlap reporting")
    parser.add_argument('--cluster-horizon', type=float,
                        default=DEFAULT_CLUSTER_HORIZON_HOURS,
                        help="Horizon hours for clustering (default: 4.0)")
    parser.add_argument('--fdr-alpha', type=float, default=FDR_ALPHA,
                        help="BH FDR alpha for significance (default: 0.10)")
    args = parser.parse_args()

    fdr_alpha = args.fdr_alpha

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    timeframes = [tf.strip() for tf in args.timeframes.split(',')]
    for tf in timeframes:
        if tf not in TF_BAR_MINUTES:
            logger.error("Unknown timeframe: %s", tf)
            sys.exit(1)

    # Default cache dir
    if args.cache_dir:
        cache_dir = args.cache_dir
    elif os.path.isdir('/scripts'):
        cache_dir = '/scripts/output/indicator_soup_cache'
    else:
        cache_dir = os.path.join(SCRIPTS_DIR, 'output', 'indicator_soup_cache')
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    # Output dir
    if os.path.isdir('/scripts'):
        output_dir = '/scripts/output'
    else:
        output_dir = os.path.join(SCRIPTS_DIR, 'output')

    # Load Phase 2a survivors (optional)
    screening_survivors = None
    if args.screening_json:
        try:
            with open(args.screening_json) as f:
                screen_data = json.load(f)
            screening_survivors = [
                s['name'] for s in screen_data.get('survivors', [])
            ]
            logger.info("Loaded %d Phase 2a survivors from %s",
                        len(screening_survivors), args.screening_json)
        except Exception as e:
            logger.warning("Could not load screening JSON: %s", e)

    logger.info("Indicator Soup Reflexivity — Phase 2b")
    logger.info("  Days: %d, Timeframes: %s, Cluster horizon: %.1fh",
                args.days, timeframes, args.cluster_horizon)
    logger.info("  Cache: %s", cache_dir)
    logger.info("  Variant count: %d", total_variant_count())
    logger.info("  Peak RSS at start: %.0f MB", _peak_rss_mb())

    t_total_start = time.time()

    for tf in timeframes:
        result = process_timeframe(
            database_url, tf, args.days, cache_dir,
            args.cluster_horizon, screening_survivors,
            fdr_alpha=fdr_alpha,
        )

        if result is None:
            continue

        # Write output for this timeframe
        write_output(
            tf,
            result['ic_profiles'],
            result['t_profiles'],
            result['nwin_profiles'],
            result['indicator_names'],
            result['symbols'],
            result['cluster_result'],
            result['stability_result'],
            result['sig_summary'],
            result['screening_overlap'],
            output_dir,
            args,
        )

        # GC between timeframes
        del result
        gc.collect()
        logger.info("  Post-%s GC. RSS: %.0f MB", tf, _peak_rss_mb())

    total_elapsed = time.time() - t_total_start
    logger.info("=" * 70)
    logger.info("REFLEXIVITY COMPLETE — %.1f min total", total_elapsed / 60)
    logger.info("=" * 70)


if __name__ == '__main__':
    main()
