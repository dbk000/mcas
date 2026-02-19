#!/usr/bin/env python3
"""
OHLCV Completeness Verification
================================

Verifies that the ohlcv table contains the expected number of bars for every
(symbol, timeframe) combination across the full backfill date range.

For each timeframe:
  1. Compute expected bars per symbol = floor((range_end - range_start) / bar_interval)
  2. Count actual bars per symbol in DB
  3. Report: total expected vs actual, per-symbol gaps, gap time ranges

Output:
  - Console summary per timeframe (expected, actual, completeness %)
  - Per-symbol gap analysis for any symbol below threshold
  - Optional JSON report written to disk

Usage:
    docker compose run --rm -T worker python3 /scripts/verify_ohlcv_completeness.py
    docker compose run --rm -T worker python3 /scripts/verify_ohlcv_completeness.py --threshold 99.0
    docker compose run --rm -T worker python3 /scripts/verify_ohlcv_completeness.py --timeframes 1m,3m --json
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ─── Env + path setup ────────────────────────────────────────────────────────

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

import psycopg

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

TF_SECONDS = {
    '1s':  1,
    '1m':  60,
    '3m':  180,
    '5m':  300,
    '15m': 900,
    '30m': 1_800,
    '1h':  3_600,
    '2h':  7_200,
    '4h':  14_400,
    '6h':  21_600,
    '8h':  28_800,
    '12h': 43_200,
    '1d':  86_400,
    '3d':  259_200,
}

# Binance listing/delisting means some symbols won't span the full range.
# We use the per-symbol min/max as the expected range for that symbol,
# not the global range. This avoids false gap reports for late-listed tokens.


# ═══════════════════════════════════════════════════════════════════════════════
# CORE VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def get_timeframes_in_db(conn):
    """Return list of distinct timeframes present in ohlcv."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT timeframe FROM ohlcv
            WHERE is_final = TRUE
            ORDER BY timeframe
        """)
        return [row[0] for row in cur]


def get_tf_summary(conn, timeframe):
    """Per-symbol bar counts and time range for a given timeframe.

    Returns list of dicts: {symbol, count, min_ts, max_ts}
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol,
                   COUNT(*) as bar_count,
                   MIN(bar_start_ts) as min_ts,
                   MAX(bar_start_ts) as max_ts
            FROM ohlcv
            WHERE timeframe = %s AND is_final = TRUE
            GROUP BY symbol
            ORDER BY symbol
        """, (timeframe,))
        rows = []
        for r in cur:
            rows.append({
                "symbol": r[0],
                "count": r[1],
                "min_ts": r[2],
                "max_ts": r[3],
            })
        return rows


def find_gaps(conn, timeframe, symbol, bar_seconds):
    """Find temporal gaps for a specific (symbol, timeframe).

    A gap is any interval where the next bar_start_ts is more than
    1.5 × bar_interval after the previous bar. Returns list of
    (gap_start, gap_end, missing_bars).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT bar_start_ts
            FROM ohlcv
            WHERE timeframe = %s AND symbol = %s AND is_final = TRUE
            ORDER BY bar_start_ts
        """, (timeframe, symbol))
        timestamps = [row[0] for row in cur]

    if len(timestamps) < 2:
        return []

    bar_delta = timedelta(seconds=bar_seconds)
    gap_threshold = timedelta(seconds=int(bar_seconds * 1.5))
    gaps = []

    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i - 1]
        if diff > gap_threshold:
            missing = int(diff.total_seconds() / bar_seconds) - 1
            gaps.append({
                "gap_start": timestamps[i - 1].isoformat(),
                "gap_end": timestamps[i].isoformat(),
                "missing_bars": missing,
                "gap_hours": round(diff.total_seconds() / 3600, 1),
            })

    return gaps


def verify_timeframe(conn, timeframe, gap_threshold_pct, max_gap_symbols):
    """Run full verification for one timeframe.

    Returns a report dict with summary and per-symbol details.
    """
    bar_seconds = TF_SECONDS.get(timeframe)
    if bar_seconds is None:
        logger.warning("Unknown timeframe %s — skipping", timeframe)
        return None

    summaries = get_tf_summary(conn, timeframe)
    if not summaries:
        return {
            "timeframe": timeframe,
            "n_symbols": 0,
            "total_expected": 0,
            "total_actual": 0,
            "completeness_pct": 0.0,
            "symbols_below_threshold": [],
        }

    total_expected = 0
    total_actual = 0
    symbols_below = []

    for s in summaries:
        # Expected bars = time range / interval + 1 (inclusive of both endpoints)
        range_seconds = (s["max_ts"] - s["min_ts"]).total_seconds()
        expected = int(range_seconds / bar_seconds) + 1
        actual = s["count"]
        pct = (actual / expected * 100) if expected > 0 else 0.0

        total_expected += expected
        total_actual += actual

        if pct < gap_threshold_pct:
            entry = {
                "symbol": s["symbol"],
                "expected": expected,
                "actual": actual,
                "completeness_pct": round(pct, 2),
                "missing": expected - actual,
                "range_start": s["min_ts"].isoformat(),
                "range_end": s["max_ts"].isoformat(),
            }
            symbols_below.append(entry)

    # Sort worst-first
    symbols_below.sort(key=lambda x: x["completeness_pct"])

    overall_pct = (total_actual / total_expected * 100) if total_expected > 0 else 0.0

    report = {
        "timeframe": timeframe,
        "bar_interval": f"{bar_seconds}s",
        "n_symbols": len(summaries),
        "total_expected": total_expected,
        "total_actual": total_actual,
        "completeness_pct": round(overall_pct, 4),
        "n_symbols_below_threshold": len(symbols_below),
        "symbols_below_threshold": symbols_below[:max_gap_symbols],
    }

    # Find gap details for the worst offenders (top 10)
    gap_detail_count = min(10, len(symbols_below))
    if gap_detail_count > 0:
        logger.info("    Scanning gaps for %d worst symbols...", gap_detail_count)
        gap_details = []
        for entry in symbols_below[:gap_detail_count]:
            gaps = find_gaps(conn, timeframe, entry["symbol"], bar_seconds)
            if gaps:
                gap_details.append({
                    "symbol": entry["symbol"],
                    "completeness_pct": entry["completeness_pct"],
                    "n_gaps": len(gaps),
                    "total_missing_bars": sum(g["missing_bars"] for g in gaps),
                    "largest_gap_hours": max(g["gap_hours"] for g in gaps),
                    "gaps": gaps[:5],  # top 5 gaps per symbol
                })
        report["gap_details"] = gap_details

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-TIMEFRAME CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════

def check_cross_tf_consistency(conn, timeframes):
    """Verify that all timeframes cover the same symbol set and date range.

    Flags:
      - Symbols present in one TF but missing in another
      - Timeframes with substantially different date ranges
    """
    tf_symbols = {}
    tf_ranges = {}

    for tf in timeframes:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT array_agg(DISTINCT symbol),
                       MIN(bar_start_ts),
                       MAX(bar_start_ts)
                FROM ohlcv
                WHERE timeframe = %s AND is_final = TRUE
            """, (tf,))
            row = cur.fetchone()
            if row and row[0]:
                tf_symbols[tf] = set(row[0])
                tf_ranges[tf] = (row[1], row[2])

    if len(tf_symbols) < 2:
        return {"consistent": True, "note": "< 2 timeframes with data"}

    # Find union and intersection of symbol sets
    all_symbols = set()
    for s in tf_symbols.values():
        all_symbols |= s
    common_symbols = set(all_symbols)
    for s in tf_symbols.values():
        common_symbols &= s

    missing_per_tf = {}
    for tf, syms in tf_symbols.items():
        missing = all_symbols - syms
        if missing:
            missing_per_tf[tf] = sorted(missing)

    # Date range comparison
    range_report = {}
    for tf, (mn, mx) in tf_ranges.items():
        range_report[tf] = {
            "start": mn.isoformat(),
            "end": mx.isoformat(),
            "span_days": round((mx - mn).total_seconds() / 86400, 1),
            "n_symbols": len(tf_symbols[tf]),
        }

    return {
        "n_timeframes": len(tf_symbols),
        "total_unique_symbols": len(all_symbols),
        "common_symbols": len(common_symbols),
        "missing_per_tf": {tf: len(v) for tf, v in missing_per_tf.items()} if missing_per_tf else {},
        "missing_examples": {tf: v[:5] for tf, v in missing_per_tf.items()} if missing_per_tf else {},
        "ranges": range_report,
        "consistent": len(missing_per_tf) == 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DUPLICATE CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def check_duplicates(conn, timeframes):
    """Check for duplicate (symbol, timeframe, bar_start_ts) rows.

    The unique index should prevent this, but belt-and-suspenders.
    """
    dupes = {}
    for tf in timeframes:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM (
                    SELECT symbol, bar_start_ts, COUNT(*) as cnt
                    FROM ohlcv
                    WHERE timeframe = %s AND is_final = TRUE
                    GROUP BY symbol, bar_start_ts
                    HAVING COUNT(*) > 1
                ) sub
            """, (tf,))
            n = cur.fetchone()[0]
            if n > 0:
                dupes[tf] = n
    return dupes


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="OHLCV Completeness Verification")
    parser.add_argument("--timeframes", type=str, default=None,
                        help="Comma-separated timeframes to check (default: all in DB)")
    parser.add_argument("--threshold", type=float, default=99.0,
                        help="Completeness %% below which to flag a symbol (default 99.0)")
    parser.add_argument("--max-gap-symbols", type=int, default=50,
                        help="Max symbols to list per TF in below-threshold report (default 50)")
    parser.add_argument("--json", action="store_true",
                        help="Write full report to JSON file")
    parser.add_argument("--no-gaps", action="store_true",
                        help="Skip per-symbol gap scanning (faster)")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    t_start = time.time()
    conn = psycopg.connect(db_url)

    logger.info("=" * 72)
    logger.info("OHLCV COMPLETENESS VERIFICATION")
    logger.info("=" * 72)
    logger.info("Threshold: %.1f%%  |  Gap scanning: %s",
                args.threshold, "OFF" if args.no_gaps else "ON")

    # ── Discover timeframes ──
    db_timeframes = get_timeframes_in_db(conn)
    if args.timeframes:
        timeframes = [tf.strip() for tf in args.timeframes.split(",")]
    else:
        timeframes = db_timeframes
    logger.info("Timeframes in DB: %s", db_timeframes)
    logger.info("Checking: %s\n", timeframes)

    # ── Per-timeframe verification ──
    tf_reports = []
    grand_expected = 0
    grand_actual = 0

    for tf in timeframes:
        logger.info("── %s ──", tf)
        report = verify_timeframe(conn, tf, args.threshold, args.max_gap_symbols)
        if report is None:
            continue

        tf_reports.append(report)
        grand_expected += report["total_expected"]
        grand_actual += report["total_actual"]

        status = "OK" if report["completeness_pct"] >= args.threshold else "GAPS"
        logger.info("    Symbols: %d  |  Expected: %s  |  Actual: %s  |  "
                     "Completeness: %.2f%%  [%s]",
                     report["n_symbols"],
                     f"{report['total_expected']:,}",
                     f"{report['total_actual']:,}",
                     report["completeness_pct"],
                     status)

        if report["n_symbols_below_threshold"] > 0:
            logger.info("    %d symbols below %.1f%% threshold",
                         report["n_symbols_below_threshold"], args.threshold)
            # Show worst 5
            for entry in report["symbols_below_threshold"][:5]:
                logger.info("      %-20s  %6.2f%%  (%d missing)",
                            entry["symbol"], entry["completeness_pct"],
                            entry["missing"])

        if not args.no_gaps and "gap_details" in report:
            for gd in report["gap_details"][:3]:
                logger.info("      %s: %d gaps, largest %.1fh, %d missing bars",
                            gd["symbol"], gd["n_gaps"],
                            gd["largest_gap_hours"], gd["total_missing_bars"])
        logger.info("")

    # ── Grand totals ──
    grand_pct = (grand_actual / grand_expected * 100) if grand_expected > 0 else 0.0
    logger.info("─" * 72)
    logger.info("GRAND TOTAL")
    logger.info("  Expected:     %s bars", f"{grand_expected:,}")
    logger.info("  Actual:       %s bars", f"{grand_actual:,}")
    logger.info("  Missing:      %s bars", f"{grand_expected - grand_actual:,}")
    logger.info("  Completeness: %.4f%%", grand_pct)

    # ── Cross-timeframe consistency ──
    logger.info("\n── CROSS-TIMEFRAME CONSISTENCY ──")
    consistency = check_cross_tf_consistency(conn, timeframes)
    logger.info("  Timeframes with data: %s", consistency.get("n_timeframes", 0))
    logger.info("  Total unique symbols: %s", consistency.get("total_unique_symbols", 0))
    logger.info("  Symbols in ALL timeframes: %s", consistency.get("common_symbols", 0))
    if consistency.get("missing_per_tf"):
        for tf, n in consistency["missing_per_tf"].items():
            examples = consistency["missing_examples"].get(tf, [])
            logger.info("  %s: missing %d symbols (e.g. %s)", tf, n,
                        ", ".join(examples[:3]))
    if consistency.get("ranges"):
        for tf, r in consistency["ranges"].items():
            logger.info("  %s: %s → %s  (%.1f days, %d symbols)",
                        tf, r["start"][:10], r["end"][:10],
                        r["span_days"], r["n_symbols"])
    logger.info("  Consistent: %s", consistency.get("consistent", "N/A"))

    # ── Duplicate check ──
    logger.info("\n── DUPLICATE CHECK ──")
    dupes = check_duplicates(conn, timeframes)
    if dupes:
        for tf, n in dupes.items():
            logger.info("  WARNING: %s has %d duplicate (symbol, bar_start_ts) groups", tf, n)
    else:
        logger.info("  No duplicates found")

    # ── Summary table ──
    logger.info("\n" + "=" * 72)
    logger.info("SUMMARY TABLE")
    logger.info("=" * 72)
    logger.info("%-6s  %8s  %14s  %14s  %10s  %8s",
                "TF", "Symbols", "Expected", "Actual", "Complete%", "Flagged")
    logger.info("-" * 72)
    for r in tf_reports:
        logger.info("%-6s  %8d  %14s  %14s  %9.2f%%  %8d",
                     r["timeframe"], r["n_symbols"],
                     f"{r['total_expected']:,}",
                     f"{r['total_actual']:,}",
                     r["completeness_pct"],
                     r["n_symbols_below_threshold"])
    logger.info("-" * 72)
    logger.info("%-6s  %8s  %14s  %14s  %9.2f%%",
                "TOTAL", "",
                f"{grand_expected:,}",
                f"{grand_actual:,}",
                grand_pct)
    logger.info("=" * 72)

    elapsed = time.time() - t_start
    logger.info("\nVerification completed in %.1f seconds", elapsed)

    conn.close()

    # ── Optional JSON report ──
    if args.json:
        out_dir = os.path.join(os.path.dirname(__file__), 'ohlcv_verification')
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        out_path = os.path.join(out_dir, f"completeness_{ts}.json")

        full_report = {
            "run_timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "threshold_pct": args.threshold,
            "grand_total": {
                "expected": grand_expected,
                "actual": grand_actual,
                "missing": grand_expected - grand_actual,
                "completeness_pct": round(grand_pct, 4),
            },
            "timeframes": tf_reports,
            "cross_tf_consistency": consistency,
            "duplicates": dupes,
        }
        with open(out_path, 'w') as f:
            json.dump(full_report, f, indent=2, default=str)
        logger.info("JSON report written to %s", out_path)


if __name__ == "__main__":
    main()
