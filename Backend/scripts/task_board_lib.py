"""Lightweight task board helpers used by equities scripts."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def _json(data: dict[str, Any] | list[Any] | None) -> str:
    return json.dumps(data if data is not None else {}, default=_json_default)


def get_database_url(database_url: str | None = None) -> str:
    resolved = database_url or os.getenv("DATABASE_URL")
    if not resolved:
        raise RuntimeError("DATABASE_URL is not configured")
    return resolved


def connect(database_url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(get_database_url(database_url), row_factory=dict_row)


def _append_event_cursor(
    cur: psycopg.Cursor,
    *,
    actor: str,
    event_type: str,
    message: str,
    task_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO task_board_events (actor, task_id, event_type, message, data)
        VALUES (%s, %s::uuid, %s, %s, %s::jsonb);
        """,
        (
            actor,
            task_id,
            event_type,
            message,
            _json(data),
        ),
    )


def append_event(
    conn: psycopg.Connection,
    *,
    actor: str,
    event_type: str,
    message: str,
    task_id: str | None = None,
    data: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    with conn.cursor() as cur:
        _append_event_cursor(
            cur,
            actor=actor,
            event_type=event_type,
            message=message,
            task_id=task_id,
            data=data,
        )
    if commit:
        conn.commit()


def create_task(
    conn: psycopg.Connection,
    *,
    created_by: str,
    task_type: str,
    title: str,
    description: str,
    priority: int = 0,
    status: str = "OPEN",
    max_attempts: int = 5,
    depends_on: list[str] | None = None,
    tags: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    needs_scoping: bool = False,
    blocked_reason: str | None = None,
    task_id: str | None = None,
    commit: bool = True,
    emit_event: bool = True,
) -> str:
    task_uuid = task_id or str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO task_board_tasks (
                id,
                created_by,
                task_type,
                title,
                description,
                priority,
                status,
                max_attempts,
                depends_on,
                tags,
                payload,
                needs_scoping,
                blocked_reason
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s::uuid[],
                %s::jsonb,
                %s::jsonb,
                %s,
                %s
            );
            """,
            (
                task_uuid,
                created_by,
                task_type,
                title,
                description,
                int(priority),
                status,
                int(max_attempts),
                depends_on or [],
                _json(tags),
                _json(payload),
                bool(needs_scoping),
                blocked_reason,
            ),
        )
        if emit_event:
            _append_event_cursor(
                cur,
                actor=created_by,
                event_type="TASK_CREATED",
                message=f"Task created: {title}",
                task_id=task_uuid,
                data={
                    "task_type": task_type,
                    "priority": int(priority),
                    "needs_scoping": bool(needs_scoping),
                },
            )
    if commit:
        conn.commit()
    return task_uuid


def create_insight(
    conn: psycopg.Connection,
    *,
    actor: str,
    title: str,
    description: str,
    tags: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 5,
    commit: bool = True,
) -> str:
    merged_tags = dict(tags or {})
    merged_tags.setdefault("thread", "insight")
    task_id = create_task(
        conn,
        created_by=actor,
        task_type="INSIGHT",
        title=title,
        description=description,
        priority=priority,
        tags=merged_tags,
        payload=payload,
        needs_scoping=True,
        commit=False,
    )
    with conn.cursor() as cur:
        _append_event_cursor(
            cur,
            actor=actor,
            event_type="INSIGHT_CREATED",
            message=title,
            task_id=task_id,
            data={"source": actor},
        )
    if commit:
        conn.commit()
    return task_id
