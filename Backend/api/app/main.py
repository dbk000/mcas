"""
MCAS Governed API
==================

Router-based architecture. All endpoint logic lives in routers/.
This file handles: app creation, CORS, middleware, auth, lifecycle hooks,
router registration, and the inline endpoints that don't fit a specific router
(health, auth/ping, strategies, markets, timeframes, ohlcv, signals, snapshot).
"""

import sys
import csv
import io
import hmac
import time
import json
import logging
import re
from datetime import date, datetime, timezone, timedelta
from typing import Optional, Any

import requests as http_requests
import psycopg
import redis

from fastapi import (
    FastAPI,
    Query,
    Depends,
    Header,
    HTTPException,
    APIRouter,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from api.app.db_pool import get_conn as _pool_get_conn, pool_stats, close_pool
from api.app.middleware import RequestLoggingMiddleware

from governance.bootstrap import bootstrap_genesis, get_config, strategy_as_api_dict, get_stop_policy
bootstrap_genesis()  # Build genesis block before any module reads registries

from governance.json_logging import setup_logging

# Initialize structured JSON logging before anything else
setup_logging()

# Force uvicorn loggers to use our JSON handler
for _uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _uv_logger = logging.getLogger(_uv_name)
    _uv_logger.handlers.clear()
    _uv_logger.propagate = True

from governance.cache_contract import SIGNAL_MAX_AGE_S
from governance.stop_policy_registry import (
    validate_stop_registry,
    build_stop_policy_template,
)
from api.app.routers.session import (
    SESSION_COOKIE_NAME,
    has_valid_session,
    router as session_router,
)

# -------------------------
# App creation
# -------------------------
app = FastAPI(
    title="MCAS Trading Backend",
    description="Market Cognition At Scale - API-first trading analytics",
    version="1.0.0",
)

# CORS: Allow frontend origins (production + dev)
_CORS_ORIGINS = get_config().Worker.CORS_ORIGINS.strip()
_cors_origins = [o.strip() for o in _CORS_ORIGINS.split(",") if o.strip()] if _CORS_ORIGINS else []
_cors_origins += [
    "https://mcasui.com",
    "https://mcas.pages.dev",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5174",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLoggingMiddleware)

# -------------------------
# Config / clients
# -------------------------
_worker_cfg = get_config().Worker
REDIS_URL = _worker_cfg.REDIS_URL
DATABASE_URL = _worker_cfg.DATABASE_URL
API_KEY = _worker_cfg.API_KEY
DEFAULT_TIMEFRAMES = _worker_cfg.DEFAULT_TIMEFRAMES.split(",")
SIGNAL_TOP_N_DAILY = _worker_cfg.SIGNAL_TOP_N_DAILY
# Strategy registry: derived from frozen StrategyRegistry (sole authority).
# No hardcoded strategy dict here — Central Control Room, Rule 1.
STRATEGY_REGISTRY = strategy_as_api_dict()

_stop_registry_errors = validate_stop_registry(
    strategy_registry=STRATEGY_REGISTRY,
    stop_policy_defaults=get_stop_policy().canonical_defaults,
)
if _stop_registry_errors:
    raise RuntimeError(
        "Stop registry validation failed:\n- " + "\n- ".join(_stop_registry_errors)
    )

REDIS_SETUP_IDS = [sid for sid, info in STRATEGY_REGISTRY.items() if sid != "EQFP"]

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# MIGRATION_DEBT: replace with Redis universe query (universe:known)
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "ETCUSDT",
    "XLMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "FILUSDT", "LDOUSDT", "AAVEUSDT", "MKRUSDT", "GRTUSDT",
    "INJUSDT", "IMXUSDT", "STXUSDT", "RUNEUSDT", "EGLDUSDT",
    "THETAUSDT", "AXSUSDT", "SANDUSDT", "MANAUSDT", "GALAUSDT",
    "APEUSDT", "CHZUSDT", "FLOWUSDT", "ZILUSDT", "ENJUSDT",
    "BATUSDT", "ZECUSDT", "DASHUSDT", "NEOUSDT", "WAVESUSDT",
    "QNTUSDT", "KAVAUSDT", "MINAUSDT", "XTZUSDT", "SNXUSDT",
    "ICPUSDT", "VETUSDT", "ALGOUSDT", "FTMUSDT", "HBARUSDT",
    "EOSUSDT", "XMRUSDT", "ARUSDT", "CAKEUSDT", "DYDXUSDT",
    "GMXUSDT", "PENDLEUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT",
    "BLURUSDT", "WLDUSDT", "FETUSDT", "AGIXUSDT", "OCEANUSDT",
    "RNDRUSDT", "KASUSDT", "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT",
    "BONKUSDT", "WIFUSDT", "BOMEUSDT", "MEMEUSDT", "TURBOUSDT",
    "ORDIUSDT", "SATSUSDT", "RATSUSDT", "1000SATSUSDT", "PYTHUSDT",
    "JUPUSDT", "WUSDT", "STRKUSDT", "MANTAUSDT", "ALTUSDT",
    "PIXELUSDT", "PORTALUSDT", "ACEUSDT", "XAIUSDT", "AIUSDT",
    "ONDOUSDT", "ENAUSDT", "ETHFIUSDT", "SAGAUSDT",
    "CFXUSDT", "CKBUSDT", "HOOKUSDT", "MAGICUSDT", "TUSDT",
    "IDUSDT", "RDNTUSDT", "LQTYUSDT", "SSVUSDT",
    "ANKRUSDT", "IOTXUSDT", "SKLUSDT", "CELRUSDT", "ARPAUSDT",
    "CTSIUSDT", "BANDUSDT", "COTIUSDT", "STMXUSDT", "OGNUSDT",
    "NKNUSDT", "DGBUSDT", "SXPUSDT", "REEFUSDT", "DENTUSDT",
    "HOTUSDT", "WINUSDT", "BTTUSDT", "SCUSDT", "ZENUSDT",
    "ONTUSDT", "IOSTUSDT", "LSKUSDT", "RVNUSDT", "ICXUSDT",
    "COMPUSDT", "YFIUSDT", "CRVUSDT", "BALUSDT", "SUSHIUSDT",
    "1INCHUSDT", "CELOUSDT", "KSMUSDT", "ROSEUSDT", "GLMRUSDT",
    "MOVRUSDT", "AGLDUSDT", "LOOKSUSDT", "ENSUSDT", "CVXUSDT",
    "API3USDT", "WOOUSDT", "JASMYUSDT", "GALUSDT", "DARUSDT",
    "GMTUSDT", "ACHUSDT", "LEVERUSDT", "AMBUSDT", "PHBUSDT",
    "LOOMUSDT", "OAXUSDT", "MDTUSDT", "KEYUSDT", "VOXELUSDT",
    "HIGHUSDT", "LITUSDT", "RSRUSDT", "UMAUSDT", "BADGERUSDT",
    "FISUSDT", "ALPACAUSDT", "QUICKUSDT", "REQUSDT", "PROMUSDT",
    "SUPERUSDT", "USTCUSDT", "LUNCUSDT", "LUNA2USDT", "ASTRUSDT",
    "AUCTIONUSDT", "BICOUSDT", "BLUEBIRDUSDT", "BONDUSDT", "BSWUSDT",
    "C98USDT", "CLVUSDT", "COSUSDT", "CTKUSDT", "DATAUSDT",
    "DEXEUSDT", "DFUSDT", "DOCKUSDT", "DREPUSDT", "DUSKUSDT",
    "EDUUSDT", "ELFUSDT", "EPXUSDT", "ERNUSDT", "FIDAUSDT",
]


# -------------------------
# Response envelope
# -------------------------
def envelope(data: Any, compute_start: float, as_of_ts: float = None) -> dict:
    """Standard response envelope with metadata."""
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


logger = logging.getLogger(__name__)


class SignalRequest(BaseModel):
    symbol: str
    timeframe: str
    setup_id: str


# -------------------------
# Auth (API key)
# -------------------------
def require_api_key(
    request: Request,
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API key not configured")
    if hmac.compare_digest(x_api_key, API_KEY):
        return

    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if has_valid_session(session_token):
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


# Protected v1 router for inline endpoints
router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


# -------------------------
# Health / ping (unprotected)
# -------------------------
@app.get("/health")
def health():
    t0 = time.time()
    r.ping()
    redis_ms = int((time.time() - t0) * 1000)

    if not DATABASE_URL:
        return {"status": "unhealthy", "error": "DATABASE_URL not set"}

    t1 = time.time()
    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
    pg_ms = int((time.time() - t1) * 1000)

    kill_switch_active = r.get("kill_switch:active") == "1"
    trading_paused = r.get("trading_paused") == "1"

    status = "healthy"
    if kill_switch_active:
        status = "killed"
    elif trading_paused:
        status = "paused"

    signal_budget = SIGNAL_TOP_N_DAILY
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        signals_today = int(r.get(f"signals:daily_count:{today_str}") or 0)
    except Exception:
        signals_today = 0
    utilization_pct = (
        round(signals_today / signal_budget * 100, 1) if signal_budget > 0 else 0.0
    )

    return {
        "status": status,
        "redis_ms": redis_ms,
        "pg_ms": pg_ms,
        "kill_switch_active": kill_switch_active,
        "trading_paused": trading_paused,
        "db_pool": pool_stats(),
        "signal_congestion": {
            "signals_today": signals_today,
            "budget": signal_budget,
            "utilization_pct": utilization_pct,
        },
    }


# -------------------------
# Lifecycle hooks
# -------------------------
@app.on_event("shutdown")
def on_shutdown():
    """Close DB connection pool on graceful shutdown."""
    close_pool()


# -------------------------
# Auth endpoint
# -------------------------
@router.get("/auth/ping")
def auth_ping():
    """Validate API key."""
    t0 = time.time()
    return envelope({"authenticated": True}, t0)


# -------------------------
# Strategy Registry
# -------------------------
@router.get("/strategies")
def list_strategies():
    """Return all known strategies with labels and types."""
    t0 = time.time()
    strategies = [
        {"id": sid, **info}
        for sid, info in STRATEGY_REGISTRY.items()
    ]
    return envelope({"strategies": strategies, "count": len(strategies)}, t0)


@router.get("/pipeline/stop_policy_template")
def get_stop_policy_template():
    """Return canonical complete stop-policy template for all strategies."""
    t0 = time.time()
    template = build_stop_policy_template(STRATEGY_REGISTRY)
    return envelope({"template": template}, t0)


# -------------------------
# Markets / Universe
# -------------------------
@router.get("/markets")
def list_markets():
    """List available symbols with metadata."""
    from governance.bootstrap import quote_currency as _qc, major_symbols as _ms
    t0 = time.time()
    _quote = _qc()
    _tier1 = set(_ms())
    markets = []
    for symbol in DEFAULT_SYMBOLS:
        # DEFAULT_SYMBOLS is the USDT fallback list — strip whichever
        # known quote suffix the symbol actually carries so the response
        # works for both USDT-fallback and registry-resolved universes.
        if symbol.endswith(_quote):
            base = symbol[:-len(_quote)]
            reported_quote = _quote
        elif symbol.endswith("USDT"):
            base = symbol[:-4]
            reported_quote = "USDT"
        else:
            base = symbol
            reported_quote = "?"
        markets.append({
            "symbol": symbol,
            "base": base,
            "quote": reported_quote,
            "status": "active",
            "liquidity_tier": 1 if symbol in _tier1 else 2,
        })
    return envelope({"markets": markets, "count": len(markets)}, t0)


@router.get("/universe")
def get_universe():
    """Live universe from Redis."""
    t0 = time.time()
    known = sorted(r.smembers("universe:known") or [])
    monitored = sorted(r.smembers("universe:monitored") or [])
    return envelope({
        "known": {"symbols": known, "count": len(known)},
        "monitored": {"symbols": monitored, "count": len(monitored)},
    }, t0)


# -------------------------
# Timeframes
# -------------------------
@router.get("/timeframes")
def list_timeframes():
    """Return supported timeframes."""
    t0 = time.time()
    return envelope({"timeframes": DEFAULT_TIMEFRAMES}, t0)


# -------------------------
# Helper functions
# -------------------------
def _read_opportunities_from_redis(min_score: float = 0.0, limit: int = 50, setup_id: str = None):
    """Read live signals from Redis ZSETs (signal_worker output)."""
    try:
        cache_ready = r.get("cache:ready")
        if cache_ready != "1":
            return None

        setup_ids = [setup_id] if setup_id else REDIS_SETUP_IDS
        timeframes = list(r.smembers("cache:timeframes")) or DEFAULT_TIMEFRAMES
        results = []
        seen = set()
        now_ts = time.time()

        for sid in setup_ids:
            for tf in timeframes:
                zset_key = f"signals:score:{sid}:{tf}"
                top = r.zrevrangebyscore(
                    zset_key, "+inf", str(min_score),
                    start=0, num=limit, withscores=True,
                )
                for member, score in top:
                    symbol = str(member)
                    dedup_key = f"{symbol}:{tf}:{sid}"
                    if dedup_key in seen:
                        continue

                    sig_key = f"sig:{symbol}:{tf}:{sid}"
                    raw = r.get(sig_key)
                    if not raw:
                        continue

                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    meta_key = f"sig_meta:{symbol}:{tf}:{sid}"
                    meta = r.hgetall(meta_key)
                    as_of_ts = float(meta.get("as_of_ts", 0)) if meta else 0

                    max_age = 86400 if sid.startswith("EQ_") else SIGNAL_MAX_AGE_S
                    if as_of_ts > 0 and (now_ts - as_of_ts) > max_age:
                        continue

                    expires_at = (as_of_ts + max_age) if as_of_ts > 0 else None

                    results.append({
                        "id": f"{sid}:{symbol}:{tf}",
                        "symbol": symbol,
                        "timeframe": tf,
                        "setup_id": sid,
                        "direction": payload.get("direction"),
                        "score": score,
                        "reason": "; ".join(payload.get("why_items", [])) if payload.get("why_items") else None,
                        "payload": payload,
                        "as_of_ts": as_of_ts,
                        "candle_ts": datetime.fromtimestamp(
                            float(meta.get("bar_start_ts", 0)), tz=timezone.utc
                        ).isoformat() if meta.get("bar_start_ts") else None,
                        "expires_at": expires_at,
                        "updated_ts": datetime.fromtimestamp(
                            as_of_ts, tz=timezone.utc
                        ).isoformat() if as_of_ts > 0 else None,
                        "source": "redis",
                    })
                    seen.add(dedup_key)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    except Exception as e:
        logger.debug(f"Redis opportunities read failed: {e}")
        return None


def _parse_payload_json(raw_payload: Any) -> dict:
    """Safely parse payload_json into a dict."""
    if isinstance(raw_payload, dict):
        return raw_payload
    if not raw_payload:
        return {}
    try:
        parsed = json.loads(raw_payload)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse epoch seconds/ms or ISO timestamp to timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        try:
            numeric = float(v)
            return _parse_timestamp(numeric)
        except ValueError:
            pass
        try:
            if v.endswith("Z"):
                v = v[:-1] + "+00:00"
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


_HORIZON_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)


def _parse_horizon_delta(value: Any) -> Optional[timedelta]:
    """Parse horizon strings like 30m, 6h, 1d, 2w into timedelta."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 0:
            return timedelta(seconds=seconds)
        return None
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    match = _HORIZON_RE.match(raw)
    if not match:
        return None
    qty = int(match.group(1))
    unit = match.group(2)
    if qty <= 0:
        return None
    if unit == "m":
        return timedelta(minutes=qty)
    if unit == "h":
        return timedelta(hours=qty)
    if unit == "d":
        return timedelta(days=qty)
    if unit == "w":
        return timedelta(weeks=qty)
    return None


def _format_exit_label(key: str) -> str:
    key_l = key.lower()
    if "tp1" in key_l:
        return "TP1 EXIT"
    if "tp2" in key_l:
        return "TP2 EXIT"
    if "tp" in key_l or "take_profit" in key_l:
        return "TP EXIT"
    if "stop" in key_l or "sl" in key_l:
        return "STOP EXIT"
    if "partial" in key_l:
        return "PARTIAL EXIT"
    if "final" in key_l:
        return "FINAL EXIT"
    if "close" in key_l:
        return "CLOSE EXIT"
    return "EXIT"


def _extract_exit_points(payload: dict[str, Any], fallback_exit_ts: datetime) -> list[dict[str, Any]]:
    """Extract all known exit-like timestamps from payload for vertical marker rendering."""
    points: dict[int, dict[str, Any]] = {}
    fallback_exit_ts_ms = int(fallback_exit_ts.timestamp() * 1000)

    def add_point(ts_value: Any, label: str, is_final: bool = False) -> None:
        ts_dt = _parse_timestamp(ts_value)
        if not ts_dt:
            return
        ts_ms = int(ts_dt.timestamp() * 1000)
        existing = points.get(ts_ms)
        if existing:
            if is_final:
                existing["label"] = label
                existing["is_final"] = True
            return
        points[ts_ms] = {"ts": ts_ms, "label": label, "is_final": is_final}

    excluded_keys = {
        "entry_ts", "exit_ts", "bar_start_ts", "created_ts", "updated_ts",
        "as_of_ts", "since_ts", "action_by_ts", "expiry_ts", "expires_at",
        "exit_settled_ts",
    }

    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        key_l = key.lower()
        if key_l in excluded_keys:
            continue
        if "ts" not in key_l:
            continue
        if not any(token in key_l for token in ("exit", "tp", "take_profit", "stop", "sl", "close")):
            continue
        add_point(value, _format_exit_label(key_l), is_final=("exit" in key_l and "entry" not in key_l))

    raw_points = payload.get("exit_points")
    if isinstance(raw_points, list):
        for idx, raw in enumerate(raw_points):
            if isinstance(raw, dict):
                ts_value = (
                    raw.get("ts") or raw.get("time") or raw.get("exit_ts") or raw.get("timestamp")
                )
                label = str(raw.get("label") or raw.get("reason") or f"EXIT {idx + 1}")
                ts_dt = _parse_timestamp(ts_value)
                if not ts_dt:
                    continue
                ts_ms = int(ts_dt.timestamp() * 1000)
                label_l = label.strip().lower()
                if label_l in {"exit", "close", "close exit"} and ts_ms != fallback_exit_ts_ms:
                    continue
                add_point(ts_dt, label, is_final=(str(raw.get("is_final")).lower() == "true"))
            else:
                add_point(raw, f"EXIT {idx + 1}")

    canonical: list[dict[str, Any]] = []
    for point in points.values():
        ts_ms = int(point["ts"])
        if ts_ms == fallback_exit_ts_ms:
            continue
        label = str(point.get("label") or "EXIT")
        label_l = label.lower()
        if any(token in label_l for token in ("tp", "take_profit", "stop", "sl", "partial")):
            canonical.append({"ts": ts_ms, "label": label, "is_final": False})

    canonical.append({"ts": fallback_exit_ts_ms, "label": "FINAL EXIT", "is_final": True})
    canonical.sort(key=lambda p: (p["ts"], 0 if p.get("is_final") else 1, p["label"]))
    return canonical


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_outcome_class(r_multiple: Any, pnl_net: Any) -> str:
    """Map existing outcome fields to winner/loser class."""
    r_value = _parse_float(r_multiple)
    if r_value is not None:
        if r_value > 0:
            return "winner"
        if r_value < 0:
            return "loser"
    pnl_value = _parse_float(pnl_net)
    if pnl_value is not None:
        if pnl_value > 0:
            return "winner"
        if pnl_value < 0:
            return "loser"
    return "unknown"


# -------------------------
# OHLCV
# -------------------------
@router.get("/ohlcv")
def get_ohlcv(
    symbol: str = Query(..., description="Trading pair, e.g. BTCUSDT"),
    tf: str = Query(default="1m", description="Timeframe"),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get OHLCV candles from persisted DB collector data."""
    t0 = time.time()

    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    symbol_norm = symbol.strip().upper()
    tf_norm = tf.strip().lower()

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close, volume, as_of_ts, is_final, source
                FROM (
                    SELECT bar_start_ts, open, high, low, close, volume, as_of_ts, is_final, source
                    FROM (
                        SELECT DISTINCT ON (bar_start_ts)
                               bar_start_ts, open, high, low, close, volume, as_of_ts, is_final, source
                        FROM ohlcv
                        WHERE symbol = %s
                          AND timeframe = %s
                        ORDER BY bar_start_ts DESC,
                                 as_of_ts DESC,
                                 CASE source
                                     WHEN 'ws' THEN 0
                                     WHEN 'binance' THEN 1
                                     ELSE 2
                                 END
                    ) dedup
                    ORDER BY bar_start_ts DESC
                    LIMIT %s
                ) latest
                ORDER BY bar_start_ts ASC;
            """, (symbol_norm, tf_norm, limit))
            rows = cur.fetchall()

    candles = []
    freshest_as_of: Optional[datetime] = None
    for r_ in rows:
        bar_ts, o_, h_, l_, c_, v_, as_of_ts, is_final, source = r_
        if as_of_ts and (freshest_as_of is None or as_of_ts > freshest_as_of):
            freshest_as_of = as_of_ts
        candles.append({
            "timestamp": int(bar_ts.timestamp() * 1000),
            "candle_ts": bar_ts.isoformat(),
            "open": float(o_),
            "high": float(h_),
            "low": float(l_),
            "close": float(c_),
            "volume": float(v_),
            "as_of_ts": as_of_ts.isoformat() if as_of_ts else None,
            "is_final": bool(is_final),
            "source": source,
        })

    as_of = freshest_as_of.timestamp() if freshest_as_of else time.time()
    collector_lag_s = int(max(0, time.time() - as_of)) if freshest_as_of else None

    return envelope({
        "symbol": symbol_norm,
        "timeframe": tf_norm,
        "candles": candles,
        "count": len(candles),
        "collector_as_of_ts": freshest_as_of.isoformat() if freshest_as_of else None,
        "collector_lag_s": collector_lag_s,
        "source": "db_ohlcv",
    }, t0, as_of_ts=as_of)


# -------------------------
# Squeeze State (S1)
# -------------------------
@router.get("/squeeze/state")
def get_squeeze_state(
    symbol: str = Query(..., description="Trading pair"),
    tf: str = Query(default="1h", description="Timeframe"),
):
    """Get BB Squeeze state for a symbol/timeframe."""
    t0 = time.time()
    cache_key = f"squeeze_state:{symbol}:{tf}"
    cached = r.get(cache_key)
    if cached:
        return envelope(json.loads(cached), t0)
    return envelope({
        "symbol": symbol, "timeframe": tf, "squeeze_on": None, "squeeze_age": None,
        "bb_width_pctile": None, "ripe": None, "box_high": None, "box_low": None,
        "bias_score": None, "btc_severe": None, "btc_wait_state": "unknown",
        "note": "State computed by worker; check opportunities for latest signals",
    }, t0)


@router.get("/btc/squeeze-status")
def get_btc_squeeze_status(
    tf: str = Query(default="1h", description="Timeframe"),
):
    """Get BTC squeeze state for correlation filter."""
    t0 = time.time()
    cache_key = f"btc_squeeze:{tf}"
    cached = r.get(cache_key)
    if cached:
        return envelope(json.loads(cached), t0)
    return envelope({
        "symbol": "BTCUSDT", "timeframe": tf, "squeeze_on": None,
        "bb_width_pctile": None, "box_high": None, "box_low": None,
        "breakout_direction": None, "note": "State computed by worker",
    }, t0)


# -------------------------
# Signals
# -------------------------
@router.post("/signals/queue")
def queue_signal(req: SignalRequest):
    """Queue a signal for processing by worker."""
    t0 = time.time()
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO signal_queue (symbol, timeframe, setup_id)
                VALUES (%s, %s, %s)
                RETURNING id;
            """, (req.symbol, req.timeframe, req.setup_id))
            signal_id = cur.fetchone()[0]
        conn.commit()
    return envelope({"signal_id": signal_id, "status": "queued"}, t0)


