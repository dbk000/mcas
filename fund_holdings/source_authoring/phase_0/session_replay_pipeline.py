"""Session-Replay Pipeline — Playwright harvests cookies + request templates,
then curl_cffi replays holdings XHRs per ticker with those cookies.

Architecture:
  Phase A — Harvest (per sponsor, once)
    1. Launch Playwright, load fund page for 2 known tickers
    2. Click through cookie-accept + audience-selector + Holdings tab
    3. Capture EVERY response ≥1KB with its request details (method, URL,
       headers, body) and context cookies
    4. For each URL that appears for both tickers, content-verify the body.
       If it passes AND differs between tickers → that's the holdings template.
    5. Export session state (cookies + URL template + method + body template +
       headers) to sessions/{sponsor}.json

  Phase B — Replay (per sponsor, for all excluded tickers)
    1. Load sessions/{sponsor}.json
    2. For each ticker in the sponsor's excluded list:
       - Substitute ticker into URL (and body if POST)
       - curl_cffi GET/POST with saved cookies + headers
       - Content-verify response
    3. Collect ticker→URL map of passing funds

  Phase C — Integration
    Run rebuild_bible_v2 with the new URL map to admit newly-recovered funds.

Usage:
  python3 session_replay_pipeline.py --sponsors dimensional vaneck
  python3 session_replay_pipeline.py --all
  python3 session_replay_pipeline.py --sponsors jpmorgan --skip-harvest
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from curl_cffi import requests as cc_requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from content_verify import _csv_ok, _xlsx_ok, _json_ok, _html_ok, MIN_BYTES_FOR_FORMAT
from sponsor_discovery_v2 import (
    expand, fetch_curl_cffi, guess_format_from_url, verify_body, IMPERSONATE,
)

PHASE0 = Path(__file__).parent
SESSIONS_DIR = PHASE0 / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)
CAP_DIR = PHASE0 / "logs" / "session_captures"
CAP_DIR.mkdir(parents=True, exist_ok=True)


# Per-sponsor harvest config. Two probe tickers each + click flow.
HARVEST_TARGETS: dict[str, dict] = {
    "dimensional": {
        "page_url_tmpl": "https://us.dimensional.com/funds/{ticker_lower}",
        "tickers": ["DFAC", "DFUS"],
        "cookie_accept": [
            "button#onetrust-accept-btn-handler",
            "button:has-text('Accept All')",
            "button:has-text('Accept')",
        ],
        "audience_accept": [
            "button:has-text('Individual Investor')",
            "button:has-text('Financial Professional')",
            "button:has-text('Continue')",
            "button:has-text('Proceed')",
            "a:has-text('Individual Investor')",
        ],
        "holdings_click": ["Holdings", "Portfolio", "Full Holdings", "Daily Holdings"],
        "holdings_hints": ["dimensional.com"],
    },
    "jpmorgan": {
        "page_url_tmpl": "https://am.jpmorgan.com/us/en/asset-management/adv/products/{ticker_lower}-etf-shares",
        "tickers": ["JEPI", "JPST"],
        "cookie_accept": [
            "button#onetrust-accept-btn-handler",
            "button:has-text('Accept All Cookies')",
            "button:has-text('Accept')",
        ],
        "audience_accept": [
            "button:has-text('Financial Professional')",
            "a:has-text('Financial Professional')",
            "button:has-text('Advisor')",
            "button:has-text('Continue')",
        ],
        "holdings_click": ["Holdings", "Portfolio", "Portfolio Holdings"],
        "holdings_hints": ["jpmorgan.com", "jpm.com"],
    },
    "vaneck": {
        "page_url_tmpl": "https://www.vaneck.com/us/en/investments/{ticker_lower}/holdings/",
        "tickers": ["SMH", "MOAT"],
        "cookie_accept": [
            "button:has-text('Accept All')",
            "button:has-text('Accept')",
            "button:has-text('I Agree')",
        ],
        "audience_accept": [
            "button:has-text('Individual Investor')",
            "button:has-text('Continue')",
            "a:has-text('Individual')",
        ],
        "holdings_click": ["Holdings", "Portfolio", "Top Ten Holdings"],
        "holdings_hints": ["vaneck.com"],
    },
    "fidelity": {
        "page_url_tmpl": "https://fundresearch.fidelity.com/etfs/summary/{TICKER}",
        "tickers": ["FTEC", "FDVV"],
        "cookie_accept": [
            "button:has-text('Accept')",
            "button:has-text('Agree')",
        ],
        "audience_accept": [],
        "holdings_click": ["Composition", "Portfolio Composition", "Holdings",
                           "Top Holdings", "Top 10 Holdings"],
        "holdings_hints": ["fidelity.com"],
    },
    "franklin_templeton": {
        "page_url_tmpl": "https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/{ticker_lower}-etf?segment=investor",
        "tickers": ["FLCB", "FLIN"],
        "cookie_accept": [
            "button#onetrust-accept-btn-handler",
            "button:has-text('Accept All')",
            "button:has-text('Accept')",
        ],
        "audience_accept": [],
        "holdings_click": ["Holdings", "Portfolio", "Portfolio Holdings", "Top Holdings"],
        "holdings_hints": ["franklintempleton.com", "ftsites.com"],
    },
}


BAD_URL_TOKENS = [
    "cookielaw", "consent", "analytics", "doubleclick", "google-analytics",
    "segment.io", "newrelic", "gtm.js", "chase.com", "linkedin", "clarity.ms",
    "omtrdc", "evergage", "hotjar", "marketo", "gstatic", "gbqofs.io", "yimg",
    "fonts.googleapis", "piwik", "contentsquare", "ipify", "recaptcha",
    "crazyegg", "csp.withgoogle", "facebook", "doubleclick", "reddit",
    "spotify", "pinterest", "adobedtm", "adobe.com/ee", "tealium",
    "adnxs", "demdex", "optimizely", "taboola", "gstatic.com",
]


async def click_first_visible(page, selectors, timeout_ms=2500):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.first.is_visible(timeout=timeout_ms // 2):
                await loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


async def click_text_any(page, texts, timeout_ms=3000):
    for t in texts:
        try:
            for role in ["tab", "link", "button"]:
                try:
                    loc = page.get_by_role(role, name=re.compile(rf"^{re.escape(t)}$", re.IGNORECASE))
                    if await loc.first.is_visible(timeout=1500):
                        await loc.first.click(timeout=timeout_ms)
                        return True
                except Exception:
                    pass
        except Exception:
            pass
    return False


async def harvest_sponsor(slug: str, cfg: dict) -> dict | None:
    """Phase A — harvest session state and capture holdings XHR for 2 tickers."""
    out = {"sponsor": slug, "harvested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "per_ticker": {}, "cookies": [], "session_headers": {}}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage",
        ])
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
        )

        for ticker in cfg["tickers"][:2]:
            page = await ctx.new_page()
            page_url = expand(cfg["page_url_tmpl"], ticker)
            captures: list[dict] = []
            # Track request bodies by URL+method via route interception
            request_bodies: dict[str, bytes | None] = {}

            async def on_route(route, request):
                try:
                    body = request.post_data_buffer
                    if body is None:
                        body = request.post_data
                        if isinstance(body, str):
                            body = body.encode('utf-8')
                    request_bodies[f"{request.method} {request.url}"] = body
                except Exception:
                    pass
                try:
                    await route.continue_()
                except Exception:
                    pass

            # Intercept everything
            await page.route("**/*", on_route)

            def on_response(resp):
                async def grab():
                    try:
                        url = resp.url
                        ct = resp.headers.get("content-type", "")
                        if not any(k in ct.lower() for k in ["html", "json", "csv", "xml", "plain"]):
                            return
                        b = await resp.body()
                        if not (1000 <= len(b) <= 5 * 1024 * 1024):
                            return
                        req = resp.request
                        key = f"{req.method} {req.url}"
                        body_bytes = request_bodies.get(key)
                        # Dump request info
                        captures.append({
                            "url": url,
                            "method": req.method,
                            "request_headers": dict(req.headers),
                            "request_post_data": (
                                body_bytes.decode('utf-8', errors='replace')
                                if isinstance(body_bytes, bytes) else body_bytes
                            ),
                            "response_ct": ct,
                            "response_body": b,
                        })
                    except Exception:
                        pass
                asyncio.create_task(grab())

            page.on("response", on_response)
            try:
                await page.goto(page_url, timeout=30000, wait_until="domcontentloaded")
                # Accept cookies
                await click_first_visible(page, cfg.get("cookie_accept", []), timeout_ms=3000)
                await page.wait_for_timeout(700)
                # Audience
                if cfg.get("audience_accept"):
                    clicked = await click_first_visible(page, cfg["audience_accept"], timeout_ms=4000)
                    if clicked:
                        await page.wait_for_timeout(2500)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception:
                            pass
                # Wait for base load
                await page.wait_for_timeout(1500)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # Click Holdings tab
                await click_text_any(page, cfg.get("holdings_click", []), timeout_ms=3000)
                await page.wait_for_timeout(4000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # Also scroll to trigger lazy loads
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2500)

                # Save captures (filter bad tokens)
                cap_filtered = []
                for c in captures:
                    low = c["url"].lower()
                    if any(bad in low for bad in BAD_URL_TOKENS):
                        continue
                    # Only keep sponsor-host responses
                    if cfg.get("holdings_hints") and \
                       not any(h in low for h in cfg["holdings_hints"]):
                        # also keep if it has holdings keyword
                        if not any(kw in low for kw in ["holding", "portfolio", "composition",
                                                         "position", "constituent"]):
                            continue
                    cap_filtered.append(c)

                out["per_ticker"][ticker] = cap_filtered
                print(f"  {slug}:{ticker} captures={len(captures)} kept={len(cap_filtered)}")

                # Grab cookies after this ticker's flow
                out["cookies"] = await ctx.cookies()
            except Exception as e:
                print(f"  {slug}:{ticker} ERROR {type(e).__name__}: {str(e)[:120]}")
                out["per_ticker"][ticker] = []
            finally:
                await page.close()

        await ctx.close()
        await browser.close()
    return out


def find_holdings_template(harvest: dict, tickers: list[str]) -> dict | None:
    """From harvested responses, find URLs that:
       1) Appear for both tickers AND differ in body (ticker-specific)
       2) Pass content-verify as holdings data
    """
    per_tk = harvest.get("per_ticker", {})
    if len(tickers) < 2:
        return None
    # Index by URL pattern
    def normalize_url(url: str, tk: str) -> str:
        """Replace ticker occurrences with placeholder so the same template matches both."""
        u = url
        u = re.sub(r'\b' + re.escape(tk) + r'\b', "{TICKER}", u, flags=re.IGNORECASE)
        u = re.sub(r'\b' + re.escape(tk.lower()) + r'\b', "{TICKER}", u, flags=re.IGNORECASE)
        return u

    by_template: dict[str, dict[str, list[dict]]] = {}
    for tk in tickers:
        for c in per_tk.get(tk, []):
            tmpl = normalize_url(c["url"], tk)
            by_template.setdefault(tmpl, {}).setdefault(tk, []).append(c)

    candidates = []
    for tmpl, tk_map in by_template.items():
        if len(tk_map) < 2:
            continue
        # Get one capture per ticker
        cap_a = tk_map[tickers[0]][0]
        cap_b = tk_map[tickers[1]][0]
        body_a = cap_a["response_body"]
        body_b = cap_b["response_body"]
        if body_a == body_b:
            continue  # same body both tickers — not ticker-specific
        # Content-verify each
        ct = cap_a["response_ct"]
        fmt_guess = ("json" if "json" in ct else
                      "csv" if "csv" in ct else
                      "html_table" if "html" in ct else "json")
        vfn = {"json": _json_ok, "csv": _csv_ok, "html_table": _html_ok}[fmt_guess]
        try:
            ok_a, reason_a, info_a = vfn(body_a)
            ok_b, reason_b, info_b = vfn(body_b)
        except Exception:
            continue
        if ok_a and ok_b:
            # Stronger gate: both should have at least one of (units/weight/market_value/cusip)
            def has_instrument_fields(info, body):
                keys_lower = [str(k).lower() for k in info.get("keys", [])]
                blob = body[:20000].decode('utf-8', errors='replace').lower()
                has_id = any(k in blob for k in ['"ticker"', '"cusip"', '"isin"', '"symbol"', '"identifier"'])
                has_inst = any(k in blob for k in ['"units"', '"weight"', '"market', '"shares"',
                                                     '"quantity"', '"percent', '"allocation"',
                                                     'percentageoftotal', 'marketvaluebase'])
                return has_id and has_inst
            if has_instrument_fields(info_a, body_a) and has_instrument_fields(info_b, body_b):
                candidates.append({
                    "template": tmpl, "format": fmt_guess,
                    "method": cap_a["method"],
                    "request_headers": cap_a["request_headers"],
                    "request_post_data_a": cap_a["request_post_data"],
                    "request_post_data_b": cap_b["request_post_data"],
                    "bytes_a": len(body_a), "bytes_b": len(body_b),
                    "info_a": info_a, "info_b": info_b,
                    "url_a": cap_a["url"], "url_b": cap_b["url"],
                })
    # Prefer JSON over html_table (more robust), largest body
    candidates.sort(key=lambda c: (c["format"] != "json", -c["bytes_a"]))
    return candidates[0] if candidates else None


def save_session(slug: str, harvest: dict, template: dict | None):
    path = SESSIONS_DIR / f"{slug}.json"
    payload = {
        "sponsor": slug,
        "harvested_at": harvest["harvested_at"],
        "cookies": harvest.get("cookies", []),
        "template": template,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    # Save full captures for debugging (bodies as base64-encoded text)
    import base64
    cap_path = CAP_DIR / f"{slug}_harvest.json"
    cap_serializable = {}
    for tk, caps in harvest.get("per_ticker", {}).items():
        lst = []
        for c in caps:
            rec = {}
            for k, v in c.items():
                if k == "response_body" and isinstance(v, bytes):
                    # Save both a text preview and base64 for full round-trip
                    rec["response_body_len"] = len(v)
                    rec["response_body_preview"] = v[:800].decode('utf-8', errors='replace')
                    rec["response_body_b64"] = base64.b64encode(v).decode('ascii')
                elif isinstance(v, bytes):
                    rec[k] = v.decode('utf-8', errors='replace')
                else:
                    rec[k] = v
            lst.append(rec)
        cap_serializable[tk] = lst
    with open(cap_path, "w") as f:
        json.dump(cap_serializable, f, indent=2, default=str)
    print(f"  saved session → {path}  + capture → {cap_path}")


# -----------------------------------------------------------------------------
# Replay
# -----------------------------------------------------------------------------
def build_cc_session(session_data: dict) -> cc_requests.Session:
    """Build a curl_cffi session with saved cookies + headers."""
    s = cc_requests.Session(impersonate=IMPERSONATE)
    # Install cookies
    for c in session_data.get("cookies", []):
        try:
            s.cookies.set(c["name"], c["value"],
                           domain=c.get("domain", ""), path=c.get("path", "/"))
        except Exception:
            pass
    return s


def replay_for_ticker(session: cc_requests.Session, template: dict, ticker: str,
                        extra_headers: dict | None = None) -> tuple[bool, str, str, dict]:
    """Replay the captured request for a new ticker. Returns (ok, reason, url, info)."""
    url = expand(template["template"], ticker)
    method = template.get("method", "GET")
    headers = dict(template.get("request_headers", {}))
    # Remove some playwright-added headers
    for k in list(headers):
        if k.lower() in ("host", ":authority:", ":method:", ":path:", ":scheme:",
                          "content-length"):
            headers.pop(k, None)
    if extra_headers:
        headers.update(extra_headers)
    # Body
    body = None
    if method == "POST":
        raw_a = template.get("request_post_data_a")
        if raw_a:
            # Template-ise ticker
            body = re.sub(re.escape(template["template"].replace("{TICKER}", "")), "", raw_a) if False else raw_a
            # Substitute ticker occurrences in body
            for tk in ["DFAC", "DFUS", "SMH", "MOAT", "JEPI", "JPST", "FLCB", "FLIN",
                       "FTEC", "FDVV"]:
                body = re.sub(r'\b' + re.escape(tk) + r'\b', ticker, body, flags=re.IGNORECASE)
                body = re.sub(r'\b' + re.escape(tk.lower()) + r'\b', ticker.lower(), body, flags=re.IGNORECASE)
    try:
        if method == "POST":
            r = session.post(url, data=body, headers=headers, timeout=15)
        else:
            r = session.get(url, headers=headers, timeout=15)
    except Exception as e:
        return False, f"exc:{type(e).__name__}:{str(e)[:60]}", url, {}
    if r.status_code != 200:
        return False, f"http_{r.status_code}", url, {}
    fmt = template["format"]
    if len(r.content) < MIN_BYTES_FOR_FORMAT.get(fmt, 100):
        return False, f"body_too_small:{len(r.content)}", url, {}
    try:
        if fmt == "json":
            ok, reason, info = _json_ok(r.content)
        elif fmt == "csv":
            ok, reason, info = _csv_ok(r.content)
        elif fmt == "html_table":
            ok, reason, info = _html_ok(r.content)
        else:
            ok, reason, info = False, f"unsupported:{fmt}", {}
        return ok, reason, url, info
    except Exception as e:
        return False, f"verify_exc:{type(e).__name__}", url, {}


def replay_sponsor(slug: str, sponsor_tickers: list[str], workers: int = 4) -> dict:
    """Replay the captured template for all tickers. Returns ticker → result."""
    sess_path = SESSIONS_DIR / f"{slug}.json"
    if not sess_path.exists():
        print(f"  [replay] no session file at {sess_path}")
        return {}
    with open(sess_path) as f:
        session_data = json.load(f)
    template = session_data.get("template")
    if not template:
        print(f"  [replay] session has no template — harvest failed to find holdings XHR")
        return {}
    print(f"  [replay] template: {template.get('method')} {template['template'][:120]}")
    print(f"  [replay] format: {template['format']}, bytes_sample: {template.get('bytes_a')}/{template.get('bytes_b')}")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    def one(tk):
        # New session per thread
        s = build_cc_session(session_data)
        return tk, replay_for_ticker(s, template, tk)

    results = {}
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one, tk): tk for tk in sponsor_tickers}
        for i, fut in enumerate(as_completed(futs), 1):
            tk, (ok, reason, url, info) = fut.result()
            results[tk] = {"ok": bool(ok), "reason": reason, "url": url,
                            "data_rows": info.get("data_rows")}
            if (i % 25) == 0:
                ok_count = sum(1 for r in results.values() if r["ok"])
                print(f"    [{i}/{len(sponsor_tickers)}] ok={ok_count} elapsed={time.monotonic()-t0:.0f}s")
    ok_count = sum(1 for r in results.values() if r["ok"])
    print(f"  [replay] {slug}: {ok_count}/{len(sponsor_tickers)} verified")
    return results


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
async def harvest_phase(sponsors: list[str]):
    for slug in sponsors:
        cfg = HARVEST_TARGETS.get(slug)
        if not cfg:
            print(f"[harvest] {slug}: no config, skipping")
            continue
        print(f"\n[harvest] === {slug} ===")
        try:
            harvest = await harvest_sponsor(slug, cfg)
        except Exception as e:
            print(f"  harvest ERROR {type(e).__name__}: {e}")
            continue
        template = find_holdings_template(harvest, cfg["tickers"][:2])
        if template:
            print(f"  ✓ template found: {template['method']} {template['template'][:100]} ({template['format']})")
        else:
            print(f"  ✗ no holdings template derived from harvest")
        save_session(slug, harvest, template)


def replay_phase(sponsors: list[str]):
    import yaml
    with open(PHASE0 / "excluded_funds.yaml") as f:
        excluded_data = yaml.safe_load(f)
    excluded_by_sponsor: dict[str, list[str]] = {}
    for r in excluded_data["records"]:
        excluded_by_sponsor.setdefault(r["sheet"], []).append(r["ticker"])

    all_results = {}
    for slug in sponsors:
        tickers = excluded_by_sponsor.get(slug, [])
        print(f"\n[replay] === {slug} ({len(tickers)} tickers) ===")
        if not tickers:
            print(f"  no excluded tickers")
            continue
        results = replay_sponsor(slug, tickers)
        all_results[slug] = results
        out_path = PHASE0 / f"session_replay_{slug}.json"
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"  wrote {out_path}")

    # Summary
    print("\n\n=== REPLAY SUMMARY ===")
    for slug, r in all_results.items():
        ok = sum(1 for v in r.values() if v["ok"])
        print(f"  {slug:25s} {ok:3d}/{len(r)} verified")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sponsors", nargs="+", default=[])
    p.add_argument("--all", action="store_true", help="Run all configured sponsors")
    p.add_argument("--skip-harvest", action="store_true")
    p.add_argument("--skip-replay", action="store_true")
    args = p.parse_args()

    if args.all:
        sponsors = list(HARVEST_TARGETS.keys())
    else:
        sponsors = args.sponsors or ["dimensional"]

    print(f"Sponsors: {sponsors}")
    print(f"Harvest: {not args.skip_harvest}  Replay: {not args.skip_replay}")

    if not args.skip_harvest:
        asyncio.run(harvest_phase(sponsors))
    if not args.skip_replay:
        replay_phase(sponsors)


if __name__ == "__main__":
    main()
