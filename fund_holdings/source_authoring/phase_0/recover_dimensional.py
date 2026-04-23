"""Recover Dimensional — CSV URL pattern discovered via session harvest:
  https://tools-blob.dimensional.com/etf/{YYYYMMDD}/{TICKER}.csv

URLs with YYYYMMDD=today work for all Dimensional ETFs. The blob CDN serves
CSV holdings for each ticker, dated by as-of-date (typically T-1 business day).

Strategy:
  1. Find the latest working date (today, then T-1, T-2, ... up to T-7)
  2. For each excluded Dimensional ticker, construct URL for that date
  3. Verify each URL with curl_cffi + content_verify
  4. Write recovered_dimensional_urls.json
"""
import json, sys, time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from sponsor_discovery_v2 import IMPERSONATE, fetch_curl_cffi
from content_verify import _csv_ok

PHASE0 = Path(__file__).parent


def find_latest_working_date(session, probe_ticker="DFAC") -> str | None:
    """Probe for the latest available YYYYMMDD where Dimensional CSV works."""
    today = datetime.utcnow().date()
    for delta in range(0, 10):
        date = today - timedelta(days=delta)
        ymd = date.strftime("%Y%m%d")
        url = f"https://tools-blob.dimensional.com/etf/{ymd}/{probe_ticker}.csv"
        try:
            r = session.get(url, timeout=8)
            if r.status_code == 200 and len(r.content) > 1000:
                ok, _, _ = _csv_ok(r.content)
                if ok:
                    return ymd
        except Exception:
            pass
    return None


def verify_one(args):
    ticker, ymd = args
    session = cc_requests.Session(impersonate=IMPERSONATE)
    url = f"https://tools-blob.dimensional.com/etf/{ymd}/{ticker}.csv"
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return ticker, {"ok": False, "reason": f"http_{r.status_code}", "url": url}
        ok, reason, info = _csv_ok(r.content)
        return ticker, {"ok": bool(ok), "reason": reason, "url": url,
                        "data_rows": info.get("data_rows") if ok else None}
    except Exception as e:
        return ticker, {"ok": False, "reason": f"exc:{type(e).__name__}", "url": url}


def main():
    import yaml
    with open(PHASE0 / "excluded_funds.yaml") as f:
        excluded = yaml.safe_load(f)
    tickers = sorted({r["ticker"] for r in excluded["records"] if r["sheet"] == "dimensional"})
    print(f"Dimensional excluded tickers: {len(tickers)}")

    session = cc_requests.Session(impersonate=IMPERSONATE)
    ymd = find_latest_working_date(session)
    if not ymd:
        print("ERROR: could not find any working date for Dimensional")
        sys.exit(1)
    print(f"Using date: {ymd}")

    results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(verify_one, (tk, ymd)): tk for tk in tickers}
        t0 = time.monotonic()
        for i, fut in enumerate(as_completed(futs), 1):
            tk, r = fut.result()
            results[tk] = r
            if (i % 10) == 0:
                ok_c = sum(1 for v in results.values() if v["ok"])
                print(f"  [{i}/{len(tickers)}] ok={ok_c} elapsed={time.monotonic()-t0:.0f}s")

    ok_count = sum(1 for r in results.values() if r["ok"])
    print(f"\nVerified: {ok_count}/{len(tickers)}")
    from collections import Counter
    fail_reasons = Counter(r["reason"] for r in results.values() if not r["ok"])
    for reason, cnt in fail_reasons.most_common():
        print(f"  {cnt:3d}  {reason}")

    out = {
        "date_stamp": ymd,
        "url_template": f"https://tools-blob.dimensional.com/etf/{{YYYYMMDD}}/{{TICKER}}.csv",
        "verified": {tk: r for tk, r in results.items() if r["ok"]},
        "failed": {tk: r for tk, r in results.items() if not r["ok"]},
    }
    path = PHASE0 / "recovered_dimensional_urls.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"Wrote: {path}")


if __name__ == "__main__":
    main()
