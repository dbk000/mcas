#!/usr/bin/env python3
"""
D2 "Fade the Casino" — Viability Analysis
==========================================
Phase 1: Honest delayed-entry backtest on pump clusters (bar-by-bar walk-forward)
Phase 2: Mania intensity as a predictor of dump severity
Phase 3: Mechanical feasibility (Binance perp shortability, capacity)

Uses 1h OHLCV from production DB. No look-ahead — all entries are delayed.

Output: scripts/output/d2_viability_{ts}.json + .txt

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d2_viability_analysis.py
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
from psycopg.rows import dict_row
from scipy.stats import spearmanr, mannwhitneyu

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
OUTPUT_JSON = os.path.join(OUTPUT_DIR, f"d2_viability_{TIMESTAMP}.json")
OUTPUT_TXT = os.path.join(OUTPUT_DIR, f"d2_viability_{TIMESTAMP}.txt")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3
ENTRY_DELAYS = [0.10, 0.15, 0.20, 0.25, 0.30]
STOP_ABOVE_PEAK_PCT = 0.05  # 5% above peak
TIME_STOP_HOURS = 72
POSITION_SIZE_USD = 1000

# Slippage + fees for short (taker entry + taker exit on perps)
SLIPPAGE_BPS = 5
FEE_BPS = 4
ROUND_TRIP_COST_PCT = 2 * (SLIPPAGE_BPS + FEE_BPS) / 10000  # 0.18%


# ---------------------------------------------------------------------------
# Cluster detection (mirrors social_pump_cluster_analysis.py)
# ---------------------------------------------------------------------------
def load_pump_signals(conn):
    """Load pump signals from social_bot_signals table."""
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
    """Gap-based clustering: same symbol, <=4h gap, >=3 alerts."""
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
    vol_changes = [float(s["vol_change_pct"]) for s in signals if s.get("vol_change_pct") is not None]
    peak_sig = max(signals, key=lambda s: float(s.get("pct_change") or 0))

    # Mania features from individual alerts
    oi_changes = [float(s["oi_change_pct"]) for s in signals if s.get("oi_change_pct") is not None]
    funding_rates = [float(s["funding_rate"]) for s in signals if s.get("funding_rate") is not None]
    bid_vols = [float(s["bid_vol"]) for s in signals if s.get("bid_vol") is not None]
    ask_vols = [float(s["ask_vol"]) for s in signals if s.get("ask_vol") is not None]
    spreads = [float(s["spread_pct"]) for s in signals if s.get("spread_pct") is not None]

    duration_hours = max(
        (signals[-1]["post_ts"] - signals[0]["post_ts"]).total_seconds() / 3600,
        0.1  # avoid div by zero
    )

    # Alert timing pattern (acceleration)
    alert_timestamps = [s["post_ts"] for s in signals]
    alert_gaps = []
    for i in range(len(alert_timestamps) - 1):
        gap = (alert_timestamps[i + 1] - alert_timestamps[i]).total_seconds() / 60  # minutes
        alert_gaps.append(gap)

    # Bid-ask imbalance
    total_bids = sum(bid_vols) if bid_vols else 0
    total_asks = sum(ask_vols) if ask_vols else 0
    bid_ask_imbalance = ((total_bids - total_asks) / (total_bids + total_asks)
                         if (total_bids + total_asks) > 0 else None)

    return {
        "symbol": symbol,
        "cluster_start_ts": signals[0]["post_ts"],
        "cluster_end_ts": signals[-1]["post_ts"],
        "alert_count": len(signals),
        "exchanges_seen": sorted(exchanges),
        "exchange_count": len(exchanges),
        "duration_hours": round(duration_hours, 2),
        # Price intensity
        "max_pct_change": max(pct_changes) if pct_changes else None,
        "cumulative_pct_change": sum(pct_changes) if pct_changes else None,
        "max_vol_change_pct": max(vol_changes) if vol_changes else None,
        # Mania features
        "alert_velocity": round(len(signals) / duration_hours, 2),
        "oi_change_max": max(oi_changes) if oi_changes else None,
        "funding_rate_at_peak": float(peak_sig["funding_rate"]) if peak_sig.get("funding_rate") else None,
        "bid_ask_imbalance": round(bid_ask_imbalance, 4) if bid_ask_imbalance is not None else None,
        "mean_spread_pct": round(np.mean(spreads), 4) if spreads else None,
        # Alert timing
        "alert_gaps_minutes": alert_gaps,
        "alert_acceleration": (
            alert_gaps[-1] < alert_gaps[0] if len(alert_gaps) >= 2 else None
        ),
        # Raw signals for Phase 2
        "_signals": signals,
    }


# ---------------------------------------------------------------------------
# OHLCV loading + peak/pre-pump price discovery
# ---------------------------------------------------------------------------
def enrich_clusters_with_ohlcv(conn, clusters):
    """Load 1h OHLCV and compute pre-pump, peak, and post prices for each cluster."""
    logger.info("Loading 1h OHLCV for %d clusters...", len(clusters))

    symbols = sorted(set(c["symbol"] for c in clusters))
    earliest = min(c["cluster_start_ts"] for c in clusters) - timedelta(hours=2)
    latest = max(c["cluster_start_ts"] for c in clusters) + timedelta(hours=80)

    # Bulk load OHLCV
    ohlcv_by_symbol = {}
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
                ohlcv_by_symbol[sym] = rows

    logger.info("  OHLCV loaded for %d/%d symbols", len(ohlcv_by_symbol), len(symbols))

    valid = 0
    for cluster in clusters:
        sym = cluster["symbol"]
        bars = ohlcv_by_symbol.get(sym)
        if not bars:
            cluster["has_ohlcv"] = False
            continue

        bar_times = [b[0] for b in bars]
        start_ts = cluster["cluster_start_ts"]

        # Pre-pump price: close of bar immediately before cluster start
        pre_idx = bisect.bisect_left(bar_times, start_ts) - 1
        if pre_idx < 0:
            cluster["has_ohlcv"] = False
            continue

        pre_pump_price = float(bars[pre_idx][4])

        # Peak price: max high in 24h after cluster start
        start_bar_idx = bisect.bisect_left(bar_times, start_ts)
        end_24h_idx = bisect.bisect_right(bar_times, start_ts + timedelta(hours=24))

        window_24h = bars[start_bar_idx:end_24h_idx]
        if not window_24h:
            cluster["has_ohlcv"] = False
            continue

        peak_price = max(float(b[2]) for b in window_24h)
        peak_bar_idx_local = max(range(len(window_24h)), key=lambda i: float(window_24h[i][2]))
        peak_bar_ts = window_24h[peak_bar_idx_local][0]
        hours_to_peak = (peak_bar_ts - start_ts).total_seconds() / 3600

        peak_move_pct = (peak_price - pre_pump_price) / pre_pump_price * 100

        # Post prices
        def get_close_near(target_ts):
            idx = bisect.bisect_left(bar_times, target_ts)
            if idx >= len(bars):
                idx = len(bars) - 1
            return float(bars[idx][4])

        post_24h = get_close_near(start_ts + timedelta(hours=24))
        post_48h = get_close_near(start_ts + timedelta(hours=48))
        post_72h = get_close_near(start_ts + timedelta(hours=72))

        cluster["has_ohlcv"] = True
        cluster["pre_pump_price"] = pre_pump_price
        cluster["peak_price"] = peak_price
        cluster["peak_bar_ts"] = peak_bar_ts
        cluster["hours_to_peak"] = hours_to_peak
        cluster["peak_move_pct"] = peak_move_pct
        cluster["post_24h_price"] = post_24h
        cluster["post_48h_price"] = post_48h
        cluster["post_72h_price"] = post_72h

        # Short-seller perspective returns
        cluster["peak_to_24h_return"] = (post_24h - peak_price) / peak_price
        cluster["peak_to_48h_return"] = (post_48h - peak_price) / peak_price
        cluster["peak_to_72h_return"] = (post_72h - peak_price) / peak_price

        # Dump severity (positive = bigger dump from peak)
        cluster["dump_severity"] = (peak_price - post_72h) / peak_price

        # Store bars for walk-forward simulation
        # Only bars from cluster start onward (need for entry detection + exit walk)
        sim_start_idx = bisect.bisect_left(bar_times, start_ts)
        sim_end_idx = bisect.bisect_right(bar_times, start_ts + timedelta(hours=80))
        cluster["_ohlcv_bars"] = bars[sim_start_idx:sim_end_idx]
        cluster["_ohlcv_bar_times"] = bar_times[sim_start_idx:sim_end_idx]

        valid += 1

    logger.info("  %d/%d clusters have valid OHLCV", valid, len(clusters))
    return clusters


# ---------------------------------------------------------------------------
# Phase 1: Delayed Entry Backtest (bar-by-bar walk-forward)
# ---------------------------------------------------------------------------
def simulate_delayed_entry(cluster, delay_pct):
    """
    Simulate a short trade with delayed entry.

    Entry: after price has dropped delay_pct from peak
    Stop: 5% above peak
    TP: pre-pump price (full round-trip)
    Time stop: 72h from cluster start

    Returns trade dict or None if entry not possible.
    """
    if not cluster.get("has_ohlcv"):
        return None

    peak_price = cluster["peak_price"]
    pre_pump_price = cluster["pre_pump_price"]
    peak_bar_ts = cluster["peak_bar_ts"]
    bars = cluster["_ohlcv_bars"]
    bar_times = cluster["_ohlcv_bar_times"]
    cluster_start = cluster["cluster_start_ts"]

    # Entry price: peak * (1 - delay)
    entry_price = peak_price * (1 - delay_pct)
    stop_loss = peak_price * (1 + STOP_ABOVE_PEAK_PCT)
    tp_price = pre_pump_price

    # Entry must be above TP (otherwise the trade makes no sense — already below pre-pump)
    if entry_price <= tp_price:
        return None

    # Risk/reward
    risk = stop_loss - entry_price  # what you lose per unit if stopped
    reward = entry_price - tp_price  # what you gain per unit if TP hit

    if risk <= 0:
        return None

    time_stop_ts = cluster_start + timedelta(hours=TIME_STOP_HOURS)

    # Walk forward through bars AFTER peak to find entry
    peak_bar_idx = bisect.bisect_left(bar_times, peak_bar_ts)
    entry_bar_idx = None
    entry_ts = None

    for i in range(peak_bar_idx, len(bars)):
        bar_low = float(bars[i][3])
        bar_ts = bar_times[i]

        # Price must have dropped to entry_price (bar_low <= entry_price)
        if bar_low <= entry_price:
            entry_bar_idx = i
            entry_ts = bar_ts
            break

        # If we've passed the time stop without entering, no trade
        if bar_ts >= time_stop_ts:
            return None

    if entry_bar_idx is None:
        return None

    # Walk forward from entry bar to find exit
    exit_reason = None
    exit_price = None
    exit_ts = None
    bars_held = 0

    for i in range(entry_bar_idx + 1, len(bars)):
        bar_high = float(bars[i][2])
        bar_low = float(bars[i][3])
        bar_close = float(bars[i][4])
        bar_ts = bar_times[i]
        bars_held += 1

        # Check stop loss first (worst case: bar_high >= stop_loss)
        if bar_high >= stop_loss:
            exit_reason = "STOP_LOSS"
            exit_price = stop_loss
            exit_ts = bar_ts
            break

        # Check TP (bar_low <= tp_price)
        if bar_low <= tp_price:
            exit_reason = "TP_HIT"
            exit_price = tp_price
            exit_ts = bar_ts
            break

        # Check time stop
        if bar_ts >= time_stop_ts:
            exit_reason = "TIME_STOP"
            exit_price = bar_close
            exit_ts = bar_ts
            break

    # If we ran out of bars without hitting any exit
    if exit_reason is None:
        if bars_held == 0:
            return None
        exit_reason = "TIME_STOP"
        last_bar = bars[min(entry_bar_idx + bars_held, len(bars) - 1)]
        exit_price = float(last_bar[4])
        exit_ts = bar_times[min(entry_bar_idx + bars_held, len(bars) - 1)]

    # PnL (short: profit = entry - exit)
    pnl_pct = (entry_price - exit_price) / entry_price
    pnl_pct_after_costs = pnl_pct - ROUND_TRIP_COST_PCT
    pnl_usd = POSITION_SIZE_USD * pnl_pct_after_costs
    r_multiple = (entry_price - exit_price) / risk

    return {
        "symbol": cluster["symbol"],
        "cluster_start": cluster_start,
        "delay_pct": delay_pct,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "tp_price": tp_price,
        "exit_reason": exit_reason,
        "exit_price": exit_price,
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "bars_held": bars_held,
        "pnl_pct": round(pnl_pct * 100, 4),
        "pnl_pct_after_costs": round(pnl_pct_after_costs * 100, 4),
        "pnl_usd": round(pnl_usd, 2),
        "r_multiple": round(r_multiple, 4),
        "risk_per_unit": risk,
        "reward_per_unit": reward,
        "peak_price": cluster["peak_price"],
        "pre_pump_price": cluster["pre_pump_price"],
        "peak_move_pct": cluster["peak_move_pct"],
    }


def run_phase1(clusters):
    """Run delayed-entry backtest across all clusters and delay levels."""
    logger.info("[PHASE 1] Running delayed-entry backtest...")
    valid_clusters = [c for c in clusters if c.get("has_ohlcv")]
    logger.info("  %d clusters with valid OHLCV", len(valid_clusters))

    results = {}
    for delay in ENTRY_DELAYS:
        delay_key = f"{int(delay*100)}pct"
        trades = []
        entry_not_possible = 0

        for cluster in valid_clusters:
            trade = simulate_delayed_entry(cluster, delay)
            if trade is not None:
                trades.append(trade)
            else:
                entry_not_possible += 1

        # Compute statistics
        if trades:
            winners = [t for t in trades if t["pnl_usd"] > 0]
            losers = [t for t in trades if t["pnl_usd"] <= 0]

            total_wins = sum(t["pnl_usd"] for t in winners)
            total_losses = abs(sum(t["pnl_usd"] for t in losers))

            tp_hits = [t for t in trades if t["exit_reason"] == "TP_HIT"]
            sl_hits = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
            ts_hits = [t for t in trades if t["exit_reason"] == "TIME_STOP"]

            win_r = [t["r_multiple"] for t in winners] if winners else [0]
            loss_r = [t["r_multiple"] for t in losers] if losers else [0]

            # Max consecutive losses
            max_consec_loss = 0
            current_streak = 0
            for t in sorted(trades, key=lambda x: x["entry_ts"]):
                if t["pnl_usd"] <= 0:
                    current_streak += 1
                    max_consec_loss = max(max_consec_loss, current_streak)
                else:
                    current_streak = 0

            stats = {
                "delay_pct": int(delay * 100),
                "clusters_with_ohlcv": len(valid_clusters),
                "entry_possible": len(trades),
                "entry_not_possible": entry_not_possible,
                "trades_taken": len(trades),
                "tp_hit": len(tp_hits),
                "tp_hit_pct": round(len(tp_hits) / len(trades) * 100, 1),
                "stop_loss": len(sl_hits),
                "stop_loss_pct": round(len(sl_hits) / len(trades) * 100, 1),
                "time_stop": len(ts_hits),
                "time_stop_pct": round(len(ts_hits) / len(trades) * 100, 1),
                "win_rate": round(len(winners) / len(trades) * 100, 1),
                "mean_r_winners": round(float(np.mean(win_r)), 4),
                "mean_r_losers": round(float(np.mean(loss_r)), 4),
                "median_r_all": round(float(np.median([t["r_multiple"] for t in trades])), 4),
                "profit_factor": round(total_wins / total_losses, 4) if total_losses > 0 else float("inf"),
                "total_pnl_usd": round(sum(t["pnl_usd"] for t in trades), 2),
                "mean_pnl_usd": round(float(np.mean([t["pnl_usd"] for t in trades])), 2),
                "max_consecutive_losses": max_consec_loss,
                "mean_bars_held": round(float(np.mean([t["bars_held"] for t in trades])), 1),
                # Time-stop PnL breakdown
                "time_stop_mean_pnl": round(float(np.mean([t["pnl_usd"] for t in ts_hits])), 2) if ts_hits else 0,
                "time_stop_win_pct": round(
                    sum(1 for t in ts_hits if t["pnl_usd"] > 0) / len(ts_hits) * 100, 1
                ) if ts_hits else 0,
            }
        else:
            stats = {
                "delay_pct": int(delay * 100),
                "clusters_with_ohlcv": len(valid_clusters),
                "entry_possible": 0,
                "entry_not_possible": entry_not_possible,
                "trades_taken": 0,
                "note": "No valid trades at this delay level",
            }

        results[delay_key] = stats
        logger.info("  Delay %d%%: %d trades, PF=%.4f, WR=%.1f%%",
                     int(delay * 100),
                     stats.get("trades_taken", 0),
                     stats.get("profit_factor", 0),
                     stats.get("win_rate", 0))

    # Viability assessment
    viable_delays = []
    for delay_key, stats in results.items():
        delay_val = stats.get("delay_pct", 0)
        if (delay_val >= 15
            and stats.get("trades_taken", 0) >= 50
            and stats.get("profit_factor", 0) > 1.3
            and stats.get("stop_loss_pct", 100) < 50
            and (stats.get("win_rate", 0) > 40
                 or (stats.get("mean_r_winners", 0) > 3 * abs(stats.get("mean_r_losers", -1))))):
            viable_delays.append(delay_key)

    results["_viability"] = {
        "viable_delays": viable_delays,
        "is_viable": len(viable_delays) > 0,
    }

    return results


# ---------------------------------------------------------------------------
# Phase 1.1: Short-seller perspective returns distribution
# ---------------------------------------------------------------------------
def compute_short_seller_returns(clusters):
    """Compute peak-to-Xh returns from short seller's perspective."""
    valid = [c for c in clusters if c.get("has_ohlcv") and c.get("peak_price")]

    horizons = {
        "peak_to_24h": "peak_to_24h_return",
        "peak_to_48h": "peak_to_48h_return",
        "peak_to_72h": "peak_to_72h_return",
    }

    result = {"total_with_ohlcv": len(valid)}

    for label, key in horizons.items():
        returns = [c[key] for c in valid if key in c]
        if not returns:
            continue

        # Negative return = profitable short
        drops = [-r for r in returns]  # convert to "drop from peak" (positive = drop)

        result[label] = {
            "n": len(returns),
            "mean_drop_pct": round(float(np.mean(drops)) * 100, 2),
            "median_drop_pct": round(float(np.median(drops)) * 100, 2),
            "pct_drop_gt_10": round(sum(1 for d in drops if d > 0.10) / len(drops) * 100, 1),
            "pct_drop_gt_20": round(sum(1 for d in drops if d > 0.20) / len(drops) * 100, 1),
            "pct_drop_gt_30": round(sum(1 for d in drops if d > 0.30) / len(drops) * 100, 1),
            "pct_drop_gt_50": round(sum(1 for d in drops if d > 0.50) / len(drops) * 100, 1),
            "pct_price_above_peak": round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1),
        }

    return result


