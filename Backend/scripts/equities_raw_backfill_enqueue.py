#!/usr/bin/env python3
"""Enqueue shard-safe equities raw backfill tasks (OHLCV + mentions/articles)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from equities_predictor.universe import get_default_symbols
from task_board_lib import append_event, connect, create_insight, create_task


def _symbols_csv(symbols_csv: str | None, top_n: int) -> str:
    if symbols_csv:
        symbols = [s.strip().upper() for s in symbols_csv.split(",") if s.strip()]
    else:
        symbols = list(get_default_symbols())
    if top_n > 0:
        symbols = symbols[:top_n]
    deduped: list[str] = []
    seen = set()
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            deduped.append(symbol)
    return ",".join(deduped)


def enqueue(
    *,
    task_board_db_url: str | None,
    data_db_url: str | None,
    actor: str,
    symbols_csv: str | None,
    top_n: int,
    lookback_days: int,
    ohlcv_shards: int,
    mention_shards: int,
    mention_sources: str,
) -> dict:
    shard_ohlcv = max(5, int(ohlcv_shards))
    shard_mentions = max(5, int(mention_shards))
    symbols = _symbols_csv(symbols_csv, top_n=max(1, int(top_n)))
    data_url = data_db_url or task_board_db_url
    if not data_url:
        raise RuntimeError("Either --database-url or --data-database-url must be set")

    conn = connect(task_board_db_url)
    created_ohlcv: list[str] = []
    created_mentions: list[str] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ohlcv_batch = f"eq_ohlcv_backfill_{stamp}"
    mention_batch = f"eq_mentions_backfill_{stamp}"

    try:
        create_insight(
            conn,
            actor=actor,
            title="Assumption: top-N equities backfill universe",
            description=(
                "Defaulted to deterministic equities universe from equities_predictor.universe, "
                f"top_n={top_n}, for raw-first 2y backfill without paid feeds."
            ),
            tags={"thread": "assumption", "program": "equities_forward_predictor"},
            payload={
                "target_task_type": "EXEC",
                "acceptance_criteria": [
                    "Backfill shards are deterministic and reproducible",
                    "Universe choice is documented in task_board_events",
                ],
                "definition_of_done": "Assumption logged and shards enqueued.",
            },
            priority=8,
            commit=False,
        )

        for shard in range(shard_ohlcv):
            task_id = create_task(
                conn,
                created_by=actor,
                task_type="BACKFILL",
                title=f"Equities OHLCV raw backfill shard {shard + 1}/{shard_ohlcv}",
                description="2y OHLCV backfill for deterministic symbol shard (1h + 1d).",
                priority=9,
                tags={
                    "program": "equities_forward_predictor",
                    "kind": "ohlcv_backfill",
                    "slice_group": ohlcv_batch,
                    "slice_index": shard,
                    "slice_total": shard_ohlcv,
                    "lookback_days": lookback_days,
                },
                payload={
                    "slice": {"index": shard, "total": shard_ohlcv},
                    "lookback_days": lookback_days,
                    "symbols": symbols,
                    "timeout_seconds": 21600,
                    "command": [
                        "python3",
                        "/scripts/equities_ohlcv_ingestor.py",
                        "--database-url",
                        data_url,
                        "--symbols",
                        symbols,
                        "--timeframes",
                        "1h,1d",
                        "--lookback-days",
                        str(lookback_days),
                        "--shards",
                        str(shard_ohlcv),
                        "--shard",
                        str(shard),
                        "--actor",
                        actor,
                    ],
                    "acceptance_criteria": [
                        "No duplicate explosion on rerun",
                        "OHLCV bars inserted with deterministic shard scope",
                        "Rows carry source=equities_* and batch_id metadata",
                    ],
                    "definition_of_done": "Shard logs inserted row counts and completes without fatal errors.",
                },
                needs_scoping=False,
                commit=False,
            )
            created_ohlcv.append(task_id)

        for shard in range(shard_mentions):
            task_id = create_task(
                conn,
                created_by=actor,
                task_type="BACKFILL",
                title=f"Equities mentions/articles raw backfill shard {shard + 1}/{shard_mentions}",
                description=(
                    "2y raw mentions/articles backfill (SEC filings, GDELT article lists, Reddit free endpoint) "
                    "for deterministic symbol shard."
                ),
                priority=10,
                tags={
                    "program": "equities_forward_predictor",
                    "kind": "raw_mentions_backfill",
                    "slice_group": mention_batch,
                    "slice_index": shard,
                    "slice_total": shard_mentions,
                    "lookback_days": lookback_days,
                },
                payload={
                    "slice": {"index": shard, "total": shard_mentions},
                    "lookback_days": lookback_days,
                    "symbols": symbols,
                    "timeout_seconds": 28800,
                    "command": [
                        "python3",
                        "/scripts/equities_raw_mentions_backfill.py",
                        "--database-url",
                        data_url,
                        "--symbols",
                        symbols,
                        "--top-n",
                        str(top_n),
                        "--lookback-days",
                        str(lookback_days),
                        "--sources",
                        mention_sources,
                        "--shards",
                        str(shard_mentions),
                        "--shard",
                        str(shard),
                        "--gdelt-window-days",
                        "14",
                        "--gdelt-max-records",
                        "120",
                        "--reddit-window-days",
                        "21",
                        "--reddit-size",
                        "120",
                        "--actor",
                        actor,
                    ],
                    "acceptance_criteria": [
                        "Raw mentions inserted into equities_raw_mentions append-only table",
                        "Normalized event rows inserted into equities_events without lookahead",
                        "SEC/GDELT/Reddit source fallback errors logged without halting shard",
                    ],
                    "definition_of_done": "Shard result includes per-source record counts and error totals.",
                },
                needs_scoping=False,
                commit=False,
            )
            created_mentions.append(task_id)

        append_event(
            conn,
            actor=actor,
            event_type="BACKFILL_ENQUEUED",
            message="Enqueued shard-safe equities OHLCV + raw mentions backfill tasks",
            data={
                "ohlcv_batch": ohlcv_batch,
                "mentions_batch": mention_batch,
                "lookback_days": lookback_days,
                "symbols": symbols,
                "ohlcv_shards": shard_ohlcv,
                "mention_shards": shard_mentions,
                "mention_sources": mention_sources,
                "task_ids": created_ohlcv + created_mentions,
            },
            commit=False,
        )
        conn.commit()
        return {
            "ohlcv_batch": ohlcv_batch,
            "mentions_batch": mention_batch,
            "symbols": symbols,
            "ohlcv_task_ids": created_ohlcv,
            "mentions_task_ids": created_mentions,
        }
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enqueue sharded equities raw backfill tasks")
    parser.add_argument("--database-url", default=None, help="Task board database URL")
    parser.add_argument("--data-database-url", default=None, help="Data-plane database URL; defaults to --database-url")
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--top-n", type=int, default=62)
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument("--ohlcv-shards", type=int, default=10)
    parser.add_argument("--mention-shards", type=int, default=15)
    parser.add_argument("--mention-sources", default="sec,gdelt,reddit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out = enqueue(
        task_board_db_url=args.database_url,
        data_db_url=args.data_database_url,
        actor=args.actor,
        symbols_csv=args.symbols,
        top_n=max(1, int(args.top_n)),
        lookback_days=max(1, int(args.lookback_days)),
        ohlcv_shards=max(1, int(args.ohlcv_shards)),
        mention_shards=max(1, int(args.mention_shards)),
        mention_sources=args.mention_sources,
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
