#!/usr/bin/env python3
"""Continuous worker loop for claiming and executing equities EXEC tasks."""

from __future__ import annotations

import json
import os
import signal
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from equities_task_executor import _select_handler

QUEUE_EMPTY_SLEEP_SECONDS = 15
CLAIMABLE_PROGRAMS = [
    p.strip()
    for p in os.getenv("EQUITIES_TASK_PROGRAMS", "equities_forward_predictor").split(",")
    if p.strip()
]
if not CLAIMABLE_PROGRAMS:
    CLAIMABLE_PROGRAMS = ["equities_forward_predictor"]

_STOP_EVENT = threading.Event()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **fields: Any) -> None:
    payload = {"ts": _now_iso(), "event": event, **fields}
    print(json.dumps(payload, default=str), flush=True)


def _on_signal(signum: int, _frame: Any) -> None:
    sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    _log("shutdown_signal", signal=sig_name)
    _STOP_EVENT.set()


def _connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


def _task_board_columns(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'task_board_tasks';
            """
        )
        return {str(r["column_name"]) for r in cur.fetchall()}


def _claim_next_task(
    conn: psycopg.Connection,
    *,
    actor: str,
    claimable_programs: list[str],
    task_columns: set[str],
) -> dict[str, Any] | None:
    set_parts = ["status = 'IN_PROGRESS'"]
    params: list[Any] = [claimable_programs]
    if "claimed_by" in task_columns:
        set_parts.append("claimed_by = %s")
        params.append(actor)
    if "claimed_at" in task_columns:
        set_parts.append("claimed_at = NOW()")
    if "updated_at" in task_columns:
        set_parts.append("updated_at = NOW()")

    sql = f"""
        WITH claim AS (
            SELECT id
            FROM task_board_tasks
            WHERE task_type = 'EXEC'
              AND status = 'OPEN'
              AND needs_scoping = FALSE
              AND tags ->> 'program' = ANY(%s::text[])
            ORDER BY priority DESC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE task_board_tasks t
        SET {", ".join(set_parts)}
        FROM claim
        WHERE t.id = claim.id
        RETURNING t.id, t.title, t.payload, t.tags;
    """

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            return dict(row) if row else None


def _mark_done(
    conn: psycopg.Connection,
    *,
    task_id: str,
    result_payload: dict[str, Any],
    task_columns: set[str],
) -> None:
    set_parts = ["status = 'DONE'"]
    params: list[Any] = []
    if "result" in task_columns:
        set_parts.append("result = %s::jsonb")
        params.append(json.dumps(result_payload, default=str))
    if "error" in task_columns:
        set_parts.append("error = NULL")
    if "blocked_reason" in task_columns:
        set_parts.append("blocked_reason = NULL")
    if "finished_at" in task_columns:
        set_parts.append("finished_at = NOW()")
    if "updated_at" in task_columns:
        set_parts.append("updated_at = NOW()")
    params.append(task_id)

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE task_board_tasks
                SET {", ".join(set_parts)}
                WHERE id = %s::uuid;
                """,
                tuple(params),
            )


def _mark_blocked(
    conn: psycopg.Connection,
    *,
    task_id: str,
    error_payload: dict[str, Any],
    task_columns: set[str],
) -> None:
    error_text = str(error_payload.get("error_message") or "task failed")
    set_parts = ["status = 'BLOCKED'"]
    params: list[Any] = []
    if "result" in task_columns:
        set_parts.append("result = %s::jsonb")
        params.append(json.dumps(error_payload, default=str))
    if "error" in task_columns:
        set_parts.append("error = %s")
        params.append(json.dumps(error_payload, default=str))
    if "blocked_reason" in task_columns:
        set_parts.append("blocked_reason = %s")
        params.append(error_text[:500])
    if "finished_at" in task_columns:
        set_parts.append("finished_at = NOW()")
    if "updated_at" in task_columns:
        set_parts.append("updated_at = NOW()")
    params.append(task_id)

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE task_board_tasks
                SET {", ".join(set_parts)}
                WHERE id = %s::uuid;
                """,
                tuple(params),
            )


def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    actor = os.getenv("TASK_WORKER_ACTOR") or os.getenv("SERVICE_NAME") or "equities_task_worker"

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    _log(
        "worker_start",
        actor=actor,
        claimable_programs=CLAIMABLE_PROGRAMS,
        queue_empty_sleep_s=QUEUE_EMPTY_SLEEP_SECONDS,
    )

    with _connect(database_url) as conn:
        task_columns = _task_board_columns(conn)
        _log(
            "schema_columns_detected",
            supports_finished_at=("finished_at" in task_columns),
            supports_result=("result" in task_columns),
            supports_error=("error" in task_columns),
        )

        while not _STOP_EVENT.is_set():
            try:
                task = _claim_next_task(
                    conn,
                    actor=actor,
                    claimable_programs=CLAIMABLE_PROGRAMS,
                    task_columns=task_columns,
                )
            except Exception as exc:
                _log("claim_error", error=str(exc))
                _STOP_EVENT.wait(5)
                continue

            if not task:
                _log("queue_empty", sleep_s=QUEUE_EMPTY_SLEEP_SECONDS)
                _STOP_EVENT.wait(QUEUE_EMPTY_SLEEP_SECONDS)
                continue

            task_id = str(task.get("id"))
            task_title = str(task.get("title") or "")
            task_payload = task.get("payload")
            if not isinstance(task_payload, dict):
                task_payload = {}

            _log("task_claimed", task_id=task_id, title=task_title)
            started_at = time.time()

            try:
                handler_name, handler = _select_handler(task_title, task_payload)
                with conn.transaction():
                    with conn.cursor() as cur:
                        handler_output = handler(cur)

                elapsed_s = round(time.time() - started_at, 3)
                done_payload = {
                    "worker": actor,
                    "handler": handler_name,
                    "completed_at": _now_iso(),
                    "duration_s": elapsed_s,
                    "output": handler_output,
                }
                _mark_done(conn, task_id=task_id, result_payload=done_payload, task_columns=task_columns)
                _log("task_done", task_id=task_id, handler=handler_name, duration_s=elapsed_s)
            except Exception as exc:
                elapsed_s = round(time.time() - started_at, 3)
                error_payload = {
                    "worker": actor,
                    "failed_at": _now_iso(),
                    "duration_s": elapsed_s,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(),
                }
                try:
                    _mark_blocked(conn, task_id=task_id, error_payload=error_payload, task_columns=task_columns)
                except Exception as mark_exc:
                    _log("task_blocked_update_error", task_id=task_id, error=str(mark_exc))
                _log("task_blocked", task_id=task_id, error=str(exc), duration_s=elapsed_s)

    _log("worker_stop", actor=actor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
