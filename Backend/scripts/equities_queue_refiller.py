#!/usr/bin/env python3
"""Brute-force equities queue refiller to keep S2/S3 continuously supplied."""

from __future__ import annotations

import argparse
import signal
import time
from datetime import datetime, timezone
from typing import Any

from task_board_lib import connect, create_task, append_event

_STOP = False

TASK_TEMPLATES = [
    {
        "title": "Equities source reliability audit",
        "description": "Measure per-symbol fetch success and latency across free OHLCV sources; propose failover thresholds.",
        "tags": {"track": "validate", "area": "ohlcv_sources"},
        "payload": {
            "acceptance_criteria": [
                "Per-source success-rate table produced for at least 30 symbols",
                "Fallback trigger thresholds documented in task result",
                "Task events include deterministic artifact key",
            ],
            "definition_of_done": "Reliability report committed and fallback policy recommendation recorded.",
        },
    },
    {
        "title": "Session alignment edge-case test pack",
        "description": "Add tests for pre-open, post-close, weekend, and holiday event-to-bar alignment behavior.",
        "tags": {"track": "validate", "area": "session_calendar"},
        "payload": {
            "acceptance_criteria": [
                "At least 6 new session edge-case tests added",
                "No event maps to bars earlier than publish timestamp",
                "Timezone conversions are explicit and covered by assertions",
            ],
            "definition_of_done": "All new session tests pass in pytest run.",
        },
    },
    {
        "title": "Event dedupe quality uplift",
        "description": "Tune hash and near-duplicate rules to reduce duplicate story families without dropping unique news.",
        "tags": {"track": "exploit", "area": "event_dedupe"},
        "payload": {
            "acceptance_criteria": [
                "Dedupe precision/recall proxy reported on sampled events",
                "Rule changes include regression test coverage",
                "Result logs include before/after duplicate counts",
            ],
            "definition_of_done": "Dedupe heuristics updated with measurable improvement evidence.",
        },
    },
    {
        "title": "Anti-bot heuristic robustness check",
        "description": "Stress test anti-bot filters against bursty posting patterns and coordinated source clusters.",
        "tags": {"track": "validate", "area": "anti_bot"},
        "payload": {
            "acceptance_criteria": [
                "Synthetic burst scenarios executed",
                "False-positive and false-negative risk notes captured",
                "Heuristic thresholds updated or justified as-is",
            ],
            "definition_of_done": "Anti-bot check emits pass/fail metrics and mitigation notes.",
        },
    },
    {
        "title": "Regime label stability analysis",
        "description": "Evaluate volatility/trend regime labels for churn and predictive utility by horizon.",
        "tags": {"track": "explore", "area": "regimes"},
        "payload": {
            "acceptance_criteria": [
                "Regime transition matrix generated",
                "Per-regime performance summary for 1d/3d/5d horizons",
                "At least one actionable regime refinement proposed",
            ],
            "definition_of_done": "Regime analysis artifact logged with follow-up actions.",
        },
    },
    {
        "title": "Feature leakage guard expansion",
        "description": "Add leakage tests that randomize event time, shift labels, and verify collapse from non-causal joins.",
        "tags": {"track": "validate", "area": "leakage"},
        "payload": {
            "acceptance_criteria": [
                "Timestamp-randomization collapse test added or strengthened",
                "Label shift sanity test asserts expected degradation",
                "Time split and symbol split leakage checks pass",
            ],
            "definition_of_done": "Leakage suite expanded with failing-when-broken coverage.",
        },
    },
    {
        "title": "Opportunity explanation completeness audit",
        "description": "Ensure each opportunity exposes top features, supporting events, regime context, and horizon rationale.",
        "tags": {"track": "exploit", "area": "opportunity_explainability"},
        "payload": {
            "acceptance_criteria": [
                "Explanation payload contract validated for latest opportunities",
                "Missing provenance fields patched with tests",
                "Deterministic ordering for contributors verified",
            ],
            "definition_of_done": "Opportunity explanations satisfy provenance contract end-to-end.",
        },
    },
    {
        "title": "Model ablation drift monitor",
        "description": "Run scheduled ablations (no sentiment/no events/no price features) and flag unexpected contribution drift.",
        "tags": {"track": "validate", "area": "model_ablation"},
        "payload": {
            "acceptance_criteria": [
                "Ablation metrics produced for current training window",
                "Drift thresholds defined and evaluated",
                "Any significant drift logged with candidate root causes",
            ],
            "definition_of_done": "Ablation monitor output is persisted and comparable run-to-run.",
        },
    },
    {
        "title": "Free-source coverage expansion sweep",
        "description": "Add or harden free/open event sources with robots-respect, caching, and deterministic extraction.",
        "tags": {"track": "explore", "area": "source_expansion"},
        "payload": {
            "acceptance_criteria": [
                "At least one source integration or parser reliability improvement completed",
                "Robots and rate-limit behavior documented in result",
                "Fallback path defined when source is blocked",
            ],
            "definition_of_done": "Coverage increase is measurable and reproducible.",
        },
    },
    {
        "title": "Backfill slice quality enforcement",
        "description": "Verify backfill requests are sharded into at least 5 slices with balanced symbol/time allocations.",
        "tags": {"track": "exploit", "area": "backfill"},
        "payload": {
            "acceptance_criteria": [
                "Any unsplit backfill task is cancelled and replaced",
                "Created slices include deterministic shard keys",
                "Validation event emitted with slice counts",
            ],
            "definition_of_done": "Backfill queue contains only compliant slice tasks.",
        },
    },
]


