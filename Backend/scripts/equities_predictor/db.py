"""DB access helpers for equities forward predictor."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row


def get_database_url(database_url: str | None = None) -> str:
    resolved = database_url or os.getenv("DATABASE_URL")
    if not resolved:
        raise RuntimeError("DATABASE_URL is not configured")
    return resolved


def connect(database_url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(get_database_url(database_url), row_factory=dict_row)


def _json(data: Any) -> str:
    return json.dumps(data if data is not None else {}, default=str)


def log_pipeline_event(
    conn: psycopg.Connection,
    *,
    actor: str,
    event_type: str,
    message: str,
    data: dict[str, Any] | None = None,
    mirror_task_board: bool = True,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO equities_pipeline_events (actor, event_type, message, data)
            VALUES (%s, %s, %s, %s::jsonb);
            """,
            (actor, event_type, message, _json(data)),
        )

        if mirror_task_board:
            cur.execute(
                """
                SELECT to_regclass('public.task_board_events') AS reg;
                """
            )
            has_task_board = cur.fetchone()["reg"] is not None
            if has_task_board:
                cur.execute(
                    """
                    INSERT INTO task_board_events (actor, event_type, message, data)
                    VALUES (%s, %s, %s, %s::jsonb);
                    """,
                    (actor, f"EQUITIES_{event_type}", message, _json(data)),
                )


def upsert_universe(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_symbol_registry (symbol, name, sector, is_etf, aliases, active)
            VALUES (%(symbol)s, %(name)s, %(sector)s, %(is_etf)s, %(aliases)s, TRUE)
            ON CONFLICT (symbol) DO UPDATE SET
                name = EXCLUDED.name,
                sector = EXCLUDED.sector,
                is_etf = EXCLUDED.is_etf,
                aliases = EXCLUDED.aliases,
                active = TRUE,
                updated_at = NOW();
            """,
            rows,
        )
    return len(rows)


def list_symbols(conn: psycopg.Connection, active_only: bool = True) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol
            FROM equities_symbol_registry
            WHERE (%s = FALSE OR active = TRUE)
            ORDER BY symbol ASC;
            """,
            (active_only,),
        )
        return [row["symbol"] for row in cur.fetchall()]


def update_symbol_cik(conn: psycopg.Connection, symbol_to_cik: dict[str, str]) -> int:
    if not symbol_to_cik:
        return 0

    rows = [{"symbol": k, "cik": v} for k, v in symbol_to_cik.items()]
    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE equities_symbol_registry
            SET cik = %(cik)s,
                updated_at = NOW()
            WHERE symbol = %(symbol)s;
            """,
            rows,
        )
    return len(rows)


def get_symbol_cik_map(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, cik
            FROM equities_symbol_registry
            WHERE cik IS NOT NULL
            ORDER BY symbol;
            """
        )
        return {row["symbol"]: row["cik"] for row in cur.fetchall()}


