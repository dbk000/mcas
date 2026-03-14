"""
Governed Config Definitions — TypedConfig dataclasses only.
============================================================

All dataclasses define the shape of governed configuration.
Actual population (os.getenv) happens exclusively in governance/bootstrap.py.

No registry classes here. The FrozenRegistry (ConfigRegistry) in
governance/bootstrap.py is the sole authority for config storage/retrieval.
"""

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Configs
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class D1TypedConfig:
    # Seal-deterministic: tuples (sorted) instead of sets
    MAJOR_SYMBOLS: tuple = field(default_factory=lambda: tuple(sorted(["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT", "LTCUSDT", "TRXUSDT", "ATOMUSDT"])))
    STABLECOIN_BASES: tuple = field(default_factory=lambda: tuple(sorted(["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD"])))
    WRAPPED_PATTERNS: list = field(default_factory=lambda: ["WBTC", "WETH", "WBNB"])
    LEVERAGE_PATTERNS: list = field(default_factory=lambda: ["UP", "DOWN", "BULL", "BEAR", "3L", "3S"])

    MIN_VOLUME_USD: float = 75_000.0
    MAX_VOLUME_USD: float = 3_000_000.0

    DORMANCY_RANGE_THRESHOLD: float = 0.010
    DORMANCY_THRESHOLD: float = 0.75
    BB_WIDTH_COMPRESS: float = 0.05
    DORMANCY_REQUIRE_NEG_VOLUME_Z: bool = False
    ACCUM_DIVERGENCE_MIN: float = 0.0
    REQUIRE_ACCUM_DIVERGENCE_GATE: bool = False
    TRIGGER_REQUIRE_DORMANCY: bool = True
    TRIGGER_REQUIRE_COMPRESSION: bool = True

    VOLUME_Z_SPIKE: float = 2.0
    RVOL_MIN: float = 3.0
    STOP_PCT: float = 0.015
    TP1_PCT: float = 0.10
    TP2_PCT: float = 0.20
    TIME_STOP_BARS_1H: int = 72
    COOLDOWN_BARS_1H: int = 24

    SOCIAL_SILENCE_LOOKBACK_HOURS: int = 24
    SOCIAL_SILENCE_MAX_MENTIONS: int = 3
    SOCIAL_SILENCE_LOOKBACK_DAYS: int = 30

    ONCHAIN_GATE_ENABLED: bool = False
    ONCHAIN_NETFLOW_LOOKBACK_HOURS: int = 48
    W_ONCHAIN_FLOW: float = 0.0

    W_DORMANCY: float = 0.20
    W_VOL_Z: float = 0.25
    W_ACCUM_DIV: float = 0.15
    W_BB_EXPANSION: float = 0.15
    W_RVOL: float = 0.05
    W_BTC_AD_RET: float = 0.10
    W_SOCIAL_SILENCE: float = 0.10

    DORMANCY_LOOKBACK: int = 48
    BTC_BETA_WINDOW: int = 20


