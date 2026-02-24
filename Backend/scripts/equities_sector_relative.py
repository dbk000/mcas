#!/usr/bin/env python3
"""Compute sector-relative and SPY-relative return features for equities."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from equities_predictor.db import connect, log_pipeline_event
from equities_predictor.universe import get_default_symbols, get_universe_rows

LOOKBACK_DAYS: tuple[int, int, int] = (1, 5, 20)
FEATURE_KEY_BY_DAYS = {
    1: ("sector_relative_1d", "spy_relative_1d"),
    5: ("sector_relative_5d", "spy_relative_5d"),
    20: ("sector_relative_20d", "spy_relative_20d"),
}

SECTOR_TO_ETF: dict[str, str] = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Index ETF": "SPY",
    "Sector ETF": "SPY",
}

SOURCE_PRIORITY = ("equities_yahoo", "equities_stooq")

logger = logging.getLogger("equities_sector_relative")


@dataclass(frozen=True)
class BarSeries:
    timestamps: list[datetime]
    closes: list[float]

    def return_over_days(self, as_of_ts: datetime, lookback_days: int) -> float | None:
        idx_cur = bisect_right(self.timestamps, as_of_ts) - 1
        if idx_cur < 0:
            return None

        target_ts = as_of_ts - timedelta(days=lookback_days)
        idx_prev = bisect_right(self.timestamps, target_ts) - 1
        if idx_prev < 0 or idx_prev >= idx_cur:
            return None

        cur_close = self.closes[idx_cur]
        prev_close = self.closes[idx_prev]
        if prev_close <= 0:
            return None
        return (cur_close / prev_close) - 1.0


def _parse_symbols_arg(raw: str | None) -> list[str]:
    if not raw:
        return list(get_default_symbols())
    out = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return out or list(get_default_symbols())


def _load_sector_map() -> dict[str, str]:
    return {
        str(row["symbol"]).upper(): str(row.get("sector") or "")
        for row in get_universe_rows()
    }


def _sector_etf_for_symbol(symbol: str, sector_map: dict[str, str]) -> str:
    sector = sector_map.get(symbol.upper(), "")
    return SECTOR_TO_ETF.get(sector, "SPY")


def _load_feature_rows(
    conn: psycopg.Connection,
    *,
    symbols: list[str],
    start_ts: datetime,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, timeframe, bar_start_ts
            FROM equities_feature_events
            WHERE symbol = ANY(%s::text[])
              AND bar_start_ts >= %s
            ORDER BY symbol, timeframe, bar_start_ts, id;
            """,
            (symbols, start_ts),
        )
        return list(cur.fetchall())


