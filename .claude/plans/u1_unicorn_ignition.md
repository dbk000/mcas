# U1 Unicorn Ignition - Implementation Plan (v2)

**Status**: Implemented
**Last Updated**: 2026-02-03

---

## 1) Overview

Unicorns identifies **low-volume pennycoins** transitioning from dead → discoverable → ignition before they appear on normal "active assets" lists.

**Three-phase pipeline**:
- Phase A: Embryo Universe (broad, cheap scan)
- Phase B: Embryo Active (capped attention budget)
- Phase C: Ignition Watch → Unicorn Signal

**Human-in-loop**: Signals are identification + ranking only. No auto-execution.

---

## 2) Ownership Model (Clear Separation)

### Pipeline Worker (new: `unicorn_pipeline.py`)
Runs on schedule (every 5 minutes). Owns:
- Universe refresh (daily full scan, incremental updates)
- Gate evaluation for promotion candidates
- Promotion decisions (EMBRYO → EMBRYO_ACTIVE)
- Demotion rules (pump traps, timeout, eviction)
- Cap enforcement (MAX_EMBRYO_ACTIVE)
- State persistence to Redis

### Signal Worker (existing: `signal_worker.py`)
Runs on stream events. Owns:
- Ignition trigger evaluation **only for symbols already in EMBRYO_ACTIVE**
- Signal emission with full payload (age_seconds, action_by_ts, etc.)
- Does NOT evaluate gates or modify pipeline state

**Rule**: Pipeline worker writes state, signal worker reads state and emits signals.

---

## 3) Universe Definition (Deterministic)

### 3.1 Universe Refresh Cadence
- **Full refresh**: Once per day at 00:00 UTC
- **Incremental**: Every 5 minutes (new listings check, delist removal)

### 3.2 Universe Selection Rules

**MVP Scope**: Spot USDT pairs only. No perpetuals in initial implementation.

```python
def base_asset(symbol: str) -> str:
    """
    Extract base asset from trading pair symbol.
    E.g., "BTCUSDT" -> "BTC", "ETHUSDT" -> "ETH"

    Assumes USDT quote (MVP constraint).
    """
    if symbol.endswith("USDT"):
        return symbol[:-4]
    # Fallback for other quote assets (future expansion)
    for quote in ["BUSD", "USDC", "BTC", "ETH"]:
        if symbol.endswith(quote):
            return symbol[:-len(quote)]
    return symbol  # Unknown format, return as-is

def is_stablecoin_base(symbol: str) -> bool:
    """Check if BASE asset is a stablecoin (not quote)."""
    base = base_asset(symbol)
    return base in STABLECOIN_BASES

def is_wrapped(symbol: str) -> bool:
    """Check if BASE asset is a wrapped token."""
    base = base_asset(symbol)
    return any(base == pattern for pattern in WRAPPED_PATTERNS)

def is_leverage_token(symbol: str) -> bool:
    """Check if BASE asset is a leverage token (UP/DOWN/BULL/BEAR/3L/3S)."""
    base = base_asset(symbol)
    return any(base.endswith(pattern) for pattern in LEVERAGE_PATTERNS)

def had_recent_hype(symbol: str, lookback_days: int = 30) -> bool:
    """
    Check if symbol had a hype spike in the lookback window.

    Returns True (exclude) if EITHER condition is met:
    - max(rel_volume_30d) > HYPE_REL_VOL_CAP (volume spike)
    - max(range_pct_30d) > HYPE_RANGE_CAP (price spike)

    This catches both "volume pump" and "pure range pump" coins.
    """
    candles = fetch_daily_candles(symbol, days=lookback_days)
    if not candles:
        return False  # No data = assume no hype

    # Compute baseline volume (median of the lookback)
    volumes = [c.volume for c in candles]
    baseline_vol = median(volumes) if volumes else 0

    # Check for volume spike
    if baseline_vol > 0:
        max_rel_vol = max(c.volume / baseline_vol for c in candles)
        if max_rel_vol > HYPE_REL_VOL_CAP:
            return True

    # Check for range spike (daily range as % of close)
    for c in candles:
        if c.close > 0:
            range_pct = (c.high - c.low) / c.close
            if range_pct > HYPE_RANGE_CAP:
                return True

    return False

def get_embryo_universe() -> list[str]:
    """
    Deterministic universe selection for Unicorns.
    Returns symbols eligible for embryo consideration.

    MVP: Spot USDT pairs only.
    """
    # Step 1: Fetch all spot USDT pairs from exchange (MVP: spot only)
    all_pairs = fetch_exchange_symbols(quote="USDT", market_type="SPOT")

    # Step 2: Hard exclusions (deterministic)
    excluded = set()
    for symbol in all_pairs:
        info = get_symbol_info(symbol)

        # Exclude: precision/min-notional failures
        if info.min_notional > MAX_MIN_NOTIONAL:  # e.g., $100
            excluded.add(symbol)
            continue

        # Exclude: stablecoins (check BASE asset, not symbol)
        # E.g., USDCUSDT has base="USDC" which is a stablecoin
        if is_stablecoin_base(symbol):
            excluded.add(symbol)
            continue

        # Exclude: wrapped assets, leverage tokens
        if is_wrapped(symbol) or is_leverage_token(symbol):
            excluded.add(symbol)
            continue

        # Exclude: delisted or halted
        if info.status != "TRADING":
            excluded.add(symbol)
            continue

    candidates = [s for s in all_pairs if s not in excluded]

    # Step 3: Volume band filter
    # Include only: MIN_VOLUME_USD <= 24h_volume <= MAX_VOLUME_USD
    volume_filtered = []
    for symbol in candidates:
        vol_24h = get_24h_volume_usd(symbol)
        if MIN_VOLUME_USD <= vol_24h <= MAX_VOLUME_USD:
            volume_filtered.append(symbol)

    # Step 4: Exclude recent hype (had major spike in last 30d)
    # Deterministic: check BOTH conditions (see had_recent_hype):
    #   - max(rel_volume_30d) > HYPE_REL_VOL_CAP (volume spike), OR
    #   - max(range_pct_30d) > HYPE_RANGE_CAP (price spike)
    final = []
    for symbol in volume_filtered:
        if not had_recent_hype(symbol, lookback_days=30):
            final.append(symbol)

    return sorted(final)  # Sorted for determinism
```

