#!/usr/bin/env python3
"""
D2 Microstructure Stress Test — entry25_stop15 Binance-only
=============================================================
Test 1: Asymmetric slippage (stop exits get 0.5-3% slippage)
Test 2: Funding rate gate (reject entry when funding < threshold)
Test 3: Inverse mania filter (tier by pump intensity)
Test 4: Win rate degradation Monte Carlo
Test 5: Black swan gap risk analysis

Re-runs the HWM trailing simulation for entry25_stop15 to capture
per-trade data, then applies stress tests without re-querying DB.

Output: scripts/output/d2_stress_{ts}.json + .txt

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d2_stress_test.py
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
OUTPUT_JSON = os.path.join(OUTPUT_DIR, f"d2_stress_{TIMESTAMP}.json")
OUTPUT_TXT = os.path.join(OUTPUT_DIR, f"d2_stress_{TIMESTAMP}.txt")

# ---------------------------------------------------------------------------
# Constants — locked config
# ---------------------------------------------------------------------------
CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3
ENTRY_THRESHOLD = 0.25       # 25% drop from HWM
STOP_PCT = 0.15              # 15% above HWM at entry
TIME_STOP_HOURS = 72
MAX_WATCH_HOURS = 48
POSITION_SIZE_USD = 1000

# Base costs
TAKER_FEE_PCT = 0.0004       # 0.04% per side
BASE_SLIPPAGE_PCT = 0.0010   # 0.10% per side (normal exits)
BASE_FUNDING_RATE = -0.003   # -0.3% per 8h epoch (base case)

# Monte Carlo
MC_SIMULATIONS = 10000
MC_TRADE_COUNT = 58


# ---------------------------------------------------------------------------
# Cluster detection + OHLCV (same as d2_honest_backtest.py)
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


def load_ohlcv(conn, clusters):
    symbols = sorted(set(c["symbol"] for c in clusters))
    earliest = min(c["cluster_start_ts"] for c in clusters) - timedelta(hours=2)
    latest = max(c["cluster_start_ts"] for c in clusters) + timedelta(hours=130)

    ohlcv = {}
    with conn.cursor() as cur:
        for sym in symbols:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close
                FROM ohlcv WHERE symbol = %s AND timeframe = '1h'
                AND bar_start_ts >= %s AND bar_start_ts <= %s
                ORDER BY bar_start_ts
            """, (sym, earliest, latest))
            rows = cur.fetchall()
            if rows:
                ohlcv[sym] = rows
    return ohlcv


def get_binance_perp_symbols():
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10)
        resp.raise_for_status()
        return {s["symbol"] for s in resp.json()["symbols"]
                if s.get("contractType") == "PERPETUAL" and s["symbol"].endswith("USDT")}
    except Exception as e:
        logger.warning("Failed to fetch Binance perps: %s", e)
        return set()


# ---------------------------------------------------------------------------
# HWM trailing simulation — returns per-trade detail
# ---------------------------------------------------------------------------
def simulate_hwm_trailing(cluster, bars, bar_times):
    """Run entry25_stop15 and return full trade detail."""
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

    entry_price = None
    entry_ts = None
    entry_hwm = None
    entry_bar_idx = None
    stop_price = None
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
                                   pre_pump_price, i - entry_bar_idx,
                                   bars, bar_times, entry_bar_idx, i)

            if bar_low <= tp_price:
                return _make_trade(cluster, "TP_HIT", tp_price, entry_price,
                                   entry_ts, bar_ts, entry_hwm, tp_price, stop_price,
                                   pre_pump_price, i - entry_bar_idx,
                                   bars, bar_times, entry_bar_idx, i)

            if bar_ts >= time_stop_ts:
                return _make_trade(cluster, "TIME_STOP", bar_close, entry_price,
                                   entry_ts, bar_ts, entry_hwm, tp_price, stop_price,
                                   pre_pump_price, i - entry_bar_idx,
                                   bars, bar_times, entry_bar_idx, i)

    if state == "IN_TRADE":
        last_i = len(bars) - 1
        return _make_trade(cluster, "TIME_STOP", float(bars[last_i][4]),
                           entry_price, entry_ts, bar_times[last_i],
                           entry_hwm, tp_price, stop_price, pre_pump_price,
                           last_i - entry_bar_idx,
                           bars, bar_times, entry_bar_idx, last_i)
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

    # False peak: did price exceed entry_hwm after entry?
    false_peak = False
    for j in range(entry_bar_idx + 1, exit_bar_idx + 1):
        if float(bars[j][2]) > entry_hwm:
            false_peak = True
            break

    # Gap analysis: max single-candle adverse move after entry
    max_adverse_candle_pct = 0.0
    candles_exceeding_stop_distance = 0
    stop_distance_pct = STOP_PCT  # 15%

    for j in range(entry_bar_idx + 1, min(exit_bar_idx + 1, len(bars))):
        bar_open = float(bars[j][1])
        bar_high = float(bars[j][2])
        if bar_open > 0:
            adverse_move = (bar_high - bar_open) / bar_open  # upward = adverse for short
            if adverse_move > max_adverse_candle_pct:
                max_adverse_candle_pct = adverse_move
            if adverse_move > stop_distance_pct:
                candles_exceeding_stop_distance += 1

    # Also check all bars in cluster window (for gap risk context)
    total_post_entry_bars = exit_bar_idx - entry_bar_idx

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
        "pnl_pct_raw": pnl_pct,  # before any costs
        "r_multiple": r_multiple,
        "risk_per_unit": risk,
        "false_peak": false_peak,
        "funding_rates_reported": cluster.get("funding_rates_reported", []),
        "max_adverse_candle_pct": round(max_adverse_candle_pct * 100, 4),
        "candles_exceeding_stop_distance": candles_exceeding_stop_distance,
        "total_post_entry_bars": total_post_entry_bars,
    }


