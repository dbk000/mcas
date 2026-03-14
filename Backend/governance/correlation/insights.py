"""
CI Insights Generator
=====================

Generates natural language log entry drafts from correlation analysis results.

These drafts preserve:
- Why a tweak was made (loss signature / evidence)
- What exactly changed (module + params + config hash)
- What happened after (did metrics improve)

Format is append-only, evidence-linked, and canon-friendly.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from contextlib import contextmanager

import psycopg

from governance.persistence import get_conn as _pool_get_conn

from .stats import CorrelationReport

logger = logging.getLogger(__name__)


@dataclass
class InsightDraft:
    """
    A draft log entry ready for human review and decision.

    Generated automatically from correlation analysis results.
    Human adds: hypothesis, change details, expected effect, decision.
    """
    # Timing
    timestamp: str

    # Scope
    setup_id: str
    timeframes: list[str]
    run_ids: list[str]

    # Evidence
    analysis_id: str
    total_trades: int
    win_rate: float

    # Observations (auto-generated)
    top_signatures: list[dict]
    observation_summary: str

    # Placeholders for human input
    hypothesis: str = "[FILL: Why does this pattern imply a tweak is needed?]"
    change_type: str = "NONE"
    change_target: str = ""
    change_before: str = ""
    change_after: str = ""
    expected_effect: str = "[FILL: What metric/bucket should change?]"

    def to_markdown(self) -> str:
        """Render as markdown log entry draft."""
        lines = [
            f"## {self.timestamp}",
            "",
            f"**Scope:** {self.setup_id} | {', '.join(self.timeframes)} | {self.total_trades} trades | {self.win_rate:.1%} WR",
            f"**Evidence:** analysis_id={self.analysis_id[:8]}... | runs={len(self.run_ids)}",
            "",
            "### Observation",
            "",
            self.observation_summary,
            "",
        ]

        if self.top_signatures:
            lines.append("Top loser predictors:")
            for sig in self.top_signatures[:3]:
                d = sig.get('cohens_d', 0)
                direction = "higher in losers" if d < 0 else "lower in losers"
                lines.append(
                    f"- **{sig['feature']}**: d={d:.2f} ({direction}), "
                    f"winner_mean={sig.get('winner_mean', 0):.3f}, "
                    f"loser_mean={sig.get('loser_mean', 0):.3f}"
                )
            lines.append("")

        lines.extend([
            "### Hypothesis",
            "",
            self.hypothesis,
            "",
            "### Change Made",
            "",
            f"- **Type:** {self.change_type}",
            f"- **Target:** {self.change_target or '[FILL: e.g., S1.entry.clv_threshold]'}",
            f"- **Before:** {self.change_before or '[FILL: previous value]'}",
            f"- **After:** {self.change_after or '[FILL: new value]'}",
            f"- **Config hash:** [FILL: from deploy]",
            "",
            "### Expected Effect",
            "",
            self.expected_effect,
            "",
            "### Result",
            "",
            "_To be filled after next analysis run._",
            "",
            "- **Effect observed:** [ ] Yes / [ ] No",
            "- **Side effects:** [FILL]",
            "",
            "### Decision",
            "",
            "- [ ] KEEP",
            "- [ ] REVERT",
            "- [ ] ITERATE",
            "",
            "**Rationale:** [FILL]",
            "",
            "---",
            "",
        ])

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "setup_id": self.setup_id,
            "timeframes": self.timeframes,
            "run_ids": self.run_ids,
            "analysis_id": self.analysis_id,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "top_signatures": self.top_signatures,
            "observation_summary": self.observation_summary,
            "hypothesis": self.hypothesis,
            "change_type": self.change_type,
            "change_target": self.change_target,
            "change_before": self.change_before,
            "change_after": self.change_after,
            "expected_effect": self.expected_effect,
        }


class InsightsGenerator:
    """
    Generates CI insight drafts from correlation analysis results.

    Usage:
        generator = InsightsGenerator()
        draft = generator.from_analysis(analysis_id, report, run_ids, setup_id)
        print(draft.to_markdown())
    """

    def from_analysis(
        self,
        analysis_id: str,
        report: CorrelationReport,
        run_ids: list[str],
        setup_id: Optional[str],
        timeframes: Optional[list[str]] = None,
    ) -> InsightDraft:
        """
        Generate an insight draft from correlation analysis results.

        Args:
            analysis_id: UUID of the correlation_analysis record
            report: CorrelationReport from the analysis
            run_ids: List of run UUIDs that were analyzed
            setup_id: Setup filter used (or None for all)
            timeframes: Timeframes covered (optional)

        Returns:
            InsightDraft ready for human review
        """
        # Extract top loser predictors (features where losers differ from winners)
        top_signatures = []
        for feature, importance, direction in report.top_loser_predictors[:5]:
            analysis = report.feature_analyses.get(feature)
            if analysis:
                top_signatures.append({
                    "feature": feature,
                    "cohens_d": analysis.cohens_d,
                    "winner_mean": analysis.winner_mean,
                    "loser_mean": analysis.loser_mean,
                    "n_samples": analysis.n_samples,
                    "insight": analysis.insight,
                })

        # Generate observation summary
        observation = self._generate_observation(report, top_signatures)

        return InsightDraft(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            setup_id=setup_id or "ALL",
            timeframes=timeframes or ["1h"],
            run_ids=run_ids,
            analysis_id=analysis_id,
            total_trades=report.total_trades,
            win_rate=report.win_rate,
            top_signatures=top_signatures,
            observation_summary=observation,
        )

    def _generate_observation(
        self,
        report: CorrelationReport,
        top_signatures: list[dict],
    ) -> str:
        """Generate natural language observation summary."""
        lines = []

        # Overall stats
        lines.append(
            f"Analyzed {report.total_trades} closed trades: "
            f"{report.winning_trades} winners, {report.losing_trades} losers "
            f"({report.win_rate:.1%} win rate)."
        )

        if report.truncated:
            lines.append("Note: Analysis was truncated to 2000 trades.")

        # Top differentiators
        if top_signatures:
            strong = [s for s in top_signatures if abs(s['cohens_d']) >= 0.5]
            if strong:
                names = [s['feature'] for s in strong[:3]]
                lines.append(
                    f"Strong predictors of outcome (|d| >= 0.5): {', '.join(names)}."
                )

            # Most actionable insight
            top = top_signatures[0]
            d = top['cohens_d']
            if d < -0.3:
                lines.append(
                    f"Losers have significantly higher {top['feature']} "
                    f"(mean {top['loser_mean']:.3f} vs {top['winner_mean']:.3f} for winners). "
                    f"Consider tightening this gate."
                )
            elif d > 0.3:
                lines.append(
                    f"Winners have significantly higher {top['feature']} "
                    f"(mean {top['winner_mean']:.3f} vs {top['loser_mean']:.3f} for losers). "
                    f"Consider requiring higher values for entry."
                )

        # Exit reason patterns
        if report.exit_reason_breakdown:
            sl_data = report.exit_reason_breakdown.get("STOP_LOSS", {})
            tp1_data = report.exit_reason_breakdown.get("TP1", {})

            if sl_data and tp1_data:
                sl_count = sl_data.get("count", 0)
                tp1_count = tp1_data.get("count", 0)
                total_exits = sl_count + tp1_count

                if total_exits > 0 and sl_count > tp1_count * 1.5:
                    lines.append(
                        f"Stop-loss exits ({sl_count}) significantly exceed TP1 exits ({tp1_count}). "
                        f"Entry timing or stop placement may need review."
                    )

        return " ".join(lines)


class InsightsStore:
    """
    Stores and retrieves CI insights from the database.

    Also maintains the append-only markdown log file.
    """

    LOG_FILE = "/home/darren/Desktop/MCAS/Backend/docs/ci_insights_log.md"

    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def _conn(self):
        with _pool_get_conn() as conn:
            yield conn

    def save_draft(
        self,
        draft: InsightDraft,
        hypothesis: Optional[str] = None,
        change_type: Optional[str] = None,
        change_target: Optional[str] = None,
        change_before: Optional[str] = None,
        change_after: Optional[str] = None,
        config_hash: Optional[str] = None,
        expected_effect: Optional[str] = None,
        author: str = "system",
    ) -> str:
        """
        Save an insight draft to the database.

        Returns the insight_id.
        """
        import uuid
        insight_id = str(uuid.uuid4())

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ci_insights (
                        id, setup_id, timeframes, run_ids,
                        analysis_id, top_loser_signatures, observation_summary,
                        hypothesis, change_type, change_target,
                        change_before, change_after, config_hash,
                        expected_effect, decision, author
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    );
                """, (
                    insight_id,
                    draft.setup_id,
                    draft.timeframes,
                    draft.run_ids,
                    draft.analysis_id,
                    json.dumps(draft.top_signatures),
                    draft.observation_summary,
                    hypothesis or draft.hypothesis,
                    change_type or draft.change_type,
                    change_target or draft.change_target,
                    change_before or draft.change_before,
                    change_after or draft.change_after,
                    config_hash,
                    expected_effect or draft.expected_effect,
                    "PENDING",
                    author,
                ))
            conn.commit()

        logger.info(f"Saved CI insight {insight_id}")
        return insight_id

    def update_result(
        self,
        insight_id: str,
        result_analysis_id: str,
        result_summary: str,
        effect_observed: bool,
        side_effects: Optional[str] = None,
        decision: str = "PENDING",
        decision_rationale: Optional[str] = None,
    ):
        """Update an insight with results from subsequent analysis."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ci_insights
                    SET result_analysis_id = %s,
                        result_summary = %s,
                        effect_observed = %s,
                        side_effects = %s,
                        decision = %s,
                        decision_rationale = %s
                    WHERE id = %s;
                """, (
                    result_analysis_id,
                    result_summary,
                    effect_observed,
                    side_effects,
                    decision,
                    decision_rationale,
                    insight_id,
                ))
            conn.commit()

        logger.info(f"Updated CI insight {insight_id} with result")

    def append_to_log(self, markdown: str):
        """Append markdown entry to the log file (append-only)."""
        try:
            with open(self.LOG_FILE, "a") as f:
                f.write(markdown)
            logger.info(f"Appended to CI insights log: {self.LOG_FILE}")
        except Exception as e:
            logger.error(f"Failed to append to CI log: {e}")

    def get_pending_insights(self) -> list[dict]:
        """Get all insights with PENDING decision."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, setup_id, created_at, observation_summary,
                           hypothesis, change_type, change_target
                    FROM ci_insights
                    WHERE decision = 'PENDING'
                    ORDER BY created_at DESC;
                """)
                rows = cur.fetchall()

        return [
            {
                "id": str(row[0]),
                "setup_id": row[1],
                "created_at": row[2].isoformat() if row[2] else None,
                "observation_summary": row[3],
                "hypothesis": row[4],
                "change_type": row[5],
                "change_target": row[6],
            }
            for row in rows
        ]
