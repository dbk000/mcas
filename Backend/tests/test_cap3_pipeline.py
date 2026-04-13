"""Tests for Backend/governance/selection/cap3_pipeline.py.

Targets 100% coverage on the four gates and ≥10 test cases. Uses the canonical
v1 JSON artefacts on disk as fixtures — no mocks, no stubs for the gate inputs
themselves. The only injected stub is the ``social_velocity_fn``, which is
deferred wiring (D7).
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pytest

from Backend.governance.selection.cap3_pipeline import (
    Cap3Pipeline,
    PipelineDecision,
    Signal,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOV = REPO_ROOT / "Backend" / "governance"

INDICATOR_FILTERS = GOV / "safe_filters" / "indicator_safe_filters_v1.json"
SOCIAL_FILTERS = GOV / "safe_filters" / "social_safe_filters_v1.json"
SOCIAL_MODULATORS = GOV / "safe_filters" / "social_size_modulators_v1.json"
CLUSTERS = GOV / "clusters" / "correlation_clusters_v1.json"
BETA_TABLE = GOV / "regime" / "asset_beta_table_binance_v1.json"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_pipeline(
    regime: str = "NORMAL",
    social_velocity_fn=None,
    max_per_cluster: int = 1,
    filter_composition_mode: str = "and_all",
) -> Cap3Pipeline:
    # Default composition_mode is "and_all" here so the legacy test suite
    # (tests 1-13, written when AND-all was the only behaviour) continues to
    # exercise that exact code path. Production default is "grouped_and"
    # (D6-REDO 2026-04-12 fix); the new grouped tests below opt in explicitly.
    return Cap3Pipeline(
        indicator_filters_path=str(INDICATOR_FILTERS),
        social_filters_path=str(SOCIAL_FILTERS),
        social_modulators_path=str(SOCIAL_MODULATORS),
        clusters_path=str(CLUSTERS),
        beta_table_path=str(BETA_TABLE),
        max_per_cluster=max_per_cluster,
        regime_state_source=lambda: regime,
        social_velocity_fn=social_velocity_fn,
        filter_composition_mode=filter_composition_mode,
    )


def _c4ml_signal_passes_indicator(symbol: str = "BTCUSDT") -> Signal:
    """C4ML has null_result on indicator filters -> auto-passes gate 1."""
    return Signal(
        opportunity_id="opp_c4ml_1",
        setup_id="C4ML",
        symbol=symbol,
        created_ts=1_700_000_000,
        payload={},
    )


def _c2s_signal(**payload_overrides) -> Signal:
    """C2S has a shortlisted quote_volume_24h_usd filter at <$1,007,259.19.

    Default payload passes all 8 C2S filters."""
    payload = {
        "quote_volume_24h_usd": 500_000.0,   # < 1,007,259 → pass
        "rsi_sma": 30.0,                      # < 35.35 → pass
        "sma50_slope_atr": -0.2,              # < -0.09928 → pass (and <-0.04401)
        "rsi": 30.0,                          # < 36.4 → pass
        "macd_signal": -0.01,                 # < -0.0005152 → pass
        "ema20_slope_atr": -0.5,              # < -0.1672 → pass
        "macd_line": -0.01,                   # < -0.000766 → pass
    }
    payload.update(payload_overrides)
    return Signal(
        opportunity_id="opp_c2s_1",
        setup_id="C2S",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_1_accept_all_gates_pass_normal_regime():
    """C4ML signal (no indicator or social filters) on BTCUSDT, NORMAL regime,
    no open positions → ACCEPT, size_multiplier=1.0."""
    p = _make_pipeline(regime="NORMAL")
    sig = _c4ml_signal_passes_indicator("BTCUSDT")
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "ACCEPT"
    assert decision.size_multiplier == 1.0
    assert decision.gate_rejecting is None
    assert decision.cluster_id is not None  # BTCUSDT is in cluster 1
    # audit records every gate.
    assert "indicator_safe" in decision.audit
    assert "social_safe" in decision.audit
    assert "cluster_cap" in decision.audit
    assert "regime_enforcer" in decision.audit


def test_2_reject_indicator_safe_c2s_quote_volume():
    """C2S filter: quote_volume_24h_usd < $1,007,259 accept_if. If the signal
    has quote_volume_24h_usd = $10M, it fails and the first shortlisted
    filter (which happens to be quote_volume_24h_usd) vetoes."""
    p = _make_pipeline(regime="NORMAL")
    sig = _c2s_signal(quote_volume_24h_usd=10_000_000.0)
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "REJECT"
    assert decision.gate_rejecting == "indicator_safe"
    assert "quote_volume_24h_usd" in decision.reject_reason


def test_3_reject_social_safe_c2m_geopolitical_below_threshold():
    """C2M has GEOPOLITICAL>0.5833 accept_if. If velocity returns 0.1 (below),
    the filter rejects."""
    def velocity_fn(signal, category):
        mapping = {"GEOPOLITICAL": 0.1, "MONETARY": 1.0}
        return mapping.get(category, 1.0)

    p = _make_pipeline(regime="NORMAL", social_velocity_fn=velocity_fn)
    sig = Signal(
        opportunity_id="opp_c2m_1",
        setup_id="C2M",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={"macd_signal": -0.01},  # passes the lone C2M indicator filter
    )
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "REJECT"
    assert decision.gate_rejecting == "social_safe"
    assert "GEOPOLITICAL" in decision.reject_reason


def test_4_cluster_cap_queues_when_full():
    """BTCUSDT and ETHUSDT are in cluster 1. With max_per_cluster=1 and
    ETHUSDT already held, BTCUSDT signal → QUEUE_CLUSTER_FULL."""
    p = _make_pipeline(regime="NORMAL", max_per_cluster=1)
    held = Signal(
        opportunity_id="opp_held_1",
        setup_id="C4ML",
        symbol="ETHUSDT",
        created_ts=1_700_000_000 - 3600,
        payload={},
    )
    new_sig = _c4ml_signal_passes_indicator("BTCUSDT")
    decision = p.evaluate(new_sig, open_positions=[held])
    assert decision.verdict == "QUEUE_CLUSTER_FULL"
    assert decision.gate_rejecting == "cluster_cap"
    assert decision.cluster_id == 1  # BTCUSDT's cluster


def test_5_regime_shock_rejects_high_beta():
    """HIGH_BETA symbols (e.g. AIXBTUSDT per beta table) are rejected on SHOCK."""
    p = _make_pipeline(regime="SHOCK")
    sig = Signal(
        opportunity_id="opp_shock_1",
        setup_id="C4ML",  # auto-passes indicator gate
        symbol="AIXBTUSDT",
        created_ts=1_700_000_000,
        payload={},
    )
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "REJECT"
    assert decision.gate_rejecting == "regime_enforcer"
    assert "SHOCK" in decision.reject_reason
    assert "HIGH_BETA" in decision.reject_reason


def test_6_regime_elevated_applies_size_modulator():
    """ELEVATED regime: a C2M signal with GEOPOLITICAL velocity > 0.583 hits
    the positive modulator (suggested_size_multiplier≈1.067)."""

    def velocity_fn(signal, category):
        # GEOPOLITICAL > 0.583 fires the first C2M modulator.
        # Also needs to pass the social SAFE gate (GEOPOLITICAL > 0.583 and
        # MONETARY > 0.583 both accept_if, so returning 1.0 works).
        return 1.0

    p = _make_pipeline(regime="ELEVATED", social_velocity_fn=velocity_fn)
    sig = Signal(
        opportunity_id="opp_elev_1",
        setup_id="C2M",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={"macd_signal": -0.01},  # passes indicator gate
    )
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "ACCEPT"
    assert decision.size_multiplier != 1.0
    assert decision.size_multiplier > 1.0  # first C2M modulator is positive
    assert "modulator_applied" in decision.audit["regime_enforcer"]


def test_7_strategy_with_no_indicator_filter_auto_passes():
    """C2SH has an empty shortlist → indicator gate auto-passes."""
    p = _make_pipeline(regime="NORMAL")
    sig = Signal(
        opportunity_id="opp_c2sh_1",
        setup_id="C2SH",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={},
    )
    decision = p.evaluate(sig, open_positions=[])
    # C2SH also has no social filters -> both auto-pass.
    assert decision.verdict == "ACCEPT"
    assert decision.audit["indicator_safe"]["auto_passed"] is True
    assert decision.audit["social_safe"]["auto_passed"] is True


def test_8_evaluate_batch_evolves_cluster_occupancy():
    """Feed 10 sequential C4ML signals, 5 of which are in cluster 1 (BTCUSDT,
    ETHUSDT, BNBUSDT, LINKUSDT, BTCUSDT again). First accept, rest queue."""
    p = _make_pipeline(regime="NORMAL", max_per_cluster=1)

    cluster1_syms = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "LINKUSDT", "BTCUSDT"]
    # Mix in other clusters to verify batch ordering doesn't corrupt state.
    # Cluster 0 contains FLOKIUSDT, DOGEUSDT, etc.
    other_syms = ["FLOKIUSDT", "DOGEUSDT", "PEPEUSDT", "BONKUSDT", "SHIBUSDT"]

    signals = []
    ts = 1_700_000_000
    # interleave
    for i in range(10):
        if i % 2 == 0:
            sym = cluster1_syms[i // 2]
        else:
            sym = other_syms[i // 2]
        signals.append(Signal(
            opportunity_id=f"opp_batch_{i}",
            setup_id="C4ML",
            symbol=sym,
            created_ts=ts + i,
            payload={},
        ))

    decisions = p.evaluate_batch(signals, open_positions=[])
    assert len(decisions) == 10
    # First cluster-1 signal accepted, rest queued.
    c1_decisions = [d for d, s in zip(decisions, signals) if s.symbol in cluster1_syms]
    assert c1_decisions[0].verdict == "ACCEPT"
    for d in c1_decisions[1:]:
        assert d.verdict == "QUEUE_CLUSTER_FULL"

    # Cluster 0 is huge (12 syms) so the first FLOKIUSDT accepts and
    # subsequent DOGEUSDT/PEPEUSDT/BONKUSDT/SHIBUSDT (all in cluster 0) queue.
    c0_decisions = [d for d, s in zip(decisions, signals) if s.symbol in other_syms]
    assert c0_decisions[0].verdict == "ACCEPT"
    for d in c0_decisions[1:]:
        assert d.verdict == "QUEUE_CLUSTER_FULL"


def test_9_symbol_not_in_cluster_map_treated_as_singleton():
    """A synthetic symbol not in the cluster JSON gets a singleton fallback.
    Two different unknown symbols → different singleton clusters, both
    accept. Same unknown symbol seen twice → second queues."""
    p = _make_pipeline(regime="NORMAL", max_per_cluster=1)

    s1 = Signal(
        opportunity_id="opp_unk_1",
        setup_id="C4ML",
        symbol="UNKNOWNSYM1USDT",
        created_ts=1_700_000_000,
        payload={},
    )
    s2 = Signal(
        opportunity_id="opp_unk_2",
        setup_id="C4ML",
        symbol="UNKNOWNSYM2USDT",
        created_ts=1_700_000_001,
        payload={},
    )
    s3 = Signal(
        opportunity_id="opp_unk_3",
        setup_id="C4ML",
        symbol="UNKNOWNSYM1USDT",  # same as s1
        created_ts=1_700_000_002,
        payload={},
    )

    decisions = p.evaluate_batch([s1, s2, s3], open_positions=[])
    verdicts = [d.verdict for d in decisions]
    assert verdicts == ["ACCEPT", "ACCEPT", "QUEUE_CLUSTER_FULL"]
    # Audit records singleton fallback for all three.
    for d in decisions:
        assert d.audit["cluster_cap"]["singleton_fallback"] is True


def test_10_missing_social_velocity_permissive_pass():
    """social_velocity_fn not provided → every social filter passes.
    Also: if provided and returns NaN, permissive pass (audit notes)."""
    # Case A: unwired.
    p = _make_pipeline(regime="NORMAL", social_velocity_fn=None)
    sig = Signal(
        opportunity_id="opp_missing_1",
        setup_id="C2M",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={"macd_signal": -0.01},
    )
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "ACCEPT"
    social_entries = decision.audit["social_safe"]["filters_evaluated"]
    assert len(social_entries) >= 1
    for entry in social_entries:
        assert entry["passed"] is True
        assert "note" in entry

    # Case B: returns NaN.
    def nan_velocity(signal, category):
        return float("nan")
    p2 = _make_pipeline(regime="NORMAL", social_velocity_fn=nan_velocity)
    d2 = p2.evaluate(sig, open_positions=[])
    assert d2.verdict == "ACCEPT"
    for entry in d2.audit["social_safe"]["filters_evaluated"]:
        assert entry["passed"] is True
        assert "nan" in entry["note"].lower() or "none" in entry["note"].lower()


def test_11_regime_shock_accepts_non_high_beta():
    """Sanity: NEUTRAL / LOW_BETA / DEFENSIVE symbols pass during SHOCK."""
    p = _make_pipeline(regime="SHOCK")
    # Use BTCUSDT — look up its class to confirm it's not HIGH_BETA.
    beta = json.loads(BETA_TABLE.read_text())
    btc_class = beta["symbols"]["BTCUSDT"]["class_4h"]
    assert btc_class != "HIGH_BETA"  # sanity on fixture
    sig = _c4ml_signal_passes_indicator("BTCUSDT")
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "ACCEPT"
    assert decision.audit["regime_enforcer"]["state"] == "SHOCK"
    assert decision.audit["regime_enforcer"]["beta_class_4h"] == btc_class


def test_12_indicator_missing_payload_value_permissive_pass():
    """If the payload lacks the feature a filter tests, don't reject.
    C2S's 8 filters all look for numeric keys — empty payload → permissive
    pass on all, then cluster+regime normal → ACCEPT."""
    p = _make_pipeline(regime="NORMAL")
    sig = Signal(
        opportunity_id="opp_c2s_missing",
        setup_id="C2S",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={},
    )
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "ACCEPT"
    for entry in decision.audit["indicator_safe"]["filters_evaluated"]:
        assert entry["passed"] is True
        assert "note" in entry


def test_14_grouped_and_reduces_macd_family_to_single_winner():
    """C4M has 7 shortlisted filters: 4 in the MACD family (macd_line,
    macd_signal, macd_histogram, macd_prev_histogram), 2 in the RSI family
    (rsi_slope ×2), and 1 in the ATR family (atr).

    In ``grouped_and`` mode, the indicator gate must collapse the 4 MACD
    filters to a single winner (highest DSR) — leaving exactly 3 evaluated
    filters total (one per family) instead of 7."""
    p = _make_pipeline(regime="NORMAL", filter_composition_mode="grouped_and")
    sig = Signal(
        opportunity_id="opp_c4m_grouped_1",
        setup_id="C4M",
        symbol="BTCUSDT",
        # Pass values that satisfy ATR>0.0069 and RSI slope>2.509 and
        # the MACD-family group winner (macd_line < -0.006707 expected).
        payload={
            "atr": 0.02,
            "rsi_slope": 5.0,
            "macd_line": -0.05,
            "macd_signal": -0.05,
            "macd_histogram": -0.05,
            "macd_prev_histogram": -0.05,
        },
        created_ts=1_700_000_000,
    )
    decision = p.evaluate(sig, open_positions=[])

    evaluated = decision.audit["indicator_safe"]["filters_evaluated"]
    families = {e["family"] for e in evaluated}
    # All MACD-family entries collapsed to one — RSI to one — ATR to one.
    assert families == {"MACD", "RSI", "ATR"}, (
        f"expected exactly the three orthogonal families "
        f"{{'MACD','RSI','ATR'}}, got {families}"
    )
    # Exactly 3 filters evaluated (one per family) — not 7.
    assert len(evaluated) == 3, (
        f"expected 3 group-winners; got {len(evaluated)}: "
        f"{[e['variant_id'] for e in evaluated]}"
    )
    # Each evaluated filter is the family DSR-winner.
    macd_entry = next(e for e in evaluated if e["family"] == "MACD")
    # macd_line had DSR≈4.6e-10 (highest among MACD-family in C4M).
    assert macd_entry["feature"] == "macd_line"
    rsi_entry = next(e for e in evaluated if e["family"] == "RSI")
    # rsi_slope >2.509 (DSR≈6.7e-14) beats rsi_slope >0.6213 (DSR≈8.4e-15).
    assert rsi_entry["variant_id"] == "C4M_indi_rsi_slope_gt_2p509_v1"
    # Composition mode threaded into audit for traceability.
    assert decision.audit["indicator_safe"]["composition_mode"] == "grouped_and"
    # And the signal should ACCEPT (all three group-winners pass on this payload).
    assert decision.verdict == "ACCEPT"


def test_15_grouped_and_keeps_orthogonal_family_representatives():
    """Verify that strategies with multiple orthogonal families keep one
    representative per family (i.e. grouping doesn't over-collapse).

    C2S has 8 shortlisted filters across 5 families: VOLUME (1), RSI (2),
    SLOPE_ATR variants (sma50_slope_atr ×2 → singleton family,
    ema20_slope_atr → singleton family), MACD (2). After grouping there must
    be exactly 5 distinct family winners — VOLUME + RSI + MACD + the two
    singleton slope features. Rejecting one of those families' winners must
    still REJECT the whole signal (AND on group-winners)."""
    p = _make_pipeline(regime="NORMAL", filter_composition_mode="grouped_and")

    # Payload that passes ALL group winners except VOLUME: huge volume → fail.
    sig = Signal(
        opportunity_id="opp_c2s_grouped_1",
        setup_id="C2S",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={
            "quote_volume_24h_usd": 50_000_000.0,  # fails VOLUME (>1.007M)
            "rsi_sma": 30.0,                       # passes RSI (best DSR)
            "rsi": 30.0,                           # (loses to rsi_sma in RSI family)
            "macd_signal": -0.01,                  # passes MACD (best DSR)
            "macd_line": -0.01,                    # (loses to macd_signal in MACD)
            "ema20_slope_atr": -0.5,               # passes its singleton family
            "sma50_slope_atr": -0.5,               # passes its singleton family
        },
    )
    decision = p.evaluate(sig, open_positions=[])
    assert decision.verdict == "REJECT"
    assert decision.gate_rejecting == "indicator_safe"
    # Reject reason mentions the VOLUME-family winner.
    assert "quote_volume_24h_usd" in decision.reject_reason

    # Now verify the family count: re-run a pipeline that records the
    # evaluated set when all gates pass (use a permissive payload).
    pass_sig = Signal(
        opportunity_id="opp_c2s_grouped_2",
        setup_id="C2S",
        symbol="BTCUSDT",
        created_ts=1_700_000_001,
        payload={
            "quote_volume_24h_usd": 500_000.0,
            "rsi_sma": 30.0,
            "rsi": 30.0,
            "macd_signal": -0.01,
            "macd_line": -0.01,
            "ema20_slope_atr": -0.5,
            "sma50_slope_atr": -0.5,
        },
    )
    pass_dec = p.evaluate(pass_sig, open_positions=[])
    assert pass_dec.verdict == "ACCEPT"
    evaluated = pass_dec.audit["indicator_safe"]["filters_evaluated"]
    families = sorted({e["family"] for e in evaluated})
    # Expect VOLUME, RSI, MACD, sma50_slope_atr (singleton), ema20_slope_atr (singleton).
    assert "VOLUME" in families
    assert "RSI" in families
    assert "MACD" in families
    assert "sma50_slope_atr" in families
    assert "ema20_slope_atr" in families
    # 5 family winners — down from 8 raw shortlisted filters.
    assert len(evaluated) == 5, (
        f"expected 5 group-winners (one per family); got {len(evaluated)}: "
        f"families={families}"
    )


def test_16_top_dsr_only_c4m_picks_atr_only():
    """C4M has 7 shortlisted indicator filters. In ``top_dsr_only`` mode the
    gate must evaluate ONLY the highest-DSR filter (atr > 0.006929, DSR≈5.9e-9).

    If the signal's atr value passes but other filters would have rejected
    (e.g. macd_line well above threshold), the gate still passes — because
    only ATR is evaluated."""
    p = _make_pipeline(regime="NORMAL", filter_composition_mode="top_dsr_only")
    sig = Signal(
        opportunity_id="opp_c4m_topdsr_1",
        setup_id="C4M",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={
            "atr": 0.02,  # > 0.006929 → passes the ATR filter
            # macd_line/signal/hist set well ABOVE the MACD thresholds —
            # these would REJECT under and_all / grouped_and, but here they
            # are not evaluated at all.
            "macd_line": 1.0,
            "macd_signal": 1.0,
            "macd_histogram": 1.0,
            "macd_prev_histogram": 1.0,
            # rsi_slope below both rsi_slope thresholds (would also reject)
            "rsi_slope": -1.0,
        },
    )
    decision = p.evaluate(sig, open_positions=[])

    evaluated = decision.audit["indicator_safe"]["filters_evaluated"]
    # Exactly ONE filter evaluated (not 7, not 3).
    assert len(evaluated) == 1, (
        f"expected 1 evaluated filter, got {len(evaluated)}: "
        f"{[e['variant_id'] for e in evaluated]}"
    )
    # It must be the ATR filter (highest DSR among the 7).
    assert evaluated[0]["feature"] == "atr"
    assert evaluated[0]["variant_id"] == "C4M_indi_atr_gt_0p006929_v1"
    assert evaluated[0]["passed"] is True
    # Composition mode threaded into audit for traceability.
    assert decision.audit["indicator_safe"]["composition_mode"] == "top_dsr_only"
    # Signal should ACCEPT (only gate concern is indicator, and it passed).
    assert decision.verdict == "ACCEPT"


def test_17_top_dsr_only_no_shortlisted_filters_auto_passes():
    """Strategies with 0 shortlisted indicator filters (C2SH / C4ML / C3M)
    must auto-pass the indicator gate in top_dsr_only mode — same behaviour
    as the other composition modes. No filter is evaluated, audit records
    auto_passed=True."""
    for setup_id in ("C2SH", "C4ML", "C3M"):
        p = _make_pipeline(regime="NORMAL", filter_composition_mode="top_dsr_only")
        sig = Signal(
            opportunity_id=f"opp_{setup_id}_topdsr_1",
            setup_id=setup_id,
            symbol="BTCUSDT",
            created_ts=1_700_000_000,
            payload={},  # empty payload: nothing to evaluate anyway
        )
        decision = p.evaluate(sig, open_positions=[])
        ind_audit = decision.audit["indicator_safe"]
        assert ind_audit["auto_passed"] is True, (
            f"{setup_id}: expected auto_passed=True under top_dsr_only"
        )
        assert ind_audit["filters_evaluated"] == [], (
            f"{setup_id}: expected zero filters evaluated, got "
            f"{ind_audit['filters_evaluated']}"
        )
        assert ind_audit["composition_mode"] == "top_dsr_only"
        assert decision.verdict == "ACCEPT"


def test_18_combo2fam_c4m_macd_and_volatility():
    """``combo2fam:MACD|VOLATILITY`` on C4M: C4M has 5 MACD-family filters
    (macd_line/signal/histogram/prev_histogram) and 1 VOLATILITY filter (atr).

    Expected semantics:
      * indicator gate evaluates exactly 2 filters — the best-DSR MACD winner
        and the best-DSR VOLATILITY winner.
      * a payload that satisfies BOTH group winners → ACCEPT.
      * a payload that fails the MACD winner OR the VOLATILITY winner → REJECT
        via indicator_safe.
    """
    p = _make_pipeline(
        regime="NORMAL",
        filter_composition_mode="combo2fam:MACD|VOLATILITY",
    )

    # --- Case A: passes both group winners -> ACCEPT ----------------------
    accept_sig = Signal(
        opportunity_id="opp_c4m_combo2fam_accept",
        setup_id="C4M",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={
            "macd_line": -0.05,            # < -0.006707 → pass MACD winner
            "macd_signal": -0.05,
            "macd_histogram": -0.05,
            "macd_prev_histogram": -0.05,
            "atr": 0.02,                   # > 0.006929 → pass VOLATILITY winner
            "rsi_slope": -1.0,             # RSI not evaluated (not a listed fam)
        },
    )
    accept_dec = p.evaluate(accept_sig, open_positions=[])
    assert accept_dec.verdict == "ACCEPT", (
        f"combo2fam pass-case: expected ACCEPT, got "
        f"{accept_dec.verdict} ({accept_dec.reject_reason})"
    )
    evaluated = accept_dec.audit["indicator_safe"]["filters_evaluated"]
    families = {e["family"] for e in evaluated}
    # Exactly 2 filters evaluated (MACD winner + VOLATILITY winner = atr).
    assert len(evaluated) == 2, (
        f"combo2fam should evaluate 2 filters, got {len(evaluated)}: "
        f"{[e['variant_id'] for e in evaluated]}"
    )
    # The base FEATURE_FAMILIES classifies atr under family 'ATR'; combo2fam
    # groups ATR+BB under VOLATILITY, but audit still records the base family
    # name so we assert on the feature set rather than family labels here.
    features_evaluated = {e["feature"] for e in evaluated}
    assert features_evaluated == {"macd_line", "atr"}, (
        f"combo2fam:MACD|VOLATILITY should pick macd_line (best-DSR MACD) "
        f"and atr (only VOLATILITY filter), got {features_evaluated}"
    )
    # Composition mode threaded through.
    assert accept_dec.audit["indicator_safe"]["composition_mode"] == (
        "combo2fam:MACD|VOLATILITY"
    )

    # --- Case B: fails MACD winner -> REJECT ------------------------------
    reject_macd_sig = Signal(
        opportunity_id="opp_c4m_combo2fam_reject_macd",
        setup_id="C4M",
        symbol="BTCUSDT",
        created_ts=1_700_000_001,
        payload={
            "macd_line": 1.0,    # fails MACD winner (needs < -0.006707)
            "atr": 0.02,         # passes VOLATILITY winner
        },
    )
    reject_macd_dec = p.evaluate(reject_macd_sig, open_positions=[])
    assert reject_macd_dec.verdict == "REJECT"
    assert reject_macd_dec.gate_rejecting == "indicator_safe"
    assert "macd_line" in reject_macd_dec.reject_reason

    # --- Case C: fails VOLATILITY winner -> REJECT ------------------------
    reject_vol_sig = Signal(
        opportunity_id="opp_c4m_combo2fam_reject_vol",
        setup_id="C4M",
        symbol="BTCUSDT",
        created_ts=1_700_000_002,
        payload={
            "macd_line": -0.05,    # passes MACD winner
            "atr": 0.001,          # fails VOLATILITY winner (needs > 0.006929)
        },
    )
    reject_vol_dec = p.evaluate(reject_vol_sig, open_positions=[])
    assert reject_vol_dec.verdict == "REJECT"
    assert reject_vol_dec.gate_rejecting == "indicator_safe"
    assert "atr" in reject_vol_dec.reject_reason


def test_19_per_strategy_mode_override_coexists_in_same_pipeline():
    """A single Cap3Pipeline instance must apply different composition modes
    to different strategies simultaneously, per ``per_strategy_mode``.

    Setup:
      * C2M uses ``top_dsr_only``  — its single macd_signal filter is kept.
      * C4M uses ``combo2fam:MACD|VOLATILITY`` — 2 filters evaluated.
      * Global default is a distinct sentinel (``and_all``) so we can verify
        non-listed strategies aren't silently inheriting one of the overrides.
    """
    per_strategy_mode = {
        "C2M": "top_dsr_only",
        "C4M": "combo2fam:MACD|VOLATILITY",
        # C2SH / C3M / C4ML parked — accepted and ignored.
        "C2SH": "parked",
        "C3M": "parked",
        "C4ML": "parked",
    }
    p = Cap3Pipeline(
        indicator_filters_path=str(INDICATOR_FILTERS),
        social_filters_path=str(SOCIAL_FILTERS),
        social_modulators_path=str(SOCIAL_MODULATORS),
        clusters_path=str(CLUSTERS),
        beta_table_path=str(BETA_TABLE),
        max_per_cluster=1,
        regime_state_source=lambda: "NORMAL",
        # Wire a permissive social velocity stub so C2M's social gate doesn't
        # reject on absent-data (we're testing the indicator gate here).
        social_velocity_fn=lambda sig, cat: 10.0,
        filter_composition_mode="and_all",
        per_strategy_mode=per_strategy_mode,
    )

    # --- C2M signal: top_dsr_only -> exactly 1 filter evaluated -----------
    c2m_sig = Signal(
        opportunity_id="opp_c2m_per_strategy",
        setup_id="C2M",
        symbol="BTCUSDT",
        created_ts=1_700_000_000,
        payload={"macd_signal": -0.01},  # passes C2M's single shortlisted filter
    )
    c2m_dec = p.evaluate(c2m_sig, open_positions=[])
    c2m_ind = c2m_dec.audit["indicator_safe"]
    assert c2m_ind["composition_mode"] == "top_dsr_only", (
        f"C2M should use per-strategy override 'top_dsr_only', got "
        f"{c2m_ind['composition_mode']}"
    )
    # C2M has exactly 1 shortlisted filter so evaluated count is 1 in any mode;
    # what matters is that the mode string reflects the override.
    assert len(c2m_ind["filters_evaluated"]) == 1

    # --- C4M signal: combo2fam:MACD|VOLATILITY -> 2 filters evaluated -----
    c4m_sig = Signal(
        opportunity_id="opp_c4m_per_strategy",
        setup_id="C4M",
        symbol="BTCUSDT",
        created_ts=1_700_000_001,
        payload={
            "macd_line": -0.05,
            "atr": 0.02,
            # rsi_slope high enough that under and_all it would pass too, but
            # under combo2fam it must not be evaluated.
            "rsi_slope": 5.0,
        },
    )
    c4m_dec = p.evaluate(c4m_sig, open_positions=[])
    c4m_ind = c4m_dec.audit["indicator_safe"]
    assert c4m_ind["composition_mode"] == "combo2fam:MACD|VOLATILITY", (
        f"C4M should use per-strategy override 'combo2fam:...', got "
        f"{c4m_ind['composition_mode']}"
    )
    evaluated_features = {e["feature"] for e in c4m_ind["filters_evaluated"]}
    assert evaluated_features == {"macd_line", "atr"}, (
        f"C4M under combo2fam:MACD|VOLATILITY should evaluate only macd_line "
        f"and atr, got {evaluated_features}"
    )
    # And the C4M signal should ACCEPT (both group winners pass).
    assert c4m_dec.verdict == "ACCEPT"

    # --- Strategy NOT in per_strategy_mode falls back to global default ---
    # C2S is not listed -> inherits and_all. C2S has 8 shortlisted filters, so
    # audit should show the full 8 when payload is permissive enough that
    # every filter falls through to permissive-pass-on-missing-value.
    c2s_sig = Signal(
        opportunity_id="opp_c2s_per_strategy",
        setup_id="C2S",
        symbol="BTCUSDT",
        created_ts=1_700_000_002,
        payload={},  # empty -> every filter permissive-passes
    )
    c2s_dec = p.evaluate(c2s_sig, open_positions=[])
    c2s_ind = c2s_dec.audit["indicator_safe"]
    assert c2s_ind["composition_mode"] == "and_all", (
        f"C2S should fall back to global default 'and_all', got "
        f"{c2s_ind['composition_mode']}"
    )
    # and_all mode evaluates every shortlisted filter.
    assert len(c2s_ind["filters_evaluated"]) == 8, (
        f"C2S under and_all should evaluate 8 shortlisted filters, got "
        f"{len(c2s_ind['filters_evaluated'])}"
    )


def test_13_evaluate_latency_under_5ms_budget():
    """Sanity perf check: single evaluate() averages well under 5ms."""
    p = _make_pipeline(regime="NORMAL")
    sig = _c4ml_signal_passes_indicator("BTCUSDT")
    # Warm caches
    p.evaluate(sig, open_positions=[])

    t0 = time.perf_counter()
    N = 500
    for _ in range(N):
        p.evaluate(sig, open_positions=[])
    elapsed = time.perf_counter() - t0
    avg_ms = (elapsed / N) * 1000
    # Target <5ms per call; fail loudly if we regress above 10ms.
    assert avg_ms < 10.0, f"evaluate() avg {avg_ms:.3f}ms exceeds budget"
