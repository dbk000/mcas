#!/usr/bin/env python3
"""
D2 Garbage Collector — Next Steps (Post Stress Test)
=====================================================
Task 1: Mania extreme gate (max_initial_pump_pct filter)
Task 2: Liquidity filter (retroactive 24h volume sweep at signal time)
Task 3: Position sizing overlay (adverse excursion percentile)
Task 4: Funding rate backfill (Binance historical API)
Task 5: Expand N analysis (lookback window, capacity)

Produces summary report with "true N" and "true PF" after all gates.

Output: scripts/output/d2_next_steps_{ts}.json + .txt

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d2_next_steps.py
"""

import bisect
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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
OUTPUT_JSON = os.path.join(OUTPUT_DIR, f"d2_next_steps_{TIMESTAMP}.json")
OUTPUT_TXT = os.path.join(OUTPUT_DIR, f"d2_next_steps_{TIMESTAMP}.txt")

# ---------------------------------------------------------------------------
# Locked config from honest backtest
# ---------------------------------------------------------------------------
CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3
ENTRY_THRESHOLD = 0.25
STOP_PCT = 0.15
TIME_STOP_HOURS = 72
MAX_WATCH_HOURS = 48
POSITION_SIZE_USD = 1000
TAKER_FEE_PCT = 0.0004
BASE_SLIPPAGE_PCT = 0.0010
BASE_FUNDING_RATE = -0.003
ROUND_TRIP_FRICTION = 2 * (TAKER_FEE_PCT + BASE_SLIPPAGE_PCT)

# Task 1: Mania gate
MANIA_EXTREME_THRESHOLD = 40.0  # max_pct_change > 40% = mania_extreme

# Task 2: Liquidity thresholds (USD)
VOLUME_THRESHOLDS = [500_000, 1_000_000, 2_000_000, 5_000_000]

# Task 3: Position sizing
ADVERSE_EXCURSION_PERCENTILES = [75, 90, 95]


# ---------------------------------------------------------------------------
# Cluster detection (identical to stress test)
# ---------------------------------------------------------------------------
def load_pump_signals(conn):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, source_post_id, source, channel, symbol, exchange,
                   signal_type, price, pct_change, vol_24h, last_vol,
                   vol_change_pct, oi, oi_change_pct, funding_rate,
                   bid_vol, ask_vol, spread_pct, post_ts
            FROM social_bot_signals ORDER BY post_ts
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
    pct_changes = [float(s["pct_change"]) for s in signals if s.get("pct_change") is not None]
    funding_rates = [float(s["funding_rate"]) for s in signals if s.get("funding_rate") is not None]
    return {
        "symbol": symbol,
        "cluster_start_ts": signals[0]["post_ts"],
        "cluster_end_ts": signals[-1]["post_ts"],
        "alert_count": len(signals),
        "exchange_count": len(set(s.get("exchange") for s in signals if s.get("exchange"))),
        "max_pct_change": max(pct_changes) if pct_changes else None,
        "funding_rates_reported": funding_rates,
    }


