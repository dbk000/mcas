"""
Strats/Evals Container — consolidated entry point.

Supervises 6 services:
- d2_paper_trader (Fade the Casino — paper trading, native async)
- unicorn_pipeline (U1 embryo detection & promotion)
- expired_opp_exit_settler (settle expired opportunities)
- equities_pipeline (equities stage orchestrator)
- equities_strategy_worker (daily equities signal evaluation)
- equities_task_worker (equities task board executor)
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

# Import run() coroutines from each service
from governance.d2_paper_trader import run as d2_paper_trader_run
from governance.unicorn_pipeline import run as unicorn_pipeline_run
# scripts/ modules are in PYTHONPATH directly — bare imports
from expired_opportunity_exit_settler import run as expired_settler_run
from equities_pipeline_runner import run as equities_pipeline_run
from governance.equities_strategy_worker import run as equities_strategy_run
from equities_task_worker_loop import run as equities_task_run


def main():
    sv = Supervisor()

    # d2_paper_trader is native async — no to_thread needed
    sv.add_task(SupervisedTask("d2_paper_trader", d2_paper_trader_run))
    sv.add_task(SupervisedTask("unicorn_pipeline", unicorn_pipeline_run))
    sv.add_task(SupervisedTask("expired_opp_exit_settler", expired_settler_run))
    sv.add_task(SupervisedTask("equities_pipeline", equities_pipeline_run))
    sv.add_task(SupervisedTask("equities_strategy_worker", equities_strategy_run))
    sv.add_task(SupervisedTask("equities_task_worker", equities_task_run))

    asyncio.run(sv.run())


if __name__ == "__main__":
    main()
