"""
S11 -- MACD Long Reversion v1.0 (validated locked config)
=========================================================

Deep-oversold pullback long reversion on 1h.

Locked parameters (validated via wide-candidate research → 5-fold purged
walk-forward → untouched holdout):
  - MACD tuple: FAST=3, SLOW=10, SIGNAL=16 (the "fast" tuple)
    Computed locally from snapshot.ohlcv.close series because the shared
    indicator pipeline only provides MACD(12,26,9) via snapshot.macd_*.
  - Entry trigger: bullish histogram crossover on the closed bar
      hist[t-1] <= 0 AND hist[t] > 0
  - Filters (BOTH required):
      close_vs_sma50_atr  = (close - sma_50)  / atr  <  -3.10
      close_vs_sma200_atr = (close - sma_200) / atr  <  -0.05
    Physical reading: price is ≥3.1 ATR below the 50-bar SMA AND the
    200-bar SMA regime is flat-to-down. "Deep oversold pullback in a
    non-uptrend context" — textbook reversion setup.
  - Direction: LONG only
  - Timeframe: 1h only
  - Exit: stop = entry − 5.0×ATR, time_stop = 24 bars, no TPs, no trailing,
    no fail-fast. Simulator/run_executor exits by stop-or-time-stop.
  - Fill convention: live worker signals on closed-bar; backtest semantics
    use NEXT_OPEN with 5 bps slippage, 4 bps taker fee.

Validation provenance (do NOT re-optimize):
  - Source dataset: Backend/scripts/output/macd_wide_candidates_20260409_192456/
  - Wide candidates: 105,892 raw MACD crossovers on 383 S10-universe symbols
    (batch regen_frozen_20260407), period 2026-01-01 → 2026-04-09
  - Walk-forward (5-fold purged, 1-day embargo, full dataset):
      4/5 folds test PF > 1.0
      4/5 folds test daily-PF median > 0.9
      avg test PF = 1.108, min 0.783
      avg test daily-PF median = 1.331
      avg test N/fold = 1,877
  - Holdout (Mar 20 → Apr 9, 21 days, evaluated ONCE):
      N=2,201, PF=1.226, daily-PF median=2.061, win%=52.4%,
      daily sharpe=+2.21, expectancy=+$3.13/trade
  - Holdout exceeds train on every metric (PF, dPFmed, win%, expectancy,
    sharpe) — strong evidence against overfit.
"""

import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from core_logic.setup_base import (
    SetupBase, SetupMetadata, SetupParameter, ExplainPayload,
    IndicatorRequirement, LiquidityTier, Direction,
)
from indicators.indicators import IndicatorSnapshot


