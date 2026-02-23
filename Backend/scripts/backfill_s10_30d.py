#!/usr/bin/env python3
"""
S10 MACD Scalp — 30-Day Historical Backfill to Postgres
========================================================

Fetches 30d of 5m candles from Binance REST (paginated), runs S10 evaluator
on rolling windows, and writes signals as expired opportunities to Postgres.

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/backfill_s10_30d.py
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Resolve import paths
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/app/worker/app")
else:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "worker", "app"))

import requests
import numpy as np
import psycopg

from indicators import FeatureFactory, OHLCV
from setups.s10_macd_scalp import S10Evaluator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("s10_backfill_30d")

# ─── Config ────────────────────────────────────────────────────────
TOP_N_SYMBOLS = 80        # Top USDT pairs by 24h volume
TARGET_DAYS = 30
WARMUP_BARS = 210         # S10 needs 201 min
BARS_PER_DAY = 288        # 24h * 60m / 5m
TOTAL_BARS = (TARGET_DAYS * BARS_PER_DAY) + WARMUP_BARS  # ~8,850
ROLLING_WINDOW = 210
ROLLING_STEP = 1
CANDLES_PER_REQUEST = 1000
MAX_FETCH_WORKERS = 4
BATCH_DELAY_S = 0.5
PG_BATCH_SIZE = 200       # Rows per INSERT batch

DATABASE_URL = os.getenv("DATABASE_URL")
BINANCE_BASE = "https://api.binance.com/api/v3"
BATCH_ID = f"s10_backfill_30d_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


# ─── Helpers ───────────────────────────────────────────────────────

def fetch_top_symbols(n: int = TOP_N_SYMBOLS) -> list[str]:
    """Fetch top N USDT symbols by 24h quote volume."""
    resp = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=15)
    resp.raise_for_status()
    tickers = resp.json()
    usdt = [t for t in tickers if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > 0]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in usdt[:n]]


def fetch_5m_candles_paginated(symbol: str, total_bars: int) -> list[OHLCV]:
    """Fetch 5m candles from Binance REST with pagination (oldest first)."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (total_bars * 5 * 60 * 1000)

    all_candles = []
    cursor_ms = start_ms

    while cursor_ms < now_ms:
        for attempt in range(3):
            try:
                resp = requests.get(f"{BINANCE_BASE}/klines", params={
                    "symbol": symbol,
                    "interval": "5m",
                    "startTime": cursor_ms,
                    "limit": CANDLES_PER_REQUEST,
                }, timeout=15)
                if resp.status_code in (418, 429):
                    time.sleep(2 ** attempt + 1)
                    continue
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    return all_candles
                for k in batch:
                    all_candles.append(OHLCV(
                        timestamp=int(k[0]),
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5]),
                    ))
                cursor_ms = int(batch[-1][0]) + 1  # Next ms after last candle
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                else:
                    logger.warning("Fetch failed %s page at %d: %s", symbol, cursor_ms, e)
                    return all_candles
        time.sleep(0.1)  # Rate limit

    return all_candles


def evaluate_symbol(symbol: str, candles: list[OHLCV]) -> list[dict]:
    """Run S10 on rolling windows. Return list of signal payloads."""
    if len(candles) < ROLLING_WINDOW:
        return []

    ff = FeatureFactory()
    evaluator = S10Evaluator()
    signals = []

    for end_idx in range(ROLLING_WINDOW, len(candles) + 1, ROLLING_STEP):
        window = candles[end_idx - ROLLING_WINDOW:end_idx]
        try:
            snap = ff.compute(window)
            state = evaluator.compute_state(snap)
            sig = evaluator.evaluate_trigger(snap, state, symbol, "5m")
            if sig is None:
                continue

            bar_ts_ms = window[-1].timestamp
            bar_dt = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc)

            reason = "; ".join(sig.explain.why_items) if sig.explain else None
            payload = {
                "setup_id": "S10",
                "setup_name": "MACD Scalp v1.0",
                "symbol": symbol,
                "timeframe": "5m",
                "direction": sig.direction.value,
                "setup_score": sig.setup_score,
                "entry_price": sig.entry_price,
                "stop_price": sig.stop_price,
                "tp1_price": sig.tp1_price,
                "tp2_price": sig.tp2_price,
                "r_value": sig.r_value,
                "time_stop_bars": sig.time_stop_bars,
                "bar_start_ts": str(bar_ts_ms),
                "as_of_ts": bar_ts_ms / 1000,
                "why_items": sig.explain.why_items if sig.explain else [],
                "macd_histogram": sig.macd_histogram,
                "rvol": sig.rvol,
                "sma_20": sig.sma_20,
                "flat_top_level": sig.flat_top_level,
                "trend_bias": sig.trend_bias,
                "score_components": sig.score_components,
            }

            signals.append({
                "symbol": symbol,
                "timeframe": "5m",
                "setup_id": "S10",
                "decision": "SIGNAL",
                "score": sig.setup_score,
                "reason": reason,
                "payload_json": json.dumps(payload),
                "status": "expired",
                "setup_version": "S10_v1.0",
                "batch_id": BATCH_ID,
                "created_ts": bar_dt,
                "updated_ts": bar_dt,
            })
        except Exception:
            pass

    return signals


