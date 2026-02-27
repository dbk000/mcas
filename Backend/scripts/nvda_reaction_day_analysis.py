#!/usr/bin/env python3
"""
NVDA Earnings Reaction Day Analysis — Multi-Resolution
=======================================================
For each historical earnings reaction day, produces per-event narratives:
  - Exact time-to-peak (highest resolution available: 1m > 5m > 1h)
  - Peak return (open-to-high %)
  - Time-to-trough (intraday low timing)
  - MFE/MAE curves from open
  - Volume profile (first-30m vs rest)
  - Cascade sequencing (do sympathy stocks peak before/after NVDA?)
  - Control day comparison (non-reaction Wednesdays)

All findings are per-event narratives. No aggregated patterns (n=5 too small).

Usage:
  python3 Backend/scripts/nvda_reaction_day_analysis.py
  # or in Docker:
  docker compose -f docker-compose.yml run --rm -T worker \
    python3 /scripts/nvda_reaction_day_analysis.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import numpy as np

# Execution context detection (Corrections Log 2026-02-12)
if os.path.isdir('/scripts'):
    sys.path.insert(0, '/scripts')
elif os.path.isdir(os.path.join(os.path.dirname(__file__), 'equities_predictor')):
    sys.path.insert(0, os.path.dirname(__file__))

from equities_predictor.db import connect, load_ohlcv
from equities_predictor.calendar import ET, is_trading_day

# ── Configuration ─────────────────────────────────────────────────────────

SYMBOLS = ["NVDA", "META", "AVGO", "QQQ", "XLK"]

# Earnings dates (AMC) — reaction day is next trading day
NVDA_EARNINGS_DATES = [
    date(2024, 2, 21), date(2024, 5, 22), date(2024, 8, 28), date(2024, 11, 20),
    date(2025, 2, 26), date(2025, 5, 28), date(2025, 8, 27), date(2025, 11, 19),
]

# RTH boundaries
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)

# Output directory
if os.path.isdir('/scripts'):
    OUTPUT_DIR = '/scripts/output'
else:
    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')

os.makedirs(OUTPUT_DIR, exist_ok=True)


def next_trading_day(d: date) -> date:
    """Find the next trading day after d."""
    candidate = d + timedelta(days=1)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    raise RuntimeError(f"No trading day found within 10 days of {d}")


def get_reaction_dates() -> list[date]:
    """Compute reaction dates from earnings dates."""
    return [next_trading_day(ed) for ed in NVDA_EARNINGS_DATES]


def get_control_dates(reaction_dates: list[date], n: int = 5) -> list[date]:
    """Pick n non-reaction trading Wednesdays from 2025 for control comparison."""
    controls = []
    # Walk backwards from most recent reaction date to find Wednesdays
    candidate = date(2025, 11, 1)
    reaction_set = set(reaction_dates)
    while len(controls) < n and candidate > date(2025, 1, 1):
        if (candidate.weekday() == 2  # Wednesday
                and is_trading_day(candidate)
                and candidate not in reaction_set
                # Not adjacent to a reaction day
                and all(abs((candidate - rd).days) > 3 for rd in reaction_dates)):
            controls.append(candidate)
        candidate -= timedelta(days=1)
    controls.sort()
    return controls


def load_day_bars(
    conn,
    day: date,
    symbols: list[str],
    timeframe: str,
) -> dict[str, list[dict[str, Any]]]:
    """Load OHLCV bars for a single day, all symbols. Returns {symbol: [bars]}."""
    # Cover full day including pre/post market
    start_ts = datetime.combine(day, time(4, 0), tzinfo=ET).astimezone(timezone.utc)
    end_ts = datetime.combine(day, time(20, 0), tzinfo=ET).astimezone(timezone.utc)

    rows = load_ohlcv(
        conn,
        symbols=symbols,
        timeframe=timeframe,
        start_ts=start_ts,
        end_ts=end_ts,
    )

    result: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    for row in rows:
        s = row["symbol"]
        if s in result:
            result[s].append(row)
    return result


def filter_rth_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to regular trading hours only (9:30 - 16:00 ET)."""
    rth = []
    for bar in bars:
        ts_et = bar["bar_start_ts"].astimezone(ET)
        t = ts_et.time()
        if RTH_OPEN <= t < RTH_CLOSE:
            rth.append(bar)
    return rth


