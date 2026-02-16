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
from pydantic import BaseModel
from middleware import RequestLoggingMiddleware

app = FastAPI(
    title="MCAS Trading Backend",
    description="Market Cognition At Scale - API-first trading analytics",
    version="1.0.0",
)
app.add_middleware(RequestLoggingMiddleware)

# -------------------------
# Config / clients
# -------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL")
API_KEY = os.getenv("API_KEY", "")
DEFAULT_TIMEFRAMES = os.getenv("DEFAULT_TIMEFRAMES", "1m,5m,15m,1h,4h,1d").split(",")

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# Fallback symbols when Redis universe unavailable
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
    "ONDOUSDT", "ENAUSDT", "WUSDT", "ETHFIUSDT", "SAGAUSDT",
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

    # Try to get dynamic universe from Redis first
    symbols = []
    try:
        monitored = r.smembers("universe:monitored")
        if monitored:
            symbols = sorted([s if isinstance(s, str) else s.decode() for s in monitored])
    except Exception:
        pass

    # Fallback to static list if Redis unavailable
    if not symbols:
        symbols = DEFAULT_SYMBOLS

    markets = []
    for symbol in symbols:
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
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    
    return envelope({
        "symbol": symbol,
        "timeframe": tf,
        "candles": candles,
        "count": len(candles),
    }, t0)


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
                    SELECT id, symbol, timeframe, setup_id, direction, score, reason, payload_json, updated_ts
                    FROM opportunities
                    WHERE status = 'active' AND timeframe = %s
                    ORDER BY score DESC, updated_ts DESC
                    LIMIT %s;
                """, (tf, limit))
            else:
                cur.execute("""
                    SELECT id, symbol, timeframe, setup_id, direction, score, reason, payload_json, updated_ts
                    FROM opportunities
                    WHERE status = 'active'
                    ORDER BY score DESC, updated_ts DESC
                    LIMIT %s;
                """, (limit,))
            rows = cur.fetchall()

    signals = []
    for r_ in rows:
        payload = json.loads(r_[7]) if r_[7] else {}
        signals.append({
            "id": r_[0],
            "symbol": r_[1],
            "timeframe": r_[2],
            "setup_id": r_[3],
            "direction": r_[4],
            "score": r_[5],
            "reason": r_[6],
            "entry_price": payload.get("entry_price"),
            "stop_price": payload.get("stop_price"),
            "tp1_price": payload.get("tp1_price"),
            "tp2_price": payload.get("tp2_price"),
            "r_value": payload.get("r_value"),
            "updated_ts": r_[8].isoformat() if r_[8] else None,
        })

    return envelope({"signals": signals, "count": len(signals)}, t0)


# -------------------------
# Opportunities
# -------------------------
@router.get("/opportunities")
def list_opportunities(
    status: str = Query(default="active"),
    min_score: float = Query(default=0.0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List opportunities with filters."""
    t0 = time.time()
    
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, signal_id, symbol, timeframe, setup_id, direction, decision, score, reason, payload_json, updated_ts
                FROM opportunities
                WHERE status = %s AND score >= %s
                ORDER BY score DESC, updated_ts DESC
                LIMIT %s;
            """, (status, min_score, limit))
            rows = cur.fetchall()

    items = []
    for r_ in rows:
        items.append({
            "id": r_[0],
            "signal_id": r_[1],
            "symbol": r_[2],
            "timeframe": r_[3],
            "setup_id": r_[4],
            "direction": r_[5],
            "decision": r_[6],
            "score": r_[7],
            "reason": r_[8],
            "payload": json.loads(r_[9]) if r_[9] else None,
            "updated_ts": r_[10].isoformat() if r_[10] else None,
        })

    return envelope({"opportunities": items, "count": len(items)}, t0)


@router.get("/opportunities/{opp_id}")
def get_opportunity(opp_id: int):
    """Get single opportunity."""
    t0 = time.time()
    
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, signal_id, symbol, timeframe, setup_id, direction, decision, score, reason, payload_json, status, updated_ts
                FROM opportunities
                WHERE id = %s;
            """, (opp_id,))
            r_ = cur.fetchone()

    if not r_:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    return envelope({
        "id": r_[0],
        "signal_id": r_[1],
        "symbol": r_[2],
        "timeframe": r_[3],
        "setup_id": r_[4],
        "direction": r_[5],
        "decision": r_[6],
        "score": r_[7],
        "reason": r_[8],
        "payload": json.loads(r_[9]) if r_[9] else None,
        "status": r_[10],
        "updated_ts": r_[11].isoformat() if r_[11] else None,
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
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
        },
    }, t0)


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
