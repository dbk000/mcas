"""Tier 5 deep probe: for Dimensional, JPMorgan, VanEck, Fidelity — load each
sponsor's ETF+holdings page via Playwright, capture EVERY response body ≥1KB,
inspect each for ticker/cusip/issuer data (holdings signature), and return the
one that content-verifies.

The key insight: holdings data is embedded in post-load XHR responses. Playwright
sees the response URL AND body; we just need to scan bodies broadly.
"""
import asyncio, json, sys, re
from pathlib import Path
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok


TARGETS = [
    ("vaneck", [
        ("SMH", "https://www.vaneck.com/us/en/investments/smh/holdings/"),
        ("MOAT", "https://www.vaneck.com/us/en/investments/moat/holdings/"),
        ("GDX", "https://www.vaneck.com/us/en/investments/gdx/holdings/"),
    ], []),
    ("dimensional", [
        ("DFAC", "https://us.dimensional.com/funds/dfac"),
        ("DFUS", "https://us.dimensional.com/funds/dfus"),
        ("DFUV", "https://us.dimensional.com/funds/dfuv"),
    ], [
        ["button:has-text('Individual Investor')",
         "button:has-text('Continue')",
         "button:has-text('Financial Professional')"],
    ]),
    ("jpmorgan", [
        ("JEPI", "https://am.jpmorgan.com/us/en/asset-management/adv/products/jepi-etf-shares"),
        ("JPST", "https://am.jpmorgan.com/us/en/asset-management/adv/products/jpst-etf-shares"),
    ], [
        ["button:has-text('Financial Professional')",
         "button:has-text('Advisor')",
         "button:has-text('Continue')"],
    ]),
    ("fidelity", [
        ("FTEC", "https://fundresearch.fidelity.com/etfs/composition/FTEC"),
        ("FDVV", "https://fundresearch.fidelity.com/etfs/composition/FDVV"),
    ], []),
]

COOKIE_ACCEPT = [
    "button#onetrust-accept-btn-handler",
    "button:has-text('Accept All Cookies')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I Agree')",
]


async def click_first(page, selectors, timeout=2500):
    for s in selectors:
        try:
            loc = page.locator(s)
            if await loc.first.is_visible(timeout=timeout):
                await loc.first.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


async def click_holdings_tab(page):
    """Click anything labelled Holdings/Portfolio/Composition."""
    for t in ["Holdings", "Portfolio", "Portfolio Holdings", "Composition",
              "Full Holdings", "Fund Holdings", "All Holdings"]:
        try:
            for role in ["tab", "link", "button"]:
                try:
                    loc = page.get_by_role(role, name=re.compile(rf"^{re.escape(t)}$", re.IGNORECASE))
                    if await loc.first.is_visible(timeout=1500):
                        await loc.first.click(timeout=2000)
                        return True
                except Exception:
                    continue
        except Exception:
            pass
    return False


async def deep_probe_sponsor(slug: str, pages: list, audience_selectors: list[list[str]]):
    results: dict[str, dict] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled", "--no-sandbox",
        ])
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 900},
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        for ticker, page_url in pages:
            page = await ctx.new_page()
            captures: list[tuple[str, bytes, str]] = []

            def on_response(resp):
                async def grab():
                    try:
                        url = resp.url
                        ct = resp.headers.get("content-type", "")
                        if not any(t in ct.lower() for t in ["html", "json", "csv", "xml", "plain"]):
                            return
                        b = await resp.body()
                        if 1000 <= len(b) <= 5 * 1024 * 1024:
                            captures.append((url, b, ct))
                    except Exception:
                        pass
                asyncio.create_task(grab())

            page.on("response", on_response)
            try:
                await page.goto(page_url, timeout=30000, wait_until="domcontentloaded")
                await click_first(page, COOKIE_ACCEPT, timeout=3000)
                await page.wait_for_timeout(800)
                for aud in audience_selectors:
                    clicked = await click_first(page, aud, timeout=3500)
                    if clicked:
                        await page.wait_for_timeout(2500)
                        break
                await page.wait_for_timeout(2000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                await click_holdings_tab(page)
                await page.wait_for_timeout(4500)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2500)

                # Screen all captures
                scored = []
                for url, body, ct in captures:
                    low = url.lower()
                    if any(bad in low for bad in ["cookielaw", "analytics", "doubleclick",
                                                    "google-analytics", "segment.io", "newrelic",
                                                    "gtm.js", "chase.com", "linkedin", "clarity.ms",
                                                    "omtrdc", "evergage", "hotjar", "marketo",
                                                    "gstatic", "gbqofs.io", "yimg", "fonts.googleapis",
                                                    "piwik", "contentsquare", "ipify", "recaptcha"]):
                        continue
                    # Try all formats
                    for fmt, fn in [("json", _json_ok), ("html_table", _html_ok),
                                     ("csv", _csv_ok)]:
                        try:
                            ok, reason, info = fn(body)
                            if ok:
                                scored.append({"url": url, "format": fmt,
                                               "bytes": len(body), "info": info})
                                break
                        except Exception:
                            pass

                results[ticker] = {
                    "page_url": page_url,
                    "total_captures": len(captures),
                    "verified": scored[:10],
                }
                print(f"  {slug:12s} {ticker:6s} captures={len(captures):3d} verified={len(scored)}")
                for sc in scored[:3]:
                    print(f"    [{sc['format']:10s}] {sc['bytes']:>7}b  {sc['url'][:130]}")
            except Exception as e:
                results[ticker] = {"error": f"{type(e).__name__}:{str(e)[:150]}"}
                print(f"  {slug:12s} {ticker:6s} ERROR: {e}")
            finally:
                await page.close()
        await ctx.close()
        await browser.close()
    return results


async def main_async():
    all_results = {}
    for slug, pages, audience in TARGETS:
        print(f"\n=== {slug} ===")
        try:
            r = await deep_probe_sponsor(slug, pages, audience)
        except Exception as e:
            r = {"_error": str(e)}
        all_results[slug] = r
    out_path = Path(__file__).parent / "tier5_deep_probe_results.json"
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main_async())
