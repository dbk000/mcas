#!/usr/bin/env python3
"""
NVDA Earnings Cascade Analyzer (D1)
====================================
Analyzes 8 historical NVDA earnings events to measure:
  1. NVDA reaction magnitude (ret_1d, overnight_gap, ret_2d, ret_5d)
  2. SPY-controlled excess returns for all equities in universe
  3. Direction consistency (how often asset excess return has same sign as NVDA)
  4. Laggard catch-up: do day-1 underperformers catch up by day 2-5?

Output:
  - nvda_cascade_profiles.json  (machine-readable, consumed by EQ_NVDA_MACRO strategy)
  - nvda_cascade_report.md      (human-readable summary)

Usage:
  docker compose -f docker-compose.yml run --rm -T worker \
    python3 /scripts/nvda_earnings_cascade.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

# Execution context detection (Corrections Log 2026-02-12)
if os.path.isdir('/scripts'):
    sys.path.insert(0, '/scripts')
elif os.path.isdir(os.path.join(os.path.dirname(__file__), 'equities_predictor')):
    sys.path.insert(0, os.path.dirname(__file__))

from equities_predictor.db import connect

logger = logging.getLogger("nvda_cascade")

# ── Configuration ──────────────────────────────────────────────────────────

# Hardcoded fallback earnings dates (all AMC — reaction bar is next trading day)
NVDA_EARNINGS_DATES_FALLBACK = [
    date(2024, 2, 21), date(2024, 5, 22), date(2024, 8, 28), date(2024, 11, 20),
    date(2025, 2, 26), date(2025, 5, 28), date(2025, 8, 27), date(2025, 11, 19),
]
NEXT_EARNINGS = date(2026, 2, 25)

# Crypto symbols (informational only — no signals from D3)
CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# Rolling beta window
BETA_WINDOW = 60

# Approximate NVDA weight in SPY (cap-weighted). Used to decontaminate SPY benchmark.
# NVDA grew from ~3.5% (early 2024) to ~7% (mid-2025). Use 6% as reasonable constant.
# Sensitivity: results are checked at 4%, 6%, 8% in the report.
NVDA_SPY_WEIGHT = 0.06

# Direction consistency threshold for cascade eligibility (relaxed from 7 — see review)
# With n=7, requiring 7/7 is 100% perfection. 6/7 (86%) is still strong signal.
MIN_DIRECTION_CONSISTENCY = 6

# Output directory
if os.path.isdir('/scripts'):
    OUTPUT_DIR = '/scripts/output'
else:
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')


# ── Helpers ────────────────────────────────────────────────────────────────

def _setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _to_date(ts) -> date:
    """Convert a bar_start_ts value to a date object."""
    if isinstance(ts, date) and not isinstance(ts, datetime):
        return ts
    if isinstance(ts, datetime):
        return ts.date()
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    return date.fromisoformat(str(ts)[:10])


def _pct(v: float, decimals: int = 2) -> str:
    """Format as percentage string."""
    return f"{v * 100:+.{decimals}f}%"


# ── Data Loading ───────────────────────────────────────────────────────────

def load_equities_ohlcv(conn) -> dict[str, list[dict]]:
    """Load all equities daily OHLCV, keyed by symbol -> sorted list of bar dicts."""
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, bar_start_ts, open, high, low, close, volume
        FROM ohlcv
        WHERE source = 'equities_yahoo' AND timeframe = '1d'
        ORDER BY symbol, bar_start_ts
    """)
    data: dict[str, list[dict]] = defaultdict(list)
    row_count = 0
    for row in cur.fetchall():
        data[row["symbol"]].append({
            "date": _to_date(row["bar_start_ts"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        })
        row_count += 1
    cur.close()
    logger.info("Loaded %d equities OHLCV rows across %d symbols", row_count, len(data))
    return dict(data)


def load_crypto_ohlcv(conn) -> dict[str, list[dict]]:
    """Load BTC/ETH daily OHLCV (informational only)."""
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(CRYPTO_SYMBOLS))
    cur.execute(f"""
        SELECT symbol, bar_start_ts, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol IN ({placeholders}) AND timeframe = '1d'
        ORDER BY symbol, bar_start_ts
    """, tuple(CRYPTO_SYMBOLS))
    data: dict[str, list[dict]] = defaultdict(list)
    for row in cur.fetchall():
        data[row["symbol"]].append({
            "date": _to_date(row["bar_start_ts"]),
            "open": float(row["open"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        })
    cur.close()
    return dict(data)


def load_earnings_dates(conn) -> list[date]:
    """Load NVDA earnings dates from equities_earnings_calendar. Fallback to hardcoded."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT report_date FROM equities_earnings_calendar
            WHERE symbol = 'NVDA'
              AND report_date IS NOT NULL
              AND report_date < %s
            ORDER BY report_date ASC
        """, (NEXT_EARNINGS,))
        rows = cur.fetchall()
        cur.close()
        if rows and len(rows) >= 4:
            dates = [_to_date(r["report_date"]) for r in rows]
            # Filter to roughly quarterly earnings (skip duplicates within 60 days)
            filtered = []
            for d in dates:
                if not filtered or (d - filtered[-1]).days > 60:
                    filtered.append(d)
            # Keep only last 8
            result = filtered[-8:]
            logger.info("Loaded %d NVDA earnings dates from DB (from %d raw)", len(result), len(rows))
            return result
    except Exception as e:
        logger.warning("Failed to load from earnings_calendar: %s, using fallback", e)
        try:
            conn.rollback()
        except Exception:
            pass

    logger.info("Using hardcoded NVDA earnings dates (%d events)", len(NVDA_EARNINGS_DATES_FALLBACK))
    return NVDA_EARNINGS_DATES_FALLBACK


# ── Trading Calendar ───────────────────────────────────────────────────────

def build_trading_calendar(spy_bars: list[dict]) -> list[date]:
    """Build sorted list of trading dates from SPY OHLCV."""
    return sorted(set(bar["date"] for bar in spy_bars))


def find_next_trading_day(cal: list[date], ref: date) -> date | None:
    """Find the first trading day strictly after ref."""
    for d in cal:
        if d > ref:
            return d
    return None


def find_trading_day_offset(cal: list[date], ref: date, offset: int) -> date | None:
    """Find the trading day at +offset from ref (ref itself is offset 0)."""
    try:
        idx = cal.index(ref)
    except ValueError:
        # ref not in calendar — find closest
        for i, d in enumerate(cal):
            if d >= ref:
                idx = i
                break
        else:
            return None
    target = idx + offset
    if 0 <= target < len(cal):
        return cal[target]
    return None


# ── Beta Computation ───────────────────────────────────────────────────────

def compute_daily_returns(bars: list[dict]) -> dict[date, float]:
    """Compute close-to-close daily returns. Returns {date: return}."""
    rets = {}
    for i in range(1, len(bars)):
        prev_c = bars[i - 1]["close"]
        curr_c = bars[i]["close"]
        if prev_c > 0 and np.isfinite(prev_c) and np.isfinite(curr_c):
            rets[bars[i]["date"]] = curr_c / prev_c - 1.0
    return rets


def compute_rolling_beta(
    asset_rets: dict[date, float],
    spy_rets: dict[date, float],
    as_of_date: date,
    cal: list[date],
    window: int = BETA_WINDOW,
) -> float:
    """Compute rolling beta of asset to SPY using `window` trading days before as_of_date."""
    # Find the index of as_of_date (or closest before it)
    idx = None
    for i, d in enumerate(cal):
        if d >= as_of_date:
            idx = i
            break
    if idx is None:
        return np.nan

    # Go back window bars
    start_idx = max(0, idx - window)
    dates_window = cal[start_idx:idx]

    asset_vals = []
    spy_vals = []
    for d in dates_window:
        a = asset_rets.get(d)
        s = spy_rets.get(d)
        if a is not None and s is not None and np.isfinite(a) and np.isfinite(s):
            asset_vals.append(a)
            spy_vals.append(s)

    if len(asset_vals) < 30:
        return np.nan

    a = np.array(asset_vals)
    s = np.array(spy_vals)
    cov = np.cov(a, s)[0, 1]
    var_s = np.var(s)
    if var_s < 1e-10:
        return np.nan
    return cov / var_s


# ── Per-Bar Lookup ─────────────────────────────────────────────────────────

def bars_to_dict(bars: list[dict]) -> dict[date, dict]:
    """Convert bars list to {date: bar_dict} for O(1) lookup."""
    return {bar["date"]: bar for bar in bars}


def get_return(bar_dict: dict[date, dict], from_date: date, to_date: date) -> float:
    """Compute return from from_date close to to_date close."""
    b_from = bar_dict.get(from_date)
    b_to = bar_dict.get(to_date)
    if not b_from or not b_to:
        return np.nan
    if b_from["close"] <= 0:
        return np.nan
    return b_to["close"] / b_from["close"] - 1.0


def get_overnight_gap(bar_dict: dict[date, dict], earnings_date: date, reaction_date: date) -> float:
    """Compute overnight gap: open[reaction] / close[earnings] - 1."""
    b_earn = bar_dict.get(earnings_date)
    b_react = bar_dict.get(reaction_date)
    if not b_earn or not b_react:
        return np.nan
    if b_earn["close"] <= 0:
        return np.nan
    return b_react["open"] / b_earn["close"] - 1.0


def get_intraday_return(bar_dict: dict[date, dict], reaction_date: date) -> float:
    """Compute intraday return on reaction day: close[reaction] / open[reaction] - 1.

    This captures the correction/continuation AFTER the overnight gap.
    """
    b = bar_dict.get(reaction_date)
    if not b:
        return np.nan
    if b["open"] <= 0:
        return np.nan
    return b["close"] / b["open"] - 1.0


def get_volume_ratio(bar_dict: dict[date, dict], reaction_date: date, cal: list[date]) -> float:
    """Volume[reaction] / rolling_mean_20d_volume before reaction."""
    b_react = bar_dict.get(reaction_date)
    if not b_react:
        return np.nan

    # Find 20 trading days before reaction
    try:
        idx = cal.index(reaction_date)
    except ValueError:
        return np.nan

    start_idx = max(0, idx - 20)
    vol_window = []
    for d in cal[start_idx:idx]:
        b = bar_dict.get(d)
        if b and np.isfinite(b["volume"]) and b["volume"] > 0:
            vol_window.append(b["volume"])

    if len(vol_window) < 5:
        return np.nan

    avg_vol = np.mean(vol_window)
    if avg_vol <= 0:
        return np.nan
    return b_react["volume"] / avg_vol


# ── Main Analysis ──────────────────────────────────────────────────────────

def analyze_cascade(conn) -> dict[str, Any]:
    """Run the full cascade analysis. Returns structured results dict."""
    t0 = time.time()

    # 1. Load data
    logger.info("Loading equities OHLCV...")
    eq_ohlcv = load_equities_ohlcv(conn)

    logger.info("Loading crypto OHLCV...")
    crypto_ohlcv = load_crypto_ohlcv(conn)

    logger.info("Loading NVDA earnings dates...")
    earnings_dates = load_earnings_dates(conn)

    if "SPY" not in eq_ohlcv:
        raise ValueError("SPY data not found in equities OHLCV")
    if "NVDA" not in eq_ohlcv:
        raise ValueError("NVDA data not found in equities OHLCV")

    # 2. Build trading calendar and lookups
    cal = build_trading_calendar(eq_ohlcv["SPY"])
    logger.info("Trading calendar: %d days (%s to %s)", len(cal), cal[0], cal[-1])

    # Build per-symbol bar dicts and daily returns
    sym_bars: dict[str, dict[date, dict]] = {}
    sym_rets: dict[str, dict[date, float]] = {}
    for sym, bars in eq_ohlcv.items():
        sym_bars[sym] = bars_to_dict(bars)
        sym_rets[sym] = compute_daily_returns(bars)

    crypto_bars: dict[str, dict[date, dict]] = {}
    for sym, bars in crypto_ohlcv.items():
        crypto_bars[sym] = bars_to_dict(bars)

    spy_rets = sym_rets.get("SPY", {})

    # All equities symbols (excluding SPY)
    equity_symbols = sorted(s for s in eq_ohlcv.keys() if s != "SPY")
    logger.info("Equity symbols (ex-SPY): %d", len(equity_symbols))

    # 3. Process each earnings event
    events = []
    reaction_dates_list = []

    for event_idx, earn_date in enumerate(earnings_dates):
        reaction_date = find_next_trading_day(cal, earn_date)
        if reaction_date is None:
            logger.warning("Event %d (%s): no reaction day found, skipping", event_idx + 1, earn_date)
            continue

        day2 = find_trading_day_offset(cal, reaction_date, 1)
        day5 = find_trading_day_offset(cal, reaction_date, 4)

        logger.info("Event %d/%d: earnings=%s, reaction=%s, day2=%s, day5=%s",
                     event_idx + 1, len(earnings_dates), earn_date, reaction_date, day2, day5)

        # NVDA reaction — decomposed into gap + intraday
        nvda_bars_d = sym_bars.get("NVDA", {})
        nvda_ret_1d = get_return(nvda_bars_d, earn_date, reaction_date)
        nvda_overnight = get_overnight_gap(nvda_bars_d, earn_date, reaction_date)
        nvda_intraday = get_intraday_return(nvda_bars_d, reaction_date)
        nvda_ret_2d = get_return(nvda_bars_d, earn_date, day2) if day2 else np.nan
        nvda_ret_5d = get_return(nvda_bars_d, earn_date, day5) if day5 else np.nan
        nvda_vol_ratio = get_volume_ratio(nvda_bars_d, reaction_date, cal)

        # SPY reaction — also decomposed
        spy_bars_d = sym_bars.get("SPY", {})
        spy_ret_1d = get_return(spy_bars_d, earn_date, reaction_date)
        spy_gap = get_overnight_gap(spy_bars_d, earn_date, reaction_date)
        spy_intraday = get_intraday_return(spy_bars_d, reaction_date)
        spy_ret_2d = get_return(spy_bars_d, earn_date, day2) if day2 else np.nan
        spy_ret_5d = get_return(spy_bars_d, earn_date, day5) if day5 else np.nan

        # ── DECONTAMINATED BENCHMARK (review fix: NVDA is ~6% of SPY) ──
        # SPY_ret contains NVDA's move. Subtracting beta*SPY partially removes
        # the very cascade we're trying to measure.
        # SPY_ex_NVDA = (SPY_ret - w * NVDA_ret) / (1 - w)
        w = NVDA_SPY_WEIGHT
        def _decontaminate(spy_r, nvda_r):
            if not np.isfinite(spy_r) or not np.isfinite(nvda_r):
                return spy_r  # can't decontaminate, use raw
            return (spy_r - w * nvda_r) / (1 - w)

        spy_clean_1d = _decontaminate(spy_ret_1d, nvda_ret_1d)
        spy_clean_gap = _decontaminate(spy_gap, nvda_overnight)
        spy_clean_intraday = _decontaminate(spy_intraday, nvda_intraday)
        spy_clean_2d = _decontaminate(spy_ret_2d, nvda_ret_2d)
        spy_clean_5d = _decontaminate(spy_ret_5d, nvda_ret_5d)

        # NVDA excess returns — using CLEAN benchmark
        nvda_beta = compute_rolling_beta(sym_rets.get("NVDA", {}), spy_rets, earn_date, cal)
        _bv = nvda_beta if np.isfinite(nvda_beta) else 0.0
        nvda_excess_1d = nvda_ret_1d - (_bv * spy_clean_1d if np.isfinite(spy_clean_1d) else 0)
        nvda_excess_2d = nvda_ret_2d - (_bv * spy_clean_2d if np.isfinite(spy_clean_2d) else 0)
        nvda_excess_5d = nvda_ret_5d - (_bv * spy_clean_5d if np.isfinite(spy_clean_5d) else 0)
        nvda_excess_gap = nvda_overnight - (_bv * spy_clean_gap if np.isfinite(spy_clean_gap) else 0)
        nvda_excess_intraday = nvda_intraday - (_bv * spy_clean_intraday if np.isfinite(spy_clean_intraday) else 0) if np.isfinite(nvda_intraday) else np.nan

        # Contamination diagnostic: how much did NVDA's own move inflate SPY?
        contamination_1d = spy_ret_1d - spy_clean_1d if np.isfinite(spy_ret_1d) and np.isfinite(spy_clean_1d) else 0

        event_data = {
            "earnings_date": str(earn_date),
            "reaction_date": str(reaction_date),
            "nvda": {
                "ret_1d": _safe_float(nvda_ret_1d),
                "overnight_gap": _safe_float(nvda_overnight),
                "intraday_ret": _safe_float(nvda_intraday),
                "ret_2d": _safe_float(nvda_ret_2d),
                "ret_5d": _safe_float(nvda_ret_5d),
                "volume_ratio": _safe_float(nvda_vol_ratio),
                "spy_beta": _safe_float(nvda_beta),
                "excess_ret_1d": _safe_float(nvda_excess_1d),
                "excess_gap": _safe_float(nvda_excess_gap),
                "excess_intraday": _safe_float(nvda_excess_intraday),
                "excess_ret_2d": _safe_float(nvda_excess_2d),
                "excess_ret_5d": _safe_float(nvda_excess_5d),
            },
            "spy": {
                "ret_1d": _safe_float(spy_ret_1d),
                "overnight_gap": _safe_float(spy_gap),
                "intraday_ret": _safe_float(spy_intraday),
                "ret_2d": _safe_float(spy_ret_2d),
                "ret_5d": _safe_float(spy_ret_5d),
                "clean_1d": _safe_float(spy_clean_1d),
                "clean_gap": _safe_float(spy_clean_gap),
                "contamination_1d": _safe_float(contamination_1d),
            },
            "assets": {},
            "crypto": {},
        }

        # Per-asset cascade data — using DECONTAMINATED SPY benchmark
        for sym in equity_symbols:
            asset_bars_d = sym_bars.get(sym, {})
            raw_ret_1d = get_return(asset_bars_d, earn_date, reaction_date)
            raw_gap = get_overnight_gap(asset_bars_d, earn_date, reaction_date)
            raw_intraday = get_intraday_return(asset_bars_d, reaction_date)
            raw_ret_2d = get_return(asset_bars_d, earn_date, day2) if day2 else np.nan
            raw_ret_5d = get_return(asset_bars_d, earn_date, day5) if day5 else np.nan

            beta = compute_rolling_beta(sym_rets.get(sym, {}), spy_rets, earn_date, cal)
            _b = beta if np.isfinite(beta) else 0.0

            # Clean excess (decontaminated SPY benchmark)
            exp_clean_1d = _b * spy_clean_1d if np.isfinite(spy_clean_1d) else 0
            exp_clean_2d = _b * spy_clean_2d if np.isfinite(spy_clean_2d) else 0
            exp_clean_5d = _b * spy_clean_5d if np.isfinite(spy_clean_5d) else 0
            excess_1d = raw_ret_1d - exp_clean_1d if np.isfinite(raw_ret_1d) else np.nan
            excess_2d = raw_ret_2d - exp_clean_2d if np.isfinite(raw_ret_2d) else np.nan
            excess_5d = raw_ret_5d - exp_clean_5d if np.isfinite(raw_ret_5d) else np.nan

            # Gap-phase clean excess
            exp_clean_gap = _b * spy_clean_gap if np.isfinite(spy_clean_gap) else 0
            excess_gap = raw_gap - exp_clean_gap if np.isfinite(raw_gap) else np.nan

            # Intraday clean excess
            exp_clean_intraday = _b * spy_clean_intraday if np.isfinite(spy_clean_intraday) else 0
            excess_intraday = raw_intraday - exp_clean_intraday if np.isfinite(raw_intraday) else np.nan

            event_data["assets"][sym] = {
                "raw_ret_1d": _safe_float(raw_ret_1d),
                "raw_gap": _safe_float(raw_gap),
                "raw_intraday": _safe_float(raw_intraday),
                "raw_ret_2d": _safe_float(raw_ret_2d),
                "raw_ret_5d": _safe_float(raw_ret_5d),
                "spy_beta": _safe_float(beta),
                "expected_clean_1d": _safe_float(exp_clean_1d),
                "excess_ret_1d": _safe_float(excess_1d),
                "excess_gap": _safe_float(excess_gap),
                "excess_intraday": _safe_float(excess_intraday),
                "excess_ret_2d": _safe_float(excess_2d),
                "excess_ret_5d": _safe_float(excess_5d),
            }

        # Crypto (raw returns only, informational)
        for sym in CRYPTO_SYMBOLS:
            cb = crypto_bars.get(sym, {})
            if cb:
                cr_1d = get_return(cb, earn_date, reaction_date)
                cr_2d = get_return(cb, earn_date, day2) if day2 else np.nan
                cr_5d = get_return(cb, earn_date, day5) if day5 else np.nan
                event_data["crypto"][sym] = {
                    "raw_ret_1d": _safe_float(cr_1d),
                    "raw_ret_2d": _safe_float(cr_2d),
                    "raw_ret_5d": _safe_float(cr_5d),
                }

        events.append(event_data)
        reaction_dates_list.append(str(reaction_date))

    logger.info("Processed %d earnings events", len(events))

    # 4. Aggregate cascade profiles
    asset_profiles = _aggregate_profiles(events, equity_symbols)

    # 5. Laggard analysis
    laggard_analysis = _analyze_laggards(events, asset_profiles)

    # 6. Build output
    result = {
        "generated_ts": datetime.now(timezone.utc).isoformat(),
        "script_version": "1.0",
        "n_events": len(events),
        "nvda_earnings_dates": [str(d) for d in earnings_dates],
        "reaction_dates": reaction_dates_list,
        "next_earnings": str(NEXT_EARNINGS),
        "next_reaction_date": str(find_next_trading_day(cal, NEXT_EARNINGS) or ""),
        "events": events,
        "assets": asset_profiles,
        "laggard_distribution": laggard_analysis.get("distribution", {}),
        "laggard_catchup_by_cut": laggard_analysis.get("catchup_by_cut", {}),
        "gap_laggard_distribution": laggard_analysis.get("gap_distribution", {}),
        "gap_laggard_catchup_by_cut": laggard_analysis.get("gap_catchup_by_cut", {}),
        "recommended_laggard_threshold": laggard_analysis.get("recommended_threshold", 0.5),
        "overall_laggard_catchup": laggard_analysis.get("overall_laggard_catchup", {}),
        "summary": {
            "cascade_eligible_count": sum(
                1 for v in asset_profiles.values() if v.get("cascade_eligible")
            ),
            "cascade_eligible_close_count": sum(
                1 for v in asset_profiles.values() if v.get("cascade_eligible_close")
            ),
            "cascade_eligible_gap_count": sum(
                1 for v in asset_profiles.values() if v.get("cascade_eligible_gap")
            ),
            "total_assets_analyzed": len(equity_symbols),
            "elapsed_s": round(time.time() - t0, 2),
        },
    }

    return result


def _safe_float(v) -> float | None:
    """Convert to JSON-safe float (None for NaN/Inf)."""
    if v is None or not np.isfinite(v):
        return None
    return round(float(v), 6)


def _mag_weighted_consistency(events: list[dict], sym: str, asset_key: str, nvda_key: str) -> float:
    """Magnitude-weighted direction consistency (review fix: magnitude blindness).

    Instead of treating +0.01% and +5.0% equally, weight each event by |excess|.
    Score = sum(|asset_excess| * sign_match) / sum(|asset_excess|).
    Returns 0.0 to 1.0 (1.0 = perfect magnitude-weighted agreement).
    """
    weighted_agree = 0.0
    total_weight = 0.0
    for event in events:
        asset_data = event["assets"].get(sym, {})
        nvda_data = event["nvda"]
        a_val = asset_data.get(asset_key)
        n_val = nvda_data.get(nvda_key)
        if a_val is None or n_val is None:
            continue
        w = abs(a_val)
        total_weight += w
        if (a_val > 0 and n_val > 0) or (a_val < 0 and n_val < 0):
            weighted_agree += w
    if total_weight < 1e-10:
        return 0.0
    return weighted_agree / total_weight


def _aggregate_profiles(events: list[dict], symbols: list[str]) -> dict[str, dict]:
    """Aggregate per-event data into cascade profiles for each symbol.

    Computes BOTH close-to-close and gap-phase direction consistency.
    The gap-phase analysis captures kneejerk reactions that may correct
    before close — these are the tradeable moves that daily close misses.
    """
    profiles = {}

    for sym in symbols:
        excess_1d_vals = []
        excess_2d_vals = []
        excess_5d_vals = []
        excess_gap_vals = []
        excess_intraday_vals = []
        raw_1d_vals = []
        raw_gap_vals = []
        sympathy_ratios = []
        gap_sympathy_ratios = []
        per_event = []

        for event in events:
            asset_data = event["assets"].get(sym, {})
            nvda_data = event["nvda"]

            e1 = asset_data.get("excess_ret_1d")
            e2 = asset_data.get("excess_ret_2d")
            e5 = asset_data.get("excess_ret_5d")
            eg = asset_data.get("excess_gap")
            ei = asset_data.get("excess_intraday")
            r1 = asset_data.get("raw_ret_1d")
            rg = asset_data.get("raw_gap")
            nvda_ret = nvda_data.get("ret_1d")
            nvda_gap = nvda_data.get("overnight_gap")
            nvda_excess = nvda_data.get("excess_ret_1d")
            nvda_excess_gap = nvda_data.get("excess_gap")

            if e1 is not None:
                excess_1d_vals.append(e1)
            if e2 is not None:
                excess_2d_vals.append(e2)
            if e5 is not None:
                excess_5d_vals.append(e5)
            if eg is not None:
                excess_gap_vals.append(eg)
            if ei is not None:
                excess_intraday_vals.append(ei)
            if r1 is not None:
                raw_1d_vals.append(r1)
            if rg is not None:
                raw_gap_vals.append(rg)
            if e1 is not None and nvda_ret is not None and abs(nvda_ret) > 0.001:
                sympathy_ratios.append(e1 / nvda_ret)
            if eg is not None and nvda_gap is not None and abs(nvda_gap) > 0.001:
                gap_sympathy_ratios.append(eg / nvda_gap)

            per_event.append({
                "earnings_date": event["earnings_date"],
                "reaction_date": event["reaction_date"],
                "raw_ret_1d": r1,
                "raw_gap": rg,
                "raw_intraday": asset_data.get("raw_intraday"),
                "spy_beta": asset_data.get("spy_beta"),
                "excess_ret_1d": e1,
                "excess_gap": eg,
                "excess_intraday": ei,
                "excess_ret_2d": e2,
                "excess_ret_5d": e5,
                "nvda_ret_1d": nvda_ret,
                "nvda_gap": nvda_gap,
                "nvda_excess_1d": nvda_excess,
                "nvda_excess_gap": nvda_excess_gap,
            })

        # Close-to-close direction consistency (original)
        n_same_sign = 0
        n_valid = 0
        for event in events:
            asset_data = event["assets"].get(sym, {})
            nvda_data = event["nvda"]
            e1 = asset_data.get("excess_ret_1d")
            nvda_e1 = nvda_data.get("excess_ret_1d")
            if e1 is not None and nvda_e1 is not None:
                n_valid += 1
                if (e1 > 0 and nvda_e1 > 0) or (e1 < 0 and nvda_e1 < 0):
                    n_same_sign += 1

        # GAP-PHASE direction consistency (kneejerk reaction)
        n_gap_same_sign = 0
        n_gap_valid = 0
        for event in events:
            asset_data = event["assets"].get(sym, {})
            nvda_data = event["nvda"]
            eg = asset_data.get("excess_gap")
            nvda_eg = nvda_data.get("excess_gap")
            if eg is not None and nvda_eg is not None:
                n_gap_valid += 1
                if (eg > 0 and nvda_eg > 0) or (eg < 0 and nvda_eg < 0):
                    n_gap_same_sign += 1

        direction_consistency = n_same_sign
        gap_direction_consistency = n_gap_same_sign

        # Magnitude-weighted consistency (review fix: treats +0.01% and +5% differently)
        # Weight = |excess|. Score = sum(|excess| * sign_match) / sum(|excess|).
        # This measures whether LARGE moves agree with NVDA, not just any move.
        mag_weighted_close = _mag_weighted_consistency(events, sym, "excess_ret_1d", "excess_ret_1d")
        mag_weighted_gap = _mag_weighted_consistency(events, sym, "excess_gap", "excess_gap")

        # Eligible if EITHER close-to-close OR gap-phase shows MIN_DIRECTION_CONSISTENCY+/N
        cascade_eligible_close = n_valid >= 6 and direction_consistency >= MIN_DIRECTION_CONSISTENCY
        cascade_eligible_gap = n_gap_valid >= 6 and gap_direction_consistency >= MIN_DIRECTION_CONSISTENCY
        cascade_eligible = cascade_eligible_close or cascade_eligible_gap

        profiles[sym] = {
            # Close-to-close metrics (original)
            "direction_consistency": direction_consistency,
            "n_events_valid": n_valid,
            "direction_consistency_frac": round(direction_consistency / max(n_valid, 1), 4),
            "mag_weighted_close": _safe_float(mag_weighted_close),
            "avg_excess_day1": _safe_float(np.mean(excess_1d_vals)) if excess_1d_vals else None,
            "avg_excess_day2": _safe_float(np.mean(excess_2d_vals)) if excess_2d_vals else None,
            "avg_excess_day5": _safe_float(np.mean(excess_5d_vals)) if excess_5d_vals else None,
            "avg_raw_day1": _safe_float(np.mean(raw_1d_vals)) if raw_1d_vals else None,
            "median_sympathy_ratio": _safe_float(np.median(sympathy_ratios)) if sympathy_ratios else None,
            # Gap-phase metrics (captures kneejerk before intraday correction)
            "gap_direction_consistency": gap_direction_consistency,
            "n_gap_events_valid": n_gap_valid,
            "gap_direction_consistency_frac": round(gap_direction_consistency / max(n_gap_valid, 1), 4),
            "mag_weighted_gap": _safe_float(mag_weighted_gap),
            "avg_excess_gap": _safe_float(np.mean(excess_gap_vals)) if excess_gap_vals else None,
            "avg_excess_intraday": _safe_float(np.mean(excess_intraday_vals)) if excess_intraday_vals else None,
            "avg_raw_gap": _safe_float(np.mean(raw_gap_vals)) if raw_gap_vals else None,
            "median_gap_sympathy_ratio": _safe_float(np.median(gap_sympathy_ratios)) if gap_sympathy_ratios else None,
            # Eligibility
            "cascade_eligible": cascade_eligible,
            "cascade_eligible_close": cascade_eligible_close,
            "cascade_eligible_gap": cascade_eligible_gap,
            "per_event": per_event,
        }

    return profiles


def _analyze_laggards(events: list[dict], profiles: dict[str, dict]) -> dict[str, Any]:
    """Analyze laggard catch-up behavior across events.

    Two analyses (review fix #4: gap decomposition for catch-up):
      A) CLOSE-TO-CLOSE: laggard = low day-1 close excess, catch-up = subsequent close excess
      B) GAP-PHASE: laggard = low gap excess, catch-up = next event's gap excess
         (if cascade lives in the gap, catch-up also happens at the next open)

    Also computes lag_depth distribution for cascade-eligible assets.
    """
    cascade_eligible = [sym for sym, p in profiles.items() if p.get("cascade_eligible")]

    if not cascade_eligible:
        logger.warning("No cascade-eligible assets found — laggard analysis skipped")
        return {
            "distribution": {},
            "catchup_by_cut": {},
            "recommended_threshold": 0.5,
        }

    # ── A) Close-to-close laggard catch-up across ALL assets ──
    laggard_subsequent = []
    non_laggard_subsequent = []
    laggard_catchup_count = 0
    laggard_total = 0

    for event in events:
        all_excess_1d = []
        for sym, adata in event["assets"].items():
            e1 = adata.get("excess_ret_1d")
            if e1 is not None:
                all_excess_1d.append((sym, e1))

        if len(all_excess_1d) < 4:
            continue

        median_excess = np.median([x[1] for x in all_excess_1d])

        for sym, e1 in all_excess_1d:
            adata = event["assets"][sym]
            e2 = adata.get("excess_ret_2d")
            e5 = adata.get("excess_ret_5d")

            if e5 is not None and e1 is not None:
                subsequent = e5 - e1
            elif e2 is not None and e1 is not None:
                subsequent = e2 - e1
            else:
                continue

            if e1 < median_excess:
                laggard_subsequent.append(subsequent)
                laggard_total += 1
                if subsequent > 0:
                    laggard_catchup_count += 1
            else:
                non_laggard_subsequent.append(subsequent)

    # ── B) Gap-phase laggard catch-up (review fix #4) ──
    # If the cascade lives in the gap, laggards are identified by gap excess
    # and catch-up is measured by the intraday excess (gap reversal) or
    # subsequent close excess (does the gap laggard catch up by close?)
    gap_laggard_subsequent = []
    gap_non_laggard_subsequent = []
    gap_laggard_catchup_count = 0
    gap_laggard_total = 0

    for event in events:
        all_gap_excess = []
        for sym, adata in event["assets"].items():
            eg = adata.get("excess_gap")
            if eg is not None:
                all_gap_excess.append((sym, eg))

        if len(all_gap_excess) < 4:
            continue

        median_gap_excess = np.median([x[1] for x in all_gap_excess])

        for sym, eg in all_gap_excess:
            adata = event["assets"][sym]
            # Catch-up measured as: close excess minus gap excess
            # i.e., did the gap laggard recover by end of day?
            e1 = adata.get("excess_ret_1d")
            if e1 is not None and eg is not None:
                subsequent_gap = e1 - eg  # intraday catch-up after gap
            else:
                continue

            if eg < median_gap_excess:
                gap_laggard_subsequent.append(subsequent_gap)
                gap_laggard_total += 1
                if subsequent_gap > 0:
                    gap_laggard_catchup_count += 1
            else:
                gap_non_laggard_subsequent.append(subsequent_gap)

    # ── 2. Lag depth distribution for cascade-eligible assets ──
    lag_depths = []
    lag_depth_events = []
    # Also gap-phase lag depths
    gap_lag_depths = []
    gap_lag_depth_events = []

    for sym in cascade_eligible:
        avg_excess_1d = profiles[sym].get("avg_excess_day1")
        avg_excess_gap = profiles[sym].get("avg_excess_gap")

        for event_idx, event in enumerate(events):
            adata = event["assets"].get(sym, {})

            # Close-to-close lag depth
            e1 = adata.get("excess_ret_1d")
            e5 = adata.get("excess_ret_5d")
            if e1 is not None and avg_excess_1d is not None and abs(avg_excess_1d) > 1e-6:
                depth = e1 / avg_excess_1d
                lag_depths.append(depth)
                subsequent = (e5 - e1) if e5 is not None else None
                lag_depth_events.append({
                    "symbol": sym,
                    "event_idx": event_idx,
                    "lag_depth": depth,
                    "subsequent_excess": subsequent,
                })

            # Gap-phase lag depth
            eg = adata.get("excess_gap")
            if eg is not None and avg_excess_gap is not None and abs(avg_excess_gap) > 1e-6:
                gap_depth = eg / avg_excess_gap
                gap_lag_depths.append(gap_depth)
                # Gap catch-up = close excess minus gap excess (intraday recovery)
                gap_subsequent = (e1 - eg) if e1 is not None and eg is not None else None
                gap_lag_depth_events.append({
                    "symbol": sym,
                    "event_idx": event_idx,
                    "lag_depth": gap_depth,
                    "subsequent_excess": gap_subsequent,
                })

    # ── Distributions and catch-up by percentile ──

    def _compute_distribution(depths):
        if not depths:
            return {}
        arr = np.array(depths)
        return {
            "min": _safe_float(np.min(arr)),
            "p10": _safe_float(np.percentile(arr, 10)),
            "p25": _safe_float(np.percentile(arr, 25)),
            "p50": _safe_float(np.percentile(arr, 50)),
            "p75": _safe_float(np.percentile(arr, 75)),
            "p90": _safe_float(np.percentile(arr, 90)),
            "max": _safe_float(np.max(arr)),
            "n": len(depths),
        }

    def _compute_catchup_by_cut(depth_events):
        result = {}
        if not depth_events:
            return result
        arr = np.array([e["lag_depth"] for e in depth_events])
        for label, pct in [("below_p10", 10), ("below_p25", 25), ("below_p50", 50), ("below_p75", 75)]:
            cut = np.percentile(arr, pct)
            subset = [e for e in depth_events if e["lag_depth"] <= cut and e["subsequent_excess"] is not None]
            if subset:
                catches = sum(1 for e in subset if e["subsequent_excess"] > 0)
                avg_sub = np.mean([e["subsequent_excess"] for e in subset])
                result[label] = {
                    "threshold": _safe_float(cut),
                    "n_observations": len(subset),
                    "catchup_rate": round(catches / len(subset), 4),
                    "avg_subsequent_excess": _safe_float(avg_sub),
                }
        return result

    # Close-to-close
    distribution = _compute_distribution(lag_depths)
    catchup_by_cut = _compute_catchup_by_cut(lag_depth_events)

    # Gap-phase
    gap_distribution = _compute_distribution(gap_lag_depths)
    gap_catchup_by_cut = _compute_catchup_by_cut(gap_lag_depth_events)

    # Recommended threshold: pick the cut with best catch-up rate (across both)
    best_cut = "below_p50"
    best_rate = 0.0
    for source in [catchup_by_cut, gap_catchup_by_cut]:
        for label, data in source.items():
            if data["catchup_rate"] > best_rate and data["n_observations"] >= 3:
                best_rate = data["catchup_rate"]
                best_cut = label

    recommended_threshold = 0.5
    # Check gap first (review says gap is where the action is), then close
    for source in [gap_catchup_by_cut, catchup_by_cut]:
        if best_cut in source:
            recommended_threshold = source[best_cut].get("threshold", 0.5)
            break

    return {
        "distribution": distribution,
        "catchup_by_cut": catchup_by_cut,
        "gap_distribution": gap_distribution,
        "gap_catchup_by_cut": gap_catchup_by_cut,
        "recommended_threshold": _safe_float(recommended_threshold),
        "overall_laggard_catchup": {
            "close_to_close": {
                "laggard_mean_subsequent": _safe_float(np.mean(laggard_subsequent)) if laggard_subsequent else None,
                "non_laggard_mean_subsequent": _safe_float(np.mean(non_laggard_subsequent)) if non_laggard_subsequent else None,
                "laggard_catchup_rate": round(laggard_catchup_count / max(laggard_total, 1), 4),
                "n_laggard_obs": laggard_total,
                "n_non_laggard_obs": len(non_laggard_subsequent),
                "laggards_outperform": bool(
                    laggard_subsequent and non_laggard_subsequent
                    and np.mean(laggard_subsequent) > np.mean(non_laggard_subsequent)
                ),
            },
            "gap_phase": {
                "laggard_mean_subsequent": _safe_float(np.mean(gap_laggard_subsequent)) if gap_laggard_subsequent else None,
                "non_laggard_mean_subsequent": _safe_float(np.mean(gap_non_laggard_subsequent)) if gap_non_laggard_subsequent else None,
                "laggard_catchup_rate": round(gap_laggard_catchup_count / max(gap_laggard_total, 1), 4),
                "n_laggard_obs": gap_laggard_total,
                "n_non_laggard_obs": len(gap_non_laggard_subsequent),
                "laggards_outperform": bool(
                    gap_laggard_subsequent and gap_non_laggard_subsequent
                    and np.mean(gap_laggard_subsequent) > np.mean(gap_non_laggard_subsequent)
                ),
            },
        },
    }


# ── Report Generation ──────────────────────────────────────────────────────

def generate_report(result: dict[str, Any]) -> str:
    """Generate human-readable markdown report."""
    lines = []
    lines.append("# NVDA Earnings Cascade Analysis Report")
    lines.append(f"\nGenerated: {result['generated_ts']}")
    lines.append(f"Events analyzed: {result['n_events']}")
    lines.append(f"Next earnings: {result['next_earnings']} (reaction: {result['next_reaction_date']})")

    # NVDA reactions table
    lines.append("\n## NVDA Reactions Per Event\n")
    lines.append("| Event Date | Reaction | ret_1d | gap | intraday | ret_5d | Vol Ratio | SPY_1d | SPY_gap |")
    lines.append("|------------|----------|--------|-----|----------|--------|-----------|--------|---------|")
    for event in result["events"]:
        nv = event["nvda"]
        sp = event["spy"]
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            event["earnings_date"],
            event["reaction_date"],
            _fmt(nv.get("ret_1d")),
            _fmt(nv.get("overnight_gap")),
            _fmt(nv.get("intraday_ret")),
            _fmt(nv.get("ret_5d")),
            _fmtx(nv.get("volume_ratio"), "x"),
            _fmt(sp.get("ret_1d")),
            _fmt(sp.get("overnight_gap")),
        ))

    # NVDA decomposition: gap vs intraday
    lines.append("\n## NVDA Gap vs Intraday Decomposition\n")
    lines.append("| Event Date | Gap (open/prev_close) | Intraday (close/open) | Net (close/prev_close) | Gap→Close Delta |")
    lines.append("|------------|----------------------|----------------------|----------------------|-----------------|")
    for event in result["events"]:
        nv = event["nvda"]
        gap = nv.get("overnight_gap")
        intra = nv.get("intraday_ret")
        net = nv.get("ret_1d")
        delta = None
        if gap is not None and net is not None:
            delta = net - gap
        lines.append("| {} | {} | {} | {} | {} |".format(
            event["earnings_date"],
            _fmt(gap),
            _fmt(intra),
            _fmt(net),
            _fmt(delta),
        ))

    # NVDA excess returns
    lines.append("\n## NVDA Excess Returns (After Decontaminated SPY Beta Control)\n")
    lines.append("| Event Date | excess_close | excess_gap | excess_intraday | SPY_beta |")
    lines.append("|------------|-------------|------------|-----------------|----------|")
    for event in result["events"]:
        nv = event["nvda"]
        lines.append("| {} | {} | {} | {} | {} |".format(
            event["earnings_date"],
            _fmt(nv.get("excess_ret_1d")),
            _fmt(nv.get("excess_gap")),
            _fmt(nv.get("excess_intraday")),
            _fmtx(nv.get("spy_beta"), "", decimals=2),
        ))

    # Cascade-eligible assets — CLOSE-TO-CLOSE
    eligible_close = [(s, p) for s, p in result["assets"].items() if p.get("cascade_eligible_close")]
    eligible_close.sort(key=lambda x: x[1].get("direction_consistency", 0), reverse=True)

    # Contamination diagnostics (review fix: NVDA is ~6% of SPY)
    lines.append("\n## SPY Decontamination Diagnostics\n")
    lines.append(f"*NVDA weight in SPY: {NVDA_SPY_WEIGHT:.0%}. SPY_ex_NVDA = (SPY - w*NVDA) / (1-w)*\n")
    lines.append("| Event Date | SPY Raw | SPY Clean | Contamination | NVDA Ret |")
    lines.append("|------------|---------|-----------|---------------|----------|")
    for event in result["events"]:
        sp = event["spy"]
        nv = event["nvda"]
        lines.append("| {} | {} | {} | {} | {} |".format(
            event["earnings_date"],
            _fmt(sp.get("ret_1d")),
            _fmt(sp.get("clean_1d")),
            _fmt(sp.get("contamination_1d")),
            _fmt(nv.get("ret_1d")),
        ))

    lines.append(f"\n## Cascade-Eligible: Close-to-Close (direction_consistency >= {MIN_DIRECTION_CONSISTENCY}/N)\n")
    if eligible_close:
        lines.append("| Symbol | Dir Consist | Mag-Wgt | Avg Excess D1 | Avg Excess D5 | Sympathy Ratio |")
        lines.append("|--------|-------------|---------|---------------|---------------|----------------|")
        for sym, p in eligible_close:
            lines.append("| {} | {}/{} | {} | {} | {} | {} |".format(
                sym,
                p.get("direction_consistency", 0),
                p.get("n_events_valid", 0),
                _fmtx(p.get("mag_weighted_close"), "", decimals=2),
                _fmt(p.get("avg_excess_day1")),
                _fmt(p.get("avg_excess_day5")),
                _fmtx(p.get("median_sympathy_ratio"), "", decimals=2),
            ))
    else:
        lines.append(f"**No assets reached {MIN_DIRECTION_CONSISTENCY}/N at close-to-close level.**")

    # Cascade-eligible assets — GAP-PHASE (THE KEY NEW ANALYSIS)
    eligible_gap = [(s, p) for s, p in result["assets"].items() if p.get("cascade_eligible_gap")]
    eligible_gap.sort(key=lambda x: x[1].get("gap_direction_consistency", 0), reverse=True)

    lines.append(f"\n## Cascade-Eligible: GAP PHASE (direction_consistency >= {MIN_DIRECTION_CONSISTENCY}/N)\n")
    lines.append("*Gap = overnight kneejerk before intraday correction. This is where sympathy trades live.*\n")
    if eligible_gap:
        lines.append("| Symbol | Gap Dir Consist | Mag-Wgt | Avg Excess Gap | Avg Excess Intraday | Gap Sympathy Ratio |")
        lines.append("|--------|----------------|---------|----------------|--------------------|--------------------|")
        for sym, p in eligible_gap:
            lines.append("| {} | {}/{} | {} | {} | {} | {} |".format(
                sym,
                p.get("gap_direction_consistency", 0),
                p.get("n_gap_events_valid", 0),
                _fmtx(p.get("mag_weighted_gap"), "", decimals=2),
                _fmt(p.get("avg_excess_gap")),
                _fmt(p.get("avg_excess_intraday")),
                _fmtx(p.get("median_gap_sympathy_ratio"), "", decimals=2),
            ))
    else:
        lines.append(f"**No assets reached {MIN_DIRECTION_CONSISTENCY}/N at gap-phase level either.**")

    # Combined eligible (either close or gap)
    eligible = [(s, p) for s, p in result["assets"].items() if p.get("cascade_eligible")]
    eligible.sort(key=lambda x: max(x[1].get("direction_consistency", 0), x[1].get("gap_direction_consistency", 0)), reverse=True)

    # Full asset table (top 20 by BEST of close or gap consistency)
    lines.append("\n## All Assets by Direction Consistency (Top 20)\n")
    all_sorted = sorted(
        result["assets"].items(),
        key=lambda x: (
            max(x[1].get("direction_consistency", 0), x[1].get("gap_direction_consistency", 0)),
            abs(x[1].get("avg_excess_day1") or 0),
        ),
        reverse=True,
    )[:20]
    lines.append("| Symbol | Close Consist | Mag-Wgt | Gap Consist | Mag-Wgt | Avg Excess D1 | Avg Excess Gap | Cascade? |")
    lines.append("|--------|--------------|---------|------------|---------|---------------|----------------|----------|")
    for sym, p in all_sorted:
        flag = ""
        if p.get("cascade_eligible_close") and p.get("cascade_eligible_gap"):
            flag = "BOTH"
        elif p.get("cascade_eligible_gap"):
            flag = "GAP"
        elif p.get("cascade_eligible_close"):
            flag = "CLOSE"
        else:
            flag = "no"
        lines.append("| {} | {}/{} | {} | {}/{} | {} | {} | {} | {} |".format(
            sym,
            p.get("direction_consistency", 0),
            p.get("n_events_valid", 0),
            _fmtx(p.get("mag_weighted_close"), "", decimals=2),
            p.get("gap_direction_consistency", 0),
            p.get("n_gap_events_valid", 0),
            _fmtx(p.get("mag_weighted_gap"), "", decimals=2),
            _fmt(p.get("avg_excess_day1")),
            _fmt(p.get("avg_excess_gap")),
            flag,
        ))

    # Laggard analysis
    lines.append("\n## Laggard Catch-Up Analysis\n")

    # Helper to render a distribution + catch-up table
    def _render_lag_section(dist_key, catchup_key, phase_label):
        dist = result.get(dist_key, {})
        if dist:
            lines.append(f"\n### {phase_label}: Lag Depth Distribution (cascade-eligible)\n")
            lines.append(f"- N observations: {dist.get('n', 0)}")
            for k in ["min", "p10", "p25", "p50", "p75", "p90", "max"]:
                v = dist.get(k)
                lines.append(f"- {k}: {v:.3f}" if v is not None else f"- {k}: N/A")

        catchup = result.get(catchup_key, {})
        if catchup:
            lines.append(f"\n### {phase_label}: Catch-Up Rate by Lag Depth Percentile\n")
            lines.append("| Cut | Threshold | N obs | Catch-Up Rate | Avg Subsequent |")
            lines.append("|-----|-----------|-------|---------------|----------------|")
            for label, data in catchup.items():
                lines.append("| {} | {:.3f} | {} | {:.1%} | {} |".format(
                    label,
                    data.get("threshold", 0),
                    data.get("n_observations", 0),
                    data.get("catchup_rate", 0),
                    _fmt(data.get("avg_subsequent_excess")),
                ))

    _render_lag_section("laggard_distribution", "laggard_catchup_by_cut", "Close-to-Close")
    _render_lag_section("gap_laggard_distribution", "gap_laggard_catchup_by_cut", "Gap-Phase")

    lines.append(f"\n**Recommended laggard threshold (lag_depth):** {result.get('recommended_laggard_threshold', 'N/A')}")

    # Crypto (informational)
    lines.append("\n## Crypto Overlap (Informational Only)\n")
    has_crypto = False
    for event in result["events"]:
        if event.get("crypto"):
            has_crypto = True
            break
    if has_crypto:
        lines.append("| Event | BTCUSDT D1 | ETHUSDT D1 | NVDA D1 |")
        lines.append("|-------|-----------|-----------|---------|")
        for event in result["events"]:
            btc = event.get("crypto", {}).get("BTCUSDT", {})
            eth = event.get("crypto", {}).get("ETHUSDT", {})
            lines.append("| {} | {} | {} | {} |".format(
                event["earnings_date"],
                _fmt(btc.get("raw_ret_1d")),
                _fmt(eth.get("raw_ret_1d")),
                _fmt(event["nvda"].get("ret_1d")),
            ))
        lines.append("\n*Crypto has limited overlapping events. No signals will be generated.*")
    else:
        lines.append("No crypto OHLCV data available for these event dates.")

    # Per-event detail for cascade-eligible assets
    if eligible:
        lines.append("\n## Per-Event Detail (Cascade-Eligible Assets)\n")
        for sym, p in eligible:
            lines.append(f"\n### {sym} (consistency: {p['direction_consistency']}/{p['n_events_valid']})\n")
            lines.append("| Event | Raw D1 | Beta | Excess D1 | Excess D5 | NVDA D1 |")
            lines.append("|-------|--------|------|-----------|-----------|---------|")
            for pe in p.get("per_event", []):
                lines.append("| {} | {} | {} | {} | {} | {} |".format(
                    pe.get("earnings_date", ""),
                    _fmt(pe.get("raw_ret_1d")),
                    _fmtx(pe.get("spy_beta"), "", decimals=2),
                    _fmt(pe.get("excess_ret_1d")),
                    _fmt(pe.get("excess_ret_5d")),
                    _fmt(pe.get("nvda_ret_1d")),
                ))

    # Decision gate
    n_eligible_close = len(eligible_close)
    n_eligible_gap = len(eligible_gap)
    n_eligible = len(eligible)
    lines.append("\n## Decision Gate\n")
    lines.append(f"- Close-to-close eligible: **{n_eligible_close}** assets")
    lines.append(f"- Gap-phase eligible: **{n_eligible_gap}** assets")
    lines.append(f"- Combined (either): **{n_eligible}** assets")
    lines.append(f"- Threshold for D3 implementation: >= 3 assets (either metric)")
    if n_eligible >= 3:
        if n_eligible_gap >= 3 and n_eligible_close < 3:
            lines.append(f"- **PASS (GAP ONLY): {n_eligible_gap} assets show gap-phase cascade. D3 needs gap-based trigger (not close-to-close).**")
        elif n_eligible_close >= 3:
            lines.append(f"- **PASS: {n_eligible} assets show genuine cascade. D3 implementation can proceed.**")
        else:
            lines.append(f"- **PASS (MIXED): {n_eligible} assets eligible across metrics. Review individually.**")
    else:
        lines.append(f"- **FAIL: Only {n_eligible} assets show cascade at either level. Do NOT implement D3.**")

    return "\n".join(lines)


