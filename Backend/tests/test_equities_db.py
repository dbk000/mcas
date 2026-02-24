"""Integration-style tests for equities_predictor.db helpers."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

pytest.importorskip("psycopg", reason="psycopg not installed")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL is not configured", allow_module_level=True)

from equities_predictor.db import (  # noqa: E402
    connect,
    insert_events,
    insert_feature_events,
    insert_predictions,
    list_settleable_opportunities,
    load_ohlcv,
    mark_opportunities_settled,
    upsert_ohlcv,
)


def _create_temp_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS ohlcv (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                bar_start_ts TIMESTAMPTZ NOT NULL,
                open DOUBLE PRECISION NOT NULL,
                high DOUBLE PRECISION NOT NULL,
                low DOUBLE PRECISION NOT NULL,
                close DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
                as_of_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                is_final BOOLEAN NOT NULL DEFAULT TRUE,
                source TEXT NOT NULL,
                batch_id TEXT,
                PRIMARY KEY (symbol, timeframe, bar_start_ts, source)
            );
            """
        )
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS equities_events (
                id UUID PRIMARY KEY,
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                publisher TEXT,
                title TEXT NOT NULL,
                url TEXT,
                published_ts TIMESTAMPTZ NOT NULL,
                available_at_ts TIMESTAMPTZ NOT NULL,
                event_type TEXT NOT NULL,
                session_relation TEXT NOT NULL,
                sentiment_score DOUBLE PRECISION,
                anti_bot_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                dedupe_hash TEXT NOT NULL,
                simhash BIGINT,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE (source, source_event_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS equities_feature_events (
                id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                bar_start_ts TIMESTAMPTZ NOT NULL,
                available_at_ts TIMESTAMPTZ NOT NULL,
                horizon TEXT NOT NULL,
                session_state TEXT NOT NULL,
                features JSONB NOT NULL,
                regime_labels JSONB NOT NULL DEFAULT '{}'::jsonb,
                source_coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE (symbol, timeframe, bar_start_ts, horizon, available_at_ts)
            );
            """
        )
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS equities_predictions (
                id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                model_name TEXT NOT NULL,
                model_version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                bar_start_ts TIMESTAMPTZ NOT NULL,
                horizon TEXT NOT NULL,
                available_at_ts TIMESTAMPTZ NOT NULL,
                predicted_return DOUBLE PRECISION,
                predicted_prob_up DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                gap_risk_score DOUBLE PRECISION,
                regime_label TEXT,
                feature_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
                event_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
                diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb,
                deterministic_hash TEXT NOT NULL,
                UNIQUE (model_name, model_version, symbol, timeframe, bar_start_ts, horizon)
            );
            """
        )
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS equities_opportunities (
                id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                prediction_id UUID NOT NULL REFERENCES equities_predictions(id) ON DELETE CASCADE,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                horizon TEXT NOT NULL,
                bar_start_ts TIMESTAMPTZ NOT NULL,
                available_at_ts TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                expected_return DOUBLE PRECISION,
                probability_up DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                regime_label TEXT,
                rationale JSONB NOT NULL DEFAULT '{}'::jsonb,
                provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
                legacy_opportunity_id INT
            );
            """
        )
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS equities_opportunity_outcomes (
                id UUID PRIMARY KEY,
                opportunity_id UUID NOT NULL UNIQUE,
                prediction_id UUID,
                symbol TEXT,
                timeframe TEXT,
                horizon TEXT,
                direction TEXT,
                regime_label TEXT,
                available_at_ts TIMESTAMPTZ,
                settle_bar_ts TIMESTAMPTZ,
                expected_return DOUBLE PRECISION,
                predicted_prob_up DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                edge_score DOUBLE PRECISION,
                realized_return DOUBLE PRECISION,
                directional_realized_return DOUBLE PRECISION,
                hit BOOLEAN,
                brier_loss DOUBLE PRECISION,
                log_loss DOUBLE PRECISION,
                outcome_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                deterministic_hash TEXT
            );
            """
        )


@pytest.fixture()
def db_conn():
    conn = connect(DATABASE_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC';")
            cur.execute("SET search_path TO pg_temp, public;")
        _create_temp_tables(conn)
        conn.autocommit = False
        yield conn
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def _base_event_row(source_event_id: str) -> dict[str, Any]:
    base_ts = datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc)
    return {
        "id": uuid.uuid4(),
        "source": "OPEN_NEWS",
        "source_event_id": source_event_id,
        "symbol": "AAPL",
        "publisher": "UnitTest Wire",
        "title": f"Event {source_event_id}",
        "url": f"https://example.com/{source_event_id}",
        "published_ts": base_ts,
        "available_at_ts": base_ts + timedelta(minutes=2),
        "event_type": "NEWS",
        "session_relation": "intraday",
        "sentiment_score": 0.25,
        "anti_bot_score": 0.05,
        "dedupe_hash": f"dedupe-{source_event_id}",
        "simhash": 123456,
        "payload": {"k": "v"},
    }


