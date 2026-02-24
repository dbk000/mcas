#!/usr/bin/env python3
"""One-shot 2y hourly OHLCV backfill for the full equities universe."""

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from multiprocessing.pool import Pool

from equities_ohlcv_ingestor import ingest
from equities_predictor.calendar import ET, is_trading_day
from equities_predictor.db import connect
from equities_predictor.sources import fetch_stooq_daily, polite_sleep
from equities_predictor.universe import get_default_symbols

LOOKBACK_DAYS_DEFAULT = 730
MAX_WORKERS_CAP = 3
POLITE_SLEEP_SECONDS = 2.0
BACKOFF_SECONDS = 60.0
PROGRESS_EVERY = 5
LOW_COVERAGE_THRESHOLD = 0.50
CONSECUTIVE_FAIL_BAR_FLOOR = 10
EQUITIES_SOURCES = ("equities_yahoo", "equities_stooq")


@dataclass
class SymbolResult:
    symbol: str
    expected_bars: float
    first_pass_bars: int = 0
    final_bars: int = 0
    retried: bool = False
    stooq_daily_bars: int = 0
    error: str | None = None
    retry_error: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def is_low_coverage(self) -> bool:
        return self.final_bars < (self.expected_bars * LOW_COVERAGE_THRESHOLD)

    @property
    def is_success(self) -> bool:
        return not self.is_low_coverage and self.error is None


@dataclass(frozen=True)
class WorkerInput:
    symbol: str
    database_url: str | None
    actor: str
    lookback_days: int
    expected_bars: float


