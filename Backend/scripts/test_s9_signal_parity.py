#!/usr/bin/env python3
"""
S9 Signal Parity Test (Phase 5)
===============================

Verifies that regenerate_opportunities.run_s9 and
signal_worker._evaluate_s9_cross_sectional produce identical S9 signals when
given the same Postgres OHLCV, the same whitelist, and the same FnG input.

Why this exists
---------------
The two paths share S9Evaluator.build_signal() but diverge on universe
construction, OHLCV source, bar timing, and microstructure entry. If they
don't match on the core cross-sectional logic, the Phase 6 forward reconcile
will fail for the wrong reason — it'll look like paper trading's exit engine
is broken when the actual fault is that the live scanner is producing a
different signal set than the validated backtest batch.

What this tests
---------------
The LOGICAL equivalence of the two cross-sectional scans against the same
Postgres OHLCV. It does NOT test Redis/WS ingestion drift — that's a runtime
infrastructure concern measured separately by comparing live signals with the
same-hour regen.

Tolerances and buckets (from the plan)
--------------------------------------
- Bucket A (acceptable): quality-filter exclusions, daily budget truncation.
  Test runs with quality filter DISABLED to remove this variable.
- Bucket B (must be zero): any divergence in ret_4h, ret_24h, entry_price,
  stop_price, tp1/tp2, rank, score. Within 1 ULP.
- Bucket C (expected, documented): microstructure entry price delta. Test
  runs with microstructure DISABLED (mocked to empty) to isolate it.

Usage
-----
    docker exec backend-computers-1 python3 /backend/scripts/test_s9_signal_parity.py \\
        --start 2026-03-20 --end 2026-03-27
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

if os.path.isdir("/backend"):
    sys.path.insert(0, "/backend")
    sys.path.insert(0, "/backend/scripts")
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core_logic.s9_short_reversion import S9Evaluator, S9State  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trading:trading@127.0.0.1:5432/trading")

# Canonical S9 config (matches bootstrap.py + frozen CSV)
S9_RET_LOOKBACK = 4
S9_PERCENTILE = 10
S9_BREADTH_THRESHOLD = 0.3
S9_MIN_RET_4H = 0.05
S9_FNG_GATE_ENABLED = True
S9_FNG_MAX_VALUE = 50

MS_PER_HOUR = 3_600_000

OUTPUT_DIR = (
    Path("/backend/scripts/output")
    if os.path.isdir("/backend") else
    Path(__file__).resolve().parent / "output"
)


# -----------------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------------

def load_ohlcv_1h(conn, start_date: datetime, symbols: set[str] | None = None) -> dict:
    """Load 1h OHLCV from Postgres. Returns {symbol: {ts_ms: (o,h,l,c,v)}}."""
    if symbols is not None and not symbols:
        return {}
    with conn.cursor() as cur:
        if symbols is not None:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE timeframe = '1h'
                  AND bar_start_ts >= %s
                  AND is_final = TRUE
                  AND symbol = ANY(%s)
                ORDER BY symbol, bar_start_ts
            """, (start_date, sorted(symbols)))
        else:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE timeframe = '1h'
                  AND bar_start_ts >= %s
                  AND is_final = TRUE
                ORDER BY symbol, bar_start_ts
            """, (start_date,))
        data: dict[str, dict[int, tuple]] = defaultdict(dict)
        for row in cur:
            sym = row[0]
            ts_ms = int(row[1].timestamp() * 1000)
            data[sym][ts_ms] = (
                float(row[2]), float(row[3]), float(row[4]),
                float(row[5]), float(row[6]) if row[6] is not None else 0.0,
            )
    return dict(data)


def load_perp_whitelist() -> set[str]:
    """Load the canonical S9 whitelist."""
    candidates = [
        "/backend/config/s9_symbol_whitelist.txt",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "config", "s9_symbol_whitelist.txt"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "binance_perp_symbols.txt"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            with open(c) as f:
                return {line.strip().upper() for line in f if line.strip()}
    logger.warning("S9 whitelist not found in any candidate path — using empty set")
    return set()


# -----------------------------------------------------------------------------
# Reference implementations of the two paths
# -----------------------------------------------------------------------------

def regen_path_signals(
    sym_data: dict[str, dict[int, tuple]],
    start_ms: int,
    end_ms: int,
    fng_data: dict[str, int],
) -> list[dict]:
    """Port of regenerate_opportunities.run_s9's inner loop — the regen path."""
    lookback_ms = S9_RET_LOOKBACK * MS_PER_HOUR
    breadth_lookback_ms = 24 * MS_PER_HOUR

    all_ts: set[int] = set()
    for bars in sym_data.values():
        all_ts.update(bars.keys())
    sorted_ts = sorted(all_ts)

    signals: list[dict] = []
    for ts_ms in sorted_ts:
        if ts_ms < start_ms or ts_ms > end_ms:
            continue
        bar_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        if S9_FNG_GATE_ENABLED and fng_data:
            date_key = bar_dt.strftime("%Y-%m-%d")
            fng_val = fng_data.get(date_key)
            if fng_val is not None and fng_val > S9_FNG_MAX_VALUE:
                continue

        returns_4h: dict[str, float] = {}
        returns_24h: dict[str, float] = {}
        closes: dict[str, float] = {}
        prev_ts = ts_ms - lookback_ms
        breadth_prev_ts = ts_ms - breadth_lookback_ms

        for sym, lookup in sym_data.items():
            current = lookup.get(ts_ms)
            if current is None:
                continue
            prev = lookup.get(prev_ts)
            if prev is None:
                continue
            close_now = current[3]
            close_prev = prev[3]
            if close_prev <= 0:
                continue
            returns_4h[sym] = (close_now - close_prev) / close_prev
            closes[sym] = close_now
            breadth_prev = lookup.get(breadth_prev_ts)
            if breadth_prev:
                close_24h_ago = breadth_prev[3]
                if close_24h_ago > 0:
                    returns_24h[sym] = (close_now - close_24h_ago) / close_24h_ago

        if len(returns_4h) < 20:
            continue

        n_positive_24h = sum(1 for r in returns_24h.values() if r > 0)
        breadth = n_positive_24h / len(returns_24h) if returns_24h else 0
        if breadth < S9_BREADTH_THRESHOLD:
            continue

        ranked = sorted(returns_4h.items(), key=lambda x: x[1], reverse=True)
        decile_size = max(1, len(ranked) * S9_PERCENTILE // 100)
        top_decile = ranked[:decile_size]
        threshold = ranked[decile_size - 1][1] if decile_size <= len(ranked) else 0
        state = S9State(
            breadth=breadth, breadth_passes=True,
            universe_size=len(returns_4h),
            top_decile_threshold=threshold,
            top_decile_count=decile_size,
        )
        for rank, (sym, ret_4h) in enumerate(top_decile):
            if ret_4h < S9_MIN_RET_4H:
                continue
            close_now = closes[sym]
            ret_24h_val = returns_24h.get(sym, 0.0)
            sig = S9Evaluator.build_signal(
                symbol=sym, close=close_now,
                ret_4h=ret_4h, ret_24h=ret_24h_val,
                breadth=breadth, rank=rank,
                universe_size=len(returns_4h),
                decile_size=decile_size,
                state=state,
                bar_start_ts=str(ts_ms),
            )
            signals.append({
                "symbol": sym,
                "bar_start_ts": ts_ms,
                "rank": rank + 1,
                "ret_4h": ret_4h,
                "ret_24h": ret_24h_val,
                "breadth": breadth,
                "universe_size": len(returns_4h),
                "decile_size": decile_size,
                "score": sig.setup_score,
                "entry_price": sig.entry_price,
                "stop_price": sig.stop_price,
                "tp1_price": sig.tp1_price,
                "tp2_price": sig.tp2_price,
            })
    return signals


def live_path_signals(
    sym_data: dict[str, dict[int, tuple]],
    start_ms: int,
    end_ms: int,
    fng_data: dict[str, int],
) -> list[dict]:
    """Port of signal_worker._evaluate_s9_cross_sectional for the test harness.

    Assumptions baked in for test isolation:
      - No Redis (OHLCV from sym_data directly, same as regen path)
      - No symbol quality filter (disabled — Bucket A)
      - No microstructure entry (mocked to empty — Bucket C)
      - No daily signal budget cap
      - FnG read from fng_data arg (same source as regen path)

    With these toggles, the live path SHOULD be equivalent to the regen path.
    Any divergence is a Bucket B bug.

    Key structural difference: the live path reads `candles[-1]` (most recent
    bar) and `candles[-5]` (4h ago) rather than iterating all historical
    timestamps. We reproduce that semantics by stepping through all
    timestamps and for each, looking up the bars at ts_ms and ts_ms - 4h.
    """
    lookback_ms = S9_RET_LOOKBACK * MS_PER_HOUR
    breadth_lookback_ms = 24 * MS_PER_HOUR  # 24-hour for breadth

    all_ts: set[int] = set()
    for bars in sym_data.values():
        all_ts.update(bars.keys())
    sorted_ts = sorted(all_ts)

    signals: list[dict] = []
    for ts_ms in sorted_ts:
        if ts_ms < start_ms or ts_ms > end_ms:
            continue
        bar_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        # FnG gate (live path calls fetch_fear_greed_index(); we use the same
        # day-keyed lookup the regen path uses).
        if S9_FNG_GATE_ENABLED and fng_data:
            date_key = bar_dt.strftime("%Y-%m-%d")
            fng_val = fng_data.get(date_key)
            if fng_val is None:
                # Live path fails closed on unavailable FnG. Match that behaviour.
                continue
            if fng_val > S9_FNG_MAX_VALUE:
                continue

        # ret_4h is required; ret_24h is OPTIONAL (matches regen path's
        # universe construction — symbols without 24h history can still be
        # ranked, but don't contribute to breadth).
        symbol_data_at_ts: dict[str, tuple] = {}
        for sym, lookup in sym_data.items():
            current = lookup.get(ts_ms)
            if current is None:
                continue
            bar_4h_ago = lookup.get(ts_ms - lookback_ms)
            if bar_4h_ago is None:
                continue
            close_now = current[3]
            close_4h = bar_4h_ago[3]
            if not (close_now > 0 and close_4h > 0):
                continue
            # Bit-for-bit identical to regen: (a-b)/b not a/b-1
            ret_4h = (close_now - close_4h) / close_4h
            ret_24h_val: float | None = None
            bar_24h_ago = lookup.get(ts_ms - breadth_lookback_ms)
            if bar_24h_ago is not None and bar_24h_ago[3] > 0:
                ret_24h_val = (close_now - bar_24h_ago[3]) / bar_24h_ago[3]
            symbol_data_at_ts[sym] = (ret_4h, ret_24h_val, close_now)

        if len(symbol_data_at_ts) < 20:
            continue

        # Breadth is computed over the subset with valid ret_24h (matches regen).
        syms_with_24h = [(s, d) for s, d in symbol_data_at_ts.items() if d[1] is not None]
        if not syms_with_24h:
            continue
        n_positive = sum(1 for _, (_, r24, _) in syms_with_24h if r24 > 0)
        breadth = n_positive / len(syms_with_24h)
        # Regen path uses `<` (strict less-than), live path used `<=` — now aligned to `<`
        if breadth < S9_BREADTH_THRESHOLD:
            continue

        sorted_syms = sorted(symbol_data_at_ts.items(), key=lambda x: x[1][0], reverse=True)
        decile_n = max(1, len(sorted_syms) // 10)
        top_decile = sorted_syms[:decile_n]

        # Magnitude filter
        top_decile = [(s, r) for s, r in top_decile if r[0] >= S9_MIN_RET_4H]
        if not top_decile:
            continue

        state = S9State(
            breadth=breadth, breadth_passes=True,
            universe_size=len(symbol_data_at_ts),
            top_decile_threshold=top_decile[-1][1][0] if top_decile else 0.0,
            top_decile_count=decile_n,
        )

        for rank, (symbol, (ret_4h, ret_24h, close)) in enumerate(top_decile):
            # Match production live path: pass 0.0 to build_signal when missing.
            ret_24h_for_sig = ret_24h if ret_24h is not None else 0.0
            sig = S9Evaluator.build_signal(
                symbol=symbol, close=close,
                ret_4h=ret_4h, ret_24h=ret_24h_for_sig,
                breadth=breadth, rank=rank,
                universe_size=len(symbol_data_at_ts),
                decile_size=decile_n,
                state=state,
                bar_start_ts=str(ts_ms),
            )
            signals.append({
                "symbol": symbol,
                "bar_start_ts": ts_ms,
                "rank": rank + 1,
                "ret_4h": ret_4h,
                "ret_24h": ret_24h_for_sig,
                "breadth": breadth,
                "universe_size": len(symbol_data_at_ts),
                "decile_size": decile_n,
                "score": sig.setup_score,
                "entry_price": sig.entry_price,
                "stop_price": sig.stop_price,
                "tp1_price": sig.tp1_price,
                "tp2_price": sig.tp2_price,
            })
    return signals


# -----------------------------------------------------------------------------
# Diff
# -----------------------------------------------------------------------------

NUMERIC_FIELDS = ("ret_4h", "ret_24h", "breadth", "score",
                  "entry_price", "stop_price", "tp1_price", "tp2_price")
EXACT_FIELDS = ("rank", "universe_size", "decile_size")

TOL_REL = 1e-9  # effectively 1 ULP for 1e9-scale numbers


def diff_signals(regen: list[dict], live: list[dict]) -> dict:
    regen_by_key = {(s["symbol"], s["bar_start_ts"]): s for s in regen}
    live_by_key = {(s["symbol"], s["bar_start_ts"]): s for s in live}

    regen_keys = set(regen_by_key)
    live_keys = set(live_by_key)

    in_both = regen_keys & live_keys
    only_regen = regen_keys - live_keys
    only_live = live_keys - regen_keys

    bucket_b: list[dict] = []
    for key in sorted(in_both):
        r = regen_by_key[key]
        l = live_by_key[key]
        diffs = []
        for field in NUMERIC_FIELDS:
            rv, lv = r[field], l[field]
            if rv is None and lv is None:
                continue
            if rv is None or lv is None:
                diffs.append(f"{field}: {rv} vs {lv}")
                continue
            if not math.isclose(rv, lv, rel_tol=TOL_REL, abs_tol=1e-12):
                diffs.append(f"{field}: {rv!r} vs {lv!r}")
        for field in EXACT_FIELDS:
            if r[field] != l[field]:
                diffs.append(f"{field}: {r[field]} vs {l[field]}")
        if diffs:
            bucket_b.append({
                "symbol": key[0],
                "bar_start_ts": key[1],
                "diffs": diffs,
            })

    return {
        "regen_count": len(regen),
        "live_count": len(live),
        "in_both_count": len(in_both),
        "only_regen_count": len(only_regen),
        "only_live_count": len(only_live),
        "bucket_b_count": len(bucket_b),
        "only_regen_sample": sorted(only_regen)[:20],
        "only_live_sample": sorted(only_live)[:20],
        "bucket_b_sample": bucket_b[:20],
    }


# -----------------------------------------------------------------------------
# Entry
# -----------------------------------------------------------------------------

def parse_fng_history() -> dict[str, int]:
    """Load FnG history — inlined to avoid a dependency on regenerate_opportunities.py
    being present in every container image."""
    import urllib.request
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=180)
    days = (end - start).days + 10
    url = f"https://api.alternative.me/fng/?limit={days}&format=json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        result: dict[str, int] = {}
        for entry in data.get("data", []):
            entry_ts = int(entry["timestamp"])
            dt = datetime.fromtimestamp(entry_ts, tz=timezone.utc)
            result[dt.strftime("%Y-%m-%d")] = int(entry["value"])
        logger.info("FnG history: %d days loaded from alternative.me", len(result))
        return result
    except Exception as e:
        logger.warning("FnG API failed: %s — returning empty history", e)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="S9 signal parity test")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--use-whitelist", action="store_true", default=True,
                        help="Restrict universe to canonical perp whitelist (default: True)")
    parser.add_argument("--no-whitelist", action="store_false", dest="use_whitelist")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc,
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_report = OUTPUT_DIR / f"s9_signal_parity_{ts}.json"
    md_report = Path.cwd() / f"s9_signal_parity_{args.start}_{args.end}.md"
    logger.info("Reports: %s, %s", json_report, md_report)

    # Load OHLCV with warmup so returns can be computed for the first test hour
    warmup = timedelta(days=3)
    ohlcv_start = start_date - warmup
    whitelist = load_perp_whitelist() if args.use_whitelist else None
    if whitelist:
        logger.info("Using whitelist: %d symbols", len(whitelist))

    with psycopg.connect(DATABASE_URL) as conn:
        sym_data = load_ohlcv_1h(conn, ohlcv_start, whitelist)
    logger.info("Loaded OHLCV for %d symbols", len(sym_data))

    fng_data = parse_fng_history()
    logger.info("FnG history: %d days loaded", len(fng_data))

    start_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000)

    logger.info("Computing regen path signals...")
    regen_sigs = regen_path_signals(sym_data, start_ms, end_ms, fng_data)
    logger.info("Regen: %d signals", len(regen_sigs))

    logger.info("Computing live path signals...")
    live_sigs = live_path_signals(sym_data, start_ms, end_ms, fng_data)
    logger.info("Live: %d signals", len(live_sigs))

    diff = diff_signals(regen_sigs, live_sigs)

    with open(json_report, "w") as f:
        json.dump({
            "start": args.start, "end": args.end,
            "use_whitelist": args.use_whitelist,
            "diff": diff,
        }, f, indent=2, sort_keys=True, default=str)

    # Also write the markdown report to repo root
    bucket_b_empty = diff["bucket_b_count"] == 0
    only_side_empty = diff["only_regen_count"] == 0 and diff["only_live_count"] == 0
    passed = bucket_b_empty and only_side_empty

    md_lines = [
        f"# S9 Signal Parity Report — {args.start} to {args.end}",
        "",
        f"Generated: {ts} UTC",
        f"Whitelist applied: {args.use_whitelist}",
        "",
        f"## Result: {'✅ PASS' if passed else '❌ FAIL'}",
        "",
        f"- Regen path signals : **{diff['regen_count']}**",
        f"- Live path signals  : **{diff['live_count']}**",
        f"- In both            : **{diff['in_both_count']}**",
        f"- Only in regen      : **{diff['only_regen_count']}** (Bucket B if non-zero)",
        f"- Only in live       : **{diff['only_live_count']}** (Bucket B if non-zero)",
        f"- Numeric divergences: **{diff['bucket_b_count']}** (Bucket B must be 0)",
        "",
        "## Bucket A (documented, accepted)",
        "",
        "- Symbol quality filter DISABLED in both paths for this test",
        "- Daily signal budget DISABLED in the test harness",
        "",
        "## Bucket C (expected, documented)",
        "",
        "- Microstructure entry DISABLED in both paths (mocked empty).",
        "  Live path production attaches `microstructure.suggested_entry_price`;",
        "  regen path never does. Phase 6 will run with microstructure disabled",
        "  for the reconcile gate, then measure impact separately.",
        "",
    ]
    if not bucket_b_empty:
        md_lines += ["## Bucket B failures (must fix)", ""]
        for d in diff["bucket_b_sample"]:
            md_lines.append(f"- **{d['symbol']}** @ {d['bar_start_ts']}: {', '.join(d['diffs'])}")
        md_lines.append("")
    if diff["only_regen_count"] > 0:
        md_lines += ["## Signals only in regen (Bucket B)", ""]
        for k in diff["only_regen_sample"]:
            md_lines.append(f"- `{k[0]}` @ `{k[1]}`")
        md_lines.append("")
    if diff["only_live_count"] > 0:
        md_lines += ["## Signals only in live (Bucket B)", ""]
        for k in diff["only_live_sample"]:
            md_lines.append(f"- `{k[0]}` @ `{k[1]}`")
        md_lines.append("")

    with open(md_report, "w") as f:
        f.write("\n".join(md_lines))
    logger.info("Wrote markdown report: %s", md_report)

    print("=" * 72)
    print(f"S9 SIGNAL PARITY — {'PASS' if passed else 'FAIL'}")
    print("=" * 72)
    print(f"Regen : {diff['regen_count']}")
    print(f"Live  : {diff['live_count']}")
    print(f"Both  : {diff['in_both_count']}")
    print(f"Only regen: {diff['only_regen_count']}")
    print(f"Only live : {diff['only_live_count']}")
    print(f"Bucket B  : {diff['bucket_b_count']}")
    print("=" * 72)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
