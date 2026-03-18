# Deploy on VPS Server
# cd /home/darren/Documents/MCAS/Backend

# =====================================================================
# STEP 1: Add import to top of api/app/routers/control.py
#
# Find the line:
#   from governance.bootstrap import get_config, strategy_active_setup_ids
# Change it to:
#   from governance.bootstrap import get_config, strategy_active_setup_ids
#   from governance.cache_contract import TF_SECONDS, STALENESS_MULTIPLIER
#
# Also add to the datetime imports if not present:
#   from datetime import datetime, timezone, timedelta
# (timedelta is the new one — datetime and timezone are likely already there)
# =====================================================================

# =====================================================================
# STEP 2: Append this endpoint to end of api/app/routers/control.py
# =====================================================================


@router.get("/data/availability")
def get_data_availability(
    limit: int = Query(default=50, ge=1, le=500, description="Max stale symbols to return"),
):
    """OHLCV data coverage report: symbols x timeframes, freshness, staleness.

    Staleness model: governed by TF_SECONDS * STALENESS_MULTIPLIER from
    governance.cache_contract (same model as check_staleness / invariant #23).
    Timeframe list: governed by get_config().Worker.DEFAULT_TIMEFRAMES.
    """
    t0 = time.time()

    # --- Registry-governed parameters ---
    governed_timeframes = get_config().Worker.DEFAULT_TIMEFRAMES.split(",")

    # Build per-timeframe staleness thresholds from the registry
    # e.g. 1h -> 3600 * 2.5 = 9000s, 1d -> 86400 * 2.5 = 216000s
    staleness_thresholds = {
        tf: TF_SECONDS.get(tf, 60) * STALENESS_MULTIPLIER
        for tf in governed_timeframes
    }

    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Per-timeframe coverage
            cur.execute("""
                SELECT
                    timeframe,
                    COUNT(DISTINCT symbol) AS symbol_count,
                    MAX(bar_start_ts)      AS latest_bar,
                    MIN(bar_start_ts)      AS earliest_bar,
                    MAX(as_of_ts)          AS freshest_ingestion,
                    COUNT(*)               AS total_rows
                FROM ohlcv
                GROUP BY timeframe
                ORDER BY timeframe;
            """)
            tf_rows = cur.fetchall()

            # Stale symbols: age > TF_SECONDS * STALENESS_MULTIPLIER
            # Compute cutoff timestamps in Python, pass as params (not SQL string interpolation)
            case_parts = []
            case_params = []
            for tf_name in governed_timeframes:
                threshold_s = staleness_thresholds[tf_name]
                cutoff = now - timedelta(seconds=threshold_s)
                case_parts.append("(timeframe = %s AND MAX(as_of_ts) < %s)")
                case_params.extend([tf_name, cutoff])

            if case_parts:
                having_clause = " OR ".join(case_parts)
                cur.execute(f"""
                    SELECT symbol, timeframe,
                           MAX(bar_start_ts) AS latest_bar,
                           MAX(as_of_ts)     AS latest_ingestion
                    FROM ohlcv
                    GROUP BY symbol, timeframe
                    HAVING ({having_clause})
                    ORDER BY MAX(as_of_ts) ASC
                    LIMIT %s;
                """, (*case_params, limit))
                stale_rows = cur.fetchall()
            else:
                stale_rows = []

            # Universe size comparison
            cur.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv;")
            ohlcv_symbol_count = cur.fetchone()[0]

    # Redis universe counts
    try:
        known_count = r.scard("universe:known") or 0
        monitored_count = r.scard("universe:monitored") or 0
    except Exception:
        known_count = 0
        monitored_count = 0

    timeframes = []
    for row in tf_rows:
        tf, sym_count, latest_bar, earliest_bar, freshest, total = row
        threshold = staleness_thresholds.get(tf)
        lag_s = int((now - freshest).total_seconds()) if freshest else None
        timeframes.append({
            "timeframe": tf,
            "symbol_count": sym_count,
            "total_rows": total,
            "latest_bar": latest_bar.isoformat() if latest_bar else None,
            "earliest_bar": earliest_bar.isoformat() if earliest_bar else None,
            "freshest_ingestion": freshest.isoformat() if freshest else None,
            "lag_seconds": lag_s,
            "staleness_threshold_s": int(threshold) if threshold else None,
            "stale": lag_s > threshold if (lag_s is not None and threshold) else None,
        })

    stale = []
    for row in stale_rows:
        sym, tf, latest_bar, latest_ing = row
        stale.append({
            "symbol": sym,
            "timeframe": tf,
            "latest_bar": latest_bar.isoformat() if latest_bar else None,
            "latest_ingestion": latest_ing.isoformat() if latest_ing else None,
            "lag_seconds": int((now - latest_ing).total_seconds()) if latest_ing else None,
        })

    return envelope({
        "universe": {
            "known": known_count,
            "monitored": monitored_count,
            "with_ohlcv": ohlcv_symbol_count,
        },
        "timeframes": timeframes,
        "stale_symbols": stale,
        "stale_count": len(stale),
        "healthy": len(stale) == 0,
        "governance": {
            "staleness_multiplier": STALENESS_MULTIPLIER,
            "tf_seconds": {tf: TF_SECONDS.get(tf) for tf in governed_timeframes},
        },
    }, t0)


# =====================================================================
# STEP 3: Restart API
# =====================================================================
# sudo systemctl restart mcas-api

# =====================================================================
# STEP 4: Verify
# =====================================================================
# curl -s http://localhost:8002/v1/control/data/availability \
#   -H "X-API-Key: $(grep API_KEY .env | cut -d= -f2)" | python3 -m json.tool | head -40
#
# Check the governance block in the response — it should echo back the
# registry values, proving the endpoint is driven by the kernel:
# "governance": {
#   "staleness_multiplier": 2.5,
#   "tf_seconds": {"1m": 60, "5m": 300, "1h": 3600, ...}
# }
