#!/usr/bin/env python3
"""
extract_ignition_baseline.py — Build baseline outcomes dataset from all opportunities.

Queries all opportunities (39K+ across S1-S9), computes forward price outcomes
from OHLCV data, and writes CSV for downstream filter research.

Output: /results/ignition_baseline_outcomes.csv (inside container)
        /srv/mcas/results/ignition_baseline_outcomes.csv (host mount)
"""

import csv
import json
import logging
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any

import numpy as np
import psycopg

# ---------------------------------------------------------------------------
# Execution context detection
# ---------------------------------------------------------------------------
IN_DOCKER = os.path.isdir("/app")

if IN_DOCKER:
    # Worker container mounts ./scripts:/scripts — write output there
    OUTPUT_DIR = "/scripts"
    SCRIPTS_DIR = "/scripts"
else:
    OUTPUT_DIR = "/srv/mcas/results"
    SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

OUTPUT_PATH = os.path.join(OUTPUT_DIR, "ignition_baseline_outcomes.csv")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://trading:trading@postgres:5432/trading" if IN_DOCKER
    else "postgresql://trading:trading@localhost:5432/trading",
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("baseline")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Forward return horizons in hours
FWD_HORIZONS_H = [1, 4, 12, 24]

# TP thresholds in basis points
TP_THRESHOLDS_BPS = [50, 100, 150, 200, 300]

# MFE/MAE and true-positive window in hours
WINDOW_H = 24

# Workers for parallel processing
N_WORKERS = 10

# Timeframe to minutes mapping
TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
    "12h": 720, "1d": 1440,
}

# CSV columns
CSV_COLUMNS = [
    "opp_id", "created_ts", "symbol", "timeframe", "setup_id", "direction",
    "score", "fwd_return_1h", "fwd_return_4h", "fwd_return_12h",
    "fwd_return_24h", "mfe_24h", "mae_24h", "tp_hit_50bps", "tp_hit_100bps",
    "tp_hit_150bps", "tp_hit_200bps", "tp_hit_300bps", "is_true_positive",
    "payload_features",
]


def fetch_all_opportunities() -> list[dict]:
    """Fetch all opportunities from the database."""
    sql = """
        SELECT id, created_ts, symbol, timeframe, setup_id,
               decision, score, payload_json, status
        FROM opportunities
        ORDER BY created_ts ASC
    """
    log.info("Fetching all opportunities...")
    t0 = time.time()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    log.info(f"Fetched {len(rows):,} opportunities in {time.time() - t0:.1f}s")
    return rows


def parse_payload(payload_json: str | None) -> dict[str, Any]:
    """Parse payload JSON, extract direction, setup_score, and other features."""
    if not payload_json:
        return {"direction": None, "setup_score": None}

    try:
        p = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return {"direction": None, "setup_score": None}

    # Extract key fields
    result = {
        "direction": p.get("direction"),
        "setup_score": p.get("setup_score"),
        "entry_price": p.get("entry_price"),
        "stop_price": p.get("stop_price"),
        "tp1_price": p.get("tp1_price"),
        "tp2_price": p.get("tp2_price"),
        "r_value": p.get("r_value"),
        "time_stop_bars": p.get("time_stop_bars"),
        "raw_score": p.get("raw_score"),
    }
    return result


def prefetch_ohlcv_for_symbols(symbols: list[str]) -> dict[str, np.ndarray]:
    """
    Fetch 1h OHLCV data for a list of symbols covering the opportunity window + 24h forward.
    Returns dict: symbol -> structured numpy array with bar_start_ts, open, high, low, close.

    We use 1h bars for all forward return calculations because:
    - They provide hourly resolution for 1h/4h/12h/24h forward windows
    - Available for ALL symbols back to 2022
    - Much more compact than 1m/5m bars
    """
    if not symbols:
        return {}

    # Date range: opportunities span 2026-01-19 to 2026-02-20, need +24h+1h forward
    start_ts = datetime(2026, 1, 18, 0, 0, 0, tzinfo=timezone.utc)
    end_ts = datetime(2026, 2, 22, 0, 0, 0, tzinfo=timezone.utc)

    sql = """
        SELECT symbol, bar_start_ts, open, high, low, close
        FROM ohlcv
        WHERE symbol = ANY(%s)
          AND timeframe = '1h'
          AND bar_start_ts >= %s
          AND bar_start_ts < %s
          AND is_final = true
          AND source = 'binance'
        ORDER BY symbol, bar_start_ts ASC
    """

    result = {}
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbols, start_ts, end_ts))

            # Group by symbol
            current_symbol = None
            bars = []
            for row in cur:
                sym, ts, o, h, l, c = row
                if sym != current_symbol:
                    if current_symbol is not None and bars:
                        result[current_symbol] = _bars_to_array(bars)
                    current_symbol = sym
                    bars = []
                bars.append((ts.timestamp(), o, h, l, c))

            if current_symbol is not None and bars:
                result[current_symbol] = _bars_to_array(bars)

    return result


