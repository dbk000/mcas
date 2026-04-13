"""Regime detector — composite-trigger poller for the Phase A enforcer.

Polls every 5 minutes:
    1. Fetch BTCUSDT 4h closes from the ``ohlcv`` table (last 2 bars).
    2. Compute the 4h return.
    3. If drawdown <= -3%: scan keyword text for the last 24h vs the
       trailing 168h baseline and compute ``velocity_ratio`` per category
       (GEOPOLITICAL / MONETARY / REGULATORY / STRUCTURAL).
    4. If ANY category velocity_ratio >= ``SHOCK_CATEGORY_VELOCITY_THRESHOLD``
       (default 3.0): call
       ``enforcer.set_state(SHOCK, reason=…, source='composite_trigger')``.

Logs every poll, even when nothing fires (debugging).

CLI
---
    python3 -m governance.regime_detector
        Run the live poller (calls ``enforcer.set_state`` when the trigger fires).

    python3 -m governance.regime_detector --dry-run
        Print what would fire without writing to Redis or audit table.

Notes
-----
- This is a **scaffold** (Phase A authoring): it lays out the polling
  loop, the configuration knobs, the dry-run path, and the shutdown
  signal handler. The DB query helpers (``_fetch_btc_4h_closes``,
  ``_fetch_recent_text_window``) are written to the existing schema's
  expected shape (``ohlcv(symbol, timeframe, bar_start_ts, close_price)``,
  ``social_posts(post_ts, post_text)``) but are **not exercised** in
  Phase A — they will run on first deploy and any schema mismatch surfaces
  at that point. Per the task brief, no DB connection is opened during
  scaffolding.
- The detector relies on the canonical
  ``governance.regime.keyword_clusters.velocity_ratio`` for ratio math
  to ensure reproducibility with ``validate_regime_thesis.py``.
- Connection management uses ``governance.persistence.get_conn()`` for
  Postgres and ``redis.from_url(REDIS_URL)`` for Redis — the same
  pattern used by other Backend/governance workers.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from typing import Any, Iterable, Optional

logger = logging.getLogger("regime_detector")


# ---------------------------------------------------------------------------
# Configuration knobs (env-driven; all have safe defaults).
# ---------------------------------------------------------------------------

#: Poll interval in seconds (default 300 = 5 minutes).
POLL_INTERVAL_SECONDS = int(os.getenv("REGIME_DETECTOR_POLL_SECONDS", "300"))

#: BTC 4h drawdown that *enables* the velocity check. Plan §A specifies
#: -3% (drawdown <= -3%). Negative number; e.g. -0.03.
BTC_DRAWDOWN_THRESHOLD = float(
    os.getenv("REGIME_DETECTOR_BTC_DRAWDOWN", "-0.03")
)

#: Categories scanned for velocity (per Phase A spec).
SHOCK_CATEGORIES: tuple[str, ...] = (
    "GEOPOLITICAL",
    "MONETARY",
    "REGULATORY",
    "STRUCTURAL",
)

#: ANY category exceeding this velocity_ratio fires SHOCK (default 3.0×).
SHOCK_CATEGORY_VELOCITY_THRESHOLD = float(
    os.getenv("REGIME_DETECTOR_VELOCITY_THRESHOLD", "3.0")
)

#: Lookback / baseline windows for velocity_ratio (hours).
VELOCITY_LOOKBACK_HOURS = int(os.getenv("REGIME_DETECTOR_LOOKBACK_H", "24"))
VELOCITY_BASELINE_HOURS = int(os.getenv("REGIME_DETECTOR_BASELINE_H", "168"))

#: Path to the beta table consumed by the enforcer.
DEFAULT_BETA_TABLE_PATH = os.getenv(
    "ENFORCER_BETA_TABLE",
    "/backend/governance/regime/asset_beta_table_binance_v1.json",
)

#: Redis URL (matches the rest of the worker stack).
DEFAULT_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


# ---------------------------------------------------------------------------
# DB helpers (production-deploy time — Phase A scaffolding does not exercise).
# ---------------------------------------------------------------------------

def _fetch_btc_4h_closes(pg_conn: Any, n_bars: int = 2) -> list[float]:
    """Return the last ``n_bars`` 4h BTCUSDT close prices (oldest -> newest).

    Empty list if the rows are missing (e.g. ohlcv backfill stalled).
    """
    sql = (
        "SELECT close FROM ohlcv "
        "WHERE symbol = %s AND timeframe = %s "
        "ORDER BY bar_start_ts DESC LIMIT %s"
    )
    closes: list[float] = []
    with pg_conn.cursor() as cur:
        cur.execute(sql, ("BTCUSDT", "4h", n_bars))
        rows = cur.fetchall()
    for row in reversed(rows):
        closes.append(float(row[0]))
    return closes


def _btc_4h_return(closes: list[float]) -> Optional[float]:
    """Compute simple return from oldest -> newest close. None if insufficient."""
    if len(closes) < 2 or closes[0] <= 0:
        return None
    return (closes[-1] / closes[0]) - 1.0


def _fetch_recent_text_window(
    pg_conn: Any,
    now_ts: int,
    baseline_hours: int,
    lookback_hours: int,
) -> list[tuple[int, str]]:
    """Return ``[(post_ts_seconds, post_text), ...]`` for velocity scoring.

    Pulls a window covering both the lookback and baseline horizons so
    velocity_ratio() can compute both numerator and denominator from one
    fetch. Rows are not pre-filtered by category — keyword_clusters does
    that match.
    """
    total_seconds = (baseline_hours + lookback_hours) * 3600
    start_ts = now_ts - total_seconds
    sql = (
        "SELECT EXTRACT(EPOCH FROM post_ts)::BIGINT, content "
        "FROM social_posts "
        "WHERE post_ts >= to_timestamp(%s) AND content IS NOT NULL"
    )
    out: list[tuple[int, str]] = []
    with pg_conn.cursor() as cur:
        cur.execute(sql, (start_ts,))
        for ts_int, text in cur.fetchall():
            out.append((int(ts_int), str(text)))
    return out


# ---------------------------------------------------------------------------
# Composite trigger evaluation
# ---------------------------------------------------------------------------

def evaluate_composite_trigger(
    btc_return_4h: Optional[float],
    texts: Iterable[tuple[int, str]],
    now_ts: int,
    drawdown_threshold: float = BTC_DRAWDOWN_THRESHOLD,
    velocity_threshold: float = SHOCK_CATEGORY_VELOCITY_THRESHOLD,
    lookback_hours: int = VELOCITY_LOOKBACK_HOURS,
    baseline_hours: int = VELOCITY_BASELINE_HOURS,
    categories: tuple[str, ...] = SHOCK_CATEGORIES,
) -> dict:
    """Return a structured trigger evaluation for logging + decision.

    Output keys:
        ``btc_return_4h``      — float or None
        ``btc_breached``       — bool (drawdown <= threshold)
        ``velocity_by_cat``    — {category: ratio} (only computed if
                                 btc_breached; otherwise empty dict)
        ``firing_categories``  — list of categories that breached velocity
        ``should_fire``        — bool (btc_breached AND any firing cat)
        ``reason``             — human-readable string suitable for
                                 enforcer.set_state(reason=...)
    """
    # Lazy import to avoid circular references at module load time.
    from governance.regime.keyword_clusters import velocity_ratio

    # Cast to a list so we can pass through multiple velocity_ratio calls.
    text_list = list(texts) if not isinstance(texts, list) else texts

    btc_breached = (
        btc_return_4h is not None and btc_return_4h <= drawdown_threshold
    )

    velocity_by_cat: dict[str, float] = {}
    firing: list[str] = []

    if btc_breached:
        for cat in categories:
            ratio = velocity_ratio(
                category=cat,
                texts=text_list,
                now_ts=now_ts,
                lookback_hours=lookback_hours,
                baseline_hours=baseline_hours,
            )
            velocity_by_cat[cat] = ratio
            if ratio >= velocity_threshold:
                firing.append(cat)

    should_fire = bool(btc_breached and firing)

    if should_fire:
        # Use the strongest firing category in the reason string.
        top_cat = max(firing, key=lambda c: velocity_by_cat.get(c, 0.0))
        top_ratio = velocity_by_cat[top_cat]
        reason = (
            f"composite_trigger: BTC {btc_return_4h * 100:.2f}% 4h, "
            f"{top_cat} velocity {top_ratio:.2f}x"
        )
    elif btc_breached:
        reason = (
            f"composite_trigger: BTC {btc_return_4h * 100:.2f}% 4h "
            f"breached but no category velocity >= {velocity_threshold:.2f}x"
        )
    elif btc_return_4h is None:
        reason = "composite_trigger: BTC 4h return unavailable (insufficient ohlcv)"
    else:
        reason = (
            f"composite_trigger: BTC {btc_return_4h * 100:.2f}% 4h "
            f"within tolerance ({drawdown_threshold * 100:.2f}%)"
        )

    return {
        "btc_return_4h": btc_return_4h,
        "btc_breached": btc_breached,
        "velocity_by_cat": velocity_by_cat,
        "firing_categories": firing,
        "should_fire": should_fire,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

class _ShutdownFlag:
    """Coalesced SIGINT/SIGTERM flag. ``stop()`` is safe to call from a
    signal handler — it only mutates a bool."""

    def __init__(self) -> None:
        self.stop_requested = False

    def stop(self, *_: Any) -> None:
        self.stop_requested = True
        logger.info("regime_detector shutdown requested")


async def _poll_once(
    enforcer: Any,
    pg_get_conn: Any,
    dry_run: bool,
) -> dict:
    """Run one composite-trigger evaluation. Returns the eval dict."""
    now_ts = int(time.time())

    # Fetch BTC 4h closes + recent text in one connection (avoid
    # holding two concurrent pool slots).
    closes: list[float] = []
    texts: list[tuple[int, str]] = []
    try:
        with pg_get_conn() as conn:
            closes = _fetch_btc_4h_closes(conn, n_bars=2)
            texts = _fetch_recent_text_window(
                pg_conn=conn,
                now_ts=now_ts,
                baseline_hours=VELOCITY_BASELINE_HOURS,
                lookback_hours=VELOCITY_LOOKBACK_HOURS,
            )
    except Exception as exc:  # pragma: no cover — runtime only
        logger.exception("poll DB fetch failed: %s", exc)
        return {
            "should_fire": False,
            "reason": f"DB fetch failed: {exc}",
            "btc_return_4h": None,
            "velocity_by_cat": {},
            "firing_categories": [],
        }

    btc_ret = _btc_4h_return(closes)
    eval_result = evaluate_composite_trigger(
        btc_return_4h=btc_ret,
        texts=texts,
        now_ts=now_ts,
    )

    logger.info(
        "poll btc_4h=%s breached=%s firing=%s reason=%r",
        f"{btc_ret * 100:.2f}%" if btc_ret is not None else "n/a",
        eval_result["btc_breached"],
        eval_result["firing_categories"],
        eval_result["reason"],
    )

    if eval_result["should_fire"]:
        # Lazy enum import.
        from governance.regime.enforcer import RegimeState

        if dry_run:
            logger.warning(
                "[DRY-RUN] would set_state(SHOCK) — reason=%s",
                eval_result["reason"],
            )
        else:
            try:
                with pg_get_conn() as conn:
                    enforcer.set_state(
                        state=RegimeState.SHOCK,
                        reason=eval_result["reason"],
                        source="composite_trigger",
                        pg_conn=conn,
                    )
            except Exception as exc:  # pragma: no cover — runtime only
                logger.exception("set_state(SHOCK) failed: %s", exc)

    return eval_result


async def run(
    enforcer: Any,
    pg_get_conn: Any,
    dry_run: bool = False,
    poll_interval_seconds: int = POLL_INTERVAL_SECONDS,
) -> None:
    """Run the poll loop until SIGINT/SIGTERM is received."""
    flag = _ShutdownFlag()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, flag.stop)
        except NotImplementedError:  # pragma: no cover — non-unix
            signal.signal(sig, flag.stop)

    logger.info(
        "regime_detector started — interval=%ds dry_run=%s",
        poll_interval_seconds,
        dry_run,
    )

    while not flag.stop_requested:
        try:
            await _poll_once(enforcer=enforcer, pg_get_conn=pg_get_conn,
                             dry_run=dry_run)
        except Exception as exc:  # pragma: no cover — runtime
            logger.exception("poll cycle failed (continuing): %s", exc)

        # Sleep in 1-second slices so shutdown is responsive.
        for _ in range(poll_interval_seconds):
            if flag.stop_requested:
                break
            await asyncio.sleep(1)

    logger.info("regime_detector shutting down cleanly")


def _build_enforcer_and_conn():  # pragma: no cover — runtime wiring
    """Build the production enforcer + pg connection-getter.

    Imported lazily so unit tests can drive ``run()`` with mocks without
    pulling in real Redis / psycopg.
    """
    import redis as redis_lib

    from governance.persistence import get_conn
    from governance.regime.enforcer import Enforcer

    redis_client = redis_lib.from_url(DEFAULT_REDIS_URL)
    enforcer = Enforcer(
        redis_client=redis_client,
        beta_table_path=DEFAULT_BETA_TABLE_PATH,
    )
    return enforcer, get_conn


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="regime_detector",
        description="Composite-trigger poller for the regime enforcer.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would fire without writing to Redis or enforcer_events.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=POLL_INTERVAL_SECONDS,
        help=(
            "Poll interval in seconds (default: env REGIME_DETECTOR_POLL_SECONDS "
            f"or {POLL_INTERVAL_SECONDS})."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level (default INFO; env LOG_LEVEL).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Bootstrap TypedConfigRegistry + connection pools BEFORE any
    # governance/persistence or enforcer access. Mirrors worker.py pattern.
    from governance.bootstrap import bootstrap_genesis
    bootstrap_genesis()
    enforcer, pg_get_conn = _build_enforcer_and_conn()
    asyncio.run(
        run(
            enforcer=enforcer,
            pg_get_conn=pg_get_conn,
            dry_run=args.dry_run,
            poll_interval_seconds=args.poll_seconds,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
