"""
Market Intelligence API Router (Wave 0)
========================================

Five data surfaces over existing infrastructure:
  GET /v1/derivatives/dashboard    — funding rates, OI, long/short, taker flow
  GET /v1/universe/heatmap         — treemap data for all monitored symbols
  GET /v1/symbols/{symbol}/seasonality — monthly return patterns from OHLCV
  GET /v1/market/fear-greed        — current Fear & Greed index (alternative.me)
  GET /v1/analytics/indicator-correlations — 54×54 indicator correlation matrix
"""

import time
import logging
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import redis
from fastapi import APIRouter, Query, HTTPException

from api.app.db_pool import get_conn as _pool_get_conn
from governance.bootstrap import get_config
from governance.cache_contract import (
    COMPOSABLE_FIELDS, ind_snap_key, get_universe, TF_SECONDS,
)
from governance.regime import fetch_fear_greed_index

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-intel"])


def _get_redis():
    cfg = get_config()
    return redis.Redis.from_url(cfg.Worker.REDIS_URL, decode_responses=True)


def _envelope(data, compute_start: float, **extra_meta) -> dict:
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "v1",
        "compute_ms": round((time.time() - compute_start) * 1000, 1),
    }
    meta.update(extra_meta)
    return {"data": data, "meta": meta}


# ---------------------------------------------------------------------------
# W0-A: Derivatives Dashboard
# ---------------------------------------------------------------------------

@router.get("/derivatives/dashboard")
def derivatives_dashboard(
    limit: int = Query(default=50, ge=1, le=300),
):
    """Latest derivatives snapshot per symbol: funding, OI changes, L/S ratio, taker flow."""
    t0 = time.time()
    cfg = get_config().MarketIntel

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            # Latest row per symbol from materialized features
            cur.execute("""
                SELECT DISTINCT ON (symbol)
                    symbol, bar_start_ts,
                    funding_rate, funding_rate_ma8, funding_rate_zscore,
                    oi_value, oi_change_pct_1h, oi_change_pct_4h, oi_change_pct_24h,
                    ls_ratio, ls_ratio_change_4h,
                    taker_buy_ratio, taker_imbalance,
                    data_completeness, materialized_at
                FROM derivatives_features_1h
                WHERE data_completeness >= %s
                ORDER BY symbol, bar_start_ts DESC
            """, (cfg.derivatives_min_completeness,))
            rows = cur.fetchall()

            # Overall data freshness
            cur.execute("SELECT MAX(materialized_at) FROM derivatives_features_1h")
            max_mat = cur.fetchone()[0]

    symbols = []
    for r in rows:
        symbols.append({
            "symbol": r[0],
            "bar_start_ts": r[1].isoformat() if r[1] else None,
            "funding_rate": r[2],
            "funding_rate_ma8": r[3],
            "funding_rate_zscore": r[4],
            "oi_value": r[5],
            "oi_change_pct_1h": r[6],
            "oi_change_pct_4h": r[7],
            "oi_change_pct_24h": r[8],
            "ls_ratio": r[9],
            "ls_ratio_change_4h": r[10],
            "taker_buy_ratio": r[11],
            "taker_imbalance": r[12],
            "data_completeness": r[13],
        })

    # Sort by absolute funding rate (most extreme first)
    symbols.sort(key=lambda s: abs(s["funding_rate"] or 0), reverse=True)
    symbols = symbols[:limit]

    return _envelope(
        {
            "symbols": symbols,
            "total_with_data": len(rows),
            "data_as_of_ts": max_mat.isoformat() if max_mat else None,
        },
        t0,
    )


# ---------------------------------------------------------------------------
# W0-B: Crypto Heatmap
# ---------------------------------------------------------------------------