def _load_ohlcv_series(
    conn: psycopg.Connection,
    *,
    symbols: list[str],
    timeframes: list[str],
    start_ts: datetime,
    end_ts: datetime,
) -> dict[tuple[str, str], BarSeries]:
    if not symbols or not timeframes:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, timeframe, bar_start_ts, close
            FROM (
                SELECT
                    symbol,
                    timeframe,
                    bar_start_ts,
                    close,
                    source,
                    as_of_ts,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol, timeframe, bar_start_ts
                        ORDER BY
                            as_of_ts DESC,
                            CASE
                                WHEN source = %s THEN 0
                                WHEN source = %s THEN 1
                                ELSE 2
                            END ASC
                    ) AS rn
                FROM ohlcv
                WHERE symbol = ANY(%s::text[])
                  AND timeframe = ANY(%s::text[])
                  AND bar_start_ts >= %s
                  AND bar_start_ts <= %s
                  AND is_final = TRUE
            ) q
            WHERE rn = 1
            ORDER BY symbol, timeframe, bar_start_ts;
            """,
            (
                SOURCE_PRIORITY[0],
                SOURCE_PRIORITY[1],
                symbols,
                timeframes,
                start_ts,
                end_ts,
            ),
        )
        rows = list(cur.fetchall())

    by_key: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    for row in rows:
        close = row.get("close")
        if close is None:
            continue
        ts = row["bar_start_ts"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        key = (str(row["symbol"]).upper(), str(row["timeframe"]))
        by_key.setdefault(key, []).append((ts, float(close)))

    out: dict[tuple[str, str], BarSeries] = {}
    for key, points in by_key.items():
        out[key] = BarSeries(
            timestamps=[p[0] for p in points],
            closes=[p[1] for p in points],
        )
    return out


def _is_append_only_block(exc: Exception) -> bool:
    sqlstate = getattr(exc, "sqlstate", None)
    text = str(exc).lower()
    return bool(
        ("append-only table" in text and "equities_feature_events" in text)
        or (sqlstate == "P0001" and "equities_feature_events" in text)
    )


def _safe_pipeline_event(
    conn: psycopg.Connection,
    *,
    actor: str,
    event_type: str,
    message: str,
    data: dict[str, Any],
) -> None:
    try:
        log_pipeline_event(
            conn,
            actor=actor,
            event_type=event_type,
            message=message,
            data=data,
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover - best effort only
        conn.rollback()
        logger.error("Failed to log pipeline event %s: %s", event_type, exc)


def run(
    *,
    database_url: str | None,
    actor: str,
    symbols: list[str],
    lookback_days: int,
    batch_size: int = 1000,
) -> tuple[dict[str, Any], int]:
    now_utc = datetime.now(timezone.utc)
    lookback_days = max(1, int(lookback_days))
    start_ts = now_utc - timedelta(days=lookback_days)

    sector_map = _load_sector_map()
    known_universe = set(get_default_symbols())
    selected_symbols = [s for s in symbols if s in known_universe]
    unknown_symbols = [s for s in symbols if s not in known_universe]
    if unknown_symbols:
        logger.warning(
            "Ignoring %d symbols not present in equities universe definitions: %s",
            len(unknown_symbols),
            ",".join(sorted(unknown_symbols)),
        )
    if not selected_symbols:
        summary = {
            "status": "no_symbols",
            "lookback_days": lookback_days,
            "symbols_requested": len(symbols),
            "symbols_selected": 0,
            "ignored_unknown_symbols": unknown_symbols,
            "feature_rows_scanned": 0,
            "rows_updated": 0,
        }
        conn = connect(database_url)
        try:
            _safe_pipeline_event(
                conn,
                actor=actor,
                event_type="SECTOR_RELATIVE_COMPLETE",
                message="No valid symbols selected from equities universe definitions",
                data=summary,
            )
        finally:
            conn.close()
        return summary, 0

    conn = connect(database_url)
    try:
        feature_rows = _load_feature_rows(
            conn,
            symbols=selected_symbols,
            start_ts=start_ts,
        )
        if not feature_rows:
            summary = {
                "status": "ok",
                "lookback_days": lookback_days,
                "symbols_selected": len(selected_symbols),
                "feature_rows_scanned": 0,
                "rows_updated": 0,
            }
            _safe_pipeline_event(
                conn,
                actor=actor,
                event_type="SECTOR_RELATIVE_COMPLETE",
                message="No equities_feature_events rows matched sector-relative update filters",
                data=summary,
            )
            return summary, 0

        timeframes = sorted({str(r["timeframe"]) for r in feature_rows})
        symbol_set = {str(r["symbol"]).upper() for r in feature_rows}
        benchmark_symbols = {_sector_etf_for_symbol(sym, sector_map) for sym in symbol_set}
        benchmark_symbols.add("SPY")
        ohlcv_symbols = sorted(symbol_set | benchmark_symbols)
        ohlcv_start_ts = start_ts - timedelta(days=max(LOOKBACK_DAYS) + 14)

        series = _load_ohlcv_series(
            conn,
            symbols=ohlcv_symbols,
            timeframes=timeframes,
            start_ts=ohlcv_start_ts,
            end_ts=now_utc,
        )

        feature_rows_scanned = 0
        rows_needing_update = 0
        rows_updated = 0
        rows_without_patch = 0
        missing_asset_series = 0
        missing_spy_series = 0
        missing_sector_series = 0
        patch_key_counts: dict[str, int] = {k: 0 for pair in FEATURE_KEY_BY_DAYS.values() for k in pair}

        update_batch: list[dict[str, Any]] = []

        def flush_updates() -> None:
            nonlocal rows_updated, update_batch
            if not update_batch:
                return
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE equities_feature_events
                    SET features = COALESCE(features, '{}'::jsonb) || %(patch)s::jsonb
                    WHERE id = %(id)s;
                    """,
                    update_batch,
                )
            rows_updated += len(update_batch)
            update_batch = []

        try:
            for row in feature_rows:
                feature_rows_scanned += 1
                symbol = str(row["symbol"]).upper()
                timeframe = str(row["timeframe"])
                ts = row["bar_start_ts"]
                if ts.tzinfo is None:
                    bar_ts = ts.replace(tzinfo=timezone.utc)
                else:
                    bar_ts = ts.astimezone(timezone.utc)

                asset_series = series.get((symbol, timeframe))
                if asset_series is None:
                    missing_asset_series += 1
                    rows_without_patch += 1
                    continue

                sector_etf = _sector_etf_for_symbol(symbol, sector_map)
                sector_series = series.get((sector_etf, timeframe))
                spy_series = series.get(("SPY", timeframe))
                if sector_series is None:
                    missing_sector_series += 1
                if spy_series is None:
                    missing_spy_series += 1

                patch: dict[str, float] = {}
                for days in LOOKBACK_DAYS:
                    sector_key, spy_key = FEATURE_KEY_BY_DAYS[days]

                    asset_ret = asset_series.return_over_days(bar_ts, days)
                    if asset_ret is None:
                        continue

                    if sector_series is not None:
                        sector_ret = sector_series.return_over_days(bar_ts, days)
                        if sector_ret is not None:
                            patch[sector_key] = float(asset_ret - sector_ret)
                            patch_key_counts[sector_key] += 1

                    if spy_series is not None:
                        spy_ret = spy_series.return_over_days(bar_ts, days)
                        if spy_ret is not None:
                            patch[spy_key] = float(asset_ret - spy_ret)
                            patch_key_counts[spy_key] += 1

                if not patch:
                    rows_without_patch += 1
                    continue

                rows_needing_update += 1
                update_batch.append(
                    {
                        "id": row["id"],
                        "patch": json.dumps(patch),
                    }
                )
                if len(update_batch) >= batch_size:
                    flush_updates()

            flush_updates()
        except Exception as exc:
            conn.rollback()
            blocked = _is_append_only_block(exc)
            summary = {
                "status": "blocked_append_only" if blocked else "error",
                "lookback_days": lookback_days,
                "symbols_selected": len(selected_symbols),
                "feature_rows_scanned": feature_rows_scanned,
                "rows_needing_update": rows_needing_update,
                "rows_updated": rows_updated,
                "missing_asset_series_rows": missing_asset_series,
                "missing_sector_series_rows": missing_sector_series,
                "missing_spy_series_rows": missing_spy_series,
                "rows_without_patch": rows_without_patch,
                "error": str(exc),
            }
            event_type = "SECTOR_RELATIVE_BLOCKED" if blocked else "SECTOR_RELATIVE_ERROR"
            _safe_pipeline_event(
                conn,
                actor=actor,
                event_type=event_type,
                message="Sector-relative feature update failed",
                data=summary,
            )

            if blocked:
                print(
                    "Append-only guard prevented updates to equities_feature_events. "
                    "Sector-relative fields were computed but not written. "
                    "Create new feature rows in an append-only flow or relax the guard for this operation.",
                    file=sys.stderr,
                )
                return summary, 2
            raise

        conn.commit()
        summary = {
            "status": "ok",
            "lookback_days": lookback_days,
            "symbols_selected": len(selected_symbols),
            "symbols_with_feature_rows": len(symbol_set),
            "feature_rows_scanned": feature_rows_scanned,
            "rows_needing_update": rows_needing_update,
            "rows_updated": rows_updated,
            "rows_without_patch": rows_without_patch,
            "missing_asset_series_rows": missing_asset_series,
            "missing_sector_series_rows": missing_sector_series,
            "missing_spy_series_rows": missing_spy_series,
            "fields_written": patch_key_counts,
            "ignored_unknown_symbols": unknown_symbols,
            "timeframes": timeframes,
        }
        _safe_pipeline_event(
            conn,
            actor=actor,
            event_type="SECTOR_RELATIVE_COMPLETE",
            message="Sector-relative features computed and merged into equities_feature_events",
            data=summary,
        )
        return summary, 0
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute sector-relative and SPY-relative equities features")
    parser.add_argument("--database-url", default=None, help="Database URL override (default: DATABASE_URL)")
    parser.add_argument("--actor", default="S1_MAIN", help="Actor name for equities_pipeline_events")
    parser.add_argument(
        "--symbols",
        default=None,
        help="Optional comma-separated symbol filter (default: full equities universe)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=120,
        help="Process feature rows with bar_start_ts within the last N days (default: 120)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of row updates per batch (default: 1000)",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    symbols = _parse_symbols_arg(args.symbols)

    summary, exit_code = run(
        database_url=args.database_url,
        actor=args.actor,
        symbols=symbols,
        lookback_days=args.lookback_days,
        batch_size=max(1, int(args.batch_size)),
    )
    print(json.dumps(summary, default=str, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