def upsert_ohlcv(
    conn: psycopg.Connection,
    rows: list[dict[str, Any]],
    *,
    source: str,
    batch_id: str,
) -> int:
    if not rows:
        return 0

    payload = []
    now = datetime.now(timezone.utc)
    for row in rows:
        payload.append(
            {
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "bar_start_ts": row["bar_start_ts"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "as_of_ts": row.get("as_of_ts", now),
                "is_final": bool(row.get("is_final", True)),
                "source": source,
                "batch_id": batch_id,
            }
        )

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ohlcv (
                symbol, timeframe, bar_start_ts,
                open, high, low, close, volume,
                as_of_ts, is_final, source, batch_id
            )
            VALUES (
                %(symbol)s, %(timeframe)s, %(bar_start_ts)s,
                %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s,
                %(as_of_ts)s, %(is_final)s, %(source)s, %(batch_id)s
            )
            ON CONFLICT (symbol, timeframe, bar_start_ts, source)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                as_of_ts = EXCLUDED.as_of_ts,
                is_final = EXCLUDED.is_final,
                batch_id = EXCLUDED.batch_id;
            """,
            payload,
        )
    return len(payload)


def upsert_bar_meta(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_bar_meta (
                symbol, timeframe, bar_start_ts, source,
                session_state, next_open_ts, last_close_ts,
                open_gap_pct, overnight_return
            )
            VALUES (
                %(symbol)s, %(timeframe)s, %(bar_start_ts)s, %(source)s,
                %(session_state)s, %(next_open_ts)s, %(last_close_ts)s,
                %(open_gap_pct)s, %(overnight_return)s
            )
            ON CONFLICT (symbol, timeframe, bar_start_ts, source)
            DO UPDATE SET
                session_state = EXCLUDED.session_state,
                next_open_ts = EXCLUDED.next_open_ts,
                last_close_ts = EXCLUDED.last_close_ts,
                open_gap_pct = EXCLUDED.open_gap_pct,
                overnight_return = EXCLUDED.overnight_return;
            """,
            rows,
        )
    return len(rows)


def load_ohlcv(
    conn: psycopg.Connection,
    *,
    symbols: list[str],
    timeframe: str,
    start_ts: datetime,
    end_ts: datetime,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, timeframe, bar_start_ts, open, high, low, close, volume, as_of_ts, is_final, source
            FROM (
                SELECT
                    symbol, timeframe, bar_start_ts, open, high, low, close, volume, as_of_ts, is_final, source,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol, timeframe, bar_start_ts
                        ORDER BY as_of_ts DESC, source ASC
                    ) AS rn
                FROM ohlcv
                WHERE symbol = ANY(%s)
                  AND timeframe = %s
                  AND bar_start_ts >= %s
                  AND bar_start_ts <= %s
                  AND is_final = TRUE
                  AND (%s::text[] IS NULL OR source = ANY(%s::text[]))
            ) q
            WHERE rn = 1
            ORDER BY symbol, bar_start_ts;
            """,
            (symbols, timeframe, start_ts, end_ts, sources, sources),
        )
        return list(cur.fetchall())


def load_events(
    conn: psycopg.Connection,
    *,
    symbols: list[str],
    start_ts: datetime,
    end_ts: datetime,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM equities_events
            WHERE symbol = ANY(%s)
              AND available_at_ts >= %s
              AND available_at_ts <= %s
            ORDER BY symbol, available_at_ts ASC, id ASC;
            """,
            (symbols, start_ts, end_ts),
        )
        return list(cur.fetchall())


def load_recent_events_for_symbol(
    conn: psycopg.Connection,
    *,
    symbol: str,
    lookback_start: datetime,
    as_of_ts: datetime,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM equities_events
            WHERE symbol = %s
              AND available_at_ts >= %s
              AND available_at_ts <= %s
            ORDER BY available_at_ts ASC, id ASC;
            """,
            (symbol, lookback_start, as_of_ts),
        )
        return list(cur.fetchall())


def find_recent_simhashes(
    conn: psycopg.Connection,
    *,
    symbol: str,
    source: str,
    lookback_hours: int = 24,
    limit: int = 200,
) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT simhash
            FROM equities_events
            WHERE symbol = %s
              AND source = %s
              AND simhash IS NOT NULL
              AND published_ts >= NOW() - make_interval(hours => %s)
            ORDER BY published_ts DESC
            LIMIT %s;
            """,
            (symbol, source, lookback_hours, limit),
        )
        return [int(row["simhash"]) for row in cur.fetchall()]


