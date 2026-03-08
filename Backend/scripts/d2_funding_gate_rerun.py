#!/usr/bin/env python3
"""
D2 Funding Gate Re-Run & Final Calibration
=============================================
Task A: Funding gate sweep with true funding costs
Task B: Monte Carlo recalibration with funding-adjusted R
Task C: True verdict table (frictionless vs funding-adjusted)
Task D: Gated true set if gate helps

Uses backfilled Binance funding rates from d2_next_steps.

Output: scripts/output/d2_funding_final_{ts}.json + .txt

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d2_funding_gate_rerun.py
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
OUTPUT_JSON = os.path.join(OUTPUT_DIR, f"d2_funding_final_{TIMESTAMP}.json")
OUTPUT_TXT = os.path.join(OUTPUT_DIR, f"d2_funding_final_{TIMESTAMP}.txt")

# Locked config
CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3
ENTRY_THRESHOLD = 0.25
STOP_PCT = 0.15
TIME_STOP_HOURS = 72
MAX_WATCH_HOURS = 48
POSITION_SIZE_USD = 1000
TAKER_FEE_PCT = 0.0004
BASE_SLIPPAGE_PCT = 0.0010
ROUND_TRIP_FRICTION = 2 * (TAKER_FEE_PCT + BASE_SLIPPAGE_PCT)
MANIA_EXTREME_THRESHOLD = 40.0

MC_SIMULATIONS = 10000
MC_TRADE_COUNT = 56  # post mania gate

# Funding gate thresholds
NEGATIVE_THRESHOLDS = [-0.25, -0.50, -0.75, -1.00, -1.50, -2.00]
POSITIVE_THRESHOLDS = [0.25, 0.50, 1.00]


# ---------------------------------------------------------------------------
# Cluster / OHLCV / simulation (same as previous scripts)
# ---------------------------------------------------------------------------
def load_pump_signals(conn):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM social_bot_signals ORDER BY post_ts")
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
    return {
        "symbol": symbol,
        "cluster_start_ts": signals[0]["post_ts"],
        "cluster_end_ts": signals[-1]["post_ts"],
        "alert_count": len(signals),
        "max_pct_change": max(pct_changes) if pct_changes else None,
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
    except:
        return set()


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
    for j in range(entry_bar_idx + 1, min(exit_bar_idx + 1, len(bars))):
        bh = float(bars[j][2])
        bo = float(bars[j][1])
        if bh > entry_hwm:
            false_peak = True
        if bo > 0:
            adv = (bh - bo) / bo
            if adv > max_adverse_pct:
                max_adverse_pct = adv

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
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "hours_held": round(hours_held, 1),
        "n_funding_epochs": n_funding_epochs,
        "pnl_pct_raw": pnl_pct,
        "r_multiple": r_multiple,
        "risk_per_unit": risk,
        "false_peak": false_peak,
        "max_adverse_candle_pct": round(max_adverse_pct * 100, 4),
    }


# ---------------------------------------------------------------------------
# Backfill funding rates for each trade
# ---------------------------------------------------------------------------
def backfill_funding(trades):
    logger.info("Backfilling funding rates from Binance...")
    for idx, t in enumerate(trades):
        sym = t["symbol"]
        entry_ts = t["entry_ts"]
        exit_ts = t["exit_ts"]

        try:
            start_ms = int((entry_ts - timedelta(hours=8)).timestamp() * 1000)
            end_ms = int((exit_ts + timedelta(hours=8)).timestamp() * 1000)
            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": sym, "startTime": start_ms, "endTime": end_ms, "limit": 100},
                timeout=5,
            )
            if resp.status_code == 200:
                rates = resp.json()
                hold_rates = []
                entry_rate = None
                for r in rates:
                    ft = datetime.fromtimestamp(r["fundingTime"] / 1000, tz=timezone.utc)
                    fr = float(r["fundingRate"]) * 100  # to %
                    if entry_ts <= ft <= exit_ts:
                        hold_rates.append(fr)
                    # Find rate closest to entry (the "starting" rate at signal time)
                    if entry_rate is None or abs((ft - entry_ts).total_seconds()) < abs((entry_rate[0] - entry_ts).total_seconds()):
                        entry_rate = (ft, fr)

                t["funding_rates"] = hold_rates
                t["funding_count"] = len(hold_rates)
                t["total_funding_cost_pct"] = sum(abs(r) for r in hold_rates if r < 0)
                t["total_funding_income_pct"] = sum(r for r in hold_rates if r > 0)
                t["net_funding_pct"] = sum(hold_rates)  # negative = you paid, positive = you earned
                t["entry_funding_rate_pct"] = entry_rate[1] if entry_rate else None
                t["has_funding"] = True
            else:
                t["has_funding"] = False
                t["funding_rates"] = []
        except:
            t["has_funding"] = False
            t["funding_rates"] = []

        if (idx + 1) % 10 == 0:
            logger.info("  %d/%d funded", idx + 1, len(trades))
        time.sleep(0.1)

    funded = sum(1 for t in trades if t.get("has_funding"))
    logger.info("  %d/%d trades with funding data", funded, len(trades))
    return trades


# ---------------------------------------------------------------------------
# Compute funding-adjusted PF
# ---------------------------------------------------------------------------
def compute_funding_adjusted_pf(trades, label=""):
    """PF with true per-trade funding costs."""
    if not trades:
        return {"label": label, "n": 0}

    wins_usd = 0
    losses_usd = 0
    pnls = []

    # Median funding cost for trades without data
    funded_trades = [t for t in trades if t.get("has_funding") and t["funding_count"] > 0]
    if funded_trades:
        median_net_funding = float(np.median([t["net_funding_pct"] for t in funded_trades]))
    else:
        median_net_funding = -0.3  # fallback

    for t in trades:
        # Friction
        cost = ROUND_TRIP_FRICTION

        # True funding cost
        if t.get("has_funding") and t["funding_count"] > 0:
            # net_funding_pct is already signed: negative = you paid
            funding_cost = -t["net_funding_pct"] / 100  # convert to fraction, flip sign so cost is positive
        else:
            # Proxy: median cost * number of epochs
            funding_cost = -median_net_funding / 100 * t["n_funding_epochs"]

        total_cost = cost + funding_cost
        pnl = POSITION_SIZE_USD * (t["pnl_pct_raw"] - total_cost)
        pnls.append(pnl)

        if pnl > 0:
            wins_usd += pnl
        else:
            losses_usd += abs(pnl)

    n = len(trades)
    winners = sum(1 for p in pnls if p > 0)
    pf = wins_usd / losses_usd if losses_usd > 0 else float("inf")

    return {
        "label": label,
        "n": n,
        "win_rate": round(winners / n * 100, 1),
        "pf": round(pf, 4),
        "total_pnl_usd": round(sum(pnls), 2),
        "mean_pnl_usd": round(float(np.mean(pnls)), 2),
    }


def get_funding_adjusted_r(trades):
    """Get per-trade funding-adjusted R multiples."""
    funded = [t for t in trades if t.get("has_funding") and t["funding_count"] > 0]
    if funded:
        median_net = float(np.median([t["net_funding_pct"] for t in funded]))
    else:
        median_net = -0.3

    adjusted_rs = []
    for t in trades:
        if t.get("has_funding") and t["funding_count"] > 0:
            funding_drag = -t["net_funding_pct"] / 100  # positive = cost
        else:
            funding_drag = -median_net / 100 * t["n_funding_epochs"]

        friction = ROUND_TRIP_FRICTION
        total_cost = friction + funding_drag

        # Adjusted PnL %
        adj_pnl = t["pnl_pct_raw"] - total_cost
        # Adjusted R = adj_pnl / (risk as % of entry)
        risk_pct = t["risk_per_unit"] / t["entry_price"]
        adj_r = adj_pnl / risk_pct if risk_pct > 0 else 0

        adjusted_rs.append(adj_r)

    return adjusted_rs


# ---------------------------------------------------------------------------
# Task A: Funding Gate Sweep
# ---------------------------------------------------------------------------
def task_a_funding_gate(trades):
    logger.info("[TASK A] Funding gate sweep...")

    # Only trades with entry funding rate
    with_entry_rate = [t for t in trades if t.get("entry_funding_rate_pct") is not None]
    without_entry_rate = [t for t in trades if t.get("entry_funding_rate_pct") is None]
    logger.info("  %d trades have entry funding rate, %d do not",
                 len(with_entry_rate), len(without_entry_rate))

    results = {"negative_thresholds": [], "positive_thresholds": []}

    # Negative thresholds: reject if entry funding < threshold
    for thresh in NEGATIVE_THRESHOLDS:
        rejected = [t for t in with_entry_rate if t["entry_funding_rate_pct"] < thresh]
        remaining = [t for t in trades if t not in rejected]

        stats = compute_funding_adjusted_pf(remaining, f"gate<{thresh}%")

        # Analyze rejected trades
        rejected_adj_rs = get_funding_adjusted_r(rejected) if rejected else []
        remaining_adj_rs = get_funding_adjusted_r(remaining) if remaining else []

        rej_winners = sum(1 for r in rejected_adj_rs if r > 0)

        results["negative_thresholds"].append({
            "threshold_pct": thresh,
            "rejected": len(rejected),
            "remaining": stats["n"],
            "pf_funding_adjusted": stats["pf"],
            "wr_pct": stats["win_rate"],
            "total_pnl_usd": stats["total_pnl_usd"],
            "rejected_winners": rej_winners,
            "rejected_losers": len(rejected) - rej_winners,
            "mean_r_rejected": round(float(np.mean(rejected_adj_rs)), 4) if rejected_adj_rs else None,
            "mean_r_remaining": round(float(np.mean(remaining_adj_rs)), 4) if remaining_adj_rs else None,
        })

        logger.info("    Gate < %.2f%%: rej=%d, N=%d, PF=%.4f, rej winners=%d/%d",
                     thresh, len(rejected), stats["n"], stats["pf"],
                     rej_winners, len(rejected))

    # Positive thresholds: reject if entry funding > threshold (paid to short)
    for thresh in POSITIVE_THRESHOLDS:
        rejected = [t for t in with_entry_rate if t["entry_funding_rate_pct"] > thresh]
        remaining = [t for t in trades if t not in rejected]

        stats = compute_funding_adjusted_pf(remaining, f"gate>{thresh}%")

        rejected_adj_rs = get_funding_adjusted_r(rejected) if rejected else []

        results["positive_thresholds"].append({
            "threshold_pct": thresh,
            "rejected": len(rejected),
            "remaining": stats["n"],
            "pf_funding_adjusted": stats["pf"],
            "wr_pct": stats["win_rate"],
            "note": "Rejecting trades where you're PAID to short" if rejected else "No trades rejected",
        })

    # Find best negative threshold (highest PF improvement)
    baseline_pf = compute_funding_adjusted_pf(trades, "baseline")["pf"]
    results["baseline_pf"] = baseline_pf

    best = max(results["negative_thresholds"],
               key=lambda x: x["pf_funding_adjusted"] if x["remaining"] >= 30 else 0)
    results["best_negative_gate"] = {
        "threshold": best["threshold_pct"],
        "pf": best["pf_funding_adjusted"],
        "improvement": round(best["pf_funding_adjusted"] - baseline_pf, 4),
        "materially_better": best["pf_funding_adjusted"] > baseline_pf + 0.20,
    }

    return results


# ---------------------------------------------------------------------------
# Task B: Monte Carlo Recalibration
# ---------------------------------------------------------------------------
def task_b_monte_carlo(trades):
    logger.info("[TASK B] Monte Carlo recalibration with funding-adjusted R...")

    adj_rs = np.array(get_funding_adjusted_r(trades))
    winners_mask = adj_rs > 0
    winner_rs = adj_rs[winners_mask]
    loser_rs = adj_rs[~winners_mask]

    actual_wr = len(winner_rs) / len(adj_rs)
    logger.info("  Funding-adjusted: %d winners (R=%.4f±%.4f), %d losers (R=%.4f±%.4f), WR=%.1f%%",
                 len(winner_rs), np.mean(winner_rs), np.std(winner_rs),
                 len(loser_rs), np.mean(loser_rs), np.std(loser_rs),
                 actual_wr * 100)

    degraded_wrs = [0.80, 0.75, 0.70, 0.65, 0.60, 0.55]
    rng = np.random.default_rng(42)
    results = []

    mean_risk_pct = np.mean([t["risk_per_unit"] / t["entry_price"] for t in trades])

    for target_wr in degraded_wrs:
        pfs = []
        max_dds = []
        net_losses = 0
        ruins = 0

        for _ in range(MC_SIMULATIONS):
            n_wins = int(MC_TRADE_COUNT * target_wr)
            n_losses = MC_TRADE_COUNT - n_wins

            sim_w = rng.choice(winner_rs, size=n_wins, replace=True)
            sim_l = rng.choice(loser_rs, size=n_losses, replace=True)
            all_rs = np.concatenate([sim_w, sim_l])
            rng.shuffle(all_rs)

            # R already includes all costs — just convert to USD
            pnl_usd = all_rs * mean_risk_pct * POSITION_SIZE_USD

            wins_sum = pnl_usd[pnl_usd > 0].sum()
            losses_sum = abs(pnl_usd[pnl_usd <= 0].sum())
            pf = wins_sum / losses_sum if losses_sum > 0 else 100
            pfs.append(min(pf, 100))

            cumulative = np.cumsum(pnl_usd)
            initial_capital = POSITION_SIZE_USD * MC_TRADE_COUNT * 0.1
            running_max = np.maximum.accumulate(cumulative)
            dd = (running_max - cumulative) / initial_capital * 100
            max_dds.append(dd.max())

            if cumulative[-1] < 0:
                net_losses += 1
            if dd.max() > 50:
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
        logger.info("    WR=%.0f%%: PF=%.4f (med=%.4f), P(loss)=%.1f%%, P(ruin)=%.1f%%",
                     target_wr * 100, results[-1]["mean_pf"], results[-1]["median_pf"],
                     results[-1]["p_net_loss"], results[-1]["p_ruin"])

    # Breakeven WR
    breakeven_wr = None
    for wr_test in np.arange(0.80, 0.30, -0.01):
        n_wins = int(MC_TRADE_COUNT * wr_test)
        n_losses = MC_TRADE_COUNT - n_wins
        losses_count = 0
        for _ in range(1000):
            sim_w = rng.choice(winner_rs, size=n_wins, replace=True)
            sim_l = rng.choice(loser_rs, size=n_losses, replace=True)
            all_rs = np.concatenate([sim_w, sim_l])
            pnl = all_rs * mean_risk_pct * POSITION_SIZE_USD
            if pnl.sum() < 0:
                losses_count += 1
        if losses_count / 1000 > 0.50:
            breakeven_wr = round(wr_test + 0.01, 2)
            break

    return {
        "actual_wr": round(actual_wr * 100, 1),
        "n_winners": int(winners_mask.sum()),
        "n_losers": int((~winners_mask).sum()),
        "winner_r_mean": round(float(np.mean(winner_rs)), 4),
        "winner_r_std": round(float(np.std(winner_rs)), 4),
        "loser_r_mean": round(float(np.mean(loser_rs)), 4),
        "loser_r_std": round(float(np.std(loser_rs)), 4),
        "degradation": results,
        "breakeven_wr": breakeven_wr,
    }


# ---------------------------------------------------------------------------
# Task C: Verdict Table
# ---------------------------------------------------------------------------
def task_c_verdict(trades, mc_frictionless, mc_funded):
    """Build side-by-side comparison."""
    # Frictionless stats
    raw_rs = np.array([t["r_multiple"] for t in trades])
    raw_winners = raw_rs[raw_rs > 0]
    raw_losers = raw_rs[raw_rs <= 0]
    raw_wr = len(raw_winners) / len(raw_rs) * 100

    wins_f = sum(POSITION_SIZE_USD * t["pnl_pct_raw"] for t in trades if t["pnl_pct_raw"] > 0)
    losses_f = abs(sum(POSITION_SIZE_USD * t["pnl_pct_raw"] for t in trades if t["pnl_pct_raw"] <= 0))
    pf_frictionless = wins_f / losses_f if losses_f > 0 else float("inf")

    # Funded stats
    adj_rs = np.array(get_funding_adjusted_r(trades))
    adj_winners = adj_rs[adj_rs > 0]
    adj_losers = adj_rs[adj_rs <= 0]
    funded_stats = compute_funding_adjusted_pf(trades, "funded")

    # Find P(ruin) at specific WR degradation levels
    def find_p_ruin(mc_results, offset):
        actual = mc_results["actual_wr"]
        target = actual - offset
        for d in mc_results["degradation"]:
            if abs(d["target_wr"] * 100 - target) < 3:
                return d["p_ruin"]
        return None

    return {
        "frictionless": {
            "n": len(trades),
            "pf": round(pf_frictionless, 4),
            "wr": round(raw_wr, 1),
            "breakeven_wr": mc_frictionless["breakeven_wr"],
            "wr_cushion": round(raw_wr - (mc_frictionless["breakeven_wr"] or 0) * 100, 1) if mc_frictionless["breakeven_wr"] else None,
            "mean_r_winners": round(float(np.mean(raw_winners)), 4),
            "mean_r_losers": round(float(np.mean(raw_losers)), 4) if len(raw_losers) > 0 else 0,
            "p_ruin_minus10": find_p_ruin(mc_frictionless, 10),
            "p_ruin_minus15": find_p_ruin(mc_frictionless, 15),
        },
        "funding_adjusted": {
            "n": funded_stats["n"],
            "pf": funded_stats["pf"],
            "wr": funded_stats["win_rate"],
            "breakeven_wr": mc_funded["breakeven_wr"],
            "wr_cushion": round(funded_stats["win_rate"] - (mc_funded["breakeven_wr"] or 0) * 100, 1) if mc_funded["breakeven_wr"] else None,
            "mean_r_winners": round(float(np.mean(adj_winners)), 4),
            "mean_r_losers": round(float(np.mean(adj_losers)), 4) if len(adj_losers) > 0 else 0,
            "p_ruin_minus10": find_p_ruin(mc_funded, 10),
            "p_ruin_minus15": find_p_ruin(mc_funded, 15),
        },
    }


# ---------------------------------------------------------------------------
# Task B (frictionless) for comparison
# ---------------------------------------------------------------------------
def task_b_frictionless(trades):
    """Monte Carlo with frictionless R for comparison table."""
    logger.info("[TASK B-ref] Frictionless Monte Carlo for comparison...")
    raw_rs = np.array([t["r_multiple"] for t in trades])
    winner_rs = raw_rs[raw_rs > 0]
    loser_rs = raw_rs[raw_rs <= 0]
    actual_wr = len(winner_rs) / len(raw_rs)
    rng = np.random.default_rng(42)
    mean_risk_pct = np.mean([t["risk_per_unit"] / t["entry_price"] for t in trades])
    cost_per_trade = ROUND_TRIP_FRICTION

    degraded_wrs = [0.80, 0.75, 0.70, 0.65, 0.60, 0.55]
    results = []

    for target_wr in degraded_wrs:
        pfs = []
        max_dds = []
        net_losses = 0
        ruins = 0
        for _ in range(MC_SIMULATIONS):
            n_wins = int(MC_TRADE_COUNT * target_wr)
            n_losses = MC_TRADE_COUNT - n_wins
            sim_w = rng.choice(winner_rs, size=n_wins, replace=True)
            sim_l = rng.choice(loser_rs, size=n_losses, replace=True)
            all_rs = np.concatenate([sim_w, sim_l])
            rng.shuffle(all_rs)
            pnl_usd = (all_rs * mean_risk_pct - cost_per_trade) * POSITION_SIZE_USD
            wins_sum = pnl_usd[pnl_usd > 0].sum()
            losses_sum = abs(pnl_usd[pnl_usd <= 0].sum())
            pf = wins_sum / losses_sum if losses_sum > 0 else 100
            pfs.append(min(pf, 100))
            cumulative = np.cumsum(pnl_usd)
            initial_capital = POSITION_SIZE_USD * MC_TRADE_COUNT * 0.1
            running_max = np.maximum.accumulate(cumulative)
            dd = (running_max - cumulative) / initial_capital * 100
            max_dds.append(dd.max())
            if cumulative[-1] < 0:
                net_losses += 1
            if dd.max() > 50:
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

    # Breakeven
    breakeven_wr = None
    for wr_test in np.arange(0.80, 0.30, -0.01):
        n_wins = int(MC_TRADE_COUNT * wr_test)
        n_losses = MC_TRADE_COUNT - n_wins
        losses_count = 0
        for _ in range(1000):
            sim_w = rng.choice(winner_rs, size=n_wins, replace=True)
            sim_l = rng.choice(loser_rs, size=n_losses, replace=True)
            all_rs = np.concatenate([sim_w, sim_l])
            pnl = (all_rs * mean_risk_pct - cost_per_trade) * POSITION_SIZE_USD
            if pnl.sum() < 0:
                losses_count += 1
        if losses_count / 1000 > 0.50:
            breakeven_wr = round(wr_test + 0.01, 2)
            break

    return {
        "actual_wr": round(actual_wr * 100, 1),
        "n_winners": int((raw_rs > 0).sum()),
        "n_losers": int((raw_rs <= 0).sum()),
        "winner_r_mean": round(float(np.mean(winner_rs)), 4),
        "winner_r_std": round(float(np.std(winner_rs)), 4),
        "loser_r_mean": round(float(np.mean(loser_rs)), 4),
        "loser_r_std": round(float(np.std(loser_rs)), 4),
        "degradation": results,
        "breakeven_wr": breakeven_wr,
    }


# ---------------------------------------------------------------------------
# Task D: Gated true set
# ---------------------------------------------------------------------------
def task_d_gated_set(trades, best_gate_threshold, mc_func):
    """If gate helps, apply and re-run MC."""
    if best_gate_threshold is None:
        return {"note": "Funding gate not beneficial — skipping Task D"}

    logger.info("[TASK D] Gated true set at threshold %.2f%%...", best_gate_threshold)

    with_rate = [t for t in trades if t.get("entry_funding_rate_pct") is not None]
    gated = [t for t in trades if t.get("entry_funding_rate_pct") is None
             or t["entry_funding_rate_pct"] >= best_gate_threshold]

    stats = compute_funding_adjusted_pf(gated, f"gated_{best_gate_threshold}")
    mc = mc_func(gated)

    return {
        "gate_threshold_pct": best_gate_threshold,
        "n_before": len(trades),
        "n_after": len(gated),
        "stats": stats,
        "monte_carlo": mc,
    }


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------
def write_report(ta, tb_funded, tb_fric, tc, td, output_path):
    lines = []
    w = lines.append

    w("=" * 75)
    w("D2 FUNDING GATE RE-RUN & FINAL CALIBRATION")
    w("=" * 75)
    w("All PF values are FUNDING-ADJUSTED unless labelled 'frictionless'")

    # Task A
    w("")
    w("TASK A: Funding Gate Sweep")
    w("-" * 60)
    w(f"  Baseline funding-adjusted PF: {ta['baseline_pf']:.4f}")
    w("")
    w("  Negative gates (reject if entry funding < threshold):")
    w(f"  {'Threshold':>10} | {'Rej':>4} | {'N':>4} | {'PF':>8} | {'WR%':>5} | {'Rej W/L':>8} | {'PnL($)':>10}")
    w(f"  {'-'*10} | {'-'*4} | {'-'*4} | {'-'*8} | {'-'*5} | {'-'*8} | {'-'*10}")
    for s in ta["negative_thresholds"]:
        w(f"  {s['threshold_pct']:>9.2f}% | {s['rejected']:>4} | {s['remaining']:>4} | "
          f"{s['pf_funding_adjusted']:>8.4f} | {s['wr_pct']:>5.1f} | "
          f"{s['rejected_winners']:>3}/{s['rejected_losers']:<3} | {s['total_pnl_usd']:>10.2f}")

    bg = ta["best_negative_gate"]
    w(f"\n  Best negative gate: {bg['threshold']}% → PF={bg['pf']:.4f} (Δ={bg['improvement']:+.4f})")
    w(f"  Materially better (>+0.20)? {'YES' if bg['materially_better'] else 'NO'}")

    w("\n  Positive gates (reject if funding > threshold — paid to short):")
    for s in ta["positive_thresholds"]:
        w(f"    >{s['threshold_pct']:.2f}%: rejected={s['rejected']}, N={s['remaining']}, PF={s['pf_funding_adjusted']:.4f}")

    # Task B
    w("")
    w("TASK B: Monte Carlo (Funding-Adjusted R)")
    w("-" * 60)
    w(f"  WR: {tb_funded['actual_wr']:.1f}% | Winners R: {tb_funded['winner_r_mean']:+.4f}±{tb_funded['winner_r_std']:.4f} | "
      f"Losers R: {tb_funded['loser_r_mean']:+.4f}±{tb_funded['loser_r_std']:.4f}")
    w(f"  {'WR%':>5} | {'PF(mean)':>9} | {'PF(med)':>8} | {'PF(10th)':>9} | {'MaxDD%':>7} | {'P(loss)':>8} | {'P(ruin)':>8}")
    w(f"  {'-'*5} | {'-'*9} | {'-'*8} | {'-'*9} | {'-'*7} | {'-'*8} | {'-'*8}")
    for d in tb_funded["degradation"]:
        w(f"  {d['target_wr']*100:>4.0f}% | {d['mean_pf']:>9.4f} | {d['median_pf']:>8.4f} | "
          f"{d['pf_10th_pct']:>9.4f} | {d['mean_max_dd_pct']:>6.1f}% | {d['p_net_loss']:>7.1f}% | {d['p_ruin']:>7.1f}%")
    be = tb_funded["breakeven_wr"]
    w(f"\n  Breakeven WR: {be*100:.0f}%" if be else "  Breakeven: could not determine")

    # Task C
    w("")
    w("TASK C: True Verdict Table")
    w("-" * 60)
    f = tc["frictionless"]
    a = tc["funding_adjusted"]
    w(f"  {'Metric':<25} | {'Frictionless':>14} | {'Funding-Adjusted':>16}")
    w(f"  {'-'*25} | {'-'*14} | {'-'*16}")
    w(f"  {'N':<25} | {f['n']:>14} | {a['n']:>16}")
    w(f"  {'PF':<25} | {f['pf']:>14.4f} | {a['pf']:>16.4f}")
    w(f"  {'WR%':<25} | {f['wr']:>13.1f}% | {a['wr']:>15.1f}%")

    be_f = f"  {f['breakeven_wr']*100:.0f}%" if f['breakeven_wr'] else "N/A"
    be_a = f"  {a['breakeven_wr']*100:.0f}%" if a['breakeven_wr'] else "N/A"
    w(f"  {'Breakeven WR':<25} | {be_f:>14} | {be_a:>16}")

    cush_f = f"{f['wr_cushion']:.1f}pp" if f['wr_cushion'] else "N/A"
    cush_a = f"{a['wr_cushion']:.1f}pp" if a['wr_cushion'] else "N/A"
    w(f"  {'WR Cushion':<25} | {cush_f:>14} | {cush_a:>16}")

    w(f"  {'Mean R (winners)':<25} | {f['mean_r_winners']:>+14.4f} | {a['mean_r_winners']:>+16.4f}")
    w(f"  {'Mean R (losers)':<25} | {f['mean_r_losers']:>+14.4f} | {a['mean_r_losers']:>+16.4f}")

    pr10_f = f"{f['p_ruin_minus10']:.1f}%" if f['p_ruin_minus10'] is not None else "N/A"
    pr10_a = f"{a['p_ruin_minus10']:.1f}%" if a['p_ruin_minus10'] is not None else "N/A"
    w(f"  {'P(ruin) at -10% WR':<25} | {pr10_f:>14} | {pr10_a:>16}")

    pr15_f = f"{f['p_ruin_minus15']:.1f}%" if f['p_ruin_minus15'] is not None else "N/A"
    pr15_a = f"{a['p_ruin_minus15']:.1f}%" if a['p_ruin_minus15'] is not None else "N/A"
    w(f"  {'P(ruin) at -15% WR':<25} | {pr15_f:>14} | {pr15_a:>16}")

    # Task D
    w("")
    w("TASK D: Gated True Set")
    w("-" * 60)
    if "note" in td:
        w(f"  {td['note']}")
    else:
        w(f"  Gate: reject entry funding < {td['gate_threshold_pct']}%")
        w(f"  N: {td['n_before']} → {td['n_after']}")
        s = td["stats"]
        w(f"  PF: {s['pf']:.4f}, WR: {s['win_rate']:.1f}%")
        mc = td["monte_carlo"]
        w(f"  Breakeven WR: {mc['breakeven_wr']*100:.0f}%" if mc.get("breakeven_wr") else "  Breakeven: N/A")

    # Final recommendation
    w("")
    w("=" * 75)
    w("FINAL RECOMMENDATION")
    w("=" * 75)

    pf_ok = a["pf"] > 1.9
    cushion_ok = a["wr_cushion"] is not None and a["wr_cushion"] > 10
    ruin_ok = a["p_ruin_minus10"] is not None and a["p_ruin_minus10"] < 5

    w(f"\n  Funding-adjusted PF > 1.9?     {'PASS' if pf_ok else 'FAIL'} ({a['pf']:.4f})")
    w(f"  Breakeven WR cushion > 10pp?   {'PASS' if cushion_ok else 'FAIL'} ({a.get('wr_cushion', 'N/A')})")
    w(f"  P(ruin) < 5% at -10% WR?      {'PASS' if ruin_ok else 'FAIL'} ({a.get('p_ruin_minus10', 'N/A')})")

    if pf_ok and cushion_ok and ruin_ok:
        w("\n  >>> PAPER TRADE <<<")
        w("  Config: entry25_stop15, Binance perps, mania gate (>40%), p75 position sizing")
    elif pf_ok and (cushion_ok or ruin_ok):
        w("\n  >>> PAPER TRADE (CAUTIOUS) <<<")
        w("  Edge exists but one criterion marginal. Start with minimum size.")
    else:
        w("\n  >>> SHELVE or NEEDS MORE DATA <<<")

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
    logger.info("D2 Funding Gate Re-Run starting...")
    logger.info("Output: %s / %s", OUTPUT_JSON, OUTPUT_TXT)

    binance_perps = get_binance_perps()

    with psycopg.connect(DATABASE_URL) as conn:
        signals = load_pump_signals(conn)
        clusters = detect_pump_clusters(signals)
        ohlcv = load_ohlcv(conn, clusters)

    # Simulate entry25_stop15 Binance-only + mania gate
    logger.info("Simulating entry25_stop15 Binance-only + mania gate...")
    trades = []
    for c in clusters:
        sym = c["symbol"]
        if sym not in ohlcv or sym not in binance_perps:
            continue
        if c.get("max_pct_change") and c["max_pct_change"] > MANIA_EXTREME_THRESHOLD:
            continue
        bars = ohlcv[sym]
        bar_times = [b[0] for b in bars]
        trade = simulate_hwm_trailing(c, bars, bar_times)
        if trade:
            trades.append(trade)
    logger.info("  %d trades (post mania gate, Binance-only)", len(trades))

    # Backfill funding
    trades = backfill_funding(trades)

    # Task A
    ta = task_a_funding_gate(trades)

    # Task B (funding-adjusted)
    tb_funded = task_b_monte_carlo(trades)

    # Task B (frictionless reference)
    tb_fric = task_b_frictionless(trades)

    # Task C
    tc = task_c_verdict(trades, tb_fric, tb_funded)

    # Task D
    bg = ta["best_negative_gate"]
    if bg["materially_better"]:
        td = task_d_gated_set(trades, bg["threshold"], task_b_monte_carlo)
    else:
        td = {"note": "Funding gate not beneficial — skipping Task D"}

    elapsed = round(time.time() - t0, 1)
    logger.info("Complete in %.1fs", elapsed)

    # JSON
    output = {
        "metadata": {"generated_at": TIMESTAMP, "elapsed_seconds": elapsed,
                      "config": "entry25_stop15 Binance-only + mania gate", "n_trades": len(trades)},
        "task_a_funding_gate": ta,
        "task_b_mc_funded": tb_funded,
        "task_b_mc_frictionless": tb_fric,
        "task_c_verdict_table": tc,
        "task_d_gated_set": td,
    }
    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)

    report = write_report(ta, tb_funded, tb_fric, tc, td, OUTPUT_TXT)
    print(report)


if __name__ == "__main__":
    main()
