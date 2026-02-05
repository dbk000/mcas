"""
Analytics API
=============

Endpoints for correlation analysis and trade diagnostics.

Async job pattern:
- POST /analytics/correlation — Enqueue analysis job
- GET /analytics/correlation/{id} — Poll for results
"""

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

router = APIRouter(prefix="/analytics", tags=["analytics"])


# =============================================================================
# Request/Response Models
# =============================================================================

class CorrelationRequest(BaseModel):
    """Request to create a correlation analysis job."""
    run_ids: list[str] = Field(..., min_length=1, description="List of run UUIDs to analyze")
    setup_id: Optional[str] = Field(None, description="Filter by setup ID (e.g., 'S1')")
    min_trades: int = Field(default=30, ge=10, le=1000, description="Minimum trades required")


class CorrelationResponse(BaseModel):
    """Response with analysis status and results."""
    analysis_id: str
    status: str
    config_hash: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_text: Optional[str] = None
    summary: Optional[dict] = None
    top_predictors: Optional[list] = None
    feature_analyses: Optional[dict] = None
    exit_reason_breakdown: Optional[dict] = None
    insights: Optional[list] = None


# =============================================================================
# Helper Functions
# =============================================================================

def _get_conn():
    """Get database connection."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    return psycopg.connect(DATABASE_URL)


def _compute_config_hash(run_ids: list[str], setup_id: Optional[str], min_trades: int) -> str:
    """
    Compute SHA256 hash of analysis configuration for caching.

    Config is sorted canonical JSON to ensure deterministic hashing.
    """
    config = {
        "min_trades": min_trades,
        "run_ids": sorted(run_ids),  # Sort for determinism
        "setup_id": setup_id,
    }
    canonical_json = json.dumps(config, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical_json.encode()).hexdigest()


def _validate_run_ids(run_ids: list[str]) -> list[str]:
    """Validate and normalize run_ids to UUID format."""
    validated = []
    for rid in run_ids:
        try:
            # Validate UUID format
            uuid.UUID(rid)
            validated.append(rid)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid run_id format: {rid}"
            )
    return validated


def _check_runs_have_trades(run_ids: list[str]) -> int:
    """Check that at least some runs have closed trades."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*)
                FROM run_trades
                WHERE run_id = ANY(%s)
                  AND status = 'CLOSED'
                  AND exit_reason IS NOT NULL;
            """, (run_ids,))
            row = cur.fetchone()
            return row[0] if row else 0


def _find_cached_analysis(config_hash: str) -> Optional[dict]:
    """Find an existing successful analysis with the same config hash."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, status, created_at
                FROM correlation_analysis
                WHERE config_hash = %s
                  AND status = 'SUCCESS'
                ORDER BY created_at DESC
                LIMIT 1;
            """, (config_hash,))
            row = cur.fetchone()

    if row:
        return {
            "analysis_id": str(row[0]),
            "status": row[1],
            "created_at": row[2].isoformat() if row[2] else None,
        }
    return None


def _envelope(data: dict, compute_start: float) -> dict:
    """Standard response envelope with metadata."""
    return {
        "data": data,
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
            "compute_ms": int((time.time() - compute_start) * 1000),
        }
    }


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/correlation")
def create_correlation_analysis(request: CorrelationRequest):
    """
    Enqueue a correlation analysis job.

    Returns analysis_id immediately. Poll GET /analytics/correlation/{id}
    for results.

    Response:
    - status: "PENDING" for new jobs, "SUCCESS" for cached results
    - If cached analysis exists with same config, returns cached result
    """
    t0 = time.time()

    # Validate run_ids
    run_ids = _validate_run_ids(request.run_ids)

    # Compute config hash for caching
    config_hash = _compute_config_hash(run_ids, request.setup_id, request.min_trades)

    # Check for cached result
    cached = _find_cached_analysis(config_hash)
    if cached:
        logger.info(f"Returning cached correlation analysis {cached['analysis_id']}")
        return _envelope({
            "analysis_id": cached["analysis_id"],
            "status": "SUCCESS",
            "config_hash": config_hash,
            "cached": True,
        }, t0)

    # Validate runs have trades
    trade_count = _check_runs_have_trades(run_ids)
    if trade_count < request.min_trades:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient trades: found {trade_count}, need {request.min_trades}"
        )

    # Create analysis job
    analysis_id = str(uuid.uuid4())
    config_json = {
        "min_trades": request.min_trades,
        "max_trades": 2000,  # Default cap
    }

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO correlation_analysis (
                    id, status, run_ids, setup_id, config_hash, config_json, created_at
                ) VALUES (
                    %s, 'PENDING', %s, %s, %s, %s, %s
                );
            """, (
                analysis_id,
                run_ids,
                request.setup_id,
                config_hash,
                json.dumps(config_json),
                datetime.now(timezone.utc),
            ))
        conn.commit()

    logger.info(f"Created correlation analysis job {analysis_id} for {len(run_ids)} runs")

    return _envelope({
        "analysis_id": analysis_id,
        "status": "PENDING",
        "config_hash": config_hash,
        "cached": False,
    }, t0)


