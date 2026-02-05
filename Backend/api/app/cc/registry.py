"""
Champion/Challenger Framework - Registry
========================================

Variant creation, one-diff enforcement, and weekly cadence checks.

Key responsibilities:
- Create variants (Baseline, Champion, Challenger)
- Enforce one-module-different rule
- Enforce weekly cadence (max 1 change per week per setup)
- Validate identifiers (no colons in IDs)

Canon compliance:
- One-module-different: Challenger differs from Champion by exactly one module
- Weekly cadence: Max 1 change per week per setup
- Baseline always runs: Dead-simple variant always evaluated for reference
"""

import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional

from .models import (
    Variant, VariantStatus, VariantModule,
    Module, ModuleKind, DiffType,
    CadenceViolation, ActiveChallengerExists, OneModuleDifferentViolation,
    InvalidIdentifierError,
    FORBIDDEN_ID_CHARS, CADENCE_SECONDS,
)
from .repository import CCRepository

logger = logging.getLogger(__name__)


# =============================================================================
# IDENTIFIER VALIDATION (v8: No colons in identifiers)
# =============================================================================

def validate_identifier(value: str, field_name: str) -> str:
    """
    Validate that an identifier doesn't contain forbidden characters.

    v8: Called at creation time for setup_id, variant_id, symbol, timeframe.

    Args:
        value: The identifier value to validate
        field_name: Name of the field (for error messages)

    Returns:
        The validated value (unchanged)

    Raises:
        InvalidIdentifierError: If identifier contains forbidden characters
    """
    if not value:
        raise InvalidIdentifierError(f"{field_name} cannot be empty")

    found_forbidden = FORBIDDEN_ID_CHARS & set(value)
    if found_forbidden:
        raise InvalidIdentifierError(
            f"{field_name} '{value}' contains forbidden character(s): {found_forbidden}. "
            f"Colons are not allowed because they're used as key delimiters."
        )

    return value


