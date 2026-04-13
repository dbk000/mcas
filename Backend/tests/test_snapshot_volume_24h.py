"""Unit tests for SNAPSHOT_ENRICH (2026-04-13).

Covers the cache_contract helpers wiring the ingestor-refreshed
``universe:volume_24h`` hash into signal_worker's payload-build path.
"""

from __future__ import annotations

import os
import sys
import time

import fakeredis
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
_REPO = os.path.dirname(_BACKEND)
for p in (_REPO, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from Backend.governance import cache_contract as cc  # type: ignore
except ImportError:
    from governance import cache_contract as cc  # type: ignore


def _r() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


def test_store_and_read_roundtrip():
    r = _r()
    cc.store_volume_24h(r, {"BTCUSDC": 1_234_567.89, "ETHUSDC": 890_123.0})
    assert cc.read_volume_24h(r, "BTCUSDC") == pytest.approx(1_234_567.89)
    assert cc.read_volume_24h(r, "ETHUSDC") == pytest.approx(890_123.0)


def test_read_missing_symbol_returns_none():
    r = _r()
    cc.store_volume_24h(r, {"BTCUSDC": 1.0})
    assert cc.read_volume_24h(r, "NOT_A_SYMBOL") is None


def test_read_stale_returns_none():
    r = _r()
    cc.store_volume_24h(r, {"BTCUSDC": 1.0})
    # Manually rewind sentinel far past the TTL.
    stale_ts = time.time() - (cc.UNIVERSE_VOLUME_24H_TTL_S + 60)
    r.set(cc.UNIVERSE_VOLUME_24H_LAST_UPDATE_KEY, str(stale_ts))
    assert cc.read_volume_24h(r, "BTCUSDC") is None


def test_read_no_sentinel_returns_none():
    r = _r()
    # Hash present but sentinel missing → treat as never-published.
    r.hset(cc.UNIVERSE_VOLUME_24H_KEY, mapping={"BTCUSDC": "1.0"})
    assert cc.read_volume_24h(r, "BTCUSDC") is None


def test_empty_store_is_noop():
    r = _r()
    cc.store_volume_24h(r, {})
    assert cc.read_volume_24h(r, "BTCUSDC") is None
    assert cc.volume_24h_last_update_ts(r) is None


def test_store_overwrites_previous_hash():
    r = _r()
    cc.store_volume_24h(r, {"FOOUSDC": 1.0, "BARUSDC": 2.0})
    # Next refresh drops FOOUSDC.
    cc.store_volume_24h(r, {"BARUSDC": 3.0})
    assert cc.read_volume_24h(r, "FOOUSDC") is None
    assert cc.read_volume_24h(r, "BARUSDC") == pytest.approx(3.0)


def test_last_update_ts_fresh_after_store():
    r = _r()
    before = time.time()
    cc.store_volume_24h(r, {"BTCUSDC": 1.0})
    after = time.time()
    ts = cc.volume_24h_last_update_ts(r)
    assert ts is not None
    assert before - 1 <= ts <= after + 1


def test_corrupted_sentinel_is_safe():
    r = _r()
    cc.store_volume_24h(r, {"BTCUSDC": 1.0})
    r.set(cc.UNIVERSE_VOLUME_24H_LAST_UPDATE_KEY, "not-a-number")
    assert cc.read_volume_24h(r, "BTCUSDC") is None
    assert cc.volume_24h_last_update_ts(r) is None
