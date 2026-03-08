#!/usr/bin/env python3
"""
D2 Fear & Greed Regime Overlay
===============================
Tests hypothesis: does D2 performance vary by market regime?

Pulls FNG history from Alternative.me API, maps to the 56 D2 trades,
computes funding-adjusted PF per regime bucket, and tests gate thresholds.

Output: scripts/output/d2_fng_overlay_{ts}.json + .txt

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/d2_fng_overlay.py
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
OUTPUT_JSON = os.path.join(OUTPUT_DIR, f"d2_fng_overlay_{TIMESTAMP}.json")
OUTPUT_TXT = os.path.join(OUTPUT_DIR, f"d2_fng_overlay_{TIMESTAMP}.txt")

# ---------------------------------------------------------------------------
# Locked config (identical to d2_next_steps.py / d2_funding_gate_rerun.py)
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
ROUND_TRIP_FRICTION = 2 * (TAKER_FEE_PCT + BASE_SLIPPAGE_PCT)

# Mania gate from d2_next_steps
MANIA_EXTREME_THRESHOLD = 40.0

# FNG regime buckets
FNG_BUCKETS = [
    ("Extreme Fear", 0, 24),
    ("Fear", 25, 49),
    ("Neutral", 50, 54),
    ("Greed", 55, 74),
    ("Extreme Greed", 75, 100),
]

# Gate thresholds to test
FNG_GATE_THRESHOLDS = [
    ("Reject FNG > 75", lambda v: v <= 75),
    ("Reject FNG > 70", lambda v: v <= 70),
    ("Reject FNG > 60", lambda v: v <= 60),
    ("Only FNG < 50", lambda v: v < 50),
]


# ---------------------------------------------------------------------------
# Cluster detection + simulation (reused from d2_next_steps.py)
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
                                   pre_pump_price, i - entry_bar_idx)
            if bar_low <= tp_price:
                return _make_trade(cluster, "TP_HIT", tp_price, entry_price,
                                   entry_ts, bar_ts, entry_hwm, tp_price, stop_price,
                                   pre_pump_price, i - entry_bar_idx)
            if bar_ts >= time_stop_ts:
                return _make_trade(cluster, "TIME_STOP", bar_close, entry_price,
                                   entry_ts, bar_ts, entry_hwm, tp_price, stop_price,
                                   pre_pump_price, i - entry_bar_idx)

    if state == "IN_TRADE":
        li = len(bars) - 1
        return _make_trade(cluster, "TIME_STOP", float(bars[li][4]),
                           entry_price, entry_ts, bar_times[li],
                           entry_hwm, tp_price, stop_price, pre_pump_price,
                           li - entry_bar_idx)
    return None


def _make_trade(cluster, exit_reason, exit_price, entry_price, entry_ts,
                exit_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                bars_held):
    risk = stop_price - entry_price
    if risk <= 0:
        risk = 0.001
    pnl_pct = (entry_price - exit_price) / entry_price
    r_multiple = (entry_price - exit_price) / risk
    hours_held = (exit_ts - entry_ts).total_seconds() / 3600
    n_funding_epochs = int(hours_held / 8)

    return {
        "symbol": cluster["symbol"],
        "cluster_start_ts": cluster["cluster_start_ts"],
        "max_pct_change": cluster.get("max_pct_change"),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "hours_held": round(hours_held, 1),
        "n_funding_epochs": n_funding_epochs,
        "pnl_pct_raw": pnl_pct,
        "r_multiple": r_multiple,
    }


# ---------------------------------------------------------------------------
# Funding-adjusted PF computation
# ---------------------------------------------------------------------------
def compute_funding_adjusted_pf(trades, label=""):
    """Compute PF using actual backfilled funding rates where available,
    falling back to pessimistic assumed rate."""
    if not trades:
        return {"label": label, "n": 0}

    # Try to get actual funding from Binance for each trade
    wins_usd = 0
    losses_usd = 0
    pnl_list = []

    for t in trades:
        # Use actual funding if we backfilled it, otherwise assume base rate
        actual_funding = t.get("actual_funding_cost_pct")
        if actual_funding is not None:
            funding_cost = abs(actual_funding) / 100
        else:
            funding_cost = abs(-0.003) * t["n_funding_epochs"]  # base: -0.3%/epoch

        total_cost = ROUND_TRIP_FRICTION + funding_cost
        pnl = POSITION_SIZE_USD * (t["pnl_pct_raw"] - total_cost)
        pnl_list.append(pnl)

        if pnl > 0:
            wins_usd += pnl
        else:
            losses_usd += abs(pnl)

    n = len(trades)
    n_winners = sum(1 for p in pnl_list if p > 0)
    pf = wins_usd / losses_usd if losses_usd > 0 else float("inf")

    return {
        "label": label,
        "n": n,
        "win_rate": round(n_winners / n * 100, 1) if n > 0 else 0,
        "pf": round(pf, 4),
        "total_pnl_usd": round(wins_usd - losses_usd, 2),
        "mean_r": round(float(np.mean([t["r_multiple"] for t in trades])), 4),
    }


# ---------------------------------------------------------------------------
# Fetch FNG history
# ---------------------------------------------------------------------------
def fetch_fng_history():
    """Fetch full Fear & Greed history from Alternative.me API."""
    logger.info("Fetching Fear & Greed Index history...")

    # API caps at ~1000 entries per call. Paginate by date ranges if needed.
    all_data = {}

    # First try with limit=0 (should return all)
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/",
            params={"limit": 0, "format": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        entries = resp.json().get("data", [])
        for entry in entries:
            ts = int(entry["timestamp"])
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            all_data[dt] = {
                "value": int(entry["value"]),
                "classification": entry["value_classification"],
            }
        logger.info("  Fetched %d FNG data points (limit=0)", len(all_data))
    except Exception as e:
        logger.error("FNG API error: %s", e)

    # If we got fewer than expected, try larger limit
    if len(all_data) < 2000:
        try:
            resp = requests.get(
                "https://api.alternative.me/fng/",
                params={"limit": 3000, "format": "json"},
                timeout=15,
            )
            resp.raise_for_status()
            entries = resp.json().get("data", [])
            for entry in entries:
                ts = int(entry["timestamp"])
                dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                if dt not in all_data:
                    all_data[dt] = {
                        "value": int(entry["value"]),
                        "classification": entry["value_classification"],
                    }
            logger.info("  Total FNG data points after second fetch: %d", len(all_data))
        except Exception as e:
            logger.warning("FNG API second fetch error: %s", e)

    if all_data:
        dates = sorted(all_data.keys())
        logger.info("  FNG date range: %s to %s", dates[0], dates[-1])

    return all_data


def classify_fng(value):
    """Return the regime bucket name for a given FNG value."""
    for name, lo, hi in FNG_BUCKETS:
        if lo <= value <= hi:
            return name
    return "Unknown"


# ---------------------------------------------------------------------------
# Backfill funding rates for each trade
# ---------------------------------------------------------------------------
def backfill_funding_for_trades(trades):
    """Fetch actual Binance funding rates for each trade's hold period."""
    logger.info("Backfilling funding rates for %d trades...", len(trades))
    backfilled = 0
    failed = 0

    for t in trades:
        try:
            start_ms = int((t["entry_ts"] - timedelta(hours=8)).timestamp() * 1000)
            end_ms = int((t["exit_ts"] + timedelta(hours=8)).timestamp() * 1000)

            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={
                    "symbol": t["symbol"],
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 100,
                },
                timeout=5,
            )

            if resp.status_code == 200:
                rates = resp.json()
                if rates:
                    hold_rates = []
                    for r in rates:
                        ft = datetime.fromtimestamp(r["fundingTime"] / 1000, tz=timezone.utc)
                        if t["entry_ts"] <= ft <= t["exit_ts"]:
                            hold_rates.append(float(r["fundingRate"]) * 100)

                    if hold_rates:
                        # Total cost from negative funding (shorts pay when rate < 0)
                        t["actual_funding_cost_pct"] = sum(abs(r) for r in hold_rates if r < 0)
                        t["actual_funding_rates"] = hold_rates
                        backfilled += 1
                    else:
                        failed += 1
                else:
                    failed += 1
            else:
                failed += 1
        except Exception:
            failed += 1

        time.sleep(0.1)  # rate limit

    logger.info("  Backfilled: %d, Failed: %d", backfilled, failed)


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    logger.info("D2 FNG Regime Overlay starting...")
    logger.info("Output JSON: %s", OUTPUT_JSON)
    logger.info("Output TXT:  %s", OUTPUT_TXT)

    # Fetch FNG data
    fng_data = fetch_fng_history()
    if not fng_data:
        logger.error("No FNG data — cannot proceed.")
        return

    # Get Binance perps
    binance_perps = get_binance_perps()

    # Load trades from DB
    with psycopg.connect(DATABASE_URL) as conn:
        signals = load_pump_signals(conn)
        logger.info("  %d signals", len(signals))
        clusters = detect_pump_clusters(signals)
        logger.info("  %d clusters", len(clusters))
        ohlcv = load_ohlcv(conn, clusters)
        logger.info("  OHLCV for %d symbols", len(ohlcv))

    # Simulate entry25_stop15 Binance-only
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
    logger.info("  %d trades generated (before mania gate)", len(trades))

    # Apply mania gate
    trades = [t for t in trades
              if t.get("max_pct_change") is None
              or t["max_pct_change"] <= MANIA_EXTREME_THRESHOLD]
    logger.info("  %d trades after mania gate", len(trades))

    # Backfill actual funding rates
    backfill_funding_for_trades(trades)

    # Map trades to FNG values
    logger.info("Mapping trades to FNG regime...")
    matched = 0
    unmatched = 0
    for t in trades:
        cluster_date = t["cluster_start_ts"].date()
        fng_entry = fng_data.get(cluster_date)
        if fng_entry:
            t["fng_value"] = fng_entry["value"]
            t["fng_classification"] = fng_entry["classification"]
            t["fng_bucket"] = classify_fng(fng_entry["value"])
            matched += 1
        else:
            # Try adjacent days (FNG may be off by timezone)
            for delta in [-1, 1]:
                adj = cluster_date + timedelta(days=delta)
                fng_entry = fng_data.get(adj)
                if fng_entry:
                    t["fng_value"] = fng_entry["value"]
                    t["fng_classification"] = fng_entry["classification"]
                    t["fng_bucket"] = classify_fng(fng_entry["value"])
                    matched += 1
                    break
            else:
                t["fng_value"] = None
                t["fng_bucket"] = "Unknown"
                unmatched += 1

    logger.info("  FNG matched: %d, unmatched: %d", matched, unmatched)

    # -----------------------------------------------------------------------
    # Analysis 1: Regime distribution
    # -----------------------------------------------------------------------
    regime_dist = defaultdict(list)
    for t in trades:
        regime_dist[t["fng_bucket"]].append(t)

    regime_table = {}
    for bucket_name, _, _ in FNG_BUCKETS:
        bucket_trades = regime_dist.get(bucket_name, [])
        if bucket_trades:
            stats = compute_funding_adjusted_pf(bucket_trades, bucket_name)
            stats["fng_range"] = f"{[lo for n, lo, hi in FNG_BUCKETS if n == bucket_name][0]}-{[hi for n, lo, hi in FNG_BUCKETS if n == bucket_name][0]}"
        else:
            stats = {"label": bucket_name, "n": 0, "win_rate": 0, "pf": 0, "total_pnl_usd": 0, "mean_r": 0}
        regime_table[bucket_name] = stats

    # Check for skew
    max_bucket_n = max(stats["n"] for stats in regime_table.values())
    total_n = sum(stats["n"] for stats in regime_table.values())
    skew_warning = None
    if total_n > 0 and max_bucket_n / total_n > 0.7:
        skew_warning = (f"WARNING: {max_bucket_n}/{total_n} trades ({max_bucket_n/total_n*100:.0f}%) "
                        f"concentrated in one bucket. Regime analysis may be inconclusive.")

    # -----------------------------------------------------------------------
    # Analysis 2: Gate sweep
    # -----------------------------------------------------------------------
    trades_with_fng = [t for t in trades if t.get("fng_value") is not None]
    baseline_stats = compute_funding_adjusted_pf(trades_with_fng, "baseline (all)")

    gate_results = []
    for gate_name, gate_fn in FNG_GATE_THRESHOLDS:
        passed = [t for t in trades_with_fng if gate_fn(t["fng_value"])]
        rejected = [t for t in trades_with_fng if not gate_fn(t["fng_value"])]

        passed_stats = compute_funding_adjusted_pf(passed, gate_name)

        # Count rejected winners/losers
        rej_winners = 0
        rej_losers = 0
        for t in rejected:
            actual_funding = t.get("actual_funding_cost_pct")
            if actual_funding is not None:
                funding_cost = abs(actual_funding) / 100
            else:
                funding_cost = abs(-0.003) * t["n_funding_epochs"]
            pnl = POSITION_SIZE_USD * (t["pnl_pct_raw"] - ROUND_TRIP_FRICTION - funding_cost)
            if pnl > 0:
                rej_winners += 1
            else:
                rej_losers += 1

        gate_results.append({
            "gate": gate_name,
            "n_passed": len(passed),
            "n_rejected": len(rejected),
            "rejected_winners": rej_winners,
            "rejected_losers": rej_losers,
            "stats": passed_stats,
            "pf_delta": round(passed_stats["pf"] - baseline_stats["pf"], 4) if passed_stats["n"] > 0 else None,
        })

    # -----------------------------------------------------------------------
    # Recommendation
    # -----------------------------------------------------------------------
    best_gate = None
    for gr in gate_results:
        if gr["stats"]["n"] >= 20 and gr["pf_delta"] is not None and gr["pf_delta"] > 0.2:
            if best_gate is None or gr["pf_delta"] > best_gate["pf_delta"]:
                best_gate = gr

    recommendation = ""
    if skew_warning:
        recommendation += skew_warning + "\n"

    if best_gate:
        n_rej = best_gate["n_rejected"]
        n_pass = best_gate["n_passed"]
        rej_w = best_gate["rejected_winners"]
        rej_l = best_gate["rejected_losers"]
        recommendation += (
            f"Best gate: {best_gate['gate']} (PF {baseline_stats['pf']:.2f} → "
            f"{best_gate['stats']['pf']:.2f}, Δ={best_gate['pf_delta']:+.4f})\n"
            f"  Rejects {n_rej} trades ({rej_w} winners, {rej_l} losers)\n"
            f"  Remaining N={n_pass}\n"
        )
        if n_pass < 30:
            recommendation += "  CAUTION: N<30 after gate — same small-sample risk as funding gate.\n"
        if rej_w > rej_l:
            recommendation += f"  CAUTION: Rejects more winners ({rej_w}) than losers ({rej_l}) — net edge cost.\n"
        recommendation += "  Recommendation: SOFT GATE (log, don't reject) during paper trading.\n"
    else:
        recommendation += (
            "No regime gate improves PF by >0.20 with N≥20. "
            "D2 performance appears regime-independent at this sample size.\n"
            "Recommendation: No regime gate for paper trading. "
            "Continue logging FNG at each trade for future analysis.\n"
        )

    elapsed = round(time.time() - t0, 1)
    logger.info("Complete in %.1fs", elapsed)

    # -----------------------------------------------------------------------
    # Output JSON
    # -----------------------------------------------------------------------
    output = {
        "metadata": {
            "generated_at": TIMESTAMP,
            "elapsed_seconds": elapsed,
            "config": "entry25_stop15 Binance-only + mania gate",
            "n_trades": len(trades),
            "fng_matched": matched,
            "fng_unmatched": unmatched,
            "fng_date_range": {
                "earliest": str(min(fng_data.keys())) if fng_data else None,
                "latest": str(max(fng_data.keys())) if fng_data else None,
                "n_days": len(fng_data),
            },
        },
        "regime_distribution": {k: v for k, v in regime_table.items()},
        "baseline": baseline_stats,
        "gate_sweep": gate_results,
        "skew_warning": skew_warning,
        "recommendation": recommendation,
        "per_trade_fng": [
            {
                "symbol": t["symbol"],
                "cluster_start": t["cluster_start_ts"].isoformat(),
                "fng_value": t.get("fng_value"),
                "fng_bucket": t.get("fng_bucket"),
                "exit_reason": t["exit_reason"],
                "pnl_pct_raw": round(t["pnl_pct_raw"], 6),
                "r_multiple": round(t["r_multiple"], 4),
            }
            for t in trades
        ],
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)

    # -----------------------------------------------------------------------
    # Output TXT
    # -----------------------------------------------------------------------
    lines = []
    w = lines.append

    w("=" * 70)
    w("D2 FEAR & GREED REGIME OVERLAY")
    w("=" * 70)
    w(f"Config: entry25_stop15 Binance-only + mania gate (N={len(trades)})")
    w(f"FNG data: {len(fng_data)} days, matched {matched}/{len(trades)} trades")
    w(f"All PF values are FUNDING-ADJUSTED")
    w("")

    if skew_warning:
        w(skew_warning)
        w("")

    w("REGIME DISTRIBUTION")
    w("-" * 70)
    w(f"  {'Regime':<16} | {'FNG':>5} | {'N':>4} | {'WR%':>5} | {'PF':>8} | {'PnL($)':>10} | {'Mean R':>7}")
    w(f"  {'-'*16} | {'-'*5} | {'-'*4} | {'-'*5} | {'-'*8} | {'-'*10} | {'-'*7}")
    for bucket_name, lo, hi in FNG_BUCKETS:
        s = regime_table[bucket_name]
        if s["n"] > 0:
            w(f"  {bucket_name:<16} | {lo:>2}-{hi:<2} | {s['n']:>4} | {s['win_rate']:>5.1f} | "
              f"{s['pf']:>8.4f} | {s['total_pnl_usd']:>10.2f} | {s['mean_r']:>7.4f}")
        else:
            w(f"  {bucket_name:<16} | {lo:>2}-{hi:<2} | {s['n']:>4} |   N/A |      N/A |        N/A |     N/A")

    w("")
    w("GATE SWEEP")
    w("-" * 70)
    w(f"  Baseline: N={baseline_stats['n']}, PF={baseline_stats['pf']:.4f}, "
      f"WR={baseline_stats['win_rate']:.1f}%")
    w("")
    w(f"  {'Gate':<20} | {'N':>4} | {'Rej':>4} | {'PF':>8} | {'WR%':>5} | {'Δ PF':>7} | {'Rej W/L':>8}")
    w(f"  {'-'*20} | {'-'*4} | {'-'*4} | {'-'*8} | {'-'*5} | {'-'*7} | {'-'*8}")
    for gr in gate_results:
        s = gr["stats"]
        delta = f"{gr['pf_delta']:>+7.4f}" if gr["pf_delta"] is not None else "    N/A"
        if s["n"] > 0:
            w(f"  {gr['gate']:<20} | {s['n']:>4} | {gr['n_rejected']:>4} | "
              f"{s['pf']:>8.4f} | {s['win_rate']:>5.1f} | {delta} | "
              f"{gr['rejected_winners']:>3}/{gr['rejected_losers']:<3}")
        else:
            w(f"  {gr['gate']:<20} | {s['n']:>4} | {gr['n_rejected']:>4} | "
              f"     N/A |   N/A | {delta} | "
              f"{gr['rejected_winners']:>3}/{gr['rejected_losers']:<3}")

    w("")
    w("RECOMMENDATION")
    w("-" * 70)
    for line in recommendation.strip().split("\n"):
        w(f"  {line}")

    w(f"\n  Output: {OUTPUT_JSON}")

    report = "\n".join(lines)
    with open(OUTPUT_TXT, "w") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
