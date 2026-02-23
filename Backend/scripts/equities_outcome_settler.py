#!/usr/bin/env python3
"""Settle matured equities opportunities with realized outcomes and calibration metrics."""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from equities_predictor.calendar import ET
from equities_predictor.db import (
    connect,
    insert_opportunity_outcomes,
    list_settleable_opportunities,
    load_ohlcv,
    log_pipeline_event,
    mark_opportunities_settled,
)
from equities_predictor.ids import deterministic_uuid, stable_hash


def _horizon_steps(timeframe: str, horizon: str) -> int:
    if timeframe == "1d":
        mapping = {"1d": 1, "3d": 3, "5d": 5}
    else:
        mapping = {"1d": 24, "3d": 72, "5d": 120}
    return mapping[horizon]


def _build_bar_map(bars: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in bars:
        out.setdefault(row["symbol"], []).append(row)
    for sym in out:
        out[sym].sort(key=lambda x: x["bar_start_ts"])
    return out


def _next_rth_open(rows: list[dict[str, Any]], idx: int) -> tuple[datetime, float] | None:
    for j in range(idx + 1, len(rows)):
        ts_et = rows[j]["bar_start_ts"].astimezone(ET)
        if ts_et.hour == 9 and ts_et.minute == 30:
            return rows[j]["bar_start_ts"], float(rows[j]["open"])
    return None


def _compute_realized_return(
    *,
    series: list[dict[str, Any]],
    bar_start_ts: datetime,
    timeframe: str,
    horizon: str,
) -> dict[str, Any] | None:
    idx = next((i for i, b in enumerate(series) if b["bar_start_ts"] == bar_start_ts), None)
    if idx is None:
        return None

    entry = float(series[idx]["close"])
    if entry <= 0:
        return None

    if horizon == "gap_open":
        target = _next_rth_open(series, idx)
        if target is None:
            return None
        settle_bar_ts, exit_price = target
        target_kind = "next_rth_open"
    else:
        steps = _horizon_steps(timeframe, horizon)
        j = idx + steps
        if j >= len(series):
            return None
        settle_bar_ts = series[j]["bar_start_ts"]
        exit_price = float(series[j]["close"])
        target_kind = "close_step"

    realized = (exit_price - entry) / entry
    return {
        "settle_bar_ts": settle_bar_ts,
        "entry_price": entry,
        "exit_price": exit_price,
        "target_kind": target_kind,
        "realized_return": float(realized),
    }


def _clamp_prob(p: float) -> float:
    return float(max(1e-6, min(1.0 - 1e-6, p)))


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    by_horizon: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_horizon[row["horizon"]].append(row)

    horizon_metrics: dict[str, dict[str, float]] = {}
    for horizon, items in by_horizon.items():
        directional = np.array([float(x["directional_realized_return"]) for x in items], dtype=float)
        hits = np.array([1.0 if bool(x["hit"]) else 0.0 for x in items], dtype=float)
        brier = np.array([float(x["brier_loss"]) for x in items], dtype=float)
        horizon_metrics[horizon] = {
            "count": float(len(items)),
            "hit_rate": float(np.mean(hits)),
            "avg_directional_return": float(np.mean(directional)),
            "avg_brier_loss": float(np.mean(brier)),
        }

    return {
        "count": len(rows),
        "avg_directional_return": float(np.mean([float(x["directional_realized_return"]) for x in rows])),
        "avg_brier_loss": float(np.mean([float(x["brier_loss"]) for x in rows])),
        "by_horizon": horizon_metrics,
    }


def run_settlement(
    *,
    database_url: str | None,
    actor: str,
    as_of_ts: datetime | None = None,
    limit: int = 3000,
) -> dict[str, Any]:
    as_of_ts = as_of_ts or datetime.now(timezone.utc)
    conn = connect(database_url)
    try:
        candidates = list_settleable_opportunities(
            conn,
            as_of_ts=as_of_ts,
            statuses=["active"],
            limit=limit,
        )
        if not candidates:
            log_pipeline_event(
                conn,
                actor=actor,
                event_type="OUTCOME_SETTLEMENT_NOOP",
                message="No settleable equities opportunities found",
                data={"as_of_ts": as_of_ts.isoformat()},
            )
            conn.commit()
            return {"candidates": 0, "settled": 0}

        bars_by_tf_symbol: dict[str, dict[str, list[dict[str, Any]]]] = {}
        by_timeframe: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in candidates:
            by_timeframe[row["timeframe"]].append(row)

        for timeframe, items in by_timeframe.items():
            symbols = sorted({str(x["symbol"]) for x in items})
            start_ts = min(x["bar_start_ts"] for x in items) - timedelta(days=10)
            bars = load_ohlcv(
                conn,
                symbols=symbols,
                timeframe=timeframe,
                start_ts=start_ts,
                end_ts=as_of_ts + timedelta(days=2),
                sources=["equities_yahoo", "equities_stooq"],
            )
            bars_by_tf_symbol[timeframe] = _build_bar_map(bars)

        outcome_rows: list[dict[str, Any]] = []
        settle_ids: list[str] = []
        unresolved = 0
        for row in candidates:
            series = bars_by_tf_symbol.get(row["timeframe"], {}).get(row["symbol"], [])
            if not series:
                unresolved += 1
                continue

            realized = _compute_realized_return(
                series=series,
                bar_start_ts=row["bar_start_ts"],
                timeframe=row["timeframe"],
                horizon=row["horizon"],
            )
            if realized is None:
                unresolved += 1
                continue

            rationale = row.get("rationale") or {}
            direction = str(rationale.get("direction") or ("LONG" if float(row.get("predicted_prob_up") or 0.5) >= 0.5 else "SHORT")).upper()
            predicted_prob_up = float(row.get("predicted_prob_up") if row.get("predicted_prob_up") is not None else row.get("probability_up") or 0.5)
            directional_prob = predicted_prob_up if direction == "LONG" else (1.0 - predicted_prob_up)
            directional_prob = _clamp_prob(directional_prob)

            realized_return = float(realized["realized_return"])
            directional_realized = realized_return if direction == "LONG" else -realized_return
            hit = directional_realized > 0.0
            label = 1.0 if hit else 0.0
            brier = float((directional_prob - label) ** 2)
            log_loss = float(-(label * math.log(directional_prob) + (1.0 - label) * math.log(1.0 - directional_prob)))

            outcome_id = deterministic_uuid("equities_outcome", row["opportunity_id"])
            outcome_rows.append(
                {
                    "id": outcome_id,
                    "opportunity_id": row["opportunity_id"],
                    "prediction_id": row["prediction_id"],
                    "symbol": row["symbol"],
                    "timeframe": row["timeframe"],
                    "horizon": row["horizon"],
                    "direction": direction,
                    "regime_label": row.get("regime_label"),
                    "available_at_ts": row["available_at_ts"],
                    "settle_bar_ts": realized["settle_bar_ts"],
                    "expected_return": row.get("expected_return"),
                    "predicted_prob_up": predicted_prob_up,
                    "confidence": row.get("confidence"),
                    "edge_score": float((rationale or {}).get("edge_score") or 0.0),
                    "realized_return": realized_return,
                    "directional_realized_return": float(directional_realized),
                    "hit": hit,
                    "brier_loss": brier,
                    "log_loss": log_loss,
                    "outcome_payload": {
                        "entry_price": realized["entry_price"],
                        "exit_price": realized["exit_price"],
                        "target_kind": realized["target_kind"],
                        "predicted_directional_prob": directional_prob,
                        "model_name": row.get("model_name"),
                        "model_version": row.get("model_version"),
                    },
                    "deterministic_hash": stable_hash(
                        row["opportunity_id"],
                        row["symbol"],
                        row["timeframe"],
                        row["horizon"],
                        direction,
                        round(realized_return, 10),
                        round(directional_prob, 10),
                    ),
                }
            )
            settle_ids.append(str(row["opportunity_id"]))

        inserted = insert_opportunity_outcomes(conn, outcome_rows)
        settled_updates = mark_opportunities_settled(conn, opportunity_ids=sorted(set(settle_ids)), status="settled")
        summary = _summarize(outcome_rows)

        log_pipeline_event(
            conn,
            actor=actor,
            event_type="OUTCOME_SETTLEMENT_COMPLETE",
            message="Equities opportunities settled",
            data={
                "as_of_ts": as_of_ts.isoformat(),
                "candidates": len(candidates),
                "computed": len(outcome_rows),
                "inserted": inserted,
                "settled_updates": settled_updates,
                "unresolved": unresolved,
                "summary": summary,
            },
        )
        conn.commit()
        return {
            "candidates": len(candidates),
            "computed": len(outcome_rows),
            "inserted": inserted,
            "settled_updates": settled_updates,
            "unresolved": unresolved,
            "summary": summary,
        }
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Settle realized outcomes for equities opportunities")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--limit", type=int, default=3000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_settlement(
        database_url=args.database_url,
        actor=args.actor,
        limit=max(100, int(args.limit)),
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
