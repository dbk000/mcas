#!/usr/bin/env python3
"""
D2 "Fade the Casino" — Honest Walk-Forward Backtest
=====================================================
No look-ahead. HWM tracked bar-by-bar. Two entry mechanisms:
  1. HWM Trailing Drop: enter when price drops X% from live HWM
  2. No-New-High Timer: enter after N hours with no new high

Parameter sweep across entry thresholds × stop losses.
Binance perps cross-reference. Funding rate sensitivity.
False peak rate tracking.

Output: scripts/output/d2_honest_{ts}.json + .txt

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d2_honest_backtest.py
"""

import bisect
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import product

import numpy as np
import psycopg
import requests
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

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
OUTPUT_JSON = os.path.join(OUTPUT_DIR, f"d2_honest_{TIMESTAMP}.json")
OUTPUT_TXT = os.path.join(OUTPUT_DIR, f"d2_honest_{TIMESTAMP}.txt")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3

# HWM trailing drop thresholds
ENTRY_THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30]

# No-new-high timer (hours)
NO_NEW_HIGH_HOURS = [2, 4, 6, 8]

# Stop losses (above HWM at entry time)
STOP_LOSSES = [0.10, 0.15, 0.20]

# Time stop: 72h from ENTRY (not cluster start)
TIME_STOP_HOURS = 72

# Max watching window: cancel signal if no entry within 48h of cluster start
MAX_WATCH_HOURS = 48

POSITION_SIZE_USD = 1000

# Trading costs (excluding funding)
TAKER_FEE_PCT = 0.0004  # 0.04% per side
SLIPPAGE_PCT = 0.0010    # 0.10% per side (microcap perps)
ROUND_TRIP_FRICTION = 2 * (TAKER_FEE_PCT + SLIPPAGE_PCT)  # 0.28%

# Funding rate assumptions (per 8h epoch, negative = you pay as short)
FUNDING_SCENARIOS = {
    "optimistic": -0.001,    # -0.1% per epoch
    "base_case": -0.003,     # -0.3% per epoch
    "pessimistic": -0.005,   # -0.5% per epoch
}


# ---------------------------------------------------------------------------
# Cluster detection (same as d2_viability_analysis.py)
# ---------------------------------------------------------------------------
def load_pump_signals(conn):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, source_post_id, source, channel, symbol, exchange,
                   signal_type, price, pct_change, vol_24h, last_vol,
                   vol_change_pct, oi, oi_change_pct, funding_rate,
                   bid_vol, ask_vol, spread_pct, post_ts
            FROM social_bot_signals
            ORDER BY post_ts
        """)
        return cur.fetchall()


def detect_pump_clusters(signals):
    pumps = [s for s in signals if s.get("signal_type") == "pump"]
    by_symbol = defaultdict(list)
    for s in pumps:
        by_symbol[s["symbol"]].append(s)

    clusters = []
    for symbol, sigs in by_symbol.items():
        sigs.sort(key=lambda x: x["post_ts"])
        current = [sigs[0]]
        for s in sigs[1:]:
            gap_h = (s["post_ts"] - current[-1]["post_ts"]).total_seconds() / 3600
            if gap_h <= CLUSTER_WINDOW_HOURS:
                current.append(s)
            else:
                if len(current) >= MIN_ALERTS_PER_CLUSTER:
                    clusters.append(_build_cluster(symbol, current))
                current = [s]
        if len(current) >= MIN_ALERTS_PER_CLUSTER:
            clusters.append(_build_cluster(symbol, current))

    clusters.sort(key=lambda c: c["cluster_start_ts"])
    return clusters


def _build_cluster(symbol, signals):
    exchanges = set(s.get("exchange") for s in signals if s.get("exchange"))
    pct_changes = [float(s["pct_change"]) for s in signals if s.get("pct_change") is not None]
    funding_rates = [float(s["funding_rate"]) for s in signals if s.get("funding_rate") is not None]

    return {
        "symbol": symbol,
        "cluster_start_ts": signals[0]["post_ts"],
        "cluster_end_ts": signals[-1]["post_ts"],
        "alert_count": len(signals),
        "exchanges_seen": sorted(exchanges),
        "exchange_count": len(exchanges),
        "max_pct_change": max(pct_changes) if pct_changes else None,
        "funding_rates_reported": funding_rates,
    }


# ---------------------------------------------------------------------------
# OHLCV loading
# ---------------------------------------------------------------------------
def load_ohlcv_for_clusters(conn, clusters):
    """Load 1h OHLCV for all cluster symbols. Returns dict: symbol -> [(ts, o, h, l, c)]."""
    logger.info("Loading 1h OHLCV for %d clusters...", len(clusters))
    symbols = sorted(set(c["symbol"] for c in clusters))
    earliest = min(c["cluster_start_ts"] for c in clusters) - timedelta(hours=2)
    latest = max(c["cluster_start_ts"] for c in clusters) + timedelta(hours=130)  # 48h watch + 72h hold + margin

    ohlcv = {}
    with conn.cursor() as cur:
        for sym in symbols:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close
                FROM ohlcv
                WHERE symbol = %s AND timeframe = '1h'
                  AND bar_start_ts >= %s AND bar_start_ts <= %s
                ORDER BY bar_start_ts
            """, (sym, earliest, latest))
            rows = cur.fetchall()
            if rows:
                ohlcv[sym] = rows

    logger.info("  OHLCV loaded for %d/%d symbols", len(ohlcv), len(symbols))
    return ohlcv


