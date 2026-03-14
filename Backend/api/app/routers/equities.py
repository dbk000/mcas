"""
Equities API
=============

Equities forward predictor endpoints + equities simulation runner.

Opportunities, explain, performance dashboard, realized dashboard,
policy dashboard, pipeline health, and simulation endpoints.
"""

from __future__ import annotations

import re
import time
import json
import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from api.app.db_pool import get_conn as _pool_get_conn
from governance.bootstrap import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/equities", tags=["equities-forward-predictor"])

EQ_SETUP_IDS = ["EQ_TSMOM", "EQ_XSMOM", "EQ_REV1A", "EQ_REV1B", "EQ_VPD", "EQ_ATC", "EQ_NVDA_MACRO"]
DEFAULT_COST_BPS = 3.0


def get_conn():
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


# =============================================================================
# Opportunity Endpoints
# =============================================================================

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
                    eo.id, eo.created_at, eo.updated_at, eo.symbol, eo.timeframe,
                    eo.horizon, eo.bar_start_ts, eo.available_at_ts, eo.status,
                    eo.expected_return, eo.probability_up, eo.confidence,
                    eo.regime_label, eo.legacy_opportunity_id,
                    COALESCE((eo.rationale ->> 'edge_score')::double precision, 0.0) AS edge_score,
                    COALESCE((eo.rationale ->> 'model_quality_factor')::double precision, 1.0) AS model_quality_factor,
                    COALESCE((eo.rationale ->> 'coverage_quality_factor')::double precision, 1.0) AS coverage_quality_factor,
                    COALESCE((eo.rationale ->> 'regime_quality_factor')::double precision, 1.0) AS regime_quality_factor,
                    ep.model_name, ep.model_version
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
        items.append({
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
        })

    return envelope({"opportunities": items, "count": len(items)}, t0)