def _base_feature_event_row(row_id: uuid.UUID | None = None) -> dict[str, Any]:
    bar_ts = datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc)
    return {
        "id": row_id or uuid.uuid4(),
        "symbol": "AAPL",
        "timeframe": "1h",
        "bar_start_ts": bar_ts,
        "available_at_ts": bar_ts + timedelta(minutes=1),
        "horizon": "1d",
        "session_state": "RTH",
        "features": {"price_return_1": 0.01},
        "regime_labels": {"trend": "up"},
        "source_coverage": {"source_count_6h": 2},
    }


def _base_prediction_row(
    *,
    row_id: uuid.UUID | None = None,
    model_name: str = "EQFP",
    model_version: str = "v1",
    symbol: str = "AAPL",
    timeframe: str = "1h",
    bar_start_ts: datetime | None = None,
    horizon: str = "1d",
) -> dict[str, Any]:
    bar_ts = bar_start_ts or datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc)
    return {
        "id": row_id or uuid.uuid4(),
        "model_name": model_name,
        "model_version": model_version,
        "symbol": symbol,
        "timeframe": timeframe,
        "bar_start_ts": bar_ts,
        "horizon": horizon,
        "available_at_ts": bar_ts + timedelta(minutes=1),
        "predicted_return": 0.012,
        "predicted_prob_up": 0.62,
        "confidence": 0.55,
        "gap_risk_score": 0.03,
        "regime_label": "risk_on",
        "feature_refs": {"feature_event_id": "abc"},
        "event_refs": {"event_id": "xyz"},
        "diagnostics": {"fold": "wf_1"},
        "deterministic_hash": f"pred-{uuid.uuid4()}",
    }


