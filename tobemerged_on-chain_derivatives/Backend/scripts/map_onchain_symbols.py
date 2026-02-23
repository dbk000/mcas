#!/usr/bin/env python3
"""
On-Chain Symbol Mapper
======================

Maps Binance USDT spot symbols to external data source IDs:
  - CoinGecko:  coin ID (e.g. BTCUSDT → 'bitcoin')
  - Santiment:  slug    (e.g. BTCUSDT → 'bitcoin')
  - DefiLlama: protocol slug (e.g. AAVEUSDT → 'aave')
  - Etherscan:  contract address + chain (ERC-20 / BEP-20)

Writes results to:
  1. symbol_mapping table in Postgres
  2. onchain_symbol_mapping.json (for offline reference)

Usage:
    docker compose run --rm -T worker python3 /scripts/map_onchain_symbols.py
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone

# ─── Env + path setup ────────────────────────────────────────────────────────
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

import requests as req_lib
import psycopg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Rate limiter for CoinGecko free tier ─────────────────────────────────────
# Free tier: 10-30 calls/min. Be conservative.
CG_DELAY = 2.5  # seconds between CoinGecko calls


def load_binance_symbols(db_url):
    """Get distinct spot symbols from OHLCV table."""
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT symbol FROM ohlcv WHERE timeframe = '1h' ORDER BY symbol")
            return [row[0] for row in cur]


def strip_usdt(symbol):
    """BTCUSDT → BTC (lowercase for matching)."""
    if symbol.endswith('USDT'):
        return symbol[:-4].lower()
    return symbol.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# CoinGecko Mapping
# ═══════════════════════════════════════════════════════════════════════════════

def map_coingecko(symbols):
    """Map Binance symbols to CoinGecko coin IDs.

    Uses /coins/list?include_platform=true for bulk matching,
    then individual /coins/{id} calls for contract addresses.

    Returns: dict[binance_symbol] -> {id, chain, contract_address}
    """
    logger.info("Fetching CoinGecko coin list...")
    resp = req_lib.get(
        "https://api.coingecko.com/api/v3/coins/list",
        params={"include_platform": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    cg_coins = resp.json()
    logger.info("  CoinGecko: %d coins in database", len(cg_coins))

    # Build lookup: symbol (lowercase) → list of CoinGecko entries
    # (multiple coins can share the same ticker symbol)
    sym_lookup = {}
    for coin in cg_coins:
        s = coin.get("symbol", "").lower()
        if s not in sym_lookup:
            sym_lookup[s] = []
        sym_lookup[s].append(coin)

    mapping = {}
    ambiguous = []

    for binance_sym in symbols:
        base = strip_usdt(binance_sym)
        candidates = sym_lookup.get(base, [])

        if len(candidates) == 0:
            continue
        elif len(candidates) == 1:
            coin = candidates[0]
        else:
            # Multiple matches — prefer the one with the most platforms,
            # or the one whose name is shortest (heuristic: the "main" token)
            # Known disambiguation
            known = _KNOWN_CG_IDS.get(base)
            if known:
                coin = next((c for c in candidates if c["id"] == known), candidates[0])
            else:
                # Sort by: has ethereum platform, then by name length
                def score(c):
                    has_eth = 1 if "ethereum" in (c.get("platforms") or {}) else 0
                    return (-has_eth, len(c.get("name", "")))
                candidates.sort(key=score)
                coin = candidates[0]
                if len(candidates) > 2:
                    ambiguous.append((binance_sym, [c["id"] for c in candidates[:3]]))

        # Extract contract addresses
        platforms = coin.get("platforms") or {}
        chain = None
        contract = None
        if "ethereum" in platforms and platforms["ethereum"]:
            chain = "ethereum"
            contract = platforms["ethereum"]
        elif "binance-smart-chain" in platforms and platforms["binance-smart-chain"]:
            chain = "bsc"
            contract = platforms["binance-smart-chain"]
        elif "solana" in platforms and platforms["solana"]:
            chain = "solana"
            contract = platforms["solana"]
        elif platforms:
            # Take the first non-empty platform
            for p_name, p_addr in platforms.items():
                if p_addr:
                    chain = p_name
                    contract = p_addr
                    break

        mapping[binance_sym] = {
            "coingecko_id": coin["id"],
            "chain": chain,
            "contract_address": contract,
        }

    if ambiguous:
        logger.warning("  Ambiguous CoinGecko matches (using heuristic): %d", len(ambiguous))
        for sym, ids in ambiguous[:5]:
            logger.warning("    %s → candidates: %s", sym, ids)

    logger.info("  Mapped %d/%d symbols to CoinGecko IDs", len(mapping), len(symbols))
    return mapping


# Known CoinGecko IDs for common ambiguous symbols
_KNOWN_CG_IDS = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "bnb": "binancecoin",
    "sol": "solana",
    "xrp": "ripple",
    "ada": "cardano",
    "doge": "dogecoin",
    "dot": "polkadot",
    "avax": "avalanche-2",
    "link": "chainlink",
    "matic": "matic-network",
    "uni": "uniswap",
    "atom": "cosmos",
    "ltc": "litecoin",
    "near": "near",
    "ftm": "fantom",
    "algo": "algorand",
    "icp": "internet-computer",
    "fil": "filecoin",
    "vet": "vechain",
    "sand": "the-sandbox",
    "mana": "decentraland",
    "axs": "axie-infinity",
    "aave": "aave",
    "grt": "the-graph",
    "snx": "havven",
    "comp": "compound-governance-token",
    "mkr": "maker",
    "crv": "curve-dao-token",
    "ldo": "lido-dao",
    "ape": "apecoin",
    "imx": "immutable-x",
    "arb": "arbitrum",
    "op": "optimism",
    "sui": "sui",
    "sei": "sei-network",
    "inj": "injective-protocol",
    "rune": "thorchain",
    "pendle": "pendle",
    "jup": "jupiter-exchange-solana",
    "ondo": "ondo-finance",
    "render": "render-token",
    "kas": "kaspa",
    "ton": "the-open-network",
    "trx": "tron",
    "shib": "shiba-inu",
    "pepe": "pepe",
    "floki": "floki",
    "bonk": "bonk",
    "wif": "dogwifcoin",
    "trump": "official-trump",
    "1inch": "1inch",
}


# ═══════════════════════════════════════════════════════════════════════════════
# DefiLlama Protocol Mapping
# ═══════════════════════════════════════════════════════════════════════════════

def map_defillama(symbols, cg_mapping):
    """Map Binance symbols to DefiLlama protocol slugs.

    DefiLlama tracks protocols (Aave, Uniswap) not tokens directly.
    We match by: CoinGecko ID match, symbol match, or name match.
    Many tokens won't have a DefiLlama protocol (e.g. DOGE) — that's expected.

    Returns: dict[binance_symbol] -> {protocol_slug, protocol_name}
    """
    logger.info("Fetching DefiLlama protocols list...")
    resp = req_lib.get("https://api.llama.fi/protocols", timeout=30)
    resp.raise_for_status()
    protocols = resp.json()
    logger.info("  DefiLlama: %d protocols", len(protocols))

    # Build lookups
    by_gecko_id = {}
    by_symbol = {}
    for p in protocols:
        gid = p.get("gecko_id")
        if gid:
            by_gecko_id[gid] = p
        sym = (p.get("symbol") or "").lower()
        if sym:
            if sym not in by_symbol:
                by_symbol[sym] = p

    mapping = {}
    for binance_sym in symbols:
        base = strip_usdt(binance_sym)
        cg_info = cg_mapping.get(binance_sym, {})
        cg_id = cg_info.get("coingecko_id")

        # Try gecko_id match first (most reliable)
        if cg_id and cg_id in by_gecko_id:
            p = by_gecko_id[cg_id]
            mapping[binance_sym] = {
                "defillama_slug": p["slug"],
                "defillama_name": p["name"],
            }
            continue

        # Fall back to symbol match
        if base in by_symbol:
            p = by_symbol[base]
            mapping[binance_sym] = {
                "defillama_slug": p["slug"],
                "defillama_name": p["name"],
            }

    logger.info("  Mapped %d/%d symbols to DefiLlama protocols", len(mapping), len(symbols))
    return mapping


# ═══════════════════════════════════════════════════════════════════════════════
# Santiment Mapping
# ═══════════════════════════════════════════════════════════════════════════════

def map_santiment(symbols, cg_mapping):
    """Map Binance symbols to Santiment slugs.

    Santiment slugs are usually identical to CoinGecko IDs.
    We verify availability via the SanAPI if API key is set.

    Returns: dict[binance_symbol] -> {santiment_slug}
    """
    api_key = os.environ.get("SANTIMENT_API_KEY", "")

    # Without API key, use CoinGecko IDs directly as best-guess slugs
    mapping = {}
    for binance_sym, cg_info in cg_mapping.items():
        cg_id = cg_info.get("coingecko_id")
        if cg_id:
            mapping[binance_sym] = {"santiment_slug": cg_id}

    if not api_key:
        logger.info("  No SANTIMENT_API_KEY set — using CoinGecko IDs as Santiment slugs")
        logger.info("  Mapped %d symbols (unverified)", len(mapping))
        return mapping

    # With API key, verify a sample of slugs
    logger.info("  Verifying Santiment slugs via SanAPI...")
    headers = {"Authorization": f"Apikey {api_key}"}
    verified = 0
    failed = []

    test_sample = list(mapping.items())[:20]  # Verify first 20 only
    for binance_sym, info in test_sample:
        slug = info["santiment_slug"]
        query = """{ projectBySlug(slug: "%s") { slug ticker } }""" % slug
        try:
            resp = req_lib.post(
                "https://api.santiment.net/graphql",
                json={"query": query},
                headers=headers,
                timeout=10,
            )
            data = resp.json()
            if data.get("data", {}).get("projectBySlug"):
                verified += 1
            else:
                failed.append(slug)
            time.sleep(0.5)  # Rate limit
        except Exception as e:
            logger.warning("    Santiment verify failed for %s: %s", slug, e)

    logger.info("  Verified %d/%d sample slugs (%d failed)", verified, len(test_sample), len(failed))
    if failed:
        logger.info("    Failed slugs: %s", failed[:5])
    logger.info("  Mapped %d symbols total", len(mapping))
    return mapping


# ═══════════════════════════════════════════════════════════════════════════════
# Write Results
# ═══════════════════════════════════════════════════════════════════════════════

def write_to_db(db_url, cg_mapping, dl_mapping, san_mapping):
    """Write all mappings to the symbol_mapping table."""
    logger.info("Writing mappings to symbol_mapping table...")
    rows = []

    for sym, info in cg_mapping.items():
        rows.append((sym, 'coingecko', info['coingecko_id'], info.get('chain'), info.get('contract_address')))

        # Etherscan entry (if we have a contract address on ethereum or bsc)
        if info.get('contract_address') and info.get('chain') in ('ethereum', 'bsc'):
            source = 'etherscan' if info['chain'] == 'ethereum' else 'bscscan'
            rows.append((sym, source, info['contract_address'], info['chain'], info['contract_address']))

    for sym, info in dl_mapping.items():
        rows.append((sym, 'defillama', info['defillama_slug'], None, None))

    for sym, info in san_mapping.items():
        rows.append((sym, 'santiment', info['santiment_slug'], None, None))

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO symbol_mapping (symbol, source, external_id, chain, contract_address)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (symbol, source) DO UPDATE SET
                    external_id = EXCLUDED.external_id,
                    chain = EXCLUDED.chain,
                    contract_address = EXCLUDED.contract_address,
                    mapped_at = NOW()
            """, rows)
        conn.commit()

    logger.info("  Wrote %d mapping rows", len(rows))


