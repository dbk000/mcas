"""
U1 - Unicorn Ignition v4.0 (WS2 Audit-Driven Refactor)
========================================================

Identifies low-volume pennycoins transitioning from dead -> discoverable -> ignition
before they appear on normal "active assets" lists.

v4.0 Changes (WS2 audit-driven — data-validated gates):
- Daily volume regime hard gate: volume_z_1d >= 4.0 (strongest WS2 predictor)
- Volatility regime hard gate: bb_width >= threshold (5m: 0.07, 15m: 0.10)
  Only validated TF+threshold pairs gated; unvalidated TFs skip gate.
- BB squeeze scoring zeroed: W_BB_COMPRESS = 0.0 (dead weight, r=-0.009)
- Funding rate boost removed entirely (was conditioned on squeeze)
- Mean reversion soft score: price_change_4h/1h decline + low bb_position_1h
  REVERSION_SCORE_WEIGHT (0.05) is an invented starting point, requires validation.
- reversion_score field added to U1Signal
- Setup version: U1_v4.0

v3.0 Changes (loosen filters + creative additions):
- All critical thresholds env-var configurable (U1_* prefixed)
- Volume band widened: 75K-3M USD (was 100K-1M)
- Gate sub-thresholds loosened (tradability 0.85, participation 1.2, range 0.10)
- Confirmations: 1-of-3 (was 2-of-3), configurable via U1_MIN_CONFIRMATIONS
- Express Lane trigger path: extreme momentum bypass (5x vol, 5% ret, 0.8 CLV)
- min_vol_z_3 feature: sustained volume floor (replaces redundant vol acceleration)
- MAX_EMBRYO_ACTIVE: 200 (was 50), T_ACTIVE_MAX_HOURS: 336h (was 168h)
- Social silence filter disabled by default
- SHIBUSDT removed from MAJOR_SYMBOLS exclusion (volume band handles it)
- trigger_path field in signal payload for attribution
- Setup version: U1_v3.0

v2.0 Changes (data-driven rebuild):
- Feature weights derived from u1_ignition_profiler.py logistic regression
- Ignition scoring uses empirical features (vol_z, bb_width compression, CLV, etc.)
- Universe temperature feature (cross-symbol volume clustering)
- Volatility-envelope stops instead of pure ATR stops
- Phase enum expanded: SCANNING, IGNITION, COOLDOWN
- All interfaces preserved for backfill_opportunities.py compatibility

Three-phase pipeline:
- Phase A: Embryo Universe (broad, cheap scan)
- Phase B: Embryo Active (capped attention budget)
- Phase C: Ignition Watch -> Unicorn Signal

Human-in-loop: Signals are identification + ranking only. No auto-execution.
"""

import logging
import os
import time as _time
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from statistics import median, stdev, mean
from enum import Enum

logger = logging.getLogger(__name__)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import OHLCV
from setup_base import Direction, ExplainPayload


# =============================================================================
# Funding Rate Cache (1h TTL)
# =============================================================================

_funding_cache: dict[str, float] = {}
_funding_cache_ts: float = 0.0
_FUNDING_CACHE_TTL: float = 3600.0  # 1 hour


def load_funding_rates(database_url: str) -> dict[str, float]:
    """Load latest funding rate per symbol from derivatives_features_1h.

    Returns {symbol: funding_rate}. Cached for 1 hour.
    If the table is empty or the query fails, returns {} (no boost applied).
    """
    global _funding_cache, _funding_cache_ts

    now = _time.time()
    if _funding_cache and (now - _funding_cache_ts) < _FUNDING_CACHE_TTL:
        return _funding_cache

    try:
        import psycopg
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (symbol) symbol, funding_rate "
                "FROM derivatives_features_1h "
                "ORDER BY symbol, bar_start_ts DESC"
            ).fetchall()
        _funding_cache = {row[0]: float(row[1]) for row in rows if row[1] is not None}
        _funding_cache_ts = now
        logger.info("Loaded funding rates for %d symbols", len(_funding_cache))
    except Exception as e:
        logger.warning("Failed to load funding rates: %s", e)
        # Keep stale cache if available, otherwise empty
        if not _funding_cache:
            _funding_cache = {}

    return _funding_cache


# =============================================================================
# Social Silence Cache (30-min TTL per symbol)
# =============================================================================
# Precision filter: coins with zero social mentions in the last 30 days
# are more likely to produce genuine ignition signals. Validated via
# walk-forward analysis — social silence predicts higher post-ignition
# follow-through because price action is not already front-run by
# social-media-driven retail flow.

_social_cache: dict[str, bool] = {}       # symbol -> has_mentions (True = reject)
_social_cache_ts: dict[str, float] = {}   # symbol -> last query epoch
_SOCIAL_CACHE_TTL: float = 1800.0         # 30 minutes

# Env-var toggle: "1" = enabled (default), "0" = disabled
U1_SOCIAL_SILENCE_FILTER = os.getenv("U1_SOCIAL_SILENCE_FILTER", "0") == "1"

# Express Lane env toggle (default enabled)
U1_EXPRESS_LANE = os.getenv("U1_EXPRESS_LANE", "1") == "1"


# =============================================================================
# Env-var helpers (runtime overrides for U1Config constants)
# =============================================================================

def _env_float(name: str, default: float) -> float:
    """Read an env var as float, falling back to default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    """Read an env var as int, falling back to default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def has_social_mentions_30d(symbol: str, database_url: str) -> bool:
    """Check if symbol has ANY rows in social_features_1h in the last 30 days.

    Returns True if mentions exist (signal should be REJECTED).
    Returns False if no mentions (signal should be ACCEPTED).
    Gracefully returns False if the table does not exist or query fails.
    Results cached per-symbol for 30 minutes.
    """
    now = _time.time()
    if symbol in _social_cache and (now - _social_cache_ts.get(symbol, 0)) < _SOCIAL_CACHE_TTL:
        return _social_cache[symbol]

    try:
        import psycopg
        with psycopg.connect(database_url) as conn:
            row = conn.execute(
                "SELECT 1 FROM social_features_1h "
                "WHERE symbol = %s AND ts >= NOW() - INTERVAL '30 days' "
                "LIMIT 1",
                (symbol,),
            ).fetchone()
        result = row is not None
    except Exception as e:
        # Table missing, connection error, etc. — accept the signal (no filter)
        logger.debug("Social silence check failed for %s: %s", symbol, e)
        result = False

    _social_cache[symbol] = result
    _social_cache_ts[symbol] = now
    return result


# =============================================================================
# Configuration
# =============================================================================

