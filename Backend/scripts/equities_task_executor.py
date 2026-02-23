#!/usr/bin/env python3
"""Execute concrete equities task-board work and emit measurable artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def _to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, tuple):
        return [_to_plain(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "task"


def _connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


def _fetch_rows(cur: psycopg.Cursor, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [_to_plain(dict(r)) for r in rows]


def _fetch_one(cur: psycopg.Cursor, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    cur.execute(sql, params)
    row = cur.fetchone()
    return _to_plain(dict(row)) if row else {}


def _handler_source_reliability(cur: psycopg.Cursor) -> dict[str, Any]:
    by_source = _fetch_rows(
        cur,
        """
        SELECT source,
               COUNT(*)::int AS events_24h,
               ROUND(AVG(EXTRACT(EPOCH FROM (available_at_ts - published_ts)))::numeric, 2) AS avg_lag_s,
               ROUND(
                 PERCENTILE_CONT(0.95)
                 WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (available_at_ts - published_ts)))::numeric,
                 2
               ) AS p95_lag_s
        FROM equities_events
        WHERE published_ts >= NOW() - INTERVAL '24 hours'
          AND available_at_ts IS NOT NULL
          AND published_ts IS NOT NULL
        GROUP BY source
        ORDER BY events_24h DESC
        LIMIT 12;
        """,
    )
    errors = _fetch_rows(
        cur,
        """
        SELECT COALESCE(data->>'source', '<unknown>') AS source,
               COUNT(*)::int AS errors_24h
        FROM equities_pipeline_events
        WHERE event_type = 'EQUITIES_SOURCE_ERROR'
          AND ts >= NOW() - INTERVAL '24 hours'
        GROUP BY 1
        ORDER BY errors_24h DESC
        LIMIT 12;
        """,
    )
    bar_freshness = _fetch_rows(
        cur,
        """
        SELECT timeframe,
               COUNT(*)::int AS rows,
               EXTRACT(EPOCH FROM (NOW() - MAX(bar_start_ts)))::int AS latest_bar_age_s
        FROM equities_bar_meta
        GROUP BY timeframe
        ORDER BY timeframe;
        """,
    )
    return {
        "headline": "Source reliability and freshness snapshot produced.",
        "highlights": {
            "sources_observed": len(by_source),
            "sources_with_errors": len([r for r in errors if int(r.get("errors_24h", 0)) > 0]),
        },
        "sections": {
            "events_by_source_24h": by_source,
            "source_errors_24h": errors,
            "bar_freshness": bar_freshness,
        },
    }


def _handler_session_alignment(cur: psycopg.Cursor) -> dict[str, Any]:
    summary = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS total_events_7d,
               COUNT(*) FILTER (WHERE available_at_ts < published_ts)::int AS temporal_violations,
               COUNT(*) FILTER (WHERE session_relation IS NULL OR session_relation = '')::int AS missing_session_relation
        FROM equities_events
        WHERE published_ts >= NOW() - INTERVAL '7 days';
        """,
    )
    session_mix = _fetch_rows(
        cur,
        """
        SELECT COALESCE(session_relation, '<null>') AS session_relation,
               COUNT(*)::int AS events_7d
        FROM equities_events
        WHERE published_ts >= NOW() - INTERVAL '7 days'
        GROUP BY 1
        ORDER BY events_7d DESC;
        """,
    )
    feature_summary = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS feature_rows_14d,
               COUNT(*) FILTER (WHERE available_at_ts < bar_start_ts)::int AS feature_prebar_violations
        FROM equities_feature_events
        WHERE created_at >= NOW() - INTERVAL '14 days';
        """,
    )
    return {
        "headline": "Session/alignment integrity checks completed.",
        "highlights": {
            "temporal_violations": int(summary.get("temporal_violations", 0) or 0),
            "feature_prebar_violations": int(feature_summary.get("feature_prebar_violations", 0) or 0),
        },
        "sections": {
            "event_temporal_summary": summary,
            "session_relation_mix": session_mix,
            "feature_alignment_summary": feature_summary,
        },
    }


def _handler_event_dedupe(cur: psycopg.Cursor) -> dict[str, Any]:
    summary = _fetch_one(
        cur,
        """
        WITH recent AS (
            SELECT dedupe_hash
            FROM equities_events
            WHERE published_ts >= NOW() - INTERVAL '14 days'
              AND dedupe_hash IS NOT NULL
        )
        SELECT COUNT(*)::int AS total_rows,
               COUNT(DISTINCT dedupe_hash)::int AS unique_hashes
        FROM recent;
        """,
    )
    duplicates = _fetch_rows(
        cur,
        """
        SELECT dedupe_hash,
               COUNT(*)::int AS dup_count
        FROM equities_events
        WHERE published_ts >= NOW() - INTERVAL '14 days'
          AND dedupe_hash IS NOT NULL
        GROUP BY dedupe_hash
        HAVING COUNT(*) > 1
        ORDER BY dup_count DESC
        LIMIT 12;
        """,
    )
    total = int(summary.get("total_rows", 0) or 0)
    unique = int(summary.get("unique_hashes", 0) or 0)
    duplicate_ratio = 0.0 if total <= 0 else max(0.0, (total - unique) / total)
    return {
        "headline": "Duplicate-family concentration measured.",
        "highlights": {
            "duplicate_ratio": round(duplicate_ratio, 4),
            "duplicate_families": len(duplicates),
        },
        "sections": {
            "dedupe_summary": summary,
            "top_duplicate_hashes": duplicates,
        },
    }


def _handler_anti_bot(cur: psycopg.Cursor) -> dict[str, Any]:
    summary = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS scored_events_7d,
               COUNT(*) FILTER (WHERE anti_bot_score >= 0.8)::int AS high_risk_events_7d,
               ROUND(AVG(anti_bot_score)::numeric, 4) AS avg_anti_bot_score
        FROM equities_events
        WHERE published_ts >= NOW() - INTERVAL '7 days'
          AND anti_bot_score IS NOT NULL;
        """,
    )
    by_source = _fetch_rows(
        cur,
        """
        SELECT source,
               COUNT(*)::int AS scored_events_7d,
               ROUND(AVG(anti_bot_score)::numeric, 4) AS avg_score,
               ROUND(
                 PERCENTILE_CONT(0.95)
                 WITHIN GROUP (ORDER BY anti_bot_score)::numeric,
                 4
               ) AS p95_score
        FROM equities_events
        WHERE published_ts >= NOW() - INTERVAL '7 days'
          AND anti_bot_score IS NOT NULL
        GROUP BY source
        ORDER BY scored_events_7d DESC;
        """,
    )
    return {
        "headline": "Anti-bot risk distribution computed.",
        "highlights": {
            "high_risk_events_7d": int(summary.get("high_risk_events_7d", 0) or 0),
            "sources_profiled": len(by_source),
        },
        "sections": {
            "anti_bot_summary": summary,
            "anti_bot_by_source": by_source,
        },
    }