def _bars_to_array(bars: list[tuple]) -> np.ndarray:
    """Convert list of (ts, o, h, l, c) tuples to structured numpy array."""
    dt = np.dtype([
        ("ts", np.float64),
        ("open", np.float64),
        ("high", np.float64),
        ("low", np.float64),
        ("close", np.float64),
    ])
    arr = np.array(bars, dtype=dt)
    return arr


def compute_forward_outcomes(
    opp_created_ts: float,
    direction: str | None,
    ohlcv_arr: np.ndarray | None,
) -> dict[str, Any]:
    """
    Compute forward returns, MFE, MAE, and TP-hit flags from 1h OHLCV bars.

    opp_created_ts: Unix epoch seconds of the opportunity
    direction: 'long' or 'short'
    ohlcv_arr: structured numpy array of 1h bars for this symbol
    """
    empty = {
        "fwd_return_1h": None, "fwd_return_4h": None,
        "fwd_return_12h": None, "fwd_return_24h": None,
        "mfe_24h": None, "mae_24h": None,
        "tp_hit_50bps": None, "tp_hit_100bps": None,
        "tp_hit_150bps": None, "tp_hit_200bps": None,
        "tp_hit_300bps": None, "is_true_positive": None,
    }

    if ohlcv_arr is None or len(ohlcv_arr) == 0 or direction is None:
        return empty

    ts_arr = ohlcv_arr["ts"]

    # Find the entry bar: last bar where bar_start_ts <= opp_created_ts
    idx = np.searchsorted(ts_arr, opp_created_ts, side="right") - 1
    if idx < 0:
        return empty

    entry_close = ohlcv_arr["close"][idx]
    if entry_close <= 0:
        return empty

    # Forward bars for MFE/MAE: strict 24h window from entry bar
    window_end_ts = opp_created_ts + WINDOW_H * 3600
    fwd_mask = (ts_arr > ts_arr[idx]) & (ts_arr <= window_end_ts)
    fwd_indices = np.where(fwd_mask)[0]

    if len(fwd_indices) == 0:
        return empty

    fwd_bars = ohlcv_arr[fwd_indices]

    # Extended window for forward return lookups: add 1h slack so that when
    # an opportunity is created mid-hour (e.g., 21:03), the bar at the next
    # full hour after the horizon (e.g., 22:00 for a 24h target at 21:03)
    # is still reachable. Without this, fwd_return_24h would be null for
    # most opportunities since 1h bars rarely align exactly with opp_ts + Nh.
    extended_end_ts = window_end_ts + 3600  # +1h slack
    ext_mask = (ts_arr > ts_arr[idx]) & (ts_arr <= extended_end_ts)
    ext_indices = np.where(ext_mask)[0]

    # Forward returns at specific horizons (using close prices)
    result = {}
    for h in FWD_HORIZONS_H:
        target_ts = opp_created_ts + h * 3600
        # Find closest bar at or after the target timestamp (in extended window)
        h_mask = ts_arr[ext_indices] >= target_ts
        if np.any(h_mask):
            h_idx = ext_indices[np.argmax(h_mask)]  # first True
            fwd_close = ohlcv_arr["close"][h_idx]
            ret = (fwd_close - entry_close) / entry_close
            result[f"fwd_return_{h}h"] = round(ret, 8)
        else:
            result[f"fwd_return_{h}h"] = None

    # MFE and MAE over strict 24h window (not extended)
    fwd_highs = fwd_bars["high"]
    fwd_lows = fwd_bars["low"]

    if direction == "long":
        # MFE = max upside from entry
        mfe = (np.max(fwd_highs) - entry_close) / entry_close
        # MAE = max downside from entry
        mae = (entry_close - np.min(fwd_lows)) / entry_close
    else:  # short
        # MFE = max downside from entry (favorable for shorts)
        mfe = (entry_close - np.min(fwd_lows)) / entry_close
        # MAE = max upside from entry (adverse for shorts)
        mae = (np.max(fwd_highs) - entry_close) / entry_close

    result["mfe_24h"] = round(float(mfe), 8)
    result["mae_24h"] = round(float(mae), 8)

    # TP hit flags: did price move >= threshold in signal direction?
    for bps in TP_THRESHOLDS_BPS:
        threshold = bps / 10000.0
        hit = mfe >= threshold
        result[f"tp_hit_{bps}bps"] = bool(hit)

    # True positive: price moved >1% in signal direction within 24h
    result["is_true_positive"] = bool(mfe > 0.01)

    return result