### 3.3 Configuration

```python
# Universe parameters
MIN_VOLUME_USD = 10_000          # Floor: too illiquid = untradeable
MAX_VOLUME_USD = 1_000_000       # Ceiling: too liquid = not a pennycoin
MAX_MIN_NOTIONAL = 100           # Exchange min notional cap
HYPE_REL_VOL_CAP = 20.0          # Exclude if had 20x volume spike in 30d
HYPE_RANGE_CAP = 0.50            # Exclude if had 50% range in 30d

# Stablecoin BASE assets to exclude (not quote)
# E.g., exclude USDCUSDT (base=USDC), but keep BTCUSDT (base=BTC)
STABLECOIN_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD"}
WRAPPED_PATTERNS = ["WBTC", "WETH", "WBNB"]
LEVERAGE_PATTERNS = ["UP", "DOWN", "BULL", "BEAR", "3L", "3S"]
```

### 3.4 Baseline Volume Calculation (Deterministic Fallback)

```python
def compute_baseline_volume(candles: list[OHLCV], timeframe: str = "5m") -> float:
    """
    Compute baseline volume with deterministic fallback windows.

    Preference: Quote volume (USDT) when available.
    Fallback: Use raw volume and assume USDT pairs (MVP).

    Fallback windows (try in order until sufficient data):
    1. 7 days (2016 bars on 5m)
    2. 3 days (864 bars on 5m)
    3. 1 day (288 bars on 5m)
    4. 288 bars (whatever data available)
    5. 48 bars (minimum viable)

    Returns median volume as baseline.
    """
    # Calculate bars needed for each window
    BARS_PER_DAY_5M = 288
    fallback_windows = [
        ("7d", 7 * BARS_PER_DAY_5M),   # 2016 bars
        ("3d", 3 * BARS_PER_DAY_5M),   # 864 bars
        ("1d", 1 * BARS_PER_DAY_5M),   # 288 bars
        ("288_bars", 288),              # 288 bars (raw)
        ("48_bars", 48),                # 48 bars (minimum)
    ]

    available_bars = len(candles)

    for window_name, bars_needed in fallback_windows:
        if available_bars >= bars_needed:
            window_candles = candles[-bars_needed:]
            volumes = [c.quote_volume if hasattr(c, 'quote_volume') and c.quote_volume > 0
                      else c.volume for c in window_candles]
            baseline = median(volumes)
            return baseline

    # Absolute minimum: use whatever we have
    if available_bars > 0:
        volumes = [c.quote_volume if hasattr(c, 'quote_volume') and c.quote_volume > 0
                  else c.volume for c in candles]
        return median(volumes) if volumes else 0.0

    return 0.0  # No data
```

---

## 4) Gate Logic (OHLCV Proxy Mode)

**Critical**: These are OHLCV-only proxies. When real spread/depth/trade data becomes available, add separate `Gate1_Tradability` (real) vs current `Gate1_TradabilityProxy`.

### 4.1 Window Definition

```python
GATE_WINDOW_BARS = 48        # 4 hours on 5m timeframe
GATE_TIMEFRAME = "5m"        # Primary evaluation timeframe

# Two adjacent windows for comparison:
# last_n  = bars[-48:]   (most recent 4h)
# prev_n  = bars[-96:-48] (prior 4h)
```

### 4.2 Gate 1: TradabilityProxy (pass 2 of 3)

**Purpose**: Detect improving market microstructure via OHLCV proxies.

