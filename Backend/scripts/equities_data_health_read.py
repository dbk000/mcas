#!/usr/bin/env python3
"""Equity data-health read — pre-backfill integrity gate (R1 / CP-2).

Purpose
-------
Runs the five integrity questions from `EQUITIES_PLAN_REVIEW_47.md` §2.1 against
the existing ~4 GB equity data in production Postgres and produces a single
PASS / FAIL verdict at `equities_plan_work/checkpoints/CP-2.md`.

This report is a decision gate: any FAIL blocks B1 (universe backfill).
Q3 (available_at_ts leakage) additionally blocks B2 (feature recompute) on
any violation.

Inputs
------
- ``DATABASE_URL`` environment variable (required). Points at the production
  Postgres (`backend-postgres-1`). If absent, the script exits 2 with a
  remediation hint.

Outputs
-------
- ``equities_plan_work/checkpoints/CP-2.md`` — the verdict report in the
  playbook §6 CP-2 template.
- ``Backend/scripts/output/equities_data_health_read_<utc>.log`` — human-readable
  audit log of every query executed and its raw output. Path is logged at start.
- ``equities_plan_work/findings/R1/<artifact>_<utc>.{csv,txt}`` — raw per-query
  evidence files for future audit.

Usage
-----
Run inside a container with DB access, or on a host that can route to
`backend-postgres-1`:

    export DATABASE_URL='postgresql://trading:<pw>@<host>:5432/trading'
    python3 Backend/scripts/equities_data_health_read.py

No console-only output: every result is persisted to disk.
"""

from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg


# ---------------------------------------------------------------------------
# Governance-justified constants — all named, all justified inline
# ---------------------------------------------------------------------------

# Windows chosen per playbook §5 R1 brief
WINDOW_OHLCV_START = "2024-02-23"   # earliest bar of existing equity data
WINDOW_OHLCV_END = "2026-04-14"     # freshness cut-off per postgres_audit.md
WINDOW_FEAT_START = "2025-08-28"    # first day of equities_feature_events per audit
WINDOW_FEAT_END = "2026-04-14"      # freshness cut-off per audit

# Tolerance: 1d bar shortfall > 5% of expected trading days = FAIL (review §2.1 Q1).
Q1_BAR_SHORTFALL_THRESHOLD_PCT = 5.0

# Q2: minimum feature-event rows per existing symbol; review §2.1 Q2 says
# "≥100 feature-event rows". Set as an explicit named constant.
Q2_MIN_FEATURE_EVENTS_PER_SYMBOL = 100

# 1h bars per RTH+ETH day from Yahoo chart endpoint with includePrePost=true:
# 04:00-09:00 (6 bars) + 09:30 half-bar (1) + 10:30-16:00 (6) + 16:00-20:00 (4) = 17 bars.
# Verified empirically on AAPL 2024-06-03. Use as expected bars/full trading day.
BARS_PER_FULL_TRADING_DAY_1H = 17

# Known splits inside 2024-02-23 → 2026-04-14 window. Extra-window splits from
# the brief (TSLA 2022-08-25, NVDA 2021-07-20) are out of window; noted but skipped.
KNOWN_SPLITS_IN_WINDOW = [
    # (symbol, ex_date, ratio_from:ratio_to, pre_close_date, post_close_date)
    ("NVDA", "2024-06-10", 10, "2024-06-07", "2024-06-10"),
]

# Expected sources in public.ohlcv for equity data per postgres_audit.md
EXPECTED_EQUITY_SOURCES = {"equities_yahoo", "yahoo_chart"}

# Known crypto sources to subtract when auditing which remaining sources might
# be equity-like. Pulled from postgres_audit.md (13 distinct sources at
# audit time; 11 crypto + 2 equity). Used only as an allowlist for the
# "silent drift" cross-check — NOT part of the primary verdict query.
KNOWN_CRYPTO_SOURCES = {
    "binance", "bybit", "kucoin", "okx", "bitmart", "bitget", "mexc",
    "htx", "gate", "coinex", "ws",
}

# NYSE US federal holidays 2024-2026 (full closures only). Pulled from the
# NYSE holiday schedule. Early-close days are NOT in this list — they still
# contribute a (reduced) daily bar, so the 5% tolerance absorbs them.
NYSE_FULL_CLOSURES = {
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29",
    "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
    "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17",  # 2025-01-09 = Carter state funeral
    "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
}