def minutes_from_open(bar_ts: datetime) -> float:
    """Minutes from 9:30 ET open."""
    ts_et = bar_ts.astimezone(ET)
    open_dt = datetime.combine(ts_et.date(), RTH_OPEN, tzinfo=ET)
    return (ts_et - open_dt).total_seconds() / 60.0


def tf_minutes(timeframe: str) -> int:
    """Convert timeframe string to minutes."""
    mapping = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
    return mapping.get(timeframe, 60)


def analyze_day(
    bars: list[dict[str, Any]],
    symbol: str,
    timeframe: str,
) -> dict[str, Any] | None:
    """Analyze a single symbol's single day. Returns event profile dict."""
    rth_bars = filter_rth_bars(bars)
    if len(rth_bars) < 3:
        return None

    open_price = float(rth_bars[0]["open"])
    if open_price <= 0:
        return None

    # Build MFE/MAE arrays from open
    highs = []
    lows = []
    closes = []
    volumes = []
    timestamps = []
    cum_mfe = []
    cum_mae = []
    running_high = float('-inf')
    running_low = float('inf')

    for bar in rth_bars:
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])
        v = float(bar["volume"])
        ts = bar["bar_start_ts"]

        running_high = max(running_high, h)
        running_low = min(running_low, l)

        highs.append(h)
        lows.append(l)
        closes.append(c)
        volumes.append(v)
        timestamps.append(ts)
        cum_mfe.append((running_high - open_price) / open_price * 100)
        cum_mae.append((running_low - open_price) / open_price * 100)

    # Find peak (intraday high)
    peak_idx = int(np.argmax(highs))
    peak_price = highs[peak_idx]
    peak_return_pct = (peak_price - open_price) / open_price * 100
    peak_minutes = minutes_from_open(timestamps[peak_idx])
    peak_time_et = timestamps[peak_idx].astimezone(ET).strftime("%H:%M ET")

    # Find trough (intraday low)
    trough_idx = int(np.argmin(lows))
    trough_price = lows[trough_idx]
    trough_return_pct = (trough_price - open_price) / open_price * 100
    trough_minutes = minutes_from_open(timestamps[trough_idx])
    trough_time_et = timestamps[trough_idx].astimezone(ET).strftime("%H:%M ET")

    # Close return
    close_price = closes[-1]
    close_return_pct = (close_price - open_price) / open_price * 100

    # Volume profile: first 30 min vs rest
    total_vol = sum(volumes)
    first_30_vol = 0.0
    tf_min = tf_minutes(timeframe)
    bars_in_30m = max(1, 30 // tf_min)
    for i in range(min(bars_in_30m, len(volumes))):
        first_30_vol += volumes[i]
    vol_first_30_pct = (first_30_vol / total_vol * 100) if total_vol > 0 else 0

    # MFE curve at key time points (minutes from open)
    checkpoints = [5, 10, 15, 30, 60, 120, 180, 390]
    mfe_curve = {}
    mae_curve = {}
    for cp in checkpoints:
        # Find the bar closest to this checkpoint
        bar_idx = min(int(cp / tf_min), len(cum_mfe) - 1)
        if bar_idx >= 0:
            mfe_curve[f"{cp}m"] = round(cum_mfe[bar_idx], 3)
            mae_curve[f"{cp}m"] = round(cum_mae[bar_idx], 3)

    # VWAP (cumulative)
    cum_vol = np.cumsum(volumes)
    cum_vp = np.cumsum([v * c for v, c in zip(volumes, closes)])
    vwap_series = []
    for i in range(len(cum_vol)):
        if cum_vol[i] > 0:
            vwap_series.append(cum_vp[i] / cum_vol[i])
        else:
            vwap_series.append(closes[i])

    # When does price cross below VWAP? (first time after first bar)
    vwap_cross_minute = None
    for i in range(1, len(closes)):
        if closes[i] < vwap_series[i]:
            vwap_cross_minute = minutes_from_open(timestamps[i])
            break

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "n_bars": len(rth_bars),
        "open_price": round(open_price, 2),
        "peak": {
            "price": round(peak_price, 2),
            "return_pct": round(peak_return_pct, 3),
            "minutes_from_open": round(peak_minutes, 1),
            "time_et": peak_time_et,
            "bar_index": peak_idx,
        },
        "trough": {
            "price": round(trough_price, 2),
            "return_pct": round(trough_return_pct, 3),
            "minutes_from_open": round(trough_minutes, 1),
            "time_et": trough_time_et,
            "bar_index": trough_idx,
        },
        "close": {
            "price": round(close_price, 2),
            "return_pct": round(close_return_pct, 3),
        },
        "volume_profile": {
            "total": round(total_vol, 0),
            "first_30m_pct": round(vol_first_30_pct, 1),
        },
        "mfe_curve": mfe_curve,
        "mae_curve": mae_curve,
        "vwap_cross_below_minute": round(vwap_cross_minute, 1) if vwap_cross_minute else None,
    }


