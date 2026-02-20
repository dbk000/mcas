#!/usr/bin/env python3
"""
S9 1-Second Execution Backtest
==============================

Re-runs the S9 short reversion strategy over a recent period.
For each historical S9 signal, fetches 300 x 1-second klines after
the hourly close and simulates multiple entry strategies.

Compares aggregate PF between naive (close price) and 1s-optimised entry.
Quantifies the execution edge on the table.

Run in Docker:
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/s9_1s_execution_backtest.py

Or in tmux on the host:
    tmux new -s s9_backtest
    cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/s9_1s_execution_backtest.py --days 14
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

# Detect execution context
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))

from s9_microstructure import (
    fetch_1s_klines, simulate_entries, Kline1s,
)

logger = logging.getLogger("s9_1s_backtest")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
COST_BPS = 18       # Roundtrip cost (matching S9 validation)
PERCENTILE = 10     # Top decile
BREADTH_THRESHOLD = 0.3
HOLD_BARS = 48      # 48h hold
FETCH_WORKERS = 6   # Parallel 1s kline fetchers (conservative for rate limits)
BATCH_DELAY_S = 1.5 # Delay between hourly batches


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db_connection():
    """Get psycopg connection to production DB."""
    import psycopg
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL)


def load_1h_candles(conn, start_ts, end_ts):
    """Load all 1h candle closes for the analysis window.

    Returns dict: symbol -> list of (bar_start_ts, close) sorted by time.
    """
    logger.info("Loading 1h candles from %s to %s ...", start_ts, end_ts)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, bar_start_ts, close
        FROM ohlcv
        WHERE timeframe = '1h'
          AND bar_start_ts >= %s
          AND bar_start_ts <= %s
          AND is_final = true
        ORDER BY symbol, bar_start_ts
    """, (start_ts, end_ts))

    data = defaultdict(list)
    count = 0
    for row in cur:
        symbol, ts, close = row
        data[symbol].append((ts, close))
        count += 1

    cur.close()
    logger.info("Loaded %d candles for %d symbols", count, len(data))
    return data


def get_close_at_offset(candle_data, symbol, signal_ts, offset_bars):
    """Get close price at signal_ts + offset_bars hours.

    Returns (close_price, exit_ts) or (None, None) if not found.
    """
    candles = candle_data.get(symbol, [])
    target_ts = signal_ts + timedelta(hours=offset_bars)

    # Find closest candle at or after target_ts
    for ts, close in candles:
        if ts >= target_ts:
            return close, ts
    return None, None


# ---------------------------------------------------------------------------
# S9 signal re-derivation
# ---------------------------------------------------------------------------

