#!/usr/bin/env python3
"""
Audit 1: N Discrepancy — Why does Config D trade N=188 vs Config A's N=510?

Traces every opportunity through the rejection filters for both configs,
identifies which filter causes each drop, and simulates dropped opps with
filters bypassed to check for selection bias.

Output: /scripts/output/audit_n_discrepancy_{timestamp}.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
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

# ── Constants (must match sim_d1_matrix.py exactly) ──
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

# Config D parameters
CONFIG_D = {"stop_pct": 1.5, "tp_pct": 10.0, "ts": {"1h": 72}}
# Config A parameters
CONFIG_A = {"stop_pct": 2.5, "tp_pct": 10.0, "ts": {"1h": 72}}


def diagnose_opportunity(
    opp_ts: datetime, direction: str, entry_price: float,
    bars: list[dict], tf: str,
    stop_pct: float, tp_pct: Optional[float],
    time_stop_bars: int,
) -> dict:
    """Trace a single opportunity through all rejection filters.
    Returns diagnosis dict with rejection_reason or 'ACCEPTED'."""

    result = {
        "rejection_reason": None,
        "rejection_line": None,
        "details": {},
    }

    if not bars:
        result["rejection_reason"] = "NO_BARS"
        result["rejection_line"] = 271
        return result

    stop_frac = stop_pct / 100.0
    if direction == "long":
        stop_price = entry_price * (1 - stop_frac)
        r_value = entry_price - stop_price
        tp1_price = entry_price * (1 + tp_pct / 100.0) if tp_pct else None
    else:
        stop_price = entry_price * (1 + stop_frac)
        r_value = stop_price - entry_price
        tp1_price = entry_price * (1 - tp_pct / 100.0) if tp_pct else None

    result["details"]["stop_price"] = stop_price
    result["details"]["r_value"] = round(r_value, 6)
    result["details"]["tp1_price"] = tp1_price

    if r_value <= 0:
        result["rejection_reason"] = "R_VALUE_ZERO"
        result["rejection_line"] = 284
        return result

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
        result["rejection_reason"] = "NO_ENTRY_BAR"
        result["rejection_line"] = 297
        return result

    bar_delta = TF_DELTA.get(tf, timedelta(hours=1))
    entry_bar = bars[entry_bar_idx]
    entry_bar_ts = entry_bar["bar_start_ts"]
    if entry_bar_ts.tzinfo is None:
        entry_bar_ts = entry_bar_ts.replace(tzinfo=timezone.utc)
    if entry_bar_ts - opp_ts > bar_delta * MAX_ENTRY_DELAY_BARS:
        result["rejection_reason"] = "ENTRY_DELAY_EXCEEDED"
        result["rejection_line"] = 305
        result["details"]["delay_bars"] = (entry_bar_ts - opp_ts) / bar_delta
        return result

    actual_entry = entry_bar["open"]
    if direction == "long":
        entry_slip = actual_entry * (1 + SIM_SLIPPAGE_BPS / 10000)
    else:
        entry_slip = actual_entry * (1 - SIM_SLIPPAGE_BPS / 10000)

    result["details"]["actual_entry"] = actual_entry
    result["details"]["entry_slip"] = round(entry_slip, 6)

    if direction == "long":
        actual_r = entry_slip - stop_price
    else:
        actual_r = stop_price - entry_slip

    result["details"]["actual_r"] = round(actual_r, 6)

    if actual_r <= 0:
        result["rejection_reason"] = "ACTUAL_R_NEGATIVE"
        result["rejection_line"] = 318
        result["details"]["explanation"] = "Slippage pushed entry past stop"
        return result

    fee_per_unit = 2 * entry_slip * SIM_FEE_BPS / 10000
    fee_r_ratio = fee_per_unit / actual_r
    result["details"]["fee_per_unit"] = round(fee_per_unit, 6)
    result["details"]["fee_r_ratio"] = round(fee_r_ratio, 4)

    if fee_per_unit > MAX_FEE_R_RATIO * actual_r:
        result["rejection_reason"] = "FEE_R_RATIO_EXCEEDED"
        result["rejection_line"] = 322
        result["details"]["threshold"] = MAX_FEE_R_RATIO
        return result

    r_preservation = actual_r / r_value if r_value > 0 else 0
    result["details"]["r_preservation"] = round(r_preservation, 4)

    if actual_r < MIN_R_PRESERVATION * r_value:
        result["rejection_reason"] = "R_PRESERVATION_FAILED"
        result["rejection_line"] = 325
        result["details"]["threshold"] = MIN_R_PRESERVATION
        return result

    result["rejection_reason"] = "ACCEPTED"
    return result


def simulate_opportunity_no_filter(
    opp_ts: datetime, direction: str, entry_price: float,
    bars: list[dict], tf: str,
    stop_pct: float, tp_pct: Optional[float],
    time_stop_bars: int, exit_config: ExitConfig,
    slippage_bps: float = SIM_SLIPPAGE_BPS,
) -> Optional[dict]:
    """Simulate without fee/R-preservation filters. Returns result or None if truly no data."""
    if not bars:
        return None

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
        return None

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
        entry_slip = actual_entry * (1 + slippage_bps / 10000)
    else:
        entry_slip = actual_entry * (1 - slippage_bps / 10000)

    if direction == "long":
        actual_r = entry_slip - stop_price
    else:
        actual_r = stop_price - entry_slip
    if actual_r <= 0:
        return None

    # SKIP the fee and R-preservation filters — that's the point

    if tp1_price is not None:
        if direction == "long" and tp1_price <= entry_slip:
            tp1_price = None
        elif direction == "short" and tp1_price >= entry_slip:
            tp1_price = None

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
        exit_reason = "MAX_BARS"
        exit_price = bars[max_bar_idx]["close"]

    fee_total = 2 * SIM_FEE_BPS / 10000 * SIM_POSITION_USD
    if direction == "long":
        gross_pnl = (exit_price - entry_slip) * position_size
    else:
        gross_pnl = (entry_slip - exit_price) * position_size
    pnl_net = gross_pnl - fee_total

    return {
        "pnl_net": round(pnl_net, 4),
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "entry_slip": round(entry_slip, 6),
        "exit_price": round(exit_price, 6),
    }


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"/scripts/output/audit_n_discrepancy_{ts}.json"
    log.info(f"Output: {output_path}")

    db_url = os.environ.get("DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading")
    conn = psycopg.connect(db_url, row_factory=dict_row)

    # Load pruned batch
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
    log.info(f"  OHLCV loaded ({len(ohlcv_cache)} combos)")

    # Diagnose every opportunity for both Config A and Config D
    log.info("Diagnosing all opportunities for Config A and Config D...")

    config_a_diag = []
    config_d_diag = []

    for opp in opps:
        cache_key = f"{opp['symbol']}_{opp['timeframe']}"
        bars = ohlcv_cache.get(cache_key, [])
        tf = opp["timeframe"]
        ts_bars_a = CONFIG_A["ts"].get(tf, 72)
        ts_bars_d = CONFIG_D["ts"].get(tf, 72)

        diag_a = diagnose_opportunity(
            opp_ts=opp["created_ts"], direction=opp["direction"],
            entry_price=opp["entry_price"], bars=bars, tf=tf,
            stop_pct=CONFIG_A["stop_pct"], tp_pct=CONFIG_A["tp_pct"],
            time_stop_bars=ts_bars_a,
        )
        diag_a["symbol"] = opp["symbol"]
        diag_a["created_ts"] = opp["created_ts"].isoformat()
        diag_a["opp_id"] = opp["id"]
        config_a_diag.append(diag_a)

        diag_d = diagnose_opportunity(
            opp_ts=opp["created_ts"], direction=opp["direction"],
            entry_price=opp["entry_price"], bars=bars, tf=tf,
            stop_pct=CONFIG_D["stop_pct"], tp_pct=CONFIG_D["tp_pct"],
            time_stop_bars=ts_bars_d,
        )
        diag_d["symbol"] = opp["symbol"]
        diag_d["created_ts"] = opp["created_ts"].isoformat()
        diag_d["opp_id"] = opp["id"]
        config_d_diag.append(diag_d)

    # Count by rejection reason
    a_reasons = Counter(d["rejection_reason"] for d in config_a_diag)
    d_reasons = Counter(d["rejection_reason"] for d in config_d_diag)

    log.info(f"\nConfig A rejection breakdown (of {len(opps)} opps):")
    for reason, count in sorted(a_reasons.items(), key=lambda x: -x[1]):
        log.info(f"  {reason}: {count}")

    log.info(f"\nConfig D rejection breakdown (of {len(opps)} opps):")
    for reason, count in sorted(d_reasons.items(), key=lambda x: -x[1]):
        log.info(f"  {reason}: {count}")

    # Identify opps accepted by A but rejected by D
    a_accepted = {d["opp_id"] for d in config_a_diag if d["rejection_reason"] == "ACCEPTED"}
    d_accepted = {d["opp_id"] for d in config_d_diag if d["rejection_reason"] == "ACCEPTED"}
    d_rejected = {d["opp_id"] for d in config_d_diag if d["rejection_reason"] != "ACCEPTED"}

    a_only = a_accepted - d_accepted  # Accepted by A, rejected by D

    log.info(f"\nConfig A accepted: {len(a_accepted)}")
    log.info(f"Config D accepted: {len(d_accepted)}")
    log.info(f"Accepted by A but rejected by D: {len(a_only)}")

    # Rejection reasons for the A-only set
    a_only_reasons = Counter()
    a_only_details = []
    for d in config_d_diag:
        if d["opp_id"] in a_only:
            a_only_reasons[d["rejection_reason"]] += 1
            a_only_details.append(d)

    log.info(f"\nWhy Config D rejected the {len(a_only)} opps that Config A accepted:")
    for reason, count in sorted(a_only_reasons.items(), key=lambda x: -x[1]):
        pct = 100.0 * count / len(a_only) if a_only else 0
        log.info(f"  {reason}: {count} ({pct:.1f}%)")

    # Sample 10 rejected opps with details
    sample = a_only_details[:10]
    log.info(f"\nSample of 10 rejected (by D) opportunities:")
    for s in sample:
        det = s["details"]
        log.info(f"  {s['symbol']} @ {s['created_ts']}")
        log.info(f"    reason={s['rejection_reason']} line={s['rejection_line']}")
        log.info(f"    r_value={det.get('r_value')}, actual_r={det.get('actual_r')}")
        log.info(f"    fee_r_ratio={det.get('fee_r_ratio')}, r_preservation={det.get('r_preservation')}")

    # Simulate dropped opps with filters bypassed
    log.info(f"\nSimulating {len(a_only)} dropped opps with Config D params, filters BYPASSED...")

    dropped_opp_map = {opp["id"]: opp for opp in opps}
    bypassed_results = []
    bypassed_failures = 0

    for opp_id in sorted(a_only):
        opp = dropped_opp_map[opp_id]
        cache_key = f"{opp['symbol']}_{opp['timeframe']}"
        bars = ohlcv_cache.get(cache_key, [])
        tf = opp["timeframe"]
        ts_bars = CONFIG_D["ts"].get(tf, 72)

        sim_result = simulate_opportunity_no_filter(
            opp_ts=opp["created_ts"], direction=opp["direction"],
            entry_price=opp["entry_price"], bars=bars, tf=tf,
            stop_pct=CONFIG_D["stop_pct"], tp_pct=CONFIG_D["tp_pct"],
            time_stop_bars=ts_bars, exit_config=NO_TRAIL,
        )
        if sim_result is None:
            bypassed_failures += 1
        else:
            bypassed_results.append(sim_result)

    # Compute PF for bypassed results
    if bypassed_results:
        pnls = [r["pnl_net"] for r in bypassed_results]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        bypassed_pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        bypassed_wr = len(wins) / len(pnls) * 100 if pnls else 0
        bypassed_pnl = sum(pnls)

        exit_reasons = Counter(r["exit_reason"] for r in bypassed_results)

        log.info(f"\nBYPASSED FILTER RESULTS (dropped opps simulated with Config D):")
        log.info(f"  N simulated: {len(bypassed_results)} (+ {bypassed_failures} truly unsimulable)")
        log.info(f"  Win rate: {bypassed_wr:.1f}%")
        log.info(f"  Aggregate PF: {bypassed_pf:.2f}")
        log.info(f"  Total PnL: ${bypassed_pnl:.2f}")
        log.info(f"  Avg PnL: ${bypassed_pnl/len(bypassed_results):.2f}")
        log.info(f"  Exit reasons: {dict(exit_reasons)}")

        # Also compute combined (accepted + bypassed) PF
        log.info(f"\nIMPLICATION:")
        if bypassed_pf < 1.0:
            log.info(f"  Dropped opps have PF={bypassed_pf:.2f} (< 1.0) — filter is doing LEGITIMATE work")
            log.info(f"  The 6.08 PF is real within scope. The filter correctly excludes losing trades.")
        elif bypassed_pf >= 1.0:
            log.info(f"  Dropped opps have PF={bypassed_pf:.2f} (>= 1.0) — POTENTIAL SELECTION BIAS")
            log.info(f"  The filter may be excluding profitable trades. True PF would be lower.")
    else:
        log.info("\nNo dropped opps could be simulated with bypassed filters.")
        bypassed_pf = None
        bypassed_wr = None
        bypassed_pnl = None
        exit_reasons = {}

    # Build output JSON
    output = {
        "audit": "N_DISCREPANCY",
        "timestamp": ts,
        "batch_id": "pruned_D1_strict_v1",
        "total_opps": len(opps),
        "config_a": {
            "rejection_breakdown": dict(a_reasons),
            "n_accepted": len(a_accepted),
        },
        "config_d": {
            "rejection_breakdown": dict(d_reasons),
            "n_accepted": len(d_accepted),
        },
        "a_only_rejected_by_d": {
            "count": len(a_only),
            "rejection_reasons": dict(a_only_reasons),
            "sample_10": [
                {
                    "symbol": s["symbol"],
                    "created_ts": s["created_ts"],
                    "rejection_reason": s["rejection_reason"],
                    "rejection_line": s["rejection_line"],
                    "details": {k: v for k, v in s["details"].items()},
                }
                for s in sample
            ],
        },
        "bypassed_simulation": {
            "n_simulated": len(bypassed_results),
            "n_unsimulable": bypassed_failures,
            "win_rate": round(bypassed_wr, 2) if bypassed_wr is not None else None,
            "agg_pf": round(bypassed_pf, 4) if bypassed_pf is not None else None,
            "total_pnl": round(bypassed_pnl, 2) if bypassed_pnl is not None else None,
            "exit_reasons": dict(exit_reasons),
        },
        "verdict": None,
    }

    if bypassed_pf is not None:
        if bypassed_pf < 1.0:
            output["verdict"] = f"FILTER_LEGITIMATE: Dropped opps PF={bypassed_pf:.2f}. The 6.08 is real within scope."
        else:
            output["verdict"] = f"SELECTION_BIAS_RISK: Dropped opps PF={bypassed_pf:.2f}. True combined PF would be lower."

    # Write output
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log.info(f"\nOutput written to {output_path}")

    conn.close()


if __name__ == "__main__":
    main()
