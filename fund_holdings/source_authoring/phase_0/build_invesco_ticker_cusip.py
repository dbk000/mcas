"""Build ticker→CUSIP map for all Invesco excluded funds by querying their
product/search API. Writes to invesco_ticker_cusip.json.
"""
import json, sys, time, urllib.parse
from pathlib import Path
from curl_cffi import requests as cc_requests

sys.path.insert(0, str(Path(__file__).parent))
from sponsor_discovery_v2 import IMPERSONATE

PHASE0 = Path(__file__).parent


def invesco_ticker_to_cusip(session, ticker: str) -> str | None:
    q = urllib.parse.quote(f"ticker:{ticker}")
    url = f"https://dng-api.invesco.com/product/search?q={q}&wt=json&rows=5"
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


def main():
    import yaml
    with open(PHASE0 / "excluded_funds.yaml") as f:
        data = yaml.safe_load(f)
    invesco_tickers = sorted({r["ticker"] for r in data["records"] if r["sheet"] == "invesco"})
    print(f"Invesco excluded: {len(invesco_tickers)} tickers")

    session = cc_requests.Session(impersonate=IMPERSONATE)
    session.headers.update({
        "Origin": "https://www.invesco.com",
        "Referer": "https://www.invesco.com/us/financial-products/etfs",
    })

    mapping: dict[str, str] = {}
    failed = []
    t0 = time.monotonic()
    for i, tk in enumerate(invesco_tickers):
        cusip = invesco_ticker_to_cusip(session, tk)
        if cusip:
            mapping[tk] = cusip
        else:
            failed.append(tk)
        if (i + 1) % 20 == 0:
            elapsed = time.monotonic() - t0
            print(f"  [{i+1}/{len(invesco_tickers)}] mapped={len(mapping)} failed={len(failed)} "
                  f"elapsed={elapsed:.0f}s")
        time.sleep(0.15)  # rate limit

    print(f"\nMapped: {len(mapping)}/{len(invesco_tickers)}")
    print(f"Failed: {failed[:10]}{'...' if len(failed) > 10 else ''}")

    out = {
        "mapping": mapping,
        "failed_tickers": failed,
    }
    path = PHASE0 / "invesco_ticker_cusip.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nWrote: {path}")


if __name__ == "__main__":
    main()