# ---------------------------------------------------------------------------
# Phase 2: Mania Intensity Analysis
# ---------------------------------------------------------------------------
def run_phase2(clusters):
    """Correlate mania intensity features with dump severity."""
    logger.info("[PHASE 2] Mania intensity analysis...")

    valid = [c for c in clusters if c.get("has_ohlcv") and c.get("dump_severity") is not None]
    logger.info("  %d clusters with dump severity data", len(valid))

    if len(valid) < 10:
        return {"error": f"Insufficient data ({len(valid)} clusters)", "n": len(valid)}

    dump_severities = np.array([c["dump_severity"] for c in valid])

    # Mania features to test
    feature_defs = {
        "alert_count": lambda c: c["alert_count"],
        "alert_velocity": lambda c: c["alert_velocity"],
        "exchange_count": lambda c: c["exchange_count"],
        "max_pct_change": lambda c: c.get("max_pct_change"),
        "cumulative_pct_change": lambda c: c.get("cumulative_pct_change"),
        "max_vol_change_pct": lambda c: c.get("max_vol_change_pct"),
        "duration_hours": lambda c: c["duration_hours"],
        "peak_move_pct": lambda c: c.get("peak_move_pct"),
        "bid_ask_imbalance": lambda c: c.get("bid_ask_imbalance"),
        "mean_spread_pct": lambda c: c.get("mean_spread_pct"),
    }

    # 2.1 Univariate correlations
    correlations = {}
    for feat_name, feat_fn in feature_defs.items():
        values = []
        ds_matched = []
        for i, c in enumerate(valid):
            v = feat_fn(c)
            if v is not None:
                values.append(float(v))
                ds_matched.append(dump_severities[i])

        if len(values) < 10:
            correlations[feat_name] = {"n": len(values), "note": "insufficient data"}
            continue

        rho, pval = spearmanr(values, ds_matched)
        correlations[feat_name] = {
            "n": len(values),
            "spearman_rho": round(float(rho), 4),
            "p_value": round(float(pval), 6),
            "significant_005": pval < 0.05,
            "direction": "higher mania → bigger dump" if rho > 0 else "higher mania → smaller dump",
        }

    # Sort by absolute correlation
    sorted_corr = sorted(
        [(k, v) for k, v in correlations.items() if "spearman_rho" in v],
        key=lambda x: abs(x[1]["spearman_rho"]),
        reverse=True
    )
    best_predictor = sorted_corr[0] if sorted_corr else None

    # 2.2 Threshold analysis: split by median on each feature
    threshold_analysis = {}
    for feat_name, feat_fn in feature_defs.items():
        values = [(feat_fn(c), c) for c in valid if feat_fn(c) is not None]
        if len(values) < 10:
            continue

        feat_vals = [v[0] for v in values]
        median_val = float(np.median(feat_vals))

        high_mania = [c for v, c in values if float(v) > median_val]
        low_mania = [c for v, c in values if float(v) <= median_val]

        def group_stats(group):
            if not group:
                return {"n": 0}
            ds = [c["dump_severity"] for c in group]
            dump_rate = sum(1 for d in ds if d > 0.10) / len(ds) * 100  # >10% dump from peak
            return {
                "n": len(group),
                "mean_dump_severity": round(float(np.mean(ds)), 4),
                "median_dump_severity": round(float(np.median(ds)), 4),
                "dump_rate_gt10pct": round(dump_rate, 1),
            }

        threshold_analysis[feat_name] = {
            "median_threshold": round(float(median_val), 4),
            "high_mania": group_stats(high_mania),
            "low_mania": group_stats(low_mania),
        }

    # 2.3 Composite mania filter test
    # Define "high mania" as: alert_velocity > median AND exchange_count >= 2 AND max_pct_change > 15%
    high_mania_clusters = [
        c for c in valid
        if c["alert_velocity"] > 2.0
        and c["exchange_count"] >= 2
        and (c.get("max_pct_change") or 0) > 15
    ]
    moderate_mania = [c for c in valid if c not in high_mania_clusters]

    composite_filter = {
        "criteria": "alert_velocity > 2/h AND exchange_count >= 2 AND max_pct_change > 15%",
        "high_mania_count": len(high_mania_clusters),
        "moderate_count": len(moderate_mania),
    }

    if high_mania_clusters:
        hm_ds = [c["dump_severity"] for c in high_mania_clusters]
        composite_filter["high_mania_mean_dump"] = round(float(np.mean(hm_ds)), 4)
        composite_filter["high_mania_dump_rate_gt10pct"] = round(
            sum(1 for d in hm_ds if d > 0.10) / len(hm_ds) * 100, 1
        )
    if moderate_mania:
        mm_ds = [c["dump_severity"] for c in moderate_mania]
        composite_filter["moderate_mean_dump"] = round(float(np.mean(mm_ds)), 4)
        composite_filter["moderate_dump_rate_gt10pct"] = round(
            sum(1 for d in mm_ds if d > 0.10) / len(mm_ds) * 100, 1
        )

    # 2.4 Alert acceleration analysis
    accel_clusters = [c for c in valid if c.get("alert_acceleration") is not None]
    if len(accel_clusters) >= 10:
        accelerating = [c for c in accel_clusters if c["alert_acceleration"]]
        decelerating = [c for c in accel_clusters if not c["alert_acceleration"]]

        accel_ds = [c["dump_severity"] for c in accelerating] if accelerating else []
        decel_ds = [c["dump_severity"] for c in decelerating] if decelerating else []

        acceleration_analysis = {
            "accelerating_count": len(accelerating),
            "decelerating_count": len(decelerating),
        }
        if accel_ds:
            acceleration_analysis["accel_mean_dump"] = round(float(np.mean(accel_ds)), 4)
            acceleration_analysis["accel_hours_to_peak"] = round(
                float(np.mean([c["hours_to_peak"] for c in accelerating if c.get("hours_to_peak")])), 2
            )
        if decel_ds:
            acceleration_analysis["decel_mean_dump"] = round(float(np.mean(decel_ds)), 4)
            acceleration_analysis["decel_hours_to_peak"] = round(
                float(np.mean([c["hours_to_peak"] for c in decelerating if c.get("hours_to_peak")])), 2
            )
        if len(accel_ds) >= 5 and len(decel_ds) >= 5:
            stat, pval = mannwhitneyu(accel_ds, decel_ds, alternative="two-sided")
            acceleration_analysis["mann_whitney_p"] = round(float(pval), 6)
            acceleration_analysis["significant_005"] = pval < 0.05
    else:
        acceleration_analysis = {"note": "insufficient data", "n": len(accel_clusters)}

    return {
        "n_clusters_analyzed": len(valid),
        "correlations": dict(sorted_corr) if sorted_corr else correlations,
        "best_predictor": {
            "feature": best_predictor[0],
            **best_predictor[1],
        } if best_predictor else None,
        "threshold_analysis": threshold_analysis,
        "composite_filter": composite_filter,
        "acceleration_analysis": acceleration_analysis,
    }


