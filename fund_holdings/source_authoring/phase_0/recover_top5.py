"""Tier 5 — targeted recovery of Invesco, Dimensional, JPMorgan, VanEck, Fidelity.

Each sponsor has its own discovery strategy derived from Tier 4b network
capture analysis:

- **Invesco**: ticker → CUSIP via `dng-api.invesco.com/product/search`, then
  holdings JSON from `dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/{CUSIP}/holdings/fund`.

- **VanEck**: load fund page, extract `contentId` (pageid) from inline state,
  then call `vaneck.com/Main/PortfolioPageBlock/GetContent/?pageid={ID}&ticker={T}`.
  Fallback: empirically probe blockid sequence on the /holdings page.

- **Dimensional**: session-auth approach — load audience-selector cookie, then
  call `etf.dimensional.com/public/v2/fundcenter/funddetail?ticker={T}`.

- **JPMorgan**: Playwright full-browser click-through captured XHRs with
  JSESSIONID; replay those URLs with session cookies.

- **Fidelity**: retry Tier 4b with different page URL (`/etfs/composition/`)
  and longer timeout.

For each sponsor, produces a per-fund Holdings_Feed_URL after 2-ticker
verification. Writes to sponsor_endpoints_v2_candidates.jsonl and a
per-sponsor ticker→url map in `recovered_top5_urls.json`.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import (
    _csv_ok, _xlsx_ok, _json_ok, _html_ok, MIN_BYTES_FOR_FORMAT,
)
from sponsor_discovery_v2 import (
    expand, fetch_curl_cffi, verify_body, guess_format_from_url, IMPERSONATE,
)

PHASE0 = Path(__file__).parent


# -----------------------------------------------------------------------------
# Invesco
# -----------------------------------------------------------------------------
def invesco_ticker_to_cusip(session, ticker: str) -> str | None:
    """Hit Invesco's product/search API to get CUSIP for a given ticker."""
    q = urllib.parse.quote(f"ticker:{ticker}")
    url = f"https://dng-api.invesco.com/product/search?q={q}&wt=json&rows=5"
    session.headers.update({"Origin": "https://www.invesco.com",
                            "Referer": "https://www.invesco.com/us/financial-products/etfs"})
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return None
        d = json.loads(r.content)
        for doc in (d.get("response") or {}).get("docs", []):
            if doc.get("ticker") == ticker and doc.get("cusip"):
                return doc["cusip"]
    except Exception:
        return None
    return None


def invesco_holdings_url(cusip: str) -> str:
    return (
        f"https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/"
        f"{cusip}/holdings/fund?idType=cusip&productType=ETF"
    )


def invesco_recover(tickers: list[str], session) -> dict[str, dict]:
    """Build ticker → (url, format) map. Verify each URL."""
    out: dict[str, dict] = {}
    for i, tk in enumerate(tickers):
        cusip = invesco_ticker_to_cusip(session, tk)
        if not cusip:
            continue
        url = invesco_holdings_url(cusip)
        status, final_url, body, ct = fetch_curl_cffi(session, url)
        if status == 200:
            ok, reason, _ = verify_body(body, "json")
            if ok:
                out[tk] = {"url": url, "format": "json", "cusip": cusip}
                continue
        # record failure
        out[tk] = {"url": url, "format": "json", "cusip": cusip,
                   "verify_failed": reason if status == 200 else f"http_{status}"}
    return out


# -----------------------------------------------------------------------------
# VanEck — discover pageid + blockid per ticker by loading the page
# -----------------------------------------------------------------------------
VANECK_PAGEID_CACHE: dict[str, int] = {}
VANECK_BLOCKID_CACHE: dict[int, list[int]] = {}


