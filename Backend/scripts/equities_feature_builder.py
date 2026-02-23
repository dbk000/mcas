#!/usr/bin/env python3
"""Build session-aware equities feature events with strict available_at_ts discipline."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

from equities_predictor.calendar import session_state
from equities_predictor.db import (
    connect,
    insert_feature_events,
    list_symbols,
    load_events,
    load_ohlcv,
    log_pipeline_event,
)
from equities_predictor.ids import deterministic_uuid
from equities_predictor.universe import get_default_symbols

HORIZONS = ("1d", "3d", "5d", "gap_open")


def _tf_delta(timeframe: str) -> timedelta:
    if timeframe == "1h":
        return timedelta(hours=1)
    if timeframe == "1d":
        return timedelta(days=1)
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _safe_float(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _compute_regime(prices: np.ndarray, returns: np.ndarray, idx: int) -> dict:
    window = returns[max(0, idx - 20):idx]
    vol = float(np.std(window)) if len(window) >= 5 else 0.0
    long_window = returns[max(0, idx - 60):idx]
    vol_ref = float(np.median(np.abs(long_window))) if len(long_window) >= 10 else 0.0

    ma_fast = float(np.mean(prices[max(0, idx - 5):idx])) if idx >= 5 else float(prices[idx])
    ma_slow = float(np.mean(prices[max(0, idx - 20):idx])) if idx >= 20 else float(prices[idx])
    slope = ma_fast - ma_slow

    trend_regime = "bull" if slope > 0 else "bear" if slope < 0 else "flat"
    vol_regime = "high" if vol_ref > 0 and vol > vol_ref else "low"
    return {
        "trend_regime": trend_regime,
        "vol_regime": vol_regime,
        "rolling_vol": vol,
        "vol_ref": vol_ref,
    }


def _event_window_stats(events: list[dict], as_of_ts: datetime) -> tuple[dict, dict]:
    windows = {
        "6h": as_of_ts - timedelta(hours=6),
        "24h": as_of_ts - timedelta(hours=24),
        "3d": as_of_ts - timedelta(days=3),
    }
    counts = {"6h": 0, "24h": 0, "3d": 0}
    sentiment_sums = {"6h": 0.0, "24h": 0.0, "3d": 0.0}
    anti_bot_sums = {"6h": 0.0, "24h": 0.0, "3d": 0.0}
    sources = {"6h": set(), "24h": set(), "3d": set()}

    for ev in events:
        ev_ts = ev["available_at_ts"]
        if ev_ts > as_of_ts:
            continue
        for label, start_ts in windows.items():
            if ev_ts >= start_ts:
                counts[label] += 1
                sentiment_sums[label] += _safe_float(ev.get("sentiment_score")) or 0.0
                anti_bot_sums[label] += _safe_float(ev.get("anti_bot_score")) or 0.0
                sources[label].add(ev.get("source") or "unknown")

    features = {
        "event_count_6h": counts["6h"],
        "event_count_24h": counts["24h"],
        "event_count_3d": counts["3d"],
        "event_sentiment_mean_6h": sentiment_sums["6h"] / max(1, counts["6h"]),
        "event_sentiment_mean_24h": sentiment_sums["24h"] / max(1, counts["24h"]),
        "event_sentiment_mean_3d": sentiment_sums["3d"] / max(1, counts["3d"]),
        "anti_bot_mean_24h": anti_bot_sums["24h"] / max(1, counts["24h"]),
    }

    coverage = {
        "source_count_6h": len(sources["6h"]),
        "source_count_24h": len(sources["24h"]),
        "source_count_3d": len(sources["3d"]),
    }

    return features, coverage


def build_features(
    *,
    database_url: str | None,
    actor: str,
    symbols: list[str],
    timeframe: str,
    start_ts: datetime,
    end_ts: datetime,
) -> dict:
    conn = connect(database_url)
    tf_delta = _tf_delta(timeframe)

    try:
        bars = load_ohlcv(
            conn,
            symbols=symbols,
            timeframe=timeframe,
            start_ts=start_ts,
            end_ts=end_ts,
            sources=["equities_yahoo", "equities_stooq"],
        )
        events = load_events(
            conn,
            symbols=symbols,
            start_ts=start_ts - timedelta(days=3),
            end_ts=end_ts + timedelta(days=1),
        )

        bars_by_symbol: dict[str, list[dict]] = defaultdict(list)
        events_by_symbol: dict[str, list[dict]] = defaultdict(list)
        for row in bars:
            bars_by_symbol[row["symbol"]].append(row)
        for ev in events:
            events_by_symbol[ev["symbol"]].append(ev)

        out_rows: list[dict] = []

        for symbol, rows in bars_by_symbol.items():
            rows.sort(key=lambda x: x["bar_start_ts"])
            closes = np.array([float(r["close"]) for r in rows], dtype=float)
            returns = np.zeros(len(rows), dtype=float)
            if len(rows) > 1:
                returns[1:] = (closes[1:] / closes[:-1]) - 1.0

            symbol_events = sorted(events_by_symbol.get(symbol, []), key=lambda x: x["available_at_ts"])

            for i, bar in enumerate(rows):
                bar_start = bar["bar_start_ts"].astimezone(timezone.utc)
                available_at = bar_start + tf_delta

                p_ret_1 = float(returns[i])
                p_ret_5 = float((closes[i] / closes[max(0, i - 5)] - 1.0) if i >= 5 else 0.0)
                p_ret_20 = float((closes[i] / closes[max(0, i - 20)] - 1.0) if i >= 20 else 0.0)

                regime = _compute_regime(closes, returns, i)
                event_feats, coverage = _event_window_stats(symbol_events, as_of_ts=available_at)

                # Earnings-window proxy from recent SEC forms.
                earnings_window = 0
                sec_count_3d = 0
                for ev in symbol_events:
                    ev_ts = ev["available_at_ts"]
                    if ev_ts > available_at:
                        break
                    if ev_ts < available_at - timedelta(days=3):
                        continue
                    et = (ev.get("event_type") or "").upper()
                    if et.startswith("SEC_"):
                        sec_count_3d += 1
                    if "EARNINGS" in et or et in {"SEC_10-Q", "SEC_10-K"}:
                        earnings_window = 1

                features = {
                    "price_return_1": p_ret_1,
                    "price_return_5": p_ret_5,
                    "price_return_20": p_ret_20,
                    "rolling_vol_20": regime["rolling_vol"],
                    "sec_event_count_3d": sec_count_3d,
                    "earnings_window_flag": earnings_window,
                    **event_feats,
                }

                regime_labels = {
                    "trend_regime": regime["trend_regime"],
                    "vol_regime": regime["vol_regime"],
                }

                sess = session_state(bar_start)
                for horizon in HORIZONS:
                    row_id = deterministic_uuid(
                        "equities_feature_event",
                        symbol,
                        timeframe,
                        bar_start.isoformat(),
                        horizon,
                        available_at.isoformat(),
                    )
                    out_rows.append(
                        {
                            "id": row_id,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "bar_start_ts": bar_start,
                            "available_at_ts": available_at,
                            "horizon": horizon,
                            "session_state": sess,
                            "features": features,
                            "regime_labels": regime_labels,
                            "source_coverage": coverage,
                        }
                    )

        inserted = insert_feature_events(conn, out_rows)
        log_pipeline_event(
            conn,
            actor=actor,
            event_type="FEATURE_BUILD_COMPLETE",
            message="Equities feature event build completed",
            data={
                "symbols": len(bars_by_symbol),
                "timeframe": timeframe,
                "input_bars": len(bars),
                "input_events": len(events),
                "rows_written": inserted,
            },
        )
        conn.commit()
        return {
            "symbols": len(bars_by_symbol),
            "rows_written": inserted,
            "timeframe": timeframe,
        }
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build equities feature events")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--actor", default="S1_MAIN")
    parser.add_argument("--symbols", default=",".join(get_default_symbols()))
    parser.add_argument("--timeframe", default="1h", choices=["1h", "1d"])
    parser.add_argument("--lookback-days", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=max(1, int(args.lookback_days)))
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    summary = build_features(
        database_url=args.database_url,
        actor=args.actor,
        symbols=symbols,
        timeframe=args.timeframe,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