def test_upsert_ohlcv_conflict_updates_existing_row(db_conn):
    bar_ts = datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc)
    symbol = "TUPS"
    timeframe = "1h"

    upsert_ohlcv(
        db_conn,
        [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "bar_start_ts": bar_ts,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
                "as_of_ts": bar_ts + timedelta(minutes=5),
                "is_final": True,
            }
        ],
        source="equities_yahoo",
        batch_id="batch-first",
    )

    upsert_ohlcv(
        db_conn,
        [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "bar_start_ts": bar_ts,
                "open": 100.0,
                "high": 103.0,
                "low": 98.5,
                "close": 102.25,
                "volume": 2500.0,
                "as_of_ts": bar_ts + timedelta(minutes=10),
                "is_final": True,
            }
        ],
        source="equities_yahoo",
        batch_id="batch-second",
    )

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT close, volume, batch_id
            FROM ohlcv
            WHERE symbol = %s
              AND timeframe = %s
              AND bar_start_ts = %s
              AND source = %s;
            """,
            (symbol, timeframe, bar_ts, "equities_yahoo"),
        )
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*)::int AS n FROM ohlcv;")
        count = int(cur.fetchone()["n"])

    assert count == 1
    assert row["close"] == pytest.approx(102.25)
    assert row["volume"] == pytest.approx(2500.0)
    assert row["batch_id"] == "batch-second"


def test_insert_events_deduplicates_source_event_id(db_conn):
    first = _base_event_row("evt-001")
    duplicate = _base_event_row("evt-001")
    duplicate["title"] = "Conflicting duplicate title"

    insert_events(db_conn, [first])
    insert_events(db_conn, [duplicate])

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*)::int AS n FROM equities_events;")
        count = int(cur.fetchone()["n"])
        cur.execute("SELECT title FROM equities_events LIMIT 1;")
        title = str(cur.fetchone()["title"])

    assert count == 1
    assert title == first["title"]


def test_insert_feature_events_is_idempotent(db_conn):
    row = _base_feature_event_row()
    insert_feature_events(db_conn, [row])
    insert_feature_events(db_conn, [row])

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*)::int AS n FROM equities_feature_events;")
        count = int(cur.fetchone()["n"])

    assert count == 1


def test_insert_predictions_is_idempotent_on_unique_key(db_conn):
    base_ts = datetime(2026, 2, 20, 15, 0, tzinfo=timezone.utc)
    row_a = _base_prediction_row(row_id=uuid.uuid4(), bar_start_ts=base_ts)
    row_b = _base_prediction_row(row_id=uuid.uuid4(), bar_start_ts=base_ts)

    insert_predictions(db_conn, [row_a])
    insert_predictions(db_conn, [row_b])

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*)::int AS n FROM equities_predictions;")
        count = int(cur.fetchone()["n"])

    assert count == 1


def test_load_ohlcv_dedupes_with_asof_then_source_priority(db_conn):
    symbol = "TLOAD"
    base = datetime(2026, 2, 1, 13, 0, tzinfo=timezone.utc)
    ts_one = base
    ts_two = base + timedelta(hours=1)

    upsert_ohlcv(
        db_conn,
        [
            {
                "symbol": symbol,
                "timeframe": "1h",
                "bar_start_ts": ts_one,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 10.0,
                "as_of_ts": base + timedelta(hours=3),
                "is_final": True,
            }
        ],
        source="equities_yahoo",
        batch_id="batch-y1",
    )
    upsert_ohlcv(
        db_conn,
        [
            {
                "symbol": symbol,
                "timeframe": "1h",
                "bar_start_ts": ts_one,
                "open": 90.0,
                "high": 91.0,
                "low": 89.0,
                "close": 90.0,
                "volume": 10.0,
                "as_of_ts": base + timedelta(hours=2),
                "is_final": True,
            }
        ],
        source="equities_stooq",
        batch_id="batch-s1",
    )

    upsert_ohlcv(
        db_conn,
        [
            {
                "symbol": symbol,
                "timeframe": "1h",
                "bar_start_ts": ts_two,
                "open": 200.0,
                "high": 201.0,
                "low": 199.0,
                "close": 201.0,
                "volume": 10.0,
                "as_of_ts": base + timedelta(hours=4),
                "is_final": True,
            }
        ],
        source="equities_yahoo",
        batch_id="batch-y2",
    )
    upsert_ohlcv(
        db_conn,
        [
            {
                "symbol": symbol,
                "timeframe": "1h",
                "bar_start_ts": ts_two,
                "open": 201.0,
                "high": 202.0,
                "low": 200.0,
                "close": 202.0,
                "volume": 10.0,
                "as_of_ts": base + timedelta(hours=4),
                "is_final": True,
            }
        ],
        source="equities_stooq",
        batch_id="batch-s2",
    )

    rows = load_ohlcv(
        db_conn,
        symbols=[symbol],
        timeframe="1h",
        start_ts=base - timedelta(hours=1),
        end_ts=base + timedelta(hours=2),
        sources=["equities_yahoo", "equities_stooq"],
    )

    assert len(rows) == 2
    by_ts = {row["bar_start_ts"]: row for row in rows}
    assert by_ts[ts_one]["source"] == "equities_yahoo"
    assert by_ts[ts_one]["close"] == pytest.approx(101.0)
    assert by_ts[ts_two]["source"] == "equities_stooq"
    assert by_ts[ts_two]["close"] == pytest.approx(202.0)


def test_list_settleable_opportunities_excludes_settled_and_outcomed(db_conn):
    now_utc = datetime(2026, 2, 21, 15, 0, tzinfo=timezone.utc)
    prediction_id = uuid.uuid4()

    pred_row = _base_prediction_row(row_id=prediction_id, bar_start_ts=now_utc - timedelta(hours=3))
    insert_predictions(db_conn, [pred_row])

    open_id = uuid.uuid4()
    outcomed_id = uuid.uuid4()
    settled_status_id = uuid.uuid4()
    future_id = uuid.uuid4()

    with db_conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_opportunities (
                id, prediction_id, symbol, timeframe, horizon, bar_start_ts, available_at_ts, status,
                expected_return, probability_up, confidence, regime_label, rationale, provenance
            )
            VALUES (
                %(id)s, %(prediction_id)s, %(symbol)s, %(timeframe)s, %(horizon)s, %(bar_start_ts)s, %(available_at_ts)s, %(status)s,
                %(expected_return)s, %(probability_up)s, %(confidence)s, %(regime_label)s, %(rationale)s::jsonb, %(provenance)s::jsonb
            );
            """,
            [
                {
                    "id": open_id,
                    "prediction_id": prediction_id,
                    "symbol": "AAPL",
                    "timeframe": "1h",
                    "horizon": "1d",
                    "bar_start_ts": now_utc - timedelta(hours=2),
                    "available_at_ts": now_utc - timedelta(minutes=30),
                    "status": "active",
                    "expected_return": 0.02,
                    "probability_up": 0.60,
                    "confidence": 0.50,
                    "regime_label": "risk_on",
                    "rationale": "{}",
                    "provenance": "{}",
                },
                {
                    "id": outcomed_id,
                    "prediction_id": prediction_id,
                    "symbol": "AAPL",
                    "timeframe": "1h",
                    "horizon": "1d",
                    "bar_start_ts": now_utc - timedelta(hours=2),
                    "available_at_ts": now_utc - timedelta(minutes=20),
                    "status": "active",
                    "expected_return": 0.01,
                    "probability_up": 0.55,
                    "confidence": 0.45,
                    "regime_label": "risk_on",
                    "rationale": "{}",
                    "provenance": "{}",
                },
                {
                    "id": settled_status_id,
                    "prediction_id": prediction_id,
                    "symbol": "AAPL",
                    "timeframe": "1h",
                    "horizon": "1d",
                    "bar_start_ts": now_utc - timedelta(hours=2),
                    "available_at_ts": now_utc - timedelta(minutes=10),
                    "status": "settled",
                    "expected_return": 0.03,
                    "probability_up": 0.65,
                    "confidence": 0.60,
                    "regime_label": "risk_on",
                    "rationale": "{}",
                    "provenance": "{}",
                },
                {
                    "id": future_id,
                    "prediction_id": prediction_id,
                    "symbol": "AAPL",
                    "timeframe": "1h",
                    "horizon": "1d",
                    "bar_start_ts": now_utc - timedelta(hours=2),
                    "available_at_ts": now_utc + timedelta(minutes=15),
                    "status": "active",
                    "expected_return": 0.04,
                    "probability_up": 0.70,
                    "confidence": 0.65,
                    "regime_label": "risk_on",
                    "rationale": "{}",
                    "provenance": "{}",
                },
            ],
        )
        cur.execute(
            """
            INSERT INTO equities_opportunity_outcomes (id, opportunity_id, prediction_id, outcome_payload)
            VALUES (%s, %s, %s, '{}'::jsonb);
            """,
            (uuid.uuid4(), outcomed_id, prediction_id),
        )

    rows = list_settleable_opportunities(db_conn, as_of_ts=now_utc, limit=100)
    returned_ids = {str(r["opportunity_id"]) for r in rows}

    assert returned_ids == {str(open_id)}


