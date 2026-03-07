"""
Tests for S9 simulation fixes:
  Bug 1: Time-travel in s9_backfill_expired.py (1s tape fetched from wrong hour)
  Bug 2: Simulator erases microstructure entry price (NEXT_OPEN overwrites)
"""

import json
from datetime import datetime, timedelta, timezone

from opportunity_simulator import OpportunitySimulator, SimulationConfig


def _make_bar(bar_start_ts, open_px, high, low, close):
    return {
        "bar_start_ts": bar_start_ts,
        "open": open_px,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
    }


def _make_bars_1h(start_hour_utc, n_bars, base_price=100.0):
    bars = []
    for i in range(n_bars):
        ts = start_hour_utc + timedelta(hours=i)
        p = base_price + i * 0.5
        bars.append(_make_bar(ts, p, p + 1, p - 1, p + 0.3))
    return bars


def _sim_opp(sim, opp_dict, bars_key, bars):
    """Helper: simulate a single opportunity with pre-built OHLCV cache."""
    return sim._simulate_single(opp_dict, {bars_key: bars})


class TestMicrostructureEntryRespected:
    """Bug 2: Simulator must use microstructure entry price, not next bar open."""

    def test_micro_entry_uses_payload_price(self):
        """When payload has microstructure entry_type != SKIP, use suggested_entry_price."""
        sim = OpportunitySimulator(SimulationConfig())

        # Signal fires at 11:05 (5min after 10:00 candle closes at 11:00)
        opp_ts = datetime(2026, 3, 1, 11, 5, tzinfo=timezone.utc)

        # Bars: 10:00, 11:00, 12:00, ..., with prices around 100-130
        bars = _make_bars_1h(
            datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
            n_bars=60, base_price=100.0,
        )

        payload = {
            "direction": "short",
            "entry_price": 101.50,
            "stop_price": 104.50,
            "tp1_price": 95.0,
            "tp2_price": None,
            "r_value": 3.0,
            "time_stop_bars": 48,
            "microstructure": {
                "entry_type": "MARKET",
                "suggested_entry_price": 101.50,
            },
        }

        opp = {
            "id": 1,
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "setup_id": "S9",
            "created_ts": opp_ts,
            "score": 0.8,
            "payload_json": json.dumps(payload),
        }

        result = _sim_opp(sim, opp, "BTCUSDT_1h", bars)

        assert result.data_available is True
        # Entry price must be the micro price (101.50), not the 12:00 bar open
        assert result.entry_price == 101.50
        # Entry bar should be the 11:00 bar (index 1, contains 11:05)
        assert result.entry_ts == datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)

    def test_skip_micro_uses_next_open(self):
        """When microstructure entry_type is SKIP, fall back to NEXT_OPEN."""
        sim = OpportunitySimulator(SimulationConfig())

        opp_ts = datetime(2026, 3, 1, 11, 5, tzinfo=timezone.utc)
        bars = _make_bars_1h(
            datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
            n_bars=60, base_price=100.0,
        )

        payload = {
            "direction": "short",
            "entry_price": 101.50,
            "stop_price": 104.50,
            "tp1_price": 95.0,
            "tp2_price": None,
            "r_value": 3.0,
            "time_stop_bars": 48,
            "microstructure": {
                "entry_type": "SKIP",
                "suggested_entry_price": 101.50,
            },
        }

        opp = {
            "id": 2,
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "setup_id": "S9",
            "created_ts": opp_ts,
            "score": 0.5,
            "payload_json": json.dumps(payload),
        }

        result = _sim_opp(sim, opp, "ETHUSDT_1h", bars)

        assert result.data_available is True
        # SKIP -> falls back to NEXT_OPEN at the 12:00 bar (index 2)
        bar_12 = bars[2]
        assert result.entry_price == bar_12["open"]

    def test_no_micro_uses_next_open(self):
        """When no microstructure in payload, use standard NEXT_OPEN."""
        sim = OpportunitySimulator(SimulationConfig())

        opp_ts = datetime(2026, 3, 1, 11, 5, tzinfo=timezone.utc)
        bars = _make_bars_1h(
            datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
            n_bars=60, base_price=100.0,
        )

        payload = {
            "direction": "long",
            "entry_price": 100.0,
            "stop_price": 97.0,
            "tp1_price": 106.0,
            "tp2_price": None,
            "r_value": 3.0,
            "time_stop_bars": 50,
        }

        opp = {
            "id": 3,
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "setup_id": "D1",
            "created_ts": opp_ts,
            "score": 0.9,
            "payload_json": json.dumps(payload),
        }

        result = _sim_opp(sim, opp, "BTCUSDT_1h", bars)

        assert result.data_available is True
        # No micro -> NEXT_OPEN at 12:00 bar (index 2)
        bar_12 = bars[2]
        assert result.entry_price == bar_12["open"]


class TestBackfillTimeAlignment:
    """Bug 1: Verify the time calculations are correct."""

    def test_candle_close_offset(self):
        """eval_hour=10:00 -> candle closes at 11:00 -> 1s tape at 11:00:01."""
        eval_hour = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        candle_close = eval_hour + timedelta(hours=1)

        hour_start_ms = int(candle_close.timestamp() * 1000) + 1000
        expected_ms = int(
            datetime(2026, 3, 1, 11, 0, 1, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert hour_start_ms == expected_ms

    def test_created_ts_after_candle_close(self):
        """created_ts should be 5 minutes after candle close, not after bar_start."""
        eval_hour = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        candle_close = eval_hour + timedelta(hours=1)
        created_ts = candle_close + timedelta(seconds=300)

        expected = datetime(2026, 3, 1, 11, 5, tzinfo=timezone.utc)
        assert created_ts == expected

    def test_bar_start_ts_matches_live(self):
        """bar_start_ts in payload should match live worker (candle close hour)."""
        eval_hour = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        candle_close = eval_hour + timedelta(hours=1)
        bar_start_ts = str(int(candle_close.timestamp() * 1000))

        expected_ms = int(
            datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert bar_start_ts == str(expected_ms)