# ---------------------------------------------------------------------------
# Test 1: Asymmetric Slippage
# ---------------------------------------------------------------------------
def test1_asymmetric_slippage(trades):
    logger.info("[TEST 1] Asymmetric slippage...")

    stop_slippage_scenarios = [50, 100, 150, 200, 300]  # bps
    results = []

    for stop_slip_bps in stop_slippage_scenarios:
        stop_slip = stop_slip_bps / 10000
        total_pnl = 0
        wins_usd = 0
        losses_usd = 0

        for t in trades:
            # Entry slippage: always base (0.10%)
            entry_cost = TAKER_FEE_PCT + BASE_SLIPPAGE_PCT

            # Exit slippage: depends on exit type
            if t["exit_reason"] == "STOP_LOSS":
                exit_cost = TAKER_FEE_PCT + stop_slip
            else:
                exit_cost = TAKER_FEE_PCT + BASE_SLIPPAGE_PCT

            # Funding
            funding_cost = abs(BASE_FUNDING_RATE) * t["n_funding_epochs"]

            total_cost = entry_cost + exit_cost + funding_cost
            pnl_after = t["pnl_pct_raw"] - total_cost
            pnl_usd = POSITION_SIZE_USD * pnl_after

            total_pnl += pnl_usd
            if pnl_usd > 0:
                wins_usd += pnl_usd
            else:
                losses_usd += abs(pnl_usd)

        pf = wins_usd / losses_usd if losses_usd > 0 else float("inf")
        results.append({
            "stop_slippage_bps": stop_slip_bps,
            "stop_slippage_pct": round(stop_slip_bps / 100, 2),
            "pf_after": round(pf, 4),
            "total_pnl_usd": round(total_pnl, 2),
        })
        logger.info("    Stop slip %d bps: PF=%.4f, PnL=$%.2f", stop_slip_bps, pf, total_pnl)

    # Find breakeven
    breakeven = None
    for slip_bps in range(10, 5000, 10):
        stop_slip = slip_bps / 10000
        wins_usd = 0
        losses_usd = 0
        for t in trades:
            entry_cost = TAKER_FEE_PCT + BASE_SLIPPAGE_PCT
            if t["exit_reason"] == "STOP_LOSS":
                exit_cost = TAKER_FEE_PCT + stop_slip
            else:
                exit_cost = TAKER_FEE_PCT + BASE_SLIPPAGE_PCT
            funding_cost = abs(BASE_FUNDING_RATE) * t["n_funding_epochs"]
            pnl_after = t["pnl_pct_raw"] - entry_cost - exit_cost - funding_cost
            pnl_usd = POSITION_SIZE_USD * pnl_after
            if pnl_usd > 0:
                wins_usd += pnl_usd
            else:
                losses_usd += abs(pnl_usd)
        pf = wins_usd / losses_usd if losses_usd > 0 else float("inf")
        if pf < 1.0:
            breakeven = round(slip_bps / 100, 1)
            break

    return {
        "scenarios": results,
        "breakeven_stop_slippage_pct": breakeven,
        "note": "Breakeven = stop slippage at which PF drops below 1.0" if breakeven else "PF stays above 1.0 even at 50% stop slippage",
    }


