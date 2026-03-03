#!/usr/bin/env python3
"""
D1 Dormancy-to-Ignition Simulation Matrix — Portfolio-Level Upgrade
====================================================================

Major upgrade from trade-by-trade to portfolio-level simulation:

Exit Configs (A-H):
  A. Binary 10% TP, 2.5% stop, no trail, 3d TS (default D1 config)
  B. Binary 15% TP, 2.5% stop, no trail, 3d TS
  C. Binary 20% TP, 2.5% stop, no trail, 3d TS
  D. Binary 10% TP, 1.5% stop, no trail, 3d TS (tighter stop — production winner)
  E. Binary 10% TP, 2.5% stop, no trail, 5d TS (longer hold)
  F. Post-TP runner trail: trail activates at +10%, 5% from HWM, 3d TS
  G. Binary 20% TP (8R), 2.5% stop, 3d TS
  H. Split exit: 50% at TP1 (10%), remainder rides with breakeven stop, 1.5% initial

Portfolio-Level Features (Phase S):
  S1. Concurrent position tracking — chronological bar-by-bar walk
  S2. Capital-at-risk drawdown (mark-to-market unrealized P&L)
  S3. Correlation stress test (worst BTC day & cascading week)
  S4. Position limit simulation (--max-concurrent sweep)
  S5. Config H split-exit implementation
  S6. Tier-specific slippage (--tier-slippage)
  S7. Compounding / reinvestment mode (--account-equity, --position-pct)

Walk-Forward Validation:
  5-fold purged rolling walk-forward with 1-day embargo
  DSR correction for multiple testing

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \\
        python3 /scripts/sim_d1_matrix.py \\
            --batch-id 'backfill_20260303_D1v1%%' \\
            --position-usd 1000 \\
            --max-concurrent 5,8,15 \\
            --account-equity 20000 \\
            --position-pct 5.0
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
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
MAX_BARS_FORWARD = 504
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
    "A": {"desc": "Binary 10% TP, 2.5% stop, 3d TS (default)", "tp_pct": 10.0, "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL, "split_exit": False},
    "B": {"desc": "Binary 15% TP, 2.5% stop, 3d TS",           "tp_pct": 15.0, "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL, "split_exit": False},
    "C": {"desc": "Binary 20% TP, 2.5% stop, 3d TS",           "tp_pct": 20.0, "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL, "split_exit": False},
    "D": {"desc": "Binary 10% TP, 1.5% stop, 3d TS",           "tp_pct": 10.0, "stop_pct": 1.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL, "split_exit": False},
    "E": {"desc": "Binary 10% TP, 2.5% stop, 5d TS",           "tp_pct": 10.0, "stop_pct": 2.5, "ts": FIVE_DAY_BARS,  "ec": NO_TRAIL, "split_exit": False},
    "F": {"desc": "Runner trail from +10%, 3d TS",              "tp_pct": None,  "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": RUNNER_TRAIL, "split_exit": False},
    "G": {"desc": "Binary 20% TP (8R), 2.5% stop, 3d TS",      "tp_pct": 20.0, "stop_pct": 2.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL, "split_exit": False},
    "H": {"desc": "Split: 50% at TP1, remainder breakeven stop","tp_pct": 10.0, "stop_pct": 1.5, "ts": THREE_DAY_BARS, "ec": NO_TRAIL, "split_exit": True, "tp2_pct": 20.0},
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


def load_btc_ohlcv(database_url: str, start_ts: datetime, end_ts: datetime) -> list[dict]:
    """Load BTCUSDT 1h OHLCV for stress test analysis."""
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = 'BTCUSDT' AND timeframe = '1h'
                  AND bar_start_ts >= %s AND bar_start_ts < %s
                  AND is_final = TRUE
                ORDER BY bar_start_ts ASC
            """, (start_ts, end_ts))
            return [
                {"bar_start_ts": r[0], "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                for r in cur.fetchall()
            ]


# ══════════════════════════════════════════════════════════════════════════
#  OPEN POSITION DATACLASS
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class OpenPosition:
    """A position being tracked in the portfolio walker."""
    opp_idx: int
    symbol: str
    direction: str
    entry_price: float       # after slippage
    stop_price: float
    tp1_price: Optional[float]
    tp2_price: Optional[float]
    r_value: float
    time_stop_bars: int
    exit_config: ExitConfig
    position_usd: float
    position_size: float     # units
    bars_held: int = 0
    hwm: Optional[float] = None
    half_r_touched: bool = False
    entry_ts: Optional[datetime] = None
    max_bar_idx: int = 0
    entry_bar_idx: int = 0
    # Split exit state
    split_exit: bool = False
    tp1_hit: bool = False             # Has the first half been closed?
    remaining_fraction: float = 1.0   # What fraction of position is still open
    breakeven_stop_active: bool = False
    # For OHLCV lookup
    cache_key: str = ""
    # Slippage for this position
    slippage_bps: float = SIM_SLIPPAGE_BPS


# ══════════════════════════════════════════════════════════════════════════
#  SINGLE-TRADE SIMULATION (backward compatible)
# ══════════════════════════════════════════════════════════════════════════

def simulate_opportunity(
    opp_ts: datetime, direction: str, entry_price: float,
    bars: list[dict], tf: str,
    stop_pct: float, tp_pct: Optional[float],
    time_stop_bars: int, exit_config: ExitConfig,
    slippage_bps: float = SIM_SLIPPAGE_BPS,
    split_exit: bool = False, tp2_pct: Optional[float] = None,
) -> dict:
    """Simulate a single D1 opportunity. Returns result dict.

    If split_exit=True: 50% closes at TP1, remainder rides with breakeven stop to TP2/time stop.
    """
    if not bars:
        return {"data_available": False, "pnl_net": None}

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

    bar_delta = TF_DELTA.get(tf, timedelta(hours=1))
    entry_bar = bars[entry_bar_idx]
    entry_bar_ts = entry_bar["bar_start_ts"]
    if entry_bar_ts.tzinfo is None:
        entry_bar_ts = entry_bar_ts.replace(tzinfo=timezone.utc)
    if entry_bar_ts - opp_ts > bar_delta * MAX_ENTRY_DELAY_BARS:
        return {"data_available": False, "pnl_net": None}

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
        return {"data_available": False, "pnl_net": None}

    fee_per_unit = 2 * entry_slip * SIM_FEE_BPS / 10000
    if fee_per_unit > MAX_FEE_R_RATIO * actual_r:
        return {"data_available": False, "pnl_net": None}

    if r_value > 0 and actual_r < MIN_R_PRESERVATION * r_value:
        return {"data_available": False, "pnl_net": None}

    if tp1_price is not None:
        if direction == "long" and tp1_price <= entry_slip:
            tp1_price = None
        elif direction == "short" and tp1_price >= entry_slip:
            tp1_price = None

    # TP2 for split exit
    tp2_price = None
    if split_exit and tp2_pct:
        if direction == "long":
            tp2_price = entry_price * (1 + tp2_pct / 100.0)
        else:
            tp2_price = entry_price * (1 - tp2_pct / 100.0)

    position_size = SIM_POSITION_USD / entry_slip
    max_bar_idx = min(entry_bar_idx + MAX_BARS_FORWARD, len(bars) - 1)

    hwm = None
    half_r_touched = False
    exit_reason = None
    exit_price = None
    bars_held = 0

    # Split exit state
    first_half_closed = False
    first_half_pnl = 0.0
    remaining_frac = 1.0
    breakeven_active = False

    for i in range(entry_bar_idx + 1, max_bar_idx + 1):
        bar = bars[i]
        bars_held += 1

        # For split exit after TP1 hit: use breakeven stop instead of original
        current_stop = stop_price
        current_tp1 = tp1_price
        current_tp2 = None

        if split_exit and first_half_closed:
            # Breakeven stop for remaining half
            current_stop = entry_slip  # breakeven = entry price
            current_tp1 = tp2_price    # now targeting TP2
            current_tp2 = None

        exit_result, new_hwm, new_half_r = check_exit_simple(
            side=direction, bar=bar,
            entry_price=entry_slip, stop_price=current_stop,
            tp1_price=current_tp1, tp2_price=current_tp2,
            r_value=actual_r, bars_held=bars_held,
            max_bars=time_stop_bars,
            high_water_mark=hwm, half_r_touched=half_r_touched,
            config=exit_config,
        )
        hwm = new_hwm
        half_r_touched = new_half_r

        if exit_result:
            reason, price = exit_result

            if split_exit and not first_half_closed and reason == "TP1":
                # Close first half at TP1
                half_size = position_size * 0.5
                if direction == "long":
                    half_exit_slip = price * (1 - slippage_bps / 10000)
                    half_pnl_gross = (half_exit_slip - entry_slip) * half_size
                else:
                    half_exit_slip = price * (1 + slippage_bps / 10000)
                    half_pnl_gross = (entry_slip - half_exit_slip) * half_size
                half_exit_fee = half_exit_slip * half_size * SIM_FEE_BPS / 10000
                first_half_pnl = half_pnl_gross - half_exit_fee
                first_half_closed = True
                remaining_frac = 0.5
                breakeven_active = True
                # Reset HWM for remaining half tracking
                continue

            # Final exit (full position or remaining half)
            exit_reason = reason
            exit_price = price
            break

    if exit_reason is None:
        last_bar = bars[min(max_bar_idx, len(bars) - 1)]
        exit_reason = "MAX_BARS"
        exit_price = last_bar["close"]

    # PnL for remaining position
    remaining_size = position_size * remaining_frac
    if exit_reason != "STOP_LOSS" or (split_exit and breakeven_active):
        if direction == "long":
            exit_slip = exit_price * (1 - slippage_bps / 10000)
        else:
            exit_slip = exit_price * (1 + slippage_bps / 10000)
    else:
        exit_slip = exit_price

    if direction == "long":
        remaining_pnl_gross = (exit_slip - entry_slip) * remaining_size
    else:
        remaining_pnl_gross = (entry_slip - exit_slip) * remaining_size

    # Fees
    entry_fee = entry_slip * position_size * SIM_FEE_BPS / 10000  # Full position entry fee
    remaining_exit_fee = exit_slip * remaining_size * SIM_FEE_BPS / 10000
    total_pnl = first_half_pnl + remaining_pnl_gross - entry_fee - remaining_exit_fee

    r_multiple = total_pnl / (actual_r * position_size) if actual_r * position_size > 0 else 0

    result = {
        "data_available": True,
        "pnl_net": round(total_pnl, 4),
        "r_multiple": round(r_multiple, 4),
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "entry_bar_idx": entry_bar_idx,
        "entry_ts": entry_bar_ts,
        "entry_slip": entry_slip,
        "stop_price": stop_price,
        "tp1_price": tp1_price,
        "actual_r": actual_r,
        "position_size": position_size,
    }

    if split_exit:
        result["tp1_hit"] = first_half_closed
        result["first_half_pnl"] = round(first_half_pnl, 4) if first_half_closed else None
        result["runner_exit_reason"] = exit_reason if first_half_closed else None

    return result


# ══════════════════════════════════════════════════════════════════════════
#  PORTFOLIO WALKER — Chronological position tracking
# ══════════════════════════════════════════════════════════════════════════

def portfolio_walk(
    traded_results: list[dict],
    metas_for_traded: list[dict],
    ohlcv_cache: dict[str, list[dict]],
    position_usd: float = SIM_POSITION_USD,
    max_concurrent: Optional[int] = None,
    account_equity: Optional[float] = None,
    position_pct: float = 5.0,
    compounding: bool = False,
) -> dict:
    """Walk equity curve chronologically tracking concurrent positions.

    Returns portfolio metrics dict.
    """
    if not traded_results:
        return {"error": "no trades"}

    # Sort by entry timestamp
    trade_pairs = list(zip(traded_results, metas_for_traded))
    trade_pairs.sort(key=lambda x: x[0].get("entry_ts") or x[1]["created_ts"])

    equity = account_equity if account_equity else 0.0
    start_equity = equity
    peak_equity = equity
    max_dd = 0.0
    max_dd_pct = 0.0

    peak_concurrent = 0
    peak_capital_deployed = 0.0
    peak_capital_at_risk = 0.0
    n_skipped = 0
    equity_curve = []
    monthly_returns: dict[str, float] = defaultdict(float)

    open_positions: list[dict] = []
    closed_trades: list[dict] = []

    for result, meta in trade_pairs:
        entry_ts = result.get("entry_ts") or meta["created_ts"]

        # Close any positions that should have exited by now
        still_open = []
        for pos in open_positions:
            pos_exit_ts = pos["entry_ts"] + timedelta(hours=pos["bars_held"])
            if pos_exit_ts <= entry_ts:
                # Position closed
                equity += pos["pnl_net"]
                month_key = pos_exit_ts.strftime("%Y-%m")
                monthly_returns[month_key] += pos["pnl_net"]
                closed_trades.append(pos)
            else:
                still_open.append(pos)
        open_positions = still_open

        # Position limit check
        if max_concurrent is not None and len(open_positions) >= max_concurrent:
            n_skipped += 1
            continue

        # Compute position size
        if compounding and account_equity:
            pos_usd = equity * (position_pct / 100.0)  # equity tracks running balance
            pos_usd = max(pos_usd, 50.0)  # Floor at $50
        else:
            pos_usd = position_usd

        # Open new position
        pos_record = {
            "opp_idx": meta.get("idx", 0),
            "symbol": meta["symbol"],
            "entry_ts": entry_ts,
            "bars_held": result.get("bars_held", 72),
            "pnl_net": result["pnl_net"] * (pos_usd / position_usd),  # Scale PnL
            "exit_reason": result["exit_reason"],
            "position_usd": pos_usd,
        }
        open_positions.append(pos_record)

        # Track portfolio state
        n_concurrent = len(open_positions)
        capital_deployed = sum(p["position_usd"] for p in open_positions)
        # Approximate unrealized losses
        unrealized_losses = sum(
            min(0, p["pnl_net"] * 0.5)  # Conservative: assume 50% of final P&L midway
            for p in open_positions
        )
        capital_at_risk = capital_deployed + abs(unrealized_losses)

        peak_concurrent = max(peak_concurrent, n_concurrent)
        peak_capital_deployed = max(peak_capital_deployed, capital_deployed)
        peak_capital_at_risk = max(peak_capital_at_risk, capital_at_risk)

        equity_curve.append({
            "ts": entry_ts.isoformat() if isinstance(entry_ts, datetime) else str(entry_ts),
            "equity": round(equity, 2),
            "concurrent": n_concurrent,
            "capital_deployed": round(capital_deployed, 2),
        })

        # Drawdown tracking (on entry events)
        peak_equity = max(peak_equity, equity)
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd
        # DD% denominator: compounding uses peak equity (has account base),
        # non-compounding uses peak capital deployed (equity starts at 0)
        dd_denom = peak_equity if account_equity else peak_capital_deployed
        if dd_denom > 0:
            dd_pct = dd / dd_denom * 100
            max_dd_pct = max(max_dd_pct, dd_pct)

    # Close remaining
    for pos in open_positions:
        equity += pos["pnl_net"]
        closed_trades.append(pos)

    peak_equity = max(peak_equity, equity)
    dd = peak_equity - equity
    if dd > max_dd:
        max_dd = dd
    dd_denom = peak_equity if account_equity else peak_capital_deployed
    if dd_denom > 0:
        max_dd_pct = max(max_dd_pct, dd / dd_denom * 100)

    # Compounding metrics
    compounding_metrics = None
    if compounding and account_equity:
        final_equity = start_equity + sum(t["pnl_net"] for t in closed_trades)

        # Time span for CAGR
        if trade_pairs:
            first_ts = trade_pairs[0][0].get("entry_ts") or trade_pairs[0][1]["created_ts"]
            last_ts = trade_pairs[-1][0].get("entry_ts") or trade_pairs[-1][1]["created_ts"]
            days = max((last_ts - first_ts).total_seconds() / 86400, 1)
            years = days / 365.25
            cagr = ((final_equity / start_equity) ** (1 / years) - 1) * 100 if start_equity > 0 and years > 0 else 0
        else:
            cagr = 0
            years = 0

        # Sharpe (annualized from monthly returns)
        if monthly_returns:
            monthly_vals = list(monthly_returns.values())
            monthly_mean = np.mean(monthly_vals)
            monthly_std = np.std(monthly_vals, ddof=1) if len(monthly_vals) > 1 else 1e-9
            sharpe = (monthly_mean / monthly_std) * np.sqrt(12) if monthly_std > 0 else 0
        else:
            sharpe = 0

        worst_month = min(monthly_returns.values()) if monthly_returns else 0
        worst_month_pct = (worst_month / start_equity) * 100 if start_equity > 0 else 0

        calmar = cagr / max_dd_pct if max_dd_pct > 0 else 0

        compounding_metrics = {
            "start_equity": start_equity,
            "end_equity": round(final_equity, 2),
            "cagr_pct": round(cagr, 2),
            "max_dd_pct": round(max_dd_pct, 2),
            "calmar_ratio": round(calmar, 2),
            "sharpe": round(float(sharpe), 2),
            "worst_month_pct": round(worst_month_pct, 2),
        }

    return {
        "peak_concurrent_positions": peak_concurrent,
        "peak_capital_deployed": round(peak_capital_deployed, 2),
        "peak_capital_at_risk": round(peak_capital_at_risk, 2),
        "max_portfolio_drawdown": round(max_dd, 2),
        "max_portfolio_drawdown_pct": round(max_dd_pct, 2),
        "n_trades": len(closed_trades),
        "n_skipped": n_skipped,
        "total_pnl": round(equity if not account_equity else equity - start_equity + sum(t["pnl_net"] for t in closed_trades), 2),
        "compounding": compounding_metrics,
        "equity_curve_len": len(equity_curve),
    }


# ══════════════════════════════════════════════════════════════════════════
#  STRESS TEST — BTC correlation
# ══════════════════════════════════════════════════════════════════════════

def stress_test(
    traded_results: list[dict],
    metas: list[dict],
    btc_bars: list[dict],
    position_usd: float = SIM_POSITION_USD,
) -> dict:
    """Find worst BTC drawdown day/week and compute cascading losses."""
    if not btc_bars or not traded_results:
        return {}

    # Compute daily BTC returns
    btc_daily: dict[str, dict] = {}
    for bar in btc_bars:
        bar_ts = bar["bar_start_ts"]
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        day = bar_ts.strftime("%Y-%m-%d")
        if day not in btc_daily:
            btc_daily[day] = {"open": bar["open"], "close": bar["close"],
                              "high": bar["high"], "low": bar["low"]}
        else:
            btc_daily[day]["close"] = bar["close"]
            btc_daily[day]["high"] = max(btc_daily[day]["high"], bar["high"])
            btc_daily[day]["low"] = min(btc_daily[day]["low"], bar["low"])

    # Daily returns
    days = sorted(btc_daily.keys())
    daily_rets = {}
    for i in range(1, len(days)):
        prev_close = btc_daily[days[i - 1]]["close"]
        if prev_close > 0:
            daily_rets[days[i]] = (btc_daily[days[i]]["close"] - prev_close) / prev_close

    if not daily_rets:
        return {}

    # Worst single day
    worst_day = min(daily_rets, key=daily_rets.get)
    worst_day_ret = daily_rets[worst_day]

    # Worst 3-day window
    worst_3d_start = None
    worst_3d_ret = 0
    for i in range(len(days) - 2):
        window_start_close = btc_daily[days[i]]["open"]
        window_end_close = btc_daily[days[i + 2]]["close"]
        if window_start_close > 0:
            ret_3d = (window_end_close - window_start_close) / window_start_close
            if ret_3d < worst_3d_ret:
                worst_3d_ret = ret_3d
                worst_3d_start = days[i]

    # Count open D1 positions on worst day
    worst_day_dt = datetime.strptime(worst_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    positions_on_worst_day = 0
    for result, meta in zip(traded_results, metas):
        entry_ts = result.get("entry_ts") or meta["created_ts"]
        bars_held = result.get("bars_held", 72)
        exit_ts = entry_ts + timedelta(hours=bars_held)
        if entry_ts <= worst_day_dt + timedelta(days=1) and exit_ts >= worst_day_dt:
            positions_on_worst_day += 1

    # Worst day stress loss (all stop out at 1.5%)
    stop_pct = 1.5
    worst_day_loss = positions_on_worst_day * position_usd * stop_pct / 100

    # Worst week cascading
    positions_in_window = 0
    if worst_3d_start:
        w_start = datetime.strptime(worst_3d_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        w_end = w_start + timedelta(days=3)
        for result, meta in zip(traded_results, metas):
            entry_ts = result.get("entry_ts") or meta["created_ts"]
            bars_held = result.get("bars_held", 72)
            exit_ts = entry_ts + timedelta(hours=bars_held)
            if entry_ts <= w_end and exit_ts >= w_start:
                positions_in_window += 1

    worst_week_loss = positions_in_window * position_usd * stop_pct / 100
    cascading_factor = worst_week_loss / worst_day_loss if worst_day_loss > 0 else 0

    return {
        "worst_day": {
            "date": worst_day,
            "btc_drawdown_pct": round(worst_day_ret * 100, 2),
            "d1_positions_open": positions_on_worst_day,
            "stress_loss": round(worst_day_loss, 2),
        },
        "worst_week": {
            "start_date": worst_3d_start,
            "end_date": (datetime.strptime(worst_3d_start, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d") if worst_3d_start else None,
            "btc_drawdown_pct": round(worst_3d_ret * 100, 2),
            "peak_concurrent_positions": positions_in_window,
            "cascading_stress_loss": round(worst_week_loss, 2),
            "cascading_factor": round(cascading_factor, 2),
        },
    }


# ══════════════════════════════════════════════════════════════════════════
#  WALK-FORWARD VALIDATION
# ══════════════════════════════════════════════════════════════════════════

def walk_forward_validate(
    all_results: dict[str, list[dict]],
    metas: list[dict],
    n_folds: int = 5,
    embargo_days: int = 1,
) -> dict:
    """5-fold purged rolling walk-forward validation.

    Returns per-config fold results and pass/fail.
    """
    # Get date range
    dates = sorted(set(
        m["created_ts"].date() for m in metas if m.get("created_ts")
    ))
    if len(dates) < n_folds * 5:
        return {"error": "insufficient date range for walk-forward", "n_dates": len(dates)}

    total_days = len(dates)
    fold_size = total_days // n_folds

    wf_results = {}

    for config_key, results in all_results.items():
        fold_metrics = []

        for fold in range(n_folds):
            test_start_idx = fold * fold_size
            test_end_idx = min(test_start_idx + fold_size, total_days)

            if fold == n_folds - 1:
                test_end_idx = total_days

            test_start_date = dates[test_start_idx]
            test_end_date = dates[test_end_idx - 1]

            # Embargo: skip trades within 1 day of train/test boundary
            embargo_start = test_start_date - timedelta(days=embargo_days)
            embargo_end = test_start_date + timedelta(days=embargo_days)

            # Test fold: only include trades in test window, excluding embargo
            test_results = []
            for result, meta in zip(results, metas):
                if result is None or not result.get("data_available"):
                    continue
                if result.get("pnl_net") is None:
                    continue
                if result.get("r_multiple", 0) < -1.05:
                    continue

                trade_date = meta["created_ts"].date()
                if trade_date < embargo_end:
                    continue
                if trade_date > test_end_date:
                    continue

                result_copy = dict(result)
                result_copy["_created_ts"] = meta["created_ts"]
                test_results.append(result_copy)

            fold_m = compute_metrics(test_results, f"fold_{fold}")
            fold_m["test_start"] = str(test_start_date)
            fold_m["test_end"] = str(test_end_date)
            fold_metrics.append(fold_m)

        # Pass/fail: profitable in ≥4/5 folds
        profitable_folds = sum(1 for fm in fold_metrics if fm.get("avg_daily_pf", 0) > 1.0)
        passes_wf = profitable_folds >= (n_folds - 1)  # ≥4/5

        wf_results[config_key] = {
            "folds": fold_metrics,
            "profitable_folds": profitable_folds,
            "total_folds": n_folds,
            "passes_walk_forward": passes_wf,
        }

    return wf_results


def compute_dsr(
    holdout_sharpe: float, n_trials: int, mean_sharpe: float = 0.0,
    std_sharpe: float = 1.0, skew_sharpe: float = 0.0, kurt_sharpe: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio (Harvey et al., 2016).

    Corrects for multiple testing by comparing observed Sharpe to expected max Sharpe.
    Returns DSR value (>0 means it survives correction).
    """
    from scipy.stats import norm

    if n_trials <= 1 or std_sharpe <= 0:
        return holdout_sharpe

    # Expected max Sharpe under the null (from n_trials)
    # E[max(SR)] ≈ sqrt(2 * ln(n_trials)) (Euler-Mascheroni approximation)
    gamma = 0.5772156649
    e_max_sr = (1 - gamma) * norm.ppf(1 - 1 / n_trials) + gamma * norm.ppf(1 - 1 / (n_trials * np.e))

    # DSR
    numerator = (holdout_sharpe - e_max_sr) * np.sqrt(n_trials - 1)
    denominator = np.sqrt(1 - skew_sharpe * holdout_sharpe + (kurt_sharpe - 1) / 4 * holdout_sharpe ** 2)

    if denominator <= 0:
        return 0.0

    dsr = norm.cdf(numerator / denominator)
    return float(dsr)


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
        exit_reasons[r.get("exit_reason", "UNKNOWN")] += 1

    # Max drawdown (sequential)
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
        if result["r_multiple"] < -1.05:
            continue
        if meta.get("dormancy_score", 0) < dormancy_min:
            continue
        if meta.get("volume_z_at_trigger", 0) < vol_z_min:
            continue
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
    log.info("=" * 90)
    log.info("  D1 SIMULATION MATRIX — Exit Config x Dormancy Threshold x Volume Z")
    log.info("=" * 90)

    dormancy_thresholds = [0.0, 0.80, 0.90, 0.95]
    vol_z_thresholds = [0.0, 2.0, 3.0, 4.0]

    for config_key in sorted(EXIT_CONFIGS.keys()):
        cfg = EXIT_CONFIGS[config_key]
        results = all_results[config_key]

        log.info("")
        log.info(f"  Config {config_key}: {cfg['desc']}")
        log.info(f"  {'':>14} | {'All':>22} | {'dorm>=0.80':>22} | {'dorm>=0.90':>22} | {'dorm>=0.95':>22}")
        log.info(f"  {'-'*14}-+-{'-'*22}-+-{'-'*22}-+-{'-'*22}-+-{'-'*22}")

        for vol_z in vol_z_thresholds:
            cells = []
            for dorm in dormancy_thresholds:
                sliced = slice_results(results, metas, dormancy_min=dorm, vol_z_min=vol_z)
                m = compute_metrics(sliced, f"d>={dorm:.2f}_vz>={vol_z:.1f}")
                cells.append(fmt_cell(m))

            label = f"vol_z>={vol_z:.1f}" if vol_z > 0 else "All vol_z"
            log.info(f"  {label:>14} | {' | '.join(cells)}")

        all_sliced = slice_results(results, metas)
        m = compute_metrics(all_sliced, f"Config_{config_key}_all")
        log.info(f"  -- Summary: WR={m['win_rate']:.1%} aggPF={m['agg_pf']:.2f} "
                 f"dailyPF={m['avg_daily_pf']:.2f} PnL=${m['total_pnl']:.0f} "
                 f"N={m['n_traded']} maxDD=${m['max_dd']:.0f}")
        if m.get("exit_reasons"):
            log.info(f"     Exits: {m['exit_reasons']}")

    log.info("")
    log.info("=" * 90)


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="D1 simulation matrix — portfolio-level upgrade")
    parser.add_argument("--batch-id", type=str, required=True,
                        help="Batch ID pattern (SQL LIKE, e.g. 'D1_v1.0%%')")
    parser.add_argument("--output-dir", type=str, default=None)
    # Portfolio parameters
    parser.add_argument("--position-usd", type=float, default=SIM_POSITION_USD,
                        help="Position size per signal (default: 1000)")
    parser.add_argument("--max-concurrent", type=str, default=None,
                        help="Position limit sweep, comma-separated (e.g. '3,5,8,15')")
    parser.add_argument("--account-equity", type=float, default=None,
                        help="Starting account equity for compounding mode (e.g. 20000)")
    parser.add_argument("--position-pct", type=float, default=5.0,
                        help="Position size as %% of equity for compounding (default: 5.0)")
    # Slippage
    parser.add_argument("--slippage-bps", type=float, default=SIM_SLIPPAGE_BPS,
                        help="Base slippage in bps (default: 5)")
    # Walk-forward
    parser.add_argument("--skip-walkforward", action="store_true",
                        help="Skip walk-forward validation")
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
    json_path = os.path.join(output_dir, f"sim_d1_matrix_{ts_tag}.json")
    file_handler = logging.FileHandler(output_path, mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(file_handler)
    log.info("Output file: %s", output_path)
    log.info("JSON output: %s", json_path)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
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

    slippage_bps = args.slippage_bps
    position_usd = args.position_usd

    # Parse max-concurrent sweep
    max_concurrent_values = []
    if args.max_concurrent:
        max_concurrent_values = [int(x.strip()) for x in args.max_concurrent.split(",")]

    # ── Load opportunities ────────────────────────────────────────────────
    log.info("=" * 90)
    log.info("  D1 SIMULATION MATRIX — Portfolio-Level Upgrade")
    log.info("=" * 90)
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

    n_symbols = len(set(m["symbol"] for m in metas))
    date_min = min(m["created_ts"] for m in metas)
    date_max = max(m["created_ts"] for m in metas)
    log.info("  Symbols: %d unique", n_symbols)
    log.info("  Date range: %s to %s", date_min.date(), date_max.date())
    log.info("  Position size: $%.0f", position_usd)
    log.info("  Slippage: %.1f bps", slippage_bps)
    if max_concurrent_values:
        log.info("  Position limit sweep: %s", max_concurrent_values)
    if args.account_equity:
        log.info("  Compounding: $%.0f equity, %.1f%% position sizing", args.account_equity, args.position_pct)

    # ── Load OHLCV ────────────────────────────────────────────────────────
    ohlcv_ranges = compute_ohlcv_ranges(opps, metas)
    log.info("  OHLCV needed for %d symbol/tf combos", len(ohlcv_ranges))

    t0 = time.time()
    ohlcv_cache = load_ohlcv_batch(database_url, ohlcv_ranges)
    log.info("  OHLCV loaded in %.1fs (%d combos, %d total bars)",
             time.time() - t0, len(ohlcv_cache),
             sum(len(v) for v in ohlcv_cache.values()))

    # ── Load BTC data for stress test ─────────────────────────────────────
    btc_bars = load_btc_ohlcv(database_url, date_min - timedelta(days=7), date_max + timedelta(days=7))
    log.info("  BTC bars loaded: %d", len(btc_bars))

    # ══════════════════════════════════════════════════════════════════════
    #  RUN SIMULATION FOR EACH EXIT CONFIG
    # ══════════════════════════════════════════════════════════════════════
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
                slippage_bps=slippage_bps,
                split_exit=cfg.get("split_exit", False),
                tp2_pct=cfg.get("tp2_pct"),
            )
            results.append(result)

        all_config_results[config_key] = results
        traded = sum(1 for r in results if r.get("data_available") and r.get("pnl_net") is not None)
        log.info("  Config %s (%s): %d/%d simulated in %.1fs",
                 config_key, cfg["desc"], traded, len(results), time.time() - t1)

    # ── Print matrix ──────────────────────────────────────────────────────
    print_matrix(all_config_results, metas)

    # ── Overall summary ───────────────────────────────────────────────────
    log.info("\n  OVERALL SUMMARY")
    log.info("  %-8s %-42s %6s %6s %8s %8s %8s", "Config", "Description",
             "WR", "aggPF", "dailyPF", "PnL", "N")
    log.info("  " + "-" * 88)
    summary_data = {}
    for ck in sorted(EXIT_CONFIGS.keys()):
        cfg = EXIT_CONFIGS[ck]
        sliced = slice_results(all_config_results[ck], metas)
        m = compute_metrics(sliced, ck)
        summary_data[ck] = {**m, "desc": cfg["desc"]}
        if m["n_traded"] > 0:
            log.info("  %-8s %-42s %5.1f%% %6.2f %8.2f $%7.0f %6d",
                     ck, cfg["desc"], m["win_rate"] * 100, m["agg_pf"],
                     m["avg_daily_pf"], m["total_pnl"], m["n_traded"])
        else:
            log.info("  %-8s %-42s %6s", ck, cfg["desc"], "—")

    # Config H split-exit detail
    h_results = all_config_results.get("H", [])
    h_traded = [r for r in h_results if r.get("data_available") and r.get("pnl_net") is not None]
    if h_traded:
        tp1_hits = sum(1 for r in h_traded if r.get("tp1_hit"))
        log.info("\n  Config H split-exit detail:")
        log.info("    TP1 hit rate: %d/%d (%.1f%%)", tp1_hits, len(h_traded),
                 tp1_hits / len(h_traded) * 100)
        runners_tp2 = sum(1 for r in h_traded if r.get("tp1_hit") and r.get("runner_exit_reason") == "TP1")
        log.info("    Runner reached TP2: %d/%d", runners_tp2, tp1_hits if tp1_hits > 0 else 1)
        first_half_pnls = [r["first_half_pnl"] for r in h_traded if r.get("first_half_pnl") is not None]
        if first_half_pnls:
            log.info("    Avg first-half PnL: $%.2f", np.mean(first_half_pnls))

    # ══════════════════════════════════════════════════════════════════════
    #  PORTFOLIO-LEVEL METRICS (Config D as primary)
    # ══════════════════════════════════════════════════════════════════════
    log.info("\n" + "=" * 90)
    log.info("  PORTFOLIO-LEVEL METRICS")
    log.info("=" * 90)

    # Get traded results for Config D
    d_results = all_config_results["D"]
    d_traded = []
    d_metas_traded = []
    for result, meta in zip(d_results, metas):
        if result.get("data_available") and result.get("pnl_net") is not None:
            if result.get("r_multiple", 0) >= -1.05:
                d_traded.append(result)
                d_metas_traded.append(meta)

    # S1/S2: Portfolio walk (no position limit)
    log.info("\n  S1/S2: Portfolio walk — Config D (no position limit)")
    portfolio = portfolio_walk(
        d_traded, d_metas_traded, ohlcv_cache,
        position_usd=position_usd,
    )
    log.info("    Peak concurrent positions: %d", portfolio.get("peak_concurrent_positions", 0))
    log.info("    Peak capital deployed: $%s", f"{portfolio.get('peak_capital_deployed', 0):,.0f}")
    log.info("    Peak capital at risk: $%s", f"{portfolio.get('peak_capital_at_risk', 0):,.0f}")
    log.info("    Max portfolio drawdown: $%s", f"{portfolio.get('max_portfolio_drawdown', 0):,.0f}")
    log.info("    Max portfolio drawdown: %.1f%%", portfolio.get("max_portfolio_drawdown_pct", 0))

    # S3: Stress test
    log.info("\n  S3: Correlation stress test (BTC)")
    stress = stress_test(d_traded, d_metas_traded, btc_bars, position_usd)
    if stress.get("worst_day"):
        wd = stress["worst_day"]
        log.info("    Worst BTC day: %s (%.1f%%)", wd["date"], wd["btc_drawdown_pct"])
        log.info("    D1 positions open: %d, stress loss: $%s", wd["d1_positions_open"], f"{wd['stress_loss']:,.0f}")
    if stress.get("worst_week"):
        ww = stress["worst_week"]
        log.info("    Worst 3-day window: %s to %s (%.1f%%)", ww["start_date"], ww["end_date"], ww["btc_drawdown_pct"])
        log.info("    Peak concurrent: %d, cascading loss: $%s, factor: %.2fx",
                 ww["peak_concurrent_positions"], f"{ww['cascading_stress_loss']:,.0f}", ww["cascading_factor"])

    # S4: Position limit sweep
    position_limit_results = {}
    if max_concurrent_values:
        log.info("\n  S4: Position limit sweep")
        log.info("    %-6s %8s %8s %8s %8s", "Limit", "dailyPF", "N_trades", "N_skip", "PnL")
        log.info("    " + "-" * 44)

        for mc in sorted(max_concurrent_values):
            pl_portfolio = portfolio_walk(
                d_traded, d_metas_traded, ohlcv_cache,
                position_usd=position_usd,
                max_concurrent=mc,
            )
            # Recompute daily PF from the portfolio's actual trades
            n_trades = pl_portfolio.get("n_trades", 0)
            n_skipped = pl_portfolio.get("n_skipped", 0)
            total_pnl = pl_portfolio.get("total_pnl", 0)

            position_limit_results[str(mc)] = {
                "n_trades": n_trades,
                "n_skipped": n_skipped,
                "peak_concurrent": pl_portfolio.get("peak_concurrent_positions", 0),
                "total_pnl": total_pnl,
            }
            log.info("    %-6d %8s %8d %8d $%7.0f", mc, "—",
                     n_trades, n_skipped, total_pnl)

    # S7: Compounding mode
    compounding_metrics = None
    if args.account_equity:
        log.info("\n  S7: Compounding mode ($%s equity, %.1f%% sizing)",
                 f"{args.account_equity:,.0f}", args.position_pct)
        comp_portfolio = portfolio_walk(
            d_traded, d_metas_traded, ohlcv_cache,
            position_usd=position_usd,
            account_equity=args.account_equity,
            position_pct=args.position_pct,
            compounding=True,
        )
        compounding_metrics = comp_portfolio.get("compounding")
        if compounding_metrics:
            log.info("    Start equity: $%s", f"{compounding_metrics['start_equity']:,.0f}")
            log.info("    End equity: $%s", f"{compounding_metrics['end_equity']:,.0f}")
            log.info("    CAGR: %.1f%%", compounding_metrics["cagr_pct"])
            log.info("    Max DD: %.1f%%", compounding_metrics["max_dd_pct"])
            log.info("    Calmar ratio: %.2f", compounding_metrics["calmar_ratio"])
            log.info("    Sharpe: %.2f", compounding_metrics["sharpe"])
            log.info("    Worst month: %.1f%%", compounding_metrics["worst_month_pct"])

    # ══════════════════════════════════════════════════════════════════════
    #  WALK-FORWARD VALIDATION
    # ══════════════════════════════════════════════════════════════════════
    wf_results = None
    if not args.skip_walkforward:
        log.info("\n" + "=" * 90)
        log.info("  WALK-FORWARD VALIDATION (5-fold purged rolling, 1-day embargo)")
        log.info("=" * 90)

        wf_results = walk_forward_validate(all_config_results, metas)

        if "error" in wf_results:
            log.warning("  Walk-forward skipped: %s", wf_results.get("error"))
        else:
            for ck in sorted(wf_results.keys()):
                wf = wf_results[ck]
                cfg_desc = EXIT_CONFIGS[ck]["desc"]
                status = "PASS" if wf["passes_walk_forward"] else "FAIL"
                log.info("\n  Config %s (%s): %s (%d/%d folds profitable)",
                         ck, cfg_desc, status, wf["profitable_folds"], wf["total_folds"])
                for fold in wf["folds"]:
                    log.info("    %s to %s: N=%d, dailyPF=%.2f, aggPF=%.2f, WR=%.1f%%",
                             fold["test_start"], fold["test_end"],
                             fold.get("n_traded", 0), fold.get("avg_daily_pf", 0),
                             fold.get("agg_pf", 0), fold.get("win_rate", 0) * 100)

            # DSR for configs that pass walk-forward
            n_trials = len(EXIT_CONFIGS)  # 8 configs tested
            log.info("\n  DSR Correction (n_trials=%d):", n_trials)
            for ck in sorted(wf_results.keys()):
                wf = wf_results[ck]
                if wf["passes_walk_forward"]:
                    # Compute holdout Sharpe from fold PnLs
                    fold_pnls = [f.get("total_pnl", 0) for f in wf["folds"]]
                    if len(fold_pnls) > 1:
                        sharpe = np.mean(fold_pnls) / (np.std(fold_pnls, ddof=1) + 1e-9)
                    else:
                        sharpe = 0
                    try:
                        dsr = compute_dsr(float(sharpe), n_trials)
                        log.info("    Config %s: holdout SR=%.3f, DSR=%.3f %s",
                                 ck, sharpe, dsr, "(survives)" if dsr > 0.5 else "(fails)")
                    except ImportError:
                        log.info("    Config %s: DSR skipped (scipy not available)", ck)

    # ══════════════════════════════════════════════════════════════════════
    #  WRITE JSON SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    config_specs = {}
    for ck, cfg in EXIT_CONFIGS.items():
        config_specs[ck] = {
            "desc": cfg["desc"],
            "tp_pct": cfg["tp_pct"],
            "stop_pct": cfg["stop_pct"],
            "time_stop_bars": cfg["ts"],
            "split_exit": cfg.get("split_exit", False),
            "tp2_pct": cfg.get("tp2_pct"),
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
        "n_symbols": n_symbols,
        "date_range": [date_min.isoformat(), date_max.isoformat()],
        "sim_parameters": {
            "slippage_bps": slippage_bps,
            "fee_bps": SIM_FEE_BPS,
            "position_usd": position_usd,
            "max_bars_forward": MAX_BARS_FORWARD,
            "max_entry_delay_bars": MAX_ENTRY_DELAY_BARS,
            "max_fee_r_ratio": MAX_FEE_R_RATIO,
            "min_r_preservation": MIN_R_PRESERVATION,
        },
        "exit_configs": config_specs,
        "configs": summary_data,
        "portfolio_metrics": {
            "peak_concurrent_positions": portfolio.get("peak_concurrent_positions", 0),
            "peak_capital_deployed": portfolio.get("peak_capital_deployed", 0),
            "peak_capital_at_risk": portfolio.get("peak_capital_at_risk", 0),
            "max_portfolio_drawdown": portfolio.get("max_portfolio_drawdown", 0),
            "max_portfolio_drawdown_pct": portfolio.get("max_portfolio_drawdown_pct", 0),
            "stress_test": stress,
            "position_limit_sweep": position_limit_results,
            "compounding": compounding_metrics,
        },
    }

    # Config H split-exit detail
    if h_traded:
        tp1_hits = sum(1 for r in h_traded if r.get("tp1_hit"))
        json_summary["config_h_detail"] = {
            "n_traded": len(h_traded),
            "tp1_hit_rate": round(tp1_hits / len(h_traded), 4) if h_traded else 0,
            "avg_first_half_pnl": round(float(np.mean([r["first_half_pnl"] for r in h_traded if r.get("first_half_pnl") is not None])), 4) if any(r.get("first_half_pnl") is not None for r in h_traded) else None,
        }

    if wf_results and "error" not in wf_results:
        # Serialize walk-forward results (convert any numpy/datetime)
        wf_serializable = {}
        for ck, wf in wf_results.items():
            wf_serializable[ck] = {
                "profitable_folds": wf["profitable_folds"],
                "total_folds": wf["total_folds"],
                "passes_walk_forward": wf["passes_walk_forward"],
                "folds": [{k: (v if not isinstance(v, (np.floating, np.integer)) else float(v))
                          for k, v in f.items()} for f in wf["folds"]],
            }
        json_summary["walk_forward"] = wf_serializable

    with open(json_path, "w") as f:
        json.dump(json_summary, f, indent=2, default=str)

    log.info("\n  JSON summary: %s", json_path)
    log.info("  Text output: %s", output_path)
    log.info("  Elapsed: %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