@router.get("/opportunities/{opp_id}")
def get_equities_opportunity(opp_id: str):
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    eo.id, eo.created_at, eo.updated_at, eo.symbol, eo.timeframe,
                    eo.horizon, eo.bar_start_ts, eo.available_at_ts, eo.status,
                    eo.expected_return, eo.probability_up, eo.confidence,
                    eo.regime_label,
                    COALESCE((eo.rationale ->> 'edge_score')::double precision, 0.0) AS edge_score,
                    COALESCE((eo.rationale ->> 'model_quality_factor')::double precision, 1.0),
                    COALESCE((eo.rationale ->> 'coverage_quality_factor')::double precision, 1.0),
                    COALESCE((eo.rationale ->> 'regime_quality_factor')::double precision, 1.0),
                    eo.rationale, eo.provenance,
                    ep.model_name, ep.model_version, ep.diagnostics
                FROM equities_opportunities eo
                JOIN equities_predictions ep ON ep.id = eo.prediction_id
                WHERE eo.id = %s;
                """,
                (opp_id,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Equities opportunity not found")

    return envelope({
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
    }, t0)


@router.get("/opportunities/{opp_id}/explain")
def explain_equities_opportunity(opp_id: str):
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    eo.id, eo.symbol, eo.horizon, eo.confidence,
                    eo.rationale, eo.provenance,
                    ep.feature_refs, ep.event_refs, ep.diagnostics
                FROM equities_opportunities eo
                JOIN equities_predictions ep ON ep.id = eo.prediction_id
                WHERE eo.id = %s;
                """,
                (opp_id,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Equities opportunity not found")

    return envelope({
        "id": row[0],
        "symbol": row[1],
        "horizon": row[2],
        "confidence": float(row[3]) if row[3] is not None else None,
        "rationale": row[4],
        "provenance": row[5],
        "feature_refs": row[6],
        "event_refs": row[7],
        "diagnostics": row[8],
    }, t0)


# =============================================================================
# Dashboards
# =============================================================================

@router.get("/dashboard/performance")
def equities_performance_dashboard(
    timeframe: Optional[str] = Query(default=None),
    horizon: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ep.timeframe, ep.horizon, ep.symbol, COUNT(*) AS n,
                       AVG(ep.predicted_return), AVG(ep.predicted_prob_up), AVG(ep.confidence)
                FROM equities_predictions ep
                WHERE (%s IS NULL OR ep.timeframe = %s) AND (%s IS NULL OR ep.horizon = %s)
                GROUP BY ep.timeframe, ep.horizon, ep.symbol
                ORDER BY AVG(ep.confidence) DESC NULLS LAST, n DESC LIMIT %s;
            """, (timeframe, timeframe, horizon, horizon, limit))
            by_symbol = [
                {"timeframe": r[0], "horizon": r[1], "symbol": r[2], "count": int(r[3]),
                 "avg_pred_return": float(r[4]) if r[4] else None,
                 "avg_prob_up": float(r[5]) if r[5] else None,
                 "avg_confidence": float(r[6]) if r[6] else None}
                for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT ep.horizon, COUNT(*), AVG(ep.predicted_return), AVG(ep.predicted_prob_up), AVG(ep.confidence)
                FROM equities_predictions ep WHERE (%s IS NULL OR ep.timeframe = %s)
                GROUP BY ep.horizon ORDER BY ep.horizon;
            """, (timeframe, timeframe))
            by_horizon = [
                {"horizon": r[0], "count": int(r[1]),
                 "avg_pred_return": float(r[2]) if r[2] else None,
                 "avg_prob_up": float(r[3]) if r[3] else None,
                 "avg_confidence": float(r[4]) if r[4] else None}
                for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT COALESCE(NULLIF(eo.regime_label, ''), 'unknown'), eo.horizon, COUNT(*),
                       AVG(eo.expected_return), AVG(eo.confidence),
                       AVG(COALESCE((eo.rationale ->> 'edge_score')::double precision, 0.0))
                FROM equities_opportunities eo
                WHERE (%s IS NULL OR eo.timeframe = %s) AND (%s IS NULL OR eo.horizon = %s)
                GROUP BY 1, eo.horizon ORDER BY 6 DESC NULLS LAST, 3 DESC;
            """, (timeframe, timeframe, horizon, horizon))
            by_regime = [
                {"regime_label": r[0], "horizon": r[1], "count": int(r[2]),
                 "avg_expected_return": float(r[3]) if r[3] else None,
                 "avg_confidence": float(r[4]) if r[4] else None,
                 "avg_edge_score": float(r[5]) if r[5] else None}
                for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT CASE
                    WHEN COALESCE((ep.event_refs -> 'source_coverage' ->> 'source_count_24h')::int, 0) >= 4 THEN '4+'
                    WHEN COALESCE((ep.event_refs -> 'source_coverage' ->> 'source_count_24h')::int, 0) >= 2 THEN '2-3'
                    WHEN COALESCE((ep.event_refs -> 'source_coverage' ->> 'source_count_24h')::int, 0) = 1 THEN '1'
                    ELSE '0' END AS source_bucket,
                    ep.horizon, COUNT(*), AVG(ep.confidence), AVG(ep.predicted_return),
                    AVG(COALESCE((ep.diagnostics ->> 'edge_score')::double precision, 0.0))
                FROM equities_predictions ep
                WHERE (%s IS NULL OR ep.timeframe = %s) AND (%s IS NULL OR ep.horizon = %s)
                GROUP BY source_bucket, ep.horizon ORDER BY 6 DESC NULLS LAST, 3 DESC;
            """, (timeframe, timeframe, horizon, horizon))
            by_source_coverage = [
                {"source_bucket": r[0], "horizon": r[1], "count": int(r[2]),
                 "avg_confidence": float(r[3]) if r[3] else None,
                 "avg_pred_return": float(r[4]) if r[4] else None,
                 "avg_edge_score": float(r[5]) if r[5] else None}
                for r in cur.fetchall()
            ]

    return envelope({"by_symbol": by_symbol, "by_horizon": by_horizon,
                     "by_regime": by_regime, "by_source_coverage": by_source_coverage}, t0)


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
            cur.execute("""
                SELECT o.timeframe, o.horizon, COUNT(*), AVG(o.realized_return),
                       AVG(o.directional_realized_return), AVG(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END),
                       AVG(o.brier_loss), AVG(o.log_loss)
                FROM equities_opportunity_outcomes o
                WHERE (%s::text IS NULL OR o.timeframe = %s::text) AND (%s::text IS NULL OR o.horizon = %s::text)
                  AND COALESCE(o.edge_score, 0.0) >= %s
                GROUP BY o.timeframe, o.horizon ORDER BY o.timeframe, o.horizon;
            """, (timeframe, timeframe, horizon, horizon, min_edge_score))
            by_horizon = [
                {"timeframe": r[0], "horizon": r[1], "count": int(r[2]),
                 "avg_realized_return": float(r[3]) if r[3] else None,
                 "avg_directional_return": float(r[4]) if r[4] else None,
                 "hit_rate": float(r[5]) if r[5] else None,
                 "avg_brier_loss": float(r[6]) if r[6] else None,
                 "avg_log_loss": float(r[7]) if r[7] else None}
                for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT o.symbol, o.horizon, COUNT(*), AVG(o.directional_realized_return),
                       AVG(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END), AVG(o.brier_loss)
                FROM equities_opportunity_outcomes o
                WHERE (%s::text IS NULL OR o.timeframe = %s::text) AND (%s::text IS NULL OR o.horizon = %s::text)
                  AND COALESCE(o.edge_score, 0.0) >= %s
                GROUP BY o.symbol, o.horizon ORDER BY 4 DESC NULLS LAST, 3 DESC LIMIT %s;
            """, (timeframe, timeframe, horizon, horizon, min_edge_score, limit))
            by_symbol = [
                {"symbol": r[0], "horizon": r[1], "count": int(r[2]),
                 "avg_directional_return": float(r[3]) if r[3] else None,
                 "hit_rate": float(r[4]) if r[4] else None,
                 "avg_brier_loss": float(r[5]) if r[5] else None}
                for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT CASE WHEN o.confidence >= 0.80 THEN '0.80-1.00' WHEN o.confidence >= 0.60 THEN '0.60-0.79'
                    WHEN o.confidence >= 0.40 THEN '0.40-0.59' WHEN o.confidence >= 0.20 THEN '0.20-0.39'
                    ELSE '0.00-0.19' END, COUNT(*), AVG(o.directional_realized_return),
                    AVG(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END), AVG(o.brier_loss)
                FROM equities_opportunity_outcomes o
                WHERE (%s::text IS NULL OR o.timeframe = %s::text) AND (%s::text IS NULL OR o.horizon = %s::text)
                  AND COALESCE(o.edge_score, 0.0) >= %s
                GROUP BY 1 ORDER BY 1 DESC;
            """, (timeframe, timeframe, horizon, horizon, min_edge_score))
            by_confidence_bucket = [
                {"confidence_bucket": r[0], "count": int(r[1]),
                 "avg_directional_return": float(r[2]) if r[2] else None,
                 "hit_rate": float(r[3]) if r[3] else None,
                 "avg_brier_loss": float(r[4]) if r[4] else None}
                for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE status='active'), COUNT(*) FILTER (WHERE status='settled')
                FROM equities_opportunities
                WHERE (%s::text IS NULL OR timeframe = %s::text) AND (%s::text IS NULL OR horizon = %s::text);
            """, (timeframe, timeframe, horizon, horizon))
            cov = cur.fetchone()
            settlement_coverage = {
                "active_opportunities": int(cov[0] or 0),
                "settled_opportunities": int(cov[1] or 0),
            }

    return envelope({"by_horizon": by_horizon, "by_symbol": by_symbol,
                     "by_confidence_bucket": by_confidence_bucket,
                     "settlement_coverage": settlement_coverage}, t0)


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
            cur.execute("""
                SELECT DISTINCT ON (timeframe, horizon)
                    id, created_at, actor, timeframe, horizon,
                    min_confidence, min_edge_score, directional_prob_floor, expected_return_floor,
                    min_samples, transaction_cost_bps, objective_score, metrics, notes
                FROM equities_selection_policies
                WHERE (%s::text IS NULL OR timeframe = %s::text) AND (%s::text IS NULL OR horizon = %s::text)
                ORDER BY timeframe, horizon, created_at DESC LIMIT %s;
            """, (timeframe, timeframe, horizon, horizon, limit))
            policies = [
                {"id": r[0], "created_at": r[1].isoformat() if r[1] else None, "actor": r[2],
                 "timeframe": r[3], "horizon": r[4],
                 "min_confidence": float(r[5]) if r[5] is not None else None,
                 "min_edge_score": float(r[6]) if r[6] is not None else None,
                 "directional_prob_floor": float(r[7]) if r[7] is not None else None,
                 "expected_return_floor": float(r[8]) if r[8] is not None else None,
                 "min_samples": int(r[9]) if r[9] is not None else None,
                 "transaction_cost_bps": float(r[10]) if r[10] is not None else None,
                 "objective_score": float(r[11]) if r[11] is not None else None,
                 "metrics": r[12], "notes": r[13]}
                for r in cur.fetchall()
            ]

            cur.execute("""
                SELECT id, created_at, actor, timeframe, horizon, severity, alert_type, message, data
                FROM equities_calibration_alerts
                WHERE created_at >= NOW() - make_interval(days => %s)
                  AND (%s::text IS NULL OR timeframe = %s::text) AND (%s::text IS NULL OR horizon = %s::text)
                ORDER BY created_at DESC LIMIT %s;
            """, (alerts_lookback_days, timeframe, timeframe, horizon, horizon, limit))
            alerts = [
                {"id": r[0], "created_at": r[1].isoformat() if r[1] else None,
                 "actor": r[2], "timeframe": r[3], "horizon": r[4],
                 "severity": r[5], "alert_type": r[6], "message": r[7], "data": r[8]}
                for r in cur.fetchall()
            ]

    return envelope({"policies": policies, "alerts": alerts,
                     "policy_count": len(policies), "alert_count": len(alerts)}, t0)


@router.get("/pipeline/health")
def equities_pipeline_health(limit: int = Query(default=100, ge=1, le=1000)):
    t0 = time.time()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ts, actor, event_type, message, data
                FROM equities_pipeline_events ORDER BY ts DESC LIMIT %s;
            """, (limit,))
            rows = cur.fetchall()

    events = [{"ts": r[0].isoformat() if r[0] else None, "actor": r[1],
               "event_type": r[2], "message": r[3], "data": r[4]} for r in rows]

    return envelope({"events": events, "count": len(events)}, t0)


