#!/usr/bin/env python3
"""
D1 Dormancy-to-Ignition Simulation Matrix
==========================================

Simulates D1 opportunities across multiple exit configurations:
  A. Binary 10% TP, 2.5% stop, no trail, 3d TS (default D1 config)
  B. Binary 15% TP, 2.5% stop, no trail, 3d TS
  C. Binary 20% TP, 2.5% stop, no trail, 3d TS
  D. Binary 10% TP, 1.5% stop, no trail, 3d TS (tighter stop)
  E. Binary 10% TP, 2.5% stop, no trail, 5d TS (longer hold)
  F. Post-TP runner trail: trail activates at +10%, 5% from HWM, 3d TS

Analysis slices: by dormancy_score, volume_z at trigger, timeframe.
Metrics: daily PF (success gate), win rate, agg PF, R-multiple, max DD, exit reasons.

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \\
        python3 /scripts/sim_d1_matrix.py --batch-id 'D1_v1.0%%'
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

import psycopg
from psycopg.rows import dict_row
from exit_checker import ExitConfig, check_exit_simple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
TF_DELTA = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}

THREE_DAY_BARS = {"1h": 72}
FIVE_DAY_BARS = {"1h": 120}

# Simulation parameters
SIM_SLIPPAGE_BPS = 5.0
SIM_FEE_BPS = 4.0
SIM_POSITION_USD = 1000.0
MAX_BARS_FORWARD = 504        # 21 days for 1h
MAX_ENTRY_DELAY_BARS = 3
MAX_FEE_R_RATIO = 0.3
MIN_R_PRESERVATION = 0.5

# No-trail, no-FF exit config
NO_TRAIL = ExitConfig(
    fail_fast_bars=0, fail_fast_threshold=0.0,
    trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
)

# Runner trail: activates at 4R (=10% when stop=2.5%), 2R distance (=5% of entry)
RUNNER_TRAIL = ExitConfig(
    fail_fast_bars=0, fail_fast_threshold=0.0,
    trail_breakeven_r=0, trail_activation_r=4.0, trail_distance_r=2.0,
)

# ── Exit config definitions ───────────────────────────────────────────────
EXIT_CONFIGS = {
    "A": {"desc": "Binary 10% TP, 2.5% stop, 3d TS (default)", "tp_pct": 10.0, "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL},
    "B": {"desc": "Binary 15% TP, 2.5% stop, 3d TS",           "tp_pct": 15.0, "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL},
    "C": {"desc": "Binary 20% TP, 2.5% stop, 3d TS",           "tp_pct": 20.0, "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL},
    "D": {"desc": "Binary 10% TP, 1.5% stop, 3d TS",           "tp_pct": 10.0, "stop_pct": 1.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL},
    "E": {"desc": "Binary 10% TP, 2.5% stop, 5d TS",           "tp_pct": 10.0, "stop_pct": 2.5, "ts": FIVE_DAY_BARS,  "ec": NO_TRAIL},
    "F": {"desc": "Runner trail from +10%, 3d TS",              "tp_pct": None,  "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": RUNNER_TRAIL},
    "G": {"desc": "Binary 20% TP (8R), 2.5% stop, 3d TS",    "tp_pct": 20.0, "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL},
}


# ══════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def load_opportunities(database_url: str, batch_pattern: str) -> list[dict]:
    """Load D1 opportunities matching batch pattern."""
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, symbol, timeframe, setup_id, decision,
                       score, created_ts, payload_json, status, batch_id
                FROM opportunities
                WHERE setup_id = 'D1'
                  AND batch_id LIKE %s
                  AND status = 'expired'
                ORDER BY created_ts ASC;
            """, (batch_pattern,))
            return list(cur.fetchall())


def parse_payload(opp: dict) -> dict:
    payload = opp.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return payload


