"""Cap=3 selection pipeline.

Composes four sprint-produced gates into a single callable that decides whether
a C-series signal is live-tradable at ``max_concurrent=3``:

    1. Indicator SAFE filter  (Backend/governance/safe_filters/indicator_safe_filters_v1.json)
    2. Social SAFE filter     (Backend/governance/safe_filters/social_safe_filters_v1.json)
    3. Correlation-cluster cap (Backend/governance/clusters/correlation_clusters_v1.json)
    4. Regime enforcer         (Backend/governance/regime/asset_beta_table_binance_v1.json)

Gates evaluate in order. The pipeline short-circuits on the first reject.

Design principles
-----------------
* Pure / testable: no DB, no network, no globals beyond loaded artefacts.
* Artefacts are re-read on mtime change so A2-RESCOPE social filter refresh
  does NOT require a process restart.
* ``evaluate()`` latency is dominated by dict lookups (<5ms expected).
* ``evaluate_batch()`` processes a chronologically ordered list of signals,
  evolving the open-positions list on each ACCEPT, so that historical
  backfill sims see realistic cluster occupancy.

Wiring into ``signal_worker.py`` is deliberately deferred to D7 — this module
is pure and has no side-effects on the Redis/Postgres pipeline.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

# --------------------------------------------------------------------------- #
# Canonical artefact paths (resolved lazily — the defaults work from repo root
# and inside the Docker worker where /app/ is mounted).
# --------------------------------------------------------------------------- #
# __file__ = .../MCAS/Backend/governance/selection/cap3_pipeline.py
# parents[0] = selection, parents[1] = governance, parents[2] = Backend
_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # .../MCAS/Backend
_GOV_ROOT = _BACKEND_ROOT / "governance"

DEFAULT_INDICATOR_FILTERS = str(_GOV_ROOT / "safe_filters" / "indicator_safe_filters_v1.json")
DEFAULT_SOCIAL_FILTERS = str(_GOV_ROOT / "safe_filters" / "social_safe_filters_v1.json")
DEFAULT_SOCIAL_MODULATORS = str(_GOV_ROOT / "safe_filters" / "social_size_modulators_v1.json")
DEFAULT_CLUSTERS = str(_GOV_ROOT / "clusters" / "correlation_clusters_v1.json")
DEFAULT_BETA_TABLE = str(_GOV_ROOT / "regime" / "asset_beta_table_binance_v1.json")

# Re-read artefact files if mtime changes more than this many seconds ago.
# Acts as a cheap TTL so social_safe_filters_v1.json refresh by A2-RESCOPE
# propagates without a restart.
_MTIME_CHECK_INTERVAL_S = 5.0


# --------------------------------------------------------------------------- #
# Public data classes
# --------------------------------------------------------------------------- #
RegimeState = Literal["NORMAL", "ELEVATED", "SHOCK"]
Verdict = Literal["ACCEPT", "REJECT", "QUEUE_CLUSTER_FULL"]
GateName = Literal["indicator_safe", "social_safe", "cluster_cap", "regime_enforcer"]


@dataclass(frozen=True)
class Signal:
    """A C-series signal candidate — the input to the cap=3 pipeline.

    ``payload`` mirrors the ``opportunities.payload_json`` shape — it holds
    indicator values at trigger (rsi_14, atr, macd_line, quote_volume_24h_usd,
    etc.) that the indicator SAFE filter tests against.
    """
    opportunity_id: str
    setup_id: str
    symbol: str
    created_ts: int  # epoch seconds UTC
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineDecision:
    """The outcome of running a ``Signal`` through all four gates."""
    opportunity_id: str
    verdict: Verdict
    gate_rejecting: Optional[GateName]
    reject_reason: Optional[str]
    cluster_id: Optional[int]
    size_multiplier: float
    audit: Dict[str, Any]


# --------------------------------------------------------------------------- #
# Artefact loader — re-reads on mtime change, TTL-protected so we don't stat
# on every signal.
# --------------------------------------------------------------------------- #
class _MtimeCachedJSON:
    """Loads a JSON file and re-loads if its mtime has changed.

    Checks mtime at most once every ``_MTIME_CHECK_INTERVAL_S`` seconds to
    avoid stat-storming when ``get()`` is hot.
    """

    def __init__(self, path: str):
        self._path = path
        self._data: Any = None
        self._loaded_mtime: float = 0.0
        self._last_stat: float = 0.0
        self._lock = threading.Lock()

    def get(self) -> Any:
        now = time.time()
        with self._lock:
            if self._data is None or (now - self._last_stat) > _MTIME_CHECK_INTERVAL_S:
                self._last_stat = now
                try:
                    mtime = os.path.getmtime(self._path)
                except FileNotFoundError:
                    if self._data is None:
                        raise
                    return self._data
                if mtime != self._loaded_mtime or self._data is None:
                    with open(self._path, "r") as f:
                        self._data = json.load(f)
                    self._loaded_mtime = mtime
            return self._data


# --------------------------------------------------------------------------- #
# Operator evaluation
# --------------------------------------------------------------------------- #
_OP_FN = {
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# --------------------------------------------------------------------------- #
# Feature-family map for "grouped_and" composition mode.
#
# Background (D6-REDO 2026-04-12): D6's first run produced DO_NOT_SHIP because
# C4M's 7 shortlisted indicator filters were AND-combined — but 5 of the 7 were
# all measuring the same thing ("is momentum negative?" via macd_line,
# macd_signal, macd_histogram, macd_prev_histogram, rsi_slope). AND-combining
# correlated filters compounds exclusion on the same condition rather than
# adding selectivity, so 91.7% of C4M signals were killed by the gate.
#
# Fix: group shortlisted filters by feature family, keep the best filter from
# each group (highest DSR; tiebreak walk_forward_PF_post), then AND the
# group-winners. This produces 2-3 orthogonal filters per strategy instead of
# 7 redundant ones. Features not in the map are treated as their own family.
# --------------------------------------------------------------------------- #
FEATURE_FAMILIES: Dict[str, set] = {
    "MACD":     {"macd_line", "macd_signal", "macd_histogram", "macd_prev_histogram"},
    "RSI":      {"rsi", "rsi_sma", "rsi_14", "rsi_slope"},
    "ATR":      {"atr", "atr_pct"},
    "BB":       {"bb_width", "bb_upper", "bb_lower", "bb_pct"},
    "VOLUME":   {"quote_volume_24h_usd", "volume_z", "volume_ma", "volume_24h"},
    "MOMENTUM": {"momentum", "roc", "velocity"},
}


# --------------------------------------------------------------------------- #
# Extended family aliases for `combo2fam:FAM1|FAM2` composition mode.
#
# The `grouped_and` mode uses the base FEATURE_FAMILIES above (preserving the
# existing ATR / BB distinction that tests 14/15 depend on). The combo2fam
# mode accepts a broader set of semantic family names — VOLATILITY bundles
# ATR + BB, TREND bundles the slope/MA features, SOCIAL is a placeholder that
# matches nothing in the indicator gate (social filters live on their own
# gate). When the set of valid combo2fam families is extended, update
# ``_COMBO2FAM_VALID_FAMS`` in tandem with this map.
# --------------------------------------------------------------------------- #
_COMBO2FAM_FAMILY_MEMBERS: Dict[str, set] = {
    "MACD":       set(FEATURE_FAMILIES["MACD"]),
    "RSI":        set(FEATURE_FAMILIES["RSI"]),
    "VOLATILITY": set(FEATURE_FAMILIES["ATR"]) | set(FEATURE_FAMILIES["BB"]),
    "TREND":      {
        "sma50_slope_atr", "sma200_slope_atr",
        "ema20_slope_atr", "ema50_slope_atr",
        "ma_gap_atr",
    },
    "VOLUME":     set(FEATURE_FAMILIES["VOLUME"]),
    # SOCIAL is intentionally empty at the indicator-gate level — any SOCIAL
    # evidence lives in the social_safe gate (gate 2). Listing SOCIAL in a
    # combo2fam spec is accepted syntactically so the same composition-mode
    # string can be reused if/when an indicator-side social feature lands,
    # but today it contributes no filter to the indicator AND-stack.
    "SOCIAL":     set(),
}

_COMBO2FAM_VALID_FAMS = frozenset(_COMBO2FAM_FAMILY_MEMBERS.keys())


def _filters_in_combo2fam_family(
    filters: List[Dict[str, Any]], fam: str
) -> List[Dict[str, Any]]:
    """Return the subset of ``filters`` whose feature belongs to ``fam``.

    Membership is defined by ``_COMBO2FAM_FAMILY_MEMBERS[fam]``. A filter
    with a feature not in any combo2fam family membership is excluded from
    every bucket — combo2fam is purposefully narrower than grouped_and's
    singleton-fallback; families you don't list are skipped entirely.
    """
    if fam not in _COMBO2FAM_FAMILY_MEMBERS:
        raise ValueError(f"unknown combo2fam family: {fam!r}")
    members = _COMBO2FAM_FAMILY_MEMBERS[fam]
    return [f for f in filters if f.get("feature", "") in members]


def _combo2fam_winners(
    filters: List[Dict[str, Any]], fams: List[str]
) -> List[Dict[str, Any]]:
    """Return the AND-stack for combo2fam mode: best-DSR winner per listed family.

    Semantics (per spec):
      * For each listed family, select the highest-DSR filter (breaking ties
        via walk_forward_PF_post, then alphabetical variant_id — reuses
        :func:`_top_dsr_filter`).
      * If a listed family has no filter for this strategy, skip it (do NOT
        fail — empty family is permissive).
      * If BOTH listed families resolve to an empty filter list, the gate
        auto-passes. Returning ``[]`` achieves that — the caller's
        ``not filters`` branch in ``_gate_indicator_safe`` auto-passes.
    """
    out: List[Dict[str, Any]] = []
    seen_variant_ids: set = set()
    for fam in fams:
        fam_filters = _filters_in_combo2fam_family(filters, fam)
        if not fam_filters:
            continue
        winner = _top_dsr_filter(fam_filters)
        if not winner:
            continue
        vid = winner[0].get("variant_id") or ""
        # Defensive: if two listed families share the same filter (shouldn't
        # happen because membership sets are disjoint), dedup by variant_id.
        if vid in seen_variant_ids:
            continue
        seen_variant_ids.add(vid)
        out.extend(winner)
    return out


def _parse_combo2fam_mode(mode: str) -> List[str]:
    """Parse ``combo2fam:FAM1|FAM2`` into ``["FAM1", "FAM2"]``.

    Accepts any number of |-separated families (≥1). Each must be a member
    of :data:`_COMBO2FAM_VALID_FAMS`. Raises ValueError for syntactic or
    semantic errors — callers validate at Cap3Pipeline init time so a
    misconfigured env var fails loud at worker boot.
    """
    if not mode.startswith("combo2fam:"):
        raise ValueError(f"not a combo2fam mode: {mode!r}")
    spec = mode[len("combo2fam:"):]
    if not spec:
        raise ValueError(f"combo2fam mode missing family list: {mode!r}")
    fams = [f.strip().upper() for f in spec.split("|") if f.strip()]
    if not fams:
        raise ValueError(f"combo2fam mode has no valid families: {mode!r}")
    bad = [f for f in fams if f not in _COMBO2FAM_VALID_FAMS]
    if bad:
        raise ValueError(
            f"combo2fam mode references unknown families {bad!r}; "
            f"valid: {sorted(_COMBO2FAM_VALID_FAMS)}"
        )
    return fams


def _is_valid_composition_mode(mode: str) -> bool:
    """Accept the three base modes plus any well-formed combo2fam:... spec."""
    if mode in ("and_all", "grouped_and", "top_dsr_only"):
        return True
    if mode.startswith("combo2fam:"):
        try:
            _parse_combo2fam_mode(mode)
            return True
        except ValueError:
            return False
    return False


def _family_for_feature(feature: str) -> str:
    """Return the family key for a feature, or the feature name itself if unknown.

    Unknown features become their own singleton family so they survive the
    group-winner selection (they have no peers to compete with).
    """
    for fam, members in FEATURE_FAMILIES.items():
        if feature in members:
            return fam
    return feature  # singleton family


def _group_winners(filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group ``filters`` by feature family and return one winner per family.

    Winner = max(DSR, walk_forward_PF_post). Filters missing DSR are treated
    as DSR=-inf so they only win if no peer has DSR. Stable ordering preserves
    determinism across runs.
    """
    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for f in filters:
        fam = _family_for_feature(f.get("feature", ""))
        by_family.setdefault(fam, []).append(f)

    winners: List[Dict[str, Any]] = []
    for fam in sorted(by_family.keys()):
        candidates = by_family[fam]
        # max by (DSR, walk_forward_PF_post); missing → -inf
        def _key(d: Dict[str, Any]) -> Tuple[float, float]:
            dsr = d.get("DSR")
            pf = d.get("walk_forward_PF_post")
            return (
                float(dsr) if isinstance(dsr, (int, float)) else float("-inf"),
                float(pf) if isinstance(pf, (int, float)) else float("-inf"),
            )
        winner = max(candidates, key=_key)
        winners.append(winner)
    return winners


