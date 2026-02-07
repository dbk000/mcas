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

import re
import numpy as np
from dataclasses import dataclass, field
from typing import Optional


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
        
        # HTF indicators (if provided)
        htf_close = None
        htf_sma50 = None
        htf_bb_mb = None
        if htf_candles and len(htf_candles) >= 50:
            htf_closes = np.array([c.close for c in htf_candles])
            htf_close = htf_closes[-1]
            htf_sma50 = self._sma(htf_closes, 50)[-1]
            htf_bb_mb = self._sma(htf_closes, 20)[-1]
        
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
            htf_close=htf_close,
            htf_sma50=htf_sma50,
            htf_bb_mb=htf_bb_mb,
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
    
    def _std(self, data: np.ndarray, n: int) -> float:
        """Rolling standard deviation (last value only)."""
        if len(data) < n:
            return float(np.std(data))
        return float(np.std(data[-n:]))


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
