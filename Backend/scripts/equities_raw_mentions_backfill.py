#!/usr/bin/env python3
"""Backfill raw equities mentions/articles from free/open sources (SEC, GDELT, Reddit)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests

from equities_predictor.calendar import session_relation
from equities_predictor.db import (
    connect,
    find_recent_raw_simhashes,
    get_cache,
    insert_events,
    insert_raw_mentions,
    log_pipeline_event,
    raw_mention_hash_exists,
    set_cache,
    update_symbol_cik,
    upsert_universe,
)
from equities_predictor.ids import deterministic_uuid, stable_hash
from equities_predictor.sources import SEC_HEADERS, fetch_sec_ticker_map, polite_sleep
from equities_predictor.text_utils import (
    anti_bot_score,
    content_hash,
    hamming_distance,
    simple_sentiment_score,
    simhash_64,
    tokenize,
)
from equities_predictor.universe import get_default_symbols, get_universe_rows


UTC = timezone.utc

GDELT_ARTLIST_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
PULLPUSH_SUBMISSION_URL = "https://api.pullpush.io/reddit/search/submission/"

REDDIT_SUBREDDITS = [
    "stocks",
    "investing",
    "wallstreetbets",
    "StockMarket",
    "SecurityAnalysis",
    "options",
]

PULLPUSH_HEADERS = {
    "User-Agent": "MCAS-EquitiesPredictor/1.0 (free-open backfill)",
}


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _try_advisory_lock(conn, lock_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS locked;", (lock_name,))
        row = cur.fetchone()
    return bool(row["locked"])


def _advisory_unlock(conn, lock_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s));", (lock_name,))


def _parse_iso_or_none(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            continue
    return None


def _parse_ymd_or_none(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_gdelt_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _cache_key(source: str, url: str, params: dict[str, Any] | None = None) -> str:
    serial = url
    if params:
        serial += "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))
    return hashlib.sha256(f"{source}|{serial}".encode("utf-8")).hexdigest()


def _request_json_with_cache(
    conn,
    *,
    source: str,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl_seconds: int | None = 3600,
    timeout_s: int = 30,
) -> Any:
    key = _cache_key(source, url, params)
    cached = get_cache(conn, key)
    if cached is not None:
        return json.loads(cached)

    req_headers = {"User-Agent": "MCAS-EquitiesPredictor/1.0"}
    if headers:
        req_headers.update(headers)
    response = requests.get(url, params=params, headers=req_headers, timeout=timeout_s)
    response.raise_for_status()
    text = response.text
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    set_cache(
        conn,
        cache_key=key,
        source=source,
        payload=text,
        ttl_seconds=ttl_seconds,
        content_hash=digest,
    )
    conn.commit()
    return json.loads(text)


def _iter_windows(
    start_ts: datetime,
    end_ts: datetime,
    *,
    days: int,
    max_windows: int | None = None,
) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    cursor = start_ts
    step = max(1, days)
    while cursor < end_ts:
        nxt = min(cursor + timedelta(days=step), end_ts)
        out.append((cursor, nxt))
        cursor = nxt
        if max_windows is not None and len(out) >= max_windows:
            break
    return out


def _gdelt_dt(ts: datetime) -> str:
    return ts.strftime("%Y%m%d%H%M%S")


def _signed_simhash(text: str) -> int:
    return simhash_64(tokenize(text))


def _is_near_duplicate(simhash: int, existing: list[int], max_distance: int = 3) -> bool:
    for other in existing:
        if hamming_distance(simhash, int(other)) <= max_distance:
            return True
    return False


def _session_rel_or_unknown(ts: datetime | None) -> str:
    if ts is None:
        return "unknown"
    try:
        return session_relation(ts)
    except Exception:
        return "unknown"


def _event_type_from_title(title: str) -> str:
    lower = (title or "").lower()
    if "earnings" in lower:
        return "NEWS_EARNINGS"
    if "upgrade" in lower or "downgrade" in lower:
        return "NEWS_ANALYST"
    if "sec" in lower or "filing" in lower:
        return "NEWS_FILING"
    return "NEWS"


def _sec_event_type(form_type: str | None, title: str) -> str:
    ft = (form_type or "").upper()
    if ft in {"10-Q", "10-K", "8-K", "6-K", "DEF 14A"}:
        return f"SEC_{ft}"
    if "earnings" in (title or "").lower():
        return "SEC_EARNINGS"
    return "SEC_FILING"


def _select_symbols(symbols_csv: str | None, top_n: int) -> list[str]:
    if symbols_csv:
        selected = [s.strip().upper() for s in symbols_csv.split(",") if s.strip()]
    else:
        selected = list(get_default_symbols())
    if top_n > 0:
        selected = selected[:top_n]
    seen = set()
    ordered = []
    for s in selected:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _company_name_map() -> dict[str, str]:
    return {
        row["symbol"].upper(): str(row.get("name") or row["symbol"])
        for row in get_universe_rows()
    }


def _fetch_sec_submission_json(conn, cik: str) -> dict[str, Any]:
    cik_norm = str(cik).zfill(10)
    return _request_json_with_cache(
        conn,
        source=f"sec_submissions_{cik_norm}",
        url=f"https://data.sec.gov/submissions/CIK{cik_norm}.json",
        headers=SEC_HEADERS,
        ttl_seconds=86400,
        timeout_s=45,
    )


def _fetch_sec_older_file(conn, file_name: str) -> dict[str, Any]:
    return _request_json_with_cache(
        conn,
        source=f"sec_submissions_file_{file_name}",
        url=f"https://data.sec.gov/submissions/{file_name}",
        headers=SEC_HEADERS,
        ttl_seconds=86400,
        timeout_s=45,
    )


def _iter_sec_filing_records(
    conn,
    *,
    symbol: str,
    cik: str,
    start_ts: datetime,
) -> list[dict[str, Any]]:
    payload = _fetch_sec_submission_json(conn, cik)

    def parse_recent_block(block: dict[str, Any], origin: str) -> list[dict[str, Any]]:
        forms = list(block.get("form") or [])
        accession_numbers = list(block.get("accessionNumber") or [])
        filing_dates = list(block.get("filingDate") or [])
        acceptance_dates = list(block.get("acceptanceDateTime") or [])
        docs = list(block.get("primaryDocument") or [])
        descriptions = list(block.get("primaryDocDescription") or [])

        rows: list[dict[str, Any]] = []
        for i in range(len(forms)):
            filing_dt = _parse_ymd_or_none(filing_dates[i] if i < len(filing_dates) else None)
            if filing_dt is None:
                continue
            if filing_dt < start_ts:
                continue

            accepted = _parse_iso_or_none(acceptance_dates[i] if i < len(acceptance_dates) else None)
            mention_ts = accepted or filing_dt
            accession = str(accession_numbers[i] if i < len(accession_numbers) else "") or stable_hash(
                symbol,
                origin,
                forms[i] if i < len(forms) else "",
                filing_dates[i] if i < len(filing_dates) else "",
                length=32,
            )
            primary_doc = str(docs[i] if i < len(docs) else "") or None
            acc_no_dash = accession.replace("-", "")
            try:
                cik_int = str(int(cik))
            except ValueError:
                cik_int = cik.lstrip("0") or cik
            filing_url = None
            if primary_doc:
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dash}/{primary_doc}"

            form_type = str(forms[i] if i < len(forms) else "") or None
            desc = str(descriptions[i] if i < len(descriptions) else "") or ""
            title = f"{form_type or 'SEC Filing'} {symbol}".strip()

            rows.append(
                {
                    "source_item_id": accession,
                    "mention_ts": mention_ts,
                    "title": title,
                    "url": filing_url,
                    "form_type": form_type,
                    "description": desc,
                    "origin": origin,
                }
            )
        return rows

    filings: list[dict[str, Any]] = []
    recent = payload.get("filings", {}).get("recent", {})
    filings.extend(parse_recent_block(recent, "recent"))

    older_files = list(payload.get("filings", {}).get("files") or [])
    for file_meta in older_files[:8]:
        file_name = file_meta.get("name")
        if not file_name:
            continue
        older = _fetch_sec_older_file(conn, str(file_name))
        older_recent = older.get("filings", {}).get("recent", {})
        rows = parse_recent_block(older_recent, str(file_name))
        if not rows:
            continue
        filings.extend(rows)
        oldest = min((row["mention_ts"] for row in rows), default=_now_utc())
        if oldest < start_ts:
            break

    # Deterministic order for reproducibility.
    filings.sort(key=lambda row: (row["mention_ts"], row["source_item_id"]))
    return filings


def _gdelt_query_for_symbol(symbol: str, company_name: str) -> str:
    if len(symbol) <= 2:
        return f'"{company_name}" AND (stock OR shares OR earnings OR guidance OR sec)'
    return (
        f'("{symbol}" OR "{company_name}") '
        "AND (stock OR shares OR earnings OR guidance OR sec OR filing OR analyst)"
    )


def _fetch_gdelt_articles(
    conn,
    *,
    query: str,
    start_ts: datetime,
    end_ts: datetime,
    max_records: int,
) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": max(10, min(250, max_records)),
        "sort": "DateAsc",
        "startdatetime": _gdelt_dt(start_ts),
        "enddatetime": _gdelt_dt(end_ts),
    }
    payload = _request_json_with_cache(
        conn,
        source=f"gdelt_artlist_{stable_hash(query, _gdelt_dt(start_ts), _gdelt_dt(end_ts), length=24)}",
        url=GDELT_ARTLIST_URL,
        params=params,
        headers={"User-Agent": "MCAS-EquitiesPredictor/1.0"},
        ttl_seconds=86400,
        timeout_s=45,
    )
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        return []
    return articles


def _reddit_mentions_symbol(symbol: str, text: str, company_name: str) -> bool:
    up = text.upper()
    if f"${symbol}" in up:
        return True
    if len(symbol) >= 3 and re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", up):
        return True
    comp = (company_name or "").strip().lower()
    if comp and comp in text.lower():
        return True
    return False


def _fetch_pullpush_posts(
    *,
    subreddit: str,
    query: str,
    after_ts: datetime,
    before_ts: datetime,
    size: int,
) -> list[dict[str, Any]]:
    params = {
        "subreddit": subreddit,
        "q": query,
        "after": int(after_ts.timestamp()),
        "before": int(before_ts.timestamp()),
        "size": max(10, min(500, size)),
        "sort_type": "created_utc",
        "sort": "desc",
    }
    response = requests.get(
        PULLPUSH_SUBMISSION_URL,
        params=params,
        headers=PULLPUSH_HEADERS,
        timeout=40,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        rows = payload.get("data")
        if isinstance(rows, list):
            return rows
        return []
    if isinstance(payload, list):
        return payload
    return []


def _build_sec_rows(
    *,
    symbol: str,
    cik: str,
    record: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    mention_ts = record["mention_ts"]
    title = record["title"]
    desc = record.get("description") or ""
    url = record.get("url")
    source_item_id = str(record["source_item_id"])
    text_blob = f"{title} {desc}".strip()
    dedupe = content_hash(symbol, title, url or source_item_id)
    sim = _signed_simhash(text_blob)
    raw_id = deterministic_uuid("equities_raw_mention", "SEC_SUBMISSIONS", symbol, source_item_id)
    event_source_id = stable_hash(symbol, source_item_id, length=32)
    event_id = deterministic_uuid("equities_event", "SEC_EDGAR", event_source_id, symbol)

    raw_row = {
        "id": raw_id,
        "source": "SEC_SUBMISSIONS",
        "source_item_id": source_item_id,
        "symbol": symbol,
        "mention_ts": mention_ts,
        "available_at_ts": mention_ts,
        "session_relation": _session_rel_or_unknown(mention_ts),
        "publisher": "SEC",
        "author": None,
        "title": title,
        "url": url,
        "content": desc or title,
        "language": "en",
        "region": "US",
        "engagement_score": None,
        "dedupe_hash": dedupe,
        "simhash": sim,
        "payload": {
            "cik": cik,
            "form_type": record.get("form_type"),
            "origin": record.get("origin"),
            "description": desc,
        },
    }

    event_row = {
        "id": event_id,
        "source": "SEC_EDGAR",
        "source_event_id": event_source_id,
        "symbol": symbol,
        "publisher": "SEC",
        "title": title,
        "url": url,
        "published_ts": mention_ts,
        "available_at_ts": mention_ts,
        "event_type": _sec_event_type(record.get("form_type"), title),
        "session_relation": _session_rel_or_unknown(mention_ts),
        "sentiment_score": simple_sentiment_score(text_blob),
        "anti_bot_score": anti_bot_score(text_blob, "sec"),
        "dedupe_hash": dedupe,
        "simhash": sim,
        "payload": raw_row["payload"],
    }
    return raw_row, event_row


def backfill(
    *,
    database_url: str | None,
    actor: str,
    symbols_csv: str | None,
    top_n: int,
    lookback_days: int,
    sources: list[str],
    shard: int | None,
    shards: int | None,
    gdelt_window_days: int,
    gdelt_max_records: int,
    reddit_window_days: int,
    reddit_size: int,
    max_windows_per_symbol: int | None,
) -> dict[str, Any]:
    conn = connect(database_url)
    start_ts = _now_utc() - timedelta(days=max(1, lookback_days))
    end_ts = _now_utc()
    source_set = {s.strip().lower() for s in sources if s.strip()}
    company_names = _company_name_map()
    symbols = _select_symbols(symbols_csv, top_n=max(1, top_n))
    if shards is not None and shard is not None:
        symbols = [sym for i, sym in enumerate(symbols) if i % shards == shard]

    summary = {
        "symbols": len(symbols),
        "lookback_days": lookback_days,
        "sources": sorted(source_set),
        "raw_inserted": 0,
        "events_inserted": 0,
        "sec_records": 0,
        "gdelt_records": 0,
        "reddit_records": 0,
        "skipped_exact": 0,
        "skipped_near": 0,
        "errors": 0,
    }

    try:
        locked_seed = _try_advisory_lock(conn, "equities_universe_seed")
        if locked_seed:
            try:
                upsert_universe(conn, get_universe_rows())
                conn.commit()
            finally:
                _advisory_unlock(conn, "equities_universe_seed")
                conn.commit()

        log_pipeline_event(
            conn,
            actor=actor,
            event_type="RAW_BACKFILL_START",
            message="Equities raw mentions backfill started",
            data={
                "sources": sorted(source_set),
                "symbols": len(symbols),
                "lookback_days": lookback_days,
                "shard": shard,
                "shards": shards,
            },
        )
        conn.commit()

        symbol_cik: dict[str, str] = {}
        if "sec" in source_set:
            sec_map = fetch_sec_ticker_map(conn)
            symbol_cik = {sym: meta["cik"] for sym, meta in sec_map.items()}

            locked_cik = _try_advisory_lock(conn, "equities_cik_seed")
            if locked_cik:
                try:
                    update_symbol_cik(conn, symbol_cik)
                    conn.commit()
                finally:
                    _advisory_unlock(conn, "equities_cik_seed")
                    conn.commit()

        raw_batch: list[dict[str, Any]] = []
        event_batch: list[dict[str, Any]] = []

        def flush() -> None:
            if raw_batch:
                summary["raw_inserted"] += insert_raw_mentions(conn, raw_batch)
                raw_batch.clear()
            if event_batch:
                summary["events_inserted"] += insert_events(conn, event_batch)
                event_batch.clear()
            conn.commit()

        for symbol in symbols:
            company_name = company_names.get(symbol, symbol)
            try:
                if "sec" in source_set:
                    cik = symbol_cik.get(symbol)
                    if cik:
                        records = _iter_sec_filing_records(conn, symbol=symbol, cik=cik, start_ts=start_ts)
                        for rec in records:
                            raw_row, event_row = _build_sec_rows(symbol=symbol, cik=cik, record=rec)
                            if raw_mention_hash_exists(
                                conn,
                                symbol=symbol,
                                dedupe_hash=raw_row["dedupe_hash"],
                                lookback_days=lookback_days,
                            ):
                                summary["skipped_exact"] += 1
                                continue
                            recent_sim = find_recent_raw_simhashes(
                                conn,
                                symbol=symbol,
                                source=raw_row["source"],
                                lookback_hours=96,
                            )
                            if _is_near_duplicate(raw_row["simhash"], recent_sim, max_distance=2):
                                summary["skipped_near"] += 1
                                continue
                            raw_batch.append(raw_row)
                            event_batch.append(event_row)
                            summary["sec_records"] += 1
                            if len(raw_batch) >= 400:
                                flush()
                        polite_sleep(0.1)

                if "gdelt" in source_set:
                    query = _gdelt_query_for_symbol(symbol, company_name)
                    windows = _iter_windows(
                        start_ts,
                        end_ts,
                        days=max(1, gdelt_window_days),
                        max_windows=max_windows_per_symbol,
                    )
                    for win_start, win_end in windows:
                        try:
                            rows = _fetch_gdelt_articles(
                                conn,
                                query=query,
                                start_ts=win_start,
                                end_ts=win_end,
                                max_records=gdelt_max_records,
                            )
                        except Exception as exc:
                            summary["errors"] += 1
                            log_pipeline_event(
                                conn,
                                actor=actor,
                                event_type="SOURCE_ERROR",
                                message=f"GDELT fetch failed for {symbol}",
                                data={
                                    "symbol": symbol,
                                    "query": query,
                                    "error": str(exc),
                                    "start": win_start.isoformat(),
                                    "end": win_end.isoformat(),
                                },
                            )
                            conn.commit()
                            continue

                        for art in rows:
                            title = str(art.get("title") or "").strip()
                            if not title:
                                continue
                            mention_ts = _parse_gdelt_ts(str(art.get("seendate") or "")) or win_end
                            if mention_ts < start_ts:
                                continue
                            url = str(art.get("url") or "").strip() or None
                            publisher = str(art.get("domain") or art.get("sourcecountry") or "GDELT")
                            source_item_id = stable_hash(
                                symbol,
                                url or "",
                                art.get("seendate") or "",
                                art.get("domain") or "",
                                length=32,
                            )
                            text_blob = f"{title} {publisher}".strip()
                            dedupe = content_hash(symbol, title, url or source_item_id)
                            sim = _signed_simhash(text_blob)
                            if raw_mention_hash_exists(
                                conn,
                                symbol=symbol,
                                dedupe_hash=dedupe,
                                lookback_days=lookback_days,
                            ):
                                summary["skipped_exact"] += 1
                                continue
                            recent_sim = find_recent_raw_simhashes(
                                conn,
                                symbol=symbol,
                                source="GDELT_ARTLIST",
                                lookback_hours=72,
                            )
                            if _is_near_duplicate(sim, recent_sim, max_distance=2):
                                summary["skipped_near"] += 1
                                continue

                            raw_payload = {
                                "query": query,
                                "window_start": win_start.isoformat(),
                                "window_end": win_end.isoformat(),
                                "raw": art,
                            }
                            raw_batch.append(
                                {
                                    "id": deterministic_uuid(
                                        "equities_raw_mention",
                                        "GDELT_ARTLIST",
                                        symbol,
                                        source_item_id,
                                    ),
                                    "source": "GDELT_ARTLIST",
                                    "source_item_id": source_item_id,
                                    "symbol": symbol,
                                    "mention_ts": mention_ts,
                                    "available_at_ts": mention_ts,
                                    "session_relation": _session_rel_or_unknown(mention_ts),
                                    "publisher": publisher,
                                    "author": None,
                                    "title": title,
                                    "url": url,
                                    "content": title,
                                    "language": str(art.get("language") or "en"),
                                    "region": str(art.get("sourcecountry") or "US"),
                                    "engagement_score": float(art.get("seendatecount") or 0.0) or None,
                                    "dedupe_hash": dedupe,
                                    "simhash": sim,
                                    "payload": raw_payload,
                                }
                            )
                            event_batch.append(
                                {
                                    "id": deterministic_uuid(
                                        "equities_event",
                                        "OPEN_NEWS",
                                        source_item_id,
                                        symbol,
                                    ),
                                    "source": "OPEN_NEWS",
                                    "source_event_id": source_item_id,
                                    "symbol": symbol,
                                    "publisher": publisher,
                                    "title": title,
                                    "url": url,
                                    "published_ts": mention_ts,
                                    "available_at_ts": mention_ts,
                                    "event_type": _event_type_from_title(title),
                                    "session_relation": _session_rel_or_unknown(mention_ts),
                                    "sentiment_score": simple_sentiment_score(text_blob),
                                    "anti_bot_score": anti_bot_score(title, publisher),
                                    "dedupe_hash": dedupe,
                                    "simhash": sim,
                                    "payload": raw_payload,
                                }
                            )
                            summary["gdelt_records"] += 1
                            if len(raw_batch) >= 400:
                                flush()

                        polite_sleep(0.1)

                if "reddit" in source_set:
                    windows = _iter_windows(
                        start_ts,
                        end_ts,
                        days=max(1, reddit_window_days),
                        max_windows=max_windows_per_symbol,
                    )
                    for win_start, win_end in windows:
                        for subreddit in REDDIT_SUBREDDITS:
                            try:
                                posts = _fetch_pullpush_posts(
                                    subreddit=subreddit,
                                    query=symbol,
                                    after_ts=win_start,
                                    before_ts=win_end,
                                    size=reddit_size,
                                )
                            except Exception as exc:
                                summary["errors"] += 1
                                log_pipeline_event(
                                    conn,
                                    actor=actor,
                                    event_type="SOURCE_ERROR",
                                    message=f"Reddit backfill fetch failed for {symbol}",
                                    data={
                                        "symbol": symbol,
                                        "subreddit": subreddit,
                                        "error": str(exc),
                                        "start": win_start.isoformat(),
                                        "end": win_end.isoformat(),
                                    },
                                )
                                conn.commit()
                                continue

                            for post in posts:
                                title = str(post.get("title") or "")
                                body = str(post.get("selftext") or "")
                                text_blob = f"{title}\n{body}".strip()
                                if not text_blob:
                                    continue
                                if not _reddit_mentions_symbol(symbol, text_blob, company_name):
                                    continue
                                created = post.get("created_utc")
                                if created is None:
                                    continue
                                try:
                                    mention_ts = datetime.fromtimestamp(int(created), tz=UTC)
                                except Exception:
                                    continue
                                if mention_ts < start_ts:
                                    continue
                                source_item_id = stable_hash(
                                    symbol,
                                    subreddit,
                                    post.get("id") or "",
                                    str(created),
                                    length=32,
                                )
                                permalink = str(post.get("permalink") or "")
                                url = (
                                    f"https://reddit.com{permalink}"
                                    if permalink and permalink.startswith("/")
                                    else str(post.get("url") or "")
                                )
                                dedupe = content_hash(symbol, title, url or source_item_id)
                                sim = _signed_simhash(text_blob)
                                if raw_mention_hash_exists(
                                    conn,
                                    symbol=symbol,
                                    dedupe_hash=dedupe,
                                    lookback_days=lookback_days,
                                ):
                                    summary["skipped_exact"] += 1
                                    continue
                                recent_sim = find_recent_raw_simhashes(
                                    conn,
                                    symbol=symbol,
                                    source="REDDIT_PULLPUSH",
                                    lookback_hours=72,
                                )
                                if _is_near_duplicate(sim, recent_sim, max_distance=2):
                                    summary["skipped_near"] += 1
                                    continue

                                payload = {
                                    "subreddit": subreddit,
                                    "author": post.get("author"),
                                    "score": post.get("score"),
                                    "num_comments": post.get("num_comments"),
                                    "raw": post,
                                }
                                raw_batch.append(
                                    {
                                        "id": deterministic_uuid(
                                            "equities_raw_mention",
                                            "REDDIT_PULLPUSH",
                                            symbol,
                                            source_item_id,
                                        ),
                                        "source": "REDDIT_PULLPUSH",
                                        "source_item_id": source_item_id,
                                        "symbol": symbol,
                                        "mention_ts": mention_ts,
                                        "available_at_ts": mention_ts,
                                        "session_relation": _session_rel_or_unknown(mention_ts),
                                        "publisher": subreddit,
                                        "author": str(post.get("author") or "") or None,
                                        "title": title or None,
                                        "url": url or None,
                                        "content": text_blob[:8000],
                                        "language": "en",
                                        "region": "US",
                                        "engagement_score": float(post.get("score") or 0.0),
                                        "dedupe_hash": dedupe,
                                        "simhash": sim,
                                        "payload": payload,
                                    }
                                )
                                event_batch.append(
                                    {
                                        "id": deterministic_uuid(
                                            "equities_event",
                                            "OPEN_SOCIAL",
                                            source_item_id,
                                            symbol,
                                        ),
                                        "source": "OPEN_SOCIAL",
                                        "source_event_id": source_item_id,
                                        "symbol": symbol,
                                        "publisher": subreddit,
                                        "title": title or f"Reddit mention {symbol}",
                                        "url": url or None,
                                        "published_ts": mention_ts,
                                        "available_at_ts": mention_ts,
                                        "event_type": "SOCIAL_MENTION",
                                        "session_relation": _session_rel_or_unknown(mention_ts),
                                        "sentiment_score": simple_sentiment_score(text_blob),
                                        "anti_bot_score": anti_bot_score(text_blob, subreddit),
                                        "dedupe_hash": dedupe,
                                        "simhash": sim,
                                        "payload": payload,
                                    }
                                )
                                summary["reddit_records"] += 1
                                if len(raw_batch) >= 400:
                                    flush()

                            polite_sleep(0.15)

                flush()
                polite_sleep(0.05)
            except Exception as exc:
                summary["errors"] += 1
                log_pipeline_event(
                    conn,
                    actor=actor,
                    event_type="RAW_BACKFILL_SYMBOL_ERROR",
                    message=f"Raw backfill failed for {symbol}",
                    data={"symbol": symbol, "error": str(exc)},
                )
                conn.commit()

        log_pipeline_event(
            conn,
            actor=actor,
            event_type="RAW_BACKFILL_COMPLETE",
            message="Equities raw mentions backfill completed",
            data=summary,
        )
        conn.commit()
        return summary
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill raw equities mentions from free/open sources")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols; defaults to deterministic universe")
    parser.add_argument("--top-n", type=int, default=62)
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument("--sources", default="sec,gdelt,reddit")
    parser.add_argument("--shards", type=int, default=None)
    parser.add_argument("--shard", type=int, default=None)
    parser.add_argument("--gdelt-window-days", type=int, default=14)
    parser.add_argument("--gdelt-max-records", type=int, default=120)
    parser.add_argument("--reddit-window-days", type=int, default=21)
    parser.add_argument("--reddit-size", type=int, default=120)
    parser.add_argument(
        "--max-windows-per-symbol",
        type=int,
        default=0,
        help="For debugging only; 0 means full lookback window coverage",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_list = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    max_windows = int(args.max_windows_per_symbol) if int(args.max_windows_per_symbol) > 0 else None

    summary = backfill(
        database_url=args.database_url,
        actor=args.actor,
        symbols_csv=args.symbols,
        top_n=max(1, int(args.top_n)),
        lookback_days=max(1, int(args.lookback_days)),
        sources=source_list,
        shard=args.shard,
        shards=args.shards,
        gdelt_window_days=max(1, int(args.gdelt_window_days)),
        gdelt_max_records=max(10, int(args.gdelt_max_records)),
        reddit_window_days=max(1, int(args.reddit_window_days)),
        reddit_size=max(10, int(args.reddit_size)),
        max_windows_per_symbol=max_windows,
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
