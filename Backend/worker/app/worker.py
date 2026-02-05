"""
MCAS Worker v3 - Queue-Driven Architecture
===========================================

The worker now:
1. Consumes runs from the queue (control plane driven)
2. Falls back to periodic scans if queue is empty
3. Handles SCAN, BACKTEST, and PAPER run types

"API enqueues, worker executes."

Queue consumption uses FOR UPDATE SKIP LOCKED for atomic claims,
preventing double-execution on retry or multi-worker scenarios.
"""

import os
import sys
import time
import json
import logging
import threading
from datetime import datetime, timezone
from contextlib import contextmanager

import psycopg
import redis

from json_logging import setup_logging, ctx_worker_id, ctx_run_id
from signal_engine import (
    SignalEngine,
    get_default_symbols,
    get_default_timeframes,
    fetch_top_symbols_by_volume,
)
from run_executor import RunExecutor
from runs.models import Run, RunType, RunStatus, UniverseSpec, StrategySpec, DataSpec
from runs.repository import RunRepository
from correlation.executor import CorrelationJobExecutor

# Configure structured JSON logging (must be before any logger usage)
setup_logging()
logger = logging.getLogger("worker")

# Environment
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL")
WORKER_ID = os.getenv("WORKER_ID", f"worker-{os.getpid()}")

# Worker config
QUEUE_POLL_SECONDS = 2
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
USE_DYNAMIC_SYMBOLS = os.getenv("USE_DYNAMIC_SYMBOLS", "false").lower() == "true"
SYMBOL_COUNT = int(os.getenv("SYMBOL_COUNT", "200"))
SETUP_IDS = os.getenv("SETUP_IDS", "S1,S2").split(",")
ENABLE_PERIODIC_SCANS = os.getenv("ENABLE_PERIODIC_SCANS", "true").lower() == "true"

# Redis client
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


@contextmanager
def get_conn():
    """Get database connection."""
    conn = psycopg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def heartbeat():
    """Record worker heartbeat in Redis."""
    r.hset(f"worker:{WORKER_ID}", mapping={
        "last_seen": str(int(time.time())),
        "status": "running",
        "pid": str(os.getpid()),
    })
    r.expire(f"worker:{WORKER_ID}", 120)  # 2 min TTL


def get_symbols() -> list[str]:
    """Get symbol universe based on configuration."""
    if USE_DYNAMIC_SYMBOLS:
        symbols = fetch_top_symbols_by_volume(n=SYMBOL_COUNT)
        logger.info(f"Using dynamic symbols: top {len(symbols)} by volume")
    else:
        symbols = get_default_symbols()[:SYMBOL_COUNT]
    return symbols


# =============================================================================
# Queue Consumer
# =============================================================================

