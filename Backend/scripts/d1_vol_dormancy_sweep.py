#!/usr/bin/env python3
"""
D1 Vol × Dormancy × Concurrency Sweep — Config D (honest simulator)

Full parameter sweep on pruned_D1_strict_v1 with r_multiple filter removed.
In-memory filtering only — no DB writes.

Dimensions:
  Vol floor (24h quote volume in USD at trigger): $75K, $100K, $125K, $250K, $500K
  Dormancy threshold: >=0.85, >=0.875, >=0.90, >=0.91, >=0.92, >=0.93, >=0.94, >=0.95
  Max concurrent positions: 3, 8, 15

Config: D only (1.5% stop, 10% TP, 3d TS)
120 cells total.

Output: /scripts/output/vol_dormancy_sweep_{timestamp}.json
        /scripts/output/vol_dormancy_sweep_{timestamp}.txt
"""
from __future__ import annotations

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

# ── Constants ──
TF_DELTA = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}
SIM_SLIPPAGE_BPS = 5.0
SIM_FEE_BPS = 4.0
SIM_POSITION_USD = 1000.0
MAX_BARS_FORWARD = 504
MAX_ENTRY_DELAY_BARS = 3
MAX_FEE_R_RATIO = 0.3
MIN_R_PRESERVATION = 0.5

NO_TRAIL = ExitConfig(
    fail_fast_bars=0, fail_fast_threshold=0.0,
    trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
)

# Config D
STOP_PCT = 1.5
TP_PCT = 10.0
TIME_STOP_BARS = {"1h": 72}

# Sweep dimensions
VOL_FLOORS = [75_000, 100_000, 125_000, 250_000, 500_000]
DORMANCY_THRESHOLDS = [0.85, 0.875, 0.90, 0.91, 0.92, 0.93, 0.94, 0.95]
MAX_CONCURRENT = [3, 8, 15]


def simulate_opportunity(
    opp: dict, bars: list[dict],
) -> dict | None:
    """Simulate a single opportunity with Config D. Returns result dict or None."""
    tf = opp["timeframe"]
    direction = opp["direction"]
    entry_price = opp["entry_price"]
    opp_ts = opp["created_ts"]

    if not bars:
        return None

    stop_frac = STOP_PCT / 100.0
    if direction == "long":
        stop_price = entry_price * (1 - stop_frac)
        r_value = entry_price - stop_price
        tp1_price = entry_price * (1 + TP_PCT / 100.0)
    else:
        stop_price = entry_price * (1 + stop_frac)
        r_value = stop_price - entry_price
        tp1_price = entry_price * (1 - TP_PCT / 100.0)

    if r_value <= 0:
        return None

    # Find entry bar
    entry_bar_idx = None
    for i, bar in enumerate(bars):
        bar_ts = bar["bar_start_ts"]
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        if bar_ts > opp_ts:
            entry_bar_idx = i
            break

    if entry_bar_idx is None:
        return None

    bar_delta = TF_DELTA.get(tf, timedelta(hours=1))
    entry_bar = bars[entry_bar_idx]
    entry_bar_ts = entry_bar["bar_start_ts"]
    if entry_bar_ts.tzinfo is None:
        entry_bar_ts = entry_bar_ts.replace(tzinfo=timezone.utc)
    if entry_bar_ts - opp_ts > bar_delta * MAX_ENTRY_DELAY_BARS:
        return None

    actual_entry = entry_bar["open"]
    if direction == "long":
        entry_slip = actual_entry * (1 + SIM_SLIPPAGE_BPS / 10000)
    else:
        entry_slip = actual_entry * (1 - SIM_SLIPPAGE_BPS / 10000)

    if direction == "long":
        actual_r = entry_slip - stop_price
    else:
        actual_r = stop_price - entry_slip
    if actual_r <= 0:
        return None

    fee_per_unit = 2 * entry_slip * SIM_FEE_BPS / 10000
    if fee_per_unit > MAX_FEE_R_RATIO * actual_r:
        return None
    if actual_r < MIN_R_PRESERVATION * r_value:
        return None

    if tp1_price is not None:
        if direction == "long" and tp1_price <= entry_slip:
            tp1_price = None
        elif direction == "short" and tp1_price >= entry_slip:
            tp1_price = None

    ts_bars = TIME_STOP_BARS.get(tf, 72)
    position_size = SIM_POSITION_USD / entry_slip
    max_bar_idx = min(entry_bar_idx + MAX_BARS_FORWARD, len(bars) - 1)

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
            max_bars=ts_bars,
            high_water_mark=hwm, half_r_touched=half_r_touched,
            config=NO_TRAIL,
        )
        hwm = new_hwm
        half_r_touched = new_half_r

        if exit_result:
            exit_reason, exit_price = exit_result
            break

    if exit_reason is None:
        exit_reason = "MAX_BARS"
        exit_price = bars[max_bar_idx]["close"]

    fee_total = 2 * SIM_FEE_BPS / 10000 * SIM_POSITION_USD
    if direction == "long":
        gross_pnl = (exit_price - entry_slip) * position_size
    else:
        gross_pnl = (entry_slip - exit_price) * position_size
    pnl_net = gross_pnl - fee_total

    r_multiple = pnl_net / (actual_r * position_size) if actual_r * position_size > 0 else 0

    return {
        "pnl_net": round(pnl_net, 4),
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "r_multiple": round(r_multiple, 4),
        "entry_ts": entry_bar_ts,
        "exit_ts": bars[min(entry_bar_idx + bars_held, len(bars) - 1)]["bar_start_ts"],
    }


