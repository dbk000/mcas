#!/usr/bin/env python3
"""
Map MCAS spot universe to Binance Futures USDT-M perpetuals.
Shows coverage, identifies gaps, and tests API endpoints for
funding rates, OI, long/short ratio, and taker buy/sell.

Usage:
    docker compose run --rm -T worker python3 /scripts/map_futures_symbols.py
"""

import os
import sys
import time
import json
import requests
import psycopg
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ─── Env setup ────────────────────────────────────────────────────────────────

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

DATABASE_URL = os.environ["DATABASE_URL"]
FAPI_BASE = "https://fapi.binance.com"


def get_spot_symbols():
    """Get distinct symbols from MCAS OHLCV database."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv 
                WHERE timeframe = '1h' 
                ORDER BY symbol
            """)
            return {row[0] for row in cur}


def get_futures_info():
    """Get Binance USDT-M perpetual contract info."""
    resp = requests.get(f"{FAPI_BASE}/fapi/v1/exchangeInfo", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    
    perps = {}
    for s in data["symbols"]:
        if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT" and s["status"] == "TRADING":
            perps[s["symbol"]] = {
                "baseAsset": s["baseAsset"],
                "marginAsset": s.get("marginAsset", "USDT"),
                "pricePrecision": s["pricePrecision"],
                "quantityPrecision": s["quantityPrecision"],
            }
    return perps


def get_funding_intervals():
    """Get funding rate intervals (some pairs use 4h instead of 8h)."""
    resp = requests.get(f"{FAPI_BASE}/fapi/v1/fundingInfo", timeout=10)
    if resp.status_code != 200:
        print(f"  Warning: fundingInfo endpoint returned {resp.status_code}")
        return {}
    
    intervals = {}
    for item in resp.json():
        sym = item.get("symbol", "")
        hours = item.get("fundingIntervalHours", 8)
        intervals[sym] = hours
    return intervals


def test_endpoint(name, url, params, sample_symbol):
    """Test a single API endpoint and report."""
    params["symbol"] = sample_symbol
    try:
        resp = requests.get(url, params=params, timeout=10)
        weight = resp.headers.get("X-MBX-USED-WEIGHT-1M", "?")
        if resp.status_code == 200:
            data = resp.json()
            n_records = len(data) if isinstance(data, list) else 1
            print(f"  ✓ {name}: {n_records} records, weight used: {weight}")
            if isinstance(data, list) and len(data) > 0:
                print(f"    Sample keys: {list(data[0].keys())}")
                return data[0]
        else:
            print(f"  ✗ {name}: HTTP {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        print(f"  ✗ {name}: {e}")
    return None


def test_api_endpoints(sample_symbol):
    """Test all Binance Futures data endpoints we plan to use."""
    print(f"\n═══ Testing API Endpoints (sample: {sample_symbol}) ═══")
    
    # Funding Rate History
    test_endpoint(
        "Funding Rate History",
        f"{FAPI_BASE}/fapi/v1/fundingRate",
        {"limit": 5},
        sample_symbol
    )
    time.sleep(0.2)
    
    # Open Interest (current)
    test_endpoint(
        "Open Interest (current)",
        f"{FAPI_BASE}/fapi/v1/openInterest",
        {},
        sample_symbol
    )
    time.sleep(0.2)
    
    # Open Interest Statistics (historical, 1h)
    test_endpoint(
        "OI Statistics (1h hist)",
        f"{FAPI_BASE}/futures/data/openInterestHist",
        {"period": "1h", "limit": 5},
        sample_symbol
    )
    time.sleep(0.2)
    
    # Global Long/Short Account Ratio
    test_endpoint(
        "Global Long/Short Ratio",
        f"{FAPI_BASE}/futures/data/globalLongShortAccountRatio",
        {"period": "1h", "limit": 5},
        sample_symbol
    )
    time.sleep(0.2)
    
    # Top Trader Long/Short Position Ratio
    test_endpoint(
        "Top Trader L/S Position Ratio",
        f"{FAPI_BASE}/futures/data/topLongShortPositionRatio",
        {"period": "1h", "limit": 5},
        sample_symbol
    )
    time.sleep(0.2)
    
    # Top Trader Long/Short Account Ratio
    test_endpoint(
        "Top Trader L/S Account Ratio",
        f"{FAPI_BASE}/futures/data/topLongShortAccountRatio",
        {"period": "1h", "limit": 5},
        sample_symbol
    )
    time.sleep(0.2)
    
    # Taker Buy/Sell Volume
    test_endpoint(
        "Taker Buy/Sell Volume",
        f"{FAPI_BASE}/futures/data/takerlongshortRatio",
        {"period": "1h", "limit": 5},
        sample_symbol
    )
    time.sleep(0.2)


def estimate_collection_load(n_symbols):
    """Estimate API weight and time for a full collection cycle."""
    print(f"\n═══ Collection Load Estimate ({n_symbols} symbols) ═══")
    
    # Endpoints and their weights per request
    endpoints = {
        "openInterestHist (1h)": {"weight": 5, "frequency": "hourly"},
        "globalLongShortAccountRatio": {"weight": 1, "frequency": "hourly"},
        "topLongShortPositionRatio": {"weight": 1, "frequency": "hourly"},
        "topLongShortAccountRatio": {"weight": 1, "frequency": "hourly"},
        "takerlongshortRatio": {"weight": 1, "frequency": "hourly"},
        "fundingRate": {"weight": 1, "frequency": "4-hourly"},
    }
    
    total_hourly_weight = 0
    for name, info in endpoints.items():
        w = info["weight"] * n_symbols
        if info["frequency"] == "4-hourly":
            w_per_hour = w / 4
        else:
            w_per_hour = w
        total_hourly_weight += w_per_hour
        print(f"  {name}: {info['weight']}w × {n_symbols} = {w}w/{info['frequency']} ({w_per_hour:.0f}w/hr)")
    
    print(f"\n  Total weight per hour: ~{total_hourly_weight:.0f}")
    print(f"  Binance Futures limit: 2400 weight/min = 144,000/hr")
    print(f"  Utilization: {100 * total_hourly_weight / 144000:.2f}%")
    
    # Estimate requests and time
    total_requests = n_symbols * 5  # 5 hourly endpoints
    requests_per_sec = 20  # conservative with ThreadPoolExecutor
    est_seconds = total_requests / requests_per_sec
    print(f"\n  Requests per hourly cycle: ~{total_requests}")
    print(f"  Estimated wall-clock time: ~{est_seconds:.0f}s ({est_seconds/60:.1f}min)")


def main():
    print("═══ MCAS → Binance Futures Symbol Mapping ═══\n")
    
    # 1. Get spot universe
    print("Loading MCAS spot universe from DB...")
    spot_symbols = get_spot_symbols()
    print(f"  MCAS spot universe: {len(spot_symbols)} symbols")
    
    # 2. Get futures info
    print("\nFetching Binance USDT-M perpetuals...")
    futures_perps = get_futures_info()
    print(f"  Active USDT-M perpetuals: {len(futures_perps)} symbols")
    
    # 3. Compute overlap
    futures_set = set(futures_perps.keys())
    overlap = spot_symbols & futures_set
    spot_only = spot_symbols - futures_set
    futures_only = futures_set - spot_symbols
    
    print(f"\n═══ Coverage Analysis ═══")
    print(f"  Overlap (spot + futures): {len(overlap)} symbols ({100*len(overlap)/len(spot_symbols):.1f}% of spot universe)")
    print(f"  Spot-only (no futures):   {len(spot_only)} symbols")
    print(f"  Futures-only (not in MCAS): {len(futures_only)} symbols")
    
    # 4. Funding intervals
    print("\nFetching funding rate intervals...")
    intervals = get_funding_intervals()
    interval_counts = defaultdict(int)
    for sym in overlap:
        h = intervals.get(sym, 8)
        interval_counts[h] += 1
    print(f"  Funding intervals for overlapping symbols:")
    for h, count in sorted(interval_counts.items()):
        print(f"    {h}h: {count} symbols")
    
    # 5. Show spot-only symbols (the gaps)
    if spot_only:
        print(f"\n═══ Spot-Only Symbols (no futures data available) ═══")
        print(f"  First 30: {sorted(spot_only)[:30]}")
        if len(spot_only) > 30:
            print(f"  ... and {len(spot_only) - 30} more")
    
    # 6. Show futures-only (potential additions to MCAS)
    if futures_only:
        print(f"\n═══ Futures-Only (not in MCAS spot universe) ═══")
        print(f"  Count: {len(futures_only)}")
        print(f"  Sample: {sorted(futures_only)[:20]}")
    
    # 7. Test API endpoints
    sample = "BTCUSDT" if "BTCUSDT" in overlap else sorted(overlap)[0]
    test_api_endpoints(sample)
    
    # 8. Estimate collection load
    estimate_collection_load(len(overlap))
    
    # 9. Write mapping to JSON for downstream use
    mapping = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spot_count": len(spot_symbols),
        "futures_count": len(futures_perps),
        "overlap_count": len(overlap),
        "coverage_pct": round(100 * len(overlap) / len(spot_symbols), 1),
        "overlap_symbols": sorted(overlap),
        "spot_only_symbols": sorted(spot_only),
        "futures_only_symbols": sorted(futures_only),
    }
    
    out_path = os.path.join(os.path.dirname(__file__), "futures_symbol_mapping.json")
    with open(out_path, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"\n═══ Mapping written to {out_path} ═══")
    
    print(f"\n═══ DONE ═══")
    print(f"Next step: If coverage >{50}%, proceed to build derivatives_collector.py")


if __name__ == "__main__":
    main()
