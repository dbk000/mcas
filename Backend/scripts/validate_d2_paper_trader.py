#!/usr/bin/env python3
"""
D2 Paper Trader Validation Replay
===================================

Runs the D2 state machine (identical to d2_paper_trader.py logic) against
historical 1h OHLCV for all detected clusters. Compares aggregate results
against the known backtest numbers from d2_honest_backtest.py:

  entry25_stop15 / binance_only:
    N=58, WR=81%, PF=2.94 (frictionless)

After mania gate (max_pct_change ≤ 40%):
    N=56, WR=82.1%, PF=3.20 (frictionless)

This validates that the paper trader's state machine reproduces the
backtest exactly. Any deviation = implementation bug.

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/validate_d2_paper_trader.py

Output: scripts/output/validate_d2_paper_trader_{ts}.json
"""

import bisect
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
import requests

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
OUTPUT_PATH = os.path.join(OUTPUT_DIR, f"validate_d2_paper_trader_{TIMESTAMP}.json")
logger.info("Output: %s", OUTPUT_PATH)

# Locked D2 config
ENTRY_THRESHOLD = 0.25
STOP_PCT = 0.15
TIME_STOP_HOURS = 72
MAX_WATCH_HOURS = 48
MANIA_GATE_PCT = 40.0
CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3

# Known backtest numbers (from d2_honest_backtest + d2_next_steps)
EXPECTED_PRE_MANIA = {"n": 58, "wr": 81.0, "pf_approx": 2.94}
EXPECTED_POST_MANIA = {"n": 56, "wr": 82.1, "pf_approx": 3.20}


