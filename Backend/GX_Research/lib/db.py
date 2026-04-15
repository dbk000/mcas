"""
GX Research — OHLCV loader + parquet cache.

Reads from production ohlcv table. USDC universe symbols are translated to USDT
for data fetch (user decision 2026-04-14: development uses USDT history, USDC
transportability checked in Stage F). Logical symbol name is preserved in outputs.

No imports from Backend/Playbook, governance, or core_logic.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

# Execution-context path resolution (root CLAUDE.md §2 preflight item 4).
SANDBOX_ROOT = Path("/app/GX_Research") if os.path.isdir("/app/GX_Research") else Path(__file__).resolve().parent.parent
CACHE_DIR = SANDBOX_ROOT / "output" / "ohlcv_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DB_URL = os.environ.get("DATABASE_URL", "postgresql://trading:trading@postgres:5432/trading")

SUPPORTED_TFS = ("5m", "15m", "1h", "4h", "1d")
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def logical_to_data_symbol(symbol: str) -> str:
    """USDC research universe -> USDT data source. Preserves non-USDC symbols verbatim."""
    if symbol.endswith("USDC"):
        return symbol[:-4] + "USDT"
    return symbol


def load_universe(path: str | Path) -> list[str]:
    path = Path(path)
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def _cache_path(data_symbol: str, tf: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"{data_symbol}_{tf}_{start}_{end}.parquet"


def load_ohlcv(
    symbol: str,
    timeframe: str,
    start_ts: str,
    end_ts: str,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Load OHLCV for (symbol, timeframe) in [start_ts, end_ts) as a DataFrame.
    Columns: bar_start_ts (UTC tz-aware), open, high, low, close, volume.
    """
    data_symbol = logical_to_data_symbol(symbol)
    cache = _cache_path(data_symbol, timeframe, start_ts[:10], end_ts[:10])
    if use_cache and cache.exists():
        return pd.read_parquet(cache)

    sql = """
        SELECT bar_start_ts, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = %s AND timeframe = %s
          AND bar_start_ts >= %s AND bar_start_ts < %s
          AND is_final = TRUE
        ORDER BY bar_start_ts ASC
    """
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (data_symbol, timeframe, start_ts, end_ts))
            rows = cur.fetchall()

    if not rows:
        df = pd.DataFrame(columns=["bar_start_ts", "open", "high", "low", "close", "volume"])
    else:
        df = pd.DataFrame(rows, columns=["bar_start_ts", "open", "high", "low", "close", "volume"])
        df["bar_start_ts"] = pd.to_datetime(df["bar_start_ts"], utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(np.float64)

    if use_cache:
        df.to_parquet(cache, index=False)
    return df


def load_universe_ohlcv(
    universe: list[str],
    timeframe: str,
    start_ts: str,
    end_ts: str,
    use_cache: bool = True,
    progress_every: int = 40,
    max_workers: int = 12,
) -> dict[str, pd.DataFrame]:
    """Load OHLCV for every symbol in universe in parallel via threads (I/O bound)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(load_ohlcv, sym, timeframe, start_ts, end_ts, use_cache): sym for sym in universe}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                df = fut.result()
            except Exception as exc:
                print(f"  [db] {sym}: FAILED ({exc})", flush=True)
                df = pd.DataFrame()
            if not df.empty:
                out[sym] = df
            done += 1
            if done % progress_every == 0:
                elapsed = time.time() - t0
                print(f"  [db] {timeframe} loaded {done}/{len(universe)} symbols ({elapsed:.1f}s elapsed)", flush=True)
    return out


def median_24h_quote_volume_usd(
    universe: list[str], start_ts: str, end_ts: str
) -> dict[str, float]:
    """Median 24h quote volume (USD) per symbol over the research window. Used for tier assignment."""
    out: dict[str, float] = {}
    sql = """
        SELECT symbol, percentile_cont(0.5) WITHIN GROUP (ORDER BY volume * close) AS median_qv
        FROM ohlcv
        WHERE symbol = ANY(%s) AND timeframe = '1d'
          AND bar_start_ts >= %s AND bar_start_ts < %s
          AND is_final = TRUE
        GROUP BY symbol
    """
    data_symbols = [logical_to_data_symbol(s) for s in universe]
    rev_map = {logical_to_data_symbol(s): s for s in universe}
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (data_symbols, start_ts, end_ts))
            for row in cur.fetchall():
                data_sym, med_qv = row
                out[rev_map.get(data_sym, data_sym)] = float(med_qv) if med_qv is not None else 0.0
    for s in universe:
        out.setdefault(s, 0.0)
    return out


@dataclass
class OhlcvBundle:
    """In-memory OHLCV cache for a given timeframe across the universe."""
    timeframe: str
    start_ts: str
    end_ts: str
    frames: dict[str, pd.DataFrame]

    def symbols(self) -> list[str]:
        return sorted(self.frames.keys())

    def mem_mb(self) -> float:
        return sum(df.memory_usage(deep=True).sum() for df in self.frames.values()) / 1e6


def build_bundle(universe: list[str], timeframe: str, start_ts: str, end_ts: str) -> OhlcvBundle:
    frames = load_universe_ohlcv(universe, timeframe, start_ts, end_ts)
    return OhlcvBundle(timeframe=timeframe, start_ts=start_ts, end_ts=end_ts, frames=frames)
