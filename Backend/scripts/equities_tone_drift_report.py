#!/usr/bin/env python3
"""equities_tone_drift_report — scheduled entry point for the tone-drift monitor.

Modes
-----
1. ``--build-baseline``
   Pull titles from ``equities_events`` over ``--window-start`` → ``--window-end``
   (inclusive), apply `score_title_tone` to each, build a training-time
   distribution, write ``Backend/scripts/output/tone_baseline.json``.
   Also computes a representative `baseline_kl` from non-overlapping
   disjoint chunks of the training window (default 6 chunks).

2. ``--report``
   Pull the last 30 days of titles from ``equities_events``, score them,
   compute KL against the baseline, compare against the 5-day KL history
   (maintained in ``Backend/scripts/output/tone_drift_history.json``),
   and write an alert row to ``equities_pipeline_events`` if
   ``trigger_active`` fires.

Either mode writes a human-readable audit log to
``Backend/scripts/output/equities_tone_drift_report_<utc>.log``.

Inputs
------
- ``DATABASE_URL`` env var (required; crashes loud without it).

Outputs (report mode)
---------------------
- Updated ``Backend/scripts/output/tone_drift_history.json`` with the
  newly-computed daily KL.
- One row in ``equities_pipeline_events`` iff ``trigger_active``.
- Log file with full audit trail.

Notes / caveats
---------------
- Brief step 2 says "reads last 30 days of tone scores from
  `equities_feature_events`". Tone scores are NOT persisted in
  `equities_feature_events` today (only `sentiment_score`, `event_count_*`,
  `anti_bot_mean_*` land in `features`). Tone is computed on-the-fly from
  `equities_events.title` by the features pipeline. This script honours
  the brief's intent ("monitor the tone-scorer's output distribution
  over a rolling 30-day window") by re-scoring titles on demand. See
  `equities_plan_work/findings/R6/findings.md` for the rationale.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import psycopg

# Make Backend/ importable when the script is invoked directly from repo root.
_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parents[1]
if str(_BACKEND) not in sys.path:  # EXEMPT: script bootstrap import path (not dispatch)
    sys.path.insert(0, str(_BACKEND))  # EXEMPT: script bootstrap import path (not dispatch)

from governance.equities.tone_drift_monitor import (  # noqa: E402
    BASELINE_SCHEMA_VERSION,
    CONSECUTIVE_DAYS_TO_TRIGGER,
    DEFAULT_BASELINE_PATH,
    DRIFT_TRIGGER_MULTIPLIER,
    MIN_SAMPLES_FOR_KL,
    MIN_TRIGGER_ABS_KL,
    ToneDriftMonitor,
    build_baseline_artifact,
    compute_kl_divergence,
    load_baseline,
)
from governance.equities.tone_scorer import score_title_tone  # noqa: E402


REPO_ROOT = _BACKEND.parent
HISTORY_PATH = REPO_ROOT / "Backend" / "scripts" / "output" / "tone_drift_history.json"
SCRIPT_OUTPUT_DIR = REPO_ROOT / "Backend" / "scripts" / "output"

# Rolling window in trading days for the report-mode distribution.
# Review §2.6: "rolling 30-day window". Named for diff visibility.
ROLLING_DAYS = 30

# Number of disjoint chunks used to compute the baseline's internal KL
# (held-out-vs-pooled). 6 chunks of a ~6-month training window ≈ 1-month
# chunks, a reasonable balance between chunk-size noise and chunk-count
# stability.
BASELINE_HELDOUT_CHUNKS = 6


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------

def _connect(db_url: str) -> psycopg.Connection:
    return psycopg.connect(db_url)


def fetch_titles(
    conn: psycopg.Connection,
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple[str, str, float]]:
    """Fetch (title, source, anti_bot_score) for every event in the window.

    All sources are returned; `score_title_tone` filters to OPEN_NEWS
    internally. Returning all rows lets the caller report coverage
    (how many events were tone-eligible vs not).
    """
    q = """
        SELECT title, source, COALESCE(anti_bot_score, 0.0) AS abs
        FROM public.equities_events
        WHERE available_at_ts >= %s
          AND available_at_ts <  %s
        ORDER BY available_at_ts
    """
    with conn.cursor() as cur:
        cur.execute(q, (start_ts, end_ts))
        return [(t or "", s or "", float(a or 0.0)) for t, s, a in cur.fetchall()]


def score_all(rows: list[tuple[str, str, float]]) -> list[float]:
    """Run `score_title_tone` over every row, return nonzero-inclusive scores.

    Zero-score titles ARE included — the zero-mass bin dominates the
    baseline distribution and its drift (e.g. keyword-set changes) is
    precisely what the monitor catches.
    """
    return [score_title_tone(t, source=s, anti_bot_score=a) for t, s, a in rows]


def write_pipeline_alert(
    conn: psycopg.Connection,
    message: str,
    data: dict,
) -> None:
    q = """
        INSERT INTO public.equities_pipeline_events (actor, event_type, message, data)
        VALUES (%s, %s, %s, %s::jsonb)
    """
    with conn.cursor() as cur:
        cur.execute(q, ("tone_drift_monitor", "TONE_DRIFT_ALERT",
                        message, json.dumps(data, sort_keys=True)))
    conn.commit()


# ---------------------------------------------------------------------------
# History file I/O
# ---------------------------------------------------------------------------

def load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open("r") as f:
        blob = json.load(f)
    entries = blob.get("entries") or []
    if not isinstance(entries, list):
        raise RuntimeError(
            f"Corrupt history file at {HISTORY_PATH}: 'entries' is not a list.")
    return entries


def save_history(entries: list[dict]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w") as f:
        json.dump({"schema_version": "1.0", "entries": entries}, f, indent=2)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_scores(scores: list[float], n_chunks: int) -> list[list[float]]:
    """Split `scores` into `n_chunks` disjoint near-equal chunks.

    Caller guarantees non-empty `scores`. Returns empty list if
    `n_chunks <= 1` (no held-out comparison possible).
    """
    if n_chunks <= 1:
        return []
    total = len(scores)
    if total < n_chunks:
        # Too few samples to chunk; caller will use baseline_kl=0.0 floor.
        return []
    size = total // n_chunks
    chunks: list[list[float]] = []
    for i in range(n_chunks):
        lo = i * size
        hi = (i + 1) * size if i < n_chunks - 1 else total
        chunks.append(scores[lo:hi])
    return chunks


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def mode_build_baseline(
    conn: psycopg.Connection,
    window_start: datetime,
    window_end: datetime,
    log,
) -> int:
    log(f"Building tone baseline from {window_start.isoformat()} to "
        f"{window_end.isoformat()}")
    rows = fetch_titles(conn, window_start, window_end)
    log(f"Fetched {len(rows)} events from equities_events in window")
    scores = score_all(rows)
    n_total = len(scores)
    n_nonzero = sum(1 for s in scores if s != 0.0)
    log(f"Scored {n_total} events; {n_nonzero} produced nonzero directional tone "
        f"({100.0 * n_nonzero / max(n_total, 1):.1f}%)")

    if n_total < MIN_SAMPLES_FOR_KL:
        log(f"FATAL: training window has {n_total} events; "
            f"need >= {MIN_SAMPLES_FOR_KL}. Widen the window.")
        return 1

    chunks = chunk_scores(scores, BASELINE_HELDOUT_CHUNKS)
    built_at = datetime.now(timezone.utc).isoformat()
    payload = build_baseline_artifact(
        training_scores=scores,
        built_at_iso=built_at,
        heldout_chunks=chunks if chunks else None,
    )
    DEFAULT_BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_BASELINE_PATH.open("w") as f:
        json.dump(payload, f, indent=2)
    log(f"Wrote baseline: {DEFAULT_BASELINE_PATH}")
    log(f"  schema_version={payload['schema_version']}")
    log(f"  baseline_kl={payload['baseline_kl']:.6f} "
        f"(held-out chunks: {len(chunks)})")
    log(f"  n_samples={payload['n_samples']}")
    return 0


def mode_report(conn: psycopg.Connection, log) -> int:
    monitor = ToneDriftMonitor.from_path()
    log(f"Loaded baseline built_at={monitor.baseline.built_at} "
        f"schema_version={monitor.baseline.schema_version} "
        f"baseline_kl={monitor.baseline.baseline_kl:.6f} "
        f"n_samples={monitor.baseline.n_samples}")

    now = datetime.now(timezone.utc)
    window_end = now
    window_start = now - timedelta(days=ROLLING_DAYS)
    rows = fetch_titles(conn, window_start, window_end)
    scores = score_all(rows)
    log(f"Rolling {ROLLING_DAYS}d window: {len(scores)} events scored")

    if len(scores) < MIN_SAMPLES_FOR_KL:
        log(f"Insufficient samples ({len(scores)} < {MIN_SAMPLES_FOR_KL}); "
            f"skipping KL computation for today's entry.")
        return 0

    kl = compute_kl_divergence(scores, monitor.baseline)
    log(f"Today's KL = {kl:.6f}")

    history = load_history()
    history.append({
        "date": now.date().isoformat(),
        "kl": kl,
        "n_samples": len(scores),
    })
    # Keep the last 30 entries in history; older entries are logged, not
    # consulted.
    if len(history) > 30:
        history = history[-30:]
    save_history(history)

    recent_kls = [entry["kl"] for entry in history[-CONSECUTIVE_DAYS_TO_TRIGGER:]]
    fired = monitor.trigger_active(recent_kls)
    effective_baseline = max(monitor.baseline.baseline_kl, MIN_TRIGGER_ABS_KL)
    threshold = DRIFT_TRIGGER_MULTIPLIER * effective_baseline
    log(f"Recent {CONSECUTIVE_DAYS_TO_TRIGGER}d KL: {recent_kls}")
    log(f"Trigger threshold (2x baseline or floor): {threshold:.6f}")
    log(f"Trigger active: {fired}")

    if fired:
        message = (f"Tone-scorer drift trigger: KL > {threshold:.6f} for "
                   f"{CONSECUTIVE_DAYS_TO_TRIGGER} consecutive days")
        data = {
            "baseline_kl": monitor.baseline.baseline_kl,
            "threshold": threshold,
            "recent_kls": recent_kls,
            "n_samples_today": len(scores),
            "monitor_schema_version": monitor.baseline.schema_version,
        }
        write_pipeline_alert(conn, message, data)
        log(f"Alert written to equities_pipeline_events: {message}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_iso(value: str) -> datetime:
    """Parse ISO date or datetime; assume UTC if tz-naive."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Could not parse {value!r} as ISO datetime. "
            "Expected e.g. 2025-01-01 or 2025-01-01T00:00:00+00:00") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tone-drift monitor (R6)")
    parser.add_argument("--build-baseline", action="store_true",
                        help="Build a new baseline artifact from a training window.")
    parser.add_argument("--report", action="store_true",
                        help="Compute today's rolling KL and update history; "
                             "alert on sustained drift.")
    parser.add_argument("--window-start", type=str,
                        help="Training window start (ISO date/datetime). "
                             "Required with --build-baseline.")
    parser.add_argument("--window-end", type=str,
                        help="Training window end (ISO date/datetime, exclusive). "
                             "Required with --build-baseline.")
    args = parser.parse_args(argv)

    if args.build_baseline == args.report:
        parser.error("Specify exactly one of --build-baseline or --report.")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.stderr.write(
            "ERROR: DATABASE_URL is not set.\n"
            "Remediation: export DATABASE_URL='postgresql://trading:<pw>@<host>:5432/trading'\n")
        return 2

    SCRIPT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    utc_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = SCRIPT_OUTPUT_DIR / f"equities_tone_drift_report_{utc_tag}.log"
    log_fh = log_path.open("w")

    def log(msg: str) -> None:
        line = msg.rstrip()
        log_fh.write(line + "\n")
        log_fh.flush()
        print(line)

    log(f"equities_tone_drift_report — start {datetime.now(timezone.utc).isoformat()}")
    log(f"log path: {log_path.relative_to(REPO_ROOT)}")
    log(f"schema_version={BASELINE_SCHEMA_VERSION}")

    try:
        with _connect(db_url) as conn:
            if args.build_baseline:
                if not (args.window_start and args.window_end):
                    log("FATAL: --build-baseline requires --window-start and --window-end")
                    log_fh.close()
                    return 2
                w_start = _parse_iso(args.window_start)
                w_end = _parse_iso(args.window_end)
                rc = mode_build_baseline(conn, w_start, w_end, log)
            else:
                rc = mode_report(conn, log)
    except FileNotFoundError as exc:
        log(f"FATAL: {exc}")
        log_fh.close()
        return 2
    except Exception as exc:
        log(f"FATAL: {type(exc).__name__}: {exc}")
        log_fh.close()
        raise

    log(f"exit rc={rc}")
    log_fh.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