class U1Config:
    """Unicorn configuration — profiler-derived defaults where applicable."""

    # Universe (MVP: Spot USDT only)
    MIN_VOLUME_USD = _env_float("U1_MIN_VOLUME_USD", 75_000)
    MAX_VOLUME_USD = _env_float("U1_MAX_VOLUME_USD", 3_000_000)
    MAX_MIN_NOTIONAL = 100
    HYPE_REL_VOL_CAP = 20.0
    HYPE_RANGE_CAP = 0.50
    STABLECOIN_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD"}
    WRAPPED_PATTERNS = ["WBTC", "WETH", "WBNB"]
    LEVERAGE_PATTERNS = ["UP", "DOWN", "BULL", "BEAR", "3L", "3S"]

    # Major symbols excluded from pennycoin universe
    MAJOR_SYMBOLS = {
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
        "MATICUSDT", "LTCUSDT", "TRXUSDT", "ATOMUSDT",
    }

    # Gates
    GATE_WINDOW_BARS = 48
    GATE_TIMEFRAME = "5m"
    TRADABILITY_IMPROVEMENT_FACTOR = _env_float("U1_TRADABILITY_IMPROVE", 0.85)
    PARTICIPATION_GROWTH_FACTOR = _env_float("U1_PARTICIPATION_GROWTH", 1.2)
    RANGE_PCT_THRESHOLD = _env_float("U1_RANGE_PCT_THRESHOLD", 0.10)
    REL_VOL_IGNITION_MIN = 3.0

    # Baseline volume fallback windows (bars on 5m)
    BASELINE_WINDOWS = [
        ("7d", 2016),
        ("3d", 864),
        ("1d", 288),
        ("288_bars", 288),
        ("48_bars", 48),
    ]

    # Compression box
    COMPRESSION_BOX_LOOKBACK = 48

    # Ignition — profiler-derived defaults (overridden by profiler output)
    REL_VOL_IGNITION = _env_float("U1_REL_VOL_IGNITION", 3.0)
    BREAKOUT_ACCEPTANCE_BARS = 2
    VOL_Z_MIN = _env_float("U1_VOL_Z_MIN", 1.5)
    IGNITION_SCORE_THRESHOLD = _env_float("U1_IGNITION_SCORE_THRESHOLD", 0.40)

    # Ignition scoring weights — profiler-derived defaults
    # These are normalized logistic regression coefficients from the profiler.
    # Updated after running u1_ignition_profiler.py on real data.
    W_VOL_Z = 0.25
    W_VOL_Z_MAX_6 = 0.15
    W_VOL_SLOPE = 0.10
    W_BB_COMPRESS = 0.0   # v4.0: zeroed (WS2 audit: dead weight, r=-0.009). Other weights unchanged.
    W_RANGE_PCT = 0.10
    W_RETURN_12 = 0.10
    W_CLV = 0.05
    W_BODY_RATIO = 0.04
    W_VOL_Z_FLOOR = 0.06  # min_vol_z_3: sustained volume floor

    # Universe temperature weight (0 = disabled)
    W_UNIVERSE_TEMP = 0.15

    # Confirmations
    SPREAD_WIDENING_THRESHOLD = 1.2

    # Time stop
    TIME_STOP_BARS_5M = 12
    TIME_STOP_BARS_15M = 8
    TIME_STOP_BARS_1H = 4

    # Cap
    MAX_EMBRYO_ACTIVE = _env_int("U1_MAX_EMBRYO_ACTIVE", 200)
    T_ACTIVE_MAX_HOURS = _env_int("U1_T_ACTIVE_MAX_HOURS", 336)  # 14 days

    # Legacy scoring weights (gate scoring — kept for backwards compat)
    W_TRADABILITY = 0.25
    W_PARTICIPATION = 0.30
    W_COMPRESSION = 0.30
    W_TRAP_RISK = 0.15

    # Risk — volatility envelope stop (replaces pure ATR stops)
    STOP_ATR_BUFFER = 1.0
    STOP_RANGE_MULT = 3.0          # Stop = entry - N × mean(high-low, 20 bars)
    MIN_STOP_PCT = 0.005           # Floor: 0.5% minimum stop distance (clears fee burden)
    TP1_R = 1.0
    TP2_R = 2.0

    # v4.0: Daily volume regime gate (HARD)
    VOLUME_Z_1D_MIN = _env_float("U1_VOLUME_Z_1D_MIN", 4.0)

    # v4.0: Volatility regime gate (replaces BB squeeze)
    # Only validated TF+threshold pairs — no generic fallback
    BB_WIDTH_MIN_5M = _env_float("U1_BB_WIDTH_MIN_5M", 0.07)
    BB_WIDTH_MIN_15M = _env_float("U1_BB_WIDTH_MIN_15M", 0.10)

    # v4.0: Mean reversion soft score thresholds
    REVERSION_PC_4H = _env_float("U1_REVERSION_PC_4H", -0.0065)
    REVERSION_PC_1H = _env_float("U1_REVERSION_PC_1H", -0.0048)
    REVERSION_BB_POS = _env_float("U1_REVERSION_BB_POS", 0.258)
    REVERSION_SCORE_WEIGHT = _env_float("U1_REVERSION_WEIGHT", 0.05)  # NOT from WS2 — invented starting point, requires validation


# =============================================================================
# Universe Helpers
# =============================================================================

def base_asset(symbol: str) -> str:
    """Extract base asset from trading pair symbol."""
    if symbol.endswith("USDT"):
        return symbol[:-4]
    for quote in ["BUSD", "USDC", "BTC", "ETH"]:
        if symbol.endswith(quote):
            return symbol[:-len(quote)]
    return symbol


def is_stablecoin_base(symbol: str) -> bool:
    """Check if BASE asset is a stablecoin (not quote)."""
    base = base_asset(symbol)
    return base in U1Config.STABLECOIN_BASES


def is_wrapped(symbol: str) -> bool:
    """Check if BASE asset is a wrapped token."""
    base = base_asset(symbol)
    return any(base == pattern for pattern in U1Config.WRAPPED_PATTERNS)


def is_leverage_token(symbol: str) -> bool:
    """Check if BASE asset is a leverage token."""
    base = base_asset(symbol)
    return any(base.endswith(pattern) for pattern in U1Config.LEVERAGE_PATTERNS)


# =============================================================================
# State Classes
# =============================================================================

class U1Phase(Enum):
    """Unicorn pipeline phase."""
    EMBRYO = "EMBRYO"
    EMBRYO_ACTIVE = "EMBRYO_ACTIVE"
    SCANNING = "SCANNING"            # New: actively scanning for ignition features
    IGNITION_WATCH = "IGNITION_WATCH"
    IGNITION = "IGNITION"            # New: ignition score above threshold
    COOLDOWN = "COOLDOWN"            # New: post-ignition cooldown (no re-signal)
    REJECTED = "REJECTED"


