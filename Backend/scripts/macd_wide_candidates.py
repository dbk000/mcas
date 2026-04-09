#!/usr/bin/env python3
"""
macd_wide_candidates.py — Phase 0 Wide Dataset + Exit Walks for MACD Long Reversion
===================================================================================

Produces the wide candidate dataset defined in §3 of
macd_long_reversion_live_plan.md, plus the companion exit-walks dataset keyed
by (candidate_id × exit_profile_id), to enable offline sweeping of the
negative_regime filter without regenerating candidates per threshold change.

Two MACD tuples are evaluated in one pass to avoid loading OHLCV twice:
  - std:  (12, 26, 9)   variant_id = std_12_26_9   (S11)
  - fast: (3,  10, 16)  variant_id = fast_3_10_16  (S12)

Universe: distinct symbols from batch `regen_frozen_20260407` where setup_id='S10'
(apples-to-apples against research_macd_multi_tf_20260409_174344).

Outputs (under /repo_root/Backend/scripts/output/macd_wide_candidates_{ts}/):
  - candidates.csv / candidates.npz      (one row per raw crossover)
  - exit_walks.csv / exit_walks.npz      (one row per candidate × exit_profile)
  - generation_summary.json

Parallelism: mp.Pool(8) one worker per symbol shard, each opens its own
psycopg3 connection and serially loads OHLCV for its shard.

Walk semantics (identical to research_macd_multi_tf.py): NEXT_OPEN entry with
slip, stop = entry - stop_mult*ATR (touch or gap fill), time stop at bars_held
>= N → next-bar open. No TP/trail/FF.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Tuple

import numpy as np
import psycopg
from numpy.lib.stride_tricks import sliding_window_view

# ─────────────────────────────── Config ────────────────────────────────
TIMEFRAME = "1h"
BATCH_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
BATCH_END = datetime(2026, 4, 9, 23, 59, 59, tzinfo=timezone.utc)
WARMUP_BUFFER_DAYS = 14

MACD_TUPLES = [
    ("std_12_26_9",  12, 26, 9),
    ("fast_3_10_16",  3, 10, 16),
]

STOP_ATR_MULT_VALUES = [2.0, 3.0, 5.0]
TIME_STOP_BARS_VALUES = [24, 48, 96]
LOOKBACK_WINDOWS = [3, 5, 8, 12]

ATR_PERIOD = 14
RSI_PERIOD = 14
VOLUME_STATS_WINDOW = 20
MIN_BARS_REQUIRED = 220  # needs SMA(200) + buffer

SLIPPAGE_BPS = 5.0
FEE_TAKER_BPS = 4.0
POSITION_SIZE_USD = 1000.0

N_WORKERS = 4  # strats_evals service has cpus:"2.0" cgroup limit; 4 procs sharing 2 CPUs is the sweet spot with DB I/O blending
DATASET_VERSION = "v1"
FAMILY_ID = "macd_long_reversion"

REPO_ROOT = "/repo_root" if os.path.isdir("/repo_root") else "/home/darren/Desktop/MCAS"
OUTPUT_DIR = os.path.join(
    REPO_ROOT, "Backend", "scripts", "output",
    f"macd_wide_candidates_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
)


# ─────────────────────────────── Loaders ────────────────────────────────
def load_universe(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT symbol
            FROM opportunities
            WHERE batch_id = 'regen_frozen_20260407' AND setup_id = 'S10'
            ORDER BY symbol
            """
        )
        return [row[0] for row in cur.fetchall()]