def best_available_timeframe(conn, day: date, symbols: list[str]) -> str:
    """Check what's the finest resolution available for this day."""
    for tf in ["1m", "5m", "15m", "30m", "1h"]:
        data = load_day_bars(conn, day, ["NVDA"], tf)
        rth = filter_rth_bars(data.get("NVDA", []))
        if len(rth) >= 3:
            return tf
    return "1h"


def analyze_reaction_day(
    conn,
    reaction_date: date,
    earnings_date: date,
) -> dict[str, Any]:
    """Full analysis of one reaction day across all symbols and resolutions."""
    print(f"\n{'='*70}")
    print(f"Reaction day: {reaction_date} (earnings: {earnings_date})")

    # Determine best available timeframe
    tf = best_available_timeframe(conn, reaction_date, SYMBOLS)
    print(f"  Best available timeframe: {tf}")

    # Load bars
    all_bars = load_day_bars(conn, reaction_date, SYMBOLS, tf)

    event_profiles: dict[str, Any] = {}
    for symbol in SYMBOLS:
        bars = all_bars.get(symbol, [])
        if not bars:
            print(f"  {symbol}: no {tf} data")
            continue

        profile = analyze_day(bars, symbol, tf)
        if profile:
            event_profiles[symbol] = profile
            p = profile["peak"]
            t = profile["trough"]
            print(f"  {symbol}: peak +{p['return_pct']:.2f}% at {p['time_et']} "
                  f"(+{p['minutes_from_open']:.0f}m), "
                  f"trough {t['return_pct']:.2f}% at {t['time_et']}, "
                  f"close {profile['close']['return_pct']:.2f}%")
        else:
            print(f"  {symbol}: insufficient bars")

    # Cascade sequencing: who peaked first?
    cascade_sequence = []
    for symbol, prof in sorted(
        event_profiles.items(),
        key=lambda x: x[1]["peak"]["minutes_from_open"],
    ):
        cascade_sequence.append({
            "symbol": symbol,
            "peak_minute": prof["peak"]["minutes_from_open"],
            "peak_time_et": prof["peak"]["time_et"],
            "peak_return_pct": prof["peak"]["return_pct"],
        })

    if cascade_sequence:
        nvda_peak = next((c for c in cascade_sequence if c["symbol"] == "NVDA"), None)
        if nvda_peak:
            print(f"  Cascade: NVDA peaked at +{nvda_peak['peak_minute']:.0f}m")
            for c in cascade_sequence:
                if c["symbol"] != "NVDA":
                    delta = c["peak_minute"] - nvda_peak["peak_minute"]
                    direction = "after" if delta > 0 else ("before" if delta < 0 else "same time as")
                    print(f"    {c['symbol']}: peaked {abs(delta):.0f}m {direction} NVDA")

    return {
        "reaction_date": str(reaction_date),
        "earnings_date": str(earnings_date),
        "timeframe": tf,
        "profiles": event_profiles,
        "cascade_sequence": cascade_sequence,
    }


def analyze_control_day(conn, day: date) -> dict[str, Any]:
    """Analyze a non-reaction day for baseline comparison."""
    tf = best_available_timeframe(conn, day, SYMBOLS)
    all_bars = load_day_bars(conn, day, SYMBOLS, tf)

    profiles: dict[str, Any] = {}
    for symbol in SYMBOLS:
        bars = all_bars.get(symbol, [])
        if not bars:
            continue
        profile = analyze_day(bars, symbol, tf)
        if profile:
            profiles[symbol] = profile

    return {
        "date": str(day),
        "timeframe": tf,
        "profiles": profiles,
    }


