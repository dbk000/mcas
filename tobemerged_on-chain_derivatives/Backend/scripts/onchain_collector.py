#!/usr/bin/env python3
"""
On-Chain Data Collector — Unified Multi-Source Pipeline
=======================================================

Collects on-chain and market data from multiple sources:
  Phase 1 (FREE):  DefiLlama (TVL, DEX, stablecoins), CoinGecko (market metadata),
                   Etherscan/BSCScan (holder stats)
  Phase 2 (PAID):  Coinglass (liquidations, extended OI), Santiment (exchange flows,
                   whale txns, holder distribution, network activity, social)

Architecture (mirrors derivatives_collector.py):
  1. ThreadPoolExecutor(20) per source for parallel HTTP fetches
  2. Separate DB writer thread with batched multi-table inserts
  3. Per-source rate limiters
  4. Resume from existing data (queries max timestamp per table per symbol)
  5. Batched INSERT ... ON CONFLICT DO NOTHING
  6. Feature materialization after each collection cycle

API keys (in .env):
  COINGLASS_API_KEY=xxx       # Required for Phase 2 Coinglass
  SANTIMENT_API_KEY=xxx       # Required for Phase 2 Santiment
  ETHERSCAN_API_KEY=xxx       # Optional (free key, higher rate limit)
  BSCSCAN_API_KEY=xxx         # Optional (free key, higher rate limit)
  COINGECKO_API_KEY=xxx       # Optional (free key for higher rate limit)

Modes:
  --backfill         Fetch max history (365d where available), then enter live mode
  --backfill-only    Backfill and exit
  --source X         Only collect from source X (defillama|coingecko|etherscan|coinglass|santiment)
  --symbols N        Limit to first N symbols (for testing)
  --materialize-only Only run feature materialization (no collection)

Usage:
    tmux new -s onchain
    docker compose run --rm -T worker python3 /scripts/onchain_collector.py --backfill
    # Or as a persistent Docker service:
    docker compose up -d onchain_collector
"""

import os
import sys
import time
import json
import queue
import logging
import argparse
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

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
if os.path.isdir('/app'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))

import requests as req_lib
import psycopg
import numpy as np

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────
N_WORKERS = 20
DB_BATCH_SIZE = 5_000
MAX_RETRIES = 5

# Collection intervals (continuous mode)
COLLECTION_INTERVAL_S = int(os.environ.get("ONCHAIN_COLLECTION_INTERVAL_S", "3600"))

# API keys
COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY", "")
SANTIMENT_API_KEY = os.environ.get("SANTIMENT_API_KEY", "")
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.environ.get("BSCSCAN_API_KEY", "")
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")

# Feature materialization lookback
FEATURE_LOOKBACK_HOURS = 800  # ~33 days


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITERS (per source)
# ═══════════════════════════════════════════════════════════════════════════════

class SimpleRateLimiter:
    """Thread-safe rate limiter based on calls per minute."""

    def __init__(self, calls_per_minute, name=""):
        self._lock = threading.Lock()
        self._max_calls = calls_per_minute
        self._window_start = time.monotonic()
        self._count = 0
        self._total = 0
        self._total_wait = 0.0
        self._name = name

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                if now - self._window_start >= 60.0:
                    self._count = 0
                    self._window_start = now
                if self._count < self._max_calls:
                    self._count += 1
                    self._total += 1
                    return
            wait = 1.0
            self._total_wait += wait
            time.sleep(wait)

    @property
    def stats(self):
        with self._lock:
            return {"total": self._total, "wait_s": round(self._total_wait, 1)}


# Source-specific rate limiters
RATE_LIMITERS = {
    "defillama": SimpleRateLimiter(120, "defillama"),     # ~2/sec, generous
    "coingecko": SimpleRateLimiter(25, "coingecko"),      # Free: ~30/min, be safe
    "etherscan": SimpleRateLimiter(240, "etherscan"),     # 5/sec with key
    "bscscan": SimpleRateLimiter(240, "bscscan"),
    "coinglass": SimpleRateLimiter(25, "coinglass"),      # Open API: 30/min
    "santiment": SimpleRateLimiter(120, "santiment"),     # Basic: 300/min
}


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP + DB INFRASTRUCTURE (same as derivatives_collector.py)
# ═══════════════════════════════════════════════════════════════════════════════

_thread_local = threading.local()


def _get_session():
    if not hasattr(_thread_local, 'session'):
        s = req_lib.Session()
        adapter = req_lib.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
            max_retries=req_lib.adapters.Retry(total=2, backoff_factor=0.5)
        )
        s.mount('https://', adapter)
        _thread_local.session = s
    return _thread_local.session


def _api_get(url, params=None, headers=None, rate_limiter=None, timeout=30):
    """Rate-limited GET with retries. Returns parsed JSON or None."""
    session = _get_session()
    if rate_limiter:
        rate_limiter.acquire()

    retries = 0
    while retries < MAX_RETRIES:
        try:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                logger.warning("Rate limited on %s, sleeping %ds", url[:60], retry_after)
                time.sleep(retry_after)
                retries += 1
                continue
            resp.raise_for_status()
            return resp.json()
        except (req_lib.exceptions.RequestException, ValueError) as e:
            retries += 1
            if retries >= MAX_RETRIES:
                return None
            time.sleep(1.5 * retries)
    return None