@router.get("/correlation/{analysis_id}")
def get_correlation_analysis(analysis_id: str):
    """
    Get correlation analysis status and results.

    Poll this endpoint until status is SUCCESS or FAILED.

    Response includes:
    - status: PENDING, RUNNING, SUCCESS, FAILED
    - For SUCCESS: summary, top_predictors, feature_analyses, insights, insight_draft
    - For FAILED: error_text
    """
    t0 = time.time()

    # Validate UUID
    try:
        uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid analysis_id format")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id, status, run_ids, setup_id, config_hash,
                    created_at, started_at, finished_at,
                    error_text,
                    total_trades, winning_trades, losing_trades,
                    feature_analyses, top_predictors,
                    exit_reason_breakdown, insights
                FROM correlation_analysis
                WHERE id = %s;
            """, (analysis_id,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found")

    (
        id_, status, run_ids, setup_id, config_hash,
        created_at, started_at, finished_at,
        error_text,
        total_trades, winning_trades, losing_trades,
        feature_analyses, top_predictors,
        exit_reason_breakdown, insights_json,
    ) = row

    # Build response
    response = {
        "analysis_id": str(id_),
        "status": status,
        "config_hash": config_hash,
        "run_ids": [str(r) for r in run_ids] if run_ids else [],
        "setup_id": setup_id,
        "created_at": created_at.isoformat() if created_at else None,
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
    }

    if status == "FAILED":
        response["error_text"] = error_text

    elif status == "SUCCESS":
        insights_data = _parse_json(insights_json, {})

        # Include results
        response["summary"] = {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(winning_trades / total_trades, 4) if total_trades else 0,
            "truncated": insights_data.get("truncated", False),
        }

        response["top_predictors"] = _parse_json(top_predictors, [])
        response["feature_analyses"] = _parse_json(feature_analyses, {})
        response["exit_reason_breakdown"] = _parse_json(exit_reason_breakdown, {})
        response["insights"] = insights_data.get("items", [])

        # Include CI insight draft for autonomous loops
        if "draft_markdown" in insights_data:
            response["insight_draft_markdown"] = insights_data["draft_markdown"]
        if "draft" in insights_data:
            response["insight_draft"] = insights_data["draft"]

    return _envelope(response, t0)


@router.get("/correlation")
def list_correlation_analyses(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
):
    """
    List recent correlation analyses.

    Useful for finding past analyses or checking job queue.
    """
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute("""
                    SELECT id, status, setup_id, config_hash,
                           created_at, finished_at,
                           total_trades, winning_trades, losing_trades
                    FROM correlation_analysis
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT %s;
                """, (status, limit))
            else:
                cur.execute("""
                    SELECT id, status, setup_id, config_hash,
                           created_at, finished_at,
                           total_trades, winning_trades, losing_trades
                    FROM correlation_analysis
                    ORDER BY created_at DESC
                    LIMIT %s;
                """, (limit,))

            rows = cur.fetchall()

    analyses = []
    for row in rows:
        (
            id_, status_, setup_id, config_hash,
            created_at, finished_at,
            total_trades, winning_trades, losing_trades,
        ) = row

        item = {
            "analysis_id": str(id_),
            "status": status_,
            "setup_id": setup_id,
            "config_hash": config_hash[:8] if config_hash else None,
            "created_at": created_at.isoformat() if created_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
        }

        if total_trades is not None:
            item["summary"] = {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
            }

        analyses.append(item)

    return _envelope({
        "analyses": analyses,
        "count": len(analyses),
    }, t0)


def _parse_json(value, default):
    """Parse JSONB field, handling str/dict/None."""
    if value is None:
        return default
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


# =============================================================================
# CI Insights Endpoints
# =============================================================================

class InsightSaveRequest(BaseModel):
    """Request to save a CI insight entry."""
    analysis_id: str
    hypothesis: str
    change_type: Optional[str] = "NONE"
    change_target: Optional[str] = None
    change_before: Optional[str] = None
    change_after: Optional[str] = None
    config_hash: Optional[str] = None
    expected_effect: Optional[str] = None


class InsightUpdateRequest(BaseModel):
    """Request to update a CI insight with results."""
    result_analysis_id: str
    result_summary: str
    effect_observed: bool
    side_effects: Optional[str] = None
    decision: str = Field(..., pattern="^(KEEP|REVERT|ITERATE)$")
    decision_rationale: Optional[str] = None


@router.post("/insights")
def save_ci_insight(request: InsightSaveRequest):
    """
    Save a CI insight entry from a correlation analysis.

    This creates a structured record of the observation, hypothesis,
    and planned change for the decision chain log.
    """
    t0 = time.time()

    # Validate analysis_id
    try:
        uuid.UUID(request.analysis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid analysis_id format")

    # Fetch the analysis to get the draft
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT setup_id, run_ids, insights
                FROM correlation_analysis
                WHERE id = %s AND status = 'SUCCESS';
            """, (request.analysis_id,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found or not complete")

    setup_id, run_ids, insights_json = row
    insights_data = _parse_json(insights_json, {})
    draft_data = insights_data.get("draft", {})

    # Create insight record
    insight_id = str(uuid.uuid4())

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ci_insights (
                    id, setup_id, timeframes, run_ids,
                    analysis_id, top_loser_signatures, observation_summary,
                    hypothesis, change_type, change_target,
                    change_before, change_after, config_hash,
                    expected_effect, decision, author
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                );
            """, (
                insight_id,
                setup_id or draft_data.get("setup_id", "ALL"),
                draft_data.get("timeframes", ["1h"]),
                [str(r) for r in run_ids] if run_ids else [],
                request.analysis_id,
                json.dumps(draft_data.get("top_signatures", [])),
                draft_data.get("observation_summary", ""),
                request.hypothesis,
                request.change_type,
                request.change_target,
                request.change_before,
                request.change_after,
                request.config_hash,
                request.expected_effect,
                "PENDING",
                "api",
            ))
        conn.commit()

    logger.info(f"Saved CI insight {insight_id} for analysis {request.analysis_id}")

    return _envelope({
        "insight_id": insight_id,
        "status": "PENDING",
    }, t0)


@router.put("/insights/{insight_id}")
def update_ci_insight(insight_id: str, request: InsightUpdateRequest):
    """
    Update a CI insight with results from subsequent analysis.

    Call this after running a new analysis to record whether
    the expected effect was observed.
    """
    t0 = time.time()

    # Validate UUIDs
    try:
        uuid.UUID(insight_id)
        uuid.UUID(request.result_analysis_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ci_insights
                SET result_analysis_id = %s,
                    result_summary = %s,
                    effect_observed = %s,
                    side_effects = %s,
                    decision = %s,
                    decision_rationale = %s
                WHERE id = %s AND decision = 'PENDING'
                RETURNING id;
            """, (
                request.result_analysis_id,
                request.result_summary,
                request.effect_observed,
                request.side_effects,
                request.decision,
                request.decision_rationale,
                insight_id,
            ))
            row = cur.fetchone()
        conn.commit()

    if not row:
        raise HTTPException(status_code=404, detail="Insight not found or already decided")

    logger.info(f"Updated CI insight {insight_id} with decision {request.decision}")

    return _envelope({
        "insight_id": insight_id,
        "decision": request.decision,
    }, t0)


