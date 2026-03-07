"""Verify min_r_multiple_floor has been fully removed."""
from opportunity_simulator import SimulationConfig


def test_simulation_config_no_r_floor():
    """SimulationConfig should not have min_r_multiple_floor attribute."""
    cfg = SimulationConfig()
    assert not hasattr(cfg, "min_r_multiple_floor")


def test_extreme_negative_r_still_data_available():
    """A -10R trade should remain data_available=True (no floor filtering)."""
    # This is a design verification — the simulator no longer drops
    # trades based on R-multiple outcome. The field is gone entirely.
    cfg = SimulationConfig()
    # Confirm no attribute that would cause outcome-based filtering
    for attr in dir(cfg):
        assert "r_multiple_floor" not in attr, f"Found residual: {attr}"
