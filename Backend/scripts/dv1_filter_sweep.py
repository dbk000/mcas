#!/usr/bin/env python3
"""
DV1 Divergence Filter Sweep
============================

DV1 (RSI Divergence Reversal) showed val PF 1.273 with 294 trades but train PF
0.412 with 40% FAIL_FAST rate. This script tests tightening gates to eliminate
noisy train signals while preserving the val edge.

Tests filters additively (like hypothesis_filter_test) to find which gates
matter most, then sweeps the best combos across exit configs.

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
      python3 /scripts/dv1_filter_sweep.py --days 30
"""

import os
import sys
import time
import argparse
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

import numpy as np

# ─── Env + path setup ────────────────────────────────────────────────────────

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

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
MAX_BARS_FORWARD = 100
MIN_SHORT_PRICE = 1.00
MAX_STOP_PCT = 0.10
MIN_BARS_NEEDED = 250

TRAIN_CUTOFF = datetime(2026, 2, 14, tzinfo=timezone.utc)


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class DV1Signal:
    """A DV1 divergence signal with metadata for filtering."""
    symbol: str
    direction: str
    bar_idx: int
    bar_ts: datetime
    entry_price: float
    stop_price: float
    r_value: float
    # Divergence metadata (for post-hoc filtering)
    rsi_at_signal: float
    rsi_diff: float           # RSI improvement (how much RSI diverges from price)
    price_displacement_atr: float  # how far price moved between swings
    clv: float
    volume_declining: bool
    volume_z: float
    near_bb_lower: bool       # within 1.5 ATR of lower BB (for longs)
    near_bb_upper: bool       # within 1.5 ATR of upper BB (for shorts)
    ema_slope_against: bool   # EMA20 slope is against the trade direction
    swing_bars_apart: int     # bars between the two swing points


@dataclass
class SimTrade:
    """Trade ready for exit simulation."""
    direction: str
    entry_price: float
    stop_price: float
    r_value: float
    time_stop_bars: int
    bars: list[dict]
    created_ts: datetime
    symbol: str
    position_size: float


# ─── Indicator computation ────────────────────────────────────────────────────