def _handler_regime_stability(cur: psycopg.Cursor) -> dict[str, Any]:
    switch_rates = _fetch_rows(
        cur,
        """
        WITH ordered AS (
            SELECT symbol,
                   timeframe,
                   horizon,
                   bar_start_ts,
                   regime_label,
                   LAG(regime_label) OVER (
                       PARTITION BY symbol, timeframe, horizon
                       ORDER BY bar_start_ts
                   ) AS prev_regime
            FROM equities_predictions
            WHERE created_at >= NOW() - INTERVAL '21 days'
              AND regime_label IS NOT NULL
        )
        SELECT timeframe,
               horizon,
               COUNT(*) FILTER (WHERE prev_regime IS NOT NULL)::int AS comparable_rows,
               COUNT(*) FILTER (WHERE prev_regime IS NOT NULL AND regime_label <> prev_regime)::int AS switches,
               ROUND(
                 COALESCE(
                   COUNT(*) FILTER (WHERE prev_regime IS NOT NULL AND regime_label <> prev_regime)::numeric
                   / NULLIF(COUNT(*) FILTER (WHERE prev_regime IS NOT NULL), 0),
                   0
                 ),
                 4
               ) AS switch_rate
        FROM ordered
        GROUP BY timeframe, horizon
        ORDER BY switch_rate DESC, comparable_rows DESC;
        """,
    )
    transitions = _fetch_rows(
        cur,
        """
        WITH ordered AS (
            SELECT symbol,
                   timeframe,
                   horizon,
                   bar_start_ts,
                   regime_label,
                   LAG(regime_label) OVER (
                       PARTITION BY symbol, timeframe, horizon
                       ORDER BY bar_start_ts
                   ) AS prev_regime
            FROM equities_predictions
            WHERE created_at >= NOW() - INTERVAL '21 days'
              AND regime_label IS NOT NULL
        )
        SELECT COALESCE(prev_regime, '<null>') AS prev_regime,
               regime_label,
               COUNT(*)::int AS transition_count
        FROM ordered
        WHERE prev_regime IS NOT NULL
        GROUP BY 1, 2
        ORDER BY transition_count DESC
        LIMIT 12;
        """,
    )
    max_switch = max((float(r.get("switch_rate") or 0.0) for r in switch_rates), default=0.0)
    return {
        "headline": "Regime-label churn profile generated.",
        "highlights": {
            "max_switch_rate": round(max_switch, 4),
            "timeframe_horizon_groups": len(switch_rates),
        },
        "sections": {
            "switch_rates": switch_rates,
            "top_transitions": transitions,
        },
    }