def derive_s9_signals(candle_data, eval_hour):
    """Re-derive S9 signals for a specific hour from 1h candle data.

    Args:
        candle_data: dict symbol -> [(ts, close), ...] sorted by time
        eval_hour: datetime of the hourly bar to evaluate

    Returns:
        list of dicts with signal info, or empty list if breadth fails.
    """
    symbol_metrics = {}

    for symbol, candles in candle_data.items():
        # Build lookup: ts -> close
        ts_close = {ts: c for ts, c in candles}

        close_now = ts_close.get(eval_hour)
        close_4h = ts_close.get(eval_hour - timedelta(hours=4))
        close_24h = ts_close.get(eval_hour - timedelta(hours=24))

        if (close_now and close_now > 0 and close_4h and close_4h > 0
                and close_24h and close_24h > 0):
            ret_4h = close_now / close_4h - 1
            ret_24h = close_now / close_24h - 1
            symbol_metrics[symbol] = {
                "close": close_now,
                "ret_4h": ret_4h,
                "ret_24h": ret_24h,
            }

    if len(symbol_metrics) < 20:
        return []

    # Market breadth
    n_positive = sum(1 for m in symbol_metrics.values() if m["ret_24h"] > 0)
    breadth = n_positive / len(symbol_metrics)

    if breadth <= BREADTH_THRESHOLD:
        return []

    # Rank by ret_4h descending, select top decile
    sorted_syms = sorted(
        symbol_metrics.items(),
        key=lambda x: x[1]["ret_4h"],
        reverse=True,
    )
    decile_n = max(1, len(sorted_syms) // PERCENTILE)
    top_decile = sorted_syms[:decile_n]

    signals = []
    for rank, (symbol, metrics) in enumerate(top_decile):
        signals.append({
            "symbol": symbol,
            "eval_hour": eval_hour,
            "close": metrics["close"],
            "ret_4h": metrics["ret_4h"],
            "ret_24h": metrics["ret_24h"],
            "breadth": breadth,
            "rank": rank + 1,
            "decile_size": decile_n,
            "universe_size": len(symbol_metrics),
        })

    return signals


# ---------------------------------------------------------------------------
# 1s kline fetching with rate limit management
# ---------------------------------------------------------------------------

def fetch_1s_for_signals(signals, max_workers=FETCH_WORKERS):
    """Fetch 300 x 1s klines for each signal.

    Groups by eval_hour for efficient batching.
    Returns dict: (symbol, eval_hour_ts_ms) -> list[Kline1s]
    """
    results = {}
    total = len(signals)
    done = 0

    # Group signals by hour for progress logging
    by_hour = defaultdict(list)
    for sig in signals:
        by_hour[sig["eval_hour"]].append(sig)

    hours_sorted = sorted(by_hour.keys())
    logger.info("Fetching 1s klines for %d signals across %d hours",
                total, len(hours_sorted))

    for hour_idx, hour in enumerate(hours_sorted):
        hour_signals = by_hour[hour]
        # Start time is the hourly close timestamp in ms
        start_ms = int(hour.timestamp() * 1000) + 1000  # +1s after candle close

        def _fetch(sig):
            return (
                sig["symbol"],
                hour,
                fetch_1s_klines(sig["symbol"], start_ms, limit=300, timeout=15),
            )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_fetch, sig) for sig in hour_signals]
            for future in as_completed(futures):
                try:
                    sym, hr, klines = future.result()
                    key = (sym, hr)
                    if klines:
                        results[key] = klines
                    done += 1
                except Exception as e:
                    done += 1
                    logger.debug("1s fetch failed: %s", e)

        # Progress + rate limit pause between hours
        elapsed_pct = (hour_idx + 1) / len(hours_sorted) * 100
        logger.info(
            "Progress: %d/%d hours (%.0f%%), %d/%d signals fetched, %d with data",
            hour_idx + 1, len(hours_sorted), elapsed_pct,
            done, total, len(results),
        )

        if hour_idx < len(hours_sorted) - 1:
            time.sleep(BATCH_DELAY_S)

    return results


# ---------------------------------------------------------------------------
# PnL computation
# ---------------------------------------------------------------------------

def compute_trade_pnl(entry_price, exit_price, cost_bps=COST_BPS):
    """Compute PnL for a short trade.

    Returns PnL as a fraction (e.g., 0.01 = 1% profit).
    For shorts: profit when exit < entry.
    """
    if entry_price <= 0:
        return 0.0
    gross_pnl = (entry_price - exit_price) / entry_price
    cost = cost_bps / 10000
    return gross_pnl - cost


def compute_profit_factor(pnls):
    """Compute profit factor from list of PnL values.

    PF = sum(profits) / abs(sum(losses))
    """
    profits = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses == 0:
        return float("inf") if profits > 0 else 0.0
    return profits / losses


def compute_daily_pf(trades):
    """Compute average daily PF (the S9 success metric).

    Groups trades by exit date, computes PF per day, returns mean.
    """
    by_day = defaultdict(list)
    for t in trades:
        if t.get("exit_ts"):
            day = t["exit_ts"].date()
            by_day[day].append(t)

    if not by_day:
        return 0.0

    daily_pfs = []
    for day, day_trades in sorted(by_day.items()):
        profits = sum(t["pnl"] for t in day_trades if t["pnl"] > 0)
        losses = abs(sum(t["pnl"] for t in day_trades if t["pnl"] < 0))
        if losses > 0:
            daily_pfs.append(profits / losses)
        elif profits > 0:
            daily_pfs.append(10.0)  # Cap at 10 for inf days

    return np.mean(daily_pfs) if daily_pfs else 0.0


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

