"""
C3 -- Breakout v1.0
===================

Classic breakout: price breaks above resistance after Bollinger compression
with volume surge. LONG only, 1h timeframe.

Trigger: close > previous bar's Donchian-20 high (excluding current bar per
         corrections log) + BB width in bottom quartile of recent 50 bars
         + bullish candle.
Confirmation: volume_z > 2.0.
Stop: prev_donchian_high - 1.0*ATR (below old resistance).
TP1: entry + 1.5*ATR.  TP2: entry + 3.0*ATR.
Time stop: 24 bars.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from core_logic.setup_base import (
    SetupBase, SetupMetadata, SetupParameter, ExplainPayload,
    IndicatorRequirement, LiquidityTier, Direction,
)
from indicators.indicators import IndicatorSnapshot, clamp


@dataclass
class C3State:
    """Tier 3 state for Breakout."""
    close: float
    prev_donchian_high: float   # Donchian high excluding current bar
    bb_width: float
    bb_width_pctile: float      # percentile of current bb_width in last 50 bars
    bullish_candle: bool
    volume_z: float
    atr: float
    entry_valid: bool


@dataclass
class C3Signal:
    """Tier 4 signal for Breakout."""
    symbol: str
    timeframe: str
    direction: Direction
    setup_score: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    r_value: float
    explain: ExplainPayload
    time_stop_bars: int = 24
    setup_id: str = "C3"
    bb_width: float = 0.0
    volume_z: float = 0.0
    prev_resistance: float = 0.0
    score_components: dict = None
    signal_ts: Optional[datetime] = None
    bar_start_ts: Optional[datetime] = None

    def __post_init__(self):
        if self.score_components is None:
            self.score_components = {}
        if self.signal_ts is None:
            raise ValueError("C3Signal.signal_ts must be set at construction")
        if self.bar_start_ts is None:
            raise ValueError("C3Signal.bar_start_ts must be set at construction")


class C3Evaluator(SetupBase[C3State, C3Signal]):
    """Breakout — price breaks Donchian-20 resistance after BB compression."""

    VOLUME_Z_MIN = 2.0
    BB_WIDTH_PCTILE_MAX = 25.0  # BB width must be in bottom 25th percentile
    BB_WIDTH_LOOKBACK = 50
    STOP_ATR_MULT = 1.0
    TP1_ATR_MULT = 1.5
    TP2_ATR_MULT = 3.0
    TIME_STOP_BARS = 24

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="C3",
            name="Breakout",
            version="v1.0",
            description="Breakout above Donchian-20 resistance after Bollinger compression with volume surge. 1h timeframe.",
            setup_type="breakout",
            timeframe_affinity=["1h"],
            typical_hold_bars=12,
            typical_win_rate=0.42,
            typical_r_multiple=2.0,
            min_bars_required=201,
            max_concurrent=10,
            liquidity_tiers=[
                LiquidityTier(name="mega_cap", min_24h_volume_usd=500_000_000, max_position_pct=2.0),
                LiquidityTier(name="large_cap", min_24h_volume_usd=50_000_000, max_position_pct=1.5),
                LiquidityTier(name="mid_cap", min_24h_volume_usd=5_000_000, max_position_pct=1.0),
            ],
            sizing_caps={"max_risk_per_trade_pct": 0.5, "max_notional_pct": 10.0},
        )

    @property
    def required_indicators(self) -> set[IndicatorRequirement]:
        return {
            IndicatorRequirement.OHLCV,
            IndicatorRequirement.ATR,
            IndicatorRequirement.VOLUME_Z,
            IndicatorRequirement.BB_BANDS,
            IndicatorRequirement.DONCHIAN,
        }

    @property
    def parameters(self) -> list[SetupParameter]:
        return [
            SetupParameter("volume_z_min", self.VOLUME_Z_MIN, "Min volume z-score on breakout", 1.0, 5.0),
            SetupParameter("bb_width_pctile_max", self.BB_WIDTH_PCTILE_MAX, "Max BB width percentile (compression)", 10.0, 50.0),
            SetupParameter("stop_atr_mult", self.STOP_ATR_MULT, "Stop below old resistance in ATR", 0.5, 3.0),
            SetupParameter("time_stop_bars", self.TIME_STOP_BARS, "Time stop in bars", 6, 48, "int"),
        ]

    def compute_state(self, snapshot: IndicatorSnapshot, **kwargs) -> C3State:
        candles = snapshot.ohlcv
        close = candles[-1].close
        atr = snapshot.atr

        # Previous bar's Donchian high (exclude current bar — corrections log 2026-03-03)
        if len(candles) >= 22:
            prev_highs = [c.high for c in candles[-(21):-1]]
            prev_donchian_high = max(prev_highs)
        else:
            prev_donchian_high = snapshot.donchian_high_20

        # BB width percentile over lookback
        bb_width = snapshot.bb_width
        if len(candles) >= self.BB_WIDTH_LOOKBACK and snapshot.bb_mb > 0:
            # Approximate historical BB widths from candle ranges
            recent_closes = [c.close for c in candles[-self.BB_WIDTH_LOOKBACK:]]
            if len(recent_closes) >= 20:
                arr = np.array(recent_closes)
                rolling_widths = []
                for i in range(20, len(arr)):
                    window = arr[i - 20:i]
                    std = np.std(window)
                    mean = np.mean(window)
                    if mean > 0:
                        rolling_widths.append(2 * 2 * std / mean)
                if rolling_widths:
                    bb_width_pctile = float(np.percentile(
                        rolling_widths,
                        [50]  # dummy — we want where current sits
                    )[0])
                    # Actual percentile of current width
                    bb_width_pctile = float(
                        np.sum(np.array(rolling_widths) <= bb_width) / len(rolling_widths) * 100
                    )
                else:
                    bb_width_pctile = 50.0
            else:
                bb_width_pctile = 50.0
        else:
            bb_width_pctile = 50.0

        bullish_candle = close > candles[-1].open

        entry_valid = (
            close > prev_donchian_high
            and bb_width_pctile <= self.BB_WIDTH_PCTILE_MAX
            and bullish_candle
            and snapshot.volume_z >= self.VOLUME_Z_MIN
        )

        return C3State(
            close=close,
            prev_donchian_high=prev_donchian_high,
            bb_width=bb_width,
            bb_width_pctile=bb_width_pctile,
            bullish_candle=bullish_candle,
            volume_z=snapshot.volume_z,
            atr=atr,
            entry_valid=entry_valid,
        )

    def evaluate_trigger(
        self, snapshot: IndicatorSnapshot, state: C3State,
        symbol: str, timeframe: str,
    ) -> Optional[C3Signal]:
        if not state.entry_valid:
            return None

        close = state.close
        atr = state.atr
        if atr <= 0 or close <= 0:
            return None

        stop_price = state.prev_donchian_high - self.STOP_ATR_MULT * atr
        stop_distance = close - stop_price
        if stop_distance <= 0:
            return None

        tp1_price = close + self.TP1_ATR_MULT * atr
        tp2_price = close + self.TP2_ATR_MULT * atr
        r_value = (tp2_price - close) / stop_distance

        # Score: volume surge + compression depth + candle body ratio
        vol_score = clamp((state.volume_z - self.VOLUME_Z_MIN) / 3.0, 0.0, 1.0)
        compression_score = clamp(1.0 - state.bb_width_pctile / 50.0, 0.0, 1.0)
        candle = snapshot.ohlcv[-1]
        body = abs(candle.close - candle.open)
        wick = candle.high - candle.low
        body_ratio = clamp(body / wick if wick > 0 else 0.5, 0.0, 1.0)

        setup_score = 0.40 * vol_score + 0.35 * compression_score + 0.25 * body_ratio
        score_components = {
            "vol_score": round(vol_score, 3),
            "compression_score": round(compression_score, 3),
            "body_ratio": round(body_ratio, 3),
        }

        why_items = [
            f"Breakout — close {close:.4f} above resistance {state.prev_donchian_high:.4f}",
            f"Bollinger squeeze — width at {state.bb_width_pctile:.0f}th percentile (compressed)",
            f"Volume surge — z-score {state.volume_z:.1f}",
        ]
        if body_ratio > 0.7:
            why_items.append(f"Strong candle body — {body_ratio:.0%} body-to-range ratio")

        explain = ExplainPayload(why_items=why_items[:8])
        if explain.validate():
            return None

        _bar_ts = datetime.fromtimestamp(
            snapshot.ohlcv[-1].timestamp / 1000, tz=timezone.utc
        )

        return C3Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=Direction.LONG,
            setup_score=setup_score,
            entry_price=close,
            stop_price=round(stop_price, 8),
            tp1_price=round(tp1_price, 8),
            tp2_price=round(tp2_price, 8),
            r_value=round(r_value, 2),
            explain=explain,
            time_stop_bars=self.TIME_STOP_BARS,
            bb_width=state.bb_width,
            volume_z=state.volume_z,
            prev_resistance=state.prev_donchian_high,
            score_components=score_components,
            signal_ts=_bar_ts,
            bar_start_ts=_bar_ts,
        )