@router.get("/insights")
def list_ci_insights(
    decision: Optional[str] = Query(None, description="Filter by decision status"),
    setup_id: Optional[str] = Query(None, description="Filter by setup"),
    limit: int = Query(20, ge=1, le=100),
):
    """
    List CI insight entries.

    Use decision=PENDING to find insights awaiting result updates.
    """
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT id, setup_id, created_at, observation_summary,
                       hypothesis, change_type, change_target,
                       decision, effect_observed
                FROM ci_insights
                WHERE 1=1
            """
            params = []

            if decision:
                query += " AND decision = %s"
                params.append(decision)

            if setup_id:
                query += " AND setup_id = %s"
                params.append(setup_id)

            query += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()

    insights = [
        {
            "insight_id": str(row[0]),
            "setup_id": row[1],
            "created_at": row[2].isoformat() if row[2] else None,
            "observation_summary": row[3][:200] + "..." if row[3] and len(row[3]) > 200 else row[3],
            "hypothesis": row[4][:100] + "..." if row[4] and len(row[4]) > 100 else row[4],
            "change_type": row[5],
            "change_target": row[6],
            "decision": row[7],
            "effect_observed": row[8],
        }
        for row in rows
    ]

    return _envelope({
        "insights": insights,
        "count": len(insights),
    }, t0)


@router.get("/insights/{insight_id}")
def get_ci_insight(insight_id: str):
    """Get full details of a CI insight entry."""
    t0 = time.time()

    try:
        uuid.UUID(insight_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid insight_id format")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, setup_id, timeframes, run_ids,
                       analysis_id, top_loser_signatures, observation_summary,
                       hypothesis, change_type, change_target,
                       change_before, change_after, config_hash,
                       expected_effect, expected_direction,
                       result_analysis_id, result_summary,
                       effect_observed, side_effects,
                       decision, decision_rationale,
                       created_at, author
                FROM ci_insights
                WHERE id = %s;
            """, (insight_id,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Insight not found")

    return _envelope({
        "insight_id": str(row[0]),
        "setup_id": row[1],
        "timeframes": row[2],
        "run_ids": [str(r) for r in row[3]] if row[3] else [],
        "analysis_id": str(row[4]) if row[4] else None,
        "top_loser_signatures": _parse_json(row[5], []),
        "observation_summary": row[6],
        "hypothesis": row[7],
        "change_type": row[8],
        "change_target": row[9],
        "change_before": row[10],
        "change_after": row[11],
        "config_hash": row[12],
        "expected_effect": row[13],
        "expected_direction": row[14],
        "result_analysis_id": str(row[15]) if row[15] else None,
        "result_summary": row[16],
        "effect_observed": row[17],
        "side_effects": row[18],
        "decision": row[19],
        "decision_rationale": row[20],
        "created_at": row[21].isoformat() if row[21] else None,
        "author": row[22],
    }, t0)
