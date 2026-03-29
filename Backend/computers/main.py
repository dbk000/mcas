"""
Computers Container — consolidated entry point.

Supervises 5 services:
- worker (queue-driven simulation executor)
- indicator_worker_0 (indicator compute, partition 0)
- indicator_worker_1 (indicator compute, partition 1)
- signal_worker_0 (signal evaluation, partition 0)
- signal_worker_1 (signal evaluation, partition 1)
"""

import asyncio
import functools
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

from governance.bootstrap import bootstrap_genesis
bootstrap_genesis()

from governance.supervisor import Supervisor, SupervisedTask

# Import run() coroutines from each service
from governance.worker import run as worker_run
from governance.indicator_worker import run as indicator_worker_run
from governance.signal_worker import run as signal_worker_run

N_WORKERS = int(os.getenv("N_WORKERS", "2"))


def main():
    sv = Supervisor()

    sv.add_task(SupervisedTask("worker", worker_run))

    # Two indicator workers, each with their own partition index
    for i in range(N_WORKERS):
        sv.add_task(SupervisedTask(
            f"indicator_worker_{i}",
            functools.partial(indicator_worker_run, worker_index=i, n_workers=N_WORKERS),
        ))

    # Two signal workers, each with their own partition index
    for i in range(N_WORKERS):
        sv.add_task(SupervisedTask(
            f"signal_worker_{i}",
            functools.partial(signal_worker_run, worker_index=i, n_workers=N_WORKERS),
        ))

    asyncio.run(sv.run())


if __name__ == "__main__":
    main()
