#!/usr/bin/env python3
"""Apply equities SQL migrations if they are not yet in schema_versions."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

_PSYCOPG_IMPORT_ERROR: Exception | None = None
try:
    import psycopg
except ModuleNotFoundError as exc:  # pragma: no cover - handled at runtime
    psycopg = None  # type: ignore[assignment]
    _PSYCOPG_IMPORT_ERROR = exc

MIGRATION_GLOB = "*_add_equities_*.sql"


def _default_migrations_dir() -> Path:
    backend_dir = Path(__file__).resolve().parents[1]
    return backend_dir / "docs" / "db" / "migrations"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply equities add_* migrations exactly once.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--migrations-dir", default=str(_default_migrations_dir()))
    return parser.parse_args()


def _ensure_schema_versions_table(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            checksum TEXT,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            applied_by TEXT DEFAULT current_user
        );
        """
    )


def _is_applied(cur: Any, filename: str) -> bool:
    cur.execute("SELECT 1 FROM schema_versions WHERE filename = %s LIMIT 1;", (filename,))
    return cur.fetchone() is not None


def _record_applied(cur: Any, filename: str, checksum: str) -> None:
    cur.execute(
        """
        INSERT INTO schema_versions (filename, checksum)
        VALUES (%s, %s)
        ON CONFLICT (filename) DO NOTHING;
        """,
        (filename, checksum),
    )


def _find_migration_files(migrations_dir: Path) -> list[Path]:
    return sorted(path for path in migrations_dir.glob(MIGRATION_GLOB) if path.is_file())


def _apply_one(conn: Any, migration_path: Path) -> bool:
    sql_text = migration_path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()

    with conn.transaction():
        with conn.cursor() as cur:
            if _is_applied(cur, migration_path.name):
                print(f"Skipping {migration_path.name}: already applied")
                return False

            print(f"Applying {migration_path.name}")
            cur.execute(sql_text)
            _record_applied(cur, migration_path.name, checksum)
            print(f"Applied {migration_path.name}")
            return True


def main() -> int:
    if psycopg is None:
        print(
            "ERROR: psycopg is required to run equities_migrate.py. "
            "Install `psycopg` (or `psycopg[binary]`) and rerun.",
            file=sys.stderr,
        )
        if _PSYCOPG_IMPORT_ERROR is not None:
            print(f"Import error: {_PSYCOPG_IMPORT_ERROR}", file=sys.stderr)
        return 2

    args = _parse_args()
    database_url = args.database_url
    if not database_url:
        print(
            "ERROR: DATABASE_URL is not configured. "
            "Set DATABASE_URL or pass --database-url.",
            file=sys.stderr,
        )
        return 2

    migrations_dir = Path(args.migrations_dir).expanduser().resolve()
    if not migrations_dir.exists():
        print(f"ERROR: migrations directory not found: {migrations_dir}", file=sys.stderr)
        return 2

    migration_files = _find_migration_files(migrations_dir)
    if not migration_files:
        print(
            f"ERROR: no migration files matched {MIGRATION_GLOB} in {migrations_dir}",
            file=sys.stderr,
        )
        return 1

    applied = 0
    skipped = 0
    try:
        with psycopg.connect(database_url) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    _ensure_schema_versions_table(cur)

            for migration_path in migration_files:
                if _apply_one(conn, migration_path):
                    applied += 1
                else:
                    skipped += 1
    except Exception as exc:
        print(f"ERROR: migration run failed: {exc}", file=sys.stderr)
        return 1

    print(
        {
            "migrations_total": len(migration_files),
            "applied": applied,
            "skipped": skipped,
            "migrations_dir": str(migrations_dir),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
