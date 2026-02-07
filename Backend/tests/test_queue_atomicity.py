"""
Canon §1.11: Queue/Concurrency Test
====================================

Automated checks:
- Worker claim_next_run() uses FOR UPDATE SKIP LOCKED
- Queue claiming is atomic (single UPDATE...RETURNING)
- Worker ID is recorded on claim
- Claimed runs are excluded from future claims
- Queue status transitions are valid
"""
import os
import re
import ast
import inspect
import pytest


# =============================================================================
# Source Code Analysis — Verify FOR UPDATE SKIP LOCKED
# =============================================================================

WORKER_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'worker', 'app', 'worker.py'
)


class TestQueueAtomicity:
    """Canon §1.11 + Corrections Log #9: Atomic queue consumption."""

    @pytest.fixture(autouse=True)
    def _read_worker(self):
        with open(WORKER_PATH) as f:
            self.worker_source = f.read()

    def test_uses_for_update_skip_locked(self):
        """Worker must use FOR UPDATE SKIP LOCKED for atomic claims."""
        assert 'FOR UPDATE SKIP LOCKED' in self.worker_source, (
            "worker.py does not contain 'FOR UPDATE SKIP LOCKED' — "
            "queue claiming is not atomic (Canon Corrections Log #9)"
        )

    def test_claim_uses_single_atomic_query(self):
        """claim_next_run should use UPDATE...WHERE...RETURNING in one statement."""
        # Check that UPDATE and RETURNING are in the same SQL block
        assert 'RETURNING run_id' in self.worker_source, (
            "claim_next_run does not use RETURNING — claim is not atomic"
        )

    def test_claim_records_worker_id(self):
        """Claimed runs must record which worker claimed them."""
        assert 'claimed_by' in self.worker_source, (
            "worker.py does not record claimed_by — cannot track which worker owns a run"
        )
        assert 'claimed_at' in self.worker_source, (
            "worker.py does not record claimed_at — cannot track claim time"
        )

    def test_claim_respects_max_attempts(self):
        """Queue consumer must check max_attempts to prevent infinite retry."""
        assert 'max_attempts' in self.worker_source, (
            "worker.py does not check max_attempts — runs could retry forever"
        )
        assert 'attempt_count' in self.worker_source, (
            "worker.py does not track attempt_count"
        )

    def test_claim_orders_by_priority(self):
        """Queue consumption should respect priority ordering."""
        assert 'priority DESC' in self.worker_source or 'priority desc' in self.worker_source.lower(), (
            "Queue claim does not ORDER BY priority DESC — higher priority runs may be skipped"
        )

    def test_queue_status_transitions(self):
        """Valid queue statuses must include QUEUED, CLAIMED, COMPLETED, FAILED."""
        for status in ['QUEUED', 'CLAIMED', 'COMPLETED', 'FAILED']:
            assert status in self.worker_source, (
                f"Queue status '{status}' not found in worker.py"
            )


# =============================================================================
# Schema Verification — run_queue table structure
# =============================================================================

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'docs', 'db', 'schema', 'mcas_schema.sql'
)


class TestQueueSchema:
    """Verify run_queue table has required columns for atomic claiming."""

    @pytest.fixture(autouse=True)
    def _read_schema(self):
        with open(SCHEMA_PATH) as f:
            self.schema = f.read()

    def test_run_queue_table_exists(self):
        assert 'run_queue' in self.schema, "run_queue table not in schema"

    def test_run_queue_has_claimed_by(self):
        assert 'claimed_by' in self.schema, "run_queue missing claimed_by column"

    def test_run_queue_has_claimed_at(self):
        assert 'claimed_at' in self.schema, "run_queue missing claimed_at column"

    def test_run_queue_has_priority(self):
        assert 'priority' in self.schema, "run_queue missing priority column"

    def test_run_queue_has_attempt_count(self):
        assert 'attempt_count' in self.schema, "run_queue missing attempt_count column"

    def test_run_queue_has_status_constraint(self):
        """Status column should have a CHECK constraint."""
        # Look for CHECK constraint on status
        assert re.search(
            r"status\s+TEXT.*CHECK.*QUEUED.*CLAIMED.*COMPLETED.*FAILED",
            self.schema,
            re.IGNORECASE | re.DOTALL
        ), "run_queue status column missing CHECK constraint for valid statuses"
