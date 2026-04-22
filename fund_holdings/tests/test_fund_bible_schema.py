"""
Phase 0 — Fund Bible: pytest wrapper for final_gates.py.

Runs the remediation-era gates against the committed fund_bible_v1.xlsx
and asserts that the bible is in the shape §7.1/§7.3/§7.4/§7.5 require.
URL content verification (§7.2) is SKIPPED by default to keep pytest
hermetic; run final_gates.py directly (without --skip-content-verify)
for the full network gate.

The legacy v1 schema_check.py is kept at
`source_authoring/verification/schema_check.py` as historical reference
but is NOT invoked here — its HEAD-check gate was the v1 verification
that missed the edgar_fallback placeholder URLs.

Run with:
    pytest -v fund_holdings/tests/test_fund_bible_schema.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import openpyxl
import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BIBLE_XLSX = REPO_ROOT / "fund_holdings" / "ground_truth" / "fund_bible_v1.xlsx"
FINAL_GATES = REPO_ROOT / "fund_holdings" / "source_authoring" / "phase_0" / "final_gates.py"


def test_bible_file_exists():
    assert BIBLE_XLSX.exists(), f"fund_bible_v1.xlsx missing at {BIBLE_XLSX}"


def test_final_gates_pass_hermetic():
    """Run final_gates.py with --skip-content-verify (no network)."""
    proc = subprocess.run(
        [sys.executable, str(FINAL_GATES),
         "--bible", str(BIBLE_XLSX),
         "--skip-content-verify"],
        capture_output=True, text=True, timeout=300,
        cwd=str(FINAL_GATES.parent),
    )
    if proc.returncode != 0:
        print("--- final_gates stdout ---")
        print(proc.stdout)
        print("--- final_gates stderr ---")
        print(proc.stderr)
    assert proc.returncode == 0, "final_gates.py exited non-zero"


def _load_rows():
    wb = openpyxl.load_workbook(BIBLE_XLSX, read_only=True, data_only=True)
    rows = []
    for ws in wb.worksheets:
        if ws.title.lower() == "overview":
            continue
        all_rows = list(ws.iter_rows(values_only=True))
        if len(all_rows) < 4:
            continue
        headers = [str(h) if h is not None else "" for h in all_rows[3]]
        for r in all_rows[4:]:
            if r is None or all(v is None or v == "" for v in r):
                continue
            rows.append((ws.title, dict(zip(headers, r))))
    return rows


def test_row_count_in_expected_band():
    """Post-remediation row count is in the 600 - 2700 band. The upper bound
    is the v1 universe (2,563); the lower bound reflects the minimum viable
    content-verified subset (roughly: iShares + SSGA + Vanguard core)."""
    rows = _load_rows()
    n = len(rows)
    assert 600 <= n <= 2_700, f"row count {n} outside [600, 2700]"


def test_all_rows_have_ticker_and_fund_name():
    for sheet, r in _load_rows():
        ticker = str(r.get("Ticker", "") or "").strip()
        fund_name = str(r.get("Fund_Name", "") or "").strip()
        assert ticker, f"{sheet}: row missing Ticker: {r!r}"
        assert fund_name, f"{sheet}: row missing Fund_Name: ticker={ticker}"


def test_v1_lineage_columns_populated():
    """Every surviving row must retain its v1 Lineage_Authored_* provenance."""
    for sheet, r in _load_rows():
        assert r.get("Lineage_Authored_By"), f"{sheet}: missing Lineage_Authored_By"
        assert r.get("Lineage_Authored_At"), f"{sheet}: missing Lineage_Authored_At"


def test_remediation_lineage_columns_populated():
    """Post-remediation: every surviving row was touched by remediation, so
    Lineage_Remediated_* must be populated (not 'null')."""
    for sheet, r in _load_rows():
        rem_by = str(r.get("Lineage_Remediated_By", "") or "")
        rem_at = str(r.get("Lineage_Remediated_At", "") or "")
        assert rem_by and rem_by.lower() != "null", \
            f"{sheet}: ticker={r.get('Ticker')!r} has null Lineage_Remediated_By"
        assert rem_at and rem_at.lower() != "null", \
            f"{sheet}: ticker={r.get('Ticker')!r} has null Lineage_Remediated_At"


def test_holdings_feed_url_populated_and_not_edgar_search():
    """Load-bearing: no row may have a missing Holdings_Feed_URL and none may
    point at an SEC EDGAR search page (the v1 failure mode)."""
    for sheet, r in _load_rows():
        u = str(r.get("Holdings_Feed_URL", "") or "").strip()
        assert u and u.lower() not in ("null", "none", ""), \
            f"{sheet}: row ticker={r.get('Ticker')!r} has null Holdings_Feed_URL"
        assert "sec.gov/cgi-bin/browse-edgar" not in u, \
            f"{sheet}: ticker={r.get('Ticker')!r} still has EDGAR-search URL"


def test_holdings_feed_format_is_canonical():
    """§7.2: permitted formats are csv / xlsx / json / html_table."""
    PERMITTED = {"csv", "xlsx", "json", "html_table"}
    for sheet, r in _load_rows():
        fmt = str(r.get("Holdings_Feed_Format", "") or "").strip().lower()
        assert fmt in PERMITTED, \
            f"{sheet}: ticker={r.get('Ticker')!r} has illegal format {fmt!r}"


def test_no_forbidden_strings_anywhere():
    """§7.1 / §9.13: no edgar_fallback / template_guess / pattern_guess / pending Phase
    in any cell of the workbook."""
    FORBIDDEN = ("edgar_fallback", "template_guess", "pattern_guess", "pending Phase")
    wb = openpyxl.load_workbook(BIBLE_XLSX, read_only=True, data_only=True)
    hits = []
    for ws in wb.worksheets:
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            for j, v in enumerate(row):
                if v is None:
                    continue
                s = str(v)
                for forb in FORBIDDEN:
                    if forb in s:
                        hits.append((ws.title, i, j, forb))
    assert not hits, f"forbidden strings in bible: {hits[:10]}"


def test_no_duplicate_tickers():
    seen: dict[str, str] = {}
    for sheet, r in _load_rows():
        t = str(r.get("Ticker", "") or "").strip().upper()
        if not t:
            continue
        if t in seen:
            pytest.fail(f"duplicate ticker {t} in {sheet!r} (first seen in {seen[t]!r})")
        seen[t] = sheet


def test_no_duplicate_holdings_urls():
    """§7.3: every Holdings_Feed_URL must be distinct at the fund level."""
    seen: dict[str, tuple] = {}
    for sheet, r in _load_rows():
        u = str(r.get("Holdings_Feed_URL", "") or "").strip()
        tk = str(r.get("Ticker", "") or "").strip().upper()
        if not u:
            continue
        if u in seen:
            pytest.fail(f"duplicate Holdings_Feed_URL for {tk} in {sheet}: "
                        f"first seen for {seen[u][1]} in {seen[u][0]}: {u}")
        seen[u] = (sheet, tk)


def test_overview_sheet_total_matches():
    wb = openpyxl.load_workbook(BIBLE_XLSX, read_only=True, data_only=True)
    overview = None
    for ws in wb.worksheets:
        if ws.title.lower() == "overview":
            overview = ws
            break
    assert overview is not None, "Overview sheet missing"

    expected = len(_load_rows())
    found = None
    for row in overview.iter_rows(values_only=True):
        if row is None:
            continue
        for v in row:
            if isinstance(v, str) and v.strip().lower() == "total":
                for v2 in row:
                    if isinstance(v2, (int, float)) and int(v2) == expected:
                        found = int(v2)
                        break
                break
    assert found == expected, (
        f"Overview Total does not match workbook row count "
        f"(expected {expected}, found {found!r})"
    )
