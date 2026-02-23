#!/usr/bin/env python3
"""
U2 Backfill Opportunities — Write winning config signals to the opportunities table.

Only run after u2_research_sweep.py finds a validated config.
Reads the winning config from u2_research_results.json and re-evaluates to generate
opportunities for simulation.

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/u2_backfill_opportunities.py [--days 90] [--config-file u2_research_results.json]

Writes to opportunities table with setup_id='U2', status='expired'.
"""

import os
import sys
import time
import json
import hashlib
import logging
import argparse
import numpy as np
from datetime import datetime, timezone
from collections import defaultdict

# Path setup
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/indicators.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))
sys.path.insert(0, os.path.join(os.path.abspath(worker_app_dir), 'setups'))

import psycopg
from indicators import OHLCV

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

# Import from the sweep script for consistency
sys.path.insert(0, os.path.dirname(__file__))
from u2_research_sweep import (
    load_ohlcv_1h, load_social_symbols, precompute_symbol_features,
    SweepConfig, evaluate_config, EXCLUDED_SYMBOLS,
)


def insert_opportunities(opportunities: list[dict], batch_size: int = 500) -> int:
    """Insert opportunities into Postgres. Returns count inserted."""
    if not opportunities:
        return 0

    inserted = 0
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for i in range(0, len(opportunities), batch_size):
                batch = opportunities[i:i + batch_size]
                values = []
                for opp in batch:
                    values.append((
                        opp["created_ts"],
                        opp["symbol"],
                        opp["timeframe"],
                        opp["setup_id"],
                        opp["decision"],
                        opp["score"],
                        opp["reason"],
                        json.dumps(opp["payload"]),
                        "expired",
                        opp["setup_version"],
                        opp["batch_id"],
                    ))
                cur.executemany("""
                    INSERT INTO opportunities
                        (created_ts, symbol, timeframe, setup_id, decision,
                         score, reason, payload_json, status,
                         setup_version, batch_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, values)
                inserted += len(batch)
        conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser(description="U2 Backfill Opportunities")
    parser.add_argument("--days", type=int, default=90, help="Days to backfill (default: 90)")
    parser.add_argument("--config-file", type=str, default=None,
                        help="Path to u2_research_results.json (auto-detected if not specified)")
    parser.add_argument("--config-id", type=int, default=None,
                        help="Config ID to use from results (default: best WF-passing)")
    parser.add_argument("--setup-version", type=str, default="U2_v0.1",
                        help="Setup version tag (default: U2_v0.1)")
    parser.add_argument("--clear-expired", action="store_true",
                        help="Delete existing U2 expired backfill opportunities first")
    args = parser.parse_args()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    # Find config file
    if args.config_file:
        config_path = args.config_file
    else:
        for candidate in [
            "/scripts/u2_research_results.json",
            os.path.join(os.path.dirname(__file__), "u2_research_results.json"),
        ]:
            if os.path.exists(candidate):
                config_path = candidate
                break
        else:
            logger.error("Cannot find u2_research_results.json. Run u2_research_sweep.py first.")
            sys.exit(1)

    # Load results
    with open(config_path) as f:
        results = json.load(f)

    # Find the winning config
    wf_passing = results.get("wf_passing_configs", [])
    if not wf_passing:
        logger.error("No walk-forward passing configs found in results. Nothing to backfill.")
        sys.exit(1)

    if args.config_id is not None:
        target = [c for c in wf_passing if c["config_id"] == args.config_id]
        if not target:
            logger.error("Config ID %d not found in WF-passing configs", args.config_id)
            sys.exit(1)
        winning_config = target[0]["config"]
    else:
        winning_config = wf_passing[0]["config"]

    logger.info("=" * 60)
    logger.info("U2 Backfill Opportunities")
    logger.info("=" * 60)
    logger.info("  Config: %s", json.dumps(winning_config, indent=2))
    logger.info("  Days: %d", args.days)
    logger.info("  Setup version: %s", args.setup_version)
    logger.info("=" * 60)

    # Generate batch ID
    batch_hash = hashlib.sha256(
        f"{datetime.now(timezone.utc).isoformat()}U2{args.setup_version}".encode()
    ).hexdigest()[:8]
    batch_id = f"backfill_{datetime.now(timezone.utc).strftime('%Y%m%d')}_U2_{batch_hash}"

    # Clear existing if requested
    if args.clear_expired:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM opportunities
                    WHERE status = 'expired'
                      AND batch_id LIKE 'backfill_%%'
                      AND setup_id = 'U2'
                      AND created_ts >= NOW() - %s * INTERVAL '1 day'
                """, (args.days,))
                deleted = cur.rowcount
            conn.commit()
        logger.info("Cleared %d existing U2 expired opportunities", deleted)

    # Load data
    ohlcv_dict, symbols, timestamps = load_ohlcv_1h(args.days + 30)
    social_symbols = load_social_symbols(30)

    # Precompute features
    logger.info("Precomputing features for %d symbols...", len(symbols))
    t0 = time.time()
    features_list = []
    features_dict = {}
    for sym in symbols:
        feat = precompute_symbol_features(ohlcv_dict[sym], sym)
        if feat is not None:
            features_list.append(feat)
            features_dict[sym] = feat
    logger.info("Features computed for %d symbols in %.1fs", len(features_list), time.time() - t0)

    # Build SweepConfig from winning config dict
    cfg = SweepConfig(config_id=0, **winning_config)

    # Evaluate to get signals
    warmup_bars = 96
    # Only keep signals within the --days window
    cutoff_ms = int((datetime.now(timezone.utc).timestamp() - args.days * 86400) * 1000)
    all_signals = evaluate_config(cfg, features_list, social_symbols, warmup_bars)
    signals = [s for s in all_signals if s["ts_ms"] >= cutoff_ms]
    logger.info("Generated %d signals within %d-day window (%d total)", len(signals), args.days, len(all_signals))

    # Convert signals to opportunities
    opportunities = []
    for sig in signals:
        bar_dt = datetime.fromtimestamp(sig["ts_ms"] / 1000, tz=timezone.utc)
        payload = {
            "setup_id": "U2",
            "symbol": sig["symbol"],
            "timeframe": "1h",
            "direction": "long",
            "setup_score": sig["score"],
            "entry_price": sig["entry_price"],
            "atr_20": sig["atr_20"],
            "vol_z": sig["vol_z"],
            "bar_start_ts": sig["ts_ms"],
            "as_of_ts": sig["ts_ms"] / 1000,
            "config": winning_config,
        }
        reason = (f"U2 ignition: score={sig['score']:.3f}, vol_z={sig['vol_z']:.1f}, "
                  f"entry={sig['entry_price']:.8f}")
        opportunities.append({
            "created_ts": bar_dt,
            "symbol": sig["symbol"],
            "timeframe": "1h",
            "setup_id": "U2",
            "decision": "SIGNAL",
            "score": sig["score"],
            "reason": reason,
            "payload": payload,
            "setup_version": args.setup_version,
            "batch_id": batch_id,
        })

    # Insert in batches
    n_inserted = insert_opportunities(opportunities)
    logger.info("Inserted %d U2 opportunities", n_inserted)

    # Summary
    logger.info("=" * 60)
    logger.info("U2 backfill complete")
    logger.info("  Signals found: %d", len(signals))
    logger.info("  Opportunities stored: %d", n_inserted)
    logger.info("  Batch ID: %s", batch_id)
    logger.info("  Unique symbols: %d", len(set(s["symbol"] for s in signals)))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