```python
def evaluate_gate1_tradability_proxy(candles_last_n, candles_prev_n) -> tuple[bool, dict]:
    """
    OHLCV proxy for tradability improvement.
    Returns (passed, metrics_dict).
    """
    # Helper: compute range (c.range may not exist on OHLCV object)
    def candle_range(c) -> float:
        return c.high - c.low

    # Metric 1: Spread proxy compression (range / close)
    # Guard: close > 0 to avoid division by zero
    spread_proxy_last = median([candle_range(c) / c.close for c in candles_last_n if c.close > 0])
    spread_proxy_prev = median([candle_range(c) / c.close for c in candles_prev_n if c.close > 0])
    spread_improving = spread_proxy_last <= 0.7 * spread_proxy_prev if spread_proxy_prev > 0 else False

    # Metric 2: Volume coefficient of variation (stability)
    # Guard: mean > 0 to avoid division by zero
    mean_vol_last = mean([c.volume for c in candles_last_n])
    mean_vol_prev = mean([c.volume for c in candles_prev_n])
    vol_cv_last = std([c.volume for c in candles_last_n]) / mean_vol_last if mean_vol_last > 0 else float('inf')
    vol_cv_prev = std([c.volume for c in candles_prev_n]) / mean_vol_prev if mean_vol_prev > 0 else float('inf')
    volume_stabilizing = vol_cv_last <= 0.8 * vol_cv_prev if vol_cv_prev < float('inf') else False

    # Metric 3: ATR% compression (volatility calming)
    atr_pct_last = compute_atr_pct(candles_last_n)
    atr_pct_prev = compute_atr_pct(candles_prev_n)
    atr_compressing = atr_pct_last <= 0.7 * atr_pct_prev

    checks = [spread_improving, volume_stabilizing, atr_compressing]
    passed = sum(checks) >= 2

    return passed, {
        "spread_proxy_last": spread_proxy_last,
        "spread_proxy_prev": spread_proxy_prev,
        "spread_improving": spread_improving,
        "vol_cv_last": vol_cv_last,
        "vol_cv_prev": vol_cv_prev,
        "volume_stabilizing": volume_stabilizing,
        "atr_pct_last": atr_pct_last,
        "atr_pct_prev": atr_pct_prev,
        "atr_compressing": atr_compressing,
    }
```

### 4.3 Gate 2: ParticipationProxy (pass all 3)

**Purpose**: Detect broadening interest via OHLCV proxies.

```python
def evaluate_gate2_participation_proxy(candles_last_n, candles_prev_n) -> tuple[bool, dict]:
    """
    OHLCV proxy for participation broadening.
    Returns (passed, metrics_dict).
    """
    # Metric 1: Volume sum rising
    vol_sum_last = sum([c.volume for c in candles_last_n])
    vol_sum_prev = sum([c.volume for c in candles_prev_n])
    volume_rising = vol_sum_last >= 1.5 * vol_sum_prev

    # Metric 2: Active bars proxy (non-zero volume bars)
    # Proxy for "small trade count rising"
    active_bars_last = sum(1 for c in candles_last_n if c.volume > 0)
    active_bars_prev = sum(1 for c in candles_prev_n if c.volume > 0)
    activity_rising = active_bars_last >= 1.3 * active_bars_prev

    # Metric 3: No whale dominance (no single bar > 10x average)
    avg_vol_last = mean([c.volume for c in candles_last_n if c.volume > 0])
    max_vol_last = max([c.volume for c in candles_last_n])
    whale_ratio = max_vol_last / avg_vol_last if avg_vol_last > 0 else float('inf')
    no_whale_dominance = whale_ratio < 10.0

    passed = volume_rising and activity_rising and no_whale_dominance

    return passed, {
        "vol_sum_last": vol_sum_last,
        "vol_sum_prev": vol_sum_prev,
        "volume_rising": volume_rising,
        "active_bars_last": active_bars_last,
        "active_bars_prev": active_bars_prev,
        "activity_rising": activity_rising,
        "whale_ratio": whale_ratio,
        "no_whale_dominance": no_whale_dominance,
    }
```

### 4.4 Gate 3: Compression (pass all 3)

**Purpose**: Detect volatility compression (coiling before breakout).

```python
def evaluate_gate3_compression(candles_last_n, candles_prev_n, rel_volume_last) -> tuple[bool, dict]:
    """
    Compression detection for pre-ignition state.
    Returns (passed, metrics_dict).
    """
    # Metric 1: Range tight (percentile-based preferred, static fallback)
    range_pct_last = (max(c.high for c in candles_last_n) - min(c.low for c in candles_last_n)) / candles_last_n[-1].close
    range_tight = range_pct_last <= RANGE_PCT_THRESHOLD  # default 0.05 (5%)

    # Metric 2: ATR contracting
    atr_pct_last = compute_atr_pct(candles_last_n)
    atr_pct_prev = compute_atr_pct(candles_prev_n)
    atr_contracting = atr_pct_last <= 0.7 * atr_pct_prev

    # Metric 3: Volume not igniting yet (accumulation, not distribution)
    # slope(volume) >= 0 AND rel_volume < ignition threshold
    vol_slope = compute_slope([c.volume for c in candles_last_n])
    not_igniting = vol_slope >= 0 and rel_volume_last < REL_VOL_IGNITION_MIN  # default 3.0

    passed = range_tight and atr_contracting and not_igniting

    return passed, {
        "range_pct_last": range_pct_last,
        "range_tight": range_tight,
        "atr_pct_last": atr_pct_last,
        "atr_pct_prev": atr_pct_prev,
        "atr_contracting": atr_contracting,
        "vol_slope": vol_slope,
        "rel_volume_last": rel_volume_last,
        "not_igniting": not_igniting,
    }
```

### 4.5 Gate 4: HolderIntegrity (optional, skipped in MVP)

```python
def evaluate_gate4_holder_integrity(symbol) -> tuple[Optional[bool], dict]:
    """
    On-chain holder data gate. Returns (None, {}) if data unavailable.
    """
    if not ONCHAIN_DATA_ENABLED:
        return None, {"skipped": True, "reason": "onchain_data_unavailable"}

    # Future implementation when data available:
    # delta_holders = get_holder_delta(symbol, window_days=7)
    # churn_adjusted = new_holders - exited_holders
    # passed = delta_holders > 0 and churn_adjusted > 0

    return None, {"skipped": True, "reason": "not_implemented"}
```

