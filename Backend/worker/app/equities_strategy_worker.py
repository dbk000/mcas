"""
Equities Strategy Worker
========================
Daily evaluation cycle for equities strategies (EQ_TSMOM, EQ_XSMOM, EQ_REV1A,
EQ_REV1B, EQ_VPD). Runs after US market close, computes features from Postgres
OHLCV data, evaluates all 5 strategies, writes signals to opportunities table
and Redis.

Usage:
    python equities_strategy_worker.py          # continuous loop
    python equities_strategy_worker.py --once   # single evaluation, then exit
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timezone, timedelta

import psycopg
import redis

# Docker import path handling (Corrections Log 2026-02-12)
if os.path.isdir('/app'):
    sys.path.insert(0, '/app')
elif os.path.isdir('/home/darren/Desktop/MCAS/Backend/worker/app'):
    sys.path.insert(0, '/home/darren/Desktop/MCAS/Backend/worker/app')

from equities_strategies import (
    ALL_STRATEGIES, EquitiesSignal,
    load_ohlcv_matrix, load_news_matrix, load_atc_matrix, engineer_features,
    check_data_freshness, check_split_guard,
)

logger = logging.getLogger("equities_strategy_worker")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
LOOKBACK_DAYS = 200  # Enough for 60d rolling features
REDIS_SIGNAL_TTL_S = 86400  # 24 hours for equities signals
MAX_FRESHNESS_RETRIES = 3
FRESHNESS_RETRY_DELAY_S = 1800  # 30 minutes


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Market close scheduling
# ---------------------------------------------------------------------------

def _next_evaluation_time() -> datetime:
    """Compute next evaluation time: 30 minutes after US market close.

    Regular close: 4:00 PM ET -> eval at 4:30 PM ET
    EDT (Mar-Nov): 4:30 PM ET = 20:30 UTC
    EST (Nov-Mar): 4:30 PM ET = 21:30 UTC

    Skips weekends (Sat/Sun).
    """
    now = datetime.now(timezone.utc)

    # US Eastern offset: -4 (EDT) or -5 (EST)
    # Simple DST check: second Sunday of March to first Sunday of November
    year = now.year
    # March: second Sunday
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    # November: first Sunday
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)

    is_edt = dst_start.day <= now.day or now.month > 3 if now.month == 3 else (
        now.month >= 4 and (now.month < 11 or (now.month == 11 and now.day < dst_end.day))
    )
    # Simpler: just check month range
    if now.month > 3 and now.month < 11:
        is_edt = True
    elif now.month == 3 and now.day >= dst_start.day:
        is_edt = True
    elif now.month == 11 and now.day < dst_end.day:
        is_edt = True
    else:
        is_edt = False

    et_offset = -4 if is_edt else -5
    eval_hour_utc = 16 + 0 - et_offset  # 4:30 PM ET in UTC hours
    eval_minute_utc = 30

    # Target: today at eval time, or tomorrow if already past
    target = now.replace(hour=eval_hour_utc, minute=eval_minute_utc, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    # Skip weekends (ET weekday check)
    et_now = target + timedelta(hours=et_offset)
    while et_now.weekday() >= 5:  # 5=Sat, 6=Sun
        target += timedelta(days=1)
        et_now = target + timedelta(hours=et_offset)

    return target


# ---------------------------------------------------------------------------
# Symbol universe
# ---------------------------------------------------------------------------

def _get_equities_symbols(conn) -> list[str]:
    """Get list of equities symbols from OHLCV data. SPY must be index 0."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT symbol FROM ohlcv
        WHERE source = 'equities_yahoo' AND timeframe = '1d'
          AND bar_start_ts > NOW() - INTERVAL '10 days'
        ORDER BY symbol
    """)
    symbols = [r[0] for r in cur.fetchall()]
    cur.close()

    # SPY must be at index 0 (feature engineering assumes this)
    if "SPY" in symbols:
        symbols.remove("SPY")
        symbols.insert(0, "SPY")
    else:
        logger.warning("SPY not found in equities OHLCV data")

    return symbols


# ---------------------------------------------------------------------------
# Signal persistence
# ---------------------------------------------------------------------------

def _write_signals_to_pg(conn, signals: list[EquitiesSignal]):
    """Batch write signals to opportunities table."""
    if not signals:
        return 0

    with conn.cursor() as cur:
        count = 0
        for sig in signals:
            payload = {
                "direction": sig.direction,
                "horizon_days": sig.horizon_days,
                "expected_return_bps": sig.expected_return_bps,
                "confidence": sig.confidence,
                "entry_price": sig.entry_price,
                "strategy_version": sig.strategy_id.replace("EQ_", "") + "_v1.0",
                "asset_class": "equities",
                "exploratory": True,
                "features": sig.features,
                "explain": sig.explain,
            }
            payload_json = json.dumps(payload)
            reason = sig.reason

            # Upsert: update if active record exists, else insert
            cur.execute("""
                UPDATE opportunities
                SET score = %s, reason = %s, payload_json = %s,
                    setup_version = %s, updated_ts = NOW()
                WHERE symbol = %s AND timeframe = '1d' AND setup_id = %s
                  AND status = 'active'
                RETURNING id;
            """, (sig.confidence, reason, payload_json,
                  sig.strategy_id + "_v1.0",
                  sig.symbol, sig.strategy_id))
            updated = cur.fetchone()

            if not updated:
                cur.execute("""
                    INSERT INTO opportunities
                        (symbol, timeframe, setup_id, decision, score, reason,
                         payload_json, status, setup_version)
                    VALUES (%s, '1d', %s, 'SIGNAL', %s, %s, %s, 'active', %s);
                """, (sig.symbol, sig.strategy_id, sig.confidence, reason,
                      payload_json, sig.strategy_id + "_v1.0"))
            count += 1

            # Batch commit every 50 signals
            if count % 50 == 0:
                conn.commit()

        conn.commit()
    return count


def _write_signals_to_redis(r, signals: list[EquitiesSignal]):
    """Cache signals in Redis with 24h TTL."""
    if not signals:
        return

    pipe = r.pipeline()
    for sig in signals:
        zset_key = f"signals:score:{sig.strategy_id}:1d"
        sig_key = f"sig:{sig.symbol}:1d:{sig.strategy_id}"
        meta_key = f"sig_meta:{sig.symbol}:1d:{sig.strategy_id}"

        payload = {
            "setup_id": sig.strategy_id,
            "symbol": sig.symbol,
            "timeframe": "1d",
            "direction": sig.direction,
            "setup_score": sig.confidence,
            "entry_price": sig.entry_price,
            "horizon_days": sig.horizon_days,
            "expected_return_bps": sig.expected_return_bps,
            "asset_class": "equities",
            "exploratory": True,
            "why_items": sig.explain,
            "features": sig.features,
            "as_of_ts": time.time(),
        }

        pipe.zadd(zset_key, {sig.symbol: sig.confidence})
        pipe.set(sig_key, json.dumps(payload), ex=REDIS_SIGNAL_TTL_S)
        pipe.hset(meta_key, mapping={
            "as_of_ts": str(time.time()),
            "bar_start_ts": str(time.time()),
        })
        pipe.expire(meta_key, REDIS_SIGNAL_TTL_S)
        pipe.expire(zset_key, REDIS_SIGNAL_TTL_S)

    pipe.execute()


def _expire_old_equities_signals(conn):
    """Expire equities opportunities past their horizon (1.5x to account for weekends)."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE opportunities
            SET status = 'expired'
            WHERE setup_id LIKE 'EQ_%%'
              AND status = 'active'
              AND created_ts < NOW() - (
                  COALESCE((payload_json::jsonb->>'horizon_days')::int, 20) * 1.5
              ) * INTERVAL '1 day';
        """)
        expired = cur.rowcount
    conn.commit()
    if expired > 0:
        logger.info("Expired %d old equities opportunities", expired)
    return expired


def _log_pipeline_event(conn, event_type: str, data: dict):
    """Log event to equities_pipeline_events if table exists."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO equities_pipeline_events
                    (actor, event_type, data)
                VALUES ('equities_strategy_worker', %s, %s)
            """, (event_type, json.dumps(data)))
        conn.commit()
    except Exception:
        # Table may not exist yet — silent fail
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate_once(conn, r) -> dict:
    """Run one full evaluation cycle. Returns summary dict."""
    t0 = time.time()

    # 1. Check data freshness
    is_fresh, msg = check_data_freshness(conn)
    if not is_fresh:
        logger.error("Data freshness check failed: %s", msg)
        return {"error": msg, "elapsed_s": time.time() - t0}
    logger.info("Data freshness: %s", msg)

    # 2. Get symbols
    symbols = _get_equities_symbols(conn)
    if len(symbols) < 5:
        msg = f"Too few symbols: {len(symbols)}"
        logger.error(msg)
        return {"error": msg, "elapsed_s": time.time() - t0}
    logger.info("Loaded %d equities symbols (SPY at idx 0: %s)", len(symbols), symbols[0])

    # 3. Load data
    ohlcv = load_ohlcv_matrix(conn, symbols, LOOKBACK_DAYS)
    news_mat = load_news_matrix(conn, symbols, ohlcv["timestamps"])
    atc_mat = load_atc_matrix(conn, symbols, ohlcv["timestamps"])
    T = ohlcv["close"].shape[0]
    logger.info("OHLCV matrix: %d bars x %d symbols", T, len(symbols))
    logger.info("ATC matrix loaded: %d non-zero slots", int((atc_mat[:, :, 0] > 0).sum()))

    # 4. Check for splits
    split_mask = check_split_guard(ohlcv["close"])
    if split_mask.any():
        n_splits = split_mask.sum()
        logger.warning("Split guard: %d bar-symbol pairs flagged, NaN-ing features", n_splits)
        for key in ohlcv:
            if key != "timestamps" and isinstance(ohlcv[key], type(split_mask)):
                ohlcv[key] = ohlcv[key].copy()
                ohlcv[key][split_mask] = float('nan')

    # 5. Engineer features
    features = engineer_features(ohlcv, news_mat, atc_mat=atc_mat)
    features["_timestamps"] = ohlcv["timestamps"]  # Date context for event-driven strategies
    logger.info("Engineered %d features", len(features))

    # 6. Evaluate all strategies at latest bar
    bar_idx = T - 1
    logger.info("Evaluating bar %d: date=%s", bar_idx, ohlcv["timestamps"][bar_idx])
    all_signals = []
    strategy_counts = {}

    strategies = [cls() for cls in ALL_STRATEGIES]
    for strat in strategies:
        try:
            sigs = strat.evaluate(features, bar_idx, symbols, ohlcv["close"])
            all_signals.extend(sigs)
            strategy_counts[strat.strategy_id] = len(sigs)
            logger.info("  %s: %d signals", strat.strategy_id, len(sigs))
        except Exception as e:
            logger.error("  %s evaluation failed: %s", strat.strategy_id, e, exc_info=True)
            strategy_counts[strat.strategy_id] = -1

    # 7. NaN check
    clean_signals = []
    nan_count = 0
    for sig in all_signals:
        if (sig.entry_price is not None and sig.entry_price > 0
                and sig.confidence is not None and 0 <= sig.confidence <= 1):
            clean_signals.append(sig)
        else:
            nan_count += 1
    if nan_count > 0:
        logger.warning("Dropped %d signals with NaN/invalid values", nan_count)

    # 8. Expire old signals
    _expire_old_equities_signals(conn)

    # 9. Write signals
    pg_count = _write_signals_to_pg(conn, clean_signals)
    logger.info("Wrote %d signals to Postgres", pg_count)

    if r:
        _write_signals_to_redis(r, clean_signals)
        logger.info("Cached %d signals in Redis (TTL=%ds)", len(clean_signals), REDIS_SIGNAL_TTL_S)

    elapsed = time.time() - t0
    summary = {
        "signals_total": len(clean_signals),
        "signals_dropped_nan": nan_count,
        "strategy_counts": strategy_counts,
        "symbols_count": len(symbols),
        "bars_count": T,
        "elapsed_s": round(elapsed, 2),
    }

    # 10. Log pipeline event
    _log_pipeline_event(conn, "daily_eval", summary)

    logger.info("Evaluation complete: %d signals in %.1fs", len(clean_signals), elapsed)
    return summary


# ---------------------------------------------------------------------------
# Zero-signal watchdog
# ---------------------------------------------------------------------------

class ZeroSignalTracker:
    """Track consecutive zero-signal days per strategy."""

    def __init__(self, max_consecutive: int = 3):
        self.max_consecutive = max_consecutive
        self._counts: dict[str, int] = {}

    def update(self, strategy_counts: dict[str, int]):
        for strat_id, count in strategy_counts.items():
            if count == 0:
                self._counts[strat_id] = self._counts.get(strat_id, 0) + 1
                if self._counts[strat_id] >= self.max_consecutive:
                    logger.warning(
                        "ALERT: %s produced 0 signals for %d consecutive evaluations",
                        strat_id, self._counts[strat_id],
                    )
            elif count > 0:
                self._counts[strat_id] = 0


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Equities Strategy Worker")
    parser.add_argument("--once", action="store_true", help="Evaluate once and exit")
    args = parser.parse_args()

    setup_logging()

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    # Redis connection (optional — signals still go to Postgres without it)
    r = None
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        logger.info("Redis connected: %s", REDIS_URL)
    except Exception as e:
        logger.warning("Redis unavailable, continuing without cache: %s", e)
        r = None

    if args.once:
        logger.info("Running single evaluation (--once mode)")
        with psycopg.connect(DATABASE_URL) as conn:
            result = evaluate_once(conn, r)
        logger.info("Result: %s", json.dumps(result, indent=2))
        return

    # Continuous loop
    logger.info("Equities strategy worker starting (continuous mode)")
    watchdog = ZeroSignalTracker(max_consecutive=3)

    while True:
        try:
            next_eval = _next_evaluation_time()
            wait_s = (next_eval - datetime.now(timezone.utc)).total_seconds()

            if wait_s > 0:
                logger.info("Next evaluation at %s (in %.0f min)",
                            next_eval.isoformat(), wait_s / 60)
                # Sleep in 60s chunks for responsiveness
                while wait_s > 0:
                    time.sleep(min(wait_s, 60))
                    wait_s = (next_eval - datetime.now(timezone.utc)).total_seconds()

            # Freshness retry loop
            for attempt in range(MAX_FRESHNESS_RETRIES):
                with psycopg.connect(DATABASE_URL) as conn:
                    is_fresh, msg = check_data_freshness(conn)
                if is_fresh:
                    break
                logger.warning("Data not fresh (attempt %d/%d): %s. Retrying in %ds...",
                               attempt + 1, MAX_FRESHNESS_RETRIES, msg, FRESHNESS_RETRY_DELAY_S)
                time.sleep(FRESHNESS_RETRY_DELAY_S)
            else:
                logger.error("Data still not fresh after %d retries, skipping this cycle",
                             MAX_FRESHNESS_RETRIES)
                continue

            # Evaluate
            with psycopg.connect(DATABASE_URL) as conn:
                result = evaluate_once(conn, r)

            if "strategy_counts" in result:
                watchdog.update(result["strategy_counts"])

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.error("Evaluation cycle failed: %s", e, exc_info=True)
            time.sleep(300)  # Wait 5 min before retry


if __name__ == "__main__":
    main()
