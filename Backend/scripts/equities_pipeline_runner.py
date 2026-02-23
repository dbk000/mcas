#!/usr/bin/env python3
"""Orchestrate end-to-end equities forward predictor pipeline."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

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


def run_cycle(
    *,
    database_url: str | None,
    actor: str,
    symbols: list[str],
    lookback_days: int,
    model_version: str,
) -> dict:
    ohlcv_summary = ingest_ohlcv(
        database_url=database_url,
        symbols=symbols,
        timeframes=["1h", "1d"],
        lookback_days=lookback_days,
        shard=None,
        shards=None,
        batch_id=None,
        actor=actor,
    )

    event_summary = collect_events(
        database_url=database_url,
        actor=actor,
        symbols=symbols,
        max_sec_items=15,
        max_news_items=8,
    )

    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=lookback_days)
    feat_1h = build_features(
        database_url=database_url,
        actor=actor,
        symbols=symbols,
        timeframe="1h",
        start_ts=start_ts,
        end_ts=end_ts,
    )
    feat_1d = build_features(
        database_url=database_url,
        actor=actor,
        symbols=symbols,
        timeframe="1d",
        start_ts=start_ts,
        end_ts=end_ts,
    )

    pred_1h = run_predictor(
        database_url=database_url,
        actor=actor,
        symbols=symbols,
        timeframe="1h",
        lookback_days=lookback_days,
        model_version=f"{model_version}_1h",
        min_confidence=0.10,
    )
    pred_1d = run_predictor(
        database_url=database_url,
        actor=actor,
        symbols=symbols,
        timeframe="1d",
        lookback_days=lookback_days,
        model_version=f"{model_version}_1d",
        min_confidence=0.10,
    )
    outcomes = run_settlement(
        database_url=database_url,
        actor=actor,
        limit=4000,
    )
    policies = run_optimizer(
        database_url=database_url,
        actor=actor,
        lookback_days=120,
        min_samples=20,
        transaction_cost_bps=6.0,
    )
    calibration = run_watchdog(
        database_url=database_url,
        actor=actor,
        lookback_days=120,
        min_bucket_samples=12,
        return_tolerance=0.0010,
        hit_tolerance=0.02,
    )
    alert_bridge = run_bridge(
        database_url=database_url,
        actor=actor,
        lookback_days=30,
        max_tasks=30,
    )

    summary = {
        "ohlcv": ohlcv_summary,
        "events": event_summary,
        "features": {"1h": feat_1h, "1d": feat_1d},
        "predictions": {"1h": pred_1h, "1d": pred_1d},
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
    parser = argparse.ArgumentParser(description="Run equities pipeline cycle")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--symbols", default=",".join(get_default_symbols()))
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--model-version", default="v1_freeopen")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    while True:
        summary = run_cycle(
            database_url=args.database_url,
            actor=args.actor,
            symbols=symbols,
            lookback_days=max(30, int(args.lookback_days)),
            model_version=args.model_version,
        )
        print(summary)
        if not args.loop:
            break
        time.sleep(max(30, int(args.interval_seconds)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
