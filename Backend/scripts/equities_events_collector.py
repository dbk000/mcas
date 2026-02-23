#!/usr/bin/env python3
"""Equities events collector (SEC filings + open news RSS, no API keys)."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from equities_predictor.calendar import session_relation
from equities_predictor.db import (
    connect,
    event_hash_exists,
    find_recent_simhashes,
    get_symbol_cik_map,
    insert_events,
    list_symbols,
    log_pipeline_event,
    update_symbol_cik,
    upsert_universe,
)
from equities_predictor.ids import deterministic_uuid, stable_hash
from equities_predictor.sources import (
    fetch_google_news_rss,
    fetch_sec_filings_atom,
    fetch_sec_ticker_map,
    polite_sleep,
)
from equities_predictor.text_utils import (
    anti_bot_score,
    content_hash,
    hamming_distance,
    simple_sentiment_score,
    simhash_64,
    tokenize,
)
from equities_predictor.universe import get_universe_rows


def _classify_sec_event(form_type: str | None, title: str) -> str:
    ft = (form_type or "").upper()
    t = (title or "").lower()
    if ft in {"10-Q", "10-K", "8-K", "6-K", "DEF 14A"}:
        return f"SEC_{ft}"
    if "earnings" in t:
        return "SEC_EARNINGS"
    return "SEC_FILING"


def _classify_news_event(title: str) -> str:
    t = (title or "").lower()
    if "earnings" in t:
        return "NEWS_EARNINGS"
    if "upgrade" in t or "downgrade" in t:
        return "NEWS_ANALYST"
    if "sec" in t or "filing" in t:
        return "NEWS_FILING"
    return "NEWS"


def _normalize_published_ts(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_near_duplicate(simhash: int, existing: list[int], max_distance: int = 3) -> bool:
    for other in existing:
        if hamming_distance(simhash, int(other)) <= max_distance:
            return True
    return False


def collect(
    *,
    database_url: str | None,
    actor: str,
    symbols: list[str] | None,
    max_sec_items: int,
    max_news_items: int,
) -> dict:
    conn = connect(database_url)
    now_utc = datetime.now(timezone.utc)

    try:
        upsert_universe(conn, get_universe_rows())

        sec_map = fetch_sec_ticker_map(conn)
        update_symbol_cik(conn, {sym: sec_map[sym]["cik"] for sym in sec_map})
        conn.commit()

        symbol_cik = get_symbol_cik_map(conn)
        target_symbols = symbols or list_symbols(conn)
        target_symbols = sorted(set(target_symbols))

        emitted = []
        skipped_exact = 0
        skipped_near = 0

        for symbol in target_symbols:
            cik = symbol_cik.get(symbol)

            if cik:
                try:
                    sec_events = fetch_sec_filings_atom(conn, cik, count=max_sec_items)
                except Exception as exc:
                    log_pipeline_event(
                        conn,
                        actor=actor,
                        event_type="SOURCE_ERROR",
                        message=f"SEC feed failed for {symbol}",
                        data={"symbol": symbol, "source": "sec", "error": str(exc)},
                    )
                    conn.commit()
                    sec_events = []

                for ev in sec_events:
                    published_ts = _normalize_published_ts(ev.get("published_ts"))
                    title = ev.get("title") or "SEC filing"
                    dedupe = content_hash(symbol, title, ev.get("url") or "")
                    if event_hash_exists(conn, symbol=symbol, dedupe_hash=dedupe, lookback_days=30):
                        skipped_exact += 1
                        continue

                    tokens = tokenize(title)
                    sim = simhash_64(tokens)
                    recent = find_recent_simhashes(conn, symbol=symbol, source="SEC_EDGAR", lookback_hours=72)
                    if _is_near_duplicate(sim, recent, max_distance=2):
                        skipped_near += 1
                        continue

                    event_id = deterministic_uuid("equities_event", "SEC_EDGAR", ev.get("source_event_id"), symbol)
                    emitted.append(
                        {
                            "id": event_id,
                            "source": "SEC_EDGAR",
                            "source_event_id": str(ev.get("source_event_id") or stable_hash(symbol, title, published_ts.isoformat())),
                            "symbol": symbol,
                            "publisher": "SEC",
                            "title": title,
                            "url": ev.get("url"),
                            "published_ts": published_ts,
                            "available_at_ts": published_ts,
                            "event_type": _classify_sec_event(ev.get("form_type"), title),
                            "session_relation": session_relation(published_ts),
                            "sentiment_score": simple_sentiment_score(title),
                            "anti_bot_score": anti_bot_score(title, "sec"),
                            "dedupe_hash": dedupe,
                            "simhash": sim,
                            "payload": {
                                "form_type": ev.get("form_type"),
                                "raw_updated": ev.get("raw_updated"),
                                "cik": cik,
                            },
                        }
                    )

            query = f"{symbol} stock"
            try:
                news_events = fetch_google_news_rss(conn, query=query, max_items=max_news_items)
            except Exception as exc:
                log_pipeline_event(
                    conn,
                    actor=actor,
                    event_type="SOURCE_ERROR",
                    message=f"Google News RSS failed for {symbol}",
                    data={"symbol": symbol, "source": "google_news", "error": str(exc)},
                )
                conn.commit()
                news_events = []

            for ev in news_events:
                published_ts = _normalize_published_ts(ev.get("published_ts"))
                title = ev.get("title") or "news"
                publisher = ev.get("publisher") or "GoogleNews"
                dedupe = content_hash(symbol, title, publisher)
                if event_hash_exists(conn, symbol=symbol, dedupe_hash=dedupe, lookback_days=7):
                    skipped_exact += 1
                    continue

                tokens = tokenize(title)
                sim = simhash_64(tokens)
                recent = find_recent_simhashes(conn, symbol=symbol, source="OPEN_NEWS", lookback_hours=36)
                if _is_near_duplicate(sim, recent, max_distance=2):
                    skipped_near += 1
                    continue

                source_event_id = str(ev.get("source_event_id") or stable_hash(symbol, title, published_ts.isoformat()))
                event_id = deterministic_uuid("equities_event", "OPEN_NEWS", source_event_id, symbol)
                emitted.append(
                    {
                        "id": event_id,
                        "source": "OPEN_NEWS",
                        "source_event_id": source_event_id,
                        "symbol": symbol,
                        "publisher": publisher,
                        "title": title,
                        "url": ev.get("url"),
                        "published_ts": published_ts,
                        "available_at_ts": published_ts,
                        "event_type": _classify_news_event(title),
                        "session_relation": session_relation(published_ts),
                        "sentiment_score": simple_sentiment_score(title),
                        "anti_bot_score": anti_bot_score(title, publisher),
                        "dedupe_hash": dedupe,
                        "simhash": sim,
                        "payload": {
                            "query": query,
                        },
                    }
                )

            polite_sleep(0.15)

        inserted = insert_events(conn, emitted)
        conn.commit()

        summary = {
            "symbols": len(target_symbols),
            "inserted": inserted,
            "prepared": len(emitted),
            "skipped_exact": skipped_exact,
            "skipped_near": skipped_near,
            "lookback_notice": "No paid analyst/social feeds used; free/open SEC+news only.",
        }
        log_pipeline_event(
            conn,
            actor=actor,
            event_type="EVENT_COLLECT_COMPLETE",
            message="Equities events collection cycle completed",
            data=summary,
        )
        conn.commit()
        return summary
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect equities events from free/open sources")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol list")
    parser.add_argument("--max-sec-items", type=int, default=20)
    parser.add_argument("--max-news-items", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    summary = collect(
        database_url=args.database_url,
        actor=args.actor,
        symbols=symbols,
        max_sec_items=max(1, int(args.max_sec_items)),
        max_news_items=max(1, int(args.max_news_items)),
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
