"""
Signal Worker - Redis Indicators to Redis Signals
==================================================

Separate Docker service (invariant #2).
Consumes indicators_ready events, runs setup evaluators, stores signals.

Design Invariants enforced:
- #2:  Separate Docker service
- #4:  Dual ZSETs for signals (score + ts)
- #10: sig_meta for reconciliation
- #12: BTC stale -> degrade gracefully
- #17: Reconciliation is sharded
- #19: TTL on setup_state
- #20: Stable partition via zlib.crc32
- #21: Transactional pipelines (MULTI/EXEC)
- #22: Mandatory m1/snap/m2 optimistic read-consistency
- #26: Lua script for stale signal pruning
"""

import os
import sys
import time
import json
import logging
import threading
from dataclasses import asdict

import redis

from cache_contract import (
    # Constants
    TIMEFRAMES,
    RECONCILE_INTERVAL_S,
    SIGNAL_MAX_AGE_S,
    # Key helpers
    ind_meta_key,
    sig_meta_key,
    stream_indicators_ready_key,
    # Operations
    read_fresh_snapshot,
    read_indicator_snapshot,
    get_indicator_history,
    get_setup_state,
    set_setup_state,
    store_signal,
    prune_stale_signals,
    check_staleness,
    get_universe,
    get_timeframes,
    is_cache_ready,
    b2s,
    b2f,
    b2i,
    symbol_partition,
)
from setups import S1Evaluator, S1State, S1Signal, S2Evaluator, S2State, S2Signal, Direction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("signal_worker")


# =============================================================================
# Configuration
# =============================================================================

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
WORKER_INDEX = int(os.environ.get("WORKER_INDEX", "0"))
N_WORKERS = int(os.environ.get("N_WORKERS", "1"))
SETUP_IDS = os.environ.get("SETUP_IDS", "S1,S2").split(",")
CONSUMER_NAME = f"signal_worker_{WORKER_INDEX}"


# =============================================================================
# Signal Worker
# =============================================================================

