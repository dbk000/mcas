#!/usr/bin/env python3
"""
Hetzner Cloud API CPU Metrics Fetcher (Secondary Path)
=======================================================
Fetches CPU metrics via the Hetzner Cloud API for historical views
and cross-verification against the on-VPS collector.

Usage:
  python hetzner_cpu_api.py --server-id 12345 --token YOUR_TOKEN --last 1h
  python hetzner_cpu_api.py --server-id 12345 --token YOUR_TOKEN --last 24h
  python hetzner_cpu_api.py --server-id 12345 --token YOUR_TOKEN --start 2026-02-20T00:00:00Z --end 2026-02-20T06:00:00Z

Environment variables (alternative to CLI args):
  HETZNER_API_TOKEN   - Your Hetzner Cloud API token
  HETZNER_SERVER_ID   - Your server ID

API Endpoint: GET /v1/servers/{id}/metrics?type=cpu&start=...&end=...&step=...
Granularity: ~60 seconds minimum
Rate limits: 3600 requests/hour per token
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    # Fallback to urllib if requests not available
    import urllib.request
    import urllib.error

    class requests:
        @staticmethod
        def get(url, headers=None, params=None, timeout=None):
            if params:
                query = "&".join(f"{k}={v}" for k, v in params.items())
                url = f"{url}?{query}"
            req = urllib.request.Request(url, headers=headers or {})
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
                class Response:
                    status_code = resp.status
                    text = resp.read().decode()
                    def json(self):
                        return json.loads(self.text)
                    def raise_for_status(self):
                        if self.status_code >= 400:
                            raise Exception(f"HTTP {self.status_code}")
                return Response()
            except urllib.error.HTTPError as e:
                class ErrResponse:
                    status_code = e.code
                    text = e.read().decode()
                    def json(self):
                        return json.loads(self.text)
                    def raise_for_status(self):
                        raise Exception(f"HTTP {self.status_code}: {self.text}")
                return ErrResponse()


API_BASE = "https://api.hetzner.cloud/v1"

DURATION_MAP = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def fetch_cpu_metrics(server_id, token, start, end, step=None):
    """Fetch CPU metrics from Hetzner Cloud API."""
    url = f"{API_BASE}/servers/{server_id}/metrics"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "type": "cpu",
        "start": start.isoformat() if isinstance(start, datetime) else start,
        "end": end.isoformat() if isinstance(end, datetime) else end,
    }
    if step:
        params["step"] = str(step)

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_metrics_response(data):
    """Parse Hetzner metrics response into a usable format."""
    metrics = data.get("metrics", {})
    timeseries = metrics.get("timeseries", {})

    result = {
        "start": metrics.get("start"),
        "end": metrics.get("end"),
        "step": metrics.get("step"),
        "series": {},
    }

    for series_name, series_data in timeseries.items():
        values = series_data.get("values", [])
        parsed = []
        for ts, val in values:
            parsed.append({
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "epoch": ts,
                "value": float(val),
            })
        result["series"][series_name] = parsed

    # Compute summary statistics if we have data
    for series_name, points in result["series"].items():
        if points:
            values = [p["value"] for p in points]
            result[f"{series_name}_summary"] = {
                "samples": len(values),
                "mean": round(sum(values) / len(values), 4),
                "max": round(max(values), 4),
                "min": round(min(values), 4),
            }

    return result


def main():
    parser = argparse.ArgumentParser(description="Fetch Hetzner Cloud CPU Metrics")
    parser.add_argument("--server-id", default=os.getenv("HETZNER_SERVER_ID"),
                        help="Hetzner server ID")
    parser.add_argument("--token", default=os.getenv("HETZNER_API_TOKEN"),
                        help="Hetzner API token")
    parser.add_argument("--last", default="1h",
                        help="Time window: 5m, 15m, 30m, 1h, 6h, 12h, 24h, 7d, 30d")
    parser.add_argument("--start", help="Explicit start time (ISO 8601)")
    parser.add_argument("--end", help="Explicit end time (ISO 8601)")
    parser.add_argument("--step", type=int, help="Step in seconds")

    args = parser.parse_args()

    if not args.server_id:
        print(json.dumps({"error": "No server ID. Set --server-id or HETZNER_SERVER_ID env var."}))
        sys.exit(1)
    if not args.token:
        print(json.dumps({"error": "No API token. Set --token or HETZNER_API_TOKEN env var."}))
        sys.exit(1)

    now = datetime.now(timezone.utc)
    if args.start and args.end:
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
    elif args.last in DURATION_MAP:
        end = now
        start = now - DURATION_MAP[args.last]
    else:
        print(json.dumps({"error": f"Unknown duration: {args.last}. Use: {list(DURATION_MAP.keys())}"}))
        sys.exit(1)

    try:
        raw = fetch_cpu_metrics(args.server_id, args.token, start, end, args.step)
        result = parse_metrics_response(raw)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