def _handler_leakage_guard(cur: psycopg.Cursor) -> dict[str, Any]:
    event_temporal = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS total_events_14d,
               COUNT(*) FILTER (WHERE available_at_ts < published_ts)::int AS event_temporal_violations
        FROM equities_events
        WHERE published_ts >= NOW() - INTERVAL '14 days';
        """,
    )
    pred_temporal = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS total_predictions_14d,
               COUNT(*) FILTER (WHERE available_at_ts < bar_start_ts)::int AS prediction_prebar_violations
        FROM equities_predictions
        WHERE created_at >= NOW() - INTERVAL '14 days';
        """,
    )
    opp_temporal = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS total_opportunities_14d,
               COUNT(*) FILTER (WHERE available_at_ts < bar_start_ts)::int AS opportunity_prebar_violations,
               COUNT(*) FILTER (WHERE prediction_id IS NULL)::int AS missing_prediction_reference
        FROM equities_opportunities
        WHERE created_at >= NOW() - INTERVAL '14 days';
        """,
    )
    opp_vs_pred = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS joined_rows,
               COUNT(*) FILTER (WHERE o.available_at_ts < p.available_at_ts)::int AS opp_before_prediction_available
        FROM equities_opportunities o
        JOIN equities_predictions p ON p.id = o.prediction_id
        WHERE o.created_at >= NOW() - INTERVAL '14 days';
        """,
    )
    return {
        "headline": "Temporal no-lookahead leakage checks executed.",
        "highlights": {
            "event_temporal_violations": int(event_temporal.get("event_temporal_violations", 0) or 0),
            "prediction_prebar_violations": int(pred_temporal.get("prediction_prebar_violations", 0) or 0),
            "opp_before_prediction_available": int(opp_vs_pred.get("opp_before_prediction_available", 0) or 0),
        },
        "sections": {
            "event_temporal": event_temporal,
            "prediction_temporal": pred_temporal,
            "opportunity_temporal": opp_temporal,
            "opportunity_vs_prediction": opp_vs_pred,
        },
    }