def compute_ema(data: np.ndarray, n: int) -> np.ndarray:
    if len(data) < n:
        return np.full(len(data), np.mean(data))
    alpha = 2.0 / (n + 1)
    result = np.zeros(len(data))
    result[n - 1] = np.mean(data[:n])
    for i in range(n, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    result[:n - 1] = result[n - 1]
    return result


def compute_sma(data: np.ndarray, n: int) -> np.ndarray:
    if len(data) < n:
        return np.full(len(data), np.mean(data))
    result = np.convolve(data, np.ones(n) / n, mode='full')[:len(data)]
    result[:n - 1] = result[n - 1]
    return result


def compute_atr_series(highs, lows, closes, n=14):
    tr = np.zeros(len(closes))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.zeros(len(closes))
    atr[:n] = np.mean(tr[:n])
    for i in range(n, len(closes)):
        atr[i] = np.mean(tr[i - n + 1:i + 1])
    return atr


def compute_rsi_series(closes, n=14):
    if len(closes) < n + 1:
        return np.full(len(closes), 50.0)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.zeros(len(deltas))
    avg_loss = np.zeros(len(deltas))
    avg_gain[n - 1] = np.mean(gains[:n])
    avg_loss[n - 1] = np.mean(losses[:n])
    for i in range(n, len(deltas)):
        avg_gain[i] = (avg_gain[i - 1] * (n - 1) + gains[i]) / n
        avg_loss[i] = (avg_loss[i - 1] * (n - 1) + losses[i]) / n
    rsi = np.full(len(closes), 50.0)
    for i in range(n, len(closes)):
        if avg_loss[i - 1] == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain[i - 1] / avg_loss[i - 1]
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_bbands(closes, n=20, k=2.0):
    mid = compute_sma(closes, n)
    std = np.zeros(len(closes))
    for i in range(n - 1, len(closes)):
        std[i] = np.std(closes[i - n + 1:i + 1])
    std[:n - 1] = std[n - 1]
    return mid, mid + k * std, mid - k * std


def compute_volume_z(volumes, n=20):
    result = np.zeros(len(volumes))
    for i in range(n, len(volumes)):
        window = volumes[i - n + 1:i + 1]
        mean = np.mean(window)
        std = np.std(window)
        result[i] = (volumes[i] - mean) / std if std > 0 else 0.0
    return result


def compute_clv(highs, lows, closes):
    ranges = highs - lows
    ranges = np.where(ranges == 0, 1.0, ranges)
    return (closes - lows) / ranges


# ─── DV1 signal generation with full metadata ────────────────────────────────

def generate_dv1_signals(bars: list[dict], symbol: str) -> list[DV1Signal]:
    """
    Generate all possible DV1 divergence signals with full metadata
    for post-hoc filtering. Uses loose gates to capture everything.
    """
    closes = np.array([b["close"] for b in bars])
    highs = np.array([b["high"] for b in bars])
    lows = np.array([b["low"] for b in bars])
    volumes = np.array([b["volume"] for b in bars])

    atr_s = compute_atr_series(highs, lows, closes, 14)
    rsi_s = compute_rsi_series(closes, 14)
    ema20 = compute_ema(closes, 20)
    bb_mid, bb_upper, bb_lower = compute_bbands(closes, 20, 2.0)
    vol_z = compute_volume_z(volumes, 20)
    clv_s = compute_clv(highs, lows, closes)

    # EMA20 slope (5-bar delta, ATR-normalized)
    ema20_slope = np.zeros(len(closes))
    for i in range(5, len(closes)):
        if atr_s[i] > 0:
            ema20_slope[i] = (ema20[i] - ema20[i - 5]) / (5 * atr_s[i])

    signals = []

    for i in range(MIN_BARS_NEEDED, len(bars) - 1):
        bar = bars[i]
        atr = atr_s[i]
        if atr <= 0 or i < 25:
            continue

        lookback_start = max(0, i - 25)
        lookback_end = max(0, i - 5)
        if lookback_end <= lookback_start:
            continue

        # ── Bullish divergence ──────────────────────────────────────────
        prior_low_idx = lookback_start + int(np.argmin(lows[lookback_start:lookback_end]))
        current_is_low = lows[i] <= float(np.min(lows[max(0, i - 3):i + 1]))

        if current_is_low and lows[i] < lows[prior_low_idx] and rsi_s[i] > rsi_s[prior_low_idx]:
            # Basic divergence exists — capture with all metadata
            rsi_diff = rsi_s[i] - rsi_s[prior_low_idx]
            price_disp = abs(lows[i] - lows[prior_low_idx]) / atr
            vol_declining = volumes[i] < volumes[prior_low_idx]
            near_bb = closes[i] < bb_lower[i] + 1.5 * atr
            slope_against = ema20_slope[i] < 0  # downtrend = against long
            swing_gap = i - prior_low_idx

            # Use loose RSI gate — we'll tighten in sweep
            if rsi_s[i] < 45:
                stop = lows[i] - 0.75 * atr
                entry = bars[i + 1]["open"]
                if stop > 0 and entry > 0:
                    r_val = entry - stop
                    stop_pct = r_val / entry
                    if r_val > 0 and stop_pct <= MAX_STOP_PCT:
                        bar_ts = bar["bar_start_ts"]
                        if bar_ts.tzinfo is None:
                            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
                        signals.append(DV1Signal(
                            symbol=symbol, direction="long", bar_idx=i,
                            bar_ts=bar_ts, entry_price=entry, stop_price=stop,
                            r_value=r_val, rsi_at_signal=rsi_s[i],
                            rsi_diff=rsi_diff, price_displacement_atr=price_disp,
                            clv=clv_s[i], volume_declining=vol_declining,
                            volume_z=vol_z[i], near_bb_lower=near_bb,
                            near_bb_upper=False, ema_slope_against=slope_against,
                            swing_bars_apart=swing_gap,
                        ))

        # ── Bearish divergence ──────────────────────────────────────────
        prior_high_idx = lookback_start + int(np.argmax(highs[lookback_start:lookback_end]))
        current_is_high = highs[i] >= float(np.max(highs[max(0, i - 3):i + 1]))

        if current_is_high and highs[i] > highs[prior_high_idx] and rsi_s[i] < rsi_s[prior_high_idx]:
            rsi_diff = rsi_s[prior_high_idx] - rsi_s[i]
            price_disp = abs(highs[i] - highs[prior_high_idx]) / atr
            vol_declining = volumes[i] < volumes[prior_high_idx]
            near_bb = closes[i] > bb_upper[i] - 1.5 * atr
            slope_against = ema20_slope[i] > 0  # uptrend = against short
            swing_gap = i - prior_high_idx

            if rsi_s[i] > 55:
                stop = highs[i] + 0.75 * atr
                entry = bars[i + 1]["open"]
                if entry >= MIN_SHORT_PRICE:
                    r_val = stop - entry
                    stop_pct = r_val / entry
                    if r_val > 0 and stop_pct <= MAX_STOP_PCT:
                        bar_ts = bar["bar_start_ts"]
                        if bar_ts.tzinfo is None:
                            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
                        signals.append(DV1Signal(
                            symbol=symbol, direction="short", bar_idx=i,
                            bar_ts=bar_ts, entry_price=entry, stop_price=stop,
                            r_value=r_val, rsi_at_signal=rsi_s[i],
                            rsi_diff=rsi_diff, price_displacement_atr=price_disp,
                            clv=clv_s[i], volume_declining=vol_declining,
                            volume_z=vol_z[i], near_bb_lower=False,
                            near_bb_upper=near_bb, ema_slope_against=slope_against,
                            swing_bars_apart=swing_gap,
                        ))

    return signals


# ─── Filter definitions ──────────────────────────────────────────────────────

@dataclass
class FilterConfig:
    """A named combination of DV1 filters."""
    name: str
    min_rsi_diff: float = 0         # A: minimum RSI divergence points
    rsi_threshold: float = 45       # B: RSI must be more extreme (long < this, short > 100-this)
    clv_threshold: float = 0.45     # C: reversal candle quality
    require_vol_decline: bool = False  # D: volume exhaustion
    min_displacement_atr: float = 0    # E: minimum price move between swings
    require_bb_extreme: bool = False   # F: price near BB extreme
    require_slope_against: bool = False  # G: EMA slope against direction (counter-trend)
    min_swing_gap: int = 5          # H: minimum bars between swings
    long_only: bool = False         # I: long trades only


def apply_filter(sig: DV1Signal, cfg: FilterConfig) -> bool:
    """Return True if signal passes all filter gates."""
    if cfg.long_only and sig.direction != "long":
        return False
    if sig.rsi_diff < cfg.min_rsi_diff:
        return False
    if sig.direction == "long" and sig.rsi_at_signal > cfg.rsi_threshold:
        return False
    if sig.direction == "short" and sig.rsi_at_signal < (100 - cfg.rsi_threshold):
        return False
    if sig.clv < cfg.clv_threshold and sig.direction == "long":
        return False
    if (1.0 - sig.clv) < cfg.clv_threshold and sig.direction == "short":
        return False
    if cfg.require_vol_decline and not sig.volume_declining:
        return False
    if sig.price_displacement_atr < cfg.min_displacement_atr:
        return False
    if cfg.require_bb_extreme:
        if sig.direction == "long" and not sig.near_bb_lower:
            return False
        if sig.direction == "short" and not sig.near_bb_upper:
            return False
    if cfg.require_slope_against and not sig.ema_slope_against:
        return False
    if sig.swing_bars_apart < cfg.min_swing_gap:
        return False
    return True


# ─── Trade simulation ────────────────────────────────────────────────────────

def signal_to_trade(sig: DV1Signal, all_bars: list[dict], time_stop_bars: int) -> Optional[SimTrade]:
    """Convert signal to trade with proper fill."""
    entry_bar_idx = sig.bar_idx + 1
    if entry_bar_idx >= len(all_bars):
        return None

    entry_bar = all_bars[entry_bar_idx]
    actual_entry = entry_bar["open"]

    if sig.direction == "long":
        entry_with_slip = actual_entry * (1 + SLIPPAGE_ENTRY_BPS / 10000)
        actual_r = entry_with_slip - sig.stop_price
    else:
        entry_with_slip = actual_entry * (1 - SLIPPAGE_ENTRY_BPS / 10000)
        actual_r = sig.stop_price - entry_with_slip

    if actual_r <= 0:
        return None

    fee_per_unit = 2 * entry_with_slip * FEE_TAKER_BPS / 10000
    if fee_per_unit > 0.3 * actual_r:
        return None

    max_bar = min(entry_bar_idx + MAX_BARS_FORWARD, len(all_bars) - 1)
    exit_bars = all_bars[entry_bar_idx + 1:max_bar + 1]

    bar_ts = entry_bar["bar_start_ts"]
    if bar_ts.tzinfo is None:
        bar_ts = bar_ts.replace(tzinfo=timezone.utc)

    return SimTrade(
        direction=sig.direction,
        entry_price=entry_with_slip,
        stop_price=sig.stop_price,
        r_value=actual_r,
        time_stop_bars=time_stop_bars,
        bars=exit_bars,
        created_ts=bar_ts,
        symbol=sig.symbol,
        position_size=POSITION_SIZE_USD / entry_with_slip,
    )


def run_exit(trade: SimTrade, config: ExitConfig) -> tuple[str, float, int]:
    hwm = None
    half_r_touched = False
    bars_held = 0
    for bar in trade.bars:
        bars_held += 1
        exit_result, new_hwm, new_half_r = check_exit_simple(
            side=trade.direction, bar=bar,
            entry_price=trade.entry_price, stop_price=trade.stop_price,
            tp1_price=None, tp2_price=None,
            r_value=trade.r_value, bars_held=bars_held,
            max_bars=trade.time_stop_bars, high_water_mark=hwm,
            half_r_touched=half_r_touched, config=config,
        )
        hwm = new_hwm
        half_r_touched = new_half_r
        if exit_result:
            return exit_result[0], exit_result[1], bars_held
    if trade.bars:
        return "TIME_STOP", trade.bars[-1]["close"], bars_held
    return "TIME_STOP", trade.entry_price, 0


def compute_pnl(trade: SimTrade, exit_reason: str, exit_price: float) -> tuple[float, float]:
    if exit_reason != "STOP_LOSS":
        if trade.direction == "long":
            exit_with_slip = exit_price * (1 - SLIPPAGE_EXIT_BPS / 10000)
        else:
            exit_with_slip = exit_price * (1 + SLIPPAGE_EXIT_BPS / 10000)
    else:
        exit_with_slip = exit_price
    if trade.direction == "long":
        pnl_gross = (exit_with_slip - trade.entry_price) * trade.position_size
    else:
        pnl_gross = (trade.entry_price - exit_with_slip) * trade.position_size
    fee_bps = FEE_TAKER_BPS
    entry_fee = trade.entry_price * trade.position_size * (fee_bps / 10000)
    exit_fee = exit_with_slip * trade.position_size * (fee_bps / 10000)
    pnl_net = pnl_gross - entry_fee - exit_fee
    risk = trade.r_value * trade.position_size
    r_mult = pnl_net / risk if risk > 0 else 0
    return pnl_net, r_mult


# ─── Metrics ──────────────────────────────────────────────────────────────────

def calc_metrics(results: list[dict]) -> dict:
    if not results:
        return {"n": 0, "wins": 0, "wr": 0, "pf": 0, "pnl": 0, "avg_r": 0, "ff_rate": 0, "exits": {}}
    pnls = [r["pnl"] for r in results]
    rs = [r["r_mult"] for r in results]
    exits = defaultdict(int)
    for r in results:
        exits[r["exit_reason"]] += 1
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    ff = exits.get("FAIL_FAST", 0)
    return {
        "n": n, "wins": wins,
        "wr": wins / n if n > 0 else 0,
        "pf": gp / gl if gl > 0 else (99.99 if gp > 0 else 0),
        "pnl": sum(pnls), "avg_r": sum(rs) / n if n > 0 else 0,
        "ff_rate": ff / n if n > 0 else 0,
        "exits": dict(exits),
    }


# ─── Data loading ─────────────────────────────────────────────────────────────

def load_all_1h_ohlcv(database_url: str, days: int) -> dict[str, list[dict]]:
    start_ts = datetime.now(timezone.utc) - timedelta(days=days)
    end_ts = datetime.now(timezone.utc)
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv
                WHERE timeframe = '1h' AND bar_start_ts >= %s AND bar_start_ts < %s AND is_final = TRUE
            """, (start_ts, end_ts))
            symbols = [row[0] for row in cur.fetchall()]
            logger.info("Found %d symbols with 1h data", len(symbols))

            cache = {}
            for idx, symbol in enumerate(symbols):
                cur.execute("""
                    SELECT DISTINCT ON (bar_start_ts) bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = '1h'
                      AND bar_start_ts >= %s AND bar_start_ts < %s AND is_final = TRUE
                    ORDER BY bar_start_ts, CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END;
                """, (symbol, start_ts, end_ts))
                rows = cur.fetchall()
                if len(rows) < MIN_BARS_NEEDED:
                    continue
                cache[symbol] = [{
                    "bar_start_ts": r[0], "open": float(r[1]), "high": float(r[2]),
                    "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
                } for r in rows]
                if (idx + 1) % 100 == 0:
                    logger.info("OHLCV loaded: %d/%d symbols", idx + 1, len(symbols))

    logger.info("OHLCV: %d symbols, %d total bars", len(cache), sum(len(v) for v in cache.values()))
    return cache


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DV1 Divergence Filter Sweep")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    t0 = time.time()

    # ── Load OHLCV ────────────────────────────────────────────────────────
    logger.info("Loading %d days of 1h OHLCV...", args.days)
    ohlcv_cache = load_all_1h_ohlcv(database_url, args.days)

    # ── Generate all DV1 signals with metadata ────────────────────────────
    logger.info("Generating DV1 signals with full metadata...")
    all_signals: dict[str, list[DV1Signal]] = {}  # symbol -> signals
    total_sigs = 0
    for idx, (symbol, bars) in enumerate(ohlcv_cache.items()):
        sigs = generate_dv1_signals(bars, symbol)
        if sigs:
            all_signals[symbol] = sigs
            total_sigs += len(sigs)
        if (idx + 1) % 100 == 0:
            logger.info("Scanned %d/%d symbols, %d signals", idx + 1, len(ohlcv_cache), total_sigs)

    flat_signals = [s for sigs in all_signals.values() for s in sigs]
    logger.info("Total DV1 signals (loose gates): %d", len(flat_signals))

    # ── Signal metadata distribution ──────────────────────────────────────
    rsi_diffs = [s.rsi_diff for s in flat_signals]
    displacements = [s.price_displacement_atr for s in flat_signals]
    clvs = [s.clv for s in flat_signals]
    swing_gaps = [s.swing_bars_apart for s in flat_signals]
    vol_decl_pct = sum(1 for s in flat_signals if s.volume_declining) / len(flat_signals) * 100
    bb_near_pct = sum(1 for s in flat_signals if s.near_bb_lower or s.near_bb_upper) / len(flat_signals) * 100
    slope_against_pct = sum(1 for s in flat_signals if s.ema_slope_against) / len(flat_signals) * 100
    long_pct = sum(1 for s in flat_signals if s.direction == "long") / len(flat_signals) * 100

    print("\n" + "=" * 100)
    print("DV1 DIVERGENCE FILTER SWEEP")
    print(f"Data: {args.days} days, {len(ohlcv_cache)} symbols | Train < Feb 14 | Val >= Feb 14")
    print(f"Total loose DV1 signals: {len(flat_signals)} ({len(flat_signals)/args.days:.1f}/day)")
    print("=" * 100)

    print(f"\nSIGNAL METADATA DISTRIBUTION:")
    print(f"  RSI diff:       p25={np.percentile(rsi_diffs, 25):.1f}  p50={np.percentile(rsi_diffs, 50):.1f}  "
          f"p75={np.percentile(rsi_diffs, 75):.1f}  p90={np.percentile(rsi_diffs, 90):.1f}")
    print(f"  Displacement:   p25={np.percentile(displacements, 25):.2f}  p50={np.percentile(displacements, 50):.2f}  "
          f"p75={np.percentile(displacements, 75):.2f}  p90={np.percentile(displacements, 90):.2f} ATR")
    print(f"  CLV:            p25={np.percentile(clvs, 25):.2f}  p50={np.percentile(clvs, 50):.2f}  "
          f"p75={np.percentile(clvs, 75):.2f}")
    print(f"  Swing gap:      p25={np.percentile(swing_gaps, 25):.0f}  p50={np.percentile(swing_gaps, 50):.0f}  "
          f"p75={np.percentile(swing_gaps, 75):.0f} bars")
    print(f"  Vol declining:  {vol_decl_pct:.0f}%")
    print(f"  Near BB:        {bb_near_pct:.0f}%")
    print(f"  Slope against:  {slope_against_pct:.0f}%")
    print(f"  Long signals:   {long_pct:.0f}%")

    # ── Define filter combos ──────────────────────────────────────────────
    filters = [
        FilterConfig(name="BASE"),
        # Individual filters
        FilterConfig(name="A_rsiDiff5",   min_rsi_diff=5),
        FilterConfig(name="A_rsiDiff8",   min_rsi_diff=8),
        FilterConfig(name="A_rsiDiff12",  min_rsi_diff=12),
        FilterConfig(name="B_rsi40",      rsi_threshold=40),
        FilterConfig(name="B_rsi35",      rsi_threshold=35),
        FilterConfig(name="B_rsi30",      rsi_threshold=30),
        FilterConfig(name="C_clv55",      clv_threshold=0.55),
        FilterConfig(name="C_clv65",      clv_threshold=0.65),
        FilterConfig(name="D_volDecl",    require_vol_decline=True),
        FilterConfig(name="E_disp0.5",    min_displacement_atr=0.5),
        FilterConfig(name="E_disp1.0",    min_displacement_atr=1.0),
        FilterConfig(name="F_bbExtreme",  require_bb_extreme=True),
        FilterConfig(name="G_slopeAgst",  require_slope_against=True),
        FilterConfig(name="H_gap8",       min_swing_gap=8),
        FilterConfig(name="H_gap12",      min_swing_gap=12),
        FilterConfig(name="I_longOnly",   long_only=True),
        # Additive combos (building on best individual filters)
        FilterConfig(name="AB_d5_r40",    min_rsi_diff=5, rsi_threshold=40),
        FilterConfig(name="AB_d8_r35",    min_rsi_diff=8, rsi_threshold=35),
        FilterConfig(name="ABC_d5r40c55", min_rsi_diff=5, rsi_threshold=40, clv_threshold=0.55),
        FilterConfig(name="ABD_d5r40vol", min_rsi_diff=5, rsi_threshold=40, require_vol_decline=True),
        FilterConfig(name="ABE_d5r40dp",  min_rsi_diff=5, rsi_threshold=40, min_displacement_atr=0.5),
        FilterConfig(name="ABF_d5r40bb",  min_rsi_diff=5, rsi_threshold=40, require_bb_extreme=True),
        FilterConfig(name="ABG_d5r40slp", min_rsi_diff=5, rsi_threshold=40, require_slope_against=True),
        FilterConfig(name="ABCD_full4",   min_rsi_diff=5, rsi_threshold=40, clv_threshold=0.55, require_vol_decline=True),
        FilterConfig(name="ABCDE_full5",  min_rsi_diff=5, rsi_threshold=40, clv_threshold=0.55,
                     require_vol_decline=True, min_displacement_atr=0.5),
        FilterConfig(name="ABCDEF_full6", min_rsi_diff=5, rsi_threshold=40, clv_threshold=0.55,
                     require_vol_decline=True, min_displacement_atr=0.5, require_bb_extreme=True),
        FilterConfig(name="ABCDEG_slp",   min_rsi_diff=5, rsi_threshold=40, clv_threshold=0.55,
                     require_vol_decline=True, min_displacement_atr=0.5, require_slope_against=True),
        # Tight combos
        FilterConfig(name="TIGHT_d8r35bb", min_rsi_diff=8, rsi_threshold=35, require_bb_extreme=True),
        FilterConfig(name="TIGHT_d8r35vol", min_rsi_diff=8, rsi_threshold=35, require_vol_decline=True),
        FilterConfig(name="TIGHT_d8r35all", min_rsi_diff=8, rsi_threshold=35, clv_threshold=0.55,
                     require_vol_decline=True, require_bb_extreme=True),
        # Long-only variants of best combos
        FilterConfig(name="ABCD_long",    min_rsi_diff=5, rsi_threshold=40, clv_threshold=0.55,
                     require_vol_decline=True, long_only=True),
        FilterConfig(name="ABF_long",     min_rsi_diff=5, rsi_threshold=40, require_bb_extreme=True,
                     long_only=True),
    ]

    # Exit configs to test
    exit_configs = [
        ("Tight",    ExitConfig(trail_breakeven_r=0, trail_activation_r=0.75, trail_distance_r=0.50)),
        ("Medium",   ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0,  trail_distance_r=0.75)),
        ("Wide",     ExitConfig(trail_breakeven_r=0, trail_activation_r=1.5,  trail_distance_r=1.0)),
    ]

    time_stops = [12, 24]

    # ── Run sweep ─────────────────────────────────────────────────────────
    print(f"\n{'FILTER SWEEP':^100}")
    print("=" * 100)
    print(f"{'Filter':>18} {'Exit':>7} {'TS':>3} {'TrnN':>5} {'TrnWR':>6} {'TrnPF':>7} {'TrnPnL':>10}"
          f" {'TrnFF':>5} {'ValN':>5} {'ValWR':>6} {'ValPF':>7} {'ValPnL':>10} {'ValFF':>5} {'Sig/d':>5}")
    print("-" * 110)

    best_combos = []  # (filter_name, exit_name, ts, train_metrics, val_metrics)

    for filt in filters:
        # Apply filter to all signals
        filtered = [s for s in flat_signals if apply_filter(s, filt)]
        if len(filtered) < 5:
            print(f"{filt.name:>18} — skipped ({len(filtered)} signals)")
            continue

        for ts_bars in time_stops:
            for exit_name, exit_cfg in exit_configs:
                # Convert to trades and simulate
                results = []
                for sig in filtered:
                    bars = ohlcv_cache.get(sig.symbol, [])
                    if not bars:
                        continue
                    trade = signal_to_trade(sig, bars, ts_bars)
                    if trade is None:
                        continue
                    exit_reason, exit_price, bars_held = run_exit(trade, exit_cfg)
                    pnl, r_mult = compute_pnl(trade, exit_reason, exit_price)
                    results.append({
                        "pnl": pnl, "r_mult": r_mult, "exit_reason": exit_reason,
                        "created_ts": trade.created_ts, "direction": sig.direction,
                    })

                train = [r for r in results if r["created_ts"] < TRAIN_CUTOFF]
                val = [r for r in results if r["created_ts"] >= TRAIN_CUTOFF]
                tm = calc_metrics(train)
                vm = calc_metrics(val)

                sig_per_day = len(filtered) / args.days

                print(f"{filt.name:>18} {exit_name:>7} {ts_bars:>3} "
                      f"{tm['n']:>5} {tm['wr'] * 100:>5.1f}% {tm['pf']:>7.3f} {tm['pnl']:>+10.2f} {tm['ff_rate'] * 100:>4.1f}%"
                      f" {vm['n']:>5} {vm['wr'] * 100:>5.1f}% {vm['pf']:>7.3f} {vm['pnl']:>+10.2f} {vm['ff_rate'] * 100:>4.1f}%"
                      f" {sig_per_day:>5.1f}")

                # Track promising combos
                if vm["n"] >= 10 and vm["pf"] > 0.9:
                    best_combos.append((filt.name, exit_name, ts_bars, tm, vm, sig_per_day))

        # Blank line between filters
        if len(filtered) >= 5:
            print()

    # ── Best combos summary ───────────────────────────────────────────────
    print(f"\n{'TOP COMBOS (val PF > 0.9, val N >= 10)':^100}")
    print("=" * 100)

    # Sort by: both profitable first, then by min(train_pf, val_pf)
    def sort_key(x):
        _, _, _, tm, vm, _ = x
        both_profitable = tm["pf"] > 1.0 and vm["pf"] > 1.0
        min_pf = min(tm["pf"], vm["pf"])
        return (both_profitable, min_pf)

    best_combos.sort(key=sort_key, reverse=True)

    print(f"{'Filter':>18} {'Exit':>7} {'TS':>3} {'TrnPF':>7} {'ValPF':>7} {'TrnFF':>5} {'ValFF':>5}"
          f" {'Sig/d':>5} {'Status':>20}")
    print("-" * 90)

    for filt_name, exit_name, ts, tm, vm, spd in best_combos[:25]:
        both = tm["pf"] > 1.0 and vm["pf"] > 1.0
        status = "BOTH PROFITABLE" if both else ("val only" if vm["pf"] > 1.0 else "near-miss")
        print(f"{filt_name:>18} {exit_name:>7} {ts:>3} {tm['pf']:>7.3f} {vm['pf']:>7.3f} "
              f"{tm['ff_rate'] * 100:>4.1f}% {vm['ff_rate'] * 100:>4.1f}% {spd:>5.1f} {status:>20}")

    # ── Decision gate ─────────────────────────────────────────────────────
    both_profitable = [(f, e, ts, tm, vm, spd) for f, e, ts, tm, vm, spd in best_combos
                       if tm["pf"] > 1.0 and vm["pf"] > 1.0]
    val_profitable = [(f, e, ts, tm, vm, spd) for f, e, ts, tm, vm, spd in best_combos
                      if vm["pf"] > 1.0]

    print(f"\n{'DECISION GATE':^100}")
    print("=" * 100)

    if both_profitable:
        print(f"  [PASS] {len(both_profitable)} combos profitable in BOTH train and val:")
        for f, e, ts, tm, vm, spd in both_profitable[:5]:
            print(f"         {f} / {e} / TS={ts}: train PF {tm['pf']:.3f}, val PF {vm['pf']:.3f}, "
                  f"{spd:.1f} sig/day, train FF {tm['ff_rate']*100:.0f}%, val FF {vm['ff_rate']*100:.0f}%")
        print(f"\n  → Implement best combo as production setup")
    elif val_profitable:
        print(f"  [PARTIAL] {len(val_profitable)} combos val-profitable, 0 both-profitable")
        print(f"  → Val edge exists but regime-dependent")
    else:
        print(f"  [FAIL] No combo achieves val PF > 1.0")

    print()
    logger.info("Done in %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