def get_pre_pump_price(bars, bar_times, cluster_start_ts):
    """Get close of bar immediately before cluster start."""
    pre_idx = bisect.bisect_left(bar_times, cluster_start_ts) - 1
    if pre_idx < 0:
        return None
    return float(bars[pre_idx][4])


# ---------------------------------------------------------------------------
# Binance perpetual futures list
# ---------------------------------------------------------------------------
def get_binance_perp_symbols():
    """Fetch current Binance USDT-M perpetual futures symbols."""
    logger.info("Fetching Binance USDT-M perpetual futures list...")
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10)
        resp.raise_for_status()
        info = resp.json()
        perps = {s["symbol"] for s in info["symbols"]
                 if s.get("contractType") == "PERPETUAL" and s["symbol"].endswith("USDT")}
        logger.info("  %d Binance USDT-M perpetual symbols", len(perps))
        return perps
    except Exception as e:
        logger.warning("  Failed to fetch Binance perps: %s. Using empty set.", e)
        return set()


# ---------------------------------------------------------------------------
# State machine: HWM Trailing Drop entry
# ---------------------------------------------------------------------------
def simulate_hwm_trailing(cluster, ohlcv_bars, bar_times, entry_threshold, stop_pct):
    """
    Walk-forward simulation with HWM trailing drop entry.

    Entry: when price drops entry_threshold from live HWM
    Stop: stop_pct above the HWM at entry time
    TP: pre-pump price
    Time stop: 72h from entry
    Max watch: 48h from cluster start
    """
    pre_pump_price = get_pre_pump_price(ohlcv_bars, bar_times, cluster["cluster_start_ts"])
    if pre_pump_price is None:
        return None

    cluster_start = cluster["cluster_start_ts"]
    max_watch_ts = cluster_start + timedelta(hours=MAX_WATCH_HOURS)

    # Find first bar at or after cluster start
    start_idx = bisect.bisect_left(bar_times, cluster_start)
    if start_idx >= len(ohlcv_bars):
        return None

    # State machine
    live_hwm = 0.0
    hwm_ts = None
    entry_price = None
    entry_ts = None
    entry_hwm = None
    stop_price = None
    tp_price = pre_pump_price
    state = "WATCHING"

    for i in range(start_idx, len(ohlcv_bars)):
        bar_ts = bar_times[i]
        bar_open = float(ohlcv_bars[i][1])
        bar_high = float(ohlcv_bars[i][2])
        bar_low = float(ohlcv_bars[i][3])
        bar_close = float(ohlcv_bars[i][4])

        if state == "WATCHING":
            # Update HWM
            if bar_high > live_hwm:
                live_hwm = bar_high
                hwm_ts = bar_ts

            # Check if we've exceeded max watch window
            if bar_ts >= max_watch_ts:
                return None  # Signal cancelled

            # Check for trailing entry: close has dropped entry_threshold from HWM
            if live_hwm > 0:
                drop_from_hwm = (live_hwm - bar_close) / live_hwm
                if drop_from_hwm >= entry_threshold:
                    entry_price = bar_close
                    entry_ts = bar_ts
                    entry_hwm = live_hwm
                    stop_price = live_hwm * (1 + stop_pct)

                    # Entry must be above TP (otherwise already below pre-pump)
                    if entry_price <= tp_price:
                        return None

                    state = "IN_TRADE"
                    continue

        elif state == "IN_TRADE":
            time_stop_ts = entry_ts + timedelta(hours=TIME_STOP_HOURS)

            # Check stop loss (bar high >= stop)
            if bar_high >= stop_price:
                return _build_trade(
                    cluster, "STOP_LOSS", stop_price, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                    ohlcv_bars, bar_times, bisect.bisect_left(bar_times, entry_ts),
                )

            # Check TP (bar low <= tp)
            if bar_low <= tp_price:
                return _build_trade(
                    cluster, "TP_HIT", tp_price, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                    ohlcv_bars, bar_times, bisect.bisect_left(bar_times, entry_ts),
                )

            # Check time stop
            if bar_ts >= time_stop_ts:
                return _build_trade(
                    cluster, "TIME_STOP", bar_close, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                    ohlcv_bars, bar_times, bisect.bisect_left(bar_times, entry_ts),
                )

    # Ran out of bars
    if state == "IN_TRADE":
        last_i = len(ohlcv_bars) - 1
        return _build_trade(
            cluster, "TIME_STOP", float(ohlcv_bars[last_i][4]),
            entry_price, entry_ts, bar_times[last_i],
            entry_hwm, tp_price, stop_price, pre_pump_price,
            last_i - bisect.bisect_left(bar_times, entry_ts),
            ohlcv_bars, bar_times, bisect.bisect_left(bar_times, entry_ts),
        )

    return None


