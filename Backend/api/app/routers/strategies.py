"""
Strategy Builder API Router
=============================

CRUD endpoints for user-defined strategies.
Security invariant: every query includes WHERE user_id = %s.
"""

import math
import time
import json
import random
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, HTTPException, Header
import redis

from api.app.db_pool import get_conn as _pool_get_conn
from governance.bootstrap import get_config
from governance.cache_contract import (
    COMPOSABLE_FIELDS, CSTRAT_GEN_KEY,
    ind_snap_key, get_universe,
)
from core_logic.user_strategy_eval import evaluate_conditions

logger = logging.getLogger(__name__)

router = APIRouter(tags=["strategies"])

VALID_OPS = frozenset({">", "<", ">=", "<=", "crosses_above", "crosses_below"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_id(x_api_key: str) -> str:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    return x_api_key


def _envelope(data, compute_start: float) -> dict:
    return {
        "data": data,
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
            "compute_ms": round((time.time() - compute_start) * 1000, 1),
        },
    }


def _get_redis():
    cfg = get_config()
    return redis.Redis.from_url(cfg.Worker.REDIS_URL, decode_responses=True)


def _invalidate_strategy_cache():
    r = _get_redis()
    r.set(CSTRAT_GEN_KEY, str(time.time()))


def _validate_conditions(conditions: list, cfg) -> list[str]:
    errors = []
    if not conditions or not isinstance(conditions, list):
        return ["conditions must be a non-empty list"]

    if len(conditions) > cfg.max_conditions_per_strategy:
        errors.append(
            f"Too many conditions ({len(conditions)}), "
            f"max is {cfg.max_conditions_per_strategy}"
        )

    for i, cond in enumerate(conditions):
        field = cond.get("field")
        op = cond.get("op")
        value = cond.get("value")
        ref_field = cond.get("ref_field")

        if not field or field not in COMPOSABLE_FIELDS:
            errors.append(f"Condition {i}: invalid field '{field}'")
        if not op or op not in VALID_OPS:
            errors.append(f"Condition {i}: invalid op '{op}'")

        has_value = value is not None
        has_ref = ref_field is not None
        if has_value == has_ref:
            errors.append(
                f"Condition {i}: must have exactly one of 'value' or 'ref_field'"
            )
        if has_value:
            try:
                v = float(value)
                if not math.isfinite(v):
                    errors.append(f"Condition {i}: value must be finite")
            except (ValueError, TypeError):
                errors.append(f"Condition {i}: value must be a number")
        if has_ref and ref_field not in COMPOSABLE_FIELDS:
            errors.append(f"Condition {i}: invalid ref_field '{ref_field}'")

    return errors


def _read_snap_as_dict(r, symbol, tf) -> dict | None:
    """Read ind_snap hash directly as a flat dict. No IndicatorSnapshot import."""
    from governance.cache_contract import b2f
    snap_key = ind_snap_key(symbol, tf)
    raw = r.hgetall(snap_key)
    if not raw:
        return None
    current = {}
    for k, v in raw.items():
        if k in COMPOSABLE_FIELDS:
            try:
                current[k] = float(v)
            except (ValueError, TypeError):
                pass
    return current if current else None


def _health_check(conditions: list, timeframes: list) -> dict:
    r = _get_redis()
    symbols = get_universe(r)
    if not symbols:
        return {"symbols_sampled": 0, "would_fire_now": 0,
                "estimated_fire_rate_pct": 0, "warning": None}

    sample = random.sample(symbols, min(10, len(symbols)))
    total_sampled = 0
    fires = 0

    for symbol in sample:
        for tf in timeframes:
            current = _read_snap_as_dict(r, symbol, tf)
            if current is None:
                continue
            total_sampled += 1
            passed, _ = evaluate_conditions(conditions, current, None)
            if passed:
                fires += 1

    fire_rate = (fires / total_sampled * 100) if total_sampled > 0 else 0
    cfg = get_config().StrategyBuilder
    warning = None
    if fires == 0:
        warning = "No symbols currently match all conditions. This strategy may fire very rarely."
    elif fire_rate >= cfg.max_fire_rate_pct:
        warning = (
            f"Most symbols currently match ({fire_rate:.0f}%). "
            f"Consider adding more conditions or the strategy will be auto-paused."
        )

    return {
        "symbols_sampled": total_sampled,
        "would_fire_now": fires,
        "estimated_fire_rate_pct": round(fire_rate, 1),
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/strategies")
def create_strategy(
    body: dict,
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    t0 = time.time()
    user_id = _get_user_id(x_api_key)
    cfg = get_config().StrategyBuilder

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    conditions = body.get("conditions", [])
    errors = _validate_conditions(conditions, cfg)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    timeframes = body.get("timeframes", [])
    valid_tfs = set(get_config().Worker.DEFAULT_TIMEFRAMES.split(","))
    if not timeframes or not all(tf in valid_tfs for tf in timeframes):
        raise HTTPException(status_code=400, detail=f"timeframes must be subset of {valid_tfs}")

    cooldown = body.get("cooldown_minutes", cfg.cooldown_default_minutes)
    if cooldown < cfg.cooldown_floor_minutes:
        raise HTTPException(
            status_code=400,
            detail=f"cooldown_minutes must be >= {cfg.cooldown_floor_minutes}",
        )

    symbol_filter = body.get("symbol_filter", "all_monitored")

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM user_strategies WHERE user_id = %s AND active = TRUE",
                (user_id,),
            )
            count = cur.fetchone()[0]
            if count >= cfg.max_strategies_per_user:
                raise HTTPException(
                    status_code=400,
                    detail=f"Maximum {cfg.max_strategies_per_user} active strategies allowed",
                )

            cur.execute("""
                INSERT INTO user_strategies
                    (user_id, name, conditions, logic, timeframes,
                     symbol_filter, cooldown_minutes)
                VALUES (%s, %s, %s, 'AND', %s, %s, %s)
                RETURNING id, created_ts
            """, (
                user_id, name,
                json.dumps(conditions, separators=(",", ":")),
                timeframes, symbol_filter, cooldown,
            ))
            row = cur.fetchone()
        conn.commit()

    _invalidate_strategy_cache()
    health = _health_check(conditions, timeframes)

    return _envelope({
        "id": row[0],
        "name": name,
        "conditions": conditions,
        "logic": "AND",
        "timeframes": timeframes,
        "symbol_filter": symbol_filter,
        "cooldown_minutes": cooldown,
        "active": True,
        "auto_paused": False,
        "created_ts": row[1].isoformat() if row[1] else None,
        "health_check": health,
    }, t0)


@router.get("/strategies")
def list_strategies(
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    t0 = time.time()
    user_id = _get_user_id(x_api_key)

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.id, s.name, s.conditions, s.logic, s.timeframes,
                       s.symbol_filter, s.cooldown_minutes, s.active,
                       s.auto_paused, s.created_ts, s.updated_ts,
                       COALESCE(h24.cnt, 0) AS hits_24h,
                       COALESCE(h7d.cnt, 0) AS hits_7d
                FROM user_strategies s
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS cnt FROM user_opportunities
                    WHERE strategy_id = s.id
                      AND created_ts > NOW() - INTERVAL '24 hours'
                ) h24 ON TRUE
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS cnt FROM user_opportunities
                    WHERE strategy_id = s.id
                      AND created_ts > NOW() - INTERVAL '7 days'
                ) h7d ON TRUE
                WHERE s.user_id = %s AND s.active = TRUE
                ORDER BY s.created_ts DESC
            """, (user_id,))
            rows = cur.fetchall()

    strategies = []
    for r in rows:
        strategies.append({
            "id": r[0], "name": r[1],
            "conditions": r[2] if isinstance(r[2], list) else json.loads(r[2]),
            "logic": r[3], "timeframes": r[4],
            "symbol_filter": r[5], "cooldown_minutes": r[6],
            "active": r[7], "auto_paused": r[8],
            "created_ts": r[9].isoformat() if r[9] else None,
            "updated_ts": r[10].isoformat() if r[10] else None,
            "hits_24h": r[11], "hits_7d": r[12],
        })

    return _envelope(strategies, t0)


@router.get("/strategies/{strategy_id}")
def get_strategy(
    strategy_id: int,
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    t0 = time.time()
    user_id = _get_user_id(x_api_key)

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, conditions, logic, timeframes,
                       symbol_filter, cooldown_minutes, active,
                       auto_paused, created_ts, updated_ts
                FROM user_strategies
                WHERE id = %s AND user_id = %s
            """, (strategy_id, user_id))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")

    return _envelope({
        "id": row[0], "name": row[1],
        "conditions": row[2] if isinstance(row[2], list) else json.loads(row[2]),
        "logic": row[3], "timeframes": row[4],
        "symbol_filter": row[5], "cooldown_minutes": row[6],
        "active": row[7], "auto_paused": row[8],
        "created_ts": row[9].isoformat() if row[9] else None,
        "updated_ts": row[10].isoformat() if row[10] else None,
    }, t0)


