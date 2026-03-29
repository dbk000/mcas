"""
Collectors Container — consolidated entry point.

Supervises 6 services:
- ingestor (Binance WS → Redis candle ring buffers)
- ohlcv_persister (Redis candles → Postgres)
- social_collector (Telegram/Reddit/RSS/CoinGecko → Postgres)
- derivatives_collector (Binance Futures data → Postgres)
- onchain_collector (DefiLlama/CoinGecko/Dune/etc → Postgres)
- cpu_collector (host CPU/memory metrics → Redis)
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
# governance/ and monitoring/ are packages (have __init__.py or are under /backend)
from governance.ingestor import run as ingestor_run
from governance.ohlcv_persister import run as ohlcv_persister_run
# social_collector uses package imports (social_collector/__init__.py exists)
from social_collector.app.collector import run as social_collector_run
# scripts/ modules are in PYTHONPATH directly — bare imports
from derivatives_collector import run as derivatives_collector_run
from onchain_collector import run as onchain_collector_run
from monitoring.cpu_collector import run as cpu_collector_run


def main():
    sv = Supervisor()

    sv.add_task(SupervisedTask("ingestor", ingestor_run))
    sv.add_task(SupervisedTask("ohlcv_persister", ohlcv_persister_run))
    sv.add_task(SupervisedTask("social_collector", social_collector_run))
    sv.add_task(SupervisedTask("derivatives_collector", derivatives_collector_run))
    sv.add_task(SupervisedTask("onchain_collector", onchain_collector_run))
    sv.add_task(SupervisedTask("cpu_collector", cpu_collector_run))

    asyncio.run(sv.run())


if __name__ == "__main__":
    main()
