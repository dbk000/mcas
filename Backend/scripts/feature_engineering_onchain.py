#!/usr/bin/env python3
"""
On-chain feature engineering and IC screening against ignition events.

Pure numpy + psycopg (no pandas). Loads ignition baseline CSV, queries Santiment
daily tables (network_activity, whale_transactions, exchange_flows), engineers
rolling-window features, and screens each by Spearman rank IC against
is_ignition and fwd_ret_4h.

Output: /results/feature_screen_onchain.json (inside container)

Run:
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T \
        -v /srv/mcas/results:/results worker python3 /scripts/feature_engineering_onchain.py
"""

import csv
import json
import math
import os
import time
from datetime import datetime, timezone, timedelta

import numpy as np

# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------
IN_DOCKER = os.path.isdir("/app")
if IN_DOCKER:
    RESULTS_DIR = "/results"
    DB_URL = os.environ.get(
        "DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading"
    )
else:
    RESULTS_DIR = "/srv/mcas/results"
    DB_URL = os.environ.get(
        "DATABASE_URL", "postgresql://trading:trading@localhost:5432/trading"
    )

BASELINE_CSV = os.path.join(RESULTS_DIR, "ignition_events_with_outcomes.csv")
OUTPUT_JSON = os.path.join(RESULTS_DIR, "feature_screen_onchain.json")

# Whale / exchange_flows data ends at 2026-01-21
WHALE_EXCHANGE_CUTOFF = datetime(2026, 1, 21, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Spearman rank correlation (no scipy)
# ---------------------------------------------------------------------------
def _rankdata(arr: np.ndarray) -> np.ndarray:
    """Average-rank ties."""
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


def spearmanr(x: np.ndarray, y: np.ndarray):
    """Spearman rank correlation + approximate p-value."""
    n = len(x)
    if n < 10:
        return np.nan, 1.0
    rx = _rankdata(x)
    ry = _rankdata(y)
    mx, my = rx.mean(), ry.mean()
    dx, dy = rx - mx, ry - my
    denom = np.sqrt((dx ** 2).sum() * (dy ** 2).sum())
    if denom == 0:
        return 0.0, 1.0
    rho = float((dx * dy).sum() / denom)
    if abs(rho) >= 1.0:
        return rho, 0.0
    t_stat = rho * np.sqrt((n - 2) / (1 - rho ** 2))
    if n > 30:
        p_val = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))
    else:
        p_val = 2 * np.exp(-0.5 * t_stat ** 2) / np.sqrt(2 * np.pi) if abs(t_stat) < 30 else 0.0
    return rho, float(p_val)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_connection():
    import psycopg
    return psycopg.connect(DB_URL, autocommit=True)


def query_rows(sql: str):
    """Execute SQL, return (column_names, rows)."""
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
# Rolling window computations (pure numpy, per-symbol arrays)
# ---------------------------------------------------------------------------
def rolling_mean(arr: np.ndarray, window: int, min_periods: int = None) -> np.ndarray:
    """Rolling mean with NaN handling. Returns same-length array."""
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


def rolling_std(arr: np.ndarray, window: int, min_periods: int = None) -> np.ndarray:
    """Rolling std with NaN handling."""
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


def rolling_sum(arr: np.ndarray, window: int, min_periods: int = 3) -> np.ndarray:
    """Rolling sum with NaN handling."""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(n):
        start = max(0, i - window + 1)
        chunk = arr[start:i+1]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) >= min_periods:
            result[i] = valid.sum()
    return result


def compute_zscore(arr: np.ndarray, window: int) -> np.ndarray:
    """Z-score of current value vs trailing window."""
    rm = rolling_mean(arr, window)
    rs = rolling_std(arr, window)
    rs[rs == 0] = np.nan
    return (arr - rm) / rs


def compute_ratio(arr: np.ndarray, window: int) -> np.ndarray:
    """Current value / trailing window mean."""
    rm = rolling_mean(arr, window)
    rm[rm == 0] = np.nan
    return arr / rm


def compute_roc(arr: np.ndarray, periods: int) -> np.ndarray:
    """Rate of change: (current - lag) / |lag|."""
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