def load_ohlcv(conn, symbol: str, start: datetime, end: datetime):
    with conn.cursor() as cur:
        cur.execute(
            """
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
                     END
            """,
            (symbol, TIMEFRAME, start, end),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    return {
        "ts": np.array([r[0] for r in rows], dtype=object),
        "open": np.array([float(r[1]) for r in rows]),
        "high": np.array([float(r[2]) for r in rows]),
        "low": np.array([float(r[3]) for r in rows]),
        "close": np.array([float(r[4]) for r in rows]),
        "volume": np.array([float(r[5]) for r in rows]),
        "n": len(rows),
    }


# ─────────────────────────────── Indicators ────────────────────────────────
def ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def compute_macd(close, fast_n, slow_n, signal_n):
    fast = ema(close, fast_n)
    slow = ema(close, slow_n)
    line = fast - slow
    sig = ema(line, signal_n)
    hist = line - sig
    return line, sig, hist


def compute_atr(high, low, close):
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.full(n, np.nan)
    if n >= ATR_PERIOD:
        atr[ATR_PERIOD - 1] = float(np.mean(tr[:ATR_PERIOD]))
        for i in range(ATR_PERIOD, n):
            atr[i] = (atr[i - 1] * (ATR_PERIOD - 1) + tr[i]) / ATR_PERIOD
    return atr


def compute_rsi(close):
    n = len(close)
    rsi = np.full(n, np.nan)
    if n < RSI_PERIOD + 1:
        return rsi
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full(n, np.nan)
    avg_loss = np.full(n, np.nan)
    avg_gain[RSI_PERIOD] = gain[:RSI_PERIOD].mean()
    avg_loss[RSI_PERIOD] = loss[:RSI_PERIOD].mean()
    for i in range(RSI_PERIOD + 1, n):
        avg_gain[i] = (avg_gain[i - 1] * (RSI_PERIOD - 1) + gain[i - 1]) / RSI_PERIOD
        avg_loss[i] = (avg_loss[i - 1] * (RSI_PERIOD - 1) + loss[i - 1]) / RSI_PERIOD
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, np.nan)
        rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_sma(close, period):
    n = len(close)
    sma = np.full(n, np.nan)
    if n < period:
        return sma
    cumsum = np.cumsum(close)
    sma[period - 1] = cumsum[period - 1] / period
    sma[period:] = (cumsum[period:] - cumsum[:-period]) / period
    return sma


def rolling_min_incl_current(arr, window):
    n = len(arr)
    out = np.empty(n)
    for i in range(min(window - 1, n)):
        slice_ = arr[: i + 1]
        out[i] = np.nanmin(slice_) if slice_.size > 0 else np.nan
    if n >= window:
        win = sliding_window_view(arr, window)
        out[window - 1:] = np.nanmin(win, axis=1)
    return out


def compute_bars_since_below_zero(arr):
    n = len(arr)
    is_below = arr < 0
    idx_arr = np.arange(n, dtype=np.int64)
    valued = np.where(is_below, idx_arr, -1)
    last_true = np.maximum.accumulate(valued)
    bars_since = np.where(last_true >= 0, idx_arr - last_true, -1)
    return bars_since


def find_bull_crossovers(hist):
    if len(hist) < 2:
        return np.array([], dtype=int)
    prev = hist[:-1]
    curr = hist[1:]
    return np.where((prev <= 0) & (curr > 0))[0] + 1


# ─────────────────────────────── Walk ────────────────────────────────
def walk_event_long(bars, signal_idx, stop_price, ts_bars):
    n = bars["n"]
    entry_idx = signal_idx + 1
    if entry_idx >= n:
        return None
    entry_price = float(bars["open"][entry_idx])
    entry_with_slip = entry_price * (1 + SLIPPAGE_BPS / 10000)
    r_value = entry_with_slip - stop_price
    if r_value <= 0:
        return None
    position_size = POSITION_SIZE_USD / entry_with_slip

    exit_idx = None
    exit_reason = None
    exit_price = None

    for j in range(entry_idx, min(n, entry_idx + ts_bars + 1)):
        bars_held = j - entry_idx
        low_j = float(bars["low"][j])
        open_j = float(bars["open"][j])
        if j > entry_idx and low_j <= stop_price:
            exit_price = open_j if open_j < stop_price else stop_price
            exit_reason = "STOP_LOSS"
            exit_idx = j
            break
        if bars_held >= ts_bars:
            exit_price = open_j
            exit_reason = "TIME_STOP"
            exit_idx = j
            break

    if exit_idx is None:
        exit_idx = n - 1
        exit_price = float(bars["close"][exit_idx])
        exit_reason = "END_OF_DATA"

    exit_with_slip = exit_price * (1 - SLIPPAGE_BPS / 10000)
    pnl_gross = (exit_with_slip - entry_with_slip) * position_size
    fees = (entry_with_slip + exit_with_slip) * position_size * FEE_TAKER_BPS / 10000
    pnl_net = pnl_gross - fees
    r_multiple = pnl_net / (r_value * position_size) if (r_value > 0 and position_size > 0) else 0.0

    entry_ts = bars["ts"][entry_idx]
    if hasattr(entry_ts, "tzinfo") and entry_ts.tzinfo is None:
        entry_ts = entry_ts.replace(tzinfo=timezone.utc)
    exit_ts = bars["ts"][exit_idx]
    if hasattr(exit_ts, "tzinfo") and exit_ts.tzinfo is None:
        exit_ts = exit_ts.replace(tzinfo=timezone.utc)

    return {
        "entry_ts": entry_ts,
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "r_value": float(r_value),
        "exit_ts": exit_ts,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "bars_held": int(exit_idx - entry_idx),
        "pnl_net": float(pnl_net),
        "r_multiple": float(r_multiple),
    }


