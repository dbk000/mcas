"""
Feature Factory - Indicator Calculations
=========================================

Canon §6 compliant: every indicator has indicator_id, inputs, params,
expression type, implementation pointer, and version.

Tiered computation model:
- Tier 0: Raw OHLCV
- Tier 1: Single-step indicators (ATR, CLV, VolumeZ, RSI)
- Tier 2: Multi-step indicators (SMAs, BBands, Keltner, slopes)
- Tier 3: State logic (computed in setups module)
- Tier 4: Triggers (computed in setups module)

All indicators use ring buffers for efficiency.
ATR-normalised where appropriate for cross-asset comparability.
"""

import os
import re
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

# Dark mode: compute expansion indicators when enabled (default: off)
COMPUTE_DARK_INDICATORS = os.getenv("COMPUTE_DARK_INDICATORS", "false").lower() in {"1", "true", "yes"}


# =============================================================================
# Canon §1.6: Canonical Indicator ID Generator
# =============================================================================

_INDICATOR_ID_RE = re.compile(r'^[A-Z0-9_.]+$')


def generate_indicator_id(family: str, *params) -> str:
    """
    Canon §1.6 compliant: single canonical generator for indicator IDs.

    Grammar: <FAMILY>_<PARAMS> using uppercase A-Z, digits 0-9,
    underscores, and dots only.

    Args:
        family: Indicator family name (e.g. "ATR", "BB", "EMA").
        *params: Positional parameters (converted to str, joined with '_').

    Returns:
        Canonical indicator ID string.

    Raises:
        ValueError: If the resulting ID contains invalid characters.
    """
    family_upper = family.upper()
    if params:
        param_str = "_".join(str(p) for p in params)
        result = f"{family_upper}_{param_str}"
    else:
        result = family_upper
    # Validate: only A-Z, 0-9, underscore, dot allowed
    if not _INDICATOR_ID_RE.match(result):
        raise ValueError(
            f"Invalid indicator ID '{result}': only A-Z, 0-9, underscore, "
            f"and dot are allowed."
        )
    return result


# =============================================================================
# Canon §6: Indicator Registry
# =============================================================================

INDICATOR_LIBRARY_VERSION = "1.0.0"


@dataclass
class IndicatorDef:
    """Canon §6: canonical indicator definition."""
    indicator_id: str
    name: str
    tier: int                          # 0=raw, 1=basic, 2=derived
    inputs: list[str]                  # e.g. ["close"], ["high","low","close"]
    params: dict = field(default_factory=dict)
    expression_type: str = "value"     # value, slope, cross, percentile
    implementation: str = ""           # method name on FeatureFactory
    version: str = "1.0.0"
    active: bool = True                # False = dark mode (computed only when COMPUTE_DARK_INDICATORS=true)


INDICATOR_REGISTRY: dict[str, IndicatorDef] = {}


def _register(ind: IndicatorDef):
    INDICATOR_REGISTRY[ind.indicator_id] = ind
    return ind


# Tier 1
_register(IndicatorDef(
    indicator_id=generate_indicator_id("ATR", 14), name="Average True Range", tier=1,
    inputs=["high", "low", "close"], params={"n": 14},
    implementation="FeatureFactory._atr",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("CLV"), name="Close Location Value", tier=1,
    inputs=["high", "low", "close"], params={},
    implementation="FeatureFactory._clv",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("VOLUME_Z", 20), name="Volume Z-Score", tier=1,
    inputs=["volume"], params={"n": 20},
    implementation="FeatureFactory._volume_z",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("RSI", 14), name="RSI (Wilder)", tier=1,
    inputs=["close"], params={"n": 14},
    implementation="FeatureFactory._rsi_series",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("RSI_SMA", 14), name="RSI SMA", tier=1,
    inputs=["close"], params={"n_rsi": 14, "n_sma": 14},
    expression_type="value",
    implementation="FeatureFactory._sma(rsi_series)",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("RSI_SLOPE"), name="RSI Slope (1-bar delta)", tier=1,
    inputs=["close"], params={"n_rsi": 14},
    expression_type="slope",
    implementation="rsi[-1] - rsi[-2]",
))

# Tier 2 — MAs
for _n in [9, 20, 21, 50]:
    _register(IndicatorDef(
        indicator_id=generate_indicator_id("EMA", _n), name=f"EMA {_n}", tier=2,
        inputs=["close"], params={"n": _n},
        implementation="FeatureFactory._ema",
    ))
for _n in [50, 200]:
    _register(IndicatorDef(
        indicator_id=generate_indicator_id("SMA", _n), name=f"SMA {_n}", tier=2,
        inputs=["close"], params={"n": _n},
        implementation="FeatureFactory._sma",
    ))

# Tier 2 — Bands
_register(IndicatorDef(
    indicator_id=generate_indicator_id("BB", 20, 2), name="Bollinger Bands (20, 2σ)", tier=2,
    inputs=["close"], params={"n": 20, "k": 2.0},
    implementation="FeatureFactory._sma + FeatureFactory._std",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("KC", 20, "1_5"), name="Keltner Channels (20, 1.5×ATR)", tier=2,
    inputs=["high", "low", "close"], params={"n": 20, "mult": 1.5},
    implementation="FeatureFactory._ema + ATR",
))

# Tier 2 — Slopes
_register(IndicatorDef(
    indicator_id=generate_indicator_id("SMA50_SLOPE", "ATR"), name="SMA50 slope (ATR-normalised)", tier=2,
    inputs=["close"], params={"n": 50},
    expression_type="slope",
    implementation="(sma50[-1] - sma50[-2]) / atr",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("SMA200_SLOPE", "ATR"), name="SMA200 slope (ATR-normalised)", tier=2,
    inputs=["close"], params={"n": 200},
    expression_type="slope",
    implementation="(sma200[-1] - sma200[-2]) / atr",
))

# Tier 2 — Derived
_register(IndicatorDef(
    indicator_id=generate_indicator_id("MA_GAP", "ATR"), name="abs(SMA50 - SMA200) / ATR", tier=2,
    inputs=["close"], params={"n_fast": 50, "n_slow": 200},
    expression_type="value",
    implementation="abs(sma_50 - sma_200) / atr",
))

