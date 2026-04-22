"""
Phase 0 — Fund Bible: pytest wrapper for schema_check.

Runs schema_check.py against the committed fund_bible_v1.xlsx and asserts
exit 0 plus a few extra sanity properties that don't fit in the shell
invocation (e.g. specific sponsor rep counts).

URL HEAD-checking is SKIPPED here by default to keep pytest fast and
hermetic. The full URL verification was already performed by
schema_check.py during the authoring session (the committed bible is
the post-verification artifact). A nightly CI job may re-run with
`--no-skip-url-check` to detect link rot.

Fail fast: any assertion failure marks the test as failed.

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
SCHEMA_CHECK = REPO_ROOT / "fund_holdings" / "source_authoring" / "verification" / "schema_check.py"
NAMESPACE_RULES = REPO_ROOT / "fund_holdings" / "registry" / "namespace_rules.yaml"


def test_bible_file_exists():
    assert BIBLE_XLSX.exists(), f"fund_bible_v1.xlsx missing at {BIBLE_XLSX}"


def test_schema_check_passes():
    """Invoke schema_check.py as a subprocess; assert exit 0."""
    proc = subprocess.run(
        [
            sys.executable,
            str(SCHEMA_CHECK),
            "--workbook", str(BIBLE_XLSX),
            "--namespace-rules", str(NAMESPACE_RULES),
            "--skip-url-check",             # hermetic by default
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        print("--- schema_check stdout ---")
        print(proc.stdout)
        print("--- schema_check stderr ---")
        print(proc.stderr)
    assert proc.returncode == 0, "schema_check exited non-zero"


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
    """Row count is in the 1,800 - 3,800 band (Phase 0 target)."""
    rows = _load_rows()
    n = len(rows)
    assert 1_800 <= n <= 3_800, f"row count {n} outside [1800, 3800]"


def test_all_rows_have_ticker_and_fund_name():
    for sheet, r in _load_rows():
        ticker = str(r.get("Ticker", "") or "").strip()
        fund_name = str(r.get("Fund_Name", "") or "").strip()
        assert ticker, f"{sheet}: row missing Ticker: {r!r}"
        assert fund_name, f"{sheet}: row missing Fund_Name: ticker={ticker}"


def test_lineage_columns_populated():
    for sheet, r in _load_rows():
        assert r.get("Lineage_Authored_By"), f"{sheet}: missing Lineage_Authored_By"
        assert r.get("Lineage_Authored_At"), f"{sheet}: missing Lineage_Authored_At"


def test_holdings_feed_url_populated():
    """Load-bearing: no row may have a missing Holdings_Feed_URL."""
    for sheet, r in _load_rows():
        u = str(r.get("Holdings_Feed_URL", "") or "").strip()
        assert u and u.lower() not in ("null", "none", ""), \
            f"{sheet}: row ticker={r.get('Ticker')!r} has null Holdings_Feed_URL"


def test_no_duplicate_tickers():
    seen: dict[str, str] = {}
    for sheet, r in _load_rows():
        t = str(r.get("Ticker", "") or "").strip().upper()
        if not t:
            continue
        if t in seen:
            pytest.fail(f"duplicate ticker {t} in {sheet!r} (first seen in {seen[t]!r})")
        seen[t] = sheet


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
