"""
OHLCV Data Layer
================

Data ingestion and retrieval for backtesting.

This layer:
1. Ingests OHLCV data from Binance
2. Stores it in Postgres with versioning
3. Provides query interface for backtests
4. Creates data snapshots for reproducibility

> "Without data versioning, backtests drift silently over time."
"""

import os
import time
import json
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional, Generator
from contextlib import contextmanager

import requests
import psycopg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Standalone DB connection helper (mirrors worker.py get_conn pattern)
# Used by module-level functions like log_signal_event().
# ---------------------------------------------------------------------------
_DATABASE_URL: str | None = os.getenv("DATABASE_URL")


@contextmanager
def get_conn():
    """Get a database connection using DATABASE_URL env var."""
    conn = psycopg.connect(_DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def log_signal_event(
    event_type: str,
    symbol: str,
    timeframe: str,
    setup_id: str,
    direction: str | None,
    bar_start_ts: datetime,
    setup_score: float | None = None,
    rank: int | None = None,
    feature_snapshot: dict | None = None,
    score_components: dict | None = None,
    reason_codes: list[str] | None = None,
    trade_id: str | None = None,
    run_id: str | None = None,
):
    """Log a signal event to signal_events table (Canon \u00a71.9)."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO signal_events (
                        event_type, symbol, timeframe, setup_id, direction,
                        bar_start_ts, as_of_ts,
                        setup_score, rank, feature_snapshot, score_components,
                        reason_codes, trade_id, run_id
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, NOW(),
                        %s, %s, %s, %s,
                        %s, %s, %s
                    )
                """, (
                    event_type, symbol, timeframe, setup_id, direction,
                    bar_start_ts,
                    setup_score, rank,
                    json.dumps(feature_snapshot) if feature_snapshot else None,
                    json.dumps(score_components) if score_components else None,
                    reason_codes,
                    trade_id, run_id,
                ))
            conn.commit()
    except Exception as e:
        logger.warning("signal_event log failed: %s", e)


@dataclass
class OHLCV:
    """Single OHLCV bar (Canon 4A compliant)."""
    symbol: str
    timeframe: str
    bar_start_ts: datetime       # bar_start_ts (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float
    as_of_ts: Optional[datetime] = None   # When data was last refreshed
    is_final: bool = True                  # Bar closed (Canon 8: anti-lookahead)

    def to_tuple(self) -> tuple:
        return (
            self.symbol, self.timeframe, self.bar_start_ts,
            self.open, self.high, self.low, self.close, self.volume,
            self.as_of_ts or datetime.now(timezone.utc),
            self.is_final,
        )


@dataclass
class DataSnapshot:
    """A frozen snapshot of data for reproducible backtests."""
    id: str
    snapshot_id: str
    created_at: datetime
    symbols: list[str]
    timeframes: list[str]
    start_ts: datetime
    end_ts: datetime
    bar_count: int
    checksum: Optional[str] = None


