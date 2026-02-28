#!/usr/bin/env python3
"""EQ Step 0A: Populate equities_earnings_calendar from event data.

Sources (in priority order):
  1. NEWS_EARNINGS / SEC_EARNINGS events (actual earnings announcements)
  2. SEC_8-K events with Item 2.02 in title (Results of Operations — filed same day)
  3. Fallback: 10-Q/10-K filing dates (confirmed=false, labelled SEC_FILING source)

BMO/AMC classification by hour of published_ts:
  - Before 09:30 ET → BMO
  - After  16:00 ET → AMC
  - Otherwise       → TBD

Dedup: If both NEWS_EARNINGS and 8-K 2.02 exist for same symbol within ±2 days,
       keep the earlier timestamp.

Usage:
  docker compose -f docker-compose.yml run --rm -T worker \
    python3 /scripts/eq_backfill_earnings_calendar.py [--include-filing-fallback]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Runtime path detection (CLAUDE.md §2)
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts/equities_predictor")
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "equities_predictor"))

from db import connect, upsert_earnings_calendar  # type: ignore[import-untyped]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def _classify_bmo_amc(ts: datetime) -> str:
    """Classify earnings timing as BMO, AMC, or TBD."""
    et_time = ts.astimezone(ET).time()
    if et_time < time(9, 30):
        return "BMO"
    elif et_time >= time(16, 0):
        return "AMC"
    return "TBD"


def _quarter_label(d: date) -> str:
    """Derive fiscal quarter label from date: e.g. '2025-Q3'."""
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill equities earnings calendar")
    parser.add_argument(
        "--include-filing-fallback",
        action="store_true",
        help="Include 10-Q/10-K filing dates as confirmed=false fallback",
    )
    args = parser.parse_args()

    conn = connect()
    conn.autocommit = True

    log.info("=" * 60)
    log.info("EQ Step 0A: Backfill Earnings Calendar")
    log.info("=" * 60)

    # ── Diagnostic counts (SME rec A) ──────────────────────────
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_type, COUNT(*) AS cnt
            FROM equities_events
            WHERE event_type IN ('NEWS_EARNINGS', 'SEC_EARNINGS')
            GROUP BY event_type
            ORDER BY event_type;
            """
        )
        earnings_counts = {r["event_type"]: r["cnt"] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE title ILIKE '%2.02%') AS with_item,
                COUNT(*) FILTER (WHERE title NOT ILIKE '%2.02%'
                                  AND title NOT ILIKE '%item%') AS without_item,
                COUNT(*) AS total
            FROM equities_events
            WHERE event_type = 'SEC_8-K';
            """
        )
        k8_counts = cur.fetchone()

    log.info("─── Diagnostic Counts (SME rec A) ───")
    for et, cnt in sorted(earnings_counts.items()):
        log.info(f"  {et}: {cnt}")
    log.info(f"  SEC_8-K total:              {k8_counts['total']}")
    log.info(f"  SEC_8-K with '2.02':        {k8_counts['with_item']}")
    log.info(f"  SEC_8-K without item number: {k8_counts['without_item']}")

    # ── Source 1: NEWS_EARNINGS / SEC_EARNINGS ──────────────────
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, published_ts, event_type
            FROM equities_events
            WHERE event_type IN ('NEWS_EARNINGS', 'SEC_EARNINGS')
            ORDER BY symbol, published_ts;
            """
        )
        earnings_events = list(cur.fetchall())

    log.info(f"Source 1: {len(earnings_events)} NEWS_EARNINGS/SEC_EARNINGS events")

    # ── Source 2: SEC_8-K Item 2.02 ─────────────────────────────
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, published_ts, event_type
            FROM equities_events
            WHERE event_type = 'SEC_8-K'
              AND title ILIKE '%2.02%'
            ORDER BY symbol, published_ts;
            """
        )
        k8_events = list(cur.fetchall())

    log.info(f"Source 2: {len(k8_events)} SEC_8-K Item 2.02 events")

    # ── Merge and dedup ─────────────────────────────────────────
    # Group by symbol, then merge events within ±2 days
    by_symbol: dict[str, list[tuple[datetime, str]]] = defaultdict(list)

    for ev in earnings_events:
        by_symbol[ev["symbol"]].append((ev["published_ts"], "NEWS_EARNINGS"))

    for ev in k8_events:
        by_symbol[ev["symbol"]].append((ev["published_ts"], "8K_2.02"))

    rows_to_insert = []
    for symbol in sorted(by_symbol):
        events = sorted(by_symbol[symbol], key=lambda x: x[0])
        merged: list[tuple[datetime, str]] = []

        for ts, src in events:
            # Check if this event is within ±2 days of an existing merged entry
            found_dup = False
            for i, (m_ts, m_src) in enumerate(merged):
                if abs((ts - m_ts).total_seconds()) < 2 * 86400:
                    # Keep the earlier timestamp
                    if ts < m_ts:
                        merged[i] = (ts, src)
                    found_dup = True
                    break
            if not found_dup:
                merged.append((ts, src))

        for ts, src in merged:
            report_date = ts.astimezone(ET).date()
            rows_to_insert.append({
                "symbol": symbol,
                "fiscal_quarter": _quarter_label(report_date),
                "report_date": report_date,
                "report_time": _classify_bmo_amc(ts),
                "confirmed": True,
                "source": src,
            })

    log.info(f"Merged: {len(rows_to_insert)} confirmed earnings entries "
             f"across {len(by_symbol)} symbols")

    # ── Source 3 (optional): 10-Q/10-K fallback ─────────────────
    fallback_count = 0
    if args.include_filing_fallback:
        confirmed_keys = {(r["symbol"], r["fiscal_quarter"]) for r in rows_to_insert}

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (symbol, DATE_TRUNC('quarter', published_ts))
                    symbol, published_ts, event_type
                FROM equities_events
                WHERE event_type IN ('SEC_10-Q', 'SEC_10-K')
                ORDER BY symbol, DATE_TRUNC('quarter', published_ts), published_ts ASC;
                """
            )
            filing_events = list(cur.fetchall())

        for ev in filing_events:
            report_date = ev["published_ts"].astimezone(ET).date()
            fq = _quarter_label(report_date)
            key = (ev["symbol"], fq)
            if key not in confirmed_keys:
                rows_to_insert.append({
                    "symbol": ev["symbol"],
                    "fiscal_quarter": fq,
                    "report_date": report_date,
                    "report_time": _classify_bmo_amc(ev["published_ts"]),
                    "confirmed": False,
                    "source": "SEC_FILING",
                })
                confirmed_keys.add(key)
                fallback_count += 1

        log.info(f"Source 3 fallback: {fallback_count} unconfirmed entries from "
                 f"10-Q/10-K filings")

    # ── Insert ──────────────────────────────────────────────────
    if rows_to_insert:
        n = upsert_earnings_calendar(conn, rows_to_insert)
        log.info(f"Upserted {n} earnings calendar entries")
    else:
        log.warning("No earnings entries found — calendar is empty")

    # ── Summary ─────────────────────────────────────────────────
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(DISTINCT symbol) AS symbols,
                   COUNT(*) FILTER (WHERE confirmed) AS confirmed,
                   COUNT(*) FILTER (WHERE NOT confirmed) AS unconfirmed
            FROM equities_earnings_calendar;
            """
        )
        summary = cur.fetchone()

    log.info("─── Final Summary ───")
    log.info(f"  Total entries:    {summary['total']}")
    log.info(f"  Distinct symbols: {summary['symbols']}")
    log.info(f"  Confirmed:        {summary['confirmed']}")
    log.info(f"  Unconfirmed:      {summary['unconfirmed']}")
    log.info("Done.")

    conn.close()


if __name__ == "__main__":
    main()