def claim_next_run() -> tuple[str, str] | None:
    """
    Atomically claim the next queued run.
    
    Uses FOR UPDATE SKIP LOCKED to prevent double-claims
    in multi-worker scenarios.
    
    Returns:
        (run_id, run_type) if a run was claimed, None otherwise
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Atomically claim next queued run
            cur.execute("""
                UPDATE run_queue
                SET status = 'CLAIMED',
                    claimed_by = %s,
                    claimed_at = %s,
                    attempt_count = attempt_count + 1
                WHERE id = (
                    SELECT id FROM run_queue
                    WHERE status = 'QUEUED'
                    AND (attempt_count < max_attempts OR max_attempts IS NULL)
                    ORDER BY priority DESC, queued_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING run_id;
            """, (WORKER_ID, datetime.now(timezone.utc)))
            
            row = cur.fetchone()
            if not row:
                return None
            
            run_id = str(row[0])
            
            # Get run type
            cur.execute("SELECT run_type FROM runs WHERE id = %s;", (run_id,))
            run_row = cur.fetchone()
            run_type = run_row[0] if run_row else "UNKNOWN"
        
        conn.commit()
    
    logger.info("Run claimed", extra={"run_id": run_id, "run_type": run_type})
    return (run_id, run_type)


def complete_queued_run(run_id: str, success: bool, error: str = None):
    """Mark a queued run as completed or failed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if success:
                cur.execute("""
                    UPDATE run_queue
                    SET status = 'COMPLETED', completed_at = %s
                    WHERE run_id = %s;
                """, (datetime.now(timezone.utc), run_id))
            else:
                cur.execute("""
                    UPDATE run_queue
                    SET status = 'FAILED', completed_at = %s, last_error = %s
                    WHERE run_id = %s;
                """, (datetime.now(timezone.utc), error, run_id))
        conn.commit()


def execute_queued_run(executor: RunExecutor, run_id: str, run_type: str) -> bool:
    """
    Execute a queued run.
    
    Returns True on success, False on failure.
    """
    token = ctx_run_id.set(run_id)
    try:
        # Get the full run from database
        run = executor.repo.get_run(run_id)
        if not run:
            logger.error("Run not found", extra={"run_id": run_id})
            complete_queued_run(run_id, False, "Run not found")
            return False

        if run.status != RunStatus.PENDING:
            logger.warning("Run not in PENDING status", extra={
                "run_id": run_id, "current_status": str(run.status),
            })
            complete_queued_run(run_id, False, f"Invalid status: {run.status}")
            return False

        # Execute based on type
        start = time.time()
        logger.info("Executing run", extra={"run_id": run_id, "run_type": run_type})
        executor.execute(run)

        duration_s = round(time.time() - start, 2)
        complete_queued_run(run_id, True)
        logger.info("Run completed", extra={
            "run_id": run_id, "run_type": run_type, "duration_s": duration_s,
        })
        return True

    except Exception as e:
        logger.error("Run failed", extra={
            "run_id": run_id, "run_type": run_type, "error": str(e),
        }, exc_info=True)
        complete_queued_run(run_id, False, str(e))
        return False
    finally:
        ctx_run_id.reset(token)


# =============================================================================
# Periodic Scan Creator
# =============================================================================

def create_periodic_scan(executor: RunExecutor, symbols: list[str], timeframes: list[str]):
    """
    Create and queue a periodic scan run.
    
    This is the fallback when no external runs are queued.
    """
    run = executor.create_scan_run(
        symbols=symbols,
        timeframes=timeframes,
        setup_ids=SETUP_IDS,
        name=f"periodic_scan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
    )
    
    # Queue it (so it goes through the same execution path)
    with get_conn() as conn:
        with conn.cursor() as cur:
            import uuid
            cur.execute("""
                INSERT INTO run_queue (id, run_id, priority, queued_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING;
            """, (str(uuid.uuid4()), run.id, 0, datetime.now(timezone.utc)))
        conn.commit()
    
    logger.debug(f"Created periodic scan {run.id}")
    return run.id


# =============================================================================
# Redis Subscriber (for immediate notifications)
# =============================================================================

class QueueSubscriber(threading.Thread):
    """
    Background thread that listens to Redis pubsub for immediate run notifications.
    
    This allows the worker to wake up immediately when a run is enqueued,
    rather than waiting for the next poll cycle.
    """
    
    def __init__(self):
        super().__init__(daemon=True)
        self.pubsub = r.pubsub()
        self.running = True
        self._new_run_event = threading.Event()
    
    def run(self):
        """Listen for queue notifications."""
        self.pubsub.subscribe("run_queue", "run_control", "paper_control")
        
        for message in self.pubsub.listen():
            if not self.running:
                break
            
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    channel = message["channel"]
                    
                    if channel == "run_queue":
                        # New run queued - wake up the main loop
                        self._new_run_event.set()
                        logger.debug("Queue notification received", extra={
                            "event": "queue_notification", "channel": channel,
                            "payload": data,
                        })

                    elif channel == "run_control":
                        action = data.get("action")
                        run_id = data.get("run_id")
                        logger.info("Run control command", extra={
                            "event": "run_control", "action": action,
                            "run_id": run_id,
                        })

                    elif channel == "paper_control":
                        action = data.get("action")
                        run_id = data.get("run_id")
                        logger.info("Paper control command", extra={
                            "event": "paper_control", "action": action,
                            "run_id": run_id,
                        })
                
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
    
    def wait_for_notification(self, timeout: float) -> bool:
        """Wait for a new run notification."""
        result = self._new_run_event.wait(timeout)
        self._new_run_event.clear()
        return result
    
    def stop(self):
        """Stop the subscriber."""
        self.running = False
        self.pubsub.unsubscribe()


# =============================================================================
# Main Worker Loop
# =============================================================================

QUEUE_DEPTH_WARN_THRESHOLD = 10


def check_queue_depth():
    """Log current queue depth. Warns if runs are backing up."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'QUEUED') AS queued,
                        COUNT(*) FILTER (WHERE status = 'CLAIMED') AS claimed
                    FROM run_queue
                    WHERE status IN ('QUEUED', 'CLAIMED');
                """)
                row = cur.fetchone()
                queued, claimed = row[0], row[1]

        lvl = logging.WARNING if queued > QUEUE_DEPTH_WARN_THRESHOLD else logging.DEBUG
        logger.log(lvl, "Queue depth", extra={
            "event": "queue_depth", "queued": queued, "claimed": claimed,
        })
    except Exception as e:
        logger.debug("Queue depth check failed", extra={"error": str(e)})


