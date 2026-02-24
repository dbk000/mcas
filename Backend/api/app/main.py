"""
MCAS Trading Backend API
========================

API-first architecture: UI is a thin client, all logic runs here.

Endpoints:
- /health - Health check (unprotected)
- /v1/auth/ping - API key validation
- /v1/markets - Symbol universe
- /v1/timeframes - Supported timeframes
- /v1/ohlcv - Candle data
- /v1/squeeze/state - BB Squeeze state (for S1)
- /v1/btc/squeeze-status - BTC correlation filter status
- /v1/signals/queue - Queue a signal job
- /v1/signals/{id} - Get signal result
- /v1/signals/top - Get top N signals (ranked)
- /v1/opportunities - List opportunities
- /v1/opportunities/{id} - Get single opportunity
- /v1/snapshot/minute - Live candle from Binance
"""

import os
import csv
import io
import time
import json
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional, Any

import requests
import psycopg
import redis

from json_logging import setup_logging

# Initialize structured JSON logging before anything else
setup_logging()

# Force uvicorn loggers to use our JSON handler
for _uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _uv_logger = logging.getLogger(_uv_name)
    _uv_logger.handlers.clear()
    _uv_logger.propagate = True

from fastapi import (
    FastAPI,
    Query,
    Depends,
    Header,
    HTTPException,
    APIRouter,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from middleware import RequestLoggingMiddleware

app = FastAPI(
    title="MCAS Trading Backend",
    description="Market Cognition At Scale - API-first trading analytics",
    version="1.0.0",
)

# CORS: Allow frontend origins (production + dev)
_CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _CORS_ORIGINS.split(",") if o.strip()] if _CORS_ORIGINS else []
_cors_origins += [
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
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL")
API_KEY = os.getenv("API_KEY", "")
DEFAULT_TIMEFRAMES = os.getenv("DEFAULT_TIMEFRAMES", "1m,5m,15m,1h,4h,1d").split(",")
SIGNAL_MAX_AGE_S = 3600  # Signals older than this are considered expired

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# DEPRECATED: hardcoded fallback only. Ingestor builds universe dynamically
# from Binance (Canon §1.4C). Prefer reading `universe:monitored` or
# `universe:known` from Redis for the live universe.
DEFAULT_SYMBOLS = [
    # Tier 1 - Majors
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    # Tier 2 - Large caps
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "ETCUSDT",
    "XLMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "FILUSDT", "LDOUSDT", "AAVEUSDT", "MKRUSDT", "GRTUSDT",
    "INJUSDT", "IMXUSDT", "STXUSDT", "RUNEUSDT", "EGLDUSDT",
    "THETAUSDT", "AXSUSDT", "SANDUSDT", "MANAUSDT", "GALAUSDT",
    "APEUSDT", "CHZUSDT", "FLOWUSDT", "ZILUSDT", "ENJUSDT",
    "BATUSDT", "ZECUSDT", "DASHUSDT", "NEOUSDT", "WAVESUSDT",
    "QNTUSDT", "KAVAUSDT", "MINAUSDT", "XTZUSDT", "SNXUSDT",
    # Tier 3 - Mid caps
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
    # Tier 4 - Active mid/small caps
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
    # Tier 5 - Extended universe
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
    """Standard response envelope with metadata.

    Canon §1.5A: Every response includes as_of_ts (backend data freshness).
    as_of_ts is the UTC timestamp of when the data was last computed/refreshed
    on the backend — distinct from meta.timestamp which is the response time.
    """
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "v1",
        "compute_ms": round((time.time() - compute_start) * 1000, 1),
    }
    if as_of_ts is not None:
        meta["as_of_ts"] = datetime.fromtimestamp(as_of_ts, tz=timezone.utc).isoformat()
    else:
        meta["as_of_ts"] = datetime.now(timezone.utc).isoformat()
    return {
        "data": data,
        "meta": meta,
    }


# -------------------------
# Auth (API key)
# -------------------------
def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API key not configured")
    if not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


# All /v1 routes protected by API key
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
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
    pg_ms = int((time.time() - t1) * 1000)

    return {"status": "healthy", "redis_ms": redis_ms, "pg_ms": pg_ms}


# -------------------------
# Auth endpoint
# -------------------------
@router.get("/auth/ping")
def auth_ping():
    """Validate API key."""
    t0 = time.time()
    return envelope({"authenticated": True}, t0)


# -------------------------
# Markets / Universe
# -------------------------
@router.get("/markets")
def list_markets():
    """List available symbols with metadata."""
    t0 = time.time()

    # For MVP, return static list with basic metadata
    markets = []
    for symbol in DEFAULT_SYMBOLS:
        markets.append({
            "symbol": symbol,
            "base": symbol.replace("USDT", ""),
            "quote": "USDT",
            "status": "active",
            "liquidity_tier": 1 if symbol in ["BTCUSDT", "ETHUSDT"] else 2,
        })

    return envelope({"markets": markets, "count": len(markets)}, t0)


@router.get("/timeframes")
def list_timeframes():
    """List supported timeframes."""
    t0 = time.time()
    return envelope({"timeframes": DEFAULT_TIMEFRAMES}, t0)


# -------------------------
# OHLCV
# -------------------------
@router.get("/ohlcv")
def get_ohlcv(
    symbol: str = Query(..., description="Trading pair, e.g. BTCUSDT"),
    tf: str = Query(default="1h", description="Timeframe"),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get OHLCV candles from Binance."""
    t0 = time.time()

    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": tf, "limit": limit}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Binance API error: {e}")

    candles = []
    for k in data:
        candles.append({
            "timestamp": k[0],
            "candle_ts": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })

    as_of = time.time()
    return envelope({
        "symbol": symbol,
        "timeframe": tf,
        "candles": candles,
        "count": len(candles),
    }, t0, as_of_ts=as_of)


# -------------------------
# Squeeze State (S1)
# -------------------------
@router.get("/squeeze/state")
def get_squeeze_state(
    symbol: str = Query(..., description="Trading pair"),
    tf: str = Query(default="1h", description="Timeframe"),
):
    """
    Get BB Squeeze state for a symbol/timeframe.

    Returns Tier 3 state useful for:
    - UI showing "squeeze brewing" before trigger
    - Debugging setup evaluation
    """
    t0 = time.time()

    # Check Redis cache first
    cache_key = f"squeeze_state:{symbol}:{tf}"
    cached = r.get(cache_key)
    if cached:
        return envelope(json.loads(cached), t0)

    # Otherwise compute (would normally call signal engine)
    # For now, return placeholder that UI can use
    return envelope({
        "symbol": symbol,
        "timeframe": tf,
        "squeeze_on": None,
        "squeeze_age": None,
        "bb_width_pctile": None,
        "ripe": None,
        "box_high": None,
        "box_low": None,
        "bias_score": None,
        "btc_severe": None,
        "btc_wait_state": "unknown",
        "note": "State computed by worker; check opportunities for latest signals",
    }, t0)


@router.get("/btc/squeeze-status")
def get_btc_squeeze_status(
    tf: str = Query(default="1h", description="Timeframe"),
):
    """
    Get BTC squeeze state for correlation filter.

    Used by UI to show "waiting for BTC" status.
    """
    t0 = time.time()

    cache_key = f"btc_squeeze:{tf}"
    cached = r.get(cache_key)
    if cached:
        return envelope(json.loads(cached), t0)

    return envelope({
        "symbol": "BTCUSDT",
        "timeframe": tf,
        "squeeze_on": None,
        "bb_width_pctile": None,
        "box_high": None,
        "box_low": None,
        "breakout_direction": None,
        "note": "State computed by worker",
    }, t0)


# -------------------------
# Signals
# -------------------------
class SignalRequest(BaseModel):
    symbol: str
    timeframe: str
    setup_id: str = "S1"


@router.post("/signals/queue")
def queue_signal(req: SignalRequest):
    """Queue a signal for processing by worker."""
    t0 = time.time()

    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with psycopg.connect(DATABASE_URL) as conn:
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

    with psycopg.connect(DATABASE_URL) as conn:
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

    with psycopg.connect(DATABASE_URL) as conn:
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
        "id": _id,
        "symbol": symbol,
        "timeframe": timeframe,
        "setup_id": setup_id,
        "status": status,
        "result": json.loads(result_json) if result_json else None,
    }, t0)


# -------------------------
# Opportunities (Redis fast path)
# -------------------------
logger = logging.getLogger(__name__)


def _read_opportunities_from_redis(min_score: float = 0.0, limit: int = 50, setup_id: str = None):
    """Read live signals from Redis ZSETs (signal_worker output).

    Returns list of opportunity dicts if Redis is available (may be empty),
    or None if Redis is unavailable / cache not ready.
    """
    try:
        # Check if cache is ready
        cache_ready = r.get("cache:ready")
        if cache_ready != "1":
            return None

        setup_ids = [setup_id] if setup_id else ["S9", "U1", "S10", "EQ_TSMOM", "EQ_XSMOM", "EQ_REV1A", "EQ_REV1B", "EQ_VPD"]
        timeframes = list(r.smembers("cache:timeframes")) or DEFAULT_TIMEFRAMES
        results = []
        seen = set()
        now_ts = time.time()

        for sid in setup_ids:
            for tf in timeframes:
                zset_key = f"signals:score:{sid}:{tf}"
                # ZREVRANGEBYSCORE: highest first, filter by min_score
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

                    # Skip expired signals (equities get 24h, crypto gets default 1h)
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

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    except Exception as e:
        logger.debug(f"Redis opportunities read failed: {e}")
        return None


@router.get("/opportunities")
def list_opportunities(
    status: str = Query(default="active"),
    setup_id: Optional[str] = Query(default=None),
    min_score: float = Query(default=0.0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List opportunities with filters.

    For status='active', tries Redis ZSETs first (live signals from signal_worker),
    falls back to Postgres if empty or stale.
    For other statuses, queries Postgres directly.
    """
    t0 = time.time()

    # Only use Redis for active signals (Redis only stores active signals)
    if status == "active":
        redis_items = _read_opportunities_from_redis(min_score, limit, setup_id=setup_id)
        if redis_items is not None:
            return envelope({
                "opportunities": redis_items,
                "count": len(redis_items),
                "source": "redis_live",
            }, t0)

    # Fallback to Postgres (or primary source for non-active statuses)
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # For non-active statuses (e.g. 'expired'), dedup to one row per
            # (symbol, tf, setup_id) taking the highest-scored version.
            # Active status is already deduped (one active per combo).
            if status != "active":
                query = """
                    SELECT DISTINCT ON (symbol, timeframe, setup_id)
                           id, signal_id, symbol, timeframe, setup_id, decision, score, reason, payload_json, updated_ts
                    FROM opportunities
                    WHERE status = %s AND score >= %s
                """
            else:
                query = """
                    SELECT id, signal_id, symbol, timeframe, setup_id, decision, score, reason, payload_json, updated_ts
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

    items = []
    for r_ in rows:
        payload = json.loads(r_[8]) if r_[8] else None
        items.append({
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
            "source": "postgres",
        })

    return envelope({
        "opportunities": items,
        "count": len(items),
        "source": "postgres",
    }, t0)


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

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # direction is stored inside payload_json, not as a top-level column
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
            v.isoformat() if hasattr(v, 'isoformat') else v
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

        # Check score from ZSET
        zset_key = f"signals:score:{setup_id}:{tf}"
        score = r.zscore(zset_key, symbol)
        if score is None:
            score = 0.0

        max_age = 86400 if setup_id.startswith("EQ_") else SIGNAL_MAX_AGE_S
        expires_at = (as_of_ts + max_age) if as_of_ts > 0 else None

        return envelope({
            "id": f"{setup_id}:{symbol}:{tf}",
            "symbol": symbol,
            "timeframe": tf,
            "setup_id": setup_id,
            "direction": payload.get("direction"),
            "score": score,
            "reason": "; ".join(payload.get("why_items", [])) if payload.get("why_items") else None,
            "payload": payload,
            "status": "active",
            "as_of_ts": as_of_ts,
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


@router.get("/opportunities/{opp_id}")
def get_opportunity(opp_id: int):
    """Get single opportunity."""
    t0 = time.time()

    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, signal_id, symbol, timeframe, setup_id, decision, score, reason, payload_json, status, updated_ts
                FROM opportunities
                WHERE id = %s;
            """, (opp_id,))
            r_ = cur.fetchone()

    if not r_:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    payload = json.loads(r_[8]) if r_[8] else None
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
    }, t0)


