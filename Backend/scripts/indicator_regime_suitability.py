#!/usr/bin/env python3
"""
Indicator Regime Suitability Analysis
======================================

Computes regime-conditional information coefficients (IC), hit rates, and
stability metrics for every numeric indicator in the MCAS IndicatorSnapshot.

Market regimes are classified from ATR percentile + SMA50 slope:
  - Trending Up:   SMA50 slope > 0.5 (ATR-normalized) AND ATR pctl > 50
  - Trending Down: SMA50 slope < -0.5 (ATR-normalized) AND ATR pctl > 50
  - Ranging:       abs(SMA50 slope) < 0.5 AND ATR pctl < 50
  - Volatile:      ATR pctl > 75 (regardless of slope)

For each indicator x regime:
  - IC: Spearman rank correlation with forward 24-bar return
  - Hit Rate: % of times indicator direction matches forward return direction
  - Stability: Std of IC across 7-day rolling windows (lower = more stable)

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/indicator_regime_suitability.py

    # Custom sample size / lookback
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/indicator_regime_suitability.py \
            --n-symbols 50 --lookback-days 90 --forward-bars 24
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import multiprocessing
import os
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
from scipy import stats as scipy_stats

# ── Runtime path detection ─────────────────────────────────────────────
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

import psycopg
from psycopg.rows import dict_row

from indicators import FeatureFactory, IndicatorSnapshot, OHLCV

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("regime_suitability")

# ── Database URL ───────────────────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading"
)

# ── Indicator fields to evaluate ───────────────────────────────────────
INDICATOR_FIELDS = [
    "atr", "clv", "volume_z", "rsi", "rsi_sma", "rsi_slope",
    "sma_50", "sma_200", "ema_9", "ema_20", "ema_21", "ema_50",
    "bb_mb", "bb_ub", "bb_lb", "bb_width",
    "kc_mb", "kc_ub", "kc_lb",
    "sma50_slope_atr", "sma200_slope_atr", "ma_gap_atr",
    "ema20_slope_atr", "ema50_slope_atr",
    "sma_20",
    "macd_line", "macd_signal", "macd_histogram",
    "rvol", "range_to_volume_ratio", "volume_acceleration",
    "donchian_high_20", "donchian_low_20", "donchian_mid_20",
    "dormancy_score", "accum_divergence",
]

# ── Regime definitions ─────────────────────────────────────────────────
REGIMES = ["trending_up", "trending_down", "ranging", "volatile"]

# Warmup bars needed for FeatureFactory (SMA200 needs 200+ bars)
WARMUP_BARS = 250

# 7-day rolling window for stability (in 1h bars)
STABILITY_WINDOW_BARS = 7 * 24  # 168

# ── COW globals (set before fork) ──────────────────────────────────────
_OHLCV_DATA: dict[str, list[dict]] = {}
_FORWARD_BARS: int = 24


# ══════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ══════════════════════════════════════════════════════════════════════

def get_top_symbols(n: int) -> list[str]:
    """Get top N symbols by 24h quote volume from production DB."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, SUM(volume * close) as quote_vol
                FROM ohlcv
                WHERE timeframe = '1h'
                  AND is_final = TRUE
                  AND bar_start_ts >= NOW() - INTERVAL '7 days'
                GROUP BY symbol
                ORDER BY quote_vol DESC
                LIMIT %s;
            """, (n,))
            rows = cur.fetchall()
            symbols = [r["symbol"] for r in rows]
            log.info("Top %d symbols by 7d quote volume: %s ... %s",
                     len(symbols), symbols[:3], symbols[-3:])
            return symbols


def load_ohlcv_for_symbols(
    symbols: list[str],
    lookback_days: int,
    timeframe: str = "1h",
) -> dict[str, list[dict]]:
    """Load OHLCV in a single query per symbol, covering lookback + warmup."""
    total_days = lookback_days + (WARMUP_BARS // 24) + 5  # extra margin
    start_ts = datetime.now(timezone.utc) - timedelta(days=total_days)
    data: dict[str, list[dict]] = {}

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for i, sym in enumerate(symbols):
                cur.execute("""
                    SELECT DISTINCT ON (bar_start_ts)
                           bar_start_ts, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = %s
                      AND bar_start_ts >= %s
                      AND is_final = TRUE
                    ORDER BY bar_start_ts,
                             CASE source WHEN 'ws' THEN 0
                                         WHEN 'binance' THEN 1
                                         ELSE 2 END;
                """, (sym, timeframe, start_ts))
                rows = cur.fetchall()
                bars = [
                    {
                        "bar_start_ts": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    }
                    for r in rows
                ]
                data[sym] = bars
                if (i + 1) % 10 == 0 or (i + 1) == len(symbols):
                    log.info("  Loaded OHLCV: %d/%d symbols (%s: %d bars)",
                             i + 1, len(symbols), sym, len(bars))

    return data


# ══════════════════════════════════════════════════════════════════════
#  REGIME CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════

def classify_regime(
    sma50_slope_atr: float,
    atr_percentile: float,
) -> str:
    """
    Classify market regime from ATR percentile + SMA50 slope.

    Priority: volatile > trending_up/trending_down > ranging.
    A bar can only be in one regime.
    """
    if atr_percentile > 75:
        return "volatile"
    if sma50_slope_atr > 0.5 and atr_percentile > 50:
        return "trending_up"
    if sma50_slope_atr < -0.5 and atr_percentile > 50:
        return "trending_down"
    if abs(sma50_slope_atr) < 0.5 and atr_percentile < 50:
        return "ranging"
    # Fallback: doesn't neatly fit any regime (e.g. moderate slope, moderate ATR)
    return "unclassified"


# ══════════════════════════════════════════════════════════════════════
#  PER-SYMBOL WORKER (runs in forked child — reads COW globals)
# ══════════════════════════════════════════════════════════════════════

def _process_symbol(symbol: str) -> Optional[dict]:
    """
    For one symbol:
      1. Compute indicators at every bar using a sliding window
      2. Classify regime per bar
      3. Compute forward returns
      4. Return per-indicator, per-regime IC / hit rate / stability
    """
    bars = _OHLCV_DATA.get(symbol, [])
    forward_bars = _FORWARD_BARS

    if len(bars) < WARMUP_BARS + forward_bars + 50:
        log.warning("  %s: only %d bars, need %d+, skipping",
                     symbol, len(bars), WARMUP_BARS + forward_bars + 50)
        return None

    # Build OHLCV objects
    candles = [
        OHLCV(
            timestamp=int(b["bar_start_ts"].timestamp() * 1000)
            if hasattr(b["bar_start_ts"], "timestamp")
            else int(b["bar_start_ts"]),
            open=b["open"],
            high=b["high"],
            low=b["low"],
            close=b["close"],
            volume=b["volume"],
        )
        for b in bars
    ]

    # Arrays for regime classification
    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])

    ff = FeatureFactory()

    # We need at least 201 candles for FeatureFactory.compute().
    # We'll slide a window starting at index WARMUP_BARS through
    # len(candles) - forward_bars, computing indicators at each bar.
    start_idx = WARMUP_BARS
    end_idx = len(candles) - forward_bars

    if start_idx >= end_idx:
        log.warning("  %s: not enough bars after warmup, skipping", symbol)
        return None

    n_bars_to_eval = end_idx - start_idx

    # Pre-compute ATR series for percentile ranking
    # Use a rolling ATR(14) for the full series
    atr_n = 14
    tr = np.zeros(len(closes))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    # Rolling ATR as SMA of TR
    atr_series = np.convolve(tr, np.ones(atr_n) / atr_n, mode="full")[:len(tr)]
    atr_series[:atr_n - 1] = atr_series[atr_n - 1]  # fill early values

    # Pre-compute forward returns: (close[i + forward_bars] - close[i]) / close[i]
    fwd_returns = np.full(len(closes), np.nan)
    for i in range(len(closes) - forward_bars):
        if closes[i] > 0:
            fwd_returns[i] = (closes[i + forward_bars] - closes[i]) / closes[i]

    # Storage: per-bar indicator values and regime labels
    indicator_values = {field: np.full(n_bars_to_eval, np.nan) for field in INDICATOR_FIELDS}
    regime_labels = [""] * n_bars_to_eval
    fwd_ret_slice = np.full(n_bars_to_eval, np.nan)

    # ATR history for percentile computation (trailing 500-bar window)
    atr_history_window = 500

    for eval_idx in range(n_bars_to_eval):
        bar_idx = start_idx + eval_idx

        # Compute indicators using FeatureFactory with window ending at bar_idx
        window_start = max(0, bar_idx - WARMUP_BARS - 50)
        window_candles = candles[window_start: bar_idx + 1]

        if len(window_candles) < 201:
            continue

        try:
            snap = ff.compute(window_candles)
        except (ValueError, ZeroDivisionError):
            continue

        # Extract indicator values
        for field in INDICATOR_FIELDS:
            val = getattr(snap, field, None)
            if val is not None and np.isfinite(val):
                indicator_values[field][eval_idx] = val

        # Regime classification
        # ATR percentile: rank current ATR within trailing window
        atr_start = max(0, bar_idx - atr_history_window)
        atr_window = atr_series[atr_start: bar_idx + 1]
        current_atr = atr_series[bar_idx]
        if len(atr_window) > 10:
            atr_pctl = 100.0 * np.sum(atr_window < current_atr) / len(atr_window)
        else:
            atr_pctl = 50.0

        regime = classify_regime(snap.sma50_slope_atr, atr_pctl)
        regime_labels[eval_idx] = regime

        fwd_ret_slice[eval_idx] = fwd_returns[bar_idx]

    # Now compute metrics per indicator x regime
    regime_labels_arr = np.array(regime_labels)
    results = {}

    for field in INDICATOR_FIELDS:
        vals = indicator_values[field]
        field_results = {}

        for regime in REGIMES:
            mask = (regime_labels_arr == regime) & np.isfinite(vals) & np.isfinite(fwd_ret_slice)
            n_samples = int(np.sum(mask))

            if n_samples < 30:
                field_results[regime] = {
                    "ic": None, "hit_rate": None, "stability": None,
                    "n_samples": n_samples,
                }
                continue

            v = vals[mask]
            r = fwd_ret_slice[mask]

            # IC: Spearman rank correlation
            try:
                ic, _ = scipy_stats.spearmanr(v, r)
                if not np.isfinite(ic):
                    ic = 0.0
            except Exception:
                ic = 0.0

            # Hit rate: indicator change direction matches return direction
            # Compute indicator "direction" as sign of z-scored value
            v_mean = np.mean(v)
            v_std = np.std(v)
            if v_std > 1e-12:
                v_direction = np.sign(v - v_mean)
            else:
                v_direction = np.zeros_like(v)
            r_direction = np.sign(r)
            # Exclude zero returns from hit rate
            nonzero = r_direction != 0
            if np.sum(nonzero) > 10:
                hit_rate = float(np.mean(v_direction[nonzero] == r_direction[nonzero]))
            else:
                hit_rate = None

            # Stability: std of IC across rolling 7-day windows
            # Get the indices where mask is True
            mask_indices = np.where(mask)[0]
            if len(mask_indices) >= STABILITY_WINDOW_BARS:
                window_ics = []
                step = STABILITY_WINDOW_BARS // 4  # 25% overlap
                for win_start in range(0, len(mask_indices) - STABILITY_WINDOW_BARS + 1, step):
                    win_idx = mask_indices[win_start: win_start + STABILITY_WINDOW_BARS]
                    wv = vals[win_idx]
                    wr = fwd_ret_slice[win_idx]
                    valid = np.isfinite(wv) & np.isfinite(wr)
                    if np.sum(valid) >= 20:
                        try:
                            wic, _ = scipy_stats.spearmanr(wv[valid], wr[valid])
                            if np.isfinite(wic):
                                window_ics.append(wic)
                        except Exception:
                            pass
                stability = float(np.std(window_ics)) if len(window_ics) >= 3 else None
            else:
                stability = None

            field_results[regime] = {
                "ic": round(float(ic), 6),
                "hit_rate": round(float(hit_rate), 4) if hit_rate is not None else None,
                "stability": round(float(stability), 6) if stability is not None else None,
                "n_samples": n_samples,
            }

        results[field] = field_results

    # Also compute regime distribution
    regime_counts = {}
    for regime in REGIMES + ["unclassified"]:
        regime_counts[regime] = int(np.sum(regime_labels_arr == regime))

    return {
        "symbol": symbol,
        "n_bars_evaluated": n_bars_to_eval,
        "regime_distribution": regime_counts,
        "indicator_metrics": results,
    }


# ══════════════════════════════════════════════════════════════════════
#  AGGREGATION
# ══════════════════════════════════════════════════════════════════════

def aggregate_results(per_symbol_results: list[dict]) -> dict:
    """
    Aggregate per-symbol results into global indicator x regime metrics.
    Uses Fisher z-transform for IC averaging.
    """
    # Collect per-symbol ICs for each (indicator, regime)
    ic_collector: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    hr_collector: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    stab_collector: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    sample_collector: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    total_regime_counts = defaultdict(int)

    for res in per_symbol_results:
        for regime, cnt in res["regime_distribution"].items():
            total_regime_counts[regime] += cnt

        for field, regime_data in res["indicator_metrics"].items():
            for regime, metrics in regime_data.items():
                if metrics["ic"] is not None:
                    ic_collector[field][regime].append(metrics["ic"])
                if metrics["hit_rate"] is not None:
                    hr_collector[field][regime].append(metrics["hit_rate"])
                if metrics["stability"] is not None:
                    stab_collector[field][regime].append(metrics["stability"])
                sample_collector[field][regime].append(metrics["n_samples"])

    # Aggregate using Fisher z-transform for IC
    aggregated = {}
    for field in INDICATOR_FIELDS:
        field_agg = {}
        for regime in REGIMES:
            ics = ic_collector[field].get(regime, [])
            hrs = hr_collector[field].get(regime, [])
            stabs = stab_collector[field].get(regime, [])
            samples = sample_collector[field].get(regime, [])

            if ics:
                # Fisher z-transform: arctanh(r), average, then tanh back
                z_vals = [np.arctanh(np.clip(ic, -0.999, 0.999)) for ic in ics]
                mean_z = np.mean(z_vals)
                mean_ic = float(np.tanh(mean_z))
                ic_std = float(np.std(ics))
            else:
                mean_ic = None
                ic_std = None

            mean_hr = float(np.mean(hrs)) if hrs else None
            mean_stab = float(np.mean(stabs)) if stabs else None
            total_samples = int(np.sum(samples)) if samples else 0
            n_symbols = len(ics)

            field_agg[regime] = {
                "ic_mean": round(mean_ic, 6) if mean_ic is not None else None,
                "ic_std": round(ic_std, 6) if ic_std is not None else None,
                "hit_rate_mean": round(mean_hr, 4) if mean_hr is not None else None,
                "stability_mean": round(mean_stab, 6) if mean_stab is not None else None,
                "total_samples": total_samples,
                "n_symbols_contributing": n_symbols,
            }

        # Best regime (by abs IC)
        valid_ics = {
            r: abs(field_agg[r]["ic_mean"])
            for r in REGIMES
            if field_agg[r]["ic_mean"] is not None
        }
        if valid_ics:
            best_regime = max(valid_ics, key=valid_ics.get)
            max_abs_ic = max(valid_ics.values())
            nonzero_ics = [v for v in valid_ics.values() if v > 0.001]
            min_abs_ic = min(nonzero_ics) if nonzero_ics else 0.001
            spread_ratio = max_abs_ic / min_abs_ic if min_abs_ic > 0.001 else float("inf")
        else:
            best_regime = None
            spread_ratio = None

        field_agg["_summary"] = {
            "best_regime": best_regime,
            "spread_ratio": round(spread_ratio, 1) if spread_ratio is not None else None,
            "regime_dependent": spread_ratio is not None and spread_ratio > 3.0,
        }

        aggregated[field] = field_agg

    return {
        "aggregated_metrics": aggregated,
        "total_regime_distribution": dict(total_regime_counts),
        "n_symbols": len(per_symbol_results),
    }


# ══════════════════════════════════════════════════════════════════════
#  OUTPUT FORMATTING
# ══════════════════════════════════════════════════════════════════════

def format_summary_table(agg: dict) -> str:
    """Create human-readable summary table."""
    lines = []
    lines.append("=" * 120)
    lines.append("INDICATOR REGIME SUITABILITY ANALYSIS")
    lines.append("=" * 120)
    lines.append("")

    # Regime distribution
    dist = agg["total_regime_distribution"]
    total_bars = sum(dist.values())
    lines.append(f"Total bars evaluated: {total_bars:,} across {agg['n_symbols']} symbols")
    lines.append("Regime distribution:")
    for regime in REGIMES + ["unclassified"]:
        cnt = dist.get(regime, 0)
        pct = 100 * cnt / total_bars if total_bars > 0 else 0
        lines.append(f"  {regime:20s}: {cnt:>10,} ({pct:5.1f}%)")
    lines.append("")

    # Main table
    header = f"{'Indicator':<28s} | {'Trend Up':>10s} | {'Trend Dn':>10s} | {'Ranging':>10s} | {'Volatile':>10s} | {'Best Regime':<15s} | {'Regime-Dep?':<20s}"
    sep = "-" * len(header)
    lines.append(header)
    lines.append(sep)

    metrics = agg["aggregated_metrics"]

    # Sort by max abs IC descending
    def sort_key(field):
        summary = metrics[field]["_summary"]
        if summary["best_regime"] is None:
            return 0
        best = summary["best_regime"]
        ic = metrics[field][best].get("ic_mean")
        return abs(ic) if ic is not None else 0

    sorted_fields = sorted(INDICATOR_FIELDS, key=sort_key, reverse=True)

    for field in sorted_fields:
        m = metrics[field]
        summary = m["_summary"]

        vals = []
        for regime in REGIMES:
            ic = m[regime].get("ic_mean")
            if ic is not None:
                vals.append(f"{ic:>10.4f}")
            else:
                vals.append(f"{'n/a':>10s}")

        best = summary["best_regime"] or "n/a"
        spread = summary["spread_ratio"]
        if spread is not None and summary["regime_dependent"]:
            dep_str = f"Yes ({spread:.1f}x spread)"
        elif spread is not None:
            dep_str = f"Mild ({spread:.1f}x)"
        else:
            dep_str = "n/a"

        line = f"{field:<28s} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]} | {best:<15s} | {dep_str:<20s}"
        lines.append(line)

    lines.append(sep)
    lines.append("")

    # Detailed section: hit rates
    lines.append("")
    lines.append("=" * 100)
    lines.append("HIT RATES BY REGIME (indicator direction vs forward return direction)")
    lines.append("=" * 100)
    header2 = f"{'Indicator':<28s} | {'Trend Up':>10s} | {'Trend Dn':>10s} | {'Ranging':>10s} | {'Volatile':>10s}"
    lines.append(header2)
    lines.append("-" * len(header2))

    for field in sorted_fields:
        m = metrics[field]
        vals = []
        for regime in REGIMES:
            hr = m[regime].get("hit_rate_mean")
            if hr is not None:
                vals.append(f"{hr:>10.1%}")
            else:
                vals.append(f"{'n/a':>10s}")
        line = f"{field:<28s} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]}"
        lines.append(line)
    lines.append("")

    # IC stability section
    lines.append("")
    lines.append("=" * 100)
    lines.append("IC STABILITY (lower = more consistent across 7-day windows)")
    lines.append("=" * 100)
    header3 = f"{'Indicator':<28s} | {'Trend Up':>10s} | {'Trend Dn':>10s} | {'Ranging':>10s} | {'Volatile':>10s}"
    lines.append(header3)
    lines.append("-" * len(header3))

    for field in sorted_fields:
        m = metrics[field]
        vals = []
        for regime in REGIMES:
            stab = m[regime].get("stability_mean")
            if stab is not None:
                vals.append(f"{stab:>10.4f}")
            else:
                vals.append(f"{'n/a':>10s}")
        line = f"{field:<28s} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]}"
        lines.append(line)
    lines.append("")

    # Top findings summary
    lines.append("")
    lines.append("=" * 100)
    lines.append("TOP FINDINGS")
    lines.append("=" * 100)

    # Strongest ICs overall
    ic_list = []
    for field in INDICATOR_FIELDS:
        for regime in REGIMES:
            ic = metrics[field][regime].get("ic_mean")
            if ic is not None:
                ic_list.append((field, regime, ic))
    ic_list.sort(key=lambda x: abs(x[2]), reverse=True)

    lines.append("")
    lines.append("Strongest IC values (top 15):")
    for field, regime, ic in ic_list[:15]:
        n = metrics[field][regime]["total_samples"]
        lines.append(f"  {field:<28s} in {regime:<15s}: IC = {ic:+.4f} (n={n:,})")

    # Most regime-dependent indicators
    lines.append("")
    lines.append("Most regime-dependent indicators (spread > 3x):")
    dep_list = [
        (field, metrics[field]["_summary"]["spread_ratio"], metrics[field]["_summary"]["best_regime"])
        for field in INDICATOR_FIELDS
        if metrics[field]["_summary"]["regime_dependent"]
    ]
    dep_list.sort(key=lambda x: x[1], reverse=True)
    for field, spread, best in dep_list[:15]:
        lines.append(f"  {field:<28s}: {spread:.1f}x spread, best in {best}")

    # Most stable ICs
    lines.append("")
    lines.append("Most stable ICs (lowest IC std across windows, with |IC| > 0.03):")
    stable_list = []
    for field in INDICATOR_FIELDS:
        for regime in REGIMES:
            ic = metrics[field][regime].get("ic_mean")
            stab = metrics[field][regime].get("stability_mean")
            if ic is not None and stab is not None and abs(ic) > 0.03:
                stable_list.append((field, regime, ic, stab))
    stable_list.sort(key=lambda x: x[3])
    for field, regime, ic, stab in stable_list[:10]:
        lines.append(f"  {field:<28s} in {regime:<15s}: IC = {ic:+.4f}, stability = {stab:.4f}")

    lines.append("")
    return "\n".join(lines)


def write_csv(agg: dict, csv_path: str):
    """Write a CSV that could be loaded into an indicator_regime_scores table."""
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "indicator_name", "regime", "ic_mean", "ic_std",
            "hit_rate_mean", "stability_mean",
            "total_samples", "n_symbols_contributing",
            "best_regime", "spread_ratio", "regime_dependent",
            "computed_at",
        ])
        now_str = datetime.now(timezone.utc).isoformat()
        metrics = agg["aggregated_metrics"]
        for field in INDICATOR_FIELDS:
            m = metrics[field]
            summary = m["_summary"]
            for regime in REGIMES:
                d = m[regime]
                writer.writerow([
                    field,
                    regime,
                    d.get("ic_mean", ""),
                    d.get("ic_std", ""),
                    d.get("hit_rate_mean", ""),
                    d.get("stability_mean", ""),
                    d.get("total_samples", 0),
                    d.get("n_symbols_contributing", 0),
                    summary.get("best_regime", ""),
                    summary.get("spread_ratio", ""),
                    summary.get("regime_dependent", ""),
                    now_str,
                ])
    log.info("CSV written: %s", csv_path)


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    global _OHLCV_DATA, _FORWARD_BARS

    parser = argparse.ArgumentParser(
        description="Indicator regime suitability analysis"
    )
    parser.add_argument("--n-symbols", type=int, default=50,
                        help="Number of top symbols to analyze (default: 50)")
    parser.add_argument("--lookback-days", type=int, default=90,
                        help="Lookback period in days (default: 90)")
    parser.add_argument("--forward-bars", type=int, default=24,
                        help="Forward return horizon in bars (default: 24)")
    parser.add_argument("--n-workers", type=int, default=8,
                        help="Parallel workers (default: 8)")
    parser.add_argument("--timeframe", type=str, default="1h",
                        help="Timeframe (default: 1h)")
    args = parser.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Determine output directory
    if os.path.isdir("/app"):
        output_dir = "/scripts/output"
    else:
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "output"
        )
    os.makedirs(output_dir, exist_ok=True)

    json_path = os.path.join(output_dir, f"indicator_regime_suitability_{ts}.json")
    txt_path = os.path.join(output_dir, f"indicator_regime_suitability_{ts}.txt")
    csv_path = os.path.join(output_dir, f"indicator_regime_suitability_{ts}.csv")

    log.info("=" * 70)
    log.info("Indicator Regime Suitability Analysis")
    log.info("=" * 70)
    log.info("Parameters:")
    log.info("  Symbols:       %d (top by 7d quote volume)", args.n_symbols)
    log.info("  Lookback:      %d days", args.lookback_days)
    log.info("  Forward bars:  %d (%s)", args.forward_bars, args.timeframe)
    log.info("  Workers:       %d", args.n_workers)
    log.info("  Timeframe:     %s", args.timeframe)
    log.info("Output paths:")
    log.info("  JSON: %s", json_path)
    log.info("  TXT:  %s", txt_path)
    log.info("  CSV:  %s", csv_path)
    log.info("=" * 70)

    t0 = time.time()

    # Step 1: Get top symbols
    log.info("Step 1: Fetching top %d symbols...", args.n_symbols)
    symbols = get_top_symbols(args.n_symbols)
    if not symbols:
        log.error("No symbols found. Check DB connectivity.")
        sys.exit(1)
    log.info("  Got %d symbols (%.1fs)", len(symbols), time.time() - t0)

    # Step 2: Load OHLCV (batch load in parent process)
    log.info("Step 2: Loading OHLCV for %d symbols...", len(symbols))
    t_load = time.time()
    ohlcv_data = load_ohlcv_for_symbols(symbols, args.lookback_days, args.timeframe)
    log.info("  OHLCV loaded: %d symbols, %d total bars (%.1fs)",
             len(ohlcv_data),
             sum(len(v) for v in ohlcv_data.values()),
             time.time() - t_load)

    # Filter out symbols with too few bars
    min_bars = WARMUP_BARS + args.forward_bars + 50
    valid_symbols = [s for s in symbols if len(ohlcv_data.get(s, [])) >= min_bars]
    log.info("  %d symbols have >= %d bars (dropped %d)",
             len(valid_symbols), min_bars, len(symbols) - len(valid_symbols))

    if not valid_symbols:
        log.error("No symbols with sufficient data. Need %d+ bars per symbol.", min_bars)
        sys.exit(1)

    # Step 3: Set COW globals before fork
    _OHLCV_DATA = ohlcv_data
    _FORWARD_BARS = args.forward_bars

    # Step 4: Process symbols in parallel
    log.info("Step 3: Processing %d symbols with %d workers...", len(valid_symbols), args.n_workers)
    t_compute = time.time()
    per_symbol_results = []
    completed = 0

    # Sort by bar count descending (heaviest first)
    valid_symbols.sort(key=lambda s: len(ohlcv_data.get(s, [])), reverse=True)
    heaviest = len(ohlcv_data.get(valid_symbols[0], []))
    lightest = len(ohlcv_data.get(valid_symbols[-1], []))
    log.info("  Heaviest: %d bars, Lightest: %d bars, Ratio: %.1fx",
             heaviest, lightest, heaviest / max(lightest, 1))

    with multiprocessing.Pool(args.n_workers) as pool:
        for result in pool.imap_unordered(_process_symbol, valid_symbols):
            completed += 1
            if result is not None:
                per_symbol_results.append(result)
            if completed % 10 == 0 or completed == len(valid_symbols):
                elapsed = time.time() - t_compute
                eta = (elapsed / completed) * (len(valid_symbols) - completed)
                log.info("  [%d/%d] %d valid | %.0fs elapsed | ~%.0fs remaining",
                         completed, len(valid_symbols), len(per_symbol_results),
                         elapsed, eta)

    log.info("  Computation complete: %d/%d symbols produced results (%.1fs)",
             len(per_symbol_results), len(valid_symbols), time.time() - t_compute)

    if not per_symbol_results:
        log.error("No symbols produced valid results.")
        sys.exit(1)

    # Step 5: Aggregate
    log.info("Step 4: Aggregating results...")
    agg = aggregate_results(per_symbol_results)

    # Step 6: Write outputs
    log.info("Step 5: Writing outputs...")

    # JSON
    full_output = {
        "metadata": {
            "n_symbols": args.n_symbols,
            "lookback_days": args.lookback_days,
            "forward_bars": args.forward_bars,
            "timeframe": args.timeframe,
            "n_workers": args.n_workers,
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "indicator_fields": INDICATOR_FIELDS,
            "regimes": REGIMES,
        },
        "aggregated": agg,
        "per_symbol": per_symbol_results,
    }
    with open(json_path, "w") as f:
        json.dump(full_output, f, indent=2, default=str)
    log.info("  JSON: %s (%.1f MB)", json_path, os.path.getsize(json_path) / 1e6)

    # TXT summary
    summary = format_summary_table(agg)
    with open(txt_path, "w") as f:
        f.write(summary)
    log.info("  TXT:  %s", txt_path)

    # CSV
    write_csv(agg, csv_path)

    # Print summary to console
    print("\n" + summary)

    elapsed_total = time.time() - t0
    log.info("=" * 70)
    log.info("Complete in %.1fs", elapsed_total)
    log.info("=" * 70)


if __name__ == "__main__":
    main()