---

## 5) Compression Box Definition (Explicit)

**Critical**: This defines the breakout reference for ignition triggers.

```python
def compute_compression_box(candles: list[OHLCV], lookback: int = 48) -> tuple[float, float]:
    """
    Compute compression box (range bounds) for breakout detection.

    Method: Donchian-style high/low over the lookback period.
    Excludes the most recent bar (trigger bar) to prevent circular definition.

    Args:
        candles: OHLCV candles, newest last
        lookback: Number of bars for box computation (default 48 = 4h on 5m)

    Returns:
        (box_high, box_low): Price bounds for the compression range
    """
    # Exclude trigger bar (last bar) to prevent lookahead
    box_candles = candles[-(lookback + 1):-1]  # lookback bars, excluding current

    if len(box_candles) < lookback:
        # Insufficient history, use what we have
        box_candles = candles[:-1]

    box_high = max(c.high for c in box_candles)
    box_low = min(c.low for c in box_candles)

    return box_high, box_low

# Configuration
COMPRESSION_BOX_LOOKBACK = 48  # 4h on 5m timeframe
```

---

## 6) Ignition Trigger (Signal Worker)

### 6.1 Trigger Set (must all be true)

```python
def check_ignition_triggers(
    snapshot: IndicatorSnapshot,
    state: U1State,
    candles: list[OHLCV],
) -> tuple[bool, dict]:
    """
    Check ignition trigger conditions for EMBRYO_ACTIVE symbol.
    """
    current = candles[-1]

    # Trigger 1: Relative volume ignition
    # rel_volume = current_volume / baseline_volume
    # baseline = median(volume) via fallback window (see 3.4)
    rel_volume = current.volume / state.rel_volume_baseline if state.rel_volume_baseline > 0 else 0
    volume_igniting = rel_volume >= REL_VOL_IGNITION  # default 5.0

    # Trigger 2: Breakout (close above compression box high)
    breakout = current.close > state.compression_box_high

    # Trigger 3: Acceptance (m consecutive closes above box_high starting with breakout)
    #
    # Definition: Breakout occurs on bar i where close[i] > box_high AND close[i-1] <= box_high
    # Acceptance: close[i : i+m] all > box_high (m bars including breakout bar)
    #
    # Implementation: Check that last m bars are ALL above box_high,
    # AND the bar before that was NOT above (confirming this IS the breakout sequence).
    #
    # This avoids:
    # - False positives (already broke out long ago)
    # - Rejecting genuine first-breakout events
    #
    m = BREAKOUT_ACCEPTANCE_BARS  # default 2
    if len(candles) >= m + 1:
        # Last m bars must all be above box_high
        last_m_above = all(c.close > state.compression_box_high for c in candles[-m:])
        # Bar before the breakout sequence must be inside the box (confirms fresh breakout)
        prior_inside = candles[-m - 1].close <= state.compression_box_high
        accepted = last_m_above and prior_inside
    else:
        accepted = False  # Insufficient history

    all_triggers = volume_igniting and breakout and accepted

    return all_triggers, {
        "rel_volume": rel_volume,
        "volume_igniting": volume_igniting,
        "breakout": breakout,
        "accepted": accepted,
        "acceptance_bars_checked": m,
        "acceptance_logic": "last m bars > box_high AND bar[-m-1] <= box_high",
    }
```

### 6.2 Confirmation Set (must meet 2 of 3)

```python
def check_ignition_confirmations(
    snapshot: IndicatorSnapshot,
    state: U1State,
    candles_last_n: list[OHLCV],
    candles_prev_n: list[OHLCV],
) -> tuple[bool, dict]:
    """
    Check confirmation conditions. Must meet at least 2 of 3.
    """
    # Helper: compute range (c.range may not exist on OHLCV object)
    def candle_range(c) -> float:
        return c.high - c.low

    # Confirmation A: Liquidity + volume rising together
    # Proxy: volume rising AND spread proxy NOT widening
    vol_rising = sum(c.volume for c in candles_last_n) > sum(c.volume for c in candles_prev_n)
    # Guard: close > 0 to avoid division by zero
    spread_proxy_last = median([candle_range(c) / c.close for c in candles_last_n if c.close > 0])
    spread_proxy_prev = median([candle_range(c) / c.close for c in candles_prev_n if c.close > 0])
    spread_not_widening = spread_proxy_last <= 1.2 * spread_proxy_prev if spread_proxy_prev > 0 else True
    liquidity_volume_ok = vol_rising and spread_not_widening

    # Confirmation B: Participation continues
    # Active bars in last_n >= prev_n (interest maintained)
    active_last = sum(1 for c in candles_last_n if c.volume > 0)
    active_prev = sum(1 for c in candles_prev_n if c.volume > 0)
    participation_continues = active_last >= active_prev

    # Confirmation C: TrendRate acceleration from low base
    # Using existing indicators: price momentum increasing
    # Proxy: close slope positive and accelerating
    closes_last = [c.close for c in candles_last_n]
    slope_last = compute_slope(closes_last)
    slope_prev_half = compute_slope(closes_last[:len(closes_last)//2])
    slope_curr_half = compute_slope(closes_last[len(closes_last)//2:])
    trend_accelerating = slope_last > 0 and slope_curr_half > slope_prev_half

    confirmations = [liquidity_volume_ok, participation_continues, trend_accelerating]
    met_count = sum(confirmations)
    passed = met_count >= 2

    return passed, {
        "liquidity_volume_ok": liquidity_volume_ok,
        "vol_rising": vol_rising,
        "spread_not_widening": spread_not_widening,
        "participation_continues": participation_continues,
        "trend_accelerating": trend_accelerating,
        "confirmations_met": met_count,
    }
```