# Tier 2 — Band components (derived from BB_20_2)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("BB_MB"), name="BB Middle Band (SMA20)", tier=2,
    inputs=["close"], params={"n": 20},
    implementation="SMA(close, 20)",
    version="1.0.0",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("BB_UB"), name="BB Upper Band", tier=2,
    inputs=["close"], params={"n": 20, "k": 2.0},
    implementation="SMA(close, 20) + k * STD(close, 20)",
    version="1.0.0",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("BB_LB"), name="BB Lower Band", tier=2,
    inputs=["close"], params={"n": 20, "k": 2.0},
    implementation="SMA(close, 20) - k * STD(close, 20)",
    version="1.0.0",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("BB_WIDTH"), name="BB Width (normalised)", tier=2,
    inputs=["close"], params={"n": 20, "k": 2.0},
    implementation="(bb_ub - bb_lb) / bb_mb",
    version="1.0.0",
))

# Tier 2 — KC components (derived from KC_20_1_5)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("KC_MB"), name="KC Middle Band (EMA20)", tier=2,
    inputs=["close"], params={"n": 20},
    implementation="EMA(close, 20)",
    version="1.0.0",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("KC_UB"), name="KC Upper Band", tier=2,
    inputs=["high", "low", "close"], params={"n": 20, "mult": 1.5},
    implementation="EMA(close, 20) + mult * ATR(14)",
    version="1.0.0",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("KC_LB"), name="KC Lower Band", tier=2,
    inputs=["high", "low", "close"], params={"n": 20, "mult": 1.5},
    implementation="EMA(close, 20) - mult * ATR(14)",
    version="1.0.0",
))

# Tier 2 — HTF proxies
_register(IndicatorDef(
    indicator_id=generate_indicator_id("HTF_CLOSE"), name="HTF Close", tier=2,
    inputs=["close"], params={},
    implementation="htf_candles[-1].close",
    version="1.0.0",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("HTF_SMA50"), name="HTF SMA 50", tier=2,
    inputs=["close"], params={"n": 50},
    implementation="SMA(htf_close, 50)",
    version="1.0.0",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("HTF_BB_MB"), name="HTF BB Middle Band", tier=2,
    inputs=["close"], params={"n": 20},
    implementation="SMA(htf_close, 20)",
    version="1.0.0",
))

# Tier 2 — EMA Slopes (5-bar delta, ATR-normalised)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("EMA20_SLOPE", "ATR"), name="EMA20 slope (5-bar, ATR-normalised)", tier=2,
    inputs=["close"], params={"n_ema": 20, "n_slope": 5},
    expression_type="slope",
    implementation="(ema20[-1] - ema20[-6]) / (5 * atr)",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("EMA50_SLOPE", "ATR"), name="EMA50 slope (5-bar, ATR-normalised)", tier=2,
    inputs=["close"], params={"n_ema": 50, "n_slope": 5},
    expression_type="slope",
    implementation="(ema50[-1] - ema50[-6]) / (5 * atr)",
))

# Tier 2 — SMA 20 (for S10 MACD Scalp)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("SMA", 20), name="SMA 20", tier=2,
    inputs=["close"], params={"n": 20},
    implementation="FeatureFactory._sma",
))

# Tier 2 — MACD (12, 26, 9)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("MACD", 12, 26, 9), name="MACD (12, 26, 9)", tier=2,
    inputs=["close"], params={"fast": 12, "slow": 26, "signal": 9},
    implementation="EMA(close, 12) - EMA(close, 26); Signal = EMA(macd, 9)",
))

# Tier 1 — Relative Volume Ratio
_register(IndicatorDef(
    indicator_id=generate_indicator_id("RVOL", 20), name="Relative Volume Ratio (20-bar)", tier=1,
    inputs=["volume"], params={"n": 20},
    implementation="volume[-1] / mean(volume[-20:])",
))

# Tier 2 — Donchian Channels (20-bar)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("DONCHIAN_HIGH", 20), name="Donchian High (20)", tier=2,
    inputs=["high"], params={"n": 20},
    implementation="np.max(highs[-20:])",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("DONCHIAN_LOW", 20), name="Donchian Low (20)", tier=2,
    inputs=["low"], params={"n": 20},
    implementation="np.min(lows[-20:])",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("DONCHIAN_MID", 20), name="Donchian Mid (20)", tier=2,
    inputs=["high", "low"], params={"n": 20},
    implementation="(donchian_high + donchian_low) / 2",
))

# Tier 2 — VWAP (intraday, resets at 00:00 UTC)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("VWAP"), name="VWAP (session reset 00:00 UTC)", tier=2,
    inputs=["high", "low", "close", "volume"], params={},
    implementation="FeatureFactory._compute_vwap",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("VWAP_UPPER_2SD"), name="VWAP + 2σ", tier=2,
    inputs=["high", "low", "close", "volume"], params={"k": 2.0},
    implementation="vwap + 2 * vwap_std",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("VWAP_LOWER_2SD"), name="VWAP - 2σ", tier=2,
    inputs=["high", "low", "close", "volume"], params={"k": 2.0},
    implementation="vwap - 2 * vwap_std",
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("VWAP_DISTANCE", "ATR"), name="(Close - VWAP) / ATR", tier=2,
    inputs=["close", "high", "low", "volume"], params={},
    expression_type="value",
    implementation="(close - vwap) / atr",
))

# Tier 2 — Dormancy Score (fraction of low-range bars in trailing window)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("DORMANCY_SCORE", 48), name="Dormancy Score (48-bar)", tier=2,
    inputs=["high", "low", "close"], params={"n": 48, "threshold": 0.001},
    implementation="FeatureFactory._dormancy_score",
))

# Tier 2 — Accumulation Divergence (ADL slope vs price slope)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("ACCUM_DIVERGENCE", 20), name="Accumulation Divergence (20-bar)", tier=2,
    inputs=["high", "low", "close", "volume"], params={"n": 20},
    implementation="FeatureFactory._accumulation_divergence",
))

