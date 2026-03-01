"""
Indicator Soup — 743 OHLCV-computable indicators across 37 families.

Public API:
    compute_all_indicators(open_, high, low, close, volume, timestamps)
        -> dict[str, np.ndarray]  (WARNING: materializes all arrays — use for tests only)

    iter_all_variants(open_, high, low, close, volume, timestamps)
        -> Iterator[(name, array)]  (streaming — variant-at-a-time, production use)

    total_variant_count() -> int

All arrays are (n_bars, n_symbols) float64. NaN marks warmup/missing.
"""

# Import families to trigger registration (order doesn't matter — topo_sort handles deps)
from . import rolling_utils  # noqa: F401

from .families import (  # noqa: F401
    compute_all_indicators,
    iter_all_variants,
    total_variant_count,
    all_families,
)

# Import all family modules to trigger register_family() calls
from .families import ma  # noqa: F401
from .families import oscillators  # noqa: F401
from .families import volatility  # noqa: F401
from .families import trend  # noqa: F401
from .families import volume  # noqa: F401
from .families import patterns  # noqa: F401
from .families import composite  # noqa: F401

__all__ = [
    "compute_all_indicators",
    "iter_all_variants",
    "total_variant_count",
    "all_families",
    "rolling_utils",
]