# =============================================================================
# Equities Simulation Runner
# =============================================================================

class EquitiesSimulationRequest(BaseModel):
    """Request to simulate expired equities opportunities."""
    name: Optional[str] = None
    setup_ids: Optional[list[str]] = None
    cost_bps: float = Field(default=DEFAULT_COST_BPS, ge=0, le=50)
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    include_dates: Optional[list[str]] = None
    exclude_dates: Optional[list[str]] = None
    limit: int = Field(default=5000, ge=1, le=50000)

    @model_validator(mode="after")
    def validate_filters(self):
        _iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")

        def _check(val, field):
            if not _iso_re.match(val):
                raise ValueError(f"{field} must be YYYY-MM-DD, got '{val}'")

        if self.include_dates is not None and len(self.include_dates) == 0:
            self.include_dates = None
        if self.exclude_dates is not None and len(self.exclude_dates) == 0:
            self.exclude_dates = None

        for lst, name in [(self.include_dates, "include_dates"),
                          (self.exclude_dates, "exclude_dates")]:
            if lst:
                for d in lst:
                    _check(d, name)

        if self.from_date:
            _check(self.from_date, "from_date")
        if self.to_date:
            _check(self.to_date, "to_date")

        if self.setup_ids:
            for sid in self.setup_ids:
                if not sid.startswith("EQ_"):
                    raise ValueError(f"Equities simulation only accepts EQ_* setup_ids, got '{sid}'")
        return self


