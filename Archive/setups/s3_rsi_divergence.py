"""
S3 - RSI Divergence Reversal v1.0 (Canonical Setup)
=====================================================

Detect bullish/bearish RSI divergence against price action, then
enter when RSI reclaims the 30/70 boundary with volume confirmation.

Philosophy: RSI divergence is one of the most reliable reversal
signals. When price makes a lower low but RSI makes a higher low
(bullish), it signals weakening downside momentum. We grade the
divergence magnitude, RSI extremity, span quality, and the
confirmation candle.

Score weights:
  DivMagnitude     0.30
  RSI_Extremity    0.25
  Participation    0.20
  CleanClose       0.15
  SpanQuality      0.10
"""

import os
import logging
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val is not None else default


import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import (
    IndicatorSnapshot,
    OHLCV,
    clamp,
    find_pivot_lows,
    find_pivot_highs,
)
from setup_base import Direction, ExplainPayload


_TF_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


@dataclass
class DivergenceInfo:
    """Details of a detected divergence."""
    divergence_type: str  # "bullish" or "bearish"

    # Price pivots
    price_pivot1_idx: int
    price_pivot1_val: float
    price_pivot2_idx: int
    price_pivot2_val: float

    # RSI pivots
    rsi_pivot1_idx: int
    rsi_pivot1_val: float
    rsi_pivot2_idx: int
    rsi_pivot2_val: float

    # Magnitude: how far RSI diverges from price direction
    magnitude: float       # normalised 0-1
    span_bars: int         # bars between the two pivot pairs


@dataclass
class S3State:
    """Tier 3 state for RSI Divergence Reversal."""
    # Divergence detection
    bullish_divergence: Optional[DivergenceInfo]
    bearish_divergence: Optional[DivergenceInfo]
    has_divergence: bool

    # RSI reclaim
    rsi_reclaimed_30: bool  # RSI crossed back above 30 (bullish)
    rsi_reclaimed_70: bool  # RSI crossed back below 70 (bearish)

    # Current RSI zone
    rsi_extreme_low: bool   # RSI was < 30 recently
    rsi_extreme_high: bool  # RSI was > 70 recently


@dataclass
class S3Signal:
    """Output when S3 triggers."""
    symbol: str
    timeframe: str
    direction: Direction
    setup_score: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    r_value: float

    # Score components
    div_magnitude: float
    rsi_extremity: float
    participation: float
    clean_close: float
    span_quality: float

    state: S3State

    explain: ExplainPayload = None
    time_stop_bars: int = 8

    since_ts: Optional[datetime] = None
    action_by_ts: Optional[datetime] = None


