"""
S4 - VWAP Reversion v1.0 (Canonical Setup)
============================================

Detect extreme extension from VWAP, then enter a mean-reversion trade
when momentum fades and price begins reverting toward VWAP.

Philosophy: VWAP is the institutional benchmark. When price gets
stretched >2 ATR from VWAP with declining momentum, a reversion
toward the mean is statistically favoured. We grade the extension
magnitude, its percentile rarity, momentum fade quality, and
the reversal candle.

Intraday only: 1m, 5m, 15m, 1h timeframes (VWAP resets daily).

Score weights:
  ExtensionMagnitude    0.30
  ExtremePctile         0.25
  MomentumFadeStrength  0.20
  CleanClose            0.15
  VolumeFade            0.10
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
    percentile_rank,
    clamp,
)
from setup_base import Direction, ExplainPayload


_TF_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

# Intraday timeframes only
_INTRADAY_TFS = {"1m", "5m", "15m", "1h"}


@dataclass
class S4State:
    """Tier 3 state for VWAP Reversion."""
    # VWAP distance
    vwap_distance_atr: float       # (close - VWAP) / ATR; positive = above
    abs_distance_atr: float        # absolute

    # Extension flags
    extended_above: bool           # distance > extension_threshold_atr
    extended_below: bool

    # Extension percentile (from history of abs distances)
    extension_pctile: float        # 0-100

    # Momentum fading
    rsi_slope_fading: bool         # RSI slope reverting toward 50
    price_slope_diverging: bool    # price making new extreme but RSI fading
    momentum_fading: bool          # combined

    # Volume fading
    volume_fading: bool            # volume_z declining from peak

    # Direction
    direction_hint: str            # "long" (revert from below), "short" (revert from above), "none"


@dataclass
class S4Signal:
    """Output when S4 triggers."""
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
    extension_magnitude: float
    extreme_pctile: float
    momentum_fade_strength: float
    clean_close: float
    volume_fade: float

    state: S4State

    explain: ExplainPayload = None
    time_stop_bars: int = 6

    since_ts: Optional[datetime] = None
    action_by_ts: Optional[datetime] = None


class S4Evaluator:
    """
    Evaluates S4 - VWAP Reversion v1.0.

    Intraday only (1m, 5m, 15m, 1h).

    Usage:
        evaluator = S4Evaluator()
        state = evaluator.compute_state(snapshot, vwap_distance_history)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, tf)
    """

    def __init__(
        self,
        # Extension threshold (ATR units)
        extension_threshold_atr: float = _env_float("S4_EXTENSION_TH_ATR", 2.0),

        # Percentile threshold (top 10% = pctile >= 90)
        extension_pctile_min: float = _env_float("S4_EXTENSION_PCTILE_MIN", 90.0),

        # Trigger thresholds
        clv_th: float = _env_float("S4_CLV_TH", 0.65),
        volume_z_min: float = _env_float("S4_VOLUME_Z_MIN", 1.0),

        # Stops (tighter for mean reversion)
        stop_atr_mult: float = 1.0,

        # Time stop
        time_stop_bars: int = 6,

        # Targets: TP1 = VWAP, TP2 = VWAP + 1 SD (opposite side)
        # Fallback to R-based if < 1R
        min_tp_r: float = 1.0,

        # Slippage
        slippage_bps: float = 0.0005,

        # RSI slope threshold for "fading"
        rsi_slope_fade_th: float = _env_float("S4_RSI_SLOPE_FADE_TH", 0.0),
    ):
        self.extension_threshold_atr = extension_threshold_atr
        self.extension_pctile_min = extension_pctile_min
        self.clv_th = clv_th
        self.volume_z_min = volume_z_min
        self.stop_atr_mult = stop_atr_mult
        self.time_stop_bars = time_stop_bars
        self.min_tp_r = min_tp_r
        self.slippage_bps = slippage_bps
        self.rsi_slope_fade_th = rsi_slope_fade_th

        logger.info(
            "S4 effective params: ext_th_atr=%.1f ext_pctile_min=%.0f "
            "clv=%.2f vol_z=%.1f rsi_slope_fade=%.2f",
            self.extension_threshold_atr, self.extension_pctile_min,
            self.clv_th, self.volume_z_min, self.rsi_slope_fade_th,
        )

    def compute_state(
        self,
        snapshot: IndicatorSnapshot,
        vwap_distance_history: list[float],
    ) -> S4State:
        """
        Compute Tier 3 state.

        Args:
            snapshot: Current indicator snapshot (with vwap_distance_atr)
            vwap_distance_history: History of abs(vwap_distance_atr) for percentile
        """
        vwap_dist = snapshot.vwap_distance_atr if snapshot.vwap_distance_atr is not None else 0.0
        abs_dist = abs(vwap_dist)

        extended_above = vwap_dist > self.extension_threshold_atr
        extended_below = vwap_dist < -self.extension_threshold_atr

        # Extension percentile
        extension_pctile = percentile_rank(abs_dist, vwap_distance_history) if vwap_distance_history else 50.0

        # Momentum fading: RSI slope reverting toward 50
        # If price is extended above (bullish), momentum fading = RSI slope < 0 (turning down)
        # If price is extended below (bearish), momentum fading = RSI slope > 0 (turning up)
        rsi_slope_fading = False
        if extended_above and snapshot.rsi_slope <= self.rsi_slope_fade_th:
            rsi_slope_fading = True
        elif extended_below and snapshot.rsi_slope >= -self.rsi_slope_fade_th:
            rsi_slope_fading = True

        # Price-RSI divergence: simplistic check
        # Extended above + RSI < 70 (not confirming the push) = fading
        price_slope_diverging = False
        if extended_above and snapshot.rsi < 70:
            price_slope_diverging = True
        elif extended_below and snapshot.rsi > 30:
            price_slope_diverging = True

        momentum_fading = rsi_slope_fading or price_slope_diverging

        # Volume fading: current volume_z lower than typical extension volume
        volume_fading = snapshot.volume_z < 1.5

        # Direction hint
        if extended_above and momentum_fading:
            direction_hint = "short"  # Revert from above
        elif extended_below and momentum_fading:
            direction_hint = "long"   # Revert from below
        else:
            direction_hint = "none"

        return S4State(
            vwap_distance_atr=vwap_dist,
            abs_distance_atr=abs_dist,
            extended_above=extended_above,
            extended_below=extended_below,
            extension_pctile=extension_pctile,
            rsi_slope_fading=rsi_slope_fading,
            price_slope_diverging=price_slope_diverging,
            momentum_fading=momentum_fading,
            volume_fading=volume_fading,
            direction_hint=direction_hint,
        )

    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S4State,
        symbol: str,
        timeframe: str,
        is_final: bool = True,
    ) -> Optional[S4Signal]:
        # Canon §1.8
        if not is_final:
            logger.debug("S4 gate:forming_candle %s %s", symbol, timeframe)
            return None

        # Intraday only
        if timeframe not in _INTRADAY_TFS:
            return None

        # Need VWAP data
        if snapshot.vwap is None:
            logger.debug("S4 gate:no_vwap %s %s", symbol, timeframe)
            return None

        if state.direction_hint == "none":
            logger.debug("S4 gate:no_extension %s %s dist=%.2f",
                         symbol, timeframe, state.vwap_distance_atr)
            return None

        # Extreme percentile check
        if state.extension_pctile < self.extension_pctile_min:
            logger.debug("S4 gate:pctile %s %s pctile=%.0f th=%.0f",
                         symbol, timeframe, state.extension_pctile, self.extension_pctile_min)
            return None

        if not state.momentum_fading:
            logger.debug("S4 gate:no_fade %s %s", symbol, timeframe)
            return None

        current = snapshot.ohlcv[-1]
        atr = snapshot.atr

        direction = Direction.LONG if state.direction_hint == "long" else Direction.SHORT

        # Reversal close check
        # Long (revert from below): need CLV high (close near bar high = buyers stepping in)
        # Short (revert from above): need CLV low (close near bar low = sellers stepping in)
        if direction == Direction.LONG and snapshot.clv < self.clv_th:
            logger.debug("S4 gate:clv %s %s clv=%.2f", symbol, timeframe, snapshot.clv)
            return None
        if direction == Direction.SHORT and snapshot.clv > (1 - self.clv_th):
            logger.debug("S4 gate:clv %s %s clv=%.2f", symbol, timeframe, snapshot.clv)
            return None

        # Volume check (less aggressive for mean reversion)
        if snapshot.volume_z < self.volume_z_min:
            logger.debug("S4 gate:vol_z %s %s vz=%.2f", symbol, timeframe, snapshot.volume_z)
            return None

        # Entry
        entry_price = current.close
        if direction == Direction.LONG:
            entry_price *= (1 + self.slippage_bps)
        else:
            entry_price *= (1 - self.slippage_bps)

        # Stop: beyond the extension extreme
        if direction == Direction.LONG:
            stop_price = current.low - self.stop_atr_mult * atr
            r_value = entry_price - stop_price
        else:
            stop_price = current.high + self.stop_atr_mult * atr
            r_value = stop_price - entry_price

        min_r = 0.5 * atr
        if r_value < min_r:
            return None

        # Targets: TP1 = VWAP (the mean), TP2 = VWAP ± 1SD (opposite side)
        vwap = snapshot.vwap
        if direction == Direction.LONG:
            tp1_raw = vwap
            tp2_raw = snapshot.vwap_upper_2sd if snapshot.vwap_upper_2sd else vwap
            # If TP too close, fall back to R-based
            if (tp1_raw - entry_price) < self.min_tp_r * r_value:
                tp1_price = entry_price + 0.5 * r_value
            else:
                tp1_price = tp1_raw
            if (tp2_raw - entry_price) < self.min_tp_r * r_value:
                tp2_price = entry_price + 1.0 * r_value
            else:
                tp2_price = tp2_raw
        else:
            tp1_raw = vwap
            tp2_raw = snapshot.vwap_lower_2sd if snapshot.vwap_lower_2sd else vwap
            if (entry_price - tp1_raw) < self.min_tp_r * r_value:
                tp1_price = entry_price - 0.5 * r_value
            else:
                tp1_price = tp1_raw
            if (entry_price - tp2_raw) < self.min_tp_r * r_value:
                tp2_price = entry_price - 1.0 * r_value
            else:
                tp2_price = tp2_raw

        # Score components
        extension_magnitude = clamp(state.abs_distance_atr / 4.0, 0.0, 1.0)
        extreme_pctile_score = clamp((state.extension_pctile - 80) / 20.0, 0.0, 1.0)

        # Momentum fade strength: both indicators fading = stronger
        fade_points = 0.0
        if state.rsi_slope_fading:
            fade_points += 0.5
        if state.price_slope_diverging:
            fade_points += 0.5
        momentum_fade_strength = clamp(fade_points, 0.0, 1.0)

        if direction == Direction.LONG:
            clean_close = clamp((snapshot.clv - self.clv_th) / (1.0 - self.clv_th), 0.0, 1.0)
        else:
            clean_close = clamp(((1 - self.clv_th) - snapshot.clv) / (1 - self.clv_th), 0.0, 1.0)

        volume_fade_score = 0.5 if state.volume_fading else 0.0
        # Extra credit if volume_z is declining but still positive
        if 0 < snapshot.volume_z < 1.5:
            volume_fade_score = 0.8

        setup_score = (
            0.30 * extension_magnitude +
            0.25 * extreme_pctile_score +
            0.20 * momentum_fade_strength +
            0.15 * clean_close +
            0.10 * volume_fade_score
        )

        # Explain
        why_items = [
            f"VWAP distance: {state.vwap_distance_atr:+.2f} ATR (extended {'above' if state.extended_above else 'below'})",
            f"Extension percentile: {state.extension_pctile:.0f}th (top {100 - state.extension_pctile:.0f}%)",
            f"Momentum fading: RSI slope={snapshot.rsi_slope:+.2f}, RSI={snapshot.rsi:.1f}",
            f"Reversal close confirmed (CLV: {snapshot.clv:.2f})",
            f"Volume Z-score: {snapshot.volume_z:.2f} (fade={state.volume_fading})",
            f"Target: VWAP at {vwap:.4g} (mean reversion)",
        ]
        explain = ExplainPayload(why_items=why_items[:8])

        bar_seconds = _TF_SECONDS.get(timeframe, 3600)
        since_ts = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
        action_by_ts = since_ts + timedelta(seconds=self.time_stop_bars * bar_seconds)

        return S4Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_score=setup_score,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            extension_magnitude=extension_magnitude,
            extreme_pctile=extreme_pctile_score,
            momentum_fade_strength=momentum_fade_strength,
            clean_close=clean_close,
            volume_fade=volume_fade_score,
            state=state,
            explain=explain,
            time_stop_bars=self.time_stop_bars,
            since_ts=since_ts,
            action_by_ts=action_by_ts,
        )
