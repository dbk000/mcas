"""
Family registry, dependency resolver, and variant-at-a-time iterator.

Each family module registers itself via register_family(). The orchestrator
computes families in dependency order, passing context dict with retained
Tier 1/2 arrays for downstream use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterator

import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Family definition
# ---------------------------------------------------------------------------


@dataclass
class IndicatorVariant:
    """One concrete indicator (e.g. SMA_20, RSI_14)."""
    name: str                    # Unique ID like "SMA_20", "RSI_14"
    family: str                  # Parent family like "MA_SMA"
    params: dict = field(default_factory=dict)
    retain_in_context: bool = False  # If True, kept in context dict for deps


@dataclass
class IndicatorFamily:
    """A group of related indicator variants (e.g. all SMA periods)."""
    name: str                    # e.g. "MA_SMA"
    tier: int                    # 1, 2, or 3 (dependency order)
    dependencies: list[str] = field(default_factory=list)  # Family names
    warmup: int = 200
    variants: list[IndicatorVariant] = field(default_factory=list)
    # Callable: (open_, high, low, close, volume, timestamps, context, variant) -> np.ndarray
    compute_fn: Callable | None = None


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_FAMILY_REGISTRY: dict[str, IndicatorFamily] = {}


def register_family(family: IndicatorFamily) -> IndicatorFamily:
    """Register a family. Called by each family module at import time."""
    _FAMILY_REGISTRY[family.name] = family
    return family


def get_family(name: str) -> IndicatorFamily:
    return _FAMILY_REGISTRY[name]


def all_families() -> dict[str, IndicatorFamily]:
    return dict(_FAMILY_REGISTRY)


# ---------------------------------------------------------------------------
# Context dict — retained arrays for downstream dependencies
# ---------------------------------------------------------------------------

# Exactly these 11 arrays are retained (plan §Phase 1 memory budget):
RETAINED_CONTEXT_KEYS = {
    "SMA_20", "SMA_50", "SMA_200",
    "EMA_9", "EMA_12", "EMA_20", "EMA_26", "EMA_50",
    "ATR_14", "ATR_10",
    "RSI_14",
}


# ---------------------------------------------------------------------------
# Dependency-ordered iteration
# ---------------------------------------------------------------------------

def _tier_sort() -> list[IndicatorFamily]:
    """Return families in tier order (Tier 1 → 2 → 3, then alpha).

    This is NOT a true topological sort — it relies on the convention that
    all dependencies are tier N → tier N-1.  If same-tier dependencies are
    added in the future, this must be replaced with a real topo-sort.
    """
    families = list(_FAMILY_REGISTRY.values())
    families.sort(key=lambda f: (f.tier, f.name))
    return families


def iter_all_variants(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    timestamps: np.ndarray | None = None,
) -> Iterator[tuple[str, np.ndarray]]:
    """
    Yield (indicator_name, indicator_array) in dependency order.

    Context dict accumulates retained Tier 1/2 arrays. Each variant is
    computed → yielded → released (unless retained). This is the
    variant-at-a-time streaming API.
    """
    context: dict[str, np.ndarray] = {}
    ohlcv = (open_, high, low, close, volume, timestamps)

    for family in _tier_sort():
        if family.compute_fn is None:
            log.warning("Family %s has no compute_fn, skipping", family.name)
            continue

        for variant in family.variants:
            try:
                arr = family.compute_fn(*ohlcv, context, variant)
            except Exception:
                log.exception("Failed computing %s", variant.name)
                continue

            if arr is None:
                continue

            # Retain if this variant is a dependency provider
            if variant.retain_in_context or variant.name in RETAINED_CONTEXT_KEYS:
                context[variant.name] = arr

            yield variant.name, arr
            # Caller can release arr after processing stats


def compute_all_indicators(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    timestamps: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """
    Compute ALL indicators, return as dict.

    WARNING: This materializes all ~743 arrays simultaneously (~100+ GB).
    Only use for small test data. For production, use iter_all_variants().
    """
    return dict(iter_all_variants(open_, high, low, close, volume, timestamps))


# ---------------------------------------------------------------------------
# Variant count
# ---------------------------------------------------------------------------

def total_variant_count() -> int:
    return sum(len(f.variants) for f in _FAMILY_REGISTRY.values())
