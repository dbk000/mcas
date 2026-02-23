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

import os
import logging
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    """Read float from env with fallback to default."""
    val = os.getenv(name)
    return float(val) if val is not None else default


def _env_int(name: str, default: int) -> int:
    """Read int from env with fallback to default."""
    val = os.getenv(name)
    return int(val) if val is not None else default

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
from setup_base import Direction, ExplainPayload, LiquidityTier  # Use shared enums + Canon 7A


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
    time_stop_bars: int = 15


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

    # Canon 7A: explainability payload (3-8 why items)
    explain: ExplainPayload = None

    # Canon 7A: time-stop for action_by_ts derivation
    time_stop_bars: int = 15

    # Canon 5E: timing metadata (signal freshness + action deadline)
    since_ts: Optional[datetime] = None       # bar_start_ts of the trigger candle (UTC)
    action_by_ts: Optional[datetime] = None   # time-stop deadline (UTC)


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
        
        # Convergence window (canonical default: 20, override via S2_N_CONV)
        n_conv: int = _env_int("S2_N_CONV", 20),
        conv_down_min_frac: float = _env_float("S2_CONV_DOWN_MIN_FRAC", 0.60),

        # Gap thresholds (ATR-normalised, canonical defaults: 4.0/1.0)
        gap_wide_atr: float = _env_float("S2_GAP_WIDE_ATR", 4.0),
        gap_narrow_atr: float = _env_float("S2_GAP_NARROW_ATR", 1.0),

        # Slope minimums (ATR-normalised)
        slope50_min: float = 0.0,
        slope200_min: float = 0.0,

        # Confirmation thresholds (canonical defaults: 0.65/1.0)
        clv_th: float = _env_float("S2_CLV_TH", 0.65),
        volume_z_min: float = _env_float("S2_VOLUME_Z_MIN", 1.0),
        
        # Stops
        stop_atr_buffer: float = 1.0,
        fail_fast_buffer: float = 0.1,
        
        # Time stop (15 bars — tightened from 24 per sim analysis)
        time_stop_bars: int = 15,
        
        # Targets
        tp1_r: float = 0.5,
        tp2_r: float = 1.0,
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

        # Log effective parameters at startup for auditability
        logger.info("S2 effective params: n_conv=%d conv_frac=%.2f "
                     "gap_wide=%.1f gap_narrow=%.1f clv=%.2f vol_z=%.2f",
                     self.n_conv, self.conv_down_min_frac,
                     self.gap_wide_atr, self.gap_narrow_atr,
                     self.clv_th, self.volume_z_min)

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
        is_final: bool = True,
    ) -> Optional[S2Signal]:
        """
        Evaluate if trigger conditions are met.

        Returns S2Signal if triggered, None otherwise.
        """
        # Canon §8: Anti-lookahead — only trigger on closed/final candles
        if not is_final:
            logger.debug("S2 gate:forming_candle %s %s (is_final=False)", symbol, timeframe)
            return None

        # Preconditions (with diagnostic logging)
        if not state.converging:
            logger.debug("S2 gate:not_converging %s %s wide=%s narrow=%s dec=%s",
                         symbol, timeframe, state.was_wide, state.is_narrow, state.gap_decreasing)
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
            logger.debug("S2 gate:no_trigger %s %s long_ok=%s short_ok=%s clv=%.2f vol_z=%.2f",
                         symbol, timeframe, state.long_allowed, state.short_allowed,
                         snapshot.clv, snapshot.volume_z)
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
        
        min_r = 0.5 * atr
        if r_value < min_r:
            return None  # Stop too close to entry (inside noise floor)
        
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
        
        # Canon 7A: build explainability payload (3-8 why items)
        why_items = [
            f"SMA50/200 converging: gap narrowed from wide to {state.ma_gap_atr:.2f} ATR",
            f"Convergence speed: gap decreased on {state.dec_count}/{self.n_conv} bars",
            f"Pre-cross {'golden' if direction == Direction.LONG else 'death'} cross anticipated (t-1 verified)",
            f"Volume Z-score: {snapshot.volume_z:.2f} (momentum confirmed)",
            f"SMA200 slope: {snapshot.sma200_slope_atr:+.4f} ATR/bar (trend intact)",
            f"Clean close (CLV): {snapshot.clv:.2f}",
        ]
        if state.btc_shock_up or state.btc_shock_down:
            why_items.append(f"BTC shock filter: {'up' if state.btc_shock_up else 'down'} — accounted for")
        explain = ExplainPayload(why_items=why_items[:8])

        # Canon 5E: compute since_ts and action_by_ts for timing metadata
        # since_ts = bar_start_ts of the trigger candle (UTC)
        # action_by_ts = since_ts + time_stop_bars * bar_duration
        _tf_seconds = {
            "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14400, "1d": 86400,
        }
        bar_seconds = _tf_seconds.get(timeframe, 3600)
        since_ts = datetime.fromtimestamp(
            current.timestamp / 1000, tz=timezone.utc
        )
        action_by_ts = since_ts + timedelta(
            seconds=self.time_stop_bars * bar_seconds
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
            explain=explain,
            time_stop_bars=self.time_stop_bars,
            since_ts=since_ts,
            action_by_ts=action_by_ts,
        )
