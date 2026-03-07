"""
S9 Microstructure Analysis
==========================

Fetches and analyzes 300 x 1-second klines after the hourly close
to derive execution instructions for S9 short reversion entries.

Used by:
- signal_worker.py (live entry at +300s)
- s9_1s_execution_backtest.py (historical comparison)
- s9_position_monitor.py (early exit detection)

Design principle: NO hardcoded rules from research averages.
Every decision reads the actual tape and derives from what happened.
"""

import time
import logging
import random
from dataclasses import dataclass, asdict
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import numpy as np

logger = logging.getLogger(__name__)

BINANCE_KLINE_URL = "https://api.binance.com/api/v3/klines"
MAX_RETRIES = 3
MAX_RETRY_WAIT_S = 10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Kline1s:
    """Single 1-second kline."""
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MicrostructureContext:
    """Derived execution context from 300 x 1s klines."""

    # Raw data summary
    n_klines: int
    hourly_close: float           # Close of the 1h candle
    current_price: float          # Close of the last 1s kline

    # Price analysis
    max_price: float              # Highest price in 300s window (best theoretical short entry)
    min_price: float              # Lowest price in 300s window
    max_price_time_offset_s: int  # Seconds after hourly close when max was hit
    price_at_10s: float           # Price 10s after close
    price_at_30s: float           # Price 30s after close
    price_at_60s: float           # Price 60s after close

    # Derived metrics (bps = basis points relative to hourly_close)
    drift_bps: float              # (current - hourly_close) / hourly_close * 10000
    max_drift_bps: float          # (max_price - hourly_close) / hourly_close * 10000
    pullback_from_max_bps: float  # (max - current) / max * 10000
    initial_pullback_bps: float   # Dip below close in first 10s (positive = below close)

    # Volume profile
    volume_first_5s: float
    volume_5_30s: float
    volume_30_300s: float
    total_volume: float
    volume_ratio_early_late: float  # first_30s / last_270s

    # Classification
    entry_type: str               # "MARKET" | "LIMIT" | "SKIP"
    suggested_entry_price: float
    reason: str                   # Human-readable reason

    # State flags
    already_reverted: bool        # Price dropped > 50bps from max
    still_climbing: bool          # Price made new high in last 30s

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExitRecommendation:
    """Exit recommendation for an open S9 position."""
    action: str          # "HOLD" | "EXIT"
    reason: str
    urgency: str = ""    # "HIGH" | "MEDIUM" | "" (for HOLD)
    unrealized_pct: float = 0.0
    tape_slope_bps: float = 0.0
    volume_surge: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Binance 1s kline fetching
# ---------------------------------------------------------------------------

def fetch_1s_klines(
    symbol: str,
    start_time_ms: int,
    limit: int = 300,
    timeout: int = 10,
) -> list[Kline1s]:
    """Fetch 1-second klines from Binance REST API.

    Args:
        symbol: e.g., "BTCUSDT"
        start_time_ms: Start timestamp in milliseconds (inclusive)
        limit: Number of 1s klines to fetch (max 1000)
        timeout: Request timeout in seconds

    Returns:
        List of Kline1s, oldest first. Empty list on failure.
    """
    params = {
        "symbol": symbol,
        "interval": "1s",
        "startTime": int(start_time_ms),
        "limit": min(limit, 1000),
    }

    data = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(BINANCE_KLINE_URL, params=params, timeout=timeout)
            if resp.status_code in (418, 429):
                retry_after = int(resp.headers.get("Retry-After", "0"))
                backoff = 2 ** attempt + random.uniform(0, 1)
                wait = max(retry_after, min(backoff, MAX_RETRY_WAIT_S))
                logger.warning(
                    "Binance 1s rate limit for %s, retry in %.1fs (attempt %d)",
                    symbol, wait, attempt + 1,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt + random.uniform(0, 0.5))
                continue
            logger.warning("Binance 1s kline timeout after retries: %s", symbol)
            return []
        except Exception as e:
            logger.error("Binance 1s kline fetch failed for %s: %s", symbol, e)
            return []
    else:
        logger.warning("Binance 1s kline retries exhausted: %s", symbol)
        return []

    klines = []
    for k in data:
        klines.append(Kline1s(
            timestamp_ms=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        ))
    return klines


def fetch_1s_klines_batch(
    symbols: list[str],
    start_time_ms: int,
    limit: int = 300,
    max_workers: int = 8,
) -> dict[str, list[Kline1s]]:
    """Fetch 1s klines for multiple symbols in parallel.

    Returns:
        Dict mapping symbol -> list of Kline1s. Only includes symbols
        where data was successfully fetched.
    """
    results = {}

    def _fetch_one(sym):
        return sym, fetch_1s_klines(sym, start_time_ms, limit)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            try:
                sym, klines = future.result()
                if klines:
                    results[sym] = klines
            except Exception as e:
                logger.debug("1s kline batch fetch failed for %s: %s",
                             futures[future], e)

    return results