# Tier 1 — L2 Proxy: Range-to-Volume Ratio (liquidity depth proxy)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("RVR"), name="Range-to-Volume Ratio", tier=1,
    inputs=["high", "low", "volume"], params={},
    implementation="(high - low) / (volume + 1e-10)",
    version="1.0.0",
    active=False,  # Dark mode — not wired into any strategy
))

# Tier 1 — L2 Proxy: Volume Acceleration (volume surge vs 20-bar SMA)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("VOL_ACCEL", 20), name="Volume Acceleration (20-bar)", tier=1,
    inputs=["volume"], params={"n": 20},
    implementation="volume[-1] / SMA(volume, 20)",
    version="1.0.0",
    active=False,  # Dark mode — not wired into any strategy
))

# =============================================================================
# Dark-mode expansion indicators (active=False)
# Computed only when COMPUTE_DARK_INDICATORS=true env var is set.
# No live strategy consumes these without explicit walk-forward validation.
# =============================================================================

# Tier 1 — Stochastic Oscillator (14, 3)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("STOCH_K", 14), name="Stochastic %K (14, 3)", tier=1,
    inputs=["high", "low", "close"], params={"k_period": 14, "d_period": 3},
    implementation="FeatureFactory._stochastic",
    active=False,
))
_register(IndicatorDef(
    indicator_id=generate_indicator_id("STOCH_D", 14), name="Stochastic %D (14, 3)", tier=1,
    inputs=["high", "low", "close"], params={"k_period": 14, "d_period": 3},
    implementation="SMA(stoch_k, 3)",
    active=False,
))

# Tier 1 — Williams %R (14)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("WILLR", 14), name="Williams %R (14)", tier=1,
    inputs=["high", "low", "close"], params={"n": 14},
    implementation="(highest_high - close) / (highest_high - lowest_low) * -100",
    active=False,
))

# Tier 2 — ADX (Average Directional Index, 14)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("ADX", 14), name="ADX (14)", tier=2,
    inputs=["high", "low", "close"], params={"n": 14},
    implementation="FeatureFactory._adx",
    active=False,
))

# Tier 2 — OBV Slope (On-Balance Volume slope, ATR-normalised)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("OBV_SLOPE", 20, "ATR"),
    name="OBV Slope (20-bar, ATR-normalised)", tier=2,
    inputs=["close", "volume"], params={"n": 20},
    expression_type="slope",
    implementation="(OBV[-1] - OBV[-20]) / (20 * atr * mean_vol)",
    active=False,
))

# Tier 2 — MFI (Money Flow Index, 14)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("MFI", 14), name="Money Flow Index (14)", tier=2,
    inputs=["high", "low", "close", "volume"], params={"n": 14},
    implementation="FeatureFactory._mfi",
    active=False,
))

# Tier 2 — CCI (Commodity Channel Index, 20)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("CCI", 20), name="CCI (20)", tier=2,
    inputs=["high", "low", "close"], params={"n": 20},
    implementation="(TP - SMA(TP, 20)) / (0.015 * MeanDev(TP, 20))",
    active=False,
))

# Tier 2 — ROC (Rate of Change, 12)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("ROC", 12), name="Rate of Change (12)", tier=2,
    inputs=["close"], params={"n": 12},
    implementation="(close - close[-12]) / close[-12] * 100",
    active=False,
))

# Tier 2 — Chaikin Volatility (10-bar EMA of high-low range change)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("CHAIKIN_VOL", 10), name="Chaikin Volatility (10)", tier=2,
    inputs=["high", "low"], params={"n": 10},
    implementation="(EMA(H-L, 10) - EMA(H-L, 10)[-10]) / EMA(H-L, 10)[-10]",
    active=False,
))

# Tier 2 — Price Distance from EMA 20 (ATR-normalised, mean reversion signal)
_register(IndicatorDef(
    indicator_id=generate_indicator_id("PRICE_DIST_EMA20", "ATR"),
    name="Price Distance from EMA20 (ATR-norm)", tier=2,
    inputs=["close"], params={"n_ema": 20},
    expression_type="value",
    implementation="(close - ema20) / atr",
    active=False,
))

# Canon §1.6: Module-level uniqueness assertion — fail fast on ID collisions
assert len(INDICATOR_REGISTRY) == len(set(d.indicator_id for d in INDICATOR_REGISTRY.values())), \
    "Indicator ID collision detected in INDICATOR_REGISTRY!"


