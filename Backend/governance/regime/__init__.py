"""Regime governance package — shock labelling, beta tables, keyword clusters,
and Fear & Greed Index fetchers (re-exported from _fng for backwards-compat
with the old governance/regime.py module that this package replaced)."""

from ._fng import (  # noqa: F401
    fetch_fear_greed_index,
    fetch_fear_greed_index_async,
)