def get_open_position_symbols() -> set[str]:
    """Get symbols that have open paper positions."""
    symbols = set()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT symbol FROM paper_positions
                    WHERE status = 'OPEN';
                """)
                for row in cur.fetchall():
                    symbols.add(row[0])
    except Exception as e:
        logger.debug(f"No open positions table yet: {e}")
    return symbols


def cleanup_old_frontend_logs():
    """Delete frontend_logs older than 7 days."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM frontend_logs WHERE server_ts < NOW() - INTERVAL '7 days';")
                deleted = cur.rowcount
            conn.commit()
        if deleted > 0:
            logger.info("Frontend logs cleanup", extra={"event": "frontend_logs_cleanup", "deleted": deleted})
    except Exception as e:
        logger.debug(f"Frontend logs cleanup failed: {e}")


def main():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    
    # Set worker identity in logging context (appears in every log line)
    ctx_worker_id.set(WORKER_ID)

    # Initialize
    symbols = get_symbols()
    timeframes = get_default_timeframes()

    logger.info("Worker starting", extra={
        "event": "worker_start",
        "symbol_count": len(symbols),
        "timeframe_count": len(timeframes),
        "timeframes": timeframes,
        "setup_ids": SETUP_IDS,
        "periodic_scans": ENABLE_PERIODIC_SCANS,
        "scan_interval_s": SCAN_INTERVAL_SECONDS,
    })
    
    # Initialize executor (pass Redis client for cache reads)
    executor = RunExecutor(DATABASE_URL, redis_client=r)

    # Initialize correlation job executor
    correlation_executor = CorrelationJobExecutor(DATABASE_URL)
    
    # Start Redis subscriber for immediate notifications
    subscriber = QueueSubscriber()
    subscriber.start()
    
    # Initialize price feed and position monitor for paper trading
    price_feed = None
    position_monitor = None
    ENABLE_POSITION_MONITORING = os.getenv("ENABLE_POSITION_MONITORING", "true").lower() == "true"
    
    if ENABLE_POSITION_MONITORING:
        try:
            from price_feed import PriceFeed, PositionMonitor
            
            # Get symbols with open positions
            open_symbols = get_open_position_symbols()
            
            # Initialize price feed
            price_feed = PriceFeed(
                symbols=list(open_symbols),
                poll_interval=2.0,  # 2 second price updates
            )
            price_feed.start()
            logger.info(f"Price feed started: tracking {len(open_symbols)} symbols with open positions")
            
            # Initialize position monitor
            def on_position_close(data):
                """Callback when a position is closed."""
                logger.info("Position closed via monitor", extra={
                    "event": "position_closed",
                    "symbol": data.get("symbol"),
                    "exit_reason": data.get("exit_reason"),
                    "realized_pnl": data.get("realized_pnl"),
                    "r_multiple": data.get("r_multiple"),
                })
            
            position_monitor = PositionMonitor(
                database_url=DATABASE_URL,
                price_feed=price_feed,
                check_interval=1.0,  # Check positions every second
                on_position_close=on_position_close,
            )
            position_monitor.start()
            logger.info("Position monitor started")
        except Exception as e:
            logger.warning(f"Position monitoring not available: {e}")
            ENABLE_POSITION_MONITORING = False
    
    last_scan_time = 0
    last_position_sync_time = 0
    last_cleanup_time = 0
    runs_executed = 0
    POSITION_SYNC_INTERVAL = 30  # Sync tracked symbols every 30 seconds
    
    try:
        while True:
            heartbeat()
            check_queue_depth()

            # Daily cleanup of old frontend telemetry logs
            if time.time() - last_cleanup_time >= 86400:
                cleanup_old_frontend_logs()
                last_cleanup_time = time.time()

            # Periodically sync price feed with open positions
            now = time.time()
            if price_feed and (now - last_position_sync_time >= POSITION_SYNC_INTERVAL):
                open_symbols = get_open_position_symbols()
                current_symbols = price_feed.symbols
                
                # Add new symbols
                for sym in open_symbols:
                    if sym not in current_symbols:
                        price_feed.add_symbol(sym)
                        logger.debug(f"Added {sym} to price feed")
                
                # Remove symbols with no open positions (optional - keep for a bit)
                # for sym in current_symbols:
                #     if sym not in open_symbols:
                #         price_feed.remove_symbol(sym)
                
                last_position_sync_time = now
            
            # Try to claim a queued run
            claimed = claim_next_run()
            
            if claimed:
                run_id, run_type = claimed
                success = execute_queued_run(executor, run_id, run_type)
                if success:
                    runs_executed += 1
                    
                    # If this was a paper trading run, sync symbols
                    if run_type == "PAPER" and price_feed:
                        open_symbols = get_open_position_symbols()
                        for sym in open_symbols:
                            price_feed.add_symbol(sym)
                
                # After executing a queued run, immediately check for more
                continue

            # No queued runs - check for correlation analysis jobs
            # Starvation guard: only process if run queue is empty, max 1 per iteration
            correlation_job_id = correlation_executor.poll_for_jobs()
            if correlation_job_id:
                logger.info("Processing correlation analysis job", extra={
                    "event": "correlation_job_start",
                    "analysis_id": correlation_job_id,
                })
                correlation_executor.execute(correlation_job_id)
                logger.info("Correlation analysis job completed", extra={
                    "event": "correlation_job_complete",
                    "analysis_id": correlation_job_id,
                })
                # Don't continue - allow periodic scan check if due

            # No queued runs - check if we should create a periodic scan
            if ENABLE_PERIODIC_SCANS and (now - last_scan_time >= SCAN_INTERVAL_SECONDS):
                logger.info("No queued runs, creating periodic scan...")
                create_periodic_scan(executor, symbols, timeframes)
                last_scan_time = now
                
                # Refresh symbols periodically if using dynamic
                if USE_DYNAMIC_SYMBOLS and runs_executed % 12 == 0:
                    symbols = get_symbols()
                    logger.info(f"Refreshed symbol universe: {len(symbols)} symbols")
                
                # Immediately try to claim the scan we just created
                continue
            
            # Wait for notification or poll interval
            subscriber.wait_for_notification(QUEUE_POLL_SECONDS)
    
    except KeyboardInterrupt:
        logger.info("Worker shutting down...")
    
    finally:
        subscriber.stop()
        if position_monitor:
            position_monitor.stop()
        if price_feed:
            price_feed.stop()
        r.hset(f"worker:{WORKER_ID}", "status", "stopped")
        logger.info("Worker stopped", extra={
            "event": "worker_stop", "runs_executed": runs_executed,
        })


if __name__ == "__main__":
    main()