# ─────────────────────────────── Per-symbol processor ────────────────────────────────
def process_symbol(conn, symbol: str) -> Tuple[list, list]:
    warmup_start = BATCH_START - timedelta(days=WARMUP_BUFFER_DAYS)
    bars = load_ohlcv(conn, symbol, warmup_start, BATCH_END)
    if bars is None or bars["n"] < MIN_BARS_REQUIRED:
        return [], []

    n = bars["n"]
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]

    ts_epoch = np.empty(n, dtype=np.int64)
    for i, t in enumerate(bars["ts"]):
        if hasattr(t, "tzinfo") and t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        ts_epoch[i] = int(t.timestamp())

    atr = compute_atr(high, low, close)
    rsi = compute_rsi(close)
    sma20 = compute_sma(close, 20)
    sma50 = compute_sma(close, 50)
    sma200 = compute_sma(close, 200)

    vol_mean = compute_sma(volume, VOLUME_STATS_WINDOW)
    vol_std = np.full(n, np.nan)
    if n >= VOLUME_STATS_WINDOW:
        vw = sliding_window_view(volume, VOLUME_STATS_WINDOW)
        vol_std[VOLUME_STATS_WINDOW - 1:] = vw.std(axis=1)

    cand_rows: list = []
    walk_rows: list = []

    for variant_id, fast_n, slow_n, signal_n in MACD_TUPLES:
        line, sig, hist = compute_macd(close, fast_n, slow_n, signal_n)
        line_mins = {w: rolling_min_incl_current(line, w) for w in LOOKBACK_WINDOWS}
        hist_mins = {w: rolling_min_incl_current(hist, w) for w in LOOKBACK_WINDOWS}
        bars_since_line = compute_bars_since_below_zero(line)
        bars_since_hist = compute_bars_since_below_zero(hist)
        zero_cross_line = np.concatenate(([False], (line[:-1] <= 0) & (line[1:] > 0)))
        crossovers = find_bull_crossovers(hist)

        for ev_idx in crossovers:
            if ev_idx + 1 >= n:
                continue
            ev_ts_raw = bars["ts"][ev_idx]
            ev_ts = (ev_ts_raw.replace(tzinfo=timezone.utc)
                     if hasattr(ev_ts_raw, "tzinfo") and ev_ts_raw.tzinfo is None
                     else ev_ts_raw)
            if ev_ts < BATCH_START or ev_ts > BATCH_END:
                continue
            if np.isnan(atr[ev_idx]) or atr[ev_idx] <= 0:
                continue

            ev_atr = float(atr[ev_idx])
            signal_open = float(bars["open"][ev_idx])
            signal_high = float(bars["high"][ev_idx])
            signal_low = float(bars["low"][ev_idx])
            signal_close = float(close[ev_idx])
            signal_volume = float(volume[ev_idx])
            next_open = float(bars["open"][ev_idx + 1])
            next_high = float(bars["high"][ev_idx + 1])
            next_low = float(bars["low"][ev_idx + 1])
            next_close = float(bars["close"][ev_idx + 1])
            next_volume = float(volume[ev_idx + 1])
            gap_pct = ((next_open - signal_close) / signal_close * 100) if signal_close > 0 else 0.0

            ev_ts_epoch = int(ts_epoch[ev_idx])
            entry_ts_epoch = int(ts_epoch[ev_idx + 1])
            candidate_id = f"{symbol}_{TIMEFRAME}_{variant_id}_{ev_ts_epoch}"

            def _fget(a, i):
                v = a[i]
                return float(v) if not (isinstance(v, float) and math.isnan(v)) and not np.isnan(v) else float("nan")

            rsi_14 = _fget(rsi, ev_idx) if ev_idx < len(rsi) else float("nan")
            rsi_prev_1 = _fget(rsi, ev_idx - 1) if ev_idx - 1 >= 0 else float("nan")
            rsi_slope_1 = (rsi_14 - rsi_prev_1) if not (math.isnan(rsi_14) or math.isnan(rsi_prev_1)) else float("nan")

            sma20_v = _fget(sma20, ev_idx)
            sma50_v = _fget(sma50, ev_idx)
            sma200_v = _fget(sma200, ev_idx)

            cvs20 = ((signal_close - sma20_v) / ev_atr) if (not math.isnan(sma20_v) and ev_atr > 0) else float("nan")
            cvs50 = ((signal_close - sma50_v) / ev_atr) if (not math.isnan(sma50_v) and ev_atr > 0) else float("nan")
            cvs200 = ((signal_close - sma200_v) / ev_atr) if (not math.isnan(sma200_v) and ev_atr > 0) else float("nan")

            vol_mean_v = _fget(vol_mean, ev_idx)
            vol_std_v = _fget(vol_std, ev_idx)
            volume_z_20 = ((signal_volume - vol_mean_v) / vol_std_v) if (not math.isnan(vol_mean_v) and not math.isnan(vol_std_v) and vol_std_v > 0) else float("nan")
            rvol_20 = (signal_volume / vol_mean_v) if (not math.isnan(vol_mean_v) and vol_mean_v > 0) else float("nan")

            entry_ts_iso = (
                bars["ts"][ev_idx + 1].replace(tzinfo=timezone.utc).isoformat()
                if hasattr(bars["ts"][ev_idx + 1], "tzinfo") and bars["ts"][ev_idx + 1].tzinfo is None
                else bars["ts"][ev_idx + 1].isoformat()
            )

            cand_rows.append({
                "candidate_id": candidate_id,
                "family_id": FAMILY_ID,
                "variant_id": variant_id,
                "dataset_version": DATASET_VERSION,
                "symbol": symbol,
                "timeframe": TIMEFRAME,
                "signal_bar_start_ts": ev_ts.isoformat(),
                "entry_bar_start_ts": entry_ts_iso,
                "signal_bar_start_ts_epoch": ev_ts_epoch,
                "entry_bar_start_ts_epoch": entry_ts_epoch,
                "macd_fast_n": fast_n,
                "macd_slow_n": slow_n,
                "macd_signal_n": signal_n,
                "signal_open": signal_open,
                "signal_high": signal_high,
                "signal_low": signal_low,
                "signal_close": signal_close,
                "signal_volume": signal_volume,
                "next_open": next_open,
                "next_high": next_high,
                "next_low": next_low,
                "next_close": next_close,
                "next_volume": next_volume,
                "gap_signal_close_to_next_open_pct": gap_pct,
                "macd_line": float(line[ev_idx]),
                "macd_signal": float(sig[ev_idx]),
                "macd_histogram": float(hist[ev_idx]),
                "macd_line_prev_1": float(line[ev_idx - 1]),
                "macd_signal_prev_1": float(sig[ev_idx - 1]),
                "macd_histogram_prev_1": float(hist[ev_idx - 1]),
                "bull_cross_up": True,
                "zero_cross_up": bool(zero_cross_line[ev_idx]),
                "bars_since_macd_below_zero": int(bars_since_line[ev_idx]),
                "bars_since_histogram_below_zero": int(bars_since_hist[ev_idx]),
                "macd_line_min_3": float(line_mins[3][ev_idx]),
                "macd_line_min_5": float(line_mins[5][ev_idx]),
                "macd_line_min_8": float(line_mins[8][ev_idx]),
                "macd_line_min_12": float(line_mins[12][ev_idx]),
                "macd_histogram_min_3": float(hist_mins[3][ev_idx]),
                "macd_histogram_min_5": float(hist_mins[5][ev_idx]),
                "macd_histogram_min_8": float(hist_mins[8][ev_idx]),
                "macd_histogram_min_12": float(hist_mins[12][ev_idx]),
                "atr_14": ev_atr,
                "rsi_14": rsi_14,
                "rsi_prev_1": rsi_prev_1,
                "rsi_slope_1": rsi_slope_1,
                "volume_z_20": volume_z_20,
                "rvol_20": rvol_20,
                "sma_20": sma20_v,
                "sma_50": sma50_v,
                "sma_200": sma200_v,
                "close_vs_sma20_atr": cvs20,
                "close_vs_sma50_atr": cvs50,
                "close_vs_sma200_atr": cvs200,
                "entry_assumption": "NEXT_OPEN",
                "entry_reference_price": next_open,
                "atr_at_signal": ev_atr,
                "bars_forward_available": int(n - 1 - ev_idx),
            })

            for stop_mult in STOP_ATR_MULT_VALUES:
                stop_price = next_open - stop_mult * ev_atr
                if stop_price <= 0:
                    continue
                for ts_bars in TIME_STOP_BARS_VALUES:
                    trade = walk_event_long(bars, ev_idx, stop_price, ts_bars)
                    if trade is None:
                        continue
                    walk_rows.append({
                        "candidate_id": candidate_id,
                        "variant_id": variant_id,
                        "exit_profile_id": f"stop{stop_mult}_ts{ts_bars}",
                        "stop_atr_mult": stop_mult,
                        "time_stop_bars": ts_bars,
                        "entry_ts": trade["entry_ts"].isoformat(),
                        "entry_ts_epoch": int(trade["entry_ts"].timestamp()),
                        "entry_price": trade["entry_price"],
                        "stop_price": trade["stop_price"],
                        "r_value": trade["r_value"],
                        "exit_ts": trade["exit_ts"].isoformat(),
                        "exit_ts_epoch": int(trade["exit_ts"].timestamp()),
                        "exit_price": trade["exit_price"],
                        "exit_reason": trade["exit_reason"],
                        "bars_held": trade["bars_held"],
                        "pnl_net": trade["pnl_net"],
                        "r_multiple": trade["r_multiple"],
                    })

    return cand_rows, walk_rows