def validate_all_identifiers(
    setup_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> None:
    """
    Validate all provided identifiers.

    v8: Call this at variant creation, run creation, and signal recording.
    """
    if setup_id is not None:
        validate_identifier(setup_id, "setup_id")
    if variant_id is not None:
        validate_identifier(variant_id, "variant_id")
    if symbol is not None:
        validate_identifier(symbol, "symbol")
    if timeframe is not None:
        validate_identifier(timeframe, "timeframe")


# =============================================================================
# DIFF COUNTING
# =============================================================================

def count_module_differences(
    champion_modules: dict[str, VariantModule],
    challenger_modules: dict[str, VariantModule],
) -> int:
    """
    Count module differences between Champion and Challenger.

    Rules:
    - Different module_id = 1 diff
    - Same module_id but different params = 1 diff
    - Missing module = 1 diff
    """
    diff_count = 0

    all_kinds = set(champion_modules.keys()) | set(challenger_modules.keys())

    for kind in all_kinds:
        champ = champion_modules.get(kind)
        chall = challenger_modules.get(kind)

        if champ is None or chall is None:
            diff_count += 1
        elif champ.module_id != chall.module_id:
            diff_count += 1
        elif champ.params != chall.params:
            diff_count += 1

    return diff_count


# =============================================================================
# VERSION INCREMENTING
# =============================================================================

def increment_version(
    parent_variant_id: str,
    repository: CCRepository,
) -> tuple[int, int, int]:
    """
    Increment version based on parent variant.

    For now, simple patch increment. Future: smarter versioning.
    """
    parent = repository.get_variant(parent_variant_id)
    if not parent:
        return (1, 0, 0)

    # Simple patch increment
    return (parent.version_major, parent.version_minor, parent.version_patch + 1)


def build_variant_id(setup_id: str, major: int, minor: int, patch: int) -> str:
    """Build a variant ID from components."""
    return f"{setup_id}@{major}.{minor}.{patch}"


# =============================================================================
# VARIANT CREATION
# =============================================================================

class VariantRegistry:
    """
    Registry for creating and managing variants.

    Enforces:
    - One-module-different rule
    - Weekly cadence
    - No active challenger before creating new one
    - Valid identifiers (no colons)
    """

    def __init__(self, repository: CCRepository):
        self.repo = repository

    def create_baseline(
        self,
        setup_id: str,
        modules: dict[str, VariantModule],
        notes: Optional[str] = None,
        created_by: str = "system",
    ) -> Variant:
        """
        Create a baseline variant.

        The baseline is a dead-simple variant that runs forever for reference.
        There can only be one active baseline per setup.
        """
        # Validate identifiers
        validate_identifier(setup_id, "setup_id")

        # Check for existing baseline
        existing = self.repo.get_baseline(setup_id)
        if existing:
            logger.info(f"Retiring existing baseline {existing.variant_id}")
            self.repo.update_variant_status(existing.variant_id, VariantStatus.RETIRED)

        # Build variant ID
        variant_id = f"{setup_id}@baseline"
        validate_identifier(variant_id, "variant_id")

        # Create variant
        variant = Variant(
            variant_id=variant_id,
            setup_id=setup_id,
            status=VariantStatus.BASELINE,
            version_major=0,
            version_minor=0,
            version_patch=0,
            notes=notes,
            created_by=created_by,
            modules=modules,
        )

        self.repo.create_variant(variant)
        logger.info(f"Created baseline variant {variant_id}")

        return variant

    def create_champion(
        self,
        setup_id: str,
        modules: dict[str, VariantModule],
        notes: Optional[str] = None,
        created_by: str = "system",
    ) -> Variant:
        """
        Create a champion variant.

        The champion is the current best-performing variant.
        Creating a new champion retires the old one.
        """
        # Validate identifiers
        validate_identifier(setup_id, "setup_id")

        # Check for existing champion
        existing = self.repo.get_champion(setup_id)
        if existing:
            logger.info(f"Retiring existing champion {existing.variant_id}")
            self.repo.update_variant_status(existing.variant_id, VariantStatus.RETIRED)

        # Determine version
        if existing:
            version = increment_version(existing.variant_id, self.repo)
        else:
            version = (1, 0, 0)

        # Build variant ID
        variant_id = build_variant_id(setup_id, *version)
        validate_identifier(variant_id, "variant_id")

        # Create variant
        variant = Variant(
            variant_id=variant_id,
            setup_id=setup_id,
            status=VariantStatus.CHAMPION,
            parent_variant_id=existing.variant_id if existing else None,
            version_major=version[0],
            version_minor=version[1],
            version_patch=version[2],
            notes=notes,
            created_by=created_by,
            modules=modules,
        )

        self.repo.create_variant(variant)
        logger.info(f"Created champion variant {variant_id}")

        return variant

    def create_challenger_from_champion(
        self,
        champion_variant_id: str,
        diff_type: DiffType,
        diff_module_kind: ModuleKind,
        diff_key: str,
        new_module_id: Optional[str] = None,
        new_params: Optional[dict] = None,
        notes: Optional[str] = None,
        created_by: str = "system",
    ) -> Variant:
        """
        Create a Challenger from a Champion.

        Enforces:
        1. One-module-different rule (exactly 1 diff)
        2. Weekly cadence (max 1 change per week per setup)
        3. No existing active challenger for this setup

        Args:
            champion_variant_id: The champion to base challenger on
            diff_type: Type of change (MODULE, PARAM_BUNDLE, SCORE_COMPONENT)
            diff_module_kind: Which module slot is being changed
            diff_key: Descriptive pointer (e.g., "EXIT.trailing")
            new_module_id: For MODULE type - the new module ID
            new_params: For PARAM_BUNDLE or SCORE_COMPONENT - the new params
            notes: Optional notes about the challenger
            created_by: Who created this variant

        Returns:
            The new challenger variant

        Raises:
            CadenceViolation: If <7 days since last change
            ActiveChallengerExists: If setup already has active challenger
            OneModuleDifferentViolation: If diff count != 1
        """
        # Get champion
        champion = self.repo.get_variant(champion_variant_id)
        if not champion:
            raise ValueError(f"Champion {champion_variant_id} not found")

        setup_id = champion.setup_id

        # =======================================================================
        # Enforcement 1: Weekly cadence check
        # Use total_seconds() to avoid truncation issues
        # =======================================================================
        last_change = self.repo.get_last_change(setup_id)
        if last_change:
            now = datetime.now(timezone.utc)
            elapsed = now - last_change.created_at
            seconds_since = elapsed.total_seconds()

            if seconds_since < CADENCE_SECONDS:
                remaining_seconds = CADENCE_SECONDS - seconds_since
                remaining_hours = remaining_seconds / 3600
                remaining_days = remaining_hours / 24

                # Format remaining time appropriately
                if remaining_hours < 24:
                    wait_msg = f"{remaining_hours:.1f} hours"
                else:
                    wait_msg = f"{remaining_days:.1f} days"

                raise CadenceViolation(
                    f"Weekly cadence violated: last change was {seconds_since / 86400:.1f} days ago "
                    f"(variant {last_change.variant_id}). Wait {wait_msg}."
                )

        # =======================================================================
        # Enforcement 2: No existing active challenger
        # =======================================================================
        active_challenger = self.repo.get_active_challenger(setup_id)
        if active_challenger:
            raise ActiveChallengerExists(
                f"Setup {setup_id} already has active challenger: {active_challenger.variant_id}"
            )

        # =======================================================================
        # Load Champion's module map
        # =======================================================================
        champion_modules = champion.modules

        # =======================================================================
        # Apply the single change
        # =======================================================================
        challenger_modules = deepcopy(champion_modules)

        module_kind_str = diff_module_kind.value
        if module_kind_str not in challenger_modules:
            raise ValueError(f"Module kind {diff_module_kind.value} not in Champion")

        if diff_type == DiffType.MODULE:
            if not new_module_id:
                raise ValueError("new_module_id required for MODULE diff_type")
            # Swap the module_id at this slot
            challenger_modules[module_kind_str].module_id = new_module_id

        elif diff_type == DiffType.PARAM_BUNDLE:
            if not new_params:
                raise ValueError("new_params required for PARAM_BUNDLE diff_type")
            # Update params for this module (entire param change = 1 "module change")
            challenger_modules[module_kind_str].params = new_params

        elif diff_type == DiffType.SCORE_COMPONENT:
            if not new_params:
                raise ValueError("new_params required for SCORE_COMPONENT diff_type")
            if diff_module_kind != ModuleKind.SCORE:
                raise ValueError("SCORE_COMPONENT diff_type requires diff_module_kind=SCORE")
            # For SCORE module kind, update scoring weights
            challenger_modules[module_kind_str].params.update(new_params)

        # =======================================================================
        # Enforcement 3: Validate exactly one difference
        # =======================================================================
        diff_count = count_module_differences(champion_modules, challenger_modules)
        if diff_count != 1:
            raise OneModuleDifferentViolation(
                f"One-module-different rule violated: {diff_count} differences detected"
            )

        # =======================================================================
        # Persist new variant
        # =======================================================================
        version = increment_version(champion_variant_id, self.repo)
        variant_id = build_variant_id(setup_id, *version)

        # Validate variant ID
        validate_identifier(variant_id, "variant_id")

        diff_description = f"{diff_type.value} change to {diff_module_kind.value}: {diff_key}"

        variant = Variant(
            variant_id=variant_id,
            setup_id=setup_id,
            status=VariantStatus.CHALLENGER,
            parent_variant_id=champion_variant_id,
            diff_type=diff_type,
            diff_module_kind=diff_module_kind,
            diff_key=diff_key,
            diff_description=diff_description,
            version_major=version[0],
            version_minor=version[1],
            version_patch=version[2],
            notes=notes,
            created_by=created_by,
            modules=challenger_modules,
        )

        self.repo.create_variant(variant)

        # Record in change ledger
        self.repo.record_change(setup_id, variant_id, "CHALLENGER_CREATED")

        logger.info(f"Created challenger {variant_id} from champion {champion_variant_id}")
        return variant

    def retire_variant(self, variant_id: str) -> bool:
        """Retire a variant (make it inactive)."""
        return self.repo.update_variant_status(variant_id, VariantStatus.RETIRED)

    def promote_challenger_to_champion(self, variant_id: str) -> bool:
        """
        Promote a challenger to champion.

        This should only be called after promotion gates pass.
        See promotion.py for gate checks.
        """
        variant = self.repo.get_variant(variant_id)
        if not variant:
            raise ValueError(f"Variant {variant_id} not found")

        if variant.status != VariantStatus.CHALLENGER:
            raise ValueError(f"Variant {variant_id} is not a challenger")

        setup_id = variant.setup_id

        # Retire old champion
        old_champion = self.repo.get_champion(setup_id)
        if old_champion:
            self.repo.update_variant_status(old_champion.variant_id, VariantStatus.RETIRED)

        # Promote challenger to champion
        self.repo.update_variant_status(variant_id, VariantStatus.CHAMPION)

        # Record in change ledger
        self.repo.record_change(setup_id, variant_id, "PROMOTED")

        logger.info(f"Promoted {variant_id} to champion")
        return True


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_initial_modules_from_setup(
    setup_id: str,
    setup_version: str,
    params: Optional[dict] = None,
) -> dict[str, VariantModule]:
    """
    Create initial module map from a setup configuration.

    This is a convenience function for creating the first champion/baseline.
    For now, creates placeholder modules. Future: integrate with setup registry.
    """
    params = params or {}

    # Create placeholder modules for each kind
    modules = {}
    for kind in ModuleKind:
        module = VariantModule(
            module_kind=kind,
            module_id=f"{setup_id}.{kind.value.lower()}.v{setup_version}",
            params=params.get(kind.value, {}),
        )
        modules[kind.value] = module

    return modules
