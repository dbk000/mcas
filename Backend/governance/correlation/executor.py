"""
Correlation Job Executor
========================

Executes correlation analysis jobs from the queue using FOR UPDATE SKIP LOCKED.

Job lifecycle:
1. API enqueues job with status=PENDING
2. Worker claims job (status=RUNNING)
3. Worker extracts features and runs analysis
4. Worker stores results (status=SUCCESS or FAILED)
"""

import json
import logging
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

import psycopg

from governance.persistence import get_conn as _pool_get_conn

from .feature_extractor import FeatureExtractor
from .stats import CorrelationAnalyzer
from .insights import InsightsGenerator, InsightsStore

logger = logging.getLogger(__name__)


class CorrelationJobExecutor:
    """
    Executes correlation analysis jobs from the queue.

    Usage:
        executor = CorrelationJobExecutor(database_url)

        # In worker loop:
        job_id = executor.poll_for_jobs()
        if job_id:
            executor.execute(job_id)
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.extractor = FeatureExtractor(database_url)
        self.analyzer = CorrelationAnalyzer()
        self.insights_generator = InsightsGenerator()
        self.insights_store = InsightsStore(database_url)

    @contextmanager
    def _conn(self):
        """Context manager for database connections from the pool."""
        with _pool_get_conn() as conn:
            yield conn

    def poll_for_jobs(self) -> Optional[str]:
        """
        Atomically claim the next pending correlation analysis job.

        Uses FOR UPDATE SKIP LOCKED to prevent double-claims in
        multi-worker scenarios.

        Returns:
            analysis_id if a job was claimed, None otherwise
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE correlation_analysis
                    SET status = 'RUNNING',
                        started_at = %s
                    WHERE id = (
                        SELECT id FROM correlation_analysis
                        WHERE status = 'PENDING'
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id;
                """, (datetime.now(timezone.utc),))

                row = cur.fetchone()
            conn.commit()

        if row:
            analysis_id = str(row[0])
            logger.info(f"Claimed correlation analysis job {analysis_id}")
            return analysis_id

        return None

    def execute(self, analysis_id: str):
        """
        Execute a correlation analysis job.

        Steps:
        1. Load analysis record
        2. Extract features from run_trades
        3. Run statistical analysis
        4. Store results
        5. Update status to SUCCESS or FAILED
        """
        logger.info(f"Executing correlation analysis {analysis_id}")

        try:
            # Load job config
            config = self._load_job_config(analysis_id)
            if not config:
                self._fail_job(analysis_id, "Job not found")
                return

            run_ids = config["run_ids"]
            setup_id = config.get("setup_id")
            analysis_config = config.get("config_json", {}) or {}
            min_trades = analysis_config.get("min_trades", 30)

            logger.info(
                f"Analyzing {len(run_ids)} runs, setup={setup_id}, "
                f"min_trades={min_trades}"
            )

            # Extract features from trades
            trades = self.extractor.extract_from_runs(run_ids, setup_id)

            if len(trades) < min_trades:
                self._fail_job(
                    analysis_id,
                    f"Insufficient trades: {len(trades)} < {min_trades} minimum"
                )
                return

            # Run analysis
            report = self.analyzer.analyze(trades, min_samples=min_trades)

            # Store results
            self._store_results(analysis_id, report)

            # Generate insight draft for CI logging
            insight_draft = self.insights_generator.from_analysis(
                analysis_id=analysis_id,
                report=report,
                run_ids=run_ids,
                setup_id=setup_id,
            )

            # Store the draft and include markdown in response
            self._store_insight_draft(analysis_id, insight_draft)

            logger.info(
                f"Completed correlation analysis {analysis_id}: "
                f"{report.total_trades} trades, "
                f"{len(report.feature_analyses)} features analyzed"
            )

        except Exception as e:
            logger.error(f"Correlation analysis {analysis_id} failed: {e}", exc_info=True)
            self._fail_job(analysis_id, str(e))

    def _load_job_config(self, analysis_id: str) -> Optional[dict]:
        """Load job configuration from database."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT run_ids, setup_id, config_json
                    FROM correlation_analysis
                    WHERE id = %s;
                """, (analysis_id,))

                row = cur.fetchone()

        if not row:
            return None

        run_ids, setup_id, config_json = row

        # Parse config_json
        if config_json is None:
            config = {}
        elif isinstance(config_json, str):
            config = json.loads(config_json)
        else:
            config = config_json

        return {
            "run_ids": [str(r) for r in run_ids] if run_ids else [],
            "setup_id": setup_id,
            "config_json": config,
        }

    def _store_results(self, analysis_id: str, report):
        """Store analysis results in database."""
        report_dict = report.to_dict()

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE correlation_analysis
                    SET status = 'SUCCESS',
                        finished_at = %s,
                        total_trades = %s,
                        winning_trades = %s,
                        losing_trades = %s,
                        feature_analyses = %s,
                        top_predictors = %s,
                        exit_reason_breakdown = %s,
                        insights = %s
                    WHERE id = %s;
                """, (
                    datetime.now(timezone.utc),
                    report.total_trades,
                    report.winning_trades,
                    report.losing_trades,
                    json.dumps(report_dict.get("feature_analyses", {})),
                    json.dumps(report_dict.get("top_predictors", [])),
                    json.dumps(report_dict.get("exit_reason_breakdown", {})),
                    json.dumps({
                        "items": report_dict.get("insights", []),
                        "truncated": report.truncated,
                    }),
                    analysis_id,
                ))
            conn.commit()

    def _store_insight_draft(self, analysis_id: str, draft):
        """Store the insight draft markdown in the analysis record."""
        markdown = draft.to_markdown()
        draft_json = draft.to_dict()

        with self._conn() as conn:
            with conn.cursor() as cur:
                # Store draft in the insights JSONB field
                cur.execute("""
                    UPDATE correlation_analysis
                    SET insights = insights || %s
                    WHERE id = %s;
                """, (
                    json.dumps({"draft": draft_json, "draft_markdown": markdown}),
                    analysis_id,
                ))
            conn.commit()

        logger.debug(f"Stored insight draft for analysis {analysis_id}")

    def _fail_job(self, analysis_id: str, error: str):
        """Mark job as failed."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE correlation_analysis
                    SET status = 'FAILED',
                        finished_at = %s,
                        error_text = %s
                    WHERE id = %s;
                """, (
                    datetime.now(timezone.utc),
                    error,
                    analysis_id,
                ))
            conn.commit()

        logger.error(f"Correlation analysis {analysis_id} failed: {error}")
