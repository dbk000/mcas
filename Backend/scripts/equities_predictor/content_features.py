"""EQ shared library: content, frequency, session, and excess-return features.

Used by both EQ-WS1 and EQ-WS2 scripts. All functions respect available_at_ts
discipline — features use only data available at prediction time.

Step 0C (SME Gap 1): excess-return labelling via rolling beta vs SPY.
Step 1: content/frequency/session feature computation.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ────────────────────────────────────────────────────────────────
# ETF list — excluded from label dataset, used as benchmarks only
# (SME rec C)
# ────────────────────────────────────────────────────────────────
ETF_SYMBOLS = frozenset({
    "SPY", "QQQ", "DIA", "IWM",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
    "XLP", "XLRE", "XLU", "XLV", "XLY",
})


def is_etf(symbol: str) -> bool:
    return symbol in ETF_SYMBOLS


# ────────────────────────────────────────────────────────────────
# Step 0C: Excess-return labelling (SME Gap 1)
# ────────────────────────────────────────────────────────────────

def compute_rolling_beta(
    symbol_returns: np.ndarray,
    spy_returns: np.ndarray,
    window: int = 60,
) -> np.ndarray:
    """Rolling OLS beta of symbol vs SPY over `window` trading days.

    Returns array same length as inputs; first `window-1` values are NaN.
    """
    n = len(symbol_returns)
    beta = np.full(n, np.nan)
    for i in range(window - 1, n):
        y = symbol_returns[i - window + 1: i + 1]
        x = spy_returns[i - window + 1: i + 1]
        x_dm = x - x.mean()
        denom = np.dot(x_dm, x_dm)
        if denom < 1e-15:
            beta[i] = 1.0  # degenerate — assume market beta
        else:
            beta[i] = np.dot(x_dm, y - y.mean()) / denom
    return beta


def compute_excess_returns(
    symbol_returns: np.ndarray,
    spy_returns: np.ndarray,
    beta: np.ndarray,
) -> np.ndarray:
    """Excess return = symbol_return - beta * spy_return."""
    return symbol_returns - beta * spy_returns


def compute_residual_vol(
    excess_returns: np.ndarray,
    window: int = 20,
) -> np.ndarray:
    """Rolling std of excess returns over `window` days."""
    n = len(excess_returns)
    vol = np.full(n, np.nan)
    for i in range(window - 1, n):
        chunk = excess_returns[i - window + 1: i + 1]
        valid = chunk[np.isfinite(chunk)]
        if len(valid) >= 5:
            vol[i] = np.std(valid, ddof=1)
    return vol


def compute_realized_vol(
    returns: np.ndarray,
    window: int = 20,
) -> np.ndarray:
    """Rolling std of raw returns over `window` days."""
    n = len(returns)
    vol = np.full(n, np.nan)
    for i in range(window - 1, n):
        chunk = returns[i - window + 1: i + 1]
        valid = chunk[np.isfinite(chunk)]
        if len(valid) >= 5:
            vol[i] = np.std(valid, ddof=1)
    return vol


def label_excess_return(
    forward_return: float,
    beta: float,
    spy_forward_return: float,
    residual_vol: float,
    z_threshold: float = 1.0,
) -> str:
    """Label a single observation using excess-return z-score.

    Returns: 'MOVED_UP', 'MOVED_DOWN', or 'FLAT'.
    """
    if not np.isfinite(beta) or not np.isfinite(residual_vol) or residual_vol < 1e-8:
        return "FLAT"
    excess = forward_return - beta * spy_forward_return
    z = excess / residual_vol
    if z > z_threshold:
        return "MOVED_UP"
    elif z < -z_threshold:
        return "MOVED_DOWN"
    return "FLAT"


def label_raw_return(
    forward_return: float,
    realized_vol: float,
    z_threshold: float = 1.0,
) -> str:
    """Label using raw vol-normalized z-score (diagnostic cross-check)."""
    if not np.isfinite(realized_vol) or realized_vol < 1e-8:
        return "FLAT"
    z = forward_return / realized_vol
    if z > z_threshold:
        return "MOVED_UP"
    elif z < -z_threshold:
        return "MOVED_DOWN"
    return "FLAT"


def moulton_effective_n(
    n_symbols: int,
    rho_avg: float,
    n_days: int,
) -> float:
    """Moulton-corrected effective sample size (SME Gap 2).

    Per-day effective N = n_symbols / (1 + (n_symbols - 1) * rho_avg).
    Total effective N = per_day * n_days.
    """
    if n_symbols <= 1 or rho_avg <= 0:
        return float(n_symbols * n_days)
    per_day = n_symbols / (1.0 + (n_symbols - 1) * rho_avg)
    return per_day * n_days


def estimate_cross_sectional_correlation(
    returns_matrix: np.ndarray,
) -> float:
    """Estimate average pairwise correlation from (n_days, n_symbols) matrix.

    Uses only finite values. Returns 0.0 if insufficient data.
    """
    n_days, n_symbols = returns_matrix.shape
    if n_symbols < 2 or n_days < 10:
        return 0.0
    # Compute correlation matrix
    # Replace NaN with column means for correlation
    col_means = np.nanmean(returns_matrix, axis=0)
    filled = np.where(np.isfinite(returns_matrix), returns_matrix, col_means)
    corr = np.corrcoef(filled.T)
    # Average off-diagonal
    mask = ~np.eye(n_symbols, dtype=bool)
    avg_corr = np.nanmean(corr[mask])
    return float(avg_corr) if np.isfinite(avg_corr) else 0.0


# ────────────────────────────────────────────────────────────────
# Content features
# ────────────────────────────────────────────────────────────────

def compute_tone_score(title: str, source: str, anti_bot_score: float = 0.0) -> float:
    """Wrap tone_scorer.score_title_tone(). Returns 0.0 for non-NEWS sources."""
    try:
        from equities_strategies.tone_scorer import score_title_tone  # type: ignore[import-untyped]
        return score_title_tone(title, source=source, anti_bot_score=anti_bot_score)
    except ImportError:
        # Fallback: try relative import path
        try:
            # In Docker, tone_scorer is at /app/equities_strategies/tone_scorer.py
            import importlib
            import sys
            import os
            if "/app" not in sys.path:
                sys.path.insert(0, "/app")
            from equities_strategies.tone_scorer import score_title_tone
            return score_title_tone(title, source=source, anti_bot_score=anti_bot_score)
        except ImportError:
            return 0.0


def compute_simple_sentiment(text: str) -> float:
    """Wrap text_utils.simple_sentiment_score(). Works on all event types."""
    try:
        from text_utils import simple_sentiment_score  # type: ignore[import-untyped]
        return simple_sentiment_score(text)
    except ImportError:
        try:
            import sys
            import os
            predictor_dir = os.path.join(os.path.dirname(__file__))
            if predictor_dir not in sys.path:
                sys.path.insert(0, predictor_dir)
            from text_utils import simple_sentiment_score
            return simple_sentiment_score(text)
        except ImportError:
            return 0.0


def compute_filing_type_features(event_type: str) -> dict[str, int]:
    """One-hot encoding for filing type classification."""
    types = {
        "filing_type_8k": int(event_type == "SEC_8-K"),
        "filing_type_10q": int(event_type == "SEC_10-Q"),
        "filing_type_10k": int(event_type == "SEC_10-K"),
        "is_news": int(event_type.startswith("NEWS")),
        "is_earnings_event": int(event_type in ("NEWS_EARNINGS", "SEC_EARNINGS")),
        "is_analyst_event": int(event_type == "NEWS_ANALYST"),
    }
    return types


def compute_event_count_deviation(
    event_count_24h: int,
    median_event_count_24h_30d: float,
) -> float:
    """Event count normalized to per-symbol baseline."""
    if median_event_count_24h_30d < 0.1:
        return 0.0
    return event_count_24h / median_event_count_24h_30d


def compute_mention_counts(
    mentions: list[dict[str, Any]],
    as_of_ts: datetime,
) -> dict[str, int]:
    """Count raw mentions in 6h/24h/3d windows backward from as_of_ts."""
    windows = {"mention_count_6h": 6, "mention_count_24h": 24, "mention_count_3d": 72}
    result = {}
    for key, hours in windows.items():
        cutoff = as_of_ts - timedelta(hours=hours)
        count = sum(
            1 for m in mentions
            if m.get("available_at_ts") is not None
            and cutoff <= m["available_at_ts"] <= as_of_ts
        )
        result[key] = count
    return result


def compute_mention_velocity(
    mentions: list[dict[str, Any]],
    as_of_ts: datetime,
) -> float:
    """Rate of change: mention_count_6h / mention_count_24h. 0 if no 24h mentions."""
    counts = compute_mention_counts(mentions, as_of_ts)
    if counts["mention_count_24h"] < 1:
        return 0.0
    return counts["mention_count_6h"] / counts["mention_count_24h"]


def compute_source_diversity(
    events: list[dict[str, Any]],
    as_of_ts: datetime,
    window_hours: int = 24,
) -> int:
    """Count distinct sources (SEC vs NEWS — effectively binary with dead GDELT/Reddit)."""
    cutoff = as_of_ts - timedelta(hours=window_hours)
    sources = set()
    for ev in events:
        ev_ts = ev.get("available_at_ts")
        if ev_ts is not None and cutoff <= ev_ts <= as_of_ts:
            src = ev.get("source", "")
            if src:
                sources.add(src)
    return len(sources)


def compute_multi_source_confirmation(
    events: list[dict[str, Any]],
    as_of_ts: datetime,
    window_hours: int = 24,
) -> int:
    """1 if both SEC and news sources present in window, 0 otherwise."""
    cutoff = as_of_ts - timedelta(hours=window_hours)
    has_sec = False
    has_news = False
    for ev in events:
        ev_ts = ev.get("available_at_ts")
        if ev_ts is not None and cutoff <= ev_ts <= as_of_ts:
            et = ev.get("event_type", "")
            if et.startswith("SEC"):
                has_sec = True
            elif et.startswith("NEWS"):
                has_news = True
            if has_sec and has_news:
                return 1
    return 0


# ────────────────────────────────────────────────────────────────
# Session / market structure features
# ────────────────────────────────────────────────────────────────

def compute_session_features(ts: datetime) -> dict[str, Any]:
    """Extended session state with ETH_PREMARKET/ETH_AFTERHOURS split.

    Returns dict with: session_state, hours_to_next_open, hours_since_close,
    day_of_week.
    """
    from calendar import session_state, next_open_ts, last_close_ts  # type: ignore[import-untyped]

    et_time = ts.astimezone(ET).time()
    base_state = session_state(ts)

    # Split ETH into premarket/afterhours
    if base_state == "ETH":
        if et_time < time(9, 30):
            detailed_state = "ETH_PREMARKET"
        else:
            detailed_state = "ETH_AFTERHOURS"
    else:
        detailed_state = base_state

    next_open = next_open_ts(ts)
    last_close = last_close_ts(ts)

    return {
        "session_state": detailed_state,
        "hours_to_next_open": (next_open - ts).total_seconds() / 3600.0,
        "hours_since_close": (ts - last_close).total_seconds() / 3600.0,
        "day_of_week": ts.astimezone(ET).weekday(),
    }


def compute_earnings_proximity(
    ts: datetime,
    earnings_dates: list[datetime],
) -> dict[str, Any]:
    """Days to/from nearest earnings, plus window flag.

    Args:
        ts: current timestamp (UTC)
        earnings_dates: list of earnings timestamps (UTC)

    Returns:
        dict with days_to_earnings (negative = past), in_earnings_window (±5 days)
    """
    if not earnings_dates:
        return {"days_to_earnings": None, "in_earnings_window": 0}

    ts_date = ts.astimezone(ET).date()
    min_delta = None
    for edt in earnings_dates:
        ed = edt if isinstance(edt, datetime) else datetime.combine(edt, time(16, 0), tzinfo=ET)
        delta_days = (ed.astimezone(ET).date() - ts_date).days
        if min_delta is None or abs(delta_days) < abs(min_delta):
            min_delta = delta_days

    in_window = 1 if min_delta is not None and abs(min_delta) <= 5 else 0
    return {
        "days_to_earnings": min_delta,
        "in_earnings_window": in_window,
    }


# ────────────────────────────────────────────────────────────────
# Feature viability audit (Phase 0.5)
# ────────────────────────────────────────────────────────────────

def feature_viability_audit(
    feature_matrix: dict[str, list[float | None]],
) -> dict[str, dict[str, Any]]:
    """Audit features for viability. Returns per-feature stats.

    Kill rules:
      - >80% zero/null → dead
      - variance < 1e-8 → dead (constant)
      - effective cardinality < 3 → degenerate (SME rec D)
    """
    results = {}
    for feat_name, values in feature_matrix.items():
        n_total = len(values)
        if n_total == 0:
            results[feat_name] = {
                "status": "dead", "reason": "no_data",
                "zero_rate": 1.0, "variance": 0.0,
                "cardinality": 0, "n_total": 0,
            }
            continue

        # Count nulls and zeros
        n_null = sum(1 for v in values if v is None or (isinstance(v, float) and math.isnan(v)))
        n_zero = sum(1 for v in values if v is not None and v == 0)
        n_zero_or_null = n_null + n_zero
        zero_rate = n_zero_or_null / n_total

        # Variance of non-null values
        finite_vals = [v for v in values if v is not None and np.isfinite(v)]
        variance = float(np.var(finite_vals)) if len(finite_vals) >= 2 else 0.0

        # Effective cardinality of non-zero values
        non_zero_vals = [v for v in finite_vals if v != 0]
        unique_non_zero = len(set(round(v, 8) for v in non_zero_vals))

        # Classification
        if zero_rate > 0.8:
            status, reason = "dead", "zero_rate_above_80pct"
        elif variance < 1e-8:
            status, reason = "dead", "constant"
        elif unique_non_zero < 3:
            status, reason = "degenerate", "cardinality_below_3"
        else:
            status, reason = "viable", ""

        results[feat_name] = {
            "status": status,
            "reason": reason,
            "zero_rate": round(zero_rate, 4),
            "variance": round(variance, 8),
            "cardinality": unique_non_zero,
            "n_total": n_total,
            "n_non_null": len(finite_vals),
            "coverage_rate": round(len(finite_vals) / n_total, 4),
        }

    return results


# ────────────────────────────────────────────────────────────────
# Feature liveability check
# ────────────────────────────────────────────────────────────────

def is_feature_liveable(feature_name: str) -> bool:
    """All features in this library use available_at_ts and are liveable."""
    return True
