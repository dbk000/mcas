"""
D2 Paper Trader — "Fade the Casino"
====================================

Standalone asyncio service that:
1. Polls social_posts for new WhaleBotPumps messages (cluster detection)
2. Gates clusters (mania, liquidity, perp listing)
3. Tracks HWM via Binance REST 1m candles
4. Manages paper short positions (entry/stop/TP/time-stop)
5. Writes results to paper_positions + d2_cluster_events

Container: d2_paper_trader (separate from signal_worker)
Config: all D2_ env vars (see D2Config below)

Usage:
    python -u d2_paper_trader.py
"""

import asyncio
import bisect
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from core_logic.d2_signal_parser import parse_whalebotpumps

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("d2_paper_trader")


# ═══════════════════════════════════════════════════════════════════════════
# Configuration — populated from frozen ConfigRegistry (D2 + Worker)
# ═══════════════════════════════════════════════════════════════════════════

def _d2_cfg():
    """Read D2TypedConfig from frozen ConfigRegistry."""
    from governance.bootstrap import get_config
    return get_config().D2


def _worker_cfg():
    """Read WorkerTypedConfig from frozen ConfigRegistry."""
    from governance.bootstrap import get_config
    return get_config().Worker


class _D2ConfigProxy:
    """Lazy proxy that reads from the frozen D2TypedConfig + Worker config.

    Provides the same attribute interface as the old D2Config dataclass
    so all downstream code continues to work via CFG.attribute.
    """
    @property
    def entry_threshold(self): return _d2_cfg().entry_threshold
    @property
    def stop_pct(self): return _d2_cfg().stop_pct
    @property
    def time_stop_hours(self): return _d2_cfg().time_stop_hours
    @property
    def max_watch_hours(self): return _d2_cfg().max_watch_hours
    @property
    def mania_gate_pct(self): return _d2_cfg().mania_gate_pct
    @property
    def liquidity_gate_usd(self): return _d2_cfg().liquidity_gate_usd
    @property
    def effective_risk_pct(self): return _d2_cfg().effective_risk_pct
    @property
    def funding_gate_enabled(self): return _d2_cfg().funding_gate_enabled
    @property
    def conflict_gate_enabled(self): return _d2_cfg().conflict_gate_enabled
    @property
    def fng_soft_gate(self): return _d2_cfg().fng_soft_gate
    @property
    def candle_interval(self): return _d2_cfg().candle_interval
    @property
    def poll_interval_s(self): return _d2_cfg().poll_interval_s
    @property
    def cluster_poll_s(self): return _d2_cfg().cluster_poll_s
    @property
    def max_concurrent(self): return _d2_cfg().max_concurrent
    @property
    def risk_per_trade_pct(self): return _d2_cfg().risk_per_trade_pct
    @property
    def account_size_usd(self): return _d2_cfg().account_size_usd
    @property
    def database_url(self): return _worker_cfg().DATABASE_URL
    @property
    def redis_url(self): return _worker_cfg().REDIS_URL


CFG = _D2ConfigProxy()

# Cluster detection constants
CLUSTER_WINDOW_HOURS = 4
MIN_ALERTS_PER_CLUSTER = 3

# WhaleBotPumps source identifiers
WHALEBOT_SOURCES = {
    ("telegram_historical", "WhaleBotPumps"),
    ("telegram_historical", "WhaleBotAlerts"),
    ("telegram", "WhaleBotPumps"),
    ("telegram", "WhaleBotAlerts"),
}


def _binance_fapi_url() -> str:
    """Read BINANCE_FAPI_URL from frozen ConfigRegistry."""
    return _worker_cfg().BINANCE_FAPI_URL


# ═══════════════════════════════════════════════════════════════════════════
# D2 State Machine
# ═══════════════════════════════════════════════════════════════════════════

class D2Phase(str, Enum):
    GATING = "GATING"
    TRACKING_HWM = "TRACKING_HWM"
    POSITION_ACTIVE = "POSITION_ACTIVE"
    COMPLETED = "COMPLETED"