@router.get("/universe/heatmap")
def universe_heatmap(
    period: str = Query(default="24h"),
    tf: str = Query(default="1h"),
):
    """Treemap data: symbol, % change, volume for all monitored symbols."""
    t0 = time.time()
    cfg = get_config().MarketIntel

    allowed = cfg.heatmap_allowed_periods
    if period not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"period must be one of {allowed}",
        )

    r = _get_redis()
    symbols = get_universe(r)
    if not symbols:
        return _envelope({"tiles": [], "symbol_count": 0}, t0)

    staleness_limit = TF_SECONDS.get(tf, 3600) * cfg.heatmap_staleness_multiplier

    now_ts = time.time()
    tiles = []
    pipe = r.pipeline(transaction=False)
    for sym in symbols:
        pipe.hgetall(ind_snap_key(sym, tf))
    results = pipe.execute()

    for sym, snap in zip(symbols, results):
        if not snap:
            continue
        try:
            close = float(snap.get("close", 0))
            opn = float(snap.get("open", 0))
            volume_z = float(snap.get("volume_z", 0)) if snap.get("volume_z") else 0
            bar_ts = float(snap.get("bar_start_ts", 0))
        except (ValueError, TypeError):
            continue

        if close <= 0 or opn <= 0:
            continue

        age = now_ts - bar_ts if bar_ts > 0 else 999999
        pct_change = (close - opn) / opn * 100.0

        tiles.append({
            "symbol": sym,
            "close": close,
            "pct_change": round(pct_change, 3),
            "volume_z": round(volume_z, 2),
            "stale": age > staleness_limit,
            "as_of_ts": bar_ts,
        })

    # Sort by absolute volume_z for treemap size weighting
    tiles.sort(key=lambda t: abs(t["volume_z"]), reverse=True)

    return _envelope(
        {"tiles": tiles, "symbol_count": len(tiles), "period": period, "tf": tf},
        t0,
    )


# ---------------------------------------------------------------------------
# W0-C: Seasonal Patterns
# ---------------------------------------------------------------------------