class SignalWorker:
    """
    Signal generation worker.

    Consumes indicators_ready events, runs setup evaluators,
    stores signals with dual ZSET indexing.
    """

    def __init__(self):
        self.r = redis.from_url(REDIS_URL, decode_responses=True)
        self.s1_evaluator = S1Evaluator()
        self.s2_evaluator = S2Evaluator()
        self._running = True
        self._reconcile_thread = None
        self._prune_thread = None

    def run(self):
        """Main entry point."""
        logger.info("=" * 60)
        logger.info(f"MCAS Signal Worker {WORKER_INDEX}/{N_WORKERS} Starting")
        logger.info(f"Setup IDs: {SETUP_IDS}")
        logger.info("=" * 60)

        # Wait for cache to be ready
        logger.info("Waiting for cache to be ready...")
        while not is_cache_ready(self.r):
            time.sleep(5)
        logger.info("Cache is ready, starting processing")

        # Start reconciliation thread
        self._reconcile_thread = threading.Thread(
            target=self._reconciliation_loop,
            daemon=True,
        )
        self._reconcile_thread.start()

        # Start pruning thread
        self._prune_thread = threading.Thread(
            target=self._pruning_loop,
            daemon=True,
        )
        self._prune_thread.start()

        # Main event loop
        self._event_loop()

    def _event_loop(self):
        """
        Main event loop: consume indicators_ready events.
        """
        timeframes = get_timeframes(self.r) or TIMEFRAMES
        streams = {stream_indicators_ready_key(tf): ">" for tf in timeframes}

        logger.info(f"Listening on streams: {list(streams.keys())}")

        while self._running:
            try:
                results = self.r.xreadgroup(
                    groupname="signal_group",
                    consumername=CONSUMER_NAME,
                    streams=streams,
                    count=100,
                    block=5000,
                )

                if not results:
                    continue

                for stream_name, messages in results:
                    tf = stream_name.split(":")[-1]

                    for message_id, data in messages:
                        try:
                            symbol = b2s(data.get("symbol", ""))
                            bar_start_ts = b2i(data.get("bar_start_ts", 0))

                            if not symbol:
                                continue

                            # Check partition (invariant #17, #20)
                            if symbol_partition(symbol, N_WORKERS) != WORKER_INDEX:
                                self.r.xack(stream_name, "signal_group", message_id)
                                continue

                            # Process the event
                            self._process_event(symbol, tf, bar_start_ts)

                            # ACK the message
                            self.r.xack(stream_name, "signal_group", message_id)

                        except Exception as e:
                            logger.error(f"Error processing message {message_id}: {e}")
                            self.r.xack(stream_name, "signal_group", message_id)

            except redis.ConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Event loop error: {e}")
                time.sleep(1)

    def _process_event(self, symbol: str, tf: str, bar_start_ts: int):
        """
        Process an indicators_ready event.

        1. Read snapshot with m1/snap/m2 consistency (invariant #22)
        2. Get indicator histories
        3. Get BTC data (with graceful degradation - invariant #12)
        4. Get/update setup state
        5. Run evaluators
        6. Store signals if triggered
        """
        try:
            # Step 1: Read snapshot with consistency check (MANDATORY - invariant #22)
            snapshot, snapshot_ts = read_fresh_snapshot(
                self.r, symbol, tf,
                expected_bar_start_ts=bar_start_ts,
            )

            if snapshot is None:
                logger.debug(f"Snapshot consistency check failed for {symbol}:{tf}")
                return

            # Step 2: Get indicator histories
            bb_width_history = get_indicator_history(self.r, "bb_width", symbol, tf)
            ma_gap_history = get_indicator_history(self.r, "ma_gap", symbol, tf)

            # Step 3: Get BTC data (invariant #12 - graceful degradation)
            btc_snapshot = None
            btc_bb_history = None

            if symbol != "BTCUSDT":
                btc_staleness = check_staleness(self.r, "BTCUSDT", tf)
                if not btc_staleness["stale"]:
                    btc_snapshot = read_indicator_snapshot(self.r, "BTCUSDT", tf)
                    btc_bb_history = get_indicator_history(self.r, "bb_width", "BTCUSDT", tf)
                else:
                    logger.debug(
                        f"BTC stale ({btc_staleness['age_s']:.0f}s), "
                        f"skipping BTC filter for {symbol}:{tf}"
                    )

            # Step 4 & 5: Run evaluators
            for setup_id in SETUP_IDS:
                try:
                    signal = self._run_evaluator(
                        setup_id=setup_id,
                        symbol=symbol,
                        tf=tf,
                        snapshot=snapshot,
                        bar_start_ts=bar_start_ts,
                        bb_width_history=bb_width_history,
                        ma_gap_history=ma_gap_history,
                        btc_snapshot=btc_snapshot,
                        btc_bb_history=btc_bb_history,
                    )

                    # Step 6: Store signal if triggered
                    if signal is not None:
                        self._store_signal(signal, symbol, tf, setup_id, bar_start_ts)
                        logger.info(
                            f"Signal: {setup_id} {symbol}:{tf} "
                            f"{signal.direction.value} score={signal.setup_score:.2f}"
                        )

                except Exception as e:
                    logger.error(f"Evaluator error {setup_id} {symbol}:{tf}: {e}")

        except Exception as e:
            logger.error(f"Process error for {symbol}:{tf}: {e}")

    def _run_evaluator(
        self,
        setup_id: str,
        symbol: str,
        tf: str,
        snapshot,
        bar_start_ts: int,
        bb_width_history: list[float],
        ma_gap_history: list[float],
        btc_snapshot,
        btc_bb_history: list[float],
    ):
        """Run a single setup evaluator."""

        # Get previous state (with TTL - invariant #19)
        prev_state_dict = get_setup_state(self.r, setup_id, symbol, tf)

        if setup_id == "S1":
            # Reconstruct previous state
            prev_state = None
            if prev_state_dict:
                try:
                    prev_state = S1State(
                        squeeze_on=prev_state_dict.get("squeeze_on", False),
                        squeeze_age=prev_state_dict.get("squeeze_age", 0),
                        bb_width_pctile=prev_state_dict.get("bb_width_pctile", 50.0),
                        ripe=prev_state_dict.get("ripe", False),
                        box_high=prev_state_dict.get("box_high", 0.0),
                        box_low=prev_state_dict.get("box_low", float("inf")),
                        bias_score=prev_state_dict.get("bias_score", 0.0),
                        long_allowed=prev_state_dict.get("long_allowed", True),
                        short_allowed=prev_state_dict.get("short_allowed", True),
                        bars_out_of_squeeze=prev_state_dict.get("bars_out_of_squeeze", 0),
                    )
                except Exception:
                    prev_state = None

            # Compute state
            state = self.s1_evaluator.compute_state(
                snapshot=snapshot,
                bb_width_history=bb_width_history,
                prev_state=prev_state,
                btc_snapshot=btc_snapshot,
                btc_bb_history=btc_bb_history,
            )

            # Save state (with TTL - invariant #19)
            set_setup_state(self.r, setup_id, symbol, tf, asdict(state))

            # Evaluate trigger
            signal = self.s1_evaluator.evaluate_trigger(
                snapshot=snapshot,
                state=state,
                symbol=symbol,
                timeframe=tf,
            )

            return signal

        elif setup_id == "S2":
            # Reconstruct previous state
            prev_state = None
            if prev_state_dict:
                try:
                    prev_state = S2State(
                        converging=prev_state_dict.get("converging", False),
                        convergence_age=prev_state_dict.get("convergence_age", 0),
                        ma_gap_pctile=prev_state_dict.get("ma_gap_pctile", 50.0),
                        ripe=prev_state_dict.get("ripe", False),
                        pending_cross=prev_state_dict.get("pending_cross", False),
                        cross_direction=Direction(prev_state_dict["cross_direction"]) if prev_state_dict.get("cross_direction") else None,
                        bias_score=prev_state_dict.get("bias_score", 0.0),
                    )
                except Exception:
                    prev_state = None

            # Compute state
            state = self.s2_evaluator.compute_state(
                snapshot=snapshot,
                ma_gap_history=ma_gap_history,
                prev_state=prev_state,
                btc_snapshot=btc_snapshot,
            )

            # Save state (with TTL)
            state_dict = asdict(state)
            # Convert Direction enum to string for JSON
            if state_dict.get("cross_direction"):
                state_dict["cross_direction"] = state_dict["cross_direction"].value
            set_setup_state(self.r, setup_id, symbol, tf, state_dict)

            # Evaluate trigger
            signal = self.s2_evaluator.evaluate_trigger(
                snapshot=snapshot,
                state=state,
                symbol=symbol,
                timeframe=tf,
            )

            return signal

        return None

    def _store_signal(self, signal, symbol: str, tf: str, setup_id: str, bar_start_ts: int):
        """
        Store a signal with dual ZSET indexing.

        Invariant #4: Dual ZSETs (score + ts)
        Invariant #21: Transactional pipeline
        """
        # Build payload
        payload = {
            "symbol": symbol,
            "timeframe": tf,
            "setup_id": setup_id,
            "direction": signal.direction.value,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "tp1_price": signal.tp1_price,
            "tp2_price": signal.tp2_price,
            "r_value": signal.r_value,
            "bar_start_ts": bar_start_ts,
            "timestamp": int(time.time() * 1000),
        }

        # Add score components if available
        if hasattr(signal, "severity"):
            payload["severity"] = signal.severity
        if hasattr(signal, "participation"):
            payload["participation"] = signal.participation
        if hasattr(signal, "break_quality"):
            payload["break_quality"] = signal.break_quality
        if hasattr(signal, "clean_close"):
            payload["clean_close"] = signal.clean_close
        if hasattr(signal, "momentum"):
            payload["momentum"] = signal.momentum

        # Store via cache_contract (invariant #4, #21)
        store_signal(
            self.r,
            symbol=symbol,
            tf=tf,
            setup_id=setup_id,
            signal_payload=payload,
            bar_start_ts=bar_start_ts,
            setup_score=signal.setup_score,
        )

    def _reconciliation_loop(self):
        """Periodic reconciliation for missed updates."""
        while self._running:
            try:
                time.sleep(RECONCILE_INTERVAL_S)

                if not is_cache_ready(self.r):
                    continue

                self._reconcile()

            except Exception as e:
                logger.error(f"Reconciliation error: {e}")

    def _reconcile(self):
        """
        Check for symbols needing signal updates.

        Compares ind_meta timestamps against sig_meta timestamps.
        """
        all_symbols = get_universe(self.r)
        my_symbols = [
            s for s in all_symbols
            if symbol_partition(s, N_WORKERS) == WORKER_INDEX
        ]

        timeframes = get_timeframes(self.r) or TIMEFRAMES
        updates_needed = 0

        for symbol in my_symbols:
            for tf in timeframes:
                try:
                    # Get indicator timestamp
                    ind_meta = self.r.hgetall(ind_meta_key(symbol, tf))
                    ind_ts = b2f(ind_meta.get("bar_start_ts", 0)) if ind_meta else 0

                    if ind_ts == 0:
                        continue

                    # Check each setup
                    for setup_id in SETUP_IDS:
                        sig_meta = self.r.hgetall(sig_meta_key(symbol, tf, setup_id))
                        sig_ts = b2f(sig_meta.get("bar_start_ts", 0)) if sig_meta else 0

                        if ind_ts > sig_ts:
                            self._process_event(symbol, tf, int(ind_ts))
                            updates_needed += 1
                            break  # Only need to recompute once per (symbol, tf)

                except Exception as e:
                    logger.error(f"Reconcile error for {symbol}:{tf}: {e}")

        if updates_needed > 0:
            logger.info(f"Reconciliation triggered {updates_needed} signal evaluations")

    def _pruning_loop(self):
        """Periodic pruning of stale signals (invariant #26)."""
        while self._running:
            try:
                time.sleep(300)  # Every 5 minutes

                timeframes = get_timeframes(self.r) or TIMEFRAMES
                total_pruned = 0

                for setup_id in SETUP_IDS:
                    for tf in timeframes:
                        pruned = prune_stale_signals(self.r, setup_id, tf)
                        total_pruned += pruned

                if total_pruned > 0:
                    logger.info(f"Pruned {total_pruned} stale signals")

            except Exception as e:
                logger.error(f"Pruning error: {e}")

    def stop(self):
        """Stop the worker."""
        self._running = False


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    worker = SignalWorker()
    try:
        worker.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        worker.stop()
