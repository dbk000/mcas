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
from datetime import datetime, timezone

import redis
import psycopg

from cache_contract import (
    read_fresh_snapshot, read_indicator_snapshot,
    check_staleness, store_signal, prune_stale_signals,
    ind_hist_key, setup_state_key, sig_meta_key, ind_meta_key,
    b2s, b2f, symbol_partition, get_universe, get_timeframes,
    is_cache_ready, get_candles_as_ohlcv,
    SETUP_STATE_TTL_S, SIGNAL_MAX_AGE_S, RECONCILE_INTERVAL_S,
    INDICATOR_HISTORY_SIZE, TF_SECONDS,
    # Unicorn keys
    get_embryo_active_symbols, read_unicorn_state,
    unicorn_state_key, UNICORN_STATE_TTL_S,
)
import numpy as np
from indicators import IndicatorSnapshot
from setups import S9Evaluator, S9Signal, S9State
from setups import S10Evaluator, S10Signal
from setups.u1_unicorn_ignition import U1Evaluator, U1State, U1Phase, U1Config, load_funding_rates
from setups.d1_dormancy_ignition import D1Evaluator, D1State, D1Phase, D1Config, is_d1_excluded, get_social_mentions_count
from setup_base import Direction
from config_reader import LiveConfig
from s9_microstructure import (
    fetch_1s_klines_batch, analyze_tape, analyze_exit_tape,
    MicrostructureContext,
)

logger = logging.getLogger("signal_worker")

from stop_policy_registry import validate_stop_registry

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
SYMBOL_QUALITY_FILTER = os.getenv("SYMBOL_QUALITY_FILTER", "0") == "1"

# Cross-setup score calibration (env-overridable)
SETUP_SCORE_MULTIPLIERS = {
    "U1": float(os.getenv("U1_SCORE_MULT", "1.0")),
    "S9": float(os.getenv("S9_SCORE_MULT", "1.0")),
    "S10": float(os.getenv("S10_SCORE_MULT", "1.0")),
    "D1": float(os.getenv("D1_SCORE_MULT", "1.0")),
}


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
    """Serialize setup state to JSON."""
    d = {}
    for k, v in state.__dict__.items():
        if hasattr(v, 'value'):
            d[k] = v.value if v is not None else None
        else:
            d[k] = v
    return json.dumps(d)


# ---------------------------------------------------------------------------
# SignalWorker
# ---------------------------------------------------------------------------

