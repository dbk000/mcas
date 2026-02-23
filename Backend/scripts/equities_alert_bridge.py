#!/usr/bin/env python3
"""Bridge calibration alerts into executable task-board remediation tasks."""

from __future__ import annotations

import argparse
from typing import Any

from task_board_lib import connect, create_task, append_event


def run_bridge(
    *,
    database_url: str | None,
    actor: str,
    lookback_days: int,
    max_tasks: int,
) -> dict[str, Any]:
    conn = connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.id, a.created_at, a.timeframe, a.horizon, a.severity, a.alert_type, a.message, a.data
                FROM equities_calibration_alerts a
                WHERE a.created_at >= NOW() - make_interval(days => %s)
                  AND a.severity IN ('HIGH', 'MEDIUM')
                  AND NOT EXISTS (
                        SELECT 1
                        FROM task_board_tasks t
                        WHERE t.task_type = 'EXEC'
                          AND t.tags ->> 'source_calibration_alert_id' = a.id::text
                  )
                ORDER BY a.created_at DESC
                LIMIT %s
                FOR UPDATE SKIP LOCKED;
                """,
                (lookback_days, max_tasks),
            )
            alerts = list(cur.fetchall())

        created: list[str] = []
        for alert in alerts:
            sev = str(alert["severity"]).upper()
            priority = 10 if sev == "HIGH" else 8
            task_id = create_task(
                conn,
                created_by=actor,
                task_type="EXEC",
                title=f"Calibration remediation {alert['timeframe']} {alert['horizon']} [{sev}]",
                description=(
                    f"{alert['message']}\n"
                    "Use settled outcomes and bucket diagnostics to restore confidence monotonicity."
                ),
                priority=priority,
                tags={
                    "program": "equities_forward_predictor",
                    "track": "validate",
                    "area": "calibration_remediation",
                    "source_calibration_alert_id": str(alert["id"]),
                    "timeframe": alert["timeframe"],
                    "horizon": alert["horizon"],
                    "severity": sev,
                },
                payload={
                    "alert_id": str(alert["id"]),
                    "alert_type": alert["alert_type"],
                    "alert_data": alert["data"] or {},
                    "command": [
                        "python3",
                        "scripts/equities_policy_optimizer.py",
                        "--actor",
                        actor,
                        "--lookback-days",
                        "180",
                        "--min-samples",
                        "20",
                        "--transaction-cost-bps",
                        "6.0",
                    ],
                    "timeout_seconds": 2400,
                    "acceptance_criteria": [
                        "Confidence-bucket monotonicity violations are reduced or eliminated",
                        "Directional return and hit-rate for higher confidence buckets improve",
                        "Regression tests and pipeline events document the remediation",
                    ],
                    "definition_of_done": "Remediation implemented and verified via calibration watchdog rerun.",
                },
                needs_scoping=False,
                commit=False,
            )
            created.append(task_id)

        append_event(
            conn,
            actor=actor,
            event_type="CALIBRATION_ALERT_BRIDGED",
            message=f"Bridged {len(created)} calibration alerts into remediation EXEC tasks",
            data={"created_task_ids": created},
            commit=False,
        )
        conn.commit()
        return {"alerts_seen": len(alerts), "tasks_created": len(created), "task_ids": created}
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge calibration alerts into task-board tasks")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--max-tasks", type=int, default=25)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_bridge(
        database_url=args.database_url,
        actor=args.actor,
        lookback_days=max(1, int(args.lookback_days)),
        max_tasks=max(1, int(args.max_tasks)),
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