# ---------------------------------------------------------------------------
# State machine: No-New-High Timer entry
# ---------------------------------------------------------------------------
def simulate_nnh_timer(cluster, ohlcv_bars, bar_times, nnh_hours, stop_pct):
    """
    Walk-forward simulation with No-New-High timer entry.

    Entry: after nnh_hours with no new high since the HWM was set
    Stop: stop_pct above the HWM at entry time
    TP: pre-pump price
    Time stop: 72h from entry
    Max watch: 48h from cluster start
    """
    pre_pump_price = get_pre_pump_price(ohlcv_bars, bar_times, cluster["cluster_start_ts"])
    if pre_pump_price is None:
        return None

    cluster_start = cluster["cluster_start_ts"]
    max_watch_ts = cluster_start + timedelta(hours=MAX_WATCH_HOURS)

    start_idx = bisect.bisect_left(bar_times, cluster_start)
    if start_idx >= len(ohlcv_bars):
        return None

    live_hwm = 0.0
    hwm_ts = None
    entry_price = None
    entry_ts = None
    entry_hwm = None
    stop_price = None
    tp_price = pre_pump_price
    state = "WATCHING"

    for i in range(start_idx, len(ohlcv_bars)):
        bar_ts = bar_times[i]
        bar_high = float(ohlcv_bars[i][2])
        bar_low = float(ohlcv_bars[i][3])
        bar_close = float(ohlcv_bars[i][4])

        if state == "WATCHING":
            if bar_high > live_hwm:
                live_hwm = bar_high
                hwm_ts = bar_ts

            if bar_ts >= max_watch_ts:
                return None

            # Check no-new-high timer
            if hwm_ts is not None and live_hwm > 0:
                hours_since_hwm = (bar_ts - hwm_ts).total_seconds() / 3600
                if hours_since_hwm >= nnh_hours:
                    entry_price = bar_close
                    entry_ts = bar_ts
                    entry_hwm = live_hwm
                    stop_price = live_hwm * (1 + stop_pct)

                    if entry_price <= tp_price:
                        return None

                    state = "IN_TRADE"
                    continue

        elif state == "IN_TRADE":
            time_stop_ts = entry_ts + timedelta(hours=TIME_STOP_HOURS)

            if bar_high >= stop_price:
                return _build_trade(
                    cluster, "STOP_LOSS", stop_price, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                    ohlcv_bars, bar_times, bisect.bisect_left(bar_times, entry_ts),
                )

            if bar_low <= tp_price:
                return _build_trade(
                    cluster, "TP_HIT", tp_price, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                    ohlcv_bars, bar_times, bisect.bisect_left(bar_times, entry_ts),
                )

            if bar_ts >= time_stop_ts:
                return _build_trade(
                    cluster, "TIME_STOP", bar_close, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                    ohlcv_bars, bar_times, bisect.bisect_left(bar_times, entry_ts),
                )

    if state == "IN_TRADE":
        last_i = len(ohlcv_bars) - 1
        return _build_trade(
            cluster, "TIME_STOP", float(ohlcv_bars[last_i][4]),
            entry_price, entry_ts, bar_times[last_i],
            entry_hwm, tp_price, stop_price, pre_pump_price,
            last_i - bisect.bisect_left(bar_times, entry_ts),
            ohlcv_bars, bar_times, bisect.bisect_left(bar_times, entry_ts),
        )

    return None