class SignalWorker:
    """Consumes indicators_ready events, evaluates setups, stores signals."""

    def __init__(self):
        # Invariant #24: decode_responses=True mandatory
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.u1_evaluator = U1Evaluator()
        self.s9_evaluator = S9Evaluator()
        self.last_reconcile = 0.0
        self.last_prune = 0.0
        self._s9_last_evaluated_hour = 0  # Unix hour for S9 dedup
        self._s9_last_position_check = 0.0  # Position monitor timestamp
        # Per-worker consumer group: each worker instance gets ALL messages
        # and filters by partition locally. Fixes the shared-group bug where
        # ACK-ing another worker's message silently drops it.
        self.group_name = f"signal_group_{WORKER_INDEX}"
        # Cache of EMBRYO_ACTIVE symbols (refreshed periodically)
        self._embryo_active_cache: set[str] = set()
        self._embryo_active_cache_ts = 0.0
        # Symbol quality filter (excluded symbols cache, 5-min TTL)
        self._symbol_quality_filter_enabled = SYMBOL_QUALITY_FILTER
        self._excluded_symbols_cache: set[str] = set()
        self._excluded_symbols_cache_ts: float = 0.0
        self.s10_evaluator = S10Evaluator()
        self.d1_evaluator = D1Evaluator()
        self._d1_prev_states: dict[str, D1State] = {}  # keyed by "symbol:tf"
        # LiveConfig: poll meta.config_version for hot-reload
        self._live_config = LiveConfig(
            database_url=DATABASE_URL,
            poll_interval_s=float(os.getenv("CONFIG_POLL_INTERVAL_S", "10")),
        ) if DATABASE_URL else None
        # Diagnostic counters (reset per logging cycle)
        self._diag = {
            "evaluated": 0, "snapshot_fail": 0, "candle_fail": 0,
            "symbol_quality_excluded": 0,
            "s10_signal": 0, "d1_signal": 0,
            "s9_signal": 0, "s9_breadth_fail": 0,
        }
        self._last_diag_log = 0.0

    def run(self):
        """Main loop."""
        logger.info(f"Signal worker {WORKER_INDEX}/{N_WORKERS} starting")

        while not is_cache_ready(self.r):
            logger.info("Waiting for cache:ready...")
            time.sleep(5)

        logger.info("Cache ready, starting stream consumption")

        # Startup config sync: Pipeline is source of truth.
        # If Pipeline has deployed config -> load into evaluator (Pipeline wins).
        # If no deployed config -> bootstrap code defaults into Pipeline.
        self._sync_node_parameters()

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

                # Hot-reload config from Pipeline (time-throttled internally)
                self._maybe_hot_reload_config()

                if now - self.last_reconcile > RECONCILE_INTERVAL_S:
                    self._reconcile()
                    self.last_reconcile = now

                if now - self.last_prune > PRUNE_INTERVAL_S:
                    self._prune_all()
                    self.last_prune = now

                # S9 cross-sectional: run once per 1h bar (after indicators settle)
                self._maybe_evaluate_s9()

                # S9 position monitor: check open positions every 5 min
                self._maybe_monitor_s9_positions()

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
        """Run active evaluators for a symbol/tf. S1-S8 archived; U1 + S10 active."""
        self._diag["evaluated"] += 1

        # Symbol quality filter — skip chronic-firer symbols
        if self._is_symbol_excluded(symbol):
            self._diag["symbol_quality_excluded"] += 1
            return

        # Step 1: Mandatory m1/snap/m2 read (invariant #22)
        snapshot, snap_ts = read_fresh_snapshot(
            self.r, symbol, tf,
            expected_bar_start_ts=bar_start_ts,
        )
        if snapshot is None:
            self._diag["snapshot_fail"] += 1
            logger.debug(f"Stale/inconsistent snapshot for {symbol}:{tf}, skipping")
            return

        # Step 1b: Populate OHLCV candles (needed by evaluators)
        snapshot.ohlcv = get_candles_as_ohlcv(self.r, symbol, tf)
        if not snapshot.ohlcv:
            self._diag["candle_fail"] += 1
            logger.debug(f"No candles in Redis for {symbol}:{tf}, skipping")
            return

        # S10: MACD Scalp (5m only, per-symbol)
        if tf == "5m":
            self._evaluate_s10(symbol, tf, snapshot, bar_start_ts)

        # D1: Dormancy-to-Ignition (1h only, microcap universe)
        if tf == "1h":
            self._evaluate_d1(symbol, tf, snapshot, bar_start_ts)

        # U1 evaluator (only for EMBRYO_ACTIVE symbols)
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

    def _refresh_excluded_symbols_cache(self):
        """Refresh cache of excluded symbols from Redis (every 300s)."""
        now = time.time()
        if now - self._excluded_symbols_cache_ts > 300:
            try:
                members = self.r.smembers("symbol:excluded:quality")
                self._excluded_symbols_cache = set(members) if members else set()
                self._excluded_symbols_cache_ts = now
            except Exception as e:
                logger.debug(f"Failed to refresh excluded symbols cache: {e}")

    def _is_symbol_excluded(self, symbol: str) -> bool:
        """Check if symbol is excluded by quality filter."""
        if not self._symbol_quality_filter_enabled:
            return False
        self._refresh_excluded_symbols_cache()
        return symbol in self._excluded_symbols_cache

    def _evaluate_s10(self, symbol: str, tf: str, snapshot, bar_start_ts: str):
        """Run S10 MACD Scalp evaluator on 5m bars."""
        try:
            state = self.s10_evaluator.compute_state(snapshot)
            sig = self.s10_evaluator.evaluate_trigger(snapshot, state, symbol, tf)
            if sig is None:
                return

            self._diag["s10_signal"] += 1

            # Build payload
            calib_mult = SETUP_SCORE_MULTIPLIERS.get("S10", 1.0)
            calibrated_score = min(sig.setup_score * calib_mult, 1.0)

            payload = {
                "setup_id": "S10",
                "setup_name": "MACD Scalp v1.0",
                "symbol": symbol,
                "timeframe": tf,
                "direction": sig.direction.value,
                "setup_score": calibrated_score,
                "raw_score": sig.setup_score,
                "entry_price": sig.entry_price,
                "stop_price": sig.stop_price,
                "tp1_price": sig.tp1_price,
                "tp2_price": sig.tp2_price,
                "r_value": sig.r_value,
                "time_stop_bars": sig.time_stop_bars,
                "bar_start_ts": bar_start_ts or "",
                "as_of_ts": time.time(),
                "why_items": sig.explain.why_items if sig.explain else [],
                "macd_histogram": sig.macd_histogram,
                "rvol": sig.rvol,
                "sma_20": sig.sma_20,
                "flat_top_level": sig.flat_top_level,
                "trend_bias": sig.trend_bias,
                "score_components": sig.score_components,
            }

            store_signal(self.r, symbol, tf, "S10", payload,
                         calibrated_score, bar_start_ts or "")
            reason = "; ".join(sig.explain.why_items) if sig.explain else None
            self._persist_signal_to_pg(symbol, tf, "S10", calibrated_score, reason, payload)

            logger.info(
                "Signal: S10 LONG %s:%s score=%.3f rvol=%.1fx macd_hist=%.6f",
                symbol, tf, calibrated_score, sig.rvol, sig.macd_histogram,
            )
        except Exception as e:
            logger.debug("S10 eval error %s:%s: %s", symbol, tf, e)

    def _evaluate_d1(self, symbol: str, tf: str, snapshot, bar_start_ts: str):
        """Run D1 Dormancy-to-Ignition evaluator on 1h bars (microcap universe)."""
        # Universe filter: skip majors, stablecoins, wrapped, leverage
        if is_d1_excluded(symbol):
            return

        try:
            state_key = f"{symbol}:{tf}"
            prev_state = self._d1_prev_states.get(state_key)

            state = self.d1_evaluator.compute_state(
                snapshot, prev_state=prev_state, btc_returns=None,
            )
            self._d1_prev_states[state_key] = state

            sig = self.d1_evaluator.evaluate_trigger(snapshot, state, symbol, tf)
            if sig is None:
                return

            self._diag["d1_signal"] += 1

            # Social mentions (soft feature — not a gate)
            if DATABASE_URL:
                trigger_ts = None
                if bar_start_ts:
                    try:
                        trigger_ts = datetime.fromtimestamp(
                            int(bar_start_ts) / 1000, tz=timezone.utc
                        )
                    except (ValueError, TypeError):
                        pass
                sig.social_mentions_30d = get_social_mentions_count(
                    symbol, DATABASE_URL, trigger_bar_start_ts=trigger_ts,
                )

            # Build payload
            calib_mult = SETUP_SCORE_MULTIPLIERS.get("D1", 1.0)
            calibrated_score = min(sig.setup_score * calib_mult, 1.0)

            payload = {
                "setup_id": "D1",
                "setup_name": "Dormancy-to-Ignition v1.2",
                "symbol": symbol,
                "timeframe": tf,
                "direction": sig.direction.value,
                "setup_score": calibrated_score,
                "raw_score": sig.setup_score,
                "entry_price": sig.entry_price,
                "stop_price": sig.stop_price,
                "tp1_price": sig.tp1_price,
                "tp2_price": sig.tp2_price,
                "r_value": sig.r_value,
                "time_stop_bars": sig.time_stop_bars,
                "bar_start_ts": bar_start_ts or "",
                "as_of_ts": time.time(),
                "why_items": sig.explain.why_items if sig.explain else [],
                "dormancy_score": sig.dormancy_score,
                "accum_divergence": sig.accum_divergence,
                "compression_box_high": sig.compression_box_high,
                "compression_box_low": sig.compression_box_low,
                "volume_z_at_trigger": sig.volume_z_at_trigger,
                "rvol_at_trigger": sig.rvol_at_trigger,
                "bb_width_at_trigger": sig.bb_width_at_trigger,
                "btc_beta": sig.btc_beta,
                "social_mentions_30d": sig.social_mentions_30d,
                "score_components": sig.score_components,
                "gates": {
                    "Gate 1 - Dormancy": f"dormancy_score >= {D1Config.DORMANCY_THRESHOLD}",
                    "Gate 2 - Compression": f"bb_width < {D1Config.BB_WIDTH_COMPRESS}",
                    "Gate 3 - Volume spike": (
                        f"volume_z >= {D1Config.VOLUME_Z_SPIKE} AND "
                        f"rvol >= {D1Config.RVOL_MIN}"
                    ),
                    "Gate 4 - Breakout": "close > prev_compression_box_high (previous donchian_high_20)",
                    "Gate 5 - BB expanding": "bb_width > bb_width_prev",
                },
                "exit_config": {
                    "trail": "none",
                    "stop_pct": D1Config.STOP_PCT,
                    "tp1_pct": D1Config.TP1_PCT,
                    "tp2_pct": D1Config.TP2_PCT,
                    "time_stop_bars_1h": D1Config.TIME_STOP_BARS_1H,
                },
                "scoring_weights": {
                    "dormancy": D1Config.W_DORMANCY,
                    "vol_z": D1Config.W_VOL_Z,
                    "accum_div": D1Config.W_ACCUM_DIV,
                    "bb_expansion": D1Config.W_BB_EXPANSION,
                    "rvol": D1Config.W_RVOL,
                    "btc_adj_ret": D1Config.W_BTC_ADJ_RET,
                },
            }

            store_signal(self.r, symbol, tf, "D1", payload,
                         calibrated_score, bar_start_ts or "")
            reason = "; ".join(sig.explain.why_items) if sig.explain else None
            self._persist_signal_to_pg(symbol, tf, "D1", calibrated_score, reason, payload)

            logger.info(
                "Signal: D1 LONG %s:%s score=%.3f dormancy=%.0f%% vol_z=%.1f rvol=%.1fx",
                symbol, tf, calibrated_score, sig.dormancy_score * 100,
                sig.volume_z_at_trigger, sig.rvol_at_trigger,
            )
        except Exception as e:
            logger.debug("D1 eval error %s:%s: %s", symbol, tf, e)

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

        # Load funding rates (cached 1h inside load_funding_rates)
        funding_rates = load_funding_rates(DATABASE_URL) if DATABASE_URL else {}

        # Evaluate trigger
        u1_signal = self.u1_evaluator.evaluate_trigger(candles, u1_state, symbol, tf, funding_rates=funding_rates, database_url=DATABASE_URL)
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
            "trigger_path": getattr(signal, "trigger_path", "traditional"),
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
            f"score={signal.setup_score:.3f} rel_vol={signal.rel_volume:.1f}x "
            f"path={getattr(signal, 'trigger_path', 'traditional')}"
        )

    # Setup version mapping for live signals
    SETUP_VERSIONS = {
        "U1": "U1_v3.0",
        "S9": "S9_v1.0",
        "S10": "S10_v1.0",
        "D1": "D1_v1.2",
    }

    def _sync_node_parameters(self):
        """Sync parameters between code and Pipeline at startup.

        Pipeline is the source of truth. Precedence:
          1. Pipeline deployed version (highest — if it exists, it wins)
          2. Environment variables (override code defaults, used for bootstrap)
          3. Code defaults (lowest — fallback when nothing else is set)

        If a deployed version exists in meta.node_version -> code reads FROM Pipeline.
        If no deployed version exists -> code writes TO Pipeline (bootstrap).

        Only worker_index=0 writes to avoid concurrent upserts from multiple workers.
        """
        if WORKER_INDEX != 0:
            return
        if not DATABASE_URL:
            return

        NODE_NAMES = {
            "D1": "D1 Dormancy-to-Ignition",
            "U1": "U1 Unicorn Ignition",
            "S9": "S9 Short Reversion",
            "S10": "S10 BB Squeeze",
        }

        evaluators = [
            ("D1", self.d1_evaluator),
            ("U1", self.u1_evaluator),
        ]

        for setup_id, evaluator in evaluators:
            try:
                node_name = NODE_NAMES.get(setup_id)
                if not node_name:
                    continue

                # Check if Pipeline has ANY version (deployed, tested, etc.)
                any_version_exists = self._node_has_any_version(node_name)

                if any_version_exists:
                    # Pipeline has a version — try to load deployed params into evaluator
                    deployed_params = None
                    if self._live_config:
                        deployed_params = self._live_config.get_strategy_params(
                            setup_id, force_reload=True
                        )
                    if deployed_params and hasattr(evaluator, 'update_config'):
                        changed = evaluator.update_config(deployed_params)
                        if changed:
                            logger.info(
                                "%s: loaded config from Pipeline (%d params changed)",
                                setup_id, len(changed),
                            )
                        else:
                            logger.info("%s: Pipeline config matches code defaults", setup_id)
                    elif deployed_params:
                        logger.info("%s: loaded Pipeline config (no update_config method yet)", setup_id)
                    else:
                        logger.info(
                            "%s: Pipeline has version(s) but none deployed — keeping code defaults",
                            setup_id,
                        )
                else:
                    # No Pipeline version at all — bootstrap code defaults
                    self._bootstrap_node_params(setup_id, evaluator, node_name)

            except Exception as e:
                logger.warning(f"Failed to sync {setup_id} parameters: {e}")

    def _node_has_any_version(self, node_name: str) -> bool:
        """Check if any version exists for this node (any status)."""
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT EXISTS(
                            SELECT 1
                            FROM meta.node n
                            JOIN meta.node_version nv ON nv.node_id = n.node_id
                            WHERE lower(n.name) = lower(%s)
                              AND nv.parameters IS NOT NULL
                              AND nv.parameters != '{}'::jsonb
                        )
                    """, (node_name,))
                    return cur.fetchone()[0]
        except Exception as e:
            logger.warning("Failed to check node versions for %s: %s", node_name, e)
            return False

    def _bootstrap_node_params(self, setup_id: str, evaluator, node_name: str):
        """Write code defaults to meta.node_version (bootstrap — no version exists at all)."""
        if not hasattr(evaluator, 'parameters'):
            return

        params = {}
        for p in evaluator.parameters:
            params[p.name] = p.default

        # Add structured params for D1
        if setup_id == "D1":
            params["trigger_require_dormancy"] = D1Config.TRIGGER_REQUIRE_DORMANCY
            params["trigger_require_compression"] = D1Config.TRIGGER_REQUIRE_COMPRESSION
            params["require_accum_divergence_gate"] = D1Config.REQUIRE_ACCUM_DIVERGENCE_GATE
            params["gates"] = {
                "Gate 1 - Dormancy": f"dormancy_score >= {D1Config.DORMANCY_THRESHOLD} "
                                     f"(fraction of 48 bars where (high-low)/close < {D1Config.DORMANCY_RANGE_THRESHOLD})",
                "Gate 2 - Compression": f"bb_width < {D1Config.BB_WIDTH_COMPRESS}",
                "Gate 3 - Volume spike": f"volume_z >= {D1Config.VOLUME_Z_SPIKE} AND rvol >= {D1Config.RVOL_MIN}",
                "Gate 4 - Breakout": "close > prev_compression_box_high (previous donchian_high_20)",
                "Gate 5 - BB expanding": "bb_width expanding (regime transition)",
                "Optional Gate - Accum divergence": (
                    f"enabled={D1Config.REQUIRE_ACCUM_DIVERGENCE_GATE}, "
                    f"accum_divergence >= {D1Config.ACCUM_DIVERGENCE_MIN}"
                ),
            }
            params["exit_config"] = {
                "stop_pct": D1Config.STOP_PCT,
                "tp1_pct": D1Config.TP1_PCT,
                "tp2_pct": D1Config.TP2_PCT,
                "time_stop_bars_1h": D1Config.TIME_STOP_BARS_1H,
                "trail": "none",
            }
            params["scoring_weights"] = {
                "dormancy": D1Config.W_DORMANCY,
                "vol_z": D1Config.W_VOL_Z,
                "accum_div": D1Config.W_ACCUM_DIV,
                "bb_expansion": D1Config.W_BB_EXPANSION,
                "rvol": D1Config.W_RVOL,
                "btc_adj_ret": D1Config.W_BTC_ADJ_RET,
            }

        if setup_id == "U1":
            params["exit_config"] = {
                "stop_pct": U1Config.STOP_PCT,
                "tp1_pct": U1Config.TP1_PCT,
                "tp2_pct": U1Config.TP2_PCT,
                "time_stop_bars": U1Config.TIME_STOP_BARS,
            }

        params_json = json.dumps(params)

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE meta.node_version nv
                    SET parameters = %s::jsonb
                    FROM meta.node n
                    WHERE nv.node_id = n.node_id
                      AND n.name = %s
                      AND nv.version_number = (
                          SELECT MAX(nv2.version_number)
                          FROM meta.node_version nv2
                          WHERE nv2.node_id = n.node_id
                      )
                """, (params_json, node_name))
                if cur.rowcount > 0:
                    logger.info(f"Bootstrapped {setup_id} code defaults to meta.node_version ({len(params)} keys)")
                else:
                    logger.debug(f"No meta.node_version found for {setup_id} — skipping bootstrap")
            conn.commit()

    def _maybe_hot_reload_config(self):
        """Check for Pipeline config changes and hot-reload evaluator configs.

        Time-throttled by LiveConfig.has_changed() (CONFIG_POLL_INTERVAL_S).
        On change: apply new params, validate, invalidate stale Redis signals.
        """
        if not self._live_config:
            return
        if not self._live_config.has_changed():
            return

        # D1 hot-reload
        try:
            d1_params = self._live_config.get_strategy_params("D1")
            if d1_params:
                changed = self.d1_evaluator.update_config(d1_params)
                if changed:
                    # Invalidate Redis signals — they have stale prices baked in
                    d1_tfs = self.d1_evaluator.metadata.timeframe_affinity
                    self._invalidate_redis_signals("D1", d1_tfs)
                    logger.info(
                        "CONFIG_RELOAD setup=D1 changed=%s version=%d",
                        list(changed.keys()),
                        self._live_config._cached_version,
                    )
        except Exception as e:
            logger.error("D1 hot-reload failed (keeping previous config): %s", e)

        # U1 hot-reload (placeholder — add update_config() to U1Evaluator when ready)
        # S9, S10 follow same pattern
        # Note: stop policy is not consumed by signal_worker (it's a sim-time concern
        # in run_executor, which already reads it per-simulation). No reload needed here.

    def _invalidate_redis_signals(self, setup_id: str, timeframes: list[str]):
        """Delete Redis signals for a setup after config change.

        Signals carry baked-in entry/stop/TP prices from the old config.
        After config reload, those prices are wrong. Delete them so the
        next evaluation cycle writes fresh signals with the new config.
        """
        from cache_contract import score_zset_key, ts_zset_key
        for tf in timeframes:
            sc_key = score_zset_key(setup_id, tf)
            ts_key = ts_zset_key(setup_id, tf)
            # Get all symbols in the ZSET to delete their individual signal keys
            members = self.r.zrange(sc_key, 0, -1)
            if members:
                pipe = self.r.pipeline(transaction=True)
                for sym in members:
                    symbol = sym if isinstance(sym, str) else sym.decode()
                    pipe.delete(f"sig:{symbol}:{tf}:{setup_id}")
                    pipe.delete(f"sig_meta:{symbol}:{tf}:{setup_id}")
                pipe.delete(sc_key)
                pipe.delete(ts_key)
                pipe.execute()
                logger.info(
                    "Invalidated %d Redis signals for %s:%s (config changed)",
                    len(members), setup_id, tf,
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
            logger.debug(f"Postgres persist failed for {setup_id}:{symbol}:{tf}: {e}")

    def _expire_stale_pg_signals(self):
        """Mark Postgres opportunities as expired if older than SIGNAL_MAX_AGE_S.

        Equities strategies (EQ_*) have their own expiry logic in
        equities_strategy_worker — do not expire them here.
        """
        if not DATABASE_URL:
            return
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE opportunities
                        SET status = 'expired'
                        WHERE status = 'active'
                          AND setup_id NOT LIKE 'EQ_%%'
                          AND updated_ts < NOW() - INTERVAL '%s seconds';
                    """ % int(SIGNAL_MAX_AGE_S))
                conn.commit()
        except Exception as e:
            logger.debug(f"Postgres expire stale signals failed: {e}")

    def _store_signal_from_evaluator(self, signal, setup_id: str,
                                      symbol: str, tf: str, bar_start_ts: str,
                                      snapshot=None):
        """Convert evaluator signal to payload and store in Redis + Postgres.

        Applies cross-setup score calibration (SETUP_SCORE_MULTIPLIERS) and
        regime-aware adjustment before storage, capped at 1.0.
        """
        # Score calibration: setup multiplier, capped at 1.0
        raw_score = signal.setup_score
        calib_mult = SETUP_SCORE_MULTIPLIERS.get(setup_id, 1.0)
        calibrated_score = min(raw_score * calib_mult, 1.0)

        payload = {
            "setup_id": setup_id,
            "symbol": symbol,
            "timeframe": tf,
            "direction": signal.direction.value,
            "setup_score": calibrated_score,
            "raw_score": raw_score,
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
            setup_score=calibrated_score,
            bar_start_ts=bar_start_ts,
        )

        # Persist to Postgres for durability (expired filter)
        reason = "; ".join(signal.explain.why_items) if signal.explain else None
        self._persist_signal_to_pg(symbol, tf, setup_id, calibrated_score, reason, payload)

        logger.info(
            f"Signal: {setup_id} {signal.direction.value} {symbol}:{tf} "
            f"score={calibrated_score:.3f} (raw={raw_score:.3f} "
            f"calib={calib_mult:.2f} regime={regime_mult:.2f})"
        )

    # ------------------------------------------------------------------
    # S9 - Cross-Sectional Short Reversion
    # ------------------------------------------------------------------

    def _maybe_evaluate_s9(self):
        """Run S9 cross-sectional evaluation once per 1h bar, +300s after the hour.

        Waits 300 seconds so the 1s tape has full data for microstructure analysis.
        Only worker 0 runs S9 to avoid duplicate signals.
        """
        if WORKER_INDEX != 0:
            return

        now = time.time()
        current_hour = int(now // 3600)

        # Already evaluated this hour?
        if current_hour == self._s9_last_evaluated_hour:
            return

        # Wait 300s after the hour: 1s tape needs 300 seconds of data
        seconds_into_hour = now - (current_hour * 3600)
        if seconds_into_hour < 300:
            return

        self._s9_last_evaluated_hour = current_hour
        try:
            self._evaluate_s9_cross_sectional()
        except Exception as e:
            logger.error(f"S9 cross-sectional evaluation failed: {e}", exc_info=True)

    def _evaluate_s9_cross_sectional(self):
        """Cross-sectional S9: short top-decile by ret_4h when breadth > 0.3.

        At +300s after the hour, also fetches 300 x 1-second klines for each
        top-decile symbol and derives live microstructure context for execution.
        """
        all_symbols = get_universe(self.r)
        if not all_symbols:
            return

        # Read recent candle closes for each symbol
        symbol_data = {}
        for symbol in all_symbols:
            if self._is_symbol_excluded(symbol):
                continue
            candles = get_candles_as_ohlcv(self.r, symbol, "1h")
            if not candles or len(candles) < 25:
                continue
            close_now = candles[-1].close
            close_4h = candles[-5].close if len(candles) >= 5 else None
            close_24h = candles[-25].close if len(candles) >= 25 else None
            if (close_now and close_now > 0 and close_4h and close_4h > 0
                    and close_24h and close_24h > 0):
                ret_4h = close_now / close_4h - 1
                ret_24h = close_now / close_24h - 1
                symbol_data[symbol] = (ret_4h, ret_24h, close_now)

        if len(symbol_data) < 20:
            logger.debug("S9: only %d symbols, need >= 20", len(symbol_data))
            return

        # Market breadth
        n_positive = sum(1 for _, (_, r24, _) in symbol_data.items() if r24 > 0)
        breadth = n_positive / len(symbol_data)

        if breadth <= S9Evaluator.BREADTH_THRESHOLD:
            self._diag["s9_breadth_fail"] += 1
            logger.info("S9: breadth=%.3f <= %.1f, no signals",
                        breadth, S9Evaluator.BREADTH_THRESHOLD)
            return

        # Rank by ret_4h descending, top decile
        sorted_syms = sorted(symbol_data.items(), key=lambda x: x[1][0], reverse=True)
        decile_n = max(1, len(sorted_syms) // 10)
        top_decile = sorted_syms[:decile_n]

        state = S9State(
            breadth=breadth,
            breadth_passes=True,
            universe_size=len(symbol_data),
            top_decile_threshold=top_decile[-1][1][0] if top_decile else 0.0,
            top_decile_count=decile_n,
        )

        # Fetch 300 x 1s klines for all top-decile symbols (parallel)
        hour_start_ms = int(time.time() // 3600) * 3600 * 1000
        top_symbols = [sym for sym, _ in top_decile]
        micro_data = {}
        try:
            kline_batch = fetch_1s_klines_batch(
                top_symbols, hour_start_ms + 1000, limit=300, max_workers=8,
            )
            for sym, klines in kline_batch.items():
                close_price = symbol_data[sym][2]  # ret_4h, ret_24h, close
                micro_data[sym] = analyze_tape(close_price, klines)
            logger.info("S9: fetched 1s klines for %d/%d top-decile symbols",
                        len(kline_batch), len(top_symbols))
        except Exception as e:
            logger.warning("S9: 1s kline fetch failed, proceeding without: %s", e)

        bar_start_ts = str(hour_start_ms)
        count = 0

        for rank, (symbol, (ret_4h, ret_24h, close)) in enumerate(top_decile):
            signal = S9Evaluator.build_signal(
                symbol=symbol, close=close,
                ret_4h=ret_4h, ret_24h=ret_24h,
                breadth=breadth, rank=rank,
                universe_size=len(symbol_data),
                decile_size=decile_n, state=state,
                bar_start_ts=bar_start_ts,
            )

            # Microstructure context (live tape analysis)
            micro_ctx = micro_data.get(symbol)
            micro_payload = {}
            if micro_ctx:
                micro_payload = {
                    "entry_type": micro_ctx.entry_type,
                    "suggested_entry_price": micro_ctx.suggested_entry_price,
                    "micro_reason": micro_ctx.reason,
                    "drift_bps": round(micro_ctx.drift_bps, 1),
                    "max_drift_bps": round(micro_ctx.max_drift_bps, 1),
                    "max_price_300s": micro_ctx.max_price,
                    "current_price_300s": micro_ctx.current_price,
                    "still_climbing": micro_ctx.still_climbing,
                    "already_reverted": micro_ctx.already_reverted,
                    "volume_ratio_early_late": round(micro_ctx.volume_ratio_early_late, 1),
                    "n_klines": micro_ctx.n_klines,
                }

            # Use tape-derived entry if available and not SKIP
            effective_entry = signal.entry_price
            if micro_ctx and micro_ctx.entry_type != "SKIP":
                effective_entry = micro_ctx.suggested_entry_price

            # Build payload
            payload = {
                "setup_id": "S9",
                "setup_name": "Short Reversion v1.0",
                "symbol": symbol,
                "timeframe": "1h",
                "direction": signal.direction.value,
                "setup_score": signal.setup_score,
                "raw_score": signal.setup_score,
                "entry_price": effective_entry,
                "naive_entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "tp1_price": signal.tp1_price,
                "tp2_price": signal.tp2_price,
                "r_value": signal.r_value,
                "time_stop_bars": signal.time_stop_bars,
                "bar_start_ts": bar_start_ts,
                "as_of_ts": time.time(),
                "why_items": signal.explain.why_items if signal.explain else [],
                "state": {
                    "ret_4h": ret_4h,
                    "ret_24h": ret_24h,
                    "breadth": breadth,
                    "rank": rank + 1,
                    "decile_size": decile_n,
                    "universe_size": len(symbol_data),
                },
                "score_components": {
                    "rank_score": signal.rank_score,
                    "breadth_score": signal.breadth_score,
                    "magnitude_score": signal.magnitude_score,
                },
                "microstructure": micro_payload,
            }

            # If tape says SKIP, still store the signal but at lower score
            final_score = signal.setup_score
            if micro_ctx and micro_ctx.entry_type == "SKIP":
                final_score = signal.setup_score * 0.5
                payload["setup_score"] = final_score
                payload["skip_reason"] = micro_ctx.reason

            store_signal(self.r, symbol, "1h", "S9", payload,
                         final_score, bar_start_ts)
            reason = "; ".join(signal.explain.why_items) if signal.explain else None
            self._persist_signal_to_pg(symbol, "1h", "S9",
                                       final_score, reason, payload)
            count += 1

        self._diag["s9_signal"] += count
        skipped = sum(1 for s in top_symbols
                      if s in micro_data and micro_data[s].entry_type == "SKIP")
        logger.info(
            "S9: breadth=%.3f, %d/%d symbols → %d signals "
            "(%d with 1s context, %d SKIP)",
            breadth, decile_n, len(symbol_data), count,
            len(micro_data), skipped,
        )

    # ------------------------------------------------------------------
    # S9 Position Monitor — early exit detection via 1s tape
    # ------------------------------------------------------------------

    S9_MONITOR_INTERVAL_S = 300  # Check every 5 minutes

    def _maybe_monitor_s9_positions(self):
        """Check open S9 positions for early exit conditions, every 5 min.

        Only worker 0 runs this to avoid duplicate checks.
        """
        if WORKER_INDEX != 0:
            return

        now = time.time()
        if now - self._s9_last_position_check < self.S9_MONITOR_INTERVAL_S:
            return

        self._s9_last_position_check = now

        if not DATABASE_URL:
            return

        try:
            self._check_s9_positions()
        except Exception as e:
            logger.error("S9 position monitor failed: %s", e, exc_info=True)

    def _check_s9_positions(self):
        """Query open S9 opportunities and check for early exit via 1s tape."""
        import requests as _req

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # Find S9 opportunities that are still active and within 48h window
                cur.execute("""
                    SELECT id, symbol, payload_json, created_ts
                    FROM opportunities
                    WHERE setup_id = 'S9'
                      AND status = 'active'
                      AND created_ts > NOW() - INTERVAL '48 hours'
                    ORDER BY created_ts DESC
                """)
                positions = cur.fetchall()

        if not positions:
            return

        logger.info("S9 monitor: checking %d open positions", len(positions))

        # Get current prices from Binance ticker
        symbols_to_check = set()
        pos_data = []
        for row in positions:
            pos_id, symbol, payload_json, created_ts = row
            try:
                payload = json.loads(payload_json) if payload_json else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            entry_price = payload.get("entry_price", 0)
            if entry_price <= 0:
                continue
            symbols_to_check.add(symbol)
            pos_data.append({
                "id": pos_id,
                "symbol": symbol,
                "entry_price": entry_price,
                "created_ts": created_ts,
                "payload": payload,
            })

        if not pos_data:
            return

        # Fetch current prices
        try:
            resp = _req.get(
                "https://api.binance.com/api/v3/ticker/price",
                timeout=10,
            )
            resp.raise_for_status()
            price_map = {
                t["symbol"]: float(t["price"])
                for t in resp.json()
                if t["symbol"] in symbols_to_check
            }
        except Exception as e:
            logger.debug("S9 monitor: price fetch failed: %s", e)
            return

        # For positions with >= 1% unrealized profit, fetch 1s tape
        positions_to_tape = []
        for pos in pos_data:
            current = price_map.get(pos["symbol"])
            if current is None:
                continue
            # Short: profit when price drops
            unrealized_pct = (pos["entry_price"] - current) / pos["entry_price"] * 100
            pos["current_price"] = current
            pos["unrealized_pct"] = unrealized_pct
            if unrealized_pct >= 1.0:
                positions_to_tape.append(pos)

        if not positions_to_tape:
            logger.debug("S9 monitor: no positions with >= 1%% unrealized profit")
            return

        # Fetch recent 300 x 1s klines for profitable positions
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - 300_000  # Last 300 seconds
        tape_symbols = [p["symbol"] for p in positions_to_tape]
        kline_batch = fetch_1s_klines_batch(
            tape_symbols, start_ms, limit=300, max_workers=6,
        )

        exit_flagged = 0
        for pos in positions_to_tape:
            klines = kline_batch.get(pos["symbol"], [])
            if not klines:
                continue

            rec = analyze_exit_tape(
                pos["entry_price"], klines, pos["unrealized_pct"],
            )

            if rec.action == "EXIT":
                exit_flagged += 1
                logger.info(
                    "S9 EARLY EXIT flagged: %s unrealized=%.1f%% — %s",
                    pos["symbol"], pos["unrealized_pct"], rec.reason,
                )
                # Update opportunity payload with exit flag
                try:
                    payload = pos["payload"]
                    payload["early_exit_flagged"] = True
                    payload["early_exit_reason"] = rec.reason
                    payload["early_exit_urgency"] = rec.urgency
                    payload["early_exit_unrealized_pct"] = round(pos["unrealized_pct"], 2)
                    payload["early_exit_ts"] = time.time()

                    with psycopg.connect(DATABASE_URL) as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE opportunities
                                SET payload_json = %s, updated_ts = NOW()
                                WHERE id = %s
                            """, (json.dumps(payload), pos["id"]))
                        conn.commit()
                except Exception as e:
                    logger.debug("S9 monitor: failed to update opp %s: %s",
                                 pos["id"], e)

        if exit_flagged > 0:
            logger.info("S9 monitor: %d/%d positions flagged for early exit",
                        exit_flagged, len(positions_to_tape))

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
                    for setup_id in ["S9", "U1", "S10", "D1"]:
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
            for setup_id in ["S9", "U1", "S10", "D1"]:
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
    stop_registry_errors = validate_stop_registry(required_setup_ids=["S9", "U1", "S10", "D1"])
    if stop_registry_errors:
        raise RuntimeError(
            "Stop registry validation failed:\n- " + "\n- ".join(stop_registry_errors)
        )
    worker = SignalWorker()
    worker.run()
