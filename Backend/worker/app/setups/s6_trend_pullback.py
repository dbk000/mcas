"""
S6 - Trend Continuation Pullback v1.0 (Canonical Setup)
========================================================

Detect established uptrend/downtrend via EMA structure + slopes,
wait for a pullback to EMA20, then enter on the first reclaim candle
with volume confirmation.

Philosophy: "Buy the dip in a trend" is one of the most reliable
retail patterns. We grade trend quality (slope magnitude, EMA stacking),
pullback depth (ideal: touch EMA20 but hold above EMA50), and the
reclaim bar's conviction.

Score weights:
  TrendStrength    0.25
  PullbackQuality  0.25
  RetraceDepth     0.20
  Participation    0.15
  CleanClose       0.15
"""

import os
import logging
import numpy as np
from dataclasses import dataclass
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
class S6State:
    """Tier 3 state for Trend Continuation Pullback."""
    # Trend structure
    ema_stacked_long: bool     # EMA20 > EMA50
    ema_stacked_short: bool    # EMA20 < EMA50
    ema20_slope_positive: bool
    ema50_slope_positive: bool
    ema20_slope_negative: bool
    ema50_slope_negative: bool
    trend_direction: str       # "long", "short", "none"
    trend_strength: float      # composite 0-1

    # Pullback detection
    touched_ema20: bool        # low <= ema20 (long) or high >= ema20 (short)
    held_ema50: bool           # low > ema50 (long) or high < ema50 (short)
    pullback_detected: bool    # touched_ema20 AND held_ema50

    # Reclaim: current bar closes back above EMA20 (long) / below (short)
    reclaimed_ema20: bool

    # HTF alignment
    htf_aligned: bool
    htf_bias: str
    htf_bias_score: float

    # Retrace depth (for scoring)
    retrace_depth_atr: float   # How deep the pullback went (in ATR units)


@dataclass
class S6Signal:
    """Output when S6 triggers."""
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
    trend_strength: float
    pullback_quality: float
    retrace_depth: float
    participation: float
    clean_close: float

    state: S6State

    explain: ExplainPayload = None
    time_stop_bars: int = 8

    since_ts: Optional[datetime] = None
    action_by_ts: Optional[datetime] = None