def run_backtest(days=14, output_path=None):
    """Run the S9 1s execution backtest."""
    start_wall = time.time()

    if output_path is None:
        if os.path.isdir("/scripts"):
            output_path = "/scripts/s9_1s_execution_backtest_results.txt"
        else:
            output_path = os.path.join(
                os.path.dirname(__file__),
                "s9_1s_execution_backtest_results.txt",
            )

    conn = get_db_connection()

    # Date range: signals from (days+2) ago to 2 days ago
    # (2-day buffer ensures 48h exit data exists)
    now = datetime.now(timezone.utc)
    signal_end = now - timedelta(days=2)
    signal_start = signal_end - timedelta(days=days)
    data_start = signal_start - timedelta(hours=25)  # Extra for lookback
    data_end = now

    logger.info("=" * 70)
    logger.info("S9 1-Second Execution Backtest")
    logger.info("=" * 70)
    logger.info("Signal window: %s to %s (%d days)",
                signal_start.strftime("%Y-%m-%d"), signal_end.strftime("%Y-%m-%d"), days)
    logger.info("Data window:   %s to %s",
                data_start.strftime("%Y-%m-%d %H:%M"), data_end.strftime("%Y-%m-%d %H:%M"))

    # Phase 1: Load historical 1h candles
    candle_data = load_1h_candles(conn, data_start, data_end)

    if not candle_data:
        logger.error("No 1h candle data found. Aborting.")
        conn.close()
        return

    # Phase 2: Re-derive S9 signals for each hour
    logger.info("Deriving S9 signals for each hour ...")
    all_signals = []
    hours_checked = 0
    hours_with_signals = 0

    current_hour = signal_start.replace(minute=0, second=0, microsecond=0)
    while current_hour <= signal_end:
        signals = derive_s9_signals(candle_data, current_hour)
        if signals:
            all_signals.extend(signals)
            hours_with_signals += 1
        hours_checked += 1
        current_hour += timedelta(hours=1)

    logger.info(
        "Derived %d signals from %d/%d hours (%.0f%% had breadth > %.1f)",
        len(all_signals), hours_with_signals, hours_checked,
        hours_with_signals / max(hours_checked, 1) * 100,
        BREADTH_THRESHOLD,
    )

    if not all_signals:
        logger.error("No S9 signals derived. Check data availability.")
        conn.close()
        return

    # Phase 3: Look up exit prices (48h later) from OHLCV
    logger.info("Looking up 48h exit prices ...")
    valid_signals = []
    for sig in all_signals:
        exit_close, exit_ts = get_close_at_offset(
            candle_data, sig["symbol"], sig["eval_hour"], HOLD_BARS,
        )
        if exit_close is not None:
            sig["exit_price"] = exit_close
            sig["exit_ts"] = exit_ts
            valid_signals.append(sig)

    logger.info(
        "%d/%d signals have valid 48h exit data",
        len(valid_signals), len(all_signals),
    )

    if not valid_signals:
        logger.error("No signals with exit data. Aborting.")
        conn.close()
        return

    # Phase 4: Fetch 300 x 1s klines for each signal
    kline_data = fetch_1s_for_signals(valid_signals)
    logger.info(
        "Fetched 1s klines for %d/%d signals (%.0f%% coverage)",
        len(kline_data), len(valid_signals),
        len(kline_data) / max(len(valid_signals), 1) * 100,
    )

    # Phase 5: Simulate entries and compute PnL
    logger.info("Simulating entry strategies ...")
    strategy_names = [
        "naive", "theoretical_best", "adaptive_limit",
        "wait_for_peak", "vwap_60s", "tape_aware",
    ]
    trades_by_strategy = {name: [] for name in strategy_names}

    for sig in valid_signals:
        key = (sig["symbol"], sig["eval_hour"])
        klines = kline_data.get(key, [])

        entries = simulate_entries(sig["close"], klines)

        for strategy_name in strategy_names:
            entry_price = entries.get(strategy_name, sig["close"])
            pnl = compute_trade_pnl(entry_price, sig["exit_price"])

            trades_by_strategy[strategy_name].append({
                "symbol": sig["symbol"],
                "eval_hour": sig["eval_hour"],
                "exit_ts": sig["exit_ts"],
                "entry_price": entry_price,
                "exit_price": sig["exit_price"],
                "pnl": pnl,
                "ret_4h": sig["ret_4h"],
                "rank": sig["rank"],
                "has_1s_data": len(klines) > 0,
            })

    conn.close()

    # Phase 6: Compute results
    elapsed = time.time() - start_wall
    results = _format_results(
        trades_by_strategy, strategy_names,
        valid_signals, kline_data,
        days, elapsed,
    )

    # Write results
    with open(output_path, "w") as f:
        f.write(results)

    logger.info("Results written to %s", output_path)
    print(results)


