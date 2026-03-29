"""
Strats/Features Container — consolidated entry point.

Supervises 1 service:
- custom_strategy_worker (user-defined strategy evaluator)

Single service wrapped in supervisor for restart-on-failure, health file,
and consistent logging. DO NOT scale > 1 without redesigning crossover state.
"""

import asyncio
import logging
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

from governance.custom_strategy_worker import run as custom_strategy_run


def main():
    sv = Supervisor()
    sv.add_task(SupervisedTask("custom_strategy_worker", custom_strategy_run))
    asyncio.run(sv.run())


if __name__ == "__main__":
    main()
