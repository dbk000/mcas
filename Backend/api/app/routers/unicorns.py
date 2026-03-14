"""
Unicorns API
============

Endpoints for Unicorn pipeline data:
- GET /unicorns/embryo-active - Ranked list of EMBRYO_ACTIVE candidates
- GET /unicorns/{symbol} - Full explainability payload for a symbol
- GET /unicorns/pipeline-status - Pipeline metadata and health
"""

import time
import json
from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Query, HTTPException

from governance.bootstrap import get_config

router = APIRouter(prefix="/unicorns", tags=["Unicorns"])

_worker_cfg = get_config().Worker
r = redis.Redis.from_url(_worker_cfg.REDIS_URL, decode_responses=True)


def envelope(data, compute_start: float) -> dict:
    """Standard response envelope."""
    return {
        "data": data,
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
            "compute_ms": int((time.time() - compute_start) * 1000),
        }
    }


def _parse_state(raw: dict) -> dict:
    """Parse Redis state hash to typed dict."""
    result = {}
    for k, v in raw.items():
        try:
            result[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            try:
                if '.' in str(v):
                    result[k] = float(v)
                elif str(v).isdigit():
                    result[k] = int(v)
                else:
                    result[k] = v
            except (ValueError, TypeError):
                result[k] = v
    return result


@router.get("/embryo-active")
def get_embryo_active(
    limit: int = Query(default=50, ge=1, le=100),
):
    """Get ranked list of EMBRYO_ACTIVE candidates."""
    t0 = time.time()

    zset_key = "unicorn:embryo_active"
    top = r.zrevrange(zset_key, 0, limit - 1, withscores=True)

    candidates = []
    for symbol, score in top:
        state_key = f"unicorn:state:{symbol}"
        state_raw = r.hgetall(state_key)
        state = _parse_state(state_raw) if state_raw else {}

        metrics_key = f"unicorn:metrics:{symbol}"
        metrics_raw = r.hgetall(metrics_key)
        metrics = _parse_state(metrics_raw) if metrics_raw else {}

        hours_active = 0.0
        promoted_at = state.get("promoted_at")
        if promoted_at:
            try:
                if isinstance(promoted_at, str):
                    promoted_dt = datetime.fromisoformat(promoted_at.replace("Z", "+00:00"))
                    hours_active = (datetime.now(timezone.utc) - promoted_dt).total_seconds() / 3600
            except Exception:
                pass

        gates_passed = []
        if state.get("gate1_passed"):
            gates_passed.append("Gate1_TradabilityProxy")
        if state.get("gate2_passed"):
            gates_passed.append("Gate2_ParticipationProxy")
        if state.get("gate3_passed"):
            gates_passed.append("Gate3_Compression")
        if state.get("gate4_passed") is True:
            gates_passed.append("Gate4_HolderIntegrity")

        candidates.append({
            "symbol": symbol,
            "embryo_active_score": score,
            "tradability_score": state.get("tradability_score", 0),
            "participation_score": state.get("participation_score", 0),
            "compression_score": state.get("compression_score", 0),
            "trap_risk_score": state.get("trap_risk_score", 0),
            "phase": state.get("phase", "EMBRYO_ACTIVE"),
            "promoted_at": promoted_at,
            "hours_active": round(hours_active, 1),
            "gates_passed": gates_passed,
            "compression_box_high": state.get("compression_box_high"),
            "compression_box_low": state.get("compression_box_low"),
            "rel_volume_baseline": state.get("rel_volume_baseline"),
        })

    max_cap = 50

    return envelope({
        "count": len(candidates),
        "max_cap": max_cap,
        "as_of_ts": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates,
    }, t0)


@router.get("/pipeline-status")
def get_pipeline_status():
    """Get pipeline metadata and health."""
    t0 = time.time()

    meta_key = "unicorn:pipeline_meta"
    meta = r.hgetall(meta_key)

    universe_key = "unicorn:embryo_universe"
    universe_count = r.scard(universe_key)

    active_key = "unicorn:embryo_active"
    active_count = r.zcard(active_key)

    return envelope({
        "universe_count": universe_count,
        "active_count": active_count,
        "max_cap": 50,
        "last_cycle_ts": meta.get("last_cycle_ts"),
        "symbols_evaluated": int(meta.get("symbols_evaluated", 0)),
        "promoted_count": int(meta.get("promoted_count", 0)),
        "demoted_count": int(meta.get("demoted_count", 0)),
        "cycle_duration_s": float(meta.get("cycle_duration_s", 0)),
    }, t0)


@router.get("/{symbol}")
def get_unicorn_detail(symbol: str):
    """Get full explainability payload for a symbol."""
    t0 = time.time()

    state_key = f"unicorn:state:{symbol}"
    state_raw = r.hgetall(state_key)

    if not state_raw:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found in Unicorn pipeline")

    state = _parse_state(state_raw)

    metrics_key = f"unicorn:metrics:{symbol}"
    metrics_raw = r.hgetall(metrics_key)
    metrics = _parse_state(metrics_raw) if metrics_raw else {}

    active_key = "unicorn:embryo_active"
    embryo_score = r.zscore(active_key, symbol)

    gates = {
        "gate1_tradability_proxy": {
            "passed": state.get("gate1_passed", False),
            "metrics": {
                "spread_proxy_last": metrics.get("spread_proxy_last"),
                "spread_proxy_prev": metrics.get("spread_proxy_prev"),
                "spread_improving": metrics.get("spread_improving"),
                "vol_cv_last": metrics.get("vol_cv_last"),
                "vol_cv_prev": metrics.get("vol_cv_prev"),
                "volume_stabilizing": metrics.get("volume_stabilizing"),
                "atr_pct_last": metrics.get("atr_pct_last"),
                "atr_pct_prev": metrics.get("atr_pct_prev"),
                "atr_compressing": metrics.get("atr_compressing"),
            }
        },
        "gate2_participation_proxy": {
            "passed": state.get("gate2_passed", False),
            "metrics": {
                "vol_sum_last": metrics.get("vol_sum_last"),
                "vol_sum_prev": metrics.get("vol_sum_prev"),
                "volume_rising": metrics.get("volume_rising"),
                "active_bars_last": metrics.get("active_bars_last"),
                "active_bars_prev": metrics.get("active_bars_prev"),
                "activity_rising": metrics.get("activity_rising"),
                "whale_ratio": metrics.get("whale_ratio"),
                "no_whale_dominance": metrics.get("no_whale_dominance"),
            }
        },
        "gate3_compression": {
            "passed": state.get("gate3_passed", False),
            "metrics": {
                "range_pct_last": metrics.get("range_pct_last"),
                "range_tight": metrics.get("range_tight"),
                "atr_pct_last": metrics.get("atr_pct_last"),
                "atr_contracting": metrics.get("atr_contracting"),
                "vol_slope": metrics.get("vol_slope"),
                "not_igniting": metrics.get("not_igniting"),
            }
        },
        "gate4_holder_integrity": {
            "passed": state.get("gate4_passed"),
            "metrics": {"skipped": True, "reason": "not_implemented"},
        }
    }

    rel_volume = None
    baseline = state.get("rel_volume_baseline")

    return envelope({
        "symbol": symbol,
        "phase": state.get("phase", "EMBRYO"),
        "embryo_active_score": embryo_score or state.get("embryo_active_score", 0),
        "scores": {
            "tradability": state.get("tradability_score", 0),
            "participation": state.get("participation_score", 0),
            "compression": state.get("compression_score", 0),
            "trap_risk": state.get("trap_risk_score", 0),
        },
        "gates": gates,
        "compression_box": {
            "high": state.get("compression_box_high"),
            "low": state.get("compression_box_low"),
            "lookback_bars": state.get("compression_box_lookback", 48),
        },
        "ignition_status": {
            "rel_volume": rel_volume,
            "rel_volume_baseline": baseline,
            "baseline_window_used": state.get("baseline_window_used"),
            "breakout": None,
            "accepted": None,
        },
        "promoted_at": state.get("promoted_at"),
        "last_eval_ts": state.get("last_eval_ts"),
    }, t0)
