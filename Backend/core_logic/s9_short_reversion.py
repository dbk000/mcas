"""
S9 - Short Reversion v1.0
==========================

Cross-sectional mean-reversion short strategy.

Signal:  ret_4h (4-hour return, cross-sectional rank)
Entry:   Short top decile (top 10% of 4h gainers)
Hold:    48 hours (time-stop exit)
Filter:  Market breadth > 0.3 (fraction of symbols with positive 24h return)

Validated on 180-day dataset:
  Walk-forward: 4/5 folds profitable with breadth filter
  Holdout PF: 2.56 at 18 bps cost, WR 65%
  DSR: p=0.961 (PASSED)
  Cost-robust: PF > 2.3 even at 46 bps worst-case

This is a CROSS-SECTIONAL strategy. Unlike S1-S8 which evaluate one symbol
at a time, S9 must see ALL symbols simultaneously to rank them. Production
evaluation happens via periodic cross-sectional scan in signal_worker, not
per-symbol evaluate_trigger().

Parameters are LOCKED -- do not modify without full re-validation.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

from core_logic.setup_base import (
    SetupBase, SetupMetadata, SetupParameter, IndicatorRequirement,
    Direction, ExplainPayload, LiquidityTier,
)

logger = logging.getLogger(__name__)


@dataclass
class S9State:
    """Cross-sectional state computed once per 1h bar for all symbols."""
    breadth: float              # Fraction of symbols with positive 24h return
    breadth_passes: bool        # breadth > 0.3
    universe_size: int          # Number of symbols with sufficient data
    top_decile_threshold: float # ret_4h value at 90th percentile
    top_decile_count: int       # Number of symbols in top decile


@dataclass
class S9Signal:
    """Output when S9 triggers for a specific symbol."""
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
    rank_score: float           # Position within top decile (1.0 = highest)
    breadth_score: float        # How far breadth is above threshold
    magnitude_score: float      # How large the 4h return is

    # Cross-sectional context
    ret_4h: float               # This symbol's 4h return
    ret_24h: float              # This symbol's 24h return
    rank: int                   # Rank in cross-sectional ordering (1 = highest ret_4h)
    universe_size: int          # Total symbols ranked
    decile_size: int            # Number of symbols in top decile
    breadth: float              # Market breadth at signal time

    # State snapshot
    state: S9State

    # Identity
    setup_id: str = "S9"

    # Explainability (3-8 why items)
    explain: ExplainPayload = None

    # Time-stop: 48 bars (48h on 1h timeframe)
    time_stop_bars: int = 48

    # Timing metadata (signal_ts/bar_start_ts: never None — evaluator must set)
    signal_ts: Optional[datetime] = None
    bar_start_ts: Optional[datetime] = None
    since_ts: Optional[datetime] = None
    action_by_ts: Optional[datetime] = None

    def __post_init__(self):
        if self.signal_ts is None:
            raise ValueError("S9Signal.signal_ts must be set at construction — evaluator bug")
        if self.bar_start_ts is None:
            raise ValueError("S9Signal.bar_start_ts must be set at construction — evaluator bug")


class S9Evaluator(SetupBase):
    """
    S9 - Short Reversion v1.0

    NOTE: This setup is cross-sectional. The evaluate_trigger() method is NOT
    called per-symbol in the standard signal_worker flow. Instead, the
    signal_worker runs a dedicated _evaluate_s9_cross_sectional() method
    that processes all symbols at once.

    This class exists for:
    1. Metadata registration in the setup registry
    2. Parameter documentation
    3. signal_engine.scan() integration (batch mode)
    """

    # LOCKED parameters -- do not modify
    RET_LOOKBACK = 4          # 4h return
    HOLD_BARS = 48            # 48h hold
    PERCENTILE = 10           # Top 10% (decile)
    BREADTH_THRESHOLD = 0.3   # Minimum market breadth
    COST_BPS = 18             # Base roundtrip cost assumption

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="S9",
            name="Short Reversion",
            version="v1.0",
            description=(
                "Cross-sectional mean-reversion: short the top decile of "
                "4h gainers, hold 48h. Gated by market breadth > 0.3."
            ),
            author="MCAS",
            setup_type="mean_reversion",
            timeframe_affinity=["1h"],
            typical_hold_bars=48,
            typical_win_rate=0.65,
            typical_r_multiple=1.5,
            min_bars_required=25,  # Need 24 bars for breadth + 1 current
            max_concurrent=50,     # Cross-sectional: many simultaneous positions
            liquidity_tiers=[
                LiquidityTier(name="futures_eligible", min_24h_volume_usd=5_000_000,
                              max_position_pct=2.5),
            ],
            sizing_caps={
                "max_risk_per_trade_pct": 2.5,  # Equal-weight ~40 positions
                "max_notional_pct": 100.0,       # Full capital across all positions
            },
        )

    @property
    def required_indicators(self) -> set:
        # S9 reads raw candle closes, not IndicatorSnapshot fields
        return {IndicatorRequirement.OHLCV}

    @property
    def parameters(self) -> list:
        return [
            SetupParameter("ret_lookback", self.RET_LOOKBACK,
                           "Hours lookback for return computation",
                           min_value=1, max_value=24, param_type="int"),
            SetupParameter("hold_bars", self.HOLD_BARS,
                           "Holding period in bars (hours)",
                           min_value=4, max_value=168, param_type="int"),
            SetupParameter("percentile", self.PERCENTILE,
                           "Top percentile to short (10 = top decile)",
                           min_value=5, max_value=30, param_type="int"),
            SetupParameter("breadth_threshold", self.BREADTH_THRESHOLD,
                           "Minimum market breadth to allow entries",
                           min_value=0.1, max_value=0.5, param_type="float"),
        ]

    def compute_state(self, snapshot, **kwargs) -> S9State:
        """Not used in production -- S9 is cross-sectional."""
        raise NotImplementedError("S9 is cross-sectional; use evaluate_cross_sectional()")

    def evaluate_trigger(self, snapshot, state, symbol, timeframe) -> Optional[S9Signal]:
        """Not used in production -- S9 evaluates cross-sectionally."""
        raise NotImplementedError("S9 is cross-sectional; use evaluate_cross_sectional()")

    @staticmethod
    def compute_score(ret_4h: float, breadth: float, rank: int,
                      decile_size: int) -> tuple:
        """Compute S9 setup score and components.

        Returns (score, rank_score, breadth_score, magnitude_score).
        """
        # Higher ret_4h magnitude = stronger signal (more to revert)
        magnitude_score = min(abs(ret_4h) / 0.10, 1.0)
        # Higher breadth = more favorable regime
        breadth_score = min((breadth - 0.3) / 0.2, 1.0)
        breadth_score = max(breadth_score, 0.0)
        # Higher rank (lower index) = strongest signal
        rank_score = 1.0 - (rank / max(decile_size, 1))

        score = 0.50 * magnitude_score + 0.25 * breadth_score + 0.25 * rank_score
        score = min(max(score, 0.01), 0.99)
        return score, rank_score, breadth_score, magnitude_score

    @staticmethod
    def build_signal(symbol: str, close: float, ret_4h: float, ret_24h: float,
                     breadth: float, rank: int, universe_size: int,
                     decile_size: int, state: S9State,
                     bar_start_ts: str = None) -> S9Signal:
        """Build a complete S9Signal for a top-decile symbol."""
        score, rank_score, breadth_score, magnitude_score = \
            S9Evaluator.compute_score(ret_4h, breadth, rank, decile_size)

        n_positive_24h = int(breadth * universe_size)

        # Price levels (display-only -- real exit is time-stop at 48h)
        entry_price = close
        stop_price = close * 1.10   # 10% protective stop (generous)
        tp1_price = close * 0.95    # 5% target
        tp2_price = close * 0.90    # 10% target
        risk = abs(stop_price - entry_price)
        reward = abs(entry_price - tp1_price)
        r_value = reward / risk if risk > 0 else 1.0

        explain = ExplainPayload(why_items=[
            (f"4h return +{ret_4h*100:.1f}% -- ranked #{rank+1}/{universe_size} "
             f"(top {(rank+1)/universe_size*100:.0f}th percentile)"),
            (f"Breadth {breadth:.2f} > 0.30 threshold -- "
             f"{n_positive_24h}/{universe_size} symbols positive on 24h"),
            ("Cross-sectional mean reversion: top-decile 4h gainers "
             "revert over next 48h (validated IC=-0.035, PF=2.56)"),
            f"Hold 48h time-stop exit -- entry at {close:.4g}",
            (f"Equal-weight short with ~{decile_size} positions "
             f"(portfolio diversification across top decile)"),
        ])

        # Derive bar timestamp from bar_start_ts param (Binance ms string → UTC).
        # No fallback — if bar_start_ts is missing, the caller is broken.
        _bar_ts = datetime.fromtimestamp(int(bar_start_ts) / 1000, tz=timezone.utc)

        return S9Signal(
            symbol=symbol,
            timeframe="1h",
            direction=Direction.SHORT,
            setup_score=score,
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            r_value=r_value,
            rank_score=rank_score,
            breadth_score=breadth_score,
            magnitude_score=magnitude_score,
            ret_4h=ret_4h,
            ret_24h=ret_24h,
            rank=rank + 1,
            universe_size=universe_size,
            decile_size=decile_size,
            breadth=breadth,
            state=state,
            explain=explain,
            time_stop_bars=48,
            signal_ts=_bar_ts,
            bar_start_ts=_bar_ts,
        )
