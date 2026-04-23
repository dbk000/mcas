"""Deep VanEck probe — navigate to /holdings/ via Playwright, capture ALL response
bodies (not just URLs), and inspect each for holdings data.
"""
import asyncio, json, sys, re
from pathlib import Path
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok


async def deep_probe(tickers):
    out = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled", "--no-sandbox",
        ])
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 900},
        )
        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        for tk in tickers:
            page = await ctx.new_page()
            captures: list[tuple[str, bytes, str]] = []

            def on_response(resp):
                async def grab():
                    try:
                        url = resp.url
                        ct = resp.headers.get("content-type", "")
                        # Only grab non-trivial content types
                        if any(t in ct.lower() for t in ["html", "json", "csv", "xml", "plain"]):
                            b = await resp.body()
                            captures.append((url, b, ct))
                    except Exception:
                        pass
                asyncio.create_task(grab())

            page.on("response", on_response)
            url = f"https://www.vaneck.com/us/en/investments/{tk.lower()}/holdings/"
            print(f"\n=== {tk} at {url} ===")
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
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
                # Audience selector
                for btn in ["button:has-text('Individual Investor')",
                            "button:has-text('Continue')"]:
                    try:
                        loc = page.locator(btn)
                        if await loc.first.is_visible(timeout=2000):
                            await loc.first.click(timeout=2000)
                            break
                    except Exception:
                        pass
                # Wait for holdings to render
                await page.wait_for_timeout(5000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                # Try to scroll to force lazy loads
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(3000)

                # Get post-render HTML — does the DOM now have holdings tables?
                html = await page.content()
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, 'lxml')
                tables = soup.find_all('table')
                table_info = []
                for i, t in enumerate(tables):
                    rows = t.find_all('tr')
                    if len(rows) >= 5:
                        first = rows[0].find_all(['th','td'])
                        table_info.append({
                            "i": i, "rows": len(rows),
                            "header": [c.get_text().strip()[:30] for c in first]
                        })

                # Score captured responses
                scored = []
                for rurl, body, ct in captures:
                    if len(body) < 1000:
                        continue
                    if any(bad in rurl.lower() for bad in ["cookielaw", "consent", "analytics",
                                                              "google", "doubleclick", ".css", ".js",
                                                              ".png", ".jpg", ".svg", ".woff",
                                                              "gtm.js", "marketo"]):
                        continue
                    # Try all formats
                    scores = {}
                    for fmt, fn in [("json", _json_ok), ("html_table", _html_ok),
                                     ("csv", _csv_ok)]:
                        try:
                            ok, reason, info = fn(body)
                            if ok:
                                scores[fmt] = info
                        except Exception:
                            pass
                    if scores:
                        scored.append({"url": rurl, "bytes": len(body), "ct": ct[:40],
                                       "verify": scores})
                out[tk] = {
                    "tables_in_rendered_dom": table_info[:5],
                    "verified_responses": scored[:10],
                    "total_captures": len(captures),
                }
                print(f"  rendered_tables: {len(table_info)}")
                print(f"  verified_responses: {len(scored)}")
                for sc in scored[:5]:
                    print(f"    [{list(sc['verify'].keys())}] {sc['bytes']}b  {sc['url'][:130]}")
            except Exception as e:
                out[tk] = {"error": f"{type(e).__name__}:{str(e)[:150]}"}
                print(f"  ERROR: {e}")
            finally:
                await page.close()

        await ctx.close()
        await browser.close()
    return out


if __name__ == "__main__":
    r = asyncio.run(deep_probe(["SMH", "MOAT", "GDX"]))
    out_path = Path(__file__).parent / "vaneck_deep_probe.json"
    out_path.write_text(json.dumps(r, indent=2))
    print(f"\nWrote {out_path}")
