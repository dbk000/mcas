"""
EXTRACTED PURE LOGIC FROM: WRK_APP_indicator_bundles.py
V5 Updates: Full Quarantine enforced.
have been physically replaced to satisfy the zero-contamination mandate.
Dataclass imports are explicitly injected to guarantee semantics.
"""

from dataclasses import dataclass, field, asdict

from dataclasses import dataclass, field

from enum import Enum

class BundleRole(str, Enum):
    """Functional role of an indicator within a setup's bundle."""
    PRIMARY = "primary"            # Drives the signal
    CONFIRMATION = "confirmation"  # Reduces false positives
    FILTER = "filter"              # Hard veto gate
    DIVERSIFICATION = "diversification"  # Uncorrelated edge
    CONTEXT = "context"            # Regime/bias (not scored)

@dataclass(frozen=True)
class IndicatorBinding:
    """Maps an indicator to its role within a specific setup."""
    indicator_id: str       # e.g. "DORMANCY_SCORE_48"
    role: BundleRole
    snapshot_field: str      # IndicatorSnapshot field name
    notes: str = ""          # Why this indicator serves this role

@dataclass(frozen=True)
class BundleSpec:
    """Complete indicator bundle for one setup."""
    setup_id: str
    version: str
    bindings: tuple[IndicatorBinding, ...]  # frozen=True needs tuple not list

    @property
    def by_role(self) -> dict[BundleRole, list[IndicatorBinding]]:
        """Group bindings by role."""
        result: dict[BundleRole, list[IndicatorBinding]] = {r: [] for r in BundleRole}
        for b in self.bindings:
            result[b.role].append(b)
        return result

    @property
    def indicator_ids(self) -> set[str]:
        """All indicator IDs in this bundle."""
        return {b.indicator_id for b in self.bindings}

    @property
    def snapshot_fields(self) -> set[str]:
        """All IndicatorSnapshot fields used by this bundle."""
        return {b.snapshot_field for b in self.bindings}

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [f"BundleSpec: {self.setup_id} v{self.version}"]
        for role, bindings in self.by_role.items():
            if bindings:
                ids = ", ".join(b.indicator_id for b in bindings)
                lines.append(f"  {role.value}: {ids}")
        return "\n".join(lines)

D1_BUNDLE = BundleSpec(
    setup_id="D1",
    version="1.0",
    bindings=(
        IndicatorBinding("DORMANCY_SCORE_48", BundleRole.PRIMARY, "dormancy_score",
                         "Core signal: fraction of bars with tight range"),
        IndicatorBinding("BB_WIDTH", BundleRole.PRIMARY, "bb_width",
                         "Compression detection: narrow bands = dormancy"),
        IndicatorBinding("VOLUME_Z_20", BundleRole.CONFIRMATION, "volume_z",
                         "Breakout confirmation: volume surge at trigger"),
        IndicatorBinding("ACCUM_DIVERGENCE_20", BundleRole.CONFIRMATION, "accum_divergence",
                         "Smart money: ADL rising while price flat"),
        IndicatorBinding("ATR_14", BundleRole.FILTER, "atr",
                         "Stop distance calculation + ATR normalization"),
        IndicatorBinding("DONCHIAN_HIGH_20", BundleRole.FILTER, "donchian_high_20",
                         "Compression box high boundary"),
        IndicatorBinding("DONCHIAN_LOW_20", BundleRole.FILTER, "donchian_low_20",
                         "Compression box low boundary"),
        IndicatorBinding("BB_UB", BundleRole.CONTEXT, "bb_ub",
                         "Upper band for expansion detection"),
        IndicatorBinding("BB_LB", BundleRole.CONTEXT, "bb_lb",
                         "Lower band for expansion detection"),
        IndicatorBinding("RVOL_20", BundleRole.CONFIRMATION, "rvol",
                         "Relative volume ratio at trigger"),
        IndicatorBinding("SMA50_SLOPE_ATR", BundleRole.CONTEXT, "sma50_slope_atr",
                         "Trend context: slope direction for regime"),
    ),
)

