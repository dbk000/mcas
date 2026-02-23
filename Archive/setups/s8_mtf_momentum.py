"""
S8 - MTF Momentum Pullback v1.0 (Phase 1 Validated)
====================================================

S6's trend-pullback pattern with extreme selectivity via Phase 1 validated
filters. Tested on 30 days of data (3,262 opportunities): ABCDE4/no_TP
filter combo achieves val PF 1.468 with 13.9 trades/day.

Key differences from S6:
1. 1h ONLY - 15m/4h/1d disabled (Filter A: biggest PF impact)
2. Strong EMA slope: |slope| > 0.10 (2x S6's 0.05) (Filter D)
3. Stop width capped at 4% of entry (Filter E4)
4. TP disabled - trail handles all exits (no_TP: PF 1.47 vs 0.93 with TP)
5. RSI alignment + HTF alignment as scoring factors (not hard gates)
6. CLV and volume thresholds match S6 (0.65 / 2.0) — validated baseline

Validated exit config: BE=0, Activation=1.5R, Distance=0.75R

Score weights:
  TrendStrength     0.20
  PullbackQuality   0.20
  RSIAlignment      0.20
  HTFConfluence     0.20
  CleanClose        0.10
  Participation     0.10
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
class S8State:
    """Tier 3 state for MTF Momentum Pullback."""
    # Trend structure
    ema_stacked_long: bool
    ema_stacked_short: bool
    ema20_slope_strong: bool      # |slope| > 0.10 (hard gate)
    ema50_slope_aligned: bool     # slope same direction as trend
    trend_direction: str          # "long", "short", "none"
    trend_strength: float         # composite 0-1

    # Pullback detection (same mechanics as S6)
    touched_ema20: bool
    held_ema50: bool
    pullback_detected: bool
    reclaimed_ema20: bool
    retrace_depth_atr: float

    # HTF alignment (soft gate - scoring factor)
    htf_aligned: bool
    htf_bias: str
    htf_bias_score: float

    # RSI alignment (soft gate - scoring factor)
    rsi_aligned: bool
    rsi_value: float
    rsi_slope: float


@dataclass
class S8Signal:
    """Output when S8 triggers."""
    symbol: str
    timeframe: str
    direction: Direction
    setup_score: float
    entry_price: float
    stop_price: float
    tp1_price: float          # 0.0 = TP disabled (trail handles exits)
    tp2_price: float          # 0.0 = TP disabled
    r_value: float

    # Score components
    trend_strength: float
    pullback_quality: float
    rsi_alignment: float
    htf_confluence: float
    clean_close: float
    participation: float

    state: S8State

    explain: ExplainPayload = None
    time_stop_bars: int = 50    # Long time stop - trail handles exits

    since_ts: Optional[datetime] = None
    action_by_ts: Optional[datetime] = None

    # Recommended exit config for exit_sweep and paper trading
    recommended_exit_config: dict = field(default_factory=lambda: {
        "trail_breakeven_r": 0,
        "trail_activation_r": 1.5,
        "trail_distance_r": 0.75,
    })


class S8Evaluator:
    """
    Evaluates S8 - MTF Momentum Pullback v1.0.

    Phase 1 validated filters applied as hard gates to S6's trend-pullback
    pattern.

    Hard gates: 1h only, strong EMA slope (>0.10), stop width <4%
    Soft gates (scoring): RSI momentum, HTF alignment
    No TP - trail handles all exits.

    Usage:
        evaluator = S8Evaluator()
        state = evaluator.compute_state(snapshot, prev_state)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, tf)
    """

    def __init__(
        self,
        # Slope threshold (raised from S6's 0.05 to validated 0.10)
        slope_min: float = _env_float("S8_SLOPE_MIN", 0.10),

        # CLV threshold (matching S6's validated threshold)
        clv_th: float = _env_float("S8_CLV_TH", 0.65),

        # Volume threshold (matching S6's validated threshold)
        volume_z_min: float = _env_float("S8_VOLUME_Z_MIN", 2.0),

        # Stop parameters
        stop_atr_buffer: float = 0.5,
        max_stop_pct: float = _env_float("S8_MAX_STOP_PCT", 0.04),

        # Time stop (long - trail handles exits)
        time_stop_bars: int = 50,

        # Slippage
        slippage_bps: float = 0.0005,

        # Pullback lookback
        pullback_lookback: int = _env_int("S8_PULLBACK_LOOKBACK", 5),

        # Microcap protection
        min_short_price: float = _env_float("S8_MIN_SHORT_PRICE", 1.00),

        # Reclaim clearance
        min_reclaim_atr: float = _env_float("S8_MIN_RECLAIM_ATR", 0.15),
    ):
        self.slope_min = slope_min
        self.clv_th = clv_th
        self.volume_z_min = volume_z_min
        self.stop_atr_buffer = stop_atr_buffer
        self.max_stop_pct = max_stop_pct
        self.time_stop_bars = time_stop_bars
        self.slippage_bps = slippage_bps
        self.pullback_lookback = pullback_lookback
        self.min_short_price = min_short_price
        self.min_reclaim_atr = min_reclaim_atr

        logger.info(
            "S8 params: slope_min=%.3f clv=%.2f vol_z=%.1f max_stop_pct=%.2f "
            "pullback_lookback=%d min_reclaim_atr=%.3f",
            self.slope_min, self.clv_th, self.volume_z_min,
            self.max_stop_pct, self.pullback_lookback, self.min_reclaim_atr,
        )

    @staticmethod
    def _htf_bias(snapshot: IndicatorSnapshot) -> tuple:
        """Compute HTF bias (same logic as S6)."""
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
        prev_state: Optional[S8State] = None,
    ) -> S8State:
        # Trend structure
        ema_stacked_long = snapshot.ema_20 > snapshot.ema_50
        ema_stacked_short = snapshot.ema_20 < snapshot.ema_50

        # Strong slope gate: |slope| > slope_min (0.10 default, 2x S6)
        ema20_slope_strong = abs(snapshot.ema20_slope_atr) > self.slope_min

        # Both slopes must be aligned with direction
        ema20_slope_pos = snapshot.ema20_slope_atr > 0
        ema50_slope_pos = snapshot.ema50_slope_atr > 0
        ema20_slope_neg = snapshot.ema20_slope_atr < 0
        ema50_slope_neg = snapshot.ema50_slope_atr < 0

        if ema_stacked_long and ema20_slope_pos and ema50_slope_pos:
            trend_direction = "long"
            ema50_slope_aligned = True
        elif ema_stacked_short and ema20_slope_neg and ema50_slope_neg:
            trend_direction = "short"
            ema50_slope_aligned = True
        else:
            trend_direction = "none"
            ema50_slope_aligned = False

        # Trend strength (same formula as S6)
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

        # Pullback detection (same as S6)
        candles = snapshot.ohlcv
        n = min(self.pullback_lookback, len(candles) - 1)
        lookback_candles = candles[-(n + 1):-1]

        touched_ema20 = False
        had_separation = False
        held_ema50 = True
        retrace_depth_atr = 0.0
        atr = snapshot.atr
        min_retrace_atr = 0.3

        if trend_direction == "long":
            for c in lookback_candles:
                if c.low > snapshot.ema_20 + 0.1 * atr:
                    had_separation = True
                if c.low <= snapshot.ema_20:
                    touched_ema20 = True
                    depth = (snapshot.ema_20 - c.low) / atr if atr > 0 else 0
                    retrace_depth_atr = max(retrace_depth_atr, depth)
                if c.low < snapshot.ema_50:
                    held_ema50 = False
        elif trend_direction == "short":
            for c in lookback_candles:
                if c.high < snapshot.ema_20 - 0.1 * atr:
                    had_separation = True
                if c.high >= snapshot.ema_20:
                    touched_ema20 = True
                    depth = (c.high - snapshot.ema_20) / atr if atr > 0 else 0
                    retrace_depth_atr = max(retrace_depth_atr, depth)
                if c.high > snapshot.ema_50:
                    held_ema50 = False

        pullback_detected = (
            touched_ema20
            and held_ema50
            and had_separation
            and retrace_depth_atr >= min_retrace_atr
        )

        # Reclaim: current bar closes back above/below EMA20
        current = candles[-1]
        if trend_direction == "long":
            reclaimed_ema20 = current.close > snapshot.ema_20
        elif trend_direction == "short":
            reclaimed_ema20 = current.close < snapshot.ema_20
        else:
            reclaimed_ema20 = False

        # HTF bias (soft gate - scoring factor)
        htf_bias, htf_bias_score = self._htf_bias(snapshot)
        if trend_direction == "long":
            htf_aligned = htf_bias == "long"
        elif trend_direction == "short":
            htf_aligned = htf_bias == "short"
        else:
            htf_aligned = False

        # RSI alignment (soft gate - scoring factor)
        rsi_value = snapshot.rsi if snapshot.rsi is not None else 50.0
        rsi_slope = snapshot.rsi_slope if snapshot.rsi_slope is not None else 0.0
        if trend_direction == "long":
            rsi_aligned = rsi_value > 50 and rsi_slope > 0
        elif trend_direction == "short":
            rsi_aligned = rsi_value < 50 and rsi_slope < 0
        else:
            rsi_aligned = False

        return S8State(
            ema_stacked_long=ema_stacked_long,
            ema_stacked_short=ema_stacked_short,
            ema20_slope_strong=ema20_slope_strong,
            ema50_slope_aligned=ema50_slope_aligned,
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            touched_ema20=touched_ema20,
            held_ema50=held_ema50,
            pullback_detected=pullback_detected,
            reclaimed_ema20=reclaimed_ema20,
            retrace_depth_atr=retrace_depth_atr,
            htf_aligned=htf_aligned,
            htf_bias=htf_bias,
            htf_bias_score=htf_bias_score,
            rsi_aligned=rsi_aligned,
            rsi_value=rsi_value,
            rsi_slope=rsi_slope,
        )

    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S8State,
        symbol: str,
        timeframe: str,
        is_final: bool = True,
    ) -> Optional[S8Signal]:
        # Canon 1.8: final candles only
        if not is_final:
            return None

        # HARD GATE: 1h only (Filter A - biggest PF impact)
        if timeframe != "1h":
            return None

        # HARD GATE: Must have trend direction
        if state.trend_direction == "none":
            return None

        # HARD GATE: Strong EMA20 slope (Filter D - |slope| > 0.10)
        if not state.ema20_slope_strong:
            return None

        # HARD GATE: Pullback pattern detected
        if not state.pullback_detected:
            return None

        # HARD GATE: EMA20 reclaim
        if not state.reclaimed_ema20:
            return None

        # Reclaim clearance: close must clear EMA20 by min ATR distance
        current = snapshot.ohlcv[-1]
        atr = snapshot.atr
        if atr is None or atr <= 0:
            return None
        if state.trend_direction == "long":
            reclaim_clearance = (current.close - snapshot.ema_20) / atr
        else:
            reclaim_clearance = (snapshot.ema_20 - current.close) / atr
        if reclaim_clearance < self.min_reclaim_atr:
            return None

        # Retrace depth gate
        if state.retrace_depth_atr < 0.3:
            return None

        direction = Direction.LONG if state.trend_direction == "long" else Direction.SHORT

        # Microcap short protection
        if direction == Direction.SHORT and current.close < self.min_short_price:
            return None

        # CLV check (loosened to 0.55)
        if direction == Direction.LONG and snapshot.clv < self.clv_th:
            return None
        if direction == Direction.SHORT and snapshot.clv > (1 - self.clv_th):
            return None

        # Volume check (loosened to 1.0)
        if snapshot.volume_z < self.volume_z_min:
            return None

        # Entry price
        entry_price = current.close
        if direction == Direction.LONG:
            entry_price *= (1 + self.slippage_bps)
        else:
            entry_price *= (1 - self.slippage_bps)

        # Stop: below EMA50 - buffer (same logic as S6)
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

        # HARD GATE: Stop width < 4% (Filter E4)
        stop_pct = abs(stop_price - entry_price) / entry_price if entry_price > 0 else 0
        if stop_pct > self.max_stop_pct:
            return None

        # TP disabled: 0.0 is falsy, exit_checker skips TP checks
        tp1_price = 0.0
        tp2_price = 0.0

        # --- Score components ---

        # Trend strength (0.20 weight)
        trend_strength_score = state.trend_strength

        # Pullback quality (0.20 weight)
        pullback_quality = 0.5
        if state.held_ema50:
            pullback_quality += 0.3
        if state.retrace_depth_atr < 1.0:
            pullback_quality += 0.2
        pullback_quality = clamp(pullback_quality, 0.0, 1.0)

        # RSI alignment (0.20 weight - soft gate contributes to score)
        rsi_alignment_score = 0.0
        if state.rsi_aligned:
            rsi_alignment_score = 0.7
            if direction == Direction.LONG and state.rsi_value > 55:
                rsi_alignment_score += 0.3
            elif direction == Direction.SHORT and state.rsi_value < 45:
                rsi_alignment_score += 0.3
        rsi_alignment_score = clamp(rsi_alignment_score, 0.0, 1.0)

        # HTF confluence (0.20 weight - soft gate contributes to score)
        htf_confluence_score = 0.0
        if state.htf_aligned:
            htf_confluence_score = 0.7
            htf_confluence_score += clamp(
                abs(state.htf_bias_score) / 1.5 * 0.3, 0.0, 0.3
            )
        htf_confluence_score = clamp(htf_confluence_score, 0.0, 1.0)

        # Clean close (0.10 weight)
        if direction == Direction.LONG:
            clean_close = clamp(
                (snapshot.clv - self.clv_th) / (1.0 - self.clv_th), 0.0, 1.0
            )
        else:
            clean_close = clamp(
                ((1 - self.clv_th) - snapshot.clv) / (1 - self.clv_th), 0.0, 1.0
            )

        # Participation (0.10 weight)
        participation = clamp((snapshot.volume_z - 1.0) / 3.0, 0.0, 1.0)

        setup_score = (
            0.20 * trend_strength_score
            + 0.20 * pullback_quality
            + 0.20 * rsi_alignment_score
            + 0.20 * htf_confluence_score
            + 0.10 * clean_close
            + 0.10 * participation
        )

        # Explain payload (Canon 1.7: 3-8 why items)
        why_items = [
            f"S8 MTF Momentum: {'Up' if direction == Direction.LONG else 'Down'}trend on 1h",
            f"Strong EMA20 slope: {snapshot.ema20_slope_atr:+.4f} (>{self.slope_min})",
            f"Pullback to EMA20 (depth {state.retrace_depth_atr:.2f} ATR), held EMA50",
            f"Reclaim confirmed, stop width {stop_pct*100:.1f}% (<{self.max_stop_pct*100:.0f}%)",
            f"RSI: {state.rsi_value:.1f} slope={state.rsi_slope:+.2f} (aligned: {state.rsi_aligned})",
            f"HTF: {state.htf_bias} (aligned: {state.htf_aligned})",
            f"Volume Z: {snapshot.volume_z:.2f}, CLV: {snapshot.clv:.2f}",
        ]
        explain = ExplainPayload(why_items=why_items[:8])

        bar_seconds = _TF_SECONDS.get(timeframe, 3600)
        since_ts = datetime.fromtimestamp(
            current.timestamp / 1000, tz=timezone.utc  # Binance ms epoch
        )
        action_by_ts = since_ts + timedelta(
            seconds=self.time_stop_bars * bar_seconds
        )

        return S8Signal(
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
            rsi_alignment=rsi_alignment_score,
            htf_confluence=htf_confluence_score,
            clean_close=clean_close,
            participation=participation,
            state=state,
            explain=explain,
            time_stop_bars=self.time_stop_bars,
            since_ts=since_ts,
            action_by_ts=action_by_ts,
        )