def compute_24h_volume(bars: list[dict], trigger_ts: datetime) -> float | None:
    """Compute 24h quote volume (sum of volume*close for 24 1h bars before trigger)."""
    # Find the bar at or just before trigger_ts
    trigger_idx = None
    for i, bar in enumerate(bars):
        bar_ts = bar["bar_start_ts"]
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        if bar_ts >= trigger_ts:
            trigger_idx = i
            break

    if trigger_idx is None or trigger_idx < 24:
        return None

    # Sum volume * close for the 24 bars ending at trigger bar (exclusive of trigger)
    total = 0.0
    for i in range(trigger_idx - 24, trigger_idx):
        total += bars[i]["volume"] * bars[i]["close"]
    return total


def compute_cell_metrics(
    trades: list[dict],
    max_concurrent: int,
) -> dict:
    """Compute metrics for a single cell, applying max concurrent position limit."""
    if not trades:
        return {"n": 0, "wr": 0, "agg_pf": 0, "daily_pf": 0, "pnl": 0, "max_dd": 0, "gap_through_rate": 0}

    # Sort trades by entry time for concurrency filtering
    sorted_trades = sorted(trades, key=lambda t: t["entry_ts"])

    # Apply concurrency limit
    active_positions = []  # list of exit_ts
    accepted_trades = []

    for trade in sorted_trades:
        entry_ts = trade["entry_ts"]
        # Remove expired positions
        active_positions = [et for et in active_positions if et > entry_ts]

        if len(active_positions) < max_concurrent:
            accepted_trades.append(trade)
            active_positions.append(trade["exit_ts"])

    if not accepted_trades:
        return {"n": 0, "wr": 0, "agg_pf": 0, "daily_pf": 0, "pnl": 0, "max_dd": 0, "gap_through_rate": 0}

    pnls = [t["pnl_net"] for t in accepted_trades]
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls) * 100

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    agg_pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Daily PF
    daily_pnls: dict[str, list[float]] = defaultdict(list)
    for t in accepted_trades:
        ts = t["entry_ts"]
        day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else str(ts)[:10]
        daily_pnls[day].append(t["pnl_net"])

    daily_pfs = []
    for _, day_pnl_list in sorted(daily_pnls.items()):
        gp = sum(p for p in day_pnl_list if p > 0)
        gl = abs(sum(p for p in day_pnl_list if p < 0))
        if gl > 0:
            daily_pfs.append(min(gp / gl, 5.0))
        elif gp > 0:
            daily_pfs.append(5.0)
    daily_pf = float(np.mean(daily_pfs)) if daily_pfs else 0.0

    total_pnl = sum(pnls)

    # Max drawdown
    equity_curve = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity_curve)
    drawdowns = running_max - equity_curve
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # Gap-through rate: % of stop-loss exits where r_multiple < -1.0
    stop_losses = [t for t in accepted_trades if t["exit_reason"] == "STOP_LOSS"]
    gap_throughs = [t for t in stop_losses if t["r_multiple"] < -1.0]
    gap_rate = len(gap_throughs) / len(stop_losses) * 100 if stop_losses else 0.0

    return {
        "n": len(accepted_trades),
        "wr": round(wr, 1),
        "agg_pf": round(agg_pf, 2),
        "daily_pf": round(daily_pf, 2),
        "pnl": round(total_pnl, 0),
        "max_dd": round(max_dd, 0),
        "gap_through_rate": round(gap_rate, 1),
    }


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_json = f"/scripts/output/vol_dormancy_sweep_{ts}.json"
    output_txt = f"/scripts/output/vol_dormancy_sweep_{ts}.txt"
    log.info(f"JSON output: {output_json}")
    log.info(f"Text output: {output_txt}")

    db_url = os.environ.get("DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading")
    conn = psycopg.connect(db_url, row_factory=dict_row)

    # Load opportunities with metadata
    log.info("Loading pruned_D1_strict_v1 opportunities...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, symbol, timeframe, created_ts,
                   payload_json::jsonb->>'direction' AS direction,
                   (payload_json::jsonb->>'entry_price')::float AS entry_price,
                   (payload_json::jsonb->>'dormancy_score')::float AS dormancy_score,
                   (payload_json::jsonb->>'volume_z_at_trigger')::float AS volume_z
            FROM opportunities
            WHERE batch_id = 'pruned_D1_strict_v1'
              AND setup_id = 'D1'
              AND status = 'expired'
            ORDER BY created_ts ASC
        """)
        opps = cur.fetchall()
    log.info(f"  Loaded {len(opps)} opportunities")

    # Load OHLCV
    symbols_tfs = set()
    for opp in opps:
        symbols_tfs.add((opp["symbol"], opp["timeframe"]))

    log.info(f"  Loading OHLCV for {len(symbols_tfs)} symbol/tf combos...")
    ohlcv_cache = {}
    loaded = 0
    with conn.cursor() as cur:
        for symbol, tf in symbols_tfs:
            cache_key = f"{symbol}_{tf}"
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = %s AND timeframe = %s
                ORDER BY bar_start_ts ASC
            """, (symbol, tf))
            ohlcv_cache[cache_key] = cur.fetchall()
            loaded += 1
            if loaded % 50 == 0:
                log.info(f"    ... {loaded}/{len(symbols_tfs)} loaded")
    log.info(f"  OHLCV loaded ({len(ohlcv_cache)} combos)")
    conn.close()

    # Simulate all opportunities once (Config D)
    log.info("Simulating all opportunities with Config D (1.5% stop, 10% TP)...")
    all_trades = []  # list of (opp_meta, sim_result)

    for i, opp in enumerate(opps):
        cache_key = f"{opp['symbol']}_{opp['timeframe']}"
        bars = ohlcv_cache.get(cache_key, [])

        result = simulate_opportunity(opp, bars)
        if result is None:
            continue

        # Compute 24h volume at trigger time
        vol_24h = compute_24h_volume(bars, opp["created_ts"])

        all_trades.append({
            "sim": result,
            "dormancy": opp["dormancy_score"],
            "vol_24h": vol_24h,
            "symbol": opp["symbol"],
        })

        if (i + 1) % 100 == 0:
            log.info(f"  ... {i+1}/{len(opps)} processed, {len(all_trades)} trades")

    log.info(f"  Total simulated trades: {len(all_trades)}")

    # Report volume distribution
    vols = [t["vol_24h"] for t in all_trades if t["vol_24h"] is not None]
    if vols:
        log.info(f"  24h volume distribution:")
        log.info(f"    Min: ${min(vols):,.0f}")
        log.info(f"    P25: ${np.percentile(vols, 25):,.0f}")
        log.info(f"    Median: ${np.median(vols):,.0f}")
        log.info(f"    P75: ${np.percentile(vols, 75):,.0f}")
        log.info(f"    Max: ${max(vols):,.0f}")
        no_vol = sum(1 for t in all_trades if t["vol_24h"] is None)
        log.info(f"    No volume data: {no_vol}")

    # Run the sweep
    log.info(f"\nRunning sweep: {len(VOL_FLOORS)} vol × {len(DORMANCY_THRESHOLDS)} dorm × {len(MAX_CONCURRENT)} conc = {len(VOL_FLOORS)*len(DORMANCY_THRESHOLDS)*len(MAX_CONCURRENT)} cells")

    cube = {}  # (vol_floor, dorm_thresh, max_conc) -> metrics

    for vol_floor in VOL_FLOORS:
        for dorm_thresh in DORMANCY_THRESHOLDS:
            # Filter trades by vol and dormancy
            filtered = []
            for t in all_trades:
                if t["dormancy"] is None or t["dormancy"] < dorm_thresh:
                    continue
                if t["vol_24h"] is not None and t["vol_24h"] < vol_floor:
                    continue
                # If vol_24h is None (no data), include it (conservative — don't exclude unknowns)
                filtered.append(t["sim"])

            for max_conc in MAX_CONCURRENT:
                metrics = compute_cell_metrics(filtered, max_conc)
                cube[(vol_floor, dorm_thresh, max_conc)] = metrics

    log.info(f"  Sweep complete: {len(cube)} cells computed")

    # ── Text output ──
    lines = []
    lines.append("=" * 100)
    lines.append("D1 CONFIG D — Vol × Dormancy × Concurrency Sweep (HONEST — no r_multiple filter)")
    lines.append("=" * 100)
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Config: D (1.5% stop, 10% TP, 3d time stop)")
    lines.append(f"Batch: pruned_D1_strict_v1 ({len(opps)} opps, {len(all_trades)} simulated)")
    lines.append(f"Cells: {len(VOL_FLOORS)} vol × {len(DORMANCY_THRESHOLDS)} dorm × {len(MAX_CONCURRENT)} conc = {len(cube)}")
    lines.append("")

    # Print 2D slices — one per concurrency level
    for max_conc in MAX_CONCURRENT:
        lines.append("-" * 100)
        lines.append(f"  Max Concurrent = {max_conc}")
        lines.append("-" * 100)

        # Header
        hdr = f"{'Vol Floor':>12s}"
        for dorm in DORMANCY_THRESHOLDS:
            hdr += f" | d>={dorm:.3f}"
        lines.append(hdr)
        lines.append("-" * len(hdr))

        for vol in VOL_FLOORS:
            row = f"${vol/1000:.0f}K".rjust(12)
            for dorm in DORMANCY_THRESHOLDS:
                m = cube[(vol, dorm, max_conc)]
                if m["n"] == 0:
                    cell = "  —  "
                else:
                    pf_str = f"{m['agg_pf']:.2f}"
                    if m["n"] < 30:
                        pf_str += "*"
                    cell = f"{pf_str:>6s}"
                row += f" | {cell:>8s}"
            lines.append(row)

        lines.append("")
        lines.append("  (* = N < 30, treat with caution)")
        lines.append("")

        # Also print N counts
        lines.append(f"  N counts (Max Concurrent = {max_conc}):")
        hdr_n = f"{'Vol Floor':>12s}"
        for dorm in DORMANCY_THRESHOLDS:
            hdr_n += f" | d>={dorm:.3f}"
        lines.append(hdr_n)
        lines.append("-" * len(hdr_n))

        for vol in VOL_FLOORS:
            row = f"${vol/1000:.0f}K".rjust(12)
            for dorm in DORMANCY_THRESHOLDS:
                m = cube[(vol, dorm, max_conc)]
                row += f" | {m['n']:>8d}"
            lines.append(row)
        lines.append("")

    # Top 10 cells
    lines.append("=" * 100)
    lines.append("TOP 10 CELLS (N>=50, gap_through_rate<15%, ranked by aggPF)")
    lines.append("=" * 100)

    candidates = []
    for (vol, dorm, conc), m in cube.items():
        if m["n"] >= 50 and m["gap_through_rate"] < 15:
            candidates.append((vol, dorm, conc, m))

    candidates.sort(key=lambda x: -x[3]["agg_pf"])

    if not candidates:
        lines.append("  No cells meet criteria (N>=50, gap_through_rate<15%)")
        # Relax and show best available
        lines.append("")
        lines.append("  Relaxed: Top 10 by aggPF where N>=30:")
        candidates_relaxed = []
        for (vol, dorm, conc), m in cube.items():
            if m["n"] >= 30:
                candidates_relaxed.append((vol, dorm, conc, m))
        candidates_relaxed.sort(key=lambda x: -x[3]["agg_pf"])
        for vol, dorm, conc, m in candidates_relaxed[:10]:
            lines.append(f"  vol>=${vol/1000:.0f}K  dorm>={dorm:.3f}  conc={conc:2d}  |  "
                          f"N={m['n']:4d}  WR={m['wr']:5.1f}%  aggPF={m['agg_pf']:5.2f}  "
                          f"dailyPF={m['daily_pf']:5.2f}  PnL=${m['pnl']:>7.0f}  "
                          f"maxDD=${m['max_dd']:>6.0f}  gap={m['gap_through_rate']:4.1f}%")
    else:
        for vol, dorm, conc, m in candidates[:10]:
            lines.append(f"  vol>=${vol/1000:.0f}K  dorm>={dorm:.3f}  conc={conc:2d}  |  "
                          f"N={m['n']:4d}  WR={m['wr']:5.1f}%  aggPF={m['agg_pf']:5.2f}  "
                          f"dailyPF={m['daily_pf']:5.2f}  PnL=${m['pnl']:>7.0f}  "
                          f"maxDD=${m['max_dd']:>6.0f}  gap={m['gap_through_rate']:4.1f}%")

    lines.append("")
    lines.append("=" * 100)

    report = "\n".join(lines)
    log.info("\n" + report)

    # Write files
    os.makedirs(os.path.dirname(output_txt), exist_ok=True)
    with open(output_txt, "w") as f:
        f.write(report)
    log.info(f"Text output: {output_txt}")

    # JSON output
    json_cube = {}
    for (vol, dorm, conc), m in cube.items():
        key = f"vol{vol}_dorm{dorm}_conc{conc}"
        json_cube[key] = {
            "vol_floor": vol,
            "dormancy_min": dorm,
            "max_concurrent": conc,
            **m,
        }

    output = {
        "sweep": "D1_VOL_DORMANCY_CONCURRENCY",
        "timestamp": ts,
        "config": {"stop_pct": STOP_PCT, "tp_pct": TP_PCT},
        "batch_id": "pruned_D1_strict_v1",
        "n_opps": len(opps),
        "n_simulated": len(all_trades),
        "dimensions": {
            "vol_floors": VOL_FLOORS,
            "dormancy_thresholds": DORMANCY_THRESHOLDS,
            "max_concurrent": MAX_CONCURRENT,
        },
        "cube": json_cube,
        "top_10": [
            {
                "vol_floor": vol,
                "dormancy_min": dorm,
                "max_concurrent": conc,
                **m,
            }
            for vol, dorm, conc, m in (candidates if candidates else candidates_relaxed)[:10]
        ] if candidates or candidates_relaxed else [],
    }

    with open(output_json, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log.info(f"JSON output: {output_json}")


if __name__ == "__main__":
    main()