@dataclass
class U1TypedConfig:
    MIN_VOLUME_USD: float = 75_000.0
    MAX_VOLUME_USD: float = 3_000_000.0
    MAX_MIN_NOTIONAL: int = 100
    HYPE_REL_VOL_CAP: float = 20.0
    HYPE_RANGE_CAP: float = 0.50
    STABLECOIN_BASES: tuple = field(default_factory=lambda: tuple(sorted(["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD"])))
    WRAPPED_PATTERNS: list = field(default_factory=lambda: ["WBTC", "WETH", "WBNB"])
    LEVERAGE_PATTERNS: list = field(default_factory=lambda: ["UP", "DOWN", "BULL", "BEAR", "3L", "3S"])

    MAJOR_SYMBOLS: tuple = field(default_factory=lambda: tuple(sorted(["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT", "LTCUSDT", "TRXUSDT", "ATOMUSDT"])))

    GATE_WINDOW_BARS: int = 48
    GATE_TIMEFRAME: str = "5m"
    TRADABILITY_IMPROVEMENT_FACTOR: float = 0.85
    PARTICIPATION_GROWTH_FACTOR: float = 1.2
    RANGE_PCT_THRESHOLD: float = 0.10
    REL_VOL_IGNITION_MIN: float = 3.0

    BASELINE_WINDOWS: list = field(default_factory=lambda: [
        ("7d", 2016),
        ("3d", 864),
        ("1d", 288),
        ("288_bars", 288),
        ("48_bars", 48),
    ])

    COMPRESSION_BOX_LOOKBACK: int = 48
    REL_VOL_IGNITION: float = 3.0
    BREAKOUT_ACCEPTANCE_BARS: int = 2
    VOL_Z_MIN: float = 1.5
    IGNITION_SCORE_THRESHOLD: float = 0.40

    W_VOL_Z: float = 0.25
    W_VOL_Z_MAX_6: float = 0.15
    W_VOL_SLOPE: float = 0.10
    W_BB_COMPRESS: float = 0.0
    W_RANGE_PCT: float = 0.10
    W_RETURN_12: float = 0.10
    W_CLV: float = 0.05
    W_BODY_RATIO: float = 0.04
    W_VOL_Z_FLOOR: float = 0.06

    W_UNIVERSE_TEMP: float = 0.15

    SPREAD_WIDENING_THRESHOLD: float = 1.2

    TIME_STOP_BARS_5M: int = 12
    TIME_STOP_BARS_15M: int = 8
    TIME_STOP_BARS_1H: int = 4

    MAX_EMBRYO_ACTIVE: int = 200
    T_ACTIVE_MAX_HOURS: int = 336

    W_TRADABILITY: float = 0.25
    W_PARTICIPATION: float = 0.30
    W_COMPRESSION: float = 0.30
    W_TRAP_RISK: float = 0.15

    STOP_ATR_BUFFER: float = 1.0
    STOP_RANGE_MULT: float = 3.0
    MIN_STOP_PCT: float = 0.005
    TP1_R: float = 1.0
    TP2_R: float = 2.0

    VOLUME_Z_1D_MIN: float = 4.0
    BB_WIDTH_MIN_5M: float = 0.07
    BB_WIDTH_MIN_15M: float = 0.10

    REVERSION_PC_4H: float = -0.0065
    REVERSION_PC_1H: float = -0.0048
    REVERSION_BB_POS: float = 0.258
    REVERSION_SCORE_WEIGHT: float = 0.05

    SOCIAL_SILENCE_FILTER: bool = False
    EXPRESS_LANE: bool = True
    MIN_CONFIRMATIONS: int = 1


@dataclass
class S9TypedConfig:
    RET_LOOKBACK: int = 4
    HOLD_BARS: int = 48
    PERCENTILE: int = 10
    BREADTH_THRESHOLD: float = 0.3
    COST_BPS: int = 18


@dataclass
class S10TypedConfig:
    RVOL_MIN: float = 2.0
    FLAT_TOP_LOOKBACK: int = 7
    FLAT_TOP_TOLERANCE_ATR: float = 0.3
    FLAT_TOP_MIN_TOUCHES: int = 2
    SMA20_PROXIMITY_ATR: float = 2.5
    STOP_ATR_MULT: float = 1.2
    TP1_R_MULT: float = 1.5
    TP2_R_MULT: float = 2.0
    TIME_STOP_BARS: int = 12


# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure Configs
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkerTypedConfig:
    REDIS_URL: str = "redis://redis:6379/0"
    DATABASE_URL: str = ""
    WORKER_INDEX: int = 0
    N_WORKERS: int = 1
    LOG_LEVEL: str = "INFO"
    SYMBOL_QUALITY_FILTER: bool = False
    SIGNAL_TOP_N_DAILY: int = 10
    CAPACITY_THRESHOLD_USD: float = 1_000_000.0
    U1_SCORE_MULT: float = 1.0
    S9_SCORE_MULT: float = 1.0
    S10_SCORE_MULT: float = 1.0
    D1_SCORE_MULT: float = 1.0
    CONFIG_POLL_INTERVAL_S: float = 10.0
    BINANCE_TICKER_URL: str = ""
    BINANCE_KLINES_URL: str = ""
    OUTPUT_DIR: str = "./output"
    MAX_CONCURRENCY: int = 8
    DXY_REGIME_ENABLED: bool = True

    # Path Configuration
    CORRELATION_JSON_PATH: str = "indicator_correlations.json"
    CORRELATION_TXT_PATH: str = "indicator_correlations.txt"
    REGIME_SUITABILITY_JSON_PATH: str = "regime_suitability.json"
    REGIME_SUITABILITY_CSV_PATH: str = "regime_suitability.csv"
    REGIME_SUITABILITY_TXT_PATH: str = "regime_suitability.txt"

    # Infrastructure (migrated from scattered os.getenv)
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 10
    DB_POOL_TIMEOUT: float = 30.0
    SERVICE_NAME: str = "unknown"
    ENGINE_VERSION: str = "1.0.0"
    KNOWN_UNIVERSE_SIZE: int = 1024
    SETUP_IDS: str = "S9,U1,D1"
    SCAN_INTERVAL_SECONDS: int = 300
    USE_DYNAMIC_SYMBOLS: bool = False
    ENABLE_PERIODIC_SCANS: bool = True
    ENABLE_POSITION_MONITORING: bool = True
    DEFAULT_TIMEFRAMES: str = "1m,5m,15m,1h,4h,1d"
    MIN_24H_QUOTE_VOLUME_USD: float = 5_000_000.0
    MAX_SOCIAL_PROMOTIONS_PER_CYCLE: int = 0
    MAX_SOCIAL_PROMOTIONS_ACTIVE: int = 0
    SEED_WORKERS: int = 20
    BINANCE_API_V3_BASE: str = "https://api.binance.com/api/v3"
    BINANCE_API_BASE: str = "https://api.binance.com"
    BINANCE_FAPI_URL: str = "https://fapi.binance.com"
    UNICORN_PIPELINE_INTERVAL_S: int = 300
    BACKFILL_RATE_PER_SEC: float = 6.0
    CORS_ORIGINS: str = ""
    API_KEY: str = ""


@dataclass
class IndicatorTypedConfig:
    COMPUTE_DARK_INDICATORS: bool = False


@dataclass
class SimulationFillsConfig:
    stop_fill: Optional[str] = None
    target_fill: Optional[str] = None
    intrabar_policy: str = "STOP_FIRST"
    one_event_per_bar: bool = True


@dataclass
class SimulationConfig:
    ENGINE_VERSION: str = "1.0.0"
    fills: SimulationFillsConfig = field(default_factory=SimulationFillsConfig)
    backfill_degraded: bool = False

    SLIPPAGE_BPS: float = 0.0
    FEE_BPS: float = 0.0
    LATENCY_MS: int = 0


@dataclass(frozen=True)
class D2TypedConfig:
    """D2 (Fade the Casino) configuration."""
    entry_threshold: float = 0.25
    stop_pct: float = 0.15
    time_stop_hours: int = 72
    max_watch_hours: int = 48
    mania_gate_pct: float = 40.0
    liquidity_gate_usd: float = 1_000_000.0
    effective_risk_pct: float = 18.76
    funding_gate_enabled: bool = False
    conflict_gate_enabled: bool = False
    fng_soft_gate: str = "log_only"
    candle_interval: str = "1m"
    poll_interval_s: int = 60
    cluster_poll_s: int = 30
    max_concurrent: int = 3
    risk_per_trade_pct: float = 2.0
    account_size_usd: float = 10_000.0
