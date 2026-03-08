#!/usr/bin/env python3
"""
Audit 3: Stop Fill Realism — Gap-Through Analysis for D1 Config D (1.5% stop)

For each of the 78 stop-loss exits in Config D, examines the OHLCV bar where
the stop triggered and measures how far the wick penetrated past the stop price.

If real-world slippage gaps through stops, a simulated 1.5% loss becomes a
larger actual loss. This audit quantifies that risk.

Metrics per stop-loss hit:
  - stop_price: the simulated stop level
  - bar_low (long) / bar_high (short): worst fill on the trigger bar
  - gap_through_pct: how far past the stop the wick went (% of entry)
  - realistic_loss_pct: actual loss if filled at the wick extreme
  - extra_slippage_bps: additional slippage beyond the stop price

Output: /scripts/output/audit_stop_fill_realism_{timestamp}.json
        /scripts/output/audit_stop_fill_realism_{timestamp}.txt
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

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

# ── Constants (must match sim_d1_matrix.py) ──
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
STOP_PCT = 1.5
TP_PCT = 10.0
TIME_STOP_BARS = {"1h": 72}


def simulate_and_trace_stop(
    opp: dict, bars: list[dict],
) -> dict | None:
    """Re-simulate a single opportunity with Config D and, if it hits a stop,
    return the trigger bar details for gap-through analysis."""

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

    # Apply rejection filters (same as sim_d1_matrix)
    fee_per_unit = 2 * entry_slip * SIM_FEE_BPS / 10000
    if fee_per_unit > MAX_FEE_R_RATIO * actual_r:
        return None
    if actual_r < MIN_R_PRESERVATION * r_value:
        return None

    if direction == "long" and tp1_price <= entry_slip:
        tp1_price = None
    elif direction == "short" and tp1_price >= entry_slip:
        tp1_price = None

    ts_bars = TIME_STOP_BARS.get(tf, 72)
    max_bar_idx = min(entry_bar_idx + MAX_BARS_FORWARD, len(bars) - 1)

    hwm = None
    half_r_touched = False
    exit_reason = None
    exit_price = None
    bars_held = 0
    trigger_bar = None

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
            trigger_bar = bar
            break

    if exit_reason is None:
        exit_reason = "MAX_BARS"
        exit_price = bars[max_bar_idx]["close"]

    if exit_reason != "STOP_LOSS":
        return None  # Only interested in stop-loss hits

    # Gap-through analysis on the trigger bar
    bar_low = trigger_bar["low"]
    bar_high = trigger_bar["high"]
    bar_open = trigger_bar["open"]
    bar_close = trigger_bar["close"]
    bar_volume = trigger_bar["volume"]
    bar_ts = trigger_bar["bar_start_ts"]
    if hasattr(bar_ts, 'isoformat'):
        bar_ts_str = bar_ts.isoformat()
    else:
        bar_ts_str = str(bar_ts)

    if direction == "long":
        # Stop is below entry. Worst fill = bar low
        worst_fill = bar_low
        gap_through_abs = stop_price - bar_low  # positive = gapped past
        gap_through_pct = gap_through_abs / entry_slip * 100
        sim_loss_pct = (entry_slip - stop_price) / entry_slip * 100
        realistic_loss_pct = (entry_slip - worst_fill) / entry_slip * 100
        # Bar range context
        bar_range_pct = (bar_high - bar_low) / bar_open * 100
    else:
        # Short: stop is above entry. Worst fill = bar high
        worst_fill = bar_high
        gap_through_abs = bar_high - stop_price  # positive = gapped past
        gap_through_pct = gap_through_abs / entry_slip * 100
        sim_loss_pct = (stop_price - entry_slip) / entry_slip * 100
        realistic_loss_pct = (worst_fill - entry_slip) / entry_slip * 100
        bar_range_pct = (bar_high - bar_low) / bar_open * 100

    extra_slippage_bps = gap_through_pct * 100  # convert % to bps

    fee_total = 2 * SIM_FEE_BPS / 10000 * SIM_POSITION_USD
    sim_pnl = -sim_loss_pct / 100 * SIM_POSITION_USD - fee_total
    realistic_pnl = -realistic_loss_pct / 100 * SIM_POSITION_USD - fee_total

    return {
        "symbol": opp["symbol"],
        "timeframe": tf,
        "direction": direction,
        "created_ts": opp["created_ts"].isoformat() if hasattr(opp["created_ts"], 'isoformat') else str(opp["created_ts"]),
        "entry_slip": round(entry_slip, 8),
        "stop_price": round(stop_price, 8),
        "bar_ts": bar_ts_str,
        "bar_open": round(bar_open, 8),
        "bar_high": round(bar_high, 8),
        "bar_low": round(bar_low, 8),
        "bar_close": round(bar_close, 8),
        "bar_volume": round(bar_volume, 2),
        "bar_range_pct": round(bar_range_pct, 4),
        "worst_fill": round(worst_fill, 8),
        "gap_through_abs": round(gap_through_abs, 8),
        "gap_through_pct": round(gap_through_pct, 4),
        "extra_slippage_bps": round(extra_slippage_bps, 2),
        "sim_loss_pct": round(sim_loss_pct, 4),
        "realistic_loss_pct": round(realistic_loss_pct, 4),
        "sim_pnl": round(sim_pnl, 4),
        "realistic_pnl": round(realistic_pnl, 4),
        "pnl_degradation": round(realistic_pnl - sim_pnl, 4),
        "bars_held": bars_held,
    }


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_json = f"/scripts/output/audit_stop_fill_realism_{ts}.json"
    output_txt = f"/scripts/output/audit_stop_fill_realism_{ts}.txt"
    log.info(f"JSON output: {output_json}")
    log.info(f"Text output: {output_txt}")

    db_url = os.environ.get("DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading")
    conn = psycopg.connect(db_url, row_factory=dict_row)

    # Load opportunities
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

    # Simulate all opps, collect stop-loss hits
    log.info("Re-simulating all opportunities with Config D (1.5% stop)...")
    stop_hits = []
    non_stop = 0
    skipped = 0

    for i, opp in enumerate(opps):
        cache_key = f"{opp['symbol']}_{opp['timeframe']}"
        bars = ohlcv_cache.get(cache_key, [])
        result = simulate_and_trace_stop(opp, bars)
        if result is None:
            # Either rejected by filters or not a stop-loss exit
            skipped += 1
        else:
            stop_hits.append(result)

        if (i + 1) % 100 == 0:
            log.info(f"  ... {i+1}/{len(opps)} processed, {len(stop_hits)} stop hits so far")

    log.info(f"  Done: {len(stop_hits)} stop-loss hits found (expected ~78)")

    if not stop_hits:
        log.error("No stop-loss hits found — cannot proceed with analysis")
        return

    # ── Analysis ──
    gap_throughs = [h["gap_through_pct"] for h in stop_hits]
    gap_throughs_bps = [h["extra_slippage_bps"] for h in stop_hits]
    sim_pnls = [h["sim_pnl"] for h in stop_hits]
    realistic_pnls = [h["realistic_pnl"] for h in stop_hits]
    degradations = [h["pnl_degradation"] for h in stop_hits]
    bar_ranges = [h["bar_range_pct"] for h in stop_hits]
    realistic_losses = [h["realistic_loss_pct"] for h in stop_hits]

    # Sort by gap-through severity
    stop_hits_sorted = sorted(stop_hits, key=lambda x: -x["gap_through_pct"])

    # Bucketing
    zero_gap = [h for h in stop_hits if h["gap_through_pct"] <= 0.01]
    small_gap = [h for h in stop_hits if 0.01 < h["gap_through_pct"] <= 0.5]
    medium_gap = [h for h in stop_hits if 0.5 < h["gap_through_pct"] <= 1.5]
    large_gap = [h for h in stop_hits if h["gap_through_pct"] > 1.5]

    # Impact on PF
    total_sim_stop_loss = sum(sim_pnls)
    total_realistic_stop_loss = sum(realistic_pnls)
    total_degradation = sum(degradations)

    # Compute what this does to Config D's overall PF
    # Config D: $7471 total PnL, 188 trades, 78 stops, PF=6.08
    # gross_profit / gross_loss = 6.08, so gross_loss = gross_profit / 6.08
    # total_pnl = gross_profit - gross_loss = 7471
    # gross_profit = 7471 * 6.08 / (6.08 - 1) = 7471 * 6.08 / 5.08
    config_d_pnl = 7471.0
    config_d_pf = 6.08
    gross_profit = config_d_pnl * config_d_pf / (config_d_pf - 1)
    gross_loss = gross_profit / config_d_pf
    adjusted_gross_loss = gross_loss + abs(total_degradation)
    adjusted_pf = gross_profit / adjusted_gross_loss if adjusted_gross_loss > 0 else float("inf")
    adjusted_pnl = config_d_pnl + total_degradation

    # ── Text Report ──
    lines = []
    lines.append("=" * 80)
    lines.append("AUDIT 3: STOP FILL REALISM — Gap-Through Analysis")
    lines.append("=" * 80)
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Config D: {STOP_PCT}% stop, {TP_PCT}% TP, 1h timeframe")
    lines.append(f"Batch: pruned_D1_strict_v1")
    lines.append(f"Stop-loss hits analyzed: {len(stop_hits)}")
    lines.append("")
    lines.append("-" * 80)
    lines.append("SUMMARY STATISTICS")
    lines.append("-" * 80)
    lines.append(f"  Gap-through (% of entry price):")
    lines.append(f"    Mean:    {np.mean(gap_throughs):.4f}%")
    lines.append(f"    Median:  {np.median(gap_throughs):.4f}%")
    lines.append(f"    P75:     {np.percentile(gap_throughs, 75):.4f}%")
    lines.append(f"    P90:     {np.percentile(gap_throughs, 90):.4f}%")
    lines.append(f"    P95:     {np.percentile(gap_throughs, 95):.4f}%")
    lines.append(f"    Max:     {np.max(gap_throughs):.4f}%")
    lines.append(f"    Min:     {np.min(gap_throughs):.4f}%")
    lines.append("")
    lines.append(f"  Extra slippage beyond stop (bps):")
    lines.append(f"    Mean:    {np.mean(gap_throughs_bps):.1f} bps")
    lines.append(f"    Median:  {np.median(gap_throughs_bps):.1f} bps")
    lines.append(f"    P90:     {np.percentile(gap_throughs_bps, 90):.1f} bps")
    lines.append(f"    Max:     {np.max(gap_throughs_bps):.1f} bps")
    lines.append("")
    lines.append(f"  Trigger bar range (%):")
    lines.append(f"    Mean:    {np.mean(bar_ranges):.4f}%")
    lines.append(f"    Median:  {np.median(bar_ranges):.4f}%")
    lines.append(f"    Max:     {np.max(bar_ranges):.4f}%")
    lines.append("")
    lines.append(f"  Realistic loss (% of entry, incl gap-through):")
    lines.append(f"    Mean:    {np.mean(realistic_losses):.4f}%")
    lines.append(f"    Median:  {np.median(realistic_losses):.4f}%")
    lines.append(f"    P90:     {np.percentile(realistic_losses, 90):.4f}%")
    lines.append(f"    Max:     {np.max(realistic_losses):.4f}%")
    lines.append(f"    (Simulated stop assumes: {STOP_PCT}% loss at stop price)")
    lines.append("")
    lines.append("-" * 80)
    lines.append("GAP-THROUGH BUCKETS")
    lines.append("-" * 80)
    lines.append(f"  Negligible (<=1bp):   {len(zero_gap):3d} / {len(stop_hits)}  ({100*len(zero_gap)/len(stop_hits):.1f}%)")
    lines.append(f"  Small (1bp - 50bp):   {len(small_gap):3d} / {len(stop_hits)}  ({100*len(small_gap)/len(stop_hits):.1f}%)")
    lines.append(f"  Medium (50bp - 150bp):{len(medium_gap):3d} / {len(stop_hits)}  ({100*len(medium_gap)/len(stop_hits):.1f}%)")
    lines.append(f"  Large (>150bp):       {len(large_gap):3d} / {len(stop_hits)}  ({100*len(large_gap)/len(stop_hits):.1f}%)")
    lines.append("")
    lines.append("-" * 80)
    lines.append("PnL IMPACT")
    lines.append("-" * 80)
    lines.append(f"  Total sim stop-loss PnL:       ${total_sim_stop_loss:>10.2f}")
    lines.append(f"  Total realistic stop-loss PnL: ${total_realistic_stop_loss:>10.2f}")
    lines.append(f"  Total degradation:             ${total_degradation:>10.2f}")
    lines.append(f"  Mean degradation per stop:     ${np.mean(degradations):>10.2f}")
    lines.append("")
    lines.append(f"  Config D original:  PF={config_d_pf:.2f}  PnL=${config_d_pnl:.0f}")
    lines.append(f"  Config D adjusted:  PF={adjusted_pf:.2f}  PnL=${adjusted_pnl:.0f}")
    lines.append(f"  PF change: {config_d_pf:.2f} -> {adjusted_pf:.2f} ({(adjusted_pf/config_d_pf - 1)*100:+.1f}%)")
    lines.append("")
    lines.append("-" * 80)
    lines.append("WORST 15 GAP-THROUGHS (sorted by gap_through_pct)")
    lines.append("-" * 80)
    for h in stop_hits_sorted[:15]:
        lines.append(f"  {h['symbol']:15s} {h['bar_ts'][:16]}  dir={h['direction']:5s}  "
                      f"gap={h['gap_through_pct']:+.4f}%  "
                      f"extra={h['extra_slippage_bps']:>6.1f}bps  "
                      f"sim_loss={h['sim_loss_pct']:.2f}%  "
                      f"real_loss={h['realistic_loss_pct']:.2f}%  "
                      f"bar_range={h['bar_range_pct']:.2f}%  "
                      f"degrad=${h['pnl_degradation']:.2f}")
    lines.append("")
    lines.append("-" * 80)
    lines.append("VERDICT")
    lines.append("-" * 80)

    mean_extra_bps = np.mean(gap_throughs_bps)
    p90_extra_bps = np.percentile(gap_throughs_bps, 90)

    if adjusted_pf >= 2.0 and np.percentile(gap_throughs, 95) < 1.5:
        verdict = (f"SURVIVABLE: Adjusted PF={adjusted_pf:.2f} (still >2.0). "
                   f"Mean extra slippage={mean_extra_bps:.0f}bps, P90={p90_extra_bps:.0f}bps. "
                   f"The 1.5% stop is viable for these altcoins.")
    elif adjusted_pf >= 1.0:
        verdict = (f"MARGINAL: Adjusted PF={adjusted_pf:.2f} (>1.0 but degraded). "
                   f"Mean extra slippage={mean_extra_bps:.0f}bps, P90={p90_extra_bps:.0f}bps. "
                   f"Consider widening stop to 2.0% or adding liquidity filter.")
    else:
        verdict = (f"FATAL: Adjusted PF={adjusted_pf:.2f} (<1.0). "
                   f"Gap-through slippage destroys the edge. "
                   f"The 1.5% stop is not viable for illiquid altcoins.")

    lines.append(f"  {verdict}")
    lines.append("")
    lines.append("=" * 80)

    report = "\n".join(lines)
    log.info("\n" + report)

    # Write text output
    os.makedirs(os.path.dirname(output_txt), exist_ok=True)
    with open(output_txt, "w") as f:
        f.write(report)
    log.info(f"Text output: {output_txt}")

    # Build JSON
    output = {
        "audit": "STOP_FILL_REALISM",
        "timestamp": ts,
        "config": {
            "stop_pct": STOP_PCT,
            "tp_pct": TP_PCT,
            "slippage_bps": SIM_SLIPPAGE_BPS,
            "fee_bps": SIM_FEE_BPS,
            "position_usd": SIM_POSITION_USD,
        },
        "batch_id": "pruned_D1_strict_v1",
        "n_stop_hits": len(stop_hits),
        "summary": {
            "gap_through_pct": {
                "mean": round(np.mean(gap_throughs), 4),
                "median": round(np.median(gap_throughs), 4),
                "p75": round(np.percentile(gap_throughs, 75), 4),
                "p90": round(np.percentile(gap_throughs, 90), 4),
                "p95": round(np.percentile(gap_throughs, 95), 4),
                "max": round(np.max(gap_throughs), 4),
                "min": round(np.min(gap_throughs), 4),
            },
            "extra_slippage_bps": {
                "mean": round(np.mean(gap_throughs_bps), 1),
                "median": round(np.median(gap_throughs_bps), 1),
                "p90": round(np.percentile(gap_throughs_bps, 90), 1),
                "max": round(np.max(gap_throughs_bps), 1),
            },
            "bar_range_pct": {
                "mean": round(np.mean(bar_ranges), 4),
                "median": round(np.median(bar_ranges), 4),
                "max": round(np.max(bar_ranges), 4),
            },
            "realistic_loss_pct": {
                "mean": round(np.mean(realistic_losses), 4),
                "median": round(np.median(realistic_losses), 4),
                "p90": round(np.percentile(realistic_losses, 90), 4),
                "max": round(np.max(realistic_losses), 4),
            },
        },
        "buckets": {
            "negligible_le_1bp": len(zero_gap),
            "small_1_50bp": len(small_gap),
            "medium_50_150bp": len(medium_gap),
            "large_gt_150bp": len(large_gap),
        },
        "pnl_impact": {
            "total_sim_stop_pnl": round(total_sim_stop_loss, 2),
            "total_realistic_stop_pnl": round(total_realistic_stop_loss, 2),
            "total_degradation": round(total_degradation, 2),
            "mean_degradation_per_stop": round(np.mean(degradations), 2),
            "original_pf": config_d_pf,
            "adjusted_pf": round(adjusted_pf, 2),
            "original_pnl": config_d_pnl,
            "adjusted_pnl": round(adjusted_pnl, 2),
        },
        "verdict": verdict,
        "per_stop_details": stop_hits_sorted,
    }

    with open(output_json, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log.info(f"JSON output: {output_json}")


if __name__ == "__main__":
    main()