# ---------------------------------------------------------------------------
# Load clusters from social_bot_signals
# ---------------------------------------------------------------------------
def load_clusters(conn) -> list[dict]:
    """Reproduce cluster detection from social_bot_signals."""
    logger.info("Loading pump signals from social_bot_signals...")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, source_post_id, symbol, exchange, signal_type,
                   price, pct_change, vol_24h, funding_rate, post_ts
            FROM social_bot_signals
            WHERE signal_type = 'pump'
            ORDER BY post_ts
        """)
        signals = cur.fetchall()
    logger.info("  %d pump signals loaded", len(signals))

    by_symbol = defaultdict(list)
    for s in signals:
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
    logger.info("  %d clusters detected", len(clusters))
    return clusters


def _build_cluster(symbol, signals):
    pct_changes = [float(s["pct_change"]) for s in signals if s.get("pct_change") is not None]
    exchanges = set(s.get("exchange") for s in signals if s.get("exchange"))
    return {
        "symbol": symbol,
        "cluster_start_ts": signals[0]["post_ts"],
        "cluster_end_ts": signals[-1]["post_ts"],
        "alert_count": len(signals),
        "exchanges_seen": sorted(exchanges),
        "max_pct_change": max(pct_changes) if pct_changes else None,
    }


# ---------------------------------------------------------------------------
# Binance perp list
# ---------------------------------------------------------------------------
def get_binance_perp_symbols():
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
        logger.warning("  Failed to fetch Binance perps: %s", e)
        return set()


# ---------------------------------------------------------------------------
# Load OHLCV
# ---------------------------------------------------------------------------
def load_ohlcv(conn, clusters):
    symbols = sorted(set(c["symbol"] for c in clusters))
    earliest = min(c["cluster_start_ts"] for c in clusters) - timedelta(hours=2)
    latest = max(c["cluster_start_ts"] for c in clusters) + timedelta(hours=130)

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
    logger.info("OHLCV loaded for %d/%d symbols", len(ohlcv), len(symbols))
    return ohlcv


# ---------------------------------------------------------------------------
# State machine replay (mirrors d2_honest_backtest.py simulate_hwm_trailing)
# ---------------------------------------------------------------------------
def replay_trade(cluster, ohlcv_bars, bar_times):
    """Replay the D2 HWM trailing state machine on 1h OHLCV bars."""
    pre_idx = bisect.bisect_left(bar_times, cluster["cluster_start_ts"]) - 1
    if pre_idx < 0:
        return None
    pre_pump_price = float(ohlcv_bars[pre_idx][4])

    cluster_start = cluster["cluster_start_ts"]
    max_watch_ts = cluster_start + timedelta(hours=MAX_WATCH_HOURS)

    start_idx = bisect.bisect_left(bar_times, cluster_start)
    if start_idx >= len(ohlcv_bars):
        return None

    live_hwm = 0.0
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

            if bar_ts >= max_watch_ts:
                return None

            if live_hwm > 0:
                drop_from_hwm = (live_hwm - bar_close) / live_hwm
                if drop_from_hwm >= ENTRY_THRESHOLD:
                    entry_price = bar_close
                    entry_ts = bar_ts
                    entry_hwm = live_hwm
                    stop_price = live_hwm * (1 + STOP_PCT)

                    if entry_price <= tp_price:
                        return None

                    state = "IN_TRADE"
                    continue

        elif state == "IN_TRADE":
            time_stop_ts = entry_ts + timedelta(hours=TIME_STOP_HOURS)

            if bar_high >= stop_price:
                return _build_result(
                    cluster, "STOP_LOSS", stop_price, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                )

            if bar_low <= tp_price:
                return _build_result(
                    cluster, "TP_HIT", tp_price, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                )

            if bar_ts >= time_stop_ts:
                return _build_result(
                    cluster, "TIME_STOP", bar_close, entry_price, entry_ts,
                    bar_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                    i - bisect.bisect_left(bar_times, entry_ts),
                )

    if state == "IN_TRADE":
        last_i = len(ohlcv_bars) - 1
        return _build_result(
            cluster, "TIME_STOP", float(ohlcv_bars[last_i][4]),
            entry_price, entry_ts, bar_times[last_i],
            entry_hwm, tp_price, stop_price, pre_pump_price,
            last_i - bisect.bisect_left(bar_times, entry_ts),
        )

    return None


def _build_result(cluster, exit_reason, exit_price, entry_price, entry_ts,
                  exit_ts, entry_hwm, tp_price, stop_price, pre_pump_price,
                  bars_held):
    risk = stop_price - entry_price
    if risk <= 0:
        risk = 0.001
    pnl_pct = (entry_price - exit_price) / entry_price
    r_multiple = (entry_price - exit_price) / risk
    hours_held = (exit_ts - entry_ts).total_seconds() / 3600 if exit_ts and entry_ts else 0

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
        "r_multiple": round(r_multiple, 4),
        "pnl_pct": round(pnl_pct * 100, 4),
        "entry_hwm": entry_hwm,
        "stop_price": stop_price,
        "tp_price": tp_price,
        "pre_pump_price": pre_pump_price,
    }


# ---------------------------------------------------------------------------
# Compute aggregate metrics
# ---------------------------------------------------------------------------
def compute_metrics(trades: list[dict]) -> dict:
    """Compute PF, WR, exit breakdown from trade list."""
    if not trades:
        return {"n": 0, "wr": 0, "pf": 0}

    n = len(trades)
    winners = [t for t in trades if t["r_multiple"] > 0]
    losers = [t for t in trades if t["r_multiple"] <= 0]
    wr = len(winners) / n * 100

    gross_profit = sum(t["r_multiple"] for t in winners)
    gross_loss = abs(sum(t["r_multiple"] for t in losers))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    exit_counts = defaultdict(int)
    for t in trades:
        exit_counts[t["exit_reason"]] += 1

    return {
        "n": n,
        "wr": round(wr, 1),
        "pf": round(pf, 2),
        "n_winners": len(winners),
        "n_losers": len(losers),
        "mean_r_winners": round(sum(t["r_multiple"] for t in winners) / len(winners), 4) if winners else 0,
        "mean_r_losers": round(sum(t["r_multiple"] for t in losers) / len(losers), 4) if losers else 0,
        "exit_breakdown": dict(exit_counts),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("D2 Paper Trader Validation Replay")
    logger.info("=" * 60)

    conn = psycopg.connect(DATABASE_URL)

    # 1. Load clusters
    all_clusters = load_clusters(conn)

    # 2. Get Binance perps for filtering
    binance_perps = get_binance_perp_symbols()

    # 3. Filter to Binance-only
    binance_clusters = [c for c in all_clusters if c["symbol"] in binance_perps]
    logger.info("Binance-only clusters: %d / %d", len(binance_clusters), len(all_clusters))

    # 4. Load OHLCV
    ohlcv = load_ohlcv(conn, binance_clusters)

    # 5. Replay all Binance clusters (pre-mania gate)
    logger.info("Replaying %d Binance clusters (pre-mania gate)...", len(binance_clusters))
    pre_mania_trades = []
    for cluster in binance_clusters:
        sym = cluster["symbol"]
        bars = ohlcv.get(sym)
        if not bars:
            continue
        bar_times = [b[0] for b in bars]
        result = replay_trade(cluster, bars, bar_times)
        if result:
            pre_mania_trades.append(result)

    pre_mania_metrics = compute_metrics(pre_mania_trades)
    logger.info("PRE-MANIA GATE:")
    logger.info("  N=%d (expected %d)", pre_mania_metrics["n"], EXPECTED_PRE_MANIA["n"])
    logger.info("  WR=%.1f%% (expected %.1f%%)", pre_mania_metrics["wr"], EXPECTED_PRE_MANIA["wr"])
    logger.info("  PF=%.2f (expected ~%.2f)", pre_mania_metrics["pf"], EXPECTED_PRE_MANIA["pf_approx"])
    logger.info("  Exits: %s", pre_mania_metrics["exit_breakdown"])

    # 6. Apply mania gate
    post_mania_trades = [
        t for t in pre_mania_trades
        if t.get("max_pct_change") is None or t["max_pct_change"] <= MANIA_GATE_PCT
    ]
    post_mania_metrics = compute_metrics(post_mania_trades)
    logger.info("POST-MANIA GATE (max_pct ≤ %.0f%%):", MANIA_GATE_PCT)
    logger.info("  N=%d (expected %d)", post_mania_metrics["n"], EXPECTED_POST_MANIA["n"])
    logger.info("  WR=%.1f%% (expected %.1f%%)", post_mania_metrics["wr"], EXPECTED_POST_MANIA["wr"])
    logger.info("  PF=%.2f (expected ~%.2f)", post_mania_metrics["pf"], EXPECTED_POST_MANIA["pf_approx"])
    logger.info("  Exits: %s", post_mania_metrics["exit_breakdown"])

    # 7. Validate
    checks = []

    # Pre-mania N — must be exact
    n_ok = pre_mania_metrics["n"] == EXPECTED_PRE_MANIA["n"]
    checks.append(("pre_mania_n", n_ok, pre_mania_metrics["n"], EXPECTED_PRE_MANIA["n"]))

    # Pre-mania WR (±5pp tolerance — backtest counted TIME_STOP as loss
    # regardless of R sign; replay counts R>0 as win. Known difference.)
    wr_ok = abs(pre_mania_metrics["wr"] - EXPECTED_PRE_MANIA["wr"]) <= 5.0
    checks.append(("pre_mania_wr", wr_ok, pre_mania_metrics["wr"], EXPECTED_PRE_MANIA["wr"]))

    # Pre-mania PF (±0.5 tolerance — same TIME_STOP classification effect)
    pf_ok = abs(pre_mania_metrics["pf"] - EXPECTED_PRE_MANIA["pf_approx"]) <= 0.5
    checks.append(("pre_mania_pf", pf_ok, pre_mania_metrics["pf"], EXPECTED_PRE_MANIA["pf_approx"]))

    # Post-mania N — must be exact
    n2_ok = post_mania_metrics["n"] == EXPECTED_POST_MANIA["n"]
    checks.append(("post_mania_n", n2_ok, post_mania_metrics["n"], EXPECTED_POST_MANIA["n"]))

    # Post-mania WR (±5pp)
    wr2_ok = abs(post_mania_metrics["wr"] - EXPECTED_POST_MANIA["wr"]) <= 5.0
    checks.append(("post_mania_wr", wr2_ok, post_mania_metrics["wr"], EXPECTED_POST_MANIA["wr"]))

    # Post-mania PF (±0.5)
    pf2_ok = abs(post_mania_metrics["pf"] - EXPECTED_POST_MANIA["pf_approx"]) <= 0.5
    checks.append(("post_mania_pf", pf2_ok, post_mania_metrics["pf"], EXPECTED_POST_MANIA["pf_approx"]))

    all_pass = all(ok for _, ok, _, _ in checks)

    logger.info("")
    logger.info("=" * 60)
    logger.info("VALIDATION CHECKS:")
    for name, ok, actual, expected in checks:
        status = "PASS" if ok else "FAIL"
        logger.info("  [%s] %s: actual=%s expected=%s", status, name, actual, expected)

    if all_pass:
        verdict = "PASS"
        logger.info("")
        logger.info("VALIDATION PASSED — state machine matches backtest")
    else:
        verdict = "FAIL"
        logger.error("")
        logger.error("VALIDATION FAILED — investigate mismatches before going live")

    elapsed = time.time() - t0
    logger.info("Elapsed: %.1fs", elapsed)
    logger.info("=" * 60)

    # 8. Write output
    output = {
        "validation_ts": TIMESTAMP,
        "verdict": verdict,
        "pre_mania_gate": {
            "metrics": pre_mania_metrics,
            "expected": EXPECTED_PRE_MANIA,
        },
        "post_mania_gate": {
            "metrics": post_mania_metrics,
            "expected": EXPECTED_POST_MANIA,
        },
        "checks": [
            {"name": name, "pass": ok, "actual": actual, "expected": expected}
            for name, ok, actual, expected in checks
        ],
        "per_trade_detail": [
            {k: (v.isoformat() if isinstance(v, datetime) else v)
             for k, v in t.items()}
            for t in post_mania_trades
        ],
        "config": {
            "entry_threshold": ENTRY_THRESHOLD,
            "stop_pct": STOP_PCT,
            "time_stop_hours": TIME_STOP_HOURS,
            "max_watch_hours": MAX_WATCH_HOURS,
            "mania_gate_pct": MANIA_GATE_PCT,
        },
        "elapsed_s": round(elapsed, 1),
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info("Output written to %s", OUTPUT_PATH)

    conn.close()


if __name__ == "__main__":
    main()
