"""
Parquet writer for sweep cells. Every cell (winner or loser) persisted per plan §2.1.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SANDBOX_ROOT = Path("/app/GX_Research") if os.path.isdir("/app/GX_Research") else Path(__file__).resolve().parent.parent
SWEEP_DIR = SANDBOX_ROOT / "output" / "sweep_cells"
SWEEP_DIR.mkdir(parents=True, exist_ok=True)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def config_hash_of(cfg: dict) -> str:
    """16-char SHA-256 prefix of deterministic JSON. Matches root CLAUDE.md §3.4a."""
    js = json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(js.encode("utf-8")).hexdigest()[:16]


def write_sweep_cells(stage: str, batch_id: str, rows: list[dict], shard: str = "") -> Path:
    """Write a shard of sweep-cell rows. Stage can be sharded (per-TF, per-parent) for
    incremental flush — `read_stage_cells` globs all shards back together."""
    if not rows:
        return SWEEP_DIR / f"{stage}_{batch_id}_EMPTY.parquet"
    df = pd.DataFrame(rows)
    shard_token = f"_{shard}" if shard else ""
    path = SWEEP_DIR / f"{stage}_{batch_id}{shard_token}_{utc_stamp()}.parquet"
    df.to_parquet(path, index=False)
    return path


def write_trades(cell_id: str, batch_id: str, stage: str, trades) -> Path | None:
    """Accepts either list[dict] or a pandas DataFrame. Empty input returns None."""
    if trades is None:
        return None
    if isinstance(trades, pd.DataFrame):
        if trades.empty:
            return None
        df = trades
    else:
        if not trades:
            return None
        df = pd.DataFrame(trades)
    d = SANDBOX_ROOT / "output" / "trades" / stage / batch_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{cell_id}.parquet"
    df.to_parquet(p, index=False)
    return p


def read_stage_cells(stage: str, batch_id: str) -> pd.DataFrame:
    """Load all sweep-cell parquets for a stage+batch."""
    matches = sorted(SWEEP_DIR.glob(f"{stage}_{batch_id}_*.parquet"))
    if not matches:
        return pd.DataFrame()
    dfs = [pd.read_parquet(p) for p in matches]
    return pd.concat(dfs, ignore_index=True)
