"""
Phase 0 — Fund Bible: remediate broken Holdings_Feed_URLs.

Some sponsor URL templates in `registry/sponsor_url_templates.yaml`
produced URLs that return 200 for the HEAD during enrichment but 404
after redirect resolution in schema_check.py (example: SSGA's
`https://www.ssga.com/.../holdings-daily-us-en-{TICKER}.xlsx` pattern
was recently migrated by SSGA and many older tickers 404 at the new
location). This script:

  1. Re-HEAD-checks every record's `holdings_feed_url`.
  2. For records where the check fails (not in OK_STATUSES), replaces
     the URL with the EDGAR fallback (constructed from `cik`) and sets
     `holdings_feed_format = 'edgar_fallback'`, appending a note
     `"url_failed_verification_edgar_fallback"`.
  3. Writes the fixed records to --output.
  4. Also rewrites `sponsor_page_url` when the primary URL fails the
     same check.

Idempotent — running twice on the same input is a no-op.

Usage:
    python3 remediate_broken_urls.py \\
        --input logs/phase_0.jsonl \\
        --output logs/phase_0_remediated.jsonl \\
        --workers 32
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests


USER_AGENT_POLITE = "MCAS Phase 0 Remediator / Darren Kenny dkcrypto520@gmail.com"
USER_AGENT_BROWSER = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
OK_STATUSES = {200, 301, 302, 303, 307, 308, 405}
SEC_HOSTS = {"www.sec.gov", "sec.gov"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("remediate")


class HostThrottle:
    def __init__(self, interval: float = 0.15):
        self.interval = interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if not host:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last.get(host, 0.0)
                if elapsed >= self.interval:
                    self._last[host] = now
                    return
                sleep_for = self.interval - elapsed
            time.sleep(sleep_for)


def head_check(session: requests.Session, url: str, throttle: HostThrottle) -> tuple[bool, int | None]:
    for method, ua in [("HEAD", USER_AGENT_POLITE),
                        ("HEAD", USER_AGENT_BROWSER),
                        ("GET", USER_AGENT_BROWSER)]:
        throttle.wait(url)
        try:
            r = session.request(
                method, url,
                headers={"User-Agent": ua, "Accept": "*/*"},
                timeout=20.0, allow_redirects=True,
            )
            if r.status_code in OK_STATUSES:
                return (True, r.status_code)
            # Only retry on 400/403/406 (bot-block indicators).
            if r.status_code not in (400, 403, 406):
                return (False, r.status_code)
        except requests.RequestException:
            continue
    return (False, None)


def edgar_url(cik: str, filing_type: str = "N-1A") -> str:
    c = (cik or "").lstrip("0") or "0"
    return (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={c}&type={filing_type}&dateb=&owner=include&count=40")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=pathlib.Path)
    ap.add_argument("--output", required=True, type=pathlib.Path)
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    records: list[dict] = []
    with args.input.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    log.info("loaded %d records", len(records))

    session = requests.Session()
    throttle = HostThrottle(interval=0.15)

    # Gather unique URLs to check (holdings + fund_page).
    urls_to_check: set[str] = set()
    for r in records:
        u = r.get("holdings_feed_url") or ""
        if u and u != "null":
            urls_to_check.add(u)
        u = r.get("sponsor_page_url") or ""
        if u and u != "null":
            urls_to_check.add(u)

    log.info("unique URLs to re-verify: %d", len(urls_to_check))

    results: dict[str, tuple[bool, int | None]] = {}
    completed = 0
    start = time.monotonic()
    last_log = start

    def _task(u: str) -> tuple[str, bool, int | None]:
        ok, status = head_check(session, u, throttle)
        return (u, ok, status)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_task, u) for u in urls_to_check]
        for fut in as_completed(futures):
            url, ok, status = fut.result()
            results[url] = (ok, status)
            completed += 1
            now = time.monotonic()
            if now - last_log > 10 or completed == len(urls_to_check):
                rate = completed / max(1e-3, now - start)
                rem = len(urls_to_check) - completed
                eta = rem / rate if rate > 0 else 0
                log.info("progress: %d/%d (%.1f/s, eta %ds)",
                         completed, len(urls_to_check), rate, int(eta))
                last_log = now

    pass_n = sum(1 for (ok, _) in results.values() if ok)
    fail_n = sum(1 for (ok, _) in results.values() if not ok)
    log.info("URL check: %d pass, %d fail (of %d unique)", pass_n, fail_n, len(results))

    # Rewrite failing URLs to EDGAR fallback per-record.
    fixed_holdings = 0
    fixed_fund_page = 0
    sponsors_hit: Counter = Counter()
    for r in records:
        hu = r.get("holdings_feed_url") or ""
        if hu and hu != "null":
            ok, status = results.get(hu, (False, None))
            if not ok:
                r["holdings_feed_url"] = edgar_url(r.get("cik", ""), "N-1A")
                r["holdings_feed_format"] = "edgar_fallback"
                existing_note = (r.get("notes") or "").strip()
                if existing_note in ("", "null"):
                    r["notes"] = "url_failed_verification_edgar_fallback"
                elif "url_failed_verification" not in existing_note:
                    r["notes"] = existing_note + " | url_failed_verification_edgar_fallback"
                fixed_holdings += 1
                sponsors_hit[r.get("sponsor_slug", "")] += 1
        fu = r.get("sponsor_page_url") or ""
        if fu and fu != "null":
            ok, _ = results.get(fu, (False, None))
            if not ok:
                r["sponsor_page_url"] = edgar_url(r.get("cik", ""), "N-1A")
                fixed_fund_page += 1

    with args.output.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    log.info("remediated %d holdings URLs, %d sponsor_page URLs", fixed_holdings, fixed_fund_page)
    if sponsors_hit:
        log.info("remediation by sponsor:")
        for s, n in sponsors_hit.most_common(20):
            log.info("  %-28s %d", s, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