def _top_dsr_filter(filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a list containing only the single highest-DSR filter.

    Tiebreakers (in order): highest ``DSR``, highest ``walk_forward_PF_post``,
    alphabetical ``variant_id``. Missing DSR/PF treated as -inf.

    Rationale: the top_dsr_only composition mode tests whether the per-filter
    edge translates to live firing rate without being starved by AND-stack
    composition. With a single filter there is no correlation-drag between
    filters — the hypothesis: are all composition modes losing the edge to
    composition starvation, or does the per-filter edge fail to materialise
    in live backfill?
    """
    if not filters:
        return []

    def _key(d: Dict[str, Any]) -> Tuple[float, float, str]:
        dsr = d.get("DSR")
        pf = d.get("walk_forward_PF_post")
        vid = d.get("variant_id") or ""
        # alphabetical ascending => negate string ordering by returning
        # max over (DSR, PF) but min over variant_id. We'll use a single
        # max() with a key that flips variant_id sort direction.
        return (
            float(dsr) if isinstance(dsr, (int, float)) else float("-inf"),
            float(pf) if isinstance(pf, (int, float)) else float("-inf"),
            # Alphabetical tiebreak: the *smaller* variant_id wins → encode
            # as negative sort via a translated string. Simplest: bubble up
            # via a secondary sort pass.
            "",  # placeholder, real tiebreak handled below
        )

    # First find max DSR+PF; then among ties, pick smallest variant_id alphabetical.
    best_dsr_pf = max(filters, key=lambda d: (
        float(d.get("DSR")) if isinstance(d.get("DSR"), (int, float)) else float("-inf"),
        float(d.get("walk_forward_PF_post")) if isinstance(d.get("walk_forward_PF_post"), (int, float)) else float("-inf"),
    ))
    best_dsr = float(best_dsr_pf.get("DSR")) if isinstance(best_dsr_pf.get("DSR"), (int, float)) else float("-inf")
    best_pf = float(best_dsr_pf.get("walk_forward_PF_post")) if isinstance(best_dsr_pf.get("walk_forward_PF_post"), (int, float)) else float("-inf")
    ties = [
        f for f in filters
        if (float(f.get("DSR")) if isinstance(f.get("DSR"), (int, float)) else float("-inf")) == best_dsr
        and (float(f.get("walk_forward_PF_post")) if isinstance(f.get("walk_forward_PF_post"), (int, float)) else float("-inf")) == best_pf
    ]
    if len(ties) > 1:
        ties.sort(key=lambda d: d.get("variant_id") or "")
        return [ties[0]]
    return [best_dsr_pf]


def _eval_comparison(value: float, op: str, threshold: float) -> bool:
    fn = _OP_FN.get(op)
    if fn is None:
        raise ValueError(f"unknown operator: {op!r}")
    return fn(value, threshold)


def _is_numeric(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# --------------------------------------------------------------------------- #
# The pipeline
# --------------------------------------------------------------------------- #
class Cap3Pipeline:
    """The single callable that runs the four gates for a cap=3 autotrader.

    Parameters
    ----------
    indicator_filters_path : str
        Path to ``indicator_safe_filters_v1.json``.
    social_filters_path : str
        Path to ``social_safe_filters_v1.json``. Re-read on mtime change to
        accommodate the imminent A2-RESCOPE refresh.
    social_modulators_path : str
        Path to ``social_size_modulators_v1.json``.
    clusters_path : str
        Path to ``correlation_clusters_v1.json``.
    beta_table_path : str
        Path to ``asset_beta_table_binance_v1.json`` (class_4h is consulted).
    max_cap : int
        Portfolio cap (default 3). Not enforced inside the pipeline itself —
        the caller is expected to slice the whitelist to ``max_cap`` ACCEPTs.
        Documented here for traceability.
    max_per_cluster : int
        Cluster cap (default 1). If ``len(open_positions in signal's cluster)
        >= max_per_cluster``, verdict is QUEUE_CLUSTER_FULL.
    regime_state_source : Callable[[], RegimeState]
        Returns current regime — NORMAL, ELEVATED, or SHOCK. The integration
        layer injects a Redis-backed reader at D7. Tests inject a stub.
    social_velocity_fn : Optional[Callable[[Signal, str], float]]
        Returns velocity_ratio for (signal, category) in [created_ts-24h,
        created_ts]. If ``None``, the social SAFE gate treats every rule as
        "missing data" and permissive-passes it (honest reporting: don't reject
        on absent data).
    """

    def __init__(
        self,
        indicator_filters_path: str = DEFAULT_INDICATOR_FILTERS,
        social_filters_path: str = DEFAULT_SOCIAL_FILTERS,
        social_modulators_path: str = DEFAULT_SOCIAL_MODULATORS,
        clusters_path: str = DEFAULT_CLUSTERS,
        beta_table_path: str = DEFAULT_BETA_TABLE,
        max_cap: int = 3,
        max_per_cluster: int = 1,
        regime_state_source: Callable[[], RegimeState] = lambda: "NORMAL",
        social_velocity_fn: Optional[Callable[[Signal, str], float]] = None,
        filter_composition_mode: str = "grouped_and",
        per_strategy_mode: Optional[Dict[str, str]] = None,
    ):
        self._indicator_store = _MtimeCachedJSON(indicator_filters_path)
        self._social_store = _MtimeCachedJSON(social_filters_path)
        self._social_mod_store = _MtimeCachedJSON(social_modulators_path)
        self._clusters_store = _MtimeCachedJSON(clusters_path)
        self._beta_store = _MtimeCachedJSON(beta_table_path)

        self._cluster_map_cache: Optional[Tuple[float, Dict[str, int]]] = None
        self._cluster_map_lock = threading.Lock()

        self.max_cap = max_cap
        self.max_per_cluster = max_per_cluster
        self._regime_state_source = regime_state_source
        self._social_velocity_fn = social_velocity_fn

        if not _is_valid_composition_mode(filter_composition_mode):
            raise ValueError(
                f"filter_composition_mode must be 'and_all', 'grouped_and', "
                f"'top_dsr_only', or 'combo2fam:FAM1|FAM2'; got "
                f"{filter_composition_mode!r}"
            )
        self.filter_composition_mode = filter_composition_mode

        # Per-strategy composition mode overrides. A strategy listed here uses
        # its override; any strategy not listed falls back to
        # ``filter_composition_mode``. Validate each override at init so a
        # bad env var (e.g. CAP3_COMPOSITION_MODE_C4M=typo) fails loud instead
        # of silently degrading to the default.
        self._per_strategy_mode: Dict[str, str] = {}
        if per_strategy_mode:
            for sid, mode in per_strategy_mode.items():
                if mode is None:
                    continue
                # "parked" is a sentinel for strategies not live — we accept
                # it as a no-op (falls through to global default if the
                # strategy ever fires). This matches the spec's comment that
                # parked modes are "irrelevant but set for completeness".
                if mode == "parked":
                    continue
                if not _is_valid_composition_mode(mode):
                    raise ValueError(
                        f"per_strategy_mode[{sid!r}] has invalid mode {mode!r}; "
                        f"must be 'and_all', 'grouped_and', 'top_dsr_only', "
                        f"'combo2fam:FAM1|FAM2', or 'parked'"
                    )
                self._per_strategy_mode[sid] = mode

    def _mode_for_strategy(self, setup_id: str) -> str:
        """Resolve composition mode for ``setup_id``.

        Per-strategy override wins; otherwise fall back to the global default.
        Spec fallback: if no per-strategy entry is set, ``top_dsr_only`` is
        the recommended default — but that decision is made at the seam
        (signal_worker) when it constructs the pipeline. Here we simply honor
        whatever ``filter_composition_mode`` the caller passed in.
        """
        return self._per_strategy_mode.get(setup_id, self.filter_composition_mode)

    # ---- Artefact accessors (re-resolve via mtime cache) ------------------ #
    def _indicator_filters(self, setup_id: str) -> List[Dict[str, Any]]:
        data = self._indicator_store.get()
        by = data.get("filters_by_strategy", {}).get(setup_id, {})
        filters = by.get("filters", []) or []
        shortlisted = [f for f in filters if f.get("status") == "shortlisted"]
        mode = self._mode_for_strategy(setup_id)
        if mode == "grouped_and":
            return _group_winners(shortlisted)
        if mode == "top_dsr_only":
            return _top_dsr_filter(shortlisted)
        if mode.startswith("combo2fam:"):
            fams = _parse_combo2fam_mode(mode)
            return _combo2fam_winners(shortlisted, fams)
        return shortlisted

    def _social_filters(self, setup_id: str) -> List[Dict[str, Any]]:
        data = self._social_store.get()
        by = data.get("filters_by_strategy", {}).get(setup_id, {})
        filters = by.get("filters", []) or []
        mode = self._mode_for_strategy(setup_id)
        if mode == "top_dsr_only":
            return _top_dsr_filter(filters)
        # combo2fam / grouped_and / and_all: don't attempt family collapsing
        # on social filters — social filter "categories" (GEOPOLITICAL,
        # MONETARY, etc.) are already orthogonal by construction, so AND-ing
        # the full set is the intended semantics.
        return filters

    def _social_modulators(self, setup_id: str) -> List[Dict[str, Any]]:
        data = self._social_mod_store.get()
        by = data.get("modulators_by_strategy", {}).get(setup_id, {})
        return by.get("modulators", []) or []

    def _cluster_map(self) -> Dict[str, int]:
        """Return {symbol -> cluster_id}, cached by clusters-file mtime."""
        data = self._clusters_store.get()
        # Invalidate on identity change (cache key is the id of the dict
        # returned by the store; _MtimeCachedJSON returns the same object
        # until the file changes on disk).
        with self._cluster_map_lock:
            if (self._cluster_map_cache is not None
                    and self._cluster_map_cache[0] == id(data)):
                return self._cluster_map_cache[1]
            m: Dict[str, int] = {}
            for c in data.get("clusters", []):
                cid = c["cluster_id"]
                for sym in c.get("symbols", []):
                    m[sym] = cid
            self._cluster_map_cache = (id(data), m)
            return m

    def _beta_class_4h(self, symbol: str) -> Optional[str]:
        data = self._beta_store.get()
        s = data.get("symbols", {}).get(symbol)
        if not s:
            return None
        return s.get("class_4h")

    # ---- Gate 1: indicator SAFE ------------------------------------------ #
    def _gate_indicator_safe(self, signal: Signal) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """Evaluate all shortlisted indicator filters for this setup (AND-combined).

        Returns (passed, reject_reason, audit). If no shortlisted filter exists
        for this setup (e.g. C2SH/C4ML/C3M), the gate auto-passes.
        """
        filters = self._indicator_filters(signal.setup_id)
        audit: Dict[str, Any] = {
            "setup_id": signal.setup_id,
            "composition_mode": self._mode_for_strategy(signal.setup_id),
            "filters_evaluated": [],
            "auto_passed": False,
        }
        if not filters:
            audit["auto_passed"] = True
            audit["reason"] = "no shortlisted indicator filter for this strategy"
            return True, None, audit

        for f in filters:
            feature = f["feature"]
            op = f["operator"]
            threshold = f["threshold"]
            direction = f.get("direction", "accept_if")  # accept_if | reject_if
            value = signal.payload.get(feature)

            entry: Dict[str, Any] = {
                "variant_id": f.get("variant_id"),
                "feature": feature,
                "family": _family_for_feature(feature),
                "operator": op,
                "threshold": threshold,
                "direction": direction,
                "value": value,
            }

            if not _is_numeric(value):
                # Missing or non-numeric payload value — permissive default
                # (CLAUDE.md §0.2 law 5: honest reporting, don't reject on
                # absent data). Log in audit.
                entry["passed"] = True
                entry["note"] = "missing_or_non_numeric_payload_value_permissive_pass"
                audit["filters_evaluated"].append(entry)
                continue

            cmp_result = _eval_comparison(float(value), op, float(threshold))
            # direction semantics:
            #   accept_if: pass iff cmp_result is True
            #   reject_if: pass iff cmp_result is False
            if direction == "accept_if":
                passed = cmp_result
            elif direction == "reject_if":
                passed = not cmp_result
            else:
                raise ValueError(f"unknown direction: {direction!r}")

            entry["passed"] = passed
            audit["filters_evaluated"].append(entry)
            if not passed:
                reason = (
                    f"{f.get('variant_id', 'unnamed')} rejected: "
                    f"{feature}={value} {op} {threshold} "
                    f"(direction={direction}) -> cmp={cmp_result}"
                )
                return False, reason, audit

        return True, None, audit

    # ---- Gate 2: social SAFE --------------------------------------------- #
    def _gate_social_safe(self, signal: Signal) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """Evaluate shortlisted social filters for this setup.

        Filters fire on velocity_ratio(signal, category). If
        ``social_velocity_fn`` is None or returns NaN, the rule is treated as
        'missing data' and permissive-passes (audit records this).
        """
        filters = self._social_filters(signal.setup_id)
        audit: Dict[str, Any] = {
            "setup_id": signal.setup_id,
            "filters_evaluated": [],
            "auto_passed": False,
        }
        if not filters:
            audit["auto_passed"] = True
            audit["reason"] = "no social filter for this strategy"
            return True, None, audit

        for f in filters:
            category = f["category"]
            op = f["operator"]
            threshold = f["threshold"]
            direction = f.get("direction", "gt")  # 'gt' | 'lt' used in JSON
            variant_id = f.get("variant_id")

            entry: Dict[str, Any] = {
                "variant_id": variant_id,
                "category": category,
                "operator": op,
                "threshold": threshold,
                "direction": direction,
            }

            if self._social_velocity_fn is None:
                entry["passed"] = True
                entry["note"] = "social_velocity_fn_not_wired_permissive_pass"
                audit["filters_evaluated"].append(entry)
                continue

            try:
                velocity = self._social_velocity_fn(signal, category)
            except Exception as exc:  # pragma: no cover - defensive
                entry["passed"] = True
                entry["note"] = f"velocity_fn_raised_permissive_pass: {exc}"
                audit["filters_evaluated"].append(entry)
                continue

            entry["velocity"] = velocity
            if velocity is None or (isinstance(velocity, float) and velocity != velocity):
                entry["passed"] = True
                entry["note"] = "velocity_nan_or_none_permissive_pass"
                audit["filters_evaluated"].append(entry)
                continue

            # Social filters in JSON are PROMOTED filters where
            # promotion criterion is "post-filter PF > pre-filter PF with
            # fold_stability 4/4". The JSON rule `operator`/`threshold` in
            # tandem with direction describes the ACCEPT predicate — i.e.
            # velocity op threshold must hold for the signal to be accepted.
            # Mirrors indicator_safe semantics with default direction=accept_if.
            cmp_result = _eval_comparison(float(velocity), op, float(threshold))
            # All promoted social filters in v1 are ACCEPT predicates; the
            # 'direction' field ('gt'/'lt') is redundant with operator. Treat
            # as accept-if for forward-compat.
            passed = cmp_result
            entry["passed"] = passed
            audit["filters_evaluated"].append(entry)
            if not passed:
                reason = (
                    f"{variant_id} rejected: "
                    f"{category} velocity={velocity} {op} {threshold} -> {cmp_result}"
                )
                return False, reason, audit

        return True, None, audit

    # ---- Gate 3: cluster cap --------------------------------------------- #
    def _gate_cluster_cap(
        self, signal: Signal, open_positions: List[Signal]
    ) -> Tuple[Verdict, Optional[GateName], Optional[str], Optional[int], Dict[str, Any]]:
        cluster_map = self._cluster_map()
        cluster_id = cluster_map.get(signal.symbol)

        audit: Dict[str, Any] = {
            "symbol": signal.symbol,
            "cluster_id": cluster_id,
            "max_per_cluster": self.max_per_cluster,
            "singleton_fallback": False,
        }

        if cluster_id is None:
            # Symbol not present in cluster map → treat as its own singleton
            # cluster. The cluster identity must still distinguish it from
            # OTHER singletons, so we encode it as a synthetic negative int
            # derived from the symbol (purely for audit / occupancy counting).
            # Using a hash-based int keeps singletons from different symbols
            # from colliding.
            audit["singleton_fallback"] = True
            synthetic_cid = -((abs(hash(signal.symbol)) % (2**31 - 1)) + 1)
            cluster_id = synthetic_cid
            audit["cluster_id"] = cluster_id

        # Occupancy — count open positions that resolve to the same cluster_id
        # (applying the same singleton-fallback to open positions).
        occupancy = 0
        for op in open_positions:
            op_cid = cluster_map.get(op.symbol)
            if op_cid is None:
                op_cid = -((abs(hash(op.symbol)) % (2**31 - 1)) + 1)
            if op_cid == cluster_id:
                occupancy += 1
        audit["current_occupancy"] = occupancy

        if occupancy >= self.max_per_cluster:
            reason = (
                f"cluster_id={cluster_id} is full "
                f"({occupancy}/{self.max_per_cluster}) — QUEUE"
            )
            return "QUEUE_CLUSTER_FULL", "cluster_cap", reason, cluster_id, audit

        return "ACCEPT", None, None, cluster_id, audit

    # ---- Gate 4: regime enforcer ----------------------------------------- #
    def _gate_regime_enforcer(
        self, signal: Signal
    ) -> Tuple[bool, Optional[str], float, Dict[str, Any]]:
        """Run the minimum-viable regime enforcer.

        On NORMAL: no-op (passed=True, size_multiplier=1.0).
        On ELEVATED: iterate social size modulators; apply the first match.
        On SHOCK: reject iff class_4h == 'HIGH_BETA'. Non-HIGH_BETA passes.

        Returns (passed, reject_reason, size_multiplier, audit).
        """
        try:
            state: RegimeState = self._regime_state_source()
        except Exception as exc:  # pragma: no cover - defensive
            # Regime source broken — permissive default is NORMAL.
            state = "NORMAL"
            _audit: Dict[str, Any] = {"state": state, "source_error": str(exc)}
            return True, None, 1.0, _audit

        audit: Dict[str, Any] = {"state": state}

        if state == "NORMAL":
            return True, None, 1.0, audit

        if state == "SHOCK":
            beta_class = self._beta_class_4h(signal.symbol)
            audit["beta_class_4h"] = beta_class
            if beta_class == "HIGH_BETA":
                return False, "regime=SHOCK and symbol class_4h=HIGH_BETA → REJECT", 1.0, audit
            # All other classes (NEUTRAL / LOW_BETA / DEFENSIVE / missing)
            # pass during SHOCK under the minimum-viable enforcer.
            return True, None, 1.0, audit

        if state == "ELEVATED":
            # Apply social size modulator if applicable. Use the first
            # modulator whose trigger is observed. If social_velocity_fn is
            # unwired, we cannot evaluate triggers → size_multiplier stays
            # 1.0 and audit notes the degraded mode.
            modulators = self._social_modulators(signal.setup_id)
            audit["modulators_considered"] = len(modulators)
            if not modulators:
                audit["reason"] = "no modulators for this strategy"
                return True, None, 1.0, audit

            if self._social_velocity_fn is None:
                audit["reason"] = "velocity_fn_unwired_no_size_adjustment"
                return True, None, 1.0, audit

            for m in modulators:
                category = m["category"]
                op = m["operator"]
                threshold = m["threshold"]
                variant_id = m.get("variant_id")
                try:
                    velocity = self._social_velocity_fn(signal, category)
                except Exception:
                    continue
                if velocity is None or (isinstance(velocity, float) and velocity != velocity):
                    continue
                if _eval_comparison(float(velocity), op, float(threshold)):
                    mult = float(m.get("suggested_size_multiplier", 1.0))
                    audit["modulator_applied"] = variant_id
                    audit["velocity"] = velocity
                    audit["size_multiplier"] = mult
                    return True, None, mult, audit

            audit["reason"] = "no modulator trigger fired"
            return True, None, 1.0, audit

        # Unknown state → permissive pass with audit note.
        audit["note"] = f"unknown_regime_state_{state}_permissive_pass"
        return True, None, 1.0, audit

    # ---- Public API ------------------------------------------------------ #
    def evaluate(self, signal: Signal, open_positions: List[Signal]) -> PipelineDecision:
        """Run all four gates for a single signal. Short-circuit on reject."""
        audit: Dict[str, Any] = {}

        # Gate 1 — indicator SAFE
        ind_pass, ind_reason, ind_audit = self._gate_indicator_safe(signal)
        audit["indicator_safe"] = ind_audit
        if not ind_pass:
            return PipelineDecision(
                opportunity_id=signal.opportunity_id,
                verdict="REJECT",
                gate_rejecting="indicator_safe",
                reject_reason=ind_reason,
                cluster_id=None,
                size_multiplier=1.0,
                audit=audit,
            )

        # Gate 2 — social SAFE
        soc_pass, soc_reason, soc_audit = self._gate_social_safe(signal)
        audit["social_safe"] = soc_audit
        if not soc_pass:
            return PipelineDecision(
                opportunity_id=signal.opportunity_id,
                verdict="REJECT",
                gate_rejecting="social_safe",
                reject_reason=soc_reason,
                cluster_id=None,
                size_multiplier=1.0,
                audit=audit,
            )

        # Gate 3 — cluster cap
        cc_verdict, cc_gate, cc_reason, cc_cluster_id, cc_audit = self._gate_cluster_cap(
            signal, open_positions
        )
        audit["cluster_cap"] = cc_audit
        if cc_verdict == "QUEUE_CLUSTER_FULL":
            return PipelineDecision(
                opportunity_id=signal.opportunity_id,
                verdict="QUEUE_CLUSTER_FULL",
                gate_rejecting="cluster_cap",
                reject_reason=cc_reason,
                cluster_id=cc_cluster_id,
                size_multiplier=1.0,
                audit=audit,
            )

        # Gate 4 — regime enforcer
        re_pass, re_reason, size_mult, re_audit = self._gate_regime_enforcer(signal)
        audit["regime_enforcer"] = re_audit
        if not re_pass:
            return PipelineDecision(
                opportunity_id=signal.opportunity_id,
                verdict="REJECT",
                gate_rejecting="regime_enforcer",
                reject_reason=re_reason,
                cluster_id=cc_cluster_id,
                size_multiplier=1.0,
                audit=audit,
            )

        return PipelineDecision(
            opportunity_id=signal.opportunity_id,
            verdict="ACCEPT",
            gate_rejecting=None,
            reject_reason=None,
            cluster_id=cc_cluster_id,
            size_multiplier=size_mult,
            audit=audit,
        )

    def evaluate_batch(
        self, signals: List[Signal], open_positions: Optional[List[Signal]] = None
    ) -> List[PipelineDecision]:
        """Evaluate a batch in chronological order.

        ACCEPTed signals are appended to a mutable ``open_positions`` list
        that is threaded through subsequent evaluations, so cluster occupancy
        evolves realistically. Callers that want true position lifetimes
        should pre-process ``signals`` to interleave signal-events and
        close-events; this method treats every ACCEPT as an indefinite hold
        for the duration of the batch (suitable for D6 backfill sim).
        """
        open_positions = list(open_positions or [])
        decisions: List[PipelineDecision] = []
        # Ensure chronological order for occupancy realism.
        ordered = sorted(signals, key=lambda s: s.created_ts)
        for sig in ordered:
            decision = self.evaluate(sig, open_positions)
            decisions.append(decision)
            if decision.verdict == "ACCEPT":
                open_positions.append(sig)
        return decisions


__all__ = [
    "Signal",
    "PipelineDecision",
    "Cap3Pipeline",
    "RegimeState",
    "Verdict",
    "GateName",
    "FEATURE_FAMILIES",
    "_family_for_feature",
    "_group_winners",
    "_top_dsr_filter",
    "_combo2fam_winners",
    "_parse_combo2fam_mode",
    "_is_valid_composition_mode",
]
