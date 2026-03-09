#!/usr/bin/env python3
"""
db_cleanup.py — Retention policy enforcement for MCAS database tables.
======================================================================

Deletes rows older than the configured retention period for each table.
Default mode is dry-run (count only). Pass --execute to actually delete.

Retention rules:
  signal_events  → 90 days  (partitioned, uses created_at)
  social_posts   → 180 days (uses post_ts)
  frontend_logs  → 30 days  (uses server_ts)
  run_events     → 90 days  (uses ts)
  ohlcv          → indefinite (skipped)
  simulation_results → indefinite (skipped)

Usage (inside Docker):
    python3 /scripts/db_cleanup.py                         # dry-run all tables
    python3 /scripts/db_cleanup.py --table frontend_logs   # dry-run one table
    python3 /scripts/db_cleanup.py --execute               # delete for real
    python3 /scripts/db_cleanup.py --execute --table social_posts --batch-size 5000

Usage (on host via Docker):
    docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/db_cleanup.py
    docker exec backend-worker-1 python3 /scripts/db_cleanup.py --execute

Output written to: scripts/output/db_cleanup_{timestamp}.txt
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

# ─── Execution context detection ─────────────────────────────────────────────
if os.path.isdir("/app"):
    # Inside Docker container
    OUTPUT_DIR = "/scripts/output"
    DB_URL_DEFAULT = os.getenv(
        "DATABASE_URL",
        "postgresql://trading:trading@postgres:5432/trading",
    )
else:
    # Running on host
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
    DB_URL_DEFAULT = os.getenv(
        "DATABASE_URL",
        "postgresql://trading:trading@localhost:5432/trading",
    )

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── Retention rules ─────────────────────────────────────────────────────────
# Each entry: (table_name, timestamp_column, retention_days, notes)
RETENTION_RULES = [
    ("signal_events", "created_at", 90, "Partitioned table — deletes from partitions"),
    ("social_posts", "post_ts", 180, "Raw social posts"),
    ("frontend_logs", "server_ts", 30, "Frontend telemetry logs"),
    ("run_events", "ts", 90, "Run event logs"),
]

# Tables explicitly skipped (documented for auditability)
SKIPPED_TABLES = [
    ("ohlcv", "indefinite", "Needed for backtests"),
    ("simulation_results", "indefinite", "Historical sim results"),
    ("opportunities", "indefinite", "Signal history for analysis"),
]


def get_connection(database_url: str):
    """Create a psycopg connection (not pooled — one-off script)."""
    import psycopg
    conn = psycopg.connect(database_url, autocommit=False)
    return conn


def count_eligible(conn, table: str, ts_col: str, retention_days: int) -> int:
    """Count rows older than the retention cutoff."""
    sql = (
        f"SELECT COUNT(*) FROM {table} "
        f"WHERE {ts_col} < NOW() - %s * INTERVAL '1 day'"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (retention_days,))
        return cur.fetchone()[0]


def count_total(conn, table: str) -> int:
    """Count total rows in a table (uses pg_class estimate for speed on large tables)."""
    # Try fast estimate first
    sql = (
        "SELECT reltuples::bigint FROM pg_class "
        "WHERE relname = %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (table,))
        row = cur.fetchone()
        if row and row[0] >= 0:
            return int(row[0])
        # Fallback to exact count
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def delete_in_batches(
    conn,
    table: str,
    ts_col: str,
    retention_days: int,
    batch_size: int,
    log_fn,
) -> int:
    """Delete rows older than retention in batches. Returns total deleted."""
    total_deleted = 0
    batch_num = 0
    t0 = time.time()

    while True:
        batch_num += 1
        # Use ctid-based batch delete for efficiency
        sql = (
            f"DELETE FROM {table} "
            f"WHERE ctid = ANY(ARRAY( "
            f"  SELECT ctid FROM {table} "
            f"  WHERE {ts_col} < NOW() - %s * INTERVAL '1 day' "
            f"  LIMIT %s "
            f"))"
        )
        with conn.cursor() as cur:
            cur.execute(sql, (retention_days, batch_size))
            deleted = cur.rowcount
        conn.commit()

        total_deleted += deleted
        elapsed = time.time() - t0

        if deleted > 0:
            msg = (
                f"  Batch {batch_num}: deleted {deleted} rows "
                f"(total: {total_deleted}, elapsed: {elapsed:.1f}s)"
            )
            log_fn(msg)

        if deleted < batch_size:
            # Last batch — no more rows to delete
            break

    return total_deleted


def main():
    parser = argparse.ArgumentParser(
        description="MCAS DB retention cleanup — dry-run by default",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete rows (default: dry-run, count only)",
    )
    parser.add_argument(
        "--table",
        type=str,
        default=None,
        help="Cleanup a specific table only (e.g., frontend_logs)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        help="Rows per delete batch (default: 10000)",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=DB_URL_DEFAULT,
        help="PostgreSQL connection URL",
    )
    args = parser.parse_args()

    # ── Output file ──
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"db_cleanup_{ts_str}.txt")

    lines = []

    def log(msg: str):
        """Print to stdout and buffer for file output."""
        print(msg, flush=True)
        lines.append(msg)

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    log(f"=== MCAS DB Cleanup — {mode} ===")
    log(f"Started: {datetime.now(timezone.utc).isoformat()}")
    log(f"Output:  {output_path}")
    log(f"Batch size: {args.batch_size}")
    log("")

    # ── Filter rules if --table specified ──
    rules = RETENTION_RULES
    if args.table:
        rules = [r for r in rules if r[0] == args.table]
        if not rules:
            valid = [r[0] for r in RETENTION_RULES]
            log(f"ERROR: Table '{args.table}' not in retention rules.")
            log(f"Valid tables: {', '.join(valid)}")
            _write_output(output_path, lines)
            sys.exit(1)

    # ── Connect ──
    try:
        import psycopg  # noqa: F811
    except ImportError:
        log("ERROR: psycopg not installed. Run inside Docker or install psycopg.")
        _write_output(output_path, lines)
        sys.exit(1)

    try:
        conn = get_connection(args.database_url)
    except Exception as e:
        log(f"ERROR: Could not connect to database: {e}")
        _write_output(output_path, lines)
        sys.exit(1)

    log(f"Connected to database.")
    log("")

    # ── Process each table ──
    t_start = time.time()
    summary = []

    for table, ts_col, retention_days, notes in rules:
        log(f"─── {table} (retention: {retention_days} days, column: {ts_col}) ───")
        log(f"    {notes}")

        try:
            total_rows = count_total(conn, table)
            eligible = count_eligible(conn, table, ts_col, retention_days)
            pct = (eligible / total_rows * 100) if total_rows > 0 else 0

            log(f"    Total rows (estimate): {total_rows:,}")
            log(f"    Rows older than {retention_days} days: {eligible:,} ({pct:.1f}%)")

            if args.execute and eligible > 0:
                log(f"    Deleting in batches of {args.batch_size}...")
                deleted = delete_in_batches(
                    conn, table, ts_col, retention_days,
                    args.batch_size, log,
                )
                log(f"    DONE: Deleted {deleted:,} rows from {table}")
                summary.append((table, total_rows, eligible, deleted))
            else:
                if eligible == 0:
                    log(f"    Nothing to delete.")
                else:
                    log(f"    [DRY-RUN] Would delete {eligible:,} rows.")
                summary.append((table, total_rows, eligible, 0))

        except Exception as e:
            log(f"    ERROR processing {table}: {e}")
            summary.append((table, -1, -1, 0))
            # Roll back any partial transaction
            try:
                conn.rollback()
            except Exception:
                pass

        log("")

    # ── Skipped tables ──
    log("─── Skipped tables (indefinite retention) ───")
    for table, policy, reason in SKIPPED_TABLES:
        log(f"    {table}: {policy} — {reason}")
    log("")

    # ── Summary ──
    elapsed = time.time() - t_start
    log("═══ Summary ═══")
    log(f"{'Table':<20} {'Total':>12} {'Eligible':>12} {'Deleted':>12}")
    log("─" * 58)
    for table, total, eligible, deleted in summary:
        t_str = f"{total:,}" if total >= 0 else "ERROR"
        e_str = f"{eligible:,}" if eligible >= 0 else "ERROR"
        d_str = f"{deleted:,}" if deleted > 0 else "—"
        log(f"{table:<20} {t_str:>12} {e_str:>12} {d_str:>12}")
    log("─" * 58)
    log(f"Elapsed: {elapsed:.1f}s")
    log(f"Mode: {mode}")
    log(f"Finished: {datetime.now(timezone.utc).isoformat()}")

    # ── Write output file ──
    _write_output(output_path, lines)
    log(f"\nResults written to: {output_path}")

    conn.close()


def _write_output(path: str, lines: list[str]):
    """Write buffered log lines to output file."""
    try:
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"WARNING: Could not write output file {path}: {e}", flush=True)


if __name__ == "__main__":
    main()