def _handler_explainability(cur: psycopg.Cursor) -> dict[str, Any]:
    completeness = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS total_opportunities_7d,
               COUNT(*) FILTER (WHERE rationale ? 'top_features')::int AS with_top_features,
               COUNT(*) FILTER (WHERE provenance ? 'event_ids')::int AS with_event_ids,
               COUNT(*) FILTER (WHERE provenance ? 'model_version')::int AS with_model_version,
               COUNT(*) FILTER (WHERE provenance ? 'selection_rule')::int AS with_selection_rule
        FROM equities_opportunities
        WHERE created_at >= NOW() - INTERVAL '7 days';
        """,
    )
    missing = _fetch_rows(
        cur,
        """
        SELECT id,
               symbol,
               timeframe,
               horizon,
               created_at
        FROM equities_opportunities
        WHERE created_at >= NOW() - INTERVAL '7 days'
          AND (
            NOT (rationale ? 'top_features')
            OR NOT (provenance ? 'event_ids')
            OR NOT (provenance ? 'model_version')
            OR NOT (provenance ? 'selection_rule')
          )
        ORDER BY created_at DESC
        LIMIT 12;
        """,
    )
    total = int(completeness.get("total_opportunities_7d", 0) or 0)
    with_top = int(completeness.get("with_top_features", 0) or 0)
    return {
        "headline": "Opportunity explanation completeness audited.",
        "highlights": {
            "top_feature_coverage": round((with_top / total), 4) if total else 0.0,
            "missing_examples": len(missing),
        },
        "sections": {
            "completeness": completeness,
            "missing_examples": missing,
        },
    }


def _handler_model_drift(cur: psycopg.Cursor) -> dict[str, Any]:
    drift = _fetch_rows(
        cur,
        """
        WITH ranked AS (
            SELECT timeframe,
                   horizon,
                   split_type,
                   model_version,
                   created_at,
                   (metrics->>'rmse')::double precision AS rmse,
                   (metrics->>'accuracy')::double precision AS accuracy,
                   ROW_NUMBER() OVER (
                       PARTITION BY timeframe, horizon, split_type
                       ORDER BY created_at DESC
                   ) AS rn
            FROM equities_model_evaluations
            WHERE created_at >= NOW() - INTERVAL '45 days'
        )
        SELECT r1.timeframe,
               r1.horizon,
               r1.split_type,
               r1.model_version AS latest_model,
               r2.model_version AS previous_model,
               r1.created_at AS latest_created_at,
               r2.created_at AS previous_created_at,
               r1.rmse AS latest_rmse,
               r2.rmse AS previous_rmse,
               (r1.rmse - r2.rmse) AS rmse_delta,
               r1.accuracy AS latest_accuracy,
               r2.accuracy AS previous_accuracy,
               (r1.accuracy - r2.accuracy) AS accuracy_delta
        FROM ranked r1
        LEFT JOIN ranked r2
          ON r1.timeframe = r2.timeframe
         AND r1.horizon = r2.horizon
         AND r1.split_type = r2.split_type
         AND r2.rn = 2
        WHERE r1.rn = 1
        ORDER BY ABS(COALESCE(r1.rmse - r2.rmse, 0)) DESC NULLS LAST
        LIMIT 20;
        """,
    )
    eval_count = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS evaluations_30d
        FROM equities_model_evaluations
        WHERE created_at >= NOW() - INTERVAL '30 days';
        """,
    )
    max_abs_rmse_delta = max((abs(float(r.get("rmse_delta") or 0.0)) for r in drift), default=0.0)
    return {
        "headline": "Model-evaluation drift deltas computed.",
        "highlights": {
            "evaluations_30d": int(eval_count.get("evaluations_30d", 0) or 0),
            "max_abs_rmse_delta": round(max_abs_rmse_delta, 6),
        },
        "sections": {
            "latest_vs_previous": drift,
            "evaluation_count": eval_count,
        },
    }


def _handler_source_expansion(cur: psycopg.Cursor) -> dict[str, Any]:
    source_mix = _fetch_rows(
        cur,
        """
        SELECT source,
               COUNT(*)::int AS events_7d
        FROM equities_events
        WHERE published_ts >= NOW() - INTERVAL '7 days'
        GROUP BY source
        ORDER BY events_7d DESC;
        """,
    )
    gaps = _fetch_rows(
        cur,
        """
        WITH recent AS (
            SELECT symbol,
                   source,
                   COUNT(*)::int AS n
            FROM equities_events
            WHERE published_ts >= NOW() - INTERVAL '7 days'
            GROUP BY symbol, source
        ),
        agg AS (
            SELECT symbol,
                   COUNT(*)::int AS source_count,
                   SUM(n)::int AS total_events
            FROM recent
            GROUP BY symbol
        )
        SELECT symbol,
               source_count,
               total_events
        FROM agg
        ORDER BY source_count ASC, total_events DESC
        LIMIT 20;
        """,
    )
    cache_freshness = _fetch_rows(
        cur,
        """
        SELECT source,
               COUNT(*)::int AS cache_entries,
               EXTRACT(EPOCH FROM (NOW() - MAX(fetched_at)))::int AS latest_fetch_age_s
        FROM equities_source_cache
        GROUP BY source
        ORDER BY cache_entries DESC;
        """,
    )
    return {
        "headline": "Cross-source coverage and freshness sweep completed.",
        "highlights": {
            "active_sources_7d": len(source_mix),
            "symbols_with_thin_source_coverage": len([r for r in gaps if int(r.get("source_count", 0)) < 2]),
        },
        "sections": {
            "source_mix_7d": source_mix,
            "lowest_coverage_symbols": gaps,
            "source_cache_freshness": cache_freshness,
        },
    }