def insert_events(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_events (
                id, source, source_event_id, symbol,
                publisher, title, url,
                published_ts, available_at_ts,
                event_type, session_relation,
                sentiment_score, anti_bot_score,
                dedupe_hash, simhash, payload
            )
            VALUES (
                %(id)s, %(source)s, %(source_event_id)s, %(symbol)s,
                %(publisher)s, %(title)s, %(url)s,
                %(published_ts)s, %(available_at_ts)s,
                %(event_type)s, %(session_relation)s,
                %(sentiment_score)s, %(anti_bot_score)s,
                %(dedupe_hash)s, %(simhash)s, %(payload)s::jsonb
            )
            ON CONFLICT (source, source_event_id) DO NOTHING;
            """,
            [
                {
                    **row,
                    "payload": _json(row.get("payload") or {}),
                }
                for row in rows
            ],
        )
        inserted = cur.rowcount if cur.rowcount is not None else len(rows)
    return inserted


def insert_raw_mentions(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_raw_mentions (
                id, source, source_item_id, symbol,
                mention_ts, available_at_ts, session_relation,
                publisher, author, title, url, content,
                language, region, engagement_score,
                dedupe_hash, simhash, payload
            )
            VALUES (
                %(id)s, %(source)s, %(source_item_id)s, %(symbol)s,
                %(mention_ts)s, %(available_at_ts)s, %(session_relation)s,
                %(publisher)s, %(author)s, %(title)s, %(url)s, %(content)s,
                %(language)s, %(region)s, %(engagement_score)s,
                %(dedupe_hash)s, %(simhash)s, %(payload)s::jsonb
            )
            ON CONFLICT (source, source_item_id, symbol) DO NOTHING;
            """,
            [
                {
                    **row,
                    "payload": _json(row.get("payload") or {}),
                }
                for row in rows
            ],
        )
        inserted = cur.rowcount if cur.rowcount is not None else len(rows)
    return inserted


def event_hash_exists(
    conn: psycopg.Connection,
    *,
    symbol: str,
    dedupe_hash: str,
    lookback_days: int = 7,
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM equities_events
            WHERE symbol = %s
              AND dedupe_hash = %s
              AND published_ts >= NOW() - make_interval(days => %s)
            LIMIT 1;
            """,
            (symbol, dedupe_hash, lookback_days),
        )
        return cur.fetchone() is not None


def raw_mention_hash_exists(
    conn: psycopg.Connection,
    *,
    symbol: str,
    dedupe_hash: str,
    lookback_days: int = 30,
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM equities_raw_mentions
            WHERE symbol = %s
              AND dedupe_hash = %s
              AND mention_ts >= NOW() - make_interval(days => %s)
            LIMIT 1;
            """,
            (symbol, dedupe_hash, lookback_days),
        )
        return cur.fetchone() is not None


def find_recent_raw_simhashes(
    conn: psycopg.Connection,
    *,
    symbol: str,
    source: str,
    lookback_hours: int = 48,
    limit: int = 250,
) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT simhash
            FROM equities_raw_mentions
            WHERE symbol = %s
              AND source = %s
              AND simhash IS NOT NULL
              AND mention_ts >= NOW() - make_interval(hours => %s)
            ORDER BY mention_ts DESC
            LIMIT %s;
            """,
            (symbol, source, lookback_hours, limit),
        )
        return [int(row["simhash"]) for row in cur.fetchall()]


def insert_feature_events(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_feature_events (
                id, symbol, timeframe, bar_start_ts, available_at_ts,
                horizon, session_state, features, regime_labels, source_coverage
            )
            VALUES (
                %(id)s, %(symbol)s, %(timeframe)s, %(bar_start_ts)s, %(available_at_ts)s,
                %(horizon)s, %(session_state)s, %(features)s::jsonb, %(regime_labels)s::jsonb, %(source_coverage)s::jsonb
            )
            ON CONFLICT DO NOTHING;
            """,
            [
                {
                    **row,
                    "features": _json(row.get("features") or {}),
                    "regime_labels": _json(row.get("regime_labels") or {}),
                    "source_coverage": _json(row.get("source_coverage") or {}),
                }
                for row in rows
            ],
        )
    return len(rows)


