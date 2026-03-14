"""
Champion/Challenger Framework - Diagnostics
============================================

Trade diagnostics: winner/loser classification and next-tweak recommendations.

Canon compliance:
- Diagnostics run on EVERY closed trade (win or loss)
- Winner buckets feed hypotheses but don't directly trigger changes
- Changes only recommended when winner and loser buckets CONVERGE on same module

Bucket types:
- Failure buckets (losses): BAD_REGIME, BAD_ENTRY_TIMING, etc.
- Winner buckets (edge leak detection): EARLY_EXIT, REGIME_TAILWIND, etc.
"""

import logging
from typing import Optional, Any

from .models import (
    ShadowTrade, TradeDiagnostic,
    FailureBucket, WinnerBucket, ModuleKind,
    NextTweakRecommendation,
    LOSER_BUCKET_TO_MODULE, WINNER_BUCKET_TO_MODULE,
    MODULE_PRIORITY_ORDER,
)
from .repository import CCRepository

logger = logging.getLogger(__name__)


# =============================================================================
# FAILURE CLASSIFICATION (Losses)
# =============================================================================

def classify_failure(trade: ShadowTrade, trace: Optional[dict] = None) -> Optional[FailureBucket]:
    """
    Classify a losing trade into a failure bucket.

    Uses heuristics based on exit reason, MAE/MFE, and context.

    Args:
        trade: The shadow trade to classify
        trace: Optional trade trace with additional context

    Returns:
        FailureBucket or None if not a failure
    """
    if trade.r_multiple >= 0:
        return None  # Not a failure

    trace = trace or trade.trace_json or {}
    exit_reason = trade.exit_reason or ""

    # BAD_STOP: Stopped out but would have been profitable
    if exit_reason == "STOP_LOSS":
        # Check if MFE was significant (would have hit TP1)
        if trade.mfe and trade.r_value > 0 and trade.mfe >= trade.r_value * 0.5:
            return FailureBucket.BAD_STOP  # Stop was too tight

    # BAD_ENTRY_TIMING: Early stop-out, MAE hit almost immediately
    if exit_reason == "STOP_LOSS" and trade.bars_held <= 2:
        return FailureBucket.BAD_ENTRY_TIMING

    # BAD_TP_TRAILING: Trailing stop hit after significant MFE
    if exit_reason in ("TRAILING_STOP", "TIME_STOP"):
        if trade.mfe and trade.r_value > 0 and trade.mfe >= trade.r_value:
            return FailureBucket.BAD_TP_TRAILING  # Should have taken profit earlier

    # BAD_REGIME: Large market move against position
    if abs(trade.r_multiple) > 2.0:
        # Check if it was a regime shift (e.g., BTC shock)
        exit_conditions = trace.get("exit_conditions", {})
        if exit_conditions.get("btc_shock"):
            return FailureBucket.BAD_REGIME

    # LIQUIDITY_ISSUE: Significant slippage
    if trade.fees_paid and trade.entry_price:
        slippage_pct = trade.fees_paid / trade.entry_price
        if slippage_pct > 0.005:  # >0.5%
            return FailureBucket.LIQUIDITY_ISSUE

    # SIGNAL_CONFLICT: Multiple signals, chose wrong one
    state_snapshot = trace.get("state_snapshot", {})
    if state_snapshot.get("conflicting_signals"):
        return FailureBucket.SIGNAL_CONFLICT

    return FailureBucket.OTHER


# =============================================================================
# WINNER CLASSIFICATION (Edge Leak Detection)
# =============================================================================

