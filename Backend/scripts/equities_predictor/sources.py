"""Free/open data sources for equities predictor with cache + fallback."""

from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import requests

from .db import get_cache, set_cache

DEFAULT_HEADERS = {
    "User-Agent": "MCAS-EquitiesPredictor/1.0 (opensource; no-auth)",
    "Accept": "*/*",
}

SEC_HEADERS = {
    "User-Agent": "MCAS-EquitiesPredictor/1.0 research@local",
    "Accept": "application/json, application/atom+xml, application/xml, text/xml, */*",
}


def _cache_key(source: str, url: str, params: dict[str, Any] | None = None) -> str:
    serial = url
    if params:
        serial += "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))
    return hashlib.sha256(f"{source}|{serial}".encode("utf-8")).hexdigest()


def _request_text(
    conn,
    *,
    source: str,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl_seconds: int | None = 900,
    timeout_s: int = 20,
) -> str:
    key = _cache_key(source, url, params)
    cached = get_cache(conn, key)
    if cached is not None:
        return cached

    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)

    resp = requests.get(url, params=params, headers=merged_headers, timeout=timeout_s)
    resp.raise_for_status()
    text = resp.text
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    set_cache(
        conn,
        cache_key=key,
        source=source,
        payload=text,
        ttl_seconds=ttl_seconds,
        content_hash=digest,
    )
    return text


def _request_json(
    conn,
    *,
    source: str,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl_seconds: int | None = 900,
    timeout_s: int = 20,
) -> Any:
    return json.loads(
        _request_text(
            conn,
            source=source,
            url=url,
            params=params,
            headers=headers,
            ttl_seconds=ttl_seconds,
            timeout_s=timeout_s,
        )
    )


def fetch_sec_ticker_map(conn) -> dict[str, dict[str, Any]]:
    """Fetch SEC ticker mapping (ticker->metadata) without API key."""
    data = _request_json(
        conn,
        source="sec_ticker_map",
        url="https://www.sec.gov/files/company_tickers.json",
        headers=SEC_HEADERS,
        ttl_seconds=86400,
    )

    out: dict[str, dict[str, Any]] = {}
    if isinstance(data, dict):
        for value in data.values():
            ticker = str(value.get("ticker", "")).upper().strip()
            if not ticker:
                continue
            out[ticker] = {
                "cik": str(value.get("cik_str", "")).zfill(10),
                "title": value.get("title"),
            }
    return out


def fetch_sec_filings_atom(conn, cik: str, count: int = 40) -> list[dict[str, Any]]:
    """Fetch SEC company filings via Atom feed."""
    cik_norm = str(cik).zfill(10)
    url = "https://www.sec.gov/cgi-bin/browse-edgar"
    params = {
        "action": "getcompany",
        "CIK": cik_norm,
        "owner": "exclude",
        "count": str(count),
        "output": "atom",
    }
    xml_text = _request_text(
        conn,
        source=f"sec_atom_{cik_norm}",
        url=url,
        params=params,
        headers=SEC_HEADERS,
        ttl_seconds=1800,
    )

    root = ET.fromstring(xml_text)
    ns = {
        "a": "http://www.w3.org/2005/Atom",
    }
    entries = []
    for entry in root.findall("a:entry", ns):
        entry_id = entry.findtext("a:id", default="", namespaces=ns)
        title = entry.findtext("a:title", default="", namespaces=ns)
        updated = entry.findtext("a:updated", default="", namespaces=ns)
        category_el = entry.find("a:category", ns)
        form_type = category_el.attrib.get("term") if category_el is not None else None

        link = None
        link_el = entry.find("a:link", ns)
        if link_el is not None:
            link = link_el.attrib.get("href")

        published_ts = None
        if updated:
            published_ts = datetime.fromisoformat(updated.replace("Z", "+00:00")).astimezone(timezone.utc)

        entries.append(
            {
                "source_event_id": entry_id,
                "title": title,
                "url": link,
                "published_ts": published_ts,
                "form_type": form_type,
                "raw_updated": updated,
            }
        )

    return entries


