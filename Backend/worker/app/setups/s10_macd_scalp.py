"""
S10 — MACD Scalp v1.0
======================

Adapted from a profitable equities scalper's methodology for crypto markets.

Core mechanics:
1. MACD(12,26,9) bullish/bearish crossover for entry timing
2. SMA(20) as dynamic support/resistance
3. SMA(200) for major trend direction
4. Flat top breakout detection (consolidation → breakout on volume)
5. Relative volume confirmation (rvol > 2x)
6. 2:1 reward-to-risk exit structure

Timeframe: 5m (scalping)
Typical hold: 6-12 bars (30-60 minutes)
Direction: LONG only (trend-following scalps)
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional

from setup_base import (
    SetupBase, SetupMetadata, SetupParameter, ExplainPayload,
    IndicatorRequirement, LiquidityTier, Direction,
)
from indicators import IndicatorSnapshot, clamp


# ─── State ─────────────────────────────────────────────────────────
@dataclass
class S10State:
    """Tier 3 state for MACD Scalp."""
    # Trend
    above_sma200: bool
    above_sma20: bool
    price_vs_sma20_atr: float  # (close - sma20) / atr

    # MACD
    macd_histogram: float
    macd_prev_histogram: float
    macd_crossover_bull: bool   # histogram crossed from negative to positive
    macd_crossover_bear: bool

    # Flat top breakout
    flat_top_detected: bool
    flat_top_level: float       # resistance level
    flat_top_touches: int       # how many bars touched resistance
    breakout: bool              # current close > flat_top_level

    # Volume
    rvol: float                 # relative volume ratio
    volume_z: float

    # Overall readiness
    entry_valid: bool


# ─── Signal ────────────────────────────────────────────────────────
@dataclass
class S10Signal:
    """Tier 4 signal for MACD Scalp."""
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
    time_stop_bars: int = 12    # 1 hour on 5m

    # Context for payload
    macd_histogram: float = 0.0
    rvol: float = 0.0
    sma_20: float = 0.0
    flat_top_level: float = 0.0
    trend_bias: str = ""
    score_components: dict = None

    def __post_init__(self):
        if self.score_components is None:
            self.score_components = {}


# ─── Evaluator ─────────────────────────────────────────────────────
class S10Evaluator(SetupBase[S10State, S10Signal]):
    """MACD Scalp — momentum breakout on volume for quick in-and-out trades."""

    # ── Tunable thresholds ──
    RVOL_MIN = 2.0              # Minimum relative volume (Alan uses 5x for equities, 2x for crypto)
    FLAT_TOP_LOOKBACK = 7       # Bars to look back for flat top detection
    FLAT_TOP_TOLERANCE_ATR = 0.3  # How tight highs must cluster (fraction of ATR)
    FLAT_TOP_MIN_TOUCHES = 2    # Minimum bars touching resistance
    SMA20_PROXIMITY_ATR = 2.5   # Max distance from SMA20 (in ATR units) — breakouts pull away
    STOP_ATR_MULT = 1.2         # Stop loss distance in ATR
    TP1_R_MULT = 1.5            # TP1 at 1.5R
    TP2_R_MULT = 2.0            # TP2 at 2R (Alan's 2:1 ratio)
    TIME_STOP_BARS = 12         # 1 hour on 5m timeframe

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="S10",
            name="MACD Scalp",
            version="v1.0",
            description="Momentum scalp: MACD crossover + flat top breakout on volume. 5m timeframe, 2:1 R:R.",
            author="MCAS (adapted from Alan McLynn)",
            setup_type="momentum",
            timeframe_affinity=["5m"],
            typical_hold_bars=8,
            typical_win_rate=0.48,
            typical_r_multiple=2.0,
            min_bars_required=201,
            max_concurrent=5,
            liquidity_tiers=[
                LiquidityTier(name="mega_cap", min_24h_volume_usd=500_000_000, max_position_pct=2.0),
                LiquidityTier(name="large_cap", min_24h_volume_usd=50_000_000, max_position_pct=1.5),
                LiquidityTier(name="mid_cap", min_24h_volume_usd=5_000_000, max_position_pct=1.0),
            ],
            sizing_caps={
                "max_risk_per_trade_pct": 0.5,
                "max_notional_pct": 10.0,
            },
        )

    @property
    def required_indicators(self) -> set[IndicatorRequirement]:
        return {
            IndicatorRequirement.OHLCV,
            IndicatorRequirement.ATR,
            IndicatorRequirement.VOLUME_Z,
            IndicatorRequirement.SMA_20,
            IndicatorRequirement.SMA_200,
            IndicatorRequirement.MACD,
        }

    @property
    def parameters(self) -> list[SetupParameter]:
        return [
            SetupParameter("rvol_min", self.RVOL_MIN, "Minimum relative volume ratio", 1.0, 10.0),
            SetupParameter("flat_top_lookback", self.FLAT_TOP_LOOKBACK, "Bars for flat top detection", 3, 20, "int"),
            SetupParameter("flat_top_tolerance_atr", self.FLAT_TOP_TOLERANCE_ATR, "Flat top cluster tolerance (ATR fraction)", 0.1, 1.0),
            SetupParameter("stop_atr_mult", self.STOP_ATR_MULT, "Stop loss distance in ATR multiples", 0.5, 3.0),
            SetupParameter("tp2_r_mult", self.TP2_R_MULT, "TP2 as multiple of risk", 1.5, 4.0),
            SetupParameter("time_stop_bars", self.TIME_STOP_BARS, "Time stop in bars", 4, 48, "int"),
        ]

    def compute_state(self, snapshot: IndicatorSnapshot, **kwargs) -> S10State:
        """Compute Tier 3 state from indicators."""
        candles = snapshot.ohlcv
        close = candles[-1].close
        atr = snapshot.atr

        # ── Trend ──
        above_sma200 = close > snapshot.sma_200
        above_sma20 = close > snapshot.sma_20
        price_vs_sma20_atr = (close - snapshot.sma_20) / atr if atr > 0 else 0.0

        # ── MACD crossover detection ──
        hist = snapshot.macd_histogram
        prev_hist = snapshot.macd_prev_histogram
        macd_crossover_bull = prev_hist <= 0 and hist > 0
        macd_crossover_bear = prev_hist >= 0 and hist < 0

        # ── Flat top breakout detection ──
        # Look back N bars before current for resistance clustering
        lookback_candles = candles[-(self.FLAT_TOP_LOOKBACK + 1):-1]
        if len(lookback_candles) >= 3 and atr > 0:
            recent_highs = [c.high for c in lookback_candles]
            resistance = max(recent_highs)
            tolerance = self.FLAT_TOP_TOLERANCE_ATR * atr
            touches = sum(1 for h in recent_highs if abs(h - resistance) <= tolerance)
            flat_top_detected = touches >= self.FLAT_TOP_MIN_TOUCHES
            flat_top_level = resistance
            # Breakout: current bar closes above resistance with a green candle
            breakout = (close > resistance and candles[-1].close > candles[-1].open)
        else:
            flat_top_detected = False
            flat_top_level = 0.0
            touches = 0
            breakout = False

        # ── Volume ──
        rvol = snapshot.rvol

        # ── Entry validity ──
        entry_valid = (
            above_sma200           # Major trend is up
            and macd_crossover_bull  # MACD just crossed bullish
            and flat_top_detected    # Consolidation pattern present
            and breakout             # Breaking above resistance
            and rvol >= self.RVOL_MIN  # Volume confirmation
            and abs(price_vs_sma20_atr) <= self.SMA20_PROXIMITY_ATR  # Not too far from SMA20
        )

        return S10State(
            above_sma200=above_sma200,
            above_sma20=above_sma20,
            price_vs_sma20_atr=price_vs_sma20_atr,
            macd_histogram=hist,
            macd_prev_histogram=prev_hist,
            macd_crossover_bull=macd_crossover_bull,
            macd_crossover_bear=macd_crossover_bear,
            flat_top_detected=flat_top_detected,
            flat_top_level=flat_top_level,
            flat_top_touches=touches,
            breakout=breakout,
            rvol=rvol,
            volume_z=snapshot.volume_z,
            entry_valid=entry_valid,
        )

    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S10State,
        symbol: str,
        timeframe: str,
    ) -> Optional[S10Signal]:
        """Evaluate if MACD scalp trigger conditions are met."""

        # Stablecoin filter — flat-pegged assets always look like "flat tops"
        _STABLES = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
                     "USDPUSDT", "USTCUSDT", "EURUSDT", "GBPUSDT", "USD1USDT",
                     "XUSDUSDT"}
        if symbol in _STABLES:
            return None

        if not state.entry_valid:
            return None

        candles = snapshot.ohlcv
        close = candles[-1].close
        atr = snapshot.atr

        if atr <= 0 or close <= 0:
            return None

        # ── Entry/exit levels ──
        direction = Direction.LONG
        stop_distance = self.STOP_ATR_MULT * atr
        stop_price = close - stop_distance
        # Also respect SMA20 as stop — use whichever is tighter
        sma20_stop = snapshot.sma_20 - (0.2 * atr)  # Just below SMA20
        if sma20_stop > stop_price:
            stop_price = sma20_stop
            stop_distance = close - stop_price

        if stop_distance <= 0:
            return None

        tp1_price = close + (self.TP1_R_MULT * stop_distance)
        tp2_price = close + (self.TP2_R_MULT * stop_distance)
        r_value = (tp2_price - close) / stop_distance

        # ── Score (0-1) ──
        # Components: MACD strength, volume, flat top quality, trend alignment
        macd_strength = clamp(abs(state.macd_histogram) / (0.001 * close), 0.0, 1.0)  # normalized by price
        vol_score = clamp((state.rvol - self.RVOL_MIN) / (5.0 - self.RVOL_MIN), 0.0, 1.0)
        flat_top_score = clamp(state.flat_top_touches / 5.0, 0.0, 1.0)
        trend_score = 1.0 if state.above_sma200 and state.above_sma20 else 0.5

        setup_score = (
            0.30 * macd_strength
            + 0.25 * vol_score
            + 0.25 * flat_top_score
            + 0.20 * trend_score
        )

        score_components = {
            "macd_strength": round(macd_strength, 3),
            "vol_score": round(vol_score, 3),
            "flat_top_score": round(flat_top_score, 3),
            "trend_score": round(trend_score, 3),
        }

        # ── Explain in scalper-friendly language ──
        why_items = [
            f"MACD bullish crossover — histogram flipped positive ({state.macd_histogram:.6f})",
            f"Flat top breakout at {state.flat_top_level:.4f} — {state.flat_top_touches} touches, new high on volume",
            f"Relative volume {state.rvol:.1f}x normal — above-average interest",
        ]
        if state.above_sma200 and state.above_sma20:
            why_items.append(f"Strong uptrend — price above both 20 SMA ({snapshot.sma_20:.4f}) and 200 SMA ({snapshot.sma_200:.4f})")
        elif state.above_sma200:
            why_items.append(f"Uptrend — price above 200 SMA ({snapshot.sma_200:.4f}), pulling back to 20 SMA")
        why_items.append(f"Risk:Reward {r_value:.1f}:1 — stop {stop_distance:.4f} below entry")
        if state.volume_z > 2.0:
            why_items.append(f"Volume surge — z-score {state.volume_z:.1f} (institutional-level)")

        explain = ExplainPayload(why_items=why_items[:8])
        errors = explain.validate()
        if errors:
            return None

        return S10Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_score=setup_score,
            entry_price=close,
            stop_price=round(stop_price, 8),
            tp1_price=round(tp1_price, 8),
            tp2_price=round(tp2_price, 8),
            r_value=round(r_value, 2),
            explain=explain,
            time_stop_bars=self.TIME_STOP_BARS,
            macd_histogram=state.macd_histogram,
            rvol=state.rvol,
            sma_20=snapshot.sma_20,
            flat_top_level=state.flat_top_level,
            trend_bias="bullish" if state.above_sma200 else "neutral",
            score_components=score_components,
        )
