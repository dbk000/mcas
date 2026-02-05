"""
Champion/Challenger Framework - API Endpoints
==============================================

Management and read endpoints for the C/C framework.

Management:
- POST /cc/variant - Create variant (Baseline or initial Champion)
- POST /cc/challenger/from/{champion_variant_id} - Create Challenger
- POST /cc/run - Start experiment run
- POST /cc/run/{run_id}/close - Close experiment run
- POST /cc/run/{run_id}/promote - Attempt promotion

Read:
- GET /cc/champions - List current champions
- GET /cc/variants - List variants
- GET /cc/variant/{variant_id} - Get variant detail
- GET /cc/runs - List experiment runs
- GET /cc/run/{run_id}/metrics - Get run metrics
- GET /cc/run/{run_id}/assets - Get per-asset metrics
- GET /cc/run/{run_id}/trades - Get trades
- GET /cc/diagnostics - Get trade diagnostics
- GET /cc/change-ledger - Get change history
"""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Query, Body, HTTPException, Depends

# Import CC modules - adjust path as needed
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../worker/app'))

from cc.models import (
    Variant, VariantStatus, VariantModule,
    ModuleKind, DiffType,
    ExperimentRun, ExperimentMode, ExperimentStatus,
    PromotionConfig,
    CadenceViolation, ActiveChallengerExists, OneModuleDifferentViolation,
    PromotionDenied, PromotionRaceError, NoBaselineError,
)
from cc.repository import CCRepository
from cc.registry import VariantRegistry, create_initial_modules_from_setup, validate_all_identifiers
from cc.promotion import check_promotion_gates, execute_promotion
from cc.diagnostics import get_diagnostics_summary, auto_classify_trades

logger = logging.getLogger(__name__)

# Database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")