# -------------------------
# Snapshot (live candle)
# -------------------------
@router.get("/snapshot/minute")
def get_snapshot(
    symbol: str = Query(...),
    timeframe: str = Query(default="1m"),
):
    """Get latest candle from Binance."""
    t0 = time.time()

    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": timeframe, "limit": 1}

    try:
        resp = requests.get(url, params=params, timeout=10)
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


# Mount the protected router
app.include_router(router)

# Mount runs API (also protected)
from runs_api import router as runs_router
app.include_router(runs_router, prefix="/v1", dependencies=[Depends(require_api_key)])

# Mount control plane API (also protected)
from control_api import router as control_router
app.include_router(control_router, prefix="/v1", dependencies=[Depends(require_api_key)])

# Mount trading API (account, positions, trades - also protected)
from trading_api import router as trading_router
app.include_router(trading_router, prefix="/v1", dependencies=[Depends(require_api_key)])

# Mount telemetry API (no API key auth - fire-and-forget from frontend)
from telemetry_api import router as telemetry_router
app.include_router(telemetry_router, prefix="/v1")

# Mount unicorns API (also protected)
from unicorns_api import router as unicorns_router
app.include_router(unicorns_router, prefix="/v1", dependencies=[Depends(require_api_key)])

# Mount Champion/Challenger API (also protected)
from cc_api import router as cc_router
app.include_router(cc_router, prefix="/v1", dependencies=[Depends(require_api_key)])

# Mount Analytics API (correlation analysis, etc.)
try:
    from analytics_api import router as analytics_router
    app.include_router(analytics_router, prefix="/v1", dependencies=[Depends(require_api_key)])
except ImportError:
    pass

# Mount Equities Forward Predictor API
try:
    from equities_api import router as equities_router
    app.include_router(equities_router, prefix="/v1", dependencies=[Depends(require_api_key)])
except ImportError:
    pass  # psycopg_pool not available in this container

# Mount Equities Simulation API
try:
    from equities_sim_runner import router as eq_sim_router
    app.include_router(eq_sim_router, prefix="/v1", dependencies=[Depends(require_api_key)])
except ImportError:
    pass
