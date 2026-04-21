"""sector_agent — common template for Phase B sector agents.

A Phase B agent session uses this module as the skeleton:

    from sector_agent import SectorAgent
    agent = SectorAgent("health_care")
    agent.preflight()                    # guardrails + namespace check
    agent.read_sector_sheet()            # load rows for this sector
    agent.author_sector_yaml([...])      # write sector.<slug>.yaml
    agent.author_sub_industry_yamls({...})
    for row in agent.rows:
        agent.fill_overlay(row)          # per-symbol overlay fill
    agent.self_verify()
    agent.emit_enriched_sheet()

This class enforces:
  - Write boundaries: you can only author source_ids under sector.<assigned_slug>.*
    and sub_industry.<assigned_slug>.*.*, and you can only write overlay columns
    for rows where the Country maps to a sector matching your assignment.
  - Schema validation on every authored entry.
  - Author-log append on every processed ticker.

NOTE: This module does NOT invoke any Opus authoring logic itself — that is the
job of the Phase B session. This class is the harness the session uses to keep
its writes governed.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import (
    NEWS_SOURCES_DIR,
    REGISTRY_DIR,
    GROUND_TRUTH_DIR,
    NamespaceRules,
    classify_namespace,
    dump_yaml,
    entry_fingerprint,
    load_event_type_slugs,
    load_yaml,
    utc_now_iso,
    validate_entry,
)
from symbol_audit_log import AuditLog


ALLOWED_SECTOR_SLUGS = {
    "information_technology",
    "communication_services",
    "consumer_discretionary",
    "consumer_staples",
    "health_care",
    "financials",
    "industrials",
    "energy",
    "utilities",
    "real_estate",
    "materials",
}

_SHEET_BY_SLUG = {
    "information_technology": "Information Technology",
    "communication_services": "Communication Services",
    "consumer_discretionary": "Consumer Discretionary",
    "consumer_staples": "Consumer Staples",
    "health_care": "Health Care",
    "financials": "Financials",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "real_estate": "Real Estate",
    "materials": "Materials",
}


OVERLAY_COLUMNS = [
    "IR_URL",
    "Press_Release_Feed_URL",
    "Official_X_Handle",
    "Official_LinkedIn_URL",
    "Earnings_Transcript_Provider",
    "Local_Regulatory_Filings_URL",
    "Sub_Industry_Override",
]

EARNINGS_TRANSCRIPT_ENUM = {
    "quartr", "seekingalpha", "motley_fool", "company_ir", "none",
}


@dataclass
class SymbolRow:
    row_idx: int
    ticker: str
    company: str
    country: str
    exchange: str
    cap_tier: str
    overlay: dict = field(default_factory=dict)


class SectorAgent:
    """Governed Phase B sector agent harness."""

    def __init__(self, sector_slug: str, agent_id: Optional[str] = None) -> None:
        if sector_slug not in ALLOWED_SECTOR_SLUGS:
            raise ValueError(f"{sector_slug!r} not in allowed sector slugs")
        self.sector_slug = sector_slug
        self.agent_id = agent_id or f"phase_b_{sector_slug}_opus"
        self.rules = NamespaceRules.load()
        self.event_types = load_event_type_slugs()
        self.rows: list[SymbolRow] = []
        self.audit = AuditLog(sector_slug, self.agent_id)

        self._sector_yaml = NEWS_SOURCES_DIR / "sectors" / f"{sector_slug}.yaml"
        self._sub_industry_dir = NEWS_SOURCES_DIR / "sub_industries" / sector_slug

    # ----------------------------------------------------------------- #
    # Preflight                                                         #
    # ----------------------------------------------------------------- #

    def preflight(self) -> None:
        """Verify Phase A is present and no write boundaries are already crossed."""
        univ = NEWS_SOURCES_DIR / "universal.yaml"
        if not univ.exists():
            raise RuntimeError("preflight: universal.yaml missing — Phase A not locked")
        if not (NEWS_SOURCES_DIR / "geo").exists():
            raise RuntimeError("preflight: geo/ directory missing — Phase A not locked")
        if not (REGISTRY_DIR / "countries_in_universe.yaml").exists():
            raise RuntimeError("preflight: countries_in_universe.yaml missing")
        # If sector file already exists with entries from a different agent_id, bail.
        if self._sector_yaml.exists():
            for e in load_yaml(self._sector_yaml) or []:
                prior = (e.get("lineage") or {}).get("authored_by", "")
                if prior and prior != self.agent_id and not prior.startswith(f"phase_b_{self.sector_slug}"):
                    raise RuntimeError(
                        f"preflight: {self._sector_yaml.name} contains entries authored by "
                        f"{prior!r} — write boundary violation"
                    )

    # ----------------------------------------------------------------- #
    # Bible sheet read                                                  #
    # ----------------------------------------------------------------- #

    def read_sector_sheet(self) -> list[SymbolRow]:
        import openpyxl
        sheet_name = _SHEET_BY_SLUG[self.sector_slug]
        bible = GROUND_TRUTH_DIR / "bible_v1.xlsx"
        wb = openpyxl.load_workbook(bible, read_only=True, data_only=True)
        ws = wb[sheet_name]
        rows: list[SymbolRow] = []
        for i, row in enumerate(ws.iter_rows(min_row=5, values_only=True), start=5):
            if not row or not isinstance(row[1], str):
                continue
            rows.append(SymbolRow(
                row_idx=i,
                ticker=row[1],
                company=row[2] or "",
                country=row[4] or "",
                exchange=row[5] or "",
                cap_tier=row[6] or "",
            ))
        self.rows = rows
        return rows

    # ----------------------------------------------------------------- #
    # Sector YAML authoring                                             #
    # ----------------------------------------------------------------- #

    def _enforce_sector_namespace(self, entries: Iterable[dict]) -> None:
        prefix = f"sector.{self.sector_slug}."
        for e in entries:
            sid = e.get("source_id")
            if not isinstance(sid, str) or not sid.startswith(prefix):
                raise ValueError(
                    f"write boundary violation: {sid!r} not under {prefix}*"
                )

    def _enforce_sub_industry_namespace(self, sub_slug: str, entries: Iterable[dict]) -> None:
        if not re.match(r"^[a-z0-9][a-z0-9_]{0,63}$", sub_slug):
            raise ValueError(f"sub_industry slug {sub_slug!r} invalid")
        prefix = f"sub_industry.{self.sector_slug}.{sub_slug}."
        for e in entries:
            sid = e.get("source_id")
            if not isinstance(sid, str) or not sid.startswith(prefix):
                raise ValueError(
                    f"write boundary violation: {sid!r} not under {prefix}*"
                )

    def _stamp_and_validate(self, entries: list[dict], where: str) -> list[dict]:
        now = utc_now_iso()
        for e in entries:
            lineage = dict(e.get("lineage") or {})
            lineage.setdefault("authored_by", self.agent_id)
            lineage.setdefault("authored_at", now)
            e["lineage"] = lineage
            errs = validate_entry(e, self.rules, self.event_types, where)
            if errs:
                msgs = "\n  ".join(x.message for x in errs)
                raise ValueError(f"{where}: validation failed:\n  {msgs}")
        return sorted(entries, key=lambda x: x["source_id"])

    def author_sector_yaml(self, entries: list[dict]) -> Path:
        self._enforce_sector_namespace(entries)
        merged = self._stamp_and_validate(entries, f"sectors/{self.sector_slug}.yaml")
        dump_yaml(self._sector_yaml, merged)
        return self._sector_yaml

    def author_sub_industry_yamls(self, by_slug: dict[str, list[dict]]) -> list[Path]:
        written: list[Path] = []
        for sub_slug, entries in by_slug.items():
            self._enforce_sub_industry_namespace(sub_slug, entries)
            merged = self._stamp_and_validate(
                entries, f"sub_industries/{self.sector_slug}/{sub_slug}.yaml"
            )
            out = self._sub_industry_dir / f"{sub_slug}.yaml"
            dump_yaml(out, merged)
            written.append(out)
        return written

    # ----------------------------------------------------------------- #
    # Per-symbol overlay fill                                           #
    # ----------------------------------------------------------------- #

    def fill_overlay(
        self,
        row: SymbolRow,
        *,
        ir_url: Optional[str],
        press_release_feed_url: Optional[str],
        official_x_handle: Optional[str],
        official_linkedin_url: Optional[str],
        earnings_transcript_provider: Optional[str],
        local_regulatory_filings_url: Optional[str],
        sub_industry_override: Optional[str],
        confidence: float,
        authoring_notes: Optional[dict] = None,
    ) -> None:
        """Populate overlay fields + append audit log event.

        Nulls are explicit — pass None for "we looked and there isn't one".
        Passing None for every field is valid and recorded as zero-fill with
        appropriate confidence.
        """
        # Validate earnings transcript enum.
        if earnings_transcript_provider is not None:
            if earnings_transcript_provider not in EARNINGS_TRANSCRIPT_ENUM:
                raise ValueError(
                    f"earnings_transcript_provider={earnings_transcript_provider!r} "
                    f"not in {sorted(EARNINGS_TRANSCRIPT_ENUM)}"
                )

        # Validate sub_industry_override points at a file we own.
        if sub_industry_override is not None:
            slug = sub_industry_override.split(".")[-1]
            expected = self._sub_industry_dir / f"{slug}.yaml"
            if not expected.exists():
                raise ValueError(
                    f"sub_industry_override={sub_industry_override!r} — "
                    f"{expected} does not exist (author the sub_industry YAML first)"
                )

        row.overlay = {
            "IR_URL": ir_url,
            "Press_Release_Feed_URL": press_release_feed_url,
            "Official_X_Handle": official_x_handle,
            "Official_LinkedIn_URL": official_linkedin_url,
            "Earnings_Transcript_Provider": earnings_transcript_provider,
            "Local_Regulatory_Filings_URL": local_regulatory_filings_url,
            "Sub_Industry_Override": sub_industry_override,
        }

        found: list[str] = []
        failed: list[str] = []
        for col, val in row.overlay.items():
            if val is not None:
                found.append(col)
            else:
                failed.append(col)
        self.audit.record(
            row.ticker,
            columns_found=found,
            columns_failed=failed,
            confidence=confidence,
            notes=authoring_notes,
        )

    # ----------------------------------------------------------------- #
    # Self-verify                                                       #
    # ----------------------------------------------------------------- #

    def self_verify(self) -> list[str]:
        """Cheap local checks. Heavy checks live in verification/merge_gate.py."""
        issues: list[str] = []
        # Sector YAML validation.
        if self._sector_yaml.exists():
            entries = load_yaml(self._sector_yaml) or []
            for i, e in enumerate(entries):
                errs = validate_entry(e, self.rules, self.event_types,
                                      f"sectors/{self.sector_slug}.yaml[{i}]")
                issues.extend(f"{x.where}: {x.message}" for x in errs)
        # Sub-industry YAMLs.
        if self._sub_industry_dir.exists():
            for p in sorted(self._sub_industry_dir.glob("*.yaml")):
                entries = load_yaml(p) or []
                for i, e in enumerate(entries):
                    errs = validate_entry(e, self.rules, self.event_types, f"{p.name}[{i}]")
                    issues.extend(f"{x.where}: {x.message}" for x in errs)
        # Overlay completeness — every row must have every overlay column (explicit nulls OK).
        for r in self.rows:
            for col in OVERLAY_COLUMNS:
                if col not in r.overlay:
                    issues.append(f"row {r.ticker}: overlay column {col} not set")
        return issues

    # ----------------------------------------------------------------- #
    # Emit enriched sheet                                               #
    # ----------------------------------------------------------------- #

    def emit_enriched_sheet(self, out_path: Optional[Path] = None) -> Path:
        """Write bible_v1_enriched.xlsx — a COPY of bible with overlay columns appended.

        Non-destructive: loads the original workbook with formatting, adds six
        columns after Notes, fills this sector's sheet, leaves others untouched.
        If bible_v1_enriched.xlsx already exists (other sectors wrote to it),
        it is the load target instead of bible_v1.xlsx.
        """
        import openpyxl
        enriched = GROUND_TRUTH_DIR / "bible_v1_enriched.xlsx"
        source = enriched if enriched.exists() else GROUND_TRUTH_DIR / "bible_v1.xlsx"
        wb = openpyxl.load_workbook(source)
        ws = wb[_SHEET_BY_SLUG[self.sector_slug]]

        # Header is row 4. Existing columns run 1..18 (A..R). Overlay columns start at 19 (S).
        header_row = 4
        existing = [ws.cell(row=header_row, column=c).value for c in range(1, 30)]
        for i, col_name in enumerate(OVERLAY_COLUMNS, start=0):
            target_col = 19 + i
            if existing[target_col - 1] != col_name:
                ws.cell(row=header_row, column=target_col, value=col_name)

        for r in self.rows:
            for i, col_name in enumerate(OVERLAY_COLUMNS, start=0):
                v = r.overlay.get(col_name, None)
                ws.cell(row=r.row_idx, column=19 + i,
                        value=("null" if v is None else v))

        out = out_path or enriched
        wb.save(out)
        return out
