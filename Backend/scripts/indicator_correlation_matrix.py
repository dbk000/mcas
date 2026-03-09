#!/usr/bin/env python3
"""
Indicator Correlation Matrix — WP-6 Task 3
=============================================

Computes pairwise Spearman correlation across all numeric indicators
on rolling 30-day windows. Identifies redundant indicators and
correlation regime shifts.

Output:
  - scripts/output/indicator_correlations_{timestamp}.json
  - scripts/output/indicator_correlations_{timestamp}.txt

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/indicator_correlation_matrix.py
"""

import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import psycopg

# Runtime path detection
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

from indicators import FeatureFactory, OHLCV, INDICATOR_REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading"
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
if os.path.isdir("/scripts/output"):
    OUTPUT_DIR = "/scripts/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
OUTPUT_JSON = os.path.join(OUTPUT_DIR, f"indicator_correlations_{TIMESTAMP}.json")
OUTPUT_TXT = os.path.join(OUTPUT_DIR, f"indicator_correlations_{TIMESTAMP}.txt")
logger.info("Output JSON: %s", OUTPUT_JSON)
logger.info("Output TXT: %s", OUTPUT_TXT)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
N_SYMBOLS = 30              # Top symbols by volume
LOOKBACK_DAYS = 90          # Total data window
WINDOW_DAYS = 30            # Rolling correlation window
TIMEFRAME = "1h"
MIN_BARS_PER_WINDOW = 500   # 30 days * 24h = 720, require at least 500
WARMUP_BARS = 201           # FeatureFactory minimum

# Numeric fields from IndicatorSnapshot to correlate
INDICATOR_FIELDS = [
    "atr", "clv", "volume_z", "rsi", "rsi_sma", "rsi_slope",
    "sma50_slope_atr", "sma200_slope_atr", "ma_gap_atr",
    "ema20_slope_atr", "ema50_slope_atr",
    "bb_width", "macd_line", "macd_signal", "macd_histogram",
    "rvol", "range_to_volume_ratio", "volume_acceleration",
    "dormancy_score", "accum_divergence",
]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def get_top_symbols(conn, n=N_SYMBOLS):
    """Get top N symbols by recent OHLCV row count (proxy for volume)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol, COUNT(*) as cnt
            FROM ohlcv
            WHERE timeframe = %s
              AND bar_start_ts > NOW() - INTERVAL '7 days'
            GROUP BY symbol
            ORDER BY cnt DESC
            LIMIT %s
        """, (TIMEFRAME, n))
        return [row[0] for row in cur.fetchall()]


def load_ohlcv(conn, symbol, lookback_days=LOOKBACK_DAYS):
    """Load OHLCV bars for a symbol."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT bar_start_ts, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = %s AND timeframe = %s
              AND bar_start_ts > NOW() - %s * INTERVAL '1 day'
            ORDER BY bar_start_ts
        """, (symbol, TIMEFRAME, lookback_days + 10))  # +10 for warmup
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------
def compute_indicator_series(rows):
    """Compute indicator values for each bar using a sliding window.

    Returns dict of {field_name: [values]} aligned by bar index.
    """
    if len(rows) < WARMUP_BARS + 10:
        return None

    # Convert to OHLCV objects
    candles = []
    for row in rows:
        ts = row[0]
        ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, 'timestamp') else int(ts)
        candles.append(OHLCV(
            timestamp=ts_ms,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        ))

    ff = FeatureFactory()

    # Compute snapshot at each bar (sliding window of WARMUP_BARS)
    series = {f: [] for f in INDICATOR_FIELDS}
    timestamps = []

    for i in range(WARMUP_BARS, len(candles)):
        window = candles[i - WARMUP_BARS:i + 1]
        try:
            snap = ff.compute(window)
            for f in INDICATOR_FIELDS:
                val = getattr(snap, f, None)
                series[f].append(float(val) if val is not None else np.nan)
            timestamps.append(candles[i].timestamp)
        except Exception:
            for f in INDICATOR_FIELDS:
                series[f].append(np.nan)
            timestamps.append(candles[i].timestamp)

    return series, timestamps


def spearman_corr(x, y):
    """Compute Spearman rank correlation between two arrays."""
    # Remove NaN pairs
    mask = ~(np.isnan(x) | np.isnan(y))
    x_clean = x[mask]
    y_clean = y[mask]

    if len(x_clean) < 10:
        return np.nan

    from scipy.stats import spearmanr
    corr, _ = spearmanr(x_clean, y_clean)
    return corr