@router.get("/signals/top")
def get_top_signals(
    limit: int = Query(default=3, ge=1, le=20),
    tf: Optional[str] = Query(default=None, description="Filter by timeframe"),
):
    """Get top N signals by score (ranked queue)."""
    t0 = time.time()

    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            if tf:
                cur.execute("""
                    SELECT id, symbol, timeframe, setup_id, score, reason, payload_json, updated_ts
                    FROM opportunities
                    WHERE status = 'active' AND timeframe = %s
                    ORDER BY score DESC, updated_ts DESC
                    LIMIT %s;
                """, (tf, limit))
            else:
                cur.execute("""
                    SELECT id, symbol, timeframe, setup_id, score, reason, payload_json, updated_ts
                    FROM opportunities
                    WHERE status = 'active'
                    ORDER BY score DESC, updated_ts DESC
                    LIMIT %s;
                """, (limit,))
            rows = cur.fetchall()

    signals = []
    for r_ in rows:
        payload = json.loads(r_[6]) if r_[6] else {}
        signals.append({
            "id": r_[0],
            "symbol": r_[1],
            "timeframe": r_[2],
            "setup_id": r_[3],
            "direction": payload.get("direction"),
            "score": r_[4],
            "reason": r_[5],
            "entry_price": payload.get("entry_price"),
            "stop_price": payload.get("stop_price"),
            "tp1_price": payload.get("tp1_price"),
            "tp2_price": payload.get("tp2_price"),
            "r_value": payload.get("r_value"),
            "candle_ts": r_[7].isoformat() if r_[7] else None,
            "updated_ts": r_[7].isoformat() if r_[7] else None,
        })

    return envelope({"signals": signals, "count": len(signals)}, t0)