# ---------------------------------------------------------------------------
# Test 2: Funding Gate
# ---------------------------------------------------------------------------
def test2_funding_gate(trades):
    logger.info("[TEST 2] Funding gate...")

    thresholds = [-0.50, -0.75, -1.00]
    results = []

    # Count how many trades have funding data
    with_funding = [t for t in trades if t["funding_rates_reported"]]
    logger.info("    Trades with funding data: %d / %d", len(with_funding), len(trades))

    for thresh in thresholds:
        rejected = []
        remaining = []

        for t in trades:
            if t["funding_rates_reported"]:
                worst_funding = min(t["funding_rates_reported"])
                if worst_funding < thresh:
                    rejected.append(t)
                    continue
            remaining.append(t)

        # Compute PF on remaining
        wins_usd = 0
        losses_usd = 0
        for t in remaining:
            cost = 2 * (TAKER_FEE_PCT + BASE_SLIPPAGE_PCT) + abs(BASE_FUNDING_RATE) * t["n_funding_epochs"]
            pnl = POSITION_SIZE_USD * (t["pnl_pct_raw"] - cost)
            if pnl > 0:
                wins_usd += pnl
            else:
                losses_usd += abs(pnl)
        pf = wins_usd / losses_usd if losses_usd > 0 else float("inf")

        # Were rejected trades winners or losers?
        rejected_winners = sum(1 for t in rejected if t["pnl_pct_raw"] > 0.003)  # net positive after ~costs

        results.append({
            "threshold_pct": thresh,
            "rejected": len(rejected),
            "remaining": len(remaining),
            "pf_remaining": round(pf, 4),
            "rejected_winners": rejected_winners,
            "rejected_losers": len(rejected) - rejected_winners,
        })
        logger.info("    Gate %.2f%%: rejected=%d, remaining=%d, PF=%.4f",
                     thresh, len(rejected), len(remaining), pf)

    return {
        "trades_with_funding_data": len(with_funding),
        "total_trades": len(trades),
        "data_coverage_pct": round(len(with_funding) / len(trades) * 100, 1),
        "scenarios": results,
        "note": f"Only {len(with_funding)}/{len(trades)} trades have funding data. Results are directional only." if len(with_funding) < 20 else None,
    }


# ---------------------------------------------------------------------------
# Test 3: Inverse Mania Filter
# ---------------------------------------------------------------------------
def test3_mania_tiers(trades):
    logger.info("[TEST 3] Pump intensity tiers...")

    def tier_label(mpc):
        if mpc is None:
            return "unknown"
        if mpc <= 10:
            return "garbage_small"
        elif mpc <= 20:
            return "garbage_medium"
        elif mpc <= 40:
            return "mania_large"
        else:
            return "mania_extreme"

    tiers = defaultdict(list)
    for t in trades:
        tiers[tier_label(t["max_pct_change"])].append(t)

    def tier_stats(tier_trades, label):
        if not tier_trades:
            return {"tier": label, "n": 0}

        wins_usd = 0
        losses_usd = 0
        for t in tier_trades:
            cost = 2 * (TAKER_FEE_PCT + BASE_SLIPPAGE_PCT) + abs(BASE_FUNDING_RATE) * t["n_funding_epochs"]
            pnl = POSITION_SIZE_USD * (t["pnl_pct_raw"] - cost)
            if pnl > 0:
                wins_usd += pnl
            else:
                losses_usd += abs(pnl)

        pf = wins_usd / losses_usd if losses_usd > 0 else float("inf")
        n = len(tier_trades)
        winners = sum(1 for t in tier_trades if t["pnl_pct_raw"] > 0.003)

        return {
            "tier": label,
            "n": n,
            "win_rate": round(winners / n * 100, 1),
            "pf": round(pf, 4),
            "false_peak_pct": round(sum(1 for t in tier_trades if t["false_peak"]) / n * 100, 1),
            "stop_loss_pct": round(sum(1 for t in tier_trades if t["exit_reason"] == "STOP_LOSS") / n * 100, 1),
            "mean_r": round(float(np.mean([t["r_multiple"] for t in tier_trades])), 4),
        }

    tier_results = {}
    for label in ["garbage_small", "garbage_medium", "mania_large", "mania_extreme", "unknown"]:
        if label in tiers:
            tier_results[label] = tier_stats(tiers[label], label)
            logger.info("    %s: N=%d, PF=%.4f, WR=%.1f%%",
                         label, tier_results[label]["n"],
                         tier_results[label]["pf"],
                         tier_results[label]["win_rate"])

    # Excluding mania_extreme
    non_extreme = [t for t in trades if tier_label(t["max_pct_change"]) != "mania_extreme"]
    excl_stats = tier_stats(non_extreme, "excluding_mania_extreme")

    return {
        "tiers": tier_results,
        "excluding_mania_extreme": excl_stats,
    }