def get_repository() -> CCRepository:
    """Get a CCRepository instance."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    return CCRepository(DATABASE_URL)


def envelope(data: Any, compute_start: float) -> dict:
    """Standard response envelope with metadata."""
    return {
        "data": data,
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
            "compute_ms": int((time.time() - compute_start) * 1000),
        }
    }


# Router for CC endpoints
router = APIRouter(prefix="/cc", tags=["Champion/Challenger"])


# =============================================================================
# VARIANT MANAGEMENT
# =============================================================================

@router.post("/variant")
def create_variant(
    setup_id: str = Body(...),
    version: str = Body("1.0"),
    modules: Optional[dict] = Body(None),
    status: str = Body("BASELINE"),  # BASELINE or CHAMPION for initial
    notes: Optional[str] = Body(None),
    repo: CCRepository = Depends(get_repository),
):
    """
    Create a new variant (typically a BASELINE or initial CHAMPION).

    For initial setup, use status="BASELINE" or status="CHAMPION".
    To create a challenger, use POST /cc/challenger/from/{champion_variant_id}.
    """
    t = time.time()

    try:
        # Validate identifiers
        validate_all_identifiers(setup_id=setup_id)

        # Create module map
        if modules:
            variant_modules = {
                k: VariantModule(
                    module_kind=ModuleKind(k),
                    module_id=v.get("module_id", f"{setup_id}.{k.lower()}.v{version}"),
                    params=v.get("params", {}),
                )
                for k, v in modules.items()
            }
        else:
            variant_modules = create_initial_modules_from_setup(setup_id, version)

        registry = VariantRegistry(repo)

        if status == "BASELINE":
            variant = registry.create_baseline(
                setup_id=setup_id,
                modules=variant_modules,
                notes=notes,
            )
        elif status == "CHAMPION":
            variant = registry.create_champion(
                setup_id=setup_id,
                modules=variant_modules,
                notes=notes,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}. Use BASELINE or CHAMPION."
            )

        return envelope(variant.to_dict(), t)

    except Exception as e:
        logger.error(f"Failed to create variant: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/challenger/from/{champion_variant_id}")
def create_challenger(
    champion_variant_id: str,
    diff_type: str = Body(...),           # "MODULE" | "PARAM_BUNDLE" | "SCORE_COMPONENT"
    diff_module_kind: str = Body(...),    # "FILTER" | "REGIME" | etc.
    diff_key: str = Body(...),            # Descriptive: "EXIT.trailing"
    new_module_id: Optional[str] = Body(None),      # For MODULE type
    new_params: Optional[dict] = Body(None),        # For PARAM_BUNDLE or SCORE_COMPONENT type
    notes: Optional[str] = Body(None),
    repo: CCRepository = Depends(get_repository),
):
    """
    Create Challenger from Champion.

    Enforces:
    - One-module-different rule (exactly 1 diff)
    - Weekly cadence (7 days since last change)
    - No existing active challenger for this setup
    """
    t = time.time()

    try:
        registry = VariantRegistry(repo)

        variant = registry.create_challenger_from_champion(
            champion_variant_id=champion_variant_id,
            diff_type=DiffType(diff_type),
            diff_module_kind=ModuleKind(diff_module_kind),
            diff_key=diff_key,
            new_module_id=new_module_id,
            new_params=new_params,
            notes=notes,
        )

        return envelope(variant.to_dict(), t)

    except CadenceViolation as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ActiveChallengerExists as e:
        raise HTTPException(status_code=409, detail=str(e))
    except OneModuleDifferentViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create challenger: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# EXPERIMENT RUN MANAGEMENT
# =============================================================================

@router.post("/run")
def start_experiment_run(
    baseline_variant_id: Optional[str] = Body(None),
    champion_variant_id: str = Body(...),
    challenger_variant_id: str = Body(...),
    mode: str = Body("SHADOW"),
    timeframes: list[str] = Body(["1h"]),
    asset_universe: Optional[list[str]] = Body(None),
    notes: Optional[str] = Body(None),
    repo: CCRepository = Depends(get_repository),
):
    """
    Start a new experiment run.

    Freezes module maps and execution spec at start.

    BASELINE ENFORCEMENT (Canon-critical):
    - If baseline_variant_id not provided, lookup from setup's baseline
    - If no baseline exists for this setup, REFUSE to start run
    - Baseline must ALWAYS be included in 3-way comparison
    """
    t = time.time()

    try:
        # Get champion to determine setup_id
        champion = repo.get_variant(champion_variant_id)
        if not champion:
            raise HTTPException(
                status_code=404,
                detail=f"Champion variant {champion_variant_id} not found"
            )

        setup_id = champion.setup_id

        # Resolve baseline
        if baseline_variant_id is None:
            baseline = repo.get_baseline(setup_id)
            if baseline:
                baseline_variant_id = baseline.variant_id
            else:
                raise NoBaselineError(
                    f"No baseline defined for setup {setup_id}. "
                    f"Create a BASELINE variant first via POST /cc/variant"
                )

        # Check for existing active run
        active = repo.get_active_run(setup_id)
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"Setup {setup_id} already has active run: {active.run_id}. "
                       f"Close or cancel it before starting a new experiment."
            )

        # Freeze module maps
        baseline_snapshot = repo.get_variant_module_map_snapshot(baseline_variant_id)
        champion_snapshot = repo.get_variant_module_map_snapshot(champion_variant_id)
        challenger_snapshot = repo.get_variant_module_map_snapshot(challenger_variant_id)

        # Default execution spec
        execution_snapshot = {
            "slippage_entry_bps": 5.0,
            "slippage_exit_bps": 5.0,
            "fee_maker_bps": 2.0,
            "fee_taker_bps": 4.0,
            "max_positions": 3,
            "max_risk_per_trade_pct": 1.0,
        }

        # Create run
        run = ExperimentRun(
            baseline_variant_id=baseline_variant_id,
            champion_variant_id=champion_variant_id,
            challenger_variant_id=challenger_variant_id,
            baseline_module_map_snapshot=baseline_snapshot,
            champion_module_map_snapshot=champion_snapshot,
            challenger_module_map_snapshot=challenger_snapshot,
            mode=ExperimentMode(mode),
            setup_id=setup_id,
            asset_universe_snapshot=asset_universe,
            timeframes=timeframes,
            execution_spec_snapshot=execution_snapshot,
            status=ExperimentStatus.RUNNING,
            notes=notes,
        )

        repo.create_experiment_run(run)

        return envelope(run.to_dict(), t)

    except NoBaselineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to start experiment run: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run/{run_id}/close")
def close_experiment_run(
    run_id: str,
    repo: CCRepository = Depends(get_repository),
):
    """End an experiment run and compute final metrics."""
    t = time.time()

    run = repo.get_experiment_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status != ExperimentStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Run {run_id} status is {run.status.value}, expected RUNNING"
        )

    # Update status
    repo.update_experiment_run_status(
        run_id,
        ExperimentStatus.COMPLETED,
        end_ts=datetime.now(timezone.utc),
    )

    # Auto-classify trades for diagnostics
    if run.challenger_variant_id:
        auto_classify_trades(run_id, run.challenger_variant_id, repo)
    if run.champion_variant_id:
        auto_classify_trades(run_id, run.champion_variant_id, repo)
    if run.baseline_variant_id:
        auto_classify_trades(run_id, run.baseline_variant_id, repo)

    return envelope({"status": "COMPLETED", "run_id": run_id}, t)


@router.post("/run/{run_id}/promote")
def attempt_promotion(
    run_id: str,
    repo: CCRepository = Depends(get_repository),
):
    """
    Attempt to promote Challenger to Champion.

    Returns gate results. Executes promotion if all gates pass.
    """
    t = time.time()

    try:
        result = check_promotion_gates(run_id, repo)

        if result.should_promote:
            execute_promotion(run_id, repo)
            return envelope({
                "promoted": True,
                "gates": [g.to_dict() for g in result.gates],
            }, t)
        else:
            return envelope({
                "promoted": False,
                "gates": [g.to_dict() for g in result.gates],
                "reason": "One or more gates failed",
            }, t)

    except PromotionDenied as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PromotionRaceError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Promotion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run/{run_id}/cancel")
def cancel_experiment_run(
    run_id: str,
    repo: CCRepository = Depends(get_repository),
):
    """Cancel an experiment run."""
    t = time.time()

    run = repo.get_experiment_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status != ExperimentStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Run {run_id} status is {run.status.value}, expected RUNNING"
        )

    repo.update_experiment_run_status(
        run_id,
        ExperimentStatus.CANCELLED,
        end_ts=datetime.now(timezone.utc),
    )

    return envelope({"status": "CANCELLED", "run_id": run_id}, t)


# =============================================================================
# READ ENDPOINTS
# =============================================================================

@router.get("/champions")
def list_champions(
    setup_id: Optional[str] = Query(None),
    repo: CCRepository = Depends(get_repository),
):
    """List current Champions (and Baselines) per setup."""
    t = time.time()

    result = {}

    # Get unique setup IDs
    if setup_id:
        setup_ids = [setup_id]
    else:
        # Get all variants and extract unique setup IDs
        variants = repo.list_variants(limit=1000)
        setup_ids = list(set(v.setup_id for v in variants if v))

    for sid in setup_ids:
        champion = repo.get_champion(sid)
        baseline = repo.get_baseline(sid)

        result[sid] = {
            "champion": champion.to_dict() if champion else None,
            "baseline": baseline.to_dict() if baseline else None,
        }

    return envelope(result, t)


@router.get("/variants")
def list_variants(
    setup_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50),
    repo: CCRepository = Depends(get_repository),
):
    """List variants with optional filters."""
    t = time.time()

    variant_status = VariantStatus(status) if status else None
    variants = repo.list_variants(
        setup_id=setup_id,
        status=variant_status,
        limit=limit,
    )

    return envelope([v.to_dict() for v in variants if v], t)


@router.get("/variant/{variant_id}")
def get_variant_detail(
    variant_id: str,
    repo: CCRepository = Depends(get_repository),
):
    """Get full variant detail including module map."""
    t = time.time()

    variant = repo.get_variant(variant_id)
    if not variant:
        raise HTTPException(status_code=404, detail=f"Variant {variant_id} not found")

    return envelope(variant.to_dict(), t)


@router.get("/runs")
def list_runs(
    status: Optional[str] = Query(None),
    setup_id: Optional[str] = Query(None),
    limit: int = Query(20),
    repo: CCRepository = Depends(get_repository),
):
    """List experiment runs."""
    t = time.time()

    run_status = ExperimentStatus(status) if status else None
    runs = repo.list_experiment_runs(
        status=run_status,
        setup_id=setup_id,
        limit=limit,
    )

    return envelope([r.to_dict() for r in runs], t)


@router.get("/run/{run_id}")
def get_run_detail(
    run_id: str,
    repo: CCRepository = Depends(get_repository),
):
    """Get experiment run detail."""
    t = time.time()

    run = repo.get_experiment_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    return envelope(run.to_dict(), t)


@router.get("/run/{run_id}/metrics")
def get_run_metrics(
    run_id: str,
    repo: CCRepository = Depends(get_repository),
):
    """Get aggregated metrics for all variants in a run."""
    t = time.time()

    metrics = repo.get_run_metrics(run_id)

    return envelope({
        k: v.to_dict() for k, v in metrics.items()
    }, t)


@router.get("/run/{run_id}/assets")
def get_run_per_asset_metrics(
    run_id: str,
    repo: CCRepository = Depends(get_repository),
):
    """Get per-asset metrics for generalization gate inspection."""
    t = time.time()

    metrics = repo.get_all_asset_metrics(run_id)

    return envelope({
        variant_id: [m.to_dict() for m in asset_list]
        for variant_id, asset_list in metrics.items()
    }, t)


@router.get("/run/{run_id}/trades")
def get_run_trades(
    run_id: str,
    variant_id: Optional[str] = Query(None),
    limit: int = Query(100),
    repo: CCRepository = Depends(get_repository),
):
    """Get trades from an experiment run."""
    t = time.time()

    trades = repo.get_trades(run_id, variant_id, limit)

    return envelope([tr.to_dict() for tr in trades], t)


@router.get("/diagnostics")
def get_trade_diagnostics(
    run_id: str = Query(...),
    repo: CCRepository = Depends(get_repository),
):
    """
    Get trade diagnostics with both winner and loser buckets (v6).

    Returns:
    - loser_buckets: Counts per failure bucket
    - winner_buckets: Counts per winner bucket
    - recommendation: Next-tweak recommendation (only if convergence exists)
    """
    t = time.time()

    summary = get_diagnostics_summary(run_id, repo)

    return envelope(summary, t)


@router.get("/change-ledger")
def get_change_ledger(
    setup_id: Optional[str] = Query(None),
    limit: int = Query(20),
    repo: CCRepository = Depends(get_repository),
):
    """Get change history for weekly cadence visibility."""
    t = time.time()

    entries = repo.get_change_ledger(setup_id, limit)

    return envelope([e.to_dict() for e in entries], t)
