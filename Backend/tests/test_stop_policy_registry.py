from stop_policy_registry import (
    CANONICAL_STOP_POLICY_DEFAULTS,
    build_stop_policy_template,
    validate_stop_registry,
)


def test_validate_stop_registry_passes_for_canonical_defaults():
    errors = validate_stop_registry(required_setup_ids=["S9", "U1", "D1", "S10"])
    assert errors == []


def test_validate_stop_registry_detects_missing_setup_default():
    broken = dict(CANONICAL_STOP_POLICY_DEFAULTS)
    broken["stop_distance_factors"] = dict(CANONICAL_STOP_POLICY_DEFAULTS["stop_distance_factors"])
    broken["stop_distance_factors"].pop("S9", None)
    errors = validate_stop_registry(stop_policy_defaults=broken, required_setup_ids=["S9", "U1"])
    assert any("missing stop defaults for setup_id=S9" in e for e in errors)


def test_build_stop_policy_template_includes_schema_and_all_setups():
    strategy_registry = {"S9": {}, "U1": {}, "D1": {}}
    template = build_stop_policy_template(strategy_registry)
    assert template["schema_version"] == "v1"
    assert set(template["stop_distance_factors"].keys()) == {"S9", "U1", "D1"}