# ---------------------------------------------------------------------------
# Trade builder + false peak detection
# ---------------------------------------------------------------------------
def _build_trade(cluster, exit_reason, exit_price, entry_price, entry_ts,
                 exit_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                 bars_held, ohlcv_bars, bar_times, entry_bar_idx):
    """Build trade result dict with false peak detection."""
    risk = stop_price - entry_price
    if risk <= 0:
        risk = 0.001  # avoid div/0

    # PnL (short: profit = entry - exit)
    pnl_pct = (entry_price - exit_price) / entry_price
    r_multiple = (entry_price - exit_price) / risk

    # Hours held
    hours_held = (exit_ts - entry_ts).total_seconds() / 3600 if exit_ts and entry_ts else 0

    # Funding epochs during hold (every 8h)
    n_funding_epochs = int(hours_held / 8)

    # False peak detection: did price make a new high above entry_hwm after entry?
    false_peak = False
    for j in range(entry_bar_idx + 1, len(ohlcv_bars)):
        if float(ohlcv_bars[j][2]) > entry_hwm:
            false_peak = True
            break
        if bar_times[j] >= exit_ts:
            break

    return {
        "symbol": cluster["symbol"],
        "cluster_start": cluster["cluster_start_ts"],
        "alert_count": cluster["alert_count"],
        "max_pct_change": cluster.get("max_pct_change"),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "entry_hwm": entry_hwm,
        "stop_price": stop_price,
        "tp_price": tp_price,
        "pre_pump_price": pre_pump_price,
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "hours_held": round(hours_held, 1),
        "n_funding_epochs": n_funding_epochs,
        "pnl_pct_before_costs": round(pnl_pct * 100, 4),
        "r_multiple": round(r_multiple, 4),
        "risk_per_unit": risk,
        "false_peak": false_peak,
        "funding_rates_reported": cluster.get("funding_rates_reported", []),
    }


