"""
S1 - BB Squeeze ON v2.1 (Canonical Setup)
==========================================

Front-run volatility expansion by detecting BB squeeze inside KC,
then entering on confirmed breakout with volume participation.

Philosophy: When Bollinger Bands squeeze inside Keltner Channels,
volatility compression precedes expansion. Enter when squeeze "pops"
with strong close and volume confirmation.

This is a retail-crowded pattern - many traders watch BB squeeze.
We detect it faster and act on confirmed breakouts.

v2.1 Changes:
- Fixed ripe condition: now checks BB width slope on 2 of last 3 bars
- Fixed squeeze reset: requires 2 consecutive bars out of squeeze
- Added fail_fast_bars parameter for early exit logic
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional

# Add parent directory to path for imports when running in Docker
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import (
    IndicatorSnapshot,
    OHLCV,
    percentile_rank,
    clamp,
    find_pivot_lows,
    find_pivot_highs,
)
from setup_base import Direction  # Use shared enum


@dataclass
class S1State:
    """Tier 3 state for BB Squeeze ON."""
    squeeze_on: bool
    squeeze_age: int
    bb_width_pctile: float
    ripe: bool
    box_high: float
    box_low: float
    bias_score: float
    long_allowed: bool
    short_allowed: bool
    
    # For proper reset logic (2 consecutive bars out)
    bars_out_of_squeeze: int = 0
    
    # For fail-fast exit
    fail_fast_bars: int = 3
    
    # BTC correlation filter state
    btc_severe: bool = False
    btc_box_high: Optional[float] = None
    btc_box_low: Optional[float] = None
    btc_breakout_direction: Optional[Direction] = None


@dataclass
class S1Signal:
    """Output when S1 triggers."""
    symbol: str
    timeframe: str
    direction: Direction
    setup_score: float
    entry_price: float  # Suggested entry (next bar open estimate)
    stop_price: float
    tp1_price: float
    tp2_price: float
    r_value: float
    
    # Score components (for debugging/display)
    severity: float
    bias_strength: float
    participation: float
    break_quality: float
    clean_close: float
    
    # State snapshot
    state: S1State


class S1Evaluator:
    """
    Evaluates S1 - BB Squeeze ON v2 setup.
    
    Usage:
        evaluator = S1Evaluator()
        state = evaluator.compute_state(snapshot, bb_width_history)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, timeframe)
    """
    
    def __init__(
        self,
        # BB Width percentile
        bb_width_pctile_trigger: float = 10.0,  # Trigger when <= this
        bb_width_pctile_window: int = 100,  # Rolling window for percentile
        
        # Squeeze age
        squeeze_age_min: int = 3,  # Minimum bars in squeeze before trigger
        
        # Confirmation thresholds
        clv_th_long: float = 0.65,
        clv_th_short: float = 0.35,
        volume_z_min: float = 1.5,
        
        # Bias scoring (BiasMode = trend_proxy)
        bias_long_threshold: float = 0.5,
        bias_short_threshold: float = -0.5,
        
        # Stops
        stop_atr_buffer: float = 0.5,
        fail_fast_buffer: float = 0.1,
        
        # Fail-fast exit (if price re-enters box within N bars)
        fail_fast_bars: int = 3,
        
        # Time stop (T_dead = 4 hours on 1h TF = 4 bars)
        time_stop_bars: int = 4,  # Scaled by timeframe
        
        # Targets
        tp1_r: float = 1.0,
        tp2_r: float = 2.0,
        trail_mult: float = 1.5,
        
        # Slippage (conservative)
        slippage_bps: float = 0.0005,
    ):
        self.bb_width_pctile_trigger = bb_width_pctile_trigger
        self.bb_width_pctile_window = bb_width_pctile_window
        self.squeeze_age_min = squeeze_age_min
        self.clv_th_long = clv_th_long
        self.clv_th_short = clv_th_short
        self.volume_z_min = volume_z_min
        self.bias_long_threshold = bias_long_threshold
        self.bias_short_threshold = bias_short_threshold
        self.stop_atr_buffer = stop_atr_buffer
        self.fail_fast_buffer = fail_fast_buffer
        self.fail_fast_bars = fail_fast_bars
        self.time_stop_bars = time_stop_bars
        self.tp1_r = tp1_r
        self.tp2_r = tp2_r
        self.trail_mult = trail_mult
        self.slippage_bps = slippage_bps
    
    def compute_state(
        self,
        snapshot: IndicatorSnapshot,
        bb_width_history: list[float],
        prev_state: Optional[S1State] = None,
        btc_snapshot: Optional[IndicatorSnapshot] = None,
        btc_bb_width_history: Optional[list[float]] = None,
    ) -> S1State:
        """
        Compute Tier 3 state from indicators.
        
        Args:
            snapshot: Current indicator snapshot
            bb_width_history: Rolling BB width values for percentile calc
            prev_state: Previous state (for squeeze age tracking)
            btc_snapshot: BTC indicator snapshot (for correlation filter)
            btc_bb_width_history: BTC BB width history
        """
        # Squeeze detection: BB inside KC
        squeeze_on = (
            snapshot.bb_ub < snapshot.kc_ub and
            snapshot.bb_lb > snapshot.kc_lb
        )
        
        # Squeeze age with 2-bar reset rule
        if prev_state is None:
            squeeze_age = 1 if squeeze_on else 0
            bars_out_of_squeeze = 0 if squeeze_on else 1
        else:
            if squeeze_on:
                squeeze_age = prev_state.squeeze_age + 1
                bars_out_of_squeeze = 0
            else:
                bars_out_of_squeeze = prev_state.bars_out_of_squeeze + 1
                # Only reset squeeze_age after 2 consecutive bars out of squeeze
                if bars_out_of_squeeze >= 2:
                    squeeze_age = 0
                else:
                    # Still within grace period - keep squeeze_age
                    squeeze_age = prev_state.squeeze_age
        
        # BB Width percentile
        bb_width_pctile = percentile_rank(snapshot.bb_width, bb_width_history[-self.bb_width_pctile_window:])
        
        # Ripe: BB width slope turning up on 2 of last 3 bars (volatility about to expand)
        # This catches the inflection point before breakout
        ripe = False
        if len(bb_width_history) >= 4:
            # Get last 4 BB widths to compute 3 slope comparisons
            recent_widths = bb_width_history[-4:]
            # Count bars where width increased (slope > 0)
            up_count = sum(1 for i in range(1, 4) if recent_widths[i] > recent_widths[i-1])
            # Ripe if at least 2 of last 3 bars had increasing BB width
            ripe = up_count >= 2
        
        # Squeeze box (non-circular: exclude trigger bar)
        if squeeze_age > 0:
            candles = snapshot.ohlcv
            squeeze_start_idx = max(0, len(candles) - squeeze_age - 1)
            box_highs = [c.high for c in candles[squeeze_start_idx:-1]] if squeeze_age > 1 else [candles[-2].high]
            box_lows = [c.low for c in candles[squeeze_start_idx:-1]] if squeeze_age > 1 else [candles[-2].low]
            box_high = max(box_highs) if box_highs else candles[-1].high
            box_low = min(box_lows) if box_lows else candles[-1].low
        else:
            box_high = snapshot.ohlcv[-1].high
            box_low = snapshot.ohlcv[-1].low
        
        # Bias scoring (BiasMode = trend_proxy)
        # TrendVote: +1 if HTF close > HTF SMA50
        trend_vote = 0.0
        if snapshot.htf_close is not None and snapshot.htf_sma50 is not None:
            if snapshot.htf_close > snapshot.htf_sma50:
                trend_vote = 1.0
            elif snapshot.htf_close < snapshot.htf_sma50:
                trend_vote = -1.0
        
        # RSIVote: +1 if RSI > RSI_SMA and RSI_slope > 0
        rsi_vote = 0.0
        if snapshot.rsi > snapshot.rsi_sma and snapshot.rsi_slope > 0:
            rsi_vote = 1.0
        elif snapshot.rsi < snapshot.rsi_sma and snapshot.rsi_slope < 0:
            rsi_vote = -1.0
        
        # MBVote: +0.5 if HTF close > HTF BB midline
        mb_vote = 0.0
        if snapshot.htf_close is not None and snapshot.htf_bb_mb is not None:
            if snapshot.htf_close > snapshot.htf_bb_mb:
                mb_vote = 0.5
            elif snapshot.htf_close < snapshot.htf_bb_mb:
                mb_vote = -0.5
        
        bias_score = trend_vote + rsi_vote + mb_vote
        long_allowed = bias_score >= self.bias_long_threshold
        short_allowed = bias_score <= self.bias_short_threshold
        
        # BTC correlation filter
        btc_severe = False
        btc_box_high = None
        btc_box_low = None
        btc_breakout_direction = None
        
        if btc_snapshot is not None and btc_bb_width_history is not None:
            btc_squeeze_on = (
                btc_snapshot.bb_ub < btc_snapshot.kc_ub and
                btc_snapshot.bb_lb > btc_snapshot.kc_lb
            )
            btc_bb_width_pctile = percentile_rank(
                btc_snapshot.bb_width,
                btc_bb_width_history[-self.bb_width_pctile_window:]
            )
            btc_severe = btc_squeeze_on and btc_bb_width_pctile <= 10
            
            if btc_severe:
                # Compute BTC squeeze box
                btc_candles = btc_snapshot.ohlcv
                btc_box_high = max(c.high for c in btc_candles[-20:])
                btc_box_low = min(c.low for c in btc_candles[-20:])
                
                # Check breakout direction
                btc_close = btc_candles[-1].close
                if btc_close > btc_box_high:
                    btc_breakout_direction = Direction.LONG
                elif btc_close < btc_box_low:
                    btc_breakout_direction = Direction.SHORT
        
        return S1State(
            squeeze_on=squeeze_on,
            squeeze_age=squeeze_age,
            bb_width_pctile=bb_width_pctile,
            ripe=ripe,
            box_high=box_high,
            box_low=box_low,
            bias_score=bias_score,
            long_allowed=long_allowed,
            short_allowed=short_allowed,
            bars_out_of_squeeze=bars_out_of_squeeze,
            fail_fast_bars=self.fail_fast_bars,
            btc_severe=btc_severe,
            btc_box_high=btc_box_high,
            btc_box_low=btc_box_low,
            btc_breakout_direction=btc_breakout_direction,
        )
    
    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S1State,
        symbol: str,
        timeframe: str,
    ) -> Optional[S1Signal]:
        """
        Evaluate if trigger conditions are met.
        
        Returns S1Signal if triggered, None otherwise.
        """
        # Preconditions
        if not state.squeeze_on:
            return None
        if state.bb_width_pctile > self.bb_width_pctile_trigger:
            return None
        if state.squeeze_age < self.squeeze_age_min:
            return None
        if not state.ripe:
            return None
        
        current = snapshot.ohlcv[-1]
        atr = snapshot.atr
        
        # Check long trigger
        long_trigger = (
            state.long_allowed and
            current.close > state.box_high and
            snapshot.clv >= self.clv_th_long and
            snapshot.volume_z >= self.volume_z_min
        )
        
        # Check short trigger
        short_trigger = (
            state.short_allowed and
            current.close < state.box_low and
            snapshot.clv <= self.clv_th_short and
            snapshot.volume_z >= self.volume_z_min
        )
        
        if not long_trigger and not short_trigger:
            return None
        
        # BTC correlation filter
        if state.btc_severe:
            if long_trigger and state.btc_breakout_direction != Direction.LONG:
                return None  # Wait for BTC to break same direction
            if short_trigger and state.btc_breakout_direction != Direction.SHORT:
                return None
        
        direction = Direction.LONG if long_trigger else Direction.SHORT
        
        # Entry price (next bar open + slippage)
        entry_price = current.close  # Approximation; real entry is next open
        if direction == Direction.LONG:
            entry_price *= (1 + self.slippage_bps)
        else:
            entry_price *= (1 - self.slippage_bps)
        
        # Stop calculation
        highs = np.array([c.high for c in snapshot.ohlcv])
        lows = np.array([c.low for c in snapshot.ohlcv])
        
        if direction == Direction.LONG:
            # Find recent swing lows
            pivot_lows = find_pivot_lows(lows[-20:], n_left=1, n_right=0)  # Realtime safe
            if pivot_lows:
                recent_swing_low = max(p[1] for p in pivot_lows[-3:])  # Most recent, highest
            else:
                recent_swing_low = state.box_low - self.stop_atr_buffer * atr
            
            stop_a = state.box_low - self.stop_atr_buffer * atr
            stop_price = max(stop_a, recent_swing_low)
            r_value = entry_price - stop_price
        else:
            # Find recent swing highs
            pivot_highs = find_pivot_highs(highs[-20:], n_left=1, n_right=0)
            if pivot_highs:
                recent_swing_high = min(p[1] for p in pivot_highs[-3:])
            else:
                recent_swing_high = state.box_high + self.stop_atr_buffer * atr
            
            stop_a = state.box_high + self.stop_atr_buffer * atr
            stop_price = min(stop_a, recent_swing_high)
            r_value = stop_price - entry_price
        
        if r_value <= 0:
            return None  # Invalid risk
        
        # Targets
        if direction == Direction.LONG:
            tp1_price = entry_price + self.tp1_r * r_value
            tp2_price = entry_price + self.tp2_r * r_value
        else:
            tp1_price = entry_price - self.tp1_r * r_value
            tp2_price = entry_price - self.tp2_r * r_value
        
        # Setup score components
        severity = 1.0 - state.bb_width_pctile / 100.0
        bias_strength = clamp(abs(state.bias_score) / 2.0, 0.0, 1.0)
        participation = clamp((snapshot.volume_z - 1.0) / 3.0, 0.0, 1.0)
        
        if direction == Direction.LONG:
            distance_beyond = current.close - state.box_high
        else:
            distance_beyond = state.box_low - current.close
        break_quality = clamp(distance_beyond / atr, 0.0, 1.0)
        
        if direction == Direction.LONG:
            clean_close = clamp((snapshot.clv - self.clv_th_long) / (1.0 - self.clv_th_long), 0.0, 1.0)
        else:
            clean_close = clamp((self.clv_th_short - snapshot.clv) / self.clv_th_short, 0.0, 1.0)
        
        # Weighted score
        setup_score = (
            0.30 * severity +
            0.25 * bias_strength +
            0.20 * participation +
            0.15 * break_quality +
            0.10 * clean_close
        )
        
        return S1Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_score=setup_score,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            severity=severity,
            bias_strength=bias_strength,
            participation=participation,
            break_quality=break_quality,
            clean_close=clean_close,
            state=state,
        )