# --- MACD local compute (3, 10, 16) from close series -----------------------
def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Simple EMA with alpha = 2/(period+1), seeded with the first close."""
    alpha = 2.0 / (period + 1)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def compute_macd(close: np.ndarray, fast_n: int, slow_n: int, signal_n: int):
    """Return (line, signal_line, histogram) full arrays.

    Matches the compute in macd_wide_candidates.py exactly — the research
    script and this live evaluator must produce bit-identical values on
    the same close series.
    """
    fast = _ema(close, fast_n)
    slow = _ema(close, slow_n)
    line = fast - slow
    sig = _ema(line, signal_n)
    hist = line - sig
    return line, sig, hist


# --- State -------------------------------------------------------------------
@dataclass
class S11State:
    """Tier 3 state for S11 MACD Long Reversion."""
    # MACD operands (computed locally from close series)
    macd_line: float
    macd_signal: float
    macd_histogram: float
    macd_prev_histogram: float
    macd_crossover_bull: bool

    # Filter operands
    sma_50: float
    sma_200: float
    atr: float
    close: float
    close_vs_sma50_atr: float
    close_vs_sma200_atr: float
    sma50_filter_passed: bool
    sma200_filter_passed: bool

    # Overall readiness
    entry_valid: bool


# --- Signal ------------------------------------------------------------------
@dataclass
class S11Signal:
    """Tier 4 signal for S11."""
    symbol: str
    timeframe: str
    direction: Direction
    setup_score: float
    entry_price: float
    stop_price: float
    r_value: float
    explain: ExplainPayload

    # S11 has no TPs — kept Optional so the payload writes JSON null, which
    # the simulator/exit_checker handle by not arming TP1/TP2.
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None

    time_stop_bars: int = 24

    setup_id: str = "S11"

    # Context for payload / trace
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    macd_prev_histogram: float = 0.0
    sma_50: float = 0.0
    sma_200: float = 0.0
    close_vs_sma50_atr: float = 0.0
    close_vs_sma200_atr: float = 0.0
    atr_at_signal: float = 0.0
    score_components: dict = field(default_factory=dict)

    # Timing (every signal carries its bar timestamp — canon)
    signal_ts: Optional[datetime] = None
    bar_start_ts: Optional[datetime] = None

    def __post_init__(self):
        if self.signal_ts is None:
            raise ValueError("S11Signal.signal_ts must be set at construction — evaluator bug")
        if self.bar_start_ts is None:
            raise ValueError("S11Signal.bar_start_ts must be set at construction — evaluator bug")


# --- Evaluator ---------------------------------------------------------------
class S11Evaluator(SetupBase[S11State, S11Signal]):
    """MACD Long Reversion — deep-oversold pullback filter on 1h."""

    # -- LOCKED parameters --
    MACD_FAST = 3
    MACD_SLOW = 10
    MACD_SIGNAL = 16

    # Filter thresholds (LOCKED from holdout validation)
    SMA50_FILTER_THRESHOLD = -3.10   # close_vs_sma50_atr < this
    SMA200_FILTER_THRESHOLD = -0.05  # close_vs_sma200_atr < this

    # Exit (LOCKED)
    STOP_ATR_MULT = 5.0
    TIME_STOP_BARS = 24

    MIN_BARS_REQUIRED = 201  # SMA200 warmup minimum (+1 for prev-bar lookback)

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="S11",
            name="MACD Long Reversion",
            version="v1.0",
            description=(
                "Deep-oversold pullback long reversion. "
                "Fast MACD (3,10,16) bullish crossover on a closed 1h bar, "
                "firing only when price is ≥3.1 ATR below SMA50 AND below "
                "SMA200. Validated via 5-fold purged walk-forward + "
                "untouched Mar 20→Apr 9 holdout (holdout PF=1.226, "
                "daily-PF median=2.061)."
            ),
            author="MCAS",
            setup_type="mean_reversion",
            timeframe_affinity=["1h"],
            typical_hold_bars=12,
            typical_win_rate=0.52,
            typical_r_multiple=1.2,
            min_bars_required=self.MIN_BARS_REQUIRED,
            max_concurrent=3,
            liquidity_tiers=[
                LiquidityTier(name="mega_cap",  min_24h_volume_usd=500_000_000, max_position_pct=3.0),
                LiquidityTier(name="large_cap", min_24h_volume_usd=50_000_000,  max_position_pct=2.0),
                LiquidityTier(name="mid_cap",   min_24h_volume_usd=5_000_000,   max_position_pct=1.0),
            ],
            sizing_caps={
                "max_risk_per_trade_pct": 0.5,
                "max_notional_pct": 5.0,
            },
        )

    @property
    def required_indicators(self) -> set[IndicatorRequirement]:
        # OHLCV for local MACD(3,10,16); ATR / SMA_50 / SMA_200 come from
        # the shared snapshot. Do NOT declare MACD here — snapshot.macd_*
        # is the wrong tuple (12,26,9).
        return {
            IndicatorRequirement.OHLCV,
            IndicatorRequirement.ATR,
            IndicatorRequirement.SMA_50,
            IndicatorRequirement.SMA_200,
        }

    @property
    def parameters(self) -> list[SetupParameter]:
        return [
            SetupParameter("macd_fast", self.MACD_FAST, "MACD fast EMA period", 2, 20, "int"),
            SetupParameter("macd_slow", self.MACD_SLOW, "MACD slow EMA period", 5, 50, "int"),
            SetupParameter("macd_signal", self.MACD_SIGNAL, "MACD signal EMA period", 5, 50, "int"),
            SetupParameter("sma50_filter_threshold",  self.SMA50_FILTER_THRESHOLD,
                           "close_vs_sma50_atr upper bound (filter fires when value < threshold)", -10.0, 0.0),
            SetupParameter("sma200_filter_threshold", self.SMA200_FILTER_THRESHOLD,
                           "close_vs_sma200_atr upper bound",                                     -10.0, 5.0),
            SetupParameter("stop_atr_mult",  self.STOP_ATR_MULT,  "Stop distance in ATR multiples", 0.5, 10.0),
            SetupParameter("time_stop_bars", self.TIME_STOP_BARS, "Time stop in bars",              4, 96, "int"),
        ]

    def compute_state(self, snapshot: IndicatorSnapshot, **kwargs) -> S11State:
        """Compute S11 state with locally-derived MACD(3,10,16)."""
        candles = snapshot.ohlcv

        # Insufficient history → return empty state
        if not candles or len(candles) < self.MIN_BARS_REQUIRED:
            return S11State(
                macd_line=0.0, macd_signal=0.0, macd_histogram=0.0,
                macd_prev_histogram=0.0, macd_crossover_bull=False,
                sma_50=0.0, sma_200=0.0, atr=0.0, close=0.0,
                close_vs_sma50_atr=0.0, close_vs_sma200_atr=0.0,
                sma50_filter_passed=False, sma200_filter_passed=False,
                entry_valid=False,
            )

        close_arr = np.array([c.close for c in candles], dtype=np.float64)
        line, sig, hist = compute_macd(
            close_arr, self.MACD_FAST, self.MACD_SLOW, self.MACD_SIGNAL,
        )

        macd_line = float(line[-1])
        macd_signal_v = float(sig[-1])
        macd_histogram = float(hist[-1])
        macd_prev_histogram = float(hist[-2])
        macd_crossover_bull = (macd_prev_histogram <= 0) and (macd_histogram > 0)

        close = float(candles[-1].close)
        atr = float(snapshot.atr)
        sma_50 = float(snapshot.sma_50)
        sma_200 = float(snapshot.sma_200)

        close_vs_sma50_atr  = ((close - sma_50)  / atr) if atr > 0 else 0.0
        close_vs_sma200_atr = ((close - sma_200) / atr) if atr > 0 else 0.0

        sma50_filter_passed  = close_vs_sma50_atr  < self.SMA50_FILTER_THRESHOLD
        sma200_filter_passed = close_vs_sma200_atr < self.SMA200_FILTER_THRESHOLD

        entry_valid = (
            atr > 0
            and close > 0
            and sma_50 > 0
            and sma_200 > 0
            and macd_crossover_bull
            and sma50_filter_passed
            and sma200_filter_passed
        )

        return S11State(
            macd_line=macd_line,
            macd_signal=macd_signal_v,
            macd_histogram=macd_histogram,
            macd_prev_histogram=macd_prev_histogram,
            macd_crossover_bull=macd_crossover_bull,
            sma_50=sma_50,
            sma_200=sma_200,
            atr=atr,
            close=close,
            close_vs_sma50_atr=close_vs_sma50_atr,
            close_vs_sma200_atr=close_vs_sma200_atr,
            sma50_filter_passed=sma50_filter_passed,
            sma200_filter_passed=sma200_filter_passed,
            entry_valid=entry_valid,
        )

    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: S11State,
        symbol: str,
        timeframe: str,
    ) -> Optional[S11Signal]:
        # Stablecoin filter — flat-pegged assets don't revert
        _STABLES = {
            "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
            "USDPUSDT", "USTCUSDT", "EURUSDT", "GBPUSDT", "USD1USDT",
            "XUSDUSDT",
        }
        if symbol in _STABLES:
            return None

        if not state.entry_valid:
            return None

        close = state.close
        atr = state.atr
        stop_distance = self.STOP_ATR_MULT * atr
        stop_price = close - stop_distance
        if stop_distance <= 0 or stop_price <= 0:
            return None

        # Fixed setup score — S11 is a binary filter with no per-signal grading
        setup_score = 1.0
        score_components = {"binary": 1.0}

        why_items = [
            f"Fast MACD ({self.MACD_FAST},{self.MACD_SLOW},{self.MACD_SIGNAL}) "
            f"bullish crossover on closed 1h bar "
            f"(hist {state.macd_prev_histogram:.6f} → {state.macd_histogram:.6f})",
            f"Price {abs(state.close_vs_sma50_atr):.2f} ATR below SMA50 "
            f"(filter threshold: {abs(self.SMA50_FILTER_THRESHOLD):.2f}) "
            f"— deep oversold pullback",
            f"Price {state.close_vs_sma200_atr:+.2f} ATR vs SMA200 — "
            f"below long-term trend, reversion context confirmed",
            f"Stop {self.STOP_ATR_MULT:.1f}×ATR below entry ({stop_distance:.6f}), "
            f"{self.TIME_STOP_BARS}-bar time stop, no TPs (wide-stop + time-exit)",
        ]
        explain = ExplainPayload(why_items=why_items[:8])
        if explain.validate():
            return None

        # Bar timestamp from the last candle (Binance ms epoch → UTC)
        _bar_ts = datetime.fromtimestamp(
            snapshot.ohlcv[-1].timestamp / 1000, tz=timezone.utc,
        )

        return S11Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=Direction.LONG,
            setup_score=setup_score,
            entry_price=close,
            stop_price=round(stop_price, 8),
            tp1_price=None,  # no TPs — stop-or-time-stop exit only
            tp2_price=None,
            r_value=1.0,     # stop_distance = 1R by construction
            explain=explain,
            time_stop_bars=self.TIME_STOP_BARS,
            macd_line=state.macd_line,
            macd_signal=state.macd_signal,
            macd_histogram=state.macd_histogram,
            macd_prev_histogram=state.macd_prev_histogram,
            sma_50=state.sma_50,
            sma_200=state.sma_200,
            close_vs_sma50_atr=state.close_vs_sma50_atr,
            close_vs_sma200_atr=state.close_vs_sma200_atr,
            atr_at_signal=state.atr,
            score_components=score_components,
            signal_ts=_bar_ts,
            bar_start_ts=_bar_ts,
        )