def fetch_google_news_rss(conn, query: str, *, max_items: int = 25) -> list[dict[str, Any]]:
    """Fetch Google News RSS query without auth."""
    url = "https://news.google.com/rss/search"
    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    xml_text = _request_text(
        conn,
        source=f"google_news_{query}",
        url=url,
        params=params,
        ttl_seconds=900,
    )

    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall("./channel/item")[:max_items]:
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        pub_date_text = item.findtext("pubDate", default="")
        source = item.findtext("source", default="GoogleNews")

        published_ts = None
        if pub_date_text:
            try:
                published_ts = parsedate_to_datetime(pub_date_text).astimezone(timezone.utc)
            except Exception:
                published_ts = None

        guid = item.findtext("guid", default=link)
        items.append(
            {
                "source_event_id": guid,
                "title": title,
                "url": link,
                "published_ts": published_ts,
                "publisher": source,
            }
        )

    return items


def fetch_yahoo_chart(
    conn,
    *,
    symbol: str,
    interval: str,
    start_ts: datetime,
    end_ts: datetime,
    include_prepost: bool = True,
) -> list[dict[str, Any]]:
    """Fetch OHLCV bars from Yahoo chart endpoint (free, no key)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "interval": interval,
        "period1": str(int(start_ts.timestamp())),
        "period2": str(int(end_ts.timestamp())),
        "events": "div,splits",
        "includePrePost": "true" if include_prepost else "false",
    }

    data = _request_json(
        conn,
        source=f"yahoo_chart_{symbol}_{interval}",
        url=url,
        params=params,
        ttl_seconds=600,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    result = data.get("chart", {}).get("result")
    if not result:
        return []
    block = result[0]

    timestamps = block.get("timestamp") or []
    quotes = (block.get("indicators", {}).get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    bars = []
    for i, ts in enumerate(timestamps):
        if i >= len(opens) or i >= len(highs) or i >= len(lows) or i >= len(closes) or i >= len(volumes):
            continue
        o = opens[i]
        h = highs[i]
        l = lows[i]
        c = closes[i]
        v = volumes[i]
        if o is None or h is None or l is None or c is None or v is None:
            continue

        bar_ts = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        bars.append(
            {
                "symbol": symbol,
                "bar_start_ts": bar_ts,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            }
        )

    bars.sort(key=lambda x: x["bar_start_ts"])
    return bars


def fetch_stooq_daily(conn, *, symbol: str, start_ts: datetime, end_ts: datetime) -> list[dict[str, Any]]:
    """Fetch daily bars from Stooq CSV as fallback."""
    # Stooq uses lower-case and .us suffix for many symbols.
    stooq_symbol = symbol.lower().replace("-", "") + ".us"
    url = "https://stooq.com/q/d/l/"
    params = {
        "s": stooq_symbol,
        "i": "d",
    }
    csv_text = _request_text(
        conn,
        source=f"stooq_daily_{symbol}",
        url=url,
        params=params,
        ttl_seconds=3600,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    lines = [x.strip() for x in csv_text.splitlines() if x.strip()]
    if not lines:
        return []

    header = [h.strip().lower() for h in lines[0].split(",")]
    try:
        date_idx = header.index("date")
        open_idx = header.index("open")
        high_idx = header.index("high")
        low_idx = header.index("low")
        close_idx = header.index("close")
        volume_idx = header.index("volume")
    except ValueError:
        return []

    bars = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(date_idx, open_idx, high_idx, low_idx, close_idx, volume_idx):
            continue
        try:
            d = datetime.fromisoformat(parts[date_idx]).replace(tzinfo=timezone.utc)
            if d < start_ts or d > end_ts:
                continue
            bars.append(
                {
                    "symbol": symbol,
                    "bar_start_ts": d,
                    "open": float(parts[open_idx]),
                    "high": float(parts[high_idx]),
                    "low": float(parts[low_idx]),
                    "close": float(parts[close_idx]),
                    "volume": float(parts[volume_idx]),
                }
            )
        except Exception:
            continue

    bars.sort(key=lambda x: x["bar_start_ts"])
    return bars


def polite_sleep(seconds: float = 0.25) -> None:
    time.sleep(max(0.0, seconds))