# ─────────────────────────────── Worker ────────────────────────────────
def _worker(symbols_shard: list) -> Tuple[list, list, int, int]:
    pid = os.getpid()
    t0 = time.time()
    db_url = os.getenv("DATABASE_URL", "")
    all_cands: list = []
    all_walks: list = []
    processed = 0
    skipped = 0
    with psycopg.connect(db_url) as conn:
        for sym in symbols_shard:
            try:
                cands, walks = process_symbol(conn, sym)
            except Exception as e:
                print(f"[worker pid={pid}] {sym} ERROR: {e}", flush=True)
                skipped += 1
                continue
            if not cands:
                skipped += 1
                continue
            all_cands.extend(cands)
            all_walks.extend(walks)
            processed += 1
            if processed % 20 == 0:
                print(
                    f"[worker pid={pid}] {processed}/{len(symbols_shard)} "
                    f"cands={len(all_cands)} walks={len(all_walks)} "
                    f"elapsed={time.time()-t0:.1f}s",
                    flush=True,
                )
    print(
        f"[worker pid={pid}] DONE processed={processed} skipped={skipped} "
        f"cands={len(all_cands)} walks={len(all_walks)} "
        f"elapsed={time.time()-t0:.1f}s",
        flush=True,
    )
    return all_cands, all_walks, processed, skipped


