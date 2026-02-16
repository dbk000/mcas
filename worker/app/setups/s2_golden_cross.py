"""
S2 - Golden Cross Anticipation v1.4 (Canonical Setup)
======================================================

Front-run a retail-crowded golden cross by detecting true convergence
of SMA50 → SMA200 before the cross prints, then entering on momentum
confirmation (strong close + volume).

Philosophy: Don't trade the cross; trade the approach state + confirmation impulse.
Golden Cross is one of the most watched retail patterns - we detect the
convergence early and enter on the first momentum confirmation.

Key innovation: ATR-normalised gap thresholds for cross-asset comparability.

v1.4 Changes:
- Added BTC shock filter (skip longs if BTC dumps, skip shorts if BTC pumps)
- Added pre-cross t-1 check (prevent triggering on exact cross bar)
- Updated time stop to 24 hours (slower setup)
- Warm-up period requirement for SMA200
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
class S2State:
    """Tier 3 state for Golden Cross Anticipation."""
    # Convergence detection
    was_wide: bool
    is_narrow: bool
    gap_decreasing: bool
    converging: bool
    
    # Cross-side (anticipation, not post-cross)
    pre_cross_long: bool   # SMA50 < SMA200 (still below, we're early)
    pre_cross_short: bool  # SMA50 > SMA200 (still above, we're early)
    
    # Pre-cross check at t-1 (prevent triggering on exact cross bar)
    pre_cross_long_t1: bool   # Was pre-cross long on previous bar
    pre_cross_short_t1: bool  # Was pre-cross short on previous bar
    
    # Slope gating
    sma50_slope_positive: bool
    sma200_slope_positive: bool
    sma50_slope_negative: bool
    sma200_slope_negative: bool
    
    # Current values
    ma_gap_atr: float
    dec_count: int  # Number of bars where gap decreased in window
    
    # Direction allowed
    long_allowed: bool
    short_allowed: bool
    
    # BTC shock filter state
    btc_shock_up: bool = False    # BTC pumping (skip shorts)
    btc_shock_down: bool = False  # BTC dumping (skip longs)
    
    # For time stop
    time_stop_bars: int = 24


@dataclass
class S2Signal:
    """Output when S2 triggers."""
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
    conv_tight: float
    conv_speed: float
    trend_strength: float
    participation: float
    break_quality: float
    clean_close: float
    
    # State snapshot
    state: S2State


class S2Evaluator:
    """
    Evaluates S2 - Golden Cross Anticipation v1.1 setup.
    
    Usage:
        evaluator = S2Evaluator()
        state = evaluator.compute_state(snapshot, ma_gap_history)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, timeframe)
    """
    
    def __init__(
        self,
        # MA periods
        n_fast: int = 50,
        n_slow: int = 200,
        
        # Convergence window
        n_conv: int = 20,
        conv_down_min_frac: float = 0.60,  # Gap must decrease on >= 60% of bars
        
        # Gap thresholds (ATR-normalised)
        gap_wide_atr: float = 4.0,
        gap_narrow_atr: float = 1.0,
        
        # Slope minimums (ATR-normalised)
        slope50_min: float = 0.0,
        slope200_min: float = 0.0,
        
        # Confirmation thresholds
        clv_th: float = 0.65,
        volume_z_min: float = 1.0,
        
        # Stops
        stop_atr_buffer: float = 0.5,
        fail_fast_buffer: float = 0.1,
        
        # Time stop (24 hours - slower setup)
        time_stop_bars: int = 24,
        
        # Targets
        tp1_r: float = 1.0,
        tp2_r: float = 2.0,
        trail_mult: float = 1.5,
        
        # Slippage
        slippage_bps: float = 0.0005,
    ):
        self.n_fast = n_fast
        self.n_slow = n_slow
        self.n_conv = n_conv
        self.conv_down_min_frac = conv_down_min_frac
        self.gap_wide_atr = gap_wide_atr
        self.gap_narrow_atr = gap_narrow_atr
        self.slope50_min = slope50_min
        self.slope200_min = slope200_min
        self.clv_th = clv_th
        self.volume_z_min = volume_z_min
        self.stop_atr_buffer = stop_atr_buffer
        self.fail_fast_buffer = fail_fast_buffer
        self.time_stop_bars = time_stop_bars
        self.tp1_r = tp1_r
        self.tp2_r = tp2_r
        self.trail_mult = trail_mult
        self.slippage_bps = slippage_bps
    
    def compute_state(
        self,
        snapshot: IndicatorSnapshot,
        ma_gap_history: list[float],
        prev_state: Optional[S2State] = None,
        btc_snapshot: Optional[IndicatorSnapshot] = None,
    ) -> S2State:
        """
        Compute Tier 3 state for Golden Cross Anticipation.
        
        Args:
            snapshot: Current indicator snapshot
            ma_gap_history: Rolling MA gap (ATR-normalised) values for convergence detection
            prev_state: Previous state for t-1 checks
            btc_snapshot: BTC indicator snapshot for shock filter
        """
        current_gap = snapshot.ma_gap_atr
        
        # Was wide: gap n_conv bars ago was >= gap_wide_atr
        if len(ma_gap_history) >= self.n_conv:
            was_wide = ma_gap_history[-self.n_conv] >= self.gap_wide_atr
        else:
            was_wide = False
        
        # Is narrow: current gap <= gap_narrow_atr
        is_narrow = current_gap <= self.gap_narrow_atr
        
        # Gap decreasing: count bars where gap decreased in window
        dec_count = 0
        if len(ma_gap_history) >= self.n_conv:
            window = ma_gap_history[-self.n_conv:]
            for i in range(1, len(window)):
                if window[i] < window[i - 1]:
                    dec_count += 1
        
        gap_decreasing = dec_count >= int(self.conv_down_min_frac * self.n_conv)
        
        # Converging: all three conditions met
        converging = was_wide and is_narrow and gap_decreasing
        
        # Cross-side condition (anticipation)
        pre_cross_long = snapshot.sma_50 < snapshot.sma_200  # Golden cross approaching
        pre_cross_short = snapshot.sma_50 > snapshot.sma_200  # Death cross approaching
        
        # Pre-cross t-1 check: track previous bar's cross state to prevent exact-bar triggers
        if prev_state is not None:
            pre_cross_long_t1 = prev_state.pre_cross_long
            pre_cross_short_t1 = prev_state.pre_cross_short
        else:
            # First bar - assume same as current (conservative)
            pre_cross_long_t1 = pre_cross_long
            pre_cross_short_t1 = pre_cross_short
        
        # Slope gating
        sma50_slope_positive = snapshot.sma50_slope_atr > self.slope50_min
        sma200_slope_positive = snapshot.sma200_slope_atr > self.slope200_min
        sma50_slope_negative = snapshot.sma50_slope_atr < -self.slope50_min
        sma200_slope_negative = snapshot.sma200_slope_atr < -self.slope200_min
        
        # BTC shock filter
        btc_shock_up = False
        btc_shock_down = False
        
        if btc_snapshot is not None:
            # Detect BTC "shock" moves using RSI and volume
            # Shock up: RSI > 70 and volume_z > 2 (BTC pumping hard)
            # Shock down: RSI < 30 and volume_z > 2 (BTC dumping hard)
            btc_rsi = btc_snapshot.rsi
            btc_vol_z = btc_snapshot.volume_z
            
            btc_shock_up = btc_rsi > 70 and btc_vol_z > 2.0
            btc_shock_down = btc_rsi < 30 and btc_vol_z > 2.0
        
        # Direction allowed (now includes BTC filter and t-1 check)
        long_allowed = (
            converging and
            pre_cross_long and
            pre_cross_long_t1 and  # Must have been pre-cross on t-1 too (not exact cross bar)
            sma50_slope_positive and
            sma200_slope_positive and
            not btc_shock_down  # Skip longs if BTC dumping
        )
        
        short_allowed = (
            converging and
            pre_cross_short and
            pre_cross_short_t1 and  # Must have been pre-cross on t-1 too
            sma50_slope_negative and
            sma200_slope_negative and
            not btc_shock_up  # Skip shorts if BTC pumping
        )
        
        return S2State(
            was_wide=was_wide,
            is_narrow=is_narrow,
            gap_decreasing=gap_decreasing,
            converging=converging,
            pre_cross_long=pre_cross_long,
            pre_cross_short=pre_cross_short,
            pre_cross_long_t1=pre_cross_long_t1,
            pre_cross_short_t1=pre_cross_short_t1,
            sma50_slope_positive=sma50_slope_positive,
            sma200_slope_positive=sma200_slope_positive,
            sma50_slope_negative=sma50_slope_negative,
            sma200_slope_negative=sma200_slope_negative,
            ma_gap_atr=current_gap,
            dec_count=dec_count,
            long_allowed=long_allowed,
            short_allowed=short_allowed,
            btc_shock_up=btc_shock_up,
            btc_shock_down=btc_shock_down,
            time_stop_bars=self.time_stop_bars,
        )
    
    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S2State,
        symbol: str,
        timeframe: str,
    ) -> Optional[S2Signal]:
        """
        Evaluate if trigger conditions are met.
        
        Returns S2Signal if triggered, None otherwise.
        """
        # Preconditions
        if not state.converging:
            return None
        
        current = snapshot.ohlcv[-1]
        atr = snapshot.atr
        
        # Long trigger: Close > SMA50 + CLV + VolumeZ
        long_trigger = (
            state.long_allowed and
            current.close > snapshot.sma_50 and
            snapshot.clv >= self.clv_th and
            snapshot.volume_z >= self.volume_z_min
        )
        
        # Short trigger
        short_trigger = (
            state.short_allowed and
            current.close < snapshot.sma_50 and
            snapshot.clv <= (1 - self.clv_th) and
            snapshot.volume_z >= self.volume_z_min
        )
        
        if not long_trigger and not short_trigger:
            return None
        
        direction = Direction.LONG if long_trigger else Direction.SHORT
        
        # Entry price
        entry_price = current.close
        if direction == Direction.LONG:
            entry_price *= (1 + self.slippage_bps)
        else:
            entry_price *= (1 - self.slippage_bps)
        
        # Stop calculation
        # Primary anchor: SMA200 ± buffer
        highs = np.array([c.high for c in snapshot.ohlcv])
        lows = np.array([c.low for c in snapshot.ohlcv])
        
        if direction == Direction.LONG:
            stop_a = snapshot.sma_200 - self.stop_atr_buffer * atr
            
            # Swing candidate
            pivot_lows = find_pivot_lows(lows[-20:], n_left=1, n_right=0)
            if pivot_lows:
                swing_low = max(p[1] for p in pivot_lows[-3:])
            else:
                swing_low = stop_a
            
            stop_price = max(stop_a, swing_low)
            r_value = entry_price - stop_price
        else:
            stop_a = snapshot.sma_200 + self.stop_atr_buffer * atr
            
            pivot_highs = find_pivot_highs(highs[-20:], n_left=1, n_right=0)
            if pivot_highs:
                swing_high = min(p[1] for p in pivot_highs[-3:])
            else:
                swing_high = stop_a
            
            stop_price = min(stop_a, swing_high)
            r_value = stop_price - entry_price
        
        if r_value <= 0:
            return None
        
        # Targets
        if direction == Direction.LONG:
            tp1_price = entry_price + self.tp1_r * r_value
            tp2_price = entry_price + self.tp2_r * r_value
        else:
            tp1_price = entry_price - self.tp1_r * r_value
            tp2_price = entry_price - self.tp2_r * r_value
        
        # Setup score components
        conv_tight = clamp(1.0 - (state.ma_gap_atr / self.gap_narrow_atr), 0.0, 1.0)
        conv_speed = clamp(state.dec_count / self.n_conv, 0.0, 1.0)
        
        # Trend strength (SMA200 slope magnitude)
        if direction == Direction.LONG:
            trend_strength = clamp(snapshot.sma200_slope_atr / 0.05, 0.0, 1.0)
        else:
            trend_strength = clamp(-snapshot.sma200_slope_atr / 0.05, 0.0, 1.0)
        
        participation = clamp((snapshot.volume_z - self.volume_z_min) / 3.0, 0.0, 1.0)
        
        # Break quality
        if direction == Direction.LONG:
            distance = current.close - snapshot.sma_50
        else:
            distance = snapshot.sma_50 - current.close
        break_quality = clamp(distance / atr, 0.0, 1.0)
        
        # Clean close
        if direction == Direction.LONG:
            clean_close = clamp((snapshot.clv - self.clv_th) / (1.0 - self.clv_th), 0.0, 1.0)
        else:
            clean_close = clamp(((1 - self.clv_th) - snapshot.clv) / (1 - self.clv_th), 0.0, 1.0)
        
        # Weighted score
        setup_score = (
            0.30 * conv_tight +
            0.20 * conv_speed +
            0.20 * trend_strength +
            0.15 * participation +
            0.10 * break_quality +
            0.05 * clean_close
        )
        
        return S2Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_score=setup_score,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            conv_tight=conv_tight,
            conv_speed=conv_speed,
            trend_strength=trend_strength,
            participation=participation,
            break_quality=break_quality,
            clean_close=clean_close,
            state=state,
        )