def load_feature_events(
    conn: psycopg.Connection,
    *,
    symbols: list[str],
    timeframe: str,
    horizons: list[str],
    start_ts: datetime,
    end_ts: datetime,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM equities_feature_events
            WHERE symbol = ANY(%s)
              AND timeframe = %s
              AND horizon = ANY(%s)
              AND bar_start_ts >= %s
              AND bar_start_ts <= %s
            ORDER BY symbol, bar_start_ts ASC, horizon ASC;
            """,
            (symbols, timeframe, horizons, start_ts, end_ts),
        )
        return list(cur.fetchall())


def insert_predictions(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_predictions (
                id, model_name, model_version,
                symbol, timeframe, bar_start_ts, horizon, available_at_ts,
                predicted_return, predicted_prob_up, confidence, gap_risk_score,
                regime_label, feature_refs, event_refs, diagnostics,
                deterministic_hash
            )
            VALUES (
                %(id)s, %(model_name)s, %(model_version)s,
                %(symbol)s, %(timeframe)s, %(bar_start_ts)s, %(horizon)s, %(available_at_ts)s,
                %(predicted_return)s, %(predicted_prob_up)s, %(confidence)s, %(gap_risk_score)s,
                %(regime_label)s, %(feature_refs)s::jsonb, %(event_refs)s::jsonb, %(diagnostics)s::jsonb,
                %(deterministic_hash)s
            )
            ON CONFLICT (model_name, model_version, symbol, timeframe, bar_start_ts, horizon)
            DO NOTHING;
            """,
            [
                {
                    **row,
                    "feature_refs": _json(row.get("feature_refs") or {}),
                    "event_refs": _json(row.get("event_refs") or {}),
                    "diagnostics": _json(row.get("diagnostics") or {}),
                }
                for row in rows
            ],
        )
    return len(rows)


def insert_model_evaluation(conn: psycopg.Connection, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO equities_model_evaluations (
                id, model_name, model_version, timeframe, horizon,
                split_type, metrics, notes, deterministic_hash
            )
            VALUES (
                %(id)s, %(model_name)s, %(model_version)s, %(timeframe)s, %(horizon)s,
                %(split_type)s, %(metrics)s::jsonb, %(notes)s, %(deterministic_hash)s
            )
            ON CONFLICT (id) DO NOTHING;
            """,
            {
                **row,
                "metrics": _json(row.get("metrics") or {}),
            },
        )


def list_settled_outcomes(
    conn: psycopg.Connection,
    *,
    timeframe: str | None = None,
    horizon: str | None = None,
    lookback_days: int = 365,
    limit: int = 20000,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM equities_opportunity_outcomes
            WHERE (%s::text IS NULL OR timeframe = %s::text)
              AND (%s::text IS NULL OR horizon = %s::text)
              AND created_at >= NOW() - make_interval(days => %s)
            ORDER BY created_at DESC
            LIMIT %s;
            """,
            (timeframe, timeframe, horizon, horizon, lookback_days, limit),
        )
        return list(cur.fetchall())


def insert_selection_policy(conn: psycopg.Connection, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO equities_selection_policies (
                id, actor, timeframe, horizon,
                min_confidence, min_edge_score, directional_prob_floor, expected_return_floor,
                min_samples, transaction_cost_bps, objective_score, metrics, notes, deterministic_hash
            )
            VALUES (
                %(id)s, %(actor)s, %(timeframe)s, %(horizon)s,
                %(min_confidence)s, %(min_edge_score)s, %(directional_prob_floor)s, %(expected_return_floor)s,
                %(min_samples)s, %(transaction_cost_bps)s, %(objective_score)s, %(metrics)s::jsonb, %(notes)s, %(deterministic_hash)s
            )
            ON CONFLICT (id) DO NOTHING;
            """,
            {
                **row,
                "metrics": _json(row.get("metrics") or {}),
            },
        )


def load_latest_selection_policies(
    conn: psycopg.Connection,
    *,
    timeframe: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (timeframe, horizon)
                id, created_at, actor, timeframe, horizon,
                min_confidence, min_edge_score, directional_prob_floor, expected_return_floor,
                min_samples, transaction_cost_bps, objective_score, metrics, notes, deterministic_hash
            FROM equities_selection_policies
            WHERE (%s::text IS NULL OR timeframe = %s::text)
            ORDER BY timeframe, horizon, created_at DESC;
            """,
            (timeframe, timeframe),
        )
        rows = list(cur.fetchall())
    return {(row["timeframe"], row["horizon"]): row for row in rows}


