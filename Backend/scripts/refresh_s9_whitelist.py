#!/usr/bin/env python3
"""
Refresh S9 Symbol Whitelist — Binance USDT-M perp-eligible universe
===================================================================

Purpose
-------
The canonical S9 universe (Phase 0, perp rerank) is the set of Binance USDT-M
perpetual-eligible symbols. Binance lists/delists perps roughly monthly; this
script re-fetches `fapi.binance.com/fapi/v1/exchangeInfo`, writes the refreshed
whitelist to `Backend/config/s9_symbol_whitelist.txt`, and alerts if the set
diverges from the previous snapshot.

Output
------
- `Backend/config/s9_symbol_whitelist.txt`                 (new canonical set)
- `Backend/scripts/output/s9_whitelist_refresh_<ts>.json`  (diff report)

Intended to be run weekly by a cron/supervised task inside `strats_evals`.

Usage
-----
    docker exec backend-worker-1 python3 /backend/scripts/refresh_s9_whitelist.py
    docker exec backend-worker-1 python3 /backend/scripts/refresh_s9_whitelist.py --dry-run

Exits non-zero if the Binance call fails (so cron can alert).
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("FATAL: requests is required", file=sys.stderr)
    sys.exit(2)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FAPI_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"

# Resolve paths for both in-container (WORKDIR /backend) and host execution.
if os.path.isdir("/backend"):
    REPO_BACKEND = Path("/backend")
else:
    REPO_BACKEND = Path(__file__).resolve().parents[1]

WHITELIST_PATH = REPO_BACKEND / "config" / "s9_symbol_whitelist.txt"
OUTPUT_DIR = REPO_BACKEND / "scripts" / "output"


def fetch_perp_symbols() -> set[str]:
    """Fetch all TRADING USDT-M perp symbols from Binance futures."""
    logger.info("Fetching Binance futures exchangeInfo: %s", FAPI_EXCHANGE_INFO)
    t0 = time.time()
    resp = requests.get(FAPI_EXCHANGE_INFO, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    logger.info("Fetched in %.1fs", time.time() - t0)

    symbols: set[str] = set()
    for entry in data.get("symbols", []):
        if (
            entry.get("contractType") == "PERPETUAL"
            and entry.get("quoteAsset") == "USDT"
            and entry.get("status") == "TRADING"
        ):
            symbols.add(entry["symbol"].upper())

    logger.info("Parsed %d TRADING USDT-M perpetual symbols", len(symbols))
    return symbols


def load_existing_whitelist(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with open(path, "r") as f:
        return {line.strip().upper() for line in f if line.strip()}


def write_whitelist(path: Path, symbols: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_symbols = sorted(symbols)
    with open(path, "w") as f:
        f.write("\n".join(sorted_symbols) + "\n")
    logger.info("Wrote %d symbols to %s", len(sorted_symbols), path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh S9 perp whitelist")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print diff without writing the whitelist file.",
    )
    args = parser.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / f"s9_whitelist_refresh_{ts}.json"
    logger.info("Report will be written to %s", report_path)

    try:
        new_symbols = fetch_perp_symbols()
    except Exception as e:
        logger.error("Binance fetch failed: %s", e)
        return 3

    if not new_symbols:
        logger.error("Binance returned zero perp symbols — aborting (likely API change)")
        return 4

    old_symbols = load_existing_whitelist(WHITELIST_PATH)
    added = sorted(new_symbols - old_symbols)
    removed = sorted(old_symbols - new_symbols)
    unchanged = len(new_symbols & old_symbols)

    report = {
        "timestamp": ts,
        "whitelist_path": str(WHITELIST_PATH),
        "old_count": len(old_symbols),
        "new_count": len(new_symbols),
        "unchanged_count": unchanged,
        "added_count": len(added),
        "removed_count": len(removed),
        "added": added,
        "removed": removed,
        "dry_run": args.dry_run,
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    logger.info(
        "Diff vs previous: unchanged=%d, added=%d, removed=%d",
        unchanged, len(added), len(removed),
    )
    if added:
        logger.info("Added (first 20): %s", ", ".join(added[:20]))
    if removed:
        logger.warning("Removed (first 20): %s", ", ".join(removed[:20]))

    if args.dry_run:
        logger.info("Dry run — not writing whitelist file")
        return 0

    write_whitelist(WHITELIST_PATH, new_symbols)

    # Alert if churn is significant (>5% of universe changed) — cron can pick this up
    total_churn = len(added) + len(removed)
    if old_symbols and total_churn > 0.05 * len(old_symbols):
        logger.warning(
            "ALERT: whitelist churn %d symbols (>5%% of previous %d) — check diff at %s",
            total_churn, len(old_symbols), report_path,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