def _simulate_opportunity(cur, opp, cost_bps: float) -> dict:
    """Simulate a single equities opportunity."""
    opp_id = opp["id"]
    symbol = opp["symbol"]
    setup_id = opp["setup_id"]
    created_ts = opp["created_ts"]

    payload = opp["payload"] if isinstance(opp["payload"], dict) else json.loads(opp["payload"])
    direction = payload.get("direction", "LONG")
    horizon_days = payload.get("horizon_days", 20)
    entry_price = payload.get("entry_price")

    if not entry_price or entry_price <= 0:
        return {
            "opportunity_id": opp_id, "symbol": symbol, "timeframe": "1d",
            "setup_id": setup_id, "direction": direction,
            "opportunity_created_ts": created_ts,
            "data_available": False, "error_message": "Missing or invalid entry_price in payload",
        }

    cur.execute("""
        SELECT bar_start_ts, close FROM ohlcv
        WHERE symbol = %s AND timeframe = '1d' AND source = 'equities_yahoo'
          AND bar_start_ts > %s
        ORDER BY bar_start_ts ASC LIMIT %s
    """, (symbol, created_ts, horizon_days))

    bars = cur.fetchall()

    if not bars:
        return {
            "opportunity_id": opp_id, "symbol": symbol, "timeframe": "1d",
            "setup_id": setup_id, "direction": direction,
            "opportunity_created_ts": created_ts,
            "data_available": False, "error_message": f"No OHLCV bars found after {created_ts}",
        }

    exit_bar = bars[-1]
    exit_ts = exit_bar[0]
    exit_price = float(exit_bar[1])
    bars_held = len(bars)

    dir_mult = 1.0 if direction == "LONG" else -1.0
    pnl_gross_pct = dir_mult * (exit_price / entry_price - 1.0)
    cost_pct = cost_bps / 10000.0
    pnl_net_pct = pnl_gross_pct - cost_pct

    return {
        "opportunity_id": opp_id, "symbol": symbol, "timeframe": "1d",
        "setup_id": setup_id, "direction": direction,
        "opportunity_created_ts": created_ts,
        "entry_ts": created_ts, "entry_price": entry_price,
        "entry_price_with_slippage": entry_price, "position_size": 1.0,
        "stop_price": None, "tp1_price": None, "tp2_price": None, "r_value": None,
        "exit_ts": exit_ts, "exit_price": exit_price,
        "exit_price_with_slippage": exit_price, "exit_reason": "HORIZON_EXIT",
        "bars_held": bars_held,
        "pnl_gross": round(pnl_gross_pct, 8),
        "fees": round(cost_pct, 8),
        "pnl_net": round(pnl_net_pct, 8),
        "r_multiple": None, "data_available": True, "error_message": None,
    }


