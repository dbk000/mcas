"""
Signal Worker — Redis indicators -> Redis signals.

Separate Docker service (invariant #2). Consumes indicators_ready stream
events, reads indicator snapshots with mandatory m1/snap/m2 consistency
(invariant #22), runs S1/S2 evaluators, stores signals in Redis ZSETs.

BTC stale -> degrade gracefully (invariant #12).
"""

import asyncio
import sys
import json
import time
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta

import redis
import psycopg

from governance.persistence import PersistenceClient, RedisClient, get_governed_persistence
from governance.kernel import LineageEnforcer

# db_pool: governed import — get_conn for direct Postgres access
def _pool_get_conn():
    """Compatibility shim: returns a context-managed Postgres connection."""
    return PersistenceClient.get_connection()

from governance.cache_contract import (
    read_fresh_snapshot, read_indicator_snapshot,
    check_staleness, store_signal, prune_stale_signals,
    ind_hist_key, setup_state_key, sig_meta_key, ind_meta_key,
    b2s, b2f, symbol_partition, get_universe, get_timeframes,
    is_cache_ready, get_candles_as_ohlcv,
    SETUP_STATE_TTL_S, SIGNAL_MAX_AGE_S, RECONCILE_INTERVAL_S,
    INDICATOR_HISTORY_SIZE, TF_SECONDS,
    PIPELINE_ACTIVITY_KEY, PIPELINE_ACTIVITY_MAX,
    # Unicorn keys
    get_embryo_active_symbols, read_unicorn_state,
    unicorn_state_key, UNICORN_STATE_TTL_S,
)
import numpy as np
from indicators.indicators import IndicatorSnapshot, FeatureFactory, OHLCV
from core_logic.s9_short_reversion import S9Evaluator, S9Signal, S9State
from core_logic.s10_macd_scalp import S10Evaluator, S10Signal
from core_logic.u1_unicorn_ignition import U1Evaluator, U1State, U1Phase, U1Config, load_funding_rates
from core_logic.d1_dormancy_ignition import D1Evaluator, D1State, D1Phase, D1Config, is_d1_excluded, get_social_mentions_count, get_social_silence_count, get_exchange_netflow
from governance.setup_base import Direction
from core_logic.setup_base import SetupBase
from governance.bootstrap import (
    get_config, get_strategy, get_lineage,
    strategy_as_api_dict, strategy_active_setup_ids,
    strategies_for_timeframe, strategy_get_multiplier,
)
from governance.config_registry import (
    WorkerTypedConfig,
)

