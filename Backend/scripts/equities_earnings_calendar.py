#!/usr/bin/env python3
"""Populate equities_earnings_calendar from SEC EDGAR submissions (10-Q/10-K proxy)."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date, datetime, time as clock_time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from equities_predictor.db import connect, log_pipeline_event
from equities_predictor.sources import SEC_HEADERS
from equities_predictor.universe import get_default_symbols

UTC = timezone.utc
ET_TZ = ZoneInfo("America/New_York")
BASE_SEC_URL = "https://data.sec.gov/submissions"
TARGET_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}
EARLIEST_AWARE_DT = datetime(1900, 1, 1, tzinfo=UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate equities_earnings_calendar using SEC EDGAR submissions (10-Q/10-K filings)."
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="G2A_EARNINGS_CALENDAR")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol list. Default: universe symbols.")
    parser.add_argument("--max-symbols", type=int, default=0, help="Optional cap for testing; 0 means no cap.")
    parser.add_argument(
        "--max-historical-files",
        type=int,
        default=0,
        help="Optional cap on SEC historical submission files per symbol; 0 means fetch all available files.",
    )
    parser.add_argument("--request-timeout", type=int, default=30, help="SEC request timeout in seconds.")
    parser.add_argument("--max-retries", type=int, default=3, help="Retry attempts per SEC request.")
    parser.add_argument("--sleep-seconds", type=float, default=0.20, help="Polite delay after each SEC request.")
    parser.add_argument("--sec-user-agent", default=SEC_HEADERS.get("User-Agent"))
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def _configure_logging(level: str) -> None:
    resolved = str(level or "INFO").upper().strip()
    logging.basicConfig(
        level=getattr(logging, resolved, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _normalize_symbol(symbol: str) -> str:
    return "".join(ch for ch in str(symbol or "").upper() if ch.isalnum())


def _select_symbols(symbols_csv: str | None, max_symbols: int) -> list[str]:
    if symbols_csv:
        raw = [token.strip().upper() for token in symbols_csv.split(",") if token.strip()]
    else:
        raw = [symbol.upper() for symbol in get_default_symbols()]

    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in raw:
        if symbol in seen:
            continue
        seen.add(symbol)
        ordered.append(symbol)

    if max_symbols > 0:
        return ordered[: max_symbols]
    return ordered


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    token = raw[:10]
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    candidates = [
        raw,
        raw.replace("Z", "+00:00"),
    ]
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            continue

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y%m%d%H%M%S"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=UTC)
            return dt
        except ValueError:
            continue

    return None


def _infer_report_time(accepted_at: datetime | None) -> str:
    if accepted_at is None:
        return "TBD"
    local = accepted_at.astimezone(ET_TZ)
    local_time = local.time()
    if local_time < clock_time(9, 30):
        return "BMO"
    if local_time >= clock_time(16, 0):
        return "AMC"
    return "TBD"


def _fiscal_quarter(form_type: str, period_end: date) -> str:
    ft = (form_type or "").upper()
    if ft.startswith("10-K"):
        return f"{period_end.year}FY"
    quarter = ((period_end.month - 1) // 3) + 1
    return f"{period_end.year}Q{quarter}"


def _source_value(form_type: str) -> str:
    return f"SEC_EDGAR_{(form_type or '').upper().replace('/', '_')}"


def _safe_list(block: dict[str, Any], key: str) -> list[Any]:
    raw = block.get(key) or []
    return raw if isinstance(raw, list) else []


def _at(values: list[Any], idx: int) -> Any:
    if 0 <= idx < len(values):
        return values[idx]
    return None


def _candidate_rank(candidate: dict[str, Any]) -> tuple[date, int, datetime, int]:
    report_date = candidate.get("report_date") or date.min
    report_time = candidate.get("report_time")
    accepted_at = candidate.get("accepted_at") or EARLIEST_AWARE_DT
    form_type = str(candidate.get("form_type") or "")
    non_amended = 1 if not form_type.endswith("/A") else 0
    has_time_bucket = 1 if report_time in {"BMO", "AMC"} else 0
    return (report_date, has_time_bucket, accepted_at, non_amended)


def _prefer_candidate(existing: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return candidate
    if _candidate_rank(candidate) >= _candidate_rank(existing):
        return candidate
    return existing


class SecClient:
    """Minimal SEC client with retries + polite throttling."""

    def __init__(
        self,
        *,
        user_agent: str | None,
        timeout_seconds: int,
        max_retries: int,
        sleep_seconds: float,
    ) -> None:
        headers = dict(SEC_HEADERS)
        if user_agent:
            headers["User-Agent"] = str(user_agent)
        self._headers = headers
        self._timeout = max(5, int(timeout_seconds))
        self._max_retries = max(0, int(max_retries))
        self._sleep_seconds = max(0.0, float(sleep_seconds))
        self._session = requests.Session()

        self.requests_total = 0
        self.request_failures = 0
        self.retries_used = 0

    def close(self) -> None:
        self._session.close()

    def get_json(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self.requests_total += 1
            try:
                response = self._session.get(url, headers=self._headers, timeout=self._timeout)
                if response.status_code == 404:
                    logging.warning("SEC endpoint returned 404 for %s", url)
                    return {}
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError(f"Unexpected SEC payload type: {type(payload)!r}")
                return payload
            except Exception as exc:
                self.request_failures += 1
                last_error = exc
                if attempt >= self._max_retries:
                    break
                self.retries_used += 1
                backoff = self._sleep_seconds * (attempt + 1)
                logging.warning(
                    "SEC request failed (%s/%s) for %s: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    url,
                    exc,
                )
                if backoff > 0:
                    time.sleep(backoff)
            finally:
                if self._sleep_seconds > 0:
                    time.sleep(self._sleep_seconds)
        raise RuntimeError(f"SEC request failed after retries: {url}") from last_error


def _safe_log_pipeline_event(
    conn,
    *,
    actor: str,
    event_type: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    try:
        log_pipeline_event(
            conn,
            actor=actor,
            event_type=event_type,
            message=message,
            data=data or {},
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logging.warning("Failed to write pipeline event %s: %s", event_type, exc)


def _ensure_target_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.equities_earnings_calendar') AS reg;")
        row = cur.fetchone()
    if not row or row["reg"] is None:
        raise RuntimeError(
            "Target table public.equities_earnings_calendar does not exist. "
            "Apply migration 20260223_add_equities_earnings_calendar.sql first."
        )


def _fetch_ticker_index(client: SecClient) -> tuple[dict[str, str], dict[str, str]]:
    payload = client.get_json("https://www.sec.gov/files/company_tickers.json")

    direct: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        ticker = str(value.get("ticker", "")).upper().strip()
        cik_raw = str(value.get("cik_str", "")).strip()
        if not ticker or not cik_raw:
            continue
        cik = cik_raw.zfill(10)
        direct[ticker] = cik
        ticker_norm = _normalize_symbol(ticker)
        if ticker_norm:
            normalized[ticker_norm] = cik
    return direct, normalized


def _resolve_symbol_cik(symbol: str, direct: dict[str, str], normalized: dict[str, str]) -> str | None:
    upper = symbol.upper().strip()
    if upper in direct:
        return direct[upper]
    return normalized.get(_normalize_symbol(upper))


def _extract_block_candidates(
    *,
    symbol: str,
    block: dict[str, Any],
    by_quarter: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    forms = _safe_list(block, "form")
    filing_dates = _safe_list(block, "filingDate")
    report_dates = _safe_list(block, "reportDate")
    acceptance_dates = _safe_list(block, "acceptanceDateTime")

    filings_seen = 0
    filings_matched = 0

    for idx, raw_form in enumerate(forms):
        filings_seen += 1
        form_type = str(raw_form or "").upper().strip()
        if form_type not in TARGET_FORMS:
            continue

        filing_date = _parse_date(_at(filing_dates, idx))
        if filing_date is None:
            continue
        period_end = _parse_date(_at(report_dates, idx)) or filing_date
        accepted_at = _parse_datetime(_at(acceptance_dates, idx))

        candidate = {
            "symbol": symbol,
            "fiscal_quarter": _fiscal_quarter(form_type, period_end),
            "report_date": filing_date,
            "report_time": _infer_report_time(accepted_at),
            "confirmed": True,
            "source": _source_value(form_type),
            "accepted_at": accepted_at,
            "form_type": form_type,
        }
        key = candidate["fiscal_quarter"]
        by_quarter[key] = _prefer_candidate(by_quarter.get(key), candidate)
        filings_matched += 1

    return filings_seen, filings_matched


def _collect_symbol_rows(
    client: SecClient,
    *,
    symbol: str,
    cik: str,
    max_historical_files: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = {
        "filings_seen": 0,
        "filings_matched": 0,
        "historical_files_discovered": 0,
        "historical_files_fetched": 0,
    }

    by_quarter: dict[str, dict[str, Any]] = {}

    root = client.get_json(f"{BASE_SEC_URL}/CIK{cik}.json")
    recent_block = root.get("filings", {}).get("recent", {})
    seen, matched = _extract_block_candidates(symbol=symbol, block=recent_block, by_quarter=by_quarter)
    stats["filings_seen"] += seen
    stats["filings_matched"] += matched

    historical_files = root.get("filings", {}).get("files") or []
    if not isinstance(historical_files, list):
        historical_files = []
    stats["historical_files_discovered"] = len(historical_files)

    if max_historical_files > 0:
        historical_files = historical_files[:max_historical_files]

    for file_meta in historical_files:
        if not isinstance(file_meta, dict):
            continue
        file_name = str(file_meta.get("name") or "").strip()
        if not file_name:
            continue
        older = client.get_json(f"{BASE_SEC_URL}/{file_name}")
        older_block = older.get("filings", {}).get("recent", {})
        seen, matched = _extract_block_candidates(symbol=symbol, block=older_block, by_quarter=by_quarter)
        stats["filings_seen"] += seen
        stats["filings_matched"] += matched
        stats["historical_files_fetched"] += 1

    rows: list[dict[str, Any]] = []
    for fiscal_quarter, item in by_quarter.items():
        rows.append(
            {
                "symbol": item["symbol"],
                "fiscal_quarter": fiscal_quarter,
                "report_date": item["report_date"],
                "report_time": item["report_time"],
                "confirmed": item["confirmed"],
                "source": item["source"],
            }
        )
    rows.sort(key=lambda row: row["fiscal_quarter"])
    return rows, stats


def _upsert_rows(conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO equities_earnings_calendar (
                symbol, fiscal_quarter, report_date, report_time, confirmed, source
            )
            VALUES (
                %(symbol)s, %(fiscal_quarter)s, %(report_date)s, %(report_time)s, %(confirmed)s, %(source)s
            )
            ON CONFLICT (symbol, fiscal_quarter) DO UPDATE SET
                report_date = EXCLUDED.report_date,
                report_time = EXCLUDED.report_time,
                confirmed = EXCLUDED.confirmed,
                source = EXCLUDED.source;
            """,
            rows,
        )
    return len(rows)