@router.get("/signals/{signal_id}")
def get_signal(signal_id: int):
    """Get signal processing result."""
    t0 = time.time()
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, status, result_json, symbol, timeframe, setup_id
                FROM signal_queue WHERE id = %s;
            """, (signal_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")
    _id, status, result_json, symbol, timeframe, setup_id = row
    return envelope({
        "id": _id, "symbol": symbol, "timeframe": timeframe,
        "setup_id": setup_id, "status": status,
        "result": json.loads(result_json) if result_json else None,
    }, t0)


# -------------------------
# Opportunities
# -------------------------
@router.get("/opportunities/csv")
def export_opportunities_csv(
    status: str = Query(default="active"),
    setup_id: Optional[str] = Query(default=None),
    min_score: float = Query(default=0.0, ge=0),
    limit: int = Query(default=10000, ge=1, le=50000),
):
    """Export opportunities as CSV for offline analysis."""
    if status not in ("active", "expired", "traded"):
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}. Must be active, expired, or traded.")
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            payload_fields = """
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'direction' END AS direction,
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'entry_price' END AS entry_price,
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'stop_price' END AS stop_price,
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'tp1_price' END AS tp1_price,
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'tp2_price' END AS tp2_price,
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'r_value' END AS r_value,
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'horizon_days' END AS horizon_days,
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'expected_return_bps' END AS expected_return_bps,
                           CASE WHEN payload_json IS NOT NULL THEN payload_json::jsonb ->> 'confidence' END AS confidence
            """
            if status != "active":
                query = f"""
                    SELECT DISTINCT ON (symbol, timeframe, setup_id)
                           id, created_ts, updated_ts, symbol, timeframe, setup_id, status, score, decision, reason,
                           {payload_fields}
                    FROM opportunities
                    WHERE status = %s AND score >= %s
                """
            else:
                query = f"""
                    SELECT id, created_ts, updated_ts, symbol, timeframe, setup_id, status, score, decision, reason,
                           {payload_fields}
                    FROM opportunities
                    WHERE status = %s AND score >= %s
                """
            params_list = [status, min_score]

            if setup_id:
                query += " AND setup_id = %s"
                params_list.append(setup_id)

            if status != "active":
                query += " ORDER BY symbol, timeframe, setup_id, score DESC, updated_ts DESC LIMIT %s;"
            else:
                query += " ORDER BY score DESC, updated_ts DESC LIMIT %s;"
            params_list.append(limit)

            cur.execute(query, params_list)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([
            v.isoformat() if isinstance(v, (datetime, date)) else v
            for v in row
        ])

    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"opportunities_{status}_{now_str}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/opportunities/live/{setup_id}/{symbol}/{tf}")
def get_live_opportunity(setup_id: str, symbol: str, tf: str):
    """Get a single live opportunity from Redis by composite key."""
    t0 = time.time()
    try:
        sig_key = f"sig:{symbol}:{tf}:{setup_id}"
        raw = r.get(sig_key)
        if not raw:
            raise HTTPException(status_code=404, detail="Live signal not found")
        payload = json.loads(raw)
        meta_key = f"sig_meta:{symbol}:{tf}:{setup_id}"
        meta = r.hgetall(meta_key)
        as_of_ts = float(meta.get("as_of_ts", 0)) if meta else 0
        zset_key = f"signals:score:{setup_id}:{tf}"
        score = r.zscore(zset_key, symbol)
        if score is None:
            score = 0.0
        max_age = 86400 if setup_id.startswith("EQ_") else SIGNAL_MAX_AGE_S
        expires_at = (as_of_ts + max_age) if as_of_ts > 0 else None
        return envelope({
            "id": f"{setup_id}:{symbol}:{tf}",
            "symbol": symbol, "timeframe": tf, "setup_id": setup_id,
            "direction": payload.get("direction"), "score": score,
            "reason": "; ".join(payload.get("why_items", [])) if payload.get("why_items") else None,
            "payload": payload, "status": "active", "as_of_ts": as_of_ts,
            "candle_ts": datetime.fromtimestamp(
                float(meta.get("bar_start_ts", 0)), tz=timezone.utc
            ).isoformat() if meta.get("bar_start_ts") else None,
            "expires_at": expires_at,
            "updated_ts": datetime.fromtimestamp(
                as_of_ts, tz=timezone.utc
            ).isoformat() if as_of_ts > 0 else None,
            "source": "redis",
        }, t0)
    except HTTPException:
        raise
    except Exception as e:
        logger.debug(f"Redis live opportunity read failed: {e}")
        raise HTTPException(status_code=502, detail="Redis unavailable")


@router.get("/opportunities/{opp_id}/price-window")
def get_opportunity_price_window(
    opp_id: int,
    pre_s: int = Query(default=3600, ge=0, le=172800, description="Seconds before entry timestamp"),
    post_s: int = Query(default=21600, ge=0, le=604800, description="Seconds after exit timestamp"),
):
    """Return 5m DB candles around an expired opportunity from pre-entry to post-exit."""
    t0 = time.time()
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.id, o.symbol, o.created_ts, o.payload_json,
                       sr.entry_ts, sr.exit_ts, sr.exit_price, sr.entry_price, sr.stop_price,
                       sr.tp1_price, sr.tp2_price, sr.r_multiple, sr.pnl_net
                FROM opportunities o
                LEFT JOIN LATERAL (
                    SELECT entry_ts, exit_ts, exit_price, entry_price, stop_price, tp1_price, tp2_price, r_multiple, pnl_net
                    FROM simulation_results
                    WHERE opportunity_id = o.id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) sr ON TRUE
                WHERE o.id = %s;
            """, (opp_id,))
            row = cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Opportunity not found")

            payload = _parse_payload_json(row[3])
            entry_ts = (
                _parse_timestamp(row[4])
                or _parse_timestamp(payload.get("entry_ts"))
                or _parse_timestamp(payload.get("bar_start_ts"))
                or _parse_timestamp(row[2])
            )
            exit_ts = _parse_timestamp(row[5]) or _parse_timestamp(payload.get("exit_ts"))

            exit_ts_inferred = False
            exit_ts_source: Optional[str] = None

            if not entry_ts:
                raise HTTPException(status_code=422, detail="expired opp missing entry_ts")

            if not exit_ts:
                inferred_direct = (
                    _parse_timestamp(payload.get("action_by_ts"))
                    or _parse_timestamp(payload.get("expiry_ts"))
                    or _parse_timestamp(payload.get("expires_at"))
                )
                if inferred_direct:
                    exit_ts = inferred_direct
                    exit_ts_inferred = True
                    exit_ts_source = "payload_expiry"

            if not exit_ts:
                horizon_delta = _parse_horizon_delta(payload.get("horizon"))
                if horizon_delta:
                    exit_ts = entry_ts + horizon_delta
                    exit_ts_inferred = True
                    exit_ts_source = f"horizon:{payload.get('horizon')}"

            if not exit_ts:
                raise HTTPException(status_code=422, detail="expired opp missing exit_ts")

            entry_px = _parse_float(row[7])
            exit_px = _parse_float(row[6])
            stop_px = _parse_float(row[8])
            tp1_px = _parse_float(row[9])
            tp2_px = _parse_float(row[10])

            if entry_px is None:
                entry_px = _parse_float(payload.get("entry_price"))
            if exit_px is None:
                exit_px = _parse_float(payload.get("exit_price"))
            if stop_px is None:
                stop_px = _parse_float(payload.get("stop_price"))
            if tp1_px is None:
                tp1_px = _parse_float(payload.get("tp1_price"))
            if tp2_px is None:
                tp2_px = _parse_float(payload.get("tp2_price"))

            tp_px = [px for px in (tp1_px, tp2_px) if px is not None]
            tp_px = list(dict.fromkeys(tp_px))

            t_start_dt = entry_ts - timedelta(seconds=pre_s)
            t_end_dt = exit_ts + timedelta(seconds=post_s)

            cur.execute("""
                SELECT DISTINCT ON (bar_start_ts)
                       bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = %s
                  AND timeframe = '5m'
                  AND bar_start_ts >= %s
                  AND bar_start_ts < %s
                  AND is_final = TRUE
                ORDER BY bar_start_ts,
                         CASE source
                             WHEN 'ws' THEN 0
                             WHEN 'binance' THEN 1
                             ELSE 2
                         END;
            """, (row[1], t_start_dt, t_end_dt))
            candle_rows = cur.fetchall()

    candles = [
        {
            "t": int(c_[0].timestamp() * 1000),
            "o": float(c_[1]),
            "h": float(c_[2]),
            "l": float(c_[3]),
            "c": float(c_[4]),
            "v": float(c_[5]),
        }
        for c_ in candle_rows
    ]

    gap_count = 0
    expected_gap_ms = 5 * 60 * 1000
    for idx in range(1, len(candles)):
        if (candles[idx]["t"] - candles[idx - 1]["t"]) > expected_gap_ms:
            gap_count += 1

    outcome_class = _compute_outcome_class(row[11], row[12])
    exit_points = _extract_exit_points(payload, exit_ts)

    return envelope({
        "opportunity_id": row[0],
        "symbol": row[1],
        "tf": "5m",
        "t_start": int(t_start_dt.timestamp() * 1000),
        "t_end": int(t_end_dt.timestamp() * 1000),
        "candles": candles,
        "count": len(candles),
        "data_quality": {
            "gap_count": gap_count,
            "is_gappy": gap_count > 0,
            "empty": len(candles) == 0,
        },
        "markers": {
            "entry_ts": int(entry_ts.timestamp() * 1000),
            "exit_ts": int(exit_ts.timestamp() * 1000),
            "exit_ts_inferred": exit_ts_inferred,
            "exit_ts_source": exit_ts_source,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "stop_px": stop_px,
            "tp_px": tp_px,
            "exit_points": exit_points,
        },
        "outcome": {
            "class": outcome_class,
            "r_multiple": _parse_float(row[11]),
            "pnl_net": _parse_float(row[12]),
        },
    }, t0)


