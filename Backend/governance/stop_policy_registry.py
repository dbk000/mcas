"""
Canonical stop-policy defaults + validation helpers.

This module reads from the frozen StopPolicyRegistry (populated by bootstrap)
for stop-policy defaults used by Pipeline templating, startup validation,
and runtime fallback resolution.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable


STOP_POLICY_SCHEMA_VERSION = "v1"


def get_known_setup_ids() -> list[str]:
    """Read known setup IDs from the frozen StopPolicyRegistry."""
    from governance.bootstrap import get_stop_policy
    return list(get_stop_policy().known_setup_ids)


def get_canonical_defaults() -> dict:
    """Read canonical stop-policy defaults from the frozen StopPolicyRegistry."""
    from governance.bootstrap import get_stop_policy
    return dict(get_stop_policy().canonical_defaults)


# Note: The old module-level KNOWN_SETUP_IDS and CANONICAL_STOP_POLICY_DEFAULTS
# are removed. Use get_known_setup_ids() and get_canonical_defaults() functions.


def build_stop_policy_template(strategy_registry: dict | None = None) -> dict:
    """
    Build complete template for Pipeline/clients.

    If strategy_registry is provided, include all strategy ids from it;
    otherwise include canonical known setup ids.
    """
    canonical = get_canonical_defaults()
    if strategy_registry:
        setup_ids = sorted(str(k) for k in strategy_registry.keys())
    else:
        setup_ids = sorted(canonical["stop_distance_factors"].keys())

    base = deepcopy(canonical)
    factors = {}
    for sid in setup_ids:
        factors[sid] = float(base["stop_distance_factors"].get(sid, 1.0))
    base["stop_distance_factors"] = factors
    return base


def validate_stop_registry(
    strategy_registry: dict | None = None,
    stop_policy_defaults: dict | None = None,
    required_setup_ids: Iterable[str] | None = None,
) -> list[str]:
    """
    Validate registry completeness + schema.

    Returns list of human-readable errors. Empty list means valid.
    """
    defaults = stop_policy_defaults or get_canonical_defaults()
    errors: list[str] = []

    schema_version = defaults.get("schema_version")
    if schema_version != STOP_POLICY_SCHEMA_VERSION:
        errors.append(
            f"schema_version mismatch: expected {STOP_POLICY_SCHEMA_VERSION}, got {schema_version!r}"
        )

    factors = defaults.get("stop_distance_factors")
    if not isinstance(factors, dict):
        errors.append("stop_distance_factors missing or not an object")
        factors = {}

    intrabar_policy = defaults.get("intrabar_policy")
    if intrabar_policy not in {"STOP_FIRST", "TP_FIRST", "OHLC_HEURISTIC"}:
        errors.append(
            f"intrabar_policy invalid: {intrabar_policy!r} "
            "(must be STOP_FIRST | TP_FIRST | OHLC_HEURISTIC)"
        )

    if not isinstance(defaults.get("one_event_per_bar"), bool):
        errors.append("one_event_per_bar must be boolean")

    if required_setup_ids is not None:
        target_ids = sorted({str(s) for s in required_setup_ids if s})
    elif strategy_registry is not None:
        target_ids = sorted(str(k) for k in strategy_registry.keys())
    else:
        target_ids = sorted(get_known_setup_ids())

    for sid in target_ids:
        if sid not in factors:
            errors.append(f"missing stop defaults for setup_id={sid}")
            continue
        val = factors[sid]
        try:
            fval = float(val)
            if fval <= 0:
                errors.append(f"invalid stop factor for {sid}: {val!r} (must be > 0)")
        except Exception:
            errors.append(f"invalid stop factor for {sid}: {val!r} (must be numeric)")

    return errors

