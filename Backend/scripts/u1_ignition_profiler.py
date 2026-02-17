#!/usr/bin/env python3
"""
U1 Ignition Profiler — Empirical Discovery of Pennycoin Ignition Events
========================================================================

Scans 110 days of OHLCV data across ~588 symbols to find every real ignition
event (+50% in 24h), extract pre-ignition features, build a control group,
run logistic regression, and output an empirical weight discovery report.

Phase 1 of the data-driven U1 rebuild. Output feeds into Phase 2 (v3 strategy)
and Phase 3 (production U1Evaluator).

Usage:
    tmux new -s u1-profile
    cd /home/darren/Desktop/MCAS/Backend
    docker compose -f docker-compose.yml run --rm -T worker \
      python3 /scripts/u1_ignition_profiler.py --days 110 --min-gain 0.50 --timeframe 1h
"""

import os
import sys
import time
import json
import math
import argparse
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

WARMUP_BARS = 250
BTC_SYMBOL = "BTCUSDT"

# Major symbols to exclude from pennycoin universe
MAJOR_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "LTCUSDT", "SHIBUSDT", "TRXUSDT", "ATOMUSDT",
}


# ─── Indicator helpers (vectorized, matching v3 patterns) ─────────────────────

def rolling_sma(data, n):
    length = len(data)
    result = np.full(length, np.nan)
    if length < n:
        return result
    cumsum = np.cumsum(data)
    cumsum = np.insert(cumsum, 0, 0)
    result[n-1:] = (cumsum[n:] - cumsum[:-n]) / n
    return result


def rolling_std(data, n):
    length = len(data)
    result = np.full(length, np.nan)
    if length < n:
        return result
    cumsum = np.cumsum(data)
    cumsum2 = np.cumsum(data**2)
    cumsum = np.insert(cumsum, 0, 0)
    cumsum2 = np.insert(cumsum2, 0, 0)
    window_sum = cumsum[n:] - cumsum[:-n]
    window_sum2 = cumsum2[n:] - cumsum2[:-n]
    mean = window_sum / n
    var = window_sum2 / n - mean**2
    var = np.maximum(var, 0)
    result[n-1:] = np.sqrt(var)
    return result


def rolling_ema(data, n):
    length = len(data)
    result = np.full(length, np.nan)
    if length < n:
        return result
    alpha = 2.0 / (n + 1)
    result[n-1] = np.mean(data[:n])
    for i in range(n, length):
        result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
    return result


def rolling_atr(highs, lows, closes, n=14):
    length = len(closes)
    prev_close = np.empty(length)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]
    hl = highs - lows
    hc = np.abs(highs - prev_close)
    lc = np.abs(lows - prev_close)
    tr = np.maximum(hl, np.maximum(hc, lc))
    tr[0] = highs[0] - lows[0]
    atr = np.zeros(length)
    cumtr = np.cumsum(tr)
    atr[n-1:] = (cumtr[n-1:] - np.concatenate([[0], cumtr[:-n]])) / n
    return atr


def rolling_volume_z(volumes, n=20):
    length = len(volumes)
    vz = np.zeros(length)
    mean = rolling_sma(volumes, n)
    std = rolling_std(volumes, n)
    valid = (std > 0) & ~np.isnan(std) & ~np.isnan(mean)
    vz[valid] = (volumes[valid] - mean[valid]) / std[valid]
    return vz


def rolling_linear_slope(data, n):
    """Linear regression slope over rolling window of n bars."""
    length = len(data)
    result = np.zeros(length)
    if length < n:
        return result
    x = np.arange(n, dtype=np.float64)
    x_mean = x.mean()
    x_var = np.sum((x - x_mean) ** 2)
    if x_var == 0:
        return result
    for i in range(n - 1, length):
        y = data[i - n + 1:i + 1]
        y_mean = y.mean()
        result[i] = np.sum((x - x_mean) * (y - y_mean)) / x_var
    return result


def rolling_percentile(data, n):
    """Percentile rank of current value vs last n values (0-100)."""
    length = len(data)
    result = np.full(length, 50.0)
    for i in range(n, length):
        window = data[i - n:i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) > 1:
            result[i] = np.sum(valid < data[i]) / (len(valid) - 1) * 100
    return result


def rolling_min(data, n):
    length = len(data)
    result = np.full(length, np.nan)
    for i in range(n - 1, length):
        result[i] = np.min(data[i - n + 1:i + 1])
    return result


def rolling_max(data, n):
    length = len(data)
    result = np.full(length, np.nan)
    for i in range(n - 1, length):
        result[i] = np.max(data[i - n + 1:i + 1])
    return result


# ─── Data loading ────────────────────────────────────────────────────────────