S9_BUNDLE = BundleSpec(
    setup_id="S9",
    version="1.0",
    bindings=(
        IndicatorBinding("OHLCV", BundleRole.PRIMARY, "ohlcv",
                         "Raw candles — S9 computes returns internally"),
    ),
)

S10_BUNDLE = BundleSpec(
    setup_id="S10",
    version="1.0",
    bindings=(
        IndicatorBinding("MACD_12_26_9", BundleRole.PRIMARY, "macd_histogram",
                         "MACD histogram crossover drives entry"),
        IndicatorBinding("MACD_12_26_9", BundleRole.PRIMARY, "macd_signal",
                         "MACD signal line for crossover detection"),
        IndicatorBinding("MACD_12_26_9", BundleRole.PRIMARY, "macd_line",
                         "MACD line value"),
        IndicatorBinding("SMA_20", BundleRole.CONFIRMATION, "sma_20",
                         "Short-term trend alignment"),
        IndicatorBinding("SMA_200", BundleRole.FILTER, "sma_200",
                         "Long-term trend direction gate"),
        IndicatorBinding("ATR_14", BundleRole.FILTER, "atr",
                         "Stop distance calculation"),
        IndicatorBinding("VOLUME_Z_20", BundleRole.CONFIRMATION, "volume_z",
                         "Volume confirmation at entry"),
    ),
)

U1_BUNDLE = BundleSpec(
    setup_id="U1",
    version="1.0",
    bindings=(
        IndicatorBinding("VOLUME_Z_20", BundleRole.PRIMARY, "volume_z",
                         "Volume surge detection (internal vol_z_1d gate)"),
        IndicatorBinding("CLV", BundleRole.CONFIRMATION, "clv",
                         "Close location value — bullish position in bar"),
        IndicatorBinding("BB_WIDTH", BundleRole.CONFIRMATION, "bb_width",
                         "Volatility expansion gate (5m/15m thresholds)"),
        IndicatorBinding("ATR_14", BundleRole.FILTER, "atr",
                         "Volatility-envelope stop calculation"),
        IndicatorBinding("OHLCV", BundleRole.PRIMARY, "ohlcv",
                         "Raw candles — U1 computes most features internally"),
    ),
)

D2_BUNDLE = BundleSpec(
    setup_id="D2",
    version="1.0",
    bindings=(
        IndicatorBinding("OHLCV", BundleRole.CONTEXT, "ohlcv",
                         "1h OHLCV for HWM tracking and exit simulation"),
        # D2's primary signal comes from WhaleBotPumps cluster detection,
        # not from IndicatorSnapshot. No indicator bindings.
    ),
)

BUNDLE_REGISTRY: dict[str, BundleSpec] = {
    b.setup_id: b for b in [D1_BUNDLE, S9_BUNDLE, S10_BUNDLE, U1_BUNDLE, D2_BUNDLE]
}

def get_bundle(setup_id: str) -> BundleSpec | None:
    """Get the BundleSpec for a setup, or None if not registered."""
    return BUNDLE_REGISTRY.get(setup_id)

def get_all_active_indicators() -> set[str]:
    """Get the union of all indicator IDs across all active bundles."""
    ids: set[str] = set()
    for bundle in BUNDLE_REGISTRY.values():
        ids.update(bundle.indicator_ids)
    return ids

def get_dark_indicators() -> set[str]:
    """Get indicators that are registered but not used by any active setup.

    These are candidates for dark-mode computation — they exist in
    INDICATOR_REGISTRY but no setup's BundleSpec references them.
    """
    from indicators import INDICATOR_REGISTRY
    all_registered = set(INDICATOR_REGISTRY.keys())
    all_active = get_all_active_indicators()
    return all_registered - all_active

def print_bundle_report():
    """Print a full report of all bundles and dark indicators."""
    lines = ["=" * 60, "INDICATOR BUNDLE REPORT", "=" * 60, ""]

    for setup_id, bundle in sorted(BUNDLE_REGISTRY.items()):
        lines.append(bundle.summary())
        lines.append("")

    dark = get_dark_indicators()
    lines.append(f"Dark indicators ({len(dark)} — registered but unused):")
    for ind_id in sorted(dark):
        lines.append(f"  {ind_id}")

    return "\n".join(lines)

