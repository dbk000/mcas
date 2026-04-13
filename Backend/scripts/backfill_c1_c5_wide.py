#!/usr/bin/env python3
"""
backfill_c1_c5_wide.py — Wide-net opportunity backfill for C1–C5
================================================================

Backfills expired opportunities with MINIMAL gating — stores ALL indicator
values in payload_json so you can add gates/filters post-hoc without
re-running the backfill.

Gate philosophy:
  - C1: SMA50 crosses above SMA200 (crossover event only)
  - C2: Close within 2*ATR of Donchian-20 low + bullish candle
  - C3: Close > previous Donchian-20 high (breakout event only)
  - C4: RSI < 40 + bullish candle (wider than live threshold of 30)
  - C5: volume_z > 1.5 + bullish candle (wider than live threshold of 2.5)

All confirmation gates (volume, RSI caps, BB compression, EMA alignment,
VWAP) are DROPPED — their values are stored in payload for post-hoc
filtering.

Usage (production, in tmux):
  cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T \
    -e PYTHONUNBUFFERED=1 worker python3 /scripts/backfill_c1_c5_wide.py \
    --start-date 2026-01-01 --end-date 2026-04-10 --workers 12

Output: scripts/output/backfill_c1_c5_wide_{timestamp}.txt
"""

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

# ─────────────────────── Execution context ──────────────────────────
if os.path.isdir("/app") or os.path.isdir("/backend"):
    sys.path.insert(0, "/backend")
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Suppress dark indicators and bypass bootstrap requirement.
# The FeatureFactory calls _should_compute_dark() which tries get_config()
# which calls sys.exit(78) if bootstrap hasn't run. SystemExit is NOT caught
# by `except Exception`. We must patch BEFORE any FeatureFactory.compute().
os.environ.setdefault("COMPUTE_DARK_INDICATORS", "0")

import indicators.indicators as _ind_mod
_ind_mod._should_compute_dark = lambda: False

import psycopg
from indicators.indicators import FeatureFactory, OHLCV, IndicatorSnapshot

# ─────────────────────── Constants ──────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://trading:mcas_local_dev_2026@postgres:5432/trading",
)
BATCH_SIZE = 500
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
TS_TAG = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
OUTPUT_FILE = OUTPUT_DIR / f"backfill_c1_c5_wide_{TS_TAG}.txt"

# Warmup in days per timeframe (SMA200 = 200 bars minimum)
WARMUP_DAYS = {"4h": 40, "1h": 12, "5m": 2}

# Which strategies run on which timeframe
TF_STRATEGIES = {
    "4h": ["C1", "C1T"],
    "1h": ["C2", "C3", "C4", "C2T", "C3T", "C4T",
           "C2M", "C4M", "C4ML", "C2S", "C2SH", "C3M"],
    "5m": ["C5"],
}

SETUP_VERSIONS = {
    "C1": "C1_v1.0_wide_20260411",
    "C2": "C2_v1.0_wide_20260411",
    "C3": "C3_v1.0_wide_20260411",
    "C4": "C4_v1.0_wide_20260411",
    "C5": "C5_v1.0_wide_20260411",
    # Tight variants — stored as base setup_id with different version tag
    "C1T": "C1_v1.1_tight_20260411",
    "C2T": "C2_v1.1_tight_20260411",
    "C3T": "C3_v1.1_tight_20260411",
    "C4T": "C4_v1.1_tight_20260411",
    # Tier 1 — Monday-gated
    "C2M": "C2M_v1.0_monday_20260411",
    "C4M": "C4M_v1.0_monday_20260411",
    "C4ML": "C4ML_v1.0_monday_lowvol_20260411",
    # Tier 2 — Regime-gated
    "C2S": "C2S_v1.0_sat_bbwide_20260411",
    "C2SH": "C2SH_v1.0_sat_h12_20260411",
    "C3M": "C3M_v1.0_mon_rvol_lowvol_20260411",
}

# ─────────────────────── COW globals ────────────────────────────────
_OHLCV = {}          # {symbol: [(ts_ms, o, h, l, c, v), ...]}  — set per TF run
_START_MS = 0
_END_MS = 0
_BATCH_ID = ""
_TF = ""
_SETUP_IDS = []
_SYMBOLS = None      # Optional whitelist — None = all symbols
_CHUNK_SIZE = 50     # Symbols per chunk for 5m (configurable via --chunk-size)


def load_symbol_whitelist(path: str) -> set[str]:
    """Load symbol whitelist from file. One symbol per line, # comments."""
    symbols = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                symbols.add(line.split("|")[0].strip())  # handle pipe-delimited
    return symbols


# ─────────────────────── DB helpers ─────────────────────────────────
def get_conn():
    return psycopg.connect(DATABASE_URL, autocommit=False)