def get_indicator_library_fingerprint() -> str:
    """Return a fingerprint of the indicator library for run reproducibility."""
    import hashlib, json
    entries = sorted(
        (k, v.version, v.params) for k, v in INDICATOR_REGISTRY.items()
    )
    raw = json.dumps(entries, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class OHLCV:
    """Single candle."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class IndicatorSnapshot:
    """
    Complete indicator state for one symbol/timeframe at one point in time.
    This is what gets passed to setup evaluators.
    """
    # Tier 0 - Raw
    ohlcv: list[OHLCV]  # Ring buffer of candles (most recent last)
    
    # Tier 1 - Basic
    atr: float
    clv: float
    volume_z: float
    rsi: float
    rsi_sma: float
    rsi_slope: float
    
    # Tier 2 - MAs and Bands
    sma_50: float
    sma_200: float
    ema_9: float
    ema_20: float
    ema_21: float
    ema_50: float
    bb_mb: float  # BB middle (SMA20)
    bb_ub: float  # BB upper
    bb_lb: float  # BB lower
    bb_width: float
    kc_mb: float  # Keltner middle (EMA20)
    kc_ub: float  # Keltner upper
    kc_lb: float  # Keltner lower
    
    # Tier 2 - Slopes (ATR-normalised)
    sma50_slope_atr: float
    sma200_slope_atr: float
    
    # Tier 2 - MA Gap (for S2)
    ma_gap_atr: float  # abs(SMA50 - SMA200) / ATR
    
    # Tier 2 - EMA Slopes (ATR-normalised, 5-bar delta)
    ema20_slope_atr: float = 0.0
    ema50_slope_atr: float = 0.0

    # Tier 2 - SMA 20
    sma_20: float = 0.0

    # Tier 2 - MACD (12, 26, 9)
    macd_line: float = 0.0       # EMA(12) - EMA(26)
    macd_signal: float = 0.0     # EMA(9) of MACD line
    macd_histogram: float = 0.0  # macd_line - macd_signal
    macd_prev_histogram: float = 0.0  # previous bar's histogram (for crossover detection)

    # Relative volume ratio (current bar vol / 20-bar mean vol)
    rvol: float = 1.0

    # Tier 2 - Donchian Channels (20-bar)
    donchian_high_20: float = 0.0
    donchian_low_20: float = 0.0
    donchian_mid_20: float = 0.0

    # Tier 2 - VWAP (intraday, resets at 00:00 UTC)
    vwap: Optional[float] = None
    vwap_upper_2sd: Optional[float] = None
    vwap_lower_2sd: Optional[float] = None
    vwap_distance_atr: Optional[float] = None

    # L2 Proxy Features (dark mode — computed but not wired into any strategy)
    range_to_volume_ratio: float = 0.0   # (high - low) / volume — price impact proxy
    volume_acceleration: float = 1.0     # volume / SMA(volume, 20) — surge ratio

    # Dark-mode expansion indicators (only populated when COMPUTE_DARK_INDICATORS=true)
    stoch_k: Optional[float] = None       # Stochastic %K (14, 3)
    stoch_d: Optional[float] = None       # Stochastic %D (14, 3)
    williams_r: Optional[float] = None    # Williams %R (14)
    adx: Optional[float] = None           # ADX (14)
    obv_slope_atr: Optional[float] = None # OBV slope (20-bar, ATR-normalised)
    mfi: Optional[float] = None           # Money Flow Index (14)
    cci: Optional[float] = None           # CCI (20)
    roc: Optional[float] = None           # Rate of Change (12)
    chaikin_vol: Optional[float] = None   # Chaikin Volatility (10)
    price_dist_ema20_atr: Optional[float] = None  # (close - EMA20) / ATR

    # Tier 2 - Dormancy / Accumulation (for D1)
    dormancy_score: float = 0.0       # Fraction of trailing N bars with tight range
    accum_divergence: float = 0.0     # ADL slope vs price slope divergence

    # HTF proxies (optional, for bias)
    htf_close: Optional[float] = None
    htf_sma50: Optional[float] = None
    htf_bb_mb: Optional[float] = None


class FeatureFactory:
    """
    Computes indicators from OHLCV data.
    
    Usage:
        ff = FeatureFactory()
        snapshot = ff.compute(candles)  # candles = list of OHLCV, oldest first
    """
    
    def __init__(
        self,
        n_atr: int = 14,
        n_rsi: int = 14,
        n_rsi_sma: int = 14,
        n_bb: int = 20,
        k_bb: float = 2.0,
        n_kc: int = 20,
        kc_mult: float = 1.5,
        n_volz: int = 20,
    ):
        self.n_atr = n_atr
        self.n_rsi = n_rsi
        self.n_rsi_sma = n_rsi_sma
        self.n_bb = n_bb
        self.k_bb = k_bb
        self.n_kc = n_kc
        self.kc_mult = kc_mult
        self.n_volz = n_volz
    
    def compute(self, candles: list[OHLCV], htf_candles: Optional[list[OHLCV]] = None) -> IndicatorSnapshot:
        """
        Compute all indicators from candle history.
        
        Args:
            candles: List of OHLCV, oldest first, most recent last. Need ~250 bars minimum.
            htf_candles: Optional higher timeframe candles for bias (e.g., 4h if trading 1h)
        
        Returns:
            IndicatorSnapshot with all computed values
        """
        if len(candles) < 201:
            raise ValueError(f"Need at least 201 candles, got {len(candles)}")
        
        # Extract arrays
        opens = np.array([c.open for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])
        closes = np.array([c.close for c in candles])
        volumes = np.array([c.volume for c in candles])
        
        # Tier 1
        atr = self._atr(highs, lows, closes, self.n_atr)
        clv = self._clv(highs[-1], lows[-1], closes[-1])
        volume_z = self._volume_z(volumes, self.n_volz)
        rsi_series = self._rsi_series(closes, self.n_rsi)
        rsi = rsi_series[-1]
        rsi_sma = self._sma(rsi_series, self.n_rsi_sma)[-1]
        rsi_slope = rsi_series[-1] - rsi_series[-2] if len(rsi_series) >= 2 else 0.0
        
        # Tier 2 - MAs
        sma_50 = self._sma(closes, 50)[-1]
        sma_200 = self._sma(closes, 200)[-1]
        ema_9 = self._ema(closes, 9)[-1]
        ema_20 = self._ema(closes, 20)[-1]
        ema_21 = self._ema(closes, 21)[-1]
        ema_50 = self._ema(closes, 50)[-1]
        
        # Tier 2 - Bollinger Bands
        bb_mb = self._sma(closes, self.n_bb)[-1]
        bb_std = self._std(closes, self.n_bb)
        bb_ub = bb_mb + self.k_bb * bb_std
        bb_lb = bb_mb - self.k_bb * bb_std
        bb_width = (bb_ub - bb_lb) / bb_mb if bb_mb != 0 else 0.0
        
        # Tier 2 - Keltner Channels
        kc_mb = ema_20  # Using EMA20 as KC middle
        kc_ub = kc_mb + self.kc_mult * atr
        kc_lb = kc_mb - self.kc_mult * atr
        
        # Tier 2 - Slopes (ATR-normalised)
        sma50_series = self._sma(closes, 50)
        sma200_series = self._sma(closes, 200)
        sma50_slope_atr = (sma50_series[-1] - sma50_series[-2]) / atr if atr > 0 else 0.0
        sma200_slope_atr = (sma200_series[-1] - sma200_series[-2]) / atr if atr > 0 else 0.0
        
        # Tier 2 - MA Gap (for S2 Golden Cross)
        ma_gap_atr = abs(sma_50 - sma_200) / atr if atr > 0 else 0.0

        # Tier 2 - EMA Slopes (5-bar delta, ATR-normalised) (for S6)
        ema20_series = self._ema(closes, 20)
        ema50_series = self._ema(closes, 50)
        if atr > 0 and len(ema20_series) >= 6:
            ema20_slope_atr = (ema20_series[-1] - ema20_series[-6]) / (5 * atr)
            ema50_slope_atr = (ema50_series[-1] - ema50_series[-6]) / (5 * atr)
        else:
            ema20_slope_atr = 0.0
            ema50_slope_atr = 0.0

        # Tier 2 - SMA 20 (for S10 MACD Scalp)
        sma_20_series = self._sma(closes, 20)
        sma_20 = float(sma_20_series[-1])

        # Tier 2 - MACD (12, 26, 9) (for S10 MACD Scalp)
        ema_12_series = self._ema(closes, 12)
        ema_26_series = self._ema(closes, 26)
        macd_line_series = ema_12_series - ema_26_series
        macd_signal_series = self._ema(macd_line_series, 9)
        macd_line = float(macd_line_series[-1])
        macd_signal = float(macd_signal_series[-1])
        macd_histogram = macd_line - macd_signal
        macd_prev_histogram = float(macd_line_series[-2] - macd_signal_series[-2]) if len(macd_line_series) >= 2 else 0.0

        # Relative volume ratio (for S10 MACD Scalp)
        vol_mean_20 = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))
        rvol = float(volumes[-1] / vol_mean_20) if vol_mean_20 > 0 else 1.0

        # L2 Proxy Features (dark mode — computed but not wired into any strategy)
        # Range-to-Volume Ratio: (high - low) / volume. High values = thin book.
        range_to_volume_ratio = float(
            (highs[-1] - lows[-1]) / (volumes[-1] + 1e-10)
        )
        # Volume Acceleration: volume / SMA(volume, 20). Pure ratio without min threshold.
        volume_acceleration = float(volumes[-1] / vol_mean_20) if vol_mean_20 > 0 else 1.0

        # Tier 2 - Donchian Channels (20-bar) (for S7)
        donchian_high_20 = float(np.max(highs[-20:])) if len(highs) >= 20 else float(np.max(highs))
        donchian_low_20 = float(np.min(lows[-20:])) if len(lows) >= 20 else float(np.min(lows))
        donchian_mid_20 = (donchian_high_20 + donchian_low_20) / 2.0

        # Tier 2 - VWAP (intraday, resets at 00:00 UTC) (for S4)
        vwap, vwap_upper_2sd, vwap_lower_2sd = self._compute_vwap(candles)
        vwap_distance_atr = None
        if vwap is not None and atr > 0:
            vwap_distance_atr = (closes[-1] - vwap) / atr
        
        # Tier 2 - Dormancy Score + Accumulation Divergence (for D1)
        dormancy_score = self._dormancy_score(highs, lows, closes)
        accum_divergence = self._accumulation_divergence(highs, lows, closes, volumes)

        # HTF indicators (if provided)
        htf_close = None
        htf_sma50 = None
        htf_bb_mb = None
        if htf_candles and len(htf_candles) >= 50:
            htf_closes = np.array([c.close for c in htf_candles])
            htf_close = htf_closes[-1]
            htf_sma50 = self._sma(htf_closes, 50)[-1]
            htf_bb_mb = self._sma(htf_closes, 20)[-1]

        # Dark-mode expansion indicators (only computed when flag is set)
        stoch_k = None
        stoch_d = None
        williams_r = None
        adx_val = None
        obv_slope_atr = None
        mfi_val = None
        cci_val = None
        roc_val = None
        chaikin_vol_val = None
        price_dist_ema20_atr = None

        if COMPUTE_DARK_INDICATORS:
            stoch_k, stoch_d = self._stochastic(highs, lows, closes, 14, 3)
            williams_r = self._williams_r(highs, lows, closes, 14)
            adx_val = self._adx(highs, lows, closes, 14)
            obv_slope_atr = self._obv_slope(closes, volumes, 20, atr)
            mfi_val = self._mfi(highs, lows, closes, volumes, 14)
            cci_val = self._cci(highs, lows, closes, 20)
            roc_val = self._roc(closes, 12)
            chaikin_vol_val = self._chaikin_volatility(highs, lows, 10)
            price_dist_ema20_atr = (closes[-1] - ema_20) / atr if atr > 0 else None

        return IndicatorSnapshot(
            ohlcv=candles,
            atr=atr,
            clv=clv,
            volume_z=volume_z,
            rsi=rsi,
            rsi_sma=rsi_sma,
            rsi_slope=rsi_slope,
            sma_50=sma_50,
            sma_200=sma_200,
            ema_9=ema_9,
            ema_20=ema_20,
            ema_21=ema_21,
            ema_50=ema_50,
            bb_mb=bb_mb,
            bb_ub=bb_ub,
            bb_lb=bb_lb,
            bb_width=bb_width,
            kc_mb=kc_mb,
            kc_ub=kc_ub,
            kc_lb=kc_lb,
            sma50_slope_atr=sma50_slope_atr,
            sma200_slope_atr=sma200_slope_atr,
            ma_gap_atr=ma_gap_atr,
            ema20_slope_atr=ema20_slope_atr,
            ema50_slope_atr=ema50_slope_atr,
            sma_20=sma_20,
            macd_line=macd_line,
            macd_signal=macd_signal,
            macd_histogram=macd_histogram,
            macd_prev_histogram=macd_prev_histogram,
            rvol=rvol,
            range_to_volume_ratio=range_to_volume_ratio,
            volume_acceleration=volume_acceleration,
            donchian_high_20=donchian_high_20,
            donchian_low_20=donchian_low_20,
            donchian_mid_20=donchian_mid_20,
            vwap=vwap,
            vwap_upper_2sd=vwap_upper_2sd,
            vwap_lower_2sd=vwap_lower_2sd,
            vwap_distance_atr=vwap_distance_atr,
            dormancy_score=dormancy_score,
            accum_divergence=accum_divergence,
            htf_close=htf_close,
            htf_sma50=htf_sma50,
            htf_bb_mb=htf_bb_mb,
            stoch_k=stoch_k,
            stoch_d=stoch_d,
            williams_r=williams_r,
            adx=adx_val,
            obv_slope_atr=obv_slope_atr,
            mfi=mfi_val,
            cci=cci_val,
            roc=roc_val,
            chaikin_vol=chaikin_vol_val,
            price_dist_ema20_atr=price_dist_ema20_atr,
        )
    
    # -------------------------------------------------------------------------
    # Tier 1 calculations
    # -------------------------------------------------------------------------
    
    def _atr(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, n: int) -> float:
        """Average True Range."""
        if len(closes) < 2:
            return highs[-1] - lows[-1]
        
        tr = np.zeros(len(closes))
        tr[0] = highs[0] - lows[0]
        for i in range(1, len(closes)):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
        
        # Use SMA for ATR
        if len(tr) < n:
            return float(np.mean(tr))
        return float(np.mean(tr[-n:]))
    
    def _clv(self, high: float, low: float, close: float) -> float:
        """Close Location Value: where close is within the bar's range."""
        if high == low:
            return 0.5
        return (close - low) / (high - low)
    
    def _volume_z(self, volumes: np.ndarray, n: int) -> float:
        """Volume Z-score."""
        if len(volumes) < n:
            return 0.0
        
        window = volumes[-n:]
        mean = np.mean(window)
        std = np.std(window)
        
        if std == 0:
            return 0.0
        
        return float((volumes[-1] - mean) / std)
    
    def _rsi_series(self, closes: np.ndarray, n: int) -> np.ndarray:
        """RSI series (returns full array for slope calculation)."""
        if len(closes) < n + 1:
            return np.full(len(closes), 50.0)
        
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        
        # Wilder's smoothed averages
        avg_gain = np.zeros(len(deltas))
        avg_loss = np.zeros(len(deltas))
        
        # Initial SMA
        avg_gain[n - 1] = np.mean(gains[:n])
        avg_loss[n - 1] = np.mean(losses[:n])
        
        # Smoothed moving average
        for i in range(n, len(deltas)):
            avg_gain[i] = (avg_gain[i - 1] * (n - 1) + gains[i]) / n
            avg_loss[i] = (avg_loss[i - 1] * (n - 1) + losses[i]) / n
        
        # RSI
        rsi = np.full(len(closes), 50.0)
        for i in range(n, len(closes)):
            if avg_loss[i - 1] == 0:
                rsi[i] = 100.0
            else:
                rs = avg_gain[i - 1] / avg_loss[i - 1]
                rsi[i] = 100.0 - (100.0 / (1.0 + rs))
        
        return rsi
    
    # -------------------------------------------------------------------------
    # Tier 2 calculations
    # -------------------------------------------------------------------------
    
    def _sma(self, data: np.ndarray, n: int) -> np.ndarray:
        """Simple Moving Average (returns full series)."""
        if len(data) < n:
            return np.full(len(data), np.mean(data))
        
        result = np.zeros(len(data))
        result[:n - 1] = np.nan
        
        for i in range(n - 1, len(data)):
            result[i] = np.mean(data[i - n + 1:i + 1])
        
        # Fill NaN with first valid value
        first_valid = result[n - 1]
        result[:n - 1] = first_valid
        
        return result
    
    def _ema(self, data: np.ndarray, n: int) -> np.ndarray:
        """Exponential Moving Average (returns full series)."""
        if len(data) < n:
            return np.full(len(data), np.mean(data))
        
        alpha = 2.0 / (n + 1)
        result = np.zeros(len(data))
        
        # Seed with SMA
        result[n - 1] = np.mean(data[:n])
        
        for i in range(n, len(data)):
            result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
        
        # Fill early values
        result[:n - 1] = result[n - 1]
        
        return result
    
    def _compute_vwap(self, candles: list) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Compute VWAP with ±2σ bands, resetting at 00:00 UTC daily.

        Returns (vwap, vwap_upper_2sd, vwap_lower_2sd) or (None, None, None)
        if insufficient data for the current session.
        """
        if not candles:
            return None, None, None

        # Find session start: walk backwards from the last candle to find
        # the first candle at or after 00:00 UTC of the last candle's day
        from datetime import datetime, timezone
        last_ts_ms = candles[-1].timestamp
        last_dt = datetime.fromtimestamp(last_ts_ms / 1000, tz=timezone.utc)
        session_start = last_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        session_start_ms = int(session_start.timestamp() * 1000)

        # Collect candles in the current session
        session_candles = [c for c in candles if c.timestamp >= session_start_ms]
        if len(session_candles) < 2:
            return None, None, None

        # Typical Price = (H + L + C) / 3
        tp_arr = np.array([(c.high + c.low + c.close) / 3.0 for c in session_candles])
        vol_arr = np.array([c.volume for c in session_candles])

        cum_tp_vol = np.cumsum(tp_arr * vol_arr)
        cum_vol = np.cumsum(vol_arr)

        # Avoid division by zero
        if cum_vol[-1] == 0:
            return None, None, None

        vwap_series = np.divide(cum_tp_vol, cum_vol, out=np.zeros_like(cum_tp_vol), where=cum_vol > 0)

        vwap = float(vwap_series[-1])

        # VWAP standard deviation: sqrt(cumulative((TP - VWAP)^2 * V) / cumV)
        vwap_broadcast = cum_tp_vol / np.where(cum_vol > 0, cum_vol, 1)
        sq_diff = (tp_arr - vwap_broadcast) ** 2
        cum_sq_vol = np.cumsum(sq_diff * vol_arr)
        vwap_var = cum_sq_vol[-1] / cum_vol[-1] if cum_vol[-1] > 0 else 0.0
        vwap_std = float(np.sqrt(vwap_var))

        vwap_upper_2sd = vwap + 2.0 * vwap_std
        vwap_lower_2sd = vwap - 2.0 * vwap_std

        return vwap, vwap_upper_2sd, vwap_lower_2sd

    def _dormancy_score(self, highs: np.ndarray, lows: np.ndarray,
                         closes: np.ndarray, n: int = 48,
                         threshold: float = 0.010) -> float:
        """Fraction of trailing N bars where (high - low) / close < threshold.

        EXCLUDES the current bar. Dormancy describes the regime you're leaving,
        not the bar you're on. The breakout bar is non-dormant by definition —
        including it would cause the score to drop below threshold on the exact
        bar the trigger should fire.

        Range: 0.0 (active / wide ranges) to 1.0 (completely dormant / flatline).
        Default threshold 1.0% calibrated from altcoin data: at 0.1% only pegged
        assets qualify; at 1.0%, 83% of altcoins exhibit dormancy periods.
        """
        # Need at least n+1 bars: n historical + 1 current (excluded)
        if len(closes) < 2:
            return 0.0
        if len(closes) - 1 < n:
            n = len(closes) - 1
        if n == 0:
            return 0.0

        # Bars BEFORE the current bar: [-(n+1):-1]
        h = highs[-(n+1):-1]
        l = lows[-(n+1):-1]
        c = closes[-(n+1):-1]

        # Avoid division by zero
        safe_c = np.where(c > 0, c, 1.0)
        ranges = (h - l) / safe_c
        dormant_count = np.sum(ranges < threshold)
        return float(dormant_count / n)

    def _accumulation_divergence(self, highs: np.ndarray, lows: np.ndarray,
                                  closes: np.ndarray, volumes: np.ndarray,
                                  n: int = 20) -> float:
        """ADL slope vs price slope divergence over trailing N bars.

        CLV = ((C - L) - (H - C)) / (H - L)  [clamped to avoid div-by-zero]
        ADL = cumsum(CLV * volume)
        Returns: normalized divergence (ADL slope - price slope).
        Positive = accumulation (ADL rising while price flat/falling).
        Normalized by avg_price * avg_volume for cross-asset comparability.
        """
        if len(closes) < n:
            return 0.0

        h = highs[-n:]
        l = lows[-n:]
        c = closes[-n:]
        v = volumes[-n:]

        # CLV: ((C-L) - (H-C)) / (H-L)
        hl_range = h - l
        # Avoid division by zero for flat bars
        safe_range = np.where(hl_range > 0, hl_range, 1.0)
        clv = ((c - l) - (h - c)) / safe_range
        # Zero out CLV where range was zero (truly flat bar)
        clv = np.where(hl_range > 0, clv, 0.0)

        # ADL = cumulative sum of CLV * volume
        adl = np.cumsum(clv * v)

        # Slopes via linear regression (index as x)
        x = np.arange(n, dtype=float)
        x_mean = x.mean()
        x_var = np.sum((x - x_mean) ** 2)
        if x_var == 0:
            return 0.0

        adl_slope = np.sum((x - x_mean) * (adl - adl.mean())) / x_var
        price_slope = np.sum((x - x_mean) * (c - c.mean())) / x_var

        # Normalize: both slopes by avg_price * avg_volume
        avg_price = np.mean(c)
        avg_volume = np.mean(v)
        normalizer = avg_price * avg_volume if (avg_price > 0 and avg_volume > 0) else 1.0

        norm_adl_slope = adl_slope / normalizer
        norm_price_slope = price_slope / (avg_price if avg_price > 0 else 1.0)

        # Divergence: ADL rising relative to price
        return float(norm_adl_slope - norm_price_slope)

    def _std(self, data: np.ndarray, n: int) -> float:
        """Rolling standard deviation (last value only)."""
        if len(data) < n:
            return float(np.std(data))
        return float(np.std(data[-n:]))

    # -------------------------------------------------------------------------
    # Dark-mode indicator calculations
    # -------------------------------------------------------------------------

    def _stochastic(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                    k_period: int = 14, d_period: int = 3) -> tuple[float, float]:
        """Stochastic Oscillator %K and %D."""
        if len(closes) < k_period:
            return 50.0, 50.0

        hh = np.max(highs[-k_period:])
        ll = np.min(lows[-k_period:])
        denom = hh - ll
        if denom <= 0:
            k = 50.0
        else:
            k = float((closes[-1] - ll) / denom * 100)

        # %D = SMA of %K over d_period (approximate with current bar only)
        # For proper %D, compute %K series then SMA
        if len(closes) < k_period + d_period:
            return k, k

        k_series = []
        for i in range(d_period):
            idx = -(d_period - i)
            end = len(highs) + idx + 1 if idx < -1 else len(highs)
            start = end - k_period
            if start < 0:
                k_series.append(50.0)
                continue
            hh_i = np.max(highs[start:end])
            ll_i = np.min(lows[start:end])
            d_i = hh_i - ll_i
            if d_i <= 0:
                k_series.append(50.0)
            else:
                k_series.append((closes[end - 1] - ll_i) / d_i * 100)

        d = float(np.mean(k_series))
        return k, d

    def _williams_r(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                    n: int = 14) -> float:
        """Williams %R."""
        if len(closes) < n:
            return -50.0

        hh = np.max(highs[-n:])
        ll = np.min(lows[-n:])
        denom = hh - ll
        if denom <= 0:
            return -50.0
        return float((hh - closes[-1]) / denom * -100)

    def _adx(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             n: int = 14) -> float:
        """Average Directional Index."""
        if len(closes) < n * 2:
            return 25.0  # neutral default

        # +DM / -DM
        up_move = np.diff(highs)
        down_move = -np.diff(lows)
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # True Range
        tr = np.zeros(len(closes) - 1)
        for i in range(len(tr)):
            tr[i] = max(
                highs[i + 1] - lows[i + 1],
                abs(highs[i + 1] - closes[i]),
                abs(lows[i + 1] - closes[i])
            )

        # Wilder smoothing
        atr_smooth = np.zeros(len(tr))
        plus_di_smooth = np.zeros(len(tr))
        minus_di_smooth = np.zeros(len(tr))

        atr_smooth[n - 1] = np.mean(tr[:n])
        plus_di_smooth[n - 1] = np.mean(plus_dm[:n])
        minus_di_smooth[n - 1] = np.mean(minus_dm[:n])

        for i in range(n, len(tr)):
            atr_smooth[i] = (atr_smooth[i - 1] * (n - 1) + tr[i]) / n
            plus_di_smooth[i] = (plus_di_smooth[i - 1] * (n - 1) + plus_dm[i]) / n
            minus_di_smooth[i] = (minus_di_smooth[i - 1] * (n - 1) + minus_dm[i]) / n

        # +DI / -DI
        safe_atr = np.where(atr_smooth > 0, atr_smooth, 1.0)
        plus_di = 100 * plus_di_smooth / safe_atr
        minus_di = 100 * minus_di_smooth / safe_atr

        # DX
        di_sum = plus_di + minus_di
        safe_di_sum = np.where(di_sum > 0, di_sum, 1.0)
        dx = 100 * np.abs(plus_di - minus_di) / safe_di_sum

        # ADX = Wilder smoothed DX
        if len(dx) < 2 * n:
            return float(np.mean(dx[n:]) if len(dx) > n else 25.0)

        adx_series = np.zeros(len(dx))
        adx_series[2 * n - 2] = np.mean(dx[n - 1:2 * n - 1])
        for i in range(2 * n - 1, len(dx)):
            adx_series[i] = (adx_series[i - 1] * (n - 1) + dx[i]) / n

        return float(adx_series[-1])

    def _obv_slope(self, closes: np.ndarray, volumes: np.ndarray,
                   n: int = 20, atr: float = 1.0) -> float:
        """OBV slope over n bars, normalised by ATR and mean volume."""
        if len(closes) < n + 1:
            return 0.0

        # OBV for last n+1 bars
        c = closes[-(n + 1):]
        v = volumes[-(n + 1):]
        direction = np.sign(np.diff(c))
        obv = np.cumsum(direction * v[1:])

        slope = (obv[-1] - obv[0]) / n
        mean_vol = np.mean(v) if np.mean(v) > 0 else 1.0
        normalizer = atr * mean_vol if atr > 0 else mean_vol
        return float(slope / normalizer)

    def _mfi(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             volumes: np.ndarray, n: int = 14) -> float:
        """Money Flow Index."""
        if len(closes) < n + 1:
            return 50.0

        tp = (highs + lows + closes) / 3
        raw_mf = tp * volumes

        pos_mf = 0.0
        neg_mf = 0.0
        for i in range(-n, 0):
            if tp[i] > tp[i - 1]:
                pos_mf += raw_mf[i]
            elif tp[i] < tp[i - 1]:
                neg_mf += raw_mf[i]

        if neg_mf == 0:
            return 100.0
        mfr = pos_mf / neg_mf
        return float(100 - 100 / (1 + mfr))

    def _cci(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             n: int = 20) -> float:
        """Commodity Channel Index."""
        if len(closes) < n:
            return 0.0

        tp = (highs[-n:] + lows[-n:] + closes[-n:]) / 3
        tp_mean = np.mean(tp)
        mean_dev = np.mean(np.abs(tp - tp_mean))
        if mean_dev == 0:
            return 0.0
        return float((tp[-1] - tp_mean) / (0.015 * mean_dev))

    def _roc(self, closes: np.ndarray, n: int = 12) -> float:
        """Rate of Change (percentage)."""
        if len(closes) < n + 1:
            return 0.0
        prev = closes[-(n + 1)]
        if prev == 0:
            return 0.0
        return float((closes[-1] - prev) / prev * 100)

    def _chaikin_volatility(self, highs: np.ndarray, lows: np.ndarray, n: int = 10) -> float:
        """Chaikin Volatility: % change in EMA of high-low range."""
        if len(highs) < 2 * n:
            return 0.0

        hl_range = highs - lows
        ema_hl = self._ema(hl_range, n)

        if len(ema_hl) < n + 1:
            return 0.0

        prev = ema_hl[-(n + 1)]
        if prev == 0:
            return 0.0
        return float((ema_hl[-1] - prev) / prev * 100)


# -----------------------------------------------------------------------------
# Utility functions for setup evaluators
# -----------------------------------------------------------------------------

def percentile_rank(value: float, history: list[float]) -> float:
    """
    Compute percentile rank of value within history.
    Returns 0-100.
    """
    if not history:
        return 50.0
    
    count_below = sum(1 for h in history if h < value)
    return 100.0 * count_below / len(history)


def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp value to range."""
    return max(min_val, min(max_val, value))


def find_pivot_lows(lows: np.ndarray, n_left: int = 1, n_right: int = 1) -> list[tuple[int, float]]:
    """
    Find pivot lows in price series.
    Returns list of (index, price) tuples.
    
    For realtime (no lookahead): use n_right=0 and check previous bars only.
    """
    pivots = []
    for i in range(n_left, len(lows) - n_right):
        is_pivot = True
        for j in range(1, n_left + 1):
            if lows[i] >= lows[i - j]:
                is_pivot = False
                break
        if is_pivot and n_right > 0:
            for j in range(1, n_right + 1):
                if lows[i] >= lows[i + j]:
                    is_pivot = False
                    break
        if is_pivot:
            pivots.append((i, float(lows[i])))
    return pivots


def find_pivot_highs(highs: np.ndarray, n_left: int = 1, n_right: int = 1) -> list[tuple[int, float]]:
    """
    Find pivot highs in price series.
    Returns list of (index, price) tuples.
    """
    pivots = []
    for i in range(n_left, len(highs) - n_right):
        is_pivot = True
        for j in range(1, n_left + 1):
            if highs[i] <= highs[i - j]:
                is_pivot = False
                break
        if is_pivot and n_right > 0:
            for j in range(1, n_right + 1):
                if highs[i] <= highs[i + j]:
                    is_pivot = False
                    break
        if is_pivot:
            pivots.append((i, float(highs[i])))
    return pivots
