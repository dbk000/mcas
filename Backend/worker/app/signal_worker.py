"""
Signal Worker — Redis indicators → Redis signals.

Separate Docker service (invariant #2). Consumes indicators_ready stream
events, reads indicator snapshots with mandatory m1/snap/m2 consistency
(invariant #22), runs S1/S2 evaluators, stores signals in Redis ZSETs.

BTC stale → degrade gracefully (invariant #12).
"""

import os
import sys
import json
import time
import logging
from dataclasses import dataclass, asdict

import redis
import psycopg

from cache_contract import (
    read_fresh_snapshot, read_indicator_snapshot,
    check_staleness, store_signal, prune_stale_signals,
    ind_hist_key, setup_state_key, sig_meta_key, ind_meta_key,
    b2s, b2f, symbol_partition, get_universe, get_timeframes,
    is_cache_ready, get_candles_as_ohlcv,
    SETUP_STATE_TTL_S, SIGNAL_MAX_AGE_S, RECONCILE_INTERVAL_S,
    INDICATOR_HISTORY_SIZE,
    # Unicorn keys
    get_embryo_active_symbols, read_unicorn_state,
    unicorn_state_key, UNICORN_STATE_TTL_S,
)
from indicators import IndicatorSnapshot
from setups import S1Evaluator, S1Signal, S2Evaluator, S2Signal
from setups.s1_bb_squeeze import S1State
from setups.s2_golden_cross import S2State
from setups.u1_unicorn_ignition import U1Evaluator, U1State, U1Phase, U1Config
from setup_base import Direction

