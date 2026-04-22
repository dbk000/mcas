"""Shared HTTP client for Phase 0 remediation.

Browser-like UA (per §8), per-host throttle, retry on transient errors,
automatic redirect follow, content-preserving GET. Used by sponsor_discovery,
batch_enrich, and content_verify — one source of truth for HTTP behavior.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA_CHROME = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
UA_FIREFOX = (
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
)

DEFAULT_HEADERS = {
    "User-Agent": UA_CHROME,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "application/json,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # avoid brotli (decoder issues)
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

SEC_HOSTS = {"www.sec.gov", "sec.gov", "efts.sec.gov", "data.sec.gov"}


class HostThrottle:
    """Per-host token-bucket throttle. SEC: 5rps, others: default 5rps."""

    def __init__(self, default_interval: float = 0.20, sec_interval: float = 0.25):
        self.default_interval = default_interval
        self.sec_interval = sec_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _interval_for(self, host: str) -> float:
        if host in SEC_HOSTS:
            return self.sec_interval
        return self.default_interval

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if not host:
            return
        min_gap = self._interval_for(host)
        while True:
            with self._lock:
                now = time.monotonic()
                last = self._last.get(host, 0.0)
                elapsed = now - last
                if elapsed >= min_gap:
                    self._last[host] = now
                    return
                sleep_for = min_gap - elapsed
            time.sleep(sleep_for + random.uniform(0.0, 0.05))


_throttle = HostThrottle()


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    retries = Retry(
        total=3,
        backoff_factor=0.7,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(("GET", "HEAD")),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=8, pool_maxsize=16)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def fetch(
    session: requests.Session,
    url: str,
    *,
    timeout: float = 25.0,
    allow_redirects: bool = True,
    max_bytes: int = 25 * 1024 * 1024,  # 25 MB hard cap per response
    referer: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    throttle: bool = True,
) -> tuple[int, str, bytes, str]:
    """GET with throttle + redirect follow + byte cap.
    Returns: (status_code, final_url, body_bytes, content_type).
    On exception: (0, url, b"", str(exc)).
    """
    if throttle:
        _throttle.wait(url)
    headers = {}
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    try:
        r = session.get(url, timeout=timeout, allow_redirects=allow_redirects,
                        headers=headers, stream=True)
        buf = bytearray()
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    break
        ct = r.headers.get("content-type", "")
        return r.status_code, r.url, bytes(buf), ct
    except Exception as e:
        return 0, url, b"", f"{type(e).__name__}: {e}"