def write_batch_to_pg(rows: list[dict]):
    """Batch insert opportunities to Postgres."""
    if not rows or not DATABASE_URL:
        return 0
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute("""
                    INSERT INTO opportunities
                        (symbol, timeframe, setup_id, decision, score, reason,
                         payload_json, status, setup_version, batch_id,
                         created_ts, updated_ts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    row["symbol"], row["timeframe"], row["setup_id"],
                    row["decision"], row["score"], row["reason"],
                    row["payload_json"], row["status"], row["setup_version"],
                    row["batch_id"], row["created_ts"], row["updated_ts"],
                ))
        conn.commit()
    return len(rows)


# ─── Main ──────────────────────────────────────────────────────────

def main():
    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    start = time.time()
    logger.info("=" * 60)
    logger.info("S10 MACD Scalp — 30-Day Backfill")
    logger.info("batch_id: %s", BATCH_ID)
    logger.info("=" * 60)

    # Phase 1: Get symbols
    logger.info("Fetching top %d symbols by volume...", TOP_N_SYMBOLS)
    symbols = fetch_top_symbols(TOP_N_SYMBOLS)
    logger.info("Got %d symbols", len(symbols))

    # Phase 2: Fetch candles (sequential with rate limiting)
    logger.info("Fetching %d bars per symbol (%dd + warmup)...", TOTAL_BARS, TARGET_DAYS)
    all_candles = {}
    for i, sym in enumerate(symbols):
        candles = fetch_5m_candles_paginated(sym, TOTAL_BARS)
        if candles:
            all_candles[sym] = candles
        if (i + 1) % 10 == 0:
            elapsed = time.time() - start
            logger.info("  Fetched %d/%d symbols (%.0fs elapsed, ~%.0fs remaining)",
                       i + 1, len(symbols), elapsed,
                       elapsed / (i + 1) * (len(symbols) - i - 1))
        time.sleep(BATCH_DELAY_S)

    fetch_time = time.time() - start
    logger.info("Fetch complete: %d symbols, %.1fs", len(all_candles), fetch_time)

    # Phase 3: Evaluate and write
    logger.info("Evaluating S10 on %d symbols...", len(all_candles))
    eval_start = time.time()
    total_signals = 0
    total_written = 0
    pg_buffer = []

    for i, (sym, candles) in enumerate(all_candles.items()):
        signals = evaluate_symbol(sym, candles)
        total_signals += len(signals)
        pg_buffer.extend(signals)

        # Batch write when buffer is full
        if len(pg_buffer) >= PG_BATCH_SIZE:
            written = write_batch_to_pg(pg_buffer)
            total_written += written
            pg_buffer = []

        if (i + 1) % 10 == 0:
            elapsed = time.time() - eval_start
            logger.info("  Evaluated %d/%d symbols — %d signals so far (%.0fs elapsed)",
                       i + 1, len(all_candles), total_signals, elapsed)

    # Flush remaining buffer
    if pg_buffer:
        written = write_batch_to_pg(pg_buffer)
        total_written += written

    eval_time = time.time() - eval_start
    total_time = time.time() - start

    # Summary
    total_hours = len(all_candles) * TARGET_DAYS * 24
    logger.info("=" * 60)
    logger.info("S10 BACKFILL COMPLETE")
    logger.info("=" * 60)
    logger.info("Symbols: %d", len(all_candles))
    logger.info("Period: %d days", TARGET_DAYS)
    logger.info("Signals found: %d", total_signals)
    logger.info("Written to Postgres: %d (status=expired, batch_id=%s)", total_written, BATCH_ID)
    logger.info("Signal rate: %.2f per symbol per day",
               total_signals / max(len(all_candles), 1) / TARGET_DAYS)
    logger.info("Timing — fetch: %.0fs, eval: %.0fs, total: %.0fs",
               fetch_time, eval_time, total_time)


if __name__ == "__main__":
    main()