@dataclass
class D2State:
    """Per-symbol state for D2 paper trader."""
    symbol: str
    phase: D2Phase
    cluster_event_id: int
    cluster_start_ts: datetime
    max_watch_ts: datetime
    pre_pump_price: float
    max_pct_change: float
    vol_24h_usd: float
    funding_rate: Optional[float] = None
    alert_count: int = 0
    fng_value: Optional[int] = None
    fng_classification: Optional[str] = None

    # HWM tracking
    live_hwm: float = 0.0
    hwm_ts: Optional[datetime] = None

    # Position fields (set on entry)
    entry_price: Optional[float] = None
    entry_ts: Optional[datetime] = None
    entry_hwm: Optional[float] = None
    signal_price: Optional[float] = None
    stop_price: Optional[float] = None
    tp_price: Optional[float] = None
    time_stop_ts: Optional[datetime] = None
    position_size: Optional[float] = None
    notional_value: Optional[float] = None
    risk_amount: Optional[float] = None
    slippage_bps: Optional[float] = None
    execution_price: Optional[float] = None

    # Tracking
    bars_polled: int = 0
    last_price: Optional[float] = None
    state_transitions: list = field(default_factory=list)

    # DB row ID (set when OPEN position written at entry)
    paper_position_id: Optional[str] = None

    def log_transition(self, from_phase: str, to_phase: str, reason: str = ""):
        self.state_transitions.append({
            "from": from_phase,
            "to": to_phase,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        })


# ═══════════════════════════════════════════════════════════════════════════
# Binance REST helpers
# ═══════════════════════════════════════════════════════════════════════════

async def fetch_binance_klines(symbol: str, interval: str = "1m", limit: int = 2) -> list[dict]:
    """Fetch klines from Binance FAPI REST. Returns list of candle dicts."""
    import aiohttp
    url = f"{_binance_fapi_url()}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("Binance klines %s: HTTP %d", symbol, resp.status)
                    return []
                data = await resp.json()
                return [
                    {
                        "open_ts": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "close_ts": int(k[6]),
                    }
                    for k in data
                ]
    except Exception as e:
        logger.error("Binance klines %s error: %s", symbol, e)
        return []


async def fetch_binance_exchange_info() -> set[str]:
    """Fetch Binance USDT-M perpetual futures symbols."""
    import aiohttp
    url = f"{_binance_fapi_url()}/fapi/v1/exchangeInfo"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Binance exchangeInfo: HTTP %d", resp.status)
                    return set()
                data = await resp.json()
                perps = {
                    s["symbol"] for s in data["symbols"]
                    if s.get("contractType") == "PERPETUAL" and s["symbol"].endswith("USDT")
                }
                logger.info("Binance perps cache: %d symbols", len(perps))
                return perps
    except Exception as e:
        logger.error("Binance exchangeInfo error: %s", e)
        return set()


async def fetch_binance_funding_rate(symbol: str) -> Optional[float]:
    """Fetch current funding rate for a symbol."""
    import aiohttp
    url = f"{_binance_fapi_url()}/fapi/v1/premiumIndex"
    params = {"symbol": symbol}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return float(data.get("lastFundingRate", 0))
    except Exception:
        return None


# Fear & Greed Index — delegated to governance/regime.py (shared across workers)
from governance.regime import fetch_fear_greed_index_async as fetch_fear_greed_index


async def fetch_ohlcv_from_db(conn, symbol: str, start_ts: datetime, interval: str = "1h") -> list[tuple]:
    """Fetch OHLCV bars from local DB. Returns [(ts, o, h, l, c), ...]."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT bar_start_ts, open, high, low, close
            FROM ohlcv
            WHERE symbol = %s AND timeframe = %s AND bar_start_ts >= %s
            ORDER BY bar_start_ts
        """, (symbol, interval, start_ts))
        return cur.fetchall()