def _handler_backfill_quality(cur: psycopg.Cursor) -> dict[str, Any]:
    summary = _fetch_one(
        cur,
        """
        SELECT COUNT(*)::int AS active_backfills,
               COUNT(*) FILTER (
                 WHERE NOT (tags ? 'parent_task_id')
                   AND GREATEST(
                     COALESCE((tags ->> 'slice_total')::int, 0),
                     COALESCE((payload -> 'slice' ->> 'total')::int, 0)
                   ) < 5
               )::int AS unsplit_active_backfills
        FROM task_board_tasks
        WHERE task_type = 'BACKFILL'
          AND status IN ('OPEN', 'CLAIMED', 'IN_PROGRESS', 'BLOCKED');
        """,
    )
    groups = _fetch_rows(
        cur,
        """
        SELECT COALESCE(tags->>'slice_group', '<none>') AS slice_group,
               COUNT(*)::int AS slices,
               MIN(COALESCE((tags->>'slice_index')::int, -1)) AS min_slice_idx,
               MAX(COALESCE((tags->>'slice_index')::int, -1)) AS max_slice_idx,
               MIN(status) AS sample_status
        FROM task_board_tasks
        WHERE task_type = 'BACKFILL'
          AND status IN ('OPEN', 'CLAIMED', 'IN_PROGRESS', 'BLOCKED')
        GROUP BY 1
        ORDER BY slices ASC, slice_group ASC
        LIMIT 20;
        """,
    )
    return {
        "headline": "Backfill shard compliance audited.",
        "highlights": {
            "unsplit_active_backfills": int(summary.get("unsplit_active_backfills", 0) or 0),
            "slice_groups_sampled": len(groups),
        },
        "sections": {
            "backfill_summary": summary,
            "slice_groups": groups,
        },
    }


def _handler_conf_followup(cur: psycopg.Cursor, task_payload: dict[str, Any]) -> dict[str, Any]:
    source_conf = str(task_payload.get("source_conf_task_id") or "")
    conf_replies = []
    if source_conf:
        conf_replies = _fetch_rows(
            cur,
            """
            SELECT actor,
                   ts,
                   LEFT(message, 240) AS message
            FROM task_board_events
            WHERE task_id = %s::uuid
              AND event_type = 'CONF_REPLY'
              AND ts >= NOW() - INTERVAL '24 hours'
            ORDER BY ts DESC
            LIMIT 20;
            """,
            (source_conf,),
        )
    board = _handler_generic(cur)
    return {
        "headline": "Conference follow-up synthesized into executable checkpoints.",
        "highlights": {
            "source_conf_task_id": source_conf or None,
            "conf_replies_sampled": len(conf_replies),
            "open_exec": board["sections"]["queue_snapshot"].get("open_exec", 0),
        },
        "sections": {
            "queue_snapshot": board["sections"]["queue_snapshot"],
            "recent_conf_replies": conf_replies,
        },
    }


def _handler_generic(cur: psycopg.Cursor) -> dict[str, Any]:
    queue_snapshot = _fetch_one(
        cur,
        """
        SELECT
          COUNT(*) FILTER (WHERE status = 'OPEN')::int AS open_total,
          COUNT(*) FILTER (WHERE status = 'IN_PROGRESS')::int AS in_progress_total,
          COUNT(*) FILTER (WHERE status = 'DONE')::int AS done_total,
          COUNT(*) FILTER (
            WHERE status = 'OPEN'
              AND task_type = 'EXEC'
          )::int AS open_exec,
          COUNT(*) FILTER (
            WHERE status = 'OPEN'
              AND task_type = 'EXEC'
              AND COALESCE(tags->>'program', '') = 'equities_forward_predictor'
          )::int AS open_exec_equities
        FROM task_board_tasks;
        """,
    )
    latest_pipeline = _fetch_one(
        cur,
        """
        SELECT MAX(ts) AS latest_pipeline_cycle_ts
        FROM equities_pipeline_events
        WHERE actor = 'S1_MAIN'
          AND event_type = 'EQUITIES_PIPELINE_CYCLE_COMPLETE';
        """,
    )
    return {
        "headline": "Queue and pipeline state summarized.",
        "highlights": {
            "open_exec": int(queue_snapshot.get("open_exec", 0) or 0),
            "open_exec_equities": int(queue_snapshot.get("open_exec_equities", 0) or 0),
        },
        "sections": {
            "queue_snapshot": queue_snapshot,
            "latest_pipeline_cycle": latest_pipeline,
        },
    }