def process_symbol_batch(args: tuple) -> list[dict]:
    """
    Process all opportunities for a batch of symbols.
    Fetches OHLCV once per symbol batch, then computes outcomes.

    args: (list of opp dicts, batch_id)
    Returns: list of result dicts ready for CSV
    """
    opps, batch_id = args
    if not opps:
        return []

    # Get unique symbols in this batch
    symbols = list(set(o["symbol"] for o in opps))

    # Fetch OHLCV for all symbols in batch
    try:
        ohlcv_data = prefetch_ohlcv_for_symbols(symbols)
    except Exception as e:
        log.error(f"Batch {batch_id}: OHLCV fetch failed: {e}")
        return []

    results = []
    for opp in opps:
        # Parse payload
        payload = parse_payload(opp.get("payload_json"))
        direction = payload.get("direction")

        # Get OHLCV array for this symbol
        ohlcv_arr = ohlcv_data.get(opp["symbol"])

        # Compute forward outcomes
        created_ts_epoch = opp["created_ts"].timestamp()
        outcomes = compute_forward_outcomes(created_ts_epoch, direction, ohlcv_arr)

        # Build payload features string (compact JSON of non-None extras)
        feat_keys = ["setup_score", "entry_price", "stop_price", "tp1_price",
                     "tp2_price", "r_value", "time_stop_bars", "raw_score"]
        payload_features = {k: payload[k] for k in feat_keys if payload.get(k) is not None}

        row = {
            "opp_id": opp["id"],
            "created_ts": opp["created_ts"].isoformat(),
            "symbol": opp["symbol"],
            "timeframe": opp["timeframe"],
            "setup_id": opp["setup_id"],
            "direction": direction,
            "score": opp["score"],
            **outcomes,
            "payload_features": json.dumps(payload_features) if payload_features else "",
        }
        results.append(row)

    return results


def group_opps_by_symbol(opps: list[dict]) -> dict[str, list[dict]]:
    """Group opportunities by symbol for efficient OHLCV fetching."""
    groups = {}
    for opp in opps:
        sym = opp["symbol"]
        if sym not in groups:
            groups[sym] = []
        groups[sym].append(opp)
    return groups


def create_balanced_batches(opps: list[dict], n_batches: int) -> list[list[dict]]:
    """
    Create balanced work batches by distributing symbols round-robin.
    Symbols with more opps are heavier — sort heaviest first so they
    spread across batches via round-robin, avoiding tail imbalance.
    """
    by_symbol = group_opps_by_symbol(opps)

    # Sort symbols by opp count descending (heaviest first)
    sorted_symbols = sorted(by_symbol.keys(), key=lambda s: len(by_symbol[s]), reverse=True)

    batches = [[] for _ in range(n_batches)]
    batch_sizes = [0] * n_batches

    # Greedy assignment: assign each symbol to the lightest batch
    for sym in sorted_symbols:
        lightest = min(range(n_batches), key=lambda i: batch_sizes[i])
        batches[lightest].extend(by_symbol[sym])
        batch_sizes[lightest] += len(by_symbol[sym])

    log.info(f"Batch sizes: min={min(batch_sizes)}, max={max(batch_sizes)}, "
             f"ratio={max(batch_sizes)/max(min(batch_sizes),1):.1f}x")

    return batches