@router.post("/simulation")
def create_equities_simulation(request: EquitiesSimulationRequest):
    """Simulate expired equities opportunities using fixed-horizon exit model."""
    t0 = time.time()
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    setup_ids = request.setup_ids or EQ_SETUP_IDS
    conditions = ["status = 'expired'", "setup_id = ANY(%s)"]
    params: list = [setup_ids]

    if request.include_dates:
        conditions.append("created_ts::date = ANY(%s::date[])")
        params.append(request.include_dates)
    else:
        if request.from_date:
            conditions.append("created_ts >= %s::date")
            params.append(request.from_date)
        if request.to_date:
            conditions.append("created_ts < (%s::date + INTERVAL '1 day')")
            params.append(request.to_date)

    if request.exclude_dates:
        conditions.append("NOT (created_ts::date = ANY(%s::date[]))")
        params.append(request.exclude_dates)

    where_clause = " AND ".join(conditions)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, symbol, setup_id, created_ts, payload_json
                FROM opportunities WHERE {where_clause}
                ORDER BY created_ts ASC LIMIT %s
            """, params + [request.limit])
            rows = cur.fetchall()

            if not rows:
                raise HTTPException(status_code=400, detail="No expired equities opportunities found")

            opportunities = []
            for row in rows:
                payload_raw = row[4]
                payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                opportunities.append({
                    "id": row[0], "symbol": row[1], "setup_id": row[2],
                    "created_ts": row[3], "payload": payload,
                })

            results = [_simulate_opportunity(cur, opp, request.cost_bps) for opp in opportunities]

            strategy_json = json.dumps({"setup_id": ",".join(setup_ids),
                "setup_params": {"setup_ids": setup_ids, "cost_bps": request.cost_bps,
                    "from_date": request.from_date, "to_date": request.to_date, "limit": request.limit}})
            execution_json = json.dumps({"entry_mode": "MARKET_CLOSE", "exit_mode": "HORIZON", "cost_bps": request.cost_bps})
            data_json = json.dumps({"data_source": "equities_simulation", "data_snapshot_ts": now.isoformat()})

            fp_input = f"{strategy_json}{execution_json}{run_id}"
            fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]
            name = request.name or f"eq_sim_{now.strftime('%Y%m%d_%H%M%S')}"

            cur.execute("""
                INSERT INTO runs (id, run_type, status, name, fingerprint, engine_version,
                    universe_json, strategy_json, execution_json, data_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (run_id, "SIMULATION", "COMPLETED", name, fingerprint, "1.0.0",
                  json.dumps({"venue": "equities_yahoo"}), strategy_json, execution_json, data_json, now))

            if results:
                cols = ["run_id", "opportunity_id", "symbol", "timeframe", "setup_id", "direction",
                    "opportunity_created_ts", "entry_ts", "entry_price", "entry_price_with_slippage",
                    "position_size", "stop_price", "tp1_price", "tp2_price", "r_value", "exit_ts",
                    "exit_price", "exit_price_with_slippage", "exit_reason", "bars_held",
                    "pnl_gross", "fees", "pnl_net", "r_multiple", "data_available", "error_message"]
                values_template = ", ".join(["%s"] * len(cols))
                insert_sql = f"INSERT INTO simulation_results ({', '.join(cols)}) VALUES ({values_template})"

                batch = []
                for r_item in results:
                    row_vals = [run_id] + [r_item.get(col) for col in cols[1:]]
                    batch.append(row_vals)

                for i in range(0, len(batch), 500):
                    cur.executemany(insert_sql, batch[i:i + 500])

            cur.execute("UPDATE runs SET status = 'COMPLETED', finished_at = NOW() WHERE id = %s", (run_id,))
        conn.commit()

    data_available = [res for res in results if res.get("data_available")]
    no_data = len(results) - len(data_available)
    winners = [res for res in data_available if (res.get("pnl_net") or 0) > 0]
    total_pnl = sum(res.get("pnl_net", 0) for res in data_available)

    by_strategy = {}
    for r_item in data_available:
        sid = r_item["setup_id"]
        if sid not in by_strategy:
            by_strategy[sid] = {"count": 0, "wins": 0, "pnl_sum": 0.0}
        by_strategy[sid]["count"] += 1
        by_strategy[sid]["pnl_sum"] += r_item.get("pnl_net", 0)
        if (r_item.get("pnl_net") or 0) > 0:
            by_strategy[sid]["wins"] += 1

    strategy_summary = {
        sid: {"trades": s["count"], "wins": s["wins"],
              "win_rate": round(s["wins"] / max(s["count"], 1), 3),
              "total_pnl_pct": round(s["pnl_sum"] * 100, 4)}
        for sid, s in by_strategy.items()
    }

    return envelope({
        "run_id": run_id, "status": "COMPLETED", "name": name,
        "total_opportunities": len(results), "data_available": len(data_available),
        "no_data": no_data, "winners": len(winners), "losers": len(data_available) - len(winners),
        "win_rate": round(len(winners) / max(len(data_available), 1), 3),
        "total_pnl_pct": round(total_pnl * 100, 4),
        "avg_pnl_pct": round(total_pnl / max(len(data_available), 1) * 100, 4),
        "cost_bps": request.cost_bps, "by_strategy": strategy_summary,
        "message": f"Simulated {len(data_available)} equities opportunities ({no_data} missing data)",
    }, t0)
