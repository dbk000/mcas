#!/usr/bin/env python3
"""
Signal Discovery v2 — Cross-Sectional & Regime-Adaptive Strategies
===================================================================

Fundamentally different approach: exploit the 446-symbol cross-section instead
of treating each symbol in isolation. Tests strategies proven to work in
quantitative finance, adapted for crypto.

Strategies:
  TSM   — Time-Series Momentum (N-hour returns, vol-normalized)
  XSM   — Cross-Sectional Momentum (rank symbols, long winners, short losers)
  BTCLEAD — BTC impulse → alt follow with lag
  VAMR  — Vol-Adjusted Mean Reversion (only in high-vol regimes)
  TOD   — Time-of-Day adaptive (momentum in US/EU, MR in Asia)
  SCALP — Short-hold momentum (1-3 bar exits, tight stops)

Walk-forward: 3+ rolling folds on 90 days (not single split).

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
      python3 /scripts/signal_discovery_v2.py --days 90
"""

import os
import sys
import time
import argparse
import logging
from dataclasses import dataclass
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
SLIPPAGE_BPS = 5.0
FEE_BPS = 4.0
MIN_SHORT_PRICE = 1.00
MAX_BARS_FORWARD = 100
WARMUP_BARS = 250
BTC_SYMBOL = "BTCUSDT"


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class Signal:
    strategy: str
    symbol: str
    direction: str  # "long" or "short"
    ts: datetime    # signal timestamp
    score: float    # quality 0-1
    meta: dict      # strategy-specific metadata


@dataclass
class TradeResult:
    strategy: str
    symbol: str
    direction: str
    ts: datetime
    pnl: float
    r_mult: float
    exit_reason: str
    bars_held: int
    score: float


# ─── Data loading ─────────────────────────────────────────────────────────────

