from opportunity_simulator import OpportunitySimulator, SimulationConfig


def _sim(cfg: SimulationConfig) -> OpportunitySimulator:
    return OpportunitySimulator(database_url="postgresql://unused", config=cfg)


def test_stop_factor_uses_per_setup_override_when_present():
    sim = _sim(SimulationConfig(
        stop_distance_factor=0.7,
        stop_distance_factors={"S9": 0.85},
    ))
    assert sim._resolve_stop_distance_factor("S9") == 0.85


def test_stop_factor_missing_setup_key_is_neutral_not_global():
    sim = _sim(SimulationConfig(
        stop_distance_factor=0.7,
        stop_distance_factors={"S9": 0.85},
    ))
    assert sim._resolve_stop_distance_factor("U1") == 1.0


def test_stop_factor_uses_global_when_no_per_setup_map():
    sim = _sim(SimulationConfig(
        stop_distance_factor=0.7,
        stop_distance_factors={},
    ))
    assert sim._resolve_stop_distance_factor("U1") == 0.7