class S3Evaluator:
    """
    Evaluates S3 - RSI Divergence Reversal v1.0.

    Usage:
        evaluator = S3Evaluator()
        state = evaluator.compute_state(snapshot, rsi_series)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, tf)
    """

    def __init__(
        self,
        # Divergence detection
        pivot_lookback: int = _env_int("S3_PIVOT_LOOKBACK", 50),
        min_span_bars: int = _env_int("S3_MIN_SPAN_BARS", 5),
        max_span_bars: int = _env_int("S3_MAX_SPAN_BARS", 40),

        # Trigger thresholds
        clv_th: float = _env_float("S3_CLV_TH", 0.65),
        volume_z_min: float = _env_float("S3_VOLUME_Z_MIN", 1.2),

        # RSI thresholds
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,

        # Stops (tighter for reversals)
        stop_atr_buffer: float = 0.3,

        # Time stop
        time_stop_bars: int = 8,

        # Targets
        tp1_r: float = 0.5,
        tp2_r: float = 1.0,

        # Slippage
        slippage_bps: float = 0.0005,
    ):
        self.pivot_lookback = pivot_lookback
        self.min_span_bars = min_span_bars
        self.max_span_bars = max_span_bars
        self.clv_th = clv_th
        self.volume_z_min = volume_z_min
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.stop_atr_buffer = stop_atr_buffer
        self.time_stop_bars = time_stop_bars
        self.tp1_r = tp1_r
        self.tp2_r = tp2_r
        self.slippage_bps = slippage_bps

        logger.info(
            "S3 effective params: pivot_lookback=%d span=%d-%d "
            "clv=%.2f vol_z=%.1f rsi_os=%.0f rsi_ob=%.0f",
            self.pivot_lookback, self.min_span_bars, self.max_span_bars,
            self.clv_th, self.volume_z_min, self.rsi_oversold, self.rsi_overbought,
        )

    def _detect_divergence(
        self,
        snapshot: IndicatorSnapshot,
        rsi_series: np.ndarray,
    ) -> tuple[Optional[DivergenceInfo], Optional[DivergenceInfo]]:
        """
        Detect bullish and bearish RSI divergences.

        Bullish: price makes lower low, RSI makes higher low.
        Bearish: price makes higher high, RSI makes lower high.

        Returns (bullish_div, bearish_div) — each Optional.
        """
        candles = snapshot.ohlcv
        n = min(self.pivot_lookback, len(candles), len(rsi_series))
        if n < 10:
            return None, None

        # Extract arrays for the lookback window
        lows = np.array([c.low for c in candles[-n:]])
        highs = np.array([c.high for c in candles[-n:]])
        rsi_window = rsi_series[-n:]

        # Find pivot lows/highs (for price and RSI)
        price_pivot_lows = find_pivot_lows(lows, n_left=2, n_right=1)
        price_pivot_highs = find_pivot_highs(highs, n_left=2, n_right=1)
        rsi_pivot_lows = find_pivot_lows(rsi_window, n_left=2, n_right=1)
        rsi_pivot_highs = find_pivot_highs(rsi_window, n_left=2, n_right=1)

        bullish_div = self._match_bullish(price_pivot_lows, rsi_pivot_lows, rsi_window)
        bearish_div = self._match_bearish(price_pivot_highs, rsi_pivot_highs, rsi_window)

        return bullish_div, bearish_div

    def _match_bullish(
        self,
        price_lows: list[tuple[int, float]],
        rsi_lows: list[tuple[int, float]],
        rsi_window: np.ndarray,
    ) -> Optional[DivergenceInfo]:
        """
        Bullish divergence: price lower low + RSI higher low.
        Match the last 2 price pivot lows with the closest RSI pivot lows.
        """
        if len(price_lows) < 2 or len(rsi_lows) < 2:
            return None

        # Take the last two price pivot lows
        p2_idx, p2_val = price_lows[-1]  # More recent
        p1_idx, p1_val = price_lows[-2]  # Earlier

        span = p2_idx - p1_idx
        if span < self.min_span_bars or span > self.max_span_bars:
            return None

        # Price lower low?
        if p2_val >= p1_val:
            return None

        # Find RSI pivots nearest to each price pivot
        r1 = self._nearest_pivot(rsi_lows, p1_idx)
        r2 = self._nearest_pivot(rsi_lows, p2_idx)
        if r1 is None or r2 is None:
            return None

        r1_idx, r1_val = r1
        r2_idx, r2_val = r2

        # RSI higher low?
        if r2_val <= r1_val:
            return None

        # Both RSI pivots should be in oversold territory (or near it)
        if min(r1_val, r2_val) > self.rsi_oversold + 10:
            return None

        # Magnitude: how much RSI diverges (normalised)
        rsi_diff = r2_val - r1_val  # positive for bullish
        price_diff = abs(p1_val - p2_val)
        magnitude = clamp(rsi_diff / 20.0, 0.0, 1.0)

        return DivergenceInfo(
            divergence_type="bullish",
            price_pivot1_idx=p1_idx,
            price_pivot1_val=p1_val,
            price_pivot2_idx=p2_idx,
            price_pivot2_val=p2_val,
            rsi_pivot1_idx=r1_idx,
            rsi_pivot1_val=r1_val,
            rsi_pivot2_idx=r2_idx,
            rsi_pivot2_val=r2_val,
            magnitude=magnitude,
            span_bars=span,
        )

    def _match_bearish(
        self,
        price_highs: list[tuple[int, float]],
        rsi_highs: list[tuple[int, float]],
        rsi_window: np.ndarray,
    ) -> Optional[DivergenceInfo]:
        """
        Bearish divergence: price higher high + RSI lower high.
        """
        if len(price_highs) < 2 or len(rsi_highs) < 2:
            return None

        p2_idx, p2_val = price_highs[-1]
        p1_idx, p1_val = price_highs[-2]

        span = p2_idx - p1_idx
        if span < self.min_span_bars or span > self.max_span_bars:
            return None

        # Price higher high?
        if p2_val <= p1_val:
            return None

        r1 = self._nearest_pivot(rsi_highs, p1_idx)
        r2 = self._nearest_pivot(rsi_highs, p2_idx)
        if r1 is None or r2 is None:
            return None

        r1_idx, r1_val = r1
        r2_idx, r2_val = r2

        # RSI lower high?
        if r2_val >= r1_val:
            return None

        # Both should be near overbought
        if max(r1_val, r2_val) < self.rsi_overbought - 10:
            return None

        rsi_diff = r1_val - r2_val  # positive for bearish div
        magnitude = clamp(rsi_diff / 20.0, 0.0, 1.0)

        return DivergenceInfo(
            divergence_type="bearish",
            price_pivot1_idx=p1_idx,
            price_pivot1_val=p1_val,
            price_pivot2_idx=p2_idx,
            price_pivot2_val=p2_val,
            rsi_pivot1_idx=r1_idx,
            rsi_pivot1_val=r1_val,
            rsi_pivot2_idx=r2_idx,
            rsi_pivot2_val=r2_val,
            magnitude=magnitude,
            span_bars=span,
        )

    @staticmethod
    def _nearest_pivot(
        pivots: list[tuple[int, float]],
        target_idx: int,
        max_distance: int = 5,
    ) -> Optional[tuple[int, float]]:
        """Find the pivot closest to target_idx within max_distance bars."""
        best = None
        best_dist = max_distance + 1
        for idx, val in pivots:
            dist = abs(idx - target_idx)
            if dist < best_dist:
                best = (idx, val)
                best_dist = dist
        return best if best_dist <= max_distance else None

    def compute_state(
        self,
        snapshot: IndicatorSnapshot,
        rsi_series: np.ndarray,
    ) -> S3State:
        """
        Compute Tier 3 state.

        Args:
            snapshot: Current indicator snapshot
            rsi_series: Full RSI series from FeatureFactory (for pivot detection)
        """
        bullish_div, bearish_div = self._detect_divergence(snapshot, rsi_series)

        # RSI reclaim checks
        # Bullish: RSI was below 30, now crossed back above
        rsi = snapshot.rsi
        rsi_reclaimed_30 = rsi > self.rsi_oversold and snapshot.rsi_slope > 0
        rsi_reclaimed_70 = rsi < self.rsi_overbought and snapshot.rsi_slope < 0

        # Was RSI extreme recently? (within last 5 bars of the RSI series)
        n_recent = min(5, len(rsi_series))
        recent_rsi = rsi_series[-n_recent:]
        rsi_extreme_low = bool(np.any(recent_rsi < self.rsi_oversold))
        rsi_extreme_high = bool(np.any(recent_rsi > self.rsi_overbought))

        return S3State(
            bullish_divergence=bullish_div,
            bearish_divergence=bearish_div,
            has_divergence=(bullish_div is not None or bearish_div is not None),
            rsi_reclaimed_30=rsi_reclaimed_30,
            rsi_reclaimed_70=rsi_reclaimed_70,
            rsi_extreme_low=rsi_extreme_low,
            rsi_extreme_high=rsi_extreme_high,
        )

    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S3State,
        symbol: str,
        timeframe: str,
        is_final: bool = True,
    ) -> Optional[S3Signal]:
        # Canon §1.8
        if not is_final:
            logger.debug("S3 gate:forming_candle %s %s", symbol, timeframe)
            return None

        if not state.has_divergence:
            logger.debug("S3 gate:no_divergence %s %s", symbol, timeframe)
            return None

        current = snapshot.ohlcv[-1]
        atr = snapshot.atr

        # Bullish trigger
        bullish_trigger = (
            state.bullish_divergence is not None
            and state.rsi_reclaimed_30
            and state.rsi_extreme_low
            and snapshot.clv >= self.clv_th
            and snapshot.volume_z >= self.volume_z_min
        )

        # Bearish trigger
        bearish_trigger = (
            state.bearish_divergence is not None
            and state.rsi_reclaimed_70
            and state.rsi_extreme_high
            and snapshot.clv <= (1 - self.clv_th)
            and snapshot.volume_z >= self.volume_z_min
        )

        if not bullish_trigger and not bearish_trigger:
            logger.debug("S3 gate:no_trigger %s %s bull=%s bear=%s recl30=%s recl70=%s",
                         symbol, timeframe,
                         state.bullish_divergence is not None,
                         state.bearish_divergence is not None,
                         state.rsi_reclaimed_30, state.rsi_reclaimed_70)
            return None

        if bullish_trigger:
            direction = Direction.LONG
            div_info = state.bullish_divergence
        else:
            direction = Direction.SHORT
            div_info = state.bearish_divergence

        # Entry
        entry_price = current.close
        if direction == Direction.LONG:
            entry_price *= (1 + self.slippage_bps)
        else:
            entry_price *= (1 - self.slippage_bps)

        # Stop: below divergence swing low (long) or above swing high (short)
        lows = np.array([c.low for c in snapshot.ohlcv])
        highs = np.array([c.high for c in snapshot.ohlcv])

        if direction == Direction.LONG:
            # Stop below the divergence low point
            div_low = div_info.price_pivot2_val
            stop_price = div_low - self.stop_atr_buffer * atr
            r_value = entry_price - stop_price
        else:
            div_high = div_info.price_pivot2_val
            stop_price = div_high + self.stop_atr_buffer * atr
            r_value = stop_price - entry_price

        min_r = 0.5 * atr
        if r_value < min_r:
            return None

        # Targets
        if direction == Direction.LONG:
            tp1_price = entry_price + self.tp1_r * r_value
            tp2_price = entry_price + self.tp2_r * r_value
        else:
            tp1_price = entry_price - self.tp1_r * r_value
            tp2_price = entry_price - self.tp2_r * r_value

        # Score components
        div_magnitude = div_info.magnitude

        # RSI extremity: how deep into oversold/overbought territory
        if direction == Direction.LONG:
            rsi_extremity = clamp((self.rsi_oversold - div_info.rsi_pivot2_val + 10) / 20.0, 0.0, 1.0)
        else:
            rsi_extremity = clamp((div_info.rsi_pivot2_val - self.rsi_overbought + 10) / 20.0, 0.0, 1.0)

        participation = clamp((snapshot.volume_z - 1.0) / 3.0, 0.0, 1.0)

        if direction == Direction.LONG:
            clean_close = clamp((snapshot.clv - self.clv_th) / (1.0 - self.clv_th), 0.0, 1.0)
        else:
            clean_close = clamp(((1 - self.clv_th) - snapshot.clv) / (1 - self.clv_th), 0.0, 1.0)

        # Span quality: ideal span is 10-25 bars; too short = noise, too long = stale
        span = div_info.span_bars
        if 10 <= span <= 25:
            span_quality = 1.0
        elif span < 10:
            span_quality = clamp(span / 10.0, 0.0, 1.0)
        else:
            span_quality = clamp(1.0 - (span - 25) / 15.0, 0.0, 1.0)

        setup_score = (
            0.30 * div_magnitude +
            0.25 * rsi_extremity +
            0.20 * participation +
            0.15 * clean_close +
            0.10 * span_quality
        )

        # Explain
        div_type = "Bullish" if direction == Direction.LONG else "Bearish"
        why_items = [
            f"{div_type} RSI divergence detected (span: {div_info.span_bars} bars)",
            f"Price: {div_info.price_pivot1_val:.4g} -> {div_info.price_pivot2_val:.4g} "
            f"({'lower low' if direction == Direction.LONG else 'higher high'})",
            f"RSI: {div_info.rsi_pivot1_val:.1f} -> {div_info.rsi_pivot2_val:.1f} "
            f"({'higher low' if direction == Direction.LONG else 'lower high'})",
            f"RSI reclaim: crossed {'above 30' if direction == Direction.LONG else 'below 70'} "
            f"(current RSI: {snapshot.rsi:.1f})",
            f"Volume Z-score: {snapshot.volume_z:.2f} (participation confirmed)",
            f"Divergence magnitude: {div_info.magnitude:.0%}",
            f"Clean close (CLV): {snapshot.clv:.2f}",
        ]
        explain = ExplainPayload(why_items=why_items[:8])

        bar_seconds = _TF_SECONDS.get(timeframe, 3600)
        since_ts = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
        action_by_ts = since_ts + timedelta(seconds=self.time_stop_bars * bar_seconds)

        return S3Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_score=setup_score,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            div_magnitude=div_magnitude,
            rsi_extremity=rsi_extremity,
            participation=participation,
            clean_close=clean_close,
            span_quality=span_quality,
            state=state,
            explain=explain,
            time_stop_bars=self.time_stop_bars,
            since_ts=since_ts,
            action_by_ts=action_by_ts,
        )