def generate_markdown_report(
    events: list[dict[str, Any]],
    controls: list[dict[str, Any]],
) -> str:
    """Generate per-event narrative markdown report."""
    lines = []
    lines.append("# NVDA Earnings Reaction Day Analysis — Multi-Resolution\n")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
    lines.append("**Disclaimer:** All findings below are per-event narratives. "
                  "n=5 positive-gap events cannot support statistical pattern detection. "
                  "The guidance in Section 8 of Alan's briefing ('sell in the first hour') is unchanged.\n")
    lines.append("---\n")

    # Per-event narratives
    for i, event in enumerate(events, 1):
        rd = event["reaction_date"]
        ed = event["earnings_date"]
        tf = event["timeframe"]
        profiles = event["profiles"]

        lines.append(f"## Event {i}: Reaction Day {rd} (Earnings {ed})\n")
        lines.append(f"**Resolution:** {tf} bars\n")

        if not profiles:
            lines.append("*No data available for this reaction day.*\n")
            lines.append("---\n")
            continue

        # NVDA first
        nvda = profiles.get("NVDA")
        if nvda:
            p = nvda["peak"]
            t = nvda["trough"]
            c = nvda["close"]
            lines.append(f"### NVDA\n")
            lines.append(f"- **Open:** ${nvda['open_price']:.2f}")
            lines.append(f"- **Peak:** +{p['return_pct']:.2f}% at {p['time_et']} "
                          f"(+{p['minutes_from_open']:.0f} min from open) → ${p['price']:.2f}")
            lines.append(f"- **Trough:** {t['return_pct']:.2f}% at {t['time_et']} "
                          f"(+{t['minutes_from_open']:.0f} min from open) → ${t['price']:.2f}")
            lines.append(f"- **Close:** {c['return_pct']:.2f}% → ${c['price']:.2f}")
            vol = nvda["volume_profile"]
            lines.append(f"- **Volume:** {vol['first_30m_pct']:.0f}% in first 30 min")
            if nvda["vwap_cross_below_minute"] is not None:
                lines.append(f"- **Price crossed below VWAP:** +{nvda['vwap_cross_below_minute']:.0f} min from open")

            # MFE curve
            mfe = nvda["mfe_curve"]
            if mfe:
                mfe_str = " → ".join(f"{k}: +{v:.2f}%" for k, v in mfe.items() if v > 0)
                if mfe_str:
                    lines.append(f"- **MFE progression:** {mfe_str}")
            lines.append("")

        # Cascade stocks
        others = [(s, profiles[s]) for s in SYMBOLS if s != "NVDA" and s in profiles]
        if others:
            lines.append("### Cascade Stocks\n")
            lines.append("| Stock | Peak Return | Peak Time | Trough | Close | Vol 1st 30m | VWAP Cross |")
            lines.append("|-------|:----------:|:---------:|:------:|:-----:|:-----------:|:----------:|")
            for sym, prof in others:
                p = prof["peak"]
                t = prof["trough"]
                c = prof["close"]
                v = prof["volume_profile"]
                vwap = f"+{prof['vwap_cross_below_minute']:.0f}m" if prof["vwap_cross_below_minute"] else "—"
                lines.append(f"| {sym} | +{p['return_pct']:.2f}% | {p['time_et']} (+{p['minutes_from_open']:.0f}m) "
                              f"| {t['return_pct']:.2f}% | {c['return_pct']:.2f}% | {v['first_30m_pct']:.0f}% | {vwap} |")
            lines.append("")

        # Cascade sequence
        seq = event.get("cascade_sequence", [])
        if seq and len(seq) > 1:
            lines.append("**Peak sequence (earliest first):**")
            for item in seq:
                lines.append(f"1. {item['symbol']}: +{item['peak_minute']:.0f}m "
                              f"({item['peak_time_et']}, +{item['peak_return_pct']:.2f}%)")
            lines.append("")

        lines.append("---\n")

    # Control days section
    lines.append("## Control Days (Non-Reaction Wednesdays)\n")
    lines.append("Baseline comparison: what does a normal Wednesday look like for these stocks?\n")

    if not controls:
        lines.append("*No control day data available.*\n")
    else:
        for ctrl in controls:
            d = ctrl["date"]
            profiles = ctrl["profiles"]
            if not profiles:
                continue
            lines.append(f"### {d} ({ctrl['timeframe']})\n")
            lines.append("| Stock | Peak | Trough | Close | Vol 1st 30m |")
            lines.append("|-------|:----:|:------:|:-----:|:-----------:|")
            for sym in SYMBOLS:
                if sym not in profiles:
                    continue
                prof = profiles[sym]
                p = prof["peak"]
                t = prof["trough"]
                c = prof["close"]
                v = prof["volume_profile"]
                lines.append(f"| {sym} | +{p['return_pct']:.2f}% (+{p['minutes_from_open']:.0f}m) "
                              f"| {t['return_pct']:.2f}% | {c['return_pct']:.2f}% | {v['first_30m_pct']:.0f}% |")
            lines.append("")

        # Aggregate control stats
        ctrl_peaks = {s: [] for s in SYMBOLS}
        ctrl_troughs = {s: [] for s in SYMBOLS}
        ctrl_vol30 = {s: [] for s in SYMBOLS}
        for ctrl in controls:
            for sym in SYMBOLS:
                if sym in ctrl["profiles"]:
                    ctrl_peaks[sym].append(ctrl["profiles"][sym]["peak"]["return_pct"])
                    ctrl_troughs[sym].append(ctrl["profiles"][sym]["trough"]["return_pct"])
                    ctrl_vol30[sym].append(ctrl["profiles"][sym]["volume_profile"]["first_30m_pct"])

        lines.append("### Control Day Averages\n")
        lines.append("| Stock | Avg Peak | Avg Trough | Avg Vol 1st 30m |")
        lines.append("|-------|:--------:|:----------:|:---------------:|")
        for sym in SYMBOLS:
            if ctrl_peaks[sym]:
                avg_p = np.mean(ctrl_peaks[sym])
                avg_t = np.mean(ctrl_troughs[sym])
                avg_v = np.mean(ctrl_vol30[sym])
                lines.append(f"| {sym} | +{avg_p:.2f}% | {avg_t:.2f}% | {avg_v:.0f}% |")
        lines.append("")

    return "\n".join(lines)