@router.get("/opportunities/{opp_id}")
def get_opportunity(opp_id: int):
    """Get single opportunity."""
    t0 = time.time()

    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.id, o.signal_id, o.symbol, o.timeframe, o.setup_id, o.decision,
                       o.score, o.reason, o.payload_json, o.status, o.updated_ts, o.trace_json,
                       sr.trace_json AS sim_trace
                FROM opportunities o
                LEFT JOIN LATERAL (
                    SELECT trace_json
                    FROM simulation_results
                    WHERE opportunity_id = o.id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) sr ON TRUE
                WHERE o.id = %s;
            """, (opp_id,))
            r_ = cur.fetchone()

    if not r_:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    payload = json.loads(r_[8]) if r_[8] else None
    trace = r_[11] if r_[11] else None
    if isinstance(trace, str):
        trace = json.loads(trace)

    sim_trace = None
    if r_[12]:
        try:
            sim_trace = json.loads(r_[12]) if isinstance(r_[12], str) else r_[12]
        except Exception:
            sim_trace = None

    return envelope({
        "id": r_[0],
        "signal_id": r_[1],
        "symbol": r_[2],
        "timeframe": r_[3],
        "setup_id": r_[4],
        "direction": payload.get("direction") if payload else None,
        "decision": r_[5],
        "score": r_[6],
        "reason": r_[7],
        "payload": payload,
        "status": r_[9],
        "candle_ts": r_[10].isoformat() if r_[10] else None,
        "updated_ts": r_[10].isoformat() if r_[10] else None,
        "trace": trace,
        "sim_trace": sim_trace,
    }, t0)


@router.get("/opportunities")
def list_opportunities(
    status: str = Query(default="active"),
    setup_id: Optional[str] = Query(default=None),
    min_score: float = Query(default=0.0),
    limit: int = Query(default=50, ge=1, le=500),
    dedup: bool = Query(default=True, description="When false, return all matching rows (no symbol/tf/setup dedup)."),
    include_outcome: bool = Query(default=False, description="Include latest simulation outcome fields for survey mode."),
    outcome_class: Optional[str] = Query(default=None, description="winner, loser, or unknown (requires include_outcome=true)."),
    ids: Optional[str] = Query(default=None, description="Comma-separated opportunity ids."),
    random_sample: bool = Query(default=False, description="Deterministic pseudo-random ordering for survey batching."),
    sample_seed: int = Query(default=0, description="Seed used when random_sample=true."),
):
    """List opportunities with filters.

    For status='active', tries Redis ZSETs first (live signals from signal_worker),
    falls back to Postgres if empty or stale.
    For other statuses, queries Postgres directly.
    """
    t0 = time.time()
    redis_items = None

    selected_ids: list[int] = []
    if ids:
        try:
            selected_ids = sorted({int(part.strip()) for part in ids.split(",") if part.strip()})
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid ids parameter; expected comma-separated integers")

    normalized_outcome = (outcome_class or "").strip().lower() or None
    if normalized_outcome and normalized_outcome not in {"winner", "loser", "unknown"}:
        raise HTTPException(status_code=400, detail="Invalid outcome_class. Must be winner, loser, or unknown.")
    if normalized_outcome and status == "active":
        raise HTTPException(status_code=400, detail="outcome_class is only supported for non-active statuses")
    if normalized_outcome:
        include_outcome = True

    if status == "active":
        redis_items = _read_opportunities_from_redis(min_score, limit, setup_id=setup_id)
        if (
            redis_items is not None
            and len(redis_items) > 0
            and setup_id
            and setup_id in REDIS_SETUP_IDS
            and not selected_ids
            and not include_outcome
            and not normalized_outcome
        ):
            return envelope({
                "opportunities": redis_items,
                "count": len(redis_items),
                "source": "redis_live",
            }, t0)

    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    outcome_sql = """
        CASE
            WHEN sr.r_multiple IS NOT NULL AND sr.r_multiple > 0 THEN 'winner'
            WHEN sr.r_multiple IS NOT NULL AND sr.r_multiple < 0 THEN 'loser'
            WHEN sr.pnl_net IS NOT NULL AND sr.pnl_net > 0 THEN 'winner'
            WHEN sr.pnl_net IS NOT NULL AND sr.pnl_net < 0 THEN 'loser'
            ELSE 'unknown'
        END
    """

    base_select = """
        o.id, o.signal_id, o.symbol, o.timeframe, o.setup_id, o.decision, o.score,
        o.reason, o.payload_json, o.updated_ts, o.created_ts
    """
    if include_outcome:
        base_select += """
            , sr.entry_ts, sr.exit_ts, sr.exit_price, sr.exit_reason,
              sr.entry_price, sr.stop_price, sr.tp1_price, sr.tp2_price,
              sr.r_multiple, sr.pnl_net
        """

    from_clause = "FROM opportunities o"
    if include_outcome:
        from_clause += """
            LEFT JOIN LATERAL (
                SELECT entry_ts, exit_ts, exit_price, exit_reason,
                       entry_price, stop_price, tp1_price, tp2_price, r_multiple, pnl_net
                FROM simulation_results
                WHERE opportunity_id = o.id
                ORDER BY created_at DESC
                LIMIT 1
            ) sr ON TRUE
        """

    where_clauses = ["o.status = %s", "o.score >= %s"]
    params_list: list[Any] = [status, min_score]

    if setup_id:
        where_clauses.append("o.setup_id = %s")
        params_list.append(setup_id)
    if selected_ids:
        where_clauses.append("o.id = ANY(%s)")
        params_list.append(selected_ids)
    if include_outcome and normalized_outcome:
        where_clauses.append(f"{outcome_sql} = %s")
        params_list.append(normalized_outcome)

    where_sql = " AND ".join(where_clauses)

    if status != "active" and dedup:
        query = f"""
            SELECT DISTINCT ON (o.symbol, o.timeframe, o.setup_id)
                   {base_select}
            {from_clause}
            WHERE {where_sql}
            ORDER BY o.symbol, o.timeframe, o.setup_id, o.score DESC, o.updated_ts DESC
            LIMIT %s;
        """
        params_list.append(limit)
    else:
        query = f"""
            SELECT {base_select}
            {from_clause}
            WHERE {where_sql}
        """
        if status != "active" and random_sample:
            query += " ORDER BY md5((o.id::text || %s::text)) "
            params_list.append(str(sample_seed))
        else:
            query += " ORDER BY o.score DESC, o.updated_ts DESC "
        query += " LIMIT %s;"
        params_list.append(limit)

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params_list)
            rows = cur.fetchall()

    pg_items = []
    for r_ in rows:
        payload = _parse_payload_json(r_[8])
        item = {
            "id": r_[0],
            "signal_id": r_[1],
            "symbol": r_[2],
            "timeframe": r_[3],
            "setup_id": r_[4],
            "direction": payload.get("direction") if payload else None,
            "decision": r_[5],
            "score": r_[6],
            "reason": r_[7],
            "payload": payload,
            "candle_ts": r_[9].isoformat() if r_[9] else None,
            "updated_ts": r_[9].isoformat() if r_[9] else None,
            "created_ts": r_[10].isoformat() if r_[10] else None,
            "source": "postgres",
        }

        if include_outcome:
            payload_entry_ts = (
                _parse_timestamp(payload.get("entry_ts"))
                or _parse_timestamp(payload.get("bar_start_ts"))
                or _parse_timestamp(r_[11])
                or _parse_timestamp(r_[10])
            )
            payload_exit_ts = _parse_timestamp(payload.get("exit_ts")) or _parse_timestamp(r_[12])
            exit_price = _parse_float(payload.get("exit_price"))
            exit_reason = payload.get("exit_reason")
            entry_price = _parse_float(payload.get("entry_price"))
            stop_price = _parse_float(payload.get("stop_price"))
            tp1_price = _parse_float(payload.get("tp1_price"))
            tp2_price = _parse_float(payload.get("tp2_price"))

            if exit_price is None:
                exit_price = _parse_float(r_[13])
            if not exit_reason:
                exit_reason = r_[14]
            if entry_price is None:
                entry_price = _parse_float(r_[15])
            if stop_price is None:
                stop_price = _parse_float(r_[16])
            if tp1_price is None:
                tp1_price = _parse_float(r_[17])
            if tp2_price is None:
                tp2_price = _parse_float(r_[18])

            item.update({
                "entry_ts": payload_entry_ts.isoformat() if payload_entry_ts else None,
                "exit_ts": payload_exit_ts.isoformat() if payload_exit_ts else None,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "outcome_class": _compute_outcome_class(r_[19], r_[20]),
                "outcome_r_multiple": _parse_float(r_[19]),
                "outcome_pnl_net": _parse_float(r_[20]),
            })

        pg_items.append(item)

    if status == "active" and redis_items:
        seen = set()
        merged = []
        for item in redis_items:
            key = f"{item['setup_id']}:{item['symbol']}:{item.get('timeframe', '')}"
            if key not in seen:
                seen.add(key)
                merged.append(item)
        for item in pg_items:
            key = f"{item['setup_id']}:{item['symbol']}:{item.get('timeframe', '')}"
            if key not in seen:
                seen.add(key)
                merged.append(item)
        merged.sort(key=lambda x: x.get("score", 0) or 0, reverse=True)
        final = merged[:limit]
        return envelope({
            "opportunities": final,
            "count": len(final),
            "source": "merged",
        }, t0)

    return envelope({
        "opportunities": pg_items,
        "count": len(pg_items),
        "source": "postgres",
    }, t0)


# -------------------------
# Live snapshot
# -------------------------
@router.get("/snapshot/minute")
def get_minute_snapshot(
    symbol: str = Query(..., description="Trading pair symbol"),
    timeframe: str = Query(default="1m"),
):
    """Fetch the latest candle directly from Binance REST API."""
    t0 = time.time()
    tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    binance_tf = tf_map.get(timeframe, "1m")

    try:
        resp = http_requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": binance_tf, "limit": 1},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Binance API error: {e}")

    if not data:
        raise HTTPException(status_code=404, detail="No data returned")

    candle = data[-1]
    return envelope({
        "symbol": symbol,
        "timeframe": timeframe,
        "snapshot": {
            "timestamp": candle[0],
            "candle_ts": datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc).isoformat(),
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
        },
    }, t0, as_of_ts=time.time())


# -------------------------
# D2 Status (inline)
# -------------------------
d2_router = APIRouter(prefix="/d2", tags=["d2"])


@d2_router.get("/status")
async def d2_status():
    """Current D2 paper trader state: active clusters, positions, stats."""
    t0 = time.time()
    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, symbol, status, alert_count, max_pct_change,
                       pre_pump_price, cluster_start_ts, created_ts, gate_reason
                FROM d2_cluster_events
                ORDER BY created_ts DESC LIMIT 50
            """)
            cols = [d[0] for d in cur.description]
            clusters = [dict(zip(cols, row)) for row in cur.fetchall()]

            cur.execute("""
                SELECT id, symbol, status, side, entry_price, exit_price,
                       exit_reason, r_multiple, entry_ts, exit_ts
                FROM paper_positions
                WHERE setup_id = 'D2'
                ORDER BY COALESCE(exit_ts, entry_ts) DESC LIMIT 50
            """)
            cols = [d[0] for d in cur.description]
            positions = [dict(zip(cols, row)) for row in cur.fetchall()]

    return envelope({
        "clusters": clusters,
        "positions": positions,
        "cluster_count": len(clusters),
        "position_count": len(positions),
    }, t0)


