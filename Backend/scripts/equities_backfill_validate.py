#!/usr/bin/env python3
"""Validate 1h equities backfill completeness for the default universe."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from equities_predictor.calendar import ET, is_trading_day
from equities_predictor.db import connect
from equities_predictor.universe import get_default_symbols

EQUITIES_SOURCES: tuple[str, str] = ("equities_yahoo", "equities_stooq")
TIMEFRAME = "1h"
BARS_PER_TRADING_DAY = 6.5

FAIL_COVERAGE_PCT = 80.0
WARN_COVERAGE_PCT = 95.0
STALE_MAX_DAYS = 7.0
WARN_MAX_CONSECUTIVE_GAP_TRADING_DAYS = 5

DEFAULT_OUTPUT_PATH = Path(__file__).with_name("equities_backfill_validation_results.json")


@dataclass(frozen=True)
class SymbolAggregate:
    symbol: str
    raw_rows: int
    distinct_bars: int
    min_ts: datetime | None
    max_ts: datetime | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Database URL override. Defaults to DATABASE_URL env var.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path to write validation JSON results.",
    )
    return parser.parse_args()


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def _to_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _count_trading_days(start_d: date, end_d: date) -> int:
    if end_d < start_d:
        return 0
    count = 0
    cur = start_d
    while cur <= end_d:
        if is_trading_day(cur):
            count += 1
        cur += timedelta(days=1)
    return count


def _longest_missing_trading_day_run(
    *,
    observed_dates: set[date],
    start_d: date,
    end_d: date,
) -> int:
    if end_d < start_d:
        return 0
    longest = 0
    run = 0
    cur = start_d
    while cur <= end_d:
        if is_trading_day(cur):
            if cur in observed_dates:
                run = 0
            else:
                run += 1
                if run > longest:
                    longest = run
        cur += timedelta(days=1)
    return longest


def _load_symbol_aggregates(conn, symbols: list[str]) -> dict[str, SymbolAggregate]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                symbol,
                COUNT(*)::bigint AS raw_rows,
                COUNT(DISTINCT bar_start_ts)::bigint AS distinct_bars,
                MIN(bar_start_ts) AS min_ts,
                MAX(bar_start_ts) AS max_ts
            FROM ohlcv
            WHERE symbol = ANY(%s::text[])
              AND timeframe = %s
              AND source = ANY(%s::text[])
              AND is_final = TRUE
            GROUP BY symbol
            ORDER BY symbol;
            """,
            (symbols, TIMEFRAME, list(EQUITIES_SOURCES)),
        )
        out: dict[str, SymbolAggregate] = {}
        for row in cur.fetchall():
            out[row["symbol"]] = SymbolAggregate(
                symbol=row["symbol"],
                raw_rows=int(row["raw_rows"] or 0),
                distinct_bars=int(row["distinct_bars"] or 0),
                min_ts=_to_utc(row.get("min_ts")),
                max_ts=_to_utc(row.get("max_ts")),
            )
        return out


def _load_source_rows(conn, symbols: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                symbol,
                source,
                COUNT(*)::bigint AS raw_rows,
                COUNT(DISTINCT bar_start_ts)::bigint AS distinct_bars,
                MIN(bar_start_ts) AS min_ts,
                MAX(bar_start_ts) AS max_ts
            FROM ohlcv
            WHERE symbol = ANY(%s::text[])
              AND timeframe = %s
              AND source = ANY(%s::text[])
              AND is_final = TRUE
            GROUP BY symbol, source
            ORDER BY symbol, source;
            """,
            (symbols, TIMEFRAME, list(EQUITIES_SOURCES)),
        )
        by_symbol: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in cur.fetchall():
            source = str(row["source"])
            by_symbol[row["symbol"]][source] = {
                "raw_rows": int(row["raw_rows"] or 0),
                "distinct_bars": int(row["distinct_bars"] or 0),
                "min_ts": _iso(_to_utc(row.get("min_ts"))),
                "max_ts": _iso(_to_utc(row.get("max_ts"))),
            }
        return by_symbol


def _load_trading_day_presence(conn, symbols: list[str]) -> dict[str, set[date]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT
                symbol,
                (bar_start_ts AT TIME ZONE 'America/New_York')::date AS trading_date
            FROM ohlcv
            WHERE symbol = ANY(%s::text[])
              AND timeframe = %s
              AND source = ANY(%s::text[])
              AND is_final = TRUE
            ORDER BY symbol, trading_date;
            """,
            (symbols, TIMEFRAME, list(EQUITIES_SOURCES)),
        )
        out: dict[str, set[date]] = defaultdict(set)
        for row in cur.fetchall():
            trading_date = row["trading_date"]
            if isinstance(trading_date, date):
                out[row["symbol"]].add(trading_date)
        return out


def _default_source_payload() -> dict[str, dict[str, Any]]:
    return {
        source: {
            "raw_rows": 0,
            "distinct_bars": 0,
            "min_ts": None,
            "max_ts": None,
        }
        for source in EQUITIES_SOURCES
    }


