#!/usr/bin/env python3
"""
Backfill Equities 1m OHLCV from Alpaca Markets API.
=====================================================
Fetches 1-minute bars for NVDA + cascade stocks on all 8 historical
earnings reaction dates. Uses raw requests (no alpaca-py dependency).

Requires ALPACA_API_KEY and ALPACA_SECRET_KEY in environment or .env file.
Free paper account provides IEX data; script tries SIP first, falls back to IEX.

8 reaction dates × 5 symbols = 40 API calls, ~33,600 bars total.

Usage:
  # Set keys in Backend/.env:
  #   ALPACA_API_KEY=PK...
  #   ALPACA_SECRET_KEY=...
  #
  python3 Backend/scripts/backfill_equities_1m_alpaca.py

  # Or in Docker (keys in .env):
  docker compose -f docker-compose.yml run --rm -T worker \
    python3 /scripts/backfill_equities_1m_alpaca.py
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone

import requests as http_requests

# Execution context detection (Corrections Log 2026-02-12)
if os.path.isdir('/scripts'):
    sys.path.insert(0, '/scripts')
    # Try loading .env from container path
    env_path = '/app/.env'
elif os.path.isdir(os.path.join(os.path.dirname(__file__), 'equities_predictor')):
    sys.path.insert(0, os.path.dirname(__file__))
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
else:
    env_path = '.env'

# Simple .env loader (no dependency on python-dotenv)
def load_dotenv(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

load_dotenv(env_path)

from equities_predictor.db import connect, upsert_ohlcv

# ── Configuration ─────────────────────────────────────────────────────────

SYMBOLS = ["NVDA", "META", "AVGO", "QQQ", "XLK"]

# Earnings dates (AMC) — reaction day is next trading day
NVDA_EARNINGS_DATES = [
    date(2024, 2, 21), date(2024, 5, 22), date(2024, 8, 28), date(2024, 11, 20),
    date(2025, 2, 26), date(2025, 5, 28), date(2025, 8, 27), date(2025, 11, 19),
]

ALPACA_BASE_URL = "https://data.alpaca.markets/v2/stocks"
SOURCE = "alpaca_historical"
BATCH_PREFIX = "eq_1m_alpaca"

# Rate limiting
REQUEST_DELAY_SEC = 0.5


def get_api_keys() -> tuple[str, str]:
    """Get Alpaca API keys from environment."""
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        print("ERROR: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")
        print("Get free keys at https://alpaca.markets (paper account, no funding required)")
        print("Set in Backend/.env:")
        print("  ALPACA_API_KEY=PK...")
        print("  ALPACA_SECRET_KEY=...")
        sys.exit(1)
    return api_key, secret_key


def next_trading_day(d: date) -> date:
    """Find next trading day (simple weekday check — Alpaca handles holidays)."""
    from equities_predictor.calendar import is_trading_day
    candidate = d + timedelta(days=1)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    raise RuntimeError(f"No trading day found within 10 days of {d}")


def get_reaction_dates() -> list[date]:
    return [next_trading_day(ed) for ed in NVDA_EARNINGS_DATES]


def fetch_alpaca_bars(
    symbol: str,
    reaction_date: date,
    api_key: str,
    secret_key: str,
    feed: str = "sip",
) -> list[dict]:
    """Fetch 1m bars from Alpaca for a single symbol/date. Returns list of bar dicts."""
    # Explicit UTC timestamps: 04:00 ET pre-market to 20:00 ET post-market
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    start_dt = datetime.combine(reaction_date, dtime(8, 0), tzinfo=timezone.utc)  # 4 AM ET in winter ≈ 9 UTC
    end_dt = datetime.combine(reaction_date + timedelta(days=1), dtime(1, 0), tzinfo=timezone.utc)  # 8 PM ET

    # Actually use ET-based times for clarity
    start_dt = datetime.combine(reaction_date, dtime(4, 0), tzinfo=et).astimezone(timezone.utc)
    end_dt = datetime.combine(reaction_date, dtime(20, 0), tzinfo=et).astimezone(timezone.utc)

    url = f"{ALPACA_BASE_URL}/{symbol}/bars"
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
        "Accept": "application/json",
    }
    params = {
        "timeframe": "1Min",
        "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adjustment": "split",
        "feed": feed,
        "limit": 10000,
    }

    all_bars = []
    page_token = None

    while True:
        if page_token:
            params["page_token"] = page_token
        elif "page_token" in params:
            del params["page_token"]

        resp = http_requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code == 403 and feed == "sip":
            # SIP not available on free tier, fall back to IEX
            return fetch_alpaca_bars(symbol, reaction_date, api_key, secret_key, feed="iex")

        resp.raise_for_status()
        data = resp.json()

        bars = data.get("bars") or []
        for bar in bars:
            # Alpaca bar format: t, o, h, l, c, v, n, vw
            bar_ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(timezone.utc)
            all_bars.append({
                "symbol": symbol,
                "timeframe": "1m",
                "bar_start_ts": bar_ts,
                "open": float(bar["o"]),
                "high": float(bar["h"]),
                "low": float(bar["l"]),
                "close": float(bar["c"]),
                "volume": float(bar["v"]),
                "is_final": True,
            })

        page_token = data.get("next_page_token")
        if not page_token:
            break

        time.sleep(REQUEST_DELAY_SEC)

    return all_bars


def main():
    print("=" * 60)
    print("NVDA Reaction Day 1m Backfill — Alpaca Markets")
    print("=" * 60)

    api_key, secret_key = get_api_keys()
    print(f"API key: {api_key[:4]}...{api_key[-4:]}")

    conn = connect()
    batch_id = f"{BATCH_PREFIX}_{uuid.uuid4().hex[:8]}"
    print(f"Batch: {batch_id}")

    reaction_dates = get_reaction_dates()
    print(f"Reaction dates: {[str(d) for d in reaction_dates]}")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Total API calls: ~{len(reaction_dates) * len(SYMBOLS)}")
    print()

    total_bars = 0
    total_calls = 0
    errors = []

    for rd_idx, (rd, ed) in enumerate(zip(reaction_dates, NVDA_EARNINGS_DATES)):
        print(f"\n[{rd_idx+1}/{len(reaction_dates)}] Reaction day {rd} (earnings {ed})")
        print("-" * 50)

        for symbol in SYMBOLS:
            total_calls += 1
            print(f"  {symbol}...", end=" ", flush=True)

            try:
                bars = fetch_alpaca_bars(symbol, rd, api_key, secret_key)
            except Exception as e:
                msg = f"{symbol} {rd}: {e}"
                print(f"FAILED: {e}")
                errors.append(msg)
                time.sleep(REQUEST_DELAY_SEC)
                continue

            if not bars:
                print("0 bars (empty)")
                time.sleep(REQUEST_DELAY_SEC)
                continue

            n = upsert_ohlcv(conn, bars, source=SOURCE, batch_id=batch_id)
            conn.commit()
            total_bars += n

            earliest = bars[0]["bar_start_ts"].strftime("%H:%M")
            latest = bars[-1]["bar_start_ts"].strftime("%H:%M")
            print(f"{len(bars)} bars ({earliest}→{latest} UTC), {n} upserted")

            time.sleep(REQUEST_DELAY_SEC)

    print(f"\n{'='*60}")
    print(f"COMPLETE")
    print(f"  API calls: {total_calls}")
    print(f"  Total bars: {total_bars}")
    print(f"  Errors: {len(errors)}")
    if errors:
        for e in errors:
            print(f"    - {e}")

    # Verification query
    with conn.cursor() as cur:
        cur.execute("""
            SELECT symbol, COUNT(*) as bars,
                   MIN(bar_start_ts)::date as earliest,
                   MAX(bar_start_ts)::date as latest
            FROM ohlcv
            WHERE timeframe = '1m'
              AND source = %s
            GROUP BY symbol
            ORDER BY symbol;
        """, (SOURCE,))
        rows = cur.fetchall()
        if rows:
            print(f"\nVerification:")
            print(f"{'Symbol':<8} {'Bars':>8} {'Earliest':<12} {'Latest':<12}")
            print("-" * 44)
            for r in rows:
                print(f"{r['symbol']:<8} {r['bars']:>8} {r['earliest']}   {r['latest']}")

    conn.close()


if __name__ == "__main__":
    main()
