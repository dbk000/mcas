"""
D1 - Dormancy-to-Ignition v1.0
================================

Detects coins transitioning from prolonged dormancy (flatline price, near-zero
volume) to active accumulation (volume spikes while price stays compressed),
entering on the breakout.

Research basis:
- 69.3% of pump events show pre-accumulation averaging 2160 min (arxiv 2504.15790)
- Dual-condition thresholding achieves 0.84 precision (arxiv 2503.08692)

Five-phase pipeline:
  SCANNING → DORMANT_CONFIRMED → ACCUMULATING → TRIGGERED → COOLDOWN

Human-in-loop: Signals are identification + ranking only. No auto-execution.
"""

import logging
import os
import time as _time
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import IndicatorSnapshot, OHLCV
from setup_base import (
    SetupBase, SetupMetadata, SetupParameter, IndicatorRequirement,
    Direction, ExplainPayload, LiquidityTier,
)


# =============================================================================
# Env-var helpers
# =============================================================================

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# =============================================================================
# Configuration
# =============================================================================

class D1Config:
    """D1 Dormancy-to-Ignition configuration — env-var overridable."""

    # Universe filters (same exclusions as U1)
    MAJOR_SYMBOLS = {
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
        "MATICUSDT", "LTCUSDT", "TRXUSDT", "ATOMUSDT",
    }
    STABLECOIN_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD"}
    WRAPPED_PATTERNS = ["WBTC", "WETH", "WBNB"]
    LEVERAGE_PATTERNS = ["UP", "DOWN", "BULL", "BEAR", "3L", "3S"]

    # Volume band for D1 universe (same as U1 — microcap focus)
    MIN_VOLUME_USD = _env_float("D1_MIN_VOLUME_USD", 75_000)
    MAX_VOLUME_USD = _env_float("D1_MAX_VOLUME_USD", 3_000_000)

    # Phase 1: Dormancy detection
    # The range threshold defines "dormant bar": (high-low)/close < threshold.
    # Calibration (150 altcoins, 6mo, 1h):
    #   0.001: only pegged assets (0% altcoins qualify) — WRONG
    #   0.005: 15% of altcoins show dormancy
    #   0.010: 83% of altcoins show dormancy periods — SWEET SPOT
    #   0.015: 98% qualify (too loose, dormancy becomes meaningless)
    DORMANCY_RANGE_THRESHOLD = _env_float("D1_DORMANCY_RANGE_THRESHOLD", 0.010)

    # Dormancy score threshold: fraction of 48 bars that must be dormant
    # Distribution (200-sym sample, 113K obs, 6mo, range_thresh=0.001 — STALE, recalibrate with 0.010):
    #   0.75: ~8% pass at old threshold
    DORMANCY_THRESHOLD = _env_float("D1_DORMANCY_THRESHOLD", 0.75)
    BB_WIDTH_COMPRESS = _env_float("D1_BB_WIDTH_COMPRESS", 0.05)  # relaxed from 0.02 (redundant w/ dormancy)
    # Optional extra condition for initial dormancy confirmation.
    # Set false to use only dormancy+compression for phase confirmation.
    DORMANCY_REQUIRE_NEG_VOLUME_Z = _env_bool("D1_DORMANCY_REQUIRE_NEG_VOLUME_Z", False)

    # Accumulation divergence: score component only, NOT a binary gate.
    # Data showed accum_div > 0 killed 58% of triggers while median on
    # triggered obs was -0.035 (coin flip). Positive accum_div boosts score.
    ACCUM_DIVERGENCE_MIN = _env_float("D1_ACCUM_DIVERGENCE_MIN", 0.0)  # used for scoring reference only
    # Optional trigger gate for accum_divergence >= ACCUM_DIVERGENCE_MIN.
    REQUIRE_ACCUM_DIVERGENCE_GATE = _env_bool("D1_REQUIRE_ACCUM_DIVERGENCE_GATE", False)

    # Trigger gate toggles (all causal; no future-bar dependency).
    TRIGGER_REQUIRE_DORMANCY = _env_bool("D1_TRIGGER_REQUIRE_DORMANCY", True)
    TRIGGER_REQUIRE_COMPRESSION = _env_bool("D1_TRIGGER_REQUIRE_COMPRESSION", True)

    # Phase 3: Breakout trigger
    # vol_z>=2.0: 4.1% pass; rvol>=3.0: 4.8% pass (was 5.0 @ 2.1%)
    # Conjunctive: dorm>=0.80 & bb<0.05 & vol_z>=2.0 & rvol>=3.0 → ~0.08% (~90 obs/113K)
    VOLUME_Z_SPIKE = _env_float("D1_VOLUME_Z_SPIKE", 2.0)
    RVOL_MIN = _env_float("D1_RVOL_MIN", 3.0)  # relaxed from 5.0 (was P95+, too restrictive)

    # Risk parameters — Config D (sim matrix winner: daily PF 2.41, aggPF 3.59)
    # Tighter 1.5% stop is the key variable: filters out garbage via fee/R guard,
    # keeping only clean breakouts (370/901 → 39.5% WR vs 21.2% at 2.5%).
    STOP_PCT = _env_float("D1_STOP_PCT", 0.015)      # 1.5% (Config D)
    TP1_PCT = _env_float("D1_TP1_PCT", 0.10)          # 10% (~6.7R)
    TP2_PCT = _env_float("D1_TP2_PCT", 0.20)          # 20% (~13.3R)
    TIME_STOP_BARS_1H = _env_int("D1_TIME_STOP_BARS_1H", 72)  # 3 days

    # Cooldown after trigger
    COOLDOWN_BARS_1H = _env_int("D1_COOLDOWN_BARS_1H", 24)  # 1 day

    # Social silence lookback (soft feature, not a gate)
    SOCIAL_SILENCE_LOOKBACK_DAYS = _env_int("D1_SOCIAL_SILENCE_LOOKBACK_DAYS", 30)

    # Scoring weights (sum to 1.0)
    W_DORMANCY = _env_float("D1_W_DORMANCY", 0.20)
    W_VOL_Z = _env_float("D1_W_VOL_Z", 0.25)
    W_ACCUM_DIV = _env_float("D1_W_ACCUM_DIV", 0.15)
    W_BB_EXPANSION = _env_float("D1_W_BB_EXPANSION", 0.15)
    W_RVOL = _env_float("D1_W_RVOL", 0.10)
    W_BTC_ADJ_RET = _env_float("D1_W_BTC_ADJ_RET", 0.15)

    # Dormancy score lookback for detection
    DORMANCY_LOOKBACK = 48  # bars

    # BTC beta rolling window
    # beta=1.0 fallback means "no adjustment": idiosyncratic return = raw return - BTC return.
    # On microcaps with thin volume, 20-bar covariance is noisy — fallback triggers often.
    # This is deliberate: no-adjustment is safer than noisy-adjustment for scoring.
    BTC_BETA_WINDOW = 20  # bars