# ---------------------------------------------------------------------------
# Correlation computation
# ---------------------------------------------------------------------------
def compute_correlations(all_series):
    """Compute pairwise Spearman correlation across all symbols.

    all_series: list of (symbol, {field: [values]}) tuples

    Returns:
        overall_corr: {(field_a, field_b): mean_corr}
        corr_stability: {(field_a, field_b): std_of_rolling_corrs}
    """
    n_fields = len(INDICATOR_FIELDS)

    # Aggregate all values per field across symbols
    aggregated = {f: [] for f in INDICATOR_FIELDS}
    for symbol, series in all_series:
        for f in INDICATOR_FIELDS:
            aggregated[f].extend(series[f])

    # Convert to arrays
    arrays = {f: np.array(aggregated[f]) for f in INDICATOR_FIELDS}

    # Overall correlation matrix
    overall_corr = {}
    for i in range(n_fields):
        for j in range(i + 1, n_fields):
            fa = INDICATOR_FIELDS[i]
            fb = INDICATOR_FIELDS[j]
            corr = spearman_corr(arrays[fa], arrays[fb])
            overall_corr[(fa, fb)] = corr

    # Rolling correlation stability (per-symbol, 30-day windows)
    rolling_corrs = defaultdict(list)
    window_size = WINDOW_DAYS * 24  # bars per window

    for symbol, series in all_series:
        n_bars = len(series[INDICATOR_FIELDS[0]])
        if n_bars < window_size:
            continue

        # Slide window
        for start in range(0, n_bars - window_size, window_size // 3):
            end = start + window_size
            for i in range(n_fields):
                for j in range(i + 1, n_fields):
                    fa = INDICATOR_FIELDS[i]
                    fb = INDICATOR_FIELDS[j]
                    x = np.array(series[fa][start:end])
                    y = np.array(series[fb][start:end])
                    corr = spearman_corr(x, y)
                    if not np.isnan(corr):
                        rolling_corrs[(fa, fb)].append(corr)

    # Stability = std of rolling correlations
    corr_stability = {}
    for pair, corrs in rolling_corrs.items():
        if len(corrs) >= 3:
            corr_stability[pair] = float(np.std(corrs))
        else:
            corr_stability[pair] = np.nan

    return overall_corr, corr_stability, rolling_corrs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    lines = []

    def log(msg):
        logger.info(msg)
        lines.append(msg)

    log("=== Indicator Correlation Matrix (WP-6 Task 3) ===")
    log(f"Symbols: top {N_SYMBOLS} by volume")
    log(f"Lookback: {LOOKBACK_DAYS} days, Window: {WINDOW_DAYS} days")
    log(f"Timeframe: {TIMEFRAME}")
    log(f"Fields: {len(INDICATOR_FIELDS)}")
    log("")

    with psycopg.connect(DATABASE_URL) as conn:
        # 1. Get symbols
        symbols = get_top_symbols(conn)
        log(f"Step 1: Selected {len(symbols)} symbols")

        # 2. Load OHLCV and compute indicators
        log("Step 2: Computing indicators per symbol...")
        all_series = []
        for si, sym in enumerate(symbols):
            rows = load_ohlcv(conn, sym)
            if len(rows) < WARMUP_BARS + 100:
                logger.debug("  %s: insufficient bars (%d), skipping", sym, len(rows))
                continue

            result = compute_indicator_series(rows)
            if result is not None:
                series, timestamps = result
                n_bars = len(series[INDICATOR_FIELDS[0]])
                all_series.append((sym, series))
                if (si + 1) % 10 == 0:
                    logger.info("  Progress: %d/%d symbols (%d bars for %s)",
                                si + 1, len(symbols), n_bars, sym)

        log(f"  Computed indicators for {len(all_series)} symbols")
        log("")

        if len(all_series) < 5:
            log("ERROR: Too few symbols with data. Aborting.")
            with open(OUTPUT_TXT, "w") as f:
                f.write("\n".join(lines))
            return

        # 3. Compute correlations
        log("Step 3: Computing pairwise correlations...")
        overall_corr, corr_stability, rolling_corrs = compute_correlations(all_series)

        # 4. Find highly correlated pairs (|corr| > 0.7) — redundancy candidates
        log("")
        log("=== HIGHLY CORRELATED PAIRS (|r| > 0.7) ===")
        log(f"{'Pair':<45} {'Corr':>8} {'Stability':>10} {'Regime Shift?':>14}")
        log("-" * 80)

        redundant_pairs = []
        for (fa, fb), corr in sorted(overall_corr.items(), key=lambda x: -abs(x[1])):
            if abs(corr) > 0.7 and not np.isnan(corr):
                stab = corr_stability.get((fa, fb), np.nan)
                regime_shift = "YES" if not np.isnan(stab) and stab > 0.15 else "No"
                pair_str = f"{fa} × {fb}"
                log(f"{pair_str:<45} {corr:>8.3f} {stab:>10.3f} {regime_shift:>14}")
                redundant_pairs.append({
                    "indicator_a": fa,
                    "indicator_b": fb,
                    "correlation": round(corr, 4),
                    "stability_std": round(stab, 4) if not np.isnan(stab) else None,
                    "regime_shift": regime_shift == "YES",
                })

        log(f"\nTotal redundant pairs (|r| > 0.7): {len(redundant_pairs)}")

        # 5. Find regime-shifting pairs (stability > 0.2)
        log("")
        log("=== REGIME-SHIFTING CORRELATIONS (stability std > 0.2) ===")
        regime_shifts = []
        for (fa, fb), stab in sorted(corr_stability.items(), key=lambda x: -x[1] if not np.isnan(x[1]) else -99):
            if not np.isnan(stab) and stab > 0.2:
                corr = overall_corr.get((fa, fb), np.nan)
                pair_str = f"{fa} × {fb}"
                log(f"  {pair_str:<45} corr={corr:.3f}  stability_std={stab:.3f}")

                # Get rolling corr range
                rc = rolling_corrs.get((fa, fb), [])
                if rc:
                    regime_shifts.append({
                        "indicator_a": fa,
                        "indicator_b": fb,
                        "mean_corr": round(float(np.mean(rc)), 4),
                        "min_corr": round(float(np.min(rc)), 4),
                        "max_corr": round(float(np.max(rc)), 4),
                        "stability_std": round(stab, 4),
                    })

        log(f"\nTotal regime-shifting pairs: {len(regime_shifts)}")

        # 6. Full correlation matrix (for JSON output)
        corr_matrix = {}
        for (fa, fb), corr in overall_corr.items():
            if not np.isnan(corr):
                key = f"{fa}|{fb}"
                corr_matrix[key] = round(corr, 4)

        # 7. Per-indicator average |correlation| — how redundant is each indicator?
        log("")
        log("=== INDICATOR REDUNDANCY SCORE (avg |correlation| with all others) ===")
        log(f"{'Indicator':<30} {'Avg |r|':>10} {'Max |r|':>10} {'Most Correlated With':<30}")
        log("-" * 85)

        ind_redundancy = {}
        for f in INDICATOR_FIELDS:
            corrs_for_f = []
            max_pair = ("", 0.0)
            for (fa, fb), corr in overall_corr.items():
                if np.isnan(corr):
                    continue
                if fa == f or fb == f:
                    corrs_for_f.append(abs(corr))
                    other = fb if fa == f else fa
                    if abs(corr) > abs(max_pair[1]):
                        max_pair = (other, corr)

            avg_abs_corr = float(np.mean(corrs_for_f)) if corrs_for_f else 0.0
            max_abs_corr = float(np.max(corrs_for_f)) if corrs_for_f else 0.0
            ind_redundancy[f] = {
                "avg_abs_corr": round(avg_abs_corr, 4),
                "max_abs_corr": round(max_abs_corr, 4),
                "most_correlated_with": max_pair[0],
                "most_correlated_r": round(max_pair[1], 4),
            }
            log(f"{f:<30} {avg_abs_corr:>10.4f} {max_abs_corr:>10.4f} {max_pair[0]:<30}")

    elapsed = time.time() - t0
    log(f"\nCompleted in {elapsed:.1f}s")

    # Write outputs
    output_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "n_symbols": len(all_series),
            "lookback_days": LOOKBACK_DAYS,
            "window_days": WINDOW_DAYS,
            "timeframe": TIMEFRAME,
            "n_fields": len(INDICATOR_FIELDS),
        },
        "correlation_matrix": corr_matrix,
        "redundant_pairs": redundant_pairs,
        "regime_shifts": regime_shifts,
        "indicator_redundancy": ind_redundancy,
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output_data, f, indent=2, default=str)
    logger.info("JSON written to %s", OUTPUT_JSON)

    with open(OUTPUT_TXT, "w") as f:
        f.write("\n".join(lines))
    logger.info("TXT written to %s", OUTPUT_TXT)


if __name__ == "__main__":
    main()
