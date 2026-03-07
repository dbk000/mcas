#!/usr/bin/env python3
"""
Prune Batch Subset — Create a restricted subset of opportunities by SQL-filtering
on gate values from payload_json.

Reads from a source batch_id, applies gate filters (dormancy, rvol, volume_z,
bb_width) via payload_json::jsonb extraction, and inserts qualifying rows into
a new batch_id. Single INSERT...SELECT — no Python-side filtering.

Usage:
    # In Docker (production):
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/prune_batch_subset.py \
            --source-batch-id 'backfill_20260303_D1v1%' \
            --new-batch-id 'pruned_D1_tight_20260307' \
            --dormancy-min 0.85 \
            --rvol-min 4.0 \
            --volume-z-min 2.5 \
            --bb-width-max 0.04

    # Dry run (count only, no insert):
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/prune_batch_subset.py \
            --source-batch-id 'backfill_20260303_D1v1%' \
            --new-batch-id 'pruned_D1_tight_20260307' \
            --dormancy-min 0.85 \
            --dry-run

Requires DATABASE_URL env var (reads from .env if available).
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load .env if available
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

import psycopg
from psycopg.rows import dict_row

# ── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ── Execution context ─────────────────────────────────────────────────────

IN_DOCKER = os.path.isdir("/app")
if IN_DOCKER:
    SCRIPTS_DIR = Path("/scripts")
else:
    SCRIPTS_DIR = Path(__file__).resolve().parent

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    logger.error("DATABASE_URL not set. Export it or place in Backend/.env")
    sys.exit(1)


def setup_output(script_name: str) -> Path:
    """Create output directory and return path for the report file."""
    if os.path.isdir("/scripts/output"):
        output_dir = Path("/scripts/output")
    elif os.path.isdir(os.path.join(os.path.dirname(__file__), "output")):
        output_dir = Path(os.path.join(os.path.dirname(__file__), "output"))
    else:
        output_dir = Path("/scripts/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{script_name}_{ts_tag}.log"


def build_gate_clauses(args) -> tuple[list[str], list]:
    """Build SQL WHERE clauses and params from gate arguments.

    Returns (clauses, params) where each clause is a SQL condition string
    with %s placeholders and params is the corresponding list of values.
    All filtering uses payload_json::jsonb extraction.
    """
    clauses = []
    params = []

    if args.dormancy_min is not None:
        clauses.append("(payload_json::jsonb->>'dormancy_score')::float >= %s")
        params.append(args.dormancy_min)

    if args.rvol_min is not None:
        clauses.append("(payload_json::jsonb->>'rvol_at_trigger')::float >= %s")
        params.append(args.rvol_min)

    if args.volume_z_min is not None:
        clauses.append("(payload_json::jsonb->>'volume_z_at_trigger')::float >= %s")
        params.append(args.volume_z_min)

    if args.bb_width_max is not None:
        clauses.append("(payload_json::jsonb->>'bb_width_at_trigger')::float <= %s")
        params.append(args.bb_width_max)

    return clauses, params


def count_source(conn, source_batch_pattern: str) -> int:
    """Count total expired opportunities matching source batch_id pattern."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM opportunities WHERE batch_id LIKE %s AND status = 'expired'",
            (source_batch_pattern,),
        )
        return cur.fetchone()[0]


def count_per_gate(conn, source_batch_pattern: str, args) -> dict:
    """Count how many source rows pass each gate individually."""
    gate_counts = {}
    base_where = "batch_id LIKE %s AND status = 'expired'"

    gates = {
        "dormancy_score": (
            "(payload_json::jsonb->>'dormancy_score')::float >= %s",
            args.dormancy_min,
        ),
        "rvol_at_trigger": (
            "(payload_json::jsonb->>'rvol_at_trigger')::float >= %s",
            args.rvol_min,
        ),
        "volume_z_at_trigger": (
            "(payload_json::jsonb->>'volume_z_at_trigger')::float >= %s",
            args.volume_z_min,
        ),
        "bb_width_at_trigger": (
            "(payload_json::jsonb->>'bb_width_at_trigger')::float <= %s",
            args.bb_width_max,
        ),
    }

    with conn.cursor() as cur:
        for gate_name, (clause, value) in gates.items():
            if value is None:
                gate_counts[gate_name] = None  # gate not applied
                continue
            sql = f"SELECT COUNT(*) FROM opportunities WHERE {base_where} AND {clause}"
            cur.execute(sql, (source_batch_pattern, value))
            gate_counts[gate_name] = cur.fetchone()[0]

    return gate_counts


def count_combined(conn, source_batch_pattern: str, gate_clauses: list, gate_params: list) -> int:
    """Count rows passing all gates combined."""
    base_where = "batch_id LIKE %s AND status = 'expired'"
    params = [source_batch_pattern]

    if gate_clauses:
        full_where = f"{base_where} AND " + " AND ".join(gate_clauses)
        params.extend(gate_params)
    else:
        full_where = base_where

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM opportunities WHERE {full_where}", params)
        return cur.fetchone()[0]


