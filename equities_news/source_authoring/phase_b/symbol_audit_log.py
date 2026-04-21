"""symbol_audit_log — append-only per-symbol authoring audit trail.

Phase B sector agents call AuditLog.record(...) once per processed ticker.
Log file lives per-sector at:
    source_authoring/phase_b/logs/{sector_slug}.jsonl

Format: JSON Lines. Each line is one event:
    {
      "timestamp":    "2026-04-21T12:00:00Z",
      "sector_slug":  "health_care",
      "agent_id":     "phase_b_health_care_opus",
      "ticker":       "PFE",
      "columns_found": ["IR_URL","Press_Release_Feed_URL","Official_X_Handle",
                        "Official_LinkedIn_URL","Earnings_Transcript_Provider",
                        "Local_Regulatory_Filings_URL"],
      "columns_failed": [],
      "columns_skipped": ["Sub_Industry_Override"],
      "confidence": 0.9,
      "authored_in_seconds": 4.3,
      "notes": { ... }
    }

Append-only guarantees are via OS-level open(..., "a"); writers should never
truncate or rewrite this file. Downstream tooling loads it with `json.loads`
per line and treats duplicate (ticker, timestamp) as legitimate re-runs.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).resolve().parent / "logs"


def _utc_now_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class AuditLog:
    """Append-only JSON-lines writer. One instance per sector agent run."""

    def __init__(self, sector_slug: str, agent_id: str) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.sector_slug = sector_slug
        self.agent_id = agent_id
        self.path = LOG_DIR / f"{sector_slug}.jsonl"

    def record(
        self,
        ticker: str,
        *,
        columns_found: list[str],
        columns_failed: list[str] | None = None,
        columns_skipped: list[str] | None = None,
        confidence: float,
        authored_in_seconds: float | None = None,
        notes: dict[str, Any] | None = None,
    ) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence={confidence} out of [0,1]")
        event = {
            "timestamp": _utc_now_iso(),
            "sector_slug": self.sector_slug,
            "agent_id": self.agent_id,
            "ticker": ticker,
            "columns_found": list(columns_found),
            "columns_failed": list(columns_failed or []),
            "columns_skipped": list(columns_skipped or []),
            "confidence": float(confidence),
            "authored_in_seconds": authored_in_seconds,
            "notes": notes or {},
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        # Open-append-close per record — durable against crashes mid-run.
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def summary(self) -> dict[str, Any]:
        """Aggregate stats over the log file so far."""
        if not self.path.exists():
            return {"n": 0}
        n = 0
        col_found = {}
        col_failed = {}
        confs = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                n += 1
                for c in e.get("columns_found", []):
                    col_found[c] = col_found.get(c, 0) + 1
                for c in e.get("columns_failed", []):
                    col_failed[c] = col_failed.get(c, 0) + 1
                confs.append(float(e.get("confidence", 0)))
        return {
            "n": n,
            "columns_found": col_found,
            "columns_failed": col_failed,
            "mean_confidence": sum(confs) / len(confs) if confs else 0.0,
        }