EXEC_COMMAND_PLANS = [
    {
        "name": "ohlcv_source_audit",
        "timeout_seconds": 2400,
        "command": [
            "python3",
            "/scripts/equities_ohlcv_ingestor.py",
            "--lookback-days",
            "45",
            "--timeframes",
            "1h,1d",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "session_alignment_feature_build",
        "timeout_seconds": 2400,
        "command": [
            "python3",
            "/scripts/equities_feature_builder.py",
            "--timeframe",
            "1h",
            "--lookback-days",
            "90",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "event_dedupe_collection",
        "timeout_seconds": 1800,
        "command": [
            "python3",
            "/scripts/equities_events_collector.py",
            "--max-sec-items",
            "30",
            "--max-news-items",
            "15",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "anti_bot_collection",
        "timeout_seconds": 1800,
        "command": [
            "python3",
            "/scripts/equities_events_collector.py",
            "--max-sec-items",
            "35",
            "--max-news-items",
            "18",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "regime_stability_feature_build",
        "timeout_seconds": 2400,
        "command": [
            "python3",
            "/scripts/equities_feature_builder.py",
            "--timeframe",
            "1d",
            "--lookback-days",
            "180",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "leakage_guard_predictor_1h",
        "timeout_seconds": 3600,
        "command": [
            "python3",
            "/scripts/equities_forward_predictor.py",
            "--timeframe",
            "1h",
            "--lookback-days",
            "180",
            "--model-version",
            "v1_realwork_leakage",
            "--min-confidence",
            "0.10",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "opportunity_explain_predictor_1d",
        "timeout_seconds": 3600,
        "command": [
            "python3",
            "/scripts/equities_forward_predictor.py",
            "--timeframe",
            "1d",
            "--lookback-days",
            "180",
            "--model-version",
            "v1_realwork_explain",
            "--min-confidence",
            "0.10",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "model_ablation_policy_optimizer",
        "timeout_seconds": 2400,
        "command": [
            "python3",
            "/scripts/equities_policy_optimizer.py",
            "--lookback-days",
            "120",
            "--min-samples",
            "20",
            "--transaction-cost-bps",
            "6.0",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "source_expansion_event_sweep",
        "timeout_seconds": 1800,
        "command": [
            "python3",
            "/scripts/equities_events_collector.py",
            "--max-sec-items",
            "40",
            "--max-news-items",
            "20",
            "--actor",
            "{actor}",
        ],
    },
    {
        "name": "backfill_slice_rule_enforcement",
        "timeout_seconds": 900,
        "command": [
            "python3",
            "/scripts/task_board_backfill_splitter.py",
            "--min-slices",
            "5",
            "--actor",
            "{actor}",
        ],
    },
]


def _resolve_command(parts: list[str], *, actor: str) -> list[str]:
    return [p.replace("{actor}", actor) if isinstance(p, str) else str(p) for p in parts]


def _handle_signal(_signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True


def _ready_exec_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM task_board_tasks
            WHERE status='OPEN'
              AND task_type='EXEC'
              AND needs_scoping=FALSE
              AND (payload ? 'command')
              AND tags ->> 'program' = 'equities_forward_predictor';
            """
        )
        return int(cur.fetchone()["n"])


def _enqueue(conn, actor: str, count: int) -> int:
    created = 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for i in range(count):
        seed = f"eq_refill_{stamp}_{i:03d}"
        template = TASK_TEMPLATES[i % len(TASK_TEMPLATES)]
        plan = EXEC_COMMAND_PLANS[i % len(EXEC_COMMAND_PLANS)]
        payload = dict(template.get("payload", {}))
        payload["command"] = _resolve_command(plan["command"], actor=actor)
        payload["timeout_seconds"] = int(plan["timeout_seconds"])
        payload["execution_plan"] = plan["name"]
        create_task(
            conn,
            created_by=actor,
            task_type="EXEC",
            title=f"{template['title']} [{stamp}-{i:03d}]",
            description=template["description"],
            priority=6,
            tags={
                "seed_key": seed,
                "program": "equities_forward_predictor",
                "auto_refill": True,
                "execution_plan": plan["name"],
                **template.get("tags", {}),
            },
            payload=payload,
            needs_scoping=False,
            commit=False,
        )
        created += 1

    append_event(
        conn,
        actor=actor,
        event_type="BACKLOG_REFILLED",
        message=f"Equities refill injected {created} command-backed EXEC tasks",
        data={"created": created, "program": "equities_forward_predictor"},
        commit=False,
    )
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Equities task queue refiller")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--min-ready", type=int, default=25)
    parser.add_argument("--refill-batch", type=int, default=30)
    parser.add_argument("--interval-seconds", type=int, default=10)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    conn = connect(args.database_url)
    try:
        while not _STOP:
            ready = _ready_exec_count(conn)
            created = 0
            if ready < args.min_ready:
                created = _enqueue(conn, actor=args.actor, count=max(1, args.refill_batch))
                conn.commit()
                ready = _ready_exec_count(conn)
            print({"ready_exec": ready, "created": created})
            if args.once:
                break
            time.sleep(max(2, int(args.interval_seconds)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
