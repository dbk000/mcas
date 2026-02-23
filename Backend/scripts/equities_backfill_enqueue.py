#!/usr/bin/env python3
"""Enqueue equities backfill as >=5 deterministic shard tasks on Task Board."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from task_board_lib import connect, create_task, append_event


def enqueue_backfill(
    *,
    database_url: str | None,
    actor: str,
    lookback_days: int,
    shards: int,
    symbols_csv: str,
) -> dict:
    if shards < 5:
        shards = 5

    conn = connect(database_url)
    created = []
    batch_id = f"eq_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    try:
        for shard in range(shards):
            task_id = create_task(
                conn,
                created_by=actor,
                task_type="BACKFILL",
                title=f"Equities predictor backfill shard {shard + 1}/{shards}",
                description="Backfill equities OHLCV/events/features/predictions for one deterministic shard.",
                priority=8,
                tags={
                    "program": "equities_forward_predictor",
                    "slice_group": batch_id,
                    "slice_index": shard,
                    "slice_total": shards,
                },
                payload={
                    "slice": {"index": shard, "total": shards},
                    "lookback_days": lookback_days,
                    "symbols": symbols_csv,
                    "command": [
                        "python3",
                        "scripts/equities_ohlcv_ingestor.py",
                        "--lookback-days",
                        str(lookback_days),
                        "--symbols",
                        symbols_csv,
                        "--shards",
                        str(shards),
                        "--shard",
                        str(shard),
                        "--actor",
                        actor,
                    ],
                    "acceptance_criteria": [
                        "Shard run completed without fatal errors",
                        "Inserted rows tracked by batch_id",
                        "Deterministic rerun yields no duplicate explosion",
                    ],
                    "definition_of_done": "Shard result logged with inserted row counts.",
                },
                needs_scoping=False,
                commit=False,
            )
            created.append(task_id)

        append_event(
            conn,
            actor=actor,
            event_type="BACKFILL_ENQUEUED",
            message=f"Enqueued equities backfill in {shards} shards",
            data={"batch_id": batch_id, "shards": shards, "task_ids": created},
            commit=False,
        )
        conn.commit()
        return {"batch_id": batch_id, "shards": shards, "task_ids": created}
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enqueue shard-safe equities backfill tasks")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--shards", type=int, default=5)
    parser.add_argument("--symbols", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out = enqueue_backfill(
        database_url=args.database_url,
        actor=args.actor,
        lookback_days=max(1, int(args.lookback_days)),
        shards=max(1, int(args.shards)),
        symbols_csv=args.symbols,
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