def load_ohlcv(conn, clusters):
    symbols = sorted(set(c["symbol"] for c in clusters))
    earliest = min(c["cluster_start_ts"] for c in clusters) - timedelta(hours=26)
    latest = max(c["cluster_start_ts"] for c in clusters) + timedelta(hours=130)
    ohlcv = {}
    with conn.cursor() as cur:
        for sym in symbols:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close, volume
                FROM ohlcv WHERE symbol = %s AND timeframe = '1h'
                AND bar_start_ts >= %s AND bar_start_ts <= %s
                ORDER BY bar_start_ts
            """, (sym, earliest, latest))
            rows = cur.fetchall()
            if rows:
                ohlcv[sym] = rows
    return ohlcv


def get_binance_perps():
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10)
        resp.raise_for_status()
        return {s["symbol"] for s in resp.json()["symbols"]
                if s.get("contractType") == "PERPETUAL" and s["symbol"].endswith("USDT")}
    except Exception as e:
        logger.warning("Binance perps fetch failed: %s", e)
        return set()


# ---------------------------------------------------------------------------
# HWM trailing simulation — full per-trade detail
# ---------------------------------------------------------------------------
def simulate_hwm_trailing(cluster, bars, bar_times):
    pre_idx = bisect.bisect_left(bar_times, cluster["cluster_start_ts"]) - 1
    if pre_idx < 0:
        return None
    pre_pump_price = float(bars[pre_idx][4])
    cluster_start = cluster["cluster_start_ts"]
    max_watch_ts = cluster_start + timedelta(hours=MAX_WATCH_HOURS)
    start_idx = bisect.bisect_left(bar_times, cluster_start)
    if start_idx >= len(bars):
        return None

    live_hwm = 0.0
    hwm_ts = None
    state = "WATCHING"
    entry_price = entry_ts = entry_hwm = entry_bar_idx = stop_price = None
    tp_price = pre_pump_price

    for i in range(start_idx, len(bars)):
        bar_ts = bar_times[i]
        bar_high = float(bars[i][2])
        bar_low = float(bars[i][3])
        bar_close = float(bars[i][4])

        if state == "WATCHING":
            if bar_high > live_hwm:
                live_hwm = bar_high
                hwm_ts = bar_ts
            if bar_ts >= max_watch_ts:
                return None
            if live_hwm > 0:
                drop = (live_hwm - bar_close) / live_hwm
                if drop >= ENTRY_THRESHOLD:
                    entry_price = bar_close
                    entry_ts = bar_ts
                    entry_hwm = live_hwm
                    entry_bar_idx = i
                    stop_price = live_hwm * (1 + STOP_PCT)
                    if entry_price <= tp_price:
                        return None
                    state = "IN_TRADE"
                    continue

        elif state == "IN_TRADE":
            time_stop_ts = entry_ts + timedelta(hours=TIME_STOP_HOURS)
            if bar_high >= stop_price:
                return _make_trade(cluster, "STOP_LOSS", stop_price, entry_price,
                                   entry_ts, bar_ts, entry_hwm, tp_price, stop_price,
                                   pre_pump_price, i - entry_bar_idx, bars, bar_times, entry_bar_idx, i)
            if bar_low <= tp_price:
                return _make_trade(cluster, "TP_HIT", tp_price, entry_price,
                                   entry_ts, bar_ts, entry_hwm, tp_price, stop_price,
                                   pre_pump_price, i - entry_bar_idx, bars, bar_times, entry_bar_idx, i)
            if bar_ts >= time_stop_ts:
                return _make_trade(cluster, "TIME_STOP", bar_close, entry_price,
                                   entry_ts, bar_ts, entry_hwm, tp_price, stop_price,
                                   pre_pump_price, i - entry_bar_idx, bars, bar_times, entry_bar_idx, i)

    if state == "IN_TRADE":
        li = len(bars) - 1
        return _make_trade(cluster, "TIME_STOP", float(bars[li][4]),
                           entry_price, entry_ts, bar_times[li],
                           entry_hwm, tp_price, stop_price, pre_pump_price,
                           li - entry_bar_idx, bars, bar_times, entry_bar_idx, li)
    return None


def _make_trade(cluster, exit_reason, exit_price, entry_price, entry_ts,
                exit_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                bars_held, bars, bar_times, entry_bar_idx, exit_bar_idx):
    risk = stop_price - entry_price
    if risk <= 0:
        risk = 0.001
    pnl_pct = (entry_price - exit_price) / entry_price
    r_multiple = (entry_price - exit_price) / risk
    hours_held = (exit_ts - entry_ts).total_seconds() / 3600
    n_funding_epochs = int(hours_held / 8)

    false_peak = False
    max_adverse_pct = 0.0
    candles_exceeding_stop = 0
    for j in range(entry_bar_idx + 1, min(exit_bar_idx + 1, len(bars))):
        bh = float(bars[j][2])
        bo = float(bars[j][1])
        if bh > entry_hwm:
            false_peak = True
        if bo > 0:
            adv = (bh - bo) / bo
            if adv > max_adverse_pct:
                max_adverse_pct = adv
            if adv > STOP_PCT:
                candles_exceeding_stop += 1

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
        "pnl_pct_raw": pnl_pct,
        "r_multiple": r_multiple,
        "risk_per_unit": risk,
        "false_peak": false_peak,
        "max_adverse_candle_pct": round(max_adverse_pct * 100, 4),
        "candles_exceeding_stop": candles_exceeding_stop,
        "funding_rates_reported": cluster.get("funding_rates_reported", []),
    }


# ---------------------------------------------------------------------------
# Compute PF helper
# ---------------------------------------------------------------------------
def compute_pf_stats(trades, label=""):
    if not trades:
        return {"label": label, "n": 0}
    wins_usd = 0
    losses_usd = 0
    for t in trades:
        cost = ROUND_TRIP_FRICTION + abs(BASE_FUNDING_RATE) * t["n_funding_epochs"]
        pnl = POSITION_SIZE_USD * (t["pnl_pct_raw"] - cost)
        if pnl > 0:
            wins_usd += pnl
        else:
            losses_usd += abs(pnl)
    n = len(trades)
    winners = [t for t in trades if POSITION_SIZE_USD * (t["pnl_pct_raw"] - ROUND_TRIP_FRICTION - abs(BASE_FUNDING_RATE) * t["n_funding_epochs"]) > 0]
    pf = wins_usd / losses_usd if losses_usd > 0 else float("inf")
    return {
        "label": label,
        "n": n,
        "win_rate": round(len(winners) / n * 100, 1),
        "pf": round(pf, 4),
        "total_pnl_usd": round(wins_usd - losses_usd, 2),
        "false_peak_pct": round(sum(1 for t in trades if t["false_peak"]) / n * 100, 1),
        "stop_loss_pct": round(sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS") / n * 100, 1),
        "mean_r": round(float(np.mean([t["r_multiple"] for t in trades])), 4),
        "gap_throughs": sum(1 for t in trades if t["candles_exceeding_stop"] > 0),
        "gap_through_pct": round(sum(1 for t in trades if t["candles_exceeding_stop"] > 0) / n * 100, 1),
    }


# ---------------------------------------------------------------------------
# Task 1: Mania Extreme Gate
# ---------------------------------------------------------------------------
def task1_mania_gate(trades):
    logger.info("[TASK 1] Mania extreme gate (threshold=%.0f%%)...", MANIA_EXTREME_THRESHOLD)

    passed = []
    rejected = []
    for t in trades:
        mpc = t.get("max_pct_change")
        if mpc is not None and mpc > MANIA_EXTREME_THRESHOLD:
            rejected.append(t)
        else:
            passed.append(t)

    logger.info("  Rejected: %d, Passed: %d", len(rejected), len(passed))

    rejected_detail = []
    for t in rejected:
        cost = ROUND_TRIP_FRICTION + abs(BASE_FUNDING_RATE) * t["n_funding_epochs"]
        pnl = POSITION_SIZE_USD * (t["pnl_pct_raw"] - cost)
        rejected_detail.append({
            "symbol": t["symbol"],
            "max_pct_change": t["max_pct_change"],
            "exit_reason": t["exit_reason"],
            "pnl_usd": round(pnl, 2),
            "was_winner": pnl > 0,
            "reason": "mania_extreme_filtered",
        })

    return {
        "threshold_pct": MANIA_EXTREME_THRESHOLD,
        "before": compute_pf_stats(trades, "before_gate"),
        "after": compute_pf_stats(passed, "after_gate"),
        "rejected_count": len(rejected),
        "rejected_detail": rejected_detail,
        "passed_trades": passed,
    }


# ---------------------------------------------------------------------------
# Task 2: Liquidity Filter (retroactive using OHLCV volume)
# ---------------------------------------------------------------------------
def task2_liquidity_filter(trades, ohlcv_by_symbol):
    logger.info("[TASK 2] Liquidity filter (retroactive volume sweep)...")

    # For each trade, compute 24h USD volume at signal time from OHLCV
    for t in trades:
        sym = t["symbol"]
        bars = ohlcv_by_symbol.get(sym, [])
        if not bars:
            t["vol_24h_usd_at_signal"] = None
            continue

        bar_times = [b[0] for b in bars]
        entry_ts = t["entry_ts"]

        # Sum volume * close for 24 bars before entry
        entry_idx = bisect.bisect_right(bar_times, entry_ts)
        start_vol_idx = max(0, entry_idx - 24)
        vol_sum = 0.0
        for j in range(start_vol_idx, entry_idx):
            vol = float(bars[j][5]) if bars[j][5] is not None else 0
            close = float(bars[j][4])
            vol_sum += vol * close  # quote volume approx

        t["vol_24h_usd_at_signal"] = round(vol_sum, 2)

    # Sweep across thresholds
    results = {}
    for thresh in VOLUME_THRESHOLDS:
        passed = [t for t in trades if t.get("vol_24h_usd_at_signal") is not None
                  and t["vol_24h_usd_at_signal"] >= thresh]
        failed = [t for t in trades if t.get("vol_24h_usd_at_signal") is not None
                  and t["vol_24h_usd_at_signal"] < thresh]
        no_data = [t for t in trades if t.get("vol_24h_usd_at_signal") is None]

        stats = compute_pf_stats(passed, f"vol>={thresh/1e6:.1f}M")

        # Check if gap-throughs are concentrated in low-liquidity
        gap_in_failed = sum(1 for t in failed if t["candles_exceeding_stop"] > 0)
        gap_in_passed = sum(1 for t in passed if t["candles_exceeding_stop"] > 0)

        results[f"{thresh/1e6:.1f}M"] = {
            "threshold_usd": thresh,
            "passed": len(passed),
            "failed": len(failed),
            "no_data": len(no_data),
            "stats": stats,
            "gap_throughs_in_passed": gap_in_passed,
            "gap_throughs_in_failed": gap_in_failed,
            "gap_through_concentration": (
                f"{gap_in_failed}/{len(failed)} low-liq trades had gaps vs "
                f"{gap_in_passed}/{len(passed)} high-liq"
                if failed else "all trades above threshold"
            ),
        }

        logger.info("  Vol >= $%.0fK: %d/%d pass, PF=%.4f, gaps: %d passed / %d failed",
                     thresh / 1000, len(passed), len(trades),
                     stats.get("pf", 0), gap_in_passed, gap_in_failed)

    # Volume distribution of all trades
    vols = [t["vol_24h_usd_at_signal"] for t in trades if t.get("vol_24h_usd_at_signal") is not None]
    vol_dist = {}
    if vols:
        for p in [10, 25, 50, 75, 90]:
            vol_dist[f"p{p}"] = round(float(np.percentile(vols, p)), 0)
        vol_dist["min"] = round(min(vols), 0)
        vol_dist["max"] = round(max(vols), 0)
        vol_dist["mean"] = round(float(np.mean(vols)), 0)

    return {
        "thresholds": results,
        "volume_distribution_usd": vol_dist,
        "trades_with_volume_data": len(vols),
    }


# ---------------------------------------------------------------------------
# Task 3: Position Sizing Overlay
# ---------------------------------------------------------------------------
def task3_position_sizing(trades):
    logger.info("[TASK 3] Position sizing overlay (adverse excursion)...")

    adverse_moves = [t["max_adverse_candle_pct"] for t in trades]

    sizing = {}
    for pct in ADVERSE_EXCURSION_PERCENTILES:
        ae_pct = float(np.percentile(adverse_moves, pct))
        stop_distance_pct = STOP_PCT * 100  # 15%
        effective_risk = max(stop_distance_pct, ae_pct)
        sizing_reduction = 1 - (stop_distance_pct / effective_risk) if effective_risk > stop_distance_pct else 0

        # For a $10K account risking 2% per trade ($200 risk budget):
        example_account = 10000
        risk_budget_pct = 0.02
        risk_budget_usd = example_account * risk_budget_pct
        naive_position = risk_budget_usd / (stop_distance_pct / 100)
        adjusted_position = risk_budget_usd / (effective_risk / 100)

        sizing[f"p{pct}"] = {
            "adverse_excursion_pct": round(ae_pct, 2),
            "stop_distance_pct": stop_distance_pct,
            "effective_risk_pct": round(effective_risk, 2),
            "sizing_reduction_pct": round(sizing_reduction * 100, 1),
            "example": {
                "account_usd": example_account,
                "risk_per_trade_pct": risk_budget_pct * 100,
                "naive_position_usd": round(naive_position, 2),
                "adjusted_position_usd": round(adjusted_position, 2),
            },
        }
        logger.info("  p%d: AE=%.2f%%, effective_risk=%.2f%%, sizing -%0.1f%%",
                     pct, ae_pct, effective_risk, sizing_reduction * 100)

    # Full distribution
    ae_dist = {}
    if adverse_moves:
        for p in [25, 50, 75, 90, 95, 99]:
            ae_dist[f"p{p}"] = round(float(np.percentile(adverse_moves, p)), 2)

    return {
        "sizing_by_percentile": sizing,
        "adverse_excursion_distribution": ae_dist,
        "n_trades": len(trades),
    }


# ---------------------------------------------------------------------------
# Task 4: Funding Rate Backfill
# ---------------------------------------------------------------------------
def task4_funding_backfill(trades):
    logger.info("[TASK 4] Funding rate backfill from Binance...")

    # Collect unique (symbol, entry_ts) pairs
    funding_data = {}
    symbols_to_fetch = set()
    for t in trades:
        symbols_to_fetch.add(t["symbol"])

    # Fetch historical funding rates
    backfilled = 0
    failed = 0
    already_had = 0

    for t in trades:
        sym = t["symbol"]
        entry_ts = t["entry_ts"]
        exit_ts = t["exit_ts"]

        if t["funding_rates_reported"]:
            already_had += 1

        # Fetch funding rates around entry time
        # Binance API: /fapi/v1/fundingRate?symbol=X&startTime=Y&endTime=Z&limit=100
        try:
            start_ms = int((entry_ts - timedelta(hours=8)).timestamp() * 1000)
            end_ms = int((exit_ts + timedelta(hours=8)).timestamp() * 1000)

            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={
                    "symbol": sym,
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 100,
                },
                timeout=5,
            )

            if resp.status_code == 200:
                rates = resp.json()
                if rates:
                    funding_rates = [float(r["fundingRate"]) * 100 for r in rates]  # convert to %
                    funding_times = [
                        datetime.fromtimestamp(r["fundingTime"] / 1000, tz=timezone.utc)
                        for r in rates
                    ]

                    # Filter to rates during position hold
                    hold_rates = []
                    for ft, fr in zip(funding_times, funding_rates):
                        if entry_ts <= ft <= exit_ts:
                            hold_rates.append(fr)

                    t["backfilled_funding_rates"] = hold_rates
                    t["backfilled_funding_count"] = len(hold_rates)
                    t["backfilled_mean_funding_pct"] = round(float(np.mean(hold_rates)), 6) if hold_rates else None
                    t["backfilled_total_funding_cost_pct"] = round(sum(abs(r) for r in hold_rates if r < 0), 6) if hold_rates else 0

                    backfilled += 1
                else:
                    t["backfilled_funding_rates"] = []
                    t["backfilled_funding_count"] = 0
                    failed += 1
            else:
                t["backfilled_funding_rates"] = []
                t["backfilled_funding_count"] = 0
                failed += 1

        except Exception as e:
            t["backfilled_funding_rates"] = []
            t["backfilled_funding_count"] = 0
            failed += 1

        # Rate limit: be gentle
        time.sleep(0.1)

    logger.info("  Backfilled: %d, Failed: %d, Already had bot data: %d",
                 backfilled, failed, already_had)

    # Summary
    with_funding = [t for t in trades if t.get("backfilled_funding_count", 0) > 0]
    coverage = len(with_funding) / len(trades) * 100 if trades else 0

    funding_summary = {}
    if with_funding:
        all_rates = []
        for t in with_funding:
            all_rates.extend(t.get("backfilled_funding_rates", []))

        if all_rates:
            funding_summary = {
                "mean_rate_pct": round(float(np.mean(all_rates)), 6),
                "median_rate_pct": round(float(np.median(all_rates)), 6),
                "min_rate_pct": round(min(all_rates), 6),
                "max_rate_pct": round(max(all_rates), 6),
                "n_epochs_total": len(all_rates),
                "pct_negative": round(sum(1 for r in all_rates if r < 0) / len(all_rates) * 100, 1),
            }

        # True funding cost: replace assumed with actual
        true_cost_trades = []
        for t in with_funding:
            actual_funding_cost = t.get("backfilled_total_funding_cost_pct", 0) / 100
            total_cost = ROUND_TRIP_FRICTION + actual_funding_cost
            pnl = POSITION_SIZE_USD * (t["pnl_pct_raw"] - total_cost)
            true_cost_trades.append(pnl)

        wins_true = sum(p for p in true_cost_trades if p > 0)
        losses_true = abs(sum(p for p in true_cost_trades if p <= 0))
        pf_true_funding = wins_true / losses_true if losses_true > 0 else float("inf")

        funding_summary["pf_with_true_funding"] = round(pf_true_funding, 4)
        funding_summary["n_trades_with_true_funding"] = len(with_funding)

    return {
        "backfilled": backfilled,
        "failed": failed,
        "already_had_bot_data": already_had,
        "coverage_pct": round(coverage, 1),
        "coverage_target": "70% (40/58)",
        "target_met": coverage >= 70,
        "funding_summary": funding_summary,
        "recommendation": (
            "RE-RUN funding gate analysis — coverage now sufficient"
            if coverage >= 70
            else f"Coverage {coverage:.0f}% — still insufficient for funding gate. Need more data."
        ),
    }


# ---------------------------------------------------------------------------
# Task 5: Expand N Analysis
# ---------------------------------------------------------------------------
def task5_expand_n(clusters, trades, ohlcv_by_symbol, binance_perps):
    logger.info("[TASK 5] Expand N analysis...")

    # Current lookback window
    all_starts = [c["cluster_start_ts"] for c in clusters]
    earliest_cluster = min(all_starts)
    latest_cluster = max(all_starts)
    span_days = (latest_cluster - earliest_cluster).total_seconds() / 86400

    # Monthly distribution of clusters
    monthly = defaultdict(int)
    for c in clusters:
        monthly[c["cluster_start_ts"].strftime("%Y-%m")] += 1

    # How many clusters have OHLCV?
    clusters_with_ohlcv = sum(1 for c in clusters if c["symbol"] in ohlcv_by_symbol)
    clusters_binance = sum(1 for c in clusters if c["symbol"] in binance_perps)

    # Current conversion: clusters → trades
    conversion_rate = len(trades) / clusters_binance * 100 if clusters_binance > 0 else 0

    # Projection: to reach N=120, how many more months needed?
    months_of_data = len(monthly)
    trades_per_month = len(trades) / months_of_data if months_of_data > 0 else 0
    months_to_120 = (120 - len(trades)) / trades_per_month if trades_per_month > 0 else float("inf")

    # What happens if we lower MIN_ALERTS_PER_CLUSTER to 2?
    # Re-detect with min=2
    all_signals = []
    for c in clusters:
        pass  # We don't have raw signals here, so estimate

    return {
        "current_lookback": {
            "earliest_cluster": earliest_cluster.isoformat(),
            "latest_cluster": latest_cluster.isoformat(),
            "span_days": round(span_days, 0),
            "span_months": round(span_days / 30, 1),
        },
        "current_yield": {
            "total_clusters": len(clusters),
            "clusters_with_ohlcv": clusters_with_ohlcv,
            "clusters_binance": clusters_binance,
            "trades_generated": len(trades),
            "conversion_rate_pct": round(conversion_rate, 1),
            "trades_per_month": round(trades_per_month, 1),
        },
        "monthly_distribution": dict(sorted(monthly.items())),
        "projection_to_n120": {
            "additional_months_needed": round(months_to_120, 1) if months_to_120 != float("inf") else "N/A",
            "note": "Assumes constant signal rate. Real rate may change with market conditions.",
        },
        "expansion_options": [
            {
                "option": "Wait for more data",
                "description": f"At {trades_per_month:.1f} trades/month, need ~{months_to_120:.0f} more months",
                "pros": "No methodology changes, clean out-of-sample",
                "cons": "Slow",
            },
            {
                "option": "Lower MIN_ALERTS_PER_CLUSTER from 3 to 2",
                "description": "More clusters detected, but noisier signals",
                "pros": "Immediate N increase",
                "cons": "Quality dilution — PF may drop, need to re-validate",
            },
            {
                "option": "Include Bybit/OKX perps",
                "description": "Some symbols may be shortable on other exchanges",
                "pros": "More executable signals",
                "cons": "Different fee structures, fragmented liquidity",
            },
        ],
        "recommendation": (
            "Wait for organic growth to N>120. Current rate suggests "
            f"~{months_to_120:.0f} months. Do NOT dilute signal quality to inflate N."
            if months_to_120 < 24
            else "Consider lowering cluster threshold to expand N, but re-validate PF."
        ),
    }


# ---------------------------------------------------------------------------
# Combined filter: true N and true PF
# ---------------------------------------------------------------------------
def compute_true_set(trades, ohlcv_by_symbol, vol_threshold=1_000_000):
    """Apply mania gate + liquidity filter, return surviving trades."""
    # Mania gate
    after_mania = [t for t in trades
                   if t.get("max_pct_change") is None
                   or t["max_pct_change"] <= MANIA_EXTREME_THRESHOLD]

    # Liquidity gate
    surviving = [t for t in after_mania
                 if t.get("vol_24h_usd_at_signal") is not None
                 and t["vol_24h_usd_at_signal"] >= vol_threshold]

    return surviving


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------
def write_report(t1, t2, t3, t4, t5, true_set_stats, output_path):
    lines = []
    w = lines.append

    w("=" * 75)
    w("D2 GARBAGE COLLECTOR — NEXT STEPS REPORT")
    w("=" * 75)

    # Task 1
    w("")
    w("TASK 1: Mania Extreme Gate (max_pct_change > 40%)")
    w("-" * 50)
    b = t1["before"]
    a = t1["after"]
    w(f"  Before: N={b['n']}, PF={b['pf']:.4f}, WR={b['win_rate']:.1f}%")
    w(f"  After:  N={a['n']}, PF={a['pf']:.4f}, WR={a['win_rate']:.1f}%")
    w(f"  Rejected: {t1['rejected_count']} trades")
    for rd in t1["rejected_detail"]:
        w(f"    {rd['symbol']}: max_pct={rd['max_pct_change']:.1f}%, "
          f"exit={rd['exit_reason']}, PnL=${rd['pnl_usd']:.2f} "
          f"({'winner' if rd['was_winner'] else 'loser'})")

    # Task 2
    w("")
    w("TASK 2: Liquidity Filter (24h volume at signal time)")
    w("-" * 50)
    vd = t2.get("volume_distribution_usd", {})
    if vd:
        w(f"  Volume distribution: min=${vd.get('min', 0):,.0f}, "
          f"p25=${vd.get('p25', 0):,.0f}, p50=${vd.get('p50', 0):,.0f}, "
          f"p75=${vd.get('p75', 0):,.0f}, max=${vd.get('max', 0):,.0f}")

    w(f"\n  {'Threshold':>12} | {'Pass':>5} | {'Fail':>5} | {'PF':>8} | {'WR%':>5} | {'Gaps(pass)':>10} | {'Gaps(fail)':>10}")
    w(f"  {'-'*12} | {'-'*5} | {'-'*5} | {'-'*8} | {'-'*5} | {'-'*10} | {'-'*10}")
    for key, data in sorted(t2["thresholds"].items()):
        s = data["stats"]
        w(f"  ${data['threshold_usd']/1e6:>10.1f}M | {data['passed']:>5} | {data['failed']:>5} | "
          f"{s.get('pf', 0):>8.4f} | {s.get('win_rate', 0):>5.1f} | "
          f"{data['gap_throughs_in_passed']:>10} | {data['gap_throughs_in_failed']:>10}")

    # Task 3
    w("")
    w("TASK 3: Position Sizing (Adverse Excursion)")
    w("-" * 50)
    ae = t3.get("adverse_excursion_distribution", {})
    w(f"  Max adverse candle distribution: p50={ae.get('p50', 0):.2f}%, "
      f"p75={ae.get('p75', 0):.2f}%, p90={ae.get('p90', 0):.2f}%, p95={ae.get('p95', 0):.2f}%")

    w(f"\n  {'Percentile':>10} | {'AE%':>6} | {'Eff.Risk%':>9} | {'Sizing adj':>10} | {'$10K/2% pos':>12}")
    w(f"  {'-'*10} | {'-'*6} | {'-'*9} | {'-'*10} | {'-'*12}")
    for key, data in sorted(t3["sizing_by_percentile"].items()):
        w(f"  {key:>10} | {data['adverse_excursion_pct']:>6.2f} | {data['effective_risk_pct']:>9.2f} | "
          f"{data['sizing_reduction_pct']:>9.1f}% | ${data['example']['adjusted_position_usd']:>10,.2f}")

    # Task 4
    w("")
    w("TASK 4: Funding Rate Backfill")
    w("-" * 50)
    w(f"  Backfilled: {t4['backfilled']}/{t4['backfilled'] + t4['failed']} symbols")
    w(f"  Coverage: {t4['coverage_pct']:.1f}% (target: 70%)")
    w(f"  Target met: {'YES' if t4['target_met'] else 'NO'}")
    fs = t4.get("funding_summary", {})
    if fs:
        w(f"  Mean funding rate: {fs.get('mean_rate_pct', 0):.6f}%")
        w(f"  Median funding rate: {fs.get('median_rate_pct', 0):.6f}%")
        w(f"  Min/Max: {fs.get('min_rate_pct', 0):.4f}% / {fs.get('max_rate_pct', 0):.4f}%")
        w(f"  % negative (you pay): {fs.get('pct_negative', 0):.1f}%")
        if "pf_with_true_funding" in fs:
            w(f"  PF with true funding (N={fs.get('n_trades_with_true_funding', 0)}): {fs['pf_with_true_funding']:.4f}")
    w(f"  Recommendation: {t4['recommendation']}")

    # Task 5
    w("")
    w("TASK 5: Expand N Analysis")
    w("-" * 50)
    cy = t5["current_yield"]
    w(f"  Lookback: {t5['current_lookback']['span_months']:.1f} months")
    w(f"  Clusters: {cy['total_clusters']} → Binance: {cy['clusters_binance']} → Trades: {cy['trades_generated']}")
    w(f"  Conversion: {cy['conversion_rate_pct']:.1f}%, {cy['trades_per_month']:.1f} trades/month")
    proj = t5["projection_to_n120"]
    w(f"  Months to N=120: {proj['additional_months_needed']}")
    w(f"  Recommendation: {t5['recommendation']}")

    # True set
    w("")
    w("=" * 75)
    w("TRUE SET (mania gate + $1M liquidity filter)")
    w("=" * 75)
    ts = true_set_stats
    w(f"  N = {ts['n']}")
    w(f"  Win Rate = {ts.get('win_rate', 0):.1f}%")
    w(f"  Profit Factor = {ts.get('pf', 0):.4f}")
    w(f"  Total PnL = ${ts.get('total_pnl_usd', 0):.2f}")
    w(f"  False Peak Rate = {ts.get('false_peak_pct', 0):.1f}%")
    w(f"  Stop Loss Rate = {ts.get('stop_loss_pct', 0):.1f}%")
    w(f"  Gap-Through Rate = {ts.get('gap_through_pct', 0):.1f}%")

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
    logger.info("D2 Next Steps starting...")
    logger.info("Output JSON: %s", OUTPUT_JSON)
    logger.info("Output TXT:  %s", OUTPUT_TXT)

    binance_perps = get_binance_perps()

    with psycopg.connect(DATABASE_URL) as conn:
        signals = load_pump_signals(conn)
        logger.info("  %d signals", len(signals))
        clusters = detect_pump_clusters(signals)
        logger.info("  %d clusters", len(clusters))
        ohlcv = load_ohlcv(conn, clusters)
        logger.info("  OHLCV for %d symbols", len(ohlcv))

    # Generate trades (entry25_stop15 Binance-only)
    logger.info("Simulating entry25_stop15 Binance-only...")
    trades = []
    for c in clusters:
        sym = c["symbol"]
        if sym not in ohlcv or sym not in binance_perps:
            continue
        bars = ohlcv[sym]
        bar_times = [b[0] for b in bars]
        trade = simulate_hwm_trailing(c, bars, bar_times)
        if trade:
            trades.append(trade)
    logger.info("  %d trades generated", len(trades))

    # Task 1
    t1 = task1_mania_gate(trades)

    # Task 2 (uses mania-gated trades)
    t2 = task2_liquidity_filter(t1["passed_trades"], ohlcv)

    # Task 3
    t3 = task3_position_sizing(t1["passed_trades"])

    # Task 4 (funding backfill on all original trades)
    t4 = task4_funding_backfill(trades)

    # Task 5
    t5 = task5_expand_n(clusters, trades, ohlcv, binance_perps)

    # True set: mania gate + $1M liquidity
    true_trades = compute_true_set(trades, ohlcv, vol_threshold=1_000_000)
    true_stats = compute_pf_stats(true_trades, "true_set_1M")

    elapsed = round(time.time() - t0, 1)
    logger.info("Complete in %.1fs", elapsed)

    # JSON
    output = {
        "metadata": {
            "generated_at": TIMESTAMP,
            "elapsed_seconds": elapsed,
            "config": "entry25_stop15 Binance-only",
            "n_trades_raw": len(trades),
        },
        "task1_mania_gate": {k: v for k, v in t1.items() if k != "passed_trades"},
        "task2_liquidity_filter": t2,
        "task3_position_sizing": t3,
        "task4_funding_backfill": t4,
        "task5_expand_n": t5,
        "true_set": true_stats,
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)

    report = write_report(t1, t2, t3, t4, t5, true_stats, OUTPUT_TXT)
    print(report)


if __name__ == "__main__":
    main()
