"""
Unicorn Pipeline Worker
=======================

Runs on schedule (every 5 minutes). Owns:
- Universe refresh (daily full scan, incremental updates)
- Gate evaluation for promotion candidates
- Promotion decisions (EMBRYO -> EMBRYO_ACTIVE)
- Demotion rules (pump traps, timeout, eviction)
- Cap enforcement (MAX_EMBRYO_ACTIVE)
- State persistence to Redis

Rule: Pipeline worker writes state, signal worker reads state and emits signals.
"""

import os
import sys
import time
import json
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

import redis

from cache_contract import (
    unicorn_universe_key, unicorn_active_key, unicorn_state_key,
    unicorn_metrics_key, unicorn_pipeline_meta_key,
    store_unicorn_state, read_unicorn_state, store_unicorn_metrics,
    add_to_embryo_active, remove_from_embryo_active,
    get_embryo_active_symbols, get_embryo_active_count,
    get_candles_as_ohlcv, b2s, b2f,
    UNICORN_STATE_TTL_S,
)
from setups.u1_unicorn_ignition import (
    U1Config, U1State, U1Phase, U1Evaluator,
    base_asset, is_stablecoin_base, is_wrapped, is_leverage_token,
    check_demotion,
)

logger = logging.getLogger("unicorn_pipeline")

# =============================================================================
# Config
# =============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PIPELINE_INTERVAL_S = int(os.getenv("UNICORN_PIPELINE_INTERVAL_S", "300"))  # 5 min
FULL_REFRESH_HOUR = 0  # UTC hour for full universe refresh
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BINANCE_API = "https://api.binance.com"


# =============================================================================
# Logging
# =============================================================================

def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))


# =============================================================================
# Universe Management
# =============================================================================

def fetch_exchange_symbols() -> list[dict]:
    """Fetch all USDT spot pairs from Binance with metadata."""
    try:
        resp = requests.get(f"{BINANCE_API}/api/v3/exchangeInfo", timeout=30)
        resp.raise_for_status()
        data = resp.json()

        symbols = []
        for s in data.get("symbols", []):
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                if s.get("isSpotTradingAllowed", False):
                    # Extract min notional from filters
                    min_notional = 0.0
                    for f in s.get("filters", []):
                        if f.get("filterType") == "NOTIONAL":
                            min_notional = float(f.get("minNotional", 0))
                            break

                    symbols.append({
                        "symbol": s["symbol"],
                        "base": s["baseAsset"],
                        "quote": s["quoteAsset"],
                        "status": s["status"],
                        "min_notional": min_notional,
                    })

        return symbols
    except Exception as e:
        logger.error(f"Failed to fetch exchange symbols: {e}")
        return []


def fetch_24h_volumes() -> dict[str, float]:
    """Fetch 24h quote volumes for all symbols."""
    try:
        resp = requests.get(f"{BINANCE_API}/api/v3/ticker/24hr", timeout=30)
        resp.raise_for_status()
        data = resp.json()

        volumes = {}
        for t in data:
            symbol = t.get("symbol", "")
            quote_vol = float(t.get("quoteVolume", 0))
            volumes[symbol] = quote_vol

        return volumes
    except Exception as e:
        logger.error(f"Failed to fetch 24h volumes: {e}")
        return {}


def fetch_daily_candles(symbol: str, days: int = 30) -> list[dict]:
    """Fetch daily candles for hype check."""
    try:
        resp = requests.get(
            f"{BINANCE_API}/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": days},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        candles = []
        for k in data:
            candles.append({
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_volume": float(k[7]),
            })
        return candles
    except Exception as e:
        logger.debug(f"Failed to fetch daily candles for {symbol}: {e}")
        return []


