#!/usr/bin/env python3
"""Detect calibration and ranking drift from settled equities outcomes."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np

from equities_predictor.db import (
    connect,
    insert_calibration_alert,
    list_settled_outcomes,
    log_pipeline_event,
)
from equities_predictor.ids import deterministic_uuid, stable_hash


BUCKETS = [
    ("0.80-1.00", 0.80, 1.01),
    ("0.60-0.79", 0.60, 0.80),
    ("0.40-0.59", 0.40, 0.60),
    ("0.20-0.39", 0.20, 0.40),
    ("0.00-0.19", 0.00, 0.20),
]


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label, lo, hi in BUCKETS:
        subset = [
            r for r in rows
            if float(r.get("confidence") or 0.0) >= lo
            and float(r.get("confidence") or 0.0) < hi
        ]
        if not subset:
            out.append(
                {
                    "bucket": label,
                    "count": 0,
                    "avg_directional_return": 0.0,
                    "hit_rate": 0.0,
                    "avg_brier_loss": 0.0,
                }
            )
            continue
        directional = np.array([float(x["directional_realized_return"]) for x in subset], dtype=float)
        hits = np.array([1.0 if bool(x["hit"]) else 0.0 for x in subset], dtype=float)
        brier = np.array([float(x["brier_loss"]) for x in subset], dtype=float)
        out.append(
            {
                "bucket": label,
                "count": int(len(subset)),
                "avg_directional_return": float(np.mean(directional)),
                "hit_rate": float(np.mean(hits)),
                "avg_brier_loss": float(np.mean(brier)),
            }
        )
    return out


def _detect_violations(
    buckets: list[dict[str, Any]],
    *,
    min_bucket_samples: int,
    return_tolerance: float,
    hit_tolerance: float,
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for i in range(len(buckets) - 1):
        hi = buckets[i]
        lo = buckets[i + 1]
        if hi["count"] < min_bucket_samples or lo["count"] < min_bucket_samples:
            continue

        if hi["avg_directional_return"] + return_tolerance < lo["avg_directional_return"]:
            violations.append(
                {
                    "type": "return_monotonicity_break",
                    "higher_bucket": hi["bucket"],
                    "lower_bucket": lo["bucket"],
                    "higher_return": hi["avg_directional_return"],
                    "lower_return": lo["avg_directional_return"],
                    "gap": lo["avg_directional_return"] - hi["avg_directional_return"],
                }
            )
        if hi["hit_rate"] + hit_tolerance < lo["hit_rate"]:
            violations.append(
                {
                    "type": "hitrate_monotonicity_break",
                    "higher_bucket": hi["bucket"],
                    "lower_bucket": lo["bucket"],
                    "higher_hit_rate": hi["hit_rate"],
                    "lower_hit_rate": lo["hit_rate"],
                    "gap": lo["hit_rate"] - hi["hit_rate"],
                }
            )
    return violations


def run_watchdog(
    *,
    database_url: str | None,
    actor: str,
    lookback_days: int,
    min_bucket_samples: int,
    return_tolerance: float,
    hit_tolerance: float,
) -> dict[str, Any]:
    conn = connect(database_url)
    try:
        outcomes = list_settled_outcomes(
            conn,
            timeframe=None,
            horizon=None,
            lookback_days=lookback_days,
            limit=50000,
        )
        if not outcomes:
            log_pipeline_event(
                conn,
                actor=actor,
                event_type="CALIBRATION_WATCHDOG_NOOP",
                message="No settled outcomes available for calibration watchdog",
                data={"lookback_days": lookback_days},
            )
            conn.commit()
            return {"groups": 0, "alerts": 0}

        by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in outcomes:
            by_group[(row["timeframe"], row["horizon"])].append(row)

        alerts_written = 0
        group_results: dict[str, Any] = {}
        run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        for (timeframe, horizon), rows in sorted(by_group.items()):
            buckets = _bucket_rows(rows)
            violations = _detect_violations(
                buckets,
                min_bucket_samples=min_bucket_samples,
                return_tolerance=return_tolerance,
                hit_tolerance=hit_tolerance,
            )
            group_key = f"{timeframe}:{horizon}"
            group_results[group_key] = {
                "count": len(rows),
                "buckets": buckets,
                "violations": violations,
            }
            if not violations:
                continue

            max_gap = max(float(v.get("gap", 0.0)) for v in violations)
            severity = "HIGH" if max_gap > 0.05 else "MEDIUM"
            msg = (
                f"Calibration ranking drift for {group_key}: "
                f"{len(violations)} monotonicity violation(s)"
            )
            alert_id = deterministic_uuid(
                "equities_calibration_alert",
                run_stamp,
                timeframe,
                horizon,
                severity,
                len(violations),
                round(max_gap, 6),
            )
            insert_calibration_alert(
                conn,
                {
                    "id": alert_id,
                    "actor": actor,
                    "timeframe": timeframe,
                    "horizon": horizon,
                    "severity": severity,
                    "alert_type": "confidence_monotonicity",
                    "message": msg,
                    "data": {
                        "lookback_days": lookback_days,
                        "min_bucket_samples": min_bucket_samples,
                        "buckets": buckets,
                        "violations": violations,
                    },
                    "deterministic_hash": stable_hash(
                        timeframe,
                        horizon,
                        severity,
                        len(violations),
                        round(max_gap, 8),
                        [(v["type"], v["higher_bucket"], v["lower_bucket"]) for v in violations],
                    ),
                },
            )
            alerts_written += 1

        if alerts_written > 0:
            log_pipeline_event(
                conn,
                actor=actor,
                event_type="CALIBRATION_ALERT",
                message="Calibration watchdog detected confidence ranking drift",
                data={
                    "alerts_written": alerts_written,
                    "group_results": group_results,
                },
            )
        else:
            log_pipeline_event(
                conn,
                actor=actor,
                event_type="CALIBRATION_OK",
                message="Calibration watchdog found no ranking drift above tolerance",
                data={
                    "groups": len(by_group),
                    "lookback_days": lookback_days,
                    "min_bucket_samples": min_bucket_samples,
                },
            )
        conn.commit()
        return {"groups": len(by_group), "alerts": alerts_written, "group_results": group_results}
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run calibration watchdog over settled equities outcomes")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--min-bucket-samples", type=int, default=12)
    parser.add_argument("--return-tolerance", type=float, default=0.0010)
    parser.add_argument("--hit-tolerance", type=float, default=0.02)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_watchdog(
        database_url=args.database_url,
        actor=args.actor,
        lookback_days=max(30, int(args.lookback_days)),
        min_bucket_samples=max(5, int(args.min_bucket_samples)),
        return_tolerance=max(0.0, float(args.return_tolerance)),
        hit_tolerance=max(0.0, float(args.hit_tolerance)),
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