def sample_filtered_out(conn, source_batch_pattern: str, gate_clauses: list, gate_params: list, n: int = 5) -> list:
    """Get a sample of opportunities that would be filtered OUT."""
    base_where = "batch_id LIKE %s AND status = 'expired'"
    params = [source_batch_pattern]

    if gate_clauses:
        # Rows that pass base but fail at least one gate
        combined = " AND ".join(gate_clauses)
        full_where = f"{base_where} AND NOT ({combined})"
        params.extend(gate_params)
    else:
        # No gates, nothing filtered out
        return []

    sql = f"""
        SELECT symbol,
               (payload_json::jsonb->>'dormancy_score')::float AS dormancy,
               (payload_json::jsonb->>'rvol_at_trigger')::float AS rvol,
               (payload_json::jsonb->>'volume_z_at_trigger')::float AS vol_z,
               (payload_json::jsonb->>'bb_width_at_trigger')::float AS bb_width
        FROM opportunities
        WHERE {full_where}
        ORDER BY created_ts DESC
        LIMIT %s
    """
    params.append(n)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def check_new_batch_exists(conn, new_batch_id: str) -> int:
    """Check if new_batch_id already has rows. Returns count."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM opportunities WHERE batch_id = %s",
            (new_batch_id,),
        )
        return cur.fetchone()[0]


def insert_subset(conn, source_batch_pattern: str, new_batch_id: str,
                  gate_clauses: list, gate_params: list) -> int:
    """INSERT...SELECT filtered subset with new batch_id."""
    base_where = "batch_id LIKE %s AND status = 'expired'"
    params = [new_batch_id, source_batch_pattern]

    if gate_clauses:
        full_where = f"{base_where} AND " + " AND ".join(gate_clauses)
        params.extend(gate_params)
    else:
        full_where = base_where

    sql = f"""
        INSERT INTO opportunities
            (created_ts, symbol, timeframe, setup_id, decision,
             score, reason, payload_json, status, setup_version,
             batch_id, trace_json)
        SELECT created_ts, symbol, timeframe, setup_id, decision,
               score, reason, payload_json, status, setup_version,
               %s, trace_json
        FROM opportunities
        WHERE {full_where}
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        inserted = cur.rowcount
    conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser(
        description="Create a pruned subset of opportunities by filtering on payload_json gate values"
    )
    parser.add_argument("--source-batch-id", type=str, required=True,
                        help="Source batch_id pattern (supports SQL LIKE wildcards, e.g. 'backfill_20260303_D1v1%%')")
    parser.add_argument("--new-batch-id", type=str, required=True,
                        help="New batch_id for the filtered subset")
    parser.add_argument("--dormancy-min", type=float, default=None,
                        help="Minimum dormancy_score (payload_json->dormancy_score >= X)")
    parser.add_argument("--rvol-min", type=float, default=None,
                        help="Minimum rvol_at_trigger (payload_json->rvol_at_trigger >= X)")
    parser.add_argument("--volume-z-min", type=float, default=None,
                        help="Minimum volume_z_at_trigger (payload_json->volume_z_at_trigger >= X)")
    parser.add_argument("--bb-width-max", type=float, default=None,
                        help="Maximum bb_width_at_trigger (payload_json->bb_width_at_trigger <= X)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count only, do not insert")
    args = parser.parse_args()

    # ── Output setup ──────────────────────────────────────────────────────
    output_path = setup_output("prune_batch_subset")
    file_handler = logging.FileHandler(str(output_path), mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(file_handler)
    logger.info("Output file: %s", output_path)
    logger.info("=" * 70)
    logger.info("PRUNE BATCH SUBSET")
    logger.info("=" * 70)
    logger.info("Source batch pattern: %s", args.source_batch_id)
    logger.info("New batch_id:         %s", args.new_batch_id)
    logger.info("Dry run:              %s", args.dry_run)
    logger.info("")

    # Log gate thresholds
    gates_applied = []
    if args.dormancy_min is not None:
        gates_applied.append(f"dormancy_score >= {args.dormancy_min}")
    if args.rvol_min is not None:
        gates_applied.append(f"rvol_at_trigger >= {args.rvol_min}")
    if args.volume_z_min is not None:
        gates_applied.append(f"volume_z_at_trigger >= {args.volume_z_min}")
    if args.bb_width_max is not None:
        gates_applied.append(f"bb_width_at_trigger <= {args.bb_width_max}")

    if not gates_applied:
        logger.warning("No gate filters specified. This would copy ALL source rows unchanged.")
        logger.warning("Specify at least one of: --dormancy-min, --rvol-min, --volume-z-min, --bb-width-max")
        sys.exit(1)

    logger.info("Gate filters:")
    for g in gates_applied:
        logger.info("  %s", g)
    logger.info("")

    t0 = time.time()

    # ── Build gate SQL ────────────────────────────────────────────────────
    gate_clauses, gate_params = build_gate_clauses(args)

    # ── Connect and run analysis ──────────────────────────────────────────
    with psycopg.connect(DATABASE_URL) as conn:
        # Step 1: Count source
        logger.info("Counting source opportunities...")
        source_count = count_source(conn, args.source_batch_id)
        logger.info("Source batch count: %s", f"{source_count:,}")

        if source_count == 0:
            logger.error("No expired opportunities found matching batch_id LIKE '%s'", args.source_batch_id)
            logger.error("Check the --source-batch-id pattern. Use '%%' for SQL wildcards.")
            sys.exit(1)

        # Step 2: Per-gate individual filter counts
        logger.info("")
        logger.info("Per-gate filter (each gate individually):")
        per_gate = count_per_gate(conn, args.source_batch_id, args)
        for gate_name, count in per_gate.items():
            if count is None:
                logger.info("  %-25s: not applied", gate_name)
            else:
                pct = (count / source_count * 100) if source_count > 0 else 0
                logger.info("  %-25s: %6s pass (%5.1f%%)", gate_name, f"{count:,}", pct)

        # Step 3: Combined filter count
        logger.info("")
        combined_count = count_combined(conn, args.source_batch_id, gate_clauses, gate_params)
        filter_rate = ((source_count - combined_count) / source_count * 100) if source_count > 0 else 0
        logger.info("Combined filter: %s pass all gates (%5.1f%% of source)",
                     f"{combined_count:,}", (combined_count / source_count * 100) if source_count > 0 else 0)
        logger.info("Filtered out:    %s (%.1f%%)",
                     f"{source_count - combined_count:,}", filter_rate)

        # Step 4: Sample of filtered-out opportunities
        logger.info("")
        filtered_sample = sample_filtered_out(conn, args.source_batch_id, gate_clauses, gate_params, n=5)
        if filtered_sample:
            logger.info("Sample of filtered-out opportunities (up to 5):")
            logger.info("  %-18s %8s %8s %8s %8s", "symbol", "dormancy", "rvol", "vol_z", "bb_width")
            logger.info("  %s", "-" * 54)
            for row in filtered_sample:
                logger.info("  %-18s %8s %8s %8s %8s",
                            row.get("symbol", "?"),
                            f"{row['dormancy']:.4f}" if row.get("dormancy") is not None else "N/A",
                            f"{row['rvol']:.2f}" if row.get("rvol") is not None else "N/A",
                            f"{row['vol_z']:.2f}" if row.get("vol_z") is not None else "N/A",
                            f"{row['bb_width']:.4f}" if row.get("bb_width") is not None else "N/A")
        else:
            logger.info("No rows filtered out (all pass).")

        # Step 5: Insert or dry-run
        logger.info("")
        if args.dry_run:
            logger.info("DRY RUN — no rows inserted.")
            logger.info("Would insert %s rows with batch_id = '%s'", f"{combined_count:,}", args.new_batch_id)
        else:
            # Validate new batch_id doesn't already exist
            existing = check_new_batch_exists(conn, args.new_batch_id)
            if existing > 0:
                logger.error("batch_id '%s' already has %s rows. Aborting to prevent duplicates.",
                             args.new_batch_id, f"{existing:,}")
                logger.error("Choose a different --new-batch-id or delete existing rows first.")
                sys.exit(1)

            logger.info("Inserting filtered subset...")
            inserted = insert_subset(conn, args.source_batch_id, args.new_batch_id,
                                     gate_clauses, gate_params)
            logger.info("Inserted %s rows with batch_id = '%s'", f"{inserted:,}", args.new_batch_id)

            if inserted != combined_count:
                logger.warning("Inserted count (%s) differs from pre-count (%s) — possible concurrent modification",
                               inserted, combined_count)

    elapsed = time.time() - t0

    # ── Summary ───────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info("Source batch:    %s  (%s rows)", args.source_batch_id, f"{source_count:,}")
    logger.info("Gates applied:   %s", ", ".join(gates_applied))
    logger.info("Passing gates:   %s  (%.1f%%)", f"{combined_count:,}",
                (combined_count / source_count * 100) if source_count > 0 else 0)
    if not args.dry_run:
        logger.info("New batch_id:    %s", args.new_batch_id)
        logger.info("Rows inserted:   %s", f"{inserted:,}")
    else:
        logger.info("Mode:            DRY RUN (no insert)")
    logger.info("Elapsed:         %.1fs", elapsed)
    logger.info("Output:          %s", output_path)
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