---

## 7) Signal Payload (Complete Specification)

### 7.1 Required Fields

**Note on Trade Levels**: Entry/stop/TP prices are **suggested levels for human execution** and for future backtest consistency. U1 does not auto-execute trades in MVP. The human operator uses these as reference points for manual order placement.

```python
@dataclass
class U1Signal:
    """Unicorn Ignition signal - complete payload."""

    # Identity
    symbol: str
    timeframe: str
    direction: Direction = Direction.LONG  # Unicorns are always long (catching ignition)
    setup_id: str = "U1"

    # Scoring
    setup_score: float  # = unicorn_confidence (0-1)

    # Trade levels (suggested for human execution, used in future backtest)
    entry_price: float      # Current close (next-bar-open entry)
    stop_price: float       # Below compression_box_low - ATR buffer
    tp1_price: float        # Entry + 1R
    tp2_price: float        # Entry + 2R
    r_value: float          # Entry - stop

    # Timing (REQUIRED - Canon compliance)
    bar_start_ts: datetime  # Candle identity (UTC) - for bar reference only
    signal_ts: datetime     # When signal was first generated (use for age/action-by)
    age_seconds: int        # Computed: now - signal_ts (NOT bar_start_ts)
    action_by_ts: datetime  # Computed: signal_ts + (time_stop_bars * bar_seconds)

    # Time stop policy
    time_stop_bars: int     # N_dead for this signal
    time_stop_policy: str = "PROGRESS_0.5R_OR_EXIT"  # Canon policy identifier

    # Ignition metrics
    rel_volume: float
    breakout_acceptance_bars: int
    compression_box_high: float
    compression_box_low: float

    # Confirmation flags
    liquidity_volume_rising: bool
    participation_confirmed: bool
    trend_rate_accelerating: bool
    confirmations_met: int

    # Gate metrics snapshot (for explainability)
    tradability_score: float
    participation_score: float
    compression_score: float
    trap_risk_score: float
    embryo_active_score: float

    # Canon 7A: Explainability (3-8 items)
    explain: ExplainPayload

    # State snapshot
    state: U1State
```

### 7.2 Timing Computation

```python
def compute_signal_timing(bar_start_ts: datetime, timeframe: str, time_stop_bars: int) -> dict:
    """
    Compute timing fields for signal payload.

    IMPORTANT: age_seconds and action_by_ts are computed from signal_ts (emission time),
    NOT from bar_start_ts. bar_start_ts is retained for bar identity/reference only.

    Returns:
        {
            "bar_start_ts": datetime,   # Bar identity (candle open time)
            "signal_ts": datetime,      # When signal was generated (= now at emission)
            "age_seconds": int,         # now - signal_ts
            "action_by_ts": datetime,   # signal_ts + N_dead * bar_seconds
        }
    """
    now = datetime.now(timezone.utc)
    signal_ts = now  # Signal timestamp is when we emit it

    # Bar duration in seconds
    tf_seconds = {
        "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "4h": 14400, "1d": 86400,
    }
    bar_seconds = tf_seconds.get(timeframe, 300)  # Default 5m

    # Age: how old is this signal (at emission, age = 0)
    # When re-read later, recompute as: now - signal_ts
    age_seconds = 0  # Fresh signal

    # Action-by: deadline for human execution
    # Canon: signal_ts + (N_dead * bar_seconds)
    action_by_ts = signal_ts + timedelta(seconds=time_stop_bars * bar_seconds)

    return {
        "bar_start_ts": bar_start_ts,  # Bar identity only
        "signal_ts": signal_ts,         # Canonical timestamp for age/action-by
        "age_seconds": age_seconds,     # Recompute on read as: now - signal_ts
        "action_by_ts": action_by_ts,
    }

# Configuration
TIME_STOP_BARS_5M = 12   # 1 hour on 5m timeframe
TIME_STOP_BARS_15M = 8   # 2 hours on 15m timeframe
TIME_STOP_BARS_1H = 4    # 4 hours on 1h timeframe
```

---

## 8) Time Stop Policy (Canon Compliant)

**Policy**: Progress-to-0.5R within N_dead bars, or exit at next bar open.

```python
"""
Time Stop Rule for U1 (Unicorn Ignition)
========================================

Same canon-compliant rule as S1/S2:

1. Track whether price has EVER reached Entry + 0.5R during the time window.
   - For long: check if High[i] >= Entry + 0.5*R for any bar in window

2. Window definition:
   - window_start = entry_bar
   - window_end = entry_bar + N_dead (time_stop_bars)

3. Exit condition:
   - If bars_held > N_dead AND price never reached 0.5R target:
     → Exit at current bar's open (TIME_STOP)

4. No exit if progress made:
   - If price touched Entry + 0.5R even once, TIME_STOP does not trigger.

Implementation:
- Track `_half_r_touched` flag on position
- Update each bar: if high >= entry + 0.5*r_value, set flag True
- Check: if bars_held > time_stop_bars and not _half_r_touched: exit

Default N_dead by timeframe:
- 5m:  12 bars (1 hour)
- 15m:  8 bars (2 hours)
- 1h:   4 bars (4 hours)
"""

def get_time_stop_bars(timeframe: str) -> int:
    """Get N_dead (time stop bars) for timeframe."""
    return {
        "5m": 12,
        "15m": 8,
        "1h": 4,
        "4h": 2,
    }.get(timeframe, 12)  # Default to 5m value
```