# ---------------------------------------------------------------------------
# Load baseline CSV (pure Python + numpy)
# ---------------------------------------------------------------------------
def load_baseline():
    log(f"Loading baseline from {BASELINE_CSV}")
    t0 = time.time()

    with open(BASELINE_CSV, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    n = len(rows)
    symbols = [r['symbol'] for r in rows]
    # Parse timestamps to date strings (YYYY-MM-DD) for joining
    event_ts_raw = [r['event_ts'] for r in rows]
    # event_ts format: "2025-02-19 11:00:00"
    event_dates = []
    event_datetimes = []
    for ts_str in event_ts_raw:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        event_datetimes.append(dt)
        event_dates.append(dt.strftime("%Y-%m-%d"))

    def safe_float(val):
        try:
            return float(val) if val != '' else np.nan
        except (ValueError, TypeError):
            return np.nan

    is_ignition = np.array([safe_float(r['is_ignition']) for r in rows])
    fwd_ret_4h = np.array([safe_float(r['fwd_ret_4h']) for r in rows])

    log(f"  Loaded {n:,} rows, {int(is_ignition.sum()):,} ignition events, "
        f"{len(set(symbols))} symbols in {time.time()-t0:.1f}s")

    return {
        'symbols': symbols,
        'event_dates': event_dates,     # "YYYY-MM-DD"
        'event_datetimes': event_datetimes,
        'is_ignition': is_ignition,
        'fwd_ret_4h': fwd_ret_4h,
        'n': n,
    }


# ---------------------------------------------------------------------------
# Load on-chain tables into per-symbol dicts
# ---------------------------------------------------------------------------
def load_onchain_table(table_name: str, columns: list) -> dict:
    """
    Load table into dict: symbol -> { 'dates': [date_str, ...], col: np.array, ... }
    Sorted by date within each symbol.
    """
    log(f"Loading {table_name} from Postgres...")
    t0 = time.time()
    col_sql = ", ".join(columns)
    sql = f"SELECT symbol, timestamp, {col_sql} FROM {table_name} ORDER BY symbol, timestamp"
    col_names, rows = query_rows(sql)

    # Build per-symbol data
    by_symbol = {}
    for row in rows:
        sym = row[0]
        ts = row[1]  # datetime with tz
        if sym not in by_symbol:
            by_symbol[sym] = {'dates': [], 'date_strs': []}
            for c in columns:
                by_symbol[sym][c] = []
        by_symbol[sym]['dates'].append(ts)
        by_symbol[sym]['date_strs'].append(ts.strftime("%Y-%m-%d"))
        for i, c in enumerate(columns):
            val = row[2 + i]
            by_symbol[sym][c].append(float(val) if val is not None else np.nan)

    # Convert to numpy
    for sym in by_symbol:
        for c in columns:
            by_symbol[sym][c] = np.array(by_symbol[sym][c], dtype=np.float64)

    n_rows = len(rows)
    n_syms = len(by_symbol)
    log(f"  Loaded {n_rows:,} rows, {n_syms} symbols in {time.time()-t0:.1f}s")
    return by_symbol


# ---------------------------------------------------------------------------
# Feature engineering per table
# ---------------------------------------------------------------------------
def engineer_features_for_symbol(vals: np.ndarray, prefix: str) -> dict:
    """
    Given a 1D array of daily values for one symbol, compute all standard
    transforms. Returns dict of feature_name -> np.array (same length as vals).
    """
    features = {}
    for w in [7, 14, 30]:
        features[f"{prefix}_zscore_{w}d"] = compute_zscore(vals, w)
    features[f"{prefix}_ratio_30d"] = compute_ratio(vals, 30)
    for p in [1, 7]:
        features[f"{prefix}_roc_{p}d"] = compute_roc(vals, p)
    return features


def build_feature_lookup(by_symbol: dict, columns: list, prefix_map: dict) -> dict:
    """
    Build a lookup: (symbol, date_str) -> {feature_name: value, ...}
    for all features derived from given columns.

    prefix_map: {column_name: feature_prefix}
    """
    log(f"  Building feature lookup for {len(prefix_map)} raw columns...")
    t0 = time.time()

    # feature_name -> { (sym, date_str): value }
    all_features = {}

    sym_count = 0
    for sym, data in by_symbol.items():
        sym_count += 1
        if sym_count % 50 == 0:
            log(f"    Processing symbol {sym_count}/{len(by_symbol)}: {sym}")

        date_strs = data['date_strs']
        n_days = len(date_strs)

        for col, prefix in prefix_map.items():
            vals = data[col]
            if np.sum(~np.isnan(vals)) < 10:
                continue

            feats = engineer_features_for_symbol(vals, prefix)
            for feat_name, feat_arr in feats.items():
                if feat_name not in all_features:
                    all_features[feat_name] = {}
                for i in range(n_days):
                    if not np.isnan(feat_arr[i]):
                        # Key by (sym, date) — the on-chain date
                        all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

    n_feat = len(all_features)
    total_obs = sum(len(v) for v in all_features.values())
    log(f"  Built {n_feat} features, {total_obs:,} total obs in {time.time()-t0:.1f}s")
    return all_features


def build_exchange_features(by_symbol: dict) -> dict:
    """Build exchange-specific features including cross-column ones."""
    log("  Building exchange features...")
    t0 = time.time()

    all_features = {}
    sym_count = 0

    for sym, data in by_symbol.items():
        sym_count += 1
        if sym_count % 50 == 0:
            log(f"    Processing symbol {sym_count}/{len(by_symbol)}: {sym}")

        date_strs = data['date_strs']
        n_days = len(date_strs)

        inflow = data['exchange_inflow']
        outflow = data['exchange_outflow']
        netflow = data['exchange_netflow']

        if np.sum(~np.isnan(inflow)) < 10:
            continue

        # Standard features for netflow
        for w in [7, 14, 30]:
            feat_name = f"exch_netflow_zscore_{w}d"
            feat_arr = compute_zscore(netflow, w)
            if feat_name not in all_features:
                all_features[feat_name] = {}
            for i in range(n_days):
                if not np.isnan(feat_arr[i]):
                    all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

        # Abs netflow ratio to 30d
        feat_name = "exch_netflow_ratio_30d"
        feat_arr = compute_ratio(np.abs(netflow), 30)
        if feat_name not in all_features:
            all_features[feat_name] = {}
        for i in range(n_days):
            if not np.isnan(feat_arr[i]):
                all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

        # Inflow z-scores
        for w in [7, 14]:
            feat_name = f"exch_inflow_zscore_{w}d"
            feat_arr = compute_zscore(inflow, w)
            if feat_name not in all_features:
                all_features[feat_name] = {}
            for i in range(n_days):
                if not np.isnan(feat_arr[i]):
                    all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

        # Outflow z-scores
        for w in [7, 14]:
            feat_name = f"exch_outflow_zscore_{w}d"
            feat_arr = compute_zscore(outflow, w)
            if feat_name not in all_features:
                all_features[feat_name] = {}
            for i in range(n_days):
                if not np.isnan(feat_arr[i]):
                    all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

        # Net direction: sign of 7d cumulative netflow
        feat_name = "exch_net_direction_7d"
        cum7 = rolling_sum(netflow, 7, min_periods=3)
        feat_arr = np.sign(cum7)
        if feat_name not in all_features:
            all_features[feat_name] = {}
        for i in range(n_days):
            if not np.isnan(feat_arr[i]):
                all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

        # Accumulation signal: (outflow - inflow) / (outflow + inflow)
        denom = outflow + inflow
        with np.errstate(divide='ignore', invalid='ignore'):
            denom_safe = np.where(denom == 0, np.nan, denom)
            accum = (outflow - inflow) / denom_safe

        feat_name = "exch_accum_signal"
        if feat_name not in all_features:
            all_features[feat_name] = {}
        for i in range(n_days):
            if not np.isnan(accum[i]):
                all_features[feat_name][(sym, date_strs[i])] = accum[i]

        # Accumulation z-score 14d
        feat_name = "exch_accum_zscore_14d"
        feat_arr = compute_zscore(accum, 14)
        if feat_name not in all_features:
            all_features[feat_name] = {}
        for i in range(n_days):
            if not np.isnan(feat_arr[i]):
                all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

        # Inflow ROC
        for p in [1, 7]:
            feat_name = f"exch_inflow_roc_{p}d"
            feat_arr = compute_roc(inflow, p)
            if feat_name not in all_features:
                all_features[feat_name] = {}
            for i in range(n_days):
                if not np.isnan(feat_arr[i]):
                    all_features[feat_name][(sym, date_strs[i])] = feat_arr[i]

    n_feat = len(all_features)
    total_obs = sum(len(v) for v in all_features.values())
    log(f"  Built {n_feat} exchange features, {total_obs:,} total obs in {time.time()-t0:.1f}s")
    return all_features


def build_cross_features(net_data: dict, whale_data: dict, exch_data: dict) -> dict:
    """Cross-table features via matching on (symbol, date)."""
    log("  Building cross-table features...")
    t0 = time.time()

    # Build flat lookups: (sym, date_str) -> value
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

    netflow_lookup = {}
    inflow_lookup = {}
    for sym, data in exch_data.items():
        for i, ds in enumerate(data['date_strs']):
            v = data['exchange_netflow'][i]
            if not np.isnan(v):
                netflow_lookup[(sym, ds)] = v
            v2 = data['exchange_inflow'][i]
            if not np.isnan(v2):
                inflow_lookup[(sym, ds)] = v2

    all_features = {
        "cross_whale_per_addr": {},
        "cross_netflow_per_txvol": {},
        "cross_inflow_per_addr": {},
    }

    # All unique (sym, date) keys
    all_keys = set(addr_lookup.keys()) | set(whale_lookup.keys()) | set(netflow_lookup.keys())

    for key in all_keys:
        addr = addr_lookup.get(key)
        txvol = txvol_lookup.get(key)
        whale = whale_lookup.get(key)
        netflow = netflow_lookup.get(key)
        inflow = inflow_lookup.get(key)

        if whale is not None and addr is not None and addr > 0:
            all_features["cross_whale_per_addr"][key] = whale / addr

        if netflow is not None and txvol is not None and txvol > 0:
            all_features["cross_netflow_per_txvol"][key] = netflow / txvol

        if inflow is not None and addr is not None and addr > 0:
            all_features["cross_inflow_per_addr"][key] = inflow / addr

    total_obs = sum(len(v) for v in all_features.values())
    log(f"  Built 3 cross-table features, {total_obs:,} total obs in {time.time()-t0:.1f}s")
    return all_features


# ---------------------------------------------------------------------------
# Align features to baseline and screen IC
# ---------------------------------------------------------------------------
def align_and_screen(
    baseline: dict,
    features: dict,  # feat_name -> {(sym, date_str): value}
    source_table: str,
    apply_cutoff: bool = False,
    min_obs: int = 500,
    min_coverage_frac: float = 0.05,
) -> list:
    """
    For each feature, align to baseline events (using previous day's on-chain
    data to avoid look-ahead), then compute Spearman IC vs is_ignition and fwd_ret_4h.
    """
    log(f"Aligning and screening {len(features)} features from {source_table}...")
    t0 = time.time()

    symbols = baseline['symbols']
    event_dates = baseline['event_dates']        # "YYYY-MM-DD"
    event_dts = baseline['event_datetimes']
    is_ignition = baseline['is_ignition']
    fwd_ret_4h = baseline['fwd_ret_4h']
    n_total = baseline['n']

    # For each event, the usable on-chain date is event_date - 1 day
    # (on-chain data for date D available for events on D+1)
    def prev_date_str(date_str):
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return (dt - timedelta(days=1)).strftime("%Y-%m-%d")

    # Pre-compute join keys: (symbol, prev_date) for each baseline event
    join_keys = []
    valid_mask = np.ones(n_total, dtype=bool)
    for i in range(n_total):
        if apply_cutoff and event_dts[i] >= WHALE_EXCHANGE_CUTOFF:
            valid_mask[i] = False
            join_keys.append(None)
        else:
            join_keys.append((symbols[i], prev_date_str(event_dates[i])))

    n_valid = int(valid_mask.sum())
    if apply_cutoff:
        log(f"  Baseline filtered to {n_valid:,} events (cutoff {WHALE_EXCHANGE_CUTOFF.date()})")

    MAX_SAMPLE = 50_000
    results = []

    targets = {
        "is_ignition": is_ignition,
        "fwd_ret_4h": fwd_ret_4h,
    }

    feat_names = sorted(features.keys())
    for fi, feat_name in enumerate(feat_names):
        if (fi + 1) % 5 == 0 or fi == 0:
            log(f"  [{source_table}] Feature {fi+1}/{len(feat_names)}: {feat_name} "
                f"({time.time()-t0:.1f}s)")

        lookup = features[feat_name]

        # Build aligned feature array
        feat_arr = np.full(n_total, np.nan)
        for i in range(n_total):
            if valid_mask[i] and join_keys[i] is not None:
                val = lookup.get(join_keys[i])
                if val is not None:
                    feat_arr[i] = val

        for target_name, target_arr in targets.items():
            # Valid observations: both feature and target non-NaN, and in valid set
            mask = valid_mask & ~np.isnan(feat_arr) & ~np.isnan(target_arr) & np.isfinite(feat_arr)
            n_obs = int(mask.sum())

            if n_obs < min_obs:
                continue

            coverage_pct = (n_obs / n_valid) * 100 if n_valid > 0 else 0
            if coverage_pct < min_coverage_frac * 100:
                continue

            x = feat_arr[mask]
            y = target_arr[mask]

            # Subsample for speed
            if n_obs > MAX_SAMPLE:
                rng = np.random.RandomState(42)
                idx = rng.choice(n_obs, MAX_SAMPLE, replace=False)
                x_sample, y_sample = x[idx], y[idx]
                n_sample = MAX_SAMPLE
            else:
                x_sample, y_sample = x, y
                n_sample = n_obs

            try:
                ic, p_val = spearmanr(x_sample, y_sample)
            except Exception:
                continue

            if np.isnan(ic):
                continue

            # IC stability: 10 chunks
            chunk_size = n_sample // 10
            ic_std = np.nan
            t_stat_val = 0.0
            if chunk_size > 50:
                chunk_ics = []
                for ci_idx in range(10):
                    s, e = ci_idx * chunk_size, (ci_idx + 1) * chunk_size
                    ci, _ = spearmanr(x_sample[s:e], y_sample[s:e])
                    if not np.isnan(ci):
                        chunk_ics.append(ci)
                if len(chunk_ics) >= 5:
                    ic_std = float(np.std(chunk_ics))
                    if ic_std > 0:
                        t_stat_val = ic / (ic_std / np.sqrt(len(chunk_ics)))

            results.append({
                "name": feat_name,
                "source_table": source_table,
                "target": target_name,
                "ic": round(ic, 6),
                "ic_std": round(ic_std, 6) if not np.isnan(ic_std) else None,
                "t_stat": round(t_stat_val, 4),
                "p_value": round(p_val, 6),
                "coverage_pct": round(coverage_pct, 2),
                "n_obs": n_obs,
            })

    results.sort(key=lambda r: abs(r["ic"]), reverse=True)
    log(f"  Screened {len(results)} feature x target pairs from {source_table} "
        f"in {time.time()-t0:.1f}s")
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t_start = time.time()
    log("=" * 70)
    log("On-chain Feature Engineering & IC Screening (pure numpy)")
    log("=" * 70)

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    baseline = load_baseline()

    net_data = load_onchain_table(
        "network_activity",
        ["active_addresses_24h", "network_growth", "transaction_volume",
         "dev_activity", "social_volume"]
    )
    whale_data = load_onchain_table("whale_transactions", ["whale_tx_count"])
    exch_data = load_onchain_table(
        "exchange_flows",
        ["exchange_inflow", "exchange_outflow", "exchange_netflow"]
    )

    log(f"Data loading complete in {time.time()-t_start:.1f}s")

    # ------------------------------------------------------------------
    # Engineer features
    # ------------------------------------------------------------------
    log("")
    log("=" * 70)
    log("FEATURE ENGINEERING")
    log("=" * 70)

    # Network activity features
    net_features = build_feature_lookup(net_data,
        ["active_addresses_24h", "network_growth", "transaction_volume",
         "dev_activity", "social_volume"],
        {
            "active_addresses_24h": "net_addr",
            "network_growth": "net_netgrowth",
            "transaction_volume": "net_txvol",
            "dev_activity": "net_devact",
            "social_volume": "net_socvol",
        }
    )

    # Whale features
    whale_features = build_feature_lookup(whale_data,
        ["whale_tx_count"],
        {"whale_tx_count": "whale_txcount"}
    )

    # Exchange features (custom multi-column)
    exch_features = build_exchange_features(exch_data)

    # Cross-table features
    cross_features = build_cross_features(net_data, whale_data, exch_data)

    log(f"Feature engineering complete in {time.time()-t_start:.1f}s")

    # ------------------------------------------------------------------
    # IC screening
    # ------------------------------------------------------------------
    log("")
    log("=" * 70)
    log("IC SCREENING")
    log("=" * 70)

    all_results = []

    # Network features (full date range)
    net_results = align_and_screen(baseline, net_features, "network_activity",
                                   apply_cutoff=False)
    all_results.extend(net_results)
    log(f"  Network done: {len(net_results)} results")

    # Whale features (pre-cutoff)
    whale_results = align_and_screen(baseline, whale_features, "whale_transactions",
                                     apply_cutoff=True)
    all_results.extend(whale_results)
    log(f"  Whale done: {len(whale_results)} results")

    # Exchange features (pre-cutoff)
    exch_results = align_and_screen(baseline, exch_features, "exchange_flows",
                                    apply_cutoff=True)
    all_results.extend(exch_results)
    log(f"  Exchange done: {len(exch_results)} results")

    # Cross-table features (pre-cutoff)
    cross_results = align_and_screen(baseline, cross_features, "cross_table",
                                     apply_cutoff=True)
    all_results.extend(cross_results)
    log(f"  Cross-table done: {len(cross_results)} results")

    # ------------------------------------------------------------------
    # Sort and recommend
    # ------------------------------------------------------------------
    all_results.sort(key=lambda r: abs(r["ic"]), reverse=True)

    recommended = []
    seen_feat_names = set()
    for r in all_results:
        if (
            abs(r["ic"]) > 0.02
            and abs(r.get("t_stat", 0)) > 2.0
            and r["coverage_pct"] > 10.0
            and r["name"] not in seen_feat_names
        ):
            r["recommended"] = True
            recommended.append(r["name"])
            seen_feat_names.add(r["name"])
        else:
            r["recommended"] = False

    # ------------------------------------------------------------------
    # Output JSON
    # ------------------------------------------------------------------
    n_ignition = int(baseline['is_ignition'].sum())
    n_total = baseline['n']
    n_features_tested = len(set(r["name"] for r in all_results))

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_file": str(BASELINE_CSV),
        "n_baseline_events": n_total,
        "n_ignition_events": n_ignition,
        "data_window_note": (
            "whale_transactions and exchange_flows end 2026-01-21; "
            "features from those tables tested on pre-Jan-21 events only. "
            "Features use previous day's on-chain data (no look-ahead)."
        ),
        "n_features_tested": n_features_tested,
        "screening_config": {
            "min_obs": 500,
            "min_coverage_pct": 5.0,
            "ic_stability_chunks": 10,
            "max_sample_per_pair": 50_000,
            "recommendation_thresholds": {
                "min_abs_ic": 0.02,
                "min_abs_t_stat": 2.0,
                "min_coverage_pct": 10.0,
            },
        },
        "features": all_results,
        "recommended_top_candidates": recommended[:20],
    }

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)

    elapsed = time.time() - t_start
    log("")
    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log(f"Total features tested: {n_features_tested}")
    log(f"Total feature x target pairs: {len(all_results)}")
    log(f"Recommended candidates: {len(recommended)}")
    for r in recommended[:10]:
        log(f"  * {r}")
    log(f"Output written to: {OUTPUT_JSON}")
    log(f"Total time: {elapsed:.1f}s")

    log("")
    log("Top 20 features by |IC|:")
    hdr = f"{'Feature':<40} {'Target':<15} {'IC':>8} {'IC_std':>8} {'t_stat':>8} {'Cov%':>7} {'Rec':>4}"
    log(hdr)
    log("-" * len(hdr))
    for r in all_results[:20]:
        ic_std_str = f"{r['ic_std']:.4f}" if r['ic_std'] is not None else "  N/A "
        rec_str = " *" if r.get("recommended") else ""
        log(f"{r['name']:<40} {r['target']:<15} {r['ic']:>8.4f} {ic_std_str:>8} "
            f"{r['t_stat']:>8.2f} {r['coverage_pct']:>6.1f}% {rec_str}")


if __name__ == "__main__":
    main()