@router.get("/symbols/{symbol}/seasonality")
def symbol_seasonality(
    symbol: str,
    years: int = Query(default=None, ge=1, le=10),
):
    """Monthly return patterns from daily OHLCV data."""
    t0 = time.time()
    cfg = get_config().MarketIntel
    if years is None:
        years = cfg.seasonality_default_years

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            # Get monthly open/close from daily candles
            cur.execute("""
                WITH monthly AS (
                    SELECT
                        DATE_TRUNC('month', bar_start_ts) AS month,
                        (array_agg(open ORDER BY bar_start_ts ASC))[1] AS month_open,
                        (array_agg(close ORDER BY bar_start_ts DESC))[1] AS month_close
                    FROM ohlcv
                    WHERE symbol = %s
                      AND timeframe = '1d'
                      AND bar_start_ts >= NOW() - make_interval(years => %s)
                    GROUP BY DATE_TRUNC('month', bar_start_ts)
                    HAVING COUNT(*) >= 15
                )
                SELECT
                    EXTRACT(MONTH FROM month)::INT AS month_num,
                    month,
                    month_open,
                    month_close,
                    (month_close - month_open) / NULLIF(month_open, 0) * 100.0 AS return_pct
                FROM monthly
                ORDER BY month
            """, (symbol, years))
            rows = cur.fetchall()

    if not rows:
        return _envelope({
            "symbol": symbol,
            "monthly_returns": [],
            "data_months": 0,
            "warning": f"No daily OHLCV data found for {symbol}",
        }, t0)

    # Group by month number (1-12), compute avg/median
    by_month = defaultdict(list)
    for r in rows:
        by_month[r[0]].append(r[4])

    data_months = len(rows)
    monthly = []
    month_names = [
        "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    for m in range(1, 13):
        returns = by_month.get(m, [])
        if returns:
            avg = sum(returns) / len(returns)
            sorted_r = sorted(returns)
            mid = len(sorted_r) // 2
            median = sorted_r[mid] if len(sorted_r) % 2 else (sorted_r[mid - 1] + sorted_r[mid]) / 2
            monthly.append({
                "month": m,
                "month_name": month_names[m],
                "avg_return_pct": round(avg, 2),
                "median_return_pct": round(median, 2),
                "sample_count": len(returns),
                "min_return_pct": round(min(returns), 2),
                "max_return_pct": round(max(returns), 2),
            })
        else:
            monthly.append({
                "month": m,
                "month_name": month_names[m],
                "avg_return_pct": None,
                "median_return_pct": None,
                "sample_count": 0,
                "min_return_pct": None,
                "max_return_pct": None,
            })

    warning = None
    if data_months < cfg.seasonality_min_months:
        warning = (
            f"Only {data_months} months of data available (minimum {cfg.seasonality_min_months} "
            f"recommended). Results may not be statistically meaningful."
        )

    return _envelope({
        "symbol": symbol,
        "monthly_returns": monthly,
        "data_months": data_months,
        "years_requested": years,
        "warning": warning,
    }, t0)


# ---------------------------------------------------------------------------
# W0-D: Fear & Greed Gauge
# ---------------------------------------------------------------------------

@router.get("/market/fear-greed")
def fear_greed():
    """Current Fear & Greed index from alternative.me (cached per fng_cache_ttl_s)."""
    t0 = time.time()
    result = fetch_fear_greed_index()

    value = result.get("value")
    classification = result.get("classification")

    # Derive regime label
    if value is None:
        regime = "unavailable"
    elif value <= 25:
        regime = "extreme_fear"
    elif value <= 45:
        regime = "fear"
    elif value <= 55:
        regime = "neutral"
    elif value <= 75:
        regime = "greed"
    else:
        regime = "extreme_greed"

    return _envelope({
        "value": value,
        "classification": classification,
        "regime": regime,
    }, t0)


# ---------------------------------------------------------------------------
# W0-E: Indicator Correlation Matrix
# ---------------------------------------------------------------------------

# Sorted field list for stable matrix ordering
_CORR_FIELDS = sorted(COMPOSABLE_FIELDS)


@router.get("/analytics/indicator-correlations")
def indicator_correlations(
    tf: str = Query(default="1h"),
):
    """54×54 indicator correlation matrix from current ind_snap values."""
    t0 = time.time()
    r = _get_redis()
    symbols = get_universe(r)

    if not symbols:
        return _envelope({"fields": _CORR_FIELDS, "matrix": [], "symbol_count": 0}, t0)

    # Pipeline read all snapshots
    pipe = r.pipeline(transaction=False)
    for sym in symbols:
        pipe.hgetall(ind_snap_key(sym, tf))
    results = pipe.execute()

    # Build (n_symbols, n_fields) matrix
    n_fields = len(_CORR_FIELDS)
    field_idx = {f: i for i, f in enumerate(_CORR_FIELDS)}
    rows = []

    for snap in results:
        if not snap:
            continue
        row = [np.nan] * n_fields
        valid_count = 0
        for field, idx in field_idx.items():
            val = snap.get(field)
            if val is not None:
                try:
                    row[idx] = float(val)
                    valid_count += 1
                except (ValueError, TypeError):
                    pass
        # Only include symbols with >50% of fields populated
        if valid_count > n_fields * 0.5:
            rows.append(row)

    if len(rows) < 10:
        return _envelope({
            "fields": _CORR_FIELDS,
            "matrix": [],
            "symbol_count": len(rows),
            "warning": "Too few symbols with data for meaningful correlations",
        }, t0)

    mat = np.array(rows, dtype=np.float64)

    # np.corrcoef expects (features, observations), so transpose
    # Use masked computation to handle NaN
    with np.errstate(invalid="ignore"):
        # Replace NaN with column means for correlation
        col_means = np.nanmean(mat, axis=0)
        for col in range(n_fields):
            mask = np.isnan(mat[:, col])
            mat[mask, col] = col_means[col]

        corr = np.corrcoef(mat.T)

    # Replace NaN in correlation matrix with 0
    corr = np.where(np.isnan(corr), 0.0, corr)

    # Convert to nested list, rounded
    matrix = [[round(float(corr[i, j]), 3) for j in range(n_fields)] for i in range(n_fields)]

    return _envelope({
        "fields": _CORR_FIELDS,
        "matrix": matrix,
        "symbol_count": len(rows),
    }, t0)
