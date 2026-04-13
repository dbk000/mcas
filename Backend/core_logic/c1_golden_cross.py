"""
C1 -- Golden Cross v1.0
=======================

Classic trend-following: SMA50 crosses above SMA200 (Golden Cross) with
volume confirmation. LONG only, 4h timeframe.

Trigger: SMA50 was below SMA200 on the previous bar, now >= SMA200.
Confirmation: volume_z > 1.0 (above-average volume on cross bar).
Stop: entry - 2.0 * ATR.
TP1: entry + 2.0 * ATR (1R).  TP2: entry + 4.0 * ATR (2R).
Time stop: 48 bars (8 days at 4h).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core_logic.setup_base import (
    SetupBase, SetupMetadata, SetupParameter, ExplainPayload,
    IndicatorRequirement, LiquidityTier, Direction,
)
from indicators.indicators import IndicatorSnapshot, clamp


@dataclass
class C1State:
    """Tier 3 state for Golden Cross."""
    sma50: float
    sma200: float
    prev_sma50: float
    prev_sma200: float
    cross_bull: bool        # SMA50 crossed above SMA200 this bar
    close_above_sma200: bool
    volume_z: float
    atr: float
    entry_valid: bool


@dataclass
class C1Signal:
    """Tier 4 signal for Golden Cross."""
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
    time_stop_bars: int = 48
    setup_id: str = "C1"
    sma50: float = 0.0
    sma200: float = 0.0
    volume_z: float = 0.0
    score_components: dict = None
    signal_ts: Optional[datetime] = None
    bar_start_ts: Optional[datetime] = None

    def __post_init__(self):
        if self.score_components is None:
            self.score_components = {}
        if self.signal_ts is None:
            raise ValueError("C1Signal.signal_ts must be set at construction")
        if self.bar_start_ts is None:
            raise ValueError("C1Signal.bar_start_ts must be set at construction")


class C1Evaluator(SetupBase[C1State, C1Signal]):
    """Golden Cross — trend-following on SMA50/200 crossover."""

    VOLUME_Z_MIN = 1.0
    STOP_ATR_MULT = 2.0
    TP1_ATR_MULT = 2.0
    TP2_ATR_MULT = 4.0
    TIME_STOP_BARS = 48

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="C1",
            name="Golden Cross",
            version="v1.0",
            description="Trend following: SMA50 crosses above SMA200 with volume confirmation. 4h timeframe.",
            setup_type="trend_following",
            timeframe_affinity=["4h"],
            typical_hold_bars=24,
            typical_win_rate=0.45,
            typical_r_multiple=2.0,
            min_bars_required=201,
            max_concurrent=10,
            liquidity_tiers=[
                LiquidityTier(name="mega_cap", min_24h_volume_usd=500_000_000, max_position_pct=2.0),
                LiquidityTier(name="large_cap", min_24h_volume_usd=50_000_000, max_position_pct=1.5),
                LiquidityTier(name="mid_cap", min_24h_volume_usd=5_000_000, max_position_pct=1.0),
            ],
            sizing_caps={"max_risk_per_trade_pct": 1.0, "max_notional_pct": 10.0},
        )

    @property
    def required_indicators(self) -> set[IndicatorRequirement]:
        return {
            IndicatorRequirement.OHLCV,
            IndicatorRequirement.ATR,
            IndicatorRequirement.VOLUME_Z,
            IndicatorRequirement.SMA_50,
            IndicatorRequirement.SMA_200,
        }

    @property
    def parameters(self) -> list[SetupParameter]:
        return [
            SetupParameter("volume_z_min", self.VOLUME_Z_MIN, "Minimum volume z-score on cross bar", 0.0, 5.0),
            SetupParameter("stop_atr_mult", self.STOP_ATR_MULT, "Stop distance in ATR multiples", 1.0, 5.0),
            SetupParameter("tp2_atr_mult", self.TP2_ATR_MULT, "TP2 distance in ATR multiples", 2.0, 8.0),
            SetupParameter("time_stop_bars", self.TIME_STOP_BARS, "Time stop in bars", 12, 96, "int"),
        ]

    def compute_state(self, snapshot: IndicatorSnapshot, **kwargs) -> C1State:
        candles = snapshot.ohlcv
        close = candles[-1].close
        sma50 = snapshot.sma_50
        sma200 = snapshot.sma_200
        atr = snapshot.atr

        # Previous bar's SMAs — approximate from slope
        prev_sma50 = sma50 - snapshot.sma50_slope_atr * atr if atr > 0 else sma50
        prev_sma200 = sma200 - snapshot.sma200_slope_atr * atr if atr > 0 else sma200

        cross_bull = (prev_sma50 < prev_sma200) and (sma50 >= sma200)

        entry_valid = (
            cross_bull
            and close > sma200
            and snapshot.volume_z >= self.VOLUME_Z_MIN
        )

        return C1State(
            sma50=sma50,
            sma200=sma200,
            prev_sma50=prev_sma50,
            prev_sma200=prev_sma200,
            cross_bull=cross_bull,
            close_above_sma200=close > sma200,
            volume_z=snapshot.volume_z,
            atr=atr,
            entry_valid=entry_valid,
        )

    def evaluate_trigger(
        self, snapshot: IndicatorSnapshot, state: C1State,
        symbol: str, timeframe: str,
    ) -> Optional[C1Signal]:
        if not state.entry_valid:
            return None

        close = snapshot.ohlcv[-1].close
        atr = state.atr
        if atr <= 0 or close <= 0:
            return None

        stop_distance = self.STOP_ATR_MULT * atr
        stop_price = close - stop_distance
        tp1_price = close + self.TP1_ATR_MULT * atr
        tp2_price = close + self.TP2_ATR_MULT * atr
        r_value = (tp2_price - close) / stop_distance

        # Score: MA gap momentum + volume + slope
        ma_gap_norm = clamp(abs(state.sma50 - state.sma200) / (atr * 5), 0.0, 1.0)
        vol_score = clamp(state.volume_z / 3.0, 0.0, 1.0)
        slope_score = clamp(snapshot.sma50_slope_atr, 0.0, 1.0)

        setup_score = 0.40 * vol_score + 0.35 * slope_score + 0.25 * ma_gap_norm
        score_components = {
            "vol_score": round(vol_score, 3),
            "slope_score": round(slope_score, 3),
            "ma_gap_norm": round(ma_gap_norm, 3),
        }

        why_items = [
            f"Golden Cross — SMA50 ({state.sma50:.4f}) crossed above SMA200 ({state.sma200:.4f})",
            f"Volume confirmation — z-score {state.volume_z:.1f}",
            f"Stop {stop_distance:.4f} below entry ({self.STOP_ATR_MULT}x ATR)",
        ]
        if snapshot.sma50_slope_atr > 0.5:
            why_items.append(f"Strong SMA50 upslope — {snapshot.sma50_slope_atr:.2f} ATR/bar")

        explain = ExplainPayload(why_items=why_items[:8])
        if explain.validate():
            return None

        _bar_ts = datetime.fromtimestamp(
            snapshot.ohlcv[-1].timestamp / 1000, tz=timezone.utc
        )

        return C1Signal(
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
            sma50=state.sma50,
            sma200=state.sma200,
            volume_z=state.volume_z,
            score_components=score_components,
            signal_ts=_bar_ts,
            bar_start_ts=_bar_ts,
        )
