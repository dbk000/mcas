import requests
import logging
import os
import sys
import json
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from governance.bootstrap import get_config
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

logger = logging.getLogger(__name__)

class InfrastructureClient:
    """
    Governed Infrastructure Client.
    Handles all external HTTP calls and manages concurrency.
    No direct 'requests' calls allowed in core logic.
    """
    _thread_executor: Optional[ThreadPoolExecutor] = None
    _process_executor: Optional[ProcessPoolExecutor] = None

    @classmethod
    def get_thread_executor(cls) -> ThreadPoolExecutor:
        if cls._thread_executor is None:
            config = get_config().Worker
            cls._thread_executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENCY)
        return cls._thread_executor

    @classmethod
    def get_process_executor(cls) -> ProcessPoolExecutor:
        """
        Rule 5 Compliance: Central ProcessPoolExecutor.
        Validates system resources (conceptually) before spawning.
        """
        if cls._process_executor is None:
            config = get_config().Worker
            # In production, this would query a central ResourceGovernor
            cls._process_executor = ProcessPoolExecutor(max_workers=config.MAX_CONCURRENCY)
        return cls._process_executor

    @classmethod
    def get(cls, url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10, headers: Optional[Dict[str, str]] = None) -> Any:
        """Governed GET request."""
        if not url:
            raise ValueError("Infrastructure URL cannot be empty.")
        
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Infrastructure GET failed for {url}: {e}")
            raise

    @classmethod
    def fetch_binance_tickers(cls) -> Dict[str, float]:
        """Specific helper for Binance tickers."""
        config = get_config().Worker
        url = config.BINANCE_TICKER_URL
        if not url:
            raise RuntimeError("BINANCE_TICKER_URL not configured in WorkerTypedConfig.")
        
        data = cls.get(url)
        return {t["symbol"]: float(t["price"]) for t in data if "symbol" in t and "price" in t}

    @classmethod
    def fetch_1s_klines_batch(cls, symbols: List[str], start_ms: int, limit: int = 300) -> Dict[str, List[Any]]:
        """
        Governed parallel fetch of 1s klines.
        Uses the internal ThreadPoolExecutor.
        """
        executor = cls.get_thread_executor()
        
        config = get_config().Worker
        url = config.BINANCE_KLINES_URL
        if not url:
             raise RuntimeError("BINANCE_KLINES_URL not configured in WorkerTypedConfig.")

        def fetch_one(symbol):
            params = {
                "symbol": symbol,
                "interval": "1s",
                "startTime": start_ms,
                "limit": limit
            }
            try:
                data = cls.get(url, params=params)
                return symbol, data
            except Exception:
                return symbol, []

        results = list(executor.map(fetch_one, symbols))
        return {symbol: klines for symbol, klines in results}

class FileSystemClient:
    """
    Governed FileSystem Client.
    Rule 3 & 4 Compliance: Logic files should not touch disk directly.
    """
    
    @staticmethod
    def write_json(path: str, data: Any, indent: int = 2):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=indent)

    @staticmethod
    def read_json(path: str) -> Any:
        with open(path, "r") as f:
            return json.load(f)
            
    @staticmethod
    def write_text(path: str, content: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    @staticmethod
    def read_text(path: str) -> str:
        with open(path, "r") as f:
            return f.read()

    @staticmethod
    def makedirs(path: str, exist_ok: bool = True):
        os.makedirs(path, exist_ok=exist_ok)

    @staticmethod
    def join_paths(*args: str) -> str:
        return os.path.join(*args)

    @staticmethod
    def exists(path: str) -> bool:
        return os.path.exists(path)

    @staticmethod
    def is_dir(path: str) -> bool:
        return os.path.isdir(path)

    @staticmethod
    def get_dirname(path: str) -> str:
        return os.path.dirname(path)

class SystemClient:
    """
    Governed System Client for environment-aware operations.
    """
    @staticmethod
    def get_stdout():
        return sys.stdout

    @staticmethod
    def get_stderr():
        return sys.stderr

    @staticmethod
    def get_now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def get_timestamp_s() -> float:
        return time.time()

    @staticmethod
    def get_env(key: str, default: str | None = None) -> str | None:
        """Removed: env reads must go through governance.bootstrap.get_config()."""
        raise NotImplementedError(
            "SystemClient.get_env() is removed. "
            "Use get_config().Worker from governance.bootstrap instead."
        )