def _format_results(trades_by_strategy, strategy_names, valid_signals,
                    kline_data, days, elapsed_s):
    """Format backtest results as human-readable text."""
    lines = []
    lines.append("=" * 70)
    lines.append("S9 1-SECOND EXECUTION BACKTEST RESULTS")
    lines.append("=" * 70)
    lines.append(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"Signal period: {days} days")
    lines.append(f"Total signals: {len(valid_signals)}")
    lines.append(f"Signals with 1s data: {sum(1 for s in valid_signals if (s['symbol'], s['eval_hour']) in kline_data)}")
    lines.append(f"Runtime: {elapsed_s:.0f}s")
    lines.append("")

    # Strategy comparison table
    lines.append("-" * 70)
    lines.append(f"{'Strategy':<20} {'Agg PF':>8} {'Avg Daily PF':>13} {'Win Rate':>9} "
                 f"{'Mean PnL':>10} {'Edge vs Naive':>14}")
    lines.append("-" * 70)

    naive_mean_pnl = None
    for name in strategy_names:
        trades = trades_by_strategy[name]
        pnls = [t["pnl"] for t in trades]

        agg_pf = compute_profit_factor(pnls)
        avg_daily_pf = compute_daily_pf(trades)
        win_rate = sum(1 for p in pnls if p > 0) / max(len(pnls), 1) * 100
        mean_pnl = np.mean(pnls) * 100 if pnls else 0  # as percentage

        if name == "naive":
            naive_mean_pnl = mean_pnl
            edge_str = "---"
        else:
            edge_bps = (mean_pnl - naive_mean_pnl) * 100 if naive_mean_pnl is not None else 0
            edge_str = f"{edge_bps:+.1f} bps"

        pf_str = f"{agg_pf:.3f}" if agg_pf < 100 else "inf"
        daily_pf_str = f"{avg_daily_pf:.3f}" if avg_daily_pf < 100 else "inf"

        lines.append(
            f"{name:<20} {pf_str:>8} {daily_pf_str:>13} {win_rate:>8.1f}% "
            f"{mean_pnl:>9.3f}% {edge_str:>14}"
        )

    lines.append("-" * 70)
    lines.append("")

    # Per-strategy detail
    for name in strategy_names:
        if name == "naive":
            continue
        trades = trades_by_strategy[name]
        naive_trades = trades_by_strategy["naive"]

        better = sum(1 for t, n in zip(trades, naive_trades) if t["pnl"] > n["pnl"])
        worse = sum(1 for t, n in zip(trades, naive_trades) if t["pnl"] < n["pnl"])
        same = len(trades) - better - worse

        lines.append(f"{name}: {better} trades improved, {worse} worsened, {same} same vs naive")

    lines.append("")

    # 1s tape statistics
    n_with_data = sum(1 for s in valid_signals
                      if (s["symbol"], s["eval_hour"]) in kline_data)
    if n_with_data > 0:
        lines.append("-" * 70)
        lines.append("1-SECOND TAPE STATISTICS")
        lines.append("-" * 70)

        # Collect max_drift_bps for all signals with 1s data
        max_drifts = []
        for sig in valid_signals:
            key = (sig["symbol"], sig["eval_hour"])
            klines = kline_data.get(key, [])
            if klines:
                highs = [k.high for k in klines]
                max_high = max(highs)
                drift_bps = (max_high - sig["close"]) / sig["close"] * 10000
                max_drifts.append(drift_bps)

        if max_drifts:
            drifts = np.array(max_drifts)
            lines.append(f"Max upward drift (300s window):")
            lines.append(f"  Mean:   {np.mean(drifts):+.1f} bps")
            lines.append(f"  Median: {np.median(drifts):+.1f} bps")
            lines.append(f"  P25:    {np.percentile(drifts, 25):+.1f} bps")
            lines.append(f"  P75:    {np.percentile(drifts, 75):+.1f} bps")
            lines.append(f"  P90:    {np.percentile(drifts, 90):+.1f} bps")
            lines.append(f"  Pct > 0 bps: {np.mean(drifts > 0)*100:.0f}%")
            lines.append(f"  Pct > 5 bps: {np.mean(drifts > 5)*100:.0f}%")
            lines.append(f"  Pct > 10 bps: {np.mean(drifts > 10)*100:.0f}%")

    lines.append("")
    lines.append("=" * 70)
    lines.append("INTERPRETATION")
    lines.append("=" * 70)
    lines.append("- 'Edge vs Naive' = improvement in mean PnL per trade (in bps)")
    lines.append("- 'theoretical_best' is the ceiling (max high in 300s window)")
    lines.append("- 'tape_aware' uses the same analyze_tape() logic as live system")
    lines.append("- Positive edge confirms 1s execution improves on naive close entry")
    lines.append("- Cost model: %d bps roundtrip (matching S9 validation)" % COST_BPS)
    lines.append("=" * 70)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )

    parser = argparse.ArgumentParser(description="S9 1s Execution Backtest")
    parser.add_argument("--days", type=int, default=14,
                        help="Number of days to backtest (default: 14)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path (default: auto-detect)")
    args = parser.parse_args()

    run_backtest(days=args.days, output_path=args.output)
