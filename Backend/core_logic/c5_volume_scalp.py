"""
C5 -- Volume Scalp v1.0
=======================

Classic volume scalp: fast momentum trades on volume spikes with EMA
alignment and VWAP confirmation. LONG only, 5m timeframe.

Trigger: volume_z > 2.5 + EMA9 > EMA20 + close > VWAP + bullish candle.
Confirmation: rvol > 2.0.
Stop: entry - 1.0*ATR (tight for scalp).
TP1: entry + 1.0*ATR (1R).  TP2: entry + 1.5*ATR (1.5R).
Time stop: 12 bars (1 hour at 5m).
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
class C5State:
    """Tier 3 state for Volume Scalp."""
    close: float
    ema9: float
    ema20: float
    ema_aligned: bool       # EMA9 > EMA20
    above_vwap: bool
    vwap: float
    vwap_distance_atr: float
    bullish_candle: bool
    volume_z: float
    rvol: float
    atr: float
    entry_valid: bool


@dataclass
class C5Signal:
    """Tier 4 signal for Volume Scalp."""
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
    time_stop_bars: int = 12
    setup_id: str = "C5"
    ema9: float = 0.0
    vwap: float = 0.0
    volume_z: float = 0.0
    rvol: float = 0.0
    score_components: dict = None
    signal_ts: Optional[datetime] = None
    bar_start_ts: Optional[datetime] = None

    def __post_init__(self):
        if self.score_components is None:
            self.score_components = {}
        if self.signal_ts is None:
            raise ValueError("C5Signal.signal_ts must be set at construction")
        if self.bar_start_ts is None:
            raise ValueError("C5Signal.bar_start_ts must be set at construction")


class C5Evaluator(SetupBase[C5State, C5Signal]):
    """Volume Scalp — momentum scalp on volume spike with EMA + VWAP alignment."""

    VOLUME_Z_MIN = 2.5
    RVOL_MIN = 2.0
    STOP_ATR_MULT = 1.0
    TP1_ATR_MULT = 1.0
    TP2_ATR_MULT = 1.5
    TIME_STOP_BARS = 12

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="C5",
            name="Volume Scalp",
            version="v1.0",
            description="Volume spike scalp with EMA9/20 alignment and VWAP confirmation. 5m timeframe.",
            setup_type="momentum",
            timeframe_affinity=["5m"],
            typical_hold_bars=6,
            typical_win_rate=0.50,
            typical_r_multiple=1.5,
            min_bars_required=201,
            max_concurrent=5,
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
            IndicatorRequirement.EMA_9,
            IndicatorRequirement.EMA_20,
            IndicatorRequirement.VWAP_IND,
        }

    @property
    def parameters(self) -> list[SetupParameter]:
        return [
            SetupParameter("volume_z_min", self.VOLUME_Z_MIN, "Min volume z-score", 1.5, 5.0),
            SetupParameter("rvol_min", self.RVOL_MIN, "Min relative volume", 1.0, 5.0),
            SetupParameter("stop_atr_mult", self.STOP_ATR_MULT, "Stop distance in ATR multiples", 0.5, 2.0),
            SetupParameter("time_stop_bars", self.TIME_STOP_BARS, "Time stop in bars", 4, 24, "int"),
        ]

    def compute_state(self, snapshot: IndicatorSnapshot, **kwargs) -> C5State:
        candles = snapshot.ohlcv
        close = candles[-1].close
        atr = snapshot.atr

        ema9 = snapshot.ema_9
        ema20 = snapshot.ema_20
        ema_aligned = ema9 > ema20
        above_vwap = close > snapshot.vwap if snapshot.vwap > 0 else False
        bullish_candle = close > candles[-1].open

        entry_valid = (
            ema_aligned
            and above_vwap
            and bullish_candle
            and snapshot.volume_z >= self.VOLUME_Z_MIN
            and snapshot.rvol >= self.RVOL_MIN
        )

        return C5State(
            close=close,
            ema9=ema9,
            ema20=ema20,
            ema_aligned=ema_aligned,
            above_vwap=above_vwap,
            vwap=snapshot.vwap,
            vwap_distance_atr=snapshot.vwap_distance_atr,
            bullish_candle=bullish_candle,
            volume_z=snapshot.volume_z,
            rvol=snapshot.rvol,
            atr=atr,
            entry_valid=entry_valid,
        )

    def evaluate_trigger(
        self, snapshot: IndicatorSnapshot, state: C5State,
        symbol: str, timeframe: str,
    ) -> Optional[C5Signal]:
        if not state.entry_valid:
            return None

        close = state.close
        atr = state.atr
        if atr <= 0 or close <= 0:
            return None

        stop_distance = self.STOP_ATR_MULT * atr
        stop_price = close - stop_distance
        tp1_price = close + self.TP1_ATR_MULT * atr
        tp2_price = close + self.TP2_ATR_MULT * atr
        r_value = (tp2_price - close) / stop_distance

        # Score: volume magnitude + VWAP distance + EMA momentum
        vol_score = clamp((state.volume_z - self.VOLUME_Z_MIN) / 3.0, 0.0, 1.0)
        vwap_score = clamp(state.vwap_distance_atr / 2.0, 0.0, 1.0)
        ema_gap = (state.ema9 - state.ema20) / atr if atr > 0 else 0.0
        ema_score = clamp(ema_gap / 1.0, 0.0, 1.0)

        setup_score = 0.40 * vol_score + 0.30 * ema_score + 0.30 * vwap_score
        score_components = {
            "vol_score": round(vol_score, 3),
            "ema_score": round(ema_score, 3),
            "vwap_score": round(vwap_score, 3),
        }

        why_items = [
            f"Volume spike — z-score {state.volume_z:.1f}, rvol {state.rvol:.1f}x",
            f"EMA aligned — EMA9 ({state.ema9:.4f}) > EMA20 ({state.ema20:.4f})",
            f"Above VWAP ({state.vwap:.4f}) — {state.vwap_distance_atr:.2f} ATR above",
        ]
        if state.rvol > 3.0:
            why_items.append(f"Extreme volume — {state.rvol:.1f}x normal participation")

        explain = ExplainPayload(why_items=why_items[:8])
        if explain.validate():
            return None

        _bar_ts = datetime.fromtimestamp(
            snapshot.ohlcv[-1].timestamp / 1000, tz=timezone.utc
        )

        return C5Signal(
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
            ema9=state.ema9,
            vwap=state.vwap,
            volume_z=state.volume_z,
            rvol=state.rvol,
            score_components=score_components,
            signal_ts=_bar_ts,
            bar_start_ts=_bar_ts,
        )