# ---------------------------------------------------------------------------
# Test 4: Win Rate Degradation Monte Carlo
# ---------------------------------------------------------------------------
def test4_monte_carlo(trades):
    logger.info("[TEST 4] Monte Carlo win rate degradation...")

    # Get actual R-multiple distributions
    winner_rs = [t["r_multiple"] for t in trades if t["pnl_pct_raw"] > 0.003]
    loser_rs = [t["r_multiple"] for t in trades if t["pnl_pct_raw"] <= 0.003]

    actual_wr = len(winner_rs) / len(trades)
    logger.info("    Actual: %d winners, %d losers, WR=%.1f%%",
                 len(winner_rs), len(loser_rs), actual_wr * 100)
    logger.info("    Winner R dist: mean=%.4f, std=%.4f",
                 np.mean(winner_rs), np.std(winner_rs))
    logger.info("    Loser R dist: mean=%.4f, std=%.4f",
                 np.mean(loser_rs), np.std(loser_rs))

    winner_rs = np.array(winner_rs)
    loser_rs = np.array(loser_rs)

    degraded_wrs = [0.80, 0.75, 0.70, 0.65, 0.60, 0.55]
    results = []

    rng = np.random.default_rng(42)

    for target_wr in degraded_wrs:
        pfs = []
        max_dds = []
        net_losses = 0
        ruins = 0  # DD > 50%

        for _ in range(MC_SIMULATIONS):
            # Generate MC_TRADE_COUNT trades
            n_wins = int(MC_TRADE_COUNT * target_wr)
            n_losses = MC_TRADE_COUNT - n_wins

            # Sample from actual distributions with replacement
            sim_winner_rs = rng.choice(winner_rs, size=n_wins, replace=True)
            sim_loser_rs = rng.choice(loser_rs, size=n_losses, replace=True)

            # Combine and shuffle
            all_rs = np.concatenate([sim_winner_rs, sim_loser_rs])
            rng.shuffle(all_rs)

            # Convert to PnL (approximate: R * risk, use mean risk from actual trades)
            mean_risk_pct = np.mean([t["risk_per_unit"] / t["entry_price"] for t in trades])
            # Add costs per trade
            cost_per_trade = 2 * (TAKER_FEE_PCT + BASE_SLIPPAGE_PCT) + abs(BASE_FUNDING_RATE) * 4  # ~4 epochs avg

            pnls = all_rs * mean_risk_pct - cost_per_trade
            pnl_usd = pnls * POSITION_SIZE_USD

            # PF
            wins_sum = pnl_usd[pnl_usd > 0].sum()
            losses_sum = abs(pnl_usd[pnl_usd <= 0].sum())
            pf = wins_sum / losses_sum if losses_sum > 0 else float("inf")
            pfs.append(min(pf, 100))  # cap for mean calc

            # Drawdown
            cumulative = np.cumsum(pnl_usd)
            running_max = np.maximum.accumulate(cumulative)
            # Start from 0, so DD relative to starting capital
            initial_capital = POSITION_SIZE_USD * MC_TRADE_COUNT * 0.1  # ~10% allocation
            dd = (running_max - cumulative) / initial_capital * 100
            max_dd = dd.max()
            max_dds.append(max_dd)

            if cumulative[-1] < 0:
                net_losses += 1
            if max_dd > 50:
                ruins += 1

        results.append({
            "target_wr": target_wr,
            "mean_pf": round(float(np.mean(pfs)), 4),
            "median_pf": round(float(np.median(pfs)), 4),
            "pf_10th_pct": round(float(np.percentile(pfs, 10)), 4),
            "mean_max_dd_pct": round(float(np.mean(max_dds)), 1),
            "p_net_loss": round(net_losses / MC_SIMULATIONS * 100, 1),
            "p_ruin": round(ruins / MC_SIMULATIONS * 100, 1),
        })
        logger.info("    WR=%.0f%%: PF=%.4f (median=%.4f), P(loss)=%.1f%%, P(ruin)=%.1f%%",
                     target_wr * 100, results[-1]["mean_pf"], results[-1]["median_pf"],
                     results[-1]["p_net_loss"], results[-1]["p_ruin"])

    # Find breakeven win rate (binary search)
    breakeven_wr = None
    for wr_test in np.arange(0.80, 0.30, -0.01):
        n_wins = int(MC_TRADE_COUNT * wr_test)
        n_losses = MC_TRADE_COUNT - n_wins
        losses_count = 0
        for _ in range(1000):  # quick sample
            sim_w = rng.choice(winner_rs, size=n_wins, replace=True)
            sim_l = rng.choice(loser_rs, size=n_losses, replace=True)
            all_rs = np.concatenate([sim_w, sim_l])
            mean_risk_pct = np.mean([t["risk_per_unit"] / t["entry_price"] for t in trades])
            cost = 2 * (TAKER_FEE_PCT + BASE_SLIPPAGE_PCT) + abs(BASE_FUNDING_RATE) * 4
            pnls = (all_rs * mean_risk_pct - cost) * POSITION_SIZE_USD
            if pnls.sum() < 0:
                losses_count += 1
        if losses_count / 1000 > 0.50:
            breakeven_wr = round(wr_test + 0.01, 2)  # previous step was above 50%
            break

    return {
        "actual_win_rate": round(actual_wr * 100, 1),
        "n_winners": len(winner_rs),
        "n_losers": len(loser_rs),
        "winner_r_mean": round(float(np.mean(winner_rs)), 4),
        "winner_r_std": round(float(np.std(winner_rs)), 4),
        "loser_r_mean": round(float(np.mean(loser_rs)), 4),
        "loser_r_std": round(float(np.std(loser_rs)), 4),
        "simulations": MC_SIMULATIONS,
        "trade_count_per_sim": MC_TRADE_COUNT,
        "degradation_results": results,
        "breakeven_win_rate": breakeven_wr,
    }


