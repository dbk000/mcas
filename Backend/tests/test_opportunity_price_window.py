from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

import main


class _FakeCursor:
    def __init__(self, opp_row, candle_rows):
        self._opp_row = opp_row
        self._candle_rows = candle_rows
        self._result = []
        self.ohlcv_params = None

    def execute(self, query, params=None):
        q = " ".join(query.split()).lower()
        if "from opportunities o" in q:
            self._result = [self._opp_row] if self._opp_row is not None else []
        elif "from ohlcv" in q:
            self.ohlcv_params = params
            self._result = list(self._candle_rows)
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

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


def _make_client(monkeypatch, opp_row, candle_rows):
    cursor = _FakeCursor(opp_row=opp_row, candle_rows=candle_rows)
    conn = _FakeConn(cursor)

    monkeypatch.setattr(main, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(main, "API_KEY", "test-api-key")
    monkeypatch.setattr(main.psycopg, "connect", lambda _: conn)

    return TestClient(main.app), cursor


def test_price_window_computes_bounds_and_clips_candles(monkeypatch):
    entry_ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    exit_ts = datetime(2026, 2, 1, 12, 30, tzinfo=timezone.utc)
    created_ts = entry_ts - timedelta(minutes=5)

    opp_row = (
        123,  # id
        "BTCUSDT",  # symbol
        created_ts,  # created_ts
        '{"entry_ts":"2026-02-01T10:00:00+00:00","exit_ts":"2026-02-01T12:30:00+00:00","entry_price":101.5,"stop_price":99.0,"tp1_price":105.0,"tp2_price":108.0}',  # payload_json
        None,  # sr.entry_ts
        None,  # sr.exit_ts
        None,  # sr.entry_price
        None,  # sr.stop_price
        None,  # sr.tp1_price
        None,  # sr.tp2_price
        1.2,  # sr.r_multiple
        25.0,  # sr.pnl_net
    )

    t_start = entry_ts - timedelta(hours=1)
    candle_rows = [
        (t_start, 100.0, 101.0, 99.5, 100.5, 1000.0),
        (t_start + timedelta(minutes=5), 100.5, 101.2, 100.0, 100.8, 1100.0),
        (exit_ts + timedelta(hours=1), 104.0, 104.8, 103.5, 104.2, 900.0),
    ]

    client, cursor = _make_client(monkeypatch, opp_row, candle_rows)
    response = client.get(
        "/v1/opportunities/123/price-window?pre_s=3600&post_s=21600",
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]

    expected_start = int((entry_ts - timedelta(seconds=3600)).timestamp() * 1000)
    expected_end = int((exit_ts + timedelta(seconds=21600)).timestamp() * 1000)
    assert body["t_start"] == expected_start
    assert body["t_end"] == expected_end
    assert body["tf"] == "5m"
    assert body["symbol"] == "BTCUSDT"
    assert body["markers"]["entry_ts"] == int(entry_ts.timestamp() * 1000)
    assert body["markers"]["exit_ts"] == int(exit_ts.timestamp() * 1000)

    assert len(body["candles"]) == 3
    assert all(expected_start <= c["t"] <= expected_end for c in body["candles"])

    # Ensure DB query only requested the computed window.
    assert cursor.ohlcv_params[0] == "BTCUSDT"
    assert cursor.ohlcv_params[1] == entry_ts - timedelta(seconds=3600)
    assert cursor.ohlcv_params[2] == exit_ts + timedelta(seconds=21600)


def test_price_window_missing_exit_ts_returns_422(monkeypatch):
    created_ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    opp_row = (
        456,  # id
        "ETHUSDT",  # symbol
        created_ts,  # created_ts
        '{"entry_price":2500.0,"stop_price":2450.0,"tp1_price":2580.0}',  # payload_json
        None,  # sr.entry_ts
        None,  # sr.exit_ts
        2500.0,  # sr.entry_price
        2450.0,  # sr.stop_price
        2580.0,  # sr.tp1_price
        None,  # sr.tp2_price
        None,  # sr.r_multiple
        None,  # sr.pnl_net
    )

    client, cursor = _make_client(monkeypatch, opp_row, [])
    response = client.get(
        "/v1/opportunities/456/price-window",
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "expired opp missing exit_ts"
    assert cursor.ohlcv_params is None