def compute_ohlcv_ranges(
    opps: list[dict], metas: list[dict]
) -> dict[str, tuple[datetime, datetime]]:
    """Compute OHLCV date ranges per (symbol, tf) from filtered opps."""
    ranges: dict[str, tuple[datetime, datetime]] = {}
    for opp, meta in zip(opps, metas):
        key = f"{meta['symbol']}_{meta['timeframe']}"
        opp_ts = meta["created_ts"]
        bar_delta = TF_DELTA.get(meta["timeframe"], timedelta(hours=1))
        end_ts = opp_ts + bar_delta * (MAX_BARS_FORWARD + 10)
        if key not in ranges:
            ranges[key] = (opp_ts, end_ts)
        else:
            s, e = ranges[key]
            ranges[key] = (min(s, opp_ts), max(e, end_ts))
    return ranges


def load_ohlcv_batch(
    database_url: str,
    symbol_tf_ranges: dict[str, tuple[datetime, datetime]],
) -> dict[str, list[dict]]:
    """Load OHLCV for given (symbol, tf) ranges."""
    cache: dict[str, list[dict]] = {}
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for key, (start_ts, end_ts) in symbol_tf_ranges.items():
                symbol, tf = key.rsplit("_", 1)
                cur.execute("""
                    SELECT DISTINCT ON (bar_start_ts)
                           bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = %s
                      AND bar_start_ts >= %s AND bar_start_ts < %s
                      AND is_final = TRUE
                    ORDER BY bar_start_ts,
                             CASE source
                                 WHEN 'ws' THEN 0
                                 WHEN 'binance' THEN 1
                                 ELSE 2
                             END;
                """, (symbol, tf, start_ts, end_ts))
                bars = [
                    {"bar_start_ts": r[0], "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                    for r in cur.fetchall()
                ]
                cache[key] = bars
    return cache


# ══════════════════════════════════════════════════════════════════════════
#  SIMULATION ENGINE
# ══════════════════════════════════════════════════════════════════════════

def simulate_opportunity(
    opp_ts: datetime, direction: str, entry_price: float,
    bars: list[dict], tf: str,
    stop_pct: float, tp_pct: Optional[float],
    time_stop_bars: int, exit_config: ExitConfig,
) -> dict:
    """Simulate a single D1 opportunity. Returns result dict."""
    if not bars:
        return {"data_available": False, "pnl_net": None}

    # Find entry bar
    stop_frac = stop_pct / 100.0
    if direction == "long":
        stop_price = entry_price * (1 - stop_frac)
        r_value = entry_price - stop_price
        tp1_price = entry_price * (1 + tp_pct / 100.0) if tp_pct else None
    else:
        stop_price = entry_price * (1 + stop_frac)
        r_value = stop_price - entry_price
        tp1_price = entry_price * (1 - tp_pct / 100.0) if tp_pct else None

    if r_value <= 0:
        return {"data_available": False, "pnl_net": None}

    # Find entry bar (first bar after opp_ts)
    entry_bar_idx = None
    for i, bar in enumerate(bars):
        bar_ts = bar["bar_start_ts"]
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        if bar_ts > opp_ts:
            entry_bar_idx = i
            break

    if entry_bar_idx is None:
        return {"data_available": False, "pnl_net": None}

    # Entry delay check
    bar_delta = TF_DELTA.get(tf, timedelta(hours=1))
    entry_bar = bars[entry_bar_idx]
    entry_bar_ts = entry_bar["bar_start_ts"]
    if entry_bar_ts.tzinfo is None:
        entry_bar_ts = entry_bar_ts.replace(tzinfo=timezone.utc)
    if entry_bar_ts - opp_ts > bar_delta * MAX_ENTRY_DELAY_BARS:
        return {"data_available": False, "pnl_net": None}

    # Entry at next bar's open with slippage
    actual_entry = entry_bar["open"]
    if direction == "long":
        entry_slip = actual_entry * (1 + SIM_SLIPPAGE_BPS / 10000)
    else:
        entry_slip = actual_entry * (1 - SIM_SLIPPAGE_BPS / 10000)

    # Recalculate r_value with actual fill
    if direction == "long":
        actual_r = entry_slip - stop_price
    else:
        actual_r = stop_price - entry_slip

    if actual_r <= 0:
        return {"data_available": False, "pnl_net": None}

    # Fee/R guard
    fee_per_unit = 2 * entry_slip * SIM_FEE_BPS / 10000
    if fee_per_unit > MAX_FEE_R_RATIO * actual_r:
        return {"data_available": False, "pnl_net": None}

    # R preservation guard
    if r_value > 0 and actual_r < MIN_R_PRESERVATION * r_value:
        return {"data_available": False, "pnl_net": None}

    # TP invalidation
    if tp1_price is not None:
        if direction == "long" and tp1_price <= entry_slip:
            tp1_price = None
        elif direction == "short" and tp1_price >= entry_slip:
            tp1_price = None

    position_size = SIM_POSITION_USD / entry_slip
    max_bar_idx = min(entry_bar_idx + MAX_BARS_FORWARD, len(bars) - 1)

    # Walk forward
    hwm = None
    half_r_touched = False
    exit_reason = None
    exit_price = None
    bars_held = 0

    for i in range(entry_bar_idx + 1, max_bar_idx + 1):
        bar = bars[i]
        bars_held += 1

        exit_result, new_hwm, new_half_r = check_exit_simple(
            side=direction, bar=bar,
            entry_price=entry_slip, stop_price=stop_price,
            tp1_price=tp1_price, tp2_price=None,
            r_value=actual_r, bars_held=bars_held,
            max_bars=time_stop_bars,
            high_water_mark=hwm, half_r_touched=half_r_touched,
            config=exit_config,
        )
        hwm = new_hwm
        half_r_touched = new_half_r

        if exit_result:
            exit_reason, exit_price = exit_result
            break

    if exit_reason is None:
        last_bar = bars[min(max_bar_idx, len(bars) - 1)]
        exit_reason = "MAX_BARS"
        exit_price = last_bar["close"]

    # PnL
    if exit_reason != "STOP_LOSS":
        if direction == "long":
            exit_slip = exit_price * (1 - SIM_SLIPPAGE_BPS / 10000)
        else:
            exit_slip = exit_price * (1 + SIM_SLIPPAGE_BPS / 10000)
    else:
        exit_slip = exit_price

    if direction == "long":
        pnl_gross = (exit_slip - entry_slip) * position_size
    else:
        pnl_gross = (entry_slip - exit_slip) * position_size

    entry_fee = entry_slip * position_size * SIM_FEE_BPS / 10000
    exit_fee = exit_slip * position_size * SIM_FEE_BPS / 10000
    pnl_net = pnl_gross - entry_fee - exit_fee
    r_multiple = pnl_net / (actual_r * position_size) if actual_r * position_size > 0 else 0

    return {
        "data_available": True,
        "pnl_net": round(pnl_net, 4),
        "r_multiple": round(r_multiple, 4),
        "exit_reason": exit_reason,
        "bars_held": bars_held,
    }


# ══════════════════════════════════════════════════════════════════════════
#  METRICS
# ══════════════════════════════════════════════════════════════════════════

def compute_metrics(results: list[dict], label: str = "") -> dict:
    """Compute summary stats from result dicts."""
    traded = [r for r in results if r.get("data_available") and r.get("pnl_net") is not None]
    if not traded:
        return {"label": label, "n": 0, "n_traded": 0}

    pnls = [r["pnl_net"] for r in traded]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    agg_pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Average daily PF (the success gate)
    daily_pnls: dict[str, list[float]] = defaultdict(list)
    for r in traded:
        ts = r.get("_created_ts")
        if ts:
            day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else str(ts)[:10]
            daily_pnls[day].append(r["pnl_net"])

    daily_pfs = []
    for _, day_pnls in sorted(daily_pnls.items()):
        gp = sum(p for p in day_pnls if p > 0)
        gl = abs(sum(p for p in day_pnls if p < 0))
        if gl > 0:
            daily_pfs.append(min(gp / gl, 5.0))
        elif gp > 0:
            daily_pfs.append(5.0)

    avg_daily_pf = float(np.mean(daily_pfs)) if daily_pfs else 0.0

    # Exit reason distribution
    exit_reasons: dict[str, int] = defaultdict(int)
    for r in traded:
        exit_reasons[r["exit_reason"]] += 1

    # Max drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in traded:
        equity += r["pnl_net"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "label": label,
        "n": len(results),
        "n_traded": len(traded),
        "win_rate": round(len(wins) / len(traded), 4),
        "agg_pf": round(agg_pf, 4) if agg_pf != float("inf") else 99.0,
        "avg_daily_pf": round(avg_daily_pf, 4),
        "n_trading_days": len(daily_pfs),
        "avg_win": round(float(np.mean(wins)), 2) if wins else 0,
        "avg_loss": round(float(np.mean(losses)), 2) if losses else 0,
        "total_pnl": round(sum(pnls), 2),
        "max_dd": round(max_dd, 2),
        "avg_r": round(float(np.mean([r["r_multiple"] for r in traded])), 4),
        "exit_reasons": dict(exit_reasons),
    }


def slice_results(
    all_results: list[dict], metas: list[dict],
    dormancy_min: float = 0.0,
    vol_z_min: float = 0.0,
    tf_filter: Optional[str] = None,
) -> list[dict]:
    """Filter results by analysis dimensions."""
    filtered = []
    for result, meta in zip(all_results, metas):
        if result is None or not result.get("data_available"):
            continue
        if result.get("pnl_net") is None:
            continue

        # Gap-through filter
        if result["r_multiple"] < -1.05:
            continue

        # Dormancy score threshold
        if meta.get("dormancy_score", 0) < dormancy_min:
            continue

        # Volume Z threshold
        if meta.get("volume_z_at_trigger", 0) < vol_z_min:
            continue

        # TF filter
        if tf_filter and meta["timeframe"] != tf_filter:
            continue

        result_copy = dict(result)
        result_copy["_created_ts"] = meta["created_ts"]
        filtered.append(result_copy)

    return filtered


# ══════════════════════════════════════════════════════════════════════════
#  OUTPUT
# ══════════════════════════════════════════════════════════════════════════

def fmt_cell(m: dict) -> str:
    if m["n_traded"] == 0:
        return f"{'—':>22}"
    return f"{m['win_rate']:>5.1%} {m['agg_pf']:>5.2f} ${m['total_pnl']:>7.0f} ({m['n_traded']:>4})"


def print_matrix(all_results: dict[str, list[dict]], metas: list[dict]):
    """Print the full simulation matrix."""
    log.info("")
    log.info("═" * 90)
    log.info("  D1 SIMULATION MATRIX — Exit Config × Dormancy Threshold × Volume Z")
    log.info("═" * 90)

    dormancy_thresholds = [0.0, 0.80, 0.90, 0.95]
    vol_z_thresholds = [0.0, 2.0, 3.0, 4.0]

    for config_key in sorted(EXIT_CONFIGS.keys()):
        cfg = EXIT_CONFIGS[config_key]
        results = all_results[config_key]

        log.info("")
        log.info(f"  Config {config_key}: {cfg['desc']}")
        log.info(f"  {'':>14} | {'All':>22} | {'dorm≥0.80':>22} | {'dorm≥0.90':>22} | {'dorm≥0.95':>22}")
        log.info(f"  {'-'*14}-+-{'-'*22}-+-{'-'*22}-+-{'-'*22}-+-{'-'*22}")

        for vol_z in vol_z_thresholds:
            cells = []
            for dorm in dormancy_thresholds:
                sliced = slice_results(results, metas, dormancy_min=dorm, vol_z_min=vol_z)
                m = compute_metrics(sliced, f"d≥{dorm:.2f}_vz≥{vol_z:.1f}")
                cells.append(fmt_cell(m))

            label = f"vol_z≥{vol_z:.1f}" if vol_z > 0 else "All vol_z"
            log.info(f"  {label:>14} | {' | '.join(cells)}")

        # Best config summary (all data)
        all_sliced = slice_results(results, metas)
        m = compute_metrics(all_sliced, f"Config_{config_key}_all")
        log.info(f"  ── Summary: WR={m['win_rate']:.1%} aggPF={m['agg_pf']:.2f} "
                 f"dailyPF={m['avg_daily_pf']:.2f} PnL=${m['total_pnl']:.0f} "
                 f"N={m['n_traded']} maxDD=${m['max_dd']:.0f}")
        if m.get("exit_reasons"):
            log.info(f"     Exits: {m['exit_reasons']}")

    log.info("")
    log.info("═" * 90)


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="D1 simulation matrix")
    parser.add_argument("--batch-id", type=str, required=True,
                        help="Batch ID pattern (SQL LIKE, e.g. 'D1_v1.0%%')")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory (default: /scripts/output or scripts/output)")
    args = parser.parse_args()

    # Set up persistent output file
    if args.output_dir:
        output_dir = args.output_dir
    elif os.path.isdir("/scripts/output"):
        output_dir = "/scripts/output"
    elif os.path.isdir(os.path.join(os.path.dirname(__file__), "output")):
        output_dir = os.path.join(os.path.dirname(__file__), "output")
    else:
        output_dir = "/scripts/output"
    os.makedirs(output_dir, exist_ok=True)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"sim_d1_matrix_{ts_tag}.txt")
    file_handler = logging.FileHandler(output_path, mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(file_handler)
    log.info("Output file: %s", output_path)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        # Try .env
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        os.environ.setdefault(key.strip(), val.strip())
            database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    # Load opportunities
    log.info("Loading D1 opportunities matching: %s", args.batch_id)
    opps = load_opportunities(database_url, args.batch_id)
    log.info("  Found %d opportunities", len(opps))

    if not opps:
        log.warning("No opportunities found. Run backfill_d1_opportunities.py first.")
        return

    # Parse payloads + build metadata
    metas = []
    for opp in opps:
        payload = parse_payload(opp)
        metas.append({
            "symbol": opp["symbol"],
            "timeframe": opp["timeframe"],
            "created_ts": opp["created_ts"],
            "direction": payload.get("direction", "long"),
            "entry_price": float(payload.get("entry_price", 0)),
            "dormancy_score": float(payload.get("dormancy_score", 0)),
            "volume_z_at_trigger": float(payload.get("volume_z_at_trigger", 0)),
            "rvol_at_trigger": float(payload.get("rvol_at_trigger", 0)),
            "accum_divergence": float(payload.get("accum_divergence", 0)),
            "score": float(opp.get("score", 0)),
            "idx": len(metas),
        })

    log.info("  Symbols: %d unique", len(set(m["symbol"] for m in metas)))
    log.info("  Date range: %s to %s",
             min(m["created_ts"] for m in metas).date(),
             max(m["created_ts"] for m in metas).date())

    # Compute OHLCV ranges needed
    ohlcv_ranges = compute_ohlcv_ranges(opps, metas)
    log.info("  OHLCV needed for %d symbol/tf combos", len(ohlcv_ranges))

    # Load OHLCV
    t0 = time.time()
    ohlcv_cache = load_ohlcv_batch(database_url, ohlcv_ranges)
    log.info("  OHLCV loaded in %.1fs (%d combos, %d total bars)",
             time.time() - t0, len(ohlcv_cache),
             sum(len(v) for v in ohlcv_cache.values()))

    # Run simulation for each exit config
    all_config_results: dict[str, list[dict]] = {}

    for config_key, cfg in EXIT_CONFIGS.items():
        t1 = time.time()
        results = []

        for opp, meta in zip(opps, metas):
            cache_key = f"{meta['symbol']}_{meta['timeframe']}"
            bars = ohlcv_cache.get(cache_key, [])

            tf = meta["timeframe"]
            ts_bars = cfg["ts"].get(tf, 72)

            result = simulate_opportunity(
                opp_ts=meta["created_ts"],
                direction=meta["direction"],
                entry_price=meta["entry_price"],
                bars=bars,
                tf=tf,
                stop_pct=cfg["stop_pct"],
                tp_pct=cfg["tp_pct"],
                time_stop_bars=ts_bars,
                exit_config=cfg["ec"],
            )
            results.append(result)

        all_config_results[config_key] = results
        traded = sum(1 for r in results if r.get("data_available") and r.get("pnl_net") is not None)
        log.info("  Config %s (%s): %d/%d simulated in %.1fs",
                 config_key, cfg["desc"], traded, len(results), time.time() - t1)

    # Print results
    print_matrix(all_config_results, metas)

    # Overall summary
    log.info("\n  OVERALL SUMMARY")
    log.info("  %-8s %-35s %6s %6s %8s %8s %8s", "Config", "Description",
             "WR", "aggPF", "dailyPF", "PnL", "N")
    log.info("  " + "-" * 80)
    summary_data = {}
    for ck in sorted(EXIT_CONFIGS.keys()):
        cfg = EXIT_CONFIGS[ck]
        sliced = slice_results(all_config_results[ck], metas)
        m = compute_metrics(sliced, ck)
        summary_data[ck] = {**m, "desc": cfg["desc"]}
        if m["n_traded"] > 0:
            log.info("  %-8s %-35s %5.1f%% %6.2f %8.2f $%7.0f %6d",
                     ck, cfg["desc"], m["win_rate"] * 100, m["agg_pf"],
                     m["avg_daily_pf"], m["total_pnl"], m["n_traded"])
        else:
            log.info("  %-8s %-35s %6s", ck, cfg["desc"], "—")

    # Write JSON summary alongside the text log
    json_path = output_path.replace(".txt", ".json")
    # Build full config specs for reproducibility
    config_specs = {}
    for ck, cfg in EXIT_CONFIGS.items():
        config_specs[ck] = {
            "desc": cfg["desc"],
            "tp_pct": cfg["tp_pct"],
            "stop_pct": cfg["stop_pct"],
            "time_stop_bars": cfg["ts"],
            "trail": {
                "fail_fast_bars": cfg["ec"].fail_fast_bars,
                "fail_fast_threshold": cfg["ec"].fail_fast_threshold,
                "trail_activation_r": cfg["ec"].trail_activation_r,
                "trail_distance_r": cfg["ec"].trail_distance_r,
                "trail_breakeven_r": cfg["ec"].trail_breakeven_r,
            },
        }
    json_summary = {
        "batch_id": args.batch_id,
        "n_opportunities": len(opps),
        "n_symbols": len(set(m["symbol"] for m in metas)),
        "date_range": [
            min(m["created_ts"] for m in metas).isoformat(),
            max(m["created_ts"] for m in metas).isoformat(),
        ],
        "sim_parameters": {
            "slippage_bps": SIM_SLIPPAGE_BPS,
            "fee_bps": SIM_FEE_BPS,
            "position_usd": SIM_POSITION_USD,
            "max_bars_forward": MAX_BARS_FORWARD,
            "max_entry_delay_bars": MAX_ENTRY_DELAY_BARS,
            "max_fee_r_ratio": MAX_FEE_R_RATIO,
            "min_r_preservation": MIN_R_PRESERVATION,
        },
        "exit_configs": config_specs,
        "configs": summary_data,
    }
    with open(json_path, "w") as f:
        json.dump(json_summary, f, indent=2, default=str)
    log.info("  JSON summary: %s", json_path)
    log.info("  Text output: %s", output_path)


if __name__ == "__main__":
    main()
