#!/usr/bin/env python3
"""
Hourly settlement for expired opportunities missing exit metadata.

For each expired opportunity without payload.exit_ts:
- Load DB candles (5m) from entry_ts to min(now, expiry boundary)
- Walk forward for stop/take-profit hits when levels exist
- Otherwise settle at horizon boundary using close price
- Persist exit_ts / exit_price / exit_reason back to opportunity payload_json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg


LOG = logging.getLogger("expired_opp_exit_settler")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

LOCK_KEY = 2026030411
DEFAULT_LIMIT = 5000
DEFAULT_INTERVAL_S = int(os.getenv("SETTLER_INTERVAL_S", "3600"))
FIVE_MINUTE = "5m"
HORIZON_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)


def env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_SYNC_SIM_RESULTS = env_truthy("SETTLER_SYNC_SIM_RESULTS", False)


def parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        try:
            return parse_ts(float(v))
        except ValueError:
            pass
        try:
            if v.endswith("Z"):
                v = v[:-1] + "+00:00"
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_horizon_delta(value: Any) -> timedelta | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 0:
            return timedelta(seconds=seconds)
        return None
    if not isinstance(value, str):
        return None
    match = HORIZON_RE.match(value.strip().lower())
    if not match:
        return None
    qty = int(match.group(1))
    unit = match.group(2)
    if qty <= 0:
        return None
    if unit == "m":
        return timedelta(minutes=qty)
    if unit == "h":
        return timedelta(hours=qty)
    if unit == "d":
        return timedelta(days=qty)
    if unit == "w":
        return timedelta(weeks=qty)
    return None


def choose_direction(payload: dict[str, Any], decision: str | None) -> str:
    raw = str(payload.get("direction") or decision or "").strip().lower()
    if raw in {"long", "buy"}:
        return "long"
    if raw in {"short", "sell"}:
        return "short"
    return "long"


def fetch_candidates(conn: psycopg.Connection, limit: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, decision, created_ts, payload_json
            FROM opportunities
            WHERE status = 'expired'
              AND (
                    payload_json IS NULL
                    OR COALESCE(payload_json::jsonb ->> 'exit_ts', '') = ''
              )
            ORDER BY score DESC, updated_ts DESC
            LIMIT %s;
            """,
            (limit,),
        )
        return cur.fetchall()


def fetch_5m_candles(
    conn: psycopg.Connection,
    symbol: str,
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple[datetime, float, float, float, float]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (bar_start_ts)
                   bar_start_ts, open, high, low, close
            FROM ohlcv
            WHERE symbol = %s
              AND timeframe = %s
              AND bar_start_ts >= %s
              AND bar_start_ts <= %s
            ORDER BY bar_start_ts ASC,
                     as_of_ts DESC,
                     CASE source
                         WHEN 'ws' THEN 0
                         WHEN 'binance' THEN 1
                         ELSE 2
                     END;
            """,
            (symbol, FIVE_MINUTE, start_ts, end_ts),
        )
        rows = cur.fetchall()
    return [
        (r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        for r in rows
    ]


def pick_horizon_end(entry_ts: datetime, payload: dict[str, Any]) -> datetime:
    direct = (
        parse_ts(payload.get("action_by_ts"))
        or parse_ts(payload.get("expiry_ts"))
        or parse_ts(payload.get("expires_at"))
        or parse_ts(payload.get("exit_ts"))
    )
    if direct:
        return direct
    delta = parse_horizon_delta(payload.get("horizon"))
    if delta:
        return entry_ts + delta
    # Safe fallback so unresolved opps don't block forever.
    return entry_ts + timedelta(days=1)


def walk_forward_exit(
    *,
    direction: str,
    entry_price: float | None,
    stop_price: float | None,
    tp_price: float | None,
    candles: list[tuple[datetime, float, float, float, float]],
    horizon_end: datetime,
    now_ts: datetime,
) -> tuple[datetime, float, str] | None:
    if not candles:
        return None

    eval_end = min(horizon_end, now_ts)
    usable = [c for c in candles if c[0] <= eval_end]
    if not usable:
        return None

    # Walk forward candle by candle for stop/tp hits.
    for ts, _o, high, low, _close in usable:
        if direction == "short":
            if stop_price is not None and high >= stop_price:
                return ts, stop_price, "STOP_LOSS"
            if tp_price is not None and low <= tp_price:
                return ts, tp_price, "TAKE_PROFIT"
        else:
            if stop_price is not None and low <= stop_price:
                return ts, stop_price, "STOP_LOSS"
            if tp_price is not None and high >= tp_price:
                return ts, tp_price, "TAKE_PROFIT"

    # Time-settle only once horizon boundary is reached.
    if now_ts < horizon_end:
        return None

    # Choose first candle at/after horizon end, else last known close.
    settle = next((c for c in usable if c[0] >= horizon_end), usable[-1])
    return settle[0], settle[4], "TIME_HORIZON"


def persist_exit(
    conn: psycopg.Connection,
    opp_id: int,
    payload: dict[str, Any],
    exit_ts: datetime,
    exit_price: float | None,
    exit_reason: str,
    entry_ts: datetime,
    entry_price: float | None,
    sync_sim_results: bool,
) -> None:
    payload["entry_ts"] = entry_ts.isoformat()
    if entry_price is not None and payload.get("entry_price") is None:
        payload["entry_price"] = entry_price
    payload["exit_ts"] = exit_ts.isoformat()
    if exit_price is not None:
        payload["exit_price"] = float(exit_price)
    payload["exit_reason"] = exit_reason
    payload["exit_settled_ts"] = datetime.now(timezone.utc).isoformat()
    payload["exit_settler"] = "expired_opportunity_exit_settler_v1"

    payload_json = json.dumps(payload, separators=(",", ":"))

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opportunities
            SET payload_json = %s,
                updated_ts = NOW()
            WHERE id = %s;
            """,
            (payload_json, opp_id),
        )

        if sync_sim_results and exit_price is not None:
            # Optional sync path for consumers still reading simulation_results.
            cur.execute(
                """
                UPDATE simulation_results
                SET exit_ts = %s,
                    exit_price = COALESCE(exit_price, %s),
                    exit_reason = COALESCE(exit_reason, %s)
                WHERE opportunity_id = %s
                  AND exit_ts IS NULL;
                """,
                (exit_ts, float(exit_price), exit_reason, opp_id),
            )