# ---------------------------------------------------------------------------
# Tape analysis: entry decision
# ---------------------------------------------------------------------------

def analyze_tape(
    hourly_close: float,
    klines: list[Kline1s],
) -> MicrostructureContext:
    """Analyze 300 x 1s klines to derive execution context for a short entry.

    Reads the actual tape — no hardcoded thresholds from research averages.
    Every decision is derived from what the price actually did in these
    specific 300 seconds for this specific symbol.

    Args:
        hourly_close: The close price of the 1h candle
        klines: Up to 300 x 1s klines starting from hourly close

    Returns:
        MicrostructureContext with derived execution instructions
    """
    if not klines or len(klines) < 10:
        return _empty_context(hourly_close, len(klines) if klines else 0,
                              "Insufficient 1s data (<10 klines)")

    closes = np.array([k.close for k in klines])
    highs = np.array([k.high for k in klines])
    lows = np.array([k.low for k in klines])
    volumes = np.array([k.volume for k in klines])
    n = len(klines)

    current_price = float(closes[-1])
    max_price = float(np.max(highs))
    min_price = float(np.min(lows))
    max_idx = int(np.argmax(highs))

    # Price at key offsets (safe indexing)
    price_at_10s = float(closes[min(9, n - 1)])
    price_at_30s = float(closes[min(29, n - 1)])
    price_at_60s = float(closes[min(59, n - 1)])

    # Metrics in basis points
    drift_bps = (current_price - hourly_close) / hourly_close * 10000
    max_drift_bps = (max_price - hourly_close) / hourly_close * 10000
    pullback_from_max_bps = (
        (max_price - current_price) / max_price * 10000 if max_price > 0 else 0
    )

    # Did price dip below close in first 10s?
    min_close_10s = float(np.min(closes[:min(10, n)]))
    initial_pullback_bps = (hourly_close - min_close_10s) / hourly_close * 10000

    # Volume profile
    vol_first_5 = float(np.sum(volumes[:min(5, n)]))
    vol_5_30 = float(np.sum(volumes[min(5, n):min(30, n)])) if n > 5 else 0.0
    vol_30_300 = float(np.sum(volumes[min(30, n):])) if n > 30 else 0.0
    total_vol = float(np.sum(volumes))
    vol_first_30 = vol_first_5 + vol_5_30
    vol_ratio = vol_first_30 / max(vol_30_300, 1e-10)

    # Is price still climbing? (new high in last 30s)
    still_climbing = False
    if n >= 30:
        recent_high = float(np.max(highs[-30:]))
        prior_high = float(np.max(highs[:-30])) if n > 30 else hourly_close
        still_climbing = (recent_high >= prior_high) and (recent_high > hourly_close)

    # Already reverted significantly from peak?
    already_reverted = (current_price < hourly_close) and (pullback_from_max_bps > 50)

    # --- Execution decision (derived from THIS tape, not from averages) ---
    entry_type, suggested_price, reason = _classify_entry(
        hourly_close, current_price, max_price, min_price,
        drift_bps, max_drift_bps, pullback_from_max_bps,
        still_climbing, already_reverted, n,
        closes, highs, volumes,
    )

    return MicrostructureContext(
        n_klines=n,
        hourly_close=hourly_close,
        current_price=current_price,
        max_price=max_price,
        min_price=min_price,
        max_price_time_offset_s=max_idx,
        price_at_10s=price_at_10s,
        price_at_30s=price_at_30s,
        price_at_60s=price_at_60s,
        drift_bps=drift_bps,
        max_drift_bps=max_drift_bps,
        pullback_from_max_bps=pullback_from_max_bps,
        initial_pullback_bps=initial_pullback_bps,
        volume_first_5s=vol_first_5,
        volume_5_30s=vol_5_30,
        volume_30_300s=vol_30_300,
        total_volume=total_vol,
        volume_ratio_early_late=vol_ratio,
        entry_type=entry_type,
        suggested_entry_price=suggested_price,
        reason=reason,
        already_reverted=already_reverted,
        still_climbing=still_climbing,
    )