# ─────────────────────────────── Writers ────────────────────────────────
CANDIDATE_FIELDS = [
    "candidate_id", "family_id", "variant_id", "dataset_version",
    "symbol", "timeframe", "signal_bar_start_ts", "entry_bar_start_ts",
    "signal_bar_start_ts_epoch", "entry_bar_start_ts_epoch",
    "macd_fast_n", "macd_slow_n", "macd_signal_n",
    "signal_open", "signal_high", "signal_low", "signal_close", "signal_volume",
    "next_open", "next_high", "next_low", "next_close", "next_volume",
    "gap_signal_close_to_next_open_pct",
    "macd_line", "macd_signal", "macd_histogram",
    "macd_line_prev_1", "macd_signal_prev_1", "macd_histogram_prev_1",
    "bull_cross_up", "zero_cross_up",
    "bars_since_macd_below_zero", "bars_since_histogram_below_zero",
    "macd_line_min_3", "macd_line_min_5", "macd_line_min_8", "macd_line_min_12",
    "macd_histogram_min_3", "macd_histogram_min_5", "macd_histogram_min_8", "macd_histogram_min_12",
    "atr_14", "rsi_14", "rsi_prev_1", "rsi_slope_1",
    "volume_z_20", "rvol_20",
    "sma_20", "sma_50", "sma_200",
    "close_vs_sma20_atr", "close_vs_sma50_atr", "close_vs_sma200_atr",
    "entry_assumption", "entry_reference_price", "atr_at_signal",
    "bars_forward_available",
]