# ---------------------------------------------------------------------------
# Statistics computation
# ---------------------------------------------------------------------------
def compute_stats(trades, label, funding_scenario=None):
    """Compute trade statistics with optional funding cost."""
    if not trades:
        return {"label": label, "trades_taken": 0, "note": "No valid trades"}

    stats_list = []
    for t in trades:
        pnl_pct = t["pnl_pct_before_costs"] / 100

        # Trading friction
        friction = ROUND_TRIP_FRICTION

        # Funding cost
        funding_cost = 0
        if funding_scenario is not None:
            rate = FUNDING_SCENARIOS[funding_scenario]
            # Use actual reported rate if available, else assumption
            if t["funding_rates_reported"]:
                actual_rate = np.mean(t["funding_rates_reported"]) / 100  # convert from % to decimal
                funding_cost = abs(actual_rate) * t["n_funding_epochs"]
            else:
                funding_cost = abs(rate) * t["n_funding_epochs"]

        pnl_after = pnl_pct - friction - funding_cost
        pnl_usd = POSITION_SIZE_USD * pnl_after

        stats_list.append({
            **t,
            "pnl_pct_after_costs": round(pnl_after * 100, 4),
            "pnl_usd": round(pnl_usd, 2),
            "funding_cost_pct": round(funding_cost * 100, 4),
        })

    winners = [s for s in stats_list if s["pnl_usd"] > 0]
    losers = [s for s in stats_list if s["pnl_usd"] <= 0]

    total_wins = sum(s["pnl_usd"] for s in winners)
    total_losses = abs(sum(s["pnl_usd"] for s in losers))

    tp_hits = [s for s in stats_list if s["exit_reason"] == "TP_HIT"]
    sl_hits = [s for s in stats_list if s["exit_reason"] == "STOP_LOSS"]
    ts_hits = [s for s in stats_list if s["exit_reason"] == "TIME_STOP"]

    win_r = [s["r_multiple"] for s in winners] if winners else [0]
    loss_r = [s["r_multiple"] for s in losers] if losers else [0]

    # Max consecutive losses
    max_consec = 0
    streak = 0
    for s in sorted(stats_list, key=lambda x: x["entry_ts"]):
        if s["pnl_usd"] <= 0:
            streak += 1
            max_consec = max(max_consec, streak)
        else:
            streak = 0

    # False peak rate
    false_peaks = sum(1 for s in stats_list if s["false_peak"])

    n = len(stats_list)
    pf_before = (
        sum(s["pnl_pct_before_costs"] for s in stats_list if s["pnl_pct_before_costs"] > 0) /
        abs(sum(s["pnl_pct_before_costs"] for s in stats_list if s["pnl_pct_before_costs"] <= 0))
        if any(s["pnl_pct_before_costs"] <= 0 for s in stats_list) else float("inf")
    )

    return {
        "label": label,
        "trades_taken": n,
        "tp_hit": len(tp_hits),
        "tp_hit_pct": round(len(tp_hits) / n * 100, 1),
        "stop_loss": len(sl_hits),
        "stop_loss_pct": round(len(sl_hits) / n * 100, 1),
        "time_stop": len(ts_hits),
        "time_stop_pct": round(len(ts_hits) / n * 100, 1),
        "win_rate": round(len(winners) / n * 100, 1),
        "mean_r_winners": round(float(np.mean(win_r)), 4),
        "mean_r_losers": round(float(np.mean(loss_r)), 4),
        "profit_factor_before_costs": round(pf_before, 4),
        "profit_factor_after_costs": round(total_wins / total_losses, 4) if total_losses > 0 else float("inf"),
        "total_pnl_usd": round(sum(s["pnl_usd"] for s in stats_list), 2),
        "mean_pnl_usd": round(float(np.mean([s["pnl_usd"] for s in stats_list])), 2),
        "max_consecutive_losses": max_consec,
        "mean_hours_held": round(float(np.mean([s["hours_held"] for s in stats_list])), 1),
        "mean_funding_cost_pct": round(float(np.mean([s["funding_cost_pct"] for s in stats_list])), 4),
        "false_peak_count": false_peaks,
        "false_peak_rate": round(false_peaks / n * 100, 1),
        "funding_scenario": funding_scenario,
    }


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------
def run_sweep(clusters, ohlcv_by_symbol, binance_perps):
    """Run full parameter sweep across both entry mechanisms."""
    logger.info("Running parameter sweep...")

    # Identify valid clusters and Binance-only subset
    valid_clusters = []
    for c in clusters:
        sym = c["symbol"]
        if sym in ohlcv_by_symbol:
            bars = ohlcv_by_symbol[sym]
            bar_times = [b[0] for b in bars]
            pre_price = get_pre_pump_price(bars, bar_times, c["cluster_start_ts"])
            if pre_price is not None:
                c["_bars"] = bars
                c["_bar_times"] = bar_times
                c["_pre_pump_price"] = pre_price
                valid_clusters.append(c)

    binance_clusters = [c for c in valid_clusters if c["symbol"] in binance_perps]
    mania_clusters = [c for c in valid_clusters
                      if (c.get("max_pct_change") or 0) > 10 and c["alert_count"] >= 4]
    mania_binance = [c for c in mania_clusters if c["symbol"] in binance_perps]

    logger.info("  Valid clusters: %d (Binance: %d, Mania: %d, Mania+Binance: %d)",
                len(valid_clusters), len(binance_clusters),
                len(mania_clusters), len(mania_binance))

    results = {
        "cluster_counts": {
            "total_clusters": len(clusters),
            "valid_ohlcv": len(valid_clusters),
            "binance_shortable": len(binance_clusters),
            "mania_filtered": len(mania_clusters),
            "mania_binance": len(mania_binance),
        },
        "hwm_trailing": {},
        "nnh_timer": {},
    }

    # HWM Trailing Drop sweep
    total_configs = len(ENTRY_THRESHOLDS) * len(STOP_LOSSES)
    config_i = 0
    for entry_thresh, stop_pct in product(ENTRY_THRESHOLDS, STOP_LOSSES):
        config_i += 1
        config_key = f"entry{int(entry_thresh*100)}_stop{int(stop_pct*100)}"
        logger.info("  [%d/%d] HWM trailing: entry=%d%%, stop=%d%%",
                     config_i, total_configs, int(entry_thresh*100), int(stop_pct*100))

        # Run on all symbol sets
        for set_name, cluster_set in [
            ("all", valid_clusters),
            ("binance_only", binance_clusters),
            ("mania_all", mania_clusters),
            ("mania_binance", mania_binance),
        ]:
            trades = []
            for c in cluster_set:
                trade = simulate_hwm_trailing(
                    c, c["_bars"], c["_bar_times"], entry_thresh, stop_pct
                )
                if trade is not None:
                    trades.append(trade)

            # Compute stats at base_case funding
            stats = compute_stats(trades, f"hwm_{config_key}_{set_name}", "base_case")

            if config_key not in results["hwm_trailing"]:
                results["hwm_trailing"][config_key] = {}
            results["hwm_trailing"][config_key][set_name] = stats

    # No-New-High Timer sweep
    total_configs = len(NO_NEW_HIGH_HOURS) * len(STOP_LOSSES)
    config_i = 0
    for nnh_h, stop_pct in product(NO_NEW_HIGH_HOURS, STOP_LOSSES):
        config_i += 1
        config_key = f"nnh{nnh_h}h_stop{int(stop_pct*100)}"
        logger.info("  [%d/%d] NNH timer: hours=%d, stop=%d%%",
                     config_i, total_configs, nnh_h, int(stop_pct*100))

        for set_name, cluster_set in [
            ("all", valid_clusters),
            ("binance_only", binance_clusters),
            ("mania_all", mania_clusters),
            ("mania_binance", mania_binance),
        ]:
            trades = []
            for c in cluster_set:
                trade = simulate_nnh_timer(
                    c, c["_bars"], c["_bar_times"], nnh_h, stop_pct
                )
                if trade is not None:
                    trades.append(trade)

            stats = compute_stats(trades, f"nnh_{config_key}_{set_name}", "base_case")

            if config_key not in results["nnh_timer"]:
                results["nnh_timer"][config_key] = {}
            results["nnh_timer"][config_key][set_name] = stats

    # Find best configs
    results["best_configs"] = find_best_configs(results)

    # Funding sensitivity on best config
    results["funding_sensitivity"] = run_funding_sensitivity(
        results["best_configs"], valid_clusters, binance_clusters, ohlcv_by_symbol
    )

    return results