def run_validation(*, database_url: str | None = None) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    symbols = list(get_default_symbols())
    conn = connect(database_url)
    try:
        aggregates = _load_symbol_aggregates(conn, symbols)
        source_rows = _load_source_rows(conn, symbols)
        trading_day_presence = _load_trading_day_presence(conn, symbols)
    finally:
        conn.close()

    symbol_results: list[dict[str, Any]] = []
    pass_count = 0
    warn_count = 0
    fail_count = 0

    for symbol in symbols:
        agg = aggregates.get(symbol)
        source_detail = _default_source_payload()
        source_detail.update(source_rows.get(symbol, {}))

        fail_reasons: list[str] = []
        warn_reasons: list[str] = []

        expected_bars: float | None = None
        trading_days: int | None = None
        coverage_pct: float | None = None
        latest_age_days: float | None = None
        longest_missing_td_gap = 0
        range_start_et: str | None = None
        range_end_et: str | None = None
        min_ts: datetime | None = None
        max_ts: datetime | None = None
        observed_distinct_bars = 0
        observed_raw_rows = 0

        if agg is None or agg.distinct_bars <= 0 or agg.min_ts is None or agg.max_ts is None:
            fail_reasons.append("symbol_missing")
        else:
            min_ts = agg.min_ts
            max_ts = agg.max_ts
            observed_distinct_bars = agg.distinct_bars
            observed_raw_rows = agg.raw_rows

            start_d = min_ts.astimezone(ET).date()
            end_d = max_ts.astimezone(ET).date()
            range_start_et = start_d.isoformat()
            range_end_et = end_d.isoformat()

            trading_days = _count_trading_days(start_d, end_d)
            expected_bars = float(trading_days) * BARS_PER_TRADING_DAY
            if expected_bars > 0:
                coverage_pct = (observed_distinct_bars / expected_bars) * 100.0
                if coverage_pct < FAIL_COVERAGE_PCT:
                    fail_reasons.append("coverage_below_80pct")
                elif coverage_pct <= WARN_COVERAGE_PCT:
                    warn_reasons.append("coverage_between_80pct_and_95pct")

            latest_age_days = max(0.0, (now_utc - max_ts).total_seconds() / 86_400.0)
            if latest_age_days > STALE_MAX_DAYS:
                fail_reasons.append("latest_bar_older_than_7d")

            observed_dates = trading_day_presence.get(symbol, set())
            longest_missing_td_gap = _longest_missing_trading_day_run(
                observed_dates=observed_dates,
                start_d=start_d,
                end_d=end_d,
            )
            if longest_missing_td_gap > WARN_MAX_CONSECUTIVE_GAP_TRADING_DAYS:
                warn_reasons.append("gap_over_5_consecutive_trading_days")

        if fail_reasons:
            status = "FAIL"
            fail_count += 1
        elif warn_reasons:
            status = "WARN"
            warn_count += 1
        else:
            status = "PASS"
            pass_count += 1

        symbol_results.append(
            {
                "symbol": symbol,
                "status": status,
                "fail_reasons": fail_reasons,
                "warn_reasons": warn_reasons,
                "metrics": {
                    "observed_distinct_bars": observed_distinct_bars,
                    "observed_raw_rows": observed_raw_rows,
                    "expected_bars": _round(expected_bars, 1),
                    "coverage_pct": _round(coverage_pct, 2),
                    "range_start_ts": _iso(min_ts),
                    "range_end_ts": _iso(max_ts),
                    "range_start_date_et": range_start_et,
                    "range_end_date_et": range_end_et,
                    "trading_days_in_range": trading_days,
                    "latest_bar_age_days": _round(latest_age_days, 2),
                    "longest_missing_trading_day_gap": longest_missing_td_gap,
                },
                "sources": source_detail,
            }
        )

    exit_code = 1 if fail_count > 0 else 0
    return {
        "checked_at": now_utc.isoformat(),
        "timeframe": TIMEFRAME,
        "sources": list(EQUITIES_SOURCES),
        "default_symbol_count": len(symbols),
        "thresholds": {
            "fail_coverage_pct_lt": FAIL_COVERAGE_PCT,
            "warn_coverage_pct_lte": WARN_COVERAGE_PCT,
            "fail_latest_bar_age_days_gt": STALE_MAX_DAYS,
            "warn_gap_trading_days_gt": WARN_MAX_CONSECUTIVE_GAP_TRADING_DAYS,
            "bars_per_trading_day_expected": BARS_PER_TRADING_DAY,
        },
        "summary": {
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "exit_code": exit_code,
        },
        "symbols": symbol_results,
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)

    try:
        payload = run_validation(database_url=args.database_url)
    except Exception as exc:  # pragma: no cover - operational guardrail
        payload = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "timeframe": TIMEFRAME,
            "sources": list(EQUITIES_SOURCES),
            "summary": {"pass": 0, "warn": 0, "fail": 1, "exit_code": 1},
            "error": str(exc),
        }

    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{rendered}\n", encoding="utf-8")

    return int(payload.get("summary", {}).get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