def classify_winner(trade: ShadowTrade, trace: Optional[dict] = None) -> Optional[WinnerBucket]:
    """
    Classify a winning trade into a winner bucket.

    v6 addition (canon compliance): Diagnostics on wins feed hypotheses
    but don't directly trigger changes unless converging with loser buckets.

    Args:
        trade: The shadow trade to classify
        trace: Optional trade trace with additional context

    Returns:
        WinnerBucket or None if not a winner
    """
    if trade.r_multiple < 0:
        return None  # Not a winner

    trace = trace or trade.trace_json or {}

    # EARLY_EXIT: Left significant profit on the table
    # MFE was much larger than realized R (e.g., MFE > 2 * realized)
    if trade.mfe and trade.r_multiple > 0 and trade.r_value > 0:
        potential_r = trade.mfe / trade.r_value
        if potential_r > 2 * trade.r_multiple:
            return WinnerBucket.EARLY_EXIT

    # REGIME_TAILWIND: Strong market move in our favor
    if trade.r_multiple > 2.0 and trade.bars_held <= 3:
        return WinnerBucket.REGIME_TAILWIND

    # ENTRY_PRECISION: Very low MAE relative to R
    if trade.mae and trade.r_value > 0:
        mae_ratio = abs(trade.mae) / trade.r_value
        if mae_ratio < 0.2:
            return WinnerBucket.ENTRY_PRECISION

    # STOP_NEVER_THREATENED: MAE never came close to stop
    if trade.mae and trade.r_value > 0:
        mae_ratio = abs(trade.mae) / trade.r_value
        if mae_ratio < 0.3:
            return WinnerBucket.STOP_NEVER_THREATENED

    # HIGH_SLIPPAGE_WIN: Won despite slippage
    if trade.fees_paid and trade.entry_price:
        slippage_pct = trade.fees_paid / trade.entry_price
        if slippage_pct > 0.003:  # >0.3%
            return WinnerBucket.HIGH_SLIPPAGE_WIN

    # CONFLICTING_SIGNAL_WIN: Won despite conflicts
    state_snapshot = trace.get("state_snapshot", {})
    if state_snapshot.get("conflicting_signals"):
        return WinnerBucket.CONFLICTING_SIGNAL_WIN

    return WinnerBucket.CLEAN_WIN


# =============================================================================
# TRADE CLASSIFICATION (Combined)
# =============================================================================

def classify_trade(trade: ShadowTrade, trace: Optional[dict] = None) -> TradeDiagnostic:
    """
    Classify a trade into appropriate bucket (winner or loser).

    Args:
        trade: The shadow trade to classify
        trace: Optional trade trace

    Returns:
        TradeDiagnostic with classification
    """
    is_winner = trade.r_multiple >= 0

    if is_winner:
        bucket = classify_winner(trade, trace)
        return TradeDiagnostic(
            trade_id=trade.trade_id,
            variant_id="",  # To be filled by caller
            run_id="",  # To be filled by caller
            is_winner=True,
            winner_bucket=bucket,
            hypothesis_module_kind=WINNER_BUCKET_TO_MODULE.get(bucket) if bucket else None,
        )
    else:
        bucket = classify_failure(trade, trace)
        return TradeDiagnostic(
            trade_id=trade.trade_id,
            variant_id="",  # To be filled by caller
            run_id="",  # To be filled by caller
            is_winner=False,
            failure_bucket=bucket,
            hypothesis_module_kind=LOSER_BUCKET_TO_MODULE.get(bucket) if bucket else None,
        )


# =============================================================================
# NEXT TWEAK RECOMMENDATION
# =============================================================================