def load_ohlcv(database_url: str, days: int) -> dict[str, list[dict]]:
    """Load 1h OHLCV for all symbols."""
    start_ts = datetime.now(timezone.utc) - timedelta(days=days + 15)  # extra warmup
    end_ts = datetime.now(timezone.utc)

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv
                WHERE timeframe = '1h' AND bar_start_ts >= %s AND is_final = TRUE
            """, (start_ts,))
            symbols = [r[0] for r in cur.fetchall()]
            logger.info("Found %d symbols", len(symbols))

            cache = {}
            for idx, sym in enumerate(symbols):
                cur.execute("""
                    SELECT DISTINCT ON (bar_start_ts) bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = '1h'
                      AND bar_start_ts >= %s AND bar_start_ts < %s AND is_final = TRUE
                    ORDER BY bar_start_ts,
                             CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END
                """, (sym, start_ts, end_ts))
                rows = cur.fetchall()
                if len(rows) >= WARMUP_BARS:
                    cache[sym] = [{
                        "ts": r[0] if r[0].tzinfo else r[0].replace(tzinfo=timezone.utc),
                        "open": float(r[1]), "high": float(r[2]),
                        "low": float(r[3]), "close": float(r[4]),
                        "volume": float(r[5]),
                    } for r in rows]
                if (idx + 1) % 100 == 0:
                    logger.info("Loaded %d/%d symbols", idx + 1, len(symbols))

    logger.info("Loaded %d symbols with %d+ bars (%d total bars)",
                len(cache), WARMUP_BARS, sum(len(v) for v in cache.values()))
    return cache


def build_timestamp_grid(ohlcv: dict[str, list[dict]]) -> list[datetime]:
    """Build sorted list of all unique timestamps across all symbols."""
    all_ts = set()
    for bars in ohlcv.values():
        for b in bars:
            all_ts.add(b["ts"])
    return sorted(all_ts)


def build_close_matrix(
    ohlcv: dict[str, list[dict]], symbols: list[str], ts_grid: list[datetime],
) -> np.ndarray:
    """Build (n_timestamps, n_symbols) close price matrix. NaN for missing."""
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    matrix = np.full((len(ts_grid), len(symbols)), np.nan)
    for j, sym in enumerate(symbols):
        for bar in ohlcv.get(sym, []):
            i = ts_to_idx.get(bar["ts"])
            if i is not None:
                matrix[i, j] = bar["close"]
    return matrix


def build_volume_matrix(
    ohlcv: dict[str, list[dict]], symbols: list[str], ts_grid: list[datetime],
) -> np.ndarray:
    """Build (n_timestamps, n_symbols) volume matrix."""
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    matrix = np.full((len(ts_grid), len(symbols)), np.nan)
    for j, sym in enumerate(symbols):
        for bar in ohlcv.get(sym, []):
            i = ts_to_idx.get(bar["ts"])
            if i is not None:
                matrix[i, j] = bar["volume"]
    return matrix


# ─── Indicator helpers ────────────────────────────────────────────────────────

def rolling_return(closes: np.ndarray, lookback: int) -> np.ndarray:
    """N-bar return: (close[i] - close[i-N]) / close[i-N]."""
    result = np.full(len(closes), np.nan)
    result[lookback:] = (closes[lookback:] - closes[:-lookback]) / np.where(
        closes[:-lookback] != 0, closes[:-lookback], 1.0)
    return result


def rolling_atr(highs, lows, closes, n=14):
    """ATR series."""
    tr = np.zeros(len(closes))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = np.zeros(len(closes))
    for i in range(n - 1, len(closes)):
        atr[i] = np.mean(tr[max(0, i - n + 1):i + 1])
    return atr


def rolling_std(data, n):
    """Rolling standard deviation."""
    result = np.full(len(data), np.nan)
    for i in range(n - 1, len(data)):
        result[i] = np.std(data[i - n + 1:i + 1])
    return result


def rolling_rsi(closes, n=14):
    """RSI series."""
    if len(closes) < n + 1:
        return np.full(len(closes), 50.0)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = np.zeros(len(deltas))
    avg_l = np.zeros(len(deltas))
    avg_g[n-1] = np.mean(gains[:n])
    avg_l[n-1] = np.mean(losses[:n])
    for i in range(n, len(deltas)):
        avg_g[i] = (avg_g[i-1] * (n-1) + gains[i]) / n
        avg_l[i] = (avg_l[i-1] * (n-1) + losses[i]) / n
    rsi = np.full(len(closes), 50.0)
    for i in range(n, len(closes)):
        if avg_l[i-1] == 0:
            rsi[i] = 100.0
        else:
            rsi[i] = 100.0 - 100.0 / (1.0 + avg_g[i-1] / avg_l[i-1])
    return rsi


def rolling_bb_width(closes, n=20, k=2.0):
    """Bollinger Band width series (normalized by mid)."""
    result = np.full(len(closes), np.nan)
    for i in range(n - 1, len(closes)):
        window = closes[i - n + 1:i + 1]
        mid = np.mean(window)
        std = np.std(window)
        if mid > 0:
            result[i] = (2 * k * std) / mid
    return result


# ─── Trade simulation ────────────────────────────────────────────────────────

def simulate_signal(
    signal: Signal, ohlcv: dict[str, list[dict]],
    stop_atr_mult: float, time_stop: int, exit_config: ExitConfig,
) -> Optional[TradeResult]:
    """Simulate a single signal → trade → exit."""
    bars = ohlcv.get(signal.symbol, [])
    if not bars:
        return None

    # Find signal bar index
    sig_idx = None
    for i, b in enumerate(bars):
        if b["ts"] == signal.ts:
            sig_idx = i
            break
    if sig_idx is None or sig_idx + 1 >= len(bars):
        return None

    # Need ATR for stop placement
    if sig_idx < 14:
        return None
    highs = np.array([b["high"] for b in bars[:sig_idx + 1]])
    lows = np.array([b["low"] for b in bars[:sig_idx + 1]])
    closes = np.array([b["close"] for b in bars[:sig_idx + 1]])
    atr = rolling_atr(highs, lows, closes, 14)[-1]
    if atr <= 0:
        return None

    # Entry at next bar open + slippage
    entry_bar = bars[sig_idx + 1]
    raw_entry = entry_bar["open"]
    if signal.direction == "long":
        entry = raw_entry * (1 + SLIPPAGE_BPS / 10000)
        stop = entry - stop_atr_mult * atr
    else:
        entry = raw_entry * (1 - SLIPPAGE_BPS / 10000)
        stop = entry + stop_atr_mult * atr
        if entry < MIN_SHORT_PRICE:
            return None

    if stop <= 0 or entry <= 0:
        return None

    r_value = abs(entry - stop)
    if r_value <= 0:
        return None
    stop_pct = r_value / entry
    if stop_pct > 0.10:
        return None

    # Fee gate
    if 2 * entry * FEE_BPS / 10000 > 0.3 * r_value:
        return None

    # Collect exit bars
    max_idx = min(sig_idx + 1 + MAX_BARS_FORWARD, len(bars) - 1)
    exit_bars = bars[sig_idx + 2:max_idx + 1]
    if not exit_bars:
        return None

    # Run exit logic
    hwm = None
    half_r_touched = False
    bars_held = 0
    exit_reason = "TIME_STOP"
    exit_price = exit_bars[-1]["close"]

    for bar in exit_bars:
        bars_held += 1
        result, new_hwm, new_half_r = check_exit_simple(
            side=signal.direction, bar=bar,
            entry_price=entry, stop_price=stop,
            tp1_price=None, tp2_price=None,
            r_value=r_value, bars_held=bars_held,
            max_bars=time_stop, high_water_mark=hwm,
            half_r_touched=half_r_touched, config=exit_config,
        )
        hwm = new_hwm
        half_r_touched = new_half_r
        if result:
            exit_reason, exit_price = result
            break

    # PnL
    if exit_reason != "STOP_LOSS":
        if signal.direction == "long":
            ep = exit_price * (1 - SLIPPAGE_BPS / 10000)
        else:
            ep = exit_price * (1 + SLIPPAGE_BPS / 10000)
    else:
        ep = exit_price

    if signal.direction == "long":
        pnl_gross = (ep - entry) * (POSITION_SIZE_USD / entry)
    else:
        pnl_gross = (entry - ep) * (POSITION_SIZE_USD / entry)

    pos_size = POSITION_SIZE_USD / entry
    fees = (entry + ep) * pos_size * FEE_BPS / 10000
    pnl_net = pnl_gross - fees
    r_mult = pnl_net / (r_value * pos_size) if r_value * pos_size > 0 else 0

    return TradeResult(
        strategy=signal.strategy, symbol=signal.symbol,
        direction=signal.direction, ts=signal.ts,
        pnl=pnl_net, r_mult=r_mult, exit_reason=exit_reason,
        bars_held=bars_held, score=signal.score,
    )


# ─── Strategy: Time-Series Momentum (TSM) ────────────────────────────────────

def gen_tsm(
    ohlcv: dict[str, list[dict]], lookback: int, threshold: float,
    min_ts: datetime, max_ts: datetime,
) -> list[Signal]:
    """
    Time-Series Momentum: go long if N-hour return > threshold,
    short if < -threshold. Threshold is in ATR-normalized units.
    """
    signals = []
    for sym, bars in ohlcv.items():
        if len(bars) < WARMUP_BARS:
            continue
        closes = np.array([b["close"] for b in bars])
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        atr = rolling_atr(highs, lows, closes, 14)
        returns = rolling_return(closes, lookback)

        for i in range(WARMUP_BARS, len(bars) - 1):
            ts = bars[i]["ts"]
            if ts < min_ts or ts >= max_ts:
                continue
            if atr[i] <= 0 or np.isnan(returns[i]):
                continue
            # ATR-normalized return
            norm_ret = (closes[i] - closes[i - lookback]) / (atr[i] * lookback)

            if norm_ret > threshold:
                signals.append(Signal("TSM", sym, "long", ts,
                    score=min(1.0, norm_ret / (2 * threshold)),
                    meta={"lookback": lookback, "norm_ret": norm_ret}))
            elif norm_ret < -threshold:
                signals.append(Signal("TSM", sym, "short", ts,
                    score=min(1.0, abs(norm_ret) / (2 * threshold)),
                    meta={"lookback": lookback, "norm_ret": norm_ret}))
    return signals


# ─── Strategy: Cross-Sectional Momentum (XSM) ────────────────────────────────

def gen_xsm(
    close_matrix: np.ndarray, symbols: list[str], ts_grid: list[datetime],
    lookback: int, top_pct: float, rebalance_every: int,
    min_ts: datetime, max_ts: datetime,
) -> list[Signal]:
    """
    Cross-Sectional Momentum: rank all symbols by N-hour return.
    Long top quintile, short bottom quintile.
    Only generate signals at rebalance points (every N bars).
    """
    signals = []
    n_ts, n_sym = close_matrix.shape

    for i in range(max(WARMUP_BARS, lookback), n_ts - 1, rebalance_every):
        ts = ts_grid[i]
        if ts < min_ts or ts >= max_ts:
            continue

        # Compute returns for all symbols
        past = close_matrix[i - lookback]
        current = close_matrix[i]
        valid = ~np.isnan(past) & ~np.isnan(current) & (past > 0)
        if valid.sum() < 20:
            continue

        returns = np.full(n_sym, np.nan)
        returns[valid] = (current[valid] - past[valid]) / past[valid]

        # Rank valid symbols
        valid_indices = np.where(valid)[0]
        valid_returns = returns[valid_indices]
        sorted_idx = np.argsort(valid_returns)

        n_valid = len(sorted_idx)
        n_top = max(1, int(n_valid * top_pct))
        n_bottom = max(1, int(n_valid * top_pct))

        # Long top performers
        for rank in range(n_valid - n_top, n_valid):
            j = valid_indices[sorted_idx[rank]]
            sym = symbols[j]
            ret = valid_returns[sorted_idx[rank]]
            signals.append(Signal("XSM", sym, "long", ts,
                score=min(1.0, ret * 10),
                meta={"lookback": lookback, "return": ret, "rank": rank}))

        # Short bottom performers (with price filter)
        for rank in range(n_bottom):
            j = valid_indices[sorted_idx[rank]]
            sym = symbols[j]
            if current[j] < MIN_SHORT_PRICE:
                continue
            ret = valid_returns[sorted_idx[rank]]
            signals.append(Signal("XSM", sym, "short", ts,
                score=min(1.0, abs(ret) * 10),
                meta={"lookback": lookback, "return": ret, "rank": rank}))

    return signals


# ─── Strategy: BTC Lead-Lag (BTCLEAD) ────────────────────────────────────────

def gen_btclead(
    ohlcv: dict[str, list[dict]], btc_bars: list[dict],
    impulse_atr_mult: float, alt_lag_threshold: float,
    min_ts: datetime, max_ts: datetime,
) -> list[Signal]:
    """
    When BTC makes an impulse move (> N ATR in 1 bar), enter alts that
    haven't moved yet in BTC's direction. Exploits information lag.
    """
    if not btc_bars or len(btc_bars) < WARMUP_BARS:
        return []

    # Precompute BTC indicators
    btc_closes = np.array([b["close"] for b in btc_bars])
    btc_highs = np.array([b["high"] for b in btc_bars])
    btc_lows = np.array([b["low"] for b in btc_bars])
    btc_atr = rolling_atr(btc_highs, btc_lows, btc_closes, 14)

    # Build BTC timestamp index
    btc_ts_idx = {b["ts"]: i for i, b in enumerate(btc_bars)}

    signals = []

    for i in range(WARMUP_BARS, len(btc_bars) - 1):
        ts = btc_bars[i]["ts"]
        if ts < min_ts or ts >= max_ts:
            continue
        if btc_atr[i] <= 0:
            continue

        # BTC move this bar
        btc_move = (btc_closes[i] - btc_bars[i]["open"]) / btc_atr[i]
        btc_ret = (btc_closes[i] - btc_bars[i]["open"]) / btc_bars[i]["open"]

        if abs(btc_move) < impulse_atr_mult:
            continue

        btc_dir = "long" if btc_move > 0 else "short"

        # Scan alts for laggards
        for sym, bars in ohlcv.items():
            if sym == BTC_SYMBOL:
                continue

            # Find matching bar
            alt_idx = None
            for j, b in enumerate(bars):
                if b["ts"] == ts:
                    alt_idx = j
                    break
            if alt_idx is None or alt_idx + 1 >= len(bars) or alt_idx < 2:
                continue

            bar = bars[alt_idx]
            alt_ret = (bar["close"] - bar["open"]) / bar["open"] if bar["open"] > 0 else 0

            # Alt hasn't moved much relative to BTC
            if btc_dir == "long" and alt_ret < btc_ret * alt_lag_threshold:
                signals.append(Signal("BTCLEAD", sym, "long", ts,
                    score=min(1.0, abs(btc_move) / (2 * impulse_atr_mult)),
                    meta={"btc_move_atr": btc_move, "alt_ret": alt_ret, "btc_ret": btc_ret}))
            elif btc_dir == "short" and alt_ret > btc_ret * alt_lag_threshold:
                if bar["close"] >= MIN_SHORT_PRICE:
                    signals.append(Signal("BTCLEAD", sym, "short", ts,
                        score=min(1.0, abs(btc_move) / (2 * impulse_atr_mult)),
                        meta={"btc_move_atr": btc_move, "alt_ret": alt_ret, "btc_ret": btc_ret}))

    return signals


# ─── Strategy: Volatility-Adjusted Mean Reversion (VAMR) ─────────────────────

def gen_vamr(
    ohlcv: dict[str, list[dict]],
    bb_width_pctl_min: float, rsi_extreme: float,
    min_ts: datetime, max_ts: datetime,
) -> list[Signal]:
    """
    Mean reversion ONLY when volatility is elevated (BB width > Nth percentile).
    In high-vol regimes, extremes snap back harder.
    Requires RSI extreme + price at BB extreme + high vol.
    """
    signals = []
    for sym, bars in ohlcv.items():
        if len(bars) < WARMUP_BARS:
            continue
        closes = np.array([b["close"] for b in bars])
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])

        rsi = rolling_rsi(closes, 14)
        bb_w = rolling_bb_width(closes, 20, 2.0)

        # Compute BB bands for touch detection
        for i in range(WARMUP_BARS, len(bars) - 1):
            ts = bars[i]["ts"]
            if ts < min_ts or ts >= max_ts:
                continue
            if np.isnan(bb_w[i]):
                continue

            # BB width percentile (rolling 200-bar lookback)
            lookback = min(200, i)
            bb_hist = bb_w[i - lookback:i + 1]
            bb_hist = bb_hist[~np.isnan(bb_hist)]
            if len(bb_hist) < 50:
                continue
            bb_pctl = np.sum(bb_hist < bb_w[i]) / len(bb_hist) * 100

            if bb_pctl < bb_width_pctl_min:
                continue  # Skip low-vol regime

            # BB bands
            window = closes[i - 19:i + 1]
            bb_mid = np.mean(window)
            bb_std = np.std(window)
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std

            close = closes[i]
            clv = (close - lows[i]) / (highs[i] - lows[i]) if highs[i] != lows[i] else 0.5

            # Long: RSI extreme low + price at BB lower + reversal candle
            if rsi[i] < rsi_extreme and close < bb_lower and clv > 0.45:
                signals.append(Signal("VAMR", sym, "long", ts,
                    score=min(1.0, (rsi_extreme - rsi[i]) / 20 + (bb_pctl - bb_width_pctl_min) / 50),
                    meta={"rsi": rsi[i], "bb_pctl": bb_pctl, "clv": clv}))

            # Short: RSI extreme high + price at BB upper + rejection candle
            if rsi[i] > (100 - rsi_extreme) and close > bb_upper and clv < 0.55:
                if close >= MIN_SHORT_PRICE:
                    signals.append(Signal("VAMR", sym, "short", ts,
                        score=min(1.0, (rsi[i] - (100 - rsi_extreme)) / 20 + (bb_pctl - bb_width_pctl_min) / 50),
                        meta={"rsi": rsi[i], "bb_pctl": bb_pctl, "clv": clv}))

    return signals


# ─── Strategy: Time-of-Day Adaptive (TOD) ────────────────────────────────────

def gen_tod(
    ohlcv: dict[str, list[dict]],
    min_ts: datetime, max_ts: datetime,
) -> list[Signal]:
    """
    Exploit crypto's intraday patterns:
    - US/EU hours (12:00-22:00 UTC): momentum (follow the move)
    - Asia hours (02:00-08:00 UTC): mean-reversion (fade the move)
    Uses 4-hour returns for signal generation.
    """
    signals = []
    for sym, bars in ohlcv.items():
        if len(bars) < WARMUP_BARS:
            continue
        closes = np.array([b["close"] for b in bars])
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])

        atr = rolling_atr(highs, lows, closes, 14)
        rsi = rolling_rsi(closes, 14)
        ret_4h = rolling_return(closes, 4)

        for i in range(WARMUP_BARS, len(bars) - 1):
            ts = bars[i]["ts"]
            if ts < min_ts or ts >= max_ts:
                continue
            if atr[i] <= 0 or np.isnan(ret_4h[i]):
                continue

            hour = ts.hour
            norm_ret = (closes[i] - closes[i - 4]) / (atr[i] * 4)

            # US/EU hours: momentum (follow strong moves)
            if 12 <= hour <= 22:
                if norm_ret > 0.15:
                    signals.append(Signal("TOD_MOM", sym, "long", ts,
                        score=min(1.0, norm_ret / 0.3),
                        meta={"hour": hour, "norm_ret": norm_ret, "mode": "momentum"}))
                elif norm_ret < -0.15:
                    if closes[i] >= MIN_SHORT_PRICE:
                        signals.append(Signal("TOD_MOM", sym, "short", ts,
                            score=min(1.0, abs(norm_ret) / 0.3),
                            meta={"hour": hour, "norm_ret": norm_ret, "mode": "momentum"}))

            # Asia hours: mean-reversion (fade extremes)
            elif 2 <= hour <= 8:
                if rsi[i] < 30 and norm_ret < -0.10:
                    signals.append(Signal("TOD_MR", sym, "long", ts,
                        score=min(1.0, (30 - rsi[i]) / 15),
                        meta={"hour": hour, "rsi": rsi[i], "mode": "mean_reversion"}))
                elif rsi[i] > 70 and norm_ret > 0.10:
                    if closes[i] >= MIN_SHORT_PRICE:
                        signals.append(Signal("TOD_MR", sym, "short", ts,
                            score=min(1.0, (rsi[i] - 70) / 15),
                            meta={"hour": hour, "rsi": rsi[i], "mode": "mean_reversion"}))

    return signals


# ─── Strategy: Short-Hold Scalp (SCALP) ──────────────────────────────────────

def gen_scalp(
    ohlcv: dict[str, list[dict]],
    min_ts: datetime, max_ts: datetime,
) -> list[Signal]:
    """
    Quick momentum trades: strong 1-bar move with volume confirmation.
    Enter next bar, exit within 1-3 bars. Tight stops.
    The edge is in the immediate continuation, not the trend.
    """
    signals = []
    for sym, bars in ohlcv.items():
        if len(bars) < WARMUP_BARS:
            continue
        closes = np.array([b["close"] for b in bars])
        opens = np.array([b["open"] for b in bars])
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        volumes = np.array([b["volume"] for b in bars])

        atr = rolling_atr(highs, lows, closes, 14)
        # Volume Z-score
        vol_z = np.zeros(len(volumes))
        for i in range(20, len(volumes)):
            w = volumes[i - 20:i]
            m, s = np.mean(w), np.std(w)
            vol_z[i] = (volumes[i] - m) / s if s > 0 else 0

        for i in range(WARMUP_BARS, len(bars) - 1):
            ts = bars[i]["ts"]
            if ts < min_ts or ts >= max_ts:
                continue
            if atr[i] <= 0:
                continue

            # Bar strength: how much of the range is body (not wick)
            bar_range = highs[i] - lows[i]
            if bar_range <= 0:
                continue
            body = abs(closes[i] - opens[i])
            body_ratio = body / bar_range
            bar_move_atr = (closes[i] - opens[i]) / atr[i]

            # Strong bullish bar + volume surge + clean body
            if bar_move_atr > 1.0 and vol_z[i] > 2.0 and body_ratio > 0.60:
                signals.append(Signal("SCALP", sym, "long", ts,
                    score=min(1.0, bar_move_atr / 2.0),
                    meta={"bar_move_atr": bar_move_atr, "vol_z": vol_z[i], "body_ratio": body_ratio}))

            # Strong bearish bar
            elif bar_move_atr < -1.0 and vol_z[i] > 2.0 and body_ratio > 0.60:
                if closes[i] >= MIN_SHORT_PRICE:
                    signals.append(Signal("SCALP", sym, "short", ts,
                        score=min(1.0, abs(bar_move_atr) / 2.0),
                        meta={"bar_move_atr": bar_move_atr, "vol_z": vol_z[i], "body_ratio": body_ratio}))

    return signals


# ─── Walk-Forward Engine ─────────────────────────────────────────────────────

@dataclass
class FoldResult:
    name: str
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_n: int
    train_pf: float
    train_wr: float
    test_n: int
    test_pf: float
    test_wr: float
    test_pnl: float
    test_ff_rate: float


def compute_folds(ts_grid: list[datetime], n_folds: int = 4) -> list[tuple]:
    """Generate rolling train/test fold boundaries."""
    start = ts_grid[WARMUP_BARS]
    end = ts_grid[-1]
    total_days = (end - start).total_seconds() / 86400
    test_days = total_days / (n_folds + 1)
    train_days = test_days * 2  # 2:1 train:test ratio

    folds = []
    for f in range(n_folds):
        test_start = start + timedelta(days=train_days + f * test_days)
        test_end = test_start + timedelta(days=test_days)
        train_start = test_start - timedelta(days=train_days)

        if test_end > end:
            test_end = end
        if test_start >= end:
            break

        folds.append((f"F{f+1}", train_start, test_start, test_start, test_end))

    return folds


def metrics(results: list[TradeResult]) -> dict:
    if not results:
        return {"n": 0, "pf": 0, "wr": 0, "pnl": 0, "avg_r": 0, "ff_rate": 0}
    pnls = [r.pnl for r in results]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    ff = sum(1 for r in results if r.exit_reason == "FAIL_FAST")
    return {
        "n": n, "pf": gp / gl if gl > 0 else (99.99 if gp > 0 else 0),
        "wr": wins / n, "pnl": sum(pnls),
        "avg_r": sum(r.r_mult for r in results) / n,
        "ff_rate": ff / n,
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set"); sys.exit(1)

    t0 = time.time()

    # ── Load data ─────────────────────────────────────────────────────────
    logger.info("Loading %dd of 1h OHLCV...", args.days)
    ohlcv = load_ohlcv(database_url, args.days)
    if not ohlcv:
        logger.error("No data"); sys.exit(1)

    symbols = sorted(ohlcv.keys())
    ts_grid = build_timestamp_grid(ohlcv)
    logger.info("Timestamp grid: %d hours (%s to %s)",
                len(ts_grid), ts_grid[0].strftime("%Y-%m-%d"), ts_grid[-1].strftime("%Y-%m-%d"))

    # Build close matrix for cross-sectional strategies
    logger.info("Building close/volume matrices...")
    close_mx = build_close_matrix(ohlcv, symbols, ts_grid)
    logger.info("Close matrix: %s (%.1f%% non-NaN)",
                close_mx.shape, np.sum(~np.isnan(close_mx)) / close_mx.size * 100)

    # BTC bars
    btc_bars = ohlcv.get(BTC_SYMBOL, [])
    logger.info("BTC bars: %d", len(btc_bars))

    # ── Walk-forward folds ────────────────────────────────────────────────
    folds = compute_folds(ts_grid, n_folds=4)
    logger.info("Walk-forward folds: %d", len(folds))
    for name, tr_s, tr_e, te_s, te_e in folds:
        logger.info("  %s: train %s→%s, test %s→%s",
                    name, tr_s.strftime("%m/%d"), tr_e.strftime("%m/%d"),
                    te_s.strftime("%m/%d"), te_e.strftime("%m/%d"))

    # ── Strategy configurations ───────────────────────────────────────────
    strategy_configs = [
        # (name, generator_fn, gen_kwargs, stop_atr, time_stop, exit_config)

        # TSM variants
        ("TSM_4h",  "tsm", {"lookback": 4,  "threshold": 0.12}, 1.5, 8,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=0.75, trail_distance_r=0.5)),
        ("TSM_12h", "tsm", {"lookback": 12, "threshold": 0.10}, 2.0, 16,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=0.75)),
        ("TSM_24h", "tsm", {"lookback": 24, "threshold": 0.08}, 2.0, 24,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=0.75)),
        ("TSM_48h", "tsm", {"lookback": 48, "threshold": 0.06}, 2.5, 36,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.5, trail_distance_r=1.0)),

        # XSM variants
        ("XSM_4h",  "xsm", {"lookback": 4,  "top_pct": 0.10, "rebalance_every": 4}, 1.5, 8,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=0.75, trail_distance_r=0.5)),
        ("XSM_12h", "xsm", {"lookback": 12, "top_pct": 0.10, "rebalance_every": 6}, 2.0, 16,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=0.75)),
        ("XSM_24h", "xsm", {"lookback": 24, "top_pct": 0.10, "rebalance_every": 12}, 2.0, 24,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=0.75)),
        ("XSM_24h_w", "xsm", {"lookback": 24, "top_pct": 0.10, "rebalance_every": 12}, 2.5, 36,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.5, trail_distance_r=1.0)),

        # BTC Lead-Lag variants
        ("BTCL_1.5", "btclead", {"impulse_atr_mult": 1.5, "alt_lag_threshold": 0.3}, 1.5, 6,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=0.75, trail_distance_r=0.5)),
        ("BTCL_2.0", "btclead", {"impulse_atr_mult": 2.0, "alt_lag_threshold": 0.3}, 1.5, 8,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=0.5)),

        # Vol-Adjusted Mean Reversion
        ("VAMR_60", "vamr", {"bb_width_pctl_min": 60, "rsi_extreme": 30}, 1.5, 12,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=0.75, trail_distance_r=0.5)),
        ("VAMR_75", "vamr", {"bb_width_pctl_min": 75, "rsi_extreme": 30}, 1.5, 12,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=0.75)),
        ("VAMR_75_t", "vamr", {"bb_width_pctl_min": 75, "rsi_extreme": 25}, 1.5, 12,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=1.0, trail_distance_r=0.75)),

        # Time-of-Day
        ("TOD", "tod", {}, 1.5, 8,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=0.75, trail_distance_r=0.5)),

        # Scalp
        ("SCALP_t", "scalp", {}, 1.0, 3,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=0.5, trail_distance_r=0.25)),
        ("SCALP_m", "scalp", {}, 1.5, 6,
         ExitConfig(trail_breakeven_r=0, trail_activation_r=0.75, trail_distance_r=0.5)),
    ]

    # ── Run each strategy through walk-forward ────────────────────────────
    print("\n" + "=" * 120)
    print("SIGNAL DISCOVERY v2 — CROSS-SECTIONAL & REGIME-ADAPTIVE STRATEGIES")
    print(f"Data: {args.days}d, {len(symbols)} symbols, {len(ts_grid)} hours")
    print(f"Walk-forward: {len(folds)} folds, rolling train/test")
    print("=" * 120)

    all_fold_results = defaultdict(list)  # strategy -> list of FoldResult

    for strat_name, gen_type, gen_kwargs, stop_atr, time_stop, exit_cfg in strategy_configs:
        logger.info("Running %s...", strat_name)

        for fold_name, tr_s, tr_e, te_s, te_e in folds:
            # Generate signals for full period
            full_start = tr_s
            full_end = te_e

            if gen_type == "tsm":
                sigs = gen_tsm(ohlcv, min_ts=full_start, max_ts=full_end, **gen_kwargs)
            elif gen_type == "xsm":
                sigs = gen_xsm(close_mx, symbols, ts_grid,
                              min_ts=full_start, max_ts=full_end, **gen_kwargs)
            elif gen_type == "btclead":
                sigs = gen_btclead(ohlcv, btc_bars,
                                  min_ts=full_start, max_ts=full_end, **gen_kwargs)
            elif gen_type == "vamr":
                sigs = gen_vamr(ohlcv, min_ts=full_start, max_ts=full_end, **gen_kwargs)
            elif gen_type == "tod":
                sigs = gen_tod(ohlcv, min_ts=full_start, max_ts=full_end)
            elif gen_type == "scalp":
                sigs = gen_scalp(ohlcv, min_ts=full_start, max_ts=full_end)
            else:
                continue

            # Split train/test
            train_sigs = [s for s in sigs if tr_s <= s.ts < tr_e]
            test_sigs = [s for s in sigs if te_s <= s.ts < te_e]

            # Simulate
            train_results = [r for r in (simulate_signal(s, ohlcv, stop_atr, time_stop, exit_cfg)
                             for s in train_sigs) if r is not None]
            test_results = [r for r in (simulate_signal(s, ohlcv, stop_atr, time_stop, exit_cfg)
                            for s in test_sigs) if r is not None]

            tm = metrics(train_results)
            em = metrics(test_results)

            all_fold_results[strat_name].append(FoldResult(
                name=fold_name,
                train_start=tr_s, train_end=tr_e,
                test_start=te_s, test_end=te_e,
                train_n=tm["n"], train_pf=tm["pf"], train_wr=tm["wr"],
                test_n=em["n"], test_pf=em["pf"], test_wr=em["wr"],
                test_pnl=em["pnl"], test_ff_rate=em["ff_rate"],
            ))

        logger.info("  %s: %d folds done", strat_name, len(folds))

    # ── Results table ─────────────────────────────────────────────────────
    print(f"\n{'WALK-FORWARD RESULTS BY STRATEGY':^120}")
    print("=" * 120)
    print(f"{'Strategy':>12} {'Fold':>4} {'TrN':>5} {'TrWR':>5} {'TrPF':>6}"
          f" {'TeN':>5} {'TeWR':>5} {'TePF':>6} {'TePnL':>9} {'TeFF':>5}")
    print("-" * 80)

    strategy_summaries = {}

    for strat_name in [sc[0] for sc in strategy_configs]:
        fold_results = all_fold_results.get(strat_name, [])
        if not fold_results:
            continue

        test_pfs = []
        total_test_n = 0
        total_test_pnl = 0

        for fr in fold_results:
            marker = " *" if fr.test_pf > 1.0 and fr.test_n >= 5 else ""
            print(f"{strat_name:>12} {fr.name:>4} {fr.train_n:>5} {fr.train_wr*100:>4.0f}% {fr.train_pf:>6.3f}"
                  f" {fr.test_n:>5} {fr.test_wr*100:>4.0f}% {fr.test_pf:>6.3f}"
                  f" {fr.test_pnl:>+9.1f} {fr.test_ff_rate*100:>4.0f}%{marker}")
            if fr.test_n >= 5:
                test_pfs.append(fr.test_pf)
            total_test_n += fr.test_n
            total_test_pnl += fr.test_pnl

        # Summary
        folds_profitable = sum(1 for pf in test_pfs if pf > 1.0)
        avg_test_pf = np.mean(test_pfs) if test_pfs else 0
        consistency = folds_profitable / len(test_pfs) if test_pfs else 0

        strategy_summaries[strat_name] = {
            "folds_profitable": folds_profitable,
            "total_folds": len(test_pfs),
            "avg_test_pf": avg_test_pf,
            "total_test_n": total_test_n,
            "total_test_pnl": total_test_pnl,
            "consistency": consistency,
        }

        status = f"  {folds_profitable}/{len(test_pfs)} profitable"
        if consistency >= 0.75:
            status += " ← CONSISTENT"
        elif consistency >= 0.50:
            status += " ← partial"
        print(f"{'':>12} {'AVG':>4} {'':>5} {'':>5} {'':>6}"
              f" {total_test_n:>5} {'':>5} {avg_test_pf:>6.3f}"
              f" {total_test_pnl:>+9.1f} {'':>5}{status}")
        print()

    # ── Ranking ───────────────────────────────────────────────────────────
    print(f"\n{'STRATEGY RANKING':^120}")
    print("=" * 120)
    print(f"{'Rank':>4} {'Strategy':>12} {'AvgTePF':>8} {'Folds+':>7} {'Consist':>8}"
          f" {'TotalN':>7} {'TotalPnL':>10}")
    print("-" * 65)

    ranked = sorted(strategy_summaries.items(),
                    key=lambda x: (x[1]["consistency"], x[1]["avg_test_pf"]),
                    reverse=True)

    for rank, (name, s) in enumerate(ranked, 1):
        marker = " <<<" if s["consistency"] >= 0.75 and s["avg_test_pf"] > 1.0 else ""
        print(f"{rank:>4} {name:>12} {s['avg_test_pf']:>8.3f} "
              f"{s['folds_profitable']}/{s['total_folds']:>2}   {s['consistency']*100:>6.0f}%"
              f" {s['total_test_n']:>7} {s['total_test_pnl']:>+10.1f}{marker}")

    # ── Decision gate ─────────────────────────────────────────────────────
    consistent = [(n, s) for n, s in ranked if s["consistency"] >= 0.75 and s["avg_test_pf"] > 1.0]
    partial = [(n, s) for n, s in ranked if s["consistency"] >= 0.50 and s["avg_test_pf"] > 0.9]

    print(f"\n{'DECISION GATE':^120}")
    print("=" * 120)

    if consistent:
        print(f"  [PASS] {len(consistent)} strategies with >= 75% fold consistency and avg test PF > 1.0:")
        for n, s in consistent:
            print(f"         {n}: avg PF {s['avg_test_pf']:.3f}, {s['folds_profitable']}/{s['total_folds']} folds, "
                  f"{s['total_test_n']} trades, PnL {s['total_test_pnl']:+.1f}")
        print(f"\n  → IMPLEMENT as production setups. These have demonstrated walk-forward edge.")
    elif partial:
        print(f"  [PARTIAL] {len(partial)} strategies with >= 50% consistency:")
        for n, s in partial:
            print(f"         {n}: avg PF {s['avg_test_pf']:.3f}, {s['folds_profitable']}/{s['total_folds']} folds")
        print(f"\n  → Promising but needs further refinement or more data.")
    else:
        print(f"  [FAIL] No strategy achieves >= 50% fold consistency with avg PF > 0.9")
        print(f"  → Current approaches insufficient. Consider: order flow data, funding rates,")
        print(f"    on-chain metrics, or higher-frequency (5m/15m) timeframes.")

    print()
    logger.info("Done in %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
