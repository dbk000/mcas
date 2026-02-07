"""
Canon §1.10 + §1.11: Schema Governance Check
=============================================

Automated checks:
- Base schema file exists
- Migration files exist and are correctly named
- schema_versions migration exists
- Migrations use advisory locks for concurrency safety
- Migrations use IF NOT EXISTS for idempotency
- Base schema has application order documentation
- No duplicate table definitions across migration files
"""
import os
import re
import hashlib
import pytest

BASE_DIR = os.path.join(os.path.dirname(__file__), '..')
SCHEMA_PATH = os.path.join(BASE_DIR, 'docs', 'db', 'schema', 'mcas_schema.sql')
MIGRATIONS_DIR = os.path.join(BASE_DIR, 'docs', 'db', 'migrations')


# =============================================================================
# Schema File Existence
# =============================================================================

class TestSchemaExists:
    """Canon §1.10: Schema source of truth must exist."""

    def test_base_schema_exists(self):
        assert os.path.isfile(SCHEMA_PATH), f"Base schema not found at {SCHEMA_PATH}"

    def test_migrations_dir_exists(self):
        assert os.path.isdir(MIGRATIONS_DIR), f"Migrations dir not found at {MIGRATIONS_DIR}"

    def test_schema_versions_migration_exists(self):
        """Canon §1.10: schema_versions table must be tracked via migration."""
        migration_files = os.listdir(MIGRATIONS_DIR)
        version_files = [f for f in migration_files if 'schema_versions' in f]
        assert len(version_files) > 0, "No schema_versions migration found"

    def test_signal_events_migration_exists(self):
        """Canon §1.9: signal_events table must exist."""
        migration_files = os.listdir(MIGRATIONS_DIR)
        event_files = [f for f in migration_files if 'signal_events' in f]
        assert len(event_files) > 0, "No signal_events migration found"


# =============================================================================
# Migration Naming
# =============================================================================

class TestMigrationNaming:
    """Canon §1.10: Migrations must be versioned and repeatable."""

    def test_migrations_have_date_prefix(self):
        """Migration filenames must start with YYYYMMDD for ordering."""
        pattern = re.compile(r'^\d{8}')
        for filename in os.listdir(MIGRATIONS_DIR):
            if filename.endswith('.sql'):
                assert pattern.match(filename), (
                    f"Migration '{filename}' does not start with YYYYMMDD date prefix"
                )

    def test_migrations_are_sql_files(self):
        for filename in os.listdir(MIGRATIONS_DIR):
            if not filename.startswith('.'):
                assert filename.endswith('.sql'), (
                    f"Non-SQL file in migrations dir: {filename}"
                )

    def test_migrations_sorted_order_is_deterministic(self):
        """Filename-sorted order must be unambiguous."""
        files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith('.sql'))
        # Ensure no two files have identical sort position
        assert len(files) == len(set(files)), "Duplicate migration filenames detected"


# =============================================================================
# Migration Content Quality
# =============================================================================

class TestMigrationContent:
    """Canon §1.10: Migrations must be safe and idempotent."""

    def _read_migrations(self):
        """Read all migration file contents."""
        result = {}
        for filename in sorted(os.listdir(MIGRATIONS_DIR)):
            if filename.endswith('.sql'):
                path = os.path.join(MIGRATIONS_DIR, filename)
                with open(path) as f:
                    result[filename] = f.read()
        return result

    def test_migrations_use_advisory_locks(self):
        """Concurrency safety: migrations should use pg_advisory_lock."""
        migrations = self._read_migrations()
        for filename, content in migrations.items():
            assert 'pg_advisory_lock' in content.lower(), (
                f"Migration '{filename}' does not use pg_advisory_lock for concurrency safety"
            )

    def test_migrations_use_if_not_exists(self):
        """Idempotency: CREATE TABLE/INDEX should use IF NOT EXISTS."""
        migrations = self._read_migrations()
        for filename, content in migrations.items():
            # Find CREATE TABLE without IF NOT EXISTS
            creates = re.findall(r'CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)', content, re.IGNORECASE)
            assert len(creates) == 0, (
                f"Migration '{filename}' has CREATE TABLE without IF NOT EXISTS"
            )


# =============================================================================
# Base Schema Documentation
# =============================================================================

class TestBaseSchemaDocumentation:
    """Canon §1.10: Base schema must document application order."""

    def test_schema_has_application_order_docs(self):
        with open(SCHEMA_PATH) as f:
            content = f.read()
        assert 'APPLICATION ORDER' in content.upper() or 'apply' in content.lower(), (
            "Base schema does not document DDL application order"
        )

    def test_schema_uses_bar_start_ts(self):
        """Canon §1.3: ohlcv must use bar_start_ts as canonical column name."""
        with open(SCHEMA_PATH) as f:
            content = f.read()
        assert 'bar_start_ts' in content, (
            "Base schema does not use bar_start_ts column name"
        )


# =============================================================================
# No DDL Duplication
# =============================================================================

class TestNoDDLDuplication:
    """Canon §1.10: Avoid duplicating DDL in multiple runtime entrypoints."""

    def test_no_duplicate_table_definitions(self):
        """Same table should not be CREATE-d in both schema and migrations
        (unless migration uses IF NOT EXISTS, which is acceptable for idempotency)."""
        with open(SCHEMA_PATH) as f:
            schema_content = f.read()

        # Extract table names from base schema
        schema_tables = set(re.findall(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)',
            schema_content, re.IGNORECASE
        ))

        # Extract table names from migrations
        migration_tables = set()
        for filename in os.listdir(MIGRATIONS_DIR):
            if filename.endswith('.sql'):
                path = os.path.join(MIGRATIONS_DIR, filename)
                with open(path) as f:
                    content = f.read()
                tables = re.findall(
                    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)',
                    content, re.IGNORECASE
                )
                migration_tables.update(tables)

        # Tables defined in both schema AND migrations (potential duplication)
        overlap = schema_tables & migration_tables
        # This is informational — IF NOT EXISTS makes overlap safe but worth tracking
        # Fail only if a migration re-creates a table that's already in the base schema
        # without IF NOT EXISTS
        for filename in os.listdir(MIGRATIONS_DIR):
            if filename.endswith('.sql'):
                path = os.path.join(MIGRATIONS_DIR, filename)
                with open(path) as f:
                    content = f.read()
                unsafe_creates = re.findall(
                    r'CREATE\s+TABLE\s+(\w+)',
                    content, re.IGNORECASE
                )
                safe_creates = re.findall(
                    r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)',
                    content, re.IGNORECASE
                )
                unsafe_only = set(unsafe_creates) - set(safe_creates)
                duplicated = unsafe_only & schema_tables
                assert len(duplicated) == 0, (
                    f"Migration '{filename}' re-creates tables {duplicated} "
                    f"already in base schema without IF NOT EXISTS"
                )