# ---------------------------------------------------------------------------
# Phase 3: Mechanical Feasibility
# ---------------------------------------------------------------------------
def run_phase3(conn, clusters):
    """Check Binance perp shortability and capacity."""
    logger.info("[PHASE 3] Mechanical feasibility...")

    # Get Binance perp symbols from OHLCV (symbols with exchange containing 'binance')
    # Better approach: check which cluster symbols exist in our ohlcv table
    # (our universe is Binance spot, but perps are what matter for shorting)

    valid = [c for c in clusters if c.get("has_ohlcv")]
    cluster_symbols = set(c["symbol"] for c in valid)

    # Check which symbols have Binance exchange in bot signals
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT symbol
            FROM social_bot_signals
            WHERE exchange = 'Binance'
        """)
        binance_symbols = set(row[0] for row in cur.fetchall())

    # Also check which are in our known universe (we only have Binance OHLCV)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT symbol
            FROM ohlcv
            WHERE timeframe = '1h'
              AND symbol = ANY(%s::text[])
        """, (list(cluster_symbols),))
        in_universe = set(row[0] for row in cur.fetchall())

    # Symbols seen on Binance in bot alerts = likely have perps
    binance_cluster_symbols = cluster_symbols & binance_symbols

    # Capacity analysis
    valid_sorted = sorted(valid, key=lambda c: c["cluster_start_ts"])

    # Monthly distribution
    monthly = defaultdict(int)
    for c in valid_sorted:
        month_key = c["cluster_start_ts"].strftime("%Y-%m")
        monthly[month_key] += 1

    months = sorted(monthly.keys())
    n_months = len(months) if months else 1

    # Max concurrent positions (72h hold)
    concurrent_max = 0
    for i, c in enumerate(valid_sorted):
        concurrent = 1
        for j in range(i + 1, len(valid_sorted)):
            if (valid_sorted[j]["cluster_start_ts"] - c["cluster_start_ts"]).total_seconds() / 3600 <= 72:
                concurrent += 1
            else:
                break
        concurrent_max = max(concurrent_max, concurrent)

    # Exchange distribution of clusters
    exchange_dist = defaultdict(int)
    for c in valid:
        for ex in c.get("exchanges_seen", []):
            exchange_dist[ex] += 1

    # Funding rate during pumps (from signals that have funding data)
    funding_vals = [c["funding_rate_at_peak"] for c in valid if c.get("funding_rate_at_peak") is not None]

    return {
        "total_valid_clusters": len(valid),
        "symbols_in_clusters": len(cluster_symbols),
        "symbols_on_binance": len(binance_cluster_symbols),
        "binance_pct": round(len(binance_cluster_symbols) / len(cluster_symbols) * 100, 1) if cluster_symbols else 0,
        "symbols_in_universe": len(in_universe),
        "in_universe_pct": round(len(in_universe) / len(cluster_symbols) * 100, 1) if cluster_symbols else 0,
        "binance_only_symbols": sorted(binance_cluster_symbols),
        "non_binance_symbols": sorted(cluster_symbols - binance_cluster_symbols),
        "exchange_distribution": dict(exchange_dist),
        "capacity": {
            "total_months": n_months,
            "monthly_distribution": dict(monthly),
            "mean_clusters_per_month": round(len(valid) / n_months, 1),
            "max_concurrent_72h": concurrent_max,
        },
        "funding_during_pumps": {
            "sample_size": len(funding_vals),
            "mean_funding_pct": round(float(np.mean(funding_vals)), 4) if funding_vals else None,
            "note": "negative = shorts squeezed (you pay), positive = you get paid",
        },
    }


