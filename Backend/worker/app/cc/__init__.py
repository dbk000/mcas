"""
Champion/Challenger Framework
=============================

Continuous, guardrailed improvement of MCAS trading strategies.

Key components:
- models: Dataclasses and enums for all CC entities
- repository: Database access layer for CC tables
- registry: Variant creation with one-diff and cadence enforcement
- evaluator: Parallel 3-way evaluation (baseline + champion + challenger)
- simulator: Shadow trade simulation using paper trading execution model
- promotion: Promotion gate checks and execution
- diagnostics: Trade classification and next-tweak recommendations

Usage:
    from cc import CCRepository, VariantRegistry
    from cc.models import Variant, ExperimentRun

    repo = CCRepository(database_url)
    registry = VariantRegistry(repo)

    # Create baseline and champion
    baseline = registry.create_baseline("S1", modules)
    champion = registry.create_champion("S1", modules)

    # Create challenger with one module different
    challenger = registry.create_challenger_from_champion(
        champion.variant_id,
        diff_type=DiffType.PARAM_BUNDLE,
        diff_module_kind=ModuleKind.EXIT,
        diff_key="EXIT.trailing",
        new_params={"trail_atr_mult": 2.5},
    )

Canon compliance:
- One-module-different rule: Challenger differs by exactly one module
- Weekly cadence: Max 1 change per week per setup
- Baseline always runs: Dead-simple variant always evaluated for reference
"""

from .models import (
    # Enums
    VariantStatus,
    ModuleKind,
    DiffType,
    ExperimentMode,
    ExperimentStatus,
    ExecutionContext,
    Direction,
    FailureBucket,
    WinnerBucket,

    # Core models
    Module,
    VariantModule,
    Variant,
    CachedVariant,
    ExperimentRun,
    ShadowTrade,
    ShadowPosition,
    ExitResult,
    PendingSignal,
    RunMetrics,
    AssetMetrics,
    PromotionConfig,
    GateResult,
    PromotionResult,
    TradeDiagnostic,
    NextTweakRecommendation,
    CCEvaluatorCacheEntry,
    ChangeLedgerEntry,

    # Exceptions
    CCError,
    CadenceViolation,
    ActiveChallengerExists,
    ActiveRunExists,
    OneModuleDifferentViolation,
    PromotionDenied,
    PromotionRaceError,
    NoBaselineError,
    InvalidIdentifierError,

    # Constants
    CC_STATE_TTL_S,
    CC_VARIANT_CACHE_TTL_S,
    MAX_PENDING_BARS,
    HEARTBEAT_INTERVAL_HOURS,
    CACHE_CLEANUP_INTERVAL_SECONDS,
    CACHE_SIZE_ALERT_THRESHOLD,
    FORBIDDEN_ID_CHARS,
    MODULE_PRIORITY_ORDER,
    LOSER_BUCKET_TO_MODULE,
    WINNER_BUCKET_TO_MODULE,
    CADENCE_SECONDS,
)

from .repository import CCRepository

from .registry import (
    VariantRegistry,
    validate_identifier,
    validate_all_identifiers,
    count_module_differences,
    create_initial_modules_from_setup,
)

from .evaluator import (
    VariantStateBackend,
    ParallelEvaluator,
    CCEvaluatorCache,
    get_evaluator_cache,
    cc_active_run_key,
    cc_variant_state_key,
    cc_shadow_position_key,
    cc_pending_signal_key,
    cc_champion_key,
    cc_baseline_key,
    cc_variant_cache_key,
)

from .simulator import (
    ShadowTradeSimulator,
    apply_slippage,
    calculate_fees,
    get_time_stop_bars,
    assert_shadow_safe,
)

from .promotion import (
    check_promotion_gates,
    execute_promotion,
    can_promote,
    safe_pct,
    safe_ratio,
    safe_improvement_pct,
)

from .diagnostics import (
    classify_failure,
    classify_winner,
    classify_trade,
    get_next_tweak_recommendation,
    get_diagnostics_summary,
    auto_classify_trades,
)

__all__ = [
    # Enums
    "VariantStatus",
    "ModuleKind",
    "DiffType",
    "ExperimentMode",
    "ExperimentStatus",
    "ExecutionContext",
    "Direction",
    "FailureBucket",
    "WinnerBucket",

    # Core models
    "Module",
    "VariantModule",
    "Variant",
    "CachedVariant",
    "ExperimentRun",
    "ShadowTrade",
    "ShadowPosition",
    "ExitResult",
    "PendingSignal",
    "RunMetrics",
    "AssetMetrics",
    "PromotionConfig",
    "GateResult",
    "PromotionResult",
    "TradeDiagnostic",
    "NextTweakRecommendation",
    "CCEvaluatorCacheEntry",
    "ChangeLedgerEntry",

    # Exceptions
    "CCError",
    "CadenceViolation",
    "ActiveChallengerExists",
    "ActiveRunExists",
    "OneModuleDifferentViolation",
    "PromotionDenied",
    "PromotionRaceError",
    "NoBaselineError",
    "InvalidIdentifierError",

    # Constants
    "CC_STATE_TTL_S",
    "CC_VARIANT_CACHE_TTL_S",
    "MAX_PENDING_BARS",
    "HEARTBEAT_INTERVAL_HOURS",
    "CACHE_CLEANUP_INTERVAL_SECONDS",
    "CACHE_SIZE_ALERT_THRESHOLD",
    "FORBIDDEN_ID_CHARS",
    "MODULE_PRIORITY_ORDER",
    "LOSER_BUCKET_TO_MODULE",
    "WINNER_BUCKET_TO_MODULE",
    "CADENCE_SECONDS",

    # Repository
    "CCRepository",

    # Registry
    "VariantRegistry",
    "validate_identifier",
    "validate_all_identifiers",
    "count_module_differences",
    "create_initial_modules_from_setup",

    # Evaluator
    "VariantStateBackend",
    "ParallelEvaluator",
    "CCEvaluatorCache",
    "get_evaluator_cache",
    "cc_active_run_key",
    "cc_variant_state_key",
    "cc_shadow_position_key",
    "cc_pending_signal_key",
    "cc_champion_key",
    "cc_baseline_key",
    "cc_variant_cache_key",

    # Simulator
    "ShadowTradeSimulator",
    "apply_slippage",
    "calculate_fees",
    "get_time_stop_bars",
    "assert_shadow_safe",

    # Promotion
    "check_promotion_gates",
    "execute_promotion",
    "can_promote",
    "safe_pct",
    "safe_ratio",
    "safe_improvement_pct",

    # Diagnostics
    "classify_failure",
    "classify_winner",
    "classify_trade",
    "get_next_tweak_recommendation",
    "get_diagnostics_summary",
    "auto_classify_trades",
]
