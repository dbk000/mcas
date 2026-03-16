"""
DXY Regime governance layer.

DXY_REGIME_ENABLED is governed by WorkerTypedConfig.
get_dxy_multiplier() is a governance-level function (reads config), not pure math.
Pure DXY math lives in core_logic/dxy_math.py.
"""
from governance.bootstrap import get_config


def get_dxy_regime_enabled() -> bool:
    """Read DXY_REGIME_ENABLED from the governed registry."""
    cfg = get_config().Worker
    return cfg.DXY_REGIME_ENABLED


# Backward-compatible module-level attribute.
# Consumers that read ``DXY_REGIME_ENABLED`` directly get the default;
# runtime code should call ``get_dxy_regime_enabled()`` for the governed value.
DXY_REGIME_ENABLED = True


def get_dxy_multiplier(*args, **kwargs) -> float:
    """Stub multiplier — always 1.0.

    Kept for backward compatibility. All new strategy logic should use
    the RegimeClassification hard gate in core_logic.dxy_math.
    """
    return 1.0