def settle_once(conn: psycopg.Connection, limit: int, sync_sim_results: bool) -> dict[str, int]:
    now_ts = datetime.now(timezone.utc)
    candidates = fetch_candidates(conn, limit)
    stats = {
        "candidates": len(candidates),
        "settled": 0,
        "pending": 0,
        "no_candles": 0,
    }

    for opp_id, symbol, decision, created_ts, payload_raw in candidates:
        payload = parse_payload(payload_raw)
        entry_ts = (
            parse_ts(payload.get("entry_ts"))
            or parse_ts(payload.get("bar_start_ts"))
            or parse_ts(created_ts)
        )
        if not entry_ts:
            stats["pending"] += 1
            continue

        horizon_end = pick_horizon_end(entry_ts, payload)
        entry_price = parse_float(payload.get("entry_price"))
        direction = choose_direction(payload, decision)

        candles = fetch_5m_candles(conn, symbol, entry_ts, min(now_ts, horizon_end) + timedelta(hours=1))
        if not candles:
            stats["no_candles"] += 1
            if now_ts >= horizon_end:
                persist_exit(
                    conn=conn,
                    opp_id=opp_id,
                    payload=payload,
                    exit_ts=horizon_end,
                    exit_price=entry_price,
                    exit_reason="TIME_HORIZON_NO_CANDLES",
                    entry_ts=entry_ts,
                    entry_price=entry_price,
                    sync_sim_results=sync_sim_results,
                )
                stats["settled"] += 1
            else:
                stats["pending"] += 1
            continue

        if entry_price is None:
            entry_price = candles[0][1]
        stop_price = parse_float(payload.get("stop_price"))
        tp_price = parse_float(payload.get("tp1_price"))
        if tp_price is None:
            tp_price = parse_float(payload.get("tp2_price"))

        resolved = walk_forward_exit(
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            tp_price=tp_price,
            candles=candles,
            horizon_end=horizon_end,
            now_ts=now_ts,
        )
        if resolved is None:
            stats["pending"] += 1
            continue

        exit_ts, exit_price, exit_reason = resolved
        persist_exit(
            conn=conn,
            opp_id=opp_id,
            payload=payload,
            exit_ts=exit_ts,
            exit_price=exit_price,
            exit_reason=exit_reason,
            entry_ts=entry_ts,
            entry_price=entry_price,
            sync_sim_results=sync_sim_results,
        )
        stats["settled"] += 1

    return stats


def run_loop(
    database_url: str,
    limit: int,
    interval_seconds: int,
    loop: bool,
    sync_sim_results: bool,
) -> None:
    while True:
        started = time.time()
        try:
            with psycopg.connect(database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
                    locked = bool(cur.fetchone()[0])
                if not locked:
                    LOG.info("skipped cycle: advisory lock held elsewhere")
                else:
                    LOG.info(
                        "starting settlement cycle limit=%s sync_sim_results=%s",
                        limit,
                        sync_sim_results,
                    )
                    stats = settle_once(conn, limit, sync_sim_results)
                    conn.commit()
                    LOG.info("settlement cycle complete: %s", stats)
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        except Exception:
            LOG.exception("settlement cycle failed")

        if not loop:
            return

        elapsed = time.time() - started
        sleep_s = max(1, interval_seconds - int(elapsed))
        time.sleep(sleep_s)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Settle expired opportunities with exit_ts/exit_price.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""), help="Postgres DSN (defaults to DATABASE_URL env)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max opportunities per cycle")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_S, help="Loop interval in seconds")
    parser.add_argument("--loop", action="store_true", help="Run continuously (hourly)")
    parser.add_argument(
        "--sync-sim-results",
        action="store_true",
        default=DEFAULT_SYNC_SIM_RESULTS,
        help="Also sync open simulation_results rows for settled opportunities",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    run_loop(
        database_url=args.database_url,
        limit=max(1, int(args.limit)),
        interval_seconds=max(60, int(args.interval_seconds)),
        loop=bool(args.loop),
        sync_sim_results=bool(args.sync_sim_results),
    )


if __name__ == "__main__":
    main()
