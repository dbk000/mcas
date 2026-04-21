"""format_validator — HTTP HEAD sweep over every URL in the registry.

Writes registry/broken_urls.yaml with entries:
    - source_id
    - url
    - status_code   (int or null)
    - error_class   (str or null)  — connection error class name
    - first_seen_at (UTC ISO)
    - last_checked_at (UTC ISO)

Good URLs update the source entry's lineage.verified_by / verified_at in-place.
This runs as a separate step from authoring because it requires network access
and is rate-limited.

Usage:
    python3 format_validator.py                 # full sweep
    python3 format_validator.py --only universal.yaml
    python3 format_validator.py --max-concurrency 4
    python3 format_validator.py --timeout 10
    python3 format_validator.py --dry-run
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import (
    NEWS_SOURCES_DIR,
    REGISTRY_DIR,
    dump_yaml,
    load_yaml,
    utc_now_iso,
)


BROKEN_PATH = REGISTRY_DIR / "broken_urls.yaml"


def _check_url(url: str, timeout: float, user_agent: str) -> tuple[Optional[int], Optional[str]]:
    """Returns (status_code or None, error_class_name or None). Never raises."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except urllib.error.URLError as e:
        return None, type(e).__name__
    except TimeoutError:
        return None, "TimeoutError"
    except Exception as e:
        return None, type(e).__name__


def _is_ok(status: Optional[int]) -> bool:
    if status is None:
        return False
    # 2xx, 3xx, 401/403 (auth-walled but live) all count as live.
    if 200 <= status < 400:
        return True
    if status in (401, 403, 405):
        return True
    return False


def _load_broken() -> dict[str, dict]:
    if not BROKEN_PATH.exists():
        return {}
    raw = load_yaml(BROKEN_PATH) or []
    return {e["source_id"]: e for e in raw if isinstance(e, dict)}


def _save_broken(broken: dict[str, dict]) -> None:
    out = sorted(broken.values(), key=lambda e: e["source_id"])
    dump_yaml(BROKEN_PATH, out)


def _iter_files(only_pattern: str | None):
    if only_pattern:
        for p in NEWS_SOURCES_DIR.rglob(only_pattern):
            if p.is_file():
                yield p
    else:
        for p in sorted(NEWS_SOURCES_DIR.rglob("*.yaml")):
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="glob pattern within registry/news_sources, e.g. universal.yaml")
    ap.add_argument("--max-concurrency", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--user-agent", default="MCAS-RegistryValidator/1.0 (+contact: darren.kenny7@gmail.com)")
    ap.add_argument("--dry-run", action="store_true", help="report only, do not update broken_urls or lineage")
    args = ap.parse_args()

    files = list(_iter_files(args.only))
    if not files:
        print("no files matched", file=sys.stderr)
        return 1

    broken = _load_broken()
    now = utc_now_iso()

    tasks: list[tuple[Path, dict, int]] = []   # (file, entry, idx)
    urls: list[tuple[str, str]] = []           # (source_id, url)
    file_cache: dict[Path, list] = {}
    for p in files:
        entries = load_yaml(p) or []
        file_cache[p] = entries
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            url = e.get("url")
            sid = e.get("source_id")
            if isinstance(url, str) and isinstance(sid, str):
                tasks.append((p, e, i))
                urls.append((sid, url))

    print(f"checking {len(urls)} URLs across {len(files)} files")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_concurrency) as pool:
        futs = {
            pool.submit(_check_url, url, args.timeout, args.user_agent): (sid, url)
            for sid, url in urls
        }
        results: dict[str, tuple[Optional[int], Optional[str]]] = {}
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            sid, url = futs[fut]
            status, err = fut.result()
            results[sid] = (status, err)
            mark = "OK " if _is_ok(status) else "BAD"
            if i % 10 == 0 or i == len(futs):
                print(f"  [{i}/{len(futs)}] {mark} {status!s:>5}  {sid}")

    ok = sum(1 for s, _ in results.values() if _is_ok(s))
    bad = len(results) - ok
    print(f"\nsummary: ok={ok} bad={bad} total={len(results)}")

    if args.dry_run:
        return 0 if bad == 0 else 1

    # Update broken_urls.yaml and lineage.
    dirty_files: set[Path] = set()
    for p, entry, idx in tasks:
        sid = entry["source_id"]
        status, err = results[sid]
        if _is_ok(status):
            if sid in broken:
                del broken[sid]
            lineage = dict(entry.get("lineage") or {})
            lineage["verified_by"] = f"format_validator_head_{status}"
            lineage["verified_at"] = now
            entry["lineage"] = lineage
            dirty_files.add(p)
        else:
            existing = broken.get(sid) or {}
            broken[sid] = {
                "source_id": sid,
                "url": entry.get("url"),
                "status_code": status,
                "error_class": err,
                "first_seen_at": existing.get("first_seen_at") or now,
                "last_checked_at": now,
            }

    for p in dirty_files:
        dump_yaml(p, file_cache[p])
    _save_broken(broken)
    print(f"\nwrote broken_urls.yaml: {len(broken)} entries")
    print(f"updated lineage in {len(dirty_files)} files")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