async def fetch_pre_pump_price(conn, symbol: str, cluster_start_ts: datetime) -> Optional[float]:
    """Get close of 1h bar immediately before cluster_start_ts.

    Tries local DB first, falls back to Binance REST.
    """
    # Try local DB
    with conn.cursor() as cur:
        cur.execute("""
            SELECT close FROM ohlcv
            WHERE symbol = %s AND timeframe = '1h' AND bar_start_ts < %s
            ORDER BY bar_start_ts DESC LIMIT 1
        """, (symbol, cluster_start_ts))
        row = cur.fetchone()
        if row:
            return float(row["close"] if isinstance(row, dict) else row[0])

    # Fallback: Binance REST
    import aiohttp
    end_ms = int(cluster_start_ts.timestamp() * 1000)
    start_ms = end_ms - 7200_000  # 2h before
    url = f"{_binance_fapi_url()}/fapi/v1/klines"
    params = {
        "symbol": symbol, "interval": "1h",
        "startTime": start_ms, "endTime": end_ms, "limit": 2,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data:
                    return None
                # Last bar whose open_time < cluster_start_ts
                for k in reversed(data):
                    if int(k[0]) < end_ms:
                        return float(k[4])  # close
                return None
    except Exception as e:
        logger.error("Binance pre-pump price %s error: %s", symbol, e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Cluster Detector
# ═══════════════════════════════════════════════════════════════════════════

class ClusterDetector:
    """Polls social_posts for WhaleBotPumps messages, detects pump clusters."""

    def __init__(self, conn):
        self.conn = conn
        self.last_seen_id = 0
        self._init_last_seen()
        # In-memory sliding window of recent alerts per symbol
        self.recent_alerts: dict[str, list[dict]] = defaultdict(list)
        # Set of cluster keys already emitted (to avoid duplicates)
        self.emitted_clusters: set[str] = set()

    def _init_last_seen(self):
        """Start from the latest post_id to avoid replaying history."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM social_posts")
            row = cur.fetchone()
            self.last_seen_id = row["max_id"] if isinstance(row, dict) else row[0]
        logger.info("Cluster detector: starting from post_id > %d", self.last_seen_id)

    def poll(self) -> list[dict]:
        """Poll for new WhaleBotPumps posts and detect clusters.

        Returns list of newly detected cluster dicts.
        """
        new_posts = self._fetch_new_posts()
        if not new_posts:
            return []

        # Parse posts
        for post in new_posts:
            parsed = parse_whalebotpumps(post["id"], post["content"], post["post_ts"])
            if parsed and parsed.get("signal_type") == "pump":
                self.recent_alerts[parsed["symbol"]].append(parsed)

        # Update last_seen
        self.last_seen_id = max(p["id"] for p in new_posts)

        # Prune old alerts (outside 4h window from now)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=CLUSTER_WINDOW_HOURS)
        for sym in list(self.recent_alerts.keys()):
            self.recent_alerts[sym] = [
                a for a in self.recent_alerts[sym]
                if a["post_ts"] >= cutoff
            ]
            if not self.recent_alerts[sym]:
                del self.recent_alerts[sym]

        # Detect clusters
        new_clusters = []
        for sym, alerts in self.recent_alerts.items():
            if len(alerts) < MIN_ALERTS_PER_CLUSTER:
                continue
            cluster_key = f"{sym}:{alerts[0]['post_ts'].isoformat()}"
            if cluster_key in self.emitted_clusters:
                continue
            cluster = self._build_cluster(sym, alerts)
            new_clusters.append(cluster)
            self.emitted_clusters.add(cluster_key)
            logger.info(
                "CLUSTER DETECTED: %s — %d alerts, max_pct=%.1f%%",
                sym, len(alerts), cluster.get("max_pct_change", 0) or 0,
            )

        return new_clusters

    def _fetch_new_posts(self) -> list[dict]:
        """Fetch new WhaleBotPumps posts since last_seen_id."""
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT id, source, channel, content, post_ts
                FROM social_posts
                WHERE id > %s
                  AND (
                    (source = 'telegram_historical' AND channel = 'WhaleBotPumps')
                    OR (source = 'telegram_historical' AND channel = 'WhaleBotAlerts')
                    OR (source = 'telegram' AND channel = 'WhaleBotPumps')
                    OR (source = 'telegram' AND channel = 'WhaleBotAlerts')
                  )
                ORDER BY id
                LIMIT 500
            """, (self.last_seen_id,))
            return cur.fetchall()

    def _build_cluster(self, symbol: str, alerts: list[dict]) -> dict:
        pct_changes = [a["pct_change"] for a in alerts if a.get("pct_change") is not None]
        funding_rates = [a["funding_rate"] for a in alerts if a.get("funding_rate") is not None]
        vol_24h_values = [a["vol_24h"] for a in alerts if a.get("vol_24h") is not None]
        exchanges = set(a.get("exchange") for a in alerts if a.get("exchange"))
        post_ids = [a["source_post_id"] for a in alerts if a.get("source_post_id")]

        return {
            "symbol": symbol,
            "cluster_start_ts": alerts[0]["post_ts"],
            "cluster_end_ts": alerts[-1]["post_ts"],
            "alert_count": len(alerts),
            "max_pct_change": max(pct_changes) if pct_changes else None,
            "first_price": alerts[0].get("price"),
            "vol_24h_usd": max(vol_24h_values) if vol_24h_values else None,
            "funding_rate": funding_rates[-1] if funding_rates else None,
            "exchanges_seen": sorted(exchanges),
            "source_post_ids": post_ids,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Gate Checks
# ═══════════════════════════════════════════════════════════════════════════

async def run_gates(cluster: dict, perp_symbols: set[str]) -> tuple[bool, str]:
    """Run all gates on a cluster. Returns (passed, reason)."""
    symbol = cluster["symbol"]

    # Gate 1: Binance perp listing
    if symbol not in perp_symbols:
        return False, f"not_binance_perp:{symbol}"

    # Gate 2: Mania gate
    max_pct = cluster.get("max_pct_change")
    if max_pct is not None and max_pct > CFG.mania_gate_pct:
        return False, f"mania_extreme:{max_pct:.1f}%>{CFG.mania_gate_pct}%"

    # Gate 3: Liquidity gate
    vol_24h = cluster.get("vol_24h_usd")
    if vol_24h is not None and vol_24h < CFG.liquidity_gate_usd:
        return False, f"low_liquidity:${vol_24h:,.0f}<${CFG.liquidity_gate_usd:,.0f}"

    # Gate 4: Funding gate (OFF — tag only)
    # Not filtering, but logged

    return True, ""


# ═══════════════════════════════════════════════════════════════════════════
# Position Sizing
# ═══════════════════════════════════════════════════════════════════════════

def calculate_position(entry_price: float, stop_price: float) -> dict:
    """Calculate position size using p75 adverse excursion effective risk."""
    stop_distance_pct = abs(stop_price - entry_price) / entry_price
    effective_risk_pct = max(stop_distance_pct, CFG.effective_risk_pct / 100)
    risk_amount = CFG.account_size_usd * (CFG.risk_per_trade_pct / 100)
    position_size_usd = risk_amount / effective_risk_pct
    position_size_units = position_size_usd / entry_price

    return {
        "effective_risk_pct": round(effective_risk_pct * 100, 2),
        "risk_amount": round(risk_amount, 2),
        "position_size_usd": round(position_size_usd, 2),
        "position_size_units": round(position_size_units, 6),
    }


# ═══════════════════════════════════════════════════════════════════════════
# DB Persistence
# ═══════════════════════════════════════════════════════════════════════════

def write_cluster_event(conn, cluster: dict, status: str, gate_reason: str = None,
                        pre_pump_price: float = None, fng: dict = None) -> int:
    """Insert or update d2_cluster_events. Returns event ID."""
    metadata = {
        "exchanges_seen": cluster.get("exchanges_seen", []),
    }
    if fng:
        metadata["fng_value"] = fng.get("value")
        metadata["fng_classification"] = fng.get("classification")

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO d2_cluster_events
                (symbol, signal_type, exchange, alert_count, cluster_start_ts,
                 cluster_end_ts, first_price, max_pct_change, vol_24h_usd,
                 funding_rate, pre_pump_price, status, gate_reason,
                 source_post_ids, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            cluster["symbol"],
            "pump",
            cluster.get("exchanges_seen", [None])[0] if cluster.get("exchanges_seen") else None,
            cluster["alert_count"],
            cluster["cluster_start_ts"],
            cluster.get("cluster_end_ts"),
            cluster.get("first_price"),
            cluster.get("max_pct_change"),
            cluster.get("vol_24h_usd"),
            cluster.get("funding_rate"),
            pre_pump_price,
            status,
            gate_reason,
            cluster.get("source_post_ids"),
            json.dumps(metadata),
        ))
        row = cur.fetchone()
        event_id = row["id"] if isinstance(row, dict) else row[0]
    conn.commit()
    return event_id


def update_cluster_status(conn, event_id: int, status: str, **extra_fields):
    """Update cluster event status and optional fields."""
    sets = ["status = %s", "updated_ts = NOW()"]
    vals = [status]
    for k, v in extra_fields.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    vals.append(event_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE d2_cluster_events SET {', '.join(sets)} WHERE id = %s",
            vals,
        )
    conn.commit()


def write_open_paper_position(conn, state: D2State) -> str:
    """Write OPEN D2 position to paper_positions at entry time.

    Makes open positions visible to the dashboard. Returns the position ID
    stored in state.paper_position_id for later UPDATE on close.
    """
    pos_id = str(uuid.uuid4())
    run_id = _get_or_create_d2_run(conn)
    risk = state.stop_price - state.entry_price if state.stop_price and state.entry_price else 0

    entry_trace = {
        "cluster_event_id": state.cluster_event_id,
        "cluster_start_ts": state.cluster_start_ts.isoformat(),
        "pre_pump_price": state.pre_pump_price,
        "max_pct_change": state.max_pct_change,
        "alert_count": state.alert_count,
        "entry_hwm": state.entry_hwm,
        "signal_price": state.signal_price,
        "execution_price": state.execution_price,
        "slippage_bps": state.slippage_bps,
        "funding_rate_at_entry": state.funding_rate,
        "vol_24h_at_entry": state.vol_24h_usd,
        "fng_value": state.fng_value,
        "fng_classification": state.fng_classification,
        "config": {
            "entry_threshold": CFG.entry_threshold,
            "stop_pct": CFG.stop_pct,
            "time_stop_hours": CFG.time_stop_hours,
            "max_watch_hours": CFG.max_watch_hours,
            "mania_gate_pct": CFG.mania_gate_pct,
            "candle_interval": CFG.candle_interval,
        },
    }

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO paper_positions
                (id, run_id, symbol, timeframe, side, setup_id, status,
                 signal_ts, signal_price, entry_ts, entry_price,
                 entry_price_with_slippage, stop_price, tp1_price,
                 r_value, position_size, notional_value, risk_amount,
                 high_water_mark, trace_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s)
        """, (
            pos_id, run_id, state.symbol, "1h", "short", "D2", "OPEN",
            state.cluster_start_ts, state.signal_price,
            state.entry_ts, state.entry_price,
            state.execution_price,
            state.stop_price, state.tp_price,
            risk,
            state.position_size, state.notional_value, state.risk_amount,
            state.entry_hwm,
            json.dumps(entry_trace),
        ))
    conn.commit()
    logger.info("POSITION OPEN: %s id=%s entry=%.4f", state.symbol, pos_id[:8], state.entry_price)
    return pos_id


def write_paper_position(conn, state: D2State, exit_reason: str,
                         exit_price: float, exit_ts: datetime) -> str:
    """Close a D2 paper position — UPDATE existing OPEN row or INSERT if missing."""
    # PnL for short: entry - exit
    pnl_pct = (state.entry_price - exit_price) / state.entry_price
    risk = state.stop_price - state.entry_price
    r_multiple = (state.entry_price - exit_price) / risk if risk > 0 else 0
    hours_held = (exit_ts - state.entry_ts).total_seconds() / 3600
    n_funding_epochs = int(hours_held / 8)

    trace = {
        "cluster_event_id": state.cluster_event_id,
        "cluster_start_ts": state.cluster_start_ts.isoformat(),
        "pre_pump_price": state.pre_pump_price,
        "max_pct_change": state.max_pct_change,
        "alert_count": state.alert_count,
        "entry_hwm": state.entry_hwm,
        "signal_price": state.signal_price,
        "execution_price": state.execution_price,
        "slippage_bps": state.slippage_bps,
        "funding_rate_at_entry": state.funding_rate,
        "vol_24h_at_entry": state.vol_24h_usd,
        "fng_value": state.fng_value,
        "fng_classification": state.fng_classification,
        "n_funding_epochs": n_funding_epochs,
        "hours_held": round(hours_held, 1),
        "bars_polled": state.bars_polled,
        "state_transitions": state.state_transitions,
        "config": {
            "entry_threshold": CFG.entry_threshold,
            "stop_pct": CFG.stop_pct,
            "time_stop_hours": CFG.time_stop_hours,
            "max_watch_hours": CFG.max_watch_hours,
            "mania_gate_pct": CFG.mania_gate_pct,
            "candle_interval": CFG.candle_interval,
        },
    }

    realized_pnl = round(pnl_pct * (state.notional_value or 0), 2)

    if state.paper_position_id:
        # UPDATE existing OPEN row
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE paper_positions SET
                    status = 'CLOSED',
                    current_price = %s,
                    unrealized_pnl = %s,
                    bars_held = %s,
                    exit_ts = %s,
                    exit_price = %s,
                    exit_reason = %s,
                    realized_pnl = %s,
                    r_multiple = %s,
                    trace_json = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                exit_price, realized_pnl, state.bars_polled,
                exit_ts, exit_price, exit_reason,
                realized_pnl, round(r_multiple, 4),
                json.dumps(trace),
                state.paper_position_id,
            ))
        conn.commit()
        pos_id = state.paper_position_id
    else:
        # Fallback: INSERT if no OPEN row exists (e.g. positions opened before this change)
        pos_id = str(uuid.uuid4())
        run_id = _get_or_create_d2_run(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO paper_positions
                    (id, run_id, symbol, timeframe, side, setup_id, status,
                     signal_ts, signal_price, entry_ts, entry_price,
                     entry_price_with_slippage, stop_price, tp1_price,
                     r_value, position_size, notional_value, risk_amount,
                     current_price, unrealized_pnl, high_water_mark, bars_held,
                     exit_ts, exit_price, exit_reason, realized_pnl, r_multiple,
                     trace_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                pos_id, run_id, state.symbol, "1h", "short", "D2", "CLOSED",
                state.cluster_start_ts, state.signal_price,
                state.entry_ts, state.entry_price,
                state.execution_price,
                state.stop_price, state.tp_price,
                risk,
                state.position_size, state.notional_value, state.risk_amount,
                exit_price, realized_pnl,
                state.entry_hwm, state.bars_polled,
                exit_ts, exit_price, exit_reason,
                realized_pnl, round(r_multiple, 4),
                json.dumps(trace),
            ))
        conn.commit()

    logger.info(
        "POSITION CLOSED: %s %s exit=%.4f R=%.2f pnl=%.2f%%",
        state.symbol, exit_reason, exit_price, r_multiple, pnl_pct * 100,
    )
    return pos_id


_D2_RUN_ID: Optional[str] = None

def _get_or_create_d2_run(conn) -> str:
    """Get or create a persistent D2 paper trading run."""
    global _D2_RUN_ID
    if _D2_RUN_ID:
        return _D2_RUN_ID

    with conn.cursor() as cur:
        # Check for existing D2 run
        cur.execute("""
            SELECT id FROM runs
            WHERE run_type = 'PAPER' AND status = 'RUNNING'
              AND strategy_json::jsonb->>'setup_id' = 'D2'
            ORDER BY created_at DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            _D2_RUN_ID = str(row["id"] if isinstance(row, dict) else row[0])
            return _D2_RUN_ID

        # Create new D2 run
        run_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO runs (id, run_type, status, name, strategy_json)
            VALUES (%s, 'PAPER', 'RUNNING', 'D2 Paper Trader', %s)
        """, (run_id, json.dumps({"setup_id": "D2", "strategy": "fade_casino"})))
    conn.commit()
    _D2_RUN_ID = run_id
    logger.info("Created D2 paper trading run: %s", run_id)
    return run_id


# ═══════════════════════════════════════════════════════════════════════════
# Main Service
# ═══════════════════════════════════════════════════════════════════════════

class D2PaperTrader:
    """Main service: cluster detection + HWM tracking + position management."""

    def __init__(self):
        self.conn = psycopg.connect(CFG.database_url, row_factory=dict_row)
        self.conn.autocommit = False
        self.detector = ClusterDetector(self.conn)
        self.active_states: dict[str, D2State] = {}  # symbol -> D2State
        self.perp_symbols: set[str] = set()
        self.perp_cache_ts: Optional[datetime] = None
        self._running = True

    async def start(self):
        """Main event loop."""
        logger.info("=" * 60)
        logger.info("D2 Paper Trader starting")
        logger.info("  Config: entry=%.0f%% stop=%.0f%% mania_gate=%.0f%%",
                     CFG.entry_threshold * 100, CFG.stop_pct * 100, CFG.mania_gate_pct)
        logger.info("  Candle interval: %s, poll: %ds, cluster poll: %ds",
                     CFG.candle_interval, CFG.poll_interval_s, CFG.cluster_poll_s)
        logger.info("  Max concurrent: %d, account: $%.0f, risk/trade: %.1f%%",
                     CFG.max_concurrent, CFG.account_size_usd, CFG.risk_per_trade_pct)
        logger.info("=" * 60)

        # Initial perp cache
        await self._refresh_perp_cache()

        # Run both loops concurrently
        await asyncio.gather(
            self._cluster_loop(),
            self._tracking_loop(),
        )

    async def _cluster_loop(self):
        """Poll for new clusters every cluster_poll_s."""
        while self._running:
            try:
                await self._process_clusters()
            except Exception:
                logger.exception("Cluster loop error")
            await asyncio.sleep(CFG.cluster_poll_s)

    async def _tracking_loop(self):
        """Poll candles for all active tracking/position states."""
        while self._running:
            try:
                await self._process_active_states()
            except Exception:
                logger.exception("Tracking loop error")
            await asyncio.sleep(CFG.poll_interval_s)

    async def _refresh_perp_cache(self):
        """Refresh Binance perp symbols cache (daily)."""
        now = datetime.now(timezone.utc)
        if self.perp_cache_ts and (now - self.perp_cache_ts).total_seconds() < 86400:
            return
        self.perp_symbols = await fetch_binance_exchange_info()
        self.perp_cache_ts = now

    async def _process_clusters(self):
        """Detect new clusters and run gates."""
        await self._refresh_perp_cache()
        new_clusters = self.detector.poll()
        if not new_clusters:
            return

        # Fetch FNG once per batch (cached 24h, updates daily)
        fng = await fetch_fear_greed_index()

        for cluster in new_clusters:
            symbol = cluster["symbol"]

            # Skip if already tracking this symbol
            if symbol in self.active_states:
                logger.info("SKIP %s — already active", symbol)
                continue

            # Skip if at max concurrent
            active_count = sum(
                1 for s in self.active_states.values()
                if s.phase in (D2Phase.TRACKING_HWM, D2Phase.POSITION_ACTIVE)
            )
            if active_count >= CFG.max_concurrent:
                logger.info("SKIP %s — at max concurrent (%d)", symbol, CFG.max_concurrent)
                continue

            # Run gates
            passed, reason = await run_gates(cluster, self.perp_symbols)

            # Fetch pre-pump price
            pre_pump = await fetch_pre_pump_price(
                self.conn, symbol, cluster["cluster_start_ts"]
            )

            if not passed:
                event_id = write_cluster_event(
                    self.conn, cluster, "GATED_OUT", gate_reason=reason,
                    pre_pump_price=pre_pump, fng=fng,
                )
                logger.info("GATED OUT: %s — %s (event_id=%d)", symbol, reason, event_id)
                continue

            if pre_pump is None:
                event_id = write_cluster_event(
                    self.conn, cluster, "GATED_OUT",
                    gate_reason="no_pre_pump_price", fng=fng,
                )
                logger.info("GATED OUT: %s — no pre-pump price (event_id=%d)", symbol, event_id)
                continue

            # Fetch funding rate (tag only)
            funding = await fetch_binance_funding_rate(symbol)

            # Write cluster event (with FNG tag)
            event_id = write_cluster_event(
                self.conn, cluster, "TRACKING",
                pre_pump_price=pre_pump, fng=fng,
            )

            # Log FNG regime for visibility
            fng_label = f"FNG={fng.get('value', '?')} ({fng.get('classification', '?')})" if fng else "FNG=unavailable"

            # Create state
            state = D2State(
                symbol=symbol,
                phase=D2Phase.TRACKING_HWM,
                cluster_event_id=event_id,
                cluster_start_ts=cluster["cluster_start_ts"],
                max_watch_ts=cluster["cluster_start_ts"] + timedelta(hours=CFG.max_watch_hours),
                pre_pump_price=pre_pump,
                max_pct_change=cluster.get("max_pct_change") or 0,
                vol_24h_usd=cluster.get("vol_24h_usd") or 0,
                funding_rate=funding,
                alert_count=cluster["alert_count"],
                fng_value=fng.get("value") if fng else None,
                fng_classification=fng.get("classification") if fng else None,
                tp_price=pre_pump,
            )
            state.log_transition(
                "IDLE", "TRACKING_HWM",
                f"cluster detected, {cluster['alert_count']} alerts, {fng_label}",
            )
            self.active_states[symbol] = state
            logger.info(
                "TRACKING: %s — pre_pump=%.4f, max_pct=%.1f%%, %s, event_id=%d",
                symbol, pre_pump, state.max_pct_change, fng_label, event_id,
            )

    async def _process_active_states(self):
        """Process all active states: HWM tracking and position monitoring."""
        if not self.active_states:
            return

        completed = []
        for symbol, state in list(self.active_states.items()):
            try:
                if state.phase == D2Phase.TRACKING_HWM:
                    await self._process_tracking(state)
                elif state.phase == D2Phase.POSITION_ACTIVE:
                    await self._process_position(state)

                if state.phase == D2Phase.COMPLETED:
                    completed.append(symbol)
            except Exception:
                logger.exception("Error processing %s", symbol)

        for symbol in completed:
            del self.active_states[symbol]

    async def _process_tracking(self, state: D2State):
        """HWM tracking phase: poll candles, check for entry trigger."""
        now = datetime.now(timezone.utc)

        # Check max watch timeout
        if now >= state.max_watch_ts:
            logger.info("EXPIRED: %s — max watch %dh exceeded", state.symbol, CFG.max_watch_hours)
            state.log_transition("TRACKING_HWM", "COMPLETED", "max_watch_expired")
            state.phase = D2Phase.COMPLETED
            update_cluster_status(self.conn, state.cluster_event_id, "EXPIRED")
            return

        # Fetch latest candle
        klines = await fetch_binance_klines(state.symbol, CFG.candle_interval, limit=2)
        if not klines:
            return

        state.bars_polled += 1

        for candle in klines:
            bar_high = candle["high"]
            bar_low = candle["low"]
            bar_close = candle["close"]
            state.last_price = bar_close

            # Update HWM from candle HIGH (critical: not close)
            if bar_high > state.live_hwm:
                state.live_hwm = bar_high
                state.hwm_ts = now

            # Check entry trigger: close dropped entry_threshold from HWM
            if state.live_hwm > 0:
                drop_from_hwm = (state.live_hwm - bar_close) / state.live_hwm
                if drop_from_hwm >= CFG.entry_threshold:
                    # Verify entry is above TP (not already below pre-pump)
                    if bar_close <= state.pre_pump_price:
                        logger.info(
                            "SKIP ENTRY: %s — price %.4f already below pre-pump %.4f",
                            state.symbol, bar_close, state.pre_pump_price,
                        )
                        state.log_transition("TRACKING_HWM", "COMPLETED", "price_below_pre_pump")
                        state.phase = D2Phase.COMPLETED
                        update_cluster_status(self.conn, state.cluster_event_id, "CANCELLED")
                        return

                    await self._enter_position(state, bar_close, bar_close)
                    return

    async def _enter_position(self, state: D2State, signal_price: float,
                              execution_price: float):
        """Enter a short position."""
        state.signal_price = signal_price
        state.execution_price = execution_price
        state.slippage_bps = abs(execution_price - signal_price) / signal_price * 10000
        state.entry_price = execution_price
        state.entry_ts = datetime.now(timezone.utc)
        state.entry_hwm = state.live_hwm
        state.stop_price = state.live_hwm * (1 + CFG.stop_pct)
        state.tp_price = state.pre_pump_price
        state.time_stop_ts = state.entry_ts + timedelta(hours=CFG.time_stop_hours)

        # Position sizing
        sizing = calculate_position(state.entry_price, state.stop_price)
        state.position_size = sizing["position_size_units"]
        state.notional_value = sizing["position_size_usd"]
        state.risk_amount = sizing["risk_amount"]

        state.log_transition("TRACKING_HWM", "POSITION_ACTIVE",
                             f"entry={execution_price:.4f} hwm={state.live_hwm:.4f} "
                             f"stop={state.stop_price:.4f} tp={state.tp_price:.4f}")
        state.phase = D2Phase.POSITION_ACTIVE
        update_cluster_status(self.conn, state.cluster_event_id, "ENTERED",
                              pre_pump_price=state.pre_pump_price)

        # Write OPEN position to DB (visible to dashboard)
        state.paper_position_id = write_open_paper_position(self.conn, state)

        logger.info(
            "ENTRY: %s SHORT @ %.4f | HWM=%.4f | stop=%.4f | TP=%.4f | "
            "size=$%.0f | risk=$%.0f | slippage=%.1fbps",
            state.symbol, execution_price, state.live_hwm,
            state.stop_price, state.tp_price,
            state.notional_value, state.risk_amount, state.slippage_bps,
        )

    async def _process_position(self, state: D2State):
        """Active position: monitor for exit conditions."""
        now = datetime.now(timezone.utc)

        # Fetch latest candle
        klines = await fetch_binance_klines(state.symbol, CFG.candle_interval, limit=2)
        if not klines:
            return

        state.bars_polled += 1

        for candle in klines:
            bar_high = candle["high"]
            bar_low = candle["low"]
            bar_close = candle["close"]
            state.last_price = bar_close

            # Check stop loss (bar high >= stop — price went up, short loses)
            if bar_high >= state.stop_price:
                exit_price = state.stop_price  # assume fill at stop
                state.log_transition("POSITION_ACTIVE", "COMPLETED", f"STOP_LOSS at {exit_price:.4f}")
                state.phase = D2Phase.COMPLETED
                write_paper_position(self.conn, state, "STOP_LOSS", exit_price, now)
                update_cluster_status(self.conn, state.cluster_event_id, "COMPLETED")
                return

            # Check TP (bar low <= tp — price dropped to target, short wins)
            if bar_low <= state.tp_price:
                exit_price = state.tp_price
                state.log_transition("POSITION_ACTIVE", "COMPLETED", f"TP_HIT at {exit_price:.4f}")
                state.phase = D2Phase.COMPLETED
                write_paper_position(self.conn, state, "TP_HIT", exit_price, now)
                update_cluster_status(self.conn, state.cluster_event_id, "COMPLETED")
                return

            # Check time stop
            if now >= state.time_stop_ts:
                exit_price = bar_close
                state.log_transition("POSITION_ACTIVE", "COMPLETED", f"TIME_STOP at {exit_price:.4f}")
                state.phase = D2Phase.COMPLETED
                write_paper_position(self.conn, state, "TIME_STOP", exit_price, now)
                update_cluster_status(self.conn, state.cluster_event_id, "COMPLETED")
                return

    def shutdown(self):
        """Graceful shutdown."""
        self._running = False
        if self.conn and not self.conn.closed:
            self.conn.close()
        logger.info("D2 Paper Trader shut down")


# ═══════════════════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    trader = D2PaperTrader()
    try:
        await trader.start()
    except KeyboardInterrupt:  # EXEMPT: graceful SIGINT shutdown — os._exit(78) is uncatchable
        logger.info("Interrupted")
    finally:
        trader.shutdown()


if __name__ == "__main__":
    from governance.bootstrap import bootstrap_config
    bootstrap_config()
    asyncio.run(main())
