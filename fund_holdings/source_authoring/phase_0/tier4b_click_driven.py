"""Tier 4b — click-driven Playwright capture.

Second-pass capture: for each target sponsor, load the ETF page, dismiss cookie
banners / audience modals, click the Holdings tab/link, wait for XHRs, then
capture:

1) Full rendered HTML (post-JS) — may contain embedded holdings table suitable
   for html_table content-verify.
2) All XHR URLs fired during the interaction — scored + validated per v1.

Handles anti-headless via:
- User-Agent spoofing with chrome124 (matches curl_cffi)
- `--disable-blink-features=AutomationControlled`
- Viewport + window.chrome mock via page.add_init_script

Output:
- logs/network_captures/{sponsor}_click.json — full XHR + rendered HTML summary
- Appends confirmed_pattern records to sponsor_endpoints_v2_candidates.jsonl
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

from curl_cffi import requests as cc_requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok, MIN_BYTES_FOR_FORMAT  # type: ignore
from sponsor_discovery_v2 import (
    expand, fetch_curl_cffi, guess_format_from_url, verify_body, IMPERSONATE,
)

PHASE0 = Path(__file__).parent
CAP_DIR = PHASE0 / "logs" / "network_captures"
CAP_DIR.mkdir(parents=True, exist_ok=True)

# Per-sponsor: (page_url_template, click_targets, cookie_accept_selectors)
# click_targets is a list of CSS selectors / text selectors to try in order.
TARGETS = {
    "invesco": {
        "page_tmpl": "https://www.invesco.com/us/financial-products/etfs/product-detail?audienceType=Investor&ticker={TICKER}",
        "tickers": ["RSP", "QQQM", "SPHQ"],
        "click_text": ["Holdings", "Portfolio", "Full Holdings"],
        "accept_cookies": ["button#onetrust-accept-btn-handler",
                           "button:has-text('Accept All')",
                           "button:has-text('Accept')"],
        "audience_click": ["button:has-text('Investor')", "button:has-text('Continue')"],
        "holdings_host_hints": ["invesco.com"],
    },
    "dimensional": {
        "page_tmpl": "https://us.dimensional.com/funds/{ticker_lower}",
        "tickers": ["DFAC", "DFUS", "DFUV"],
        "click_text": ["Holdings", "Portfolio", "Full Holdings", "Daily Holdings"],
        "accept_cookies": ["button#onetrust-accept-btn-handler",
                           "button:has-text('Accept All')",
                           "button:has-text('Accept')"],
        "audience_click": ["button:has-text('Individual Investor')",
                            "button:has-text('Financial Professional')",
                            "button:has-text('Continue')"],
        "holdings_host_hints": ["dimensional.com"],
    },
    "jpmorgan": {
        "page_tmpl": "https://am.jpmorgan.com/us/en/asset-management/adv/products/{ticker_lower}-etf-shares",
        "tickers": ["JEPI", "JPST", "JEPQ"],
        "click_text": ["Holdings", "Portfolio", "Portfolio Holdings", "Full Holdings"],
        "accept_cookies": ["button#onetrust-accept-btn-handler",
                           "button:has-text('Accept All Cookies')",
                           "button:has-text('Accept')"],
        "audience_click": ["button:has-text('Financial Advisor')",
                            "button:has-text('Advisor')",
                            "button:has-text('Continue')"],
        "holdings_host_hints": ["jpmorgan.com", "jpm.com"],
    },
    "vaneck": {
        "page_tmpl": "https://www.vaneck.com/us/en/investments/{ticker_lower}/",
        "tickers": ["SMH", "GDX", "MOAT"],
        "click_text": ["Holdings", "Portfolio", "Top Ten Holdings"],
        "accept_cookies": ["button:has-text('Accept All')",
                           "button:has-text('Accept')",
                           "button:has-text('I Agree')"],
        "audience_click": ["button:has-text('Individual Investor')",
                            "button:has-text('Continue')"],
        "holdings_host_hints": ["vaneck.com"],
    },
    "fidelity": {
        "page_tmpl": "https://fundresearch.fidelity.com/etfs/summary/{TICKER}",
        "tickers": ["FBND", "FTEC", "FDVV"],
        "click_text": ["Composition", "Portfolio Composition", "Holdings", "Top Holdings"],
        "accept_cookies": ["button:has-text('Accept')",
                           "button:has-text('Agree')"],
        "audience_click": [],
        "holdings_host_hints": ["fidelity.com"],
    },
}

HOLDINGS_KEYWORDS = [
    "holdings", "holding", "portfolio", "composition", "position", "constituent",
    "fundholdings", "fund-holdings", "all-holdings", "allholdings",
]

BAD_TOKENS = [
    "cookielaw", "consent", "analytics", "optimizely", "adobe.com/ee",
    "googletagmanager", "google-analytics", "doubleclick", "facebook",
    "adnxs", "demdex", "adobedtm", "segment.io", "gtm.js", "segment.com",
    "tealium", "omtrdc", "everest", "bt.lt", "hotjar", "qualtrics",
    "fonts.googleapis", "youtube.com/embed", ".woff", ".css", ".svg",
    ".png", ".jpg", ".gif", ".webp", ".ico", "akamaihd.net", "gstatic.com",
    "clarity.ms", "newrelic", "dynatrace", "contentsquare", "evergage.com",
    "piwik", "boomtrain", "linkedin", "spotify", "reddit", "pinterest",
    "gmp/conversion", "bam.nr-data.net", "recaptcha", "maze.co",
    "chase.com/apps", "chasecdn.com", "chase.com/etc", "userway.org",
    "gbqofs.io", "yimg.com", "crazyegg", "csp.withgoogle",
]


def score_xhr(url: str) -> int:
    low = url.lower()
    score = 0
    for kw in HOLDINGS_KEYWORDS:
        if kw in low:
            score += 5
    if ".csv" in low:
        score += 3
    if ".xlsx" in low:
        score += 3
    if "/api/" in low or "/rest/" in low or "/services/" in low:
        score += 2
    if "ticker=" in low or "/fund/" in low or "/etf/" in low:
        score += 2
    if low.endswith(".json"):
        score += 1
    if "download" in low:
        score += 2
    for bad in BAD_TOKENS:
        if bad in low:
            score -= 10
    return score


def filter_real_xhr(urls: list[str], host_hints: list[str]) -> list[str]:
    """Return XHRs that are plausibly holdings-related."""
    seen = set()
    out = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        low = u.lower()
        # Must be under the sponsor's domain OR explicitly match a holdings keyword
        host_match = any(h in low for h in host_hints)
        kw_match = any(kw in low for kw in HOLDINGS_KEYWORDS)
        if not (host_match or kw_match):
            continue
        if any(bad in low for bad in BAD_TOKENS):
            continue
        out.append(u)
    return out


async def try_click_any(page, selectors: list[str], *, timeout: int = 4000) -> bool:
    """Try each selector. Return True if one succeeded."""
    for sel in selectors:
        try:
            if sel.startswith("button:has-text"):
                # Pass through to locator
                loc = page.locator(sel)
            elif sel.startswith("#") or sel.startswith("."):
                loc = page.locator(sel)
            else:
                loc = page.locator(sel)
            if await loc.first.is_visible(timeout=timeout // 2):
                await loc.first.click(timeout=timeout)
                return True
        except Exception:
            pass
    return False


async def click_text_any(page, texts: list[str], *, timeout: int = 4000) -> bool:
    """Try clicking any of these text-based selectors (link / button / tab)."""
    for t in texts:
        try:
            # Try role=tab first, then link, then button, then plain text
            for role in ["tab", "link", "button"]:
                try:
                    loc = page.get_by_role(role, name=re.compile(t, re.IGNORECASE))
                    if await loc.first.is_visible(timeout=timeout // 3):
                        await loc.first.click(timeout=timeout)
                        return True
                except Exception:
                    continue
            # Fallback: text-only
            loc = page.get_by_text(re.compile(rf"^{re.escape(t)}$", re.IGNORECASE))
            if await loc.first.is_visible(timeout=timeout // 3):
                await loc.first.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


async def capture_sponsor(playwright, slug: str, cfg: dict) -> dict:
    """For one sponsor, load 2-3 tickers and try to click through to Holdings."""
    out = {"sponsor": slug, "ticker_captures": {}}
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    ctx = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
        java_script_enabled=True,
    )
    # Anti-headless tweaks
    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    """)

    for ticker in cfg["tickers"][:2]:  # 2 tickers per sponsor for verification
        page = await ctx.new_page()
        xhrs: list[str] = []
        page.on("request", lambda r: xhrs.append(r.url) if r.resource_type in ("xhr", "fetch") else None)
        tk_url = expand(cfg["page_tmpl"], ticker)
        cap = {"ticker": ticker, "url": tk_url, "steps": []}
        try:
            await page.goto(tk_url, timeout=25000, wait_until="domcontentloaded")
            cap["steps"].append("goto_ok")
            # Accept cookies
            if cfg.get("accept_cookies"):
                ok = await try_click_any(page, cfg["accept_cookies"], timeout=3000)
                if ok:
                    cap["steps"].append("cookies_accepted")
                    await page.wait_for_timeout(500)
            # Audience selector
            if cfg.get("audience_click"):
                ok = await try_click_any(page, cfg["audience_click"], timeout=3000)
                if ok:
                    cap["steps"].append("audience_selected")
                    await page.wait_for_timeout(2000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
            # Initial network-idle wait
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            cap["xhr_pre_click"] = len(xhrs)
            # Click Holdings
            clicked = await click_text_any(page, cfg["click_text"], timeout=4000)
            cap["holdings_clicked"] = clicked
            if clicked:
                cap["steps"].append("holdings_clicked")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                await page.wait_for_timeout(3000)
            cap["xhr_post_click"] = len(xhrs)
            # Extract rendered HTML
            try:
                html = await page.content()
                cap["html_bytes"] = len(html)
                # Sniff tables
                cap["n_tables"] = html.count("<table")
            except Exception:
                cap["html_bytes"] = 0

            # Filter real XHRs
            real = filter_real_xhr(xhrs, cfg["holdings_host_hints"])
            scored = sorted([(u, score_xhr(u)) for u in real], key=lambda x: -x[1])
            cap["xhr_count"] = len(xhrs)
            cap["xhr_real_count"] = len(real)
            cap["top_scored"] = [{"url": u, "score": s} for u, s in scored[:15] if s > 0]
            cap["all_real"] = real[:60]
        except Exception as e:
            cap["error"] = f"{type(e).__name__}:{str(e)[:150]}"
        finally:
            await page.close()
        out["ticker_captures"][ticker] = cap

    await ctx.close()
    await browser.close()
    return out


async def main_async():
    print(f"[tier4b] Processing {len(TARGETS)} priority sponsors with click-driven capture")
    async with async_playwright() as p:
        results = []
        # Process one sponsor at a time (memory-bounded; already 2 tickers per sponsor in browser)
        for slug, cfg in TARGETS.items():
            print(f"[tier4b] === {slug} ===")
            t0 = time.monotonic()
            try:
                cap = await capture_sponsor(p, slug, cfg)
            except Exception as e:
                cap = {"sponsor": slug, "error": f"{type(e).__name__}:{str(e)[:200]}"}
            dt = time.monotonic() - t0
            results.append(cap)
            cap_path = CAP_DIR / f"{slug}_click.json"
            with open(cap_path, "w") as f:
                json.dump(cap, f, indent=2)
            # Short summary
            summary = []
            for tk, data in cap.get("ticker_captures", {}).items():
                summary.append(f"{tk}: clicked={data.get('holdings_clicked')}, "
                               f"xhr_real={data.get('xhr_real_count')}, "
                               f"tables={data.get('n_tables')}")
            print(f"  took={dt:.1f}s  {'; '.join(summary)}  → {cap_path.name}")
    return results


def main():
    asyncio.run(main_async())
    print("\n[tier4b] Captures written. Use curated_from_tier4b.py to probe + verify.")


if __name__ == "__main__":
    main()
