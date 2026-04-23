"""Tier 4: Playwright headless Chromium network-tab capture.

For each unrecovered top-30 priority sponsor (score ≥5 per recovery plan §4),
launch headless Chromium against 2 probe tickers per sponsor. Wait for network
idle. Dump every XHR URL. Score by holdings-related keywords. Probe top-3 against
content_verify.

Concurrency: 4-way (memory-bounded — ~400MB per chromium process, ~1.6GB RSS peak).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests as cc_requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok, MIN_BYTES_FOR_FORMAT  # type: ignore
from sponsor_discovery_v2 import (
    SPONSOR_HOSTS, expand, fetch_curl_cffi,
    guess_format_from_url, verify_body, IMPERSONATE,
)

PHASE0 = Path(__file__).parent
CAP_DIR = PHASE0 / "logs" / "network_captures"
CAP_DIR.mkdir(parents=True, exist_ok=True)

# Score 5+ priority sponsors per recovery plan §4 (top-30 subset)
PRIORITY_SPONSORS = [
    # Score 9
    "invesco",
    # Score 8
    "proshares", "dimensional", "jpmorgan", "schwab", "vaneck",
    "wisdomtree", "northern_trust",
    # Score 7
    "fidelity", "nuveen", "blackrock", "pacer", "tidal",
    # Score 6
    "alpha_architect", "franklin_templeton", "innovator", "calamos",
    "janus_henderson", "alliancebernstein", "alps",
    "victory_capital", "pgim", "bondbloxx",
    # Score 5
    "pimco", "t_rowe_price", "capital_group", "john_hancock", "xtrackers",
    "principal", "hartford", "exchange_traded_concepts",
]

HOLDINGS_KEYWORDS = [
    "holdings", "holding", "portfolio", "composition", "position", "constituent",
    "fundholdings", "fund-holdings", "all-holdings", "allholdings",
]


def score_xhr(url: str) -> int:
    """Score an XHR URL by keyword relevance to holdings data."""
    low = url.lower()
    score = 0
    for kw in HOLDINGS_KEYWORDS:
        if kw in low:
            score += 3
    if ".csv" in low:
        score += 2
    if ".xlsx" in low:
        score += 2
    if "/api/" in low or "/rest/" in low or "/services/" in low:
        score += 1
    if low.endswith(".json") or "application/json" in low:
        score += 1
    # Negative signals — likely not holdings
    for neg in ["analytics", "tracking", "metrics", "google", "doubleclick", "adnxs",
                "gtm.js", "facebook", "linkedin", "pixel", "segment.io"]:
        if neg in low:
            score -= 5
    return score


async def capture_xhr_for_ticker(playwright, host: str, path_template: str,
                                  ticker: str, timeout_ms: int = 20000) -> tuple[list[str], str | None]:
    """Launch Chromium, visit page, collect all XHR URLs. Return (xhr_list, final_html)."""
    xhr_urls: list[str] = []
    final_html: str | None = None
    browser = await playwright.chromium.launch(headless=True, args=[
        "--disable-blink-features=AutomationControlled",
        "--disable-web-security",
        "--no-sandbox",
    ])
    ctx = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
    )
    page = await ctx.new_page()

    def on_request(req):
        rtype = req.resource_type
        if rtype in ("xhr", "fetch", "other"):
            xhr_urls.append(req.url)

    page.on("request", on_request)

    url = f"https://{host}{path_template if path_template.startswith('/') else '/' + path_template}"
    url = expand(url, ticker)
    try:
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        # Wait for extra idle
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        # Give an extra 2s for late XHRs
        await page.wait_for_timeout(2000)
        try:
            final_html = await page.content()
        except Exception:
            pass
    except Exception as e:
        xhr_urls.append(f"__error__:{type(e).__name__}:{str(e)[:80]}")
    finally:
        await ctx.close()
        await browser.close()
    return xhr_urls, final_html


async def process_sponsor_async(slug: str, probe_tickers: list[str], sem: asyncio.Semaphore) -> dict:
    """Process one sponsor under a semaphore (concurrency cap)."""
    async with sem:
        cfg = SPONSOR_HOSTS.get(slug, {})
        if not cfg.get("hosts") or not cfg.get("base_paths") or len(probe_tickers) < 2:
            return {"sponsor": slug, "event": "skip_no_config"}
        host = cfg["hosts"][0]
        base_path = cfg["base_paths"][0]

        out = {
            "sponsor": slug,
            "host": host,
            "base_path": base_path,
            "probe_tickers": probe_tickers[:2],
            "xhrs_by_ticker": {},
        }
        async with async_playwright() as p:
            for t in probe_tickers[:2]:
                xhrs, _ = await capture_xhr_for_ticker(p, host, base_path, t)
                # Score and sort
                scored = [(u, score_xhr(u)) for u in xhrs]
                scored.sort(key=lambda x: -x[1])
                out["xhrs_by_ticker"][t] = {
                    "count": len(xhrs),
                    "top_scored": [(u, s) for u, s in scored[:15] if s > 0],
                    "all_sample": xhrs[:50],
                }
        # Save full capture
        cap_file = CAP_DIR / f"{slug}.json"
        with open(cap_file, "w") as f:
            json.dump(out, f, indent=2)
        return out


def verify_top_candidates(slug: str, capture: dict, probe_tickers: list[str]) -> list[dict]:
    """From the capture, probe the top-N XHR URLs with curl_cffi + content_verify."""
    # Union the scored URLs across tickers
    candidates: list[tuple[str, int]] = []
    for t, data in capture.get("xhrs_by_ticker", {}).items():
        for u, s in data.get("top_scored", []):
            candidates.append((u, s))
    # Dedup + sort by score
    seen: set[str] = set()
    uniq: list[tuple[str, int]] = []
    for u, s in sorted(candidates, key=lambda x: -x[1]):
        if u in seen:
            continue
        seen.add(u)
        uniq.append((u, s))

    # Template-ise top-5
    templates: set[str] = set()
    for u, _ in uniq[:10]:
        for t in probe_tickers[:2]:
            if t.upper() in u.upper():
                tmpl = re.sub(re.escape(t), "{TICKER}", u, count=1, flags=re.IGNORECASE)
                templates.add(tmpl)
        templates.add(u)  # also raw

    session = cc_requests.Session(impersonate=IMPERSONATE)
    session.headers.update({"Referer": f"https://{capture['host']}/"})
    results: list[dict] = []
    confirmed_patterns: list[dict] = []
    for tmpl in list(templates)[:8]:  # cap
        per_tk_ok = []
        fmt_final = None
        for t in probe_tickers[:2]:
            url = expand(tmpl, t)
            status, final_url, body, ct = fetch_curl_cffi(session, url)
            fmt = guess_format_from_url(final_url, ct, body) if status == 200 else "html_table"
            ok, reason, info = (False, f"http_{status}", {})
            if status == 200:
                ok, reason, info = verify_body(body, fmt)
            results.append({
                "sponsor": slug, "tier": "T4_verify", "ticker": t,
                "url": url, "template": tmpl, "status": status,
                "ok": bool(ok), "reason": reason, "format": fmt,
            })
            per_tk_ok.append(ok)
            fmt_final = fmt_final or fmt
        if all(per_tk_ok) and len(per_tk_ok) == 2:
            confirmed_patterns.append({
                "sponsor": slug, "event": "confirmed_pattern",
                "label": f"{slug}:t4",
                "template": tmpl, "format": fmt_final,
                "verified_tickers": probe_tickers[:2],
            })
    results.extend(confirmed_patterns)
    return results


async def main_async():
    # Load confirmed so far
    confirmed_path = PHASE0 / "sponsor_endpoints_v2_candidates.jsonl"
    confirmed: set[str] = set()
    if confirmed_path.exists():
        with open(confirmed_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    confirmed.add(d["sponsor"])
                except Exception:
                    pass

    todo = [s for s in PRIORITY_SPONSORS if s not in confirmed]
    print(f"[tier4] Processing {len(todo)} priority sponsors unconfirmed after T1/T2/T3")

    rt_path = PHASE0 / "recovery_targets.json"
    with open(rt_path) as f:
        targets = json.load(f)
    test_tickers: dict[str, list] = targets["test_tickers"]

    sem = asyncio.Semaphore(4)  # memory-bounded
    tasks = []
    for slug in todo:
        ts = [t for _, t, _ in test_tickers.get(slug, [])][:2]
        if len(ts) < 2:
            continue
        tasks.append(process_sponsor_async(slug, ts, sem))

    captures = await asyncio.gather(*tasks, return_exceptions=True)

    # Verify candidates with curl_cffi (synchronous is fine here)
    all_results: list[dict] = []
    confirmed_patterns: list[dict] = []
    for cap in captures:
        if isinstance(cap, Exception):
            print(f"EXCEPTION: {cap}")
            continue
        if cap.get("event") == "skip_no_config":
            continue
        slug = cap["sponsor"]
        ts = cap["probe_tickers"]
        vrecs = verify_top_candidates(slug, cap, ts)
        all_results.extend(vrecs)
        conf = [r for r in vrecs if r.get("event") == "confirmed_pattern"]
        confirmed_patterns.extend(conf)
        n_xhrs = sum(d["count"] for d in cap.get("xhrs_by_ticker", {}).values())
        print(f"  {slug:28s} xhrs={n_xhrs:4d}  confirmed={len(conf)}")

    # Append to probes + confirmed
    out_probes = PHASE0 / "sponsor_probes_v2.jsonl"
    with open(out_probes, "a") as f:
        for r in all_results:
            f.write(json.dumps(r) + "\n")

    by_sponsor: dict[str, list] = {}
    for r in confirmed_patterns:
        by_sponsor.setdefault(r["sponsor"], []).append(r)
    with open(confirmed_path, "a") as f:
        for s, recs in by_sponsor.items():
            f.write(json.dumps({"sponsor": s, "patterns": recs}) + "\n")

    print(f"\nT4 confirmed {len(by_sponsor)} additional sponsors:")
    for s in sorted(by_sponsor):
        print(f"  {s:28s} {by_sponsor[s][0]['template'][:100]}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