def _fmt(v, decimals: int = 2) -> str:
    """Format a percentage value for the table."""
    if v is None:
        return "N/A"
    return f"{v * 100:+.{decimals}f}%"


def _fmtx(v, suffix: str = "x", decimals: int = 1) -> str:
    """Format a ratio value."""
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}{suffix}"


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NVDA Earnings Cascade Analyzer")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    _setup_logging()
    logger.info("NVDA Earnings Cascade Analyzer starting...")

    conn = connect(args.database_url)
    try:
        result = analyze_cascade(conn)
    finally:
        conn.close()

    # Write outputs
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    json_path = os.path.join(OUTPUT_DIR, "nvda_cascade_profiles.json")
    with open(json_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info("Wrote cascade profiles to %s", json_path)

    report = generate_report(result)
    md_path = os.path.join(OUTPUT_DIR, "nvda_cascade_report.md")
    with open(md_path, "w") as f:
        f.write(report)
    logger.info("Wrote report to %s", md_path)

    # Print summary
    summary = result.get("summary", {})
    n_close = summary.get("cascade_eligible_close_count", 0)
    n_gap = summary.get("cascade_eligible_gap_count", 0)
    n_either = summary.get("cascade_eligible_count", 0)
    logger.info("=" * 60)
    logger.info("SUMMARY: eligible close=%d, gap=%d, either=%d (need >= 3)", n_close, n_gap, n_either)
    logger.info("Analysis completed in %.1fs", summary.get("elapsed_s", 0))
    if n_either >= 3:
        if n_gap >= 3 and n_close < 3:
            logger.info("DECISION: GAP-PHASE CASCADE EXISTS — D3 needs gap-based trigger redesign")
        else:
            logger.info("DECISION: CASCADE IS REAL — proceed with D3 implementation")
    else:
        logger.info("DECISION: NO CASCADE at either level — do NOT implement D3")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
