#!/usr/bin/env python3
"""
S9 Granularity Comparison: 1m (champ) vs 1s (challenger)
=========================================================

Runs S9 exit walk-forward at both 1m and 1s resolution for the last 30 days.
Compares PF, exit reason distribution, and per-opportunity deltas.

If 1s materially improves PF, it becomes the new default for S9 in
EXIT_WALK_TIMEFRAME. If PF is within noise, 1m is validated.

Run in tmux on production:
    tmux new -s s9_1s_test
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/s9_granularity_1m_vs_1s.py
"""

import argparse
import bisect
import os
import sys
import json
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

# Detect execution context
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))

from exit_checker import check_exit_simple, ExitConfig

logger = logging.getLogger("s9_1m_vs_1s")

DATABASE_URL = os.getenv("DATABASE_URL")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# S9 exit config: pure time-stop, no fail-fast, no trail
S9_EXIT_CFG = ExitConfig(
    fail_fast_bars=0, fail_fast_threshold=0.0,
    trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
)

# Slippage and fees (S9 @ 1h timeframe = 5 bps)
SLIPPAGE_BPS = 5.0
FEE_BPS = 4.0

# Binance 1s kline endpoint
BINANCE_KLINE_URL = "https://api.binance.com/api/v3/klines"

# Fetch config
FETCH_WORKERS = 6       # Parallel symbol fetches
KLINES_PER_REQUEST = 1000  # Binance max per request

# Minimum coverage to accept a 1s window (Critical Fix 3)
MIN_COVERAGE_PCT = 0.80