---

## 9) Scoring System

### 9.1 Component Scores (0-100)

```python
def compute_tradability_score(metrics: dict) -> float:
    """
    TradabilityScore based on proxy metrics.
    Higher = better tradability conditions.
    """
    # Factors: spread compression, volume stability, ATR compression
    spread_factor = 1.0 - min(metrics["spread_proxy_last"] / 0.05, 1.0)  # 5% cap
    vol_cv_factor = 1.0 - min(metrics["vol_cv_last"] / 2.0, 1.0)  # CV=2 cap
    atr_factor = 1.0 - min(metrics["atr_pct_last"] / 0.10, 1.0)  # 10% ATR cap

    return (0.4 * spread_factor + 0.3 * vol_cv_factor + 0.3 * atr_factor) * 100

def compute_participation_score(metrics: dict) -> float:
    """
    ParticipationScore based on activity metrics.
    Higher = broader participation.
    """
    volume_growth = min(metrics["vol_sum_last"] / metrics["vol_sum_prev"], 3.0) / 3.0
    activity_growth = min(metrics["active_bars_last"] / max(metrics["active_bars_prev"], 1), 2.0) / 2.0
    whale_penalty = 1.0 - min(metrics["whale_ratio"] / 10.0, 1.0)

    return (0.4 * volume_growth + 0.3 * activity_growth + 0.3 * whale_penalty) * 100

def compute_compression_score(metrics: dict) -> float:
    """
    CompressionScore based on range/volatility contraction.
    Higher = tighter compression (more potential energy).
    """
    range_tightness = 1.0 - min(metrics["range_pct_last"] / 0.10, 1.0)  # 10% cap
    atr_contraction = 1.0 - min(metrics["atr_pct_last"] / metrics.get("atr_pct_prev", 0.10), 1.0)

    return (0.6 * range_tightness + 0.4 * atr_contraction) * 100

def compute_trap_risk_score(metrics: dict, candles: list) -> float:
    """
    TrapRiskScore (higher = MORE risk, penalizes score).
    Detects pump-and-dump patterns.
    """
    # Risk factors:
    # 1. Extreme volume spike (single bar dominance)
    whale_risk = min(metrics.get("whale_ratio", 1) / 20.0, 1.0)

    # 2. Spread widening during volume
    spread_widening = 1.0 if metrics.get("spread_proxy_last", 0) > 1.5 * metrics.get("spread_proxy_prev", 1) else 0.0

    # 3. Recent hype (high rel_volume in recent history)
    # Already filtered at universe level, but double-check

    return (0.5 * whale_risk + 0.5 * spread_widening) * 100
```

### 9.2 Composite Scores

```python
def compute_embryo_active_score(
    tradability: float,
    participation: float,
    compression: float,
    trap_risk: float,
) -> float:
    """
    EmbryoActiveScore for ranking and cap enforcement.

    Formula: w1*T + w2*P + w3*C - w4*Risk
    """
    W_TRADABILITY = 0.25
    W_PARTICIPATION = 0.30
    W_COMPRESSION = 0.30
    W_TRAP_RISK = 0.15

    score = (
        W_TRADABILITY * tradability +
        W_PARTICIPATION * participation +
        W_COMPRESSION * compression -
        W_TRAP_RISK * trap_risk
    )

    return max(0, min(100, score))

def compute_unicorn_confidence(
    ignition_score: float,
    confirmations_met: int,
    tradability: float,
    trap_risk: float,
) -> float:
    """
    UnicornConfidence for signal ranking (0-1 scale for setup_score).
    """
    base = ignition_score / 100  # Normalize to 0-1

    # Bonus for confirmations (2 required, 3 is better)
    confirmation_bonus = 0.1 * (confirmations_met - 2) if confirmations_met > 2 else 0

    # Tradability bonus
    tradability_bonus = 0.1 * (tradability / 100)

    # Risk penalty
    risk_penalty = 0.2 * (trap_risk / 100)

    confidence = base + confirmation_bonus + tradability_bonus - risk_penalty

    return max(0, min(1, confidence))
```

---

## 10) Demotion Rules

```python
def check_demotion(state: U1State, metrics: dict, candles: list) -> tuple[bool, str]:
    """
    Check if EMBRYO_ACTIVE asset should be demoted.

    Returns:
        (should_demote, reason)
    """
    # Rule 1: Pump liquidity trap
    # Volume spikes but spread/slippage worsens
    if metrics.get("rel_volume", 0) > 3.0:  # Volume elevated
        spread_widening = metrics.get("spread_proxy_last", 0) > 1.5 * metrics.get("spread_proxy_prev", 1)
        if spread_widening:
            return True, "PUMP_TRAP:spread_widening_with_volume"

    # Rule 2: Whale dump
    # Single bar > 20x average volume
    if metrics.get("whale_ratio", 0) > 20:
        return True, "WHALE_DUMP:single_bar_dominance"

    # Rule 3: Timeout (stagnation)
    # No ignition within T_active_max since promoted_at
    if state.promoted_at:
        hours_active = (datetime.now(timezone.utc) - state.promoted_at).total_seconds() / 3600
        if hours_active > T_ACTIVE_MAX_HOURS:  # default 168 (7 days)
            return True, f"TIMEOUT:no_ignition_after_{hours_active:.0f}h"

    # Rule 4: Hard reject filter triggered (e.g., delisted)
    # Checked at universe level, but double-check here

    return False, ""

# Configuration
T_ACTIVE_MAX_HOURS = 168  # 7 days
```

