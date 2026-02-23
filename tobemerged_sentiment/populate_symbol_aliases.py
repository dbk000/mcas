#!/usr/bin/env python3
"""
Populate symbol_aliases table from Binance exchange info + ambiguous_tickers.json.

For each TRADING USDT pair, inserts:
  - ticker alias (e.g. 'DOGE' for DOGEUSDT)
  - cashtag alias (e.g. '$DOGE')
Marks tickers from ambiguous_tickers.json as is_ambiguous=TRUE.

Usage:
    python3 populate_symbol_aliases.py [--db-url DATABASE_URL]
"""

import os
import sys
import json
import logging
import argparse

import requests
import psycopg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"

# Resolve ambiguous_tickers.json relative to this script or social_collector/app/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AMBIGUOUS_PATHS = [
    os.path.join(SCRIPT_DIR, '..', 'social_collector', 'app', 'ambiguous_tickers.json'),
    os.path.join(SCRIPT_DIR, 'ambiguous_tickers.json'),
    '/app/ambiguous_tickers.json',
]


def load_ambiguous_tickers():
    for path in AMBIGUOUS_PATHS:
        abspath = os.path.abspath(path)
        if os.path.exists(abspath):
            with open(abspath) as f:
                data = json.load(f)
            tickers = set(t.upper() for t in data.get("ambiguous_tickers", []))
            logger.info("Loaded %d ambiguous tickers from %s", len(tickers), abspath)
            return tickers
    logger.warning("ambiguous_tickers.json not found in any expected location")
    return set()


def fetch_usdt_pairs():
    """Fetch all TRADING USDT pairs from Binance."""
    logger.info("Fetching Binance exchange info...")
    resp = requests.get(BINANCE_EXCHANGE_INFO_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    pairs = []
    for sym in data.get("symbols", []):
        if (sym.get("quoteAsset") == "USDT"
                and sym.get("status") == "TRADING"):
            pairs.append({
                "symbol": sym["symbol"],          # e.g. DOGEUSDT
                "baseAsset": sym["baseAsset"],     # e.g. DOGE
            })

    logger.info("Found %d TRADING USDT pairs", len(pairs))
    return pairs


def populate(db_url: str):
    ambiguous = load_ambiguous_tickers()
    pairs = fetch_usdt_pairs()

    rows = []
    for p in pairs:
        symbol = p["symbol"]           # DOGEUSDT
        ticker = p["baseAsset"].upper()  # DOGE
        is_ambig = ticker in ambiguous

        # Ticker alias
        rows.append((symbol, ticker, 'ticker', is_ambig))
        # Cashtag alias
        rows.append((symbol, f'${ticker}', 'cashtag', is_ambig))

    logger.info("Inserting %d alias rows (%d pairs × 2)...", len(rows), len(pairs))

    sql = """
        INSERT INTO symbol_aliases (symbol, alias, alias_type, is_ambiguous)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (symbol, alias) DO UPDATE SET
            is_ambiguous = EXCLUDED.is_ambiguous
    """

    inserted = 0
    batch_size = 500
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                cur.executemany(sql, batch)
                inserted += len(batch)
                logger.info("  inserted batch %d-%d / %d",
                            i, i + len(batch), len(rows))
        conn.commit()

    logger.info("Done. Total rows upserted: %d", inserted)


def main():
    parser = argparse.ArgumentParser(description="Populate symbol_aliases table")
    parser.add_argument("--db-url", default=None,
                        help="PostgreSQL connection URL")
    args = parser.parse_args()

    db_url = args.db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        # Fallback: connect via docker network IP
        db_url = "postgresql://trading:mcas_local_dev_2026@172.18.0.11:5432/trading"
        logger.info("No DATABASE_URL set, using container network address")

    logger.info("Connecting to: %s", db_url.split("@")[0] + "@***")
    populate(db_url)


if __name__ == "__main__":
    main()