# =============================================================================
# Universe Helpers
# =============================================================================

def _base_asset(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return symbol[:-4]
    for quote in ["BUSD", "USDC", "BTC", "ETH"]:
        if symbol.endswith(quote):
            return symbol[:-len(quote)]
    return symbol


def is_d1_excluded(symbol: str) -> bool:
    """Check if symbol should be excluded from D1 evaluation."""
    if symbol in D1Config.MAJOR_SYMBOLS:
        return True
    if not symbol.endswith("USDT"):
        return True
    base = _base_asset(symbol)
    if base in D1Config.STABLECOIN_BASES:
        return True
    if any(base == p for p in D1Config.WRAPPED_PATTERNS):
        return True
    if any(base.endswith(p) for p in D1Config.LEVERAGE_PATTERNS):
        return True
    return False


# =============================================================================
# Social Mentions (soft feature — count, not gate)
# =============================================================================

_social_count_cache: dict[str, int] = {}       # symbol -> mention_count
_social_count_cache_ts: dict[str, float] = {}  # symbol -> last query epoch
_SOCIAL_COUNT_CACHE_TTL: float = 1800.0        # 30 minutes


def get_social_mentions_count(
    symbol: str,
    database_url: str,
    trigger_bar_start_ts: Optional[datetime] = None,
    lookback_days: Optional[int] = None,
) -> int:
    """Return total mention_count from social_features_1h in the lookback window.

    Uses strict `< trigger_bar_start_ts` to exclude the trigger bar itself
    (it captures the reaction, not the pre-breakout silence).

    Returns 0 if the table doesn't exist or query fails.
    Results cached per-symbol for 30 minutes.
    """
    if lookback_days is None:
        lookback_days = D1Config.SOCIAL_SILENCE_LOOKBACK_DAYS

    now = _time.time()
    cache_key = symbol
    if cache_key in _social_count_cache and (now - _social_count_cache_ts.get(cache_key, 0)) < _SOCIAL_COUNT_CACHE_TTL:
        return _social_count_cache[cache_key]

    try:
        import psycopg
        with psycopg.connect(database_url) as conn:
            if trigger_bar_start_ts is not None:
                row = conn.execute(
                    "SELECT COALESCE(SUM(mention_count), 0) "
                    "FROM social_features_1h "
                    "WHERE symbol = %s "
                    "  AND bar_start_ts >= %s - %s * INTERVAL '1 day' "
                    "  AND bar_start_ts < %s",
                    (symbol, trigger_bar_start_ts, lookback_days, trigger_bar_start_ts),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(mention_count), 0) "
                    "FROM social_features_1h "
                    "WHERE symbol = %s "
                    "  AND bar_start_ts >= NOW() - %s * INTERVAL '1 day' "
                    "  AND bar_start_ts < NOW()",
                    (symbol, lookback_days),
                ).fetchone()
        result = int(row[0]) if row else 0
    except Exception as e:
        logger.debug("Social mentions count failed for %s: %s", symbol, e)
        result = 0

    _social_count_cache[cache_key] = result
    _social_count_cache_ts[cache_key] = now
    return result