# LiveConfig: hot-reload from Pipeline
class _LiveConfigCompat:
    """Compatibility shim for LiveConfig (poll-based config reload)."""
    def __init__(self, database_url, poll_interval_s=10.0):
        self.database_url = database_url
        self.poll_interval_s = poll_interval_s
        self._cached_version = 0
        self._last_check = 0.0

    def has_changed(self) -> bool:
        now = time.time()
        if now - self._last_check < self.poll_interval_s:
            return False
        self._last_check = now
        try:
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM meta.config_version WHERE key = 'version' LIMIT 1")
                    row = cur.fetchone()
                    if row:
                        new_ver = int(row[0])
                        if new_ver != self._cached_version:
                            self._cached_version = new_ver
                            return True
        except Exception:
            pass
        return False

    def get_strategy_params(self, setup_id: str, force_reload: bool = False):
        try:
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT nv.parameters
                        FROM meta.node n
                        JOIN meta.node_version nv ON nv.node_id = n.node_id
                        WHERE lower(n.name) LIKE lower(%s)
                          AND nv.status = 'deployed'
                          AND nv.parameters IS NOT NULL
                        ORDER BY nv.version_number DESC LIMIT 1
                    """, (f"%{setup_id}%",))
                    row = cur.fetchone()
                    if row and row[0]:
                        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception:
            pass
        return None

LiveConfig = _LiveConfigCompat

from core_logic.microstructure import (
    analyze_tape, analyze_exit_tape,
    MicrostructureContext,
)
from governance.microstructure_fetcher import fetch_1s_klines_batch
from governance.trace_context import TraceContext
from governance.dxy_regime import get_dxy_multiplier, DXY_REGIME_ENABLED
from governance.regime import fetch_fear_greed_index

logger = logging.getLogger("signal_worker")

# ---------------------------------------------------------------------------
# Config — resolved lazily from frozen ConfigRegistry (no os.getenv)
# ---------------------------------------------------------------------------

# Protocol constants (not config — these don't vary between environments)
XREAD_BLOCK_MS = 5000
XREAD_COUNT = 50
PRUNE_INTERVAL_S = 300  # Prune stale signals every 5 min


def _worker_cfg() -> WorkerTypedConfig:
    """Return the frozen Worker config from the bootstrap registry."""
    return get_config().Worker


def _redis_url() -> str:
    return _worker_cfg().REDIS_URL


def _database_url() -> str:
    return _worker_cfg().DATABASE_URL


# Thread-local overrides for worker_index/n_workers — used when two
# SignalWorker instances share one process (consolidated container).
# Each asyncio.to_thread gets its own thread, so thread-local is safe.
import threading as _threading
_thread_overrides = _threading.local()


def _worker_index() -> int:
    override = getattr(_thread_overrides, "worker_index", None)
    if override is not None:
        return override
    return _worker_cfg().WORKER_INDEX


def _n_workers() -> int:
    override = getattr(_thread_overrides, "n_workers", None)
    if override is not None:
        return override
    return _worker_cfg().N_WORKERS


def _log_level() -> str:
    return _worker_cfg().LOG_LEVEL


def _consumer_name() -> str:
    return f"sig-{_worker_index()}"


def _symbol_quality_filter() -> bool:
    return _worker_cfg().SYMBOL_QUALITY_FILTER


def _signal_top_n_daily() -> int:
    return _worker_cfg().SIGNAL_TOP_N_DAILY


def _signal_top_n_daily_per_setup() -> int:
    return _worker_cfg().SIGNAL_TOP_N_DAILY_PER_SETUP


def _capacity_threshold_usd() -> float:
    return _worker_cfg().CAPACITY_THRESHOLD_USD


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
    level = logging.getLevelName(_log_level().upper())
    root.setLevel(level if isinstance(level, int) else logging.INFO)


# ---------------------------------------------------------------------------
# State serialization helpers
# ---------------------------------------------------------------------------

def _serialize_state(state) -> str:
    """Serialize setup state to JSON."""
    from enum import Enum
    from dataclasses import fields
    d = {}
    for f in fields(state):
        v = getattr(state, f.name)
        if isinstance(v, Enum):
            d[f.name] = v.value
        else:
            d[f.name] = v
    return json.dumps(d)


# ---------------------------------------------------------------------------
# SignalWorker
# ---------------------------------------------------------------------------

class SignalWorker(LineageEnforcer):
    """Consumes indicators_ready events, evaluates setups, stores signals.

    Inherits LineageEnforcer: every governed payload is verified against the
    active process seal. Foreign or unsealed payloads kill the process.
    """

    def __init__(self):
        # Initialize lineage enforcement
        from governance.bootstrap import get_lineage
        super().__init__(get_lineage())
        self._governed_persistence = get_governed_persistence()
        # Invariant #24: decode_responses=True mandatory
        self.r = redis.Redis.from_url(_redis_url(), decode_responses=True)
        self.u1_evaluator = U1Evaluator()
        self.s9_evaluator = S9Evaluator()
        self.last_reconcile = 0.0
        self.last_prune = 0.0
        self._s9_last_evaluated_hour = 0  # Unix hour for S9 dedup
        self._s9_last_position_check = 0.0  # Position monitor timestamp
        # Per-worker consumer group: each worker instance gets ALL messages
        # and filters by partition locally. Fixes the shared-group bug where
        # ACK-ing another worker's message silently drops it.
        self.group_name = f"signal_group_{_worker_index()}"
        # Cache of EMBRYO_ACTIVE symbols (refreshed periodically)
        self._embryo_active_cache: set[str] = set()
        self._embryo_active_cache_ts = 0.0
        # Symbol quality filter (excluded symbols cache, 5-min TTL)
        self._symbol_quality_filter_enabled = _symbol_quality_filter()
        self._excluded_symbols_cache: set[str] = set()
        self._excluded_symbols_cache_ts: float = 0.0
        self.s10_evaluator = S10Evaluator()
        self.d1_evaluator = D1Evaluator()
        self._d1_prev_states: dict[str, D1State] = {}  # keyed by "symbol:tf"
        # LiveConfig: poll meta.config_version for hot-reload
        wcfg = _worker_cfg()
        db_url = _database_url()
        self._live_config = LiveConfig(
            database_url=db_url,
            poll_interval_s=wcfg.CONFIG_POLL_INTERVAL_S,
        ) if db_url else None
        # Diagnostic counters (reset per logging cycle)
        self._diag = {
            "evaluated": 0, "snapshot_fail": 0, "candle_fail": 0,
            "symbol_quality_excluded": 0,
            "s10_signal": 0, "d1_signal": 0,
            "s9_signal": 0, "s9_breadth_fail": 0,
            "s9_fng_gated": 0, "s9_fng_unavailable": 0, "s9_magnitude_filtered": 0,
            "congestion_dropped": 0,
        }
        self._last_diag_log = 0.0
        # Register strategies into the StrategyRegistry — this is the sole
        # authority for which strategies exist and which TFs they evaluate on.
        # Passing self binds worker_evaluate callables at registration time.
        from governance.strategy_registry import StrategyRegistry, register_all_strategies
        register_all_strategies(worker=self)

        # Eager resolution: traceable strategy set from frozen registry
        self._traceable_setups = {
            sid: handler.traceable
            for sid, handler in StrategyRegistry._strategies.items()
        }

        # ── A1 Startup Assertion ──────────────────────────────────────────
        # Crash at boot if any registered strategy's evaluator needs
        # indicator fields that SNAPSHOT_FIELDS doesn't carry through Redis.
        # This makes the failure mode LOUD (process death) instead of silent
        # (zeroed fields at runtime).
        from governance.cache_contract import SNAPSHOT_FIELDS
        from core_logic.setup_base import IndicatorRequirement
        _INDICATOR_REQ_TO_SNAPSHOT_FIELDS = {
            IndicatorRequirement.OHLCV: [],  # stored in ring buffer, not ind_snap
            IndicatorRequirement.ATR: ["atr"],
            IndicatorRequirement.CLV: ["clv"],
            IndicatorRequirement.VOLUME_Z: ["volume_z"],
            IndicatorRequirement.RSI: ["rsi", "rsi_sma", "rsi_slope"],
            IndicatorRequirement.SMA_50: ["sma_50"],
            IndicatorRequirement.SMA_200: ["sma_200"],
            IndicatorRequirement.EMA_9: ["ema_9"],
            IndicatorRequirement.EMA_20: ["ema_20"],
            IndicatorRequirement.EMA_21: ["ema_21"],
            IndicatorRequirement.EMA_50: ["ema_50"],
            IndicatorRequirement.BB_BANDS: ["bb_mb", "bb_ub", "bb_lb", "bb_width"],
            IndicatorRequirement.KC_BANDS: ["kc_mb", "kc_ub", "kc_lb"],
            IndicatorRequirement.SMA_SLOPES: ["sma50_slope_atr", "sma200_slope_atr"],
            IndicatorRequirement.MA_GAP: ["ma_gap_atr"],
            IndicatorRequirement.HTF_INDICATORS: ["htf_close", "htf_sma50", "htf_bb_mb"],
            IndicatorRequirement.MACD: ["macd_line", "macd_signal", "macd_histogram", "macd_prev_histogram"],
            IndicatorRequirement.SMA_20: ["sma_20"],
            IndicatorRequirement.DONCHIAN: ["donchian_high_20", "donchian_low_20", "donchian_mid_20"],
            IndicatorRequirement.VWAP_IND: ["vwap", "vwap_upper_2sd", "vwap_lower_2sd", "vwap_distance_atr"],
            IndicatorRequirement.EMA_SLOPES: ["ema20_slope_atr", "ema50_slope_atr"],
            IndicatorRequirement.DORMANCY_SCORE: ["dormancy_score"],
            IndicatorRequirement.ACCUM_DIVERGENCE: ["accum_divergence"],
        }
        snapshot_fields_set = set(SNAPSHOT_FIELDS)
        for sid, handler in StrategyRegistry._strategies.items():
            evaluator = handler.evaluator
            if evaluator is None:
                continue
            if not hasattr(evaluator, 'required_indicators'):
                continue
            for req in evaluator.required_indicators:
                mapped_fields = _INDICATOR_REQ_TO_SNAPSHOT_FIELDS.get(req)
                if mapped_fields is None:
                    raise RuntimeError(
                        f"GOVERNANCE VIOLATION: Strategy {sid} requires {req.name} "
                        f"but no SNAPSHOT_FIELDS mapping exists for it. "
                        f"Add a mapping in signal_worker.__init__ assertion."
                    )
                missing = [f for f in mapped_fields if f not in snapshot_fields_set]
                if missing:
                    raise RuntimeError(
                        f"GOVERNANCE VIOLATION: Strategy {sid} requires {req.name} "
                        f"which maps to SNAPSHOT_FIELDS {mapped_fields}, but "
                        f"{missing} are NOT in SNAPSHOT_FIELDS. "
                        f"Add them to cache_contract.py SNAPSHOT_FIELDS."
                    )
        logger.info("SNAPSHOT_FIELDS assertion passed: all registered strategies' "
                     "required_indicators are satisfiable")
        # ── End A1 Startup Assertion ──────────────────────────────────────

        # ── A2 Registry ↔ Class Constant Sync Assertions ─────────────────
        # Crash at boot if registry values diverge from core_logic class constants.
        # core_logic modules still read class constants (D1Config, U1Config) internally;
        # signal_worker reads from registry. Both MUST agree.
        _d1_reg = get_config().D1
        _d1_sync_fields = {
            "DORMANCY_THRESHOLD": (D1Config.DORMANCY_THRESHOLD, _d1_reg.DORMANCY_THRESHOLD),
            "BB_WIDTH_COMPRESS": (D1Config.BB_WIDTH_COMPRESS, _d1_reg.BB_WIDTH_COMPRESS),
            "VOLUME_Z_SPIKE": (D1Config.VOLUME_Z_SPIKE, _d1_reg.VOLUME_Z_SPIKE),
            "RVOL_MIN": (D1Config.RVOL_MIN, _d1_reg.RVOL_MIN),
            "STOP_PCT": (D1Config.STOP_PCT, _d1_reg.STOP_PCT),
            "TP1_PCT": (D1Config.TP1_PCT, _d1_reg.TP1_PCT),
            "TP2_PCT": (D1Config.TP2_PCT, _d1_reg.TP2_PCT),
            "TIME_STOP_BARS_1H": (D1Config.TIME_STOP_BARS_1H, _d1_reg.TIME_STOP_BARS_1H),
            "DORMANCY_LOOKBACK": (D1Config.DORMANCY_LOOKBACK, _d1_reg.DORMANCY_LOOKBACK),
            "SOCIAL_SILENCE_MAX_MENTIONS": (D1Config.SOCIAL_SILENCE_MAX_MENTIONS, _d1_reg.SOCIAL_SILENCE_MAX_MENTIONS),
            "ONCHAIN_GATE_ENABLED": (D1Config.ONCHAIN_GATE_ENABLED, _d1_reg.ONCHAIN_GATE_ENABLED),
            "W_DORMANCY": (D1Config.W_DORMANCY, _d1_reg.W_DORMANCY),
            "W_VOL_Z": (D1Config.W_VOL_Z, _d1_reg.W_VOL_Z),
            "W_BTC_ADJ_RET": (D1Config.W_BTC_ADJ_RET, _d1_reg.W_BTC_ADJ_RET),
        }
        for field_name, (class_val, reg_val) in _d1_sync_fields.items():
            assert class_val == reg_val, (
                f"D1 {field_name}: class={class_val} != registry={reg_val}"
            )
        _u1_reg = get_config().U1
        _u1_sync_fields = {
            "IGNITION_SCORE_THRESHOLD": (U1Config.IGNITION_SCORE_THRESHOLD, _u1_reg.IGNITION_SCORE_THRESHOLD),
            "VOL_Z_MIN": (U1Config.VOL_Z_MIN, _u1_reg.VOL_Z_MIN),
            "TRADABILITY_IMPROVEMENT_FACTOR": (U1Config.TRADABILITY_IMPROVEMENT_FACTOR, _u1_reg.TRADABILITY_IMPROVEMENT_FACTOR),
            "PARTICIPATION_GROWTH_FACTOR": (U1Config.PARTICIPATION_GROWTH_FACTOR, _u1_reg.PARTICIPATION_GROWTH_FACTOR),
            "RANGE_PCT_THRESHOLD": (U1Config.RANGE_PCT_THRESHOLD, _u1_reg.RANGE_PCT_THRESHOLD),
            "REL_VOL_IGNITION_MIN": (U1Config.REL_VOL_IGNITION_MIN, _u1_reg.REL_VOL_IGNITION_MIN),
            "STOP_RANGE_MULT": (U1Config.STOP_RANGE_MULT, _u1_reg.STOP_RANGE_MULT),
            "MIN_STOP_PCT": (U1Config.MIN_STOP_PCT, _u1_reg.MIN_STOP_PCT),
            "TP1_R": (U1Config.TP1_R, _u1_reg.TP1_R),
            "TP2_R": (U1Config.TP2_R, _u1_reg.TP2_R),
            "TIME_STOP_BARS_5M": (U1Config.TIME_STOP_BARS_5M, _u1_reg.TIME_STOP_BARS_5M),
            "MAX_EMBRYO_ACTIVE": (U1Config.MAX_EMBRYO_ACTIVE, _u1_reg.MAX_EMBRYO_ACTIVE),
            "GATE_TIMEFRAME": (U1Config.GATE_TIMEFRAME, _u1_reg.GATE_TIMEFRAME),
            "GATE_WINDOW_BARS": (U1Config.GATE_WINDOW_BARS, _u1_reg.GATE_WINDOW_BARS),
        }
        for field_name, (class_val, reg_val) in _u1_sync_fields.items():
            assert class_val == reg_val, (
                f"U1 {field_name}: class={class_val} != registry={reg_val}"
            )
        logger.info("A2 Registry sync assertions passed: D1 (%d fields), U1 (%d fields)",
                     len(_d1_sync_fields), len(_u1_sync_fields))
        # ── End A2 Registry Sync Assertions ──────────────────────────────

        # 24h volume cache: {symbol: volume_usd}, refreshed every 5 min
        self._volume_cache: dict[str, float] = {}
        self._volume_cache_ts: float = 0.0

    # ------------------------------------------------------------------
    # D1 Cold-Start Bootstrap
    # ------------------------------------------------------------------

    def _bootstrap_d1_states(self):
        """Replay recent 1h OHLCV from Postgres through D1's compute_state()
        to populate _d1_prev_states at startup — eliminates cold-start delay
        where D1 needs DORMANCY_LOOKBACK bars to reach DORMANT_CONFIRMED.

        Governance:
        - Replay window derived from get_config().D1.DORMANCY_LOOKBACK * MULTIPLIER
        - OHLCV from governed Postgres persistence layer
        - FeatureFactory for indicator computation (same code path as live)
        - is_d1_excluded() applied (same filter as live eval)
        """
        t0 = time.time()
        d1_cfg = get_config().D1
        lookback = d1_cfg.DORMANCY_LOOKBACK
        multiplier = d1_cfg.BOOTSTRAP_WARMUP_MULTIPLIER
        replay_bars = int(lookback * multiplier)
        # FeatureFactory needs 201 bars minimum for indicator warmup
        indicator_warmup = 201
        total_bars_needed = indicator_warmup + replay_bars

        symbols = get_universe(self.r)
        eligible = [s for s in symbols if not is_d1_excluded(s)]

        if not eligible:
            logger.info("D1 bootstrap: no eligible symbols in monitored universe")
            return

        logger.info(
            "D1 bootstrap: replaying %d bars (lookback=%d * multiplier=%.1f) "
            "for %d eligible symbols (fetching %d bars each for indicator warmup)",
            replay_bars, lookback, multiplier, len(eligible), total_bars_needed,
        )

        bootstrapped = 0
        oldest_ts = None
        newest_ts = None
        skipped_insufficient = 0

        with _pool_get_conn() as conn:
            with conn.cursor() as cur:
                for symbol in eligible:
                    try:
                        # Load last total_bars_needed rows of 1h OHLCV
                        # Prefer ws source (live data), fall back to binance (REST backfill)
                        cur.execute("""
                            SELECT bar_start_ts, open, high, low, close, volume
                            FROM (
                                SELECT DISTINCT ON (bar_start_ts)
                                       bar_start_ts, open, high, low, close, volume
                                FROM ohlcv
                                WHERE symbol = %s AND timeframe = '1h'
                                  AND is_final = TRUE
                                ORDER BY bar_start_ts,
                                         CASE source
                                             WHEN 'ws' THEN 0
                                             WHEN 'binance' THEN 1
                                             ELSE 2
                                         END
                            ) sub
                            ORDER BY bar_start_ts DESC
                            LIMIT %s
                        """, (symbol, total_bars_needed))

                        rows = cur.fetchall()

                        if len(rows) < total_bars_needed:
                            skipped_insufficient += 1
                            continue

                        # Reverse to chronological order (oldest first)
                        rows = rows[::-1]

                        # Convert Postgres rows to OHLCV dataclass instances
                        # bar_start_ts is TIMESTAMPTZ — convert to epoch seconds (int)
                        all_candles = [
                            OHLCV(
                                timestamp=int(row[0].timestamp()),
                                open=float(row[1]),
                                high=float(row[2]),
                                low=float(row[3]),
                                close=float(row[4]),
                                volume=float(row[5]),
                            )
                            for row in rows
                        ]

                        # Replay the last replay_bars through D1 state machine
                        ff = FeatureFactory()
                        prev_state = None

                        for i in range(replay_bars):
                            # Window: indicator_warmup bars ending at this replay position
                            end_idx = indicator_warmup + i + 1
                            window = all_candles[:end_idx]

                            snapshot = ff.compute(window)
                            prev_state = self.d1_evaluator.compute_state(
                                snapshot, prev_state=prev_state, btc_returns=None,
                            )

                        if prev_state is not None:
                            state_key = f"{symbol}:1h"
                            self._d1_prev_states[state_key] = prev_state
                            bootstrapped += 1

                            # Track timestamp range for log summary
                            bar_ts = all_candles[-1].timestamp
                            first_replay_ts = all_candles[indicator_warmup].timestamp
                            if oldest_ts is None or first_replay_ts < oldest_ts:
                                oldest_ts = first_replay_ts
                            if newest_ts is None or bar_ts > newest_ts:
                                newest_ts = bar_ts

                    except Exception as e:
                        logger.warning("D1 bootstrap failed for %s: %s", symbol, e)

        elapsed = time.time() - t0
        oldest_str = (
            datetime.fromtimestamp(oldest_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if oldest_ts else "N/A"
        )
        newest_str = (
            datetime.fromtimestamp(newest_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if newest_ts else "N/A"
        )
        logger.info(
            "D1 state bootstrapped: %d symbols, oldest state from %s, newest from %s "
            "(%.1fs, %d skipped insufficient data)",
            bootstrapped, oldest_str, newest_str, elapsed, skipped_insufficient,
        )

    def run(self):
        """Main loop."""
        logger.info(f"Signal worker {_worker_index()}/{_n_workers()} starting")
        logger.info("StrategyRegistry populated: %s",
                     [sid for sid in self._traceable_setups])

        while not is_cache_ready(self.r):
            logger.info("Waiting for cache:ready...")
            time.sleep(5)

        logger.info("Cache ready, starting stream consumption")

        # Startup config sync: Pipeline is source of truth.
        self._sync_node_parameters()

        # Sync config hashes to config_active_strategies for dashboard tracking
        if _database_url() and _worker_index() == 0:
            try:
                from governance.bootstrap import sync_config_hashes
                sync_config_hashes()
            except Exception as e:
                logger.warning("Config hash sync failed (non-fatal): %s", e)

        # D1 cold-start bootstrap: replay recent OHLCV to populate state machine
        if _database_url():
            self._bootstrap_d1_states()

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

        streams = {f"stream:indicators_ready:{tf}": ">" for tf in timeframes}

        while True:
            try:
                results = self.r.xreadgroup(
                    groupname=self.group_name,
                    consumername=_consumer_name(),
                    streams=streams,
                    count=XREAD_COUNT,
                    block=XREAD_BLOCK_MS,
                )

                batch_evals = 0
                if results:
                    for stream_name, messages in results:
                        tf = b2s(stream_name).split(":")[-1]
                        for msg_id, fields in messages:
                            self._process_event(tf, fields, msg_id, stream_name)
                            batch_evals += 1

                now = time.time()

                # Pipeline activity: push compact event per XREAD cycle
                if batch_evals > 0:
                    self._push_activity_event(now, batch_evals)

                self._maybe_hot_reload_config()

                if now - self.last_reconcile > RECONCILE_INTERVAL_S:
                    self._reconcile()
                    self.last_reconcile = now

                if now - self.last_prune > PRUNE_INTERVAL_S:
                    self._prune_all()
                    self.last_prune = now

                self._maybe_evaluate_s9()
                self._maybe_monitor_s9_positions()

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

    _last_activity_snapshot = None  # Cached per-cycle snapshot for activity events

    def _push_activity_event(self, now: float, batch_evals: int):
        """Push a full-trace activity event to Redis ring buffer.

        Each event mirrors the trace_context.py shape: eval_ts, gates,
        score, indicator count, bar count, latency. This is the firehose
        that powers the landing page heartbeat.
        """
        try:
            snap = self._last_activity_snapshot
            gates_ok = self._diag["evaluated"] - self._diag["snapshot_fail"] - self._diag["candle_fail"] - self._diag["symbol_quality_excluded"]
            gates_fail = self._diag["snapshot_fail"] + self._diag["candle_fail"] + self._diag["symbol_quality_excluded"]
            total_signals = self._diag["s10_signal"] + self._diag["d1_signal"] + self._diag["s9_signal"]

            event = {
                "ts": now,
                "w": _worker_index(),
                "evals": batch_evals,
                "signals": total_signals,
                "gates": [
                    {"name": "snapshot", "passed": self._diag["snapshot_fail"] == 0, "checked": self._diag["evaluated"]},
                    {"name": "candles", "passed": self._diag["candle_fail"] == 0, "checked": self._diag["evaluated"]},
                    {"name": "quality", "passed": self._diag["symbol_quality_excluded"] == 0, "checked": self._diag["evaluated"]},
                ],
                "gates_ok": gates_ok,
                "gates_fail": gates_fail,
                "ind_count": snap["ind_count"] if snap else 0,
                "bar_count": snap["bar_count"] if snap else 0,
                "score": snap["score"] if snap else None,
                "tf": snap.get("tf") if snap else None,
                "indicators": snap.get("indicators") if snap else None,
                "candle_window": snap.get("candle_window") if snap else None,
            }
            raw = json.dumps(event, separators=(",", ":"))
            pipe = self.r.pipeline(transaction=False)
            pipe.lpush(PIPELINE_ACTIVITY_KEY, raw)
            pipe.ltrim(PIPELINE_ACTIVITY_KEY, 0, PIPELINE_ACTIVITY_MAX - 1)
            pipe.execute()
        except Exception:
            pass  # Non-critical — feed degrades gracefully

    def _process_event(self, tf: str, fields: dict, msg_id, stream_name):
        """Process a single indicators_ready event."""
        symbol = b2s(fields.get("symbol", ""))
        bar_start_ts = b2s(fields.get("bar_start_ts", ""))

        if not symbol or not bar_start_ts:
            self.r.xack(stream_name, self.group_name, msg_id)
            return

        if symbol_partition(symbol, _n_workers()) != _worker_index():
            self.r.xack(stream_name, self.group_name, msg_id)
            return

        try:
            self._evaluate_symbol(symbol, tf, bar_start_ts)
        except Exception as e:
            logger.error(f"Failed to evaluate {symbol}:{tf}: {e}", exc_info=True)

        self.r.xack(stream_name, self.group_name, msg_id)

    def _evaluate_symbol(self, symbol: str, tf: str, bar_start_ts: str = None):
        """Run registered evaluators for a symbol/tf.

        Dispatch is governed by StrategyRegistry.strategies_for_timeframe(tf).
        Each registered strategy with matching timeframe_affinity gets evaluated.
        """
        self._diag["evaluated"] += 1

        if self._is_symbol_excluded(symbol):
            self._diag["symbol_quality_excluded"] += 1
            return

        snapshot, snap_ts = read_fresh_snapshot(
            self.r, symbol, tf,
            expected_bar_start_ts=bar_start_ts,
        )
        if snapshot is None:
            self._diag["snapshot_fail"] += 1
            logger.debug(f"Stale/inconsistent snapshot for {symbol}:{tf}, skipping")
            return

        snapshot.ohlcv = get_candles_as_ohlcv(self.r, symbol, tf)
        if not snapshot.ohlcv:
            self._diag["candle_fail"] += 1
            logger.debug(f"No candles in Redis for {symbol}:{tf}, skipping")
            return

        # Capture full snapshot for activity firehose (last eval wins per cycle)
        from dataclasses import fields as dc_fields
        snap_dict = {}
        for f in dc_fields(snapshot):
            if f.name == "ohlcv":
                continue
            v = getattr(snapshot, f.name, None)
            if isinstance(v, float):
                snap_dict[f.name] = round(v, 6)
            elif v is not None:
                snap_dict[f.name] = v
        self._last_activity_snapshot = {
            "ind_count": len(snap_dict),
            "bar_count": len(snapshot.ohlcv),
            "score": None,
            "tf": tf,
            "indicators": snap_dict,
            "candle_window": {
                "first_ts": str(snapshot.ohlcv[0].timestamp),
                "last_ts": str(snapshot.ohlcv[-1].timestamp),
                "bar_count": len(snapshot.ohlcv),
                "timeframe": tf,
            },
        }

        # Registry-driven dispatch: evaluate all strategies registered for this TF
        from governance.strategy_registry import StrategyRegistry
        for setup_id, handler in StrategyRegistry.strategies_for_timeframe(tf):
            if handler.worker_evaluate is None:
                raise NotImplementedError(f"No worker_evaluate bound for strategy {setup_id}")
            handler.worker_evaluate(symbol, tf, snapshot, bar_start_ts)

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

    # ------------------------------------------------------------------
    # Signal Congestion Control (per-strategy budgets)
    # ------------------------------------------------------------------

    def _daily_count_key(self, setup_id: str) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"signals:daily_count:{setup_id}:{today}"

    def _get_daily_signal_count(self, setup_id: str) -> int:
        try:
            val = self.r.get(self._daily_count_key(setup_id))
            return int(val) if val else 0
        except Exception:
            return 0

    def _increment_daily_signal_count(self, setup_id: str) -> int:
        key = self._daily_count_key(setup_id)
        try:
            new_val = self.r.incr(key)
            if new_val == 1:
                self.r.expire(key, 48 * 3600)
            return new_val
        except Exception:
            return 0

    def _is_budget_exhausted(self, setup_id: str) -> bool:
        top_n = _signal_top_n_daily_per_setup()
        if top_n <= 0:
            return False
        return self._get_daily_signal_count(setup_id) >= top_n

    def _try_emit_signal(self, symbol: str, tf: str, setup_id: str,
                         calibrated_score: float) -> bool:
        top_n = _signal_top_n_daily_per_setup()
        if top_n <= 0:
            return True

        current = self._get_daily_signal_count(setup_id)
        if current >= top_n:
            self._diag["congestion_dropped"] += 1
            logger.info(
                "CONGESTION_DROP %s %s:%s score=%.3f budget=%d/%d",
                setup_id, symbol, tf, calibrated_score,
                current, top_n,
            )
            return False

        self._increment_daily_signal_count(setup_id)
        return True

    # ------------------------------------------------------------------
    # Capacity-Aware Symbol Ranking
    # ------------------------------------------------------------------

    def _refresh_volume_cache(self):
        now = time.time()
        if now - self._volume_cache_ts < 300:
            return

        try:
            all_symbols = get_universe(self.r)
            vol_map = {}
            for symbol in all_symbols:
                candles = get_candles_as_ohlcv(self.r, symbol, "1h")
                if not candles or len(candles) < 24:
                    continue
                recent = candles[-24:]
                vol_usd = sum(c.close * c.volume for c in recent)
                vol_map[symbol] = vol_usd

            self._volume_cache = vol_map
            self._volume_cache_ts = now
        except Exception as e:
            logger.debug("Failed to refresh volume cache: %s", e)

    def _capacity_adjusted_score(self, symbol: str,
                                  calibrated_score: float) -> float:
        threshold = _capacity_threshold_usd()
        if threshold <= 0:
            return calibrated_score

        self._refresh_volume_cache()
        vol_24h = self._volume_cache.get(symbol, 0.0)

        if vol_24h <= 0:
            return calibrated_score

        capacity_mult = min(1.0, vol_24h / threshold)
        return calibrated_score * capacity_mult

    def _evaluate_s10(self, symbol: str, tf: str, snapshot, bar_start_ts: str):
        """Run S10 MACD Scalp evaluator on 5m bars."""
        try:
            state = self.s10_evaluator.compute_state(snapshot)
            # Edge re-evaluation for active S10 opps on this symbol/tf
            self._reeval_s10_edge(symbol, tf, state)
            sig = self.s10_evaluator.evaluate_trigger(snapshot, state, symbol, tf)
            if sig is None:
                return

            self._diag["s10_signal"] += 1

            calib_mult = strategy_get_multiplier("S10")
            calibrated_score = min(sig.setup_score * calib_mult, 1.0)

            dxy_mult = get_dxy_multiplier(self.r)
            calibrated_score = min(calibrated_score * dxy_mult, 1.0)

            adjusted_score = self._capacity_adjusted_score(symbol, calibrated_score)

            if not self._try_emit_signal(symbol, tf, "S10", adjusted_score):
                return

            payload = {
                "setup_id": "S10",
                "setup_name": self._get_setup_label("S10"),
                "symbol": symbol,
                "timeframe": tf,
                "direction": sig.direction.value,
                "setup_score": adjusted_score,
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

            # --- TRACE (mandatory) ---
            trace_dict = None
            trace = TraceContext(strategy="S10", symbol=symbol, timeframe=tf)
            if snapshot.ohlcv:
                trace.record_candle_window(
                    first_ts=snapshot.ohlcv[0].timestamp,
                    last_ts=snapshot.ohlcv[-1].timestamp,
                    bar_count=len(snapshot.ohlcv),
                    timeframe=tf,
                )
            trace.record_indicator_params({
                "n_atr": 14, "n_rsi": 14, "n_bb": 20,
                "k_bb": 2.0, "n_kc": 20, "n_volz": 20,
            })
            trace.record_indicators_bulk({
                "macd_histogram": snapshot.macd_histogram,
                "macd_prev_histogram": snapshot.macd_prev_histogram,
                "rvol": snapshot.rvol,
                "sma_20": snapshot.sma_20,
                "sma_200": snapshot.sma_200,
                "atr": snapshot.atr,
                "bb_width": snapshot.bb_width,
                "volume_z": snapshot.volume_z,
            })
            trace.record_state({
                "above_sma200": state.above_sma200,
                "above_sma20": state.above_sma20,
                "price_vs_sma20_atr": state.price_vs_sma20_atr,
                "macd_histogram": state.macd_histogram,
                "macd_prev_histogram": state.macd_prev_histogram,
                "macd_crossover_bull": state.macd_crossover_bull,
                "flat_top_detected": state.flat_top_detected,
                "flat_top_level": state.flat_top_level,
                "flat_top_touches": state.flat_top_touches,
                "breakout": state.breakout,
                "rvol": state.rvol,
                "entry_valid": state.entry_valid,
            }, is_hydrated=False)
            trace.record_gate("above_sma200", state.above_sma200, True, "==", state.above_sma200)
            trace.record_gate("macd_crossover_bull", state.macd_crossover_bull, True, "==", state.macd_crossover_bull)
            trace.record_gate("flat_top_detected", state.flat_top_detected, True, "==", state.flat_top_detected)
            trace.record_gate("breakout", state.breakout, True, "==", state.breakout)
            trace.record_gate("rvol", state.rvol, self.s10_evaluator.RVOL_MIN, ">=", True)
            trace.record_gate("sma20_proximity", abs(state.price_vs_sma20_atr),
                              self.s10_evaluator.SMA20_PROXIMITY_ATR, "<=", True)
            trace.record_score(
                sig.score_components, sig.setup_score,
                calib_mult, calibrated_score, promotion_threshold=0.0,
            )
            trace.record_config({
                "RVOL_MIN": self.s10_evaluator.RVOL_MIN,
                "FLAT_TOP_LOOKBACK": self.s10_evaluator.FLAT_TOP_LOOKBACK,
                "FLAT_TOP_TOLERANCE_ATR": self.s10_evaluator.FLAT_TOP_TOLERANCE_ATR,
                "FLAT_TOP_MIN_TOUCHES": self.s10_evaluator.FLAT_TOP_MIN_TOUCHES,
                "SMA20_PROXIMITY_ATR": self.s10_evaluator.SMA20_PROXIMITY_ATR,
                "STOP_ATR_MULT": self.s10_evaluator.STOP_ATR_MULT,
                "TP1_R_MULT": self.s10_evaluator.TP1_R_MULT,
                "TP2_R_MULT": self.s10_evaluator.TP2_R_MULT,
                "TIME_STOP_BARS": self.s10_evaluator.TIME_STOP_BARS,
            })
            setup_version = self._get_setup_version("S10")
            trace_dict = trace.seal(setup_version)

            store_signal(self.r, symbol, tf, "S10", payload,
                         adjusted_score, bar_start_ts or "")
            reason = "; ".join(sig.explain.why_items) if sig.explain else None
            self._persist_signal_to_pg(symbol, tf, "S10", adjusted_score,
                                       reason, payload, trace_json=trace_dict)

            logger.info(
                "Signal: S10 LONG %s:%s score=%.3f (adj=%.3f) rvol=%.1fx macd_hist=%.6f",
                symbol, tf, calibrated_score, adjusted_score,
                sig.rvol, sig.macd_histogram,
            )
        except Exception as e:
            logger.debug("S10 eval error %s:%s: %s", symbol, tf, e)

    def _evaluate_d1(self, symbol: str, tf: str, snapshot, bar_start_ts: str):
        """Run D1 Dormancy-to-Ignition evaluator on 1h bars (microcap universe)."""
        if is_d1_excluded(symbol):
            return

        d1_cfg = get_config().D1

        try:
            state_key = f"{symbol}:{tf}"
            prev_state = self._d1_prev_states.get(state_key)

            state = self.d1_evaluator.compute_state(
                snapshot, prev_state=prev_state, btc_returns=None,
            )
            self._d1_prev_states[state_key] = state

            # Edge re-evaluation for active D1 opps on this symbol/tf
            self._reeval_d1_edge(symbol, tf, state)

            if state.phase in (D1Phase.DORMANT_CONFIRMED, D1Phase.ACCUMULATING):
                trigger_ts = None
                if bar_start_ts:
                    try:
                        trigger_ts = datetime.fromtimestamp(
                            int(bar_start_ts) / 1000, tz=timezone.utc
                        )
                    except (ValueError, TypeError):
                        pass
                state.social_mention_count = get_social_silence_count(
                    symbol, conn_factory=_pool_get_conn, trigger_bar_start_ts=trigger_ts,
                )
                if d1_cfg.ONCHAIN_GATE_ENABLED:
                    state.onchain_netflow = get_exchange_netflow(
                        symbol, conn_factory=_pool_get_conn, as_of_ts=trigger_ts,
                    )

            sig = self.d1_evaluator.evaluate_trigger(snapshot, state, symbol, tf)
            if sig is None:
                return

            self._diag["d1_signal"] += 1

            if _database_url():
                trigger_ts = None
                if bar_start_ts:
                    try:
                        trigger_ts = datetime.fromtimestamp(
                            int(bar_start_ts) / 1000, tz=timezone.utc
                        )
                    except (ValueError, TypeError):
                        pass
                sig.social_mentions_30d = get_social_mentions_count(
                    symbol, conn_factory=_pool_get_conn, trigger_bar_start_ts=trigger_ts,
                )

            calib_mult = strategy_get_multiplier("D1")
            calibrated_score = min(sig.setup_score * calib_mult, 1.0)

            dxy_mult = get_dxy_multiplier(self.r)
            calibrated_score = min(calibrated_score * dxy_mult, 1.0)

            adjusted_score = self._capacity_adjusted_score(symbol, calibrated_score)

            if not self._try_emit_signal(symbol, tf, "D1", adjusted_score):
                return

            social_silence_passed = (
                state.social_mention_count < 0
                or state.social_mention_count <= d1_cfg.SOCIAL_SILENCE_MAX_MENTIONS
            )

            payload = {
                "setup_id": "D1",
                "setup_name": self._get_setup_label("D1"),
                "symbol": symbol,
                "timeframe": tf,
                "direction": sig.direction.value,
                "setup_score": adjusted_score,
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
                "social_mentions_24h": state.social_mention_count,
                "social_silence_passed": social_silence_passed,
                "onchain_netflow": state.onchain_netflow,
                "onchain_gate_enabled": d1_cfg.ONCHAIN_GATE_ENABLED,
                "score_components": sig.score_components,
                "gates": {
                    "Gate 1 - Dormancy": f"dormancy_score >= {d1_cfg.DORMANCY_THRESHOLD}",
                    "Gate 2 - Compression": f"bb_width < {d1_cfg.BB_WIDTH_COMPRESS}",
                    "Gate 3 - Volume spike": (
                        f"volume_z >= {d1_cfg.VOLUME_Z_SPIKE} AND "
                        f"rvol >= {d1_cfg.RVOL_MIN}"
                    ),
                    "Gate 4 - Breakout": "close > prev_compression_box_high (previous donchian_high_20)",
                    "Gate 5 - BB expanding": "bb_width > bb_width_prev",
                    "Gate 6 - Social silence (soft)": (
                        f"mentions_24h <= {d1_cfg.SOCIAL_SILENCE_MAX_MENTIONS} "
                        f"(actual: {state.social_mention_count}, score weight: {d1_cfg.W_SOCIAL_SILENCE})"
                    ),
                    "Gate 7 - Exchange net flow (soft)": (
                        f"netflow < 0 = accumulation "
                        f"(actual: {state.onchain_netflow}, enabled: {d1_cfg.ONCHAIN_GATE_ENABLED}, "
                        f"score weight: {d1_cfg.W_ONCHAIN_FLOW})"
                    ),
                },
                "exit_config": {
                    "trail": "none",
                    "stop_pct": d1_cfg.STOP_PCT,
                    "tp1_pct": d1_cfg.TP1_PCT,
                    "tp2_pct": d1_cfg.TP2_PCT,
                    "time_stop_bars_1h": d1_cfg.TIME_STOP_BARS_1H,
                },
                "scoring_weights": {
                    "dormancy": d1_cfg.W_DORMANCY,
                    "vol_z": d1_cfg.W_VOL_Z,
                    "accum_div": d1_cfg.W_ACCUM_DIV,
                    "bb_expansion": d1_cfg.W_BB_EXPANSION,
                    "rvol": d1_cfg.W_RVOL,
                    "btc_adj_ret": d1_cfg.W_BTC_ADJ_RET,
                    "social_silence": d1_cfg.W_SOCIAL_SILENCE,
                    "onchain_flow": d1_cfg.W_ONCHAIN_FLOW,
                },
            }

            # --- TRACE (mandatory) ---
            trace_dict = None
            trace = TraceContext(strategy="D1", symbol=symbol, timeframe=tf)
            if snapshot.ohlcv:
                trace.record_candle_window(
                    first_ts=snapshot.ohlcv[0].timestamp,
                    last_ts=snapshot.ohlcv[-1].timestamp,
                    bar_count=len(snapshot.ohlcv),
                    timeframe=tf,
                )
            trace.record_indicator_params({
                "n_atr": 14, "n_rsi": 14, "n_bb": 20,
                "k_bb": 2.0, "n_kc": 20, "n_volz": 20,
            })
            trace.record_indicators_bulk({
                "dormancy_score": snapshot.dormancy_score,
                "bb_width": snapshot.bb_width,
                "volume_z": snapshot.volume_z,
                "rvol": snapshot.rvol,
                "accum_divergence": snapshot.accum_divergence,
                "donchian_high_20": snapshot.donchian_high_20,
                "donchian_low_20": snapshot.donchian_low_20,
                "atr": snapshot.atr,
                "rsi": snapshot.rsi,
            })
            is_hydrated = False
            trace.record_state(state.to_dict(), is_hydrated=is_hydrated)
            close = snapshot.ohlcv[-1].close if snapshot.ohlcv else 0.0
            trace.record_gate("dormancy", state.dormancy_score,
                              d1_cfg.DORMANCY_THRESHOLD, ">=", True)
            trace.record_gate("compression", state.bb_width,
                              d1_cfg.BB_WIDTH_COMPRESS, "<", True)
            trace.record_gate("volume_z", state.volume_z,
                              d1_cfg.VOLUME_Z_SPIKE, ">=", True)
            trace.record_gate("rvol", state.rvol,
                              d1_cfg.RVOL_MIN, ">=", True)
            trace.record_gate("breakout", close,
                              state.prev_compression_box_high, ">", True,
                              note="close > prev_compression_box_high")
            trace.record_gate("bb_expanding", state.bb_width,
                              state.bb_width_prev, ">", True,
                              note="bb_width > bb_width_prev")
            trace.record_gate("social_silence", state.social_mention_count,
                              d1_cfg.SOCIAL_SILENCE_MAX_MENTIONS, "<=",
                              social_silence_passed,
                              note=f"soft gate: mentions_24h={state.social_mention_count}, "
                                   f"threshold={d1_cfg.SOCIAL_SILENCE_MAX_MENTIONS}, "
                                   f"score_weight={d1_cfg.W_SOCIAL_SILENCE}")
            onchain_passed = (
                state.onchain_netflow is None
                or state.onchain_netflow < 0
            )
            trace.record_gate("onchain_netflow", state.onchain_netflow,
                              0, "<", onchain_passed,
                              note=f"soft gate: netflow={state.onchain_netflow}, "
                                   f"enabled={d1_cfg.ONCHAIN_GATE_ENABLED}, "
                                   f"score_weight={d1_cfg.W_ONCHAIN_FLOW}")
            trace.record_score(
                sig.score_components, sig.setup_score,
                calib_mult, calibrated_score, promotion_threshold=0.0,
            )
            trace.record_config({
                "DORMANCY_THRESHOLD": d1_cfg.DORMANCY_THRESHOLD,
                "BB_WIDTH_COMPRESS": d1_cfg.BB_WIDTH_COMPRESS,
                "VOLUME_Z_SPIKE": d1_cfg.VOLUME_Z_SPIKE,
                "RVOL_MIN": d1_cfg.RVOL_MIN,
                "STOP_PCT": d1_cfg.STOP_PCT,
                "TP1_PCT": d1_cfg.TP1_PCT,
                "TP2_PCT": d1_cfg.TP2_PCT,
                "TIME_STOP_BARS_1H": d1_cfg.TIME_STOP_BARS_1H,
                "SOCIAL_SILENCE_LOOKBACK_HOURS": d1_cfg.SOCIAL_SILENCE_LOOKBACK_HOURS,
                "SOCIAL_SILENCE_MAX_MENTIONS": d1_cfg.SOCIAL_SILENCE_MAX_MENTIONS,
                "ONCHAIN_GATE_ENABLED": d1_cfg.ONCHAIN_GATE_ENABLED,
                "ONCHAIN_NETFLOW_LOOKBACK_HOURS": d1_cfg.ONCHAIN_NETFLOW_LOOKBACK_HOURS,
                "W_ONCHAIN_FLOW": d1_cfg.W_ONCHAIN_FLOW,
            })
            setup_version = self._get_setup_version("D1")
            trace_dict = trace.seal(setup_version)

            store_signal(self.r, symbol, tf, "D1", payload,
                         adjusted_score, bar_start_ts or "")
            reason = "; ".join(sig.explain.why_items) if sig.explain else None
            self._persist_signal_to_pg(symbol, tf, "D1", adjusted_score,
                                       reason, payload, trace_json=trace_dict)

            logger.info(
                "Signal: D1 LONG %s:%s score=%.3f (adj=%.3f) dormancy=%.0f%% vol_z=%.1f rvol=%.1fx",
                symbol, tf, calibrated_score, adjusted_score,
                sig.dormancy_score * 100,
                sig.volume_z_at_trigger, sig.rvol_at_trigger,
            )
        except Exception as e:
            logger.debug("D1 eval error %s:%s: %s", symbol, tf, e)

    def _evaluate_u1(self, symbol: str, tf: str, snapshot, bar_start_ts: str):
        """Run U1 evaluator for EMBRYO_ACTIVE symbols only.

        TF filtering is handled by StrategyRegistry.timeframe_affinity — the
        registry only dispatches U1 on 5m. No internal TF guard needed.

        Accepts the same (symbol, tf, snapshot, bar_start_ts) signature as all
        other _evaluate_* methods — extracts candles internally.
        """
        u1_cfg = get_config().U1
        candles = snapshot.ohlcv

        self._refresh_embryo_active_cache()

        if symbol not in self._embryo_active_cache:
            return

        state_dict = read_unicorn_state(self.r, symbol)
        if not state_dict:
            return

        try:
            u1_state = U1State.from_dict(state_dict)
        except Exception as e:
            logger.debug(f"Failed to deserialize U1 state for {symbol}: {e}")
            return

        # Edge re-evaluation for active U1 opps on this symbol/tf
        self._reeval_u1_edge(symbol, tf, u1_state, snapshot=snapshot)

        if u1_state.phase not in (U1Phase.EMBRYO_ACTIVE, U1Phase.IGNITION_WATCH):
            return

        funding_rates = load_funding_rates(_pool_get_conn) if _database_url() else {}

        u1_signal = self.u1_evaluator.evaluate_trigger(candles, u1_state, symbol, tf, funding_rates=funding_rates, conn_factory=_pool_get_conn)
        if u1_signal:
            # --- TRACE (mandatory) ---
            trace_dict = None
            trace = TraceContext(strategy="U1", symbol=symbol, timeframe=tf)
            if candles:
                trace.record_candle_window(
                    first_ts=candles[0].timestamp,
                    last_ts=candles[-1].timestamp,
                    bar_count=len(candles),
                    timeframe=tf,
                )
            trace.record_indicator_params({
                "n_atr": 14, "n_rsi": 14, "n_bb": 20,
                "k_bb": 2.0, "n_kc": 20, "n_volz": 20,
            })
            trace.record_indicators_bulk({
                "rel_volume": u1_signal.rel_volume,
                "compression_box_high": u1_signal.compression_box_high,
                "compression_box_low": u1_signal.compression_box_low,
                "tradability_score": u1_signal.tradability_score,
                "participation_score": u1_signal.participation_score,
                "compression_score": u1_signal.compression_score,
                "embryo_active_score": u1_signal.embryo_active_score,
            })
            trace.record_state(u1_state.to_dict(), is_hydrated=False)
            trace.record_gate("phase", u1_state.phase.value,
                              "EMBRYO_ACTIVE|IGNITION_WATCH", "in", True)
            trace.record_gate("gate1_tradability", u1_state.gate1_passed,
                              True, "==", u1_state.gate1_passed)
            trace.record_gate("gate2_participation", u1_state.gate2_passed,
                              True, "==", u1_state.gate2_passed)
            trace.record_gate("gate3_compression", u1_state.gate3_passed,
                              True, "==", u1_state.gate3_passed)
            trace.record_gate("ignition_score",
                              u1_state.ignition_score,
                              u1_cfg.IGNITION_SCORE_THRESHOLD, ">=", True)
            trace.record_score(
                {"setup_score": u1_signal.setup_score},
                u1_signal.setup_score, 1.0, u1_signal.setup_score,
                promotion_threshold=u1_cfg.IGNITION_SCORE_THRESHOLD,
            )
            trace.record_config({
                "TRADABILITY_IMPROVEMENT_FACTOR": u1_cfg.TRADABILITY_IMPROVEMENT_FACTOR,
                "PARTICIPATION_GROWTH_FACTOR": u1_cfg.PARTICIPATION_GROWTH_FACTOR,
                "RANGE_PCT_THRESHOLD": u1_cfg.RANGE_PCT_THRESHOLD,
                "REL_VOL_IGNITION_MIN": u1_cfg.REL_VOL_IGNITION_MIN,
                "VOL_Z_MIN": u1_cfg.VOL_Z_MIN,
                "IGNITION_SCORE_THRESHOLD": u1_cfg.IGNITION_SCORE_THRESHOLD,
                "STOP_RANGE_MULT": u1_cfg.STOP_RANGE_MULT,
                "MIN_STOP_PCT": u1_cfg.MIN_STOP_PCT,
                "TP1_R": u1_cfg.TP1_R,
                "TP2_R": u1_cfg.TP2_R,
                "TIME_STOP_BARS_5M": u1_cfg.TIME_STOP_BARS_5M,
            })
            setup_version = self._get_setup_version("U1")
            trace_dict = trace.seal(setup_version)
            self._store_u1_signal(u1_signal, symbol, tf, bar_start_ts,
                                  trace_json=trace_dict)

    def _store_u1_signal(self, signal, symbol: str, tf: str, bar_start_ts: str,
                         trace_json: dict = None):
        """Store U1 signal in Redis."""
        dxy_mult = get_dxy_multiplier(self.r)
        u1_calibrated = min(signal.setup_score * dxy_mult, 1.0)

        adjusted_score = self._capacity_adjusted_score(symbol, u1_calibrated)

        if not self._try_emit_signal(symbol, tf, "U1", adjusted_score):
            return

        payload = {
            "setup_id": "U1",
            "symbol": symbol,
            "timeframe": tf,
            "direction": signal.direction.value,
            "setup_score": adjusted_score,
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
            "rel_volume": signal.rel_volume,
            "compression_box_high": signal.compression_box_high,
            "compression_box_low": signal.compression_box_low,
            "confirmations_met": signal.confirmations_met,
            "tradability_score": signal.tradability_score,
            "participation_score": signal.participation_score,
            "compression_score": signal.compression_score,
            "embryo_active_score": signal.embryo_active_score,
            "trigger_path": signal.trigger_path,
        }

        if signal.explain:
            payload["why_items"] = signal.explain.why_items

        store_signal(
            self.r, symbol, tf, "U1",
            signal_payload=payload,
            setup_score=adjusted_score,
            bar_start_ts=bar_start_ts,
        )

        reason = "; ".join(signal.explain.why_items) if signal.explain else None
        self._persist_signal_to_pg(symbol, tf, "U1", adjusted_score,
                                   reason, payload, trace_json=trace_json)

        logger.info(
            f"Signal: U1 {signal.direction.value} {symbol}:{tf} "
            f"score={adjusted_score:.3f} (raw={signal.setup_score:.3f}) "
            f"rel_vol={signal.rel_volume:.1f}x "
            f"path={signal.trigger_path}"
        )

    def _get_setup_version(self, setup_id: str) -> str:
        """Get setup version from StrategyRegistry handler."""
        from governance.strategy_registry import StrategyRegistry
        handler = StrategyRegistry.get_handler(setup_id)
        if handler:
            return handler.version
        return f"{setup_id}_v1.0"

    def _get_setup_label(self, setup_id: str) -> str:
        """Get human-readable setup name from StrategyRegistry handler."""
        from governance.strategy_registry import StrategyRegistry
        handler = StrategyRegistry.get_handler(setup_id)
        if handler and handler.label:
            return handler.label
        return f"{setup_id} (unknown)"

    def _sync_node_parameters(self):
        """Sync parameters between code and Pipeline at startup."""
        if _worker_index() != 0:
            return
        if not _database_url():
            return

        # Derive node names from StrategyRegistry (sole authority for strategy identity)
        _catalog = strategy_as_api_dict()
        NODE_NAMES = {
            sid: info["label"]
            for sid, info in _catalog.items()
            if info.get("status") == "active" and info.get("type") == "crypto"
        }

        # Derive evaluators from runtime StrategyRegistry (sole authority)
        from governance.strategy_registry import StrategyRegistry
        evaluators = [
            (setup_id, handler.evaluator)
            for setup_id, handler in StrategyRegistry._strategies.items()
            if handler.evaluator is not None
        ]

        for setup_id, evaluator in evaluators:
            try:
                node_name = NODE_NAMES.get(setup_id)
                if not node_name:
                    continue

                any_version_exists = self._node_has_any_version(node_name)

                if any_version_exists:
                    deployed_params = None
                    if self._live_config:
                        deployed_params = self._live_config.get_strategy_params(
                            setup_id, force_reload=True
                        )
                    if deployed_params and handler.supports_config_sync:
                        changed = evaluator.update_config(deployed_params)
                        if changed:
                            evaluator.sync_config(changed)
                            logger.info(
                                "%s: loaded config from Pipeline (%d params changed)",
                                setup_id, len(changed),
                            )
                        else:
                            logger.info("%s: Pipeline config matches code defaults", setup_id)
                    elif deployed_params:
                        logger.info("%s: loaded Pipeline config (no config sync support)", setup_id)
                    else:
                        logger.info(
                            "%s: Pipeline has version(s) but none deployed -- keeping code defaults",
                            setup_id,
                        )
                else:
                    self._bootstrap_node_params(setup_id, evaluator, node_name)

            except Exception as e:
                logger.warning(f"Failed to sync {setup_id} parameters: {e}")

    def _node_has_any_version(self, node_name: str) -> bool:
        try:
            with _pool_get_conn() as conn:
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
        # Collect base params from SetupBase.parameters if available
        params = {}
        if isinstance(evaluator, SetupBase):
            for p in evaluator.parameters:
                params[p.name] = p.default

        # Merge bootstrap metadata from the evaluator (concrete method on SetupBase)
        if isinstance(evaluator, SetupBase):
            meta = evaluator.get_bootstrap_metadata()
            if meta:
                params.update(meta)

        if not params:
            logger.debug(f"No bootstrap params for {setup_id} -- skipping")
            return

        params_json = json.dumps(params)

        with _pool_get_conn() as conn:
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
                    logger.debug(f"No meta.node_version found for {setup_id} -- skipping bootstrap")
            conn.commit()

    def _maybe_hot_reload_config(self):
        if not self._live_config:
            return
        if not self._live_config.has_changed():
            return

        # Generic hot-reload: iterate strategies that declare config sync support
        from governance.strategy_registry import StrategyRegistry
        for setup_id, handler in StrategyRegistry._strategies.items():
            if not handler.supports_config_sync:
                continue
            evaluator = handler.evaluator
            if evaluator is None:
                continue
            try:
                params = self._live_config.get_strategy_params(setup_id)
                if params:
                    changed = evaluator.update_config(params)
                    if changed:
                        evaluator.sync_config(changed)
                        tfs = handler.timeframe_affinity or []
                        self._invalidate_redis_signals(setup_id, tfs)
                        logger.info(
                            "CONFIG_RELOAD setup=%s changed=%s version=%d",
                            setup_id,
                            list(changed.keys()),
                            self._live_config._cached_version,
                        )
            except Exception as e:
                logger.error("%s hot-reload failed (keeping previous config): %s", setup_id, e)

    def _invalidate_redis_signals(self, setup_id: str, timeframes: list[str]):
        from governance.cache_contract import score_zset_key, ts_zset_key
        for tf in timeframes:
            sc_key = score_zset_key(setup_id, tf)
            ts_key = ts_zset_key(setup_id, tf)
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
                               score: float, reason: str, payload: dict,
                               trace_json):
        if not _database_url():
            return
        # Persistence gate: traceable strategies must have sealed trace
        if self._traceable_setups.get(setup_id, False):
            if trace_json is None:
                raise RuntimeError(
                    f"GOVERNANCE VIOLATION: {setup_id} is traceable but trace_json=None for {symbol}:{tf}"
                )
            if "trace_hash" not in trace_json:
                raise RuntimeError(
                    f"GOVERNANCE VIOLATION: trace_json for {setup_id}:{symbol}:{tf} not sealed (no trace_hash)"
                )
        try:
            payload_str = json.dumps(payload)
            trace_str = json.dumps(trace_json) if trace_json else None
            setup_version = self._get_setup_version(setup_id)
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE opportunities
                        SET score = %s, reason = %s, payload_json = %s,
                            setup_version = %s, trace_json = %s,
                            updated_ts = NOW()
                        WHERE symbol = %s AND timeframe = %s AND setup_id = %s
                          AND status = 'active'
                        RETURNING id;
                    """, (score, reason, payload_str, setup_version, trace_str,
                          symbol, tf, setup_id))
                    updated = cur.fetchone()

                    if not updated:
                        cur.execute("""
                            INSERT INTO opportunities
                                (symbol, timeframe, setup_id, decision, score, reason,
                                 payload_json, status, setup_version, trace_json)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s);
                        """, (symbol, tf, setup_id, 'SIGNAL', score, reason,
                              payload_str, setup_version, trace_str))
                conn.commit()
        except Exception as e:
            logger.debug(f"Postgres persist failed for {setup_id}:{symbol}:{tf}: {e}")

    def _expire_stale_pg_signals(self):
        """Safety-net garbage collector — expires opps older than 7 days.

        Normal expiry is handled by per-strategy edge re-evaluation.
        This only catches opps whose symbols left the monitored universe entirely.
        """
        SAFETY_NET_SECONDS = 7 * 86400  # 7 days
        if not _database_url():
            return
        try:
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE opportunities
                        SET status = 'expired',
                            payload_json = jsonb_set(
                                COALESCE(payload_json::jsonb, '{}'::jsonb),
                                '{edge_invalidated}',
                                %s::jsonb
                            )::text
                        WHERE status = 'active'
                          AND setup_id NOT LIKE 'EQ_%%'
                          AND updated_ts < NOW() - %s * INTERVAL '1 second';
                    """, (
                        json.dumps({"reason": "safety_net_7d", "ts": time.time()}),
                        SAFETY_NET_SECONDS,
                    ))
                conn.commit()
        except Exception as e:
            logger.debug(f"Postgres expire stale signals failed: {e}")

    # ------------------------------------------------------------------
    # Edge Re-Evaluation Helpers
    # ------------------------------------------------------------------

    _active_opps_cache: dict = {}
    _active_opps_cache_ts: float = 0.0
    _ACTIVE_OPPS_CACHE_TTL_S = 10.0  # refresh every 10s

    def _get_active_opps(self, setup_id: str) -> list[dict]:
        """Fetch all active opps for a setup (cached per cycle)."""
        now = time.time()
        if now - self._active_opps_cache_ts > self._ACTIVE_OPPS_CACHE_TTL_S:
            self._active_opps_cache = {}
            self._active_opps_cache_ts = now

        if setup_id in self._active_opps_cache:
            return self._active_opps_cache[setup_id]

        opps = []
        if not _database_url():
            return opps
        try:
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, symbol, timeframe, payload_json
                        FROM opportunities
                        WHERE status = 'active'
                          AND setup_id = %s
                          AND batch_id IS NULL
                    """, (setup_id,))
                    for row in cur.fetchall():
                        opps.append({
                            "id": row[0],
                            "symbol": row[1],
                            "timeframe": row[2],
                            "payload_json": row[3],
                        })
        except Exception as e:
            logger.debug(f"Failed to fetch active opps for {setup_id}: {e}")
        self._active_opps_cache[setup_id] = opps
        return opps

    def _expire_opp_edge_gone(self, opp_id: int, reason: str):
        """Expire an opp with edge_invalidated metadata in payload."""
        try:
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE opportunities
                        SET status = 'expired',
                            payload_json = jsonb_set(
                                COALESCE(payload_json::jsonb, '{}'::jsonb),
                                '{edge_invalidated}',
                                %s::jsonb
                            )::text
                        WHERE id = %s AND status = 'active';
                    """, (
                        json.dumps({"reason": reason, "ts": time.time()}),
                        opp_id,
                    ))
                conn.commit()
            logger.info("Edge expired opp %d: %s", opp_id, reason)
        except Exception as e:
            logger.debug(f"Failed to expire opp {opp_id}: {e}")

    def _bump_opp_alive(self, opp_id: int):
        """Bump updated_ts to keep opp alive (edge still valid)."""
        try:
            with _pool_get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE opportunities
                        SET updated_ts = NOW()
                        WHERE id = %s AND status = 'active';
                    """, (opp_id,))
                conn.commit()
        except Exception as e:
            logger.debug(f"Failed to bump opp {opp_id}: {e}")

    def _reeval_s9_edge(self, breadth: float, top_decile_symbols: set,
                        symbol_data: dict, s9_cfg, fng_value):
        """Re-evaluate edge for all active S9 opps."""
        active_opps = self._get_active_opps("S9")
        if not active_opps:
            return

        expired_count = 0
        alive_count = 0
        for opp in active_opps:
            sym = opp["symbol"]
            opp_id = opp["id"]

            # Gate 1: Breadth
            if breadth <= s9_cfg.BREADTH_THRESHOLD:
                self._expire_opp_edge_gone(opp_id, f"breadth_collapsed_{breadth:.3f}")
                expired_count += 1
                continue

            # Gate 2: FnG
            if fng_value is not None and fng_value > s9_cfg.FNG_MAX_VALUE:
                self._expire_opp_edge_gone(opp_id, f"fng_regime_{fng_value}")
                expired_count += 1
                continue

            # Gate 3: Still in top decile
            if sym not in top_decile_symbols:
                self._expire_opp_edge_gone(opp_id, "left_top_decile")
                expired_count += 1
                continue

            # Gate 4: Magnitude still holds
            sym_rets = symbol_data.get(sym)
            if sym_rets is None or sym_rets[0] < s9_cfg.MIN_RET_4H:
                self._expire_opp_edge_gone(opp_id, f"magnitude_faded_{sym_rets[0]:.4f}" if sym_rets else "symbol_data_missing")
                expired_count += 1
                continue

            # All gates pass — keep alive
            self._bump_opp_alive(opp_id)
            alive_count += 1

        if expired_count > 0 or alive_count > 0:
            logger.info("S9 edge re-eval: %d expired, %d alive", expired_count, alive_count)

    def _reeval_d1_edge(self, symbol: str, tf: str, state):
        """Re-evaluate edge for active D1 opp on this symbol/tf."""
        active_opps = self._get_active_opps("D1")
        for opp in active_opps:
            if opp["symbol"] != symbol or opp["timeframe"] != tf:
                continue

            opp_id = opp["id"]

            # Phase regression: if state machine is below TRIGGERED, edge is gone
            if state.phase.value < D1Phase.TRIGGERED.value:
                self._expire_opp_edge_gone(opp_id, f"phase_regressed_to_{state.phase.name}")
                return

            # Re-check key gates
            d1_cfg = get_config().D1
            if state.dormancy_score < d1_cfg.DORMANCY_THRESHOLD:
                self._expire_opp_edge_gone(opp_id, f"dormancy_below_{state.dormancy_score:.2f}")
                return
            if state.volume_z < d1_cfg.VOLUME_Z_SPIKE:
                self._expire_opp_edge_gone(opp_id, f"volume_z_faded_{state.volume_z:.2f}")
                return
            if state.rvol < d1_cfg.RVOL_MIN:
                self._expire_opp_edge_gone(opp_id, f"rvol_faded_{state.rvol:.2f}")
                return

            # Edge intact — bump
            self._bump_opp_alive(opp_id)
            return

    def _reeval_s10_edge(self, symbol: str, tf: str, state):
        """Re-evaluate edge for active S10 opp on this symbol/tf."""
        active_opps = self._get_active_opps("S10")
        for opp in active_opps:
            if opp["symbol"] != symbol or opp["timeframe"] != tf:
                continue

            opp_id = opp["id"]

            # Key gates: trend, momentum, breakout
            if not state.above_sma200:
                self._expire_opp_edge_gone(opp_id, "trend_lost_below_sma200")
                return
            if state.macd_histogram is not None and state.macd_histogram <= 0:
                self._expire_opp_edge_gone(opp_id, f"momentum_lost_macd_{state.macd_histogram:.6f}")
                return
            if not state.breakout:
                self._expire_opp_edge_gone(opp_id, "breakout_lost_below_flat_top")
                return

            # Edge intact
            self._bump_opp_alive(opp_id)
            return

    def _reeval_u1_edge(self, symbol: str, tf: str, u1_state, snapshot=None):
        """Re-evaluate edge for active U1 opp on this symbol/tf."""
        active_opps = self._get_active_opps("U1")
        for opp in active_opps:
            if opp["symbol"] != symbol or opp["timeframe"] != tf:
                continue

            opp_id = opp["id"]

            # Phase demotion
            if u1_state.phase not in (U1Phase.EMBRYO_ACTIVE, U1Phase.IGNITION_WATCH):
                self._expire_opp_edge_gone(opp_id, f"phase_demoted_to_{u1_state.phase.name}")
                return

            # Gate checks
            if not u1_state.gate1_passed:
                self._expire_opp_edge_gone(opp_id, "gate1_tradability_failed")
                return
            if not u1_state.gate2_passed:
                self._expire_opp_edge_gone(opp_id, "gate2_participation_failed")
                return
            if not u1_state.gate3_passed:
                self._expire_opp_edge_gone(opp_id, "gate3_compression_failed")
                return

            # Volume regime check (volume_z from snapshot if available)
            u1_cfg = get_config().U1
            if snapshot is not None and hasattr(snapshot, 'volume_z'):
                if snapshot.volume_z < u1_cfg.VOL_Z_MIN:
                    self._expire_opp_edge_gone(opp_id, f"volume_z_below_{snapshot.volume_z:.2f}")
                    return

            # Edge intact
            self._bump_opp_alive(opp_id)
            return

    # ------------------------------------------------------------------
    # S9 - Cross-Sectional Short Reversion
    # ------------------------------------------------------------------

    def _maybe_evaluate_s9(self):
        if _worker_index() != 0:
            return

        now = time.time()
        current_hour = int(now // 3600)

        if current_hour == self._s9_last_evaluated_hour:
            return

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

        Canonical universe: Binance USDT-M perp-eligible symbols only.
        The whitelist is loaded at bootstrap from S9TypedConfig.SYMBOL_WHITELIST_PATH
        and registered as a sorted tuple under ConfigRegistry.s9_symbol_whitelist.
        An empty tuple = no restriction (non-canonical — bootstrap logs a warning).
        """
        all_symbols = get_universe(self.r)
        if not all_symbols:
            return

        # Perp-only universe gate (Phase 0 — perp rerank canon).
        # Cache the frozenset on self to avoid rebuilding every scan; the
        # underlying tuple in the frozen registry never changes.
        if not hasattr(self, "_s9_whitelist_set"):
            wl_tuple = get_config().s9_symbol_whitelist
            self._s9_whitelist_set = frozenset(wl_tuple)
        s9_whitelist = self._s9_whitelist_set
        if s9_whitelist:
            pre_count = len(all_symbols)
            all_symbols = [s for s in all_symbols if s.upper() in s9_whitelist]
            self._diag["s9_whitelist_filtered"] = (
                self._diag.get("s9_whitelist_filtered", 0) + (pre_count - len(all_symbols))
            )

        symbol_data = {}
        for symbol in all_symbols:
            if self._is_symbol_excluded(symbol):
                continue
            candles = get_candles_as_ohlcv(self.r, symbol, "1h")
            # Minimum requirement: 5 bars for ret_4h. ret_24h is OPTIONAL so
            # new listings with <25h history can still participate in ranking.
            # This matches regenerate_opportunities.run_s9 (Phase 5 parity canon).
            if not candles or len(candles) < 5:
                continue
            close_now = candles[-1].close
            close_4h = candles[-5].close if len(candles) >= 5 else None
            if not (close_now and close_now > 0 and close_4h and close_4h > 0):
                continue
            # Use (close_now - close_prev) / close_prev (not close_now/close_4h - 1)
            # to match regenerate_opportunities.run_s9 bit-for-bit. The two are
            # mathematically equivalent but differ by 1 ULP in float, which
            # swaps ranks on ties (Phase 5 parity canon).
            ret_4h = (close_now - close_4h) / close_4h
            ret_24h = None
            if len(candles) >= 25:
                close_24h = candles[-25].close
                if close_24h and close_24h > 0:
                    ret_24h = (close_now - close_24h) / close_24h
            symbol_data[symbol] = (ret_4h, ret_24h, close_now)

        if len(symbol_data) < 20:
            logger.debug("S9: only %d symbols, need >= 20", len(symbol_data))
            return

        # Breadth is computed over the SUBSET of symbols that have a valid
        # ret_24h — new listings without 24h history don't vote on breadth but
        # can still be ranked. Matches regenerate_opportunities.run_s9.
        symbols_with_24h = [(s, d) for s, d in symbol_data.items() if d[1] is not None]
        if not symbols_with_24h:
            logger.debug("S9: no symbols with 24h history for breadth calc")
            return
        n_positive = sum(1 for _, (_, r24, _) in symbols_with_24h if r24 > 0)
        breadth = n_positive / len(symbols_with_24h)

        s9_cfg = get_config().S9
        # Strict less-than matches regen path (Phase 5 parity canon).
        if breadth < s9_cfg.BREADTH_THRESHOLD:
            self._diag["s9_breadth_fail"] += 1
            logger.info("S9: breadth=%.3f < %.1f, no signals",
                        breadth, s9_cfg.BREADTH_THRESHOLD)
            self._reeval_s9_edge(breadth, set(), symbol_data, s9_cfg, None)
            return

        # Fear & Greed regime gate (fail-closed)
        fng = {}
        fng_value = None
        if s9_cfg.FNG_GATE_ENABLED:
            fng = fetch_fear_greed_index()
            fng_value = fng.get("value")
            if fng_value is None:
                self._diag["s9_fng_unavailable"] += 1
                logger.warning("S9: FnG unavailable — suppressing signals (fail-closed)")
                return  # FAIL-CLOSED: suppress when we can't check
            if fng_value > s9_cfg.FNG_MAX_VALUE:
                self._diag["s9_fng_gated"] += 1
                logger.info("S9: FnG=%d (>%d) — regime gate suppressing signals",
                            fng_value, s9_cfg.FNG_MAX_VALUE)
                self._reeval_s9_edge(breadth, set(), symbol_data, s9_cfg, fng_value)
                return

        sorted_syms = sorted(symbol_data.items(), key=lambda x: x[1][0], reverse=True)
        decile_n = max(1, len(sorted_syms) // 10)
        top_decile = sorted_syms[:decile_n]

        # Magnitude gate: filter out coins with ret_4h below threshold
        min_ret = s9_cfg.MIN_RET_4H
        pre_filter_count = len(top_decile)
        top_decile = [(sym, rets) for sym, rets in top_decile if rets[0] >= min_ret]
        if not top_decile:
            self._diag["s9_magnitude_filtered"] += 1
            logger.info("S9: breadth=%.3f OK but all %d top-decile ret_4h < %.1f%% — no outliers",
                        breadth, pre_filter_count, min_ret * 100)
            return

        state = S9State(
            breadth=breadth,
            breadth_passes=True,
            universe_size=len(symbol_data),
            top_decile_threshold=top_decile[-1][1][0] if top_decile else 0.0,
            top_decile_count=decile_n,
        )

        hour_start_ms = int(time.time() // 3600) * 3600 * 1000
        top_symbols = [sym for sym, _ in top_decile]
        micro_data = {}
        try:
            kline_batch = fetch_1s_klines_batch(
                top_symbols, hour_start_ms + 1000, limit=300, max_workers=8,
            )
            for sym, klines in kline_batch.items():
                close_price = symbol_data[sym][2]
                micro_data[sym] = analyze_tape(close_price, klines)
            logger.info("S9: fetched 1s klines for %d/%d top-decile symbols",
                        len(kline_batch), len(top_symbols))
        except Exception as e:
            logger.warning("S9: 1s kline fetch failed, proceeding without: %s", e)

        bar_start_ts = str(hour_start_ms)
        count = 0

        eval_hour_iso = datetime.fromtimestamp(
            hour_start_ms / 1000, tz=timezone.utc
        ).isoformat()
        s9_cross_ctx = {
            "universe_size": len(symbol_data),
            "breadth": breadth,
            "breadth_threshold": s9_cfg.BREADTH_THRESHOLD,
            "decile_threshold_ret_4h": top_decile[-1][1][0] if top_decile else 0.0,
            "symbols_in_decile": decile_n,
            "eval_hour": eval_hour_iso,
        }

        for rank, (symbol, (ret_4h, ret_24h, close)) in enumerate(top_decile):
            # ret_24h may be None for new listings without 24h history —
            # default to 0.0 for build_signal (matches regen path).
            ret_24h_val = ret_24h if ret_24h is not None else 0.0
            signal = S9Evaluator.build_signal(
                symbol=symbol, close=close,
                ret_4h=ret_4h, ret_24h=ret_24h_val,
                breadth=breadth, rank=rank,
                universe_size=len(symbol_data),
                decile_size=decile_n, state=state,
                bar_start_ts=bar_start_ts,
            )

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

            effective_entry = signal.entry_price
            if micro_ctx and micro_ctx.entry_type != "SKIP":
                effective_entry = micro_ctx.suggested_entry_price

            payload = {
                "setup_id": "S9",
                "setup_name": self._get_setup_label("S9"),
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
                "fng_value": fng_value,
                "fng_classification": fng.get("classification"),
            }

            payload["action_by_ts"] = (
                datetime.fromtimestamp(hour_start_ms / 1000, tz=timezone.utc)
                + timedelta(hours=49)
            ).isoformat()

            final_score = signal.setup_score
            if micro_ctx and micro_ctx.entry_type == "SKIP":
                final_score = signal.setup_score * 0.5
                payload["skip_reason"] = micro_ctx.reason

            payload["setup_score"] = final_score

            if not self._try_emit_signal(symbol, "1h", "S9", final_score):
                continue

            # --- TRACE (mandatory) ---
            trace_dict = None
            trace = TraceContext(strategy="S9", symbol=symbol, timeframe="1h")
            sym_candles = get_candles_as_ohlcv(self.r, symbol, "1h")
            if sym_candles and len(sym_candles) >= 5:
                trace.record_candle_window(
                    first_ts=sym_candles[-5].timestamp,
                    last_ts=sym_candles[-1].timestamp,
                    bar_count=5, timeframe="1h",
                )
            if micro_ctx:
                trace.record_candle_window(
                    first_ts=str(hour_start_ms + 1000),
                    last_ts=str(hour_start_ms + 300000),
                    bar_count=micro_ctx.n_klines,
                    timeframe="1s", note="microstructure tape",
                )
            trace.record_indicator_params({
                "ret_4h_window": s9_cfg.RET_LOOKBACK,
                "ret_24h_window": 24,
                "breadth_threshold": s9_cfg.BREADTH_THRESHOLD,
                "decile_pct": s9_cfg.PERCENTILE / 100.0,
            })
            trace.record_indicators_bulk({
                "ret_4h": ret_4h,
                "ret_24h": ret_24h,
                "close": close,
                "rank": rank + 1,
            })
            trace.record_state({"is_hydrated": False})
            trace.record_cross_sectional_context(s9_cross_ctx)
            trace.record_gate("breadth", breadth,
                              s9_cfg.BREADTH_THRESHOLD, ">", True)
            trace.record_gate("top_decile", rank + 1, decile_n, "<=", True,
                              note="rank <= decile_size")
            trace.record_gate("ret_4h_positive", ret_4h, 0.0, ">", True)
            trace.record_gate("magnitude", ret_4h,
                              s9_cfg.MIN_RET_4H, ">=", True,
                              note=f"ret_4h={ret_4h:.4f} >= {s9_cfg.MIN_RET_4H}")
            if fng_value is not None:
                trace.record_gate(
                    "fng_regime", fng_value,
                    s9_cfg.FNG_MAX_VALUE, "<=", True,
                    note=f"FnG={fng_value} ({fng.get('classification', '?')})",
                )
            if micro_ctx:
                trace.record_microstructure({
                    "n_klines": micro_ctx.n_klines,
                    "entry_type": micro_ctx.entry_type,
                    "suggested_entry_price": micro_ctx.suggested_entry_price,
                    "drift_bps": round(micro_ctx.drift_bps, 1),
                    "still_climbing": micro_ctx.still_climbing,
                    "already_reverted": micro_ctx.already_reverted,
                })
            trace.record_score(
                {"rank_score": signal.rank_score,
                 "breadth_score": signal.breadth_score,
                 "magnitude_score": signal.magnitude_score},
                signal.setup_score, 1.0, final_score,
                promotion_threshold=0.0,
            )
            trace.record_config({
                "BREADTH_THRESHOLD": s9_cfg.BREADTH_THRESHOLD,
                "PERCENTILE": s9_cfg.PERCENTILE,
                "RET_LOOKBACK": s9_cfg.RET_LOOKBACK,
                "HOLD_BARS": s9_cfg.HOLD_BARS,
                "COST_BPS": s9_cfg.COST_BPS,
                "FNG_GATE_ENABLED": s9_cfg.FNG_GATE_ENABLED,
                "FNG_MAX_VALUE": s9_cfg.FNG_MAX_VALUE,
                "MIN_RET_4H": s9_cfg.MIN_RET_4H,
            })
            setup_version = self._get_setup_version("S9")
            trace_dict = trace.seal(setup_version)

            store_signal(self.r, symbol, "1h", "S9", payload,
                         final_score, bar_start_ts)
            reason = "; ".join(signal.explain.why_items) if signal.explain else None
            self._persist_signal_to_pg(symbol, "1h", "S9",
                                       final_score, reason, payload,
                                       trace_json=trace_dict)
            count += 1

        # Edge re-evaluation: check active opps against current cross-sectional state
        top_decile_set = set(top_symbols)
        self._reeval_s9_edge(breadth, top_decile_set, symbol_data, s9_cfg, fng_value)

        self._diag["s9_signal"] += count
        skipped = sum(1 for s in top_symbols
                      if s in micro_data and micro_data[s].entry_type == "SKIP")
        logger.info(
            "S9: breadth=%.3f, %d/%d symbols -> %d signals "
            "(%d with 1s context, %d SKIP)",
            breadth, decile_n, len(symbol_data), count,
            len(micro_data), skipped,
        )

    # ------------------------------------------------------------------
    # S9 Position Monitor
    # ------------------------------------------------------------------

    S9_MONITOR_INTERVAL_S = 300

    def _maybe_monitor_s9_positions(self):
        if _worker_index() != 0:
            return

        now = time.time()
        if now - self._s9_last_position_check < self.S9_MONITOR_INTERVAL_S:
            return

        self._s9_last_position_check = now

        if not _database_url():
            return

        try:
            self._check_s9_positions()
        except Exception as e:
            logger.error("S9 position monitor failed: %s", e, exc_info=True)

    def _check_s9_positions(self):
        """Query open S9 opportunities and check for early exit via 1s tape."""
        import requests as _req

        with _pool_get_conn() as conn:
            with conn.cursor() as cur:
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

        try:
            _ticker_base = _worker_cfg().BINANCE_TICKER_URL
            resp = _req.get(
                f"{_ticker_base}/price",
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

        positions_to_tape = []
        for pos in pos_data:
            current = price_map.get(pos["symbol"])
            if current is None:
                continue
            unrealized_pct = (pos["entry_price"] - current) / pos["entry_price"] * 100
            pos["current_price"] = current
            pos["unrealized_pct"] = unrealized_pct
            if unrealized_pct >= 1.0:
                positions_to_tape.append(pos)

        if not positions_to_tape:
            logger.debug("S9 monitor: no positions with >= 1%% unrealized profit")
            return

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - 300_000
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
                    "S9 EARLY EXIT flagged: %s unrealized=%.1f%% -- %s",
                    pos["symbol"], pos["unrealized_pct"], rec.reason,
                )
                try:
                    payload = pos["payload"]
                    payload["early_exit_flagged"] = True
                    payload["early_exit_reason"] = rec.reason
                    payload["early_exit_urgency"] = rec.urgency
                    payload["early_exit_unrealized_pct"] = round(pos["unrealized_pct"], 2)
                    payload["early_exit_ts"] = time.time()

                    with _pool_get_conn() as conn:
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
        from governance.strategy_registry import StrategyRegistry
        try:
            all_symbols = get_universe(self.r)
            timeframes = get_timeframes(self.r)

            my_symbols = [
                s for s in all_symbols
                if symbol_partition(s, _n_workers()) == _worker_index()
            ]

            recomputed = 0
            for symbol in my_symbols:
                for tf in timeframes:
                    ind_ts = b2f(self.r.hget(ind_meta_key(symbol, tf), "bar_start_ts"))
                    if ind_ts <= 0:
                        continue

                    reeval_needed = False
                    for setup_id in StrategyRegistry.active_setup_ids():
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
        from governance.strategy_registry import StrategyRegistry
        try:
            timeframes = get_timeframes(self.r)
            for setup_id in StrategyRegistry.active_setup_ids():
                for tf in timeframes:
                    prune_stale_signals(self.r, setup_id, tf)
            self._expire_stale_pg_signals()
        except Exception as e:
            logger.error(f"Prune error: {e}")


# ---------------------------------------------------------------------------
# Supervised entry point (for consolidated containers)
# ---------------------------------------------------------------------------

async def run(worker_index: int | None = None, n_workers: int | None = None):
    """Async wrapper for supervisor. Sets thread-local overrides then runs in a thread."""
    def _run_sync():
        # Set thread-local overrides (safe — each to_thread gets its own thread)
        if worker_index is not None:
            _thread_overrides.worker_index = worker_index
        if n_workers is not None:
            _thread_overrides.n_workers = n_workers
        setup_logging()
        worker = SignalWorker()
        worker.run()

    await asyncio.to_thread(_run_sync)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from governance.bootstrap import bootstrap_genesis
    bootstrap_genesis()
    setup_logging()
    worker = SignalWorker()
    worker.run()
