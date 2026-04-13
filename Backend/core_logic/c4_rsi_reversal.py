"""
C4 -- RSI Reversal v1.0
=======================

Classic RSI oversold bounce: buy when RSI < 30 near Bollinger lower band
with bullish confirmation. LONG only, 1h timeframe.

Trigger: RSI < 30 + close within 1.0*ATR of BB lower band + bullish candle.
Confirmation: volume_z > 0.5.
Stop: entry - 1.5*ATR.
TP1: BB midline.  TP2: BB upper band.
Time stop: 18 bars.
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
class C4State:
    """Tier 3 state for RSI Reversal."""
    close: float
    rsi: float
    bb_lb: float
    bb_mb: float
    bb_ub: float
    dist_to_bb_lower_atr: float
    bullish_candle: bool
    volume_z: float
    atr: float
    entry_valid: bool


@dataclass
class C4Signal:
    """Tier 4 signal for RSI Reversal."""
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
    time_stop_bars: int = 18
    setup_id: str = "C4"
    rsi: float = 0.0
    bb_lb: float = 0.0
    volume_z: float = 0.0
    score_components: dict = None
    signal_ts: Optional[datetime] = None
    bar_start_ts: Optional[datetime] = None

    def __post_init__(self):
        if self.score_components is None:
            self.score_components = {}
        if self.signal_ts is None:
            raise ValueError("C4Signal.signal_ts must be set at construction")
        if self.bar_start_ts is None:
            raise ValueError("C4Signal.bar_start_ts must be set at construction")


class C4Evaluator(SetupBase[C4State, C4Signal]):
    """RSI Reversal — oversold bounce near Bollinger lower band."""

    RSI_THRESHOLD = 30.0
    BB_PROXIMITY_ATR = 1.0
    VOLUME_Z_MIN = 0.5
    STOP_ATR_MULT = 1.5
    TIME_STOP_BARS = 18

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="C4",
            name="RSI Reversal",
            version="v1.0",
            description="RSI oversold bounce near Bollinger lower band with bullish confirmation. 1h timeframe.",
            setup_type="mean_reversion",
            timeframe_affinity=["1h"],
            typical_hold_bars=10,
            typical_win_rate=0.52,
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
            IndicatorRequirement.BB_BANDS,
        }

    @property
    def parameters(self) -> list[SetupParameter]:
        return [
            SetupParameter("rsi_threshold", self.RSI_THRESHOLD, "RSI oversold threshold", 15.0, 40.0),
            SetupParameter("bb_proximity_atr", self.BB_PROXIMITY_ATR, "Max distance from BB lower in ATR", 0.5, 2.0),
            SetupParameter("stop_atr_mult", self.STOP_ATR_MULT, "Stop distance in ATR multiples", 0.5, 3.0),
            SetupParameter("time_stop_bars", self.TIME_STOP_BARS, "Time stop in bars", 6, 36, "int"),
        ]

    def compute_state(self, snapshot: IndicatorSnapshot, **kwargs) -> C4State:
        candles = snapshot.ohlcv
        close = candles[-1].close
        atr = snapshot.atr

        dist_to_bb_lower_atr = (close - snapshot.bb_lb) / atr if atr > 0 else 999.0
        bullish_candle = close > candles[-1].open

        entry_valid = (
            snapshot.rsi < self.RSI_THRESHOLD
            and 0 <= dist_to_bb_lower_atr <= self.BB_PROXIMITY_ATR
            and bullish_candle
            and snapshot.volume_z >= self.VOLUME_Z_MIN
        )

        return C4State(
            close=close,
            rsi=snapshot.rsi,
            bb_lb=snapshot.bb_lb,
            bb_mb=snapshot.bb_mb,
            bb_ub=snapshot.bb_ub,
            dist_to_bb_lower_atr=dist_to_bb_lower_atr,
            bullish_candle=bullish_candle,
            volume_z=snapshot.volume_z,
            atr=atr,
            entry_valid=entry_valid,
        )

    def evaluate_trigger(
        self, snapshot: IndicatorSnapshot, state: C4State,
        symbol: str, timeframe: str,
    ) -> Optional[C4Signal]:
        if not state.entry_valid:
            return None

        close = state.close
        atr = state.atr
        if atr <= 0 or close <= 0:
            return None

        stop_distance = self.STOP_ATR_MULT * atr
        stop_price = close - stop_distance
        tp1_price = state.bb_mb
        tp2_price = state.bb_ub

        # Ensure TPs are above entry
        if tp1_price <= close:
            tp1_price = close + 1.0 * atr
        if tp2_price <= close:
            tp2_price = close + 2.0 * atr
        r_value = (tp2_price - close) / stop_distance

        # Score: RSI depth + BB proximity + volume
        rsi_score = clamp((self.RSI_THRESHOLD - state.rsi) / self.RSI_THRESHOLD, 0.0, 1.0)
        bb_score = clamp(1.0 - state.dist_to_bb_lower_atr / self.BB_PROXIMITY_ATR, 0.0, 1.0)
        vol_score = clamp(state.volume_z / 2.0, 0.0, 1.0)

        setup_score = 0.40 * rsi_score + 0.35 * bb_score + 0.25 * vol_score
        score_components = {
            "rsi_score": round(rsi_score, 3),
            "bb_score": round(bb_score, 3),
            "vol_score": round(vol_score, 3),
        }

        why_items = [
            f"RSI oversold — {state.rsi:.1f} (below {self.RSI_THRESHOLD})",
            f"Near Bollinger lower band — {state.dist_to_bb_lower_atr:.2f} ATR from BB low ({state.bb_lb:.4f})",
            f"Bullish reversal candle — close above open at oversold extreme",
        ]
        if state.volume_z > 1.5:
            why_items.append(f"Volume pickup — z-score {state.volume_z:.1f}")
        why_items.append(f"Target BB midline ({state.bb_mb:.4f}) to upper ({state.bb_ub:.4f})")

        explain = ExplainPayload(why_items=why_items[:8])
        if explain.validate():
            return None

        _bar_ts = datetime.fromtimestamp(
            snapshot.ohlcv[-1].timestamp / 1000, tz=timezone.utc
        )

        return C4Signal(
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
            bb_lb=state.bb_lb,
            volume_z=state.volume_z,
            score_components=score_components,
            signal_ts=_bar_ts,
            bar_start_ts=_bar_ts,
        )
