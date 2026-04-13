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
    W_BTC_ADJ_RET: float = 0.10
    W_SOCIAL_SILENCE: float = 0.10

    DORMANCY_LOOKBACK: int = 48
    BOOTSTRAP_WARMUP_MULTIPLIER: float = 1.5
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
    # Magnitude gate: minimum 4h return to qualify as outlier pump
    MIN_RET_4H: float = 0.05
    # FnG regime gate (moved from WorkerTypedConfig)
    FNG_GATE_ENABLED: bool = True
    FNG_MAX_VALUE: int = 50
    # Canonical concurrency cap — perp rerank Phase 0 (s9_perp_rerank_findings.md)
    # produced daily PF 3.738 at cap=15 on the perp-only universe. This is the
    # single source of truth; paper/live ExecutionConfig.max_positions must match.
    MAX_CONCURRENT: int = 15
    # Canonical symbol universe — Binance USDT-M perpetual-eligible symbols.
    # Loaded by bootstrap and applied by signal_worker._evaluate_s9_cross_sectional
    # and regenerate_opportunities.run_s9. Path is resolved relative to /backend
    # inside the container (COPY'd by Dockerfile.computers) or repo root on host.
    SYMBOL_WHITELIST_PATH: str = "/backend/config/s9_symbol_whitelist.txt"


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


@dataclass
class C1TypedConfig:
    """C1 Golden Cross — SMA50/200 crossover trend following."""
    VOLUME_Z_MIN: float = 1.0
    STOP_ATR_MULT: float = 2.0
    TP1_ATR_MULT: float = 2.0
    TP2_ATR_MULT: float = 4.0
    TIME_STOP_BARS: int = 48


@dataclass
class C2TypedConfig:
    """C2 Support Bounce — buy near Donchian-20 support."""
    SUPPORT_PROXIMITY_ATR: float = 0.5
    RSI_MAX: float = 40.0
    VOLUME_Z_MIN: float = 0.5
    STOP_ATR_BELOW_SUPPORT: float = 0.5
    TIME_STOP_BARS: int = 24


@dataclass
class C3TypedConfig:
    """C3 Breakout — Donchian resistance break after BB compression."""
    VOLUME_Z_MIN: float = 2.0
    BB_WIDTH_PCTILE_MAX: float = 25.0
    BB_WIDTH_LOOKBACK: int = 50
    STOP_ATR_MULT: float = 1.0
    TP1_ATR_MULT: float = 1.5
    TP2_ATR_MULT: float = 3.0
    TIME_STOP_BARS: int = 24


@dataclass
class C4TypedConfig:
    """C4 RSI Reversal — oversold bounce near BB lower band."""
    RSI_THRESHOLD: float = 30.0
    BB_PROXIMITY_ATR: float = 1.0
    VOLUME_Z_MIN: float = 0.5
    STOP_ATR_MULT: float = 1.5
    TIME_STOP_BARS: int = 18


@dataclass
class C5TypedConfig:
    """C5 Volume Scalp — EMA + VWAP alignment on volume spike."""
    VOLUME_Z_MIN: float = 2.5
    RVOL_MIN: float = 2.0
    STOP_ATR_MULT: float = 1.0
    TP1_ATR_MULT: float = 1.0
    TP2_ATR_MULT: float = 1.5
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
    SIGNAL_TOP_N_DAILY_PER_SETUP: int = 50
    CAPACITY_THRESHOLD_USD: float = 1_000_000.0
    U1_SCORE_MULT: float = 1.0
    S9_SCORE_MULT: float = 1.0
    S10_SCORE_MULT: float = 1.0
    S11_SCORE_MULT: float = 1.0
    D1_SCORE_MULT: float = 1.0
    C1_SCORE_MULT: float = 1.0
    C2_SCORE_MULT: float = 1.0
    C3_SCORE_MULT: float = 1.0
    C4_SCORE_MULT: float = 1.0
    C5_SCORE_MULT: float = 1.0
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
    SETUP_IDS: str = "S9,U1,D1,S11"
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

    # Public feed
    PUBLIC_FEED_CACHE_TTL_S: int = 10
    PUBLIC_FEED_MAX_EVENTS: int = 50

    # Pipeline activity heartbeat
    ACTIVITY_SAMPLE_EVERY: int = 10


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
class TraceTypedConfig:
    schema_version: str = "1.0"


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


@dataclass
class AlertTypedConfig:
    """Alert watchdog configuration. Governs staleness thresholds and webhook delivery."""
    SIGNAL_STALE_HOURS: int = 6
    WEBHOOK_URL: str = ""


@dataclass(frozen=True)
class PMTypedConfig:
    """Polymarket prediction market bot configuration."""
    # LLM provider keys (validated in bootstrap — required when paper_mode=False)
    groq_api_key: str = ""
    gemini_api_key: str = ""

    # Model cutoff dates (ISO format) — used by classify_info_freshness()
    model_cutoff_gemini: str = "2025-01-01"
    model_cutoff_qwen3: str = "2025-03-01"
    model_cutoff_llama31: str = "2024-12-01"

    # Scanning
    scan_interval_s: int = 3600
    min_volume_24h: float = 50_000.0
    min_liquidity: float = 10_000.0
    min_days_to_resolution: int = 2
    max_days_to_resolution: int = 90
    re_estimate_cooldown_hours: int = 24

    # Complexity routing thresholds (T1/T2/T3 classification)
    complexity_description_len_t1: int = 500
    complexity_description_len_t2: int = 200
    complexity_categories_t1: tuple = field(default_factory=lambda: tuple(sorted([
        "politics", "geopolitics", "legal", "regulation"
    ])))

    # Edge
    min_executable_edge_pct: float = 8.0
    consensus_edge_pct: float = 15.0

    # Sizing
    bankroll_usd: float = 10_000.0
    kelly_fraction: float = 0.25
    max_position_pct: float = 5.0
    max_category_exposure_pct: float = 15.0
    holdout_pct: float = 20.0

    # Operational
    poll_interval_s: int = 300
    monitor_interval_s: int = 14400
    resolution_interval_s: int = 3600
    circuit_breaker_failures: int = 5
    circuit_breaker_backoff_s: int = 1800
    paper_mode: bool = True

    # Daily budget management
    groq_daily_limit: int = 14400
    gemini_daily_limit: int = 1000
    budget_reserve_pct: float = 10.0

    # Gamma API backoff
    gamma_request_delay_s: float = 0.1
    gamma_backoff_multiplier: float = 2.0


@dataclass(frozen=True)
class StrategyBuilderTypedConfig:
    """Strategy builder limits and operational config."""
    max_strategies_per_user: int = 10
    max_conditions_per_strategy: int = 8
    strategy_cache_refresh_s: int = 60
    cooldown_floor_minutes: int = 5
    cooldown_default_minutes: int = 60
    evaluation_log_interval_s: int = 300
    max_fire_rate_pct: float = 50.0
    max_hits_per_day_per_user: int = 5000


@dataclass(frozen=True)
class MarketIntelTypedConfig:
    """Market intelligence surface configuration (Wave 0)."""
    # Derivatives dashboard
    derivatives_min_completeness: float = 0.5

    # Heatmap
    heatmap_staleness_multiplier: float = 2.5
    heatmap_allowed_periods: tuple = field(default_factory=lambda: ("1h", "4h", "24h"))

    # Seasonality
    seasonality_default_years: int = 3
    seasonality_min_months: int = 24

    # Fear & Greed
    fng_cache_ttl_s: int = 3600
