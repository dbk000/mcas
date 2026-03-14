"""
Microstructure I/O — Binance 1s kline fetching
===============================================

Fetches 1-second klines from Binance REST API for microstructure analysis.
This module handles all network I/O; the pure analysis math lives in
core_logic/microstructure.py.

Import direction: governance/ can import core_logic/, never reversed.
"""

import time
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from core_logic.microstructure import Kline1s

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
MAX_RETRY_WAIT_S = 10


def _binance_kline_url() -> str:
    """Read BINANCE_KLINES_URL from frozen ConfigRegistry."""
    from governance.bootstrap import get_config
    return get_config().Worker.BINANCE_KLINES_URL


def fetch_1s_klines(
    symbol: str,
    start_time_ms: int,
    limit: int = 300,
    timeout: int = 10,
) -> list[Kline1s]:
    """Fetch 1-second klines from Binance REST API.

    Args:
        symbol: e.g., "BTCUSDT"
        start_time_ms: Start timestamp in milliseconds (inclusive)
        limit: Number of 1s klines to fetch (max 1000)
        timeout: Request timeout in seconds

    Returns:
        List of Kline1s, oldest first. Empty list on failure.
    """
    params = {
        "symbol": symbol,
        "interval": "1s",
        "startTime": int(start_time_ms),
        "limit": min(limit, 1000),
    }

    data = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(_binance_kline_url(), params=params, timeout=timeout)
            if resp.status_code in (418, 429):
                retry_after = int(resp.headers.get("Retry-After", "0"))
                backoff = 2 ** attempt + random.uniform(0, 1)
                wait = max(retry_after, min(backoff, MAX_RETRY_WAIT_S))
                logger.warning(
                    "Binance 1s rate limit for %s, retry in %.1fs (attempt %d)",
                    symbol, wait, attempt + 1,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt + random.uniform(0, 0.5))
                continue
            logger.warning("Binance 1s kline timeout after retries: %s", symbol)
            return []
        except Exception as e:
            logger.error("Binance 1s kline fetch failed for %s: %s", symbol, e)
            return []
    else:
        logger.warning("Binance 1s kline retries exhausted: %s", symbol)
        return []

    klines = []
    for k in data:
        klines.append(Kline1s(
            timestamp_ms=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        ))
    return klines


def fetch_1s_klines_batch(
    symbols: list[str],
    start_time_ms: int,
    limit: int = 300,
    max_workers: int = 8,
) -> dict[str, list[Kline1s]]:
    """Fetch 1s klines for multiple symbols in parallel.

    Returns:
        Dict mapping symbol -> list of Kline1s. Only includes symbols
        where data was successfully fetched.
    """
    results = {}

    def _fetch_one(sym):
        return sym, fetch_1s_klines(sym, start_time_ms, limit)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            try:
                sym, klines = future.result()
                if klines:
                    results[sym] = klines
            except Exception as e:
                logger.debug("1s kline batch fetch failed for %s: %s",
                             futures[future], e)

    return results