def write_json(cg_mapping, dl_mapping, san_mapping):
    """Write combined mapping to JSON for offline reference."""
    combined = {}
    for sym in set(list(cg_mapping) + list(dl_mapping) + list(san_mapping)):
        combined[sym] = {}
        if sym in cg_mapping:
            combined[sym].update(cg_mapping[sym])
        if sym in dl_mapping:
            combined[sym].update(dl_mapping[sym])
        if sym in san_mapping:
            combined[sym].update(san_mapping[sym])

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_symbols": len(combined),
        "coingecko_mapped": len(cg_mapping),
        "defillama_mapped": len(dl_mapping),
        "santiment_mapped": len(san_mapping),
        "etherscan_mapped": sum(1 for v in cg_mapping.values() if v.get('chain') == 'ethereum' and v.get('contract_address')),
        "bscscan_mapped": sum(1 for v in cg_mapping.values() if v.get('chain') == 'bsc' and v.get('contract_address')),
        "mappings": combined,
    }

    out_path = os.path.join(os.path.dirname(__file__), 'onchain_symbol_mapping.json')
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2)
    logger.info("Wrote %s", out_path)
    return out_path


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("ON-CHAIN SYMBOL MAPPER")
    logger.info("=" * 70)

    # Load Binance symbols from DB
    symbols = load_binance_symbols(db_url)
    logger.info("Loaded %d Binance spot symbols from OHLCV table", len(symbols))

    # Map to CoinGecko (foundation — other mappings build on this)
    cg_mapping = map_coingecko(symbols)
    time.sleep(1)  # Respect rate limit between CG calls

    # Map to DefiLlama
    dl_mapping = map_defillama(symbols, cg_mapping)

    # Map to Santiment
    san_mapping = map_santiment(symbols, cg_mapping)

    # Summary
    eth_count = sum(1 for v in cg_mapping.values() if v.get('chain') == 'ethereum' and v.get('contract_address'))
    bsc_count = sum(1 for v in cg_mapping.values() if v.get('chain') == 'bsc' and v.get('contract_address'))

    logger.info("")
    logger.info("=" * 70)
    logger.info("MAPPING SUMMARY")
    logger.info("=" * 70)
    logger.info("  Total Binance symbols:  %d", len(symbols))
    logger.info("  CoinGecko mapped:       %d (%.1f%%)", len(cg_mapping), 100 * len(cg_mapping) / len(symbols))
    logger.info("  DefiLlama protocols:    %d (%.1f%%)", len(dl_mapping), 100 * len(dl_mapping) / len(symbols))
    logger.info("  Santiment slugs:        %d (%.1f%%)", len(san_mapping), 100 * len(san_mapping) / len(symbols))
    logger.info("  Etherscan (ERC-20):     %d", eth_count)
    logger.info("  BSCScan (BEP-20):       %d", bsc_count)

    # Write to DB and JSON
    try:
        write_to_db(db_url, cg_mapping, dl_mapping, san_mapping)
    except Exception as e:
        logger.warning("Could not write to DB (table may not exist yet): %s", e)
        logger.info("Run the migration first: 20260219_add_onchain_tables.sql")

    write_json(cg_mapping, dl_mapping, san_mapping)

    logger.info("")
    logger.info("Done. Next: run onchain_collector.py --backfill")


if __name__ == "__main__":
    main()
