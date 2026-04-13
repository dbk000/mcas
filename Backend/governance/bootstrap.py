"""
MCAS Genesis Block Builder — governance/bootstrap.py
=====================================================

The ONLY place where:
    - os.getenv is called
    - Registries are populated
    - Registries are frozen
    - The runtime graph is sealed
    - The process-wide Lineage is produced

Everything downstream receives values from frozen registries.
Nothing downstream reads env, constructs registries, or resolves config.

If this module fails, the process is dead. That is correct.
"""

import os
import sys
import logging
from typing import Optional

from governance.kernel import (
    ConfigRegistry, StrategyRegistry, TaskRegistry, StopPolicyRegistry,
    RuntimeGraphSeal, Lineage, LineageEnforcer,
    install_import_guard,
)

from governance.config_registry import (
    WorkerTypedConfig,
    D1TypedConfig,
    U1TypedConfig,
    S9TypedConfig,
    S10TypedConfig,
    IndicatorTypedConfig,
    D2TypedConfig,
    TraceTypedConfig,
    AlertTypedConfig,
    PMTypedConfig,
    StrategyBuilderTypedConfig,
    MarketIntelTypedConfig,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Module-level singletons — set exactly once by bootstrap_genesis()
# TECH DEBT: target state is explicit injection via constructors / DI.
# ═══════════════════════════════════════════════════════════════════════════════

_LINEAGE: Lineage | None = None
_CONFIG: ConfigRegistry | None = None
_STRATEGY: StrategyRegistry | None = None
_TASK: TaskRegistry | None = None
_STOP_POLICY: StopPolicyRegistry | None = None


def get_lineage() -> Lineage:
    """Return the process-wide Lineage. Crashes if bootstrap has not run."""
    if _LINEAGE is None:
        print("FATAL: Lineage accessed before bootstrap_genesis()", file=sys.stderr)
        sys.exit(78)
    return _LINEAGE


def get_config() -> ConfigRegistry:
    """Return the frozen ConfigRegistry. Crashes if bootstrap has not run."""
    if _CONFIG is None:
        print("FATAL: ConfigRegistry accessed before bootstrap_genesis()", file=sys.stderr)
        sys.exit(78)
    return _CONFIG


def quote_currency() -> str:
    """Return the live-trading quote currency from the registry.
    Crashes if bootstrap hasn't run."""
    return get_config().Worker.QUOTE_CURRENCY


def macro_reference_symbol() -> str:
    """Return the macro reference symbol (BTC{QUOTE_CURRENCY}) used by
    regime detection, telemetry, and any 'what's BTC doing right now' lookup.
    Single source of truth — REFACTOR-2 sweeps any remaining BTCUSDT
    literals to use this helper."""
    return f"BTC{quote_currency()}"


def quote_currency_filter_suffix() -> str:
    """Return the quote-currency suffix used in ``symbol.endswith(...)``
    universe filters. Identity alias for ``quote_currency()`` — kept as a
    distinct name so the intent is obvious at the call site."""
    return quote_currency()


# MAJOR_BASES: canonical set of majors the codebase treats specially
# (portfolio-level gates, social-silence carve-outs, BTC-beta anchors, etc.).
# Derived symbol list = MAJOR_BASES × QUOTE_CURRENCY resolved at call time.
MAJOR_BASES: tuple[str, ...] = (
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA",
    "AVAX", "DOT", "LINK", "MATIC", "LTC", "TRX", "ATOM",
)


def major_symbols() -> tuple[str, ...]:
    """Return sorted tuple of MAJOR_BASES × current QUOTE_CURRENCY.

    Replaces the hardcoded MAJOR_SYMBOLS tuples scattered through strategy
    configs. Resolution is at call time so the list follows the live
    registry value.
    """
    q = quote_currency()
    return tuple(sorted(f"{b}{q}" for b in MAJOR_BASES))


def _parse_day_gate_env(raw: str) -> dict[str, bool]:
    """Parse ``DAY_GATE_ENABLED`` env var into ``{setup_id: bool}``.

    Format: ``"C2M:false,C4M:false,C2S:true"``. Empty string → empty dict.
    Any entry without a colon, or with a non-bool value, is ignored so a
    mis-typed env var never silently enables something unexpected.
    """
    result: dict[str, bool] = {}
    if not raw:
        return result
    for chunk in raw.split(","):
        if ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        k = k.strip().upper()
        v = v.strip().lower()
        if not k:
            continue
        if v in ("true", "1", "yes", "on"):
            result[k] = True
        elif v in ("false", "0", "no", "off"):
            result[k] = False
    return result


def day_gate_active(setup_id: str) -> bool:
    """Return True iff the historical day-gate for ``setup_id`` is still in
    effect. Reads ``WorkerTypedConfig.DAY_GATE_ENABLED``. Default (missing
    key) is ``False`` — the post-DAY_GATE_VALIDATION equilibrium."""
    cfg = get_config().Worker.DAY_GATE_ENABLED
    if not isinstance(cfg, dict):
        return False
    return bool(cfg.get(setup_id, False))


# Canonical weekday-of-gate map. Shared by backfill scripts and any live
# code that still honours a day gate (post-validation: only strategies
# whose DAY_GATE_ENABLED flag is True).
DAY_GATE_WEEKDAY: dict[str, int] = {
    "C2M": 0,   # Monday
    "C4M": 0,
    "C4ML": 0,
    "C3M": 0,
    "C2S": 5,   # Saturday
    "C2SH": 5,
}


def day_gate_weekday(setup_id: str) -> Optional[int]:
    """Return the weekday (0=Mon..6=Sun) the strategy's historical gate
    selects, or None if the strategy has no documented day gate."""
    return DAY_GATE_WEEKDAY.get(setup_id)


def get_strategy() -> StrategyRegistry:
    """Return the frozen StrategyRegistry. Crashes if bootstrap has not run."""
    if _STRATEGY is None:
        print("FATAL: StrategyRegistry accessed before bootstrap_genesis()", file=sys.stderr)
        sys.exit(78)
    return _STRATEGY


def get_task() -> TaskRegistry:
    """Return the frozen TaskRegistry. Crashes if bootstrap has not run."""
    if _TASK is None:
        print("FATAL: TaskRegistry accessed before bootstrap_genesis()", file=sys.stderr)
        sys.exit(78)
    return _TASK


def get_stop_policy() -> StopPolicyRegistry:
    """Return the frozen StopPolicyRegistry. Crashes if bootstrap has not run."""
    if _STOP_POLICY is None:
        print("FATAL: StopPolicyRegistry accessed before bootstrap_genesis()", file=sys.stderr)
        sys.exit(78)
    return _STOP_POLICY


def get_trace_config() -> TraceTypedConfig:
    """Return the frozen TraceTypedConfig. Crashes if bootstrap has not run."""
    cfg = get_config()
    return cfg.Trace


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience helpers (replace old classmethod patterns)
# ═══════════════════════════════════════════════════════════════════════════════

def strategy_as_api_dict() -> dict:
    """Return strategy catalog for API consumption. Replaces StrategyRegistry.as_api_dict()."""
    strat = get_strategy()
    return {sid: getattr(strat, sid) for sid in strat._keys}


def strategy_active_setup_ids() -> list:
    """Return list of active strategy setup IDs."""
    strat = get_strategy()
    return [sid for sid in strat._keys
            if getattr(strat, sid).get("status") == "active"]


def strategies_for_timeframe(tf: str) -> list:
    """Return (setup_id, spec) pairs whose timeframe_affinity includes tf.
    Cross-sectional strategies are excluded."""
    strat = get_strategy()
    result = []
    for sid in strat._keys:
        spec = getattr(strat, sid)
        if spec.get("dispatch_mode") == "cross_sectional":
            continue
        affinity = spec.get("timeframe_affinity")
        if affinity is None or tf in affinity:
            result.append((sid, spec))
    return result


def strategy_get_multiplier(setup_id: str) -> float:
    """Return score multiplier for a strategy.

    Active crypto strategies have explicit multiplier fields on WorkerTypedConfig.
    Direct attribute access — process crashes if the field is missing from config.
    Other strategies (legacy, equities) have a fixed multiplier of 1.0 — this is
    not a silent default on a governed value; they genuinely don't have one.
    """
    worker = get_config().Worker
    # Governed multipliers: direct attribute access, crash if missing.
    # The dict comprehension eagerly evaluates all 4 fields at call time —
    # a missing field on WorkerTypedConfig is an immediate AttributeError.
    _GOVERNED = {
        "U1": worker.U1_SCORE_MULT,
        "S9": worker.S9_SCORE_MULT,
        "S10": worker.S10_SCORE_MULT,
        "D1": worker.D1_SCORE_MULT,
    }
    if setup_id in _GOVERNED:
        return float(_GOVERNED[setup_id])
    # Non-configurable strategies (legacy, equities): fixed 1.0
    return 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# Backward compatibility — bridge during migration
# bootstrap_config() calls bootstrap_genesis() for code that still uses the
# old import path. Will be removed once all callers are migrated.
# ═══════════════════════════════════════════════════════════════════════════════

def bootstrap_config() -> None:
    """Legacy bridge: calls bootstrap_genesis(). Idempotent."""
    bootstrap_genesis()


# ═══════════════════════════════════════════════════════════════════════════════
# Populators — called during OPEN phase, before freeze
# ═══════════════════════════════════════════════════════════════════════════════

def _populate_config(reg: ConfigRegistry) -> None:
    """Read ALL environment variables and register typed configs.

    This is the ONLY function in the entire codebase that calls os.getenv().
    """
    # --- Worker / infrastructure config ---
    worker = WorkerTypedConfig(
        REDIS_URL=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        DATABASE_URL=os.getenv("DATABASE_URL", ""),
        WORKER_INDEX=int(os.getenv("WORKER_INDEX", "0")),
        N_WORKERS=int(os.getenv("N_WORKERS", "1")),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        SYMBOL_QUALITY_FILTER=os.getenv("SYMBOL_QUALITY_FILTER", "0") == "1",
        SIGNAL_TOP_N_DAILY=int(os.getenv("SIGNAL_TOP_N_DAILY", "10")),
        CAPACITY_THRESHOLD_USD=float(os.getenv("CAPACITY_THRESHOLD_USD", "1000000")),
        U1_SCORE_MULT=float(os.getenv("U1_SCORE_MULT", "1.0")),
        S9_SCORE_MULT=float(os.getenv("S9_SCORE_MULT", "1.0")),
        S10_SCORE_MULT=float(os.getenv("S10_SCORE_MULT", "1.0")),
        D1_SCORE_MULT=float(os.getenv("D1_SCORE_MULT", "1.0")),
        BINANCE_TICKER_URL=os.getenv("BINANCE_TICKER_URL", "https://api.binance.com/api/v3/ticker"),
        BINANCE_KLINES_URL=os.getenv("BINANCE_KLINES_URL", "https://api.binance.com/api/v3/klines"),
        OUTPUT_DIR=os.getenv("OUTPUT_DIR", "./output"),
        MAX_CONCURRENCY=int(os.getenv("MAX_CONCURRENCY", "8")),
        # Infrastructure env vars (migrated from scattered reads)
        DB_POOL_MIN=int(os.getenv("DB_POOL_MIN", "2")),
        DB_POOL_MAX=int(os.getenv("DB_POOL_MAX", "10")),
        DB_POOL_TIMEOUT=float(os.getenv("DB_POOL_TIMEOUT", "30.0")),
        SERVICE_NAME=os.getenv("SERVICE_NAME", "unknown"),
        ENGINE_VERSION=os.getenv("ENGINE_VERSION", "1.0.0"),
        KNOWN_UNIVERSE_SIZE=int(os.getenv("KNOWN_UNIVERSE_SIZE", "1024")),
        SETUP_IDS=os.getenv("SETUP_IDS", "S9,U1,D1"),
        SCAN_INTERVAL_SECONDS=int(os.getenv("SCAN_INTERVAL_SECONDS", "300")),
        USE_DYNAMIC_SYMBOLS=os.getenv("USE_DYNAMIC_SYMBOLS", "false").lower() == "true",
        ENABLE_PERIODIC_SCANS=os.getenv("ENABLE_PERIODIC_SCANS", "true").lower() == "true",
        ENABLE_POSITION_MONITORING=os.getenv("ENABLE_POSITION_MONITORING", "true").lower() == "true",
        DEFAULT_TIMEFRAMES=os.getenv("DEFAULT_TIMEFRAMES", "1m,5m,15m,1h,4h,1d"),
        MIN_24H_QUOTE_VOLUME_USD=float(os.getenv("MIN_24H_QUOTE_VOLUME_USD", "5000000")),
        MAX_SOCIAL_PROMOTIONS_PER_CYCLE=int(os.getenv("MAX_SOCIAL_PROMOTIONS_PER_CYCLE", "0")),
        MAX_SOCIAL_PROMOTIONS_ACTIVE=int(os.getenv("MAX_SOCIAL_PROMOTIONS_ACTIVE", "0")),
        SEED_WORKERS=int(os.getenv("SEED_WORKERS", "20")),
        BINANCE_API_V3_BASE=os.getenv("BINANCE_API_V3_BASE", "https://api.binance.com/api/v3"),
        BINANCE_API_BASE=os.getenv("BINANCE_API_BASE", "https://api.binance.com"),
        BINANCE_FAPI_URL=os.getenv("BINANCE_FAPI_URL", "https://fapi.binance.com"),
        UNICORN_PIPELINE_INTERVAL_S=int(os.getenv("UNICORN_PIPELINE_INTERVAL_S", "300")),
        BACKFILL_RATE_PER_SEC=float(os.getenv("BACKFILL_RATE_PER_SEC", "6.0")),
        CORS_ORIGINS=os.getenv("CORS_ORIGINS", ""),
        API_KEY=os.getenv("API_KEY", ""),
        # Live-trading quote currency. Default USDC matches the operator's
        # account permissions (TRD_GRP_039 restricts USDT spot trading).
        # Drives execution_worker translation + regime_detector macro symbol.
        QUOTE_CURRENCY=os.getenv("QUOTE_CURRENCY", "USDC").upper(),
        # SNAPSHOT_ENRICH (2026-04-13): per-signal quote_volume_24h_usd
        # injection from the ingestor-refreshed Redis hash.
        SNAPSHOT_VOLUME_24H_ENABLED=os.getenv(
            "SNAPSHOT_VOLUME_24H_ENABLED", "true").lower() == "true",
        # DAY_GATE_VALIDATION (2026-04-13): per-strategy day-gate map.
        # Parse from "SETUP_ID:true,SETUP_ID:false" format. Missing
        # keys = gate bypassed.
        DAY_GATE_ENABLED=_parse_day_gate_env(os.getenv("DAY_GATE_ENABLED", "")),
    )
    reg._register("Worker", worker)

    # REFACTOR-2: sync QUOTE_CURRENCY into the core_logic strategy
    # classes. These modules cannot import governance (pure-math layer),
    # so bootstrap patches the class attr directly once the registry is
    # populated. Import lazily so a failed core_logic import can't brick
    # registry bootstrap.
    try:
        from core_logic.d1_dormancy_ignition import D1Config as _D1Config
        _D1Config.QUOTE_CURRENCY = worker.QUOTE_CURRENCY
    except Exception as _exc:  # pragma: no cover
        logger.warning("D1Config QUOTE_CURRENCY sync failed: %s", _exc)
    try:
        from core_logic.u1_unicorn_ignition import U1Config as _U1Config
        _U1Config.QUOTE_CURRENCY = worker.QUOTE_CURRENCY
    except Exception as _exc:  # pragma: no cover
        logger.warning("U1Config QUOTE_CURRENCY sync failed: %s", _exc)

    # --- Strategy configs ---
    reg._register("D1", D1TypedConfig(
        DORMANCY_THRESHOLD=float(os.getenv("D1_DORMANCY_THRESHOLD", "0.75")),
        BB_WIDTH_COMPRESS=float(os.getenv("D1_BB_WIDTH_COMPRESS", "0.05")),
        VOLUME_Z_SPIKE=float(os.getenv("D1_VOLUME_Z_SPIKE", "2.0")),
        RVOL_MIN=float(os.getenv("D1_RVOL_MIN", "3.0")),
        STOP_PCT=float(os.getenv("D1_STOP_PCT", "0.015")),
        TP1_PCT=float(os.getenv("D1_TP1_PCT", "0.10")),
        TP2_PCT=float(os.getenv("D1_TP2_PCT", "0.20")),
        TIME_STOP_BARS_1H=int(os.getenv("D1_TIME_STOP_BARS_1H", "72")),
        SOCIAL_SILENCE_MAX_MENTIONS=int(os.getenv("D1_SOCIAL_SILENCE_MAX_MENTIONS", "3")),
        ONCHAIN_GATE_ENABLED=os.getenv("D1_ONCHAIN_GATE_ENABLED", "false").lower() == "true",
        W_DORMANCY=float(os.getenv("D1_W_DORMANCY", "0.20")),
        W_VOL_Z=float(os.getenv("D1_W_VOL_Z", "0.25")),
        W_ACCUM_DIV=float(os.getenv("D1_W_ACCUM_DIV", "0.15")),
        W_BB_EXPANSION=float(os.getenv("D1_W_BB_EXPANSION", "0.15")),
        W_RVOL=float(os.getenv("D1_W_RVOL", "0.05")),
        W_BTC_ADJ_RET=float(os.getenv("D1_W_BTC_ADJ_RET", "0.10")),
        W_SOCIAL_SILENCE=float(os.getenv("D1_W_SOCIAL_SILENCE", "0.10")),
        W_ONCHAIN_FLOW=float(os.getenv("D1_W_ONCHAIN_FLOW", "0.0")),
    ))
    reg._register("U1", U1TypedConfig(
        IGNITION_SCORE_THRESHOLD=float(os.getenv("U1_IGNITION_SCORE_THRESHOLD", "0.40")),
        VOL_Z_MIN=float(os.getenv("U1_VOL_Z_MIN", "1.5")),
        TRADABILITY_IMPROVEMENT_FACTOR=float(os.getenv("U1_TRADABILITY_IMPROVEMENT_FACTOR", "0.85")),
        PARTICIPATION_GROWTH_FACTOR=float(os.getenv("U1_PARTICIPATION_GROWTH_FACTOR", "1.2")),
        RANGE_PCT_THRESHOLD=float(os.getenv("U1_RANGE_PCT_THRESHOLD", "0.10")),
        REL_VOL_IGNITION_MIN=float(os.getenv("U1_REL_VOL_IGNITION_MIN", "3.0")),
        STOP_RANGE_MULT=float(os.getenv("U1_STOP_RANGE_MULT", "3.0")),
        MIN_STOP_PCT=float(os.getenv("U1_MIN_STOP_PCT", "0.005")),
        TP1_R=float(os.getenv("U1_TP1_R", "1.0")),
        TP2_R=float(os.getenv("U1_TP2_R", "2.0")),
        TIME_STOP_BARS_5M=int(os.getenv("U1_TIME_STOP_BARS_5M", "12")),
        MAX_EMBRYO_ACTIVE=int(os.getenv("U1_MAX_EMBRYO_ACTIVE", "200")),
        HYPE_REL_VOL_CAP=float(os.getenv("U1_HYPE_REL_VOL_CAP", "20.0")),
        HYPE_RANGE_CAP=float(os.getenv("U1_HYPE_RANGE_CAP", "0.50")),
        MAX_MIN_NOTIONAL=int(os.getenv("U1_MAX_MIN_NOTIONAL", "100")),
        MIN_VOLUME_USD=float(os.getenv("U1_MIN_VOLUME_USD", "75000")),
        MAX_VOLUME_USD=float(os.getenv("U1_MAX_VOLUME_USD", "3000000")),
        GATE_TIMEFRAME=os.getenv("U1_GATE_TIMEFRAME", "5m"),
        GATE_WINDOW_BARS=int(os.getenv("U1_GATE_WINDOW_BARS", "48")),
    ))
    s9_cfg = S9TypedConfig(
        RET_LOOKBACK=int(os.getenv("S9_RET_LOOKBACK", "4")),
        HOLD_BARS=int(os.getenv("S9_HOLD_BARS", "48")),
        PERCENTILE=int(os.getenv("S9_PERCENTILE", "10")),
        BREADTH_THRESHOLD=float(os.getenv("S9_BREADTH_THRESHOLD", "0.3")),
        COST_BPS=int(os.getenv("S9_COST_BPS", "18")),
        MIN_RET_4H=float(os.getenv("S9_MIN_RET_4H", "0.05")),
        FNG_GATE_ENABLED=os.getenv("S9_FNG_GATE_ENABLED", "true").lower() == "true",
        FNG_MAX_VALUE=int(os.getenv("S9_FNG_MAX_VALUE", "50")),
        MAX_CONCURRENT=int(os.getenv("S9_MAX_CONCURRENT", "15")),
        SYMBOL_WHITELIST_PATH=os.getenv(
            "S9_SYMBOL_WHITELIST_PATH",
            "/backend/config/s9_symbol_whitelist.txt",
        ),
    )
    reg._register("S9", s9_cfg)

    # Load S9 symbol whitelist (perp-only universe). Stored as a sorted tuple
    # so the registry seal is deterministic across processes (frozenset/set
    # ordering is non-deterministic under hash randomization). Consumers
    # convert to frozenset at read time for O(1) membership. Fail-open: if
    # the file is missing, register an empty tuple — signal_worker treats
    # this as "no restriction" (full universe) and logs a warning.
    s9_whitelist_sorted: tuple = ()
    wl_path = s9_cfg.SYMBOL_WHITELIST_PATH
    candidate_paths = [wl_path]
    if not os.path.isabs(wl_path):
        candidate_paths.append(os.path.abspath(wl_path))
    else:
        host_fallback = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "binance_perp_symbols.txt",
        )
        candidate_paths.append(host_fallback)
    for path in candidate_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    s9_whitelist_sorted = tuple(sorted({
                        line.strip().upper() for line in f if line.strip()
                    }))
                print(
                    f"[BOOTSTRAP] S9 symbol whitelist loaded: {len(s9_whitelist_sorted)} symbols from {path}",
                    file=sys.stderr,
                )
                break
            except Exception as e:
                print(
                    f"[BOOTSTRAP] WARNING: failed to read S9 whitelist at {path}: {e}",
                    file=sys.stderr,
                )
    if not s9_whitelist_sorted:
        print(
            f"[BOOTSTRAP] WARNING: S9 symbol whitelist empty (searched {candidate_paths}) — "
            "signal_worker will use full universe (non-canonical)",
            file=sys.stderr,
        )
    reg._register("s9_symbol_whitelist", s9_whitelist_sorted)

    reg._register("S10", S10TypedConfig())

    # --- Indicator config ---
    reg._register("Indicator", IndicatorTypedConfig(
        COMPUTE_DARK_INDICATORS=os.getenv("COMPUTE_DARK_INDICATORS", "0") == "1",
    ))

    # --- Trace config ---
    reg._register("Trace", TraceTypedConfig())

    # --- D2 config ---
    reg._register("D2", D2TypedConfig(
        entry_threshold=float(os.getenv("D2_ENTRY_THRESHOLD", "0.25")),
        stop_pct=float(os.getenv("D2_STOP_PCT", "0.15")),
        time_stop_hours=int(os.getenv("D2_TIME_STOP_HOURS", "72")),
        max_watch_hours=int(os.getenv("D2_MAX_WATCH_HOURS", "48")),
        mania_gate_pct=float(os.getenv("D2_MANIA_GATE_PCT", "40.0")),
        liquidity_gate_usd=float(os.getenv("D2_LIQUIDITY_GATE_USD", "1000000")),
        effective_risk_pct=float(os.getenv("D2_EFFECTIVE_RISK_PCT", "18.76")),
        funding_gate_enabled=os.getenv("D2_FUNDING_GATE", "").strip().lower() in {"1", "true", "yes", "on"},
        conflict_gate_enabled=os.getenv("D2_CONFLICT_GATE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
        fng_soft_gate=os.getenv("D2_FNG_SOFT_GATE", "log_only"),
        candle_interval=os.getenv("D2_CANDLE_INTERVAL", "1m"),
        poll_interval_s=int(os.getenv("D2_POLL_INTERVAL_S", "60")),
        cluster_poll_s=int(os.getenv("D2_CLUSTER_POLL_S", "30")),
        max_concurrent=int(os.getenv("D2_MAX_CONCURRENT", "3")),
        risk_per_trade_pct=float(os.getenv("D2_RISK_PER_TRADE_PCT", "2.0")),
        account_size_usd=float(os.getenv("D2_ACCOUNT_SIZE_USD", "10000")),
    ))

    # --- Alert watchdog config ---
    reg._register("Alert", AlertTypedConfig(
        SIGNAL_STALE_HOURS=int(os.getenv("ALERT_SIGNAL_STALE_HOURS", "6")),
        WEBHOOK_URL=os.getenv("ALERT_WEBHOOK_URL", ""),
    ))

    # --- Polymarket config ---
    pm_cfg = PMTypedConfig(
        groq_api_key=os.getenv("PM_GROQ_API_KEY", ""),
        gemini_api_key=os.getenv("PM_GEMINI_API_KEY", ""),
        min_volume_24h=float(os.getenv("PM_MIN_VOLUME_24H", "50000")),
        min_liquidity=float(os.getenv("PM_MIN_LIQUIDITY", "10000")),
        scan_interval_s=int(os.getenv("PM_SCAN_INTERVAL_S", "3600")),
        min_executable_edge_pct=float(os.getenv("PM_MIN_EXECUTABLE_EDGE_PCT", "8.0")),
        bankroll_usd=float(os.getenv("PM_BANKROLL_USD", "10000")),
        kelly_fraction=float(os.getenv("PM_KELLY_FRACTION", "0.25")),
        paper_mode=os.getenv("PM_PAPER_MODE", "true").lower() in {"1", "true", "yes"},
    )
    if not pm_cfg.paper_mode:
        if not pm_cfg.groq_api_key or not pm_cfg.gemini_api_key:
            raise RuntimeError("PM_GROQ_API_KEY and PM_GEMINI_API_KEY required when PM_PAPER_MODE=false")
    reg._register("PM", pm_cfg)

    # --- Strategy Builder config ---
    reg._register("StrategyBuilder", StrategyBuilderTypedConfig(
        max_strategies_per_user=int(os.getenv("SB_MAX_STRATEGIES_PER_USER", "10")),
        max_conditions_per_strategy=int(os.getenv("SB_MAX_CONDITIONS_PER_STRATEGY", "8")),
        strategy_cache_refresh_s=int(os.getenv("SB_STRATEGY_CACHE_REFRESH_S", "60")),
        cooldown_floor_minutes=int(os.getenv("SB_COOLDOWN_FLOOR_MINUTES", "5")),
        cooldown_default_minutes=int(os.getenv("SB_COOLDOWN_DEFAULT_MINUTES", "60")),
        evaluation_log_interval_s=int(os.getenv("SB_EVALUATION_LOG_INTERVAL_S", "300")),
        max_fire_rate_pct=float(os.getenv("SB_MAX_FIRE_RATE_PCT", "50.0")),
        max_hits_per_day_per_user=int(os.getenv("SB_MAX_HITS_PER_DAY_PER_USER", "5000")),
    ))

    # --- Market Intelligence surface config ---
    reg._register("MarketIntel", MarketIntelTypedConfig(
        derivatives_min_completeness=float(os.getenv("MI_DERIVATIVES_MIN_COMPLETENESS", "0.5")),
        heatmap_staleness_multiplier=float(os.getenv("MI_HEATMAP_STALENESS_MULT", "2.5")),
        seasonality_default_years=int(os.getenv("MI_SEASONALITY_DEFAULT_YEARS", "3")),
        seasonality_min_months=int(os.getenv("MI_SEASONALITY_MIN_MONTHS", "24")),
        fng_cache_ttl_s=int(os.getenv("MI_FNG_CACHE_TTL_S", "3600")),
    ))


def _populate_strategies(reg: StrategyRegistry) -> None:
    """Register canonical strategy definitions (metadata only).

    Runtime handlers (evaluators, dispatch callables) are built by the
    consuming worker from frozen config — NOT stored in the sealed registry.
    """
    _CATALOG = {
        "S1": {"label": "S1 (BB Squeeze)", "type": "crypto", "status": "legacy"},
        "S2": {"label": "S2 (Golden Cross)", "type": "crypto", "status": "legacy"},
        "S6": {"label": "S6 (Trend Pullback)", "type": "crypto", "status": "legacy"},
        "S9": {
            "label": "S9 (Short Reversion)", "type": "crypto", "status": "active",
            "timeframe_affinity": None, "dispatch_mode": "cross_sectional",
            "traceable": True,
        },
        "S10": {
            "label": "S10 (MACD Scalp)", "type": "crypto", "status": "active",
            "timeframe_affinity": ["5m"], "dispatch_mode": "per_symbol",
            "traceable": True,
        },
        "S11": {
            "label": "S11 (MACD Long Reversion)", "type": "crypto", "status": "active",
            "timeframe_affinity": ["1h"], "dispatch_mode": "per_symbol",
            "traceable": True,
        },
        "U1": {
            "label": "U1 (Unicorn)", "type": "crypto", "status": "active",
            "timeframe_affinity": ["5m"], "dispatch_mode": "per_symbol",
            "traceable": True,
        },
        "D1": {
            "label": "D1 (Dormancy Ignition)", "type": "crypto", "status": "active",
            "timeframe_affinity": ["1h"], "dispatch_mode": "per_symbol",
            "traceable": True,
        },
        "D2": {"label": "D2 (Fade the Casino)", "type": "crypto", "status": "paper"},
        "PM": {"label": "PM (Polymarket)", "type": "prediction", "status": "paper", "dispatch_mode": "standalone"},
        "EQFP": {"label": "EQ: Forward Predictor", "type": "equities", "status": "active"},
        "EQ_TSMOM": {"label": "EQ: TSMOM (20d)", "type": "equities", "status": "active"},
        "EQ_XSMOM": {"label": "EQ: XS Momentum (20d)", "type": "equities", "status": "active"},
        "EQ_REV1A": {"label": "EQ: Reversion 1a (10d)", "type": "equities", "status": "active"},
        "EQ_REV1B": {"label": "EQ: Reversion 1b (10d)", "type": "equities", "status": "active"},
        "EQ_VPD": {"label": "EQ: Vol-Price Div (1d)", "type": "equities", "status": "active"},
        "EQ_ATC": {"label": "EQ: After-Trading Chatter (1d)", "type": "equities", "status": "active"},
        "EQ_NVDA_MACRO": {"label": "EQ: NVDA Macro Cascade (3d)", "type": "equities", "status": "active"},
    }
    for setup_id, spec in _CATALOG.items():
        reg._register(setup_id, spec)


def _populate_tasks(reg: TaskRegistry) -> None:
    """Register canonical task definitions.

    Tasks are metadata entries. Actual handler callables are bound at runtime
    by the worker that executes them.
    """
    _TASKS = {
        "source_reliability": {"name": "source_reliability", "runtime_family": "cc"},
        "indicator_correlation": {"name": "indicator_correlation", "runtime_family": "cc"},
        "regime_suitability": {"name": "regime_suitability", "runtime_family": "cc"},
        "equities_forward_predictor": {"name": "equities_forward_predictor", "runtime_family": "equities"},
    }
    for task_id, spec in _TASKS.items():
        reg._register(task_id, spec)


def _populate_stop_policies(reg: StopPolicyRegistry) -> None:
    """Register canonical stop-policy defaults."""
    from core_logic.exit_checker import ExitConfig

    # Derive setup IDs from the strategy catalog (which is already populated
    # but not yet frozen at this point — we use the hardcoded catalog directly).
    _SETUP_IDS = sorted([
        "S1", "S2", "S6", "S9", "S10", "S11", "U1", "D1", "D2", "PM",
        # C-series (Monday 2026-04-13 sprint — parents + live variants).
        "C1", "C2", "C3", "C4", "C5", "C2M", "C2S", "C2SH", "C3M", "C4M", "C4ML",
        "EQFP", "EQ_TSMOM", "EQ_XSMOM", "EQ_REV1A", "EQ_REV1B",
        "EQ_VPD", "EQ_ATC", "EQ_NVDA_MACRO",
    ])

    reg._register("canonical_defaults", {
        "schema_version": "v1",
        "sl_r": 1.0,
        "stop_distance_factors": {sid: 1.0 for sid in _SETUP_IDS},
        "intrabar_policy": "STOP_FIRST",
        "one_event_per_bar": True,
    })

    reg._register("schema_version", "v1")
    reg._register("known_setup_ids", _SETUP_IDS)

    # Per-setup exit configs — canonical exit parameters per strategy.
    # The simulator reads these via SimulationConfig.setup_exit_configs.
    reg._register("setup_exit_configs", {
        "D1": ExitConfig(
            fail_fast_bars=0, fail_fast_threshold=0.0,
            trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
        ),
        "S9": ExitConfig(
            fail_fast_bars=0, fail_fast_threshold=0.0,
            trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
        ),
        "S10": ExitConfig(
            fail_fast_bars=2, fail_fast_threshold=0.003,
            trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
        ),
        "S11": ExitConfig(
            # S11 is stop-or-time-stop only (validated wide-stop reversion).
            # No fail-fast, no trail, no TPs (payload carries tp1/tp2=None).
            fail_fast_bars=0, fail_fast_threshold=0.0,
            trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
        ),
        "U1": ExitConfig(
            fail_fast_bars=0, fail_fast_threshold=0.0,
            trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
        ),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Post-freeze Validators
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_trace_governance(
    strategy_reg: StrategyRegistry,
) -> None:
    """Verify trace governance invariants after registries are frozen.

    Every strategy marked traceable=True must have status "active".
    If any violation is found, the process dies — this is a governance
    misconfiguration that cannot be resolved at runtime.
    """
    violations = []
    for sid in strategy_reg._keys:
        spec = getattr(strategy_reg, sid)
        if spec.get("traceable") and spec.get("status") != "active":
            violations.append(
                f"  {sid}: traceable=True but status={spec.get('status')!r}"
            )
    if violations:
        print(
            "FATAL: Trace governance validation failed:\n"
            + "\n".join(violations),
            file=sys.stderr,
        )
        sys.exit(78)


# ═══════════════════════════════════════════════════════════════════════════════
# Genesis Block Builder
# ═══════════════════════════════════════════════════════════════════════════════

def bootstrap_genesis() -> Lineage:
    """Build the genesis block. Call once at process start. Idempotent.

    Returns the Lineage for this process lifetime.
    """
    global _LINEAGE, _CONFIG, _STRATEGY, _TASK, _STOP_POLICY

    if _LINEAGE is not None:
        return _LINEAGE

    print("[BOOTSTRAP] Building genesis block...", file=sys.stderr)

    # --- Phase 1: Install import guard ---
    install_import_guard()

    # --- Phase 2: Create registries (OPEN) ---
    config_reg = ConfigRegistry("config")
    strategy_reg = StrategyRegistry("strategy")
    task_reg = TaskRegistry("task")
    stop_policy_reg = StopPolicyRegistry("stop_policy")

    # --- Phase 3: Populate ---
    _populate_config(config_reg)
    _populate_strategies(strategy_reg)
    _populate_tasks(task_reg)
    _populate_stop_policies(stop_policy_reg)

    # --- Phase 4: Freeze ---
    config_seal = config_reg._freeze()
    strategy_seal = strategy_reg._freeze()
    task_seal = task_reg._freeze()
    stop_seal = stop_policy_reg._freeze()

    print(f"[BOOTSTRAP] Config seal    : {config_seal[:12]}...", file=sys.stderr)
    print(f"[BOOTSTRAP] Strategy seal  : {strategy_seal[:12]}...", file=sys.stderr)
    print(f"[BOOTSTRAP] Task seal      : {task_seal[:12]}...", file=sys.stderr)
    print(f"[BOOTSTRAP] StopPolicy seal: {stop_seal[:12]}...", file=sys.stderr)

    # --- Phase 5: Seal runtime graph ---
    graph = RuntimeGraphSeal()
    graph.include(config_reg)
    graph.include(strategy_reg)
    graph.include(task_reg)
    graph.include(stop_policy_reg)
    lineage = graph.seal()

    # --- Phase 5b: Post-freeze validation ---
    _validate_trace_governance(strategy_reg)

    # --- Phase 6: Store singletons ---
    _LINEAGE = lineage
    _CONFIG = config_reg
    _STRATEGY = strategy_reg
    _TASK = task_reg
    _STOP_POLICY = stop_policy_reg

    print(
        f"[BOOTSTRAP] Runtime graph sealed: {lineage.runtime_graph_seal[:12]}...",
        file=sys.stderr,
    )
    print(f"[BOOTSTRAP] Process ID: {lineage.process_id}", file=sys.stderr)
    print("[BOOTSTRAP] Genesis block complete.", file=sys.stderr)

    return lineage


def sync_config_hashes():
    """Compute config hashes for each active crypto strategy and sync to DB.

    Uses the same deterministic JSON + SHA-256[:16] as TraceContext.record_config().
    Call after bootstrap_genesis() when DB is available.
    """
    import hashlib
    import json
    import psycopg

    def _det_json(d: dict) -> str:
        return json.dumps(d, sort_keys=True, separators=(',', ':'))

    def _config_hash(d: dict) -> str:
        return hashlib.sha256(_det_json(d).encode('utf-8')).hexdigest()[:16]

    cfg = get_config()

    hashes = {}

    # S9
    s9 = cfg.S9
    hashes["S9"] = _config_hash({
        "BREADTH_THRESHOLD": s9.BREADTH_THRESHOLD,
        "PERCENTILE": s9.PERCENTILE,
        "RET_LOOKBACK": s9.RET_LOOKBACK,
        "HOLD_BARS": s9.HOLD_BARS,
        "COST_BPS": s9.COST_BPS,
        "FNG_GATE_ENABLED": s9.FNG_GATE_ENABLED,
        "FNG_MAX_VALUE": s9.FNG_MAX_VALUE,
        "MIN_RET_4H": s9.MIN_RET_4H,
    })

    # D1
    d1 = cfg.D1
    hashes["D1"] = _config_hash({
        "DORMANCY_THRESHOLD": d1.DORMANCY_THRESHOLD,
        "BB_WIDTH_COMPRESS": d1.BB_WIDTH_COMPRESS,
        "VOLUME_Z_SPIKE": d1.VOLUME_Z_SPIKE,
        "RVOL_MIN": d1.RVOL_MIN,
        "STOP_PCT": d1.STOP_PCT,
        "TP1_PCT": d1.TP1_PCT,
        "TP2_PCT": d1.TP2_PCT,
        "TIME_STOP_BARS_1H": d1.TIME_STOP_BARS_1H,
        "SOCIAL_SILENCE_MAX_MENTIONS": d1.SOCIAL_SILENCE_MAX_MENTIONS,
        "ONCHAIN_GATE_ENABLED": d1.ONCHAIN_GATE_ENABLED,
        "W_ONCHAIN_FLOW": d1.W_ONCHAIN_FLOW,
    })

    # U1
    u1 = cfg.U1
    hashes["U1"] = _config_hash({
        "TRADABILITY_IMPROVEMENT_FACTOR": u1.TRADABILITY_IMPROVEMENT_FACTOR,
        "PARTICIPATION_GROWTH_FACTOR": u1.PARTICIPATION_GROWTH_FACTOR,
        "RANGE_PCT_THRESHOLD": u1.RANGE_PCT_THRESHOLD,
        "REL_VOL_IGNITION_MIN": u1.REL_VOL_IGNITION_MIN,
        "VOL_Z_MIN": u1.VOL_Z_MIN,
        "IGNITION_SCORE_THRESHOLD": u1.IGNITION_SCORE_THRESHOLD,
        "STOP_RANGE_MULT": u1.STOP_RANGE_MULT,
        "MIN_STOP_PCT": u1.MIN_STOP_PCT,
        "TP1_R": u1.TP1_R,
        "TP2_R": u1.TP2_R,
        "TIME_STOP_BARS_5M": u1.TIME_STOP_BARS_5M,
    })

    # S10 — no typed config params, use empty dict for now
    hashes["S10"] = _config_hash({})

    # S11 — locked parameters are evaluator class constants (no typed config
    # registry entry yet), so hash the locked values directly. Any future
    # hot-reload of S11 thresholds must update this dict in lockstep.
    from core_logic.s11_macd_long_reversion import S11Evaluator as _S11Ev
    hashes["S11"] = _config_hash({
        "MACD_FAST": _S11Ev.MACD_FAST,
        "MACD_SLOW": _S11Ev.MACD_SLOW,
        "MACD_SIGNAL": _S11Ev.MACD_SIGNAL,
        "SMA50_FILTER_THRESHOLD": _S11Ev.SMA50_FILTER_THRESHOLD,
        "SMA200_FILTER_THRESHOLD": _S11Ev.SMA200_FILTER_THRESHOLD,
        "STOP_ATR_MULT": _S11Ev.STOP_ATR_MULT,
        "TIME_STOP_BARS": _S11Ev.TIME_STOP_BARS,
    })

    # Write to DB
    db_url = cfg.Worker.DATABASE_URL
    if not db_url:
        logger.warning("sync_config_hashes: no DATABASE_URL, skipping")
        return hashes

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                for setup_id, config_hash in hashes.items():
                    cur.execute("""
                        UPDATE config_active_strategies
                        SET current_config_hash = %s
                        WHERE setup_id = %s;
                    """, (config_hash, setup_id))
            conn.commit()
        logger.info("Config hashes synced: %s", hashes)
    except Exception as e:
        logger.warning("sync_config_hashes failed: %s", e)

    return hashes