def insert_calibration_alert(conn: psycopg.Connection, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO equities_calibration_alerts (
                id, actor, timeframe, horizon, severity, alert_type, message, data, deterministic_hash
            )
            VALUES (
                %(id)s, %(actor)s, %(timeframe)s, %(horizon)s, %(severity)s, %(alert_type)s, %(message)s, %(data)s::jsonb, %(deterministic_hash)s
            )
            ON CONFLICT (id) DO NOTHING;
            """,
            {
                **row,
                "data": _json(row.get("data") or {}),
            },
        )


def load_recent_calibration_alerts(
    conn: psycopg.Connection,
    *,
    lookback_days: int = 30,
    limit: int = 500,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM equities_calibration_alerts
            WHERE created_at >= NOW() - make_interval(days => %s)
            ORDER BY created_at DESC
            LIMIT %s;
            """,
            (lookback_days, limit),
        )
        return list(cur.fetchall())


def insert_legacy_opportunity(conn: psycopg.Connection, row: dict[str, Any]) -> int:
    batch_id = row.get("batch_id")
    with conn.cursor() as cur:
        # Dedup: if batch_id provided and already exists, return existing id
        if batch_id:
            cur.execute(
                "SELECT id FROM opportunities WHERE batch_id = %s LIMIT 1",
                (batch_id,),
            )
            existing = cur.fetchone()
            if existing:
                return int(existing["id"])
        cur.execute(
            """
            INSERT INTO opportunities (
                created_ts, updated_ts,
                symbol, timeframe, setup_id, decision,
                score, reason, payload_json, status, batch_id
            )
            VALUES (NOW(), NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                row["symbol"],
                row["timeframe"],
                row.get("setup_id", "EQFP"),
                row["decision"],
                float(row["score"]),
                row.get("reason"),
                _json(row.get("payload_json") or {}),
                row.get("status", "active"),
                batch_id,
            ),
        )
        return int(cur.fetchone()["id"])


def upsert_equities_opportunity(conn: psycopg.Connection, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO equities_opportunities (
                id, prediction_id,
                symbol, timeframe, horizon, bar_start_ts, available_at_ts,
                status, expected_return, probability_up, confidence,
                regime_label, rationale, provenance, legacy_opportunity_id
            )
            VALUES (
                %(id)s, %(prediction_id)s,
                %(symbol)s, %(timeframe)s, %(horizon)s, %(bar_start_ts)s, %(available_at_ts)s,
                %(status)s, %(expected_return)s, %(probability_up)s, %(confidence)s,
                %(regime_label)s, %(rationale)s::jsonb, %(provenance)s::jsonb, %(legacy_opportunity_id)s
            )
            ON CONFLICT (id) DO UPDATE SET
                updated_at = NOW(),
                status = EXCLUDED.status,
                expected_return = EXCLUDED.expected_return,
                probability_up = EXCLUDED.probability_up,
                confidence = EXCLUDED.confidence,
                regime_label = EXCLUDED.regime_label,
                rationale = EXCLUDED.rationale,
                provenance = EXCLUDED.provenance,
                legacy_opportunity_id = EXCLUDED.legacy_opportunity_id;
            """,
            {
                **row,
                "rationale": _json(row.get("rationale") or {}),
                "provenance": _json(row.get("provenance") or {}),
            },
        )


