"""
DXY (US Dollar Index) Regime Gate — soft score multiplier for crypto signals.

When DXY is trending up strongly, crypto tends to underperform. This module
provides a regime-aware multiplier that penalizes signal scores during
strong-dollar environments and slightly boosts them during weak-dollar periods.

Architecture:
  - Pluggable data source via refresh_dxy_regime(slope, source)
  - Cached in Redis with 4h TTL (key: dxy:regime)
  - Default: NEUTRAL (multiplier 1.0) when no data source is configured
  - Controlled by DXY_REGIME_ENABLED env var (default: false)

Regime classification (20-day DXY slope):
  STRONG_UP  (slope > 0.10)  → 0.70 multiplier (30% penalty)
  UP         (slope > 0.03)  → 0.85 multiplier (15% penalty)
  NEUTRAL    (-0.03 to 0.03) → 1.00 multiplier (no effect)
  DOWN       (slope < -0.03) → 1.10 multiplier (10% boost)
  STRONG_DOWN(slope < -0.10) → 1.15 multiplier (15% boost)

Usage in signal_worker.py:
  from dxy_regime import get_dxy_multiplier
  calibrated_score *= get_dxy_multiplier(redis_client)
"""

import json
import logging
import os

logger = logging.getLogger("dxy_regime")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DXY_REGIME_ENABLED = os.getenv("DXY_REGIME_ENABLED", "0") == "1"

REDIS_KEY = "dxy:regime"
REDIS_TTL_S = 4 * 3600  # 4 hours

# Regime thresholds (slope of DXY over 20 trading days)
SLOPE_STRONG_UP = 0.10
SLOPE_UP = 0.03
SLOPE_DOWN = -0.03
SLOPE_STRONG_DOWN = -0.10

# Score multipliers per regime
REGIME_MULTIPLIERS = {
    "STRONG_UP": 0.70,
    "UP": 0.85,
    "NEUTRAL": 1.0,
    "DOWN": 1.10,
    "STRONG_DOWN": 1.15,
}

_DEFAULT_REGIME = {
    "regime": "NEUTRAL",
    "multiplier": 1.0,
    "slope": None,
    "source": "default",
}


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def classify_slope(slope: float) -> str:
    """Classify DXY slope into a regime label."""
    if slope > SLOPE_STRONG_UP:
        return "STRONG_UP"
    elif slope > SLOPE_UP:
        return "UP"
    elif slope < SLOPE_STRONG_DOWN:
        return "STRONG_DOWN"
    elif slope < SLOPE_DOWN:
        return "DOWN"
    else:
        return "NEUTRAL"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_dxy_regime(redis_client) -> dict:
    """Get current DXY regime from Redis cache or return default.

    Returns dict with keys: regime, multiplier, slope, source.
    Never raises — returns NEUTRAL default on any failure.
    """
    if not DXY_REGIME_ENABLED:
        return _DEFAULT_REGIME.copy()

    try:
        cached = redis_client.get(REDIS_KEY)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.debug("Failed to read DXY regime from Redis: %s", e)

    return _DEFAULT_REGIME.copy()


def get_dxy_multiplier(redis_client) -> float:
    """Get the DXY regime score multiplier.

    Convenience wrapper — returns just the float multiplier.
    Returns 1.0 if disabled or on any failure.
    """
    regime = get_dxy_regime(redis_client)
    return regime.get("multiplier", 1.0)


def refresh_dxy_regime(redis_client, slope: float, source: str = "external") -> dict:
    """Update the DXY regime cache with a new slope measurement.

    Called by an external data collector (cron job, data pipeline, etc.)
    when fresh DXY data is available.

    Args:
        redis_client: Redis connection
        slope: Slope of DXY over the measurement window (e.g. 20 trading days)
        source: Identifier for the data source (e.g. 'yahoo', 'fred', 'manual')

    Returns:
        The regime dict that was cached.
    """
    regime_label = classify_slope(slope)
    multiplier = REGIME_MULTIPLIERS[regime_label]

    regime = {
        "regime": regime_label,
        "multiplier": multiplier,
        "slope": round(slope, 6),
        "source": source,
    }

    try:
        redis_client.set(REDIS_KEY, json.dumps(regime), ex=REDIS_TTL_S)
        logger.info(
            "DXY regime updated: %s (slope=%.4f, mult=%.2f, source=%s)",
            regime_label, slope, multiplier, source,
        )
    except Exception as e:
        logger.warning("Failed to cache DXY regime to Redis: %s", e)

    return regime
