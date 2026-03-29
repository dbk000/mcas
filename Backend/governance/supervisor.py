"""
Supervisor — governance/supervisor.py
=====================================

Manages multiple supervised tasks within a single container. Provides:
- Restart-on-failure with exponential backoff + jitter
- Health file (/tmp/healthy) for Docker healthcheck
- Structured logging per task lifecycle
- Graceful shutdown on SIGINT/SIGTERM

Design: sync blocking services run via asyncio.to_thread(). Native async
services (e.g. d2_paper_trader) run as direct coroutines. The supervisor
itself is a single asyncio event loop — no threading at the supervisor level.

Lives in governance/ because it is orchestration infrastructure. Does not
import from core_logic/ or indicators/.
"""

import asyncio
import logging
import os
import random
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable

logger = logging.getLogger("supervisor")

HEALTH_FILE = Path("/tmp/healthy")

# Supervisor config — could be moved to TypedConfigRegistry later
MAX_BACKOFF_S = 60.0
CLEAN_RUN_RESET_S = 60.0  # Reset backoff after this many seconds of clean running
PERSISTENT_FAILURE_THRESHOLD = 5  # restarts within PERSISTENT_FAILURE_WINDOW_S
PERSISTENT_FAILURE_WINDOW_S = 600.0  # 10 minutes
HEALTH_CHECK_INTERVAL_S = 10.0


@dataclass
class TaskHealth:
    status: str = "pending"  # pending, running, restarting, failed
    restart_count: int = 0
    last_restart: float | None = None
    last_error: str | None = None
    started_at: float | None = None
    restart_timestamps: list[float] = field(default_factory=list)


@dataclass
class SupervisedTask:
    """Definition of a task to be supervised.

    coro_factory: async callable that returns when the task completes (or crashes).
                  For sync services, wrap with asyncio.to_thread inside.
    """
    name: str
    coro_factory: Callable[[], Awaitable[None]]
    restart_on_failure: bool = True
    max_backoff_s: float = MAX_BACKOFF_S
    initial_delay_s: float = 0.0


class Supervisor:
    """Launches and supervises multiple async tasks with restart-on-failure."""

    def __init__(self):
        self._tasks: list[SupervisedTask] = []
        self._health: dict[str, TaskHealth] = {}
        self._shutdown_event = asyncio.Event()
        self._async_tasks: dict[str, asyncio.Task] = {}

    def add_task(self, task: SupervisedTask) -> None:
        self._tasks.append(task)
        self._health[task.name] = TaskHealth()

    def get_health(self) -> dict[str, TaskHealth]:
        return dict(self._health)

    async def run(self) -> None:
        """Main supervisor loop. Blocks until shutdown signal received."""
        loop = asyncio.get_running_loop()

        # Install signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._signal_handler, sig)

        logger.info(f"Supervisor starting with {len(self._tasks)} tasks")

        # Launch all tasks
        for task_def in self._tasks:
            self._async_tasks[task_def.name] = asyncio.create_task(
                self._supervise_task(task_def),
                name=f"supervisor:{task_def.name}",
            )

        # Health check loop
        health_task = asyncio.create_task(
            self._health_loop(), name="supervisor:health"
        )

        # Wait for shutdown
        await self._shutdown_event.wait()

        logger.info("Shutdown signal received, cancelling tasks...")
        health_task.cancel()
        for name, atask in self._async_tasks.items():
            atask.cancel()
            logger.info(f"  Cancelled: {name}")

        # Wait for all tasks to finish cancellation
        all_tasks = list(self._async_tasks.values()) + [health_task]
        await asyncio.gather(*all_tasks, return_exceptions=True)

        # Remove health file
        HEALTH_FILE.unlink(missing_ok=True)
        logger.info("Supervisor shutdown complete")

    async def _supervise_task(self, task_def: SupervisedTask) -> None:
        """Run a single task with restart-on-failure and exponential backoff."""
        health = self._health[task_def.name]
        backoff = 1.0

        # Initial delay (e.g. stagger startup)
        if task_def.initial_delay_s > 0:
            await asyncio.sleep(task_def.initial_delay_s)

        while not self._shutdown_event.is_set():
            health.status = "running"
            health.started_at = time.time()
            logger.info(f"[{task_def.name}] Starting")

            try:
                await task_def.coro_factory()
                # Clean exit — task returned normally
                logger.info(f"[{task_def.name}] Exited cleanly")
                if not task_def.restart_on_failure:
                    health.status = "completed"
                    return
                # For restart_on_failure tasks, a clean exit is still restarted
                # (the while-True loops inside services shouldn't exit)
                health.status = "restarting"

            except asyncio.CancelledError:
                logger.info(f"[{task_def.name}] Cancelled (shutdown)")
                health.status = "cancelled"
                return

            except Exception as e:
                health.last_error = f"{type(e).__name__}: {e}"
                health.restart_count += 1
                health.last_restart = time.time()
                health.restart_timestamps.append(time.time())
                health.status = "restarting"
                logger.error(
                    f"[{task_def.name}] Crashed (restart #{health.restart_count}): {e}",
                    exc_info=True,
                )

                # Check for persistent failure
                if self._is_persistent_failure(health):
                    health.status = "failed"
                    logger.critical(
                        f"[{task_def.name}] PERSISTENT FAILURE — "
                        f"{PERSISTENT_FAILURE_THRESHOLD} restarts in "
                        f"{PERSISTENT_FAILURE_WINDOW_S}s. Giving up."
                    )
                    return

                if not task_def.restart_on_failure:
                    health.status = "failed"
                    return

                # Exponential backoff with jitter
                jitter = random.uniform(0, backoff * 0.3)
                wait = min(backoff + jitter, task_def.max_backoff_s)
                logger.info(f"[{task_def.name}] Restarting in {wait:.1f}s")
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, task_def.max_backoff_s)
                continue

            # Clean run — reset backoff
            backoff = 1.0

    def _is_persistent_failure(self, health: TaskHealth) -> bool:
        """Check if task has failed too many times in the window."""
        now = time.time()
        cutoff = now - PERSISTENT_FAILURE_WINDOW_S
        # Prune old timestamps
        health.restart_timestamps = [
            ts for ts in health.restart_timestamps if ts > cutoff
        ]
        return len(health.restart_timestamps) >= PERSISTENT_FAILURE_THRESHOLD

    async def _health_loop(self) -> None:
        """Periodically write/remove health file based on task states."""
        while True:
            try:
                all_ok = True
                for name, health in self._health.items():
                    if health.status == "failed":
                        all_ok = False
                        break

                if all_ok:
                    HEALTH_FILE.write_text("ok")
                else:
                    HEALTH_FILE.unlink(missing_ok=True)
                    logger.warning("Health check: at least one task in persistent failure")

                # Log summary periodically
                summary = {
                    name: f"{h.status} (restarts={h.restart_count})"
                    for name, h in self._health.items()
                }
                logger.debug(f"Health: {summary}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Health loop error: {e}")

            await asyncio.sleep(HEALTH_CHECK_INTERVAL_S)

    def _signal_handler(self, sig: signal.Signals) -> None:
        logger.info(f"Received {sig.name}, initiating shutdown...")
        self._shutdown_event.set()