logger = logging.getLogger("signal_worker")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL")
WORKER_INDEX = int(os.getenv("WORKER_INDEX", "0"))
N_WORKERS = int(os.getenv("N_WORKERS", "1"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
CONSUMER_NAME = f"sig-{WORKER_INDEX}"
XREAD_BLOCK_MS = 5000
XREAD_COUNT = 50
PRUNE_INTERVAL_S = 300  # Prune stale signals every 5 min


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# State serialization helpers
# ---------------------------------------------------------------------------

def _serialize_state(state) -> str:
    """Serialize S1State or S2State to JSON."""
    d = {}
    for k, v in state.__dict__.items():
        if isinstance(v, Direction):
            d[k] = v.value if v is not None else None
        else:
            d[k] = v
    return json.dumps(d)


def _deserialize_s1_state(raw: str) -> S1State:
    """Deserialize S1State from JSON."""
    d = json.loads(raw)
    # Convert Direction enum
    if d.get("btc_breakout_direction") is not None:
        d["btc_breakout_direction"] = Direction(d["btc_breakout_direction"])
    else:
        d["btc_breakout_direction"] = None
    return S1State(**d)


def _deserialize_s2_state(raw: str) -> S2State:
    """Deserialize S2State from JSON."""
    d = json.loads(raw)
    return S2State(**d)


# ---------------------------------------------------------------------------
# SignalWorker
# ---------------------------------------------------------------------------

class SignalWorker:
    """Consumes indicators_ready events, evaluates setups, stores signals."""

    def __init__(self):
        # Invariant #24: decode_responses=True mandatory
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.s1_evaluator = S1Evaluator()
        self.s2_evaluator = S2Evaluator()
        self.u1_evaluator = U1Evaluator()
        self.last_reconcile = 0.0
        self.last_prune = 0.0
        # Per-worker consumer group: each worker instance gets ALL messages
        # and filters by partition locally. Fixes the shared-group bug where
        # ACK-ing another worker's message silently drops it.
        self.group_name = f"signal_group_{WORKER_INDEX}"
        # Cache of EMBRYO_ACTIVE symbols (refreshed periodically)
        self._embryo_active_cache: set[str] = set()
        self._embryo_active_cache_ts = 0.0
        # Diagnostic counters (reset per logging cycle)
        self._diag = {
            "evaluated": 0, "snapshot_fail": 0, "candle_fail": 0,
            "s1_squeeze_off": 0, "s1_pctile_fail": 0,
            "s1_age_fail": 0, "s1_ripe_fail": 0, "s1_trigger_fail": 0,
            "s1_signal": 0, "s2_not_converging": 0,
            "s2_trigger_fail": 0, "s2_signal": 0,
        }
        self._last_diag_log = 0.0

    def run(self):
        """Main loop."""
        logger.info(f"Signal worker {WORKER_INDEX}/{N_WORKERS} starting")

        while not is_cache_ready(self.r):
            logger.info("Waiting for cache:ready...")
            time.sleep(5)

        logger.info("Cache ready, starting stream consumption")

        timeframes = get_timeframes(self.r)
        if not timeframes:
            timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]

        # Create per-worker consumer group on each stream (idempotent)
        for tf in timeframes:
            stream = f"stream:indicators_ready:{tf}"
            try:
                self.r.xgroup_create(stream, self.group_name, id="0", mkstream=True)
                logger.info(f"Created consumer group {self.group_name} on {stream}")
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    raise
                # Group already exists — safe to continue

        streams = {f"stream:indicators_ready:{tf}": ">" for tf in timeframes}

        while True:
            try:
                results = self.r.xreadgroup(
                    groupname=self.group_name,
                    consumername=CONSUMER_NAME,
                    streams=streams,
                    count=XREAD_COUNT,
                    block=XREAD_BLOCK_MS,
                )

                if results:
                    for stream_name, messages in results:
                        tf = b2s(stream_name).split(":")[-1]
                        for msg_id, fields in messages:
                            self._process_event(tf, fields, msg_id, stream_name)

                # Periodic tasks
                now = time.time()
                if now - self.last_reconcile > RECONCILE_INTERVAL_S:
                    self._reconcile()
                    self.last_reconcile = now

                if now - self.last_prune > PRUNE_INTERVAL_S:
                    self._prune_all()
                    self.last_prune = now

                # Diagnostic summary every 60s
                if now - self._last_diag_log > 60 and self._diag["evaluated"] > 0:
                    logger.info("Signal diagnostics: %s", self._diag)
                    self._diag = {k: 0 for k in self._diag}
                    self._last_diag_log = now

            except redis.exceptions.ConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                time.sleep(1)

    def _process_event(self, tf: str, fields: dict, msg_id, stream_name):
        """Process a single indicators_ready event."""
        symbol = b2s(fields.get("symbol", ""))
        bar_start_ts = b2s(fields.get("bar_start_ts", ""))

        if not symbol or not bar_start_ts:
            self.r.xack(stream_name, self.group_name, msg_id)
            return

        # Only process symbols in our partition (invariant #17)
        if symbol_partition(symbol, N_WORKERS) != WORKER_INDEX:
            self.r.xack(stream_name, self.group_name, msg_id)
            return

        try:
            self._evaluate_symbol(symbol, tf, bar_start_ts)
        except Exception as e:
            logger.error(f"Failed to evaluate {symbol}:{tf}: {e}", exc_info=True)

        self.r.xack(stream_name, self.group_name, msg_id)

    def _evaluate_symbol(self, symbol: str, tf: str, bar_start_ts: str = None):
        """Run S1/S2 evaluators for a symbol/tf."""
        self._diag["evaluated"] += 1

        # Step 1: Mandatory m1/snap/m2 read (invariant #22)
        snapshot, snap_ts = read_fresh_snapshot(
            self.r, symbol, tf,
            expected_bar_start_ts=bar_start_ts,
        )
        if snapshot is None:
            self._diag["snapshot_fail"] += 1
            logger.debug(f"Stale/inconsistent snapshot for {symbol}:{tf}, skipping")
            return

        # Step 1b: Populate OHLCV candles (needed by setup evaluators for box/range calcs)
        snapshot.ohlcv = get_candles_as_ohlcv(self.r, symbol, tf)
        if not snapshot.ohlcv:
            self._diag["candle_fail"] += 1
            logger.debug(f"No candles in Redis for {symbol}:{tf}, skipping")
            return

        # Step 2: Read indicator histories from Redis
        bb_width_history = [
            b2f(x) for x in
            self.r.lrange(ind_hist_key("bb_width", symbol, tf), 0, -1)
        ]
        ma_gap_history = [
            b2f(x) for x in
            self.r.lrange(ind_hist_key("ma_gap", symbol, tf), 0, -1)
        ]

        # Step 3: Read previous setup states
        prev_s1_raw = self.r.get(setup_state_key("S1", symbol, tf))
        prev_s1_state = None
        if prev_s1_raw:
            try:
                prev_s1_state = _deserialize_s1_state(b2s(prev_s1_raw))
            except Exception:
                pass

        prev_s2_raw = self.r.get(setup_state_key("S2", symbol, tf))
        prev_s2_state = None
        if prev_s2_raw:
            try:
                prev_s2_state = _deserialize_s2_state(b2s(prev_s2_raw))
            except Exception:
                pass

        # Step 4: BTC data with graceful degradation (invariant #12)
        btc_snapshot = None
        btc_bb_hist = None

        if symbol != "BTCUSDT":
            btc_staleness = check_staleness(self.r, "BTCUSDT", tf)
            if not btc_staleness["stale"]:
                btc_snapshot = read_indicator_snapshot(self.r, "BTCUSDT", tf)
                btc_bb_hist = [
                    b2f(x) for x in
                    self.r.lrange(ind_hist_key("bb_width", "BTCUSDT", tf), 0, -1)
                ]
            else:
                logger.warning(
                    f"BTC stale ({btc_staleness['age_s']:.0f}s), "
                    f"skipping BTC filter for {symbol}:{tf}"
                )

        # Step 5: Run S1 evaluator
        s1_state = self.s1_evaluator.compute_state(
            snapshot, bb_width_history, prev_s1_state,
            btc_snapshot, btc_bb_hist,
        )

        # Store updated state with TTL (invariant #19)
        self.r.set(
            setup_state_key("S1", symbol, tf),
            _serialize_state(s1_state),
            ex=SETUP_STATE_TTL_S,
        )

        s1_signal = self.s1_evaluator.evaluate_trigger(snapshot, s1_state, symbol, tf)
        if s1_signal:
            self._diag["s1_signal"] += 1
            self._store_signal_from_evaluator(s1_signal, "S1", symbol, tf, snap_ts)
        else:
            # Track which S1 gate rejected (from state)
            if not s1_state.squeeze_on:
                self._diag["s1_squeeze_off"] += 1
            elif s1_state.bb_width_pctile > self.s1_evaluator.bb_width_pctile_trigger:
                self._diag["s1_pctile_fail"] += 1
            elif s1_state.squeeze_age < self.s1_evaluator.squeeze_age_min:
                self._diag["s1_age_fail"] += 1
            elif not s1_state.ripe_at_prev_bar:
                self._diag["s1_ripe_fail"] += 1
            else:
                self._diag["s1_trigger_fail"] += 1

        # Step 6: Run S2 evaluator
        s2_state = self.s2_evaluator.compute_state(
            snapshot, ma_gap_history, prev_s2_state, btc_snapshot,
        )

        self.r.set(
            setup_state_key("S2", symbol, tf),
            _serialize_state(s2_state),
            ex=SETUP_STATE_TTL_S,
        )

        s2_signal = self.s2_evaluator.evaluate_trigger(snapshot, s2_state, symbol, tf)
        if s2_signal:
            self._diag["s2_signal"] += 1
            self._store_signal_from_evaluator(s2_signal, "S2", symbol, tf, snap_ts)
        else:
            if not s2_state.converging:
                self._diag["s2_not_converging"] += 1
            else:
                self._diag["s2_trigger_fail"] += 1

        # Step 7: Run U1 evaluator (only for EMBRYO_ACTIVE symbols)
        self._evaluate_u1(symbol, tf, snapshot.ohlcv, snap_ts)

    def _refresh_embryo_active_cache(self):
        """Refresh cache of EMBRYO_ACTIVE symbols (every 60s)."""
        now = time.time()
        if now - self._embryo_active_cache_ts > 60:
            try:
                self._embryo_active_cache = {
                    sym for sym, _ in get_embryo_active_symbols(self.r)
                }
                self._embryo_active_cache_ts = now
            except Exception as e:
                logger.debug(f"Failed to refresh embryo active cache: {e}")

    def _evaluate_u1(self, symbol: str, tf: str, candles, bar_start_ts: str):
        """Run U1 evaluator for EMBRYO_ACTIVE symbols only."""
        # Only evaluate on 5m timeframe (U1 primary TF)
        if tf != U1Config.GATE_TIMEFRAME:
            return

        # Refresh cache
        self._refresh_embryo_active_cache()

        # Only evaluate if symbol is in EMBRYO_ACTIVE
        if symbol not in self._embryo_active_cache:
            return

        # Read U1 state from Redis
        state_dict = read_unicorn_state(self.r, symbol)
        if not state_dict:
            return

        try:
            u1_state = U1State.from_dict(state_dict)
        except Exception as e:
            logger.debug(f"Failed to deserialize U1 state for {symbol}: {e}")
            return

        # Only evaluate if in EMBRYO_ACTIVE phase
        if u1_state.phase not in (U1Phase.EMBRYO_ACTIVE, U1Phase.IGNITION_WATCH):
            return

        # Evaluate trigger
        u1_signal = self.u1_evaluator.evaluate_trigger(candles, u1_state, symbol, tf)
        if u1_signal:
            self._store_u1_signal(u1_signal, symbol, tf, bar_start_ts)

    def _store_u1_signal(self, signal, symbol: str, tf: str, bar_start_ts: str):
        """Store U1 signal in Redis."""
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
            "bar_start_ts": bar_start_ts,
            "signal_ts": signal.signal_ts.isoformat() if signal.signal_ts else None,
            "action_by_ts": signal.action_by_ts.isoformat() if signal.action_by_ts else None,
            "age_seconds": signal.age_seconds,
            "as_of_ts": time.time(),
            # Ignition metrics
            "rel_volume": signal.rel_volume,
            "compression_box_high": signal.compression_box_high,
            "compression_box_low": signal.compression_box_low,
            "confirmations_met": signal.confirmations_met,
            # Scores
            "tradability_score": signal.tradability_score,
            "participation_score": signal.participation_score,
            "compression_score": signal.compression_score,
            "embryo_active_score": signal.embryo_active_score,
        }

        if signal.explain:
            payload["why_items"] = signal.explain.why_items

        store_signal(
            self.r, symbol, tf, "U1",
            signal_payload=payload,
            setup_score=signal.setup_score,
            bar_start_ts=bar_start_ts,
        )

        # Persist to Postgres for durability (expired filter)
        reason = "; ".join(signal.explain.why_items) if signal.explain else None
        self._persist_signal_to_pg(symbol, tf, "U1", signal.setup_score, reason, payload)

        logger.info(
            f"Signal: U1 {signal.direction.value} {symbol}:{tf} "
            f"score={signal.setup_score:.3f} rel_vol={signal.rel_volume:.1f}x"
        )

    def _persist_signal_to_pg(self, symbol: str, tf: str, setup_id: str,
                               score: float, reason: str, payload: dict):
        """Persist signal to Postgres opportunities table for durability.

        If an active record already exists for this (symbol, tf, setup_id),
        refreshes it in place (score, reason, payload, updated_ts).
        Only inserts a new row when no active record exists.
        This prevents duplicate expired rows when the same signal persists
        across multiple scan cycles. Canon: one position per symbol per tf.
        """
        if not DATABASE_URL:
            return
        try:
            payload_json = json.dumps(payload)
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    # Try to update existing active record in place
                    cur.execute("""
                        UPDATE opportunities
                        SET score = %s, reason = %s, payload_json = %s, updated_ts = NOW()
                        WHERE symbol = %s AND timeframe = %s AND setup_id = %s
                          AND status = 'active'
                        RETURNING id;
                    """, (score, reason, payload_json, symbol, tf, setup_id))
                    updated = cur.fetchone()

                    if not updated:
                        # No active record exists — insert new one
                        cur.execute("""
                            INSERT INTO opportunities
                                (symbol, timeframe, setup_id, decision, score, reason, payload_json, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active');
                        """, (symbol, tf, setup_id, 'SIGNAL', score, reason, payload_json))
                conn.commit()
        except Exception as e:
            logger.debug(f"Postgres persist failed for {setup_id}:{symbol}:{tf}: {e}")

    def _expire_stale_pg_signals(self):
        """Mark Postgres opportunities as expired if older than SIGNAL_MAX_AGE_S."""
        if not DATABASE_URL:
            return
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE opportunities
                        SET status = 'expired'
                        WHERE status = 'active'
                          AND updated_ts < NOW() - INTERVAL '%s seconds';
                    """ % int(SIGNAL_MAX_AGE_S))
                conn.commit()
        except Exception as e:
            logger.debug(f"Postgres expire stale signals failed: {e}")

    def _store_signal_from_evaluator(self, signal, setup_id: str,
                                      symbol: str, tf: str, bar_start_ts: str):
        """Convert evaluator signal to payload and store in Redis + Postgres."""
        payload = {
            "setup_id": setup_id,
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
            "bar_start_ts": bar_start_ts,
            "as_of_ts": time.time(),
        }

        if signal.explain:
            payload["why_items"] = signal.explain.why_items

        store_signal(
            self.r, symbol, tf, setup_id,
            signal_payload=payload,
            setup_score=signal.setup_score,
            bar_start_ts=bar_start_ts,
        )

        # Persist to Postgres for durability (expired filter)
        reason = "; ".join(signal.explain.why_items) if signal.explain else None
        self._persist_signal_to_pg(symbol, tf, setup_id, signal.setup_score, reason, payload)

        logger.info(
            f"Signal: {setup_id} {signal.direction.value} {symbol}:{tf} "
            f"score={signal.setup_score:.3f}"
        )

    def _reconcile(self):
        """Periodic reconciliation (invariant #17 — sharded, #30 — decode safety)."""
        try:
            all_symbols = get_universe(self.r)
            timeframes = get_timeframes(self.r)

            my_symbols = [
                s for s in all_symbols
                if symbol_partition(s, N_WORKERS) == WORKER_INDEX
            ]

            recomputed = 0
            for symbol in my_symbols:
                for tf in timeframes:
                    ind_ts = b2f(self.r.hget(ind_meta_key(symbol, tf), "bar_start_ts"))
                    if ind_ts <= 0:
                        continue

                    reeval_needed = False
                    for setup_id in ["S1", "S2"]:
                        sig_ts = b2f(self.r.hget(
                            sig_meta_key(symbol, tf, setup_id), "bar_start_ts"
                        ))
                        if ind_ts > sig_ts:
                            reeval_needed = True
                            break

                    if reeval_needed:
                        try:
                            self._evaluate_symbol(symbol, tf)
                            recomputed += 1
                        except Exception as e:
                            logger.debug(f"Reconcile failed for {symbol}:{tf}: {e}")

            if recomputed > 0:
                logger.info(f"Reconciliation: re-evaluated {recomputed} symbol/tf pairs")

        except Exception as e:
            logger.error(f"Reconciliation error: {e}")

    def _prune_all(self):
        """Prune stale signals across all setups and timeframes."""
        try:
            timeframes = get_timeframes(self.r)
            for setup_id in ["S1", "S2", "U1"]:
                for tf in timeframes:
                    prune_stale_signals(self.r, setup_id, tf)
            # Also expire stale active records in Postgres
            self._expire_stale_pg_signals()
        except Exception as e:
            logger.error(f"Prune error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    setup_logging()
    worker = SignalWorker()
    worker.run()