def _api_post(url, json_body=None, headers=None, rate_limiter=None, timeout=30):
    """Rate-limited POST (for GraphQL APIs). Returns parsed JSON or None."""
    session = _get_session()
    if rate_limiter:
        rate_limiter.acquire()

    retries = 0
    while retries < MAX_RETRIES:
        try:
            resp = session.post(url, json=json_body, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                logger.warning("Rate limited on %s, sleeping %ds", url[:60], retry_after)
                time.sleep(retry_after)
                retries += 1
                continue
            resp.raise_for_status()
            return resp.json()
        except (req_lib.exceptions.RequestException, ValueError) as e:
            retries += 1
            if retries >= MAX_RETRIES:
                return None
            time.sleep(1.5 * retries)
    return None


def get_max_timestamps(db_url, table, ts_column, symbols, extra_where=""):
    """Get max timestamp per symbol from a table for resume."""
    result = {}
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT symbol, MAX({ts_column})
                    FROM {table}
                    WHERE symbol = ANY(%s) {extra_where}
                    GROUP BY symbol
                """, (symbols,))
                for row in cur:
                    ts = row[1]
                    if ts and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    result[row[0]] = ts
    except Exception as e:
        logger.warning("Could not read %s for resume: %s", table, e)
    return result


# ─── DB Writer Thread ─────────────────────────────────────────────────────────

_INSERT_SQL = {
    'protocol_tvl': """
        INSERT INTO protocol_tvl (symbol, timestamp, tvl_usd, chain, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'dex_volume': """
        INSERT INTO dex_volume (symbol, timestamp, volume_usd, source_dex, chain, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'stablecoin_supply': """
        INSERT INTO stablecoin_supply (symbol, timestamp, total_circulating, total_circulating_usd, chain, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'market_metadata': """
        INSERT INTO market_metadata (symbol, timestamp, market_cap_usd, market_cap_rank,
            circulating_supply, total_supply, max_supply,
            twitter_followers, reddit_subscribers, github_stars, github_forks, github_commits_4w,
            source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'token_holder_stats': """
        INSERT INTO token_holder_stats (symbol, timestamp, chain, unique_holders, top10_pct, top50_pct,
            contract_address, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'liquidations': """
        INSERT INTO liquidations (symbol, timestamp, long_liquidation_usd, short_liquidation_usd,
            long_count, short_count, period, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'oi_history_extended': """
        INSERT INTO oi_history_extended (symbol, timestamp, open_interest_usd, oi_change_pct,
            exchange, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'exchange_flows': """
        INSERT INTO exchange_flows (symbol, timestamp, exchange_inflow, exchange_outflow,
            exchange_netflow, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'whale_transactions': """
        INSERT INTO whale_transactions (symbol, timestamp, whale_tx_count, whale_tx_volume_usd,
            source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'network_activity': """
        INSERT INTO network_activity (symbol, timestamp, active_addresses_24h, network_growth,
            transaction_volume, dev_activity, social_volume, social_dominance, source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
    'holder_distribution': """
        INSERT INTO holder_distribution (symbol, timestamp, tier, address_count, total_held,
            source, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """,
}


def db_writer_loop(db_url, write_queue, stats):
    """Dedicated DB writer thread. Same pattern as derivatives_collector.py."""
    conn = psycopg.connect(db_url)
    batches = defaultdict(list)
    total_written = 0

    try:
        while True:
            try:
                item = write_queue.get(timeout=2)
            except queue.Empty:
                for table_name, rows in list(batches.items()):
                    if rows:
                        _flush_batch(conn, table_name, rows)
                        total_written += len(rows)
                        stats['rows_written'] = total_written
                batches.clear()
                continue

            if item is None:
                for table_name, rows in list(batches.items()):
                    if rows:
                        _flush_batch(conn, table_name, rows)
                        total_written += len(rows)
                        stats['rows_written'] = total_written
                break

            table_name, rows = item
            batches[table_name].extend(rows)
            if len(batches[table_name]) >= DB_BATCH_SIZE:
                _flush_batch(conn, table_name, batches[table_name])
                total_written += len(batches[table_name])
                stats['rows_written'] = total_written
                batches[table_name] = []
    finally:
        conn.close()

    logger.info("DB writer: %d total rows written", total_written)


def _flush_batch(conn, table_name, rows):
    if not rows:
        return
    sql = _INSERT_SQL.get(table_name)
    if not sql:
        logger.error("No INSERT SQL for table: %s", table_name)
        return
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    except Exception as e:
        logger.error("DB flush failed for %s (%d rows): %s", table_name, len(rows), e)
        conn.rollback()


# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOL MAPPING LOADER
# ═══════════════════════════════════════════════════════════════════════════════

def load_symbol_mapping(db_url):
    """Load symbol mappings from DB or JSON file.

    Returns: dict[source] -> dict[binance_symbol] -> external_id
    """
    mappings = defaultdict(dict)

    # Try DB first
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol, source, external_id, chain, contract_address FROM symbol_mapping")
                for row in cur:
                    mappings[row[1]][row[0]] = {
                        "external_id": row[2],
                        "chain": row[3],
                        "contract_address": row[4],
                    }
        if mappings:
            total = sum(len(v) for v in mappings.values())
            logger.info("Loaded %d mappings from DB (%s)", total,
                        ", ".join(f"{k}={len(v)}" for k, v in sorted(mappings.items())))
            return dict(mappings)
    except Exception as e:
        logger.warning("Could not load mappings from DB: %s", e)

    # Fall back to JSON file
    for candidate in [
        os.path.join(os.path.dirname(__file__), 'onchain_symbol_mapping.json'),
        '/scripts/onchain_symbol_mapping.json',
    ]:
        if os.path.exists(candidate):
            with open(candidate) as f:
                data = json.load(f)
            for sym, info in data.get("mappings", {}).items():
                if "coingecko_id" in info:
                    mappings["coingecko"][sym] = {"external_id": info["coingecko_id"],
                                                  "chain": info.get("chain"),
                                                  "contract_address": info.get("contract_address")}
                if "defillama_slug" in info:
                    mappings["defillama"][sym] = {"external_id": info["defillama_slug"]}
                if "santiment_slug" in info:
                    mappings["santiment"][sym] = {"external_id": info["santiment_slug"]}
                # Derive etherscan/bscscan
                if info.get("contract_address"):
                    if info.get("chain") == "ethereum":
                        mappings["etherscan"][sym] = {"external_id": info["contract_address"],
                                                       "chain": "ethereum",
                                                       "contract_address": info["contract_address"]}
                    elif info.get("chain") == "bsc":
                        mappings["bscscan"][sym] = {"external_id": info["contract_address"],
                                                     "chain": "bsc",
                                                     "contract_address": info["contract_address"]}
            total = sum(len(v) for v in mappings.values())
            logger.info("Loaded %d mappings from %s", total, candidate)
            return dict(mappings)

    logger.warning("No symbol mappings found. Run map_onchain_symbols.py first.")
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE: DefiLlama (FREE)
# ═══════════════════════════════════════════════════════════════════════════════

def collect_defillama(symbols, mapping, write_queue, batch_id, backfill_days=365):
    """Collect TVL, stablecoin supply from DefiLlama."""
    rl = RATE_LIMITERS["defillama"]
    dl_map = mapping.get("defillama", {})

    if not dl_map:
        logger.warning("  DefiLlama: no symbol mappings available, skipping")
        return {"protocol_tvl": 0, "stablecoin_supply": 0}

    stats = defaultdict(int)
    mapped_symbols = [s for s in symbols if s in dl_map]
    logger.info("  DefiLlama: collecting TVL for %d protocols", len(mapped_symbols))

    # ── Protocol TVL ──────────────────────────────────────────────────────────
    completed = 0
    failed = 0
    t0 = time.time()

    for sym in mapped_symbols:
        slug = dl_map[sym]["external_id"]
        try:
            data = _api_get(
                f"https://api.llama.fi/protocol/{slug}",
                rate_limiter=rl,
            )
            if not data or "tvl" not in data:
                failed += 1
                continue

            tvl_history = data.get("tvl", [])
            if not tvl_history:
                continue

            # Filter to backfill window
            cutoff_ts = time.time() - (backfill_days * 86400)
            rows = []
            for point in tvl_history:
                ts_unix = point.get("date", 0)
                if ts_unix < cutoff_ts:
                    continue
                ts = datetime.fromtimestamp(ts_unix, tz=timezone.utc)
                tvl = point.get("totalLiquidityUSD", 0)
                if tvl and tvl > 0:
                    rows.append((sym, ts, float(tvl), None, 'defillama', batch_id))

            if rows:
                write_queue.put(('protocol_tvl', rows))
                stats['protocol_tvl'] += len(rows)

        except Exception as e:
            logger.warning("    DefiLlama %s failed: %s", sym, e)
            failed += 1

        completed += 1
        if completed % 25 == 0 or completed == len(mapped_symbols):
            elapsed = time.time() - t0
            logger.info("    TVL: %d/%d protocols (%.1f/s), %d rows, %d failed",
                        completed, len(mapped_symbols),
                        completed / elapsed if elapsed > 0 else 0,
                        stats['protocol_tvl'], failed)

    # ── Stablecoin Supply (market-level) ──────────────────────────────────────
    logger.info("  DefiLlama: collecting stablecoin supply history...")
    try:
        data = _api_get("https://stablecoins.llama.fi/stablecoins?includePrices=false",
                        rate_limiter=rl)
        if data and "peggedAssets" in data:
            stables_of_interest = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD"}
            for stable in data["peggedAssets"]:
                sym_name = stable.get("symbol", "")
                if sym_name not in stables_of_interest:
                    continue

                stable_id = stable.get("id")
                if not stable_id:
                    continue

                # Get history
                hist = _api_get(
                    f"https://stablecoins.llama.fi/stablecoin/{stable_id}",
                    rate_limiter=rl,
                )
                if not hist or "tokens" not in hist:
                    continue

                rows = []
                for entry in hist.get("tokens", []):
                    ts_unix = entry.get("date", 0)
                    if ts_unix < time.time() - (backfill_days * 86400):
                        continue
                    ts = datetime.fromtimestamp(ts_unix, tz=timezone.utc)
                    circ = 0
                    for chain_data in (entry.get("tokens") or {}).values():
                        circ += chain_data.get("peggedUSD", 0)
                    if circ > 0:
                        rows.append((sym_name, ts, float(circ), float(circ), 'all',
                                     'defillama', batch_id))

                if rows:
                    write_queue.put(('stablecoin_supply', rows))
                    stats['stablecoin_supply'] += len(rows)
                    logger.info("    Stablecoin %s: %d data points", sym_name, len(rows))

    except Exception as e:
        logger.warning("    Stablecoin supply collection failed: %s", e)

    logger.info("  DefiLlama complete: tvl=%d, stablecoins=%d rows",
                stats['protocol_tvl'], stats['stablecoin_supply'])
    return dict(stats)


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE: CoinGecko (FREE TIER)
# ═══════════════════════════════════════════════════════════════════════════════

def collect_coingecko(symbols, mapping, write_queue, batch_id):
    """Collect market metadata from CoinGecko. Daily granularity."""
    rl = RATE_LIMITERS["coingecko"]
    cg_map = mapping.get("coingecko", {})

    if not cg_map:
        logger.warning("  CoinGecko: no symbol mappings available, skipping")
        return {"market_metadata": 0}

    stats = defaultdict(int)
    mapped = [s for s in symbols if s in cg_map]
    logger.info("  CoinGecko: collecting market data for %d symbols", len(mapped))

    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    completed = 0
    failed = 0
    t0 = time.time()
    now_ts = datetime.now(timezone.utc)

    for sym in mapped:
        cg_id = cg_map[sym]["external_id"]
        try:
            data = _api_get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "true",
                    "developer_data": "true",
                },
                headers=headers,
                rate_limiter=rl,
            )
            if not data:
                failed += 1
                continue

            md = data.get("market_data", {})
            community = data.get("community_data", {})
            dev = data.get("developer_data", {})

            row = (
                sym,
                now_ts,
                _safe_float(md.get("market_cap", {}).get("usd")),
                _safe_int(md.get("market_cap_rank")),
                _safe_float(md.get("circulating_supply")),
                _safe_float(md.get("total_supply")),
                _safe_float(md.get("max_supply")),
                _safe_int(community.get("twitter_followers")),
                _safe_int(community.get("reddit_subscribers")),
                _safe_int((dev.get("stars") if isinstance(dev.get("stars"), int)
                           else None)),
                _safe_int((dev.get("forks") if isinstance(dev.get("forks"), int)
                           else None)),
                _safe_int(dev.get("commit_count_4_weeks")),
                'coingecko',
                batch_id,
            )
            write_queue.put(('market_metadata', [row]))
            stats['market_metadata'] += 1

        except Exception as e:
            logger.warning("    CoinGecko %s failed: %s", sym, e)
            failed += 1

        completed += 1
        if completed % 50 == 0 or completed == len(mapped):
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (len(mapped) - completed) / rate / 60 if rate > 0 else 0
            logger.info("    CoinGecko: %d/%d (%.1f/s), ETA: %.0fm, %d failed",
                        completed, len(mapped), rate, eta, failed)

    logger.info("  CoinGecko complete: %d metadata rows, %d failed",
                stats['market_metadata'], failed)
    return dict(stats)


def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE: Etherscan / BSCScan (FREE)
# ═══════════════════════════════════════════════════════════════════════════════

def collect_etherscan(symbols, mapping, write_queue, batch_id):
    """Collect token holder counts from Etherscan + BSCScan."""
    stats = defaultdict(int)
    now_ts = datetime.now(timezone.utc)

    for chain_source in ['etherscan', 'bscscan']:
        chain_map = mapping.get(chain_source, {})
        if not chain_map:
            continue

        chain = "ethereum" if chain_source == "etherscan" else "bsc"
        api_base = ("https://api.etherscan.io/api" if chain == "ethereum"
                    else "https://api.bscscan.com/api")
        api_key = ETHERSCAN_API_KEY if chain == "ethereum" else BSCSCAN_API_KEY
        rl = RATE_LIMITERS[chain_source]

        mapped = [s for s in symbols if s in chain_map]
        logger.info("  %s: collecting holder stats for %d tokens", chain_source, len(mapped))

        completed = 0
        failed = 0
        t0 = time.time()

        for sym in mapped:
            contract = chain_map[sym].get("contract_address")
            if not contract:
                continue

            try:
                # Token holder list (page 1, sort by quantity desc → top holders)
                params = {
                    "module": "token",
                    "action": "tokenholderlist",
                    "contractaddress": contract,
                    "page": "1",
                    "offset": "50",
                }
                if api_key:
                    params["apikey"] = api_key

                data = _api_get(api_base, params=params, rate_limiter=rl)

                if data and data.get("status") == "1" and data.get("result"):
                    holders = data["result"]
                    unique_count = len(holders)  # This is just top 50
                    # We don't get total holder count from this endpoint
                    # but we get top holder concentration

                    # Compute top10/top50 percentage (approximate from balances)
                    # TokenHolderList returns: TokenHolderAddress, TokenHolderQuantity
                    total_held = sum(float(h.get("TokenHolderQuantity", 0)) for h in holders)
                    if total_held > 0 and len(holders) >= 10:
                        top10_held = sum(float(holders[i].get("TokenHolderQuantity", 0))
                                         for i in range(min(10, len(holders))))
                        top50_held = sum(float(holders[i].get("TokenHolderQuantity", 0))
                                         for i in range(min(50, len(holders))))
                        top10_pct = (top10_held / total_held) * 100
                        top50_pct = (top50_held / total_held) * 100
                    else:
                        top10_pct = None
                        top50_pct = None

                    row = (sym, now_ts, chain, unique_count, top10_pct, top50_pct,
                           contract, chain_source, batch_id)
                    write_queue.put(('token_holder_stats', [row]))
                    stats['token_holder_stats'] += 1

            except Exception as e:
                logger.warning("    %s %s failed: %s", chain_source, sym, e)
                failed += 1

            completed += 1
            if completed % 50 == 0 or completed == len(mapped):
                logger.info("    %s: %d/%d tokens, %d rows, %d failed",
                            chain_source, completed, len(mapped),
                            stats['token_holder_stats'], failed)

    logger.info("  Etherscan/BSCScan complete: %d holder stat rows", stats['token_holder_stats'])
    return dict(stats)


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE: Coinglass (PAID — requires COINGLASS_API_KEY)
# ═══════════════════════════════════════════════════════════════════════════════

def collect_coinglass(symbols, mapping, write_queue, batch_id, backfill_days=365):
    """Collect liquidation data and extended OI from Coinglass."""
    if not COINGLASS_API_KEY:
        logger.info("  Coinglass: COINGLASS_API_KEY not set, skipping")
        return {}

    rl = RATE_LIMITERS["coinglass"]
    headers = {"coinglassSecret": COINGLASS_API_KEY}
    stats = defaultdict(int)

    # Coinglass uses same symbols as Binance futures
    futures_symbols = [s for s in symbols if s.endswith("USDT")]
    logger.info("  Coinglass: collecting for %d symbols", len(futures_symbols))

    completed = 0
    failed = 0
    t0 = time.time()

    for sym in futures_symbols:
        # Strip USDT for Coinglass API (some endpoints want base symbol)
        base = sym[:-4] if sym.endswith("USDT") else sym

        # ── Liquidation history ───────────────────────────────────────────
        try:
            data = _api_get(
                "https://open-api.coinglass.com/public/v2/liquidation_history",
                params={"symbol": base, "time_type": "h1"},
                headers=headers,
                rate_limiter=rl,
            )
            if data and data.get("success") and data.get("data"):
                rows = []
                for item in data["data"]:
                    ts_ms = item.get("t") or item.get("createTime") or item.get("timestamp")
                    if not ts_ms:
                        continue
                    ts = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                    long_usd = float(item.get("longLiquidationUsd", 0) or 0)
                    short_usd = float(item.get("shortLiquidationUsd", 0) or 0)
                    rows.append((
                        sym, ts, long_usd, short_usd,
                        _safe_int(item.get("longCount")),
                        _safe_int(item.get("shortCount")),
                        '1h', 'coinglass', batch_id,
                    ))
                if rows:
                    write_queue.put(('liquidations', rows))
                    stats['liquidations'] += len(rows)
        except Exception as e:
            logger.warning("    Coinglass liq %s failed: %s", sym, e)

        # ── Extended OI history ───────────────────────────────────────────
        try:
            data = _api_get(
                "https://open-api.coinglass.com/public/v2/open_interest_history",
                params={"symbol": base, "time_type": "h1", "currency": "USD"},
                headers=headers,
                rate_limiter=rl,
            )
            if data and data.get("success") and data.get("data"):
                rows = []
                oi_list = data["data"]
                for i, item in enumerate(oi_list):
                    ts_ms = item.get("t") or item.get("createTime") or item.get("timestamp")
                    if not ts_ms:
                        continue
                    ts = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                    oi_usd = float(item.get("openInterest", 0) or item.get("oi", 0) or 0)
                    # Compute change pct
                    oi_change = None
                    if i > 0:
                        prev_oi = float(oi_list[i - 1].get("openInterest", 0) or
                                        oi_list[i - 1].get("oi", 0) or 0)
                        if prev_oi > 0:
                            oi_change = (oi_usd - prev_oi) / prev_oi
                    rows.append((sym, ts, oi_usd, oi_change, 'binance', 'coinglass', batch_id))
                if rows:
                    write_queue.put(('oi_history_extended', rows))
                    stats['oi_history_extended'] += len(rows)
        except Exception as e:
            logger.warning("    Coinglass OI %s failed: %s", sym, e)

        completed += 1
        if completed % 25 == 0 or completed == len(futures_symbols):
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (len(futures_symbols) - completed) / rate / 60 if rate > 0 else 0
            logger.info("    Coinglass: %d/%d (%.1f/s), ETA: %.0fm | liq=%d oi=%d",
                        completed, len(futures_symbols), rate, eta,
                        stats['liquidations'], stats['oi_history_extended'])
            failed += 1 if stats['liquidations'] == 0 and completed > 5 else 0

    logger.info("  Coinglass complete: liquidations=%d, oi_extended=%d rows",
                stats['liquidations'], stats['oi_history_extended'])
    return dict(stats)


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE: Santiment (PAID — requires SANTIMENT_API_KEY)
# ═══════════════════════════════════════════════════════════════════════════════

_SAN_GRAPHQL = "https://api.santiment.net/graphql"


def _san_query(slug, metric, from_dt, to_dt, interval="1h"):
    """Build a Santiment GraphQL query for getMetric."""
    return {
        "query": """
        {
            getMetric(metric: "%s") {
                timeseriesData(
                    slug: "%s"
                    from: "%s"
                    to: "%s"
                    interval: "%s"
                ) {
                    datetime
                    value
                }
            }
        }
        """ % (metric, slug, from_dt.isoformat(), to_dt.isoformat(), interval)
    }


def collect_santiment(symbols, mapping, write_queue, batch_id, backfill_days=365):
    """Collect exchange flows, whale txns, network activity, social from Santiment."""
    if not SANTIMENT_API_KEY:
        logger.info("  Santiment: SANTIMENT_API_KEY not set, skipping")
        return {}

    rl = RATE_LIMITERS["santiment"]
    headers = {"Authorization": f"Apikey {SANTIMENT_API_KEY}"}
    san_map = mapping.get("santiment", {})
    stats = defaultdict(int)

    if not san_map:
        logger.warning("  Santiment: no symbol mappings, skipping")
        return {}

    mapped = [s for s in symbols if s in san_map]
    logger.info("  Santiment: collecting for %d symbols", len(mapped))

    now = datetime.now(timezone.utc)
    from_dt = now - timedelta(days=backfill_days)

    completed = 0
    failed = 0
    t0 = time.time()

    for sym in mapped:
        slug = san_map[sym]["external_id"]

        # ── Exchange flows ────────────────────────────────────────────────
        try:
            inflow_data = _fetch_san_metric(slug, "exchange_inflow", from_dt, now,
                                            headers, rl, interval="1d")
            outflow_data = _fetch_san_metric(slug, "exchange_outflow", from_dt, now,
                                             headers, rl, interval="1d")

            if inflow_data or outflow_data:
                # Merge inflow and outflow by timestamp
                inflow_map = {d["datetime"]: d["value"] for d in (inflow_data or [])}
                outflow_map = {d["datetime"]: d["value"] for d in (outflow_data or [])}
                all_dates = sorted(set(list(inflow_map.keys()) + list(outflow_map.keys())))

                rows = []
                for dt_str in all_dates:
                    ts = _parse_san_datetime(dt_str)
                    if not ts:
                        continue
                    inf = inflow_map.get(dt_str)
                    outf = outflow_map.get(dt_str)
                    netf = None
                    if inf is not None and outf is not None:
                        netf = float(inf) - float(outf)
                    rows.append((
                        sym, ts,
                        float(inf) if inf is not None else None,
                        float(outf) if outf is not None else None,
                        netf,
                        'santiment', batch_id,
                    ))
                if rows:
                    write_queue.put(('exchange_flows', rows))
                    stats['exchange_flows'] += len(rows)

        except Exception as e:
            logger.warning("    Santiment flows %s: %s", sym, e)

        # ── Whale transactions ────────────────────────────────────────────
        try:
            whale_data = _fetch_san_metric(slug, "whale_transaction_count_100k_usd_to_inf",
                                           from_dt, now, headers, rl, interval="1d")
            if whale_data:
                rows = []
                for item in whale_data:
                    ts = _parse_san_datetime(item["datetime"])
                    if not ts:
                        continue
                    rows.append((
                        sym, ts,
                        _safe_int(item.get("value")),
                        None,  # volume not available from count metric
                        'santiment', batch_id,
                    ))
                if rows:
                    write_queue.put(('whale_transactions', rows))
                    stats['whale_transactions'] += len(rows)

        except Exception as e:
            logger.warning("    Santiment whale %s: %s", sym, e)

        # ── Network activity (active addresses, growth, social, dev) ──────
        try:
            metrics_to_fetch = [
                ("active_addresses_24h", "active_addresses_24h"),
                ("network_growth", "network_growth"),
                ("transaction_volume", "transaction_volume"),
                ("dev_activity", "dev_activity"),
                ("social_volume_total", "social_volume"),
            ]
            metric_data = {}
            for san_metric, col_name in metrics_to_fetch:
                data = _fetch_san_metric(slug, san_metric, from_dt, now,
                                         headers, rl, interval="1d")
                if data:
                    metric_data[col_name] = {d["datetime"]: d["value"] for d in data}

            if metric_data:
                all_dates = set()
                for v in metric_data.values():
                    all_dates.update(v.keys())
                all_dates = sorted(all_dates)

                rows = []
                for dt_str in all_dates:
                    ts = _parse_san_datetime(dt_str)
                    if not ts:
                        continue
                    rows.append((
                        sym, ts,
                        _safe_int(metric_data.get("active_addresses_24h", {}).get(dt_str)),
                        _safe_int(metric_data.get("network_growth", {}).get(dt_str)),
                        _safe_float(metric_data.get("transaction_volume", {}).get(dt_str)),
                        _safe_float(metric_data.get("dev_activity", {}).get(dt_str)),
                        _safe_int(metric_data.get("social_volume", {}).get(dt_str)),
                        None,  # social_dominance (separate query, skip for now)
                        'santiment', batch_id,
                    ))
                if rows:
                    write_queue.put(('network_activity', rows))
                    stats['network_activity'] += len(rows)

        except Exception as e:
            logger.warning("    Santiment network %s: %s", sym, e)

        completed += 1
        if completed % 10 == 0 or completed == len(mapped):
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (len(mapped) - completed) / rate / 60 if rate > 0 else 0
            logger.info("    Santiment: %d/%d (%.2f/s), ETA: %.0fm | "
                        "flows=%d whale=%d network=%d",
                        completed, len(mapped), rate, eta,
                        stats['exchange_flows'], stats['whale_transactions'],
                        stats['network_activity'])

    logger.info("  Santiment complete: flows=%d, whale=%d, network=%d rows",
                stats['exchange_flows'], stats['whale_transactions'],
                stats['network_activity'])
    return dict(stats)


def _fetch_san_metric(slug, metric, from_dt, to_dt, headers, rl, interval="1d"):
    """Fetch a single Santiment metric for a slug. Returns list of {datetime, value}."""
    body = _san_query(slug, metric, from_dt, to_dt, interval)
    data = _api_post(_SAN_GRAPHQL, json_body=body, headers=headers, rate_limiter=rl)
    if not data:
        return None
    ts_data = (data.get("data", {})
               .get("getMetric", {})
               .get("timeseriesData"))
    return ts_data


def _parse_san_datetime(dt_str):
    """Parse Santiment datetime string to timezone-aware datetime."""
    if not dt_str:
        return None
    try:
        # Santiment returns ISO format: "2024-01-15T00:00:00Z"
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE MATERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def materialize_features(db_url, symbols, lookback_hours=FEATURE_LOOKBACK_HOURS):
    """Compute on-chain features from raw tables and UPSERT into onchain_features_1h.

    Aggregates daily data to the nearest hourly bar for alignment with OHLCV.
    """
    logger.info("Materializing on-chain features for %d symbols, lookback=%dh...",
                len(symbols), lookback_hours)
    t0 = time.time()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # ── Load raw data ─────────────────────────────────────────────

            # Exchange flows
            cur.execute("""
                SELECT symbol, timestamp, exchange_inflow, exchange_outflow, exchange_netflow
                FROM exchange_flows
                WHERE symbol = ANY(%s) AND timestamp >= %s
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            flow_rows = cur.fetchall()

            # Whale transactions
            cur.execute("""
                SELECT symbol, timestamp, whale_tx_count, whale_tx_volume_usd
                FROM whale_transactions
                WHERE symbol = ANY(%s) AND timestamp >= %s
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            whale_rows = cur.fetchall()

            # Network activity
            cur.execute("""
                SELECT symbol, timestamp, active_addresses_24h, network_growth,
                       social_volume, dev_activity
                FROM network_activity
                WHERE symbol = ANY(%s) AND timestamp >= %s
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            network_rows = cur.fetchall()

            # Protocol TVL
            cur.execute("""
                SELECT symbol, timestamp, tvl_usd
                FROM protocol_tvl
                WHERE symbol = ANY(%s) AND timestamp >= %s
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            tvl_rows = cur.fetchall()

            # Liquidations
            cur.execute("""
                SELECT symbol, timestamp, long_liquidation_usd, short_liquidation_usd
                FROM liquidations
                WHERE symbol = ANY(%s) AND timestamp >= %s AND period = '1h'
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            liq_rows = cur.fetchall()

            # Extended OI
            cur.execute("""
                SELECT symbol, timestamp, open_interest_usd
                FROM oi_history_extended
                WHERE symbol = ANY(%s) AND timestamp >= %s
                ORDER BY symbol, timestamp
            """, (symbols, cutoff))
            oi_ext_rows = cur.fetchall()

            # Market metadata (latest per symbol)
            cur.execute("""
                SELECT DISTINCT ON (symbol) symbol, circulating_supply, market_cap_rank
                FROM market_metadata
                WHERE symbol = ANY(%s)
                ORDER BY symbol, timestamp DESC
            """, (symbols,))
            meta_rows = cur.fetchall()

            # Stablecoin supply (market-level)
            cur.execute("""
                SELECT symbol, timestamp, total_circulating_usd
                FROM stablecoin_supply
                WHERE timestamp >= %s AND chain = 'all'
                ORDER BY symbol, timestamp
            """, (cutoff,))
            stable_rows = cur.fetchall()

    total_raw = (len(flow_rows) + len(whale_rows) + len(network_rows) +
                 len(tvl_rows) + len(liq_rows) + len(oi_ext_rows))
    logger.info("  Loaded %d raw rows (%.1fs): flows=%d whale=%d network=%d tvl=%d liq=%d oi=%d",
                total_raw, time.time() - t0,
                len(flow_rows), len(whale_rows), len(network_rows),
                len(tvl_rows), len(liq_rows), len(oi_ext_rows))

    if total_raw == 0:
        logger.warning("  No raw on-chain data to materialize.")
        return 0

    # ── Build hourly grid from all available timestamps ───────────────────
    all_ts = set()
    for rows in [flow_rows, whale_rows, network_rows, tvl_rows, liq_rows, oi_ext_rows]:
        for row in rows:
            ts = row[1]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            all_ts.add(ts.replace(minute=0, second=0, microsecond=0))

    if not all_ts:
        return 0

    ts_grid = sorted(all_ts)
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_bars = len(ts_grid)
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    n_sym = len(symbols)

    logger.info("  Grid: %d bars x %d symbols", n_bars, n_sym)

    # ── Build matrices (forward-fill daily data to hourly) ────────────────

    def _fill_matrix(rows, sym_col=0, ts_col=1, val_col=2):
        """Build (n_bars, n_sym) matrix from DB rows, forward-filling."""
        mat = np.full((n_bars, n_sym), np.nan, dtype=np.float64)
        for row in rows:
            sym = row[sym_col]
            if sym not in sym_to_idx:
                continue
            ts = row[ts_col]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts_h = ts.replace(minute=0, second=0, microsecond=0)
            if ts_h in ts_to_idx:
                val = row[val_col]
                if val is not None:
                    mat[ts_to_idx[ts_h], sym_to_idx[sym]] = float(val)
        # Forward-fill
        for s in range(n_sym):
            col = mat[:, s]
            mask = ~np.isnan(col)
            if mask.any():
                idx = np.where(mask, np.arange(n_bars), 0)
                np.maximum.accumulate(idx, out=idx)
                mat[:, s] = col[idx]
        return mat

    # Exchange flows
    inflow_mat = _fill_matrix(flow_rows, val_col=2) if flow_rows else np.full((n_bars, n_sym), np.nan)
    outflow_mat = _fill_matrix(flow_rows, val_col=3) if flow_rows else np.full((n_bars, n_sym), np.nan)
    netflow_mat = _fill_matrix(flow_rows, val_col=4) if flow_rows else np.full((n_bars, n_sym), np.nan)

    # Whale txns
    whale_count_mat = _fill_matrix(whale_rows, val_col=2) if whale_rows else np.full((n_bars, n_sym), np.nan)
    whale_vol_mat = _fill_matrix(whale_rows, val_col=3) if whale_rows else np.full((n_bars, n_sym), np.nan)

    # Network
    active_addr_mat = _fill_matrix(network_rows, val_col=2) if network_rows else np.full((n_bars, n_sym), np.nan)
    net_growth_mat = _fill_matrix(network_rows, val_col=3) if network_rows else np.full((n_bars, n_sym), np.nan)
    social_vol_mat = _fill_matrix(network_rows, val_col=4) if network_rows else np.full((n_bars, n_sym), np.nan)
    dev_act_mat = _fill_matrix(network_rows, val_col=5) if network_rows else np.full((n_bars, n_sym), np.nan)

    # TVL
    tvl_mat = _fill_matrix(tvl_rows, val_col=2) if tvl_rows else np.full((n_bars, n_sym), np.nan)

    # Liquidations (NOT forward-filled — point-in-time)
    def _fill_matrix_noff(rows, sym_col=0, ts_col=1, val_col=2):
        mat = np.full((n_bars, n_sym), np.nan, dtype=np.float64)
        for row in rows:
            sym = row[sym_col]
            if sym not in sym_to_idx:
                continue
            ts = row[ts_col]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts_h = ts.replace(minute=0, second=0, microsecond=0)
            if ts_h in ts_to_idx:
                val = row[val_col]
                if val is not None:
                    mat[ts_to_idx[ts_h], sym_to_idx[sym]] = float(val)
        return mat

    long_liq_mat = _fill_matrix_noff(liq_rows, val_col=2) if liq_rows else np.full((n_bars, n_sym), np.nan)
    short_liq_mat = _fill_matrix_noff(liq_rows, val_col=3) if liq_rows else np.full((n_bars, n_sym), np.nan)

    # Extended OI
    oi_ext_mat = _fill_matrix(oi_ext_rows, val_col=2) if oi_ext_rows else np.full((n_bars, n_sym), np.nan)

    # ── Compute derived features (vectorized) ─────────────────────────────

    # Z-scores (30-day = 720 hourly bars)
    ZSCORE_WINDOW = 720

    inflow_zscore = _zscore(inflow_mat, ZSCORE_WINDOW)
    outflow_zscore = _zscore(outflow_mat, ZSCORE_WINDOW)
    netflow_zscore = _zscore(netflow_mat, ZSCORE_WINDOW)
    netflow_ma24 = _rolling_mean(netflow_mat, 24)

    whale_count_zscore = _zscore(whale_count_mat, ZSCORE_WINDOW)
    whale_vol_zscore = _zscore(whale_vol_mat, ZSCORE_WINDOW)

    active_addr_zscore = _zscore(active_addr_mat, ZSCORE_WINDOW)
    net_growth_zscore = _zscore(net_growth_mat, ZSCORE_WINDOW)
    social_vol_zscore = _zscore(social_vol_mat, ZSCORE_WINDOW)
    dev_act_zscore = _zscore(dev_act_mat, ZSCORE_WINDOW)

    # Social volume acceleration: sv[t] - sv[t-24]
    social_vol_accel = np.full_like(social_vol_mat, np.nan)
    if n_bars > 24:
        social_vol_accel[24:] = social_vol_mat[24:] - social_vol_mat[:-24]

    # TVL changes
    with np.errstate(divide='ignore', invalid='ignore'):
        tvl_change_1d = np.full_like(tvl_mat, np.nan)
        if n_bars > 24:
            tvl_change_1d[24:] = np.where(tvl_mat[:-24] > 0,
                                           (tvl_mat[24:] - tvl_mat[:-24]) / tvl_mat[:-24], np.nan)
        tvl_change_7d = np.full_like(tvl_mat, np.nan)
        if n_bars > 168:
            tvl_change_7d[168:] = np.where(tvl_mat[:-168] > 0,
                                            (tvl_mat[168:] - tvl_mat[:-168]) / tvl_mat[:-168], np.nan)
    tvl_zscore = _zscore(tvl_mat, ZSCORE_WINDOW)

    # Liquidation features
    long_liq_zscore = _zscore(long_liq_mat, ZSCORE_WINDOW)
    short_liq_zscore = _zscore(short_liq_mat, ZSCORE_WINDOW)

    liq_total = np.where(np.isnan(long_liq_mat), 0, long_liq_mat) + \
                np.where(np.isnan(short_liq_mat), 0, short_liq_mat)
    with np.errstate(divide='ignore', invalid='ignore'):
        liq_imbalance = np.where(
            liq_total > 0,
            (np.where(np.isnan(long_liq_mat), 0, long_liq_mat) -
             np.where(np.isnan(short_liq_mat), 0, short_liq_mat)) / liq_total,
            np.nan
        )

    # Liquidation cascade: cumulative long liqs in prior 24h
    liq_cascade = _rolling_sum(np.where(np.isnan(long_liq_mat), 0, long_liq_mat), 24)

    # Extended OI features
    with np.errstate(divide='ignore', invalid='ignore'):
        oi_ext_change_7d = np.full_like(oi_ext_mat, np.nan)
        if n_bars > 168:
            oi_ext_change_7d[168:] = np.where(
                oi_ext_mat[:-168] > 0,
                (oi_ext_mat[168:] - oi_ext_mat[:-168]) / oi_ext_mat[:-168], np.nan)
        oi_ext_change_30d = np.full_like(oi_ext_mat, np.nan)
        if n_bars > 720:
            oi_ext_change_30d[720:] = np.where(
                oi_ext_mat[:-720] > 0,
                (oi_ext_mat[720:] - oi_ext_mat[:-720]) / oi_ext_mat[:-720], np.nan)
    oi_ext_zscore = _zscore(oi_ext_mat, ZSCORE_WINDOW)

    # Stablecoin features (market-level — same value for all symbols)
    stable_change_1d = np.full((n_bars, n_sym), np.nan)
    stable_change_7d = np.full((n_bars, n_sym), np.nan)
    if stable_rows:
        # Aggregate total stablecoin supply per timestamp
        stable_by_ts = defaultdict(float)
        for row in stable_rows:
            ts = row[1]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts_h = ts.replace(minute=0, second=0, microsecond=0)
            if row[2] is not None:
                stable_by_ts[ts_h] += float(row[2])
        if stable_by_ts:
            stable_arr = np.full(n_bars, np.nan)
            for ts_h, val in stable_by_ts.items():
                if ts_h in ts_to_idx:
                    stable_arr[ts_to_idx[ts_h]] = val
            # Forward-fill
            mask = ~np.isnan(stable_arr)
            if mask.any():
                idx = np.where(mask, np.arange(n_bars), 0)
                np.maximum.accumulate(idx, out=idx)
                stable_arr = stable_arr[idx]
            # Changes
            with np.errstate(divide='ignore', invalid='ignore'):
                sc_1d = np.full(n_bars, np.nan)
                if n_bars > 24:
                    sc_1d[24:] = np.where(stable_arr[:-24] > 0,
                                           (stable_arr[24:] - stable_arr[:-24]) / stable_arr[:-24], np.nan)
                sc_7d = np.full(n_bars, np.nan)
                if n_bars > 168:
                    sc_7d[168:] = np.where(stable_arr[:-168] > 0,
                                            (stable_arr[168:] - stable_arr[:-168]) / stable_arr[:-168], np.nan)
            # Broadcast to all symbols
            stable_change_1d = np.broadcast_to(sc_1d[:, np.newaxis], (n_bars, n_sym)).copy()
            stable_change_7d = np.broadcast_to(sc_7d[:, np.newaxis], (n_bars, n_sym)).copy()

    # Holder/supply features from market_metadata (point-in-time, no time series)
    # These are daily snapshots — we'll compute changes when we have history
    circ_supply_change = np.full((n_bars, n_sym), np.nan)
    mcap_rank_change = np.full((n_bars, n_sym), np.nan)
    whale_holder_pct_change = np.full((n_bars, n_sym), np.nan)
    unique_holders_growth = np.full((n_bars, n_sym), np.nan)
    holder_concentration = np.full((n_bars, n_sym), np.nan)

    # DEX features (placeholder — need DEX volume data aligned per symbol)
    dex_vol_zscore = np.full((n_bars, n_sym), np.nan)
    dex_cex_ratio = np.full((n_bars, n_sym), np.nan)

    # ── Data completeness ─────────────────────────────────────────────────
    TOTAL_FEATURES = 31
    feature_arrays = [
        inflow_zscore, outflow_zscore, netflow_zscore, netflow_ma24,
        whale_count_mat, whale_count_zscore, whale_vol_zscore,
        whale_holder_pct_change, unique_holders_growth, holder_concentration,
        active_addr_zscore, net_growth_zscore, social_vol_zscore,
        social_vol_accel, dev_act_zscore,
        tvl_change_1d, tvl_change_7d, tvl_zscore,
        dex_vol_zscore, dex_cex_ratio,
        stable_change_1d, stable_change_7d,
        long_liq_zscore, short_liq_zscore, liq_imbalance, liq_cascade,
        oi_ext_change_7d, oi_ext_change_30d, oi_ext_zscore,
        circ_supply_change, mcap_rank_change,
    ]
    non_null = np.zeros((n_bars, n_sym), dtype=np.float64)
    for arr in feature_arrays:
        non_null += (~np.isnan(arr)).astype(np.float64)
    completeness = non_null / TOTAL_FEATURES

    # ── UPSERT ────────────────────────────────────────────────────────────
    logger.info("  Writing features to onchain_features_1h...")
    t_write = time.time()
    upsert_count = 0

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            batch = []
            for t_idx in range(n_bars):
                ts = ts_grid[t_idx]
                for s_idx in range(n_sym):
                    if completeness[t_idx, s_idx] == 0:
                        continue

                    sym = symbols[s_idx]

                    def _v(arr, t=t_idx, s=s_idx):
                        v = arr[t, s]
                        return None if np.isnan(v) else float(v)

                    batch.append((
                        sym, ts,
                        _v(inflow_zscore), _v(outflow_zscore), _v(netflow_zscore), _v(netflow_ma24),
                        _v(whale_count_mat), _v(whale_count_zscore), _v(whale_vol_zscore),
                        _v(whale_holder_pct_change), _v(unique_holders_growth), _v(holder_concentration),
                        _v(active_addr_zscore), _v(net_growth_zscore), _v(social_vol_zscore),
                        _v(social_vol_accel), _v(dev_act_zscore),
                        _v(tvl_change_1d), _v(tvl_change_7d), _v(tvl_zscore),
                        _v(dex_vol_zscore), _v(dex_cex_ratio),
                        _v(stable_change_1d), _v(stable_change_7d),
                        _v(long_liq_zscore), _v(short_liq_zscore), _v(liq_imbalance), _v(liq_cascade),
                        _v(oi_ext_change_7d), _v(oi_ext_change_30d), _v(oi_ext_zscore),
                        _v(circ_supply_change), _v(mcap_rank_change),
                        float(completeness[t_idx, s_idx]),
                    ))

                    if len(batch) >= DB_BATCH_SIZE:
                        _upsert_onchain_features(cur, batch)
                        upsert_count += len(batch)
                        batch = []

            if batch:
                _upsert_onchain_features(cur, batch)
                upsert_count += len(batch)
        conn.commit()

    logger.info("  Materialized %d feature rows in %.1fs", upsert_count, time.time() - t_write)
    return upsert_count


def _upsert_onchain_features(cur, batch):
    cur.executemany("""
        INSERT INTO onchain_features_1h (
            symbol, bar_start_ts,
            exchange_inflow_zscore, exchange_outflow_zscore, exchange_netflow_zscore, exchange_netflow_ma24,
            whale_tx_count, whale_tx_count_zscore, whale_tx_volume_zscore,
            whale_holder_pct_change_7d, unique_holders_growth_rate, holder_concentration_change,
            active_addresses_zscore, network_growth_zscore, social_volume_zscore,
            social_volume_acceleration, dev_activity_zscore,
            tvl_change_pct_1d, tvl_change_pct_7d, tvl_zscore,
            dex_volume_zscore, dex_cex_volume_ratio,
            stablecoin_supply_change_1d, stablecoin_supply_change_7d,
            long_liquidation_zscore, short_liquidation_zscore, liquidation_imbalance, liquidation_cascade_score,
            oi_extended_change_7d, oi_extended_change_30d, oi_extended_zscore,
            circulating_supply_change_7d, market_cap_rank_change_7d,
            data_completeness, materialized_at
        ) VALUES (
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, NOW()
        )
        ON CONFLICT (symbol, bar_start_ts) DO UPDATE SET
            exchange_inflow_zscore = EXCLUDED.exchange_inflow_zscore,
            exchange_outflow_zscore = EXCLUDED.exchange_outflow_zscore,
            exchange_netflow_zscore = EXCLUDED.exchange_netflow_zscore,
            exchange_netflow_ma24 = EXCLUDED.exchange_netflow_ma24,
            whale_tx_count = EXCLUDED.whale_tx_count,
            whale_tx_count_zscore = EXCLUDED.whale_tx_count_zscore,
            whale_tx_volume_zscore = EXCLUDED.whale_tx_volume_zscore,
            whale_holder_pct_change_7d = EXCLUDED.whale_holder_pct_change_7d,
            unique_holders_growth_rate = EXCLUDED.unique_holders_growth_rate,
            holder_concentration_change = EXCLUDED.holder_concentration_change,
            active_addresses_zscore = EXCLUDED.active_addresses_zscore,
            network_growth_zscore = EXCLUDED.network_growth_zscore,
            social_volume_zscore = EXCLUDED.social_volume_zscore,
            social_volume_acceleration = EXCLUDED.social_volume_acceleration,
            dev_activity_zscore = EXCLUDED.dev_activity_zscore,
            tvl_change_pct_1d = EXCLUDED.tvl_change_pct_1d,
            tvl_change_pct_7d = EXCLUDED.tvl_change_pct_7d,
            tvl_zscore = EXCLUDED.tvl_zscore,
            dex_volume_zscore = EXCLUDED.dex_volume_zscore,
            dex_cex_volume_ratio = EXCLUDED.dex_cex_volume_ratio,
            stablecoin_supply_change_1d = EXCLUDED.stablecoin_supply_change_1d,
            stablecoin_supply_change_7d = EXCLUDED.stablecoin_supply_change_7d,
            long_liquidation_zscore = EXCLUDED.long_liquidation_zscore,
            short_liquidation_zscore = EXCLUDED.short_liquidation_zscore,
            liquidation_imbalance = EXCLUDED.liquidation_imbalance,
            liquidation_cascade_score = EXCLUDED.liquidation_cascade_score,
            oi_extended_change_7d = EXCLUDED.oi_extended_change_7d,
            oi_extended_change_30d = EXCLUDED.oi_extended_change_30d,
            oi_extended_zscore = EXCLUDED.oi_extended_zscore,
            circulating_supply_change_7d = EXCLUDED.circulating_supply_change_7d,
            market_cap_rank_change_7d = EXCLUDED.market_cap_rank_change_7d,
            data_completeness = EXCLUDED.data_completeness,
            materialized_at = NOW()
    """, batch)


# ── Numpy helpers ─────────────────────────────────────────────────────────────

def _rolling_mean(arr, window):
    out = np.full_like(arr, np.nan)
    if arr.shape[0] < window:
        return out
    cs = np.nancumsum(arr, axis=0)
    count = np.nancumsum(~np.isnan(arr), axis=0).astype(np.float64)
    out[window - 1:] = cs[window - 1:]
    out[window - 1:] -= np.concatenate([np.zeros((1, arr.shape[1])), cs[:-window]], axis=0)
    cnt = count[window - 1:]
    cnt -= np.concatenate([np.zeros((1, arr.shape[1])), count[:-window]], axis=0)
    with np.errstate(divide='ignore', invalid='ignore'):
        out[window - 1:] = np.where(cnt > 0, out[window - 1:] / cnt, np.nan)
    return out


def _rolling_std(arr, window):
    mean = _rolling_mean(arr, window)
    sq_mean = _rolling_mean(arr ** 2, window)
    with np.errstate(invalid='ignore'):
        var = sq_mean - mean ** 2
        var = np.where(var > 0, var, 0)
        return np.sqrt(var)


def _rolling_sum(arr, window):
    out = np.full_like(arr, np.nan)
    if arr.shape[0] < window:
        return out
    cs = np.nancumsum(arr, axis=0)
    out[window - 1:] = cs[window - 1:]
    out[window - 1:] -= np.concatenate([np.zeros((1, arr.shape[1])), cs[:-window]], axis=0)
    return out


def _zscore(arr, window):
    mean = _rolling_mean(arr, window)
    std = _rolling_std(arr, window)
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(std > 0, (arr - mean) / std, np.nan)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="On-Chain Data Collector")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill historical data before continuous mode")
    parser.add_argument("--backfill-only", action="store_true",
                        help="Backfill and exit")
    parser.add_argument("--materialize-only", action="store_true",
                        help="Only run feature materialization")
    parser.add_argument("--source", type=str, default="all",
                        help="Only collect from: defillama|coingecko|etherscan|coinglass|santiment")
    parser.add_argument("--symbols", type=int, default=0,
                        help="Limit to first N symbols (0 = all)")
    parser.add_argument("--backfill-days", type=int, default=365,
                        help="Backfill history depth in days")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("ON-CHAIN DATA COLLECTOR")
    logger.info("=" * 70)

    # ── Check API keys ────────────────────────────────────────────────────
    logger.info("API keys:")
    logger.info("  COINGLASS_API_KEY:  %s", "SET" if COINGLASS_API_KEY else "NOT SET (Phase 2 — skipping Coinglass)")
    logger.info("  SANTIMENT_API_KEY:  %s", "SET" if SANTIMENT_API_KEY else "NOT SET (Phase 2 — skipping Santiment)")
    logger.info("  ETHERSCAN_API_KEY:  %s", "SET" if ETHERSCAN_API_KEY else "NOT SET (using free tier)")
    logger.info("  BSCSCAN_API_KEY:    %s", "SET" if BSCSCAN_API_KEY else "NOT SET (using free tier)")
    logger.info("  COINGECKO_API_KEY:  %s", "SET" if COINGECKO_API_KEY else "NOT SET (using free tier)")

    # ── Load symbol mappings ──────────────────────────────────────────────
    mapping = load_symbol_mapping(db_url)
    if not mapping:
        logger.error("No symbol mappings found. Run: python3 /scripts/map_onchain_symbols.py")
        sys.exit(1)

    # Build symbol list from all mapped sources
    all_symbols = set()
    for source_map in mapping.values():
        all_symbols.update(source_map.keys())
    symbols = sorted(all_symbols)

    if args.symbols > 0:
        symbols = symbols[:args.symbols]
    logger.info("Symbols: %d", len(symbols))

    # ── Materialize-only mode ─────────────────────────────────────────────
    if args.materialize_only:
        n = materialize_features(db_url, symbols)
        logger.info("Done. %d feature rows materialized.", n)
        return

    # ── Start DB writer thread ────────────────────────────────────────────
    write_queue = queue.Queue(maxsize=1000)
    writer_stats = {"rows_written": 0}
    writer_thread = threading.Thread(
        target=db_writer_loop, args=(db_url, write_queue, writer_stats),
        daemon=True, name="db-writer",
    )
    writer_thread.start()

    # ── Collection ────────────────────────────────────────────────────────
    def run_collection(backfill_days=365):
        batch_id = f"onchain_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        all_stats = {}

        sources = {
            "defillama": lambda: collect_defillama(symbols, mapping, write_queue, batch_id, backfill_days),
            "coingecko": lambda: collect_coingecko(symbols, mapping, write_queue, batch_id),
            "etherscan": lambda: collect_etherscan(symbols, mapping, write_queue, batch_id),
            "coinglass": lambda: collect_coinglass(symbols, mapping, write_queue, batch_id, backfill_days),
            "santiment": lambda: collect_santiment(symbols, mapping, write_queue, batch_id, backfill_days),
        }

        for name, collect_fn in sources.items():
            if args.source != "all" and args.source != name:
                continue
            logger.info("\n── Source: %s ──", name.upper())
            try:
                stats = collect_fn()
                all_stats[name] = stats
            except Exception as e:
                logger.error("Source %s failed: %s", name, e)
                all_stats[name] = {"error": str(e)}

        return all_stats

    if args.backfill or args.backfill_only:
        logger.info("\nStarting backfill (%d days)...", args.backfill_days)
        t0 = time.time()
        stats = run_collection(args.backfill_days)

        # Flush writer
        time.sleep(3)
        write_queue.put(None)
        writer_thread.join(timeout=120)

        logger.info("\n" + "=" * 70)
        logger.info("BACKFILL COMPLETE (%.1f min)", (time.time() - t0) / 60)
        logger.info("Total rows written: %d", writer_stats["rows_written"])
        for name, s in stats.items():
            logger.info("  %s: %s", name, s)
        logger.info("=" * 70)

        # Materialize features
        materialize_features(db_url, symbols)

        if args.backfill_only:
            return

        # Restart writer for continuous mode
        write_queue = queue.Queue(maxsize=1000)
        writer_stats = {"rows_written": 0}
        writer_thread = threading.Thread(
            target=db_writer_loop, args=(db_url, write_queue, writer_stats),
            daemon=True, name="db-writer",
        )
        writer_thread.start()

    # ── Continuous collection ─────────────────────────────────────────────
    logger.info("\nEntering continuous collection mode (interval=%ds)", COLLECTION_INTERVAL_S)
    cycle_count = 0

    try:
        while True:
            cycle_count += 1
            cycle_start = time.time()
            logger.info("\n── Cycle %d at %s ──",
                        cycle_count, datetime.now(timezone.utc).strftime('%H:%M:%S'))

            stats = run_collection(backfill_days=7)  # Only last 7 days for live cycles

            time.sleep(3)  # Let writer catch up
            materialize_features(db_url, symbols, lookback_hours=48)

            logger.info("  Cycle %d: DB rows=%d", cycle_count, writer_stats["rows_written"])

            cycle_elapsed = time.time() - cycle_start
            sleep_time = max(0, COLLECTION_INTERVAL_S - cycle_elapsed)
            if sleep_time > 0:
                logger.info("  Sleeping %.0fs until next cycle...", sleep_time)
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        write_queue.put(None)
        writer_thread.join(timeout=30)
        logger.info("On-chain collector stopped. %d total rows.", writer_stats["rows_written"])


if __name__ == "__main__":
    main()