def get_next_tweak_recommendation(
    run_id: str,
    repository: CCRepository,
) -> NextTweakRecommendation:
    """
    Analyze postmortems and recommend next module to tweak.

    v6 (canon compliance): Only recommend when winner and loser buckets
    CONVERGE on the same module. This implements the non-confounding rule:
    "changes only when winner-side and loser-side point to same module."

    Args:
        run_id: The experiment run ID
        repository: CCRepository instance

    Returns:
        NextTweakRecommendation with module and details
    """
    loser_buckets = repository.get_loser_bucket_counts(run_id)
    winner_buckets = repository.get_winner_bucket_counts(run_id)

    total_losses = sum(loser_buckets.values()) if loser_buckets else 0
    total_wins = sum(winner_buckets.values()) if winner_buckets else 0

    if total_losses == 0:
        return NextTweakRecommendation(
            reason="NO_FAILURES_TO_ANALYZE",
            loser_module_scores={},
            winner_module_scores={},
        )

    # Compute module scores from both sides
    loser_module_scores: dict[str, int] = {}
    for bucket_str, count in loser_buckets.items():
        try:
            bucket = FailureBucket(bucket_str)
            module = LOSER_BUCKET_TO_MODULE.get(bucket)
            if module:
                key = module.value
                loser_module_scores[key] = loser_module_scores.get(key, 0) + count
        except ValueError:
            pass  # Unknown bucket

    winner_module_scores: dict[str, int] = {}
    for bucket_str, count in winner_buckets.items():
        try:
            bucket = WinnerBucket(bucket_str)
            module = WINNER_BUCKET_TO_MODULE.get(bucket)
            if module:
                key = module.value
                winner_module_scores[key] = winner_module_scores.get(key, 0) + count
        except ValueError:
            pass  # Unknown bucket

    # Find convergence: modules that appear in BOTH loser and winner analysis
    converging_modules = []
    for module_kind in MODULE_PRIORITY_ORDER:
        module_key = module_kind.value
        loser_count = loser_module_scores.get(module_key, 0)
        winner_count = winner_module_scores.get(module_key, 0)

        if loser_count > 0 and winner_count > 0:
            # Both sides point to this module
            converging_modules.append({
                "module_kind": module_kind,
                "loser_count": loser_count,
                "winner_count": winner_count,
                "combined_signal_strength": loser_count + winner_count,
                "loser_pct": round(loser_count / total_losses * 100, 1),
                "winner_pct": round(winner_count / total_wins * 100, 1) if total_wins > 0 else 0,
            })

    if not converging_modules:
        # No convergence - report hypothesis only (no action recommended)
        top_loser_module = None
        if loser_module_scores:
            top_loser_key = max(loser_module_scores.items(), key=lambda x: x[1])[0]
            try:
                top_loser_module = ModuleKind(top_loser_key)
            except ValueError:
                pass

        return NextTweakRecommendation(
            module_kind=None,
            reason="NO_CONVERGENCE",
            loser_module_scores=loser_module_scores,
            winner_module_scores=winner_module_scores,
        )

    # Return highest-priority converging module
    recommendation = converging_modules[0]

    return NextTweakRecommendation(
        module_kind=recommendation["module_kind"],
        reason="CONVERGENCE_FOUND",
        loser_count=recommendation["loser_count"],
        winner_count=recommendation["winner_count"],
        combined_signal_strength=recommendation["combined_signal_strength"],
        loser_pct=recommendation["loser_pct"],
        winner_pct=recommendation["winner_pct"],
        converging_alternatives=[
            {
                "module_kind": c["module_kind"].value,
                "loser_count": c["loser_count"],
                "winner_count": c["winner_count"],
                "combined_signal_strength": c["combined_signal_strength"],
            }
            for c in converging_modules[1:]
        ],
        loser_module_scores=loser_module_scores,
        winner_module_scores=winner_module_scores,
    )


# =============================================================================
# DIAGNOSTIC AGGREGATION
# =============================================================================

def get_diagnostics_summary(
    run_id: str,
    repository: CCRepository,
) -> dict:
    """
    Get a summary of diagnostics for a run.

    Returns:
        Dict with loser_buckets, winner_buckets, recommendation
    """
    loser_buckets = repository.get_loser_bucket_counts(run_id)
    winner_buckets = repository.get_winner_bucket_counts(run_id)
    recommendation = get_next_tweak_recommendation(run_id, repository)

    return {
        "loser_buckets": loser_buckets,
        "winner_buckets": winner_buckets,
        "total_losses": sum(loser_buckets.values()) if loser_buckets else 0,
        "total_wins": sum(winner_buckets.values()) if winner_buckets else 0,
        "recommendation": recommendation.to_dict(),
    }


# =============================================================================
# AUTO-CLASSIFICATION
# =============================================================================

def auto_classify_trades(
    run_id: str,
    variant_id: str,
    repository: CCRepository,
) -> int:
    """
    Automatically classify all trades for a variant in a run.

    Args:
        run_id: The experiment run ID
        variant_id: The variant ID to classify trades for
        repository: CCRepository instance

    Returns:
        Number of trades classified
    """
    trades = repository.get_trades(run_id, variant_id, limit=10000)
    classified = 0

    for trade in trades:
        if trade.exit_reason:  # Only classify closed trades
            diagnostic = classify_trade(trade, trade.trace_json)
            diagnostic.variant_id = variant_id
            diagnostic.run_id = run_id
            repository.save_diagnostic(diagnostic)
            classified += 1

    logger.info(f"Auto-classified {classified} trades for variant {variant_id} in run {run_id}")
    return classified