# =============================================================================
# Phase Enum
# =============================================================================

class D1Phase(Enum):
    """D1 pipeline phase."""
    SCANNING = "SCANNING"                     # Default: checking dormancy
    DORMANT_CONFIRMED = "DORMANT_CONFIRMED"   # Dormancy criteria met
    ACCUMULATING = "ACCUMULATING"             # ADL divergence detected
    TRIGGERED = "TRIGGERED"                   # Breakout signal fired
    COOLDOWN = "COOLDOWN"                     # Post-trigger cooldown


# =============================================================================
# State
# =============================================================================

@dataclass
class D1State:
    """Tier 3 state for Dormancy-to-Ignition."""

    phase: D1Phase = D1Phase.SCANNING

    # Phase 1: Dormancy metrics
    dormancy_score: float = 0.0
    bb_width: float = 0.0
    dormancy_confirmed_at_bar: int = 0

    # Compression box (Donchian envelope during dormancy)
    # prev_ values = BEFORE current bar's donchian update.
    # Trigger compares close vs prev_compression_box_high to avoid the bug
    # where current bar's high updates the ceiling you're trying to break.
    compression_box_high: float = 0.0
    compression_box_low: float = 0.0
    prev_compression_box_high: float = 0.0
    prev_compression_box_low: float = 0.0

    # Phase 2: Accumulation metrics
    accum_divergence: float = 0.0
    accumulation_detected_at_bar: int = 0

    # Phase 3: Trigger metrics
    volume_z: float = 0.0
    rvol: float = 0.0
    bb_width_prev: float = 0.0  # for expansion detection

    # BTC beta adjustment
    btc_beta: float = 1.0

    # Timing
    bar_index: int = 0
    cooldown_until_bar: int = 0

    # Score components (computed at trigger time)
    score_components: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "dormancy_score": self.dormancy_score,
            "bb_width": self.bb_width,
            "dormancy_confirmed_at_bar": self.dormancy_confirmed_at_bar,
            "compression_box_high": self.compression_box_high,
            "compression_box_low": self.compression_box_low,
            "prev_compression_box_high": self.prev_compression_box_high,
            "prev_compression_box_low": self.prev_compression_box_low,
            "accum_divergence": self.accum_divergence,
            "accumulation_detected_at_bar": self.accumulation_detected_at_bar,
            "volume_z": self.volume_z,
            "rvol": self.rvol,
            "bb_width_prev": self.bb_width_prev,
            "btc_beta": self.btc_beta,
            "bar_index": self.bar_index,
            "cooldown_until_bar": self.cooldown_until_bar,
            "score_components": self.score_components,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "D1State":
        return cls(
            phase=D1Phase(d.get("phase", "SCANNING")),
            dormancy_score=float(d.get("dormancy_score", 0)),
            bb_width=float(d.get("bb_width", 0)),
            dormancy_confirmed_at_bar=int(d.get("dormancy_confirmed_at_bar", 0)),
            compression_box_high=float(d.get("compression_box_high", 0)),
            compression_box_low=float(d.get("compression_box_low", 0)),
            prev_compression_box_high=float(d.get("prev_compression_box_high", 0)),
            prev_compression_box_low=float(d.get("prev_compression_box_low", 0)),
            accum_divergence=float(d.get("accum_divergence", 0)),
            accumulation_detected_at_bar=int(d.get("accumulation_detected_at_bar", 0)),
            volume_z=float(d.get("volume_z", 0)),
            rvol=float(d.get("rvol", 0)),
            bb_width_prev=float(d.get("bb_width_prev", 0)),
            btc_beta=float(d.get("btc_beta", 1.0)),
            bar_index=int(d.get("bar_index", 0)),
            cooldown_until_bar=int(d.get("cooldown_until_bar", 0)),
            score_components=dict(d.get("score_components", {})),
        )