@dataclass
class U1State:
    """Tier 3 state for Unicorn Ignition."""

    # Phase
    phase: U1Phase = U1Phase.EMBRYO

    # Promotion tracking
    promoted_at: Optional[datetime] = None
    last_eval_ts: Optional[datetime] = None

    # Gate results
    gate1_passed: bool = False
    gate2_passed: bool = False
    gate3_passed: bool = False
    gate4_passed: Optional[bool] = None  # None if skipped

    # Compression box
    compression_box_high: float = 0.0
    compression_box_low: float = 0.0
    compression_box_lookback: int = 48

    # Baseline volume
    rel_volume_baseline: float = 0.0
    baseline_window_used: str = ""

    # Scores (legacy gate scores)
    tradability_score: float = 0.0
    participation_score: float = 0.0
    compression_score: float = 0.0
    trap_risk_score: float = 0.0
    embryo_active_score: float = 0.0

    # Data-driven ignition score (v2.0)
    ignition_score: float = 0.0
    per_coin_score: float = 0.0
    universe_temp_score: float = 0.0
    vol_z_at_trigger: float = 0.0

    # Funding rate conditioning (v2.1)
    funding_rate: float = 0.0
    funding_rate_updated: bool = False

    # Metrics snapshot
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dict for Redis storage."""
        return {
            "phase": self.phase.value,
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
            "last_eval_ts": self.last_eval_ts.isoformat() if self.last_eval_ts else None,
            "gate1_passed": self.gate1_passed,
            "gate2_passed": self.gate2_passed,
            "gate3_passed": self.gate3_passed,
            "gate4_passed": self.gate4_passed,
            "compression_box_high": self.compression_box_high,
            "compression_box_low": self.compression_box_low,
            "compression_box_lookback": self.compression_box_lookback,
            "rel_volume_baseline": self.rel_volume_baseline,
            "baseline_window_used": self.baseline_window_used,
            "tradability_score": self.tradability_score,
            "participation_score": self.participation_score,
            "compression_score": self.compression_score,
            "trap_risk_score": self.trap_risk_score,
            "embryo_active_score": self.embryo_active_score,
            "ignition_score": self.ignition_score,
            "per_coin_score": self.per_coin_score,
            "universe_temp_score": self.universe_temp_score,
            "vol_z_at_trigger": self.vol_z_at_trigger,
            "funding_rate": self.funding_rate,
            "funding_rate_updated": self.funding_rate_updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "U1State":
        """Create from dict."""
        return cls(
            phase=U1Phase(d.get("phase", "EMBRYO")),
            promoted_at=datetime.fromisoformat(d["promoted_at"]) if d.get("promoted_at") else None,
            last_eval_ts=datetime.fromisoformat(d["last_eval_ts"]) if d.get("last_eval_ts") else None,
            gate1_passed=bool(d.get("gate1_passed", False)),
            gate2_passed=bool(d.get("gate2_passed", False)),
            gate3_passed=bool(d.get("gate3_passed", False)),
            gate4_passed=d.get("gate4_passed"),
            compression_box_high=float(d.get("compression_box_high", 0)),
            compression_box_low=float(d.get("compression_box_low", 0)),
            compression_box_lookback=int(d.get("compression_box_lookback", 48)),
            rel_volume_baseline=float(d.get("rel_volume_baseline", 0)),
            baseline_window_used=str(d.get("baseline_window_used", "")),
            tradability_score=float(d.get("tradability_score", 0)),
            participation_score=float(d.get("participation_score", 0)),
            compression_score=float(d.get("compression_score", 0)),
            trap_risk_score=float(d.get("trap_risk_score", 0)),
            embryo_active_score=float(d.get("embryo_active_score", 0)),
            ignition_score=float(d.get("ignition_score", 0)),
            per_coin_score=float(d.get("per_coin_score", 0)),
            universe_temp_score=float(d.get("universe_temp_score", 0)),
            vol_z_at_trigger=float(d.get("vol_z_at_trigger", 0)),
            funding_rate=float(d.get("funding_rate", 0)),
            funding_rate_updated=bool(d.get("funding_rate_updated", False)),
        )


@dataclass
class U1Signal:
    """Unicorn Ignition signal - complete payload."""

    # Identity
    symbol: str
    timeframe: str
    direction: Direction = Direction.LONG
    setup_id: str = "U1"

    # Scoring
    setup_score: float = 0.0  # = ignition_score (0-1)

    # Trade levels (suggested for human execution)
    entry_price: float = 0.0
    stop_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    r_value: float = 0.0

    # Timing (REQUIRED - Canon compliance)
    bar_start_ts: Optional[datetime] = None
    signal_ts: Optional[datetime] = None
    age_seconds: int = 0
    action_by_ts: Optional[datetime] = None

    # Time stop policy
    time_stop_bars: int = 12
    time_stop_policy: str = "PROGRESS_0.5R_OR_EXIT"

    # Ignition metrics
    rel_volume: float = 0.0
    breakout_acceptance_bars: int = 2
    compression_box_high: float = 0.0
    compression_box_low: float = 0.0

    # Confirmation flags
    liquidity_volume_rising: bool = False
    participation_confirmed: bool = False
    trend_rate_accelerating: bool = False
    confirmations_met: int = 0

    # Gate metrics snapshot
    tradability_score: float = 0.0
    participation_score: float = 0.0
    compression_score: float = 0.0
    trap_risk_score: float = 0.0
    embryo_active_score: float = 0.0

    # Trigger path attribution (v3.0)
    trigger_path: str = "traditional"  # "traditional", "score_based", or "express_lane"

    # v4.0: Mean reversion score (0-3)
    reversion_score: int = 0

    # Explainability
    explain: Optional[ExplainPayload] = None

    # State snapshot
    state: Optional[U1State] = None


# =============================================================================
# Gate Logic (OHLCV Proxy Mode)
# =============================================================================

def _candle_range(c: OHLCV) -> float:
    """Compute candle range (c.range may not exist)."""
    return c.high - c.low


def _safe_median(values: list) -> float:
    """Safe median that handles empty/single values."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return median(values)


def _safe_mean(values: list) -> float:
    """Safe mean that handles empty lists."""
    if not values:
        return 0.0
    return mean(values)


def _safe_std(values: list) -> float:
    """Safe stdev that handles small lists."""
    if len(values) < 2:
        return 0.0
    return stdev(values)


def compute_baseline_volume(candles: list[OHLCV], timeframe: str = "5m") -> tuple[float, str]:
    """
    Compute baseline volume with deterministic fallback windows.

    Returns (baseline_volume, window_name_used).
    """
    available_bars = len(candles)

    for window_name, bars_needed in U1Config.BASELINE_WINDOWS:
        if available_bars >= bars_needed:
            window_candles = candles[-bars_needed:]
            volumes = [getattr(c, 'quote_volume', c.volume) if hasattr(c, 'quote_volume')
                      and getattr(c, 'quote_volume', 0) > 0 else c.volume
                      for c in window_candles]
            baseline = _safe_median(volumes)
            return baseline, window_name

    # Absolute minimum
    if available_bars > 0:
        volumes = [c.volume for c in candles]
        return _safe_median(volumes), "all_available"

    return 0.0, "none"


def compute_compression_box(candles: list[OHLCV], lookback: int = 48) -> tuple[float, float]:
    """
    Compute compression box (range bounds) for breakout detection.
    Excludes trigger bar (last bar) to prevent circular definition.
    """
    if len(candles) < 2:
        return 0.0, 0.0

    # Exclude trigger bar
    box_candles = candles[-(lookback + 1):-1] if len(candles) > lookback else candles[:-1]

    if not box_candles:
        return candles[-1].high, candles[-1].low

    box_high = max(c.high for c in box_candles)
    box_low = min(c.low for c in box_candles)

    return box_high, box_low


def compute_atr_pct(candles: list[OHLCV], period: int = 14) -> float:
    """Compute ATR as percentage of close."""
    if len(candles) < period + 1:
        return 0.0

    tr_values = []
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

    if len(tr_values) < period:
        return 0.0

    atr = _safe_mean(tr_values[-period:])
    close = candles[-1].close

    return atr / close if close > 0 else 0.0


def compute_slope(values: list) -> float:
    """Compute simple linear slope of values."""
    if len(values) < 2:
        return 0.0

    n = len(values)
    x = np.arange(n)
    y = np.array(values)

    x_mean = x.mean()
    y_mean = y.mean()

    numerator = np.sum((x - x_mean) * (y - y_mean))
    denominator = np.sum((x - x_mean) ** 2)

    if denominator == 0:
        return 0.0

    return float(numerator / denominator)


# =============================================================================
# v4.0 Helpers
# =============================================================================

def compute_volume_z_1d_from_daily(daily_volumes: list[float], lookback: int = 20) -> float:
    """Z-score of last completed day's volume vs prior lookback days.

    daily_volumes: list of daily volume values, oldest first.
    Uses last completed day (index -1) vs prior `lookback` days.
    Returns 0.0 if insufficient data.
    """
    if len(daily_volumes) < lookback + 1:
        return 0.0

    last_day_vol = daily_volumes[-1]
    prior = daily_volumes[-(lookback + 1):-1]
    m = np.mean(prior)
    s = np.std(prior)
    if s <= 0:
        return 0.0
    return float((last_day_vol - m) / s)


