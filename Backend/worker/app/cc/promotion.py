"""
Champion/Challenger Framework - Promotion
==========================================

Promotion gate checks and execution.

Gate definitions:
1. Expectancy improves (challenger > champion)
2. Drawdown within tolerance (challenger DD <= champion DD + tolerance)
3. Multi-asset generalization (improvement on >= X assets with >= N trades each)

Canon compliance:
- All gates must pass for promotion
- Insufficient sample size explicitly fails (not crash)
- Promotion execution is atomic with proper locking
"""

import logging
import math
from datetime import datetime, timezone
from statistics import median
from typing import Optional

from .models import (
    PromotionConfig, PromotionResult, GateResult,
    RunMetrics, AssetMetrics,
    ExperimentStatus, VariantStatus,
    PromotionDenied, PromotionRaceError,
)
from .repository import CCRepository

logger = logging.getLogger(__name__)


# =============================================================================
# SAFE METRICS HELPERS (v8: No inf/NaN propagation)
# =============================================================================

def safe_pct(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Calculate percentage safely, returning default if denominator is zero/invalid.

    v8: Use this for ALL percentage calculations to prevent inf/NaN.

    Returns:
        (numerator / denominator) * 100, or default if denominator is zero/None/NaN
    """
    if denominator is None or denominator == 0 or math.isnan(denominator):
        return default
    result = (numerator / denominator) * 100
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Calculate ratio safely, returning default if denominator is zero/invalid.

    v8: Use this for ALL ratio calculations to prevent inf/NaN.
    """
    if denominator is None or denominator == 0 or math.isnan(denominator):
        return default
    result = numerator / denominator
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def safe_improvement_pct(old_value: float, new_value: float) -> tuple[Optional[float], str]:
    """
    Calculate improvement percentage with human-readable edge case handling.

    v8: Returns (pct, description) tuple for display.

    Returns:
        (improvement_pct, description)
        - Normal case: (15.5, "15.5% improvement")
        - Old was zero, new positive: (None, "new > 0 (baseline was 0)")
        - Both zero: (0.0, "no change (both 0)")
        - Old was zero, new negative: (None, "new < 0 (baseline was 0)")
    """
    if old_value == 0:
        if new_value > 0:
            return (None, "new > 0 (baseline was 0)")
        elif new_value == 0:
            return (0.0, "no change (both 0)")
        else:
            return (None, "new < 0 (baseline was 0)")

    pct = ((new_value - old_value) / abs(old_value)) * 100

    # Guard against NaN/inf from edge cases
    if math.isnan(pct) or math.isinf(pct):
        return (None, f"calculation error ({old_value} -> {new_value})")

    direction = "improvement" if pct > 0 else "decline"
    return (pct, f"{pct:+.1f}% {direction}")


# =============================================================================
# PROMOTION GATE CHECKS
# =============================================================================

def check_promotion_gates(
    run_id: str,
    repository: CCRepository,
    config: Optional[PromotionConfig] = None,
) -> PromotionResult:
    """
    Check if Challenger should be promoted to Champion.

    All three gates must pass:
    1. Expectancy improves
    2. Drawdown within tolerance
    3. Multi-asset generalization

    Returns:
        PromotionResult with should_promote flag and gate details
    """
    config = config or PromotionConfig()
    gates: list[GateResult] = []

    # Load metrics
    metrics = repository.get_run_metrics(run_id)
    run = repository.get_experiment_run(run_id)

    if not run:
        return PromotionResult(
            should_promote=False,
            gates=[GateResult(
                gate_name="run_exists",
                passed=False,
                details={"error": f"Run {run_id} not found"},
            )],
        )

    champ_metrics = metrics.get(run.champion_variant_id)
    chall_metrics = metrics.get(run.challenger_variant_id)

    if not champ_metrics or not chall_metrics:
        return PromotionResult(
            should_promote=False,
            gates=[GateResult(
                gate_name="metrics_exist",
                passed=False,
                details={
                    "error": "Missing metrics",
                    "has_champion_metrics": champ_metrics is not None,
                    "has_challenger_metrics": chall_metrics is not None,
                },
            )],
        )

    # =========================================================================
    # Gate 1: Expectancy improves
    # =========================================================================
    champ_expectancy = champ_metrics.expectancy or 0.0
    chall_expectancy = chall_metrics.expectancy or 0.0

    expectancy_improved = (
        chall_expectancy > champ_expectancy *
        (1 + config.min_expectancy_improvement_pct / 100)
    )

    # v8: Use safe improvement calculation
    improvement_pct, improvement_desc = safe_improvement_pct(
        champ_expectancy, chall_expectancy
    )

    gates.append(GateResult(
        gate_name="expectancy",
        passed=expectancy_improved,
        details={
            "champion": champ_expectancy,
            "challenger": chall_expectancy,
            "improvement_pct": improvement_pct,
            "improvement_desc": improvement_desc,
            "min_required_pct": config.min_expectancy_improvement_pct,
        },
    ))

    # =========================================================================
    # Gate 2: Drawdown within tolerance
    # =========================================================================
    champ_dd = champ_metrics.max_drawdown_pct or 0.0
    chall_dd = chall_metrics.max_drawdown_pct or 0.0

    dd_within_tolerance = (
        chall_dd <= champ_dd + config.max_drawdown_increase_pct
    )

    gates.append(GateResult(
        gate_name="drawdown",
        passed=dd_within_tolerance,
        details={
            "champion": champ_dd,
            "challenger": chall_dd,
            "tolerance": config.max_drawdown_increase_pct,
            "max_allowed": champ_dd + config.max_drawdown_increase_pct,
        },
    ))

    # =========================================================================
    # Gate 3: Multi-asset generalization
    # =========================================================================
    generalization_result = _check_generalization_gate(
        run_id=run_id,
        champ_variant_id=run.champion_variant_id,
        chall_variant_id=run.challenger_variant_id,
        repository=repository,
        config=config,
    )
    gates.append(generalization_result)

    # All gates must pass
    all_passed = all(g.passed for g in gates)

    return PromotionResult(
        should_promote=all_passed,
        gates=gates,
    )


def _check_generalization_gate(
    run_id: str,
    champ_variant_id: str,
    chall_variant_id: str,
    repository: CCRepository,
    config: PromotionConfig,
) -> GateResult:
    """
    Check the multi-asset generalization gate.

    CRITICAL FIX (v5): Require BOTH champion AND challenger to have
    min_trades_per_asset for a symbol before it can:
    1. Count toward assets_with_enough_trades
    2. Contribute to assets_improved

    This prevents "unfair wins" where challenger beats a statistically
    meaningless champion number (e.g., champion had <5 trades on that symbol).
    """
    all_asset_metrics = repository.get_all_asset_metrics(run_id)

    champ_assets = {m.symbol: m for m in all_asset_metrics.get(champ_variant_id, [])}
    chall_assets = {m.symbol: m for m in all_asset_metrics.get(chall_variant_id, [])}

    # Get all symbols
    all_symbols = set(champ_assets.keys()) | set(chall_assets.keys())

    # Collect assets where BOTH variants have sufficient trades
    assets_with_enough_trades = 0
    assets_improved = 0
    champ_qualifying_expectancies: list[float] = []
    chall_qualifying_expectancies: list[float] = []

    for symbol in all_symbols:
        champ_asset = champ_assets.get(symbol)
        chall_asset = chall_assets.get(symbol)

        # BOTH must have min trades for fair comparison
        champ_has_enough = (
            champ_asset is not None and
            champ_asset.trade_count >= config.min_trades_per_asset
        )
        chall_has_enough = (
            chall_asset is not None and
            chall_asset.trade_count >= config.min_trades_per_asset
        )

        if champ_has_enough and chall_has_enough:
            # This asset qualifies for comparison
            assets_with_enough_trades += 1

            champ_exp = champ_asset.expectancy or 0.0
            chall_exp = chall_asset.expectancy or 0.0

            champ_qualifying_expectancies.append(champ_exp)
            chall_qualifying_expectancies.append(chall_exp)

            # Count improvement only for qualifying assets
            if chall_exp > champ_exp:
                assets_improved += 1

    # GUARD: Insufficient sample size -> gate fails explicitly
    if assets_with_enough_trades < config.min_assets_improved:
        return GateResult(
            gate_name="generalization",
            passed=False,
            details={
                "reason": "INSUFFICIENT_SAMPLE_SIZE",
                "assets_with_enough_trades": assets_with_enough_trades,
                "min_required": config.min_assets_improved,
                "assets_improved": assets_improved,
                "min_trades_per_asset": config.min_trades_per_asset,
            },
        )

    generalizes = assets_improved >= config.min_assets_improved

    # Median comparison (with empty-set guard - already covered by above check)
    if config.use_median_comparison and champ_qualifying_expectancies and chall_qualifying_expectancies:
        champ_median = median(champ_qualifying_expectancies)
        chall_median = median(chall_qualifying_expectancies)
        generalizes = generalizes and (chall_median > champ_median)

        return GateResult(
            gate_name="generalization",
            passed=generalizes,
            details={
                "assets_improved": assets_improved,
                "assets_with_enough_trades": assets_with_enough_trades,
                "min_required": config.min_assets_improved,
                "champ_median_expectancy": champ_median,
                "chall_median_expectancy": chall_median,
                "min_trades_per_asset": config.min_trades_per_asset,
            },
        )

    return GateResult(
        gate_name="generalization",
        passed=generalizes,
        details={
            "assets_improved": assets_improved,
            "assets_with_enough_trades": assets_with_enough_trades,
            "min_required": config.min_assets_improved,
            "min_trades_per_asset": config.min_trades_per_asset,
        },
    )


# =============================================================================
# PROMOTION EXECUTION
# =============================================================================

def execute_promotion(
    run_id: str,
    repository: CCRepository,
    redis_client: Optional[object] = None,
) -> bool:
    """
    Promote Challenger to Champion.

    v8: Wrapped in transaction with row-level locking to prevent race conditions.

    Returns:
        True if promotion succeeded

    Raises:
        PromotionDenied: If gates don't pass
        PromotionRaceError: If run was already promoted by another process
    """
    # First, verify gates pass
    result = check_promotion_gates(run_id, repository)
    if not result.should_promote:
        raise PromotionDenied(
            f"Gates not passed: {[g.to_dict() for g in result.gates if not g.passed]}"
        )

    # Lock the run for promotion
    run = repository.lock_run_for_promotion(run_id)
    if not run:
        raise PromotionRaceError(
            f"Could not acquire lock on run {run_id} - likely being promoted by another process"
        )

    # Check run hasn't already been promoted
    if run.status == ExperimentStatus.PROMOTED:
        raise PromotionRaceError(
            f"Run {run_id} already promoted (likely by concurrent process)"
        )

    if run.status != ExperimentStatus.RUNNING:
        raise PromotionDenied(
            f"Run {run_id} status is {run.status.value}, expected RUNNING"
        )

    # Get setup_id
    setup_id = run.setup_id

    # Execute promotion
    # 1. Demote old Champion to RETIRED
    repository.update_variant_status(run.champion_variant_id, VariantStatus.RETIRED)

    # 2. Promote Challenger to CHAMPION
    repository.update_variant_status(run.challenger_variant_id, VariantStatus.CHAMPION)

    # 3. Mark run as PROMOTED
    repository.update_experiment_run_status(
        run_id,
        ExperimentStatus.PROMOTED,
        end_ts=datetime.now(timezone.utc),
    )

    # 4. Record in change ledger
    repository.record_change(setup_id, run.challenger_variant_id, "PROMOTED")

    # 5. Update Redis champion key (if client provided)
    if redis_client:
        from .evaluator import cc_champion_key
        redis_client.set(cc_champion_key(setup_id), run.challenger_variant_id)

    logger.info(f"Promoted {run.challenger_variant_id} to CHAMPION for {setup_id}")

    return True


# =============================================================================
# PROMOTION HELPERS
# =============================================================================

def can_promote(
    run_id: str,
    repository: CCRepository,
    config: Optional[PromotionConfig] = None,
) -> tuple[bool, PromotionResult]:
    """
    Check if a run can be promoted (convenience wrapper).

    Returns:
        (can_promote, promotion_result)
    """
    result = check_promotion_gates(run_id, repository, config)
    return result.should_promote, result