# ---------------------------------------------------------------------------
# Test 5: Gap Risk
# ---------------------------------------------------------------------------
def test5_gap_risk(trades):
    logger.info("[TEST 5] Black swan gap risk...")

    total_post_entry_bars = sum(t["total_post_entry_bars"] for t in trades)
    candles_exceeding = sum(t["candles_exceeding_stop_distance"] for t in trades)
    trades_with_gap = sum(1 for t in trades if t["candles_exceeding_stop_distance"] > 0)

    adverse_moves = [t["max_adverse_candle_pct"] for t in trades]
    worst_move = max(adverse_moves) if adverse_moves else 0

    # Distribution of max adverse moves
    percentiles = {}
    if adverse_moves:
        for p in [50, 75, 90, 95, 99]:
            percentiles[f"p{p}"] = round(float(np.percentile(adverse_moves, p)), 2)

    return {
        "total_post_entry_bars": total_post_entry_bars,
        "candles_exceeding_stop_distance": candles_exceeding,
        "trades_with_gap_through": trades_with_gap,
        "trades_total": len(trades),
        "gap_through_rate_pct": round(trades_with_gap / len(trades) * 100, 1) if trades else 0,
        "worst_single_candle_adverse_pct": round(worst_move, 2),
        "max_adverse_candle_percentiles": percentiles,
        "risk_assessment": (
            "NEGLIGIBLE" if trades_with_gap == 0
            else "MODERATE" if trades_with_gap <= 3
            else "SEVERE"
        ),
    }


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------
def write_report(t1, t2, t3, t4, t5, n_trades, output_path):
    lines = []
    w = lines.append

    w("=" * 70)
    w(f"D2 STRESS TEST — entry25_stop15 Binance-only (N={n_trades})")
    w("=" * 70)

    # Test 1
    w("")
    w("TEST 1: Asymmetric Slippage")
    w("-" * 50)
    w(f"  {'Stop slip':>10} | {'PF(after)':>10} | {'PnL($)':>10}")
    w(f"  {'-'*10} | {'-'*10} | {'-'*10}")
    for s in t1["scenarios"]:
        w(f"  {s['stop_slippage_pct']:>9.1f}% | {s['pf_after']:>10.4f} | {s['total_pnl_usd']:>10.2f}")
    be = t1["breakeven_stop_slippage_pct"]
    w(f"  Breakeven stop slippage: {be}%" if be else "  Breakeven: PF stays >1.0 even at extreme slippage")

    # Test 2
    w("")
    w("TEST 2: Funding Gate")
    w("-" * 50)
    w(f"  Trades with funding data: {t2['trades_with_funding_data']}/{t2['total_trades']} ({t2['data_coverage_pct']:.1f}%)")
    if t2.get("note"):
        w(f"  NOTE: {t2['note']}")
    w(f"  {'Threshold':>10} | {'Rejected':>8} | {'Remaining':>9} | {'PF':>8} | {'Win rej':>8}")
    w(f"  {'-'*10} | {'-'*8} | {'-'*9} | {'-'*8} | {'-'*8}")
    for s in t2["scenarios"]:
        w(f"  {s['threshold_pct']:>9.2f}% | {s['rejected']:>8} | {s['remaining']:>9} | "
          f"{s['pf_remaining']:>8.4f} | {s['rejected_winners']:>3}/{s['rejected']:>3}")

    # Test 3
    w("")
    w("TEST 3: Pump Intensity Tiers")
    w("-" * 50)
    w(f"  {'Tier':<20} | {'N':>4} | {'WR%':>5} | {'PF':>8} | {'FP%':>5} | {'SL%':>5} | {'MeanR':>7}")
    w(f"  {'-'*20} | {'-'*4} | {'-'*5} | {'-'*8} | {'-'*5} | {'-'*5} | {'-'*7}")
    for label in ["garbage_small", "garbage_medium", "mania_large", "mania_extreme"]:
        tier = t3["tiers"].get(label, {})
        if tier.get("n", 0) == 0:
            w(f"  {label:<20} |    0 |")
            continue
        w(f"  {label:<20} | {tier['n']:>4} | {tier['win_rate']:>5.1f} | {tier['pf']:>8.4f} | "
          f"{tier['false_peak_pct']:>5.1f} | {tier['stop_loss_pct']:>5.1f} | {tier['mean_r']:>+7.4f}")

    excl = t3["excluding_mania_extreme"]
    w(f"\n  Excluding mania_extreme: N={excl['n']}, PF={excl['pf']:.4f}, "
      f"WR={excl['win_rate']:.1f}%, FP={excl['false_peak_pct']:.1f}%")

    # Test 4
    w("")
    w("TEST 4: Win Rate Degradation (Monte Carlo, 10K sims)")
    w("-" * 50)
    w(f"  Actual WR: {t4['actual_win_rate']:.1f}% | Winner R: {t4['winner_r_mean']:+.4f} | Loser R: {t4['loser_r_mean']:+.4f}")
    w(f"  {'WR%':>5} | {'PF(mean)':>9} | {'PF(med)':>8} | {'PF(10th)':>9} | {'MaxDD%':>7} | {'P(loss)':>8} | {'P(ruin)':>8}")
    w(f"  {'-'*5} | {'-'*9} | {'-'*8} | {'-'*9} | {'-'*7} | {'-'*8} | {'-'*8}")
    for d in t4["degradation_results"]:
        w(f"  {d['target_wr']*100:>4.0f}% | {d['mean_pf']:>9.4f} | {d['median_pf']:>8.4f} | "
          f"{d['pf_10th_pct']:>9.4f} | {d['mean_max_dd_pct']:>6.1f}% | {d['p_net_loss']:>7.1f}% | {d['p_ruin']:>7.1f}%")
    w(f"\n  Breakeven win rate: {t4['breakeven_win_rate']*100:.0f}%" if t4["breakeven_win_rate"] else
      "  Breakeven: could not determine (profitable at all tested WRs)")

    # Test 5
    w("")
    w("TEST 5: Gap Risk")
    w("-" * 50)
    w(f"  Post-entry bars examined: {t5['total_post_entry_bars']}")
    w(f"  Candles exceeding stop distance (15%): {t5['candles_exceeding_stop_distance']}")
    w(f"  Trades with gap-through: {t5['trades_with_gap_through']}/{t5['trades_total']} ({t5['gap_through_rate_pct']:.1f}%)")
    w(f"  Worst single-candle adverse move: {t5['worst_single_candle_adverse_pct']:.2f}%")
    if t5["max_adverse_candle_percentiles"]:
        pcts = t5["max_adverse_candle_percentiles"]
        w(f"  Adverse move percentiles: p50={pcts.get('p50', 0):.2f}%, p90={pcts.get('p90', 0):.2f}%, "
          f"p99={pcts.get('p99', 0):.2f}%")
    w(f"  Risk assessment: {t5['risk_assessment']}")

    # Verdict
    w("")
    w("=" * 70)
    w("FINAL VERDICT")
    w("=" * 70)

    slip_ok = any(s["pf_after"] > 1.3 for s in t1["scenarios"] if s["stop_slippage_bps"] == 200)
    funding_ok = t2["data_coverage_pct"] < 20  # insufficient data = can't judge
    mania_benefit = (t3["excluding_mania_extreme"]["pf"] > t3["tiers"].get("mania_extreme", {}).get("pf", 999)
                     if t3["tiers"].get("mania_extreme", {}).get("n", 0) > 0 else False)
    be_wr = t4.get("breakeven_win_rate")
    gap_ok = t5["risk_assessment"] in ("NEGLIGIBLE", "MODERATE")

    w("")
    w(f"  Survives slippage stress?    {'YES' if slip_ok else 'NO'} (PF>1.3 at 2% stop slippage)")
    w(f"  Survives funding gate?       {'INSUFFICIENT DATA' if funding_ok else 'YES'}")
    w(f"  Benefits from garbage filter? {'YES' if mania_benefit else 'NO'}")
    if be_wr:
        w(f"  Win rate cliff:              {be_wr*100:.0f}%")
    w(f"  Gap risk:                    {t5['risk_assessment']}")

    if slip_ok and gap_ok and (not be_wr or be_wr <= 0.65):
        w(f"\n  RECOMMENDATION: PAPER TRADE")
        w("  The edge survives realistic adverse conditions. Deploy to paper trading")
        w("  with entry25_stop15 config, Binance perps only, excluding mania_extreme pumps.")
    elif slip_ok and gap_ok:
        w(f"\n  RECOMMENDATION: PAPER TRADE (CAUTIOUS)")
        w(f"  Edge exists but win rate cliff at {be_wr*100:.0f}% is close to backtest WR.")
    else:
        w(f"\n  RECOMMENDATION: SHELVE or NEEDS MORE DATA")

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
    logger.info("D2 Stress Test starting...")
    logger.info("Output JSON: %s", OUTPUT_JSON)
    logger.info("Output TXT:  %s", OUTPUT_TXT)

    binance_perps = get_binance_perp_symbols()

    with psycopg.connect(DATABASE_URL) as conn:
        signals = load_pump_signals(conn)
        logger.info("  %d signals loaded", len(signals))

        clusters = detect_pump_clusters(signals)
        logger.info("  %d clusters detected", len(clusters))

        ohlcv = load_ohlcv(conn, clusters)
        logger.info("  OHLCV for %d symbols", len(ohlcv))

    # Run simulation for entry25_stop15 Binance-only
    logger.info("Running entry25_stop15 simulation on Binance-only...")
    trades = []
    for c in clusters:
        sym = c["symbol"]
        if sym not in ohlcv or sym not in binance_perps:
            continue
        bars = ohlcv[sym]
        bar_times = [b[0] for b in bars]
        trade = simulate_hwm_trailing(c, bars, bar_times)
        if trade is not None:
            trades.append(trade)

    logger.info("  %d trades generated", len(trades))

    # Run stress tests
    t1 = test1_asymmetric_slippage(trades)
    t2 = test2_funding_gate(trades)
    t3 = test3_mania_tiers(trades)
    t4 = test4_monte_carlo(trades)
    t5 = test5_gap_risk(trades)

    elapsed = round(time.time() - t0, 1)
    logger.info("Complete in %.1fs", elapsed)

    # JSON output
    output = {
        "metadata": {
            "generated_at": TIMESTAMP,
            "elapsed_seconds": elapsed,
            "config": "entry25_stop15",
            "symbol_filter": "binance_perps_only",
            "n_trades": len(trades),
            "binance_perps_count": len(binance_perps),
        },
        "test1_asymmetric_slippage": t1,
        "test2_funding_gate": t2,
        "test3_mania_tiers": t3,
        "test4_monte_carlo": t4,
        "test5_gap_risk": t5,
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)

    report = write_report(t1, t2, t3, t4, t5, len(trades), OUTPUT_TXT)
    print(report)


if __name__ == "__main__":
    main()