def _classify_entry(
    hourly_close, current_price, max_price, min_price,
    drift_bps, max_drift_bps, pullback_from_max_bps,
    still_climbing, already_reverted, n,
    closes, highs, volumes,
) -> tuple[str, float, str]:
    """Derive entry instruction from tape state.

    For S9 shorts: we want to enter at the highest possible price.
    Decisions are based on what this specific tape shows.

    Returns (entry_type, suggested_price, reason).
    """
    if already_reverted:
        # Price already below hourly close and dropped significantly from peak.
        if current_price > hourly_close * 0.998:
            return (
                "MARKET", current_price,
                f"Reverted {pullback_from_max_bps:.1f}bps from peak but still "
                f"near close. Market order at {current_price:.6g}"
            )
        else:
            return (
                "SKIP", current_price,
                f"Already reverted {pullback_from_max_bps:.1f}bps from peak, "
                f"price {drift_bps:.1f}bps below close. Move may be exhausted."
            )

    if still_climbing:
        # Price still making new highs — more extended = stronger reversion.
        # For short: this is favorable if we believe in mean reversion.
        # Limit at current price; if it ticks up more, we get filled higher.
        return (
            "LIMIT", current_price,
            f"Still climbing (+{drift_bps:.1f}bps). Limit at "
            f"{current_price:.6g} — extended further, stronger setup"
        )

    if current_price > hourly_close:
        # Above close but momentum fading. Ideal short zone.
        return (
            "MARKET", current_price,
            f"Above close +{drift_bps:.1f}bps, momentum fading. "
            f"Market order at {current_price:.6g}"
        )

    # Below close. Less favorable for short entry.
    if abs(drift_bps) < 15:
        return (
            "MARKET", current_price,
            f"Near close ({drift_bps:+.1f}bps). "
            f"Market order at {current_price:.6g}"
        )

    return (
        "SKIP", current_price,
        f"Below close by {abs(drift_bps):.1f}bps. "
        f"Short entry unfavorable — reversion may be underway."
    )


def _empty_context(hourly_close, n_klines, reason):
    """Build a MicrostructureContext when data is insufficient."""
    return MicrostructureContext(
        n_klines=n_klines,
        hourly_close=hourly_close,
        current_price=hourly_close,
        max_price=hourly_close,
        min_price=hourly_close,
        max_price_time_offset_s=0,
        price_at_10s=hourly_close,
        price_at_30s=hourly_close,
        price_at_60s=hourly_close,
        drift_bps=0.0,
        max_drift_bps=0.0,
        pullback_from_max_bps=0.0,
        initial_pullback_bps=0.0,
        volume_first_5s=0.0,
        volume_5_30s=0.0,
        volume_30_300s=0.0,
        total_volume=0.0,
        volume_ratio_early_late=0.0,
        entry_type="SKIP",
        suggested_entry_price=hourly_close,
        reason=reason,
        already_reverted=False,
        still_climbing=False,
    )


# ---------------------------------------------------------------------------
# Tape analysis: exit decision (for position monitor)
# ---------------------------------------------------------------------------

def analyze_exit_tape(
    entry_price: float,
    klines: list[Kline1s],
    unrealized_pct: float,
) -> ExitRecommendation:
    """Analyze recent 1s klines for an open S9 short position.

    Detects early exit conditions based on what the tape actually shows:
    - Price reversal with volume (profit at risk)
    - Strong profit capture threshold
    - Accelerating upward momentum

    Args:
        entry_price: Short entry price
        klines: Recent 1s klines (e.g., last 300s)
        unrealized_pct: Current unrealized P&L percentage (positive = profit for short)

    Returns:
        ExitRecommendation with action and reasoning.
    """
    if not klines or len(klines) < 30:
        return ExitRecommendation(
            action="HOLD",
            reason="Insufficient tape data for exit analysis",
            unrealized_pct=unrealized_pct,
        )

    closes = np.array([k.close for k in klines])
    volumes = np.array([k.volume for k in klines])
    n = len(klines)

    current_price = float(closes[-1])

    # Price trajectory in last 60s (bps per 60s)
    window = min(60, n)
    last_window = closes[-window:]
    slope_bps = float(
        (last_window[-1] - last_window[0]) / last_window[0] * 10000
    ) if len(last_window) >= 2 else 0.0

    # Volume comparison: last 30s vs prior 30s
    if n >= 60:
        recent_vol = float(np.sum(volumes[-30:]))
        prior_vol = float(np.sum(volumes[-60:-30]))
        vol_surge = recent_vol / max(prior_vol, 1e-10)
    else:
        vol_surge = 1.0

    # --- Exit conditions ---

    # 1. Profitable >= 2% and price ticking back up with volume
    if unrealized_pct >= 2.0 and slope_bps > 5 and vol_surge > 1.5:
        return ExitRecommendation(
            action="EXIT",
            reason=(
                f"Unrealized +{unrealized_pct:.1f}%, price reversing "
                f"(+{slope_bps:.1f}bps/60s) with volume ({vol_surge:.1f}x). "
                f"Capture profit."
            ),
            urgency="HIGH",
            unrealized_pct=unrealized_pct,
            tape_slope_bps=slope_bps,
            volume_surge=vol_surge,
        )

    # 2. Profitable >= 3% — lock in strong profit regardless of tape
    if unrealized_pct >= 3.0:
        return ExitRecommendation(
            action="EXIT",
            reason=f"Unrealized +{unrealized_pct:.1f}% exceeds 3% threshold. Lock profit.",
            urgency="MEDIUM",
            unrealized_pct=unrealized_pct,
            tape_slope_bps=slope_bps,
            volume_surge=vol_surge,
        )

    # 3. Strong upward momentum with volume (even at modest profit)
    if slope_bps > 20 and vol_surge > 2.0 and unrealized_pct > 0:
        return ExitRecommendation(
            action="EXIT",
            reason=(
                f"Strong upward momentum (+{slope_bps:.1f}bps/60s, "
                f"vol {vol_surge:.1f}x). Protect +{unrealized_pct:.1f}% gain."
            ),
            urgency="HIGH",
            unrealized_pct=unrealized_pct,
            tape_slope_bps=slope_bps,
            volume_surge=vol_surge,
        )

    return ExitRecommendation(
        action="HOLD",
        reason=f"Unrealized {unrealized_pct:+.1f}%, no exit trigger met",
        unrealized_pct=unrealized_pct,
        tape_slope_bps=slope_bps,
        volume_surge=vol_surge,
    )


