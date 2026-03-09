"""Equities forward predictor API endpoints."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg
from fastapi import APIRouter, HTTPException, Query

from db_pool import get_conn as _pool_get_conn

DATABASE_URL = os.getenv("DATABASE_URL")

router = APIRouter(prefix="/equities", tags=["equities-forward-predictor"])


def get_conn():
    """Return a connection context manager from the shared pool.

    Usage: `with get_conn() as conn:` -- connection is automatically
    returned to the pool when the block exits.
    """
    return _pool_get_conn()


def envelope(data: Any, compute_start: float, as_of_ts: float = None) -> dict:
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "v1",
        "compute_ms": round((time.time() - compute_start) * 1000, 1),
    }
    if as_of_ts is not None:
        meta["as_of_ts"] = datetime.fromtimestamp(as_of_ts, tz=timezone.utc).isoformat()
    else:
        meta["as_of_ts"] = datetime.now(timezone.utc).isoformat()
    return {"data": data, "meta": meta}


@router.get("/opportunities")
def list_equities_opportunities(
    symbol: Optional[str] = Query(default=None),
    horizon: Optional[str] = Query(default=None),
    status: str = Query(default="active"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    min_edge_score: float = Query(default=0.0, ge=0.0),
    limit: int = Query(default=100, ge=1, le=1000),
):
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    eo.id,
                    eo.created_at,
                    eo.updated_at,
                    eo.symbol,
                    eo.timeframe,
                    eo.horizon,
                    eo.bar_start_ts,
                    eo.available_at_ts,
                    eo.status,
                    eo.expected_return,
                    eo.probability_up,
                    eo.confidence,
                    eo.regime_label,
                    eo.legacy_opportunity_id,
                    COALESCE((eo.rationale ->> 'edge_score')::double precision, 0.0) AS edge_score,
                    COALESCE((eo.rationale ->> 'model_quality_factor')::double precision, 1.0) AS model_quality_factor,
                    COALESCE((eo.rationale ->> 'coverage_quality_factor')::double precision, 1.0) AS coverage_quality_factor,
                    COALESCE((eo.rationale ->> 'regime_quality_factor')::double precision, 1.0) AS regime_quality_factor,
                    ep.model_name,
                    ep.model_version
                FROM equities_opportunities eo
                JOIN equities_predictions ep ON ep.id = eo.prediction_id
                WHERE (%s IS NULL OR eo.symbol = %s)
                  AND (%s IS NULL OR eo.horizon = %s)
                  AND (%s IS NULL OR eo.status = %s)
                  AND eo.confidence >= %s
                  AND COALESCE((eo.rationale ->> 'edge_score')::double precision, 0.0) >= %s
                ORDER BY edge_score DESC, eo.confidence DESC, eo.updated_at DESC
                LIMIT %s;
                """,
                (symbol, symbol, horizon, horizon, status, status, min_confidence, min_edge_score, limit),
            )
            rows = cur.fetchall()

    items = []
    for row in rows:
        items.append(
            {
                "id": row[0],
                "created_at": row[1].isoformat() if row[1] else None,
                "updated_at": row[2].isoformat() if row[2] else None,
                "symbol": row[3],
                "timeframe": row[4],
                "horizon": row[5],
                "bar_start_ts": row[6].isoformat() if row[6] else None,
                "available_at_ts": row[7].isoformat() if row[7] else None,
                "status": row[8],
                "expected_return": float(row[9]) if row[9] is not None else None,
                "probability_up": float(row[10]) if row[10] is not None else None,
                "confidence": float(row[11]) if row[11] is not None else None,
                "regime_label": row[12],
                "legacy_opportunity_id": row[13],
                "edge_score": float(row[14]) if row[14] is not None else 0.0,
                "model_quality_factor": float(row[15]) if row[15] is not None else 1.0,
                "coverage_quality_factor": float(row[16]) if row[16] is not None else 1.0,
                "regime_quality_factor": float(row[17]) if row[17] is not None else 1.0,
                "model_name": row[18],
                "model_version": row[19],
            }
        )

    return envelope({"opportunities": items, "count": len(items)}, t0)


