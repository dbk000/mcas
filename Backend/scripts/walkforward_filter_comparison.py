#!/usr/bin/env python3
"""
Walk-Forward Filter Comparison: Onchain + Sentiment precision filters
=====================================================================

Tests top on-chain and sentiment features as precision filters on ignition
signals using 4-fold walk-forward validation with 1-day embargo.

For each candidate feature:
  - Builds a filter that accepts/rejects signals based on feature value
  - Threshold determined from training fold (adaptive median)
  - Evaluates precision improvement, TP rejection rate, avg daily PF

Output: /results/filter_walkforward_comparison.json

Run:
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T \
        -v /srv/mcas/results:/results worker python3 /scripts/walkforward_filter_comparison.py
"""

import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------
IN_DOCKER = os.path.isdir("/app")
if IN_DOCKER:
    RESULTS_DIR = "/results"
    SCRIPTS_DIR = "/scripts"
    DB_URL = os.environ.get(
        "DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading"
    )
else:
    RESULTS_DIR = "/srv/mcas/results"
    SCRIPTS_DIR = "/srv/mcas/Backend/scripts"
    DB_URL = os.environ.get(
        "DATABASE_URL", "postgresql://trading:trading@localhost:5432/trading"
    )

BASELINE_CSV = os.path.join(RESULTS_DIR, "ignition_events_with_outcomes.csv")
ONCHAIN_SCREEN = os.path.join(RESULTS_DIR, "feature_screen_onchain.json")
SENTIMENT_SCREEN = os.path.join(RESULTS_DIR, "feature_screen_sentiment.json")
OUTPUT_JSON = os.path.join(RESULTS_DIR, "filter_walkforward_comparison.json")

# Whale / exchange_flows data ends at 2026-01-21
WHALE_EXCHANGE_CUTOFF = datetime(2026, 1, 21, tzinfo=timezone.utc)

N_FOLDS = 4
EMBARGO_HOURS = 24


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_connection():
    import psycopg
    return psycopg.connect(DB_URL, autocommit=True)


def query_rows(sql: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return cols, rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rolling window computations (vectorised where possible)
# ---------------------------------------------------------------------------
def rolling_mean(arr, window, min_periods=None):
    if min_periods is None:
        min_periods = max(window // 2, 2)
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(n):
        start = max(0, i - window + 1)
        chunk = arr[start:i+1]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) >= min_periods:
            result[i] = valid.mean()
    return result


def rolling_std(arr, window, min_periods=None):
    if min_periods is None:
        min_periods = max(window // 2, 2)
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(n):
        start = max(0, i - window + 1)
        chunk = arr[start:i+1]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) >= min_periods:
            result[i] = valid.std(ddof=1) if len(valid) > 1 else 0.0
    return result


def rolling_sum(arr, window, min_periods=3):
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(n):
        start = max(0, i - window + 1)
        chunk = arr[start:i+1]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) >= min_periods:
            result[i] = valid.sum()
    return result


def compute_zscore(arr, window):
    rm = rolling_mean(arr, window)
    rs = rolling_std(arr, window)
    rs[rs == 0] = np.nan
    return (arr - rm) / rs


def compute_ratio(arr, window):
    rm = rolling_mean(arr, window)
    rm[rm == 0] = np.nan
    return arr / rm


def compute_roc(arr, periods):
    n = len(arr)
    result = np.full(n, np.nan)
    if periods < n:
        lag = arr[:-periods] if periods > 0 else arr
        cur = arr[periods:]
        with np.errstate(divide='ignore', invalid='ignore'):
            lag_safe = lag.copy()
            lag_safe[lag_safe == 0] = np.nan
            roc = (cur - lag) / np.abs(lag_safe)
        result[periods:] = roc
    return result


def engineer_features_for_symbol(vals, prefix):
    features = {}
    for w in [7, 14, 30]:
        features[f"{prefix}_zscore_{w}d"] = compute_zscore(vals, w)
    features[f"{prefix}_ratio_30d"] = compute_ratio(vals, 30)
    for p in [1, 7]:
        features[f"{prefix}_roc_{p}d"] = compute_roc(vals, p)
    return features


