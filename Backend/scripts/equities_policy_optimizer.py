#!/usr/bin/env python3
"""Learn horizon-specific selection policy from settled equities outcomes."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np

from equities_predictor.db import (
    connect,
    insert_calibration_alert,
    insert_selection_policy,
    list_settled_outcomes,
    log_pipeline_event,
)
from equities_predictor.ids import deterministic_uuid, stable_hash


def _clamp(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


def _default_expected_return_floor(timeframe: str, horizon: str) -> float:
    floor = 0.0008 if timeframe == "1h" else 0.0015
    if horizon == "gap_open":
        floor *= 1.2
    elif horizon == "5d":
        floor *= 0.9
    return float(floor)


def _metrics(rows: list[dict[str, Any]], *, transaction_cost_bps: float) -> dict[str, float]:
    if not rows:
        return {
            "count": 0.0,
            "hit_rate": 0.0,
            "avg_directional_return": 0.0,
            "avg_net_directional_return": 0.0,
            "avg_brier_loss": 0.0,
        }
    cost = float(transaction_cost_bps) / 10000.0
    directional = np.array([float(r["directional_realized_return"]) for r in rows], dtype=float)
    hits = np.array([1.0 if bool(r["hit"]) else 0.0 for r in rows], dtype=float)
    brier = np.array([float(r["brier_loss"]) for r in rows], dtype=float)
    return {
        "count": float(len(rows)),
        "hit_rate": float(np.mean(hits)),
        "avg_directional_return": float(np.mean(directional)),
        "avg_net_directional_return": float(np.mean(directional - cost)),
        "avg_brier_loss": float(np.mean(brier)),
    }


def _objective(metrics: dict[str, float]) -> float:
    if metrics["count"] <= 0:
        return -1e9
    # Emphasize net directional return, keep calibration and hit-rate in score.
    return float(
        metrics["avg_net_directional_return"]
        + 0.002 * (metrics["hit_rate"] - 0.5)
        - 0.001 * max(0.0, metrics["avg_brier_loss"] - 0.25)
    )


def _risk_off_thresholds(rows: list[dict[str, Any]], *, timeframe: str, horizon: str) -> dict[str, float]:
    conf = np.array([float(r.get("confidence") or 0.0) for r in rows], dtype=float)
    edge = np.array([float(r.get("edge_score") or 0.0) for r in rows], dtype=float)
    abs_dir = np.array([abs(float(r["directional_realized_return"])) for r in rows], dtype=float)
    return {
        "min_confidence": _clamp(float(np.percentile(conf, 75)) if len(conf) else 0.70, 0.55, 0.95),
        "min_edge_score": max(
            0.0015,
            float(np.percentile(edge, 65)) if len(edge) else 0.0015,
        ),
        "directional_prob_floor": 0.62,
        "expected_return_floor": max(
            _default_expected_return_floor(timeframe, horizon),
            float(np.percentile(abs_dir, 60)) if len(abs_dir) else 0.0,
        ),
    }


def _optimize_policy_for_rows(
    rows: list[dict[str, Any]],
    *,
    timeframe: str,
    horizon: str,
    transaction_cost_bps: float = 6.0,
    min_samples: int = 20,
) -> dict[str, Any] | None:
    if len(rows) < min_samples:
        return None

    conf_grid = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
    edge_grid = [0.0, 0.0002, 0.0004, 0.0006, 0.0008, 0.0012, 0.0016, 0.0020, 0.0030]

    baseline_metrics = _metrics(rows, transaction_cost_bps=transaction_cost_bps)
    best = {
        "min_confidence": 0.10,
        "min_edge_score": 0.0,
        "metrics": baseline_metrics,
        "objective_score": _objective(baseline_metrics),
    }

    for conf in conf_grid:
        for edge in edge_grid:
            subset = [
                r for r in rows
                if float(r.get("confidence") or 0.0) >= conf
                and float(r.get("edge_score") or 0.0) >= edge
            ]
            if len(subset) < min_samples:
                continue
            m = _metrics(subset, transaction_cost_bps=transaction_cost_bps)
            score = _objective(m)
            if score > best["objective_score"]:
                best = {
                    "min_confidence": float(conf),
                    "min_edge_score": float(edge),
                    "metrics": m,
                    "objective_score": float(score),
                }

    risk_mode = "normal"
    if best["objective_score"] < -0.0010:
        risk = _risk_off_thresholds(rows, timeframe=timeframe, horizon=horizon)
        subset_risk = [
            r for r in rows
            if float(r.get("confidence") or 0.0) >= risk["min_confidence"]
            and float(r.get("edge_score") or 0.0) >= risk["min_edge_score"]
        ]
        # If risk-off leaves too few samples, keep objective-best thresholds and tighten only prob floor.
        if len(subset_risk) >= max(10, min_samples // 2):
            risk_mode = "risk_off"
            m_risk = _metrics(subset_risk, transaction_cost_bps=transaction_cost_bps)
            best = {
                "min_confidence": risk["min_confidence"],
                "min_edge_score": risk["min_edge_score"],
                "metrics": m_risk,
                "objective_score": float(_objective(m_risk)),
                "directional_prob_floor_override": risk["directional_prob_floor"],
                "expected_return_floor_override": risk["expected_return_floor"],
            }
        else:
            risk_mode = "risk_guarded"

    selected = [
        r for r in rows
        if float(r.get("confidence") or 0.0) >= float(best["min_confidence"])
        and float(r.get("edge_score") or 0.0) >= float(best["min_edge_score"])
    ]
    if len(selected) < min_samples:
        selected = rows

    selected_conf = np.array([float(r.get("confidence") or 0.0) for r in selected], dtype=float)
    selected_abs_ret = np.array([abs(float(r["directional_realized_return"])) for r in selected], dtype=float)
    directional_prob_floor = float(best.get("directional_prob_floor_override") or (0.53 if best["metrics"]["hit_rate"] >= 0.50 else 0.56))
    expected_floor = max(
        float(best.get("expected_return_floor_override") or 0.0),
        _default_expected_return_floor(timeframe, horizon),
        float(np.percentile(selected_abs_ret, 35)) if len(selected_abs_ret) else 0.0,
    )

    return {
        "timeframe": timeframe,
        "horizon": horizon,
        "min_confidence": _clamp(float(best["min_confidence"]), 0.05, 0.95),
        "min_edge_score": max(0.0, float(best["min_edge_score"])),
        "directional_prob_floor": _clamp(directional_prob_floor, 0.50, 0.75),
        "expected_return_floor": max(0.0, float(expected_floor)),
        "min_samples": int(min_samples),
        "transaction_cost_bps": float(transaction_cost_bps),
        "objective_score": float(best["objective_score"]),
        "risk_mode": risk_mode,
        "metrics": {
            "baseline": baseline_metrics,
            "selected": best["metrics"],
            "selected_confidence_quantiles": {
                "q25": float(np.percentile(selected_conf, 25)) if len(selected_conf) else 0.0,
                "q50": float(np.percentile(selected_conf, 50)) if len(selected_conf) else 0.0,
                "q75": float(np.percentile(selected_conf, 75)) if len(selected_conf) else 0.0,
            },
            "selected_count": float(len(selected)),
        },
    }


def run_optimizer(
    *,
    database_url: str | None,
    actor: str,
    lookback_days: int,
    min_samples: int,
    transaction_cost_bps: float,
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
                event_type="POLICY_OPTIMIZER_NOOP",
                message="No settled outcomes available for policy optimizer",
                data={"lookback_days": lookback_days},
            )
            conn.commit()
            return {"policies_written": 0, "groups": 0}

        by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in outcomes:
            by_group[(row["timeframe"], row["horizon"])].append(row)

        written = 0
        group_count = 0
        summaries: dict[str, Any] = {}
        for (timeframe, horizon), rows in sorted(by_group.items()):
            group_count += 1
            policy = _optimize_policy_for_rows(
                rows,
                timeframe=timeframe,
                horizon=horizon,
                transaction_cost_bps=transaction_cost_bps,
                min_samples=min_samples,
            )
            if policy is None:
                continue

            pol_id = deterministic_uuid(
                "equities_selection_policy",
                actor,
                timeframe,
                horizon,
                lookback_days,
                round(policy["min_confidence"], 4),
                round(policy["min_edge_score"], 6),
                round(policy["directional_prob_floor"], 4),
                round(policy["expected_return_floor"], 6),
                round(policy["objective_score"], 8),
                int(policy["metrics"]["selected"]["count"]),
            )
            row = {
                "id": pol_id,
                "actor": actor,
                "timeframe": timeframe,
                "horizon": horizon,
                "min_confidence": policy["min_confidence"],
                "min_edge_score": policy["min_edge_score"],
                "directional_prob_floor": policy["directional_prob_floor"],
                "expected_return_floor": policy["expected_return_floor"],
                "min_samples": policy["min_samples"],
                "transaction_cost_bps": policy["transaction_cost_bps"],
                "objective_score": policy["objective_score"],
                "metrics": policy["metrics"],
                "notes": "derived from settled outcomes",
                "deterministic_hash": stable_hash(
                    timeframe,
                    horizon,
                    policy["min_confidence"],
                    policy["min_edge_score"],
                    policy["directional_prob_floor"],
                    policy["expected_return_floor"],
                    policy["objective_score"],
                    policy["metrics"]["selected"]["count"],
                ),
            }
            insert_selection_policy(conn, row)
            written += 1
            summaries[f"{timeframe}:{horizon}"] = {
                "min_confidence": policy["min_confidence"],
                "min_edge_score": policy["min_edge_score"],
                "directional_prob_floor": policy["directional_prob_floor"],
                "expected_return_floor": policy["expected_return_floor"],
                "objective_score": policy["objective_score"],
                "selected_count": policy["metrics"]["selected"]["count"],
                "risk_mode": policy["risk_mode"],
            }

            if float(policy["objective_score"]) < 0.0:
                severity = "HIGH" if float(policy["objective_score"]) < -0.0020 else "MEDIUM"
                alert_id = deterministic_uuid(
                    "equities_calibration_alert_policy",
                    timeframe,
                    horizon,
                    severity,
                    lookback_days,
                    round(float(policy["objective_score"]), 8),
                    int(policy["metrics"]["selected"]["count"]),
                )
                insert_calibration_alert(
                    conn,
                    {
                        "id": alert_id,
                        "actor": actor,
                        "timeframe": timeframe,
                        "horizon": horizon,
                        "severity": severity,
                        "alert_type": "policy_objective_negative",
                        "message": (
                            f"Policy objective negative for {timeframe}:{horizon} "
                            f"(score={policy['objective_score']:.6f}, risk_mode={policy['risk_mode']})"
                        ),
                        "data": {
                            "policy": summaries[f"{timeframe}:{horizon}"],
                            "selected_metrics": policy["metrics"]["selected"],
                            "baseline_metrics": policy["metrics"]["baseline"],
                        },
                        "deterministic_hash": stable_hash(
                            timeframe,
                            horizon,
                            severity,
                            round(float(policy["objective_score"]), 8),
                            policy["risk_mode"],
                            policy["min_confidence"],
                            policy["min_edge_score"],
                        ),
                    },
                )

        log_pipeline_event(
            conn,
            actor=actor,
            event_type="POLICY_OPTIMIZER_COMPLETE",
            message="Equities selection policy optimization complete",
            data={
                "lookback_days": lookback_days,
                "group_count": group_count,
                "policies_written": written,
                "transaction_cost_bps": transaction_cost_bps,
                "summaries": summaries,
            },
        )
        conn.commit()
        return {"policies_written": written, "groups": group_count, "summaries": summaries}
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize equities selection policy from settled outcomes")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--transaction-cost-bps", type=float, default=6.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_optimizer(
        database_url=args.database_url,
        actor=args.actor,
        lookback_days=max(30, int(args.lookback_days)),
        min_samples=max(10, int(args.min_samples)),
        transaction_cost_bps=max(0.0, float(args.transaction_cost_bps)),
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