# =============================================================================
# Signal
# =============================================================================

@dataclass
class D1Signal:
    """D1 Dormancy-to-Ignition signal — complete payload."""

    # Identity
    symbol: str = ""
    timeframe: str = "1h"
    direction: Direction = Direction.LONG
    setup_id: str = "D1"

    # Scoring
    setup_score: float = 0.0

    # Trade levels
    entry_price: float = 0.0
    stop_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    r_value: float = 0.0

    # Time stop
    time_stop_bars: int = D1Config.TIME_STOP_BARS_1H

    # D1-specific metrics
    dormancy_score: float = 0.0
    accum_divergence: float = 0.0
    compression_box_high: float = 0.0
    compression_box_low: float = 0.0
    volume_z_at_trigger: float = 0.0
    rvol_at_trigger: float = 0.0
    bb_width_at_trigger: float = 0.0
    btc_beta: float = 1.0

    # Score breakdown
    score_components: dict = field(default_factory=dict)

    # Social silence (soft feature — not a gate)
    social_mentions_30d: int = 0

    # Explainability
    explain: Optional[ExplainPayload] = None

    # State snapshot
    state: Optional[D1State] = None


# =============================================================================
# D1 Evaluator
# =============================================================================

class D1Evaluator(SetupBase):
    """
    Dormancy-to-Ignition evaluator.

    Detects coins transitioning from dormancy → accumulation → breakout.
    Operates on 1h bars. Uses indicators from FeatureFactory (dormancy_score,
    accum_divergence, bb_width, volume_z, rvol, donchian channels).
    """

    @property
    def metadata(self) -> SetupMetadata:
        return SetupMetadata(
            id="D1",
            name="Dormancy-to-Ignition",
            version="v1.2",
            description=(
                "Detects coins transitioning from prolonged dormancy to active "
                "accumulation, entering on volume breakout above compression box."
            ),
            author="MCAS",
            setup_type="breakout",
            timeframe_affinity=["1h"],
            typical_hold_bars=72,
            typical_win_rate=0.35,
            typical_r_multiple=4.0,
            min_bars_required=201,
            max_concurrent=3,
            liquidity_tiers=[
                LiquidityTier(name="micro_cap", min_24h_volume_usd=0, max_position_pct=1.0),
                LiquidityTier(name="small_cap", min_24h_volume_usd=500_000, max_position_pct=2.0),
                LiquidityTier(name="mid_cap", min_24h_volume_usd=5_000_000, max_position_pct=3.0),
            ],
        )

    @property
    def required_indicators(self) -> set:
        return {
            IndicatorRequirement.BB_BANDS,
            IndicatorRequirement.VOLUME_Z,
            IndicatorRequirement.DONCHIAN,
            IndicatorRequirement.DORMANCY_SCORE,
            IndicatorRequirement.ACCUM_DIVERGENCE,
            IndicatorRequirement.ATR,
        }

    @property
    def parameters(self) -> list:
        return [
            SetupParameter("dormancy_threshold", D1Config.DORMANCY_THRESHOLD,
                           "Min dormancy_score for Phase 1", 0.5, 1.0),
            SetupParameter("bb_width_compress", D1Config.BB_WIDTH_COMPRESS,
                           "Max BB width for dormancy confirmation", 0.005, 0.05),
            SetupParameter("volume_z_spike", D1Config.VOLUME_Z_SPIKE,
                           "Min volume z-score for breakout trigger", 1.0, 5.0),
            SetupParameter("rvol_min", D1Config.RVOL_MIN,
                           "Min relative volume for breakout", 2.0, 20.0),
            SetupParameter("accum_divergence_min", D1Config.ACCUM_DIVERGENCE_MIN,
                           "Min ADL divergence for accumulation phase", -0.1, 0.5),
            SetupParameter("stop_pct", D1Config.STOP_PCT,
                           "Stop loss percentage", 0.01, 0.10),
            SetupParameter("tp1_pct", D1Config.TP1_PCT,
                           "Take profit 1 percentage", 0.05, 0.30),
            SetupParameter("tp2_pct", D1Config.TP2_PCT,
                           "Take profit 2 percentage", 0.10, 0.50),
            SetupParameter("time_stop_bars", D1Config.TIME_STOP_BARS_1H,
                           "Time stop in bars (1h)", 24, 168, param_type="int"),
        ]

    def update_config(self, params: dict) -> dict:
        """Hot-reload config from Pipeline. Does not reset runtime state.

        Accepts the nested parameter dict as stored in meta.node_version
        (with sub-objects: gates, exit_config, scoring_weights).
        Returns dict of changed keys with old/new values.
        """
        changed = {}

        # Flat top-level parameters → D1Config class attrs
        _FLAT_MAP = {
            "dormancy_threshold": "DORMANCY_THRESHOLD",
            "dormancy_range_threshold": "DORMANCY_RANGE_THRESHOLD",
            "bb_width_compress": "BB_WIDTH_COMPRESS",
            "volume_z_spike": "VOLUME_Z_SPIKE",
            "rvol_min": "RVOL_MIN",
            "accum_divergence_min": "ACCUM_DIVERGENCE_MIN",
            "trigger_require_dormancy": "TRIGGER_REQUIRE_DORMANCY",
            "trigger_require_compression": "TRIGGER_REQUIRE_COMPRESSION",
            "require_accum_divergence_gate": "REQUIRE_ACCUM_DIVERGENCE_GATE",
            "cooldown_bars_1h": "COOLDOWN_BARS_1H",
            "min_volume_usd": "MIN_VOLUME_USD",
            "max_volume_usd": "MAX_VOLUME_USD",
            "dormancy_require_neg_volume_z": "DORMANCY_REQUIRE_NEG_VOLUME_Z",
            "social_silence_lookback_days": "SOCIAL_SILENCE_LOOKBACK_DAYS",
        }

        # Apply flat params
        for param_key, config_attr in _FLAT_MAP.items():
            if param_key in params:
                old = getattr(D1Config, config_attr, None)
                new = params[param_key]
                if old != new:
                    setattr(D1Config, config_attr, new)
                    changed[config_attr] = {"old": old, "new": new}

        # Apply nested exit_config
        exit_cfg = params.get("exit_config", {})
        if isinstance(exit_cfg, dict):
            _EXIT_MAP = {
                "stop_pct": "STOP_PCT",
                "tp1_pct": "TP1_PCT",
                "tp2_pct": "TP2_PCT",
                "time_stop_bars_1h": "TIME_STOP_BARS_1H",
            }
            for param_key, config_attr in _EXIT_MAP.items():
                if param_key in exit_cfg:
                    old = getattr(D1Config, config_attr, None)
                    new = exit_cfg[param_key]
                    if old != new:
                        setattr(D1Config, config_attr, new)
                        changed[config_attr] = {"old": old, "new": new}

        # Apply nested scoring_weights (existing _compute_score normalises by sum)
        weights_cfg = params.get("scoring_weights", {})
        if isinstance(weights_cfg, dict):
            _WEIGHT_MAP = {
                "dormancy": "W_DORMANCY",
                "vol_z": "W_VOL_Z",
                "accum_div": "W_ACCUM_DIV",
                "bb_expansion": "W_BB_EXPANSION",
                "rvol": "W_RVOL",
                "btc_adj_ret": "W_BTC_ADJ_RET",
            }
            for param_key, config_attr in _WEIGHT_MAP.items():
                if param_key in weights_cfg:
                    old = getattr(D1Config, config_attr, None)
                    new = weights_cfg[param_key]
                    if old != new:
                        setattr(D1Config, config_attr, new)
                        changed[config_attr] = {"old": old, "new": new}

        if changed:
            logger.info("D1Config hot-reloaded: %s", changed)
        return changed

    def compute_state(
        self,
        snapshot: IndicatorSnapshot,
        prev_state: Optional[D1State] = None,
        btc_returns: Optional[np.ndarray] = None,
    ) -> D1State:
        """
        Compute D1 state from indicator snapshot.

        Phase transitions:
          SCANNING → DORMANT_CONFIRMED: dormancy_score >= threshold AND bb_width < compress
          DORMANT_CONFIRMED → ACCUMULATING: accum_divergence > min
          ACCUMULATING → TRIGGERED: (handled in evaluate_trigger)
          TRIGGERED → COOLDOWN: immediate after signal fire
          COOLDOWN → SCANNING: after cooldown_bars elapsed
        """
        state = prev_state if prev_state else D1State()
        state.bar_index += 1

        # Read current indicator values
        state.bb_width_prev = state.bb_width
        state.dormancy_score = snapshot.dormancy_score
        state.bb_width = snapshot.bb_width
        state.volume_z = snapshot.volume_z
        state.rvol = snapshot.rvol
        state.accum_divergence = snapshot.accum_divergence

        # BTC beta (if btc returns provided)
        if btc_returns is not None and len(btc_returns) >= D1Config.BTC_BETA_WINDOW:
            state.btc_beta = self._compute_btc_beta(snapshot, btc_returns)

        # Cooldown check
        if state.phase == D1Phase.COOLDOWN:
            if state.bar_index >= state.cooldown_until_bar:
                state.phase = D1Phase.SCANNING
            return state

        # Reset to SCANNING if triggered (evaluate_trigger handles transition)
        if state.phase == D1Phase.TRIGGERED:
            state.phase = D1Phase.COOLDOWN
            state.cooldown_until_bar = state.bar_index + D1Config.COOLDOWN_BARS_1H
            return state

        # Phase 1: Dormancy detection
        if state.phase == D1Phase.SCANNING:
            if (
                state.dormancy_score >= D1Config.DORMANCY_THRESHOLD
                and state.bb_width < D1Config.BB_WIDTH_COMPRESS
                and (
                    not D1Config.DORMANCY_REQUIRE_NEG_VOLUME_Z
                    or state.volume_z < 0
                )
            ):
                state.phase = D1Phase.DORMANT_CONFIRMED
                state.dormancy_confirmed_at_bar = state.bar_index
                # Record compression box from Donchian
                # prev_ = current since this is the first bar of dormancy
                state.compression_box_high = snapshot.donchian_high_20
                state.compression_box_low = snapshot.donchian_low_20
                state.prev_compression_box_high = snapshot.donchian_high_20
                state.prev_compression_box_low = snapshot.donchian_low_20

        # Phase 2: Dormancy confirmed — track compression box, wait for trigger
        # accum_divergence is a score component (boosts signal when positive),
        # NOT a phase gate. Data: median accum_div at trigger = -0.035 (coin flip).
        if state.phase == D1Phase.DORMANT_CONFIRMED:
            # Save PREVIOUS bar's compression box before updating.
            # evaluate_trigger compares close vs prev_compression_box_high
            # to avoid the bug where the breakout bar's own high updates
            # the ceiling you're trying to break through.
            state.prev_compression_box_high = state.compression_box_high
            state.prev_compression_box_low = state.compression_box_low
            state.compression_box_high = snapshot.donchian_high_20
            state.compression_box_low = snapshot.donchian_low_20

            # Track accumulation if detected (for scoring, not gating)
            if state.accum_divergence > D1Config.ACCUM_DIVERGENCE_MIN:
                state.phase = D1Phase.ACCUMULATING
                state.accumulation_detected_at_bar = state.bar_index

            # Dormancy broken → back to scanning
            if (state.dormancy_score < D1Config.DORMANCY_THRESHOLD * 0.5
                    or state.bb_width > D1Config.BB_WIDTH_COMPRESS * 3):
                state.phase = D1Phase.SCANNING

        # Accumulating: keep updating compression box, ready for trigger
        if state.phase == D1Phase.ACCUMULATING:
            # Save previous before update
            state.prev_compression_box_high = state.compression_box_high
            state.prev_compression_box_low = state.compression_box_low
            state.compression_box_high = max(state.compression_box_high,
                                              snapshot.donchian_high_20)
            # If accumulation fades, still stay in dormant_confirmed (can still trigger)
            if state.accum_divergence < -abs(D1Config.ACCUM_DIVERGENCE_MIN) * 2:
                state.phase = D1Phase.DORMANT_CONFIRMED

        return state

    def evaluate_trigger(
        self,
        snapshot: IndicatorSnapshot,
        state: D1State,
        symbol: str,
        timeframe: str,
    ) -> Optional[D1Signal]:
        """
        Evaluate if D1 trigger conditions are met.

        Five gates must pass (all known at trigger bar close):
          Gate 1: Dormancy (dormancy_score >= threshold)
          Gate 2: Compression (bb_width < threshold)
          Gate 3: Volume spike (volume_z >= threshold AND rvol >= threshold)
          Gate 4: Price breakout above compression box high
          Gate 5: BB width expanding (regime transition from compressed)

        Optional:
          Gate 6 (disabled by default): accum_divergence >= min threshold.
        """
        # Trigger from DORMANT_CONFIRMED or ACCUMULATING
        if state.phase not in (D1Phase.DORMANT_CONFIRMED, D1Phase.ACCUMULATING):
            return None

        close = snapshot.ohlcv[-1].close if snapshot.ohlcv else 0.0
        if close <= 0:
            return None

        # Gate 1: Dormancy must have been confirmed
        if state.dormancy_confirmed_at_bar == 0:
            return None
        if D1Config.TRIGGER_REQUIRE_DORMANCY and state.dormancy_score < D1Config.DORMANCY_THRESHOLD:
            return None

        # Gate 2: Compression at trigger
        if D1Config.TRIGGER_REQUIRE_COMPRESSION and state.bb_width >= D1Config.BB_WIDTH_COMPRESS:
            return None

        # Gate 3: Volume spike
        if state.volume_z < D1Config.VOLUME_Z_SPIKE:
            return None
        if state.rvol < D1Config.RVOL_MIN:
            return None

        # Optional gate: accumulation divergence minimum
        if (
            D1Config.REQUIRE_ACCUM_DIVERGENCE_GATE
            and state.accum_divergence < D1Config.ACCUM_DIVERGENCE_MIN
        ):
            return None

        # Gate 4: Price breakout above compression box
        # Uses PREVIOUS bar's compression box — not current bar's donchian.
        # Current bar's high updates donchian_high_20, making close > donchian_high
        # impossible (close <= high <= donchian_high). We ask: "did price break
        # above where the ceiling WAS", not "is price above where the ceiling IS."
        if state.prev_compression_box_high <= 0:
            return None
        if close <= state.prev_compression_box_high:
            return None

        # Gate 5: BB width expanding (current > previous)
        if state.bb_width <= state.bb_width_prev:
            return None

        # All gates passed — compute signal
        entry = close
        stop = max(state.prev_compression_box_low, entry * (1 - D1Config.STOP_PCT))
        tp1 = entry * (1 + D1Config.TP1_PCT)
        tp2 = entry * (1 + D1Config.TP2_PCT)

        risk = entry - stop
        if risk <= 0:
            return None
        r_value = (tp1 - entry) / risk

        # Score computation
        score_components = self._compute_score(state, snapshot, close)
        setup_score = sum(score_components.values())
        setup_score = max(0.0, min(1.0, setup_score))

        # BTC-adjusted return component
        if state.btc_beta > 0 and len(snapshot.ohlcv) >= 2:
            prev_close = snapshot.ohlcv[-2].close
            raw_ret = (close - prev_close) / prev_close if prev_close > 0 else 0
            # Idiosyncratic component is positive → genuine buying
            idio_positive = raw_ret > 0
        else:
            idio_positive = True

        # Build explain payload
        why_items = [
            f"Dormancy score {state.dormancy_score:.0%} (threshold {D1Config.DORMANCY_THRESHOLD:.0%})",
            f"Compression width {state.bb_width:.4f} (max {D1Config.BB_WIDTH_COMPRESS:.4f})",
            f"Volume z-score {state.volume_z:.1f} (threshold {D1Config.VOLUME_Z_SPIKE:.1f})",
            f"Relative volume {state.rvol:.1f}x (threshold {D1Config.RVOL_MIN:.1f}x)",
            f"Accumulation divergence {state.accum_divergence:.4f} ({'positive — boosts score' if state.accum_divergence > 0 else 'negative — score neutral'})",
            f"Price broke above compression box {state.prev_compression_box_high:.8g}",
            f"BB width expanding {state.bb_width_prev:.4f} → {state.bb_width:.4f}",
        ]
        if not idio_positive:
            why_items.append("Caution: return correlated with BTC (beta-driven)")

        explain = ExplainPayload(why_items=why_items)

        # Mark state as triggered
        state.phase = D1Phase.TRIGGERED
        state.score_components = score_components

        return D1Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=Direction.LONG,
            setup_score=setup_score,
            entry_price=entry,
            stop_price=stop,
            tp1_price=tp1,
            tp2_price=tp2,
            r_value=r_value,
            time_stop_bars=D1Config.TIME_STOP_BARS_1H,
            dormancy_score=state.dormancy_score,
            accum_divergence=state.accum_divergence,
            compression_box_high=state.compression_box_high,
            compression_box_low=state.compression_box_low,
            volume_z_at_trigger=state.volume_z,
            rvol_at_trigger=state.rvol,
            bb_width_at_trigger=state.bb_width,
            btc_beta=state.btc_beta,
            score_components=score_components,
            explain=explain,
            state=state,
        )

    def _compute_score(self, state: D1State, snapshot: IndicatorSnapshot,
                       close: float) -> dict:
        """Compute weighted score components. Each in [0, 1], sum weighted."""
        components = {}
        w_sum = (
            D1Config.W_DORMANCY
            + D1Config.W_VOL_Z
            + D1Config.W_ACCUM_DIV
            + D1Config.W_BB_EXPANSION
            + D1Config.W_RVOL
            + D1Config.W_BTC_ADJ_RET
        )
        if w_sum <= 0:
            w_sum = 1.0
        w_dormancy = D1Config.W_DORMANCY / w_sum
        w_vol_z = D1Config.W_VOL_Z / w_sum
        w_accum_div = D1Config.W_ACCUM_DIV / w_sum
        w_bb_expansion = D1Config.W_BB_EXPANSION / w_sum
        w_rvol = D1Config.W_RVOL / w_sum
        w_btc_adj_ret = D1Config.W_BTC_ADJ_RET / w_sum

        # Dormancy: higher is better (more dormant before breakout)
        components["dormancy"] = w_dormancy * min(
            state.dormancy_score / D1Config.DORMANCY_THRESHOLD, 1.0
        )

        # Volume z: higher is better (stronger volume spike)
        vol_z_norm = min(state.volume_z / 5.0, 1.0)  # Normalize to 5.0 max
        components["vol_z"] = w_vol_z * vol_z_norm

        # Accumulation divergence: higher is better
        accum_norm = min(max(state.accum_divergence, 0) / 0.01, 1.0)  # 0.01 as reference max
        components["accum_div"] = w_accum_div * accum_norm

        # BB expansion: ratio of current to previous width
        if state.bb_width_prev > 0:
            expansion = state.bb_width / state.bb_width_prev
            expansion_norm = min((expansion - 1.0) / 1.0, 1.0)  # 2x expansion = 1.0
            expansion_norm = max(expansion_norm, 0.0)
        else:
            expansion_norm = 0.5
        components["bb_expansion"] = w_bb_expansion * expansion_norm

        # RVOL: higher is better
        rvol_norm = min(state.rvol / 20.0, 1.0)  # Normalize to 20x max
        components["rvol"] = w_rvol * rvol_norm

        # BTC-adjusted return: positive idiosyncratic return = genuine buying
        if len(snapshot.ohlcv) >= 2:
            prev_close = snapshot.ohlcv[-2].close
            raw_ret = (close - prev_close) / prev_close if prev_close > 0 else 0
            # Simple BTC adjustment: assume beta-correlated portion is noise
            adj_ret = raw_ret  # Full BTC decomposition done in backfill with actual BTC data
            btc_adj_score = min(max(adj_ret / 0.05, 0), 1.0)  # 5% return = max score
        else:
            btc_adj_score = 0.5
        components["btc_adj_ret"] = w_btc_adj_ret * btc_adj_score

        return components

    def _compute_btc_beta(self, snapshot: IndicatorSnapshot,
                          btc_returns: np.ndarray) -> float:
        """Compute rolling beta vs BTC returns over D1Config.BTC_BETA_WINDOW bars."""
        n = D1Config.BTC_BETA_WINDOW
        if len(snapshot.ohlcv) < n + 1 or len(btc_returns) < n:
            return 1.0

        # Asset returns
        closes = np.array([c.close for c in snapshot.ohlcv[-(n+1):]])
        asset_rets = np.diff(closes) / closes[:-1]
        btc_rets = btc_returns[-n:]

        if len(asset_rets) != len(btc_rets):
            return 1.0

        # Beta = cov(asset, btc) / var(btc)
        btc_var = np.var(btc_rets)
        if btc_var == 0:
            return 1.0

        cov = np.cov(asset_rets, btc_rets)[0, 1]
        beta = cov / btc_var
        return float(np.clip(beta, 0.0, 3.0))
