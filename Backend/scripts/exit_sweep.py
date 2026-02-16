#!/usr/bin/env python3
"""
Exit Parameter Sweep
====================

Loads opportunities + OHLCV once, runs entry logic once to get valid trades,
then sweeps exit parameter combinations in-memory. ~120x faster than running
128 separate simulation API calls.

Outputs:
  <outdir>/exit_sweep_results.csv — All combo metrics (train + val + all)
  Console summary: top 10 combos by train PF with val PF alongside

Usage:
    # In Docker (production server), in tmux:
    docker compose -f docker-compose.yml run --rm -T worker \
      python3 /scripts/exit_sweep.py \
      --batch-id backfill_20260216_5a7a894c \
      --outdir /tmp/exit_sweep/

    # On host (dev):
    python Backend/scripts/exit_sweep.py \
      --batch-id backfill_20260216_5a7a894c \
      --outdir analysis/exit_sweep/

Requires DATABASE_URL env var (reads from .env if available).
"""

import os
import sys
import csv
import json
import time
import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from itertools import product
from typing import Optional

# Load .env if available
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

# Add worker/app to path for imports
# In Docker: script at /scripts/, worker code at /app/
# On host: script at Backend/scripts/, worker code at Backend/worker/app/
worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/exit_checker.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))

import psycopg
from exit_checker import ExitConfig, check_exit_simple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

POSITION_SIZE_USD = 1000.0
SLIPPAGE_ENTRY_BPS = 5.0
SLIPPAGE_EXIT_BPS = 5.0
FEE_TAKER_BPS = 4.0
TIME_STOP_BARS = 50
MAX_BARS_FORWARD = 100
MAX_FEE_R_RATIO = 0.3
MIN_R_PRESERVATION = 0.5
MIN_SHORT_PRICE = 1.00
MAX_STOP_PCT = 0.10

TRAIN_CUTOFF = datetime(2026, 2, 14, tzinfo=timezone.utc)  # train < this, val >= this

# Parameter grid
BREAKEVEN_R_VALUES = [0, 0.5, 0.75, 1.0]
ACTIVATION_R_VALUES = [0.75, 1.0, 1.25, 1.5]
DISTANCE_R_VALUES = [0.25, 0.5, 0.75, 1.0]
TP1_MODES = ["exit", "disabled"]

TF_DELTA = {
    "1m": timedelta(minutes=1), "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15), "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1), "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class PreparedTrade:
    """A trade that passed all entry gates, ready for exit sweep."""
    direction: str
    entry_price: float  # with slippage
    stop_price: float
    tp1_price: Optional[float]
    tp2_price: Optional[float]
    r_value: float
    time_stop_bars: int
    bars: list[dict]  # OHLCV bars starting from entry_bar+1
    created_ts: datetime
    symbol: str
    setup_id: str
    timeframe: str
    position_size: float
    # For PnL calculation
    exit_slippage_bps: float = SLIPPAGE_EXIT_BPS
    fee_taker_bps: float = FEE_TAKER_BPS


@dataclass
class ComboResult:
    """Metrics for a single parameter combination."""
    combo_id: int
    breakeven_r: float
    activation_r: float
    distance_r: float
    tp1_mode: str
    # Train metrics
    train_trades: int = 0
    train_wins: int = 0
    train_wr: float = 0.0
    train_pf: float = 0.0
    train_pnl: float = 0.0
    train_avg_r: float = 0.0
    # Val metrics
    val_trades: int = 0
    val_wins: int = 0
    val_wr: float = 0.0
    val_pf: float = 0.0
    val_pnl: float = 0.0
    val_avg_r: float = 0.0
    # All metrics
    all_trades: int = 0
    all_wins: int = 0
    all_wr: float = 0.0
    all_pf: float = 0.0
    all_pnl: float = 0.0
    all_avg_r: float = 0.0
    # Exit distributions (train)
    train_exits_ff: int = 0
    train_exits_sl: int = 0
    train_exits_ts: int = 0
    train_exits_trail: int = 0
    train_exits_tp1: int = 0
    train_exits_tp2: int = 0
    train_exits_time: int = 0