def list_settleable_opportunities(
    conn: psycopg.Connection,
    *,
    as_of_ts: datetime,
    statuses: list[str] | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    statuses = statuses or ["active"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                eo.id AS opportunity_id,
                eo.prediction_id,
                eo.symbol,
                eo.timeframe,
                eo.horizon,
                eo.bar_start_ts,
                eo.available_at_ts,
                eo.status,
                eo.expected_return,
                eo.probability_up,
                eo.confidence,
                eo.regime_label,
                eo.rationale,
                eo.provenance,
                ep.model_name,
                ep.model_version,
                ep.predicted_return,
                ep.predicted_prob_up,
                ep.diagnostics,
                ep.feature_refs,
                ep.event_refs
            FROM equities_opportunities eo
            JOIN equities_predictions ep ON ep.id = eo.prediction_id
            LEFT JOIN equities_opportunity_outcomes outc ON outc.opportunity_id = eo.id
            WHERE eo.status = ANY(%s::text[])
              AND eo.available_at_ts <= %s
              AND outc.opportunity_id IS NULL
            ORDER BY eo.available_at_ts ASC
            LIMIT %s;
            """,
            (statuses, as_of_ts, limit),
        )
        return list(cur.fetchall())


def insert_opportunity_outcomes(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_opportunity_outcomes (
                id, opportunity_id, prediction_id,
                symbol, timeframe, horizon, direction, regime_label,
                available_at_ts, settle_bar_ts,
                expected_return, predicted_prob_up, confidence, edge_score,
                realized_return, directional_realized_return, hit,
                brier_loss, log_loss, outcome_payload, deterministic_hash
            )
            VALUES (
                %(id)s, %(opportunity_id)s, %(prediction_id)s,
                %(symbol)s, %(timeframe)s, %(horizon)s, %(direction)s, %(regime_label)s,
                %(available_at_ts)s, %(settle_bar_ts)s,
                %(expected_return)s, %(predicted_prob_up)s, %(confidence)s, %(edge_score)s,
                %(realized_return)s, %(directional_realized_return)s, %(hit)s,
                %(brier_loss)s, %(log_loss)s, %(outcome_payload)s::jsonb, %(deterministic_hash)s
            )
            ON CONFLICT (opportunity_id) DO NOTHING;
            """,
            [
                {
                    **row,
                    "outcome_payload": _json(row.get("outcome_payload") or {}),
                }
                for row in rows
            ],
        )
        return cur.rowcount if cur.rowcount is not None else len(rows)


def mark_opportunities_settled(
    conn: psycopg.Connection,
    *,
    opportunity_ids: list[str],
    status: str = "settled",
) -> int:
    if not opportunity_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE equities_opportunities
            SET status = %s,
                updated_at = NOW()
            WHERE id = ANY(%s::uuid[])
              AND status <> %s;
            """,
            (status, opportunity_ids, status),
        )
        return cur.rowcount if cur.rowcount is not None else 0


def get_cache(conn: psycopg.Connection, cache_key: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT payload
            FROM equities_source_cache
            WHERE cache_key = %s
              AND (expires_at IS NULL OR expires_at > NOW())
            LIMIT 1;
            """,
            (cache_key,),
        )
        row = cur.fetchone()
    return row["payload"] if row else None


def set_cache(
    conn: psycopg.Connection,
    *,
    cache_key: str,
    source: str,
    payload: str,
    ttl_seconds: int | None,
    content_hash: str | None = None,
) -> None:
    expires_at = None
    if ttl_seconds is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds))

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO equities_source_cache (
                cache_key, source, fetched_at, expires_at, content_hash, payload
            )
            VALUES (%s, %s, NOW(), %s, %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                source = EXCLUDED.source,
                fetched_at = EXCLUDED.fetched_at,
                expires_at = EXCLUDED.expires_at,
                content_hash = EXCLUDED.content_hash,
                payload = EXCLUDED.payload;
            """,
            (cache_key, source, expires_at, content_hash, payload),
        )
