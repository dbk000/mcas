"""
U1 - Unicorn Ignition v1.0
==========================

Identifies low-volume pennycoins transitioning from dead -> discoverable -> ignition
before they appear on normal "active assets" lists.

Three-phase pipeline:
- Phase A: Embryo Universe (broad, cheap scan)
- Phase B: Embryo Active (capped attention budget)
- Phase C: Ignition Watch -> Unicorn Signal

Human-in-loop: Signals are identification + ranking only. No auto-execution.

Trade levels (entry/stop/TP) are suggested levels for human execution
and for future backtest consistency.
"""

import logging
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
# Configuration
# =============================================================================

class U1Config:
    """Unicorn configuration defaults."""

    # Universe (MVP: Spot USDT only)
    MIN_VOLUME_USD = 10_000
    MAX_VOLUME_USD = 1_000_000
    MAX_MIN_NOTIONAL = 100
    HYPE_REL_VOL_CAP = 20.0
    HYPE_RANGE_CAP = 0.50
    STABLECOIN_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD"}
    WRAPPED_PATTERNS = ["WBTC", "WETH", "WBNB"]
    LEVERAGE_PATTERNS = ["UP", "DOWN", "BULL", "BEAR", "3L", "3S"]

    # Gates
    GATE_WINDOW_BARS = 48
    GATE_TIMEFRAME = "5m"
    TRADABILITY_IMPROVEMENT_FACTOR = 0.7
    PARTICIPATION_GROWTH_FACTOR = 1.5
    RANGE_PCT_THRESHOLD = 0.05
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

    # Ignition
    REL_VOL_IGNITION = 5.0
    BREAKOUT_ACCEPTANCE_BARS = 2

    # Confirmations
    SPREAD_WIDENING_THRESHOLD = 1.2

    # Time stop
    TIME_STOP_BARS_5M = 12
    TIME_STOP_BARS_15M = 8
    TIME_STOP_BARS_1H = 4

    # Cap
    MAX_EMBRYO_ACTIVE = 50
    T_ACTIVE_MAX_HOURS = 168  # 7 days

    # Scoring weights
    W_TRADABILITY = 0.25
    W_PARTICIPATION = 0.30
    W_COMPRESSION = 0.30
    W_TRAP_RISK = 0.15

    # Risk (suggested levels for human execution)
    STOP_ATR_BUFFER = 1.0
    TP1_R = 1.0
    TP2_R = 2.0


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
    IGNITION_WATCH = "IGNITION_WATCH"
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

    # Scores
    tradability_score: float = 0.0
    participation_score: float = 0.0
    compression_score: float = 0.0
    trap_risk_score: float = 0.0
    embryo_active_score: float = 0.0

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
    setup_score: float = 0.0  # = unicorn_confidence (0-1)

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
    spread_improving = spread_proxy_last <= 0.7 * spread_proxy_prev if spread_proxy_prev > 0 else False

    # Metric 2: Volume coefficient of variation
    mean_vol_last = _safe_mean([c.volume for c in candles_last_n])
    mean_vol_prev = _safe_mean([c.volume for c in candles_prev_n])
    vol_cv_last = _safe_std([c.volume for c in candles_last_n]) / mean_vol_last if mean_vol_last > 0 else float('inf')
    vol_cv_prev = _safe_std([c.volume for c in candles_prev_n]) / mean_vol_prev if mean_vol_prev > 0 else float('inf')
    volume_stabilizing = vol_cv_last <= 0.8 * vol_cv_prev if vol_cv_prev < float('inf') else False

    # Metric 3: ATR% compression
    atr_pct_last = compute_atr_pct(candles_last_n)
    atr_pct_prev = compute_atr_pct(candles_prev_n)
    atr_compressing = atr_pct_last <= 0.7 * atr_pct_prev if atr_pct_prev > 0 else False

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
    """OHLCV proxy for participation broadening. Pass all 3."""

    # Metric 1: Volume sum rising
    vol_sum_last = sum(c.volume for c in candles_last_n)
    vol_sum_prev = sum(c.volume for c in candles_prev_n)
    volume_rising = vol_sum_last >= 1.5 * vol_sum_prev if vol_sum_prev > 0 else False

    # Metric 2: Active bars proxy
    active_bars_last = sum(1 for c in candles_last_n if c.volume > 0)
    active_bars_prev = sum(1 for c in candles_prev_n if c.volume > 0)
    activity_rising = active_bars_last >= 1.3 * active_bars_prev if active_bars_prev > 0 else False

    # Metric 3: No whale dominance
    volumes_last = [c.volume for c in candles_last_n if c.volume > 0]
    avg_vol_last = _safe_mean(volumes_last) if volumes_last else 0
    max_vol_last = max(volumes_last) if volumes_last else 0
    whale_ratio = max_vol_last / avg_vol_last if avg_vol_last > 0 else float('inf')
    no_whale_dominance = whale_ratio < 10.0

    passed = volume_rising and activity_rising and no_whale_dominance

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
    """Compression detection for pre-ignition state. Pass all 3."""

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

    passed = range_tight and atr_contracting and not_igniting

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
    confirmations_met: int,
    tradability: float,
    trap_risk: float,
) -> float:
    """UnicornConfidence for signal ranking (0-1 scale)."""
    base = embryo_score / 100
    confirmation_bonus = 0.1 * (confirmations_met - 2) if confirmations_met > 2 else 0
    tradability_bonus = 0.1 * (tradability / 100)
    risk_penalty = 0.2 * (trap_risk / 100)

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
    Evaluates U1 - Unicorn Ignition setup.

    Two modes of operation:
    1. Pipeline mode: Evaluate gates for promotion (called by unicorn_pipeline)
    2. Signal mode: Evaluate triggers for signal emission (called by signal_worker)
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

        # Compute scores
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

        # Determine phase
        all_gates_passed = g1_passed and g2_passed and g3_passed

        if all_gates_passed:
            if state.phase == U1Phase.EMBRYO:
                state.phase = U1Phase.EMBRYO_ACTIVE
                state.promoted_at = datetime.now(timezone.utc)
            # Already EMBRYO_ACTIVE or IGNITION_WATCH stays
        else:
            # Check demotion
            should_demote, reason = check_demotion(state, all_metrics)
            if should_demote and state.phase in (U1Phase.EMBRYO_ACTIVE, U1Phase.IGNITION_WATCH):
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
    ) -> Optional[U1Signal]:
        """
        Evaluate ignition trigger (Signal worker calls this for EMBRYO_ACTIVE symbols).

        Returns U1Signal if triggered, None otherwise.
        """
        # Canon §1.8: Anti-lookahead — only trigger on closed/final candles
        if not is_final:
            logger.debug("U1 gate:forming_candle %s %s (is_final=False)", symbol, timeframe)
            return None

        # Only evaluate for EMBRYO_ACTIVE
        if state.phase not in (U1Phase.EMBRYO_ACTIVE, U1Phase.IGNITION_WATCH):
            return None

        # Check triggers
        triggers_passed, trigger_metrics = check_ignition_triggers(state, candles)
        if not triggers_passed:
            return None

        # Check confirmations
        window = U1Config.GATE_WINDOW_BARS
        if len(candles) < window * 2:
            return None

        candles_last_n = candles[-window:]
        candles_prev_n = candles[-(window * 2):-window]

        confirmations_passed, confirm_metrics = check_ignition_confirmations(candles_last_n, candles_prev_n)
        if not confirmations_passed:
            return None

        # All conditions met - generate signal
        current = candles[-1]

        # Compute ATR for stop calculation
        atr_pct = compute_atr_pct(candles)
        atr = atr_pct * current.close

        # Entry price (next bar open estimate)
        entry_price = current.close

        # Stop below compression box low
        stop_price = state.compression_box_low - U1Config.STOP_ATR_BUFFER * atr

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

        # Confidence
        confidence = compute_unicorn_confidence(
            state.embryo_active_score,
            confirm_metrics["confirmations_met"],
            state.tradability_score,
            state.trap_risk_score,
        )

        # Build explainability
        why_items = [
            f"Rel volume ignition: {trigger_metrics['rel_volume']:.1f}x baseline",
            f"Breakout accepted: {trigger_metrics['acceptance_bars_checked']} bars above box",
            f"Compression box: {state.compression_box_high:.8f} - {state.compression_box_low:.8f}",
            f"Tradability score: {state.tradability_score:.0f}/100",
            f"Participation score: {state.participation_score:.0f}/100",
            f"Compression score: {state.compression_score:.0f}/100",
            f"Confirmations met: {confirm_metrics['confirmations_met']}/3",
        ]
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
            rel_volume=trigger_metrics["rel_volume"],
            breakout_acceptance_bars=trigger_metrics["acceptance_bars_checked"],
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
        )