# =============================================
# Mount the protected inline router
# =============================================
app.include_router(router)

app.include_router(session_router, prefix="/v1")

# =============================================
# Mount domain routers
# =============================================
from api.app.routers.runs import router as runs_router
app.include_router(runs_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.control import router as control_router
app.include_router(control_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.trading import router as trading_router
app.include_router(trading_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.telemetry import router as telemetry_router
app.include_router(telemetry_router, prefix="/v1")

from api.app.routers.public_feed import router as public_feed_router
app.include_router(public_feed_router, prefix="/v1")  # no auth — public endpoint

from api.app.routers.unicorns import router as unicorns_router
app.include_router(unicorns_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.cc import router as cc_router
app.include_router(cc_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.emergency import (
    router as emergency_router,
    regime_router as _phase_e_regime_router,
    system_router as _phase_e_system_router,
)
app.include_router(emergency_router, prefix="/v1", dependencies=[Depends(require_api_key)])
app.include_router(_phase_e_regime_router, prefix="/v1", dependencies=[Depends(require_api_key)])
app.include_router(_phase_e_system_router, prefix="/v1", dependencies=[Depends(require_api_key)])

try:
    from api.app.routers.analytics import router as analytics_router
    app.include_router(analytics_router, prefix="/v1", dependencies=[Depends(require_api_key)])
except ImportError:
    pass

try:
    from api.app.routers.equities import router as equities_router
    app.include_router(equities_router, prefix="/v1", dependencies=[Depends(require_api_key)])
except ImportError:
    pass

try:
    from api.app.routers.equities_sim import router as eq_sim_router
    app.include_router(eq_sim_router, prefix="/v1", dependencies=[Depends(require_api_key)])
except ImportError:
    pass

try:
    from api.app.routers.meta import router as meta_router
    app.include_router(meta_router, prefix="/v1", dependencies=[Depends(require_api_key)])
except ImportError:
    pass

app.include_router(d2_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.stats import router as stats_router
app.include_router(stats_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.strategies import router as strategies_router
app.include_router(strategies_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.market_intel import router as market_intel_router
app.include_router(market_intel_router, prefix="/v1", dependencies=[Depends(require_api_key)])

from api.app.routers.stripe_router import router as stripe_router
app.include_router(stripe_router, prefix="/v1", dependencies=[])  # auth handled per-endpoint