def main():
    print("NVDA Earnings Reaction Day Analysis — Multi-Resolution")
    print("=" * 60)

    conn = connect()

    # Compute reaction dates
    reaction_dates = get_reaction_dates()
    print(f"Reaction dates: {[str(d) for d in reaction_dates]}")

    # Analyze each reaction day
    events = []
    for rd, ed in zip(reaction_dates, NVDA_EARNINGS_DATES):
        result = analyze_reaction_day(conn, rd, ed)
        events.append(result)

    # Control days
    print(f"\n{'='*70}")
    print("Analyzing control days...")
    control_dates = get_control_dates(reaction_dates)
    print(f"Control dates: {[str(d) for d in control_dates]}")
    controls = []
    for cd in control_dates:
        print(f"  Control {cd}...", end=" ", flush=True)
        ctrl = analyze_control_day(conn, cd)
        n_profiles = len(ctrl["profiles"])
        print(f"{n_profiles} symbols with data ({ctrl['timeframe']})")
        controls.append(ctrl)

    conn.close()

    # Write JSON
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reaction_events": events,
        "control_days": controls,
    }
    json_path = os.path.join(OUTPUT_DIR, "nvda_reaction_day_analysis.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nJSON output: {json_path}")

    # Write markdown
    md = generate_markdown_report(events, controls)
    md_path = os.path.join(OUTPUT_DIR, "nvda_reaction_day_analysis.md")
    with open(md_path, "w") as f:
        f.write(md)
    print(f"Markdown output: {md_path}")

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for event in events:
        rd = event["reaction_date"]
        nvda = event["profiles"].get("NVDA")
        if nvda:
            p = nvda["peak"]
            print(f"  {rd}: NVDA peaked +{p['return_pct']:.2f}% at {p['time_et']} "
                  f"(+{p['minutes_from_open']:.0f}m), closed {nvda['close']['return_pct']:.2f}%")
        else:
            print(f"  {rd}: no NVDA data")


if __name__ == "__main__":
    main()
