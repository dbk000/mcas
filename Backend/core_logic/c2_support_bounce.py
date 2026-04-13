"""
C2 -- Support Bounce v1.0
=========================

Classic support/resistance: buy near horizontal support on volume.
LONG only, 1h timeframe.

Trigger: close within 0.5*ATR of Donchian-20 low + bullish candle + RSI < 40.
Confirmation: volume_z > 0.5.
Stop: donchian_low_20 - 0.5*ATR (below support).
TP1: donchian_mid_20.  TP2: donchian_high_20.
Time stop: 24 bars.
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
class C2State:
    """Tier 3 state for Support Bounce."""
    close: float
    donchian_low: float
    donchian_mid: float
    donchian_high: float
    dist_to_support_atr: float  # (close - donchian_low) / atr
    bullish_candle: bool
    rsi: float
    volume_z: float
    atr: float
    entry_valid: bool


@dataclass
class C2Signal:
    """Tier 4 signal for Support Bounce."""
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
    setup_id: str = "C2"
    rsi: float = 0.0
    donchian_low: float = 0.0
    volume_z: float = 0.0
    score_components: dict = None
    signal_ts: Optional[datetime] = None
    bar_start_ts: Optional[datetime] = None

    def __post_init__(self):
        if self.score_components is None:
            self.score_components = {}
        if self.signal_ts is None:
            raise ValueError("C2Signal.signal_ts must be set at construction")
        if self.bar_start_ts is None:
            raise ValueError("C2Signal.bar_start_ts must be set at construction")


class C2Evaluator(SetupBase[C2State, C2Signal]):
    """Support Bounce — buy near Donchian-20 support on bullish reversal."""

    SUPPORT_PROXIMITY_ATR = 0.5
    RSI_MAX = 40.0
    VOLUME_Z_MIN = 0.5
    STOP_ATR_BELOW_SUPPORT = 0.5
    TIME_STOP_BARS = 24

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="C2",
            name="Support Bounce",
            version="v1.0",
            description="Buy near Donchian-20 support with bullish candle and RSI confirmation. 1h timeframe.",
            setup_type="mean_reversion",
            timeframe_affinity=["1h"],
            typical_hold_bars=12,
            typical_win_rate=0.50,
            typical_r_multiple=1.5,
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
            IndicatorRequirement.RSI,
            IndicatorRequirement.DONCHIAN,
        }

    @property
    def parameters(self) -> list[SetupParameter]:
        return [
            SetupParameter("support_proximity_atr", self.SUPPORT_PROXIMITY_ATR, "Max distance from support in ATR", 0.1, 2.0),
            SetupParameter("rsi_max", self.RSI_MAX, "Max RSI to qualify as not overbought", 20.0, 50.0),
            SetupParameter("volume_z_min", self.VOLUME_Z_MIN, "Min volume z-score", 0.0, 3.0),
            SetupParameter("time_stop_bars", self.TIME_STOP_BARS, "Time stop in bars", 6, 48, "int"),
        ]

    def compute_state(self, snapshot: IndicatorSnapshot, **kwargs) -> C2State:
        candles = snapshot.ohlcv
        close = candles[-1].close
        atr = snapshot.atr
        donchian_low = snapshot.donchian_low_20
        donchian_mid = snapshot.donchian_mid_20
        donchian_high = snapshot.donchian_high_20

        dist_to_support_atr = (close - donchian_low) / atr if atr > 0 else 999.0
        bullish_candle = close > candles[-1].open

        entry_valid = (
            0 <= dist_to_support_atr <= self.SUPPORT_PROXIMITY_ATR
            and bullish_candle
            and snapshot.rsi < self.RSI_MAX
            and snapshot.volume_z >= self.VOLUME_Z_MIN
        )

        return C2State(
            close=close,
            donchian_low=donchian_low,
            donchian_mid=donchian_mid,
            donchian_high=donchian_high,
            dist_to_support_atr=dist_to_support_atr,
            bullish_candle=bullish_candle,
            rsi=snapshot.rsi,
            volume_z=snapshot.volume_z,
            atr=atr,
            entry_valid=entry_valid,
        )

    def evaluate_trigger(
        self, snapshot: IndicatorSnapshot, state: C2State,
        symbol: str, timeframe: str,
    ) -> Optional[C2Signal]:
        if not state.entry_valid:
            return None

        close = state.close
        atr = state.atr
        if atr <= 0 or close <= 0:
            return None

        stop_price = state.donchian_low - self.STOP_ATR_BELOW_SUPPORT * atr
        stop_distance = close - stop_price
        if stop_distance <= 0:
            return None

        tp1_price = state.donchian_mid
        tp2_price = state.donchian_high
        # Ensure TP levels are above entry
        if tp1_price <= close:
            tp1_price = close + 1.0 * atr
        if tp2_price <= close:
            tp2_price = close + 2.0 * atr
        r_value = (tp2_price - close) / stop_distance

        # Score: proximity to support + RSI depth + volume
        proximity_score = clamp(1.0 - state.dist_to_support_atr / self.SUPPORT_PROXIMITY_ATR, 0.0, 1.0)
        rsi_score = clamp((self.RSI_MAX - state.rsi) / self.RSI_MAX, 0.0, 1.0)
        vol_score = clamp(state.volume_z / 2.0, 0.0, 1.0)

        setup_score = 0.40 * proximity_score + 0.35 * rsi_score + 0.25 * vol_score
        score_components = {
            "proximity_score": round(proximity_score, 3),
            "rsi_score": round(rsi_score, 3),
            "vol_score": round(vol_score, 3),
        }

        why_items = [
            f"Price near support — {state.dist_to_support_atr:.2f} ATR from Donchian-20 low ({state.donchian_low:.4f})",
            f"Bullish candle — close above open at support level",
            f"RSI {state.rsi:.1f} — not overbought, room to bounce",
        ]
        if state.volume_z > 1.5:
            why_items.append(f"Volume pickup — z-score {state.volume_z:.1f}")

        explain = ExplainPayload(why_items=why_items[:8])
        if explain.validate():
            return None

        _bar_ts = datetime.fromtimestamp(
            snapshot.ohlcv[-1].timestamp / 1000, tz=timezone.utc
        )

        return C2Signal(
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
            rsi=state.rsi,
            donchian_low=state.donchian_low,
            volume_z=state.volume_z,
            score_components=score_components,
            signal_ts=_bar_ts,
            bar_start_ts=_bar_ts,
        )
