"""
Unicorn Pipeline Worker
=======================

Runs on schedule (every 5 minutes). Self-contained pipeline that:
- Universe refresh (daily full scan, incremental updates)
- Fetches 5m candles from Binance REST for embryo coins (not in monitored universe)
- Gate evaluation for promotion candidates
- Trigger evaluation + signal emission for EMBRYO_ACTIVE/SCANNING symbols
- Promotion decisions (EMBRYO -> EMBRYO_ACTIVE)
- Demotion rules (pump traps, timeout, eviction)
- Cap enforcement (MAX_EMBRYO_ACTIVE)
- State persistence to Redis

Self-contained: fetches its own candles, evaluates triggers, and emits signals.
Embryo coins are NOT in the monitored universe, so the normal ingestor/signal_worker
pipeline never sees them. This worker handles the entire lifecycle.
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
    store_signal, check_staleness,
    candle_key, candle_meta_key, serialize_candle,
    ohlcv_to_candle_dict, RING_BUFFER_SIZE,
    UNICORN_STATE_TTL_S,
)
from indicators import OHLCV
from setups.u1_unicorn_ignition import (
    U1Config, U1State, U1Phase, U1Evaluator,
    base_asset, is_stablecoin_base, is_wrapped, is_leverage_token,
    check_demotion, load_funding_rates,
)

logger = logging.getLogger("unicorn_pipeline")

# =============================================================================
# Config
# =============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL")
PIPELINE_INTERVAL_S = int(os.getenv("UNICORN_PIPELINE_INTERVAL_S", "300"))  # 5 min
FULL_REFRESH_HOUR = 0  # UTC hour for full universe refresh
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BINANCE_API = "https://api.binance.com"
KLINE_FETCH_LIMIT = 200   # 200 5m bars = ~16.7 hours (enough for 96-bar windows + buffer)
KLINE_FETCH_DELAY_S = 0.1  # Rate limit delay between Binance API requests


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
# Candle Fetching (self-contained — embryo coins aren't in monitored universe)
# =============================================================================

def fetch_5m_klines(symbol: str, limit: int = KLINE_FETCH_LIMIT) -> list[OHLCV]:
    """Fetch 5m candles from Binance REST API.

    Returns list[OHLCV] oldest->newest. Timestamps are ms (Binance format).
    Used for embryo coins that have no WS feed or ingestor coverage.
    """
    try:
        resp = requests.get(
            f"{BINANCE_API}/api/v3/klines",
            params={"symbol": symbol, "interval": "5m", "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        candles = []
        for k in resp.json():
            candles.append(OHLCV(
                timestamp=int(k[0]),   # open_time_ms (Binance = ms)
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            ))
        return candles
    except Exception as e:
        logger.debug("Failed to fetch 5m klines for %s: %s", symbol, e)
        return []


def seed_candles_to_redis(r, symbol: str, tf: str, candles: list[OHLCV]):
    """Bulk-seed candle ring buffer from fetched OHLCV list.

    Replaces existing data for this symbol/tf. Used by the pipeline
    for embryo coins that have no WS feed (not in monitored universe).
    """
    if not candles:
        return

    key = candle_key(symbol, tf)
    meta_k = candle_meta_key(symbol, tf)

    pipe = r.pipeline(transaction=True)
    pipe.delete(key)
    for c in candles:
        pipe.rpush(key, serialize_candle(ohlcv_to_candle_dict(c)))
    pipe.ltrim(key, -RING_BUFFER_SIZE, -1)

    last = candles[-1]
    pipe.hset(meta_k, mapping={
        "last_bar_ts": str(last.timestamp),
        "last_close": str(last.close),
        "is_final": "True",
        "as_of_ts": str(time.time()),
        "count": str(min(len(candles), RING_BUFFER_SIZE)),
    })
    pipe.execute()


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
    """Runs the Unicorn pipeline on schedule. Self-contained: fetches candles,
    evaluates gates + triggers, emits signals."""

    SETUP_VERSIONS = {"U1": "U1_v3.0"}

    def __init__(self):
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.evaluator = U1Evaluator()
        self.last_full_refresh: Optional[datetime] = None
        self._funding_rates: dict = {}
        self._funding_rates_ts: float = 0.0

    def run(self):
        """Main loop."""
        setup_logging()
        logger.info("Unicorn pipeline starting (self-contained mode)")

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

        # Refresh funding rates (cached 1h inside load_funding_rates)
        if DATABASE_URL and time.time() - self._funding_rates_ts > 3600:
            self._funding_rates = load_funding_rates(DATABASE_URL)
            self._funding_rates_ts = time.time()

        # Counters for diagnostics
        evaluated = 0
        promoted = []
        demoted = []
        candle_fetched = 0
        candle_cached = 0
        gate_passed = 0
        triggers_fired = 0

        for symbol in universe:
            try:
                result = self._evaluate_symbol(symbol, active_symbols)
                evaluated += 1

                if result is None:
                    continue

                state, metrics, was_fetched, trigger_fired = result

                if was_fetched:
                    candle_fetched += 1
                else:
                    candle_cached += 1

                if state.gate1_passed and state.gate2_passed and state.gate3_passed:
                    gate_passed += 1

                if trigger_fired:
                    triggers_fired += 1

                # Check promotion
                if state.phase in (U1Phase.EMBRYO_ACTIVE, U1Phase.SCANNING) \
                        and symbol not in active_symbols:
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
            "candle_fetched": str(candle_fetched),
            "candle_cached": str(candle_cached),
            "gate_passed": str(gate_passed),
            "triggers_fired": str(triggers_fired),
            "cycle_duration_s": str(time.time() - cycle_start),
        })

        logger.info(
            "Pipeline cycle: universe=%d evaluated=%d "
            "candles(fetched=%d cached=%d) "
            "gates_passed=%d triggers=%d "
            "promoted=%d demoted=%d active=%d "
            "duration=%.1fs",
            len(universe), evaluated,
            candle_fetched, candle_cached,
            gate_passed, triggers_fired,
            len(promoted), len(demoted),
            get_embryo_active_count(self.r),
            time.time() - cycle_start,
        )

    def _evaluate_symbol(self, symbol: str, active_symbols: set) -> Optional[tuple]:
        """Evaluate a single symbol: fetch candles -> gates -> trigger -> signal.

        Returns (state, metrics, was_fetched, trigger_fired) or None.
        Incremental: processes one symbol at a time, no bulk accumulation.
        """
        tf = U1Config.GATE_TIMEFRAME
        required_bars = U1Config.GATE_WINDOW_BARS * 2  # 96

        # Step 1: Get candles (try Redis first, fetch from Binance if needed)
        candles = get_candles_as_ohlcv(self.r, symbol, tf)
        was_fetched = False

        need_fetch = len(candles) < required_bars
        if not need_fetch:
            # Check freshness — re-fetch if stale (>2.5x candle interval = 12.5 min)
            staleness = check_staleness(self.r, symbol, tf)
            if staleness["stale"]:
                need_fetch = True

        if need_fetch:
            fetched = fetch_5m_klines(symbol, limit=KLINE_FETCH_LIMIT)
            if fetched:
                seed_candles_to_redis(self.r, symbol, tf, fetched)
                candles = fetched
                was_fetched = True
            time.sleep(KLINE_FETCH_DELAY_S)  # Rate limit

        if len(candles) < required_bars:
            return None

        # Step 2: Read existing state
        state_dict = read_unicorn_state(self.r, symbol)
        prev_state = U1State.from_dict(state_dict) if state_dict else None

        # Step 3: Evaluate gates
        state = self.evaluator.evaluate_gates(candles, prev_state)

        # Store state + metrics (refreshes TTL)
        store_unicorn_state(self.r, symbol, state.to_dict())
        store_unicorn_metrics(self.r, symbol, state.metrics)

        # Step 4: Evaluate trigger for promoted symbols
        trigger_fired = False
        if state.phase in (U1Phase.EMBRYO_ACTIVE, U1Phase.SCANNING,
                           U1Phase.IGNITION_WATCH):
            signal = self.evaluator.evaluate_trigger(
                candles, state, symbol, tf,
                funding_rates=self._funding_rates,
                database_url=DATABASE_URL,
            )
            if signal:
                self._store_u1_signal(signal, symbol, tf)
                trigger_fired = True
                # Update state after trigger fired (phase changed to IGNITION)
                store_unicorn_state(self.r, symbol, state.to_dict())

        return state, state.metrics, was_fetched, trigger_fired

    def _store_u1_signal(self, signal, symbol: str, tf: str):
        """Store U1 signal in Redis + Postgres."""
        bar_ts_str = str(signal.bar_start_ts.isoformat()) if signal.bar_start_ts else ""

        payload = {
            "setup_id": "U1",
            "symbol": symbol,
            "timeframe": tf,
            "direction": signal.direction.value,
            "setup_score": signal.setup_score,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "tp1_price": signal.tp1_price,
            "tp2_price": signal.tp2_price,
            "r_value": signal.r_value,
            "time_stop_bars": signal.time_stop_bars,
            "time_stop_policy": signal.time_stop_policy,
            "bar_start_ts": bar_ts_str,
            "signal_ts": signal.signal_ts.isoformat() if signal.signal_ts else None,
            "action_by_ts": signal.action_by_ts.isoformat() if signal.action_by_ts else None,
            "age_seconds": signal.age_seconds,
            "as_of_ts": time.time(),
            "rel_volume": signal.rel_volume,
            "compression_box_high": signal.compression_box_high,
            "compression_box_low": signal.compression_box_low,
            "confirmations_met": signal.confirmations_met,
            "tradability_score": signal.tradability_score,
            "participation_score": signal.participation_score,
            "compression_score": signal.compression_score,
            "embryo_active_score": signal.embryo_active_score,
            "trigger_path": getattr(signal, "trigger_path", "traditional"),
        }

        if signal.explain:
            payload["why_items"] = signal.explain.why_items

        store_signal(
            self.r, symbol, tf, "U1",
            signal_payload=payload,
            setup_score=signal.setup_score,
            bar_start_ts=bar_ts_str,
        )

        # Persist to Postgres
        reason = "; ".join(signal.explain.why_items) if signal.explain else None
        self._persist_signal_to_pg(symbol, tf, "U1", signal.setup_score, reason, payload)

        logger.info(
            "Signal: U1 %s %s:%s score=%.3f rel_vol=%.1fx",
            signal.direction.value, symbol, tf,
            signal.setup_score, signal.rel_volume,
        )

    def _persist_signal_to_pg(self, symbol: str, tf: str, setup_id: str,
                               score: float, reason: str, payload: dict):
        """Persist signal to Postgres opportunities table.

        Upsert pattern: update existing active record in place, or insert new.
        Prevents duplicate expired rows when the same signal persists
        across multiple pipeline cycles.
        """
        if not DATABASE_URL:
            return
        try:
            import psycopg
            payload_json = json.dumps(payload)
            setup_version = self.SETUP_VERSIONS.get(setup_id, f"{setup_id}_v1.0")
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    # Try to update existing active record in place
                    cur.execute("""
                        UPDATE opportunities
                        SET score = %s, reason = %s, payload_json = %s,
                            setup_version = %s, updated_ts = NOW()
                        WHERE symbol = %s AND timeframe = %s AND setup_id = %s
                          AND status = 'active'
                        RETURNING id;
                    """, (score, reason, payload_json, setup_version,
                          symbol, tf, setup_id))
                    updated = cur.fetchone()

                    if not updated:
                        # No active record exists — insert new one
                        cur.execute("""
                            INSERT INTO opportunities
                                (symbol, timeframe, setup_id, decision, score, reason,
                                 payload_json, status, setup_version)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s);
                        """, (symbol, tf, setup_id, 'SIGNAL', score, reason,
                              payload_json, setup_version))
                conn.commit()
        except Exception as e:
            logger.debug("Postgres persist failed for %s:%s:%s: %s",
                         setup_id, symbol, tf, e)

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