def populate_earnings_calendar(
    *,
    database_url: str | None,
    actor: str,
    symbols_csv: str | None,
    max_symbols: int,
    max_historical_files: int,
    request_timeout: int,
    max_retries: int,
    sleep_seconds: float,
    sec_user_agent: str | None,
) -> dict[str, Any]:
    started = time.monotonic()
    symbols = _select_symbols(symbols_csv, max_symbols=max_symbols)

    summary: dict[str, Any] = {
        "symbols_total": len(symbols),
        "symbols_with_cik": 0,
        "symbols_processed": 0,
        "symbols_no_cik": 0,
        "symbols_failed": 0,
        "filings_seen": 0,
        "filings_matched_10q_10k": 0,
        "historical_files_discovered": 0,
        "historical_files_fetched": 0,
        "rows_upserted": 0,
        "errors": 0,
        "max_historical_files": max_historical_files if max_historical_files > 0 else None,
    }

    conn = connect(database_url)
    sec = SecClient(
        user_agent=sec_user_agent,
        timeout_seconds=request_timeout,
        max_retries=max_retries,
        sleep_seconds=sleep_seconds,
    )

    try:
        _ensure_target_table(conn)
        _safe_log_pipeline_event(
            conn,
            actor=actor,
            event_type="EARNINGS_CALENDAR_SYNC_START",
            message="SEC EDGAR earnings calendar sync started",
            data={
                "symbols": len(symbols),
                "max_historical_files": summary["max_historical_files"],
                "sleep_seconds": sleep_seconds,
            },
        )

        direct_tickers, normalized_tickers = _fetch_ticker_index(sec)

        for symbol in symbols:
            cik = _resolve_symbol_cik(symbol, direct_tickers, normalized_tickers)
            if not cik:
                summary["symbols_no_cik"] += 1
                logging.warning("No SEC CIK found for symbol %s; skipping.", symbol)
                continue

            summary["symbols_with_cik"] += 1
            try:
                rows, stats = _collect_symbol_rows(
                    sec,
                    symbol=symbol,
                    cik=cik,
                    max_historical_files=max_historical_files,
                )
                touched = _upsert_rows(conn, rows)
                conn.commit()

                summary["symbols_processed"] += 1
                summary["rows_upserted"] += touched
                summary["filings_seen"] += stats["filings_seen"]
                summary["filings_matched_10q_10k"] += stats["filings_matched"]
                summary["historical_files_discovered"] += stats["historical_files_discovered"]
                summary["historical_files_fetched"] += stats["historical_files_fetched"]

                logging.info(
                    "Processed %s (CIK %s): %s quarters upserted, %s matching filings.",
                    symbol,
                    cik,
                    touched,
                    stats["filings_matched"],
                )
            except Exception as exc:
                conn.rollback()
                summary["symbols_failed"] += 1
                summary["errors"] += 1
                logging.exception("Failed processing symbol %s (CIK %s): %s", symbol, cik, exc)
                _safe_log_pipeline_event(
                    conn,
                    actor=actor,
                    event_type="EARNINGS_CALENDAR_SYMBOL_ERROR",
                    message=f"Earnings calendar symbol sync failed for {symbol}",
                    data={
                        "symbol": symbol,
                        "cik": cik,
                        "error": str(exc),
                    },
                )

        summary["sec_requests_total"] = sec.requests_total
        summary["sec_request_failures"] = sec.request_failures
        summary["sec_retries_used"] = sec.retries_used
        summary["duration_seconds"] = round(time.monotonic() - started, 3)

        _safe_log_pipeline_event(
            conn,
            actor=actor,
            event_type="EARNINGS_CALENDAR_SYNC_COMPLETE",
            message="SEC EDGAR earnings calendar sync completed",
            data=summary,
        )
        return summary
    except Exception as exc:
        conn.rollback()
        summary["errors"] += 1
        summary["sec_requests_total"] = sec.requests_total
        summary["sec_request_failures"] = sec.request_failures
        summary["sec_retries_used"] = sec.retries_used
        summary["duration_seconds"] = round(time.monotonic() - started, 3)
        _safe_log_pipeline_event(
            conn,
            actor=actor,
            event_type="EARNINGS_CALENDAR_SYNC_FAILED",
            message="SEC EDGAR earnings calendar sync failed",
            data={"error": str(exc), **summary},
        )
        raise
    finally:
        sec.close()
        conn.close()


def main() -> int:
    args = _parse_args()
    _configure_logging(args.log_level)
    try:
        summary = populate_earnings_calendar(
            database_url=args.database_url,
            actor=args.actor,
            symbols_csv=args.symbols,
            max_symbols=max(0, int(args.max_symbols)),
            max_historical_files=max(0, int(args.max_historical_files)),
            request_timeout=max(5, int(args.request_timeout)),
            max_retries=max(0, int(args.max_retries)),
            sleep_seconds=max(0.0, float(args.sleep_seconds)),
            sec_user_agent=args.sec_user_agent,
        )
    except Exception as exc:
        logging.error("Earnings calendar sync failed: %s", exc)
        return 1

    print(json.dumps(summary, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