def aggregate_candles_to_1h(candles: list[OHLCV], native_tf: str) -> list[OHLCV]:
    """Aggregate sub-hourly candles into synthetic 1h candles.

    Groups by UTC hour boundary. Returns synthetic OHLCV with proper
    OHLC (first open, max high, min low, last close, sum volume).
    """
    if native_tf == "1h":
        return candles

    # Bars per hour for grouping
    bph = {"5m": 12, "15m": 4, "1m": 60}.get(native_tf)
    if bph is None or len(candles) < bph:
        return []

    # Group by UTC hour boundary (truncate timestamp to hour)
    hourly: dict[int, list[OHLCV]] = {}
    for c in candles:
        # Truncate ms timestamp to hour boundary
        hour_ts = (c.timestamp // 3_600_000) * 3_600_000
        if hour_ts not in hourly:
            hourly[hour_ts] = []
        hourly[hour_ts].append(c)

    result = []
    for hour_ts in sorted(hourly.keys()):
        group = hourly[hour_ts]
        if len(group) < bph:
            continue  # incomplete hour
        result.append(OHLCV(
            timestamp=hour_ts,
            open=group[0].open,
            high=max(c.high for c in group),
            low=min(c.low for c in group),
            close=group[-1].close,
            volume=sum(c.volume for c in group),
        ))
    return result


def compute_bb_position_1h(candles: list[OHLCV], native_tf: str) -> float:
    """Compute 1h BB position by aggregating to synthetic 1h candles.

    Returns (close - lower) / (upper - lower), or 0.5 if insufficient data.
    """
    hourly = aggregate_candles_to_1h(candles, native_tf)
    if len(hourly) < 20:
        return 0.5

    closes = np.array([c.close for c in hourly[-20:]])
    sma = np.mean(closes)
    std = np.std(closes)
    if std <= 0:
        return 0.5

    upper = sma + 2 * std
    lower = sma - 2 * std
    bb_range = upper - lower
    if bb_range <= 0:
        return 0.5

    return float((hourly[-1].close - lower) / bb_range)


# =============================================================================
# Data-Driven Feature Extraction (v2.0)
# =============================================================================

def compute_ignition_features(candles: list[OHLCV], baseline_vol: float, timeframe: str = "5m") -> dict:
    """
    Extract the profiler-derived features from OHLCV candles.
    Returns dict of feature values for ignition scoring.

    v4.0: Adds price_change_1h, price_change_4h, bb_position, bb_position_1h.
    """
    if len(candles) < 25:
        return {}

    volumes = np.array([c.volume for c in candles])
    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    opens = np.array([c.open for c in candles])
    n = len(candles)

    # Volume z-score (20-bar)
    if n >= 20:
        vol_mean = np.mean(volumes[-20:])
        vol_std = np.std(volumes[-20:])
        vol_z = (volumes[-1] - vol_mean) / vol_std if vol_std > 0 else 0
    else:
        vol_z = 0.0

    # Vol z-score max over last 6 bars
    vol_z_vals = []
    for i in range(max(0, n - 6), n):
        if n >= 20 and i >= 20:
            vm = np.mean(volumes[i - 20:i])
            vs = np.std(volumes[i - 20:i])
            vol_z_vals.append((volumes[i] - vm) / vs if vs > 0 else 0)
    vol_z_max_6 = max(vol_z_vals) if vol_z_vals else vol_z

    # Volume slope (5-bar linear regression)
    if n >= 5:
        recent_vol = volumes[-5:]
        x = np.arange(5, dtype=float)
        x_mean = x.mean()
        y_mean = recent_vol.mean()
        x_var = np.sum((x - x_mean) ** 2)
        vol_slope = np.sum((x - x_mean) * (recent_vol - y_mean)) / x_var if x_var > 0 else 0
    else:
        vol_slope = 0.0

    # Bollinger Band width and compression percentile
    if n >= 20:
        sma_20 = np.mean(closes[-20:])
        std_20 = np.std(closes[-20:])
        bb_width = (4 * std_20) / sma_20 if sma_20 > 0 else 0
    else:
        bb_width = 0.0

    # BB width as percentile vs available history
    bb_widths = []
    for i in range(20, n):
        sm = np.mean(closes[i - 20:i])
        st = np.std(closes[i - 20:i])
        bb_widths.append((4 * st) / sm if sm > 0 else 0)
    bb_pctile = 50.0
    if bb_widths and len(bb_widths) > 1:
        bb_pctile = sum(1 for w in bb_widths if w < bb_width) / len(bb_widths) * 100

    # Range percent
    bar_range = highs[-1] - lows[-1]
    range_pct = bar_range / closes[-1] if closes[-1] > 0 else 0

    # Body ratio
    body = abs(closes[-1] - opens[-1])
    body_ratio = body / bar_range if bar_range > 0 else 0

    # CLV (Close Location Value)
    clv = (closes[-1] - lows[-1]) / bar_range if bar_range > 0 else 0.5

    # 12-bar return
    return_12 = 0.0
    if n > 12 and closes[-13] > 0:
        return_12 = (closes[-1] - closes[-13]) / closes[-13]

    # Relative volume
    rel_vol = volumes[-1] / baseline_vol if baseline_vol > 0 else 0

    # Volume floor: minimum vol_z over last 3 bars (sustained volume indicator)
    if n >= 23 and vol_std > 0:  # need 20-bar window + 3 bars
        vol_z_1 = (volumes[-1] - vol_mean) / vol_std
        vol_z_2 = (volumes[-2] - vol_mean) / vol_std
        vol_z_3 = (volumes[-3] - vol_mean) / vol_std
        min_vol_z_3 = min(vol_z_1, vol_z_2, vol_z_3)
    else:
        min_vol_z_3 = 0.0

    # v4.0: Price change lookback (in bars) based on timeframe
    bars_per_1h = {"5m": 12, "15m": 4, "1h": 1, "4h": 1}.get(timeframe, 12)
    bars_per_4h = {"5m": 48, "15m": 16, "1h": 4, "4h": 1}.get(timeframe, 48)

    price_change_1h = 0.0
    if n > bars_per_1h and closes[-(bars_per_1h + 1)] > 0:
        price_change_1h = (closes[-1] - closes[-(bars_per_1h + 1)]) / closes[-(bars_per_1h + 1)]

    price_change_4h = 0.0
    if n > bars_per_4h and closes[-(bars_per_4h + 1)] > 0:
        price_change_4h = (closes[-1] - closes[-(bars_per_4h + 1)]) / closes[-(bars_per_4h + 1)]

    # v4.0: BB position on native TF
    bb_position = 0.5
    if n >= 20 and std_20 > 0:
        upper = sma_20 + 2 * std_20
        lower = sma_20 - 2 * std_20
        bb_range = upper - lower
        if bb_range > 0:
            bb_position = (closes[-1] - lower) / bb_range

    # v4.0: BB position on 1h (aggregated from sub-hourly candles)
    bb_position_1h = compute_bb_position_1h(candles, timeframe)

    return {
        "vol_z": float(vol_z),
        "vol_z_max_6": float(vol_z_max_6),
        "vol_slope_5": float(vol_slope),
        "bb_width": float(bb_width),
        "bb_width_pctile": float(bb_pctile),
        "range_pct": float(range_pct),
        "body_ratio": float(body_ratio),
        "clv": float(clv),
        "return_12": float(return_12),
        "rel_volume": float(rel_vol),
        "min_vol_z_3": float(min_vol_z_3),
        "price_change_1h": float(price_change_1h),
        "price_change_4h": float(price_change_4h),
        "bb_position": float(bb_position),
        "bb_position_1h": float(bb_position_1h),
    }


def compute_ignition_score(features: dict, universe_temp: dict = None) -> tuple[float, float, float]:
    """
    Compute ignition score using profiler-derived weights.

    Returns (total_score, per_coin_score, temp_score).
    """
    # Normalize features to [0, 1] range for scoring
    s_vol_z = min(1.0, max(0, features.get("vol_z", 0)) / 5.0)
    s_vol_z_max = min(1.0, max(0, features.get("vol_z_max_6", 0)) / 5.0)
    s_vol_slope = min(1.0, max(0, features.get("vol_slope_5", 0) / 1e6))  # normalize by scale
    s_bb_compress = 1.0 - features.get("bb_width_pctile", 50.0) / 100.0
    s_range = min(1.0, features.get("range_pct", 0) / 0.05)
    s_return = min(1.0, max(0, features.get("return_12", 0) * 5))
    s_clv = features.get("clv", 0.5)
    s_body = features.get("body_ratio", 0.5)
    s_vol_z_floor = min(1.0, max(0, features.get("min_vol_z_3", 0)) / 3.0)

    per_coin = (
        U1Config.W_VOL_Z * s_vol_z +
        U1Config.W_VOL_Z_MAX_6 * s_vol_z_max +
        U1Config.W_VOL_SLOPE * s_vol_slope +
        U1Config.W_BB_COMPRESS * s_bb_compress +
        U1Config.W_RANGE_PCT * s_range +
        U1Config.W_RETURN_12 * s_return +
        U1Config.W_CLV * s_clv +
        U1Config.W_BODY_RATIO * s_body +
        U1Config.W_VOL_Z_FLOOR * s_vol_z_floor
    )

    # Universe temperature component
    temp_score = 0.0
    if universe_temp:
        n_gt2 = universe_temp.get("n_gt2", 0)
        mean_vz = universe_temp.get("mean_vol_z", 0)
        temp_score = min(1.0, n_gt2 / 20.0) * 0.5 + min(1.0, max(0, mean_vz) / 2.0) * 0.5

    total = (1 - U1Config.W_UNIVERSE_TEMP) * per_coin + U1Config.W_UNIVERSE_TEMP * temp_score

    return float(total), float(per_coin), float(temp_score)


# =============================================================================
# Gate Evaluators
# =============================================================================

def evaluate_gate1_tradability_proxy(
    candles_last_n: list[OHLCV],
    candles_prev_n: list[OHLCV],
) -> tuple[bool, dict]:
    """OHLCV proxy for tradability improvement. Pass 2 of 3."""

    # Metric 1: Spread proxy compression (range / close)
    spread_values_last = [_candle_range(c) / c.close for c in candles_last_n if c.close > 0]
    spread_values_prev = [_candle_range(c) / c.close for c in candles_prev_n if c.close > 0]
    spread_proxy_last = _safe_median(spread_values_last)
    spread_proxy_prev = _safe_median(spread_values_prev)
    spread_improving = spread_proxy_last <= U1Config.TRADABILITY_IMPROVEMENT_FACTOR * spread_proxy_prev if spread_proxy_prev > 0 else False

    # Metric 2: Volume coefficient of variation
    mean_vol_last = _safe_mean([c.volume for c in candles_last_n])
    mean_vol_prev = _safe_mean([c.volume for c in candles_prev_n])
    vol_cv_last = _safe_std([c.volume for c in candles_last_n]) / mean_vol_last if mean_vol_last > 0 else float('inf')
    vol_cv_prev = _safe_std([c.volume for c in candles_prev_n]) / mean_vol_prev if mean_vol_prev > 0 else float('inf')
    volume_stabilizing = vol_cv_last <= 0.9 * vol_cv_prev if vol_cv_prev < float('inf') else False

    # Metric 3: ATR% compression
    atr_pct_last = compute_atr_pct(candles_last_n)
    atr_pct_prev = compute_atr_pct(candles_prev_n)
    atr_compressing = atr_pct_last <= U1Config.TRADABILITY_IMPROVEMENT_FACTOR * atr_pct_prev if atr_pct_prev > 0 else False

    checks = [spread_improving, volume_stabilizing, atr_compressing]
    passed = sum(checks) >= 2

    return passed, {
        "spread_proxy_last": spread_proxy_last,
        "spread_proxy_prev": spread_proxy_prev,
        "spread_improving": spread_improving,
        "vol_cv_last": vol_cv_last,
        "vol_cv_prev": vol_cv_prev,
        "volume_stabilizing": volume_stabilizing,
        "atr_pct_last": atr_pct_last,
        "atr_pct_prev": atr_pct_prev,
        "atr_compressing": atr_compressing,
    }


def evaluate_gate2_participation_proxy(
    candles_last_n: list[OHLCV],
    candles_prev_n: list[OHLCV],
) -> tuple[bool, dict]:
    """OHLCV proxy for participation broadening. Pass 2 of 3."""

    # Metric 1: Volume sum rising
    vol_sum_last = sum(c.volume for c in candles_last_n)
    vol_sum_prev = sum(c.volume for c in candles_prev_n)
    volume_rising = vol_sum_last >= U1Config.PARTICIPATION_GROWTH_FACTOR * vol_sum_prev if vol_sum_prev > 0 else False

    # Metric 2: Active bars proxy
    active_bars_last = sum(1 for c in candles_last_n if c.volume > 0)
    active_bars_prev = sum(1 for c in candles_prev_n if c.volume > 0)
    activity_rising = active_bars_last >= 1.1 * active_bars_prev if active_bars_prev > 0 else False

    # Metric 3: No whale dominance
    volumes_last = [c.volume for c in candles_last_n if c.volume > 0]
    avg_vol_last = _safe_mean(volumes_last) if volumes_last else 0
    max_vol_last = max(volumes_last) if volumes_last else 0
    whale_ratio = max_vol_last / avg_vol_last if avg_vol_last > 0 else float('inf')
    no_whale_dominance = whale_ratio < 10.0

    passed = sum([volume_rising, activity_rising, no_whale_dominance]) >= 2

    return passed, {
        "vol_sum_last": vol_sum_last,
        "vol_sum_prev": vol_sum_prev,
        "volume_rising": volume_rising,
        "active_bars_last": active_bars_last,
        "active_bars_prev": active_bars_prev,
        "activity_rising": activity_rising,
        "whale_ratio": whale_ratio,
        "no_whale_dominance": no_whale_dominance,
    }


def evaluate_gate3_compression(
    candles_last_n: list[OHLCV],
    candles_prev_n: list[OHLCV],
    rel_volume_last: float,
) -> tuple[bool, dict]:
    """Compression detection for pre-ignition state. Pass 2 of 3."""

    if not candles_last_n:
        return False, {}

    # Metric 1: Range tight
    high_last = max(c.high for c in candles_last_n)
    low_last = min(c.low for c in candles_last_n)
    close_last = candles_last_n[-1].close
    range_pct_last = (high_last - low_last) / close_last if close_last > 0 else 0
    range_tight = range_pct_last <= U1Config.RANGE_PCT_THRESHOLD

    # Metric 2: ATR contracting
    atr_pct_last = compute_atr_pct(candles_last_n)
    atr_pct_prev = compute_atr_pct(candles_prev_n)
    atr_contracting = atr_pct_last <= 0.7 * atr_pct_prev if atr_pct_prev > 0 else False

    # Metric 3: Volume not igniting yet
    vol_slope = compute_slope([c.volume for c in candles_last_n])
    not_igniting = vol_slope >= 0 and rel_volume_last < U1Config.REL_VOL_IGNITION_MIN

    passed = sum([range_tight, atr_contracting, not_igniting]) >= 2

    return passed, {
        "range_pct_last": range_pct_last,
        "range_tight": range_tight,
        "atr_pct_last": atr_pct_last,
        "atr_pct_prev": atr_pct_prev,
        "atr_contracting": atr_contracting,
        "vol_slope": vol_slope,
        "rel_volume_last": rel_volume_last,
        "not_igniting": not_igniting,
    }


# =============================================================================
# Scoring
# =============================================================================

def compute_tradability_score(metrics: dict) -> float:
    """TradabilityScore based on proxy metrics (0-100)."""
    spread = metrics.get("spread_proxy_last", 0.05)
    vol_cv = metrics.get("vol_cv_last", 2.0)
    atr_pct = metrics.get("atr_pct_last", 0.10)

    spread_factor = 1.0 - min(spread / 0.05, 1.0)
    vol_cv_factor = 1.0 - min(vol_cv / 2.0, 1.0)
    atr_factor = 1.0 - min(atr_pct / 0.10, 1.0)

    return (0.4 * spread_factor + 0.3 * vol_cv_factor + 0.3 * atr_factor) * 100


def compute_participation_score(metrics: dict) -> float:
    """ParticipationScore based on activity metrics (0-100)."""
    vol_last = metrics.get("vol_sum_last", 0)
    vol_prev = metrics.get("vol_sum_prev", 1)
    active_last = metrics.get("active_bars_last", 0)
    active_prev = max(metrics.get("active_bars_prev", 1), 1)
    whale_ratio = metrics.get("whale_ratio", 1)

    volume_growth = min(vol_last / vol_prev if vol_prev > 0 else 0, 3.0) / 3.0
    activity_growth = min(active_last / active_prev, 2.0) / 2.0
    whale_penalty = 1.0 - min(whale_ratio / 10.0, 1.0)

    return (0.4 * volume_growth + 0.3 * activity_growth + 0.3 * whale_penalty) * 100


def compute_compression_score(metrics: dict) -> float:
    """CompressionScore based on range/volatility contraction (0-100)."""
    range_pct = metrics.get("range_pct_last", 0.10)
    atr_last = metrics.get("atr_pct_last", 0.10)
    atr_prev = metrics.get("atr_pct_prev", 0.10)

    range_tightness = 1.0 - min(range_pct / 0.10, 1.0)
    atr_contraction = 1.0 - min(atr_last / atr_prev if atr_prev > 0 else 1.0, 1.0)

    return (0.6 * range_tightness + 0.4 * atr_contraction) * 100


def compute_trap_risk_score(metrics: dict) -> float:
    """TrapRiskScore - higher = MORE risk (0-100)."""
    whale_ratio = metrics.get("whale_ratio", 1)
    spread_last = metrics.get("spread_proxy_last", 0)
    spread_prev = metrics.get("spread_proxy_prev", 1)

    whale_risk = min(whale_ratio / 20.0, 1.0)
    spread_widening = 1.0 if spread_last > 1.5 * spread_prev else 0.0

    return (0.5 * whale_risk + 0.5 * spread_widening) * 100


def compute_embryo_active_score(
    tradability: float,
    participation: float,
    compression: float,
    trap_risk: float,
) -> float:
    """EmbryoActiveScore for ranking and cap enforcement (0-100)."""
    score = (
        U1Config.W_TRADABILITY * tradability +
        U1Config.W_PARTICIPATION * participation +
        U1Config.W_COMPRESSION * compression -
        U1Config.W_TRAP_RISK * trap_risk
    )
    return max(0, min(100, score))


def compute_unicorn_confidence(
    embryo_score: float,
    ignition_score: float,
    confirmations_met: int,
    tradability: float,
    trap_risk: float,
) -> float:
    """UnicornConfidence for signal ranking (0-1 scale). v2.0: incorporates ignition_score."""
    # Blend legacy embryo score with data-driven ignition score
    base = 0.4 * (embryo_score / 100) + 0.6 * ignition_score
    confirmation_bonus = 0.1 * (confirmations_met - 2) if confirmations_met > 2 else 0
    tradability_bonus = 0.05 * (tradability / 100)
    risk_penalty = 0.15 * (trap_risk / 100)

    confidence = base + confirmation_bonus + tradability_bonus - risk_penalty
    return max(0, min(1, confidence))


# =============================================================================
# Ignition Triggers
# =============================================================================

def check_ignition_triggers(
    state: U1State,
    candles: list[OHLCV],
) -> tuple[bool, dict]:
    """Check ignition trigger conditions for EMBRYO_ACTIVE symbol."""
    if len(candles) < U1Config.BREAKOUT_ACCEPTANCE_BARS + 1:
        return False, {"error": "insufficient_candles"}

    current = candles[-1]

    # Trigger 1: Relative volume ignition
    rel_volume = current.volume / state.rel_volume_baseline if state.rel_volume_baseline > 0 else 0
    volume_igniting = rel_volume >= U1Config.REL_VOL_IGNITION

    # Trigger 2: Breakout (close above compression box high)
    breakout = current.close > state.compression_box_high

    # Trigger 3: Acceptance (m consecutive closes above box_high, prior was inside)
    m = U1Config.BREAKOUT_ACCEPTANCE_BARS
    if len(candles) >= m + 1:
        last_m_above = all(c.close > state.compression_box_high for c in candles[-m:])
        prior_inside = candles[-m - 1].close <= state.compression_box_high
        accepted = last_m_above and prior_inside
    else:
        accepted = False

    all_triggers = volume_igniting and breakout and accepted

    return all_triggers, {
        "rel_volume": rel_volume,
        "volume_igniting": volume_igniting,
        "breakout": breakout,
        "accepted": accepted,
        "acceptance_bars_checked": m,
    }


def check_ignition_confirmations(
    candles_last_n: list[OHLCV],
    candles_prev_n: list[OHLCV],
) -> tuple[bool, dict]:
    """Check confirmation conditions. Must meet at least 2 of 3."""

    # Confirmation A: Liquidity + volume rising together
    vol_rising = sum(c.volume for c in candles_last_n) > sum(c.volume for c in candles_prev_n)
    spread_values_last = [_candle_range(c) / c.close for c in candles_last_n if c.close > 0]
    spread_values_prev = [_candle_range(c) / c.close for c in candles_prev_n if c.close > 0]
    spread_proxy_last = _safe_median(spread_values_last)
    spread_proxy_prev = _safe_median(spread_values_prev)
    spread_not_widening = spread_proxy_last <= 1.2 * spread_proxy_prev if spread_proxy_prev > 0 else True
    liquidity_volume_ok = vol_rising and spread_not_widening

    # Confirmation B: Participation continues
    active_last = sum(1 for c in candles_last_n if c.volume > 0)
    active_prev = sum(1 for c in candles_prev_n if c.volume > 0)
    participation_continues = active_last >= active_prev

    # Confirmation C: TrendRate acceleration
    closes_last = [c.close for c in candles_last_n]
    slope_last = compute_slope(closes_last)
    half_len = len(closes_last) // 2
    slope_prev_half = compute_slope(closes_last[:half_len]) if half_len > 1 else 0
    slope_curr_half = compute_slope(closes_last[half_len:]) if half_len > 1 else 0
    trend_accelerating = slope_last > 0 and slope_curr_half > slope_prev_half

    confirmations = [liquidity_volume_ok, participation_continues, trend_accelerating]
    met_count = sum(confirmations)
    passed = met_count >= 2

    return passed, {
        "liquidity_volume_ok": liquidity_volume_ok,
        "vol_rising": vol_rising,
        "spread_not_widening": spread_not_widening,
        "participation_continues": participation_continues,
        "trend_accelerating": trend_accelerating,
        "confirmations_met": met_count,
    }


# =============================================================================
# Time Stop
# =============================================================================

def get_time_stop_bars(timeframe: str) -> int:
    """Get N_dead (time stop bars) for timeframe."""
    return {
        "5m": U1Config.TIME_STOP_BARS_5M,
        "15m": U1Config.TIME_STOP_BARS_15M,
        "1h": U1Config.TIME_STOP_BARS_1H,
        "4h": 2,
    }.get(timeframe, U1Config.TIME_STOP_BARS_5M)


def compute_signal_timing(bar_start_ts: datetime, timeframe: str, time_stop_bars: int) -> dict:
    """Compute timing fields for signal payload."""
    now = datetime.now(timezone.utc)
    signal_ts = now

    tf_seconds = {
        "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "4h": 14400, "1d": 86400,
    }
    bar_seconds = tf_seconds.get(timeframe, 300)

    age_seconds = 0  # Fresh signal
    action_by_ts = signal_ts + timedelta(seconds=time_stop_bars * bar_seconds)

    return {
        "bar_start_ts": bar_start_ts,
        "signal_ts": signal_ts,
        "age_seconds": age_seconds,
        "action_by_ts": action_by_ts,
    }


# =============================================================================
# Stop Placement (v2.0: Volatility Envelope)
# =============================================================================

def compute_volatility_envelope_stop(candles: list[OHLCV], entry_price: float,
                                     range_mult: float = None) -> float:
    """
    Volatility envelope stop: entry - N × mean(high-low, last 20 bars).
    Adapts to actual price behavior rather than ATR (less noisy on penny coins).
    """
    if range_mult is None:
        range_mult = U1Config.STOP_RANGE_MULT

    if len(candles) < 20:
        # Fallback to ATR-based
        atr_pct = compute_atr_pct(candles)
        stop = entry_price * (1 - atr_pct * U1Config.STOP_ATR_BUFFER)
        min_stop = entry_price * (1 - U1Config.MIN_STOP_PCT)
        return min(stop, min_stop)

    # Mean range over last 20 bars
    ranges = [c.high - c.low for c in candles[-20:]]
    mean_range = _safe_mean(ranges)

    stop = entry_price - range_mult * mean_range
    # Floor: stop must be at least MIN_STOP_PCT away (clears fee burden on pennycoins)
    min_stop = entry_price * (1 - U1Config.MIN_STOP_PCT)
    stop = min(stop, min_stop)
    return max(stop, entry_price * 0.80)  # Ceiling: never risk more than 20%


# =============================================================================
# Demotion Rules
# =============================================================================

def check_demotion(state: U1State, metrics: dict) -> tuple[bool, str]:
    """Check if EMBRYO_ACTIVE asset should be demoted."""

    # Rule 1: Pump liquidity trap
    if metrics.get("rel_volume", 0) > 3.0:
        spread_last = metrics.get("spread_proxy_last", 0)
        spread_prev = metrics.get("spread_proxy_prev", 1)
        if spread_last > 1.5 * spread_prev:
            return True, "PUMP_TRAP:spread_widening_with_volume"

    # Rule 2: Whale dump
    if metrics.get("whale_ratio", 0) > 20:
        return True, "WHALE_DUMP:single_bar_dominance"

    # Rule 3: Timeout (stagnation)
    if state.promoted_at:
        hours_active = (datetime.now(timezone.utc) - state.promoted_at).total_seconds() / 3600
        if hours_active > U1Config.T_ACTIVE_MAX_HOURS:
            return True, f"TIMEOUT:no_ignition_after_{hours_active:.0f}h"

    return False, ""


# =============================================================================
# Evaluator
# =============================================================================

class U1Evaluator:
    """
    Evaluates U1 - Unicorn Ignition setup (v2.0 data-driven).

    Two modes of operation:
    1. Pipeline mode: Evaluate gates for promotion (called by unicorn_pipeline)
    2. Signal mode: Evaluate triggers for signal emission (called by signal_worker)

    v2.0: Ignition scoring uses profiler-derived features and weights.
    """

    def __init__(self):
        self.config = U1Config()

    def evaluate_gates(
        self,
        candles: list[OHLCV],
        prev_state: Optional[U1State] = None,
        is_final: bool = True,
    ) -> U1State:
        """
        Evaluate promotion gates (Pipeline worker calls this).

        Canon §1.8: Gates evaluate on final bars only by default.
        The pipeline should pass is_final=True (bars are closed 5m candles).

        Args:
            candles: OHLCV candles (need ~96 bars for two windows)
            prev_state: Previous state for continuity
            is_final: Whether current bar is final (Canon §1.8)

        Returns:
            Updated U1State with gate results and scores
        """
        state = prev_state or U1State()
        state.last_eval_ts = datetime.now(timezone.utc)

        # Need enough candles for two windows
        window = U1Config.GATE_WINDOW_BARS
        if len(candles) < window * 2:
            return state

        candles_last_n = candles[-window:]
        candles_prev_n = candles[-(window * 2):-window]

        # Compute baseline volume
        baseline, window_used = compute_baseline_volume(candles)
        state.rel_volume_baseline = baseline
        state.baseline_window_used = window_used

        # Compute compression box
        box_high, box_low = compute_compression_box(candles, U1Config.COMPRESSION_BOX_LOOKBACK)
        state.compression_box_high = box_high
        state.compression_box_low = box_low
        state.compression_box_lookback = U1Config.COMPRESSION_BOX_LOOKBACK

        # Compute rel_volume for current bar
        current = candles[-1]
        rel_volume_last = current.volume / baseline if baseline > 0 else 0

        # Gate 1: TradabilityProxy
        g1_passed, g1_metrics = evaluate_gate1_tradability_proxy(candles_last_n, candles_prev_n)
        state.gate1_passed = g1_passed

        # Gate 2: ParticipationProxy
        g2_passed, g2_metrics = evaluate_gate2_participation_proxy(candles_last_n, candles_prev_n)
        state.gate2_passed = g2_passed

        # Gate 3: Compression
        g3_passed, g3_metrics = evaluate_gate3_compression(candles_last_n, candles_prev_n, rel_volume_last)
        state.gate3_passed = g3_passed

        # Gate 4: HolderIntegrity (skipped in MVP)
        state.gate4_passed = None

        # Combine metrics
        all_metrics = {**g1_metrics, **g2_metrics, **g3_metrics}
        state.metrics = all_metrics

        # Compute legacy scores
        state.tradability_score = compute_tradability_score(all_metrics)
        state.participation_score = compute_participation_score(all_metrics)
        state.compression_score = compute_compression_score(all_metrics)
        state.trap_risk_score = compute_trap_risk_score(all_metrics)
        state.embryo_active_score = compute_embryo_active_score(
            state.tradability_score,
            state.participation_score,
            state.compression_score,
            state.trap_risk_score,
        )

        # Compute data-driven ignition features (v2.0)
        ign_features = compute_ignition_features(candles, baseline)
        if ign_features:
            total, per_coin, temp = compute_ignition_score(ign_features)
            state.ignition_score = total
            state.per_coin_score = per_coin
            state.universe_temp_score = temp
            state.vol_z_at_trigger = ign_features.get("vol_z", 0)

        # Determine phase
        all_gates_passed = g1_passed and g2_passed and g3_passed

        if all_gates_passed:
            if state.phase == U1Phase.EMBRYO:
                state.phase = U1Phase.EMBRYO_ACTIVE
                state.promoted_at = datetime.now(timezone.utc)
            # Check if ignition score qualifies for SCANNING
            if state.phase == U1Phase.EMBRYO_ACTIVE and state.ignition_score > 0.3:
                state.phase = U1Phase.SCANNING
        else:
            # Check demotion
            should_demote, reason = check_demotion(state, all_metrics)
            if should_demote and state.phase in (
                U1Phase.EMBRYO_ACTIVE, U1Phase.SCANNING, U1Phase.IGNITION_WATCH
            ):
                state.phase = U1Phase.EMBRYO
                state.promoted_at = None

        return state

    def evaluate_trigger(
        self,
        candles: list[OHLCV],
        state: U1State,
        symbol: str,
        timeframe: str,
        is_final: bool = True,
        funding_rates: dict = None,
        database_url: str = None,
        volume_z_1d: float = None,
    ) -> Optional[U1Signal]:
        """
        Evaluate ignition trigger (Signal worker calls this for EMBRYO_ACTIVE symbols).

        v2.0: Uses data-driven ignition scoring alongside traditional triggers.
        v2.2: Social silence filter — reject signals for coins with social mentions in last 30d.
        v4.0: volume_z_1d hard gate, bb_width hard gate, funding boost removed,
              mean reversion soft score added.
        Returns U1Signal if triggered, None otherwise.
        """
        # Canon §1.8: Anti-lookahead — only trigger on closed/final candles
        if not is_final:
            logger.debug("U1 gate:forming_candle %s %s (is_final=False)", symbol, timeframe)
            return None

        # Only evaluate for active phases
        if state.phase not in (U1Phase.EMBRYO_ACTIVE, U1Phase.SCANNING, U1Phase.IGNITION_WATCH):
            return None

        # v4.0: Daily volume regime gate (HARD)
        if volume_z_1d is not None and volume_z_1d < U1Config.VOLUME_Z_1D_MIN:
            return None

        # Compute features early — needed for bb_width gate + scoring
        ign_features = compute_ignition_features(candles, state.rel_volume_baseline, timeframe=timeframe)

        # v4.0: Volatility regime gate (HARD) — replaces BB squeeze
        # Only gate on bb_width for validated TF+threshold pairs.
        # bb_width is structurally different across timeframes (20-period BB covers
        # different wall-clock spans), so each TF needs its own validated threshold.
        # Unvalidated TFs skip this gate entirely rather than guessing.
        _BB_WIDTH_THRESHOLDS = {"5m": U1Config.BB_WIDTH_MIN_5M, "15m": U1Config.BB_WIDTH_MIN_15M}
        if ign_features and timeframe in _BB_WIDTH_THRESHOLDS:
            bb_width = ign_features.get("bb_width", 0)
            if bb_width < _BB_WIDTH_THRESHOLDS[timeframe]:
                return None

        # Check traditional triggers
        triggers_passed, trigger_metrics = check_ignition_triggers(state, candles)

        # v2.0: Also check if ignition score alone is high enough
        score_check = False
        if ign_features:
            total, per_coin, temp = compute_ignition_score(ign_features)

            # v4.0: Funding rate — still track for data but no score boost
            fr = (funding_rates or {}).get(symbol, None)
            if fr is not None:
                state.funding_rate = fr
                state.funding_rate_updated = True

            state.ignition_score = total
            state.per_coin_score = per_coin
            state.vol_z_at_trigger = ign_features.get("vol_z", 0)

            # Score-based trigger: ignition score above threshold AND vol_z above floor
            score_check = (
                total >= U1Config.IGNITION_SCORE_THRESHOLD
                and ign_features.get("vol_z", 0) >= U1Config.VOL_Z_MIN
            )

        # v3.0: Express Lane — extreme momentum trigger path
        express_lane = False
        if not triggers_passed and not score_check:
            if U1_EXPRESS_LANE and ign_features:
                now_utc = datetime.now(timezone.utc)
                hours_active = (now_utc - state.promoted_at).total_seconds() / 3600 if state.promoted_at else 0
                ign = ign_features
                # Pump guard: check spread not widening (reuse demotion Rule 1 threshold)
                spread_ok = True
                window_chk = U1Config.GATE_WINDOW_BARS
                if len(candles) >= window_chk * 2:
                    sp_last = [_candle_range(c) / c.close for c in candles[-window_chk:] if c.close > 0]
                    sp_prev = [_candle_range(c) / c.close for c in candles[-(window_chk * 2):-window_chk] if c.close > 0]
                    if sp_last and sp_prev:
                        spread_ok = _safe_median(sp_last) <= 1.5 * _safe_median(sp_prev)

                if (ign.get("rel_volume", 0) >= 5.0
                        and ign.get("return_12", 0) >= 0.05
                        and ign.get("clv", 0) >= 0.80
                        and ign.get("body_ratio", 0) >= 0.70
                        and ign.get("vol_z", 0) >= 1.0
                        and hours_active >= 1.0
                        and spread_ok):
                    express_lane = True
                    logger.info("U1 EXPRESS_LANE triggered: %s rel_vol=%.1f ret12=%.3f clv=%.2f hours_active=%.1f",
                                symbol, ign.get("rel_volume", 0), ign.get("return_12", 0),
                                ign.get("clv", 0), hours_active)

        # Must pass traditional triggers, score-based trigger, OR express lane
        if not triggers_passed and not score_check and not express_lane:
            return None

        # Check confirmations — required for ALL paths (including express lane)
        window = U1Config.GATE_WINDOW_BARS
        if len(candles) < window * 2:
            return None

        candles_last_n = candles[-window:]
        candles_prev_n = candles[-(window * 2):-window]

        confirmations_passed, confirm_metrics = check_ignition_confirmations(candles_last_n, candles_prev_n)
        min_confirmations = _env_int("U1_MIN_CONFIRMATIONS", 1)
        if confirm_metrics["confirmations_met"] < min_confirmations:
            return None

        # Social silence filter (v2.2) — skip for express lane (disabled by default anyway)
        if not express_lane:
            if U1_SOCIAL_SILENCE_FILTER and database_url:
                if has_social_mentions_30d(symbol, database_url):
                    logger.info("U1 social_silence_filter REJECT %s — social mentions found in 30d", symbol)
                    return None

        # Determine trigger path for attribution
        trigger_path = "traditional" if triggers_passed else ("score_based" if score_check else "express_lane")

        # All conditions met - generate signal
        current = candles[-1]

        # Entry price (next bar open estimate)
        entry_price = current.close

        # v2.0: Volatility envelope stop (adapts to coin's actual range)
        stop_price = compute_volatility_envelope_stop(candles, entry_price)

        # R-value and targets
        r_value = entry_price - stop_price
        if r_value <= 0:
            return None

        tp1_price = entry_price + U1Config.TP1_R * r_value
        tp2_price = entry_price + U1Config.TP2_R * r_value

        # Timing
        time_stop_bars = get_time_stop_bars(timeframe)
        bar_start_ts = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
        timing = compute_signal_timing(bar_start_ts, timeframe, time_stop_bars)

        # v2.0: Confidence incorporates ignition score
        confidence = compute_unicorn_confidence(
            state.embryo_active_score,
            state.ignition_score,
            confirm_metrics["confirmations_met"],
            state.tradability_score,
            state.trap_risk_score,
        )

        # v4.0: Mean reversion soft score
        reversion_score = 0
        if ign_features:
            if ign_features.get("price_change_4h", 0) < U1Config.REVERSION_PC_4H:
                reversion_score += 1
            if ign_features.get("price_change_1h", 0) < U1Config.REVERSION_PC_1H:
                reversion_score += 1
            if ign_features.get("bb_position_1h", 0.5) < U1Config.REVERSION_BB_POS:
                reversion_score += 1

        # REVERSION_SCORE_WEIGHT (0.05) is an invented starting point, NOT validated by WS2.
        # WS2 validated the thresholds but did not produce a calibrated weight.
        # Backfill will report precision with and without this boost to validate it.
        confidence += reversion_score * U1Config.REVERSION_SCORE_WEIGHT
        confidence = max(0.0, min(1.0, confidence))

        # Update state phase
        state.phase = U1Phase.IGNITION

        # Build explainability
        why_items = [
            f"Trigger path: {trigger_path}",
            f"Ignition score: {state.ignition_score:.3f} (threshold: {U1Config.IGNITION_SCORE_THRESHOLD})",
            f"Vol z-score: {state.vol_z_at_trigger:.1f} (min: {U1Config.VOL_Z_MIN})",
        ]
        if volume_z_1d is not None:
            why_items.append(
                f"Volume regime: volume_z_1d={volume_z_1d:.1f} (min: {U1Config.VOLUME_Z_1D_MIN})"
            )
        if ign_features and timeframe in _BB_WIDTH_THRESHOLDS:
            why_items.append(
                f"Volatility regime: bb_width={ign_features.get('bb_width', 0):.4f} (min: {_BB_WIDTH_THRESHOLDS[timeframe]:.2f})"
            )
        if triggers_passed:
            why_items.append(
                f"Rel volume ignition: {trigger_metrics.get('rel_volume', 0):.1f}x baseline"
            )
            why_items.append(
                f"Breakout accepted: {trigger_metrics.get('acceptance_bars_checked', 0)} bars above box"
            )
        if express_lane:
            why_items.append(
                f"Express lane: rel_vol={ign_features.get('rel_volume', 0):.1f}x "
                f"ret12={ign_features.get('return_12', 0):.3f} clv={ign_features.get('clv', 0):.2f}"
            )
        if reversion_score > 0:
            why_items.append(
                f"Reversion boost: {reversion_score}/3 "
                f"(pc4h={ign_features.get('price_change_4h', 0):.4f}, "
                f"pc1h={ign_features.get('price_change_1h', 0):.4f}, "
                f"bbpos1h={ign_features.get('bb_position_1h', 0.5):.3f})"
            )
        why_items.extend([
            f"Per-coin score: {state.per_coin_score:.3f}, Universe temp: {state.universe_temp_score:.3f}",
            f"Compression box: {state.compression_box_high:.8f} - {state.compression_box_low:.8f}",
            f"Confirmations met: {confirm_metrics['confirmations_met']}/3",
            f"Volatility envelope stop: {stop_price:.8f}",
        ])
        explain = ExplainPayload(why_items=why_items)

        return U1Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=Direction.LONG,
            setup_score=confidence,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            bar_start_ts=timing["bar_start_ts"],
            signal_ts=timing["signal_ts"],
            age_seconds=timing["age_seconds"],
            action_by_ts=timing["action_by_ts"],
            time_stop_bars=time_stop_bars,
            rel_volume=trigger_metrics.get("rel_volume", 0) if triggers_passed else ign_features.get("rel_volume", 0),
            breakout_acceptance_bars=trigger_metrics.get("acceptance_bars_checked", 0) if triggers_passed else 0,
            compression_box_high=state.compression_box_high,
            compression_box_low=state.compression_box_low,
            liquidity_volume_rising=confirm_metrics["liquidity_volume_ok"],
            participation_confirmed=confirm_metrics["participation_continues"],
            trend_rate_accelerating=confirm_metrics["trend_accelerating"],
            confirmations_met=confirm_metrics["confirmations_met"],
            tradability_score=state.tradability_score,
            participation_score=state.participation_score,
            compression_score=state.compression_score,
            trap_risk_score=state.trap_risk_score,
            embryo_active_score=state.embryo_active_score,
            explain=explain,
            state=state,
            trigger_path=trigger_path,
            reversion_score=reversion_score,
        )