@router.put("/strategies/{strategy_id}")
def update_strategy(
    strategy_id: int,
    body: dict,
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    t0 = time.time()
    user_id = _get_user_id(x_api_key)
    cfg = get_config().StrategyBuilder

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM user_strategies WHERE id = %s AND user_id = %s",
                (strategy_id, user_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Strategy not found")

            updates = []
            params = []

            if "name" in body:
                n = body["name"].strip()
                if not n:
                    raise HTTPException(status_code=400, detail="name cannot be empty")
                updates.append("name = %s")
                params.append(n)

            conditions = None
            if "conditions" in body:
                conditions = body["conditions"]
                errs = _validate_conditions(conditions, cfg)
                if errs:
                    raise HTTPException(status_code=400, detail="; ".join(errs))
                updates.append("conditions = %s")
                params.append(json.dumps(conditions, separators=(",", ":")))

            if "timeframes" in body:
                valid_tfs = set(get_config().Worker.DEFAULT_TIMEFRAMES.split(","))
                tfs = body["timeframes"]
                if not tfs or not all(tf in valid_tfs for tf in tfs):
                    raise HTTPException(status_code=400, detail="Invalid timeframes")
                updates.append("timeframes = %s")
                params.append(tfs)

            if "cooldown_minutes" in body:
                cd = body["cooldown_minutes"]
                if cd < cfg.cooldown_floor_minutes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"cooldown_minutes must be >= {cfg.cooldown_floor_minutes}",
                    )
                updates.append("cooldown_minutes = %s")
                params.append(cd)

            if "symbol_filter" in body:
                updates.append("symbol_filter = %s")
                params.append(body["symbol_filter"])

            if "auto_paused" in body:
                updates.append("auto_paused = %s")
                params.append(body["auto_paused"])

            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")

            updates.append("updated_ts = NOW()")
            params.extend([strategy_id, user_id])

            cur.execute(
                f"UPDATE user_strategies SET {', '.join(updates)} "
                f"WHERE id = %s AND user_id = %s "
                f"RETURNING id, name, conditions, logic, timeframes, "
                f"symbol_filter, cooldown_minutes, active, auto_paused, "
                f"created_ts, updated_ts",
                params,
            )
            row = cur.fetchone()
        conn.commit()

    _invalidate_strategy_cache()

    result = {
        "id": row[0], "name": row[1],
        "conditions": row[2] if isinstance(row[2], list) else json.loads(row[2]),
        "logic": row[3], "timeframes": row[4],
        "symbol_filter": row[5], "cooldown_minutes": row[6],
        "active": row[7], "auto_paused": row[8],
        "created_ts": row[9].isoformat() if row[9] else None,
        "updated_ts": row[10].isoformat() if row[10] else None,
    }

    if conditions is not None:
        result["health_check"] = _health_check(conditions, row[4])

    return _envelope(result, t0)


@router.delete("/strategies/{strategy_id}")
def delete_strategy(
    strategy_id: int,
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    t0 = time.time()
    user_id = _get_user_id(x_api_key)

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_strategies SET active = FALSE, updated_ts = NOW() "
                "WHERE id = %s AND user_id = %s RETURNING id",
                (strategy_id, user_id),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")

    _invalidate_strategy_cache()
    return _envelope({"id": row[0], "deleted": True}, t0)


@router.get("/strategies/{strategy_id}/opportunities")
def list_strategy_opportunities(
    strategy_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    t0 = time.time()
    user_id = _get_user_id(x_api_key)

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM user_strategies WHERE id = %s AND user_id = %s",
                (strategy_id, user_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Strategy not found")

            cur.execute("""
                SELECT id, symbol, timeframe, direction, triggered_ts,
                       conditions_snapshot, created_ts
                FROM user_opportunities
                WHERE strategy_id = %s AND user_id = %s
                ORDER BY created_ts DESC
                LIMIT %s OFFSET %s
            """, (strategy_id, user_id, limit, offset))
            rows = cur.fetchall()

    opps = []
    for r in rows:
        opps.append({
            "id": r[0], "symbol": r[1], "timeframe": r[2],
            "direction": r[3],
            "triggered_ts": r[4].isoformat() if r[4] else None,
            "conditions_snapshot": r[5],
            "created_ts": r[6].isoformat() if r[6] else None,
        })

    return _envelope(opps, t0)


# ---------------------------------------------------------------------------
# Indicator Menu
# ---------------------------------------------------------------------------

_INDICATOR_MENU = None


def _build_indicator_menu():
    global _INDICATOR_MENU
    if _INDICATOR_MENU is not None:
        return _INDICATOR_MENU

    popular = {
        "rsi": {"rank": 1, "desc": "Measures momentum on a 0-100 scale. Below 30 = oversold."},
        "volume_z": {"rank": 2, "desc": "How unusual is current volume? >2 = significant spike."},
        "sma_200": {"rank": 3, "desc": "Long-term trend. Price above = bullish, below = bearish."},
        "bb_width": {"rank": 4, "desc": "How tight are Bollinger Bands? Low = squeeze, potential breakout."},
        "macd_histogram": {"rank": 5, "desc": "Momentum direction. Positive = bullish, negative = bearish."},
        "close": {"rank": 6, "desc": "Current bar close price. Compare against moving averages."},
    }

    categories = {
        "close": "Price", "open": "Price", "high": "Price", "low": "Price",
        "rsi": "Oscillators", "rsi_sma": "Oscillators", "rsi_slope": "Oscillators",
        "stoch_k": "Oscillators", "stoch_d": "Oscillators", "williams_r": "Oscillators",
        "mfi": "Oscillators", "cci": "Oscillators",
        "sma_20": "Moving Averages", "sma_50": "Moving Averages", "sma_200": "Moving Averages",
        "ema_9": "Moving Averages", "ema_20": "Moving Averages", "ema_21": "Moving Averages",
        "ema_50": "Moving Averages",
        "bb_mb": "Bands & Channels", "bb_ub": "Bands & Channels", "bb_lb": "Bands & Channels",
        "bb_width": "Bands & Channels",
        "kc_mb": "Bands & Channels", "kc_ub": "Bands & Channels", "kc_lb": "Bands & Channels",
        "donchian_high_20": "Bands & Channels", "donchian_low_20": "Bands & Channels",
        "donchian_mid_20": "Bands & Channels",
        "macd_line": "Trend & Momentum", "macd_signal": "Trend & Momentum",
        "macd_histogram": "Trend & Momentum",
        "adx": "Trend & Momentum", "roc": "Trend & Momentum",
        "sma50_slope_atr": "Trend & Momentum", "sma200_slope_atr": "Trend & Momentum",
        "ema20_slope_atr": "Trend & Momentum", "ema50_slope_atr": "Trend & Momentum",
        "ma_gap_atr": "Trend & Momentum",
        "volume_z": "Volume", "rvol": "Volume", "volume_acceleration": "Volume",
        "range_to_volume_ratio": "Volume", "vwap": "Volume",
        "vwap_distance_atr": "Volume", "obv_slope_atr": "Volume",
        "dormancy_score": "MCAS Proprietary", "accum_divergence": "MCAS Proprietary",
        "chaikin_vol": "MCAS Proprietary",
        "clv": "Advanced", "atr": "Advanced", "price_dist_ema20_atr": "Advanced",
        "htf_close": "Advanced", "htf_sma50": "Advanced",
    }

    htf_fields = {"htf_close", "htf_sma50", "htf_bb_mb", "vwap", "vwap_upper_2sd",
                  "vwap_lower_2sd", "vwap_distance_atr"}

    menu = []
    for field in sorted(COMPOSABLE_FIELDS):
        entry = {
            "field": field,
            "category": categories.get(field, "Other"),
            "popularity_rank": popular.get(field, {}).get("rank"),
            "description": popular.get(field, {}).get("desc"),
            "supports_crossover": True,
            "htf_only": field in htf_fields,
        }
        menu.append(entry)

    _INDICATOR_MENU = menu
    return menu


@router.get("/indicators/menu")
def get_indicator_menu():
    t0 = time.time()
    menu = _build_indicator_menu()
    return _envelope(menu, t0)