def load_ohlcv(database_url: str, days: int, timeframe: str) -> dict:
    """Load OHLCV for one timeframe. Returns dict[(symbol,tf)] -> numpy arrays."""
    start_ts = datetime.now(timezone.utc) - timedelta(days=days + 30)  # warmup
    end_ts = datetime.now(timezone.utc)
    result = {}

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s AND is_final = TRUE
            """, (timeframe, start_ts))
            symbols = [r[0] for r in cur.fetchall()]
            logger.info("Found %d symbols for %s", len(symbols), timeframe)

            loaded = 0
            for idx, sym in enumerate(symbols):
                cur.execute("""
                    SELECT DISTINCT ON (bar_start_ts) bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = %s
                      AND bar_start_ts >= %s AND bar_start_ts < %s AND is_final = TRUE
                    ORDER BY bar_start_ts,
                             CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END
                """, (sym, timeframe, start_ts, end_ts))
                rows = cur.fetchall()
                if len(rows) < WARMUP_BARS:
                    continue

                ts_arr = np.array([
                    r[0].replace(tzinfo=timezone.utc).timestamp() if r[0].tzinfo is None
                    else r[0].timestamp()
                    for r in rows
                ])
                result[(sym, timeframe)] = {
                    "ts": ts_arr,
                    "open": np.array([float(r[1]) for r in rows]),
                    "high": np.array([float(r[2]) for r in rows]),
                    "low": np.array([float(r[3]) for r in rows]),
                    "close": np.array([float(r[4]) for r in rows]),
                    "volume": np.array([float(r[5]) for r in rows]),
                }
                loaded += 1

                if (idx + 1) % 100 == 0:
                    logger.info("  Loaded %d/%d symbols", idx + 1, len(symbols))

            logger.info("Loaded %d symbols with %d+ bars", loaded, WARMUP_BARS)

    return result


# ─── 1A. Ignition Event Identification ───────────────────────────────────────

def find_ignition_events(ohlcv_data: dict, tf: str, min_gain: float,
                         merge_window: int = 48) -> list[dict]:
    """
    Find ignition events: bars where max(high[t+1:t+25]) / close[t] >= (1 + min_gain).

    Dedup: merge events for same symbol within merge_window bars.
    """
    events = []
    symbols = sorted(set(sym for (sym, tf_) in ohlcv_data if tf_ == tf))

    for sym_idx, sym in enumerate(symbols):
        key = (sym, tf)
        if key not in ohlcv_data:
            continue

        # Skip major symbols
        if sym in MAJOR_SYMBOLS:
            continue

        data = ohlcv_data[key]
        closes = data["close"]
        highs = data["high"]
        n = len(closes)

        sym_events = []
        for i in range(WARMUP_BARS, n - 25):
            reference = closes[i]
            if reference <= 0:
                continue

            forward_high = np.max(highs[i + 1:i + 25])
            gain = (forward_high - reference) / reference

            if gain >= min_gain:
                # Find the bar where peak occurred
                peak_offset = np.argmax(highs[i + 1:i + 25]) + 1
                sym_events.append({
                    "symbol": sym,
                    "bar_index": i,
                    "reference_price": float(reference),
                    "peak_price": float(forward_high),
                    "gain_pct": float(gain * 100),
                    "peak_bar_offset": int(peak_offset),
                    "ts": float(data["ts"][i]),
                })

        # Merge events within merge_window bars (same move)
        merged = []
        for evt in sym_events:
            if merged and evt["bar_index"] - merged[-1]["bar_index"] < merge_window:
                # Keep the earlier event (higher gain if same move typically)
                if evt["gain_pct"] > merged[-1]["gain_pct"]:
                    merged[-1] = evt  # Replace if this one has higher gain
            else:
                merged.append(evt)

        events.extend(merged)

        if (sym_idx + 1) % 100 == 0:
            logger.info("  Event scan: %d/%d symbols, %d events so far",
                        sym_idx + 1, len(symbols), len(events))

    logger.info("Found %d ignition events at %.0f%% threshold across %d symbols",
                len(events), min_gain * 100, len(symbols))
    return events


# ─── 1B. Pre-Ignition Feature Extraction ────────────────────────────────────

def compute_all_indicators(data: dict) -> dict:
    """Compute all indicators needed for feature extraction."""
    closes = data["close"]
    highs = data["high"]
    lows = data["low"]
    opens = data["open"]
    volumes = data["volume"]

    atr = rolling_atr(highs, lows, closes, 14)
    vol_z = rolling_volume_z(volumes, 20)
    sma_20 = rolling_sma(closes, 20)
    sma_50 = rolling_sma(closes, 50)
    ema_20 = rolling_ema(closes, 20)
    std_20 = rolling_std(closes, 20)

    # Bollinger Band width
    bb_upper = sma_20 + 2 * std_20
    bb_lower = sma_20 - 2 * std_20
    bb_width = np.where(sma_20 > 0, (bb_upper - bb_lower) / sma_20, 0)

    # Volume slope
    vol_slope_5 = rolling_linear_slope(volumes, 5)

    # BB width percentile
    bb_width_pctile_200 = rolling_percentile(bb_width, 200)

    # ATR percentile
    atr_pctile_50 = rolling_percentile(atr, 50)

    # BB width min over last 20 bars, then percentile vs 200
    bb_width_min_20 = rolling_min(bb_width, 20)

    # Returns
    returns_1 = np.zeros(len(closes))
    returns_1[1:] = (closes[1:] - closes[:-1]) / np.where(closes[:-1] != 0, closes[:-1], 1)

    # 12-bar return
    returns_12 = np.zeros(len(closes))
    returns_12[12:] = (closes[12:] - closes[:-12]) / np.where(closes[:-12] != 0, closes[:-12], 1)

    # Body ratio and CLV
    bar_range = highs - lows
    body = np.abs(closes - opens)
    body_ratio = np.where(bar_range > 0, body / bar_range, 0)
    clv = np.where(bar_range > 0, (closes - lows) / bar_range, 0.5)
    range_pct = np.where(closes > 0, bar_range / closes, 0)

    # Is bullish
    is_bullish = (closes > opens).astype(float)

    # Bars since 52-bar low
    low_52 = rolling_min(lows, 52)
    bars_since_low_52 = np.zeros(len(closes))
    for i in range(52, len(closes)):
        for lookback in range(52):
            if lows[i - lookback] <= low_52[i] + 1e-12:
                bars_since_low_52[i] = lookback
                break

    return {
        "atr": atr, "vol_z": vol_z, "sma_20": sma_20, "sma_50": sma_50,
        "ema_20": ema_20, "std_20": std_20, "bb_width": bb_width,
        "bb_width_pctile_200": bb_width_pctile_200,
        "bb_width_min_20": bb_width_min_20,
        "atr_pctile_50": atr_pctile_50,
        "vol_slope_5": vol_slope_5,
        "returns_1": returns_1, "returns_12": returns_12,
        "body_ratio": body_ratio, "clv": clv, "range_pct": range_pct,
        "is_bullish": is_bullish, "bars_since_low_52": bars_since_low_52,
    }


def extract_features_at_bar(data: dict, ind: dict, i: int, btc_ind: dict = None,
                            btc_data: dict = None, universe_temp: dict = None,
                            ts: float = 0.0) -> dict:
    """Extract all features at a specific bar index."""
    closes = data["close"]
    lows = data["low"]
    n = len(closes)

    if i < WARMUP_BARS or i >= n:
        return {}

    atr_val = ind["atr"][i]
    if atr_val <= 0 or np.isnan(atr_val):
        atr_val = 1e-8  # prevent division by zero

    features = {}

    # Volume features
    features["vol_z"] = float(ind["vol_z"][i])
    vz_6 = ind["vol_z"][max(0, i - 5):i + 1]
    features["vol_z_max_6"] = float(np.max(vz_6)) if len(vz_6) > 0 else 0.0
    features["vol_slope_5"] = float(ind["vol_slope_5"][i])
    # Volume acceleration
    if i >= 2:
        prev_slope = ind["vol_slope_5"][i - 1] if i - 1 >= 0 else 0
        features["vol_accel"] = float(ind["vol_slope_5"][i] - prev_slope)
    else:
        features["vol_accel"] = 0.0

    # Volatility features
    features["bb_width"] = float(ind["bb_width"][i])
    features["bb_width_pctile_200"] = float(ind["bb_width_pctile_200"][i])
    bb_min_20 = ind["bb_width_min_20"][i]
    features["bb_width_min_20_pctile"] = float(
        rolling_percentile(ind["bb_width"][:i + 1], min(200, i + 1))[-1]
    ) if not np.isnan(bb_min_20) else 50.0
    features["atr_pctile_50"] = float(ind["atr_pctile_50"][i])
    features["range_pct"] = float(ind["range_pct"][i])

    # Price structure features
    ema20_val = ind["ema_20"][i]
    sma50_val = ind["sma_50"][i]
    if not np.isnan(ema20_val):
        features["close_vs_ema20"] = float((closes[i] - ema20_val) / atr_val)
    else:
        features["close_vs_ema20"] = 0.0
    if not np.isnan(sma50_val):
        features["close_vs_sma50"] = float((closes[i] - sma50_val) / atr_val)
    else:
        features["close_vs_sma50"] = 0.0
    features["clv"] = float(ind["clv"][i])
    features["body_ratio"] = float(ind["body_ratio"][i])
    features["is_bullish"] = float(ind["is_bullish"][i])

    # Higher lows count (last 3 pivot lows)
    hl_count = 0
    if i >= 10:
        recent_lows = lows[max(0, i - 20):i + 1]
        pivot_lows = []
        for k in range(2, len(recent_lows) - 2):
            if recent_lows[k] <= recent_lows[k - 1] and recent_lows[k] <= recent_lows[k - 2] \
               and recent_lows[k] <= recent_lows[k + 1] and recent_lows[k] <= recent_lows[k + 2]:
                pivot_lows.append(recent_lows[k])
        if len(pivot_lows) >= 2:
            for k in range(1, min(4, len(pivot_lows))):
                if pivot_lows[-k] > pivot_lows[-k - 1]:
                    hl_count += 1
    features["higher_lows_3"] = float(hl_count)

    features["bars_since_52bar_low"] = float(ind["bars_since_low_52"][i])
    features["return_12"] = float(ind["returns_12"][i])

    # BTC context features
    if btc_ind is not None and btc_data is not None:
        btc_closes = btc_data["close"]
        # Find closest BTC bar to our timestamp
        btc_ts = btc_data["ts"]
        btc_idx = np.searchsorted(btc_ts, ts, side='right') - 1
        btc_idx = max(12, min(btc_idx, len(btc_closes) - 1))

        features["btc_return_12"] = float(btc_ind["returns_12"][btc_idx])
        features["coin_vs_btc_12"] = float(ind["returns_12"][i] - btc_ind["returns_12"][btc_idx])
        features["btc_vol_z"] = float(btc_ind["vol_z"][btc_idx])
    else:
        features["btc_return_12"] = 0.0
        features["coin_vs_btc_12"] = 0.0
        features["btc_vol_z"] = 0.0

    # Universe temperature features
    if universe_temp is not None:
        ts_key = float(ts)
        temp = universe_temp.get(ts_key, {})
        features["n_coins_vol_z_gt2"] = float(temp.get("n_gt2", 0))
        features["n_coins_vol_z_gt3"] = float(temp.get("n_gt3", 0))
        features["n_coins_positive_4h"] = float(temp.get("n_positive_4h", 0))
        features["universe_vol_z_mean"] = float(temp.get("mean_vol_z", 0))
    else:
        features["n_coins_vol_z_gt2"] = 0.0
        features["n_coins_vol_z_gt3"] = 0.0
        features["n_coins_positive_4h"] = 0.0
        features["universe_vol_z_mean"] = 0.0

    return features


# ─── 1B+. Universe Temperature Computation ──────────────────────────────────

def compute_universe_temperature(ohlcv_data: dict, tf: str) -> dict:
    """
    Compute universe temperature at each timestamp.
    Returns dict[ts_float] -> {n_gt2, n_gt3, n_positive_4h, mean_vol_z}
    """
    logger.info("Computing universe temperature...")

    # Collect all timestamps and per-symbol vol_z + returns
    ts_vol_z = defaultdict(list)
    ts_returns_4 = defaultdict(list)

    symbols = sorted(set(sym for (sym, tf_) in ohlcv_data if tf_ == tf))
    for sym in symbols:
        key = (sym, tf)
        if key not in ohlcv_data or sym in MAJOR_SYMBOLS:
            continue
        data = ohlcv_data[key]
        closes = data["close"]
        volumes = data["volume"]
        ts_arr = data["ts"]

        vz = rolling_volume_z(volumes, 20)
        ret_4 = np.zeros(len(closes))
        ret_4[4:] = (closes[4:] - closes[:-4]) / np.where(closes[:-4] != 0, closes[:-4], 1)

        for i in range(WARMUP_BARS, len(closes)):
            t = float(ts_arr[i])
            ts_vol_z[t].append(vz[i])
            ts_returns_4[t].append(ret_4[i])

    # Aggregate per timestamp
    result = {}
    for t in sorted(ts_vol_z.keys()):
        vz_arr = np.array(ts_vol_z[t])
        ret_arr = np.array(ts_returns_4.get(t, [0]))
        result[t] = {
            "n_gt2": int(np.sum(vz_arr > 2.0)),
            "n_gt3": int(np.sum(vz_arr > 3.0)),
            "n_positive_4h": int(np.sum(ret_arr > 0)),
            "mean_vol_z": float(np.mean(vz_arr)),
        }

    logger.info("Universe temperature computed for %d timestamps", len(result))
    return result


# ─── 1C. Control Group ──────────────────────────────────────────────────────

def build_control_group(events: list[dict], ohlcv_data: dict, tf: str,
                        all_indicators: dict, btc_ind: dict, btc_data: dict,
                        universe_temp: dict, n_same_sym: int = 10,
                        n_other_sym: int = 10) -> list[dict]:
    """
    Build control group: for each event, sample non-ignition bars from
    same symbol and random other symbols.
    """
    rng = np.random.RandomState(42)
    controls = []

    # Collect non-major symbols
    penny_symbols = sorted(set(
        sym for (sym, tf_) in ohlcv_data
        if tf_ == tf and sym not in MAJOR_SYMBOLS
    ))
    event_symbols = {e["symbol"] for e in events}

    for evt_idx, evt in enumerate(events):
        sym = evt["symbol"]
        key = (sym, tf)
        if key not in ohlcv_data:
            continue
        data = ohlcv_data[key]
        ind = all_indicators.get(key)
        if ind is None:
            continue
        closes = data["close"]
        n = len(closes)
        trigger_bar = evt["bar_index"]

        # Same-symbol controls: random bars from same symbol with <10% gain
        candidate_bars = []
        for i in range(WARMUP_BARS, n - 25):
            # Skip bars near the ignition event
            if abs(i - trigger_bar) < 48:
                continue
            # Check forward gain < 10%
            reference = closes[i]
            if reference <= 0:
                continue
            forward_high = np.max(data["high"][i + 1:i + 25])
            gain = (forward_high - reference) / reference
            if gain < 0.10:
                candidate_bars.append(i)

        if candidate_bars:
            chosen = rng.choice(candidate_bars, size=min(n_same_sym, len(candidate_bars)),
                                replace=False)
            for bar_i in chosen:
                feats = extract_features_at_bar(
                    data, ind, bar_i, btc_ind, btc_data, universe_temp,
                    ts=data["ts"][bar_i]
                )
                if feats:
                    feats["label"] = 0
                    feats["control_type"] = "same_symbol"
                    feats["source_event_idx"] = evt_idx
                    controls.append(feats)

        # Other-symbol controls: bars from random other penny coins at same timestamp
        other_syms = [s for s in penny_symbols if s != sym]
        if other_syms:
            chosen_syms = rng.choice(other_syms, size=min(n_other_sym, len(other_syms)),
                                     replace=False)
            for other_sym in chosen_syms:
                other_key = (other_sym, tf)
                if other_key not in ohlcv_data:
                    continue
                other_data = ohlcv_data[other_key]
                other_ind = all_indicators.get(other_key)
                if other_ind is None:
                    continue
                # Find bar closest to event timestamp
                other_ts = other_data["ts"]
                bar_i = np.searchsorted(other_ts, evt["ts"], side='right') - 1
                bar_i = max(WARMUP_BARS, min(bar_i, len(other_data["close"]) - 26))

                # Verify this is indeed non-ignition
                ref = other_data["close"][bar_i]
                if ref <= 0 or bar_i + 25 >= len(other_data["close"]):
                    continue
                fwd_high = np.max(other_data["high"][bar_i + 1:bar_i + 25])
                if (fwd_high - ref) / ref >= 0.10:
                    continue

                feats = extract_features_at_bar(
                    other_data, other_ind, bar_i, btc_ind, btc_data,
                    universe_temp, ts=other_ts[bar_i]
                )
                if feats:
                    feats["label"] = 0
                    feats["control_type"] = "other_symbol"
                    feats["source_event_idx"] = evt_idx
                    controls.append(feats)

        if (evt_idx + 1) % 20 == 0:
            logger.info("  Control group: %d/%d events processed, %d controls",
                        evt_idx + 1, len(events), len(controls))

    logger.info("Built %d control samples for %d events", len(controls), len(events))
    return controls


# ─── 1D. Analysis & Weight Discovery ────────────────────────────────────────

def univariate_analysis(event_features: list[dict], control_features: list[dict],
                        feature_names: list[str]) -> dict:
    """
    For each feature, compute distribution stats for events vs controls.
    Welch t-test implemented in pure numpy.
    """
    results = {}
    for feat in feature_names:
        evt_vals = np.array([f.get(feat, 0) for f in event_features])
        ctrl_vals = np.array([f.get(feat, 0) for f in control_features])

        # Remove NaN/inf
        evt_vals = evt_vals[np.isfinite(evt_vals)]
        ctrl_vals = ctrl_vals[np.isfinite(ctrl_vals)]

        if len(evt_vals) < 3 or len(ctrl_vals) < 3:
            results[feat] = {
                "mean_event": 0, "mean_control": 0,
                "median_event": 0, "median_control": 0,
                "p_value": 1.0, "effect_size": 0,
            }
            continue

        mean_e, mean_c = float(np.mean(evt_vals)), float(np.mean(ctrl_vals))
        std_e, std_c = float(np.std(evt_vals, ddof=1)), float(np.std(ctrl_vals, ddof=1))
        n_e, n_c = len(evt_vals), len(ctrl_vals)

        # Welch's t-test
        se_e = std_e**2 / n_e if n_e > 0 else 1e-10
        se_c = std_c**2 / n_c if n_c > 0 else 1e-10
        se = math.sqrt(se_e + se_c)
        t_stat = (mean_e - mean_c) / se if se > 0 else 0

        # Approximate p-value using normal distribution (good enough for large n)
        # |t| -> p via 2 * Phi(-|t|) approximation
        abs_t = abs(t_stat)
        # Abramowitz & Stegun approximation for normal CDF
        p = 2.0 * _normal_cdf_complement(abs_t)

        # Effect size (Cohen's d)
        pooled_std = math.sqrt((std_e**2 + std_c**2) / 2) if (std_e + std_c) > 0 else 1
        cohen_d = (mean_e - mean_c) / pooled_std if pooled_std > 0 else 0

        results[feat] = {
            "mean_event": round(mean_e, 4),
            "mean_control": round(mean_c, 4),
            "median_event": round(float(np.median(evt_vals)), 4),
            "median_control": round(float(np.median(ctrl_vals)), 4),
            "std_event": round(std_e, 4),
            "std_control": round(std_c, 4),
            "t_stat": round(float(t_stat), 3),
            "p_value": round(p, 6),
            "effect_size": round(cohen_d, 3),
            "n_event": n_e,
            "n_control": n_c,
        }

    return results


def _normal_cdf_complement(x):
    """Approximation of 1 - Phi(x) for x >= 0 (Abramowitz & Stegun 26.2.17)."""
    if x < 0:
        return 1 - _normal_cdf_complement(-x)
    t = 1.0 / (1.0 + 0.2316419 * x)
    d = 0.3989422804014327  # 1/sqrt(2*pi)
    p = d * math.exp(-x * x / 2) * t * (
        0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))
    )
    return max(0.0, min(1.0, p))


def feature_correlation(features: list[dict], feature_names: list[str]) -> dict:
    """Compute correlation matrix and identify redundant features."""
    n_feats = len(feature_names)
    matrix = np.zeros((len(features), n_feats))
    for i, f in enumerate(features):
        for j, name in enumerate(feature_names):
            matrix[i, j] = f.get(name, 0)

    # Replace nan/inf
    matrix = np.nan_to_num(matrix, nan=0, posinf=0, neginf=0)

    # Correlation matrix
    if matrix.shape[0] < 3:
        return {"correlations": {}, "redundant_pairs": []}

    # Standardize
    means = matrix.mean(axis=0)
    stds = matrix.std(axis=0)
    stds[stds == 0] = 1
    standardized = (matrix - means) / stds

    corr = (standardized.T @ standardized) / (matrix.shape[0] - 1)

    # Find highly correlated pairs (|r| > 0.8)
    redundant = []
    for i in range(n_feats):
        for j in range(i + 1, n_feats):
            r = corr[i, j]
            if abs(r) > 0.8:
                redundant.append({
                    "feat1": feature_names[i],
                    "feat2": feature_names[j],
                    "correlation": round(float(r), 3),
                })

    return {
        "redundant_pairs": redundant,
    }


def logistic_regression_irls(X: np.ndarray, y: np.ndarray,
                             max_iter: int = 100, l2_lambda: float = 0.1,
                             tol: float = 1e-6) -> tuple[np.ndarray, list[float]]:
    """
    Logistic regression via iterative reweighted least squares.
    Pure numpy, no sklearn/scipy.

    Args:
        X: (n_samples, n_features) — should include intercept column
        y: (n_samples,) binary labels
        l2_lambda: L2 regularization strength
        tol: convergence tolerance

    Returns:
        (coefficients, loss_history)
    """
    n, p = X.shape
    beta = np.zeros(p)
    loss_history = []

    for iteration in range(max_iter):
        # Predicted probabilities
        z = X @ beta
        z = np.clip(z, -30, 30)  # prevent overflow
        mu = 1.0 / (1.0 + np.exp(-z))

        # Log loss
        eps = 1e-15
        loss = -np.mean(y * np.log(mu + eps) + (1 - y) * np.log(1 - mu + eps))
        loss += 0.5 * l2_lambda * np.sum(beta[1:] ** 2) / n  # L2 (skip intercept)
        loss_history.append(float(loss))

        # Check convergence
        if iteration > 0 and abs(loss_history[-1] - loss_history[-2]) < tol:
            logger.info("  Logistic regression converged at iteration %d (loss=%.6f)",
                        iteration, loss)
            break

        # Weights for IRLS
        w = mu * (1 - mu)
        w = np.maximum(w, 1e-10)  # prevent singularity

        # Weighted Hessian + L2 regularization
        W = np.diag(w)
        H = X.T @ W @ X
        # Add L2 to diagonal (skip intercept)
        reg = l2_lambda * np.eye(p)
        reg[0, 0] = 0  # don't regularize intercept
        H += reg

        # Gradient
        gradient = X.T @ (mu - y) + l2_lambda * np.concatenate([[0], beta[1:]])

        # Newton step
        try:
            delta = np.linalg.solve(H, gradient)
        except np.linalg.LinAlgError:
            # Fallback: use pseudo-inverse
            delta = np.linalg.lstsq(H, gradient, rcond=None)[0]

        beta -= delta

    return beta, loss_history


def run_logistic_regression(event_features: list[dict], control_features: list[dict],
                            feature_names: list[str]) -> dict:
    """Run logistic regression and return coefficients + model info."""
    # Build feature matrix
    all_features = event_features + control_features
    n = len(all_features)
    p = len(feature_names)

    X = np.zeros((n, p + 1))  # +1 for intercept
    X[:, 0] = 1  # intercept
    y = np.zeros(n)

    for i, f in enumerate(all_features):
        for j, name in enumerate(feature_names):
            X[i, j + 1] = f.get(name, 0)
        y[i] = f.get("label", 0)

    # Replace nan/inf
    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)

    # Standardize features (not intercept)
    means = X[:, 1:].mean(axis=0)
    stds = X[:, 1:].std(axis=0)
    stds[stds == 0] = 1
    X[:, 1:] = (X[:, 1:] - means) / stds

    logger.info("Running logistic regression: %d samples, %d features", n, p)

    beta, loss_history = logistic_regression_irls(X, y, max_iter=100, l2_lambda=0.1)

    # Predicted probabilities
    z = X @ beta
    z = np.clip(z, -30, 30)
    probs = 1.0 / (1.0 + np.exp(-z))

    # Coefficients mapped to feature names
    coefficients = {}
    for j, name in enumerate(feature_names):
        coefficients[name] = {
            "coeff": round(float(beta[j + 1]), 4),
            "abs_coeff": round(abs(float(beta[j + 1])), 4),
        }

    # Sort by absolute coefficient
    ranked = sorted(coefficients.items(), key=lambda x: -x[1]["abs_coeff"])

    # Normalize coefficients to recommended weights (sum to 1.0)
    total_abs = sum(v["abs_coeff"] for v in coefficients.values())
    recommended_weights = {}
    if total_abs > 0:
        for name, info in ranked:
            recommended_weights[name] = round(info["abs_coeff"] / total_abs, 4)

    return {
        "intercept": round(float(beta[0]), 4),
        "coefficients": {name: info for name, info in ranked},
        "recommended_weights": recommended_weights,
        "loss_history": loss_history[:10] + loss_history[-3:] if len(loss_history) > 13 else loss_history,
        "converged": len(loss_history) < 100,
        "feature_means": {name: round(float(means[j]), 4) for j, name in enumerate(feature_names)},
        "feature_stds": {name: round(float(stds[j]), 4) for j, name in enumerate(feature_names)},
        "n_events": int(sum(y)),
        "n_controls": int(len(y) - sum(y)),
    }


def threshold_sweep(event_features: list[dict], control_features: list[dict],
                    feature_names: list[str], beta: dict,
                    feature_means: dict, feature_stds: dict,
                    intercept: float) -> list[dict]:
    """Sweep score threshold and report precision/recall/F1."""
    all_features = event_features + control_features
    n = len(all_features)

    # Build standardized feature matrix
    X = np.zeros((n, len(feature_names) + 1))
    X[:, 0] = 1  # intercept
    y = np.zeros(n)

    for i, f in enumerate(all_features):
        for j, name in enumerate(feature_names):
            val = f.get(name, 0)
            mean = feature_means.get(name, 0)
            std = feature_stds.get(name, 1)
            X[i, j + 1] = (val - mean) / std if std > 0 else 0
        y[i] = f.get("label", 0)

    # Reconstruct beta vector
    beta_vec = np.zeros(len(feature_names) + 1)
    beta_vec[0] = intercept
    for j, name in enumerate(feature_names):
        beta_vec[j + 1] = beta.get(name, {}).get("coeff", 0)

    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)
    z = X @ beta_vec
    z = np.clip(z, -30, 30)
    probs = 1.0 / (1.0 + np.exp(-z))

    results = []
    for threshold in np.arange(0.30, 0.85, 0.05):
        predicted = probs >= threshold
        tp = np.sum(predicted & (y == 1))
        fp = np.sum(predicted & (y == 0))
        fn = np.sum(~predicted & (y == 1))

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        results.append({
            "threshold": round(float(threshold), 2),
            "n_signals": int(np.sum(predicted)),
            "precision": round(float(precision), 4),
            "recall": round(float(recall), 4),
            "f1": round(float(f1), 4),
        })

    return results


# ─── 1E. Clustering Analysis ────────────────────────────────────────────────

def clustering_analysis(events: list[dict], window_hours: float = 4.0,
                        tf_seconds: float = 3600.0) -> dict:
    """
    Check how many ignition events cluster within +/- window_hours.
    """
    window_bars = int(window_hours * 3600 / tf_seconds)
    n = len(events)
    if n == 0:
        return {"pct_clustered": 0, "avg_cluster_size": 0, "clusters": []}

    # Sort events by timestamp
    sorted_events = sorted(events, key=lambda e: e["ts"])

    clustered_count = 0
    cluster_sizes = []
    visited = set()

    for i, evt in enumerate(sorted_events):
        if i in visited:
            continue
        cluster = [i]
        visited.add(i)
        for j in range(i + 1, n):
            if j in visited:
                continue
            if sorted_events[j]["ts"] - evt["ts"] <= window_hours * 3600:
                if sorted_events[j]["symbol"] != evt["symbol"]:
                    cluster.append(j)
                    visited.add(j)
            else:
                break

        if len(cluster) > 1:
            clustered_count += len(cluster)
            cluster_sizes.append(len(cluster))

    pct_clustered = clustered_count / n if n > 0 else 0
    avg_cluster_size = float(np.mean(cluster_sizes)) if cluster_sizes else 0

    return {
        "pct_clustered": round(pct_clustered, 4),
        "avg_cluster_size": round(avg_cluster_size, 2),
        "n_clusters": len(cluster_sizes),
        "max_cluster_size": max(cluster_sizes) if cluster_sizes else 0,
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="U1 Ignition Profiler — Empirical Discovery")
    parser.add_argument("--days", type=int, default=110, help="Days of OHLCV to load")
    parser.add_argument("--min-gain", type=float, default=0.50, help="Minimum gain for ignition (0.50 = +50%%)")
    parser.add_argument("--timeframe", type=str, default="1h", help="Timeframe to analyze")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    t0 = time.time()
    tf = args.timeframe

    # ── 1. Load OHLCV ────────────────────────────────────────────────────
    logger.info("Loading %dd of OHLCV for %s...", args.days, tf)
    ohlcv_data = load_ohlcv(database_url, args.days, tf)
    if not ohlcv_data:
        logger.error("No data loaded")
        sys.exit(1)

    # ── 2. Compute indicators for all symbols ────────────────────────────
    logger.info("Computing indicators for %d symbol-tf combos...", len(ohlcv_data))
    all_indicators = {}
    for idx, (key, data) in enumerate(ohlcv_data.items()):
        all_indicators[key] = compute_all_indicators(data)
        if (idx + 1) % 100 == 0:
            logger.info("  Indicators: %d/%d", idx + 1, len(ohlcv_data))

    # Load BTC data
    btc_key = (BTC_SYMBOL, tf)
    btc_data = ohlcv_data.get(btc_key)
    btc_ind = all_indicators.get(btc_key)

    # ── 3. Compute universe temperature ──────────────────────────────────
    universe_temp = compute_universe_temperature(ohlcv_data, tf)

    # ── 4. Find ignition events at multiple thresholds ───────────────────
    thresholds = [0.50, 0.30, 0.20]
    event_counts = {}
    primary_events = None

    for gain_thresh in thresholds:
        events = find_ignition_events(ohlcv_data, tf, gain_thresh)
        event_counts[f"{int(gain_thresh * 100)}pct"] = len(events)
        if gain_thresh == args.min_gain:
            primary_events = events

    if primary_events is None:
        primary_events = find_ignition_events(ohlcv_data, tf, args.min_gain)

    logger.info("Event counts: %s", event_counts)

    if not primary_events:
        logger.warning("No ignition events found at %.0f%% threshold!", args.min_gain * 100)
        # Try lower threshold
        if args.min_gain > 0.20:
            logger.info("Trying 20%% threshold...")
            primary_events = find_ignition_events(ohlcv_data, tf, 0.20)
            if primary_events:
                logger.info("Found %d events at 20%% — using these for analysis", len(primary_events))
            else:
                logger.error("No events even at 20%% threshold. Dataset may be too small.")
                sys.exit(1)

    # Print event summary
    print(f"\n{'=' * 80}")
    print(f"IGNITION EVENT CATALOG")
    print(f"{'=' * 80}")
    print(f"Timeframe: {tf}")
    print(f"Days scanned: {args.days}")
    print(f"Symbols scanned: {len(set(sym for sym, _ in ohlcv_data if _ == tf))}")
    print(f"\nEvent counts by threshold:")
    for thresh_name, count in event_counts.items():
        print(f"  +{thresh_name}: {count} events")
    print(f"\nUsing {len(primary_events)} events at +{int(args.min_gain * 100)}% for analysis")

    # Top movers
    top_events = sorted(primary_events, key=lambda e: -e["gain_pct"])[:20]
    print(f"\nTop 20 ignition events:")
    print(f"  {'Symbol':<12} {'Gain%':>7} {'Peak Offset':>12} {'Date':<20}")
    print(f"  {'-'*55}")
    for evt in top_events:
        dt = datetime.fromtimestamp(evt["ts"], tz=timezone.utc)
        print(f"  {evt['symbol']:<12} {evt['gain_pct']:>6.1f}% {evt['peak_bar_offset']:>8} bars  "
              f"{dt.strftime('%Y-%m-%d %H:%M')}")

    # ── 5. Extract features for ignition events ──────────────────────────
    logger.info("Extracting features for %d ignition events...", len(primary_events))
    event_features = []
    for evt_idx, evt in enumerate(primary_events):
        sym = evt["symbol"]
        key = (sym, tf)
        if key not in ohlcv_data or key not in all_indicators:
            continue
        data = ohlcv_data[key]
        ind = all_indicators[key]

        feats = extract_features_at_bar(
            data, ind, evt["bar_index"], btc_ind, btc_data,
            universe_temp, ts=evt["ts"]
        )
        if feats:
            feats["label"] = 1
            feats["symbol"] = sym
            feats["gain_pct"] = evt["gain_pct"]
            event_features.append(feats)

    logger.info("Extracted features for %d events", len(event_features))

    # ── 6. Build control group ───────────────────────────────────────────
    logger.info("Building control group...")
    control_features = build_control_group(
        primary_events, ohlcv_data, tf, all_indicators,
        btc_ind, btc_data, universe_temp
    )

    # ── 7. Analysis ──────────────────────────────────────────────────────
    feature_names = [
        "vol_z", "vol_z_max_6", "vol_slope_5", "vol_accel",
        "bb_width", "bb_width_pctile_200", "bb_width_min_20_pctile",
        "atr_pctile_50", "range_pct",
        "close_vs_ema20", "close_vs_sma50", "clv", "body_ratio",
        "is_bullish", "higher_lows_3", "bars_since_52bar_low", "return_12",
        "n_coins_vol_z_gt2", "n_coins_vol_z_gt3", "n_coins_positive_4h",
        "universe_vol_z_mean",
        "btc_return_12", "coin_vs_btc_12", "btc_vol_z",
    ]

    # Step 1: Univariate analysis
    logger.info("Running univariate analysis...")
    univariate = univariate_analysis(event_features, control_features, feature_names)

    print(f"\n{'=' * 80}")
    print(f"UNIVARIATE ANALYSIS — Events vs Controls")
    print(f"{'=' * 80}")
    print(f"{'Feature':<25} {'Mean(Evt)':>10} {'Mean(Ctrl)':>10} {'Effect':>8} {'p-value':>8}")
    print(f"{'-' * 65}")
    for feat in sorted(univariate.keys(), key=lambda f: abs(univariate[f].get("effect_size", 0)),
                       reverse=True):
        info = univariate[feat]
        sig = "***" if info["p_value"] < 0.001 else "**" if info["p_value"] < 0.01 \
              else "*" if info["p_value"] < 0.05 else ""
        print(f"  {feat:<23} {info['mean_event']:>10.3f} {info['mean_control']:>10.3f} "
              f"{info['effect_size']:>+7.2f} {info['p_value']:>7.4f} {sig}")

    # Step 2: Feature correlation
    logger.info("Computing feature correlations...")
    corr_analysis = feature_correlation(event_features, feature_names)
    if corr_analysis["redundant_pairs"]:
        print(f"\nRedundant feature pairs (|r| > 0.8):")
        for pair in corr_analysis["redundant_pairs"]:
            print(f"  {pair['feat1']} <-> {pair['feat2']}: r={pair['correlation']}")

    # Step 3: Logistic regression
    logger.info("Running logistic regression...")
    logreg = run_logistic_regression(event_features, control_features, feature_names)

    print(f"\n{'=' * 80}")
    print(f"LOGISTIC REGRESSION RESULTS")
    print(f"{'=' * 80}")
    print(f"Converged: {logreg['converged']}")
    print(f"Samples: {logreg['n_events']} events + {logreg['n_controls']} controls")
    print(f"\nFeature weights (by absolute coefficient):")
    print(f"  {'Feature':<25} {'Coeff':>8} {'Rec.Weight':>10}")
    print(f"  {'-' * 45}")
    for name, info in logreg["coefficients"].items():
        weight = logreg["recommended_weights"].get(name, 0)
        print(f"  {name:<25} {info['coeff']:>+7.3f} {weight:>9.3f}")

    # Step 4: Threshold sweep
    logger.info("Running threshold sweep...")
    sweep_results = threshold_sweep(
        event_features, control_features, feature_names,
        logreg["coefficients"], logreg["feature_means"],
        logreg["feature_stds"], logreg["intercept"]
    )

    print(f"\n{'=' * 80}")
    print(f"THRESHOLD SWEEP")
    print(f"{'=' * 80}")
    print(f"  {'Threshold':>9} {'Signals':>8} {'Precision':>10} {'Recall':>8} {'F1':>6}")
    print(f"  {'-' * 45}")
    best_f1 = max(sweep_results, key=lambda x: x["f1"])
    for r in sweep_results:
        marker = " <-- best F1" if r["threshold"] == best_f1["threshold"] else ""
        print(f"  {r['threshold']:>9.2f} {r['n_signals']:>8} {r['precision']:>10.3f} "
              f"{r['recall']:>8.3f} {r['f1']:>6.3f}{marker}")

    # Step 5: Clustering analysis
    logger.info("Running clustering analysis...")
    cluster_results = clustering_analysis(primary_events)

    print(f"\n{'=' * 80}")
    print(f"CLUSTERING ANALYSIS")
    print(f"{'=' * 80}")
    print(f"  Events clustered (within 4h): {cluster_results['pct_clustered']*100:.1f}%")
    print(f"  Number of clusters: {cluster_results['n_clusters']}")
    print(f"  Average cluster size: {cluster_results['avg_cluster_size']:.1f}")
    print(f"  Max cluster size: {cluster_results['max_cluster_size']}")
    temp_validated = cluster_results['pct_clustered'] > 0.30
    print(f"  Universe temperature validated: {'YES' if temp_validated else 'NO'} "
          f"(>30% clustered = validated)")

    # ── 8. Write JSON report ─────────────────────────────────────────────
    report = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "days": args.days,
            "timeframe": tf,
            "min_gain": args.min_gain,
        },
        "event_counts": event_counts,
        "event_catalog": [
            {
                "symbol": e["symbol"],
                "gain_pct": round(e["gain_pct"], 2),
                "peak_bar_offset": e["peak_bar_offset"],
                "ts": e["ts"],
                "date": datetime.fromtimestamp(e["ts"], tz=timezone.utc).isoformat(),
            }
            for e in primary_events
        ],
        "feature_importance": univariate,
        "correlation_analysis": corr_analysis,
        "logistic_regression": {
            "intercept": logreg["intercept"],
            "coefficients": logreg["coefficients"],
            "recommended_weights": logreg["recommended_weights"],
            "converged": logreg["converged"],
            "loss_history": logreg["loss_history"],
            "feature_means": logreg["feature_means"],
            "feature_stds": logreg["feature_stds"],
        },
        "threshold_sweep": sweep_results,
        "best_threshold": best_f1,
        "clustering": cluster_results,
        "universe_temperature_validated": temp_validated,
        "summary": {
            "n_events": len(primary_events),
            "n_event_features": len(event_features),
            "n_controls": len(control_features),
            "top_features": [name for name, _ in list(logreg["coefficients"].items())[:5]],
            "recommended_threshold": best_f1["threshold"],
        },
    }

    output_path = os.path.join(os.path.dirname(__file__), "u1_ignition_profiler_report.json")
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Report written to %s", output_path)

    elapsed = time.time() - t0
    print(f"\n{'=' * 80}")
    print(f"COMPLETE — {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"{'=' * 80}")
    print(f"Report: {output_path}")

    # Summary for next phase
    print(f"\n--- PHASE 2 INPUTS ---")
    print(f"Events found: {len(primary_events)} at +{int(args.min_gain*100)}%")
    print(f"Top 5 features: {', '.join(report['summary']['top_features'])}")
    print(f"Recommended threshold: {best_f1['threshold']}")
    print(f"Universe temperature: {'ENABLED' if temp_validated else 'DISABLED'}")
    v3_feasible = len(primary_events) >= 25
    print(f"v3 validation feasible (>= 25 events): {'YES' if v3_feasible else 'NO — consider lowering threshold'}")


if __name__ == "__main__":
    main()