# ─── Dedup (copied from run_executor — same logic) ───────────────────────────

def _dedup_opportunities(opportunities: list[dict]) -> list[dict]:
    """
    Collapse pre-dedup-fix duplicates while preserving distinct events.

    Two opportunities are duplicates if they share (symbol, timeframe, setup_id,
    entry_price) and were created within 1 hour of each other.
    """
    CLUSTER_GAP_S = 3600

    last_kept: dict[tuple, tuple] = {}
    deduped: list[dict] = []
    skipped = 0

    for opp in opportunities:
        sym = opp.get("symbol", "")
        tf = opp.get("timeframe", "")
        sid = opp.get("setup_id", "")
        created = opp.get("created_ts")

        raw = opp.get("payload_json")
        if isinstance(raw, str):
            try:
                p = json.loads(raw)
            except Exception:
                p = {}
        else:
            p = raw or {}
        ep = p.get("entry_price")
        ep_rounded = round(ep, 8) if ep is not None else None

        grp = (sym, tf, sid)
        if grp in last_kept:
            prev_ts, prev_ep = last_kept[grp]
            gap = (created - prev_ts).total_seconds() if created and prev_ts else float("inf")
            if gap < CLUSTER_GAP_S and ep_rounded == prev_ep:
                last_kept[grp] = (created, ep_rounded)
                skipped += 1
                continue

        last_kept[grp] = (created, ep_rounded)
        deduped.append(opp)

    if skipped:
        logger.info("Dedup: dropped %d same-signal duplicates, kept %d",
                     skipped, len(deduped))
    return deduped


# ─── Data loading ─────────────────────────────────────────────────────────────