def test_mark_opportunities_settled_updates_status_and_timestamp(db_conn):
    now_utc = datetime(2026, 2, 22, 12, 0, tzinfo=timezone.utc)
    prediction_id = uuid.uuid4()
    pred_row = _base_prediction_row(row_id=prediction_id, bar_start_ts=now_utc - timedelta(hours=2))
    insert_predictions(db_conn, [pred_row])

    active_id = uuid.uuid4()
    already_settled_id = uuid.uuid4()
    active_updated_at = now_utc - timedelta(days=2)
    settled_updated_at = now_utc - timedelta(hours=1)

    with db_conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_opportunities (
                id, created_at, updated_at, prediction_id, symbol, timeframe, horizon, bar_start_ts, available_at_ts,
                status, expected_return, probability_up, confidence, rationale, provenance
            )
            VALUES (
                %(id)s, %(created_at)s, %(updated_at)s, %(prediction_id)s, %(symbol)s, %(timeframe)s, %(horizon)s, %(bar_start_ts)s, %(available_at_ts)s,
                %(status)s, %(expected_return)s, %(probability_up)s, %(confidence)s, %(rationale)s::jsonb, %(provenance)s::jsonb
            );
            """,
            [
                {
                    "id": active_id,
                    "created_at": active_updated_at,
                    "updated_at": active_updated_at,
                    "prediction_id": prediction_id,
                    "symbol": "MSFT",
                    "timeframe": "1h",
                    "horizon": "1d",
                    "bar_start_ts": now_utc - timedelta(hours=3),
                    "available_at_ts": now_utc - timedelta(hours=2),
                    "status": "active",
                    "expected_return": 0.02,
                    "probability_up": 0.57,
                    "confidence": 0.48,
                    "rationale": "{}",
                    "provenance": "{}",
                },
                {
                    "id": already_settled_id,
                    "created_at": settled_updated_at,
                    "updated_at": settled_updated_at,
                    "prediction_id": prediction_id,
                    "symbol": "MSFT",
                    "timeframe": "1h",
                    "horizon": "1d",
                    "bar_start_ts": now_utc - timedelta(hours=3),
                    "available_at_ts": now_utc - timedelta(hours=2),
                    "status": "settled",
                    "expected_return": 0.01,
                    "probability_up": 0.51,
                    "confidence": 0.44,
                    "rationale": "{}",
                    "provenance": "{}",
                },
            ],
        )

    updated = mark_opportunities_settled(
        db_conn,
        opportunity_ids=[str(active_id), str(already_settled_id)],
        status="settled",
    )

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, status, updated_at
            FROM equities_opportunities
            WHERE id = ANY(%s::uuid[]);
            """,
            ([str(active_id), str(already_settled_id)],),
        )
        rows = {str(r["id"]): r for r in cur.fetchall()}

    assert updated == 1
    assert rows[str(active_id)]["status"] == "settled"
    assert rows[str(active_id)]["updated_at"] > active_updated_at
    assert rows[str(already_settled_id)]["status"] == "settled"
    assert rows[str(already_settled_id)]["updated_at"] == settled_updated_at
