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
    )
    reg._register("Worker", worker)

    # --- Strategy configs ---
    reg._register("D1", D1TypedConfig())
    reg._register("U1", U1TypedConfig())
    reg._register("S9", S9TypedConfig())
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
        "S1", "S2", "S6", "S9", "S10", "U1", "D1", "D2",
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