---

## 11) Cap Enforcement

```python
MAX_EMBRYO_ACTIVE = 50  # Hard cap, zero-sum

def enforce_cap(
    current_active: dict[str, U1State],  # symbol -> state
    candidates: dict[str, U1State],       # newly qualified symbols
) -> tuple[list[str], list[str]]:
    """
    Enforce MAX_EMBRYO_ACTIVE cap.

    Returns:
        (promoted_symbols, demoted_symbols)
    """
    # Merge current + candidates, all ranked by EmbryoActiveScore
    all_symbols = {}
    for symbol, state in current_active.items():
        all_symbols[symbol] = state.embryo_active_score
    for symbol, state in candidates.items():
        if symbol not in all_symbols:
            all_symbols[symbol] = state.embryo_active_score

    # Sort by score descending
    ranked = sorted(all_symbols.items(), key=lambda x: x[1], reverse=True)

    # Top N stay in EMBRYO_ACTIVE
    keep = set(symbol for symbol, _ in ranked[:MAX_EMBRYO_ACTIVE])

    # Determine promotions and demotions
    promoted = [s for s in candidates if s in keep and s not in current_active]
    demoted = [s for s in current_active if s not in keep]

    return promoted, demoted
```

---

## 12) Redis Schema

```python
# Key patterns (use builders, never hardcode)

def embryo_universe_key() -> str:
    return "unicorn:embryo_universe"  # SET of symbols

def embryo_active_key() -> str:
    return "unicorn:embryo_active"  # ZSET (symbol, embryo_active_score)

def unicorn_state_key(symbol: str) -> str:
    return f"unicorn:state:{symbol}"  # HASH with all state fields

def unicorn_metrics_key(symbol: str) -> str:
    return f"unicorn:metrics:{symbol}"  # HASH with latest gate/trigger metrics

# Signal keys follow existing pattern:
# sig:U1:{symbol}:{tf}           - JSON payload
# sig_meta:U1:{symbol}:{tf}      - metadata
# signals:score:U1:{tf}          - ZSET ranking
# signals:ts:U1:{tf}             - ZSET by timestamp

# TTLs
# EMBRYO_STATE_TTL_S = 2 × T_ACTIVE_MAX to allow state to persist beyond
# the maximum active window. TTL is refreshed on each pipeline evaluation cycle.
EMBRYO_STATE_TTL_S = 86400 * 14  # 14 days (2 × 7-day T_ACTIVE_MAX)
EMBRYO_METRICS_TTL_S = 3600      # 1 hour
SIGNAL_TTL_S = 3600              # 1 hour (existing)

# NOTE: Pipeline worker must refresh TTL on each cycle via EXPIRE command
# to prevent premature expiration during active monitoring.
```

---

## 13) API Endpoints

```python
# Backend/api/app/unicorns_api.py

@router.get("/unicorns/embryo-active")
def get_embryo_active(
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    """
    Get ranked list of EMBRYO_ACTIVE candidates.

    Returns:
        {
            "count": int,
            "max_cap": int,
            "as_of_ts": str,
            "candidates": [
                {
                    "symbol": str,
                    "embryo_active_score": float,
                    "tradability_score": float,
                    "participation_score": float,
                    "compression_score": float,
                    "trap_risk_score": float,
                    "phase": str,  # "EMBRYO_ACTIVE" or "IGNITION_WATCH"
                    "promoted_at": str,
                    "hours_active": float,
                    "gates_passed": list[str],
                },
                ...
            ]
        }
    """

@router.get("/unicorns/{symbol}")
def get_unicorn_detail(symbol: str) -> dict:
    """
    Get full explainability payload for a symbol.

    Returns:
        {
            "symbol": str,
            "phase": str,
            "embryo_active_score": float,
            "scores": {
                "tradability": float,
                "participation": float,
                "compression": float,
                "trap_risk": float,
            },
            "gates": {
                "gate1_tradability_proxy": {"passed": bool, "metrics": dict},
                "gate2_participation_proxy": {"passed": bool, "metrics": dict},
                "gate3_compression": {"passed": bool, "metrics": dict},
                "gate4_holder_integrity": {"passed": bool|null, "metrics": dict},
            },
            "compression_box": {
                "high": float,
                "low": float,
                "lookback_bars": int,
            },
            "ignition_status": {
                "rel_volume": float,
                "rel_volume_baseline": float,
                "breakout": bool,
                "accepted": bool,
            },
            "promoted_at": str|null,
            "last_eval_ts": str,
        }
    """

# Signals available via existing endpoint with filter:
# GET /v1/signals?setup=U1
```

---

## 14) File Structure

