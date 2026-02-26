#!/usr/bin/env python3
"""Orchestrate end-to-end equities forward predictor pipeline.

Time-aware scheduling: each stage has a UTC window + minimum interval.
On cold start, all stages run once immediately.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from equities_events_collector import collect as collect_events
from equities_feature_builder import build_features
from equities_ohlcv_ingestor import ingest as ingest_ohlcv
from equities_calibration_watchdog import run_watchdog
from equities_alert_bridge import run_bridge
from equities_outcome_settler import run_settlement
from equities_policy_optimizer import run_optimizer
from equities_forward_predictor import run_predictor
from equities_predictor.db import connect, log_pipeline_event
from equities_predictor.universe import get_default_symbols

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PIPELINE_INTERVAL_SECONDS = 600
LAST_RUN_BY_STAGE: dict[str, datetime] = {}
COLD_START_ACTIVE = True

_WEEKDAYS = {0, 1, 2, 3, 4}
_MIN_INTERVAL_BY_STAGE = {
    "ingest_ohlcv_daily": timedelta(hours=20),
    "run_settlement": timedelta(hours=20),
    "run_optimizer": timedelta(hours=20),
    "run_watchdog": timedelta(days=6),
}
_WINDOW_RULES = {
    "ingest_ohlcv_daily": {
        "kind": "weekday_window",
        "start": (3, 0),
        "end": (3, 30),
        "next": "03:00 UTC Mon-Fri",
    },
    "collect_events": {
        "kind": "weekday_window",
        "start": (10, 0),
        "end": (23, 30),
        "next": "10:00 UTC Mon-Fri",
    },
    "build_features": {"kind": "always"},
    "run_predictor": {"kind": "always"},
    "run_settlement": {
        "kind": "weekday_window",
        "start": (22, 0),
        "end": (22, 30),
        "next": "22:00 UTC Mon-Fri",
    },
    "run_optimizer": {
        "kind": "weekday_window",
        "start": (22, 30),
        "end": (23, 0),
        "next": "22:30 UTC Mon-Fri",
    },
    "run_watchdog": {
        "kind": "weekly_window",
        "weekday": 6,  # Sunday
        "start": (6, 0),
        "end": (6, 30),
        "next": "Sunday 06:00 UTC",
    },
    "run_bridge": {"kind": "always"},
}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _in_hhmm_window(now_utc: datetime, start_hhmm: tuple[int, int], end_hhmm: tuple[int, int]) -> bool:
    now_minutes = now_utc.hour * 60 + now_utc.minute
    start_minutes = start_hhmm[0] * 60 + start_hhmm[1]
    end_minutes = end_hhmm[0] * 60 + end_hhmm[1]
    return start_minutes <= now_minutes < end_minutes


def _window_allowed(stage_name: str, now_utc: datetime) -> tuple[bool, str]:
    rule = _WINDOW_RULES.get(stage_name, {"kind": "always"})
    kind = rule["kind"]

    if kind == "always":
        return True, ""

    start_hhmm = rule["start"]
    end_hhmm = rule["end"]
    next_hint = rule["next"]

    if kind == "weekday_window":
        if now_utc.weekday() not in _WEEKDAYS:
            return False, f"outside window (next: {next_hint})"
        if not _in_hhmm_window(now_utc, start_hhmm, end_hhmm):
            return False, f"outside window (next: {next_hint})"
        return True, ""

    if kind == "weekly_window":
        if now_utc.weekday() != rule["weekday"]:
            return False, f"outside window (next: {next_hint})"
        if not _in_hhmm_window(now_utc, start_hhmm, end_hhmm):
            return False, f"outside window (next: {next_hint})"
        return True, ""

    return True, ""


def _min_interval(stage_name: str) -> timedelta:
    return _MIN_INTERVAL_BY_STAGE.get(stage_name, timedelta(seconds=max(1, PIPELINE_INTERVAL_SECONDS)))


def should_run_stage(stage_name: str, now_utc: datetime) -> tuple[bool, str]:
    """Return whether a stage should run at *now_utc* based on windows + min interval."""
    if COLD_START_ACTIVE and stage_name not in LAST_RUN_BY_STAGE:
        return True, "cold-start run"

    in_window, window_reason = _window_allowed(stage_name, now_utc)
    if not in_window:
        return False, window_reason

    last_run = LAST_RUN_BY_STAGE.get(stage_name)
    min_iv = _min_interval(stage_name)
    if last_run is not None:
        elapsed = now_utc - last_run
        if elapsed < min_iv:
            remaining_s = int((min_iv - elapsed).total_seconds())
            return False, f"minimum interval not reached ({remaining_s}s remaining)"

    return True, "ready"


def _run_stage(stage_name: str, fn: Callable[[], Any]) -> Any:
    now_utc = datetime.now(timezone.utc)
    should_run, reason = should_run_stage(stage_name, now_utc)
    if not should_run:
        logger.info("Skipping %s: %s", stage_name, reason)
        return {"skipped": True, "reason": reason}

    logger.info("Running %s", stage_name)
    t0 = time.monotonic()
    try:
        out = fn()
    except Exception:
        elapsed = time.monotonic() - t0
        logger.exception("FAILED %s after %.1fs", stage_name, elapsed)
        return {"failed": True, "stage": stage_name}
    elapsed = time.monotonic() - t0
    LAST_RUN_BY_STAGE[stage_name] = datetime.now(timezone.utc)
    logger.info("Completed %s in %.1fs", stage_name, elapsed)
    return out


def run_cycle(
    *,
    database_url: str | None,
    actor: str,
    symbols: list[str],
    lookback_days: int,
    model_version: str,
    min_confidence: float,
) -> dict:
    ohlcv_summary = _run_stage(
        "ingest_ohlcv_daily",
        lambda: ingest_ohlcv(
            database_url=database_url,
            symbols=symbols,
            timeframes=["1h", "1d"],
            lookback_days=lookback_days,
            shard=None,
            shards=None,
            batch_id=None,
            actor=actor,
        ),
    )

    event_summary = _run_stage(
        "collect_events",
        lambda: collect_events(
            database_url=database_url,
            actor=actor,
            symbols=symbols,
            max_sec_items=15,
            max_news_items=20,
        ),
    )

    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=lookback_days)
    features = _run_stage(
        "build_features",
        lambda: {
            "1h": build_features(
                database_url=database_url,
                actor=actor,
                symbols=symbols,
                timeframe="1h",
                start_ts=start_ts,
                end_ts=end_ts,
            ),
            "1d": build_features(
                database_url=database_url,
                actor=actor,
                symbols=symbols,
                timeframe="1d",
                start_ts=start_ts,
                end_ts=end_ts,
            ),
        },
    )

    predictions_1d = _run_stage(
        "run_predictor",
        lambda: run_predictor(
            database_url=database_url,
            actor=actor,
            symbols=symbols,
            timeframe="1d",
            lookback_days=lookback_days,
            model_version=f"{model_version}_1d",
            min_confidence=min_confidence,
        ),
    )
    predictions_1h = _run_stage(
        "run_predictor_1h",
        lambda: run_predictor(
            database_url=database_url,
            actor=actor,
            symbols=symbols,
            timeframe="1h",
            lookback_days=lookback_days,
            model_version=f"{model_version}_1h",
            min_confidence=min_confidence,
        ),
    )
    predictions = {"1d": predictions_1d, "1h": predictions_1h}

    outcomes = _run_stage(
        "run_settlement",
        lambda: run_settlement(
            database_url=database_url,
            actor=actor,
            limit=4000,
        ),
    )
    policies = _run_stage(
        "run_optimizer",
        lambda: run_optimizer(
            database_url=database_url,
            actor=actor,
            lookback_days=120,
            min_samples=20,
            transaction_cost_bps=6.0,
        ),
    )
    calibration = _run_stage(
        "run_watchdog",
        lambda: run_watchdog(
            database_url=database_url,
            actor=actor,
            lookback_days=120,
            min_bucket_samples=12,
            return_tolerance=0.0010,
            hit_tolerance=0.02,
        ),
    )
    alert_bridge = _run_stage(
        "run_bridge",
        lambda: run_bridge(
            database_url=database_url,
            actor=actor,
            lookback_days=30,
            max_tasks=30,
        ),
    )

    summary = {
        "ohlcv": ohlcv_summary,
        "events": event_summary,
        "features": features,
        "predictions": predictions,
        "outcomes": outcomes,
        "policies": policies,
        "calibration": calibration,
        "alert_bridge": alert_bridge,
    }

    conn = connect(database_url)
    try:
        log_pipeline_event(
            conn,
            actor=actor,
            event_type="PIPELINE_CYCLE_COMPLETE",
            message="Equities pipeline full cycle completed",
            data=summary,
        )
        conn.commit()
    finally:
        conn.close()

    return summary


def parse_args() -> argparse.Namespace:
    symbols_env = (os.getenv("EQUITIES_SYMBOLS") or "").strip()
    symbols_default = symbols_env or ",".join(get_default_symbols())

    parser = argparse.ArgumentParser(description="Run equities pipeline cycle")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--symbols", default=symbols_default)
    parser.add_argument("--lookback-days", type=int, default=_env_int("EQUITIES_OHLCV_LOOKBACK_DAYS", 180))
    parser.add_argument("--model-version", default=os.getenv("EQUITIES_MODEL_VERSION", "v1_freeopen"))
    parser.add_argument("--min-confidence", type=float, default=_env_float("EQUITIES_MIN_CONFIDENCE", 0.10))
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=_env_int("EQUITIES_PIPELINE_INTERVAL_S", 600))
    return parser.parse_args()


def main() -> int:
    global COLD_START_ACTIVE, PIPELINE_INTERVAL_SECONDS

    args = parse_args()
    PIPELINE_INTERVAL_SECONDS = max(1, int(args.interval_seconds))
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    logger.info("Equities pipeline starting | %d symbols | interval %ds | min_confidence %.2f",
                len(symbols), PIPELINE_INTERVAL_SECONDS, args.min_confidence)

    while True:
        t0 = time.monotonic()
        summary = run_cycle(
            database_url=args.database_url,
            actor=args.actor,
            symbols=symbols,
            lookback_days=max(30, int(args.lookback_days)),
            model_version=args.model_version,
            min_confidence=max(0.0, float(args.min_confidence)),
        )
        elapsed = time.monotonic() - t0
        logger.info("Pipeline cycle complete in %.1fs", elapsed)
        COLD_START_ACTIVE = False
        if not args.loop:
            break
        logger.info("Sleeping %ds until next cycle", PIPELINE_INTERVAL_SECONDS)
        time.sleep(PIPELINE_INTERVAL_SECONDS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