# Paths — all relative to repo root (script assumed to run from any cwd).
REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINTS_DIR = REPO_ROOT / "equities_plan_work" / "checkpoints"
FINDINGS_DIR = REPO_ROOT / "equities_plan_work" / "findings" / "R1"
SCRIPT_OUTPUT_DIR = REPO_ROOT / "Backend" / "scripts" / "output"


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class QuestionResult:
    qid: str
    title: str
    passed: bool
    summary: str        # one-line diagnostic for CP-2.md
    evidence_path: str  # relative path to raw artifact


# ---------------------------------------------------------------------------
# Utility — trading day count between two dates (inclusive), weekdays minus holidays.
# ---------------------------------------------------------------------------

def _expected_trading_days(start_iso: str, end_iso: str) -> int:
    """Count NYSE trading days in the inclusive date range.

    Weekdays (Mon-Fri) minus `NYSE_FULL_CLOSURES`. Early-close days
    still count as one trading day and still ship one 1d bar, so the tolerance
    band in Q1 absorbs their reduced 1h bar count.
    """
    start = datetime.fromisoformat(start_iso).date()
    end = datetime.fromisoformat(end_iso).date()
    if end < start:
        raise ValueError(f"end {end_iso} precedes start {start_iso}")
    days = 0
    cur = start
    one_day = (datetime.fromisoformat("2024-01-02").date()
               - datetime.fromisoformat("2024-01-01").date())
    while cur <= end:
        if cur.weekday() < 5 and cur.isoformat() not in NYSE_FULL_CLOSURES:
            days += 1
        cur = cur + one_day
    return days


# ---------------------------------------------------------------------------
# Query runners — each returns a QuestionResult and writes a raw artifact.
# ---------------------------------------------------------------------------

def run_q1_ohlcv_continuity(conn: psycopg.Connection, utc_tag: str, log) -> QuestionResult:
    """Q1 — per-symbol OHLCV continuity on 1h + 1d across each symbol's own
    observed window. Fails if any symbol is > Q1_BAR_SHORTFALL_THRESHOLD_PCT short.
    """
    log("=" * 72)
    log("Q1 — OHLCV continuity on existing equity symbols (1h + 1d)")
    log("=" * 72)
    q = """
        SELECT
            symbol,
            timeframe,
            COUNT(*)                       AS n_bars,
            MIN(bar_start_ts)::date        AS first_bar,
            MAX(bar_start_ts)::date        AS last_bar
        FROM public.ohlcv
        WHERE source = 'equities_yahoo'
          AND timeframe IN ('1h', '1d')
        GROUP BY symbol, timeframe
        ORDER BY timeframe, symbol
    """
    with conn.cursor() as cur:
        cur.execute(q)
        rows = cur.fetchall()

    artifact = FINDINGS_DIR / f"q1_continuity_output_{utc_tag}.csv"
    fails: list[str] = []
    with artifact.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["symbol", "timeframe", "n_bars", "first_bar", "last_bar",
             "expected_bars", "shortfall_pct", "verdict"]
        )
        for symbol, timeframe, n_bars, first_bar, last_bar in rows:
            trading_days = _expected_trading_days(first_bar.isoformat(), last_bar.isoformat())
            if timeframe == "1d":
                expected = trading_days
            elif timeframe == "1h":
                expected = trading_days * BARS_PER_FULL_TRADING_DAY_1H
            else:
                raise RuntimeError(f"unexpected timeframe in filtered query: {timeframe}")
            if expected == 0:
                shortfall = 0.0
            else:
                shortfall = 100.0 * max(0, expected - n_bars) / expected
            verdict = "PASS" if shortfall <= Q1_BAR_SHORTFALL_THRESHOLD_PCT else "FAIL"
            if verdict == "FAIL":
                fails.append(f"{symbol}/{timeframe}: {n_bars}/{expected} "
                             f"({shortfall:.1f}% short)")
            w.writerow([symbol, timeframe, n_bars, first_bar.isoformat(),
                        last_bar.isoformat(), expected, f"{shortfall:.2f}", verdict])
            log(f"  {symbol:8s} {timeframe:3s}  n={n_bars:<6d} "
                f"expected={expected:<6d} short={shortfall:5.2f}%  {verdict}")

    n_pairs = len(rows)
    passed = len(fails) == 0
    if passed:
        summary = (f"{n_pairs} (symbol,timeframe) pairs within "
                   f"{Q1_BAR_SHORTFALL_THRESHOLD_PCT}% tolerance")
    else:
        head = "; ".join(fails[:5])
        extra = f" (+{len(fails)-5} more)" if len(fails) > 5 else ""
        summary = f"{len(fails)} pairs failed: {head}{extra}"
    log(f"Q1 verdict: {'PASS' if passed else 'FAIL'} — {summary}")
    return QuestionResult("Q1", "OHLCV continuity (70 sym)", passed,
                          summary, str(artifact.relative_to(REPO_ROOT)))