class OHLCVRepository:
    """
    Repository for OHLCV data storage and retrieval.
    
    Usage:
        repo = OHLCVRepository(database_url)
        
        # Ingest data
        repo.ingest_from_binance("BTCUSDT", "1h", days_back=365)
        
        # Query data
        bars = repo.get_bars("BTCUSDT", "1h", start, end)
        
        # Create snapshot
        snapshot = repo.create_snapshot(["BTCUSDT"], ["1h"], start, end)
    """
    
    def __init__(self, database_url: str):
        self.database_url = database_url
    
    @contextmanager
    def _conn(self):
        """Get database connection."""
        conn = psycopg.connect(self.database_url)
        try:
            yield conn
        finally:
            conn.close()
    
    def ensure_schema(self):
        """
        Ensure pgcrypto extension exists.

        Table DDL is governed by Backend/docs/db/schema/mcas_schema.sql
        (Canon §10: single source of truth for DDL).
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            conn.commit()
        logger.info("OHLCV schema ensured")
    
    # =========================================================================
    # Ingestion
    # =========================================================================
    
    def ingest_from_binance(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days_back: int = 365,
        batch_id: Optional[str] = None,
    ) -> int:
        """
        Ingest OHLCV data from Binance.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            timeframe: Candle interval (e.g., "1h", "4h", "1d")
            start: Start time (defaults to days_back from now)
            end: End time (defaults to now)
            days_back: Days to fetch if start not specified
            batch_id: Optional batch identifier for versioning
        
        Returns:
            Number of bars ingested
        """
        if not start:
            start = datetime.now(timezone.utc) - timedelta(days=days_back)
        if not end:
            end = datetime.now(timezone.utc)
        
        batch_id = batch_id or f"ingest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        
        logger.info(f"Ingesting {symbol} {timeframe} from {start} to {end}")
        
        bars_ingested = 0
        current_start = start
        
        while current_start < end:
            # Fetch batch from Binance (max 1000 bars)
            bars = self._fetch_binance_klines(
                symbol, timeframe,
                start_time=int(current_start.timestamp() * 1000),
                end_time=int(end.timestamp() * 1000),
                limit=1000,
            )
            
            if not bars:
                break
            
            # Insert into database
            self._insert_bars(bars, batch_id)
            bars_ingested += len(bars)
            
            # Move start forward
            last_bar = bars[-1]
            current_start = last_bar.bar_start_ts + self._timeframe_to_delta(timeframe)
            
            # Rate limiting
            time.sleep(0.2)
            
            logger.debug(f"Ingested {len(bars)} bars, total: {bars_ingested}")
        
        logger.info(f"Completed ingestion: {bars_ingested} bars for {symbol} {timeframe}")
        return bars_ingested
    
    def _fetch_binance_klines(
        self,
        symbol: str,
        timeframe: str,
        start_time: int,
        end_time: int,
        limit: int = 1000,
    ) -> list[OHLCV]:
        """Fetch klines from Binance API."""
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": timeframe,
            "startTime": start_time,
            "endTime": end_time,
            "limit": limit,
        }
        
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code in (418, 429):
                logger.warning("Binance rate limit hit (data layer)", extra={
                    "event": "rate_limit", "endpoint": "klines",
                    "symbol": symbol, "timeframe": timeframe,
                    "http_status": resp.status_code,
                    "retry_after": resp.headers.get("Retry-After", "unknown"),
                })
                return []
            resp.raise_for_status()
            data = resp.json()

            return [
                OHLCV(
                    symbol=symbol,
                    timeframe=timeframe,
                    bar_start_ts=datetime.fromtimestamp(bar[0] / 1000, tz=timezone.utc),
                    open=float(bar[1]),
                    high=float(bar[2]),
                    low=float(bar[3]),
                    close=float(bar[4]),
                    volume=float(bar[5]),
                )
                for bar in data
            ]
        except requests.exceptions.Timeout:
            logger.warning("Binance klines fetch timeout (data layer)", extra={
                "event": "timeout", "endpoint": "klines",
                "symbol": symbol, "timeframe": timeframe,
                "timeout_s": 30,
            })
            return []
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            logger.error("Binance API error (data layer)", extra={
                "event": "api_error", "endpoint": "klines",
                "symbol": symbol, "timeframe": timeframe,
                "http_status": status,
                "error": str(e),
            })
            return []
    
    def _insert_bars(self, bars: list[OHLCV], batch_id: str):
        """Insert bars into database with upsert."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO ohlcv (
                        symbol, timeframe, bar_start_ts,
                        open, high, low, close, volume,
                        as_of_ts, is_final,
                        source, batch_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, timeframe, bar_start_ts, source)
                    DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        as_of_ts = EXCLUDED.as_of_ts,
                        is_final = EXCLUDED.is_final,
                        batch_id = EXCLUDED.batch_id,
                        ingested_at = NOW();
                """, [
                    (*bar.to_tuple(), 'binance', batch_id)
                    for bar in bars
                ])
            conn.commit()
    
    def _timeframe_to_delta(self, tf: str) -> timedelta:
        """Convert timeframe string to timedelta."""
        mapping = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
        }
        return mapping.get(tf, timedelta(hours=1))
    
    # =========================================================================
    # Retrieval
    # =========================================================================
    
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        final_only: bool = False,
    ) -> list[OHLCV]:
        """
        Get OHLCV bars for a symbol/timeframe/range.

        Args:
            final_only: If True, filter to is_final=TRUE only (Canon 8: anti-lookahead).

        Returns bars in chronological order.
        """
        final_clause = "AND is_final = TRUE" if final_only else ""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT symbol, timeframe, bar_start_ts,
                           open, high, low, close, volume,
                           as_of_ts, is_final
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = %s
                    AND bar_start_ts >= %s AND bar_start_ts <= %s
                    {final_clause}
                    ORDER BY bar_start_ts;
                """, (symbol, timeframe, start, end))
                rows = cur.fetchall()

        return [
            OHLCV(
                symbol=row[0],
                timeframe=row[1],
                bar_start_ts=row[2],
                open=row[3],
                high=row[4],
                low=row[5],
                close=row[6],
                volume=row[7],
                as_of_ts=row[8],
                is_final=row[9],
            )
            for row in rows
        ]
    
    def get_bars_generator(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        chunk_size: int = 10000,
    ) -> Generator[OHLCV, None, None]:
        """
        Generator for large data ranges (memory efficient).
        
        Yields bars one at a time.
        """
        offset = 0
        
        while True:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT symbol, timeframe, bar_start_ts,
                               open, high, low, close, volume
                        FROM ohlcv
                        WHERE symbol = %s AND timeframe = %s
                        AND bar_start_ts >= %s AND bar_start_ts <= %s
                        ORDER BY bar_start_ts
                        LIMIT %s OFFSET %s;
                    """, (symbol, timeframe, start, end, chunk_size, offset))
                    rows = cur.fetchall()
            
            if not rows:
                break
            
            for row in rows:
                yield OHLCV(
                    symbol=row[0],
                    timeframe=row[1],
                    bar_start_ts=row[2],
                    open=row[3],
                    high=row[4],
                    low=row[5],
                    close=row[6],
                    volume=row[7],
                )
            
            offset += chunk_size
    
    def get_latest_bar(self, symbol: str, timeframe: str) -> Optional[OHLCV]:
        """Get the most recent bar."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT symbol, timeframe, bar_start_ts,
                           open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY bar_start_ts DESC
                    LIMIT 1;
                """, (symbol, timeframe))
                row = cur.fetchone()
        
        if not row:
            return None
        
        return OHLCV(
            symbol=row[0],
            timeframe=row[1],
            bar_start_ts=row[2],
            open=row[3],
            high=row[4],
            low=row[5],
            close=row[6],
            volume=row[7],
        )
    
    def get_bar_count(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        """Get count of bars for a symbol/timeframe."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                if start and end:
                    cur.execute("""
                        SELECT COUNT(*) FROM ohlcv
                        WHERE symbol = %s AND timeframe = %s
                        AND bar_start_ts >= %s AND bar_start_ts <= %s;
                    """, (symbol, timeframe, start, end))
                else:
                    cur.execute("""
                        SELECT COUNT(*) FROM ohlcv
                        WHERE symbol = %s AND timeframe = %s;
                    """, (symbol, timeframe))
                return cur.fetchone()[0]
    
    def get_data_range(
        self,
        symbol: str,
        timeframe: str,
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        """Get the date range of available data."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT MIN(bar_start_ts), MAX(bar_start_ts)
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = %s;
                """, (symbol, timeframe))
                row = cur.fetchone()
        
        return (row[0], row[1]) if row else (None, None)
    
    # =========================================================================
    # Snapshots
    # =========================================================================
    
    def create_snapshot(
        self,
        symbols: list[str],
        timeframes: list[str],
        start: datetime,
        end: datetime,
        description: Optional[str] = None,
    ) -> DataSnapshot:
        """
        Create a data snapshot for reproducible backtests.
        
        The snapshot records:
        - Which symbols/timeframes
        - Date range
        - Total bar count
        - Checksum of the data
        
        Backtests reference this snapshot_id to ensure they're using
        the same data as the original run.
        """
        # Count total bars
        total_bars = 0
        for symbol in symbols:
            for tf in timeframes:
                total_bars += self.get_bar_count(symbol, tf, start, end)
        
        # Compute checksum (hash of bar timestamps + closes)
        checksum = self._compute_checksum(symbols, timeframes, start, end)
        
        # Generate snapshot ID
        snapshot_id = f"snap_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{checksum[:8]}"
        
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO data_snapshots (
                        snapshot_id, description,
                        symbols_json, timeframes_json,
                        start_ts, end_ts, bar_count, checksum
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at;
                """, (
                    snapshot_id, description,
                    json.dumps(sorted(symbols)), json.dumps(sorted(timeframes)),
                    start, end, total_bars, checksum,
                ))
                row = cur.fetchone()
            conn.commit()
        
        logger.info(f"Created snapshot {snapshot_id}: {total_bars} bars, checksum={checksum[:16]}")
        
        return DataSnapshot(
            id=str(row[0]),
            snapshot_id=snapshot_id,
            created_at=row[1],
            symbols=symbols,
            timeframes=timeframes,
            start_ts=start,
            end_ts=end,
            bar_count=total_bars,
            checksum=checksum,
        )
    
    def _compute_checksum(
        self,
        symbols: list[str],
        timeframes: list[str],
        start: datetime,
        end: datetime,
    ) -> str:
        """Compute checksum of data for verification."""
        h = hashlib.sha256()
        
        for symbol in sorted(symbols):
            for tf in sorted(timeframes):
                with self._conn() as conn:
                    with conn.cursor() as cur:
                        # Hash timestamps + closes
                        cur.execute("""
                            SELECT bar_start_ts, close FROM ohlcv
                            WHERE symbol = %s AND timeframe = %s
                            AND bar_start_ts >= %s AND bar_start_ts <= %s
                            ORDER BY bar_start_ts;
                        """, (symbol, tf, start, end))
                        for row in cur:
                            h.update(f"{row[0].isoformat()}:{row[1]}".encode())
        
        return h.hexdigest()
    
    def get_snapshot(self, snapshot_id: str) -> Optional[DataSnapshot]:
        """Get a snapshot by ID."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, snapshot_id, created_at, description,
                           symbols_json, timeframes_json,
                           start_ts, end_ts, bar_count, checksum
                    FROM data_snapshots
                    WHERE snapshot_id = %s;
                """, (snapshot_id,))
                row = cur.fetchone()
        
        if not row:
            return None
        
        return DataSnapshot(
            id=str(row[0]),
            snapshot_id=row[1],
            created_at=row[2],
            symbols=json.loads(row[4]),
            timeframes=json.loads(row[5]),
            start_ts=row[6],
            end_ts=row[7],
            bar_count=row[8],
            checksum=row[9],
        )
    
    def verify_snapshot(self, snapshot_id: str) -> bool:
        """
        Verify a snapshot's data hasn't changed.
        
        Recomputes checksum and compares to stored value.
        """
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot:
            return False
        
        current_checksum = self._compute_checksum(
            snapshot.symbols,
            snapshot.timeframes,
            snapshot.start_ts,
            snapshot.end_ts,
        )
        
        return current_checksum == snapshot.checksum
    
    # =========================================================================
    # Stats
    # =========================================================================
    
    def get_stats(self) -> dict:
        """Get overall data statistics."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Total bars
                cur.execute("SELECT COUNT(*) FROM ohlcv;")
                total_bars = cur.fetchone()[0]
                
                # Distinct symbols
                cur.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv;")
                total_symbols = cur.fetchone()[0]
                
                # Date range
                cur.execute("SELECT MIN(bar_start_ts), MAX(bar_start_ts) FROM ohlcv;")
                date_range = cur.fetchone()
                
                # By timeframe
                cur.execute("""
                    SELECT timeframe, COUNT(*) 
                    FROM ohlcv 
                    GROUP BY timeframe 
                    ORDER BY timeframe;
                """)
                by_tf = {row[0]: row[1] for row in cur.fetchall()}
        
        return {
            "total_bars": total_bars,
            "total_symbols": total_symbols,
            "min_date": date_range[0].isoformat() if date_range[0] else None,
            "max_date": date_range[1].isoformat() if date_range[1] else None,
            "by_timeframe": by_tf,
        }
