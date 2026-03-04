from datetime import datetime, timezone

from fastapi.testclient import TestClient

import main


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.params = None
        self._result = []

    def execute(self, query, params=None):
        q = " ".join(query.split()).lower()
        if "from ohlcv" in q:
            self.params = params
            self._result = list(self.rows)
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _make_client(monkeypatch, rows):
    cursor = _FakeCursor(rows)
    conn = _FakeConn(cursor)
    monkeypatch.setattr(main, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(main, "API_KEY", "test-api-key")
    monkeypatch.setattr(main.psycopg, "connect", lambda _: conn)
    return TestClient(main.app), cursor


def test_ohlcv_reads_db_and_shapes_response(monkeypatch):
    rows = [
        (
            datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc),
            100.0,
            101.0,
            99.0,
            100.5,
            1234.0,
            datetime(2026, 3, 4, 10, 1, tzinfo=timezone.utc),
            True,
            "ws",
        ),
        (
            datetime(2026, 3, 4, 10, 1, tzinfo=timezone.utc),
            100.5,
            102.0,
            100.0,
            101.8,
            1700.0,
            datetime(2026, 3, 4, 10, 2, tzinfo=timezone.utc),
            False,
            "binance",
        ),
    ]

    client, cursor = _make_client(monkeypatch, rows)
    response = client.get(
        "/v1/ohlcv?symbol=btcusdt&tf=1m&limit=2",
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["symbol"] == "BTCUSDT"
    assert body["timeframe"] == "1m"
    assert body["source"] == "db_ohlcv"
    assert body["count"] == 2

    assert cursor.params == ("BTCUSDT", "1m", 2)
    assert body["candles"][0]["timestamp"] == int(rows[0][0].timestamp() * 1000)
    assert body["candles"][1]["timestamp"] == int(rows[1][0].timestamp() * 1000)
    assert body["candles"][0]["source"] == "ws"
    assert body["candles"][1]["is_final"] is False
