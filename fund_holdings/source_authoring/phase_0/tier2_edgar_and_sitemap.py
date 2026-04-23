"""Tier 2 + Tier 3: EDGAR prospectus grep, sitemap.xml enumeration, and
inline-JSON (hydration) extraction for sponsors that failed Tier 1.

Reads sponsor_probes_v2.jsonl to determine which sponsors lack a confirmed
endpoint, then attempts three techniques in order:

T2a — /sitemap.xml, /sitemap_index.xml, /robots.txt per sponsor host.
      Extract URLs containing the probe ticker. Verify promising ones.

T2b — EDGAR 497/N-1A filings: for each probe ticker fetch the latest
      497-related filing, grep body for http(s)://.*(holdings|holding),
      verify candidates.

T3  — Inline JSON / hydration-state extraction. Load the sponsor's landing
      page with curl_cffi; extract __NEXT_DATA__, window.__PRELOADED_STATE__,
      JSON-LD, etc. Look for URLs containing "holdings"/"portfolio"/"api".

Output appends to sponsor_probes_v2.jsonl and sponsor_endpoints_v2_candidates.jsonl.
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok, MIN_BYTES_FOR_FORMAT  # type: ignore
from sponsor_discovery_v2 import (
    SPONSOR_HOSTS, expand, fetch_curl_cffi, verify_body,
    guess_format_from_url, IMPERSONATE,
)

PHASE0 = Path(__file__).parent
LOG_DIR = PHASE0 / "logs" / "sponsor_discovery"
LOG_DIR.mkdir(parents=True, exist_ok=True)

EDGAR_UA = "MCAS-Phase0-Recovery dbk000+claude@anthropic.com"

HOLDINGS_KEYWORDS = [
    "holdings", "holding", "portfolio", "composition", "positions", "constituent",
    "portfoliosdata", "fundholdings", "fund-holdings",
]


def extract_urls_from_text(text: str, sponsor_hosts: list[str]) -> list[str]:
    """Extract http(s) URLs pointing at sponsor_hosts and containing holdings-related keywords."""
    urls: set[str] = set()
    # Generic URL regex
    url_re = re.compile(r"https?://[^\s\"'<>)(]{8,300}", re.IGNORECASE)
    for m in url_re.finditer(text):
        url = m.group(0).rstrip(".,;:\\")
        low = url.lower()
        # Sponsor-domain filter
        host = urlparse(url).netloc.lower()
        if not any(sh in host for sh in sponsor_hosts if sh):
            continue
        # Holdings-keyword filter
        if any(k in low for k in HOLDINGS_KEYWORDS):
            urls.add(url)
    return sorted(urls)


def tier2_sitemap(session, slug: str, cfg: dict) -> list[dict]:
    """Fetch /sitemap.xml and /robots.txt; extract candidate holdings URLs."""
    out: list[dict] = []
    for host in cfg.get("hosts", [])[:2]:
        base = f"https://{host}"
        for suffix in ["/sitemap.xml", "/sitemap_index.xml", "/robots.txt"]:
            url = base + suffix
            status, final_url, body, ct = fetch_curl_cffi(session, url)
            rec = {
                "sponsor": slug, "tier": "T2a_sitemap", "url": url,
                "status": status, "bytes": len(body), "content_type": ct[:60],
            }
            if status == 200 and body:
                text = body.decode("utf-8", errors="replace")
                urls = extract_urls_from_text(text, cfg["hosts"])
                rec["candidates"] = urls[:20]
                rec["n_candidates"] = len(urls)
            out.append(rec)
    return out


def tier2_edgar_prospectus(session, slug: str, cfg: dict, probe_tickers: list[str]) -> list[dict]:
    """Search EDGAR for 497 filings; grep body for sponsor-host URLs with holdings keyword."""
    out: list[dict] = []
    edgar_sess = cc_requests.Session(impersonate=IMPERSONATE)
    edgar_sess.headers.update({"User-Agent": EDGAR_UA})
    for t in probe_tickers[:1]:  # one ticker is enough for prospectus grep per sponsor
        # EDGAR full-text search for the ticker + "497"
        search_url = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22{t}%22&dateRange=custom"
            f"&startdt=2025-01-01&enddt=2026-04-23&forms=497K,497"
        )
        status, final_url, body, ct = fetch_curl_cffi(edgar_sess, search_url)
        rec = {
            "sponsor": slug, "tier": "T2b_edgar_search", "ticker": t,
            "url": search_url, "status": status, "bytes": len(body),
        }
        candidate_filings: list[str] = []
        if status == 200 and body:
            try:
                j = json.loads(body)
                for hit in (j.get("hits", {}).get("hits") or [])[:3]:
                    src = hit.get("_source", {})
                    acc = (src.get("adsh") or "").replace("-", "")
                    cik = (src.get("ciks") or [""])[0]
                    if acc and cik:
                        candidate_filings.append(
                            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
                        )
            except Exception as e:
                rec["parse_error"] = str(e)[:80]
        rec["candidate_filings"] = candidate_filings
        out.append(rec)

        # For each candidate filing, fetch the index and grep the main 497 doc
        for filing_url in candidate_filings[:2]:
            try:
                s2, _, idx_body, _ = fetch_curl_cffi(edgar_sess, filing_url)
                if s2 == 200 and idx_body:
                    # Find the main .htm file (usually first in the filing listing)
                    idx_text = idx_body.decode("utf-8", errors="replace")
                    # Look for href to a .htm file that isn't the index itself
                    hrefs = re.findall(r'href="([^"]+\.htm[l]?)"', idx_text, re.IGNORECASE)
                    main_docs = [h for h in hrefs if not h.endswith(("index.htm", "index.html"))][:2]
                    for md in main_docs:
                        doc_url = urljoin(filing_url, md)
                        s3, _, doc_body, _ = fetch_curl_cffi(edgar_sess, doc_url)
                        if s3 == 200 and doc_body:
                            text = doc_body.decode("utf-8", errors="replace")
                            urls = extract_urls_from_text(text, cfg.get("hosts", []))
                            out.append({
                                "sponsor": slug, "tier": "T2b_edgar_doc",
                                "doc_url": doc_url, "status": s3,
                                "bytes": len(doc_body),
                                "candidates": urls[:10],
                                "n_candidates": len(urls),
                            })
            except Exception as e:
                out.append({
                    "sponsor": slug, "tier": "T2b_edgar_doc_error",
                    "filing_url": filing_url,
                    "error": f"{type(e).__name__}: {str(e)[:100]}",
                })
    return out


HYDRATION_RE = [
    # Next.js
    re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL),
    # Redux / Apollo / initial-state hydration
    re.compile(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});', re.DOTALL),
    re.compile(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', re.DOTALL),
    re.compile(r'window\.__APOLLO_STATE__\s*=\s*(\{.*?\});', re.DOTALL),
    # Generic inline-json payload with an "etf" or "fund" key nearby
    re.compile(r'<script[^>]+type="application/(ld\+)?json"[^>]*>(.*?)</script>', re.DOTALL),
]


def tier3_hydration(session, slug: str, cfg: dict, probe_tickers: list[str]) -> list[dict]:
    """For each host × base_path × probe ticker, fetch the landing page and
    extract inline JSON. Look for candidate holdings URLs.
    """
    out: list[dict] = []
    for host in cfg.get("hosts", [])[:1]:  # primary host only — Tier 3 is slow
        for bp in cfg.get("base_paths", [])[:2]:  # first two base paths
            for t in probe_tickers[:1]:
                tmpl = f"https://{host}{bp if bp.startswith('/') else '/' + bp}"
                page_url = expand(tmpl, t)
                status, final_url, body, ct = fetch_curl_cffi(session, page_url)
                rec = {
                    "sponsor": slug, "tier": "T3_hydration",
                    "ticker": t, "url": page_url,
                    "final_url": final_url, "status": status, "bytes": len(body),
                }
                if status != 200 or len(body) < 500:
                    rec["note"] = "no_body"
                    out.append(rec)
                    continue
                text = body.decode("utf-8", errors="replace")
                candidates: set[str] = set()
                blobs: list[str] = []
                for pat in HYDRATION_RE:
                    for m in pat.finditer(text):
                        # Some patterns return full JSON, others an inner group
                        blob = m.group(m.lastindex) if m.lastindex else m.group(0)
                        if blob and len(blob) > 100:
                            blobs.append(blob[:200000])  # cap per blob
                # Also search for holdings-looking URLs anywhere in the HTML
                candidates.update(extract_urls_from_text(text, cfg["hosts"]))
                # And extract from parsed JSON blobs
                for blob in blobs:
                    try:
                        d = json.loads(blob)
                        flat = json.dumps(d)
                        candidates.update(extract_urls_from_text(flat, cfg["hosts"]))
                    except Exception:
                        # treat as raw text
                        candidates.update(extract_urls_from_text(blob, cfg["hosts"]))
                rec["n_blobs"] = len(blobs)
                rec["candidates"] = sorted(candidates)[:20]
                rec["n_candidates"] = len(candidates)
                out.append(rec)
    return out


def verify_candidate(session, slug: str, url_tmpl: str, probe_tickers: list[str]) -> dict:
    """Verify a candidate URL on 2 probe tickers. Returns {ok, fmt, template, details}."""
    results = []
    fmt_guess = "csv" if url_tmpl.lower().endswith(".csv") else (
        "xlsx" if url_tmpl.lower().endswith(".xlsx") else (
            "json" if "/api/" in url_tmpl.lower() or "json" in url_tmpl.lower() else "html_table"
        )
    )
    for t in probe_tickers[:2]:
        url = expand(url_tmpl, t)
        status, final_url, body, ct = fetch_curl_cffi(session, url)
        fmt_real = guess_format_from_url(final_url, ct, body) if status == 200 else fmt_guess
        ok, reason, info = (False, f"http_{status}", {})
        if status == 200:
            ok, reason, info = verify_body(body, fmt_real)
        results.append({
            "sponsor": slug, "tier": "T23_verify", "ticker": t,
            "url": url, "status": status, "ok": bool(ok), "reason": reason,
            "format": fmt_real, "bytes": len(body),
        })
    both_ok = all(r["ok"] for r in results) and len(results) == 2
    return {
        "confirmed": both_ok, "verify_records": results,
        "format": results[0]["format"] if results else fmt_guess,
    }


def process_sponsor(slug: str, probe_tickers: list[str]) -> list[dict]:
    """Run T2 + T3 for one sponsor. Returns all JSONL records."""
    cfg = SPONSOR_HOSTS.get(slug, {})
    if not cfg.get("hosts"):
        return [{"sponsor": slug, "event": "no_hosts_configured"}]
    if len(probe_tickers) < 2:
        return [{"sponsor": slug, "event": "insufficient_probe_tickers"}]

    session = cc_requests.Session(impersonate=IMPERSONATE)
    records: list[dict] = []
    all_candidates: set[str] = set()

    # T2a — sitemap
    try:
        for r in tier2_sitemap(session, slug, cfg):
            records.append(r)
            all_candidates.update(r.get("candidates") or [])
    except Exception as e:
        records.append({"sponsor": slug, "tier": "T2a_sitemap_error", "err": f"{type(e).__name__}: {e}"})

    # T2b — EDGAR
    try:
        for r in tier2_edgar_prospectus(session, slug, cfg, probe_tickers):
            records.append(r)
            all_candidates.update(r.get("candidates") or [])
    except Exception as e:
        records.append({"sponsor": slug, "tier": "T2b_edgar_error", "err": f"{type(e).__name__}: {e}"})

    # T3 — hydration
    try:
        for r in tier3_hydration(session, slug, cfg, probe_tickers):
            records.append(r)
            all_candidates.update(r.get("candidates") or [])
    except Exception as e:
        records.append({"sponsor": slug, "tier": "T3_hydration_error", "err": f"{type(e).__name__}: {e}"})

    # For each candidate URL, try to ticker-parameterize it and verify
    # Candidate URLs often have specific tickers baked in; template-ise by replacing
    # the first probe-ticker occurrence with {TICKER}.
    templates: set[str] = set()
    for u in all_candidates:
        for t in probe_tickers[:2]:
            if t.upper() in u.upper():
                # Template-ise
                tmpl = re.sub(re.escape(t), "{TICKER}", u, count=1, flags=re.IGNORECASE)
                templates.add(tmpl)
        templates.add(u)  # also try raw URL (may be static)

    confirmed_patterns: list[dict] = []
    for tmpl in list(templates)[:25]:  # cap — prevent runaway
        try:
            res = verify_candidate(session, slug, tmpl, probe_tickers)
            records.extend(res["verify_records"])
            if res["confirmed"]:
                confirmed_patterns.append({
                    "sponsor": slug, "event": "confirmed_pattern",
                    "label": f"{slug}:t23",
                    "template": tmpl, "format": res["format"],
                    "verified_tickers": probe_tickers[:2],
                })
        except Exception as e:
            records.append({"sponsor": slug, "tier": "T23_verify_error",
                            "template": tmpl, "err": f"{type(e).__name__}: {e}"})

    records.extend(confirmed_patterns)
    return records


def main():
    # Determine which sponsors need T2/T3: those NOT in sponsor_endpoints_v2_candidates.jsonl
    confirmed_path = PHASE0 / "sponsor_endpoints_v2_candidates.jsonl"
    confirmed_sponsors: set[str] = set()
    if confirmed_path.exists():
        with open(confirmed_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    confirmed_sponsors.add(d["sponsor"])
                except Exception:
                    pass

    # All sponsors we should cover
    ALL = list(SPONSOR_HOSTS.keys())
    unconfirmed = [s for s in ALL if s not in confirmed_sponsors]

    # Probe tickers
    rt_path = PHASE0 / "recovery_targets.json"
    with open(rt_path) as f:
        targets = json.load(f)
    test_tickers: dict[str, list] = targets["test_tickers"]
    excluded_by_sponsor = targets.get("excluded_funds_by_sponsor", {})

    work = []
    for slug in unconfirmed:
        ts = [t for _, t, _ in test_tickers.get(slug, [])]
        if len(ts) < 2:
            ts = list({r["ticker"] for r in excluded_by_sponsor.get(slug, [])})[:4]
        work.append((slug, ts[:2]))

    print(f"[tier2/3] Processing {len(work)} unconfirmed sponsors (8-way concurrency)\n")

    out_probes = PHASE0 / "sponsor_probes_v2.jsonl"  # append
    results_all = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(process_sponsor, slug, tk): slug for slug, tk in work}
        for i, fut in enumerate(as_completed(futs), 1):
            slug = futs[fut]
            try:
                recs = fut.result()
                results_all.extend(recs)
                n_confirmed = sum(1 for r in recs if r.get("event") == "confirmed_pattern")
                n_cands = sum(r.get("n_candidates", 0) for r in recs)
                print(f"[{i:2}/{len(work)}] {slug:28s} confirmed={n_confirmed}  total_candidates={n_cands}")
            except Exception as e:
                print(f"[{i:2}/{len(work)}] {slug:28s} EXCEPTION {type(e).__name__}: {e}")

    # Append to probes jsonl
    with open(out_probes, "a") as f:
        for r in results_all:
            f.write(json.dumps(r) + "\n")

    # Extend confirmed jsonl
    confirmed_recs = [r for r in results_all if r.get("event") == "confirmed_pattern"]
    by_sponsor: dict[str, list] = {}
    for r in confirmed_recs:
        by_sponsor.setdefault(r["sponsor"], []).append(r)
    with open(confirmed_path, "a") as f:
        for s, recs in by_sponsor.items():
            f.write(json.dumps({"sponsor": s, "patterns": recs}) + "\n")

    print(f"\nT2/T3 added confirmed for {len(by_sponsor)} sponsors")
    for s in sorted(by_sponsor):
        print(f"  {s:28s} {by_sponsor[s][0]['template'][:100]}")


if __name__ == "__main__":
    main()
