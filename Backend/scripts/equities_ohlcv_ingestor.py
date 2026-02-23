#!/usr/bin/env python3
"""Equities OHLCV ingestor (free/open sources, no API keys)."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from equities_predictor.calendar import ET, session_info
from equities_predictor.db import (
    connect,
    log_pipeline_event,
    upsert_bar_meta,
    upsert_ohlcv,
    upsert_universe,
)
from equities_predictor.sources import fetch_stooq_daily, fetch_yahoo_chart, polite_sleep
from equities_predictor.universe import get_default_symbols, get_universe_rows


def _parse_timeframe(tf: str) -> str:
    allowed = {"1h": "1h", "1d": "1d"}
    if tf not in allowed:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return allowed[tf]


def _bar_is_final(bar_start_ts: datetime, timeframe: str, now_utc: datetime) -> bool:
    if timeframe == "1h":
        return (bar_start_ts + timedelta(hours=1)) <= now_utc
    return (bar_start_ts + timedelta(days=1)) <= now_utc


def _is_rth_open_bar(ts_utc: datetime) -> bool:
    ts_et = ts_utc.astimezone(ET)
    return ts_et.hour == 9 and ts_et.minute == 30


def _try_advisory_lock(conn, lock_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS locked;", (lock_name,))
        row = cur.fetchone()
    return bool(row["locked"])


def _advisory_unlock(conn, lock_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s));", (lock_name,))


def ingest(
    *,
    database_url: str | None,
    symbols: list[str],
    timeframes: list[str],
    lookback_days: int,
    shard: int | None,
    shards: int | None,
    batch_id: str | None,
    actor: str,
) -> dict:
    conn = connect(database_url)
    now_utc = datetime.now(timezone.utc)
    start_ts = now_utc - timedelta(days=lookback_days)
    end_ts = now_utc
    batch = batch_id or f"eq_ohlcv_{now_utc.strftime('%Y%m%d_%H%M%S')}"

    if shard is not None and shards is not None:
        symbols = [s for i, s in enumerate(symbols) if i % shards == shard]

    inserted_total = 0
    source_counts = {"equities_yahoo": 0, "equities_stooq": 0}

    try:
        locked_seed = _try_advisory_lock(conn, "equities_universe_seed")
        if locked_seed:
            try:
                upsert_universe(conn, get_universe_rows())
                conn.commit()
            finally:
                _advisory_unlock(conn, "equities_universe_seed")
                conn.commit()

        for symbol in symbols:
            for tf in timeframes:
                interval = _parse_timeframe(tf)

                bars = []
                source = "equities_yahoo"
                try:
                    bars = fetch_yahoo_chart(
                        conn,
                        symbol=symbol,
                        interval=interval,
                        start_ts=start_ts,
                        end_ts=end_ts,
                        include_prepost=True,
                    )
                except Exception as exc:
                    log_pipeline_event(
                        conn,
                        actor=actor,
                        event_type="SOURCE_FALLBACK",
                        message=f"Yahoo fetch failed for {symbol} {tf}; fallback will be attempted",
                        data={"symbol": symbol, "timeframe": tf, "error": str(exc)},
                    )

                if (not bars) and tf == "1d":
                    source = "equities_stooq"
                    try:
                        bars = fetch_stooq_daily(conn, symbol=symbol, start_ts=start_ts, end_ts=end_ts)
                    except Exception as exc:
                        log_pipeline_event(
                            conn,
                            actor=actor,
                            event_type="SOURCE_ERROR",
                            message=f"Stooq fallback failed for {symbol} {tf}",
                            data={"symbol": symbol, "timeframe": tf, "error": str(exc)},
                        )

                if not bars:
                    continue

                ohlcv_rows = []
                meta_rows = []
                prev_close = None
                for bar in bars:
                    bar_ts = bar["bar_start_ts"].astimezone(timezone.utc)
                    final = _bar_is_final(bar_ts, tf, now_utc)

                    ohlcv_rows.append(
                        {
                            "symbol": symbol,
                            "timeframe": tf,
                            "bar_start_ts": bar_ts,
                            "open": bar["open"],
                            "high": bar["high"],
                            "low": bar["low"],
                            "close": bar["close"],
                            "volume": bar["volume"],
                            "as_of_ts": now_utc,
                            "is_final": final,
                        }
                    )

                    s_info = session_info(bar_ts)
                    gap = None
                    if prev_close is not None and _is_rth_open_bar(bar_ts):
                        if prev_close > 0:
                            gap = (bar["open"] - prev_close) / prev_close

                    meta_rows.append(
                        {
                            "symbol": symbol,
                            "timeframe": tf,
                            "bar_start_ts": bar_ts,
                            "source": source,
                            "session_state": s_info.session_state,
                            "next_open_ts": s_info.next_open_ts,
                            "last_close_ts": s_info.last_close_ts,
                            "open_gap_pct": gap,
                            "overnight_return": gap,
                        }
                    )
                    prev_close = bar["close"]

                inserted = upsert_ohlcv(conn, ohlcv_rows, source=source, batch_id=batch)
                upsert_bar_meta(conn, meta_rows)
                conn.commit()
                inserted_total += inserted
                source_counts[source] += inserted

                log_pipeline_event(
                    conn,
                    actor=actor,
                    event_type="OHLCV_INGEST",
                    message=f"Ingested {inserted} bars for {symbol} {tf} via {source}",
                    data={"symbol": symbol, "timeframe": tf, "source": source, "batch_id": batch},
                )
                conn.commit()
                polite_sleep(0.2)

        summary = {
            "batch_id": batch,
            "symbols": len(symbols),
            "inserted_total": inserted_total,
            "source_counts": source_counts,
        }
        log_pipeline_event(
            conn,
            actor=actor,
            event_type="OHLCV_INGEST_COMPLETE",
            message="Equities OHLCV ingest completed",
            data=summary,
        )
        conn.commit()
        return summary
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Equities OHLCV ingestor")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--symbols", default=",".join(get_default_symbols()))
    parser.add_argument("--timeframes", default="1h,1d")
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--shards", type=int, default=None)
    parser.add_argument("--shard", type=int, default=None)
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    summary = ingest(
        database_url=args.database_url,
        symbols=symbols,
        timeframes=timeframes,
        lookback_days=max(1, int(args.lookback_days)),
        shard=args.shard,
        shards=args.shards,
        batch_id=args.batch_id,
        actor=args.actor,
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