def had_recent_hype(symbol: str, lookback_days: int = 30) -> bool:
    """
    Check if symbol had a hype spike in the lookback window.
    Returns True if EITHER condition met:
    - max(rel_volume_30d) > HYPE_REL_VOL_CAP
    - max(range_pct_30d) > HYPE_RANGE_CAP
    """
    candles = fetch_daily_candles(symbol, lookback_days)
    if not candles:
        return False

    volumes = [c["quote_volume"] for c in candles]
    if not volumes:
        return False

    baseline_vol = sorted(volumes)[len(volumes) // 2]  # median

    # Check volume spike
    if baseline_vol > 0:
        max_rel_vol = max(v / baseline_vol for v in volumes)
        if max_rel_vol > U1Config.HYPE_REL_VOL_CAP:
            return True

    # Check range spike
    for c in candles:
        if c["close"] > 0:
            range_pct = (c["high"] - c["low"]) / c["close"]
            if range_pct > U1Config.HYPE_RANGE_CAP:
                return True

    return False


def build_embryo_universe(r) -> list[str]:
    """
    Build embryo universe - deterministic selection.
    MVP: Spot USDT pairs only.
    """
    logger.info("Building embryo universe...")

    # Fetch all symbols
    all_symbols = fetch_exchange_symbols()
    logger.info(f"Fetched {len(all_symbols)} USDT spot pairs from Binance")

    # Fetch 24h volumes
    volumes = fetch_24h_volumes()

    # Step 2: Hard exclusions
    excluded = set()
    for info in all_symbols:
        symbol = info["symbol"]

        # Min notional too high
        if info["min_notional"] > U1Config.MAX_MIN_NOTIONAL:
            excluded.add(symbol)
            continue

        # Stablecoin base
        if is_stablecoin_base(symbol):
            excluded.add(symbol)
            continue

        # Wrapped or leverage
        if is_wrapped(symbol) or is_leverage_token(symbol):
            excluded.add(symbol)
            continue

    candidates = [s["symbol"] for s in all_symbols if s["symbol"] not in excluded]
    logger.info(f"After hard exclusions: {len(candidates)} candidates")

    # Step 3: Volume band filter
    volume_filtered = []
    for symbol in candidates:
        vol_24h = volumes.get(symbol, 0)
        if U1Config.MIN_VOLUME_USD <= vol_24h <= U1Config.MAX_VOLUME_USD:
            volume_filtered.append(symbol)

    logger.info(f"After volume filter ({U1Config.MIN_VOLUME_USD}-{U1Config.MAX_VOLUME_USD} USD): {len(volume_filtered)} candidates")

    # Step 4: Exclude recent hype (sampling for performance)
    # Full hype check is expensive - do it on a sample or during promotion
    final = volume_filtered  # Skip hype check in bulk refresh for performance

    # Store in Redis
    key = unicorn_universe_key()
    if final:
        pipe = r.pipeline()
        pipe.delete(key)
        pipe.sadd(key, *final)
        pipe.execute()

    logger.info(f"Embryo universe: {len(final)} symbols")
    return sorted(final)


# =============================================================================
# Pipeline Worker
# =============================================================================

class UnicornPipeline:
    """Runs the Unicorn pipeline on schedule."""

    def __init__(self):
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.evaluator = U1Evaluator()
        self.last_full_refresh: Optional[datetime] = None

    def run(self):
        """Main loop."""
        setup_logging()
        logger.info("Unicorn pipeline starting")

        while True:
            try:
                self._run_cycle()
            except Exception as e:
                logger.error(f"Pipeline cycle error: {e}", exc_info=True)

            time.sleep(PIPELINE_INTERVAL_S)

    def _run_cycle(self):
        """Run a single pipeline cycle."""
        now = datetime.now(timezone.utc)
        cycle_start = time.time()

        # Check if full refresh needed (daily at FULL_REFRESH_HOUR UTC)
        need_full_refresh = False
        if self.last_full_refresh is None:
            need_full_refresh = True
        elif now.hour == FULL_REFRESH_HOUR and (now - self.last_full_refresh).total_seconds() > 3600:
            need_full_refresh = True

        if need_full_refresh:
            build_embryo_universe(self.r)
            self.last_full_refresh = now

        # Get universe
        universe = [b2s(m) for m in self.r.smembers(unicorn_universe_key())]
        if not universe:
            logger.warning("Empty embryo universe, skipping cycle")
            return

        # Get current EMBRYO_ACTIVE
        active_symbols = {sym for sym, _ in get_embryo_active_symbols(self.r)}

        # Evaluate candidates
        promoted = []
        demoted = []
        evaluated = 0

        for symbol in universe:
            try:
                result = self._evaluate_symbol(symbol)
                evaluated += 1

                if result is None:
                    continue

                state, metrics = result

                # Check promotion
                if state.phase == U1Phase.EMBRYO_ACTIVE and symbol not in active_symbols:
                    promoted.append((symbol, state))

                # Check demotion
                if symbol in active_symbols and state.phase == U1Phase.EMBRYO:
                    demoted.append(symbol)

            except Exception as e:
                logger.debug(f"Failed to evaluate {symbol}: {e}")

        # Apply promotions (with cap enforcement)
        self._apply_promotions(promoted)

        # Apply demotions
        for symbol in demoted:
            remove_from_embryo_active(self.r, symbol)
            logger.info(f"Demoted {symbol} from EMBRYO_ACTIVE")

        # Update pipeline meta
        self.r.hset(unicorn_pipeline_meta_key(), mapping={
            "last_cycle_ts": str(now.isoformat()),
            "symbols_evaluated": str(evaluated),
            "promoted_count": str(len(promoted)),
            "demoted_count": str(len(demoted)),
            "active_count": str(get_embryo_active_count(self.r)),
            "cycle_duration_s": str(time.time() - cycle_start),
        })

        logger.info(
            f"Pipeline cycle: evaluated={evaluated}, "
            f"promoted={len(promoted)}, demoted={len(demoted)}, "
            f"active={get_embryo_active_count(self.r)}, "
            f"duration={time.time() - cycle_start:.1f}s"
        )

    def _evaluate_symbol(self, symbol: str) -> Optional[tuple[U1State, dict]]:
        """Evaluate a single symbol for gate passage."""

        # Read existing state
        state_dict = read_unicorn_state(self.r, symbol)
        prev_state = U1State.from_dict(state_dict) if state_dict else None

        # Get candles from Redis
        tf = U1Config.GATE_TIMEFRAME
        candles = get_candles_as_ohlcv(self.r, symbol, tf)

        if len(candles) < U1Config.GATE_WINDOW_BARS * 2:
            # Not enough data yet
            return None

        # Evaluate gates
        state = self.evaluator.evaluate_gates(candles, prev_state)

        # Store state (refreshes TTL)
        store_unicorn_state(self.r, symbol, state.to_dict())

        # Store metrics
        store_unicorn_metrics(self.r, symbol, state.metrics)

        return state, state.metrics

    def _apply_promotions(self, promoted: list[tuple[str, U1State]]):
        """Apply promotions with cap enforcement."""
        if not promoted:
            return

        current_count = get_embryo_active_count(self.r)
        current_active = {sym: score for sym, score in get_embryo_active_symbols(self.r)}

        # Add all promoted to candidate pool
        all_candidates = dict(current_active)
        for symbol, state in promoted:
            all_candidates[symbol] = state.embryo_active_score

        # Sort by score descending
        ranked = sorted(all_candidates.items(), key=lambda x: x[1], reverse=True)

        # Take top MAX_EMBRYO_ACTIVE
        keep = set()
        for symbol, score in ranked[:U1Config.MAX_EMBRYO_ACTIVE]:
            keep.add(symbol)
            add_to_embryo_active(self.r, symbol, score)

        # Remove those that didn't make the cut
        for symbol in all_candidates:
            if symbol not in keep:
                remove_from_embryo_active(self.r, symbol)
                if symbol in current_active:
                    logger.info(f"Evicted {symbol} from EMBRYO_ACTIVE (cap enforcement)")

        # Log promotions
        for symbol, state in promoted:
            if symbol in keep:
                logger.info(
                    f"Promoted {symbol} to EMBRYO_ACTIVE "
                    f"(score={state.embryo_active_score:.1f})"
                )


# =============================================================================
# Entry point
# =============================================================================

def run_pipeline():
    """Entry point for running the pipeline."""
    pipeline = UnicornPipeline()
    pipeline.run()


if __name__ == "__main__":
    run_pipeline()
