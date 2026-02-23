"""
S5 - Support/Resistance Breakout v1.0 (Canonical Setup)
========================================================

Detect key horizontal support/resistance levels from pivot clusters,
then enter when price breaks through with conviction (strong close + volume).

Philosophy: Horizontal S/R is the most universally watched structure.
We detect levels from clustered pivots, grade them by touch count and
recency, then trade the first clean break with participation.

Edge: Faster detection + better level grading + volume confirmation
= entering the breakout before the crowd recognises it.

Score weights:
  LevelStrength  0.30
  Participation  0.25
  HTFConfluence  0.20
  CleanClose     0.15
  Recency        0.10
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
    percentile_rank,
    clamp,
    find_pivot_lows,
    find_pivot_highs,
)
from setup_base import Direction, ExplainPayload, LiquidityTier


# ── Timeframe helpers ──────────────────────────────────────────────
_TF_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


@dataclass
class SRLevel:
    """A detected support or resistance level."""
    price: float
    touches: int
    strength: float        # composite strength score 0-1
    is_round_number: bool
    last_touch_idx: int    # index of most recent touch in candle array
    side: str              # "resistance" or "support"


@dataclass
class S5State:
    """Tier 3 state for S/R Breakout."""
    detected_levels: list[SRLevel]
    nearest_resistance: Optional[SRLevel]
    nearest_support: Optional[SRLevel]
    htf_bias: str  # "long", "short", "neutral"
    htf_bias_score: float


@dataclass
class S5Signal:
    """Output when S5 triggers."""
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
    level_strength: float
    participation: float
    htf_confluence: float
    clean_close: float
    recency: float

    # State snapshot
    state: S5State

    # Canon 7A
    explain: ExplainPayload = None
    time_stop_bars: int = 8

    # Canon 5E
    since_ts: Optional[datetime] = None
    action_by_ts: Optional[datetime] = None


class S5Evaluator:
    """
    Evaluates S5 - Support/Resistance Breakout v1.0.

    Usage:
        evaluator = S5Evaluator()
        state = evaluator.compute_state(snapshot)
        signal = evaluator.evaluate_trigger(snapshot, state, symbol, timeframe)
    """

    def __init__(
        self,
        # Level detection
        cluster_atr_mult: float = _env_float("S5_CLUSTER_ATR_MULT", 0.5),
        min_touches: int = _env_int("S5_MIN_TOUCHES", 3),
        lookback_bars: int = _env_int("S5_LOOKBACK_BARS", 100),
        round_number_bonus: float = 0.15,

        # Trigger thresholds
        clv_th: float = _env_float("S5_CLV_TH", 0.70),
        volume_z_min: float = _env_float("S5_VOLUME_Z_MIN", 1.5),

        # Stops
        stop_atr_buffer: float = 0.5,

        # Time stop
        time_stop_bars: int = 8,

        # Targets
        tp1_r: float = 0.5,
        tp2_r: float = 1.0,

        # Slippage
        slippage_bps: float = 0.0005,
    ):
        self.cluster_atr_mult = cluster_atr_mult
        self.min_touches = min_touches
        self.lookback_bars = lookback_bars
        self.round_number_bonus = round_number_bonus
        self.clv_th = clv_th
        self.volume_z_min = volume_z_min
        self.stop_atr_buffer = stop_atr_buffer
        self.time_stop_bars = time_stop_bars
        self.tp1_r = tp1_r
        self.tp2_r = tp2_r
        self.slippage_bps = slippage_bps

        logger.info(
            "S5 effective params: cluster_atr=%.2f min_touches=%d "
            "clv=%.2f vol_z=%.1f lookback=%d",
            self.cluster_atr_mult, self.min_touches,
            self.clv_th, self.volume_z_min, self.lookback_bars,
        )

    # ── Level Detection ────────────────────────────────────────────
    def _detect_levels(
        self,
        snapshot: IndicatorSnapshot,
    ) -> list[SRLevel]:
        """
        Cluster pivot highs and lows within cluster_atr_mult × ATR,
        count touches per cluster, and return graded S/R levels.
        """
        candles = snapshot.ohlcv
        n = min(self.lookback_bars, len(candles))
        recent = candles[-n:]
        atr = snapshot.atr
        cluster_dist = self.cluster_atr_mult * atr

        highs = np.array([c.high for c in recent])
        lows = np.array([c.low for c in recent])

        piv_highs = find_pivot_highs(highs, n_left=2, n_right=0)
        piv_lows = find_pivot_lows(lows, n_left=2, n_right=0)

        all_pivots = [(idx, price, "high") for idx, price in piv_highs] + \
                     [(idx, price, "low") for idx, price in piv_lows]

        if not all_pivots:
            return []

        # Sort by price for clustering
        all_pivots.sort(key=lambda x: x[1])

        # Greedy clustering
        clusters: list[list[tuple[int, float, str]]] = []
        current_cluster = [all_pivots[0]]

        for i in range(1, len(all_pivots)):
            if all_pivots[i][1] - current_cluster[0][1] <= cluster_dist:
                current_cluster.append(all_pivots[i])
            else:
                clusters.append(current_cluster)
                current_cluster = [all_pivots[i]]
        clusters.append(current_cluster)

        levels: list[SRLevel] = []
        close = candles[-1].close

        for cluster in clusters:
            touches = len(cluster)
            if touches < self.min_touches:
                continue

            avg_price = np.mean([p[1] for p in cluster])
            last_touch_idx = max(p[0] for p in cluster)

            # Round number bonus
            is_round = self._is_round_number(avg_price)

            # Strength: touch count normalised + round number bonus
            strength = clamp(touches / 8.0, 0.0, 1.0)
            if is_round:
                strength = min(1.0, strength + self.round_number_bonus)

            # Classify as support or resistance relative to current close
            side = "resistance" if avg_price > close else "support"

            levels.append(SRLevel(
                price=float(avg_price),
                touches=touches,
                strength=strength,
                is_round_number=is_round,
                last_touch_idx=last_touch_idx,
                side=side,
            ))

        return levels

    @staticmethod
    def _is_round_number(price: float) -> bool:
        """Check if price is near a psychologically round number."""
        if price <= 0:
            return False
        # Determine round-number granularity based on price magnitude
        if price >= 1000:
            return (price % 100) / price < 0.005
        elif price >= 100:
            return (price % 10) / price < 0.005
        elif price >= 10:
            return (price % 1) / price < 0.005
        elif price >= 1:
            return (price % 0.1) / price < 0.01
        else:
            return (price % 0.01) / price < 0.02 if price > 0 else False

    # ── HTF Bias ───────────────────────────────────────────────────
    @staticmethod
    def _compute_htf_bias(snapshot: IndicatorSnapshot) -> tuple[str, float]:
        """Compute HTF directional bias from available HTF indicators."""
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

    # ── State ──────────────────────────────────────────────────────
    def compute_state(
        self,
        snapshot: IndicatorSnapshot,
    ) -> S5State:
        levels = self._detect_levels(snapshot)
        close = snapshot.ohlcv[-1].close

        # Find nearest resistance (above) and support (below)
        resistances = [lv for lv in levels if lv.side == "resistance"]
        supports = [lv for lv in levels if lv.side == "support"]

        nearest_res = min(resistances, key=lambda lv: lv.price - close) if resistances else None
        nearest_sup = max(supports, key=lambda lv: close - lv.price) if supports else None

        htf_bias, htf_bias_score = self._compute_htf_bias(snapshot)

        return S5State(
            detected_levels=levels,
            nearest_resistance=nearest_res,
            nearest_support=nearest_sup,
            htf_bias=htf_bias,
            htf_bias_score=htf_bias_score,
        )

    # ── Trigger ────────────────────────────────────────────────────
    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S5State,
        symbol: str,
        timeframe: str,
        is_final: bool = True,
    ) -> Optional[S5Signal]:
        # Canon §1.8: anti-lookahead
        if not is_final:
            logger.debug("S5 gate:forming_candle %s %s", symbol, timeframe)
            return None

        current = snapshot.ohlcv[-1]
        atr = snapshot.atr

        # Check resistance breakout (long)
        long_signal = None
        if (state.nearest_resistance is not None
                and current.close > state.nearest_resistance.price
                and snapshot.clv >= self.clv_th
                and snapshot.volume_z >= self.volume_z_min):
            # HTF must be long or neutral (not opposing)
            if state.htf_bias != "short":
                long_signal = self._build_signal(
                    snapshot, state, state.nearest_resistance,
                    Direction.LONG, symbol, timeframe,
                )

        # Check support breakout (short)
        short_signal = None
        if (state.nearest_support is not None
                and current.close < state.nearest_support.price
                and snapshot.clv <= (1 - self.clv_th)
                and snapshot.volume_z >= self.volume_z_min):
            if state.htf_bias != "long":
                short_signal = self._build_signal(
                    snapshot, state, state.nearest_support,
                    Direction.SHORT, symbol, timeframe,
                )

        # Return highest scoring if both triggered
        if long_signal and short_signal:
            return long_signal if long_signal.setup_score >= short_signal.setup_score else short_signal
        return long_signal or short_signal

    def _build_signal(
        self,
        snapshot: IndicatorSnapshot,
        state: S5State,
        broken_level: SRLevel,
        direction: Direction,
        symbol: str,
        timeframe: str,
    ) -> Optional[S5Signal]:
        current = snapshot.ohlcv[-1]
        atr = snapshot.atr

        # Entry
        entry_price = current.close
        if direction == Direction.LONG:
            entry_price *= (1 + self.slippage_bps)
        else:
            entry_price *= (1 - self.slippage_bps)

        # Stop: opposite side of broken level - buffer
        if direction == Direction.LONG:
            stop_price = broken_level.price - self.stop_atr_buffer * atr
            r_value = entry_price - stop_price
        else:
            stop_price = broken_level.price + self.stop_atr_buffer * atr
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
        level_strength = broken_level.strength

        participation = clamp((snapshot.volume_z - 1.0) / 3.0, 0.0, 1.0)

        htf_confluence = 0.0
        if direction == Direction.LONG and state.htf_bias == "long":
            htf_confluence = clamp(abs(state.htf_bias_score) / 1.5, 0.0, 1.0)
        elif direction == Direction.SHORT and state.htf_bias == "short":
            htf_confluence = clamp(abs(state.htf_bias_score) / 1.5, 0.0, 1.0)
        elif state.htf_bias == "neutral":
            htf_confluence = 0.3  # Neutral is acceptable, partial credit

        if direction == Direction.LONG:
            clean_close = clamp((snapshot.clv - self.clv_th) / (1.0 - self.clv_th), 0.0, 1.0)
        else:
            clean_close = clamp(((1 - self.clv_th) - snapshot.clv) / (1 - self.clv_th), 0.0, 1.0)

        # Recency: how recent was the last touch? More recent = higher score
        total_bars = len(snapshot.ohlcv)
        bars_since_touch = total_bars - 1 - broken_level.last_touch_idx
        recency = clamp(1.0 - bars_since_touch / self.lookback_bars, 0.0, 1.0)

        setup_score = (
            0.30 * level_strength +
            0.25 * participation +
            0.20 * htf_confluence +
            0.15 * clean_close +
            0.10 * recency
        )

        # Explainability
        why_items = [
            f"S/R level at {broken_level.price:.4g} broken ({broken_level.touches} touches, strength {broken_level.strength:.0%})",
            f"{'Resistance' if direction == Direction.LONG else 'Support'} breakout confirmed",
            f"Volume Z-score: {snapshot.volume_z:.2f} (participation)",
            f"Clean close (CLV): {snapshot.clv:.2f}",
            f"HTF bias: {state.htf_bias} (score {state.htf_bias_score:+.1f})",
            f"Level recency: {bars_since_touch} bars since last touch",
        ]
        if broken_level.is_round_number:
            why_items.append(f"Round-number level ({broken_level.price:.4g})")
        explain = ExplainPayload(why_items=why_items[:8])

        # Timing
        bar_seconds = _TF_SECONDS.get(timeframe, 3600)
        since_ts = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
        action_by_ts = since_ts + timedelta(seconds=self.time_stop_bars * bar_seconds)

        return S5Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_score=setup_score,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            level_strength=level_strength,
            participation=participation,
            htf_confluence=htf_confluence,
            clean_close=clean_close,
            recency=recency,
            state=state,
            explain=explain,
            time_stop_bars=self.time_stop_bars,
            since_ts=since_ts,
            action_by_ts=action_by_ts,
        )