class S6Evaluator:
    """
    Evaluates S6 - Trend Continuation Pullback v1.0.

    Usage:
        evaluator = S6Evaluator()
        state = evaluator.compute_state(snapshot, prev_state)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, tf)
    """

    def __init__(
        self,
        # Slope thresholds (ATR-normalised)
        slope_min: float = _env_float("S6_SLOPE_MIN", 0.03),

        # Trigger thresholds
        clv_th: float = _env_float("S6_CLV_TH", 0.65),
        volume_z_min: float = _env_float("S6_VOLUME_Z_MIN", 2.0),

        # Stops
        stop_atr_buffer: float = 0.5,

        # Time stop
        time_stop_bars: int = 8,

        # Targets
        tp1_r: float = 0.5,
        tp2_r: float = 1.0,

        # Slippage
        slippage_bps: float = 0.0005,

        # Pullback lookback (bars to check for touch)
        pullback_lookback: int = _env_int("S6_PULLBACK_LOOKBACK", 5),
    ):
        self.slope_min = slope_min
        self.clv_th = clv_th
        self.volume_z_min = volume_z_min
        self.stop_atr_buffer = stop_atr_buffer
        self.time_stop_bars = time_stop_bars
        self.tp1_r = tp1_r
        self.tp2_r = tp2_r
        self.slippage_bps = slippage_bps
        self.pullback_lookback = pullback_lookback

        logger.info(
            "S6 effective params: slope_min=%.3f clv=%.2f vol_z=%.1f "
            "pullback_lookback=%d",
            self.slope_min, self.clv_th, self.volume_z_min,
            self.pullback_lookback,
        )

    @staticmethod
    def _htf_bias(snapshot: IndicatorSnapshot) -> tuple[str, float]:
        score = 0.0
        if snapshot.htf_close is not None and snapshot.htf_sma50 is not None:
            if snapshot.htf_close > snapshot.htf_sma50:
                score += 1.0
            elif snapshot.htf_close < snapshot.htf_sma50:
                score -= 1.0
        if snapshot.htf_close is not None and snapshot.htf_bb_mb is not None:
            if snapshot.htf_close > snapshot.htf_bb_mb:
                score += 0.5
            elif snapshot.htf_close < snapshot.htf_bb_mb:
                score -= 0.5
        if score > 0.3:
            return "long", score
        elif score < -0.3:
            return "short", score
        return "neutral", score

    def compute_state(
        self,
        snapshot: IndicatorSnapshot,
        prev_state: Optional[S6State] = None,
    ) -> S6State:
        # Trend structure
        ema_stacked_long = snapshot.ema_20 > snapshot.ema_50
        ema_stacked_short = snapshot.ema_20 < snapshot.ema_50
        ema20_slope_pos = snapshot.ema20_slope_atr > self.slope_min
        ema50_slope_pos = snapshot.ema50_slope_atr > self.slope_min
        ema20_slope_neg = snapshot.ema20_slope_atr < -self.slope_min
        ema50_slope_neg = snapshot.ema50_slope_atr < -self.slope_min

        if ema_stacked_long and ema20_slope_pos and ema50_slope_pos:
            trend_direction = "long"
        elif ema_stacked_short and ema20_slope_neg and ema50_slope_neg:
            trend_direction = "short"
        else:
            trend_direction = "none"

        # Trend strength: combine slope magnitudes
        if trend_direction == "long":
            trend_strength = clamp(
                (snapshot.ema20_slope_atr + snapshot.ema50_slope_atr) / 0.1,
                0.0, 1.0,
            )
        elif trend_direction == "short":
            trend_strength = clamp(
                (-snapshot.ema20_slope_atr - snapshot.ema50_slope_atr) / 0.1,
                0.0, 1.0,
            )
        else:
            trend_strength = 0.0

        # Pullback detection: check last N bars for touch of EMA20
        candles = snapshot.ohlcv
        n = min(self.pullback_lookback, len(candles) - 1)
        lookback_candles = candles[-(n + 1):-1]  # Exclude current bar

        touched_ema20 = False
        held_ema50 = True
        retrace_depth_atr = 0.0
        atr = snapshot.atr

        if trend_direction == "long":
            for c in lookback_candles:
                if c.low <= snapshot.ema_20:
                    touched_ema20 = True
                    depth = (snapshot.ema_20 - c.low) / atr if atr > 0 else 0
                    retrace_depth_atr = max(retrace_depth_atr, depth)
                if c.low < snapshot.ema_50:
                    held_ema50 = False
        elif trend_direction == "short":
            for c in lookback_candles:
                if c.high >= snapshot.ema_20:
                    touched_ema20 = True
                    depth = (c.high - snapshot.ema_20) / atr if atr > 0 else 0
                    retrace_depth_atr = max(retrace_depth_atr, depth)
                if c.high > snapshot.ema_50:
                    held_ema50 = False

        # Also count inherited pullback from prev_state
        if prev_state is not None and prev_state.pullback_detected:
            touched_ema20 = True
            if not prev_state.held_ema50:
                held_ema50 = False

        pullback_detected = touched_ema20 and held_ema50

        # Reclaim: current bar closes back above/below EMA20
        current = candles[-1]
        if trend_direction == "long":
            reclaimed_ema20 = current.close > snapshot.ema_20
        elif trend_direction == "short":
            reclaimed_ema20 = current.close < snapshot.ema_20
        else:
            reclaimed_ema20 = False

        htf_bias, htf_bias_score = self._htf_bias(snapshot)
        if trend_direction == "long":
            htf_aligned = htf_bias in ("long", "neutral")
        elif trend_direction == "short":
            htf_aligned = htf_bias in ("short", "neutral")
        else:
            htf_aligned = False

        return S6State(
            ema_stacked_long=ema_stacked_long,
            ema_stacked_short=ema_stacked_short,
            ema20_slope_positive=ema20_slope_pos,
            ema50_slope_positive=ema50_slope_pos,
            ema20_slope_negative=ema20_slope_neg,
            ema50_slope_negative=ema50_slope_neg,
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            touched_ema20=touched_ema20,
            held_ema50=held_ema50,
            pullback_detected=pullback_detected,
            reclaimed_ema20=reclaimed_ema20,
            htf_aligned=htf_aligned,
            htf_bias=htf_bias,
            htf_bias_score=htf_bias_score,
            retrace_depth_atr=retrace_depth_atr,
        )

    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S6State,
        symbol: str,
        timeframe: str,
        is_final: bool = True,
    ) -> Optional[S6Signal]:
        # Canon §1.8
        if not is_final:
            logger.debug("S6 gate:forming_candle %s %s", symbol, timeframe)
            return None

        if state.trend_direction == "none":
            logger.debug("S6 gate:no_trend %s %s", symbol, timeframe)
            return None

        if not state.pullback_detected:
            logger.debug("S6 gate:no_pullback %s %s", symbol, timeframe)
            return None

        if not state.reclaimed_ema20:
            logger.debug("S6 gate:no_reclaim %s %s", symbol, timeframe)
            return None

        if not state.htf_aligned:
            logger.debug("S6 gate:htf_oppose %s %s htf=%s", symbol, timeframe, state.htf_bias)
            return None

        current = snapshot.ohlcv[-1]
        atr = snapshot.atr

        direction = Direction.LONG if state.trend_direction == "long" else Direction.SHORT

        # CLV check
        if direction == Direction.LONG and snapshot.clv < self.clv_th:
            logger.debug("S6 gate:clv %s %s clv=%.2f", symbol, timeframe, snapshot.clv)
            return None
        if direction == Direction.SHORT and snapshot.clv > (1 - self.clv_th):
            logger.debug("S6 gate:clv %s %s clv=%.2f", symbol, timeframe, snapshot.clv)
            return None

        # Volume check
        if snapshot.volume_z < self.volume_z_min:
            logger.debug("S6 gate:vol_z %s %s vz=%.2f", symbol, timeframe, snapshot.volume_z)
            return None

        # Entry
        entry_price = current.close
        if direction == Direction.LONG:
            entry_price *= (1 + self.slippage_bps)
        else:
            entry_price *= (1 - self.slippage_bps)

        # Stop: below EMA50 - buffer (or swing low/high)
        highs = np.array([c.high for c in snapshot.ohlcv])
        lows = np.array([c.low for c in snapshot.ohlcv])

        if direction == Direction.LONG:
            stop_a = snapshot.ema_50 - self.stop_atr_buffer * atr
            pivot_lows = find_pivot_lows(lows[-20:], n_left=1, n_right=0)
            swing_low = max(p[1] for p in pivot_lows[-3:]) if pivot_lows else stop_a
            stop_price = max(stop_a, swing_low)
            r_value = entry_price - stop_price
        else:
            stop_a = snapshot.ema_50 + self.stop_atr_buffer * atr
            pivot_highs = find_pivot_highs(highs[-20:], n_left=1, n_right=0)
            swing_high = min(p[1] for p in pivot_highs[-3:]) if pivot_highs else stop_a
            stop_price = min(stop_a, swing_high)
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
        trend_strength_score = state.trend_strength

        # Pullback quality: touched EMA20 cleanly, held EMA50
        pullback_quality = 0.5  # Base for meeting conditions
        if state.held_ema50:
            pullback_quality += 0.3
        if state.retrace_depth_atr < 1.0:
            pullback_quality += 0.2  # Shallow pullback bonus
        pullback_quality = clamp(pullback_quality, 0.0, 1.0)

        # Retrace depth: sweet spot is 0.3-0.8 ATR (not too shallow, not too deep)
        if 0.3 <= state.retrace_depth_atr <= 0.8:
            retrace_depth_score = 1.0
        elif state.retrace_depth_atr < 0.3:
            retrace_depth_score = clamp(state.retrace_depth_atr / 0.3, 0.0, 1.0)
        else:
            retrace_depth_score = clamp(1.0 - (state.retrace_depth_atr - 0.8) / 1.0, 0.0, 1.0)

        participation = clamp((snapshot.volume_z - 1.0) / 3.0, 0.0, 1.0)

        if direction == Direction.LONG:
            clean_close = clamp((snapshot.clv - self.clv_th) / (1.0 - self.clv_th), 0.0, 1.0)
        else:
            clean_close = clamp(((1 - self.clv_th) - snapshot.clv) / (1 - self.clv_th), 0.0, 1.0)

        setup_score = (
            0.25 * trend_strength_score +
            0.25 * pullback_quality +
            0.20 * retrace_depth_score +
            0.15 * participation +
            0.15 * clean_close
        )

        # Explain
        why_items = [
            f"{'Up' if direction == Direction.LONG else 'Down'}trend: EMA20 {'>' if direction == Direction.LONG else '<'} EMA50, both slopes positive",
            f"EMA20 slope: {snapshot.ema20_slope_atr:+.4f}, EMA50 slope: {snapshot.ema50_slope_atr:+.4f} (ATR/bar)",
            f"Pullback to EMA20 detected (depth {state.retrace_depth_atr:.2f} ATR), held above EMA50",
            f"Reclaim confirmed: close {'above' if direction == Direction.LONG else 'below'} EMA20",
            f"Volume Z-score: {snapshot.volume_z:.2f} (participation)",
            f"Clean close (CLV): {snapshot.clv:.2f}",
            f"HTF bias: {state.htf_bias} (aligned: {state.htf_aligned})",
        ]
        explain = ExplainPayload(why_items=why_items[:8])

        bar_seconds = _TF_SECONDS.get(timeframe, 3600)
        since_ts = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
        action_by_ts = since_ts + timedelta(seconds=self.time_stop_bars * bar_seconds)

        return S6Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_score=setup_score,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            trend_strength=trend_strength_score,
            pullback_quality=pullback_quality,
            retrace_depth=retrace_depth_score,
            participation=participation,
            clean_close=clean_close,
            state=state,
            explain=explain,
            time_stop_bars=self.time_stop_bars,
            since_ts=since_ts,
            action_by_ts=action_by_ts,
        )
