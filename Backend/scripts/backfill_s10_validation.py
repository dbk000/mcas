#!/usr/bin/env python3
"""
S10 MACD Scalp — Live Validation Backfill
==========================================

Pulls real 5m candles from Binance REST for top-volume symbols,
runs S10 evaluator on rolling windows, and reports signal counts
and characteristics.

Usage:
    docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/backfill_s10_validation.py

Or directly:
    python3 Backend/scripts/backfill_s10_validation.py
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

# Resolve import paths for both Docker and host execution
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/app/worker/app")
else:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "worker", "app"))

import requests
import numpy as np

from indicators import FeatureFactory, OHLCV
from setups.s10_macd_scalp import S10Evaluator, S10Signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("s10_backfill")

# ─── Config ────────────────────────────────────────────────────────
TOP_N_SYMBOLS = 50       # Top USDT pairs by 24h volume
CANDLE_LIMIT = 500       # 500 × 5m = ~41 hours of data
ROLLING_WINDOW = 210     # Bars per evaluation window (201 min + margin)
ROLLING_STEP = 1         # Evaluate every bar
MAX_WORKERS = 6          # Parallel Binance fetches
BATCH_SIZE = 10          # Symbols per batch
BATCH_DELAY_S = 1.5      # Rate limit spacing
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "s10_validation_results.json")

BINANCE_BASE = "https://api.binance.com/api/v3"


# ─── Helpers ───────────────────────────────────────────────────────

def fetch_top_symbols(n: int = TOP_N_SYMBOLS) -> list[str]:
    """Fetch top N USDT symbols by 24h quote volume."""
    resp = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=15)
    resp.raise_for_status()
    tickers = resp.json()
    usdt = [t for t in tickers if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > 0]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in usdt[:n]]


def fetch_5m_candles(symbol: str, limit: int = CANDLE_LIMIT) -> list[OHLCV]:
    """Fetch 5m candles from Binance REST."""
    for attempt in range(3):
        try:
            resp = requests.get(f"{BINANCE_BASE}/klines", params={
                "symbol": symbol, "interval": "5m", "limit": limit
            }, timeout=10)
            if resp.status_code in (418, 429):
                time.sleep(2 ** attempt + 1)
                continue
            resp.raise_for_status()
            return [
                OHLCV(
                    timestamp=int(k[0]),
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                )
                for k in resp.json()
            ]
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                logger.warning("Failed %s: %s", symbol, e)
                return []
    return []


def evaluate_symbol(symbol: str, candles: list[OHLCV]) -> dict:
    """Run S10 on rolling windows over the candle history. Return summary."""
    if len(candles) < ROLLING_WINDOW:
        return {"symbol": symbol, "bars": len(candles), "signals": 0, "error": "insufficient_bars"}

    ff = FeatureFactory()
    evaluator = S10Evaluator()
    signals_found = []

    for end_idx in range(ROLLING_WINDOW, len(candles) + 1, ROLLING_STEP):
        window = candles[end_idx - ROLLING_WINDOW:end_idx]
        try:
            snap = ff.compute(window)
            state = evaluator.compute_state(snap)
            sig = evaluator.evaluate_trigger(snap, state, symbol, "5m")
            if sig is not None:
                ts = datetime.fromtimestamp(window[-1].timestamp / 1000, tz=timezone.utc)
                signals_found.append({
                    "timestamp": ts.isoformat(),
                    "entry": sig.entry_price,
                    "stop": sig.stop_price,
                    "tp1": sig.tp1_price,
                    "tp2": sig.tp2_price,
                    "r_value": sig.r_value,
                    "score": round(sig.setup_score, 4),
                    "rvol": round(sig.rvol, 1),
                    "macd_hist": round(sig.macd_histogram, 8),
                    "flat_top_level": round(sig.flat_top_level, 6),
                    "why": sig.explain.why_items[:3],
                })
        except Exception:
            pass

    hours_covered = (len(candles) - ROLLING_WINDOW) * 5 / 60
    return {
        "symbol": symbol,
        "bars": len(candles),
        "hours_covered": round(hours_covered, 1),
        "signals": len(signals_found),
        "signal_rate_per_hour": round(len(signals_found) / max(hours_covered, 0.1), 2),
        "details": signals_found[:10],  # cap at 10 for output size
    }


# ─── Main ──────────────────────────────────────────────────────────

def main():
    start = time.time()

    logger.info("Fetching top %d symbols by volume...", TOP_N_SYMBOLS)
    symbols = fetch_top_symbols(TOP_N_SYMBOLS)
    logger.info("Got %d symbols: %s ... %s", len(symbols), symbols[:3], symbols[-3:])

    # Fetch 5m candles in batches
    logger.info("Fetching %d × %d 5m candles...", len(symbols), CANDLE_LIMIT)
    all_candles: dict[str, list[OHLCV]] = {}
    fetched = 0

    for batch_start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[batch_start:batch_start + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(fetch_5m_candles, s): s for s in batch}
            for future in as_completed(futures):
                sym = futures[future]
                candles = future.result()
                if candles:
                    all_candles[sym] = candles
                    fetched += 1
        if batch_start + BATCH_SIZE < len(symbols):
            time.sleep(BATCH_DELAY_S)
        logger.info("  Fetched %d/%d symbols", fetched, len(symbols))

    fetch_time = time.time() - start
    logger.info("Fetch complete: %d symbols, %.1fs", len(all_candles), fetch_time)

    # Evaluate S10 on each symbol
    logger.info("Running S10 evaluation on %d symbols...", len(all_candles))
    eval_start = time.time()
    results = []

    for i, (sym, candles) in enumerate(all_candles.items()):
        result = evaluate_symbol(sym, candles)
        results.append(result)
        if result["signals"] > 0:
            logger.info("  %s: %d signals in %.1fh (%.2f/h) — best score %.3f",
                       sym, result["signals"], result["hours_covered"],
                       result["signal_rate_per_hour"],
                       max(s["score"] for s in result["details"]) if result["details"] else 0)
        if (i + 1) % 10 == 0:
            logger.info("  Progress: %d/%d symbols evaluated", i + 1, len(all_candles))

    eval_time = time.time() - eval_start

    # Summary
    total_signals = sum(r["signals"] for r in results)
    symbols_with_signals = sum(1 for r in results if r["signals"] > 0)
    total_hours = sum(r.get("hours_covered", 0) for r in results)

    summary = {
        "run_ts": datetime.now(timezone.utc).isoformat(),
        "config": {
            "symbols": len(all_candles),
            "candle_limit": CANDLE_LIMIT,
            "rolling_window": ROLLING_WINDOW,
            "timeframe": "5m",
        },
        "results": {
            "total_signals": total_signals,
            "symbols_with_signals": symbols_with_signals,
            "total_hours_scanned": round(total_hours, 1),
            "signals_per_hour_universe": round(total_signals / max(total_hours, 0.1), 4),
        },
        "timing": {
            "fetch_s": round(fetch_time, 1),
            "eval_s": round(eval_time, 1),
            "total_s": round(time.time() - start, 1),
        },
        "per_symbol": sorted(results, key=lambda r: r["signals"], reverse=True),
    }

    logger.info("=" * 60)
    logger.info("S10 VALIDATION SUMMARY")
    logger.info("=" * 60)
    logger.info("Symbols scanned: %d", len(all_candles))
    logger.info("Total hours: %.1f", total_hours)
    logger.info("Total signals: %d across %d symbols", total_signals, symbols_with_signals)
    logger.info("Signal rate: %.4f per symbol-hour", summary["results"]["signals_per_hour_universe"])
    logger.info("Fetch: %.1fs | Eval: %.1fs | Total: %.1fs", fetch_time, eval_time, time.time() - start)

    # Top signal examples
    all_details = []
    for r in results:
        for d in r.get("details", []):
            d["symbol"] = r["symbol"]
            all_details.append(d)
    all_details.sort(key=lambda d: d["score"], reverse=True)

    if all_details:
        logger.info("\nTop 5 signals by score:")
        for d in all_details[:5]:
            logger.info("  %s @ %s  score=%.3f  rvol=%.1fx  entry=%.4f  R=%.1f",
                       d["symbol"], d["timestamp"], d["score"], d["rvol"], d["entry"], d["r_value"])
            for w in d["why"][:2]:
                logger.info("    → %s", w)

    # Write results
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info("\nFull results written to: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