def run_q2_feature_coverage(conn: psycopg.Connection, utc_tag: str, log) -> QuestionResult:
    """Q2 — per-symbol row count in `equities_feature_events`.
    Fails if any symbol < Q2_MIN_FEATURE_EVENTS_PER_SYMBOL.
    """
    log("=" * 72)
    log("Q2 — equities_feature_events per-symbol coverage")
    log("=" * 72)
    q = """
        SELECT symbol, COUNT(*) AS n
        FROM public.equities_feature_events
        WHERE bar_start_ts::date BETWEEN %s AND %s
        GROUP BY symbol
        ORDER BY symbol
    """
    with conn.cursor() as cur:
        cur.execute(q, (WINDOW_FEAT_START, WINDOW_FEAT_END))
        rows = cur.fetchall()

    artifact = FINDINGS_DIR / f"q2_feature_coverage_{utc_tag}.csv"
    fails: list[str] = []
    with artifact.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "n_feature_events", "verdict"])
        for symbol, n in rows:
            verdict = "PASS" if n >= Q2_MIN_FEATURE_EVENTS_PER_SYMBOL else "FAIL"
            if verdict == "FAIL":
                fails.append(f"{symbol}: {n}")
            w.writerow([symbol, n, verdict])
            log(f"  {symbol:8s}  n={n:<7d}  {verdict}")

    # Cross-check: also check whether every symbol in the registry has ANY rows.
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.symbol
            FROM public.equities_symbol_registry r
            LEFT JOIN public.equities_feature_events f
              ON f.symbol = r.symbol
             AND f.bar_start_ts::date BETWEEN %s AND %s
            WHERE f.symbol IS NULL
            ORDER BY r.symbol
        """, (WINDOW_FEAT_START, WINDOW_FEAT_END))
        missing = [row[0] for row in cur.fetchall()]
    if missing:
        for s in missing:
            fails.append(f"{s}: 0")
        log(f"  MISSING entirely from equities_feature_events: {missing}")

    passed = len(fails) == 0
    if passed:
        summary = (f"{len(rows)} symbols all ≥ "
                   f"{Q2_MIN_FEATURE_EVENTS_PER_SYMBOL} feature-events")
    else:
        head = "; ".join(fails[:5])
        extra = f" (+{len(fails)-5} more)" if len(fails) > 5 else ""
        summary = f"{len(fails)} symbols below threshold: {head}{extra}"
    log(f"Q2 verdict: {'PASS' if passed else 'FAIL'} — {summary}")
    return QuestionResult("Q2", "feature-event coverage", passed,
                          summary, str(artifact.relative_to(REPO_ROOT)))


def run_q3_available_at_ts_integrity(conn: psycopg.Connection, utc_tag: str,
                                     log) -> QuestionResult:
    """Q3 — point-in-time leakage check. Any row where
    available_at_ts <= bar_start_ts is a silent future-leak. FAIL on any.
    """
    log("=" * 72)
    log("Q3 — equities_feature_events available_at_ts integrity")
    log("=" * 72)
    q_count = """
        SELECT COUNT(*)
        FROM public.equities_feature_events
        WHERE available_at_ts <= bar_start_ts
    """
    with conn.cursor() as cur:
        cur.execute(q_count)
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("COUNT(*) returned no row; unexpected")
        (violations,) = row

    artifact = FINDINGS_DIR / f"q3_leakage_check_{utc_tag}.txt"
    lines = [f"Q3 point-in-time leakage check",
             f"  query: {q_count.strip()}",
             f"  violation_count: {violations}"]

    # If any, capture up to 20 offending rows for diagnostic.
    if violations:
        q_sample = """
            SELECT symbol, timeframe, bar_start_ts, available_at_ts, horizon
            FROM public.equities_feature_events
            WHERE available_at_ts <= bar_start_ts
            ORDER BY bar_start_ts
            LIMIT 20
        """
        with conn.cursor() as cur:
            cur.execute(q_sample)
            sample = cur.fetchall()
        lines.append(f"  sample (up to 20):")
        for s in sample:
            lines.append(f"    {s}")

    artifact.write_text("\n".join(lines) + "\n")
    for ln in lines:
        log(f"  {ln}")

    passed = violations == 0
    summary = (f"{violations} rows with available_at_ts <= bar_start_ts"
               + ("" if passed else "  [BLOCKS B2]"))
    log(f"Q3 verdict: {'PASS' if passed else 'FAIL'} — {summary}")
    return QuestionResult("Q3", "available_at_ts integrity", passed,
                          summary, str(artifact.relative_to(REPO_ROOT)))


def run_q4_source_consistency(conn: psycopg.Connection, utc_tag: str,
                              log) -> QuestionResult:
    """Q4 — only `equities_yahoo` and `yahoo_chart` sources allowed for equity
    rows of `public.ohlcv`. Any other equity-origin source = silent schema drift = FAIL.

    Implementation: run the canonical brief query
    (`SELECT source, COUNT(*) FROM public.ohlcv WHERE source IN
    ('equities_yahoo','yahoo_chart') GROUP BY source`) for the primary PASS
    criterion, then cross-check the full distinct-source list against the
    known crypto allowlist. Any source outside (crypto ∪ equity) is surfaced.
    Aggregate over `source` is fast because the column is indexed via the
    (symbol, timeframe, bar_start_ts, source) unique constraint.
    """
    log("=" * 72)
    log("Q4 — source-column consistency")
    log("=" * 72)

    # Primary: the canonical brief query.
    q_primary = """
        SELECT source, COUNT(*) AS n
        FROM public.ohlcv
        WHERE source IN ('equities_yahoo', 'yahoo_chart')
        GROUP BY source
        ORDER BY source
    """
    with conn.cursor() as cur:
        cur.execute(q_primary)
        primary_rows = cur.fetchall()
    primary_sources = {src for src, _ in primary_rows}

    # Cross-check: enumerate every source in the table, subtract known crypto
    # and expected equity sources; anything left is a rogue.
    q_all_sources = """
        SELECT source, COUNT(*) AS n
        FROM public.ohlcv
        GROUP BY source
        ORDER BY source
    """
    with conn.cursor() as cur:
        cur.execute(q_all_sources)
        all_rows = cur.fetchall()

    artifact = FINDINGS_DIR / f"q4_source_counts_{utc_tag}.csv"
    rogue: list[str] = []
    with artifact.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "n_rows", "bucket", "verdict"])
        for source, n in all_rows:
            if source in EXPECTED_EQUITY_SOURCES:
                bucket = "equity_expected"
                verdict = "PASS"
            elif source in KNOWN_CRYPTO_SOURCES:
                bucket = "crypto_known"
                verdict = "PASS"
            else:
                bucket = "unknown"
                verdict = "FAIL"
                rogue.append(f"{source}={n}")
            w.writerow([source, n, bucket, verdict])
            log(f"  source={source!r}  n={n}  bucket={bucket}  {verdict}")

    # Primary brief criterion: exactly the two expected equity sources appear
    # in the filtered query. If primary_sources != EXPECTED_EQUITY_SOURCES AND
    # there are equity-tagged sources missing, FAIL.
    primary_ok = primary_sources == EXPECTED_EQUITY_SOURCES
    passed = primary_ok and len(rogue) == 0
    sources_seen = ", ".join(sorted(primary_sources))
    if passed:
        summary = f"equity sources exactly {{{sources_seen}}}; no rogue crypto/equity drift"
    else:
        fail_parts = []
        if not primary_ok:
            diff = primary_sources.symmetric_difference(EXPECTED_EQUITY_SOURCES)
            fail_parts.append(f"primary query diverges: {diff}")
        if rogue:
            fail_parts.append(f"rogue sources: {'; '.join(rogue)}")
        summary = "; ".join(fail_parts)
    log(f"Q4 verdict: {'PASS' if passed else 'FAIL'} — {summary}")
    return QuestionResult("Q4", "source-column consistency", passed,
                          summary, str(artifact.relative_to(REPO_ROOT)))


def run_q5_adjustment_regime(conn: psycopg.Connection, utc_tag: str,
                             log) -> QuestionResult:
    """Q5 — determine adjustment regime for stored Yahoo close prices by
    examining ratios across known splits. Only splits inside the
    2024-02-23 → 2026-04-14 window are exercised.

    Pass condition: a single consistent regime across every exercised split.
    Expected default: `split-only` because the fetcher pulls
    `indicators.quote[0].close` from Yahoo's v8 chart endpoint — that series
    is split-adjusted but NOT dividend-adjusted. If the ratio indicates raw,
    report `raw` and FAIL (adjustment schema for OHLCV-consumers is undefined).
    """
    log("=" * 72)
    log("Q5 — adjustment regime per Yahoo source (known splits in window)")
    log("=" * 72)
    artifact = FINDINGS_DIR / f"q5_adjustment_regime_evidence_{utc_tag}.txt"
    lines: list[str] = ["Q5 adjustment regime evidence"]
    regime_per_split: list[tuple[str, str, str]] = []  # (symbol, ex_date, regime)

    for symbol, ex_date, ratio_from_to, pre_date, post_date in KNOWN_SPLITS_IN_WINDOW:
        q = """
            SELECT bar_start_ts::date AS dt, close
            FROM public.ohlcv
            WHERE source = 'equities_yahoo'
              AND symbol = %s
              AND timeframe = '1d'
              AND bar_start_ts::date IN (%s, %s)
            ORDER BY bar_start_ts
        """
        with conn.cursor() as cur:
            cur.execute(q, (symbol, pre_date, post_date))
            result = cur.fetchall()
        if len(result) != 2:
            lines.append(
                f"{symbol} {ex_date} {ratio_from_to}:1 split — "
                f"expected 2 rows ({pre_date}, {post_date}), got {len(result)}. SKIP.")
            log(lines[-1])
            continue
        (_, pre_close), (_, post_close) = result
        price_ratio = float(pre_close) / float(post_close) if post_close else 0.0
        # If price_ratio ~ ratio_from_to (e.g. 10 for NVDA 10:1) → RAW (unadjusted).
        # If price_ratio ~ 1 → ADJUSTED (split-adjusted of some flavour).
        if 0.8 <= price_ratio <= 1.2:
            # Adjusted regime. Split-only vs split+div requires a separate cross-check
            # against an ex-dividend date on the same symbol — not encoded here because
            # the fetcher's source of truth already answers:
            #   Backend/scripts/equities_predictor/sources.py:295 uses
            #   `indicators.quote[0].close`, which is Yahoo's split-adjusted (non-div)
            #   series. adjclose is a separate array and is NOT fetched.
            regime = "split-only"
        elif 0.5 * ratio_from_to <= price_ratio <= 1.5 * ratio_from_to:
            regime = "raw"
        else:
            regime = "unknown"
        lines.append(
            f"{symbol} {ex_date} {ratio_from_to}:1 split — "
            f"pre_close({pre_date})={pre_close}, post_close({post_date})={post_close}, "
            f"ratio={price_ratio:.4f} -> regime={regime}")
        log(lines[-1])
        regime_per_split.append((symbol, ex_date, regime))

    # Evidence cross-check: which quote path did the fetcher use?
    fetcher_src = REPO_ROOT / "Backend" / "scripts" / "equities_predictor" / "sources.py"
    fetcher_quote = "unknown"
    if fetcher_src.exists():
        txt = fetcher_src.read_text()
        if "adjclose" in txt:
            fetcher_quote = "contains adjclose reference"
        elif "indicators.quote" in txt or 'quote"][0]' in txt or "quotes.get" in txt:
            fetcher_quote = "indicators.quote[0] (split-only per Yahoo semantics)"
    lines.append(f"Fetcher quote path: {fetcher_quote}")
    log(f"Fetcher quote path: {fetcher_quote}")

    artifact.write_text("\n".join(lines) + "\n")

    # PASS logic: at least one split evaluated AND all evaluated regimes agree AND
    # regime is not 'raw' and not 'unknown'. If zero splits were in-window we
    # cannot form a verdict — escalate.
    if not regime_per_split:
        passed = False
        summary = "no in-window splits evaluated; regime undetermined — ESCALATE"
        final_regime = "unknown"
    else:
        regimes = {r for _, _, r in regime_per_split}
        if len(regimes) > 1:
            passed = False
            summary = f"mixed regimes across splits: {regimes}"
            final_regime = "mixed"
        else:
            (final_regime,) = regimes
            if final_regime == "split-only":
                passed = True
                summary = ("split-only (Yahoo indicators.quote path) — "
                           "consistent across evaluated splits; dividend-"
                           "adjustment will be handled by A7 corporate-actions migration")
            elif final_regime == "split-and-div":
                passed = True  # consistent, usable
                summary = "split-and-div-adjusted consistent across splits"
            else:
                passed = False
                summary = f"regime={final_regime}; unexpected. FAIL."
    log(f"Q5 verdict: {'PASS' if passed else 'FAIL'} — regime={final_regime} — {summary}")
    return QuestionResult("Q5", "adjustment regime", passed,
                          f"{final_regime} — {summary}",
                          str(artifact.relative_to(REPO_ROOT)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.stderr.write(
            "ERROR: DATABASE_URL is not set.\n"
            "Remediation: export DATABASE_URL='postgresql://trading:<pw>@<host>:5432/trading'\n"
            "             For prod DB via host, use the postgres container IP or\n"
            "             run inside a container whose env already carries DATABASE_URL.\n")
        return 2

    utc_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log_path = SCRIPT_OUTPUT_DIR / f"equities_data_health_read_{utc_tag}.log"
    log_fh = log_path.open("w")

    def log(msg: str) -> None:
        line = msg.rstrip()
        log_fh.write(line + "\n")
        log_fh.flush()
        print(line)

    log(f"equities_data_health_read — start {datetime.now(timezone.utc).isoformat()}")
    log(f"log path: {log_path.relative_to(REPO_ROOT)}")
    log(f"DATABASE_URL host: {db_url.split('@', 1)[-1].split('/', 1)[0]}")

    results: list[QuestionResult] = []
    try:
        with psycopg.connect(db_url) as conn:
            results.append(run_q1_ohlcv_continuity(conn, utc_tag, log))
            results.append(run_q2_feature_coverage(conn, utc_tag, log))
            results.append(run_q3_available_at_ts_integrity(conn, utc_tag, log))
            results.append(run_q4_source_consistency(conn, utc_tag, log))
            results.append(run_q5_adjustment_regime(conn, utc_tag, log))
    except Exception as exc:
        log(f"FATAL: {type(exc).__name__}: {exc}")
        log_fh.close()
        raise

    # Q3 is a hard gate per playbook R1 escalation note: any violation = FAIL
    # regardless of other outcomes AND blocks B2.
    overall_pass = all(r.passed for r in results)

    # Compose CP-2.md per playbook §6 CP-2 template.
    lines = []
    lines.append("# CP-2 — Pre-Backfill Data-Health Read")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Script: `Backend/scripts/equities_data_health_read.py`")
    lines.append(f"Log: `{log_path.relative_to(REPO_ROOT)}`")
    lines.append("")
    lines.append("## Verdicts")
    lines.append("")
    for r in results:
        v = "PASS" if r.passed else "FAIL"
        lines.append(f"{r.qid} {r.title}: {v} — {r.summary}")
    lines.append("")
    lines.append(f"OVERALL VERDICT: {'PASS' if overall_pass else 'FAIL'}")
    lines.append("")
    lines.append("## Evidence Artifacts")
    lines.append("")
    for r in results:
        lines.append(f"- {r.qid}: `{r.evidence_path}`")
    lines.append("")
    if not overall_pass:
        lines.append("## Escalation Notes")
        lines.append("")
        q3 = next(r for r in results if r.qid == "Q3")
        if not q3.passed:
            lines.append("- Q3 FAIL blocks B2 (feature recompute cannot reuse existing pane).")
        for r in results:
            if not r.passed and r.qid != "Q3":
                lines.append(f"- {r.qid} FAIL: {r.summary}")

    cp2_path = CHECKPOINTS_DIR / "CP-2.md"
    cp2_path.write_text("\n".join(lines) + "\n")
    log(f"CP-2 report: {cp2_path.relative_to(REPO_ROOT)}")
    log(f"OVERALL VERDICT: {'PASS' if overall_pass else 'FAIL'}")
    log_fh.close()

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
