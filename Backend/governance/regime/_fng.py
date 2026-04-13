"""Fear & Greed Index fetchers — relocated from governance/regime.py when the
regime/ package was created. Imports preserved via regime/__init__.py
re-export. Do not import from here directly; import from governance.regime."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

_FNG_CACHE: dict = {"value": None, "classification": None, "ts": 0.0}
_FNG_CACHE_TTL_S = 3600  # 1 hour — FnG updates more often than daily


def fetch_fear_greed_index() -> dict:
    """Fetch current Fear & Greed Index (synchronous, cached 1h).

    Returns {"value": int 0-100, "classification": str} or empty dict on failure.
    """
    now = time.time()
    if _FNG_CACHE["ts"] and (now - _FNG_CACHE["ts"]) < _FNG_CACHE_TTL_S:
        return {"value": _FNG_CACHE["value"], "classification": _FNG_CACHE["classification"]}

    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("FnG API: HTTP %d", resp.status_code)
            return {}
        data = resp.json()
        entry = data.get("data", [{}])[0]
        value = int(entry.get("value", 0))
        classification = entry.get("value_classification", "")
        _FNG_CACHE["value"] = value
        _FNG_CACHE["classification"] = classification
        _FNG_CACHE["ts"] = now
        logger.info("FnG: %d (%s)", value, classification)
        return {"value": value, "classification": classification}
    except Exception as e:
        logger.warning("FnG API unavailable: %s", e)
        return {}


async def fetch_fear_greed_index_async() -> dict:
    """Async variant for use in d2_paper_trader and other asyncio services."""
    now = time.time()
    if _FNG_CACHE["ts"] and (now - _FNG_CACHE["ts"]) < _FNG_CACHE_TTL_S:
        return {"value": _FNG_CACHE["value"], "classification": _FNG_CACHE["classification"]}

    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.alternative.me/fng/?limit=1",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning("FnG API: HTTP %d", resp.status)
                    return {}
                data = await resp.json(content_type=None)
                entry = data.get("data", [{}])[0]
                value = int(entry.get("value", 0))
                classification = entry.get("value_classification", "")
                _FNG_CACHE["value"] = value
                _FNG_CACHE["classification"] = classification
                _FNG_CACHE["ts"] = now
                logger.info("FnG: %d (%s)", value, classification)
                return {"value": value, "classification": classification}
    except Exception as e:
        logger.warning("FnG API unavailable: %s", e)
        return {}