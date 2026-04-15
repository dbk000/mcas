"""Shared helpers for Stage scripts."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Make parent importable whether we're in Docker (/app/GX_Research) or host.
_HERE = Path(__file__).resolve().parent
_SANDBOX = _HERE.parent
if str(_SANDBOX.parent) not in sys.path:
    sys.path.insert(0, str(_SANDBOX.parent))


SANDBOX_ROOT = _SANDBOX
UNIVERSE_PATH = SANDBOX_ROOT / "universe.txt"
LOG_DIR = SANDBOX_ROOT / "output" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_DIR = SANDBOX_ROOT / "output" / "progress"
PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

# Research window & holdout (plan §0 header).
RESEARCH_START = os.environ.get("GX_RESEARCH_START", "2025-01-01T00:00:00Z")
RESEARCH_END = os.environ.get("GX_RESEARCH_END", "2026-04-14T00:00:00Z")
HOLDOUT_FRAC = float(os.environ.get("GX_HOLDOUT_FRAC", "0.15"))

# Timeframes for Stage-A sweep. Plan §3 mandates 5m, 15m, 1h, 4h, 1d — the
# two short TFs carry ~83M of the ~90M total bars and are where n>2000 gates
# are achievable. Overriding to a subset is for smoke tests only.
_tfs_env = os.environ.get("GX_TIMEFRAMES", "5m,15m,1h,4h,1d")
TIMEFRAMES = tuple(t.strip() for t in _tfs_env.split(",") if t.strip())

BATCH_ID = os.environ.get("GX_BATCH_ID", time.strftime("gx_%Y%m%dT%H%M%SZ", time.gmtime()))


def log(msg: str):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[{ts}] {msg}", flush=True)


def log_file(stage: str) -> Path:
    return LOG_DIR / f"{stage}_{BATCH_ID}.log"


def write_progress(stage: str, batch_id: str, data: dict) -> Path:
    """Crash-survivable progress marker. Overwrites on each call — latest wins."""
    import json
    p = PROGRESS_DIR / f"{stage}_{batch_id}.json"
    payload = {"stage": stage, "batch_id": batch_id,
               "utc_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **data}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(p)
    return p
