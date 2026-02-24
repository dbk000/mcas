#!/usr/bin/env python3
"""Report equities pipeline freshness and error state as JSON."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from equities_predictor.db import connect

EQUITIES_SOURCES = ("equities_yahoo", "equities_stooq")
STALE_THRESHOLDS_SECONDS = {
    "ohlcv_1h": 2 * 60 * 60,
    "ohlcv_1d": 26 * 60 * 60,
    "events": 2 * 60 * 60,
    "feature_events": 2 * 60 * 60,
    "predictions": 2 * 60 * 60,
    "pipeline_events": 2 * 60 * 60,
}


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def _age_seconds(now_utc: datetime, ts: datetime | None) -> int | None:
    if ts is None:
        return None
    return max(0, int((now_utc - ts).total_seconds()))


def _freshness_component(
    *,
    now_utc: datetime,
    latest_ts: datetime | None,
    threshold_seconds: int,
    extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    age_seconds = _age_seconds(now_utc, latest_ts)
    is_stale = latest_ts is None or (age_seconds is not None and age_seconds > threshold_seconds)
    payload: dict[str, Any] = {
        "latest_ts": _iso(latest_ts),
        "age_seconds": age_seconds,
        "threshold_seconds": threshold_seconds,
        "is_stale": bool(is_stale),
    }
    if extra:
        payload.update(extra)
    return payload, bool(is_stale)


def _compute_health_payload(conn) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                timeframe,
                MAX(bar_start_ts) AS latest_bar_start_ts,
                COUNT(*) FILTER (WHERE bar_start_ts >= NOW() - INTERVAL '24 hours')::bigint AS rows_24h
            FROM ohlcv
            WHERE timeframe = ANY(%s::text[])
              AND is_final = TRUE
              AND source = ANY(%s::text[])
            GROUP BY timeframe;
            """,
            (["1h", "1d"], list(EQUITIES_SOURCES)),
        )
        ohlcv_rows = {str(r["timeframe"]): r for r in cur.fetchall()}

        cur.execute(
            """
            SELECT
                MAX(published_ts) AS latest_published_ts,
                COUNT(*) FILTER (WHERE published_ts >= NOW() - INTERVAL '24 hours')::bigint AS rows_24h
            FROM equities_events;
            """
        )
        events_row = cur.fetchone() or {}

        cur.execute(
            """
            SELECT
                MAX(created_at) AS latest_created_at,
                COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours')::bigint AS rows_24h
            FROM equities_feature_events;
            """
        )
        feature_row = cur.fetchone() or {}

        cur.execute(
            """
            SELECT
                MAX(created_at) AS latest_created_at,
                COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours')::bigint AS rows_24h
            FROM equities_predictions;
            """
        )
        prediction_row = cur.fetchone() or {}

        cur.execute(
            """
            SELECT
                MAX(ts) AS latest_ts,
                COUNT(*) FILTER (WHERE ts >= NOW() - INTERVAL '24 hours')::bigint AS events_24h,
                COUNT(*) FILTER (
                    WHERE ts >= NOW() - INTERVAL '24 hours'
                      AND event_type ILIKE '%%ERROR%%'
                )::bigint AS errors_24h,
                COUNT(*) FILTER (
                    WHERE ts >= NOW() - INTERVAL '1 hour'
                      AND event_type ILIKE '%%ERROR%%'
                )::bigint AS errors_1h
            FROM equities_pipeline_events;
            """
        )
        pipeline_row = cur.fetchone() or {}

        cur.execute(
            """
            SELECT
                COUNT(*)::bigint AS total,
                COUNT(*) FILTER (WHERE status = 'active')::bigint AS active,
                COUNT(*) FILTER (WHERE status = 'settled')::bigint AS settled
            FROM equities_opportunities;
            """
        )
        opportunities_row = cur.fetchone() or {}

        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE severity = 'HIGH'
                      AND created_at >= NOW() - INTERVAL '7 days'
                )::bigint AS high_7d,
                COUNT(*) FILTER (
                    WHERE severity = 'MEDIUM'
                      AND created_at >= NOW() - INTERVAL '7 days'
                )::bigint AS medium_7d
            FROM equities_calibration_alerts;
            """
        )
        calibration_row = cur.fetchone() or {}

    stale_components: list[str] = []

    ohlcv_1h_component, ohlcv_1h_stale = _freshness_component(
        now_utc=now_utc,
        latest_ts=ohlcv_rows.get("1h", {}).get("latest_bar_start_ts"),
        threshold_seconds=STALE_THRESHOLDS_SECONDS["ohlcv_1h"],
        extra={"rows_24h": int(ohlcv_rows.get("1h", {}).get("rows_24h") or 0)},
    )
    if ohlcv_1h_stale:
        stale_components.append("ohlcv.1h")

    ohlcv_1d_component, ohlcv_1d_stale = _freshness_component(
        now_utc=now_utc,
        latest_ts=ohlcv_rows.get("1d", {}).get("latest_bar_start_ts"),
        threshold_seconds=STALE_THRESHOLDS_SECONDS["ohlcv_1d"],
        extra={"rows_24h": int(ohlcv_rows.get("1d", {}).get("rows_24h") or 0)},
    )
    if ohlcv_1d_stale:
        stale_components.append("ohlcv.1d")

    events_component, events_stale = _freshness_component(
        now_utc=now_utc,
        latest_ts=events_row.get("latest_published_ts"),
        threshold_seconds=STALE_THRESHOLDS_SECONDS["events"],
        extra={"rows_24h": int(events_row.get("rows_24h") or 0)},
    )
    if events_stale:
        stale_components.append("events")

    feature_component, feature_stale = _freshness_component(
        now_utc=now_utc,
        latest_ts=feature_row.get("latest_created_at"),
        threshold_seconds=STALE_THRESHOLDS_SECONDS["feature_events"],
        extra={"rows_24h": int(feature_row.get("rows_24h") or 0)},
    )
    if feature_stale:
        stale_components.append("feature_events")

    prediction_component, prediction_stale = _freshness_component(
        now_utc=now_utc,
        latest_ts=prediction_row.get("latest_created_at"),
        threshold_seconds=STALE_THRESHOLDS_SECONDS["predictions"],
        extra={"rows_24h": int(prediction_row.get("rows_24h") or 0)},
    )
    if prediction_stale:
        stale_components.append("predictions")

    pipeline_component, pipeline_stale = _freshness_component(
        now_utc=now_utc,
        latest_ts=pipeline_row.get("latest_ts"),
        threshold_seconds=STALE_THRESHOLDS_SECONDS["pipeline_events"],
        extra={
            "events_24h": int(pipeline_row.get("events_24h") or 0),
            "errors_24h": int(pipeline_row.get("errors_24h") or 0),
            "errors_1h": int(pipeline_row.get("errors_1h") or 0),
        },
    )
    if pipeline_stale:
        stale_components.append("pipeline_events")

    opportunities_total = int(opportunities_row.get("total") or 0)
    opportunities_active = int(opportunities_row.get("active") or 0)
    opportunities_settled = int(opportunities_row.get("settled") or 0)

    high_7d = int(calibration_row.get("high_7d") or 0)
    medium_7d = int(calibration_row.get("medium_7d") or 0)
    errors_1h = int(pipeline_row.get("errors_1h") or 0)
    errors_24h = int(pipeline_row.get("errors_24h") or 0)

    if errors_1h > 0:
        status = "ERROR"
    elif stale_components:
        status = "STALE"
    else:
        status = "OK"

    return {
        "status": status,
        "checked_at": now_utc.isoformat(),
        "thresholds_seconds": STALE_THRESHOLDS_SECONDS,
        "stale_components": stale_components,
        "components": {
            "ohlcv": {
                "1h": ohlcv_1h_component,
                "1d": ohlcv_1d_component,
            },
            "events": events_component,
            "feature_events": feature_component,
            "predictions": prediction_component,
            "pipeline_events": pipeline_component,
        },
        "metrics": {
            "opportunities": {
                "active": opportunities_active,
                "settled": opportunities_settled,
                "other": max(0, opportunities_total - opportunities_active - opportunities_settled),
                "total": opportunities_total,
            },
            "calibration_alerts_7d": {
                "high": high_7d,
                "medium": medium_7d,
                "high_medium_total": high_7d + medium_7d,
            },
            "pipeline_errors": {
                "last_1h": errors_1h,
                "last_24h": errors_24h,
            },
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Equities pipeline health check")
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    conn = None
    try:
        conn = connect(args.database_url)
        payload = _compute_health_payload(conn)
    except Exception as exc:
        payload = {
            "status": "ERROR",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        }
    finally:
        if conn is not None:
            conn.close()

    print(json.dumps(payload, sort_keys=args.pretty, indent=2 if args.pretty else None))
    if payload["status"] == "ERROR":
        return 2
    if payload["status"] == "STALE":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