# ---------------------------------------------------------------------------
# Load baseline
# ---------------------------------------------------------------------------
def load_baseline():
    log(f"Loading baseline from {BASELINE_CSV}")
    t0 = time.time()

    with open(BASELINE_CSV, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    n = len(rows)
    symbols = [r['symbol'] for r in rows]
    event_datetimes = []
    event_dates = []
    for r in rows:
        dt = datetime.strptime(r['event_ts'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        event_datetimes.append(dt)
        event_dates.append(dt.strftime("%Y-%m-%d"))

    def sf(val):
        try:
            return float(val) if val != '' else np.nan
        except (ValueError, TypeError):
            return np.nan

    is_ignition = np.array([sf(r['is_ignition']) for r in rows])
    fwd_ret_4h = np.array([sf(r['fwd_ret_4h']) for r in rows])
    volume = np.array([sf(r['volume']) for r in rows])

    n_ign = int(np.nansum(is_ignition))
    log(f"  Loaded {n:,} rows, {n_ign:,} ignition events, {len(set(symbols))} symbols in {time.time()-t0:.1f}s")

    return {
        'symbols': symbols,
        'event_dates': event_dates,
        'event_datetimes': event_datetimes,
        'is_ignition': is_ignition,
        'fwd_ret_4h': fwd_ret_4h,
        'volume': volume,
        'n': n,
        'rows': rows,  # keep raw rows for harness compatibility
    }


# ---------------------------------------------------------------------------
# Load on-chain tables
# ---------------------------------------------------------------------------
def load_onchain_table(table_name, columns):
    log(f"Loading {table_name} from Postgres...")
    t0 = time.time()
    col_sql = ", ".join(columns)
    sql = f"SELECT symbol, timestamp, {col_sql} FROM {table_name} ORDER BY symbol, timestamp"
    _, rows = query_rows(sql)

    by_symbol = {}
    for row in rows:
        sym = row[0]
        ts = row[1]
        if sym not in by_symbol:
            by_symbol[sym] = {'dates': [], 'date_strs': []}
            for c in columns:
                by_symbol[sym][c] = []
        by_symbol[sym]['dates'].append(ts)
        by_symbol[sym]['date_strs'].append(ts.strftime("%Y-%m-%d"))
        for i, c in enumerate(columns):
            val = row[2 + i]
            by_symbol[sym][c].append(float(val) if val is not None else np.nan)

    for sym in by_symbol:
        for c in columns:
            by_symbol[sym][c] = np.array(by_symbol[sym][c], dtype=np.float64)

    log(f"  Loaded {len(rows):,} rows, {len(by_symbol)} symbols in {time.time()-t0:.1f}s")
    return by_symbol


# ---------------------------------------------------------------------------
# Build on-chain feature lookups: (symbol, date_str) -> value
# ---------------------------------------------------------------------------
def build_onchain_feature_lookups(needed_features: set):
    """Build only the features we need. Returns dict: feat_name -> {(sym, date): val}"""
    log(f"Building on-chain feature lookups for {len(needed_features)} features...")
    t0 = time.time()

    # Determine which tables we need
    need_net = any(f.startswith(('net_', )) for f in needed_features)
    need_whale = any(f.startswith('whale_') for f in needed_features)
    need_exch = any(f.startswith('exch_') for f in needed_features)
    need_cross = any(f.startswith('cross_') for f in needed_features)

    net_data = whale_data = exch_data = None

    if need_net or need_cross:
        net_data = load_onchain_table(
            "network_activity",
            ["active_addresses_24h", "network_growth", "transaction_volume",
             "dev_activity", "social_volume"]
        )
    if need_whale or need_cross:
        whale_data = load_onchain_table("whale_transactions", ["whale_tx_count"])
    if need_exch or need_cross:
        exch_data = load_onchain_table(
            "exchange_flows",
            ["exchange_inflow", "exchange_outflow", "exchange_netflow"]
        )

    all_features = {}

    # Network activity features
    if need_net and net_data:
        prefix_map = {
            "active_addresses_24h": "net_addr",
            "network_growth": "net_netgrowth",
            "transaction_volume": "net_txvol",
            "dev_activity": "net_devact",
            "social_volume": "net_socvol",
        }
        sym_count = 0
        for sym, data in net_data.items():
            sym_count += 1
            if sym_count % 100 == 0:
                log(f"    Net features: symbol {sym_count}/{len(net_data)}")
            date_strs = data['date_strs']
            for col, prefix in prefix_map.items():
                vals = data[col]
                if np.sum(~np.isnan(vals)) < 10:
                    continue
                feats = engineer_features_for_symbol(vals, prefix)
                for feat_name, feat_arr in feats.items():
                    if feat_name not in needed_features:
                        continue
                    if feat_name not in all_features:
                        all_features[feat_name] = {}
                    for i in range(len(date_strs)):
                        if not np.isnan(feat_arr[i]):
                            all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

    # Whale features
    if need_whale and whale_data:
        sym_count = 0
        for sym, data in whale_data.items():
            sym_count += 1
            if sym_count % 100 == 0:
                log(f"    Whale features: symbol {sym_count}/{len(whale_data)}")
            date_strs = data['date_strs']
            vals = data['whale_tx_count']
            if np.sum(~np.isnan(vals)) < 10:
                continue
            feats = engineer_features_for_symbol(vals, "whale_txcount")
            for feat_name, feat_arr in feats.items():
                if feat_name not in needed_features:
                    continue
                if feat_name not in all_features:
                    all_features[feat_name] = {}
                for i in range(len(date_strs)):
                    if not np.isnan(feat_arr[i]):
                        all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

    # Exchange features
    if need_exch and exch_data:
        sym_count = 0
        for sym, data in exch_data.items():
            sym_count += 1
            if sym_count % 100 == 0:
                log(f"    Exchange features: symbol {sym_count}/{len(exch_data)}")
            date_strs = data['date_strs']
            inflow = data['exchange_inflow']
            outflow = data['exchange_outflow']
            netflow = data['exchange_netflow']
            if np.sum(~np.isnan(inflow)) < 10:
                continue

            # outflow z-scores
            for w in [7, 14]:
                feat_name = f"exch_outflow_zscore_{w}d"
                if feat_name in needed_features:
                    feat_arr = compute_zscore(outflow, w)
                    if feat_name not in all_features:
                        all_features[feat_name] = {}
                    for i in range(len(date_strs)):
                        if not np.isnan(feat_arr[i]):
                            all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

            # inflow z-scores
            for w in [7, 14]:
                feat_name = f"exch_inflow_zscore_{w}d"
                if feat_name in needed_features:
                    feat_arr = compute_zscore(inflow, w)
                    if feat_name not in all_features:
                        all_features[feat_name] = {}
                    for i in range(len(date_strs)):
                        if not np.isnan(feat_arr[i]):
                            all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

            # accumulation signal
            if "exch_accum_signal" in needed_features:
                denom = outflow + inflow
                with np.errstate(divide='ignore', invalid='ignore'):
                    denom_safe = np.where(denom == 0, np.nan, denom)
                    accum = (outflow - inflow) / denom_safe
                feat_name = "exch_accum_signal"
                if feat_name not in all_features:
                    all_features[feat_name] = {}
                for i in range(len(date_strs)):
                    if not np.isnan(accum[i]):
                        all_features[feat_name][(sym, date_strs[i])] = accum[i]

    # Cross-table features
    if need_cross and net_data and whale_data and exch_data:
        log("    Building cross-table features...")
        addr_lookup = {}
        txvol_lookup = {}
        for sym, data in net_data.items():
            for i, ds in enumerate(data['date_strs']):
                v = data['active_addresses_24h'][i]
                if not np.isnan(v):
                    addr_lookup[(sym, ds)] = v
                v2 = data['transaction_volume'][i]
                if not np.isnan(v2):
                    txvol_lookup[(sym, ds)] = v2

        whale_lookup = {}
        for sym, data in whale_data.items():
            for i, ds in enumerate(data['date_strs']):
                v = data['whale_tx_count'][i]
                if not np.isnan(v):
                    whale_lookup[(sym, ds)] = v

        inflow_lookup = {}
        for sym, data in exch_data.items():
            for i, ds in enumerate(data['date_strs']):
                v2 = data['exchange_inflow'][i]
                if not np.isnan(v2):
                    inflow_lookup[(sym, ds)] = v2

        all_keys = set(addr_lookup.keys()) | set(whale_lookup.keys())

        if "cross_whale_per_addr" in needed_features:
            all_features["cross_whale_per_addr"] = {}
        if "cross_netflow_per_txvol" in needed_features:
            all_features["cross_netflow_per_txvol"] = {}
        if "cross_inflow_per_addr" in needed_features:
            all_features["cross_inflow_per_addr"] = {}

        for key in all_keys:
            addr = addr_lookup.get(key)
            whale = whale_lookup.get(key)
            inflow_val = inflow_lookup.get(key)

            if "cross_whale_per_addr" in needed_features and whale is not None and addr is not None and addr > 0:
                all_features["cross_whale_per_addr"][key] = whale / addr
            if "cross_inflow_per_addr" in needed_features and inflow_val is not None and addr is not None and addr > 0:
                all_features["cross_inflow_per_addr"][key] = inflow_val / addr

    log(f"  Built {len(all_features)} on-chain features in {time.time()-t0:.1f}s")
    for fn, lk in all_features.items():
        log(f"    {fn}: {len(lk):,} observations")

    return all_features


# ---------------------------------------------------------------------------
# Build sentiment feature lookups: (symbol, event_ts_str) -> value
# ---------------------------------------------------------------------------
def build_sentiment_feature_lookups(needed_features: set, baseline: dict):
    """
    Sentiment features from social_features_1h. The screen computed:
      - velocity_24h_max: max mention_velocity in 24h before event
      - mention_count: exact match at event hour
      - unique_authors: exact match at event hour
      - mention_volume_ratio_24h_max: max mention_volume_ratio in 24h before event
      - engagement_24h_sum: sum engagement_sum over 24h before event
      - platform_count: exact match at event hour

    We reconstruct these using the same logic. The join key is (symbol, bar_start_ts)
    where bar_start_ts matches event_ts for hourly direct features, or
    24h lookback aggregations.
    """
    log(f"Building sentiment feature lookups for {len(needed_features)} features...")
    t0 = time.time()

    # Determine what raw columns we need
    need_hourly_direct = bool(needed_features & {'mention_count', 'unique_authors', 'platform_count',
                                                  'engagement_sum', 'author_novelty_ratio',
                                                  'mention_burst_ratio'})
    need_24h_lookback = bool(needed_features & {'velocity_24h_max', 'mention_volume_ratio_24h_max',
                                                 'engagement_24h_sum', 'mention_count_24h_sum',
                                                 'platform_breadth_24h', 'source_diversity_24h',
                                                 'engagement_per_mention_24h',
                                                 'mention_acceleration_24h_mean'})

    # Load social_features_1h into lookup by (symbol, ts_str) -> row dict
    log("  Loading social_features_1h from Postgres...")
    sql = """SELECT symbol, bar_start_ts, mention_count, unique_authors, platform_count,
                    engagement_sum, mention_velocity, mention_acceleration,
                    author_novelty_ratio, mention_volume_ratio
             FROM social_features_1h ORDER BY symbol, bar_start_ts"""
    _, rows = query_rows(sql)
    log(f"  Loaded {len(rows):,} social_features_1h rows")

    # Build per-symbol time series for 24h lookback
    # Index: symbol -> [(ts, {col: val}), ...]
    by_symbol = defaultdict(list)
    for row in rows:
        sym, ts = row[0], row[1]
        rec = {
            'mention_count': row[2] or 0,
            'unique_authors': row[3] or 0,
            'platform_count': row[4] or 0,
            'engagement_sum': row[5] or 0,
            'mention_velocity': row[6],
            'mention_acceleration': row[7],
            'author_novelty_ratio': row[8],
            'mention_volume_ratio': row[9],
        }
        by_symbol[sym].append((ts, rec))

    # Build direct hourly lookup: (symbol, ts_str) -> rec
    hourly_lookup = {}
    for row in rows:
        sym, ts = row[0], row[1]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        hourly_lookup[(sym, ts_str)] = {
            'mention_count': row[2] or 0,
            'unique_authors': row[3] or 0,
            'platform_count': row[4] or 0,
            'engagement_sum': row[5] or 0,
            'mention_velocity': row[6],
            'mention_acceleration': row[7],
            'author_novelty_ratio': row[8],
            'mention_volume_ratio': row[9],
        }

    # For 24h lookback features, we need to aggregate over 24 hours before each event
    # Build per-symbol sorted arrays for efficient lookback
    log("  Building 24h lookback aggregates...")
    sym_ts_arrays = {}
    for sym in by_symbol:
        entries = sorted(by_symbol[sym], key=lambda x: x[0])
        sym_ts_arrays[sym] = entries

    all_features = {}
    for fn in needed_features:
        all_features[fn] = {}

    # Process each baseline event
    symbols = baseline['symbols']
    event_dts = baseline['event_datetimes']
    n = baseline['n']

    progress_interval = n // 10

    for idx in range(n):
        if idx % progress_interval == 0 and idx > 0:
            log(f"    Sentiment alignment: {idx:,}/{n:,} events ({100*idx/n:.0f}%)")

        sym = symbols[idx]
        evt_dt = event_dts[idx]
        evt_ts_str = evt_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Direct hourly features
        if need_hourly_direct:
            rec = hourly_lookup.get((sym, evt_ts_str))
            if rec is not None:
                for fn in ['mention_count', 'unique_authors', 'platform_count',
                           'engagement_sum', 'author_novelty_ratio']:
                    if fn in needed_features and rec.get(fn) is not None:
                        all_features[fn][(sym, evt_dt)] = float(rec[fn])

        # 24h lookback features
        if need_24h_lookback and sym in sym_ts_arrays:
            entries = sym_ts_arrays[sym]
            lookback_start = evt_dt - timedelta(hours=24)

            # Binary search for window [lookback_start, evt_dt)
            # Find start index
            lo, hi = 0, len(entries)
            while lo < hi:
                mid = (lo + hi) // 2
                if entries[mid][0] < lookback_start:
                    lo = mid + 1
                else:
                    hi = mid
            start_idx = lo

            # Find end index
            lo2, hi2 = start_idx, len(entries)
            while lo2 < hi2:
                mid2 = (lo2 + hi2) // 2
                if entries[mid2][0] < evt_dt:
                    lo2 = mid2 + 1
                else:
                    hi2 = mid2
            end_idx = lo2

            if end_idx > start_idx:
                window_entries = entries[start_idx:end_idx]

                if 'velocity_24h_max' in needed_features:
                    vels = [e[1].get('mention_velocity') for e in window_entries
                            if e[1].get('mention_velocity') is not None]
                    if vels:
                        all_features['velocity_24h_max'][(sym, evt_dt)] = max(vels)

                if 'mention_volume_ratio_24h_max' in needed_features:
                    mvrs = [e[1].get('mention_volume_ratio') for e in window_entries
                            if e[1].get('mention_volume_ratio') is not None]
                    if mvrs:
                        all_features['mention_volume_ratio_24h_max'][(sym, evt_dt)] = max(mvrs)

                if 'engagement_24h_sum' in needed_features:
                    engs = [e[1].get('engagement_sum', 0) for e in window_entries]
                    all_features['engagement_24h_sum'][(sym, evt_dt)] = float(sum(engs))

                if 'mention_count_24h_sum' in needed_features:
                    mcs = [e[1].get('mention_count', 0) for e in window_entries]
                    all_features['mention_count_24h_sum'][(sym, evt_dt)] = float(sum(mcs))

                if 'mention_acceleration_24h_mean' in needed_features:
                    accs = [e[1].get('mention_acceleration') for e in window_entries
                            if e[1].get('mention_acceleration') is not None]
                    if accs:
                        all_features['mention_acceleration_24h_mean'][(sym, evt_dt)] = np.mean(accs)

                if 'platform_breadth_24h' in needed_features:
                    pcs = [e[1].get('platform_count', 0) for e in window_entries]
                    all_features['platform_breadth_24h'][(sym, evt_dt)] = float(max(pcs)) if pcs else 0

    log(f"  Built {len(all_features)} sentiment features in {time.time()-t0:.1f}s")
    for fn, lk in all_features.items():
        log(f"    {fn}: {len(lk):,} observations")

    return all_features


# ---------------------------------------------------------------------------
# Spearman rank IC (for threshold optimisation)
# ---------------------------------------------------------------------------
def _rankdata(arr):
    n = len(arr)
    sorter = np.argsort(arr, kind="mergesort")
    ranks = np.empty(n, dtype=np.float64)
    sorted_arr = arr[sorter]
    i = 0
    while i < n:
        j = i + 1
        while j < n and sorted_arr[j] == sorted_arr[i]:
            j += 1
        avg_rank = 0.5 * (i + j + 1)
        for k in range(i, j):
            ranks[sorter[k]] = avg_rank
        i = j
    return ranks


# ---------------------------------------------------------------------------
# Walk-forward fold construction
# ---------------------------------------------------------------------------
def build_folds(n_signals, event_dts, n_folds=4, embargo_hours=24):
    """Build walk-forward folds based on event timestamps."""
    ts_start = event_dts[0]
    ts_end = event_dts[-1]
    total_duration = ts_end - ts_start
    if total_duration.total_seconds() <= 0:
        return []

    embargo_delta = timedelta(hours=embargo_hours)
    window_duration = total_duration / n_folds

    folds = []
    for k in range(n_folds):
        test_start = ts_start + k * window_duration
        test_end = ts_start + (k + 1) * window_duration
        train_cutoff = test_start - embargo_delta

        train_indices = []
        test_indices = []
        embargo_excluded = 0

        for i in range(n_signals):
            ts = event_dts[i]
            if ts < train_cutoff:
                if k > 0:
                    train_indices.append(i)
            elif ts < test_start:
                embargo_excluded += 1
            elif ts < test_end:
                test_indices.append(i)

        if k == 0 and len(train_indices) == 0:
            continue
        if len(test_indices) < 10 or len(train_indices) < 50:
            continue

        folds.append({
            'fold_num': k + 1,
            'train_indices': np.array(train_indices),
            'test_indices': np.array(test_indices),
            'test_start': event_dts[test_indices[0]],
            'test_end': event_dts[test_indices[-1]],
            'embargo_excluded': embargo_excluded,
        })

    return folds


# ---------------------------------------------------------------------------
# Compute metrics for a fold
# ---------------------------------------------------------------------------
def compute_fold_metrics(is_ignition, fwd_ret_4h, test_indices, accepted_mask):
    """
    Given test indices and a boolean mask of which are accepted,
    compute precision, recall, TP rejection rate, PF, avg daily PF.
    """
    test_ign = is_ignition[test_indices]
    test_ret = fwd_ret_4h[test_indices]

    total_tp = int(test_ign.sum())
    n_total = len(test_indices)
    n_accepted = int(accepted_mask.sum())
    n_rejected = n_total - n_accepted

    accepted_ign = test_ign[accepted_mask]
    accepted_ret = test_ret[accepted_mask]
    accepted_tp = int(accepted_ign.sum())
    rejected_tp = total_tp - accepted_tp

    precision = accepted_tp / n_accepted if n_accepted > 0 else 0.0
    recall = accepted_tp / total_tp if total_tp > 0 else 0.0
    tp_rejection_rate = rejected_tp / total_tp if total_tp > 0 else 0.0
    acceptance_rate = n_accepted / n_total if n_total > 0 else 0.0

    # Profit factor
    valid_ret = accepted_ret[~np.isnan(accepted_ret)]
    if len(valid_ret) > 0:
        wins = valid_ret[valid_ret > 0]
        losses = valid_ret[valid_ret < 0]
        gp = float(wins.sum()) if len(wins) > 0 else 0.0
        gl = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        agg_pf = gp / gl if gl > 0 else (5.0 if gp > 0 else 0.0)
        win_rate = len(wins) / len(valid_ret)
        avg_ret = float(valid_ret.mean())
    else:
        agg_pf = 0.0
        win_rate = 0.0
        avg_ret = 0.0

    return {
        'n_total': n_total,
        'n_accepted': n_accepted,
        'acceptance_rate': round(acceptance_rate, 4),
        'total_tp': total_tp,
        'accepted_tp': accepted_tp,
        'rejected_tp': rejected_tp,
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'tp_rejection_rate': round(tp_rejection_rate, 4),
        'aggregate_pf': round(agg_pf, 4),
        'win_rate': round(win_rate, 4),
        'avg_return_bps': round(avg_ret * 10000, 2),
    }


def compute_daily_pfs(event_dts, fwd_ret_4h, test_indices, accepted_mask):
    """Compute per-day profit factors for accepted signals."""
    by_date = defaultdict(list)
    acc_test_idx = test_indices[accepted_mask]
    for i in acc_test_idx:
        r = fwd_ret_4h[i]
        if np.isnan(r):
            continue
        d = event_dts[i].date()
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
            daily_pfs.append(5.0)
    return daily_pfs


# ---------------------------------------------------------------------------
# DSR
# ---------------------------------------------------------------------------
def compute_dsr(daily_pfs, n_trials):
    if not daily_pfs or n_trials < 1:
        return {'sharpe_ratio': 0.0, 'e_max_sr': 0.0, 'dsr_statistic': 0.0,
                'dsr_prob': 0.0, 'n_trials': n_trials, 'dsr_pass': False}

    pf_arr = np.array(daily_pfs)
    T = len(pf_arr)
    if T < 3:
        return {'sharpe_ratio': 0.0, 'e_max_sr': 0.0, 'dsr_statistic': 0.0,
                'dsr_prob': 0.0, 'n_trials': n_trials, 'dsr_pass': False}

    mean_pf = float(pf_arr.mean())
    std_pf = float(pf_arr.std(ddof=1))
    if std_pf == 0:
        sr = 0.0
    else:
        sr = (mean_pf - 1.0) / std_pf * np.sqrt(T)

    e_max_sr = math.sqrt(2 * math.log(max(n_trials, 1)))

    skew_pf = float(((pf_arr - mean_pf) ** 3).mean() / (std_pf ** 3)) if std_pf > 0 else 0
    kurt_pf = float(((pf_arr - mean_pf) ** 4).mean() / (std_pf ** 4)) - 3 if std_pf > 0 else 0
    var_sr = (1 - skew_pf * sr + (kurt_pf / 4) * sr ** 2) / T
    se_sr = math.sqrt(max(var_sr, 1e-12))

    dsr_stat = (sr - e_max_sr) / se_sr if se_sr > 0 else 0
    dsr_prob = 0.5 * (1 + math.erf(dsr_stat / math.sqrt(2)))

    return {
        'sharpe_ratio': round(sr, 4),
        'e_max_sr': round(e_max_sr, 4),
        'dsr_statistic': round(dsr_stat, 4),
        'dsr_prob': round(dsr_prob, 4),
        'n_trials': n_trials,
        'n_days': T,
        'dsr_pass': dsr_prob > 0.95,
    }


# ---------------------------------------------------------------------------
# Run a single filter walk-forward
# ---------------------------------------------------------------------------
def run_filter_walkforward(
    label: str,
    feat_name: str,
    ic_sign: float,
    feature_lookup: dict,
    baseline: dict,
    folds: list,
    n_trials: int,
    use_event_ts_key: bool = False,
):
    """
    Run walk-forward for a single feature filter.

    ic_sign < 0 means lower feature => more ignition => accept when feature < threshold
    ic_sign > 0 means higher feature => more ignition => accept when feature > threshold

    use_event_ts_key: if True, lookup key is (symbol, event_dt), else (symbol, prev_date_str)
    """
    symbols = baseline['symbols']
    event_dates = baseline['event_dates']
    event_dts = baseline['event_datetimes']
    is_ignition = baseline['is_ignition']
    fwd_ret_4h = baseline['fwd_ret_4h']
    n = baseline['n']

    # Align feature to all baseline events
    feat_values = np.full(n, np.nan)
    if use_event_ts_key:
        for i in range(n):
            key = (symbols[i], event_dts[i])
            val = feature_lookup.get(key)
            if val is not None:
                feat_values[i] = val
    else:
        # On-chain: use previous day's data
        for i in range(n):
            dt = datetime.strptime(event_dates[i], "%Y-%m-%d")
            prev_date = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
            key = (symbols[i], prev_date)
            val = feature_lookup.get(key)
            if val is not None:
                feat_values[i] = val

    n_available = int(np.sum(~np.isnan(feat_values)))
    coverage = n_available / n if n > 0 else 0
    log(f"  [{label}] Feature coverage: {n_available:,}/{n:,} = {coverage*100:.1f}%")

    if n_available < 100:
        log(f"  [{label}] Insufficient coverage, skipping")
        return None

    all_fold_results = []
    all_daily_pfs = []

    for fold in folds:
        train_idx = fold['train_indices']
        test_idx = fold['test_indices']

        # Compute threshold from training data
        train_feat = feat_values[train_idx]
        train_mask = ~np.isnan(train_feat)
        if train_mask.sum() < 20:
            # Not enough training data with this feature
            continue

        threshold = float(np.nanmedian(train_feat[train_mask]))

        # Apply filter to test set
        test_feat = feat_values[test_idx]
        has_feature = ~np.isnan(test_feat)

        if ic_sign < 0:
            # Lower feature value => more ignition => accept below threshold
            accepted = has_feature & (test_feat < threshold)
        else:
            # Higher feature value => more ignition => accept above threshold
            accepted = has_feature & (test_feat > threshold)

        if accepted.sum() < 5:
            continue

        metrics = compute_fold_metrics(is_ignition, fwd_ret_4h, test_idx, accepted)
        daily_pfs = compute_daily_pfs(event_dts, fwd_ret_4h, test_idx, accepted)
        metrics['fold_num'] = fold['fold_num']
        metrics['threshold'] = round(threshold, 6)
        metrics['avg_daily_pf'] = round(float(np.mean(daily_pfs)), 4) if daily_pfs else 0.0
        metrics['n_trading_days'] = len(daily_pfs)

        all_fold_results.append(metrics)
        all_daily_pfs.extend(daily_pfs)

    if not all_fold_results:
        log(f"  [{label}] No valid folds")
        return None

    # Aggregate
    total_accepted = sum(r['n_accepted'] for r in all_fold_results)
    total_signals = sum(r['n_total'] for r in all_fold_results)
    total_accepted_tp = sum(r['accepted_tp'] for r in all_fold_results)
    total_tp = sum(r['total_tp'] for r in all_fold_results)
    total_rejected_tp = sum(r['rejected_tp'] for r in all_fold_results)

    precision = total_accepted_tp / total_accepted if total_accepted > 0 else 0.0
    recall = total_accepted_tp / total_tp if total_tp > 0 else 0.0
    tp_rejection_rate = total_rejected_tp / total_tp if total_tp > 0 else 0.0
    acceptance_rate = total_accepted / total_signals if total_signals > 0 else 0.0
    avg_daily_pf = float(np.mean(all_daily_pfs)) if all_daily_pfs else 0.0

    # Aggregate PF
    total_gp = sum(max(r['avg_return_bps'], 0) * r['n_accepted'] for r in all_fold_results)
    total_gl = sum(abs(min(r['avg_return_bps'], 0)) * r['n_accepted'] for r in all_fold_results)

    # Profitable folds
    profitable_folds = sum(1 for r in all_fold_results if r['avg_daily_pf'] > 1.0)
    fold_pass_rate = profitable_folds / len(all_fold_results)
    wf_pass = fold_pass_rate >= 0.80

    dsr = compute_dsr(all_daily_pfs, n_trials)

    # Efficiency ratio: precision improvement per TP rejected
    baseline_precision = 0.25  # approximate, will be computed properly
    precision_improvement = precision - baseline_precision
    efficiency_ratio = precision_improvement / tp_rejection_rate if tp_rejection_rate > 0 else 0.0

    result = {
        'name': label,
        'feature': feat_name,
        'source': 'onchain' if not use_event_ts_key else 'sentiment',
        'ic_sign': 'negative' if ic_sign < 0 else 'positive',
        'n_signals_tested': total_signals,
        'n_available': n_available,
        'coverage': round(coverage, 4),
        'acceptance_rate': round(acceptance_rate, 4),
        'precision_with_filter': round(precision, 4),
        'recall': round(recall, 4),
        'tp_rejection_rate': round(tp_rejection_rate, 4),
        'avg_daily_pf': round(avg_daily_pf, 4),
        'fold_results': all_fold_results,
        'n_folds_valid': len(all_fold_results),
        'profitable_folds': profitable_folds,
        'fold_pass_rate': round(fold_pass_rate, 4),
        'wf_pass': wf_pass,
        'dsr': dsr,
        'n_trading_days': len(all_daily_pfs),
    }

    log(f"  [{label}] Precision: {precision*100:.1f}%, Accept: {acceptance_rate*100:.1f}%, "
        f"TP reject: {tp_rejection_rate*100:.1f}%, Avg daily PF: {avg_daily_pf:.3f}, "
        f"WF pass: {wf_pass} ({profitable_folds}/{len(all_fold_results)})")

    return result


# ---------------------------------------------------------------------------
# Run combination filter walk-forward
# ---------------------------------------------------------------------------
def run_combination_walkforward(
    label: str,
    feature_configs: list,  # [(feat_name, ic_sign, lookup, use_event_ts_key), ...]
    baseline: dict,
    folds: list,
    n_trials: int,
):
    """
    Combination filter: accept signal only if ALL features agree.
    Each feature uses median threshold from training fold.
    """
    symbols = baseline['symbols']
    event_dates = baseline['event_dates']
    event_dts = baseline['event_datetimes']
    is_ignition = baseline['is_ignition']
    fwd_ret_4h = baseline['fwd_ret_4h']
    n = baseline['n']

    # Align all features
    feat_arrays = []
    for feat_name, ic_sign, lookup, use_event_ts_key in feature_configs:
        vals = np.full(n, np.nan)
        if use_event_ts_key:
            for i in range(n):
                key = (symbols[i], event_dts[i])
                val = lookup.get(key)
                if val is not None:
                    vals[i] = val
        else:
            for i in range(n):
                dt = datetime.strptime(event_dates[i], "%Y-%m-%d")
                prev_date = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
                key = (symbols[i], prev_date)
                val = lookup.get(key)
                if val is not None:
                    vals[i] = val
        feat_arrays.append((feat_name, ic_sign, vals))

    # Coverage: events where ALL features are available
    all_available = np.ones(n, dtype=bool)
    for _, _, vals in feat_arrays:
        all_available &= ~np.isnan(vals)
    n_available = int(all_available.sum())
    coverage = n_available / n if n > 0 else 0
    log(f"  [{label}] Combined coverage: {n_available:,}/{n:,} = {coverage*100:.1f}%")

    if n_available < 100:
        log(f"  [{label}] Insufficient combined coverage, skipping")
        return None

    all_fold_results = []
    all_daily_pfs = []

    for fold in folds:
        train_idx = fold['train_indices']
        test_idx = fold['test_indices']

        # Compute thresholds from training data for each feature
        thresholds = []
        valid_train = True
        for feat_name, ic_sign, vals in feat_arrays:
            train_feat = vals[train_idx]
            train_mask = ~np.isnan(train_feat)
            if train_mask.sum() < 20:
                valid_train = False
                break
            thresholds.append(float(np.nanmedian(train_feat[train_mask])))

        if not valid_train:
            continue

        # Apply combined filter to test set
        accepted = np.ones(len(test_idx), dtype=bool)
        for fi, (feat_name, ic_sign, vals) in enumerate(feat_arrays):
            test_feat = vals[test_idx]
            has_feat = ~np.isnan(test_feat)
            if ic_sign < 0:
                feat_pass = has_feat & (test_feat < thresholds[fi])
            else:
                feat_pass = has_feat & (test_feat > thresholds[fi])
            accepted &= feat_pass

        if accepted.sum() < 5:
            continue

        metrics = compute_fold_metrics(is_ignition, fwd_ret_4h, test_idx, accepted)
        daily_pfs = compute_daily_pfs(event_dts, fwd_ret_4h, test_idx, accepted)
        metrics['fold_num'] = fold['fold_num']
        metrics['thresholds'] = [round(t, 6) for t in thresholds]
        metrics['avg_daily_pf'] = round(float(np.mean(daily_pfs)), 4) if daily_pfs else 0.0
        metrics['n_trading_days'] = len(daily_pfs)

        all_fold_results.append(metrics)
        all_daily_pfs.extend(daily_pfs)

    if not all_fold_results:
        log(f"  [{label}] No valid folds")
        return None

    total_accepted = sum(r['n_accepted'] for r in all_fold_results)
    total_signals = sum(r['n_total'] for r in all_fold_results)
    total_accepted_tp = sum(r['accepted_tp'] for r in all_fold_results)
    total_tp = sum(r['total_tp'] for r in all_fold_results)
    total_rejected_tp = sum(r['rejected_tp'] for r in all_fold_results)

    precision = total_accepted_tp / total_accepted if total_accepted > 0 else 0.0
    recall = total_accepted_tp / total_tp if total_tp > 0 else 0.0
    tp_rejection_rate = total_rejected_tp / total_tp if total_tp > 0 else 0.0
    acceptance_rate = total_accepted / total_signals if total_signals > 0 else 0.0
    avg_daily_pf = float(np.mean(all_daily_pfs)) if all_daily_pfs else 0.0

    profitable_folds = sum(1 for r in all_fold_results if r['avg_daily_pf'] > 1.0)
    fold_pass_rate = profitable_folds / len(all_fold_results)
    wf_pass = fold_pass_rate >= 0.80

    dsr = compute_dsr(all_daily_pfs, n_trials)

    result = {
        'name': label,
        'features': [fc[0] for fc in feature_configs],
        'source': 'combination',
        'n_signals_tested': total_signals,
        'n_available': n_available,
        'coverage': round(coverage, 4),
        'acceptance_rate': round(acceptance_rate, 4),
        'precision_with_filter': round(precision, 4),
        'recall': round(recall, 4),
        'tp_rejection_rate': round(tp_rejection_rate, 4),
        'avg_daily_pf': round(avg_daily_pf, 4),
        'fold_results': all_fold_results,
        'n_folds_valid': len(all_fold_results),
        'profitable_folds': profitable_folds,
        'fold_pass_rate': round(fold_pass_rate, 4),
        'wf_pass': wf_pass,
        'dsr': dsr,
        'n_trading_days': len(all_daily_pfs),
    }

    log(f"  [{label}] Precision: {precision*100:.1f}%, Accept: {acceptance_rate*100:.1f}%, "
        f"TP reject: {tp_rejection_rate*100:.1f}%, Avg daily PF: {avg_daily_pf:.3f}, "
        f"WF pass: {wf_pass}")

    return result


# ---------------------------------------------------------------------------
# Volume control check
# ---------------------------------------------------------------------------
def volume_control_check(
    feat_name: str,
    ic_sign: float,
    feature_lookup: dict,
    baseline: dict,
    use_event_ts_key: bool = False,
):
    """
    Check if a sentiment filter is just a market cap / volume proxy.
    Split signals into volume quartiles and check if filter's precision
    improvement persists within each quartile.
    """
    log(f"  Volume control check for {feat_name}...")

    symbols = baseline['symbols']
    event_dates = baseline['event_dates']
    event_dts = baseline['event_datetimes']
    is_ignition = baseline['is_ignition']
    fwd_ret_4h = baseline['fwd_ret_4h']
    volume = baseline['volume']
    n = baseline['n']

    # Align feature
    feat_values = np.full(n, np.nan)
    if use_event_ts_key:
        for i in range(n):
            key = (symbols[i], event_dts[i])
            val = feature_lookup.get(key)
            if val is not None:
                feat_values[i] = val
    else:
        for i in range(n):
            dt = datetime.strptime(event_dates[i], "%Y-%m-%d")
            prev_date = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
            key = (symbols[i], prev_date)
            val = feature_lookup.get(key)
            if val is not None:
                feat_values[i] = val

    # Compute overall threshold (median)
    valid_feat = feat_values[~np.isnan(feat_values)]
    if len(valid_feat) < 100:
        return {'error': 'insufficient coverage'}
    threshold = float(np.median(valid_feat))

    # Split into volume quartiles
    valid_vol = volume[~np.isnan(volume)]
    if len(valid_vol) < 100:
        return {'error': 'insufficient volume data'}
    q25, q50, q75 = np.percentile(valid_vol, [25, 50, 75])

    quartile_results = []
    quartile_labels = [
        ('Q1_low_vol', 0, q25),
        ('Q2', q25, q50),
        ('Q3', q50, q75),
        ('Q4_high_vol', q75, float('inf')),
    ]

    for qlabel, vmin, vmax in quartile_labels:
        # Mask for this quartile
        vol_mask = (volume >= vmin) & (volume < vmax) & ~np.isnan(volume)
        # Also need feature available
        feat_mask = ~np.isnan(feat_values)
        both_mask = vol_mask & feat_mask

        n_in_quartile = int(both_mask.sum())
        if n_in_quartile < 50:
            quartile_results.append({
                'quartile': qlabel,
                'n_signals': n_in_quartile,
                'note': 'insufficient data',
            })
            continue

        # Baseline precision in this quartile
        q_ign = is_ignition[both_mask]
        baseline_prec = float(q_ign.sum()) / n_in_quartile if n_in_quartile > 0 else 0

        # Filtered precision
        q_feat = feat_values[both_mask]
        if ic_sign < 0:
            filter_mask = q_feat < threshold
        else:
            filter_mask = q_feat > threshold

        n_filtered = int(filter_mask.sum())
        if n_filtered < 10:
            quartile_results.append({
                'quartile': qlabel,
                'n_signals': n_in_quartile,
                'n_filtered': n_filtered,
                'note': 'too few filtered signals',
            })
            continue

        filtered_ign = q_ign[filter_mask]
        filtered_prec = float(filtered_ign.sum()) / n_filtered if n_filtered > 0 else 0

        quartile_results.append({
            'quartile': qlabel,
            'volume_range': f"{vmin:.0f}-{vmax:.0f}",
            'n_signals': n_in_quartile,
            'n_filtered': n_filtered,
            'baseline_precision': round(baseline_prec, 4),
            'filtered_precision': round(filtered_prec, 4),
            'precision_improvement': round(filtered_prec - baseline_prec, 4),
        })

    # Check if improvement persists
    improvements = [r.get('precision_improvement', 0) for r in quartile_results
                    if 'precision_improvement' in r]
    all_positive = all(imp > 0 for imp in improvements) if improvements else False
    mostly_positive = sum(1 for imp in improvements if imp > 0) >= len(improvements) * 0.75 if improvements else False

    return {
        'filter_tested': feat_name,
        'threshold': round(threshold, 6),
        'by_volume_quartile': quartile_results,
        'improvement_persists': all_positive,
        'mostly_persists': mostly_positive,
        'verdict': 'genuine' if mostly_positive else 'volume_proxy',
    }


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    t_start = time.time()
    log("=" * 70)
    log("Walk-Forward Filter Comparison: Onchain + Sentiment")
    log("=" * 70)

    # -----------------------------------------------------------------------
    # Phase 0: Load config and baseline
    # -----------------------------------------------------------------------
    log("")
    log("PHASE 0: Loading data")
    log("-" * 40)

    baseline = load_baseline()

    # Filter to ignition events only for filter evaluation
    is_ign_arr = baseline['is_ignition']
    n_total = baseline['n']
    n_ignition = int(np.nansum(is_ign_arr))
    baseline_precision = n_ignition / n_total if n_total > 0 else 0

    log(f"  Baseline precision (ignition rate): {baseline_precision*100:.2f}%")

    # Load screen results for feature metadata
    with open(ONCHAIN_SCREEN, 'r') as f:
        onchain_screen = json.load(f)
    with open(SENTIMENT_SCREEN, 'r') as f:
        sentiment_screen = json.load(f)

    # Top 5 onchain features (by IC vs is_ignition)
    onchain_candidates = [
        ('cross_whale_per_addr', -0.154, 'cross_table'),
        ('whale_txcount_zscore_30d', 0.104, 'whale_transactions'),
        ('net_txvol_zscore_30d', 0.103, 'network_activity'),
        ('exch_outflow_zscore_14d', 0.086, 'exchange_flows'),
        ('net_addr_zscore_30d', 0.085, 'network_activity'),
    ]

    # Top 3 sentiment features (all negative IC)
    sentiment_candidates = [
        ('velocity_24h_max', -0.133),
        ('mention_volume_ratio_24h_max', -0.092),
        ('engagement_24h_sum', -0.071),
    ]

    # -----------------------------------------------------------------------
    # Phase 1: Build feature lookups
    # -----------------------------------------------------------------------
    log("")
    log("PHASE 1: Building feature lookups")
    log("-" * 40)

    needed_onchain = set(f[0] for f in onchain_candidates)
    onchain_lookups = build_onchain_feature_lookups(needed_onchain)

    needed_sentiment = set(f[0] for f in sentiment_candidates)
    sentiment_lookups = build_sentiment_feature_lookups(needed_sentiment, baseline)

    log(f"  Phase 1 complete in {time.time()-t_start:.1f}s")

    # -----------------------------------------------------------------------
    # Phase 2: Build folds
    # -----------------------------------------------------------------------
    log("")
    log("PHASE 2: Building walk-forward folds")
    log("-" * 40)

    event_dts = baseline['event_datetimes']
    folds = build_folds(n_total, event_dts, n_folds=N_FOLDS, embargo_hours=EMBARGO_HOURS)
    log(f"  Built {len(folds)} valid folds")
    for fold in folds:
        log(f"    Fold {fold['fold_num']}: train={len(fold['train_indices']):,}, "
            f"test={len(fold['test_indices']):,}, embargo={fold['embargo_excluded']}")

    # Baseline: run with accept-all filter
    log("")
    log("  Computing baseline (accept all)...")
    baseline_daily_pfs = []
    for fold in folds:
        test_idx = fold['test_indices']
        accepted_all = np.ones(len(test_idx), dtype=bool)
        dpfs = compute_daily_pfs(event_dts, baseline['fwd_ret_4h'], test_idx, accepted_all)
        baseline_daily_pfs.extend(dpfs)
    baseline_avg_daily_pf = float(np.mean(baseline_daily_pfs)) if baseline_daily_pfs else 0.0
    log(f"  Baseline avg daily PF: {baseline_avg_daily_pf:.4f}")

    # Total number of filter configs for DSR
    n_individual = len(onchain_candidates) + len(sentiment_candidates)
    n_combinations = 4  # top2 onchain, top3 onchain, best onchain+sentiment, all 3
    n_trials = n_individual + n_combinations

    # -----------------------------------------------------------------------
    # Phase 3: Individual feature walk-forward
    # -----------------------------------------------------------------------
    log("")
    log("PHASE 3: Individual feature walk-forward")
    log("=" * 60)

    individual_results = []

    # Onchain features
    for feat_name, ic_val, source in onchain_candidates:
        lookup = onchain_lookups.get(feat_name, {})
        if not lookup:
            log(f"  [{feat_name}] No lookup data, skipping")
            continue
        ic_sign = -1.0 if ic_val < 0 else 1.0
        direction = "below" if ic_val < 0 else "above"
        label = f"{feat_name}_{direction}_median"

        result = run_filter_walkforward(
            label=label,
            feat_name=feat_name,
            ic_sign=ic_sign,
            feature_lookup=lookup,
            baseline=baseline,
            folds=folds,
            n_trials=n_trials,
            use_event_ts_key=False,
        )
        if result:
            result['source'] = 'onchain'
            result['precision_improvement'] = round(result['precision_with_filter'] - baseline_precision, 4)
            eff = result['precision_improvement'] / result['tp_rejection_rate'] if result['tp_rejection_rate'] > 0 else 0
            result['efficiency_ratio'] = round(eff, 4)
            individual_results.append(result)

    # Sentiment features
    for feat_name, ic_val in sentiment_candidates:
        lookup = sentiment_lookups.get(feat_name, {})
        if not lookup:
            log(f"  [{feat_name}] No lookup data, skipping")
            continue
        ic_sign = -1.0 if ic_val < 0 else 1.0
        direction = "below" if ic_val < 0 else "above"
        label = f"{feat_name}_{direction}_median"

        result = run_filter_walkforward(
            label=label,
            feat_name=feat_name,
            ic_sign=ic_sign,
            feature_lookup=lookup,
            baseline=baseline,
            folds=folds,
            n_trials=n_trials,
            use_event_ts_key=True,
        )
        if result:
            result['source'] = 'sentiment'
            result['precision_improvement'] = round(result['precision_with_filter'] - baseline_precision, 4)
            eff = result['precision_improvement'] / result['tp_rejection_rate'] if result['tp_rejection_rate'] > 0 else 0
            result['efficiency_ratio'] = round(eff, 4)
            individual_results.append(result)

    log(f"\n  Phase 3 complete: {len(individual_results)} individual filters tested")

    # -----------------------------------------------------------------------
    # Phase 4: Combination filters
    # -----------------------------------------------------------------------
    log("")
    log("PHASE 4: Combination filters")
    log("=" * 60)

    combination_results = []

    # Sort individual results by efficiency ratio to pick best
    onchain_results = [r for r in individual_results if r['source'] == 'onchain']
    sentiment_results = [r for r in individual_results if r['source'] == 'sentiment']

    onchain_results.sort(key=lambda r: r.get('efficiency_ratio', 0), reverse=True)
    sentiment_results.sort(key=lambda r: r.get('efficiency_ratio', 0), reverse=True)

    # Build config tuples for combinations
    def make_config(feat_name, ic_val, lookups, is_sentiment=False):
        ic_sign = -1.0 if ic_val < 0 else 1.0
        lookup = lookups.get(feat_name, {})
        return (feat_name, ic_sign, lookup, is_sentiment)

    # a) Best onchain pair
    if len(onchain_results) >= 2:
        top2_names = [onchain_results[0]['feature'], onchain_results[1]['feature']]
        top2_ics = {}
        for fn, ic, _ in onchain_candidates:
            top2_ics[fn] = ic
        configs = [make_config(fn, top2_ics[fn], onchain_lookups) for fn in top2_names if fn in top2_ics]
        if len(configs) == 2:
            r = run_combination_walkforward(
                f"combo_top2_onchain_{'_AND_'.join(top2_names)}",
                configs, baseline, folds, n_trials)
            if r:
                r['precision_improvement'] = round(r['precision_with_filter'] - baseline_precision, 4)
                eff = r['precision_improvement'] / r['tp_rejection_rate'] if r['tp_rejection_rate'] > 0 else 0
                r['efficiency_ratio'] = round(eff, 4)
                combination_results.append(r)

    # b) Best onchain triple
    if len(onchain_results) >= 3:
        top3_names = [onchain_results[i]['feature'] for i in range(3)]
        configs = [make_config(fn, top2_ics.get(fn, 0.1), onchain_lookups) for fn in top3_names if fn in top2_ics]
        if len(configs) == 3:
            r = run_combination_walkforward(
                f"combo_top3_onchain_{'_AND_'.join(top3_names)}",
                configs, baseline, folds, n_trials)
            if r:
                r['precision_improvement'] = round(r['precision_with_filter'] - baseline_precision, 4)
                eff = r['precision_improvement'] / r['tp_rejection_rate'] if r['tp_rejection_rate'] > 0 else 0
                r['efficiency_ratio'] = round(eff, 4)
                combination_results.append(r)

    # c) Best onchain + best sentiment
    if onchain_results and sentiment_results:
        oc_feat = onchain_results[0]['feature']
        se_feat = sentiment_results[0]['feature']
        oc_ic = next((ic for fn, ic, _ in onchain_candidates if fn == oc_feat), 0.1)
        se_ic = next((ic for fn, ic in sentiment_candidates if fn == se_feat), -0.1)
        configs = [
            make_config(oc_feat, oc_ic, onchain_lookups, False),
            make_config(se_feat, se_ic, sentiment_lookups, True),
        ]
        r = run_combination_walkforward(
            f"combo_onchain_sentiment_{oc_feat}_AND_{se_feat}",
            configs, baseline, folds, n_trials)
        if r:
            r['precision_improvement'] = round(r['precision_with_filter'] - baseline_precision, 4)
            eff = r['precision_improvement'] / r['tp_rejection_rate'] if r['tp_rejection_rate'] > 0 else 0
            r['efficiency_ratio'] = round(eff, 4)
            combination_results.append(r)

    # d) All 3 best (top onchain + top 2 sentiment)
    if len(onchain_results) >= 1 and len(sentiment_results) >= 2:
        oc_feat = onchain_results[0]['feature']
        se_feat1 = sentiment_results[0]['feature']
        se_feat2 = sentiment_results[1]['feature']
        oc_ic = next((ic for fn, ic, _ in onchain_candidates if fn == oc_feat), 0.1)
        se_ic1 = next((ic for fn, ic in sentiment_candidates if fn == se_feat1), -0.1)
        se_ic2 = next((ic for fn, ic in sentiment_candidates if fn == se_feat2), -0.1)
        configs = [
            make_config(oc_feat, oc_ic, onchain_lookups, False),
            make_config(se_feat1, se_ic1, sentiment_lookups, True),
            make_config(se_feat2, se_ic2, sentiment_lookups, True),
        ]
        r = run_combination_walkforward(
            f"combo_mixed_3_{oc_feat}_{se_feat1}_{se_feat2}",
            configs, baseline, folds, n_trials)
        if r:
            r['precision_improvement'] = round(r['precision_with_filter'] - baseline_precision, 4)
            eff = r['precision_improvement'] / r['tp_rejection_rate'] if r['tp_rejection_rate'] > 0 else 0
            r['efficiency_ratio'] = round(eff, 4)
            combination_results.append(r)

    log(f"\n  Phase 4 complete: {len(combination_results)} combinations tested")

    # -----------------------------------------------------------------------
    # Phase 5: Volume control check
    # -----------------------------------------------------------------------
    log("")
    log("PHASE 5: Volume control check")
    log("=" * 60)

    vol_control_results = []

    # Check each sentiment filter
    for feat_name, ic_val in sentiment_candidates:
        lookup = sentiment_lookups.get(feat_name, {})
        if not lookup:
            continue
        ic_sign = -1.0 if ic_val < 0 else 1.0
        vc = volume_control_check(
            feat_name=feat_name,
            ic_sign=ic_sign,
            feature_lookup=lookup,
            baseline=baseline,
            use_event_ts_key=True,
        )
        vol_control_results.append(vc)

    # Also check best onchain feature as a reference
    if onchain_results:
        best_oc = onchain_results[0]['feature']
        best_oc_ic = next((ic for fn, ic, _ in onchain_candidates if fn == best_oc), 0.1)
        lookup = onchain_lookups.get(best_oc, {})
        if lookup:
            vc = volume_control_check(
                feat_name=best_oc,
                ic_sign=-1.0 if best_oc_ic < 0 else 1.0,
                feature_lookup=lookup,
                baseline=baseline,
                use_event_ts_key=False,
            )
            vol_control_results.append(vc)

    # -----------------------------------------------------------------------
    # Phase 6: Output
    # -----------------------------------------------------------------------
    log("")
    log("PHASE 6: Generating output")
    log("=" * 60)

    # Sort individual results by efficiency ratio
    individual_results.sort(key=lambda r: r.get('efficiency_ratio', 0), reverse=True)
    combination_results.sort(key=lambda r: r.get('efficiency_ratio', 0), reverse=True)

    # Find recommended filter
    all_filters = individual_results + combination_results
    all_filters.sort(key=lambda r: r.get('efficiency_ratio', 0), reverse=True)

    recommended = None
    for f in all_filters:
        if f.get('wf_pass', False):
            recommended = {
                'name': f['name'],
                'precision_with_filter': f['precision_with_filter'],
                'precision_improvement': f['precision_improvement'],
                'efficiency_ratio': f['efficiency_ratio'],
                'tp_rejection_rate': f['tp_rejection_rate'],
                'avg_daily_pf': f['avg_daily_pf'],
                'rationale': (
                    f"Best efficiency ratio ({f['efficiency_ratio']:.3f}) among walk-forward passing filters. "
                    f"Precision {f['precision_with_filter']*100:.1f}% (improvement +{f['precision_improvement']*100:.1f}pp) "
                    f"at {f['tp_rejection_rate']*100:.1f}% TP rejection rate."
                ),
            }
            break

    if recommended is None and all_filters:
        # No WF-passing filter, recommend the best anyway with caveat
        f = all_filters[0]
        recommended = {
            'name': f['name'],
            'precision_with_filter': f['precision_with_filter'],
            'precision_improvement': f.get('precision_improvement', 0),
            'efficiency_ratio': f.get('efficiency_ratio', 0),
            'tp_rejection_rate': f['tp_rejection_rate'],
            'avg_daily_pf': f['avg_daily_pf'],
            'rationale': (
                "No filter passed walk-forward validation (>=80% profitable folds). "
                f"Best candidate: {f['name']} with precision {f['precision_with_filter']*100:.1f}%."
            ),
        }

    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'baseline_precision': round(baseline_precision, 4),
        'baseline_avg_daily_pf': round(baseline_avg_daily_pf, 4),
        'n_signals_total': n_total,
        'n_ignition_signals': n_ignition,
        'n_folds': N_FOLDS,
        'embargo_hours': EMBARGO_HOURS,
        'n_trials_dsr': n_trials,
        'individual_filters': individual_results,
        'combination_filters': combination_results,
        'volume_control_check': vol_control_results,
        'recommended_filter': recommended,
        'elapsed_seconds': round(time.time() - t_start, 1),
    }

    # Write output
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    log(f"\nOutput written to: {OUTPUT_JSON}")

    # -----------------------------------------------------------------------
    # Summary table
    # -----------------------------------------------------------------------
    log("")
    log("=" * 90)
    log("SUMMARY TABLE")
    log("=" * 90)
    hdr = f"{'Filter':<45} {'Src':>4} {'Prec':>6} {'+Imp':>6} {'TPrej':>6} {'EffR':>6} {'AvgPF':>6} {'WF':>4}"
    log(hdr)
    log("-" * 90)

    for r in individual_results:
        log(f"{r['name'][:45]:<45} {r['source'][:4]:>4} "
            f"{r['precision_with_filter']*100:>5.1f}% "
            f"{r.get('precision_improvement',0)*100:>+5.1f} "
            f"{r['tp_rejection_rate']*100:>5.1f}% "
            f"{r.get('efficiency_ratio',0):>6.3f} "
            f"{r['avg_daily_pf']:>6.3f} "
            f"{'YES' if r.get('wf_pass') else 'no':>4}")

    log("-" * 90)
    for r in combination_results:
        log(f"{r['name'][:45]:<45} {'comb':>4} "
            f"{r['precision_with_filter']*100:>5.1f}% "
            f"{r.get('precision_improvement',0)*100:>+5.1f} "
            f"{r['tp_rejection_rate']*100:>5.1f}% "
            f"{r.get('efficiency_ratio',0):>6.3f} "
            f"{r['avg_daily_pf']:>6.3f} "
            f"{'YES' if r.get('wf_pass') else 'no':>4}")

    log("-" * 90)
    log(f"Baseline: precision={baseline_precision*100:.1f}%, avg_daily_pf={baseline_avg_daily_pf:.4f}")

    if recommended:
        log(f"\nRECOMMENDED: {recommended['name']}")
        log(f"  {recommended['rationale']}")

    # Volume control verdict
    for vc in vol_control_results:
        if 'error' in vc:
            continue
        log(f"\nVolume control ({vc['filter_tested']}): {vc['verdict']}")
        for qr in vc.get('by_volume_quartile', []):
            if 'precision_improvement' in qr:
                log(f"  {qr['quartile']}: base={qr['baseline_precision']*100:.1f}% "
                    f"filt={qr['filtered_precision']*100:.1f}% "
                    f"imp={qr['precision_improvement']*100:+.1f}pp")

    elapsed = time.time() - t_start
    log(f"\nTotal elapsed: {elapsed:.1f}s")
    log("DONE")


if __name__ == "__main__":
    main()