WALK_FIELDS = [
    "candidate_id", "variant_id", "exit_profile_id",
    "stop_atr_mult", "time_stop_bars",
    "entry_ts", "entry_ts_epoch", "entry_price", "stop_price", "r_value",
    "exit_ts", "exit_ts_epoch", "exit_price", "exit_reason", "bars_held",
    "pnl_net", "r_multiple",
]


def write_csv(rows, fields, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_candidates_npz(rows, path):
    if not rows:
        np.savez_compressed(path, candidate_id=np.array([], dtype="U80"))
        return
    arrs = {
        "candidate_id": np.array([r["candidate_id"] for r in rows], dtype="U80"),
        "variant_id": np.array([r["variant_id"] for r in rows], dtype="U20"),
        "symbol": np.array([r["symbol"] for r in rows], dtype="U20"),
        "signal_bar_start_ts_epoch": np.array([r["signal_bar_start_ts_epoch"] for r in rows], dtype=np.int64),
        "macd_line": np.array([r["macd_line"] for r in rows], dtype=np.float64),
        "macd_histogram": np.array([r["macd_histogram"] for r in rows], dtype=np.float64),
        "bars_since_macd_below_zero": np.array([r["bars_since_macd_below_zero"] for r in rows], dtype=np.int32),
        "macd_line_min_3": np.array([r["macd_line_min_3"] for r in rows], dtype=np.float64),
        "macd_line_min_5": np.array([r["macd_line_min_5"] for r in rows], dtype=np.float64),
        "macd_line_min_8": np.array([r["macd_line_min_8"] for r in rows], dtype=np.float64),
        "macd_line_min_12": np.array([r["macd_line_min_12"] for r in rows], dtype=np.float64),
        "macd_histogram_min_3": np.array([r["macd_histogram_min_3"] for r in rows], dtype=np.float64),
        "macd_histogram_min_5": np.array([r["macd_histogram_min_5"] for r in rows], dtype=np.float64),
        "macd_histogram_min_8": np.array([r["macd_histogram_min_8"] for r in rows], dtype=np.float64),
        "macd_histogram_min_12": np.array([r["macd_histogram_min_12"] for r in rows], dtype=np.float64),
        "atr_14": np.array([r["atr_14"] for r in rows], dtype=np.float64),
    }
    np.savez_compressed(path, **arrs)


def write_walks_npz(rows, path):
    if not rows:
        np.savez_compressed(path, candidate_id=np.array([], dtype="U80"))
        return
    arrs = {
        "candidate_id": np.array([r["candidate_id"] for r in rows], dtype="U80"),
        "variant_id": np.array([r["variant_id"] for r in rows], dtype="U20"),
        "stop_atr_mult": np.array([r["stop_atr_mult"] for r in rows], dtype=np.float32),
        "time_stop_bars": np.array([r["time_stop_bars"] for r in rows], dtype=np.int32),
        "entry_ts_epoch": np.array([r["entry_ts_epoch"] for r in rows], dtype=np.int64),
        "exit_reason": np.array([r["exit_reason"] for r in rows], dtype="U20"),
        "pnl_net": np.array([r["pnl_net"] for r in rows], dtype=np.float64),
        "r_multiple": np.array([r["r_multiple"] for r in rows], dtype=np.float64),
    }
    np.savez_compressed(path, **arrs)


# ─────────────────────────────── Main ────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"[main] OUTPUT_DIR = {OUTPUT_DIR}", flush=True)
    print(f"[main] timeframe={TIMEFRAME} period={BATCH_START.date()}→{BATCH_END.date()}", flush=True)
    print(f"[main] MACD tuples: {[t[0] for t in MACD_TUPLES]}", flush=True)
    print(f"[main] N_WORKERS={N_WORKERS}", flush=True)

    t0 = time.time()
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        print("[main] ERROR: DATABASE_URL not set", file=sys.stderr, flush=True)
        sys.exit(1)

    with psycopg.connect(db_url) as conn:
        universe = load_universe(conn)
    n_symbols = len(universe)
    universe_hash = hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]
    print(f"[main] universe: {n_symbols} symbols (hash={universe_hash})", flush=True)

    rng = random.Random(42)
    shuffled = list(universe)
    rng.shuffle(shuffled)
    shards = [[] for _ in range(N_WORKERS)]
    for i, sym in enumerate(shuffled):
        shards[i % N_WORKERS].append(sym)
    for i, s in enumerate(shards):
        print(f"[main] shard {i}: {len(s)} symbols", flush=True)

    mp.set_start_method("fork", force=True)
    with mp.Pool(processes=N_WORKERS) as pool:
        results = pool.map(_worker, shards)

    all_cands: list = []
    all_walks: list = []
    total_processed = 0
    total_skipped = 0
    for cands, walks, processed, skipped in results:
        all_cands.extend(cands)
        all_walks.extend(walks)
        total_processed += processed
        total_skipped += skipped

    compute_elapsed = time.time() - t0
    print(
        f"[main] compute done in {compute_elapsed:.1f}s | "
        f"processed={total_processed}/{n_symbols} skipped={total_skipped} | "
        f"candidates={len(all_cands)} walks={len(all_walks)}",
        flush=True,
    )

    cand_csv = os.path.join(OUTPUT_DIR, "candidates.csv")
    cand_npz = os.path.join(OUTPUT_DIR, "candidates.npz")
    walks_csv = os.path.join(OUTPUT_DIR, "exit_walks.csv")
    walks_npz = os.path.join(OUTPUT_DIR, "exit_walks.npz")

    print(f"[main] writing {cand_csv}...", flush=True)
    write_csv(all_cands, CANDIDATE_FIELDS, cand_csv)
    print(f"[main] writing {cand_npz}...", flush=True)
    write_candidates_npz(all_cands, cand_npz)
    print(f"[main] writing {walks_csv}...", flush=True)
    write_csv(all_walks, WALK_FIELDS, walks_csv)
    print(f"[main] writing {walks_npz}...", flush=True)
    write_walks_npz(all_walks, walks_npz)

    per_tuple_counts: dict = defaultdict(int)
    for r in all_cands:
        per_tuple_counts[r["variant_id"]] += 1

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": TIMEFRAME,
        "period_start": BATCH_START.isoformat(),
        "period_end": BATCH_END.isoformat(),
        "warmup_days": WARMUP_BUFFER_DAYS,
        "universe_size": n_symbols,
        "universe_hash": universe_hash,
        "symbols_processed": total_processed,
        "symbols_skipped": total_skipped,
        "candidates_total": len(all_cands),
        "walks_total": len(all_walks),
        "per_tuple_candidate_counts": dict(per_tuple_counts),
        "macd_tuples": [
            {"variant_id": v, "fast": f, "slow": s, "signal": sg}
            for v, f, s, sg in MACD_TUPLES
        ],
        "stop_atr_mults": STOP_ATR_MULT_VALUES,
        "time_stop_bars": TIME_STOP_BARS_VALUES,
        "lookback_windows": LOOKBACK_WINDOWS,
        "slippage_bps": SLIPPAGE_BPS,
        "fee_taker_bps": FEE_TAKER_BPS,
        "position_size_usd": POSITION_SIZE_USD,
        "compute_elapsed_s": round(compute_elapsed, 1),
        "total_elapsed_s": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUTPUT_DIR, "generation_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(f"[main] summary: {json.dumps(summary, indent=2)}", flush=True)
    print(f"[main] DONE in {time.time()-t0:.1f}s", flush=True)
    print(f"[main] OUTPUT_DIR = {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