# ---------------------------------------------------------------------------
# Phase 2.3: Mania-filtered backtest
# ---------------------------------------------------------------------------
def run_mania_filtered_backtest(clusters, phase2_results):
    """Re-run Phase 1 backtest on high-mania clusters only."""
    logger.info("[PHASE 2.3] Mania-filtered backtest...")

    valid = [c for c in clusters if c.get("has_ohlcv")]

    # Use composite filter criteria from phase 2
    high_mania = [
        c for c in valid
        if c["alert_velocity"] > 2.0
        and c["exchange_count"] >= 2
        and (c.get("max_pct_change") or 0) > 15
    ]

    if len(high_mania) < 5:
        # Try relaxed criteria
        logger.info("  Only %d high-mania clusters, trying relaxed criteria...", len(high_mania))
        high_mania = [
            c for c in valid
            if (c.get("max_pct_change") or 0) > 10
            and c["alert_count"] >= 4
        ]
        logger.info("  Relaxed criteria: %d clusters", len(high_mania))

    if len(high_mania) < 5:
        return {"error": f"Insufficient high-mania clusters ({len(high_mania)})"}

    # Run the same delayed entry simulation on filtered set
    results = {}
    for delay in ENTRY_DELAYS:
        delay_key = f"{int(delay*100)}pct"
        trades = []

        for cluster in high_mania:
            trade = simulate_delayed_entry(cluster, delay)
            if trade is not None:
                trades.append(trade)

        if trades:
            winners = [t for t in trades if t["pnl_usd"] > 0]
            losers = [t for t in trades if t["pnl_usd"] <= 0]
            total_wins = sum(t["pnl_usd"] for t in winners)
            total_losses = abs(sum(t["pnl_usd"] for t in losers))

            results[delay_key] = {
                "delay_pct": int(delay * 100),
                "trades_taken": len(trades),
                "win_rate": round(len(winners) / len(trades) * 100, 1),
                "profit_factor": round(total_wins / total_losses, 4) if total_losses > 0 else float("inf"),
                "total_pnl_usd": round(sum(t["pnl_usd"] for t in trades), 2),
                "stop_loss_pct": round(
                    sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS") / len(trades) * 100, 1
                ),
            }
        else:
            results[delay_key] = {"trades_taken": 0, "delay_pct": int(delay * 100)}

    results["_filter_used"] = {
        "n_clusters": len(high_mania),
        "criteria": "max_pct_change > 10% AND alert_count >= 4 (relaxed)"
                    if any(c["alert_velocity"] <= 2.0 for c in high_mania)
                    else "alert_velocity > 2/h AND exchange_count >= 2 AND max_pct_change > 15%",
    }

    return results


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------
def write_text_report(phase1, short_returns, phase2, phase2_filtered, phase3, output_path):
    """Write human-readable report."""
    lines = []
    w = lines.append

    w("=" * 80)
    w("D2 'FADE THE CASINO' — VIABILITY ANALYSIS")
    w("=" * 80)
    w("")

    # Short-seller returns
    w("PEAK-TO-Xh RETURNS (Short Seller Perspective)")
    w("-" * 50)
    w(f"Clusters with OHLCV: {short_returns['total_with_ohlcv']}")
    for horizon in ["peak_to_24h", "peak_to_48h", "peak_to_72h"]:
        if horizon in short_returns:
            h = short_returns[horizon]
            w(f"\n  {horizon.replace('_', ' ').upper()}:")
            w(f"    Mean drop from peak:  {h['mean_drop_pct']:+.1f}%")
            w(f"    Median drop from peak: {h['median_drop_pct']:+.1f}%")
            w(f"    >10% drop: {h['pct_drop_gt_10']:.1f}%")
            w(f"    >20% drop: {h['pct_drop_gt_20']:.1f}%")
            w(f"    >30% drop: {h['pct_drop_gt_30']:.1f}%")
            w(f"    >50% drop: {h['pct_drop_gt_50']:.1f}%")
            w(f"    Price above peak (squeezed): {h['pct_price_above_peak']:.1f}%")

    w("")
    w("=" * 80)
    w("PHASE 1: DELAYED ENTRY BACKTEST")
    w("=" * 80)
    w(f"Entry: short after price drops X% from peak")
    w(f"Stop: 5% above peak | TP: pre-pump price | Time stop: 72h")
    w(f"Position: ${POSITION_SIZE_USD} | Costs: {ROUND_TRIP_COST_PCT*100:.2f}% round-trip")
    w("")

    header = f"{'Delay':>6} | {'Trades':>6} | {'WR%':>6} | {'PF':>8} | {'TP%':>6} | {'SL%':>6} | {'TS%':>6} | {'PnL($)':>10} | {'MaxLoss':>7}"
    w(header)
    w("-" * len(header))

    for delay_key in sorted(k for k in phase1 if not k.startswith("_")):
        s = phase1[delay_key]
        if s.get("trades_taken", 0) == 0:
            w(f"  {s.get('delay_pct', '?'):>3}%  |      0 | No valid trades")
            continue
        w(f"  {s['delay_pct']:>3}%  | {s['trades_taken']:>5} | {s['win_rate']:>5.1f} | {s['profit_factor']:>7.4f} | "
          f"{s['tp_hit_pct']:>5.1f} | {s['stop_loss_pct']:>5.1f} | {s['time_stop_pct']:>5.1f} | "
          f"{s['total_pnl_usd']:>9.2f} | {s['max_consecutive_losses']:>6}")

    viability = phase1.get("_viability", {})
    w("")
    if viability.get("is_viable"):
        w(f"VIABLE at delays: {', '.join(viability['viable_delays'])}")
    else:
        w("NOT VIABLE at any delay level (criteria: PF>1.3, WR>40% or R-ratio>3:1, N>=50, SL%<50%)")

    w("")
    w("=" * 80)
    w("PHASE 2: MANIA INTENSITY")
    w("=" * 80)

    if "error" in phase2:
        w(f"  {phase2['error']}")
    else:
        w(f"Clusters analyzed: {phase2['n_clusters_analyzed']}")
        w("")
        w("Univariate Correlations (Spearman rank vs dump severity):")
        w(f"  {'Feature':<25} | {'rho':>8} | {'p-value':>10} | {'Sig?':>5} | {'Direction'}")
        w("  " + "-" * 85)
        for feat_name, corr in phase2.get("correlations", {}).items():
            if isinstance(corr, dict) and "spearman_rho" in corr:
                sig = "**" if corr["significant_005"] else ""
                w(f"  {feat_name:<25} | {corr['spearman_rho']:>+7.4f} | {corr['p_value']:>10.6f} | {sig:>5} | {corr['direction']}")

        bp = phase2.get("best_predictor")
        if bp:
            w(f"\nBest predictor: {bp['feature']} (rho={bp['spearman_rho']:+.4f}, p={bp['p_value']:.6f})")

        cf = phase2.get("composite_filter", {})
        w(f"\nComposite Filter: {cf.get('criteria', 'N/A')}")
        w(f"  High mania: {cf.get('high_mania_count', 0)} clusters")
        if cf.get("high_mania_dump_rate_gt10pct") is not None:
            w(f"  High mania dump rate (>10%): {cf['high_mania_dump_rate_gt10pct']:.1f}%")
        if cf.get("moderate_dump_rate_gt10pct") is not None:
            w(f"  Moderate dump rate (>10%): {cf['moderate_dump_rate_gt10pct']:.1f}%")

        aa = phase2.get("acceleration_analysis", {})
        w(f"\nAlert Acceleration Analysis:")
        if "note" in aa:
            w(f"  {aa['note']}")
        else:
            w(f"  Accelerating: {aa.get('accelerating_count', 0)} clusters, "
              f"mean dump: {aa.get('accel_mean_dump', 'N/A')}")
            w(f"  Decelerating: {aa.get('decelerating_count', 0)} clusters, "
              f"mean dump: {aa.get('decel_mean_dump', 'N/A')}")
            if "mann_whitney_p" in aa:
                sig = " **" if aa.get("significant_005") else ""
                w(f"  Mann-Whitney p={aa['mann_whitney_p']:.6f}{sig}")

    # Mania-filtered backtest
    w("")
    w("Mania-Filtered Backtest:")
    if "error" in phase2_filtered:
        w(f"  {phase2_filtered['error']}")
    else:
        filt = phase2_filtered.get("_filter_used", {})
        w(f"  Filter: {filt.get('criteria', 'N/A')} ({filt.get('n_clusters', 0)} clusters)")
        w(f"  {'Delay':>6} | {'Trades':>6} | {'WR%':>6} | {'PF':>8} | {'SL%':>6} | {'PnL($)':>10}")
        for delay_key in sorted(k for k in phase2_filtered if not k.startswith("_")):
            s = phase2_filtered[delay_key]
            if s.get("trades_taken", 0) == 0:
                w(f"    {s.get('delay_pct', '?'):>3}%  |      0 |")
                continue
            w(f"    {s['delay_pct']:>3}%  | {s['trades_taken']:>5} | {s['win_rate']:>5.1f} | "
              f"{s['profit_factor']:>7.4f} | {s['stop_loss_pct']:>5.1f} | {s['total_pnl_usd']:>9.2f}")

    w("")
    w("=" * 80)
    w("PHASE 3: MECHANICAL FEASIBILITY")
    w("=" * 80)
    w(f"Cluster symbols: {phase3['symbols_in_clusters']}")
    w(f"  Seen on Binance (in bot alerts): {phase3['symbols_on_binance']} ({phase3['binance_pct']:.1f}%)")
    w(f"  In MCAS universe (have OHLCV):   {phase3['symbols_in_universe']} ({phase3['in_universe_pct']:.1f}%)")

    w(f"\nExchange Distribution (in cluster alerts):")
    for ex, cnt in sorted(phase3.get("exchange_distribution", {}).items(), key=lambda x: -x[1]):
        w(f"  {ex:<15}: {cnt}")

    cap = phase3.get("capacity", {})
    w(f"\nCapacity:")
    w(f"  Data period: {cap.get('total_months', 0)} months")
    w(f"  Mean clusters/month: {cap.get('mean_clusters_per_month', 0):.1f}")
    w(f"  Max concurrent positions (72h window): {cap.get('max_concurrent_72h', 0)}")

    fund = phase3.get("funding_during_pumps", {})
    w(f"\nFunding Rate During Pumps:")
    w(f"  Sample size: {fund.get('sample_size', 0)}")
    if fund.get("mean_funding_pct") is not None:
        w(f"  Mean funding: {fund['mean_funding_pct']:.4f}%")
        w(f"  ({fund.get('note', '')})")

    # Verdict
    w("")
    w("=" * 80)
    w("VERDICT")
    w("=" * 80)

    viable = viability.get("is_viable", False)
    binance_pct = phase3.get("binance_pct", 0)

    issues = []
    if not viable:
        issues.append("Phase 1: No delay level meets viability criteria (PF>1.3, WR>40%, N>=50, SL%<50%)")
    if binance_pct < 30:
        issues.append(f"Phase 3: Only {binance_pct:.0f}% of clusters on Binance — most are non-shortable microcaps")
    if phase3.get("capacity", {}).get("mean_clusters_per_month", 0) < 5:
        issues.append(f"Phase 3: Only {cap.get('mean_clusters_per_month', 0):.1f} clusters/month — too infrequent")

    if not issues:
        w("D2 is VIABLE — proceed to strategy implementation")
    elif len(issues) <= 1 and viable:
        w("D2 is CONDITIONALLY VIABLE — address issues below")
    else:
        w("D2 is NOT VIABLE")

    for issue in issues:
        w(f"  - {issue}")

    w(f"\nOutput: {OUTPUT_JSON}")

    report = "\n".join(lines)
    with open(output_path, "w") as f:
        f.write(report)

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    logger.info("D2 Viability Analysis starting...")
    logger.info("Output JSON: %s", OUTPUT_JSON)
    logger.info("Output TXT:  %s", OUTPUT_TXT)

    with psycopg.connect(DATABASE_URL) as conn:
        # Load signals and detect clusters
        logger.info("Loading pump signals from social_bot_signals...")
        signals = load_pump_signals(conn)
        logger.info("  %d signals loaded", len(signals))

        pump_count = sum(1 for s in signals if s["signal_type"] == "pump")
        logger.info("  %d pump signals", pump_count)

        logger.info("Detecting pump clusters...")
        clusters = detect_pump_clusters(signals)
        logger.info("  %d clusters detected", len(clusters))

        # Enrich with OHLCV
        clusters = enrich_clusters_with_ohlcv(conn, clusters)

        # Phase 1.1: Short-seller returns
        logger.info("[PHASE 1.1] Short-seller perspective returns...")
        short_returns = compute_short_seller_returns(clusters)

        # Phase 1: Delayed entry backtest
        phase1 = run_phase1(clusters)

        # Phase 2: Mania intensity
        phase2 = run_phase2(clusters)

        # Phase 2.3: Mania-filtered backtest
        phase2_filtered = run_mania_filtered_backtest(clusters, phase2)

        # Phase 3: Feasibility
        phase3 = run_phase3(conn, clusters)

    elapsed = round(time.time() - t0, 1)
    logger.info("Analysis complete in %.1fs", elapsed)

    # Build JSON output (strip internal fields)
    def clean_cluster(c):
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in c.items()
                if not k.startswith("_")}

    output = {
        "metadata": {
            "generated_at": TIMESTAMP,
            "elapsed_seconds": elapsed,
            "description": "D2 Fade the Casino — Viability Analysis",
            "cost_model": {
                "slippage_bps": SLIPPAGE_BPS,
                "fee_bps": FEE_BPS,
                "round_trip_pct": ROUND_TRIP_COST_PCT * 100,
                "position_size_usd": POSITION_SIZE_USD,
            },
        },
        "short_seller_returns": short_returns,
        "phase1_delayed_entry": phase1,
        "phase2_mania_intensity": phase2,
        "phase2_mania_filtered_backtest": phase2_filtered,
        "phase3_feasibility": phase3,
        "cluster_details": [clean_cluster(c) for c in clusters if c.get("has_ohlcv")],
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info("JSON output: %s", OUTPUT_JSON)

    # Write text report
    report = write_text_report(phase1, short_returns, phase2, phase2_filtered, phase3, OUTPUT_TXT)
    print(report)
    logger.info("TXT output: %s", OUTPUT_TXT)


if __name__ == "__main__":
    main()
