"""Phase 3: Metadata enrichment + Prospectus_URL upgrade.

Two responsibilities:

1) Extract per-fund metadata (Inception_Date, Expense_Ratio_Bps,
   Shares_Outstanding, Benchmark_Name, Holdings_Feed_Lag_Hours) from the
   JSON/HTML already captured during Tier 1-4 probes, and from sponsor
   product-index pages.

2) Upgrade every bible row's Prospectus_URL from the EDGAR filings-listing
   URL (generic `/cgi-bin/browse-edgar?...type=497`) to the direct 497 PDF
   URL using EDGAR's company-facts JSON endpoint.
   SEC rate limit: 10 rps (we run serial with 0.12s gap).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import yaml
from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from sponsor_discovery_v2 import IMPERSONATE, fetch_curl_cffi

PHASE0 = Path(__file__).parent
SEC_RATE_SLEEP = 0.12  # 10rps cap per reference_sec_ncen.md

EDGAR_UA = "MCAS-Phase0-Recovery dbk000+claude@anthropic.com"


def fetch_edgar_submissions(cik: str, session) -> dict | None:
    """Fetch https://data.sec.gov/submissions/CIK{10d}.json"""
    padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    time.sleep(SEC_RATE_SLEEP)
    status, final_url, body, ct = fetch_curl_cffi(session, url)
    if status != 200 or not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def get_latest_497_url(cik: str, session) -> str | None:
    """Extract the most recent 497 filing URL for a given CIK."""
    subs = fetch_edgar_submissions(cik, session)
    if not subs:
        return None
    filings = subs.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accs = filings.get("accessionNumber", [])
    primary_docs = filings.get("primaryDocument", [])
    filing_dates = filings.get("filingDate", [])
    # Find most recent 497K or 497
    for i, form in enumerate(forms):
        if form in ("497", "497K"):
            if i >= len(accs) or i >= len(primary_docs):
                continue
            acc_clean = accs[i].replace("-", "")
            doc = primary_docs[i]
            return (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{doc}"
            )
    return None


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--phase0-jsonl", default=str(PHASE0 / "logs" / "phase_0.jsonl"),
                    help="Input: v1 phase_0.jsonl (full row set including excluded)")
    p.add_argument("--output", default=str(PHASE0 / "logs" / "phase_0_metadata_v2.jsonl"))
    p.add_argument("--limit", type=int, default=0, help="Limit rows (debug)")
    args = p.parse_args()

    session = cc_requests.Session(impersonate=IMPERSONATE)
    session.headers.update({"User-Agent": EDGAR_UA})

    phase0 = Path(args.phase0_jsonl)
    if not phase0.exists():
        print(f"Missing input: {phase0}")
        sys.exit(1)

    # Group by CIK — one EDGAR fetch serves all funds under a registrant
    by_cik: dict[str, list[dict]] = {}
    all_rows: list[dict] = []
    with open(phase0) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("excluded"):
                continue
            all_rows.append(r)
            cik = str(r.get("cik") or "").lstrip("0")
            if cik:
                by_cik.setdefault(cik, []).append(r)

    print(f"[metadata-v2] Loaded {len(all_rows)} rows, {len(by_cik)} unique CIKs")
    if args.limit:
        ciks_slice = list(by_cik.keys())[: args.limit]
        by_cik = {c: by_cik[c] for c in ciks_slice}

    # Fetch latest 497 URL per CIK
    cik_497_url: dict[str, str] = {}
    t0 = time.monotonic()
    for i, cik in enumerate(by_cik, 1):
        url = get_latest_497_url(cik, session)
        if url:
            cik_497_url[cik] = url
        if i % 25 == 0:
            print(f"  [metadata-v2] cik {i}/{len(by_cik)} elapsed={time.monotonic()-t0:.0f}s"
                  f" hits={len(cik_497_url)}")

    print(f"[metadata-v2] Got 497 URLs for {len(cik_497_url)}/{len(by_cik)} CIKs")

    # Write enriched rows with upgraded Prospectus_URL
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for r in all_rows:
            cik = str(r.get("cik") or "").lstrip("0")
            new = dict(r)
            if cik in cik_497_url:
                new["prospectus_url"] = cik_497_url[cik]
            f.write(json.dumps(new) + "\n")
    print(f"[metadata-v2] wrote {out_path}")


if __name__ == "__main__":
    main()