# ---------------------------------------------------------------------------
# Thread-safe rate limiter (Critical Fix 2)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Thread-safe token bucket rate limiter.

    Binance /api/v3/klines costs weight 2 per request.
    1200 weight/min = 600 req/min = 10 req/s.
    We use 8 req/s for safety margin.
    """

    def __init__(self, max_per_second: float = 8.0):
        self._lock = threading.Lock()
        self._min_interval = 1.0 / max_per_second
        self._last_request = 0.0

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()


rate_limiter = RateLimiter(max_per_second=8.0)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db_connection():
    import psycopg
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL)


def load_s9_opportunities(conn, days=30):
    """Load expired S9 opportunities from the last N days."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, symbol, created_ts, payload_json
        FROM opportunities
        WHERE setup_id = 'S9'
          AND status = 'expired'
          AND created_ts > NOW() - %s * INTERVAL '1 day'
        ORDER BY created_ts
    """, (days,))

    opps = []
    for row in cur:
        opp_id, symbol, created_ts, payload_json = row
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}

        entry_price = payload.get("entry_price")
        stop_price = payload.get("stop_price")
        tp1_price = payload.get("tp1_price")
        tp2_price = payload.get("tp2_price")
        direction = payload.get("direction", "short")
        time_stop_bars = payload.get("time_stop_bars", 48)

        if not entry_price or not stop_price:
            continue

        # Micro entry
        micro = payload.get("microstructure", {})
        use_micro = (
            isinstance(micro, dict)
            and micro.get("entry_type") not in (None, "SKIP")
            and micro.get("suggested_entry_price") is not None
        )
        actual_entry = micro["suggested_entry_price"] if use_micro else entry_price

        if created_ts.tzinfo is None:
            created_ts = created_ts.replace(tzinfo=timezone.utc)

        opps.append({
            "id": opp_id,
            "symbol": symbol,
            "created_ts": created_ts,
            "direction": direction,
            "entry_price": actual_entry,
            "stop_price": stop_price,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "time_stop_bars": time_stop_bars,
            "use_micro": use_micro,
        })

    cur.close()
    logger.info("Loaded %d S9 expired opportunities (last %d days)", len(opps), days)
    return opps


def load_ohlcv_bars(conn, symbol, timeframe, start_ts, end_ts):
    """Load OHLCV bars for one symbol/timeframe from DB."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (bar_start_ts)
               bar_start_ts, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = %s AND timeframe = %s
          AND bar_start_ts >= %s AND bar_start_ts < %s
          AND is_final = TRUE
        ORDER BY bar_start_ts,
                 CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END
    """, (symbol, timeframe, start_ts, end_ts))

    bars = [
        {
            "bar_start_ts": row[0].replace(tzinfo=timezone.utc)
                            if row[0].tzinfo is None else row[0],
            "open": row[1], "high": row[2], "low": row[3],
            "close": row[4], "volume": row[5],
        }
        for row in cur
    ]
    cur.close()
    return bars


# ---------------------------------------------------------------------------
# Merged fetch windows (Critical Fix 1)
# ---------------------------------------------------------------------------

def compute_fetch_windows(opps, hold_hours=48, buffer_hours=2):
    """Compute minimal merged time windows per symbol.

    Each opportunity needs [created_ts, created_ts + hold + buffer].
    Overlapping windows for the same symbol get merged.
    Returns {symbol: [(start_dt, end_dt), ...]}
    """
    raw_windows = defaultdict(list)
    for opp in opps:
        start = opp["created_ts"]
        end = start + timedelta(hours=hold_hours + buffer_hours)
        raw_windows[opp["symbol"]].append((start, end))

    merged = {}
    for symbol, windows in raw_windows.items():
        windows.sort()
        result = [list(windows[0])]
        for start, end in windows[1:]:
            if start <= result[-1][1]:  # overlapping
                result[-1][1] = max(result[-1][1], end)
            else:
                result.append([start, end])
        merged[symbol] = [(s, e) for s, e in result]

    return merged


def estimate_fetch_requests(merged_windows):
    """Estimate total API requests needed for all merged windows."""
    total_seconds = 0
    total_windows = 0
    for sym, windows in merged_windows.items():
        for start, end in windows:
            total_seconds += (end - start).total_seconds()
            total_windows += 1
    total_bars = total_seconds  # 1 bar per second
    total_requests = int(total_bars / KLINES_PER_REQUEST) + total_windows
    return total_requests, total_windows, total_seconds


# ---------------------------------------------------------------------------
# Binance 1s kline fetcher (paginated for long ranges)
# ---------------------------------------------------------------------------

def fetch_1s_range(symbol, start_ms, end_ms):
    """Fetch 1s klines for a full time range, paginating at 1000 per request.

    Uses the global rate_limiter for thread-safe rate control.
    Returns list of bar dicts sorted by time.
    """
    import requests

    bars = []
    cursor_ms = int(start_ms)
    end_ms = int(end_ms)

    while cursor_ms < end_ms:
        rate_limiter.acquire()

        params = {
            "symbol": symbol,
            "interval": "1s",
            "startTime": cursor_ms,
            "endTime": end_ms,
            "limit": KLINES_PER_REQUEST,
        }

        data = None
        for attempt in range(3):
            try:
                resp = requests.get(BINANCE_KLINE_URL, params=params, timeout=15)
                if resp.status_code in (418, 429):
                    wait = max(int(resp.headers.get("Retry-After", "5")), 2 ** attempt)
                    logger.warning("Rate limited on %s, waiting %ds", symbol, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt == 2:
                    logger.debug("1s fetch failed for %s at %d: %s", symbol, cursor_ms, e)
                    return bars  # Return what we have
                time.sleep(2 ** attempt)

        if not data:
            break

        for k in data:
            ts_ms = int(k[0])
            bars.append({
                "bar_start_ts": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

        # Advance cursor past the last returned kline
        last_ts = int(data[-1][0])
        if last_ts <= cursor_ms:
            break  # No progress
        cursor_ms = last_ts + 1000  # +1s

    return bars


def fetch_symbol_windows(symbol, windows):
    """Fetch 1s bars for all merged windows of a single symbol.

    Returns concatenated list of bars across all windows.
    """
    all_bars = []
    for start_dt, end_dt in windows:
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        bars = fetch_1s_range(symbol, start_ms, end_ms)
        all_bars.extend(bars)
    return all_bars


# ---------------------------------------------------------------------------
# Coverage validation (Critical Fix 3)
# ---------------------------------------------------------------------------

def has_sufficient_coverage(bars, expected_start, expected_end,
                            min_coverage_pct=MIN_COVERAGE_PCT):
    """Check if 1s bars cover at least min_coverage_pct of the expected window."""
    if not bars:
        return False
    actual_start = bars[0]["bar_start_ts"]
    actual_end = bars[-1]["bar_start_ts"]
    expected_duration = (expected_end - expected_start).total_seconds()
    if expected_duration <= 0:
        return False
    actual_duration = (actual_end - actual_start).total_seconds()
    return actual_duration >= expected_duration * min_coverage_pct


# ---------------------------------------------------------------------------
# Exit walk (shared for both resolutions) — Performance Fix 2: bisect
# ---------------------------------------------------------------------------

def _precompute_timestamps(bars):
    """Extract sorted timestamp list for bisect lookups."""
    return [b["bar_start_ts"] for b in bars]


def _find_start_idx(timestamps, entry_ts):
    """Binary search for first bar at or after entry_ts."""
    idx = bisect.bisect_left(timestamps, entry_ts)
    return idx if idx < len(timestamps) else None


def walk_exit(bars, entry_ts, entry_price, stop_price, tp1_price, tp2_price,
              direction, r_value, time_stop_bars, exit_cfg,
              strategy_tf_seconds=3600, precomputed_ts=None):
    """Walk exit forward through bars. Returns (exit_reason, exit_price, exit_ts, bars_held).

    bars_held is in strategy-timeframe units (elapsed_seconds / strategy_tf_seconds).
    precomputed_ts: optional pre-built timestamp list for bisect (Performance Fix 2).
    """
    if not bars:
        return None, None, None, 0

    # Apply entry slippage
    if direction == "long":
        entry_with_slip = entry_price * (1 + SLIPPAGE_BPS / 10000)
    else:
        entry_with_slip = entry_price * (1 - SLIPPAGE_BPS / 10000)

    # Recompute r_value with slipped entry
    if direction == "long":
        actual_r = entry_with_slip - stop_price
    else:
        actual_r = stop_price - entry_with_slip
    if actual_r <= 0:
        return "SKIP_BAD_R", entry_price, entry_ts, 0

    hwm = None
    half_r_touched = False
    sim_stop = stop_price

    # Find first bar at or after entry_ts (Performance Fix 2: bisect)
    if precomputed_ts is not None:
        start_idx = _find_start_idx(precomputed_ts, entry_ts)
    else:
        start_idx = None
        for j, bar in enumerate(bars):
            if bar["bar_start_ts"] >= entry_ts:
                start_idx = j
                break
    if start_idx is None:
        return None, None, None, 0

    max_forward_seconds = 200 * strategy_tf_seconds  # max_bars_forward=200
    last_walked_idx = start_idx

    for j in range(start_idx, len(bars)):
        bar = bars[j]
        elapsed_s = (bar["bar_start_ts"] - entry_ts).total_seconds()

        if elapsed_s > max_forward_seconds:
            break

        last_walked_idx = j
        elapsed_strat_bars = int(elapsed_s / strategy_tf_seconds)
        is_entry_bar = (j == start_idx and elapsed_s < strategy_tf_seconds)

        result, new_hwm, new_half_r = check_exit_simple(
            side=direction,
            bar=bar,
            entry_price=entry_with_slip,
            stop_price=sim_stop,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=actual_r,
            bars_held=elapsed_strat_bars,
            max_bars=time_stop_bars,
            high_water_mark=hwm,
            half_r_touched=half_r_touched,
            config=exit_cfg,
            is_entry_bar=is_entry_bar,
        )
        hwm = new_hwm
        half_r_touched = new_half_r

        if result:
            reason, price = result
            return reason, price, bar["bar_start_ts"], elapsed_strat_bars

    # Force close at last walked bar
    last = bars[last_walked_idx]
    elapsed_s = (last["bar_start_ts"] - entry_ts).total_seconds()
    elapsed_strat_bars = int(elapsed_s / strategy_tf_seconds)
    return "TIME_STOP", last["close"], last["bar_start_ts"], elapsed_strat_bars


def compute_pnl(direction, entry_price, exit_price, position_usd=1000.0):
    """Compute PnL net of slippage and fees."""
    if not entry_price or not exit_price or entry_price <= 0:
        return None

    # Entry with slippage
    if direction == "long":
        entry_slip = entry_price * (1 + SLIPPAGE_BPS / 10000)
    else:
        entry_slip = entry_price * (1 - SLIPPAGE_BPS / 10000)

    # Exit with slippage
    if direction == "long":
        exit_slip = exit_price * (1 - SLIPPAGE_BPS / 10000)
    else:
        exit_slip = exit_price * (1 + SLIPPAGE_BPS / 10000)

    size = position_usd / entry_slip
    if direction == "long":
        pnl_gross = (exit_slip - entry_slip) * size
    else:
        pnl_gross = (entry_slip - exit_slip) * size

    fees = (entry_slip * size + exit_slip * size) * (FEE_BPS / 10000)
    return pnl_gross - fees


# ---------------------------------------------------------------------------
# Main comparison
# ---------------------------------------------------------------------------

def run_comparison(days=30):
    start_wall = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(
        OUTPUT_DIR,
        f"granularity_1m_vs_1s_s9_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt",
    )
    logger.info("Output: %s", output_path)

    conn = get_db_connection()

    # 1. Load opportunities
    opps = load_s9_opportunities(conn, days=days)
    if not opps:
        logger.error("No S9 opportunities found. Aborting.")
        conn.close()
        return

    # 2. Compute merged fetch windows (Critical Fix 1)
    merged_windows = compute_fetch_windows(opps, hold_hours=48, buffer_hours=2)
    unique_symbols = list(merged_windows.keys())
    est_requests, est_windows, est_seconds = estimate_fetch_requests(merged_windows)
    logger.info("Unique symbols: %d, merged windows: %d, "
                "estimated 1s API requests: %d (~%.0f min at 8 req/s)",
                len(unique_symbols), est_windows, est_requests,
                est_requests / 8.0 / 60.0)

    # Also compute absolute ranges per symbol for 1m/1h DB loads
    symbol_ranges = defaultdict(lambda: [datetime.max.replace(tzinfo=timezone.utc),
                                          datetime.min.replace(tzinfo=timezone.utc)])
    for opp in opps:
        sr = symbol_ranges[opp["symbol"]]
        sr[0] = min(sr[0], opp["created_ts"])
        sr[1] = max(sr[1], opp["created_ts"] + timedelta(hours=50))

    # 3. Load 1m bars from DB (champ) — absolute ranges are fine for DB loads
    logger.info("Loading 1m bars from DB for %d symbols...", len(unique_symbols))
    champ_cache = {}
    champ_ts_cache = {}  # precomputed timestamp lists for bisect
    for sym in unique_symbols:
        sr = symbol_ranges[sym]
        bars = load_ohlcv_bars(conn, sym, "1m", sr[0], sr[1])
        if bars:
            champ_cache[sym] = bars
            champ_ts_cache[sym] = _precompute_timestamps(bars)
    logger.info("1m bars loaded: %d symbols, %d total bars",
                len(champ_cache),
                sum(len(v) for v in champ_cache.values()))

    conn.close()

    # 4. Precompute opp_date_map (Performance Fix 1)
    opp_date_map = {opp["id"]: opp["created_ts"].date() for opp in opps}

    # Group opps by symbol for per-symbol processing
    opps_by_symbol = defaultdict(list)
    for opp in opps:
        opps_by_symbol[opp["symbol"]].append(opp)

    # 5. Process symbol-by-symbol: fetch 1s, run both walks, discard 1s
    #    (Performance Fix 3: memory-safe, no global 1s cache)
    results_1m = []
    results_1s = []
    both_valid = 0
    skipped_no_1m = 0
    skipped_no_1s = 0
    skipped_coverage = 0

    fetch_start = time.time()
    symbols_done = 0

    for sym in unique_symbols:
        sym_opps = opps_by_symbol[sym]

        # Check 1m data exists
        bars_1m = champ_cache.get(sym)
        if not bars_1m:
            skipped_no_1m += len(sym_opps)
            symbols_done += 1
            continue
        ts_1m = champ_ts_cache[sym]

        # Fetch 1s windows for this symbol only (Critical Fix 1 + Perf Fix 3)
        windows = merged_windows[sym]
        bars_1s = fetch_symbol_windows(sym, windows)

        if not bars_1s:
            skipped_no_1s += len(sym_opps)
            symbols_done += 1
            continue

        # Precompute 1s timestamp list for bisect
        ts_1s = _precompute_timestamps(bars_1s)

        # Walk each opportunity at both resolutions
        for opp in sym_opps:
            entry_ts = opp["created_ts"]
            r_value = abs(opp["entry_price"] - opp["stop_price"])
            if r_value <= 0:
                continue

            # Critical Fix 3: validate 1s coverage for this opportunity
            opp_end = entry_ts + timedelta(hours=opp["time_stop_bars"])
            if not has_sufficient_coverage(bars_1s, entry_ts, opp_end):
                skipped_coverage += 1
                continue  # Skip BOTH resolutions to keep paired

            common_args = dict(
                entry_ts=entry_ts,
                entry_price=opp["entry_price"],
                stop_price=opp["stop_price"],
                tp1_price=opp["tp1_price"],
                tp2_price=opp["tp2_price"],
                direction=opp["direction"],
                r_value=r_value,
                time_stop_bars=opp["time_stop_bars"],
                exit_cfg=S9_EXIT_CFG,
                strategy_tf_seconds=3600,
            )

            # Champ: 1m
            reason_1m, price_1m, ts_exit_1m, bh_1m = walk_exit(
                bars_1m, **common_args, precomputed_ts=ts_1m)

            # Challenger: 1s
            reason_1s, price_1s, ts_exit_1s, bh_1s = walk_exit(
                bars_1s, **common_args, precomputed_ts=ts_1s)

            if reason_1m is None or reason_1s is None:
                continue

            pnl_1m = compute_pnl(opp["direction"], opp["entry_price"], price_1m)
            pnl_1s = compute_pnl(opp["direction"], opp["entry_price"], price_1s)

            if pnl_1m is None or pnl_1s is None:
                continue

            both_valid += 1
            results_1m.append({
                "opp_id": opp["id"], "symbol": sym,
                "exit_reason": reason_1m, "exit_price": price_1m,
                "pnl_net": pnl_1m, "bars_held": bh_1m,
            })
            results_1s.append({
                "opp_id": opp["id"], "symbol": sym,
                "exit_reason": reason_1s, "exit_price": price_1s,
                "pnl_net": pnl_1s, "bars_held": bh_1s,
            })

        # Discard 1s data for this symbol (Performance Fix 3)
        del bars_1s, ts_1s

        symbols_done += 1
        if symbols_done % 5 == 0:
            elapsed = time.time() - fetch_start
            logger.info("Progress: %d/%d symbols (%.0fs elapsed, %d paired so far)",
                        symbols_done, len(unique_symbols), elapsed, both_valid)

    fetch_elapsed = time.time() - fetch_start
    logger.info("All symbols processed: %d paired results (%.0fs)", both_valid, fetch_elapsed)

    # 6. Compute comparison metrics
    lines = []
    lines.append("=" * 72)
    lines.append("S9 GRANULARITY COMPARISON: 1m (CHAMP) vs 1s (CHALLENGER)")
    lines.append("=" * 72)
    lines.append(f"Opportunities loaded:        {len(opps)}")
    lines.append(f"Both resolutions valid:      {both_valid}")
    lines.append(f"Skipped (no 1m data):        {skipped_no_1m}")
    lines.append(f"Skipped (no 1s data):        {skipped_no_1s}")
    lines.append(f"Skipped (1s coverage < {MIN_COVERAGE_PCT:.0%}): {skipped_coverage}")
    lines.append(f"1s fetch + walk time:        {fetch_elapsed:.0f}s")
    lines.append("")

    for label, results in [("1m (CHAMP)", results_1m), ("1s (CHALLENGER)", results_1s)]:
        pnls = [r["pnl_net"] for r in results]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
        wr = len(wins) / len(pnls) * 100 if pnls else 0

        # Exit reason distribution
        reason_counts = defaultdict(int)
        for r in results:
            reason_counts[r["exit_reason"]] += 1

        # Daily PF (Performance Fix 1: precomputed opp_date_map)
        by_day = defaultdict(list)
        for r in results:
            dt = opp_date_map.get(r["opp_id"])
            if dt:
                by_day[dt].append(r["pnl_net"])

        daily_pfs = []
        for day, day_pnls in sorted(by_day.items()):
            day_wins = sum(p for p in day_pnls if p > 0)
            day_losses = abs(sum(p for p in day_pnls if p <= 0))
            if day_losses > 0:
                daily_pfs.append(day_wins / day_losses)
            elif day_wins > 0:
                daily_pfs.append(10.0)  # cap

        avg_daily_pf = np.mean(daily_pfs) if daily_pfs else 0

        lines.append(f"--- {label} ---")
        lines.append(f"  Trades:        {len(pnls)}")
        lines.append(f"  Win rate:      {wr:.1f}%")
        lines.append(f"  Aggregate PF:  {pf:.3f}")
        lines.append(f"  Avg daily PF:  {avg_daily_pf:.3f}")
        lines.append(f"  Total PnL:     ${sum(pnls):.2f}")
        lines.append(f"  Avg PnL:       ${np.mean(pnls):.4f}" if pnls else "  Avg PnL: N/A")
        lines.append(f"  Avg bars held: {np.mean([r['bars_held'] for r in results]):.1f}")
        lines.append(f"  Exit reasons:")
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            lines.append(f"    {reason:20s} {count:5d} ({count/len(results)*100:.1f}%)")
        lines.append("")

    # Per-opportunity deltas
    changed = 0
    exit_changes = defaultdict(int)
    pnl_deltas = []

    for r1m, r1s in zip(results_1m, results_1s):
        if r1m["exit_reason"] != r1s["exit_reason"]:
            changed += 1
            key = f"{r1m['exit_reason']} -> {r1s['exit_reason']}"
            exit_changes[key] += 1
        pnl_deltas.append(r1s["pnl_net"] - r1m["pnl_net"])

    lines.append("--- DELTA (1s - 1m) ---")
    lines.append(f"  Opps with changed exit_reason: {changed}/{both_valid} "
                 f"({changed/max(both_valid,1)*100:.1f}%)")
    lines.append(f"  Mean PnL delta:  ${np.mean(pnl_deltas):.4f}" if pnl_deltas else "")
    lines.append(f"  Total PnL delta: ${sum(pnl_deltas):.2f}" if pnl_deltas else "")
    lines.append("")
    if exit_changes:
        lines.append("  Exit reason transitions:")
        for transition, count in sorted(exit_changes.items(), key=lambda x: -x[1]):
            lines.append(f"    {transition:40s} {count:5d}")
    lines.append("")

    # Verdict
    pf_1m = (sum(p for p in [r["pnl_net"] for r in results_1m] if p > 0) /
             max(abs(sum(p for p in [r["pnl_net"] for r in results_1m] if p <= 0)), 0.01))
    pf_1s = (sum(p for p in [r["pnl_net"] for r in results_1s] if p > 0) /
             max(abs(sum(p for p in [r["pnl_net"] for r in results_1s] if p <= 0)), 0.01))
    pf_delta = pf_1s - pf_1m

    lines.append("--- VERDICT ---")
    if abs(pf_delta) < 0.05:
        lines.append(f"  PF delta: {pf_delta:+.3f} — WITHIN NOISE. 1m validated for S9.")
    elif pf_delta > 0:
        lines.append(f"  PF delta: {pf_delta:+.3f} — 1s WINS. Consider upgrading S9 to 1s.")
    else:
        lines.append(f"  PF delta: {pf_delta:+.3f} — 1m WINS. 1s adds noise, keep 1m.")

    lines.append("")
    elapsed = time.time() - start_wall
    lines.append(f"Total runtime: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    lines.append("=" * 72)

    report = "\n".join(lines)
    logger.info("\n%s", report)

    with open(output_path, "w") as f:
        f.write(report)
    logger.info("Results written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description="S9 1m vs 1s granularity comparison")
    parser.add_argument("--days", type=int, default=30,
                        help="Look-back window in days (default: 30)")
    args = parser.parse_args()
    run_comparison(days=args.days)