```
Backend/
├── worker/
│   └── app/
│       ├── setups/
│       │   ├── __init__.py              # Add U1 exports
│       │   ├── s1_bb_squeeze.py
│       │   ├── s2_golden_cross.py
│       │   └── u1_unicorn_ignition.py   # NEW: State, Signal, Evaluator
│       ├── unicorn_pipeline.py          # NEW: Pipeline worker
│       ├── signal_worker.py             # MODIFY: Add U1 trigger evaluation
│       └── cache_contract.py            # MODIFY: Add unicorn key builders
├── api/
│   └── app/
│       ├── main.py                      # MODIFY: Mount unicorns router
│       └── unicorns_api.py              # NEW: API endpoints
```

---

## 15) Implementation Order

1. **cache_contract.py** - Add unicorn Redis key builders
2. **u1_unicorn_ignition.py** - Core dataclasses (U1State, U1Signal) and U1Evaluator
3. **unicorn_pipeline.py** - Pipeline worker (universe, gates, promotion, demotion, cap)
4. **signal_worker.py** - Integrate U1 trigger evaluation for EMBRYO_ACTIVE
5. **unicorns_api.py** - API endpoints
6. **setups/__init__.py** - Export U1 classes
7. **main.py** - Mount unicorns router

---

## 16) Configuration Summary

```python
# Unicorn Configuration Defaults
class U1Config:
    # Universe (MVP: Spot USDT only)
    MIN_VOLUME_USD = 10_000
    MAX_VOLUME_USD = 1_000_000
    MAX_MIN_NOTIONAL = 100
    HYPE_REL_VOL_CAP = 20.0
    HYPE_RANGE_CAP = 0.50
    STABLECOIN_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD"}

    # Gates
    GATE_WINDOW_BARS = 48
    GATE_TIMEFRAME = "5m"
    TRADABILITY_IMPROVEMENT_FACTOR = 0.7
    PARTICIPATION_GROWTH_FACTOR = 1.5
    RANGE_PCT_THRESHOLD = 0.05
    REL_VOL_IGNITION_MIN = 3.0

    # Baseline volume fallback windows (bars on 5m)
    BASELINE_WINDOWS = [
        ("7d", 2016),   # 7 days
        ("3d", 864),    # 3 days
        ("1d", 288),    # 1 day
        ("288_bars", 288),
        ("48_bars", 48),  # Minimum viable
    ]

    # Compression box
    COMPRESSION_BOX_LOOKBACK = 48

    # Ignition
    REL_VOL_IGNITION = 5.0
    BREAKOUT_ACCEPTANCE_BARS = 2  # Includes current bar

    # Confirmations
    SPREAD_WIDENING_THRESHOLD = 1.2

    # Time stop (note: trade levels are for human execution reference)
    TIME_STOP_BARS_5M = 12
    TIME_STOP_BARS_15M = 8
    TIME_STOP_BARS_1H = 4

    # Cap
    MAX_EMBRYO_ACTIVE = 50
    T_ACTIVE_MAX_HOURS = 168  # 7 days

    # Redis TTLs (refresh on each pipeline cycle)
    EMBRYO_STATE_TTL_S = 86400 * 14  # 14 days (2 × T_ACTIVE_MAX)
    EMBRYO_METRICS_TTL_S = 3600      # 1 hour

    # Scoring weights
    W_TRADABILITY = 0.25
    W_PARTICIPATION = 0.30
    W_COMPRESSION = 0.30
    W_TRAP_RISK = 0.15

    # Risk (suggested levels for human execution)
    STOP_ATR_BUFFER = 1.0
    TP1_R = 1.0
    TP2_R = 2.0
```

---

## 17) Acceptance Criteria

1. [ ] Assets promoted to EMBRYO_ACTIVE only if Gate 1→2→3 pass (Gate 4 optional)
2. [ ] Hard cap MAX_EMBRYO_ACTIVE strictly enforced (zero-sum promotion/eviction)
3. [ ] Unicorn signals emit only when ignition triggers + 2/3 confirmations pass
4. [ ] Every signal includes `signal_ts`, `age_seconds`, `action_by_ts` (age/action-by from signal_ts)
5. [ ] Time stop policy documented: "PROGRESS_0.5R_OR_EXIT" with N_dead per timeframe
6. [ ] Compression box explicitly defined: Donchian-style, excludes trigger bar
7. [ ] Gates labeled as "Proxy" versions (TradabilityProxy, ParticipationProxy)
8. [ ] Demotion rules operate and remove pump traps
9. [ ] Pipeline worker owns state, signal worker owns trigger evaluation only
10. [ ] System works without on-chain data (degrades gracefully)
11. [ ] No rolling history stored in Postgres; cache-first behavior preserved
12. [ ] Universe is spot USDT pairs only (no perpetuals in MVP)
13. [ ] Stablecoin exclusion checks BASE asset (not symbol match)
14. [ ] Baseline volume uses deterministic fallback windows (7d → 3d → 1d → 288 → 48 bars)
15. [ ] Trade levels (entry/stop/TP) documented as "suggested for human execution"
16. [ ] Redis state TTL is 14 days (2× T_ACTIVE_MAX) with refresh on each pipeline cycle
17. [ ] Acceptance logic: m consecutive closes > box_high AND bar[-m-1] <= box_high (fresh breakout)
18. [ ] had_recent_hype() checks BOTH rel_volume AND range_pct caps
19. [ ] is_wrapped()/is_leverage_token() check base_asset, not full symbol
20. [ ] Division-by-zero guards on all metric calculations (close>0, mean>0)