def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("Ignition Baseline Outcomes Extraction")
    log.info("=" * 60)

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Fetch all opportunities
    opps = fetch_all_opportunities()
    if not opps:
        log.error("No opportunities found!")
        sys.exit(1)

    # Count unique symbols
    symbols = set(o["symbol"] for o in opps)
    log.info(f"Unique symbols: {len(symbols)}")
    log.info(f"Setup distribution: { {sid: sum(1 for o in opps if o['setup_id']==sid) for sid in sorted(set(o['setup_id'] for o in opps))} }")

    # 2. Create balanced batches for parallel processing
    batches = create_balanced_batches(opps, N_WORKERS)
    work_args = [(batch, i) for i, batch in enumerate(batches)]

    # 3. Process in parallel
    log.info(f"Processing {len(opps):,} opportunities across {N_WORKERS} workers...")
    t1 = time.time()

    with mp.Pool(N_WORKERS) as pool:
        batch_results = pool.map(process_symbol_batch, work_args)

    all_results = []
    for br in batch_results:
        all_results.extend(br)

    elapsed = time.time() - t1
    log.info(f"Forward outcome computation done: {len(all_results):,} rows in {elapsed:.1f}s")

    # Sort by opp_id for deterministic output
    all_results.sort(key=lambda r: r["opp_id"])

    # 4. Write CSV
    log.info(f"Writing CSV to {OUTPUT_PATH}...")
    t2 = time.time()

    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        written = 0
        for row in all_results:
            writer.writerow(row)
            written += 1
            if written % 5000 == 0:
                log.info(f"  Written {written:,}/{len(all_results):,} rows")

    log.info(f"CSV written: {written:,} rows in {time.time() - t2:.1f}s")

    # 5. Summary stats
    n_with_fwd = sum(1 for r in all_results if r.get("fwd_return_1h") is not None)
    n_tp = sum(1 for r in all_results if r.get("is_true_positive") is True)
    n_long = sum(1 for r in all_results if r.get("direction") == "long")
    n_short = sum(1 for r in all_results if r.get("direction") == "short")

    log.info("=" * 60)
    log.info("SUMMARY")
    log.info(f"  Total opportunities: {len(all_results):,}")
    log.info(f"  With forward data:   {n_with_fwd:,} ({100*n_with_fwd/len(all_results):.1f}%)")
    log.info(f"  True positives (>1%): {n_tp:,} ({100*n_tp/max(n_with_fwd,1):.1f}% of those with data)")
    log.info(f"  Direction split:     {n_long:,} long / {n_short:,} short")

    # TP hit rates
    for bps in TP_THRESHOLDS_BPS:
        key = f"tp_hit_{bps}bps"
        n_hit = sum(1 for r in all_results if r.get(key) is True)
        log.info(f"  TP hit {bps}bps: {n_hit:,} ({100*n_hit/max(n_with_fwd,1):.1f}%)")

    # Mean forward returns
    for h in FWD_HORIZONS_H:
        key = f"fwd_return_{h}h"
        vals = [r[key] for r in all_results if r.get(key) is not None]
        if vals:
            log.info(f"  Mean fwd return {h}h: {np.mean(vals)*100:.4f}% "
                     f"(median: {np.median(vals)*100:.4f}%, std: {np.std(vals)*100:.4f}%)")

    # MFE/MAE
    mfe_vals = [r["mfe_24h"] for r in all_results if r.get("mfe_24h") is not None]
    mae_vals = [r["mae_24h"] for r in all_results if r.get("mae_24h") is not None]
    if mfe_vals:
        log.info(f"  Mean MFE 24h: {np.mean(mfe_vals)*100:.4f}% (median: {np.median(mfe_vals)*100:.4f}%)")
    if mae_vals:
        log.info(f"  Mean MAE 24h: {np.mean(mae_vals)*100:.4f}% (median: {np.median(mae_vals)*100:.4f}%)")

    total_elapsed = time.time() - t0
    log.info(f"  Total elapsed: {total_elapsed:.1f}s")
    log.info(f"  Output: {OUTPUT_PATH}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