def vaneck_load_page_ids(session, ticker: str) -> dict | None:
    """Load the fund page, extract pageid + candidate blockids.

    VanEck's pageid is in the rendered HTML (inside script tags & data- attrs).
    curl_cffi gets most of it; we look broader patterns.
    """
    for path in [
        f"/us/en/investments/{ticker.lower()}/holdings/",
        f"/us/en/investments/{ticker.lower()}/",
        f"/us/en/investments/{ticker.lower()}",
    ]:
        url = f"https://www.vaneck.com{path}"
        try:
            r = session.get(url, timeout=15, allow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200 or len(r.content) < 10000:
            continue
        body = r.content.decode('utf-8', errors='replace')
        # Multiple patterns seen in VanEck DOM:
        # 1) "contentId":233107
        # 2) ?pageid=233107 in iframe/block URLs
        # 3) Epi.contentId = 233107 in inline script
        pats = [
            r'"contentId"\s*:\s*(\d{5,7})',
            r'contentId\s*[=:]\s*[\'"]?(\d{5,7})',
            r'pageid[=:][\'"]?(\d{5,7})',
            r'Epi\.contentId\s*=\s*(\d{5,7})',
            r'data-content-id=["\'](\d{5,7})',
        ]
        all_ids: list[str] = []
        for p in pats:
            all_ids.extend(re.findall(p, body))
        if not all_ids:
            continue
        counts = {i: all_ids.count(i) for i in set(all_ids)}
        pageid = max(counts, key=counts.get)
        # Block IDs
        blockids = list(set(re.findall(r'blockid[=:][\'"]?(\d{5,7})', body)))
        # Also try FundDetails script tags
        blockids += list(set(re.findall(r'BlockId"?\s*:\s*(\d{5,7})', body)))
        # Deduplicate
        blockids = list(set(blockids))
        return {"pageid": int(pageid),
                "blockids": [int(b) for b in blockids],
                "final_url": r.url}
    return None


def vaneck_holdings_url(pageid: int, blockid: int, ticker: str) -> str:
    """Construct a GetContent URL. Known blockids:
    229617 = FundDetailsBlock (SMH), 364471 = ContentArea (SMH).
    These vary per fund.
    """
    return (
        f"https://www.vaneck.com/Main/FundDetailsBlock/GetContent/"
        f"?blockid={blockid}&pageid={pageid}&ticker={ticker}"
        f"&reactlang=en&reactctr=us&epieditmode=false&latest=false&contextmode=Default"
    )


def vaneck_recover(tickers: list[str], session) -> dict[str, dict]:
    """For each ticker: load fund page → extract pageid + candidate blockids → probe."""
    out: dict[str, dict] = {}
    for tk in tickers:
        info = vaneck_load_page_ids(session, tk)
        if not info:
            out[tk] = {"verify_failed": "no_pageid_found"}
            continue
        pageid = info["pageid"]
        blockids = info["blockids"]
        # Prefer blockids that look like FundDetails or ContentArea (heuristic: 6-digit)
        verified_url = None
        verified_reason = None
        for bid in blockids[:6]:
            url = vaneck_holdings_url(pageid, bid, tk)
            status, final_url, body, ct = fetch_curl_cffi(session, url)
            if status != 200:
                continue
            # VanEck returns JSON with embedded HTML; try JSON first
            try:
                d = json.loads(body)
                # Find holdings-like markup
                markup = None
                for k, v in d.items():
                    if isinstance(v, str) and ("<table" in v or "holdings" in v.lower()):
                        markup = v
                        break
                if not markup and isinstance(d.get("data"), dict):
                    for k, v in d["data"].items():
                        if isinstance(v, str) and ("<table" in v or "holdings" in v.lower()):
                            markup = v
                            break
                if markup:
                    # Verify the markup as html_table
                    ok, reason, _ = _html_ok(markup.encode("utf-8"))
                    if ok:
                        verified_url = url
                        out[tk] = {"url": url, "format": "html_table",
                                   "pageid": pageid, "blockid": bid}
                        break
                    else:
                        verified_reason = reason
            except Exception:
                # Try as raw html
                ok, reason, _ = _html_ok(body)
                if ok:
                    verified_url = url
                    out[tk] = {"url": url, "format": "html_table",
                               "pageid": pageid, "blockid": bid}
                    break
                else:
                    verified_reason = reason
        if tk not in out:
            out[tk] = {"verify_failed": verified_reason or "no_blockid_matched",
                       "pageid": pageid, "blockids": blockids}
    return out


# -----------------------------------------------------------------------------
# Dimensional — try direct API with session cookies
# -----------------------------------------------------------------------------
def dimensional_build_session(session):
    """Load audience selector + accept cookies to get session-bearing cookies."""
    # Load landing page to set cookies
    urls = [
        "https://us.dimensional.com/audience-selector-api/individual-investor",
        "https://us.dimensional.com/funds/dfac",
    ]
    for u in urls:
        session.get(u, timeout=10)


def dimensional_recover(tickers: list[str], session) -> dict[str, dict]:
    """Try fund-detail API for each ticker."""
    dimensional_build_session(session)
    out: dict[str, dict] = {}
    session.headers.update({
        "Origin": "https://us.dimensional.com",
        "Referer": "https://us.dimensional.com/funds/",
    })
    for tk in tickers:
        # Candidate URLs
        candidates = [
            f"https://etf.dimensional.com/public/v2/fundcenter/funddetail?ticker={tk}",
            f"https://us.dimensional.com/funds/{tk.lower()}/holdings.json",
            f"https://us.dimensional.com/data/funds/{tk.lower()}/holdings.csv",
            f"https://tools-blob.dimensional.com/holdings/{tk}.json",
            f"https://tools-blob.dimensional.com/holdings/{tk}.csv",
            f"https://www.dimensional.com/api/funds/{tk.lower()}/holdings",
        ]
        got = False
        for url in candidates:
            try:
                r = session.get(url, timeout=10)
                if r.status_code != 200 or len(r.content) < 500:
                    continue
                fmt = guess_format_from_url(r.url, r.headers.get("content-type", ""), r.content)
                ok, reason, _ = verify_body(r.content, fmt)
                if ok:
                    out[tk] = {"url": url, "format": fmt}
                    got = True
                    break
            except Exception:
                continue
        if not got:
            out[tk] = {"verify_failed": "no_candidate_worked"}
    return out


# -----------------------------------------------------------------------------
# JPMorgan — Playwright full browser to get session, then replay XHRs
# -----------------------------------------------------------------------------
async def jpmorgan_playwright_get_holdings(tickers: list[str]) -> dict[str, dict]:
    """Full Playwright flow: accept cookies, click audience=Advisor,
    navigate to product page, click Holdings tab, capture XHR.
    """
    from playwright.async_api import async_playwright
    out: dict[str, dict] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled", "--no-sandbox",
        ])
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        for tk in tickers:
            page = await ctx.new_page()
            holdings_xhrs: list[tuple[str, bytes]] = []

            def on_response(resp):
                url = resp.url
                if any(kw in url.lower() for kw in ["holding", "portfolio", "composition"]):
                    # consume body
                    async def grab():
                        try:
                            b = await resp.body()
                            holdings_xhrs.append((url, b))
                        except Exception:
                            pass
                    asyncio.create_task(grab())

            page.on("response", on_response)
            try:
                await page.goto(f"https://am.jpmorgan.com/us/en/asset-management/adv/products/{tk.lower()}-etf-shares",
                                timeout=30000, wait_until="domcontentloaded")
                # Accept cookies
                for btn in ["button#onetrust-accept-btn-handler",
                            "button:has-text('Accept All')"]:
                    try:
                        loc = page.locator(btn)
                        if await loc.first.is_visible(timeout=2000):
                            await loc.first.click(timeout=2000)
                            break
                    except Exception:
                        pass
                await page.wait_for_timeout(1500)
                # Audience selector
                for btn in ["button:has-text('Financial Professional')",
                            "button:has-text('Advisor')",
                            "button:has-text('Continue')"]:
                    try:
                        loc = page.locator(btn)
                        if await loc.first.is_visible(timeout=2000):
                            await loc.first.click(timeout=2000)
                            break
                    except Exception:
                        pass
                await page.wait_for_timeout(3000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                # Click Holdings
                for t in ["Holdings", "Portfolio", "Portfolio Holdings"]:
                    try:
                        loc = page.get_by_role("link", name=re.compile(t, re.IGNORECASE))
                        if await loc.first.is_visible(timeout=2000):
                            await loc.first.click(timeout=2000)
                            break
                    except Exception:
                        pass
                await page.wait_for_timeout(5000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                # Inspect captured XHRs
                best = None
                for url, body in holdings_xhrs:
                    if len(body) < 1024:
                        continue
                    fmt = guess_format_from_url(url, "", body)
                    ok, reason, _ = verify_body(body, fmt)
                    if ok:
                        best = (url, fmt)
                        break
                if best:
                    out[tk] = {"url": best[0], "format": best[1]}
                else:
                    out[tk] = {"verify_failed": "no_holdings_xhr",
                               "xhr_count": len(holdings_xhrs)}
            except Exception as e:
                out[tk] = {"verify_failed": f"playwright_error:{type(e).__name__}"}
            finally:
                await page.close()
        await ctx.close()
        await browser.close()
    return out


# -----------------------------------------------------------------------------
# Fidelity — same as JPMorgan but different URL
# -----------------------------------------------------------------------------
async def fidelity_playwright_get_holdings(tickers: list[str]) -> dict[str, dict]:
    from playwright.async_api import async_playwright
    out: dict[str, dict] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled", "--no-sandbox",
        ])
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 900},
        )
        for tk in tickers:
            page = await ctx.new_page()
            xhrs: list[tuple[str, bytes]] = []

            def on_response(resp):
                url = resp.url
                if any(kw in url.lower() for kw in ["holding", "portfolio", "composition", "compDetails"]):
                    async def grab():
                        try:
                            b = await resp.body()
                            xhrs.append((url, b))
                        except Exception:
                            pass
                    asyncio.create_task(grab())

            page.on("response", on_response)
            try:
                await page.goto(
                    f"https://fundresearch.fidelity.com/etfs/composition/{tk}",
                    timeout=25000, wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(3000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # Find best XHR
                best = None
                for url, body in xhrs:
                    if len(body) < 1024:
                        continue
                    fmt = guess_format_from_url(url, "", body)
                    ok, reason, _ = verify_body(body, fmt)
                    if ok:
                        best = (url, fmt)
                        break
                if best:
                    out[tk] = {"url": best[0], "format": best[1]}
                else:
                    out[tk] = {"verify_failed": "no_holdings_xhr",
                               "xhr_count": len(xhrs)}
            except Exception as e:
                out[tk] = {"verify_failed": f"playwright_error:{type(e).__name__}"}
            finally:
                await page.close()
        await ctx.close()
        await browser.close()
    return out


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    # Load recovery targets
    with open(PHASE0 / "recovery_targets.json") as f:
        targets = json.load(f)
    test_tickers = targets["test_tickers"]

    session = cc_requests.Session(impersonate=IMPERSONATE)
    all_results: dict[str, dict[str, dict]] = {}

    # Invesco
    print("\n=== Invesco ===")
    tk_invesco = [t for _, t, _ in test_tickers.get("invesco", [])][:3]
    invesco_res = invesco_recover(tk_invesco, session)
    for tk, info in invesco_res.items():
        print(f"  {tk:6s} {info}")
    all_results["invesco"] = invesco_res

    # VanEck
    print("\n=== VanEck ===")
    tk_vaneck = [t for _, t, _ in test_tickers.get("vaneck", [])][:3]
    session2 = cc_requests.Session(impersonate=IMPERSONATE)
    vaneck_res = vaneck_recover(tk_vaneck, session2)
    for tk, info in vaneck_res.items():
        print(f"  {tk:6s} {info}")
    all_results["vaneck"] = vaneck_res

    # Dimensional
    print("\n=== Dimensional ===")
    tk_dim = [t for _, t, _ in test_tickers.get("dimensional", [])][:3]
    session3 = cc_requests.Session(impersonate=IMPERSONATE)
    dim_res = dimensional_recover(tk_dim, session3)
    for tk, info in dim_res.items():
        print(f"  {tk:6s} {info}")
    all_results["dimensional"] = dim_res

    # JPMorgan
    print("\n=== JPMorgan (Playwright) ===")
    tk_jpm = [t for _, t, _ in test_tickers.get("jpmorgan", [])][:2]
    try:
        jpm_res = asyncio.run(jpmorgan_playwright_get_holdings(tk_jpm))
    except Exception as e:
        jpm_res = {"_error": str(e)}
    for tk, info in jpm_res.items():
        print(f"  {tk:6s} {info}")
    all_results["jpmorgan"] = jpm_res

    # Fidelity
    print("\n=== Fidelity (Playwright) ===")
    tk_fid = [t for _, t, _ in test_tickers.get("fidelity", [])][:2]
    try:
        fid_res = asyncio.run(fidelity_playwright_get_holdings(tk_fid))
    except Exception as e:
        fid_res = {"_error": str(e)}
    for tk, info in fid_res.items():
        print(f"  {tk:6s} {info}")
    all_results["fidelity"] = fid_res

    # Save
    out_path = PHASE0 / "recovered_top5_urls.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nWrote: {out_path}")


if __name__ == "__main__":
    main()