def find_best_configs(results):
    """Find best config for each mechanism × symbol set."""
    best = {}

    for mechanism in ["hwm_trailing", "nnh_timer"]:
        for set_name in ["all", "binance_only"]:
            best_pf = 0
            best_key = None
            for config_key, sets in results[mechanism].items():
                s = sets.get(set_name, {})
                pf = s.get("profit_factor_after_costs", 0)
                n = s.get("trades_taken", 0)
                if n >= 10 and pf > best_pf:
                    best_pf = pf
                    best_key = config_key
            if best_key:
                best[f"{mechanism}_{set_name}"] = {
                    "config_key": best_key,
                    "stats": results[mechanism][best_key][set_name],
                }

    return best


def run_funding_sensitivity(best_configs, valid_clusters, binance_clusters, ohlcv_by_symbol):
    """Run funding sensitivity on best configs."""
    logger.info("Running funding sensitivity analysis...")
    sensitivity = {}

    for config_label, config_info in best_configs.items():
        config_key = config_info["config_key"]
        is_binance = "binance" in config_label
        cluster_set = binance_clusters if is_binance else valid_clusters

        # Parse config params
        if "hwm" in config_label:
            parts = config_key.split("_")
            entry_thresh = int(parts[0].replace("entry", "")) / 100
            stop_pct = int(parts[1].replace("stop", "")) / 100

            trades = []
            for c in cluster_set:
                trade = simulate_hwm_trailing(c, c["_bars"], c["_bar_times"], entry_thresh, stop_pct)
                if trade:
                    trades.append(trade)
        elif "nnh" in config_label:
            parts = config_key.split("_")
            nnh_h = int(parts[0].replace("nnh", "").replace("h", ""))
            stop_pct = int(parts[1].replace("stop", "")) / 100

            trades = []
            for c in cluster_set:
                trade = simulate_nnh_timer(c, c["_bars"], c["_bar_times"], nnh_h, stop_pct)
                if trade:
                    trades.append(trade)
        else:
            continue

        if not trades:
            continue

        funding_results = {}
        for scenario in FUNDING_SCENARIOS:
            stats = compute_stats(trades, f"{config_label}_{scenario}", scenario)
            funding_results[scenario] = {
                "pf_after_costs": stats["profit_factor_after_costs"],
                "total_pnl_usd": stats["total_pnl_usd"],
                "mean_funding_cost_pct": stats["mean_funding_cost_pct"],
                "win_rate": stats["win_rate"],
            }

        sensitivity[config_label] = {
            "config_key": config_key,
            "n_trades": len(trades),
            "scenarios": funding_results,
        }

    return sensitivity


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------
def write_text_report(results, binance_perps, clusters, output_path):
    lines = []
    w = lines.append

    w("=" * 90)
    w("D2 'FADE THE CASINO' — HONEST WALK-FORWARD BACKTEST")
    w("=" * 90)
    w("")
    w("Entry mechanisms: HWM Trailing Drop + No-New-High Timer")
    w("Stop: above HWM at entry time | TP: pre-pump price | Time stop: 72h from entry")
    w(f"Max watch window: {MAX_WATCH_HOURS}h | Position: ${POSITION_SIZE_USD}")
    w(f"Costs: {ROUND_TRIP_FRICTION*100:.2f}% friction + per-epoch funding")
    w("")

    cc = results["cluster_counts"]
    w(f"Clusters: {cc['total_clusters']} total, {cc['valid_ohlcv']} valid OHLCV, "
      f"{cc['binance_shortable']} Binance-shortable")
    w(f"Mania filter: {cc['mania_filtered']} (all), {cc['mania_binance']} (Binance)")

    # Binance perps cross-reference
    cluster_symbols = set(c["symbol"] for c in clusters)
    shortable = cluster_symbols & binance_perps
    not_shortable = cluster_symbols - binance_perps
    w(f"\nBinance USDT-M Perps: {len(binance_perps)} total listed")
    w(f"  Cluster symbols shortable: {len(shortable)}/{len(cluster_symbols)} ({len(shortable)/len(cluster_symbols)*100:.1f}%)")
    if shortable:
        w(f"  Shortable: {', '.join(sorted(shortable)[:20])}{'...' if len(shortable) > 20 else ''}")

    # HWM Trailing results
    w("")
    w("=" * 90)
    w("HWM TRAILING DROP — Parameter Sweep (base case funding)")
    w("=" * 90)

    for set_name, set_label in [("all", "ALL SYMBOLS"), ("binance_only", "BINANCE-ONLY")]:
        w(f"\n  {set_label}:")
        header = f"  {'Config':<20} | {'N':>4} | {'WR%':>5} | {'PF(pre)':>8} | {'PF(post)':>8} | {'SL%':>5} | {'FP%':>5} | {'PnL($)':>9}"
        w(header)
        w("  " + "-" * len(header.strip()))

        for config_key in sorted(results["hwm_trailing"]):
            s = results["hwm_trailing"][config_key].get(set_name, {})
            n = s.get("trades_taken", 0)
            if n == 0:
                w(f"  {config_key:<20} |    0 |")
                continue
            w(f"  {config_key:<20} | {n:>4} | {s['win_rate']:>5.1f} | "
              f"{s['profit_factor_before_costs']:>8.4f} | {s['profit_factor_after_costs']:>8.4f} | "
              f"{s['stop_loss_pct']:>5.1f} | {s['false_peak_rate']:>5.1f} | {s['total_pnl_usd']:>9.2f}")

    # NNH Timer results
    w("")
    w("=" * 90)
    w("NO-NEW-HIGH TIMER — Parameter Sweep (base case funding)")
    w("=" * 90)

    for set_name, set_label in [("all", "ALL SYMBOLS"), ("binance_only", "BINANCE-ONLY")]:
        w(f"\n  {set_label}:")
        header = f"  {'Config':<20} | {'N':>4} | {'WR%':>5} | {'PF(pre)':>8} | {'PF(post)':>8} | {'SL%':>5} | {'FP%':>5} | {'PnL($)':>9}"
        w(header)
        w("  " + "-" * len(header.strip()))

        for config_key in sorted(results["nnh_timer"]):
            s = results["nnh_timer"][config_key].get(set_name, {})
            n = s.get("trades_taken", 0)
            if n == 0:
                w(f"  {config_key:<20} |    0 |")
                continue
            w(f"  {config_key:<20} | {n:>4} | {s['win_rate']:>5.1f} | "
              f"{s['profit_factor_before_costs']:>8.4f} | {s['profit_factor_after_costs']:>8.4f} | "
              f"{s['stop_loss_pct']:>5.1f} | {s['false_peak_rate']:>5.1f} | {s['total_pnl_usd']:>9.2f}")

    # Best configs
    w("")
    w("=" * 90)
    w("BEST CONFIGURATIONS")
    w("=" * 90)

    for label, info in results.get("best_configs", {}).items():
        s = info["stats"]
        w(f"\n  {label}: {info['config_key']}")
        w(f"    Trades: {s['trades_taken']}, WR: {s['win_rate']:.1f}%, "
          f"PF(after): {s['profit_factor_after_costs']:.4f}")
        w(f"    TP: {s['tp_hit_pct']:.1f}%, SL: {s['stop_loss_pct']:.1f}%, "
          f"TS: {s['time_stop_pct']:.1f}%")
        w(f"    PnL: ${s['total_pnl_usd']:.2f}, MaxConsecLoss: {s['max_consecutive_losses']}")
        w(f"    False peak rate: {s['false_peak_rate']:.1f}%")
        w(f"    Mean hold: {s['mean_hours_held']:.1f}h, Mean funding cost: {s['mean_funding_cost_pct']:.4f}%")

    # Funding sensitivity
    w("")
    w("=" * 90)
    w("FUNDING SENSITIVITY")
    w("=" * 90)

    for label, info in results.get("funding_sensitivity", {}).items():
        w(f"\n  {label} ({info['config_key']}, N={info['n_trades']}):")
        for scenario, data in info.get("scenarios", {}).items():
            w(f"    {scenario:<15}: PF={data['pf_after_costs']:.4f}, "
              f"PnL=${data['total_pnl_usd']:.2f}, "
              f"Funding cost={data['mean_funding_cost_pct']:.4f}%")

    # Mania filter comparison
    w("")
    w("=" * 90)
    w("MANIA FILTER COMPARISON (best HWM config, base case funding)")
    w("=" * 90)

    best_hwm_all = results.get("best_configs", {}).get("hwm_trailing_all", {})
    if best_hwm_all:
        config_key = best_hwm_all["config_key"]
        hwm_data = results["hwm_trailing"].get(config_key, {})

        for set_pair in [("all", "mania_all"), ("binance_only", "mania_binance")]:
            base_set, mania_set = set_pair
            base = hwm_data.get(base_set, {})
            mania = hwm_data.get(mania_set, {})

            w(f"\n  {base_set.upper()} vs MANIA-FILTERED:")
            w(f"    Unfiltered: N={base.get('trades_taken', 0)}, "
              f"PF={base.get('profit_factor_after_costs', 0):.4f}")
            n_mania = mania.get('trades_taken', 0)
            if n_mania < 30:
                w(f"    Mania-filtered: N={n_mania}, "
                  f"PF={mania.get('profit_factor_after_costs', 0):.4f} "
                  f"(DIRECTIONAL ONLY — N<30)")
            else:
                w(f"    Mania-filtered: N={n_mania}, "
                  f"PF={mania.get('profit_factor_after_costs', 0):.4f}")

    # Verdict
    w("")
    w("=" * 90)
    w("VERDICT")
    w("=" * 90)

    best_binance_hwm = results.get("best_configs", {}).get("hwm_trailing_binance_only", {})
    best_binance_nnh = results.get("best_configs", {}).get("nnh_timer_binance_only", {})

    # Check pessimistic funding PF
    pessimistic_pf = 0
    best_mechanism = None
    for label, info in results.get("funding_sensitivity", {}).items():
        if "binance" in label:
            pf = info.get("scenarios", {}).get("pessimistic", {}).get("pf_after_costs", 0)
            if pf > pessimistic_pf:
                pessimistic_pf = pf
                best_mechanism = label

    if pessimistic_pf > 1.3:
        verdict = "VIABLE"
        verdict_detail = f"PF {pessimistic_pf:.2f} at pessimistic funding on Binance-only"
    elif pessimistic_pf > 1.0:
        verdict = "MARGINAL"
        verdict_detail = f"PF {pessimistic_pf:.2f} at pessimistic funding — edge exists but thin"
    else:
        verdict = "NOT VIABLE"
        verdict_detail = f"PF {pessimistic_pf:.2f} at pessimistic funding — no edge after costs"

    w(f"\n  D2 is {verdict}")
    w(f"  {verdict_detail}")
    if best_mechanism:
        w(f"  Best mechanism: {best_mechanism}")

    # False peak summary
    all_fp_rates = []
    for mech in ["hwm_trailing", "nnh_timer"]:
        for config_data in results[mech].values():
            for s in config_data.values():
                if s.get("trades_taken", 0) > 0:
                    all_fp_rates.append(s["false_peak_rate"])
    if all_fp_rates:
        w(f"\n  False peak rate range: {min(all_fp_rates):.1f}% — {max(all_fp_rates):.1f}%")

    w(f"\n  Output: {OUTPUT_JSON}")

    report = "\n".join(lines)
    with open(output_path, "w") as f:
        f.write(report)
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    logger.info("D2 Honest Walk-Forward Backtest starting...")
    logger.info("Output JSON: %s", OUTPUT_JSON)
    logger.info("Output TXT:  %s", OUTPUT_TXT)

    # Fetch Binance perps list
    binance_perps = get_binance_perp_symbols()

    with psycopg.connect(DATABASE_URL) as conn:
        # Load signals and detect clusters
        logger.info("Loading pump signals...")
        signals = load_pump_signals(conn)
        logger.info("  %d signals loaded (%d pump)",
                     len(signals), sum(1 for s in signals if s["signal_type"] == "pump"))

        clusters = detect_pump_clusters(signals)
        logger.info("  %d clusters detected", len(clusters))

        # Load OHLCV
        ohlcv_by_symbol = load_ohlcv_for_clusters(conn, clusters)

    # Run sweep
    results = run_sweep(clusters, ohlcv_by_symbol, binance_perps)

    elapsed = round(time.time() - t0, 1)
    logger.info("Analysis complete in %.1fs", elapsed)
    results["metadata"] = {
        "generated_at": TIMESTAMP,
        "elapsed_seconds": elapsed,
        "binance_perps_count": len(binance_perps),
        "cost_model": {
            "taker_fee_pct": TAKER_FEE_PCT * 100,
            "slippage_pct": SLIPPAGE_PCT * 100,
            "round_trip_friction_pct": ROUND_TRIP_FRICTION * 100,
            "funding_scenarios": {k: f"{v*100:.1f}% per 8h epoch" for k, v in FUNDING_SCENARIOS.items()},
        },
    }

    # Write JSON
    with open(OUTPUT_JSON, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("JSON: %s", OUTPUT_JSON)

    # Write text report
    report = write_text_report(results, binance_perps, clusters, OUTPUT_TXT)
    print(report)
    logger.info("TXT: %s", OUTPUT_TXT)


if __name__ == "__main__":
    main()