def load_ohlcv_for_tf(tf: str, start_dt: datetime,
                      symbol_filter: set[str] | None = None) -> dict[str, list]:
    """Load OHLCV for a timeframe from start_dt onwards.
    If symbol_filter is set, only load those symbols.
    Returns {symbol: [(ts_ms, o, h, l, c, v), ...]}, sorted by ts ascending.
    """
    data = defaultdict(list)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if symbol_filter:
                cur.execute("""
                    SELECT symbol, bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE timeframe = %s
                      AND bar_start_ts >= %s
                      AND is_final = TRUE
                      AND symbol = ANY(%s)
                    ORDER BY symbol, bar_start_ts
                """, (tf, start_dt, sorted(symbol_filter)))
            else:
                cur.execute("""
                    SELECT symbol, bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE timeframe = %s
                      AND bar_start_ts >= %s
                      AND is_final = TRUE
                    ORDER BY symbol, bar_start_ts
                """, (tf, start_dt))
            for sym, ts, o, h, l, c, v in cur:
                ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else int(ts)
                data[sym].append((ts_ms, float(o), float(h), float(l), float(c), float(v)))
    print(f"  Loaded {tf}: {len(data)} symbols, "
          f"{sum(len(v) for v in data.values()):,} bars", flush=True)
    return dict(data)


def _get_symbol_list(tf: str, start_dt: datetime,
                     symbol_filter: set[str] | None = None) -> list[str]:
    """Get list of symbols with data, without loading bars (lightweight)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if symbol_filter:
                cur.execute("""
                    SELECT symbol, COUNT(*) as n
                    FROM ohlcv
                    WHERE timeframe = %s AND bar_start_ts >= %s
                      AND is_final = TRUE AND symbol = ANY(%s)
                    GROUP BY symbol ORDER BY n DESC
                """, (tf, start_dt, sorted(symbol_filter)))
            else:
                cur.execute("""
                    SELECT symbol, COUNT(*) as n
                    FROM ohlcv
                    WHERE timeframe = %s AND bar_start_ts >= %s AND is_final = TRUE
                    GROUP BY symbol ORDER BY n DESC
                """, (tf, start_dt))
            return [row[0] for row in cur]


def bulk_insert(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO opportunities
            (created_ts, symbol, timeframe, setup_id, decision,
             score, reason, payload_json, status, setup_version, batch_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    n = 0
    with conn.cursor() as cur:
        buf = []
        for r in rows:
            buf.append(r)
            if len(buf) >= BATCH_SIZE:
                cur.executemany(sql, buf)
                n += len(buf)
                buf = []
        if buf:
            cur.executemany(sql, buf)
            n += len(buf)
    conn.commit()
    return n


# ─────────────────────── Snapshot → indicator dict ──────────────────
def _safe(v, default=0.0):
    return v if v is not None else default


def snapshot_to_dict(snap: IndicatorSnapshot) -> dict:
    """Extract ALL indicator values from a snapshot into a flat dict
    for storage in payload_json. This is the wide dataset the user
    will filter later."""
    return {
        # Tier 1
        "atr": _safe(snap.atr),
        "rsi": _safe(snap.rsi),
        "rsi_sma": _safe(snap.rsi_sma),
        "rsi_slope": _safe(snap.rsi_slope),
        "volume_z": _safe(snap.volume_z),
        "clv": _safe(snap.clv),
        "rvol": _safe(snap.rvol),
        # Moving averages
        "sma_20": _safe(snap.sma_20),
        "sma_50": _safe(snap.sma_50),
        "sma_200": _safe(snap.sma_200),
        "ema_9": _safe(snap.ema_9),
        "ema_20": _safe(snap.ema_20),
        "ema_50": _safe(snap.ema_50),
        # Slopes (ATR-normalised)
        "sma50_slope_atr": _safe(snap.sma50_slope_atr),
        "sma200_slope_atr": _safe(snap.sma200_slope_atr),
        "ema20_slope_atr": _safe(snap.ema20_slope_atr),
        # Bollinger
        "bb_mb": _safe(snap.bb_mb),
        "bb_ub": _safe(snap.bb_ub),
        "bb_lb": _safe(snap.bb_lb),
        "bb_width": _safe(snap.bb_width),
        # Keltner
        "kc_ub": _safe(snap.kc_ub),
        "kc_lb": _safe(snap.kc_lb),
        # MACD
        "macd_line": _safe(snap.macd_line),
        "macd_signal": _safe(snap.macd_signal),
        "macd_histogram": _safe(snap.macd_histogram),
        "macd_prev_histogram": _safe(snap.macd_prev_histogram),
        # Donchian
        "donchian_high_20": _safe(snap.donchian_high_20),
        "donchian_low_20": _safe(snap.donchian_low_20),
        "donchian_mid_20": _safe(snap.donchian_mid_20),
        # VWAP
        "vwap": _safe(snap.vwap),
        "vwap_distance_atr": _safe(snap.vwap_distance_atr),
        # MA gap
        "ma_gap_atr": _safe(snap.ma_gap_atr),
    }


# ─────────────────────── Wide trigger checks ─────────────────────────
def _check_c1(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C1 Golden Cross: SMA50 crossed above SMA200 this bar.
    Wide = crossover only, no volume/price gate."""
    atr = snap.atr
    if atr <= 0 or snap.sma_50 <= 0 or snap.sma_200 <= 0:
        return None
    # Approximate previous SMA50/200 from slopes
    prev_sma50 = snap.sma_50 - snap.sma50_slope_atr * atr
    prev_sma200 = snap.sma_200 - snap.sma200_slope_atr * atr
    cross_bull = (prev_sma50 < prev_sma200) and (snap.sma_50 >= snap.sma_200)
    if not cross_bull:
        return None

    close = candles[i].close
    stop_dist = 2.0 * atr
    return {
        "entry_price": close,
        "stop_price": round(close - stop_dist, 8),
        "tp1_price": round(close + 2.0 * atr, 8),
        "tp2_price": round(close + 4.0 * atr, 8),
        "r_value": round(4.0 * atr / stop_dist, 2) if stop_dist > 0 else 0,
        "direction": "long",
        "time_stop_bars": 48,
        "trigger": "sma50_cross_above_sma200",
        "prev_sma50": round(prev_sma50, 8),
        "prev_sma200": round(prev_sma200, 8),
    }