def load_opportunities(database_url: str, batch_id: str, timeframes: list[str]) -> list[dict]:
    """Load expired opportunities from DB."""
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, created_ts, symbol, timeframe, setup_id,
                       decision, score, reason, payload_json
                FROM opportunities
                WHERE status = 'expired'
                  AND batch_id = %s
                  AND timeframe = ANY(%s)
                ORDER BY created_ts ASC;
            """, (batch_id, timeframes))

            rows = cur.fetchall()
            opportunities = []
            for row in rows:
                opportunities.append({
                    "id": row[0],
                    "created_ts": row[1],
                    "symbol": row[2],
                    "timeframe": row[3],
                    "setup_id": row[4],
                    "decision": row[5],
                    "score": row[6],
                    "reason": row[7],
                    "payload_json": row[8],
                })

    logger.info("Loaded %d raw opportunities", len(opportunities))
    opportunities = _dedup_opportunities(opportunities)
    logger.info("After dedup: %d opportunities", len(opportunities))
    return opportunities


def compute_ohlcv_ranges(
    opportunities: list[dict],
) -> dict[str, tuple[datetime, datetime]]:
    """Compute the OHLCV date range needed for each (symbol, timeframe) pair."""
    ranges: dict[str, tuple[datetime, datetime]] = {}

    for opp in opportunities:
        symbol = opp.get("symbol", "")
        timeframe = opp.get("timeframe", "1h")
        key = f"{symbol}_{timeframe}"

        opp_ts = opp.get("created_ts")
        if isinstance(opp_ts, str):
            opp_ts = datetime.fromisoformat(opp_ts.replace("Z", "+00:00"))
        if opp_ts is None:
            continue
        if opp_ts.tzinfo is None:
            opp_ts = opp_ts.replace(tzinfo=timezone.utc)

        bar_delta = TF_DELTA.get(timeframe, timedelta(hours=1))
        end_ts = opp_ts + (bar_delta * (MAX_BARS_FORWARD + 10))

        if key not in ranges:
            ranges[key] = (opp_ts, end_ts)
        else:
            existing_start, existing_end = ranges[key]
            ranges[key] = (
                min(existing_start, opp_ts),
                max(existing_end, end_ts),
            )

    return ranges


def batch_load_ohlcv(
    database_url: str,
    symbol_tf_ranges: dict[str, tuple[datetime, datetime]],
) -> dict[str, list[dict]]:
    """Batch load OHLCV data for all (symbol, timeframe) pairs."""
    cache: dict[str, list[dict]] = {}

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            total = len(symbol_tf_ranges)
            for idx, (key, (start_ts, end_ts)) in enumerate(symbol_tf_ranges.items()):
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

                rows = cur.fetchall()
                bars = [
                    {
                        "bar_start_ts": row[0],
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    }
                    for row in rows
                ]
                cache[key] = bars

                if (idx + 1) % 100 == 0 or idx + 1 == total:
                    logger.info("OHLCV loaded: %d/%d symbol-timeframes", idx + 1, total)

    total_bars = sum(len(v) for v in cache.values())
    logger.info("OHLCV cache: %d symbol-timeframes, %d total bars", len(cache), total_bars)
    return cache


# ─── Entry logic (replicated from opportunity_simulator._simulate_single) ─────

def prepare_trade(opp: dict, ohlcv_cache: dict[str, list[dict]]) -> Optional[PreparedTrade]:
    """
    Run entry logic for a single opportunity. Returns PreparedTrade if valid,
    None if filtered out by any gate.

    Replicates opportunity_simulator._simulate_single() entry logic lines 265-530.
    """
    symbol = opp.get("symbol", "")
    timeframe = opp.get("timeframe", "1h")
    setup_id = opp.get("setup_id", "")

    # Parse payload
    payload = opp.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if payload is None:
        payload = {}

    direction = payload.get("direction", opp.get("decision", ""))

    # Parse opportunity timestamp
    opp_ts = opp.get("created_ts")
    if isinstance(opp_ts, str):
        opp_ts = datetime.fromisoformat(opp_ts.replace("Z", "+00:00"))
    if opp_ts is None:
        return None
    if opp_ts.tzinfo is None:
        opp_ts = opp_ts.replace(tzinfo=timezone.utc)

    # Get OHLCV bars
    key = f"{symbol}_{timeframe}"
    bars = ohlcv_cache.get(key, [])
    if not bars:
        return None

    # Get price levels from payload
    entry_price = payload.get("entry_price")
    stop_price = payload.get("stop_price")
    tp1_price = payload.get("tp1_price")
    tp2_price = payload.get("tp2_price")
    r_value = payload.get("r_value")

    if entry_price is None or stop_price is None:
        return None

    # Stop distance factor is always 1.0 — no stop rescaling in sweep

    # Microcap short gate
    if direction == "short" and entry_price < MIN_SHORT_PRICE:
        return None

    # Stop-width gate
    if entry_price > 0:
        stop_pct = abs(stop_price - entry_price) / entry_price
        if stop_pct > MAX_STOP_PCT:
            return None

    # Calculate r_value if not present
    if r_value is None:
        if direction == "long":
            r_value = entry_price - stop_price
        else:
            r_value = stop_price - entry_price
        if r_value <= 0:
            return None

    # Find the first bar after opportunity timestamp (NEXT_OPEN fill)
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

    # Staleness gate: reject if first bar is too far from signal
    bar_duration = TF_DELTA.get(timeframe, timedelta(hours=1))
    max_entry_delay = bar_duration * 3
    entry_bar = bars[entry_bar_idx]
    entry_bar_ts = entry_bar["bar_start_ts"]
    if entry_bar_ts.tzinfo is None:
        entry_bar_ts = entry_bar_ts.replace(tzinfo=timezone.utc)
    entry_delay = entry_bar_ts - opp_ts
    if entry_delay > max_entry_delay:
        return None

    # Entry at next bar's open
    actual_entry_price = entry_bar["open"]

    # Apply entry slippage
    if direction == "long":
        entry_with_slip = actual_entry_price * (1 + SLIPPAGE_ENTRY_BPS / 10000)
    else:
        entry_with_slip = actual_entry_price * (1 - SLIPPAGE_ENTRY_BPS / 10000)

    # Recalculate r_value with actual fill price
    if direction == "long":
        actual_r_value = entry_with_slip - stop_price
    else:
        actual_r_value = stop_price - entry_with_slip

    if actual_r_value <= 0:
        return None

    # Fee/R gate
    fee_per_unit = 2 * entry_with_slip * FEE_TAKER_BPS / 10000
    if fee_per_unit > MAX_FEE_R_RATIO * actual_r_value:
        return None

    # Adverse gap gate
    if r_value > 0 and actual_r_value < MIN_R_PRESERVATION * r_value:
        return None

    # TP invalidation: wrong side of actual entry
    if direction == "long":
        if tp1_price is not None and tp1_price <= entry_with_slip:
            tp1_price = None
        if tp2_price is not None and tp2_price <= entry_with_slip:
            tp2_price = None
    else:
        if tp1_price is not None and tp1_price >= entry_with_slip:
            tp1_price = None
        if tp2_price is not None and tp2_price >= entry_with_slip:
            tp2_price = None

    # TP invalidation: too close to cover costs
    min_tp_distance = entry_with_slip * (
        SLIPPAGE_EXIT_BPS + 2 * FEE_TAKER_BPS
    ) / 10000
    if direction == "long":
        if tp1_price is not None and (tp1_price - entry_with_slip) < min_tp_distance:
            tp1_price = None
        if tp2_price is not None and (tp2_price - entry_with_slip) < min_tp_distance:
            tp2_price = None
    else:
        if tp1_price is not None and (entry_with_slip - tp1_price) < min_tp_distance:
            tp1_price = None
        if tp2_price is not None and (entry_with_slip - tp2_price) < min_tp_distance:
            tp2_price = None

    # Position sizing
    position_size = POSITION_SIZE_USD / entry_with_slip

    # Time stop from setup or default
    time_stop_bars = payload.get("time_stop_bars", TIME_STOP_BARS)

    # Collect bars for exit walk (entry_bar+1 onwards, up to max_bars_forward)
    max_bar_idx = min(entry_bar_idx + MAX_BARS_FORWARD, len(bars) - 1)
    exit_bars = bars[entry_bar_idx + 1: max_bar_idx + 1]

    return PreparedTrade(
        direction=direction,
        entry_price=entry_with_slip,
        stop_price=stop_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        r_value=actual_r_value,
        time_stop_bars=time_stop_bars,
        bars=exit_bars,
        created_ts=opp_ts,
        symbol=symbol,
        setup_id=setup_id,
        timeframe=timeframe,
        position_size=position_size,
    )


# ─── Exit sweep logic ────────────────────────────────────────────────────────

def run_exit_for_trade(
    trade: PreparedTrade,
    config: ExitConfig,
    tp1_mode: str,
) -> tuple[str, float, int]:
    """
    Walk bars and apply exit logic with given config.

    Returns (exit_reason, exit_price, bars_held).
    """
    tp1 = trade.tp1_price if tp1_mode == "exit" else None
    tp2 = trade.tp2_price

    hwm = None
    half_r_touched = False
    bars_held = 0

    for bar in trade.bars:
        bars_held += 1

        exit_result, new_hwm, new_half_r = check_exit_simple(
            side=trade.direction,
            bar=bar,
            entry_price=trade.entry_price,
            stop_price=trade.stop_price,
            tp1_price=tp1,
            tp2_price=tp2,
            r_value=trade.r_value,
            bars_held=bars_held,
            max_bars=trade.time_stop_bars,
            high_water_mark=hwm,
            half_r_touched=half_r_touched,
            config=config,
        )

        hwm = new_hwm
        half_r_touched = new_half_r

        if exit_result:
            reason, price = exit_result
            return reason, price, bars_held

    # No exit triggered — force close at last bar's close
    if trade.bars:
        return "TIME_STOP", trade.bars[-1]["close"], bars_held
    else:
        return "TIME_STOP", trade.entry_price, 0


def compute_pnl(
    trade: PreparedTrade,
    exit_reason: str,
    exit_price: float,
) -> tuple[float, float]:
    """
    Compute net PnL and R-multiple for a trade.

    Returns (pnl_net, r_multiple).
    """
    # Apply exit slippage (NOT for stop losses — fill already modeled)
    if exit_reason != "STOP_LOSS":
        if trade.direction == "long":
            exit_with_slip = exit_price * (1 - trade.exit_slippage_bps / 10000)
        else:
            exit_with_slip = exit_price * (1 + trade.exit_slippage_bps / 10000)
    else:
        exit_with_slip = exit_price

    # PnL
    if trade.direction == "long":
        pnl_gross = (exit_with_slip - trade.entry_price) * trade.position_size
    else:
        pnl_gross = (trade.entry_price - exit_with_slip) * trade.position_size

    # Fees (entry + exit)
    fee_bps = trade.fee_taker_bps
    entry_fee = trade.entry_price * trade.position_size * (fee_bps / 10000)
    exit_fee = exit_with_slip * trade.position_size * (fee_bps / 10000)
    total_fees = entry_fee + exit_fee

    pnl_net = pnl_gross - total_fees

    # R-multiple
    risk_per_unit = trade.r_value * trade.position_size
    r_multiple = pnl_net / risk_per_unit if risk_per_unit > 0 else 0

    return pnl_net, r_multiple


def compute_metrics(pnls: list[float], r_multiples: list[float]) -> tuple[float, float, float, float]:
    """Compute WR, PF, total PnL, avg R from trade results."""
    if not pnls:
        return 0.0, 0.0, 0.0, 0.0

    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls)

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    if pf == float('inf'):
        pf = 99.99  # cap for CSV/display

    total_pnl = sum(pnls)
    avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

    return wr, pf, total_pnl, avg_r


def generate_combos() -> list[tuple[int, float, float, float, str]]:
    """
    Generate valid parameter combinations.

    Constraint: trail_breakeven_r must be < trail_activation_r (or 0 = disabled).
    """
    combos = []
    combo_id = 0

    for be_r, act_r, dist_r, tp1_mode in product(
        BREAKEVEN_R_VALUES, ACTIVATION_R_VALUES, DISTANCE_R_VALUES, TP1_MODES
    ):
        # Constraint: breakeven must be below activation (or disabled)
        if be_r > 0 and be_r >= act_r:
            continue

        combo_id += 1
        combos.append((combo_id, be_r, act_r, dist_r, tp1_mode))

    return combos


def run_sweep(
    train_trades: list[PreparedTrade],
    val_trades: list[PreparedTrade],
    combos: list[tuple[int, float, float, float, str]],
    csv_path: str,
) -> list[ComboResult]:
    """Run all parameter combos against prepared trades."""

    results: list[ComboResult] = []
    total_combos = len(combos)

    # Open CSV for incremental writes
    fieldnames = [
        "combo_id", "breakeven_r", "activation_r", "distance_r", "tp1_mode",
        "train_trades", "train_wr", "train_pf", "train_pnl", "train_avg_r",
        "val_trades", "val_wr", "val_pf", "val_pnl", "val_avg_r",
        "all_trades", "all_wr", "all_pf", "all_pnl", "all_avg_r",
        "train_exits_ff", "train_exits_sl", "train_exits_ts",
        "train_exits_trail", "train_exits_tp1", "train_exits_tp2", "train_exits_time",
    ]

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for idx, (combo_id, be_r, act_r, dist_r, tp1_mode) in enumerate(combos):
            config = ExitConfig(
                trail_breakeven_r=be_r,
                trail_activation_r=act_r,
                trail_distance_r=dist_r,
            )

            cr = ComboResult(
                combo_id=combo_id,
                breakeven_r=be_r,
                activation_r=act_r,
                distance_r=dist_r,
                tp1_mode=tp1_mode,
            )

            # --- Train split ---
            train_pnls, train_rs, train_exits = [], [], {}
            for trade in train_trades:
                reason, price, bars_held = run_exit_for_trade(trade, config, tp1_mode)
                pnl, r_mult = compute_pnl(trade, reason, price)
                train_pnls.append(pnl)
                train_rs.append(r_mult)
                train_exits[reason] = train_exits.get(reason, 0) + 1

            cr.train_trades = len(train_pnls)
            cr.train_wins = sum(1 for p in train_pnls if p > 0)
            cr.train_wr, cr.train_pf, cr.train_pnl, cr.train_avg_r = compute_metrics(train_pnls, train_rs)

            cr.train_exits_ff = train_exits.get("FAIL_FAST", 0)
            cr.train_exits_sl = train_exits.get("STOP_LOSS", 0)
            cr.train_exits_ts = train_exits.get("TIME_STOP", 0)
            cr.train_exits_trail = train_exits.get("TRAIL_STOP", 0)
            cr.train_exits_tp1 = train_exits.get("TP1", 0)
            cr.train_exits_tp2 = train_exits.get("TP2", 0)
            cr.train_exits_time = train_exits.get("TIME_STOP", 0)

            # --- Val split ---
            val_pnls, val_rs = [], []
            for trade in val_trades:
                reason, price, bars_held = run_exit_for_trade(trade, config, tp1_mode)
                pnl, r_mult = compute_pnl(trade, reason, price)
                val_pnls.append(pnl)
                val_rs.append(r_mult)

            cr.val_trades = len(val_pnls)
            cr.val_wins = sum(1 for p in val_pnls if p > 0)
            cr.val_wr, cr.val_pf, cr.val_pnl, cr.val_avg_r = compute_metrics(val_pnls, val_rs)

            # --- All ---
            all_pnls = train_pnls + val_pnls
            all_rs = train_rs + val_rs
            cr.all_trades = len(all_pnls)
            cr.all_wins = sum(1 for p in all_pnls if p > 0)
            cr.all_wr, cr.all_pf, cr.all_pnl, cr.all_avg_r = compute_metrics(all_pnls, all_rs)

            results.append(cr)

            # Write CSV row incrementally
            writer.writerow({
                "combo_id": cr.combo_id,
                "breakeven_r": cr.breakeven_r,
                "activation_r": cr.activation_r,
                "distance_r": cr.distance_r,
                "tp1_mode": cr.tp1_mode,
                "train_trades": cr.train_trades,
                "train_wr": round(cr.train_wr, 4),
                "train_pf": round(cr.train_pf, 3),
                "train_pnl": round(cr.train_pnl, 2),
                "train_avg_r": round(cr.train_avg_r, 4),
                "val_trades": cr.val_trades,
                "val_wr": round(cr.val_wr, 4),
                "val_pf": round(cr.val_pf, 3),
                "val_pnl": round(cr.val_pnl, 2),
                "val_avg_r": round(cr.val_avg_r, 4),
                "all_trades": cr.all_trades,
                "all_wr": round(cr.all_wr, 4),
                "all_pf": round(cr.all_pf, 3),
                "all_pnl": round(cr.all_pnl, 2),
                "all_avg_r": round(cr.all_avg_r, 4),
                "train_exits_ff": cr.train_exits_ff,
                "train_exits_sl": cr.train_exits_sl,
                "train_exits_ts": cr.train_exits_ts,
                "train_exits_trail": cr.train_exits_trail,
                "train_exits_tp1": cr.train_exits_tp1,
                "train_exits_tp2": cr.train_exits_tp2,
                "train_exits_time": cr.train_exits_time,
            })
            f.flush()

            # Progress logging
            if (idx + 1) % 10 == 0 or idx + 1 == total_combos:
                logger.info(
                    "Combo %d/%d: BE=%.2f ACT=%.2f DIST=%.2f TP1=%s → "
                    "train PF=%.3f WR=%.1f%% | val PF=%.3f WR=%.1f%%",
                    idx + 1, total_combos,
                    be_r, act_r, dist_r, tp1_mode,
                    cr.train_pf, cr.train_wr * 100,
                    cr.val_pf, cr.val_wr * 100,
                )

    return results


# ─── Output ───────────────────────────────────────────────────────────────────

def print_summary(results: list[ComboResult]):
    """Print top 10 combos and reference combos."""

    # Sort by train PF descending
    sorted_results = sorted(results, key=lambda r: r.train_pf, reverse=True)

    print("\n" + "=" * 100)
    print("EXIT PARAMETER SWEEP RESULTS")
    print("=" * 100)

    # Top 10
    print(f"\n{'TOP 10 BY TRAINING PF':^100}")
    print("-" * 100)
    print(f"{'#':>3} {'BE_R':>5} {'ACT_R':>6} {'DIST_R':>6} {'TP1':>8} "
          f"{'TrnN':>5} {'TrnWR':>6} {'TrnPF':>6} {'TrnPnL':>9} "
          f"{'ValN':>5} {'ValWR':>6} {'ValPF':>6} {'ValPnL':>9} "
          f"{'Overfit':>8}")
    print("-" * 100)

    for i, cr in enumerate(sorted_results[:10]):
        overfit = ""
        if cr.val_pf > 0 and cr.train_pf > 0:
            pf_drop = (cr.train_pf - cr.val_pf) / cr.train_pf
            if pf_drop > 0.2:
                overfit = f"!{pf_drop:.0%}"
            else:
                overfit = f" {pf_drop:.0%}"

        print(f"{i+1:>3} {cr.breakeven_r:>5.2f} {cr.activation_r:>6.2f} {cr.distance_r:>6.2f} "
              f"{cr.tp1_mode:>8} "
              f"{cr.train_trades:>5} {cr.train_wr*100:>5.1f}% {cr.train_pf:>6.3f} "
              f"{cr.train_pnl:>+9.2f} "
              f"{cr.val_trades:>5} {cr.val_wr*100:>5.1f}% {cr.val_pf:>6.3f} "
              f"{cr.val_pnl:>+9.2f} "
              f"{overfit:>8}")

    # Reference combos
    print(f"\n{'REFERENCE COMBOS':^100}")
    print("-" * 100)

    # Find baseline (BE=0, ACT=1.0, DIST=1.0, TP1=exit) — pre-Phase-5 original
    # and Phase 5b (BE=0.75, ACT=1.0, DIST=0.5, TP1=exit)
    refs = [
        ("Original baseline", 0, 1.0, 1.0, "exit"),
        ("Phase 5b current", 0.75, 1.0, 0.5, "exit"),
    ]

    for label, be_r, act_r, dist_r, tp1 in refs:
        match = [r for r in results
                 if r.breakeven_r == be_r and r.activation_r == act_r
                 and r.distance_r == dist_r and r.tp1_mode == tp1]
        if match:
            cr = match[0]
            print(f"  {label:20s}: BE={cr.breakeven_r:.2f} ACT={cr.activation_r:.2f} "
                  f"DIST={cr.distance_r:.2f} TP1={cr.tp1_mode}")
            print(f"    Train: {cr.train_trades} trades, WR={cr.train_wr*100:.1f}%, "
                  f"PF={cr.train_pf:.3f}, PnL={cr.train_pnl:+.2f}, AvgR={cr.train_avg_r:+.4f}")
            print(f"    Val:   {cr.val_trades} trades, WR={cr.val_wr*100:.1f}%, "
                  f"PF={cr.val_pf:.3f}, PnL={cr.val_pnl:+.2f}, AvgR={cr.val_avg_r:+.4f}")
            print(f"    All:   {cr.all_trades} trades, WR={cr.all_wr*100:.1f}%, "
                  f"PF={cr.all_pf:.3f}, PnL={cr.all_pnl:+.2f}, AvgR={cr.all_avg_r:+.4f}")
            print(f"    Exits: FF={cr.train_exits_ff} SL={cr.train_exits_sl} "
                  f"TS={cr.train_exits_ts} TRAIL={cr.train_exits_trail} "
                  f"TP1={cr.train_exits_tp1} TP2={cr.train_exits_tp2}")
        else:
            print(f"  {label:20s}: NOT FOUND in sweep grid")

    print("\n" + "=" * 100)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Exit parameter sweep")
    parser.add_argument("--batch-id", required=True, help="Opportunity batch_id to sweep")
    parser.add_argument("--outdir", default=".", help="Output directory for CSV")
    parser.add_argument(
        "--timeframes", default="15m,1h,4h,1d",
        help="Comma-separated timeframes (default: 15m,1h,4h,1d)",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    timeframes = [t.strip() for t in args.timeframes.split(",")]
    os.makedirs(args.outdir, exist_ok=True)
    csv_path = os.path.join(args.outdir, "exit_sweep_results.csv")

    t0 = time.time()

    # ── Step 1: Load opportunities ────────────────────────────────────────
    logger.info("Loading opportunities (batch=%s, timeframes=%s)", args.batch_id, timeframes)
    opportunities = load_opportunities(database_url, args.batch_id, timeframes)
    if not opportunities:
        logger.error("No opportunities found for batch_id=%s", args.batch_id)
        sys.exit(1)
    logger.info("Loaded %d opportunities in %.1fs", len(opportunities), time.time() - t0)

    # ── Step 2: Load OHLCV ────────────────────────────────────────────────
    t1 = time.time()
    logger.info("Computing OHLCV ranges...")
    ranges = compute_ohlcv_ranges(opportunities)
    logger.info("Loading OHLCV for %d symbol-timeframe pairs...", len(ranges))
    ohlcv_cache = batch_load_ohlcv(database_url, ranges)
    logger.info("OHLCV loaded in %.1fs", time.time() - t1)

    # ── Step 3: Prepare trades (run entry logic once) ─────────────────────
    t2 = time.time()
    logger.info("Running entry logic for %d opportunities...", len(opportunities))
    all_trades: list[PreparedTrade] = []
    filtered = 0

    for opp in opportunities:
        trade = prepare_trade(opp, ohlcv_cache)
        if trade is not None:
            all_trades.append(trade)
        else:
            filtered += 1

    logger.info(
        "Entry logic: %d valid trades, %d filtered (%.1f%%) in %.1fs",
        len(all_trades), filtered,
        filtered / len(opportunities) * 100 if opportunities else 0,
        time.time() - t2,
    )

    if not all_trades:
        logger.error("No valid trades after entry logic — nothing to sweep")
        sys.exit(1)

    # ── Step 4: Split into train/val ──────────────────────────────────────
    train_trades = [t for t in all_trades if t.created_ts < TRAIN_CUTOFF]
    val_trades = [t for t in all_trades if t.created_ts >= TRAIN_CUTOFF]
    logger.info(
        "Train/val split at %s: %d train, %d val",
        TRAIN_CUTOFF.strftime("%Y-%m-%d"), len(train_trades), len(val_trades),
    )

    # ── Step 5: Generate parameter combos ─────────────────────────────────
    combos = generate_combos()
    logger.info("Generated %d valid parameter combinations", len(combos))

    # ── Step 6: Run sweep ─────────────────────────────────────────────────
    t3 = time.time()
    logger.info("Starting sweep (CSV → %s)...", csv_path)
    results = run_sweep(train_trades, val_trades, combos, csv_path)
    logger.info("Sweep completed in %.1fs", time.time() - t3)

    # ── Step 7: Summary ───────────────────────────────────────────────────
    print_summary(results)

    total_time = time.time() - t0
    logger.info(
        "Done. Total time: %.1fs. CSV: %s (%d rows)",
        total_time, csv_path, len(results),
    )


if __name__ == "__main__":
    main()