@router.get("/opportunities/{opp_id}")
def get_equities_opportunity(opp_id: str):
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    eo.id,
                    eo.created_at,
                    eo.updated_at,
                    eo.symbol,
                    eo.timeframe,
                    eo.horizon,
                    eo.bar_start_ts,
                    eo.available_at_ts,
                    eo.status,
                    eo.expected_return,
                    eo.probability_up,
                    eo.confidence,
                    eo.regime_label,
                    COALESCE((eo.rationale ->> 'edge_score')::double precision, 0.0) AS edge_score,
                    COALESCE((eo.rationale ->> 'model_quality_factor')::double precision, 1.0) AS model_quality_factor,
                    COALESCE((eo.rationale ->> 'coverage_quality_factor')::double precision, 1.0) AS coverage_quality_factor,
                    COALESCE((eo.rationale ->> 'regime_quality_factor')::double precision, 1.0) AS regime_quality_factor,
                    eo.rationale,
                    eo.provenance,
                    ep.model_name,
                    ep.model_version,
                    ep.diagnostics
                FROM equities_opportunities eo
                JOIN equities_predictions ep ON ep.id = eo.prediction_id
                WHERE eo.id = %s;
                """,
                (opp_id,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Equities opportunity not found")

    item = {
        "id": row[0],
        "created_at": row[1].isoformat() if row[1] else None,
        "updated_at": row[2].isoformat() if row[2] else None,
        "symbol": row[3],
        "timeframe": row[4],
        "horizon": row[5],
        "bar_start_ts": row[6].isoformat() if row[6] else None,
        "available_at_ts": row[7].isoformat() if row[7] else None,
        "status": row[8],
        "expected_return": float(row[9]) if row[9] is not None else None,
        "probability_up": float(row[10]) if row[10] is not None else None,
        "confidence": float(row[11]) if row[11] is not None else None,
        "regime_label": row[12],
        "edge_score": float(row[13]) if row[13] is not None else 0.0,
        "model_quality_factor": float(row[14]) if row[14] is not None else 1.0,
        "coverage_quality_factor": float(row[15]) if row[15] is not None else 1.0,
        "regime_quality_factor": float(row[16]) if row[16] is not None else 1.0,
        "rationale": row[17],
        "provenance": row[18],
        "model_name": row[19],
        "model_version": row[20],
        "diagnostics": row[21],
    }
    return envelope(item, t0)


@router.get("/opportunities/{opp_id}/explain")
def explain_equities_opportunity(opp_id: str):
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    eo.id,
                    eo.symbol,
                    eo.horizon,
                    eo.confidence,
                    eo.rationale,
                    eo.provenance,
                    ep.feature_refs,
                    ep.event_refs,
                    ep.diagnostics
                FROM equities_opportunities eo
                JOIN equities_predictions ep ON ep.id = eo.prediction_id
                WHERE eo.id = %s;
                """,
                (opp_id,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Equities opportunity not found")

    data = {
        "id": row[0],
        "symbol": row[1],
        "horizon": row[2],
        "confidence": float(row[3]) if row[3] is not None else None,
        "rationale": row[4],
        "provenance": row[5],
        "feature_refs": row[6],
        "event_refs": row[7],
        "diagnostics": row[8],
    }
    return envelope(data, t0)


@router.get("/dashboard/performance")
def equities_performance_dashboard(
    timeframe: Optional[str] = Query(default=None),
    horizon: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ep.timeframe,
                    ep.horizon,
                    ep.symbol,
                    COUNT(*) AS n,
                    AVG(ep.predicted_return) AS avg_pred_return,
                    AVG(ep.predicted_prob_up) AS avg_prob_up,
                    AVG(ep.confidence) AS avg_confidence
                FROM equities_predictions ep
                WHERE (%s IS NULL OR ep.timeframe = %s)
                  AND (%s IS NULL OR ep.horizon = %s)
                GROUP BY ep.timeframe, ep.horizon, ep.symbol
                ORDER BY avg_confidence DESC NULLS LAST, n DESC
                LIMIT %s;
                """,
                (timeframe, timeframe, horizon, horizon, limit),
            )
            by_symbol = [
                {
                    "timeframe": r[0],
                    "horizon": r[1],
                    "symbol": r[2],
                    "count": int(r[3]),
                    "avg_pred_return": float(r[4]) if r[4] is not None else None,
                    "avg_prob_up": float(r[5]) if r[5] is not None else None,
                    "avg_confidence": float(r[6]) if r[6] is not None else None,
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    ep.horizon,
                    COUNT(*) AS n,
                    AVG(ep.predicted_return) AS avg_pred_return,
                    AVG(ep.predicted_prob_up) AS avg_prob_up,
                    AVG(ep.confidence) AS avg_confidence
                FROM equities_predictions ep
                WHERE (%s IS NULL OR ep.timeframe = %s)
                GROUP BY ep.horizon
                ORDER BY ep.horizon;
                """,
                (timeframe, timeframe),
            )
            by_horizon = [
                {
                    "horizon": r[0],
                    "count": int(r[1]),
                    "avg_pred_return": float(r[2]) if r[2] is not None else None,
                    "avg_prob_up": float(r[3]) if r[3] is not None else None,
                    "avg_confidence": float(r[4]) if r[4] is not None else None,
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    COALESCE(NULLIF(eo.regime_label, ''), 'unknown') AS regime_label,
                    eo.horizon,
                    COUNT(*) AS n,
                    AVG(eo.expected_return) AS avg_expected_return,
                    AVG(eo.confidence) AS avg_confidence,
                    AVG(COALESCE((eo.rationale ->> 'edge_score')::double precision, 0.0)) AS avg_edge_score
                FROM equities_opportunities eo
                WHERE (%s IS NULL OR eo.timeframe = %s)
                  AND (%s IS NULL OR eo.horizon = %s)
                GROUP BY regime_label, eo.horizon
                ORDER BY avg_edge_score DESC NULLS LAST, n DESC;
                """,
                (timeframe, timeframe, horizon, horizon),
            )
            by_regime = [
                {
                    "regime_label": r[0],
                    "horizon": r[1],
                    "count": int(r[2]),
                    "avg_expected_return": float(r[3]) if r[3] is not None else None,
                    "avg_confidence": float(r[4]) if r[4] is not None else None,
                    "avg_edge_score": float(r[5]) if r[5] is not None else None,
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    CASE
                        WHEN COALESCE((ep.event_refs -> 'source_coverage' ->> 'source_count_24h')::int, 0) >= 4 THEN '4+'
                        WHEN COALESCE((ep.event_refs -> 'source_coverage' ->> 'source_count_24h')::int, 0) >= 2 THEN '2-3'
                        WHEN COALESCE((ep.event_refs -> 'source_coverage' ->> 'source_count_24h')::int, 0) = 1 THEN '1'
                        ELSE '0'
                    END AS source_bucket,
                    ep.horizon,
                    COUNT(*) AS n,
                    AVG(ep.confidence) AS avg_confidence,
                    AVG(ep.predicted_return) AS avg_pred_return,
                    AVG(COALESCE((ep.diagnostics ->> 'edge_score')::double precision, 0.0)) AS avg_edge_score
                FROM equities_predictions ep
                WHERE (%s IS NULL OR ep.timeframe = %s)
                  AND (%s IS NULL OR ep.horizon = %s)
                GROUP BY source_bucket, ep.horizon
                ORDER BY avg_edge_score DESC NULLS LAST, n DESC;
                """,
                (timeframe, timeframe, horizon, horizon),
            )
            by_source_coverage = [
                {
                    "source_bucket": r[0],
                    "horizon": r[1],
                    "count": int(r[2]),
                    "avg_confidence": float(r[3]) if r[3] is not None else None,
                    "avg_pred_return": float(r[4]) if r[4] is not None else None,
                    "avg_edge_score": float(r[5]) if r[5] is not None else None,
                }
                for r in cur.fetchall()
            ]

    return envelope(
        {
            "by_symbol": by_symbol,
            "by_horizon": by_horizon,
            "by_regime": by_regime,
            "by_source_coverage": by_source_coverage,
        },
        t0,
    )


@router.get("/dashboard/realized")
def equities_realized_dashboard(
    timeframe: Optional[str] = Query(default=None),
    horizon: Optional[str] = Query(default=None),
    min_edge_score: float = Query(default=0.0, ge=0.0),
    limit: int = Query(default=250, ge=1, le=2000),
):
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    o.timeframe,
                    o.horizon,
                    COUNT(*) AS n,
                    AVG(o.realized_return) AS avg_realized_return,
                    AVG(o.directional_realized_return) AS avg_directional_return,
                    AVG(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END) AS hit_rate,
                    AVG(o.brier_loss) AS avg_brier_loss,
                    AVG(o.log_loss) AS avg_log_loss
                FROM equities_opportunity_outcomes o
                WHERE (%s::text IS NULL OR o.timeframe = %s::text)
                  AND (%s::text IS NULL OR o.horizon = %s::text)
                  AND COALESCE(o.edge_score, 0.0) >= %s
                GROUP BY o.timeframe, o.horizon
                ORDER BY o.timeframe, o.horizon;
                """,
                (timeframe, timeframe, horizon, horizon, min_edge_score),
            )
            by_horizon = [
                {
                    "timeframe": r[0],
                    "horizon": r[1],
                    "count": int(r[2]),
                    "avg_realized_return": float(r[3]) if r[3] is not None else None,
                    "avg_directional_return": float(r[4]) if r[4] is not None else None,
                    "hit_rate": float(r[5]) if r[5] is not None else None,
                    "avg_brier_loss": float(r[6]) if r[6] is not None else None,
                    "avg_log_loss": float(r[7]) if r[7] is not None else None,
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    o.symbol,
                    o.horizon,
                    COUNT(*) AS n,
                    AVG(o.directional_realized_return) AS avg_directional_return,
                    AVG(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END) AS hit_rate,
                    AVG(o.brier_loss) AS avg_brier_loss
                FROM equities_opportunity_outcomes o
                WHERE (%s::text IS NULL OR o.timeframe = %s::text)
                  AND (%s::text IS NULL OR o.horizon = %s::text)
                  AND COALESCE(o.edge_score, 0.0) >= %s
                GROUP BY o.symbol, o.horizon
                ORDER BY avg_directional_return DESC NULLS LAST, n DESC
                LIMIT %s;
                """,
                (timeframe, timeframe, horizon, horizon, min_edge_score, limit),
            )
            by_symbol = [
                {
                    "symbol": r[0],
                    "horizon": r[1],
                    "count": int(r[2]),
                    "avg_directional_return": float(r[3]) if r[3] is not None else None,
                    "hit_rate": float(r[4]) if r[4] is not None else None,
                    "avg_brier_loss": float(r[5]) if r[5] is not None else None,
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    CASE
                        WHEN o.confidence >= 0.80 THEN '0.80-1.00'
                        WHEN o.confidence >= 0.60 THEN '0.60-0.79'
                        WHEN o.confidence >= 0.40 THEN '0.40-0.59'
                        WHEN o.confidence >= 0.20 THEN '0.20-0.39'
                        ELSE '0.00-0.19'
                    END AS confidence_bucket,
                    COUNT(*) AS n,
                    AVG(o.directional_realized_return) AS avg_directional_return,
                    AVG(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END) AS hit_rate,
                    AVG(o.brier_loss) AS avg_brier_loss
                FROM equities_opportunity_outcomes o
                WHERE (%s::text IS NULL OR o.timeframe = %s::text)
                  AND (%s::text IS NULL OR o.horizon = %s::text)
                  AND COALESCE(o.edge_score, 0.0) >= %s
                GROUP BY confidence_bucket
                ORDER BY confidence_bucket DESC;
                """,
                (timeframe, timeframe, horizon, horizon, min_edge_score),
            )
            by_confidence_bucket = [
                {
                    "confidence_bucket": r[0],
                    "count": int(r[1]),
                    "avg_directional_return": float(r[2]) if r[2] is not None else None,
                    "hit_rate": float(r[3]) if r[3] is not None else None,
                    "avg_brier_loss": float(r[4]) if r[4] is not None else None,
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status='active') AS active_opportunities,
                    COUNT(*) FILTER (WHERE status='settled') AS settled_opportunities
                FROM equities_opportunities
                WHERE (%s::text IS NULL OR timeframe = %s::text)
                  AND (%s::text IS NULL OR horizon = %s::text);
                """,
                (timeframe, timeframe, horizon, horizon),
            )
            cov = cur.fetchone()
            settlement_coverage = {
                "active_opportunities": int(cov[0] or 0),
                "settled_opportunities": int(cov[1] or 0),
            }

    return envelope(
        {
            "by_horizon": by_horizon,
            "by_symbol": by_symbol,
            "by_confidence_bucket": by_confidence_bucket,
            "settlement_coverage": settlement_coverage,
        },
        t0,
    )


@router.get("/dashboard/policy")
def equities_policy_dashboard(
    timeframe: Optional[str] = Query(default=None),
    horizon: Optional[str] = Query(default=None),
    alerts_lookback_days: int = Query(default=14, ge=1, le=365),
    limit: int = Query(default=200, ge=1, le=2000),
):
    t0 = time.time()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (timeframe, horizon)
                    id, created_at, actor, timeframe, horizon,
                    min_confidence, min_edge_score, directional_prob_floor, expected_return_floor,
                    min_samples, transaction_cost_bps, objective_score, metrics, notes
                FROM equities_selection_policies
                WHERE (%s::text IS NULL OR timeframe = %s::text)
                  AND (%s::text IS NULL OR horizon = %s::text)
                ORDER BY timeframe, horizon, created_at DESC
                LIMIT %s;
                """,
                (timeframe, timeframe, horizon, horizon, limit),
            )
            policies = [
                {
                    "id": r[0],
                    "created_at": r[1].isoformat() if r[1] else None,
                    "actor": r[2],
                    "timeframe": r[3],
                    "horizon": r[4],
                    "min_confidence": float(r[5]) if r[5] is not None else None,
                    "min_edge_score": float(r[6]) if r[6] is not None else None,
                    "directional_prob_floor": float(r[7]) if r[7] is not None else None,
                    "expected_return_floor": float(r[8]) if r[8] is not None else None,
                    "min_samples": int(r[9]) if r[9] is not None else None,
                    "transaction_cost_bps": float(r[10]) if r[10] is not None else None,
                    "objective_score": float(r[11]) if r[11] is not None else None,
                    "metrics": r[12],
                    "notes": r[13],
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    id, created_at, actor, timeframe, horizon, severity, alert_type, message, data
                FROM equities_calibration_alerts
                WHERE created_at >= NOW() - make_interval(days => %s)
                  AND (%s::text IS NULL OR timeframe = %s::text)
                  AND (%s::text IS NULL OR horizon = %s::text)
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (alerts_lookback_days, timeframe, timeframe, horizon, horizon, limit),
            )
            alerts = [
                {
                    "id": r[0],
                    "created_at": r[1].isoformat() if r[1] else None,
                    "actor": r[2],
                    "timeframe": r[3],
                    "horizon": r[4],
                    "severity": r[5],
                    "alert_type": r[6],
                    "message": r[7],
                    "data": r[8],
                }
                for r in cur.fetchall()
            ]

    return envelope(
        {
            "policies": policies,
            "alerts": alerts,
            "policy_count": len(policies),
            "alert_count": len(alerts),
        },
        t0,
    )


@router.get("/pipeline/health")
def equities_pipeline_health(limit: int = Query(default=100, ge=1, le=1000)):
    t0 = time.time()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ts, actor, event_type, message, data
                FROM equities_pipeline_events
                ORDER BY ts DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    events = []
    for r in rows:
        events.append(
            {
                "ts": r[0].isoformat() if r[0] else None,
                "actor": r[1],
                "event_type": r[2],
                "message": r[3],
                "data": r[4],
            }
        )

    return envelope({"events": events, "count": len(events)}, t0)