HandlerFn = Callable[[psycopg.Cursor], dict[str, Any]]


def _select_handler(title: str, payload: dict[str, Any]) -> tuple[str, Callable[[psycopg.Cursor], dict[str, Any]]]:
    low = title.lower()
    if title.lower().startswith("conference follow-up"):
        return "conference_followup", lambda cur: _handler_conf_followup(cur, payload)
    if "source reliability" in low:
        return "source_reliability", _handler_source_reliability
    if "session alignment" in low:
        return "session_alignment", _handler_session_alignment
    if "dedupe" in low:
        return "event_dedupe", _handler_event_dedupe
    if "anti-bot" in low or "anti bot" in low:
        return "anti_bot", _handler_anti_bot
    if "regime" in low:
        return "regime_stability", _handler_regime_stability
    if "leakage" in low:
        return "leakage_guard", _handler_leakage_guard
    if "opportunity explanation" in low:
        return "opportunity_explainability", _handler_explainability
    if "ablation" in low:
        return "model_drift", _handler_model_drift
    if "coverage expansion" in low or "free-source coverage" in low:
        return "source_expansion", _handler_source_expansion
    if "backfill" in low:
        return "backfill_quality", _handler_backfill_quality
    return "generic", _handler_generic


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute concrete equities task-board work")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--task-title", default="")
    parser.add_argument("--task-type", default="EXEC")
    parser.add_argument("--actor", default="TASK_WORKER")
    parser.add_argument("--program", default="equities_forward_predictor")
    parser.add_argument("--track", default=None)
    parser.add_argument("--area", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        raise RuntimeError("DATABASE_URL (or --database-url) is required")

    started = time.time()
    task_payload: dict[str, Any] = {}
    task_tags: dict[str, Any] = {}
    task_title = args.task_title
    task_type = args.task_type

    with _connect(args.database_url) as conn:
        with conn.cursor() as cur:
            if args.task_id:
                cur.execute(
                    """
                    SELECT title, task_type, payload, tags
                    FROM task_board_tasks
                    WHERE id = %s::uuid;
                    """,
                    (args.task_id,),
                )
                row = cur.fetchone()
                if row:
                    task_title = str(row.get("title") or task_title)
                    task_type = str(row.get("task_type") or task_type)
                    task_payload = _to_plain(dict(row.get("payload") or {}))
                    task_tags = _to_plain(dict(row.get("tags") or {}))

            handler_name, handler = _select_handler(task_title, task_payload)
            analysis = handler(cur)

    artifact_dir = Path(__file__).resolve().parent / ".runtime" / "task_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_utc().strftime("%Y%m%dT%H%M%SZ")
    ident = args.task_id or f"{_slug(task_title)}-{stamp}"
    artifact_path = artifact_dir / f"{ident}.json"

    report = {
        "generated_at": _now_utc().isoformat(),
        "duration_s": round(time.time() - started, 3),
        "task": {
            "task_id": args.task_id,
            "task_title": task_title,
            "task_type": task_type,
            "actor": args.actor,
            "program": args.program,
            "track": args.track or task_tags.get("track"),
            "area": args.area or task_tags.get("area"),
            "tags": task_tags,
        },
        "handler": handler_name,
        "analysis": analysis,
    }

    artifact_path.write_text(json.dumps(_to_plain(report), default=_json_default, indent=2), encoding="utf-8")

    summary = {
        "mode": "real_analysis",
        "handler": handler_name,
        "task_id": args.task_id,
        "task_title": task_title,
        "duration_s": report["duration_s"],
        "artifact_path": str(artifact_path),
        "headline": analysis.get("headline"),
        "highlights": analysis.get("highlights", {}),
    }
    print(json.dumps(_to_plain(summary), default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
