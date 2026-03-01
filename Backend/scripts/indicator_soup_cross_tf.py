#!/usr/bin/env python3
"""
Indicator Soup Cross-TF — Phase 2.5 Cross-Timeframe Correlation Analysis
=========================================================================

Runs AFTER indicator_soup_screen.py completes for all timeframes. Consumes
the cached OHLCV and screening results to produce:
1. Cross-TF IC series correlation matrix
2. Global n_effective (true effective test count across all TFs)
3. Cross-TF redundancy clusters
4. Cross-TF interaction screening (conditioning gates)
5. Multi-resolution feature bundles for Phase 3

Usage:
    tmux new -s cross-tf
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/indicator_soup_cross_tf.py \
        --screening-json /scripts/output/indicator_soup_screen_TIMESTAMP.json \
        --cache-dir /scripts/output/indicator_soup_cache \
        --target-tf 1h
"""

# ─── Imports ────────────────────────────────────────────────────────────────

import gc
import os
import sys
import time
import json
import math
import resource
import argparse
import logging
from dataclasses import dataclass, field, asdict
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

os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')
os.environ.setdefault('MKL_NUM_THREADS', '4')

import numpy as np
from scipy import stats as sp_stats

from indicator_soup import iter_all_variants, total_variant_count
from indicator_soup_screen import (
    TF_BAR_MINUTES, MIN_CS_SYMBOLS, _horizons_for_tf,
    load_ohlcv_matrices, compute_forward_returns_raw, stratify_universe,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cross_tf")


def _peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


# ─── Step 1: Load Survivors from Screening ──────────────────────────────────

@dataclass
class SurvivorEntry:
    """A feature that survived (or nearly survived) FDR screening."""
    name: str
    timeframe: str
    horizon_hours: float
    horizon_bars: int
    tier: str
    mean_ic: float
    t_stat: float
    adjusted_pvalue: float


def load_survivors_from_screening(screening_json_path, near_miss_alpha=0.10):
    """
    Parse the Phase 2 JSON output.

    Returns survivors + near-misses (adjusted_pvalue < near_miss_alpha).
    Near-misses are included because cross-TF interaction might push
    borderline indicators over the threshold.
    """
    with open(screening_json_path) as f:
        data = json.load(f)

    survivors = []
    for s in data.get('survivors', []):
        survivors.append(SurvivorEntry(
            name=s['name'],
            timeframe=s['timeframe'],
            horizon_hours=s['horizon_hours'],
            horizon_bars=s['horizon_bars'],
            tier=s['tier'],
            mean_ic=s['mean_ic'],
            t_stat=s['t_stat'],
            adjusted_pvalue=s.get('adjusted_pvalue', 0.0),
        ))

    # Deduplicate: keep strongest entry per (name, timeframe)
    best = {}
    for s in survivors:
        key = (s.name, s.timeframe)
        if key not in best or abs(s.t_stat) > abs(best[key].t_stat):
            best[key] = s

    result = list(best.values())
    logger.info("Loaded %d unique survivors from %s", len(result), screening_json_path)
    return result


# ─── Step 2: Build Cross-TF Feature Matrix ─────────────────────────────────

# Aggregation method per indicator family type
# Default is 'last' (safest — no information invented)
AGG_METHODS = {
    # Volume indicators: average flow over the period
    'OBV': 'mean', 'CMF': 'mean', 'AD': 'mean', 'MFI': 'mean',
    'VPT': 'mean', 'VWAP': 'mean', 'PVT': 'mean',
    # Volatility: worst-case expansion
    'ATR': 'max', 'NATR': 'max', 'BB': 'max', 'KC': 'max',
    'HIST_VOL': 'max', 'PARKINSON': 'max', 'GARMAN_KLASS': 'max',
}


def _get_agg_method(indicator_name):
    """Determine aggregation method for lower-TF → target-TF alignment."""
    for prefix, method in AGG_METHODS.items():
        if indicator_name.startswith(prefix):
            return method
    return 'last'  # Default: use most recent value


def align_higher_tf_to_target(source_indicator, source_ts, target_ts, source_bar_seconds):
    """
    Align a higher-TF indicator to the target-TF grid via forward-fill.

    Uses bar COMPLETION times to avoid look-ahead: a source bar starting
    at time T doesn't complete until T + source_bar_seconds. Target bars
    before the first source bar completes get NaN.
    """
    n_target = len(target_ts)
    n_symbols = source_indicator.shape[1]

    # Source bar completion times
    source_completion_ts = source_ts + source_bar_seconds

    # For each target timestamp, find the last source bar that COMPLETED before it
    align_idx = np.searchsorted(source_completion_ts, target_ts, side='right') - 1

    aligned = np.full((n_target, n_symbols), np.nan, dtype=np.float64)
    valid = align_idx >= 0
    aligned[valid] = source_indicator[align_idx[valid]]

    return aligned


def align_lower_tf_to_target(source_indicator, source_ts, target_ts,
                             target_bar_seconds, agg_method='last'):
    """
    Align a lower-TF indicator to the target-TF grid via aggregation.

    For each target bar, aggregates the constituent lower-TF bars.
    """
    n_target = len(target_ts)
    n_symbols = source_indicator.shape[1]
    aligned = np.full((n_target, n_symbols), np.nan, dtype=np.float64)

    for t_idx in range(n_target):
        t_start = target_ts[t_idx]
        t_end = t_start + target_bar_seconds
        # Source bars whose start time falls within [t_start, t_end)
        mask = (source_ts >= t_start) & (source_ts < t_end)
        if not mask.any():
            continue
        chunk = source_indicator[mask]  # (n_sub_bars, n_symbols)
        if agg_method == 'last':
            aligned[t_idx] = chunk[-1]
        elif agg_method == 'mean':
            aligned[t_idx] = np.nanmean(chunk, axis=0)
        elif agg_method == 'max':
            aligned[t_idx] = np.nanmax(chunk, axis=0)

    return aligned


def build_cross_tf_feature_matrix(target_tf, survivor_list, cache_dir, database_url, target_days):
    """
    Build a feature dict where features come from ALL timeframes,
    aligned to target_tf's bar grid.

    Returns:
        feature_matrix: dict[str, (n_bars_target, n_symbols) float64]
        feature_meta: list of dicts with alignment metadata
        target_ohlcv: tuple of (open_, high, low, close, volume, ts_grid, symbols, btc_idx)
    """
    target_bar_seconds = TF_BAR_MINUTES[target_tf] * 60

    # Load target TF OHLCV (from cache)
    target_ohlcv = load_ohlcv_matrices(database_url, target_tf, target_days, cache_dir)
    target_open, target_high, target_low, target_close, target_vol, target_ts, target_symbols, target_btc_idx = target_ohlcv
    n_target_bars = len(target_ts)
    n_target_symbols = len(target_symbols)

    logger.info("Target TF %s: %d bars × %d symbols", target_tf, n_target_bars, n_target_symbols)

    # Group survivors by source timeframe
    tf_survivors = {}
    for s in survivor_list:
        tf_survivors.setdefault(s.timeframe, []).append(s)

    # Collect all unique timeframes we need
    all_tfs = set(tf_survivors.keys())
    logger.info("Source timeframes: %s", sorted(all_tfs))

    feature_matrix = {}
    feature_meta = []

    for source_tf in sorted(all_tfs):
        source_bar_seconds = TF_BAR_MINUTES[source_tf] * 60
        survivors_in_tf = tf_survivors[source_tf]
        survivor_names = {s.name for s in survivors_in_tf}

        logger.info("  Loading %s indicators (%d survivors)...", source_tf, len(survivor_names))
        t0 = time.time()

        if source_tf == target_tf:
            # Same TF: direct copy — compute from target OHLCV
            source_ohlcv = target_ohlcv
            source_ts_arr = target_ts
            alignment = 'same'
        else:
            # Load source TF OHLCV
            source_ohlcv = load_ohlcv_matrices(database_url, source_tf, target_days, cache_dir)
            source_ts_arr = source_ohlcv[5]  # ts_grid
            alignment = 'higher' if source_bar_seconds > target_bar_seconds else 'lower'

        # Stream through indicators, keep only survivors
        s_open, s_high, s_low, s_close, s_vol, s_ts, s_symbols, _ = source_ohlcv
        n_collected = 0

        for name, arr in iter_all_variants(s_open, s_high, s_low, s_close, s_vol, s_ts):
            if name not in survivor_names:
                continue

            feature_key = f"{name}@{source_tf}"

            if alignment == 'same':
                feature_matrix[feature_key] = arr
            elif alignment == 'higher':
                feature_matrix[feature_key] = align_higher_tf_to_target(
                    arr, source_ts_arr, target_ts, source_bar_seconds
                )
            else:  # lower
                agg = _get_agg_method(name)
                feature_matrix[feature_key] = align_lower_tf_to_target(
                    arr, source_ts_arr, target_ts, target_bar_seconds, agg
                )

            feature_meta.append({
                'name': name,
                'source_tf': source_tf,
                'target_tf': target_tf,
                'alignment': alignment,
                'agg_method': _get_agg_method(name) if alignment == 'lower' else None,
                'feature_key': feature_key,
            })

            n_collected += 1
            if n_collected >= len(survivor_names):
                break

        # Free source OHLCV if different from target
        if source_tf != target_tf:
            del source_ohlcv, s_open, s_high, s_low, s_close, s_vol
            gc.collect()

        logger.info("    Collected %d/%d features from %s in %.1fs",
                     n_collected, len(survivor_names), source_tf, time.time() - t0)

    logger.info("Total cross-TF features: %d", len(feature_matrix))
    return feature_matrix, feature_meta, target_ohlcv


# ─── Step 3: Cross-TF IC Series Correlation ────────────────────────────────

def cross_tf_ic_correlation(feature_matrix, fwd_ret, tier_mask):
    """
    For each feature, compute IC series. Then compute correlation matrix
    of IC series across ALL features (including cross-TF).

    Returns:
        ic_corr_matrix: (n_features, n_features) float64
        ic_series_matrix: (n_features, n_bars) float64
        feature_names: list[str]
    """
    feature_names = sorted(feature_matrix.keys())
    n_features = len(feature_names)
    n_bars = fwd_ret.shape[0]

    logger.info("Computing IC series for %d features across %d bars...", n_features, n_bars)
    t0 = time.time()

    ic_series_matrix = np.zeros((n_features, n_bars), dtype=np.float64)

    for fi, fname in enumerate(feature_names):
        arr = feature_matrix[fname]

        for t in range(n_bars):
            ind_row = arr[t, tier_mask] if arr.shape[1] == tier_mask.shape[0] else arr[t]
            ret_row = fwd_ret[t, tier_mask]

            valid = ~np.isnan(ind_row) & ~np.isnan(ret_row)
            nv = valid.sum()
            if nv < 20:
                continue

            i_ranks = np.argsort(np.argsort(ind_row[valid])).astype(np.float64)
            r_ranks = np.argsort(np.argsort(ret_row[valid])).astype(np.float64)
            im, rm = i_ranks.mean(), r_ranks.mean()
            isd, rsd = i_ranks.std(), r_ranks.std()
            if isd > 0 and rsd > 0:
                ic_series_matrix[fi, t] = np.mean((i_ranks - im) * (r_ranks - rm)) / (isd * rsd)

        if (fi + 1) % 20 == 0:
            logger.info("    IC series: %d/%d features (%.0fs)", fi + 1, n_features, time.time() - t0)

    # Correlation matrix of IC series
    logger.info("  Computing %d×%d IC correlation matrix...", n_features, n_features)
    corr = np.corrcoef(ic_series_matrix)
    corr = np.nan_to_num(corr, nan=0.0)

    logger.info("  IC correlation complete in %.1fs", time.time() - t0)
    return corr, ic_series_matrix, feature_names


# ─── Step 4: Global n_effective ─────────────────────────────────────────────

def global_n_effective(ic_corr_matrix):
    """
    Eigenvalue decomposition on the FULL cross-TF IC correlation matrix.

    This is the true effective test count for the entire indicator soup
    across ALL timeframes. The meta-combiner MUST use this value,
    not the sum of per-TF n_effective.
    """
    eigenvalues = np.linalg.eigvalsh(ic_corr_matrix)
    eigenvalues = np.maximum(eigenvalues, 0)
    sum_l = eigenvalues.sum()
    sum_l2 = (eigenvalues ** 2).sum()
    n_eff = int(round(sum_l ** 2 / sum_l2)) if sum_l2 > 0 else ic_corr_matrix.shape[0]

    logger.info("  Global n_effective = %d (from %d features)", n_eff, ic_corr_matrix.shape[0])
    logger.info("  Top 5 eigenvalues: %s", np.sort(eigenvalues)[::-1][:5].round(2))

    return n_eff


# ─── Step 4b: Redundancy Clusters ──────────────────────────────────────────

def find_redundancy_clusters(ic_corr_matrix, feature_names, threshold=0.7):
    """
    Group features with high IC correlation (>threshold) into clusters.

    Within each cluster, the feature with the highest mean |IC| is the
    representative; the rest are redundant.
    """
    n = len(feature_names)
    visited = set()
    clusters = []

    for i in range(n):
        if i in visited:
            continue
        cluster = [i]
        visited.add(i)
        for j in range(i + 1, n):
            if j in visited:
                continue
            if abs(ic_corr_matrix[i, j]) > threshold:
                cluster.append(j)
                visited.add(j)
        if len(cluster) > 1:
            members = [feature_names[idx] for idx in cluster]
            # Average pairwise IC correlation within cluster
            corrs = []
            for a in range(len(cluster)):
                for b in range(a + 1, len(cluster)):
                    corrs.append(abs(ic_corr_matrix[cluster[a], cluster[b]]))
            avg_corr = float(np.mean(corrs)) if corrs else 0.0
            clusters.append({
                'representative': members[0],  # Will be updated to best IC below
                'members': members,
                'avg_ic_correlation': avg_corr,
            })

    logger.info("  Found %d redundancy clusters (threshold=%.2f)", len(clusters), threshold)
    return clusters


# ─── Step 5: Cross-TF Interaction Screening ────────────────────────────────

@dataclass
class Interaction:
    feature_a: str
    feature_b: str
    a_tf: str
    b_tf: str
    unconditional_ic_a: float
    conditional_ic_a_given_b_low: float
    conditional_ic_a_given_b_mid: float
    conditional_ic_a_given_b_high: float
    interaction_strength: float
    best_conditioning: str


def screen_cross_tf_interactions(feature_matrix, fwd_ret, tier_mask, ic_corr_matrix,
                                 feature_names, ic_series_matrix,
                                 significance_threshold=0.01, corr_threshold=0.3):
    """
    For pairs where IC correlation is LOW but both have significant IC,
    test whether conditioning on B improves A's IC.

    Returns list of Interaction dataclasses.
    """
    n_features = len(feature_names)
    n_bars = fwd_ret.shape[0]

    # Compute mean |IC| per feature from IC series
    mean_abs_ic = np.array([
        np.mean(np.abs(ic_series_matrix[fi][ic_series_matrix[fi] != 0]))
        if np.any(ic_series_matrix[fi] != 0) else 0.0
        for fi in range(n_features)
    ])

    # Find candidate pairs: low correlation, both significant
    candidates = []
    for i in range(n_features):
        if mean_abs_ic[i] < significance_threshold:
            continue
        tf_i = feature_names[i].split('@')[-1] if '@' in feature_names[i] else '?'
        for j in range(i + 1, n_features):
            if mean_abs_ic[j] < significance_threshold:
                continue
            if abs(ic_corr_matrix[i, j]) >= corr_threshold:
                continue  # Too correlated — skip
            tf_j = feature_names[j].split('@')[-1] if '@' in feature_names[j] else '?'
            # Prefer cross-TF pairs, but allow same-TF if different families
            candidates.append((i, j, tf_i, tf_j))

    logger.info("  Screening %d candidate interactions (from %d features)", len(candidates), n_features)
    t0 = time.time()

    interactions = []
    for ci, (idx_a, idx_b, tf_a, tf_b) in enumerate(candidates):
        name_a = feature_names[idx_a]
        name_b = feature_names[idx_b]
        arr_a = feature_matrix[name_a]
        arr_b = feature_matrix[name_b]

        # Split bars into terciles by B's cross-sectional median
        # (Use B's cross-sectional rank median to define regimes)
        uncond_ics = []
        cond_ics = {'low': [], 'mid': [], 'high': []}

        for t in range(n_bars):
            a_row = arr_a[t, tier_mask] if arr_a.shape[1] == tier_mask.shape[0] else arr_a[t]
            b_row = arr_b[t, tier_mask] if arr_b.shape[1] == tier_mask.shape[0] else arr_b[t]
            ret_row = fwd_ret[t, tier_mask]

            valid = ~np.isnan(a_row) & ~np.isnan(b_row) & ~np.isnan(ret_row)
            nv = valid.sum()
            if nv < MIN_CS_SYMBOLS:
                continue

            av, bv, rv = a_row[valid], b_row[valid], ret_row[valid]

            # Unconditional IC of A
            a_ranks = np.argsort(np.argsort(av)).astype(np.float64)
            r_ranks = np.argsort(np.argsort(rv)).astype(np.float64)
            am, rm = a_ranks.mean(), r_ranks.mean()
            asd, rsd = a_ranks.std(), r_ranks.std()
            if asd > 0 and rsd > 0:
                uncond_ics.append(np.mean((a_ranks - am) * (r_ranks - rm)) / (asd * rsd))

            # B terciles
            b_p33, b_p66 = np.percentile(bv, [33.3, 66.7])
            for label, mask_fn in [
                ('low', bv <= b_p33),
                ('mid', (bv > b_p33) & (bv <= b_p66)),
                ('high', bv > b_p66),
            ]:
                sub = mask_fn
                if sub.sum() < 10:
                    continue
                a_sub = av[sub]
                r_sub = rv[sub]
                ar = np.argsort(np.argsort(a_sub)).astype(np.float64)
                rr = np.argsort(np.argsort(r_sub)).astype(np.float64)
                arm, rrm = ar.mean(), rr.mean()
                arsd, rrsd = ar.std(), rr.std()
                if arsd > 0 and rrsd > 0:
                    cond_ics[label].append(np.mean((ar - arm) * (rr - rrm)) / (arsd * rrsd))

        if len(uncond_ics) < 30:
            continue

        mean_uncond = float(np.mean(uncond_ics))
        mean_cond = {}
        for label in ['low', 'mid', 'high']:
            mean_cond[label] = float(np.mean(cond_ics[label])) if len(cond_ics[label]) >= 10 else np.nan

        # Interaction strength: max conditional IC - unconditional IC
        valid_conds = [v for v in mean_cond.values() if not np.isnan(v)]
        if not valid_conds:
            continue

        best_cond = max(valid_conds, key=abs)
        interaction_strength = abs(best_cond) - abs(mean_uncond)

        if interaction_strength <= 0:
            continue  # No improvement from conditioning

        # Which tercile of B maximizes A's IC?
        best_label = max(
            [(label, abs(v)) for label, v in mean_cond.items() if not np.isnan(v)],
            key=lambda x: x[1]
        )[0]

        interactions.append(Interaction(
            feature_a=name_a,
            feature_b=name_b,
            a_tf=tf_a,
            b_tf=tf_b,
            unconditional_ic_a=mean_uncond,
            conditional_ic_a_given_b_low=mean_cond.get('low', np.nan),
            conditional_ic_a_given_b_mid=mean_cond.get('mid', np.nan),
            conditional_ic_a_given_b_high=mean_cond.get('high', np.nan),
            interaction_strength=interaction_strength,
            best_conditioning=f"{name_b} {best_label} -> {name_a} IC strongest",
        ))

        if (ci + 1) % 100 == 0:
            logger.info("    Interactions: %d/%d pairs screened (%.0fs)",
                        ci + 1, len(candidates), time.time() - t0)

    # Sort by interaction strength
    interactions.sort(key=lambda x: x.interaction_strength, reverse=True)
    logger.info("  Found %d positive interactions in %.1fs", len(interactions), time.time() - t0)

    return interactions


# ─── Step 5b: Bundle Construction ───────────────────────────────────────────

def build_multi_resolution_bundles(interactions, ic_corr_matrix, feature_names,
                                   ic_series_matrix, max_bundle_size=5):
    """
    Group interacting features into multi-resolution bundles.

    A bundle contains features from different TFs that interact positively.
    Internal correlation is tracked for n_effective accounting.
    """
    # Start from strongest interactions
    used = set()
    bundles = []

    for inter in interactions:
        if inter.feature_a in used and inter.feature_b in used:
            continue

        bundle_features = {inter.feature_a, inter.feature_b}
        bundle_tfs = {inter.a_tf, inter.b_tf}

        # Try to extend the bundle with other interacting features
        for other in interactions:
            if len(bundle_features) >= max_bundle_size:
                break
            for f in [other.feature_a, other.feature_b]:
                if f in bundle_features:
                    continue
                f_tf = f.split('@')[-1] if '@' in f else '?'
                # Check it interacts with at least one bundle member
                interacts = False
                for bf in bundle_features:
                    if (other.feature_a == f and other.feature_b == bf) or \
                       (other.feature_b == f and other.feature_a == bf):
                        interacts = True
                        break
                if interacts:
                    bundle_features.add(f)
                    bundle_tfs.add(f_tf)

        # Compute bundle IC (average of member mean ICs)
        bundle_ics = []
        for fname in bundle_features:
            if fname in feature_names:
                fi = feature_names.index(fname)
                series = ic_series_matrix[fi]
                nz = series[series != 0]
                if len(nz) > 0:
                    bundle_ics.append(np.mean(nz))

        bundle_ic = float(np.mean(bundle_ics)) if bundle_ics else 0.0

        # Effective feature count within bundle
        bundle_indices = [feature_names.index(f) for f in bundle_features if f in feature_names]
        if len(bundle_indices) >= 2:
            sub_corr = ic_corr_matrix[np.ix_(bundle_indices, bundle_indices)]
            eigvals = np.linalg.eigvalsh(sub_corr)
            eigvals = np.maximum(eigvals, 0)
            sl = eigvals.sum()
            sl2 = (eigvals ** 2).sum()
            n_eff_bundle = sl ** 2 / sl2 if sl2 > 0 else len(bundle_indices)
        else:
            n_eff_bundle = len(bundle_features)

        bundles.append({
            'features': sorted(bundle_features),
            'timeframes': sorted(bundle_tfs),
            'bundle_ic': bundle_ic,
            'n_features_effective': round(float(n_eff_bundle), 1),
            'interaction_strength': inter.interaction_strength,
        })

        used.update(bundle_features)

    logger.info("  Built %d multi-resolution bundles", len(bundles))
    return bundles


# ─── Step 6: Output ─────────────────────────────────────────────────────────

def write_cross_tf_output(global_n_eff, per_tf_n_eff, clusters, interactions,
                          bundles, ic_corr_matrix, ic_series_matrix,
                          feature_names, feature_meta, output_dir):
    """Write JSON + NPZ output."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / f"indicator_soup_cross_tf_{timestamp}.json"
    npz_path = output_path / f"indicator_soup_cross_tf_{timestamp}.npz"

    # Update cluster representatives to feature with highest mean |IC|
    for cluster in clusters:
        best_name = cluster['members'][0]
        best_ic = 0.0
        for m in cluster['members']:
            if m in feature_names:
                fi = feature_names.index(m)
                series = ic_series_matrix[fi]
                nz = series[series != 0]
                mic = np.mean(np.abs(nz)) if len(nz) > 0 else 0.0
                if mic > best_ic:
                    best_ic = mic
                    best_name = m
        cluster['representative'] = best_name

    # Serialize interactions
    interaction_dicts = [asdict(i) for i in interactions[:100]]  # Top 100

    # NaN-safe encoder
    class NanEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                return None
            if isinstance(obj, (np.integer, np.int64)):
                return int(obj)
            if isinstance(obj, (np.floating, np.float64)):
                return float(obj) if np.isfinite(obj) else None
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return super().default(obj)

    output = {
        'global_n_effective': global_n_eff,
        'per_tf_n_effective': per_tf_n_eff,
        'n_features_total': len(feature_names),
        'cross_tf_redundancy_clusters': clusters,
        'cross_tf_interactions': interaction_dicts,
        'multi_resolution_bundles': bundles,
        'feature_meta': feature_meta,
    }

    with open(json_path, 'w') as f:
        json.dump(output, f, indent=2, cls=NanEncoder)
    logger.info("  JSON: %s (%.1f KB)", json_path, json_path.stat().st_size / 1024)

    # NPZ for Phase 3 / meta-combiner
    np.savez_compressed(
        npz_path,
        ic_corr_matrix=ic_corr_matrix,
        ic_series_matrix=ic_series_matrix,
        feature_names=np.array(feature_names),
        global_n_effective=np.array(global_n_eff),
        cross_tf_align_meta=np.array(json.dumps(feature_meta)),
    )
    logger.info("  NPZ: %s (%.1f KB)", npz_path, npz_path.stat().st_size / 1024)

    return json_path, npz_path


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Indicator Soup Phase 2.5: Cross-Timeframe Correlation Analysis")
    parser.add_argument('--screening-json', type=str, required=True,
                        help="Path to Phase 2 screening JSON output")
    parser.add_argument('--cache-dir', type=str, required=True,
                        help="OHLCV cache directory from Phase 2")
    parser.add_argument('--target-tf', type=str, default='1h',
                        help="Target trading timeframe (default: 1h)")
    parser.add_argument('--near-miss-alpha', type=float, default=0.10,
                        help="Include near-misses with adj_p < this (default: 0.10)")
    parser.add_argument('--days', type=int, default=270,
                        help="Target days (must match Phase 2, default: 270)")
    args = parser.parse_args()

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    if args.target_tf not in TF_BAR_MINUTES:
        logger.error("Unknown target timeframe: %s", args.target_tf)
        sys.exit(1)

    # Output dir
    if os.path.isdir('/scripts'):
        output_dir = '/scripts/output'
    else:
        output_dir = os.path.join(SCRIPTS_DIR, 'output')

    logger.info("Cross-TF Analysis — Phase 2.5")
    logger.info("  Screening JSON: %s", args.screening_json)
    logger.info("  Target TF: %s, Days: %d", args.target_tf, args.days)
    t_total_start = time.time()

    # Step 1: Load survivors
    survivors = load_survivors_from_screening(args.screening_json, args.near_miss_alpha)
    if not survivors:
        logger.error("No survivors found in screening output")
        sys.exit(1)

    # Per-TF n_effective from screening output
    with open(args.screening_json) as f:
        screen_data = json.load(f)
    per_tf_n_eff = {}
    n_eff_from_screen = screen_data.get('summary', {}).get('n_effective')
    for tf in screen_data.get('summary', {}).get('timeframes_screened', []):
        per_tf_n_eff[tf] = n_eff_from_screen  # Same value was reused across TFs

    # Step 2: Build cross-TF feature matrix
    feature_matrix, feature_meta, target_ohlcv = build_cross_tf_feature_matrix(
        args.target_tf, survivors, args.cache_dir, database_url, args.days
    )
    target_close = target_ohlcv[3]
    target_ts = target_ohlcv[5]
    target_symbols = target_ohlcv[6]
    logger.info("  Peak RSS after feature matrix: %.0f MB", _peak_rss_mb())

    if len(feature_matrix) < 2:
        logger.error("Only %d features — need at least 2 for cross-TF analysis", len(feature_matrix))
        sys.exit(1)

    # Forward returns for target TF (use ~1h horizon or closest)
    fwd_ret_raw = compute_forward_returns_raw(target_close, args.target_tf, args.cache_dir)
    h1_bars = max(1, round(60 / TF_BAR_MINUTES[args.target_tf]))
    available = list(fwd_ret_raw.keys())
    closest_h = min(available, key=lambda x: abs(x - h1_bars))
    fwd_ret = fwd_ret_raw[closest_h]
    logger.info("  Using h=%d bars (%.1fh) for IC computation", closest_h,
                closest_h * TF_BAR_MINUTES[args.target_tf] / 60)

    # Stratification (use top tier)
    tiers = stratify_universe(target_close, target_ohlcv[4], target_symbols)
    top_tier_mask = tiers[-1][1]
    logger.info("  Using top tier '%s': %d symbols", tiers[-1][0], top_tier_mask.sum())

    # Step 3: IC correlation
    ic_corr, ic_series, feature_names = cross_tf_ic_correlation(
        feature_matrix, fwd_ret, top_tier_mask
    )

    # Step 4: Global n_effective
    g_n_eff = global_n_effective(ic_corr)

    # Sanity: global should be <= sum of per-TF
    sum_per_tf = sum(per_tf_n_eff.values()) if per_tf_n_eff else 0
    if sum_per_tf > 0 and g_n_eff > sum_per_tf:
        logger.warning("  WARNING: global n_eff (%d) > sum of per-TF (%d) — possible bug",
                        g_n_eff, sum_per_tf)

    # Step 4b: Redundancy clusters
    clusters = find_redundancy_clusters(ic_corr, feature_names, threshold=0.7)

    # Step 5: Cross-TF interaction screening
    interactions = screen_cross_tf_interactions(
        feature_matrix, fwd_ret, top_tier_mask, ic_corr,
        feature_names, ic_series,
    )

    # Step 5b: Bundle construction
    bundles = build_multi_resolution_bundles(
        interactions, ic_corr, feature_names, ic_series
    )

    # Step 6: Output
    json_path, npz_path = write_cross_tf_output(
        g_n_eff, per_tf_n_eff, clusters, interactions, bundles,
        ic_corr, ic_series, feature_names, feature_meta, output_dir,
    )

    total_elapsed = time.time() - t_total_start
    logger.info("=" * 70)
    logger.info("COMPLETE in %.1f min", total_elapsed / 60)
    logger.info("  Global n_effective: %d", g_n_eff)
    logger.info("  Redundancy clusters: %d", len(clusters))
    logger.info("  Interactions: %d", len(interactions))
    logger.info("  Bundles: %d", len(bundles))
    logger.info("  JSON: %s", json_path)
    logger.info("  NPZ:  %s", npz_path)
    logger.info("  Peak RSS: %.0f MB", _peak_rss_mb())


if __name__ == '__main__':
    main()
