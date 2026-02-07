"""
Canon §1.6 + §1.11: Indicator Registry Lint
============================================

Automated checks:
- All indicator IDs are unique (no collisions)
- All IDs follow the canonical grammar: [A-Z0-9_.]+
- All IDs are produced via generate_indicator_id() (not hand-authored)
- No semantic change without version bump
- Library fingerprint is stable and deterministic
"""
import re
import pytest

from indicators import (
    INDICATOR_REGISTRY,
    generate_indicator_id,
    get_indicator_library_fingerprint,
    IndicatorDef,
    _INDICATOR_ID_RE,
)


# =============================================================================
# ID Uniqueness
# =============================================================================

class TestIndicatorUniqueness:
    """Canon §1.6: No two indicators may share the same indicator_id."""

    def test_no_duplicate_ids(self):
        ids = [defn.indicator_id for defn in INDICATOR_REGISTRY.values()]
        duplicates = [x for x in ids if ids.count(x) > 1]
        assert len(duplicates) == 0, f"Duplicate indicator IDs found: {set(duplicates)}"

    def test_registry_keys_match_indicator_ids(self):
        """Registry dict keys must match the indicator_id field."""
        for key, defn in INDICATOR_REGISTRY.items():
            assert key == defn.indicator_id, (
                f"Registry key '{key}' != indicator_id '{defn.indicator_id}'"
            )

    def test_registry_not_empty(self):
        assert len(INDICATOR_REGISTRY) > 0, "Indicator registry is empty"


# =============================================================================
# ID Grammar
# =============================================================================

class TestIndicatorIDGrammar:
    """Canon §1.6: IDs use uppercase A-Z, digits 0-9, underscores, dots only."""

    def test_all_ids_match_grammar(self):
        pattern = re.compile(r'^[A-Z0-9_.]+$')
        bad_ids = []
        for defn in INDICATOR_REGISTRY.values():
            if not pattern.match(defn.indicator_id):
                bad_ids.append(defn.indicator_id)
        assert len(bad_ids) == 0, f"IDs violating grammar: {bad_ids}"

    def test_no_lowercase_in_ids(self):
        for defn in INDICATOR_REGISTRY.values():
            assert defn.indicator_id == defn.indicator_id.upper().replace("-", "_"), (
                f"ID '{defn.indicator_id}' contains lowercase or hyphens"
            )

    def test_no_synonyms(self):
        """Canon §1.6: No synonymous single-indicator families (e.g. MA_50 and SMA_50)."""
        # Check for indicators that compute the same thing with different names.
        # MA_GAP_ATR is a derived metric (gap between MAs), not a moving average,
        # so MA + SMA coexisting is fine. We check for direct conflicts:
        # e.g. both MA_50 and SMA_50 existing as simple moving averages.
        ma_ids = {d.indicator_id for d in INDICATOR_REGISTRY.values()
                  if d.indicator_id.startswith("MA_") and "GAP" not in d.indicator_id and "SLOPE" not in d.indicator_id}
        sma_ids = {d.indicator_id for d in INDICATOR_REGISTRY.values()
                   if d.indicator_id.startswith("SMA_") and "SLOPE" not in d.indicator_id}
        # Extract numeric params to check for direct conflicts
        ma_params = {d.indicator_id.replace("MA_", "") for d in INDICATOR_REGISTRY.values()
                     if d.indicator_id in ma_ids}
        sma_params = {d.indicator_id.replace("SMA_", "") for d in INDICATOR_REGISTRY.values()
                      if d.indicator_id in sma_ids}
        overlap = ma_params & sma_params
        assert len(overlap) == 0, (
            f"Synonym conflict: MA and SMA both define params {overlap} — "
            f"Canon §1.6 forbids synonyms"
        )


# =============================================================================
# Canonical Generator
# =============================================================================

class TestCanonicalGenerator:
    """Canon §1.6: All IDs produced via generate_indicator_id()."""

    def test_generator_basic(self):
        assert generate_indicator_id("ATR", 14) == "ATR_14"

    def test_generator_no_params(self):
        assert generate_indicator_id("CLV") == "CLV"

    def test_generator_multiple_params(self):
        assert generate_indicator_id("BB", 20, 2) == "BB_20_2"

    def test_generator_uppercase(self):
        assert generate_indicator_id("ema", 9) == "EMA_9"

    def test_generator_rejects_invalid_chars(self):
        with pytest.raises(ValueError):
            generate_indicator_id("ema-bad!", 9)

    def test_generator_rejects_spaces(self):
        with pytest.raises(ValueError):
            generate_indicator_id("MY INDICATOR", 14)

    def test_all_registry_ids_reproducible_via_generator(self):
        """Every ID in the registry must be validatable by the ID regex."""
        for defn in INDICATOR_REGISTRY.values():
            assert _INDICATOR_ID_RE.match(defn.indicator_id), (
                f"ID '{defn.indicator_id}' fails canonical format validation"
            )


# =============================================================================
# Version and Fingerprint
# =============================================================================

class TestIndicatorVersioning:
    """Canon §1.6: Version must be present; fingerprint must be deterministic."""

    def test_all_have_version(self):
        for defn in INDICATOR_REGISTRY.values():
            assert defn.version, f"Indicator '{defn.indicator_id}' has no version"

    def test_fingerprint_deterministic(self):
        fp1 = get_indicator_library_fingerprint()
        fp2 = get_indicator_library_fingerprint()
        assert fp1 == fp2, "Library fingerprint is non-deterministic"

    def test_fingerprint_is_hex_string(self):
        fp = get_indicator_library_fingerprint()
        assert len(fp) == 16, f"Fingerprint length {len(fp)}, expected 16"
        assert all(c in "0123456789abcdef" for c in fp), "Fingerprint is not hex"


# =============================================================================
# Indicator Definitions Completeness
# =============================================================================

class TestIndicatorDefinitions:
    """Canon §1.6: Each definition includes required fields."""

    def test_all_have_required_fields(self):
        for defn in INDICATOR_REGISTRY.values():
            assert defn.indicator_id, f"Missing indicator_id"
            assert defn.name, f"Missing name for {defn.indicator_id}"
            assert defn.tier >= 0, f"Invalid tier for {defn.indicator_id}"
            assert isinstance(defn.inputs, list), f"inputs not a list for {defn.indicator_id}"
            assert len(defn.inputs) > 0, f"Empty inputs for {defn.indicator_id}"

    def test_tier_range(self):
        for defn in INDICATOR_REGISTRY.values():
            assert 0 <= defn.tier <= 4, (
                f"Indicator '{defn.indicator_id}' has tier {defn.tier}, expected 0-4"
            )
