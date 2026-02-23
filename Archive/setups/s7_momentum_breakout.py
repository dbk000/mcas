"""
S7 - Momentum Breakout (Donchian Channel) v1.0 (Canonical Setup)
=================================================================

Detect a Donchian channel breakout with momentum confirmation.
Enter when price closes beyond the 20-bar high/low channel with
volume participation and directional bias alignment.

Philosophy: Donchian breakout is one of the oldest trend-following
signals. We grade it by break strength, volume quality, and
multi-timeframe bias alignment to filter out false breakouts.

Score weights:
  BreakStrength    0.25
  VolumeQuality    0.25
  BiasStrength     0.20
  CleanClose       0.15
  HTF_Confluence   0.15
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

# S7 is statistically unprofitable on 15m (Phase 2 analysis: 277 trades, 28.9% WR, -$725 PnL).
# Gate to 1h+ only. Same pattern as S4's _INTRADAY_TFS check.
_SUPPORTED_TFS = {"1h", "4h", "1d"}


@dataclass
class S7State:
    """Tier 3 state for Momentum Breakout (Donchian)."""
    donchian_high: float
    donchian_low: float
    donchian_mid: float

    # Prior bar's Donchian values (for "close > prior Don_High")
    prev_donchian_high: float
    prev_donchian_low: float

    # Breakout flags
    long_breakout: bool
    short_breakout: bool

    # Bias
    htf_bias: str          # "long", "short", "neutral"
    htf_bias_score: float
    rsi_bias: str          # "long", "short", "neutral"


@dataclass
class S7Signal:
    """Output when S7 triggers."""
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
    break_strength: float
    volume_quality: float
    bias_strength: float
    clean_close: float
    htf_confluence: float

    state: S7State

    explain: ExplainPayload = None
    time_stop_bars: int = 6

    since_ts: Optional[datetime] = None
    action_by_ts: Optional[datetime] = None


class S7Evaluator:
    """
    Evaluates S7 - Momentum Breakout (Donchian) v1.0.

    Usage:
        evaluator = S7Evaluator()
        state = evaluator.compute_state(snapshot, prev_snapshot)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, tf)
    """

    def __init__(
        self,
        # Trigger thresholds
        clv_th_long: float = _env_float("S7_CLV_TH_LONG", 0.70),
        clv_th_short: float = _env_float("S7_CLV_TH_SHORT", 0.30),
        volume_z_min: float = _env_float("S7_VOLUME_Z_MIN", 1.5),

        # Stops
        stop_atr_buffer: float = 0.5,

        # Time stop
        time_stop_bars: int = 6,

        # Targets
        tp1_r: float = 0.5,
        tp2_r: float = 1.0,

        # Slippage
        slippage_bps: float = 0.0005,
    ):
        self.clv_th_long = clv_th_long
        self.clv_th_short = clv_th_short
        self.volume_z_min = volume_z_min
        self.stop_atr_buffer = stop_atr_buffer
        self.time_stop_bars = time_stop_bars
        self.tp1_r = tp1_r
        self.tp2_r = tp2_r
        self.slippage_bps = slippage_bps

        logger.info(
            "S7 effective params: clv_long=%.2f clv_short=%.2f vol_z=%.1f",
            self.clv_th_long, self.clv_th_short, self.volume_z_min,
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

    @staticmethod
    def _rsi_bias(snapshot: IndicatorSnapshot) -> str:
        if snapshot.rsi > 50 and snapshot.rsi_slope > 0:
            return "long"
        elif snapshot.rsi < 50 and snapshot.rsi_slope < 0:
            return "short"
        return "neutral"

    def compute_state(
        self,
        snapshot: IndicatorSnapshot,
        prev_snapshot: Optional[IndicatorSnapshot] = None,
    ) -> S7State:
        """
        Compute Tier 3 state.

        Args:
            snapshot: Current indicator snapshot (has donchian_high_20 etc.)
            prev_snapshot: Previous bar's snapshot for prior Donchian values
        """
        don_h = snapshot.donchian_high_20
        don_l = snapshot.donchian_low_20
        don_m = snapshot.donchian_mid_20

        # Previous bar Donchian: use prior snapshot if available,
        # else approximate from OHLCV
        if prev_snapshot is not None:
            prev_don_h = prev_snapshot.donchian_high_20
            prev_don_l = prev_snapshot.donchian_low_20
        else:
            # Compute from candles[-21:-1] (excluding current bar)
            candles = snapshot.ohlcv
            if len(candles) >= 21:
                highs_prev = np.array([c.high for c in candles[-21:-1]])
                lows_prev = np.array([c.low for c in candles[-21:-1]])
                prev_don_h = float(np.max(highs_prev))
                prev_don_l = float(np.min(lows_prev))
            else:
                prev_don_h = don_h
                prev_don_l = don_l

        current = snapshot.ohlcv[-1]
        long_breakout = current.close > prev_don_h
        short_breakout = current.close < prev_don_l

        htf_bias, htf_bias_score = self._htf_bias(snapshot)
        rsi_bias = self._rsi_bias(snapshot)

        return S7State(
            donchian_high=don_h,
            donchian_low=don_l,
            donchian_mid=don_m,
            prev_donchian_high=prev_don_h,
            prev_donchian_low=prev_don_l,
            long_breakout=long_breakout,
            short_breakout=short_breakout,
            htf_bias=htf_bias,
            htf_bias_score=htf_bias_score,
            rsi_bias=rsi_bias,
        )

    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S7State,
        symbol: str,
        timeframe: str,
        is_final: bool = True,
    ) -> Optional[S7Signal]:
        # Canon §1.8
        if not is_final:
            logger.debug("S7 gate:forming_candle %s %s", symbol, timeframe)
            return None

        if timeframe not in _SUPPORTED_TFS:
            logger.debug("S7 gate:unsupported_timeframe %s %s", symbol, timeframe)
            return None

        current = snapshot.ohlcv[-1]
        atr = snapshot.atr

        # Long trigger
        long_trigger = (
            state.long_breakout
            and snapshot.clv >= self.clv_th_long
            and snapshot.volume_z >= self.volume_z_min
            and state.htf_bias != "short"
            and state.rsi_bias != "short"
        )

        # Short trigger
        short_trigger = (
            state.short_breakout
            and snapshot.clv <= self.clv_th_short
            and snapshot.volume_z >= self.volume_z_min
            and state.htf_bias != "long"
            and state.rsi_bias != "long"
        )

        if not long_trigger and not short_trigger:
            logger.debug("S7 gate:no_trigger %s %s break_l=%s break_s=%s clv=%.2f vz=%.2f",
                         symbol, timeframe, state.long_breakout, state.short_breakout,
                         snapshot.clv, snapshot.volume_z)
            return None

        direction = Direction.LONG if long_trigger else Direction.SHORT

        # Entry
        entry_price = current.close
        if direction == Direction.LONG:
            entry_price *= (1 + self.slippage_bps)
        else:
            entry_price *= (1 - self.slippage_bps)

        # Stop
        highs = np.array([c.high for c in snapshot.ohlcv])
        lows = np.array([c.low for c in snapshot.ohlcv])

        if direction == Direction.LONG:
            # Stop at max(Don_Mid, Don_High - 0.5*ATR, swing_low)
            stop_a = state.donchian_mid
            stop_b = state.donchian_high - self.stop_atr_buffer * atr
            pivot_lows = find_pivot_lows(lows[-20:], n_left=1, n_right=0)
            swing_low = max(p[1] for p in pivot_lows[-3:]) if pivot_lows else stop_a
            stop_price = max(stop_a, stop_b, swing_low)
            r_value = entry_price - stop_price
        else:
            stop_a = state.donchian_mid
            stop_b = state.donchian_low + self.stop_atr_buffer * atr
            pivot_highs = find_pivot_highs(highs[-20:], n_left=1, n_right=0)
            swing_high = min(p[1] for p in pivot_highs[-3:]) if pivot_highs else stop_a
            stop_price = min(stop_a, stop_b, swing_high)
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
        if direction == Direction.LONG:
            distance = current.close - state.prev_donchian_high
        else:
            distance = state.prev_donchian_low - current.close
        break_strength = clamp(distance / atr, 0.0, 1.0)

        volume_quality = clamp((snapshot.volume_z - 1.0) / 3.0, 0.0, 1.0)

        # Bias strength: RSI + HTF alignment
        bias_points = 0.0
        if direction == Direction.LONG:
            if state.rsi_bias == "long":
                bias_points += 0.5
            if state.htf_bias == "long":
                bias_points += 0.5
        else:
            if state.rsi_bias == "short":
                bias_points += 0.5
            if state.htf_bias == "short":
                bias_points += 0.5
        bias_strength = clamp(bias_points, 0.0, 1.0)

        if direction == Direction.LONG:
            clean_close = clamp((snapshot.clv - self.clv_th_long) / (1.0 - self.clv_th_long), 0.0, 1.0)
        else:
            clean_close = clamp((self.clv_th_short - snapshot.clv) / self.clv_th_short, 0.0, 1.0)

        htf_confluence = 0.0
        if (direction == Direction.LONG and state.htf_bias == "long") or \
           (direction == Direction.SHORT and state.htf_bias == "short"):
            htf_confluence = clamp(abs(state.htf_bias_score) / 1.5, 0.0, 1.0)
        elif state.htf_bias == "neutral":
            htf_confluence = 0.3

        setup_score = (
            0.25 * break_strength +
            0.25 * volume_quality +
            0.20 * bias_strength +
            0.15 * clean_close +
            0.15 * htf_confluence
        )

        # Explain
        prior_channel = state.prev_donchian_high if direction == Direction.LONG else state.prev_donchian_low
        why_items = [
            f"Donchian {'high' if direction == Direction.LONG else 'low'} breakout: "
            f"close {current.close:.4g} vs prior channel {prior_channel:.4g}",
            f"Break strength: {break_strength:.0%} ({distance / atr:.2f} ATR beyond channel)",
            f"Volume Z-score: {snapshot.volume_z:.2f} (participation confirmed)",
            f"RSI bias: {state.rsi_bias} (RSI={snapshot.rsi:.1f})",
            f"HTF bias: {state.htf_bias} (score {state.htf_bias_score:+.1f})",
            f"Clean close (CLV): {snapshot.clv:.2f}",
        ]
        explain = ExplainPayload(why_items=why_items[:8])

        # Timing
        bar_seconds = _TF_SECONDS.get(timeframe, 3600)
        since_ts = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
        action_by_ts = since_ts + timedelta(seconds=self.time_stop_bars * bar_seconds)

        return S7Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_score=setup_score,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            break_strength=break_strength,
            volume_quality=volume_quality,
            bias_strength=bias_strength,
            clean_close=clean_close,
            htf_confluence=htf_confluence,
            state=state,
            explain=explain,
            time_stop_bars=self.time_stop_bars,
            since_ts=since_ts,
            action_by_ts=action_by_ts,
        )
