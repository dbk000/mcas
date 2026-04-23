"""Verify every Invesco ticker's holdings URL with curl_cffi + content_verify.
Outputs invesco_verified_urls.json with the final list of admitted tickers.
"""
import json, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from sponsor_discovery_v2 import IMPERSONATE, fetch_curl_cffi, verify_body

PHASE0 = Path(__file__).parent


def invesco_holdings_url(cusip: str) -> str:
    return (
        f"https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/"
        f"{cusip}/holdings/fund?idType=cusip&productType=ETF"
    )


def verify_one(args):
    ticker, cusip = args
    session = cc_requests.Session(impersonate=IMPERSONATE)
    session.headers.update({
        "Origin": "https://www.invesco.com",
        "Referer": f"https://www.invesco.com/us/financial-products/etfs/product-detail?audienceType=Investor&ticker={ticker}",
    })
    url = invesco_holdings_url(cusip)
    status, final_url, body, ct = fetch_curl_cffi(session, url)
    if status != 200:
        return ticker, {"ok": False, "reason": f"http_{status}", "cusip": cusip, "url": url}
    ok, reason, info = verify_body(body, "json")
    return ticker, {
        "ok": bool(ok), "reason": reason, "cusip": cusip, "url": url,
        "data_rows": info.get("data_rows") if ok else None,
        "bytes": len(body),
    }


def main():
    with open(PHASE0 / "invesco_ticker_cusip.json") as f:
        data = json.load(f)
    mapping = data["mapping"]
    print(f"[invesco_verify] Verifying {len(mapping)} tickers with 8-way concurrency")

    results = {}
    args_list = list(mapping.items())
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(verify_one, a): a[0] for a in args_list}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                tk, info = fut.result()
                results[tk] = info
            except Exception as e:
                tk = futs[fut]
                results[tk] = {"ok": False, "reason": f"exc:{type(e).__name__}"}
            if (i % 25) == 0:
                elapsed = time.monotonic() - t0
                ok_count = sum(1 for r in results.values() if r.get("ok"))
                print(f"  [{i}/{len(args_list)}] ok={ok_count} elapsed={elapsed:.0f}s")

    ok_results = {tk: r for tk, r in results.items() if r["ok"]}
    fail_results = {tk: r for tk, r in results.items() if not r["ok"]}
    print(f"\nVerified OK: {len(ok_results)}")
    print(f"Failed: {len(fail_results)}")
    from collections import Counter
    reasons = Counter(r["reason"] for r in fail_results.values())
    for r, c in reasons.most_common():
        print(f"  {c:3d} {r}")

    out = {"verified": ok_results, "failed": fail_results,
           "summary": {"mapped": len(mapping), "verified": len(ok_results),
                       "failed": len(fail_results)}}
    path = PHASE0 / "invesco_verified_urls.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nWrote: {path}")


if __name__ == "__main__":
    main()