# ---------------------------------------------------------------------------
# Backtest helpers: simulate entry strategies
# ---------------------------------------------------------------------------

def simulate_entries(
    hourly_close: float,
    klines: list[Kline1s],
) -> dict[str, float]:
    """Simulate multiple short entry strategies on historical 1s klines.

    Returns dict mapping strategy name -> entry price achieved.
    For shorts, higher entry = better.

    Strategies:
    1. naive: Enter at hourly close (baseline)
    2. theoretical_best: max(high) in 300s window (unreachable ceiling)
    3. adaptive_limit: Limit at close*1.0003 for 60s; if not filled, market at T+60s
    4. wait_for_peak: Enter at first 3-bar local high after T+10s; market at T+60s if none
    5. vwap_60s: Enter at VWAP of first 60s
    6. tape_aware: Use analyze_tape() classification
    """
    strategies = {"naive": hourly_close}

    if not klines or len(klines) < 10:
        # All strategies degenerate to naive with insufficient data
        for name in ["theoretical_best", "adaptive_limit", "wait_for_peak",
                     "vwap_60s", "tape_aware"]:
            strategies[name] = hourly_close
        return strategies

    highs = np.array([k.high for k in klines])
    closes = np.array([k.close for k in klines])
    volumes = np.array([k.volume for k in klines])
    n = len(klines)

    # 1. Theoretical best: max high over entire window
    strategies["theoretical_best"] = float(np.max(highs))

    # 2. Adaptive limit: limit at close*1.0003, check if filled in first 60s
    limit_price = hourly_close * 1.0003
    filled_limit = False
    for i in range(min(60, n)):
        if highs[i] >= limit_price:
            strategies["adaptive_limit"] = limit_price
            filled_limit = True
            break
    if not filled_limit:
        strategies["adaptive_limit"] = float(closes[min(59, n - 1)])

    # 3. Wait for peak: first 3-bar local high after T+10s
    found_peak = False
    for i in range(max(10, 2), min(60, n - 1)):
        if highs[i] >= highs[i - 1] and highs[i] >= highs[i - 2]:
            if i + 1 < n and highs[i] > highs[i + 1]:
                strategies["wait_for_peak"] = float(highs[i])
                found_peak = True
                break
    if not found_peak:
        strategies["wait_for_peak"] = float(closes[min(59, n - 1)])

    # 4. VWAP of first 60s
    window_60 = min(60, n)
    vwap_prices = (closes[:window_60] * volumes[:window_60])
    total_vol = np.sum(volumes[:window_60])
    if total_vol > 0:
        strategies["vwap_60s"] = float(np.sum(vwap_prices) / total_vol)
    else:
        strategies["vwap_60s"] = hourly_close

    # 5. Tape-aware: use classification
    ctx = analyze_tape(hourly_close, klines)
    if ctx.entry_type == "SKIP":
        strategies["tape_aware"] = hourly_close  # Fall back to naive
    else:
        strategies["tape_aware"] = ctx.suggested_entry_price

    return strategies