def _fmt_seconds(total_seconds: float | None) -> str:
    if total_seconds is None or not math.isfinite(total_seconds):
        return "n/a"
    total = max(0, int(round(total_seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _count_trading_days(start_d: date, end_d: date) -> int:
    if end_d < start_d:
        return 0
    cur = start_d
    days = 0
    while cur <= end_d:
        if is_trading_day(cur):
            days += 1
        cur += timedelta(days=1)
    return days


def _expected_hourly_bars(lookback_days: int) -> tuple[int, float]:
    now_et = datetime.now(timezone.utc).astimezone(ET)
    start_d = (now_et - timedelta(days=lookback_days)).date()
    end_d = now_et.date()
    trading_days = _count_trading_days(start_d, end_d)
    return trading_days, float(trading_days) * 6.5


def _ingest_single_symbol(
    *,
    database_url: str | None,
    symbol: str,
    actor: str,
    lookback_days: int,
    batch_tag: str,
) -> int:
    summary = ingest(
        database_url=database_url,
        symbols=[symbol],
        timeframes=["1h"],
        lookback_days=lookback_days,
        shard=None,
        shards=None,
        batch_id=f"{batch_tag}_{symbol}_{int(time.time())}",
        actor=actor,
    )
    return int(summary.get("inserted_total", 0) or 0)


def _count_persisted_hourly_bars(database_url: str | None, symbol: str, lookback_days: int) -> int:
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=lookback_days)
    conn = connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT bar_start_ts)::int AS bars
                FROM ohlcv
                WHERE symbol = %s
                  AND timeframe = '1h'
                  AND is_final = TRUE
                  AND source = ANY(%s::text[])
                  AND bar_start_ts >= %s
                  AND bar_start_ts <= %s;
                """,
                (symbol, list(EQUITIES_SOURCES), start_ts, end_ts),
            )
            row = cur.fetchone() or {}
            return int(row.get("bars") or 0)
    finally:
        conn.close()


def _stooq_probe_bars(database_url: str | None, symbol: str, lookback_days: int) -> int:
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=lookback_days)
    conn = connect(database_url)
    try:
        rows = fetch_stooq_daily(conn, symbol=symbol, start_ts=start_ts, end_ts=end_ts)
        return len(rows)
    finally:
        conn.close()


def _backfill_worker(payload: WorkerInput) -> SymbolResult:
    started = time.monotonic()
    result = SymbolResult(symbol=payload.symbol, expected_bars=payload.expected_bars)

    try:
        _ingest_single_symbol(
            database_url=payload.database_url,
            symbol=payload.symbol,
            actor=payload.actor,
            lookback_days=payload.lookback_days,
            batch_tag="eq_hourly_backfill",
        )
        result.first_pass_bars = _count_persisted_hourly_bars(
            payload.database_url,
            payload.symbol,
            payload.lookback_days,
        )
        result.final_bars = result.first_pass_bars
    except Exception as exc:
        result.error = str(exc)
        result.final_bars = 0

    if result.first_pass_bars < (payload.expected_bars * LOW_COVERAGE_THRESHOLD):
        result.retried = True
        try:
            result.stooq_daily_bars = _stooq_probe_bars(
                payload.database_url,
                payload.symbol,
                payload.lookback_days,
            )
        except Exception as exc:
            result.retry_error = f"stooq_probe_failed: {exc}"

        try:
            _ingest_single_symbol(
                database_url=payload.database_url,
                symbol=payload.symbol,
                actor=payload.actor,
                lookback_days=payload.lookback_days,
                batch_tag="eq_hourly_backfill_retry",
            )
            retry_bars = _count_persisted_hourly_bars(
                payload.database_url,
                payload.symbol,
                payload.lookback_days,
            )
            result.final_bars = max(result.final_bars, retry_bars)
            if retry_bars > 0 and result.error:
                if result.retry_error:
                    result.retry_error = f"{result.retry_error}; initial_ingest_failed: {result.error}"
                else:
                    result.retry_error = f"initial_ingest_failed: {result.error}"
                result.error = None
        except Exception as exc:
            if result.retry_error:
                result.retry_error = f"{result.retry_error}; ingest_retry_failed: {exc}"
            else:
                result.retry_error = f"ingest_retry_failed: {exc}"

    result.elapsed_seconds = time.monotonic() - started
    polite_sleep(POLITE_SLEEP_SECONDS)
    return result


def _is_timeout_or_429(message: str | None) -> bool:
    if not message:
        return False
    text = message.lower()
    return (
        "429" in text
        or "too many requests" in text
        or "timed out" in text
        or "timeout" in text
        or "read timeout" in text
    )


def _is_consecutive_fail(result: SymbolResult) -> bool:
    if result.final_bars < CONSECUTIVE_FAIL_BAR_FLOOR:
        return True
    if _is_timeout_or_429(result.error):
        return True
    if _is_timeout_or_429(result.retry_error):
        return True
    return False


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_backfill(
    *,
    database_url: str | None,
    actor: str,
    symbols: list[str],
    lookback_days: int,
    workers: int,
) -> dict[str, object]:
    started = time.monotonic()

    trading_days, expected_bars = _expected_hourly_bars(lookback_days)
    total = len(symbols)
    capped_workers = max(1, min(MAX_WORKERS_CAP, workers))

    print(
        f"[start] symbols={total} lookback_days={lookback_days} trading_days={trading_days} "
        f"expected_hourly_bars_per_symbol={expected_bars:.1f} workers={capped_workers}"
    )

    processed = 0
    success_count = 0
    fail_count = 0
    retry_count = 0
    consecutive_failures = 0

    with Pool(processes=capped_workers) as pool:
        for symbol_batch in _chunked(symbols, capped_workers):
            payloads = [
                WorkerInput(
                    symbol=symbol,
                    database_url=database_url,
                    actor=actor,
                    lookback_days=lookback_days,
                    expected_bars=expected_bars,
                )
                for symbol in symbol_batch
            ]
            batch_results = pool.map(_backfill_worker, payloads)

            for res in batch_results:
                processed += 1
                if res.retried:
                    retry_count += 1
                    print(
                        f"[warn] {res.symbol} low hourly coverage {res.first_pass_bars}/{res.expected_bars:.1f}; "
                        f"retrying with stooq fallback probe (daily bars={res.stooq_daily_bars})"
                    )

                if res.is_success:
                    success_count += 1
                    print(
                        f"[ok] {res.symbol} bars={res.final_bars} "
                        f"elapsed={_fmt_seconds(res.elapsed_seconds)}"
                    )
                else:
                    fail_count += 1
                    print(
                        f"[fail] {res.symbol} bars={res.final_bars} "
                        f"expected={res.expected_bars:.1f} "
                        f"error={res.error or '-'} retry_error={res.retry_error or '-'}"
                    )

                if _is_consecutive_fail(res):
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= 3:
                    print(
                        f"[backoff] {consecutive_failures} consecutive failures "
                        f"(429/timeout/<{CONSECUTIVE_FAIL_BAR_FLOOR} bars). Sleeping {int(BACKOFF_SECONDS)}s."
                    )
                    polite_sleep(BACKOFF_SECONDS)
                    consecutive_failures = 0

                if (processed % PROGRESS_EVERY == 0) or (processed == total):
                    elapsed = time.monotonic() - started
                    rate = processed / elapsed if elapsed > 0 else 0.0
                    remaining = total - processed
                    eta = (remaining / rate) if rate > 0 else None
                    print(
                        f"[progress] {processed}/{total} elapsed={_fmt_seconds(elapsed)} "
                        f"eta={_fmt_seconds(eta)} success={success_count} "
                        f"fail={fail_count} retried={retry_count}"
                    )

    elapsed_total = time.monotonic() - started
    summary = {
        "symbols_total": total,
        "lookback_days": lookback_days,
        "trading_days": trading_days,
        "expected_hourly_bars_per_symbol": expected_bars,
        "workers": capped_workers,
        "success_count": success_count,
        "fail_count": fail_count,
        "retry_count": retry_count,
        "elapsed_seconds": round(elapsed_total, 3),
    }
    print(f"[done] elapsed={_fmt_seconds(elapsed_total)} summary={summary}")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill 2y equities hourly OHLCV for all symbols")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--symbols", default=",".join(get_default_symbols()))
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS_DEFAULT)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS_CAP)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    run_backfill(
        database_url=args.database_url,
        actor=args.actor,
        symbols=symbols,
        lookback_days=max(1, int(args.lookback_days)),
        workers=max(1, int(args.workers)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