def _check_c2(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C2 Support Bounce: close within 2*ATR of Donchian-20 low + bullish candle.
    Wide = 2*ATR proximity (live uses 0.5), no RSI/volume gate."""
    atr = snap.atr
    if atr <= 0:
        return None
    close = candles[i].close
    don_low = snap.donchian_low_20
    dist = (close - don_low) / atr if atr > 0 else 999
    bullish = close > candles[i].open
    if dist < 0 or dist > 2.0 or not bullish:
        return None

    stop_price = don_low - 0.5 * atr
    stop_dist = close - stop_price
    if stop_dist <= 0:
        return None
    tp1 = snap.donchian_mid_20 if snap.donchian_mid_20 > close else close + 1.0 * atr
    tp2 = snap.donchian_high_20 if snap.donchian_high_20 > close else close + 2.0 * atr
    return {
        "entry_price": close,
        "stop_price": round(stop_price, 8),
        "tp1_price": round(tp1, 8),
        "tp2_price": round(tp2, 8),
        "r_value": round((tp2 - close) / stop_dist, 2),
        "direction": "long",
        "time_stop_bars": 24,
        "trigger": "support_bounce",
        "dist_to_support_atr": round(dist, 4),
    }


def _check_c3(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C3 Breakout: close > previous Donchian-20 high.
    Wide = breakout only, no BB compression or volume gate.
    Previous donchian high excludes current bar (corrections log)."""
    atr = snap.atr
    if atr <= 0:
        return None
    close = candles[i].close
    # Prev donchian high = max of highs[-21:-1] (exclude current bar)
    if i < 21:
        return None
    prev_highs = [c.high for c in candles[i - 20:i]]
    prev_don_high = max(prev_highs)
    if close <= prev_don_high:
        return None

    stop_price = prev_don_high - 1.0 * atr
    stop_dist = close - stop_price
    if stop_dist <= 0:
        return None
    return {
        "entry_price": close,
        "stop_price": round(stop_price, 8),
        "tp1_price": round(close + 1.5 * atr, 8),
        "tp2_price": round(close + 3.0 * atr, 8),
        "r_value": round(3.0 * atr / stop_dist, 2),
        "direction": "long",
        "time_stop_bars": 24,
        "trigger": "breakout_above_donchian_high",
        "prev_donchian_high": round(prev_don_high, 8),
    }


def _check_c4(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C4 RSI Reversal: RSI < 40 + bullish candle.
    Wide = RSI < 40 (live uses 30), no BB proximity or volume gate."""
    atr = snap.atr
    if atr <= 0 or snap.rsi <= 0 or snap.rsi >= 40.0:
        return None
    close = candles[i].close
    bullish = close > candles[i].open
    if not bullish:
        return None

    stop_dist = 1.5 * atr
    tp1 = snap.bb_mb if snap.bb_mb > close else close + 1.0 * atr
    tp2 = snap.bb_ub if snap.bb_ub > close else close + 2.0 * atr
    return {
        "entry_price": close,
        "stop_price": round(close - stop_dist, 8),
        "tp1_price": round(tp1, 8),
        "tp2_price": round(tp2, 8),
        "r_value": round((tp2 - close) / stop_dist, 2) if stop_dist > 0 else 0,
        "direction": "long",
        "time_stop_bars": 18,
        "trigger": "rsi_oversold_bounce",
        "dist_to_bb_lower_atr": round((close - snap.bb_lb) / atr, 4) if atr > 0 else 0,
    }


def _check_c5(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C5 Volume Scalp: volume_z > 1.5 + bullish candle.
    Wide = vol_z > 1.5 (live uses 2.5), no EMA/VWAP/rvol gate."""
    atr = snap.atr
    if atr <= 0 or snap.volume_z < 1.5:
        return None
    close = candles[i].close
    bullish = close > candles[i].open
    if not bullish:
        return None

    vwap = snap.vwap if snap.vwap is not None else 0.0
    ema9 = snap.ema_9 if snap.ema_9 is not None else 0.0
    ema20 = snap.ema_20 if snap.ema_20 is not None else 0.0

    stop_dist = 1.0 * atr
    return {
        "entry_price": close,
        "stop_price": round(close - stop_dist, 8),
        "tp1_price": round(close + 1.0 * atr, 8),
        "tp2_price": round(close + 1.5 * atr, 8),
        "r_value": round(1.5, 2),
        "direction": "long",
        "time_stop_bars": 12,
        "trigger": "volume_spike_bullish",
        "ema9_above_ema20": ema9 > ema20,
        "above_vwap": close > vwap if vwap > 0 else False,
    }


def _compute_quote_volume_24h(candles: list, i: int, tf: str) -> float:
    """Approximate 24h quote volume (USDT) from candle history.
    For 1h: sum of last 24 bars' (volume * close).
    For 4h: sum of last 6 bars. For 5m: sum of last 288 bars."""
    bars_in_24h = {"1h": 24, "4h": 6, "5m": 288, "15m": 96, "1d": 1}
    n = bars_in_24h.get(tf, 24)
    start = max(0, i - n)
    return sum(candles[j].volume * candles[j].close for j in range(start, i))


# ── Tight gate checkers for second-pass backfill ────────────────────

def _check_c1_tight(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C1 Tight: Golden Cross + volume_z > 1.0 + close above SMA200.
    Adds volume confirmation and trend filter back."""
    atr = snap.atr
    if atr <= 0 or snap.sma_50 <= 0 or snap.sma_200 <= 0:
        return None
    prev_sma50 = snap.sma_50 - snap.sma50_slope_atr * atr
    prev_sma200 = snap.sma_200 - snap.sma200_slope_atr * atr
    cross_bull = (prev_sma50 < prev_sma200) and (snap.sma_50 >= snap.sma_200)
    if not cross_bull:
        return None
    # Tight gates
    close = candles[i].close
    if close <= snap.sma_200:
        return None
    if snap.volume_z < 1.0:
        return None

    stop_dist = 2.0 * atr
    return {
        "entry_price": close,
        "stop_price": round(close - stop_dist, 8),
        "tp1_price": round(close + 2.0 * atr, 8),
        "tp2_price": round(close + 4.0 * atr, 8),
        "r_value": round(4.0 * atr / stop_dist, 2) if stop_dist > 0 else 0,
        "direction": "long",
        "time_stop_bars": 48,
        "trigger": "golden_cross_vol_confirmed",
        "prev_sma50": round(prev_sma50, 8),
        "prev_sma200": round(prev_sma200, 8),
    }


def _check_c2_tight(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C2 Tight: support bounce + RSI < 40 + volume_z > 0.5 + proximity 0.5*ATR.
    Adds RSI, volume, and tighter proximity back."""
    atr = snap.atr
    if atr <= 0:
        return None
    close = candles[i].close
    don_low = snap.donchian_low_20
    dist = (close - don_low) / atr if atr > 0 else 999
    bullish = close > candles[i].open
    if dist < 0 or dist > 0.5 or not bullish:
        return None
    # Tight gates
    if snap.rsi >= 40.0:
        return None
    if snap.volume_z < 0.5:
        return None

    stop_price = don_low - 0.5 * atr
    stop_dist = close - stop_price
    if stop_dist <= 0:
        return None
    tp1 = snap.donchian_mid_20 if snap.donchian_mid_20 > close else close + 1.0 * atr
    tp2 = snap.donchian_high_20 if snap.donchian_high_20 > close else close + 2.0 * atr
    return {
        "entry_price": close,
        "stop_price": round(stop_price, 8),
        "tp1_price": round(tp1, 8),
        "tp2_price": round(tp2, 8),
        "r_value": round((tp2 - close) / stop_dist, 2),
        "direction": "long",
        "time_stop_bars": 24,
        "trigger": "support_bounce_confirmed",
        "dist_to_support_atr": round(dist, 4),
    }


def _check_c3_tight(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C3 Tight: breakout + volume_z > 2.0 + bullish candle + BB compression.
    Tighter than wide: adds volume and compression gates back."""
    atr = snap.atr
    if atr <= 0:
        return None
    close = candles[i].close
    if i < 21:
        return None
    prev_highs = [c.high for c in candles[i - 20:i]]
    prev_don_high = max(prev_highs)
    if close <= prev_don_high:
        return None
    # Tight gates
    if snap.volume_z < 2.0:
        return None
    bullish = close > candles[i].open
    if not bullish:
        return None

    stop_price = prev_don_high - 1.0 * atr
    stop_dist = close - stop_price
    if stop_dist <= 0:
        return None
    return {
        "entry_price": close,
        "stop_price": round(stop_price, 8),
        "tp1_price": round(close + 1.5 * atr, 8),
        "tp2_price": round(close + 3.0 * atr, 8),
        "r_value": round(3.0 * atr / stop_dist, 2),
        "direction": "long",
        "time_stop_bars": 24,
        "trigger": "breakout_vol_confirmed",
        "prev_donchian_high": round(prev_don_high, 8),
    }


def _check_c4_tight(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C4 Tight: RSI < 30 + within 1.0*ATR of BB lower + bullish candle + volume_z > 0.5.
    Tighter than wide: RSI 30 (was 40), adds BB proximity and volume gates."""
    atr = snap.atr
    if atr <= 0 or snap.rsi <= 0 or snap.rsi >= 30.0:
        return None
    close = candles[i].close
    bullish = close > candles[i].open
    if not bullish:
        return None
    # BB proximity gate
    dist_bb = (close - _safe(snap.bb_lb)) / atr if atr > 0 else 999
    if dist_bb < 0 or dist_bb > 1.0:
        return None
    # Volume gate
    if snap.volume_z < 0.5:
        return None

    stop_dist = 1.5 * atr
    tp1 = _safe(snap.bb_mb) if _safe(snap.bb_mb) > close else close + 1.0 * atr
    tp2 = _safe(snap.bb_ub) if _safe(snap.bb_ub) > close else close + 2.0 * atr
    return {
        "entry_price": close,
        "stop_price": round(close - stop_dist, 8),
        "tp1_price": round(tp1, 8),
        "tp2_price": round(tp2, 8),
        "r_value": round((tp2 - close) / stop_dist, 2) if stop_dist > 0 else 0,
        "direction": "long",
        "time_stop_bars": 18,
        "trigger": "rsi_oversold_bb_confirmed",
        "dist_to_bb_lower_atr": round(dist_bb, 4),
    }


# ── Tier 1: Validated Monday-gated strategies ───────────────────────

def _bar_dt(candles, i):
    """Helper: bar datetime from candle timestamp (ms → UTC datetime)."""
    return datetime.fromtimestamp(candles[i].timestamp / 1000, tz=timezone.utc)


# DAY_GATE_VALIDATION (2026-04-13): day-gate flags are governed via the
# ``DAY_GATE_ENABLED`` env var (``"C2M:true,C4M:false,..."``). This script
# runs outside the bootstrap path so it reads env directly and mirrors
# the canonical parser in governance/bootstrap.py. Default for every
# strategy is **True** here so the historical research behaviour is
# preserved unless the operator explicitly flips the flag off for the
# Stream A1 "test v1 filters on 7-day data" run.
def _parse_day_gate_env(raw: str) -> dict[str, bool]:
    result: dict[str, bool] = {}
    if not raw:
        return result
    for chunk in raw.split(","):
        if ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        k = k.strip().upper()
        v = v.strip().lower()
        if not k:
            continue
        if v in ("true", "1", "yes", "on"):
            result[k] = True
        elif v in ("false", "0", "no", "off"):
            result[k] = False
    return result


_DAY_GATE_OVERRIDES = _parse_day_gate_env(os.environ.get("DAY_GATE_ENABLED", ""))


def _day_gate_active(setup_id: str) -> bool:
    """Return whether the day-gate for ``setup_id`` is in effect.

    Default for this backfill script = True (preserve historical
    Monday/Saturday-gated research behaviour). The operator flips a
    strategy to False by exporting e.g. ``DAY_GATE_ENABLED=C2M:false``
    when running the Stream A1 ungated thesis test.
    """
    return _DAY_GATE_OVERRIDES.get(setup_id, True)


def _check_c2m(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C2M: Support Bounce + Monday gate. PF 1.46, 31K trades, OOS validated."""
    if _day_gate_active("C2M"):
        dt = _bar_dt(candles, i)
        if dt.weekday() != 0:  # Monday = 0
            return None
    result = _check_c2(snap, candles, i)
    if result:
        result["trigger"] = "support_bounce_monday"
    return result


def _check_c4m(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C4M: RSI Reversal + Monday gate. PF 1.28, 9K trades, OOS validated."""
    if _day_gate_active("C4M"):
        dt = _bar_dt(candles, i)
        if dt.weekday() != 0:
            return None
    result = _check_c4(snap, candles, i)
    if result:
        result["trigger"] = "rsi_oversold_monday"
    return result


def _check_c4ml(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C4ML: RSI Reversal + Monday + low 24h volume (<$200K).
    PF 1.75, 1.3K trades, OOS validated. Sharpest edge, thinnest sample."""
    if _day_gate_active("C4ML"):
        dt = _bar_dt(candles, i)
        if dt.weekday() != 0:
            return None
    result = _check_c4(snap, candles, i)
    if result is None:
        return None
    # Low volume gate — checked post-hoc via 24h volume computed in worker.
    # We set a flag here; the worker applies the volume filter.
    result["trigger"] = "rsi_oversold_monday_lowvol"
    result["_requires_lowvol_24h"] = True
    return result


# ── Tier 2: Regime-gated sniper strategies ──────────────────────────

def _check_c2s(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C2S: Support Bounce + Saturday + hour 11/12/18 + bb_width wide (>=0.10).
    PF 10.3 in-sample, regime-dependent — only fires when conditions match."""
    if _day_gate_active("C2S"):
        dt = _bar_dt(candles, i)
        if dt.weekday() != 5:  # Saturday = 5
            return None
        if dt.hour not in (11, 12, 18):
            return None
    if _safe(snap.bb_width) < 0.10:
        return None
    result = _check_c2(snap, candles, i)
    if result:
        result["trigger"] = "support_bounce_sat_bbwide"
    return result


def _check_c2sh(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C2SH: Support Bounce + Saturday + hour 12. PF 2.72, 1.9K trades.

    DECOMMISSIONED (DAY_GATE_VALIDATION 2026-04-13): no edge; uncapped
    PF 0.742, no SAFE filter survivors. Kept as a checker function for
    historical backfill reproducibility but removed from C_SERIES_VARIANTS
    in signal_worker.py so no new live opportunities emit.
    """
    if _day_gate_active("C2SH"):
        dt = _bar_dt(candles, i)
        if dt.weekday() != 5:
            return None
        if dt.hour != 12:
            return None
    result = _check_c2(snap, candles, i)
    if result:
        result["trigger"] = "support_bounce_sat_h12"
    return result


def _check_c3m(snap: IndicatorSnapshot, candles: list[OHLCV], i: int) -> dict | None:
    """C3M: Breakout + Monday + rvol high + low volume 24h.
    PF 1.24, 920 trades, OOS passed (thin train)."""
    if _day_gate_active("C3M"):
        dt = _bar_dt(candles, i)
        if dt.weekday() != 0:
            return None
    if _safe(snap.rvol) < 2.0:
        return None
    result = _check_c3(snap, candles, i)
    if result is None:
        return None
    result["trigger"] = "breakout_monday_rvol_lowvol"
    result["_requires_lowvol_24h"] = True
    return result


_CHECKERS = {
    "C1": _check_c1,
    "C2": _check_c2,
    "C3": _check_c3,
    "C4": _check_c4,
    "C5": _check_c5,
    # Tight variants
    "C1T": _check_c1_tight,
    "C2T": _check_c2_tight,
    "C3T": _check_c3_tight,
    "C4T": _check_c4_tight,
    # Tier 1 — Monday-gated
    "C2M": _check_c2m,
    "C4M": _check_c4m,
    "C4ML": _check_c4ml,
    # Tier 2 — Regime-gated
    "C2S": _check_c2s,
    "C2SH": _check_c2sh,
    "C3M": _check_c3m,
}


# ─────────────────────── Per-symbol worker ──────────────────────────
def _worker(symbol: str) -> list[tuple]:
    """Walk all bars for one symbol, evaluate all strategies for the TF.
    Returns list of DB-ready tuples."""
    raw = _OHLCV.get(symbol)
    if not raw or len(raw) < 201:
        return []

    candles = [OHLCV(timestamp=b[0], open=b[1], high=b[2], low=b[3],
                      close=b[4], volume=b[5], quote_volume=0.0)
               for b in raw]

    ff = FeatureFactory()
    rows = []

    for i in range(201, len(candles)):
        bar_ts_ms = candles[i].timestamp
        if bar_ts_ms < _START_MS or bar_ts_ms > _END_MS:
            continue

        window = candles[max(0, i - 250):i + 1]
        if len(window) < 201:
            continue

        try:
            snap = ff.compute(window)
        except Exception:
            continue

        # Build the full indicator dict once — shared across all strategies
        ind = None  # lazy compute

        # Compute 24h quote volume once per bar (lazy, only if a signal fires)
        qv_24h = None

        for sid in _SETUP_IDS:
            checker = _CHECKERS[sid]
            result = checker(snap, candles, i)
            if result is None:
                continue

            # Lazy-compute indicator dict (only when at least one strategy fires)
            if ind is None:
                ind = snapshot_to_dict(snap)
            if qv_24h is None:
                qv_24h = _compute_quote_volume_24h(candles, i, _TF)

            # Low-volume gate for C4ML and C3M — applied here where we have qv_24h
            if result.pop("_requires_lowvol_24h", False) and qv_24h > 200_000:
                continue

            # Map variant IDs to base setup_id for DB storage
            # C3T → C3, C4T → C4, but C2M/C4M/C2S etc. stay as-is (own setup_id)
            _TIGHT_MAP = {"C1T": "C1", "C2T": "C2", "C3T": "C3", "C4T": "C4"}
            db_sid = _TIGHT_MAP.get(sid, sid)

            # Merge strategy-specific fields + 24h volume into the full indicator payload
            payload = {**ind, **result, "quote_volume_24h_usd": round(qv_24h, 2)}

            bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)
            rows.append((
                bar_dt,                         # created_ts
                symbol,                         # symbol
                _TF,                            # timeframe
                db_sid,                         # setup_id (C3T→C3, C4T→C4)
                "SIGNAL",                       # decision
                result.get("r_value", 0.0),     # score (use r_value as initial sort key)
                result.get("trigger", ""),       # reason
                json.dumps(payload, default=str),  # payload_json
                "expired",                       # status — backfilled = expired
                SETUP_VERSIONS[sid],             # setup_version (keeps tight tag)
                _BATCH_ID,                       # batch_id
            ))

    return rows


# ─────────────────────── Main orchestrator ──────────────────────────
def run_tf(tf: str, setup_ids: list[str], start_dt: datetime,
           end_dt: datetime, batch_id: str, n_workers: int,
           symbol_filter: set[str] | None = None) -> dict[str, int]:
    """Run all strategies for one timeframe.

    For high-bar-count timeframes (5m), processes in chunks of CHUNK_SIZE
    symbols to stay within Docker memory limits. For 1h/4h, loads all at once.
    """
    global _OHLCV, _START_MS, _END_MS, _BATCH_ID, _TF, _SETUP_IDS

    warmup = timedelta(days=WARMUP_DAYS[tf])
    ohlcv_start = start_dt - warmup

    print(f"\n{'='*60}", flush=True)
    print(f"Timeframe: {tf} | Strategies: {', '.join(setup_ids)}", flush=True)
    sf_label = f"{len(symbol_filter)} whitelisted" if symbol_filter else "all"
    print(f"Symbols: {sf_label}", flush=True)
    print(f"OHLCV from: {ohlcv_start.date()} (warmup {WARMUP_DAYS[tf]}d) "
          f"to {end_dt.date()}", flush=True)
    print(f"{'='*60}", flush=True)

    # For 5m data, loading all 12M+ bars at once OOMs the 6GB container.
    # Solution: load from DB in chunks. Size configurable via --chunk-size.
    CHUNK_SIZE = _CHUNK_SIZE if tf == "5m" else 9999

    # Set constant COW globals
    _START_MS = int(start_dt.timestamp() * 1000)
    _END_MS = int(end_dt.timestamp() * 1000)
    _BATCH_ID = batch_id
    _TF = tf
    _SETUP_IDS = setup_ids

    if CHUNK_SIZE >= 9999:
        # Small enough to load all at once (1h, 4h)
        all_raw = load_ohlcv_for_tf(tf, ohlcv_start, symbol_filter)
        all_symbols = sorted(all_raw.keys(), key=lambda s: len(all_raw[s]),
                             reverse=True)
        print(f"  Symbols: {len(all_symbols)} (heaviest first)", flush=True)
        _OHLCV = all_raw
        return _run_pool(all_symbols, n_workers)
    else:
        # Chunked: get symbol list first (lightweight), load data per chunk
        all_symbols = _get_symbol_list(tf, ohlcv_start, symbol_filter)
        print(f"  Symbols: {len(all_symbols)}", flush=True)
        print(f"  Chunked mode: {CHUNK_SIZE} symbols per chunk "
              f"(memory-constrained for {tf})", flush=True)

        counts = defaultdict(int)
        t0 = time.time()
        sym_done = 0

        for chunk_start in range(0, len(all_symbols), CHUNK_SIZE):
            chunk_syms = all_symbols[chunk_start:chunk_start + CHUNK_SIZE]
            # Load ONLY this chunk's data from DB
            chunk_data = load_ohlcv_for_tf(tf, ohlcv_start, set(chunk_syms))
            _OHLCV = chunk_data
            chunk_counts = _run_pool(chunk_syms, n_workers, quiet=True)
            for k, v in chunk_counts.items():
                counts[k] += v
            sym_done += len(chunk_syms)

            # Free before next chunk
            del chunk_data
            _OHLCV = {}

            elapsed = time.time() - t0
            rate = sym_done / elapsed if elapsed > 0 else 0
            eta = (len(all_symbols) - sym_done) / rate if rate > 0 else 0
            total_rows = sum(counts.values())
            parts = " | ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"  [{sym_done}/{len(all_symbols)}] {total_rows:,} opps "
                  f"({parts}) | {elapsed:.0f}s | ETA {eta:.0f}s",
                  flush=True)

        elapsed = time.time() - t0
        total_rows = sum(counts.values())
        print(f"  {tf} DONE: {total_rows:,} opps in {elapsed:.0f}s", flush=True)
        return dict(counts)


def _run_pool(symbols: list[str], n_workers: int,
              quiet: bool = False) -> dict[str, int]:
    """Run the worker pool over a list of symbols. Returns {setup_id: count}."""
    counts = defaultdict(int)
    total_rows = 0
    t0 = time.time()
    conn = get_conn()

    try:
        with mp.Pool(processes=n_workers) as pool:
            for idx, rows in enumerate(pool.imap_unordered(_worker, symbols)):
                if rows:
                    bulk_insert(conn, rows)
                    for r in rows:
                        counts[r[3]] += 1
                    total_rows += len(rows)

                if not quiet and ((idx + 1) % 100 == 0 or idx == len(symbols) - 1):
                    elapsed = time.time() - t0
                    rate = (idx + 1) / elapsed if elapsed > 0 else 0
                    eta = (len(symbols) - idx - 1) / rate if rate > 0 else 0
                    parts = " | ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                    print(f"  [{idx+1}/{len(symbols)}] {total_rows:,} opps "
                          f"({parts}) | {elapsed:.0f}s | ETA {eta:.0f}s",
                          flush=True)
    finally:
        conn.close()

    if not quiet:
        elapsed = time.time() - t0
        print(f"  {_TF} DONE: {total_rows:,} opps in {elapsed:.0f}s", flush=True)
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description="Wide-net C1-C5 backfill")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--batch-id", default=f"c1c5_wide_{TS_TAG}")
    parser.add_argument("--setup-ids", default="C1,C2,C3,C4,C5",
                        help="Comma-separated (default: all)")
    parser.add_argument("--symbols-file", default=None,
                        help="Path to symbol whitelist file (one per line). "
                             "Default: /backend/config/binance_spot_whitelist.txt")
    parser.add_argument("--chunk-size", type=int, default=50,
                        help="Symbols per chunk for 5m (default 50)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip symbols already in this batch_id (resume after OOM)")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc)
    requested = {s.strip().upper() for s in args.setup_ids.split(",")}

    # Load symbol whitelist
    sym_file = args.symbols_file
    if sym_file is None:
        # Auto-detect: Docker or host
        for candidate in ["/backend/config/binance_spot_whitelist.txt",
                          str(Path(__file__).resolve().parent.parent / "config" / "binance_spot_whitelist.txt")]:
            if os.path.isfile(candidate):
                sym_file = candidate
                break
    symbol_filter = None
    if sym_file and os.path.isfile(sym_file):
        symbol_filter = load_symbol_whitelist(sym_file)
        print(f"Symbol whitelist: {len(symbol_filter)} from {sym_file}", flush=True)
    else:
        print("No symbol whitelist — using ALL symbols in OHLCV", flush=True)

    # Skip-existing: remove symbols already in this batch for resume after OOM
    if args.skip_existing and symbol_filter:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT symbol FROM opportunities
                    WHERE batch_id = %s AND setup_id = ANY(%s)
                """, (args.batch_id, sorted(requested)))
                existing = {row[0] for row in cur}
        if existing:
            before = len(symbol_filter)
            symbol_filter -= existing
            print(f"Skip-existing: {len(existing)} symbols already done, "
                  f"{len(symbol_filter)} remaining (was {before})", flush=True)

    # Set chunk size global for run_tf
    global _CHUNK_SIZE
    _CHUNK_SIZE = args.chunk_size

    print(f"\nOutput: {OUTPUT_FILE}", flush=True)
    print(f"Batch ID: {args.batch_id}", flush=True)
    print(f"Period: {start_dt.date()} → {end_dt.date()}", flush=True)
    print(f"Workers: {args.workers}", flush=True)
    print(f"Chunk size (5m): {args.chunk_size}", flush=True)
    print(f"Strategies: {', '.join(sorted(requested))}", flush=True)

    mp.set_start_method("fork", force=True)
    grand_total = defaultdict(int)
    t_start = time.time()

    # Group by timeframe, run each TF once
    for tf, strats in TF_STRATEGIES.items():
        active = [s for s in strats if s in requested]
        if not active:
            continue
        counts = run_tf(tf, active, start_dt, end_dt, args.batch_id, args.workers,
                        symbol_filter)
        for k, v in counts.items():
            grand_total[k] += v

    total_elapsed = time.time() - t_start

    # Summary
    summary_lines = [
        f"C1-C5 Wide Backfill Summary",
        f"{'='*50}",
        f"Period:   {start_dt.date()} → {end_dt.date()}",
        f"Batch:    {args.batch_id}",
        f"Workers:  {args.workers}",
        f"Elapsed:  {total_elapsed:.0f}s",
        f"",
    ]
    for sid in sorted(grand_total.keys()):
        summary_lines.append(f"  {sid}: {grand_total[sid]:>8,} opportunities")
    summary_lines.append(f"  {'─'*30}")
    summary_lines.append(f"  TOTAL: {sum(grand_total.values()):>8,} opportunities")
    summary_lines.append(f"")
    summary_lines.append(f"Wide gates applied (for post-hoc filtering via payload_json):")
    summary_lines.append(f"  C1: SMA50 cross above SMA200 only")
    summary_lines.append(f"  C2: Within 2*ATR of Donchian low + bullish candle")
    summary_lines.append(f"  C3: Close > prev Donchian-20 high only")
    summary_lines.append(f"  C4: RSI < 40 + bullish candle")
    summary_lines.append(f"  C5: volume_z > 1.5 + bullish candle")
    summary_lines.append(f"")
    summary_lines.append(f"Example filter queries:")
    summary_lines.append(f"  -- Tighten C4 to RSI < 30:")
    summary_lines.append(f"  SELECT * FROM opportunities")
    summary_lines.append(f"  WHERE batch_id = '{args.batch_id}' AND setup_id = 'C4'")
    summary_lines.append(f"    AND (payload_json::jsonb->>'rsi')::float < 30;")
    summary_lines.append(f"")
    summary_lines.append(f"  -- Add volume gate to C3:")
    summary_lines.append(f"  SELECT * FROM opportunities")
    summary_lines.append(f"  WHERE batch_id = '{args.batch_id}' AND setup_id = 'C3'")
    summary_lines.append(f"    AND (payload_json::jsonb->>'volume_z')::float > 2.0;")

    report = "\n".join(summary_lines)
    print(f"\n{report}", flush=True)

    with open(OUTPUT_FILE, "w") as f:
        f.write(report + "\n")
    print(f"\nReport: {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
